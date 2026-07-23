# OpenTelemetry Concepts

OpenTelemetry, usually shortened to OTel, is the vendor-neutral standard for
creating, correlating, processing, and exporting telemetry data. It gives your
application one observability vocabulary instead of one SDK per backend.

The most important mental model is this:

```text
application code and instrumented libraries
  -> OpenTelemetry API
  -> OpenTelemetry SDK providers
       resource identity
       instrumentation scopes
       samplers, processors, metric readers, log processors
       exporters
  -> OTLP or another export format
  -> OpenTelemetry Collector
       receivers -> processors -> exporters
  -> observability backends
       traces, metrics, logs, LLM observability, dashboards, alerts
```

OpenTelemetry does not replace your backend. It standardizes what your code
emits and how telemetry moves to tools such as Jaeger, Prometheus, Grafana,
Datadog, Honeycomb, Elastic, or Langfuse.

## What OpenTelemetry Is Not

It is important to be precise:

- OpenTelemetry is not a tracing database like Jaeger.
- OpenTelemetry is not a metrics database like Prometheus.
- OpenTelemetry is not a dashboarding product like Grafana.
- OpenTelemetry is not a log search product.
- OpenTelemetry is not an LLM observability product like Langfuse.
- OpenTelemetry is not only a protocol.
- OpenTelemetry is not only auto-instrumentation.

OpenTelemetry is a standard plus implementations:

| Part | What it means |
| --- | --- |
| Specification | Cross-language rules for APIs, SDKs, data models, context propagation, exporters, and behavior. |
| APIs | Interfaces your code and libraries call to create spans, metrics, logs, context, and baggage. |
| SDKs | Runtime implementations that sample, aggregate, process, batch, and export telemetry. |
| Semantic conventions | Standard names for attributes, metrics, spans, resources, and common operations. |
| OTLP | OpenTelemetry Protocol, the native transport format for OTel telemetry. |
| Collector | A vendor-neutral telemetry proxy that receives, processes, routes, and exports data. |
| Instrumentation libraries | Packages that automatically instrument frameworks and clients such as FastAPI, HTTPX, SQLAlchemy, Redis, Celery, and LLM SDKs. |

The reason this is powerful is separation of concerns. Application code should
describe what happened. Pipeline configuration should decide where that data
goes. Backends should store, query, visualize, and alert on it.

## The Problem OTel Solves

Without OTel, observability often looks like this:

```text
service code
  -> vendor A tracing SDK
  -> vendor B metrics SDK
  -> custom log format
  -> framework-specific propagation
  -> backend-specific dashboards and alerts
```

Changing vendors, adding a second backend, or sharing instrumentation between
teams becomes painful because observability concerns leak into product code.

With OTel, the shape becomes:

```text
service code
  -> OpenTelemetry API and instrumentation
  -> OpenTelemetry SDK
  -> OTLP
  -> Collector
  -> one or more backends
```

That gives you:

- one way to create traces, metrics, and logs;
- one context propagation model across services;
- one naming scheme for common attributes;
- one transport protocol for telemetry;
- one pipeline layer where you can batch, redact, sample, transform, and route;
- freedom to change or add backends without rewriting application code.

## The End-To-End Lifecycle

A concrete request makes the architecture easier to understand. Imagine a
FastAPI `chat-api` service receives `POST /chat`, calls a retrieval service,
calls an LLM provider, writes logs, and records token metrics.

```text
client
  -> chat-api POST /chat
       server span: POST /chat
       manual span: rag.retrieve
       client span: POST retrieval /search
         -> retrieval service
              server span: POST /search
              manual span: vector_db.query
       manual span: prompt.build
       client span: chat gpt-4o-mini
       metrics: llm.requests, llm.tokens, llm.request.duration
       logs: "retrieval returned 5 docs", "model call timed out"
  -> response
```

Here is what OTel does along the way:

1. The HTTP server instrumentation sees the incoming request and starts a
   `SERVER` span.
2. The propagator extracts trace headers if the caller sent them. If no trace
   exists, this service starts a new root trace.
3. The active span is stored in the current execution context, so code deeper
   in the call stack can create child spans without passing IDs manually.
4. Manual instrumentation creates spans around business operations that the
   framework cannot understand, such as `rag.retrieve` or `prompt.build`.
5. HTTP client instrumentation creates outbound `CLIENT` spans and injects the
   current trace context into request headers.
6. The downstream service extracts those headers and creates spans with the
   same `trace_id`, so the backend can assemble one distributed trace.
7. Metrics are recorded as measurements with low-cardinality attributes such as
   route, model, provider, and outcome.
8. Logs created while a span is active can include the current `trace_id` and
   `span_id`, which lets a backend link log lines to the trace.
9. When spans end, the SDK passes them through processors, usually a batch
   processor, and exports them over OTLP.
10. The Collector receives telemetry, applies shared policy such as batching,
    memory limits, redaction, enrichment, sampling, and routing.
11. Backends store and visualize the data. Metrics usually drive alerts,
    traces explain example requests, and logs provide local details.

The glue is not magic. It is mainly:

- a shared trace context: `trace_id`, `span_id`, parent relationship, sampled flag;
- a resource identity: `service.name`, service version, environment, instance;
- instrumentation scopes: which library or module emitted the telemetry;
- semantic conventions: common attribute names such as `http.route` or `gen_ai.request.model`;
- propagation headers: usually W3C `traceparent`, `tracestate`, and `baggage`;
- SDK and Collector pipelines that export the data consistently.

## The Six Layers

Every OpenTelemetry setup has the same conceptual layers, even if a small local
demo collapses some of them.

| Layer | Owns | Example |
| --- | --- | --- |
| Application work | The actual operation users care about. | Handle `/chat`, query vector DB, call model. |
| Instrumentation | Creates telemetry from that work. | Auto FastAPI span, manual `rag.retrieve` span, token counter. |
| SDK | Runtime behavior inside the process. | Providers, resources, samplers, processors, metric readers, exporters. |
| Transport | How telemetry leaves the process. | OTLP/HTTP to `http://otel-collector:4318/v1/traces`. |
| Collector pipeline | Shared telemetry infrastructure. | Receive OTLP, batch, redact, tail sample, export to multiple backends. |
| Backend | Storage, query, dashboards, alerts, workflows. | Prometheus metrics, Jaeger traces, Langfuse LLM traces. |

