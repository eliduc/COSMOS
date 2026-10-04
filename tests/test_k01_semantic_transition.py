"""K01 §12.16 Д: real readers on valid, temporary 0.7 catalogue pairs.

No production catalogue release, service, LLM, solver or external consumer.
The old catalogue is physically preserved and resolved by its fingerprint.
"""
import copy
import json
import shutil

import pytest

import template_catalog as tc
from template_catalog import subject_vocabulary
from cdpm import catalog_bridge as cb, family_params as fp, model as m
from cdpm import export_task_package as tp
from test_catalog_060_versioned_schema import modern_docs
from test_k03_unit_vocabulary import attach, vocabulary
from test_k03_subject_vocabulary import vocabulary as subject_words
from test_template_catalog_validate import _broken
from test_k01_carried_conditions import FAMILY, CELLS, clean_bridge
from test_cdpm_k01_family_barrier import _doc_with, _landed


PARAM = "duty_extension_cap"


def pair(tmp_path, monkeypatch, edit, *, legacy=False, old_legacy=False):
    def prepare(docs, version, change=False, legacy_side=False):
        if not legacy_side:
            modern_docs(docs, version)
            docs.update(subject_vocabulary.attach(docs, subject_words()))
            updated = attach(docs, vocabulary())
            docs.clear()
            docs.update(updated)
        else:
            docs["catalog.json"]["version"] = version
        if change:
            edit(docs)
    old_version = "0.5.1" if legacy or old_legacy else "0.7.0"
    new_version = "0.5.2" if legacy else "0.7.1"
    old = _broken(tmp_path / "old", lambda d: prepare(
        d, old_version, legacy_side=legacy or old_legacy))
    new = _broken(tmp_path / "new", lambda d: prepare(
        d, new_version, True, legacy_side=legacy))
    old_cat = tc.load(old)
    rev = old_cat.fingerprint()
    shutil.copytree(old, new / "releases" / old_version)
    (new / "versions.json").write_text(json.dumps({"form_version": 1, "versions": [
        {"version": old_version, "fingerprint": rev, "released_at": "2026-09-15",
         "released_by": "isolated test", "code": "4359748714184e1a5bc743eec2ce1083f57041ce",
         "changes": []}]}), encoding="utf-8")
    for folder in (old, new):
        errors = [p for p in tc.load(folder).validate() if p.severity == "error"]
        assert not errors, errors
    monkeypatch.setenv(cb.ENV_CATALOG, str(new))
    cb.forget()
    assert fp._release_records(rev)[FAMILY] == old_cat.family(FAMILY)
    rule = _landed(rid="R-SEM", family=FAMILY, zone="PAIRING", kind="SOFT",
                   cells=copy.deepcopy(CELLS), rev=rev)
    rule.update(weight=3, statement="Продление с согласия на базе ST1; потолок 960 минут.")
    return _doc_with(rule), old_cat, tc.load(new)


def change_scale(docs):
    entry = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
    for word, scale in list(entry["scale"].items()):
        if scale == 60:
            entry["scale"][word] = 90


def family_note(docs):
    next(r for r in docs["families.json"]["families"] if r["id"] == FAMILY)["params"][0]["note"] = "Пояснение изменено"


def other_unit(docs):
    entry = docs["dictionaries.json"]["unit_words"]["time_of_day_minutes"]
    entry["words"].append("новое слово времени")


def equivalent_word(docs):
    entry = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
    entry["words"].append("добавочный час")
    entry["scale"]["добавочный час"] = 60


def test_dictionary_change_reaches_all_family_readers(tmp_path, monkeypatch):
    doc, old, new = pair(tmp_path, monkeypatch, change_scale)
    # CDPM-429: this negative must carry the actually changed word.
    # The old default "960 минут" was a false-ban oracle; the separate
    # citation pair now proves it remains compatible when hours change.
    doc["rules"][-1]["template_fit"]["params"][PARAM]["quote"] = "16 ч"
    assert old.family(FAMILY) == new.family(FAMILY)
    assert old.fingerprint() != new.fingerprint()
    from template_catalog import units
    assert units.to_canonical(10, "часов", "duration_minutes", source=old.path) == 600
    assert units.to_canonical(10, "часов", "duration_minutes", source=new.path) == 900
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert not got["missing"] and not got["curable"]
    assert any("множитель" in x for x in got["contract"]["differences"])
    assert any("множитель" in x for x in m.validate_model(doc))
    assert not m.readiness(doc)["pairing_ready"]
    assert not m.readiness_matrix(doc)["pairing"]["optimization"]["ready"]
    modes, _ = tp.allowed_modes(doc)
    assert "optimization" not in modes["pairing"]
    assert fp.ensure_family_param_questions(doc) == []


@pytest.mark.parametrize("edit,ready", [(family_note, True), (change_scale, False)])
def test_readiness_and_modes_distinguish_semantics_with_k06_isolated(
        tmp_path, monkeypatch, edit, ready):
    doc, _old, _new = pair(tmp_path, monkeypatch, edit)
    doc["rules"][-1]["template_fit"]["params"][PARAM]["quote"] = "16 ч"
    # Temporary catalogue has no matching released executor attestation.
    # Isolate ONLY neighbour K06, not the K01 judge or its readers. This
    # proves their wiring, not a released executor or a commercial RC.
    monkeypatch.setattr(m, "support_problems", lambda _doc, _spec_version=None: [])
    got = m.readiness(doc)
    assert got["pairing_ready"] is ready, got
    modes, reasons = tp.allowed_modes(doc)
    assert ("feasibility-soft" in modes["pairing"]) is ready, (modes, reasons)
    barrier_words = [p["words"] for p in m.readiness_barriers(doc)]
    assert any("множитель" in w for w in barrier_words) is (not ready)


