# -*- coding: utf-8 -*-
"""A05-барьер волны 17а (контракт П0 §3.1 законы 1 и 3, §12.5 и §9;
12.09.2026): у параметра правила бывает ДВА носителя — ячейка посадки
`template_fit.params.<имя>` и старая запись `params[]`. Пока живы оба,
они обязаны говорить одно и то же: значение (после приведения к
канонической единице семьи), единицу, отношение и условие/область.
Расхождение — не «первое попавшееся значение», а остановка:
`rule_param_cells` отдаёт пометку расхождения, рубеж выпуска отказывает
`param-carriers-diverge` с перечнем «правило/параметр», гейт
производителя краснит теми же словами. Сверка идёт по ИСХОДНЫМ
носителям — ДО самолечения сборкой (`enrich_rules_for_package` →
`canon_fit_params` переносит единицу и условие ЗАПИСИ в ячейку, и
контрпример аудитора «3 против 30» исчез бы до проверки).

Выведение `params[]` из посадки (`derive_params_from_landing`),
ведомость исполнения по значению и стендовая связка — волна 18: здесь
не проверяются."""
import copy
import hashlib
import json
import re

import pytest

from cdpm import export_task_package as tp
from cdpm import family_params as fp
from cdpm import model as m
from cdpm.gates import gate_task_package as gate

REV = m.registry_fingerprint()


def _ready_doc():
    """Свод уровня «в» (образец tests/test_cdpm_task_package.py): чистая
    жёсткая, взвешенная мягкая, критерий пейринга, отказ от оптимизации
    ростеринга."""
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1",
                 "revision": 2},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-S", "title": "Ночёвки",
             "statement": "штраф за ночь вне базы: вес 10",
             "zone": "PAIRING", "kind": "SOFT",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
        "questions": [
            {"id": "Q-3a-GOAL", "part": "3a", "title": "Целевая",
             "text": "т", "answer_format": {"type": "registry"},
             "rule_refs": ["R-S"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "таблица", "status": "применён",
                        "revision": 2, "goal_decline": False}},
            {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
             "text": "т2", "answer_format": {"type": "registry"},
             "rule_refs": ["R-H"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "любое допустимое решение",
                        "status": "применён", "revision": 2,
                        "goal_decline": True}},
        ],
        "objectives": [
            {"id": "O-1", "title": "Критерий оптимизации пейринга",
             "statement": "Минимум человеко-дней командировок; цена "
                          "рабочего дня (смены) — 100 условных единиц",
             "zone": "PAIRING", "direction": "minimize",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
    }


def _landed(rid="R-L", zone="PAIRING", kind="HARD", family="duty_limits",
            cells=None, legacy=None, rev=REV):
    """Правило-носитель с посадкой A. Набор параметров — ПОЛНЫЙ: неполная
    семья держала бы готовность сама (K01-барьер, соседний файл)."""
    r = {"id": rid, "title": "Посаженное", "statement": "не более 3 суток",
         "zone": zone, "kind": kind,
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "template_fit": {"category": "A", "registry_rev": rev,
                          "family": family,
                          "params": cells if cells is not None else
                          {"max_pairing_days": {
                              "value": 3, "quote": "не более 3 суток"}}}}
    if legacy is not None:
        r["params"] = legacy
    return r


def _legacy(name, value, unit="count", relation="max",
            as_written="не более 3 суток", **extra):
    """ЗАКОННАЯ запись `params[]`: валидатор свода требует у неё единицу
    каталога, отношение и дословный след. Барьер судит документ, каким
    его принимает контракт, а не обрывок."""
    out = {"name": name, "value": value, "unit": unit,
           "relation": relation, "as_written": as_written}
    out.update(extra)
    return out


def _doc_with(rule):
    doc = _ready_doc()
    doc["rules"].append(rule)
    return doc


def _run_dir(tmp_path, doc, name="run-9001"):
    run = tmp_path / name
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text("# свод\n", encoding="utf-8")
    return run


def _repack(files: dict, doc: dict) -> dict:
    """Пакет с подменённым model.json и пересчитанными sha256 манифеста:
    гейт судит БАРЬЕР, а не порчу байтов (её судит сосед по набору,
    tests/test_cdpm_task_package.py)."""
    out = dict(files)
    out["model.json"] = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(out["manifest.json"])
    for row in manifest.get("files", []):
        name = row.get("name")
        if name in out and "sha256" in row:
            row["sha256"] = hashlib.sha256(out[name]).hexdigest()
    out["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    return out


# --------------------------------------------------------------------
# Сверка носителей: значение, единица, отношение, условие
# --------------------------------------------------------------------

def test_sc2_three_against_thirty_names_rule_param_and_both_values():
    """Контрпример аудитора SC-2: посадка говорит 3, запись params[] —
    30. Слова называют правило, параметр и ОБА значения с единицами."""
    r = _landed(legacy=[{"name": "max_pairing_days", "value": 30}])
    words = m.reconcile_carriers(r)
    assert len(words) == 1, words
    one = words[0]
    assert "R-L" in one and "max_pairing_days" in one
    assert "3 count" in one and "30 count" in one


def test_carriers_that_agree_are_not_a_divergence():
    """Согласные носители молчат — барьер не ловит совпадение."""
    r = _landed(legacy=[{"name": "max_pairing_days", "value": 3}])
    assert m.reconcile_carriers(r) == []
    assert m.rule_param_cells(r) == {"max_pairing_days": 3}


def test_unit_words_are_normalised_before_compare():
    """Единицы приводятся к канонической единице семьи минимальным
    перечнем K02 (контракт §12.6): 50 ч = 3000 минут — согласие; 49 ч —
    расхождение, и в словах обе величины после приведения."""
    cells = {"max_weekly_block": {"value": 3000,
                                  "unit": "duration_minutes",
                                  "quote": "не более 50 часов в неделю"}}
    same = _landed(family="span_accumulators", cells=cells,
                   legacy=[{"name": "max_weekly_block", "value": 50,
                            "unit": "ч"}])
    assert m.reconcile_carriers(same) == []
    other = _landed(family="span_accumulators", cells=cells,
                    legacy=[{"name": "max_weekly_block", "value": 49,
                             "unit": "ч"}])
    words = m.reconcile_carriers(other)
    assert len(words) == 1 and "max_weekly_block" in words[0]
    assert "3000 duration_minutes" in words[0]
    assert "2940 duration_minutes" in words[0]


def test_carrier_without_unit_is_read_in_the_family_unit():
    """Носитель БЕЗ единицы уже в канонической единице семьи (закон
    §12.5): 3000 без единицы согласно с 3000 минутами, 50 без единицы —
    расходится (50 минут против 3000)."""
    cells = {"max_weekly_block": {"value": 3000,
                                  "unit": "duration_minutes", "quote": "q"}}
    same = _landed(family="span_accumulators", cells=cells,
                   legacy=[{"name": "max_weekly_block", "value": 3000}])
    assert m.reconcile_carriers(same) == []
    other = _landed(family="span_accumulators", cells=cells,
                    legacy=[{"name": "max_weekly_block", "value": 50}])
    assert len(m.reconcile_carriers(other)) == 1


def test_relation_divergence_is_named():
    """Отношение сверяется наравне со значением; у ячейки без своего
    `relation` отношение берётся из записи семьи каталога."""
    cells = {"min_rest_home": {"value": 720, "quote": "08:00",
                               "relation": "min"},
             "min_rest_outstation": {"value": 600, "quote": "10:00"}}
    r = _landed(family="rest_floors", zone="ROSTERING", cells=cells,
                legacy=[{"name": "min_rest_home", "value": 720,
                         "relation": "max"}])
    words = m.reconcile_carriers(r)
    assert len(words) == 1 and "отношение" in words[0]
    assert "min" in words[0] and "max" in words[0]
    # Ячейка без своего отношения судится отношением записи семьи (min).
    cells2 = copy.deepcopy(cells)
    cells2["min_rest_home"].pop("relation")
    ok = _landed(family="rest_floors", zone="ROSTERING", cells=cells2,
                 legacy=[{"name": "min_rest_home", "value": 720,
                          "relation": "min"}])
    assert m.reconcile_carriers(ok) == []


def test_condition_is_compared_structurally_and_by_canonical_area():
    """Условие/область входит в сверку (§12.5): структурное условие
    ячейки против уточнителя записи — по канону области; та же область
    словами — согласие, другая — расхождение."""
    def rule(qualifier):
        return _landed(family="connection_windows", cells={
            "min_turnaround": {"value": 45, "quote": "q",
                               "condition": {"kind": "area",
                                             "value": "база"}}},
            legacy=[{"name": "min_turnaround", "value": 45,
                     "qualifier": qualifier}])
    assert m.reconcile_carriers(rule("в базе")) == []
    words = m.reconcile_carriers(rule("вне базы"))
    assert len(words) == 1 and "условие" in words[0]


def test_name_carried_by_one_carrier_only_is_not_a_divergence():
    """Имя, которое несёт только один носитель, расхождением не
    является: сверяются ТОЛЬКО общие имена."""
    r = _landed(cells={"max_pairing_days": {"value": 3, "quote": "q"}},
                legacy=[{"name": "max_duty_duration", "value": 720}])
    assert m.reconcile_carriers(r) == []


def test_unreadable_carrier_is_a_problem_not_a_silent_match():
    """Нечитаемое значение или единица — тоже проблема: «совпало» не
    выдумывается (fail-closed)."""
    bad_value = _landed(cells={"max_pairing_days": {"value": "три",
                                                    "quote": "q"}},
                        legacy=[{"name": "max_pairing_days", "value": 3}])
    words = m.reconcile_carriers(bad_value)
    assert len(words) == 1 and "сверка невозможна" in words[0]
    bad_unit = _landed(family="span_accumulators", cells={
        "max_weekly_block": {"value": 50, "unit": "фунт", "quote": "q"}},
        legacy=[{"name": "max_weekly_block", "value": 3000}])
    words = m.reconcile_carriers(bad_unit)
    assert len(words) == 1 and "сверка невозможна" in words[0]


# --------------------------------------------------------------------
# Закон 3: rule_param_cells отдаёт проблему, а не первое значение
# --------------------------------------------------------------------

def test_rule_param_cells_gives_the_problem_instead_of_the_first_value():
    """Закон 3 контракта: при расхождении читатель получает пометку
    расхождения со словами, а не одну из двух правд. Имя по-прежнему
    «несёт строка» — темы и внесения не плодят третьего носителя."""
    r = _landed(legacy=[{"name": "max_pairing_days", "value": 30}])
    cells = m.rule_param_cells(r)
    assert "max_pairing_days" in cells
    got = cells["max_pairing_days"]
    assert isinstance(got, m.CarrierDivergence)
    assert got != 3 and got != 30
    assert str(got) == m.reconcile_carriers(r)[0]


# --------------------------------------------------------------------
# Рубеж выпуска и гейт производителя
# --------------------------------------------------------------------

def test_release_refuses_with_code_and_items(tmp_path):
    """Рубеж выпуска: отказ `param-carriers-diverge`, в `items` —
    «правило/параметр», в словах — обе правды и что делать."""
    doc = _doc_with(_landed(legacy=[{"name": "max_pairing_days",
                                     "value": 30}]))
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc))
    assert e.value.code == tp.PARAM_CARRIERS_DIVERGE
    assert tp.PARAM_CARRIERS_DIVERGE == "param-carriers-diverge"
    assert e.value.items == ["R-L/max_pairing_days"]
    assert "два носителя с разной правдой" in str(e.value)
    assert "3 count" in str(e.value) and "30 count" in str(e.value)


