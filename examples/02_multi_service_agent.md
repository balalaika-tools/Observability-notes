# Example: Multi-Service Agent

Last checked against the Langfuse and OpenTelemetry documentation on 2026-07-20.

This example has:

- a gateway that authenticates the caller and owns public request/session identity;
- an internal agent service with a real, bounded model-directed tool loop;
- W3C trace context and allowlisted Langfuse baggage across HTTP;
- one outcome record for every model and tool call;
- idempotent retry and explicit downstream-failure behavior.

Use bootstrap profile A from [README.md](README.md) in both processes. Each process initializes the Langfuse trace provider/exporter and OTLP metric reader before instrumenting FastAPI or HTTPX. Set a different `OTEL_SERVICE_NAME` in each process and the same `LANGFUSE_RELEASE` / `LANGFUSE_TRACING_ENVIRONMENT` deployment values.

## 🏗️ Architecture

```text
client
  -> gateway-service SERVER span
       gateway.answer
       -> HTTPX CLIENT span + traceparent + bounded baggage
          -> agent-service SERVER span
               agent.answer (max 5 decision steps)
                 llm.decide
                 tool.search_docs or tool.lookup_account
                 ...
                 llm.decide/final_answer
```

The Langfuse client is constructed with `should_export_span=lambda span: True` in the shared bootstrap so the FastAPI and HTTPX spans that connect the services are not filtered out. Do not configure a second global trace provider in either process.

> ⚠️ **Watch out:** Configuring a second global trace provider in either service creates duplicate spans and splits trace context — the Langfuse client already owns the provider.

## 🛠️ Gateway Service

```python
# gateway_service.py
import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException
from langfuse import propagate_attributes
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from pydantic import BaseModel, Field

# This import initializes trace export and the metric reader before instrumentation.
from observability import langfuse, shutdown_observability
from privacy import safe_text


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)


class Identity(BaseModel):
    user_id: str
    tenant_id: str
    account_id: str


class ServerSession(BaseModel):
    session_id: str


def require_identity() -> Identity:
    # Verify the cookie/JWT and load opaque internal IDs. Never accept these fields
    # from AnswerRequest.
    ...


def load_session(identity: Identity = Depends(require_identity)) -> ServerSession:
    # Resolve or create the conversation in server-side storage and verify ownership.
    ...


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent_client = httpx.AsyncClient(
        base_url=os.getenv("AGENT_SERVICE_URL", "http://agent-service:8080"),
        timeout=httpx.Timeout(20.0, connect=2.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        headers={"authorization": f"Bearer {os.environ['INTERNAL_AGENT_TOKEN']}"},
    )
    try:
        yield
    finally:
        await app.state.agent_client.aclose()
        shutdown_observability()


app = FastAPI(lifespan=lifespan)
HTTPXClientInstrumentor().instrument()
FastAPIInstrumentor.instrument_app(app)


async def call_agent(payload: dict) -> dict:
    """Retry only an idempotent internal request, then return a stable public error."""
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            response = await app.state.agent_client.post("/run", json=payload)
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.1)
                continue
            raise HTTPException(status_code=504, detail="agent timed out") from exc
        except httpx.ConnectError as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.1)
                continue
            raise HTTPException(status_code=503, detail="agent unavailable") from exc

        if response.status_code >= 500:
            if attempt == 0:
                await asyncio.sleep(0.1)
                continue
            raise HTTPException(status_code=503, detail="agent unavailable")
        if response.status_code >= 400:
            # Internal 4xx responses indicate a contract/authentication failure, not a
            # caller error. Do not leak the downstream response body.
            raise HTTPException(status_code=502, detail="agent request rejected")
        return response.json()

    raise HTTPException(status_code=503, detail="agent unavailable") from last_error


@app.post("/answer")
async def answer(
    body: AnswerRequest,
    identity: Identity = Depends(require_identity),
    session: ServerSession = Depends(load_session),
) -> dict:
    request_id = str(uuid4())
    safe_question = safe_text(body.question, limit=1_000)

    with propagate_attributes(
        trace_name="agent.answer",
        user_id=identity.user_id,
        session_id=session.session_id,
        environment=os.environ["LANGFUSE_TRACING_ENVIRONMENT"],
        version=os.getenv("AGENT_VERSION", "agent-dev"),
        metadata={"entrypoint": "public-api"},
        tags=["agent", "gateway"],
        as_baggage=True,
    ):
        with langfuse.start_as_current_observation(
            as_type="span",
            name="gateway.answer",
            input={"question": safe_question},
        ) as span:
            trace_id = langfuse.get_current_trace_id()
            try:
                result = await call_agent(
                    {
                        "request_id": request_id,
                        "question": body.question,
                        # This is trusted service-to-service input derived from auth. It
                        # is not baggage and the internal route authenticates the gateway.
                        "account_id": identity.account_id,
                    }
                )
            except HTTPException as exc:
                span.update(level="ERROR", status_message=f"downstream_{exc.status_code}")
                raise

            public_result = {
                "answer": result["answer"],
                "stop_reason": result["stop_reason"],
                "steps": result["steps"],
                "langfuse_trace_id": trace_id,
            }
            span.update(
                output={
                    **public_result,
                    "answer": safe_text(public_result["answer"]),
                }
            )
            return public_result
```

