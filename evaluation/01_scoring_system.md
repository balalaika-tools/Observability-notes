# Langfuse Scoring System: Attaching Evaluation Results to Traces

> **Who this is for**: Engineers building evaluation pipelines, implementing user feedback mechanisms, or wiring automated quality checks into production LLM systems.

---

## 1. What a Score Is

---

A **score** is Langfuse's universal data object for recording evaluation results. Every score ties a measurement to a specific piece of your LLM pipeline — a full interaction, a single generation, or a multi-turn session.

Every score has four required fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Metric identifier, e.g. `"hallucination_check"`, `"overall_quality"` |
| `value` | float or string | The measurement — depends on `data_type` |
| `data_type` | enum | `NUMERIC`, `CATEGORICAL`, `BOOLEAN`, or `TEXT` |
| attachment | `trace_id`, `observation_id`, or `session_id` | Where this score attaches |

**Score sources** fall into four categories:

- **LLM judges** — automated scoring by a separate LLM evaluating the output
- **Human annotation** — reviewers scoring traces in the Langfuse UI or via API
- **SDK/API** — programmatic scores emitted during or after pipeline execution
- **User feedback** — thumbs up/down or ratings captured from your product UI

> **Key insight**: Scores are decoupled from execution. You can score a trace seconds after it completes, hours later during batch evaluation, or weeks later during a human annotation campaign — all using the same API.

---

## 2. Four Data Types

---

Choose the `data_type` that matches what you are measuring:

| Type | Value | Use Case |
|------|-------|----------|
| `NUMERIC` | Float | Quality scores (0.0–1.0), latency, BLEU score, similarity |
| `CATEGORICAL` | String | `"good"`, `"bad"`, `"neutral"` — predefined categories |
| `BOOLEAN` | `0` or `1` | Pass/fail gates, hallucination yes/no, safety checks |
| `TEXT` | String (1–500 chars) | Qualitative feedback, explanations, reviewer notes |

> **Key insight**: `BOOLEAN` scores use `0` and `1` as numeric values — not Python booleans. This ensures consistent storage and aggregation across the platform.

💡 **Tip**: Use `NUMERIC` for anything you want to average or trend over time. Use `CATEGORICAL` when you need discrete buckets that show up cleanly in dashboards (e.g., a multi-class classifier output).

⚠️ `TEXT` scores are not aggregatable — they appear in the UI as raw strings. Reserve them for qualitative reviewer notes, not primary metrics.

---

## 3. Three Attachment Levels

---

Every score attaches to exactly one level of your observability hierarchy:

**Trace-level** — overall quality of the full LLM interaction.

Pass `trace_id` only. Use this for end-to-end quality metrics: "Did this pipeline produce a correct answer?"

**Observation-level** — quality of a specific span or generation within a trace.

Pass both `trace_id` and `observation_id`. Use this when you want to isolate which step introduced an error — e.g., scoring only the retrieval step or only the final generation call.

**Session-level** — quality across a multi-turn conversation.

Pass `session_id` only (no `trace_id`). Use this for user satisfaction scores that span multiple turns — the score aggregates over the whole session rather than a single exchange.

> **Key insight**: Observation-level scores are the most surgical. If you have a RAG pipeline with retrieval, reranking, and generation as separate spans, you can score each step independently and pinpoint exactly where quality degrades.

---

## 4. Python SDK — Scoring Methods

---

There are three patterns for creating scores from Python. All three ultimately call the same Langfuse API — the difference is ergonomics and where in your code the scoring happens.

### Method 1: `langfuse.create_score()` — Explicit, out-of-band

Use this when you have a `trace_id` in hand and want to attach a score directly. This is the most flexible method and works from anywhere in your codebase.

```python
from langfuse import get_client

langfuse = get_client()

# Trace-level score — overall quality of the full interaction
langfuse.create_score(
    trace_id="trace-abc123",
    name="overall_quality",
    value=0.87,
    data_type="NUMERIC",
    comment="Clear, accurate, well-structured response",
)

# Observation-level score — quality of a specific LLM call within the trace
langfuse.create_score(
    trace_id="trace-abc123",
    observation_id="obs-xyz789",
    name="hallucination_check",
    value=0,        # 0 = no hallucination detected
    data_type="BOOLEAN",
)

# Session-level score — satisfaction across a multi-turn conversation
langfuse.create_score(
    session_id="sess-user123",
    name="user_satisfaction",
    value="positive",
    data_type="CATEGORICAL",
)
```

