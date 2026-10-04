# -*- coding: utf-8 -*-
"""Гейт сверки спорного (линия данных, ДЛ-7).

Проверяет сверенный отчёт 3.3 и сводку прохода сверки:

1. самотест: валидатор ловит контрольные дефекты (вердикт вне перечня,
   вердикт без обоснования, сверка находки встроенной проверки,
   разъехавшиеся счётчики сверки);
2. инварианты контракта отчёта (при переданном своде — с трассировкой);
3. счётчик вердиктов сводки сходится с пересчётом по находкам отчёта;
   каждая «не сверенная» позиция сводки несёт причину и указывает на
   существующую находку без вердикта;
4. md-отчёт побайтно совпадает с рендером из модели.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_data_review ОТЧЁТ.json СВОДКА.json \
        [--svod MODEL.json] [--md data-report.md]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from cdpm import data_report as dr


def _reviewed_sample() -> dict:
    """Образец «Полярис» со сверенной находкой — материал самотеста."""
    doc = dr.sample_data_report()
    doc["findings"][0]["review"] = {
        "verdict": "похоже на настоящее нарушение",
        "basis": "допуск CAT-C истёк до даты рейса — прямое чтение "
                 "строки свода"}
    doc["counters"] = dr.metrics_of(doc)
    return doc


def _selftest() -> None:
    from cdpm.sample import sample_model
    svod = sample_model()
    clean = _reviewed_sample()
    assert dr.validate_report(clean, svod) == [], \
        "самотест: сверенный образец не проходит контракт"
    assert clean["counters"]["review_by_verdict"] == {
        "похоже на настоящее нарушение": 1}, \
        "самотест: счётчик вердиктов не считается"
    plain = dr.sample_data_report()
    assert "review_by_verdict" not in plain["counters"], \
        "самотест: несверённый отчёт получил счётчик сверки"

    def broken(mutate) -> list[str]:
        doc = copy.deepcopy(clean)
        mutate(doc)
        doc_problems = dr.validate_report(doc, svod)
        return doc_problems

    probs = broken(lambda d: d["findings"][0]["review"].update(
        verdict="скорее всего нормально"))
    assert any("вне перечня" in p for p in probs), \
        "самотест: вердикт вне перечня не пойман"
    probs = broken(lambda d: d["findings"][0]["review"].update(basis=""))
    assert any("без обоснования" in p for p in probs), \
        "самотест: вердикт без обоснования не пойман"
    probs = broken(lambda d: d["findings"][1].update(
        review={"verdict": "законное исключение", "basis": "формат"}))
    assert any("только для находок" in p for p in probs), \
        "самотест: сверка встроенной проверки не поймана"
    probs = broken(lambda d: d["counters"].pop("review_by_verdict"))
    assert any("не сходятся с пересчётом" in p for p in probs), \
        "самотест: разъехавшийся счётчик сверки не пойман"


def check(report_path: Path, summary_path: Path,
          svod_path: Path | None = None,
          md_path: Path | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    if not report_path.is_file():
        return [f"отчёт {report_path} не существует"], {}
    if not summary_path.is_file():
        return [f"сводка сверки {summary_path} не существует"], {}
    doc = dr.load_report(report_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    svod = json.loads(svod_path.read_text(encoding="utf-8")) \
        if svod_path else None
    problems += dr.validate_report(doc, svod)
    if svod_path is None:
        problems.append("свод не передан — трассировка и блокировки "
                        "строк-оснований не проверены (передайте "
                        "--svod)")

    by_verdict = {v: 0 for v in dr.REVIEW_VERDICTS}
    findings_by_id: dict[str, dict] = {}
    for f in doc.get("findings", []):
        findings_by_id[f.get("id") or ""] = f
        verdict = (f.get("review") or {}).get("verdict")
        if verdict in by_verdict:
            by_verdict[verdict] += 1
    by_verdict = {k: v for k, v in by_verdict.items() if v}
    if summary.get("by_verdict") != by_verdict:
        problems.append(f"сводка сверки: by_verdict "
                        f"{summary.get('by_verdict')} не сходится с "
                        f"пересчётом по находкам {by_verdict}")
    reviewed = sum(by_verdict.values())
    # «reviewed» сводки — счёт ПРОХОДА (повторная сверка может дать 0);
    # с отчётом сверяется накопленный итог reviewed_total.
    if summary.get("reviewed_total") != reviewed:
        problems.append(f"сводка сверки: reviewed_total "
                        f"{summary.get('reviewed_total')} не сходится "
                        f"с пересчётом {reviewed}")
    for s in summary.get("skipped", []):
        fid = s.get("finding")
        if not (s.get("reason") or "").strip():
            problems.append(f"сводка сверки: «не сверено» {fid} без "
                            f"причины (fail-closed)")
        f = findings_by_id.get(fid)
        if f is None:
            problems.append(f"сводка сверки: «не сверено» указывает на "
                            f"несуществующую находку {fid!r}")
        elif f.get("review"):
            problems.append(f"сводка сверки: находка {fid} и «не "
                            f"сверена», и несёт вердикт")

    if md_path is not None:
        if not md_path.is_file():
            problems.append(f"md-отчёт {md_path} не существует")
        elif md_path.read_text(encoding="utf-8") \
                != dr.render_data_report(doc):
            problems.append("md-отчёт не совпадает с пересборкой из "
                            "модели — правка мимо модели")

    counters = {"findings": len(doc.get("findings", [])),
                "candidates": summary.get("candidates", 0),
                "reviewed": reviewed,
                "by_verdict": by_verdict,
                "skipped": len(summary.get("skipped", []))}
    return problems, counters


def run(report: str, summary: str, svod: str | None,
        md: str | None) -> int:
    _selftest()
    problems, c = check(Path(report), Path(summary),
                        Path(svod) if svod else None,
                        Path(md) if md else None)
    if c:
        by = ", ".join(f"{k} {v}" for k, v in c["by_verdict"].items())
        print(f"рассмотрено: находок {c['findings']}, кандидатов "
              f"сверки {c['candidates']}, сверено {c['reviewed']}"
              + (f" ({by})" if by else "")
              + f", не сверено {c['skipped']} (самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: сверка спорного соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(description="Гейт сверки спорного")
    ap.add_argument("report", help="машинная модель отчёта (json)")
    ap.add_argument("summary", help="review-summary.json прохода")
    ap.add_argument("--svod", help="model.json свода")
    ap.add_argument("--md", help="data-report.md для побайтной сверки")
    args = ap.parse_args(argv)
    return run(args.report, args.summary, args.svod, args.md)


if __name__ == "__main__":
    raise SystemExit(main())
