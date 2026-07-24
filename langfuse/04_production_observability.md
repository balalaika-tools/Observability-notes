# Production Observability Workflows

Last verified against official Langfuse observability documentation on 2026-07-20.

## 🧭 Mental Model

Production Langfuse instrumentation should make real incidents and product questions easier to answer.

Good traces are not just pretty trees. They connect user impact, model behavior, cost, quality, and deploy history.

Use Langfuse as the AI workflow layer in a broader observability system:

```text
user impact / alert / feedback
  -> metrics identify unhealthy service, route, model, release, or score
  -> Langfuse traces explain prompt/retrieval/tool/model behavior
  -> logs provide exact exceptions and audit evidence
  -> datasets and experiments verify the fix
```

Langfuse solves "what happened inside the LLM workflow and was it good?" Native Monitors can notify on supported observation and score cost, latency, volume, and quality thresholds. External metrics and incident systems remain necessary for infrastructure SLOs, unsupported signals, and richer paging/escalation. Langfuse also does not replace logs for forensic detail or privacy controls that decide what may leave the application.

> 💡 **Key insight:** Langfuse is the LLM behavior layer of your observability stack — not a replacement for it; wire it alongside your metrics backend and log system, not instead of them.

## 📦 What to Capture

Capture enough to debug and evaluate the workflow:

- trace name and observation names
- user and session identifiers
- release and prompt or workflow version
- model and provider
- input and output where privacy rules allow it
- retrieval metadata: query, document IDs, scores, and corpus version
- tool names, inputs, outputs, and errors
- guardrail decisions
- token usage
- scores and user feedback

Do not capture secrets, credentials, payment data, raw medical data, or sensitive documents unless your legal, security, and compliance model explicitly allows it.

Layered capture guidance:

- Beginner intuition: capture the decision trail, not every byte.
- Technical mechanics: root observations hold workflow input/output; child observations hold step-specific input/output, metadata, status, and timing.
- Production implications: capture policy affects privacy, retention, dashboard value, and whether traces can be shared in incidents.
- Common mistakes: recording huge documents in metadata, disabling all capture and losing debuggability, or assuming provider SDK inputs are automatically safe.

## 🗺️ Trace Design by Workflow

### Chat

```text
chat.answer
  input.normalize
  memory.load
  llm.generate_answer
  guardrail.output_policy
  response.persist
```

Useful dimensions:

- `user_id`
- `session_id`
- `release`
- `version`
- `model`
- `tenant_tier`
- `conversation_mode`

Tradeoffs:

- Capture conversation memory IDs by default; capture full memory content only with a privacy decision.
- Add user feedback scores to the trace the user saw, not the next request.
- Use sessions for multi-turn chat so reviewers can inspect context across traces.

### RAG

```text
rag.answer
  query.rewrite
  rag.retrieve
  rag.rerank
  llm.generate_answer
  evaluator.groundedness
```

Useful retrieval metadata:

- retrieval strategy
- index name and version
- top-k
- document IDs
- retrieval scores
- empty retrieval count
- reranker model

Keep retrieved document text out of metadata. Store small snippets only when allowed and useful.

Tradeoffs:

- Document IDs and scores are usually enough for debugging retrieval ranking.
- Snippets help explain bad answers but increase privacy and retention risk.
- Empty retrieval rate belongs in metrics; representative empty-retrieval traces belong in Langfuse.

### Agent

```text
agent.answer
  agent.plan
  tool.search
  tool.lookup_account
  llm.reflect
  guardrail.tool_policy
  llm.final_answer
```

Useful agent metadata:

- max steps
- actual steps
- tool call count
- failed tool count
- stop reason
- planner version
- policy version

Tradeoffs:

- Record each meaningful tool call; do not hide the entire loop inside one generation.
- Use a max-steps value and stop reason so loops and early exits are obvious.
- Record tool failures as both Langfuse observation errors and low-cardinality OTel metrics.

## 🏷️ Environments, Releases, and Versions

Use all three consistently:

- Environment: where it ran, such as `prod`, `staging`, or `dev`.
- Release: deployed software version, such as Git SHA or image tag.
- Version: workflow, prompt, chain, or agent version.

Example:

