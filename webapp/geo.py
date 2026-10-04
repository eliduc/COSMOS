"""Координаты аэропортов для карты сети (пакет «Карта на гео-подложке»).

Справочник webapp/geo-data/airports-iata.json — выжимка OurAirports
(общественное достояние): {IATA: [lat, lon]}, только трёхбуквенные
коды. Загрузка лениво, кэш на процесс.

ПРАВДА ДИЗАЙНА (решение 2026-08-30): гео-режим — опция, которую
включает ЧЕЛОВЕК; умолчание фронта — схема (силовая раскладка).
Причина: справочник резолвит и вымышленные коды обезличенных наборов,
случайно совпавшие с настоящими (TRD — Тронхейм), и «гео-карта» такого
набора была бы бессмысленной географией. Модуль честно отдаёт всё,
что нашёл, плюс перечень ненайденного — судит человек у экрана.
"""
from __future__ import annotations

import json
from pathlib import Path

_TABLE_PATH = Path(__file__).resolve().parent / "geo-data" / \
    "airports-iata.json"
# Локальные дополнения: ИСТОРИЧЕСКИЕ коды, выпавшие из свежего
# справочника (живой пример 2026-08-30: PBI из данных BTS — аэропорт
# в мире переименован, IATA сменён). Дополнение только ЗАПОЛНЯЕТ
# пробелы — при конфликте побеждает основной справочник.
_EXTRA_PATH = Path(__file__).resolve().parent / "geo-data" / \
    "airports-extra.json"
_table: dict[str, list[float]] | None = None


def _load() -> dict[str, list[float]]:
    global _table
    if _table is None:
        merged = json.loads(_EXTRA_PATH.read_text(encoding="ascii")) \
            if _EXTRA_PATH.is_file() else {}
        merged.update(
            json.loads(_TABLE_PATH.read_text(encoding="ascii")))
        _table = merged
    return _table


def resolve(codes: list[str]) -> dict:
    """Координаты по кодам станций.

    Возвращает {"coords": {code: [lat, lon]}, "missing": [...],
    "complete": bool}: complete — у ВСЕХ кодов есть координаты (только
    тогда фронт предлагает режим «на карте мира»). Пустой список кодов
    — честно complete=False: карте мира нечего показывать.
    """
    table = _load()
    coords: dict[str, list[float]] = {}
    missing: list[str] = []
    for raw in codes:
        code = str(raw or "").strip().upper()
        if not code:
            continue
        if code in table:
            coords[code] = table[code]
        elif code not in missing:
            missing.append(code)
    return {"coords": coords, "missing": missing,
            "complete": bool(coords) and not missing}


# Классы данных, у которых код станции — настоящий IATA и справочник
# по коду уместен. Принцип 05.09.2026 (общий с CDPM, ревизия ночи
# 05→06.09): у синтетического набора вымышленные коды случайно
# совпадают с настоящими (PLY — Плимут, SVG — Ставангер, GRT —
# Гуджрат), и «карта мира» рисовала чужую географию. Координаты книги
# (лист «Аэропорты», «Широта»/«Долгота») ПЕРВИЧНЫ для любого класса;
# справочник по коду применяется только при ЯВНОМ классе open или
# proprietary; synthetic и «класс не указан» — только книга.
REFERENCE_CLASSES = ("open", "proprietary")


def resolve_for(codes: list[str], *, data_class: str | None,
                known: dict | None = None,
                fallback_reference: bool = False) -> dict:
    """Координаты по принципу класса данных.

    known — координаты из данных набора ({code: [lat, lon]}, лист
    «Аэропорты» книги через пакет постановки); они побеждают всегда.
    Справочник по коду добирает пробелы только у классов
    REFERENCE_CLASSES; fallback_reference=True разрешает добор и при
    НЕОБЪЯВЛЕННОМ классе (наборы, загруженные без пакета постановки —
    решение 30.08: режим «карта мира» включает человек); у явного
    synthetic справочник не применяется никогда. Ответ — как у
    resolve() плюс basis: «book» (все координаты из книги),
    «reference» (все из справочника), «book+reference» (смешано),
    «none» (координат нет) — фронт говорит человеку, откуда география.
    """
    cls = str(data_class or "").strip().lower()
    use_ref = cls in REFERENCE_CLASSES or (fallback_reference and not cls)
    coords: dict[str, list[float]] = {}
    missing: list[str] = []
    book_hits = 0
    ref_hits = 0
    for raw in codes:
        code = str(raw or "").strip().upper()
        if not code or code in coords:
            continue
        pt = (known or {}).get(code)
        if pt is None:
            pt = (known or {}).get(str(raw or "").strip())
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            coords[code] = [float(pt[0]), float(pt[1])]
            book_hits += 1
            continue
        if use_ref:
            table = _load()
            if code in table:
                coords[code] = table[code]
                ref_hits += 1
                continue
        if code not in missing:
            missing.append(code)
    basis = ("book+reference" if book_hits and ref_hits
             else "book" if book_hits
             else "reference" if ref_hits else "none")
    return {"coords": coords, "missing": missing,
            "complete": bool(coords) and not missing,
            "basis": basis, "data_class": cls or None}


def known_from_airports(airports) -> dict[str, list[float]]:
    """{code: [lat, lon]} из аэропортов набора решателя (поля
    latitude/longitude, как у домена решателя). Пара 0/0 — «не задано»
    (умолчание домена), в известные не попадает."""
    out: dict[str, list[float]] = {}
    for a in airports or []:
        if not isinstance(a, dict):
            continue
        code = str(a.get("code") or "").strip().upper()
        try:
            lat = float(a.get("latitude"))
            lon = float(a.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not code or (lat == 0.0 and lon == 0.0):
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out[code] = [lat, lon]
    return out
