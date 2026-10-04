# -*- coding: utf-8 -*-
"""Блокер прохода COSMOS 06.09.2026 (run-0086, правило R-S1-абз-11):
обработчик добавил правилу, посаженному на шаблон fleet_exclusions с
ручкой fleet_types, блок logic с enforce=required — решатель отверг пакет
(«второй исполнитель — двойной счёт одного нарушения»). Принцип (сужен
по ревью GPT-036/037/038): сведение logic → post-check только у ЖЁСТКОГО
правила с ТОЧНОЙ посадкой A, чьи ручки ДОКАЗАННО исполняют форму блока
logic — пара из таблицы производителя LOGIC_FIT_EQUIVALENT; всё вне
таблицы (посадка B, мягкие, ручки без доказанной эквивалентности, иная
форма — контрпример GPT-037: отдых после длинной смены против ручки
максимума смены) остаётся required, решатель отказывает как прежде.
Нормализация детерминирована, гейт пакета fail-closed."""
from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

from cdpm import model as m

sys.path.insert(0, str(Path(__file__).resolve().parent))
hp = importlib.import_module("test_cdpm_horizon_presence")


def _rule(fit=None, logic=None, kind="HARD", rid="R-S1-абз-11"):
    # структура — настоящее правило run-0086 (проходит контракт модели)
    r = {"id": rid, "title": "Допуск по типу воздушного судна",
         "statement": "Рейс на воздушном судне типа Полярис-Р200 выполняет "
                      "пилот, у которого допуск на этот тип действует на дату рейса.",
         "zone": "BOTH", "kind": kind,
         "sources": [{"sid": "S1", "anchor": "абз.11"}]}
    if fit is not None:
        r["template_fit"] = fit
    if logic is not None:
        r["logic"] = logic
    return r


FIT_A = {"category": "A", "family": "fleet_exclusions",
         "params": {"fleet_types": {"value": "Полярис-Р200",
                                    "quote": "на воздушном судне типа Полярис-Р200"}},
         "registry_rev": "bc78f37804ec"}
FIT_B = {"category": "B", "family": "fleet_exclusions", "assumption": "тип ВС читается как флот",
         "params": {"fleet_types": {"value": "Полярис-Р200", "quote": "…"}},
         "registry_rev": "bc78f37804ec"}
LOGIC_REQ = {"subject": "assignment",
             "condition": [{"predicate": "has_qualification_for",
                            "type_value": "Полярис-Р200",
                            "as_written": "допуск на этот тип действует на дату рейса"}],
             "demand": {"kind": "exists_person", "subject": "employee",
                        "as_written": "выполняет пилот, у которого допуск ... действует"},
             "source_params": [], "enforce": "required"}


def test_exact_fit_hard_rule_gets_post_check_deterministically_with_note():
    doc = {"rules": [_rule(FIT_A, copy.deepcopy(LOGIC_REQ))]}
    assert m.logic_fit_conflicts(doc), "до нормализации — конфликт двух исполнителей"
    healed = m.reconcile_logic_with_template_fit(doc)
    assert len(healed) == 1 and "post-check" in healed[0]
    r = doc["rules"][0]
    assert r["logic"]["enforce"] == "post-check"
    assert "сведён к post-check" in r["note"] and "fleet_exclusions" in r["note"]
    assert m.logic_fit_conflicts(doc) == []
    again = copy.deepcopy(doc)
    assert m.reconcile_logic_with_template_fit(again) == [] and again == doc


def test_missing_enforce_counts_as_required_for_exact_fit():
    logic = copy.deepcopy(LOGIC_REQ)
    logic.pop("enforce")
    doc = {"rules": [_rule(FIT_A, logic)]}
    assert m.logic_fit_conflicts(doc)
    m.reconcile_logic_with_template_fit(doc)
    assert doc["rules"][0]["logic"]["enforce"] == "post-check"


