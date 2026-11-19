"""Tests for the learner-database tools (read-only access and the "Unable to verify" fallback)."""

import csv
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.config import CSV_PATH, DB_PATH
from src.fallback import is_unable_to_verify
from src.tools import db_queries as dq

CSV_ROWS = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))


def _copy_db(folder: str) -> Path:
    target = Path(folder) / "copy.db"
    shutil.copy(DB_PATH, target)
    return target


# ------------------------------------------------------------------ layer 1: statement guard
@pytest.mark.parametrize("sql", [
    "SELECT * FROM learners",
    "select learner_id from learners where target_salary_egp > 20000;",
    "WITH t AS (SELECT * FROM learners) SELECT COUNT(*) FROM t",
    "SELECT * FROM learners WHERE name = 'Drop; Table'",            # keywords inside a literal
    "SELECT 1 -- delete everything",
])
def test_guard_accepts_one_select(sql) -> None:
    assert dq.assert_select_only(sql) == sql


@pytest.mark.parametrize("sql", [
    "DROP TABLE learners", "DELETE FROM learners", "UPDATE learners SET python_assignment_score = 100",
    "INSERT INTO learners VALUES (1)", "SELECT 1; DROP TABLE learners", "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x.db' AS x", "SELECT * INTO t FROM learners", "", "   ", "/* x */ drop table learners",
    "WITH d AS (DELETE FROM learners RETURNING *) SELECT * FROM d",
])
def test_guard_rejects_everything_else(sql) -> None:
    with pytest.raises(dq.QueryRejected):
        dq.assert_select_only(sql)


# ------------------------------------------------------------------ layer 2: read-only connection
def test_connection_is_read_only_even_without_the_guard() -> None:
    with tempfile.TemporaryDirectory() as folder:
        conn = dq.connect_read_only(_copy_db(folder))
        try:
            assert conn.execute("SELECT COUNT(*) FROM learners").fetchone()[0] == len(CSV_ROWS)
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM learners")
        finally:
            conn.close()


def test_missing_database_file_raises_a_sqlite_error() -> None:
    with pytest.raises(sqlite3.OperationalError, match="not found"):
        dq.connect_read_only(Path("/nonexistent/none.db"))


# ------------------------------------------------------------------ run_select and the fallback
def test_run_select_returns_rows_with_parameters() -> None:
    result = dq.run_select("SELECT name, target_salary_egp FROM learners WHERE learner_id = ?", ("SPR-1001",))
    expected = next(r for r in CSV_ROWS if r["learner_id"] == "SPR-1001")
    assert result["status"] == "OK" and result["row_count"] == 1
    assert result["rows"][0] == {"name": expected["name"], "target_salary_egp": int(expected["target_salary_egp"])}
    assert result["columns"] == ["name", "target_salary_egp"] and result["truncated"] is False


def test_run_select_caps_the_rows_and_flags_truncation() -> None:
    result = dq.run_select("SELECT learner_id FROM learners", max_rows=5)
    assert result["row_count"] == 5 and result["truncated"] is True


@pytest.mark.parametrize("sql,code", [
    ("DROP TABLE learners", "QUERY_REJECTED"),
    ("SELECT 1; SELECT 2", "QUERY_REJECTED"),
    ("SELECT * FROM no_such_table", "DB_ERROR"),
    ("SELECT nope FROM learners", "DB_ERROR"),
])
def test_bad_queries_return_unable_to_verify_not_an_exception(sql, code) -> None:
    result = dq.run_select(sql)
    assert is_unable_to_verify(result) and result["reason_code"] == code
    assert result["message"].startswith("Unable to verify")


def test_missing_and_corrupt_databases_return_unable_to_verify() -> None:
    missing = dq.run_select("SELECT 1", db_path=Path("/nonexistent/none.db"))
    assert is_unable_to_verify(missing) and missing["reason_code"] == "DB_ERROR"
    with tempfile.TemporaryDirectory() as folder:
        broken = Path(folder) / "broken.db"
        broken.write_bytes(b"this is not a sqlite database" * 50)
        result = dq.run_select("SELECT * FROM learners", db_path=broken)
        assert is_unable_to_verify(result) and "Unable to verify" in result["message"]


