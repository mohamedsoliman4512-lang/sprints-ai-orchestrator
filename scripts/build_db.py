"""Build the SQLite learner database from data/sprints_learner_db.csv.

    python scripts/build_db.py

Curriculum:
    Module 10 (SQL and Data Tools): turns the raw CSV into a typed, validated, queryable SQLite
    database with a normalised employers table, the data the advisor agent queries.

The script is idempotent (it rebuilds the file) and refuses to build if the CSV breaks a rule
(duplicate IDs, unknown readiness status, out-of-range numbers), so bad data never reaches an agent.
"""

import csv
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import (CSV_PATH, DB_PATH, OPENING_STATUSES, OPENINGS_CSV_PATH,  # noqa: E402
                        READINESS_STATUSES, TOTAL_MODULES)

COLUMNS = ["learner_id", "name", "bootcamp_track", "completed_modules", "python_assignment_score",
           "target_salary_egp", "preferred_employers", "readiness_status"]

SCHEMA = """
CREATE TABLE learners (
    learner_id              TEXT PRIMARY KEY CHECK (learner_id GLOB 'SPR-[0-9][0-9][0-9][0-9]*'),
    name                    TEXT NOT NULL,
    bootcamp_track          TEXT NOT NULL,
    completed_modules       INTEGER NOT NULL CHECK (completed_modules BETWEEN 0 AND %d),
    python_assignment_score INTEGER NOT NULL CHECK (python_assignment_score BETWEEN 0 AND 100),
    target_salary_egp       INTEGER NOT NULL CHECK (target_salary_egp > 0),
    readiness_status        TEXT NOT NULL CHECK (readiness_status IN (%s))
);
CREATE TABLE learner_employers (
    learner_id TEXT NOT NULL REFERENCES learners(learner_id),
    employer   TEXT NOT NULL,
    position   INTEGER NOT NULL,            -- 1 = first choice, in the order given by the learner
    PRIMARY KEY (learner_id, employer)
);
CREATE TABLE partner_openings (
    opening_id      TEXT PRIMARY KEY CHECK (opening_id GLOB 'OPN-[0-9][0-9][0-9]*'),
    employer        TEXT NOT NULL,
    role_title      TEXT NOT NULL,
    bootcamp_track  TEXT NOT NULL,
    min_modules     INTEGER NOT NULL CHECK (min_modules BETWEEN 0 AND %d),
    min_score       INTEGER NOT NULL CHECK (min_score BETWEEN 0 AND 100),
    salary_min_egp  INTEGER NOT NULL CHECK (salary_min_egp > 0),
    salary_max_egp  INTEGER NOT NULL CHECK (salary_max_egp >= salary_min_egp),
    status          TEXT NOT NULL CHECK (status IN (%s)),
    location        TEXT NOT NULL
);
CREATE INDEX idx_learners_status ON learners(readiness_status);
CREATE INDEX idx_learners_track  ON learners(bootcamp_track);
""" % (TOTAL_MODULES, ", ".join(f"'{s}'" for s in READINESS_STATUSES), TOTAL_MODULES,
       ", ".join(f"'{s}'" for s in OPENING_STATUSES))


def read_rows(csv_path: Path) -> List[Dict[str, str]]:
    """Read and validate the CSV; raise ValueError listing every problem found."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing columns: {missing}")
        rows = list(reader)
    problems, seen = [], set()
    for n, row in enumerate(rows, start=2):
        if not re.fullmatch(r"SPR-\d{4,}", row["learner_id"]):
            problems.append(f"line {n}: bad learner_id {row['learner_id']!r}")
        if row["learner_id"] in seen:
            problems.append(f"line {n}: duplicate learner_id {row['learner_id']}")
        seen.add(row["learner_id"])
        if row["readiness_status"] not in READINESS_STATUSES:
            problems.append(f"line {n}: unknown readiness_status {row['readiness_status']!r}")
        for col, low, high in (("completed_modules", 0, TOTAL_MODULES),
                               ("python_assignment_score", 0, 100), ("target_salary_egp", 1, 10**7)):
            if not row[col].isdigit() or not low <= int(row[col]) <= high:
                problems.append(f"line {n}: {col}={row[col]!r} outside {low}..{high}")
    if problems:
        raise ValueError("invalid CSV:\n  " + "\n  ".join(problems))
    return rows


OPENING_COLUMNS = ["opening_id", "employer", "role_title", "bootcamp_track", "min_modules", "min_score",
                   "salary_min_egp", "salary_max_egp", "status", "location"]


def read_openings(csv_path: Path) -> List[Dict[str, str]]:
    """Read and validate the partner openings CSV; raise ValueError listing every problem."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in OPENING_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"openings CSV is missing columns: {missing}")
        rows = list(reader)
    problems, seen = [], set()
    for n, row in enumerate(rows, start=2):
        if not re.fullmatch(r"OPN-\d{3,}", row["opening_id"]) or row["opening_id"] in seen:
            problems.append(f"line {n}: bad or duplicate opening_id {row['opening_id']!r}")
        seen.add(row["opening_id"])
        if row["status"] not in OPENING_STATUSES:
            problems.append(f"line {n}: unknown status {row['status']!r}")
        for col in ("min_modules", "min_score", "salary_min_egp", "salary_max_egp"):
            if not row[col].isdigit():
                problems.append(f"line {n}: {col}={row[col]!r} is not a whole number")
        if all(row[c].isdigit() for c in ("salary_min_egp", "salary_max_egp")) and int(row["salary_max_egp"]) < int(row["salary_min_egp"]):
            problems.append(f"line {n}: salary_max_egp is below salary_min_egp")
    if problems:
        raise ValueError("invalid openings CSV:\n  " + "\n  ".join(problems))
    return rows


def build(csv_path: Path = CSV_PATH, db_path: Path = DB_PATH, openings_path: Path = OPENINGS_CSV_PATH) -> int:
    """Create ``db_path`` from the learner and openings CSVs; return the number of learners loaded."""
    rows = read_rows(csv_path)
    openings = read_openings(openings_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for r in rows:
            conn.execute("INSERT INTO learners VALUES (?,?,?,?,?,?,?)",
                         (r["learner_id"], r["name"], r["bootcamp_track"], int(r["completed_modules"]),
                          int(r["python_assignment_score"]), int(r["target_salary_egp"]),
                          r["readiness_status"]))
            names = [e.strip() for e in r["preferred_employers"].split(",") if e.strip()]
            for position, employer in enumerate(names, start=1):
                conn.execute("INSERT INTO learner_employers VALUES (?,?,?)", (r["learner_id"], employer, position))
        for o in openings:
            conn.execute("INSERT INTO partner_openings VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (o["opening_id"], o["employer"], o["role_title"], o["bootcamp_track"],
                          int(o["min_modules"]), int(o["min_score"]), int(o["salary_min_egp"]),
                          int(o["salary_max_egp"]), o["status"], o["location"]))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


if __name__ == "__main__":
    count = build()
    print(f"Built {DB_PATH.relative_to(ROOT)} with {count} learners from {CSV_PATH.relative_to(ROOT)}")
