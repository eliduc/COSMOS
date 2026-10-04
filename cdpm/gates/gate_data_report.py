# -*- coding: utf-8 -*-
"""Гейт отчёта 3.3 — качество операционных данных (линия данных, ДЛ-1).

Проверяет машинную модель отчёта:

1. самотест: валидатор ловит контрольные дефекты (находка без
   доказательства, находка по непрогнанной проверке, класс не
   согласован с основанием, «прогнана» без счёта записей, проверка по
   блокированной строке свода, разъехавшиеся счётчики);
2. инварианты контракта (при переданном своде — трассировка оснований
   к его строкам);
3. счётчики отчёта сходятся с пересчётом по спискам;
4. md-отчёт побайтно совпадает с рендером из модели — правка Markdown
   мимо модели ловится.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_data_report ОТЧЁТ.json \
        [--svod MODEL.json] [--md data-report.md]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from cdpm import data_report as dr


def _selftest() -> None:
    from cdpm.sample import sample_model
    svod = sample_model()
    clean = dr.sample_data_report()
    assert dr.validate_report(clean, svod) == [], \
        "самотест: чистый образец не проходит контракт"

    def broken(mutate) -> list[str]:
        doc = copy.deepcopy(clean)
        mutate(doc)
        return dr.validate_report(doc, svod)

    probs = broken(lambda d: d["findings"][0].update(evidence=""))
    assert any("без доказательства" in p for p in probs), \
        "самотест: находка без доказательства не поймана"
    probs = broken(lambda d: d["findings"][0].update(
        check="C-R-S1-П-1-1-0"))
    assert any("не «прогнана»" in p for p in probs), \
        "самотест: находка по непрогнанной проверке не поймана"
    probs = broken(lambda d: d["findings"][1].update(
        **{"class": "дубль"}))
    assert any("не согласован с основанием" in p for p in probs), \
        "самотест: класс против основания не пойман"
    probs = broken(lambda d: d["checks"][0].update(rows_checked=0))
    assert any("У-05" in p for p in probs), \
        "самотест: «прогнана» без счёта записей не поймана"
    probs = broken(lambda d: d["checks"][1].update(
        status="прогнана", rows_checked=40))
    assert any("блокирована вопросом" in p for p in probs), \
        "самотест: проверка по блокированной строке не поймана"
    probs = broken(lambda d: d["counters"].update(rows_total=1))
    assert any("не сходятся с пересчётом" in p for p in probs), \
        "самотест: разъехавшиеся счётчики не пойманы"


def check(report_path: Path, svod_path: Path | None = None,
          md_path: Path | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    if not report_path.is_file():
        return [f"отчёт {report_path} не существует"], {}
    doc = dr.load_report(report_path)
    svod = json.loads(svod_path.read_text(encoding="utf-8")) \
        if svod_path else None
    problems += dr.validate_report(doc, svod)
    if svod_path is None:
        problems.append("свод не передан — трассировка оснований к "
                        "строкам свода не проверена (передайте --svod)")
    if md_path is not None:
        if not md_path.is_file():
            problems.append(f"md-отчёт {md_path} не существует")
        elif md_path.read_text(encoding="utf-8") \
                != dr.render_data_report(doc):
            problems.append("md-отчёт не совпадает с пересборкой из "
                            "модели — правка мимо модели")
    m = dr.metrics_of(doc)
    counters = {"sources": m["files"], "rows": m["rows_total"],
                "checks": len(doc.get("checks", [])),
                "checks_run": m["checks_run"],
                "findings": len(doc.get("findings", []))}
    return problems, counters


def run(report: str, svod: str | None, md: str | None) -> int:
    _selftest()
    problems, c = check(Path(report),
                        Path(svod) if svod else None,
                        Path(md) if md else None)
    if c:
        print(f"рассмотрено: источников {c['sources']}, записей "
              f"{c['rows']}, проверок {c['checks']} (прогнано "
              f"{c['checks_run']}), находок {c['findings']} "
              f"(самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: отчёт о качестве данных соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(description="Гейт отчёта 3.3 CDPM")
    ap.add_argument("report", help="машинная модель отчёта (json)")
    ap.add_argument("--svod", help="model.json свода — трассировка "
                                   "оснований")
    ap.add_argument("--md", help="data-report.md для побайтной сверки")
    args = ap.parse_args(argv)
    return run(args.report, args.svod, args.md)


if __name__ == "__main__":
    raise SystemExit(main())
