# Evaluation, Metrics, and Alerting

Langfuse turns traces into AI engineering feedback loops. The loop is:

1. Observe production behavior.
2. Score important traces.
3. Build datasets from representative examples.
4. Run experiments against candidate changes.
5. Promote the best version.
6. Watch quality, latency, cost, and volume after release.

## Scores

Scores are the core metric primitive for quality. They can come from users, humans, code, or model-based evaluators.

```python
from langfuse import get_client

langfuse = get_client()


def score_active_trace(relevance: float, accepted: bool) -> None:
    langfuse.score_current_trace(
        name="answer_relevance",
        value=relevance,
        data_type="NUMERIC",
    )
    langfuse.score_current_trace(
        name="user_accepted",
        value=1 if accepted else 0,
        data_type="BOOLEAN",
    )
```

Out-of-band scoring:

```python
from langfuse import get_client

langfuse = get_client()


def record_judge_score(trace_id: str, score_id: str, value: float) -> None:
    langfuse.create_score(
        trace_id=trace_id,
        score_id=score_id,
        name="groundedness",
        value=value,
        data_type="NUMERIC",
        comment="Automated groundedness judge",
        metadata={"judge": "groundedness-v3"},
    )
```

Use deterministic score IDs for idempotency. A good pattern is:

```text
<trace_id>:<score_name>:<evaluator_version>
```

## Choosing Score Types

| Need | Score type | Example |
| --- | --- | --- |
| Continuous quality | `NUMERIC` | relevance `0.81` |
| Pass/fail | `BOOLEAN` | safe answer `1` |
| Discrete label | `CATEGORICAL` | `"correct"`, `"partial"`, `"incorrect"` |
| Reviewer note | `TEXT` | `"cites stale source"` |

Use `BOOLEAN` values as `0` or `1`.

## Score Configs

Score configs standardize scoring across a team. They define score names, data types, and optional constraints such as numeric ranges or allowed categories.

Use score configs when:

- multiple humans or evaluators produce the same score name;
- annotation queues should present a fixed rubric;
- dashboards compare scores across releases;
- you want invalid score values rejected instead of silently polluting analytics.

Score configs are required for annotation queues and optional for programmatic SDK/API scoring. Create and manage them in the Langfuse UI under project score/evaluation settings, or via the Langfuse API when you need automation.

Practical score config examples:

| Score | Type | Suggested constraint |
| --- | --- | --- |
| `answer_relevance` | `NUMERIC` | `0.0` to `1.0` |
| `groundedness` | `NUMERIC` | `0.0` to `1.0` |
| `user_accepted` | `BOOLEAN` | `0` or `1` |
| `failure_category` | `CATEGORICAL` | `"retrieval"`, `"prompt"`, `"tool"`, `"policy"`, `"provider"` |
| `review_notes` | `TEXT` | free-form reviewer comment |

## User Feedback

User feedback should land on the trace the user actually saw.

```python
from langfuse import get_client

langfuse = get_client()


def submit_feedback(trace_id: str, thumbs_up: bool, comment: str | None = None) -> None:
    langfuse.create_score(
        trace_id=trace_id,
        name="user_feedback",
        value=1 if thumbs_up else 0,
        data_type="BOOLEAN",
        comment=comment,
    )
```

Store the Langfuse trace ID with the response payload or conversation message so the feedback endpoint can attach the score later.

## Code Evaluators

Use deterministic code for checks that do not require judgment:

- JSON schema validity
- citation format
- required fields present
- no empty answer
- all tool calls completed
- grounded citation IDs exist in retrieved documents

```python
from langfuse import get_client

langfuse = get_client()


def score_json_validity(trace_id: str, output: str) -> None:
    try:
        json.loads(output)
        valid = 1
        comment = "Valid JSON"
    except ValueError as exc:
        valid = 0
        comment = str(exc)

    langfuse.create_score(
        trace_id=trace_id,
        score_id=f"{trace_id}:json_valid:v1",
        name="json_valid",
        value=valid,
        data_type="BOOLEAN",
        comment=comment,
    )
```

## LLM-as-Judge

Use model judges for fuzzy judgments, but treat them as evaluators that need validation.

Good judge dimensions:

- answer relevance
- groundedness
- instruction following
- helpfulness
- safety policy compliance
- tone

Practical rules:

- Keep judge prompts versioned.
- Calibrate against human-reviewed examples.
- Store judge model and prompt version in score metadata.
- Prefer numeric or categorical rubrics over free-form only.
- Run judge scoring asynchronously when latency matters.
- Avoid scoring the judge with the same context that produced the original answer if that creates bias.
- Prefer observation-level evaluators for production monitoring when the question is about a specific generation, retrieval, tool, or guardrail observation.
- Use trace-level evaluators only when the judge truly needs the full multi-step workflow context.
- Use experiments for controlled offline comparisons before release.

