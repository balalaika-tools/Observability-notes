# OTel GenAI Semantic Conventions — Attribute Schema for LLM Observability

> **Who this is for**: Engineers already familiar with OpenTelemetry traces and spans (see [02_opentelemetry_primer.md](02_opentelemetry_primer.md)) who want the definitive reference on which attributes go on LLM spans, how Langfuse reads them, and how to instrument correctly from the start.

---

## 1. What Semantic Conventions Are

**Semantic conventions** are a standardized vocabulary for span attributes — a shared contract that says "when you record an LLM call, name the model attribute *this*, name the token count attribute *that*." They are part of the OpenTelemetry specification, maintained by working groups, and versioned alongside the OTel SDK.

The **GenAI semantic conventions** (short for Generative AI) are the specific subset covering large language models, embedding models, image generators, and AI agents. The working group was formally chartered in 2023 and reached experimental stability in 2024. The core LLM attributes are now widely implemented.

> **Key insight**: Semantic conventions are not a runtime feature — they are a naming standard. Nothing breaks if you use a different name. But nothing interoperates either. Langfuse, Datadog, Grafana, and Honeycomb can all parse the same span *because* the attribute names are agreed upon in advance.

The conventions live in the OTel specification at `opentelemetry.io/docs/specs/semconv/gen-ai/`. The short prefix for all LLM attributes is `gen_ai.*`.

---

## 2. The Problem They Solve

Before the GenAI conventions stabilized, every instrumentation library invented its own attribute names:

| Library / Framework | Token attribute | Model attribute |
|---------------------|----------------|-----------------|
| Early LangChain     | `llm.token_count` | `llm.model` |
| OpenLLMetry         | `llm.token_count.prompt` / `llm.token_count.completion` | `llm.model_name` |
| Custom in-house     | `usage.total_tokens` | `model_id` |
| Raw OpenAI call     | `token_count` | `model` |
| OTel GenAI spec     | `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` | `gen_ai.request.model` |

The consequence: dashboards built for one library broke with another. Cost calculations had to be re-implemented per source. Backends like Langfuse had to maintain a mapping table for every framework that ever shipped spans.

> **Key insight**: Standardized attribute names make LLM observability *measurable, comparable, and interoperable*. A span from a LangChain call and a span from a raw `httpx` call against the OpenAI API should look identical to the backend — if both follow the convention.

Langfuse accepts all the legacy naming schemes (see Section 6) precisely because the ecosystem didn't converge overnight. But new instrumentation should use `gen_ai.*` exclusively.

---

## 3. Core GenAI Span Attributes

This table is the primary reference. Keep it open when writing instrumentation code.

### 3a. Request Attributes

Set these *before* the LLM call completes — they describe what was requested.

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `gen_ai.system` | string | `openai`, `anthropic`, `google`, `mistral`, `cohere` | The LLM provider. Use the lowercase canonical name. |
| `gen_ai.request.model` | string | `gpt-4o`, `claude-3-5-sonnet-20241022`, `gemini-1.5-pro` | The model name/version *as requested*. May differ from what responded. |
| `gen_ai.request.temperature` | float | `0.7` | Sampling temperature. |
| `gen_ai.request.top_p` | float | `0.95` | Nucleus sampling parameter. |
| `gen_ai.request.max_tokens` | int | `2048` | Maximum tokens requested in the response. |
| `gen_ai.request.stop_sequences` | string[] | `["\\n\\n", "###"]` | List of stop sequences passed to the model. |
| `gen_ai.request.frequency_penalty` | float | `0.2` | Frequency penalty (OpenAI-style). |
| `gen_ai.request.presence_penalty` | float | `0.1` | Presence penalty (OpenAI-style). |

### 3b. Response / Usage Attributes

Set these *after* the LLM call completes, once the response is available.

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `gen_ai.response.model` | string | `gpt-4o-2024-08-06` | The *actual* model that responded. Often includes a date suffix the request omitted. |
| `gen_ai.response.finish_reasons` | string[] | `["stop"]`, `["length"]`, `["tool_calls"]` | Why the model stopped generating. |
| `gen_ai.response.id` | string | `chatcmpl-abc123` | Provider-assigned response ID for correlation. |
| `gen_ai.usage.input_tokens` | int | `512` | Prompt token count. |
| `gen_ai.usage.output_tokens` | int | `128` | Completion token count. |
| `gen_ai.usage.cost` | float | `0.00192` | Monetary cost in USD. Not in the official spec yet but widely adopted; Langfuse reads it directly. |

