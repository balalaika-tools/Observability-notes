# Framework Integrations — LangChain, OpenAI, LiteLLM, and Auto-Instrumentation

> **Who this is for**: Python engineers already using common LLM frameworks (LangChain, OpenAI SDK, LiteLLM) who want full Langfuse observability with minimal boilerplate. Assumes you have credentials configured — read [Setup & Config](01_setup_and_config.md) and [Instrumentation](03_instrumentation.md) first.

---

## 1. Why Framework Integrations

**Framework integrations** are pre-built adapters that hook into the internals of popular LLM libraries, emitting Langfuse traces automatically — no manual span creation required.

Without integrations, adding tracing to an existing LangChain application means wrapping every chain invocation, every LLM call, every retriever call, and every tool execution individually. That is fragile, repetitive, and breaks whenever the application evolves.

With integrations, a single setup call covers the entire framework:

| Approach | Lines of tracing code added | Breaks when code changes? |
|----------|-----------------------------|---------------------------|
| Manual `@observe` on every call | Many — one per callsite | Yes — new calls are invisible |
| Framework callback / wrapper | One setup call | No — hooks fire automatically |

**What integrations auto-capture:**
- Model name and provider (no hardcoding)
- Token usage (prompt tokens, completion tokens, total)
- Full input messages and output content
- Latency per step
- Tool/function call names and arguments
- Retriever queries and returned documents (LangChain)

> **Key insight**: Integration-based tracing is additive. You can layer `@observe` on top for business-level context — user IDs, session IDs, custom metadata — while the framework integration fills in all the LLM-level detail automatically.

---

## 2. LangChain / LangGraph Integration

The **LangChain callback handler** is the primary integration for LangChain and LangGraph applications. It uses LangChain's native callback mechanism, which fires hooks at every step of chain execution.

### Installation

```bash
pip install "langfuse[langchain]"
```

### Basic usage — pass to invocation

```python
from langfuse.callback import CallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Create the handler — reads LANGFUSE_* env vars automatically
langfuse_handler = CallbackHandler()

llm = ChatOpenAI(model="gpt-4o")
prompt = ChatPromptTemplate.from_template("Answer concisely: {question}")
chain = prompt | llm | StrOutputParser()

# Pass the handler at invocation time — traces this single run
response = chain.invoke(
    {"question": "What is RAG?"},
    config={"callbacks": [langfuse_handler]},
)
print(response)
```

### Option 2 — bind globally with `.with_config()`

```python
from langfuse.callback import CallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

langfuse_handler = CallbackHandler()

retriever = get_vector_store().as_retriever()  # your retriever
llm = ChatOpenAI(model="gpt-4o")
prompt = ChatPromptTemplate.from_template(
    "Context: {context}\n\nQuestion: {question}\n\nAnswer:"
)

# Bind the handler once — every invocation of this chain is traced
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
).with_config({"callbacks": [langfuse_handler]})

# No need to pass callbacks here — already bound
response = chain.invoke("What is RAG?")
```

### LangGraph — same pattern, graph invocation

```python
from langfuse.callback import CallbackHandler
from langchain_core.messages import HumanMessage

langfuse_handler = CallbackHandler()

# Pass the handler in the graph's config dict
result = graph.invoke(
    {"messages": [HumanMessage(content="Hello, what is your name?")]},
    config={"callbacks": [langfuse_handler]},
)
```

### What gets captured automatically

| Event | Captured as | Fields |
|-------|-------------|--------|
| Chain start / end | Span | name, input, output, latency |
| LLM call | Generation | model, input messages, output, token counts |
| Tool call | Span | tool name, input, output |
| Retriever call | Span | query, returned documents |
| Agent step | Span | thought, action, observation |

### Adding user and session context

```python
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    user_id="user-123",
    session_id="session-456",
    trace_name="rag-pipeline",
    tags=["production", "v2"],
    metadata={"feature_flag": "new-retriever"},
)

# All traces from this handler are tagged with the above context
response = chain.invoke(
    {"question": "Summarize the Q3 report"},
    config={"callbacks": [langfuse_handler]},
)
```

