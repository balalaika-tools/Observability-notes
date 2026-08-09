# Langfuse Overview and Data Model

Last verified against official Langfuse documentation on 2026-07-24.

## 🧭 Mental Model

Langfuse starts from OpenTelemetry's trace model and turns it into an AI engineering record. OpenTelemetry can tell you that a request spent 820 ms in an outbound HTTP call. Langfuse can tell you that the call was a generation using a specific model and prompt version, consumed a certain number of tokens, produced a specific answer, received negative user feedback, and regressed after a release.

Think of Langfuse as three connected layers:

```text
execution layer     trace -> observations -> generation/retriever/tool/guardrail details
evaluation layer    feedback -> scores -> annotations -> datasets -> experiments
analytics layer     dashboards/API -> cost, latency, volume, quality, release comparisons
```

The data model solves the question "What happened, why, and was it good?" It does not by itself solve authorization, redaction, incident paging, or business-specific evaluation logic. Those come from your application, governance controls, metric/log stack, and evaluator code.

The most important design choice is where information belongs:

- Put workflow identity on the trace.
- Put step-specific evidence on observations.
- Put model-call details on generations.
- Put quality judgments on scores.
- Put reproducible examples in datasets.
- Put operational health in metrics and logs outside Langfuse when it is not LLM-specific.

## 📌 What Langfuse Is

Langfuse is a production platform for AI engineering:

- Observability for LLM calls, RAG pipelines, tools, agents, chains, retrievers, guardrails, and evaluators.
- Prompt management and prompt version tracking.
- Scores from users, humans, code evaluators, and LLM-as-judge systems.
- Datasets and experiments for offline evaluation.
- Metrics and dashboards for quality, cost, latency, and volume.
- Public API and data access for internal analytics and workflows.

Langfuse is built on OpenTelemetry. A Langfuse observation is an OpenTelemetry span with Langfuse-specific attributes and types.

## 🔄 Lifecycle: From Request to Improvement

1. A user message, background job, or agent task starts.
2. The application creates a root trace/observation such as `rag.answer`.
3. Child observations describe the workflow: `query.rewrite`, `rag.retrieve`, `llm.generate_answer`, `guardrail.output_policy`, `response.persist`.
4. Generations record model, parameters, safe input/output, usage, and cost details.
5. Trace attributes such as user, session, release, version, tags, and metadata let you slice the data later.
6. After the response, feedback, code checks, human review, or LLM judges create scores.
7. Useful failures become dataset items.
8. Experiments run candidate prompts, models, retrieval settings, or agent logic against datasets.
9. Langfuse metrics and dashboards show whether production quality, latency, volume, and cost improved after rollout.

The lifecycle matters because each object answers a different debugging question:

| Question | Primary object |
| --- | --- |
| Which product workflow ran? | Trace |
| Which step was slow, wrong, or missing? | Observation |
| What did the model see and return? | Generation |
| Which documents or tools influenced the answer? | Retriever/tool observations |
| Was the answer good? | Score |
| Can we reproduce and compare fixes? | Dataset and experiment |
| Did a release change quality, latency, or cost? | Metrics and trace dimensions |

## 📦 Core Objects

### Trace

A trace is one end-to-end user or system workflow.

Examples:

- A chat message from HTTP request to final streamed answer.
- A RAG answer that retrieves documents, reranks them, calls a model, validates output, and stores feedback.
- An agent task with planning, tool calls, retries, and final synthesis.

In the v4/Fast Preview observation-first model, a trace is primarily the correlation envelope identified by the OpenTelemetry trace ID. The observations carry the work and payload. Shared trace dimensions include:

- `name`
- `userId`
- `sessionId`
- `release`
- `version`
- `tags`
- trace-level `metadata`

The input and output of the root observation are the current source of trace input and output. Older ingestion paths may still expose separately written trace-level input/output fields for compatibility, but new v4 instrumentation should not write two competing copies.

Use a trace name for the product workflow, such as `chat.answer`, `support.agent`, or `code_review.generate`, not for high-cardinality values.

Layered explanation:

- Beginner intuition: one trace is one story from request to result.
- Technical mechanics: Langfuse uses the OpenTelemetry trace ID to correlate observations; the root observation supplies current trace I/O, while propagated attributes supply shared dimensions.
- Production implications: root trace names, release/version, environment, user/session, and tags decide whether dashboards and incident filters work.
- Common mistakes: naming traces with request IDs, setting trace attributes only on a leaf span, and failing to separate release from prompt/workflow version.

### Observation

An observation is Langfuse's representation of a span. It has a name, timing, input, output, metadata, level, status message, and type.

