# Production Architecture

OpenTelemetry can export directly from an SDK to a backend, but production
systems usually put an OpenTelemetry Collector between services and backends.

The Collector is not just another agent. It is the control point for telemetry:

```text
services
  -> OTLP exporters
  -> Collector receivers
  -> Collector processors
  -> Collector exporters
  -> trace, metric, log, and LLM observability backends
```

This chapter explains how to design that pipeline so it is understandable,
operable, and safe.

## What The Collector Owns

The Collector owns shared telemetry policy. It should not own application
meaning. Application services still create spans, metrics, logs, attributes, and
context. The Collector receives those signals and applies infrastructure policy.

| Concern | Usually owned by |
| --- | --- |
| Span names and business attributes | Application instrumentation |
| Metric instruments and labels | Application instrumentation |
| Service identity | Application resource configuration, sometimes enriched by Collector |
| Propagation between services | Application SDK and instrumentations |
| Batching, retry, queueing | SDK and Collector |
| Redaction and attribute cleanup | Collector, with app-side defense too |
| Routing to backends | Collector |
| Tail sampling | Collector |
| Backend credentials | Collector or secret manager |
| Alerts and dashboards | Metrics/log/trace backends |

If the Collector is the first place that knows whether an operation was a RAG
retrieval, an agent tool call, or an LLM generation, the app is under-
instrumented. If every service hardcodes backend credentials and routing, the
platform is under-architected.

## Collector Components

Collector config is built from components. Components are configured at the top
level and enabled in `service.pipelines`.

| Component | Direction | Examples |
| --- | --- | --- |
| Receiver | Into the Collector | `otlp`, `prometheus`, `filelog`, `hostmetrics`. |
| Processor | Between receiver and exporter | `memory_limiter`, `batch`, `attributes`, `resource`, `transform`, `tail_sampling`. |
| Exporter | Out of the Collector | `otlphttp`, `prometheusremotewrite`, `debug`, vendor exporters. |
| Connector | Between pipelines | Span-derived metrics, routing, pipeline fan-in/fan-out. |
| Extension | Collector capability | `health_check`, `pprof`, `zpages`, auth extensions. |

The `service` section enables components:

```yaml
service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/traces]
```

A configured component that is not referenced in a pipeline is not active.
Processor order matters.

## Common Topologies

### Direct Export

```text
service -> OTLP exporter -> backend
```

Use this for local development, prototypes, or very small systems.

Pros:

- simplest deployment;
- fewer moving parts;
- good for first tests.

Cons:

- backend endpoints and credentials live in every service;
- changing routing requires redeploying services;
- redaction and sampling policies are duplicated;
- fan-out is harder;
- backend outages can affect every service directly.

### Local Agent Or Sidecar Collector

```text
service -> local Collector -> backend
```

The Collector runs near the workload: same host, same VM, sidecar container, or
Kubernetes DaemonSet.

Use it when:

- services need a stable local endpoint;
- you want local buffering, retry, or redaction;
- host metrics and application telemetry should share a nearby pipeline;
- teams deploy services independently;
- network egress should be standardized through local infrastructure.

Tradeoff: every node or pod now runs a Collector, so you operate more Collector
instances. Keep local config small and push global policy to a gateway when the
system grows.

### Gateway Collector

```text
services -> Collector gateway -> backends
```

The Collector is a shared service, usually one per cluster, region, or
environment.

Use it when:

- many services share the same telemetry policy;
- backend credentials should be centralized;
- you need fan-out to several destinations;
- sampling, redaction, and routing should change without redeploying services;
- platform teams own observability infrastructure.

Tradeoff: the gateway becomes shared infrastructure. Scale it, load balance it,
and monitor it like any other production dependency.

### Agent-To-Gateway

```text
service -> local Collector -> gateway Collector -> backends
```

This is common at scale.

Local agent:

- receives app telemetry locally;
- batches and retries near the workload;
- collects host/container signals;
- forwards to a gateway.

Gateway:

- performs centralized redaction and routing;
- applies tail sampling;
- fans out to multiple backends;
- owns backend credentials.

This pattern gives both local resilience and centralized policy.

## Signal-Specific Pipelines

Do not assume traces, metrics, and logs should travel to the same backend.

Typical routing:

```text
traces -> trace backend and/or Langfuse
metrics -> Prometheus-compatible or vendor metrics backend
logs -> log search/storage backend
```

Collector pipelines are signal-specific:

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, attributes/redact, batch]
      exporters: [otlphttp/traces]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, attributes/redact, batch]
      exporters: [otlphttp/logs]
