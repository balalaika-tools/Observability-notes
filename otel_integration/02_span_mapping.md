# OTel Span → Langfuse Data Model: Field Mapping Reference

> **Who this is for**: Engineers debugging why their OTel spans look wrong in Langfuse (missing generations, userId not appearing, metadata not filterable), or anyone building custom instrumentation that targets the Langfuse OTLP backend specifically.

---

## 1. Overview

---

When Langfuse receives an **OTLP trace**, it does not store spans as-is. Instead, it converts each span into its own internal data model with four distinct object types: **Trace**, **Span**, **Generation**, and **Event**.

The conversion is entirely attribute-driven. Langfuse inspects each span's attributes at ingest time and decides what to create:

```
OTel OTLP Export
        │
        ▼
┌───────────────────┐
│  Langfuse Ingest  │
│  (OTLP endpoint)  │
└────────┬──────────┘
         │ per-span processing
         ▼
┌─────────────────────────────────────────────┐
│           Span Classification               │
│                                             │
│  has gen_ai.request.model? ──────► Generation│
│  langfuse.observation.type=event? ──► Event  │
│  no parent span? ──────────────────► Trace  │
│  everything else ──────────────────► Span   │
└─────────────────────────────────────────────┘
```

> **Key insight**: The **presence of a model attribute** is what promotes a span to a `generation`. If your LLM call shows up as a generic span in Langfuse, a missing `gen_ai.request.model` attribute is almost always the reason.

**Cross-references**:
- Previous: [Part 1: OTel Backend Setup](01_otel_backend.md)
- GenAI semantic conventions: [../foundations/03_otel_genai_semantics.md](../foundations/03_otel_genai_semantics.md)
- Langfuse data model: [../python_sdk/02_trace_data_model.md](../python_sdk/02_trace_data_model.md)

---

## 2. Span-to-Observation Type Mapping

---

Langfuse inspects each incoming span and assigns it a type using this decision table:

| OTel Span Characteristic | Langfuse Observation Type |
|--------------------------|---------------------------|
| Has `gen_ai.request.model`, `llm.model_name`, or `model` attribute | `generation` |
| Has `langfuse.observation.type = "event"` | `event` |
| Root span (no `parentSpanId`) | Creates a `trace` container |
| Everything else | `span` |

⚠️ The `langfuse.observation.type` attribute can **force** a specific type, overriding auto-detection. This means you can mark a span as `"event"` even if it has model attributes — `langfuse.observation.type` wins.

```
Span Classification Flow:

  Incoming OTel Span
         │
         ▼
  langfuse.observation.type set?
    │                  │
   yes                 no
    │                  │
    ▼                  ▼
  Use that type    has gen_ai.request.model
                   OR llm.model_name
                   OR model ?
                     │        │
                    yes        no
                     │        │
                     ▼        ▼
                generation   has parentSpanId?
                              │          │
                             yes          no
                              │          │
                              ▼          ▼
                            span       trace
```

💡 **Tip**: You can omit `langfuse.observation.type` entirely for LLM calls — just set `gen_ai.request.model` and Langfuse will auto-classify to `generation`. Reserve the explicit attribute for edge cases like forcing `event` type.

---

## 3. Trace-Level Attributes

---

These attributes can appear on **any span** in a trace. Langfuse extracts them and applies them to the root **Trace** object, not to the individual observation.

| Langfuse Field | Primary OTel Attribute | Fallback |
|----------------|------------------------|----------|
| `name` | `langfuse.trace.name` | root span name |
| `userId` | `langfuse.user.id` | `user.id` |
| `sessionId` | `langfuse.session.id` | `session.id` |
| `tags` | `langfuse.trace.tags` (string array) | — |
| `release` | `langfuse.release` | — |
| `public` | `langfuse.trace.public` (boolean) | — |
| `input` | `langfuse.trace.input` | — |
| `output` | `langfuse.trace.output` | — |
| `metadata.*` | `langfuse.trace.metadata.{key}` | — |

> **Key insight**: Trace-level attributes are "promoted" from whichever span carries them. You can attach `langfuse.user.id` to a deep child span and it will still appear on the root Trace. This is useful when the context (e.g. user identity) is only known mid-trace.

⚠️ If `langfuse.user.id` and `user.id` are **both** present on the same span, `langfuse.user.id` wins. Always prefer the `langfuse.*` namespace when targeting Langfuse specifically.

