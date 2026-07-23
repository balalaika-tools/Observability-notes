# Python Instrumentation

This chapter turns the concepts from [01_concepts.md](01_concepts.md) into a
Python service setup. The goal is not just to paste a startup snippet. The goal
is to understand where each piece belongs in a real process:

```text
Python application starts
  -> configure Resource
  -> configure TracerProvider, MeterProvider, optional LoggerProvider
  -> attach processors, metric readers, exporters
  -> install framework/client instrumentations
  -> handle requests and background work
  -> create automatic and manual spans
  -> record metrics
  -> correlate logs
  -> flush on shutdown
```

The examples use FastAPI, HTTPX, and OTLP/HTTP, but the same pattern applies to
Flask, Django, Celery, workers, CLIs, and batch jobs.

## The Python Mental Model

In Python, there are three layers you need to keep separate:

| Layer | Package family | What it does |
| --- | --- | --- |
| API | `opentelemetry-api` | Gives instrumentation code `trace.get_tracer()`, `metrics.get_meter()`, context, baggage, and propagators. |
| SDK | `opentelemetry-sdk` | Implements providers, resources, span processors, metric readers, aggregation, sampling, and exporting. |
| Instrumentation | `opentelemetry-instrumentation-*` | Hooks frameworks and client libraries to create spans, metrics, propagation, and log correlation automatically. |

Library code should usually depend only on the API. Application startup code
owns the SDK configuration.

If no SDK provider is configured, API calls become no-ops. This lets shared
libraries add spans without deciding your backend.

## Code-Based vs Zero-Code Instrumentation

Python supports two common setup styles.

| Style | How it works | Best for |
| --- | --- | --- |
| Code-based | Your app imports a startup module and configures providers and instrumentors directly. | Production services where you want explicit control. |
| Zero-code | The `opentelemetry-instrument` launcher injects SDK and instrumentation setup using environment variables. | Fast trials, platform-managed instrumentation, or apps where code changes are hard. |

Do not mix them casually. If `opentelemetry-instrument` configures the SDK and
your app also calls `trace.set_tracer_provider()`, you can end up with double
instrumentation, no-op providers, duplicate spans, or configuration that silently
loses data. Pick one owner for provider setup.

These notes mostly use code-based setup because it is easier to reason about in
application code. A zero-code section appears later in this file.

## Install Packages

Install only what you use. A typical FastAPI service that emits traces and
metrics over OTLP/HTTP and correlates logs needs:

```bash
pip install opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-httpx \
  opentelemetry-instrumentation-logging
```

Add instrumentation packages for the libraries in your request path:

```bash
pip install opentelemetry-instrumentation-sqlalchemy \
  opentelemetry-instrumentation-redis \
  opentelemetry-instrumentation-celery
```

Use the OTLP HTTP exporter when your Collector listens on `4318`. Use the OTLP
gRPC exporter when it listens on `4317`.

```bash
pip install opentelemetry-exporter-otlp-proto-grpc
```

The package set should match your deployment. Do not install every
instrumentation package "just in case"; each instrumentation hooks imports or
library internals and should be intentional.

## Environment Configuration

Use environment variables for deployment-specific values:

```bash
export OTEL_SERVICE_NAME=chat-api
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
export OTEL_PROPAGATORS=tracecontext,baggage
export OTEL_TRACES_SAMPLER=parentbased_traceidratio
export OTEL_TRACES_SAMPLER_ARG=1.0
export SERVICE_VERSION=$(git rev-parse --short HEAD)
export ENVIRONMENT=production
```

Common variables:

| Variable | Meaning |
| --- | --- |
| `OTEL_SERVICE_NAME` | Sets the logical service name. Required for useful backends. |
| `OTEL_RESOURCE_ATTRIBUTES` | Adds resource attributes such as `deployment.environment.name=production`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP endpoint. For HTTP, usually `http://collector:4318`. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Signal-specific traces endpoint. |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Signal-specific metrics endpoint. |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | Signal-specific logs endpoint. |
| `OTEL_EXPORTER_OTLP_HEADERS` | Static exporter headers, often credentials. |
| `OTEL_EXPORTER_OTLP_<SIGNAL>_HEADERS` | Signal-specific headers that override the common headers for that signal. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Common OTLP transport, usually `http/protobuf` or `grpc`. |
| `OTEL_EXPORTER_OTLP_<SIGNAL>_PROTOCOL` | Signal-specific transport override. |
| `OTEL_PROPAGATORS` | Propagators, commonly `tracecontext,baggage`. |
| `OTEL_TRACES_SAMPLER` | Trace sampler name. |
| `OTEL_TRACES_SAMPLER_ARG` | Trace sampler argument, such as probability. |