### 3c. Prompt and Completion — Span Events (not Attributes)

The actual prompt messages and completion text are **not span attributes**. They are captured as **span events**. This is a deliberate design decision (see Section 4).

| Event name | Key fields | Description |
|------------|-----------|-------------|
| `gen_ai.prompt` | `gen_ai.prompt` (JSON-encoded messages array) | The full list of input messages sent to the model. |
| `gen_ai.completion` | `gen_ai.completion` (JSON-encoded choices array) | The model's output messages. |

Langfuse reads both events and falls back to span attributes `input` / `output` when events are absent.

---

## 4. Why Prompts Go on Events, Not Attributes

OTel span attributes are stored in the span header and indexed for filtering and querying. They have a hard size limit — typically **128 KB total** across all attributes on a single span, enforced by the collector and most backends.

A production system prompt plus a multi-turn conversation history can easily exceed that limit. Putting prompt content in attributes would cause spans to be dropped, truncated, or rejected by the collector.

Span **events** are time-stamped annotations attached to a span. They are stored separately from the span header, in the event log, and are not subject to the same size indexing pressure. This makes them the right home for large payload content.

```
Span
├── Attributes (indexed, size-limited)
│   ├── gen_ai.system = "openai"
│   ├── gen_ai.request.model = "gpt-4o"
│   ├── gen_ai.usage.input_tokens = 512
│   └── gen_ai.usage.output_tokens = 128
│
└── Events (time-stamped, payload-friendly)
    ├── [t=0ms]  name="gen_ai.prompt"
    │            gen_ai.prompt = '[{"role":"system","content":"..."},...]'
    │
    └── [t=847ms] name="gen_ai.completion"
                  gen_ai.completion = '[{"role":"assistant","content":"..."}]'
```

> **Key insight**: Attributes are for filtering and aggregation. Events are for payload content. The GenAI working group made this split deliberately — it keeps spans fast to query while preserving full prompt/completion capture.

⚠️ Some older instrumentation libraries still put prompts directly in span attributes as `gen_ai.prompt` / `gen_ai.completion`. Langfuse handles both patterns, but the event-based approach is correct for any new code you write.

---

## 5. Langfuse's Own `langfuse.*` Namespace

Langfuse defines a set of **first-party attributes** under the `langfuse.*` prefix. These take priority over any generic OTel attribute when Langfuse ingests a span. Use them to pass Langfuse-specific metadata that has no equivalent in the GenAI spec.

| Attribute | Langfuse field | Type | Notes |
|-----------|----------------|------|-------|
| `langfuse.user.id` | `userId` | string | Identifies the end user for user-level analytics. |
| `langfuse.session.id` | `sessionId` | string | Groups traces into a conversation session. |
| `langfuse.trace.name` | trace name | string | Overrides the span name when naming the root trace. |
| `langfuse.trace.tags` | tags | string[] | Arbitrary tags for filtering in the UI. |
| `langfuse.release` | release | string | Application version string (e.g., `v2.3.1`, `sha-abc123`). Used for version-over-version comparison. |
| `langfuse.observation.type` | observation type | string | Force a span to be interpreted as `span`, `generation`, or `event`. Otherwise Langfuse infers the type. |
| `langfuse.observation.prompt.name` | linked prompt | string | Links the observation to a **managed prompt** in Langfuse by name. |
| `langfuse.observation.prompt.version` | linked prompt version | int | Version of the managed prompt, used alongside `prompt.name`. |
| `langfuse.trace.metadata.{key}` | metadata (top-level) | any | Sets a named key directly in trace metadata, queryable as a first-class field. |

> **Key insight**: `langfuse.*` attributes are read from the **root span** of a trace to populate trace-level fields. Setting `langfuse.user.id` on a child span will not propagate it to the trace. Put these on the root span, or use the Baggage API to propagate them (see [../otel_integration/03_attribute_propagation.md](../otel_integration/03_attribute_propagation.md)).

---

## 6. Alternative Attribute Sets — OpenLLMetry and Legacy Schemas

Langfuse's OTLP ingestion layer checks multiple attribute keys for each logical field. This means spans from OpenLLMetry-instrumented code work without any translation layer.

