# -*- coding: utf-8 -*-
"""Сравнение порогов пакета с порогами решателя (этап В, пункт «б»).

Проверяется суть, а не форма отчёта: сравнение обязано различать ТРИ
исхода. «Совпадает» и «расходится» описывают сопоставимые величины;
третий — НЕСРАВНИМО — существует потому, что у решателя бывает ОДНА
ручка против нескольких разных требований пакета, и её совпадение с
одним из них не согласие, а промах мимо остальных. Назвать такое
зелёным значило бы завести проверку, которая ничего не проверяет.
"""

from pathlib import Path

import pytest

from webapp import task_package as tp
from webapp import threshold_gate as tg

REPO = Path(__file__).resolve().parents[1]
ETALON = (REPO.parent / "handoff" / "2026-08-22-MOST-A" / "etalon-nano"
          / "run-0027-task-package.zip")

etalon_needed = pytest.mark.skipif(
    not ETALON.exists(), reason="эталонный пакет не найден: %s" % ETALON)

#: Соответствие «параметр -> показание» — как его объявляет решатель в
#: отчёте (thresholds.paramToThreshold). Своей копии у гейта нет.
MAPPING = {
    "max_duty_duration": "max_duty_span_min",
    "min_turnaround": ["min_sit_home_min", "min_sit_outstation_min"],
    "min_rest_home": "min_rest_home_min",
    "min_rest_outstation": "min_rest_outstation_min",
    "max_monthly_block": "max_monthly_block_min",
    "max_landings_per_duty": "max_landings_per_duty",
}
QUALIFIED = {"min_turnaround|база": "min_sit_home_min",
             "min_turnaround|в базе": "min_sit_home_min",
             "min_turnaround|вне базы": "min_sit_outstation_min",
             "min_rest_home|база": "min_rest_home_min",
             # Подстановочный ключ решателя: уточнитель, которого ручка не
             # различает, — ко всей ручке (только как ужесточение).
             "min_turnaround|*": ["min_sit_home_min", "min_sit_outstation_min"],
             "max_duty_duration|*": "max_duty_span_min"}
#: Литералы-выключатели — как их объявляет решатель (thresholds.offLiterals).
OFF = {"max_landings_per_duty": 0}

#: Показания профиля по умолчанию — те, что решатель отдаёт в отчёте.
DEFAULT_READINGS = {
    "min_sit_home_min": 60, "min_sit_outstation_min": 60,
    "min_rest_home_min": 480, "min_rest_outstation_min": 480,
    "max_duty_span_min": 840, "max_monthly_block_min": 6000,
    "max_landings_per_duty": 0, "day_break_threshold_min": 360,
    "max_weekly_block_min": 3600, "max_consecutive_duty_days": 6,
}


def _pkg(rules):
    return {"model": {"rules": rules}}


def test_совпадение_называется_совпадением():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 840,
         "unit": "duration_minutes", "relation": "max"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)
    assert [x["verdict"] for x in v] == [tg.MATCH]


def test_расхождение_называется_числом_и_стороной():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "relation": "max"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)[0]
    assert v["verdict"] == tg.DIVERGE
    assert v["solver_value"] == 840 and v["package_values"] == [720]
    # Сторона названа: предел решателя ШИРЕ требования, а не просто «иной».
    assert "ШИРЕ" in v["note"] and "120" in v["note"]


def test_отдых_дома_и_вне_базы_сравниваются_порознь():
    """Ревизия 3: у решателя ДВЕ ручки отдыха, сведения больше нет."""
    pkg = _pkg([
        {"id": "R1", "kind": "HARD", "params": [
            {"name": "min_rest_home", "value": 720,
             "unit": "duration_minutes", "relation": "min"}]},
        {"id": "R2", "kind": "HARD", "params": [
            {"name": "min_rest_outstation", "value": 480,
             "unit": "duration_minutes", "relation": "min"}]},
    ])
    v = {x["reading"]: x for x in tg.compare(
        pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)}
    assert v["min_rest_outstation_min"]["verdict"] == tg.MATCH      # 480 = 480
    assert v["min_rest_home_min"]["verdict"] == tg.DIVERGE          # 720 ≠ 480


