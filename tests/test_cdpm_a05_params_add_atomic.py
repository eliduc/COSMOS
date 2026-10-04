"""A05 params_add: reject contradictions before committing either carrier.

Pure dictionaries and temporary model files; the producer's model response is
injected, never a live model call. This does not certify full landing/2 migration.
"""
import copy
import json

import pytest

from cdpm import extract, model as m


NAME = "max_pairing_days"


def _add(value=3, relation="max", name=NAME):
    return {"name": name, "value": value, "relation": relation,
            "as_written": f"не более {value} суток"}


def _rule(*, landed=True):
    r = {"id": "R-S", "title": "Дни подряд", "statement": "не более 3 суток",
         "zone": "PAIRING", "kind": "SOFT", "weight": 5,
         "sources": [{"sid": "S1", "anchor": "п.1"}]}
    if landed:
        r["template_fit"] = {
            "category": "A", "family": "duty_limits",
            "registry_rev": m.registry_fingerprint(),
            "params": {NAME: {"value": 3, "quote": "не более 3 суток",
                              "relation": "max"}}}
    return r


@pytest.mark.parametrize("change,addition,word", [
    ({}, _add(30), "значение"),
    ({}, _add(3, "min"), "отношение"),
    ({"condition": {"kind": "base", "value": "SVO"}}, _add(), "условие"),
])
def test_new_legacy_candidate_cannot_contradict_landing(change, addition, word):
    rule = _rule()
    rule["template_fit"]["params"][NAME].update(change)
    before = copy.deepcopy(rule)
    problem = extract.merge_soft_params(rule, [addition])
    assert problem and NAME in problem and word in problem
    assert rule == before


def test_late_conflict_refuses_entire_addition_batch():
    rule = _rule()
    before = copy.deepcopy(rule)
    additions = [_add(30, "eq", "long_sit_step"), _add(30)]
    assert extract.merge_soft_params(rule, additions)
    assert rule == before


@pytest.mark.parametrize("values", [(3, 30), (30, 3)])
def test_duplicate_names_inside_batch_cannot_hide_conflicting_values(values):
    rule = _rule(landed=False)
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [_add(v) for v in values])
    assert rule == before


def test_identical_addition_is_written_once():
    rule = _rule(landed=False)
    addition = _add()
    assert extract.merge_soft_params(rule, [addition, copy.deepcopy(addition)]) is None
    assert len(rule["params"]) == 1


@pytest.mark.parametrize("values", [(3, 30), (30, 3)])
def test_all_existing_records_are_checked_not_only_last_name(values):
    rule = _rule(landed=False)
    rule["params"] = [{**_add(v), "unit": "count"} for v in values]
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [_add()])
    assert rule == before


def test_same_value_cannot_hide_changed_explicit_relation():
    rule = _rule(landed=False)
    rule["params"] = [{**_add(), "unit": "count"}]
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [_add(3, "min")])
    assert rule == before


@pytest.mark.parametrize("bad", [
    "not a list", [None], [{"name": []}], [_add(True)], [_add(-1)],
    [{"name": NAME, "value": 3}], [_add(3, "unknown")],
])
def test_bad_addition_is_a_nonmutating_refusal(bad):
    rule = _rule()
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, bad)
    assert rule == before


def test_original_carrier_conflict_is_not_healed_by_new_candidate():
    rule = _rule()
    rule["params"] = [{**_add(30), "unit": "count"}]
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [_add(30)])
    assert rule == before


@pytest.mark.parametrize("bad", [{}, 0, False, "", [None], [{"value": 3}]])
def test_malformed_existing_carrier_is_not_silently_replaced(bad):
    rule = _rule()
    rule["params"] = bad
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [_add()])
    assert rule == before


def test_canonical_conversion_accepts_healthy_hours_against_minutes():
    rule = _rule()
    rule["template_fit"].update(family="rest_floors", params={
        "min_rest_outstation": {"value": 10, "unit": "ч", "relation": "min",
                                "quote": "не менее 10 часов"}})
    before = copy.deepcopy(rule["template_fit"])
    addition = {"name": "min_rest_outstation", "value": 600,
                "relation": "min", "as_written": "не менее 10 часов"}
    assert extract.merge_soft_params(rule, [addition]) is None
    assert rule["template_fit"] == before
    assert m.reconcile_carriers(rule) == []


@pytest.mark.parametrize("landed", [False, True])
def test_healthy_value_preserves_landing_and_original_response(landed):
    rule = _rule(landed=landed)
    landing_before = copy.deepcopy(rule.get("template_fit"))
    addition = _add()
    before_add = copy.deepcopy(addition)
    assert extract.merge_soft_params(rule, [addition]) is None
    assert rule.get("template_fit") == landing_before
    assert addition == before_add
    assert m.reconcile_carriers(rule) == []


@pytest.mark.parametrize("value,invalid_logic,attached", [
    (3, False, 1), (30, False, 0), (3, True, 0)])
def test_real_producer_with_injected_reply_does_not_persist_contradiction(
        tmp_path, monkeypatch, value, invalid_logic, attached):
    doc = {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1", "revision": 2},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [_rule()], "questions": [], "objectives": []}
    doc["rules"][0]["note"] = "Прежнее примечание человека"
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    logic = {"subject": "assignment", "condition": [],
             "demand": {"kind": "мягкий-предел-дней-подряд", "bound_param": NAME,
                        "as_written": "не более 3 суток"},
             "source_params": [NAME], "enforce": "required"}
    if invalid_logic:
        logic["demand"]["bound_param"] = "missing_parameter"
    reply = {"logic": logic, "params_add": [_add(value)]}
    monkeypatch.setattr(extract, "_ask", lambda *_: copy.deepcopy(reply))
    monkeypatch.setattr(extract.llm_mod, "parallel_map",
                        lambda fn, values, **_: [fn(v) for v in values])
    (tmp_path / "model.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    result = extract.produce_logic(tmp_path, lambda *_: pytest.fail("No live model"))
    saved = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    assert result["attached"] == attached
    assert result["dropped"] == 1 - attached
    assert saved["rules"][0]["template_fit"] == doc["rules"][0]["template_fit"]
    assert m.reconcile_carriers(saved["rules"][0]) == []
    assert ("logic" in saved["rules"][0]) == bool(attached)
    assert saved["rules"][0]["note"].startswith("Прежнее примечание человека")
    assert result["asked"] == result["attached"] + result["dropped"]
    svod = (tmp_path / "svod.md").read_text(encoding="utf-8")
    anketa = (tmp_path / "anketa.md").read_text(encoding="utf-8")
    assert "Прежнее примечание человека" in svod
    assert ("параметры формы добавлены" in svod) == bool(attached)
    assert "параметры формы добавлены" not in anketa
    if not attached:
        assert "логика отброшена" in svod
    if not attached:
        assert "params" not in saved["rules"][0]
        assert "параметры формы добавлены" not in saved["rules"][0]["note"]
        if not invalid_logic:
            assert "два носителя" in saved["rules"][0]["note"]
