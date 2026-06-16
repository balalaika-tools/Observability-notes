# Production Observability Workflows

Production Langfuse instrumentation should make real incidents and product questions easier to answer.

Good traces are not just pretty trees. They connect user impact, model behavior, cost, quality, and deploy history.

## What to Capture

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

## Trace Design by Workflow

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

## Environments, Releases, and Versions

Use all three consistently:

- Environment: where it ran, such as `prod`, `staging`, or `dev`.
- Release: deployed software version, such as Git SHA or image tag.
- Version: workflow, prompt, chain, or agent version.

Example:

```python
import os
from langfuse import get_client, propagate_attributes

langfuse = get_client()


def run_workflow(user_id: str, session_id: str) -> None:
    with langfuse.start_as_current_observation(as_type="span", name="agent.answer"):
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            trace_name="agent.answer",
            version=os.getenv("PROMPT_VERSION", "prompt-dev"),
            metadata={
                "environment": os.getenv("ENVIRONMENT", "dev"),
                "release": os.getenv("RELEASE", "local"),
                "agentVersion": os.getenv("AGENT_VERSION", "local"),
            },
            tags=["agent"],
        ):
            execute_agent()
```

Keep release and environment values low-cardinality and stable.

## Privacy and Data Minimization

Production LLM traces can contain sensitive content. Decide what to capture before broad rollout.

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

## Sampling Strategy

Sampling is a tradeoff. For LLM systems, the rare traces are often the most valuable.

Common strategy:

- Keep all errors.
- Keep all user-thumbs-down traces.
- Keep all safety or guardrail failures.
- Keep all traces from new releases for a short burn-in window.
- Sample successful high-volume traffic.
- Keep a representative sample per trace name, model, tenant tier, and release.

If sampling outside Langfuse with the Collector, make sure complete traces reach Langfuse. Dropping key spans can make agent and RAG workflows hard to debug.

## Operational Triage

When an incident happens, move from metrics to Langfuse traces:

1. Metrics alert fires: high latency, errors, cost spike, or quality drop.
2. Open Langfuse filtered by trace name, release, model, tag, user segment, or score.
3. Inspect slow or failed traces.
4. Compare generations across versions or releases.
5. Look at scores and feedback distribution.
6. Turn representative failures into dataset items.
7. Run an experiment before shipping the fix.

## Quality Triage

For a drop in answer quality:

1. Filter traces by score name and low score value.
2. Group by prompt version, release, model, retrieval strategy, and tenant tier.
3. Inspect retrieved documents and tool calls.
4. Check whether failures are retrieval, prompt, model, tool, or policy problems.
5. Add examples to a dataset.
6. Run experiments across candidate prompts or models.
7. Promote the change only after offline and online metrics improve.

## Cost Triage

For a token or cost spike:

1. Group by trace name, model, user segment, release, and prompt version.
2. Inspect long-context generations and repeated tool loops.
3. Check whether retrieval returned too many documents.
4. Check whether agent max steps or retry logic changed.
5. Add OpenTelemetry metrics for tokens per request and agent step count if not already present.
6. Alert on sustained spikes, not one-off large requests.

## Multi-Service Production Pattern

For a gateway calling downstream LLM services:

- gateway creates the root span and user/session attributes;
- gateway propagates W3C trace context;
- gateway uses baggage only for allowlisted Langfuse attributes;
- downstream services create child spans;
- LLM services use Langfuse `generation`, `tool`, `retriever`, and `agent` observations;
- OpenTelemetry metrics are exported to the metrics backend for SLO alerts.

The gateway should not need to know every downstream LLM detail. It should only establish request identity and trace continuity.

## What to Put in Langfuse vs Metrics vs Logs

| Data | Best home |
| --- | --- |
| Prompt, generation output, model, tokens | Langfuse |
| User feedback and evaluator results | Langfuse scores |
| Cost/latency by prompt version or model | Langfuse metrics and dashboards |
| HTTP request rate, p95 latency, error rate | OTel metrics backend |
| Queue depth, worker saturation, CPU, memory | OTel metrics backend |
| Exception stack traces and audit events | Log backend |
| Trace and span IDs | All systems for correlation |

## Common Production Pitfalls

- Capturing everything without a privacy review.
- Naming traces with user IDs or request IDs.
- Forgetting release/version fields, making regressions hard to isolate.
- Using tags for values that should be scores.
- Using scores for values that are only labels.
- Not recording token usage on generations.
- Propagating baggage too broadly.
- Sampling away failure traces.
- Sending only LLM leaf spans to Langfuse while losing the request context.
- Treating Langfuse dashboards as the only alerting mechanism.
