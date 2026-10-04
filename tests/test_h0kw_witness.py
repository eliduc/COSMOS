# -*- coding: utf-8 -*-
"""Расписание-свидетель набора «Полярис-H0-КИ-окна»: генератор строит
допустимый ростер по сбалансированной книге комплекта (замкнутые ротации
без позиционирования) и отрицательные варианты; самопроверка по жёстким
правилам ВМП H0 (с историей, если книга её несёт) даёт ровно ожидание
сценария — не согласие двух реализаций.

Ожидания: nohist-positive и hist-positive — нарушений нет, ночёвок вне базы
нет; hist-neg-A — ровно VN-091 у H03; hist-neg-B — ровно VN-023 у H07.
Записанные файлы witness/*.json совпадают с генератором."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "open-bench" / "polyaris-h0kw"
sys.path.insert(0, str(KIT))
import build_witness_h0kw as w  # noqa: E402


@pytest.mark.parametrize("variant", w.VARIANTS)
def test_witness_matches_scenario(variant):
    sol = w.build_solution(variant)
    viol, facts = w.check(sol)
    got = sorted({(r, e) for r, e, _ in viol})
    assert got == sorted(w.EXPECT_VIOLATIONS[variant]), viol
    assert facts["layovers"] == {}
    # Покрытие: каждое место каждого рейса книги занято ровно двумя
    # действующими пилотами, перегонов нет.
    operating = [a for a in sol["assignments"] if a["deadhead"] == "нет"]
    assert len(operating) == 2 * facts["flights"]
    assert facts["deadheads"] == 0
    # Резерв по базам в худшие сутки — не ниже нормы VN-060 (в пилотах);
    # у PLY и GRT ровно по норме, у SVG запас.
    assert facts["reserve_free_min"] == {"PLY": 4, "SVG": 4, "GRT": 2}
    # Семь суток: месяц не закрыт, месячного правила о выходных в комплекте
    # нет (VN-093 убрано 05.09) — норма выходных месяца не судится.
    assert facts["windows_unjudged_beyond_horizon"] > 0


def test_book_has_no_orphan_night_legs():
    """Ночная программа книги — замкнутые пары внутри горизонта: у PL402
    первых суток есть свой PL401 накануне, у PL401 последних суток — свой
    PL402 назавтра. Сироты делали бы набор неразрешимым без позиционирования."""
    legs = w.legs_by_key()
    assert ("PL402", w.DAYS[0]) not in legs
    assert ("PL401", w.DAYS[-1]) not in legs


def test_scenario_employees_first_duties():
    """Сценарии A и B в книге с историей: H03 начинает дневной ротацией
    (серия ночных сброшена), H07 — не раньше 05.09 10:00 (отдых ≥ 36 ч от
    конца последней смены 03.09 22:00)."""
    sol = w.build_solution("hist-positive")
    by = {}
    for d in sol["duties"]:
        by.setdefault(d["employee"], []).append(d["start"])
    assert min(by["H03"]) == "2026-09-04T08:00"
    assert min(by["H07"]) >= "2026-09-05T10:00"


@pytest.mark.parametrize("variant", w.VARIANTS)
def test_written_files_match_generator(variant):
    path = KIT / "witness" / f"witness-{variant}.json"
    assert path.is_file(), path
    doc = json.loads(path.read_text(encoding="utf-8"))
    sol = w.build_solution(variant)
    assert doc["assignments"] == sol["assignments"]
    assert [d["id"] for d in doc["duties"]] == [d["id"] for d in sol["duties"]]
    assert doc["expected_violations"] == [
        {"rule": r, "employee": e} for r, e in w.EXPECT_VIOLATIONS[variant]]
    assert doc["book"] == w.bw.VARIANTS[w.BOOK_OF[variant]]
    assert (KIT / "witness" / f"witness-{variant}.xlsx").is_file()
