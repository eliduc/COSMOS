# -*- coding: utf-8 -*-
"""Гейт Excel-издания анкеты CDPM (пакет ЭА-1).

Книга xlsx не сверяется побайтно: zip-контейнер несёт метки времени,
и два рендера одной модели байтно различны. Гейт сверяет СОДЕРЖИМОЕ
книги с пересборкой из машинной модели:

1. самотест: контрольная порча ячейки идентификатора и счёта сводки
   ловится — гейт, не способный найти ни одной ошибки, не проверка
   (урок методики §6);
2. состав и порядок листов: «Сводка» плюс лист на каждую непустую
   часть анкеты;
3. каждый вопрос ровно один раз на листе своей части: шапка на месте,
   все заполняемые модулем колонки (пять описательных и скрытый
   отпечаток издания вопроса) совпадают с пересборкой, лишних строк
   нет;
4. сводка сходится: авиакомпания, счёт вопросов по частям,
   инструкция заполнения.

Колонка «ОТВЕТ АВИАКОМПАНИИ» не сверяется: она принадлежит
авиакомпании — книга остаётся проверяемой и после заполнения.

Печатает, сколько объектов рассмотрел (У-05), и ВЕРДИКТ; красный
гейт — код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_anketa_xlsx КНИГА.xlsx MODEL.json
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from cdpm import anketa_xlsx as ax
from cdpm import model as m
from cdpm import sample


def check_workbook(doc: dict,
                   book_path: str | Path) -> tuple[list[str], dict]:
    """Сверка книги с моделью. Возвращает (нарушения, счётчики);
    находки предъявляются пакетом, не первой."""
    counters = {"sheets": 0, "questions": 0, "links": 0}
    path = Path(book_path)
    if not path.is_file():
        return [f"книга {path} не существует"], counters
    wb = load_workbook(str(path))
    problems: list[str] = []

    expected_answered = ax.answered_rows(doc)
    expected_names = [ax.SUMMARY_TITLE] + [t for t, _ in
                                           ax.sheet_plan(doc)]
    if expected_answered:
        expected_names.append(ax.ANSWERED_TITLE)
    if wb.sheetnames != expected_names:
        problems.append(f"состав или порядок листов {wb.sheetnames} "
                        f"не совпадает с ожидаемым {expected_names}")

    files = {s["sid"]: s["file"] for s in doc.get("sources", [])}
    n_cols = len(ax.HEADERS)
    for part in m.PARTS:
        part_qs = ax.part_questions(doc, part)
        if not part_qs:
            continue
        title = ax.SHEET_TITLES[part]
        if title not in wb.sheetnames:
            problems.append(f"{title}: лист отсутствует в книге")
            continue
        ws = wb[title]
        counters["sheets"] += 1
        header = tuple(ws.cell(row=1, column=c).value
                       for c in range(1, n_cols + 1))
        if header != ax.HEADERS:
            problems.append(f"{title}: шапка {header!r} не совпадает "
                            f"с {ax.HEADERS!r}")
        for i, q in enumerate(part_qs, start=2):
            row = ax.published_question_row(doc, q, files)
            for c, want in enumerate(row, start=1):
                if c == ax.ANSWER_COL:
                    continue        # ответ принадлежит авиакомпании
                got = ws.cell(row=i, column=c).value
                if got != want:
                    problems.append(
                        f"{title}, строка {i}, колонка "
                        f"«{ax.HEADERS[c - 1]}»: {got!r} вместо "
                        f"{want!r}")
            counters["questions"] += 1
            counters["links"] += (len(q.get("rule_refs", []))
                                  + len(q.get("source_refs", [])))
        for i in range(len(part_qs) + 2, ws.max_row + 1):
            vals = [ws.cell(row=i, column=c).value
                    for c in range(1, n_cols + 1)]
            if any(v not in (None, "") for v in vals):
                problems.append(f"{title}: лишняя строка {i} — в "
                                f"книге больше строк, чем вопросов "
                                f"части")

    if ax.SUMMARY_TITLE in wb.sheetnames:
        ws = wb[ax.SUMMARY_TITLE]
        counters["sheets"] += 1
        for i, row in enumerate(ax.summary_rows(doc), start=1):
            for c, want in enumerate(row, start=1):
                got = ws.cell(row=i, column=c).value
                if got != want:
                    problems.append(
                        f"{ax.SUMMARY_TITLE}, строка {i}: {got!r} "
                        f"вместо {want!r}")
    else:
        problems.append(f"{ax.SUMMARY_TITLE}: лист отсутствует в "
                        f"книге")

    # Лист «Принятые ответы» (ИР-1) — только у моделей с ответами;
    # содержимое сверяется полностью: ответы — часть машинной модели.
    if expected_answered:
        if ax.ANSWERED_TITLE not in wb.sheetnames:
            problems.append(f"{ax.ANSWERED_TITLE}: лист отсутствует "
                            f"в книге")
        else:
            ws = wb[ax.ANSWERED_TITLE]
            counters["sheets"] += 1
            header = tuple(
                ws.cell(row=1, column=c).value
                for c in range(1, len(ax.ANSWERED_HEADERS) + 1))
            if header != ax.ANSWERED_HEADERS:
                problems.append(f"{ax.ANSWERED_TITLE}: шапка "
                                f"{header!r} не совпадает с "
                                f"{ax.ANSWERED_HEADERS!r}")
            for i, row in enumerate(expected_answered, start=2):
                for c, want in enumerate(row, start=1):
                    got = ws.cell(row=i, column=c).value
                    if got != want:
                        problems.append(
                            f"{ax.ANSWERED_TITLE}, строка {i}, "
                            f"колонка «{ax.ANSWERED_HEADERS[c - 1]}»: "
                            f"{got!r} вместо {want!r}")
                counters["questions"] += 1
    return problems, counters


def _selftest() -> None:
    """Контрольные порчи, которые сверка ОБЯЗАНА поймать; ломается
    сверка — гейт падает до проверки живой книги."""
    doc = sample.sample_model()
    with tempfile.TemporaryDirectory() as td:
        book = Path(td) / "anketa.xlsx"
        ax.render_anketa_xlsx(doc, book)
        problems, _ = check_workbook(doc, book)
        assert problems == [], \
            f"самотест: честная книга не прошла: {problems}"

        wb = load_workbook(str(book))
        ws = wb[ax.SHEET_TITLES["1"]]
        ws.cell(row=2, column=1).value = "Q-ПОРЧА"  # чужой ID вопроса
        wb.save(str(book))
        problems, _ = check_workbook(doc, book)
        assert any("Q-ПОРЧА" in p for p in problems), \
            "самотест: порча идентификатора вопроса не поймана"

        ax.render_anketa_xlsx(doc, book)
        wb = load_workbook(str(book))
        ws = wb[ax.SUMMARY_TITLE]
        for row in ws.iter_rows(min_col=1, max_col=2):
            if row[0].value == "Всего вопросов":
                row[1].value = 999          # сломанный счёт сводки
        wb.save(str(book))
        problems, _ = check_workbook(doc, book)
        assert any("999" in p for p in problems), \
            "самотест: расхождение сводки не поймано"


def run(book_path: str, model_path: str) -> int:
    _selftest()
    doc = m.load_model(model_path)
    problems, c = check_workbook(doc, book_path)
    print(f"рассмотрено: листов {c['sheets']}, "
          f"вопросов {c['questions']}, ссылок {c['links']} "
          f"(самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: книга анкеты соответствует модели")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # У-14: кириллица
    ap = argparse.ArgumentParser(
        description="Гейт Excel-издания анкеты CDPM")
    ap.add_argument("book", help="путь к книге anketa.xlsx")
    ap.add_argument("model", help="путь к model.json")
    args = ap.parse_args(argv)
    return run(args.book, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
