# -*- coding: utf-8 -*-
"""Каноническая текстовая форма ВМП — чтение КОДОМ (обратное
`vmp_form.render_row_line`).

Принцип (ступень ИДАК-0, владелец 2026-09-02): документ, записанный в
канонической текстовой форме ВМП (заголовок ВМП, заголовки разделов
формы, запись формы одной строкой), читается детерминированно — без
языковой модели: строка → запись формы → форма (`vmp_form`) → машинная
модель (`vmp_form.from_form`). Модель нужна только зрению (изображение
→ текст, штатная заморозка). То, что написано в канонической форме,
конвейер не переизвлекает и не переспрашивает: цикл и анкета к таким
записям не применяются.

Грамматика записи (одна строка):
    <id>. <название>. <формулировка> [Шаблон: <семья> (категория A|B
    [; допущение: …])[; носитель нормы: «…»].] [Шаблона нет: <gap>.]
    [Параметры: <имя> = <значение> [<единица>] («<цитата>»)[; …].]
    [Штраф: <коэффициент> за <единица нарушения по-русски>.]
    [Область: пейринг — …; ростеринг — ….] [Запись листа исключений.]
    [Пометка: исключено … авиакомпании (вопрос <id>, ред. <n>).]
    [Примечание: ….]
  определения: … Вид записи: запись файла данных. | определение или
    перечень документа. [Пометка …] [Примечание …]
  критерии:    … Направление: minimize|maximize[; метрика: <m>]; ярус
    <n>[; допуск <v> percent|absolute]. [Машинный критерий: <kind>][;
    ставка <v>[ за <per>]][; ставки рангов: полёт <f>, работа <w>][;
    версия ставок <v>][; действует с <d>]. [Примечание …]

Разбор fail-closed: строка, не разобранная целиком, записью не
считается и попадает в отчёт «не распознано» — никаких догадок.

    parse_row_line(text, section) -> row | None
    read_form_text(entries) -> (form, report)
    model_from_form_text(form) -> model
"""
from __future__ import annotations

import copy
import re

from cdpm import model as m
from cdpm import vmp_form as vf

_HEADINGS = {title: key for key, title in vf.SECTION_TITLES.items()}
_SEG_MARKERS = ("Шаблон: ", "Шаблона нет: ", "Параметры: ", "Штраф: ",
                "Область: ", "Пометка: ", "Вид записи: ", "Направление: ",
                "Машинный критерий: ", "Ставка ", "Ставки рангов: ",
                "Запись листа исключений.", "Примечание: ")
_ROW_RE = re.compile(
    r"^(?P<id>[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\. "
    r"(?P<title>[^.]+?)\. (?P<rest>.+)$", re.S)
_HEADER_CUSTOMER = re.compile(r"^ВМП — (?P<c>.+)$")
_HEADER_CURRENCY = re.compile(
    r"^Валюта ставок и коэффициентов: (?P<cur>.+?)\. "
    r"База шаблонов: (?P<rev>\S+?)\.$")
_TEMPLATE_RE = re.compile(
    r"^(?P<family>[a-z_0-9]+) \(категория (?P<cat>[AB])"
    r"(?:; допущение: (?P<assumption>.+?))?\)"
    r"(?:; носитель нормы: «(?P<quote>.+?)»)?$", re.S)
#: Выражение параметров строится ЛЕНИВО (`_param_re()` ниже, шаг 7
#: каталога): единицы — словарь каталога, и вычисление на уровне модуля
#: роняло бы импорт `cdpm.form_text` без каталога.
_PARAM_RE_SNAPSHOT: dict = {}
#: Обратная карта «слово единицы → код» (`unit_from_word()` ниже):
#: тоже лениво и тоже по словарю каталога — рукописной карты нет.
_UNIT_FROM_RU_SNAPSHOT: dict = {}
_PENALTY_RE = re.compile(r"^(?P<coef>\d+) за (?P<unit>.+)$", re.S)
_SCOPE_RE = re.compile(r"^пейринг — (?P<p>.+?); ростеринг — (?P<r>.+)$", re.S)
_EXCLUDED_RE = re.compile(
    r"^исключено из расчёта до получения данных по решению авиакомпании"
    r"(?: \(вопрос (?P<q>[^,]+), ред\. (?P<rev>\d+)\))?$", re.S)
