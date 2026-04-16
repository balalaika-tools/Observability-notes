# What Is Langfuse — Platform Overview, Data Model, and Why It Exists

> **Who this is for**: Python engineers who understand distributed tracing concepts (spans, traces, collectors) from systems like Jaeger or Datadog APM, but are new to LLM observability and want to understand what Langfuse adds — and why general APM tools fall short for AI applications.

---

## 1. The Problem: General APM Does Not Understand LLMs

If you have used **Datadog**, **Jaeger**, or **Honeycomb** to observe a microservices system, you already understand the core loop: emit spans, attach metadata, query traces when something goes wrong. That model works well when your system is deterministic — a database query either succeeds or it doesn't, latency is either acceptable or it isn't.

LLM applications break that model in several ways.

**What general APM gives you for an LLM call:**
- A span with a start time and duration
- HTTP status code (probably 200)
- The request URL (`api.openai.com/v1/chat/completions`)

**What you actually need:**
- The exact prompt that was sent, with all variable substitutions applied
- The raw model response, including finish reason
- Token counts — prompt tokens, completion tokens, total
- Estimated cost in USD for that specific call
- The model name and version (`gpt-4o-2024-08-06`, not just `openai`)
- Temperature, top-p, and other sampling parameters that affected the output
- Whether the model called any tools, and with what arguments
- The latency to first token vs. total latency (critical for streaming)
- A quality score: was this response good? Did it answer the question?

A general APM tool treats an LLM call as an opaque HTTP request. It cannot tell you that your prompt grew by 400 tokens between deploys because you added a new system prompt, which is why your costs doubled. It cannot tell you that the 3% of traces where latency spiked are all requests that triggered tool use. It cannot help you compare prompt version A against prompt version B on a held-out test set.

**Langfuse** is purpose-built to answer exactly these questions.

> **Key insight**: General APM tools observe *infrastructure*. Langfuse observes *AI behaviour* — the prompts, responses, costs, and quality of your model calls, at the level of abstraction that actually matters for LLM applications.

---

## 2. What Langfuse Is

**Langfuse** is an open-source LLM engineering platform. It is not a monitoring tool bolted onto an LLM SDK — it is a purpose-built system with four interconnected capabilities:

| Capability | What It Does | When You Need It |
|---|---|---|
| **Tracing & Observability** | Records every LLM call with full context: prompt, response, tokens, cost, latency | Always — this is the foundation |
| **Prompt Management** | Versions, deploys, and retrieves prompts via API; links each production call to the prompt version that generated it | When prompts are changing and you need rollback or A/B visibility |
| **Evaluation** | Attaches quality scores to traces — via human annotation, rule-based logic, or LLM-as-judge | When you need to measure output quality, not just latency |
| **Datasets & Experiments** | Runs a prompt or chain against a curated dataset; compares runs side-by-side | When you need offline regression testing before a prompt change ships |

These four capabilities share a common data model, which is described in section 3. They are not separate products — a trace captured by the observability layer can have a score added to it by the evaluation layer, and that score can become part of a dataset for the experiments layer.

Langfuse is **self-hostable** (open-source, Docker-compose or Kubernetes) or available as a managed cloud service:
- EU region: `cloud.langfuse.com`
- US region: `us.cloud.langfuse.com`

The SDK sends data to whichever endpoint you configure. The self-hosted option is architecturally identical to cloud — same API, same UI.

---

## 3. The Core Data Model

Understanding Langfuse's data model before touching the SDK saves significant debugging time later. There are five object types, arranged in a strict hierarchy.

