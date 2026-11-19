"""Assessment worker implementing executable grading and a bounded evaluator-optimizer loop.

Curriculum:
    Module 2 (Python Functions, Testing and Debugging): execute learner functions against tests.
    Module 13 (Evaluator-Optimizer and Reflection Patterns): cap refinement at three iterations.
    Module 14 (Human-in-the-Loop and Safety): execute untrusted submissions in a fail-closed sandbox.
"""
import os
from typing import Any, Callable, Dict, List
from src.tools.code_repl import grade_submission
from src.config import MAX_REFINEMENT_ITERATIONS
from src.fallback import is_unable_to_verify, unable_to_verify

def _llm_optimizer(code: str, result: Dict[str, Any]) -> str:
    """Ask the configured model for a revised submission using failed test evidence only.

    Curriculum:
        Module 3 (LLM APIs and Prompt Engineering): constrain a model request to a code revision.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): feedback drives bounded refinement.
        Module 14 (Human-in-the-Loop and Safety): the revised code is re-executed in the sandbox.
    """
    from langchain_openai import ChatOpenAI
    prompt = (
        "You are a Python assignment optimizer. Return ONLY the complete revised Python source code. "
        "Do not use file, network, process, or system access. Fix only issues supported by the test evidence.\n\n"
        f"CURRENT CODE:\n{code}\n\nEXECUTABLE TEST RESULTS:\n{result.get('results', [])}\n"
        f"LOAD ERROR: {result.get('load_error')}"
    )
    response = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0).invoke(prompt)
    content = response.content
    if isinstance(content, list):
        content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    revised = str(content).strip()
    if revised.startswith("```python"):
        revised = revised[len("```python"):]
    if revised.startswith("```"):
        revised = revised[3:]
    if revised.endswith("```"):
        revised = revised[:-3]
    revised = revised.strip()
    if not revised:
        raise ValueError("optimizer returned empty source")
    return revised

def evaluator_optimizer(code: str, tests: List[Dict[str, Any]], execute: Callable[..., Dict[str, Any]],
                        optimizer: Callable[[str, Dict[str, Any]], str], max_iterations: int = MAX_REFINEMENT_ITERATIONS) -> Dict[str, Any]:
    """Execute, evaluate and optionally refine code with a strict maximum of three refinements.

    Curriculum:
        Module 2 (Python Functions, Testing and Debugging): every candidate is checked by executable tests.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): the loop has a hard three-refinement cap.
        Module 14 (Human-in-the-Loop and Safety): every candidate re-enters the same sandbox boundary.

    The initial submission is executed once; up to ``max_iterations`` revisions may follow. Tool
    failures stop immediately and are never interpreted as learner failures or optimized around.
    """
    cap = min(max(int(max_iterations), 0), MAX_REFINEMENT_ITERATIONS)
    history = []
    current = code
    result: Dict[str, Any] = {}
    refinements = 0
    for attempt in range(cap + 1):
        result = execute(current, tests)
        history.append({"attempt": attempt, "result": result})
        if is_unable_to_verify(result) or result.get("all_passed") or attempt == cap:
            break
        try:
            current = optimizer(current, result)
            refinements += 1
        except Exception as exc:
            result["optimizer_status"] = "SKIPPED"
            result["optimizer_message"] = f"Unable to refine ({type(exc).__name__}); executable result retained."
            break
    return {"final_code": current, "result": result, "history": history,
            "refinement_iterations": refinements, "max_refinement_iterations": MAX_REFINEMENT_ITERATIONS}

def assessor_worker(state: Dict[str, Any]) -> Dict[str, Any]:
    """Grade code with deterministic tests and optional, strictly bounded model refinement.

    Curriculum:
        Module 2 (Python Functions, Testing and Debugging): tests are executable grading evidence.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): loop counter enforces hard cap.
        Module 14 (Human-in-the-Loop and Safety): sandbox errors never trigger unsafe execution fallback.
    """
    try:
        code, tests = state.get("code", ""), state.get("tests", [])
        if not code or not tests:
            result = unable_to_verify("assessor_worker", "submission code and test cases are required", "BAD_REQUEST")
        else:
            optimizer = _llm_optimizer if os.getenv("OPENAI_API_KEY") else (lambda _source, _evidence: (_ for _ in ()).throw(RuntimeError("OPENAI_API_KEY is not configured")))
            loop = evaluator_optimizer(code, tests, grade_submission, optimizer)
            result = dict(loop["result"])
            result.update({"refinement_iterations": loop["refinement_iterations"],
                           "max_refinement_iterations": loop["max_refinement_iterations"],
                           "evaluation_basis": "deterministic executable tests; LLM revisions cannot override test outcomes",
                           "refinement_history": [{"attempt": h["attempt"], "status": h["result"].get("status"),
                                                   "passed": h["result"].get("passed"), "total": h["result"].get("total")}
                                                  for h in loop["history"]]})
            if not os.getenv("OPENAI_API_KEY") and not is_unable_to_verify(result) and not result.get("all_passed"):
                result["optimizer_status"] = "DISABLED_NO_API_KEY"
        return {"assessment_result": result, "response": result}
    except Exception as exc:
        result = unable_to_verify("assessor_worker", f"grading failed ({type(exc).__name__})", "ASSESSOR_FAILURE")
        return {"assessment_result": result, "response": result}
