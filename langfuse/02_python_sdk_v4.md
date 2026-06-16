# Python SDK v4

The current Langfuse Python SDK is v4. Import from `langfuse`, not from the legacy `langfuse.decorators` module.

Use the SDK when you are writing Python LLM application code and want Langfuse-specific features such as observations, generations, scores, prompt management, datasets, and experiments.

## Install

```bash
pip install langfuse
```

For a typical LLM service you may also install provider clients and OpenTelemetry instrumentations:

```bash
pip install openai httpx fastapi uvicorn opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-httpx
```

## Configure

Set credentials in the environment:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

Use the base URL for your Langfuse region or self-hosted instance. The current SDK uses `LANGFUSE_BASE_URL`; older examples using `LANGFUSE_HOST` should be updated.

Common environment variables:

| Variable | Use |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY` | Project public key |
| `LANGFUSE_SECRET_KEY` | Project secret key |
| `LANGFUSE_BASE_URL` | Cloud region or self-hosted URL |
| `LANGFUSE_DEBUG` | Debug logging during development |
| `LANGFUSE_SAMPLE_RATE` | Client-side trace sampling from `0.0` to `1.0` |
| `LANGFUSE_TRACING_ENABLED` | Set to `false` to disable tracing without changing code |
| `LANGFUSE_TIMEOUT` | HTTP timeout in seconds |
| `LANGFUSE_RELEASE` | Release identifier when not passed in code |
| `LANGFUSE_TRACING_ENVIRONMENT` | Environment name when not passed in code |

## Initialize

```python
from langfuse import get_client

langfuse = get_client()

if not langfuse.auth_check():
    raise RuntimeError("Langfuse authentication failed")
```

`get_client()` returns a singleton client configured from the environment. For explicit configuration:

```python
from langfuse import Langfuse

langfuse = Langfuse(
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    base_url="https://cloud.langfuse.com",
)
```

Constructor arguments override environment values for that client instance. In production, prefer environment variables for credentials and deployment-specific settings, and use constructor arguments only when the process intentionally talks to multiple projects or needs test-specific overrides.

## Singleton and Multi-Project Use

`get_client()` uses a singleton pattern. In normal single-project services, calling it from many modules returns the same client and avoids duplicate exporters.

```python
from langfuse import get_client

langfuse = get_client()
```

Multi-project use is experimental. If more than one project client exists in the same process, pass `public_key` when retrieving a client. Without an explicit project key, the SDK returns a disabled client to avoid cross-project data leakage.

```python
from langfuse import Langfuse, get_client

Langfuse(public_key="pk-lf-project-a", secret_key="sk-lf-...")
Langfuse(public_key="pk-lf-project-b", secret_key="sk-lf-...")

project_a = get_client(public_key="pk-lf-project-a")
project_b = get_client(public_key="pk-lf-project-b")
```

Prefer separate processes or services for separate tenants/projects unless there is a strong operational reason to multiplex.

## Context Manager Instrumentation

The context manager is the clearest way to instrument production code because parent-child relationships are explicit and automatic.

```python
from langfuse import get_client, propagate_attributes
from openai import OpenAI

langfuse = get_client()
openai = OpenAI()


def answer_question(question: str, user_id: str, session_id: str) -> str:
    with langfuse.start_as_current_observation(
        as_type="span",
        name="chat.answer",
        input={"question": question},
    ) as root:
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            trace_name="chat.answer",
            metadata={"feature": "chat", "tenantTier": "enterprise"},
            tags=["chat", "production"],
            version="prompt-v17",
        ):
            messages = [{"role": "user", "content": question}]

            with langfuse.start_as_current_observation(
                as_type="generation",
                name="llm.generate_answer",
                model="gpt-4o-mini",
                input={"messages": messages},
                model_parameters={"temperature": 0.2},
            ) as generation:
                response = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=0.2,
                )
                answer = response.choices[0].message.content or ""
                usage = response.usage

                generation.update(
                    output=answer,
                    usage_details={
                        "input_tokens": usage.prompt_tokens if usage else 0,
                        "output_tokens": usage.completion_tokens if usage else 0,
                    },
                )

            root.update(output={"answer": answer})

            return answer
```

For new code, set trace input and output on the root observation. Langfuse keeps legacy trace input/output helpers for migration cases, but root observation input/output is the current default pattern.

## Decorator Instrumentation

Use `@observe()` for stable functions where function boundaries match observation boundaries.

```python
from langfuse import observe


@observe(as_type="retriever", name="rag.retrieve")
def retrieve_documents(query: str) -> list[dict]:
    return vector_store.search(query, limit=5)


@observe(as_type="tool", name="tool.lookup_order")
def lookup_order(order_id: str) -> dict:
    return orders_api.get(order_id)
```

The decorator can capture inputs and outputs. Disable capture on sensitive functions:

```python
from langfuse import observe


@observe(as_type="tool", name="tool.payment_lookup", capture_input=False, capture_output=False)
def payment_lookup(account_id: str) -> dict:
    return payment_service.lookup(account_id)
