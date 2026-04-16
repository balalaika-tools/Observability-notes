# Configuring Langfuse as an OpenTelemetry Backend

> **Who this is for**: Engineers with an existing OpenTelemetry setup — collector, SDK, or env-var-based — who want to route LLM traces to Langfuse without adopting the Langfuse-native SDK.

**Prerequisites**: [OpenTelemetry Primer](../foundations/02_opentelemetry_primer.md) · [OTel GenAI Semantics](../foundations/03_otel_genai_semantics.md)

---

## 1. What "Langfuse as an OTel Backend" Means

---

Langfuse exposes a standard **OTLP (OpenTelemetry Protocol) HTTP endpoint** at `/api/public/otel`. Any application, framework, or agent that already emits OTel spans can send them directly to Langfuse — no Langfuse-specific SDK required, no vendor lock-in in the application layer.

```
Your App (Go, Java, Node.js, Python, …)
     │
     │  OTel SDK — standard instrumentation
     ▼
OTLP/HTTP exporter
     │
     ▼
https://cloud.langfuse.com/api/public/otel
     │
     ▼
Langfuse — parses spans → traces / observations / generations
```

> **Key insight**: Langfuse SDK v3+ is itself built on this same OTel infrastructure. Using the raw OTLP endpoint and using the Langfuse SDK are two paths to the same destination — the SDK just adds convenience wrappers.

This matters because:
- You can add Langfuse to an existing OTel pipeline **without touching application code** — only collector config changes.
- Every language with OTel support gains Langfuse observability immediately.
- LLM frameworks that auto-instrument with OTel (LlamaIndex, Haystack, OpenLLMetry, etc.) work out-of-the-box.

---

## 2. Endpoints

---

Langfuse offers cloud-hosted regions and self-hosted deployments. Use the base endpoint for automatic signal routing, or the signal-specific path to target traces explicitly.

| Environment | Base Endpoint |
|---|---|
| EU Cloud (default) | `https://cloud.langfuse.com/api/public/otel` |
| US Cloud | `https://us.cloud.langfuse.com/api/public/otel` |
| Self-hosted (v3.22.0+) | `http://localhost:3000/api/public/otel` |
| Signal-specific (traces) | `{base}/api/public/otel/v1/traces` |

💡 The signal-specific path (`/v1/traces`) is required when configuring exporters that append the signal suffix automatically — some OTel SDKs and the `OTLPSpanExporter` class expect the base URL without the suffix, while others (e.g., the OTel Collector `otlphttp` exporter) append `/v1/traces` themselves. Check your exporter docs to avoid double-suffixing.

⚠️ Self-hosted requires Langfuse **v3.22.0 or later**. Earlier versions do not expose the OTLP endpoint.

---

## 3. Authentication

---

Langfuse uses **HTTP Basic Authentication** over every OTLP request. The credential is a base64-encoded string of your **public key** and **secret key** separated by a colon.

### Generating the auth string

Run this once and store the result in a secret manager or environment variable:

```bash
# Generate the auth string (run once, store the output securely)
AUTH_STRING=$(echo -n "pk-lf-YOUR_PUBLIC_KEY:sk-lf-YOUR_SECRET_KEY" | base64)
echo $AUTH_STRING   # e.g. cGstbGYtLi4uOnNrLWxmLS4uLg==
```

> **Key insight**: The `-n` flag on `echo` is critical — it suppresses the trailing newline. A newline in the input produces a different base64 string that will fail authentication silently.

### Required headers

| Header | Value | Purpose |
|---|---|---|
| `Authorization` | `Basic <AUTH_STRING>` | Authenticate the request |
| `x-langfuse-ingestion-version` | `4` | Enables fast preview in the Langfuse UI |

The `x-langfuse-ingestion-version: 4` header is technically optional but strongly recommended — without it, traces may take longer to appear in the UI after ingestion.

---

## 4. Protocol Support

---

Langfuse supports two OTLP/HTTP transport encodings:

| Protocol | Supported | Notes |
|---|---|---|
| ✅ OTLP/HTTP — `http/protobuf` | Yes | Default; lower overhead, recommended for production |
| ✅ OTLP/HTTP — `http/json` | Yes | Easier to inspect with curl/Postman during debugging |
| ❌ OTLP/gRPC | No | Not supported as of SDK v3; use HTTP variants |

Always use the `OTLPSpanExporter` (HTTP variant) — **not** the gRPC exporter. The package names differ:

```
✅  opentelemetry-exporter-otlp-proto-http   →  OTLPSpanExporter (http/protobuf)
❌  opentelemetry-exporter-otlp-proto-grpc   →  OTLPSpanExporter (gRPC) — not supported
```

---

## 5. Python SDK Direct Configuration

---

Install the required packages:

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Configure the exporter and wire it into a `TracerProvider`:

```python
import base64
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

LANGFUSE_PUBLIC_KEY = "pk-lf-..."
LANGFUSE_SECRET_KEY = "sk-lf-..."

# Build Basic Auth credential
auth = base64.b64encode(
    f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
).decode()

exporter = OTLPSpanExporter(
    endpoint="https://cloud.langfuse.com/api/public/otel/v1/traces",
    headers={
        "Authorization": f"Basic {auth}",
        "x-langfuse-ingestion-version": "4",
    },
)

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)

# --- from here, your existing OTel instrumentation works unchanged ---
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("my-llm-call") as span:
    span.set_attribute("gen_ai.system", "openai")
    span.set_attribute("gen_ai.request.model", "gpt-4o")
    # ... call your LLM ...
```

