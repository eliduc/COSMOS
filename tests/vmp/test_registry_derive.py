# -*- coding: utf-8 -*-
"""Авто-вывод реестра исполнителей из базы шаблонов (registry_derive).

Постановка P-N-SHD (2026-08-30): вердикты выводятся из машинных
признаков контракта пакета (kind, export_solver.family, имена
параметров), а не издаются рукой под конкретный прогон. Закон
приёмки принципа: правило ЧУЖОГО мира того же типа покрывается без
правки кода; правило нового типа — честный пробел, который deep_check
предъявляет поимённо.

Фикстуры синтетические: два выдуманных мира с разными титулами,
числами и id — данных заказчика нет и быть не может.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import registry_check as rc  # noqa: E402
from vmp import registry_derive as rd  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in rc.EXECUTOR_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# исполнитель %s\n" % rel, encoding="utf-8")
    return root


# Мир А: полный спектр видов — по вердикту на каждый машинный признак.
WORLD_A = {"rules": [
    {"id": "R-A-1", "kind": "DATA", "zone": "BOTH",
     "title": "плечи сети"},
    {"id": "R-A-2", "kind": "OUT", "zone": "BOTH",
     "title": "ручное согласование"},
    {"id": "R-A-3", "kind": "HARD", "zone": "PAIRING",
     "title": "предел смены",
     "params": [{"name": "max_duty_duration", "value": "11:30"}]},
    {"id": "R-A-4", "kind": "DEF", "zone": "BOTH",
     "title": "шкала времени",
     "params": [{"name": "base_tz_offset_min", "value": 0}]},
    {"id": "R-A-5", "kind": "SOFT", "zone": "PAIRING",
     "title": "ночёвки вне базы", "weight": 10,
     "export_solver": {"family": "layover_penalty"}},
    {"id": "R-A-6", "kind": "SOFT", "zone": "PAIRING",
     "title": "продление с согласия", "weight": 5},
    {"id": "R-A-7", "kind": "HARD", "zone": "PAIRING",
     "title": "неведомый порог",
     "params": [{"name": "какой_то_новый_порог", "value": 1}]},
    {"id": "R-A-8", "kind": "SOFT", "zone": "PAIRING",
     "title": "семья не из словаря", "weight": 2,
     "export_solver": {"family": "нет_такой_семьи"}},
    {"id": "R-A-9", "kind": "HARD", "zone": "ROSTERING",
     "title": "правило без параметров"},
]}

# Мир Б: ЧУЖИЕ титулы, id и числа тех же типов — закон приёмки
# P-N-SHD: покрытие обязано выйти тем же без правки кода.
WORLD_B = {"rules": [
    {"id": "REGLA-77", "kind": "HARD", "zone": "PAIRING",
     "title": "límite de jornada",
     "params": [{"name": "max_duty_duration", "value": "13:45"},
                {"name": "min_turnaround", "value": "00:50"}]},
    {"id": "REGLA-78", "kind": "SOFT", "zone": "PAIRING",
     "title": "pernocta fuera de base", "weight": 4,
     "export_solver": {"family": "layover_penalty"}},
    {"id": "REGLA-79", "kind": "HARD", "zone": "ROSTERING",
     "title": "reservas por base",
     "params": [{"name": "reserve_crews", "value": 2},
                {"name": "reserve_crews", "value": 1}]},
    # Семья родилась конвейером шаблонов 2026-08-31 (демо-заявка):
    # чужое правило того же типа покрывается без правки кода.
    {"id": "REGLA-80", "kind": "SOFT", "zone": "BOTH",
     "title": "prórroga con consentimiento", "weight": 4,
     "export_solver": {"family": "duty_extension_penalty"},
     "params": [{"name": "max_duty_duration", "value": 800,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "13:20"}]},
]}


def test_вердикты_выводятся_из_машинных_признаков(tmp_path):
    reg = rd.derive_registry(WORLD_A, _fake_repo(tmp_path), "тест")
    by_id = {r["rule_id"]: r for r in reg["rules"]}
    assert by_id["R-A-1"]["verdict"] == "data_or_out_of_scope"
    assert by_id["R-A-2"]["verdict"] == "data_or_out_of_scope"
    assert by_id["R-A-3"]["verdict"] == "covered_full"
    assert "duty_limits" in by_id["R-A-3"]["evidence"]
    assert by_id["R-A-4"]["verdict"] == "covered_full"
    assert "time_scale" in by_id["R-A-4"]["evidence"]
    assert by_id["R-A-5"]["verdict"] == "covered_full"
    assert "layover_penalty" in by_id["R-A-5"]["evidence"]
    # Мягкое без объявленного типа механики — пробел, даже взвешенное.
    assert by_id["R-A-6"]["verdict"] == rd.GAP_NO_MECHANIC
    assert "заявка" in by_id["R-A-6"]["gap_note"]
    assert by_id["R-A-7"]["verdict"] == rd.GAP_PARAM_UNKNOWN
    assert "какой_то_новый_порог" in by_id["R-A-7"]["gap_note"]
    assert by_id["R-A-8"]["verdict"] == rd.GAP_FAMILY_UNKNOWN
    assert by_id["R-A-9"]["verdict"] == rd.GAP_NO_MECHANIC


def test_закон_приёмки_чужой_мир_покрыт_без_правки_кода(tmp_path):
    reg = rd.derive_registry(WORLD_B, _fake_repo(tmp_path), "мир Б")
    verdicts = {r["rule_id"]: r["verdict"] for r in reg["rules"]}
    assert verdicts == {"REGLA-77": "covered_full",
                       "REGLA-78": "covered_full",
                       "REGLA-79": "covered_full",
                       "REGLA-80": "covered_full"}
    by_id = {r["rule_id"]: r for r in reg["rules"]}
    assert "duty_extension_penalty" in by_id["REGLA-80"]["evidence"]


def test_выведенный_реестр_по_форме_и_судится_deep_check(tmp_path):
    root = _fake_repo(tmp_path)
    p = tmp_path / "derived.json"
    rd.derive_and_write(WORLD_A, root, p, "форма")
    # Форма принимается загрузчиком источника истины (fail-closed).
    reg = rc.load_registry(p)
    assert reg["derived"] is True
    # Пробелы вывода останавливают запуск поимённо.
    gaps = rc.deep_check(WORLD_A["rules"], None, reg,
                         zones=("PAIRING", "BOTH"))
    gap_ids = {g["rule_id"] for g in gaps}
    assert gap_ids == {"R-A-6", "R-A-7", "R-A-8"}
    for g in gaps:
        assert g["note"], "пробел обязан говорить словами"
    # Полностью покрытый чужой мир проходит рубеж без пробелов.
    p2 = tmp_path / "derived-b.json"
    rd.derive_and_write(WORLD_B, root, p2, "мир Б")
    assert rc.deep_check(WORLD_B["rules"], None, rc.load_registry(p2),
                         zones=("PAIRING", "BOTH", "ROSTERING")) == []


def test_сторож_дрейфа_жив_у_выведенного_реестра(tmp_path):
    root = _fake_repo(tmp_path)
    p = tmp_path / "derived.json"
    rd.derive_and_write(WORLD_B, root, p, "дрейф")
    reg = rc.load_registry(p)
    assert rc.drift_check(reg, root) == []
    (root / rc.EXECUTOR_FILES[0]).write_text("# изменился\n",
                                             encoding="utf-8")
    assert rc.drift_check(reg, root) == [rc.EXECUTOR_FILES[0]]
    # Перевывод — штатная пересверка: отпечатки снимаются заново.
    rd.derive_and_write(WORLD_B, root, p, "дрейф-2")
    assert rc.drift_check(rc.load_registry(p), root) == []


def test_повторный_вывод_перезаписывает_атомарно(tmp_path):
    root = _fake_repo(tmp_path)
    p = tmp_path / "derived.json"
    rd.derive_and_write(WORLD_A, root, p, "первый")
    rd.derive_and_write(WORLD_B, root, p, "второй")
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["scope"] == "второй"
    assert {r["rule_id"] for r in doc["rules"]} \
        == {"REGLA-77", "REGLA-78", "REGLA-79", "REGLA-80"}
    leftovers = [x.name for x in tmp_path.iterdir()
                 if x.name.startswith("derived.json.")]
    assert leftovers == [], "временных огрызков быть не должно"


def test_определение_без_параметров_вне_контура_расчёта():
    # Лестница H0 (отмашка владельца 2026-09-01): DEF без машинных
    # параметров — определение мира, исполнителя не требует.
    from vmp.registry_derive import derive_rule
    rec = derive_rule({"id": "R-def", "title": "Базы приписки",
                       "kind": "DEF", "zone": "BOTH",
                       "statement": "base ∈ {SVG, PLY, GRT}"})
    assert rec["verdict"] == "data_or_out_of_scope"
    assert "определение мира" in rec["evidence"]
    # а DEF С параметрами судится как прежде (параметрное определение
    # — норма, требует механику)
    rec2 = derive_rule({"id": "R-def2", "title": "Ночная смена",
                        "kind": "DEF", "zone": "PAIRING",
                        "statement": "ночная = пересекает окно",
                        "params": [{"name": "no_such_param",
                                    "value": 1}]})
    assert rec2["verdict"] != "data_or_out_of_scope"


# Одна правда о семье (критика принципов 2026-09-02, П4): посадка на
# шаблон A/B первична для любого вида; export_solver.family / имена
# параметров — запас формы до посадок; две правды — пробел.

def _fit(family: str, cat: str = "A", **params) -> dict:
    fit = {"category": cat, "family": family, "registry_rev": "t",
           "quote": "дословный носитель нормы"}
    if params:
        fit["params"] = {k: {"value": v, "quote": str(v)}
                         for k, v in params.items()}
    if cat == "B":
        fit["assumption"] = "допущение"
    return fit


def test_посадка_первична_для_мягкого_без_поля_export_solver():
    rec = rd.derive_rule({"id": "S-1", "kind": "SOFT", "zone": "BOTH",
                          "title": "продление", "weight": 5,
                          "template_fit": _fit("duty_extension_penalty",
                                               max_duty_duration=780)})
    assert rec["verdict"] == "covered_full"
    assert "duty_extension_penalty" in rec["evidence"]
    assert "посадка" in rec["evidence"]


def test_две_правды_о_семье_мягкого_это_пробел_а_не_тихий_выбор():
    rec = rd.derive_rule({"id": "S-2", "kind": "SOFT", "zone": "PAIRING",
                          "title": "ночёвки в конце недели", "weight": 3,
                          "export_solver": {"family": "layover_penalty"},
                          "template_fit": _fit("calendar_windows",
                                               days=["saturday"])})
    assert rec["verdict"] == rd.GAP_FAMILY_CONFLICT
    assert "calendar_windows" in rec["gap_note"]
    assert "layover_penalty" in rec["gap_note"]
    # Согласные правды — покрытие, без шума.
    rec2 = rd.derive_rule({"id": "S-3", "kind": "SOFT", "zone": "PAIRING",
                           "title": "ночёвки", "weight": 3,
                           "export_solver": {"family": "layover_penalty"},
                           "template_fit": _fit("layover_penalty")})
    assert rec2["verdict"] == "covered_full"


def test_посадка_первична_для_жёсткого_и_конфликт_со_словарём():
    ok = rd.derive_rule({"id": "H-1", "kind": "HARD", "zone": "PAIRING",
                         "title": "предел смены",
                         "params": [{"name": "max_duty_duration",
                                     "value": "12:00"}],
                         "template_fit": _fit("duty_limits",
                                              max_duty_duration=720)})
    assert ok["verdict"] == "covered_full"
    assert "duty_limits" in ok["evidence"]
    # Посадка в чужую семью при словарных параметрах другой семьи —
    # две правды, пробел поимённо.
    bad = rd.derive_rule({"id": "H-2", "kind": "HARD", "zone": "ROSTERING",
                          "title": "предел смены",
                          "params": [{"name": "max_duty_duration",
                                      "value": "12:00"}],
                          "template_fit": _fit("rest_floors",
                                               min_rest_home=720)})
    assert bad["verdict"] == rd.GAP_FAMILY_CONFLICT
    assert "rest_floors" in bad["gap_note"]
    assert "duty_limits" in bad["gap_note"]
    # Посадка с параметрами вне словаря запаса: посадка — закон базы.
    only_fit = rd.derive_rule({"id": "H-3", "kind": "HARD",
                               "zone": "ROSTERING", "title": "резерв",
                               "template_fit": _fit("reserve_floors",
                                                    reserve_crews=2)})
    assert only_fit["verdict"] == "covered_full"
    # Семья посадки вне базы — пробел «семья не значится».
    alien = rd.derive_rule({"id": "H-4", "kind": "HARD", "zone": "BOTH",
                            "title": "чужая семья",
                            "template_fit": _fit("нет_такой_семьи")})
    assert alien["verdict"] == rd.GAP_FAMILY_UNKNOWN


def test_семья_флота_посажена_на_перечень_типов():
    """Отмашка владельца 2026-09-03: правило допуска/исключения по типу
    судна с параметром fleet_types (единица CDPM «перечень кодов»)
    покрыто семьёй fleet_exclusions; исполнители у решателя названы;
    коды — из документа, шаблон обезличен (чужой мир покрыт без
    правки кода)."""
    from vmp import template_registry as reg
    assert reg.PARAM_TO_FAMILY["fleet_types"] == "fleet_exclusions"
    # 0.2.0-draft.2 (слово владельца 08.09): к перечню типов прибавлен
    # синоним «один код» fleet_type — та же ручка решателя FLEET_THRESHOLD.
    assert reg.PARAM_TO_FAMILY["fleet_type"] == "fleet_exclusions"
    assert reg.families()["fleet_exclusions"] == ["fleet_type", "fleet_types"]
    execs = reg.families_map()["rule_families"]["fleet_exclusions"]["executors"]
    assert any("typeRatingRequired" in e for e in execs)
    assert any("typeRatingCurrency" in e for e in execs)
    for world, codes in (("Ладога", "ЛТ-100, ЛТ-200"), ("Полярис", "Полярис-Р200")):
        got = rd.derive_rule({"id": "H-FLEET", "kind": "HARD", "zone": "BOTH",
                              "title": "допуск по типу (%s)" % world,
                              "params": [{"name": "fleet_types",
                                          "value": codes}],
                              "template_fit": _fit("fleet_exclusions",
                                                   fleet_types=codes)})
        assert got["verdict"] == "covered_full", got
        assert "fleet_exclusions" in got["evidence"]
    # Перечень типов в чужой семье — две правды, пробел поимённо.
    bad = rd.derive_rule({"id": "H-X", "kind": "HARD", "zone": "BOTH",
                          "title": "допуск по типу",
                          "params": [{"name": "fleet_types",
                                      "value": "ЛТ-100"}],
                          "template_fit": _fit("duty_limits",
                                               max_duty_duration=720)})
    assert bad["verdict"] == rd.GAP_FAMILY_CONFLICT


def test_посадка_c_d_не_семья_решает_запас_формы():
    # Посадка C (пробел) — семьи нет; запас export_solver.family жив.
    rec = rd.derive_rule({"id": "S-4", "kind": "SOFT", "zone": "PAIRING",
                          "title": "ночёвки", "weight": 1,
                          "export_solver": {"family": "layover_penalty"},
                          "template_fit": {"category": "C",
                                           "registry_rev": "t",
                                           "gap": "шаблона нет"}})
    assert rec["verdict"] == "covered_full"
    assert "посадка" not in rec["evidence"]
    # Посадка D (кандидаты) без запаса — честный пробел.
    rec2 = rd.derive_rule({"id": "S-5", "kind": "SOFT", "zone": "PAIRING",
                           "title": "неясная механика", "weight": 1,
                           "template_fit": {"category": "D",
                                            "registry_rev": "t",
                                            "candidates": ["a", "b"]}})
    assert rec2["verdict"] == rd.GAP_NO_MECHANIC


def test_исключено_до_получения_данных_финальный_вердикт(tmp_path):
    """Структура ВМП (владелец 2026-09-02): пометка excluded_until_data
    — финальный вердикт реестра со следом вопроса, не пробел."""
    rule = {"id": "H-9", "kind": "HARD", "zone": "PAIRING",
            "title": "норма без данных",
            "params": [{"name": "какой_то_новый_порог", "value": 1}],
            "excluded_until_data": {"question": "Q-3b-нет-данных-9",
                                    "revision": 2}}
    rec = rd.derive_rule(rule)
    assert rec["verdict"] == "excluded_until_data"
    assert "Q-3b-нет-данных-9" in rec["evidence"]
    assert rec["verdict"] in rc.FINAL_VERDICTS
    root = _fake_repo(tmp_path)
    p = tmp_path / "derived.json"
    rd.derive_and_write({"rules": [rule]}, root, p, "исключено")
    assert rc.deep_check([rule], None, rc.load_registry(p),
                         zones=("PAIRING", "BOTH")) == []


def test_конфликт_семей_останавливает_deep_check_поимённо(tmp_path):
    world = {"rules": [
        {"id": "S-2", "kind": "SOFT", "zone": "PAIRING",
         "title": "ночёвки в конце недели", "weight": 3,
         "export_solver": {"family": "layover_penalty"},
         "template_fit": _fit("calendar_windows")},
    ]}
    root = _fake_repo(tmp_path)
    p = tmp_path / "derived.json"
    rd.derive_and_write(world, root, p, "конфликт")
    gaps = rc.deep_check(world["rules"], None, rc.load_registry(p),
                         zones=("PAIRING", "BOTH"))
    assert [g["rule_id"] for g in gaps] == ["S-2"]
    assert "две правды" in gaps[0]["note"]
