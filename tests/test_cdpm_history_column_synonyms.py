# -*- coding: utf-8 -*-
"""Единая версия 06.09.2026 (план §3.8, замечание группы A): заголовки
листа «История» набора сертификатора COSMOS (vmp/run/roster_certify.py)
читаются каноном CDPM в те же поля раздела истории, что и канонические
заголовки книги данных. Иначе формы истории в наборе сертификатора не
находили бы историю и окна оставались бы «неизвестно»."""
from __future__ import annotations

from cdpm import history_section as hs

CANON = {
    "Табельный номер": "E1",
    "Срез": "2026-10-01T00:00",
    "Полётное время с начала месяца, мин": "600",
    "Рабочих дней с начала месяца": "5",
    "Посадок с начала месяца": "7",
    "Последняя смена: конец": "2026-09-30T18:00",
    "Последняя смена: станция": "PLY",
    "Последняя смена: ночная": "нет",
    "Серия рабочих дней": "3",
    "Серия ночных смен": "1",
    "Дней с выходного": "2",
}
CERTIFIER = {
    "Табельный номер": "E1",
    "Срез": "2026-10-01T00:00",
    "Полётное время с начала месяца, мин": "600",
    "Рабочих дней с начала месяца": "5",
    "Посадок с начала месяца": "7",
    "Конец последней смены": "2026-09-30T18:00",
    "Место последней смены": "PLY",
    "Последняя смена ночная": "нет",
    "Рабочих дней подряд": "3",
    "Ночных смен подряд": "1",
    "Дней с последнего выходного": "2",
    # производные величины набора сертификатора — полей раздела нет
    "Есть история": "да",
    "Отдых до горизонта, мин": "360",
}


def _fields(row: dict) -> dict:
    return {col: hs.COLUMNS[col.lower()] for col in row
            if col.lower() in hs.COLUMNS}


def test_certifier_headers_map_to_the_same_record_as_canonical_headers():
    canon_rec, canon_problems = hs.parse_row(CANON, _fields(CANON), {})
    cert_rec, cert_problems = hs.parse_row(CERTIFIER, _fields(CERTIFIER), {})
    assert canon_problems == [] and cert_problems == []
    assert cert_rec == canon_rec
    assert cert_rec["last_duty"]["end_station"] == "PLY"
    assert cert_rec["series"]["consecutive_night_duties"] == 1
    assert cert_rec["month_to_date"]["flight_time_min"] == 600


def test_derived_certifier_columns_have_no_record_field():
    for col in ("Есть история", "Отдых до горизонта, мин"):
        assert col.lower() not in hs.COLUMNS
