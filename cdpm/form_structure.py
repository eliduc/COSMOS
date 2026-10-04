# -*- coding: utf-8 -*-
"""Структурное решает код, модель отвечает только за смысл (решение
владельца 2026-09-04, два пункта очереди после прогона H0 человеком).

1. Зона записи — по разделу документа. Когда документы авиакомпании
   написаны в канонической форме ВМП (общий раздел, часть «Пейринг»,
   часть «Ростеринг»), зона каждой записи известна из места, где она
   стоит: общий раздел — обе части, часть 1 — пейринг, часть 2 —
   ростеринг. Это факт о структуре, а не о смысле; модель его выбирать
   не должна (живой случай 04.09: определение кредитного часа из общего
   раздела прочитано как «ростеринг» — 1 расхождение с эталоном из 8
   проходов). Документы без распознанной структуры не трогаются.

2. Цитата параметра — по величине в клаузе. Параметр посадки обязан
   нести дословную цитату-источник; когда модель поставила верное
   значение без цитаты, цитату находит код: та же машинная сверка
   величин клаузы, что у баланса (число, время «11:30» ≡ минуты, часы ≡
   минуты, коды баз), фрагмент — отрезок клаузы с величиной. Не
   найдено — оставляется как есть (находка аудитора остаётся честной).

Правки в модель не записывают служебных заметок (схема meta закрыта):
перечни правок возвращаются вызывающей стороне для отчёта.
"""
from __future__ import annotations

import re

ZONE_BY_PART = {"common": "BOTH", "pairing": "PAIRING", "rostering": "ROSTERING"}


def zones_by_source(entries: list[dict]) -> dict:
    """{(sid, anchor): зона} для записей, распознанных читателем
    канонической формы; пусто — структуры в документах нет."""
    from cdpm import form_text as ft
    try:
        form, report = ft.read_form_text(entries)
    except Exception:                                    # noqa: BLE001
        return {}
    if not report.get("headings"):
        return {}
    out: dict = {}
    for part in ("common", "pairing", "rostering"):
        for sec, rows in (form.get(part) or {}).items():
            for row in rows or []:
                for s in row.get("sources") or []:
                    out[(str(s.get("sid")), str(s.get("anchor")))] = ZONE_BY_PART[part]
    return out


def enforce_form_zones(doc: dict, entries: list[dict]) -> list[dict]:
    """Зона строк и целевых свода — по разделу документа (когда он
    распознан). Возвращает перечень правок {id, from, to, anchor}."""
    zmap = zones_by_source(entries)
    if not zmap:
        return []
    changes: list[dict] = []
    for item in list(doc.get("rules") or []) + list(doc.get("objectives") or []):
        if not isinstance(item, dict) or item.get("kind") == "OUT":
            continue
        src = next((s for s in item.get("sources") or [] if isinstance(s, dict)), None)
        if not src:
            continue
        want = zmap.get((str(src.get("sid")), str(src.get("anchor"))))
        if want and item.get("zone") != want:
            changes.append({"id": item.get("id"), "from": item.get("zone"), "to": want,
                            "anchor": src.get("anchor")})
            item["zone"] = want
    return changes


_SEG_SPLIT = re.compile(r"(?<=[.;])\s+")


def _segment_with(text: str, start: int, end: int) -> str:
    """Отрезок клаузы (по «.»/«;»), содержащий величину; сам текст
    остаётся дословной подстрокой клаузы."""
    pos = 0
    for seg in _SEG_SPLIT.split(text):
        a = text.find(seg, pos)
        b = a + len(seg)
        if a <= start and end <= b:
            return seg.strip().rstrip(".").strip()
        pos = b
    return text[start:end]


def _quote_for_value(clause: str, value) -> str | None:
    from cdpm import measure as ms
    body = ms.carrier_body(clause)
    vals = ms.iter_values(body)
    field = {"value": value, "quote": None}
    hits = [v for v in vals if v.kind not in ("unclassified", "requisite")
            and ms._value_matches_field(v, field)]
    if not hits:
        return None
    v = hits[0]
    seg = _segment_with(body, v.start, v.end)
    # Цитата обязана быть подстрокой клаузы-источника (тот же критерий,
    # что у validate_fit_quotes): тело носителя — часть клаузы.
    return seg if seg and seg in clause else body[v.start:v.end]


def backfill_param_quotes(doc: dict, clause_by_key: dict) -> list[dict]:
    """Цитаты параметров посадок A/B, поставленных без цитаты, — кодом
    из клаузы-источника. Каждая цитата судится тем же критерием, что
    у validate_fit_quotes (дословная подстрока клаузы источника после
    нормализации). Возвращает перечень {id, param, quote}."""
    from cdpm import model as m
    filled: list[dict] = []
    for r in doc.get("rules") or []:
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else None
        if not fit or fit.get("category") not in ("A", "B"):
            continue
        params = fit.get("params")
        if not isinstance(params, dict):
            continue
        srcs = [s for s in r.get("sources") or [] if isinstance(s, dict)]
        clauses = [clause_by_key.get((str(s.get("sid")), str(s.get("anchor")))) for s in srcs]
        clauses = [c for c in clauses if c]
        if not clauses:
            continue
        norm_clauses = [m._fit_norm_text(c) for c in clauses]
        for name, cell in params.items():
            if not isinstance(cell, dict):
                continue
            if str(cell.get("quote") or "").strip():
                continue
            val = cell.get("value")
            if val is None:
                continue
            quote = None
            for c in clauses:
                quote = _quote_for_value(c, val)
                if quote:
                    break
            if not quote:
                continue
            nq = m._fit_norm_text(quote)
            if not nq or not any(nq in t for t in norm_clauses):
                continue
            cell["quote"] = quote
            filled.append({"id": r.get("id"), "param": name, "quote": quote})
    return filled