| Langfuse field | Primary (GenAI spec) | OpenLLMetry alternative | Legacy / custom |
|----------------|---------------------|------------------------|-----------------|
| Model (request) | `gen_ai.request.model` | `llm.model_name` | `llm.model`, `model` |
| Model (response) | `gen_ai.response.model` | `llm.model_name` | — |
| Input tokens | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` | `usage.prompt_tokens` |
| Output tokens | `gen_ai.usage.output_tokens` | `llm.token_count.completion` | `usage.completion_tokens` |
| Temperature | `gen_ai.request.temperature` | `llm.invocation_parameters.temperature` | — |
| Top-p | `gen_ai.request.top_p` | `llm.invocation_parameters.top_p` | — |
| Provider | `gen_ai.system` | `llm.system` | — |
| Prompt content | `gen_ai.prompt` event | `llm.prompts` attribute | `input` attribute |
| Completion content | `gen_ai.completion` event | `llm.completions` attribute | `output` attribute |

✅ For new instrumentation, use `gen_ai.*` exclusively — it is the converging standard.

❌ Don't mix `gen_ai.*` and `llm.*` on the same span. Langfuse uses the first non-null value it finds, but the priority order is `gen_ai.*` > `llm.*` > legacy keys. Mixing creates confusing precedence.

💡 If you are consuming spans from a third-party library you don't control (e.g., an OpenLLMetry-instrumented framework), you don't need to do anything — Langfuse handles the mapping automatically.

---

## 7. Metadata Mapping

Not everything fits into the structured `gen_ai.*` schema. Langfuse provides three tiers of metadata storage for arbitrary data:

```
┌─────────────────────────────────────────────────────┐
│                    Metadata Tiers                    │
│                                                     │
│  1. langfuse.trace.metadata.{key}                   │
│     → Top-level filterable metadata field           │
│     → Queryable as metadata.{key} in the UI         │
│     → Example: langfuse.trace.metadata.customer_tier│
│                                                     │
│  2. Standard OTel span attributes                   │
│     → Stored under metadata.attributes              │
│     → Not filterable, but visible in trace detail   │
│     → Example: http.method, db.statement            │
│                                                     │
│  3. Resource attributes                             │
│     → Stored under metadata.resourceAttributes      │
│     → Set once on the SDK, applies to all spans     │
│     → Example: service.name, service.version        │
└─────────────────────────────────────────────────────┘
```

**Tier 1** is the most useful for business context — it keeps metadata flat and filterable:

```python
# Sets metadata.customer_tier = "enterprise" on the trace
span.set_attribute("langfuse.trace.metadata.customer_tier", "enterprise")
span.set_attribute("langfuse.trace.metadata.request_id", "req_abc123")
span.set_attribute("langfuse.trace.metadata.feature_flag", "new_rag_pipeline")
```

**Tier 3** (resource attributes) is set at SDK initialization, not per-span:

```python
from opentelemetry.sdk.resources import Resource

resource = Resource.create({
    "service.name": "chat-api",
    "service.version": "2.3.1",
    "deployment.environment": "production",
})
```

These appear in Langfuse under `metadata.resourceAttributes` on every trace that SDK instance produces.

---

## 8. AI Agent Attributes (2025)

The OTel GenAI working group extended the conventions in 2025 to cover **agentic workflows** — systems where an LLM issues tool calls, reasons across multiple steps, and orchestrates sub-agents. This is now part of the experimental spec.

### Agent-specific span kinds

| Span kind | `gen_ai.operation.name` | When to use |
|-----------|------------------------|-------------|
| LLM call  | `chat` | Single model inference call |
| Tool call | `execute_tool` | Executing a tool the model requested |
| Agent step | `invoke_agent` | One reasoning cycle of an agent |
| Embedding | `embeddings` | Embedding model call |

### Tool call attributes

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `gen_ai.tool.name` | string | `search_web`, `run_sql` | Name of the tool being invoked. |
| `gen_ai.tool.description` | string | `"Search the web for..."` | Tool description as given to the model. |
| `gen_ai.tool.call.id` | string | `call_abc123` | Provider-assigned tool call ID from the LLM response. |

### Multi-step agent flow

```
┌─────────────────────────────────────────────────────────┐
│  Root Span: invoke_agent  (gen_ai.operation.name)        │
│  langfuse.user.id, langfuse.session.id set here          │
│                                                         │
│  ├── Child Span: chat  (LLM decides to use tools)        │
│  │   Events: gen_ai.prompt, gen_ai.completion            │
│  │   gen_ai.response.finish_reasons = ["tool_calls"]     │
│  │                                                       │
│  ├── Child Span: execute_tool  (run search_web)          │
│  │   gen_ai.tool.name = "search_web"                     │
│  │   gen_ai.tool.call.id = "call_xyz"                    │
│  │                                                       │
│  └── Child Span: chat  (LLM synthesizes final answer)    │
│      Events: gen_ai.prompt (includes tool result),       │
│              gen_ai.completion                           │
│      gen_ai.response.finish_reasons = ["stop"]           │
└─────────────────────────────────────────────────────────┘
```

> **Key insight**: In agentic spans, `gen_ai.usage.*` tokens should be set on each individual `chat` span, not accumulated on the root agent span. Langfuse sums them automatically when computing trace-level token counts.

---

## 9. Full Example — Manual Instrumentation with All Key Attributes

This example shows how to set the complete attribute set on a span manually. In practice you would use an instrumentation library (OpenLLMetry, the Langfuse SDK, etc.) that does this automatically — but reading this code is the fastest way to understand what data gets collected and where.

```python
import json
import time
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
import openai

