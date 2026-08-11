#!/usr/bin/env python3
"""Deterministic validation for the observability skill package."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

try:
    import yaml
except ImportError as exc:  # pragma: no cover - actionable environment failure
    raise SystemExit("PyYAML is required: install it before running validation") from exc


COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.158.0"
STANDARD_GENAI_METRICS = {
    "gen_ai.client.operation.duration",
    "gen_ai.client.operation.time_to_first_chunk",
    "gen_ai.client.operation.time_per_output_chunk",
    "gen_ai.client.token.usage",
    "gen_ai.execute_tool.duration",
    "gen_ai.invoke_agent.duration",
    "gen_ai.invoke_agent.inference_calls",
    "gen_ai.invoke_agent.tool_calls",
    "gen_ai.invoke_workflow.duration",
    "gen_ai.server.request.duration",
    "gen_ai.server.time_per_output_token",
    "gen_ai.server.time_to_first_token",
}


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def find_quick_validator(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        candidates.append(
            Path(codex_home)
            / "skills/.system/skill-creator/scripts/quick_validate.py"
        )
    candidates.append(
        Path.home()
        / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValidationError(
        "official quick_validate.py not found; pass --quick-validator PATH"
    )


def run_official_validator(skill_root: Path, quick_validator: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(quick_validator), str(skill_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    require(
        result.returncode == 0,
        "official skill validator failed:\n" + result.stdout + result.stderr,
    )


def markdown_targets(text: str) -> set[str]:
    targets = set(re.findall(r"\[[^\]]*\]\(([^)]+\.md(?:#[^)]+)?)\)", text))
    targets.update(re.findall(r"`([^`\n]+\.md(?:#[^`\n]+)?)`", text))
    return targets


def validate_references(skill_root: Path) -> None:
    missing: list[str] = []
    for document in skill_root.rglob("*.md"):
        for target in markdown_targets(document.read_text(encoding="utf-8")):
            clean = target.split("#", 1)[0]
            if "://" in clean or clean.startswith("mailto:"):
                continue
            candidates = [
                (document.parent / clean).resolve(),
                (skill_root / clean).resolve(),
                *((parent / clean).resolve() for parent in skill_root.parents),
            ]
            if not any(candidate.is_file() for candidate in candidates):
                missing.append(f"{document.relative_to(skill_root)} -> {target}")
    require(not missing, "unresolved Markdown references:\n" + "\n".join(missing))


def validate_standard_genai_contract(skill_root: Path) -> None:
    metrics_path = skill_root / "references/metrics/genai.md"
    text = metrics_path.read_text(encoding="utf-8")
    declared = set(
        re.findall(
            r"create_histogram\(\s*[\"'](gen_ai\.[^\"']+)[\"']",
            text,
        )
    )
    unknown = sorted(declared - STANDARD_GENAI_METRICS)
    require(not unknown, f"unknown standard gen_ai.* metrics: {unknown}")
    require("gen_ai.workflow.duration" not in text, "deprecated workflow metric remains")
    for forbidden in ("cache_read", "cache_creation", "reasoning"):
        require(
            f'(\"{forbidden}\", normalized[' not in text,
            f"standard token histogram still records {forbidden}",
        )
    require(
        '("input", normalized["input_tokens"])' in text
        and '("output", normalized["output_tokens"])' in text,
        "standard token histogram must record input and output totals",
    )
    literal_token_types = re.findall(
        r"[\"']gen_ai\.token\.type[\"']\s*:\s*[\"']([^\"']+)", text
    )
    require(
        set(literal_token_types) <= {"input", "output"},
        f"custom values used under gen_ai.token.type: {literal_token_types}",
    )


def complete_python_blocks(document: Path) -> list[str]:
    text = document.read_text(encoding="utf-8")
    return re.findall(
        r"<!-- complete-python-template -->\s*```python\n(.*?)```",
        text,
        re.DOTALL,
    )


def validate_python(skill_root: Path) -> None:
    compiled_templates = 0
    for script in (skill_root / "scripts").glob("*.py"):
        compile(script.read_text(encoding="utf-8"), str(script), "exec")
    for document in skill_root.rglob("*.md"):
        for index, block in enumerate(complete_python_blocks(document), start=1):
            compile(block, f"{document}#complete-template-{index}", "exec")
            compiled_templates += 1
    require(compiled_templates > 0, "no complete Python template was compiled")


def validate_trace_budget_calculator(skill_root: Path) -> None:
    script = skill_root / "scripts/estimate_trace_budget.py"
    spec = importlib.util.spec_from_file_location("estimate_trace_budget", script)
    require(spec is not None and spec.loader is not None, "cannot load trace budget calculator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    decimal = module.Decimal
    estimate = module.estimate_trace_budget(
        traces_per_second=decimal("10"),
        average_spans_per_trace=decimal("5"),
        average_bytes_per_span=decimal("1000"),
        effective_retained_percentage=decimal("5"),
        decision_wait_seconds=decimal("30"),
        burst_factor=decimal("2"),
        cache_multiplier=decimal("10"),
    )
    require(estimate.daily_input_traces == 864_000, "daily input traces are wrong")
    require(estimate.daily_input_spans == 4_320_000, "daily input spans are wrong")
    require(estimate.daily_retained_traces == 43_200, "retained traces are wrong")
    require(estimate.daily_retained_spans == 216_000, "retained spans are wrong")
    require(estimate.daily_retained_bytes == 216_000_000, "retained bytes are wrong")
    require(
        estimate.minimum_active_trace_capacity == 600,
        "active trace capacity is wrong",
    )
    require(
        estimate.suggested_sampled_cache_lower_bound == 6_000
        and estimate.suggested_non_sampled_cache_lower_bound == 6_000,
        "decision cache lower bounds are wrong",
    )
    try:
        module.estimate_trace_budget(
            traces_per_second=decimal("10"),
            average_spans_per_trace=decimal("5"),
            average_bytes_per_span=decimal("1000"),
            effective_retained_percentage=decimal("101"),
            decision_wait_seconds=decimal("30"),
        )
    except ValueError:
        pass
    else:
        raise ValidationError("trace budget calculator accepts retention above 100%")


def validate_serializer_fixtures(skill_root: Path) -> None:
    document = skill_root / "references/tracing/genai/content_capture.md"
    blocks = complete_python_blocks(document)
    require(len(blocks) == 1, "expected one complete content serializer template")
    namespace: dict[str, object] = {}
    exec(compile(blocks[0], str(document), "exec"), namespace)

    captured, batch_size = namespace["serialize_chat_model_input"](
        [
            [{"role": "user", "content": "first"}],
            [{"role": "user", "content": "second"}],
        ]
    )
    require(batch_size == 2, "batched input size is incorrect")
    captured_messages = json.loads(captured)
    require(
        len(captured_messages) == 1
        and captured_messages[0]["parts"][0]["content"] == "first",
        "batched input merged independent conversations",
    )

    first_message = SimpleNamespace(
        type="ai",
        content_blocks=[
            {"type": "text", "text": "choice one"},
            {"type": "image", "url": "https://example.invalid/image"},
        ],
        tool_calls=[{"id": "call-1", "name": "lookup", "args": {"id": 1}}],
        response_metadata={"finish_reason": "tool_calls"},
    )
    second_message = SimpleNamespace(
        type="ai",
        content="choice two",
        tool_calls=[],
        response_metadata={"finish_reason": "stop"},
    )
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(message=first_message, generation_info={}),
                SimpleNamespace(message=second_message, generation_info={}),
            ]
        ]
    )
    outputs = json.loads(namespace["serialize_llm_result"](response))
    require(len(outputs) == 2, "independent generations were merged")
    require(
        outputs[0]["finish_reason"] == "tool_calls"
        and any(part["type"] == "image" for part in outputs[0]["parts"])
        and any(part["type"] == "tool_call" for part in outputs[0]["parts"]),
        "finish reason, multimodal part, or tool call was not preserved",
    )


def production_yaml(skill_root: Path) -> str:
    path = skill_root / "references/collector/production.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"## Configuration.*?```yaml\n(.*?)```", text, re.DOTALL)
    require(match is not None, "production Collector YAML block not found")
    return match.group(1)


def validate_collector_yaml(skill_root: Path) -> str:
    config_text = production_yaml(skill_root)
    parsed = yaml.safe_load(config_text)
    require(isinstance(parsed, dict), "production Collector config is not a mapping")
    exporters = parsed.get("exporters", {})
    require(
        "prometheus_remote_write" in exporters,
        "production config must use prometheus_remote_write",
    )
    require(
        "prometheusremotewrite" not in exporters,
        "deprecated prometheusremotewrite alias remains",
    )
    email_actions = [
        action
        for action in parsed.get("processors", {})
        .get("attributes/drop_secrets", {})
        .get("actions", [])
        if action.get("key") == "user.email"
    ]
    require(
        email_actions == [{"key": "user.email", "action": "delete"}],
        "user.email must be deleted by default",
    )
    return config_text


def validate_collector_image(config_text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="observability-skill-") as temp_dir:
        config_path = Path(temp_dir) / "config.prod.yaml"
        config_path.write_text(config_text, encoding="utf-8")
        command = [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{config_path}:/etc/otelcol/config.yaml:ro",
        ]
        for key, value in {
            "APM_ENDPOINT": "https://example.invalid/otel",
            "APM_AUTHORIZATION": "test",
            "PROMETHEUS_WRITE_ENDPOINT": "https://example.invalid/write",
            "PROMETHEUS_AUTHORIZATION": "test",
            "LOGS_ENDPOINT": "https://example.invalid/logs",
            "LOGS_AUTHORIZATION": "test",
        }.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend(
            [COLLECTOR_IMAGE, "validate", "--config=/etc/otelcol/config.yaml"]
        )
        result = subprocess.run(
            command, check=False, capture_output=True, text=True
        )
        require(
            result.returncode == 0,
            "Collector image validation failed:\n" + result.stdout + result.stderr,
        )


def validate_compatibility(skill_root: Path) -> None:
    text = (skill_root / "references/compatibility.md").read_text(encoding="utf-8")
    for required in (
        "2026-08-10",
        "1.44",
        "1.44.0",
        "46d43c8949afb53765a202e89f4534eeb75ca3fa",
        "1.3.14",
        "1.2.10",
        "0.158.0",
        "opentelemetry-instrumentation-aws-lambda",
        "0.65b0",
        'x-langfuse-ingestion-version: "4"',
    ):
        require(required in text, f"compatibility contract missing {required}")


def validate_resource_identity_contract(skill_root: Path) -> None:
    identity_path = skill_root / "references/setup/resource_identity.md"
    identity = identity_path.read_text(encoding="utf-8")
    for required in (
        "service.namespace",
        "service.name",
        "service.instance.id",
        "deployment.environment.name",
        "service.version",
        "product-data-management-automation",
        "pdma-api",
        "pdma-worker",
        "full Git commit SHA",
    ):
        require(required in identity, f"resource identity contract missing {required}")
    require(
        "deployment.environment.name` is **not** part" in identity,
        "environment is not excluded from service-instance uniqueness",
    )
    require(
        "gateway Collector must not derive `service.instance.id`" in identity,
        "gateway Collector ownership guard is missing",
    )
    for forbidden in ("## Kubernetes", "## Docker Compose", "## Amazon ECS"):
        require(forbidden not in identity, f"runtime detail remains in identity core: {forbidden}")

    kubernetes = (
        skill_root / "references/setup/resource_kubernetes.md"
    ).read_text(encoding="utf-8")
    for required in ("metadata.uid", "k8s.pod.uid", "SERVICE_INSTANCE_ID"):
        require(required in kubernetes, f"Kubernetes identity missing {required}")

    compose = (
        skill_root / "references/setup/resource_docker_compose.md"
    ).read_text(encoding="utf-8")
    for required in ("container.id", "Compose expands", "host environment"):
        require(required in compose, f"Docker Compose identity missing {required}")

    ecs = (skill_root / "references/setup/resource_ecs.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "ECS_CONTAINER_METADATA_URI_V4",
        "TaskARN",
        "ContainerARN",
        "DockerId",
        "ecs_resource_attributes",
        "instance_scope",
        "CONTAINER_V4",
        "TASK_V4",
        "/taskWithTags",
    ):
        require(required in ecs, f"ECS identity missing {required}")
    require(
        'instance_scope: Literal["task", "container"]' in ecs,
        "ECS resolver does not make instance granularity explicit",
    )

    processes = (
        skill_root / "references/setup/resource_processes.md"
    ).read_text(encoding="utf-8")
    for required in ("UUID v4", "UUID v5", "after the fork"):
        require(required in processes, f"process identity missing {required}")

    package_layout = (
        skill_root / "references/setup/package_layout.md"
    ).read_text(encoding="utf-8")
    for required in (
        'alias="SERVICE_NAMESPACE"',
        'alias="SERVICE_INSTANCE_ID"',
        "default_factory=lambda: str(uuid4())",
        "full Git SHA for PDMA",
    ):
        require(required in package_layout, f"package settings missing {required}")
    require(
        'alias="HOSTNAME"' not in package_layout,
        "HOSTNAME still owns service.instance.id in generic settings",
    )

    bootstrap = (
        skill_root / "references/setup/sdk_bootstrap.md"
    ).read_text(encoding="utf-8")
    require(
        '"service.namespace": settings.service_namespace' in bootstrap,
        "SDK resource omits service.namespace",
    )
    require(
        '"service.instance.id": settings.service_instance_id' in bootstrap,
        "SDK resource omits resolved service.instance.id",
    )
    require(
        "pod name in Kubernetes" not in bootstrap,
        "SDK bootstrap still recommends pod name as instance identity",
    )

    verification = (skill_root / "references/verification.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "Start two replicas",
        "full Git commit SHA supplied by CI/build",
        "A gateway Collector does not stamp application telemetry",
    ):
        require(required in verification, f"identity verification missing {required}")


def validate_ecs_resolver_fixtures(skill_root: Path) -> None:
    document = skill_root / "references/setup/resource_ecs.md"
    blocks = complete_python_blocks(document)
    require(len(blocks) == 1, "expected one complete ECS resolver template")
    namespace: dict[str, object] = {}
    exec(compile(blocks[0], str(document), "exec"), namespace)

    task_arn = (
        "arn:aws:ecs:eu-west-1:111122223333:task/"
        "pricing/158d1c8083dd49d6b527399fd6414f5c"
    )
    container_arn = (
        "arn:aws:ecs:eu-west-1:111122223333:container/cluster/abc123"
    )
    docker_id = "ee08638adaaf009d78c248913f629e382"
    task = {
        "Cluster": "arn:aws:ecs:eu-west-1:111122223333:cluster/pricing",
        "TaskARN": task_arn,
    }
    container = {"ContainerARN": container_arn, "DockerId": docker_id}

    def fake_read(url: str) -> dict[str, str]:
        return task if url.endswith("/task") else container

    namespace["_read_metadata"] = fake_read
    resolve = namespace["ecs_resource_attributes"]
    task_attributes = resolve("http://169.254.170.2/v4/mock", instance_scope="task")
    require(
        task_attributes["service.instance.id"] == task_arn,
        "ECS task scope does not select TaskARN",
    )
    container_attributes = resolve(
        "http://169.254.170.2/v4/mock", instance_scope="container"
    )
    require(
        container_attributes["service.instance.id"] == container_arn,
        "ECS container scope does not select ContainerARN",
    )

    container.pop("ContainerARN")
    fallback_attributes = resolve(
        "http://169.254.170.2/v4/mock", instance_scope="container"
    )
    require(
        fallback_attributes["service.instance.id"] == docker_id,
        "ECS container scope does not fall back to DockerId",
    )
    try:
        resolve("http://169.254.170.2/v4/mock", instance_scope="invalid")
    except ValueError:
        pass
    else:
        raise ValidationError("ECS resolver accepts an invalid instance scope")


def validate_async_work_contract(skill_root: Path) -> None:
    router = " ".join(
        (skill_root / "SKILL.md").read_text(encoding="utf-8").split()
    )
    for required in (
        "DB-backed state machines",
        "Durable handoffs carry context",
        "stable workflow/run ID",
    ):
        require(required in router, f"durable-work router missing {required}")

    discovery = " ".join(
        (skill_root / "references/discovery.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "FOR UPDATE SKIP LOCKED",
        "otel_traceparent",
        "carrier atomically with the work item/state change",
        "workflow_run_id",
    ):
        require(required in discovery, f"durable-work discovery missing {required}")

    handoffs = " ".join(
        (skill_root / "references/tracing/async_handoffs.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "Decide parent-or-link first",
        "traceparent",
        "context=otel_context.Context()",
        "context=None",
        "untrusted metadata",
    ):
        require(required in handoffs, f"async-handoff contract missing {required}")

    durable = " ".join(
        (skill_root / "references/tracing/durable_work.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "DB-Backed Work Queues and Durable State Machines",
        "normalize_trace_carrier",
        "context=otel_context.Context()",
        "links=links",
        "app.workflow.run.id",
        "workflow_run_id",
        "same database transaction",
        "Retrying the **same** work item keeps its original scheduling carrier",
    ):
        require(required in durable, f"durable-work tracing missing {required}")

    queue = " ".join(
        (skill_root / "references/tracing/queue_messaging.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "Boto3SQSGetter",
        "Boto3SQSSetter",
        "MessageAttributeNames",
        "context=otel_context.Context()",
        "messaging.batch.message_count",
        "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=boto3",
    ):
        require(required in queue, f"queue tracing missing {required}")

    scheduled = " ".join(
        (skill_root / "references/tracing/scheduled_jobs.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in ("Always start a new root trace", "shutdown_observability()"):
        require(required in scheduled, f"scheduled-job tracing missing {required}")
    for forbidden in ("Boto3SQSGetter", "FOR UPDATE SKIP LOCKED", "MessageAttributes"):
        require(forbidden not in scheduled, f"scheduled-job reference leaks {forbidden}")

    worker_runtime = " ".join(
        (skill_root / "references/tracing/worker_runtime.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in ("signal.SIGTERM", "context.attach", "context.detach"):
        require(required in worker_runtime, f"worker runtime missing {required}")

    logging = " ".join(
        (skill_root / "references/logging/structlog.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "Logs carry **execution correlation**; traces carry **causal topology**",
        "worker logs use trace_id B",
        "workflow_run_id",
        "causal_trace_id",
    ):
        require(required in logging, f"durable-work logging missing {required}")

    verification = " ".join(
        (skill_root / "references/verification.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for required in (
        "durable DB work",
        "carrier and the work item/runnable state commit atomically",
        "logs carry the current worker",
    ):
        require(required in verification, f"durable-work verification missing {required}")


def validate_lambda_contract(skill_root: Path) -> None:
    lambda_path = skill_root / "references/tracing/lambda_functions.md"
    text = lambda_path.read_text(encoding="utf-8")
    for required in (
        "AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-handler",
        "/opt/otel-instrument",
        "OTEL_PROPAGATORS=tracecontext,xray-lambda",
        "**not** use `xray-lambda`",
        "faas.invocation_id",
        "aws.lambda.invoked_arn",
        "OTEL_INSTRUMENTATION_AWS_LAMBDA_FLUSH_TIMEOUT",
        "force_flush()",
        "Do not use `context.aws_request_id` as `service.instance.id`",
        "API_GATEWAY_EVENT",
        "SQS_EVENT",
        "LAMBDA_CONTEXT",
        "partial-batch failure",
    ):
        require(required in text, f"Lambda contract missing {required}")
    require(
        not re.search(r"arn:aws:lambda:[^\s]+:layer:", text),
        "Lambda reference hardcodes a regional/versioned layer ARN",
    )


def validate_production_policy_contract(skill_root: Path) -> None:
    router = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    require(
        "references/tracing/production_policy.md" in router,
        "production policy is not routed from SKILL.md",
    )
    require(
        "Don't accept a force-sampling signal from an untrusted" in router,
        "force-sampling trust guard is missing from SKILL.md",
    )

    policy = (
        skill_root / "references/tracing/production_policy.md"
    ).read_text(encoding="utf-8")
    for required in (
        "new-release burn-in",
        "actual effective retained ratio",
        "minimum active trace capacity",
        "estimate_trace_budget.py",
        "authenticated internal control",
        "`tracecontext` by default",
        "tested rollback path",
    ):
        require(required in policy, f"production policy missing {required}")

    discovery = (skill_root / "references/discovery.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "p99 complete-trace arrival time",
        "backend ingest/retention budget",
        "Do not silently substitute `5%`",
    ):
        require(required in discovery, f"production discovery missing {required}")

    production = (skill_root / "references/collector/production.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "filter/successful_probes",
        "decision_cache:",
        "num_traces >= new_traces_per_second",
        "keep-release-burn-in",
    ):
        require(required in production, f"production Collector guide missing {required}")

    package_layout = (
        skill_root / "references/setup/package_layout.md"
    ).read_text(encoding="utf-8")
    bootstrap = (skill_root / "references/setup/sdk_bootstrap.md").read_text(
        encoding="utf-8"
    )
    require(
        "| `OTEL_PROPAGATORS` |" in package_layout
        and "| `tracecontext` |" in package_layout,
        "package layout does not default propagators to tracecontext",
    )
    require(
        "export OTEL_PROPAGATORS=tracecontext" in bootstrap,
        "SDK bootstrap does not default propagators to tracecontext",
    )


def validate_routing_contract(skill_root: Path) -> None:
    router_path = skill_root / "SKILL.md"
    router_text = router_path.read_text(encoding="utf-8")
    router = " ".join(router_text.split())
    discovery = " ".join(
        (skill_root / "references/discovery.md")
        .read_text(encoding="utf-8")
        .split()
    )

    paths = (
        "references/tracing/async_handoffs.md",
        "references/tracing/queue_messaging.md",
        "references/tracing/durable_work.md",
        "references/tracing/scheduled_jobs.md",
        "references/tracing/worker_runtime.md",
        "references/tracing/lambda_functions.md",
        "references/setup/resource_kubernetes.md",
        "references/setup/resource_docker_compose.md",
        "references/setup/resource_ecs.md",
        "references/setup/resource_processes.md",
        "references/setup/startup_fastapi.md",
        "references/setup/startup_worker_cli.md",
        "references/setup/startup_prefork.md",
    )
    for path in paths:
        require(path in router, f"SKILL router does not expose {path}")

    rows = {
        line.split("|", 2)[1].strip(): line
        for line in router_text.splitlines()
        if line.startswith("|") and line.count("|") >= 3
    }

    def require_row(
        label: str,
        required: tuple[str, ...],
        forbidden: tuple[str, ...] = (),
    ) -> None:
        row = rows.get(label, "")
        require(row, f"routing row missing: {label}")
        for fragment in required:
            require(fragment in row, f"{label} route missing {fragment}")
        for fragment in forbidden:
            require(fragment not in row, f"{label} route leaks {fragment}")

    require_row(
        "Scheduled job or CLI batch",
        ("scheduled_jobs.md",),
        ("queue_messaging.md", "durable_work.md", "async_handoffs.md"),
    )
    require_row(
        "Queue publish or direct queue consumer",
        ("async_handoffs.md", "queue_messaging.md"),
        ("durable_work.md", "scheduled_jobs.md"),
    )
    require_row(
        "DB-backed queue, outbox, lease, or durable state transition",
        ("async_handoffs.md", "durable_work.md"),
        ("queue_messaging.md", "scheduled_jobs.md"),
    )
    require_row("Amazon ECS or Fargate", ("resource_ecs.md",))
    require_row("AWS Lambda function", ("lambda_functions.md",))
    require_row("FastAPI", ("sdk_bootstrap.md", "startup_fastapi.md"))
    require_row(
        "Gunicorn/uWSGI or another pre-fork server",
        ("sdk_bootstrap.md", "startup_prefork.md", "resource_processes.md"),
    )

    for required in (
        "managed invocation",
        "tracing/lambda_functions.md",
        "tracing/scheduled_jobs.md",
        "tracing/durable_work.md",
    ):
        require(required in discovery, f"discovery routing missing {required}")

    stale_path = skill_root / "references/tracing/workers_and_queues.md"
    require(not stale_path.exists(), "stale workers_and_queues.md still exists")
    for document in skill_root.rglob("*.md"):
        require(
            "workers_and_queues.md" not in document.read_text(encoding="utf-8"),
            f"stale workers_and_queues.md route in {document.relative_to(skill_root)}",
        )


def validate_context_footprint(skill_root: Path) -> None:
    maximum_lines = {
        "SKILL.md": 240,
        "references/setup/resource_identity.md": 220,
        "references/setup/sdk_bootstrap.md": 380,
        "references/setup/resource_ecs.md": 220,
        "references/tracing/async_handoffs.md": 80,
        "references/tracing/queue_messaging.md": 260,
        "references/tracing/durable_work.md": 220,
        "references/tracing/scheduled_jobs.md": 80,
        "references/tracing/worker_runtime.md": 100,
        "references/tracing/lambda_functions.md": 300,
    }
    for relative, maximum in maximum_lines.items():
        line_count = len((skill_root / relative).read_text(encoding="utf-8").splitlines())
        require(
            line_count <= maximum,
            f"context budget exceeded for {relative}: {line_count} > {maximum}",
        )


def validate_review_regressions(skill_root: Path) -> None:
    model = (
        skill_root
        / "references/tracing/genai/langchain/model_callback.md"
    ).read_text(encoding="utf-8")
    require(
        'for key in ("ls_model_name", "ls_model_type")' not in model,
        "ls_model_type is still a model-name fallback",
    )
    require(
        '"chunk_count": 0' in model and '"captured_chunks"' in model,
        "streaming count and content buffer are not independent",
    )

    streaming = (
        skill_root
        / "references/tracing/genai/langchain/streaming_and_agent_span.md"
    ).read_text(encoding="utf-8")
    require('version="v2"' in streaming, "LangChain stream schema is not explicit")
    require(
        streaming.count("invocation_counters() as counters") >= 3,
        "one or more agent wrappers omit invocation counters",
    )

    for relative in (
        "references/collector/component.md",
        "references/collector/production.md",
    ):
        text = (skill_root / relative).read_text(encoding="utf-8")
        require(
            'x-langfuse-ingestion-version: "4"' in text,
            f"{relative} lacks the Langfuse v4 header",
        )

    for relative in (
        "references/tracing/genai/langchain/model_callback.md",
        "references/tracing/genai/provider_sdk.md",
        "references/setup/sdk_bootstrap.md",
        "references/setup/resource_ecs.md",
        "references/collector/production.md",
        "references/tracing/genai/content_capture.md",
        "references/tracing/queue_messaging.md",
        "references/tracing/durable_work.md",
        "references/tracing/lambda_functions.md",
    ):
        require(
            "## Contents" in (skill_root / relative).read_text(encoding="utf-8"),
            f"{relative} lacks navigation",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "skill_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--quick-validator", type=Path)
    parser.add_argument(
        "--collector-image",
        action="store_true",
        help=f"also validate YAML with {COLLECTOR_IMAGE}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    skill_root = args.skill_root.resolve()
    try:
        run_official_validator(skill_root, find_quick_validator(args.quick_validator))
        validate_references(skill_root)
        validate_standard_genai_contract(skill_root)
        validate_python(skill_root)
        validate_trace_budget_calculator(skill_root)
        validate_serializer_fixtures(skill_root)
        config_text = validate_collector_yaml(skill_root)
        validate_compatibility(skill_root)
        validate_resource_identity_contract(skill_root)
        validate_ecs_resolver_fixtures(skill_root)
        validate_async_work_contract(skill_root)
        validate_lambda_contract(skill_root)
        validate_production_policy_contract(skill_root)
        validate_routing_contract(skill_root)
        validate_context_footprint(skill_root)
        validate_review_regressions(skill_root)
        if args.collector_image:
            validate_collector_image(config_text)
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: observability skill validation")
    if not args.collector_image:
        print(f"NOTE: rerun with --collector-image to validate {COLLECTOR_IMAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
