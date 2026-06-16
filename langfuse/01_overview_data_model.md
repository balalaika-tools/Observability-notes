# Langfuse Overview and Data Model

Langfuse starts from OpenTelemetry's trace model and adds LLM-native fields, workflows, and analytics.

OpenTelemetry can tell you that a request spent 820 ms in an outbound HTTP call. Langfuse can tell you that the call was a generation using a specific model and prompt version, consumed a certain number of tokens, produced a specific answer, received negative user feedback, and regressed after a release.

## What Langfuse Is

Langfuse is a production platform for AI engineering:

- Observability for LLM calls, RAG pipelines, tools, agents, chains, retrievers, guardrails, and evaluators.
- Prompt management and prompt version tracking.
- Scores from users, humans, code evaluators, and LLM-as-judge systems.
- Datasets and experiments for offline evaluation.
- Metrics and dashboards for quality, cost, latency, and volume.
- Public API and data access for internal analytics and workflows.

Langfuse is built on OpenTelemetry. A Langfuse observation is an OpenTelemetry span with Langfuse-specific attributes and types.

## Core Objects

### Trace

A trace is one end-to-end user or system workflow.

Examples:

- A chat message from HTTP request to final streamed answer.
- A RAG answer that retrieves documents, reranks them, calls a model, validates output, and stores feedback.
- An agent task with planning, tool calls, retries, and final synthesis.

Langfuse traces share IDs with OpenTelemetry traces. The trace record can hold:

- `name`
- `userId`
- `sessionId`
- `release`
- `version`
- `tags`
- trace-level `metadata`
- trace-level input and output

Use a trace name for the product workflow, such as `chat.answer`, `support.agent`, or `code_review.generate`, not for high-cardinality values.

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

### Generation

A generation is a special observation for model calls. It should include:

- model name
- model parameters
- input messages or prompt
- output text or structured output
- token usage via `usage_details`
- optional cost details if your system computes custom cost
- provider and request metadata when useful

Generations are where most LLM debugging starts: prompt, model, retrieved context, tool state, token usage, latency, and final output all meet there.

### Event

An event is a point-in-time observation. Use it for something meaningful that happened but does not need duration tracking.

Examples:

- routing decision made;
- cache hit or miss;
- tool selected;
- guardrail decision emitted;
- user feedback submitted;
- fallback path triggered.

Use spans for work that has duration. Use events for instantaneous markers inside a larger workflow.

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

### Sessions and Users

`user_id` lets you analyze behavior, cost, and quality by end user or account. `session_id` groups multiple traces into a conversation or workflow.

In production, use stable internal identifiers, not raw email addresses or personally identifiable values. If your privacy model requires anonymization, hash or tokenize user identifiers before sending them to Langfuse.

### Releases and Versions

Use:

- `release` for deployed application version, such as a Git SHA or container image tag.
- `version` for logical workflow or prompt version where relevant.

This lets you answer questions like:

- Did latency increase after the last deploy?
- Did answer quality change after prompt v17?
- Did a model migration increase refusal or tool failure rates?

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

### Prompts

Prompt management lets you version prompts independently from application code. In production, connect prompt versions to traces so quality, latency, and cost can be compared across prompt changes.

A useful prompt workflow:

1. Store prompt templates in Langfuse.
2. Fetch the version your environment should use.
3. Include the prompt name and version on traces or observations.
4. Run experiments before promotion.
5. Watch quality and cost after release.

### Datasets and Experiments

Datasets represent curated examples. Experiments run a task against dataset items and store traces plus evaluation scores.

Use datasets for:

- regression tests before prompt/model changes
- benchmark sets for important workflows
- examples from production failures
- representative user questions
- red-team or safety suites

Good teams continuously turn interesting production traces into dataset items.

## Langfuse Metrics

Langfuse metrics are derived from observability and evaluation traces. They are strongest for:

- quality from scores and feedback
- cost and token usage
- latency by model, prompt version, user, or trace name
- volume by trace, token, model, tag, release, or version

Use custom dashboards and the metrics API for product and AI quality questions. Use OpenTelemetry metrics for infrastructure alerting and SLOs.

## How Langfuse Relates to OpenTelemetry

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

## Naming Guidelines

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

## Minimal Trace Shape for Production LLM Apps

Every important LLM trace should answer:

- Who or what requested this? (`user_id`, `session_id`, tenant metadata)
- Which workflow ran? (`trace_name`, tags)
- Which code and prompt version ran? (`release`, `version`, prompt metadata)
- Which model was called? (generation model)
- What input and output were observed? (respecting privacy controls)
- What did it cost? (tokens and cost fields where available)
- Was it good? (scores, feedback, guardrail results)
- What changed after deploys? (release/version dimensions)
