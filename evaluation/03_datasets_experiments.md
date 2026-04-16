# Datasets and Experiments: Validating LLM Changes Before Production

> **Who this is for**: Engineers who want a structured offline evaluation loop — create test datasets, run experiments against multiple prompt/model versions, detect regressions automatically, and gate deploys on eval results. Assumes familiarity with Langfuse tracing (see [Scoring System](01_scoring_system.md)) and optionally [LLM-as-Judge](02_llm_as_judge.md) for automated scoring.

---

## 1. The Offline Evaluation Loop

The core idea: before shipping a change, run your app against a fixed set of known inputs and compare scores against the last known-good version.

```
Curate Dataset ──▶ Run Experiment ──▶ Score Results ──▶ Compare Versions ──▶ Deploy/Reject
      │                                                           │
      └──────────── Add edge cases from production ◀─────────────┘
```

The loop feeds itself: production traffic surfaces new failure modes, which become new dataset items, which sharpen the next experiment.

**Four building blocks:**

| Concept | What it is |
|---|---|
| **Dataset** | A named collection of curated test cases with optional expected outputs |
| **Dataset Item** | A single test case: `input`, `expected_output`, `metadata` |
| **Experiment Run** | One pass of your pipeline over all dataset items under a specific config (model, prompt version, temperature) |
| **Scores** | Numeric or categorical evaluations attached to each run item — automated or human |

> **Key insight**: A dataset is only as useful as its coverage. Happy-path inputs give you false confidence. The inputs that broke production are the ones that belong in your dataset.

---

## 2. Creating a Dataset

**Datasets** are created once and reused across many experiment runs. Give them version suffixes from the start — you will create `v2` eventually.

```python
from langfuse import get_client

langfuse = get_client()

# Create a new dataset — idempotent-safe to call repeatedly if you check first
dataset = langfuse.create_dataset(
    name="qa-evaluation-v1",
    description="QA test cases for the customer support bot",
    metadata={"domain": "customer-support", "created_by": "ml-team"},
)

# Retrieve an existing dataset by name
# dataset = langfuse.get_dataset("qa-evaluation-v1")
print(f"Dataset '{dataset.name}' ready — {len(dataset.items)} items loaded.")
```

> **Key insight**: `create_dataset` and `get_dataset` are the two entry points. In scripts that run repeatedly (e.g., CI), prefer `get_dataset` after initial creation to avoid duplicate-name errors.

---

## 3. Adding Dataset Items

**Dataset items** are the unit of evaluation. Each item carries an `input` (what goes into your pipeline), an optional `expected_output` (ground truth for scoring), and free-form `metadata` for filtering and grouping later.

```python
# Single item — hand-curated test case
langfuse.create_dataset_item(
    dataset_name="qa-evaluation-v1",
    input={"question": "How do I reset my password?"},
    expected_output="Navigate to Settings > Security > Reset Password, then check your email.",
    metadata={"category": "account-management", "difficulty": "easy"},
)

# Edge case — empty input should be handled gracefully
langfuse.create_dataset_item(
    dataset_name="qa-evaluation-v1",
    input={"question": ""},
    expected_output="I'm sorry, I didn't receive a question. Could you please rephrase?",
    metadata={"category": "edge-case", "difficulty": "hard"},
)
```

**Bulk promotion from production traces** is the most efficient way to grow a high-signal dataset. Tag production traces that represent interesting behavior, then promote them:

```python
# Bulk creation from production traces (promoting good examples)
# Tag traces in Langfuse UI or via SDK: langfuse_context.update_current_trace(tags=["high-quality"])
traces = langfuse.get_traces(tags=["production", "high-quality"], limit=100)

for trace in traces:
    langfuse.create_dataset_item(
        dataset_name="qa-evaluation-v1",
        input=trace.input,
        expected_output=trace.output,          # human-verified output becomes ground truth
        metadata={"source_trace_id": trace.id},  # keep provenance for debugging
    )

print(f"Promoted {len(traces)} production traces to dataset items.")
```

> **Key insight**: `source_trace_id` in metadata is invaluable. When a test item fails, you can jump straight to the original production trace to understand the full context.

---