When notes or docs say "configure OpenTelemetry", ask which layer they mean.
Many confusions come from mixing these layers together.

## Signals

OpenTelemetry supports multiple signals. For application observability, the
main three are traces, metrics, and logs. Baggage is context that can travel
with those signals. Profiles are also part of the OTel ecosystem, but these
notes focus on traces, metrics, logs, and baggage because they are the core
signals used by most services and LLM applications today.

### Traces

A trace describes one execution path through a system. It answers:

```text
What happened to this specific request?
Where did time go?
Which downstream call failed?
Which services participated?
What was the parent-child order of operations?
```

A trace is made of spans that share a `trace_id`.

```text
trace_id: 9f4...

POST /chat                         SERVER span in chat-api
  rag.retrieve                     INTERNAL span in chat-api
    POST retrieval /search         CLIENT span in chat-api
      POST /search                 SERVER span in retrieval service
        vector_db.query            CLIENT or INTERNAL span
  prompt.build                     INTERNAL span
  chat gpt-4o-mini                 CLIENT span to model provider
```

A span represents one operation or unit of work. Important span fields are:

| Field | Meaning |
| --- | --- |
| `trace_id` | Identifier shared by all spans in the same trace. |
| `span_id` | Identifier for this span. |
| `parent_span_id` | The caller span. Empty for a root span. |
| `name` | Low-cardinality operation name, for example `POST /chat` or `rag.retrieve`. |
| `kind` | Role of the span: `SERVER`, `CLIENT`, `INTERNAL`, `PRODUCER`, or `CONSUMER`. |
| timestamps | Start time, end time, and duration. |
| attributes | Key-value metadata such as route, model, DB system, outcome. |
| events | Timestamped happenings inside the span, such as an exception or retry. |
| links | References to related spans that are not parent-child. Useful for queues and batch work. |
| status | Whether the operation ended in error. |

Span names should be stable and low-cardinality:

| Good | Bad |
| --- | --- |
| `GET /users/{id}` | `GET /users/123456` |
| `rag.retrieve` | `retrieve query "how do refunds work"` |
| `chat gpt-4o-mini` | `chat user@example.com gpt-4o-mini` |

Put variable detail in attributes only when it is safe and bounded. Avoid raw
prompts, emails, full URLs, request bodies, document content, and user-provided
strings in general-purpose span attributes.

#### Span Kind

Span kind describes the role of the operation:

| Kind | Use for |
| --- | --- |
| `SERVER` | Receiving a request from another process, such as an HTTP endpoint. |
| `CLIENT` | Calling another process, such as HTTP, DB, Redis, vector DB, or LLM provider. |
| `INTERNAL` | Work inside the current process, such as prompt building or reranking. |
| `PRODUCER` | Publishing a message to a queue or stream. |
| `CONSUMER` | Receiving or processing a message from a queue or stream. |

Span kind helps trace backends understand the boundary between services. For an
HTTP call, the caller usually has a `CLIENT` span and the callee usually has a
`SERVER` span. They share a trace ID, but each service owns its own span.

#### Span Attributes, Events, And Links

Use attributes for searchable facts about the operation:

```text
http.request.method = POST
http.route = /chat
http.response.status_code = 200
gen_ai.provider.name = openai
gen_ai.request.model = gpt-4o-mini
rag.retrieval.strategy = hybrid
rag.top_k = 5
```

Use events for timestamped details inside a span:

```text
event: retry.scheduled
  retry.attempt = 2
  retry.delay_ms = 250

event: exception
  exception.type = TimeoutError
  exception.message = request timed out
```

Use links when there is a relationship but not a direct parent-child hierarchy.
For example, a batch job that processes 100 messages may link to the producing
spans rather than pretending one message is the parent of the whole batch.

#### Complete Worked Trace

The field list becomes easier to understand when every field belongs to one
request. This teaching example deliberately includes:

- a successful HTTP request with a failed child operation;
- nested internal and client spans;
- attributes, events, an exception, and span status;
- a queue producer and a consumer in a new trace;
- a span link between the two traces;
- resource and instrumentation-scope metadata.

The application accepts a chat request, retrieves context, calls an LLM twice,
handles the provider failure, publishes an asynchronous result, and later
persists that result:

```text
Trace A: 4bf92f3577b34da6a3ce929d0e0e4736

POST /chat                                      SERVER    2.850 s  UNSET
├── rag.retrieve                                INTERNAL  0.320 s  UNSET
│   └── POST /vector-search                     CLIENT    0.280 s  UNSET
├── chat claude-sonnet                          CLIENT    2.100 s  ERROR
└── send exception-analysis-results             PRODUCER  0.040 s  UNSET

Trace B: 9a82a1c307ab4f18b029d712f346a015

process exception-analysis-results              CONSUMER  0.740 s  UNSET
└── INSERT analysis_result                      CLIENT    0.120 s  UNSET

process exception-analysis-results
  -- link --> send exception-analysis-results in Trace A
```

Trace A is one tree because its spans share a trace ID and use parent-child
relationships. The consumer intentionally starts Trace B as a new root. Its
link preserves the causal relationship without pretending that work performed
later by another process is nested synchronous work.

Continuing the producer trace into the consumer is also valid for many
single-message flows. A new trace plus a link is useful when the queue boundary
is also a lifecycle boundary, or when one consumer operation represents a
batch with several producing contexts. Choose one policy deliberately and
apply it consistently.

##### One Span With Every Major Field

This is a normalized teaching representation of the failed LLM span. It is not
literal OTLP JSON: OTLP uses its own protobuf/JSON encoding, and console
exporters have a language-specific debug format.

