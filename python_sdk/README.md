# Python SDK

> Instrument your LLM application with the Langfuse Python SDK — from one-line setup to production-hardened async patterns.

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![langfuse](https://img.shields.io/badge/langfuse-3.x-FF6C37.svg)](https://pypi.org/project/langfuse/)
[![OpenTelemetry](https://img.shields.io/badge/Built_on-OTel-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_setup_and_config.md](01_setup_and_config.md) | Setup | Install, authenticate, initialize — all configuration options and env vars |
| [02_trace_data_model.md](02_trace_data_model.md) | Data Model | Trace, Span, Generation, Event — field reference and nesting rules |
| [03_instrumentation.md](03_instrumentation.md) | Instrumentation | Context managers, `@observe` decorator, manual span/generation creation |
| [04_advanced_patterns.md](04_advanced_patterns.md) | Advanced | Async, sampling, data masking, multi-threading, span filtering |
| [05_framework_integrations.md](05_framework_integrations.md) | Integrations | LangChain, OpenAI SDK wrapper, LiteLLM, Anthropic auto-instrumentation |

---

## Reading Order

1. **Setup & Config** — get credentials and a working client before anything else
2. **Trace Data Model** — understand what you're creating before you start creating it
3. **Instrumentation** — the three patterns for wrapping your code with observations
4. **Advanced Patterns** — production concerns: async, sampling, masking, threads
5. **Framework Integrations** — drop-in auto-instrumentation for popular LLM libraries

---

## Prerequisites

- [What is Langfuse](../foundations/01_what_is_langfuse.md) — understand the platform data model first
- Basic Python knowledge (context managers, decorators)
- A Langfuse account (cloud or self-hosted) for API keys
