# OpenTelemetry Integration

> Configure Langfuse as an OTLP backend to receive traces from any OpenTelemetry-instrumented application — language agnostic, standard protocol.

[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-OTLP-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![Langfuse](https://img.shields.io/badge/Langfuse-OTEL_backend-orange.svg)](https://langfuse.com/integrations/native/opentelemetry)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_otel_backend.md](01_otel_backend.md) | Backend Setup | OTLP endpoint URLs, Basic Auth, protocol support, Collector YAML config |
| [02_span_mapping.md](02_span_mapping.md) | Span Mapping | How OTel span attributes translate to Langfuse traces and observations |
| [03_attribute_propagation.md](03_attribute_propagation.md) | Propagation | Baggage API and BaggageSpanProcessor for distributing userId/sessionId |

---

## Reading Order

1. **OTel Backend** — configure the exporter endpoint and authentication first
2. **Span Mapping** — understand how Langfuse interprets the spans you send
3. **Attribute Propagation** — ensure user/session context flows to every child span

---

## Prerequisites

- [OpenTelemetry Primer](../foundations/02_opentelemetry_primer.md) — OTel concepts and pipeline
- [OTel GenAI Semantics](../foundations/03_otel_genai_semantics.md) — attribute naming conventions
- An existing OTel setup, or familiarity with `opentelemetry-sdk` Python package
