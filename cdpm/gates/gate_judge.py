# -*- coding: utf-8 -*-
"""Гейт судьи меры успеха CDPM (пакет работ П-4).

Проверяет отчёт судьи против эталона и модели:

1. самотест: разбор эталонного формата обязан извлекать строки и
   вопросы (и не принимать мусорные строки таблиц);
2. полнота счёта: строк в отчёте ровно столько, сколько строк Части 1
   в эталоне (повторным разбором эталона, не числом из отчёта);
   вердикт каждой строки — из закрытого перечня; сводка by_verdict
   сходится с построчными вердиктами; список спорных пар — ровно
   строки с вердиктом «спорно»;
3. ссылки: вердикты «найдено»/«найдено частично» несут кандидата,
   существующего в модели CDPM; «не найдено»/«спорно» — без кандидата;
4. вопросы: строк в отчёте — по числу вопросов Ч2 эталона; вердикты из
   перечня; «изобретённые» правила существуют в модели;
5. человекочитаемый отчёт md побайтно совпадает с пересборкой из
   машинного отчёта.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_judge ЭТАЛОН.md КАТАЛОГ_ИЗВЛЕЧЕНИЯ \
        КАТАЛОГ_ОТЧЁТА
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdpm import judge, model as m

_ALLOWED_ROW = judge.VERDICTS + ("спорно",)
_ALLOWED_Q = ("переоткрыт", "не переоткрыт", "спорно")

_SELFTEST_ETALON = """## ЧАСТЬ 1

| ID | Описание правила | Этап | Тип | Источник | Примечание |
|---|---|---|---|---|---|
| T-1 | **Проверочное правило:** HARD: x ≥ 1 | PAIRING | HARD | DOC-1 П.1.1 | — |
| мусор | не строка свода | — | ЧУШЬ | — | — |

#### ⚠️ ВОПРОС 9.9.9: Проверочный вопрос
(а) да; (б) нет.
"""


def _selftest() -> None:
    got = judge.parse_etalon(_SELFTEST_ETALON)
    assert [r["id"] for r in got["rules"]] == ["T-1"], \
        "самотест: разбор эталона принял мусорную строку или потерял " \
        "настоящую"
    assert got["rules"][0]["anchors"] == ["1.1"], \
        "самотест: якоря строки эталона не извлечены"
    assert [q["qid"] for q in got["questions"]] == ["9.9.9"], \
        "самотест: вопрос Ч2 не извлечён"
    assert got["questions"][0]["subs"] == ["а", "б"], \
        "самотест: подпункты вопроса не сосчитаны"


def check(etalon_path: Path, extract_dir: Path,
          out_dir: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    counters = {"rows": 0, "questions": 0, "disputed": 0, "invented": 0}
    rep_path = out_dir / "judge-report.json"
    if not rep_path.is_file():
        return [f"отчёт {rep_path} не существует"], counters
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    etalon = judge.parse_etalon(etalon_path.read_text(encoding="utf-8"))
    doc = m.load_model(extract_dir / "model.json")
    rule_ids = {r["id"] for r in doc.get("rules", [])}
    q_ids = {q["id"] for q in doc.get("questions", [])}

    rows = rep.get("rows", [])
    counters["rows"] = len(rows)
    if len(rows) != len(etalon["rules"]):
        problems.append(f"строк в отчёте {len(rows)}, а в эталоне "
                        f"{len(etalon['rules'])} — счёт неполон")
    et_ids = [r["id"] for r in etalon["rules"]]
    if [r.get("etalon") for r in rows] != et_ids:
        problems.append("порядок или состав строк отчёта не совпадает "
                        "со строками эталона")
    seen_disputed = []
    by_verdict = {v: 0 for v in judge.VERDICTS}
    for row in rows:
        v = row.get("verdict")
        if v not in _ALLOWED_ROW:
            problems.append(f"{row.get('etalon')}: вердикт {v!r} вне "
                            f"перечня {_ALLOWED_ROW}")
            continue
        if v in judge.VERDICTS:
            by_verdict[v] += 1
        cand = row.get("candidate")
        if v in ("найдено", "найдено частично"):
            if not cand:
                problems.append(f"{row.get('etalon')}: вердикт {v!r} "
                                f"без кандидата")
            elif cand not in rule_ids:
                problems.append(f"{row.get('etalon')}: кандидат {cand!r} "
                                f"не существует в модели CDPM")
        elif cand:
            problems.append(f"{row.get('etalon')}: вердикт {v!r} не "
                            f"должен нести кандидата")
        if v == "спорно":
            seen_disputed.append(row.get("etalon"))
    if rep.get("by_verdict") != by_verdict:
        problems.append(f"сводка by_verdict {rep.get('by_verdict')} не "
                        f"сходится с построчными вердиктами {by_verdict}")
    disputed = [d.get("etalon") for d in rep.get("disputed", [])]
    counters["disputed"] = len(disputed)
    if sorted(disputed) != sorted(seen_disputed):
        problems.append("список спорных пар не совпадает со строками с "
                        "вердиктом «спорно»")

    q_rows = rep.get("questions", [])
    counters["questions"] = len(q_rows)
    if len(q_rows) != len(etalon["questions"]):
        problems.append(f"вопросов в отчёте {len(q_rows)}, а в эталоне "
                        f"{len(etalon['questions'])}")
    for q in q_rows:
        if q.get("verdict") not in _ALLOWED_Q:
            problems.append(f"{q.get('etalon')}: вердикт вопроса "
                            f"{q.get('verdict')!r} вне перечня")
        if q.get("verdict") == "переоткрыт" and \
                q.get("candidate") not in q_ids:
            problems.append(f"{q.get('etalon')}: вопрос-кандидат "
                            f"{q.get('candidate')!r} не существует в "
                            f"модели")

    counters["invented"] = len(rep.get("invented", []))
    for inv in rep.get("invented", []):
        if inv.get("rule") not in rule_ids:
            problems.append(f"изобретённое {inv.get('rule')!r} не "
                            f"существует в модели")

    md_path = out_dir / "judge-report.md"
    if not md_path.is_file():
        problems.append(f"{md_path} не существует")
    elif md_path.read_text(encoding="utf-8") != \
            judge._render_report(rep):
        problems.append("judge-report.md не совпадает с пересборкой из "
                        "машинного отчёта — правлен мимо отчёта")
    return problems, counters


def run(etalon: str, extract_dir: str, out_dir: str) -> int:
    _selftest()
    problems, c = check(Path(etalon), Path(extract_dir), Path(out_dir))
    print(f"рассмотрено: строк эталона {c['rows']}, вопросов "
          f"{c['questions']}, спорных {c['disputed']}, изобретённых "
          f"{c['invented']} (самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: отчёт судьи соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(description="Гейт судьи меры успеха")
    ap.add_argument("etalon", help="эталонный свод (md)")
    ap.add_argument("extract_dir", help="каталог результата извлечения")
    ap.add_argument("out", help="каталог отчёта судьи")
    args = ap.parse_args(argv)
    return run(args.etalon, args.extract_dir, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