```python
import os
from langfuse import Langfuse, propagate_attributes

langfuse = Langfuse(
    release=os.environ["LANGFUSE_RELEASE"],
    environment=os.environ["LANGFUSE_TRACING_ENVIRONMENT"],
)


def run_workflow(user_id: str, session_id: str) -> None:
    with langfuse.start_as_current_observation(as_type="span", name="agent.answer"):
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            trace_name="agent.answer",
            environment=os.environ["LANGFUSE_TRACING_ENVIRONMENT"],
            version=os.getenv("PROMPT_VERSION", "prompt-dev"),
            metadata={
                "agentVersion": os.getenv("AGENT_VERSION", "local"),
            },
            tags=["agent"],
        ):
            execute_agent()
```

Set `LANGFUSE_RELEASE` to the deployed artifact and `LANGFUSE_TRACING_ENVIRONMENT` to the deployment environment before client initialization. `propagate_attributes(environment=...)` may override the environment for a request-scoped execution. Do not put `environment` or `release` in generic metadata, and do not use `version` as a release substitute. Keep all three values low-cardinality and stable.

Production convention:

| Field | Recommended value | Do not use |
| --- | --- | --- |
| Environment | `prod`, `staging`, `dev` | Per-developer names in production dashboards |
| Release | Git SHA, image tag, build ID | Prompt version or request ID |
| Version | Prompt/workflow/agent/evaluator version | Git SHA unless it is truly the logical workflow version |
| Tags | Feature/workflow labels | Quality judgments or user-specific values |
| Metadata | Low-cardinality segment fields | Secrets, raw docs, unbounded unique keys |

## 🔒 Privacy and Data Minimization

Production LLM traces can contain sensitive content. Decide what to capture before broad rollout.

> ⚠️ **Watch out:** Provider SDK integrations (e.g., the OpenAI wrapper) capture inputs and outputs automatically — without an explicit capture policy and masking tests in place before rollout, sensitive data reaches Langfuse silently.

Recommended controls:

- Disable capture on sensitive functions with `capture_input=False` and `capture_output=False`.
- Store document IDs instead of full document bodies when possible.
- Redact secrets before values enter Langfuse.
- Avoid putting PII into baggage, tags, or metadata.
- Use tenant or user surrogate IDs.
- Separate production and development projects if access policies differ.
- Review retention and export policies for your compliance requirements.

Example:

```python
from langfuse import observe


@observe(
    as_type="tool",
    name="tool.get_payment_status",
    capture_input=False,
    capture_output=False,
)
def get_payment_status(account_id: str) -> dict:
    return billing_api.payment_status(account_id)
```

Privacy review questions:

- Which fields can leave the application boundary?
- Which fields may be stored in a third-party SaaS or self-hosted Langfuse instance?
- Which identifiers must be hashed or tokenized?
- How long should production traces and datasets be retained?
- Can traces be shared through public links, and who can enable that?
- Which traces can be promoted into reusable datasets?
- Are masking tests part of CI or staging validation?

## 📐 Sampling Strategy

Sampling is a tradeoff. For LLM systems, the rare traces are often the most valuable.

`LANGFUSE_SAMPLE_RATE` and ordinary SDK samplers make a head decision when the root starts. They cannot see a later error, thumbs-down, safety score, or final cost. A later score cannot restore an excluded trace, and scores attached to an unsampled trace are not sent.

> ⚠️ **Watch out:** If you rely on negative user feedback to identify bad traces, head-sampling can silently discard those traces before the feedback arrives — they are gone and unrecoverable.

An implementable strategy is:

- Retain 100 percent of a bounded candidate stream until an outcome-aware decision is possible.
- Use start-time head rules only for facts already known: new release, environment, route, internal test traffic, or bounded tenant tier.
- If final errors, tool failures, latency, or safety attributes decide retention, send complete candidate traces to a trace-ID-routed Collector tail-sampling tier.
- If user feedback arrives after export, retain the original candidate trace up front; feedback cannot rescue a head-dropped trace.
- Sample remaining successful high-volume traffic by a documented probability.

If sampling outside Langfuse with the Collector, make sure complete traces reach Langfuse. Dropping key spans can make agent and RAG workflows hard to debug.

Sampling decision points:

| Goal | Strategy | Risk |
| --- | --- | --- |
| Reduce routine volume | Sample successful traces by workflow/model/tenant tier | May miss slow-burn quality regressions. |
| Protect high-risk visibility | Buffer candidate traces and tail-select completed errors/safety attributes; retain feedback candidates up front | Costs memory/bandwidth and temporarily holds all candidate payloads. |
| Control cost centrally | Collector tail/head sampling and routing | Incomplete traces if filters are span-level and too aggressive. |
| Debug a rollout | Temporarily raise sample rate for one release/version | Must remember to return to normal after burn-in. |