### Method 2: Contextual scoring from within an observation

Use this when you are inside an active observation context and want to score it (or its parent trace) without passing IDs manually. Langfuse resolves the IDs from the active context.

```python
with langfuse.start_as_current_observation(as_type="generation", name="llm-call") as gen:
    response = call_llm(prompt)
    gen.update(output=response)

    # Score this specific generation observation
    gen.score(
        name="relevance",
        value=0.9,
        data_type="NUMERIC",
    )

    # Score the parent trace that contains this generation
    langfuse.score_current_trace(
        name="task_completed",
        value=1,
        data_type="BOOLEAN",
    )
```

💡 **Tip**: `gen.score()` attaches to the observation; `score_current_trace()` walks up and attaches to the enclosing trace. Use both together to capture different granularities without managing IDs.

### Method 3: `@observe` decorator with `langfuse_context`

Use this when your pipeline is instrumented with the decorator pattern. `langfuse_context` provides the same contextual scoring API inside any `@observe`-decorated function.

```python
from langfuse.decorators import observe, langfuse_context

@observe()
def evaluate_response(response: str, expected: str) -> float:
    score = compute_similarity(response, expected)

    # Score the current observation (this function's span)
    langfuse_context.score_current_observation(
        name="similarity_score",
        value=score,
        data_type="NUMERIC",
    )

    # Score the root trace containing this function call
    langfuse_context.score_current_trace(
        name="eval_passed",
        value=1 if score > 0.8 else 0,
        data_type="BOOLEAN",
    )

    return score
```

✅ All three methods produce identical score objects in Langfuse — choose based on your instrumentation pattern, not on any difference in outcome.

❌ Do not mix `langfuse_context` calls with the context-manager pattern (`start_as_current_observation`) in the same function — pick one instrumentation style per module to avoid context conflicts.

---

## 5. Async Scoring — Scoring After the Fact

---

**Async scoring** means attaching scores to a trace after the trace has already completed — and even after the trace has been flushed to Langfuse. This is a first-class pattern.

```python
# Step 1: run the LLM pipeline and capture the trace ID
result = run_pipeline(user_input)
trace_id = result.trace_id  # persist this ID — you'll need it later

# Step 2: score asynchronously — minutes, hours, or days later
# Scores can be attached without waiting until the trace has been created
langfuse.create_score(
    trace_id=trace_id,
    name="human_eval",
    value="correct",
    data_type="CATEGORICAL",
)
```

This is the canonical pattern for **human annotation workflows**:

1. Your app runs and traces are created in Langfuse.
2. Reviewers open the Langfuse annotation queue (or use your internal tool calling `create_score` via API).
3. Scores are written back to the existing trace — no re-execution needed.

> **Key insight**: The trace does not need to be open or in-flight when you create a score. Langfuse stores scores independently and links them by ID. This means your evaluation pipeline can be fully decoupled from your serving pipeline.

⚠️ Store `trace_id` values wherever you log your pipeline outputs — in your database, job queue, or evaluation dataset. Without them, you cannot attach scores retroactively.

---

## 6. Idempotency with Score IDs

---

By default, each call to `create_score()` creates a new score record. If your evaluator retries or re-runs, you will accumulate duplicate scores. Use a deterministic **score ID** to make scoring idempotent.

```python
import hashlib

# Build a deterministic ID from trace + metric + version
score_id = hashlib.md5(f"{trace_id}:correctness:v1".encode()).hexdigest()

langfuse.create_score(
    id=score_id,          # idempotent — re-running this upserts, not duplicates
    trace_id=trace_id,
    name="correctness",
    value=0.95,
    data_type="NUMERIC",
)
```

When the same `id` is submitted again, Langfuse **updates** the existing score rather than creating a duplicate.

Use idempotent score IDs when:

- Your evaluator has retry logic (network failures, timeouts)
- You re-run evaluations after fixing a bug in your scoring function
- You want to update a score's value without first fetching the existing score's UUID
- You run evaluation in distributed workers where the same trace might be processed twice

✅ Idempotent scoring is safe to run multiple times — the latest value wins.

❌ Do not use random UUIDs for `id` if you need idempotency — randomness defeats the purpose.

💡 **Tip**: Incorporate a version string (`:v1`, `:v2`) in your hash input. This lets you intentionally create a new score (bump to `:v2`) while still preventing retries from duplicating within the same version.

---

## 7. Score Configs — Optional Validation

---

