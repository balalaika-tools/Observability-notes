# GenAI And LLM Observability

OpenTelemetry now has a dedicated GenAI semantic convention effort for model calls, embeddings, retrievals, tool execution, memory, agents, and GenAI metrics. The conventions are still marked Development, so production code should isolate the attribute names in helpers and be prepared for future migration.

This page uses the current names from the OpenTelemetry GenAI semantic conventions repository as of June 16, 2026.

## Core Span Shape

An LLM inference span should represent the logical model operation from request start until the final response, error, or cancellation. If the client retries internally, the span should cover the logical operation including retries.

Recommended span shape:

| Field | Value |
| --- | --- |
| Span name | `{gen_ai.operation.name} {gen_ai.request.model}`, for example `chat gpt-4o-mini` |
| Span kind | `CLIENT` for remote provider calls |
| Required attributes | `gen_ai.operation.name`, `gen_ai.provider.name` |
| Common request attributes | `gen_ai.request.model`, temperature, max tokens, stream |
| Common response attributes | `gen_ai.response.model`, finish reasons, response ID |
| Usage attributes | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |

## Current Attribute Names

Use these names for new raw OTel instrumentation:

| Purpose | Attribute |
| --- | --- |
| Operation | `gen_ai.operation.name` |
| Provider | `gen_ai.provider.name` |
| Requested model | `gen_ai.request.model` |
| Actual response model | `gen_ai.response.model` |
| Temperature | `gen_ai.request.temperature` |
| Max generated tokens | `gen_ai.request.max_tokens` |
| Streaming flag | `gen_ai.request.stream` |
| Output type | `gen_ai.output.type` |
| Response ID | `gen_ai.response.id` |
| Finish reasons | `gen_ai.response.finish_reasons` |
| Input tokens | `gen_ai.usage.input_tokens` |
| Output tokens | `gen_ai.usage.output_tokens` |
| Cache read input tokens | `gen_ai.usage.cache_read.input_tokens` |
| Cache creation input tokens | `gen_ai.usage.cache_creation.input_tokens` |
| Reasoning output tokens | `gen_ai.usage.reasoning.output_tokens` |
| Time to first chunk | `gen_ai.response.time_to_first_chunk` |

The older `gen_ai.system` attribute appears in many tools and older docs. Treat it as legacy compatibility. Prefer `gen_ai.provider.name` for new instrumentation.

## Manual LLM Span Example

```python
import os
import time

from openai import OpenAI
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer(__name__)
client = OpenAI()


def chat_completion(messages: list[dict], *, route: str) -> str:
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    start = time.perf_counter()

    with tracer.start_as_current_span(
        f"chat {model}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai",
            "gen_ai.request.model": model,
            "gen_ai.request.temperature": 0.2,
            "gen_ai.request.max_tokens": 800,
            "gen_ai.output.type": "text",
            "http.route": route,
        },
    ) as span:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=800,
            )
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error.type", type(exc).__name__)
            raise

        first_choice = response.choices[0]
        answer = first_choice.message.content or ""

        span.set_attribute("gen_ai.response.model", response.model)
        span.set_attribute("gen_ai.response.id", response.id)
        span.set_attribute("gen_ai.response.finish_reasons", [first_choice.finish_reason])

        if response.usage:
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)

        span.set_attribute("app.llm.duration_ms", (time.perf_counter() - start) * 1000)
        return answer
```

## Capturing Prompt And Output Content

Prompt and output content can be sensitive and large. The GenAI conventions support opt-in content attributes such as:

- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.system_instructions`
- `gen_ai.tool.definitions`

Do not enable full content capture blindly in general observability backends. Decide:

- whether prompts/responses are allowed to leave your environment;
- whether content should be sent only to Langfuse;
- whether content needs masking before export;
- how long content should be retained;
- whether user consent or contractual controls apply.

When sending raw OTel traces to Langfuse and you need precise Langfuse input/output fields, use Langfuse-specific attributes:

```python
import json

span.set_attribute("langfuse.observation.input", json.dumps({"messages": messages}))
span.set_attribute("langfuse.observation.output", json.dumps({"content": answer}))
```

For the Langfuse SDK, prefer SDK fields (`input=...`, `output=...`) and masking hooks rather than manually setting OTel payload attributes.

## Embeddings

Embedding calls are GenAI operations too:

```python
with tracer.start_as_current_span(
    f"embeddings {embedding_model}",
    kind=SpanKind.CLIENT,
    attributes={
        "gen_ai.operation.name": "embeddings",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": embedding_model,
    },
) as span:
    response = client.embeddings.create(model=embedding_model, input=texts)
    span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
```

For RAG systems, use separate spans for embedding, vector search, reranking, and generation. This lets you distinguish model latency from retrieval latency.

## Tool Calls And Agents

Agent traces should show reasoning and tool execution as separate operations:

```text
agent.invoke
  plan
  chat gpt-4o-mini
  execute_tool search_docs
  execute_tool get_account_status
  chat gpt-4o-mini
```

Use current operation names where applicable:

| Operation | Use for |
| --- | --- |
| `invoke_agent` | One agent invocation. |
| `plan` | Planning or task decomposition. |
| `chat` | Model call. |
| `execute_tool` | Tool execution requested by model or agent. |
| `retrieval` | Retrieval operation. |
| `embeddings` | Embedding model call. |
| `search_memory` | Memory retrieval. |
| `upsert_memory` | Memory write/update. |

Tool span example:

```python
def execute_tool(tool_name: str, arguments: dict) -> dict:
    with tracer.start_as_current_span(
        f"execute_tool {tool_name}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
        },
    ) as span:
        span.set_attribute("app.tool.argument_count", len(arguments))
        result = tools[tool_name](**arguments)
        span.set_attribute("app.tool.result_size", len(str(result)))
        return result
```

Keep tool names bounded. If tools are user-defined, normalize them to a small set for metrics and attributes.

## GenAI Metrics

The GenAI conventions include a required duration histogram:

- `gen_ai.client.operation.duration`, unit `s`

They also define token usage dimensions such as `gen_ai.token.type`. In your own apps, you can use either the semantic convention metrics or application metrics such as `llm.request.duration` and `llm.tokens`. The important part is consistency.

Recommended LLM metrics:

| Metric | Why |
| --- | --- |
| Duration histogram | User latency and provider degradation. |
| Request counter by outcome | Error rates and volume. |
| Token counter by token type | Cost, capacity, prompt growth. |
| Time-to-first-token/chunk | Streaming UX. |
| Tool call counter | Agent loops and tool failures. |
| Guardrail block counter | Safety and product quality. |

## Best Practices

- Set important sampling attributes at span creation time: provider, operation, model, route.
- Keep span names low-cardinality.
- Use `gen_ai.provider.name`, not legacy `gen_ai.system`, for new code.
- Record actual `gen_ai.response.model` when the provider returns it.
- Treat prompt, output, and tool content as opt-in sensitive data.
- Emit metrics separately from traces.
- Use Langfuse for LLM-specific payloads, costs, sessions, scores, prompt versions, and evaluation workflows.
- Keep a compatibility layer around GenAI attribute names because the convention is still Development.

