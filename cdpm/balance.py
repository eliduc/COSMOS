# -*- coding: utf-8 -*-
"""Баланс потребления величин клаузы (план «прямой ход» 2.4; 4П —
биекция «не потерять» без эталона).

Код перечисляет значимые величины каждой клаузы документов правил
(закрытый мир знаков) и сверяет их с тем, что потребили машинные поля
строк и целевых, трассированных на эту клаузу. Судьбу величины
назначает КОД по её классу:

  * потреблена слотом — число совпало с машинным значением строки
    (в канонических единицах) либо текст величины лежит в цитате слота;
  * потреблена определением — клауза покрыта строкой общего раздела
    (DEF/DATA): определение потребляет свою клаузу целиком;
  * потреблена атрибутом ВМП — валюта, отпечаток базы, титул (meta);
  * не норма по классу кода — ссылка на строку свода, дата, версия,
    номер пункта, категория посадки, служебные слова формы;
  * иначе — БЕЗ СУДЬБЫ: дефект, ремонт или остановка. Величины с
    определённой семантикой (суммы в валюте, длительности, счёты,
    времена суток, дни недели, коды перечней) не могут быть объявлены
    не нормой никем.

Неклассифицированный знак (число, которое код не отнёс ни к одному
классу) — тоже находка: закрытый мир знаков без порога.

    report(entries, doc, verdicts=None) -> сводка с перечнем находок
    python -m cdpm.balance <каталог прогона>   # офлайн, без моделей
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from cdpm import model as m

# ---------------------------------------------------------------- словари
_NUM_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "одного": 1, "одной": 1, "одну": 1,
    "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8,
    "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
    "одиннадцать": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "двадцати": 20, "тридцать": 30,
    "тридцати": 30, "сорок": 40, "сорока": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "ста": 100, "полтора": 1.5, "полутора": 1.5,
}
_SPECIAL_DURATIONS = {"полсуток": 720, "сутки": 1440, "суток": 1440,
                      "неделя": 7 * 1440, "недели": 7 * 1440, "неделю": 7 * 1440}
_WEEKDAYS = ("понедельник", "вторник", "среда", "среду", "среды", "четверг",
             "пятниц", "суббот", "воскресен")
_OPERATORS = ("не менее", "не более", "не меньше", "не больше", "минимум",
              "максимум", "свыше", "сверх", "ровно", "≥", "≤", "не превыша",
              "как минимум", "не позднее", "не ранее", "за каждую", "за каждый",
              "за каждое", "каждые")
_MONEY = r"(усл(?:овн\w*)?\.?\s*ед(?:иниц\w*)?\.?|у\.\s?е\.|руб\w*|₽|usd|eur|евро|доллар\w*)"
_DUR_UNITS = r"(минут\w*|мин\.?|час\w*|ч\.?|сут(?:ок|ки)|дн(?:я|ей|и)|день|недел\w*|месяц\w*)"
_COUNT_NOUNS = r"(экипаж\w*|пилот\w*|смен\w*|сектор\w*|рейс\w*|ноч\w*|дн(?:я|ей|и)|день|перегон\w*|стыков\w*|человек\w*|раз\b|ярус\w*|очк\w*)"
_ID_RE = re.compile(r"\b(?:[A-ZА-Я]{1,3}-(?:[A-ZА-Я0-9]+-)*[A-ZА-Я0-9]+)\b")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b")
_CLOCK_RE = re.compile(r"(?<![\d.,])(\d{1,2}):(\d{2})(?![\d:])")
_PERCENT_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*%")
_MONEY_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*" + _MONEY, re.IGNORECASE)
_DUR_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*" + _DUR_UNITS + r"\b", re.IGNORECASE)
_COUNT_RE = re.compile(r"(?<![\d.,])(\d+)\s*" + _COUNT_NOUNS, re.IGNORECASE)
_NUM_RE = re.compile(r"(?<![\w.,:/-])(\d+(?:[.,]\d+)?)(?![\w:/-])")
_HEX_RE = re.compile(r"\b[0-9a-f]{12,64}\b")
_CODE_RE = re.compile(r"\b[A-Z]{2,4}\b")
_SECTION_NO_RE = re.compile(r"^(?:Раздел|Часть|Глава|Приложение|п\.|пункт)?\s*(\d+(?:\.\d+)*)[\.\)]?\s", re.IGNORECASE)
_VERSION_RE = re.compile(r"верси\w*\s+(?:ставок\s+)?([A-Za-z0-9][\w./-]*)", re.IGNORECASE)
_FIT_CAT_RE = re.compile(r"категори[яи]\s+([ABCD])\b")
_WORD_RE = re.compile(r"[а-яёА-ЯЁa-zA-Z_]+")
_CATALOG_NOUN = r"(?:семь[яиюе]|семейств[оауе]|шаблон[а-яё]*|критери[а-яё]*|словар[а-яё]*)"
_CATALOG_ONE_RE = re.compile(
    r"\bни\s+(?P<one>один|одна|одного|одной|одну)\s+" + _CATALOG_NOUN + r"\b")
_CATALOG_CONTEXT_RE = re.compile(r"\b" + _CATALOG_NOUN + r"\s+каталог[а-яё]*\b")

SEMANTIC_CLASSES = ("amount", "duration", "clock", "count", "weekday", "code",
                    "percent", "number_word")
CODE_CLASSES = ("reference", "date", "version", "section_no", "fit_category",
                "registry_rev", "dictionary_word")
# Знаки, которые вправе потребить определительная строка (DEF/DATA) без
# слота: коды перечня и ссылочные/словарные знаки. Классы SEMANTIC_CLASSES
# в это множество не входят намеренно.
DEFINITIONAL_CLASSES = CODE_CLASSES + ("code",)


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def _num(s: str) -> float:
    return float(str(s).replace(",", "."))


def _minutes(value: float, unit: str) -> float | None:
    u = unit.lower()
    if u.startswith("мин"):
        return value
    if u.startswith("ч"):
        return value * 60
    if u.startswith("сут"):
        return value * 1440
    if u.startswith("дн") or u == "день":
        return value * 1440
    if u.startswith("недел"):
        return value * 7 * 1440
    return None


# ------------------------------------------------------------ распознавание

def _catalog_quantifier_spans(low: str) -> set[tuple[int, int]]:
    """«Ни одна семья каталога» describes coverage, not a parameter of 1.

    Only the negative quantifier of a catalog object is classified. Require
    explicit catalog-object context in the same sentence: neither customer
    counts nor ambiguous «семья» elsewhere acquire this non-normative fate.
    All other values in that sentence still go through the normal balance.
    """
    spans = set()
    for sentence in re.finditer(r"[^.!?\r\n]+", low):
        text = sentence.group()
        if not _CATALOG_CONTEXT_RE.search(text):
            continue
        for match in _CATALOG_ONE_RE.finditer(text):
            tail = text[match.end():]
            # A catalog mentioned elsewhere is not enough to turn an
            # unrelated family count into metadata. Bind this noun directly
            # to the catalog or to an explicit list of its technical objects.
            if not re.match(r"\s+каталог[а-яё]*\b|\s*(?:,|или)\s*"
                            r"(?:критери[а-яё]*|словар[а-яё]*|шаблон[а-яё]*)\b", tail):
                continue
            a, b = match.span("one")
            spans.add((sentence.start() + a, sentence.start() + b))
    return spans


def values_of(text: str, known_ids: set | None = None) -> list[dict]:
    """Значимые величины клаузы: [{class, text, value, span}]; каждый
    знак попадает ровно в один класс (приоритет: дата, версия, ссылка,
    время, сумма, длительность, счёт, процент, число словом, код,
    отпечаток, число)."""
    t = str(text or "")
    taken: list[tuple[int, int]] = []
    out: list[dict] = []

    def _free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def _add(cls: str, mt, value=None, text_override=None):
        a, b = mt.span()
        if not _free(a, b):
            return
        taken.append((a, b))
        out.append({"class": cls, "text": text_override or mt.group(0),
                    "value": value, "span": [a, b]})

    for mt in _DATE_RE.finditer(t):
        _add("date", mt, mt.group(1))
    for mt in _VERSION_RE.finditer(t):
        _add("version", mt, mt.group(1), mt.group(1))
    for mt in _ID_RE.finditer(t):
        tok = mt.group(0)
        if (known_ids and tok in known_ids) or re.match(r"^(VN|O|Q|R|D|W|C|P)-", tok):
            _add("reference", mt, tok)
    for mt in _HEX_RE.finditer(t):
        _add("registry_rev", mt, mt.group(0))
    for mt in _CLOCK_RE.finditer(t):
        _add("clock", mt, int(mt.group(1)) * 60 + int(mt.group(2)))
    for mt in _MONEY_RE.finditer(t):
        _add("amount", mt, _num(mt.group(1)))
    for mt in _PERCENT_RE.finditer(t):
        _add("percent", mt, _num(mt.group(1)))
    for mt in _DUR_RE.finditer(t):
        _add("duration", mt, _minutes(_num(mt.group(1)), mt.group(2)))
    for mt in _COUNT_RE.finditer(t):
        _add("count", mt, _num(mt.group(1)))
    for mt in _FIT_CAT_RE.finditer(t):
        _add("fit_category", mt, mt.group(1))
    for mt in re.finditer(r"ярус\w*\s+(\d+)", t, re.IGNORECASE):
        _add("tier", mt, int(mt.group(1)))
    for mt in re.finditer(r"(?<![\d.])(\d{1,2})\.(20\d{2})(?![\d.])", t):
        _add("date", mt, mt.group(0))            # месяц.год
    sec = _SECTION_NO_RE.match(t)
    if sec:
        span = sec.span(1)
        if _free(*span):
            taken.append(span)
            out.append({"class": "section_no", "text": sec.group(1),
                        "value": sec.group(1), "span": list(span)})
    # Ячейка или строка, состоящая из одного номера пункта («2.1», «1.3.»)
    if re.fullmatch(r"\s*\d+(?:\.\d+)+\.?\s*", t) and _free(0, len(t)):
        taken.append((0, len(t)))
        out.append({"class": "section_no", "text": t.strip(), "value": t.strip(),
                    "span": [0, len(t)]})
    low = t.lower()
    catalog_quantifiers = _catalog_quantifier_spans(low)
    for mt in re.finditer(r"[а-яё]+", low):
        w = mt.group(0)
        if w in _SPECIAL_DURATIONS and _free(*mt.span()):
            # Слово-длительность без числа («сутки», «неделя») — величина
            # только при слове меры рядом («не менее суток», «в течение
            # суток», «за сутки»); «в конце недели», «сутки — по времени
            # базы» — существительное, не величина.
            before = low[max(0, mt.start() - 40):mt.start()]
            if re.search(r"(не менее|не более|не меньше|не больше|в течение|"
                         r"за|через|каждые|каждую|каждый|минимум|максимум|"
                         r"до|после|не реже|не чаще|раз в|свыше|более|менее)\s*$",
                         before):
                _add("duration", mt, _SPECIAL_DURATIONS[w], t[mt.start():mt.end()])
        elif w in _NUM_WORDS and _free(*mt.span()):
            if mt.span() in catalog_quantifiers:
                _add("dictionary_word", mt, t[mt.start():mt.end()], t[mt.start():mt.end()])
            else:
                _add("number_word", mt, _NUM_WORDS[w], t[mt.start():mt.end()])
        elif any(w.startswith(d) for d in _WEEKDAYS) and _free(*mt.span()):
            _add("weekday", mt, w, t[mt.start():mt.end()])
    for mt in _CODE_RE.finditer(t):
        _add("code", mt, mt.group(0))
    dictionary = set(m.PARAMS) | set(m.PARAMS.values()) | set(m.template_families()) \
        | set(m.family_conditions()) \
        | set(m.CRITERION_KINDS) | set(m.CRITERION_KINDS.values()) | set(m.METRICS)
    for mt in _WORD_RE.finditer(t):
        w = mt.group(0)
        if (w in dictionary or w.lower() in dictionary) and _free(*mt.span()):
            _add("dictionary_word", mt, w)
    for mt in _NUM_RE.finditer(t):
        _add("number", mt, _num(mt.group(1)))
    out.sort(key=lambda v: v["span"][0])
    return out


def operators_of(text: str) -> list[str]:
    low = _norm(text)
    return [op for op in _OPERATORS if op in low]


# --------------------------------------------------------------- потребление

_UNIT_KIND = {"duration_minutes": "duration", "time_of_day_minutes": "clock",
              "count": "count", "hours_per_credit_hour": "ratio",
              "credit_hours_per_hour": "ratio", "credit_hours": "duration"}


class _Pools:
    """Типизированные носители машинных полей строк клаузы: число
    потребляется только полем СВОЕГО класса (сумма — ставкой или весом,
    длительность — параметром длительности, счёт — параметром счёта,
    ярус — ярусом); совпадение числа с полем чужого класса не потребление
    (ярус 1 против ставки 1 — живой ложный зачёт при отладке)."""

    def __init__(self):
        self.nums: dict = {"amount": set(), "duration": set(), "clock": set(),
                           "count": set(), "ratio": set(), "tier": set(),
                           "percent": set(), "other": set()}
        self.texts: set = set()       # тексты-носители нормы (цитата посадки, записи «как написано»)
        self.evidence: set = set()    # доказательный слой: цитаты ячеек параметров — НЕ потребление
        self.words: set = set()

    def add_num(self, kind: str, v) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            self.nums.setdefault(kind, set()).add(float(v))

    def merge(self, other: "_Pools") -> None:
        for k, s in other.nums.items():
            self.nums.setdefault(k, set()).update(s)
        self.texts |= other.texts
        self.evidence |= other.evidence
        self.words |= other.words


def _structured_records(row, cells, clause_text_by_key, doc):
    """Diagnose each structured slot through the strict consumer projection.

    A quotation is evidence, not a substitute for its machine value. In
    particular, a base/count map must keep each base/value pair, and an
    answer-derived cell needs the full question/version provenance. Without
    source context a new structured value cannot receive machine credit.
    A bad neighboring cell remains a full-row landing/export error, not a
    missing value in this healthy cell. Only this diagnostic view is sliced;
    the canonical row and the consumer's whole-row gate are never changed.
    """
    names = {name for name, cell in cells.items()
             if isinstance(name, str) and isinstance(cell, dict)
             and isinstance(cell.get("value"), (dict, list, tuple, set))}
    if not names or clause_text_by_key is None or doc is None:
        return []
    legacy = row.get("params")
    if legacy is not None and (not isinstance(legacy, list) or any(
            not isinstance(prm, dict) or not isinstance(prm.get("name"), str)
            for prm in legacy)):
        return []
    records = []
    for name in sorted(names):
        view = copy.deepcopy(row)
        view["template_fit"]["params"] = {name: copy.deepcopy(cells[name])}
        if legacy is not None:
            view["params"] = [copy.deepcopy(prm) for prm in legacy if prm["name"] == name]
        projected, problems = m.logic_param_records(view, selected_names={name})
        if problems or m.validate_fit_quotes({**doc, "rules": [view]}, clause_text_by_key):
            continue
        records.extend(projected)
    return records


def _row_pools(row: dict, *, clause_text_by_key=None, doc=None) -> _Pools:
    p = _Pools()
    fit = row.get("template_fit") if isinstance(row.get("template_fit"), dict) else {}
    if fit.get("family"):
        p.words.add(str(fit["family"]))
    if fit.get("category"):
        p.words.add(f"категория {fit['category']}")
    if fit.get("quote"):
        # Цитата посадки — носитель нормы БЕСПАРАМЕТРНОЙ семьи. У посадки
        # с params носитель величин — ячейки параметров; цитата семьи
        # тогда лишь доказывает посадку и величины не потребляет (иначе
        # искажённый порог при верной цитате прятался в «держится
        # текстом»: реестр 2712cf6 дал семье разброса словарный параметр,
        # а посадка несла и params, и цитату).
        (p.evidence if fit.get("params") else p.texts).add(_norm(fit["quote"]))
    # С-1 на пути РЕМОНТА (находка аудитора GPT174). Расчёт баланса
    # стоит ПЕРЕД ремонтом (`extract`: баланс → находки → `repair`), и
    # ремонт по своему назначению чинит ПОВРЕЖДЁННЫЕ своды — то есть
    # такие, где схема уже краснеет. Прежнее `or {}` спасало только
    # ЛОЖНЫЕ формы (`0`, `[]`, `None`), а истинное не-хранилище
    # оставалось собой: `1`, `7.5`, `True` роняли `.items()`
    # `AttributeError`-ом ДО того, как ремонт успевал сказать слова.
    # Кривая форма = ячеек нет; беду называет валидатор, а не заплата.
    ячейки = fit.get("params")
    for name, cell in (ячейки if isinstance(ячейки, dict) else {}).items():
        p.words.add(str(name))
        kind = _UNIT_KIND.get(m.PARAMS.get(str(name), ""), "other")
        val = cell.get("value") if isinstance(cell, dict) else cell
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            p.add_num(kind, val)
        elif isinstance(val, str):
            p.texts.add(_norm(val))
            for part in re.split(r"[,\s;]+", val):
                if part:
                    p.texts.add(_norm(part.split(":")[0]))
                    tail = part.split(":")[-1]
                    if tail.isdigit():
                        p.add_num(kind, int(tail))
        if isinstance(cell, dict) and cell.get("quote"):
            # Цитата ячейки параметра доказывает ЗНАЧЕНИЕ параметра, а не
            # потребляет величины сама по себе (проверка плана №11):
            # искажённое значение при сохранённой цитате обязано всплыть.
            p.evidence.add(_norm(cell["quote"]))
    for prm in _structured_records(
            row, ячейки if isinstance(ячейки, dict) else {}, clause_text_by_key, doc):
        unit = prm.get("unit")
        p.add_num(_UNIT_KIND.get(unit, "other"), prm.get("value"))
        if unit == "code_list":
            p.words.update(m.split_code_list(prm["value"]))
        elif unit == "code":
            p.words.add(prm["value"])
        if isinstance(prm.get("qualifier"), str):
            p.words.add(prm["qualifier"])
        # Do not add as_written to texts: a valid quote must not hide an
        # omitted code/count or a different unconsumed value in that quote.
    for prm in row.get("params") or []:
        if isinstance(prm, dict):
            p.words.add(str(prm.get("name")))
            kind = _UNIT_KIND.get(str(prm.get("unit") or ""), "other")
            p.add_num(kind, prm.get("value"))
            if prm.get("as_written"):
                p.texts.add(_norm(prm["as_written"]))
    if row.get("weight") is not None:
        p.add_num("amount", row["weight"])
    crit = row.get("criterion") if isinstance(row.get("criterion"), dict) else None
    if crit:
        p.words.add(str(crit.get("kind")))
        p.words.add(str(crit.get("kind")).upper())
        p.add_num("amount", crit.get("rate_per_duty"))
        for v in (crit.get("rates") or {}).values():
            p.add_num("amount", v)
        for k in ("as_written", "per", "version", "effective_from"):
            if crit.get(k):
                p.texts.add(_norm(crit[k]))
    rate = row.get("rate") if isinstance(row.get("rate"), dict) else None
    if rate:
        p.add_num("amount", rate.get("value"))
        for k in ("as_written", "per"):
            if rate.get(k):
                p.texts.add(_norm(rate[k]))
    if row.get("metric"):
        p.words.add(str(row["metric"]))
    tol = row.get("tolerance") if isinstance(row.get("tolerance"), dict) else None
    if tol and tol.get("value") is not None:
        p.add_num("percent" if tol.get("unit") == "percent" else "other", tol["value"])
    if "direction" in row:                      # целевая: ярус по умолчанию 1
        p.add_num("tier", row.get("tier") or 1)
    es = row.get("export_solver") if isinstance(row.get("export_solver"), dict) else {}
    for d in es.get("days") or []:
        p.words.add(str(d))
    for s in row.get("sources") or []:
        if isinstance(s, dict) and s.get("quote"):
            p.texts.add(_norm(s["quote"]))
    return p


_WEEKDAY_EN = {"понедельник": "monday", "вторник": "tuesday", "среда": "wednesday",
               "среду": "wednesday", "среды": "wednesday",
               "четверг": "thursday", "пятниц": "friday", "суббот": "saturday",
               "воскресен": "sunday"}


_CLASS_TO_POOLS = {
    "amount": ("amount",),
    "duration": ("duration",),
    "clock": ("duration", "clock"),
    "count": ("count", "amount", "other"),
    "percent": ("percent",),
    "tier": ("tier",),
    "number_word": ("count", "duration", "amount", "other"),
    "number": ("amount", "duration", "clock", "count", "ratio", "percent", "other"),
}


def consumption(v: dict, pools: _Pools) -> str | None:
    """Судьба величины по носителям строки: «слот» — машинная сверка
    значения с типизированным носителем (числовые классы) либо словарным
    словом; «текст» — величина лишь входит в текст цитаты/записи строки
    (доказательный слой), машинного слота у неё нет; None — не потреблена.
    Проверка плана №11: вхождение в цитату прежде числовой сверки делало
    баланс слепым к искажённому значению параметра при верной цитате."""
    cls, val, txt = v["class"], v["value"], v["text"]
    nt = _norm(txt)
    texts, words = pools.texts, pools.words
    in_text = any(nt and nt in q for q in texts)
    if cls in _CLASS_TO_POOLS:
        if isinstance(val, (int, float)):
            allowed: set = set()
            for k in _CLASS_TO_POOLS[cls]:
                allowed |= pools.nums.get(k, set())
            if float(val) in allowed:
                return "слот"
            # часы против минут: «12 часов» = 720 у параметра длительности.
            if cls in ("count", "number", "number_word") \
                    and float(val) * 60 in pools.nums.get("duration", set()):
                return "слот"
        return "текст" if in_text else None
    if in_text:
        return "слот"
    if cls == "weekday":
        stem = next((d for d in _WEEKDAYS if str(val).startswith(d)), None)
        return "слот" if (stem and _WEEKDAY_EN.get(stem) in words) else None
    if cls in ("code",):
        return "слот" if any(nt == _norm(w) for w in words) else None
    if cls == "dictionary_word":
        return "слот" if (txt in words or txt.lower() in {w.lower() for w in words}) else None
    if cls == "fit_category":
        return "слот" if f"категория {val}" in words else None
    if cls == "version":
        return "слот" if any(_norm(val) in q for q in texts) else None
    if cls == "date":
        return "слот" if any(_norm(val) in q for q in texts) else None
    return None


def _consumed(v: dict, pools: _Pools) -> bool:
    """Потреблена ли величина хоть как-то (слот или текст) — для ремонта
    и совместимости; баланс различает судьбы через consumption()."""
    return consumption(v, pools) is not None


def report(entries: list[dict], doc: dict, verdicts: dict | None = None) -> dict:
    """Баланс по каждой клаузе документов правил."""
    verdicts = verdicts or {}
    known_ids = {r.get("id") for r in doc.get("rules", [])} \
        | {o.get("id") for o in doc.get("objectives", [])} \
        | {q.get("id") for q in doc.get("questions", [])}
    rows_by_anchor: dict = {}
    for coll in ("rules", "objectives"):
        for item in doc.get(coll, []) or []:
            if not isinstance(item, dict):
                continue
            for s in item.get("sources") or []:
                if isinstance(s, dict):
                    rows_by_anchor.setdefault((str(s.get("sid")), str(s.get("anchor"))),
                                              []).append(item)
    meta = doc.get("meta") or {}
    meta_texts = {_norm(meta.get(k)) for k in ("currency", "registry_rev_declared",
                                              "customer") if meta.get(k)}
    # Коды перечней общего раздела (DEF/DATA): упоминание такого кода в
    # другой клаузе — ссылка на запись перечня, потреблённая определением.
    registry_codes: set = set()
    for r in doc.get("rules", []) or []:
        if isinstance(r, dict) and r.get("kind") in ("DEF", "DATA"):
            registry_codes |= set(_CODE_RE.findall(str(r.get("statement") or "")))
            registry_codes |= {str(c.get("code")) for c in (r.get("codes") or [])
                               if isinstance(c, dict) and c.get("code")}
    findings: list[dict] = []
    held_by_text: list[dict] = []
    by_class: dict = {}
    by_fate: dict = {}
    total = consumed = 0
    clauses = 0
    clause_text_by_key = {(str(e.get("sid")), str(c.get("anchor"))):
                          str(c.get("text") or "").replace("**", "")
                          for e in entries for c in e.get("clauses") or []}
    seen_clauses = set()
    for e in entries:
        sid = str(e.get("sid"))
        for c in e.get("clauses") or []:
            # Use exactly the reading normalization, not a broader text
            # equivalence: Markdown bold does not create another obligation.
            text = str(c.get("text") or "").replace("**", "")
            # Repeated transport of the same normalized source clause is not a
            # second obligation. Different sources, text or occurrences
            # inside one clause remain distinct and are still checked.
            identity = (sid, str(e.get("file")), str(e.get("sha256")),
                        str(c.get("anchor")), text)
            if identity in seen_clauses:
                continue
            seen_clauses.add(identity)
            clauses += 1
            anchor = str(c.get("anchor"))
            key = (sid, anchor)
            rows = rows_by_anchor.get(key, [])
            verdict = verdicts.get(key)
            definitional = any(r.get("kind") in ("DEF", "DATA") for r in rows)
            excluded = any(r.get("kind") == "OUT" for r in rows)
            pools = _Pools()
            for r in rows:
                pools.merge(_row_pools(r, clause_text_by_key=clause_text_by_key, doc=doc))
            vals = values_of(text, known_ids)
            ops = operators_of(text)
            for v in vals:
                total += 1
                by_class[v["class"]] = by_class.get(v["class"], 0) + 1
                cls = v["class"]
                fate = None
                how = consumption(v, pools)
                if how == "слот":
                    fate = "слот"
                elif how == "текст":
                    # Величина есть в цитате строки, но машинного слота
                    # у неё нет — держится текстом (проверка плана №11):
                    # считается отдельно и печатается поимённо.
                    fate = "держится текстом цитаты (без машинного слота)"
                    held_by_text.append({"sid": sid, "anchor": anchor, "class": cls,
                                         "text": v["text"], "rows": [r.get("id") for r in rows]})
                elif excluded:
                    fate = "исключено решением (OUT)"
                elif definitional and cls in DEFINITIONAL_CLASSES:
                    # Судьба (е): определительная строка потребляет только
                    # коды перечня и словарные/ссылочные знаки. Измеримая
                    # величина внутри определения (окно 22:00–06:00, «72
                    # часа», «двух») судьбы (е) не получает — слот или
                    # находка (К-3 проверки плана: иначе определение —
                    # лазейка для величин).
                    fate = "определение"
                elif cls in ("registry_rev",) or (cls in ("code", "dictionary_word", "number")
                                                  and _norm(v["text"]) in meta_texts):
                    fate = "атрибут ВМП"
                elif any(_norm(v["text"]) in mt for mt in meta_texts if mt):
                    fate = "атрибут ВМП"
                elif cls == "code" and v["text"] in registry_codes:
                    fate = "ссылка на запись перечня"
                elif cls in ("reference", "date", "version", "section_no",
                             "fit_category", "dictionary_word"):
                    fate = "не норма (класс кода)"
                elif verdict and verdict.get("verdict") == "не правило" \
                        and cls not in SEMANTIC_CLASSES:
                    fate = "не норма (вердикт структуры)"
                elif any(isinstance(r.get("template_fit"), dict)
                         and r["template_fit"].get("category") in ("C", "D")
                         and not str(r["template_fit"].get("gap") or "")
                         .startswith("посадка модели отброшена")
                         and r.get("kind") in ("HARD", "SOFT") for r in rows):
                    # Судьба (б): строка посадки C/D несёт норму текстом,
                    # машинных слотов у неё нет — величина потреблена
                    # неформализованной строкой. Подтверждение кодом «ни
                    # одна семья не подходит по сигнатуре» (Д7 плана) —
                    # следующий шаг; до него судьба считается отдельно.
                    fate = "неформализованная строка (посадка C)"
                if fate:
                    consumed += 1
                    by_fate[fate] = by_fate.get(fate, 0) + 1
                    continue
                findings.append({"sid": sid, "anchor": anchor, "class": cls,
                                 "text": v["text"], "value": v["value"],
                                 "rows": [r.get("id") for r in rows],
                                 "structure_verdict": (verdict or {}).get("reason"),
                                 "note": ("измеримая величина внутри определительной "
                                          "строки: слот или вопрос «параметр или "
                                          "справка?»"
                                          if definitional and cls in SEMANTIC_CLASSES
                                          else "величина семантического класса в клаузе с "
                                          "вердиктом «не правило»"
                                          if verdict and cls in SEMANTIC_CLASSES
                                          else "не потреблена ни одним слотом")})
            if ops and not vals and not definitional and not excluded and not verdict:
                findings.append({"sid": sid, "anchor": anchor, "class": "operator",
                                 "text": ", ".join(ops), "value": None,
                                 "rows": [r.get("id") for r in rows],
                                 "structure_verdict": None,
                                 "note": "оператор сравнения без связанной величины"})
            # Ставка без «за что»: сумма потреблена ставкой/критерием, а
            # клауза называет, за что она начисляется («… за календарный
            # день …»), но слот per пуст (форма ВМП: строка в) несёт per).
            for r in rows:
                carriers = [x for x in (r.get("criterion"), r.get("rate"))
                            if isinstance(x, dict)]
                for cr in carriers:
                    has_amount = cr.get("rate_per_duty") is not None \
                        or cr.get("value") is not None or cr.get("rates")
                    if has_amount and not cr.get("per") \
                            and re.search(r"\bза\s+(?:каждый|каждую|каждое|один|одну)?\s*[а-яё]", text.lower()):
                        findings.append({"sid": sid, "anchor": anchor,
                                         "class": "rate_per", "text": "за …",
                                         "value": None, "rows": [r.get("id")],
                                         "structure_verdict": None,
                                         "note": "ставка без слота «за что», хотя "
                                                 "клауза называет, за что она "
                                                 "начисляется"})
    return {"clauses": clauses, "values": total, "consumed": consumed,
            "unconsumed": len(findings), "by_class": by_class,
            "by_fate": by_fate, "held_by_text": held_by_text,
            "findings": findings}


def main(argv=None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Баланс потребления величин клауз")
    ap.add_argument("run_dir", help="каталог прогона (freeze/, extract/model.json)")
    ap.add_argument("--model", default=None, help="иная модель вместо extract/model.json")
    a = ap.parse_args(argv)
    from cdpm import coverage as cov
    from cdpm import extract as extract_mod
    run = Path(a.run_dir)
    entries = extract_mod._load_kit(run / "freeze")
    doc = m.load_model(Path(a.model) if a.model else run / "extract" / "model.json")
    rep = report(entries, doc, cov.run_verdicts(run))
    print(f"клауз {rep['clauses']}, величин {rep['values']}, потреблено "
          f"{rep['consumed']}, без судьбы {rep['unconsumed']}; классы: {rep['by_class']}")
    for f in rep["findings"]:
        print(f"  ? {f['sid']}/{f['anchor']} [{f['class']}] {f['text']!r} — {f['note']}"
              f" (строки: {', '.join(x for x in f['rows'] if x) or '—'})")
    return 0 if not rep["findings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
