# Human Annotation in Langfuse: Structured Review Workflows for LLM Quality

> **Who this is for**: ML engineers and team leads setting up quality review processes for LLM applications — especially those combining human oversight with automated evaluation at scale.

---

## 1. Why Human Annotation

---

**Human annotation** is the practice of having subject-matter experts or trained reviewers manually assess LLM outputs and attach structured scores to traces. It is the ground-truth layer of any evaluation stack.

Automated **LLM-as-judge** evaluators are fast and cheap, but they are not perfect. Research consistently shows ~70–80% agreement between LLM judges and human raters on standard quality metrics. That gap matters in practice:

| Evaluation Method | Coverage | Cost | Agreement with Ground Truth |
|---|---|---|---|
| LLM-as-judge | 100% of traces | Low | ~70–80% |
| Human annotation | Sampled subset | High | 100% (is the ground truth) |
| User feedback | Self-selected | Near-zero | High signal, low volume |

For certain application domains, automated scoring alone is insufficient:

- **High-stakes decisions** — medical triage, legal document drafting, financial advice: errors have real consequences and require human sign-off
- **Customer-facing quality audits** — when SLA or brand reputation depends on output quality, humans must verify
- **Calibrating automated evaluators** — LLM judges must be validated against human consensus before they can be trusted at scale
- **Detecting subtle issues** — sycophancy, tone drift, instruction-following failures that LLMs often miss in each other

> **Key insight**: Human annotation is not a replacement for automated scoring — it is the calibration layer that makes automated scoring trustworthy. You use humans to validate machines, not to scale with them.

---

## 2. Annotation Queues

---

An **Annotation Queue** is a Langfuse-managed list of traces or observations routed to human reviewers for structured scoring. Instead of reviewers browsing traces ad hoc, a queue enforces a defined workflow: specific criteria, specific traces, specific reviewers.

Queues are configured in the Langfuse UI under **Evaluations → Annotation Queues**.

**Queue configuration options:**

| Option | Description |
|---|---|
| Name | Identifier for the queue (e.g., `production-quality-review`) |
| Description | Context for reviewers about what to evaluate and why |
| Score configs | Which score dimensions to collect (maps to existing score config definitions) |
| Sampling | Annotate N% of all traces matching a filter, or add traces manually/programmatically |

When a reviewer opens a queue item, they see:

- The trace input and output (the conversation or generation)
- The scoring form with all configured criteria
- Any metadata attached to the trace (tags, user ID, session context)

Critically, reviewers do **not** see the model version, prompt template, or code — this prevents **confirmation bias** toward known-good or known-bad configurations.

> **Key insight**: Queue-based review is structured workflow, not ad-hoc clicking. Without queues, review quality degrades because different reviewers score different things with different coverage.

---

## 3. Setting Up a Queue via SDK

---

Queues can be created and populated programmatically, enabling automated sampling pipelines that route traces to human review without manual intervention.

```python
from langfuse import get_client
from datetime import datetime, timedelta

langfuse = get_client()

last_week = datetime.utcnow() - timedelta(days=7)

# Create annotation queue programmatically
queue = langfuse.create_annotation_queue(
    name="production-quality-review",
    description="Weekly sample of production traces for quality audit",
    score_configs=["overall_quality", "factual_accuracy", "tone"],
)

# Route specific traces to annotation queue
traces = langfuse.get_traces(
    from_timestamp=last_week,
    tags=["production"],
    limit=50,  # sample 50 per week
)

for trace in traces:
    langfuse.add_to_annotation_queue(
        queue_id=queue.id,
        trace_id=trace.id,
    )
```

This pattern is typically run as a scheduled job (weekly or daily) that pulls a representative sample into the queue. The `score_configs` list references score configurations already defined in your Langfuse project — reviewers will see exactly those fields in their scoring form.

⚠️ `create_annotation_queue` creates a new queue each time it is called. In production, create the queue once (or look it up by name) and reuse the `queue.id` when adding traces.

💡 Tag traces at instrumentation time (e.g., `tags=["production", "v2.1", "medical-domain"]`) so your sampling queries can target exactly the right population.

---

## 4. The Annotation Workflow

---

