# Example: RAG with OpenTelemetry and Langfuse

This example shows a single Python service that:

- records a RAG trace in Langfuse;
- creates retriever and generation observations;
- records OpenTelemetry metrics for alerting;
- adds user feedback as a Langfuse score.

The code assumes OpenTelemetry metrics are configured as shown in [../opentelemetry/02_python_instrumentation.md](../opentelemetry/02_python_instrumentation.md).

## Shape of the Trace

```text
rag.answer
  rag.retrieve
  llm.generate_answer
  evaluator.citation_check
```

## Code

```python
import os
import time
from dataclasses import dataclass

from langfuse import get_client, propagate_attributes
from openai import OpenAI
from opentelemetry import metrics

langfuse = get_client()
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


@dataclass
class Document:
    id: str
    title: str
    snippet: str
    score: float


def retrieve(query: str, *, limit: int) -> list[Document]:
    # Replace this with your vector, hybrid, or keyword search.
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
    release = os.getenv("RELEASE", "local")
    prompt_version = os.getenv("PROMPT_VERSION", "rag-prompt-dev")

    with langfuse.start_as_current_observation(
        as_type="span",
        name="rag.answer",
        input={"question": question},
    ) as root:
        with propagate_attributes(
            trace_name="rag.answer",
            user_id=user_id,
            session_id=session_id,
            version=prompt_version,
            metadata={
                "release": release,
                "retrievalStrategy": "hybrid-v2",
                "environment": os.getenv("ENVIRONMENT", "dev"),
            },
            tags=["rag"],
        ):
            with langfuse.start_as_current_observation(
                as_type="retriever",
                name="rag.retrieve",
                input={"query": question, "limit": 5},
            ) as retrieval:
                documents = retrieve(question, limit=5)
                retrieval_documents.record(
                    len(documents),
                    {"retrieval.strategy": "hybrid-v2"},
                )
                if not documents:
                    empty_retrievals.add(1, {"retrieval.strategy": "hybrid-v2"})

                retrieval.update(
                    output=[
                        {
                            "id": doc.id,
                            "title": doc.title,
                            "score": doc.score,
                        }
                        for doc in documents
                    ],
                    metadata={"topK": 5, "index": "support-kb"},
                )

            context = "\n\n".join(
                f"[{doc.id}] {doc.title}\n{doc.snippet}" for doc in documents
            )
            messages = [
                {
                    "role": "system",
                    "content": "Answer using the provided context and cite document IDs.",
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nContext:\n{context}",
                },
            ]

            start = time.perf_counter()
            with langfuse.start_as_current_observation(
                as_type="generation",
                name="llm.generate_answer",
                model=model,
                input={"messages": messages},
                model_parameters={"temperature": 0.2},
            ) as generation:
                response = openai.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                )
                duration = time.perf_counter() - start
                answer = response.choices[0].message.content or ""
                usage = response.usage

                attrs = {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "openai",
                    "gen_ai.request.model": model,
                }
                operation_duration.record(duration, attrs)

                if usage:
                    token_usage.record(
                        usage.prompt_tokens,
                        attrs | {"gen_ai.token.type": "input"},
                    )
                    token_usage.record(
                        usage.completion_tokens,
                        attrs | {"gen_ai.token.type": "output"},
                    )

                generation.update(
                    output=answer,
                    usage_details={
                        "input_tokens": usage.prompt_tokens if usage else 0,
                        "output_tokens": usage.completion_tokens if usage else 0,
                    },
                )

            citation_ids = {doc.id for doc in documents}
            citations_present = int(any(doc_id in answer for doc_id in citation_ids))
            langfuse.score_current_trace(
                name="citation_present",
                value=citations_present,
                data_type="BOOLEAN",
            )

            result = {
                "answer": answer,
                "citations_present": bool(citations_present),
            }
            root.update(output=result)
            return result
```

## Feedback Endpoint

Store the Langfuse trace ID with the user-visible response. Then feedback can attach to the exact trace later.

```python
from langfuse import get_client

langfuse = get_client()


def record_feedback(trace_id: str, accepted: bool, comment: str | None = None) -> None:
    langfuse.create_score(
        trace_id=trace_id,
        name="user_accepted",
        value=1 if accepted else 0,
        data_type="BOOLEAN",
        comment=comment,
    )
```

## What to Alert On

Export OpenTelemetry metrics and alert on:

- `rag.retrieval.empty` rate by retrieval strategy;
- `gen_ai.client.operation.duration` p95 by model and operation;
- `gen_ai.client.token.usage` by model and token type;
- HTTP request error rate from FastAPI instrumentation.

Use Langfuse for:

- examples behind low `citation_present` scores;
- quality by prompt version;
- cost and latency by model;
- trace inspection when a metric alert fires.
