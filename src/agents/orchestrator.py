"""LangGraph orchestrator with learner-scoped checkpoints and placement HITL interrupt.

Curriculum:
    Module 11 (LangGraph State Machines): graph routing between advisor, assessor and placement.
    Module 12 (Memory and Checkpointing): MemorySaver checkpoint keyed by learner thread ID.
    Module 14 (Human-in-the-Loop and Safety): every placement proposal pauses for human approval.
"""
from typing import Any, Dict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from src.state import AgentState
from src.agents.advisor_agent import advisor_worker
from src.agents.assessor_agent import assessor_worker
from src.tools.db_queries import find_matching_openings
from src.fallback import is_unable_to_verify, unable_to_verify

def route_request(state: AgentState) -> str:
    """Route an incoming request to exactly one worker based on validated intent.

    Curriculum:
        Module 11 (LangGraph State Machines): conditional edges select the relevant worker.
        Module 8 (Tools and Function Calling): avoids unnecessary calls to unrelated tools.
    """
    intent = state.get("intent", "advisor")
    return "assessor" if intent == "assessment" else ("placement" if intent == "placement" else "advisor")

def placement_worker(state: AgentState) -> Dict[str, Any]:
    """Generate deterministic opening matches, then pause for an explicit human decision.

    Curriculum:
        Module 15 (Capstone and Career Readiness): matches are computed from live learner/opening rows.
        Module 14 (Human-in-the-Loop and Safety): interrupt prevents autonomous final placement.
    """
    proposal = find_matching_openings(state.get("learner_id", ""))
    if is_unable_to_verify(proposal) or proposal.get("status") != "OK" or not proposal.get("matches"):
        return {"placement_proposal": proposal, "response": proposal}
    decision = interrupt({"type": "placement_approval_required", "learner_id": state.get("learner_id"),
                          "proposal": proposal, "instruction": "Resume with {'approved': true|false, 'reviewer': 'name'}"})
    approval = {"approved": bool(decision.get("approved")), "reviewer": decision.get("reviewer", "unspecified")}
    final = {"status": "APPROVED" if approval["approved"] else "REJECTED",
             "message": "Human reviewer approved the placement proposal." if approval["approved"] else "Human reviewer rejected the placement proposal.",
             "proposal": proposal, "approval": approval}
    return {"placement_proposal": proposal, "approval": approval, "response": final}

def build_graph(checkpointer: Any = None):
    """Compile the single LangGraph state machine with a per-session checkpointer.

    Curriculum:
        Module 11 (LangGraph State Machines): StateGraph is the sole multi-agent orchestrator.
        Module 12 (Memory and Checkpointing): checkpointer stores each learner thread separately.
        Module 14 (Human-in-the-Loop and Safety): placement node can suspend execution for approval.
    """
    graph = StateGraph(AgentState)
    graph.add_node("advisor", advisor_worker)
    graph.add_node("assessor", assessor_worker)
    graph.add_node("placement", placement_worker)
    graph.add_conditional_edges(START, route_request, {"advisor": "advisor", "assessor": "assessor", "placement": "placement"})
    graph.add_edge("advisor", END)
    graph.add_edge("assessor", END)
    graph.add_edge("placement", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def learner_config(learner_id: str) -> Dict[str, Any]:
    """Create a stable checkpoint namespace so learners cannot share conversational state.

    Curriculum:
        Module 12 (Memory and Checkpointing): thread IDs scope graph memory to one learner.
        Module 14 (Human-in-the-Loop and Safety): the identifier is not derived from user text.
    """
    return {"configurable": {"thread_id": f"sprints-learner-{learner_id}"}}


def run_request(graph: Any, state: AgentState, config: Dict[str, Any]):
    """Invoke or resume the graph using the supplied learner-specific checkpoint configuration.

    Curriculum:
        Module 11 (LangGraph State Machines): graph invocation executes the selected worker.
        Module 12 (Memory and Checkpointing): config retains the learner's isolated thread state.
    """
    from langgraph.types import Command
    if state.get("_resume") is not None:
        return graph.invoke(Command(resume=state["_resume"]), config=config)
    clean = {k: v for k, v in state.items() if not k.startswith("_")}
    return graph.invoke(clean, config=config)