def test_an_exception_inside_sqlite_is_converted_not_propagated() -> None:
    with mock.patch.object(dq, "connect_read_only", side_effect=sqlite3.DatabaseError("disk I/O error")):
        result = dq.run_select("SELECT 1")
    assert is_unable_to_verify(result) and "disk I/O error" in result["message"]
    with mock.patch.object(dq, "connect_read_only", side_effect=PermissionError("denied")):
        assert is_unable_to_verify(dq.run_select("SELECT 1"))


# ------------------------------------------------------------------ typed lookups
def test_learner_profile_has_progress_salary_and_employers() -> None:
    row = next(r for r in CSV_ROWS if r["learner_id"] == "SPR-1001")
    profile = dq.get_learner_profile("SPR-1001")
    assert profile["status"] == "OK"
    learner = profile["learner"]
    assert learner["target_salary_egp"] == int(row["target_salary_egp"])
    assert learner["completed_modules"] == int(row["completed_modules"])
    assert set(learner["preferred_employers"]) == {e.strip() for e in row["preferred_employers"].split(",")}
    assert learner["progress_pct"] == round(100 * int(row["completed_modules"]) / 15, 1)


def test_placement_criteria_follow_the_configured_thresholds() -> None:
    for row in CSV_ROWS:
        learner = dq.get_learner_profile(row["learner_id"])["learner"]
        expected = int(row["completed_modules"]) >= 12 and int(row["python_assignment_score"]) >= 85
        assert learner["meets_placement_criteria"] is expected, row["learner_id"]


def test_id_is_normalised_and_unknown_or_malicious_ids_are_handled() -> None:
    assert dq.get_learner_profile(" spr-1001 ")["status"] == "OK"
    assert dq.get_learner_profile("SPR-9999")["status"] == "NOT_FOUND"
    for bad in ("1001", "SPR-1001' OR '1'='1", "SPR-1001; DROP TABLE learners", ""):
        result = dq.get_learner_profile(bad)
        assert is_unable_to_verify(result) and result["reason_code"] == "BAD_INPUT", bad
    assert dq.run_select("SELECT COUNT(*) AS n FROM learners")["rows"][0]["n"] == len(CSV_ROWS)  # nothing dropped


def test_profile_falls_back_when_the_database_fails() -> None:
    with mock.patch.object(dq, "connect_read_only", side_effect=sqlite3.OperationalError("database is locked")):
        result = dq.get_learner_profile("SPR-1001")
    assert is_unable_to_verify(result) and result["message"].startswith("Unable to verify")


def test_cohort_summary_matches_the_csv() -> None:
    summary = dq.get_cohort_summary()
    counts = {r["readiness_status"]: r["learners"] for r in summary["by_status"]}
    expected = {}
    for r in CSV_ROWS:
        expected[r["readiness_status"]] = expected.get(r["readiness_status"], 0) + 1
    assert counts == expected
    backend = dq.get_cohort_summary("Backend")
    assert sum(r["learners"] for r in backend["by_status"]) == sum(r["bootcamp_track"] == "Backend" for r in CSV_ROWS)
    assert dq.get_cohort_summary("x' OR '1'='1")["by_status"] == []   # parameterised: no injection


# ------------------------------------------------------------------ LangChain tools return JSON text
def test_tools_return_json_strings_and_describe_the_schema() -> None:
    out = dq.query_learner_db.invoke({"query": "SELECT COUNT(*) AS n FROM learners"})
    assert json.loads(out)["rows"][0]["n"] == len(CSV_ROWS)
    assert "target_salary_egp" in dq.query_learner_db.description and "EGP" in dq.query_learner_db.description
    profile = json.loads(dq.lookup_learner_profile.invoke({"learner_id": "SPR-1001"}))
    assert profile["learner"]["learner_id"] == "SPR-1001"
    bad = json.loads(dq.query_learner_db.invoke({"query": "DELETE FROM learners"}))
    assert bad["status"] == "UNABLE_TO_VERIFY" and bad["message"].startswith("Unable to verify")


# ------------------------------------------------------------------ partner matching (live database records)
OPENING_ROWS = list(csv.DictReader((CSV_PATH.parent / "partner_openings.csv").open(encoding="utf-8")))


