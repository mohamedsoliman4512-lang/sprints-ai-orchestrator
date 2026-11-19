"""Python execution tool used by the assessor agent to grade learner submissions, strictly sandboxed.

Curriculum:
    Module 2 (Python Functions, Testing and Debugging): a submission is graded the way the
    bootcamp teaches testing: call the learner's function with inputs and compare the result with
    the expected value.
    Module 8 (Tools and Function Calling): exposed as the LangChain tool ``run_python_tests``.
    Module 14 (Human-in-the-Loop and Safety): learner code is untrusted and must never breach the
    platform, so it runs inside a sandbox chosen by ``SPRINTS_SANDBOX``.

Sandbox modes (default: ``docker``):
    ``docker``   The real boundary. A throw-away container with no network, a read-only file
                 system, a non-root user, all capabilities dropped, and memory, CPU and process
                 limits. If Docker is not available the tool FAILS CLOSED: it returns "Unable to
                 verify" and runs nothing. It never falls back to a weaker mode by itself.
    ``process``  Development only. A separate ``python -I`` process with a clean environment, a
                 temporary working directory, a time limit and (on Linux/macOS) memory and CPU
                 limits. It is NOT a security boundary. It must be chosen explicitly.
In both modes the code is also checked against an import allow-list and a blocklist of dangerous
names and attributes before it runs (defence in depth, not the boundary).

Failure contract: a learner's own bugs (wrong answers, exceptions in their code, an infinite loop,
exceeding the memory limit) are normal graded outcomes. A failure of the grader itself (sandbox
unavailable, no result returned, invalid test definition) returns the structured "Unable to
verify" fallback.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from src.fallback import unable_to_verify

DEFAULT_TIMEOUT_S = 5.0
MAX_CODE_CHARS = 20_000
MAX_TESTS = 50
RESULT_MARKER = "__SPRINTS_RESULT__"

SANDBOX_ENV = "SPRINTS_SANDBOX"
IMAGE_ENV = "SPRINTS_SANDBOX_IMAGE"
DEFAULT_IMAGE = "python:3.12-slim"
SANDBOX_MODES = ("docker", "process")
DOCKER_MEMORY = "128m"
DOCKER_CPUS = "0.5"
DOCKER_PIDS = "32"
DOCKER_STARTUP_GRACE_S = 10.0        # container start-up is not charged to the learner's time limit
PROCESS_MEMORY_BYTES = 256 * 1024 * 1024
RESOURCE_EXIT_CODES = {137}          # 137 = Docker OOM kill / SIGKILL
DOCKER_FAILURE_CODES = {125, 126, 127}   # docker itself could not run the container

# The grader runs in a fresh interpreter (``python -I``), fed through stdin. It is kept as a
# string so the parent process never imports or executes learner code itself.
_HARNESS = r'''
import ast, builtins, io, json, sys, types

ALLOWED = {"math", "statistics", "itertools", "functools", "collections", "string", "re", "json",
           "datetime", "decimal", "fractions", "typing", "dataclasses", "heapq", "bisect"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "input", "globals", "locals", "vars",
                   "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview"}
# frame / generator / traceback attributes lead to the interpreter's real globals
FORBIDDEN_ATTRS = {"gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame",
                   "ag_code", "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame",
                   "tb_next", "mro", "format_map"}
ALLOWED_DUNDER_ATTRS = {"__init__", "__name__"}
SAFE = ("abs", "all", "any", "ascii", "bin", "bool", "chr", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "hash", "hex", "int", "isinstance", "issubclass", "iter",
        "len", "list", "map", "max", "min", "next", "object", "oct", "ord", "pow", "property",
        "range", "repr", "reversed", "round", "set", "slice", "sorted", "staticmethod",
        "classmethod", "str", "sum", "super", "tuple", "zip", "Exception", "ValueError",
        "TypeError", "KeyError", "IndexError", "ZeroDivisionError", "StopIteration",
        "ArithmeticError", "AssertionError", "RuntimeError", "NotImplementedError",
        "AttributeError", "True", "False", "None")


def is_dunder(name):
    return name.startswith("__") and name.endswith("__")


def validate(code):
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED:
                    raise PermissionError("import of '%s' is not allowed" % alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "") not in ALLOWED:
                raise PermissionError("import from '%s' is not allowed" % node.module)
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS or (is_dunder(node.attr) and node.attr not in ALLOWED_DUNDER_ATTRS):
                raise PermissionError("access to attribute '%s' is not allowed" % node.attr)
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or (node.id.startswith("__") and node.id != "__name__"):
                raise PermissionError("use of name '%s' is not allowed" % node.id)
    return tree


def public_view(module):
    """Only the module's public, non-module names: 'typing.sys' or 'collections.abc' must not exist."""
    view = types.SimpleNamespace()
    for name in dir(module):
        if name.startswith("_"):
            continue
        value = getattr(module, name)
        if not isinstance(value, types.ModuleType):
            setattr(view, name, value)
    return view


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name not in ALLOWED:
        raise ImportError("import of '%s' is not allowed" % name)
    return public_view(builtins.__import__(name))


