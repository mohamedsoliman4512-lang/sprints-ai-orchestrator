# Sprints Multi-Agent AI Career & Assessment Orchestrator

A LangGraph-based agent ecosystem for curriculum Q&A, learner progress lookup, sandboxed Python assessment, and deterministic partner-opening proposals with mandatory human approval.

> **Demo data notice:** the supplied learner CSV contains 25 sample learners. The curriculum fixture and partner-opening data are synthetic demo assets where original client files were not provided. They are not live Sprints or employer records.

## 1. Requirements

- Python **3.11** (recommended) or 3.12
- Docker Desktop running for secure grading (Docker is the default and only security boundary)
- Internet access on first setup if using the default `fastembed` embedding model
- Optional `OPENAI_API_KEY` for LLM-assisted code refinement; the application remains usable without it

## 2. Setup — Windows PowerShell / VS Code

```powershell
# Open this folder in VS Code, then in its integrated terminal:
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` only if needed. Keep `SPRINTS_SANDBOX=docker` for the secure default. Do not commit `.env` or API keys.

Build / validate the SQLite database:

```powershell
python scripts/build_db.py
python -m pytest -q
```

The CLI automatically builds the ChromaDB index on its first run and warms up the embedding model before accepting a query.

## 3. One-command stakeholder demo

Start Docker Desktop first, then run:

```powershell
python -m src.demo
```

The demo executes three phases in sequence:
1. Curriculum RAG question plus learner profile lookup (`SPR-1001`).
2. Python function grading in a disposable Docker container (`--network none`, read-only filesystem, non-root, dropped capabilities and resource limits). If Docker is unavailable, execution fails closed and returns `Unable to verify`.
3. Deterministic partner-opening proposal from SQLite followed by a LangGraph interrupt. A human must approve or reject; no match becomes final automatically.

The sample learner `SPR-1001` is `Ready for Placement` and has openings in the synthetic sample table. Use another learner ID to demonstrate a non-ready outcome.

## 4. Individual workflows

```powershell
# Curriculum and learner profile
python -m src.main --intent advisor --learner SPR-1001 --query "Explain RAG and LangGraph state"

# Secure Python grading (JSON test cases)
python -m src.main --intent assessment --learner SPR-1001 --code "def add(a,b): return a+b" --tests "[{\"function\":\"add\",\"args\":[2,3],\"expected\":5}]"

# Placement proposal and interactive human approval
python -m src.main --intent placement --learner SPR-1001
```

## 5. Retrieval benchmark

```powershell
python scripts/benchmark_retrieval.py
```

The report separates cold-start model loading from warmed retrieval and reports p50, p95, maximum, over-budget count and pass/fail against 1000 ms. Benchmark on the actual demo machine; do not claim the latency target until the output confirms it.

Embedding options in `.env`:
- `fastembed` (default): local semantic model; first use may download model weights.
- `openai`: OpenAI embeddings; requires credentials and network access.
- `hashing`: offline lexical fallback; does not provide semantic embedding quality.

## 6. Architecture

```text
Learner Request (Pydantic)
          |
          v
   LangGraph StateGraph  <---- MemorySaver (thread_id per learner)
      /       |       \\
 Advisor  Assessor  Placement
   |         |          |
 ChromaDB   Docker    SQLite matcher
 + SQLite   sandbox      |
   |         |       interrupt()
   +---- structured results ----> Human reviewer
                                  |
                            approve / reject
```

- `src/state.py`: Pydantic request/response contracts and typed graph state.
- `src/agents/orchestrator.py`: one graph, conditional routing, learner-scoped checkpoints and placement interrupt.
- `src/agents/advisor_agent.py`: curriculum retrieval and read-only learner profile lookup.
- `src/agents/assessor_agent.py`: deterministic grading with an optional, capped optimizer loop.
- `src/tools/rag_retriever.py`: 500-token chunks, 50-token overlap, ChromaDB, source metadata and latency benchmark.
- `src/tools/db_queries.py`: read-only SQLite access, learner profile and deterministic opening matching.
- `src/tools/code_repl.py`: fail-closed Docker execution tool.
- `src/fallback.py`: shared structured `UNABLE_TO_VERIFY` response.

## 7. Safety and operational guarantees

- All multi-agent routing is performed by LangGraph.
- SQLite uses a read-only connection and a SELECT-only guard.
- Matching is deterministic over database records; every rejected opening includes failed checks.
- A learner must have `readiness_status == "Ready for Placement"` before matching is considered.
- Every successful placement proposal pauses for human approval.
- Docker is the secure grading default. If Docker is unavailable, learner code is not executed. `SPRINTS_SANDBOX=process` is a development-only mode and is **not** a security boundary.
- Tool failures return structured `Unable to verify` results instead of invented answers.
- The evaluator-optimizer can perform no more than three refinements. Executable tests remain authoritative; an LLM cannot override failed tests.

## 8. Test suite

```powershell
python -m pytest -q
```

Tests cover database validation and read-only behavior, tool failure contracts, grading outcomes and sandbox escape attempts, docstring-to-curriculum traceability, graph routing, learner checkpoint namespaces and the three-refinement cap.

## 9. Submission artifacts

- `docs/technical_report.pdf` — architecture, memory, RAG, safety, evidence and limitations.
- `docs/final_presentation.pptx` — stakeholder presentation.
- `docs/demo_script.md` — timed 3–5 minute walkthrough narration and recording checklist.
- `notebooks/evaluator_optimizer_loop.ipynb` — bounded evaluator-optimizer example.
- `docs/project_charter.md` and `docs/CHANGELOG.md` — discovery decisions and traceability.

## 10. Known limitations / production next steps

- Supplied starter assets do not include original curriculum documents or live employer openings; the included fixtures are explicitly synthetic.
- The 80% wait-time reduction requires Sprints' pre-automation baseline and cannot be proven from the supplied sample.
- `MemorySaver` is process-local for the demo. Production needs durable checkpoint storage, authentication, authorization and a verified learner-to-thread mapping.
- Production deployment requires observability, secrets management, retention controls, a hardened container runtime and security review of the execution boundary.