---

## 4. Observation-Level Attributes

---

These attributes apply to the **individual span** being converted. They populate the fields of the resulting `span`, `generation`, or `event` observation.

| Langfuse Field | Primary OTel Attribute | Fallback(s) |
|----------------|------------------------|-------------|
| `name` | span name | — |
| `type` | `langfuse.observation.type` | auto-detected (see §2) |
| `level` | `langfuse.observation.level` | `span.status.code` |
| `statusMessage` | `langfuse.observation.status_message` | — |
| `model` | `gen_ai.request.model` | `llm.model_name`, `model` |
| `modelParameters.temperature` | `gen_ai.request.temperature` | `llm.invocation_parameters.temperature` |
| `modelParameters.maxTokens` | `gen_ai.request.max_tokens` | `llm.invocation_parameters.max_tokens` |
| `usage.input` | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` |
| `usage.output` | `gen_ai.usage.output_tokens` | `llm.token_count.completion` |
| `cost` | `gen_ai.usage.cost` | calculated from `gen_ai.usage.*` |
| `input` | `gen_ai.prompt` (span event) | `input.value` |
| `output` | `gen_ai.completion` (span event) | `output.value` |
| `prompt.name` | `langfuse.observation.prompt.name` | — |
| `prompt.version` | `langfuse.observation.prompt.version` | — |
| `metadata` | `langfuse.observation.metadata.*` | — |

⚠️ **Prompts and completions live on span events, not span attributes.** Langfuse reads `input`/`output` from `gen_ai.prompt` and `gen_ai.completion` **events** added via `span.add_event(...)`. Setting them as plain attributes will not populate the generation's input/output fields.

💡 **`level` mapping**: When `langfuse.observation.level` is absent, Langfuse maps `span.status.code` as follows:
- `STATUS_CODE_OK` → `DEFAULT`
- `STATUS_CODE_ERROR` → `ERROR`
- `STATUS_CODE_UNSET` → `DEFAULT`

---

## 5. Attribute Priority Rule

---

> **Rule**: `langfuse.*` namespace attributes **always take priority** over generic OTel GenAI conventions. Use `langfuse.*` attributes when you need precise control over Langfuse-specific fields.

```
Priority order (highest → lowest):

  langfuse.* namespace
        │
        ▼
  gen_ai.* semantic conventions  (OpenTelemetry GenAI)
        │
        ▼
  llm.* attributes               (OpenInference / LlamaIndex convention)
        │
        ▼
  generic attributes             (e.g., bare "model", "user.id")
```

This layered approach means Langfuse works out of the box with frameworks that emit standard OTel GenAI spans (LangChain, LlamaIndex, OpenAI instrumentation), while still letting you override individual fields with `langfuse.*` attributes when the framework's output doesn't match what you want.

---

## 6. Metadata Organization

---

Langfuse organizes metadata into a **three-level hierarchy** based on where each attribute originates:

```
OTel Span Attributes
├── langfuse.trace.metadata.customer_id = "cust-123"
│   → metadata.customer_id
│     (first-class field, filterable and searchable in UI)
│
├── gen_ai.request.temperature = 0.7
│   → metadata.attributes.gen_ai.request.temperature
│     (catch-all bucket for unrecognized OTel attributes)
│
└── resource.service.name = "my-app"
    → metadata.resourceAttributes.service.name
      (resource-level attributes from the OTLP ResourceSpans envelope)