```

## Manual Observations

Manual observations are useful for background tasks or lifecycles that do not fit a `with` block. End them explicitly.

```python
from langfuse import get_client

langfuse = get_client()

span = langfuse.start_observation(name="background.embed_batch", as_type="span")
try:
    span.update(input={"batch_size": 128})
    process_batch()
    span.update(output={"status": "ok"})
finally:
    span.end()
```

If you need nested children under a manual observation, create them from that observation object so the hierarchy remains correct.

## Cross-Service Attribute Propagation

For distributed tracing, use OpenTelemetry context propagation for trace IDs. If downstream Langfuse spans must inherit `user_id`, `session_id`, tags, or metadata, propagate them as baggage.

```python
import requests
from langfuse import get_client, propagate_attributes

langfuse = get_client()


def call_agent_service(user_id: str, session_id: str, question: str) -> dict:
    with langfuse.start_as_current_observation(as_type="span", name="gateway.request"):
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            trace_name="agent.answer",
            metadata={"entrypoint": "api"},
            tags=["agent"],
            as_baggage=True,
        ):
            response = requests.post(
                "https://agent.internal/answer",
                json={"question": question},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
```

Rules for baggage:

- Keep values small.
- Never put secrets or raw PII in baggage.
- Propagate only stable identifiers needed by downstream services.
- Prefer internal IDs over email addresses, names, or full account data.

For propagated Langfuse attributes, keep values as short strings. Metadata keys should be alphanumeric; use names like `tenantTier` or `retrievalStrategy` instead of keys with spaces or punctuation.

## Custom Trace IDs

Use a deterministic Langfuse trace ID when an external system already owns the request ID and you need stable correlation or idempotent batch processing.

```python
from langfuse import Langfuse, get_client

langfuse = get_client()

external_request_id = "support-ticket-12345"
trace_id = Langfuse.create_trace_id(seed=external_request_id)

with langfuse.start_as_current_observation(
    as_type="span",
    name="support.answer",
    trace_context={"trace_id": trace_id},
) as span:
    span.update(input={"ticketId": external_request_id})
    answer = answer_ticket(external_request_id)
    span.update(output={"answer": answer})
```

Trace IDs must be 32 lowercase hexadecimal characters. `create_trace_id(seed=...)` creates a valid deterministic ID from an arbitrary string.

## Updating Current Observations

Use current-observation helpers when lower-level code should enrich the active span without receiving the observation object.

```python
from langfuse import get_client

langfuse = get_client()


def validate_answer(answer: str) -> bool:
    passed = "forbidden" not in answer.lower()
    langfuse.update_current_span(
        metadata={"guardrail": "keyword_check", "passed": passed},
    )
    return passed
```

For active generation observations, use `update_current_generation()` when you need generation-specific fields.

## Scores in Application Code

Use `score_current_trace()` for inline user feedback or guardrail outcomes on the active trace.

```python
from langfuse import get_client

langfuse = get_client()


def record_user_feedback(accepted: bool) -> None:
    langfuse.score_current_trace(
        name="user_accepted",
        value=1 if accepted else 0,
        data_type="BOOLEAN",
    )
```

Use `create_score()` when scoring happens out of band and you already know the trace ID or observation ID.

```python
from langfuse import get_client

langfuse = get_client()

langfuse.create_score(
    trace_id="9d1b6c3e7f9a4d8bb1e2c3a4f5d6e7a8",
    name="answer_relevance",
    value=0.86,
    data_type="NUMERIC",
    comment="LLM-as-judge relevance score",
)
```

Use deterministic `score_id` values for idempotent evaluators that may retry.

## Flush and Shutdown

The SDK sends telemetry asynchronously. In long-running web services, normal process shutdown hooks are usually enough. In scripts, workers, tests, and serverless functions, flush before exit.

```python
from langfuse import get_client

langfuse = get_client()

try:
    run_job()
finally:
    langfuse.flush()
```

Use `shutdown()` when the process is ending and you want to close resources after flushing.

## Debugging and Sampling

Enable debug logs only while troubleshooting:

```bash
export LANGFUSE_DEBUG=true
```

Sample at the SDK layer when you need to reduce successful trace volume before export:

```bash
export LANGFUSE_SAMPLE_RATE=0.1
```

or:

```python
from langfuse import Langfuse

langfuse = Langfuse(sample_rate=0.1)
```

Use sampling deliberately. Keep errors, negative feedback, guardrail failures, and new-release burn-in traces whenever possible.

Disable tracing without removing instrumentation:

```bash
export LANGFUSE_TRACING_ENABLED=false
```

## Production Checklist

- Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` from secret management.
- Call `auth_check()` at service startup when failing fast is acceptable.
- Use stable trace and observation names.
- Add `user_id`, `session_id`, `release`, `version`, tags, and filterable metadata early in the trace.
- Use `propagate_attributes(..., as_baggage=True)` only for cross-service attributes that should travel downstream.
- Disable input/output capture for sensitive paths.
- Record token usage on generations.
- Add user feedback and evaluator results as scores.
- Flush in short-lived processes.
- Avoid mixing old SDK v3 patterns with v4 imports in the same codebase.