## 4. Running an Experiment via SDK

An **experiment run** is identified by a `run_name`. Every dataset item processed under that name gets linked together in the Langfuse UI, enabling side-by-side comparison across runs.

The `item.observe(run_name=...)` context manager is the key primitive — it creates a trace for this item and associates it with the named run automatically.

```python
from langfuse import get_client
from langfuse.decorators import observe

langfuse = get_client()


def run_my_pipeline(input_data: dict) -> str:
    """The pipeline to evaluate — same code as production, no test-only branching."""
    question = input_data["question"]
    response = llm_pipeline(question)   # your actual LLM call
    return response


def score_output(output: str, expected: str) -> float:
    """
    Simple substring scorer — swap this for LLM-as-judge when precision matters.
    See 02_llm_as_judge.md for a production-grade judge implementation.
    """
    if not expected:
        return 0.0  # can't score without ground truth — skip or flag for human review
    if expected.lower() in output.lower():
        return 1.0
    return 0.5  # partial credit for partial matches


def run_experiment(dataset_name: str, run_name: str) -> None:
    dataset = langfuse.get_dataset(dataset_name)

    for item in dataset.items:
        # item.observe() creates a trace per item and tags it with run_name
        with item.observe(run_name=run_name) as trace_id:
            output = run_my_pipeline(item.input)

            # Attach score to this specific dataset run item
            langfuse.create_score(
                trace_id=trace_id,
                name="answer_quality",
                value=score_output(output, item.expected_output),
                data_type="NUMERIC",
            )

    # flush() ensures all scores are sent before the script exits
    langfuse.flush()
    print(f"Experiment '{run_name}' completed. View in Langfuse UI → Datasets → {dataset_name}.")


# Run the same dataset against two prompt versions — results are comparable in the UI
run_experiment("qa-evaluation-v1", run_name="prompt-v1-gpt4o")
run_experiment("qa-evaluation-v1", run_name="prompt-v2-gpt4o")
```

> **Key insight**: Use `run_name` to encode everything that makes this run unique — prompt version, model, temperature, date. Example: `"prompt-v2-gpt4o-temp0.3-2024-01-15"`. You'll thank yourself when comparing 10 runs three months later.

---

## 5. Comparing Experiment Runs in the Langfuse UI

After running experiments, the UI provides a structured comparison view:

```
Langfuse UI Navigation:
  Datasets
    └── qa-evaluation-v1
          └── Runs
                ├── prompt-v1-gpt4o   ← baseline
                └── prompt-v2-gpt4o   ← candidate
```

**What the comparison view shows:**

| Metric | prompt-v1-gpt4o | prompt-v2-gpt4o | Delta |
|---|---|---|---|
| `answer_quality` mean | 0.74 | 0.81 | +0.07 ✅ |
| Pass rate (≥ 0.8) | 61% | 73% | +12pp ✅ |
| Median latency | 820 ms | 1,240 ms | +420 ms ⚠️ |
| Cost per item | $0.0021 | $0.0038 | +81% ⚠️ |

**Workflow for analyzing regressions:**

1. Sort items by `score delta` descending — items where v2 scored *lower* than v1 appear first
2. For each regressed item, click through to the side-by-side trace view
3. Compare the full prompt, model output, and score explanation between runs
4. Tag confirmed regressions for human review (see [Human Annotation](04_human_annotation.md))

> **Key insight**: v2 improving accuracy at the cost of latency and cost is a real trade-off that belongs in a team decision, not a script. The dataset comparison makes that trade-off explicit and reviewable.

---

## 6. Automated Regression Detection

Manual UI review doesn't scale to CI. This pattern computes the score delta programmatically and raises an error if the candidate run drops below the baseline by more than a configurable threshold.