```json
{
  "identity": {
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "span_id": "7e31fbd423ab9c11",
    "parent_span_id": "00f067aa0ba902b7",
    "trace_flags": "01",
    "trace_state": ""
  },
  "name": "chat claude-sonnet",
  "kind": "CLIENT",
  "start_time": "2026-07-23T10:15:30.500Z",
  "end_time": "2026-07-23T10:15:32.600Z",
  "duration_ms": 2100,
  "status": {
    "code": "ERROR",
    "description": "Bedrock failed after retries"
  },
  "attributes": {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": "aws.bedrock",
    "gen_ai.request.model": "claude-sonnet",
    "server.address": "bedrock-runtime.us-east-1.amazonaws.com",
    "retry.count": 1,
    "app.outcome": "provider_failed",
    "error.type": "ProviderTimeoutError"
  },
  "events": [
    {
      "name": "request.sent",
      "timestamp": "2026-07-23T10:15:30.520Z",
      "attributes": {
        "retry.attempt": 1
      }
    },
    {
      "name": "retry.scheduled",
      "timestamp": "2026-07-23T10:15:31.300Z",
      "attributes": {
        "retry.next_attempt": 2,
        "retry.delay_ms": 200,
        "retry.reason": "provider_timeout"
      }
    },
    {
      "name": "request.sent",
      "timestamp": "2026-07-23T10:15:31.500Z",
      "attributes": {
        "retry.attempt": 2
      }
    },
    {
      "name": "exception",
      "timestamp": "2026-07-23T10:15:32.590Z",
      "attributes": {
        "exception.type": "ProviderTimeoutError",
        "exception.message": "Bedrock request timed out on attempt 2",
        "exception.stacktrace": "Traceback (most recent call last): ...",
        "exception.escaped": false
      }
    }
  ],
  "links": [],
  "dropped_attributes_count": 0,
  "dropped_events_count": 0,
  "dropped_links_count": 0,
  "resource": {
    "service.name": "chat-api",
    "service.version": "1.0.0",
    "deployment.environment.name": "development"
  },
  "instrumentation_scope": {
    "name": "example.complete-trace",
    "version": "1.0.0"
  }
}
```

Read it in layers:

| Layer | Fields | Meaning |
| --- | --- | --- |
| Identity and position | `trace_id`, `span_id`, `parent_span_id`, trace flags and state | Which trace this operation belongs to, where it sits in the tree, and propagation/sampling state. |
| Operation | `name`, `kind` | What happened and whether this process acted as server, client, internal code, producer, or consumer. |
| Time | Start and end timestamps | The measured interval. Duration is normally derived from these timestamps rather than assigned by application code. |
| Description | Attributes | Searchable facts about the operation as a whole. |
| Timeline | Events | Timestamped facts that happened during the operation. |
| Non-tree relationships | Links | Causal references that do not imply a parent-child interval. |
| Outcome | Status | Whether this operation failed. The exception event explains the failure; status classifies the final outcome. |
| Emitter identity | Resource and instrumentation scope | Which service emitted the span and which code created it. |
| Data loss | Dropped counts | Whether SDK limits caused attributes, events, or links to be discarded. |

The LLM child is `ERROR`, but the root HTTP span remains `UNSET` and records
`http.response.status_code=202`. That is correct here: the application handled
the provider failure and successfully accepted asynchronous work. A parent's
status describes the parent operation, not the worst status among its children.

The consumer span has a different trace ID and no parent, but contains this
relationship:

```json
{
  "name": "process exception-analysis-results",
  "trace_id": "9a82a1c307ab4f18b029d712f346a015",
  "parent_span_id": null,
  "links": [
    {
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "ae32b4f11d98c307",
      "attributes": {
        "link.type": "produced_by",
        "messaging.message.id": "message-987"
      }
    }
  ]
}
```

##### Runnable Python Simulation

This is a single-process simulation, not a substitute for FastAPI, HTTP client,
SQS, LLM, and database instrumentations. It keeps the infrastructure fake so
the span mechanics remain visible. It uses `SimpleSpanProcessor` only to make
console output deterministic for a short-lived demo; production services
normally use `BatchSpanProcessor` and OTLP.

Use Python 3.11 or later:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install \
  'opentelemetry-api>=1.39,<2' \
  'opentelemetry-sdk>=1.39,<2'
```

Save the following as `complete_trace_demo.py` and run
`python complete_trace_demo.py`:

```python
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Link, SpanKind, Status, StatusCode


resource = Resource.create(
    {
        "service.name": "chat-api",
        "service.version": "1.0.0",
        "deployment.environment.name": "development",
    }
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    SimpleSpanProcessor(ConsoleSpanExporter())
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("example.complete-trace", "1.0.0")


class ProviderTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    job_id: str
    body: dict[str, Any]
    headers: dict[str, str]


def fake_vector_search(query: str) -> list[dict[str, Any]]:
    time.sleep(0.01)
    return [
        {"id": f"doc-{index}", "score": 0.9 - index * 0.05}
        for index in range(1, 6)
    ]


def fake_llm_call(attempt: int) -> str:
    time.sleep(0.01)
    raise ProviderTimeoutError(
        f"Bedrock request timed out on attempt {attempt}"
    )


def retrieve_documents(query: str) -> list[dict[str, Any]]:
    with tracer.start_as_current_span(
        "rag.retrieve",
        kind=SpanKind.INTERNAL,
        attributes={
            "rag.query.length": len(query),
            "rag.top_k": 5,
            "rag.reranking.enabled": True,
        },
    ) as retrieval_span:
        with tracer.start_as_current_span(
            "POST /vector-search",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": "POST",
                "url.full": (
                    "https://retrieval-service/vector-search"
                ),
                "server.address": "retrieval-service",
                "server.port": 443,
            },
        ) as client_span:
            documents = fake_vector_search(query)
            client_span.set_attribute(
                "http.response.status_code", 200
            )

        retrieval_span.add_event(
            "reranking.completed",
            {
                "rag.documents.before": 20,
                "rag.documents.after": len(documents),
                "rag.reranker.model": "cohere-rerank-v3",
            },
        )
        retrieval_span.set_attribute(
            "rag.documents.returned", len(documents)
        )
        retrieval_span.set_attribute("app.outcome", "success")
        return documents