```python
from langfuse import get_client

langfuse = get_client()


def record_llm_judge(trace_id: str, groundedness: float, explanation: str) -> None:
    langfuse.create_score(
        trace_id=trace_id,
        score_id=f"{trace_id}:groundedness:judge-v4",
        name="groundedness",
        value=groundedness,
        data_type="NUMERIC",
        comment=explanation,
        metadata={"judge": "judge-v4", "rubric": "groundedness-2026-06"},
    )
```

Production targeting guidance:

| Target | Use when |
| --- | --- |
| Observation | Evaluate one operation, such as final answer helpfulness, retrieval relevance, toxicity, or tool result quality |
| Trace | Evaluate an end-to-end workflow that needs context across retrieval, tools, generation, and guardrails |
| Experiment | Compare prompt, model, retrieval, or code variants on a controlled dataset |

Observation-level evaluators are usually faster and cheaper for production monitoring because they avoid judging entire traces when only one operation matters.

## Human Annotation

Human review is still the anchor for high-stakes quality work. Use it to calibrate LLM-as-judge prompts, review edge cases, and build gold datasets.

Annotation queues are the Langfuse workflow for assigning traces, observations, or sessions to domain experts. Reviewers add scores and comments using score configs.

Use annotation queues for:

- reviewing low-score or high-risk traces;
- sampling production traffic for quality audits;
- collecting corrected outputs;
- aligning humans and LLM judges;
- creating gold examples for datasets;
- reviewing safety or compliance-sensitive cases.

Recommended workflow:

1. Define score configs for the rubric.
2. Add traces, observations, or sessions to an annotation queue.
3. Assign reviewers or domain experts.
4. Reviewers score and comment on each item.
5. Compare human scores against automated judge scores.
6. Promote representative reviewed traces into datasets.

Track inter-rater reliability when more than one reviewer scores the same examples. For categorical labels, compare agreement rates. For numeric scores, track average disagreement and review outliers. Reconcile unclear rubric language before using the scores as a release gate.

## Datasets

Datasets turn production examples into regression tests.

```python
from langfuse import get_client

langfuse = get_client()

langfuse.create_dataset(
    name="support-rag-regression",
    description="Representative support RAG cases and historical failures",
    metadata={"owner": "ai-platform", "domain": "support"},
)

langfuse.create_dataset_item(
    dataset_name="support-rag-regression",
    input={"question": "How do I reset SSO for my workspace?"},
    expected_output={"must_cite": ["sso-admin-guide"]},
    metadata={"source": "production_failure", "priority": "high"},
    source_trace_id="9d1b6c3e7f9a4d8bb1e2c3a4f5d6e7a8",
)
```

Use datasets for:

- top user workflows
- historical regressions
- safety cases
- edge cases
- important tenants or account types
- model migration tests

Dataset best practices:

- Include both common workflows and hard edge cases.
- Preserve the production source trace when a dataset item came from a real failure.
- Keep expected outputs structured enough for evaluators to use.
- Version prompts, retrieval strategy, model, and code in experiment metadata.
- Add newly discovered failure modes continuously.
- Keep personally identifiable or sensitive data out of reusable datasets unless your governance model explicitly allows it.

## Experiments

Use experiments to compare prompt, model, retrieval, or agent changes before production rollout.

```python
from langfuse import get_client

langfuse = get_client()
dataset = langfuse.get_dataset("support-rag-regression")


def task(*, item, **kwargs):
    return answer_question(item.input["question"], prompt_version="prompt-v18")


def evaluator(*, input, output, expected_output, metadata=None):
    cited = set(output.get("citations", []))
    expected = set(expected_output.get("must_cite", []))
    return {
        "name": "required_citations_present",
        "value": 1 if expected.issubset(cited) else 0,
        "data_type": "BOOLEAN",
    }


result = langfuse.run_experiment(
    name="support-rag-regression",
    run_name="prompt-v18",
    data=dataset.items,
    task=task,
    evaluators=[evaluator],
    metadata={"candidate": "prompt-v18"},
)
```

Keep experiment names and run names stable enough that dashboards and release notes can compare them over time.

For datasets hosted in Langfuse, you can also run from the dataset client so the run is automatically linked to the dataset in the UI:

