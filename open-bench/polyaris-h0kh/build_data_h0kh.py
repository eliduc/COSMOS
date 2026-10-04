# -*- coding: utf-8 -*-
"""Книга данных набора «Полярис-H0-КИ» = книга H0 + листы «История» и
«Утверждённые назначения» (спецификация 1.17, ступень L плана аудита).

Сценарий согласован с COSMOS 05.09.2026:
- H01–H12 — остатки с начала месяца (налёт, рабочее время, дни, посадки),
  последняя смена 02.09 вечером на базе, серия 2 дня — обычная история;
- H13 — конец последней смены ДОМА за 6 часов до горизонта
  (03.09 18:00 при planSince 04.09 00:00): ожидаемый красный по полу
  отдыха дома 720 минут (правило «Минимальный отдых дома»);
- H14 — строки истории НЕТ: раздел передан, записи нет,
  assume_zero_when_missing=false → ожидаемая остановка кнопки COSMOS
  словами (неизвестное не превращается в ноль);
- H15–H24 — отдыхали весь месяц: явные нули и последняя смена 20.08 —
  законная история без работы;
- «Утверждённые назначения»: H01 на PL101 04.09.2026 — переносится как
  закрепление (исполнителя у потребителя пока нет — переносится дословно).

    python open-bench/polyaris-h0kh/build_data_h0kh.py --write [--variant stop|full]

Варианты (согласованы с COSMOS 05.09 после первого нажатия):
- stop (умолчание, data/ID-H0KH-DATA.xlsx, комплект ./kit-manifest.json):
  H14 без строки → ожидаемая остановка кнопки ростеринга на Ш-2с;
- full (data/ID-H0KH-FULL-DATA.xlsx, комплект ./full/kit-manifest.json):
  строки у всех 24 (H14 — профиль «отдыхал весь месяц»), H13 с отдыхом
  6 ч → ожидание «допустимо», первая смена H13 не раньше 04.09 06:00,
  проверка пола отдыха судится у 24 сотрудников. Красный по отдыху в
  сквозном прогоне недостижим: пол отдыха — hard у решателя, он его не
  нарушает; красный сертификата проверяется подложенным решением в
  модульных тестах COSMOS.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "open-bench" / "polyaris-h0"))
import build_data_h0 as h0  # noqa: E402

OUT_XLSX = HERE / "data" / "ID-H0KH-DATA.xlsx"
#: Вариант → (табельный без строки истории или None, имя книги).
VARIANTS = {
    "stop": ("H14", "ID-H0KH-DATA.xlsx"),
    "full": (None, "ID-H0KH-FULL-DATA.xlsx"),
}
HISTORY_COLUMNS = [
    "Табельный номер", "Срез",
    "Налёт с начала месяца, мин", "Рабочее время с начала месяца, мин",
    "Лётное время с начала месяца, мин", "Дней отработано", "Посадок",
    "Последняя смена: конец", "Последняя смена: станция",
    "Последняя смена: работа, мин", "Последняя смена: размах, мин",
    "Последняя смена: ночная",
    "Серия рабочих дней", "Серия ночных смен", "Дней с выходного",
    "Местоположение", "Окно 7 дн: работа, мин",
]
NO_HISTORY_ROW = "H14"        # без строки — ожидаемая остановка кнопки
SHORT_REST_ROW = "H13"        # 6 часов до горизонта — красный по полу 720


def history_rows(crew: list[dict], no_history_row: str | None = NO_HISTORY_ROW) -> list[dict]:
    rows = []
    for n, c in enumerate(crew, 1):
        eid = c["Табельный номер"]
        base = c["База"]
        if eid == no_history_row:
            continue
        row = {col: "" for col in HISTORY_COLUMNS}
        row["Табельный номер"] = eid
        row["Срез"] = "04.09.2026 00:00"
        if eid == SHORT_REST_ROW:
            row.update({
                "Налёт с начала месяца, мин": "540",
                "Рабочее время с начала месяца, мин": "720",
                "Лётное время с начала месяца, мин": "540",
                "Дней отработано": "2", "Посадок": "4",
                "Последняя смена: конец": "03.09.2026 18:00",
                "Последняя смена: станция": base,
                "Последняя смена: работа, мин": "600",
                "Последняя смена: размах, мин": "660",
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": "2", "Серия ночных смен": "0",
                "Дней с выходного": "2", "Местоположение": base,
                "Окно 7 дн: работа, мин": "1320"})
        elif n <= 12:
            row.update({
                "Налёт с начала месяца, мин": str(600 + 20 * n),
                "Рабочее время с начала месяца, мин": str(900 + 25 * n),
                "Лётное время с начала месяца, мин": str(600 + 20 * n),
                "Дней отработано": "3", "Посадок": "6",
                "Последняя смена: конец": "02.09.2026 19:30",
                "Последняя смена: станция": base,
                "Последняя смена: работа, мин": "480",
                "Последняя смена: размах, мин": "540",
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": "2", "Серия ночных смен": "0",
                "Дней с выходного": "2", "Местоположение": base,
                "Окно 7 дн: работа, мин": str(900 + 25 * n)})
        else:
            row.update({
                "Налёт с начала месяца, мин": "0",
                "Рабочее время с начала месяца, мин": "0",
                "Лётное время с начала месяца, мин": "0",
                "Дней отработано": "0", "Посадок": "0",
                "Последняя смена: конец": "20.08.2026 15:00",
                "Последняя смена: станция": base,
                "Последняя смена: работа, мин": "420",
                "Последняя смена: размах, мин": "480",
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": "0", "Серия ночных смен": "0",
                "Дней с выходного": "14", "Местоположение": base,
                "Окно 7 дн: работа, мин": "0"})
        rows.append(row)
    return rows


def approved_rows() -> list[dict]:
    return [{"Табельный номер": "H01", "Рейс": "PL101", "Дата": "04.09.2026"}]


def tables(variant: str = "stop") -> dict:
    no_row, _name = VARIANTS[variant]
    t = h0.tables()
    crew = t["Экипажи"]["rows"]
    t["История"] = {"columns": HISTORY_COLUMNS, "rows": history_rows(crew, no_row)}
    t["Утверждённые назначения"] = {"columns": ["Табельный номер", "Рейс", "Дата"],
                                     "rows": approved_rows()}
    return t


def self_check(variant: str = "stop") -> list[str]:
    """Книга согласована сама с собой: у каждого сотрудника (кроме
    стоп-строки варианта) — строка; конец последней смены не позже среза;
    числа неотрицательны."""
    problems: list[str] = []
    no_row, _name = VARIANTS[variant]
    t = tables(variant)
    crew = {r["Табельный номер"] for r in t["Экипажи"]["rows"]}
    hist = {r["Табельный номер"]: r for r in t["История"]["rows"]}
    if set(hist) != crew - ({no_row} if no_row else set()):
        problems.append(f"состав строк истории не равен экипажам "
                        f"{'без ' + no_row if no_row else 'полностью'}")
    from cdpm import history_section as hs
    fields, rolling, unknown = hs.column_map(HISTORY_COLUMNS)
    if unknown:
        problems.append(f"неопознанные колонки: {unknown}")
    for eid, row in hist.items():
        rec, probs = hs.parse_row(row, fields, rolling)
        problems += [f"{eid}: {p}" for p in probs]
        end = (rec.get("last_duty") or {}).get("end")
        if end and end > rec.get("as_of", ""):
            problems.append(f"{eid}: конец смены {end} позже среза")
    return problems


def write_xlsx(variant: str = "stop", path: Path | None = None) -> Path:
    import openpyxl
    path = path or HERE / "data" / VARIANTS[variant][1]
    wb = openpyxl.Workbook()
    first = True
    for name, sheet in tables(variant).items():
        ws = wb.active if first else wb.create_sheet(name)
        ws.title = name
        first = False
        ws.append(list(sheet["columns"]))
        for row in sheet["rows"]:
            ws.append([row.get(c) for c in sheet["columns"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    # Байты книги — от её текста, а не от часов машины: нормализация тем
    # же носителем, что у базового сборщика (freeze_office_file). Книга
    # H0-КИ выводится из книги H0 и берёт носитель через неё
    # (build_data_h0.freeze_office) — своей копии формулы не заводит.
    return h0.freeze_office(path)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="записать xlsx")
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="stop",
                    help="stop — H14 без строки (умолчание); full — строки у всех")
    a = ap.parse_args(argv)
    no_row, _name = VARIANTS[a.variant]
    problems = self_check(a.variant)
    t = tables(a.variant)
    print(f"книга данных H0-КИ [{a.variant}]:",
          ", ".join(f"{k} {len(v['rows'])}" for k, v in t.items()))
    if problems:
        print("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
        return 1
    print("самопроверка чиста; без строки истории:", no_row or "никто",
          "; короткий отдых:", SHORT_REST_ROW)
    if a.write:
        print("записано:", write_xlsx(a.variant))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
