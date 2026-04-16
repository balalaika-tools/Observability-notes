# Evaluation

> Replace guesswork with data — attach scores to traces, automate quality checks with LLM judges, and run structured experiments against datasets.

[![Langfuse](https://img.shields.io/badge/Langfuse-evals-orange.svg)](https://langfuse.com/docs/evaluation/overview)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_scoring_system.md](01_scoring_system.md) | Scores | Score types, attaching scores to traces/observations/sessions via SDK |
| [02_llm_as_judge.md](02_llm_as_judge.md) | LLM-as-Judge | Automated evaluation with LLM judges — criteria, setup, production patterns |
| [03_datasets_experiments.md](03_datasets_experiments.md) | Datasets | Dataset items, experiment runs, version comparison, regression detection |
| [04_human_annotation.md](04_human_annotation.md) | Human Review | Annotation queues, structured workflows, team collaboration |

---

## Reading Order

1. **Scoring System** — understand what scores are and where they attach before evaluating anything
2. **LLM-as-Judge** — automate quality scoring at production scale
3. **Datasets & Experiments** — structure offline benchmarks to validate changes before shipping
4. **Human Annotation** — layer human review on top of automated scoring for high-stakes decisions

---

## Prerequisites

- [What is Langfuse](../foundations/01_what_is_langfuse.md) — traces and observations that scores attach to
- [Instrumentation](../python_sdk/03_instrumentation.md) — how to create traces that evaluation can score
