# OpenTelemetry Guide

OpenTelemetry is the vendor-neutral instrumentation layer for modern distributed systems. It gives you a standard way to create traces, metrics, logs, context propagation, and telemetry pipelines without tying application code to a single backend.

This section is a beginner-to-production guide. It starts with the mental model, then moves into Python examples, multi-service propagation, metrics, production collector patterns, alerting, and the GenAI conventions used by LLM and agent systems.

## Reading Path

| File | Purpose |
| --- | --- |
| [01_concepts.md](01_concepts.md) | What OpenTelemetry is, why it matters, and the core vocabulary. |
| [02_python_instrumentation.md](02_python_instrumentation.md) | Practical Python setup for traces, metrics, logs, exporters, and auto-instrumentation. |
| [03_production_architecture.md](03_production_architecture.md) | Collector topologies, deployment patterns, sampling, security, and operations. |
| [04_multi_service_examples.md](04_multi_service_examples.md) | Gateway-to-service tracing, W3C Trace Context, baggage, and signal correlation. |
| [05_custom_metrics_alerting.md](05_custom_metrics_alerting.md) | Custom metric design, Prometheus-style export, SLOs, alerts, and runbooks. |
| [06_genai_and_llm_observability.md](06_genai_and_llm_observability.md) | Current GenAI semantic conventions and how they apply to LLM and agent systems. |

## When To Use OpenTelemetry Directly

Use raw OpenTelemetry instrumentation when:

- you have multiple services, languages, or telemetry backends;
- you already run an OpenTelemetry Collector;
- you need infrastructure and application metrics alongside LLM traces;
- you want traces to go to Langfuse and metrics/logs to observability backends such as Prometheus, Grafana, Datadog, Honeycomb, or Elastic;
- you need vendor-neutral instrumentation in libraries or shared internal frameworks.

Use the Langfuse SDK when:

- your main goal is LLM observability, prompt/version tracking, and evaluation;
- you are writing Python or JS/TS application code and want higher-level helpers for generations, scores, and attribute propagation;
- you still want OpenTelemetry compatibility but do not want to wire OTLP exporters manually.

In production LLM systems, the strongest pattern is usually both:

```text
Application code
  |-- Langfuse SDK or GenAI instrumentation for LLM traces
  |-- OpenTelemetry SDK/instrumentations for HTTP, DB, queues, metrics, logs
       |
       v
OpenTelemetry Collector
  |-- traces -> Langfuse and/or APM backend
  |-- metrics -> Prometheus-compatible metrics backend
  |-- logs -> log backend
```

## Official References Used

This guide was checked on June 16, 2026 against:

- [OpenTelemetry overview](https://opentelemetry.io/docs/what-is-opentelemetry/)
- [OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/)
- [OpenTelemetry metrics](https://opentelemetry.io/docs/concepts/signals/metrics/)
- [OpenTelemetry logs](https://opentelemetry.io/docs/concepts/signals/logs/)
- [OpenTelemetry context propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
- [OpenTelemetry baggage](https://opentelemetry.io/docs/concepts/signals/baggage/)
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
- [OpenTelemetry Collector configuration](https://opentelemetry.io/docs/collector/configuration/)
- [OpenTelemetry sampling](https://opentelemetry.io/docs/concepts/sampling/)
- [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai)

