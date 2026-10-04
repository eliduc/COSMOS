# -*- coding: utf-8 -*-
"""Гейт режима 2 — сверки формализации третьей стороны (пакет РМ2).

Проверяет отчёт сверки и модель с вопросами части 4:

1. самотест: вердикт вне перечня и «расходится» без строки свода
   отбраковываются сверкой; вопрос части 4 без third_party_refs и
   часть 4 при meta.mode=1 ловятся контрактом модели;
2. модель проходит оба слоя контракта; каждый вопрос части 4 несёт
   third_party_refs с якорями, существующими в отчёте;
3. счётчики отчёта сходятся с пересчётом по перечню; каждое «не
   сверено» несёт причину;
4. md-отчёт побайтно совпадает с рендером из машинной модели отчёта.

Печатает счётчики (У-05) и ВЕРДИКТ; красный — код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_mode2 MODEL.json ОТЧЁТ.json \
        [--md third-party-report.md]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from cdpm import mode2
from cdpm import model as m


def _selftest() -> None:
    from cdpm.sample import sample_model
    svod = sample_model()
    entry = {"file": "THIRD.md", "sha256": "0" * 64, "clauses": [
        {"anchor": "п.1", "kind": "p",
         "text": "Отдых на базе не менее 14 часов между сменами "
                 "экипажа."},
        {"anchor": "п.2", "kind": "p",
         "text": "Экипажу запрещены полёты в дни солнечных затмений "
                 "по внутреннему стандарту."},
    ]}
    rid = svod["rules"][0]["id"]

    def llm(prompt):
        return json.dumps({"checks": [
            {"anchor": "п.1", "verdict": "расходится", "rule": rid,
             "note": "у нас 10 часов, у третьей стороны 14"},
            {"anchor": "п.2", "verdict": "в источниках отсутствует",
             "rule": rid, "note": "затмений в источниках нет"},
        ]}, ensure_ascii=False)

    report, questions = mode2.run_mode2(svod, entry, llm)
    assert report["by_verdict"] == {"расходится": 1,
                                    "в источниках отсутствует": 1}, \
        "самотест: вердикты не разложились"
    assert len(questions) == 2, "самотест: вопросы части 4 не собраны"
    added = mode2.apply_questions(svod, questions)
    assert added == 2 and svod["meta"]["mode"] == 2
    assert m.validate_schema(svod) + m.validate_model(svod) == [], \
        "самотест: модель с частью 4 не проходит контракт"

    # Вердикт вне перечня и «расходится» без строки — отбраковка.
    bad_report, bad_q = mode2.run_mode2(
        sample_model(), entry,
        lambda p: json.dumps({"checks": [
            {"anchor": "п.1", "verdict": "почти сходится"},
            {"anchor": "п.2", "verdict": "расходится", "rule": "R-НЕТ",
             "note": "…"}]}, ensure_ascii=False))
    assert len(bad_report["skipped"]) == 2 and not bad_q, \
        "самотест: отбраковка вердиктов не сработала"

    # Часть 4 при mode=1 ловится контрактом.
    broken = sample_model()
    broken["questions"].append(copy.deepcopy(questions[0]))
    probs = m.validate_model(broken)
    assert any("режиме 2" in p for p in probs), \
        "самотест: часть 4 при mode=1 не поймана"


def check(model_path: Path, report_path: Path,
          md_path: Path | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    if not model_path.is_file():
        return [f"модель {model_path} не существует"], {}
    if not report_path.is_file():
        return [f"отчёт {report_path} не существует"], {}
    svod = m.load_model(model_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    problems += m.validate_schema(svod)
    problems += m.validate_model(svod)

    anchors = {it.get("anchor") for it in report.get("items", [])}
    part4 = [q for q in svod.get("questions", [])
             if q.get("part") == "4"]
    for q in part4:
        for ref in q.get("third_party_refs", []):
            if ref.get("anchor") not in anchors:
                problems.append(f"{q.get('id')}: якорь "
                                f"{ref.get('anchor')!r} отсутствует в "
                                f"отчёте сверки")
    recount: dict[str, int] = {}
    for it in report.get("items", []):
        v = it.get("verdict")
        recount[v] = recount.get(v, 0) + 1
    recount = {k: v for k, v in recount.items() if v}
    if report.get("by_verdict") != recount:
        problems.append(f"отчёт: by_verdict {report.get('by_verdict')} "
                        f"не сходится с пересчётом {recount}")
    if report.get("checked") != len(report.get("items", [])):
        problems.append("отчёт: checked не сходится с перечнем items")
    for s in report.get("skipped", []):
        if not (s.get("reason") or "").strip():
            problems.append(f"отчёт: «не сверено» {s.get('anchor')} "
                            f"без причины (fail-closed)")
    if md_path is not None:
        if not md_path.is_file():
            problems.append(f"md-отчёт {md_path} не существует")
        elif md_path.read_text(encoding="utf-8") \
                != mode2.render_report_md(report):
            problems.append("md-отчёт не совпадает с пересборкой из "
                            "модели — правка мимо модели")
    counters = {"claims": report.get("claims_total", 0),
                "checked": report.get("checked", 0),
                "by_verdict": report.get("by_verdict", {}),
                "part4": len(part4),
                "skipped": len(report.get("skipped", []))}
    return problems, counters


def run(model: str, report: str, md: str | None) -> int:
    _selftest()
    problems, c = check(Path(model), Path(report),
                        Path(md) if md else None)
    if c:
        by = ", ".join(f"{k} {v}" for k, v in c["by_verdict"].items())
        print(f"рассмотрено: утверждений {c['claims']}, сверено "
              f"{c['checked']}" + (f" ({by})" if by else "")
              + f", вопросов части 4: {c['part4']}, не сверено "
              f"{c['skipped']} (самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: сверка формализации соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(description="Гейт режима 2 (часть 4)")
    ap.add_argument("model", help="model.json с вопросами части 4")
    ap.add_argument("report", help="third-party-report.json")
    ap.add_argument("--md", help="third-party-report.md для сверки")
    args = ap.parse_args(argv)
    return run(args.model, args.report, args.md)


if __name__ == "__main__":
    raise SystemExit(main())