```
Production Traces
      │
      ▼  (sampling / filter rules — e.g., 5% random, or all traces tagged "flagged")
Annotation Queue
      │
      ▼  (assigned to reviewer — first-come or round-robin)
Reviewer UI ──▶ Rate: quality=4, factual_accuracy=1 (pass/fail), tone="neutral"
                     │
                     ▼
                Scores attached to trace  (source="HUMAN_ANNOTATION")
                     │
                     ▼
                Analytics / Model comparison / Dataset promotion
```

The queue acts as a buffer between production traffic and reviewer bandwidth. Once scores are attached, they appear in the same Langfuse analytics views as automated scores — enabling direct comparison between human and LLM-judge evaluations on the same traces.

---

## 5. Creating Scores from Human Review

---

The Langfuse UI internally calls `create_score` when a reviewer submits their form. If you are building a custom annotation tool (e.g., a domain-specific review interface for medical staff), you can replicate this exactly via the SDK.

```python
from langfuse import get_client

def record_human_annotation(
    trace_id: str,
    reviewer_id: str,
    overall_quality: int,      # 1-5
    factual_accuracy: bool,    # True = accurate
    tone: str,                 # "professional" / "casual" / "inappropriate"
    notes: str = "",
):
    langfuse = get_client()

    langfuse.create_score(
        trace_id=trace_id,
        name="overall_quality",
        value=overall_quality,
        data_type="NUMERIC",
        comment=notes,
        source="HUMAN_ANNOTATION",  # marks it as human-sourced (vs. "API" for automated)
    )
    langfuse.create_score(
        trace_id=trace_id,
        name="factual_accuracy",
        value=1 if factual_accuracy else 0,
        data_type="BOOLEAN",
    )
    langfuse.create_score(
        trace_id=trace_id,
        name="tone",
        value=tone,
        data_type="CATEGORICAL",
    )
```

**Score source values:**

| Source | When to use |
|---|---|
| `"HUMAN_ANNOTATION"` | Reviewer submitted via Langfuse UI or custom annotation tool |
| `"API"` | Programmatic score from your own pipeline (default) |
| `"MODEL"` | LLM-as-judge automated score |

The `source` field enables filtering analytics to show only human scores, only automated scores, or both — essential when comparing the two populations.

---

## 6. Inter-Rater Reliability

---

**Inter-rater reliability (IRR)** measures how consistently different reviewers score the same traces. Without measuring IRR, you cannot tell whether low scores reflect genuine quality problems or reviewer disagreement about the rubric.

When multiple reviewers are assigned the same trace, each creates a separate score entry on that trace. The following computes a simple disagreement metric (standard deviation across raters) as a proxy for IRR:

```python
from collections import defaultdict
import statistics
from langfuse import get_client

langfuse = get_client()

def compute_inter_rater_agreement(metric_name: str, min_raters: int = 2):
    """Compute simple agreement rate for human scores using std dev as disagreement indicator."""
    traces = langfuse.get_traces(limit=200)

    trace_scores: dict[str, list[float]] = defaultdict(list)

    for trace in traces:
        scores = langfuse.get_scores(
            trace_id=trace.id,
            name=metric_name,
        )
        if len(scores) >= min_raters:
            trace_scores[trace.id] = [s.value for s in scores]

    # Compute pairwise agreement: lower std dev = higher agreement
    agreements = []
    for trace_id, values in trace_scores.items():
        if len(values) >= 2:
            # Simple agreement: std dev as disagreement indicator
            agreements.append(statistics.stdev(values))

    avg_disagreement = statistics.mean(agreements) if agreements else 0
    print(f"Avg std dev for '{metric_name}': {avg_disagreement:.3f}")
    print(f"Annotated traces: {len(trace_scores)}")
```

**Interpreting results:**

| Avg Std Dev | Interpretation | Action |
|---|---|---|
| < 0.5 | High agreement | Rubric is clear, scoring is consistent |
| 0.5 – 1.0 | Moderate disagreement | Revisit rubric definitions for this metric |
| > 1.0 | High disagreement | Rubric is ambiguous — hold a calibration session before continuing |

For categorical scores (tone, intent), use percent agreement or Cohen's Kappa instead of std dev. A Kappa < 0.6 indicates the category definitions need revision.

> **Key insight**: IRR is not a one-time check. Measure it each annotation round. Reviewer agreement tends to drift over time as individual interpretations of "3 vs 4" gradually diverge.

---

## 7. Human + Automated Evaluation Combined

---

The highest-value evaluation stack combines automated scoring for coverage with human annotation for calibration and triage.

