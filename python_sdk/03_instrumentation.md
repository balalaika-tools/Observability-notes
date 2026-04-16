# Instrumentation Patterns: Context Managers, Decorators, and Manual Spans

> **Who this is for**: Python engineers who have read [Part 2: Trace Data Model](02_trace_data_model.md) and are ready to instrument real application code. You should understand what traces, spans, and generations are before reading this.

---

## 1. Overview — Three Patterns

Langfuse gives you three ways to instrument Python code. They differ in how much boilerplate they require, how much control they give you, and whether context propagation (parent-child span relationships) happens automatically.

| Pattern | Best For | Complexity | Context Propagation |
|---------|----------|------------|---------------------|
| Context manager | Precise control, wrapping existing code | Medium | Automatic |
| `@observe` decorator | Function-level tracing, clean APIs | Low | Automatic |
| Manual creation | Maximum control, complex flows | High | Manual |

The right choice depends on your code's shape:

```
Is your unit of work a function?
       │
       ├─ Yes ──► @observe decorator  (least boilerplate)
       │
       └─ No ──► Does it have clear start/end scope within a block?
                       │
                       ├─ Yes ──► context manager  (explicit boundary)
                       │
                       └─ No ──► manual creation   (background jobs,
                                                    event-driven systems)
```

> **Key insight**: `@observe` and context managers both propagate parent-child relationships automatically via Python's contextvars. Manual creation requires you to thread the parent reference explicitly.

---

## 2. Context Manager Pattern

**`start_as_current_observation()`** is the core context manager method. It creates an observation (span, generation, or event), registers it as the current active observation in the thread-local context, and automatically closes it when the `with` block exits — even on exceptions.

```python
from langfuse import get_client

# get_client() returns the singleton initialized via environment variables:
#   LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST
langfuse = get_client()


def process_user_query(user_id: str, query: str) -> str:
    # start_as_current_observation creates the span and sets it as current context.
    # Any nested observations opened inside this block become children automatically.
    with langfuse.start_as_current_observation(
        as_type="span",
        name="process-user-query",
    ) as span:

        # update_current_trace() reaches up to the root trace from anywhere in the stack.
        # Call it as early as possible so metadata is attached even if the trace errors.
        langfuse.update_current_trace(
            user_id=user_id,
            session_id=f"session-{user_id}",
            tags=["production", "rag"],
        )

        # Retrieval step — nested span.
        # Because this opens inside the outer `with`, it becomes a child of span.
        with langfuse.start_as_current_observation(
            as_type="span",
            name="vector-retrieval",
        ) as retrieval_span:
            docs = retrieve_documents(query)
            retrieval_span.update(
                input={"query": query},
                output={"num_docs": len(docs)},
            )

        # LLM call — nested generation.
        # as_type="generation" enables token cost tracking in Langfuse.
        with langfuse.start_as_current_observation(
            as_type="generation",
            name="llm-response",
            model="gpt-4o",
            model_parameters={"temperature": 0.7, "max_tokens": 500},
        ) as generation:
            response = call_llm(query, docs)
            generation.update(
                input={"messages": [{"role": "user", "content": query}]},
                output=response.content,
                usage_details={
                    "input": response.usage.prompt_tokens,
                    "output": response.usage.completion_tokens,
                },
            )

        span.update(output=response.content)
        return response.content
```

**Trace shape produced:**

```
Trace: process-user-query
├── Span: process-user-query          ← outer with block
│   ├── Span: vector-retrieval        ← inner with block (child)
│   └── Generation: llm-response      ← inner with block (child)
```

### Key methods on observation objects

| Method | Purpose |
|--------|---------|
| `.update(**kwargs)` | Add or overwrite fields after the observation was opened. Use this to attach `input`/`output` after the work is done rather than before. |
| `.score(name, value, comment=None)` | Attach a numeric score to this specific observation (e.g., retrieval precision, latency SLA). |

### `update_current_trace()`

This reaches up through any nesting depth and updates the root trace. Fields it accepts: `name`, `user_id`, `session_id`, `version`, `release`, `tags`, `metadata`, `input`, `output`, `public`.

