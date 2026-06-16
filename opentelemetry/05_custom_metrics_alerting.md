# Custom Metrics And Alerting

Traces are for investigation. Metrics are for detection. A production guide needs both.

This page shows how to design custom metrics, export them, and turn them into practical alerts for distributed systems and LLM applications.

## Metric Design Principles

Good metrics are:

- low-cardinality;
- named consistently;
- dimensioned by operationally useful labels;
- emitted independently from tracing sampling;
- tied to an owner and a runbook.

Avoid labels with unbounded values:

| Do | Avoid |
| --- | --- |
| `http.route="/chat"` | `url.full="/chat/123?token=..."` |
| `gen_ai.request.model="gpt-4o-mini"` | `prompt="full prompt text"` |
| `app.outcome="error"` | `exception.message="...unique..."` |
| `app.tenant.tier="enterprise"` | `user.email="person@example.com"` |

## Useful Metrics For Production Systems

| Metric | Instrument | Labels | Alert use |
| --- | --- | --- | --- |
| `http.server.request.duration` | Histogram | route, method, status | Latency SLOs. |
| `http.server.requests` | Counter | route, method, status | Error rate and traffic drop. |
| `worker.queue.depth` | ObservableGauge | queue | Backlog and saturation. |
| `worker.jobs.processed` | Counter | queue, outcome | Worker failures. |
| `db.client.operation.duration` | Histogram | db system, operation | Database latency. |
| `llm.request.duration` | Histogram | provider, model, route, outcome | LLM latency and timeouts. |
| `llm.requests` | Counter | provider, model, route, outcome | LLM error rate and volume. |
| `llm.tokens` | Counter | provider, model, token_type | Cost and capacity. |
| `rag.retrieved_documents` | Histogram | strategy | Retrieval health. |
| `agent.tool.calls` | Counter | tool_name, outcome | Tool failures and loops. |

## Python Custom Metrics Example

```python
from opentelemetry import metrics

meter = metrics.get_meter("chat-api")

agent_tool_calls = meter.create_counter(
    "agent.tool.calls",
    unit="{call}",
    description="Tool calls executed by agents.",
)

agent_steps = meter.create_histogram(
    "agent.steps",
    unit="{step}",
    description="Number of reasoning/tool steps per agent run.",
)

guardrail_blocks = meter.create_counter(
    "guardrail.blocks",
    unit="{block}",
    description="Responses blocked by guardrails.",
)


def run_tool(tool_name: str, args: dict) -> dict:
    outcome = "success"
    try:
        return tools[tool_name](**args)
    except Exception:
        outcome = "error"
        raise
    finally:
        agent_tool_calls.add(
            1,
            {
                "agent.tool.name": tool_name,
                "app.outcome": outcome,
            },
        )
```

Keep `tool_name` bounded. If users can define arbitrary tool names, normalize or bucket them.

## Export Patterns

Most teams export custom metrics through OTLP to a Collector and then to a metrics backend:

```text
Python MeterProvider
  -> OTLPMetricExporter
  -> OpenTelemetry Collector
  -> Prometheus remote write, Datadog, Grafana Cloud, etc.
```

Collector pipeline:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 5s
    limit_mib: 512
  batch:

exporters:
  prometheusremotewrite:
    endpoint: https://prometheus.example.com/api/v1/write

service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]
```

Metric names may be transformed by the backend. For Prometheus, dotted names are commonly represented with underscores and counters usually appear with a `_total` suffix. Confirm final names in your backend before writing alerts.

## Alert Design

Alerts should represent user or business impact, not every weird internal value.

Use this pattern:

1. Define an SLO or concrete failure mode.
2. Pick one or two metrics that detect it.
3. Add labels that identify ownership without exploding cardinality.
4. Choose a window and threshold.
5. Attach a runbook that tells the responder what to check next.

Prefer multi-window alerts for paging. A 5-minute burn catches fast incidents; a 1-hour burn catches slower degradation.

## Practical Alerts

The PromQL names below are examples. Adjust them to your backend's actual metric names.

### High HTTP Error Rate

```yaml
- alert: ChatApiHigh5xxRate
  expr: |
    sum(rate(http_server_requests_total{http_response_status_code=~"5.."}[5m]))
    /
    sum(rate(http_server_requests_total[5m]))
    > 0.02
  for: 10m
  labels:
    severity: page
    service: chat-api
  annotations:
    summary: "chat-api 5xx rate is above 2%"
    runbook: "Check recent deploys, upstream provider errors, and slow traces."