```
Every trace:    Automated LLM judge (10% sample) ──▶ hallucination score
Weekly sample:  Human reviewer ──▶ overall_quality + tone scores
Flagged traces: human review triggered when hallucination_score < 0.6
```

**Triage pattern** — use automated scoring to filter for human review:

```python
from langfuse import get_client

HUMAN_REVIEW_QUEUE_ID = "queue_abc123"  # set from your Langfuse project

langfuse = get_client()

def triage_for_human_review(trace_id: str, hallucination_score: float):
    """Route low-scoring traces to human annotation queue."""
    if hallucination_score < 0.6:
        langfuse.add_to_annotation_queue(
            queue_id=HUMAN_REVIEW_QUEUE_ID,
            trace_id=trace_id,
            metadata={"triggered_by": "hallucination_score", "score": hallucination_score},
        )
```

Call `triage_for_human_review` immediately after your LLM judge emits its score. This closes the loop: automated scoring runs at full volume, and the worst-performing traces are automatically escalated to human reviewers who verify whether the model genuinely failed or the judge was wrong.

**Why this matters:** The most valuable annotation targets are not random traces — they are the traces where automated and human judgement are likely to disagree. Triage directs reviewer attention exactly there.

---

## 8. Calibration — Aligning Humans and Automated Judges

---

**Calibration** is the process of verifying that your LLM judge agrees with human consensus on a known-good reference set, and retuning the judge when it does not.

**Calibration workflow:**

```
Step 1: Build a golden set
        ──▶ 50–100 traces that multiple human reviewers have scored
        ──▶ Retain only traces with high inter-rater agreement (std dev < 0.5)
        ──▶ This is your ground truth

Step 2: Score the golden set with your LLM judge
        ──▶ Run the judge prompt against all 50–100 traces
        ──▶ Record judge scores separately (don't overwrite human scores)

Step 3: Measure agreement
        ──▶ Compare judge scores to human consensus on each trace
        ──▶ Target: ≥ 70% agreement on binary pass/fail decisions
        ──▶ Target: Pearson r ≥ 0.7 for numeric metrics

Step 4: Retune if needed
        ──▶ If judge agrees < 70%: revise the judge system prompt
        ──▶ Add few-shot examples drawn from disagreement cases
        ──▶ Repeat calibration

Step 5: Schedule quarterly recalibration
        ──▶ LLM response style evolves with model updates and prompt changes
        ──▶ A calibrated judge from 6 months ago may no longer reflect current quality
```

⚠️ A judge that has never been calibrated against human scores is not an evaluator — it is an opinion. Calibration is what converts it into a measurement.

---

## 9. Best Practices

---

✅ **Define scoring rubrics before annotation starts.** Write down exactly what "3" vs "4" means on your quality scale. If reviewers have to infer it, they will infer it differently.

✅ **Double-annotate 10–15% of traces to measure inter-rater reliability.** Assign the same traces to two reviewers without telling either. Compare scores to verify rubric clarity.

✅ **Rotate reviewers to prevent bias drift.** A reviewer who handles 500 traces alone will gradually develop idiosyncratic standards. Cross-assign reviewers across teams and time periods.

✅ **Record reviewer identity on every score.** Use the `comment` field or a separate metadata score to tag `reviewer_id`. This enables per-reviewer calibration and IRR analysis.

❌ **Don't let reviewers see the model version or prompt template.** If reviewers know they are reviewing "the new GPT-4o version," they introduce expectation bias. Blind review is more reliable.

❌ **Don't use annotation as your primary coverage mechanism.** Human annotation is expensive — 50–200 traces per week is realistic for a small team. Use automated scoring for volume; use humans for signal quality.

⚠️ **Human annotation is expensive.** Budget reviewer time explicitly. A realistic throughput is 30–60 traces per reviewer-hour for simple scoring tasks, less for complex multi-criteria review.

💡 **Most valuable annotation target:** Traces where the automated judge score was low but actual quality was high (false positives from the judge). These cases teach you the most about where your judge fails and are the best few-shot examples for retuning it.

---

## 10. Integration with Datasets — Promoting Annotated Traces to Test Sets

---

High-quality human-annotated traces are the best raw material for **golden datasets** — the reference sets used in offline experiments to catch regressions before shipping.