💡 Call `update_current_trace()` at the start of the outermost block so trace metadata is written even if the function throws later.

---

## 3. `@observe` Decorator Pattern

**`@observe`** is the cleanest option when your work is already shaped as functions. It wraps the function body in a span automatically — no `with` statement needed. Nested decorated functions become child spans by reading the active context from Python's `contextvars`.

```python
from langfuse.decorators import observe, langfuse_context
import openai


# as_type="span" makes this a regular span (default if omitted).
# name= overrides the default, which would be the function name.
@observe(name="retrieve-documents", as_type="span")
def retrieve_documents(query: str) -> list[str]:
    docs = vector_db.search(query, top_k=5)

    # langfuse_context.update_current_observation() adds fields to the span
    # wrapping the current function. It's the decorator equivalent of span.update().
    langfuse_context.update_current_observation(
        input={"query": query},
        output={"docs": [d.page_content for d in docs]},
    )
    return docs


# as_type="generation" enables cost/token tracking.
@observe(name="generate-response", as_type="generation")
def generate_response(query: str, context: list[str]) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Answer using only the provided context."},
            {"role": "user", "content": query},
        ],
    )

    # Attach model metadata after the call so token counts are available.
    langfuse_context.update_current_observation(
        model="gpt-4o",
        input={"messages": [{"role": "user", "content": query}], "context": context},
        output=response.choices[0].message.content,
        usage_details={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        },
    )
    return response.choices[0].message.content


# The outermost decorated function becomes the root span.
# Calling retrieve_documents() and generate_response() from here
# automatically nests them as children — no manual parenting needed.
@observe(name="rag-pipeline")
def rag_pipeline(user_id: str, query: str) -> str:
    # update_current_trace() works identically from decorator context.
    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id="session-abc123",
        tags=["production", "rag"],
    )

    docs = retrieve_documents(query)          # auto-nested child span
    return generate_response(query, docs)     # auto-nested child generation
```

**Trace shape produced:**

```
Trace: rag-pipeline
└── Span: rag-pipeline                ← outermost @observe
    ├── Span: retrieve-documents      ← called from within rag-pipeline
    └── Generation: generate-response ← called from within rag-pipeline
```

### `langfuse_context` reference

**`langfuse_context`** is a thread-local (actually `contextvars`-local) object that exposes the active observation. Two primary methods:

| Method | Equivalent to |
|--------|--------------|
| `langfuse_context.update_current_observation(**kwargs)` | `span.update(**kwargs)` |
| `langfuse_context.update_current_trace(**kwargs)` | `langfuse.update_current_trace(**kwargs)` |

### `@observe` decorator parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | function name | Observation name in Langfuse UI |
| `as_type` | `"span"` \| `"generation"` \| `"event"` | `"span"` | Observation type |
| `capture_input` | `bool` | `True` | Whether to record function arguments |
| `capture_output` | `bool` | `True` | Whether to record the return value |

> **Key insight**: `capture_input` and `capture_output` default to `True`, which means all function arguments and return values are sent to Langfuse automatically. This is convenient for debugging but requires opt-out for any function handling PII. See Section 6 for the privacy pattern.

---

## 4. Manual Observation Creation

Use manual creation when your work doesn't fit neatly into a function scope or a `with` block — background workers, message queue consumers, async task runners, or multi-step jobs where the start and end are in different call frames.