```python
from langfuse import get_client

langfuse = get_client()


def get_run_scores(dataset_name: str, run_name: str, metric: str) -> list[float]:
    """
    Fetch per-item scores for a named experiment run.
    Returns only items that have a score for the given metric — missing scores are skipped,
    not treated as zero, to avoid penalizing runs that didn't cover every item.
    """
    dataset = langfuse.get_dataset(dataset_name)
    scores = []

    for item in dataset.items:
        run = item.get_langfuse_object(run_name)
        if run is None:
            continue  # this item wasn't processed in this run — skip it

        item_scores = langfuse.get_scores(trace_id=run.id, name=metric)
        if item_scores:
            scores.append(item_scores[0].value)  # take the most recent score

    return scores


def detect_regression(
    dataset_name: str,
    baseline_run: str,
    candidate_run: str,
    metric: str = "answer_quality",
    threshold: float = 0.05,
) -> None:
    """
    Raise ValueError if candidate run scores more than `threshold` below baseline.
    Default threshold of 0.05 = 5% drop triggers a CI failure.
    """
    baseline_scores = get_run_scores(dataset_name, baseline_run, metric=metric)
    candidate_scores = get_run_scores(dataset_name, candidate_run, metric=metric)

    if not baseline_scores:
        raise ValueError(f"No scores found for baseline run '{baseline_run}' on metric '{metric}'.")
    if not candidate_scores:
        raise ValueError(f"No scores found for candidate run '{candidate_run}' on metric '{metric}'.")

    baseline_mean = sum(baseline_scores) / len(baseline_scores)
    candidate_mean = sum(candidate_scores) / len(candidate_scores)
    delta = candidate_mean - baseline_mean

    print(
        f"[{metric}] baseline={baseline_mean:.3f} ({len(baseline_scores)} items)  "
        f"candidate={candidate_mean:.3f} ({len(candidate_scores)} items)  "
        f"delta={delta:+.3f}"
    )

    if delta < -threshold:
        raise ValueError(
            f"Regression detected: '{candidate_run}' scored {candidate_mean:.3f} "
            f"vs baseline '{baseline_run}' {baseline_mean:.3f} "
            f"(delta {delta:.3f} exceeds threshold -{threshold:.3f})"
        )

    print("No regression detected.")
```

> **Key insight**: Treat `threshold` as a team policy, not a magic number. A 5% drop on a 50-item dataset may be noise; the same drop on a 500-item dataset with a consistent pattern is a real regression. Start at 0.05 and tighten it as your dataset matures.

---

## 7. CI/CD Integration: Gating Deploys on Eval Results

This is how teams actually use datasets — every pull request runs the evaluation suite, and a regression blocks the merge.

**GitHub Actions workflow:**

```yaml
# .github/workflows/eval.yml
name: LLM Evaluation Gate

on:
  pull_request:
    paths:
      - "src/prompts/**"       # only run evals when prompts or pipeline code changes
      - "src/pipeline/**"
      - "scripts/run_experiment.py"

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run LLM Evaluation
        env:
          LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
          LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
          LANGFUSE_HOST: ${{ secrets.LANGFUSE_HOST }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          python scripts/run_experiment.py \
            --dataset qa-evaluation-v1 \
            --run-name "pr-${{ github.event.pull_request.number }}-$(git rev-parse --short HEAD)" \
            --compare-to "main-latest"
```

**Evaluation script (`scripts/run_experiment.py`):**

```python
import sys
import argparse
from langfuse import get_client

# Import your pipeline and the helpers defined above
from src.pipeline import llm_pipeline
from src.eval import run_experiment, detect_regression

langfuse = get_client()

parser = argparse.ArgumentParser(description="Run Langfuse experiment and check for regressions.")
parser.add_argument("--dataset", required=True, help="Langfuse dataset name")
parser.add_argument("--run-name", required=True, help="Unique name for this experiment run")
parser.add_argument("--compare-to", required=True, help="Baseline run name to compare against")
parser.add_argument("--threshold", type=float, default=0.05, help="Regression threshold (default: 0.05)")
args = parser.parse_args()

print(f"Running experiment '{args.run_name}' against dataset '{args.dataset}'...")
run_experiment(args.dataset, args.run_name)

print(f"Comparing against baseline '{args.compare_to}'...")
try:
    detect_regression(
        dataset_name=args.dataset,
        baseline_run=args.compare_to,
        candidate_run=args.run_name,
        threshold=args.threshold,
    )
except ValueError as e:
    # Print clearly so the CI log is readable without opening the Langfuse UI
    print(f"\nCI FAIL: {e}")
    print(f"Review run in Langfuse: Datasets → {args.dataset} → Runs → {args.run_name}")
    sys.exit(1)

print(f"\nEval passed. PR is safe to merge.")
sys.exit(0)
```

