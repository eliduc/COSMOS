# -*- coding: utf-8 -*-
"""Рендеры выходных артефактов CDPM из машинной модели.

Оба артефакта — md-свод (формат свода v1: таблица правил + вопросы) и
md-анкета (части 1–3, в режиме 2 — 4) — чистые функции модели: один и
тот же документ всегда даёт одни и те же байты. На этом стоит гейт
(`gate_artifacts` пересобирает рендер и сравнивает байты): правка
артефакта мимо модели не проживёт ни одного прогона гейта.

Запуск отдельно от приложения:
    python -m cdpm.render model.json --out-dir КАТАЛОГ
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from cdpm import model as m

_DRAFT_NOTICE = (
    "> Черновик уровня «первая ревизия»: свод собран модулем CDPM, статус "
    "READY достигается аудит-циклом по замороженному критерию приёмки. "
    "Блокированные строки не компилируются до ответа на названный вопрос "
    "(fail-closed).")


def _cell(text: str) -> str:
    """Экранирование ячейки md-таблицы: вертикальная черта и перенос
    строки ломают строку таблицы."""
    return text.replace("|", "\\|").replace("\n", "<br>")


def _files_by_sid(doc: dict) -> dict[str, str]:
    return {s["sid"]: s["file"] for s in doc.get("sources", [])}


def _trace(refs: list[dict], files: dict[str, str]) -> str:
    """Единый вид трассировки к источникам: «файл, место»."""
    return "; ".join(
        f"{files.get(ref['sid'], ref['sid'])}, {ref['anchor']}"
        for ref in refs)


def _kind_cell(rule: dict) -> str:
    cond = rule.get("conditional")
    if not cond:
        return rule["kind"]
    branches = "/".join(v for _, v in sorted(cond["branches"].items()))
    return f"{branches} — по Вопросу {cond['question']}"


def _note_cell(rule: dict) -> str:
    parts = []
    for qid in rule.get("blockers", []):
        parts.append(f"**[БЛОКИРОВАНО — см. Вопрос {qid}]**")
    if rule.get("note"):
        parts.append(rule["note"])
    return " ".join(parts) or "—"


def _answer_source_notes(doc: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Show answer-marked model values and link to their exact source.

    This is a reading aid, not acceptance of the value's meaning. In particular,
    a broken exact identity must never fall back to the answer at the chain root.
    """
    from cdpm import provenance

    def label(value) -> str:
        text = str(value).replace("\r", " ").replace("\n", " ")
        fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)),
                              default=0))
        return f"{fence} {text} {fence}"

    notes: dict[str, list[str]] = {}
    sources: dict[str, list[str]] = {}
    typed_cells = set()
    for rule in doc.get("rules") or ():
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        if (isinstance(fit, dict) and fit.get("schema_version") == "landing/2"
                and isinstance(fit.get("params"), dict)):
            typed_cells.update(id(cell) for cell in fit["params"].values())
    for rid, family, name, cell in provenance.answer_cells(doc):
        # Read the same template_fit.params cells as the VMP form. A source
        # quote is not the parsed value; never reparse it while rendering.
        # Typed values carry their own unit. Looking up today's catalogue
        # would silently relabel historical minutes as counts or clock time.
        typed = id(cell) in typed_cells
        unit = cell.get("unit") if typed else m.fit_param_unit(family, name)
        shown_unit = (f"единица: {label(unit)}" if isinstance(unit, str) and unit.strip()
                      else "единица не указана в типизированной ячейке" if typed
                      else "единица не определена каталогом")
        value = cell.get("value")
        shown_value = ("значение не получено" if value is None else
                       "значение в модели: " + label(json.dumps(
                           value, ensure_ascii=False, sort_keys=True, default=str)))
        value_note = f"{label(name)}: {shown_value}; {shown_unit}"
        try:
            src = provenance.cell_source(cell)
            if src is not None:
                q, raw = provenance.resolve_source(doc, src)
            else:
                q, raw = provenance._legacy_raw(doc, cell)
                if q is None:
                    raise provenance.ProvenanceRefusal(raw)
            edition = provenance.question_edition(q)
            version = provenance.answer_version_fingerprint(raw)
        except provenance.ProvenanceRefusal as exc:
            notes.setdefault(rid, []).append(
                f"{value_note}; источник ответа не разрешён — {label(exc)}")
            continue
        identity = json.dumps([edition, version], ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
        anchor = "cdpm-answer-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        title = (f"{label(q['id'])}, издание {edition['revision']}, "
                 f"версия ответа {version}")
        notes.setdefault(rid, []).append(f"{value_note}; [{title}](#{anchor})")
        if anchor not in sources:
            # The raw answer is data, including any Markdown it contains.
            fence = "`" * max(3, 1 + max(
                (len(run) for run in re.findall(r"`+", raw)), default=0))
            sources[anchor] = [f'<a id="{anchor}"></a>', "",
                               f"### Ответ {title}", "",
                               f"Отпечаток издания: {edition['fingerprint']}.", "",
                               fence, raw, fence, ""]
    appendix = ["## Источники значений: полные ответы", ""] if sources else []
    for lines in sources.values():
        appendix.extend(lines)
    return notes, appendix