Common observation types:

| Type | Use for |
| --- | --- |
| `span` | Generic work such as request handling, parsing, routing, or policy checks |
| `generation` | LLM text or chat completion calls |
| `embedding` | Embedding model calls |
| `retriever` | Vector search, keyword search, hybrid search, or document lookup |
| `tool` | Tool calls made by an agent or chain |
| `agent` | Agent loop or agent-level task |
| `chain` | Multi-step orchestration |
| `evaluator` | Evaluation logic |
| `guardrail` | Safety, policy, or validation checks |
| `event` | Point-in-time marker |

Pick the most specific type because the Langfuse UI and metrics can use it for filtering and visualization.

Layered explanation:

- Beginner intuition: an observation is one step in the trace.
- Technical mechanics: observations are OpenTelemetry spans with Langfuse-specific type and fields; they can nest under parent observations.
- Production implications: good observation boundaries make slow steps, bad tool calls, empty retrieval, and guardrail failures visible.
- Common mistakes: recording only the final LLM call, using generic names like `process`, and storing large or sensitive payloads in metadata.

### Generation

A generation is a special observation for model calls. It should include:

- model name
- model parameters
- input messages or prompt
- output text or structured output
- token usage via `usage_details`
- optional cost details when you must override inferred pricing
- provider and request metadata when useful

Generations are where most LLM debugging starts: prompt, model, retrieved context, tool state, token usage, latency, and final output all meet there.