def test_fit_b_soft_and_no_knob_rules_are_left_to_the_solver():
    """Посадка B (с допущением), мягкое правило, посадка C, без посадки,
    посадка A без параметров — не трогаем: решатель судит как прежде."""
    fit_c = {"category": "C", "gap": "шаблона нет", "registry_rev": "bc78f37804ec"}
    fit_a_noparams = {"category": "A", "family": "fleet_exclusions", "params": {},
                      "registry_rev": "bc78f37804ec"}
    rules = [_rule(FIT_B, copy.deepcopy(LOGIC_REQ), rid="R-B"),
             _rule(FIT_A, copy.deepcopy(LOGIC_REQ), kind="SOFT", rid="R-SOFT"),
             _rule(fit_c, copy.deepcopy(LOGIC_REQ), rid="R-C"),
             _rule(None, copy.deepcopy(LOGIC_REQ), rid="R-NOFIT"),
             _rule(fit_a_noparams, copy.deepcopy(LOGIC_REQ), rid="R-A0")]
    doc = {"rules": rules}
    assert m.logic_fit_conflicts(doc) == []
    assert m.reconcile_logic_with_template_fit(doc) == []
    assert all(r["logic"]["enforce"] == "required" for r in doc["rules"])
    assert all("note" not in r for r in doc["rules"])


def _run_with_logic_rule(tmp_path, logic):
    run = hp._run_with_data(tmp_path / "x", hp._book())
    p = run / "extract" / "model.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["rules"].append(_rule(FIT_A, logic))
    assert m.validate_model(doc) == [], m.validate_model(doc)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return run


def test_build_package_reconciles_and_real_gate_is_green(tmp_path):
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    run = _run_with_logic_rule(tmp_path, copy.deepcopy(LOGIC_REQ))
    files = tp.build_package(run)
    pkg_doc = json.loads(files["model.json"].decode("utf-8"))
    rule = next(r for r in pkg_doc["rules"] if r["id"] == "R-S1-абз-11")
    assert rule["logic"]["enforce"] == "post-check"
    assert "сведён к post-check" in rule.get("note", "")
    # повторная сборка байт-идентична (детерминизм)
    assert tp.build_package(run)["model.json"] == files["model.json"]
    (tmp_path / "ok.zip").write_bytes(tp.write_zip(files))
    probs, _ = gate.check(tmp_path / "ok.zip")
    assert probs == [], probs


def test_real_gate_refuses_package_with_unreconciled_required_logic(tmp_path):
    """Пакет, собранный мимо build_package (logic required у точной
    посадки A с ручкой), настоящий гейт краснит."""
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    run = _run_with_logic_rule(tmp_path, copy.deepcopy(LOGIC_REQ))
    files = tp.build_package(run)
    bad = dict(files)
    doc = json.loads(bad["model.json"].decode("utf-8"))
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-абз-11")
    rule["logic"]["enforce"] = "required"
    bad["model.json"] = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    (tmp_path / "bad.zip").write_bytes(tp.write_zip(bad))
    probs, _ = gate.check(tmp_path / "bad.zip")
    assert any("второй исполнитель" in p and "post-check" in p for p in probs), probs

# ── GPT-037 (LOGIC-FIT-R1): метка A — не доказательство совпадения формы ──

FIT_A_DUTY = {"category": "A", "family": "duty_limits",
              "params": {"max_duty_min": {"value": "840", "quote": "смена не более 840 минут"}},
              "registry_rev": "bc78f37804ec"}
LOGIC_REST_AFTER_LONG_DUTY = {
    "subject": "duty",
    "condition": [{"predicate": "duration_over", "value": 720,
                   "as_written": "после смены длиннее 720 минут"}],
    "demand": {"kind": "rest_at_least", "value": 840,
               "as_written": "отдых не менее 840 минут"},
    "source_params": [], "enforce": "required"}


def test_unproven_equivalence_keeps_required_gpt_037_counterexample():
    """Правило «после смены > 720 отдых ≥ 840» посажено A на duty_limits с
    ручкой максимума смены: ручка исполняет ДРУГОЕ требование — сведение
    запрещено, required остаётся, отказ решателя при несовпадении честный."""
    doc = {"rules": [_rule(FIT_A_DUTY, copy.deepcopy(LOGIC_REST_AFTER_LONG_DUTY), rid="R-REST")]}
    assert m.logic_fit_conflicts(doc) == []
    assert m.reconcile_logic_with_template_fit(doc) == []
    r = doc["rules"][0]
    assert r["logic"]["enforce"] == "required" and "note" not in r