Tail sampling requires all spans for a trace to reach the same decision point and arrive within its decision window. Size active-trace capacity from peak new traces per second, trace duration, late arrival, and payload size. Apply masking before buffering; sampling is never a privacy control.

## 📊 Alerting Architecture

Use native Langfuse Monitors first when the signal is available from observations or numeric/categorical scores:

1. Choose the data source and metric, such as p95 generation latency, summed cost, observation count, average groundedness, or categorical failure count.
2. Filter by environment, trace/observation name, model, tags, release, or other bounded dimensions.
3. Set a required alert threshold, optional warning threshold, and an evaluation window.
4. Choose no-data behavior explicitly: treat as zero, keep previous severity, show `NO_DATA`, or notify after sustained no data.
5. Enable renotification only when repeated notification adds operational value.
6. Link Slack, an HMAC-verified webhook, or GitHub Actions automation.

Keep OpenTelemetry metrics plus the incident platform for HTTP/service SLOs, queue depth, CPU/memory, Collector/exporter health, signals unsupported by Monitors, and advanced routing, deduplication, paging, or escalation. A Langfuse webhook can bridge a supported monitor into that broader incident workflow.

## 🔍 Operational Triage

When an incident happens, move from metrics to Langfuse traces:

1. Metrics alert fires: high latency, errors, cost spike, or quality drop.
2. Open Langfuse filtered by trace name, release, model, tag, user segment, or score.
3. Inspect slow or failed traces.
4. Compare generations across versions or releases.
5. Look at scores and feedback distribution.
6. Turn representative failures into dataset items.
7. Run an experiment before shipping the fix.

Incident metadata to preserve:

- alert name and time window
- service, route, model, release, environment
- affected trace names and score names
- representative Langfuse trace URLs or trace IDs
- suspected category: retrieval, prompt, model, tool, guardrail, provider, infrastructure
- dataset items created from the incident

## 🔍 Quality Triage

For a drop in answer quality:

1. Filter traces by score name and low score value.
2. Group by prompt version, release, model, retrieval strategy, and tenant tier.
3. Inspect retrieved documents and tool calls.
4. Check whether failures are retrieval, prompt, model, tool, or policy problems.
5. Add examples to a dataset.
6. Run experiments across candidate prompts or models.
7. Promote the change only after offline and online metrics improve.

Quality failure categories:

| Category | Evidence to inspect |
| --- | --- |
| Retrieval | Empty results, low scores, wrong corpus/index, stale document IDs |
| Prompt | Prompt version, missing instructions, overly long context, bad examples |
| Model | Model name, parameters, provider errors, refusal/drift pattern |
| Tool | Tool inputs/outputs, exceptions, latency, permissions, stale schema |
| Guardrail | Policy version, false positive/negative, blocked output |
| UX/product | User asked unsupported task, missing clarification flow, bad handoff |

## 📊 Cost Triage

For a token or cost spike:

1. Group by trace name, model, user segment, release, and prompt version.
2. Inspect long-context generations and repeated tool loops.
3. Check whether retrieval returned too many documents.
4. Check whether agent max steps or retry logic changed.
5. Add OpenTelemetry metrics for tokens per request and agent step count if not already present.
6. Alert on sustained spikes, not one-off large requests.

Cost controls:

- Store prompt/workflow version on every trace.
- Track input and output tokens by model and workflow.
- Cap retrieval top-k and agent max steps.
- Add budget tests for prompts and context builders.
- Watch model migrations with side-by-side Langfuse dashboards.

## 🗺️ Multi-Service Production Pattern

For a gateway calling downstream LLM services:

- gateway creates the root span and user/session attributes;
- gateway propagates W3C trace context;
- gateway uses baggage only for allowlisted Langfuse attributes;
- downstream services create child spans;
- LLM services use Langfuse `generation`, `tool`, `retriever`, and `agent` observations;
- OpenTelemetry metrics are exported to the metrics backend for SLO alerts.

The gateway should not need to know every downstream LLM detail. It should only establish request identity and trace continuity.

Responsibilities:

| Component | Owns |
| --- | --- |
| Gateway | User/session identity, public route, request validation, trace root, W3C propagation. |
| Agent/RAG service | Agent loop, retrieval, tool calls, generations, guardrails, LLM-specific scores. |
| Collector | Redaction, batching, routing, resource attributes, backend-specific exporters. |
| Metrics backend | SLO alerts, saturation, provider error rate, latency, queue health. |
| Langfuse | Trace inspection, quality/cost analytics, score workflows, native Monitors, datasets, experiments. |
| Logs backend | Exceptions, stack traces, audit events, operational details. |

