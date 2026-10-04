"""K01 §12.16 Е: a named reference is not set coverage.

Real local catalogue pairs; no executor, release or live service.
"""
import copy

import pytest

from cdpm import catalog_bridge as cb, model as m
from test_k01_semantic_transition import pair, clean_bridge


FAMILY = "days_off_floor"


def record(docs):
    return next(r for r in docs["families.json"]["families"] if r["id"] == FAMILY)


def unchanged(_docs):
    pass


def rename(docs):
    for item in record(docs)["param_sets"]:
        item["id"] = "renamed_" + item["id"]


def swap(docs):
    sets = record(docs)["param_sets"]
    sets[0]["params"], sets[1]["params"] = sets[1]["params"], sets[0]["params"]


def added_set(docs):
    record(docs)["param_sets"].append({"id": "additional", "params": ["days_off_window_days"], "note": "Новый независимый набор"})


def expanded(docs):
    record(docs)["param_sets"][0]["params"].append("days_off_window_days")
    # This alternative cannot silently replace the original monthly choice.
    record(docs)["param_sets"].append({"id": "alternative", "params": ["min_days_off"], "note": "Иной выбор"})


def note_and_order(docs):
    rec = record(docs)
    rec["params"][0]["note"] = "Новое пояснение"
    rec["param_sets"].reverse()
    for item in rec["param_sets"]:
        item["params"].reverse()


def set_note(docs):
    record(docs)["param_sets"][0]["note"] = "Новое примечание самого набора"


def relaxed(docs):
    record(docs)["param_sets"][1]["params"] = ["days_off_window_days"]
    # Keep the catalogue union-of-sets invariant: the old member still
    # exists, but belongs to a different newly named choice.
    record(docs)["param_sets"].append({"id": "window_floor", "params": ["min_days_off_in_window"],
                                        "note": "Отдельный выбор минимума"})


def pinned_doc(old, new, monkeypatch):
    from test_cdpm_k01_family_barrier import _doc_with, _landed
    fit = landed(old, monkeypatch)
    rule = _landed(rid="R-PIN", family=FAMILY, rev=old.fingerprint(), zone="PAIRING")
    rule["template_fit"] = fit
    monkeypatch.setenv(cb.ENV_CATALOG, str(new.path))
    cb.forget()
    return _doc_with(rule)


def candidate():
    return {"kind": "HARD", "template_fit": {
        "category": "A", "family": FAMILY,
        "params": {"min_days_off": {"value": 8, "quote": "8"}}}}


def landed(old, monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(old.path))
    cb.forget()
    fit, _confidence, note = m.accept_fit_candidate(candidate(), "Выходных в месяц не менее 8.")
    assert fit["category"] == "A", (fit, note)
    return fit


def test_acceptance_writes_named_reference_from_catalogue(tmp_path, monkeypatch):
    _doc, old, _new = pair(tmp_path, monkeypatch, unchanged)
    fit = landed(old, monkeypatch)
    pin = fit.get("family_contract")
    assert isinstance(pin, dict), "The actual landing writer must create the reference"
    assert set(pin) == {"family_id", "catalog_version", "catalog_fingerprint", "record_fingerprint", "param_set"}
    assert pin["family_id"] == FAMILY and pin["param_set"] == "monthly"
    assert pin["catalog_fingerprint"] == fit["registry_rev"] == old.fingerprint()
    assert pin["catalog_version"] == old.version()


@pytest.mark.parametrize("edit,verdict", [(unchanged, "совместимо"), (rename, "несовместимо"), (swap, "несовместимо")])
def test_reference_cannot_silently_follow_renamed_or_reassigned_id(tmp_path, monkeypatch, edit, verdict):
    from cdpm import family_contract as fc
    _doc, old, new = pair(tmp_path, monkeypatch, edit)
    fit = landed(old, monkeypatch)
    before = copy.deepcopy(fit)
    result = fc.read(fit, new)
    assert result["verdict"] == verdict, result
    assert result["origin"]["param_set"] == "monthly"
    assert result["old_members"] == ["min_days_off"]
    assert fit == before, "A reader must not rewrite the origin or current stamp"


@pytest.mark.parametrize("field,value", [
    ("family_id", "rest_floors"), ("catalog_version", "9.9.9"),
    ("catalog_fingerprint", "0" * 64), ("record_fingerprint", "0" * 64),
    ("param_set", "missing-set"),
])
def test_false_pin_never_becomes_compatible(tmp_path, monkeypatch, field, value):
    from cdpm import family_contract as fc
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    fit = landed(old, monkeypatch)
    fit["family_contract"][field] = value
    before = copy.deepcopy(fit)
    result = fc.read(fit, new)
    assert result["verdict"] == "совместимость не доказана"
    assert result["issues"]
    assert fit == before