def norm(value):
    if isinstance(value, (tuple, list)):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): norm(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((norm(v) for v in value), key=repr)
    return value


def same(got, expected, tol):
    if isinstance(expected, bool) or isinstance(got, bool):
        return got is expected
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        return abs(got - expected) <= tol
    return got == expected


def jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def main():
    payload = json.loads(sys.stdin.read())
    out = io.StringIO()

    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        out.write(sep.join(str(a) for a in args) + end)

    safe = {name: getattr(builtins, name) for name in SAFE if hasattr(builtins, name)}
    safe.update({"print": _print, "__import__": safe_import, "__build_class__": builtins.__build_class__})
    namespace = {"__builtins__": safe, "__name__": "learner_submission"}
    result = {"load_error": None, "results": [], "stdout": ""}
    try:
        exec(compile(validate(payload["code"]), "<submission>", "exec"), namespace)
    except BaseException as exc:
        result["load_error"] = "%s: %s" % (type(exc).__name__, exc)
    else:
        for index, case in enumerate(payload["tests"]):
            entry = {"case": index, "function": case["function"], "args": jsonable(case.get("args", [])),
                     "expected": jsonable(case["expected"]), "got": None, "passed": False, "error": None}
            function = namespace.get(case["function"])
            if not callable(function):
                entry["error"] = "function '%s' is not defined" % case["function"]
            else:
                try:
                    got = norm(function(*case.get("args", []), **case.get("kwargs", {})))
                    entry["got"] = jsonable(got)
                    entry["passed"] = same(got, norm(case["expected"]), case.get("tolerance", 1e-9))
                except BaseException as exc:
                    entry["error"] = "%s: %s" % (type(exc).__name__, exc)
            result["results"].append(entry)
    result["stdout"] = out.getvalue()[:2000]
    sys.stdout.write("\n__SPRINTS_RESULT__" + json.dumps(result) + "\n")


main()
'''


# ---------------------------------------------------------------- request validation
def _validate_request(code: Any, tests: Any) -> None:
    """Raise ValueError if the grading request itself is malformed (a caller bug, not learner code)."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"code is longer than {MAX_CODE_CHARS} characters")
    if not isinstance(tests, list) or not tests:
        raise ValueError("tests must be a non-empty list")
    if len(tests) > MAX_TESTS:
        raise ValueError(f"at most {MAX_TESTS} tests are allowed")
    for i, case in enumerate(tests):
        if not isinstance(case, dict) or "function" not in case or "expected" not in case:
            raise ValueError(f"test {i} must be an object with 'function' and 'expected'")
        if not isinstance(case.get("args", []), list) or not isinstance(case.get("kwargs", {}), dict):
            raise ValueError(f"test {i}: 'args' must be a list and 'kwargs' an object")
    json.dumps(tests)  # raises TypeError/ValueError if a test is not JSON-serialisable


# ---------------------------------------------------------------- sandbox backends
def configured_sandbox() -> str:
    """Sandbox mode from the environment; the secure default is ``docker``.

    Curriculum:
        Module 14 (Human-in-the-Loop and Safety): the safe choice is the default; the weaker
        development mode must be requested explicitly with ``SPRINTS_SANDBOX=process``.
    """
    return os.environ.get(SANDBOX_ENV, "docker").strip().lower()


def build_docker_command(container_name: str, image: Optional[str] = None) -> List[str]:
    """Return the ``docker run`` command (without the program) that isolates learner code.

    Curriculum:
        Module 14 (Human-in-the-Loop and Safety): every flag removes a capability the learner's
        code could abuse: no network, no writable file system, no root, no extra privileges,
        and hard limits on memory, CPU and number of processes.

    Args:
        container_name: Unique name, so a timed-out container can be removed.
        image: Image to run; defaults to ``SPRINTS_SANDBOX_IMAGE`` or ``python:3.12-slim``.
    """
    return ["docker", "run", "--rm", "-i", "--name", container_name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--memory", DOCKER_MEMORY, "--memory-swap", DOCKER_MEMORY,
            "--cpus", DOCKER_CPUS, "--pids-limit", DOCKER_PIDS,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            image or os.environ.get(IMAGE_ENV, DEFAULT_IMAGE)]


def docker_available(timeout_s: float = 8.0) -> bool:
    """True if a Docker daemon answers; used to fail closed when it does not.

    Curriculum:
        Module 14 (Human-in-the-Loop and Safety): the check that stops the system from running
        learner code in a weaker environment than the one the client approved.
    """
    try:
        done = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _remove_container(name: str) -> None:
    """Best-effort ``docker rm -f``: killing the docker client does not stop the container."""
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_in_docker(payload: str, timeout_s: float) -> "subprocess.CompletedProcess[str]":
    name = f"sprints-grader-{uuid.uuid4().hex[:12]}"
    command = build_docker_command(name) + ["python", "-I", "-c", _HARNESS]
    try:
        return subprocess.run(command, input=payload, capture_output=True, text=True,
                              timeout=timeout_s + DOCKER_STARTUP_GRACE_S, check=False)
    except subprocess.TimeoutExpired:
        _remove_container(name)
        raise


def _process_limits(cpu_seconds: int):
    """preexec function (POSIX): memory, CPU, open-file and file-size limits for the child."""
    def _apply() -> None:
        import resource
        for limit, value in ((resource.RLIMIT_AS, PROCESS_MEMORY_BYTES), (resource.RLIMIT_CPU, cpu_seconds),
                             (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_FSIZE, 1_000_000)):
            resource.setrlimit(limit, (value, value))
    return _apply


def _run_in_process(payload: str, timeout_s: float) -> "subprocess.CompletedProcess[str]":
    env = {"PYTHONIOENCODING": "utf-8"}
    if os.environ.get("SYSTEMROOT"):                      # Windows needs it to start Python
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    kwargs: Dict[str, Any] = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _process_limits(int(timeout_s) + 2)
    with tempfile.TemporaryDirectory(prefix="sprints_grader_") as workdir:
        return subprocess.run([sys.executable, "-I", "-c", _HARNESS], input=payload, capture_output=True,
                              text=True, timeout=timeout_s, cwd=workdir, env=env, check=False, **kwargs)


def _outcome(tests: List[Dict[str, Any]], mode: str, **fields: Any) -> Dict[str, Any]:
    """A graded (non-failure) result; ``fields`` override the defaults."""
    base = {"status": "OK", "passed": 0, "total": len(tests), "all_passed": False, "timed_out": False,
            "resource_limit_exceeded": False, "load_error": None, "results": [], "stdout": "", "sandbox": mode}
    base.update(fields)
    return base


def grade_submission(code: str, tests: List[Dict[str, Any]], timeout_s: float = DEFAULT_TIMEOUT_S,
                     sandbox: Optional[str] = None) -> Dict[str, Any]:
    """Run a learner's Python code against test cases inside the configured sandbox.

    Curriculum:
        Module 2 (Python Functions, Testing and Debugging): function-level tests with expected
        values, the same style as the bootcamp's Python assignments.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): the objective, executable
        evidence the evaluator uses before an LLM judge comments on style and feedback.
        Module 14 (Human-in-the-Loop and Safety): untrusted code, strict sandbox, fail closed.

    Args:
        code: The learner's source code (defines the functions under test).
        tests: List of ``{"function": "add", "args": [2, 3], "kwargs": {}, "expected": 5}``;
            floats may add ``"tolerance"``.
        timeout_s: Wall-clock limit for the learner's code.
        sandbox: ``"docker"`` (default, strict) or ``"process"`` (development only); ``None``
            reads ``SPRINTS_SANDBOX``.

    Returns:
        ``{"status": "OK", "passed": n, "total": m, "all_passed": bool, "timed_out": bool,
        "resource_limit_exceeded": bool, "load_error": str | None, "results": [...],
        "stdout": str, "sandbox": mode}``. A learner's bug is reported inside this result. If the
        grader itself fails the result is the "Unable to verify" fallback (reason codes
        ``BAD_REQUEST``, ``SANDBOX_UNAVAILABLE`` and ``REPL_FAILURE``).
    """
    mode = (sandbox or configured_sandbox()).strip().lower()
    if mode not in SANDBOX_MODES:
        return unable_to_verify("run_python_tests", f"unknown sandbox mode {mode!r} (use docker or process)", "BAD_REQUEST")
    try:
        _validate_request(code, tests)
    except (ValueError, TypeError) as exc:
        return unable_to_verify("run_python_tests", f"the grading request was invalid ({exc})", "BAD_REQUEST")
    if mode == "docker" and not docker_available():
        return unable_to_verify("run_python_tests", "the Docker sandbox is not available, so the code was NOT run",
                                "SANDBOX_UNAVAILABLE", sandbox=mode)
    payload = json.dumps({"code": code, "tests": tests})
    started = time.monotonic()
    try:
        process = (_run_in_docker if mode == "docker" else _run_in_process)(payload, timeout_s)
    except subprocess.TimeoutExpired:
        return _outcome(tests, mode, timed_out=True,
                        message=f"The submission did not finish within {timeout_s:g} seconds (possible infinite loop).")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return unable_to_verify("run_python_tests", f"the sandbox process could not be started ({type(exc).__name__}: {exc})",
                                "REPL_FAILURE", sandbox=mode)
    marker_at = process.stdout.rfind(RESULT_MARKER)
    if marker_at == -1:
        if mode == "docker" and process.returncode in DOCKER_FAILURE_CODES:
            return unable_to_verify("run_python_tests",
                                    f"Docker could not run the sandbox container (exit code {process.returncode}: "
                                    f"{(process.stderr or '').strip()[-200:]})", "SANDBOX_UNAVAILABLE", sandbox=mode)
        if process.returncode in RESOURCE_EXIT_CODES or process.returncode < 0:
            return _outcome(tests, mode, resource_limit_exceeded=True,
                            message="The submission was stopped for exceeding a resource limit (memory or CPU).")
        tail = (process.stderr or process.stdout or "no output").strip()[-300:]
        return unable_to_verify("run_python_tests", f"the grader returned no result (exit code {process.returncode}: {tail})",
                                "REPL_FAILURE", sandbox=mode)
    try:
        report = json.loads(process.stdout[marker_at + len(RESULT_MARKER):])
    except json.JSONDecodeError as exc:
        return unable_to_verify("run_python_tests", f"the grader result could not be parsed ({exc})", "REPL_FAILURE",
                                sandbox=mode)
    passed = sum(1 for r in report["results"] if r["passed"])
    result = _outcome(tests, mode, passed=passed, all_passed=report["load_error"] is None and passed == len(tests),
                      load_error=report["load_error"], results=report["results"], stdout=report["stdout"],
                      elapsed_s=round(time.monotonic() - started, 3))
    if mode == "process":
        result["sandbox_warning"] = "development mode: this is not a strict sandbox"
    return result


@tool
def run_python_tests(code: str, tests_json: str) -> str:
    """Execute a learner's Python code against test cases in a strict sandbox and return JSON.

    Curriculum:
        Module 2 (Python Functions, Testing and Debugging): grades function-level assignments.
        Module 8 (Tools and Function Calling): the assessor agent's live-execution tool.
        Module 14 (Human-in-the-Loop and Safety): runs untrusted code in an isolated container
        with no network and hard resource limits; fails closed if the sandbox is unavailable.

    Args:
        code: The learner's Python source (it defines the functions to test).
        tests_json: JSON list such as
            [{"function": "add", "args": [2, 3], "expected": 5}] (floats may add "tolerance").

    Returns JSON with passed, total, all_passed, timed_out, resource_limit_exceeded, load_error,
    per-test results and the sandbox used. If the grader or sandbox fails, the JSON has status
    UNABLE_TO_VERIFY and a message starting "Unable to verify"; no code was run in that case.
    """
    try:
        tests = json.loads(tests_json)
    except json.JSONDecodeError as exc:
        return json.dumps(unable_to_verify("run_python_tests", f"tests_json is not valid JSON ({exc})", "BAD_REQUEST"))
    return json.dumps(grade_submission(code, tests), ensure_ascii=False)
