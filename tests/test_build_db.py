"""The database builder must refuse bad data, so a corrupt record never reaches an agent."""

import csv
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_db  # noqa: E402

from src.config import CSV_PATH, OPENINGS_CSV_PATH  # noqa: E402


def _write(folder, name, header, rows):
    path = Path(folder) / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_the_real_data_builds_into_a_valid_database() -> None:
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "t.db"
        assert build_db.build(CSV_PATH, db, OPENINGS_CSV_PATH) == 25
        conn = sqlite3.connect(db)
        try:                                   # Windows cannot delete a database that is still open
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT COUNT(*) FROM partner_openings").fetchone()[0] == 12
            assert conn.execute("SELECT MIN(position) FROM learner_employers").fetchone()[0] == 1
        finally:
            conn.close()


@pytest.mark.parametrize("mutation,message", [
    (lambda r: r.__setitem__("readiness_status", "Maybe"), "unknown readiness_status"),
    (lambda r: r.__setitem__("completed_modules", "99"), "completed_modules"),
    (lambda r: r.__setitem__("python_assignment_score", "abc"), "python_assignment_score"),
    (lambda r: r.__setitem__("learner_id", "X-1"), "bad learner_id"),
])
def test_invalid_learner_rows_are_refused(mutation, message) -> None:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    mutation(rows[0])
    with tempfile.TemporaryDirectory() as folder:
        path = _write(folder, "l.csv", build_db.COLUMNS, [[r[c] for c in build_db.COLUMNS] for r in rows])
        with pytest.raises(ValueError, match=message):
            build_db.read_rows(path)


def test_duplicate_learner_ids_are_refused() -> None:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    rows.append(rows[0])
    with tempfile.TemporaryDirectory() as folder:
        path = _write(folder, "l.csv", build_db.COLUMNS, [[r[c] for c in build_db.COLUMNS] for r in rows])
        with pytest.raises(ValueError, match="duplicate learner_id"):
            build_db.read_rows(path)


def test_invalid_openings_are_refused() -> None:
    base = list(csv.DictReader(OPENINGS_CSV_PATH.open(encoding="utf-8")))[0]
    for field, value, message in (("status", "Maybe", "unknown status"), ("salary_max_egp", "1000", "below salary_min"),
                                  ("min_score", "high", "whole number"), ("opening_id", "BAD", "opening_id")):
        row = dict(base)
        row[field] = value
        with tempfile.TemporaryDirectory() as folder:
            path = _write(folder, "o.csv", build_db.OPENING_COLUMNS, [[row[c] for c in build_db.OPENING_COLUMNS]])
            with pytest.raises(ValueError, match=message):
                build_db.read_openings(path)
