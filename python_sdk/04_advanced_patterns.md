# Advanced Production Patterns — Langfuse Python SDK

> **Who this is for**: Engineers moving a Langfuse-instrumented service from development to production. Assumes familiarity with the `@observe` decorator and the trace data model — read [Part 3: Instrumentation](03_instrumentation.md) first. Working knowledge of Python async, threading, and OpenTelemetry concepts helps but is not required.

---

## 1. Async Support

The `@observe` decorator is **fully async-compatible** — it works identically on both sync and async functions. No separate async variant is needed. Under the hood, Langfuse uses OpenTelemetry's **async context variables** (`contextvars.ContextVar`), which propagate correctly across `await` boundaries without any manual wiring.

```python
import asyncio
from langfuse.decorators import observe
from langfuse import get_client

langfuse = get_client()

# Async generation — behaves identically to the sync version.
# The span is opened when the coroutine starts and closed when it returns.
@observe(as_type="generation")
async def async_llm_call(prompt: str) -> str:
    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


@observe(name="async-rag")
async def async_rag_pipeline(query: str) -> str:
    # asyncio.gather launches both coroutines concurrently.
    # Both spans are recorded as children of "async-rag" in the trace tree —
    # OTel's ContextVar propagation handles the parent-child linking automatically.
    docs_a, docs_b = await asyncio.gather(
        fetch_from_source_a(query),
        fetch_from_source_b(query),
    )
    combined_context = f"{query}\n\nSource A:\n{docs_a}\n\nSource B:\n{docs_b}"
    return await async_llm_call(combined_context)


asyncio.run(async_rag_pipeline("What is RAG?"))
langfuse.flush()
```

**Trace structure produced by the pipeline above:**

```
async-rag  [trace root]
├── fetch_from_source_a   [span, runs concurrently]
├── fetch_from_source_b   [span, runs concurrently]
└── async_llm_call        [generation, runs after gather]
```

> **Key insight**: `asyncio.gather` runs coroutines concurrently within the same event loop thread. Because OTel context is stored per-coroutine via `contextvars`, each gathered task correctly carries the parent span ID. You get accurate parallel spans in the trace without any extra instrumentation code.

**One caveat — `asyncio.create_task` with context capture:**

```python
@observe(name="pipeline")
async def pipeline(query: str) -> str:
    # ✅ Correct: context is captured at task creation time
    task = asyncio.create_task(fetch_documents(query))
    result = await task
    return result

# ❌ Anti-pattern: starting tasks outside an @observe scope
# means the task has no parent span to attach to
async def bad_pattern():
    task = asyncio.create_task(fetch_documents("query"))  # no active trace here
    await task
```

💡 If you need to fan out work to a thread pool from an async context, see Section 4 — the rules change when real OS threads are involved.

---

## 2. Sampling

**Sampling** controls what fraction of traces are exported to Langfuse. At high traffic volumes, exporting every trace is expensive and often unnecessary — sampling lets you maintain statistical visibility at a fraction of the cost.

### Configuration

```python
from langfuse import Langfuse

# Constructor argument — takes precedence over env var
langfuse = Langfuse(sample_rate=0.1)  # export 10% of traces

# Or via environment variable (preferred for deployed services)
# LANGFUSE_SAMPLE_RATE=0.1
```

Sampling is **trace-level and binary**: when a trace is sampled out, every span, generation, and score belonging to that trace is dropped before transmission. There is no partial-trace sampling.

```
Incoming request
       │
       ▼
 ┌─────────────────┐
 │  OTel Sampler   │  ← evaluates sample_rate once per root span
 └────────┬────────┘
          │
    ┌─────┴─────┐
    │           │
  SAMPLED    DROPPED
    │           │
    ▼           ▼
  Export    Discard
  (all      (root +
  children)  all children)
```

The sampling decision uses OTel's native **TraceIdRatioBased** sampler, which means the decision is consistent: all spans in a trace always share the same fate regardless of when child spans are created.

### Dynamic sampling by tier (workaround pattern)

