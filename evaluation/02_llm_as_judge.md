# LLM-as-Judge: Automated Evaluation at Scale

> **Who this is for**: Engineers who need to automatically evaluate LLM output quality in production or in experiments — without manually reviewing every trace. Assumes you've read [Part 1: Scoring System](01_scoring_system.md) and understand what scores are and how they attach to traces.

---

## 1. What LLM-as-Judge Is

**LLM-as-Judge** is the practice of using a language model to automatically evaluate another model's output. Instead of a human reviewer reading responses and assigning scores, a **judge model** receives the original prompt, the generated response, and optionally a reference answer — then returns a numeric or categorical score according to criteria you define.

This unlocks evaluation at a scale that human annotation cannot match: thousands of traces per day, scored continuously, with structured feedback attached to each one.

The judge model is just another LLM call. What makes it work is:

- A well-crafted **evaluation prompt** that defines the scoring rubric precisely
- **Structured output** (function calling or JSON) so scores are machine-readable
- Integration with Langfuse's `create_score` API so results land on the right trace

> **Key insight**: The judge is not magic — it reflects the quality of your evaluation prompt. A vague rubric produces noisy scores. A rubric with examples and a clear scale produces scores that correlate with human judgment.

---

## 2. Where LLM-as-Judge Runs in Langfuse

Langfuse supports three evaluation modes, each targeting a different part of the system:

```
Production Traces ──▶ Live Evaluator ──▶ Scores on Traces

Dataset Experiment ──▶ Experiment Evaluator ──▶ Scores on Dataset Run

Individual Span ──▶ Observation Evaluator ──▶ Scores on Observation
```

| Mode | Target | When it runs | Use case |
|------|--------|--------------|----------|
| **Live Evaluator** | Incoming production traces | Asynchronously after each trace | Continuous quality monitoring |
| **Experiment Evaluator** | Dataset experiment runs | After an experiment completes | Pre-deployment validation |
| **Observation Evaluator** | Individual spans/generations | On demand or scheduled | Per-model-call attribution |

**On Traces** (production monitoring): the evaluator watches for new traces matching your filter criteria and scores them automatically. You get a rolling quality signal over real traffic.

**On Observations** (operation-level): scores attach to a specific generation, retrieval step, or tool execution within a trace — not the trace as a whole. This is useful when a trace has multiple LLM calls and you want to attribute quality to the right one.

**On Experiments** (offline): when you run a dataset experiment (e.g., testing a new prompt variant), evaluators score each item's output and aggregate results for comparison. This is the pre-deployment gate.

---

## 3. Setting Up in the Langfuse UI

Navigate to **Evaluations → LLM-as-a-Judge** in the Langfuse dashboard. Click **Create Evaluator**.

The evaluator configuration has five fields:

**Name** — a slug identifier used as the score name on traces. Choose something descriptive and stable: `hallucination-check`, `relevance-score`, `toxicity-flag`. This becomes the `name` field on every score this evaluator creates.

**Model** — the judge model. Langfuse supports:
- OpenAI GPT-4o / GPT-4o-mini (recommended default — fast, cost-effective)
- Anthropic Claude (Sonnet, Haiku)
- Azure OpenAI (bring your own deployment)
- AWS Bedrock (for AWS-native deployments)

Langfuse uses **function calling** to extract structured scores — this is why the model must support it. Don't use text-only models here.

**Template** — the evaluation prompt. Write it once, reuse it across traces. Use variables in double-braces:
- `{{input}}` — the original user prompt or trace input
- `{{output}}` — the model's response being evaluated
- `{{expected_output}}` — reference answer (for correctness evaluation)
- `{{metadata}}` — any trace metadata you want to expose to the judge

**Score Config** — defines what the evaluator returns:
- `NUMERIC` with a min/max range (e.g., 0.0–1.0 for a grounded score)
- `CATEGORICAL` with defined values (e.g., `helpful` / `neutral` / `unhelpful`)
- `BOOLEAN` for binary pass/fail

**Target** — choose traces, observations, or experiments. For traces, configure a filter (e.g., tag = `production`) and a sampling rate.

> **Key insight**: Set up a Score Config before creating the evaluator. The config defines the schema; the evaluator writes to it. They're separate objects so you can share one config across multiple evaluators (e.g., both human annotators and LLM judges writing `relevance` scores on the same 0–1 scale).

---

## 4. Custom Evaluator via Python SDK

