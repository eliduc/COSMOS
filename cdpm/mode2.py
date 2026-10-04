# -*- coding: utf-8 -*-
"""Режим 2 (пакет РМ2): сверка формализации ТРЕТЬЕЙ СТОРОНЫ с
источниками — часть 4 анкеты.

Постановка (владелец, 2026-08-16): у заказчика часто уже есть чужая
формализация правил (интегратора, прежней системы). Режим 2 принимает
такой файл, приводит его к перечню утверждений с якорями (той же
заморозкой, что документы) и сверяет каждое утверждение с источниками.
Базой сверки служит ВЫВЕРЕННЫЙ свод CDPM этого прогона (он прошёл
аудит-цикл и арбитраж и несёт трассировку каждой строки к клаузам
источников) — отчёт честно называет базу.

Разделение труда прежнее: модель-СВЕРЩИК предлагает вердикты, КОД
проверяет (закрытый перечень дословно, названная строка свода
существует, чужой якорь существует); отбраковка и молчание канала —
честные «не сверено» с причиной (fail-closed чанком).

Вердикты по каждому утверждению третьей стороны:
- «соответствует» — утверждение согласуется со сводом и источниками;
- «расходится» — противоречит строке свода (обязательна ссылка на
  строку и суть расхождения) → ВОПРОС ЧАСТИ 4 анкеты;
- «в источниках отсутствует» — утверждение не выводится из источников
  (обязательна ближайшая по теме строка свода — «куда смотреть»)
  → ВОПРОС ЧАСТИ 4;
- «неясно» — по материалам решить нельзя (в отчёт, вопрос не
  создаётся автоматически).

Запуск отдельно от приложения:
    python -m cdpm.mode2 КОМПЛЕКТ MODEL.json ЧУЖОЙ_ФАЙЛ --out-dir КАТАЛОГ \
        [--llm cloud|local|auto]
"""
from __future__ import annotations

import json
from pathlib import Path

from cdpm import freeze as freeze_mod
from cdpm import llm as llm_mod
from cdpm import model as m

CHECK_CHUNK = 12
VERDICTS = ("соответствует", "расходится",
            "в источниках отсутствует", "неясно")


class Mode2Error(ValueError):
    """Честный отказ сверки формализации: причина всегда названа."""


_PROMPT_CHECK = """Ты — аудитор ЧУЖОЙ формализации правил авиакомпании.
Ниже — выверенный свод правил (строки с идентификаторами; каждая
трассирована к первоисточникам) и чанк утверждений чужой формализации.
По КАЖДОМУ утверждению дай один вердикт. Верни СТРОГО один JSON-объект:

{{"checks": [{{
  "anchor": "<якорь утверждения, ровно как в списке>",
  "verdict": "соответствует" | "расходится" |
             "в источниках отсутствует" | "неясно",
  "rule": "<id строки свода: для «расходится» — противоречащая строка;
           для «в источниках отсутствует» — ближайшая по теме;
           для «соответствует» — подтверждающая; для «неясно» — можно
           опустить>",
  "note": "<для «расходится» и «в источниках отсутствует» — одно-два
           предложения сути; иначе можно опустить>"
}}, …]}}

Правила: ровно один вердикт на каждое утверждение; вердикты дословно
из перечня; идентификатор строки — только из свода ниже; выдумывать
правила нельзя; редакционные различия формулировок при том же смысле
— «соответствует»; различие ЧИСЕЛ, границ, области действия или
жёсткости — «расходится».

=== СВОД ({n_rules} строк) ===
{rules}

=== УТВЕРЖДЕНИЯ ТРЕТЬЕЙ СТОРОНЫ ({n}) ===
{claims}""" + m.ID_IMMUTABILITY_RULE


def load_third_party(path: Path) -> dict:
    """Чужой файл → перечень утверждений с якорями (той же заморозкой,
    что документы; md/docx/xlsx). Имя и SHA-256 — для трассировки."""
    entry = freeze_mod.extract_file(path)
    if not entry.get("clauses"):
        raise Mode2Error(f"{path.name}: не извлечено ни одного "
                         f"утверждения")
    return {"file": path.name,
            "sha256": freeze_mod.sha256_of(path),
            "clauses": entry["clauses"]}


def _claims_of(entry: dict) -> list[dict]:
    """Утверждения для сверки: непустые клаузы длиннее заголовка."""
    out = []
    for c in entry["clauses"]:
        text = (c.get("text") or "").strip()
        if len(text) >= 25:
            out.append({"anchor": c["anchor"], "text": text})
    return out


