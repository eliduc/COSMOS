# -*- coding: utf-8 -*-
"""Стадия структуры документа — вердикты КОДОМ до извлечения (план
«прямой ход» 2.2, решения владельца 02.09).

Код ставит вердикт клаузе только по ПОЛОЖИТЕЛЬНОМУ структурному
признаку; всё остальное остаётся кандидатом для модели (клауза с
величиной, кодом перечня или субъектом планирования — всегда кандидат,
отсев «за отсутствие признаков» запрещён). Классы вердиктов кода:

  * «пусто»            — клауза без текста;
  * «заголовок»        — заголовок раздела формы ВМП (SECTION_TITLES),
                         заголовок «Раздел N.» / «Часть N.» / «Глава N.»
                         без величин, титул документа первой строкой;
  * «пустой раздел»    — «(нет)» под заголовком раздела формы (канон
                         рендера пустого раздела; ИДАК H0-К 04.09);
  * «мета-предложение» — предложение о самом ВМП, а не о планировании:
                         титул ВМП, валюта ставок и база шаблонов,
                         канон композиции целевой; потребляется
                         атрибутами meta (currency, registry_rev), не
                         строками свода.

Модельные вердикты («не правило» с причиной) приходят из извлечения и
цикла отдельно (cdpm.coverage); здесь — только код.

    code_verdicts(entries) -> {(sid, anchor): {"verdict", "reason", "by": "code", "meta": {...}?}}
    meta_from_verdicts(verdicts) -> {"currency": ..., "registry_rev": ...}
"""
from __future__ import annotations

import re

from cdpm import vmp_form as vf

NOT_RULE = "не правило"
_HEADINGS = set(vf.SECTION_TITLES.values())
_EMPTY_SECTION = "(нет)"       # канонический рендер пустого раздела формы
_SECTION_RE = re.compile(r"^(Раздел|Часть|Глава|Приложение)\s+\d+[\.\)]?(\s+\S.*)?$")
_TITLE_RE = re.compile(r"^ВМП\s+—\s+(?P<c>.+)$")
_CURRENCY_RE = re.compile(
    r"^Валюта ставок и коэффициентов:\s*(?P<cur>.+?)\.\s*"
    r"(?:База шаблонов:\s*(?P<rev>\S+?)\.)?$")
_DIGIT_RE = re.compile(r"\d")


def _clean(text) -> str:
    return " ".join(str(text or "").replace("**", "").split())


def code_verdicts(entries: list[dict]) -> dict:
    out: dict = {}
    for e in entries:
        sid = str(e.get("sid"))
        clauses = e.get("clauses") or []
        for i, c in enumerate(clauses):
            anchor = str(c.get("anchor"))
            t = _clean(c.get("text"))
            key = (sid, anchor)
            if not t:
                out[key] = {"verdict": NOT_RULE, "reason": "пусто", "by": "code"}
                continue
            if t in _HEADINGS:
                out[key] = {"verdict": NOT_RULE, "reason": "заголовок раздела формы",
                            "by": "code"}
                continue
            if t == _EMPTY_SECTION:
                # Канонический рендер ПУСТОГО раздела формы (vmp_form:
                # «(нет)» под заголовком) — структура, не кандидат;
                # ИДАК H0-К: разделы «в) критерии оптимизации» пусты.
                out[key] = {"verdict": NOT_RULE, "reason": "пустой раздел формы («(нет)»)",
                            "by": "code"}
                continue
            m_t = _TITLE_RE.match(t)
            if m_t and i == 0:
                out[key] = {"verdict": NOT_RULE, "reason": "титул ВМП (мета-предложение)",
                            "by": "code", "meta": {"customer": m_t.group("c").strip()}}
                continue
            m_c = _CURRENCY_RE.match(t)
            if m_c:
                meta = {"currency": m_c.group("cur").strip()}
                if m_c.group("rev"):
                    meta["registry_rev"] = m_c.group("rev")
                out[key] = {"verdict": NOT_RULE,
                            "reason": "валюта ставок и база шаблонов (мета-предложение)",
                            "by": "code", "meta": meta}
                continue
            if t == vf.COMPOSITION_TEXT:
                out[key] = {"verdict": NOT_RULE,
                            "reason": "канон композиции целевой (мета-предложение)",
                            "by": "code"}
                continue
            if _SECTION_RE.match(t) and not _DIGIT_RE.search(
                    _SECTION_RE.match(t).group(2) or ""):
                out[key] = {"verdict": NOT_RULE, "reason": "заголовок раздела документа",
                            "by": "code"}
                continue
    return out


def meta_from_verdicts(verdicts: dict) -> dict:
    """Атрибуты ВМП, потреблённые мета-предложениями (валюта, отпечаток
    базы, титул). Противоречивые значения (две валюты) — не берутся,
    остаются на суд баланса."""
    out: dict = {}
    conflicts: set = set()
    for v in verdicts.values():
        for k, val in (v.get("meta") or {}).items():
            if k in out and out[k] != val:
                conflicts.add(k)
            out.setdefault(k, val)
    for k in conflicts:
        out.pop(k, None)
    return out
