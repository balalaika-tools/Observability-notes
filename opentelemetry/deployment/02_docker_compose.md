# Run The Collector With Docker Compose

> **Who this is for**: Engineers who want a reproducible Collector deployment for local development, integration tests, or a small single-host environment.

Read **[Deployment Decisions](01_deployment_decisions.md)** first so the image
and topology are intentional.

---

## 1. 🗺️ What This Baseline Proves

This deployment proves four things before Kubernetes is involved:

1. the chosen Collector distribution contains every configured component;
2. the Collector config parses and starts;
3. an application can reach both OTLP receivers;
4. the backend accepts the configured protocol, TLS connection, and credentials.

It is not highly available. One process, host, disk, and network path remain one
failure domain.

```text
application on host or Compose network
  -> Collector container :4317/:4318
  -> HTTPS OTLP backend

operator
  -> health endpoint :13133
  -> internal Prometheus metrics :8888
```

---

## 2. 🛠️ Collector Configuration

Save this as `collector.yaml`. It receives all three stable signals, protects
memory, batches data, retries temporary backend failures, exposes health and
internal metrics, and exports over OTLP/HTTP.

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
    check_interval: 1s
    limit_mib: 768
    spike_limit_mib: 192
  batch:
    timeout: 5s
    send_batch_size: 512

exporters:
  otlphttp/backend:
    endpoint: ${env:OTLP_BACKEND_ENDPOINT}
    headers:
      Authorization: ${env:OTLP_BACKEND_AUTHORIZATION}
    sending_queue:
      enabled: true
      num_consumers: 10
      queue_size: 2000
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 15m

service:
  extensions: [health_check]
  telemetry:
    metrics:
      level: normal
      readers:
        - pull:
            exporter:
              prometheus:
                host: 0.0.0.0
                port: 8888
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/backend]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/backend]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/backend]
```

Adapt this before use:

- Remove pipelines the backend does not accept.
- Add required redaction before `batch`; see
  [Production Architecture](../03_production_architecture.md).
- Use the backend's exact endpoint and authentication header. Some backends use
  separate endpoints or credentials per signal.
- Keep `memory_limiter` first. Its limit must stay below the container's memory
  limit so the process can shed load before the runtime is killed.
- Calculate queue size from measured batch size and tolerated outage; a larger
  queue consumes more memory and does not repair a slow backend.

> ⚠️ **Watch out:** A health-check response only proves that the Collector process is running; it does not prove that the backend is accepting telemetry.

---

## 3. 📦 Compose The Container

Save this as `compose.yaml` next to `collector.yaml`:

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.157.0
    command:
      - --config=/etc/otelcol/collector.yaml
    restart: unless-stopped
    environment:
      OTLP_BACKEND_ENDPOINT: ${OTLP_BACKEND_ENDPOINT:?set OTLP_BACKEND_ENDPOINT}
      OTLP_BACKEND_AUTHORIZATION: ${OTLP_BACKEND_AUTHORIZATION:?set OTLP_BACKEND_AUTHORIZATION}
    volumes:
      - ./collector.yaml:/etc/otelcol/collector.yaml:ro
    ports:
      - 127.0.0.1:4317:4317
      - 127.0.0.1:4318:4318
      - 127.0.0.1:13133:13133
      - 127.0.0.1:8888:8888
    stop_grace_period: 30s
```

The loopback bindings prevent another machine from sending arbitrary telemetry
to the development Collector. Containers on the same Compose network should
send to `otel-collector:4317` or `otel-collector:4318`, not the host mapping.

Provide credentials without committing them:

```bash
export OTLP_BACKEND_ENDPOINT="https://otlp.example.com"
export OTLP_BACKEND_AUTHORIZATION="Bearer replace-from-secret-store"

docker compose config
docker compose up -d
```

`docker compose config` expands variables and can print secrets. Do not paste
its output into tickets or CI logs. In a real deployment, inject the values from
the platform's secret store instead of a checked-in `.env` file.

---

