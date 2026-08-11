# The OpenTelemetry Collector Component

The Collector is a deployable component of the system, not a config file appended to a service. Treat it like a service: its own directory, its own image pin, its own configs per environment, its own monitoring.

---

## Why it exists

Without a Collector, every service holds backend endpoints and credentials, and changing a destination means redeploying every service.

```
application
    │ OTLP
    ▼
OpenTelemetry Collector
    ├── traces  → trace backend (and/or Langfuse)
    ├── metrics → metrics backend
    └── logs    → log backend
```

With it, applications export plain OTLP to one internal endpoint and know nothing about vendors. Credentials, redaction, sampling, retries, and fan-out all live in one place, changed without touching application code.

Keep vendor-specific configuration at the Collector boundary. Vendor attribute names and API keys should not appear in business code.

---

## Layout

In a monorepo it lives beside the services, following the repo's own convention:

```
services/
    api/
    worker/
    otel-collector/
        Dockerfile
        config.dev.yaml
        config.staging.yaml
        config.prod.yaml
        README.md
```

`apps/` instead of `services/` if that is what the repo uses. Match the existing convention rather than introducing a new top-level directory.

Three separate configs, not one with conditionals. Development and production want genuinely different behaviour — full retention versus sampling, debug exporters versus real backends — and a single templated file makes both harder to read and to review.

---

## Choosing a distribution

Configuration cannot enable a component the binary does not contain. Pick the distribution by the components your config actually references.

| Distribution | Image | Choose when |
| --- | --- | --- |
| Core | `otel/opentelemetry-collector` | A narrow OTLP-in, OTLP-out pipeline |
| Contrib | `otel/opentelemetry-collector-contrib` | You need `tail_sampling`, `transform`, `file_storage`, `prometheus_remote_write`, or a vendor exporter |
| Kubernetes | `ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-k8s` | Kubernetes, and its component set covers your config |
| Custom (OCB) | your registry | You need a minimized, reviewed component set |

Most production configs in this skill need **Contrib**, because `tail_sampling` and `transform` are Contrib components.

Verify before deploying:

```bash
docker run --rm otel/opentelemetry-collector-contrib:0.158.0 components
```

Compare the output against every component ID in your config. Look up the current release before pinning — the Collector moves fast, and `0.158.0` was current in early August 2026.

## Pin three things independently

1. the image version, and after promotion the image digest;
2. the Helm chart or Operator version;
3. the config revision.

Never run `latest`. A chart upgrade can change Services, probes, and security defaults without any change to your values file.

```dockerfile
# services/otel-collector/Dockerfile
FROM otel/opentelemetry-collector-contrib:0.158.0

# Config is baked in per environment via build arg, or mounted at runtime.
ARG CONFIG_FILE=config.dev.yaml
COPY ${CONFIG_FILE} /etc/otelcol/config.yaml

CMD ["--config=/etc/otelcol/config.yaml"]
```

Mounting the config at runtime (a ConfigMap or a volume) is equally valid and usually better in Kubernetes, because a config change does not require a rebuild. Baking it in gives you an immutable artifact. Pick one and say which.

---

## Topology

| Topology | Use when | Cost |
| --- | --- | --- |
| Gateway Deployment | Central routing, credentials, redaction, fan-out for many services | Shared dependency: needs ≥2 replicas and load balancing |
| Sidecar | One Collector failure must be isolated to one pod, or `localhost` is required | Highest overhead; one per replica |
| DaemonSet agent | Node-local logs/host metrics, or a node-local endpoint for workloads | One failure affects its node |
| Agent → gateway | Node-local collection plus central policy | Two tiers to configure, monitor, upgrade |

**Default: a gateway Deployment.** Use a DaemonSet only for work that is genuinely node-local, not because agents feel more production-like.

Tail sampling is the one exception to "any replica can handle any request": all spans of a trace must reach the same instance. That needs trace-ID-aware routing in front of the sampling tier — see `production.md`.

---

## Backend routing

The routing decision is: **which backend is allowed to receive which attributes?**