Prefer `deployment.environment.name` over older ad hoc names such as `env`.
If your code uses a local `ENVIRONMENT` variable, convert it into the OTel
resource attribute during startup.

### Common Defaults And Signal-Specific Overrides

Traces, metrics, and logs do not have to use the same destination. Every common
OTLP exporter option has a signal-specific form, and the signal-specific value
takes precedence for that signal:

```text
OTEL_EXPORTER_OTLP_<SIGNAL>_<OPTION>
  -> otherwise OTEL_EXPORTER_OTLP_<OPTION>
       -> otherwise the SDK/exporter default
```

`<SIGNAL>` is `TRACES`, `METRICS`, or `LOGS`. Common options include endpoint,
headers, protocol, compression, timeout, certificates, and client keys.
Language support varies, so verify the SDK's environment-variable compliance
before depending on a less common option.

#### One OTLP/HTTP Base Endpoint

When every signal goes to the same OTLP/HTTP receiver, one base endpoint is
enough:

```bash
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

The OTLP/HTTP exporter constructs the signal URLs:

```text
traces  -> http://otel-collector:4318/v1/traces
metrics -> http://otel-collector:4318/v1/metrics
logs    -> http://otel-collector:4318/v1/logs
```

If the base contains a path, the signal path is appended below it:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://telemetry.example.com/ingest
```

```text
traces  -> https://telemetry.example.com/ingest/v1/traces
metrics -> https://telemetry.example.com/ingest/v1/metrics
logs    -> https://telemetry.example.com/ingest/v1/logs
```

#### Split Destinations

Set full signal-specific endpoints when signals need different receivers:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=\
http://otel-collector:4318/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=\
https://metrics.example.com/v1/metrics
```

The result is:

```text
traces  -> OpenTelemetry Collector
metrics -> metrics.example.com
```

A common default plus one override is also valid:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=\
https://metrics.example.com/v1/metrics
```

```text
traces  -> http://otel-collector:4318/v1/traces
logs    -> http://otel-collector:4318/v1/logs
metrics -> https://metrics.example.com/v1/metrics
```

#### The OTLP/HTTP Path Rule

The general and signal-specific endpoint variables have intentionally different
path behavior:

| Configuration | OTLP/HTTP behavior |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318` | Treat it as a base and append `/v1/traces`, `/v1/metrics`, or `/v1/logs`. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://collector:4318/custom` | Send traces to `/custom` exactly; do not append `/v1/traces`. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://collector:4318` | Send traces to the root path `/`, which is usually not an OTLP/HTTP traces endpoint. |

This rule is a common cause of `404` responses. For a standard Collector
receiver, this signal-specific setting is normally correct:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=\
http://otel-collector:4318/v1/traces
```

Do not append HTTP signal paths to OTLP/gRPC endpoints. A gRPC exporter connects
to a target such as `http://otel-collector:4317` and calls the signal's protobuf
service method:

```bash
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

#### Per-Signal Protocols And Credentials

Signals can also use different protocols and authentication:

```bash
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=\
http://trace-collector:4317
export OTEL_EXPORTER_OTLP_TRACES_HEADERS=\
x-api-key=trace-token

