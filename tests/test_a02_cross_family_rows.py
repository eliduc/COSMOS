"""Remaining A02 evidence: one answer spans unrelated catalogue families."""
import copy
import json

import pytest

from cdpm import answers, catalog_bridge as cb, model as m
from cdpm.gates import gate_answers
from test_cdpm_a02_worker_row_results import _pending, _result_reply, _worker
from test_k03_catalogue_numeric_reader import selected


@pytest.fixture(params=["0.5", "0.7"])
def catalogue(request, tmp_path, monkeypatch):
    if request.param == "0.7":
        selected(tmp_path, monkeypatch)
    else:
        monkeypatch.delenv(cb.ENV_CATALOG, raising=False)
        cb.forget()
    yield request.param
    cb.forget()


def cross_family_pending():
    doc, q = _pending()
    first, second = doc["rules"][0], doc["rules"][-1]
    for row, family, params, statement in (
        (first, "rest_floors", {"min_rest_home": {"value": 720, "quote": "12:00"},
                               "min_rest_outstation": {"value": 600, "quote": "10:00"}},
         "Минимальный отдых дома — 12:00, вне базы — 10:00."),
        (second, "night_series", {"max_consecutive_night_duties": {"value": 3, "quote": "3"}},
         "Не более 3 ночных дежурств подряд."),
    ):
        row.update(statement=statement, zone="ROSTERING", kind="HARD")
        row["template_fit"] = {"category": "A", "registry_rev": m.registry_fingerprint(),
                               "family": family, "params": params}
    m.derive_all_rule_traits(doc, "cross-family fixture")
    assert first["template_fit"]["family"] != second["template_fit"]["family"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, q, first, second


def check_gate(tmp_path, doc):
    model_path, summary_path = tmp_path / "model.json", tmp_path / "summary.json"
    model_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(json.dumps({"revision": doc["meta"]["revision"]}), encoding="utf-8")
    return gate_answers.check(model_path, summary_path)


@pytest.mark.parametrize("first_index", [0, 1])
def test_cross_family_partial_gate_and_only_remaining_retry(catalogue, tmp_path, first_index):
    doc, q, rest, night = cross_family_pending()
    first, second = (rest, night) if first_index == 0 else (night, rest)
    untouched = copy.deepcopy(second)
    assert q["id"] in first["blockers"] and q["id"] in second["blockers"]
    result = answers.apply_worker_patches(doc, _worker(q, [first]))
    assert result["worker_rejected"] == [] and result["worker_partial"] == 1
    assert second == untouched and q["id"] not in first.get("blockers", [])
    issues, counts = check_gate(tmp_path, doc)
    assert any(q["id"] in issue and "частично" in issue for issue in issues)
    assert counts["partial"] == 1 and m.is_open_question(q, doc=doc)
    assert answers._affected_rules(doc, q) == [second]
    fixed_first = copy.deepcopy(first)
    result = answers.apply_worker_patches(doc, _worker(q, [second]))
    assert result["worker_rejected"] == [] and result["worker_applied"] == 1
    assert q["answer"]["status"] == "применён" and first == fixed_first
    assert not m.is_open_question(q, doc=doc)
    assert check_gate(tmp_path, doc)[0] == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_cross_family_complete_and_quoted_unchanged_keep_both_values(catalogue, tmp_path):
    doc, q, first, second = cross_family_pending()
    before = [copy.deepcopy(r["template_fit"]) for r in (first, second)]
    q["answer"]["raw"] = "Оба правила без изменений."
    result = answers.apply_worker_patches(doc, _result_reply(q, [
        {"id": r["id"], "unchanged": {"basis": q["answer"]["raw"]}}
        for r in (first, second)]))
    assert result["worker_rejected"] == [] and result["worker_applied"] == 1
    assert [r["template_fit"] for r in (first, second)] == before
    assert all(q["id"] not in r.get("blockers", []) for r in (first, second))
    assert check_gate(tmp_path, doc)[0] == []