def test_одна_ручка_против_двух_требований_несравнима():
    """Два разных требования на ОДНУ ручку — по-прежнему несравнимо."""
    pkg = _pkg([
        {"id": "R1", "kind": "HARD", "params": [
            {"name": "max_duty_duration", "value": 720,
             "unit": "duration_minutes", "relation": "max"}]},
        {"id": "R2", "kind": "HARD", "params": [
            {"name": "max_duty_duration", "value": 690,
             "unit": "duration_minutes", "relation": "max"}]},
    ])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)[0]
    assert v["verdict"] == tg.INCOMPARABLE and "ОДНА величина" in v["note"]


def test_стыковка_с_уточнителем_идёт_в_свою_ручку():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 45, "unit": "duration_minutes",
         "relation": "min", "qualifier": "база"}]}])
    readings = dict(DEFAULT_READINGS, min_sit_home_min=45)
    v = tg.compare(pkg, readings, profile="DEFAULT",
                   mapping=MAPPING, qualified=QUALIFIED)
    assert len(v) == 1 and v[0]["reading"] == "min_sit_home_min"
    assert v[0]["verdict"] == tg.MATCH


def test_стыковка_без_уточнителя_сверяется_с_обеими_ручками():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 50, "unit": "duration_minutes",
         "relation": "min"}]}])
    readings = dict(DEFAULT_READINGS, min_sit_home_min=50, min_sit_outstation_min=60)
    v = {x["reading"]: x for x in tg.compare(
        pkg, readings, profile="DEFAULT", mapping=MAPPING, qualified=QUALIFIED)}
    assert v["min_sit_home_min"]["verdict"] == tg.MATCH
    assert v["min_sit_outstation_min"]["verdict"] == tg.DIVERGE


def test_уточнитель_сопоставляется_точно_а_не_по_префиксу():
    """Ревизия 3 брала startswith: «база PLY» уходила в домашнюю ручку."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 45, "unit": "duration_minutes",
         "relation": "min", "qualifier": "база PLY"}]}])
    readings = dict(DEFAULT_READINGS, min_sit_home_min=45)
    # Без подстановочного ключа цели нет — и это названо, а не «имя неизвестно».
    exact_only = {k: v for k, v in QUALIFIED.items() if not k.endswith("|*")}
    v = tg.compare(pkg, readings, profile="DEFAULT",
                   mapping=MAPPING, qualified=exact_only)
    assert [x["verdict"] for x in v] == [tg.INCOMPARABLE]
    assert "не нашёл цели" in v[0]["note"] and "база PLY" in v[0]["note"]
    # С подстановочным ключом — ко ВСЕЙ ручке, обе области, и домашние 45
    # не «совпадение»: решатель такое ослабление не применил бы.
    v = {x["reading"]: x for x in tg.compare(
        pkg, readings, profile="DEFAULT", mapping=MAPPING, qualified=QUALIFIED)}
    assert set(v) == {"min_sit_home_min", "min_sit_outstation_min"}
    assert v["min_sit_outstation_min"]["verdict"] == tg.DIVERGE


def test_пробелы_и_регистр_уточнителя_нормализуются():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 45, "unit": "duration_minutes",
         "relation": "min", "qualifier": "  В базе "}]}])
    readings = dict(DEFAULT_READINGS, min_sit_home_min=45)
    v = tg.compare(pkg, readings, profile="DEFAULT",
                   mapping=MAPPING, qualified=QUALIFIED)
    assert len(v) == 1 and v[0]["reading"] == "min_sit_home_min"
    assert v[0]["verdict"] == tg.MATCH


def test_без_таблицы_уточнителей_цель_не_домысливается():
    """Нет записи — нет цели; «ко всей сети» гейт не подставляет."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 45, "unit": "duration_minutes",
         "relation": "min", "qualifier": "база"}]}])
    readings = dict(DEFAULT_READINGS, min_sit_home_min=45)
    v = tg.compare(pkg, readings, profile="DEFAULT", mapping=MAPPING, qualified=None)
    assert [x["verdict"] for x in v] == [tg.INCOMPARABLE]
    assert "не нашёл цели" in v[0]["note"]