def run_mode2(svod: dict, entry: dict, llm,
              control=None) -> tuple[dict, list[dict]]:
    """Сверка утверждений третьей стороны со сводом: возвращает
    (отчёт, вопросы части 4 для добавления в модель). Модель свода НЕ
    меняется здесь — вопросы применяет вызывающий (webui/CLI) с полной
    валидацией контракта."""
    rules_by_id = {r["id"]: r for r in svod.get("rules", [])}
    rules_listing = "\n".join(
        f"{r['id']} | {r['zone']} | {r['kind']} | {r['statement']}"
        for r in svod.get("rules", []))
    claims = _claims_of(entry)
    items: list[dict] = []
    skipped: list[dict] = []
    for start in range(0, len(claims), CHECK_CHUNK):
        if control:
            control()          # пауза/остановка между чанками (ПС-1)
        batch = claims[start:start + CHECK_CHUNK]
        listing = "\n".join(f"[{c['anchor']}] {c['text']}"
                            for c in batch)
        prompt = _PROMPT_CHECK.format(
            n_rules=len(rules_by_id), rules=rules_listing,
            n=len(batch), claims=listing)
        reply = None
        for attempt in (1, 2):
            ask = prompt if attempt == 1 else (
                prompt + "\n\nПредыдущий ответ не разобрался. Верни "
                         "СТРОГО один JSON-объект и ничего больше.")
            try:
                reply = llm_mod.parse_json_reply(llm(ask))
                break
            except (llm_mod.LLMReplyError, llm_mod.LLMCallError):
                continue
        got = {}
        for c in (reply or {}).get("checks", []):
            if isinstance(c.get("anchor"), str):
                got.setdefault(c["anchor"], c)
        for c in batch:
            r = got.get(c["anchor"])
            if r is None:
                skipped.append({"anchor": c["anchor"],
                                "reason": "сверщик не дал вердикта — "
                                          "неполный пакет или отказ "
                                          "канала"})
                continue
            verdict = (r.get("verdict") or "").strip()
            rule_id = r.get("rule")
            note = (r.get("note") or "").strip()
            if verdict not in VERDICTS:
                skipped.append({"anchor": c["anchor"],
                                "reason": f"вердикт {verdict!r} вне "
                                          f"перечня — отбракован"})
                continue
            if verdict in ("расходится", "в источниках отсутствует"):
                if not isinstance(rule_id, str) \
                        or rule_id not in rules_by_id:
                    skipped.append(
                        {"anchor": c["anchor"],
                         "reason": f"вердикт «{verdict}» со строкой "
                                   f"{rule_id!r} вне свода — "
                                   f"отбракован"})
                    continue
                if not note:
                    skipped.append(
                        {"anchor": c["anchor"],
                         "reason": f"вердикт «{verdict}» без сути "
                                   f"расхождения — отбракован"})
                    continue
            items.append({"anchor": c["anchor"], "text": c["text"],
                          "verdict": verdict,
                          "rule": rule_id if isinstance(rule_id, str)
                          and rule_id in rules_by_id else None,
                          "note": note})
    by_verdict = {v: 0 for v in VERDICTS}
    for it in items:
        by_verdict[it["verdict"]] += 1
    report = {"file": entry["file"],
              "sha256": entry.get("sha256", ""),
              "base": "выверенный свод CDPM (аудит-цикл + арбитраж) с "
                      "трассировкой к источникам",
              "claims_total": len(claims),
              "checked": len(items),
              "by_verdict": {k: v for k, v in by_verdict.items() if v},
              "items": items,
              "skipped": skipped}
    questions = _build_questions(svod, entry, items)
    return report, questions