```python
from langfuse import get_client
from typing import Any

langfuse = get_client()


def run_background_classification_job(job_id: str, records: list[dict[str, Any]]) -> None:
    # trace() creates a root trace explicitly. No context manager, no decorator.
    trace = langfuse.trace(
        name="background-classification-job",
        user_id="system",
        metadata={"job_id": job_id, "record_count": len(records)},
    )

    # span() creates a child span attached to the trace.
    # Parent-child relationship is set explicitly via method chaining (trace.span).
    processing_span = trace.span(
        name="data-processing",
        input={"records": len(records)},
    )

    batches = [records[i:i + 100] for i in range(0, len(records), 100)]
    all_labels: list[str] = []

    for batch_num, batch in enumerate(batches):
        # generation() creates a child generation attached to processing_span.
        # Note: span.generation() — parent is the span, not the trace.
        batch_generation = processing_span.generation(
            name=f"classify-batch-{batch_num}",
            model="gpt-4o-mini",
            input={"batch_size": len(batch), "batch_num": batch_num},
        )

        labels = classify_batch_with_llm(batch)
        all_labels.extend(labels)

        # Manual observations require explicit .end() — the span is NOT closed
        # automatically. Forgetting .end() leaves the duration as null in the UI.
        batch_generation.end(
            output={"labels": labels, "label_count": len(labels)},
            usage_details={
                "input": len(batch) * 50,   # estimated prompt tokens
                "output": len(batch) * 5,   # estimated completion tokens
            },
        )

    processing_span.end(
        output={"total_processed": len(records), "total_labels": len(all_labels)},
    )

    # trace itself can also be ended explicitly to set a final output.
    trace.update(output={"status": "complete", "job_id": job_id})
```

**Trace shape produced:**

```
Trace: background-classification-job
└── Span: data-processing
    ├── Generation: classify-batch-0
    ├── Generation: classify-batch-1
    └── Generation: classify-batch-N
```

⚠️ **Manual creation requires explicit `.end()` calls.** Context managers call `.end()` automatically on `__exit__`. With manual creation, forgetting `.end()` means:
- The observation's `endTime` is never set
- Duration shows as `null` in the Langfuse UI
- Cost calculations for generations may be incorrect

⚠️ **Context propagation is your responsibility.** `trace.span()` and `span.generation()` work because you hold the parent reference. If you need cross-thread or cross-process propagation, you must pass trace IDs manually. See [Part 4: Advanced Patterns](04_advanced_patterns.md) for distributed tracing.

---

## 5. Updating Trace Fields from Deep Nesting

Both `langfuse.update_current_trace()` (context manager style) and `langfuse_context.update_current_trace()` (decorator style) walk up the context stack to find the root trace, regardless of nesting depth.

This is useful when you only know certain trace-level fields (like `user_id` or error tags) deep in the call stack:

```python
from langfuse.decorators import observe, langfuse_context
import logging

logger = logging.getLogger(__name__)


@observe(name="validate-user-permissions")
def validate_permissions(user_id: str, resource: str) -> bool:
    has_permission = check_acl(user_id, resource)

    if not has_permission:
        # Tag the root trace from 3 levels deep in the call stack.
        # Langfuse walks up the contextvars stack to find the trace root.
        langfuse_context.update_current_trace(
            tags=["permission-denied"],
            metadata={"denied_resource": resource},
        )
        logger.warning("Permission denied: user=%s resource=%s", user_id, resource)

    return has_permission


@observe(name="fetch-document")
def fetch_document(user_id: str, doc_id: str) -> dict:
    if not validate_permissions(user_id, doc_id):
        raise PermissionError(f"User {user_id} cannot access {doc_id}")
    return document_store.get(doc_id)


@observe(name="handle-document-request")
def handle_document_request(user_id: str, doc_id: str) -> dict:
    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id=f"session-{user_id}",
    )
    return fetch_document(user_id, doc_id)
```

> **Key insight**: `update_current_trace()` is safe to call from utility functions, validators, and helpers — they don't need to know they're being observed. This keeps instrumentation out of business logic.

---

## 6. Privacy Control — `capture_input` / `capture_output`

By default, `@observe` records all function arguments as `input` and the return value as `output`. For functions that handle sensitive data, opt out explicitly:

```python
from langfuse.decorators import observe, langfuse_context


# capture_input=False: function args (ssn, dob) are NOT sent to Langfuse.
# capture_output=False: return value is NOT sent to Langfuse.
# The span still appears in the trace — it just has no input/output fields.
@observe(name="process-pii-record", capture_input=False, capture_output=False)
def handle_pii_data(ssn: str, dob: str, user_id: str) -> str:
    result = run_compliance_check(ssn, dob)

    # You can still attach non-sensitive metadata manually.
    langfuse_context.update_current_observation(
        metadata={
            "user_id": user_id,           # safe to log
            "check_passed": result.passed, # safe to log
            # ssn and dob are intentionally omitted
        },
        output={"status": "checked", "passed": result.passed},  # safe summary only
    )
    return result.recommendation


@observe(name="compliance-pipeline")
def compliance_pipeline(user_id: str, ssn: str, dob: str) -> str:
    langfuse_context.update_current_trace(user_id=user_id)
    # handle_pii_data call is visible in the trace, but its arguments are not.
    return handle_pii_data(ssn, dob, user_id)
```

**What each flag controls:**

| Flag | Default | When `False` |
|------|---------|-------------|
| `capture_input` | `True` | Function args not sent. `langfuse_context.update_current_observation(input=...)` still works. |
| `capture_output` | `True` | Return value not sent. `langfuse_context.update_current_observation(output=...)` still works. |

💡 These flags only affect the automatic capture. You can always call `update_current_observation(input=..., output=...)` manually to send a sanitized version of the data.

---

## 7. Choosing the Right Pattern

```
┌────────────────────────────────────────────────────────────┐
│                   Pattern Decision Guide                   │
├────────────────────────┬───────────────────────────────────┤
│ Situation              │ Pattern                           │
├────────────────────────┼───────────────────────────────────┤
│ Function-based code    │ @observe  (lowest boilerplate)    │
│ Clean public APIs      │ @observe                          │
│ Span boundary ≠ fn     │ context manager                   │
│ Wrapping third-party   │ context manager                   │
│ Background workers     │ manual creation                   │
│ Message queue consumer │ manual creation                   │
│ Multi-thread/process   │ manual creation + ID passing      │
└────────────────────────┴───────────────────────────────────┘
```

**Decision checklist:**

✅ Use `@observe` when your code is function-based and you want clean tracing with minimal boilerplate — it's the right default for most application code.

✅ Use context managers when you need precise control over span boundaries within a function body, or when instrumenting code you can't modify (third-party calls, library wrappers).

✅ Use manual creation for background jobs, event-driven systems, or non-function-based flows where the span lifecycle crosses call-frame or thread boundaries.

❌ Don't mix `@observe` and manual creation for the same logical operation. If `rag_pipeline()` is decorated with `@observe`, don't also call `langfuse.trace()` inside it — you'll create two separate traces instead of one nested tree.

❌ Don't use manual creation as the default just because it "feels more explicit". The automatic context propagation in `@observe` and context managers is a feature, not a leaky abstraction.

⚠️ If you use manual creation inside an `@observe`-decorated function, the manually created trace will be a root trace — not a child of the decorator's trace. This is almost never what you want.

---

## 8. Pattern Flow Comparison

```
@observe (decorator)                Context manager               Manual creation
─────────────────────               ────────────────              ───────────────

@observe("pipeline")                with langfuse                 trace = langfuse.trace(
def pipeline():                         .start_as_current_            name="pipeline",
    # span opens here                    observation(             )
                                         as_type="span",
    @observe("step-a")                   name="pipeline",        span = trace.span(
    def step_a():           vs       ) as span:                       name="step-a",
        # child span                     # span open             )
                                                                  # ... work ...
    step_a()                            with langfuse            span.end()
                                            .start_as_current_
    # span closes here                       observation(
                                             as_type="span",
                                             name="step-a",
                                         ) as child:
                                             pass   # closes here
```

> **Key insight**: All three patterns produce identical trace structures in Langfuse. The choice is about code ergonomics, not output shape.

---

**Prerequisites**: [Part 2: Trace Data Model](02_trace_data_model.md) — understand traces, spans, and generations before instrumenting.

**See also**: [Part 5: Framework Integrations](05_framework_integrations.md) — auto-instrumentation for OpenAI, LangChain, and other frameworks that removes the need to instrument LLM calls manually.

**Next**: [Part 4: Advanced Patterns](04_advanced_patterns.md)
