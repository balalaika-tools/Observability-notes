# Agent Tracing Design Cheatsheet

Last verified against OpenTelemetry GenAI conventions and Langfuse OTLP mapping on 2026-07-24.

## 🏗️ Default Architecture

```text
application: standard OTel + gen_ai.* + app.*
  -> OpenTelemetry Collector
       |-- traces/general --------------------> APM backend
       |     HTTP, RPC, DB, queues, runtime       X-Ray, Groundcover, etc.
       |
       |-- traces/agent
             Langfuse-only transform
             -> OTLP/HTTP --------------------> agent backend
                                                   Langfuse, etc.
```

Design rules:

- Applications know only Collector endpoints. Credentials, exporters, and `langfuse.*` stay in Collector configuration.
- The general backend receives request and dependency traces. It may also receive agent traces for operational debugging.
- The agent backend receives the complete GenAI workflow: root, agent, retrieval, model, tool, guardrail, and relevant HTTP spans.
- Never keep only spans containing `gen_ai.request.model`; that produces orphaned and unreadable agent traces.
- Prefer standard semantic conventions, then documented `app.*` attributes for product semantics.
- Pin the GenAI semantic-convention version. These conventions are still evolving.

> ⚠️ **Watch out:** Never filter to only spans containing `gen_ai.request.model` — sending them without root and parent context creates orphaned, unreadable agent traces.

## 🔄 Recommended Trace Shape

```text
POST /chat                              HTTP server span
  invoke_workflow support_rag           workflow
    invoke_agent support_agent          agent run
      retrieval product_docs            retrieval
      chat provider-model               inference
      execute_tool order_lookup         tool
      chat provider-model               final inference
      app.guardrail output_policy       product-specific check
```

Create spans for operations that explain latency, cost, failure, or decisions. Do not span every function.

## 📦 Baseline Fields

Resource attributes belong on every service:

| Field | Use |
| --- | --- |
| `service.name` | Stable service identity |
| `service.version` | Git SHA, image tag, or release |
| `deployment.environment.name` | `dev`, `staging`, `prod` |
| `cloud.region` | Regional failures and latency |

HTTP spans should rely on standard instrumentation:

| Field | Use |
| --- | --- |
| `http.request.method` | Request method |
| `http.route` | Low-cardinality route template |
| `http.response.status_code` | Response status |
| `server.address` | Destination or server |
| `error.type` | Low-cardinality failure class |

Never use raw URLs, prompt text, user IDs, or request IDs as span names.

## 📦 Meaningful GenAI Fields

Use only fields available at the relevant boundary.

| Area | Core fields |
| --- | --- |
| Workflow | `gen_ai.operation.name="invoke_workflow"`, `gen_ai.workflow.name`, `error.type` on failure |
| Agent | `gen_ai.operation.name="invoke_agent"`, `gen_ai.provider.name`, `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.agent.version`, optional `gen_ai.agent.description`, `gen_ai.conversation.id`, and request model when one model defines the agent |
| Inference | `gen_ai.operation.name`, `gen_ai.provider.name`, `server.address`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons` |
| Streaming | `gen_ai.request.stream`, `gen_ai.response.time_to_first_chunk` in seconds |
| Usage | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.reasoning.output_tokens` |
| Model behavior | `gen_ai.output.type`, selected `gen_ai.request.*` parameters such as `temperature`, `top_p`, `top_k`, `max_tokens`, `choice.count`, `seed`, `stop_sequences`, penalties, and `reasoning.level` |
| Prompt identity | `gen_ai.prompt.name`; use an `app.*` version field if no standard version field is available |
| Tool | `gen_ai.operation.name="execute_tool"`, `gen_ai.tool.name`, `gen_ai.tool.type`, `gen_ai.tool.call.id`, optionally `gen_ai.tool.description` |
| Retrieval | `gen_ai.operation.name="retrieval"`, `gen_ai.data_source.id`, `gen_ai.request.top_k` |

Sensitive, opt-in fields:

- `gen_ai.system_instructions`
- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.tool.definitions`
- `gen_ai.tool.call.arguments`
- `gen_ai.tool.call.result`
- `gen_ai.retrieval.query.text`
- `gen_ai.retrieval.documents`

Capture these only after redaction, size limits, and a documented retention decision.

Minimal inference example:

```python
with tracer.start_as_current_span("chat provider-model") as span:
    span.set_attribute("gen_ai.operation.name", "chat")
    span.set_attribute("gen_ai.provider.name", "provider-name")
    span.set_attribute("gen_ai.request.model", "provider-model")

    response = call_model()

    span.set_attribute("gen_ai.response.model", response.model)
    span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)
