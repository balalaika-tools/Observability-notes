# Review: `.agents/skills/observability`

Reviewed 2026-08-11 against the skill as it stands on disk (untracked in git).
Scope of the read: all 45 files — `SKILL.md`, 41 reference files, 2 scripts,
`agents/openai.yaml`.

**Measurements used below**

| Thing | Value |
| --- | --- |
| Total package | ~391 KB |
| `references/` | ~337 KB (~84k tokens) |
| Unconditional load (`SKILL.md` + the six "Always" files + `discovery.md` + `verification.md`) | ~93 KB (~23k tokens) |
| Largest single reference | `collector/production.md`, 497 lines |
| Frontmatter description | 715 chars / 90 words |

**Overall.** This is a strong, unusually disciplined skill. The router/reference
split is real (not decorative), the "one owner per boundary" rule is enforced
consistently across files, the error contract is genuinely single-sourced, and
the deterministic validator is more than most skills ever get. The findings
below are mostly about (a) a build-order/dependency inversion that makes the
declared workflow unbuildable as written, (b) copy-paste hazards in the
Collector configs that the skill's own rules forbid, (c) org-specific content
baked into a "reusable" skill and enforced by its validator, and (d) unconditional
context cost.

Findings are grouped by the review layer you asked for, tagged
**[High] / [Medium] / [Low]**, and each states what / why / recommendation.

---

## Contents