```

The same receiver can feed all three pipelines. The processors and exporters
should reflect each signal's needs.

## A Production Collector Config

This example:

- receives OTLP over gRPC and HTTP;
- exposes a health endpoint;
- protects memory;
- enriches resources with environment;
- redacts sensitive attributes from traces and logs;
- batches data;
- sends traces to Langfuse and a generic trace backend;
- sends metrics to Prometheus remote write;
- sends logs to a log backend.

```yaml
extensions:
  health_check:
    endpoint: 0.0.0.0:13133

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 5s
    limit_mib: 1024
    spike_limit_mib: 256

  resource/add_environment:
    attributes:
      - key: deployment.environment.name
        value: production
        action: upsert

  attributes/redact:
    actions:
      - key: http.request.header.authorization
        action: delete
      - key: http.request.header.cookie
        action: delete
      - key: db.statement
        action: delete
      - key: user.email
        action: hash
      - key: gen_ai.input.messages
        action: delete
      - key: gen_ai.output.messages
        action: delete

  batch:
    timeout: 5s
    send_batch_size: 512

exporters:
  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_STRING}"
      x-langfuse-ingestion-version: "4"

  otlphttp/traces:
    endpoint: https://apm.example.com/otlp
    headers:
      api-key: "${env:APM_API_KEY}"

  prometheusremotewrite:
    endpoint: https://prometheus.example.com/api/v1/write
    headers:
      authorization: "Bearer ${env:PROMETHEUS_TOKEN}"

  otlphttp/logs:
    endpoint: https://logs.example.com/otlp
    headers:
      api-key: "${env:LOGS_API_KEY}"

service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, resource/add_environment, attributes/redact, batch]
      exporters: [otlphttp/langfuse, otlphttp/traces]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource/add_environment, batch]
      exporters: [prometheusremotewrite]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource/add_environment, attributes/redact, batch]
      exporters: [otlphttp/logs]
```

Validate config before deploying:

```bash
otelcol validate --config=collector.yaml
```

In CI, validate every Collector config change. In production, deploy config
changes gradually because a bad telemetry config can create blind spots during
incidents.

## Processor Order

A practical default order:

```text
memory_limiter
  -> resource enrichment
  -> redaction/filter/transform
  -> sampling or routing
  -> batch
  -> exporters
```

Reasons:

- `memory_limiter` should run early so the Collector protects itself.
- resource enrichment should happen before routing rules that use resource attributes.
- redaction should happen before data crosses trust boundaries.
- tail sampling needs enough trace attributes available to make decisions.
- `batch` is usually near the end so exporters send efficient payloads.

Some processors are signal-specific. A processor referenced by multiple
pipelines gets a separate instance per pipeline.

## Langfuse In A Collector Pipeline

Langfuse accepts traces through an OTLP HTTP endpoint. Use a Collector
`otlphttp` exporter that points at the Langfuse OTEL base endpoint:

```yaml
exporters:
  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_STRING}"
      x-langfuse-ingestion-version: "4"
```

The `otlphttp` exporter appends the signal path for the traces pipeline. Do not
append `/v1/traces` unless the specific exporter or backend configuration
requires it.

Important Langfuse routing rules:

- Send traces to Langfuse; send general metrics to a metrics backend.
- Langfuse can map `gen_ai.request.model`, `gen_ai.response.model`, and
  `gen_ai.usage.*` into generation fields.
- Use `langfuse.trace.metadata.*` or `langfuse.observation.metadata.*` when you
  need metadata to be filterable in Langfuse.
- Langfuse currently supports OTLP over HTTP/protobuf and HTTP/JSON for
  ingestion; OTLP/gRPC ingestion is not supported.

For privacy, it is common to fan out traces like this:

```text
traces with rich LLM payloads -> Langfuse
operational traces with redacted payloads -> APM backend
metrics -> metrics backend
logs -> log backend
```

If you send the same raw trace stream to multiple backends, every backend gets
the same sensitive attributes. Use a dedicated redacted pipeline when that is
not acceptable.

## Routing And Fan-Out

Fan-out means sending one signal to multiple exporters:

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, attributes/redact, batch]
      exporters: [otlphttp/traces, otlphttp/langfuse]
```

That is simple but coarse. Every exporter receives the same processed data.

When destinations need different data shapes, create separate pipelines or
route before redaction:

```text
raw trace stream
  -> pipeline A: preserve LLM payloads -> Langfuse
  -> pipeline B: remove LLM payloads -> APM backend
```

Exact routing mechanics depend on Collector distribution and available
processors/connectors. The design question is always the same: which backend is
allowed to receive which attributes?

## Sampling Strategy

Sampling controls trace volume. It is not a substitute for metrics.

Start without sampling when possible. First learn:

- request volume by service and route;
- average spans per trace;
- error and latency distribution;
- backend cost;
- which attributes are needed for incident debugging.

Then sample intentionally.

### Head Sampling