In a provider-faithful trace, `generation.input` is the actual prompt or message array sent for that model call. A stateless agent therefore repeats earlier context on every call: the second generation usually contains the same system and conversation messages as the first, plus the assistant tool call and tool result. This is not Langfuse-specific. The OpenTelemetry GenAI conventions define `gen_ai.input.messages` as the chat history provided to one model invocation, in send order, so OTel-native instrumentations produce the same repeated-history shape before Langfuse ingestion. See the [OpenTelemetry GenAI content-capture guidance](../opentelemetry/06_genai_and_llm_observability.md#full-request-versus-filtered-telemetry).

Langfuse does not require that representation. With manual instrumentation, you can make a frequently reviewed generation easier to scan by recording only the step's relevant input, such as the newest user message or an `input_delta` / `new_context` object:

```python
generation.update(
    input={
        "input_delta": [
            {"role": "tool", "content": tool_result},
        ]
    },
    output={
        "role": "assistant",
        "content": final_answer,
    },
)
```

This compact form reduces repeated history in the UI, but it is no longer an exact record of the model request and cannot support faithful replay by itself. Name the field so reviewers know it is a delta; keep the full request in approved metadata only when debugging or replay requires it and the privacy policy allows it. A practical default is reviewer-friendly user input and final output on the root observation, explicit arguments/results on tool observations, and full messages on generations only when exact model-call debugging is worth the duplication.

The `input_delta` wrapper above is a Langfuse manual-observation shape, not the OpenTelemetry schema for `gen_ai.input.messages`. On a raw OTel span, keep a schema-valid ordered message array, use filtering/truncation only as an intentional capture policy, and add a bounded `app.gen_ai.input.capture_mode` marker so the compact value is not mistaken for the full request.

Langfuse can infer usage when it has a tokenizer and can infer cost when usage plus a matching model-price definition are available. Provider/SDK-supplied usage is preferred to inferred usage. User-supplied `usage_details` and `cost_details` take precedence over inferred values; supplied cost is used verbatim, so provide it only for an authoritative provider charge or custom agreement. Usage detail buckets must be mutually exclusive or totals and inferred cost can be double-counted.

> ⚠️ **Watch out:** If usage detail buckets overlap, token totals and inferred cost will be silently double-counted with no error from Langfuse.

Layered explanation:

- Beginner intuition: a generation is the model call receipt.
- Technical mechanics: it is a generation-type observation with model fields, model parameters, input/output, usage details, optional cost details, and optional prompt link.
- Production implications: token and cost analytics, model migration comparisons, streaming latency, and prompt regressions depend on accurate generation fields.
- Common mistakes: not recording usage, hiding the model name behind an alias without metadata, and capturing full prompts before privacy review.

### Event

An event is a point-in-time observation. Use it for something meaningful that happened but does not need duration tracking.

This Langfuse observation type is not the OpenTelemetry Span Event API. The
OTel API is being deprecated in favor of named log records, while Langfuse
[`event` remains a supported observation type](https://langfuse.com/docs/observability/features/observation-types).
The [Langfuse OTLP endpoint](https://langfuse.com/integrations/native/opentelemetry)
ingests traces rather than the OTel logs signal; use the Langfuse SDK when the
marker must appear as a Langfuse event observation, and send vendor-neutral
OTel log-based events to a logs backend.

Examples:

- routing decision made;
- cache hit or miss;
- tool selected;
- guardrail decision emitted;
- user feedback submitted;
- fallback path triggered.

Use spans for work that has duration. Use events for instantaneous markers inside a larger workflow.

Layered explanation:

- Beginner intuition: an event is a timestamped marker.
- Technical mechanics: events can be represented as point-in-time observations where duration is not meaningful.
- Production implications: events are useful for decisions and state changes, but overusing them can clutter traces.
- Common mistakes: using events for work that should have latency, or recording every minor branch as an event.

### Scores

A score measures quality or feedback. Scores can be attached to:

- traces
- observations
- sessions
- dataset runs

Score data types:

| Type | Value shape | Example |
| --- | --- | --- |
| `NUMERIC` | float | relevance `0.87` |
| `BOOLEAN` | `0` or `1` | hallucination detected `0`, accepted answer `1` |
| `CATEGORICAL` | string | `"correct"`, `"partially_correct"`, `"unsafe"` |
| `TEXT` | string | reviewer notes |

Use scores for things that can change after tracing: user feedback, human review, LLM-as-judge results, regression checks, and dataset evaluations. Use tags for describing what a trace is.

> 💡 **Key insight:** Scores and tags are not interchangeable — using tags for quality labels silently blocks quality dashboards, experiment comparisons, and release-gate analytics.

Layered explanation:

- Beginner intuition: a score says whether something was good, safe, useful, or accepted.
- Technical mechanics: scores have a name, value, data type, optional comment, optional metadata, and a target such as trace, observation, session, or dataset run.
- Production implications: scores power quality dashboards, annotation workflows, eval gates, and release comparisons.
- Common mistakes: using tags for quality labels, changing score meanings over time without configs, and scoring the wrong target.

### Sessions and Users

`user_id` lets you analyze behavior, cost, and quality by end user or account. `session_id` groups multiple traces into a conversation or workflow.

In production, use stable internal identifiers, not raw email addresses or personally identifiable values. If your privacy model requires anonymization, hash or tokenize user identifiers before sending them to Langfuse.

Layered explanation:

- Beginner intuition: a user owns requests; a session groups related requests.
- Technical mechanics: trace attributes propagate across observations so sessions can aggregate traces and user filters can segment usage.
- Production implications: sessions make multi-turn chat and agent debugging possible; user IDs enable cost and quality analysis by account segment.
- Common mistakes: using email addresses, rotating identifiers too often, or treating baggage-propagated IDs as trusted authorization data.

### Releases and Versions

Use:

- `release` for deployed application version, such as a Git SHA or container image tag.
- `version` for logical workflow or prompt version where relevant.

This lets you answer questions like:

- Did latency increase after the last deploy?
- Did answer quality change after prompt v17?
- Did a model migration increase refusal or tool failure rates?

Layered explanation:

- Beginner intuition: release says which code shipped; version says which AI workflow design ran.
- Technical mechanics: release and version are trace/observation fields that appear as dimensions in Langfuse analytics.
- Production implications: without both, regressions turn into archaeology.
- Common mistakes: putting Git SHA in `version`, putting prompt version in `release`, or using unique build timestamps that make grouping useless.

### Metadata

Metadata is useful for filtering and segmentation, but it must be deliberate.

Good metadata:

- `tenantTier`
- `feature`
- `route`
- `model_family`
- `retrievalStrategy`
- `experiment`
- `region`

Avoid metadata with unbounded cardinality:

- full prompts
- raw document text
- request IDs as filter keys
- unique user names as key names
- secrets or credentials

For raw OpenTelemetry ingestion, top-level filterable trace metadata should use `langfuse.trace.metadata.<key>` attributes. Standard OTel attributes are still captured but may land under catch-all metadata and be less useful for Langfuse filtering.

> ⚠️ **Watch out:** Generic OTel attributes without the `langfuse.trace.metadata.<key>` prefix land in catch-all metadata and cannot be used as Langfuse dashboard filters.

Layered explanation:

- Beginner intuition: metadata is the context you want to filter by later.
- Technical mechanics: SDK metadata maps to first-level Langfuse metadata; raw OTel attributes need the `langfuse.trace.metadata.*` or `langfuse.observation.metadata.*` prefix for first-level filtering.
- Production implications: metadata schema becomes part of your observability contract.
- Common mistakes: high-cardinality keys, raw documents, secrets, nested values you expect to filter by, and inconsistent casing.

### Prompts

Prompt management lets you version prompts independently from application code. In production, connect prompt versions to traces so quality, latency, and cost can be compared across prompt changes.

A runnable production workflow fetches the `production` label (the default), compiles variables, and links the returned prompt object to the exact generation:

```python
from langfuse import get_client
from openai import OpenAI

langfuse = get_client()
openai = OpenAI()

prompt = langfuse.get_prompt(
    "support-answer",
    label="production",       # Or use version=17 for a pinned rollout/experiment.
    cache_ttl_seconds=300,
)
compiled = prompt.compile(question="How do I reset SSO?")

with langfuse.start_as_current_observation(
    as_type="generation",
    name="support-answer",
    model="gpt-4o-mini",
    input=compiled,
    prompt=prompt,             # Links this persisted name/version.
) as generation:
    response = openai.responses.create(model="gpt-4o-mini", input=compiled)
    generation.update(output=response.output_text)
```

For a third-party integration that creates the generation, Python SDK 4.14.0 or later can propagate the prompt link:

```python
from langfuse import propagate_attributes
from langfuse.openai import OpenAI

client = OpenAI()
prompt = langfuse.get_prompt("support-answer", label="production")

with propagate_attributes(prompt=prompt):
    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt.compile(question="How do I reset SSO?"),
    )
```

Propagation links only generation observations. A prompt set explicitly on a generation wins over the propagated prompt.

Prompt fetches use a client cache. Fresh entries return locally; expired entries can be served stale while background revalidation runs. Pre-fetch at startup if an instance must not accept traffic without the managed prompt. A `fallback=` value covers a first fetch when both cache and network are unavailable:

```python
prompt = langfuse.get_prompt(
    "support-answer",
    label="production",
    fallback="Answer this support question: {{question}}",
)
compiled = prompt.compile(question="How do I reset SSO?")
```

Check `prompt.is_fallback`. A fallback has no persisted Langfuse prompt version and cannot be linked to a generation; omit `prompt=prompt` and do not propagate it. Record a bounded `promptFallback=true` metadata flag and alert on fallback use instead.

A useful prompt lifecycle is:

1. Store prompt templates in Langfuse.
2. Point a protected or controlled environment label at the approved version.
3. Fetch that label, or pin an immutable version for an experiment.
4. Link the returned prompt object to generation observations.
5. Run experiments before promotion.
6. Watch quality and cost after release.

### Datasets and Experiments

Datasets represent curated examples. Experiments run a task against dataset items and store traces plus evaluation scores.

Use datasets for:

- regression tests before prompt/model changes
- benchmark sets for important workflows
- examples from production failures
- representative user questions
- red-team or safety suites

Good teams continuously turn interesting production traces into dataset items.

Layered explanation:

- Beginner intuition: datasets are the saved examples you use to test changes before users see them.
- Technical mechanics: dataset items contain input, optional expected output, metadata, and optional source trace/observation links; experiment runs execute a task and attach scores.
- Production implications: dataset quality determines whether release gates catch meaningful regressions.
- Common mistakes: including only easy examples, failing to preserve source trace links, and letting expected outputs become stale.

## 📊 Langfuse Metrics

Langfuse metrics are derived from observability and evaluation traces. They are strongest for:

- quality from scores and feedback
- cost and token usage
- latency by model, prompt version, user, or trace name
- volume by trace, token, model, tag, release, or version

Use custom dashboards and the metrics API for product and AI quality questions. Use OpenTelemetry metrics for infrastructure alerting and SLOs.

## 🗺️ End-to-End Trace Example

This compact shape gives future debuggers enough evidence without turning Langfuse into a raw data lake:

```json
{
  "trace": {
    "id": "9d1b6c3e7f9a4d8bb1e2c3a4f5d6e7a8",
    "name": "rag.answer",
    "user_id": "user_8f3a",
    "session_id": "chat_2026_06_18_001",
    "release": "sha-6f4c2d1",
    "version": "rag-prompt-v18",
    "tags": ["rag", "support"],
    "metadata": {
      "tenantTier": "enterprise",
      "retrievalStrategy": "hybrid-v2"
    },
    "environment": "prod"
  },
  "observations": [
    {
      "type": "span",
      "name": "rag.answer",
      "parent_observation_id": null,
      "input": {"question": "How do I reset SSO?"},
      "output": {"answer": "Use Security settings [doc_001]."}
    },
    {
      "type": "retriever",
      "name": "rag.retrieve",
      "input": {"query": "How do I reset SSO?"},
      "output": [{"id": "doc_001", "score": 0.82}],
      "metadata": {"index": "support-kb", "topK": 5}
    },
    {
      "type": "generation",
      "name": "llm.generate_answer",
      "model": "provider-model-name",
      "model_parameters": {"temperature": 0.2},
      "usage_details": {"input_tokens": 820, "output_tokens": 164}
    }
  ],
  "scores": [
    {"name": "citation_present", "data_type": "BOOLEAN", "value": 1},
    {"name": "answer_relevance", "data_type": "NUMERIC", "value": 0.86}
  ]
}
```

Avoid copying this literally into every service. Use it as a minimum review shape: identity, workflow, versioning, step evidence, model usage, and quality.

## 📐 Tradeoffs and Decision Points

| Decision | Prefer this | Tradeoff |
| --- | --- | --- |
| Trace granularity | One trace per user-visible workflow | Too broad hides failures; too narrow loses causality. |
| Observation granularity | One observation per meaningful step | Excessive tiny spans create noise and cost. |
| Input/output capture | Capture safe, useful debugging evidence | Full payload capture can violate privacy or increase retention risk. |
| Metadata | Low-cardinality fields used for filtering | High-cardinality metadata hurts usability and analytics. |
| Scores vs tags | Scores for quality judgments, tags for classification | Misusing tags for quality blocks evaluation analytics. |
| Dataset inclusion | Representative, reviewed failures and common paths | Random dumps of production data create noisy regression tests. |

## 🔍 Troubleshooting the Data Model

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Trace appears but filtering by user/session is unreliable | Trace attributes were set on only one span or not propagated | Use SDK `propagate_attributes()` or baggage plus an allowlisted span processor for raw OTel. |
| Metrics cannot group by `tenantTier` or `retrievalStrategy` | Values were sent as generic OTel attributes under catch-all metadata | Use SDK metadata or `langfuse.trace.metadata.<key>` / `langfuse.observation.metadata.<key>`. |
| LLM cost and token charts are empty | Generations do not include usage details or provider integration did not capture them | Record `usage_details` on generations or use an integration that captures usage. |
| Agent trace is hard to read | All work was recorded as one generic span | Add `agent`, `tool`, `generation`, `retriever`, and `guardrail` observations at natural boundaries. |
| Quality dashboards disagree across teams | Score names or value meanings are inconsistent | Define score configs and evaluator versions; document score semantics. |
| Production failures are not reproducible | Interesting traces never become dataset items | Add a triage step to promote failures into datasets with source trace IDs. |

## 🔗 How Langfuse Relates to OpenTelemetry

| OpenTelemetry concept | Langfuse concept |
| --- | --- |
| Trace | Langfuse trace |
| Span | Langfuse observation |
| Span attributes | Observation fields, metadata, and mapped trace attributes |
| Resource attributes | Service metadata |
| Context propagation | Parent-child relationships across functions and services |
| Baggage | Optional carrier for Langfuse trace attributes across service boundaries |
| OTLP exporter | Direct or Collector-based ingestion into Langfuse |

The practical rule: use OpenTelemetry to represent distributed work consistently, then use Langfuse fields to make LLM behavior understandable.

## 🏷️ Naming Guidelines

Use stable, low-cardinality names:

- `chat.answer`
- `rag.retrieve`
- `llm.generate_answer`
- `agent.execute`
- `tool.web_search`
- `guardrail.output_policy`

Avoid embedding request-specific values:

- Bad: `answer-user-12345`
- Bad: `query-where-is-my-order-987`
- Bad: `tool-call-169236482`

Put variable details in input, output, metadata, or span attributes depending on sensitivity and query needs.

## 🗺️ Minimal Trace Shape for Production LLM Apps

Every important LLM trace should answer:

- Who or what requested this? (`user_id`, `session_id`, tenant metadata)
- Which workflow ran? (`trace_name`, tags)
- Which code and prompt version ran? (`release`, `version`, prompt metadata)
- Which model was called? (generation model)
- What input and output were observed? (respecting privacy controls)
- What did it cost? (tokens and cost fields where available)
- Was it good? (scores, feedback, guardrail results)
- What changed after deploys? (release/version dimensions)

## ✅ Data Model Checklist

- Define stable trace names for each product workflow.
- Define observation names and types for retrieval, tools, generations, agents, guardrails, evaluators, and persistence steps.
- Decide which input/output fields are safe to capture and which must be redacted or disabled.
- Standardize trace attributes: user, session, environment, release, version, tags, and filterable metadata.
- Record token usage on every generation.
- Use scores for feedback and quality; use tags only for known-at-trace-time labels.
- Link prompt/workflow versions to traces and experiments.
- Convert reviewed production failures into dataset items with source trace or observation IDs.
- Keep the same terminology and field casing across services.