def test_выключенное_правило_несравнимо_а_не_строже():
    """Показание 0 у посадок — «правило не считается», не «ноль посадок»."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_landings_per_duty", "value": 6, "unit": "count",
         "relation": "max"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT",
                   mapping=MAPPING, off_literals=OFF)
    assert [x["verdict"] for x in v] == [tg.INCOMPARABLE]
    assert "ВЫКЛЮЧЕНО" in v[0]["note"] and "СТРОЖЕ" not in v[0]["note"]
    # Включённое правило сравнивается арифметикой как любое другое.
    v = tg.compare(pkg, dict(DEFAULT_READINGS, max_landings_per_duty=6),
                   profile="DEFAULT", mapping=MAPPING, off_literals=OFF)
    assert [x["verdict"] for x in v] == [tg.MATCH]


def test_счётчик_рассмотренного_сходится_с_пакетом_а_не_с_вердиктами():
    """Стыковка без уточнителя даёт ДВА вердикта — но запись одна."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 60, "unit": "duration_minutes",
         "relation": "min"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT",
                   mapping=MAPPING, qualified=QUALIFIED)
    assert len(v) == 2
    text = tg.report(v, considered=len(tg.collect_params(pkg)))
    assert "рассмотрено имён: 1, записей параметров: 1;" in text
    # И без подсказки счёт идёт по записям (правило, имя), а не по вердиктам.
    assert "рассмотрено имён: 1, записей параметров: 1;" in tg.report(v)


def test_нет_встречной_величины_названо_причиной():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "duty_rig", "value": 2, "unit": "hours_per_credit_hour",
         "relation": "min", "qualifier": "PLY"},
        {"name": "duty_rig", "value": 1, "unit": "hours_per_credit_hour",
         "relation": "min", "qualifier": "SVG"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)[0]
    assert v["verdict"] == tg.INCOMPARABLE
    assert "кредитные часы" in v["note"]
    # Уточнители не теряются: показать один из двух значило бы скрыть,
    # что записей несколько.
    assert v["qualifiers"] == ["PLY", "SVG"]