The UI evaluator is convenient but limited to Langfuse-hosted models and fixed templates. For production use, you'll often want a **programmatic evaluator** — full control over the prompt, model, retries, and score attachment.

```python
import json
import anthropic
from langfuse import get_client

langfuse = get_client()
claude = anthropic.Anthropic()


def evaluate_hallucination(trace_id: str, output: str, context: str) -> float:
    """Rate whether the output introduces information not present in the context.

    Returns a grounding score: 0 = fully hallucinated, 1 = fully grounded.
    Attaches the score and reasoning directly to the trace in Langfuse.
    """
    prompt = f"""You are an expert evaluator. Given a context and a generated response,
determine if the response contains hallucinated information not present in the context.

Context: {context}

Response: {output}

Rate on a scale of 0-1 where:
0 = completely hallucinated (information not in context)
1 = grounded (all claims supported by context)

Return only a JSON object: {{"score": <float>, "reasoning": "<brief explanation>"}}"""

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    result = json.loads(response.content[0].text)

    # Attach score to the trace — this is what surfaces in the Langfuse UI
    langfuse.create_score(
        trace_id=trace_id,
        name="hallucination_score",
        value=result["score"],
        data_type="NUMERIC",
        comment=result["reasoning"],  # reasoning shows up in the score detail view
    )

    return result["score"]
```

The `comment` field is where the judge's reasoning goes. This is invaluable when reviewing low-scoring traces — you can see exactly why the judge flagged something without re-running the evaluation.

> **Key insight**: Use `data_type="NUMERIC"` explicitly. Langfuse infers it from the value, but being explicit prevents surprises when score values are `0` or `1` (which could be mistaken for booleans).

---

## 5. Common Evaluation Criteria

| Criterion | What it measures | Score Type | Notes |
|-----------|-----------------|------------|-------|
| **Hallucination** | Does response introduce false info? | NUMERIC (0=bad, 1=good) | Requires context in the prompt |
| **Relevance** | Does response address the question? | NUMERIC (0–1) | Pure input/output, no reference needed |
| **Helpfulness** | Is the response actually useful? | CATEGORICAL (helpful/neutral/unhelpful) | Subjective — calibrate carefully |
| **Toxicity** | Does response contain harmful content? | BOOLEAN | Low threshold: flag anything borderline |
| **Faithfulness** | Is response faithful to source docs? | NUMERIC (0–1) | RAG pipelines — compare to retrieved chunks |
| **Correctness** | Does response match ground truth? | BOOLEAN or NUMERIC | Requires `expected_output` reference |
| **Conciseness** | Is response appropriately concise? | CATEGORICAL | Domain-dependent definition of "appropriate" |
| **Completeness** | Does response cover all required aspects? | NUMERIC (0–1) | Define required aspects explicitly in the rubric |

For RAG pipelines, **faithfulness** and **hallucination** address related but distinct failure modes: faithfulness asks "did the model stay within the retrieved documents?", while hallucination asks "did the model invent facts not present in the context?". Run both.

---

## 6. Batch Evaluation Pattern

This is the pattern production teams actually run: a scheduled job that scores all recent traces that haven't been evaluated yet. Idempotent, resumable, and safe to run multiple times.

```python
from datetime import datetime, timedelta
from langfuse import get_client

langfuse = get_client()


def run_daily_evaluation():
    """Score all traces from the last 24 hours that lack hallucination scores.

    Designed to run as a cron job (e.g., nightly at 02:00 UTC).
    Idempotent — skips traces that already have a hallucination_score.
    """
    yesterday = datetime.utcnow() - timedelta(days=1)

    # Fetch recent production traces — limit to 500 to bound LLM costs per run
    traces = langfuse.get_traces(
        from_timestamp=yesterday,
        tags=["production"],
        limit=500,
    )

    scored = 0
    skipped = 0

    for trace in traces:
        # Skip if already scored — makes the job idempotent
        existing_scores = langfuse.get_scores(
            trace_id=trace.id,
            name="hallucination_score",
        )
        if existing_scores:
            skipped += 1
            continue

        # Only evaluate traces that have both input context and output
        if not (trace.output and trace.input):
            skipped += 1
            continue

        score = evaluate_hallucination(
            trace_id=trace.id,
            output=trace.output,
            context=trace.input.get("context", ""),
        )
        scored += 1
        print(f"Trace {trace.id}: hallucination={score:.2f}")

    print(f"Done. Scored: {scored}, Skipped: {skipped}")
```

💡 Run this as a cron job rather than inline with your application. Evaluation adds latency and LLM cost — keep it out of the request path.

