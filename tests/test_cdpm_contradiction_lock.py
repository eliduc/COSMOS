# -*- coding: utf-8 -*-
"""Замок части 1 анкеты (слово владельца 11.09.2026): противоречие
принимается ТОЛЬКО при машинно видимых разных значениях одной нормы.

Классы поведения:

- подтверждённое расхождение (одна семья посадки и одно имя параметра,
  разные значения) → вопрос части 1 со свидетельством `contradiction` и
  блокировкой обеих строк — как было до замка;
- одинаковые значения, разные нормы, разные области применимости →
  вопрос части 2 с припиской словами, строки НЕ блокируются;
- определения/данные и дополняющие семьи → прежняя отбраковка, вопроса
  нет вовсе;
- числа с единицей сравниваются числами, перечни — множествами,
  уточнитель различает записи одного имени;
- детерминизм: повтор даёт тот же результат байт в байт;
- схема модели принимает свидетельство и бракует его кривую форму."""
import copy
import json

import pytest

from cdpm import contradiction as contra, extract, model as m
from cdpm.sample import sample_model

REV = m.registry_fingerprint()

_SOURCES = [{"sid": "S1", "file": "ОМ-А.md", "sha256": "a" * 64},
            {"sid": "S2", "file": "Колдоговор.md", "sha256": "b" * 64}]

_PROPOSAL = {
    "rule_ids": ["R-S1-1", "R-S2-1"],
    "title": "Минимальный отдых на базе: 10 или 12 часов",
    "text": "Какое требование о минимальном отдыхе действует?",
    "options": [{"key": "а", "text": "10 часов"},
                {"key": "б", "text": "12 часов"}],
}


def _rule(rid, sid, anchor, *, statement, family=None, params=None,
          fit_params=None, kind="HARD", zone="PAIRING", **extra):
    rule = {"id": rid, "title": f"Строка {rid}", "statement": statement,
            "zone": zone, "kind": kind,
            "sources": [{"sid": sid, "anchor": anchor}]}
    if params is not None:
        rule["params"] = params
    if family:
        fit = {"category": "A", "registry_rev": REV, "family": family}
        if fit_params is not None:
            fit["params"] = fit_params
        rule["template_fit"] = fit
    rule.update(extra)
    return rule


def _assembled(rules, proposal=None):
    """Сборка с предложением модели о противоречии: (сборка, причина
    отбраковки либо None)."""
    asm = extract._Assembler("Полярис (тест)", _SOURCES)
    asm.doc["rules"] = copy.deepcopy(rules)
    reason = asm.add_contradiction(copy.deepcopy(proposal or _PROPOSAL))
    return asm, reason


def _only_question(asm):
    assert len(asm.doc["questions"]) == 1, asm.doc["questions"]
    return asm.doc["questions"][0]


def _fit_pair(value_a, value_b, family="rest_rules", name="min_rest_home"):
    return [
        _rule("R-S1-1", "S1", "п.1.9", statement="Отдых на базе.",
              family=family,
              fit_params={name: {"value": value_a, "quote": "не менее"}}),
        _rule("R-S2-1", "S2", "п.2.2", statement="Отдых на базе.",
              family=family,
              fit_params={name: {"value": value_b, "quote": "не менее"}}),
    ]


# ------------------------------------------ подтверждённое расхождение

def test_confirmed_difference_becomes_part1_with_evidence():
    """Одна семья посадки, одно имя параметра, разные значения — вопрос
    части 1 со свидетельством и блокировкой обеих строк."""
    asm, reason = _assembled(_fit_pair(600, 720))
    assert reason is None
    q = _only_question(asm)
    assert q["part"] == "1"
    assert q["title"] == _PROPOSAL["title"]
    assert q["text"] == _PROPOSAL["text"]          # текст модели без приписки
    ev = q["contradiction"]
    assert ev["norm"] == {"family": "rest_rules", "param": "min_rest_home"}
    assert [v["rule_id"] for v in ev["values"]] == ["R-S1-1", "R-S2-1"]
    assert [v["value"] for v in ev["values"]] == [600, 720]
    assert ev["values"][0]["source"] == {"sid": "S1", "anchor": "п.1.9"}
    for r in asm.doc["rules"]:
        assert r["blockers"] == [q["id"]]
    assert len(asm.contradictions_confirmed) == 1
    assert asm.contradictions_downgraded == []