```

## 🏷️ Business Attributes

Keep names stable and values bounded.

| Field | Example |
| --- | --- |
| `app.telemetry.category` | `genai` |
| `app.workflow.version` | `support-agent-v12` |
| `app.trace.dimension.tenant_tier` | `enterprise` |
| `app.trace.dimension.feature` | `support_chat` |
| `app.trace.dimension.experiment` | `reranker_v2` |
| `app.observation.dimension.tool_family` | `account_lookup` |
| `app.observation.dimension.retrieval_strategy` | `hybrid_v3` |
| `app.outcome` | `success`, `error`, `timeout`, `blocked` |
| `app.agent.stop_reason` | `completed`, `step_limit`, `guardrail`, `cancelled` |
| `app.agent.fallback.used` | `true` or `false` |
| `app.observation.completion_started_at` | Absolute ISO-8601 first-chunk timestamp |

`user.id`, `session.id`, and opaque tenant IDs may be useful on traces, but must not become metric labels.

## 🔗 Baggage Across Services

Use baggage only for small, allowlisted request facts that must be available in
more than one service. Trace context connects spans; baggage carries additional
values; span attributes make those values searchable. These are separate steps.

```text
first trusted service
  -> authenticate and derive trusted values
  -> set them on the already-running root SERVER span
  -> attach them as baggage
  -> instrumented client injects trace context + baggage
  -> downstream server extracts both before starting its SERVER span
  -> baggage span processor copies allowlisted values onto new spans
```

Recommended baggage-to-attribute keys:

| Baggage key | Span attribute | Why propagate it |
| --- | --- | --- |
| `session.id` | `session.id` | Correlate one authorized session across service spans. |
| `app.trace.dimension.tenant_tier` | Same key | Compare bounded service tiers. |
| `app.trace.dimension.feature` | Same key | Identify the product path without high-cardinality payloads. |
| `app.trace.dimension.experiment` | Same key | Compare a bounded experiment variant across the whole trace. |

Implementation rules:

- Set `OTEL_PROPAGATORS=tracecontext,baggage` consistently. This is the Python
  default, but explicit deployment configuration prevents service drift.
- Define one allowlisted `SpanProcessor` implementation in the shared telemetry
  package. Register one instance on the SDK `TracerProvider` in every service
  process where baggage should become span attributes.
- Register the processor after the provider exists and before the service
  accepts requests or starts background spans. With
  `opentelemetry-instrument`, add it to the provider created by the agent; do
  not create a second provider.
- The processor runs at span start. It enriches downstream `SERVER` spans
  because baggage is extracted first, and it enriches all later local spans.
- The first service usually creates baggage after its root `SERVER` span has
  started. Set those attributes directly on `trace.get_current_span()`, then
  attach the baggage so the processor can handle later spans.
- Do not manually call `inject()` or `extract()` for FastAPI/HTTPX when their
  instrumentations already propagate context. Use manual propagation for
  unsupported transports, custom carriers, and focused tests.
- Drop or sanitize caller-supplied baggage at an untrusted ingress. Never
  propagate secrets, raw prompts, documents, email addresses, or unrestricted
  user input.

Registration shape:

```python
provider = trace.get_tracer_provider()
provider.add_span_processor(AllowlistedBaggageSpanProcessor())
```

The processor is an application SDK component, not a Collector processor. See
[Multi-Service Examples](../opentelemetry/04_multi_service_examples.md) for the
complete gateway, downstream, and manual-transport patterns.

## 🔗 Collector-Side Langfuse Mapping

Langfuse directly understands model, usage, user/session, status, and environment fields. Transform only schema gaps and filterable business dimensions.

| Neutral source | Langfuse destination | Handling |
| --- | --- | --- |
| `gen_ai.request.model` / `gen_ai.response.model` | observation model | Direct |
| `gen_ai.usage.*` | usage details | Direct |
| `user.id`, `session.id` | user/session | Direct |
| `deployment.environment.name` | environment | Direct |
| `gen_ai.workflow.name` | `langfuse.trace.name` | Collector copy |
| `service.version` | `langfuse.release` | Collector copy |
| `app.workflow.version` | `langfuse.version` | Collector copy |
| `app.trace.dimension.tenant_tier` | `langfuse.trace.metadata.tenant_tier` | Collector copy |
| `app.observation.dimension.tool_family` | `langfuse.observation.metadata.tool_family` | Collector copy |
| `app.observation.completion_started_at` | `langfuse.observation.completion_start_time` | Collector copy |

Example:

```yaml
processors:
  transform/langfuse:
    error_mode: ignore
    trace_statements:
      - |
        set(span.attributes["langfuse.trace.name"],
            span.attributes["gen_ai.workflow.name"])
        where span.attributes["gen_ai.workflow.name"] != nil
      - |
        set(span.attributes["langfuse.trace.metadata.tenant_tier"],
            span.attributes["app.trace.dimension.tenant_tier"])
        where span.attributes["app.trace.dimension.tenant_tier"] != nil
      - |
        set(span.attributes["langfuse.observation.metadata.tool_family"],
            span.attributes["app.observation.dimension.tool_family"])
        where span.attributes["app.observation.dimension.tool_family"] != nil
      - |
        set(span.attributes["langfuse.release"],
            resource.attributes["service.version"])
        where resource.attributes["service.version"] != nil
      - |
        set(span.attributes["langfuse.version"],
            span.attributes["app.workflow.version"])
        where span.attributes["app.workflow.version"] != nil
      - |
        set(span.attributes["langfuse.observation.completion_start_time"],
            span.attributes["app.observation.completion_started_at"])
        where span.attributes["app.observation.completion_started_at"] != nil