_DIRECTION_RE = re.compile(
    r"^(?P<d>minimize|maximize)(?:; метрика: (?P<m>[a-z_]+))?; ярус "
    r"(?P<t>\d+)(?:; допуск (?P<tv>\d+(?:\.\d+)?) (?P<tu>percent|absolute))?$")
_CRIT_KIND_RE = re.compile(r"^Машинный критерий: (?P<k>\S+)$")
_CRIT_RATE_RE = re.compile(r"^[Сс]тавка (?P<v>\d+(?:\.\d+)?)(?: за (?P<per>.+))?$", re.S)
_CRIT_RATES_RE = re.compile(
    r"^[Сс]тавки рангов: полёт (?P<f>\d+(?:\.\d+)?), работа (?P<w>\d+(?:\.\d+)?)$")
_CRIT_VERSION_RE = re.compile(r"^версия ставок (?P<v>.+)$", re.S)
_CRIT_DATE_RE = re.compile(r"^действует с (?P<d>.+)$", re.S)
_ROLE_TEXT = {"запись файла данных": "registry",
              "определение или перечень документа": "term",
              "определение термина": "term"}          # прежняя редакция


def unit_from_word(word: str) -> str:
    """Код единицы размера нарушения по слову документа. Карта обратная
    словарю каталога (`violation_units`: код → слово) и строится ЛЕНИВО —
    вычисление на уровне модуля роняло бы импорт без каталога. Слово не
    из словаря возвращается как есть: разбор не выдумывает код, а отдаёт
    прочитанное — судит его рубеж формы."""
    words = vf.violation_unit_words()
    got = _UNIT_FROM_RU_SNAPSHOT.get("map")
    if got is None or got[0] is not words:
        got = (words, {w: c for c, w in words.items()})
        _UNIT_FROM_RU_SNAPSHOT["map"] = got
    return got[1].get(word, word)


def _param_re():
    """Выражение разбора строки параметров формы по единицам словаря
    каталога. Снимок держится по тождеству словаря PARAMS: сменился
    экземпляр каталога — выражение пересобирается."""
    params = m.PARAMS
    got = _PARAM_RE_SNAPSHOT.get("re")
    if got is not None and got[0] is params:
        return got[1]
    units = sorted({u for u in params.values()}, key=len, reverse=True)
    rx = re.compile(
        r"(?P<name>[a-z_0-9]+) = (?P<value>.+?)"
        r"(?: (?P<unit>" + "|".join(re.escape(u) for u in units) + r"))?"
        r" \(«(?P<quote>.*?)»\)(?:; |$)", re.S)
    _PARAM_RE_SNAPSHOT["re"] = (params, rx)
    return rx


def _num(s: str):
    s = s.strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _strip_dot(s: str) -> str:
    s = s.strip()
    return s[:-1].rstrip() if s.endswith(".") else s


def _split_segments(rest: str) -> tuple[str, list]:
    """Формулировка и служебные сегменты записи: маркер сегмента
    считается только на границе предложения («. » перед ним либо
    начало хвоста)."""
    hits: list[tuple[int, str]] = []
    for mk in _SEG_MARKERS:
        start = 0
        while True:
            i = rest.find(mk, start)
            if i < 0:
                break
            if i == 0 or rest[max(0, i - 2):i] == ". ":
                hits.append((i, mk))
            start = i + 1
    hits.sort()
    if not hits:
        return rest.strip(), []
    stmt = rest[:hits[0][0]].strip()
    segs = []
    for n, (i, mk) in enumerate(hits):
        j = hits[n + 1][0] if n + 1 < len(hits) else len(rest)
        segs.append((mk, rest[i + len(mk):j].strip()))
    return stmt, segs


def _parse_params(body: str) -> list | None:
    body = _strip_dot(body)
    out, pos = [], 0
    for mt in _param_re().finditer(body):
        if mt.start() != pos:
            return None
        pos = mt.end()
        p = {"name": mt.group("name"), "value": _num(mt.group("value")),
             "quote": mt.group("quote")}
        unit = mt.group("unit") or m.PARAMS.get(mt.group("name"))
        if unit:
            p["unit"] = unit
        out.append(p)
    if pos != len(body) or not out:
        return None
    return out