def test_confirmed_difference_by_params_and_subject():
    """Посадки нет — норму держат предмет машинной логики и имя
    параметра `params[]`."""
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="Отдых на базе.",
              params=[{"name": "min_rest_home", "value": 600,
                       "unit": "duration_minutes", "as_written": "10 ч",
                       "relation": "min"}],
              logic={"subject": "duty", "demand": {"kind": "at_least"}}),
        _rule("R-S2-1", "S2", "п.2.2", statement="Отдых на базе.",
              params=[{"name": "min_rest_home", "value": 720,
                       "unit": "duration_minutes", "as_written": "12 ч",
                       "relation": "min"}],
              logic={"subject": "duty", "demand": {"kind": "at_least"}}),
    ]
    asm, reason = _assembled(rules)
    assert reason is None
    q = _only_question(asm)
    assert q["part"] == "1"
    assert q["contradiction"]["norm"] == {"subject": "duty",
                                          "param": "min_rest_home"}


def test_confirmed_difference_by_formal_statement_without_params():
    """Параметров нет ни у одной строки — судится формальная запись:
    записи, различающиеся только числом, — одна норма."""
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="rest_home ≥ 10 ч"),
        _rule("R-S2-1", "S2", "п.2.2", statement="rest_home ≥ 12 ч"),
    ]
    asm, reason = _assembled(rules)
    assert reason is None
    q = _only_question(asm)
    assert q["part"] == "1"
    assert q["contradiction"]["norm"] == {"subject": "rest_home",
                                          "param": "rest_home"}
    assert [v["unit"] for v in q["contradiction"]["values"]] == ["ч", "ч"]


# ------------------------------------------------ понижение в часть 2

def _assert_downgraded(asm, reason, why_part):
    assert reason is None
    q = _only_question(asm)
    assert q["part"] == "2"
    assert q["id"].startswith("Q-2-")
    assert "contradiction" not in q
    assert q["text"].startswith(_PROPOSAL["text"])
    assert ("расхождение значений машинно не подтверждено — уточнение, "
            "не противоречие") in q["text"].lower()
    assert why_part in q["text"], q["text"]
    for r in asm.doc["rules"]:
        assert "blockers" not in r
    assert asm.contradictions_confirmed == []
    assert len(asm.contradictions_downgraded) == 1
    assert asm.contradictions_downgraded[0]["rule_ids"] == \
        _PROPOSAL["rule_ids"]
    return q


def test_same_values_are_downgraded_to_part2():
    """Значения одной нормы совпадают — спора нет: вопрос части 2,
    строки не блокируются."""
    asm, reason = _assembled(_fit_pair(720, 720))
    _assert_downgraded(asm, reason, "машинно совпадают")


def test_different_norms_are_downgraded_to_part2():
    """Разные имена записей — разные нормы: расхождения значений одной
    нормы код не видит."""
    rules = _fit_pair(600, 720)
    rules[1]["template_fit"]["params"] = {
        "min_rest_layover": {"value": 720, "quote": "не менее"}}
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "общих машинных записей")


def test_different_families_are_downgraded_to_part2():
    """Значения одноимённых записей разных семей — две нормы разных
    предметов, а не спор (семьи при этом друг друга не дополняют)."""
    rules = _fit_pair(600, 720)
    rules[1]["template_fit"]["family"] = "calendar_windows"
    rules[1]["statement"] = "Календарное окно."
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "нормы разные")


def test_different_qualifier_is_another_record():
    """Уточнитель различает записи одного имени: норма резерва по базе
    SVG не спорит с нормой по базе PLY."""
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="Резерв.",
              params=[{"name": "reserve_count", "value": 2, "unit": "count",
                       "as_written": "2", "relation": "min",
                       "qualifier": "SVG"}]),
        _rule("R-S2-1", "S2", "п.2.2", statement="Резерв.",
              params=[{"name": "reserve_count", "value": 3, "unit": "count",
                       "as_written": "3", "relation": "min",
                       "qualifier": "PLY"}]),
    ]
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "общих машинных записей")


def test_different_conditions_are_downgraded_to_part2():
    """Условие применения у одной строки и не у другой — предел и его
    условное исключение: две нормы для разных случаев."""
    rules = _fit_pair(600, 720)
    rules[1]["logic"] = {"subject": "duty", "demand": {"kind": "at_least"},
                         "condition": [{"kind": "tz_diff_over",
                                        "hours": 3}]}
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "условия применения")


def test_different_scope_is_downgraded_to_part2():
    """Области действия параметра мира заданы разными словами — нормы
    разных областей."""
    rules = _fit_pair(600, 720)
    rules[0].update(zone="BOTH", scope={"pairing": "все пейринги",
                                        "rostering": "все ростеры"})
    rules[1].update(zone="BOTH", scope={"pairing": "только чартеры",
                                        "rostering": "только чартеры"})
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "области действия")