def _answer_format_lines(q: dict) -> list[str]:
    fmt = q["answer_format"]
    ftype = fmt["type"]
    if ftype == "choice":
        lines = ["Формат ответа (закрытый перечень — выбрать один вариант):"]
        lines += [f"- {o['key']}) {o['text']}" for o in fmt.get("options", [])]
        if fmt.get("note"):
            lines.append(f"- примечание: {fmt['note']}")
        return lines
    label = {"value": "значение", "registry": "реестр (приложить перечень)",
             "text": "свободный текст"}[ftype]
    note = f" — {fmt['note']}" if fmt.get("note") else ""
    return [f"Формат ответа: {label}{note}."]


def _question_block(q: dict, files: dict[str, str],
                    heading: str, with_answer_slot: bool, doc: dict | None = None) -> list[str]:
    lines = [f"{heading} ВОПРОС {q['id']}: {q['title']}", ""]
    if doc is not None and not with_answer_slot and "missing_data" in q:
        from cdpm import question_editions as qe, question_resolutions as qr
        if qe.is_superseded(doc, q):
            lines += [f"Историческое издание; актуальный вопрос — {qe.latest(doc, q)['id']}.", ""]
        elif qr.complete(q) and not qr.problems(doc, q):
            lines += ["Вопрос снят кодом; прежний текст описывает состояние до снятия.", ""]
        lines += [*qr.notes(q, doc=doc), ""] if q.get("code_resolutions") else []
    lines += [q["text"], ""]
    answer = q.get("answer") or {}
    if answer.get("status") == m.REVISION_CONFLICT_STATUS:
        lines += [f"**Пересмотр: конфликт.** {answer.get('note', '')}",
                  f"Неприменённый ответ сохранён: «{answer.get('raw', '')}».", ""]
    from cdpm import answer_feedback
    lines += answer_feedback.lines(q, doc=doc)
    lines += _answer_format_lines(q)
    lines.append("")
    lines.append(f"Трассировка: свод — {', '.join(q['rule_refs'])}; "
                 f"источники — {_trace(q['source_refs'], files)}.")
    for ref in q.get("third_party_refs", []):
        lines.append(f"Файл третьей стороны: {ref['file']}, {ref['anchor']}.")
    if with_answer_slot and answer.get("status") != m.PARTIAL_ANSWER_STATUS:
        # Урок анкет v4–v7: у отвечающего должно быть КУДА писать ответ.
        lines += ["", "Ответ:", ""]
    else:
        lines.append("")
    return lines


def render_svod(doc: dict) -> str:
    meta = doc["meta"]
    files = _files_by_sid(doc)
    answer_notes, answer_sources = _answer_source_notes(doc)
    out = [f"# Формализованный свод правил — {meta['customer']}", ""]
    out += [f"Собран модулем CDPM {meta['cdpm_version']}, "
            f"режим {meta['mode']}."]
    if meta.get("revision", 1) > 1:
        # Ревизия появляется только после применения ответов (ИР-1):
        # прежние модели рендерятся байт-в-байт.
        out += [f"Ревизия свода: {meta['revision']} (с учётом ответов "
                f"авиакомпании)."]
    if meta.get("generated_at"):
        out += [f"Дата сборки: {meta['generated_at']}."]
    out += ["", _DRAFT_NOTICE, ""]

    out += ["## Из чего собран", "",
            "| Источник | Файл | SHA-256 |", "|---|---|---|"]
    for s in doc.get("sources", []):
        out.append(f"| {s['sid']} | {_cell(s['file'])} | `{s['sha256']}` |")
    out.append("")

    out += ["## Часть 1. Свод правил и ограничений", "",
            "| ID | Правило | Этап | Тип | Источник | Примечание |",
            "|---|---|---|---|---|---|"]
    for r in doc.get("rules", []):
        note = _note_cell(r)
        if answer_notes.get(r["id"]):
            note = (note + " " if note != "—" else "") + "; ".join(answer_notes[r["id"]])
        out.append(
            f"| {r['id']} | **{_cell(r['title'])}:** {_cell(r['statement'])} "
            f"| {r['zone']} | {_cell(_kind_cell(r))} "
            f"| {_cell(_trace(r['sources'], files))} "
            f"| {_cell(note)} |")
    out.append("")

    # ЦФ-1: целевые функции — отдельным разделом (конвенция свода v1);
    # каждая целевая — отдельная задача оптимизации своей зоны.
    objectives = doc.get("objectives", [])
    if objectives:
        out += ["## Часть 1а. Целевые функции", "",
                "| ID | Целевая | Направление | Этап | Источник |",
                "|---|---|---|---|---|"]
        for o in objectives:
            direction = ("минимизировать"
                         if o.get("direction") == "minimize"
                         else "максимизировать")
            trace = _trace(o["sources"], files)
            origin = o.get("origin")
            if origin:
                # ЦФ-1 фаза Б: целевая из ответа авиакомпании —
                # происхождение печатается честно, sources несут
                # трассировку вопроса.
                trace = (f"ответ авиакомпании на "
                         f"{origin.get('question')} (ревизия "
                         f"{origin.get('revision')}); {trace}")
            out.append(
                f"| {o['id']} | **{_cell(o['title'])}:** "
                f"{_cell(o['statement'])} | {direction} "
                f"| {o['zone']} "
                f"| {_cell(trace)} |")
        out.append("")

    out += answer_sources
    out += ["## Часть 2. Вопросы (блокеры постановки)", ""]
    for part in m.PARTS:
        qs = [q for q in doc.get("questions", []) if q["part"] == part]
        if not qs:
            continue
        out += [f"### {m.PART_TITLES[part]}", ""]
        for q in qs:
            out += _question_block(q, files, "####", with_answer_slot=False, doc=doc)
    return "\n".join(out).rstrip() + "\n"