def test_release_compares_original_carriers_before_any_healing(tmp_path,
                                                               monkeypatch):
    """§12.5: сверка идёт по ИСХОДНЫМ носителям — до самолечения
    сборкой. Доказательство: самолечение (`enrich_rules_for_package`,
    внутри — `canon_fit_params`) при расхождении не выполняется ни
    разу."""
    calls = []
    real = tp.enrich_rules_for_package
    monkeypatch.setattr(tp, "enrich_rules_for_package",
                        lambda doc: (calls.append(doc), real(doc))[1])
    doc = _doc_with(_landed(legacy=[{"name": "max_pairing_days",
                                     "value": 30, "unit": "count"}]))
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc))
    assert e.value.code == tp.PARAM_CARRIERS_DIVERGE
    assert calls == [], "лечение посадки выполнено ДО сверки носителей"


def test_healing_canonises_the_cell_and_never_changes_its_value(tmp_path):
    """Исправление В-3 неавтор-ревью (прежде тест ждал обратного —
    «после лечения расхождения уже не видно»): канон посадки переносил
    в ячейку ЕДИНИЦУ записи, НЕ трогая значение, и «50 ч» становилось
    «50 минут» — подмена величины после пройденной сверки. Закон:
    единица ячейки канонизируется ВМЕСТЕ со значением тем же
    `canonical_value`, поэтому величина ячейки не меняется никогда — ни
    у расходящихся носителей (расхождение остаётся видно), ни у
    согласных."""
    r = _landed(family="span_accumulators", cells={
        "max_weekly_block": {"value": 50, "unit": "ч", "quote": "q"}},
        legacy=[{"name": "max_weekly_block", "value": 50,
                 "unit": "duration_minutes"}])
    assert len(m.reconcile_carriers(r)) == 1, "3000 минут против 50 минут"
    tp.canon_fit_params(r)                     # самолечение сборки
    cell = r["template_fit"]["params"]["max_weekly_block"]
    assert cell["unit"] == "duration_minutes" and cell["value"] == 3000
    assert len(m.reconcile_carriers(r)) == 1, \
        "лечение не прячет расхождение: величина ячейки та же (3000)"
    # Согласные носители в РАЗНЫХ единицах: пакет несёт ту же величину.
    ok = _doc_with(_landed(family="span_accumulators", cells={
        "max_weekly_block": {"value": 3000, "quote": "q"}},
        legacy=[_legacy("max_weekly_block", 50, unit="ч", relation="max",
                        as_written="50 часов")]))
    files = tp.build_package(_run_dir(tmp_path, ok, "run-9003"))
    pkg_cell = json.loads(files["model.json"])["rules"][-1][
        "template_fit"]["params"]["max_weekly_block"]
    value, bad = fp.canonical_value(pkg_cell.get("value"),
                                    pkg_cell.get("unit"), "duration_minutes")
    assert bad is None and value == 3000, pkg_cell


def test_producer_gate_is_red_with_the_same_words(tmp_path):
    """Гейт производителя зовёт ТОТ ЖЕ расчёт на model.json пакета и
    краснит теми же словами с кодом отказа рубежа."""
    good = _doc_with(_landed(legacy=[{"name": "max_pairing_days",
                                      "value": 3}]))
    files = tp.build_package(_run_dir(tmp_path, good))
    bad = copy.deepcopy(good)
    bad["rules"][-1]["params"] = [{"name": "max_pairing_days", "value": 30}]
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(_repack(files, bad)))
    problems, _ = gate.check(pkg)
    words = m.reconcile_carriers(bad["rules"][-1])[0]
    assert any(words in p for p in problems), problems
    assert any(tp.PARAM_CARRIERS_DIVERGE in p for p in problems), problems


def test_agreeing_carriers_pass_release_and_gate(tmp_path):
    """Зелёный контроль: согласные носители выпуску и гейту не мешают."""
    doc = _doc_with(_landed(legacy=[_legacy("max_pairing_days", 3)]))
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "в"
    files = tp.build_package(_run_dir(tmp_path, doc))
    pkg = tmp_path / "ok.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert problems == [], problems


# --------------------------------------------------------------------
# Классы ранней карты аудитора (PRECHECK-CLASSES…-20260912, §6)
# --------------------------------------------------------------------

