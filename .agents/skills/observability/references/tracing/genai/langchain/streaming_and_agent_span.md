# The Agent Span, Streaming, and Conversation Correlation

The outer span is the unit everything else hangs off. Nothing in LangChain emits it, so you write it.

---

## Why it has to exist

Without it, model and tool spans attach directly to the HTTP or worker span. You then have no span whose duration is "one agent invocation," which means no `gen_ai.invoke_agent.duration`, no model-calls-per-invocation, no tool-calls-per-invocation — the three signals that detect an agent regressing into a loop.

```
POST /chat                          SERVER
  invoke_agent support_agent        <- this one
    chat gpt-5
    execute_tool web_search
    chat gpt-5
```

---

## Non-streaming

```python
# agents/observability/agent_span.py
import asyncio
import time

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from observability.agent_counters import invocation_counters
from observability.genai_attributes import (
    ERROR_TYPE,
    GENAI_AGENT_NAME,
    GENAI_CONVERSATION_ID,
    GENAI_OPERATION_NAME,
)
from observability.genai_metrics import record_agent_invocation

tracer = trace.get_tracer(__name__)


def record_agent_result(*, started_at, agent_name, error_type, counters) -> None:
    """One duration and one pair of fan-out observations per invocation."""
    record_agent_invocation(
        duration_s=time.perf_counter() - started_at,
        agent_name=agent_name,
        error_type=error_type,
        inference_calls=counters.inference_calls,
        tool_calls=counters.tool_calls,
    )


async def invoke_agent(
    agent,
    messages: list,
    *,
    agent_name: str,
    conversation_id: str | None = None,
) -> dict:
    started_at = time.perf_counter()
    error_type: str | None = None

    # The callback and the tool middleware increment these; this wrapper is
    # the only thing that reads them. See ../../../metrics/genai.md.
    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        record_exception=False,
        attributes={
            GENAI_OPERATION_NAME: "invoke_agent",
            GENAI_AGENT_NAME: agent_name,
        },
    ) as span, invocation_counters() as counters:
        if conversation_id:
            span.set_attribute(GENAI_CONVERSATION_ID, conversation_id)

        try:
            result = await agent.ainvoke({"messages": messages})
        except asyncio.CancelledError:
            error_type = "cancelled"
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        finally:
            record_agent_result(
                started_at=started_at,
                agent_name=agent_name,
                error_type=error_type,
                counters=counters,
            )

        # Bounded facts about how the agent behaved on this run.
        span.set_attribute("app.agent.message_count", len(result["messages"]))
        return result
```

Note what is **not** here: an `agent.duration` attribute. The span's own start and end times already carry that, and the metric carries the aggregate. A duplicate attribute is a second number to keep consistent with the first.

The `invocation_counters()` scope is what makes model-calls-per-invocation and tool-calls-per-invocation possible at all — nothing in LangChain counts them for you. The `ContextVar` it wraps, and the increments the callback and tool middleware make into it, are in `../../../metrics/genai.md`. `record_agent_result()` is shared by every wrapper so streaming cannot silently omit the required fan-out metrics.

---

## Streaming: this file owns the agent-level TTFC

`app.agent.time_to_first_chunk` — agent invocation to the first chunk the caller sees, including planning, retrieval, and tool calls. It is a different number from the model's `gen_ai.response.time_to_first_chunk` (`model_callback.md`) and from the API's first byte. The full taxonomy and why each needs its own attribute: `../attributes.md`.

## Stream modes

| `stream_mode` | Yields | Use for |
| --- | --- | --- |
| `"updates"` | state updates after each agent step | step-level progress; **not** token latency |
| `"messages"` | LLM tokens with metadata, as generated | token streaming and agent TTFC |
| `"values"` | the full state after each step | rarely useful for telemetry — large payloads |
| a list, e.g. `["updates", "messages"]` | both, interleaved and tagged | UIs that show progress and tokens |

**Do not compute TTFC from `"updates"`.** An update arrives when a graph node finishes, which is long after the first token left the model. It measures step completion and will be several times the real number.

Every template below passes `version="v2"` explicitly. LangGraph >=1.1 then
returns a `StreamPart` dictionary with `type`, `ns`, and `data` regardless of
whether one or several stream modes are selected. The v1 default yields tuples
for multiple modes; do not mix the two schemas. See `../../../compatibility.md`.

### Token streaming

```python
from observability.genai_attributes import GENAI_CONVERSATION_ID
from observability.genai_metrics import record_agent_time_to_first_chunk


async def stream_agent_tokens(
    agent, messages: list, *, agent_name: str, conversation_id: str | None = None
):
    started_at = time.perf_counter()
    first_chunk_seen = False
    error_type: str | None = None

    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        record_exception=False,
        attributes={
            GENAI_OPERATION_NAME: "invoke_agent",
            GENAI_AGENT_NAME: agent_name,
            "gen_ai.request.stream": True,
        },
    ) as span, invocation_counters() as counters:
        if conversation_id:
            span.set_attribute(GENAI_CONVERSATION_ID, conversation_id)

        try:
            async for part in agent.astream(
                {"messages": messages},
                stream_mode="messages",
                version="v2",
            ):
                if part["type"] != "messages":
                    continue
                token, metadata = part["data"]
                if not first_chunk_seen:
                    elapsed = time.perf_counter() - started_at
                    # Organisation-owned: there is no standard agent-level
                    # TTFC attribute. gen_ai.response.time_to_first_chunk
                    # belongs to a single model request, not to an agent run.
                    span.set_attribute("app.agent.time_to_first_chunk", elapsed)
                    record_agent_time_to_first_chunk(elapsed, agent_name=agent_name)
                    first_chunk_seen = True

                yield token
        except asyncio.CancelledError:
            error_type = "cancelled"
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        finally:
            record_agent_result(
                started_at=started_at,
                agent_name=agent_name,
                error_type=error_type,
                counters=counters,
            )
```

