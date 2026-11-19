"""Typed shared state and structured contracts for the Sprints agent graph.

Curriculum:
    Module 4 (Structured Outputs with Pydantic): validated request and response contracts.
    Module 11 (LangGraph State Machines): TypedDict state shared by graph workers.
    Module 12 (Memory and Checkpointing): learner-scoped state persisted by a checkpointer.
"""
from typing import Any, Dict, List, Literal, Optional, TypedDict
from pydantic import BaseModel, Field

class OrchestratorRequest(BaseModel):
    """Validate user intent and inputs before graph execution.

    Curriculum:
        Module 4 (Structured Outputs with Pydantic): typed fields prevent ambiguous payloads.
        Module 11 (LangGraph State Machines): request data becomes the initial graph state.
    """
    learner_id: str = Field(description="Sprints learner identifier, e.g. SPR-1001")
    query: str = Field(default="", description="Learner question or task description")
    intent: Literal["advisor", "assessment", "placement"] = Field(default="advisor")
    code: str = Field(default="", description="Python submission source code")
    tests: List[Dict[str, Any]] = Field(default_factory=list, description="Executable grading test cases")

class AgentState(TypedDict, total=False):
    """State passed between LangGraph workers for one learner session.

    Curriculum:
        Module 11 (LangGraph State Machines): typed graph state and explicit worker outputs.
        Module 12 (Memory and Checkpointing): checkpoint state is isolated by learner thread ID.
        Module 14 (Human-in-the-Loop and Safety): approval status is explicit and auditable.
    """
    learner_id: str
    query: str
    intent: str
    code: str
    tests: List[Dict[str, Any]]
    advisor_result: Dict[str, Any]
    assessment_result: Dict[str, Any]
    placement_proposal: Dict[str, Any]
    approval: Optional[Dict[str, Any]]
    response: Dict[str, Any]
    errors: List[str]

class AgentResponse(BaseModel):
    """Stable external response schema returned by the CLI and graph workers.

    Curriculum:
        Module 4 (Structured Outputs with Pydantic): machine-readable response validation.
        Module 8 (Tools and Function Calling): tool outcomes remain structured at boundaries.
    """
    status: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