💡 Create a new `CallbackHandler` instance per request when you need different `user_id` or `session_id` values per call. Handler instances are lightweight — creating one per request is fine.

✅ One handler instance per trace context (per user request)
❌ One global handler shared across all users — user IDs bleed across traces

---

## 3. OpenAI SDK — Drop-in Wrapper

The **OpenAI wrapper** is the lowest-friction integration available. It replaces the `openai` import with `langfuse.openai` — the API surface is identical, but every call is automatically traced.

### Synchronous usage

```python
from langfuse.openai import openai  # drop-in replacement for `import openai`

# Use exactly like the standard openai library — no other changes needed
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain RAG in two sentences."},
    ],
    temperature=0.3,
)

print(response.choices[0].message.content)
# model, token usage, messages, and output are all captured automatically
```

### Async usage

```python
from langfuse.openai import AsyncOpenAI

client = AsyncOpenAI()

async def explain_concept(concept: str) -> str:
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"Explain {concept}"}],
    )
    return response.choices[0].message.content
```

### Linking to a parent span with `@observe`

The wrapper detects an active `@observe` context and automatically nests the OpenAI generation inside it. This gives you a single trace with your business logic at the root and LLM calls as children.

```python
from langfuse.decorators import observe, langfuse_context
from langfuse.openai import openai

@observe(name="rag-pipeline")  # creates the root trace span
def rag_pipeline(user_id: str, query: str) -> str:
    # Attach user context to the root trace
    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id=f"session-{user_id}",
        tags=["rag", "production"],
    )

    # Retrieval step — instrument manually if needed
    context_docs = retrieve_documents(query)
    context_text = "\n".join(doc.page_content for doc in context_docs)

    # openai call auto-nests inside the @observe span as a Generation
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Answer using only the provided context."},
            {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"},
        ],
    )
    return response.choices[0].message.content
```

> **Key insight**: The `langfuse.openai` wrapper reads the active OTel span context. If `@observe` is on the call stack, the OpenAI generation appears as a child span automatically — no extra wiring required.

⚠️ If you import `openai` directly alongside `from langfuse.openai import openai`, the bare `openai` import bypasses tracing. Audit your imports — there should be exactly one import of the openai client per module.

---

## 4. LiteLLM Integration

**LiteLLM** is a proxy library that normalizes calls to 100+ LLM providers (OpenAI, Anthropic, Mistral, Cohere, Bedrock, and more) behind a single API. Langfuse integrates via LiteLLM's callback system.

### Pattern 1 — One-liner callback (simplest)

```python
import litellm

# Enable Langfuse tracing for all LiteLLM calls — reads LANGFUSE_* env vars
litellm.success_callback = ["langfuse"]

# Now all calls to any provider are automatically traced
response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)

# Switch providers — tracing works identically
response = litellm.completion(
    model="anthropic/claude-3-5-sonnet-20241022",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)
```

### Pattern 2 — LiteLLM proxy server config

When running LiteLLM as a standalone proxy (e.g., a shared gateway for multiple teams), configure Langfuse in `config.yaml`:

```yaml
# litellm config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
  - model_name: claude-3-5-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: os.environ/ANTHROPIC_API_KEY

litellm_settings:
  success_callback: ["langfuse"]
  # Langfuse credentials read from LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
```

All teams calling the proxy receive Langfuse traces without any changes to their own code.

### Pattern 3 — LiteLLM via OpenTelemetry backend

LiteLLM also supports sending traces directly to Langfuse's OTLP endpoint. This is the recommended path when you want traces unified with other OTel instrumentation:

```python
import litellm
import os

# Configure LiteLLM to export via OTLP to Langfuse
litellm.success_callback = ["otel"]

os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://cloud.langfuse.com/api/public/otel"
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = (
    f"Authorization=Basic {auth_string},"
    "x-langfuse-ingestion-version=4"
)

response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
```