def test_норма_резерва_по_базам_сверяется_с_ручкой_каждой_базы():
    """Строка 8 свода: у решателя появился исполнитель — «несравнимых» нет."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "reserve_crews", "value": 2, "unit": "count",
         "relation": "min", "qualifier": "PLY"},
        {"name": "reserve_crews", "value": 1, "unit": "count",
         "relation": "min", "qualifier": "SVG"}]},
        {"id": "R2", "kind": "HARD", "params": [
        {"name": "reserve_crews", "value": 2, "unit": "count",
         "relation": "eq", "qualifier": "PLY"}]}])
    mapping = dict(MAPPING, reserve_crews="reserve_crews_min")
    qualified = dict(QUALIFIED, **{"reserve_crews|PLY": "reserve_crews_min[PLY]",
                                   "reserve_crews|SVG": "reserve_crews_min[SVG]"})
    readings = dict(DEFAULT_READINGS, **{"reserve_crews_min[PLY]": 2, "reserve_crews_min[SVG]": 1})
    applied = [{"threshold": "reserve_crews_min[PLY]", "value": 2, "rules": ["R1", "R2"],
                "fromParams": ["reserve_crews"],
                "declarations": ["РАВЕНСТВО: правила R2 требуют «ровно 2»; исполнено как «не менее»"]},
               {"threshold": "reserve_crews_min[SVG]", "value": 1, "rules": ["R1"],
                "fromParams": ["reserve_crews"], "declarations": []}]
    v = {x["reading"]: x for x in tg.compare(
        pkg, readings, profile="DEFAULT", mapping=mapping, qualified=qualified,
        applied=applied, off_literals={"reserve_crews_min": 0, "reserve_crews_min[PLY]": 0,
                                       "reserve_crews_min[SVG]": 0})}
    assert set(v) == {"reserve_crews_min[PLY]", "reserve_crews_min[SVG]"}
    assert v["reserve_crews_min[SVG]"]["verdict"] == tg.MATCH
    # «Ровно» исполнено нижней границей — совпадение с оговоркой решателя.
    assert v["reserve_crews_min[PLY]"]["verdict"] == tg.MATCH
    assert v["reserve_crews_min[PLY]"]["note"].startswith("РАВЕНСТВО")
    # Показание-выключатель у базы — «выключено», не «строже».
    v2 = tg.compare(pkg, dict(readings, **{"reserve_crews_min[SVG]": 0}), profile="DEFAULT",
                    mapping=mapping, qualified=qualified, applied=applied,
                    off_literals={"reserve_crews_min[SVG]": 0})
    assert any(x["reading"] == "reserve_crews_min[SVG]" and "ВЫКЛЮЧЕНО" in x["note"]
               for x in v2)


def test_неприменённая_запись_с_уточнителем_не_утаскивает_законные():
    """Одно правило — три базы; четвёртая запись без кода отвергнута решателем."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "reserve_crews", "value": 2, "unit": "count",
         "relation": "min", "qualifier": "PLY"},
        {"name": "reserve_crews", "value": 1, "unit": "count",
         "relation": "min", "qualifier": "база"}]}])
    mapping = dict(MAPPING, reserve_crews="reserve_crews_min")
    qualified = dict(QUALIFIED, **{"reserve_crews|PLY": "reserve_crews_min[PLY]"})
    ignored = [{"param": "reserve_crews", "rules": ["R1"], "qualifiers": ["база"],
                "reason": "уточнитель «база» — не код базы; правило НЕ исполняется"}]
    v = tg.compare(pkg, dict(DEFAULT_READINGS, **{"reserve_crews_min[PLY]": 2}),
                   profile="DEFAULT", mapping=mapping, qualified=qualified, ignored=ignored)
    kinds = {(x["reading"], x["verdict"]) for x in v}
    assert ("reserve_crews_min[PLY]", tg.MATCH) in kinds
    assert any(x["verdict"] == tg.INCOMPARABLE and x["qualifiers"] == ["база"] for x in v)


