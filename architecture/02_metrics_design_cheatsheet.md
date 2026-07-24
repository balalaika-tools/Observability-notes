# Production AI Metrics Cheatsheet

Last verified against OpenTelemetry HTTP and GenAI metric conventions on 2026-07-24.

```text
OTel metrics -> Collector -> metrics backend -> dashboards, SLOs, alerts
agent traces + scores -----------------------> agent backend for investigation
```

## 📐 Metric Rules

- Metrics detect fleet problems; traces explain individual requests.

> 💡 **Key insight:** Metrics answer "is something wrong at fleet scale?"; traces answer "why did this specific request fail?" — both are needed and neither substitutes for the other.

- Emit metrics independently from trace sampling.
- Prefer standard semantic-convention metrics.
- Use counters for totals, histograms for distributions, UpDownCounters for in-flight work, and gauges for current state.
- Alert on rates, ratios, and p95/p99 distributions—not a single average.
- Attach trace IDs through exemplars, never as metric labels.

> ⚠️ **Watch out:** Adding trace IDs as metric labels gives every trace its own time series — this exhausts cardinality budgets and will crash most metrics backends.

- Every metric needs an owner, dashboard purpose, cardinality budget, and retention decision.

## 🏷️ Safe Dimensions

Good bounded dimensions:

```text
service.name
deployment.environment.name
cloud.region
http.route
http.request.method
http.response.status_code
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.agent.name         only from a bounded registry
gen_ai.tool.name          only from a bounded registry
gen_ai.workflow.name
app.workflow.version
app.trace.dimension.tenant_tier
app.outcome
error.type                normalized, low-cardinality
```

Never label metrics with user/session/trace/request IDs, prompt or response content, raw URLs, exception messages, tool arguments, response IDs, or document IDs.

> ⚠️ **Watch out:** Each unique label value creates a new time series — high-cardinality labels like user IDs or trace IDs can exhaust backend storage and cause query timeouts.

## 📊 HTTP and Dependency Metrics

| Metric | Production use |
| --- | --- |
| `http.server.request.duration` | Traffic from histogram count, p50/p95/p99 latency, error ratio by status or `error.type` |
| `http.server.active_requests` | Concurrency and saturation |
| `http.server.request.body.size` | Payload growth and abuse, opt-in |
| `http.server.response.body.size` | Response growth, opt-in |
| `http.client.request.duration` | Provider/tool/dependency latency and errors |
| `http.client.active_requests` | Outbound pressure |
| `db.client.operation.duration` | Database/vector-store latency and failures |
| `messaging.client.operation.duration` | Queue publish/consume latency |
| `app.worker.queue.depth` | Backlog |
| `app.worker.oldest_job.age` | User-visible queue delay |
| runtime/process/container metrics | CPU, memory, GC/event-loop delay, restarts, file descriptors, network |

Useful HTTP SLIs:

```text
availability = successful requests / eligible requests
error rate   = failed requests / total requests
latency SLI  = requests below target / total requests
```

Use `http.route`, not raw paths. Define whether cancellations, 4xx responses, and timeouts count as failures before writing alerts.

## 📊 Standard GenAI Metrics

These are development conventions; pin and test the emitted version.

| Metric | Unit | Watch |
| --- | --- | --- |
| `gen_ai.client.operation.duration` | `s` | Model/provider p95 and p99 latency, errors, timeouts |
| `gen_ai.client.token.usage` | `{token}` | Input/output token distributions and prompt growth |
| `gen_ai.client.operation.time_to_first_chunk` | `s` | Streaming user experience |
| `gen_ai.client.operation.time_per_output_chunk` | `s` | Streaming stalls and chunk cadence |
| `gen_ai.workflow.duration` | `s` | Multi-agent workflow latency |
| `gen_ai.invoke_agent.duration` | `s` | One agent invocation |
| `gen_ai.invoke_agent.inference_calls` | `{inference_call}` | Model-call fan-out per invocation |
| `gen_ai.invoke_agent.tool_calls` | `{tool_call}` | Average and p95 tool calls per invocation |
| `gen_ai.execute_tool.duration` | `s` | Tool latency and error rate |

If hosting models, also collect:

- `gen_ai.server.request.duration`
- `gen_ai.server.time_to_first_token`
- `gen_ai.server.time_per_output_token`

Key derived views:

```text
model operation rate  = count/rate of gen_ai.client.operation.duration
model error rate     = failed model operations / model operations
tokens per operation = token histogram by gen_ai.token.type
cache token ratio    = cache-read input tokens / total input tokens
agent model fan-out  = avg/p95 gen_ai.invoke_agent.inference_calls
agent tool fan-out   = avg/p95 gen_ai.invoke_agent.tool_calls
```

## 📊 Product-Specific Agent Metrics

Use `app.*` only where no standard metric expresses the product fact.