export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=\
https://metrics.example.com/v1/metrics
export OTEL_EXPORTER_OTLP_METRICS_HEADERS=\
x-api-key=metrics-token
```

Inject header values from secret management. Do not commit exporter credentials
to source control or bake them into container images.

The explicit Python imports later in this chapter use
`opentelemetry.exporter.otlp.proto.http`, so that code has already chosen
OTLP/HTTP with protobuf. Protocol environment variables are primarily useful
when a distro or zero-code launcher selects the exporter. To change the
code-based example to gRPC, install and import the gRPC exporter package rather
than expecting `OTEL_EXPORTER_OTLP_PROTOCOL` to replace the imported class.

#### Direct OTLP Metrics To Prometheus

Prometheus can receive OTLP metrics over HTTP when its OTLP receiver is enabled:

```bash
prometheus --web.enable-otlp-receiver
```

The receiver listens on `/api/v1/otlp/v1/metrics`. Because a signal-specific
endpoint is used as-is, supply that complete path:

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=none

export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=\
http://otel-collector:4318/v1/traces

export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=\
http://prometheus:9090/api/v1/otlp/v1/metrics
```

The `OTEL_TRACES_EXPORTER` and `OTEL_METRICS_EXPORTER` selectors matter to
zero-code and auto-configured SDK distributions. They are unnecessary in the
code-based setup below because it constructs `OTLPSpanExporter` and
`OTLPMetricExporter` directly.

The Prometheus OTLP receiver is disabled by default and may be exposed without
authentication. Enable it only on an appropriately protected network or behind
an authenticated proxy. Direct OTLP export is useful for a small deployment,
an experiment, or a backend with a dedicated ingest endpoint. A Collector is
usually the better production boundary:

```text
application
  traces  ─┐
  metrics ─┼─> Collector ─> trace, metric, and log backends
  logs    ─┘
```

Central routing keeps backend credentials, retries, redaction, sampling, and
destination changes out of every application. If you split signals directly
from applications, make ownership of those concerns explicit.

## Startup Order

Startup order is a common source of subtle bugs.

The important boundary is not merely module import. In a code-based setup, you
often import both the target library and its instrumentor before installing the
hook. The risky operations are creating long-lived clients, building a
framework's middleware stack, making startup calls, or starting background work
before providers and instrumentation are ready.

A reliable order is:

```text
process starts
  -> configure OTel providers
  -> install global client/logging instrumentation
  -> create the application
  -> instrument the application instance
  -> create long-lived clients and start background work
  -> serve traffic
```

### Bad: Application Work Happens Before Instrumentation

This module performs an HTTP warm-up and creates a long-lived client before OTel
is configured:

```python
# bad_app.py
import httpx
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from observability import configure_otel


# This request has already finished before an OTel provider or HTTPX
# instrumentation exists, so it cannot produce the expected client span.
catalog_response = httpx.get(
    "https://model-registry.internal/models"
)
model_catalog = catalog_response.json()

# Whether an instrumentor can retrofit an already-created client is
# library- and version-specific. Do not make correctness depend on it.
downstream_client = httpx.AsyncClient(
    base_url="https://retrieval-service.internal"
)
app = FastAPI()

# Too late for the warm-up and potentially too late for objects or tasks
# whose hooks were fixed when they were created.
configure_otel()
HTTPXClientInstrumentor().instrument()
FastAPIInstrumentor.instrument_app(app)
```

Problems with this shape:

- the warm-up request is definitely absent from traces;
- its propagation headers were never injected;
- existing clients may or may not be retrofitted, depending on the
  instrumentation implementation;
- any background task started above `configure_otel()` would capture the same
  uninstrumented state;
- module import now performs network I/O, which also makes tests and reloaders
  harder to control;
- the client and providers have no coordinated shutdown path.

Another common mistake is calling `FastAPIInstrumentor.instrument_app(app)`
inside the FastAPI lifespan. Lifespan runs after the server has started the
application; that is too late to safely add instrumentation middleware and may
raise a middleware-stack error.

### Good: Instrument First, Create Runtime Clients In Lifespan

Configure providers at module startup, install global hooks, create and
instrument the FastAPI instance, and let the lifespan own long-lived runtime
clients:

```python
# app.py
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from observability import configure_otel, shutdown_otel


# 1. Providers exist before any instrumented work starts.
configure_otel()

# 2. Install process-wide library hooks before creating clients.
HTTPXClientInstrumentor().instrument()
LoggingInstrumentor().instrument(set_logging_format=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 3. This client is created after HTTPX instrumentation is active.
    app.state.retrieval_client = httpx.AsyncClient(
        base_url="https://retrieval-service.internal",
        timeout=5.0,
    )
    try:
        # Start background tasks here as well, after instrumentation.
        yield
    finally:
        # 4. Stop work and close clients while telemetry is still active.
        await app.state.retrieval_client.aclose()

        # 5. Flush and shut down providers last.
        shutdown_otel()


# The app must exist before instance-level FastAPI instrumentation can
# attach middleware, but instrumentation still happens before serving.
app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/documents/{document_id}")
async def get_document(document_id: str, request: Request) -> dict:
    response = await request.app.state.retrieval_client.get(
        f"/documents/{document_id}"
    )
    response.raise_for_status()
    return response.json()
```

This produces the intended ordering:

```text
module import
  configure providers
  install HTTPX and logging hooks
  create FastAPI app
  attach FastAPI instrumentation

application startup
  create instrumented AsyncClient
  start background tasks

application shutdown
  stop background tasks
  close AsyncClient
  flush and shut down OTel providers
```

Closing application resources before OTel matters. A client's `close()` call
or a worker's final operation may finish spans; shutting down the providers
first can discard that final telemetry.

### Acceptable: A Module-Level Client Created In The Right Order

Lifespan ownership is usually clearer, but a module-level client can still be
correct when the library supports it and shutdown is explicit. Using the same
imports as the previous example:

```python
configure_otel()
HTTPXClientInstrumentor().instrument()

downstream_client = httpx.AsyncClient(
    base_url="https://retrieval-service.internal"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await downstream_client.aclose()
        shutdown_otel()


app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
```

The important fact is that `downstream_client` is constructed after the HTTPX
instrumentor is active, and is closed before OTel shuts down.

### Zero-Code Owns The Early Setup

With `opentelemetry-instrument`, the launcher configures the SDK and installs
instrumentation before importing `app:app`:

```bash
OTEL_SERVICE_NAME=chat-api \
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
opentelemetry-instrument uvicorn app:app
```

In that mode, do not also call `configure_otel()` or process-wide
`instrument()` methods in application code. The application should own its
clients and lifespan cleanup; the launcher owns provider and auto-
instrumentation setup.

## Configure Traces And Metrics

Create one startup module. Keep it idempotent so tests, reloaders, or worker
forks do not configure providers twice.

```python
# observability.py
from __future__ import annotations

import os
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


@dataclass(slots=True)
class OtelHandles:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider


_otel_handles: OtelHandles | None = None


def configure_otel() -> OtelHandles:
    global _otel_handles

    if _otel_handles is not None:
        return _otel_handles

    resource = Resource.create(
        {
            "service.name": os.environ["OTEL_SERVICE_NAME"],
            "service.version": os.getenv("SERVICE_VERSION", "unknown"),
            "service.instance.id": os.getenv("HOSTNAME", "local"),
            "deployment.environment.name": os.getenv("ENVIRONMENT", "development"),
        }
    )

    otlp_base = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
    ).rstrip("/")
    traces_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"{otlp_base}/v1/traces",
    )
    metrics_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        f"{otlp_base}/v1/metrics",
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=traces_endpoint),
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
            export_timeout_millis=30000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_endpoint),
        export_interval_millis=15000,
        export_timeout_millis=30000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    _otel_handles = OtelHandles(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )

    return _otel_handles


def shutdown_otel() -> None:
    global _otel_handles

    if _otel_handles is None:
        return

    handles = _otel_handles
    _otel_handles = None
    try:
        handles.tracer_provider.shutdown()
    finally:
        handles.meter_provider.shutdown()
```

What this does:

- `Resource.create(...)` attaches service identity to all emitted telemetry.
- `TracerProvider` creates tracers and owns trace sampling/export behavior.
- `BatchSpanProcessor` buffers spans and exports them in batches.
- `OTLPSpanExporter` sends spans to the configured trace destination.
- `MeterProvider` creates meters and owns metric aggregation/export behavior.
- `PeriodicExportingMetricReader` collects aggregated metrics on an interval.
- `OTLPMetricExporter` sends metric batches to the configured metrics destination.
- Signal-specific endpoint variables override the normalized global base and
  are passed through unchanged. Only the global base is normalized before the
  code appends `/v1/traces` or `/v1/metrics`.