def _parse_criterion_segment(full: str, row: dict) -> bool:
    text = _strip_dot(full)
    for piece in text.split("; "):
        piece = piece.strip()
        if (mt := _CRIT_KIND_RE.match(piece)):
            row["kind"] = mt.group("k")
        elif (mt := _CRIT_RATE_RE.match(piece)):
            rate = row.setdefault("rate", {})
            rate["value"] = _num(mt.group("v"))
            if mt.group("per"):
                rate["per"] = mt.group("per").strip()
        elif (mt := _CRIT_RATES_RE.match(piece)):
            row.setdefault("rate", {})["rates"] = {
                "flight": _num(mt.group("f")), "work": _num(mt.group("w"))}
        elif (mt := _CRIT_VERSION_RE.match(piece)):
            row["version"] = mt.group("v").strip()
        elif (mt := _CRIT_DATE_RE.match(piece)):
            row["effective_from"] = mt.group("d").strip()
        else:
            return False
    if "rate" in row:
        # Как написано — сам сегмент документа (фрагмент этой же строки).
        row["rate"]["as_written"] = text
    return True


def parse_row_line(text: str, section: str) -> dict | None:
    """Запись формы из одной строки канонического текста; None — строка
    не является записью данного раздела (разбор fail-closed)."""
    mt = _ROW_RE.match(" ".join(str(text or "").split()))
    if not mt:
        return None
    rid, title, rest = mt.group("id"), mt.group("title").strip(), mt.group("rest")
    stmt, segs = _split_segments(rest)
    if not stmt:
        return None
    row: dict = {"id": rid, "title": title, "statement": stmt}
    crit_parts: list[str] = []
    for mk, body in segs:
        if mk == "Шаблон: ":
            tm = _TEMPLATE_RE.match(_strip_dot(body))
            if not tm or "template" in row:
                return None
            tpl = {"family": tm.group("family"), "category": tm.group("cat")}
            if tm.group("assumption"):
                tpl["assumption"] = tm.group("assumption").strip()
            if tm.group("quote"):
                tpl["quote"] = tm.group("quote")
            row["template"] = tpl
        elif mk == "Шаблона нет: ":
            gap = _strip_dot(body)
            if not gap or "gap" in row:
                return None
            row["template"] = None
            row["gap"] = gap
        elif mk == "Параметры: ":
            params = _parse_params(body)
            if params is None or "params" in row:
                return None
            row["params"] = params
        elif mk == "Штраф: ":
            pm = _PENALTY_RE.match(_strip_dot(body))
            if not pm or "penalty" in row:
                return None
            unit_ru = pm.group("unit").strip()
            unit = None if unit_ru.startswith("—") else unit_from_word(unit_ru)
            row["penalty"] = {"coefficient": int(pm.group("coef")),
                              "violation_unit": unit}
        elif mk == "Область: ":
            sm = _SCOPE_RE.match(_strip_dot(body))
            if not sm or "scope" in row:
                return None
            row["scope"] = {"pairing": sm.group("p").strip(),
                            "rostering": sm.group("r").strip()}
        elif mk == "Запись листа исключений.":
            if body:
                return None
            row["case"] = True
        elif mk == "Пометка: ":
            em = _EXCLUDED_RE.match(_strip_dot(body))
            if not em:
                return None
            row["excluded_until_data"] = {
                "question": (em.group("q") or "не указан").strip(),
                "revision": int(em.group("rev") or 1)}
        elif mk == "Вид записи: ":
            role = _ROLE_TEXT.get(_strip_dot(body))
            if not role:
                return None
            row["role"] = role
        elif mk == "Направление: ":
            dm = _DIRECTION_RE.match(_strip_dot(body))
            if not dm:
                return None
            row["direction"] = dm.group("d")
            if dm.group("m"):
                row["metric"] = dm.group("m")
            row["tier"] = int(dm.group("t"))
            if dm.group("tv"):
                row["tolerance"] = {"value": _num(dm.group("tv")),
                                    "unit": dm.group("tu")}
        elif mk in ("Машинный критерий: ", "Ставка ", "Ставки рангов: "):
            crit_parts.append(mk + body)
        elif mk == "Примечание: ":
            row["note"] = _strip_dot(body)
        else:                                              # pragma: no cover
            return None
    if crit_parts:
        if len(crit_parts) != 1 or not _parse_criterion_segment(crit_parts[0], row):
            return None
    # Состав по разделу — лишнее в разделе означает «не запись раздела».
    rule_keys = {"template", "gap", "params", "penalty", "scope", "case"}
    if section == "definitions":
        if "role" not in row or (rule_keys | {"direction", "kind", "rate"}) & set(row):
            return None
    elif section == "criteria":
        if "direction" not in row or (rule_keys | {"role"}) & set(row):
            return None
    else:
        if {"role", "direction", "kind", "rate"} & set(row):
            return None
        row.setdefault("template", None)
        if section != "soft" and "penalty" in row:
            pass                       # оставить — нарушение покажет validate_form
    return row