def test_legacy_read_does_not_invent_original_choice(tmp_path, monkeypatch):
    from cdpm import family_contract as fc
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    fit = candidate()["template_fit"]
    fit["registry_rev"] = old.fingerprint()
    before = copy.deepcopy(fit)
    assert fc.read(fit, new)["legacy"] is True
    assert fit == before and "family_contract" not in fit


@pytest.mark.parametrize("edit,blocked", [(unchanged, False), (rename, True),
                                        (swap, True), (added_set, False),
                                        (set_note, False),
                                        (note_and_order, False)])
def test_common_readers_honor_named_pin(tmp_path, monkeypatch, edit, blocked):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, edit)
    doc = pinned_doc(old, new, monkeypatch)
    before = copy.deepcopy(doc)
    result = fp.family_completeness(doc)[FAMILY]
    assert (result["contract_state"] == fp.CONTRACT_INCOMPATIBLE) is blocked, result
    assert bool(fp.family_barrier_problems(doc)) is blocked
    assert any("набор" in p for p in m.validate_model(doc)) is blocked
    # K06 executor support is a separate unreleased boundary of this fixture.
    assert any(p.get("family") == FAMILY and p.get("code") == fp.FAMILY_INCOMPLETE
               for p in m.readiness_barriers(doc)) is blocked
    assert fp.ensure_family_param_questions(doc) == []
    assert doc == before


def test_expansion_keeps_selected_obligation_even_with_an_easier_alternative(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, expanded)
    doc = pinned_doc(old, new, monkeypatch)
    before = copy.deepcopy(doc["rules"][-1]["template_fit"])
    result = fp.family_completeness(doc)[FAMILY]
    assert result["verdict"] == fp.VERDICT_INCOMPLETE
    assert result["missing"] == ["days_off_window_days"]
    assert result["curable"] is True
    assert result["contract_state"] != fp.CONTRACT_INCOMPATIBLE
    assert fp.ensure_family_param_questions(doc)
    assert doc["rules"][-1]["template_fit"] == before


def test_expansion_answer_closes_missing_without_replacing_the_original_pin(tmp_path, monkeypatch):
    from cdpm import answers, family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, expanded)
    doc = pinned_doc(old, new, monkeypatch)
    pin = copy.deepcopy(doc["rules"][-1]["template_fit"]["family_contract"])
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1
    result = answers.apply_answers(doc, {created[0]: "28"})
    assert result["applied"] == 1, result
    assert not fp.family_completeness(doc)[FAMILY]["missing"]
    assert not fp.family_barrier_problems(doc)
    assert doc["rules"][-1]["template_fit"]["family_contract"] == pin


