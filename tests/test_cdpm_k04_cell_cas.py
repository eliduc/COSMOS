"""K04 cell-level compare-and-swap; synthetic models, no server or solver."""
import copy

import pytest

from cdpm import answers, family_params as fp, model as m, render
from test_cdpm_k04_revision_barrier import (
    _doc_after_conflicting_revision, _q, PARAM, OTHER_PARAM, REV_Q,
    FAMILY, ROOT_Q, REV,
)


@pytest.mark.parametrize("raw", ["9 часов", "не применяется"])
def test_revision_cannot_overwrite_a_late_change_to_its_cell(raw):
    doc, rule = _doc_after_conflicting_revision()
    cell = rule["template_fit"]["params"][PARAM]
    cell["value"] = 660
    cell["quote"] = "позднее согласовано 11 часов"
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    before = copy.deepcopy(rule)

    result = answers.apply_answers(doc, {REV_Q: raw})

    assert result["applied"] == 0
    assert result["conflicted"] == 1 and result["unparsed"] == 0
    assert rule == before
    answer = _q(doc, REV_Q)["answer"]
    assert answer["raw"] == raw
    assert "пересмотр: конфликт" in answer.get("note", "").lower()
    assert answer["status"] == m.REVISION_CONFLICT_STATUS
    assert answer["conflicts"]
    assert "посадка" in answer["conflicts"][0]
    assert m.is_open_question(_q(doc, REV_Q))
    assert m.pending_revision_facts(doc)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []


def test_revision_preserves_a_late_change_to_another_cell():
    doc, rule = _doc_after_conflicting_revision()
    other = rule["template_fit"]["params"][OTHER_PARAM]
    other["value"] = 780
    other["quote"] = "позднее согласовано дома 13 часов"
    before = copy.deepcopy(other)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []

    result = answers.apply_answers(doc, {REV_Q: "9 часов"})

    assert result["applied"] == 1
    assert rule["template_fit"]["params"][PARAM]["value"] == 540
    assert rule["template_fit"]["params"][OTHER_PARAM] == before
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []


@pytest.mark.parametrize("change", ["version", "deletion", "quote"])
def test_revision_detects_source_version_and_other_same_cell_edits(change):
    doc, rule = _doc_after_conflicting_revision()
    cells = rule["template_fit"]["params"]
    if change == "version":
        cells[PARAM]["source"]["version"]["fingerprint"] = "f" * 64
    elif change == "quote":
        cells[PARAM]["quote"] = "уточнённая цитата"
    else:
        del cells[PARAM]
    before = copy.deepcopy(rule)
    assert answers.apply_answers(doc, {REV_Q: "9 часов"})["applied"] == 0
    assert rule == before
    assert _q(doc, REV_Q)["answer"]["status"] == m.REVISION_CONFLICT_STATUS


@pytest.mark.parametrize("raw", ["9 часов", "не применяется"])
def test_all_write_and_drop_targets_are_checked_before_any_mutation(raw):
    doc, rule = _doc_after_conflicting_revision()
    late = copy.deepcopy(rule)
    late["id"] = "R-LATE"
    late["template_fit"]["params"][PARAM]["value"] = 660
    doc["rules"].append(late)
    before = copy.deepcopy(doc["rules"])
    assert answers.apply_answers(doc, {REV_Q: raw})["applied"] == 0
    assert doc["rules"] == before
    assert "R-LATE" in str(_q(doc, REV_Q)["answer"]["conflicts"])


def test_retry_and_sync_do_not_silently_refresh_conflicted_preconditions():
    doc, rule = _doc_after_conflicting_revision()
    q = _q(doc, REV_Q)
    baseline = copy.deepcopy(q["revision_cells"])
    rule["template_fit"]["params"][PARAM]["value"] = 660
    assert answers.apply_answers(doc, {REV_Q: "9 часов"})["applied"] == 0
    fp.ensure_family_param_questions(doc)
    assert answers.apply_answers(doc, {REV_Q: "8 часов"})["applied"] == 0
    assert q["revision_cells"] == baseline
    assert q["answer"]["raw"] == "8 часов"
    assert rule["template_fit"]["params"][PARAM]["value"] == 660