def test_known_family_but_other_logic_form_is_not_reconciled():
    """Та же семья fleet_exclusions/fleet_types, но форма logic иная
    (не из таблицы эквивалентностей) — не трогаем."""
    other = copy.deepcopy(LOGIC_REQ)
    other["demand"] = {"kind": "count_within", "subject": "employee", "as_written": "…"}
    doc = {"rules": [_rule(FIT_A, other, rid="R-OTHER")]}
    assert m.logic_fit_conflicts(doc) == []
    assert m.reconcile_logic_with_template_fit(doc) == []
    assert doc["rules"][0]["logic"]["enforce"] == "required"


def test_equivalence_table_is_explicit_and_small():
    assert ("fleet_exclusions", frozenset({"fleet_types"})) in m.LOGIC_FIT_EQUIVALENT
    for key, entry in m.LOGIC_FIT_EQUIVALENT.items():
        assert isinstance(key[1], frozenset) and entry["forms"] and entry["args"]


# ── GPT-043 (LOGIC-FIT-R2/FLEET-ARGS): аргументы сверяются, не имена ──

def _logic_types(*types):
    logic = copy.deepcopy(LOGIC_REQ)
    logic["condition"] = [{"predicate": "has_qualification_for", "type_value": tv,
                           "as_written": f"допуск на {tv}"} for tv in types]
    return logic


def test_type_mismatch_keeps_required_gpt_043():
    """Ручка fleet_types = TYPE-A, условие has_qualification_for(TYPE-B):
    не эквивалентность — required остаётся, отказ решателя сохраняется."""
    fit = copy.deepcopy(FIT_A)
    fit["params"]["fleet_types"]["value"] = "TYPE-A"
    doc = {"rules": [_rule(fit, _logic_types("TYPE-B"))]}
    assert m.logic_fit_conflicts(doc) == []
    assert m.reconcile_logic_with_template_fit(doc) == []
    assert doc["rules"][0]["logic"]["enforce"] == "required"


def test_extra_condition_keeps_required_gpt_043():
    """Ручка TYPE-A, условия TYPE-A + TYPE-B — лишнее условие не покрыто
    ручкой: required остаётся."""
    fit = copy.deepcopy(FIT_A)
    fit["params"]["fleet_types"]["value"] = "TYPE-A"
    doc = {"rules": [_rule(fit, _logic_types("TYPE-A", "TYPE-B"))]}
    assert m.reconcile_logic_with_template_fit(doc) == []
    assert doc["rules"][0]["logic"]["enforce"] == "required"


def test_matching_arguments_reconcile_including_multi_value_knob():
    """Положительный контроль: TYPE-A ↔ TYPE-A сводится; ручка с двумя
    типами «TYPE-A, TYPE-B» ↔ два условия по одному на тип — тоже."""
    fit = copy.deepcopy(FIT_A)
    fit["params"]["fleet_types"]["value"] = "TYPE-A"
    doc = {"rules": [_rule(fit, _logic_types("TYPE-A"))]}
    assert len(m.reconcile_logic_with_template_fit(doc)) == 1
    assert doc["rules"][0]["logic"]["enforce"] == "post-check"
    fit2 = copy.deepcopy(FIT_A)
    fit2["params"]["fleet_types"]["value"] = "TYPE-A, TYPE-B"
    doc2 = {"rules": [_rule(fit2, _logic_types("TYPE-B", "TYPE-A"))]}
    assert len(m.reconcile_logic_with_template_fit(doc2)) == 1
    # дубликат условия при одном значении — не один к одному
    fit3 = copy.deepcopy(FIT_A)
    fit3["params"]["fleet_types"]["value"] = "TYPE-A"
    doc3 = {"rules": [_rule(fit3, _logic_types("TYPE-A", "TYPE-A"))]}
    assert m.reconcile_logic_with_template_fit(doc3) == []

def test_demand_subject_is_part_of_the_form_gpt_045():
    """Та же семья, те же предикат и вид требования, но demand.subject
    иной (не «employee») — не эквивалентность, required остаётся."""
    logic = copy.deepcopy(LOGIC_REQ)
    logic["demand"]["subject"] = "duty"
    doc = {"rules": [_rule(FIT_A, logic)]}
    assert m.logic_fit_conflicts(doc) == []
    assert m.reconcile_logic_with_template_fit(doc) == []
    assert doc["rules"][0]["logic"]["enforce"] == "required"