def test_different_zones_are_downgraded_to_part2():
    rules = _fit_pair(600, 720)
    rules[1]["zone"] = "ROSTERING"
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "зоны")


def test_statements_differing_beyond_value_are_downgraded():
    """Формальные записи различаются не только числом — это разные
    нормы, а не спор об одном значении."""
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="rest_home ≥ 10 ч"),
        _rule("R-S2-1", "S2", "п.2.2", statement="rest_layover ≥ 12 ч"),
    ]
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "не только")


def test_statements_without_numbers_are_downgraded():
    rules = [
        _rule("R-S1-1", "S1", "п.1.9",
              statement="Ночёвки вне базы нежелательны."),
        _rule("R-S2-1", "S2", "п.2.2",
              statement="Ночёвки вне базы допустимы."),
    ]
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "нет чисел")


def test_downgraded_question_passes_model_contract():
    """Понижённый вопрос — законный пункт части 2: развилка, трассировка
    к строкам и источникам; свод проходит инварианты контракта."""
    doc = sample_model()
    rules = _fit_pair(600, 600)
    asm = extract._Assembler("Полярис (тест)", _SOURCES)
    asm.doc["rules"] = copy.deepcopy(rules)
    assert asm.add_contradiction(copy.deepcopy(_PROPOSAL)) is None
    doc["rules"] = asm.doc["rules"]
    doc["questions"] = asm.doc["questions"]
    doc["objectives"] = []
    problems = m.validate_schema(doc)
    assert problems == [], problems


# ----------------------------------------------------- прежняя отбраковка

def test_definitions_are_rejected_as_before():
    """Определения и данные требований не несут — прежняя отбраковка,
    вопроса нет вовсе (замок их не смягчает)."""
    rules = _fit_pair(600, 720)
    rules[0]["kind"] = "DEF"
    rules[0].pop("template_fit")
    asm, reason = _assembled(rules)
    assert reason and "определение или данные" in reason
    assert asm.doc["questions"] == []
    assert asm.contradictions_downgraded == []


def test_complementary_families_are_rejected_as_before():
    """Предел и его продление друг друга дополняют — прежняя отбраковка
    конвенцией кода, вопроса нет."""
    from cdpm import catalog_bridge as bridge
    comps = bridge.complements()
    pair = next(((fam, other) for fam, others in sorted(comps.items())
                 for other in sorted(others or ())), None)
    if pair is None:
        pytest.skip("в каталоге шаблонов нет дополняющих семей")
    rules = _fit_pair(600, 720, family=pair[0])
    rules[1]["template_fit"]["family"] = pair[1]
    asm, reason = _assembled(rules)
    assert reason and "дополняют друг друга" in reason
    assert asm.doc["questions"] == []


def test_single_source_contradiction_is_rejected_as_before():
    """Подтверждённое расхождение из ОДНОГО места источника вопросом
    части 1 не становится: контракт части 1 требует двух мест."""
    rules = _fit_pair(600, 720)
    rules[1]["sources"] = [{"sid": "S1", "anchor": "п.1.9"}]
    asm, reason = _assembled(rules)
    assert reason == "у противоречия менее двух мест источников"
    assert asm.doc["questions"] == []


# ------------------------------------------------------------- детерминизм

def test_lock_is_deterministic():
    """Повтор на том же входе даёт тот же вопрос и то же свидетельство
    байт в байт — и при подтверждении, и при понижении."""
    for rules in (_fit_pair(600, 720), _fit_pair(720, 720)):
        first, _ = _assembled(rules)
        second, _ = _assembled(rules)
        assert json.dumps(first.doc, ensure_ascii=False, sort_keys=True) \
            == json.dumps(second.doc, ensure_ascii=False, sort_keys=True)
        assert first.contradictions_downgraded == \
            second.contradictions_downgraded


# ---------------------------------------------- функция свидетельства отдельно

def test_numbers_with_units_are_compared_as_numbers():
    """«10 ч» и «600 мин» — одно значение: расхождения нет."""
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="rest_home ≥ 10 ч"),
        _rule("R-S2-1", "S2", "п.2.2", statement="rest_home ≥ 600 мин"),
    ]
    # Маска записи различается («ч» против «мин») — разные нормы для
    # кода; ту же величину через параметры он видит равной.
    ev, why = contra.evidence(rules)
    assert ev is None and why
    rules = [
        _rule("R-S1-1", "S1", "п.1.9", statement="Отдых.",
              params=[{"name": "min_rest_home", "value": 10, "unit": "ч",
                       "as_written": "10 ч", "relation": "min"}],
              logic={"subject": "duty", "demand": {"kind": "at_least"}}),
        _rule("R-S2-1", "S2", "п.2.2", statement="Отдых.",
              params=[{"name": "min_rest_home", "value": 600,
                       "unit": "мин", "as_written": "600 мин",
                       "relation": "min"}],
              logic={"subject": "duty", "demand": {"kind": "at_least"}}),
    ]
    ev, why = contra.evidence(rules)
    assert ev is None and "совпадают" in why