- [1. Context delegation and structure](#1-context-delegation-and-structure)
- [2. Routing / dependency-graph correctness](#2-routing--dependency-graph-correctness)
- [3. Technical correctness and best practice](#3-technical-correctness-and-best-practice)
- [4. Coverage](#4-coverage)
- [5. Consistency and contradictions](#5-consistency-and-contradictions)
- [6. Clarity and usability](#6-clarity-and-usability)
- [7. Maintainability and tooling](#7-maintainability-and-tooling)
- [8. Agent behaviour](#8-agent-behaviour)
- [9. What is already good](#9-what-is-already-good-keep-it)
- [10. Prioritised action list](#10-prioritised-action-list)

---

## 1. Context delegation and structure

### 1.1 [High] The unconditional load is ~23k tokens, before any task-specific file

**What.** `SKILL.md` says "Do not read the whole `references/` tree", then the
**Always** table (`SKILL.md:87-96`) mandates six files — `compatibility.md`,
`conventions/naming.md`, `conventions/errors.md`, `setup/resource_identity.md`,
`setup/package_layout.md`, `setup/auto_instrumentation.md` — plus
`discovery.md` (Step 1) and `verification.md` (before reporting). That is
~93 KB / ~23k tokens for *every* invocation, including "add one business
attribute to an existing span" or "why do I have duplicate spans?".

**Why it matters.** The skill's stated design goal is minimal, task-relevant
context. At ~23k tokens of fixed cost the router is no longer doing the job it
claims; and for small tasks the ratio of irrelevant to relevant material is
worse than if the agent had grepped for what it needed. `resource_identity.md`
(168 lines on namespace/version/instance-ID ownership) and `package_layout.md`
(213 lines of pydantic settings) are dead weight for a repair or debug task on
an already-instrumented service.

**Recommendation.** Split "Always" into **Always** vs **Always for new/extended
instrumentation**:

- Truly always: `conventions/naming.md` + `conventions/errors.md` (they are the
  house style every code sample depends on).
- Only when creating or changing SDK setup: `resource_identity.md`,
  `package_layout.md`, `auto_instrumentation.md`.
- `compatibility.md`: demote to "read before copying any version-sensitive
  example" (which is what the file itself says at line 3) and put the two facts
  that matter unconditionally — the pinned convention revision and the Langfuse
  v4 header — inline in `SKILL.md` as a 3-line block.
- `verification.md` (292 lines): split into `verification/core.md` (trace shape,
  errors, config, shutdown) and per-domain fragments (`genai`, `propagation`,
  `collector`, `production`) routed by the same conditions that routed the
  implementation. Today a plain HTTP service loads 11 GenAI/Lambda/durable-work
  checklist sections it will never run.

### 1.2 [Medium] `provider_sdk.md` is a mandatory dependency of the LangChain path for two unrelated span shapes

**What.** `SKILL.md:165` routes "RAG — embedding and retrieval spans, on
**either** path" to `references/tracing/genai/provider_sdk.md`, "Other
operations". A LangChain/LangGraph RAG agent therefore loads a 387-line
direct-SDK file — OpenAI client wrapper, streaming generator, retry loop — to
obtain two ~15-line span sketches. `architecture.md:74` repeats the same
cross-route.

**Why.** This is exactly the "unrelated reference material contaminates the
implementation" failure `SKILL.md:10` warns about, and it invites the agent to
also copy the direct-SDK wrapper into a LangChain service — the duplicate-owner
defect rule 5 exists to prevent.

**Recommendation.** Extract `references/tracing/genai/retrieval.md` (~50 lines:
`embeddings` span, `retrieval` span, safe-by-default vs opt-in attribute lists,
the `invoke_workflow` wrapper sketch). Route both paths there. `provider_sdk.md`
then becomes genuinely direct-SDK-only.

### 1.3 [Medium] `model_callback.md` (518 lines) carries two ~90 %-identical callback classes

**What.** `OTelLLMCallback` and `OTelStreamingLLMCallback` differ in three
places (a `GENAI_REQUEST_STREAM` value, `on_llm_new_token`, and the chunk
bookkeeping). The file itself says at line 497 "The two can be merged into one
handler that branches on the streaming flag".

**Why.** ~200 lines of near-duplicate code an agent must diff to find the delta;
two places for every future fix (the abandoned-run guard, for example, only
mentions `chunk_count` defensively because it was written for both). It is also
the largest file under `genai/` and is loaded for every GenAI task.

**Recommendation.** Ship one handler with `streaming: bool` behaviour and a
short "if you prefer two classes while building, split at these three points"
note. Expect ~180 lines saved on the hot path.

### 1.4 [Low] Two files are below the useful floor for a separate file

`tracing/scheduled_jobs.md` (34 lines) and `setup/startup_worker_cli.md`
(21 lines) both exist mainly to say "configure once, flush in `finally`", and
`worker_runtime.md` says it a third time. Consider merging
`startup_worker_cli.md` into `scheduled_jobs.md` + `worker_runtime.md`, or
folding the flush pattern into `sdk_bootstrap.md`'s "Startup order" section and
keeping only the boundary-specific guidance in the tracing files. Fewer files
also means fewer routing rows to keep in sync.

### 1.5 [Low] `agents/openai.yaml` is an undocumented cross-harness artifact

`agents/openai.yaml` declares `default_prompt: "Use $observability to …"`.
Nothing in `SKILL.md` references it, `validate_skill.py` does not check it, and
`$observability` is not a Claude Code invocation form. Either document it in a
`README` inside the skill (what harness consumes it, who keeps it in sync with
the frontmatter description) or delete it.

---

## 2. Routing / dependency-graph correctness

### 2.1 [High] The declared work order is unbuildable: tracing code imports the metrics module

**What.** `SKILL.md:62` mandates **tracing → metrics → logging → collector**,
and `langchain/architecture.md:63-71` restates it as a build order with metrics
at step 5. But every tracing code sample imports the metrics module that step 5
creates:

- `model_callback.md:158` — `from observability.genai_metrics import record_model_operation`
- `model_callback.md:139` — `from observability.agent_counters import current_counters`
- `model_callback.md:330` — `record_time_to_first_chunk`
- `provider_sdk.md:58-61` — `record_model_operation`, `record_time_to_first_chunk`
- `tools_and_middleware.md:31` — `record_tool_execution`
- `streaming_and_agent_span.md:31,38` — `invocation_counters`, `record_agent_invocation`

`observability/genai_metrics.py` and `observability/agent_counters.py` are both
*defined only in* `references/metrics/genai.md` (lines 32-221 and 233-265).

**Why it matters.** An agent following the routing table literally writes step 2
(model callback), gets six `ImportError`s, and then either loads
`metrics/genai.md` out of order (fine, but the router said not to yet) or —
worse — invents its own recorder functions inline, which is exactly the label-drift
failure `metrics/genai.md:30` says the module exists to prevent. The
`architecture.md` instruction "Do not build all four layers and then debug" makes
this worse: step 2 cannot be verified in isolation as promised.

**Recommendation.** Pick one:

1. Move the instrument definitions and recorder signatures into the tracing
   entry point (`genai/attributes.md`) as a "contract you will import", leaving
   `metrics/genai.md` to own dashboards, cardinality traps, and the fan-out
   rationale; or
2. Change the routing order for GenAI services to **attributes → metrics module
   → tracing → logging**, and say why in `SKILL.md:62`; or
3. Cheapest: add one line to `SKILL.md:62` and `architecture.md:63` —
   "GenAI tracing imports `observability/genai_metrics.py` and
   `observability/agent_counters.py`; create those first from
   `metrics/genai.md`, then return here."

Option 3 unblocks the agent; option 1 is the right long-term shape.

### 2.2 [Medium] `agent_counters.py` is placed in `package_layout.md` but specified in `metrics/genai.md`

`package_layout.md:32` and `architecture.md:91` both list
`observability/agent_counters.py` in the tree, but its `ContextVar` contract
lives in `metrics/genai.md:233`. Combined with 2.1, the agent has to hold three
files open to write one 25-line module. Whichever fix you take for 2.1, keep the
`InvocationCounters` definition in the same file as the routing row that first
requires it.

### 2.3 [Medium] No routing row exists for the audit / repair / troubleshoot modes the description promises

**What.** The frontmatter description advertises "Add, **audit, repair, upgrade,
or troubleshoot**… investigate missing or duplicate signals". `SKILL.md` has no
row, section, or file for any of those. Step 1 (`discovery.md`) is written for
greenfield instrumentation ("Answer these before editing code", "Find the
service's entry point"). The only repair-shaped material is one line at
`discovery.md:37` ("If the service already has partial instrumentation, extend
it") and the diagnostic tables scattered in
`sdk_bootstrap.md:357-367`, `resource_identity.md:161-168`, and
`queue_messaging.md:149-160`.

**Why.** Half the advertised trigger surface has no workflow. An agent asked
"my spans are duplicated" or "token counts are zero on streamed calls" will
either run the full greenfield discovery (~23k tokens of context for a
one-line fix) or improvise. The knowledge to answer both is already in the skill
— it is just not reachable from the router.

**Recommendation.** Add a **Step 0 — pick the task mode** table to `SKILL.md`
with 4-5 rows, each naming a minimal file set:

| Mode | Load |
| --- | --- |
| New instrumentation | Step 1 discovery, then the routing tables below |
| Audit / review existing telemetry | `conventions/*`, `verification.md`, plus the boundary file for the service type |
| Debug a specific symptom | `references/troubleshooting.md` (new), then the one file it points at |
| Version upgrade | `compatibility.md` only, then the files its checklist names |
| Collector-only change | `collector/*` + `tracing/production_policy.md` |

And create `references/troubleshooting.md` as a **symptom → cause → file** index
consolidating the failure tables that already exist (no spans / duplicate spans /
siblings instead of children / zero tokens on streamed calls / orphan consumer
traces / 404 from Collector / spans missing in Gunicorn / missing final spans in
CLI / identical `service.instance.id` across replicas / metrics series growth).
This is close to zero new content and is the single highest-leverage addition.

### 2.4 [Low] Flask/Django route to a FastAPI-flavoured file

`SKILL.md:126` sends "HTTP/API service (FastAPI, Flask, Django)" to
`tracing/http_service.md`, whose only concrete code is a FastAPI
`@app.exception_handler` and `request.scope.get("route")`. There is no Flask
`errorhandler` / Django middleware equivalent, and `startup_fastapi.md` has no
sibling. Either add a 15-line "Flask/Django equivalents" block to
`http_service.md` (exception hook, route-template attribute, where
`instrument_app` goes) or narrow the routing row to FastAPI and say the others
are "same shape, framework hooks differ".

---

## 3. Technical correctness and best practice

### 3.1 [High] Streaming spans held across `yield` leak the span into the caller's context

**What.** `provider_sdk.md:168-273` (`stream_chat`) and
`streaming_and_agent_span.md:135-190 / 195-237` all wrap a generator body in
`with tracer.start_as_current_span(...)` and `yield` from inside it.

**Why it matters.** Python `contextvars` are not isolated per generator (PEP 550
was rejected). `start_as_current_span` calls `context.attach()`; when the
generator yields, that attach is still in effect in the **consumer's** context.
Consequences, all silent:

- Any span the consumer creates between chunks becomes a child of the model span
  instead of the request span. This produces precisely the "siblings where you
  expected nesting" / wrong-parent symptom that `verification.md:67` and
  `streaming_and_agent_span.md:321` attribute to *lost* context — so the skill's
  own diagnostic will send the reader the wrong way.
- If the consumer interleaves two streams, the `detach()` tokens unwind out of
  order and the SDK logs `Failed to detach context`.
- Concurrent streaming requests in one task can inherit each other's parent.

The skill correctly flags the *other* generator hazard (span never ends,
`provider_sdk.md:283`, `streaming_and_agent_span.md:270`) but not this one.

**Recommendation.** For streaming boundaries, do not hold `start_as_current_span`
across a `yield`. Use `tracer.start_span()` plus a narrow
`trace.use_span(span, end_on_exit=False, record_exception=False)` around only the
work that must nest under it, and end the span in `finally` — the pattern the
skill already uses in the LangChain callback (`model_callback.md:205`,
`errors.md:104-118`, "Case 3 — you started the span manually"). Add a
`verification.md` check: "a span created by the consumer between two streamed
chunks is a child of the request span, not of the model span."

### 3.2 [High] Collector `filter` snippet uses a key the filter processor does not define, and is never image-validated

**What.** `production.md:56-68`:

```yaml
filter/successful_probes:
  error_mode: ignore
  trace_conditions:
    - >
      resource.attributes["service.name"] == "my-api" and ...
```

with the prose claim "The pinned Collector uses `trace_conditions`". The
`filterprocessor` schema is `traces: { span: [...], spanevent: [...] }`;
`trace_statements` (note: *statements*) is the **transform** processor's key.
`trace_conditions` matches neither.

**Why.** `validate_skill.py:258-263` extracts only the first ```` ```yaml ````
block **after** the `## Configuration` heading. The filter snippet sits *before*
that heading (line 58), so `--collector-image` never validates it. Neither are:
the Langfuse pipeline block (`production.md:309-362`), the temporary-policy
block (`production.md:432-444`), both `dev_staging.md` configs, or
`component.md`'s pipeline and exporter snippets. An agent that copies the filter
block ships a Collector that refuses to start — during the one change where
telemetry blindness is most expensive.

**Recommendation.** (a) Fix the key to the processor's real schema and re-verify
against the pinned image. (b) Change `production_yaml()` to extract **every**
YAML block from `collector/*.md`, merge-or-validate each independently, and
image-validate all of them under `--collector-image`. That converts a class of
silent doc rot into a CI failure.

### 3.3 [High] The production config hardcodes exactly the values the skill forbids copying

**What.** `production.md:151-195` is a complete, copy-pasteable `tail_sampling`
block containing `decision_wait: 30s`, `num_traces: 50000`,
`expected_new_traces_per_sec: 500`, cache sizes `500000`,
`threshold_ms: 15000`, `min_value: 8000` (input tokens), and
`sampling_percentage: 5`. `discovery.md:262` likewise shows
`120 traces/s; p99 … 18 s; normal successes measured at 3%` in the report
template.

Meanwhile `SKILL.md:211`, `discovery.md:210`, `production_policy.md:56`, and
`production.md:30` all say — four times — do not copy these.

**Why.** Four prose warnings do not beat one syntactically valid YAML block an
agent can paste. This is the highest-probability real-world failure of the whole
skill: an unjustified 5 % sample and a `decision_wait` shorter than a 30-second
agent trace, shipped with confident-sounding prose around it.

**Recommendation.** Replace every unmeasurable literal in the *config* blocks
with typed placeholders that cannot be deployed by accident, e.g.
`decision_wait: <MEASURED_P99_COMPLETE_ARRIVAL_PLUS_JITTER>`,
`sampling_percentage: <MEASURED_NORMAL_SUCCESS_PERCENT>`, and add a
`validate_skill.py` rule asserting that no `sampling_percentage`,
`threshold_ms`, `num_traces`, `decision_wait`, or token `min_value` in
`collector/production.md` is a bare number. Keep one clearly-fenced
"illustrative filled-in example" section further down with `# EXAMPLE ONLY —
derived from the measurements in §Required measurements` on every line. Same
treatment for `discovery.md:262`.

### 3.4 [High] The Collector deletes `exception.stacktrace` on the logs path — the error contract's only carrier

**What.** `errors.md` moves all exception detail out of spans and into "one
structured log record with a stack trace" (`errors.md:22`, `:139`).
`structlog.md:286` implements that by putting the trace into the OTel log
record's attributes:

```python
attributes["exception.stacktrace"] = event_dict["exception"]
```

But `production.md:126-131` and `dev_staging.md:160-166` define
`attributes/drop_secrets` with `exception.message` and `exception.stacktrace`
set to `action: delete`, and apply that processor to the **logs** pipeline
(`production.md:272-279`, `dev_staging.md:218-221`).

**Why.** End to end, the skill forbids span events *and* strips the log
attribute that replaced them. `verification.md:90` ("The exception appears once
in the logs, with a stack trace") and `:207` cannot both pass with these configs
deployed. The deletions were plainly written for span attributes (where they are
correct — auto-instrumentation exception events) and then reused on logs.

**Recommendation.** Split the processor: `attributes/drop_secrets` (auth,
cookies, `db.query.text`, `db.statement`, `user.email`) applied to all
pipelines, and `attributes/drop_span_exception_detail`
(`exception.message`, `exception.stacktrace`) applied to **traces only**. Add a
`verification.md` check: "the canary exception's stack trace is present in the
log backend after passing through the Collector."

### 3.5 [Medium] Two trace pipelines each instantiate their own `tail_sampling`, doubling its memory budget

**What.** `production.md:340-362` puts `tail_sampling` in both `traces/apm` and
`traces/langfuse`.

**Why.** The Collector creates a **separate instance** of a processor per
pipeline. So `num_traces: 50000` plus two 500k decision caches are allocated
twice, and the capacity guidance (`production.md:410-420`,
`production_policy.md:105-116`, `estimate_trace_budget.py`) computes a
single-instance figure. On a memory-limited gateway this is the difference
between headroom and `memory_limiter` shedding during an incident. (Decisions
themselves should agree, since the probabilistic policy hashes the trace ID with
a shared default salt — but that is worth stating, not leaving to the reader.)

**Recommendation.** Either sample once and fan out (`tail_sampling` in a first
pipeline exporting to a `forward`/routing connector, then two attribute-shaping
pipelines), or state explicitly that N trace pipelines means N× sampler memory
and multiply the `estimate_trace_budget.py` output by the pipeline count. Add
the pipeline-count factor to the script as `--sampling-pipelines`.

### 3.6 [Medium] `tail_sampling` has no `and_` example, so "5 % of normal successes" is not what the config does

**What.** `production.md` frames the strategy as "errors 100 %, slow 100 %,
important 100 %, **normal successes** a percentage" (`:20-26`), but implements
the last line as a bare top-level `probabilistic` policy at `:191-194`.
Tail-sampling policies OR together, so `sample-the-rest` samples 5 % of
*everything*, including traces already retained. There is no negation operator,
so the stated intent needs `and_`.

**Why.** The effective retained ratio is not what the prose claims, which is
exactly why `production_policy.md:40` has to say "measure the actual effective
retained ratio instead of adding configured percentages". A correct example
would remove most of the need for that caveat.

**Recommendation.** Show the composed form:

```yaml
- name: sample-normal-successes
  type: and
  and:
    and_sub_policy:
      - name: not-error
        type: status_code
        status_code: { status_codes: [OK, UNSET] }
      - name: rate
        type: probabilistic
        probabilistic: { sampling_percentage: <MEASURED> }
```

and keep the "still measure the effective ratio" sentence for the overlap that
remains (latency, attribute policies).

### 3.7 [Medium] Collector `resource/environment` uses `upsert`, overwriting the application's `deployment.environment.name`

**What.** All three configs set `deployment.environment.name` with
`action: upsert` (`dev_staging.md:39-43`, `:145-149`;
`production.md:109-113`). The application already sets it from
`ENVIRONMENT` (`sdk_bootstrap.md:164`, `package_layout.md:109`).

**Why.** This violates the skill's own "one owner per attribute" rule
(`resource_identity.md:48-58`) and the verification check "Exactly one owner
sets each service resource attribute" (`verification.md:215`). Practically: a
service correctly reporting `uat` gets silently relabelled `production` by the
gateway, and the failure mode is undetectable from the application side.

Related precision bug: `resource_identity.md:60` says "Configure enrichment with
`override: false`" — but `override` is a field of the **resourcedetection**
processor, while the configs use the **resource** processor's `action`. The two
mechanisms are conflated.

**Recommendation.** Use `action: insert` in all three configs (fills the gap for
un-instrumented senders, never overwrites), and correct
`resource_identity.md:60` to name both mechanisms: "`resource` processor →
`action: insert`; `resourcedetection` processor → `override: false`". Add the
`insert`-vs-`upsert` distinction to the verification check so it is testable.

### 3.8 [Medium] The manual SQS path requires installing the instrumentation package the skill tells you to leave off

**What.** `queue_messaging.md:55` imports `Boto3SQSGetter` / `Boto3SQSSetter`
from `opentelemetry.instrumentation.boto3sqs`, and says (line 66) "Importing the
adapters does not enable automatic spans". True — but the *package* must be
installed. `auto_instrumentation.md:48-50` lists `boto3sqs` under "leave off",
and `:36-40` warns that installing packages "just in case" widens what zero-code
mode may patch.

**Why.** The reader is left with a genuine contradiction and no resolution. In
zero-code mode, installing the package silently activates the boto3sqs
instrumentation, producing the exact double-consumer-span defect
`queue_messaging.md:187-202` warns about.

**Recommendation.** State the resolution explicitly in
`auto_instrumentation.md` and `queue_messaging.md`: "install
`opentelemetry-instrumentation-boto3sqs` **for its carrier adapters only**, and
in zero-code mode disable the entry point with
`OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=boto3`" — or, better, inline the ~8-line
getter/setter (SQS `MessageAttributes` is `{name: {DataType, StringValue}}`) so
the manual path has no dependency on an instrumentation package at all.

### 3.9 [Medium] `MessageAttributeNames` guidance misses the system-attribute case Lambda relies on

**What.** `queue_messaging.md:70-78` correctly stresses
`MessageAttributeNames=list(propagate.get_global_textmap().fields)`. But
`lambda_functions.md:119` and the SQS fixture at `:207-212` depend on
`AWSTraceHeader`, which is an SQS **system** attribute — retrieved with
`MessageSystemAttributeNames=['AWSTraceHeader']` (or legacy `AttributeNames`),
not `MessageAttributeNames`. A direct (non-Lambda) consumer of an X-Ray-carried
trace following `queue_messaging.md` gets orphans with no error — the same class
of failure the file calls "the single most common cause".

**Recommendation.** Add one row to `queue_messaging.md`'s SQS section
distinguishing user message attributes from system attributes, and note the SQS
limits that bite here (10 message attributes max; attributes count toward the
256 KB message size).

### 3.10 [Medium] The `gen_ai.*` "standard vs invented" boundary is asserted but not verifiable, and the validator only checks metrics

**What.** Rule 3 (`SKILL.md:43`) — "Never invent a key inside a standard
namespace such as `gen_ai.*`" — is the skill's sharpest correctness rule, and
`naming.md:17` doubles down. The skill then uses a large `gen_ai.*` attribute
set as standard, including several that are load-bearing and hard for a reader
to confirm: `gen_ai.response.time_to_first_chunk`,
`gen_ai.request.stream`, `gen_ai.usage.cache_read.input_tokens`,
`gen_ai.usage.cache_creation.input_tokens`,
`gen_ai.usage.reasoning.output_tokens`, `gen_ai.request.reasoning.level`,
`gen_ai.conversation.compacted`, `gen_ai.tool.call.result`,
`gen_ai.data_source.id`. The only machine check is
`validate_skill.py:24-37`, a hand-maintained allowlist of **metric** names —
attributes are not checked at all — and the authority cited is a single
`semantic-conventions-genai` commit SHA (`compatibility.md:14`) with no per-key
traceability.

**Why.** Attributes are the larger and higher-risk surface (the whole point of
`app.gen_ai.usage.*_token_details` living under `app.*` is that guessing wrong
collides with a future OTel key). Right now a reviewer cannot check any single
key without cloning the pinned commit, and the validator gives false assurance
that the contract is enforced.

**Recommendation.** Add `references/genai_attribute_inventory.md`: one row per
`gen_ai.*`/`app.gen_ai.*` key used anywhere in the skill, with columns
*key | standard? | stability (stable/development) | path in the pinned commit |
requirement level*. Then extend `validate_skill.py` to (a) extract every
`gen_ai.*` string literal from all references and assert it appears in the
inventory, and (b) assert every inventory row marked non-standard uses the
`app.` prefix. That converts rule 3 from an assertion into an invariant, and
makes the upgrade checklist mechanical.

### 3.11 [Medium] `record_exception` / `add_event` prohibition is stated as settled fact with no citation and no backend trade-off

**What.** `errors.md:9` — "OpenTelemetry is deprecating `Span.add_event()` and
`Span.record_exception()`" — is asserted with no link, and `SKILL.md:42` and
`:203` turn it into an absolute. `verification.md:82-88` greps for zero hits.

**Why.** Two problems. (1) It is the most consequential single rule in the skill
and the only one with no normative reference, in a file set that otherwise links
the spec generously (`resource_identity.md:41`, `lambda_functions.md:10-12`).
When the reader's OTel version still documents `record_exception` as stable API,
the rule reads as house preference dressed as spec. (2) Several trace backends
render error detail from the `exception` span event; dropping it changes the UI
for real users. The skill elsewhere refuses to choose backends for the user
(`SKILL.md:214`) but silently makes a backend-visible choice here.

**Recommendation.** Add the citation (spec/OTEP + the semconv revision that
moves exception detail to log records) to `errors.md:9`, and add three lines:
"Consequence at the backend: X/Y surface exception detail from the span event;
with this contract that detail arrives as a correlated log record instead —
confirm the backend can pivot span → log before adopting." Also scope the grep
check explicitly to first-party code (auto-instrumentation will keep emitting
exception events, so a naive reader will think the check failed).

### 3.12 [Low] `error.type` values `"cancelled"` and `"abandoned"` break the file's own rule

`errors.md:126-133` says `error.type` should be an exception class name or a
provider status code. `provider_sdk.md:241`, `streaming_and_agent_span.md:175`,
and `model_callback.md:306` use lowercase sentinels `"cancelled"` /
`"abandoned"`. They are bounded, so the cardinality intent holds — but the
convention is now "class name, except three special strings", undocumented.
Either use `CancelledError` / `GeneratorExit` (real class names, still bounded)
or add the sentinel set to `errors.md:126` as a documented closed enum.

### 3.13 [Low] `batch` processor vs exporter-level batching at the pinned version

All three Collector configs use the `batch` processor. Recent Collector
direction is to move batching into `exporterhelper`
(`sending_queue.batch`), with the `batch` processor on a deprecation path.
`compatibility.md`'s upgrade checklist covers component renames but not this.
Add a checklist item: "confirm whether `batch` is still the recommended
mechanism at the pinned version, or whether exporter `sending_queue.batch`
supersedes it" — and note that `batch`-after-`tail_sampling` ordering advice
(`production.md:290-297`) changes if batching moves into the exporter.

### 3.14 [Low] `verification.md` tells you to curl a metrics port no config exposes

`verification.md:176` and `:189` use `curl -s localhost:8889/metrics`, and
`metrics/service.md:197` repeats it. No config in the skill creates a
`prometheus` **exporter** — dev/staging/prod all use `otlphttp` /
`prometheus_remote_write`, and `8888` is the Collector's *self*-telemetry
(`dev_staging.md:71`, `production.md:252`). Following the checks literally
yields a connection refused on the two most important checks in the file
(instrument coverage and the forbidden-label grep).

**Recommendation.** Either add a commented-out `prometheus` exporter on `8889`
to `dev_staging.md` labelled "verification only, remove before commit", or
change the checks to the console/OTLP-debug path the rest of
`verification.md:26-57` already establishes.

---

## 4. Coverage

### 4.1 [High] No test harness, despite tests being mandated in four places

`SKILL.md:56`, `SKILL.md:213`, `verification.md:269-284`,
`model_callback.md:515`, `resource_ecs.md:159`, and
`lambda_functions.md:231` all require focused tests. The skill supplies fixture
*data* (ECS metadata, Lambda events, LangChain message objects) but never shows
how to assert on telemetry: no `InMemorySpanExporter`, no isolated
`TracerProvider` fixture, no metric reader fixture, no example of asserting
parent-vs-link from exported spans — even though
`async_handoffs.md:38` and `durable_work.md:149` explicitly say the in-memory
`links` list is not proof and you must check *exported* spans.

**Recommendation.** Add `references/testing.md` (~90 lines): a pytest fixture
building a `TracerProvider` with `InMemorySpanExporter` and a
`MeterProvider` with `InMemoryMetricReader`, one assertion helper for
"exactly one span named X with parent P and links L", one for "no forbidden
metric label", and the redaction-canary pattern. Route it from `SKILL.md` under
the conditional-load section that currently only has prose.

### 4.2 [Medium] Python-only scope is never declared

The frontmatter description does not say Python; the skill's entire code surface
is Python (`pyproject.toml`/`requirements.txt` in `discovery.md:34`,
structlog, pydantic-settings, `opentelemetry-instrumentation-*`). A Node, Go,
or Java service will match the description and route into a skill with no
applicable code.

**Recommendation.** Add "**Python services**" to the description's first clause,
and one line in `SKILL.md` Scope: "This skill's code is Python; the routing,
conventions, policy, and Collector material are language-neutral — for another
runtime use those and skip `setup/`, `tracing/genai/`, and `logging/`."

### 4.3 [Medium] Named transports appear in discovery but have no adapter

`discovery.md:101-105` names `kombu`, `pika`, `confluent_kafka`, `celery`,
Redis streams, and `google.cloud.pubsub` as detection signals, and
`discovery.md:120-122` asks about Kafka record headers. `queue_messaging.md`
implements only SQS. Celery is recommended for install
(`auto_instrumentation.md:20`) with no note on what its instrumentation already
owns — a live duplicate-owner risk given rule 5.

**Recommendation.** Add a short "other brokers" section to
`queue_messaging.md`: Kafka headers (`list[tuple[str, bytes]]` — needs a
decode/encode getter/setter), Celery (instrumentation already owns publish and
consume spans; do not add your own), Redis streams / Pub/Sub (carrier goes in
the message body field, name the contract). ~40 lines closes the gap between
what discovery asks and what the skill can implement.

### 4.4 [Medium] LangChain sync-vs-async callback selection is unspecified

`model_callback.md:166` and `:333` subclass `AsyncCallbackHandler`, and
`:491-497` selects a callback purely on `streaming=True/False`. Nothing says
whether the handler must match the invocation style
(`invoke` vs `ainvoke` / `stream` vs `astream`) — even though
`tools_and_middleware.md:111` is careful to make exactly that distinction for
middleware ("`@wrap_tool_call` on an `async def`; class-based equivalent is
`awrap_tool_call`; use the async form when invoked with `ainvoke`/`astream`").

**Why.** If sync/async matters for the pinned `langchain-core`, a sync
`agent.invoke()` silently produces zero model spans — indistinguishable from a
broken exporter. Even if the pinned version bridges them, the reader cannot tell,
and the file's own precedent implies it matters.

**Recommendation.** Add a row to the "Which callback to use" table for
invocation style, state what the pinned `langchain-core` version actually does
(verify once, record the result), and add a `verification.md` line: "invoke the
agent the way production invokes it — sync and async paths must both produce
model spans."

### 4.5 [Medium] `on_llm_start` / non-chat models and retriever callbacks are not handled

The callback implements `on_chat_model_start` only. A service using a
completion-style LLM (`text_completion` — an operation the skill's own
vocabulary lists at `attributes.md:95`) or `HuggingFacePipeline` gets no spans.
Similarly, `on_retriever_start`/`on_retriever_end` exist and would cover the
LangChain retriever case that `architecture.md:74` currently pushes into
hand-written `provider_sdk.md` spans.

**Recommendation.** One paragraph in `model_callback.md`: "`on_llm_start` for
non-chat models — same shape, `messages` is `prompts: list[str]`, operation
`text_completion`", and a note on why retriever callbacks are (or are not)
preferred over explicit retriever spans.

### 4.6 [Low] Operational concerns absent

Worth one short section or a line each: telemetry kill switch
(`OTEL_SDK_DISABLED`) for incident response; log-volume sampling (the skill
covers trace sampling in depth and says "sample or drop noisy INFO/DEBUG"
at `structlog.md:341` with no mechanism); app→Collector TLS/mTLS and
`OTEL_EXPORTER_OTLP_HEADERS` (only Collector→backend TLS is covered,
`component.md:218-222`); cost-attribution ownership (`app.llm.estimated_cost_usd`
appears at `metrics/genai.md:316` with no word on where prices live or how they
version); and a privacy note that `dev_staging.md`'s `debug` exporter with
`verbosity: detailed` prints captured prompts to Collector logs.

### 4.7 [Low] gRPC and WebSocket boundaries

`http_service.md` covers HTTP and SSE/chunked streaming. A gRPC service (a
plausible internal API shape) has no routing row, and WebSocket sessions — where
"one trace per turn, correlated by conversation ID"
(`attributes.md:124-141`) is directly relevant — are not mentioned. Low priority
unless the target repos use them.

---

## 5. Consistency and contradictions

### 5.1 [High] Org-specific PDMA content is embedded in a reusable skill *and enforced by its validator*

**What.** `resource_identity.md:144-158` is a "PDMA mapping" section pinning
`service.namespace = product-data-management-automation` and
`service.name = pdma-api | pdma-worker`. `package_layout.md:169` and
`verification.md:73` say "full Git SHA **for PDMA**". Worse,
`validate_skill.py:351-354` and `:414` *require* those strings to be present —
removing the org-specific content makes the skill fail its own validation.

**Why.** The skill is otherwise carefully generic ("a reusable skill cannot
choose a stable repository-specific service identity",
`package_layout.md:99`). An agent reading `resource_identity.md` for an
unrelated service now has a concrete namespace in context and may adopt it; and
nobody can generalise the skill without editing the validator.

**Recommendation.** Move PDMA specifics to a separate, clearly-scoped
`references/local/pdma_identity.md` routed only when the repo is PDMA (or into
the repo's `CLAUDE.md`/`AGENTS.md` where project facts belong). Change the
validator to assert the *shape* — "an example mapping section exists with
namespace/name/version rows" — not the literal strings. Replace
"full Git SHA for PDMA" with "full Git SHA" everywhere.

### 5.2 [Medium] `naming.md` forbids instrument-suffixed metric names; three other files use them

`naming.md:100-106` gives `app.pricing.updates` as **good** and
`app.pricing.counter` as **problematic**, under the rule "Do not encode the
instrument in the name". Then:

- `discovery.md:240` — `app.exceptions_processed.count`
- `metrics/service.md:171-176` — `app.exceptions_processed.count`,
  `app.exceptions_resolved.count`, `app.pricing.updates.count`,
  `app.queue_items_processed.count`
- `metrics/genai.md:311-315` — `app.agent.step_limit.count`,
  `app.agent.fallback.count`, `app.agent.cancelled.count`,
  `app.guardrail.decision.count`

`app.pricing.updates.count` directly contradicts `naming.md`'s own example, and
in Prometheus it renders as `app_pricing_updates_count_total`.

**Recommendation.** Pick one and sweep: drop the `.count` suffix everywhere
(consistent with `naming.md` and with `app.worker.jobs`, which the same file
gets right), or amend `naming.md` to permit `.count` on counters and fix
`app.pricing.updates` → `app.pricing.updates.count`. First option is better.
Add a validator rule so it cannot drift again.

### 5.3 [Medium] Three competing `app.*` namespaces for GenAI facts

| Fact | Namespace used | Where |
| --- | --- | --- |
| token detail maps, capture mode, batch size, token histograms | `app.gen_ai.*` | `attributes.md:48`, `:77`, `metrics/genai.md:53-67` |
| stream chunk count, retry attempt, upstream provider | `app.llm.*` | `provider_sdk.md:253`, `:303`, `attributes.md:119` |
| output capture mode | `app.gen_ai.output.*` | `provider_sdk.md:260` |
| agent TTFC, step count, stop reason, fallback | `app.agent.*` | `streaming_and_agent_span.md:168`, `:291-296` |
| tenant tier | `app.tenant.tier` **and** `app.trace.dimension.tenant_tier` | `http_service.md:143`, `naming.md:118` vs `baggage.md:52`, `production.md:323` |

**Why.** `naming.md:86-89` defines the shape as `app.<domain>.<noun>`; four
different domains are in use for one domain, and the tenant-tier split means the
same fact has two keys, one of which is in the *allowed metric label* list and
the other of which is what baggage actually propagates and the Langfuse
transform reads. A dashboard built on one will silently miss the other.

**Recommendation.** Publish the `app.*` namespace registry in `naming.md` (one
table: `app.gen_ai.*` for model/provider facts, `app.agent.*` for agent-run
facts, `app.workflow.*`, `app.job.*`, `app.<business-domain>.*`), retire
`app.llm.*` into `app.gen_ai.*`, and pick **one** tenant-tier key. Add the
registry to the validator as an allowlist of `app.` prefixes.

### 5.4 [Medium] `error.type` success sentinel: `_NONE` vs omit, unexplained

`metrics/service.md:143-151` records `error.type="_NONE"` on the success path
and explains why ("or success and failure land in different label sets").
`metrics/genai.md:127-132` does the opposite and explains why ("Do not add an
application success sentinel to a standard instrument"), and
`verification.md:186` enforces the omission for standard instruments only.

Both are defensible; the rule is nowhere stated as a rule, so an agent writing a
new `app.*` instrument next to a standard one will guess.

**Recommendation.** One line in `conventions/errors.md` (the owner of
`error.type`): "Standard OTel instruments: omit `error.type` on success.
Application-owned (`app.*`) instruments: use the `_NONE` sentinel so success and
failure share a label set." Cross-reference from both metrics files.

### 5.5 [Low] "Don't need a separate request counter" vs the worker table

`metrics/service.md:24` argues the duration histogram's `_count` makes a
separate request counter unnecessary. The worker table at `:28-36` then defines
both `app.worker.job.duration` (histogram) and `app.worker.jobs` (counter), and
`measure_job` records both. Add one sentence explaining the asymmetry (the
counter carries `app.outcome`, which the standard HTTP histogram already carries
via status code) or drop the counter.

### 5.6 [Low] `configure_logging()` call signature drifts across startup files

`startup_fastapi.md:21` → `configure_logging(providers.logger_provider)`;
`startup_worker_cli.md:12` → same; `worker_runtime.md:24` →
`configure_logging()`; `scheduled_jobs.md` → never calls it (and never imports
`trace`, which it uses at line 10). The default parameter makes all of these
*run*, but the inconsistency reads as three different APIs.

**Recommendation.** Use `configure_logging(providers.logger_provider)`
uniformly, and add the missing call and import to `scheduled_jobs.md`.

### 5.7 [Low] `decision_wait` restated more weakly in the final checklist

`production_policy.md:73`, `production.md:406`, and `verification.md:248` all
correctly say `decision_wait` must exceed **p99 complete-trace arrival plus
jitter**. The closing checklist at `production.md:487` says "exceeds the p99
trace **duration**" — the weaker condition the body specifically warns against.
Make the checklist line identical to the rule.

### 5.8 [Low] `verification.md` structure drift

The `## AWS Lambda (if used)` section (line 121) is unnumbered while its
neighbours are `## 4.` and `## 5.`, and it is missing from the `## Contents`
list (lines 7-22). Trivial, but this file is a checklist an agent walks
top-to-bottom, so a section outside the numbering is a section that gets skipped.

---

## 6. Clarity and usability

### 6.1 [Medium] Fragment-vs-complete-template status is signalled inconsistently

Three different mechanisms are in play: an HTML marker
(`<!-- complete-python-template -->`, used in `resource_ecs.md`,
`content_capture.md`, `lambda_functions.md`, and enforced by
`validate_skill.py:138`), prose warnings
(`provider_sdk.md:16-22`, `model_callback.md:17-22`,
`provider_sdk.md:313-316` "boundary sketches"), and nothing at all
(`metrics/genai.md`'s module, `baggage.md`'s processor, `structlog.md`'s
processor — all complete and compilable but unmarked, so unvalidated).

**Why.** `validate_skill.py` compiles only marked blocks. Three complete modules
that an agent will paste verbatim get no syntax check, while the prose warnings
depend on the agent reading a paragraph above a code fence it is about to copy.

**Recommendation.** Mark every fence with one of three markers —
`complete-python-template`, `partial-fragment`, `sketch` — and extend the
validator to (a) compile all `complete-*` blocks, (b) assert every ```` ```python ````
fence carries one of the three markers. That makes "is this safe to paste?"
mechanical rather than a reading-comprehension exercise.

### 6.2 [Low] `SKILL.md`'s tree and its routing tables disagree about what is "always"

The tree comment at `SKILL.md:68-82` annotates `discovery.md`, `compatibility.md`,
`conventions/`, and `verification.md` as "always", but the **Always** table at
`:87-96` lists a different set (adds three `setup/` files, omits discovery and
verification). Two overlapping representations of the same routing means two
things to keep in sync. Delete the annotations from the tree (keep it as a pure
map) and let the tables be authoritative.

### 6.3 [Low] The provider-SDK streaming fragment needs three undefined helpers

`provider_sdk.md:226-229` calls `extract_stream_usage()` and
`extract_text_delta()`, defined nowhere; the file flags this honestly at `:276-279`
and `:16-22`. It is the right call not to guess provider internals, but the
reader is left with a fence that cannot run and no shape for either helper.
Give each a two-line signature-plus-contract stub
(`extract_text_delta(chunk) -> str | None` — "the incremental text of this
chunk, `None` for a usage-only or role-only chunk") so the gap is a fill-in, not
a design task.

### 6.4 [Low] Ownership footers are good but uneven

Most reference files end with a "Then" pointer (`http_service.md`,
`attributes.md`, `provider_sdk.md`, `metrics/*`, `logging/*`). Missing from
`baggage.md`, `production_policy.md`, `async_handoffs.md`, `worker_runtime.md`,
`resource_*.md`. Cheap to add and it is how an agent finds the next step
without re-reading `SKILL.md`.

---

## 7. Maintainability and tooling

### 7.1 [High] `validate_skill.py` hard-fails without a Codex-installed external validator

**What.** `find_quick_validator()` (`validate_skill.py:49-68`) looks for
`skill-creator/scripts/quick_validate.py` under `$CODEX_HOME` or
`~/.codex/…` and raises `ValidationError` if absent; `main()` calls it first
(`:884`), so the whole run returns `FAIL`. Yet `compatibility.md:48` makes
"Run `python scripts/validate_skill.py`" a **mandatory** step of the upgrade
checklist.

**Why.** On any machine without Codex's skill-creator — including this one — the
mandated step cannot pass, so it will be skipped, so ~800 lines of genuinely
valuable checks never run. The dependency is also invisible: nothing documents
that the validator needs a foreign toolchain.

**Recommendation.** Make the external validator optional: warn and continue when
`quick_validate.py` is absent, require it only under an explicit
`--official-validator` flag, and document the dependency in a `scripts/README.md`.
Add a `--collector-image`-free smoke mode to the compatibility checklist so the
mandatory step is always satisfiable.

### 7.2 [Medium] Most of the checks are string-presence assertions that ossify prose

`validate_async_work_contract`, `validate_lambda_contract`,
`validate_production_policy_contract`, `validate_review_regressions` assert on
exact sentences, e.g.
`"Retrying the **same** work item keeps its original scheduling carrier"`
(`:554`), `"deployment.environment.name\` is **not** part"` (`:358`),
`"Logs carry **execution correlation**; traces carry **causal topology**"`
(`:597`) — bold markers and all.

**Why.** These are regression pins, and the intent is sound (each encodes a real
past defect). But they make ordinary editorial improvement fail CI, which
trains maintainers to weaken the assertion rather than think about the invariant.
And they check the *doc*, not the behaviour.

**Recommendation.** Keep the structural checks (routing rows, reference
resolution, YAML validity, template compilation, calculator arithmetic, forbidden
patterns like `ls_model_type`, hardcoded layer ARNs, `prometheusremotewrite`) —
those are real invariants. Convert the prose pins into a
`tests/regressions.yaml` of `{id, why, pattern, file}` entries, so each pin
carries the defect it prevents and a maintainer can retire it deliberately.
Aim to cut `validate_skill.py` from ~910 to ~400 lines of logic plus data.

### 7.3 [Medium] `validate_context_footprint` covers 10 of 41 files, and not the biggest ones

`validate_context_footprint` (`:791-809`) caps `SKILL.md` at 240 lines and nine
others. Not capped: `collector/production.md` (497),
`langchain/model_callback.md` (518), `metrics/genai.md` (369),
`content_capture.md` (245), `verification.md` (292), `discovery.md` (293),
`logging/structlog.md` (363), `sdk_bootstrap.md` (366 — capped at 380, i.e. 14
lines of headroom).

**Recommendation.** Cap every reference file, and add an aggregate cap on the
unconditional-load set (finding 1.1) — that is the number that actually governs
per-invocation cost. Print the budget usage on success so growth is visible.

### 7.4 [Low] The trace-budget calculator does not model what the docs ask of it

`estimate_trace_budget.py` computes retained volume from a single
`effective_retained_percentage`, but `production_policy.md:22-34` describes a
*stratified* policy (errors 100 %, slow 100 %, critical 100 %, burn-in 100 %,
normal N %) and both the policy file and `production.md:424` warn that
configured percentages cannot be added. The script therefore cannot answer the
question its callers have; it can only re-multiply a number the user must already
have measured. It also ignores the pipeline-count factor from finding 3.5.

**Recommendation.** Accept per-stratum rate/percentage pairs
(`--stratum errors:0.02:100 --stratum slow:0.01:100 --stratum normal:0.97:<N>`),
report the union-aware effective ratio and the resulting daily volume, and add
`--sampling-pipelines` to the capacity output. Keep the current single-ratio
mode as a fallback.

### 7.5 [Low] Compatibility file has no machine-checkable expiry

`compatibility.md` records a review date (2026-08-10) and a version set, and
`validate_skill.py:325-340` asserts those strings are present — which pins them
but never notices when they go stale.

**Recommendation.** Add a `review_by` date to `compatibility.md` and have the
validator warn (not fail) past it. Cheap, and it is the one file whose decay
silently invalidates every code sample.

### 7.6 [Low] Skill placement / discoverability

The skill lives at `.agents/skills/observability/`. It is not listed among this
session's available skills, so in this harness it is reachable only by explicit
file read, not by `/observability` or by automatic description matching. The
sibling `note-maker`/`note-reviewer` skills in the same directory are deleted in
the working tree, so the migration state is ambiguous.

**Recommendation.** Confirm which directory this harness scans (`.claude/skills/`
vs `.agents/skills/`) and either move the skill or symlink it, then verify it
appears in the skill listing. A skill that cannot be auto-invoked loses the
routing benefit that `SKILL.md` is built around.

---

## 8. Agent behaviour

### 8.1 [High] No task-mode selection (see 2.3)

The single biggest agent-behaviour gap. Everything in `SKILL.md` assumes
"instrument this service from scratch"; the description promises five modes. Fix
via the Step 0 table and `troubleshooting.md` in finding 2.3.

### 8.2 [Medium] Conditional-load discipline is stated once for baggage and nowhere else

`discovery.md:157` is a model instruction: "**Do not open `tracing/baggage.md` to
make this decision.** The decision is made here, and the answer is no unless…".
That negative-routing pattern appears exactly once. The same protection is
needed for the files an agent is most likely to open speculatively:
`production_policy.md` + `collector/production.md` (only for production/sampling
work — otherwise the agent absorbs 680 lines of retention policy and starts
proposing tail sampling for a dev service), `lambda_functions.md`,
`durable_work.md`, and `genai/*` for a non-GenAI service (`discovery.md:91`
handles this one well).

**Recommendation.** Add a one-line "do not open unless" guard at the top of each
conditionally-routed file, mirroring the baggage wording. It costs ~10 lines
total and it is the cheapest defence against the contamination `SKILL.md:10`
warns about.

### 8.3 [Medium] Two files are needed before the agent can answer the two blocking questions

`SKILL.md:26-32` correctly names the two blocking questions (backends; consumer
carrier). But answering the second requires the carrier→consequence table at
`discovery.md:129-137`, which the agent only sees after loading a 293-line
file — and by then it has also absorbed the production-measurement checklist
(`discovery.md:191-227`) that is irrelevant to most tasks.

**Recommendation.** Inline the 7-row carrier→consequence table into `SKILL.md`
(it is the highest-value 10 lines in `discovery.md`), and split
`discovery.md` into `discovery.md` (intake, ~150 lines) and
`discovery_production.md` (§8 measurement inputs), routed by the same condition
that routes `production_policy.md`.

### 8.4 [Low] The report template invites fabricated precision

`discovery.md:249-265` asks the agent to post a report containing
`Production: 120 traces/s; p99 complete arrival 18 s; … measured at 3%` before
editing. An agent with no measurements will fill the shape rather than leave it
blank — the report's format implies these numbers are obtainable at intake.

**Recommendation.** Change the template's production line to
`Production: <not measured — see discovery_production.md; policy is PROVISIONAL>`
and require the literal word `PROVISIONAL` whenever any input is unmeasured.
`discovery.md:211` already demands this in prose; make the template enforce it.

---

## 9. What is already good (keep it)

Worth naming so it does not get refactored away:

- **The router genuinely routes.** Condition-composition ("a pre-fork service on
  ECS needs both", `SKILL.md:100`) is handled explicitly, which most skills get
  wrong.
- **Single-ownership discipline.** Rule 5 (one owner per boundary) is applied
  consistently in `auto_instrumentation.md`, `queue_messaging.md`,
  `lambda_functions.md`, `architecture.md`, and `http_service.md` — including
  the harder cases (boto3sqs, Langfuse `CallbackHandler`, LiteLLM gateway).
- **The token-usage funnel.** One normalized dict → one writer → span and metric
  from the same dict is the correct design, and the reasons given
  (`token_usage.md:10`, `:122`) are the real ones.
- **`context=otel_context.Context()` vs `context=None`.** Called out three times
  (`async_handoffs.md:31`, `queue_messaging.md:147`, `durable_work.md:104`) with
  the right emphasis. This is a genuinely invisible bug and the skill kills it.
- **"Verify from exported spans, not the in-memory `links` list."** Rare, correct,
  and repeated where it matters.
- **Middleware ordering A vs B** (`tools_and_middleware.md:123-175`) — the
  clearest explanation of a real trade-off in the whole skill.
- **`_NONE` / both-paths metric recording.** The "denominator that excludes
  errors" argument appears everywhere it should.
- **"Sampling is never a privacy control"** and the unsalted-SHA-1 hash warning
  (`production.md:385-390`) — both are things practitioners get wrong.
- **Honest verification.** "Do not claim a step passed that you did not actually
  run" (`verification.md:5`) plus the closing report-honestly section.

---

## 10. Prioritised action list

**Fix first — these produce broken output today**

1. 2.1 — resolve the tracing↔metrics build-order inversion (3 lines minimum, or restructure).
2. 3.4 — stop deleting `exception.stacktrace` on the logs pipeline.
3. 3.2 — fix the `filter` processor key and image-validate every YAML block.
4. 3.3 — replace copy-pasteable sampling literals with placeholders + validator rule.
5. 3.1 — fix the streaming-generator context leak in all three wrappers.
6. 7.1 — make `validate_skill.py` runnable without Codex's `quick_validate.py`.

**Then — structural / correctness**

7. 2.3 + 8.1 — add task-mode routing and `references/troubleshooting.md`.
8. 1.1 — tier the "Always" set; split `verification.md` and `discovery.md`.
9. 5.1 — move PDMA specifics out and stop the validator requiring them.
10. 3.7 — `upsert` → `insert`; correct the `override:false` vs `action` conflation.
11. 3.10 — add the `gen_ai.*` attribute inventory and validate attributes, not just metrics.
12. 4.1 — add `references/testing.md` with an in-memory exporter harness.

**Then — consistency and cleanup**

13. 5.2 / 5.3 / 5.4 — metric `.count` suffix, `app.*` namespace registry, `error.type` sentinel rule.
14. 3.5 / 3.6 — sampler-per-pipeline memory, `and_` policy for normal successes.
15. 3.8 / 3.9 — boto3sqs adapter-without-instrumentation, SQS system attributes.
16. 1.2 / 1.3 — extract `genai/retrieval.md`; merge the two callback classes.
17. 4.2 / 4.3 / 4.4 / 4.5 — declare Python scope; other brokers; sync-vs-async callbacks; `on_llm_start`.
18. 6.1 — one marker scheme for every code fence, enforced.
19. 7.2 / 7.3 / 7.4 — de-ossify the validator, cap every file, stratify the budget calculator.
20. 3.11 — cite the span-event deprecation and state the backend trade-off.

**Low priority**

3.12, 3.13, 3.14, 1.4, 1.5, 2.4, 4.6, 4.7, 5.5, 5.6, 5.7, 5.8, 6.2, 6.3, 6.4,
7.5, 7.6, 8.2, 8.3, 8.4.
