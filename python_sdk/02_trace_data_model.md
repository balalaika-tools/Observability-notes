# Trace Data Model: Trace, Span, Generation, Event

> **Who this is for**: Python engineers who have the Langfuse SDK installed (see [Setup & Config](01_setup_and_config.md)) and want a clear mental model of what gets created before writing any instrumentation code. This is a field-level reference you will return to repeatedly.

---

## 1. The Hierarchy

Every piece of observability data in Langfuse lives inside a hierarchy. Before reading individual field tables, understand the shape of what you're building.

```
Session  (groups multi-turn conversations — same session_id across many traces)
│
└── Trace  (one request / one LLM interaction — the root container)
    │
    ├── Span  (retrieval step, DB query, API call — any non-LLM operation)
    │   │
    │   └── Generation  (LLM call nested inside a retrieval pipeline)
    │
    ├── Generation  (direct LLM call at the top level of the trace)
    │
    └── Event  (tool call result, decision point, user action — instant in time)
```

> **Key insight**: A **Trace** is the root. Everything else — Span, Generation, Event — is an **observation** that belongs to a trace. They share the same `trace_id`. Sessions are not observations; they are a grouping mechanism that spans multiple traces.

The four observation types map to different concepts:

| Type | Analogy | Has duration? | LLM-specific fields? |
|------|---------|:---:|:---:|
| `Span` | A function call or I/O operation | ✅ | ❌ |
| `Generation` | An LLM invocation | ✅ | ✅ |
| `Event` | A log line with context | ❌ (point-in-time) | ❌ |
| Trace | The root envelope | ✅ (inferred) | ❌ |

---

## 2. Trace — The Root Container

A **Trace** is created for every logical unit of work — typically one user request, one pipeline run, or one LLM interaction. It is the envelope that all observations attach to.

```python
from langfuse import Langfuse

langfuse = Langfuse()

# Creating a trace explicitly — all fields are optional
trace = langfuse.trace(
    name="rag-answer-pipeline",
    input={"question": "What is retrieval-augmented generation?"},
    output={"answer": "RAG combines a retrieval step with an LLM generation..."},
    user_id="user_abc123",
    session_id="session_xyz789",
    metadata={
        "environment": "production",
        "pipeline_version": "2.4.1",
        "retrieval_strategy": "hybrid",
    },
    tags=["prod", "rag", "v2"],
    version="2.4.1",
    release="2024-q4-release",
    public=False,
)
```

### Trace Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | Auto UUID | Unique trace identifier. Provide a custom value for idempotency or cross-system correlation. |
| `name` | `str` | `None` | Human-readable label shown in the Langfuse UI. Use a consistent naming scheme (e.g., `rag-answer-pipeline`). |
| `input` | `Any` | `None` | The request that triggered this trace — dict, string, or any JSON-serializable value. |
| `output` | `Any` | `None` | The final response produced. Set this at the end of your handler. |
| `user_id` | `str` | `None` | Who made the request. Critical for user-level analytics, cost attribution, and abuse detection. |
| `session_id` | `str` | `None` | Groups traces into a conversation. All traces with the same `session_id` appear together in the session view. |
| `metadata` | `dict` | `None` | Arbitrary key-value pairs for custom fields. Not indexed — use `tags` for filterable labels. |
| `tags` | `list[str]` | `[]` | String array for UI filtering. Example: `["prod", "rag", "v2"]`. Indexed and filterable. |
| `version` | `str` | `None` | Application version string. Enables per-version comparison in analytics. |
| `release` | `str` | `None` | Release identifier (e.g., git SHA, deploy tag). Distinct from `version` — use both if you track both. |
| `timestamp` | `datetime` | `now()` | When the trace started. Defaults to the moment the trace object is created. |
| `public` | `bool` | `False` | If `True`, the trace URL is publicly accessible without authentication — useful for sharing with stakeholders. |

### Updating Trace Fields After Creation

Trace-level fields (`output`, `user_id`, etc.) are often not known at trace creation time — you may only have `output` once the pipeline finishes. Use `update_trace` to backfill them:

```python
trace = langfuse.trace(
    name="rag-answer-pipeline",
    input={"question": user_question},
    user_id=request.user_id,
    session_id=request.session_id,
)

# ... run the pipeline ...

# Set output once you have it — this is a PATCH, not a replacement
trace.update(
    output={"answer": final_answer, "sources": source_urls},
    metadata={"total_latency_ms": elapsed_ms},
)
```

