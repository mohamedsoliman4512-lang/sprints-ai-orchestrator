# Engineering Changelog and Requirements Traceability

## Delivery implementation

| Requirement | Implementation | Evidence / status |
|---|---|---|
| LangGraph multi-agent orchestration | `src/agents/orchestrator.py` | Single `StateGraph`, conditional routing |
| Typed state and learner checkpointing | `src/state.py`, `learner_config()` | `MemorySaver`, thread ID per learner |
| Curriculum RAG with ChromaDB | `src/tools/rag_retriever.py` | 500-token chunks, 50-token overlap, metadata citations |
| Retrieval latency | `benchmark_retrieval()` | Cold start reported separately; warmed p95/max measured |
| Read-only SQLite | `src/tools/db_queries.py` | SELECT guard and SQLite read-only connection |
| Secure Python grading | `src/tools/code_repl.py` | Docker default, no network, read-only root FS, non-root, resource caps; fail closed |
| Evaluator-optimizer cap | `src/agents/assessor_agent.py` | Hard maximum is 3; executable tests remain authoritative |
| Deterministic employer matching | `find_matching_openings()` | Live database rows and explicit rejection reasons |
| Human placement approval | `placement_worker()` | LangGraph interrupt pauses final decision |
| Graceful tool failures | `src/fallback.py` | Structured `UNABLE_TO_VERIFY` response |

## Decisions and limitations

- The supplied learner CSV contains 25 learners while the kickoff deck reportedly states 500; the supplied CSV is treated as the executable sample of record.
- The partner openings CSV and curriculum file are synthetic demo fixtures where original client data was not supplied. They are labelled accordingly and must not be represented as live employer data.
- The placement thresholds (12 modules and score 85) are inferred from the supplied sample and require sponsor confirmation.
- The discovery-call requirements are taken from `docs/project_charter.md`; the 80% wait-time reduction cannot be claimed without Sprints' baseline.
- Docker is the only default grading security boundary. The explicit `process` mode is for local development only and is not a secure sandbox.
- The local in-memory checkpointer is suitable for a reproducible demo process. Production deployment requires a durable checkpointer and authenticated identity-to-thread mapping.
- Retrieval benchmark results are environment-dependent. Report measured cold-start, p95 and maximum values; do not claim a pass before running the benchmark on the target machine.
- Fixed ImportError in src/main.py: CHROMA_DIR was defined only in
  src/tools/rag_retriever.py. It is now defined once in src/config.py and
  imported by rag_retriever.py. Unit tests did not import main.py, so the
  defect was found only during end-to-end verification.