- `shutdown_otel()` clears its stored handles before shutdown, so a repeated call is a no-op. This guide invokes it explicitly from the FastAPI lifespan or a script `finally` block rather than also registering `atexit`.

## Add Auto-Instrumentation

Use instrumentation libraries for framework and library telemetry. They handle
server spans, client spans, propagation, and common semantic attributes.

```python
# app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from observability import configure_otel, shutdown_otel

configure_otel()

HTTPXClientInstrumentor().instrument()
LoggingInstrumentor().instrument(set_logging_format=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    shutdown_otel()


app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
```

What you get:

| Instrumentation | Creates |
| --- | --- |
| FastAPI | HTTP server spans, route attributes, context extraction. |
| HTTPX | HTTP client spans, context injection. |
| Logging | Trace ID and span ID fields on log records created inside spans. |

If an HTTP client is instrumented, it usually injects `traceparent` and baggage
headers automatically. If the server framework is instrumented, it usually
extracts incoming trace context automatically.

Manual propagation is still useful for queues, custom transports, tests, and
places where you want exact control. See [04_multi_service_examples.md](04_multi_service_examples.md).

## Add Manual Spans

Auto-instrumentation knows framework calls. It does not know your business
steps. Add manual spans around operations that explain latency, errors, or
behavior.

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)


def retrieve_documents(query: str, top_k: int) -> list[dict]:
    with tracer.start_as_current_span("rag.retrieve") as span:
        span.set_attribute("rag.retrieval.strategy", "hybrid")
        span.set_attribute("rag.top_k", top_k)

        try:
            docs = vector_store.search(query=query, top_k=top_k)
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            raise

        span.set_attribute("rag.retrieved_documents", len(docs))
        return docs
```

Span design rules:

| Rule | Example |
| --- | --- |
| Use stable names. | `rag.retrieve`, not `retrieve user 123`. |
| Put bounded facts in attributes. | `rag.top_k=5`, `rag.retrieval.strategy=hybrid`. |
| Record exceptions and set error status. | Let `start_as_current_span()` record an escaping exception and error status by default; add low-cardinality `error.type` once. |
| Avoid sensitive content. | Do not store raw prompts, documents, access tokens, or emails. |
| Use semantic conventions where possible. | `http.route`, `gen_ai.request.model`, `db.system.name`. |

Use `trace.get_current_span()` when you only need to enrich the active span:

```python
from opentelemetry import trace


def attach_request_context(tenant_tier: str) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("app.tenant.tier", tenant_tier)
```

## Add Custom Metrics

Create instruments once, usually at module load time. Record measurements in
request paths with low-cardinality attributes.

```python
import time
from contextlib import contextmanager
from collections.abc import Iterator

from opentelemetry import metrics

meter = metrics.get_meter(__name__)

llm_requests = meter.create_counter(
    "llm.requests",
    unit="{request}",
    description="Number of LLM requests attempted.",
)

llm_tokens = meter.create_counter(
    "llm.tokens",
    unit="{token}",
    description="Tokens used by LLM calls.",
)

llm_duration = meter.create_histogram(
    "llm.request.duration",
    unit="s",
    description="End-to-end LLM request duration.",
)

llm_inflight = meter.create_up_down_counter(
    "llm.inflight",
    unit="{request}",
    description="LLM requests currently in progress.",
)


@contextmanager
def measure_llm_call(*, provider: str, model: str, route: str) -> Iterator[None]:
    attributes = {
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "http.route": route,
    }

    llm_inflight.add(1, attributes)
    start = time.perf_counter()
    outcome = "success"

    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        duration = time.perf_counter() - start
        metric_attrs = {**attributes, "app.outcome": outcome}
        llm_requests.add(1, metric_attrs)
        llm_duration.record(duration, metric_attrs)
        llm_inflight.add(-1, attributes)
```

Record token usage after the provider returns:

```python
def record_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    base = {"gen_ai.provider.name": provider, "gen_ai.request.model": model}
    llm_tokens.add(input_tokens, {**base, "gen_ai.token.type": "input"})
    llm_tokens.add(output_tokens, {**base, "gen_ai.token.type": "output"})