> **Key insight**: `trace.update()` is a partial update — it merges the new fields with whatever was already set. You do not need to repeat fields you already provided.

---

## 3. Span — Generic Operation

A **Span** represents any operation that has a start time, an end time, and produces some output — but is not an LLM call. Use it to wrap retrieval steps, pre/post-processing, external API calls, and database queries.

```python
# Manual span creation inside a trace context
with trace.span(
    name="vector-retrieval",
    input={"query_embedding": query_vector[:5], "top_k": 10},
    metadata={"index": "prod-knowledge-base", "metric": "cosine"},
) as retrieval_span:
    # start_time is set automatically when entering the context manager
    docs = vector_store.similarity_search(query_vector, k=10)

    retrieval_span.update(
        output={"doc_count": len(docs), "top_score": docs[0].score},
        level="DEFAULT",
    )
    # end_time is set automatically on context manager exit
```

> **Rule**: If your operation makes an LLM API call, use a `Generation` not a `Span`. If it does anything else — including pre/post-processing around an LLM call — use a `Span`.

### Span Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | Auto UUID | Observation identifier. Auto-generated unless you need idempotency. |
| `name` | `str` | `None` | Label for this operation in the UI (e.g., `vector-retrieval`, `reranking`). |
| `start_time` | `datetime` | `now()` | When the operation began. Set automatically by context managers. |
| `end_time` | `datetime` | `None` | When the operation completed. Set automatically on context exit. |
| `input` | `Any` | `None` | What this operation received — query, document list, etc. |
| `output` | `Any` | `None` | What this operation produced. |
| `level` | `str` | `DEFAULT` | Severity level. One of `DEFAULT`, `DEBUG`, `WARNING`, `ERROR`. |
| `status_message` | `str` | `None` | Human-readable status detail — especially useful when `level` is `ERROR`. |
| `metadata` | `dict` | `None` | Arbitrary key-value pairs for this specific observation. |
| `version` | `str` | `None` | Version of this component (e.g., retrieval strategy version). |
| `parent_observation_id` | `str` | (from context) | Explicit parent in manual (non-context-manager) mode. See [Section 9](#9-observation-nesting-rules). |

The SDK class that backs a span is **`LangfuseSpan`**, which wraps an OTel span under the hood.

---

## 4. Generation — LLM Invocation

A **Generation** is the most important observation type. It carries all the LLM-specific data that Langfuse uses for cost tracking, latency analysis, and quality evaluation.

```python
import time
from langfuse.model import Usage

generation = trace.generation(
    name="answer-synthesis",
    model="gpt-4o",
    model_parameters={
        "temperature": 0.2,
        "max_tokens": 1024,
        "top_p": 0.9,
    },
    input=[
        {"role": "system", "content": "You are a helpful assistant. Use the provided context."},
        {"role": "user", "content": f"Context:\n{retrieved_context}\n\nQuestion: {user_question}"},
    ],
)

completion_start = time.time()
response = openai_client.chat.completions.create(
    model="gpt-4o",
    messages=generation.input,
    temperature=0.2,
    max_tokens=1024,
)
first_token_time = time.time()  # capture TTFT

generation.update(
    output=response.choices[0].message.content,
    completion_start_time=first_token_time,  # used to compute TTFT
    usage_details={
        "input": response.usage.prompt_tokens,
        "output": response.usage.completion_tokens,
        "total": response.usage.total_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cached_tokens", 0),
    },
    metadata={"finish_reason": response.choices[0].finish_reason},
)
generation.end()
```

### Generation Field Reference

All Span fields apply, plus the following LLM-specific fields:

| Field | Type | Description |
|-------|------|-------------|
| `model` | `str` | Model identifier — e.g., `gpt-4o`, `claude-3-5-sonnet-20241022`, `llama-3.1-70b-instruct`. Must match a model known to Langfuse for automatic cost calculation. |
| `model_parameters` | `dict` | Sampling configuration passed to the API. Common keys: `temperature`, `top_p`, `max_tokens`, `frequency_penalty`, `seed`. |
| `usage_details` | `dict` | Token breakdown. Keys: `input` (prompt tokens), `output` (completion tokens), `total`, `cache_read_input_tokens`. Langfuse uses these with per-model pricing to compute cost. |
| `cost_details` | `dict` | Explicit monetary cost breakdown if you calculate it yourself. Keys: `input_cost`, `output_cost`, `total_cost` (all in USD). Overrides the automatic cost calculation from `usage_details`. |
| `completion_start_time` | `datetime` | Timestamp of the first token arriving. Used by Langfuse to compute **TTFT** (time-to-first-token) as `completion_start_time - start_time`. |
| `prompt_name` | `str` | Name of a Langfuse-managed prompt linked to this generation. Enables prompt versioning analytics. |
| `prompt_version` | `int` | Version number of the linked Langfuse prompt. Used alongside `prompt_name`. |

The SDK class that backs a generation is **`LangfuseGeneration`**, which also wraps an OTel span.

### Span vs. Generation — Side-by-Side Comparison

| Field | Span | Generation |
|-------|:----:|:----------:|
| `id` | ✅ | ✅ |
| `name` | ✅ | ✅ |
| `start_time` / `end_time` | ✅ | ✅ |
| `input` / `output` | ✅ | ✅ |
| `level` / `status_message` | ✅ | ✅ |
| `metadata` / `version` | ✅ | ✅ |
| `model` | ❌ | ✅ |
| `model_parameters` | ❌ | ✅ |
| `usage_details` | ❌ | ✅ |
| `cost_details` | ❌ | ✅ |
| `completion_start_time` | ❌ | ✅ |
| `prompt_name` / `prompt_version` | ❌ | ✅ |

> **Key insight**: Every generation is a span with extra LLM fields. If Langfuse can't find the model in its pricing catalog, it skips cost calculation silently — set `cost_details` explicitly if you manage pricing yourself.

---

## 5. Event — Point-in-Time Occurrence

An **Event** represents something that happened at a specific instant — it has no duration. Use it for tool call results, retrieval decisions, guard rail triggers, and user actions that happen between spans.

```python
# Record a tool call result that returns immediately
tool_event = trace.event(
    name="weather-tool-call",
    input={
        "tool": "get_current_weather",
        "arguments": {"location": "San Francisco, CA", "unit": "celsius"},
    },
    output={
        "temperature": 18.5,
        "condition": "partly cloudy",
        "humidity": 72,
    },
    level="DEFAULT",
    metadata={"tool_call_id": "call_a1b2c3d4"},
)

# Record a routing decision
langfuse.event(
    trace_id=trace.id,
    name="query-router-decision",
    input={"user_query": user_question, "query_type_scores": type_scores},
    output={"routed_to": "rag-pipeline", "confidence": 0.94},
    level="DEBUG",
    metadata={"routing_model": "bert-base-classifier-v3"},
)
```

### Event Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | Auto UUID | Observation identifier. |
| `name` | `str` | `None` | Label for this event in the UI. |
| `timestamp` | `datetime` | `now()` | When the event occurred. Events have no `end_time` — they are instants. |
| `input` | `Any` | `None` | What triggered or fed into this event. |
| `output` | `Any` | `None` | What this event produced or recorded. |
| `level` | `str` | `DEFAULT` | `DEFAULT`, `DEBUG`, `WARNING`, or `ERROR`. |
| `status_message` | `str` | `None` | Detail message, especially for `ERROR` level events. |
| `metadata` | `dict` | `None` | Arbitrary key-value pairs. |

⚠️ Events do not have `start_time` / `end_time` or any LLM-specific fields. If you find yourself wanting to record latency for something, use a `Span` instead.

---

## 6. Session — Conversation Grouping

A **Session** is not an observation type — it has no fields of its own and is never created explicitly. It emerges automatically in the Langfuse UI whenever two or more traces share the same `session_id`.

```
Conversation: "Plan a trip to Tokyo"
│
├── Turn 1 → Trace(id="t1", session_id="sess_abcde", name="chat-turn")
│             └── Generation(name="initial-response")
│
├── Turn 2 → Trace(id="t2", session_id="sess_abcde", name="chat-turn")
│             └── Generation(name="follow-up")
│
└── Turn 3 → Trace(id="t3", session_id="sess_abcde", name="chat-turn")
              └── Generation(name="itinerary-synthesis")
```

```python
import uuid

# Generate once per conversation — persist it across turns (e.g., in your database or JWT)
session_id = str(uuid.uuid4())

def handle_chat_turn(user_message: str, conversation_history: list) -> str:
    trace = langfuse.trace(
        name="chat-turn",
        input={"message": user_message},
        user_id=current_user.id,
        session_id=session_id,  # same value for every turn in this conversation
        metadata={"turn_number": len(conversation_history) + 1},
    )

    with trace.generation(
        name="response-generation",
        model="gpt-4o",
        input=conversation_history + [{"role": "user", "content": user_message}],
    ) as gen:
        response = openai_client.chat.completions.create(...)
        gen.update(
            output=response.choices[0].message.content,
            usage_details={
                "input": response.usage.prompt_tokens,
                "output": response.usage.completion_tokens,
                "total": response.usage.total_tokens,
            },
        )

    trace.update(output={"reply": response.choices[0].message.content})
    return response.choices[0].message.content
```

💡 Session-level scores (e.g., an end-of-conversation satisfaction score) can be attached directly to the `session_id` without referencing a specific trace. See [Scoring System](../evaluation/01_scoring_system.md) for the scoring API.

---

## 7. Score — Evaluation Data Object

A **Score** is not an observation — it is an evaluation data object that attaches to a trace, a specific observation, or a session. Scores are how you record quality signals alongside tracing data.

```python
# Attach a score to a trace after evaluation
langfuse.score(
    trace_id=trace.id,
    name="answer-faithfulness",
    value=0.91,             # numeric: 0.0 to 1.0
    data_type="NUMERIC",
    comment="All claims in the answer are supported by retrieved documents",
)

# Attach a score to a specific generation observation
langfuse.score(
    trace_id=trace.id,
    observation_id=generation.id,
    name="response-quality",
    value="good",           # categorical: one of your defined categories
    data_type="CATEGORICAL",
)

# Attach a boolean pass/fail score
langfuse.score(
    trace_id=trace.id,
    name="safety-check-passed",
    value=True,
    data_type="BOOLEAN",
    comment="No PII or harmful content detected",
)
```

### Score Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Score name — used as the metric identifier across all traces. Use a consistent naming scheme (e.g., `answer-faithfulness`, `latency-ms`). |
| `value` | `float \| str \| bool` | The score value. Type must match `data_type`. |
| `data_type` | `str` | `NUMERIC`, `CATEGORICAL`, `BOOLEAN`, or `TEXT`. Controls how the value is interpreted in analytics. |
| `comment` | `str` | Optional human-readable explanation of the score. Shown in the trace detail view. |
| `trace_id` | `str` | Required — the trace this score belongs to. |
| `observation_id` | `str` | Optional — pins the score to a specific span/generation/event within the trace. |

For the full scoring API including idempotency patterns and session-level scores, see [Scoring System](../evaluation/01_scoring_system.md).

---

## 8. ID Generation and Idempotency

By default, Langfuse generates a **UUID v4** for every trace and observation. You can override this with a custom `id` to enable two critical production patterns:

**Idempotency**: Resubmitting the same trace (e.g., after a retry) with the same `id` updates the existing record instead of creating a duplicate.

**Cross-system correlation**: Use your own request ID (from an HTTP header, a queue message ID, etc.) as the Langfuse `trace_id` so you can look up a trace directly from your application logs.

```python
import hashlib

# Pattern 1: Use the upstream request ID as the trace ID
def handle_request(request: Request) -> Response:
    trace = langfuse.trace(
        id=request.headers.get("X-Request-ID"),   # exact correlation with app logs
        name="api-request",
        input=request.json(),
        user_id=request.user.id,
    )

# Pattern 2: Deterministic ID from content (for batch jobs — ensures no duplicate traces)
def process_document(doc_id: str, pipeline_run_id: str) -> None:
    # Same doc + same run = same trace ID — safe to retry
    trace_id = hashlib.sha256(f"{pipeline_run_id}:{doc_id}".encode()).hexdigest()[:32]
    trace = langfuse.trace(
        id=trace_id,
        name="document-processing",
        input={"doc_id": doc_id},
        metadata={"pipeline_run_id": pipeline_run_id},
    )
```

⚠️ Custom IDs must be globally unique within your Langfuse project. Using the same ID for different logical traces will silently merge them.

---

## 9. Observation Nesting Rules

Langfuse builds the parent-child tree of observations using `parent_observation_id`. In most cases you never set this manually because the SDK manages it through context managers and the `@observe` decorator.

### Context Manager Mode (recommended)

```
trace
 └── retrieval_span      ← created with trace.span(...)
      └── embed_gen       ← created with retrieval_span.generation(...)
```

```python
trace = langfuse.trace(name="rag-pipeline", input={"q": user_question})

with trace.span(name="retrieval") as retrieval_span:
    # Parent is automatically set to trace.id
    embeddings = embed_model.encode(user_question)

    with retrieval_span.generation(name="embed-query", model="text-embedding-3-small") as embed_gen:
        # Parent is automatically set to retrieval_span.id
        embed_gen.update(
            input=user_question,
            output={"embedding_dims": len(embeddings)},
            usage_details={"input": len(user_question.split()), "output": 0, "total": len(user_question.split())},
        )

    docs = vector_store.query(embeddings, top_k=5)
    retrieval_span.update(output={"doc_count": len(docs)})
```

### Manual Mode (for async / cross-function scenarios)

When context managers aren't practical (e.g., you're passing state across async tasks), set `parent_observation_id` explicitly:

```python
trace = langfuse.trace(name="async-pipeline", input=payload)
trace_id = trace.id

async def retrieval_step(trace_id: str) -> tuple[list, str]:
    span = langfuse.span(
        trace_id=trace_id,
        name="async-retrieval",
        input={"query": payload["question"]},
    )
    docs = await vector_store.aquery(payload["question"])
    span.update(output={"count": len(docs)})
    span.end()
    return docs, span.id   # return span.id so children can reference it

async def generation_step(trace_id: str, parent_span_id: str, context: str) -> str:
    gen = langfuse.generation(
        trace_id=trace_id,
        parent_observation_id=parent_span_id,   # explicit parent linkage
        name="answer-generation",
        model="gpt-4o",
        input=[{"role": "user", "content": f"Context: {context}\n\nAnswer the question."}],
    )
    response = await async_openai.chat.completions.create(model="gpt-4o", messages=gen.input)
    gen.update(output=response.choices[0].message.content)
    gen.end()
    return response.choices[0].message.content

docs, span_id = await retrieval_step(trace_id)
answer = await generation_step(trace_id, span_id, "\n\n".join(d.text for d in docs))
trace.update(output={"answer": answer})
```

### The Complete Object Flow

```
┌──────────────────────────────────────────────────────────┐
│  Langfuse Project                                        │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Session (session_id groups traces)                │  │
│  │                                                    │  │
│  │  ┌──────────────────────────────────────────────┐  │  │
│  │  │  Trace  (id, name, user_id, tags, metadata)  │  │  │
│  │  │                                              │  │  │
│  │  │  ┌─────────────────────────────────────┐    │  │  │
│  │  │  │  Span  (start_time → end_time)      │    │  │  │
│  │  │  │                                     │    │  │  │
│  │  │  │  ┌────────────────────────────────┐ │    │  │  │
│  │  │  │  │  Generation  (+ model, usage,  │ │    │  │  │
│  │  │  │  │   cost, completion_start_time) │ │    │  │  │
│  │  │  │  └────────────────────────────────┘ │    │  │  │
│  │  │  └─────────────────────────────────────┘    │  │  │
│  │  │                                              │  │  │
│  │  │  ┌─────────────────────────────────────┐    │  │  │
│  │  │  │  Generation  (direct, no parent)    │    │  │  │
│  │  │  └─────────────────────────────────────┘    │  │  │
│  │  │                                              │  │  │
│  │  │  ┌─────────────────────────────────────┐    │  │  │
│  │  │  │  Event  (timestamp only, no end)    │    │  │  │
│  │  │  └─────────────────────────────────────┘    │  │  │
│  │  │                                              │  │  │
│  │  │  ┌─────────────────────────────────────┐    │  │  │
│  │  │  │  Score  (attaches to trace or obs.) │    │  │  │
│  │  │  └─────────────────────────────────────┘    │  │  │
│  │  └──────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

> **Key insight**: Every observation method (`trace.span()`, `trace.generation()`, `trace.event()`) accepts an `id`, `name`, `input`, `output`, `metadata`, and `level`. The difference between types is what extra fields they support — not how they're created.

💡 If you're unsure whether to use a `Span` or a `Generation` for a particular operation, ask: "Does this make an LLM API call?" If yes, use `Generation`. If no, use `Span`. If it happens instantaneously and you just want to record it, use `Event`.

---

**Next**: [Part 3: Instrumentation](03_instrumentation.md)