💡 Use `success_callback = ["langfuse"]` for Langfuse-only setups. Use `success_callback = ["otel"]` when you also send traces to other OTel backends (Jaeger, Honeycomb, etc.) and want a single pipeline.

---

## 5. OpenLLMetry Auto-Instrumentation

**OpenLLMetry** (by Traceloop) is an OpenTelemetry-based instrumentation library that patches popular LLM SDKs and frameworks at import time. It covers more providers than the native Langfuse wrapper and requires no changes to existing call sites.

### Installation

```bash
pip install traceloop-sdk
```

### Setup

```python
import base64
import os
from traceloop.sdk import Traceloop

# Build the Basic auth string from Langfuse credentials
public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
secret_key = os.environ["LANGFUSE_SECRET_KEY"]
auth_string = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

Traceloop.init(
    app_name="my-llm-app",
    api_endpoint="https://cloud.langfuse.com/api/public/otel",
    headers={
        "Authorization": f"Basic {auth_string}",
        "x-langfuse-ingestion-version": "4",
    },
)

# From this point, all calls to instrumented libraries are auto-traced.
# No other changes needed anywhere in the codebase.
import openai
client = openai.OpenAI()
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### What OpenLLMetry instruments automatically

| Category | Covered libraries |
|----------|-------------------|
| LLM providers | OpenAI, Anthropic, Cohere, Mistral, Azure OpenAI, Bedrock |
| Orchestration | LangChain, LlamaIndex |
| Vector stores | Pinecone, Chroma, Weaviate, Qdrant |
| Embeddings | OpenAI Embeddings, Cohere Embeddings |

> **Key insight**: OpenLLMetry instruments at the library level via monkey-patching. You do not import from `traceloop` in your business logic — only the `Traceloop.init()` call at application startup is needed. This makes it easy to add to existing codebases without touching any feature code.

⚠️ OpenLLMetry and the `langfuse.openai` wrapper both patch the OpenAI client. Do not use both simultaneously — pick one instrumentation path for each provider.

---

## 6. OpenLIT Auto-Instrumentation

**OpenLIT** is an alternative OTel-based auto-instrumentation library with support for GPU metrics, cost tracking, and a broader set of OTel-native integrations. It extends Langfuse observability to non-Python runtimes via standard OTLP.

### Installation

```bash
pip install openlit
```

### Setup

```python
import base64
import os
import openlit

public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
secret_key = os.environ["LANGFUSE_SECRET_KEY"]
auth_string = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

openlit.init(
    otlp_endpoint="https://cloud.langfuse.com/api/public/otel",
    otlp_headers={
        "Authorization": f"Basic {auth_string}",
        "x-langfuse-ingestion-version": "4",
    },
)

# All subsequent LLM calls from any instrumented framework are traced
```

### OpenLIT additional capabilities

| Feature | Notes |
|---------|-------|
| GPU metrics | CPU/GPU utilization attached to LLM spans |
| Cost tracking | Per-call USD cost estimates via token pricing tables |
| Java / Go support | Other runtimes export via OTLP to the same Langfuse project |
| Vector DB tracing | Chroma, Weaviate, Qdrant, Milvus |

💡 OpenLIT is the right choice when your team runs LLM workloads in multiple languages (Python + Java services, or Python + Go microservices) and you want a single Langfuse project covering all of them via shared OTel infrastructure.

---

## 7. Mixing Auto-Instrumentation with Manual `@observe`

Auto-instrumentation and manual `@observe` decorators compose naturally. The framework integration fills in LLM-level detail; `@observe` adds the business-level wrapper with user context, session IDs, and custom metadata.

```python
from langfuse.decorators import observe, langfuse_context
from langfuse.openai import openai  # auto-instrumented OpenAI wrapper