def test_a05b01_defect_moves_sign_and_decimal_part():
    """A05b-01: дефект переносится по знаку, дробной части и месту.
    Эталон — не сравниваемая функция: ожидаемые величины выписаны
    руками."""
    # Дробная часть: 7,5 кредитного часа против 7,05 — расхождение.
    dec = _landed(family="balanced_workload", zone="ROSTERING", kind="SOFT",
                  cells={"workload_spread_threshold": {
                      "value": 7.5, "unit": "credit_hours", "quote": "7,5"}},
                  legacy=[_legacy("workload_spread_threshold", 7.05,
                                  unit="credit_hours",
                                  as_written="7,05 кредитного часа")])
    words = m.reconcile_carriers(dec)
    assert len(words) == 1 and "7.5" in words[0] and "7.05" in words[0]
    same = _landed(family="balanced_workload", zone="ROSTERING", kind="SOFT",
                   cells={"workload_spread_threshold": {
                       "value": 7.5, "unit": "credit_hours", "quote": "7,5"}},
                   legacy=[_legacy("workload_spread_threshold", 7.50,
                                   unit="credit_hours",
                                   as_written="7,50 кредитного часа")])
    assert m.reconcile_carriers(same) == []
    # Знак: −600 против 600 — расхождение, а не «одно и то же по модулю».
    sign = _landed(family="rest_floors", zone="ROSTERING", cells={
        "min_rest_home": {"value": 600, "unit": "duration_minutes",
                          "quote": "10:00"},
        "min_rest_outstation": {"value": 600, "unit": "duration_minutes",
                                "quote": "10:00"}},
        legacy=[_legacy("min_rest_home", -600, unit="duration_minutes",
                        relation="min", as_written="−600 минут")])
    words = m.reconcile_carriers(sign)
    assert len(words) == 1 and "-600" in words[0]


def test_a05b01_defect_in_the_second_param_and_second_rule():
    """A05b-01 (место дефекта): расхождение второго параметра строки
    называет ИМЕННО его; согласные параметры остаются значениями. Второе
    правило свода судится наравне с первым."""
    r = _landed(rid="R-N", family="night_series", cells={
        "max_consecutive_night_duties": {"value": 3, "unit": "count",
                                         "quote": "3"},
        "night_duty_start": {"value": 1320, "unit": "time_of_day_minutes",
                             "quote": "22:00"},
        "night_duty_end": {"value": 300, "unit": "time_of_day_minutes",
                           "quote": "05:00"}},
        legacy=[_legacy("max_consecutive_night_duties", 3,
                        as_written="не более 3"),
                _legacy("night_duty_start", 1200,
                        unit="time_of_day_minutes", relation="min",
                        as_written="20:00")])
    got = m.carrier_divergences(r)
    assert [d["param"] for d in got] == ["night_duty_start"]
    cells = m.rule_param_cells(r)
    assert cells["max_consecutive_night_duties"] == 3
    assert cells["night_duty_end"] == 300
    assert isinstance(cells["night_duty_start"], m.CarrierDivergence)
    doc = _doc_with(_landed())            # согласная строка рядом
    doc["rules"].append(r)
    assert [d["rule"] for d in m.carrier_problems(doc)] == ["R-N"]


def test_a05b02_sixty_minutes_and_one_hour_agree_two_hours_do_not():
    """A05b-02: 60 минут и 1 час — одно и то же (множитель словаря
    контракта ×60, выписан руками); 60 минут и 2 часа — расхождение;
    чужой код единицы каталога не угадывается."""
    def rule(value, unit):
        return _landed(family="rest_floors", zone="ROSTERING", cells={
            "min_rest_home": {"value": 60, "unit": "duration_minutes",
                              "quote": "60 минут"},
            "min_rest_outstation": {"value": 600,
                                    "unit": "duration_minutes",
                                    "quote": "10:00"}},
            legacy=[_legacy("min_rest_home", value, unit=unit,
                            relation="min", as_written=f"{value} {unit}")])
    assert m.reconcile_carriers(rule(1, "час")) == []
    assert m.reconcile_carriers(rule(1, "ч")) == []
    words = m.reconcile_carriers(rule(2, "часа"))
    assert len(words) == 1 and "120 duration_minutes" in words[0]
    # Несовместимый КОД каталога (count у параметра в минутах) — не
    # «равно» и не выдуманный масштаб, а явная невозможность сверки.
    bad = m.reconcile_carriers(rule(60, "count"))
    assert len(bad) == 1 and "сверка невозможна" in bad[0]


def test_a05b03_null_condition_is_not_a_wildcard_and_key_order_is_not_a_conflict():
    """A05b-03: `condition: null` — «вся область», а не «поле не
    сравнивать»; разные области расходятся; перестановка ключей одного и
    того же структурного условия конфликта не создаёт."""
    def rule(cell_condition, qualifier, with_key=True):
        cell = {"value": 45, "unit": "duration_minutes", "quote": "45 минут"}
        if with_key:
            cell["condition"] = cell_condition
        legacy = _legacy("min_turnaround", 45, unit="duration_minutes",
                         relation="min", as_written="45 минут")
        if qualifier is not None:
            legacy["qualifier"] = qualifier
        return _landed(family="connection_windows",
                       cells={"min_turnaround": cell}, legacy=[legacy])
    # null против «вне базы» — расхождение (null не «игнорировать поле»).
    words = m.reconcile_carriers(rule(None, "вне базы"))
    assert len(words) == 1 and "условие" in words[0]
    # база против «вне базы» — расхождение; база против «в базе» — нет.
    area = {"kind": "area", "value": "база"}
    assert len(m.reconcile_carriers(rule(area, "вне базы"))) == 1
    assert m.reconcile_carriers(rule(area, "в базе")) == []
    # Перестановка ключей того же условия — то же самое условие.
    swapped = {"value": "база", "kind": "area"}
    assert m.reconcile_carriers(rule(swapped, "в базе")) == []
    # Обе стороны без области — согласие.
    assert m.reconcile_carriers(rule(None, None)) == []


def test_a05b04_duplicate_legacy_entries_cannot_be_averaged_away():
    """A05b-04: две записи `params[]` одного имени — одна согласна,
    другая нет. Нельзя взять первую и объявить «равны»: расхождение
    предъявляется."""
    r = _landed(legacy=[_legacy("max_pairing_days", 3),
                        _legacy("max_pairing_days", 30,
                                as_written="не более 30 суток")])
    words = m.reconcile_carriers(r)
    assert len(words) == 1 and "30 count" in words[0]
    assert isinstance(m.rule_param_cells(r)["max_pairing_days"],
                      m.CarrierDivergence)


def test_a05b05_single_carrier_and_non_landed_rules_invent_nothing():
    """A05b-05: один носитель, пустой `params[]`, посадка не A/B —
    расхождения не выдумываются (и полноты с исполнимостью это тоже не
    доказывает: их судят соседние барьеры)."""
    assert m.reconcile_carriers(_landed()) == []            # только посадка
    assert m.reconcile_carriers(_landed(legacy=[])) == []   # пустой список
    only_legacy = {"id": "R-OLD", "kind": "HARD", "zone": "PAIRING",
                   "params": [_legacy("max_pairing_days", 30)]}
    assert m.reconcile_carriers(only_legacy) == []
    gap = _landed(legacy=[_legacy("max_pairing_days", 30)])
    gap["template_fit"] = {"category": "C", "registry_rev": REV,
                           "gap": "механики нет"}
    assert m.reconcile_carriers(gap) == []


def test_a05b06_order_does_not_change_the_verdict():
    """A05b-06: перестановка правил свода и имён параметров строки не
    меняет ни вердикта, ни адреса находки."""
    doc = _doc_with(_landed(legacy=[_legacy("max_pairing_days", 30,
                                            as_written="не более 30 суток")]))
    first = [(d["rule"], d["param"]) for d in m.carrier_problems(doc)]
    doc["rules"].reverse()
    assert [(d["rule"], d["param"])
            for d in m.carrier_problems(doc)] == first
    r = _landed(family="rest_floors", zone="ROSTERING", cells={
        "min_rest_outstation": {"value": 600, "unit": "duration_minutes",
                                "quote": "10:00"},
        "min_rest_home": {"value": 720, "unit": "duration_minutes",
                          "quote": "12:00"}},
        legacy=[_legacy("min_rest_home", 600, unit="duration_minutes",
                        relation="min", as_written="10:00")])
    assert [d["param"] for d in m.carrier_divergences(r)] == ["min_rest_home"]


