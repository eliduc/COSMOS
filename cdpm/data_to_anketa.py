# -*- coding: utf-8 -*-
"""Вынос находок отчёта 3.3 в анкету (линия данных, пакет ДЛ-6).

Решение владельца 2026-08-18: спорные прочтения, вскрытые проверками
данных (правило «36 часов между цепочками», отсутствующие коды
допусков), уходят ВОПРОСАМИ части 2 анкеты — толкует авиакомпания,
не мы. Механика — по образцу арбитражного «в анкету»:

- вопрос части 2 со стабильным идентификатором, трассировкой к
  строкам свода-основаниям затронутых проверок и к их источникам;
- строки-основания БЛОКИРУЮТСЯ до ответа (fail-closed: спорная строка
  не компилируется в проверки — они честно получают «не
  скомпилирована: строка блокирована вопросом»);
- свод и анкета перерендериваются из модели; контракт П-1 обязателен.

    python -m cdpm.data_to_anketa MODEL.json ОТЧЁТ.json \
        --checks C-…[,C-…] --title "…" --text "…" --out-dir КАТАЛОГ
"""
from __future__ import annotations

import json
from pathlib import Path

from cdpm import model as m


class PromoteError(ValueError):
    """Честный отказ: причина всегда названа."""


def promote_to_question(svod: dict, report: dict, check_ids: list[str],
                        title: str, text: str,
                        options: list[dict]) -> str:
    """Добавляет в модель свода ОДИН вопрос части 2 по группе проверок
    отчёта 3.3; возвращает идентификатор вопроса. Строки-основания
    блокируются до ответа. Модель меняется на месте; валидацию по
    контракту делает вызывающий (гейт)."""
    checks = {c["id"]: c for c in report.get("checks", [])}
    rules_by_id = {r["id"]: r for r in svod.get("rules", [])}
    rule_refs: list[str] = []
    source_refs: list[dict] = []
    for cid in check_ids:
        check = checks.get(cid)
        if check is None:
            raise PromoteError(f"проверки {cid!r} нет в отчёте")
        rid = (check.get("basis") or {}).get("rule")
        if not rid:
            raise PromoteError(f"проверка {cid} без строки-основания "
                               f"— в анкету выносятся вопросы к "
                               f"правилам")
        rule = rules_by_id.get(rid)
        if rule is None:
            raise PromoteError(f"строки {rid!r} нет в своде")
        if rid not in rule_refs:
            rule_refs.append(rid)
        for src in rule.get("sources", []):
            if src not in source_refs:
                source_refs.append(dict(src))
    if not rule_refs:
        raise PromoteError("группа проверок пуста — вопрос не к чему "
                           "привязать")
    qid = m.stable_question_id("2", "дл-" + "-".join(sorted(check_ids)))
    if any(q.get("id") == qid for q in svod.get("questions", [])):
        raise PromoteError(f"вопрос {qid} уже есть в модели — повторный "
                           f"вынос той же группы")
    svod.setdefault("questions", []).append({
        "id": qid, "part": "2", "title": title, "text": text,
        # Часть 2 по контракту — развилка с закрытым перечнем
        # чтений («развилка вместо выбора»), не свободный текст.
        "answer_format": {"type": "choice", "options": options},
        "rule_refs": rule_refs,
        "source_refs": source_refs,
    })
    for rid in rule_refs:
        blockers = rules_by_id[rid].setdefault("blockers", [])
        if qid not in blockers:
            blockers.append(qid)
    return qid


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    from cdpm import render
    ap = argparse.ArgumentParser(
        description="Вынос находок отчёта 3.3 в анкету (часть 2)")
    ap.add_argument("model")
    ap.add_argument("report")
    ap.add_argument("--checks", required=True,
                    help="идентификаторы проверок через запятую")
    ap.add_argument("--title", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--options", required=True,
                    help="варианты вида «а=текст;б=текст»")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)
    svod = m.load_model(args.model)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    options = []
    for part in args.options.split(";"):
        key, _, text_ = part.partition("=")
        if key.strip() and text_.strip():
            options.append({"key": key.strip(),
                            "text": text_.strip()})
    qid = promote_to_question(svod, report,
                              [c.strip() for c in
                               args.checks.split(",") if c.strip()],
                              args.title, args.text, options)
    problems = m.validate_schema(svod) + m.validate_model(svod)
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m.dump_model(svod, out / "model.json")
    (out / "svod.md").write_text(render.render_svod(svod),
                                 encoding="utf-8")
    (out / "anketa.md").write_text(render.render_anketa(svod),
                                   encoding="utf-8")
    print(f"вопрос добавлен: {qid}")
    print(f"модель: {out / 'model.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