```
┌─────────────────────────────────────────────────────────────┐
│  SESSION (groups multiple traces — e.g., a chat thread)     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TRACE  (one complete request / user turn)           │   │
│  │                                                      │   │
│  │  ┌──────────────────┐  ┌───────────────────────────┐ │   │
│  │  │  SPAN            │  │  GENERATION               │ │   │
│  │  │  (any operation) │  │  (LLM model invocation)   │ │   │
│  │  │                  │  │  ├─ model name            │ │   │
│  │  │  ┌────────────┐  │  │  ├─ prompt tokens         │ │   │
│  │  │  │  SPAN      │  │  │  ├─ completion tokens     │ │   │
│  │  │  │  (nested)  │  │  │  ├─ cost (USD)            │ │   │
│  │  │  └────────────┘  │  │  └─ model parameters      │ │   │
│  │  └──────────────────┘  └───────────────────────────┘ │   │
│  │                                                      │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  EVENT  (discrete point-in-time occurrence)  │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │                                                      │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  SCORE  (quality evaluation — attaches to    │   │   │
│  │  │          trace, span, generation, or session)│   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Trace

A **Trace** is the root container for one complete execution lifecycle — typically one user request. Everything else lives inside a trace. When a user sends a message to your chatbot, one trace is created. That trace might contain a retrieval span, a prompt-construction span, a generation (the actual LLM call), and a post-processing span.

Key fields on a trace:
- `id` — unique identifier (UUID or your own string)
- `name` — human-readable label (`"chat_response"`, `"document_summary"`)
- `input` / `output` — the top-level request and final response
- `user_id` — the end user who triggered this trace
- `session_id` — the session this trace belongs to (optional)
- `metadata` — arbitrary key-value pairs for filtering

### 3.2 Span

A **Span** represents a generic unit of work within a trace — a retrieval step, a data transformation, a tool execution, a cache lookup. Spans can be nested arbitrarily deep. A span that calls an external vector database might itself contain a child span for the embedding generation.

Spans model the same concept as OpenTelemetry spans (section 5 explains the relationship in depth). They have a start time, end time, and can carry attributes.

### 3.3 Generation

A **Generation** is a specialized span for **LLM model invocations**. It is the most important object type in Langfuse. In addition to all span fields, a generation has:

| Field | Type | Example |
|---|---|---|
| `model` | string | `"gpt-4o-2024-08-06"` |
| `model_parameters` | dict | `{"temperature": 0.2, "max_tokens": 1024}` |
| `input` | list[dict] | The messages array sent to the model |
| `output` | dict | The model's response object |
| `usage.prompt_tokens` | int | `342` |
| `usage.completion_tokens` | int | `89` |
| `usage.total_tokens` | int | `431` |
| `usage.input_cost` | Decimal | `0.000171` |
| `usage.output_cost` | Decimal | `0.000534` |
| `usage.total_cost` | Decimal | `0.000705` |
| `prompt_name` / `prompt_version` | str/int | Links to managed prompt |

> **Key insight**: The `Generation` type is what makes Langfuse fundamentally different from general APM. It is not a tag on a span — it is a first-class object that the UI, the cost aggregation engine, and the evaluation system all understand natively.

### 3.4 Event

An **Event** is a discrete, point-in-time occurrence with no duration — it happens instantaneously. Use events for things like "user clicked thumbs down", "cache hit detected", "guardrail triggered", or "retry attempt 2 of 3". Events are lightweight and do not need start/end timestamps.

### 3.5 Session

A **Session** groups multiple traces that belong to the same logical conversation or user flow. In a multi-turn chatbot, each user turn creates one trace; all turns in the same conversation share one `session_id`.

Sessions enable a crucial analytical capability: you can observe how quality, cost, and latency evolve across the turns of a conversation, rather than looking at each turn in isolation.

### 3.6 Score

A **Score** is the universal evaluation data object. It is not part of the trace hierarchy but attaches to any object: a trace, a generation, a span, or a session. A score has:

- `name` — the evaluation dimension (`"correctness"`, `"toxicity"`, `"groundedness"`)
- `value` — numeric or categorical
- `comment` — optional free-text explanation
- `source` — `"human"`, `"model"` (LLM-as-judge), or `"api"` (rule-based)

See [`../evaluation/`](../evaluation/) for a deep-dive on how scores are created and used.

---

## 4. What Langfuse Captures Per LLM Call

Here is a concrete example. Suppose you have a RAG pipeline: the user asks a question, you retrieve documents, and you call GPT-4o with the retrieved context. This is what one trace looks like, and what Langfuse records for the generation:

```python
import os
from langfuse import Langfuse
from langfuse.openai import openai  # drop-in replacement for the openai client