def call_llm_with_retry(prompt: str) -> str | None:
    with tracer.start_as_current_span(
        "chat claude-sonnet",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.request.model": "claude-sonnet",
            "server.address": (
                "bedrock-runtime.us-east-1.amazonaws.com"
            ),
            "app.prompt.length": len(prompt),
        },
    ) as span:
        for attempt in (1, 2):
            span.add_event(
                "request.sent", {"retry.attempt": attempt}
            )
            try:
                return fake_llm_call(attempt)
            except ProviderTimeoutError as exc:
                if attempt == 1:
                    span.add_event(
                        "retry.scheduled",
                        {
                            "retry.next_attempt": 2,
                            "retry.delay_ms": 5,
                            "retry.reason": "provider_timeout",
                        },
                    )
                    time.sleep(0.005)
                    continue

                # The exception is handled inside this span, so record both
                # its details and the operation's final status explicitly.
                span.record_exception(
                    exc,
                    attributes={
                        "retry.attempt": attempt,
                        "exception.escaped": False,
                    },
                )
                span.set_attribute("retry.count", attempt - 1)
                span.set_attribute(
                    "app.outcome", "provider_failed"
                )
                span.set_attribute(
                    "error.type", type(exc).__qualname__
                )
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description="Bedrock failed after retries",
                    )
                )
                return None

    raise AssertionError("unreachable")


def publish_result(
    *, job_id: str, result: dict[str, Any]
) -> QueueMessage:
    message_id = str(uuid.uuid4())

    with tracer.start_as_current_span(
        "send exception-analysis-results",
        kind=SpanKind.PRODUCER,
        attributes={
            "messaging.system": "aws_sqs",
            "messaging.destination.name": (
                "exception-analysis-results"
            ),
            "messaging.operation.name": "send",
            "messaging.operation.type": "send",
            "messaging.message.id": message_id,
            "app.job.id": job_id,
        },
    ) as span:
        time.sleep(0.005)
        headers: dict[str, str] = {}
        propagate.inject(headers)
        span.add_event(
            "message.published",
            {
                "messaging.message.body.size": len(
                    str(result).encode()
                )
            },
        )

    return QueueMessage(
        message_id=message_id,
        job_id=job_id,
        body=result,
        headers=headers,
    )


def persist_result(result: dict[str, Any]) -> None:
    with tracer.start_as_current_span(
        "INSERT analysis_result",
        kind=SpanKind.CLIENT,
        attributes={
            "db.system.name": "postgresql",
            "db.namespace": "exception_management",
            "db.operation.name": "INSERT",
            "db.collection.name": "analysis_result",
            "server.address": "results-db.internal",
            "server.port": 5432,
        },
    ):
        time.sleep(0.005)


def consume_message(message: QueueMessage) -> None:
    extracted = propagate.extract(message.headers)
    producer_context = trace.get_current_span(
        extracted
    ).get_span_context()
    producer_link = Link(
        producer_context,
        attributes={
            "link.type": "produced_by",
            "messaging.message.id": message.message_id,
        },
    )

    # An empty Context prevents the producer from becoming the parent.
    with tracer.start_as_current_span(
        "process exception-analysis-results",
        context=otel_context.Context(),
        kind=SpanKind.CONSUMER,
        links=[producer_link],
        attributes={
            "messaging.system": "aws_sqs",
            "messaging.destination.name": (
                "exception-analysis-results"
            ),
            "messaging.operation.name": "process",
            "messaging.operation.type": "process",
            "messaging.message.id": message.message_id,
            "app.job.id": message.job_id,
        },
    ) as span:
        span.add_event(
            "message.received",
            {"messaging.message.delivery_count": 1},
        )
        persist_result(message.body)


def handle_chat_request(query: str) -> QueueMessage:
    job_id = str(uuid.uuid4())
    with tracer.start_as_current_span(
        "POST /chat",
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": "POST",
            "http.route": "/chat",
            "url.path": "/chat",
            "url.scheme": "https",
            "server.address": "chat-api",
        },
    ) as root_span:
        root_span.add_event(
            "request.validated",
            {"validation.schema": "chat-request-v2"},
        )
        documents = retrieve_documents(query)
        answer = call_llm_with_retry(
            f"{query}\n\nRetrieved document count: {len(documents)}"
        )

        if answer is None:
            result: dict[str, Any] = {
                "status": "provider_failed",
                "answer": None,
            }
            root_span.add_event(
                "llm.unavailable",
                {"fallback.action": "publish_failure_result"},
            )
        else:
            result = {"status": "completed", "answer": answer}

        message = publish_result(job_id=job_id, result=result)
        root_span.set_attribute("http.response.status_code", 202)
        root_span.set_attribute("app.job.id", job_id)
        root_span.add_event(
            "response.created",
            {"response.mode": "async"},
        )
        return message


def main() -> None:
    message = handle_chat_request(
        "How does OpenTelemetry tracing work?"
    )
    time.sleep(0.01)
    consume_message(message)
    provider.shutdown()


if __name__ == "__main__":
    main()
```

The console output will use generated IDs and actual measured timestamps, so it
will not match the illustrative IDs above. Verify these invariants instead:

- the first five spans share one trace ID and follow the shown parent IDs;
- every span has its own span ID;
- the LLM span has an exception event and `ERROR` status;
- the root span remains `UNSET` with HTTP status `202`;
- the consumer has a new trace ID, no parent, and one link to the producer;
- the database span is a child of the consumer;
- every span carries the simulation's `chat-api` resource; in a real
  deployment, the worker would normally have its own service resource;
- `get_tracer()` attaches the `example.complete-trace` instrumentation scope,
  although some console-exporter versions omit it from their debug rendering.

The SDK generates IDs, captures start and end times, calculates parentage from
the active context, and tracks limit-related dropped counts. Instrumentation
chooses the operation name and kind and adds domain attributes and events.
Provider setup supplies the resource; `get_tracer()` supplies the
instrumentation scope. Status can be explicit, or the Python context manager
can record an escaped exception and set `ERROR` automatically. If code catches
an exception inside the span, as this demo does for retry handling, record the
exception and final error status explicitly.

In a real service, let FastAPI/ASGI, HTTPX, the queue client, and the database
instrumentation create their protocol spans and propagate context. Keep manual
spans for business operations such as `rag.retrieve`. Export with OTLP through
a batch processor, and never put raw prompts, retrieved documents, queue
payloads, credentials, or personal data into general-purpose attributes.
The GenAI and messaging conventions evolve faster than core tracing, so pin
the convention version used by instrumentation packages and recheck these
names during upgrades.

### Metrics

Metrics are numerical measurements aggregated over time. They answer:

```text
How is the system behaving in aggregate?
Is latency above the SLO?
Did the error rate spike?
How many tokens are we using per model?
How deep is the queue?
```

Metrics are not "compressed traces". They are their own signal and should be
emitted independently. This matters because traces are often sampled, while
unsampled metrics are the appropriate aggregate signal for dashboards,
autoscaling, request/error rates, and alerts.

"Unsampled" does not mean an accounting ledger. Exporter loss, queue overflow,
process crashes or restarts, cumulative/delta temporality conversion, Collector
failure, and backend rejection can all lose or reset measurements. Use a
transactional billing or audit system for exact money, quota, and compliance
records; use telemetry metrics to operate the system.

OpenTelemetry metrics have a few moving parts:

```text
MeterProvider
  -> Meter
       -> instrument
            -> measurement + attributes
                 -> SDK aggregation
                      -> metric reader
                           -> exporter
