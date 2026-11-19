"""Tests for the assessor's strict evaluator-optimizer iteration cap."""
from src.agents.assessor_agent import evaluator_optimizer

def test_optimizer_stops_after_three_refinements():
    calls = {"execute": 0, "optimize": 0}
    def execute(_code, _tests):
        calls["execute"] += 1
        return {"status": "OK", "all_passed": False, "passed": 0, "total": 1}
    def optimize(code, _result):
        calls["optimize"] += 1
        return code + "\n# refined"
    result = evaluator_optimizer("def f(): return 0", [], execute, optimize, max_iterations=20)
    assert result["refinement_iterations"] == 3
    assert calls == {"execute": 4, "optimize": 3}

def test_optimizer_stops_immediately_when_tool_cannot_verify():
    calls = {"execute": 0, "optimize": 0}
    def execute(_code, _tests):
        calls["execute"] += 1
        return {"status": "UNABLE_TO_VERIFY", "message": "Unable to verify"}
    def optimize(code, _result):
        calls["optimize"] += 1
        return code
    result = evaluator_optimizer("def f(): return 0", [], execute, optimize)
    assert result["refinement_iterations"] == 0
    assert calls == {"execute": 1, "optimize": 0}
