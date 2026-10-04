"""Named scope delta from DECISION-K01-CARRIED-SCOPE-CDPM160.

The same parameter/field change with and without a physical cell. A bad
cell still counts as carried; missing/declined values do not invent a cell.
"""
import copy

import pytest

from cdpm import family_params as fp
from cdpm import answers, catalog_bridge as cb, model as m, question_editions
import catalog_copies as cc
import template_catalog as tc
from test_k01_carried_conditions import CELLS, FAMILY, changed_catalog, clean_bridge
from test_cdpm_k01_family_barrier import _doc_with, _landed


PARAM = "duty_extension_cap"


@pytest.mark.parametrize("field,value", [
    ("unit", "credit_hours"), ("relation", "min"),
])
@pytest.mark.parametrize("physical", ["absent", "valid", "unreadable", "no_value"])
def test_same_changed_parameter_with_and_without_a_physical_cell(
        tmp_path, monkeypatch, field, value, physical):
    def edit(rec):
        next(p for p in rec["params"] if p["name"] == PARAM)[field] = value
    doc = changed_catalog(tmp_path, monkeypatch, edit)
    cells = doc["rules"][-1]["template_fit"]["params"]
    if physical == "absent":
        cells.pop(PARAM)
    elif physical == "unreadable":
        cells[PARAM]["value"] = "нечисловая ячейка"
    elif physical == "no_value":
        cells[PARAM].pop("value")
    got = fp.family_completeness(doc)[FAMILY]
    if physical != "absent":
        assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
        assert got["verdict"] == fp.VERDICT_RELAND and not got["curable"]
        assert any(PARAM in x for x in got["contract"]["differences"])
        assert fp.ensure_family_param_questions(doc) == []
    else:
        # §12.16 Д: legacy releases have no unit_words. That uncertainty
        # must preserve the same curable shortage, not invent relanding.
        assert got["contract_state"] == fp.CONTRACT_UNPROVEN
        assert all("карты слов единиц" in x for x in got["contract"]["unproven_reasons"])
        assert got["verdict"] == fp.VERDICT_INCOMPLETE and got["curable"]
        assert got["missing"] == [PARAM]
        assert got["contract"]["differences"] == []
        questions = fp.ensure_family_param_questions(doc)
        assert len(questions) == 1
        assert questions[0].endswith(f"параметр-{FAMILY}-{PARAM}")


@pytest.mark.parametrize("carried", [set(), {"p"}])
def test_qualifier_scope_on_the_same_count_parameter(carried):
    # Pure projection: base_code is legal for count, not duration_minutes.
    # No claim that this minimal record is an independently released family.
    old = {"params": [{"name": "p", "unit": "count", "relation": "max",
                       "qualifier": None}]}
    new = {"params": [{"name": "p", "unit": "count", "relation": "max",
                       "qualifier": "base_code"}]}
    breaks = fp._contract_breaks(fp._contract_shape(old), fp._contract_shape(new), carried)
    assert bool(breaks) == bool(carried)
    if carried:
        assert "изменился уточнитель" in breaks[0]


@pytest.mark.parametrize("answer", ["960 минут", "Не применяется"])
def test_missing_changed_parameter_is_really_cured_by_its_answer(
        tmp_path, monkeypatch, answer):
    def edit(rec):
        next(p for p in rec["params"] if p["name"] == PARAM)["relation"] = "min"
    doc = changed_catalog(tmp_path, monkeypatch, edit)
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, answer, 2)["applied"]
    got = fp.family_completeness(doc)[FAMILY]
    assert got["verdict"] == fp.VERDICT_COMPLETE
    assert got["missing"] == [] and got["contract"]["differences"] == []


def old_missing_document():
    cells = copy.deepcopy(CELLS)
    cells.pop(PARAM)
    rule = _landed(rid="R-DEP", zone="PAIRING", kind="SOFT", family=FAMILY,
                   cells=cells, rev=m.registry_fingerprint())
    rule.update(weight=3, statement="Продление с согласия на базе ST1; "
                "потолок 960 минут, порог 720 минут.")
    return _doc_with(rule)