_is_open = m.is_open_question


def render_anketa(doc: dict) -> str:
    meta = doc["meta"]
    files = _files_by_sid(doc)
    out = [f"# Анкета вопросов к авиакомпании — {meta['customer']}", ""]
    out += [f"Составлена модулем CDPM {meta['cdpm_version']}, "
            f"режим {meta['mode']}. Анкета — издание вопросов свода: "
            f"каждый пункт ссылается на строки свода и на исходные "
            f"документы (файл и место). Ответы вносятся в свод — после "
            f"этого блокировки снимаются."]
    if meta.get("revision", 1) > 1:
        out += [f"Ревизия анкеты: {meta['revision']} — открытые "
                f"вопросы после применения ответов; принятые ответы — "
                f"в конце документа."]
    out += ["", _DRAFT_NOTICE, ""]
    for part in m.PARTS:
        qs = m.open_questions(doc, part)
        if not qs:
            continue
        out += [f"## {m.PART_TITLES[part]}", ""]
        for q in qs:
            if (q.get("answer") or {}).get("status") == "не разобран":
                out += [f"**Прежний ответ не разобрался** "
                        f"(«{q['answer']['raw']}») — просим ответить "
                        f"в форме, названной ниже.", ""]
            out += _question_block(q, files, "###", with_answer_slot=True, doc=doc)
            from cdpm import question_editions
            for note in question_editions.prior_answer_notes(doc, q):
                out += [f"Источник прежнего ответа: {note}.", ""]
            for note in question_editions.scope_change_notes(doc, q):
                out += [note, ""]
            for note in question_editions.source_change_notes(doc, q, files):
                out += [note, ""]
            from cdpm import question_resolutions
            for note in question_resolutions.notes(q, doc=doc):
                out += [note, ""]
    answered = [q for q in doc.get("questions", []) if not _is_open(q, doc=doc)]
    if answered:
        from cdpm import question_resolutions
        has_code = any(question_resolutions.complete(q)
                       or (q.get("answer") or {}).get("closed_by") == "code"
                       for q in answered)
        title = "Принятые ответы и решения кода" if has_code else "Принятые ответы"
        out += [f"## {title}", ""]
        out += ["| Вопрос | Ответ | Статус | Ревизия |",
                "|---|---|---|---|"]
        for q in answered:
            from cdpm import question_resolutions
            ans = question_resolutions.display_answer(q, doc=doc)
            from cdpm import question_editions
            prior = question_editions.prior_answer_notes(doc, q)
            title = q['title']
            if prior:
                title += "; источник прежнего ответа: " + "; ".join(prior)
            if question_resolutions.notes(q, doc=doc):
                title += "; " + "; ".join(question_resolutions.notes(q, doc=doc))
            shown = ans.get("key") or ans.get("raw", "")
            if ans.get("key") and ans.get("note"):
                shown = f"{ans['key']} ({ans['note']})"
            from cdpm import answer_feedback
            feedback = answer_feedback.lines({"answer": ans})
            if feedback:
                shown += "; " + "; ".join(feedback)
            out.append(f"| {q['id']} — {_cell(title)} "
                       f"| {_cell(shown)} | {ans['status']} "
                       f"| {ans['revision']} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    # Печать кириллицы не должна ронять процесс на консоли cp1251 (У-14).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Рендер md-свода и md-анкеты из машинной модели CDPM")
    ap.add_argument("model", help="путь к model.json")
    ap.add_argument("--out-dir", required=True,
                    help="каталог для svod.md и anketa.md")
    args = ap.parse_args(argv)
    doc = m.load_model(args.model)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svod = out_dir / "svod.md"
    anketa = out_dir / "anketa.md"
    svod.write_text(render_svod(doc), encoding="utf-8")
    anketa.write_text(render_anketa(doc), encoding="utf-8")
    print(f"записано: {svod.resolve()}")
    print(f"записано: {anketa.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
