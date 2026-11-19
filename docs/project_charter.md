# Project Charter: Sprints Multi-Agent AI Career & Assessment Orchestrator

**Status:** v2 (updated with the discovery-call transcript) | **Owner:** MOHAMED EMAD SOLIMAN
**Sponsor:** Youssef El-Sherif, VP Engineering, Sprints

## Problem
The Advanced Agentic AI bootcamp is scaling across MENA and the student-success team is overwhelmed by
repetitive curriculum questions, manual Python grading and hand-matching graduates to hiring partners
(Fawry, e& Egypt, Swvl; the learner data also lists Vodafone Egypt). Autonomous career decisions are too
risky: a wrong qualification claim or a wrong employer match would damage trust built over years.

## What the discovery call fixed (new in v2)
| From the call | What it means for the design |
|---|---|
| Success = student wait times cut by 80%, loop automated reliably | Measure end-to-end latency per scenario; the 80% claim needs Sprints' manual baseline (open item, not invented) |
| Non-negotiable 1: one LangGraph state graph, typed state, session checkpointing per learner | `TypedDict` state, checkpointer keyed by learner thread; a test proves learner A never sees learner B's context |
| Non-negotiable 2: grading strictly sandboxed, learner code never breaches the platform | Docker sandbox (no network, read-only FS, non-root, resource limits) that **fails closed**; hardened harness; 26 escape attempts tested |
| Biggest fear: hallucination (wrong qualifications, wrong employer) | Matching is deterministic code over database rows, every rejection has a reason, a learner who is not "Ready for Placement" is never matched, every fact in an answer must come from a tool result, and a human approves every placement |
| Final presentation: no slide decks, a **live end-to-end demo**: curriculum query, secure grading, routing to a partner opening from live records | One command runs exactly that scenario; the recorded video is the same run. A short deck is still produced because the course rubric requires it |

## Scope
In: LangGraph orchestrator-worker system; curriculum Q&A (RAG over ChromaDB); ReAct advisor with
read-only SQL over the learner database, including partner openings; assessor with live execution and an
evaluator-optimizer loop (max 3 refinements); per-learner memory; human-in-the-loop approval of every final
placement. Out: production hosting, authentication, real employer integrations, fine-tuning.

## Success criteria
1. All multi-agent orchestration is LangGraph, with typed state and per-learner checkpointing.
2. Every function and tool has a docstring mapped to a curriculum module *(automated test)*.
3. ChromaDB retrieval completes within 1000 ms *(benchmark: max and p95)*.
4. Tool failures return a structured "Unable to verify" result *(fault-injection tests)*.
5. The refinement loop halts after at most 3 iterations *(test with a judge that never approves)*.
6. Every final placement decision pauses for human approval.
7. Learner code never leaves the sandbox *(escape-attempt suite; real Docker run in the demo environment)*.

## Risks
| Risk | Effect | Mitigation |
|---|---|---|
| ChromaDB and Docker are not available on the developer's PC (Application Control) | The benchmark, the strict sandbox and the live demo cannot run locally | Run the demo in GitHub Codespaces (Docker, no policy blocks); keep the retriever behind one interface |
| Free-tier LLM rate limits (429) | Slow or failed runs | Retries, small prompts, cached evaluation outputs |
| LLM judge is inconsistent | Unstable grades | Executable tests are the primary evidence; the judge only adds feedback; hard iteration cap |
| LLM invents a fact in its final text | Loss of trust | A verification step compares the numbers and names in the answer with the tool results before it is shown |
| The 80% target has no baseline | Claim cannot be proven | Ask Sprints for current turnaround times; report measured latency and the assumption separately |

## Assumptions (to confirm)
- The deck says 500 learners; the CSV has 25. The 25 are the source of truth.
- The deck's requirements are cut off at about 120 characters; they are read as the 10 items in `docs/CHANGELOG.md`.
- No curriculum documents or starter repository were provided: a synthetic 15-module curriculum and a
  synthetic `partner_openings` table (12 rows) are written and clearly labelled.
- Placement thresholds (12 modules, score 85) are inferred from the data; learner SPR-1022 meets them but is
  "In Progress", so the readiness status, not the thresholds alone, gates a match.
- The transcript (UniScribe) misspells names ("Fowry", "EN Egypt", "Swell"); they are read as Fawry, e& Egypt, Swvl.