```
APM / general trace backend   trace shape, durations, error types, models,
                              token counts — no prompts or outputs
Metrics backend               aggregates only; no IDs or content
Log backend                   application logs, correlated, masked
Langfuse                      prompts, outputs, sessions, scores — when policy allows
```

Fanning one pipeline out to two exporters is simple but coarse: both destinations get identical data. When they need different data, use separate pipelines.

```yaml
service:
  pipelines:
    traces/apm:
      receivers: [otlp]
      processors: [memory_limiter, redact_payloads, batch]
      exporters: [otlphttp/apm]

    traces/langfuse:
      receivers: [otlp]
      processors: [memory_limiter, transform/langfuse, batch]
      exporters: [otlphttp/langfuse]
```

### If Langfuse is one of the destinations

- Langfuse ingests over **OTLP/HTTP** only. An OTLP/gRPC exporter pointed at it fails.
- Authentication is HTTP Basic with base64 of `public_key:secret_key`. Build it without a trailing newline and inject it from a secret store.
- Send **complete traces**, not just the spans containing `gen_ai.request.model`. Filtering to model leaves discards the root, HTTP, retrieval, and tool spans and produces orphaned, unreadable agent traces.
- Langfuse is a trace backend. Operational metrics go to the metrics backend.
- `langfuse.*` attributes are added in the Langfuse pipeline by a `transform` processor, never in application code.

```yaml
exporters:
  otlphttp/langfuse:
    # The otlphttp exporter appends /v1/traces for the traces pipeline.
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_STRING}"
      x-langfuse-ingestion-version: "4"
```

Use the correct regional or self-hosted base URL. The v4 header selects
real-time ingestion; omitting it can delay visibility. Re-check the requirement
when upgrading the compatibility contract in `../compatibility.md`.

---

## Routing GenAI traces without breaking them

To send only GenAI workflows to Langfuse while everything goes to the APM backend, route by **complete trace**, never by individual span attributes.

Two workable approaches:

1. **Separate receivers.** GenAI services send to a second OTLP receiver on different ports; only that receiver feeds the Langfuse pipeline.
2. **A neutral trace-wide marker.** Set `app.telemetry.category="genai"` on
   every span and evaluate it in a trace-aware component after assembly. Use
   baggage for this only when the user explicitly requested the cross-service
   value and `../tracing/baggage.md` was loaded; otherwise prefer separate
   receivers or a service-owned resource/span enrichment rule.

A span-level filter on `gen_ai.request.model` looks correct and is not: it keeps the model calls and drops everything that explains them.

---

## Validate before deploying

Run the exact production image against the exact config:

```bash
docker run --rm \
  --volume "${PWD}/config.prod.yaml:/etc/otelcol/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.158.0 \
  validate --config=/etc/otelcol/config.yaml
```

Do this in CI on every config change. A bad telemetry config creates blind spots exactly when you need visibility.

Then check both ends:

```bash
curl --fail http://127.0.0.1:13133/            # health
curl --fail http://127.0.0.1:8888/metrics | grep '^otelcol_'
```

A health check proves the process is running. It does **not** prove the backend is accepting data — for that, watch the exporter counters and then find the data in the backend.

---

## Monitor it like a service

The Collector emits its own metrics on `:8888`. Alert on:

- exporter send failures above zero for 10 minutes;
- dropped spans, metric points, or log records above zero;
- exporter queue utilisation above 80%;
- memory above 85% of the `memory_limiter` limit;
- OTLP receiver refusals;
- tail-sampling late or errored decisions.

Silent telemetry loss during an incident is worse than no telemetry, because you will trust the empty dashboard.

---

## Security

- Bind receivers to private networks; never expose OTLP, pprof, or zPages publicly.
- TLS or mTLS across trust boundaries.
- Backend credentials from a secret store, injected only into the Collector — never into application containers.
- Redact before data crosses a trust boundary.
- Treat OTLP headers containing Basic Auth as credentials: not in ConfigMaps, not in committed `.env` files, not in CI logs.

---

## Then

- `dev_staging.md` for the development and staging configs;
- `production.md` for sampling, redaction, and resilience in production.
