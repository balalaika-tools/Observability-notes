# Langfuse + OpenTelemetry Notes

> End-to-end reference for tracing, monitoring, and evaluating LLM applications with Langfuse and OpenTelemetry — from first principles to production patterns.

[![Langfuse](https://img.shields.io/badge/Langfuse-0.100+-orange.svg?logo=data:image/svg+xml;base64,&logoColor=white)](https://langfuse.com)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-1.x-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![PyPI](https://img.shields.io/badge/langfuse-3.x-FF6C37.svg)](https://pypi.org/project/langfuse/)

---

## Structure

```
LangFuseNotes/
│
│ ── FOUNDATIONS ────────────────────────────────────────────────────────
├── foundations/
│   ├── 01_what_is_langfuse.md        Platform overview, data model, key concepts
│   ├── 02_opentelemetry_primer.md    OTel architecture: traces, spans, signals
│   └── 03_otel_genai_semantics.md    GenAI semantic conventions, what gets measured
│
│ ── PYTHON SDK ─────────────────────────────────────────────────────────
├── python_sdk/
│   ├── 01_setup_and_config.md        Installation, auth, env vars, client init
│   ├── 02_trace_data_model.md        Trace → Span → Generation → Event hierarchy
│   ├── 03_instrumentation.md         Context managers, @observe decorator, manual
│   ├── 04_advanced_patterns.md       Async, sampling, masking, multi-threading
│   └── 05_framework_integrations.md  LangChain, OpenAI SDK, LiteLLM, Anthropic
│
│ ── OPENTELEMETRY INTEGRATION ──────────────────────────────────────────
├── otel_integration/
│   ├── 01_otel_backend.md            Langfuse as OTLP backend, endpoint setup
│   ├── 02_span_mapping.md            How OTel spans map to Langfuse observations
│   └── 03_attribute_propagation.md   Baggage, BaggageSpanProcessor, trace attrs
│
│ ── EVALUATION ─────────────────────────────────────────────────────────
└── evaluation/
    ├── 01_scoring_system.md          Score types, SDK scoring, idempotency
    ├── 02_llm_as_judge.md            Automated LLM-based evaluation setup
    ├── 03_datasets_experiments.md    Dataset items, experiment runs, comparisons
    └── 04_human_annotation.md        Annotation queues, human review workflows
```

---

## Contents

### Foundations — [full index](foundations/README.md)

[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-1.x-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![Langfuse](https://img.shields.io/badge/Langfuse-concepts-orange.svg)](https://langfuse.com/docs)

| Guide | Description |
|-------|-------------|
| [What is Langfuse](foundations/01_what_is_langfuse.md) | Platform purpose, core data model, how traces flow through the system |
| [OpenTelemetry Primer](foundations/02_opentelemetry_primer.md) | OTel architecture — traces, spans, metrics, logs, the collector pipeline |
| [OTel GenAI Semantics](foundations/03_otel_genai_semantics.md) | Standard attribute schema for LLM calls: tokens, model, prompts, cost |

---

### Python SDK — [full index](python_sdk/README.md)

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![langfuse](https://img.shields.io/badge/langfuse-3.x-FF6C37.svg)](https://pypi.org/project/langfuse/)

| Guide | Description |
|-------|-------------|
| [Setup & Config](python_sdk/01_setup_and_config.md) | Install, authenticate, initialize the client — env vars and constructor options |
| [Trace Data Model](python_sdk/02_trace_data_model.md) | Trace, Span, Generation, Event — the four observation types and their fields |
| [Instrumentation](python_sdk/03_instrumentation.md) | Three ways to instrument: context managers, `@observe`, manual span creation |
| [Advanced Patterns](python_sdk/04_advanced_patterns.md) | Async workloads, sampling, data masking, multi-threading context propagation |
| [Framework Integrations](python_sdk/05_framework_integrations.md) | LangChain callbacks, OpenAI wrapper, LiteLLM, Anthropic auto-instrumentation |

---

### OpenTelemetry Integration — [full index](otel_integration/README.md)

[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-OTLP-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![Langfuse](https://img.shields.io/badge/Langfuse-OTEL_backend-orange.svg)](https://langfuse.com/integrations/native/opentelemetry)

| Guide | Description |
|-------|-------------|
| [OTel Backend](otel_integration/01_otel_backend.md) | Configuring Langfuse as an OTLP backend — endpoints, auth, protocol support |
| [Span Mapping](otel_integration/02_span_mapping.md) | How OTel span attributes translate to Langfuse observations and trace fields |
| [Attribute Propagation](otel_integration/03_attribute_propagation.md) | Baggage API, BaggageSpanProcessor, distributing userId/sessionId across spans |

---

### Evaluation — [full index](evaluation/README.md)

[![Langfuse](https://img.shields.io/badge/Langfuse-evals-orange.svg)](https://langfuse.com/docs/evaluation/overview)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)

| Guide | Description |
|-------|-------------|
| [Scoring System](evaluation/01_scoring_system.md) | Numeric/categorical/boolean scores, attaching to traces and observations |
| [LLM-as-Judge](evaluation/02_llm_as_judge.md) | Automated evaluation with LLM judges — setup, criteria, production patterns |
| [Datasets & Experiments](evaluation/03_datasets_experiments.md) | Dataset management, running experiments, comparing versions |
| [Human Annotation](evaluation/04_human_annotation.md) | Annotation queues, structured human review, team collaboration workflows |

---

## Reading Order

> [!TIP]
> Not sure where to start? Pick the path that matches your goal.

### Path 1: New to Langfuse — start here

1. [What is Langfuse](foundations/01_what_is_langfuse.md) — understand the platform and data model
2. [Setup & Config](python_sdk/01_setup_and_config.md) — get credentials and install the SDK
3. [Trace Data Model](python_sdk/02_trace_data_model.md) — learn the Trace → Generation hierarchy
4. [Instrumentation](python_sdk/03_instrumentation.md) — instrument your first LLM call

### Path 2: OTel engineer integrating with Langfuse

1. [OpenTelemetry Primer](foundations/02_opentelemetry_primer.md) — if you need an OTel refresher
2. [OTel GenAI Semantics](foundations/03_otel_genai_semantics.md) — the standard attribute schema
3. [OTel Backend](otel_integration/01_otel_backend.md) — point your OTLP exporter at Langfuse
4. [Span Mapping](otel_integration/02_span_mapping.md) — understand how spans become observations
5. [Attribute Propagation](otel_integration/03_attribute_propagation.md) — propagate userId/sessionId

### Path 3: Building an evaluation pipeline

1. [What is Langfuse](foundations/01_what_is_langfuse.md) — understand what scores attach to
2. [Scoring System](evaluation/01_scoring_system.md) — the universal score data object
3. [LLM-as-Judge](evaluation/02_llm_as_judge.md) — automate quality checks at scale
4. [Datasets & Experiments](evaluation/03_datasets_experiments.md) — run structured benchmarks

### Path 4: Production hardening

1. [Advanced Patterns](python_sdk/04_advanced_patterns.md) — sampling, masking, async, threading
2. [Attribute Propagation](otel_integration/03_attribute_propagation.md) — reliable context flow
3. [Framework Integrations](python_sdk/05_framework_integrations.md) — auto-instrument your stack

---

*Last updated: April 2026*