```

Metric attribute rules:

| Good | Bad |
| --- | --- |
| model | prompt text |
| provider | user email |
| route template | raw URL |
| outcome enum | exception message |
| tenant tier | tenant ID if many tenants |

Traces can tolerate some high-cardinality searchable data if the backend and
privacy model allow it. Metrics usually cannot. Metric attributes create time
series, so be strict.

## Observable Gauges

Use an observable gauge when the value is read at collection time rather than
recorded inline.

```python
from opentelemetry import metrics

meter = metrics.get_meter(__name__)


def observe_queue_depth(options):
    depth = queue_client.depth("llm-jobs")
    yield metrics.Observation(depth, {"queue.name": "llm-jobs"})


meter.create_observable_gauge(
    "worker.queue.depth",
    callbacks=[observe_queue_depth],
    unit="{job}",
    description="Jobs waiting in the worker queue.",
)
```

The callback runs when the SDK collects metrics. Keep it fast and reliable. Do
not call slow or fragile dependencies in gauge callbacks unless you can tolerate
collection failures.

## Correlate Logs With Traces

OpenTelemetry log correlation is different from OTel log export.

| Capability | Meaning |
| --- | --- |
| Trace-log correlation | Normal Python logs include `trace_id` and `span_id` while a span is active. |
| OTel log export | Log records flow through an OpenTelemetry log pipeline. |

For Python, trace-log correlation is the most common starting point.

```python
import logging

logger = logging.getLogger(__name__)


def build_prompt(question: str, docs: list[dict]) -> str:
    logger.info("building prompt", extra={"document_count": len(docs)})
    return render_prompt(question=question, docs=docs)
```

With `LoggingInstrumentor().instrument(set_logging_format=True)`, records
created inside an active span can include trace and span identifiers. In
production, many teams use structured JSON logging and add those fields to their
formatter instead of using the default OTel logging format.

Good log fields:

- `trace_id`;
- `span_id`;
- `service.name`;
- `event_name`;
- bounded request context such as route, model, provider, outcome;
- local error details and stack traces.

Bad log fields:

- raw prompts;
- full request bodies;
- access tokens;
- cookies;
- retrieved document text;
- personal data unless explicitly permitted.

## Optional OTel Log Export

The OTel logs signal is stable in the specification, but Python log API/SDK
support is still listed as Development in the language status. Treat Python log
export as an evolving area and validate it carefully before standardizing on it.

If you do use OTel log export, keep it separate in your mental model:

```text
Python logging
  -> logging bridge or handler
  -> LoggerProvider
  -> log processor
  -> log exporter
  -> Collector logs pipeline
  -> log backend
```

For many Python services, this is enough:

```text
structured application logs -> log agent/backend
trace and span IDs in log fields -> correlation with traces
```

## Zero-Code Instrumentation

Zero-code instrumentation launches your app through `opentelemetry-instrument`.
It can install matching instrumentation packages and configure exporters through
environment variables.

Typical setup:

```bash
pip install opentelemetry-distro \
  opentelemetry-exporter-otlp-proto-http

opentelemetry-bootstrap -a install
```

Run:

```bash
OTEL_SERVICE_NAME=chat-api \
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
OTEL_TRACES_EXPORTER=otlp \
OTEL_METRICS_EXPORTER=otlp \
OTEL_LOGS_EXPORTER=none \
opentelemetry-instrument uvicorn app:app --host 0.0.0.0 --port 8000
```

Use zero-code when you want broad automatic coverage quickly. Use code-based
setup when you need precise provider configuration, custom processors, explicit
metric views, or controlled shutdown.

Zero-code still benefits from manual spans and metrics. Manual instrumentation
calls the OTel API and will use the provider that the launcher configured.

## Background Workers And Queues

Workers need the same provider setup as web services:

```text
worker starts
  -> configure OTel
  -> instrument queue/client libraries
  -> extract context from message headers
  -> create consumer/process spans
  -> flush on shutdown