def _clean_clause(text: str) -> str:
    # Заморозка помечает жирный «**…**» — каноническому тексту жирный не
    # нужен; маркеры снимаются, содержимое не меняется.
    return " ".join(str(text or "").replace("**", "").split())


def read_form_text(entries: list[dict], currency: str | None = None) -> tuple[dict, dict]:
    """Форма из клауз заморозки (записи S1..Sn: sid, file, sha256,
    clauses[{anchor, text}]). Возвращает (форма, отчёт): отчёт несёт
    счётчики и перечень нераспознанных клауз — при ступени ИДАК-0 он
    обязан быть пуст."""
    form: dict = {
        "form_version": vf.FORM_VERSION, "customer": None,
        "currency": currency or vf.DEFAULT_CURRENCY,
        "sources": [{"sid": e["sid"], "file": e["file"], "sha256": e["sha256"]}
                    for e in entries],
        "common": {"definitions": [], "world": []},
        "pairing": {"hard": [], "soft": [], "criteria": []},
        "rostering": {"hard": [], "soft": [], "criteria": []},
        "withdrawn": [], "open_questions": [], "answered_questions": [],
    }
    report: dict = {"headers": 0, "headings": 0, "rows": 0,
                    "unrecognized": [], "by_section": {}}
    cur: tuple | None = None
    for e in entries:
        for c in e.get("clauses") or []:
            t = _clean_clause(c.get("text"))
            if not t:
                continue
            hm = _HEADER_CUSTOMER.match(t)
            if hm and form["customer"] is None:
                form["customer"] = hm.group("c").strip()
                report["headers"] += 1
                continue
            hm = _HEADER_CURRENCY.match(t)
            if hm:
                form["currency"] = hm.group("cur").strip()
                form["registry_rev"] = hm.group("rev")
                report["headers"] += 1
                continue
            if t == vf.COMPOSITION_TEXT:
                report["headers"] += 1
                continue
            if t in _HEADINGS:
                cur = _HEADINGS[t]
                report["headings"] += 1
                continue
            row = parse_row_line(t, cur[1]) if cur else None
            if row is None:
                report["unrecognized"].append(
                    {"sid": e["sid"], "anchor": c.get("anchor"), "text": t[:200]})
                continue
            row["sources"] = [{"sid": e["sid"], "anchor": c.get("anchor")}]
            part, sec = cur
            target = form["common"][sec] if part == "common" else form[part][sec]
            target.append(row)
            report["rows"] += 1
            report["by_section"][f"{part}/{sec}"] = \
                report["by_section"].get(f"{part}/{sec}", 0) + 1
    form["customer"] = form["customer"] or "—"
    form.setdefault("registry_rev", m.registry_fingerprint())
    return form, report


def model_from_form_text(form: dict, note: str | None = None) -> dict:
    """Машинная модель из формы, прочитанной кодом: обратное построение
    формы + машинные поля, которые извлечение ставит кодом (ярлыки из
    посадки, семьи мягких механик)."""
    model = vf.from_form(copy.deepcopy(form))
    model["meta"] = {
        "customer": form.get("customer"), "mode": 1,
        "cdpm_version": getattr(m, "CDPM_VERSION", "form-text-1"),
        "fit_contract": True,
        "note": note or ("прочитано канонической текстовой формой ВМП "
                         "(cdpm/form_text): вопросов анкеты нет"),
    }
    derive = getattr(m, "derive_all_rule_traits", None)
    if derive:
        derive(model, "чтение канонической формы")
    assign = getattr(m, "assign_soft_families", None)
    if assign:
        assign(model)
    return model