@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_reconsideration_resolves_conflict_without_rolling_back_late_edit(legacy):
    doc, rule = _doc_after_conflicting_revision()
    if legacy:
        _q(doc, REV_Q).pop("revision_cells")
    rule["template_fit"]["params"][PARAM]["value"] = 660
    assert answers.apply_answers(doc, {REV_Q: "9 часов"})["applied"] == 0
    opened = answers.open_revision(doc, REV_Q)
    new_id = opened["created"]
    assert new_id == REV_Q + "-2" and opened["restored"] is False
    assert "НЕ применён из-за конфликта" in _q(doc, new_id)["text"]
    assert rule["template_fit"]["params"][PARAM]["value"] == 660
    assert _q(doc, REV_Q)["answer"]["revised_by"] == new_id
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    assert answers.apply_answers(doc, {REV_Q: "8 часов"})["already_answered"] == 1
    assert answers.apply_answers(doc, {new_id: "9 часов"})["applied"] == 1
    assert rule["template_fit"]["params"][PARAM]["value"] == 540
    assert m.pending_revision_facts(doc) == []
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []


def test_conflict_is_visible_in_both_human_documents():
    doc, rule = _doc_after_conflicting_revision()
    rule["template_fit"]["params"][PARAM]["value"] = 660
    answers.apply_answers(doc, {REV_Q: "9 часов"})
    for text in (render.render_anketa(doc), render.render_svod(doc)):
        assert "**Пересмотр: конфликт.**" in text
        assert f"template_fit.params.{PARAM}" in text
        assert "Неприменённый ответ сохранён: «9 часов»" in text


def test_revision_with_successful_rollback_captures_absence_and_applies_normally():
    from cdpm.sample import sample_model
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {"category": "A", "registry_rev": REV,
        "family": FAMILY, "params": {OTHER_PARAM: {"value": 720, "quote": "12 часов"}}}
    assert fp.ensure_family_param_questions(doc) == [ROOT_Q]
    assert answers.apply_answers(doc, {ROOT_Q: "10 часов"})["applied"] == 1
    assert answers.open_revision(doc, ROOT_Q)["restored"] is True
    assert PARAM not in rule["template_fit"]["params"]
    assert answers.apply_answers(doc, {REV_Q: "9 часов"})["applied"] == 1
    assert rule["template_fit"]["params"][PARAM]["value"] == 540
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []


@pytest.mark.parametrize("change", ["removed", "new_owner"])
def test_decline_cannot_hide_change_to_the_originally_owned_cell(change):
    doc, rule = _doc_after_conflicting_revision()
    cells = rule["template_fit"]["params"]
    if change == "removed":
        del cells[PARAM]
    else:
        cells[PARAM] = {"value": 660, "quote": "из уточнённой клаузы"}
    before = copy.deepcopy(rule)
    assert answers.apply_answers(doc, {REV_Q: "не применяется"})["applied"] == 0
    assert rule == before


def test_actual_reconsider_api_reopens_conflict_and_persists_the_new_baseline(tmp_path, monkeypatch):
    import json
    from cdpm import webui
    from test_cdpm_k04_revision_barrier import _run
    doc, rule = _doc_after_conflicting_revision()
    rule["template_fit"]["params"][PARAM]["value"] = 660
    assert answers.apply_answers(doc, {REV_Q: "9 часов"})["applied"] == 0
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    run = _run(tmp_path / "runs", doc, "run-9417")
    (run / "status.json").write_text(json.dumps({"state": "готов"}), encoding="utf-8")
    result = webui.reconsider_question("run-9417", REV_Q)
    after = m.load_model(run / "extract/model.json")
    new_q = _q(after, result["created"])
    assert m.is_open_question(new_q) and new_q["revision_cells"]["targets"]
    after_rule = next(r for r in after["rules"] if r["id"] == rule["id"])
    assert after_rule["template_fit"]["params"][PARAM]["value"] == 660
    assert answers.apply_answers(after, {result["created"]: "9 часов"})["applied"] == 1
    assert m.validate_schema(after) == [] and m.validate_model(after) == []