def _build_questions(svod: dict, entry: dict,
                     items: list[dict]) -> list[dict]:
    """Вопросы части 4 по расхождениям и изобретённому: формулировки —
    из материалов сверки, без дополнительной модели."""
    rules_by_id = {r["id"]: r for r in svod.get("rules", [])}
    out = []
    for it in items:
        if it["verdict"] == "расходится":
            title = f"Расхождение формализации: {it['anchor']}"
            text = (f"Формализация третьей стороны утверждает: "
                    f"«{it['text'][:300]}». Наше чтение источников "
                    f"({it['rule']}): {it['note']} Какое чтение "
                    f"верно?")
            options = [
                {"key": "а", "text": "верна формализация третьей "
                                     "стороны — источники читаются "
                                     "так"},
                {"key": "б", "text": f"верно чтение источников по "
                                     f"строке {it['rule']} — чужую "
                                     f"формализацию исправить"},
            ]
        elif it["verdict"] == "в источниках отсутствует":
            title = f"Утверждение без источника: {it['anchor']}"
            text = (f"Формализация третьей стороны содержит "
                    f"утверждение, которое не выводится из переданных "
                    f"документов: «{it['text'][:300]}». {it['note']} "
                    f"Подтвердите основание или снимите требование.")
            options = [
                {"key": "а", "text": "требование действует — передадим "
                                     "недостающий документ-основание"},
                {"key": "б", "text": "требования нет — исключить из "
                                     "формализации"},
            ]
        else:
            continue
        rule = rules_by_id[it["rule"]]
        qid = m.stable_question_id("4", it["anchor"])
        out.append({
            "id": qid, "part": "4", "title": title, "text": text,
            "answer_format": {"type": "choice", "options": options},
            "rule_refs": [it["rule"]],
            "source_refs": [dict(s) for s in rule.get("sources", [])],
            "third_party_refs": [{"file": entry["file"],
                                  "anchor": it["anchor"]}],
        })
    return out


def apply_questions(svod: dict, questions: list[dict]) -> int:
    """Добавление вопросов части 4 в модель (на месте): режим 2,
    дубли по идентификатору пропускаются. Валидацию контрактом делает
    вызывающий (гейт)."""
    svod.setdefault("meta", {})["mode"] = 2
    known = {q.get("id") for q in svod.get("questions", [])}
    added = 0
    for q in questions:
        if q["id"] in known:
            continue
        svod.setdefault("questions", []).append(q)
        added += 1
    return added


def render_report_md(report: dict) -> str:
    """Аудиторский md-отчёт сверки — детерминированный рендер."""
    out = [f"# Аудит формализации третьей стороны — {report['file']}",
           "",
           f"База сверки: {report['base']}.",
           f"SHA-256 файла: `{report['sha256']}`.", "",
           f"Утверждений рассмотрено: {report['claims_total']}; "
           f"сверено: {report['checked']}; не сверено: "
           f"{len(report['skipped'])}."]
    by = ", ".join(f"{k} {v}" for k, v in report["by_verdict"].items())
    if by:
        out.append(f"По вердиктам: {by}.")
    out += ["", "| Якорь | Вердикт | Строка свода | Суть |",
            "|---|---|---|---|"]
    for it in report["items"]:
        if it["verdict"] == "соответствует":
            continue
        out.append(f"| {it['anchor']} | {it['verdict']} | "
                   f"{it['rule'] or '—'} | "
                   f"{(it['note'] or '—').replace('|', '\\|')} |")
    if report["skipped"]:
        out += ["", "## Не сверено", ""]
        for s in report["skipped"]:
            out.append(f"- {s['anchor']}: {s['reason']}")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    from cdpm import anketa_xlsx, render
    ap = argparse.ArgumentParser(
        description="Режим 2: сверка чужой формализации с источниками")
    ap.add_argument("model", help="model.json свода CDPM")
    ap.add_argument("third", help="файл формализации третьей стороны")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--llm", default="cloud",
                    choices=("cloud", "local", "auto"))
    args = ap.parse_args(argv)
    svod = m.load_model(args.model)
    entry = load_third_party(Path(args.third))
    llm = llm_mod.cosmos_llm(requested=args.llm)
    report, questions = run_mode2(svod, entry, llm)
    added = apply_questions(svod, questions)
    problems = m.validate_schema(svod) + m.validate_model(svod)
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m.publish_model(svod, out / "model.json")
    (out / "svod.md").write_text(render.render_svod(svod),
                                 encoding="utf-8")
    (out / "anketa.md").write_text(render.render_anketa(svod),
                                   encoding="utf-8")
    anketa_xlsx.render_anketa_xlsx(svod, out / "anketa.xlsx")
    (out / "third-party-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (out / "third-party-report.md").write_text(
        render_report_md(report), encoding="utf-8")
    print(f"сверено {report['checked']} из {report['claims_total']}, "
          f"вопросов части 4 добавлено {added}")
    print(f"отчёт: {out / 'third-party-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