def test_a05b06_release_rechecks_carriers_and_does_not_trust_an_earlier_ok(
        tmp_path):
    """A05b-06: авторитетный рубеж судит ТЕ носители, которые выпускает.
    Успешный выпуск не даёт пропуска следующему: правка `params[]` после
    него останавливает выпуск, и конфликтного пакета не выдаётся."""
    doc = _doc_with(_landed(legacy=[_legacy("max_pairing_days", 3)]))
    run = _run_dir(tmp_path, doc)
    assert tp.build_package(run)                      # первый выпуск — есть
    doc["rules"][-1]["params"] = [_legacy("max_pairing_days", 30,
                                          as_written="не более 30 суток")]
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert e.value.code == tp.PARAM_CARRIERS_DIVERGE
    assert not list(tmp_path.glob("*.zip"))


def test_a05b03_null_relation_in_the_cell_does_not_silence_the_comparison():
    """A05b-03 (пустое поле — не «игнорировать»): `relation: null` в
    ячейке — то же, что ключа нет, и отношение берётся из записи семьи
    (`rest_floors.min_rest_home` — `min`). Иначе одним `null`
    расхождение отношений выключалось бы, хотя канон посадки всё равно
    перенёс бы отношение записи в ячейку."""
    def rule(cell_relation, legacy_relation, with_key=True):
        cell = {"value": 720, "unit": "duration_minutes", "quote": "12:00"}
        if with_key:
            cell["relation"] = cell_relation
        cells = {"min_rest_home": cell,
                 "min_rest_outstation": {"value": 600,
                                         "unit": "duration_minutes",
                                         "quote": "10:00"}}
        return _landed(family="rest_floors", zone="ROSTERING", cells=cells,
                       legacy=[_legacy("min_rest_home", 720,
                                       unit="duration_minutes",
                                       relation=legacy_relation,
                                       as_written="12:00")])
    for cell_relation, with_key in ((None, True), (None, False)):
        words = m.reconcile_carriers(rule(cell_relation, "max", with_key))
        assert len(words) == 1 and "отношение" in words[0], \
            f"{cell_relation!r}/{with_key}: {words}"
        assert "min" in words[0] and "max" in words[0]
        # Соседний законный случай: запись согласна с отношением семьи.
        assert m.reconcile_carriers(rule(cell_relation, "min", with_key)) == []


def test_a05b04_identical_records_that_are_not_numbers_are_not_a_problem():
    """A05b-04/X-06: значение параметра бывает не числом — массив или
    объект обозначений (контракт §3.1). Два носителя с ОДНОЙ И ТОЙ ЖЕ
    записью не расходятся, и «сверка невозможна» о них не выдумывается;
    отличие хоть в одном элементе — снова предмет барьера."""
    def rule(cell_value, legacy_value, unit="code_list"):
        return _landed(family="fleet_exclusions", cells={
            "fleet_types": {"value": cell_value, "unit": unit,
                            "quote": "типы"}},
            legacy=[_legacy("fleet_types", legacy_value, unit=unit,
                            relation="in", as_written="типы")])
    assert m.reconcile_carriers(rule(["A320", "B738"], ["A320", "B738"])) == []
    assert m.reconcile_carriers(rule({"AAA": 1}, {"AAA": 1})) == []
    for cell_value, legacy_value in ((["A320", "B738"], ["A320", "B739"]),
                                     (["A320"], ["A320", "B738"]),
                                     ({"AAA": 1}, {"AAA": 2}),
                                     ({"AAA": 1}, {"BBB": 1})):
        words = m.reconcile_carriers(rule(cell_value, legacy_value))
        assert len(words) == 1, f"{cell_value!r} / {legacy_value!r}: {words}"
    # Одинаковая запись при РАЗНЫХ словах единицы — не «совпало» молча.
    r = _landed(family="fleet_exclusions", cells={
        "fleet_types": {"value": ["A320"], "unit": "code_list",
                        "quote": "типы"}},
        legacy=[_legacy("fleet_types", ["A320"], unit="count",
                        relation="in", as_written="типы")])
    assert len(m.reconcile_carriers(r)) == 1
    # Логическое `true` — не число 1: подмена типа сверкой не съедается.
    r2 = _landed(cells={"max_pairing_days": {"value": True, "quote": "q"}},
                 legacy=[_legacy("max_pairing_days", 1)])
    assert len(m.reconcile_carriers(r2)) == 1


# --------------------------------------------------------------------
# Находки неавтор-ревью ранней части (В-1…В-5, Н-1, Н-2; 12.09.2026)
# --------------------------------------------------------------------

def _gate_problems(tmp_path, files: dict, name: str) -> list:
    """Проблемы гейта производителя на ВЫПУЩЕННОМ пакете."""
    pkg = tmp_path / name
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    return problems


def _no_a05(problems: list) -> bool:
    return not any(tp.PARAM_CARRIERS_DIVERGE in p for p in problems)


def _released(tmp_path, doc, name):
    """(files | None, ошибка | None) — реальный путь выпуска пакета."""
    try:
        return tp.build_package(_run_dir(tmp_path, doc, name)), None
    except tp.TaskPackageError as e:
        return None, e


# --- В-1: значение-НЕскаляр грамматике числа не показывается ---------

def test_b1_object_values_of_different_bases_are_a_divergence(tmp_path):
    """В-1: `canonical_value` брала `str(raw)`, и грамматика числа
    вычитывала «1» из «{'AAA': 1}» — два носителя РАЗНЫХ баз «совпадали».
    Закон: структурная запись сверяется записью; отличие — расхождение
    словами «расходятся», а не тихое согласие."""
    def rule(cell_value, legacy_value):
        return _landed(rid="R-RES", family="reserve_floors",
                       zone="ROSTERING", cells={
                           "reserve_crews": {"value": cell_value,
                                             "quote": "AAA ≥ 1 экипаж"}},
                       legacy=[_legacy("reserve_crews", legacy_value,
                                       unit="count", relation="min",
                                       as_written="AAA ≥ 1 экипаж")])
    files, err = _released(tmp_path, _doc_with(rule({"AAA": 1}, {"BBB": 1})),
                           "run-9101")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert err.items == ["R-RES/reserve_crews"]
    assert "расходятся" in str(err) and "AAA" in str(err) \
        and "BBB" in str(err)
    assert not list(tmp_path.rglob("*.zip"))
    # Обратный контроль: ОДНА И ТА ЖЕ запись — выпуск идёт, и барьера на
    # выпущенном пакете нет (форму записи судит схема пакета, не A05).
    files, err = _released(tmp_path, _doc_with(rule({"AAA": 1}, {"AAA": 1})),
                           "run-9102")
    assert files is not None, str(err)
    assert _no_a05(_gate_problems(tmp_path, files, "res-ok.zip"))


def test_b1_object_cell_against_a_scalar_with_a_base_qualifier(tmp_path):
    """В-1: объект нормы по базам против скаляра с уточнителем базы
    сверяется записью ТОЙ базы (§3.1). Нет записи названной базы —
    расхождение, «согласие» не выдумывается."""
    def rule(base):
        return _landed(rid="R-RES", family="reserve_floors",
                       zone="ROSTERING", cells={
                           "reserve_crews": {"value": {"AAA": 2},
                                             "quote": "AAA ≥ 2 экипажа"}},
                       legacy=[_legacy("reserve_crews", 2, unit="count",
                                       relation="min", qualifier=base,
                                       as_written="AAA ≥ 2 экипажа")])
    files, err = _released(tmp_path, _doc_with(rule("BBB")), "run-9103")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert "базы BBB" in str(err), str(err)
    # Та же база — согласие: сверяются числа этой базы.
    files, err = _released(tmp_path, _doc_with(rule("AAA")), "run-9104")
    assert files is not None, str(err)
    assert _no_a05(_gate_problems(tmp_path, files, "base-ok.zip"))
    # И величина той базы сверяется по-настоящему: 3 против 2 — спор.
    other = _doc_with(rule("AAA"))
    other["rules"][-1]["params"][0]["value"] = 3
    files, err = _released(tmp_path, other, "run-9105")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert "база AAA" in str(err), str(err)