def _reference_matches(row):
    """Independent re-implementation of the rules, straight from the two CSV files."""
    if row["readiness_status"] != "Ready for Placement":
        return None
    employers = [e.strip() for e in row["preferred_employers"].split(",")]
    found = []
    for o in OPENING_ROWS:
        if (o["status"] == "Open" and o["employer"] in employers and o["bootcamp_track"] == row["bootcamp_track"]
                and int(row["completed_modules"]) >= int(o["min_modules"])
                and int(row["python_assignment_score"]) >= int(o["min_score"])
                and int(o["salary_max_egp"]) >= int(row["target_salary_egp"])):
            found.append(o["opening_id"])
    return found


def test_the_demo_learner_is_routed_to_fawry_then_swvl_with_reasons_for_every_other_opening() -> None:
    result = dq.find_matching_openings("SPR-1001")
    assert result["status"] == "OK"
    assert [(m["opening_id"], m["employer"], m["rank"]) for m in result["matches"]] == [
        ("OPN-101", "Fawry", 1), ("OPN-102", "Swvl", 2)]
    assert result["matches"][0]["salary_headroom_egp"] == 38000 - result["learner"]["target_salary_egp"]
    assert len(result["matches"]) + len(result["rejected"]) == len(OPENING_ROWS)
    assert all(r["failed_checks"] for r in result["rejected"])
    reasons = {r["opening_id"]: " ".join(r["failed_checks"]) for r in result["rejected"]}
    assert "Filled" in reasons["OPN-105"] and "needs 15" in reasons["OPN-104"] and "not one of" in reasons["OPN-103"]
    assert "proposal" in result["message"] and "human approval" in result["message"]


def test_matches_agree_with_an_independent_reference_for_every_learner() -> None:
    for row in CSV_ROWS:
        result = dq.find_matching_openings(row["learner_id"])
        expected = _reference_matches(row)
        if expected is None:
            assert result["status"] == "NOT_READY" and result["matches"] == [], row["learner_id"]
        else:
            assert result["status"] == "OK"
            assert sorted(m["opening_id"] for m in result["matches"]) == sorted(expected), row["learner_id"]


def test_a_learner_whose_numbers_qualify_but_who_is_not_ready_gets_no_match() -> None:
    """SPR-1022 has 13 modules and a score of 87, yet is 'In Progress': a human must decide."""
    profile = dq.get_learner_profile("SPR-1022")["learner"]
    assert profile["meets_placement_criteria"] is True and profile["readiness_status"] == "In Progress"
    result = dq.find_matching_openings("SPR-1022")
    assert result["status"] == "NOT_READY" and result["matches"] == [] and "human advisor" in result["message"]


def test_preferred_employers_keep_the_learners_order() -> None:
    row = next(r for r in CSV_ROWS if r["learner_id"] == "SPR-1001")
    assert dq.get_learner_profile("SPR-1001")["learner"]["preferred_employers"] == [
        e.strip() for e in row["preferred_employers"].split(",")]


def test_matching_handles_unknown_ids_and_database_failures() -> None:
    assert dq.find_matching_openings("SPR-9999")["status"] == "NOT_FOUND"
    assert is_unable_to_verify(dq.find_matching_openings("not-an-id"))
    with mock.patch.object(dq, "connect_read_only", side_effect=sqlite3.OperationalError("database is locked")):
        result = dq.find_matching_openings("SPR-1001")
    assert is_unable_to_verify(result) and result["message"].startswith("Unable to verify")


def test_matching_falls_back_when_only_the_openings_table_is_broken() -> None:
    with tempfile.TemporaryDirectory() as folder:
        broken = _copy_db(folder)
        conn = sqlite3.connect(broken)
        conn.execute("DROP TABLE partner_openings")
        conn.commit()
        conn.close()
        result = dq.find_matching_openings("SPR-1001", db_path=broken)
    assert is_unable_to_verify(result) and result["reason_code"] == "DB_ERROR"


def test_matching_tool_returns_json_and_tells_the_agent_not_to_guess() -> None:
    payload = json.loads(dq.match_partner_openings.invoke({"learner_id": "SPR-1001"}))
    assert payload["status"] == "OK" and payload["matches"][0]["opening_id"] == "OPN-101"
    assert "never your own reasoning" in dq.match_partner_openings.description
    assert "partner_openings" in dq.query_learner_db.description
