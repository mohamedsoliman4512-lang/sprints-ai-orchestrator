"""The structured "Unable to verify" result shared by every tool.

Curriculum:
    Module 8 (Tools and Function Calling): a tool must never raise into the agent loop; it
    returns a machine-readable result the agent can reason about.
    Module 14 (Human-in-the-Loop and Safety): when a fact cannot be verified, the system says so
    instead of guessing, and the decision goes to a human.
"""

from typing import Any, Dict

from src.config import UNABLE_TO_VERIFY_PREFIX

UNABLE_STATUS = "UNABLE_TO_VERIFY"


def unable_to_verify(tool: str, reason: str, reason_code: str = "TOOL_ERROR", **extra: Any) -> Dict[str, Any]:
    """Build the structured fallback returned when a tool fails or a fact cannot be checked.

    Curriculum:
        Module 8 (Tools and Function Calling): the standard failure result of every tool.
        Module 14 (Human-in-the-Loop and Safety): the signal that routes a request to a human.

    Args:
        tool: Name of the tool that failed (for the audit trail).
        reason: Short human-readable cause; it is placed after "Unable to verify:".
        reason_code: Machine-readable cause, for example DB_ERROR, QUERY_REJECTED, REPL_FAILURE.
        **extra: Additional JSON-serialisable context.

    Returns:
        ``{"status": "UNABLE_TO_VERIFY", "message": "Unable to verify: <reason>", "tool": ...,
        "reason_code": ..., ...}``
    """
    result: Dict[str, Any] = {
        "status": UNABLE_STATUS,
        "message": f"{UNABLE_TO_VERIFY_PREFIX}: {reason}",
        "tool": tool,
        "reason_code": reason_code,
    }
    result.update(extra)
    return result


def is_unable_to_verify(result: Any) -> bool:
    """True when ``result`` is an "Unable to verify" fallback (used by agents and tests).

    Curriculum:
        Module 9 (ReAct Agents): the agent checks every observation before it trusts it.
    """
    return isinstance(result, dict) and result.get("status") == UNABLE_STATUS
