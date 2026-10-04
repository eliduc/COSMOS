"""Typed answer evidence remains literal across independent read surfaces.

Real temporary catalogues, question/answer writer, measurer and Markdown
renderer. No model, export, solver, process or network execution.
"""
import copy

import pytest

from cdpm import answers, catalog_bridge as cb, measure, model as m, provenance, render
from template_catalog import family_contract
from test_cdpm_a05_cell_writer import _doc, FAMILY, PARAM, OTHER
from test_k01_semantic_transition import pair, clean_bridge


def answered_pair(tmp_path, monkeypatch, current_unit):
    def edit(docs):
        record = next(r for r in docs["families.json"]["families"] if r["id"] == FAMILY)
        next(p for p in record["params"] if p["name"] == PARAM)["unit"] = current_unit

    _, old, new = pair(tmp_path, monkeypatch, edit)
    monkeypatch.setenv(cb.ENV_CATALOG, str(old.path))
    cb.forget()
    doc, rule, q = _doc()
    fit = rule["template_fit"]
    fit["schema_version"] = "landing/2"
    fit["family_contract"] = family_contract.snapshot(old, FAMILY, fit["params"])
    fit["params"][OTHER].update(unit="duration_minutes", relation="min", condition=None,
        source={"kind": "rule", "rule_id": rule["id"]})
    assert answers.apply_answers(doc, {q["id"]: "10 часов"})["applied"] == 1
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    assert provenance.package_problems(doc) == []
    return doc, rule, q, new


@pytest.mark.parametrize("current_unit", ["duration_minutes", "count", "time_of_day_minutes"])
def test_historical_typed_answer_display_uses_recorded_unit_not_current_catalogue(
        tmp_path, monkeypatch, current_unit):
    doc, rule, q, new = answered_pair(tmp_path, monkeypatch, current_unit)
    cell = rule["template_fit"]["params"][PARAM]
    assert cell["value"] == 600 and cell["unit"] == "duration_minutes"
    before = copy.deepcopy(doc)
    monkeypatch.setenv(cb.ENV_CATALOG, str(new.path))
    cb.forget()
    assert m.fit_param_unit(FAMILY, PARAM) == current_unit  # Reach changed real catalogue.
    text = render.render_svod(doc)
    line = next(s for s in text.splitlines() if s.startswith("| R-REST |"))
    assert "значение в модели: ` 600 `; единица: ` duration_minutes `" in line
    assert doc == before
    assert cell["source"]["question"]["id"] == q["id"]


@pytest.mark.parametrize("unit", [None, False, [], "", " "])
def test_invalid_typed_unit_is_not_completed_from_catalogue(tmp_path, monkeypatch, unit):
    doc, rule, _q, _new = answered_pair(tmp_path, monkeypatch, "duration_minutes")
    rule["template_fit"]["params"][PARAM]["unit"] = unit
    before = copy.deepcopy(doc)
    text = render.render_svod(doc)
    line = next(s for s in text.splitlines() if s.startswith("| R-REST |"))
    assert "значение в модели: ` 600 `; единица не указана в типизированной ячейке" in line
    assert doc == before


@pytest.mark.parametrize("forged", [False, True])
def test_measurer_and_source_display_keep_answer_carrier_separate_from_clause(
        tmp_path, monkeypatch, forged):
    doc, rule, q, _new = answered_pair(tmp_path, monkeypatch, "duration_minutes")
    cell = rule["template_fit"]["params"][PARAM]
    if forged:
        cell["source"]["version"]["fingerprint"] = "0" * 12
    before = copy.deepcopy(doc)
    fields = measure.field_values(rule)
    answer_field = next(f for f in fields if f["field"] == "params." + PARAM)
    assert answer_field["carrier_answer"] == q["id"]
    # The measurer classifies the carrier; it does not validate answer identity.
    assert not any(f["field"] == "params." + PARAM
                   for f in measure.carriers(rule["statement"], rule))
    src = rule["sources"][0]
    report = measure.measure(doc, [{"sid": src["sid"], "anchor": src["anchor"],
                                   "text": rule["statement"]}])
    assert report["counts"]["fields_without_carrier"] == 0
    assert measure.verdict(report) == (True, [])
    assert "Вердикт структурных классов: **ЧИСТО**" in measure.render_report(report)
    problems = provenance.package_problems(doc)
    text = render.render_svod(doc)
    line = next(s for s in text.splitlines() if s.startswith("| R-REST |"))
    if forged:
        assert problems and "источник ответа не разрешён" in line
        assert "cdpm-answer-" not in line
    else:
        assert problems == [] and "cdpm-answer-" in line
        assert "10 часов" in text and q["id"] in line
    assert doc == before


def test_two_real_typed_revisions_keep_exact_source_through_readers(tmp_path, monkeypatch):
    doc, rule, q, _new = answered_pair(tmp_path, monkeypatch, "duration_minutes")
    root_id = q["id"]
    for raw, expected in [("9 часов", 540), ("8 часов", 480)]:
        old = copy.deepcopy(rule["template_fit"]["params"][PARAM])
        qid = answers.open_revision(doc, q["id"])["created"]
        q = next(q for q in doc["questions"] if q["id"] == qid)
        assert answers.apply_answers(doc, {qid: raw})["applied"] == 1
        cell = rule["template_fit"]["params"][PARAM]
        before = copy.deepcopy(doc)
        assert cell["value"] == expected and cell["source"]["root"] == root_id
        assert provenance.package_problems(doc) == []
        assert provenance.resolve_source(doc, cell["source"])[1] == raw
        line = next(s for s in render.render_svod(doc).splitlines()
                    if s.startswith("| R-REST |"))
        assert qid in line and provenance.answer_version_fingerprint(raw) in line
        assert f"значение в модели: ` {expected} `" in line
        assert doc == before
        corrupted = copy.deepcopy(doc)
        corrupted["rules"][0]["template_fit"]["params"][PARAM] = old
        assert provenance.package_problems(corrupted)
        line = next(s for s in render.render_svod(corrupted).splitlines()
                    if s.startswith("| R-REST |"))
        assert "источник ответа не разрешён" in line and "cdpm-answer-" not in line