def test_конфликт_видов_одного_имени_называется_а_не_молчит():
    """Класс с «Лайта»: 720 жёстким правилом, 750 — правилом вида DATA.

    Решатель применяет жёсткое, сертификатор проверяет и второе — ростер
    получает нарушения при зелёном сравнении порогов. Сравнение таких
    правил невозможно, молчание — запрещено.
    """
    pkg = _pkg([
        {"id": "R-HARD", "kind": "HARD", "params": [
            {"name": "min_rest_home", "value": 720, "unit": "duration_minutes",
             "relation": "min"}]},
        {"id": "R-DATA", "kind": "DATA", "params": [
            {"name": "min_rest_home", "value": 750, "unit": "duration_minutes",
             "relation": "min"}]},
    ])
    conflicts = tg.kind_conflicts(pkg)
    assert len(conflicts) == 1
    assert conflicts[0]["values"] == [720, 750]
    assert conflicts[0]["by_kind"] == {"DATA": ["R-DATA"], "HARD": ["R-HARD"]}
    v = tg.compare(pkg, dict(DEFAULT_READINGS, min_rest_home_min=720),
                   profile="DEFAULT", mapping=MAPPING)
    kinds = {(x["verdict"], "КОНФЛИКТ ВИДОВ" in x["note"]) for x in v}
    assert (tg.MATCH, False) in kinds           # жёсткое правило сверено
    assert (tg.INCOMPARABLE, True) in kinds     # и конфликт назван
    # Контроль: одинаковые числа в разных видах конфликтом не считаются,
    # как и разные числа ВНУТРИ одного вида (это сведение, а не конфликт).
    same = _pkg([
        {"id": "R1", "kind": "HARD", "params": [
            {"name": "min_rest_home", "value": 720, "unit": "duration_minutes", "relation": "min"}]},
        {"id": "R2", "kind": "DATA", "params": [
            {"name": "min_rest_home", "value": 720, "unit": "duration_minutes", "relation": "min"}]},
    ])
    assert tg.kind_conflicts(same) == []
    one_kind = _pkg([
        {"id": "R1", "kind": "HARD", "params": [
            {"name": "max_duty_duration", "value": 720, "unit": "duration_minutes", "relation": "max"}]},
        {"id": "R2", "kind": "HARD", "params": [
            {"name": "max_duty_duration", "value": 690, "unit": "duration_minutes", "relation": "max"}]},
    ])
    assert tg.kind_conflicts(one_kind) == []
    # Отменённое заказчиком (OUT) требованием не является.
    with_out = _pkg([
        {"id": "R1", "kind": "HARD", "params": [
            {"name": "min_rest_home", "value": 720, "unit": "duration_minutes", "relation": "min"}]},
        {"id": "R2", "kind": "OUT", "params": [
            {"name": "min_rest_home", "value": 750, "unit": "duration_minutes", "relation": "min"}]},
    ])
    assert tg.kind_conflicts(with_out) == []


def test_численность_сравнивается_как_ровно():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "crew_size", "value": 2, "unit": "count", "relation": "eq"}]}])
    mapping = dict(MAPPING, crew_size="crew_size")
    same = tg.compare(pkg, dict(DEFAULT_READINGS, crew_size=2), profile="DEFAULT", mapping=mapping)
    assert [x["verdict"] for x in same] == [tg.MATCH]
    other = tg.compare(pkg, dict(DEFAULT_READINGS, crew_size=3), profile="DEFAULT", mapping=mapping)
    assert other[0]["verdict"] == tg.DIVERGE and "НЕ РАВЕН" in other[0]["note"]
    off = tg.compare(pkg, dict(DEFAULT_READINGS, crew_size=0), profile="DEFAULT",
                     mapping=mapping, off_literals={"crew_size": 0})
    assert off[0]["verdict"] == tg.INCOMPARABLE and "ВЫКЛЮЧЕНО" in off[0]["note"]


def test_неизвестное_имя_параметра_не_угадывается():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "crew_happiness", "value": 7, "unit": "count",
         "relation": "min"}]}])
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING)[0]
    assert v["verdict"] == tg.INCOMPARABLE
    assert "не известно таблице соответствий" in v["note"]


def test_отменённое_правило_в_сравнение_не_берётся():
    """Поверять решатель тем, что заказчик отозвал, нельзя."""
    pkg = _pkg([{"id": "R-OUT", "kind": "OUT", "params": [
        {"name": "max_duty_duration", "value": 690,
         "unit": "duration_minutes", "relation": "max"}]}])
    assert tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=MAPPING) == []
    # Но и не пропадает молча: отменённое видно отдельным перечнем.
    ex = tg.excluded_params(pkg)
    assert len(ex) == 1 and ex[0]["kind"] == "OUT"


def test_пустые_показания_решателя_отказ():
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "relation": "max"}]}])
    with pytest.raises(tg.ThresholdGateError) as e:
        tg.compare(pkg, {}, profile="DEFAULT", mapping=MAPPING)
    assert "сравнивать пороги пакета не с чем" in str(e.value)


