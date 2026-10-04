# -*- coding: utf-8 -*-
"""Сверка спорного моделью (линия данных, пакет ДЛ-7).

Проверки данных честно фиксируют срабатывания, но срабатывание — ещё
не вердикт: правило могло быть скомпилировано не о том (живой прогон
3.3: условная норма вида «N часов за месяц» легла на длительность
цепочки), либо
данные несут законное исключение. Решение владельца 2026-08-18
(«по 3 даю добро»): отдельная стадия после исполнения проверок —
модель-СВЕРЩИК раскладывает находки-кандидаты на три корзины:

- «похоже на настоящее нарушение» — данные противоречат правилу в его
  прямом чтении;
- «законное исключение» — срабатывание объяснимо без нарушения;
  обоснование обязательно и опирается только на материалы находки;
- «неясно — к владельцу» — по материалам решить нельзя; решает
  человек, не модель.

Разделение труда прежнее: модель ПРЕДЛАГАЕТ вердикт, код валидирует
(закрытый перечень дословно, обоснование непустое, ровно один ответ
на находку); отбраковка и молчание канала — честные «не сверено» с
причиной, не догадка и не смерть прогона (fail-closed чанком).

Сверяются ТОЛЬКО находки «нарушение правила» по прогнанным проверкам:
спор — о чтении правила; встроенные проверки качества (дубль, формат)
механические, спорить не о чем. Находка по блокированной строке свода
не сверяется — её уже решает вопрос анкеты.

Запуск отдельно от приложения:
    python -m cdpm.data_review MODEL.json ОТЧЁТ.json --out-dir КАТАЛОГ \
        [--checks C-…,C-…] [--llm cloud|local|auto]
"""
from __future__ import annotations

import json
from pathlib import Path

from cdpm import data_report as dr
from cdpm import llm as llm_mod

REVIEW_CHUNK = 12        # находок в одном обращении к сверщику


class DataReviewError(ValueError):
    """Честный отказ сверки: причина всегда названа."""


from cdpm import model as m

_PROMPT_REVIEW = """Ты — сверщик спорных находок проверки операционных
данных авиакомпании. Каждая находка ниже утверждает, что запись данных
нарушает правило-основание. По КАЖДОЙ находке дай один вердикт:

- "похоже на настоящее нарушение" — данные противоречат правилу в его
  прямом чтении;
- "законное исключение" — срабатывание объяснимо без нарушения
  (например, проверка мерила не то, что требует правило: другой
  период, другой объект счёта), обоснование ОБЯЗАТЕЛЬНО и опирается
  только на приведённые материалы;
- "неясно — к владельцу" — по материалам решить нельзя.

Верни СТРОГО один JSON-объект без пояснений:

{{"reviews": [{{
  "finding": "<id находки, ровно как в списке>",
  "verdict": "<один из трёх вердиктов, дословно>",
  "basis": "<одно-два предложения обоснования>"
}}, …]}}

Правила работы: ровно один ответ на каждую находку списка; вердикты
только из перечня и дословно; выдумывать правила и данные нельзя —
суди только по приведённому; сомнение — честное "неясно — к владельцу".

Находки ({n}):
{findings}""" + m.ID_IMMUTABILITY_RULE


def select_findings(report: dict, svod: dict,
                    check_ids: list[str] | None = None) -> list[dict]:
    """Находки-кандидаты сверки: «нарушение правила» по прогнанным
    проверкам, ещё не сверенные; блокированное основание — решает
    анкета, не сверка. check_ids сужает выбор до названных проверок."""
    checks = {c["id"]: c for c in report.get("checks", [])}
    rules = {r["id"]: r for r in (svod or {}).get("rules", [])}
    out = []
    for f in report.get("findings", []):
        check = checks.get(f.get("check"))
        if check is None or check.get("status") not in dr.RUN_STATUSES:
            continue
        rid = (check.get("basis") or {}).get("rule")
        if not rid:
            continue
        if check_ids is not None and f["check"] not in check_ids:
            continue
        if f.get("review"):
            continue
        rule = rules.get(rid)
        if rule is not None and rule.get("blockers"):
            continue
        out.append(f)
    return out


def _finding_line(f: dict, checks: dict, rules: dict) -> str:
    check = checks.get(f.get("check"), {})
    rid = (check.get("basis") or {}).get("rule") or "<нет>"
    rule = rules.get(rid, {})
    t = f.get("target") or {}
    where = (f"{t.get('sid')}/{t.get('sheet')} строка {t.get('row')}"
             + (f" колонка {t.get('column')}" if t.get("column")
                else ""))
    return (f"{f['id']} | правило {rid}: "
            f"{rule.get('statement') or '<текст правила недоступен>'} "
            f"| проверка: {check.get('statement', '')} | где: {where} "
            f"| находка: {f.get('text', '')} | доказательство: "
            f"{f.get('evidence', '')}")