## 4. ✅ Validate Before Starting

Run the exact production image against the exact config:

```bash
docker run --rm \
  --env OTLP_BACKEND_ENDPOINT="https://otlp.example.com" \
  --env OTLP_BACKEND_AUTHORIZATION="Bearer validation-placeholder" \
  --volume "${PWD}/collector.yaml:/etc/otelcol/collector.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.157.0 \
  validate --config=/etc/otelcol/collector.yaml
```

Then inspect startup and health:

```bash
docker compose logs --tail=100 otel-collector
curl --fail --silent --show-error http://127.0.0.1:13133/
curl --fail --silent --show-error http://127.0.0.1:8888/metrics \
  | grep '^otelcol_'
```

Configure a local application with one protocol at a time:

```bash
# OTLP/HTTP base endpoint. The SDK appends /v1/traces, /v1/metrics, or /v1/logs.
export OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318"
export OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
export OTEL_SERVICE_NAME="deployment-smoke-test"
```

Success requires evidence at both boundaries:

- Collector receive counters increase.
- Collector export counters increase without sustained failures.
- The backend can find `service.name=deployment-smoke-test`.

Container logs alone are not proof of end-to-end delivery.

---

## 5. 🗄️ Add A Persistent Sending Queue Only Deliberately

An in-memory sending queue loses buffered telemetry when the container restarts.
If restart durability is required, use the Contrib `file_storage` extension and
a persistent volume:

```yaml
extensions:
  health_check:
    endpoint: 0.0.0.0:13133
  file_storage:
    directory: /var/lib/otelcol

exporters:
  otlphttp/backend:
    endpoint: ${env:OTLP_BACKEND_ENDPOINT}
    headers:
      Authorization: ${env:OTLP_BACKEND_AUTHORIZATION}
    sending_queue:
      enabled: true
      storage: file_storage
      queue_size: 2000

service:
  extensions: [health_check, file_storage]
```

Mount storage:

```yaml
services:
  otel-collector:
    volumes:
      - ./collector.yaml:/etc/otelcol/collector.yaml:ro
      - collector-data:/var/lib/otelcol

volumes:
  collector-data:
```

Verify that the image's non-root user can write the volume. Do not "fix"
permission errors by permanently running the Collector as root. Initialize the
volume ownership once, bound disk use, monitor free space, and test recovery by
stopping the backend, enqueueing telemetry, restarting the Collector, and
restoring the backend.

A persistent queue is a write-ahead log, not an unlimited durable message bus.
It can still lose data when the disk fails, fills, is deleted, or the retry
window expires.

---

## 6. ⚠️ Production Gaps

Before moving this single-host pattern into production, answer:

| Gap | Required production decision |
| --- | --- |
| Availability | Multiple instances and a protocol-aware load balancer, or an accepted single-host outage. |
| TLS on ingress | TLS or mTLS when traffic crosses a trust boundary. |
| Authentication | Prevent untrusted clients from consuming telemetry capacity or injecting false data. |
| Secrets | Platform secret store, rotation, and least-privilege backend token. |
| Resource isolation | CPU/memory limits, disk quota, and noisy-neighbor protection. |
| Monitoring | Scrape `:8888`, alert on refusal, enqueue failure, send failure, queue saturation, restarts, and memory pressure. |
| Upgrade | Pinned image digest, config validation, canary, drain behavior, and rollback. |
| Stateful processing | Trace-aware routing before tail sampling or span-to-metrics aggregation. |

For Kubernetes, implement those controls through the chart or Operator rather
than translating Compose line by line.

---

## 🔗 Official References

- [Install the Collector with Docker](https://opentelemetry.io/docs/collector/install/docker/)
- [Collector configuration](https://opentelemetry.io/docs/collector/configuration/)
- [Collector internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/)
- [Collector resiliency](https://opentelemetry.io/docs/collector/resiliency/)
- [File storage extension](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/extension/storage/filestorage)

**Next**: [Part 3: Kubernetes Installation](03_kubernetes_installation.md)
