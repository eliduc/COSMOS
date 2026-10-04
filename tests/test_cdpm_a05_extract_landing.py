"""A05 target-pass writer: prepared additions go to landing atomically."""
import copy
import json

import pytest

from cdpm import extract, family_params as fp, model as m
from test_cdpm_a05_params_add_atomic import _rule, _add, NAME


def _missing():
    r = _rule()
    r["statement"] = "не более 3 суток; смена не более 12 часов; шаг 30 минут"
    r["template_fit"]["params"] = {
        "max_duty_duration": {"value": 720, "quote": "12 часов", "relation": "max"}}
    return r


def _count_ready():
    # A genuinely unitless source, not a numeric fragment stripped out of
    # the original "3 суток". PARAM-level counted-subject metadata is absent.
    r = _missing()
    r["statement"] = "число дней подряд — не более 3; смена не более 12 часов; шаг 30 минут"
    return r


def _count_add():
    return {**_add(), "as_written": "не более 3"}


def test_new_declared_parameter_goes_through_shared_writer_not_legacy(monkeypatch):
    r = _count_ready()
    a = _count_add()
    before = copy.deepcopy(a)
    calls = []
    real = fp.write_cell
    def spy(rule, name, cell):
        calls.append((name, copy.deepcopy(cell)))
        return real(rule, name, cell)
    monkeypatch.setattr(fp, "write_cell", spy)
    assert extract.merge_soft_params(r, [a]) is None
    cell = r["template_fit"]["params"][NAME]
    assert cell == {"value": 3, "unit": "count", "relation": "max",
                    "quote": "не более 3", "source": {"kind": "rule", "rule_id": "R-S"}}
    assert r.get("params") in (None, [])
    assert calls == [(NAME, cell)]
    assert a == before
    assert m.reconcile_carriers(r) == []


def test_mixed_batch_keeps_only_unbound_parameter_in_legacy():
    r = _count_ready()
    step = {"name": "long_sit_step", "value": 30, "relation": "eq", "as_written": "30 минут"}
    assert extract.merge_soft_params(r, [_count_add(), step]) is None
    assert NAME in r["template_fit"]["params"]
    assert [p["name"] for p in r["params"]] == ["long_sit_step"]


def test_late_writer_refusal_rolls_back_entire_rule(monkeypatch):
    r = _count_ready()
    r["template_fit"]["params"] = {}
    before = copy.deepcopy(r)
    real = fp.write_cell
    calls = []
    def fail_second(rule, name, cell):
        calls.append(name)
        if len(calls) == 2:
            return "вторая запись отказана"
        return real(rule, name, cell)
    monkeypatch.setattr(fp, "write_cell", fail_second)
    adds = [_count_add(), {"name": "max_duty_duration", "value": 720,
                    "relation": "max", "as_written": "12 часов"}]
    assert extract.merge_soft_params(r, adds) == "вторая запись отказана"
    assert calls == [NAME, "max_duty_duration"]
    assert r == before


@pytest.mark.parametrize("change", [{"family": "does-not-exist"},
                                    {"params": None}])
def test_bad_landing_is_not_bypassed_by_legacy_fallback(change):
    r = _missing()
    r["template_fit"].update(change)
    before = copy.deepcopy(r)
    assert extract.merge_soft_params(r, [_add()])
    assert r == before


def test_new_cell_cannot_claim_a_quote_absent_from_the_rule():
    r = _missing()
    a = _add()
    a["as_written"] = "не более 30 суток"
    before = copy.deepcopy(r)
    assert extract.merge_soft_params(r, [a])
    assert r == before


def test_new_cell_value_must_agree_with_its_real_quote():
    r = _count_ready()
    r["statement"] = "число дней подряд — не более 30"
    a = _count_add()
    a["as_written"] = "не более 30"
    before = copy.deepcopy(r)
    assert extract.merge_soft_params(r, [a])
    assert r == before


@pytest.mark.parametrize("quote", ["не более 3 суток", "не более 30 суток", "не более 3 часа"])
def test_new_count_cell_does_not_strip_a_unit_from_the_original_source(quote):
    # Keep the old day-bearing inputs as negative controls; a bare-count
    # positive must not weaken the existing count/unit contract.
    r = _missing()
    r["statement"] = quote
    before = copy.deepcopy(r)
    problem = extract.merge_soft_params(r, [{**_add(), "as_written": quote}])
    assert problem and "не разобрана" in problem and "count" in problem
    assert r == before


@pytest.mark.parametrize("bad", [None, {}, [], True])
def test_broken_existing_cell_is_not_replaced_by_an_addition(bad):
    r = _missing()
    r["template_fit"]["params"][NAME] = bad
    before = copy.deepcopy(r)
    assert extract.merge_soft_params(r, [_add()])
    assert r == before


@pytest.mark.parametrize("statement,quote", [
    ("не более 3 суток", "не более 3"),
    ("не более 3 часа", "3"),
    ("не более 3 календарных недель", "не более 3"),
    ("число не более 13", "3"),
    ("число не более 3.1", "3"),
])
def test_new_count_quote_cannot_cut_the_measure_out_of_its_source(statement, quote):
    r = _missing()
    r["statement"] = statement
    before = copy.deepcopy(r)
    problem = extract.merge_soft_params(r, [{**_add(), "as_written": quote}])
    assert problem and "обрезает" in problem
    assert r == before


@pytest.mark.parametrize("invalid_logic", [False, True])
def test_real_target_pass_persists_cell_or_rolls_back_it_all(tmp_path, monkeypatch, invalid_logic):
    r = _count_ready()
    doc = {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1", "revision": 2},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [r], "questions": [], "objectives": []}
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    logic = {"subject": "assignment", "condition": [],
             "demand": {"kind": "мягкий-предел-дней-подряд",
                        "bound_param": "missing" if invalid_logic else NAME,
                        "as_written": "не более 3 суток"},
             "source_params": [NAME], "enforce": "required"}
    reply = {"logic": logic, "params_add": [_count_add()]}
    monkeypatch.setattr(extract, "_ask", lambda *_: copy.deepcopy(reply))
    monkeypatch.setattr(extract.llm_mod, "parallel_map",
                        lambda fn, values, **_: [fn(v) for v in values])
    (tmp_path / "model.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    result = extract.produce_logic(tmp_path, lambda *_: pytest.fail("No live model"))
    saved = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))["rules"][0]
    assert result["attached"] == int(not invalid_logic)
    assert result["dropped"] == int(invalid_logic)
    assert saved.get("params") in (None, [])
    if invalid_logic:
        assert saved["template_fit"] == r["template_fit"]
    else:
        assert saved["template_fit"]["params"][NAME]["value"] == 3
        assert m.validate_logic(saved["logic"], saved) == []
        assert "<max_pairing_days?>" not in m.render_logic(saved["logic"], saved)
