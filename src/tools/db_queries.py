"""Learner-database tools for the Sprints advisor agent.

Curriculum:
    Module 10 (SQL and Data Tools): read-only access to the learner database (progress, scores,
    target salary in EGP, preferred employers).
    Module 8 (Tools and Function Calling): every function here is also exposed as a LangChain
    tool with a precise description, and returns JSON, never an exception.
    Module 14 (Human-in-the-Loop and Safety): the database is opened read-only and only a single
    SELECT is accepted, so a mistaken or prompt-injected query cannot change learner records.

Failure contract: if SQLite raises (missing file, locked, bad SQL, corrupt database) or the query is
rejected by the guard, the tool returns the structured result from ``src.fallback.unable_to_verify``
("Unable to verify: ...") instead of raising.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.tools import tool

from src.config import (DB_PATH, PASSING_SCORE, PLACEMENT_MIN_MODULES, PLACEMENT_MIN_SCORE,
                        TOTAL_MODULES)
from src.fallback import unable_to_verify

MAX_ROWS = 50
_LEARNER_ID = re.compile(r"^SPR-\d{4,}$")

# ---- Layer 1: statement guard (single SELECT / WITH only) --------------------------------------
_NOISE = re.compile(r"'(?:[^']|'')*'" r'|"(?:[^"]|"")*"' r"|/\*.*?\*/" r"|--[^\n]*", re.DOTALL)
_STARTS_READ = re.compile(r"(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|replace|attach|detach|pragma|vacuum|"
    r"reindex|analyze|into)\b", re.IGNORECASE)


class QueryRejected(ValueError):
    """Raised by :func:`assert_select_only` for anything that is not one plain SELECT.

    Curriculum:
        Module 14 (Human-in-the-Loop and Safety): the error that marks an unsafe query, for
        example a write, a DROP, several statements or a PRAGMA, so the tool can answer with the
        structured "Unable to verify" result instead of touching the learner records.
    """


def assert_select_only(sql: str) -> str:
    """Accept exactly one SELECT/WITH statement; reject everything else.

    Curriculum:
        Module 14 (Human-in-the-Loop and Safety): layer 1 of the read-only design. Layer 2 is
        the read-only SQLite connection in :func:`connect_read_only`.

    Args:
        sql: Statement written by the agent.

    Returns:
        The same text, if it is acceptable.

    Raises:
        QueryRejected: Several statements, a non-SELECT start, or a write/DDL/PRAGMA keyword.
    """
    text = str(sql)
    cleaned = _NOISE.sub(" ", text).strip().rstrip(";").strip()
    if not cleaned:
        raise QueryRejected("empty query")
    if ";" in cleaned:
        raise QueryRejected("only one SQL statement is allowed")
    if not _STARTS_READ.match(cleaned):
        raise QueryRejected("only SELECT queries are allowed")
    found = _FORBIDDEN.search(cleaned)
    if found:
        raise QueryRejected(f"keyword {found.group(1).upper()!r} is not allowed (read-only access)")
    return text


def connect_read_only(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the learner database read-only (layer 2 of the read-only design).

    Curriculum:
        Module 10 (SQL and Data Tools): connection handling for the learner database.
        Module 14 (Human-in-the-Loop and Safety): SQLite itself refuses every write on this
        connection (``mode=ro`` and ``PRAGMA query_only``), even if the statement guard had a bug.

    Raises:
        sqlite3.Error: If the file is missing or cannot be opened.
    """
    path = Path(db_path).resolve()
    if not path.exists():
        raise sqlite3.OperationalError(f"database file not found: {path.name}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def run_select(sql: str, params: Sequence[Any] = (), max_rows: int = MAX_ROWS,
               db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Run one read-only SELECT and return rows as dictionaries.

    Curriculum:
        Module 10 (SQL and Data Tools): the generic query primitive under every learner lookup.
        Module 8 (Tools and Function Calling): returns a structured result; never raises.

    Args:
        sql: A single SELECT/WITH statement. Use ``?`` placeholders for values.
        params: Values for the placeholders.
        max_rows: Row cap, so a careless query cannot flood the agent's context.
        db_path: Database file (tests point this at a temporary database).

    Returns:
        ``{"status": "OK", "columns": [...], "rows": [...], "row_count": n, "truncated": bool}``
        or the "Unable to verify" fallback (reason codes ``QUERY_REJECTED`` and ``DB_ERROR``).
    """
    try:
        assert_select_only(sql)
    except QueryRejected as exc:
        return unable_to_verify("query_learner_db", f"the query was rejected ({exc})", "QUERY_REJECTED")
    try:
        conn = connect_read_only(db_path)
        try:
            cursor = conn.execute(sql, tuple(params))
            fetched = cursor.fetchmany(max_rows + 1)
            columns = [d[0] for d in cursor.description or []]
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return unable_to_verify("query_learner_db", f"the learner database could not be read ({type(exc).__name__}: {exc})",
                                "DB_ERROR")
    rows = [dict(r) for r in fetched[:max_rows]]
    return {"status": "OK", "columns": columns, "rows": rows, "row_count": len(rows),
            "truncated": len(fetched) > max_rows}


def get_learner_profile(learner_id: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Return one learner's progress, assignment score, target salary (EGP) and employers.

    Curriculum:
        Module 10 (SQL and Data Tools): parameterised lookup (no string-built SQL).
        Module 15 (Capstone and Career Readiness): the facts a placement decision rests on;
        ``meets_placement_criteria`` applies the thresholds in ``src.config``.

    Args:
        learner_id: Identifier such as ``SPR-1001``.
        db_path: Database file.

    Returns:
        ``{"status": "OK", "learner": {...}}``; ``{"status": "NOT_FOUND", ...}`` for an unknown ID
        (a normal answer, not a failure); or the "Unable to verify" fallback.
    """
    learner_id = str(learner_id).strip().upper()
    if not _LEARNER_ID.match(learner_id):
        return unable_to_verify("get_learner_profile", f"{learner_id!r} is not a valid learner ID (expected SPR-1234)",
                                "BAD_INPUT")
    base = run_select("SELECT * FROM learners WHERE learner_id = ?", (learner_id,), db_path=db_path)
    if base["status"] != "OK":
        return {**base, "tool": "get_learner_profile"}
    if not base["rows"]:
        return {"status": "NOT_FOUND", "message": f"No learner with ID {learner_id} exists in the database.",
                "learner_id": learner_id}
    employers = run_select("SELECT employer FROM learner_employers WHERE learner_id = ? ORDER BY position",
                           (learner_id,), db_path=db_path)
    if employers["status"] != "OK":
        return {**employers, "tool": "get_learner_profile"}
    row = base["rows"][0]
    modules, score = row["completed_modules"], row["python_assignment_score"]
    row["preferred_employers"] = [e["employer"] for e in employers["rows"]]
    row["progress_pct"] = round(100 * modules / TOTAL_MODULES, 1)
    row["passed_python_assignment"] = score >= PASSING_SCORE
    row["meets_placement_criteria"] = modules >= PLACEMENT_MIN_MODULES and score >= PLACEMENT_MIN_SCORE
    return {"status": "OK", "learner": row}


def get_cohort_summary(track: Optional[str] = None, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Summarise readiness and target salaries (EGP), optionally for one bootcamp track.

    Curriculum:
        Module 10 (SQL and Data Tools): aggregate SQL (GROUP BY) instead of pulling every row.
        Module 15 (Capstone and Career Readiness): the cohort view an advisor uses to plan placements.

    Args:
        track: Bootcamp track such as ``Backend``; ``None`` for all tracks.
        db_path: Database file.

    Returns:
        ``{"status": "OK", "by_status": [{readiness_status, learners, avg_target_salary_egp}, ...]}``
        or the "Unable to verify" fallback.
    """
    sql = ("SELECT readiness_status, COUNT(*) AS learners, "
           "CAST(ROUND(AVG(target_salary_egp)) AS INTEGER) AS avg_target_salary_egp "
           "FROM learners {where} GROUP BY readiness_status ORDER BY learners DESC")
    if track:
        result = run_select(sql.format(where="WHERE bootcamp_track = ?"), (track,), db_path=db_path)
    else:
        result = run_select(sql.format(where=""), db_path=db_path)
    if result["status"] != "OK":
        return {**result, "tool": "get_cohort_summary"}
    return {"status": "OK", "track": track or "all", "by_status": result["rows"]}


def evaluate_opening(learner: Dict[str, Any], opening: Dict[str, Any]) -> List[str]:
    """Return the checks a learner FAILS for one opening (empty list means eligible).

    Curriculum:
        Module 15 (Capstone and Career Readiness): the explicit, auditable placement rules.
        Module 14 (Human-in-the-Loop and Safety): matching is deterministic code over database
        records, never a language-model guess, so a wrong employer match cannot be hallucinated.

    Args:
        learner: Output of :func:`get_learner_profile` (``learner`` dictionary).
        opening: One row of ``partner_openings``.

    Returns:
        Failure descriptions such as ``"opening is Filled (must be Open)"``; ``[]`` when eligible.
    """
    failed = []
    if opening["status"] != "Open":
        failed.append(f"opening is {opening['status']} (must be Open)")
    if opening["employer"] not in learner["preferred_employers"]:
        failed.append(f"{opening['employer']} is not one of the learner's preferred employers")
    if opening["bootcamp_track"] != learner["bootcamp_track"]:
        failed.append(f"opening is for the {opening['bootcamp_track']} track, learner is in {learner['bootcamp_track']}")
    if learner["completed_modules"] < opening["min_modules"]:
        failed.append(f"learner completed {learner['completed_modules']} modules, opening needs {opening['min_modules']}")
    if learner["python_assignment_score"] < opening["min_score"]:
        failed.append(f"learner scored {learner['python_assignment_score']}, opening needs {opening['min_score']}")
    if opening["salary_max_egp"] < learner["target_salary_egp"]:
        failed.append(f"opening pays at most {opening['salary_max_egp']} EGP, learner targets {learner['target_salary_egp']} EGP")
    return failed


def find_matching_openings(learner_id: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Match a ready learner to partner openings using only live database records.

    Curriculum:
        Module 10 (SQL and Data Tools): reads ``learners``, ``learner_employers`` and
        ``partner_openings`` and applies the placement rules in code.
        Module 15 (Capstone and Career Readiness): the proposal a human advisor approves; the
        result lists every opening that was rejected and exactly why, for the audit trail.
        Module 14 (Human-in-the-Loop and Safety): the output is a PROPOSAL; no placement is final
        until a human approves it.

    Rules (all must hold): learner status is "Ready for Placement"; opening is Open; its employer
    is among the learner's preferred employers; same bootcamp track; the learner meets the opening's
    minimum modules and score; the opening's maximum salary reaches the learner's target (EGP).
    Matches are ordered by the learner's employer preference, then by salary headroom.

    Args:
        learner_id: Identifier such as ``SPR-1001``.
        db_path: Database file.

    Returns:
        ``{"status": "OK", "learner": {...}, "matches": [...], "rejected": [...]}``; or
        ``{"status": "NOT_READY", ...}`` when the learner is not "Ready for Placement" (a normal
        answer); ``NOT_FOUND``; or the "Unable to verify" fallback if the database fails.
    """
    profile = get_learner_profile(learner_id, db_path)
    if profile["status"] != "OK":
        return profile
    learner = profile["learner"]
    summary = {k: learner[k] for k in ("learner_id", "name", "bootcamp_track", "completed_modules",
                                         "python_assignment_score", "target_salary_egp", "readiness_status",
                                         "preferred_employers")}
    if learner["readiness_status"] != "Ready for Placement":
        return {"status": "NOT_READY", "learner": summary, "matches": [], "rejected": [],
                "message": (f"{learner['name']} is '{learner['readiness_status']}', not 'Ready for Placement'; "
                            "no placement match is proposed. A human advisor must review this learner.")}
    openings = run_select("SELECT * FROM partner_openings ORDER BY opening_id", db_path=db_path)
    if openings["status"] != "OK":
        return {**openings, "tool": "find_matching_openings"}
    matches, rejected = [], []
    for opening in openings["rows"]:
        failed = evaluate_opening(learner, opening)
        if failed:
            rejected.append({"opening_id": opening["opening_id"], "employer": opening["employer"],
                             "role_title": opening["role_title"], "failed_checks": failed})
        else:
            matches.append({**opening, "salary_headroom_egp": opening["salary_max_egp"] - learner["target_salary_egp"],
                            "employer_preference_rank": learner["preferred_employers"].index(opening["employer"]) + 1})
    matches.sort(key=lambda m: (m["employer_preference_rank"], -m["salary_headroom_egp"], m["opening_id"]))
    for rank, match in enumerate(matches, start=1):
        match["rank"] = rank
    return {"status": "OK", "learner": summary, "matches": matches, "rejected": rejected,
            "message": (f"{len(matches)} eligible opening(s) found. This is a proposal that requires human approval."
                        if matches else "No eligible opening exists right now. A human advisor must review this learner.")}


# ---- LangChain tools (what the advisor agent actually calls) -----------------------------------
@tool
def query_learner_db(query: str) -> str:
    """Run ONE read-only SQL SELECT on the Sprints learner database and return JSON.

    Curriculum:
        Module 10 (SQL and Data Tools): the custom SQL tool of the ReAct advisor.
        Module 9 (ReAct Agents): the agent writes the SQL, reads the JSON observation and decides
        its next step.

    Tables: learners(learner_id, name, bootcamp_track, completed_modules, python_assignment_score,
    target_salary_egp, readiness_status), learner_employers(learner_id, employer, position) and
    partner_openings(opening_id, employer, role_title, bootcamp_track, min_modules, min_score,
    salary_min_egp, salary_max_egp, status, location). Salaries are in EGP. readiness_status is one of: Ready for Placement, In Progress, Needs Review, Dropped.
    Only a single SELECT is accepted; at most 50 rows are returned. If the database cannot be read
    the result has status UNABLE_TO_VERIFY and the message starts with "Unable to verify".
    """
    return json.dumps(run_select(query), ensure_ascii=False)


@tool
def lookup_learner_profile(learner_id: str) -> str:
    """Look up one learner by ID (for example SPR-1001) and return JSON.

    Curriculum:
        Module 10 (SQL and Data Tools): the safest way to read a single learner (parameterised).
        Module 15 (Capstone and Career Readiness): returns progress_pct, python_assignment_score,
        target_salary_egp, preferred_employers and meets_placement_criteria for the advisor.

    An unknown ID returns status NOT_FOUND. A database failure returns status UNABLE_TO_VERIFY with
    a message that starts with "Unable to verify".
    """
    return json.dumps(get_learner_profile(learner_id), ensure_ascii=False)


@tool
def match_partner_openings(learner_id: str) -> str:
    """Propose partner job openings for one learner from live database records and return JSON.

    Curriculum:
        Module 10 (SQL and Data Tools): joins learner records with the partner openings table.
        Module 15 (Capstone and Career Readiness): the placement proposal an advisor reviews.
        Module 14 (Human-in-the-Loop and Safety): deterministic rules, not model judgement; the
        result is only a proposal and must go to a human for approval.

    Use this tool, never your own reasoning, to decide which employer or opening suits a learner.
    The JSON lists "matches" (eligible openings, best first) and "rejected" (every other opening with
    the exact failed checks). Status NOT_READY means the learner is not "Ready for Placement".
    A database failure returns UNABLE_TO_VERIFY with a message starting "Unable to verify".
    """
    return json.dumps(find_matching_openings(learner_id), ensure_ascii=False)