```

Common instruments:

| Instrument | Meaning | Example |
| --- | --- | --- |
| Counter | Monotonic total that only increases. | Requests, errors, tokens, jobs completed. |
| UpDownCounter | Value that can increase or decrease through add operations. | In-flight requests, active sessions. |
| Histogram | Distribution of recorded values. | Request duration, token count, document count. |
| ObservableGauge | Current value observed by callback. | Queue depth, cache size, open connections. |

Metric attributes become dimensions. Keep them low-cardinality:

| Good attribute | Why |
| --- | --- |
| `http.route="/chat"` | Bounded route template. |
| `http.response.status_code=500` | Small set of status codes. |
| `gen_ai.request.model="gpt-4o-mini"` | Bounded model list. |
| `app.outcome="error"` | Small outcome enum. |
| `app.tenant.tier="enterprise"` | Small business category. |

Avoid high-cardinality metric attributes:

| Bad attribute | Why |
| --- | --- |
| `user.email` | Personal data and unbounded values. |
| `trace_id` | Unique per trace, explodes time series. |
| `url.full` | Often contains IDs and query strings. |
| `prompt` | Sensitive, huge, and unbounded. |
| `exception.message` | Often unique per error. |

Use metrics for detection. Use traces for investigation. During an incident,
the usual flow is:

```text
metric alert fires
  -> dashboard shows route/model/provider affected
  -> trace query finds example slow/error requests
  -> logs explain local details
```

### Logs

Logs are timestamped records. They answer:

```text
What did the code say at this moment?
What local values or messages were recorded?
What stack trace was produced?
```

OpenTelemetry can do two useful things for logs:

1. It can normalize logs into the OTel log data model and export them through
   an OTel pipeline.
2. It can correlate logs with traces by adding `trace_id` and `span_id` to log
   records created while a span is active.

In Python, full OTel log export support is still marked Development in the
official language status, while the logs signal itself is stable in the OTel
specification. In practice, Python teams often start with normal structured
logging plus trace-log correlation, then decide later whether to export logs
through OTel.

Use structured logs in production:

```json
{
  "timestamp": "2026-06-18T10:15:23Z",
  "level": "INFO",
  "message": "retrieval completed",
  "service.name": "chat-api",
  "trace_id": "9f4...",
  "span_id": "1a2...",
  "rag.returned_documents": 5
}
```

Use span events for events that are only meaningful inside a trace. Use logs
for operational records you may want to query independently.

### Baggage

Baggage is key-value context that propagates with a request across service
boundaries via the W3C `baggage` HTTP header (alongside `traceparent` and
`tracestate`). It answers:

```text
What small, request-level facts should all downstream services be able to see?
```

Examples:

```text
app.tenant.tier = enterprise
app.experiment.variant = reranker_b
app.session.id = opaque-session-id
app.request.class = interactive
```

Baggage travels the entire downstream chain, not just the immediate next hop.
Every service that receives the request can read it.

Baggage is not the same as span attributes:

| | Baggage | Span attribute |
| --- | --- | --- |
| Travels downstream | Yes, to all services in the chain | No, stays in the current span |
| Appears in traces automatically | No | Yes |
| Use when | Downstream services need the value | Only the current service needs it |

Baggage does not appear in a trace, metric, or log unless you explicitly copy
selected keys. To attach a baggage value to the current span:

```python
from opentelemetry import baggage, trace

tenant_tier = baggage.get_baggage("app.tenant.tier")
if tenant_tier:
    trace.get_current_span().set_attribute("app.tenant.tier", tenant_tier)
```

Security rule: baggage is sent across service boundaries and is readable by
every service in the chain. Do not put secrets, API keys, emails, raw prompts,
retrieved documents, access tokens, or personal data in baggage. Treat incoming
baggage as untrusted unless you control the caller.

## API, SDK, And Instrumentation

OpenTelemetry separates the API from the SDK.

The API is what instrumentation code calls:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)


def rerank_documents(query: str, documents: list[str]) -> list[str]:
    with tracer.start_as_current_span("rag.rerank") as span:
        span.set_attribute("rag.candidate_documents", len(documents))
        ranked = reranker.rank(query, documents)
        span.set_attribute("rag.returned_documents", len(ranked))
        return ranked
```

The SDK is what the application configures once at startup:

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

resource = Resource.create(
    {
        "service.name": "chat-api",
        "service.version": "2026.06.18",
        "deployment.environment.name": "production",
    }
)

provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://otel-collector:4318/v1/traces")
    )
)
trace.set_tracer_provider(provider)
```

If no SDK is configured, API calls are no-ops. This is intentional. A library
can safely depend on `opentelemetry-api` and create spans without forcing an
exporter or backend on every application that imports it.

### Providers

Providers are the SDK entry points:

| Provider | Creates | Signal |
| --- | --- | --- |
| `TracerProvider` | `Tracer` instances | Traces |
| `MeterProvider` | `Meter` instances | Metrics |
| `LoggerProvider` | OTel logger instances or bridges | Logs |

Most applications configure one provider per signal during process startup.
Avoid creating providers in random modules. Instrumentation should ask the
global provider for a tracer or meter, not create its own provider.

### Tracers, Meters, And Loggers

A tracer creates spans. A meter creates metric instruments. A logger or logging
bridge creates log records.

When you call:

```python
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
```

the name becomes part of the instrumentation scope. It does not usually
represent the service. The service is represented by the resource.

### Instrumentation Scope

Instrumentation scope identifies the code unit that emitted telemetry. It is
usually a module, package, class, library, or instrumentation library name plus
version.

This distinction matters:

| Concept | Describes | Example |
| --- | --- | --- |
| Resource | The entity producing telemetry. | `service.name=chat-api`, `service.version=abc123`. |
| Instrumentation scope | The code that emitted telemetry. | `opentelemetry.instrumentation.fastapi`, `myapp.rag`. |
| Span attributes | Facts about one operation. | `http.route=/chat`, `rag.top_k=5`. |
| Metric attributes | Dimensions for one measurement. | `gen_ai.request.model=gpt-4o-mini`, `app.outcome=success`. |
| Baggage | Small propagated request context. | `app.tenant.tier=enterprise`. |

Do not overload one layer to do another layer's job. For example, do not put
`user.id` in a resource, because a resource describes the service instance, not
the request.

### Auto-Instrumentation And Manual Instrumentation

Auto-instrumentation creates telemetry for common framework and library work:

- inbound HTTP server spans;
- outbound HTTP client spans;
- database client spans;
- queue producer and consumer spans;
- common runtime or framework metrics;
- trace context injection and extraction;
- trace-log correlation.

Manual instrumentation fills the business gaps:

- `rag.retrieve`;
- `prompt.build`;
- `agent.plan`;
- `agent.execute_tool`;
- `guardrail.evaluate`;
- domain-specific counters and histograms.

Use both. Auto-instrumentation gives coverage. Manual instrumentation gives
meaning.

## Resources

A resource describes the entity that produced telemetry. Every production
service should set `service.name` explicitly.

```python
Resource.create(
    {
        "service.name": "chat-api",
        "service.version": "git-sha-or-semver",
        "service.instance.id": "pod-name-or-host-id",
        "deployment.environment.name": "production",
        "cloud.region": "us-east-1",
        "k8s.namespace.name": "prod",
    }
)
```

Good resource attributes:

- service name;
- service version;
- deployment environment;
- service instance ID;
- host, container, pod, namespace, cluster, cloud region;
- SDK language and version, often added by the SDK.

Bad resource attributes:

- user ID;
- session ID;
- prompt text;
- request URL;
- tenant ID if there are many tenants;
- anything that changes per request.

Resources are attached to telemetry by the provider. Once a provider is created
with a resource, telemetry from tracers and meters from that provider carries
that resource.

## Context Propagation

Context propagation is what turns separate spans from separate processes into
one trace.

Inside one process, OpenTelemetry stores the active span in an execution
context. When code creates a new span, it becomes a child of the active span
unless you specify otherwise.

Across processes, that context must be serialized into a carrier such as HTTP
headers, queue message metadata, or gRPC metadata.

For HTTP, the standard trace context headers are:

```text
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate: vendor-specific-key-values
baggage: app.tenant.tier=enterprise,app.experiment.variant=reranker_b
```

The basic operation is:

```text
caller
  current span exists
  inject context into outbound headers
  send request

callee
  extract context from inbound headers
  start server span with remote parent
```

In Python, manual propagation looks like this:

```python
from opentelemetry.propagate import extract, inject

headers: dict[str, str] = {}
inject(headers)

# Send headers to downstream service.

ctx = extract(incoming_headers)
with tracer.start_as_current_span("worker.process", context=ctx):
    process_message()
```

Most HTTP framework/client instrumentations do this automatically. Manual
`inject()` and `extract()` are still useful for custom transports, queues,
tests, or code where you need explicit control.

Broken propagation symptoms:

- every service starts a separate trace;
- traces show client spans with no matching downstream server span;
- logs contain span IDs but no shared trace ID across services;
- asynchronous jobs cannot be connected to their producer.

Common causes:

- missing HTTP client/server instrumentation;
- custom transport does not copy headers or message metadata;
- reverse proxy strips tracing headers;
- async context is lost by thread pools or background tasks;
- services use incompatible propagator configuration;
- incoming context is extracted after the server span has already been created.

## Semantic Conventions

Semantic conventions are standard names for common telemetry. They are why
backends can understand traces and metrics from many languages and libraries.

Examples:

| Area | Attributes |
| --- | --- |
| HTTP | `http.request.method`, `http.route`, `http.response.status_code` |
| Service/resource | `service.name`, `service.version`, `deployment.environment.name` |
| Database | `db.system.name`, `db.namespace`, `db.operation.name` |
| Messaging | `messaging.system`, `messaging.destination.name`, `messaging.operation.name` |
| GenAI | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` |

Use semantic convention names when they exist. Use your own namespace for
application-specific facts:

```text
app.tenant.tier
app.outcome
rag.retrieval.strategy
agent.tool.name
guardrail.policy.name
```

Do not invent names that conflict with official conventions. Do not use
backend-specific names in general application instrumentation unless the data is
intentionally backend-specific.

## Exporters

Exporters send telemetry out of the SDK or Collector.

Common exporter destinations:

- console/stdout for local debugging;
- OTLP endpoint, usually a Collector;
- a backend-specific endpoint;
- Prometheus pull endpoint for metrics in some setups.

OTLP is the native OpenTelemetry protocol. Common defaults:

| Transport | Port | Typical endpoint |
| --- | --- | --- |
| OTLP/gRPC | `4317` | `http://otel-collector:4317` |
| OTLP/HTTP | `4318` | `http://otel-collector:4318` |
| OTLP/HTTP traces | `4318` | `/v1/traces` |
| OTLP/HTTP metrics | `4318` | `/v1/metrics` |
| OTLP/HTTP logs | `4318` | `/v1/logs` |

In application code, prefer exporting to a Collector in production:

```text
service -> OTLP exporter -> Collector -> backends
```

Direct export to a backend is fine for local development or simple prototypes,
but production systems usually benefit from a Collector because it centralizes
routing, retries, redaction, sampling, batching, credentials, and backend
configuration.

## Processors, Readers, And Samplers

Telemetry usually does not go straight from API call to network request. The SDK
has signal-specific components in between.

### Span Processors

Span processors receive spans from the SDK before export.

| Processor | Use |
| --- | --- |
| `BatchSpanProcessor` | Production default. Queues spans and exports them in batches. |
| `SimpleSpanProcessor` | Exports synchronously when a span ends. Useful for tests and debugging. |
| Custom span processor | Add attributes, copy baggage, filter, or integrate custom behavior. |

Use `BatchSpanProcessor` in production request paths. `SimpleSpanProcessor`
adds export latency directly to the operation ending the span.

### Metric Readers

