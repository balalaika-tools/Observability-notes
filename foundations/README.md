# Foundations

> Core concepts behind Langfuse and OpenTelemetry — what they are, how they work, and why they fit together for LLM observability.

[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-1.x-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![Langfuse](https://img.shields.io/badge/Langfuse-concepts-orange.svg)](https://langfuse.com/docs)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_what_is_langfuse.md](01_what_is_langfuse.md) | Platform Overview | Langfuse's purpose, data model, core features, and how it differs from general APM tools |
| [02_opentelemetry_primer.md](02_opentelemetry_primer.md) | OTel Architecture | Traces, spans, metrics, logs — the three pillars and how the collector pipeline works |
| [03_otel_genai_semantics.md](03_otel_genai_semantics.md) | GenAI Conventions | Standard attribute schema for LLM calls, token tracking, cost, model metadata |

---

## Reading Order

1. **What is Langfuse** — establishes the platform's data model and vocabulary before touching any SDK
2. **OpenTelemetry Primer** — explains the underlying observability standard the Langfuse SDK is built on
3. **OTel GenAI Semantics** — the specific attribute conventions that make LLM telemetry standardized and interoperable

---

## Prerequisites

- Familiarity with distributed systems concepts (requests, latency, logs) is helpful but not required
- No prior Langfuse or OpenTelemetry experience needed — these files start from scratch