```

Apply it only to the Langfuse pipeline:

```yaml
service:
  pipelines:
    traces/general:
      receivers: [otlp, otlp/genai]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/apm]

    traces/agent:
      receivers: [otlp/genai]
      processors: [memory_limiter, transform/langfuse, batch]
      exporters: [otlphttp/langfuse]
```

## 🛠️ Time to First Chunk

> ⚠️ **Watch out:** `gen_ai.response.time_to_first_chunk` is a duration in seconds; `langfuse.observation.completion_start_time` is an absolute ISO-8601 timestamp — copying one into the other produces incorrect data.

Do not directly copy `gen_ai.response.time_to_first_chunk` into `langfuse.observation.completion_start_time`:

- the GenAI field is a duration in seconds;
- the Langfuse field is an absolute ISO-8601 timestamp.

Record both neutral facts when streaming:

```python
started = time.perf_counter()

for index, chunk in enumerate(stream):
    if index == 0:
        span.set_attribute(
            "gen_ai.response.time_to_first_chunk",
            time.perf_counter() - started,
        )
        span.set_attribute(
            "app.observation.completion_started_at",
            datetime.now(timezone.utc).isoformat(),
        )
```

Also emit `gen_ai.client.operation.time_to_first_chunk` for aggregate monitoring.

## ✅ Production Checklist

- Propagate W3C trace context across HTTP, queues, tools, and workers.
- Propagate only allowlisted, non-sensitive baggage.
- Register the baggage span processor once per service process before requests
  or background work begin.
- Set baggage-derived attributes directly on the first service's already-running
  root span.
- Copy trace-wide filter dimensions onto every relevant observation.
- Preserve the root and parents when routing to the agent backend.
- Set span status and low-cardinality `error.type` on failures.
- Redact content before backend-specific fan-out.
- Tail-sample by complete trace, never by isolated GenAI leaves.

> 💡 **Key insight:** Tail-sampling isolated GenAI leaf spans discards the root span and sibling context that make a trace debuggable — always sample the complete trace.

- Keep metrics independent from trace sampling.
- Canary one trace containing root, retrieval, model, and tool spans before rollout.
- Test both outputs: the APM payload remains neutral; the Langfuse payload contains only intended mappings.

## 🔗 References

- [Detailed OTLP ingestion and mapping](../langfuse/03_otel_ingestion_and_mapping.md)
- [Detailed GenAI observability](../opentelemetry/06_genai_and_llm_observability.md)
- [Multi-service propagation and baggage](../opentelemetry/04_multi_service_examples.md)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [Langfuse OpenTelemetry attribute mapping](https://langfuse.com/integrations/native/opentelemetry)