# Langfuse is configured via environment variables:
# LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
langfuse = Langfuse()

def answer_question(user_id: str, session_id: str, question: str) -> str:
    # Create a root trace for this request
    trace = langfuse.trace(
        name="rag_answer",
        user_id=user_id,
        session_id=session_id,
        input={"question": question},
    )

    # Span for the retrieval step — Langfuse records start/end time
    retrieval_span = trace.span(
        name="vector_retrieval",
        input={"query": question},
    )
    retrieved_docs = retrieve_from_vectorstore(question)  # your retrieval logic
    retrieval_span.end(
        output={"num_docs": len(retrieved_docs), "doc_ids": [d.id for d in retrieved_docs]},
    )

    # The LLM call — Langfuse captures the full prompt, response, tokens, and cost.
    # Using langfuse.openai means the generation is automatically recorded.
    messages = build_messages(question, retrieved_docs)
    response = openai.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=messages,
        temperature=0.1,
        max_tokens=512,
        # langfuse_observation_id links this call to the trace
        langfuse_trace_id=trace.id,
        langfuse_observation_id=trace.id + "_generation",
    )

    answer = response.choices[0].message.content

    # Close the root trace with the final output
    trace.update(output={"answer": answer})
    return answer
```

After this runs, the Langfuse UI shows the trace with:

- The exact `messages` array that was sent to GPT-4o (including the retrieved context)
- The model's raw response JSON
- `prompt_tokens: 1842`, `completion_tokens: 187`, `total_tokens: 2029`
- `total_cost: $0.00243` (calculated automatically from the model's public pricing)
- Latency breakdown: retrieval took 120ms, generation took 1,340ms
- The `user_id` and `session_id` for filtering and grouping

None of this requires changes to your business logic. The `langfuse.openai` wrapper intercepts the OpenAI client call and records everything automatically.

---

## 5. The SDK Architecture: Built on OpenTelemetry

This is the architectural detail that most Langfuse users learn late, but that matters a great deal for production deployments.

The Langfuse Python SDK v3+ (and JS SDK v4+) are **built on top of OpenTelemetry**. The `LangfuseSpan` and `LangfuseGeneration` objects you create via the SDK are not Langfuse-proprietary objects — they are **native OpenTelemetry spans** with Langfuse-specific attributes attached to them.

```
┌───────────────────────────────────────────────────────┐
│                  Your Application                     │
└───────────────────────┬───────────────────────────────┘
                        │  uses
                        ▼
┌───────────────────────────────────────────────────────┐
│              Langfuse Python SDK v3+                  │
│                                                       │
│   LangfuseSpan       →  wraps  →  OTel Span           │
│   LangfuseGeneration →  wraps  →  OTel Span           │
│     + model, tokens, cost attrs on the OTel span      │
└───────────────────────┬───────────────────────────────┘
                        │  emits
                        ▼
┌───────────────────────────────────────────────────────┐
│           OpenTelemetry SDK (opentelemetry-sdk)       │
│                                                       │
│   TracerProvider → BatchSpanProcessor → Exporter      │
└───────────────────────┬───────────────────────────────┘
                        │  OTLP/HTTP or OTLP/gRPC
                        ▼