@pytest.mark.parametrize("edit", [family_note, other_unit, equivalent_word])
def test_unrelated_and_equivalent_edits_preserve_complete_landing(tmp_path, monkeypatch, edit):
    doc, _old, _new = pair(tmp_path, monkeypatch, edit)
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert got["verdict"] == fp.VERDICT_COMPLETE
    assert got["contract"]["differences"] == []


@pytest.mark.parametrize("missing", [False, True])
def test_legacy_dictionary_gap_is_not_a_fabricated_relanding(tmp_path, monkeypatch, missing):
    doc, _old, _new = pair(tmp_path, monkeypatch, family_note, legacy=True)
    if missing:
        doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["missing"] == ([PARAM] if missing else [])
    assert got["verdict"] == (fp.VERDICT_INCOMPLETE if missing else fp.VERDICT_COMPLETE)
    assert got["curable"]
    if missing:
        words = " ".join(p["words"] for p in got["problems"])
        assert "карты слов единиц" in words
        assert "не числится ни за одной" not in words


def test_pre_apply_guard_uses_the_same_dictionary_transition(tmp_path, monkeypatch):
    doc, old, new = pair(tmp_path, monkeypatch, change_scale)
    monkeypatch.setenv(cb.ENV_CATALOG, str(old.path))
    cb.forget()
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    healthy = copy.deepcopy(doc)
    healthy_q = next(q for q in healthy["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(healthy, healthy_q, "10 часов", 2).get("applied"), healthy_q.get("answer")
    monkeypatch.setenv(cb.ENV_CATALOG, str(new.path))
    cb.forget()
    before = copy.deepcopy(doc["rules"])
    result = fp.apply_param_answer(doc, q, "10 часов", 2)
    assert not result.get("applied")
    assert doc["rules"] == before
    assert "множитель" in q["answer"].get("note", ""), q["answer"]


@pytest.mark.parametrize("edit", [family_note, other_unit, equivalent_word])
def test_old_question_still_accepts_answer_after_equivalent_transition(
        tmp_path, monkeypatch, edit):
    doc, old, new = pair(tmp_path, monkeypatch, edit)
    monkeypatch.setenv(cb.ENV_CATALOG, str(old.path))
    cb.forget()
    doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    monkeypatch.setenv(cb.ENV_CATALOG, str(new.path))
    cb.forget()
    result = fp.apply_param_answer(doc, q, "10 часов", 2)
    assert result.get("applied"), q["answer"]
    assert doc["rules"][-1]["template_fit"]["params"][PARAM]["value"] == 600
    assert fp.family_completeness(doc)[FAMILY]["verdict"] == fp.VERDICT_COMPLETE


@pytest.mark.parametrize("missing", [False, True])
def test_legacy_to_modern_dictionary_is_unproven_not_false_relanding(
        tmp_path, monkeypatch, missing):
    doc, old, new = pair(tmp_path, monkeypatch, lambda _d: None, old_legacy=True)
    assert "unit_words" not in old.dictionaries()
    assert "unit_words" in new.dictionaries()
    if missing:
        doc["rules"][-1]["template_fit"]["params"].pop(PARAM)
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["missing"] == ([PARAM] if missing else [])
    assert got["verdict"] == (fp.VERDICT_INCOMPLETE if missing else fp.VERDICT_COMPLETE)
    assert got["curable"] and not got["contract"]["differences"]
    assert any("карты слов единиц" in r for r in got["contract"]["unproven_reasons"])
    questions = fp.ensure_family_param_questions(doc)
    assert len(questions) == (1 if missing else 0)


def test_unknown_carried_name_has_its_own_unproven_reason(tmp_path, monkeypatch):
    doc, _old, _new = pair(tmp_path, monkeypatch, family_note)
    doc["rules"][-1]["template_fit"]["params"]["never_declared"] = {"value": 5}
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["missing"] == []
    assert any("never_declared" in x for x in got["contract"]["unproven_reasons"])
    assert any("never_declared" in x for x in m.validate_model(doc))


def test_missing_revision_remains_named_in_unproven_diagnostic():
    rule = _landed()
    rule["template_fit"].pop("registry_rev")
    got = fp.family_completeness(_doc_with(rule))["rest_floors"]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["missing"] == ["min_rest_outstation"]
    words = " ".join(p["words"] for p in got["problems"])
    assert "совместимость посадки не доказана" in words
    assert "без ревизии" in words
    assert got["contract"]["differences"] == []


def test_disappeared_unit_word_is_unproven_not_a_proven_change(tmp_path, monkeypatch):
    def remove(docs):
        entry = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
        entry["words"].remove("часов")
        entry["scale"].pop("часов")
    doc, _old, _new = pair(tmp_path, monkeypatch, remove)
    doc["rules"][-1]["template_fit"]["params"][PARAM]["quote"] = "16 часов"
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["verdict"] == fp.VERDICT_COMPLETE
    assert any("часов" in x for x in got["contract"]["unproven_reasons"])
    assert not any(p.get("proven") for p in got["problems"])


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_scale_map_refuses_ambiguous_historical_normalization(reverse):
    from template_catalog import units
    entries = [("час", 60), ("час.", 90)]
    if reverse:
        entries.reverse()
    entry = {"canon": "минута", "form": "integer_nonneg",
             "words": [w for w, _ in entries], "scale": dict(entries)}
    with pytest.raises(units.UnitsError, match="разные множители"):
        units.scale_map(entry, "duration_minutes")
    with pytest.raises(units.UnitsError, match="разные множители"):
        units._multiplier(entry, "duration_minutes", "duration_minutes", {"duration_minutes": entry})
    entry["scale"]["час."] = 60
    assert units.scale_map(entry, "duration_minutes") == {"час": 60}