## 📐 What to Put in Langfuse vs Metrics vs Logs

| Data | Best home |
| --- | --- |
| Prompt, generation output, model, tokens | Langfuse |
| User feedback and evaluator results | Langfuse scores |
| Cost/latency by prompt version or model | Langfuse metrics and dashboards |
| HTTP request rate, p95 latency, error rate | OTel metrics backend |
| Queue depth, worker saturation, CPU, memory | OTel metrics backend |
| Exception stack traces and audit events | Log backend |
| Trace and span IDs | All systems for correlation |

## ⚠️ Common Production Pitfalls

- Capturing everything without a privacy review.
- Naming traces with user IDs or request IDs.
- Forgetting release/version fields, making regressions hard to isolate.
- Using tags for values that should be scores.
- Using scores for values that are only labels.
- Not recording token usage on generations.
- Propagating baggage too broadly.
- Sampling away failure traces.
- Sending only LLM leaf spans to Langfuse while losing the request context.
- Creating dashboards but no native Monitor or external alert for sustained user impact.

## ✅ Testing and Operational Checks

Before production:

- Run one known chat/RAG/agent request in staging and confirm the trace tree is complete.
- Confirm user/session/release/version/environment filters work in Langfuse.
- Verify token usage appears on generations.
- Submit sample feedback and confirm it attaches to the correct trace.
- Trigger a representative tool failure and confirm both Langfuse and metrics record it.
- Run redaction tests against prompts, retrieved docs, tool outputs, headers, and exceptions.
- Validate Collector config and confirm no root spans are dropped.
- Confirm short-lived workers call `flush()` before exit.

After production rollout:

- Compare the new release/version to the previous one for quality, latency, cost, and error traces.
- Review low-score traces daily during burn-in.
- Promote representative failures into datasets.
- Revisit sampling and capture policy after traffic volume is known.

## 🔍 Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Incident alert fires but Langfuse has no useful trace | Workflow not instrumented or traces sampled/dropped | Add root/product spans, preserve failure traces, validate exporter/Collector path. |
| Quality regression cannot be isolated | Missing release/version/prompt metadata | Add release and workflow/prompt version to all traces; update rollout checklist. |
| RAG failures are opaque | Retriever output lacks document IDs/scores/index metadata | Record safe retrieval evidence and empty-retrieval metrics. |
| Agent loops are hard to diagnose | Steps/tools not represented as observations | Record agent, tool, generation, stop reason, max steps, and tool failures. |
| Privacy issue found in traces | Capture policy too broad or masking failed | Disable capture on sensitive paths, add masking tests, purge/handle affected data per policy. |
| High-cardinality dashboards are unusable | Request/user-specific values used as names/tags/metric labels | Move variable values to safe metadata/input/output; keep names and labels stable. |
| Scores do not align with user complaints | Feedback attached to wrong trace or score semantics changed | Store trace ID with UI response; define score configs and evaluator versions. |
| Datasets do not catch regressions | Dataset has easy/stale examples only | Add production failures, edge cases, and reviewed expected outputs continuously. |

## ✅ Production Checklist

- Define trace designs for chat, RAG, agent, guardrail, and evaluator workflows.
- Decide capture, masking, retention, and access policy before broad rollout.
- Set environment, release, version, user/session, tags, and metadata consistently.
- Use native Langfuse Monitors for supported observation/score thresholds and an external metrics/incident stack for infrastructure SLOs, unsupported signals, and escalation.
- Preserve complete traces for errors, safety failures, negative feedback, and new releases.
- Record token usage and model parameters on generations.
- Record retriever IDs/scores/index metadata and tool input/output status.
- Attach user feedback, guardrail outcomes, and evaluator results as scores.
- Turn representative production failures into dataset items.
- Validate staging traces, dashboards, alerts, redaction, and Collector filters before production.

## 🔌 Official References

- Langfuse concepts: <https://langfuse.com/docs/observability/data-model>
- SDK instrumentation: <https://langfuse.com/docs/observability/sdk/instrumentation>
- SDK advanced features: <https://langfuse.com/docs/observability/sdk/advanced-features>
- OpenTelemetry ingestion: <https://langfuse.com/integrations/native/opentelemetry>
- Metrics overview: <https://langfuse.com/docs/metrics/overview>
- Monitors: <https://langfuse.com/docs/metrics/features/monitors>
