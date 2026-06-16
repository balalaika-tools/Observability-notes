# OpenTelemetry and Langfuse Examples

These examples show how to combine OpenTelemetry and Langfuse in real LLM and agent systems.

Read the concept guides first:

- [../opentelemetry/README.md](../opentelemetry/README.md)
- [../langfuse/README.md](../langfuse/README.md)

Then use these examples as implementation templates.

| File | Scenario |
| --- | --- |
| [01_rag_with_otel_and_langfuse.md](01_rag_with_otel_and_langfuse.md) | Single-service RAG with Langfuse traces and OpenTelemetry metrics |
| [02_multi_service_agent.md](02_multi_service_agent.md) | Gateway service calling an internal agent service with trace and baggage propagation |
| [03_collector_prometheus_langfuse.md](03_collector_prometheus_langfuse.md) | Collector routing to Langfuse and Prometheus-compatible metrics with alert examples |

## Integration Principle

Use the system that is best at each job:

- Langfuse: prompts, generations, retrievals, tools, agent traces, feedback, scores, datasets, quality/cost/latency analytics.
- OpenTelemetry traces: service-to-service causality and compatibility with the broader observability ecosystem.
- OpenTelemetry metrics: SLOs, alerting, saturation, request health, provider errors, cost and token guardrails.
- Logs: exact operational evidence, stack traces, and audit records.

The examples use Python because most LLM application code in this guide is Python.

