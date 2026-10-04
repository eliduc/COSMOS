# -*- coding: utf-8 -*-
"""Гейт применения ответов анкеты (интерактивный цикл, ИР-1).

Проверяет модель после приёмника ответов и сводку прохода:

1. самотест: контракт ловит ключ ответа вне вариантов развилки,
   «применённый» ответ при оставшейся блокировке, ревизию ответа вне
   диапазона; честное применение образца проходит чисто;
2. модель проходит оба слоя контракта (схема + инварианты);
3. счётчики сводки сходятся с пересчётом по модели: применено /
   ожидает обработчика / не разобрано, ревизия равна meta.revision;
4. рендеры сверяются побайтно (свод, анкета) и по содержимому
   (Excel-книга — через гейт книги): открытые вопросы в частях,
   принятые ответы отдельным разделом;
5. замечания чтения книги (сводка `read_problems`: книга чужой
   ревизии, переизданный вопрос, противоречащие дубли, лист вне
   частей) — нарушения: отклонённый ответ авиакомпании не проходит
   молча (аудит 04.09, п. A3.2).

Печатает, сколько объектов рассмотрел (У-05), и ВЕРДИКТ; красный
гейт — код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_answers MODEL.json СВОДКА.json \
        [--svod svod.md] [--anketa anketa.md] [--book anketa.xlsx]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from cdpm import model as m
from cdpm import render
from cdpm.gates import gate_anketa_xlsx


def _recount(doc: dict) -> dict:
    got = {"применён": 0, "ожидает обработчика": 0, "не разобран": 0,
           "пересмотрен": 0, m.REVISION_CONFLICT_STATUS: 0,
           m.PARTIAL_ANSWER_STATUS: 0}
    for q in doc.get("questions", []):
        status = (q.get("answer") or {}).get("status")
        if status in got:
            got[status] += 1
    return got


def _selftest() -> None:
    from cdpm import answers as ans_mod
    from cdpm.sample import sample_model
    doc = sample_model()
    cond_q = next(q for q in doc["questions"]
                  if any(r.get("conditional", {}).get("question")
                         == q["id"] for r in doc["rules"]))
    key = cond_q["answer_format"]["options"][0]["key"]
    summary = ans_mod.apply_answers(doc, {cond_q["id"]: key})
    assert summary["applied"] == 1 and summary["resolved_rules"], \
        "самотест: развилка условного типа не применилась"
    assert m.validate_schema(doc) + m.validate_model(doc) == [], \
        "самотест: применённый образец не проходит контракт"

    broken = copy.deepcopy(doc)
    next(q for q in broken["questions"]
         if q["id"] == cond_q["id"])["answer"]["key"] = "щ"
    assert any("вне вариантов" in p
               for p in m.validate_model(broken)), \
        "самотест: ключ ответа вне вариантов не пойман"

    broken = copy.deepcopy(doc)
    for r in broken["rules"]:
        if r["id"] in summary["resolved_rules"]:
            r.setdefault("blockers", []).append(cond_q["id"])
    assert any("всё ещё заблокированы" in p
               for p in m.validate_model(broken)), \
        "самотест: «применён» при оставшейся блокировке не пойман"

    broken = copy.deepcopy(doc)
    next(q for q in broken["questions"]
         if q["id"] == cond_q["id"])["answer"]["revision"] = 99
    assert any("вне диапазона" in p
               for p in m.validate_model(broken)), \
        "самотест: ревизия ответа вне диапазона не поймана"


def check(model_path: Path, summary_path: Path,
          svod_path: Path | None = None,
          anketa_path: Path | None = None,
          book_path: Path | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    if not model_path.is_file():
        return [f"модель {model_path} не существует"], {}
    if not summary_path.is_file():
        return [f"сводка {summary_path} не существует"], {}
    doc = m.load_model(model_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    problems += m.validate_schema(doc)
    problems += m.validate_model(doc)

    recount = _recount(doc)
    pairs = (("applied", "применён"),
             ("partial", m.PARTIAL_ANSWER_STATUS),
             ("awaiting", "ожидает обработчика"),
             ("unparsed", "не разобран"),
             ("conflicted", m.REVISION_CONFLICT_STATUS))
    for field, status in pairs:
        # Сводка описывает ПРОХОД; пересчёт по модели — накопленный
        # итог. Проход не может превышать итог; ревизия — точная.
        if summary.get(field, 0) > recount[status]:
            problems.append(f"сводка: {field}={summary.get(field)} "
                            f"больше пересчёта по модели "
                            f"({recount[status]} «{status}»)")
    revision = doc.get("meta", {}).get("revision", 1)
    if summary.get("revision") != revision:
        problems.append(f"сводка: ревизия {summary.get('revision')} "
                        f"не равна meta.revision ({revision})")
    # Замечания чтения книги (аудит 04.09, п. A3.2): книга чужой
    # ревизии, переизданный вопрос, противоречащие дубли, лист вне
    # частей, неизвестный вопрос — каждое есть потерянный или
    # отклонённый ответ авиакомпании; гейт краснеет, стадия не
    # закрывается «ответы применены» молча.
    read_problems = summary.get("read_problems") or []
    if not isinstance(read_problems, list):
        problems.append("сводка: read_problems не список")
        read_problems = []
    for p in read_problems:
        problems.append(f"чтение книги: {p}")
    # Отброшенные патчи обработчика (ИР-2): причина обязательна,
    # вопрос существует и честно остался «ожидает обработчика».
    q_by_id = {q.get("id"): q for q in doc.get("questions", [])}
    for qid, q in q_by_id.items():
        if (q.get("answer") or {}).get("status") == m.PARTIAL_ANSWER_STATUS:
            from cdpm import answers
            pending = ", ".join(r["id"] for r in answers._affected_rules(doc, q))
            problems.append(f"{qid}: ответ применён частично — остались строки "
                            f"без результата обработчика: {pending or 'не определены — проверьте модель'}")
    for rej in summary.get("worker_rejected", []):
        qid = rej.get("question")
        if not rej.get("reasons"):
            problems.append(f"сводка: отброшенный патч {qid} без "
                            f"причины (fail-closed)")
        q = q_by_id.get(qid)
        if q is None:
            problems.append(f"сводка: отброшенный патч указывает на "
                            f"несуществующий вопрос {qid!r}")
        elif (q.get("answer") or {}).get("status") \
                not in ("ожидает обработчика", m.PARTIAL_ANSWER_STATUS):
            problems.append(f"сводка: патч {qid} отброшен, но статус "
                            f"ответа {(q.get('answer') or {}).get('status')!r} "
                            f"— несогласованность")

    if svod_path is not None:
        if not svod_path.is_file():
            problems.append(f"свод {svod_path} не существует")
        elif svod_path.read_text(encoding="utf-8") \
                != render.render_svod(doc):
            problems.append("свод не совпадает с пересборкой из "
                            "модели — правка мимо модели")
    if anketa_path is not None:
        if not anketa_path.is_file():
            problems.append(f"анкета {anketa_path} не существует")
        elif anketa_path.read_text(encoding="utf-8") \
                != render.render_anketa(doc):
            problems.append("анкета не совпадает с пересборкой из "
                            "модели — правка мимо модели")
    if book_path is not None:
        book_problems, _ = gate_anketa_xlsx.check_workbook(doc,
                                                           book_path)
        problems += [f"книга: {p}" for p in book_problems]

    open_q = len(m.open_questions(doc))
    counters = {"questions": len(doc.get("questions", [])),
                "open": open_q,
                "applied": recount["применён"],
                "awaiting": recount["ожидает обработчика"],
                "unparsed": recount["не разобран"],
                "revision": revision}
    if recount[m.REVISION_CONFLICT_STATUS]:
        counters["conflicted"] = recount[m.REVISION_CONFLICT_STATUS]
    if recount[m.PARTIAL_ANSWER_STATUS]:
        counters["partial"] = recount[m.PARTIAL_ANSWER_STATUS]
    return problems, counters


def run(model: str, summary: str, svod: str | None,
        anketa: str | None, book: str | None) -> int:
    _selftest()
    problems, c = check(Path(model), Path(summary),
                        Path(svod) if svod else None,
                        Path(anketa) if anketa else None,
                        Path(book) if book else None)
    if c:
        print(f"рассмотрено: вопросов {c['questions']} (открытых "
              f"{c['open']}), применено {c['applied']}, ожидает "
              f"обработчика {c['awaiting']}, не разобрано "
              f"{c['unparsed']}; ревизия {c['revision']} (самотест "
              f"пройден)"
              + (f"; конфликтов пересмотра {c['conflicted']}"
                 if c.get("conflicted") else ""))
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: применение ответов соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Гейт применения ответов анкеты (ИР-1)")
    ap.add_argument("model", help="model.json после применения")
    ap.add_argument("summary", help="answers-summary.json прохода")
    ap.add_argument("--svod", help="svod.md для побайтной сверки")
    ap.add_argument("--anketa", help="anketa.md для побайтной сверки")
    ap.add_argument("--book", help="anketa.xlsx для сверки содержимого")
    args = ap.parse_args(argv)
    return run(args.model, args.summary, args.svod, args.anketa,
               args.book)


if __name__ == "__main__":
    raise SystemExit(main())
