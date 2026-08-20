# Compatibility Contract

Read this before copying version-sensitive examples. The GenAI conventions, LangChain stream shapes, Collector component schemas, and backend ingestion requirements evolve independently.

## Reviewed version set

Review date: **2026-08-10**. Review by: **2027-02-10**.

Past the review-by date, treat every version-sensitive example here as
unverified and say so in your report. `validate_skill.py` warns once the date
passes; it does not fail, because a stale contract is a prompt to re-check, not
a broken package.

| Surface | Contract used by this skill |
| --- | --- |
| OpenTelemetry Python | `opentelemetry-api`, `opentelemetry-sdk`, and OTLP exporters `>=1.44,<1.45`; instrumentation packages from the matching `0.65b0` line |
| AWS Lambda instrumentation | `opentelemetry-instrumentation-aws-lambda==0.65b0`; this line adds SQS context propagation, while the AWS Lambda semantic conventions remain development-status |
| Resource semantic conventions | OpenTelemetry semantic conventions `1.44.0`; service identity is stable, platform resource conventions are at the status stated there |
| GenAI semantic conventions | Dedicated `open-telemetry/semantic-conventions-genai` repository at commit `46d43c8949afb53765a202e89f4534eeb75ca3fa` (2026-08-09) |
| LangChain | `>=1.3,<1.4`; examples were reviewed against `1.3.14` and `langchain-core 1.5.3` |
| LangGraph | `>=1.2,<1.3`; examples were reviewed against `1.2.10` and use the v2 `StreamPart` schema |
| Collector | Contrib distribution `otel/opentelemetry-collector-contrib:0.158.0` |
| Langfuse | OTLP/HTTP ingestion v4; send `x-langfuse-ingestion-version: "4"` |

These are compatibility bounds for the templates, not a demand to downgrade a service that already uses newer packages. When the repository has a locked version outside a range, adapt the example to that version and run the upgrade checks below.

## Deliberate compatibility choices

- Standard `gen_ai.client.token.usage` observations use only `gen_ai.token.type=input` and `output`. Cache and reasoning subsets use application-owned instruments.
- LangChain `stream()` and `astream()` examples pass `version="v2"` and consume `StreamPart` dictionaries with `type`, `ns`, and `data`. Do not mix them with the v1 tuple shape.
- Lambda examples distinguish the community `/opt/otel-handler` wrapper from
  the AWS-managed ADOT wrapper used by the selected layer. Layer ARNs are not
  pinned here because they vary by region, architecture, runtime, and release.
- Use `xray-lambda` only when Lambda spans export to AWS X-Ray. Do not combine
  it with the ordinary `xray` propagator, and do not use it for a non-X-Ray
  trace backend.
- `prometheus_remote_write` is the Collector component name. `prometheusremotewrite` is a deprecated alias. Contrib 0.158.0 still requires its HTTP client settings at top level; migrate them under `http` only after the upgraded image validates that schema.
- Collector self-metrics use the declarative
  `service.telemetry.metrics.readers` schema. The manual Prometheus readers set
  `without_type_suffix` and `without_units` so alert names match the canonical
  OTLP `otelcol_*` inventory. Internal logs remain at `INFO` and go to
  `stderr`; internal traces are experimental and opt-in, not a production
  baseline.
- Langfuse receives complete traces over OTLP/HTTP and the v4 ingestion header. The endpoint remains configurable for region and self-hosting.

## Upgrade checklist

Before changing any version above:

1. Re-check every standard metric name, attribute name, enum value, requirement level, and recommended histogram boundary against the pinned GenAI conventions.
2. Re-check service-instance uniqueness, deployment environment values, and Kubernetes/container/cloud resource attributes against the pinned resource conventions.
3. Re-check Lambda invocation attributes, API Gateway/SQS trigger semantics,
   `xray-lambda` rules, wrapper path, layer compatibility, and end-of-invocation
   force-flush behaviour against the selected instrumentation release.
4. Run capture-on and capture-off streaming tests against the real LangChain/LangGraph stream shape. Cover an empty stream, cancellation, and an error after the first chunk.
5. Re-run model/provider metadata fixtures so `gen_ai.request.model` can never become a model type such as `chat` or `llm`.
6. Validate **every** Collector YAML block under `references/collector/` with the exact candidate image and inspect its `components` output for renamed or removed components. Re-test whether `prometheus_remote_write` now accepts nested `http.endpoint` and `http.headers`.
7. Re-check the Collector internal-telemetry declarative schema, metric names
   and stability levels, Prometheus suffix behaviour, log options, and the
   maturity of internal traces. Validate the self-telemetry resource, logs, and
   reader blocks against the candidate image, then test the actual scrape and
   alert queries before promotion.
8. Confirm whether the `batch` **processor** is still the recommended batching mechanism at the candidate version, or whether exporter-level `sending_queue.batch` supersedes it. If batching moves into the exporter, the "`batch` last, after `tail_sampling`" ordering advice in `collector/production.md` changes with it.
9. Re-check every `gen_ai.*` attribute this skill uses against the pinned convention revision, not only the metric names. `validate_skill.py` pins the attribute set as an allowlist, so a convention change shows up as a validation failure with the exact key — resolve each one deliberately rather than widening the allowlist.
10. Re-check backend authentication, endpoints, required headers, and whether trace ingestion remains real-time.
11. Run `python scripts/validate_skill.py` (add `--collector-image` in CI), then perform the exported-telemetry checks in `verification.md`. The script runs without any external toolchain; `--official-validator` additionally requires the Codex skill-creator validator.

Record the new version set, convention tag or commit, and review date in this file in the same change.