def test_числа_решателя_приходят_аргументом_а_не_живут_в_модуле():
    """Копия чужого порога — второй источник правды, он разойдётся молча.

    Проверяется поведением, а не поиском чисел в тексте: тот же пакет
    против ДВУХ разных показаний обязан дать разные вердикты. Если бы
    модуль хранил свою копию порога, ответ не зависел бы от аргумента.
    """
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "relation": "max"}]}])
    same = tg.compare(pkg, {"max_duty_span_min": 720}, profile="X", mapping=MAPPING)[0]
    other = tg.compare(pkg, {"max_duty_span_min": 900}, profile="X", mapping=MAPPING)[0]
    assert same["verdict"] == tg.MATCH
    assert other["verdict"] == tg.DIVERGE and "180" in other["note"]


@etalon_needed
def test_на_настоящем_эталоне_расхождения_находятся():
    pkg = tp.load(ETALON, run_gate=False)
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT",
                   mapping=MAPPING, qualified=QUALIFIED, off_literals=OFF)
    kinds = {x["verdict"] for x in v}
    # На эталоне 1.3 есть и расхождения, и структурная несравнимость —
    # проверка не вырождается ни в «всё зелено», ни в «всё несравнимо».
    assert tg.DIVERGE in kinds and tg.INCOMPARABLE in kinds
    names = {n for x in v for n in x["params"]}
    assert {"max_duty_duration", "min_rest_home", "min_rest_outstation"} <= names
    # Отчёт печатается и несёт числа обеих сторон.
    text = tg.report(v, tg.excluded_params(pkg))
    assert "720" in text and "840" in text


def test_без_соответствия_от_решателя_отказ():
    """Своей таблицы у гейта нет — она была бы вторым источником правды."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "relation": "max"}]}])
    with pytest.raises(tg.ThresholdGateError) as e:
        tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT", mapping=None)
    assert "paramToThreshold" in str(e.value)


def test_неприменённое_решателем_несравнимо_его_словами():
    """Что решатель отверг сам — несравнимо с ЕГО причиной, не нашей."""
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "min_turnaround", "value": 45, "unit": "duration_minutes",
         "relation": "min", "qualifier": "база"}]}])
    ignored = [{"param": "min_turnaround", "rules": ["R1"],
                "reason": "уточнитель «база» сужает область"}]
    v = tg.compare(pkg, DEFAULT_READINGS, profile="DEFAULT",
                   mapping=MAPPING, ignored=ignored)
    assert len(v) == 1 and v[0]["verdict"] == tg.INCOMPARABLE
    assert "решатель не применил" in v[0]["note"]
    assert "сужает область" in v[0]["note"]


def test_заявленное_решателем_сверяется_с_прочитанным_провайдером():
    """Главная сверка «прислали = применили».

    Решатель ЗАЯВИЛ применённым 720, а созданный провайдер ПРОЧИТАЛ 840 —
    пороги дошли до отчёта, но не до счёта. Ревизия 1 гейта этого
    поймать не могла: показания собирались из того же пакета, и
    сравнение шло пакета с самим собой.
    """
    pkg = _pkg([{"id": "R1", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "relation": "max"}]}])
    claimed = [{"threshold": "max_duty_span_min", "value": 720,
                "fromParams": ["max_duty_duration"], "rules": ["R1"]}]
    # Провайдер прочитал литерал — расхождение, и названо, чьё оно.
    v = tg.compare(pkg, {"max_duty_span_min": 840}, profile="DEFAULT",
                   mapping=MAPPING, applied=claimed)
    diverge = [x for x in v if x["verdict"] == tg.DIVERGE]
    assert any("ЗАЯВИЛ" in x["note"] and "ПРОЧИТАЛ" in x["note"] for x in diverge)
    # Контроль: провайдер прочитал то же — сверка молчит.
    v2 = tg.compare(pkg, {"max_duty_span_min": 720}, profile="DEFAULT",
                    mapping=MAPPING, applied=claimed)
    assert all(x["verdict"] != tg.DIVERGE for x in v2)