Sampling rate is configured at SDK init — it cannot be changed per-request after initialization. For tier-based or priority-based selective tracing, use tags combined with Langfuse's server-side filters:

```python
from langfuse.decorators import observe, langfuse_context

@observe()
def handle_request(user_tier: str, query: str) -> str:
    # Tag the trace at entry — all child spans inherit this tag.
    # In the Langfuse dashboard you can then filter/alert by tier.
    langfuse_context.update_current_trace(
        tags=[user_tier, "production"],
        metadata={"user_tier": user_tier},
    )
    return run_llm_pipeline(query)
```

⚠️ Sampling is configured at SDK init, not per-trace. For workloads where you need 100% sampling for `enterprise` users and 5% for `free` users, the correct approach is to run two SDK instances (different `sample_rate` values) and route requests to the appropriate instance, or tag everything at 1.0 and apply cost control via Langfuse's dataset sampling features post-hoc.

### Sampling rate guidelines

| Traffic (requests/hour) | Recommended `sample_rate` | Notes |
|------------------------|--------------------------|-------|
| < 1,000 | `1.0` | Capture everything during early growth |
| 1,000 – 10,000 | `0.2 – 0.5` | Balance visibility with cost |
| 10,000 – 100,000 | `0.05 – 0.1` | Statistical samples sufficient for monitoring |
| > 100,000 | `0.01 – 0.05` | Sample up for specific user cohorts via tags |

---

## 3. Data Masking and PII Redaction

The **`mask` parameter** accepts a callable that is invoked on all trace data — inputs, outputs, and metadata — before the data is serialized and transmitted to Langfuse. This is the correct place to redact PII: it runs client-side, so sensitive data never leaves your infrastructure.

The mask function receives `str`, `dict`, or `list` values and must return the same type. The SDK calls it recursively for nested structures, so a flat implementation handles arbitrarily deep payloads.

```python
import re
from langfuse import Langfuse

def mask_pii(data: dict | list | str) -> dict | list | str:
    """Redact email addresses and US phone numbers before sending to Langfuse.

    Called by the SDK on every value before serialization. Must be a pure
    function — no side effects, no mutation of the input object.
    """
    if isinstance(data, str):
        # Redact email addresses
        data = re.sub(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            '[EMAIL]',
            data,
        )
        # Redact US-style phone numbers: 555-867-5309, 555.867.5309, 5558675309
        data = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', data)
        return data
    elif isinstance(data, dict):
        return {k: mask_pii(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [mask_pii(item) for item in data]
    # Passthrough for int, float, bool, None — no PII risk
    return data


langfuse = Langfuse(mask=mask_pii)
```

**What the mask function receives (and when):**

```
Your code runs an @observe function
          │
          ▼
   Function returns
          │
          ▼
   SDK captures input/output
          │
          ▼
   mask_pii(input) called    ← your function runs here
   mask_pii(output) called   ← and here
          │
          ▼
   Redacted data serialized to OTLP
          │
          ▼
   Langfuse ingest API       ← PII never reaches this point
```

### Selective capture suppression

For functions that handle data too sensitive to log even in redacted form, suppress capture entirely at the decorator level:

```python
from langfuse.decorators import observe

# input and output are never captured — only the span timing and name are recorded
@observe(as_type="span", capture_input=False, capture_output=False)
def process_payment_details(card_number: str, cvv: str) -> dict:
    return charge_card(card_number, cvv)
```

| Approach | What's sent to Langfuse | Use when |
|----------|------------------------|----------|
| `mask=mask_pii` | Redacted inputs/outputs | Most cases — you want observability without raw PII |
| `capture_input=False` | No inputs at all | Input is structurally sensitive (card numbers, SSNs) |
| `capture_output=False` | No outputs at all | Output contains derived PII (personalised summaries) |
| Both `capture_*=False` | Only span name + timing | Audit trail only — full blackout |

> **Key insight**: The `mask` function applies globally to all traces. `capture_input=False` / `capture_output=False` apply per-function. Use the global mask as the baseline and per-function suppression for the highest-sensitivity callsites.

---

## 4. Multi-threading Context Propagation

