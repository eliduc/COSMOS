# -*- coding: utf-8 -*-
"""ЭКС-3 (решение владельца 2026-08-21): экспорт ОЧИЩЕННЫХ
операционных данных авиакомпании — нижний контур.

Очистка честная и машинно-обоснованная: из книги данных исключаются
только строки, на которые указывают находки отчёта проверок (3.3)
БЛОКИРУЮЩЕЙ и КРИТИЧЕСКОЙ серьёзности с адресом строки; каждая
исключённая строка попадает в отчёт очистки (лист/строка/класс/
причина). Серьёзные и редакционные находки строк НЕ удаляют — они
перечисляются в отчёте как оставленные. Ничего не правится «по
догадке»: очистка = вычитание доказанных дефектов, не исправление.

Экспорт требует отчёта проверок: без него «очищенных» данных не
существует — честный отказ.
"""
from __future__ import annotations

import io
import json

_REMOVE_SEVERITIES = ("БЛОКИРУЮЩЕЕ", "КРИТИЧЕСКОЕ")

# Реляционные шаблоны: находка описывает СВОЙСТВО ПАРЫ строк
# (например, тесная стыковка расписания), а не дефект самой строки —
# вычитание строки по ней уничтожает задачу, не дефект (находка
# сессии COSMOS на мини-эталоне, 2026-08-22: очистка выкинула 12 из
# 13 рейсов по «минимальному разрыву» и оставила задачу из одного
# рейса). Такие находки остаются в данных с пометкой.
_RELATIONAL_TEMPLATES = ("минимальный-разрыв-часов",)


class CleanDataError(ValueError):
    """Очистка не состоялась; причина названа."""


def build_clean(dataset: dict, report: dict) -> tuple[dict, dict]:
    """(очищенные tables, отчёт очистки). Строки нумеруются так же,
    как в находках проверок (1-based по списку rows листа)."""
    findings = report.get("findings", [])
    template_of = {c.get("id"): c.get("template")
                   for c in report.get("checks", [])}
    to_remove: dict[tuple, dict] = {}
    kept_serious: list[dict] = []
    for f in findings:
        t = f.get("target") or {}
        sid, sheet, row = t.get("sid"), t.get("sheet"), t.get("row")
        entry = {"sid": sid, "sheet": sheet, "row": row,
                 "finding": f.get("id"), "class": f.get("class"),
                 "severity": f.get("severity"),
                 "text": (f.get("text") or "")[:200]}
        if row is None or sheet is None:
            kept_serious.append(entry)      # неадресная — не удалить
            continue
        if template_of.get(f.get("check")) in _RELATIONAL_TEMPLATES:
            entry["relational"] = True
            entry["text"] = ("реляционная находка (свойство пары "
                            "строк) — строку не вычитает: "
                            + entry["text"])[:200]
            kept_serious.append(entry)
            continue
        if f.get("severity") in _REMOVE_SEVERITIES:
            to_remove[(sid, sheet, row)] = entry
        else:
            kept_serious.append(entry)

    clean_tables: dict = {}
    removed: list[dict] = []
    for sid, sheets in (dataset.get("tables") or {}).items():
        clean_tables[sid] = {}
        for name, table in sheets.items():
            rows = table.get("rows", [])
            kept_rows = []
            # Нумерация находок — ЛИСТОВАЯ конвенция исполнителей
            # проверок: строка 2 = первая запись (заголовок = 1).
            # Очистка резала по 1-базной и вычитала СОСЕДНЮЮ строку
            # (живой Лайт run-0040: вместо PL300 без времени вылета
            # ушла здоровая запись; вычетов до этого дня не было ни
            # одного — смещение жило незамеченным).
            for i, row in enumerate(rows, 2):
                hit = to_remove.get((sid, name, i))
                if hit is not None:
                    removed.append(hit)
                else:
                    kept_rows.append(row)
            clean_tables[sid][name] = {
                "columns": table.get("columns", []),
                "rows": kept_rows,
            }
    cleaning = {
        "removed": removed,
        "kept_findings": kept_serious,
        "rows_removed": len(removed),
        "note": "удалены только строки с адресными находками "
                "блокирующей и критической серьёзности; прочие "
                "находки перечислены и оставлены",
    }
    return clean_tables, cleaning


def export_json(dataset: dict, report: dict) -> str:
    tables, cleaning = build_clean(dataset, report)
    return json.dumps({"customer": report.get("customer"),
                       "tables": tables, "cleaning": cleaning},
                      ensure_ascii=False, indent=2) + "\n"


def export_xlsx(dataset: dict, report: dict) -> bytes:
    """Книга в исходном виде (лист на таблицу) + лист «Очистка» с
    перечнем исключённого и оставленного."""
    import openpyxl
    tables, cleaning = build_clean(dataset, report)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sid, sheets in tables.items():
        for name, table in sheets.items():
            title = f"{name}"[:31] or sid
            n = 1
            base = title
            while title in wb.sheetnames:
                n += 1
                title = f"{base[:28]}-{n}"
            ws = wb.create_sheet(title=title)
            cols = table["columns"]
            ws.append(cols)
            for row in table["rows"]:
                ws.append([row.get(c, "") for c in cols])
    ws = wb.create_sheet(title="Очистка")
    ws.append(["Что", "Источник", "Лист", "Строка", "Класс",
               "Серьёзность", "Находка", "Замечание"])
    for e in cleaning["removed"]:
        ws.append(["удалено", e["sid"], e["sheet"], e["row"],
                   e["class"], e["severity"], e["finding"], e["text"]])
    for e in cleaning["kept_findings"]:
        ws.append(["оставлено", e.get("sid"), e.get("sheet"),
                   e.get("row"), e["class"], e["severity"],
                   e["finding"], e["text"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
