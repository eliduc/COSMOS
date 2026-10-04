"""Evolution improvement-findings store (webapp/evolution.py): dedup on
registration, the full found -> ... -> deployed pipeline with metrics
round-trip, loud validation failures, and the listing helpers the
nightly research agent and the Telegram admin flows rely on.

The improvements table lives in users.db, so redirecting
users.USERS_DB_PATH to a tmp file isolates the whole store.
"""
import pytest

from webapp import evolution, users


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()


# ------------------------------------------------------------------ dedup

def test_add_finding_dedup_by_url(store):
    created1, row1 = evolution.add_finding(
        "Set-partitioning warm start", "Warm-start CG from prior optimum.",
        source_url="https://Example.ORG/Papers/WarmStart ")
    created2, row2 = evolution.add_finding(
        "Different title, same paper", "Rediscovered by the nightly scan.",
        source_url="  https://example.org/papers/warmstart")
    assert created1 is True and created2 is False
    assert row1["id"] == row2["id"]
    # The existing row is returned as-is: original title/summary kept.
    assert row2["title"] == "Set-partitioning warm start"
    assert row2["status"] == evolution.STATUS_FOUND
    assert row2["dedup_key"] == "https://example.org/papers/warmstart"


def test_add_finding_dedup_by_title_fallback(store):
    created1, row1 = evolution.add_finding(
        "  Ruin-and-Recreate move selector ", "No URL for this idea.")
    created2, row2 = evolution.add_finding(
        "ruin-and-recreate MOVE selector", "Same idea, different casing.")
    assert created1 is True and created2 is False
    assert row1["id"] == row2["id"]
    assert row1["dedup_key"] == "title:ruin-and-recreate move selector"
    # A finding WITH a url does not collide with the title-keyed one.
    created3, row3 = evolution.add_finding(
        "Ruin-and-Recreate move selector", "Now with a source.",
        source_url="https://example.org/rr")
    assert created3 is True and row3["id"] != row1["id"]


# ------------------------------------------------------------- happy path

def test_full_pipeline_happy_path(store):
    created, row = evolution.add_finding(
        "OR-Tools 9.11 routing upgrade",
        "New CP-SAT presolve should speed up pricing.",
        source_url="https://example.org/ortools-9.11",
        integration_plan="Bump the dependency, rerun the S5 battery.",
        expected_gain="5-10% faster pricing iterations")
    assert created is True
    imp_id = row["id"]
    assert row["status"] == evolution.STATUS_FOUND
    assert row["test_metrics"] is None and row["stage_metrics"] is None
    assert row["integration_plan"].startswith("Bump")
    assert row["expected_gain"] == "5-10% faster pricing iterations"

    row = evolution.set_status(imp_id, evolution.STATUS_APPROVED_FOR_TEST,
                               decided_by="telegram-admin")
    assert row["status"] == evolution.STATUS_APPROVED_FOR_TEST
    assert row["decided_by"] == "telegram-admin"

    row = evolution.set_status(imp_id, evolution.STATUS_TESTING,
                               branch="evolution/ortools-911")
    assert row["branch"] == "evolution/ortools-911"
    assert row["decided_by"] == "telegram-admin"     # COALESCE-preserved

    test_metrics = {"baseline_cost": 123456.0, "candidate_cost": 118200.5,
                    "runs": 5, "improved": True}
    row = evolution.attach_metrics(imp_id, "test", test_metrics)
    assert row["test_metrics"] == test_metrics       # parsed round-trip
    row = evolution.set_status(imp_id, evolution.STATUS_TEST_IMPROVED)
    assert row["status"] == evolution.STATUS_TEST_IMPROVED

    row = evolution.set_status(imp_id, evolution.STATUS_APPROVED_FOR_STAGE,
                               decided_by="telegram-admin")
    row = evolution.set_status(imp_id, evolution.STATUS_STAGING)
    stage_metrics = {"smoke_suite": "green", "wall_clock_s": 812}
    row = evolution.attach_metrics(imp_id, "stage", stage_metrics)
    assert row["stage_metrics"] == stage_metrics
    assert row["test_metrics"] == test_metrics       # untouched
    row = evolution.set_status(imp_id, evolution.STATUS_STAGE_PASSED)

    row = evolution.set_status(imp_id, evolution.STATUS_APPROVED_FOR_PROD)
    row = evolution.set_status(imp_id, evolution.STATUS_DEPLOYING)
    row = evolution.set_status(imp_id, evolution.STATUS_DEPLOYED)
    assert row["status"] == evolution.STATUS_DEPLOYED
    assert row["status"] in evolution.TERMINAL_STATUSES

    # Everything survived the whole pipeline; get() agrees with the
    # value returned by the last mutation.
    final = evolution.get(imp_id)
    assert final == row
    assert final["decided_by"] == "telegram-admin"
    assert final["branch"] == "evolution/ortools-911"
    assert final["test_metrics"] == test_metrics
    assert final["stage_metrics"] == stage_metrics
    assert final["error"] is None