def run_review(svod: dict, report: dict, llm,
               check_ids: list[str] | None = None,
               control=None) -> dict:
    """Сверка выбранных находок отчёта: вердикты пишутся в поле review
    находки (модель меняется на месте), счётчики пересчитываются.
    Возвращает сводку прохода: кандидаты, сверено, по вердиктам,
    несверённые с причинами, чужие ответы."""
    checks = {c["id"]: c for c in report.get("checks", [])}
    rules = {r["id"]: r for r in svod.get("rules", [])}
    todo = select_findings(report, svod, check_ids)
    reviewed = 0
    skipped: list[dict] = []
    alien = 0
    for start in range(0, len(todo), REVIEW_CHUNK):
        if control:
            control()          # пауза/остановка между чанками (ПС-1)
        batch = todo[start:start + REVIEW_CHUNK]
        listing = "\n".join(_finding_line(f, checks, rules)
                            for f in batch)
        prompt = _PROMPT_REVIEW.format(n=len(batch), findings=listing)
        reply = None
        for attempt in (1, 2):
            ask = prompt if attempt == 1 else (
                prompt + "\n\nПредыдущий ответ не разобрался. Верни "
                         "СТРОГО один JSON-объект и ничего больше.")
            try:
                reply = llm_mod.parse_json_reply(llm(ask))
                break
            except llm_mod.LLMReplyError:
                continue
        if reply is None:
            # Fail-closed чанком: находки чанка — честные «не сверено»
            # с причиной, конвейер живёт дальше.
            for f in batch:
                skipped.append({"finding": f["id"],
                                "reason": "канал сверки не дал "
                                          "разборного JSON после "
                                          "повтора"})
            continue
        batch_ids = {f["id"] for f in batch}
        got: dict[str, dict] = {}
        for r in reply.get("reviews", []):
            fid = r.get("finding")
            if fid not in batch_ids:
                alien += 1     # ответ о находке вне чанка — отбраковка
                continue
            got.setdefault(fid, r)
        for f in batch:
            r = got.get(f["id"])
            if r is None:
                skipped.append({"finding": f["id"],
                                "reason": "сверщик не дал ответа по "
                                          "находке — неполный пакет"})
                continue
            verdict = (r.get("verdict") or "").strip()
            basis = (r.get("basis") or "").strip()
            if verdict not in dr.REVIEW_VERDICTS:
                skipped.append({"finding": f["id"],
                                "reason": f"вердикт {verdict!r} вне "
                                          f"перечня — отбракован"})
                continue
            if not basis:
                skipped.append({"finding": f["id"],
                                "reason": "вердикт без обоснования — "
                                          "отбракован"})
                continue
            f["review"] = {"verdict": verdict, "basis": basis}
            reviewed += 1
    report["counters"] = dr.metrics_of(report)
    by_verdict = report["counters"].get("review_by_verdict", {})
    return {"candidates": len(todo),
            "reviewed": reviewed,                 # сверено ЭТИМ проходом
            "reviewed_total": sum(by_verdict.values()),   # всего в отчёте
            "by_verdict": by_verdict,
            "skipped": skipped,
            "alien_answers": alien}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Сверка спорного моделью (отчёт 3.3, ДЛ-7)")
    ap.add_argument("model", help="model.json свода")
    ap.add_argument("report", help="машинная модель отчёта 3.3")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--checks",
                    help="сузить до проверок (id через запятую)")
    ap.add_argument("--llm", default="cloud",
                    choices=("cloud", "local", "auto"),
                    help="канал сверщика (умолчание cloud)")
    args = ap.parse_args(argv)
    svod = m.load_model(args.model)
    report = dr.load_report(args.report)
    check_ids = [c.strip() for c in (args.checks or "").split(",")
                 if c.strip()] or None
    llm = llm_mod.cosmos_llm(requested=args.llm)
    summary = run_review(svod, report, llm, check_ids)
    problems = dr.validate_report(report, svod)
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dr.dump_report(report, out / "data-report.json")
    (out / "data-report.md").write_text(dr.render_data_report(report),
                                        encoding="utf-8")
    (out / "review-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    by = ", ".join(f"{k}: {v}" for k, v in summary["by_verdict"].items())
    print(f"кандидатов {summary['candidates']}, сверено "
          f"{summary['reviewed']}" + (f" — {by}" if by else ""))
    print(f"не сверено: {len(summary['skipped'])}")
    print(f"отчёт: {out / 'data-report.json'}")
    print(f"сводка сверки: {out / 'review-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