Retries are safe because `request_id` is generated once and the agent service deduplicates successful results. Do not blindly retry non-idempotent tool calls. For write tools, use an operation-specific idempotency key at the tool boundary or require human confirmation.

> 💡 **Key insight:** Generating `request_id` once before retries and deduplicating on the server side makes the whole request safe to replay without executing write operations twice.

> ⚠️ **Watch out:** Do not replay write-side tool calls automatically — they need a tool-level idempotency key or explicit human confirmation, not the same retry logic as read-only requests.

## 🛠️ Agent Service

The model decides whether to call an allowed tool or return a final answer. `MAX_STEPS` bounds model decision iterations; tool names and arguments are validated before execution.

```python
# agent_service.py
import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from openai import OpenAI
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from observability import langfuse, shutdown_observability
from privacy import safe_text

MAX_STEPS = 5
MODEL = "gpt-4o-mini"
openai = OpenAI()
meter = metrics.get_meter("example.agent")

agent_steps = meter.create_histogram("agent.steps", unit="{step}")
tool_calls = meter.create_counter("agent.tool.calls", unit="{call}")
tool_failures = meter.create_counter("agent.tool.failures", unit="{call}")
llm_duration = meter.create_histogram("gen_ai.client.operation.duration", unit="s")
llm_failures = meter.create_counter("gen_ai.client.operation.failures", unit="{request}")


class RunRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=128)
    question: str = Field(min_length=1, max_length=4_000)
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")


def require_gateway() -> None:
    # Verify mTLS identity or the internal bearer token. Reject public callers.
    ...


def load_completed_request(request_id: str) -> dict | None:
    ...


def store_completed_request(request_id: str, result: dict) -> None:
    # Use a unique constraint on request_id and return the existing value on conflict.
    ...


def search_docs(query: str) -> list[dict]:
    return [{"id": "doc_001", "snippet": "Admins can reset SSO in Security settings."}]


def lookup_account(account_id: str) -> dict:
    account = account_store.get(account_id)
    # The model may see only allowlisted categories, not names or billing details.
    return {"tier": account.tier, "region": account.region}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search approved support documentation.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 1000}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_account",
            "description": "Return the authenticated account's tier and region.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


def execute_tool(name: str, raw_arguments: str, *, request: RunRequest) -> dict:
    # Normalize untrusted model output before using it as a span name or metric label.
    metric_name = name if name in {"search_docs", "lookup_account"} else "unsupported_tool"
    outcome = "success"

    with langfuse.start_as_current_observation(
        as_type="tool",
        name=f"tool.{metric_name}",
        input={"tool": metric_name},
    ) as tool:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise TypeError("tool_arguments_must_be_an_object")

            if name == "search_docs":
                query = arguments["query"]
                if not isinstance(query, str) or not query.strip():
                    raise ValueError("invalid_search_query")
                tool.update(input={"query": safe_text(query, limit=1_000)})
                value = search_docs(query[:1_000])
                traced_output = {"documents": [doc["id"] for doc in value]}
            elif name == "lookup_account":
                tool.update(input={"account_ref": "authenticated_account"})
                value = lookup_account(request.account_id)
                traced_output = {"tier": value["tier"], "region": value["region"]}
            else:
                raise ValueError("unsupported_tool")

            tool.update(output=traced_output)
            return value
        except Exception as exc:
            outcome = "error"
            error_type = type(exc).__name__
            tool_failures.add(1, {"tool.name": metric_name, "error.type": error_type})
            tool.update(level="ERROR", status_message=error_type)
            raise
        finally:
            # Exactly one call measurement, after the outcome is known.
            tool_calls.add(1, {"tool.name": metric_name, "app.outcome": outcome})


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        shutdown_observability()


app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.post("/run", dependencies=[Depends(require_gateway)])
def run_agent(request: RunRequest) -> dict:
    cached = load_completed_request(request.request_id)
    if cached is not None:
        return cached

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "Answer support questions with approved tool results. Use account tier/region "
                "when relevant, cite document IDs, and do not invent missing facts."
            ),
        },
        {"role": "user", "content": request.question},
    ]
    stop_reason = "max_steps"
    answer = "I could not complete the request within the tool-step limit."
    steps_used = 0

    with langfuse.start_as_current_observation(
        as_type="agent",
        name="agent.answer",
        input={"question": safe_text(request.question, limit=1_000)},
        metadata={"maxSteps": MAX_STEPS},
    ) as agent:
        for step in range(1, MAX_STEPS + 1):
            steps_used = step
            metric_attrs = {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": MODEL,
            }
            started = time.perf_counter()
            outcome = "success"

            with langfuse.start_as_current_observation(
                as_type="generation",
                name="llm.decide",
                model=MODEL,
                input={
                    "messages": [
                        {"role": m["role"], "content": safe_text(str(m.get("content", "")))}
                        for m in messages[-8:]
                    ]
                },
                model_parameters={"temperature": 0.1},
            ) as generation:
                try:
                    response = openai.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        temperature=0.1,
                    )
                    choice = response.choices[0]
                    message = choice.message
                    usage = response.usage
                    generation.update(
                        output={
                            "finish_reason": choice.finish_reason,
                            "content": safe_text(message.content or ""),
                            "tool_names": [call.function.name for call in message.tool_calls or []],
                        },
                        usage_details={
                            "input_tokens": usage.prompt_tokens if usage else 0,
                            "output_tokens": usage.completion_tokens if usage else 0,
                        },
                    )
                except Exception as exc:
                    outcome = "error"
                    error_type = type(exc).__name__
                    llm_failures.add(1, metric_attrs | {"error.type": error_type})
                    generation.update(level="ERROR", status_message=error_type)
                    agent.update(level="ERROR", status_message="model_call_failed")
                    raise HTTPException(status_code=503, detail="model unavailable") from exc
                finally:
                    llm_duration.record(
                        time.perf_counter() - started,
                        metric_attrs | {"app.outcome": outcome},
                    )

            messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                answer = message.content or ""
                stop_reason = "final_answer"
                break

            for call in message.tool_calls:
                name = call.function.name
                try:
                    tool_result = execute_tool(
                        name,
                        call.function.arguments or "{}",
                        request=request,
                    )
                    content = json.dumps(tool_result)
                except Exception as exc:
                    # Validation and execution failures are visible as a bounded result. The
                    # next decision may recover; arbitrary exception messages never leak.
                    content = json.dumps({"error": type(exc).__name__})

                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": content[:4_000]}
                )

        result = {"answer": answer, "steps": steps_used, "stop_reason": stop_reason}
        agent_steps.record(
            steps_used,
            {"gen_ai.agent.name": "support_agent", "app.stop_reason": stop_reason},
        )
        agent.update(
            output={"answer": safe_text(answer), "steps": steps_used},
            metadata={"stopReason": stop_reason},
        )
        store_completed_request(request.request_id, result)
        return result
```