| Metric | Instrument | Record |
| --- | --- | --- |
| `app.agent.run.count` | Counter | One per run with bounded `app.outcome` |
| `app.agent.active_runs` | UpDownCounter | Increment at start, decrement at finish |
| `app.agent.run.steps` | Histogram | Total reasoning/orchestration steps per completed run |
| `app.agent.run.retries` | Histogram | Provider and tool retries per run |
| `app.agent.run.handoffs` | Histogram | Agent-to-agent transfers per run |
| `app.agent.step_limit.count` | Counter | Runs stopped by maximum-step protection |
| `app.agent.fallback.count` | Counter | Model/provider/workflow fallback activations |
| `app.agent.cancelled.count` | Counter | User or system cancellations |

Do not duplicate standard metrics. For example, use `gen_ai.invoke_agent.tool_calls` for tool calls per invocation; add a custom counter only if you need a separate event-rate view.

## 📊 Tool, Retrieval, and Guardrail Metrics

| Area | Minimum production metrics |
| --- | --- |
| Tools | call rate, `gen_ai.execute_tool.duration`, error/timeout rate, argument-validation failures |
| Retrieval | duration, result-count histogram, empty-result rate, reranker duration, optional bounded score distribution |
| Embeddings | duration, token usage, batch size, error rate |
| Guardrails | allow/block/redact/error counts, decision latency, policy category |
| Memory | read/write duration, miss rate, persistence errors |
| Queues | depth, oldest-item age, processing duration, retry/dead-letter counts |

Example custom instruments:

```python
retrieval_results = meter.create_histogram(
    "app.retrieval.result_count",
    unit="{document}",
)
guardrail_decisions = meter.create_counter(
    "app.guardrail.decision.count",
    unit="{decision}",
)

retrieval_results.record(
    len(documents),
    {"app.retrieval.strategy": "hybrid_v3"},
)
guardrail_decisions.add(
    1,
    {"app.guardrail.decision": "block", "app.guardrail.policy": "pii"},
)
```

Keep strategies, policies, tool names, and outcomes bounded.

## 🧪 Cost and Quality

Operational health is not answer quality. Track both.

> 💡 **Key insight:** Low latency and low error rate do not mean answers are correct — quality requires separate evaluator scores or user feedback signals that operational metrics cannot provide.

| Signal | Recommended source |
| --- | --- |
| Input/output/cache/reasoning tokens | Standard GenAI metrics |
| Estimated or billed USD | Langfuse cost, provider billing export, or bounded `app.gen_ai.cost` metric |
| Task success / resolution | Product event or Langfuse score |
| User negative feedback | Product event or Langfuse score |
| Groundedness / faithfulness | Evaluator score |
| Safety / policy pass rate | Guardrail or evaluator |
| Refusal / abstention rate | Product metric |
| Citation coverage | Evaluator or product metric |
| Empty retrieval rate | Retrieval metric |

Quality rules:

- Version evaluator name and rubric.
- Publish sample count beside every score average.
- Compare score distributions and critical-failure rates, not only the mean.
- Page only on high-confidence safety or availability failures; ticket slower quality drift.
- Keep per-trace details in the agent backend and export only compact aggregates to the metrics backend.

## 🗺️ Starter Dashboard

One production dashboard should show:

1. Request rate, HTTP error rate, and p50/p95/p99 latency.
2. Agent success, timeout, cancellation, fallback, and step-limit rates.
3. Workflow and agent p95 latency.
4. Model calls, provider errors, rate limits, retries, and TTFC.
5. Input/output/cache/reasoning tokens and estimated cost.
6. Model calls and tool calls per agent run.
7. Tool latency/error rate and worst tools.
8. Retrieval latency, result count, and empty-result rate.
9. Guardrail decisions and safety failures.
10. Quality scores, user feedback, and sample volume.
11. Queue depth/age plus CPU, memory, and Collector export health.

## 🔔 Starter Alerts

Tie thresholds to SLOs and baselines:

- Fast/slow burn for HTTP availability and latency.
- Agent failure, timeout, cancellation, or step-limit spike.
- Provider error/rate-limit spike or p95 model latency regression.
- Streaming TTFC regression.
- Token or cost budget burn.
- Tool error/timeout spike.
- Empty retrieval or retrieval-latency spike.
- Guardrail/safety failure spike.
- Sustained quality-score or user-feedback regression with a minimum sample count.
- Queue age, saturation, or Collector export failure.

Each alert should link to a dashboard, owner, runbook, and filtered trace search.

## 🔗 References

- [Detailed metrics and alerting guide](../opentelemetry/05_custom_metrics_alerting.md)
- [Detailed GenAI observability guide](../opentelemetry/06_genai_and_llm_observability.md)
- [Langfuse evaluation and metrics](../langfuse/05_evaluation_metrics_alerting.md)
- [OpenTelemetry HTTP metrics](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
