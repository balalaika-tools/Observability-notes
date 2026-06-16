# Langfuse Guide

Last verified against official Langfuse documentation on 2026-06-16.

Langfuse is an observability, evaluation, prompt management, and metrics platform for LLM applications. It is built on OpenTelemetry, but it adds LLM-specific concepts that general observability backends do not usually understand well: prompts, generations, model usage, cost, user feedback, scores, datasets, experiments, and review workflows.

This section assumes you already understand the OpenTelemetry basics in [../opentelemetry/README.md](../opentelemetry/README.md). Start here when you want to use Langfuse in production Python applications.

## Reading Path

| File | What it covers |
| --- | --- |
| [01_overview_data_model.md](01_overview_data_model.md) | Langfuse's trace, observation, generation, score, dataset, prompt, and metric concepts |
| [02_python_sdk_v4.md](02_python_sdk_v4.md) | Current Python SDK setup and instrumentation patterns |
| [03_otel_ingestion_and_mapping.md](03_otel_ingestion_and_mapping.md) | Sending raw OpenTelemetry spans to Langfuse and mapping attributes correctly |
| [04_production_observability.md](04_production_observability.md) | Production workflows, privacy, sampling, environments, releases, sessions, and operations |
| [05_evaluation_metrics_alerting.md](05_evaluation_metrics_alerting.md) | Scores, online/offline evaluation, Langfuse metrics, and alerting patterns |
| [06_framework_integrations.md](06_framework_integrations.md) | Current integration patterns for LangChain, OpenAI, LiteLLM, and OpenTelemetry-native libraries |

## Which Integration Should You Use?

| Situation | Recommended path |
| --- | --- |
| Python or JavaScript/TypeScript app and you can add Langfuse code | Use the Langfuse SDK. For Python, use SDK v4 APIs from `langfuse`: `get_client`, `observe`, and `propagate_attributes`. |
| Existing service already emits OpenTelemetry spans | Send OTLP/HTTP traces to Langfuse, either directly or through the Collector. |
| Polyglot microservices | Use OpenTelemetry everywhere; use Langfuse SDKs in Python/JS LLM services where they add value; route GenAI traces to Langfuse. |
| You need SLO alerts on request rate, errors, latency, queue depth, or saturation | Export OpenTelemetry metrics to a metrics backend and use Langfuse for trace, quality, and evaluation workflows. |
| You need quality, cost, latency, token, prompt-version, or user feedback analytics | Use Langfuse traces, scores, metrics, dashboards, and the metrics API. |

## Production Mental Model

Use Langfuse as the LLM observability and evaluation system, not as a replacement for your whole telemetry stack.

```text
Application
  |
  |-- Langfuse SDK or OTLP spans --> Langfuse
  |       traces, generations, prompts, scores, sessions, cost, quality
  |
  |-- OpenTelemetry metrics -------> Prometheus / managed metrics backend
  |       latency SLOs, errors, saturation, alerting
  |
  |-- OpenTelemetry or app logs ----> log backend
          incident forensics, audit trails, debugging
```

The strongest production setup uses all three. Langfuse explains what happened inside LLM and agent workflows. Metrics wake you up when the system is unhealthy. Logs provide exact operational evidence.

## Official References

- Langfuse SDK overview: <https://langfuse.com/docs/observability/sdk/overview>
- Langfuse instrumentation: <https://langfuse.com/docs/observability/sdk/instrumentation>
- Langfuse OpenTelemetry ingestion: <https://langfuse.com/integrations/native/opentelemetry>
- Langfuse metrics: <https://langfuse.com/docs/metrics/overview>
- Langfuse scores: <https://langfuse.com/docs/evaluation/scores/overview>
- Python SDK reference: <https://python.reference.langfuse.com/langfuse>
- Langfuse integrations: <https://langfuse.com/integrations>