Metric readers control how metric data is collected and exported. A common
production setup uses a periodic exporting reader:

```text
measurements recorded continuously
  -> SDK aggregates by instrument and attributes
  -> reader collects every N seconds
  -> exporter sends metric batch
```

Metrics are aggregation-first. You record measurements; the SDK turns them into
streams suitable for your exporter and backend.

### Log Processors

Log processors play the same general role for OTel log records: they receive,
process, batch, and export logs. Language support varies, so check the language
status before relying on full log export in a production design.

### Samplers

Samplers decide whether traces should be recorded and exported.

Head sampling decides early, usually when the root span starts:

```text
root span starts
  -> sampler decides sampled or not sampled
  -> a parent-based sampler makes children follow the parent decision
```

Head sampling is cheap and easy. Its limitation is that it cannot know whether
the trace will later become slow or error.

Parent-following is not universal. `ParentBased` delegates root decisions to a
configured root sampler and respects the remote/local parent's sampled flag for
children. A non-parent-based sampler can make a new decision for each span,
which can produce partial traces and usually needs a deliberate reason.

Tail sampling decides later, usually in the Collector, after seeing most or all
spans in a trace:

```text
spans arrive at Collector
  -> Collector buffers trace for a decision window
  -> policy keeps errors, slow traces, important attributes, or a percentage
```

Tail sampling is more powerful but stateful. It requires all spans for a trace
to reach the same sampling decision point.

Sampling affects traces. It should not be your only source of request counts,
error rates, or SLO metrics. Emit metrics separately.

## The Collector

The OpenTelemetry Collector is a telemetry proxy. It can receive telemetry in
several formats, process it, and export it to one or more backends.

Collector configuration has pipeline components:

| Component | Direction | Example use |
| --- | --- | --- |
| Receiver | Into the Collector. | Receive OTLP over gRPC/HTTP, scrape Prometheus, read files. |
| Processor | In the middle. | Batch, memory limit, redact, transform, add resource attributes, sample. |
| Exporter | Out of the Collector. | Send OTLP to Langfuse/APM, remote write metrics, send logs. |
| Connector | Between pipelines. | Derive metrics from spans or route data between pipelines. |
| Extension | Collector capability, not direct telemetry flow. | Health check, auth, diagnostics. |

The `service.pipelines` section enables the actual pipelines. A component can
be configured but unused if it is not listed in a pipeline.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 5s
    limit_mib: 512
  batch:

exporters:
  otlphttp/traces:
    endpoint: https://apm.example.com/otlp
  prometheus_remote_write:
    endpoint: https://prometheus.example.com/api/v1/write

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/traces]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus_remote_write]
```

Processor order matters. For example, put memory limiting early, redaction
before exporting outside your trust boundary, and batching near the end.

Common production reasons to use a Collector:

- keep backend credentials out of application services;
- change exporters without redeploying every service;
- fan out traces to multiple backends;
- send traces, metrics, and logs to different destinations;
- centralize redaction and attribute cleanup;
- add resource attributes consistently;
- tail sample traces;
- protect backends with batching, retry, and queueing;
- observe the telemetry pipeline itself.

## Backends

Backends are where telemetry becomes useful to humans and automation:

| Backend type | Stores | Typical use |
| --- | --- | --- |
| Trace backend | Spans and traces | Investigate individual requests and service dependencies. |
| Metrics backend | Time series | Dashboards, alerts, SLOs, autoscaling, capacity. |
| Log backend | Log records | Search local messages, stack traces, audit-style events. |
| LLM observability backend | LLM spans, prompts, outputs, scores, sessions | Prompt/version analysis, quality evaluation, cost, datasets, experiments. |

OTel does not define your dashboards or alerting rules. It gives the data a
portable shape so those backends can consume it.

For LLM applications:

```text
OpenTelemetry
  -> distributed systems view
  -> HTTP, DB, queues, services, metrics, logs, propagation

Langfuse
  -> LLM product and quality view
  -> prompts, generations, sessions, scores, traces, costs, evals
```

They work well together. A common pattern is to send rich LLM traces to
Langfuse and send operational traces, metrics, and logs to general observability
backends.

## Signal Correlation

The signals answer different questions:

| Signal | Best question |
| --- | --- |
| Metrics | "Is something wrong in aggregate?" |
| Traces | "What happened to this request?" |
| Logs | "What did the code report at this moment?" |
| Baggage | "What request context should travel downstream?" |

A good production workflow usually starts with metrics:

```text
alert: LLM p95 latency is high
  -> metric labels show provider=openai, model=gpt-4o-mini, route=/chat
  -> trace query filters same provider/model/route
  -> one slow trace shows retry and retrieval delay
  -> logs inside that trace show provider timeout and fallback
  -> Langfuse shows prompt version and token growth for affected sessions
```

This is why correlation matters. Without shared resource attributes, trace IDs,
semantic names, and low-cardinality dimensions, every backend becomes a separate
island.

## Environment Variables Worth Knowing

Many SDKs and instrumentations support standard environment variables. Exact
support varies by language, but these are common:

| Variable | Purpose |
| --- | --- |
| `OTEL_SERVICE_NAME` | Sets `service.name`. |
| `OTEL_RESOURCE_ATTRIBUTES` | Adds resource attributes, for example `deployment.environment.name=prod`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP endpoint, for example `http://otel-collector:4318`. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Signal-specific traces endpoint. |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Signal-specific metrics endpoint. |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | Signal-specific logs endpoint. |
| `OTEL_TRACES_EXPORTER` | Selects trace exporter in supported auto-config setups. |
| `OTEL_METRICS_EXPORTER` | Selects metrics exporter in supported auto-config setups. |
| `OTEL_LOGS_EXPORTER` | Selects logs exporter in supported auto-config setups. |
| `OTEL_PROPAGATORS` | Propagators to use, commonly `tracecontext,baggage`. |
| `OTEL_TRACES_SAMPLER` | Trace sampler selection. |
| `OTEL_TRACES_SAMPLER_ARG` | Sampler argument, such as probability. |

Prefer environment configuration for deployment-specific values. Prefer code
for application-specific manual spans and metric instruments.

## Common Design Rules

### Where To Put Data

