# Packaging Validation Evidence

Date: 2026-11-19

## Checks completed in the packaging environment

- `python -m compileall -q src scripts tests`: PASS.
- AST audit of every non-empty `src/*.py` module, public class and public function for a detailed `Curriculum:` docstring with a valid module title: PASS.
- Jupyter notebook JSON/schema validation and compilation of all code cells: PASS.
- PowerPoint file opens with 9 slides; technical report PDF is 5 pages; presentation PDF is 9 pages.
- SQLite builder: 25 learner rows; database `PRAGMA integrity_check` returns `ok`; 41 learner-employer preferences; 12 partner openings.
- Focused tests: 52 passed (database builder, database query/fallback behavior, curriculum docstring traceability, evaluator-optimizer cap). These ran with a minimal local compatibility shim for the LangChain `@tool` wrapper because the real LangChain packages were not installed in this environment.
- Placement smoke check for sample learner `SPR-1001`: readiness is `Ready for Placement`; deterministic sample matches are OPN-101 (Fawry) and OPN-102 (Swvl).
- Docker default smoke check: Docker unavailable; grader returned `UNABLE_TO_VERIFY` with `SANDBOX_UNAVAILABLE` and did not execute learner code (expected fail-closed behavior).

## Checks not completed here

- Full `python -m pytest -q` against the actual LangChain/LangGraph packages.
- LangGraph runtime, checkpoint isolation execution, ChromaDB indexing/search and warmed latency benchmark.
- Real Docker sandbox grading and 26 escape-attempt suite in a Docker-enabled environment.
- Recorded 3–5 minute walkthrough.

The packaging environment had no network access to install the required LangChain/LangGraph packages. Process-mode grading tests were also blocked by the packaging runtime refusing child-process thread creation (`pthread_create`, resource temporarily unavailable). These are environment limitations, not claimed passes. Run the full verification on the target VS Code machine before submission.