```python
from langfuse import get_client

langfuse = get_client()

def get_human_score(trace_id: str, metric_name: str) -> float | None:
    """Retrieve the most recent human annotation score for a trace."""
    scores = langfuse.get_scores(
        trace_id=trace_id,
        name=metric_name,
        source="HUMAN_ANNOTATION",
    )
    return scores[0].value if scores else None

# Traces with high human scores become golden dataset items
annotated_traces = langfuse.get_traces(limit=500, tags=["human-reviewed"])

high_quality_traces = [
    trace for trace in annotated_traces
    if get_human_score(trace.id, "overall_quality") >= 4
]

for trace in high_quality_traces:
    langfuse.create_dataset_item(
        dataset_name="golden-set-v1",
        input=trace.input,
        expected_output=trace.output,
        metadata={"human_score": get_human_score(trace.id, "overall_quality")},
    )
```

This creates a virtuous cycle:

```
Production traces
      │
      ▼ (automated triage)
Human annotation queue
      │
      ▼ (reviewer scores ≥ 4)
Dataset: golden-set-v1
      │
      ▼ (run experiments against dataset)
Catch regressions before shipping
      │
      ▼ (new model/prompt passes → ships to production → generates new traces)
      └──────────────────────────────────────────────────────────────────────▶ (loop)
```

💡 Version your golden sets (e.g., `golden-set-v1`, `golden-set-v2`). As your application evolves, traces from six months ago may no longer represent the current input distribution. Refresh the golden set quarterly alongside your LLM judge calibration.

---

## 11. Navigation and Cross-References

---

This file is part of the `evaluation/` section. Reading order and related files:

| File | Topic | Relationship to this file |
|---|---|---|
| [01_scoring_system.md](01_scoring_system.md) | Scores | `create_score` is the primitive that all human annotation calls use |
| [02_llm_as_judge.md](02_llm_as_judge.md) | LLM-as-Judge | The automated counterpart — human annotation calibrates LLM judges |
| [03_datasets_experiments.md](03_datasets_experiments.md) | Datasets & Experiments | Human-annotated traces feed into golden datasets for offline experiments |
| [04_human_annotation.md](04_human_annotation.md) | Human Annotation | This file |
| [../README.md](../README.md) | Root index | Top-level navigation for the full knowledge base |

---

## Full Evaluation Architecture Summary

---

The four files in this section form a complete, layered evaluation system. Each layer depends on and validates the one below it:

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 4: Human Annotation                    │
│  Annotation queues · IRR measurement · Calibration sessions     │
│  "Human scores are the ground truth that validates everything." │
└──────────────────────────────┬──────────────────────────────────┘
                               │ validates & promotes to
┌──────────────────────────────▼──────────────────────────────────┐
│               LAYER 3: Datasets & Experiments                   │
│  Golden sets · Experiment runs · Regression detection           │
│  "Run every candidate change against a known-good benchmark."   │
└──────────────────────────────┬──────────────────────────────────┘
                               │ scores feed into
┌──────────────────────────────▼──────────────────────────────────┐
│                  LAYER 2: LLM-as-Judge                          │
│  Automated quality scoring at scale · Triage · Volume coverage  │
│  "Score every trace — humans can't, LLMs can."                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ built on
┌──────────────────────────────▼──────────────────────────────────┐
│                  LAYER 1: Scoring System                        │
│  create_score · NUMERIC / BOOLEAN / CATEGORICAL · Sources       │
│  "A score is a named measurement attached to any trace."        │
└─────────────────────────────────────────────────────────────────┘
```

**How the layers interact in practice:**

1. **Scoring System** (01) defines the data model. Every evaluation result — automated or human — is a `Score` object with a `name`, `value`, `data_type`, and `source`. Nothing in layers 2–4 works without this primitive.

2. **LLM-as-Judge** (02) provides scale. Automated judges run against the full production trace volume or a large sample, producing scores on every trace. This is not ground truth — it is a signal that needs calibration.

3. **Datasets & Experiments** (03) provides reproducibility. When you change a prompt or model, you run it against a fixed dataset and compare score distributions across runs. Regressions appear as degraded scores on the same inputs across versions.

4. **Human Annotation** (04) provides ground truth. Reviewers score a sampled subset via annotation queues. Their scores calibrate the LLM judge, validate experiment conclusions, and feed back into the golden dataset — closing the loop.

> **Key insight**: The system is only as strong as its calibration. Invest in human annotation not to score everything, but to ensure your automated scoring accurately represents real quality. A well-calibrated LLM judge backed by a quarterly-refreshed golden set is the realistic target state for a mature LLM evaluation practice.
