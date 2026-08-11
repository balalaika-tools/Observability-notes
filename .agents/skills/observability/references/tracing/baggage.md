# Baggage

Baggage is a request-scoped key/value store that travels with trace context across service boundaries. It solves one problem: a fact decided in service A is needed by service B, and threading it through every function signature and every API contract in between is impractical.

It is not needed to connect spans — trace context already does that.

---

## Three separate mechanisms

Confusing these produces the most common baggage bug: "I set baggage but I can't filter on it."

| Mechanism | Does | Handled by |
| --- | --- | --- |
| Local parent context | Makes a new span a child of the active one | OTel Context API |
| Remote propagation | Carries trace context **and baggage** across a boundary | Instrumented client/server + propagators |
| Baggage enrichment | Copies baggage values into **span attributes** | Your code — nothing does this automatically |

Propagation makes baggage *available* downstream. It does not make it *searchable*. A span attribute makes it searchable. Both steps are required.

---

## When baggage is the right tool

All three must hold:

1. the value is decided at this service, from trusted input;
2. a *different* service needs it;
3. it is small, bounded, and non-sensitive.

Good values: tenant tier, experiment variant, an authorized session ID, a request class, a region.

Never: access tokens, API keys, email addresses, raw prompts, retrieved documents, request bodies, or any unbounded user input. Baggage rides in headers on **every** outbound call, including to third parties.

---

## The allowlist is the schema and the security boundary

Define one mapping in the shared telemetry package. It is both a contract and a filter — baggage has no integrity guarantee, and instrumentation forwards it to services you did not think about.

```python
# observability/baggage.py
from __future__ import annotations

from collections.abc import Mapping

from opentelemetry import baggage, context
from opentelemetry.sdk.trace import SpanProcessor

BAGGAGE_TO_SPAN_ATTRIBUTE: dict[str, str] = {
    "session.id": "session.id",
    "app.trace.dimension.tenant_tier": "app.trace.dimension.tenant_tier",
    "app.trace.dimension.feature": "app.trace.dimension.feature",
    "app.trace.dimension.experiment": "app.trace.dimension.experiment",
}


class AllowlistedBaggageSpanProcessor(SpanProcessor):
    """Copy allowlisted baggage values onto every span at start."""

    def __init__(
        self, attribute_map: Mapping[str, str] = BAGGAGE_TO_SPAN_ATTRIBUTE
    ) -> None:
        self._attribute_map = dict(attribute_map)

    def on_start(self, span, parent_context=None) -> None:
        ctx = parent_context if parent_context is not None else context.get_current()
        for baggage_key, attribute_name in self._attribute_map.items():
            value = baggage.get_baggage(baggage_key, context=ctx)
            if value is not None:
                span.set_attribute(attribute_name, value)

    def on_end(self, span) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
```

Do not copy every key. An open loop over `baggage.get_all()` turns any upstream service — or any caller who sets a `baggage` header — into an author of your span schema.

---

## Register it once per process

In the telemetry bootstrap, on the SDK provider, before the service accepts requests:

```python
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(AllowlistedBaggageSpanProcessor())
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(...)))
trace.set_tracer_provider(tracer_provider)
```

Enrichment happens synchronously in `on_start()`, so registration order relative to `BatchSpanProcessor` does not change the result — but registering it first makes the startup sequence easier to audit.

With a pre-fork server, register in each worker after the fork. With `opentelemetry-instrument`, add it to the **existing** provider (see `../setup/sdk_bootstrap.md`); do not build a second one.

---

## Creating trusted baggage at the first service

The gateway's `SERVER` span already started before your handler runs, so the processor cannot enrich it retroactively. That span needs the attributes written directly. Hide the dual-write behind one helper so handlers do not carry telemetry mechanics:

```python
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import baggage, context, trace

from observability.baggage import BAGGAGE_TO_SPAN_ATTRIBUTE


@contextmanager
def use_trusted_baggage(values: Mapping[str, str]) -> Iterator[None]:
    unknown = set(values) - set(BAGGAGE_TO_SPAN_ATTRIBUTE)
    if unknown:
        raise ValueError(f"non-allowlisted baggage keys: {sorted(unknown)}")

    # Keep the active span and trace context; discard anything the caller sent.
    ctx = baggage.clear(context.get_current())
    server_span = trace.get_current_span()

    for key, value in values.items():
        ctx = baggage.set_baggage(key, value, context=ctx)
        if server_span.is_recording():
            server_span.set_attribute(BAGGAGE_TO_SPAN_ATTRIBUTE[key], value)

    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)
```

Used from a handler, after authentication:

```python
@app.post("/chat")
async def chat(request: Request) -> dict:
    identity = authenticate(request)

    with use_trusted_baggage(
        {
            "session.id": load_authorized_session(identity, ...),
            "app.trace.dimension.tenant_tier": validate_enum(
                identity.tenant_tier, {"free", "pro", "enterprise"}
            ),
            "app.trace.dimension.feature": "support_chat",
        }
    ):
        return await handle_chat(request)
```

Two writes, two destinations, both required:

| Call | Affects |
| --- | --- |
| `server_span.set_attribute(...)` | only the `SERVER` span that already exists |
| `set_baggage(...)` + `context.attach(...)` | every span created afterwards, and every downstream service |

---

## Downstream services

Nothing to do in handler code. Server instrumentation extracts baggage before it starts the `SERVER` span, so the registered processor enriches that span and all later ones. Do not call `extract()` and do not create another server span.

---

## Untrusted ingress

At an internet-facing boundary, **drop the inbound `baggage` header** — at the proxy, or by clearing it before instrumentation extracts it. Otherwise a caller can spoof any allowlisted key, including one you use as a metric dimension or a sampling input.

The helper above calls `baggage.clear()` for exactly this reason, but clearing after extraction is weaker than never accepting it: anything that ran before the handler already saw the caller's values.

Also confirm that calls to third parties do not carry internal baggage.

---

## Verifying

Send one request through two services and inspect the exported spans:

```
gateway  SERVER   app.trace.dimension.tenant_tier=enterprise   (set directly)
gateway  CLIENT   app.trace.dimension.tenant_tier=enterprise   (from processor)
agent    SERVER   app.trace.dimension.tenant_tier=enterprise   (from processor)
agent    INTERNAL app.trace.dimension.tenant_tier=enterprise   (from processor)
```

All four share one `trace_id`. If the attribute appears on the gateway's spans but not the agent's, the agent has no processor registered. If it appears nowhere downstream, `OTEL_PROPAGATORS` is missing `baggage`, or the baggage was set after the outbound headers were injected.

Then check the negative: no baggage value appears as a **metric** attribute. Baggage-derived dimensions on metrics is how a cardinality incident starts.