```python
from langfuse import get_client

langfuse = get_client()
dataset = langfuse.get_dataset("support-rag-regression")

result = dataset.run_experiment(
    name="prompt-v18",
    description="Candidate prompt for support RAG",
    task=task,
    evaluators=[evaluator],
)

print(result.format())
```

Experiment runner capabilities to rely on in production:

- concurrent execution with configurable limits;
- automatic tracing of each task execution;
- item-level and run-level evaluators;
- error isolation so one failed item does not stop the full run;
- dataset-run comparison in the Langfuse UI for hosted datasets.

## Langfuse Metrics

Langfuse metrics derive from traces and scores. Use them for:

- quality trends by score
- cost by model, user, tenant tier, or prompt version
- latency by trace name and release
- volume by trace name, model, tags, or user segment
- token usage trends

Common dashboards:

| Dashboard | Useful charts |
| --- | --- |
| Executive AI quality | acceptance rate, groundedness, safety pass rate, cost |
| Model migration | quality, p95 latency, cost, error traces by old vs new model |
| Prompt rollout | score distributions by prompt version |
| RAG health | empty retrieval rate, groundedness, citation validity |
| Agent health | tool failure rate, step count, loop stops, final answer quality |

## Alerting Philosophy

Langfuse is excellent for LLM quality and trace analytics. For paging, use a metrics or incident system that can evaluate alert rules continuously.

Use two alert layers:

1. Operational alerts from OpenTelemetry metrics: latency, errors, saturation, queue depth, provider failures.
2. AI quality alerts from Langfuse metrics/scores exported or queried into your alerting workflow: feedback, groundedness, safety, cost, token spikes.

Do not page on every single bad answer. Page on sustained user impact or safety-critical failures.

## Practical Alert Ideas

| Alert | Source | Why it matters |
| --- | --- | --- |
| High HTTP 5xx rate | OTel metrics | Service is failing |
| High LLM provider error rate | OTel metrics or traces | Model calls are failing |
| LLM p95 latency high | OTel metrics | User experience degradation |
| Token usage per request spike | OTel metrics or Langfuse metrics | Cost or prompt regression |
| User thumbs-down rate high | Langfuse scores | Quality regression |
| Groundedness score low | Langfuse scores | RAG hallucination risk |
| Safety pass rate drops | Langfuse scores | Trust and compliance risk |
| Agent tool failure rate high | OTel metrics and Langfuse traces | Broken tools or bad plans |
| Empty retrieval rate high | OTel metrics | Search/index issue |
| Experiment regression | Langfuse experiments | Block release before production |

## Example: Export Quality Metrics to Alerting

One production pattern is a scheduled job that queries Langfuse metrics or scores and writes compact time series to your metrics backend.

```python
from dataclasses import dataclass


@dataclass
class QualityWindow:
    trace_name: str
    score_name: str
    average: float
    count: int


def publish_quality_metrics(windows: list[QualityWindow]) -> None:
    for window in windows:
        metrics.gauge(
            "llm_quality_score",
            window.average,
            tags={
                "trace_name": window.trace_name,
                "score_name": window.score_name,
            },
        )
        metrics.gauge(
            "llm_quality_score_count",
            window.count,
            tags={
                "trace_name": window.trace_name,
                "score_name": window.score_name,
            },
        )
```

Keep alert dimensions low-cardinality. Alert by workflow, environment, release, and model; investigate individual traces in Langfuse.

## Release Gates

Before deploying a prompt, model, retrieval, or agent change:

- run dataset experiments;
- compare against the current production version;
- check quality, latency, cost, and safety scores;
- inspect failures manually;
- promote only if the candidate beats or matches current behavior on important segments;
- add a temporary production dashboard filtered by the new release/version.

This turns Langfuse from a passive trace viewer into an engineering control system.

## CI/CD Evaluation Gates

Run a small but representative experiment suite in CI for changes that affect prompts, retrieval, models, tools, or agent logic.

Gate on:

- minimum average quality score;
- no regression against the current production baseline;
- safety pass rate;
- maximum p95 latency for the test run;
- maximum token or cost budget per answer;
- no critical failures in required categories.

Example shape:

```python
def assert_release_gate(result) -> None:
    scores = result.run_evaluations
    average_relevance = next(s.value for s in scores if s.name == "average_relevance")
    safety_pass_rate = next(s.value for s in scores if s.name == "safety_pass_rate")

    if average_relevance < 0.82:
        raise SystemExit("release gate failed: relevance below threshold")

    if safety_pass_rate < 0.99:
        raise SystemExit("release gate failed: safety pass rate below threshold")
```

CI gates should be conservative and stable. Use them to block obvious regressions, then use production Langfuse dashboards and alerts to monitor the long tail.