```

For long-running workers, configure once at process startup. For short-lived
jobs, flush in `finally`.

```python
from opentelemetry.propagate import extract


def handle_message(message) -> None:
    ctx = extract(message.headers)
    with tracer.start_as_current_span("worker.process_message", context=ctx):
        process(message.payload)
```

If a queued job is causally connected to one request, use propagated context as
the parent. If it is intentionally decoupled, delayed, batched, or fan-in/fan-
out, use span links instead of forcing a misleading parent-child relationship.

## CLI Jobs And Batch Scripts

Short-lived programs often lose telemetry because the process exits before the
batch processor exports.

```python
from observability import configure_otel, shutdown_otel


def main() -> None:
    configure_otel()
    try:
        run_batch()
    finally:
        shutdown_otel()


if __name__ == "__main__":
    main()
```

For scripts, consider a shorter batch delay or explicit shutdown after the work
finishes. Console exporters are useful while developing a job locally.

## Tests

Avoid exporting telemetry to real backends from unit tests. Use one of these
patterns:

| Test type | Pattern |
| --- | --- |
| Unit test | Do not configure the SDK, or use in-memory exporters. |
| Integration test | Export to a local Collector or in-memory exporter. |
| Trace shape test | Assert span names, attributes, and parent-child relationships. |
| Metrics test | Assert recorded measurements or exported points with bounded attributes. |

If your production setup module registers global providers, make tests
idempotent and isolated. Global OTel state is process-wide, so test suites can
interfere with themselves if they repeatedly configure providers.

## Troubleshooting Python Telemetry

No spans:

- Did `configure_otel()` run before the app served requests?
- Is the correct exporter package installed?
- Is `OTEL_SERVICE_NAME` set?
- Is the Collector reachable from the app container?
- Is the trace pipeline enabled in the Collector?
- Is sampling dropping the trace?

Duplicate spans:

- Are you using both zero-code and code-based setup?
- Did a dev reloader configure instrumentation twice?
- Did tests import the app repeatedly without idempotent setup?
- Did you call `HTTPXClientInstrumentor().instrument()` more than once?

Broken traces across services:

- Is the outgoing client instrumented?
- Is the receiving framework instrumented?
- Are `traceparent` headers preserved by proxies?
- Are both services using `tracecontext` propagator?
- Is context lost when work moves to a thread, task, or queue?

Missing metrics:

- Is `MeterProvider` configured?
- Is there a metric reader?
- Is the metrics exporter endpoint correct?
- Does the Collector have a metrics pipeline?
- Is your backend renaming metric names or labels?

Logs not correlated:

- Is logging instrumentation enabled before logs are emitted?
- Are logs created while a span is active?
- Does your formatter include trace and span fields?
- Does the log backend parse those fields?

## Common Python Pitfalls

- Configuring the SDK after importing or creating framework clients.
- Creating a new `TracerProvider` in every module.
- Calling instrumentors repeatedly in reloaders or tests.
- Using `SimpleSpanProcessor` in production request paths.
- Emitting metrics with high-cardinality attributes.
- Putting raw prompts, request bodies, document contents, emails, or secrets in attributes.
- Expecting baggage to automatically appear as span attributes.
- Using only sampled traces for alerting totals.
- Forgetting to flush in CLI jobs, subprocesses, serverless handlers, and workers.
- Expecting `/v1/traces` or `/v1/metrics` to be appended to a signal-specific
  OTLP/HTTP endpoint.
- Appending OTLP/HTTP signal paths to an OTLP/gRPC target.
- Assuming Python OTel log export has the same maturity as trace-log correlation.

## Minimal Production Checklist

- `service.name`, `service.version`, instance ID, and environment are set.
- Providers are configured once at process startup.
- Framework and client instrumentations are installed before clients are created.
- All services use compatible propagators.
- Manual spans cover business operations, not every small helper function.
- Metrics use bounded attributes and are emitted independently from traces.
- Logs include trace and span IDs.
- Exporters normally send to a Collector. Any direct per-signal backend routes
  have explicit owners for credentials, retries, policy, and destination
  changes.
- Shutdown flushes telemetry in web apps, workers, jobs, and serverless handlers.
- Tests do not leak telemetry to production backends.