def test_b1_list_value_never_enters_the_number_grammar(tmp_path):
    """В-1: перечень `[3]` против числа `3` — не «одно и то же число».
    Грамматике числа перечень не показывается вовсе."""
    value, bad = fp.canonical_value([3], "count", "count")
    assert value is None and bad, "перечень не читается числом"
    value, bad = fp.canonical_value({"AAA": 1}, "count", "count")
    assert value is None and bad, "объект не читается числом"
    files, err = _released(
        tmp_path, _doc_with(_landed(cells={"max_pairing_days": {
            "value": [3], "quote": "q"}},
            legacy=[_legacy("max_pairing_days", 3)])), "run-9106")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert "расходятся" in str(err)
    # Обратный контроль: скаляр против скаляра — выпуск и зелёный гейт.
    files, err = _released(
        tmp_path, _doc_with(_landed(legacy=[_legacy("max_pairing_days", 3)])),
        "run-9107")
    assert files is not None, str(err)
    assert _gate_problems(tmp_path, files, "scalar-ok.zip") == []


# --- В-2: пакет = вылеченные носители -------------------------------

def _sit_answer_doc(cell_value):
    """Свод, где ответ авиакомпании на вопрос конфликта стыковки вносит
    `params[] min_turnaround = 45` САМОЛЕЧЕНИЕМ СБОРКИ (после сверки
    исходных носителей)."""
    doc = _ready_doc()
    doc["rules"].append(_landed(
        rid="R-SIT", family="connection_windows",
        cells={"min_turnaround": {"value": cell_value,
                                  "quote": "45 минут"}}))
    doc["rules"][-1]["statement"] = "Стыковка между рейсами 45 мин"
    doc["questions"].append({
        "id": m.stable_question_id("3a", "условия-стыковка"),
        "part": "3a", "title": "Стыковка", "text": "сколько?",
        "answer_format": {"type": "value", "note": "минут"},
        "rule_refs": ["R-SIT"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"raw": "стыковка = 45 мин", "status": "применён",
                   "revision": 2}})
    return doc


def test_b2_release_rechecks_carriers_after_healing(tmp_path):
    """В-2: самолечения сборки идут ПОСЛЕ первой сверки и вправе
    создать второго носителя (здесь — внесение ответа авиакомпании о
    стыковке). Закон: пакет — это ВЫЛЕЧЕННЫЙ свод, и та же сверка стоит
    между лечением и байтами пакета. Прежде конфликтный пакет уезжал, а
    гейт производителя на нём краснел (ручка гейт не зовёт)."""
    files, err = _released(tmp_path, _sit_answer_doc(60), "run-9111")
    assert files is None, "пакет с двумя правдами (60 и 45) не выпускается"
    assert err.code == tp.PARAM_CARRIERS_DIVERGE
    assert err.items == ["R-SIT/min_turnaround"]
    assert "45" in str(err) and "60" in str(err)
    assert not list(tmp_path.rglob("*.zip"))
    # Обратный контроль: лечение согласно с ячейкой — выпуск и зелёный
    # гейт, и в пакете носители говорят одно.
    files, err = _released(tmp_path, _sit_answer_doc(45), "run-9112")
    assert files is not None, str(err)
    rule = json.loads(files["model.json"])["rules"][-1]
    assert m.carrier_divergences(rule) == []
    assert _gate_problems(tmp_path, files, "healed-ok.zip") == []


def test_b2_healers_do_not_add_a_second_carrier_next_to_the_cell(tmp_path):
    """В-2 (вторая половина закона): самолечение, поднимающее величину
    из ФОРМУЛИРОВКИ, не дописывает `params[]` тому имени, которое строка
    уже несёт ячейкой посадки, — иначе лечение само плодит вторую
    правду. Величина прозы не пропадает молча: она остаётся
    примечанием правила пакета."""
    doc = _doc_with(_landed(
        rid="R-SIT", family="connection_windows",
        cells={"min_turnaround": {"value": 45, "quote": "45 минут"}}))
    doc["rules"][-1]["statement"] = \
        "Минимальная стыковка между рейсами не менее 60 минут"
    healed = copy.deepcopy(doc)
    tp.heal_for_package(healed)
    assert healed["rules"][-1].get("params") in (None, []), \
        "лечение не должно добавлять конкурирующий параметр из прозы"
    files, err = _released(tmp_path, doc, "run-9113")
    assert files is not None, str(err)
    rule = json.loads(files["model.json"])["rules"][-1]
    assert [p["value"] for p in rule.get("params") or []] == [45], \
        "совместимая проекция берёт посадочные45, а не прозовые60"
    assert "60" in str(rule.get("note")), "величина прозы потеряна без следа"
    assert _gate_problems(tmp_path, files, "sit-guard.zip") == []
    # Обратный контроль: честное лечение (ячейки НЕТ) по-прежнему вносит
    # параметр и выпускается.
    honest = _ready_doc()
    honest["rules"].append({
        "id": "R-PROSE", "title": "Проза", "kind": "HARD", "zone": "PAIRING",
        "statement": "Минимальная стыковка между рейсами не менее 60 минут",
        "sources": [{"sid": "S1", "anchor": "п.1"}]})
    files, err = _released(tmp_path, honest, "run-9114")
    assert files is not None, str(err)
    rule = json.loads(files["model.json"])["rules"][-1]
    assert [p["value"] for p in rule.get("params") or []] == [60]
    assert _gate_problems(tmp_path, files, "prose-ok.zip") == []


# --- В-3: канон посадки не меняет величину --------------------------

@pytest.mark.parametrize("cell,legacy_value,legacy_unit", [
    ({"value": 720, "unit": "duration_minutes", "quote": "12:00"}, 12, "ч"),
    ({"value": 12, "unit": "ч", "quote": "12 часов"}, 720, "duration_minutes"),
    ({"value": 720, "quote": "12:00"}, 12, "ч"),
])
def test_b3_released_cell_keeps_the_value_it_agreed_on(tmp_path, cell,
                                                       legacy_value,
                                                       legacy_unit):
    """В-3: согласные носители (720 минут = 12 часов) выпускаются, и
    ячейка ВЫПУЩЕННОГО пакета обязана нести ту же величину. Прежде
    канон посадки переносил в ячейку единицу записи, не трогая значение:
    «12 ч» становилось «12 duration_minutes» = 12 минут."""
    doc = _doc_with(_landed(family="duty_limits", cells={
        "max_duty_duration": dict(cell)},
        legacy=[_legacy("max_duty_duration", legacy_value,
                        unit=legacy_unit, relation="max",
                        as_written="12 часов")]))
    files, err = _released(tmp_path, doc, "run-9121")
    assert files is not None, str(err)
    got = json.loads(files["model.json"])["rules"][-1][
        "template_fit"]["params"]["max_duty_duration"]
    value, bad = fp.canonical_value(got.get("value"), got.get("unit"),
                                    "duration_minutes")
    assert bad is None and value == 720, got
    assert _no_a05(_gate_problems(tmp_path, files, "canon.zip"))


# --- В-4: нет единицы = каноническая единица записи ------------------

def test_b4_a_carrier_without_a_unit_is_not_a_different_record(tmp_path):
    """В-4: правило «близнецов» требовало БУКВАЛЬНОГО совпадения слов
    единиц, а ячейки посадки единицу не несут вовсе (её ставит канон
    сборки). Одна и та же запись без единицы против записи с кодом
    каталога — согласие, а не «сверка невозможна»."""
    same = "GRT:1,PLY:2,SVG:1"
    r = _landed(rid="R-RES", family="reserve_floors", zone="ROSTERING",
                cells={"reserve_crews": {"value": same, "quote": same}},
                legacy=[_legacy("reserve_crews", same, unit="count",
                                relation="min", as_written=same)])
    assert m.reconcile_carriers(r) == []
    files, err = _released(tmp_path, _doc_with(r), "run-9131")
    assert files is not None, str(err)
    assert _no_a05(_gate_problems(tmp_path, files, "res-str.zip"))
    # Условие семьи с единицей `flag` ТОЛЬКО у записи — то же правило.
    cond = _landed(rid="R-EXT", family="duty_extension_penalty", kind="SOFT",
                   cells={"duty_extension_cap": {"value": 780,
                                                 "quote": "13:00"},
                          "long_duty_threshold": {"value": 720,
                                                  "quote": "12:00"},
                          "extension_consent": {"value": True,
                                                "quote": "согласие"}},
                   legacy=[_legacy("extension_consent", True, unit="flag",
                                   relation="yes", as_written="согласие")])
    cond["statement"] = "штраф за продление смены: вес 3"
    assert m.reconcile_carriers(cond) == []
    files, err = _released(tmp_path, _doc_with(cond), "run-9132")
    assert files is not None, str(err)
    assert _no_a05(_gate_problems(tmp_path, files, "flag-ok.zip"))
    # Обратный контроль: РАЗНЫЕ записи по-прежнему предмет барьера.
    other = copy.deepcopy(r)
    other["params"][0]["value"] = "GRT:1,PLY:2"
    assert len(m.reconcile_carriers(other)) == 1


