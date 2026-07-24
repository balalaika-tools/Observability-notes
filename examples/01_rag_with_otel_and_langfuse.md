# Example: RAG with OpenTelemetry and Langfuse

Last checked against the Langfuse guide and official documentation on 2026-07-20.

This example shows a single Python service that:

- records a RAG trace in Langfuse;
- creates retriever, generation, and evaluator observations;
- records OpenTelemetry metrics on success and failure;
- handles empty retrieval without asking the model to invent an answer;
- stores safe, idempotent user feedback as a score.

Use the direct SDK bootstrap from [README.md](README.md). Set `LANGFUSE_RELEASE` and `LANGFUSE_TRACING_ENVIRONMENT` before constructing the client.

## 🔄 Shape of the Trace

```text
rag.answer
  rag.retrieve
  llm.generate_answer
  evaluator.citation_check
```

## 🛠️ RAG Code

```python
import os
import re
import time
from dataclasses import dataclass

from langfuse import propagate_attributes
from openai import OpenAI
from opentelemetry import metrics

from observability import langfuse

openai = OpenAI()
meter = metrics.get_meter("example.rag")

retrieval_documents = meter.create_histogram(
    "rag.retrieval.documents",
    unit="{document}",
    description="Number of documents returned by retrieval",
)
empty_retrievals = meter.create_counter(
    "rag.retrieval.empty",
    unit="{request}",
    description="RAG requests where retrieval returned no documents",
)
token_usage = meter.create_histogram(
    "gen_ai.client.token.usage",
    unit="{token}",
    description="Input and output tokens used by model calls",
)
operation_duration = meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="Duration of GenAI client operations",
)
operation_failures = meter.create_counter(
    "gen_ai.client.operation.failures",
    unit="{request}",
    description="Failed GenAI client operations",
)

SECRET = re.compile(r"(?i)(authorization|api[_-]?key|password)\s*[:=]\s*\S+")
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")


def safe_text(value: str, *, limit: int = 2_000) -> str:
    value = SECRET.sub(r"\1=[REDACTED]", value)
    value = EMAIL.sub("[EMAIL]", value)
    return value[:limit]


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    snippet: str
    score: float


def retrieve(query: str, *, limit: int) -> list[Document]:
    # Replace this with vector, hybrid, or keyword search. Enforce document ACLs
    # before returning results; observability must not bypass retrieval authorization.
    return [
        Document(
            id="doc_001",
            title="SSO Administration",
            snippet="Workspace admins can reset SSO configuration from Security settings.",
            score=0.82,
        )
    ][:limit]


def answer_question(question: str, *, user_id: str, session_id: str) -> dict:
    model = "gpt-4o-mini"
    prompt_version = "rag-prompt-v18"
    safe_question = safe_text(question, limit=1_000)

    # Environment is a first-class propagated field. Release is configured once on
    # the client through LANGFUSE_RELEASE (or Langfuse(release=...)).
    with propagate_attributes(
        trace_name="rag.answer",
        user_id=user_id,
        session_id=session_id,
        environment=os.environ["LANGFUSE_TRACING_ENVIRONMENT"],
        version=prompt_version,
        metadata={"retrievalStrategy": "hybrid-v2"},
        tags=["rag"],
    ):
        with langfuse.start_as_current_observation(
            as_type="span",
            name="rag.answer",
            input={"question": safe_question},
        ) as root:
            trace_id = langfuse.get_current_trace_id()

            with langfuse.start_as_current_observation(
                as_type="retriever",
                name="rag.retrieve",
                input={"query": safe_question, "limit": 5},
            ) as retrieval:
                documents = retrieve(question, limit=5)
                retrieval_documents.record(
                    len(documents),
                    {"retrieval.strategy": "hybrid-v2"},
                )
                if not documents:
                    empty_retrievals.add(1, {"retrieval.strategy": "hybrid-v2"})

                # Retain IDs, titles, and scores. Do not export full document bodies.
                retrieval.update(
                    output=[
                        {"id": doc.id, "title": safe_text(doc.title, limit=200), "score": doc.score}
                        for doc in documents
                    ],
                    metadata={"topK": 5, "index": "support-kb"},
                )

            if not documents:
                result = {
                    "status": "no_context",
                    "answer": "I could not find approved source material for this question.",
                    "citations_present": False,
                    "langfuse_trace_id": trace_id,
                }
                root.update(output=result, metadata={"stopReason": "empty_retrieval"})
                return result

            # Raw authorized snippets go to the model. Only a masked copy goes to tracing.
            context = "\n\n".join(
                f"[{doc.id}] {doc.title}\n{doc.snippet}" for doc in documents
            )
            messages = [
                {
                    "role": "system",
                    "content": "Answer using only the provided context and cite document IDs.",
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nContext:\n{context}",
                },
            ]
            traced_messages = [
                {"role": message["role"], "content": safe_text(message["content"])}
                for message in messages
            ]

            metric_attrs = {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": model,
            }
            started = time.perf_counter()
            outcome = "success"

            with langfuse.start_as_current_observation(
                as_type="generation",
                name="llm.generate_answer",
                model=model,
                input={"messages": traced_messages},
                model_parameters={"temperature": 0.2},
            ) as generation:
                try:
                    response = openai.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.2,
                    )
                    answer = response.choices[0].message.content or ""
                    usage = response.usage

                    if usage:
                        token_usage.record(
                            usage.prompt_tokens,
                            metric_attrs | {"gen_ai.token.type": "input"},
                        )
                        token_usage.record(
                            usage.completion_tokens,
                            metric_attrs | {"gen_ai.token.type": "output"},
                        )

                    generation.update(
                        output=safe_text(answer),
                        usage_details={
                            "input_tokens": usage.prompt_tokens if usage else 0,
                            "output_tokens": usage.completion_tokens if usage else 0,
                        },
                    )
                except Exception as exc:
                    outcome = "error"
                    error_type = type(exc).__name__
                    operation_failures.add(
                        1,
                        metric_attrs | {"error.type": error_type},
                    )
                    generation.update(level="ERROR", status_message=error_type)
                    raise
                finally:
                    operation_duration.record(
                        time.perf_counter() - started,
                        metric_attrs | {"app.outcome": outcome},
                    )

            citation_ids = {doc.id for doc in documents}
            with langfuse.start_as_current_observation(
                as_type="evaluator",
                name="evaluator.citation_check",
                input={"candidate_ids": sorted(citation_ids), "answer": safe_text(answer)},
            ) as evaluator:
                matched_ids = sorted(doc_id for doc_id in citation_ids if doc_id in answer)
                citations_present = int(bool(matched_ids))
                evaluator.update(
                    output={"citation_present": bool(citations_present), "matched_ids": matched_ids}
                )
                langfuse.score_current_trace(
                    name="citation_present",
                    value=citations_present,
                    data_type="BOOLEAN",
                )

            result = {
                "status": "answered",
                "answer": answer,
                "citations_present": bool(citations_present),
                "langfuse_trace_id": trace_id,
            }
            root.update(
                output={
                    "status": result["status"],
                    "answer": safe_text(answer),
                    "citations_present": result["citations_present"],
                }
            )
            return result
```

