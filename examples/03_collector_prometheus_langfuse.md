# Example: Collector, Prometheus, Langfuse, and Alerts

Last checked against the Langfuse guide and official docs on 2026-06-18.

This example uses the OpenTelemetry Collector as a production routing layer:

- traces go to a general tracing backend;
- LLM and agent traces also go to Langfuse;
- metrics go to a Prometheus-compatible backend;
- logs go to a log backend;
- alert rules run from metrics, while Langfuse is used for investigation and quality workflows.

## Collector Configuration

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
    check_interval: 1s
    limit_mib: 1024
    spike_limit_mib: 256
  batch:
    timeout: 5s
    send_batch_size: 1024
  resource:
    attributes:
      - key: deployment.environment.name
        value: prod
        action: upsert
  attributes/redact:
    actions:
      - key: http.request.header.authorization
        action: delete
      - key: db.statement
        action: delete
  filter/langfuse:
    error_mode: ignore
    traces:
      span:
        - 'attributes["gen_ai.operation.name"] == nil and attributes["langfuse.observation.type"] == nil and attributes["llm.workflow"] != true'

exporters:
  otlp/main_traces:
    endpoint: traces.example.com:4317
    tls:
      insecure: false

  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${AUTH_STRING}"
      x-langfuse-ingestion-version: "4"

  prometheusremotewrite:
    endpoint: https://prometheus.example.com/api/v1/write

  otlp/logs:
    endpoint: logs.example.com:4317
    tls:
      insecure: false

service:
  pipelines:
    traces/main:
      receivers: [otlp]
      processors: [memory_limiter, resource, attributes/redact, batch]
      exporters: [otlp/main_traces]

    traces/langfuse:
      receivers: [otlp]
      processors: [memory_limiter, resource, attributes/redact, filter/langfuse, batch]
      exporters: [otlphttp/langfuse]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [prometheusremotewrite]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource, attributes/redact, batch]
      exporters: [otlp/logs]
```

Validate Collector config before deployment:

```bash
otelcol validate --config collector.yaml
```

## Application Environment

Applications export to the Collector, not directly to every backend.

```bash
export OTEL_SERVICE_NAME="agent-service"
export OTEL_RESOURCE_ATTRIBUTES="service.version=${RELEASE},deployment.environment.name=prod"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4317"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
```

The Collector filter above keeps spans that have GenAI attributes, Langfuse observation attributes, or a workflow marker. Set the marker on the root workflow span when a whole trace should be routed to Langfuse:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("rag.answer") as span:
    span.set_attribute("llm.workflow", True)
    span.set_attribute("langfuse.trace.name", "rag.answer")
    run_rag_workflow()
```

If a Python service uses the Langfuse SDK directly, it can still export Langfuse traces itself. Choose one path per service to avoid duplicate spans:

- SDK direct export: easiest for Python LLM code.
- Collector export: best when platform teams centralize telemetry.

## Alert Rules

Exact metric names can differ after export depending on your backend's translation rules. Prometheus remote write often normalizes dots to underscores.

### HTTP Error Rate

```yaml
groups:
  - name: llm-app-operational
    rules:
      - alert: HighHttp5xxRate
        expr: |
          sum(rate(http_server_request_duration_count{http_response_status_code=~"5.."}[5m]))
          /
          clamp_min(sum(rate(http_server_request_duration_count[5m])), 1)
          > 0.02
        for: 10m
        labels:
          severity: page
        annotations:
          summary: "HTTP 5xx rate is above 2%"
```

### LLM Latency

```yaml
      - alert: HighLlmLatency
        expr: |
          histogram_quantile(
            0.95,
            sum by (le, gen_ai_request_model) (
              rate(gen_ai_client_operation_duration_bucket[10m])
            )
          ) > 15
        for: 15m
        labels:
          severity: page
        annotations:
          summary: "LLM p95 latency is above 15 seconds"
```

### Token Usage Spike

```yaml
      - alert: TokenUsageSpike
        expr: |
          sum by (gen_ai_request_model, gen_ai_token_type) (
            rate(gen_ai_client_token_usage_sum[15m])
          )
          >
          2 * avg_over_time(
            sum by (gen_ai_request_model, gen_ai_token_type) (
              rate(gen_ai_client_token_usage_sum[15m])
            )[6h:15m]
          )
        for: 30m
        labels:
          severity: ticket
        annotations:
          summary: "Token usage is more than 2x recent baseline"
```

### Agent Tool Failures

```yaml
      - alert: AgentToolFailureRateHigh
        expr: |
          sum by (tool_name) (rate(agent_tool_failures_total[5m]))
          /
          clamp_min(sum by (tool_name) (rate(agent_tool_calls_total[5m])), 1)
          > 0.05
        for: 10m
        labels:
          severity: page
        annotations:
          summary: "Agent tool failure rate is above 5%"
```

### Empty Retrievals

```yaml
      - alert: RagEmptyRetrievalsHigh
        expr: |
          sum(rate(rag_retrieval_empty_total[10m]))
          /
          clamp_min(sum(rate(http_server_request_duration_count{http_route="/answer"}[10m])), 1)
          > 0.10
        for: 15m
        labels:
          severity: ticket
        annotations:
          summary: "RAG empty retrieval rate is above 10%"
```

## Quality Alerts from Langfuse

For quality signals, query Langfuse metrics or scores on a schedule and publish compact time series to the metrics backend.

Examples:

- average `answer_relevance` by trace name and prompt version;
- percentage of `user_accepted = 1`;
- safety pass rate;
- groundedness score p50 and p10;
- cost per successful answer;
- failed experiment gates.

Then alert on sustained drops:

```yaml
      - alert: AnswerQualityDropped
        expr: |
          avg_over_time(llm_quality_score{score_name="answer_relevance", trace_name="rag.answer"}[30m]) < 0.75
          and
          avg_over_time(llm_quality_score_count{score_name="answer_relevance", trace_name="rag.answer"}[30m]) > 100
        for: 30m
        labels:
          severity: ticket
        annotations:
          summary: "RAG answer relevance dropped below threshold"
```

## Incident Workflow

When an alert fires:

1. Open the alert and identify service, release, route, model, or tool labels.
2. Open Langfuse filtered by the same time window, trace name, release, model, tag, or score.
3. Inspect representative traces.
4. Classify the failure as retrieval, prompt, model, tool, guardrail, provider, or infrastructure.
5. Turn representative failures into dataset items.
6. Run an experiment for the proposed fix.
7. Deploy behind a release/version marker and watch both metrics and Langfuse dashboards.

## Production Notes

- Keep Collector receivers private; do not expose them publicly.
- Use TLS/mTLS or network policy between applications and Collectors.
- Redact sensitive headers and high-risk attributes in the Collector.
- Use `memory_limiter` and `batch` processors.
- Avoid routing duplicate traces to Langfuse from both SDK and Collector.
- Keep metric labels low-cardinality.
- Validate dashboards and alerts in staging before paging production teams.