@observe(name="full-pipeline")  # manual root span — sets the trace name
def full_pipeline(user_id: str, query: str) -> str:
    # Attach business context to the root trace
    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id=f"session-{user_id}-{get_date()}",
        tags=["production", "v3"],
        metadata={"query_length": len(query)},
    )

    # Retrieval — wrap in a child span for visibility
    with langfuse_context.observe(name="retrieval"):
        docs = retrieve_documents(query)
        langfuse_context.update_current_observation(
            input=query,
            output={"doc_count": len(docs)},
        )

    context_text = "\n".join(doc.page_content for doc in docs)

    # openai call auto-traces INSIDE the @observe span as a Generation child
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Answer using only the provided context."},
            {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"},
        ],
    )
    return response.choices[0].message.content


# Resulting trace structure:
# full-pipeline (Span)
# ├── retrieval (Span)           ← manual @observe child
# └── openai.chat.completions    ← auto-captured Generation
#     model: gpt-4o
#     prompt_tokens: 312
#     completion_tokens: 87
```

The nesting works because both `@observe` and `langfuse.openai` write into the same OTel span context. The active span set by `@observe` becomes the parent of any spans created inside its scope.

> **Key insight**: Think of `@observe` as your business-logic layer and framework integrations as the LLM-detail layer. They are designed to work together. You rarely need to choose one or the other exclusively.

---

## 8. Integration Comparison

| Integration | Setup Effort | Code Changes Required | What's Auto-Captured | Best For |
|-------------|-------------|----------------------|---------------------|----------|
| **LangChain callback** | Low — one handler instance | Pass `config={"callbacks": [...]}` | Full chain, LLM calls, tools, retrieval | LangChain / LangGraph apps |
| **OpenAI wrapper** | Very low — import swap | Change `import openai` to `from langfuse.openai import openai` | Model, tokens, messages, latency | Direct OpenAI SDK usage |
| **LiteLLM callback** | Very low — one-liner | `litellm.success_callback = ["langfuse"]` | All 100+ LiteLLM providers | Multi-provider or gateway setups |
| **OpenLLMetry** | Low — `init()` at startup | None after `Traceloop.init()` | 20+ providers and frameworks | Polyglot stacks, complex multi-framework apps |
| **OpenLIT** | Low — `init()` at startup | None after `openlit.init()` | LLM providers, vector stores, GPU metrics | Multi-language teams (Python + Java/Go) |

### Choosing an integration

```
Are you using LangChain or LangGraph?
  └─ Yes → langfuse.callback.CallbackHandler

Are you calling OpenAI directly (no proxy)?
  └─ Yes → from langfuse.openai import openai

Are you routing calls through LiteLLM?
  └─ Yes → litellm.success_callback = ["langfuse"]

Do you use multiple providers and want one setup call covering all of them?
  └─ Yes → OpenLLMetry (Traceloop) or OpenLIT

Do you have non-Python services to trace in the same project?
  └─ Yes → OpenLIT (broadest OTel language support)
```

---

## 9. A Note on Transport — OTLP, Not Native SDK Methods

All framework integrations described in this file send traces via the **OTLP (OpenTelemetry Protocol)** HTTP endpoint at `https://cloud.langfuse.com/api/public/otel` — not via the Langfuse native SDK's ingestion methods used by `get_client()` or `@observe` directly.

This means:
- Framework integrations work in any language or runtime that supports OTLP — they are not Python-only
- OTel-based integrations (OpenLLMetry, OpenLIT, LiteLLM-OTLP) are interoperable with the rest of your OTel infrastructure (Jaeger, Prometheus, Grafana)
- You can send traces from framework integrations and from the native `@observe` decorator to the same Langfuse project — they will appear as unified traces as long as both use the same OTel trace context

⚠️ If you configure both a framework integration and a manual `Langfuse()` client to point at the same project, verify that span parent-child linking is correct. Both paths share the OTel context propagation mechanism, so nesting should work — but test with a single end-to-end trace before deploying to production.

---

**Prerequisites**: [03_instrumentation.md](03_instrumentation.md) — `@observe` decorator and manual span creation patterns used in section 7 above.

**Next — OTel backend integration**: [../otel_integration/01_otel_backend.md](../otel_integration/01_otel_backend.md) — configuring Langfuse as an OpenTelemetry backend, OTLP endpoint details, authentication, and receiving traces from any OTel-compatible SDK or collector.