OTel context is stored in Python's `contextvars.ContextVar`, which is **not automatically copied to new OS threads**. A span created in the main thread will not appear as a parent in spans created inside `ThreadPoolExecutor` or `threading.Thread` unless you explicitly propagate context.

The correct fix is `ThreadingInstrumentor`, which patches `Thread` and `ThreadPoolExecutor` to copy the active OTel context at thread creation time:

```python
from opentelemetry.instrumentation.threading import ThreadingInstrumentor
from concurrent.futures import ThreadPoolExecutor
from langfuse.decorators import observe

# Instrument once at application startup — not inside request handlers.
# This patches threading.Thread and ThreadPoolExecutor globally.
ThreadingInstrumentor().instrument()


@observe(name="parallel-processing")
def process_batch(items: list[str]) -> list[str]:
    """Process items concurrently. Each item's span is a child of this span."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        # ThreadingInstrumentor ensures the OTel context from this thread
        # (where "parallel-processing" span is active) is copied into each
        # worker thread before process_item runs.
        futures = [executor.submit(process_item, item) for item in items]
        return [f.result() for f in futures]


@observe(as_type="span")
def process_item(item: str) -> str:
    # This span is automatically linked to the "parallel-processing" parent
    # because ThreadingInstrumentor copied the context from the submitting thread.
    return transform(item)
```

**Trace structure with `ThreadingInstrumentor`:**

```
parallel-processing  [trace root, main thread]
├── process_item("item-0")  [span, worker thread 1]
├── process_item("item-1")  [span, worker thread 2]
├── process_item("item-2")  [span, worker thread 3]
└── process_item("item-3")  [span, worker thread 4]
```

**Without `ThreadingInstrumentor`:**

```
parallel-processing  [trace root, main thread]
                     ← no children, worker spans are orphaned

[orphaned span]  process_item("item-0")  [no parent trace]
[orphaned span]  process_item("item-1")  [no parent trace]
```

### `multiprocessing` — a different problem

`ThreadingInstrumentor` only handles threads within the same process. For `multiprocessing.Pool` or subprocess-based parallelism, context crosses a **process boundary** — OTel context variables do not survive `fork()` or `spawn()`.

The solution is to manually extract and inject **W3C TraceContext headers**:

```python
from opentelemetry import propagate, context
from opentelemetry.propagators.textmap import DefaultGetter
from multiprocessing import Pool
from langfuse.decorators import observe

def worker_with_context(args: tuple) -> str:
    """Runs in a subprocess. Reconstructs OTel context from injected headers."""
    item, carrier = args

    # Restore the parent context from the serialized headers passed by the caller
    ctx = propagate.extract(carrier)
    token = context.attach(ctx)
    try:
        return process_item(item)
    finally:
        context.detach(token)


@observe(name="multiprocess-batch")
def process_batch_mp(items: list[str]) -> list[str]:
    # Extract current context as a dict of W3C TraceContext headers
    carrier: dict = {}
    propagate.inject(carrier)  # populates 'traceparent', 'tracestate' keys

    # Pass carrier alongside each item so each worker can reconstruct context
    with Pool(processes=4) as pool:
        return pool.map(worker_with_context, [(item, carrier) for item in items])
```

⚠️ Multiprocessing context propagation is manual and fragile. For most LLM workloads, `ThreadPoolExecutor` with `ThreadingInstrumentor` is simpler and sufficient. Reserve multiprocessing for CPU-bound pre/post-processing steps, not LLM calls.

---

## 5. Span Filtering

**Span filtering** controls which spans are exported after sampling has already decided a trace is included. This is useful for reducing noise from infrastructure-level spans (health checks, DB connection pings) that inflate trace counts without providing LLM observability value.

### Default behavior

By default, Langfuse applies a built-in filter that passes through spans marked as LLM-related (generations, retrieval steps, chain steps) and drops generic HTTP/DB instrumentation spans from auto-instrumented libraries. This default is correct for most setups.

### Custom `should_export_span` callback

For fine-grained control, pass a `should_export_span` callback at init time:

```python
from langfuse import Langfuse
from langfuse.client import LangfuseSpanAttributes

def custom_span_filter(span_attributes: LangfuseSpanAttributes) -> bool:
    """Export all generation spans, plus any span that took longer than 100ms.

    This keeps every LLM call and surfaces slow operations worth investigating,
    while discarding cheap, fast utility spans that add volume without signal.
    """
    # Always export LLM generations — these are the core observability signal
    if span_attributes.span_type == "generation":
        return True

    # Export any span that took over 100ms — likely worth investigating
    duration_ms = span_attributes.duration_ms
    return duration_ms is not None and duration_ms > 100


langfuse = Langfuse(should_export_span=custom_span_filter)
```

**`LangfuseSpanAttributes` fields available in the callback:**

| Field | Type | Description |
|-------|------|-------------|
| `span_type` | `str \| None` | `"generation"`, `"span"`, `"event"`, or `None` |
| `span_name` | `str \| None` | Name set via `@observe(name=...)` or inferred |
| `duration_ms` | `float \| None` | Wall-clock duration of the span in milliseconds |
| `level` | `str \| None` | `"DEFAULT"`, `"DEBUG"`, `"WARNING"`, `"ERROR"` |
| `model` | `str \| None` | Model name for generation spans |

### Composing with the default filter

Replacing the default filter entirely can accidentally re-include infrastructure noise. To extend rather than replace, compose with `is_default_export_span`:

```python
from langfuse import Langfuse
from langfuse.client import LangfuseSpanAttributes, is_default_export_span

def extended_span_filter(span_attributes: LangfuseSpanAttributes) -> bool:
    """Apply default filter, then additionally export all ERROR-level spans."""
    if is_default_export_span(span_attributes):
        return True
    # Also export error spans even if they'd normally be filtered out —
    # you always want to see failures regardless of noise rules
    return span_attributes.level == "ERROR"


langfuse = Langfuse(should_export_span=extended_span_filter)
```

> **Key insight**: Sampling (Section 2) and span filtering operate at different levels. Sampling is a binary decision on the entire trace made at trace root creation. Span filtering is applied span-by-span after sampling has already committed. Use sampling for volume control, filtering for signal quality.

---

## 6. Flushing Strategies

The SDK ships spans to Langfuse via a **background export thread** that batches and transmits asynchronously. For long-running services this is invisible — spans drain continuously. For short-lived processes, the process exits before the buffer is flushed, and traces are silently lost.

**`flush()` blocks until the export buffer is empty** or the configured timeout is reached.

```python
# ── Script / batch job ──────────────────────────────────────────────────────
# Always wrap in try/finally so flush runs even when exceptions occur.

from langfuse import get_client
from langfuse.decorators import observe

langfuse = get_client()

@observe()
def run_nightly_eval(dataset_id: str) -> dict:
    ...

def main():
    try:
        results = run_nightly_eval("eval-2026-04")
        save_results(results)
    finally:
        langfuse.flush()  # guarantees export before process exit

if __name__ == "__main__":
    main()
```

```python
# ── FastAPI — flush on application shutdown ──────────────────────────────────
# Use the lifespan context manager (preferred over @app.on_event in FastAPI 0.93+).

from contextlib import asynccontextmanager
from fastapi import FastAPI
from langfuse import get_client

langfuse = get_client()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # application runs here
    langfuse.flush()  # called during graceful shutdown — drains the buffer

app = FastAPI(lifespan=lifespan)
```

```python
# ── AWS Lambda ───────────────────────────────────────────────────────────────
# Lambda freezes the execution environment immediately after the handler returns.
# The background export thread is suspended mid-flight. Flush synchronously
# before returning so all spans are transmitted during this invocation's billing window.

from langfuse import get_client
from langfuse.decorators import observe

langfuse = get_client()

@observe()
def process_event(event: dict) -> dict:
    ...

def lambda_handler(event, context):
    result = process_event(event)
    langfuse.flush()  # must complete before return — Lambda freezes after this line
    return result
```

⚠️ Forgetting `flush()` in short-lived processes is the single most common cause of missing traces. The SDK does not warn you — spans simply disappear. Add `flush()` to every script, job, and Lambda handler as a non-negotiable hygiene rule.