# --- В-5: у единицы flag есть область значений ----------------------

def test_b5_flag_compares_booleans_and_only_booleans(tmp_path):
    """В-5 (решение ведущего сессии по §12.5): у единицы `flag` сверка
    строго логическая. Разные логические — расхождение словами «true
    против false», а не «сверка невозможна». `True` не равно 1 и не
    равно «да»: словаря слов у CDPM нет, и пока не появится `unit_words`
    каталога 0.6.0, такая пара — «сверка невозможна» (fail-closed)."""
    def rule(cell_value, legacy_value):
        r = _landed(rid="R-EXT", family="duty_extension_penalty",
                    kind="SOFT", cells={
                        "duty_extension_cap": {"value": 780,
                                               "quote": "13:00"},
                        "long_duty_threshold": {"value": 720,
                                                "quote": "12:00"},
                        "extension_consent": {"value": cell_value,
                                              "quote": "согласие"}},
                    legacy=[_legacy("extension_consent", legacy_value,
                                    unit="flag", relation="yes",
                                    as_written="согласие")])
        r["statement"] = "штраф за продление смены: вес 3"
        return r
    words = m.reconcile_carriers(rule(True, False))
    assert len(words) == 1 and "расходятся" in words[0]
    assert "посадка true против params[] false" in words[0], words
    for alien in (1, "да", "true"):
        words = m.reconcile_carriers(rule(True, alien))
        assert len(words) == 1 and "сверка невозможна" in words[0], alien
    assert m.reconcile_carriers(rule(True, True)) == []
    assert m.reconcile_carriers(rule(False, False)) == []
    files, err = _released(tmp_path, _doc_with(rule(True, False)),
                           "run-9141")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert "true" in str(err) and "false" in str(err)


# --- Н-1 / Н-2: адрес находки и противоречие внутри записи ----------

def test_n1_one_finding_per_defect_and_the_qualifier_is_named():
    """Н-1: две БУКВАЛЬНО одинаковые записи `params[]` — одна беда, а не
    две (перечень `items` рубежа не двоится); при НЕСКОЛЬКИХ записях
    одного имени слова называют уточнитель записи — иначе адрес находки
    неразличим."""
    twice = _landed(legacy=[_legacy("max_pairing_days", 30,
                                    as_written="не более 30 суток"),
                            _legacy("max_pairing_days", 30,
                                    as_written="не более 30 суток")])
    assert [d["param"] for d in m.carrier_divergences(twice)] \
        == ["max_pairing_days"]
    areas = _landed(family="connection_windows", cells={
        "min_turnaround": {"value": 45, "quote": "45 минут"}},
        legacy=[_legacy("min_turnaround", 45, unit="duration_minutes",
                        relation="min", qualifier="база",
                        as_written="45 мин"),
                _legacy("min_turnaround", 60, unit="duration_minutes",
                        relation="min", qualifier="вне базы",
                        as_written="60 мин")])
    words = m.reconcile_carriers(areas)
    assert len(words) == 1 and "вне базы" in words[0], words


def test_n2_a_record_that_contradicts_itself_is_not_read_silently(tmp_path):
    """Н-2: запись `value: "50 мин"` при `unit: "ч"` противоречит себе —
    молчаливое «50» из неё не выводится: сверка невозможна. Согласная
    единица ту же запись читает по-прежнему."""
    def rule(legacy_value, legacy_unit):
        return _landed(family="rest_floors", zone="ROSTERING", cells={
            "min_rest_home": {"value": 50, "quote": "50 мин"},
            "min_rest_outstation": {"value": 600, "quote": "10:00"}},
            legacy=[_legacy("min_rest_home", legacy_value, unit=legacy_unit,
                            relation="min", as_written="50 мин")])
    words = m.reconcile_carriers(rule("50 мин", "ч"))
    assert len(words) == 1 and "сверка невозможна" in words[0]
    assert "противоречие внутри записи" in words[0], words
    assert m.reconcile_carriers(rule("50 мин", "duration_minutes")) == []
    assert m.reconcile_carriers(rule(50, "duration_minutes")) == []
    files, err = _released(tmp_path, _doc_with(rule("50 мин", "ч")),
                           "run-9151")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE


# --------------------------------------------------------------------
# Открытый вопрос 2 ранней части (Б/К): канон не переписывает ЯВНОЕ
# поле ячейки — молчаливой смены смысла после пройденной сверки нет
# --------------------------------------------------------------------

def _healed_relation_doc(cell_relation, legacy_relation="max"):
    """Свод, где самолечение сборки (`restore_param_relations`) чинит
    отношение ЗАПИСИ `params[]` по природе имени и формулировке: имя
    `min_rest_home`, формулировка «не менее», запись говорит «max» —
    после лечения запись говорит «min»."""
    cell = {"value": 720, "quote": "12:00"}
    if cell_relation is not None:
        cell["relation"] = cell_relation
    r = _landed(rid="R-REL", family="rest_floors", zone="ROSTERING",
                cells={"min_rest_home": cell,
                       "min_rest_outstation": {"value": 600,
                                               "quote": "10:00"}},
                legacy=[_legacy("min_rest_home", 720,
                                unit="duration_minutes",
                                relation=legacy_relation,
                                as_written="12 часов")])
    r["statement"] = "Отдых дома не менее 12 часов"
    return _doc_with(r)


def test_canon_does_not_silently_replace_an_explicit_cell_relation(tmp_path):
    """Б/К (открытый вопрос 2): ячейка ЯВНО говорит «max», а лечение
    сборки чинит запись `params[]` на «min». Прежде канон посадки
    переносил «min» в ячейку — смысл посадки менялся ПОСЛЕ пройденной
    сверки и молча. Закон: явное поле ячейки канон не трогает, а
    разошедшиеся носители судит ПОВТОРНАЯ сверка тем же кодом."""
    doc = _healed_relation_doc("max")
    # Исходные носители согласны — первая сверка пропускает.
    assert m.reconcile_carriers(doc["rules"][-1]) == []
    files, err = _released(tmp_path, doc, "run-9161")
    assert files is None, "пакет с двумя правдами об отношении выпущен"
    assert err.code == tp.PARAM_CARRIERS_DIVERGE
    assert err.items == ["R-REL/min_rest_home"]
    assert "отношение" in str(err) and "min" in str(err) \
        and "max" in str(err)
    assert not list(tmp_path.rglob("*.zip"))
    # Та же проверка на самой функции канона: ячейку не переписали.
    rule = copy.deepcopy(doc["rules"][-1])
    rule["params"][0]["relation"] = "min"           # как после лечения
    tp.canon_fit_params(rule)
    assert rule["template_fit"]["params"]["min_rest_home"]["relation"] \
        == "max"


def test_a_cell_without_a_relation_still_inherits_it_from_the_record(
        tmp_path):
    """Обратный контроль того же закона: своей правды у ПУСТОГО поля
    нет — отношение записи ячейка наследует по-прежнему, и пакет
    выпускается (канон не превратился в «ничего не делать»)."""
    doc = _healed_relation_doc(None, legacy_relation="min")
    files, err = _released(tmp_path, doc, "run-9162")
    assert files is not None, str(err)
    cell = json.loads(files["model.json"])["rules"][-1]["template_fit"][
        "params"]["min_rest_home"]
    assert cell["relation"] == "min"
    assert cell["value"] == 720                      # величина не менялась
    assert _gate_problems(tmp_path, files, "rel-ok.zip") == []


