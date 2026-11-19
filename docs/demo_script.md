# Stakeholder Demo Script (3–5 minutes)

## Before recording

1. Open this repository in VS Code and activate `.venv`.
2. Start Docker Desktop and confirm `docker info` succeeds.
3. Run `python scripts/build_db.py` and `python -m pytest -q`.
4. Run `python scripts/benchmark_retrieval.py` once and keep its actual output visible. Do not claim the 1000 ms target unless `passed` is `true`.
5. Start screen recording (OBS Studio, Windows Snipping Tool screen recording, or PowerPoint recording). Keep the terminal and VS Code visible.

## Walkthrough (target 4 minutes)

**0:00–0:30 — Business problem**  
“This system addresses three bottlenecks: repetitive curriculum questions, manual Python grading, and career matching. The key constraint is trust: the system must not invent learner qualifications or make final placement decisions autonomously.”

**0:30–1:10 — Architecture**  
“Every request enters one LangGraph StateGraph with typed state. Conditional routing sends it to the advisor, assessor, or placement worker. Checkpoints are scoped by learner ID, so separate learner sessions do not share a thread.”

**1:10–1:55 — Curriculum and learner context**  
“The advisor retrieves curriculum passages from ChromaDB, with module and source metadata, and reads learner progress from read-only SQLite. It returns evidence and retrieval timing. If the source is unavailable, the system says ‘Unable to verify’ rather than guessing.”

**1:55–2:40 — Secure assessment**  
“The assessor executes the submitted function against deterministic test cases in a disposable Docker container. The container has no network, a read-only filesystem, a non-root user and resource limits. If Docker is unavailable, the code is not executed. Optional optimization can make no more than three revisions, and tests remain authoritative.”

**2:40–3:35 — Placement and HITL**  
“The matcher checks the live database rows deterministically. It only considers learners whose readiness status is ‘Ready for Placement’, checks track, score, completed modules, preferred employer and salary, and explains rejected openings. The graph then pauses. A human reviewer explicitly approves or rejects the proposal.”

**3:35–4:00 — Close**  
“This is a working demo built on the supplied sample records. The 25 learner rows and 12 partner openings are demo data, and the curriculum/openings fixtures are synthetic where original source material was not provided. Production rollout requires validated client data, durable checkpoint storage, and the measured wait-time baseline.”

## Run command

```powershell
python -m src.demo
```

The demo asks for a human approval decision during the final phase. Record the same run; do not edit or fabricate benchmark numbers or test output.