```

| Attribute Origin | Destination in Langfuse | Filterable in UI? |
|-----------------|-------------------------|-------------------|
| `langfuse.trace.metadata.{key}` | `metadata.{key}` | ✅ Yes |
| `langfuse.observation.metadata.{key}` | observation `metadata.{key}` | ✅ Yes |
| Unrecognized OTel span attributes | `metadata.attributes.*` | ❌ No |
| OTel resource attributes | `metadata.resourceAttributes.*` | ❌ No |

> **Key insight**: Only attributes in the `langfuse.trace.metadata.*` and `langfuse.observation.metadata.*` namespaces become first-class metadata fields you can filter on in the Langfuse UI. Everything else ends up nested under `attributes` or `resourceAttributes` — visible, but not filterable.

💡 **Tip**: When you need a custom field to appear as a filterable dimension in Langfuse (e.g., `customer_id`, `environment`, `experiment_id`), always use the `langfuse.trace.metadata.{key}` prefix rather than a bare or `gen_ai.*` attribute.

---

## 7. Practical Example: End-to-End Mapping

---

The following shows exactly what you send at the OTel layer and what Langfuse creates from it.

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
# (assume exporter configured to Langfuse OTLP endpoint — see 01_otel_backend.md)

tracer = trace.get_tracer("my-app")

# Sending this OTel span:
with tracer.start_as_current_span("llm-call") as span:
    # Model identification → promotes this span to a Generation
    span.set_attribute("gen_ai.system", "openai")
    span.set_attribute("gen_ai.request.model", "gpt-4o")

    # Model parameters → modelParameters fields
    span.set_attribute("gen_ai.request.temperature", 0.7)
    span.set_attribute("gen_ai.request.max_tokens", 512)

    # Token usage → usage fields (auto-calculates cost if pricing known)
    span.set_attribute("gen_ai.usage.input_tokens", 150)
    span.set_attribute("gen_ai.usage.output_tokens", 200)

    # Trace-level context → appears on root Trace object
    span.set_attribute("langfuse.user.id", "user-abc")
    span.set_attribute("langfuse.session.id", "sess-xyz")
    span.set_attribute("langfuse.trace.metadata.environment", "production")

    # Prompt/completion → must be span EVENTS, not attributes
    span.add_event("gen_ai.prompt", {"content": "What is RAG?"})
    span.add_event("gen_ai.completion", {"content": "RAG is retrieval-augmented generation..."})

# ─── What Langfuse creates ────────────────────────────────────────────────────
#
# Trace
#   name        = "llm-call"          (from root span name)
#   userId      = "user-abc"          (from langfuse.user.id)
#   sessionId   = "sess-xyz"          (from langfuse.session.id)
#   metadata    = { environment: "production" }
#
# Generation  (child of Trace, promoted because gen_ai.request.model is set)
#   name        = "llm-call"
#   model       = "gpt-4o"
#   modelParameters = { temperature: 0.7, maxTokens: 512 }
#   usage       = { input: 150, output: 200 }
#   input       = "What is RAG?"
#   output      = "RAG is retrieval-augmented generation..."
```

---

## 8. Common Mapping Issues

---

Use this checklist when something doesn't look right in the Langfuse UI.

**Observation type issues**

❌ **Span not becoming a `generation`**
- Cause: `gen_ai.request.model` (and all fallbacks) are absent
- Fix: Add `span.set_attribute("gen_ai.request.model", "gpt-4o")`

❌ **Span stuck as `generation` when you want `span`**
- Cause: A model attribute is being set (perhaps by an auto-instrumentation library)
- Fix: Set `span.set_attribute("langfuse.observation.type", "span")` to override

**Trace context issues**

❌ **`userId` not appearing on the Trace**
- Cause: Using `user.id` (generic) which is being shadowed or not recognized
- Fix: Use `langfuse.user.id` explicitly — it has highest priority

❌ **`sessionId` missing despite being set**
- Cause: Same issue — `session.id` (generic) vs `langfuse.session.id` (preferred)
- Fix: Switch to `span.set_attribute("langfuse.session.id", "...")`

**Metadata issues**

❌ **Metadata not filterable in the UI**
- Cause: Using standard OTel attributes (e.g., `service.name`, `gen_ai.system`)
- Fix: Use `langfuse.trace.metadata.{key}` prefix for fields you need to filter on

**Input/Output issues**

⚠️ **Prompts/completions not showing up in generation detail**
- Cause: Set as span **attributes** (`span.set_attribute("gen_ai.prompt", ...)`)
- Fix: Must be span **events** — use `span.add_event("gen_ai.prompt", {"content": "..."})`

```python
# ❌ Wrong — attribute, will NOT populate generation input
span.set_attribute("gen_ai.prompt", "What is RAG?")

# ✅ Correct — event, WILL populate generation input
span.add_event("gen_ai.prompt", {"content": "What is RAG?"})
```

⚠️ **Cost not calculating**
- Cause: `gen_ai.usage.cost` not set and model pricing not in Langfuse's registry
- Fix: Either set `gen_ai.usage.cost` explicitly, or register the model under Settings → Models in the Langfuse UI

---

**Next**: [Part 3: Attribute Propagation](03_attribute_propagation.md)
