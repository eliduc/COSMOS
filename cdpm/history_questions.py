# -*- coding: utf-8 -*-
"""Вопросы анкеты трёх исходов по истории сотрудников (1.17.2).

Решение владельца 05.09.2026: если данных фактических назначений (лист
«История» или «Журнал назначений») хватает — брать; частично — предупредить
и предложить дополнить либо продолжить по частичным; нет — предупредить и
предложить ввести либо продолжить с нулевыми остатками.

Вопрос задаётся только когда в пакете есть строки ростеринга, зависящие от
истории (иначе истории пакету не нужно). Строки вопрос НЕ блокирует:
открытый вопрос части 3б по жёстким строкам сам переводит готовность
ростеринга в stop (матрица готовности 1.15), ответ применяется без
изменения строк и уходит в manifest.history.user_decision.

Каталог 0.5.0: вопрос задаётся ПО ВИДУ ЧТЕНИЯ ИСТОРИИ — один блок анкеты
на вид (`reads_history.kind` записи семьи), встретившийся среди семей
ВМП, идентификатор `Q-3b-история-<вид>`. Что именно нужно правилу,
берётся из записи каталога (`reads_history.need`): рукописных перечней
потребностей в коде больше нет, носитель один. Восемь вопросов на
маленьком наборе были бы шумом, один общий — неправдой: виды читают
разные листы данных.
"""
from __future__ import annotations

from cdpm import history_section as hs
from cdpm import model as m

#: Заголовок блока по полноте данных; хвостом идёт потребность в истории
#: словами каталога — она и отличает блоки разных видов друг от друга.
_TITLE = {
    "partial": "История сотрудников передана не полностью",
    "none": "Истории сотрудников в данных нет",
}
#: Дословный текст ответа, которым линия данных закрывает устаревший вопрос
#: (по нему же вопрос узнаётся как снятый кодом и открывается снова).
CODE_CLOSED_RAW = "снят кодом: состояние данных истории изменилось"
#: Потребность строк, зависящих от истории по СВОЕМУ блоку логики: семьи
#: с чтением истории у них нет, и назвать читаемое словами каталога
#: нечем — блок называет остатки общим словом (`HISTORY_KIND_BY_LOGIC`).
NEED_BY_LOGIC = "остатки на начало горизонта"


def _window_needs(rule: dict) -> list[str]:
    """Счётчик в скользящем окне (окно в сутках по семье —
    WINDOW_DAY_PARAMS): окно через границу горизонта судится по
    посуточному календарю. Это потребность САМОЙ СТРОКИ (число суток
    названо её параметром), а не семьи, и каталог её знать не может."""
    out = []
    for name in hs.WINDOW_DAY_PARAMS:
        win = hs._rule_param_value(rule, name)
        if win is not None:
            out.append(f"календарь суток за скользящее окно {win} сут. "
                       f"(счётчик окна через границу горизонта)")
    return out


def blocks_of(rules: list[dict]) -> list[dict]:
    """Блоки анкеты по видам чтения истории: [{"read_kind", "rules",
    "needs"}] в порядке словаря каталога, последним — блок строк,
    зависящих от истории по своему блоку логики (вида чтения у них нет).

    Строка попадает в блок каждого вида, который читает ЕЁ семья; строка
    без такой семьи — в блок логики. Потребности («что именно нужно»)
    идут из записей каталога семей этого вида плюс потребности самой
    строки (календарь суток за её окно)."""
    reads = hs.history_reads()
    order = list(hs.history_read_kinds()) + [hs.HISTORY_KIND_BY_LOGIC]
    by_kind: dict[str, dict] = {}
    for rule in rules:
        fams = sorted(f for f in hs.rule_families(rule) if f in reads)
        kinds = {reads[f]["kind"] for f in fams} or {hs.HISTORY_KIND_BY_LOGIC}
        extra = _window_needs(rule)
        for kind in kinds:
            block = by_kind.setdefault(kind, {"read_kind": kind, "rules": [],
                                              "needs": []})
            block["rules"].append(rule)
            needs = [reads[f]["need"] for f in fams
                     if reads[f]["kind"] == kind] or [NEED_BY_LOGIC]
            for need in needs + extra:
                if need not in block["needs"]:
                    block["needs"].append(need)
    return [by_kind[k] for k in order if k in by_kind]