**When flush is and is not required:**

| Runtime | Flush required? | Reason |
|---------|----------------|--------|
| CLI script (one-shot) | ✅ Yes | Process exits immediately after `main()` |
| AWS Lambda | ✅ Yes | Execution environment freezes after handler returns |
| Batch job / cron | ✅ Yes | Same as CLI — short-lived process |
| FastAPI / Django | ✅ On shutdown | Use `lifespan` hook for graceful drain |
| Celery worker (long-running) | ❌ Not per-task | Worker process lives across tasks |
| Celery beat task (short-lived) | ✅ Yes | Task process exits after completion |

---

## 7. Error Handling in Instrumentation

The Langfuse SDK is designed to be **non-crashing**: all export errors, serialization failures, and network timeouts are caught internally and written to the Python logger at `WARNING` or `ERROR` level. They never propagate as exceptions into your application.

This means instrumentation failures are silent by default. To surface them during development, enable debug logging:

```python
import logging
logging.getLogger("langfuse").setLevel(logging.DEBUG)
```

### Recording exceptions in spans

When your application code raises an exception, the default `@observe` behaviour closes the span without recording the error. To capture exception details explicitly:

```python
from langfuse import get_client

langfuse = get_client()

# Use the context-manager form when you need to update span state mid-execution
with langfuse.start_as_current_observation(as_type="span", name="risky-op") as span:
    try:
        result = risky_operation()
    except Exception as e:
        span.update(
            level="ERROR",
            status_message=str(e),
            output={"error": type(e).__name__, "detail": str(e)},
        )
        raise  # re-raise so the caller sees the exception — don't swallow it
```

For `@observe`-decorated functions, the same pattern applies but you access the current span via `langfuse_context`:

```python
from langfuse.decorators import observe, langfuse_context

@observe(as_type="span")
def fetch_with_retry(url: str, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return http_get(url)
        except Exception as e:
            last_error = e
            langfuse_context.update_current_observation(
                metadata={"failed_attempt": attempt + 1, "error": str(e)},
            )

    # All retries exhausted — record final failure on the span
    langfuse_context.update_current_observation(
        level="ERROR",
        status_message=f"All {retries} attempts failed: {last_error}",
    )
    raise last_error
```

> **Key insight**: Never catch exceptions inside an `@observe` function just to prevent error recording — you lose the stack trace and the span silently succeeds. Always re-raise after recording. If you need to return a fallback value, record the error on the span first, then return the fallback.

---

## 8. Production Configuration Checklist

A consolidated reference for the configuration decisions that matter when moving from development to production:

| Setting | Development | Production | Notes |
|---------|-------------|------------|-------|
| `sample_rate` | `1.0` | `0.05 – 0.2` | Tune based on traffic volume; see Section 2 |
| `debug` | `True` | `False` | Debug mode adds latency and log volume |
| `mask` | `None` | PII redactor | Required for GDPR/CCPA compliance |
| `flush()` | Optional | Required (scripts, Lambda) | See Section 6 |
| `release` | Optional | Required | Set to git SHA at deploy for regression detection |
| `threads` | `1` | `2 – 4` | Increase under high trace volume |
| `capture_input` / `capture_output` | `True` | Varies | Disable per-function for high-sensitivity callsites |
| `should_export_span` | default | custom filter | Add noise reduction for busy services |

**Minimal production init:**

```python
import os
from langfuse import Langfuse
from your_app.masking import mask_pii  # your PII redactor

langfuse = Langfuse(
    # Credentials from environment — never hardcode
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    # Version tag — set at deploy time to git SHA or semver
    release=os.environ.get("GIT_SHA", "unknown"),
    # Capture 10% of traces — adjust based on your traffic
    sample_rate=float(os.environ.get("LANGFUSE_SAMPLE_RATE", "0.1")),
    # PII redactor — runs before any data leaves the process
    mask=mask_pii,
    # Never verbose in production
    debug=False,
)
```

---

**Next**: [Part 5: Framework Integrations](05_framework_integrations.md)