Head sampling is decided at trace start, usually in the SDK:

```text
root span starts
  -> sampler decides sampled or not sampled
  -> child spans follow parent decision
```

Pros:

- simple;
- cheap;
- can be configured in SDKs;
- preserves whole traces when parent-aware.

Cons:

- cannot know future errors;
- cannot know final latency;
- cannot keep all traces matching attributes added later.

Use it for broad volume reduction when losing some interesting traces is
acceptable. Prefer parent-aware probability sampling.

### Tail Sampling

Tail sampling decides after the Collector has seen most or all spans in a trace:

```text
spans arrive
  -> Collector buffers by trace ID
  -> rules evaluate the complete trace
  -> keep or drop
```

Common policies:

- keep all error traces;
- keep traces above a latency threshold;
- keep traces from a new release;
- keep traces with specific routes, models, tenants, or feature flags;
- sample the remaining normal traffic probabilistically.

Example policy sketch:

```yaml
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: keep-errors
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: keep-slow
        type: latency
        latency:
          threshold_ms: 5000
      - name: sample-rest
        type: probabilistic
        probabilistic:
          sampling_percentage: 5
```

For a fuller Collector-side example that samples on exact attribute values,
error types, token and cost thresholds, release burn-in, tenant tiers, and
guardrail outcomes, see
[../examples/03_collector_prometheus_langfuse.md](../examples/03_collector_prometheus_langfuse.md).

Tail sampling requirements:

- all spans for a trace must reach the same sampling decision point;
- the Collector must hold traces in memory until the decision;
- `decision_wait` must be long enough for normal traces to finish;
- Collector memory, queue sizes, and dropped spans must be monitored.

At scale, use trace-ID-aware load balancing before the tail-sampling tier so all
spans for the same trace land on the same Collector instance.

## Metrics Are Not Sampled Traces

Do not build alerting totals only from sampled traces. A 5 percent trace sample
cannot accurately tell you exact request volume, token volume, error rate, or
SLO burn.

Emit metrics independently:

```text
traces -> sampled for investigation cost control
metrics -> complete aggregate signal for dashboards and alerts
logs -> searchable local details and audit-style records
```

Span-derived metrics can be useful for convenience, but they should be treated
carefully if the traces are sampled before derivation.

## Scaling The Collector

Scale the Collector like a production service.

Watch:

- CPU and memory;
- accepted, refused, and dropped spans/metrics/logs;
- exporter queue size;
- exporter send failures;
- batch size and latency;
- tail sampling decision latency;
- receiver request rate;
- backend throttling or 4xx/5xx responses.

Horizontal scale:

```text
services -> load balancer -> gateway Collector replicas -> backends
```

For most pipelines, any Collector replica can process any request. Tail sampling
is the exception: trace spans must be consistently routed to the same sampling
replica.

Collector capacity depends on:

- signal volume;
- spans per trace;
- attribute size;
- processor cost;
- exporter latency;
- backend throughput;
- queue and retry settings;
- tail sampling decision windows.

Large attributes, especially LLM prompts and outputs, can dominate bandwidth and
memory. Decide early where those payloads are allowed and how much to truncate.

## Resilience And Backpressure

Telemetry should help production; it should not take production down.

Use:

- `memory_limiter` to protect Collector memory;
- batching to reduce exporter overhead;
- exporter queues and retry where available;
- local agents when network links are unreliable;
- backend rate-limit monitoring;
- load shedding policies when telemetry volume spikes.

Understand failure modes:

| Failure | Result |
| --- | --- |
| Collector down and SDK has no retry/buffer | Service may drop telemetry. |
| Backend down | Collector queues then drops when queue fills. |
| Collector overloaded | Refused or dropped telemetry, higher memory and CPU. |
| Redaction misconfigured | Sensitive data may leave the network. |
| Tail sampler underprovisioned | Incomplete or dropped traces. |

Telemetry loss is usually preferable to application downtime, but silent
telemetry loss during an incident is painful. Alert on Collector self-telemetry.

## Security And Privacy

Secure the Collector as production infrastructure:

- bind receivers to private networks when possible;
- do not expose OTLP, pprof, zPages, or debug endpoints publicly;
- use TLS or mTLS for cross-network traffic;
- keep exporter credentials in secret managers or environment variables;
- restrict who can change Collector config;
- validate config in CI;
- redact or hash sensitive attributes before telemetry leaves your network;
- avoid raw prompts and retrieved documents in general APM backends;
- allowlist baggage keys and treat incoming baggage as untrusted;
- use separate pipelines when different backends have different data permissions.

Common sensitive fields:

- authorization headers;
- cookies;
- API keys;
- session tokens;
- full URLs with query strings;
- request and response bodies;
- raw prompts and completions;
- retrieved document contents;
- emails and names;
- payment, health, or contractual data.

