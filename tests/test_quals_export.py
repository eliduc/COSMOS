# -*- coding: utf-8 -*-
"""Лист «Допуски» для сертификатора: минуты решателя — датами книги.

Проверяется прочтение границ, а не форма: открытая граница отдаётся
пустой строкой (её нельзя подменять ни 1970 годом, ни сегодняшней
датой), «Действует до» остаётся датой ТЕХ ЖЕ суток, что в книге, хотя
внутри решателя это 23:59 тех суток.
"""

from datetime import datetime, timedelta

from webapp import quals_export as qe
from webapp.task_package import QUAL_OPEN_END_MIN


def _mins(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds() // 60)


def _payload(windows):
    return {"employees": [{"id": "E1", "qualificationWindows": windows}]}


def test_срок_остаётся_датой_книги_а_не_соседними_сутками():
    # Конвертер пишет последнюю минуту суток: 2027-03-31 23:59.
    hi = _mins(datetime(2027, 4, 1)) - 1
    rows = qe.qualification_rows(_payload({"Полярис-Р200": [[0, hi]]}))
    assert rows == [{"employee": "E1", "type": "Полярис-Р200",
                     "valid_from": "", "valid_to": "2027-03-31"}]


def test_открытые_границы_пусты_а_не_выдуманы():
    rows = qe.qualification_rows(_payload({"A320": [[0, QUAL_OPEN_END_MIN]]}))
    assert rows[0]["valid_from"] == "" and rows[0]["valid_to"] == ""


def test_обе_границы_заданы_переносятся_обе():
    lo = _mins(datetime(2026, 6, 1))
    hi = _mins(datetime(2027, 1, 1)) - 1
    rows = qe.qualification_rows(_payload({"A320": [[lo, hi]]}))
    assert rows[0]["valid_from"] == "2026-06-01"
    assert rows[0]["valid_to"] == "2026-12-31"


def test_несколько_окон_и_типов_упорядочены():
    hi = _mins(datetime(2027, 4, 1)) - 1
    payload = {"employees": [
        {"id": "E2", "qualificationWindows": {"B": [[0, hi]], "A": [[0, hi]]}},
        {"id": "E1", "qualificationWindows": {"A": [[0, hi]]}},
    ]}
    rows = qe.qualification_rows(payload)
    assert [(r["employee"], r["type"]) for r in rows] == [
        ("E1", "A"), ("E2", "A"), ("E2", "B")]


def test_нет_окон_нет_строк():
    assert qe.qualification_rows({"employees": [{"id": "E1"}]}) == []
    # Битая запись не роняет выгрузку и не превращается в догадку.
    assert qe.qualification_rows(_payload({"A320": [[0]]})) == []
