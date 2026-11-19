"""Run the stakeholder-facing end-to-end Sprints demo in one command.

Curriculum:
    Module 7 (Vector Stores and Retrieval-Augmented Generation): answer a curriculum question.
    Module 2 (Python Functions, Testing and Debugging): grade a Python submission in Docker.
    Module 14 (Human-in-the-Loop and Safety): pause a deterministic placement proposal for review.
"""
import json
from dotenv import load_dotenv
from src.agents.orchestrator import build_graph, learner_config
from src.main import ensure_curriculum_index

def _print_section(title, payload):
    """Print a named demo phase as readable JSON for a stakeholder walkthrough.

    Curriculum:
        Module 8 (Tools and Function Calling): present structured worker observations.
        Module 11 (LangGraph State Machines): make each orchestrated phase visible in sequence.
    """
    print("\n" + "=" * 76 + "\n" + title + "\n" + "=" * 76)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

def main():
    """Execute curriculum Q&A, sandbox grading and human-approved placement sequentially.

    Curriculum:
        Module 11 (LangGraph State Machines): one compiled graph coordinates all three workers.
        Module 12 (Memory and Checkpointing): each phase uses a learner-scoped thread identifier.
        Module 14 (Human-in-the-Loop and Safety): placement cannot finalize without reviewer input.
    """
    load_dotenv()
    ensure_curriculum_index()
    graph = build_graph()
    learner_id = "SPR-1001"  # sample learner in the supplied CSV; has matching partner openings
    config = learner_config(learner_id)

    advisor = graph.invoke({"learner_id": learner_id, "intent": "advisor",
                            "query": "Explain the LangGraph state machine module and RAG retrieval."}, config=config)
    _print_section("1/3 — CURRICULUM Q&A + LEARNER PROFILE", advisor.get("response", advisor))

    assessment = graph.invoke({"learner_id": learner_id, "intent": "assessment",
                               "code": "def add(a, b):\n    return a + b\n",
                               "tests": [{"function": "add", "args": [2, 3], "expected": 5},
                                         {"function": "add", "args": [-1, 1], "expected": 0}]}, config=config)
    _print_section("2/3 — SECURE PYTHON GRADING", assessment.get("response", assessment))

    placement = graph.invoke({"learner_id": learner_id, "intent": "placement"}, config=config)
    snapshot = graph.get_state(config)
    if snapshot and snapshot.next:
        _print_section("3/3 — HUMAN APPROVAL REQUIRED", snapshot.tasks[0].interrupts[0].value)
        approved = input("Approve this placement proposal? [y/N]: ").strip().lower() == "y"
        reviewer = input("Reviewer name: ").strip() or "Sprints reviewer"
        from langgraph.types import Command
        placement = graph.invoke(Command(resume={"approved": approved, "reviewer": reviewer}), config=config)
    _print_section("3/3 — FINAL REVIEW OUTCOME", placement.get("response", placement))

if __name__ == "__main__":
    main()
