"""Offline prompt-capture regressions for the H0C typed-cell projection loss.

These tests inspect the actual _audit_round prompt, not a substitute renderer.
The callable auditor is local and synthetic; no model/provider is constructed.
They prove transport of existing evidence, not an audit verdict or E2E PASS.
"""
from copy import deepcopy
import json
import socket

import pytest

from cdpm import exchange as ex
from cdpm import model


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("This prompt-capture profile must not use network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    # Only the unrelated catalogue listing is bounded. The rule projection,
    # coverage, measurement, question provenance and cache stay production code.
    monkeypatch.setattr(model, "template_fit_listing",
                        lambda: "OFFLINE fixture catalogue: typed params require quote/source")


def _cell(value=0):
    return {
        "value": value,
        "unit": "duration_minutes",
        "unit_label": "минуты",
        "relation": "eq",
        "condition": None,
        "quote": "смещение шкалы данных 0 минут",
        "source": {"kind": "rule", "rule_id": "R-1"},
    }


def _rule():
    return {
        "id": "R-1", "zone": "PAIRING", "kind": "HARD",
        "statement": "Шкала данных без смещения.",
        "sources": [{"sid": "S1", "anchor": "п.1"}],
        "template_fit": {
            "category": "A", "family": "time_scale",
            "params": {"base_tz_offset_min": _cell()},
            "schema_version": "landing/2",
            "registry_rev": "a" * 64,
            "family_contract": {
                "family_id": "time_scale", "catalog_version": "0.7.0",
                "catalog_fingerprint": "b" * 64,
                "record_fingerprint": "c" * 64,
                "param_set": ["base_tz_offset_min"],
            },
        },
    }


def _doc(rule=None):
    return {"rules": [rule or _rule()], "objectives": [], "questions": [],
            "sources": [{"sid": "S1"}, {"sid": "S2"}]}


def _kit():
    return [{"sid": "S1", "clauses": [
        {"anchor": "п.1", "text": "Шкала данных без смещения."}]}]


def _capture(doc=None, *, kit=None, cache=None, reply=None):
    prompts = []

    def auditor(prompt):
        prompts.append(prompt)
        return json.dumps(reply if reply is not None else {"remarks": []},
                          ensure_ascii=False)

    pack, rejected = ex._audit_round(
        auditor, doc or _doc(), kit or _kit(), 1, cache_dir=cache)
    return prompts, pack, rejected


def _line(prompt, rule_id="R-1"):
    return next(line for line in prompt.splitlines()
                if line.startswith(rule_id + " | "))


def _json_after(line, marker):
    raw = line.split(marker, 1)[1]
    value, _end = json.JSONDecoder().raw_decode(raw)
    return value


@pytest.mark.parametrize("orphan", [False, True], ids=["normal", "orphan"])
def test_actual_audit_prompt_preserves_full_typed_cell_and_fit_contract(orphan):
    rule = _rule()
    if orphan:
        rule["sources"][0]["anchor"] = "п.вне-комплекта"
    before = deepcopy(rule)
    prompts, _pack, rejected = _capture(_doc(rule))
    assert rejected == []
    assert len(prompts) == 1
    line = _line(prompts[0])
    cell = _json_after(line, "base_tz_offset_min=")
    assert isinstance(cell, dict), "audit projection lost quote/source by keeping value only"
    assert cell == rule["template_fit"]["params"]["base_tz_offset_min"]
    metadata = _json_after(line, "контракт посадки (JSON)=")
    assert metadata == {key: rule["template_fit"][key] for key in
                        ("schema_version", "registry_rev", "family_contract")}
    assert rule == before, "rendering must not mutate the model"
    if orphan:
        assert "СТРОКИ С ТРАССИРОВКОЙ ВНЕ" in prompts[0]


@pytest.mark.parametrize("value", [0, 780, 2.5, False, None,
                                     ["A320", "B737"], {"AAA": 1, "BBB": 2}])
def test_typed_value_and_nested_provenance_are_data_not_scalarized(value):
    rule = _rule()
    cell = _cell(value)
    cell["condition"] = {"base_codes": ["AAA"], "negated": False}
    cell["quote"] = 'строка с "кавычками", \\ и\nпереносом'
    cell["source"] = {"kind": "answer", "question_id": "Q-1", "revision": 2}
    cell["contract_extension"] = {"flags": [False, None], "encoding": "typed"}
    rule["template_fit"]["params"]["base_tz_offset_min"] = cell
    prompts, _pack, _rejected = _capture(_doc(rule))
    line = _line(prompts[0])
    assert _json_after(line, "base_tz_offset_min=") == cell
    assert json.dumps(cell, ensure_ascii=False, sort_keys=True) in line


@pytest.mark.parametrize("missing", ["value", "unit", "unit_label", "relation",
                                      "condition", "quote", "source"])
def test_missing_typed_fields_remain_absent_not_repaired_by_renderer(missing):
    rule = _rule()
    cell = rule["template_fit"]["params"]["base_tz_offset_min"]
    del cell[missing]
    prompts, _pack, _rejected = _capture(_doc(rule))
    rendered = _json_after(_line(prompts[0]), "base_tz_offset_min=")
    assert isinstance(rendered, dict)
    assert rendered == cell
    assert missing not in rendered


def test_empty_cell_remains_empty_and_is_not_invented_from_source_clause():
    rule = _rule()
    rule["template_fit"]["params"]["base_tz_offset_min"] = {}
    kit = _kit()
    kit[0]["clauses"][0]["text"] = "смещение шкалы данных 0 минут"
    prompts, _pack, _rejected = _capture(_doc(rule), kit=kit)
    assert _json_after(_line(prompts[0]), "base_tz_offset_min=") == {}


@pytest.mark.parametrize("missing", ["schema_version", "registry_rev", "family_contract"])
def test_fit_contract_fields_are_not_filled_when_absent(missing):
    rule = _rule()
    fit = rule["template_fit"]
    del fit[missing]
    prompts, _pack, _rejected = _capture(_doc(rule))
    metadata = _json_after(_line(prompts[0]), "контракт посадки (JSON)=")
    assert metadata == {key: fit[key] for key in
                        ("schema_version", "registry_rev", "family_contract")
                        if key in fit}
    assert missing not in metadata


@pytest.mark.parametrize("scalar", [0, 17, 2.5, "legacy", False, None])
def test_legacy_scalar_projection_is_unchanged(scalar):
    rule = _rule()
    rule["template_fit"] = {"category": "A", "family": "time_scale",
                            "params": {"base_tz_offset_min": scalar}}
    prompts, _pack, _rejected = _capture(_doc(rule))
    assert _line(prompts[0]) == (
        "R-1 | PAIRING | HARD | Шкала данных без смещения. | машинные поля: "
        f"посадка A time_scale {{base_tz_offset_min={scalar}}}")


def test_weight_top_level_quote_and_applied_answer_marker_are_preserved():
    rule = _rule()
    rule.update(kind="SOFT", weight=15)
    rule["template_fit"]["quote"] = "норма с весом"
    doc = _doc(rule)
    doc["questions"] = [{
        "id": "Q-1", "part": "1", "title": "Уточнение", "text": "Значение?",
        "answer_format": {"type": "value"}, "rule_refs": ["R-1"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"status": "применён", "raw": "0", "revision": 1},
    }]
    prompts, _pack, _rejected = _capture(doc)
    line = _line(prompts[0])
    assert "вес 15; посадка A time_scale" in line
    assert "цитата «норма с весом»" in line
    assert line.endswith("[значения установлены применённым ответом анкеты: Q-1]")
    assert _json_after(line, "base_tz_offset_min=") == _cell()


@pytest.mark.parametrize("kind", ["criterion", "legacy_rate"])
def test_objective_machine_fields_still_reach_every_source_chunk(kind):
    doc = _doc()
    objective = {
        "id": "O-1", "zone": "PAIRING", "direction": "minimize",
        "metric": "cost", "statement": "Минимум стоимости", "tier": 2,
        "sources": [{"sid": "S2", "anchor": "п.2"}],
    }
    if kind == "criterion":
        objective["criterion"] = {
            "kind": "person_days", "rate_per_duty": 700,
            "per": "duty", "version": "v1", "effective_from": "2026-09-20",
        }
        expected = (" | машинный критерий person_days, ставка 700 за duty, "
                    "версия v1, действует с 2026-09-20")
    else:
        objective["rate"] = {"value": 700, "per": "duty"}
        expected = " | ставка 700 за duty"
    doc["objectives"] = [objective]
    kit = _kit() + [{"sid": "S2", "clauses": [
        {"anchor": "п.2", "text": "Минимум стоимости"}]}]
    prompts, _pack, _rejected = _capture(doc, kit=kit)
    assert len(prompts) == 2
    for prompt in prompts:
        assert _line(prompt, "O-1") == (
            "O-1 | PAIRING | minimize | cost | Минимум стоимости" + expected
            + " | ярус 2 | источники: S2|п.2")


@pytest.mark.parametrize("field", ["quote", "source", "family_contract"])
def test_changed_evidence_invalidates_cache_but_unchanged_evidence_reuses_it(tmp_path, field):
    doc = _doc()
    cache = tmp_path / "audit-cache"
    prompts, _pack, _rejected = _capture(doc, cache=cache)
    assert len(prompts) == 1
    unchanged, _pack, _rejected = _capture(deepcopy(doc), cache=cache)
    assert unchanged == []
    fit = doc["rules"][0]["template_fit"]
    if field == "family_contract":
        fit[field]["record_fingerprint"] = "d" * 64
    else:
        fit["params"]["base_tz_offset_min"][field] = (
            "другая цитата" if field == "quote" else
            {"kind": "answer", "question_id": "Q-2", "revision": 3})
    changed, _pack, _rejected = _capture(doc, cache=cache)
    assert len(changed) == 1
    assert changed[0] != prompts[0]


def test_projection_does_not_include_unrelated_runtime_settings(monkeypatch):
    sentinel = "OFFLINE_ONLY_NOT_A_REAL_SECRET"
    monkeypatch.setenv("CDPM_PROJECTION_TEST_SECRET", sentinel)
    doc = _doc()
    doc["runtime_settings"] = {"credential": sentinel}
    prompts, _pack, _rejected = _capture(doc)
    assert all(sentinel not in prompt for prompt in prompts)


def test_real_missing_quote_remark_is_not_suppressed_or_downgraded():
    doc = _doc()
    del doc["rules"][0]["template_fit"]["params"]["base_tz_offset_min"]["quote"]
    remark = {
        "severity": "СЕРЬЁЗНОЕ", "defect_class": "искажение",
        "target": {"rule": "R-1", "question": None},
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "text": "У параметра отсутствует цитата-носитель.",
        "evidence": "Шкала данных без смещения.",
        "proposal": "Добавить обоснованную цитату параметра.",
    }
    prompts, pack, rejected = _capture(doc, reply={"remarks": [remark]})
    assert rejected == []
    assert len(pack["remarks"]) == 1
    assert pack["remarks"][0]["severity"] == "СЕРЬЁЗНОЕ"
    assert pack["remarks"][0]["text"] == remark["text"]
    rendered = _json_after(_line(prompts[0]), "base_tz_offset_min=")
    assert isinstance(rendered, dict) and "quote" not in rendered
