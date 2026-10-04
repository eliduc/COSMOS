# -*- coding: utf-8 -*-
"""Блок «четыре принципа ВМП» (4П владельца, 2026-09-02) для карточки
прогона: по каждому принципу — числа, которые уже считает код, и
светофор. Ничего не решает и не лечит — только сводит машинные факты
модели, покрытия клауз и матрицы готовности в один взгляд.

    summary(doc, coverage=None, readiness=None, kit_entries=None,
            measure_report=None, facts_doc=None, facts_notes=None)
        -> {"P1": …, …, "P4": …}

Принцип 4 («Биекция», A11): факты и вердикт — из единого расчёта
cdpm.coverage.bijection_facts, того же, что судит замок выпуска пакета
и гейт пакета; kit_entries — комплект заморозки (судья висячих якорей),
coverage — сводка coverage.run_report, measure_report — отчёт
измерителя (coverage.run_measure): с ним слова о содержании те же, что
в отказе выпуска (имя клаузы и величины).

Предел заявления принципа 4 (A12-заявления, контракт П0 v1.7 §9):
строка называет ФАКТЫ ПОКРЫТИЯ — клаузы с вердиктом, якоря, строки
свода, величины, содержание, — и смысловой полнотой они не являются.
Покрытая клауза не равна всем обязательствам внутри неё: два
обязательства в одном абзаце (числовой предел и нечисловой допуск)
дают один покрытый якорь и одно учтённое число. Поэтому у P4 есть
поле `meaning_completeness` (слова cdpm.coverage.MEANING_GAP): что
именно не доказано и чем это закрывается (ведомость атомарных
обязательств, волна 19). Признака, объявляющего смысловую полноту
доказанной, у блока нет; зелёный P4 означает ровно перечисленные
факты и не больше.

facts_doc — свод, по которому судится ИМЕННО принцип 4: замок выпуска
судит свод ПОСЛЕ машинного лечения сборкой, и панель обязана судить
его же (coverage.package_doc), иначе индикатор и выпуск расходятся на
строках, которые лечение снимает. Принципы 1–3 остаются о
КАНОНИЧЕСКОМ своде прогона: панель показывает состояние модели, а не
её вид в пакете. facts_notes — слова, которые читатель обязан
предъявить рядом с фактами (свод к виду сборки не приведён): они
краснят индикатор, потому что незнание успехом не считается.
"""
from __future__ import annotations

from cdpm import model as m


def _part(q: dict) -> str:
    return str(q.get("part") or "")


def _reconsidered_history(q: dict, doc: dict | None) -> bool:
    """Hide only history with a complete, unambiguous applied replacement.

    Reconsideration uses revision_of, not edition_of. Keep broken links and
    unfinished replacements visible; this display predicate grants no release.
    """
    from cdpm import question_editions

    if not isinstance(doc, dict):
        return False
    questions, rules = doc.get("questions"), doc.get("rules")
    if not isinstance(questions, list) or not isinstance(rules, list):
        return False
    if any(not isinstance(x, dict) or not isinstance(x.get("id"), str)
           or not x["id"] for x in questions):
        return False
    by_id = {x["id"]: x for x in questions}
    root = q.get("revision_of") or q.get("id")
    if (len(by_id) != len(questions) or not isinstance(root, str)
            or root not in by_id or by_id[root].get("revision_of")
            or not isinstance(q.get("id"), str) or by_id.get(q["id"]) is not q):
        return False
    target = q["id"]
    q = by_id[root]
    seen = set()
    while True:
        qid = q.get("id")
        answer = q.get("answer")
        if (qid in seen or by_id.get(qid) is not q
                or not isinstance(answer, dict)
                or question_editions.problems(q) or m.is_open_question(q, doc=doc)):
            return False
        seen.add(qid)
        if answer.get("status") == "применён":
            break
        if answer.get("status") != "пересмотрен":
            return False
        revised_by = answer.get("revised_by")
        successor = by_id.get(revised_by) if isinstance(revised_by, str) else None
        if (successor is None or successor.get("revision_of") != root
                or any(successor.get(k) != q.get(k) for k in
                       ("part", "rule_refs", "source_refs", "answer_format", "parameter_contract"))):
            return False
        q = successor
    # Every reconsideration of this root must belong to the one linked chain,
    # including when the caller is an intermediate historical answer.
    family = {root} | {x["id"] for x in questions if x.get("revision_of") == root}
    if target not in seen or seen != family:
        return False
    for r in rules:
        if not isinstance(r, dict):
            return False
        blockers, conditional = r.get("blockers", []), r.get("conditional", {})
        if (not isinstance(blockers, list)
                or any(not isinstance(x, str) or not x for x in blockers)
                or not isinstance(conditional, dict)):
            return False
        question = conditional.get("question")
        if "conditional" in r and (not isinstance(question, str) or not question):
            return False
        if seen.intersection(blockers) or question in seen:
            return False
    return True


def _open(q: dict, *, doc: dict | None = None) -> bool:
    if (isinstance(q.get("answer"), dict)
            and q["answer"].get("status") == "пересмотрен"
            and _reconsidered_history(q, doc)):
        return False
    return m.is_open_question(q, doc=doc) or \
        (q.get("answer") or {}).get("status") not in (None, "применён") \
        if isinstance(q.get("answer"), dict) else m.is_open_question(q, doc=doc)