# --- SDK bootstrap (done once at application startup) ---

resource = Resource.create({
    "service.name": "document-qa-api",
    "service.version": "1.4.2",
    "deployment.environment": "production",
})

exporter = OTLPSpanExporter(
    endpoint="https://us.cloud.langfuse.com/api/public/otel/v1/traces",
    headers={
        # Base64-encoded "pk_...:sk_..." — see python_sdk/01_setup_and_config.md
        "Authorization": "Basic <base64-encoded-credentials>",
    },
)

provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("document-qa-api", "1.4.2")
openai_client = openai.OpenAI()


# --- Per-request instrumentation ---

def answer_document_question(
    user_id: str,
    session_id: str,
    system_prompt: str,
    question: str,
    model: str = "gpt-4o",
    temperature: float = 0.2,
) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    # Root span: sets trace-level Langfuse metadata
    with tracer.start_as_current_span("answer_document_question") as root_span:
        # Langfuse-specific: trace identity and routing
        root_span.set_attribute("langfuse.user.id", user_id)
        root_span.set_attribute("langfuse.session.id", session_id)
        root_span.set_attribute("langfuse.trace.name", "document_qa")
        root_span.set_attribute("langfuse.release", "1.4.2")
        root_span.set_attribute("langfuse.trace.tags", ["document-qa", "gpt-4o"])

        # Business context as filterable metadata
        root_span.set_attribute("langfuse.trace.metadata.question_length", len(question))
        root_span.set_attribute("langfuse.trace.metadata.feature", "document-qa")

        # Child span: the actual LLM call
        with tracer.start_as_current_span("llm_call") as llm_span:
            # Mark this span as a generation so Langfuse renders it correctly
            llm_span.set_attribute("langfuse.observation.type", "generation")

            # GenAI spec: request attributes (set before the call)
            llm_span.set_attribute("gen_ai.system", "openai")
            llm_span.set_attribute("gen_ai.request.model", model)
            llm_span.set_attribute("gen_ai.request.temperature", temperature)
            llm_span.set_attribute("gen_ai.request.max_tokens", 1024)

            # Capture the prompt as a span event (not an attribute — size reasons)
            llm_span.add_event(
                "gen_ai.prompt",
                attributes={
                    # JSON-encode the messages array
                    "gen_ai.prompt": json.dumps(messages),
                },
            )

            call_start = time.monotonic()
            response = openai_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=1024,
            )
            latency_ms = (time.monotonic() - call_start) * 1000

            answer = response.choices[0].message.content

            # GenAI spec: response attributes (set after the call)
            llm_span.set_attribute("gen_ai.response.model", response.model)
            llm_span.set_attribute(
                "gen_ai.response.finish_reasons",
                [response.choices[0].finish_reason],
            )
            llm_span.set_attribute("gen_ai.response.id", response.id)

            # Usage — these drive token counts and cost in Langfuse
            llm_span.set_attribute(
                "gen_ai.usage.input_tokens", response.usage.prompt_tokens
            )
            llm_span.set_attribute(
                "gen_ai.usage.output_tokens", response.usage.completion_tokens
            )

            # Cost (non-spec, widely supported by Langfuse)
            # gpt-4o pricing as of Q1 2025: $2.50/1M input, $10.00/1M output
            cost = (
                response.usage.prompt_tokens * 2.50 / 1_000_000
                + response.usage.completion_tokens * 10.00 / 1_000_000
            )
            llm_span.set_attribute("gen_ai.usage.cost", cost)

            # Completion as a span event
            llm_span.add_event(
                "gen_ai.completion",
                attributes={
                    "gen_ai.completion": json.dumps(
                        [
                            {
                                "role": "assistant",
                                "content": answer,
                                "finish_reason": response.choices[0].finish_reason,
                            }
                        ]
                    ),
                },
            )

    return answer