def _text(coverage: str, block: dict, section: dict,
          tables: dict | None = None) -> str:
    cov = section.get("coverage") or {}
    total, with_h = cov.get("employees_total"), cov.get("with_history")
    rules = block["rules"]
    rule_ids = ", ".join(str(r.get("id")) for r in rules[:6])
    more = "" if len(rules) <= 6 else f" и ещё {len(rules) - 6}"
    head = (f"Правила ростеринга {rule_ids}{more} судятся с учётом истории "
            f"сотрудника до начала горизонта: {'; '.join(block['needs'])}. ")
    if coverage == "partial":
        req, avail = cov.get("depth_days_required"), cov.get("depth_days_available")
        depth_short = req is not None and (avail is None or avail < req)
        if with_h == total and depth_short:
            why = (f"История передана у всех {total} сотрудников, но глубина журнала "
                   f"{avail} дн. меньше требуемых {req} дн. — окна и остатки месяца "
                   f"за недостающие сутки неизвестны.")
        else:
            depth = (f" Глубина журнала {avail} дн. при требуемых {req} дн."
                     if depth_short else "")
            why = (f"История передана у {with_h} из {total} сотрудников.{depth} Без "
                   f"истории у остальных эти правила проверить нельзя.")
        return head + why + " Как поступить?"
    journal_no_as_of = (tables is not None and hs.find_sheet(tables, hs.JOURNAL_SHEETS)
                        is not None and not hs.plan_since(tables))
    if journal_no_as_of:
        why = ("Лист «Журнал назначений» в книге есть, но среза (planSince в листе "
               "«Свойства») нет — вывести остатки из журнала нельзя.")
    else:
        why = (f"В книге данных нет ни листа «История», ни листа «Журнал назначений» "
               f"ни у одного из {total} сотрудников.")
    return head + why + " Без истории эти правила проверить нельзя. Как поступить?"


def _title(coverage: str, block: dict) -> str:
    """Заголовок блока: полнота данных плюс потребность в истории
    словами каталога — по ней блоки разных видов различимы в анкете."""
    return f"{_TITLE[coverage]} — {'; '.join(block['needs'])}"


_OPTIONS = {
    "partial": [
        {"key": "а", "text": "дополнить: прислать лист «История», журнал назначений по "
                             "недостающим сотрудникам или более ранние строки журнала — "
                             "расчёт ростеринга ждёт данных"},
        {"key": "б", "text": "продолжить по частичным данным: правила с историей судятся у "
                             "тех, у кого она есть; у остальных — с явной оговоркой в выдаче "
                             "и сертификате"},
    ],
    "none": [
        {"key": "а", "text": "ввести: прислать лист «История» или журнал назначений — "
                             "расчёт ростеринга ждёт данных"},
        {"key": "б", "text": "продолжить с нулевыми остатками: у всех сотрудников остатки "
                             "на начало горизонта считаются нулём по решению авиакомпании "
                             "(допущение называется словами в выдаче и сертификате)"},
    ],
}


def _source_ref(rules: list[dict]) -> dict | None:
    for r in rules:
        for s in r.get("sources") or []:
            if isinstance(s, dict) and s.get("sid") and s.get("anchor"):
                return {"sid": s["sid"], "anchor": s["anchor"]}
    return None