# ------------------------------------------------------------- validation

def test_validation_failures_are_loud(store):
    _, row = evolution.add_finding("Valid", "Something real.")
    with pytest.raises(ValueError):
        evolution.set_status(row["id"], "shipped")           # unknown status
    with pytest.raises(evolution.ImprovementNotFoundError):
        evolution.get(99999)
    with pytest.raises(evolution.ImprovementNotFoundError):
        evolution.set_status(99999, evolution.STATUS_REJECTED)
    with pytest.raises(evolution.ImprovementNotFoundError):
        evolution.attach_metrics(99999, "test", {})
    with pytest.raises(ValueError):
        evolution.add_finding("", "summary present")
    with pytest.raises(ValueError):
        evolution.add_finding("   ", "whitespace-only title")
    with pytest.raises(ValueError):
        evolution.add_finding("title present", "")
    with pytest.raises(ValueError):
        evolution.add_finding(None, "not a string title")
    with pytest.raises(ValueError):
        evolution.attach_metrics(row["id"], "prod", {"x": 1})  # bad phase
    # ImprovementNotFoundError is a typed users-store error.
    assert issubclass(evolution.ImprovementNotFoundError, users.UsersError)


# ---------------------------------------------------------------- listing

def test_known_findings_and_list_all_filter(store):
    _, a = evolution.add_finding("Alpha", "First.",
                                 source_url="https://example.org/a")
    _, b = evolution.add_finding("Beta", "Second.")
    _, c = evolution.add_finding("Gamma", "Third.",
                                 source_url="https://example.org/c")
    evolution.set_status(b["id"], evolution.STATUS_REJECTED,
                         decided_by="telegram-admin")
    evolution.set_status(c["id"], evolution.STATUS_APPROVED_FOR_TEST,
                         decided_by="telegram-admin")

    # known_findings: title/source_url pairs for ALL rows, any status.
    known = evolution.known_findings()
    assert {"title": "Alpha", "source_url": "https://example.org/a"} in known
    assert {"title": "Beta", "source_url": None} in known
    assert {"title": "Gamma", "source_url": "https://example.org/c"} in known
    assert len(known) == 3

    everything = evolution.list_all()
    assert len(everything) == 3
    assert [r["id"] for r in everything] == sorted(
        [a["id"], b["id"], c["id"]], reverse=True)      # newest-first

    found_only = evolution.list_all(status=evolution.STATUS_FOUND)
    assert [r["id"] for r in found_only] == [a["id"]]
    rejected = evolution.list_all(status=evolution.STATUS_REJECTED)
    assert [r["id"] for r in rejected] == [b["id"]]
    assert evolution.list_all(status=evolution.STATUS_DEPLOYED) == []
    with pytest.raises(ValueError):
        evolution.list_all(status="bogus")