def relation_change(rec, parameter=PARAM):
    next(p for p in rec["params"] if p["name"] == parameter)["relation"] = "min"


@pytest.mark.parametrize("parameter", [PARAM, "long_duty_threshold"])
def test_answer_before_catalogue_change_does_not_make_any_old_cell_fresh(
        tmp_path, monkeypatch, parameter):
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    old_rev = doc["rules"][-1]["template_fit"]["registry_rev"]
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    assert q["edition"]["parameter_contract"]["registry_rev"] == old_rev
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    changed_catalog(tmp_path, monkeypatch, lambda rec: relation_change(rec, parameter))
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert any(parameter in x for x in got["contract"]["differences"])
    assert doc["rules"][-1]["template_fit"]["registry_rev"] == old_rev


def test_question_issued_before_change_cannot_apply_using_new_meaning(tmp_path, monkeypatch):
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    question_editions.freeze(q)
    before = copy.deepcopy(doc["rules"])
    changed_catalog(tmp_path, monkeypatch, relation_change)
    assert not fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    assert "контракт выданного вопроса изменился" in q["answer"]["note"]
    assert doc["rules"] == before


@pytest.mark.parametrize("corrupt", ["pin", "source_version", "source_only_tag"])
def test_new_answer_requires_exact_immutable_context_not_just_an_answer_tag(
        tmp_path, monkeypatch, corrupt):
    doc = changed_catalog(tmp_path, monkeypatch, relation_change)
    rule = doc["rules"][-1]
    rule["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    cell = rule["template_fit"]["params"][PARAM]
    if corrupt == "pin":
        q["parameter_contract"]["registry_rev"] = rule["template_fit"]["registry_rev"]
        assert question_editions.problems(q)
    elif corrupt == "source_version":
        cell["source"]["version"]["fingerprint"] = "0" * 12
    else:
        cell["source"] = {"kind": "answer"}
    assert fp.family_completeness(doc)[FAMILY]["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert not m.readiness(doc)["pairing_ready"]


def test_new_answer_does_not_clear_a_different_old_cell_after_next_change(
        tmp_path, monkeypatch):
    doc = changed_catalog(tmp_path, monkeypatch, relation_change)
    rule = doc["rules"][-1]
    rule["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    before = copy.deepcopy(rule)
    def edit(rec):
        relation_change(rec)
        relation_change(rec, "long_duty_threshold")
    folder = cc.copy_catalog(tmp_path, families={FAMILY: edit}, releases=True,
                             candidates=False, version="0.5.2", name="next-catalog")
    assert not [x for x in tc.load(folder).validate() if x.severity == "error"]
    cc.use_catalog(monkeypatch, folder)
    cb.forget()
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert got["contract"]["differences"]
    assert all("long_duty_threshold" in x for x in got["contract"]["differences"])
    assert rule == before  # Neither a reader nor answering restamped the row.


def test_reconsideration_preserves_exact_catalogue_context(tmp_path, monkeypatch):
    doc = changed_catalog(tmp_path, monkeypatch, relation_change)
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    rid = answers.open_revision(doc, qid)["created"]
    revision = next(q for q in doc["questions"] if q["id"] == rid)
    assert revision["parameter_contract"] == q["parameter_contract"]
    issued = revision["edition"]["fingerprint"]
    assert fp.apply_param_answer(doc, revision, "900 минут", 3)["applied"]
    assert revision["edition"]["fingerprint"] == issued
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    assert revision["edition"]["parameter_contract"] == q["parameter_contract"]


@pytest.mark.parametrize("legacy", [False, True])
def test_stale_question_is_reissued_not_restamped_and_new_answer_heals(
        tmp_path, monkeypatch, legacy):
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    if legacy:
        q.pop("parameter_contract")
        q.pop("edition", None)  # Explicit fixture for the pre-context format.
    question_editions.freeze(q, legacy=legacy)
    before = copy.deepcopy(q)
    changed_catalog(tmp_path, monkeypatch, relation_change)
    notes = []
    created = fp.ensure_family_param_questions(doc, notes)
    assert len(created) == 1 and created[0] != qid, "\n".join(notes)
    new = next(q for q in doc["questions"] if q["id"] == created[0])
    assert q == before
    assert new["edition_of"] == qid
    assert new["parameter_contract"]["registry_rev"] == cb.stamp()["fingerprint"]
    assert not question_editions.problems(new)
    assert m.validate_schema(doc) == []
    assert fp.apply_param_answer(doc, new, "960 минут", 2)["applied"]
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    assert fp.ensure_family_param_questions(doc) == []
    assert m.validate_schema(doc) == []


def test_new_question_identity_is_frozen_before_its_answer():
    from cdpm import anketa_xlsx
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert q["edition"]["parameter_contract"] == q["parameter_contract"]
    issued = anketa_xlsx.question_fingerprint(q)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    source = doc["rules"][-1]["template_fit"]["params"][PARAM]["source"]
    assert source["question"]["fingerprint"] == issued
    assert anketa_xlsx.question_fingerprint(q) == issued
    assert m.validate_schema(doc) == []


def test_real_answer_book_keeps_the_issued_contract_identity(tmp_path, monkeypatch):
    from test_cdpm_question_editions import _filled
    from cdpm import anketa_xlsx
    doc = changed_catalog(tmp_path, monkeypatch, relation_change)
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    issued = anketa_xlsx.question_fingerprint(q)
    book = _filled(tmp_path, doc, q)
    reading = answers.read_book(book, doc)
    assert reading.answers[qid] == "600 минут"
    assert answers.apply_answers(doc, reading.answers, None)["applied"] == 1
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    source = doc["rules"][-1]["template_fit"]["params"][PARAM]["source"]
    assert source["question"]["fingerprint"] == issued
    assert m.validate_schema(doc) == []


def test_unfrozen_context_cannot_be_added_while_applying_an_answer():
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    q.pop("edition")
    before = copy.deepcopy(doc["rules"])
    assert not fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    assert doc["rules"] == before
    assert "edition" not in q


@pytest.mark.parametrize("change", [False, True])
def test_self_healing_uses_current_compatibility_not_answer_age(tmp_path, monkeypatch, change):
    doc = old_missing_document()
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    old_answer = copy.deepcopy(q)
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    if change:
        changed_catalog(tmp_path, monkeypatch, relation_change)
    notes = []
    created = fp.ensure_family_param_questions(doc, notes)
    cells = doc["rules"][-1]["template_fit"]["params"]
    if change:
        assert PARAM not in cells
        assert any("контракт выданного вопроса изменился" in note for note in notes)
        assert q == old_answer
        assert len(created) == 1 and created[0] != qid
        new = next(q for q in doc["questions"] if q["id"] == created[0])
        assert fp.apply_param_answer(doc, new, "900 минут", 3)["applied"]
    else:
        assert cells[PARAM]["value"] == 960
        assert not notes and created == []
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE


def test_new_answer_does_not_shadow_the_same_old_parameter_on_another_carrier(
        tmp_path, monkeypatch):
    doc = changed_catalog(tmp_path, monkeypatch, relation_change)
    rule = doc["rules"][-1]
    other = copy.deepcopy(rule)
    other["id"] = "R-OLD-SAME-PARAM"
    rule["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(doc, q, "960 минут", 2)["applied"]
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE
    doc["rules"].append(other)
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert any(PARAM in x for x in got["contract"]["differences"])


def test_edition_integrity_does_not_need_its_retired_name_in_todays_catalogue():
    # Pure identity check, NOT a released-catalogue or applicability verdict.
    q = {"id": "Q-3a-параметр-retired_family-retired_parameter", "part": "3a",
         "title": "Исторический вопрос", "text": "Каково значение?",
         "answer_format": {"type": "value", "note": "минуты"},
         "rule_refs": ["R-OLD"], "source_refs": [{"sid": "S1", "anchor": "П.1"}],
         "parameter_contract": {"family": "retired_family", "parameter": "retired_parameter",
                                "registry_rev": "a" * 64}}
    question_editions.freeze(q)
    assert question_editions.problems(q) == []
    assert fp.question_target(q) is None  # Not accepted by today's catalogue.