```

Key points about this example:

- The **root span** carries all `langfuse.*` trace-level attributes. Child spans do not need to repeat them.
- The **LLM child span** uses `langfuse.observation.type = "generation"` to tell Langfuse to render it as a Generation observation (with token counts, model, cost).
- Prompt and completion use `add_event()` with the official event names — this avoids attribute size limits.
- Cost is calculated client-side. Langfuse can also compute it server-side from the model name and token counts if you configure a price list, but explicit `gen_ai.usage.cost` always wins.

---

## 10. Common Mistakes and Anti-Patterns

❌ **Setting `langfuse.user.id` on a child span instead of the root span**

```python
# Wrong: child span attributes don't populate trace-level fields
with tracer.start_as_current_span("llm_call") as span:
    span.set_attribute("langfuse.user.id", user_id)  # ❌ ignored for trace userId
```

✅ Set identity attributes on the root span, or propagate them via Baggage (see [../otel_integration/03_attribute_propagation.md](../otel_integration/03_attribute_propagation.md)).

---

❌ **Putting the full prompt text in a span attribute**

```python
# Wrong: will blow up attribute size limits and may be truncated or dropped
span.set_attribute("gen_ai.prompt", long_system_prompt + user_message)  # ❌
```

✅ Use `span.add_event("gen_ai.prompt", attributes={"gen_ai.prompt": json.dumps(messages)})`.

---

❌ **Mixing `gen_ai.*` and `llm.*` on the same span**

```python
span.set_attribute("gen_ai.request.model", "gpt-4o")   # gen_ai namespace
span.set_attribute("llm.model_name", "gpt-4o")          # ❌ OpenLLMetry namespace
# Redundant — Langfuse picks gen_ai.request.model, llm.model_name is ignored
```

---

⚠️ **Not setting `gen_ai.response.model`**

`gen_ai.request.model` is what you asked for. `gen_ai.response.model` is what actually ran your prompt. These can differ — OpenAI regularly aliases model names to versioned snapshots. Always read `response.model` from the API response and set it explicitly. Langfuse uses the response model for cost calculations.

---

⚠️ **Using `gen_ai.usage.cost` without a currency assumption**

The GenAI spec doesn't define a currency for `gen_ai.usage.cost`. The convention is USD. If your cost is in another currency, either convert to USD or document the currency in a companion metadata attribute.

---

💡 **Tip — let the instrumentation library handle this for you**

For OpenAI, Anthropic, and LangChain, OpenLLMetry's auto-instrumentation sets all of the above attributes correctly:

```python
from opentelemetry.instrumentation.openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument()
# Every openai.chat.completions.create() call now produces a fully-attributed span
```

Write manual instrumentation (like the example in Section 9) only when you need fine-grained control or are wrapping a provider that doesn't have an existing instrumentor.

---

## 11. Attribute Precedence — Full Priority Order

When Langfuse ingests a span, it resolves each logical field from attributes in this priority order:

```
┌──────────────────────────────────────────────────────────────┐
│               Langfuse Attribute Resolution                  │
│                                                              │
│  Priority  Namespace       Example                           │
│  ────────  ─────────────   ────────────────────────────────  │
│     1      langfuse.*      langfuse.user.id                  │
│                            langfuse.trace.name               │
│                            langfuse.observation.type         │
│                                                              │
│     2      gen_ai.*        gen_ai.request.model              │
│            (OTel spec)     gen_ai.usage.input_tokens         │
│                            gen_ai.system                     │
│                                                              │
│     3      llm.*           llm.model_name                    │
│            (OpenLLMetry)   llm.token_count.prompt            │
│                            llm.invocation_parameters.*       │
│                                                              │
│     4      legacy / misc   model, token_count, input         │
│                                                              │
│  First non-null value wins. Later priorities are fallbacks.  │
└──────────────────────────────────────────────────────────────┘
```

This precedence guarantees that Langfuse-specific overrides always win, the standard spec is the default for new code, and legacy instrumentation continues to work without changes.

For the full mapping of how these attributes become Langfuse observations, see [../otel_integration/02_span_mapping.md](../otel_integration/02_span_mapping.md).

---

**Previous**: [02_opentelemetry_primer.md](02_opentelemetry_primer.md) — OTel architecture, traces, spans, the collector pipeline

**Next**: [../python_sdk/01_setup_and_config.md](../python_sdk/01_setup_and_config.md) — Installing the Langfuse Python SDK, configuring credentials, initializing the client
