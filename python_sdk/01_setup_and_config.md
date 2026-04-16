# Setup & Configuration — Langfuse Python SDK

> **Who this is for**: Python engineers setting up Langfuse for the first time. Assumes a working Python 3.9+ environment and a Langfuse account (cloud or self-hosted). No prior Langfuse or OpenTelemetry knowledge required — read [What is Langfuse](../foundations/01_what_is_langfuse.md) first if you want the big-picture context.

---

## 1. Installation

The **Langfuse Python SDK** (version 3.x) is built on top of **OpenTelemetry**. The base package covers all core functionality. Optional extras add framework-specific integrations.

```bash
# Core SDK — covers all instrumentation, tracing, and evaluation APIs
pip install langfuse

# With LangChain integration (installs the callback handler)
pip install "langfuse[langchain]"

# Pin to a specific minor version in production
pip install "langfuse>=3.2,<4.0"
```

> **Key insight**: Langfuse 3.x is a ground-up rebuild on OpenTelemetry. If you used Langfuse 2.x, the API surface changed significantly — the `@observe` decorator and `get_client()` singleton are the new primary patterns.

Verify the install:

```python
import langfuse
print(langfuse.__version__)  # should print 3.x.x
```

---

## 2. Account Setup

Langfuse offers three deployment options:

| Option | Base URL | When to use |
|--------|----------|-------------|
| **Cloud EU** (default) | `https://cloud.langfuse.com` | Most users — EU data residency |
| **Cloud US** | `https://us.cloud.langfuse.com` | US data residency requirement |
| **Self-hosted** | `http://localhost:3000` (default dev port) | Air-gapped, compliance, or full control |