```

### High LLM Latency

```yaml
- alert: LlmP95LatencyHigh
  expr: |
    histogram_quantile(
      0.95,
      sum(rate(llm_request_duration_seconds_bucket[10m]))
      by (le, gen_ai_request_model, http_route)
    ) > 8
  for: 15m
  labels:
    severity: ticket
  annotations:
    summary: "LLM p95 latency is above 8 seconds"
    runbook: "Check provider status, model mix, streaming TTFT, retries, and trace examples."
```

### LLM Error Spike

```yaml
- alert: LlmErrorRateHigh
  expr: |
    sum(rate(llm_requests_total{app_outcome="error"}[5m]))
    /
    sum(rate(llm_requests_total[5m]))
    > 0.05
  for: 10m
  labels:
    severity: page
  annotations:
    summary: "LLM request errors exceed 5%"
```

### Token Usage Spike

```yaml
- alert: LlmInputTokenSpike
  expr: |
    sum(rate(llm_tokens_total{gen_ai_token_type="input"}[15m]))
    >
    2 * sum(rate(llm_tokens_total{gen_ai_token_type="input"}[15m] offset 1d))
  for: 20m
  labels:
    severity: ticket
  annotations:
    summary: "Input token usage is more than 2x the same window yesterday"
    runbook: "Check prompt version, retrieval document count, and traffic mix."
```

### Tool Failure Rate

```yaml
- alert: AgentToolFailureRateHigh
  expr: |
    sum(rate(agent_tool_calls_total{app_outcome="error"}[10m])) by (agent_tool_name)
    /
    sum(rate(agent_tool_calls_total[10m])) by (agent_tool_name)
    > 0.10
  for: 15m
  labels:
    severity: ticket
  annotations:
    summary: "Agent tool failure rate is above 10%"
```

### Retrieval Health

```yaml
- alert: RagEmptyRetrievalsHigh
  expr: |
    sum(rate(rag_retrieval_requests_total{app_outcome="empty"}[10m]))
    /
    sum(rate(rag_retrieval_requests_total[10m]))
    > 0.05
  for: 15m
  labels:
    severity: ticket
  annotations:
    summary: "More than 5% of RAG retrievals return no documents"
```

## LLM Quality Alerts

Latency and errors are not enough for LLM systems. You also need quality metrics.

Sources of quality metrics:

- Langfuse scores from human feedback;
- Langfuse LLM-as-judge scores;
- deterministic code evaluators;
- guardrail pass/fail scores;
- experiment results in CI;
- custom counters emitted by the app when a guardrail blocks or a fallback path is used.

Langfuse metrics are derived from traces and scores and can be viewed in dashboards or queried through the metrics/API layer. For paging, many teams either:

- export/poll Langfuse metrics into an alerting system;
- emit parallel OTel metrics for guardrails and high-confidence quality failures;
- block deploys in CI using Langfuse experiments rather than waking responders.

Example quality alert design:

| Alert | Source | Threshold |
| --- | --- | --- |
| Faithfulness regression | Langfuse score analytics or metrics API | 1h average drops below 0.85. |
| User negative feedback spike | Langfuse score or app OTel counter | negative feedback rate > 8%. |
| Safety guardrail block spike | OTel counter | block rate doubles day-over-day. |
| Release quality regression | Langfuse dashboard/experiment | new release below baseline by 5%. |

## Runbook Template

Every alert should include:

- What user impact does this represent?
- Which service/team owns it?
- What dashboard should the responder open first?
- Which trace filters should they use?
- What recent deploys or prompt versions should they compare?
- What rollback or mitigation is available?
- When should the alert be downgraded to a ticket?

For LLM incidents, the trace investigation usually starts with:

1. Filter by affected model, route, release, prompt version, or tenant tier.
2. Open slow/error examples in traces.
3. Compare prompt input size, retrieved document count, model parameters, and tool call count.
4. Check Langfuse scores and user feedback for quality impact.
5. Decide whether to rollback code, prompt, model, retrieval config, or provider route.