┌───────────────────────────────────────────────────────┐
│              Langfuse Backend  (OTLP endpoint)        │
│   Receives OTel spans, maps Langfuse attrs to its     │
│   Trace / Generation / Span / Event data model        │
└───────────────────────────────────────────────────────┘
```

This architecture has two important consequences:

**1. You can use standard OTel instrumentation libraries alongside Langfuse.** If you have `opentelemetry-instrumentation-httpx` or `opentelemetry-instrumentation-sqlalchemy` already wired up, those spans will appear in the same trace tree as your Langfuse generations. You get a unified view of your LLM calls alongside the database queries and HTTP calls they trigger.

**2. You can export to multiple backends simultaneously.** Because the SDK emits standard OTLP spans, you can configure a second exporter to send the same spans to Jaeger or Honeycomb. Langfuse-specific attributes will be ignored by those backends, but the core trace structure will be visible everywhere.

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from langfuse import Langfuse

# Langfuse registers its OTLP exporter automatically when you call Langfuse().
# You can add additional exporters to the same TracerProvider.
langfuse = Langfuse()  # sets up OTel TracerProvider internally

# Add a second exporter — e.g., your existing Jaeger or Honeycomb backend
provider = langfuse.get_tracer_provider()  # access the underlying OTel provider
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint="https://your-otel-collector/v1/traces")
    )
)
# Now every span goes to both Langfuse and your existing backend.
```

See [`02_opentelemetry_primer.md`](02_opentelemetry_primer.md) for a full explanation of the OTel architecture, and [`../otel_integration/`](../otel_integration/) for production configuration patterns.

---

## 6. Async-First Design and Latency Impact

One of the most common questions from engineers evaluating Langfuse is: **how much latency does this add to my production requests?**

The answer is: **near zero**, by design.

Langfuse uses an **async-first, batched export** model. When your code creates a span or generation, the SDK does not make an HTTP request to the Langfuse server. Instead:

```
Your code calls trace.span(...)
        │
        ▼
Span data is serialized and placed in an in-process queue (memory buffer)
        │
        │  (your code continues immediately — no blocking)
        ▼
Background thread / async task drains the queue every N seconds
or when the batch reaches M items
        │
        ▼
Batch of spans is sent to Langfuse via OTLP/HTTP
```

The in-process queue is the same `BatchSpanProcessor` from the OpenTelemetry SDK. Default settings send batches every 5 seconds or when 512 spans accumulate.

This means:
- Adding Langfuse to a production endpoint typically adds **< 1ms** to p99 latency
- Network errors to the Langfuse backend do not cause errors in your application
- At application shutdown, you must call `langfuse.flush()` to drain any queued spans before the process exits

```python
import atexit
from langfuse import Langfuse

langfuse = Langfuse()

# Register flush at process exit — critical for scripts and short-lived processes.
# Long-running servers (FastAPI, Gunicorn) flush continuously; this matters for
# CLI tools, batch jobs, and test suites that exit immediately after the last call.
atexit.register(langfuse.flush)

# Or use the context manager for a bounded scope:
with langfuse:
    trace = langfuse.trace(name="batch_job")
    # ... do work ...
# langfuse.__exit__ calls flush() automatically
```

⚠️ The most common production bug with Langfuse is a script that completes successfully but has no traces in the UI because `flush()` was never called and the process exited before the background thread could send the queued batch. Always register `atexit.register(langfuse.flush)` or use the context manager.

---

## 7. Langfuse vs. General APM — Feature Comparison

| Capability | Datadog / Jaeger | Langfuse |
|---|---|---|
| Distributed tracing | ✅ Native | ✅ Via OTel |
| Span timing and latency | ✅ | ✅ |
| HTTP/DB/queue instrumentation | ✅ Rich auto-instrumentation | ✅ Via OTel plugins |
| LLM prompt capture | ❌ | ✅ First-class |
| LLM response capture | ❌ | ✅ First-class |
| Token usage tracking | ❌ | ✅ Per-call, aggregatable |
| Cost estimation | ❌ | ✅ Automatic, model-aware |
| Generation type (vs. generic span) | ❌ | ✅ Native object type |
| Prompt versioning and management | ❌ | ✅ Built-in |
| LLM-as-judge evaluation | ❌ | ✅ Built-in |
| Human annotation interface | ❌ | ✅ Built-in annotation queues |
| Dataset creation from traces | ❌ | ✅ Built-in |
| Offline experiment comparison | ❌ | ✅ Built-in |
| Self-hostable | ⚠️ Partial / complex | ✅ Docker-compose in minutes |
| Open source | ❌ (Jaeger yes, Datadog no) | ✅ MIT licensed |