⚠️ The `limit=500` cap is intentional. At $0.002–0.01 per judge call, 500 traces/night costs $1–5. Remove the cap only after you've profiled your per-trace cost.

---

## 7. Observation-Level Evaluation

When a trace contains multiple LLM calls — a router, a retriever, and a generator, for example — trace-level scores average across all of them. **Observation-level evaluation** lets you attach scores directly to the span responsible for a particular output.

```python
from datetime import datetime, timedelta
from langfuse import get_client

langfuse = get_client()
yesterday = datetime.utcnow() - timedelta(days=1)


def evaluate_quality(input_data, output_data) -> float:
    """Stub — replace with your actual evaluation logic."""
    # ... judge call here ...
    return 0.85


# Fetch individual generation observations (not full traces)
observations = langfuse.get_observations(
    type="GENERATION",
    from_timestamp=yesterday,
)

for obs in observations:
    # Target a specific model — useful when you run multiple models in the same trace
    if obs.model and "gpt-4" in obs.model:
        langfuse.create_score(
            trace_id=obs.trace_id,      # still needed for grouping
            observation_id=obs.id,      # this pins the score to the specific span
            name="response_quality",
            value=evaluate_quality(obs.input, obs.output),
            data_type="NUMERIC",
        )
```

Providing `observation_id` alongside `trace_id` is what makes this an observation-level score. Without `observation_id`, the score attaches to the trace root.

Benefits over trace-level scoring:
- **Faster**: skip evaluating spans that don't produce user-facing output (e.g., router calls)
- **Precise attribution**: if quality drops, you know which model call in the pipeline caused it
- **Model comparison**: filter observations by `obs.model` to compare GPT-4o vs. Claude side-by-side on the same metric

---

## 8. Live Evaluator Setup

**Live evaluators** run automatically on new traces as they arrive in Langfuse — no cron job required. This is the fully managed path.

Setup in the UI:
1. **Evaluations → LLM-as-a-Judge → Create Live Evaluator**
2. Set the **trigger**: every trace, or sampled (e.g., 10%)
3. Pick the **model**, write the **template**, and link a **Score Config**
4. Set a **trace filter** if you only want to evaluate a subset (e.g., `tag = production`, `user_id != internal-test`)

The evaluator runs **asynchronously** after each trace is ingested — it doesn't block your application's response. Scores appear in the trace detail view within seconds.

**Sampling** is the primary cost control lever. Use it:

| Traffic volume | Recommended sampling | Cost implication |
|----------------|---------------------|-----------------|
| < 1K traces/day | 100% | Evaluate everything |
| 1K–10K traces/day | 20–50% | Balance coverage vs. cost |
| > 10K traces/day | 5–10% | Statistical sample is sufficient for trends |

⚠️ Sampling is random by default. If you need to ensure specific user segments or error cases are always evaluated, use a trace filter to separate them and set a higher sampling rate on that segment.

---

## 9. Evaluator Quality Tips

Getting the judge right matters more than the infrastructure around it.

✅ **Use function calling (structured output)** to extract scores. Parsing free-text responses is fragile — models occasionally explain their reasoning before giving the score, or format the number differently. Function calling guarantees a clean float or string every time.

✅ **Include few-shot examples in your judge prompt**. A rubric without examples is ambiguous. Show two or three scored examples that anchor what "0.2", "0.6", and "1.0" look like for your specific domain.

✅ **Calibrate against human labels before deploying**. Run your judge on 50–100 traces that a human has already scored. Measure agreement (Cohen's kappa or Spearman correlation). If agreement is below 0.6, revise the prompt before trusting the scores.

❌ **Don't use the same model as both judge and subject**. GPT-4o judging GPT-4o outputs exhibits **self-preference bias** — it rates its own outputs higher than equivalent outputs from other models. Use a different provider or model family as the judge.

⚠️ **LLM judges have roughly 70–80% agreement with human annotators** on most criteria. This is good enough for trends and regression detection, but not for high-stakes decisions (safety, legal, medical). For those domains, use LLM scores as a first-pass filter and route flagged traces to [human annotation queues](04_human_annotation.md).

💡 **Version your evaluation prompts**. When you improve the judge prompt, old scores and new scores are no longer comparable. Use a versioned score name (`hallucination_v1`, `hallucination_v2`) or a separate Score Config per version, so you can track improvements over time without corrupting historical trend lines.

---

**Next**: [Part 3: Datasets & Experiments](03_datasets_experiments.md)