def test_canon_does_not_silently_replace_an_explicit_cell_condition(
        tmp_path):
    """Тот же закон об УСЛОВИИ: ячейка явно назвала свою область, запись
    `params[]` — другую. Канон область ячейки не переписывает, спор
    называет сверка носителей; ячейка БЕЗ условия область записи
    наследует."""
    def rule(cell_condition):
        cell = {"value": 720, "quote": "12:00"}
        if cell_condition is not None:
            cell["condition"] = cell_condition
        return _landed(rid="R-COND", family="rest_floors", zone="ROSTERING",
                       cells={"min_rest_home": cell,
                              "min_rest_outstation": {"value": 600,
                                                      "quote": "10:00"}},
                       legacy=[_legacy("min_rest_home", 720,
                                       unit="duration_minutes",
                                       relation="min", qualifier="вне базы",
                                       as_written="12:00")])
    r = rule("в базе")
    tp.canon_fit_params(r)
    assert r["template_fit"]["params"]["min_rest_home"]["condition"] \
        == "в базе"
    files, err = _released(tmp_path, _doc_with(rule("в базе")), "run-9163")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE
    assert "условие" in str(err)
    # Ячейка без условия — наследование живо, выпуск идёт.
    empty = rule(None)
    tp.canon_fit_params(empty)
    assert empty["template_fit"]["params"]["min_rest_home"]["condition"] \
        == "вне базы"
    files, err = _released(tmp_path, _doc_with(rule(None)), "run-9164")
    assert files is not None, str(err)


# --- Н-3 (класс X-04): дубль ключа в БАЙТАХ — подмена значения -------