Self-hosted deployment uses the official Docker image. See the [Langfuse self-hosting docs](https://langfuse.com/self-hosting) for Docker Compose and Kubernetes configurations.

### Generating credentials

In the Langfuse dashboard: **Settings → API Keys → Create new key**

You receive two values:
- **`public_key`** — starts with `pk-lf-...`. Safe to include in client-side or shared configs.
- **`secret_key`** — starts with `sk-lf-...`. Treat like a password. Never commit to source control.

```
┌─────────────────────────────────────┐
│         Langfuse Dashboard          │
│                                     │
│  Settings → API Keys                │
│  ┌─────────────────────────────┐   │
│  │ Public Key:  pk-lf-abc123   │   │
│  │ Secret Key:  sk-lf-xyz789   │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

⚠️ The secret key is only shown once at creation time. Copy it immediately.

---

## 3. Environment Variables

**Environment variables** are the recommended way to configure credentials. They decouple secrets from code and work identically in local dev, CI, and production containers.

```bash
# Required — authenticate to Langfuse
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."

# Required if not using EU cloud
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"   # EU (default)
# export LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"  # US
# export LANGFUSE_BASE_URL="http://localhost:3000"           # Self-hosted

# Optional tuning
export LANGFUSE_SAMPLE_RATE="1.0"    # 1.0 = trace everything (default)
export LANGFUSE_DEBUG="false"        # Set to "true" for verbose SDK logs
export LANGFUSE_RELEASE="v2.1.0"    # Tag traces with your app version
export LANGFUSE_THREADS="2"         # Background export threads (default: 1)
```

For `.env` files in development, use [python-dotenv](https://pypi.org/project/python-dotenv/):

```python
from dotenv import load_dotenv
load_dotenv()  # reads .env from the current directory

from langfuse import get_client
langfuse = get_client()  # picks up LANGFUSE_* vars from .env
```

Add `.env` to your `.gitignore` immediately.

```bash
echo ".env" >> .gitignore
```

### Complete env var reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | `str` | — | Project public key (`pk-lf-...`) |
| `LANGFUSE_SECRET_KEY` | `str` | — | Project secret key (`sk-lf-...`) |
| `LANGFUSE_BASE_URL` | `str` | `https://cloud.langfuse.com` | Langfuse instance URL |
| `LANGFUSE_SAMPLE_RATE` | `float` | `1.0` | Fraction of traces to export (0.0–1.0) |
| `LANGFUSE_DEBUG` | `bool` | `false` | Enable SDK-level debug logging |
| `LANGFUSE_RELEASE` | `str` | `None` | App version/release tag attached to all traces |
| `LANGFUSE_THREADS` | `int` | `1` | Background threads for async export |

💡 `LANGFUSE_RELEASE` is especially useful for tracking regressions: set it to your Git SHA or semantic version at deploy time so you can filter traces by release in the dashboard.

---

## 4. Client Initialization

There are two initialization patterns. Choose once per project and stick with it.

### Pattern 1 — Singleton via `get_client()` (recommended)

```python
from langfuse import get_client

# Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL from env.
# Returns the same Langfuse instance on every call — safe to call from
# multiple modules without creating duplicate exporters.
langfuse = get_client()
```

This is the right choice for:
- Applications that use the `@observe` decorator
- Any codebase with multiple modules that all need tracing
- FastAPI, Django, or other long-running web services

### Pattern 2 — Explicit constructor

```python
from langfuse import Langfuse

langfuse = Langfuse(
    public_key="pk-lf-...",         # override env var
    secret_key="sk-lf-...",         # override env var
    base_url="https://cloud.langfuse.com",
    release="v2.1.0",               # tag every trace with this version
    sample_rate=0.5,                # export 50% of traces (reduces cost at scale)
    debug=False,                    # True adds verbose logs — keep False in prod
)
```

Use the explicit constructor when:
- Writing tests that need isolated Langfuse instances
- Running multi-project setups with different credentials per call
- Building CLI tools where env vars aren't set

✅ Production apps: `get_client()`
✅ Tests and CLIs: explicit `Langfuse()`
❌ Both in the same app: creates duplicate exporters, doubled data

---

## 5. The Singleton Pattern Explained

`get_client()` returns the same `Langfuse` object across all imports within a process. This matters because the `@observe` decorator — the primary instrumentation tool — always reads from the **global singleton**.

```python
# module_a.py
from langfuse import get_client
langfuse = get_client()  # instance #1

# module_b.py
from langfuse import get_client
langfuse = get_client()  # same instance #1 — not a new object

# Both modules share one background export thread,
# one OTLP connection, and one active trace context.
```

```
Process memory
┌─────────────────────────────────────────────┐
│                                             │
│  module_a     module_b     module_c         │
│     │             │            │            │
│     └─────────────┴────────────┘            │
│                   │                         │
│                   ▼                         │
│          ┌─────────────────┐                │
│          │ Langfuse (×1)   │                │
│          │ singleton       │                │
│          │ OTel tracer     │                │
│          │ export thread   │                │
│          └────────┬────────┘                │
│                   │ OTLP/HTTP               │
└───────────────────┼─────────────────────────┘
                    ▼
           Langfuse ingest API
```

> **Key insight**: If you call `Langfuse()` directly in multiple modules, each call spawns its own background export thread and OTLP connection. Under load, this creates duplicate spans and wastes resources. Use `get_client()` everywhere except where you explicitly need isolation.

---

## 6. Multi-project Setup (SDK >= 3.2.2)

For applications that route traces to different Langfuse projects (e.g., one project per tenant or environment), pass `langfuse_public_key` to the top-level `@observe`-decorated function. All nested calls inherit this routing automatically.

```python
from langfuse.decorators import observe
from langfuse import get_client

langfuse = get_client()

@observe()
def process_tenant_request(query: str, tenant_id: str) -> str:
    # Retrieve the tenant-specific public key from your config store
    tenant_key = get_tenant_langfuse_key(tenant_id)  # your lookup function

    # Set on the current trace — all child spans inherit this project routing
    langfuse.update_current_trace(
        metadata={"tenant_id": tenant_id},
        # SDK >= 3.2.2: pass langfuse_public_key here to route to a different project
        langfuse_public_key=tenant_key,
    )
    return run_llm_pipeline(query)
```

⚠️ Multi-project routing requires SDK >= 3.2.2. Pin your dependency accordingly if you rely on this feature.

---

## 7. Verifying the Connection

The SDK is designed to be **non-crashing** — errors during export are caught internally and logged, never raised as exceptions into your application code.

```python
import logging
from langfuse import get_client
from langfuse.decorators import observe

# Enable SDK debug logs to see what's happening during development
logging.basicConfig(level=logging.DEBUG)

langfuse = get_client()

@observe()
def verify_setup() -> str:
    return "connection verified"

result = verify_setup()

# Flush immediately so the test trace is exported before the script exits
langfuse.flush()

print("Check the Langfuse dashboard — a trace named 'verify_setup' should appear.")
```

After running this script, open your Langfuse project and look under **Traces**. A single trace named `verify_setup` should appear within a few seconds.

💡 If no trace appears: check `LANGFUSE_BASE_URL` (a common mistake is using the EU URL when your account is US, or vice versa), and set `LANGFUSE_DEBUG=true` to see the raw export attempt in your terminal.

---

## 8. Flush Before Exit

The SDK exports traces **asynchronously** in background threads. For long-running services (FastAPI, Django, Celery workers), this is transparent — spans are batched and shipped continuously.

For **scripts, batch jobs, and short-lived Lambda functions**, the process may exit before the background thread has flushed its buffer. Always call `flush()` at the end:

```python
import sys
from langfuse import get_client
from langfuse.decorators import observe

langfuse = get_client()

@observe()
def batch_process_documents(doc_ids: list[str]) -> dict:
    results = {}
    for doc_id in doc_ids:
        results[doc_id] = process_single_document(doc_id)
    return results

def main():
    doc_ids = load_document_ids_from_s3()  # your data source

    try:
        results = batch_process_documents(doc_ids)
        save_results(results)
    finally:
        # Always flush — even if an exception occurred.
        # The finally block guarantees this runs before sys.exit().
        langfuse.flush()

if __name__ == "__main__":
    main()
```

```
Script lifecycle (without flush)          Script lifecycle (with flush)
─────────────────────────────────         ────────────────────────────────
main() runs                               main() runs
  @observe creates spans          →         @observe creates spans
  spans buffered in memory        →         spans buffered in memory
process exits                     →       langfuse.flush() called
  buffer destroyed ❌             →         waits for export thread ✅
  Langfuse receives nothing       →         Langfuse receives all spans
```

| Context | Flush required? |
|---------|-----------------|
| CLI script | ✅ Yes — always |
| AWS Lambda | ✅ Yes — process recycles after invocation |
| Batch job (one-shot) | ✅ Yes |
| FastAPI / Django | ❌ No — long-lived process, continuous export |
| Celery worker (long-running) | ❌ No |
| Celery task (short-lived) | ✅ Yes — depending on worker lifecycle |

---

## 9. Configuration Precedence

When the same option is set in multiple places, the SDK resolves it in this order:

```
Constructor argument  →  Environment variable  →  Default value
      (highest)                                      (lowest)
```

| Setting | Constructor arg | Env var | Default |
|---------|----------------|---------|---------|
| Public key | `public_key=` | `LANGFUSE_PUBLIC_KEY` | — (required) |
| Secret key | `secret_key=` | `LANGFUSE_SECRET_KEY` | — (required) |
| Base URL | `base_url=` | `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` |
| Sample rate | `sample_rate=` | `LANGFUSE_SAMPLE_RATE` | `1.0` |
| Debug | `debug=` | `LANGFUSE_DEBUG` | `False` |
| Release | `release=` | `LANGFUSE_RELEASE` | `None` |
| Threads | `threads=` | `LANGFUSE_THREADS` | `1` |

In practice: **set everything via env vars in deployed environments**, and use constructor overrides only in tests.

```python
# In tests — override env vars without touching the environment
import pytest
from langfuse import Langfuse

@pytest.fixture
def langfuse_test_client():
    return Langfuse(
        public_key=os.environ["TEST_LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["TEST_LANGFUSE_SECRET_KEY"],
        base_url="https://cloud.langfuse.com",
        sample_rate=1.0,   # capture everything during tests
        debug=True,        # verbose output in CI logs
    )
```

---

## 10. Production Checklist

Before deploying a Langfuse-instrumented service:

```
✅ Credentials in environment variables — never hardcoded in source files
✅ LANGFUSE_RELEASE set to git SHA or semantic version at deploy time
✅ LANGFUSE_SAMPLE_RATE tuned for your traffic volume (start at 1.0, reduce if needed)
✅ LANGFUSE_DEBUG=false (or unset) in production
✅ langfuse.flush() called in scripts, batch jobs, and Lambda handlers
✅ .env file added to .gitignore
✅ Secret key rotated if accidentally committed (generate a new key immediately)
```

> **Key insight**: Sampling is the lever for cost control at scale. A service handling 10,000 LLM calls per hour doesn't need every one traced — `LANGFUSE_SAMPLE_RATE=0.1` cuts ingestion 90% while preserving statistical visibility. See [Advanced Patterns](04_advanced_patterns.md) for sampling strategies that preserve full traces for errors.

---

**Next**: [Part 2: Trace Data Model](02_trace_data_model.md)