def summary(doc: dict, coverage: dict | None = None,
            readiness: dict | None = None,
            kit_entries: list[dict] | None = None,
            measure_report: dict | None = None,
            facts_doc: dict | None = None,
            facts_notes: list[str] | None = None) -> dict:
    questions = m.current_questions(doc)
    rules = [r for r in doc.get("rules", []) if isinstance(r, dict)]
    active = [r for r in rules if r.get("kind") in ("HARD", "SOFT", "CASE")]
    open_by_part: dict = {}
    total_by_part: dict = {}
    for q in questions:
        p = _part(q)
        key = "3" if p.startswith("3") else p
        total_by_part[key] = total_by_part.get(key, 0) + 1
        if _open(q, doc=doc):
            open_by_part[key] = open_by_part.get(key, 0) + 1
    param_conflicts = sum(1 for q in questions if "парам-" in str(q.get("id")))

    # P2: посадки по категориям и B без подтверждения.
    fit_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "none": 0}
    b_pending = 0
    open_ids = {q.get("id") for q in questions if _open(q, doc=doc)}
    for r in active:
        fit = r.get("template_fit")
        cat = fit.get("category") if isinstance(fit, dict) else None
        fit_counts[cat if cat in fit_counts else "none"] += 1
        if cat == "B" and (fit or {}).get("pending_question") in open_ids:
            b_pending += 1

    # P3: мягкие без веса, целевые по зонам.
    soft_no_weight = [r["id"] for r in active
                      if r.get("kind") == "SOFT" and r.get("weight") is None]
    objectives = [o for o in doc.get("objectives", []) if isinstance(o, dict)]
    obj_zones = sorted({str(o.get("zone")) for o in objectives})
    excluded = [r["id"] for r in rules if r.get("excluded_until_data")]

    # P4: ЕДИНЫЙ расчёт фактов биекции (A11, аудит 11.09.2026) — те же
    # факты и тот же вердикт, что у замка выпуска пакета и гейта пакета
    # (cdpm.coverage.bijection_facts): строки без источника, висячие
    # якоря (по комплекту заморозки), клаузы без вердикта, частично
    # покрытые клаузы, величины без адресата, нормативные носители без
    # строк. Незнание (нет комплекта, сводки или измерения) успехом не
    # считается — индикатор зелёный только там, где выпуск прошёл бы.
    # Судится свод В ВИДЕ СБОРКИ (facts_doc, coverage.package_doc) — тот
    # же вход, что у замка выпуска; не дан — канонический (панель тогда
    # вправе быть строже сборки, но не мягче).
    from cdpm import coverage as cov_mod
    facts = cov_mod.bijection_facts(facts_doc if facts_doc is not None
                                    else doc, coverage,
                                    measure_report=measure_report,
                                    kit_entries=kit_entries)
    notes = [str(x) for x in facts_notes or ()]
    p4_problems = notes + list(facts["problems"])
    return {
        # Замок части 1 (слово владельца 11.09.2026): панель называет
        # основание честно — противоречием считается ТОЛЬКО машинно
        # видимое расхождение значений одной нормы (свидетельство
        # cdpm.contradiction либо конфликт одноимённых параметров).
        # Чтение модели без такого свидетельства живёт вопросом части 2
        # и в этой строке не считается.
        "P1": {"title": "Непротиворечивость: противоречия — только "
                        "машинно видимые",
               "basis": "вопрос части 1 рождается только при машинном "
                        "свидетельстве разных значений одной нормы "
                        "(одна семья посадки или один предмет и одно "
                        "имя параметра, области применимости "
                        "совпадают); чтение модели без свидетельства "
                        "уходит уточнением в часть 2 и строки не "
                        "блокирует",
               "open_contradictions": open_by_part.get("1", 0),
               "contradictions_total": total_by_part.get("1", 0),
               "param_conflicts": param_conflicts,
               "ok": open_by_part.get("1", 0) == 0},
        "P2": {"title": "Определённость",
               "fit": fit_counts, "b_pending": b_pending,
               "open_forks": open_by_part.get("2", 0),
               "forks_total": total_by_part.get("2", 0),
               "ok": open_by_part.get("2", 0) == 0 and fit_counts["D"] == 0
               and b_pending == 0},
        "P3": {"title": "Полнота постановки",
               "open_statement_questions": open_by_part.get("3", 0),
               "soft_without_weight": soft_no_weight,
               "objectives": len(objectives), "objective_zones": obj_zones,
               "excluded_until_data": excluded,
               "readiness": readiness,
               "ok": open_by_part.get("3", 0) == 0 and not soft_no_weight
               and bool(objectives)},
        # A12-заявления: заголовок строки называет ПРЕДМЕТ суда —
        # факты покрытия, а не смысловую полноту; предел заявления
        # едет отдельным полем и словами одного носителя.
        "P4": {"title": "Биекция: факты покрытия",
               "basis": "те же факты, что замок выпуска пакета: строки "
                        "без источника, висячие якоря, клаузы без "
                        "вердикта, частично покрытые клаузы, величины "
                        "без адресата, нормативные носители без строк; "
                        "не судившееся успехом не считается; смысловой "
                        "полноты строка не заявляет — " + cov_mod.MEANING_GAP,
               "meaning_completeness": cov_mod.MEANING_GAP,
               "rows_without_source": facts["rows_without_source"],
               "coverage": ({k: coverage.get(k) for k in
                             ("clauses", "covered_by_rows", "not_rule")}
                            | {"missing": len(coverage.get("missing") or [])})
               if coverage else None,
               "dangling_anchors": len(facts["dangling_anchors"]),
               "dangling": facts["dangling_anchors"][:8],
               "anchors_judged": facts["anchors_judged"],
               "content_judged": facts["content_judged"],
               "covered_partially": facts["covered_partially"],
               "values_without_addressee": facts["values_without_addressee"],
               "normative_without_rows": facts["normative_without_rows"],
               "problems": p4_problems,
               "ok": facts["ok"] and not notes},
    }