def _dup_bytes(raw):
    """Байты свода, где у ячейки посадки `value` объявлено ДВАЖДЫ: 3,
    затем 30. Правка строковая — именно байты, а не разобранный
    документ (иначе дубля в них и не будет); годится и для сжатой
    записи прогона, и для отступов пакета."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    # Export now also derives params[].value=3. Mutate the landing cell
    # specifically, so a matching scalar elsewhere cannot absorb the probe.
    changed, count = re.subn(
        r'("max_pairing_days"\s*:\s*\{\s*)"value"\s*:\s*3(?=\s*[,}])',
        r'\1"value": 3, "value": 30', text)
    assert count == 1, "ожидалась ровно одна исходная ячейка посадки"
    return changed.encode("utf-8")


def test_n3_duplicate_key_in_the_bytes_stops_the_release(tmp_path):
    """Н-3: `json.loads` читает `{"value": 3, "value": 30}` как 30 —
    молча, последним объявлением. Пакет уезжал с тридцаткой, которой в
    своде не видел никто: ни экран, ни сверка носителей (первое
    значение исчезало ДО неё). Закон: дубль ключа — отказ формы
    словами, называющими ключ и его путь."""
    doc = _doc_with(_landed())
    run = _run_dir(tmp_path, doc, "run-9171")
    path = run / "extract" / "model.json"
    path.write_bytes(_dup_bytes(path.read_bytes()))
    # Контрольный факт: библиотека молча берёт последнее.
    assert json.loads(path.read_bytes().decode("utf-8"))["rules"][-1][
        "template_fit"]["params"]["max_pairing_days"]["value"] == 30
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert "дубль ключа" in str(e.value)
    assert "max_pairing_days" in str(e.value) and "value" in str(e.value)
    assert e.value.code is None            # отказ формы (§1.4: code null)
    assert not list(tmp_path.rglob("*.zip"))


def test_n3_duplicate_key_in_the_package_reddens_the_gate(tmp_path):
    """Тот же дубль в БАЙТАХ пакета (манифест пересчитан — порчи нет):
    гейт производителя краснит теми же словами, а не читает последнее
    объявление молча."""
    files, err = _released(tmp_path, _doc_with(_landed()), "run-9172")
    assert files is not None, str(err)
    bad = dict(files)
    bad["model.json"] = _dup_bytes(files["model.json"])
    changed_rule = json.loads(bad["model.json"])["rules"][-1]
    assert changed_rule["template_fit"]["params"]["max_pairing_days"]["value"] == 30
    assert next(p["value"] for p in changed_rule["params"] if p["name"] == "max_pairing_days") == 3
    manifest = json.loads(bad["manifest.json"])
    for row in manifest.get("files", []):
        if row.get("name") == "model.json":
            row["sha256"] = hashlib.sha256(bad["model.json"]).hexdigest()
            row["bytes"] = len(bad["model.json"])
    bad["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    problems = _gate_problems(tmp_path, bad, "dup.zip")
    assert any("дубль ключа" in p for p in problems), problems
    assert any("max_pairing_days" in p for p in problems), problems
    # Честные байты того же пакета гейт по-прежнему пропускает.
    assert _gate_problems(tmp_path, files, "honest.zip") == []


def _kak_tekst(soderzhimoe) -> str:
    """Содержимое файла пакета текстом: строитель отдаёт байты либо
    строку, и проба не должна зависеть от того, чем именно."""
    return (soderzhimoe.decode("utf-8")
            if isinstance(soderzhimoe, bytes) else str(soderzhimoe))


def _zamorozit_chasy(monkeypatch, metka: dict):
    """Часы свидетельства SMT — УПРАВЛЯЕМЫЕ, и только в пробе.

    `smt_verify._now` (`:346`) пишет `checked_at` (`:435`) с точностью до
    СЕКУНДЫ. Первая сборка пакета идёт ≈1,2 с, вторая ≈0,1 с, и окно
    между ними случайно пересекало границу секунды — проба краснела
    примерно в каждом десятом прогоне, притом краснело ровно
    утверждение о ДЕТЕРМИНИЗМЕ.

    Лечение выбрано по условиям аудитора: НЕ исключать свидетельство из
    сравнения и НЕ подделывать время в продукте (то и другое запрещено
    прямо), а управлять часами в пробе. Тогда сравнение остаётся
    ПОБАЙТНЫМ по ВСЕМ файлам, без единого исключения."""
    from cdpm import smt_verify as sv
    monkeypatch.setattr(sv, "_now", lambda: metka["v"])


def test_n3_honest_bytes_are_read_exactly_as_before(tmp_path, monkeypatch):
    """Обратный контроль: строгое чтение НИЧЕГО не меняет честным
    байтам — результат равен `json.loads`, а две сборки ОДНОГО СВОДА
    байт в байт совпадают (детерминизм пакета не тронут).

    Прежде докстрока говорила «одного прогона», а код строил ДВА разных
    (`run-9173` и `run-9174`) — поправлено словом, а не кодом: предмет
    сравнения именно свод, имя прогона живёт в `manifest.json`.

    ДОГОВОР О ЧАСАХ — первая половина: часы заморожены, и различий нет
    НИ ОДНОГО, без исключений. Вторую половину держат две соседние
    пробы: управляемый переход через секунду и настоящая порча
    свидетельства."""
    _zamorozit_chasy(monkeypatch, {"v": "2026-09-13T05:00:00+02:00"})
    doc = _doc_with(_landed())
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    assert m.loads_strict(raw) == json.loads(raw.decode("utf-8"))
    first, err = _released(tmp_path, doc, "run-9173")
    assert first is not None, str(err)
    second, err = _released(tmp_path, doc, "run-9174")
    assert second is not None, str(err)
    assert first.keys() == second.keys()
    differ = [n for n in first
              if first[n] != second[n] and n != "manifest.json"]
    assert differ == [], differ


def test_n3b_clock_step_moves_only_checked_at(tmp_path, monkeypatch):
    """ДОГОВОР О ЧАСАХ — вторая половина: управляемый переход через
    секунду. Между сборками часы уходят ровно на секунду вперёд, и
    различается РОВНО одно поле — `checked_at` в `smt-report.json`.

    Это и есть договор, названный словами: единственное поле пакета,
    которому позволено различаться между двумя сборками. Прежде это
    знание нигде не было записано, и потому случайное пересечение
    границы секунды выглядело поломкой детерминизма."""
    метка = {"v": "2026-09-13T05:00:00+02:00"}
    _zamorozit_chasy(monkeypatch, метка)
    doc = _doc_with(_landed())
    first, err = _released(tmp_path, doc, "run-9173")
    assert first is not None, str(err)
    метка["v"] = "2026-09-13T05:00:01+02:00"
    second, err = _released(tmp_path, doc, "run-9174")
    assert second is not None, str(err)

    differ = [n for n in first
              if first[n] != second[n] and n != "manifest.json"]
    assert differ == ["smt-report.json"], differ

    a = json.loads(_kak_tekst(first["smt-report.json"]))
    b = json.loads(_kak_tekst(second["smt-report.json"]))
    assert a["checked_at"] != b["checked_at"], (a["checked_at"],)
    assert {k: v for k, v in a.items() if k != "checked_at"} ==            {k: v for k, v in b.items() if k != "checked_at"}


def test_n3c_the_comparison_is_not_blind_to_the_smt_report(tmp_path,
                                                           monkeypatch):
    """ДОГОВОР О ЧАСАХ — отрицательный контроль: сравнение НЕ СЛЕПО к
    свидетельству SMT.

    Без него договор превратился бы в разрешение не смотреть: «часы
    заморожены, различий нет» ничего не стоило бы, если бы сравнение и
    настоящей порчи не замечало. Портим СОДЕРЖАТЕЛЬНОЕ поле (не
    `checked_at`) — и требуем, чтобы его назвали."""
    _zamorozit_chasy(monkeypatch, {"v": "2026-09-13T05:00:00+02:00"})
    doc = _doc_with(_landed())
    first, err = _released(tmp_path, doc, "run-9173")
    assert first is not None, str(err)
    second, err = _released(tmp_path, doc, "run-9174")
    assert second is not None, str(err)

    порченое = dict(second)
    тело = json.loads(_kak_tekst(second["smt-report.json"]))
    тело["translator"] = "ЧУЖОЙ-ПЕРЕВОДЧИК"
    новый = json.dumps(тело, ensure_ascii=False)
    порченое["smt-report.json"] = (
        новый.encode("utf-8")
        if isinstance(second["smt-report.json"], bytes) else новый)

    differ = [n for n in first
              if first[n] != порченое[n] and n != "manifest.json"]
    assert differ == ["smt-report.json"], differ


# --- Н-4: расхождение носителей — основание ГОТОВНОСТИ ---------------

def test_n4_divergence_holds_readiness_below_v_with_the_same_words():
    """Н-4: рубеж выпуска и гейт отказывали, а экран показывал «в» —
    владелец узнавал о двух правдах, только нажав «Выпустить». Закон:
    расхождение носителей — такое же основание готовности, как барьеры
    K01/K06, и слова у него те же (`carrier_problems`)."""
    doc = _doc_with(_landed(legacy=[_legacy("max_pairing_days", 30)]))
    words = m.carrier_problems(doc)[0]["words"]
    got = m.readiness(doc)
    assert got["state"] != "в", got["label"]
    assert not got["pairing_ready"]            # носитель зоны PAIRING
    assert words in got["pairing_open"]
    assert words in got["label"]
    ground = m.carrier_grounds(doc)[0]
    assert ground["code"] == m.PARAM_CARRIERS_DIVERGE
    assert ground["rules"] == ["R-L"] and ground["hard"] is True
    # Матрица корректности снимает режимы затронутой задачи теми же
    # словами — как у барьеров.
    cells = m.readiness_matrix(doc)["pairing"]
    assert not any(cells[mode]["ready"] for mode in m.TASK_MODES)
    assert words in cells["feasibility-hard"]["reasons"]
    # Обратный контроль: согласные носители — готовность «в».
    ok = _doc_with(_landed(legacy=[_legacy("max_pairing_days", 3)]))
    assert m.carrier_grounds(ok) == [] and m.readiness(ok)["state"] == "в"


def test_n4_a_soft_carrier_holds_only_the_penalty_modes():
    """Н-4 (граница): носитель МЯГКОЙ строки держит режимы со штрафами,
    а выполнимость по жёстким — нет (как пробел покрытия и барьеры)."""
    doc = _doc_with(_landed(rid="R-SOFT", kind="SOFT",
                            legacy=[_legacy("max_pairing_days", 30)]))
    assert m.carrier_grounds(doc)[0]["hard"] is False
    cells = m.readiness_matrix(doc)["pairing"]
    assert cells["feasibility-hard"]["ready"]
    assert not cells["feasibility-soft"]["ready"]


def test_n4_fit_signature_does_not_crash_on_a_list_of_params():
    """Н-4: `fit_signature` звала `.items()` у `params` посадки и падала
    AttributeError, когда форма нарушена списком, — падал весь прогон
    вместо отказа словами. Закон тот же, что у `rule_param_cells`:
    не-объект = ячеек нет, а форму судит проверка посадки."""
    broken = _landed(cells=[{"max_pairing_days": 3}])
    assert broken["template_fit"]["params"] == [{"max_pairing_days": 3}]
    sig = m.fit_signature(broken)
    assert sig is not None and sig[0] == "duty_limits"
    assert m.rule_param_cells(broken) == {}
    # Суд дублей на таком своде работает, а не валится.
    doc = _doc_with(broken)
    doc["rules"].append(copy.deepcopy(broken))
    doc["rules"][-1]["id"] = "R-L2"
    assert m.find_duplicate_rule(doc, doc["rules"][-1]) is not None
    # Форму называет проверка посадки — молчания нет.
    assert any("params" in p or "посадк" in p.lower()
               for p in m.validate_model(doc)), m.validate_model(doc)


# --- Единственная дверь к значению: конечность числа -----------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"),
                                 float("-inf")])
def test_non_finite_numbers_are_not_a_value(bad):
    """Питоновский разбор JSON принимает `NaN`/`Infinity` молча, и
    прежде такое «число» доезжало до сравнения: NaN не равен даже себе,
    и два носителя расходились бы тишиной, а исполнителю ехала бы
    бессмыслица. Закон в ОДНОЙ двери к значению (`canonical_value`):
    не-конечное число — не значение, слова называют причину."""
    value, trouble = fp.canonical_value(bad, "count", "count")
    assert value is None and "не конечное число" in trouble
    # То же и у носителя, объявившего свою единицу.
    value, trouble = fp.canonical_value(bad, "ч", "duration_minutes")
    assert value is None and trouble
    # Обратный контроль: конечное число читается как прежде.
    assert fp.canonical_value(3, "count", "count") == (3, None)


def test_non_finite_carrier_is_a_problem_not_a_silent_match(tmp_path):
    """Тот же закон на своде: NaN в ячейке против числа в `params[]` —
    «сверка невозможна» словами, а не молчаливое согласие; выпуск
    останавливается."""
    r = _landed(cells={"max_pairing_days": {"value": float("nan"),
                                            "quote": "3 суток"}},
                legacy=[_legacy("max_pairing_days", 3)])
    words = m.reconcile_carriers(r)
    assert len(words) == 1 and "сверка невозможна" in words[0]
    assert "не конечное число" in words[0], words
    files, err = _released(tmp_path, _doc_with(r), "run-9181")
    assert files is None and err.code == tp.PARAM_CARRIERS_DIVERGE


def test_a_legal_code_list_is_not_broken_by_a_structural_guard():
    """Местный сторож K01 бракова́л ЛЮБОЙ перечень — в том числе
    законный перечень обозначений у единицы `code_list` (`fleet_types`
    семьи `fleet_exclusions`). Дверь A05 судит по форме КАНОНИЧЕСКОЙ
    единицы: перечень строк — значение, перечень чисел — нет."""
    value, trouble = fp.canonical_value(["B737", "A320"], "", "code_list")
    assert trouble is None and value
    value, trouble = fp.canonical_value([3], "", "code_list")
    assert value is None and trouble
    value, trouble = fp.canonical_value([3], "count", "count")
    assert value is None and "структурная запись" in trouble