**Score configs** are named schemas you define once in the Langfuse UI and then reference from your code via `config_id`. They enforce that scores conform to expected ranges or value sets before being written to the dashboard.

| Config type | What it validates |
|-------------|------------------|
| `NUMERIC` config | Min and max allowed values (e.g., `0.0` to `1.0`) |
| `CATEGORICAL` config | Allowed string values (e.g., `["good", "neutral", "bad"]`) |
| `BOOLEAN` | No config needed — always `0` or `1` |

```python
# After creating a "quality_score" config in the Langfuse UI:
langfuse.create_score(
    trace_id=trace_id,
    name="quality_score",
    value=1.25,           # ❌ this will fail validation if config range is 0.0–1.0
    data_type="NUMERIC",
    config_id="cfg-quality-score-v1",
)
```

> **Key insight**: Score configs protect your dashboards from bad data. An evaluator bug that emits `value=100` instead of `value=1.0` on a 0–1 scale will silently corrupt your averages unless a config rejects it at write time.

⚠️ `config_id` is optional — scores without a config are accepted unconditionally. For production evaluation pipelines, always define and attach configs to numeric and categorical scores.

---

## 8. User Feedback Pattern

---

Capturing **user feedback** (thumbs up/down, star ratings) from your product UI is a lightweight way to generate real-world signal on production traces. The pattern: your frontend sends a rating to your backend, your backend writes it as a score.

```python
from fastapi import FastAPI
from langfuse import get_client

app = FastAPI()
langfuse = get_client()

@app.post("/feedback")
async def submit_feedback(trace_id: str, rating: int):
    """
    Receive end-user ratings and attach them to the generating trace.
    rating: 1 = thumbs up, -1 = thumbs down
    """
    langfuse.create_score(
        trace_id=trace_id,
        name="user_feedback",
        value=rating,
        data_type="NUMERIC",
        comment="End-user rating",
    )
    return {"status": "recorded"}
```

To use this pattern end-to-end:

1. When your LLM pipeline runs, capture and return `trace_id` to the frontend (embed it in the API response or a custom header).
2. When the user rates a response, the frontend POSTs `trace_id` + `rating` to `/feedback`.
3. Your backend calls `create_score()` — the score is linked to the exact trace that generated the response the user saw.

💡 **Tip**: Use `NUMERIC` with `-1`/`0`/`1` rather than `CATEGORICAL` for thumbs ratings — numeric scores can be aggregated into a mean satisfaction trend. A `CATEGORICAL` `"thumbs_up"` cannot be averaged.

✅ This pattern works in async contexts — the trace may have been created minutes before the user submits feedback.

---

## 9. Querying Scores for Analysis

---

Use `get_scores()` to retrieve scores programmatically — for offline analysis, regression detection, or building your own dashboards.

```python
from datetime import datetime

# Fetch all scores attached to a specific trace
scores = langfuse.get_scores(trace_id="trace-abc123")

# Fetch scores by metric name and time window
scores = langfuse.get_scores(
    name="overall_quality",
    from_timestamp=datetime(2026, 1, 1),
    data_type="NUMERIC",
)

# Compute basic statistics
values = [s.value for s in scores]
if values:
    avg = sum(values) / len(values)
    minimum = min(values)
    maximum = max(values)
    print(f"n={len(values)}  avg={avg:.3f}  min={minimum:.3f}  max={maximum:.3f}")
```

Common query patterns:

| Goal | Parameters to use |
|------|------------------|
| Audit a single trace | `trace_id=` |
| Monitor a metric over time | `name=`, `from_timestamp=`, `to_timestamp=` |
| Pull all human annotations | `name=`, `data_type="CATEGORICAL"` |
| Compare two model versions | `name=`, filter by `trace_id` list from each experiment |

> **Key insight**: `get_scores()` is a flat list — it does not group by trace or session. Join on `trace_id` in your own code if you need per-trace aggregation.

💡 **Tip**: For large-scale analysis, export scores via the Langfuse REST API with pagination rather than pulling all scores into memory. Use `page` and `limit` parameters to batch the fetch.

---

## Cross-References

- **Prerequisites**: [`../foundations/01_what_is_langfuse.md`](../foundations/01_what_is_langfuse.md) — traces, observations, and sessions that scores attach to
- **Creating traces to score**: [`../python_sdk/03_instrumentation.md`](../python_sdk/03_instrumentation.md) — how to instrument your pipeline so you have `trace_id` and `observation_id` values to work with

---

**Next**: [Part 2: LLM-as-Judge](02_llm_as_judge.md)
