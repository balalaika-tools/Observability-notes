# OpenTelemetry and Langfuse Examples

Last checked against the Langfuse notes and official docs on 2026-06-18.

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

## Baseline Environment

All examples assume these values come from secret management or local development environment variables:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
export OPENAI_API_KEY="sk-proj-..."
export ENVIRONMENT="dev"
export RELEASE="local"
```

For direct OpenTelemetry export to Langfuse, use OTLP/HTTP as shown in [../langfuse/03_otel_ingestion_and_mapping.md](../langfuse/03_otel_ingestion_and_mapping.md). For application export to a Collector, use OTLP/gRPC or OTLP/HTTP according to your Collector configuration.

## Example Review Checklist

- Replace placeholder model names with models available in your provider account.
- Keep one primary capture path per model call to avoid duplicate Langfuse generations.
- Return or persist `langfuse_trace_id` with user-visible answers if feedback attaches later.
- Do not send raw secrets, payment data, or regulated content in trace input/output or baggage.
- Flush Langfuse in scripts, tests, workers, and serverless functions before process exit.