| Data | Put it in |
| --- | --- |
| Service name/version/environment | Resource attributes. |
| Operation name and duration | Span. |
| Request route/status/method | Span attributes and metric attributes. |
| User/session/tenant context needed downstream | Baggage, if safe and small. |
| Business category for filtering | Span or metric attribute, if low-cardinality. |
| Exact user input or prompt | Usually not general OTel attributes; use explicit, protected LLM observability storage if allowed. |
| Error stack trace | Log record and span exception event. |
| Alertable totals and rates | Metrics. |
| Example request investigation | Traces. |

### Cardinality

Cardinality means "how many distinct values exist." High-cardinality attributes
can overwhelm metric backends and make traces hard to query.

Low-cardinality:

```text
route: /chat
method: POST
status_code: 500
model: gpt-4o-mini
tenant_tier: enterprise
outcome: error
```

High-cardinality:

```text
url: /chat/session/5ee3a8...?token=...
email: person@example.com
prompt: long user prompt
trace_id: unique every request
exception_message: unique message with IDs
```

For traces, high-cardinality attributes are sometimes acceptable if the backend
can handle them and the data is safe. For metrics, be much stricter.

### Span Granularity

Create spans around operations that help explain latency, errors, or behavior.

Good manual spans:

- `rag.retrieve`;
- `vector_db.query`;
- `prompt.build`;
- `llm.chat`;
- `agent.plan`;
- `agent.execute_tool`;
- `guardrail.evaluate`;
- `cache.lookup`;
- `payment.authorize`.

Avoid spans for every tiny function if they do not help investigation. Too many
spans make traces noisy and increase cost.

### Privacy

Telemetry leaves the request path and often leaves the service boundary. Treat
it as production data.

Avoid sending these to general observability backends:

- raw prompts and completions;
- retrieved document text;
- API keys and authorization headers;
- cookies;
- emails and names;
- full request/response bodies;
- full URLs with query strings;
- payment or health data;
- access tokens;
- unreviewed baggage from external callers.

If you need rich LLM payload observability, make an explicit decision about
where that data goes, how it is masked, who can access it, and how long it is
retained.

## Common Misunderstandings

### "The exporter is the backend"

The exporter is only the sender. It sends telemetry to a destination. The
destination might be a Collector or a backend.

```text
SDK exporter -> Collector receiver -> Collector exporter -> backend
```

There can be two exporters in the path: one in the application SDK and one in
the Collector.

### "The Collector creates my spans"

Usually no. Your application instrumentation creates spans. The Collector
receives already-created telemetry, then processes and routes it. Some Collector
receivers can create telemetry from files, host metrics, or scraped endpoints,
but distributed application traces normally start in the service.

### "Baggage automatically appears in traces"

No. Baggage is separate context. It must be explicitly copied into span
attributes, metric attributes, or logs if you want to see it in a backend.

### "Metrics can be derived from traces"

Sometimes, but do not rely on this for alerting or aggregate behavior. Traces
may be sampled. Emit metrics independently, and remember that the telemetry
delivery path still does not make them a transactional accounting record.

### "OpenTelemetry gives me observability by itself"

OTel gives you instrumentation and pipeline standards. You still need useful
spans, low-cardinality metrics, structured logs, dashboards, alerts, runbooks,
and backends.

### "Auto-instrumentation is enough"

Auto-instrumentation is a strong baseline. It usually cannot know your business
operations, product concepts, LLM prompt flow, retrieval strategy, quality
signals, or domain-specific outcomes. Add manual instrumentation where the
business meaning lives.

## A Minimal Production Shape

For a production service, aim for this baseline:

```text
service process
  configure SDK at startup
  set resource attributes
  enable framework/client instrumentation
  add manual business spans
  emit custom metrics
  correlate logs with traces
  export OTLP to Collector

Collector
  receive OTLP
  memory limit
  redact sensitive attributes
  add shared resource attributes
  batch
  sample traces if needed
  export traces, metrics, logs to their backends

Backends
  metrics dashboards and alerts
  trace investigation
  log search
  LLM-specific analysis in Langfuse when needed
```

## Troubleshooting Checklist

No telemetry appears:

- Is the SDK configured before app/framework clients are initialized?
- Is `service.name` set?
- Is the exporter endpoint correct?
- Is the Collector reachable from the service?
- Is the Collector receiver enabled in `service.pipelines`?
- Are spans being sampled out?
- Does the process flush on shutdown for scripts, jobs, workers, or serverless?

Traces are broken across services:

- Are both inbound and outbound instrumentations enabled?
- Are `traceparent` headers or queue metadata preserved?
- Do all services use compatible propagators, usually `tracecontext,baggage`?
- Is a proxy, API gateway, or message broker dropping metadata?
- Is context lost across threads, async tasks, or background workers?

Metrics are expensive or missing:

- Are metric attributes too high-cardinality?
- Is the metric reader configured?
- Is the metrics exporter endpoint correct?
- Does the Collector have a metrics pipeline?
- Did the backend rename metric names or labels?

Logs do not link to traces:

- Is logging instrumentation or trace-log correlation enabled?
- Are logs emitted while a span is active?
- Does the log formatter include trace and span IDs?
- Does the log backend parse those fields?

LLM traces are not useful:

- Are model calls represented as spans?
- Are provider, operation, model, route, outcome, and token usage recorded?
- Are prompts and outputs intentionally captured, masked, and routed?
- Are retrieval, reranking, tool execution, and guardrails separate spans?
- Are metrics emitted separately for latency, errors, tokens, and quality events?

## How To Read The Rest Of These Notes

This page is the conceptual map. The rest of the OpenTelemetry notes deepen
specific parts of the map:

- [02_python_instrumentation.md](02_python_instrumentation.md) shows how to wire
  Python providers, exporters, auto-instrumentation, manual spans, metrics, and
  log correlation.
- [03_production_architecture.md](03_production_architecture.md) focuses on
  Collector topologies, production pipelines, sampling, security, and
  operations.
- [04_multi_service_examples.md](04_multi_service_examples.md) shows context
  propagation, baggage, logs, metrics, and traces across services.
- [05_custom_metrics_alerting.md](05_custom_metrics_alerting.md) explains
  metric design, Prometheus-style alerts, and runbooks.
- [06_genai_and_llm_observability.md](06_genai_and_llm_observability.md) covers
  GenAI semantic conventions and how they apply to LLM and agent systems.