The model-call duration is recorded in `finally`, so timeouts and provider errors are part of latency distributions. Failures also increment a counter and mark the Langfuse generation as an error. This avoids dashboards that report only successful-call latency.

> 💡 **Key insight:** Recording `operation_duration` in `finally` means provider errors and timeouts count toward latency distributions — omitting this makes tail latency appear artificially low.

## 🔒 Safe Feedback Endpoint

> ⚠️ **Watch out:** Never write a score for a caller-supplied trace ID — validate ownership against the authenticated user's own application record first.

Do not expose a helper that writes a score for any caller-supplied trace ID. Authorize against the application record that stored the answer, not against baggage or a value echoed by the browser.

```python
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from observability import langfuse

app = FastAPI()


class FeedbackBody(BaseModel):
    accepted: bool
    comment: str | None = Field(default=None, max_length=500)


class Identity(BaseModel):
    user_id: str
    tenant_id: str


def require_identity() -> Identity:
    # Validate the session/JWT and return server-derived identity.
    ...


def load_stored_answer(trace_id: str):
    # Return the application's answer record, including owner and tenant IDs.
    ...


@app.post("/answers/{trace_id}/feedback", status_code=204)
def record_feedback(
    trace_id: str,
    body: FeedbackBody,
    identity: Annotated[Identity, Depends(require_identity)],
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> None:
    answer = load_stored_answer(trace_id)
    if (
        answer is None
        or answer.user_id != identity.user_id
        or answer.tenant_id != identity.tenant_id
    ):
        # Do not reveal whether another tenant's trace exists.
        raise HTTPException(status_code=404, detail="answer not found")

    score_id = str(uuid5(NAMESPACE_URL, f"{answer.id}:user_accepted:{idempotency_key}"))
    langfuse.create_score(
        score_id=score_id,
        trace_id=answer.langfuse_trace_id,
        name="user_accepted",
        value=1 if body.accepted else 0,
        data_type="BOOLEAN",
        comment=safe_text(body.comment, limit=500) if body.comment else None,
    )
```

Persist the idempotency key and request outcome if the application must return the same HTTP response across retries. A deterministic score ID prevents duplicate Langfuse scores when a request is retried after an uncertain network result.

## ⚠️ What `citation_present` Does Not Prove

This evaluator checks only whether at least one retrieved document ID appears as a substring in the answer.

> ⚠️ **Watch out:** An ID appearing as a substring does not confirm the answer correctly attributes the source — use this score only as a cheap formatting signal, not a groundedness check.

- False positive: the answer says "Unlike `[doc_001]`, this claim has no source." The ID is present, but it does not support the claim.
- False negative: the answer correctly paraphrases the retrieved source but uses a footnote format that omits the raw document ID.
- It does not check whether every factual claim is supported, whether the cited document entails the claim, or whether the cited passage was actually in the model context.

Use it as a cheap formatting signal. For groundedness, evaluate claim-to-source entailment against the exact retrieved passages, require structured citation spans, and calibrate the evaluator on human-reviewed examples.

## 🔔 What to Alert On

Export OpenTelemetry metrics and alert on:

- empty-retrieval rate with a minimum request-volume guard;
- model-call p95 duration by service, environment, model, operation, and outcome;
- model failure rate by low-cardinality error type;
- token usage by model and token type;
- HTTP request error rate from FastAPI instrumentation.

Use Langfuse for trace inspection, prompt-version comparison, cost/latency analysis, and examples behind low evaluator or user-feedback scores.
