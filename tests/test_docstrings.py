"""Acceptance criterion: every function, class and module in src/ has a detailed docstring that maps
to the Sprints curriculum ("Curriculum:" section naming a real module number)."""

import ast
import re
from pathlib import Path

from src.config import CURRICULUM_MODULES

SRC = Path(__file__).resolve().parents[1] / "src"
FILES = [p for p in sorted(SRC.rglob("*.py")) if p.read_text(encoding="utf-8").strip()]
MODULE_REF = re.compile(r"Module (\d{1,2}) \(([^)]+)\)")


def _check(doc, where):
    assert doc and "Curriculum:" in doc, f"{where}: docstring lacks a 'Curriculum:' section"
    refs = MODULE_REF.findall(doc)
    assert refs, f"{where}: no 'Module N (Title)' reference"
    for number, title in refs:
        assert int(number) in CURRICULUM_MODULES, f"{where}: Module {number} does not exist"
        assert title == CURRICULUM_MODULES[int(number)], f"{where}: Module {number} is '{CURRICULUM_MODULES[int(number)]}', not '{title}'"
    assert len(doc.split()) >= 25, f"{where}: docstring is too short to be 'detailed'"


def test_there_are_source_files_to_check() -> None:
    assert len(FILES) >= 3


def test_every_module_has_a_curriculum_docstring() -> None:
    for path in FILES:
        _check(ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))), str(path.relative_to(SRC.parent)))


def test_every_public_function_and_class_has_a_curriculum_docstring() -> None:
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                _check(ast.get_docstring(node), f"{path.relative_to(SRC.parent)}:{node.name}")
