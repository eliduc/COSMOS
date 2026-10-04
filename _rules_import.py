"""Рамка импорта правил авиакомпаний из внешних форматов (2026-07-24).

Единая цель всех приёмников — Пакет правил COSMOS (Ярус 2а: правило как
запись данных с происхождением; совместимо с webapp/rule_packs.py).
Детерминированный код без обращений к моделям и сети.

Реестр форматов:
  solver-format5   — файл оптимизатора вендора (parameters с шаблонами
                     @type + constraintWeightOverrides) — РЕАЛИЗОВАН;
  meridian-limits  — выгрузка «Меридиана» (format:1): числовые лимиты на
                     сотрудниках и переопределения отдыха — РЕАЛИЗОВАН;
  cosmos-rules-json— нейтральный обменный формат COSMOS (см. ниже) —
                     РЕАЛИЗОВАН;
  jeppesen-rave    — ПОДМНОЖЕСТВО языка Rave (Jeppesen), 2026-07-24:
                     детерминированный разбор ядра по четырём открытым
                     источникам (диссертация Чалмерса с дословным
                     синтаксисом; перечень концептов курса Jeppesen;
                     словарь лексики стороннего расширения редактора;
                     дословный фрагмент кода в диссертации Университета
                     Сабанджи, язык ARUS). Разбираются: module, правила
                     rule...end с условием valid, параметры со
                     значением/границами/пояснением, сравнения в теле
                     правил. ВСЁ неразобранное честно считается и
                     показывается — семантика не угадывается; полная
                     совместимость с закрытым языком не заявляется;
  norm-table       — параметрическая таблица норм (CSV или JSON):
                     «имя ограничения / значение / единица / окно» —
                     самый быстрый приёмник; явные канонические имена
                     ложатся на шаблоны Яруса 2б и параметры Яруса 1;
  roster-xml       — XML-контейнеры смежных областей (второй конкурс
                     графиков медсестёр, Staff Roster Solutions):
                     инвентаризация числовых ограничений контрактов в
                     записи «ресурса» и структурные записи;
  sabre-altitude, netline-crew, aims — закрытые форматы, ожидают
                     образцов (разбор по догадкам не выполняется).

Нейтральный обменный формат cosmos-rules-json (наш вклад в отсутствие
отраслевого стандарта) — JSON-объект:
  {"format": "cosmos-rules-json", "version": 1,
   "source": {"document": "...", "section": "...", "url": "..."},
   "rules": [{"code": "...", "title": "...", "statement": "...",
              "template": "<имя шаблона или null>",
              "parameters": {...}, "weight": "OFF|LOW|STD|HIGH|...",
              "tier1": {"parameter": "...", "value": N} | null}]}
Любая сторона может выгрузить правила в этом виде — импорт без кода.

Usage:
    python _rules_import.py <файл> --format solver-format5|meridian-limits|
        cosmos-rules-json|auto [--country XX] [--out pack.json]
    (--country обязателен для форматов, не несущих страну юрисдикции
    в файле: meridian-limits, solver-format5)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _solver_to_payload import (_COUNTRY_RE, _pt_minutes,    # noqa: E402
                                build_rule_pack)
import _rule_templates as templates                          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class RulesImportError(ValueError):
    """Отказ приёмника — всегда с объяснением."""


# ------------------------------------------------------------- приёмники

def import_solver_format5(data: dict, source_name: str) -> dict:
    """Файл оптимизатора вендора (format:5) -> Пакет правил."""
    if not isinstance(data, dict) or "parameters" not in data:
        raise RulesImportError(
            "solver-format5: во входе нет блока 'parameters'")
    return build_rule_pack(data, source_name)


def import_meridian_limits(data: dict, source_name: str) -> dict:
    """Выгрузка «Меридиана» (format:1): правила, которые несёт сам файл —
    числовые лимиты на сотрудниках (минуты) и переопределения отдыха.
    Значения агрегируются по различию: одинаковые у всех = правило
    авиакомпании; различающиеся = индивидуальные (структурная запись)."""
    if not isinstance(data, dict) or not isinstance(data.get("employees"),
                                                    list):
        raise RulesImportError(
            "meridian-limits: во входе нет списка 'employees'")
    src = {"document": f"Выгрузка «Меридиана» ({source_name})",
           "section": "employees[].workTimeLimit/flightTimeLimit"}
    rules: List[dict] = []
    for key, title, tier1 in (
            ("workTimeLimit", "Лимит рабочего времени за план", None),
            ("flightTimeLimit", "Лимит налёта за план",
             "max_monthly_block_min")):
        vals = sorted({e.get(key) for e in data["employees"]
                       if isinstance(e, dict)
                       and isinstance(e.get(key), (int, float))
                       and not isinstance(e.get(key), bool)})
        if not vals:
            continue
        uniform = len(vals) == 1
        # ревью 2026-07-24: дробный лимит раньше молча усекался int(),
        # а лимит за пределом Int32 рождал правило, которое слой пакета
        # отбрасывал; оба случая теперь честно остаются структурными
        v0 = vals[0]
        executable = (uniform and tier1 is not None
                      and float(v0) == int(v0)
                      and 0 < int(v0) <= 2_147_483_647)
        stmt = (f"{title}: {v0:g} мин на каждого сотрудника."
                if uniform else
                f"{title}: индивидуальные значения "
                f"({vals[0]:g}..{vals[-1]:g} мин).")
        rule: dict = {"title": title, "statement": stmt, "source": src}
        if executable:
            rule["constraint"] = {"kind": "parameter",
                                  "parameter": tier1,
                                  "value": int(v0)}
        else:
            rule["constraint"] = {
                "kind": "note",
                "note": json.dumps({"vendor_field": key,
                                    "distinct_values": len(vals),
                                    "min": vals[0], "max": vals[-1]},
                                   ensure_ascii=False)}
        rules.append(rule)
    n_rest = sum(1 for d in (data.get("duties") or [])
                 if isinstance(d, dict)
                 and isinstance(d.get("restOverride"), (int, float))
                 and d.get("restOverride"))
    if n_rest:
        rules.append({
            "title": "Точечные переопределения отдыха",
            "statement": f"Выгрузка несёт {n_rest} переопределений "
                         f"отдыха на нарядах (restOverride) — "
                         f"индивидуальные решения планировщика вендора.",
            "source": src,
            "constraint": {"kind": "note",
                           "note": json.dumps({"vendor_field":
                                               "duties[].restOverride",
                                               "count": n_rest},
                                              ensure_ascii=False)}})
    if not rules:
        raise RulesImportError(
            "meridian-limits: во входе не найдено ни одного правила")
    # Файл format:1 юрисдикцию не несёт — страну обязан подать оператор;
    # обязательность держит диспетчер import_rules*/CLI (пустая страна
    # означала бы «пакет действует на все наборы»).
    return {"name": f"Правила из выгрузки «Меридиана» ({source_name[:40]})",
            "country": "",
            "notes": "Автоимпорт лимитов из format:1; детерминированно.",
            "rules": rules}


def import_cosmos_rules_json(data: dict, source_name: str) -> dict:
    """Нейтральный обменный формат COSMOS -> Пакет правил."""
    if not isinstance(data, dict) \
            or data.get("format") != "cosmos-rules-json":
        raise RulesImportError(
            "cosmos-rules-json: во входе нет поля "
            "format='cosmos-rules-json'")
    src_default = data.get("source") if isinstance(data.get("source"),
                                                   dict) else {}
    rules: List[dict] = []
    for i, r in enumerate(data.get("rules") or []):
        if not isinstance(r, dict):
            raise RulesImportError(f"rules[{i}]: не объект")
        title = str(r.get("title") or r.get("code") or "").strip()
        stmt = str(r.get("statement") or "").strip()
        if not title or not stmt:
            raise RulesImportError(
                f"rules[{i}]: обязательны title и statement")
        src = r.get("source") if isinstance(r.get("source"), dict) \
            else src_default
        if not str((src or {}).get("document") or "").strip():
            raise RulesImportError(
                f"rules[{i}]: у правила должен быть источник (document)")
        t1 = r.get("tier1")
        if isinstance(t1, dict) and t1.get("parameter"):
            constraint = {"kind": "parameter",
                          "parameter": str(t1["parameter"]),
                          "value": t1.get("value")}
        else:
            constraint = {"kind": "note",
                          "note": json.dumps(
                              {"code": r.get("code"),
                               "template": r.get("template"),
                               "parameters": r.get("parameters"),
                               "weight": r.get("weight")},
                              ensure_ascii=False, sort_keys=True)[:2000]}
        rules.append({"title": title[:120], "statement": stmt[:500],
                      "source": src, "constraint": constraint})
    if not rules:
        raise RulesImportError("cosmos-rules-json: список rules пуст")
    return {"name": str(data.get("name") or f"Импорт ({source_name[:40]})"),
            "country": str(data.get("country") or ""),
            "notes": str(data.get("notes") or "Импорт cosmos-rules-json."),
            "rules": rules}


# ------------------------------------------- приёмник подмножества Rave

_RAVE_MODULE_STMT = re.compile(r"^\s*module\s+([\w.]+)\s*")
_RAVE_RULE_HEAD = re.compile(
    r"^\s*rule\s*(?:\(\s*(on|off)\s*\)\s*)?(\w+)\s*=\s*(.*)$", re.S)
_RAVE_PARAM_STMT = re.compile(
    r"^\s*%(\w+)%\s*=\s*parameter\b(.*)$", re.S)
_RAVE_VAR_STMT = re.compile(r"^\s*%(\w+)%\s*=")
_RAVE_END = re.compile(r"^\s*end\b\s*")
_RAVE_VALID = re.compile(r"^valid\b\s*")
_RAVE_REMARK = re.compile(r'remark\s+"([^"]*)"')
_RAVE_TIME = re.compile(r"^(\d{1,4}):([0-5]\d)$")
_RAVE_CMP = re.compile(
    r"([^;<>=]+?)\s*(<=|>=|<>|=|<|>)\s*([^;<>=]+)")
_RAVE_MAX_SNIPPET = 160


def _rave_strip_comments(text: str) -> str:
    """Удаление комментариев с учётом строковых литералов: `//`, `/*…*/`
    и `#` в начале строки НЕ трогаются внутри двойных кавычек (ревью
    2026-07-24: адрес в remark обрывал разбор всего файла)."""
    out: List[str] = []
    i, n = 0, len(text)
    in_string = False
    at_line_start = True
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == '"':
                in_string = False
            elif ch == "\n":       # незакрытая строка не съедает файл
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            at_line_start = False
            continue
        if ch == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
            continue
        if ch == "#" and at_line_start:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(ch)
        at_line_start = (ch == "\n") or (at_line_start and ch in " \t")
        i += 1
    return "".join(out)


def _rave_split_statements(text: str) -> List[str]:
    """Разрез по `;` вне строковых литералов."""
    stmts: List[str] = []
    buf: List[str] = []
    in_string = False
    for ch in text:
        if in_string:
            buf.append(ch)
            if ch == '"' or ch == "\n":
                in_string = False
            continue
        if ch == '"':
            in_string = True
            buf.append(ch)
            continue
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        stmts.append("".join(buf))
    return stmts


def _rave_scalar(tok: str) -> dict:
    """Значение параметра Rave: число, время Ч:ММ или сырой текст."""
    tok = tok.strip()
    m = _RAVE_TIME.match(tok)
    if m:
        return {"raw": tok,
                "minutes": int(m.group(1)) * 60 + int(m.group(2))}
    try:
        return {"raw": tok, "number": float(tok)}
    except ValueError:
        return {"raw": tok[:60]}


def _rave_param_body(body: str) -> dict:
    """'7:30 minvalue 5:00 maxvalue 12:00 remark "..."' -> поля."""
    out: dict = {}
    remark = _RAVE_REMARK.search(body)
    if remark:
        out["remark"] = remark.group(1)[:200]
        body = _RAVE_REMARK.sub(" ", body)
    for key in ("minvalue", "maxvalue"):
        m = re.search(key + r"\s+(\S+)", body)
        if m:
            out[key] = _rave_scalar(m.group(1))
            body = body.replace(m.group(0), " ")
    first = body.split()
    if first:
        out["value"] = _rave_scalar(first[0])
    return out


def _rave_rule_record(state: str, name: str, body_stmts: List[str],
                      src: dict) -> dict:
    """Запись правила из накопленных операторов его тела."""
    remark = None
    valid = ""
    comparisons = []
    for raw_stmt in body_stmts:
        s = raw_stmt.strip()
        if not s:
            continue
        r = _RAVE_REMARK.search(s)
        if r:
            remark = r.group(1)
            s = _RAVE_REMARK.sub(" ", s).strip()
            if not s:
                continue
        v = _RAVE_VALID.match(s)
        if v:
            valid = s[v.end():].strip()
            continue
        if len(comparisons) < 6:
            c = _RAVE_CMP.search(s)
            if c:
                comparisons.append({
                    "lhs": c.group(1).strip()[:80], "op": c.group(2),
                    "rhs": c.group(3).strip()[:80]})
    stmt = (remark
            or "; ".join(f"{c['lhs']} {c['op']} {c['rhs']}"
                         for c in comparisons)
            or f"Правило {name} (условие: {valid[:120] or '—'})")
    return {
        "title": f"Правило Rave {name}"[:120],
        "statement": stmt[:500], "source": src,
        "constraint": {"kind": "note", "note": json.dumps(
            {"rave": "rule", "name": name, "state": state,
             "valid": valid[:200], "comparisons": comparisons},
            ensure_ascii=False)[:600]}}


def import_rave_subset(text: str, source_name: str) -> dict:
    """Подмножество языка Rave -> Пакет правил (всё структурно, Ярус 2а:
    семантика правил авиакомпании НЕ угадывается — имена параметров
    специфичны для каждого заказчика, автоматический перенос в
    исполняемый Ярус 1 здесь был бы догадкой).

    Ревью 2026-07-24: разбор переписан со «глобальных» регулярных
    выражений на строко-осознанный сканер — комментарии и `;` внутри
    строковых литералов не рвут операторы, а терминатором правила
    служит ТОЛЬКО отдельный оператор `end` (атрибуты вида `trip.end`
    и блоки `if…end` внутри выражений тело правила не обрывают)."""
    stripped = _rave_strip_comments(text)
    src = {"document": f"Файл Rave ({source_name})",
           "section": "разбор подмножества языка"}
    etab_refs = len(re.findall(r"\.etab\b", stripped))

    modules: List[str] = []
    param_records: List[dict] = []
    rule_records: List[dict] = []
    var_names: List[str] = []
    leftovers: List[str] = []

    in_rule: Optional[tuple] = None      # (state, name, [операторы])
    pending = _rave_split_statements(stripped)
    idx = 0
    while idx < len(pending):
        stmt = pending[idx]
        idx += 1
        s = stmt.strip()
        if not s:
            continue
        m = _RAVE_MODULE_STMT.match(stmt)
        if m and in_rule is None:
            modules.append(m.group(1))
            rest = stmt[m.end():]
            if rest.strip():
                pending.insert(idx, rest)
            continue
        if in_rule is not None:
            e = _RAVE_END.match(stmt)
            if e:
                state, name, body = in_rule
                rule_records.append(
                    _rave_rule_record(state, name, body, src))
                in_rule = None
                rest = stmt[e.end():]
                if rest.strip():
                    pending.insert(idx, rest)
                continue
            in_rule[2].append(stmt)
            continue
        m = _RAVE_RULE_HEAD.match(stmt)
        if m:
            in_rule = (m.group(1) or "on", m.group(2), [])
            if m.group(3).strip():
                in_rule[2].append(m.group(3))
            continue
        m = _RAVE_PARAM_STMT.match(stmt)
        if m:
            name, body = m.group(1), _rave_param_body(m.group(2))
            p_stmt = body.get("remark") or (
                f"Параметр %{name}% = "
                f"{body.get('value', {}).get('raw', '?')}"
                + (f", границы {body['minvalue']['raw']}.."
                   f"{body['maxvalue']['raw']}"
                   if "minvalue" in body and "maxvalue" in body
                   else "") + ".")
            param_records.append({
                "title": f"Параметр Rave %{name}%"[:120],
                "statement": p_stmt[:500], "source": src,
                "constraint": {"kind": "note", "note": json.dumps(
                    {"rave": "parameter", "name": name, **body},
                    ensure_ascii=False)[:600]}})
            continue
        m = _RAVE_VAR_STMT.match(stmt)
        if m:
            var_names.append(m.group(1))
            continue
        leftovers.append(s)

    if in_rule is not None:
        # незавершённое правило — честно в неразобранное, не в разобранное
        state, name, body = in_rule
        leftovers.append(f"rule {name} = … (нет завершающего end)")
        leftovers += [b.strip() for b in body if b.strip()]

    rules = param_records + rule_records
    if not rules:
        raise RulesImportError(
            "jeppesen-rave: во входе не найдено ни правил (rule...end), "
            "ни параметров (%имя% = parameter ...) — это не файл Rave "
            "или конструкции вне поддерживаемого подмножества")

    summary = {"modules": modules[:10], "rules": len(rule_records),
               "parameters": len(param_records),
               "variables": len(var_names),
               "external_table_refs": etab_refs,
               "unparsed_statements": len(leftovers)}
    head = [{
        "title": "Разбор файла Rave: состав и границы",
        "statement": (
            f"Разобрано подмножество языка: правил {len(rule_records)}, "
            f"параметров {len(param_records)}, переменных вне правил "
            f"{len(var_names)}, ссылок на внешние таблицы {etab_refs}. "
            + (f"Неразобранных конструкций: {len(leftovers)} — их "
               f"семантика не угадывается, образцы в отдельной записи."
               if leftovers else "Неразобранных конструкций нет.")),
        "source": src,
        "constraint": {"kind": "note", "note": json.dumps(
            summary, ensure_ascii=False)[:600]}}]
    if leftovers:
        head.append({
            "title": "Неразобранные конструкции (образцы)",
            "statement": " | ".join(
                s[:_RAVE_MAX_SNIPPET] for s in leftovers[:3])[:500],
            "source": src,
            "constraint": {"kind": "note", "note": json.dumps(
                {"count": len(leftovers),
                 "samples": [s[:_RAVE_MAX_SNIPPET]
                             for s in leftovers[:5]]},
                ensure_ascii=False)[:600]}})
    rules = head + rules
    if len(rules) > 195:
        dropped = len(rules) - 195
        rules = rules[:195]
        rules.append({
            "title": "Урезание по вместимости пакета",
            "statement": f"Показаны первые 195 записей; ещё {dropped} "
                         f"не вошли (предел пакета — 200 правил). "
                         f"Разделите файл на модули.",
            "source": src,
            "constraint": {"kind": "note", "note": json.dumps(
                {"dropped": dropped}, ensure_ascii=False)}})
    return {"name": f"Rave: {source_name[:100]}",
            "country": "",
            "notes": "Импорт подмножества Rave; детерминированно, "
                     "семантика не угадывается.",
            "rules": rules}


# --------------------------------------- приёмник таблиц норм (CSV/JSON)

_NORM_COLS = {
    "name": {"name", "имя", "constraint", "ограничение", "rule",
             "правило", "norm", "норма"},
    "value": {"value", "значение"},
    "unit": {"unit", "единица", "ед", "units"},
    "window": {"window", "окно", "period", "период", "scope"},
    "parameter": {"parameter", "параметр", "cosmos_parameter",
                  "template", "шаблон"},
    "description": {"description", "описание", "примечание", "comment",
                    "note"},
}


def _norm_header_map(header: List[str]) -> Optional[dict]:
    out = {}
    for i, h in enumerate(header):
        key = str(h or "").strip().lower().lstrip("﻿")
        for canon, syns in _NORM_COLS.items():
            if key in syns and canon not in out:
                out[canon] = i
    return out if "name" in out and "value" in out else None


def _norm_rows_from_csv(text: str) -> List[dict]:
    first = text.lstrip("﻿\n\r ").splitlines()[0] if text.strip() \
        else ""
    delim = max(";,\t", key=first.count)
    reader = csv.reader(io.StringIO(text.lstrip("﻿")),
                        delimiter=delim)
    try:
        table = [row for row in reader if any(c.strip() for c in row)]
    except csv.Error as exc:
        # ревью 2026-07-24: поле сверх лимита csv роняло импорт кодом 500
        raise RulesImportError(f"norm-table: CSV не разобран: {exc}")
    if not table:
        raise RulesImportError("norm-table: файл пуст")
    colmap = _norm_header_map(table[0])
    if colmap is None:
        raise RulesImportError(
            "norm-table: в заголовке не найдены обязательные колонки "
            "«имя/name» и «значение/value»")
    rows = []
    for row in table[1:]:
        def cell(c):
            i = colmap.get(c)
            return row[i].strip() if i is not None and i < len(row) else ""
        rows.append({k: cell(k) for k in _NORM_COLS})
    return rows


def _norm_rows_from_json(data) -> List[dict]:
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RulesImportError("norm-table: ожидается список rows")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or "name" not in r or "value" not in r:
            raise RulesImportError(
                f"norm-table: rows[{i}] должен быть объектом с name и "
                f"value")
        # ревью 2026-07-24: `or ""` схлопывал числовой 0 в пустую строку
        out.append({k: ("" if r.get(k) is None else str(r.get(k))).strip()
                    for k in _NORM_COLS})
    return out


def import_norm_table(text: str, source_name: str) -> dict:
    """Параметрическая таблица норм -> Пакет правил. Канонические имена
    (колонка parameter или совпадение name с идентификатором шаблона)
    ложатся на шаблоны Яруса 2б; при совместимом окне и наличии
    параметра Яруса 1 — сразу исполняемое правило."""
    stripped = text.lstrip("﻿\r\n ")
    if stripped[:1] in "{[":
        try:
            rows = _norm_rows_from_json(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise RulesImportError(f"norm-table: JSON не разобран: {exc}")
    else:
        rows = _norm_rows_from_csv(text)
    if not rows:
        raise RulesImportError("norm-table: в таблице нет строк данных")
    if len(rows) > 195:
        raise RulesImportError(
            f"norm-table: строк {len(rows)}, предел пакета — 200 правил; "
            f"разделите таблицу")
    src = {"document": f"Таблица норм ({source_name})",
           "section": "имя/значение/единица/окно"}
    rules: List[dict] = []
    problems: List[str] = []
    for i, r in enumerate(rows):
        name = r["name"]
        if not name:
            problems.append(f"строка {i + 2}: пустое имя")
            continue
        window = templates.normalize_window(r["window"])
        if window is None:
            problems.append(
                f"строка {i + 2} ({name}): окно «{r['window']}» не "
                f"распознано")
            continue
        tid = templates.canonical_template(r["parameter"]) \
            or templates.canonical_template(name)
        desc = r["description"]
        title = (templates.TEMPLATES[tid]["title"] if tid else name)[:120]
        stmt = (desc or f"{name}: {r['value']} {r['unit']}".strip()
                + (f" за {r['window']}" if r["window"] else "")
                + ".")[:500]
        if tid:
            t = templates.TEMPLATES[tid]
            if t["unit"] == "count":
                # ревью 2026-07-24: чужая единица на счётном шаблоне
                # («часов» у максимума посадок) — противоречие, не молчим
                if not templates.is_count_unit(r["unit"]):
                    problems.append(
                        f"строка {i + 2} ({name}): единица «{r['unit']}» "
                        f"противоречит счётному шаблону")
                    continue
                converted = templates.value_to_count(r["value"])
            else:
                # ревью 2026-07-24: «голое число = минуты» было молчаливым
                # допущением; теперь единица обязательна (или запись Ч:ММ)
                if not str(r["unit"]).strip() \
                        and not templates.looks_like_time(r["value"]):
                    problems.append(
                        f"строка {i + 2} ({name}): единица не указана — "
                        f"задайте её явно (мин/ч/дн) или запишите "
                        f"значение как Ч:ММ")
                    continue
                converted = templates.value_to_minutes(
                    r["value"], r["unit"] or "min")
            if converted is None:
                problems.append(
                    f"строка {i + 2} ({name}): значение "
                    f"«{r['value']} {r['unit']}» не преобразуется в "
                    f"{t['unit']}")
                continue
            if converted <= 0:
                # ревью 2026-07-24: Ярус 1 требует положительное значение;
                # нулевое правило раньше молча выпадало на слое пакета
                problems.append(
                    f"строка {i + 2} ({name}): значение должно быть "
                    f"положительным")
                continue
            window_ok = window in ("", t["level"])
            if t["tier1"] and window_ok:
                constraint = {"kind": "parameter",
                              "parameter": t["tier1"],
                              "value": converted}
            else:
                constraint = templates.resource_constraint(tid, converted)
                if window and window != t["level"]:
                    constraint["level"] = window
        else:
            constraint = {"kind": "note", "note": json.dumps(
                {"name": name, "value": r["value"], "unit": r["unit"],
                 "window": r["window"], "description": desc},
                ensure_ascii=False, sort_keys=True)[:600]}
        rules.append({"title": title, "statement": stmt, "source": src,
                      "constraint": constraint})
    if not rules:
        raise RulesImportError(
            "norm-table: ни одна строка не дала правила"
            + (f" ({'; '.join(problems[:3])})" if problems else ""))
    if problems:
        rules.insert(0, {
            "title": "Строки, не принятые приёмником",
            "statement": "; ".join(problems[:4])[:500],
            "source": src,
            "constraint": {"kind": "note", "note": json.dumps(
                {"problems": problems[:10]}, ensure_ascii=False)[:600]}})
    return {"name": f"Таблица норм: {source_name[:90]}",
            "country": "",
            "notes": "Импорт параметрической таблицы норм; "
                     "детерминированно.",
            "rules": rules}


# --------------------------- приёмник XML-контейнеров смежных областей

_XML_VOCAB = re.compile(
    r"(?i)max|min|rest|consecutive|assignment|working|weekend|shift|"
    r"hour|load|holiday|off|cover")
_XML_KNOWN_ROOTS = {
    "SchedulingPeriod": "Staff Roster Solutions / первый конкурс "
                        "графиков медсестёр",
    "Scenario": "второй конкурс графиков медсестёр (INRC-II)",
    "SchedulingHorizon": "Staff Roster Solutions",
}


def _xml_tag(el) -> str:
    return el.tag.split("}")[-1]


_XML_FORBIDDEN = re.compile(r"(?i)<!\s*(doctype|entity)")


def _xml_walk(root) -> tuple:
    """Итеративный обход (ревью 2026-07-24: рекурсия падала на глубокой
    вложенности). Возвращает (found, skipped): подходящие по словарю
    элементы с целым значением 0..Int32 и ЧЕСТНЫЙ список отброшенных
    (нецелое, отрицательное, за Int32, не-число)."""
    found: List[dict] = []
    skipped: List[dict] = []
    stack = [(root, "")]
    while stack:
        el, path = stack.pop()
        tag = _xml_tag(el)
        ident = el.get("ID") or el.get("Id") or el.get("id")
        here = f"{path}/{tag}" + (f"[{ident}]" if ident else "")
        text_val = (el.text or "").strip()
        if text_val and _XML_VOCAB.search(tag):
            try:
                num = float(text_val)
            except ValueError:
                num = None
            if num is not None and math.isfinite(num) \
                    and num == int(num) and 0 <= num < 2**31:
                found.append({"path": here, "tag": tag,
                              "value": int(num)})
            else:
                skipped.append({"path": here, "tag": tag,
                                "raw": text_val[:60]})
        stack.extend((child, here) for child in reversed(list(el)))
    return found, skipped


def _xml_constraint(tag: str, value: int, path: str) -> dict:
    """Ограничение по тегу БЕЗ угадывания семантики (ревью 2026-07-24):
    единица берётся только из явного слова в теге (Minutes/Hours/Days);
    часы честно переводятся в минуты; теги со словом Time без единицы
    остаются структурной записью — их масштаб не проверяем."""
    low = tag.lower()
    is_max = low.startswith("max")
    is_min = low.startswith("min")
    if not (is_max or is_min):
        return {"kind": "note", "note": json.dumps(
            {"path": path, "tag": tag, "value": value},
            ensure_ascii=False)[:600]}
    if "minute" in low:
        unit, val = "minutes", value
    elif "hour" in low:
        unit, val = "minutes", value * 60
        if val >= 2**31:
            return {"kind": "note", "note": json.dumps(
                {"path": path, "tag": tag, "value": value,
                 "reason": "часы за пределом Int32 после конверсии"},
                ensure_ascii=False)[:600]}
    elif "day" in low:
        unit, val = "days", value
    elif "time" in low:
        # масштаб значения не объявлен тегом — не угадываем
        return {"kind": "note", "note": json.dumps(
            {"path": path, "tag": tag, "value": value,
             "reason": "единица времени тегом не объявлена"},
            ensure_ascii=False)[:600]}
    else:
        unit, val = "count", value
    level = ("roster" if "weekend" in low
             else "week" if "week" in low
             else "day" if "day" in low else "roster")
    return {
        "kind": "resource", "resource": tag[:120],
        "window": ({"min": None, "max": val} if is_max
                   else {"min": val, "max": None}),
        "unit": unit, "level": level,
        "extension": (f"перенос из смежной области как есть; "
                      f"исходный путь {path}")[:300]}


def import_roster_xml(text: str, source_name: str) -> dict:
    """XML-контейнер смежной области -> инвентаризация числовых
    ограничений. Записи Max*/Min* становятся «ресурсами» (Ярус 2б) с
    исходным путём в функции продления; перенос в исполняемый Ярус 1 не
    выполняется — семантика смежной области (смены, дни) не совпадает с
    авиационной, и приёмник этого честно не скрывает."""
    # страж по ВСЕМУ тексту (ревью 2026-07-24: длинный комментарий перед
    # DOCTYPE обходил проверку первых 4096 байт)
    if _XML_FORBIDDEN.search(text):
        raise RulesImportError(
            "roster-xml: входы с DOCTYPE/ENTITY не принимаются "
            "(защита от раздувания сущностей)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RulesImportError(f"roster-xml: XML не разобран: {exc}")
    root_tag = _xml_tag(root)
    container = _XML_KNOWN_ROOTS.get(root_tag)
    found, skipped = _xml_walk(root)
    if not found and not skipped:
        raise RulesImportError(
            f"roster-xml: в контейнере <{root_tag}> не найдено числовых "
            f"ограничений со словарём Max/Min/Rest/Shift/...")
    src = {"document": f"XML-контейнер ({source_name})",
           "section": f"корень <{root_tag}>"
                      + (f" — {container}" if container else "")}
    container_label = container or ("общий XML (структура не из "
                                    "известного перечня)")
    rules: List[dict] = [{
        "title": f"Контейнер <{root_tag}>: инвентаризация",
        "statement": (f"Распознан контейнер: {container_label}. "
                      f"Числовых ограничений: {len(found)}."
                      + (f" Не принято значений: {len(skipped)} "
                         f"(нецелые, отрицательные или вне диапазона) — "
                         f"образцы в отдельной записи." if skipped
                         else "")),
        "source": src,
        "constraint": {"kind": "note", "note": json.dumps(
            {"root": root_tag, "known": bool(container),
             "numeric_constraints": len(found),
             "skipped_values": len(skipped)},
            ensure_ascii=False)}}]
    if skipped:
        rules.append({
            "title": "Не принятые значения (образцы)",
            "statement": " | ".join(
                f"{s['tag']}={s['raw']}" for s in skipped[:4])[:500],
            "source": src,
            "constraint": {"kind": "note", "note": json.dumps(
                {"count": len(skipped), "samples": skipped[:5]},
                ensure_ascii=False)[:600]}})
    dropped = 0
    for f in found:
        if len(rules) >= 195:
            dropped += 1
            continue
        stmt = f"{f['tag']} = {f['value']} (путь {f['path']})."[:500]
        rules.append({"title": f["tag"][:120], "statement": stmt,
                      "source": src,
                      "constraint": _xml_constraint(
                          f["tag"], f["value"], f["path"])})
    if dropped:
        rules.append({
            "title": "Урезание по вместимости пакета",
            "statement": f"Ещё {dropped} ограничений не вошли (предел "
                         f"пакета — 200 правил).",
            "source": src,
            "constraint": {"kind": "note", "note": json.dumps(
                {"dropped": dropped}, ensure_ascii=False)}})
    return {"name": f"XML-контейнер: {source_name[:90]}",
            "country": "",
            "notes": "Инвентаризация ограничений смежной области; "
                     "детерминированно, без переноса семантики.",
            "rules": rules}


def _pending(vendor: str, hint: str) -> Callable[[dict, str], dict]:
    def _fail(data: dict, source_name: str) -> dict:
        raise RulesImportError(
            f"{vendor}: формат закрытый, публичной спецификации нет — "
            f"приёмник ожидает образцов файлов и документации. {hint} "
            f"Разбор по догадкам не выполняется намеренно.")
    return _fail


FORMATS: Dict[str, Callable[[dict, str], dict]] = {
    "solver-format5": import_solver_format5,
    "meridian-limits": import_meridian_limits,
    "cosmos-rules-json": import_cosmos_rules_json,
    "sabre-altitude": _pending(
        "sabre-altitude", "Нужны: выгрузка конфигурации правил Altitude."),
    "netline-crew": _pending(
        "netline-crew", "Нужны: выгрузка правил NetLine/Crew "
        "(Lufthansa Systems)."),
    "aims": _pending("aims", "Нужны: выгрузка правил AIMS."),
}

# Текстовые приёмники (2026-07-24): вход — сырой текст, не JSON-объект.
TEXT_HANDLERS: Dict[str, Callable[[str, str], dict]] = {
    "jeppesen-rave": import_rave_subset,
    "norm-table": import_norm_table,
    "roster-xml": import_roster_xml,
}

ALL_FORMATS = tuple(sorted(set(FORMATS) | set(TEXT_HANDLERS)))

# Форматы, чьи файлы НЕ несут страну юрисдикции: её обязан явно подать
# оператор (поле country запроса / --country CLI). Пустая страна в
# пакете означает «действует на все наборы» (webapp/rule_packs.
# pack_applies_to) — тихая замена значения расширила бы область
# действия пакета на чужие юрисдикции.
FORMATS_REQUIRE_COUNTRY = {"meridian-limits", "solver-format5"}


def _apply_country(pack: dict, fmt: str, country: Optional[str]) -> dict:
    """Штамп страны юрисдикции на готовый пакет — значение только из
    данных: из файла (cosmos-rules-json) либо от оператора. Кривое или
    противоречащее файлу значение — отказ; отсутствие для форматов из
    FORMATS_REQUIRE_COUNTRY — отказ. _COUNTRY_RE — копия
    webapp/registry.COUNTRY_RE (равенство держит тест-страж)."""
    file_country = str(pack.get("country") or "").strip().upper()
    if country is not None:
        c = str(country).strip().upper()
        if not _COUNTRY_RE.match(c):
            raise RulesImportError(
                f"страна юрисдикции {country!r} не принята: ожидается "
                f"INTL или код ISO-3166 из двух заглавных букв")
        if file_country and file_country != c:
            raise RulesImportError(
                f"{fmt}: страна в файле ({file_country}) противоречит "
                f"заданной ({c}); совпадение — норма, расхождение — "
                f"ошибка данных или запроса")
        pack["country"] = c
        return pack
    if not file_country and fmt in FORMATS_REQUIRE_COUNTRY:
        raise RulesImportError(
            f"формат {fmt} не несёт страну юрисдикции в файле; укажите "
            f"country (поле запроса) или --country (CLI) — без неё "
            f"пакет действовал бы не в той юрисдикции")
    return pack


def detect_format(data: dict) -> Optional[str]:
    """Автоопределение по строению JSON-входа (без угадывания)."""
    if not isinstance(data, dict):
        return None
    if data.get("format") == "cosmos-rules-json":
        return "cosmos-rules-json"
    if data.get("format") == "norm-table":
        return "norm-table"
    if "parameters" in data and "constraintWeightOverrides" in data:
        return "solver-format5"
    if data.get("format") == 1 and isinstance(data.get("employees"), list):
        return "meridian-limits"
    return None


_RAVE_HINT = re.compile(r"(?m)^\s*(module\s+[\w.]+|rule\b)|"
                        r"%\w+%\s*=\s*parameter\b")


def detect_format_text(raw: str) -> Optional[str]:
    """Автоопределение по сырому тексту (JSON / XML / Rave / CSV)."""
    s = raw.lstrip("﻿ \t\r\n")
    if not s:
        return None
    if s[0] in "{[":
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            return detect_format(data)
        if isinstance(data, list) and data \
                and all(isinstance(r, dict) and "name" in r
                        and "value" in r for r in data[:5]):
            return "norm-table"
        return None
    if s[0] == "<":
        return "roster-xml"
    if _RAVE_HINT.search(s) and re.search(r"\bend\b|= parameter", s):
        return "jeppesen-rave"
    first = s.splitlines()[0]
    delim = max(";,\t", key=first.count)
    if first.count(delim) and _norm_header_map(first.split(delim)):
        return "norm-table"
    return None


def import_rules(data: dict, fmt: str, source_name: str, *,
                 country: Optional[str] = None) -> dict:
    """JSON-вход (обратная совместимость). Текстовые форматы сюда не
    попадают — для них import_rules_text. country — страна юрисдикции
    от оператора; обязательна для FORMATS_REQUIRE_COUNTRY."""
    if fmt == "auto":
        detected = detect_format(data)
        if detected is None:
            raise RulesImportError(
                "auto: формат входа не распознан; укажите --format явно "
                f"(доступны: {', '.join(ALL_FORMATS)})")
        fmt = detected
    if fmt == "norm-table":
        pack = import_norm_table(json.dumps(data, ensure_ascii=False),
                                 source_name)
        return _apply_country(pack, fmt, country)
    if fmt in TEXT_HANDLERS:
        raise RulesImportError(
            f"{fmt}: текстовый формат — передайте содержимое файла как "
            f"текст (file_text), а не разобранный JSON")
    if fmt not in FORMATS:
        raise RulesImportError(
            f"неизвестный формат {fmt!r}; доступны: "
            f"{', '.join(ALL_FORMATS)}")
    return _apply_country(FORMATS[fmt](data, source_name), fmt, country)


def import_rules_text(raw: str, fmt: str, source_name: str, *,
                      country: Optional[str] = None) -> dict:
    """Единая точка входа: сырой текст файла любого формата. country —
    страна юрисдикции от оператора; отказ называет РАЗРЕШЁННЫЙ формат
    (важно при format=auto)."""
    if not isinstance(raw, str) or not raw.strip():
        raise RulesImportError("пустой вход")
    if fmt == "auto":
        detected = detect_format_text(raw)
        if detected is None:
            raise RulesImportError(
                "auto: формат входа не распознан; укажите формат явно "
                f"(доступны: {', '.join(ALL_FORMATS)})")
        fmt = detected
    if fmt in TEXT_HANDLERS:
        return _apply_country(TEXT_HANDLERS[fmt](raw, source_name),
                              fmt, country)
    if fmt not in FORMATS:
        raise RulesImportError(
            f"неизвестный формат {fmt!r}; доступны: "
            f"{', '.join(ALL_FORMATS)}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RulesImportError(
            f"{fmt}: вход не разобран как JSON: {exc}")
    if not isinstance(data, dict):
        raise RulesImportError(f"{fmt}: ожидается JSON-объект")
    return _apply_country(FORMATS[fmt](data, source_name), fmt, country)


# --------------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="импорт правил авиакомпании во Пакет правил COSMOS")
    ap.add_argument("input", type=Path)
    ap.add_argument("--format", default="auto",
                    choices=["auto"] + list(ALL_FORMATS))
    ap.add_argument("--country", default=None,
                    help="страна юрисдикции пакета (INTL или код "
                         "ISO-3166 из двух букв); обязательна для "
                         "форматов, не несущих её в файле: "
                         + ", ".join(sorted(FORMATS_REQUIRE_COUNTRY)))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if not args.input.is_file():
        print(f"ERROR: файл не найден: {args.input}", flush=True)
        return 2
    try:
        raw = args.input.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            raw = args.input.read_text(encoding="cp1251")
        except UnicodeDecodeError as exc:
            print(f"ERROR: кодировка входа не распознана: {exc}",
                  flush=True)
            return 1
    try:
        pack = import_rules_text(raw, args.format, args.input.name,
                                 country=args.country)
    except RulesImportError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 1
    out = args.out or args.input.with_name(args.input.stem
                                           + "_rule_pack.json")
    out.write_text(json.dumps(pack, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"пакет правил: {out} ({len(pack['rules'])} правил)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
