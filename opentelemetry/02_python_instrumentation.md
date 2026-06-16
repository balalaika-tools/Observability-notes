# Python Instrumentation

This page shows a production-shaped Python setup: resource attributes, tracing, metrics, logs correlation, auto-instrumentation, custom spans, graceful shutdown, and OTLP export.

## Install Packages

Install only what you use. A typical FastAPI service that emits traces and metrics over OTLP/HTTP needs:

```bash
pip install opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-httpx \
  opentelemetry-instrumentation-logging
```

Add database, queue, or framework instrumentations as needed. For example:

```bash
pip install opentelemetry-instrumentation-sqlalchemy \
  opentelemetry-instrumentation-redis \
  opentelemetry-instrumentation-celery
```

## Configure Traces And Metrics

Create one startup module and import it before the rest of your application initializes framework clients.

```python
# observability.py
from __future__ import annotations

import atexit
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_otel() -> None:
    resource = Resource.create(
        {
            "service.name": os.environ["OTEL_SERVICE_NAME"],
            "service.version": os.getenv("SERVICE_VERSION", "unknown"),
            "service.instance.id": os.getenv("HOSTNAME", "local"),
            "deployment.environment.name": os.getenv("ENVIRONMENT", "development"),
        }
    )

    otlp_base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{otlp_base}/v1/traces"),
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
            export_timeout_millis=30000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{otlp_base}/v1/metrics"),
        export_interval_millis=15000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    atexit.register(tracer_provider.shutdown)
    atexit.register(meter_provider.shutdown)
```

Set environment:

```bash
export OTEL_SERVICE_NAME=chat-api
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
export SERVICE_VERSION=$(git rev-parse --short HEAD)
export ENVIRONMENT=production
```

## Add Auto-Instrumentation

Use instrumentation libraries for framework and library telemetry. They are tested, handle propagation, and reduce manual boilerplate.

```python
# app.py
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from observability import configure_otel

configure_otel()

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

# Adds trace/span fields to Python log records. Python OTel log export is still
# evolving, but trace-log correlation is already useful.
LoggingInstrumentor().instrument(set_logging_format=True)
```

## Add Manual Spans

Add manual spans around business operations that framework instrumentation cannot understand:

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer(__name__)


def retrieve_documents(query: str, top_k: int) -> list[dict]:
    with tracer.start_as_current_span("rag.retrieve") as span:
        span.set_attribute("rag.retrieval.strategy", "hybrid")
        span.set_attribute("rag.top_k", top_k)

        try:
            docs = vector_store.search(query=query, top_k=top_k)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise

        span.set_attribute("rag.retrieved_documents", len(docs))
        return docs
```

Span names should be stable and low-cardinality. Use `rag.retrieve`, not `retrieve user 123 question about refunds`.

## Add Custom Metrics

Create instruments once, usually at module load time, and record measurements in request paths.

```python
import time
from contextlib import contextmanager

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
def measure_llm_call(*, provider: str, model: str, route: str):
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
def record_usage(provider: str, model: str, prompt_tokens: int, completion_tokens: int):
    base = {"gen_ai.provider.name": provider, "gen_ai.request.model": model}
    llm_tokens.add(prompt_tokens, {**base, "gen_ai.token.type": "input"})
    llm_tokens.add(completion_tokens, {**base, "gen_ai.token.type": "output"})
```

Metric attributes must be low-cardinality. Good labels: model, provider, route template, environment, outcome. Bad labels: user ID, email, raw URL, prompt text, trace ID, request ID.

## Correlate Logs With Traces

Use normal Python logging, but include trace context:

```python
import logging

logger = logging.getLogger(__name__)


def build_prompt(question: str, docs: list[dict]) -> str:
    logger.info("building prompt", extra={"document_count": len(docs)})
    return render_prompt(question=question, docs=docs)
```

With logging instrumentation, log records created inside an active span include trace and span identifiers. Most log backends can use those fields to deep-link from a log line to a trace.

## Graceful Shutdown

Long-running servers flush continuously, but you still need shutdown hooks. Short-lived scripts, one-shot jobs, and serverless handlers must flush before exit.

```python
def main() -> None:
    configure_otel()
    try:
        run_batch()
    finally:
        trace.get_tracer_provider().shutdown()
        metrics.get_meter_provider().shutdown()
```

In a FastAPI app, use lifespan shutdown:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from opentelemetry import metrics, trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    trace.get_tracer_provider().shutdown()
    metrics.get_meter_provider().shutdown()


app = FastAPI(lifespan=lifespan)
```

## Common Python Pitfalls

- Configuring the SDK after importing framework clients. Import the OTel setup first.
- Creating a new `TracerProvider` in every module. Configure one provider at process startup.
- Using `SimpleSpanProcessor` in production request paths.
- Emitting metrics with high-cardinality attributes.
- Putting raw prompts, request bodies, or secrets in span attributes.
- Expecting baggage to automatically appear as span attributes.
- Using only sampled traces for alerting totals.
- Forgetting to flush in CLI jobs, worker subprocesses, and serverless handlers.

