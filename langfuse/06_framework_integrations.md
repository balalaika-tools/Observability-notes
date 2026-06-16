# Framework Integrations

Last verified against official Langfuse integration docs on 2026-06-17.

Use framework integrations when your application already relies on an LLM framework, model SDK, gateway, or auto-instrumentation layer. Use manual SDK instrumentation when you need exact trace shape and business-specific observations.

## Integration Decision Table

| Stack | Recommended integration |
| --- | --- |
| LangChain or LangGraph | `langfuse.langchain.CallbackHandler` |
| OpenAI Python SDK | `langfuse.openai` drop-in wrapper |
| LiteLLM Proxy | `langfuse_otel` callback in LiteLLM proxy config |
| LiteLLM SDK | Langfuse LiteLLM SDK integration or OpenAI-compatible wrapper path |
| Existing OpenTelemetry instrumentation | OTLP/HTTP to Langfuse, preferably through the Collector |
| Mixed custom code plus framework calls | Wrap the workflow with Langfuse SDK spans and pass the integration callback inside the active context |

## LangChain and LangGraph

Langfuse integrates with LangChain through LangChain callbacks. The callback captures chains, LLM calls, tools, retrievers, and LangGraph agent executions.

Install:

```bash
pip install langfuse langchain langchain_openai langgraph
```

Basic usage:

```python
from langchain.agents import create_agent
from langfuse import get_client
from langfuse.langchain import CallbackHandler

langfuse = get_client()
langfuse_handler = CallbackHandler()


def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


agent = create_agent(
    model="openai:gpt-5-mini",
    tools=[add_numbers],
    system_prompt="You are a helpful math tutor.",
)

agent.invoke(
    {"messages": [{"role": "user", "content": "what is 42 + 58?"}]},
    config={"callbacks": [langfuse_handler]},
)
```

### Nest Framework Calls in a Product Trace

For production workflows, create a root observation and propagate trace attributes before invoking LangChain or LangGraph. The callback will attach framework spans under the current trace.

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

langfuse = get_client()


def answer_with_chain(user_input: str, user_id: str, session_id: str) -> str:
    with langfuse.start_as_current_observation(
        as_type="span",
        name="langchain.answer",
        input={"query": user_input},
    ) as root:
        with propagate_attributes(
            trace_name="langchain.answer",
            user_id=user_id,
            session_id=session_id,
            tags=["langchain"],
            metadata={"feature": "chat"},
        ):
            handler = CallbackHandler()
            prompt = ChatPromptTemplate.from_template("Respond to: {input}")
            chain = prompt | ChatOpenAI(model_name="gpt-4o")

            result = chain.invoke(
                {"input": user_input},
                config={"callbacks": [handler]},
            )

            answer = result.content
            root.update(output={"answer": answer})
            return answer
```

You can also set dynamic trace attributes via LangChain invocation metadata, but the SDK `propagate_attributes()` path is easier to reason about when the trace includes both framework and custom application work.

## OpenAI Python SDK

The OpenAI Python integration is a drop-in wrapper. It works with OpenAI and Azure OpenAI and automatically tracks prompts/completions, streaming, async calls, function/tool calls, latencies, API errors, token usage, and cost where available.

Install:

```bash
pip install langfuse openai
```

Switch imports:

```python
from langfuse.openai import OpenAI

client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Explain RAG in one sentence."}],
)
```

Alternative imports include:

```python
from langfuse.openai import AsyncOpenAI, AzureOpenAI, AsyncAzureOpenAI, openai
```

### Add Trace Attributes

For simple calls, pass Langfuse attributes through metadata:

```python
from langfuse.openai import OpenAI

client = OpenAI()

client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "1 + 1 ="}],
    name="calculator.chat",
    metadata={
        "langfuse_session_id": "session_123",
        "langfuse_user_id": "user_456",
        "langfuse_tags": ["calculator"],
        "feature": "calculator",
    },
)
```

For larger workflows, use an enclosing Langfuse span:

```python
from langfuse import get_client, propagate_attributes
from langfuse.openai import OpenAI

langfuse = get_client()
client = OpenAI()

with langfuse.start_as_current_observation(as_type="span", name="calculator.request"):
    with propagate_attributes(
        trace_name="calculator.request",
        session_id="session_123",
        user_id="user_456",
        tags=["calculator"],
    ):
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "1 + 1 ="}],
        )
```

Flush before exit in scripts and serverless functions:

```python
from langfuse import get_client

get_client().flush()
```

The OpenAI wrapper is best for client-side OpenAI SDK calls. Langfuse notes that tracing the OpenAI Assistants API is not supported by this integration because Assistants have server-side state that cannot be captured completely without additional API requests. For agentic systems, prefer explicit agent/tool/generation observations or a framework integration that exposes the workflow steps.

## LiteLLM Proxy

LiteLLM Proxy can log all model calls through a central gateway to Langfuse using the `langfuse_otel` callback.

Set credentials:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_OTEL_HOST="https://cloud.langfuse.com"
```

Example `litellm_config.yaml`:

```yaml
model_list:
  - model_name: gpt-5.1
    litellm_params:
      model: gpt-5.1

litellm_settings:
  callbacks: ["langfuse_otel"]
```

Start the proxy:

```bash
litellm --config /path/to/litellm_config.yaml
```

Use this path when:

- multiple services call models through the same gateway;
- you want provider-level observability without changing every application;
- you still propagate user/session/tags from application requests where possible.

If the application already uses the Langfuse SDK or OpenTelemetry exporter directly, avoid double-instrumenting the same model call through both the app and the proxy unless you intentionally want both views.

## OpenTelemetry-Native Libraries

For OpenTelemetry-native or third-party auto-instrumentation libraries:

1. Prefer the current OpenTelemetry GenAI semantic conventions.
2. Route traces to Langfuse through OTLP/HTTP.
3. Add Langfuse-specific attributes when you need first-class trace, observation, user, session, metadata, tags, release, or version fields.
4. Send operational metrics to your metrics backend.

See [03_otel_ingestion_and_mapping.md](03_otel_ingestion_and_mapping.md) and [../opentelemetry/06_genai_and_llm_observability.md](../opentelemetry/06_genai_and_llm_observability.md).

Legacy notes in this repo previously included OpenLLMetry and OpenLIT examples. Treat these as OpenTelemetry-native instrumentation choices: if they emit current OTel spans, route them to Langfuse via OTLP/HTTP and add Langfuse attributes where needed. Use the current project documentation for those packages before copying setup code, because auto-instrumentation package APIs change faster than the OTLP and Langfuse mapping layer.

## Mixing Auto and Manual Instrumentation

The best production traces often combine both:

```text
support.answer             manual root span
  input.normalize          manual span
  langchain.retriever      framework callback
  openai.chat.completions  OpenAI wrapper generation
  guardrail.output         manual guardrail span
```

Rules:

- Create the product-level root span yourself.
- Propagate user, session, tags, release, version, and metadata early.
- Let framework integrations capture internals.
- Add manual spans for business decisions the framework cannot know about.
- Score the trace or important observations after the framework run finishes.
- Do not use deprecated trace input/output helpers for new code; set input/output on the root observation.
