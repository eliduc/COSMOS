"""Real questionnaire writer and Z3: an answer must reach the SMT subject.

The deterministic translator reads the actual prompt, not the source model.
No network, external service, catalog support or acceptance is simulated.
"""
import copy
import json

import pytest

from cdpm import answers, smt_verify as sv
from test_cdpm_vmp_form_typed_roundtrip import answered
from test_k01_semantic_transition import clean_bridge


def translator(prompt):
    # Original home norm remains in the statement. The second norm can only
    # arrive through the prompt's separate, provenance-checked answer field.
    assert "R-REST | дома не менее 12 часов" in prompt
    extra = next((json.loads(line.split("ANSWER_PARAMS_JSON: ", 1)[1])
                  for line in prompt.splitlines() if line.startswith("ANSWER_PARAMS_JSON: ")), {})
    params = extra.get("params", {})
    formula = "(>= home 720)"
    if "min_rest_outstation" in params:
        cell = params["min_rest_outstation"]
        assert extra["family"] == "rest_floors"
        assert cell["unit"] == "duration_minutes" and cell["relation"] == "min"
        assert cell["source"]["kind"] == "answer" and cell["quote"] == "600 минут"
        formula = f"(and {formula} (>= outstation {cell['value']}))"
    bound = 599 if "R-UPPER | вне базы не более 599 минут" in prompt else 600
    return json.dumps({"decls": ["(declare-fun home () Int)",
                                  "(declare-fun outstation () Int)"],
                       "asserts": [{"rule": "R-REST", "smt": formula},
                                   {"rule": "R-UPPER", "smt": f"(<= outstation {bound})"}]})


@pytest.mark.parametrize("upper,verdict", [(599, "unsat"), (600, "sat")])
def test_answer_reaches_real_solver_and_preserves_healthy_case(tmp_path, monkeypatch, upper, verdict):
    doc, rule, q = answered(tmp_path, monkeypatch)
    doc["rules"].append({"id": "R-UPPER", "kind": "HARD",
                         "statement": f"вне базы не более {upper} минут"})
    original = copy.deepcopy(doc)
    got = sv.verify(doc, translator)
    assert got["verdict"] == verdict
    assert got["n_rules"] == got["n_translated"] == 2
    if verdict == "unsat":
        assert set(got["core"]) == {rule["id"], "R-UPPER"}
    assert doc == original


@pytest.mark.parametrize("field", ["value", "unit", "relation", "condition", "source", "quote"])
def test_answer_change_invalidates_subject_without_statement_change(tmp_path, monkeypatch, field):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    changed = copy.deepcopy(doc)
    cell = changed["rules"][0]["template_fit"]["params"]["min_rest_outstation"]
    cell[field] = {"value": 601, "unit": "count", "relation": "max",
                   "condition": {"kind": "area", "value": "TEST"},
                   "source": None, "quote": "601 минут"}[field]
    assert changed["rules"][0]["statement"] == rule["statement"]
    old, new = sv.subject_fingerprint(doc), sv.subject_fingerprint(changed)
    assert old != new
    assert any("анкет" in words for words in sv.subject_changes(doc, changed))
    report = {"schema_version": sv.REPORT_SCHEMA, "model_sha256": "a" * 64,
              "subject_sha256": old}
    assert not sv.is_fresh(report, "a" * 64, new)


@pytest.mark.parametrize("mutation", ["value", "question", "version", "target", "unapplied"])
def test_unproved_answer_is_refused_before_translator(tmp_path, monkeypatch, mutation):
    doc, rule, q = answered(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"]["min_rest_outstation"]
    if mutation == "value":
        cell["value"] = 601
    elif mutation == "question":
        cell["source"]["question"]["id"] = "Q-NOT-THERE"
    elif mutation == "version":
        cell["source"]["version"]["fingerprint"] = "0" * 12
    elif mutation == "target":
        cell["source"]["effect"]["rule"] = "min_rest_home"
    else:
        q["answer"]["status"] = "не разобран"

    def never(prompt):
        raise AssertionError("unproved answer reached translator")

    with pytest.raises(sv.SmtVerifyError, match="анкет"):
        sv.verify(doc, never)


def test_answer_revision_changes_subject_and_real_translation(tmp_path, monkeypatch):
    doc, rule, q = answered(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    opened = answers.open_revision(doc, q["id"])
    assert answers.apply_answers(doc, {opened["created"]: "540 минут"})["applied"] == 1
    assert sv.subject_fingerprint(before) != sv.subject_fingerprint(doc)
    assert sv.subject_of(doc)[0]["answer_params"]["params"]["min_rest_outstation"]["value"] == 540


def test_no_answer_subject_keeps_original_shape_and_soft_is_out():
    doc = {"rules": [{"id": "R-A", "kind": "HARD", "statement": "x <= 1"},
                     {"id": "R-S", "kind": "SOFT", "statement": "prefer x"}]}
    assert sv.subject_of(doc) == [{"id": "R-A", "kind": "HARD", "statement": "x <= 1"}]


def test_definition_stays_in_subject_and_honest_untranslatable_stays_red():
    doc = {"rules": [{"id": "D", "kind": "DEF", "statement": "См. внешний регламент."}]}

    def refuse(prompt):
        assert "D | См. внешний регламент." in prompt
        assert "kind=DEF" in prompt
        return json.dumps({"decls": [], "asserts": [{"rule": "D", "status": "untranslatable",
                                                    "note": "нет текста внешнего регламента"}]})

    got = sv.verify(doc, refuse)
    assert got["verdict"] == "none" and got["n_translated"] == 0 and got["n_rules"] == 1


@pytest.mark.parametrize("source", ["exact", "wrong-anchor", "wrong-text", "missing"])
def test_panel_form_uses_only_its_frozen_clause(tmp_path, monkeypatch, source):
    from fastapi.testclient import TestClient
    from cdpm import webui
    doc, rule, _q = answered(tmp_path, monkeypatch)
    original_statement = rule["statement"]
    rule["statement"] = "Дома отдых не менее 12:00."
    run = tmp_path / "run"
    (run / "extract").mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    (run / "extract/model.json").write_bytes(raw)
    if source != "missing":
        (run / "freeze/clauses").mkdir(parents=True)
        (run / "freeze/manifest.json").write_text(json.dumps({"files": [{"sid": "S1"}]}))
        entry = {"sid": "S1", "file": "a.md", "clauses": [{
            "anchor": "не п.1" if source == "wrong-anchor" else "п.1",
            "text": "ничего общего" if source == "wrong-text" else original_statement}]}
        (run / "freeze/clauses/S1.json").write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webui, "_run_dir", lambda rid: run)
    response = TestClient(webui.app).get("/api/runs/x/form")
    assert response.status_code == 200, response.text
    got = response.json()
    assert got["lossless"] is True
    assert (got["problems"] == []) == (source == "exact"), got["problems"]
    assert (run / "extract/model.json").read_bytes() == raw
