"""Tests for the sandboxed grading tool.

Process-mode tests run everywhere (they exercise the harness, the hardening and the result handling).
Docker-mode tests that need a real daemon are skipped when Docker is not available; the Docker command
and the fail-closed behaviour are tested with mocks and always run.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.fallback import is_unable_to_verify
from src.tools import code_repl as cr

GOOD = "def add(a, b):\n    return a + b\n"
TESTS = [{"function": "add", "args": [2, 3], "expected": 5},
         {"function": "add", "args": [-1, 1], "expected": 0}]


def grade(code, tests, **kw):
    """Development sandbox, explicitly requested (the default is the strict Docker sandbox)."""
    return cr.grade_submission(code, tests, sandbox="process", **kw)


# ------------------------------------------------------------------ learner results (process mode)
def test_correct_submission_passes_all_tests() -> None:
    result = grade(GOOD, TESTS)
    assert result["status"] == "OK" and result["all_passed"] and (result["passed"], result["total"]) == (2, 2)
    assert result["sandbox"] == "process" and "not a strict sandbox" in result["sandbox_warning"]


def test_wrong_answer_is_a_graded_outcome_with_the_value_received() -> None:
    result = grade("def add(a, b):\n    return a + b + (1 if a > 0 else 0)\n", TESTS)
    assert result["status"] == "OK" and not result["all_passed"] and result["passed"] == 1
    assert result["results"][0]["got"] == 6 and result["results"][0]["expected"] == 5


def test_exceptions_in_learner_code_are_recorded_not_raised() -> None:
    result = grade("def add(a, b):\n    return a / 0\n", TESTS)
    assert result["status"] == "OK" and "ZeroDivisionError" in result["results"][0]["error"]


def test_syntax_error_is_a_load_error() -> None:
    result = grade("def add(a, b)\n    return a + b\n", TESTS)
    assert result["status"] == "OK" and "SyntaxError" in result["load_error"] and not result["all_passed"]


def test_infinite_loop_times_out_as_a_graded_outcome() -> None:
    result = grade("def add(a, b):\n    while True:\n        pass\n", TESTS, timeout_s=1.0)
    assert result["status"] == "OK" and result["timed_out"] and not result["all_passed"]
    assert "did not finish" in result["message"]


def test_memory_bomb_is_stopped_and_graded_not_a_system_failure() -> None:
    result = grade("def add(a, b):\n    return len([0] * (10 ** 10))\n", TESTS)
    assert result["status"] == "OK" and not result["all_passed"]
    outcome = " ".join(str(r["error"]) for r in result["results"]) + str(result.get("message"))
    assert "MemoryError" in outcome or result["resource_limit_exceeded"]


def test_function_stdout_classes_tolerance_normalisation_and_oop_idioms() -> None:
    assert "not defined" in grade(GOOD, [{"function": "sub", "args": [1], "expected": 1}])["results"][0]["error"]
    assert grade("print('hello')\n" + GOOD, TESTS)["stdout"].strip() == "hello"
    code = ("import math\nfrom collections import Counter\n"
            "class Shape:\n    def __init__(self, n):\n        self.__n = n\n    def n(self):\n        return self.__n\n"
            "class Circle(Shape):\n    def __init__(self, r):\n        super().__init__(r)\n        self._r = r\n"
            "    def area(self):\n        return math.pi * self._r ** 2\n"
            "def area(r):\n    return Circle(r).area()\ndef pair(a, b):\n    return (a, b)\n"
            "def uniq(xs):\n    return set(xs)\ndef top(xs):\n    return Counter(xs).most_common(1)[0][0]\n"
            "def fmt(x):\n    return '{:.2f}|{}'.format(x, x)\n"
            "if __name__ == '__main__':\n    print('not executed by the grader')\n")
    tests = [{"function": "area", "args": [2], "expected": 12.566370614359172, "tolerance": 1e-6},
             {"function": "pair", "args": [1, 2], "expected": [1, 2]},
             {"function": "uniq", "args": [[3, 1, 3]], "expected": [1, 3]},
             {"function": "top", "args": [["a", "b", "a"]], "expected": "a"},
             {"function": "fmt", "args": [1.5], "expected": "1.50|1.5"}]
    result = grade(code, tests)
    assert result["load_error"] is None and result["all_passed"], result


# ------------------------------------------------------------------ the learner cannot breach the platform
SECRET = "TOP-SECRET-PLATFORM-KEY-12345"
ATTACKS = {
    "import os": "import os\ndef f():\n    return os.getcwd()",
    "import subprocess": "import subprocess\ndef f():\n    return subprocess.run(['id'])",
    "import socket": "import socket\ndef f():\n    return 1",
    "import sys": "import sys\ndef f():\n    return sys.modules",
    "import builtins": "import builtins\ndef f():\n    return 1",
    "import importlib": "import importlib\ndef f():\n    return 1",
    "import ctypes": "import ctypes\ndef f():\n    return 1",
    "import pickle": "import pickle\ndef f():\n    return 1",
    "import multiprocessing": "import multiprocessing\ndef f():\n    return 1",
    "from os import system": "from os import system\ndef f():\n    return 1",
    "dotted import": "import collections.abc\ndef f():\n    return 1",
    "operator.attrgetter": "import operator\ndef f():\n    return operator.attrgetter('__class__')(1)",
    "open()": "def f():\n    return open('/etc/passwd').read()",
    "eval": "def f():\n    return eval('1+1')",
    "exec": "def f():\n    exec('x = 1')\n    return 1",
    "compile": "def f():\n    return compile('1', 'x', 'eval')",
    "__import__": "def f():\n    return __import__('os')",
    "getattr": "def f():\n    return getattr(1, 'real')",
    "globals": "def f():\n    return globals()",
    "vars": "def f():\n    return vars()",
    "__builtins__": "def f():\n    return __builtins__",
    "dunder walk": "def f():\n    return ().__class__.__bases__[0].__subclasses__()",
    "function globals": "def f():\n    return f.__globals__",
    "generator frame": "def f():\n    g = (i for i in [1])\n    return g.gi_frame.f_back.f_globals",
    "frame via traceback": "def f():\n    try:\n        1 / 0\n    except Exception as e:\n        return e.__traceback__.tb_frame",
    "mro": "def f():\n    return int.mro()",
    "format_map": "def f():\n    return '{a}'.format_map({'a': 1})",
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attack_is_blocked_before_it_runs(name) -> None:
    result = grade(ATTACKS[name], [{"function": "f", "args": [], "expected": None}])
    assert result["status"] == "OK" and not result["all_passed"], name
    assert result["load_error"] and "not allowed" in result["load_error"], (name, result["load_error"])


def test_standard_library_modules_do_not_expose_the_modules_they_import() -> None:
    """typing.sys, json.codecs, re.enum ... would hand the learner real interpreter internals."""
    code = ("import typing\nimport json\nimport re\nimport collections\n"
            "def probe(which):\n    try:\n        if which == 'typing.sys':\n            typing.sys\n"
            "        elif which == 'typing.collections':\n            typing.collections\n"
            "        elif which == 'json.codecs':\n            json.codecs\n"
            "        elif which == 're.enum':\n            re.enum\n"
            "        elif which == 'collections.abc':\n            collections.abc\n"
            "    except AttributeError:\n        return 'hidden'\n    return 'EXPOSED'\n"
            "def legit():\n    return [typing.List is not None, json.dumps([1]), bool(re.compile('a')), "
            "collections.Counter('aab')['a']]\n")
    tests = [{"function": "probe", "args": [w], "expected": "hidden"}
             for w in ("typing.sys", "typing.collections", "json.codecs", "re.enum", "collections.abc")]
    tests.append({"function": "legit", "args": [], "expected": [True, "[1]", True, 2]})
    result = grade(code, tests)
    assert result["load_error"] is None and result["all_passed"], result


def test_learner_code_cannot_read_host_files_or_leak_a_secret() -> None:
    with tempfile.TemporaryDirectory() as folder:
        secret_file = Path(folder) / "secret.txt"
        secret_file.write_text(SECRET, encoding="utf-8")
        code = f"def f():\n    return open({str(secret_file)!r}).read()\n"
        result = grade(code, [{"function": "f", "args": [], "expected": SECRET}])
    assert not result["all_passed"] and SECRET not in json.dumps(result)


def test_the_child_process_gets_a_clean_environment_and_an_empty_working_directory() -> None:
    with mock.patch.dict("os.environ", {"GROQ_API_KEY": SECRET, "OPENAI_API_KEY": SECRET}):
        seen = {}
        real_run = subprocess.run

        def spy(*args, **kwargs):
            seen["env"], seen["cwd"] = kwargs.get("env"), kwargs.get("cwd")
            return real_run(*args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=spy):
            result = grade(GOOD, TESTS)
    assert result["all_passed"] and SECRET not in json.dumps(seen["env"]) and "API_KEY" not in json.dumps(seen["env"])
    assert seen["cwd"] and "sprints_grader_" in seen["cwd"]


def test_output_flooding_is_capped() -> None:
    result = grade("def f():\n    print('x' * 10000000)\n    return 1\n", [{"function": "f", "args": [], "expected": 1}])
    assert result["status"] == "OK" and len(result["stdout"]) <= 2000


# ------------------------------------------------------------------ Docker sandbox: command and fail-closed behaviour
def test_docker_is_the_default_sandbox_and_unknown_modes_are_rejected() -> None:
    with mock.patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop(cr.SANDBOX_ENV, None)
        assert cr.configured_sandbox() == "docker"
    bad = cr.grade_submission(GOOD, TESTS, sandbox="none")
    assert is_unable_to_verify(bad) and bad["reason_code"] == "BAD_REQUEST"


def test_docker_command_applies_every_isolation_flag() -> None:
    command = cr.build_docker_command("sprints-grader-test", image="python:3.12-slim")
    joined = " ".join(command)
    for expected in ("--rm", "--network none", "--read-only", "--cap-drop ALL", "no-new-privileges",
                     "--pids-limit 32", "--memory 128m", "--memory-swap 128m", "--cpus 0.5", "--user 65534:65534",
                     "--name sprints-grader-test"):
        assert expected in joined, expected
    assert "noexec" in joined and command[-1] == "python:3.12-slim"
    assert "-v " not in joined and "--privileged" not in joined and "--volume" not in joined  # no mounts, no privilege


def test_docker_mode_fails_closed_when_docker_is_missing_and_runs_nothing() -> None:
    with mock.patch.object(cr, "docker_available", return_value=False), \
            mock.patch.object(cr, "_run_in_process") as weaker, mock.patch.object(cr, "_run_in_docker") as docker:
        result = cr.grade_submission(GOOD, TESTS, sandbox="docker")
    assert is_unable_to_verify(result) and result["reason_code"] == "SANDBOX_UNAVAILABLE"
    assert "NOT run" in result["message"] and result["message"].startswith("Unable to verify")
    weaker.assert_not_called()       # never falls back to the weaker mode by itself
    docker.assert_not_called()


def test_docker_timeout_removes_the_container() -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(cr, "docker_available", return_value=True), mock.patch.object(subprocess, "run", side_effect=fake_run):
        result = cr.grade_submission(GOOD, TESTS, timeout_s=1.0, sandbox="docker")
    assert result["status"] == "OK" and result["timed_out"] and result["sandbox"] == "docker"
    removal = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
    assert len(removal) == 1 and removal[0][3].startswith("sprints-grader-")


def test_docker_exit_codes_are_classified() -> None:
    def returning(code, out="", err=""):
        return mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], code, out, err))

    with mock.patch.object(cr, "docker_available", return_value=True):
        with returning(137):
            oom = cr.grade_submission(GOOD, TESTS, sandbox="docker")
        with returning(125, err="Unable to find image"):
            missing = cr.grade_submission(GOOD, TESTS, sandbox="docker")
    assert oom["status"] == "OK" and oom["resource_limit_exceeded"] and not oom["all_passed"]
    assert is_unable_to_verify(missing) and missing["reason_code"] == "SANDBOX_UNAVAILABLE"


@pytest.mark.skipif(not cr.docker_available(), reason="no Docker daemon in this environment")
def test_real_docker_sandbox_grades_and_contains_a_resource_bomb() -> None:
    ok = cr.grade_submission(GOOD, TESTS, sandbox="docker", timeout_s=20)
    assert ok["status"] == "OK" and ok["all_passed"] and ok["sandbox"] == "docker"
    bomb = cr.grade_submission("def f():\n    return len([0] * (10 ** 10))\n",
                               [{"function": "f", "args": [], "expected": 1}], sandbox="docker", timeout_s=20)
    assert bomb["status"] == "OK" and not bomb["all_passed"]


# ------------------------------------------------------------------ grader failures -> Unable to verify
@pytest.mark.parametrize("code,tests", [
    ("", TESTS), ("   ", TESTS), (GOOD, []), (GOOD, "not a list"), (GOOD, [{"function": "add"}]),
    (GOOD, [{"function": "add", "expected": 1, "args": "x"}]), (GOOD, [{"function": "add", "expected": {1, 2}}]),
    (GOOD, [{"function": "add", "expected": 1}] * 51), ("x" * 20_001, TESTS), (None, TESTS),
])
def test_malformed_requests_return_unable_to_verify(code, tests) -> None:
    result = grade(code, tests)
    assert is_unable_to_verify(result) and result["reason_code"] == "BAD_REQUEST"
    assert result["message"].startswith("Unable to verify")


def test_grader_process_failures_return_unable_to_verify() -> None:
    with mock.patch.object(subprocess, "run", side_effect=OSError("cannot spawn")):
        result = grade(GOOD, TESTS)
    assert is_unable_to_verify(result) and result["reason_code"] == "REPL_FAILURE" and "cannot spawn" in result["message"]
    crashed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Fatal Python error")
    with mock.patch.object(subprocess, "run", return_value=crashed):
        result = grade(GOOD, TESTS)
    assert is_unable_to_verify(result) and "no result" in result["message"]
    garbage = subprocess.CompletedProcess(args=[], returncode=0, stdout=cr.RESULT_MARKER + "{not json", stderr="")
    with mock.patch.object(subprocess, "run", return_value=garbage):
        assert is_unable_to_verify(grade(GOOD, TESTS))


def test_the_tool_wrapper_returns_json_and_never_raises() -> None:
    with mock.patch.dict("os.environ", {cr.SANDBOX_ENV: "process"}):
        ok = json.loads(cr.run_python_tests.invoke({"code": GOOD, "tests_json": json.dumps(TESTS)}))
    assert ok["status"] == "OK" and ok["all_passed"]
    bad = json.loads(cr.run_python_tests.invoke({"code": GOOD, "tests_json": "{oops"}))
    assert bad["status"] == "UNABLE_TO_VERIFY" and bad["message"].startswith("Unable to verify")
    assert "Curriculum" in cr.run_python_tests.description and "sandbox" in cr.run_python_tests.description