The account result is no longer discarded: when selected, it becomes a tool message used by the next model decision. A failed search, account lookup, or model call records an error observation and an operational metric. Tool-call metrics are emitted once, after the final outcome is known.

## 🔍 Failure Policy

| Failure | Retry | Client result | Idempotency rule |
| --- | --- | --- | --- |
| Gateway connection failure | Once with short backoff | `503` | Reuse the same `request_id` |
| Gateway timeout | Once | `504` | Agent result store deduplicates the request |
| Agent `5xx` | Once | `503` | Retry only the whole read-only run |
| Agent `4xx` | No | `502` | Treat as internal auth/contract defect |
| Read-only tool failure | Agent can choose another step | Bounded error result or final failure | Count the execution once |
| Write tool failure | Operation-specific only | Explicit partial/failure result | Require a tool-level idempotency key; never replay blindly |
| Model failure | No hidden application retry in this example | Agent `503`, gateway `503` | Add bounded provider retries only if their total latency fits the deadline |

Set an end-to-end deadline shorter than the public proxy timeout. Retry budgets must fit within it; otherwise retries convert a fast error into a slow error.

## 🔔 Correlation and Alerts

Verify that the gateway server span, HTTP client span, agent server span, Langfuse observations, and logs share one trace ID. Baggage carries only opaque user/session identity and bounded trace attributes; authorization-sensitive account identity travels in an authenticated internal request.

Alert on signals actually produced here:

- gateway 5xx/timeout rate with traffic guards;
- agent p95 latency and max-step stops;
- tool failure rate by bounded tool name;
- model failure rate and p95 duration by model and service;
- Collector/exporter loss if using an intermediate metrics Collector.

Thumbs-down and groundedness alerts require score producers and a native Langfuse Monitor or a scheduled Metrics API bridge. They are intentionally not claimed by this example.
