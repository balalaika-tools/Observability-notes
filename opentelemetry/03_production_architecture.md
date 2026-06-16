# Production Architecture

OpenTelemetry works without a Collector, but production systems usually use one. The Collector gives you a standard place to receive telemetry, batch it, protect memory, redact sensitive data, sample traces, and fan out to multiple backends.

## Common Topologies

### Direct Export

```text
service -> OTLP exporter -> backend
```

Use direct export for local development, prototypes, or very small deployments. It is the simplest path but puts backend-specific endpoints, credentials, retries, and routing directly in every service.

### Agent Or Sidecar Collector

```text
service -> local collector -> backend
```

The Collector runs on the same host or pod as the workload. This reduces application network configuration and lets the local Collector batch and retry exports. In Kubernetes, this can be a sidecar or DaemonSet.

Use it when:

- teams own services independently;
- you need local buffering or redaction;
- network egress should be centralized through a nearby process.

### Gateway Collector

```text
services -> collector gateway -> backends
```

The Collector is a shared regional or cluster service. It gives platform teams a single place to enforce routing, sampling, redaction, and exporter configuration.

Use it when:

- many services share the same observability pipeline;
- you need centralized fan-out;
- you want to change backends without redeploying services.

### Agent-To-Gateway

```text
service -> local collector -> gateway collector -> backends
```

This is common at scale. The local Collector handles near-service buffering and host signals. The gateway handles centralized policy, tail sampling, and backend fan-out.

## A Production Collector Config

This example sends traces to Langfuse and a generic trace backend, metrics to Prometheus remote write, and logs to a log backend. Adjust exporters to your infrastructure.

```yaml
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

  batch:
    timeout: 5s
    send_batch_size: 512

  resource/add_environment:
    attributes:
      - key: deployment.environment.name
        value: production
        action: upsert

  attributes/redact:
    actions:
      - key: http.request.header.authorization
        action: delete
      - key: db.statement
        action: delete
      - key: user.email
        action: hash

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

Validate Collector config before deploying:

```bash
otelcol validate --config=collector.yaml
```

## Langfuse In A Collector Pipeline

Langfuse accepts traces over OTLP/HTTP at `/api/public/otel`. It currently supports OTLP over HTTP/protobuf and HTTP/JSON for traces; OTLP/gRPC is not supported for Langfuse ingestion. Use the Collector `otlphttp` exporter with the base endpoint:

```yaml
exporters:
  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_STRING}"
      x-langfuse-ingestion-version: "4"
```

The `otlphttp` exporter appends `/v1/traces` for the traces pipeline. Do not configure the exporter endpoint as `/v1/traces` in Collector YAML unless that specific exporter configuration requires it.

Metrics and logs should usually go to metric and log backends. Langfuse derives LLM metrics from traces and scores; it is not a replacement for a general-purpose metrics time series database.

## Sampling Strategy

Sampling controls trace cost and volume. Do not start with sampling until you know your data shape.

### Head Sampling

Head sampling is decided early. It is cheap and easy:

```text
root span starts -> sampling decision -> all children follow parent decision
```

Use it for broad volume reduction when losing some traces is acceptable. Prefer parent-aware probability sampling so child spans do not disagree with the root decision.

### Tail Sampling

Tail sampling happens after the Collector has seen enough spans to evaluate a trace. It can keep:

- all error traces;
- traces above a latency threshold;
- traces from a new release;
- traces with specific attributes;
- a probabilistic sample of normal traffic.

Tail sampling requires all spans for the same trace to reach the same Collector instance. At scale, use a trace-ID-aware load-balancing exporter before the tail-sampling tier.

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

Tail sampling is powerful but stateful. Monitor the Collector's memory, dropped spans, queue sizes, and sampling processor latency.

## Security And Privacy

Secure the Collector as production infrastructure:

- bind receivers to private networks when possible;
- do not expose OTLP, pprof, zPages, or debug endpoints publicly;
- use TLS or mTLS for cross-network traffic;
- keep exporter credentials in secret managers or environment variables;
- redact or hash sensitive attributes before telemetry leaves your network;
- keep baggage allowlisted and non-sensitive;
- avoid raw prompts or documents in general APM backends unless you have explicit privacy controls.

For LLM systems, decide where prompt and completion payloads are allowed to go. It is common to send rich LLM payloads to Langfuse, while sending only low-cardinality operational attributes to general APM and metrics backends.

## Operational Checklist

Before production:

- Every service sets `service.name`, version, instance ID, and environment.
- SDKs export to a local or gateway Collector, not directly to many backends.
- Collector config is validated in CI.
- Collector has `memory_limiter` and `batch` processors.
- Sensitive attributes are removed or hashed.
- Trace context propagates across HTTP, queues, and workers.
- Sampling policy is documented and tested.
- Metrics are emitted independently of traces.
- Collector self-telemetry is scraped and alerted on.
- Short-lived workloads flush on shutdown.

During incidents:

- Use alerts and dashboards from metrics to detect the problem.
- Pivot to traces for examples of affected requests.
- Use logs for detailed local messages and stack traces.
- Use Langfuse sessions, generations, scores, and prompt versions for LLM quality and cost analysis.