> **Key insight**: Use `BatchSpanProcessor`, not `SimpleSpanProcessor`. `SimpleSpanProcessor` exports synchronously on every span end, blocking your application thread. `BatchSpanProcessor` queues spans and flushes in the background.

💡 Call `provider.shutdown()` (or `trace.get_tracer_provider().shutdown()`) at application exit to flush any buffered spans before the process terminates.

---

## 6. Environment Variable Configuration

---

Most OTel-instrumented frameworks and auto-instrumentation libraries respect the standard OTel environment variables. Set these once and all OTel exporters in the process will target Langfuse — **no code changes required**.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${AUTH_STRING},x-langfuse-ingestion-version=4"
export OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
```

For signal-specific overrides (traces only, leaving metrics/logs to a different backend):

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://cloud.langfuse.com/api/public/otel/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Basic ${AUTH_STRING},x-langfuse-ingestion-version=4"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/protobuf"
```

⚠️ Signal-specific variables (`OTEL_EXPORTER_OTLP_TRACES_*`) take precedence over the generic ones (`OTEL_EXPORTER_OTLP_*`). If both are set, the signal-specific ones win.

---

## 7. OTel Collector Configuration

---

If you already run an **OTel Collector**, you can add Langfuse as an additional exporter in your existing pipeline — no application changes, no new processes. This is the preferred approach when you want to fan out traces to multiple backends.

### Architecture

```
Your App (any language)
     │ OTel SDK
     ▼
┌─────────────────────────────────────────┐
│           OTel Collector                │
│                                         │
│  receivers:  otlp (gRPC :4317,          │
│              HTTP  :4318)               │
│                                         │
│  processors: batch                      │
│                                         │
│  exporters:  ┌─ otlphttp/langfuse ──────┼──▶ Langfuse
│              ├─ jaeger ─────────────────┼──▶ Jaeger UI
│              └─ prometheus ─────────────┼──▶ Grafana
└─────────────────────────────────────────┘
```

### Collector YAML

```yaml
# otel-collector-config.yaml

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 512

exporters:
  otlphttp/langfuse:
    endpoint: "https://cloud.langfuse.com/api/public/otel"
    headers:
      Authorization: "Basic ${AUTH_STRING}"
      x-langfuse-ingestion-version: "4"

  # Keep your existing exporters alongside Langfuse
  jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true

  prometheus:
    endpoint: "0.0.0.0:8889"

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/langfuse, jaeger]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

💡 The Collector's `otlphttp` exporter appends `/v1/traces` automatically. Set `endpoint` to the **base** path (`/api/public/otel`), not the signal-specific path — otherwise you'll get a 404 from `/api/public/otel/v1/traces/v1/traces`.

---

## 8. Span Filtering in the Collector

---

Use the **`filterprocessor`** to route only LLM-related spans to Langfuse, keeping non-LLM spans out of your AI observability budget.

```yaml
# Add to your collector config

processors:
  filter/llm-only:
    error_mode: ignore
    traces:
      span:
        - 'attributes["gen_ai.system"] == nil'   # drop spans with no gen_ai.system attribute
```

Wire it into the pipeline before the Langfuse exporter:

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch, filter/llm-only]
      exporters: [otlphttp/langfuse]
```

⚠️ Root spans (spans with no parent) **must always be sent** to Langfuse — they create the trace container that all child spans attach to. If you drop a root span, Langfuse receives orphaned child spans it cannot display. Only filter leaf spans or internal non-LLM spans, and ensure at least one ancestor per trace always passes the filter.

---

## 9. Verifying the Integration

---

### Quick smoke test (curl)

```bash
# Encode credentials
AUTH=$(echo -n "pk-lf-YOUR_KEY:sk-lf-YOUR_KEY" | base64)

# Send a minimal OTLP/JSON trace (single root span)
curl -X POST https://cloud.langfuse.com/api/public/otel/v1/traces \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic ${AUTH}" \
  -H "x-langfuse-ingestion-version: 4" \
  -d '{
    "resourceSpans": [{
      "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "smoke-test"}}]},
      "scopeSpans": [{
        "spans": [{
          "traceId": "aabbccddeeff00112233445566778899",
          "spanId": "aabbccddeeff0011",
          "name": "test-span",
          "kind": 1,
          "startTimeUnixNano": "1700000000000000000",
          "endTimeUnixNano":   "1700000001000000000",
          "status": {"code": 1}
        }]
      }]
    }]
  }'
```

A `200 OK` response means Langfuse accepted the payload. The trace should appear in the Langfuse UI within **5–10 seconds**.

### Debugging checklist

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Wrong base64 encoding | Regenerate with `echo -n` (no newline) |
| `404 Not Found` | Wrong endpoint path | Check for double `/v1/traces` suffix |
| Spans accepted but not visible | Missing `x-langfuse-ingestion-version: 4` | Add the header |
| Spans accepted but slow | Using `SimpleSpanProcessor` | Switch to `BatchSpanProcessor` |
| No export attempts logged | Exporter not wired to provider | Confirm `add_span_processor` was called |
| gRPC connection refused | Using gRPC exporter | Switch to `OTLPSpanExporter` (HTTP) |

### Enable debug logging

```bash
export OTEL_LOG_LEVEL=debug
python your_app.py 2>&1 | grep -i "otlp\|export\|langfuse"
```

This prints every export attempt, HTTP response code, and retry event — essential for diagnosing silent failures.

---

**Next**: [Part 2: Span Mapping](02_span_mapping.md)
