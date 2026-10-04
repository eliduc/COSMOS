"""A12 field projection: actual ledger evaluator, synthetic local inputs."""
import copy
import hashlib
import json

import pytest

from cdpm import obligations as ob, obligation_field_view as view
from test_cdpm_a12_obligations_integration import _case
from test_cdpm_a12_obligations import _link


def _input(tmp_path):
    run, ledger = _case(tmp_path)
    # _case supplies only LEFT's link. Add the independent RIGHT declaration
    # explicitly; matching reader lists never manufacture the second link.
    _link(ledger, reader="RIGHT")
    (run / ob.LEDGER_REL).write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    raw = (run / "extract/model.json").read_bytes()
    report = ob.run_report(run, model_bytes=raw)
    assert report["form"] == "valid" and report["freshness"] == "current"
    return run, ledger, raw, report


def test_exact_field_value_and_separate_readers_remain_read_only(tmp_path):
    _, _, raw, report = _input(tmp_path)
    before = copy.deepcopy(report)
    got = view.from_report(model_bytes=raw, report=report)
    assert got["status"] == "current"
    assert got["model_sha256"] == hashlib.sha256(raw).hexdigest()
    assert got["meaning"] == "not_proven"
    assert got["fields"]
    doc = json.loads(raw)
    for field in got["fields"]:
        row = next(r for r in doc[field["collection"]] if r["id"] == field["row_id"])
        assert field["value"] == ob._pointer(row, field["pointer"], "test")
        assert all(link["correspondence"] == "declared_not_semantically_verified"
                   for link in field["links"])
    assert {link["reader"] for f in got["fields"] for link in f["links"]} == {"LEFT", "RIGHT"}
    got["fields"][0]["links"].clear()
    got["expert_review"]["status"] = "changed only in detached view"
    assert report == before


@pytest.mark.parametrize("status", ["disputed", "open_question"])
def test_unresolved_link_never_becomes_covered_field(tmp_path, status):
    run, ledger, raw, _ = _input(tmp_path)
    for link in ledger["links"]:
        link["status"] = status
    (run / ob.LEDGER_REL).write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    report = ob.run_report(run, model_bytes=raw)
    got = view.from_report(model_bytes=raw, report=report)
    assert got["status"] == "current" and got["fields"]
    assert {link["status"] for f in got["fields"] for link in f["links"]} == {status}


def test_new_model_bytes_do_not_reuse_current_old_field_links(tmp_path):
    _, _, raw, report = _input(tmp_path)
    got = view.from_report(model_bytes=raw + b" ", report=report)
    assert got["status"] == "stale_or_unresolved" and got["fields"] == []
    assert got["model_sha256"] != report["input_binding"]["model_sha256"]


def test_answered_question_target_still_has_open_question_disposition(tmp_path):
    run, ledger = _case(tmp_path, with_answer=True)
    _link(ledger, oid="O2", collection="questions", row="Q-LICENCE", pointer="/text")
    (run / ob.LEDGER_REL).write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    raw = (run / "extract/model.json").read_bytes()
    got = view.from_report(model_bytes=raw, report=ob.run_report(run, model_bytes=raw))
    assert got["status"] == "current"
    field = next(f for f in got["fields"] if f["collection"] == "questions")
    assert field["row_id"] == "Q-LICENCE" and field["pointer"] == "/text"
    assert {link["status"] for link in field["links"]} == {"open_question"}


@pytest.mark.parametrize("kind", ["absent", "invalid_json", "stale"])
def test_unavailable_or_stale_ledger_is_not_empty_success(tmp_path, kind):
    run, _, raw, _ = _input(tmp_path)
    if kind == "absent":
        (run / ob.LEDGER_REL).unlink()
    elif kind == "invalid_json":
        (run / ob.LEDGER_REL).write_text('{"schema_version":', encoding="utf-8")
    else:
        raw += b" "
    report = ob.run_report(run, model_bytes=raw)
    got = view.from_report(model_bytes=raw, report=report)
    assert got["status"] == {"absent": "not_recorded", "invalid_json": "unavailable",
                             "stale": "stale_or_unresolved"}[kind]
    assert got["fields"] == [] and got["meaning"] == "not_proven"


@pytest.mark.parametrize("damage", ["unknown_row", "unknown_pointer", "unknown_obligation", "unknown_status"])
def test_corrupt_backlink_view_refuses_partial_fields(tmp_path, damage):
    _, _, raw, report = _input(tmp_path)
    ref = report["reverse_links"][-1]
    if damage == "unknown_row": ref["row_id"] = "NO-SUCH-ROW"
    elif damage == "unknown_pointer": ref["pointer"] = "/missing-field"
    elif damage == "unknown_obligation": ref["obligation"] = "NO-SUCH-OBLIGATION"
    else: report["readers"][0]["items"][0]["status"] = "semantically-proven"
    got = view.from_report(model_bytes=raw, report=report)
    assert got["status"] == "unavailable" and got["fields"] == []
    assert got["issues"][-1]["code"] == "field_report_invalid"