def test_conflict_status_requires_a_revision_and_named_reasons():
    doc, rule = _doc_after_conflicting_revision()
    rule["template_fit"]["params"][PARAM]["value"] = 660
    answers.apply_answers(doc, {REV_Q: "9 часов"})
    _q(doc, REV_Q)["answer"].pop("conflicts")
    assert any("без перечня причин" in p for p in m.validate_model(doc))
    root = _q(doc, ROOT_Q)
    root["answer"] = copy.deepcopy(_q(doc, REV_Q)["answer"])
    assert any("не является пересмотром" in p for p in m.validate_model(doc))


def test_xlsx_conflict_explains_reason_but_keeps_the_airline_answer_slot_empty():
    from cdpm import anketa_xlsx as ax
    doc, rule = _doc_after_conflicting_revision()
    rule["template_fit"]["params"][PARAM]["value"] = 660
    answers.apply_answers(doc, {REV_Q: "9 часов"})
    row = ax.question_row(_q(doc, REV_Q), {"S1": "a.md"})
    assert "Пересмотр: конфликт" in row[1]
    assert f"template_fit.params.{PARAM}" in row[1]
    assert "«9 часов»" in row[1]
    assert row[5] is None


def test_real_book_upload_preserves_conflict_without_calling_a_model(tmp_path, monkeypatch):
    import json
    from cdpm import webui
    from test_cdpm_k04_revision_barrier import _run, _filled_book, _wait_stage
    doc, rule = _doc_after_conflicting_revision()
    # Issue the answer book before the late change: the edition remains valid,
    # but cell-level preconditions must still reject its outdated operation.
    book = _filled_book(tmp_path, doc, {REV_Q: "9 часов"})
    rule["template_fit"]["params"][PARAM]["value"] = 660
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    run = _run(tmp_path / "runs", doc, "run-9418")
    (run / "status.json").write_text(json.dumps({"state": "готов"}), encoding="utf-8")
    def no_channel(*args, **kwargs):
        def fail(*args, **kwargs):
            raise AssertionError("No model may handle this deterministic revision")
        return fail
    monkeypatch.setattr(webui, "_channel_for", no_channel)
    webui._upload_answers_bytes("run-9418", "answers.xlsx", book)
    status = _wait_stage(run)
    assert status["state"] == "ответы применены", status
    assert status["answers_summary"]["conflicted"] == 1
    assert status["answers_summary"]["applied"] == 0
    after = m.load_model(run / "extract/model.json")
    assert _q(after, REV_Q)["answer"]["status"] == m.REVISION_CONFLICT_STATUS
    assert _q(after, REV_Q)["answer"]["raw"] == "9 часов"
    after_rule = next(r for r in after["rules"] if r["id"] == rule["id"])
    assert after_rule["template_fit"]["params"][PARAM]["value"] == 660
    assert m.is_open_question(_q(after, REV_Q))
    assert m.validate_schema(after) == [] and m.validate_model(after) == []


def test_answer_gate_recounts_conflicts_and_rejects_a_forged_count(tmp_path):
    import json
    from cdpm.gates import gate_answers
    doc, rule = _doc_after_conflicting_revision()
    rule["template_fit"]["params"][PARAM]["value"] = 660
    summary = answers.apply_answers(doc, {REV_Q: "9 часов"})
    model_path = tmp_path / "model.json"
    summary_path = tmp_path / "summary.json"
    m.dump_model(doc, model_path)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    problems, counts = gate_answers.check(model_path, summary_path)
    assert problems == [] and counts["conflicted"] == 1
    summary["conflicted"] = 2
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    problems, _ = gate_answers.check(model_path, summary_path)
    assert any("conflicted=2" in p for p in problems)