For LLM systems, privacy is an architecture decision, not an instrumentation
afterthought. Decide which backend is allowed to receive prompt and completion
payloads, how masking works, who can access the data, and how long it is
retained.

## Collector Self-Telemetry

The Collector emits its own logs and metrics. Scrape or export them.

Useful questions:

- Is the Collector receiving telemetry?
- Is it dropping data?
- Are exporters failing?
- Are queues filling?
- Is memory close to the limiter?
- Is tail sampling making decisions in time?
- Did a config rollout change throughput?

Expose health checks for orchestration and use internal metrics for alerts.

Example operational alerts:

- Collector exporter send failures above zero for 10 minutes.
- Collector dropped spans or metric points above zero.
- Exporter queue utilization above 80 percent.
- Collector memory above 85 percent of limit.
- Tail sampling late decisions or policy errors.
- OTLP receiver request errors or refused data.

## Deployment Patterns

### Kubernetes

Common Kubernetes shapes:

| Pattern | Description |
| --- | --- |
| Sidecar | Collector runs in the same pod as the app. Strong isolation, more resource overhead. |
| DaemonSet | One Collector per node. Good for local node-level collection. |
| Deployment gateway | Shared Collector service. Good for central policy and fan-out. |
| Agent-to-gateway | DaemonSet or sidecar forwards to gateway. Common at scale. |

Kubernetes checklist:

- services export to a stable Collector DNS name;
- requests and limits are set for Collector pods;
- HPA or manual scaling is based on Collector metrics;
- config is managed through versioned manifests;
- receivers are not exposed through public load balancers;
- pod, namespace, node, and cluster resource attributes are added or detected;
- tail sampling receives all spans for a trace on the same tier.

### VMs Or Bare Metal

Common non-Kubernetes shape:

```text
service process -> local Collector service -> regional gateway -> backends
```

Use system service management for the local Collector, and keep app exporter
endpoints stable on localhost or a local network address.

### Serverless

Serverless functions need special care:

- cold starts must configure OTel once per runtime instance;
- the function must flush before freeze/exit if telemetry is buffered;
- direct export may be simpler than a local Collector;
- managed layers or extensions may provide instrumentation;
- keep payload capture small because invocation time matters.

## Environment Separation

Separate environments explicitly:

```text
deployment.environment.name = development | staging | production
service.namespace = payments | search | llm-platform
service.version = git sha or semver
```

Do not mix staging and production in the same dashboards without a clear
environment label. Do not use the same exporter credentials for all
environments unless your backend permissions are intentionally shared.

## Operational Checklist

Before production:

- Every service sets `service.name`, version, instance ID, and environment.
- SDKs export to a local or gateway Collector.
- Collector config is validated in CI.
- Receivers bind to private networks.
- Collector has health checks.
- Collector self-telemetry is scraped and alerted on.
- `memory_limiter` and `batch` processors are present.
- Sensitive attributes are removed, hashed, or routed only to approved backends.
- Trace context propagates across HTTP, gRPC, queues, and workers.
- Sampling policy is documented and tested.
- Metrics are emitted independently from traces.
- Short-lived workloads flush on shutdown.
- Backends are tested with realistic telemetry volume.

During incidents:

- Start with metric dashboards and alerts.
- Pivot to traces for examples.
- Use logs for local details and stack traces.
- Check Collector self-telemetry if data looks missing.
- Check recent deploys, prompt versions, model routes, and backend status.
- Verify whether sampling or redaction changed visibility.

## Production Failure Modes

No telemetry reaches backend:

- SDK exporter endpoint is wrong.
- Collector receiver is not enabled in `service.pipelines`.
- Network policy blocks service-to-Collector traffic.
- Exporter credentials are missing.
- Backend rejects payloads.

Telemetry reaches Collector but not backend:

- exporter is not listed in the pipeline;
- exporter endpoint or headers are wrong;
- backend rate limits or rejects payloads;
- processor drops or filters data unexpectedly;
- queue fills and data is dropped.

Traces are incomplete:

- propagation is broken at service boundaries;
- tail sampling tier does not receive all spans for a trace;
- SDK shuts down before spans flush;
- a service exports to a different Collector or backend;
- span limits drop large attributes or events.

Metrics are expensive:

- high-cardinality attributes are used;
- resource attributes include per-request values;
- histograms use too many dimensions;
- backend converts dotted names and labels unexpectedly;
- duplicate instrumentation records the same metric twice.

Sensitive data appears in a backend:

- app set raw content as span attributes;
- baggage carried unsafe values downstream;
- Collector redaction ran after fan-out;
- a backend received the raw pipeline instead of the redacted one;
- prompts and outputs were enabled without a masking policy.
