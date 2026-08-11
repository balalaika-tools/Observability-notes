# Worker Process Runtime

Read this file only for a long-running worker loop or when work crosses a
thread, executor, or independently scheduled in-process task. Queue carrier
semantics and durable DB handoffs live in their own references.

## Long-running worker loops

Configure providers once at startup, not per message. Shut down on the termination signal, after the in-flight message finishes:

```python
import signal

stopping = False


def _request_stop(*_):
    global stopping
    stopping = True


def main() -> None:
    configure_observability()
    configure_logging()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        while not stopping:
            for message in receive_messages():
                handle_message(message)
    finally:
        shutdown_observability()
```

## Context loss inside the worker

Context flows through normal async and sync call paths but is lost when work moves to a thread pool, executor, or independently scheduled task. Symptom: spans that should be children suddenly become roots.

```python
from opentelemetry import context


def submit_background_work(executor, payload: dict) -> None:
    current_ctx = context.get_current()
    executor.submit(_run_with_context, current_ctx, payload)


def _run_with_context(parent_ctx, payload: dict) -> None:
    token = context.attach(parent_ctx)
    try:
        with tracer.start_as_current_span("process payload", record_exception=False):
            process(payload)
    finally:
        context.detach(token)
```

Verify that child spans keep the boundary span's trace ID, that shutdown waits
for the in-flight unit of work, and that providers are configured exactly once
per process.