def test_code_lists_are_compared_as_sets():
    """Перечень кодов — множество: порядок и пробелы значения не
    меняют, иной состав — расхождение."""
    same = [
        _rule("R-S1-1", "S1", "п.1.9", statement="Базы.", family="bases",
              fit_params={"bases": {"value": "SVG, PLY", "quote": "…"}}),
        _rule("R-S2-1", "S2", "п.2.2", statement="Базы.", family="bases",
              fit_params={"bases": {"value": "PLY,SVG", "quote": "…"}}),
    ]
    ev, why = contra.evidence(same)
    assert ev is None and "совпадают" in why
    other = copy.deepcopy(same)
    other[1]["template_fit"]["params"]["bases"]["value"] = "PLY, GRT"
    ev, why = contra.evidence(other)
    assert ev is not None and why == ""
    assert ev["norm"] == {"family": "bases", "param": "bases"}


def test_empty_value_is_not_a_difference():
    """Значение записи не заполнено — код его не сравнил, а не увидел
    спор: часть 2, строки не блокируются (fail-closed)."""
    rules = _fit_pair(600, 720)
    rules[1]["template_fit"]["params"]["min_rest_home"].pop("value")
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "несопоставимы")


def test_values_of_different_nature_are_not_a_difference():
    """Число против слов — значения разной природы: расхождением это не
    объявляется."""
    rules = _fit_pair(600, "по договорённости сторон")
    asm, reason = _assembled(rules)
    _assert_downgraded(asm, reason, "несопоставимы")


def test_evidence_needs_two_rules():
    ev, why = contra.evidence([_rule("R-S1-1", "S1", "п.1.9",
                                     statement="rest_home ≥ 10 ч")])
    assert ev is None and "менее чем на две строки" in why


def test_evidence_is_pure():
    """Функция свидетельства ничего не меняет во входных строках."""
    rules = _fit_pair(600, 720)
    before = json.dumps(rules, ensure_ascii=False, sort_keys=True)
    contra.evidence(rules)
    assert json.dumps(rules, ensure_ascii=False, sort_keys=True) == before


# ------------------------------------------------------------------- схема

def _doc_with_contradiction(block):
    doc = sample_model()
    q = next(q for q in doc["questions"] if q["part"] == "1")
    q["contradiction"] = block
    return doc


def test_schema_accepts_evidence_block():
    doc = _doc_with_contradiction({
        "norm": {"family": "rest_rules", "param": "min_rest_home"},
        "values": [
            {"rule_id": "R-S1-П-1-4", "value": 600, "unit": None,
             "source": {"sid": "S1", "anchor": "П.1.4"}},
            {"rule_id": "R-S2-П-5-1", "value": 720, "unit": None,
             "source": {"sid": "S2", "anchor": "П.5.1"}},
        ]})
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("block", [
    {"values": []},                                    # без нормы
    {"norm": {"param": "min_rest_home"}, "values": [   # норма без носителя
        {"rule_id": "R-S1-П-1-4", "value": 600,
         "source": {"sid": "S1", "anchor": "П.1.4"}},
        {"rule_id": "R-S2-П-5-1", "value": 720,
         "source": {"sid": "S2", "anchor": "П.5.1"}}]},
    {"norm": {"family": "rest_rules", "param": "min_rest_home"},
     "values": [{"rule_id": "R-S1-П-1-4", "value": 600,
                 "source": {"sid": "S1", "anchor": "П.1.4"}}]},   # одно
    {"norm": {"family": "rest_rules", "param": "min_rest_home",
              "чужое": 1},
     "values": [{"rule_id": "R-S1-П-1-4", "value": 600,
                 "source": {"sid": "S1", "anchor": "П.1.4"}},
                {"rule_id": "R-S2-П-5-1", "value": 720,
                 "source": {"sid": "S2", "anchor": "П.5.1"}}]},
])
def test_schema_rejects_broken_evidence(block):
    assert m.validate_schema(_doc_with_contradiction(block)) != []


# ------------------------------------------------- сводка принципов

def test_principles_words_name_machine_basis():
    """Панель «Итог» называет основание принципа 1 честно: противоречия
    — только машинно видимые."""
    from cdpm import principles as pr
    p1 = pr.summary(sample_model())["P1"]
    assert "машинно видимые" in p1["title"]
    assert "часть 2" in p1["basis"]