### Step updates

```python
async def stream_agent_updates(agent, messages: list, *, agent_name: str):
    started_at = time.perf_counter()
    step_count = 0
    error_type: str | None = None

    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        record_exception=False,
        attributes={
            GENAI_OPERATION_NAME: "invoke_agent",
            GENAI_AGENT_NAME: agent_name,
        },
    ) as span, invocation_counters() as counters:
        try:
            async for part in agent.astream(
                {"messages": messages},
                stream_mode="updates",
                version="v2",
            ):
                if part["type"] != "updates":
                    continue
                update = part["data"]
                step_count += 1
                yield update
        except asyncio.CancelledError:
            error_type = "cancelled"
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        finally:
            span.set_attribute("app.agent.step_count", step_count)
            record_agent_result(
                started_at=started_at,
                agent_name=agent_name,
                error_type=error_type,
                counters=counters,
            )
```

### Both at once

```python
async for part in agent.astream(
    {"messages": messages},
    stream_mode=["updates", "messages"],
    version="v2",
):
    stream_mode = part["type"]
    chunk = part["data"]
    if stream_mode == "updates":
        step_count += 1
        yield {"type": "update", "data": chunk}
    elif stream_mode == "messages":
        token, metadata = chunk
        if not first_chunk_seen:
            span.set_attribute(
                "app.agent.time_to_first_chunk", time.perf_counter() - started_at
            )
            first_chunk_seen = True
        yield {"type": "token", "data": token.content}
```

Because the call opts into v2, both single-mode and multi-mode streams use the
same dictionary shape. If the service deliberately stays on v1, put tuple
unpacking behind one adapter and pin that decision in its dependency lock.

---

## The generator must always finish

The span ends in the generator's `finally`, which only runs when the generator is exhausted, closed, or garbage-collected. If a client disconnects mid-stream and the caller abandons the generator, the span can stay open indefinitely.

Guard at the caller — for FastAPI, `StreamingResponse` closes the generator on disconnect, but confirm it on a real disconnect test. If the span is missing from a trace whose model spans are present, this is the cause.

Also call the callback's `abandon_runs_older_than()` (see `model_callback.md`) here, so a cancelled stream does not leave model spans open too.

---

## Conversation correlation

The agent span is where `gen_ai.conversation.id` goes in a LangChain service — it is the root of each turn. Both wrappers above already accept and set it.

The rule it implements (one trace per turn, never one trace per conversation, never a metric attribute) is in `../attributes.md`.

---

## Business attributes worth setting on the agent span

Read the agent's actual behaviour and record the bounded facts that would matter in a review:

| Attribute | Why |
| --- | --- |
| `app.agent.step_count` | Detects loops |
| `app.agent.stop_reason` | `completed` / `step_limit` / `guardrail` / `cancelled` — bounded |
| `app.agent.fallback.used` | Whether a model or workflow fallback fired |
| `app.outcome` | `success` / `error` / `timeout` / `blocked` |
| `app.workflow.version` | Which prompt/agent version produced this run |

Keep every value bounded — these are the ones you will want as metric dimensions too.

---

## Multi-agent workflows

When one user request coordinates several agents, add a workflow span above them:

```
invoke_workflow support_rag         gen_ai.operation.name=invoke_workflow
  invoke_agent triage_agent
  invoke_agent support_agent
    chat gpt-5
    execute_tool order_lookup
```

Use `invoke_workflow` for the coordinating process and `invoke_agent` for each agent execution. `gen_ai.workflow.name` on the workflow span; `gen_ai.agent.name` on each agent span.

---

## Verify before moving on

- Exactly one `invoke_agent <name>` span per invocation.
- Every model and tool span is a **child** of it, not a sibling. If they are siblings, context was lost — check that the agent runs in the same task/context where the span was made current.
- Streaming runs have `app.agent.time_to_first_chunk`, and it is **larger** than the first model span's `gen_ai.response.time_to_first_chunk`. If they are equal, one of them is measuring the wrong thing.
- Both streaming wrappers record `gen_ai.invoke_agent.inference_calls` and `gen_ai.invoke_agent.tool_calls` once, with values matching child spans even on error or cancellation.
- Every stream call opts into v2 and consumes `StreamPart["type"]` / `["data"]`; no v1 tuple unpacking remains.
- Three turns of one conversation produce three traces sharing `gen_ai.conversation.id`.
- A cancelled stream still ends the agent span.
- `gen_ai.conversation.id` appears on no metric.

---

## Then

- metrics: `../../../metrics/genai.md`
- logging: `../../../logging/genai.md`
- final checks: `../../../verification.md`
