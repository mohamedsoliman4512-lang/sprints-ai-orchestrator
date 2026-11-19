"""Tests for graph routing, per-learner checkpoint keys and bounded refinement contracts."""
from src.agents.orchestrator import learner_config, route_request
from src.agents.assessor_agent import evaluator_optimizer

def test_graph_routes_each_intent_to_one_worker():
    assert route_request({"intent": "assessment"}) == "assessor"
    assert route_request({"intent": "placement"}) == "placement"
    assert route_request({"intent": "advisor"}) == "advisor"

def test_checkpoint_namespace_is_learner_scoped():
    assert learner_config("SPR-1001") != learner_config("SPR-1002")

def test_evaluator_optimizer_never_exceeds_three_refinements():
    calls = {"execute": 0, "optimize": 0}
    def execute(_code, _tests):
        calls["execute"] += 1
        return {"status": "OK", "all_passed": False, "passed": 0, "total": 1}
    def optimize(code, _result):
        calls["optimize"] += 1
        return code + "\n# revision"
    result = evaluator_optimizer("def f(): return 0", [], execute, optimize, max_iterations=99)
    assert result["refinement_iterations"] == 3
    assert calls == {"execute": 4, "optimize": 3}
