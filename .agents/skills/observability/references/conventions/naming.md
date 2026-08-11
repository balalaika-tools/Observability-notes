# Naming: Spans, Attributes, Metrics, Log Events

One rule underpins all of these: **the name is the low-cardinality part, the value is the variable part.** A name that varies per request destroys aggregation in every backend.

---

## Priority order

Whenever you need a name, work down this list and stop at the first hit:

```
1. An OpenTelemetry semantic convention exists       -> use it verbatim
2. No convention exists, the fact is organisational  -> app.<domain>.<thing>
3. The user asked for a specific name                -> use theirs, note the deviation
```

Never invent a key inside a standard namespace. `gen_ai.usage.input_token_details` looks official and is not; it belongs under `app.gen_ai.usage.input_token_details`. A backend that ships support for the real attribute later will then not collide with yours.

---

## Span names

Format: **operation, then a stable subject**. No IDs, no user input, no prompt text.

| Good | Bad | Why the bad one hurts |
| --- | --- | --- |
| `GET /orders/{order_id}` | `GET /orders/12345` | One span name per order |
| `process exception` | `process exception 931272` | Unaggregatable |
| `invoke_agent support_agent` | `agent invocation` | Loses which agent when there are several |
| `chat gpt-5` | `chat` | Cannot compare models |
| `execute_tool web_search` | `tool call` | Cannot find the failing tool |
| `publish sqs pricing-jobs` | `publish message` | Loses the queue |
| `consume sqs pricing-jobs` | `worker loop` | Describes the code, not the operation |
| `run workflow transition` | `run transition wf-123` | Puts a workflow-run ID in the name |

GenAI spans follow the semantic convention shape `{gen_ai.operation.name} {subject}`:

```
chat gpt-5
embeddings text-embedding-3-small
retrieval product_docs
execute_tool order_lookup
invoke_agent support_agent
invoke_workflow support_rag
```

Tool names come from the model and are therefore untrusted. If users can define arbitrary tools, map unknown names to a bounded value such as `custom_tool` before they reach a span name or metric attribute, and keep the raw name in an attribute.

---

## Span attributes

Rules:

- lowercase, dot-separated namespaces;
- stable keys — never build a key from a runtime value (`user.abc123.count` is a leak, not an attribute);
- bounded values wherever the value will be filtered on;
- no arbitrary payloads; a serialized blob belongs behind a content-capture flag or not at all;
- domain vocabulary over implementation vocabulary.

Prefer the word the business uses:

```
app.pricing.product_count        not   processed_items
app.exception.rule               not   rule_str
app.retrieval.result_count       not   n
```

### Namespaces you will actually use

| Namespace | Owner | Examples |
| --- | --- | --- |
| Resource attributes | OTel | `service.name`, `service.version`, `deployment.environment.name`, `service.instance.id` |
| HTTP | OTel | `http.request.method`, `http.route`, `http.response.status_code`, `server.address` |
| Messaging | OTel | `messaging.system`, `messaging.destination.name`, `messaging.operation.type` |
| Database | OTel | `db.system.name`, `db.operation.name` |
| Errors | OTel | `error.type` — low-cardinality class or code, never a message |
| GenAI | OTel | `gen_ai.*` — the full set lives in `../tracing/genai/attributes.md` |
| Identity | OTel | `user.id`, `session.id` — subject to privacy policy, and never on metrics |
| Everything else | You | `app.*` |

High-cardinality values (`order.id`, `user.id`, `session.id`) are acceptable on spans when they have real diagnostic value and policy allows. They are never acceptable on metrics.

### The `app.*` shape

```
app.<domain>.<noun>              app.pricing.product_count
app.<domain>.<noun>.<qualifier>  app.retrieval.result_count
app.outcome                      success | error | timeout | blocked
```

Keep the enum values for `app.outcome` fixed across the whole service. A metric that groups on it is only useful if the set is closed.

---

## Metric names and units

Metric names describe the measured thing; the instrument type describes how it is measured. Do not encode the instrument in the name.

| Good | Problematic |
| --- | --- |
| `app.pricing.updates` | `app.pricing.counter` |
| `app.worker.job.duration` (unit `s`) | `app.worker.job.latency_ms` recorded in seconds |
| `app.retrieval.result_count` | `docs` |

Units are UCUM, and the unit is part of the name's contract: `s` for durations (never `ms`), `By` for bytes, and braced annotations such as `{request}` `{token}` `{document}` `{job}` for dimensionless counts. A name whose suffix disagrees with its unit — `latency_ms` recorded in seconds — misleads every reader and every alert.

*Which* instrument to reach for is a metrics-design question, not a naming one: `../metrics/service.md` has the table.

### Metric attributes are a hard boundary

Every unique attribute combination is a time series. Bounded values only:

```
allowed    service.name, deployment.environment.name, http.route, http.request.method,
           http.response.status_code, messaging.destination.name, messaging.operation.type,
           gen_ai.operation.name, gen_ai.provider.name, gen_ai.request.model,
           gen_ai.response.model, gen_ai.tool.name (bounded), gen_ai.agent.name (bounded),
           app.job.type, app.outcome, app.tenant.tier, error.type (normalized)

forbidden  user.id, session.id, gen_ai.conversation.id, request_id, trace_id,
           order_id, document_id, gen_ai.response.id, raw URLs, exception messages,
           prompt or response text, tool arguments
```

This is the list the metrics files defer to — `../metrics/service.md` and `../metrics/genai.md` add only the traps specific to their domain.

Trace IDs reach metrics through exemplars, never through labels.

Backends rename metrics. Prometheus turns `app.pricing.updates` into `app_pricing_updates_total` and `app.worker.job.duration` into `app_worker_job_duration_bucket`/`_count`/`_sum`. Verify the exported names in the backend before writing an alert against them.

---

## Log event names

A log event name identifies *what happened*, in the past tense or as a state change, and stays stable. Varying values go in fields.

| Good | Bad |
| --- | --- |
| `request_received`, `request_completed` | `processing` |
| `job_started`, `job_completed`, `job_failed` | `done` |
| `queue_message_received`, `queue_message_processed` | `got message` |
| `workflow_transition_started`, `workflow_transition_completed` | `state changed to paid for wf-123` |
| `retrieval_completed` | `retrieved 5 docs` |
| `tool_execution_failed` | `something_failed` |
| `agent_invocation_completed` | `finished` |

Full logging setup, field lists, and the OTel named-event mechanics are in `../logging/structlog.md`.

---

## Consistency across signals

The same fact should carry the same name everywhere it is bounded enough to appear:

```
span attribute   gen_ai.request.model = "gpt-5"
metric attribute gen_ai.request.model = "gpt-5"
log field        model = "gpt-5"
```

Where a name must differ (log fields are conventionally flat, metric labels get normalised by the backend), document the mapping once in the observability package rather than letting each call site improvise.

---

## Where names live in code

Do not scatter string literals. Put convention names in one module — see `../tracing/genai/attributes.md` for the GenAI constants module and `../setup/package_layout.md` for where it sits. When a convention changes, one file changes instead of forty call sites.