def test_a_healthy_neighbour_cannot_hide_a_broken_reference(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    doc = pinned_doc(old, new, monkeypatch)
    bad = copy.deepcopy(doc["rules"][-1])
    bad["id"] = "R-BAD-PIN"
    bad["template_fit"]["family_contract"]["record_fingerprint"] = "0" * 64
    doc["rules"].append(bad)
    for order in (doc["rules"], list(reversed(doc["rules"]))):
        trial = {**doc, "rules": order}
        result = fp.family_completeness(trial)[FAMILY]
        assert result["problems"] and not result["curable"]
        assert result["contract_state"] == fp.CONTRACT_UNPROVEN


def test_relaxation_is_an_explicit_transition_not_a_silent_rebinding(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    from template_catalog import family_contract as shared
    _doc, old, new = pair(tmp_path, monkeypatch, relaxed)
    doc = pinned_doc(old, new, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    fit["params"] = {"days_off_window_days": {"value": 28, "quote": "28"}}
    fit["family_contract"] = shared.snapshot(old, FAMILY, fit["params"])
    assert fit["family_contract"]["param_set"] == "rolling_window"
    before = copy.deepcopy(fit)
    result = fp.family_completeness(doc)[FAMILY]
    ref = result["contract"]["references"][0]
    assert ref["change"] == "relaxed"
    assert set(ref["old_members"]) == {"min_days_off_in_window", "days_off_window_days"}
    assert ref["new_members"] == ["days_off_window_days"]
    assert any("сужен" in words for words in result["contract"]["transitions"])
    assert result["verdict"] == fp.VERDICT_COMPLETE and not result["problems"]
    assert fit == before


def test_schema_accepts_real_pin_but_stays_closed(tmp_path, monkeypatch):
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    doc = pinned_doc(old, new, monkeypatch)
    assert not m.validate_schema(doc)
    doc["rules"][-1]["template_fit"]["family_contract"]["foreign"] = "x"
    assert any("foreign" in x for x in m.validate_schema(doc))


def test_unknown_origin_blocks_without_inventing_relanding(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    doc = pinned_doc(old, new, monkeypatch)
    doc["rules"][-1]["template_fit"]["family_contract"]["catalog_version"] = "9.9.9"
    result = fp.family_completeness(doc)[FAMILY]
    assert result["contract_state"] == fp.CONTRACT_UNPROVEN
    assert result["problems"] and not result["curable"]
    assert result["verdict"] != fp.VERDICT_RELAND
    assert fp.ensure_family_param_questions(doc) == []


def test_shared_writer_refuses_broken_pin_without_mutation(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, swap)
    doc = pinned_doc(old, new, monkeypatch)
    rule = doc["rules"][-1]
    before = copy.deepcopy(rule)
    assert fp.write_cell(rule, "min_days_off", {"value": 9, "quote": "9"})
    assert rule == before


def test_candidate_cannot_supply_its_own_pin(tmp_path, monkeypatch):
    _doc, old, _new = pair(tmp_path, monkeypatch, unchanged)
    real = landed(old, monkeypatch)
    attempt = candidate()
    attempt["template_fit"]["family_contract"] = real["family_contract"]
    fit, _confidence, reason = m.accept_fit_candidate(attempt, "Выходных в месяц не менее 8.")
    assert fit["category"] == "C" and reason
    assert "family_contract" not in fit


def test_new_selection_prefers_first_complete_not_largest_intersection(tmp_path, monkeypatch):
    from template_catalog import family_contract as shared
    _doc, old, _new = pair(tmp_path, monkeypatch, unchanged)
    result = shared.snapshot(old, FAMILY, ["min_days_off", "min_days_off_in_window",
                                           "days_off_window_days"])
    assert result["param_set"] == "monthly"


@pytest.mark.parametrize("field", ["family", "registry_rev", "category"])
def test_pin_requires_carrier_identity(tmp_path, monkeypatch, field):
    from cdpm import family_contract as fc
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    fit = landed(old, monkeypatch)
    fit.pop(field)
    result = fc.read(fit, new)
    assert result["issues"] and result["verdict"] == "совместимость не доказана"


def test_equal_content_does_not_replace_explicit_historical_version(tmp_path, monkeypatch):
    from template_catalog import semantics as sem
    _doc, old, new = pair(tmp_path, monkeypatch, unchanged)
    assert old.fingerprint() == new.fingerprint() and old.version() != new.version()
    resolved = sem.resolve_release(new, fingerprint=old.fingerprint(), version=old.version())
    assert resolved.version() == old.version() and resolved.path != new.path
    assert sem.resolve_release(new, fingerprint=new.fingerprint()).path == new.path
    with pytest.raises(sem.SemanticsError):
        sem.resolve_release(new, fingerprint=old.fingerprint(), version="../../old")


def test_missing_release_is_unproven_and_export_advice_preserves_origin(tmp_path, monkeypatch):
    from cdpm import family_params as fp, export_task_package as tp
    _doc, old, new = pair(tmp_path, monkeypatch, rename)
    doc = pinned_doc(old, new, monkeypatch)
    # Only rename the isolated test release; no product or auditor files.
    release = new.path / "releases" / old.version()
    release.rename(release.with_name("unavailable"))
    result = fp.family_completeness(doc)[FAMILY]
    assert result["contract_state"] == fp.CONTRACT_UNPROVEN
    assert result["problems"] and not result["curable"]
    with pytest.raises(tp.TaskPackageError) as caught:
        # Exercise advice once all modes are unavailable; this is not an
        # assertion that the unaffected rostering task must be blocked.
        tp.refuse_by_barriers(doc, {"pairing": [], "rostering": []})
    assert "восстановите закреплённый выпуск" in str(caught.value)
    assert "Ответьте на вопрос анкеты" not in str(caught.value)


def test_shared_writer_keeps_original_pin_on_healthy_update(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, added_set)
    doc = pinned_doc(old, new, monkeypatch)
    rule = doc["rules"][-1]
    pin = copy.deepcopy(rule["template_fit"]["family_contract"])
    assert fp.write_cell(rule, "min_days_off", {"value": 9, "quote": "9"}) is None
    assert rule["template_fit"]["family_contract"] == pin
    assert rule["template_fit"]["params"]["min_days_off"]["value"] == 9


def test_legacy_landing_keeps_old_set_law(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    _doc, old, new = pair(tmp_path, monkeypatch, swap)
    doc = pinned_doc(old, new, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    fit.pop("family_contract")
    before = copy.deepcopy(doc)
    result = fp.family_completeness(doc)[FAMILY]
    assert result["contract_state"] != fp.CONTRACT_INCOMPATIBLE
    assert not result["problems"] and not result["missing"]
    assert doc == before
