# OpenTelemetry and Langfuse Guide

Last verified against official OpenTelemetry and Langfuse documentation on 2026-06-16.

This repo is a production-focused guide to OpenTelemetry and Langfuse for engineers building modern distributed systems, LLM applications, RAG pipelines, and agentic systems.

It has three goals:

1. Teach OpenTelemetry from fundamentals to production use.
2. Teach Langfuse as a practical LLM observability, evaluation, and metrics platform.
3. Show how OpenTelemetry and Langfuse work together in real Python systems.

## 🧭 Start Here

| Path | Use it when |
| --- | --- |
| [opentelemetry/README.md](opentelemetry/README.md) | You want to deeply understand OpenTelemetry and production observability architecture. |
| [opentelemetry/deployment/README.md](opentelemetry/deployment/README.md) | You need to deploy, discover, scale, secure, or upgrade OpenTelemetry Collectors. |
| [langfuse/README.md](langfuse/README.md) | You want to instrument, evaluate, and operate LLM applications with Langfuse. |
| [examples/README.md](examples/README.md) | You want concrete Python patterns for RAG, agents, multi-service propagation, metrics, and alerts. |

## 📁 Structure

```text
LangFuseNotes/
|-- opentelemetry/
|   |-- 01_concepts.md
|   |-- 02_python_instrumentation.md
|   |-- 03_production_architecture.md
|   |-- 04_multi_service_examples.md
|   |-- 05_custom_metrics_alerting.md
|   |-- 06_genai_and_llm_observability.md
|   `-- deployment/
|       |-- 01_deployment_decisions.md
|       |-- 02_docker_compose.md
|       |-- 03_kubernetes_installation.md
|       |-- 04_service_discovery_and_clients.md
|       |-- 05_scaling_and_resilience.md
|       `-- 06_security_operations_and_upgrades.md
|
|-- langfuse/
|   |-- 01_overview_data_model.md
|   |-- 02_python_sdk_v4.md
|   |-- 03_otel_ingestion_and_mapping.md
|   |-- 04_production_observability.md
|   |-- 05_evaluation_metrics_alerting.md
|   `-- 06_framework_integrations.md
|
|-- examples/
|   |-- 01_rag_with_otel_and_langfuse.md
|   |-- 02_multi_service_agent.md
|   `-- 03_collector_prometheus_langfuse.md
```

## 📖 Recommended Reading Order

New to both topics:

1. [OpenTelemetry concepts](opentelemetry/01_concepts.md)
2. [Python instrumentation](opentelemetry/02_python_instrumentation.md)
3. [Production architecture](opentelemetry/03_production_architecture.md)
4. [Langfuse overview and data model](langfuse/01_overview_data_model.md)
5. [Langfuse Python SDK v4](langfuse/02_python_sdk_v4.md)
6. [Framework integrations](langfuse/06_framework_integrations.md)
7. [RAG example](examples/01_rag_with_otel_and_langfuse.md)

Already an OpenTelemetry engineer:

1. [Production architecture](opentelemetry/03_production_architecture.md)
2. [GenAI and LLM observability](opentelemetry/06_genai_and_llm_observability.md)
3. [Langfuse OTLP ingestion and mapping](langfuse/03_otel_ingestion_and_mapping.md)
4. [Collector, Prometheus, Langfuse, and alerts](examples/03_collector_prometheus_langfuse.md)

Building production LLM or agent systems:

1. [Langfuse production observability](langfuse/04_production_observability.md)
2. [Evaluation, metrics, and alerting](langfuse/05_evaluation_metrics_alerting.md)
3. [Framework integrations](langfuse/06_framework_integrations.md)
4. [Multi-service OpenTelemetry examples](opentelemetry/04_multi_service_examples.md)
5. [Multi-service agent example](examples/02_multi_service_agent.md)

Deploying OpenTelemetry Collectors:

1. [Deployment decisions](opentelemetry/deployment/01_deployment_decisions.md)
2. [Docker Compose baseline](opentelemetry/deployment/02_docker_compose.md)
3. [Kubernetes installation](opentelemetry/deployment/03_kubernetes_installation.md)
4. [Service discovery and client configuration](opentelemetry/deployment/04_service_discovery_and_clients.md)
5. [Scaling and resilience](opentelemetry/deployment/05_scaling_and_resilience.md)
6. [Security, operations, and upgrades](opentelemetry/deployment/06_security_operations_and_upgrades.md)

> 💡 Scan **Current Guidance Highlights** before starting any path — it flags API changes and naming conventions that affect every section.

## ✅ Current Guidance Highlights

- Langfuse Python SDK examples use SDK v4 APIs from `langfuse`: `get_client`, `observe`, and `propagate_attributes`.
- Use `LANGFUSE_BASE_URL`, not legacy `LANGFUSE_HOST`, in new code.
- Langfuse OTLP ingestion uses OTLP/HTTP and Basic Auth; OTLP/gRPC is not supported by Langfuse ingestion.
- Use current GenAI semantic convention names such as `gen_ai.provider.name`, `gen_ai.operation.name`, and `gen_ai.usage.*`.
- Send operational metrics to a metrics backend for alerts. Use Langfuse for LLM traces, generations, scores, datasets, quality analytics, cost, and workflow investigation.
- Treat baggage as an allowlisted propagation mechanism, not as a place for secrets or arbitrary user data.
- Pin the Collector image and chart independently, validate the exact distribution's component set, and use trace-ID-aware routing before scaling a tail-sampling tier.

## 🔗 Official References

- OpenTelemetry docs: <https://opentelemetry.io/docs/>
- OpenTelemetry Python docs: <https://opentelemetry.io/docs/languages/python/>
- OpenTelemetry Collector docs: <https://opentelemetry.io/docs/collector/>
- OpenTelemetry GenAI semantic conventions: <https://github.com/open-telemetry/semantic-conventions-genai>
- Langfuse docs: <https://langfuse.com/docs>
- Langfuse SDK overview: <https://langfuse.com/docs/observability/sdk/overview>
- Langfuse OpenTelemetry ingestion: <https://langfuse.com/integrations/native/opentelemetry>
- Langfuse integrations: <https://langfuse.com/integrations>
- Langfuse Python reference: <https://python.reference.langfuse.com/langfuse>