> **Key insight**: If you are already using Datadog for infrastructure metrics and alerts, you do not need to replace it with Langfuse. The two are complementary. Datadog tells you when your pod is OOMKilled; Langfuse tells you why your model's output quality dropped after last Tuesday's prompt change.

---

## 8. A Complete Minimal Example

This example shows the full lifecycle: create a trace, record a retrieval span, record an LLM generation with manual token tracking, attach a score, and flush.

```python
import os
from decimal import Decimal
from langfuse import Langfuse
from langfuse.model import ModelUsage

langfuse = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)

def process_support_ticket(
    ticket_id: str,
    user_id: str,
    session_id: str,
    ticket_text: str,
) -> dict:
    trace = langfuse.trace(
        name="support_ticket_response",
        # trace-level metadata for filtering in the Langfuse UI
        user_id=user_id,
        session_id=session_id,
        input={"ticket_id": ticket_id, "text": ticket_text},
        metadata={"ticket_id": ticket_id, "channel": "email"},
    )

    # --- Step 1: Classify the ticket ---
    classification_span = trace.span(
        name="classify_ticket",
        input={"text": ticket_text},
    )
    category = classify_ticket(ticket_text)  # your rule-based classifier
    classification_span.end(output={"category": category})

    # --- Step 2: Retrieve knowledge base articles ---
    kb_span = trace.span(
        name="kb_retrieval",
        input={"category": category, "query": ticket_text},
    )
    articles = retrieve_kb_articles(category, ticket_text)
    kb_span.end(output={"num_articles": len(articles), "article_ids": [a["id"] for a in articles]})

    # --- Step 3: Generate response (LLM call) ---
    # Using a managed prompt — Langfuse records which version was used
    prompt_template = langfuse.get_prompt("support_response_v2")
    messages = prompt_template.compile(
        ticket_category=category,
        kb_articles="\n\n".join(a["content"] for a in articles),
        customer_message=ticket_text,
    )

    # Record the generation — this is the core Langfuse object for LLM calls
    generation = trace.generation(
        name="draft_response",
        model="gpt-4o-mini",
        model_parameters={"temperature": 0.3, "max_tokens": 800},
        input=messages,
        prompt=prompt_template,  # links this generation to the prompt version
    )

    # Your actual OpenAI call (could also use langfuse.openai for auto-capture)
    import openai
    raw_response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.3,
        max_tokens=800,
    )
    response_text = raw_response.choices[0].message.content

    # Close the generation with output and usage — Langfuse calculates cost automatically
    generation.end(
        output=raw_response.choices[0].message.model_dump(),
        usage=ModelUsage(
            input=raw_response.usage.prompt_tokens,
            output=raw_response.usage.completion_tokens,
        ),
    )

    # --- Step 4: Attach an automated quality score ---
    # Rule-based: penalise responses that don't reference the ticket category
    quality = 1.0 if category.lower() in response_text.lower() else 0.5
    langfuse.score(
        trace_id=trace.id,
        name="category_grounding",
        value=quality,
        comment=f"Expected category '{category}' mentioned in response",
        source="api",  # rule-based, not human or model
    )

    # Close the root trace
    trace.update(output={"response": response_text, "category": category})
    return {"response": response_text, "category": category}
```

After this runs, the Langfuse UI shows:
- A trace named `support_ticket_response` with `user_id` and `session_id` set
- Three children: `classify_ticket` (span), `kb_retrieval` (span), `draft_response` (generation)
- The generation has the exact messages array, full response object, token counts, cost, and a link to `support_response_v2` prompt version
- A `category_grounding` score of 1.0 or 0.5 attached to the trace

---

**Next**: [Part 2: OpenTelemetry Primer](02_opentelemetry_primer.md)
