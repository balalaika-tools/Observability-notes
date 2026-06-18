# Example: Multi-Service Agent

Last checked against the Langfuse guide and official docs on 2026-06-18.

This example shows:

- a gateway service receiving user requests;
- an internal agent service performing tool calls and LLM calls;
- W3C trace context propagation across HTTP;
- Langfuse attribute propagation through baggage;
- OpenTelemetry metrics for alerting.

## Architecture

```text
client
  |
  v
gateway-service
  |  traceparent + baggage
  v
agent-service
  |-- tool.search
  |-- tool.lookup_account
  |-- llm.final_answer
```

Instrument both services with FastAPI and HTTPX instrumentation:

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
```

The gateway creates request identity. The agent service records the LLM and tool details.

## Gateway Service

```python
import os

import httpx
from fastapi import FastAPI
from langfuse import get_client, propagate_attributes
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

langfuse = get_client()
agent_client = httpx.Client(base_url="http://agent-service:8080", timeout=20.0)


@app.post("/answer")
def answer(payload: dict) -> dict:
    question = payload["question"]
    user_id = payload["user_id"]
    session_id = payload["session_id"]

    with langfuse.start_as_current_observation(
        as_type="span",
        name="gateway.answer",
        input={"question": question},
    ) as span:
        trace_id = langfuse.get_current_trace_id()
        with propagate_attributes(
            trace_name="agent.answer",
            user_id=user_id,
            session_id=session_id,
            version=os.getenv("AGENT_VERSION", "agent-dev"),
            metadata={
                "entrypoint": "public-api",
                "release": os.getenv("RELEASE", "local"),
            },
            tags=["agent", "gateway"],
            as_baggage=True,
        ):
            response = agent_client.post(
                "/run",
                json={"question": question},
            )
            response.raise_for_status()
            result = response.json()
            span.update(output={"answer": result["answer"]})
            result["langfuse_trace_id"] = trace_id
            return result
```

`as_baggage=True` allows downstream instrumented services to receive Langfuse trace attributes alongside trace context. Keep baggage small and allowlisted.

## Agent Service

```python
import time

from fastapi import FastAPI
from langfuse import get_client
from openai import OpenAI
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)

langfuse = get_client()
openai = OpenAI()
meter = metrics.get_meter("example.agent")

agent_steps = meter.create_histogram(
    "agent.steps",
    unit="{step}",
    description="Number of steps used by an agent run",
)
tool_calls = meter.create_counter(
    "agent.tool.calls",
    unit="{call}",
    description="Agent tool calls",
)
tool_failures = meter.create_counter(
    "agent.tool.failures",
    unit="{call}",
    description="Failed agent tool calls",
)
llm_duration = meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="Duration of GenAI client operations",
)


def search_docs(query: str) -> list[dict]:
    return [{"id": "doc_001", "snippet": "Admins can reset SSO in Security settings."}]


def lookup_account(account_id: str) -> dict:
    return {"tier": "enterprise", "region": "eu"}


@app.post("/run")
def run_agent(payload: dict) -> dict:
    question = payload["question"]
    model = "gpt-4o-mini"
    steps = 0

    with langfuse.start_as_current_observation(
        as_type="agent",
        name="agent.answer",
        input={"question": question},
        metadata={"maxSteps": 5},
    ) as agent:
        with langfuse.start_as_current_observation(
            as_type="tool",
            name="tool.search_docs",
            input={"query": question},
        ) as tool:
            steps += 1
            try:
                tool_calls.add(1, {"tool.name": "search_docs", "status": "ok"})
                docs = search_docs(question)
                tool.update(output={"documents": [doc["id"] for doc in docs]})
            except Exception as exc:
                tool_failures.add(1, {"tool.name": "search_docs", "error.type": type(exc).__name__})
                tool.update(level="ERROR", status_message=str(exc))
                raise

        with langfuse.start_as_current_observation(
            as_type="tool",
            name="tool.lookup_account",
            input={"account_id": "account_123"},
        ) as tool:
            steps += 1
            account = lookup_account("account_123")
            tool_calls.add(1, {"tool.name": "lookup_account", "status": "ok"})
            tool.update(output=account)

        tool_context = "\n".join(
            f"[{doc['id']}] {doc['snippet']}" for doc in docs
        )
        messages = [
            {"role": "system", "content": "Answer using tool results. Cite document IDs."},
            {
                "role": "user",
                "content": f"Question: {question}\n\nTool results:\n{tool_context}",
            },
        ]

        start = time.perf_counter()
        with langfuse.start_as_current_observation(
            as_type="generation",
            name="llm.final_answer",
            model=model,
            input={"messages": messages},
            model_parameters={"temperature": 0.1},
        ) as generation:
            response = openai.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
            )
            duration = time.perf_counter() - start
            answer = response.choices[0].message.content or ""
            usage = response.usage

            llm_duration.record(
                duration,
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "openai",
                    "gen_ai.request.model": model,
                },
            )

            generation.update(
                output=answer,
                usage_details={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                },
            )

        agent_steps.record(steps, {"agent.name": "support_agent"})
        agent.update(output={"answer": answer, "steps": steps})

        return {"answer": answer, "steps": steps}
```

## Correlation

When both services use OpenTelemetry:

- the gateway server span is the parent of the outbound HTTP client span;
- the agent service server span is a child of that request;
- Langfuse observations created inside those contexts join the same trace;
- logs can include `trace_id` and `span_id`;
- metrics use low-cardinality labels and link back to trace investigation by time, release, and service.

## Alerts

Good first alerts:

- high gateway 5xx rate;
- high agent p95 latency;
- high `agent.tool.failures` rate by tool name;
- high `agent.steps` p95;
- high `gen_ai.client.operation.duration` p95 by model;
- high thumbs-down or low groundedness scores in Langfuse-derived quality metrics.

## Pitfalls

- Do not pass user email or raw account names as baggage.
- Do not rely on baggage for authorization; it is observability context.
- Do not create separate traces in downstream services by disabling HTTP extraction.
- Do not add request IDs or user IDs as metric labels.
- Do not drop root spans when routing traces to Langfuse.
