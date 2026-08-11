# Development and Staging Collector Configuration

Development and staging optimise for **debugging visibility**, not cost. That means:

```
no trace sampling
no unnecessary filtering
full metrics
```

If you cannot reproduce a production incident in staging because staging sampled the trace away, the environment failed at its job. Add sampling to a lower environment only when telemetry volume genuinely makes it impractical, and say so in the config comments.

---

## Development

Small limits, a debug exporter you can actually read, no credentials.

```yaml
# services/otel-collector/config.dev.yaml
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
    limit_mib: 256
    spike_limit_mib: 64

  resource/environment:
    attributes:
      - key: deployment.environment.name
        value: development
        action: upsert

  batch:
    timeout: 1s          # short, so spans appear while you are still looking
    send_batch_size: 128

exporters:
  # Prints telemetry to the Collector log. Invaluable locally, never in prod.
  debug:
    verbosity: detailed
    sampling_initial: 5
    sampling_thereafter: 200

  otlphttp/traces:
    endpoint: ${env:TRACES_ENDPOINT}

  otlphttp/metrics:
    endpoint: ${env:METRICS_ENDPOINT}

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
      processors: [memory_limiter, resource/environment, batch]
      exporters: [debug, otlphttp/traces]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource/environment, batch]
      exporters: [debug, otlphttp/metrics]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource/environment, batch]
      exporters: [debug]
```

Notes on the choices:

- **No sampling processor at all.** Every trace is kept.
- **`debug` with `verbosity: detailed`** prints full spans. The `sampling_*` settings stop a busy local run from flooding the terminal.
- **Short batch timeout.** Five seconds feels broken when you are watching a terminal for a span you just triggered.
- **`memory_limiter` is still present.** It is cheap, and its absence in dev is how you discover in production that nobody ever tested with it.

### Compose

```yaml
# compose.yaml
services:
  otel-collector:
    build:
      context: ./services/otel-collector
      args:
        CONFIG_FILE: config.dev.yaml
    restart: unless-stopped
    ports:
      # Loopback only: nothing on the network can inject telemetry.
      - 127.0.0.1:4317:4317
      - 127.0.0.1:4318:4318
      - 127.0.0.1:13133:13133
      - 127.0.0.1:8888:8888
    stop_grace_period: 30s
```

Services on the same Compose network export to `http://otel-collector:4318`, not the host mapping.

---

## Staging

Staging should be production's shape with production's *retention* behaviour removed. Same processors, same exporters, same redaction — no sampling.

```yaml
# services/otel-collector/config.staging.yaml
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
    limit_mib: 512
    spike_limit_mib: 128

  resource/environment:
    attributes:
      - key: deployment.environment.name
        value: staging
        action: upsert

  # Identical to production. Redaction bugs must surface here, not in prod.
  attributes/drop_secrets:
    actions:
      - key: http.request.header.authorization
        action: delete
      - key: http.request.header.cookie
        action: delete
      - key: http.response.header.set_cookie
        action: delete
      - key: db.query.text
        action: delete
      - key: exception.message
        action: delete
      - key: exception.stacktrace
        action: delete

  batch:
    timeout: 5s
    send_batch_size: 512

exporters:
  otlphttp/traces:
    endpoint: ${env:TRACES_ENDPOINT}
    headers:
      Authorization: ${env:TRACES_AUTHORIZATION}
    sending_queue:
      enabled: true
      queue_size: 2000
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 5m

  otlphttp/metrics:
    endpoint: ${env:METRICS_ENDPOINT}
    headers:
      Authorization: ${env:METRICS_AUTHORIZATION}

  otlphttp/logs:
    endpoint: ${env:LOGS_ENDPOINT}
    headers:
      Authorization: ${env:LOGS_AUTHORIZATION}

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
      # No tail_sampling: staging keeps everything.
      processors: [memory_limiter, resource/environment, attributes/drop_secrets, batch]
      exporters: [otlphttp/traces]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource/environment, batch]
      exporters: [otlphttp/metrics]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource/environment, attributes/drop_secrets, batch]
      exporters: [otlphttp/logs]
```

The one thing staging must share with production is **redaction**. A staging config without it means the first real test of the redaction rules happens in production with real user data.

Use separate backend credentials per environment, and keep environments visually separated on dashboards by `deployment.environment.name`.

---

## Metrics are never sampled like traces

Even in production, metrics keep flowing at full fidelity. A sampled trace stream cannot produce accurate request counts, error rates, token totals, or SLO burn. There is no `tail_sampling` in any metrics pipeline on this page, and there should not be one in `production.md` either.

---

## Verify

Send one request through an instrumented service and confirm:

```bash
docker compose logs --tail=200 otel-collector | head -60
curl --fail http://127.0.0.1:8888/metrics | grep -E 'otelcol_(receiver_accepted|exporter_sent|exporter_send_failed)'
```

- receive counters increase;
- export counters increase, and `send_failed` stays at zero;
- the debug exporter prints spans with your `service.name` and populated attributes;
- the backend can find `service.name=<your service>`.

Container logs alone are not proof of delivery. Look in the backend.