def ensure_history_questions(doc: dict, tables_doc: dict) -> list[str]:
    """Создать блоки вопроса трёх исходов по состоянию данных — один на
    вид чтения истории, встретившийся среди семей ВМП; вернуть id
    созданных. `tables_doc` — {"tables": ...} книги (замороженный набор
    или очищенные данные). Идемпотентно: блок не создаётся дважды; при
    смене полноты (данные дошли, данные ушли) устаревшие ОТКРЫТЫЕ блоки
    закрываются кодом ключом «неприменимо» с пометкой — иначе они
    держали бы ростеринг в stop."""
    rules = hs.history_dependent_rules(doc)
    section = hs.history_section(tables_doc, doc)
    coverage = (section or {}).get("coverage", {}).get("kind") if section else "none"
    if section is None:
        coverage = "none"
    blocks = blocks_of(rules) if coverage in hs.HISTORY_COVERAGE_KINDS else []
    want = {hs.history_question_id(b["read_kind"]): b for b in blocks}
    questions = doc.setdefault("questions", [])
    if section is None:
        section = {"coverage": {"employees_total": len(hs.employee_ids(
            (tables_doc or {}).get("tables") or {})), "with_history": 0, "kind": "none"}}
    tables = (tables_doc or {}).get("tables")
    created, published = _update_published(doc, want, coverage, section, tables)
    # Устаревшие вопросы истории (другой полноты, другого вида, прежней
    # формы) — закрыть кодом. Применённый ответ при ДРУГОЙ полноте тоже
    # снимается: он был решением о других данных, и держать им замок
    # снятым нельзя; дословный текст прежнего ответа уходит в пометку —
    # решение авиакомпании не теряется молча.
    for q in questions:
        if not isinstance(q, dict) or not hs.is_history_question(q.get("id")):
            continue
        if "edition" in q:
            continue  # immutable records are handled by the edition writer
        if q.get("id") in want and hs.history_question_coverage(q) == coverage:
            continue
        ans = q.get("answer") if isinstance(q.get("answer"), dict) else None
        if ans is not None and ans.get("raw") == CODE_CLOSED_RAW:
            continue
        was = ""
        if ans is not None and ans.get("key") in ("а", "б"):
            was = (f"; прежний ответ «{ans.get('key')}» ({ans.get('raw')}) "
                   f"дан при другой полноте данных и решением не является")
        elif ans is not None and ans.get("status") != "не разобран":
            continue
        # K04 (§2.4 (б)): снятие вопроса кодом идёт единственным
        # писателем — открытый пересмотр с живым носителем он не
        # снимет. Пересмотра у вопроса истории не бывает по построению
        # (`answers._revision_unsupported` отказывает словами), но
        # закон читается здесь так же, как на прочих путях.
        if m.code_close_refusal(doc, q):
            continue
        m.close_question_by_code(
            doc, q, raw=CODE_CLOSED_RAW, key="неприменимо",
            note=f"данные истории изменились (полнота: {coverage}) — "
                 f"вопрос снят линией данных, действует "
                 f"{', '.join(sorted(want)) or 'полная история без вопроса'}"
                 f"{was}")
    if not want:
        return created
    by_id = {q.get("id"): q for q in questions if isinstance(q, dict)}
    for qid in sorted(want):
        if qid in published:
            continue
        block = want[qid]
        existing = by_id.get(qid)
        if existing is not None:
            ans = existing.get("answer") if isinstance(existing.get("answer"), dict) else None
            if ans and ans.get("raw") == CODE_CLOSED_RAW:
                # Исход вернулся: снятый кодом вопрос снова открыт (ответ
                # авиакомпании по нему не давался — терять нечего), текст и
                # полнота обновлены по данным.
                existing.pop("answer", None)
                existing["title"] = _title(coverage, block)
                existing["text"] = _text(coverage, block, section, tables)
                existing["answer_format"] = {"type": "choice", "options": list(_OPTIONS[coverage])}
                existing["history"] = {"read_kind": block["read_kind"],
                                       "coverage": coverage}
                created.append(qid)
            continue
        from cdpm import question_editions
        sources = question_editions.normative_rule_sources(block["rules"])
        if not sources:
            continue
        questions.append({
            "id": qid, "part": "3b", "title": _title(coverage, block),
            "text": _text(coverage, block, section, tables),
            "answer_format": {"type": "choice",
                              "options": list(_OPTIONS[coverage])},
            "history": {"read_kind": block["read_kind"], "coverage": coverage},
            "rule_refs": [str(r.get("id")) for r in block["rules"]],
            "source_refs": sources,
        })
        created.append(qid)
    return created


def _update_published(doc, want, coverage, section, tables):
    """Data changes create editions, never replace a published human answer.

    Retirement also has its own edition and code outcome. If data disappear
    again, a subsequent open edition cannot revive the old permission to use
    zero history. Draft-only legacy records keep their established IDs.
    """
    from cdpm import question_editions as qe
    questions = doc.get("questions", [])
    superseded = {q.get("edition_of") for q in questions}
    created, handled = [], set()
    for q in list(questions):
        if "edition" not in q or not hs.is_history_question(q.get("id")) \
                or q.get("id") in superseded:
            continue
        qe.lineage_root(doc, q)
        kind = (q.get("history") or {}).get("read_kind")
        base = hs.history_question_id(kind) if kind else q["id"]
        block = want.get(base)
        closed = (q.get("answer") or {}).get("raw") == CODE_CLOSED_RAW
        if block is None:
            if closed:
                continue
            reason = (f"Вопрос истории снят линией данных: полнота {coverage}; "
                      f"действующие блоки {', '.join(sorted(want)) or 'не требуются'}.")
            new = qe.expand(doc, q, [], content={"text": reason})
            m.close_question_by_code(doc, new, raw=CODE_CLOSED_RAW,
                                     key="неприменимо", note=reason)
        else:
            handled.add(base)
            content = {"title": _title(coverage, block),
                       "text": _text(coverage, block, section, tables),
                       "answer_format": {"type": "choice", "options": list(_OPTIONS[coverage])},
                       "history": {"read_kind": kind, "coverage": coverage}}
            refs = [str(r["id"]) for r in block["rules"]]
            content["source_refs"] = qe.normative_rule_sources(
                block["rules"], q.get("source_refs") or [])
            changed = any(q.get(key) != value for key, value in content.items())
            if not closed and not changed and set(refs) <= qe.targets(q):
                continue
            new = qe.expand(doc, q, refs, content=content)
        created.append(new["id"])
    return created, handled