**Full CI/CD flow:**

```
PR opened
    │
    ▼
GitHub Actions triggers eval.yml
    │
    ▼
run_experiment() ──▶ Langfuse: new run created, traces + scores stored
    │
    ▼
detect_regression() ──▶ fetches scores for candidate + baseline
    │
    ├── delta ≥ -threshold ──▶ exit 0 ──▶ CI green ──▶ PR can merge
    │
    └── delta < -threshold ──▶ exit 1 ──▶ CI red ──▶ PR blocked
                                                │
                                                ▼
                                    Engineer reviews Langfuse UI
                                    for per-item regression details
```

⚠️ **Pin your `--compare-to` run name in CI.** A common pattern is to tag the last merged run as `main-latest` after each successful deploy. If you pass a stale baseline, regressions slip through undetected.

💡 Store the Langfuse run URL in the CI output: `https://your-langfuse-host/datasets/{dataset}/runs/{run_name}`. Engineers click directly to the regression details instead of navigating manually.

---

## 8. Dataset Best Practices

| Practice | Guidance |
|---|---|
| ✅ Edge cases | Include: empty inputs, very long inputs (near context limit), ambiguous queries, multilingual inputs |
| ✅ Production failures | Traces that caused user complaints or escalations are the highest-signal dataset items |
| ✅ Version datasets | `qa-eval-v1`, `qa-eval-v2` — never mutate a dataset that has experiment runs attached to it |
| ✅ Metadata discipline | Tag every item with `category`, `difficulty`, and `source_trace_id` — enables drill-down filtering |
| ❌ Happy path only | A dataset of easy, well-formed inputs won't catch the regressions that matter in production |
| ❌ Huge noisy datasets | 10,000 low-quality items produces noisy means that obscure real signal |
| ⚠️ Dataset size | 50–200 focused items is the practical sweet spot; scale up only with a curation process |
| ⚠️ Mutating live datasets | Adding items to a dataset after runs have been recorded makes historical comparisons unreliable |
| 💡 Regular promotion | Schedule a weekly review of production traces — promote the interesting ones to your dataset |
| 💡 Difficulty tiers | Tag items `easy`/`medium`/`hard` and track pass rate per tier — regressions often show up first on hard items |

> **Key insight**: A small, curated, version-stable dataset beats a large, noisy, ever-changing one. The goal is a reliable signal, not coverage theater.

---

## 9. Concept Map

```
                    ┌─────────────────────────────────────┐
                    │           Langfuse Dataset           │
                    │  name: "qa-evaluation-v1"            │
                    └──────────────┬──────────────────────┘
                                   │  contains
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
               ┌──────────┐ ┌──────────┐ ┌──────────┐
               │  Item 1  │ │  Item 2  │ │  Item N  │
               │ input    │ │ input    │ │ input    │
               │ expected │ │ expected │ │ expected │
               └────┬─────┘ └────┬─────┘ └────┬─────┘
                    │             │             │
           run_name │             │             │  item.observe(run_name=...)
                    ▼             ▼             ▼
               ┌──────────┐ ┌──────────┐ ┌──────────┐
               │ Trace 1  │ │ Trace 2  │ │ Trace N  │   ← Experiment Run A
               │ score=0.8│ │ score=1.0│ │ score=0.5│
               └──────────┘ └──────────┘ └──────────┘

               ┌──────────┐ ┌──────────┐ ┌──────────┐
               │ Trace 1' │ │ Trace 2' │ │ Trace N' │   ← Experiment Run B
               │ score=0.9│ │ score=0.9│ │ score=0.7│
               └──────────┘ └──────────┘ └──────────┘
                    │
                    ▼
             detect_regression()
             compares mean(Run A) vs mean(Run B)
             raises if delta < -threshold
```

---

**Previous**: [Part 2: LLM-as-Judge](02_llm_as_judge.md)

**Next**: [Part 4: Human Annotation](04_human_annotation.md)
