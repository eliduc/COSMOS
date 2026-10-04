# -*- coding: utf-8 -*-
"""Точное происхождение значения ячейки посадки — закон CDPM
(контракт П0 §2.3, минимальная часть A03-мин/K05-мин, 12.09.2026).

Предмет. Значение параметра семьи, внесённое в посадку правила
ответом авиакомпании, обязано ссылаться на ТОЧНУЮ версию того
ответа, из которой оно прочитано, а не на «вопрос вообще». Иначе
значение можно приписать любому применённому ответу: живой
контрпример аудита — источник «не более 600 минут», ответ «Без
изменений», заплата меняет значение на 660 и добавляет ссылку на
этот ответ; приём заплаты, модель, измеритель и гейт пакета
проходят, в выпущенном файле остаётся 660.

Закон (общий, без упоминания набора и семьи):

1. При внесении значения ячейка получает `source` — объект
   происхождения: вид (`answer`), точное издание вопроса
   (`{id, revision, fingerprint}`), отпечаток версии ответа
   (`version.fingerprint`), адрес эффекта (`effect = {rule, field}`)
   и корень родословной (`root`). Прежняя строка `answer` (id
   корневого вопроса) остаётся рядом: читатели, знающие только её,
   не ломаются (миграция — волна 17б).
2. Обратный ход — один: `resolve_source(doc, source)` находит запись
   по ТОЧНОЙ идентичности (идентификатор издания + отпечаток издания
   + отпечаток версии ответа), а не «первый применённый по порядку
   списка». Несовпадение любого отпечатка — отказ с кодом
   `answer-provenance` и словами, называющими ячейку, вопрос и оба
   отпечатка. Поблажки «сойдёт корень цепочки» нет: прямо сейчас
   применяется РОВНО отвечаемый вопрос, и только он назван в
   `applying` — иначе заплата на вопрос-пересмотр `X-R` оправдывала бы
   значение пересмотренным ответом на `X`.
2а. Ячейка СТАРОЙ формы (одна строка `answer`, без `source`) точной
   версии не несёт, и форма ячейки закон не отменяет: её «точная
   версия» — ДЕЙСТВУЮЩИЙ ответ цепочки, то есть последний по ссылкам
   `revised_by` (пересмотренный ответ не действует). Цитата и значение
   судятся против его `raw`; нет действующего ответа — отказ. Завести
   такую ячейку или изменить её заплатой нельзя вовсе
   (`patch_problems`): новые ячейки — только с точным происхождением,
   иначе форма ячейки была бы обходом закона (находка ревью).
3. `quote` ячейки — дословная подстрока `raw` ИМЕННО той версии; а
   `value` ячейки читается из этой цитаты грамматикой K02
   (`family_params.parse_value`). Минимальная часть судит ПОДЛИННОСТЬ
   подстроки и ЧИТАЕМОСТЬ числа из неё, но НЕ смысл основания: «Без
   изменений» не обосновывает никакого числа (числа в цитате нет), а
   вот цитата «660 минут», вырезанная из ответа «…600 минут, не 660
   минут», подстрокой проходит — смысл и область основания судит
   полный A02/A03 (волна 17б, контракт §12.3). Частичная защита есть и
   сейчас: ответ на вопрос параметра с несколькими разными значениями
   машинно не применяется (`family_params._parse_sentence`).
4. Адресат значения судится тоже: ответ обязан быть ОБ ЭТОЙ ячейке —
   вопрос параметра семьи несёт адресата в своём ключе (семья +
   параметр), прочий вопрос адресует строки (`rule_refs` своей
   цепочки). Иначе значение одного параметра оправдывается применённым
   ответом о другом.
5. Один факт — одно место (общий закон П0 № 2): адрес эффекта
   (`source.effect`), корень родословной (`source.root`) и прежняя
   строка `answer` сверяются с ячейкой и её вопросом; расхождение —
   отказ, а не выбор «кому верить».
6. Отпечатки детерминированы: издание вопроса — существующий
   `anketa_xlsx.question_fingerprint` (издание 1, путь совместимости
   `fingerprint_v1` §2.5, пока изданий нет — A04 идёт волной 17б);
   версия ответа — sha256 нормализованного по пробелам `raw`
   (§2.5: старый ответ без `answer_version` получает отпечаток,
   вычислимый из `raw`).

Кто судит этим законом: полная проверка цитат
(`model.validate_fit_quotes`), приём заплаты обработчика
(`answers.apply_worker_patches`), рубеж выпуска
(`export_task_package`) и гейт пакета (`gates/gate_task_package`) —
один расчёт у производителя и у приёмника (общий закон П0 № 4).

Импорты модулей CDPM — только внутри функций: закон происхождения
читают и `family_params` (писатель), и `model` (проверка), и
`answers` (обратный ход), между которыми уже есть свои связи.
"""
from __future__ import annotations

import hashlib

#: Машинный код отказа выпуска и приёма заплаты (контракт П0 §1.4).
ANSWER_PROVENANCE = "answer-provenance"

#: Вид происхождения «значение из ответа авиакомпании».
KIND_ANSWER = "answer"

#: Состояния ответа, при которых значение из него правомерно стоит в
#: ячейке. «применён частично» вводит A02 (волна 17б) — перечень
#: назван здесь заранее, чтобы читатель не пришлось менять дважды.
APPLIED_STATUSES = ("применён", "применён частично")

#: Состояние ответа, который сменил ответ на вопрос-пересмотр: сам он
#: уже не действует, действует ответ по ссылке `revised_by`.
REVISED_STATUS = "пересмотрен"

#: Псевдострока журнала следствий: запись о целевых, а не о строке
#: свода (`answers._OBJECTIVES_KEY`) — адресатом ячейки не бывает.
OBJECTIVES_KEY = "__objectives__"

#: Поле строки свода, которое ведёт КОД, а не содержание ответа: список
#: вопросов-блокировок. Запись журнала следствий об этом поле — служебный
#: след применения (блокировку снимает само применение ответа), а не
#: эффект содержания ответа, и адресата ячейки она не создаёт (находка
#: третьего ревью).
SERVICE_EFFECT_FIELDS = ("blockers",)

#: Виды строки, при которых посадка — а с нею и ячейки, внесённые
#: ответом, — на строке ОСТАЁТСЯ: машинное правило. У прочих видов
#: (OUT, DATA, DEF, CASE) посадку тут же снимает КОД
#: (`model.align_fit_with_kind`), и ячейка ответа исчезает вместе с
#: ней. Потому смена вида и снятие строки — это снятие ячейки ответа
#: чужими руками, и заплате они закрыты так же (находка четвёртого
#: ревью, дверь «снятие в два хода»).
FIT_KINDS = ("HARD", "SOFT")


class ProvenanceRefusal(ValueError):
    """Отказ по происхождению значения: слова + код + перечень."""

    def __init__(self, words: str, items=None):
        super().__init__(words)
        self.code = ANSWER_PROVENANCE
        self.items = list(items or [])


# ── отпечатки ────────────────────────────────────────────────────────

def norm_raw(raw) -> str:
    """Нормализация дословного ответа для отпечатка: склейка пробелов
    (регистр и знаки сохраняются — ответ «10 часов» и «10 ЧАСОВ» это
    разные версии ответа)."""
    return " ".join(str(raw or "").split())


def answer_version_fingerprint(raw) -> str:
    """Отпечаток ТОЧНОЙ версии ответа — 12 hex из sha256
    нормализованного `raw` (контракт §2.2/§2.5)."""
    return hashlib.sha256(norm_raw(raw).encode("utf-8")).hexdigest()[:12]


def _root_id(q: dict) -> str:
    """Корень родословной вопроса: сам вопрос либо тот, чей ответ он
    пересматривает (`revision_of` указывает корень и у пересмотра
    пересмотра)."""
    return str(q.get("revision_of") or q.get("id") or "")


def question_edition(q: dict) -> dict:
    """Точное издание вопроса `{id, revision, fingerprint}`.

    Явное издание проверяется общим читателем снимка; его идентичность
    совпадает со скрытой колонкой книги. Старый документ без edition
    сохраняет первое издание и v1-отпечаток. При явной миграции ранее
    опубликованного вопроса fingerprint_v1 сохраняет ту же идентичность;
    последующее переиздание получает новый полный отпечаток."""
    from cdpm import anketa_xlsx, question_editions
    try:
        fingerprint = anketa_xlsx.question_fingerprint(q)
    except question_editions.EditionRefusal as exc:
        raise ProvenanceRefusal(str(exc), items=[q.get("id")]) from exc
    ed = q.get("edition") if isinstance(q.get("edition"), dict) else {}
    rev = ed.get("revision")
    rev = int(rev) if isinstance(rev, int) and not isinstance(rev, bool) \
        and rev >= 1 else 1
    return {"id": str(q.get("id") or ""),
            "revision": rev,
            "fingerprint": fingerprint}


def answer_source(q: dict, param: str, raw, field: str = "value") -> dict:
    """Происхождение значения, вносимого в ячейку ответом `q`.

    `question` — издание ИМЕННО отвеченного вопроса (у пересмотра
    `Q-…-R` — его собственное, K05), `version` — отпечаток той версии
    ответа, из которой прочитано значение, `root` — корень
    родословной (родословная, не источник), `effect` — адрес эффекта
    (для параметра семьи: `rule` = ключ параметра, `field` = `value`,
    контракт §2.3)."""
    return {"kind": KIND_ANSWER,
            "question": question_edition(q),
            "version": {"fingerprint": answer_version_fingerprint(raw)},
            "effect": {"rule": str(param), "field": str(field)},
            "root": _root_id(q)}


# ── чтение ячейки ────────────────────────────────────────────────────

def cell_source(cell) -> dict | None:
    """Объект происхождения ячейки либо None (старая форма)."""
    if not isinstance(cell, dict):
        return None
    src = cell.get("source")
    return src if isinstance(src, dict) else None


def is_answer_cell(cell) -> bool:
    """Ячейка НАЗЫВАЕТ ответ: прежней строкой `answer` (старые
    документы до миграции 17б) либо видом происхождения
    `source.kind = answer`.

    Предикат один на всех читателей ячейки — происхождение, полная
    проверка цитат, измеритель постановки, замок обмена, — и он НЕ
    зависит от того, что ячейка объявила видом источника. Иначе
    заплата, оставив метку `answer` и написав `source.kind` иного вида,
    выводила бы ячейку из-под закона у одного читателя и оставляла бы
    её «несомой ответом» у другого: два читателя видели бы одну ячейку
    по-разному (находка второго ревью, дверь «смена вида источника»).
    Несовпадение вида судит `resolve_source` словами «не названо
    ответом» — один факт, одно место (общий закон П0 № 2)."""
    if not isinstance(cell, dict):
        return False
    if cell.get("answer") is not None:
        return True
    src = cell_source(cell)
    return src is not None and str(src.get("kind") or "") == KIND_ANSWER


def answer_marker(cell) -> str:
    """Кого ячейка называет ответом: прежняя строка `answer`, иначе
    издание вопроса из точного происхождения (корень — на крайний
    случай). Читателю, которому нужен только АДРЕС ответа (измеритель
    постановки), — одно имя вместо двух разборов."""
    if not isinstance(cell, dict):
        return ""
    if cell.get("answer") is not None:
        return str(cell.get("answer"))
    src = cell_source(cell) or {}
    q = src.get("question") if isinstance(src.get("question"), dict) else {}
    return str(q.get("id") or src.get("root") or "")


def answer_cells(doc: dict):
    """Обход всех ячеек посадки, внесённых ответом: (rid, семья, имя,
    ячейка)."""
    for r in (doc.get("rules") or []):
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if not isinstance(fit, dict) or not isinstance(fit.get("params"),
                                                       dict):
            continue
        for name, cell in fit["params"].items():
            if is_answer_cell(cell):
                yield str(r.get("id")), fit.get("family"), str(name), cell


def has_answer_cells(doc: dict) -> bool:
    return any(True for _ in answer_cells(doc))


# ── обратный ход по точной идентичности ──────────────────────────────

def _question_by_id(doc: dict, qid: str) -> dict | None:
    return next((x for x in (doc.get("questions") or [])
                 if isinstance(x, dict) and str(x.get("id")) == qid), None)


def _chain_ids(doc: dict, q: dict) -> set:
    """Идентификаторы всей цепочки вопроса: корень и все его
    пересмотры (вопрос-пересмотр объявляет корень полем
    `revision_of`)."""
    root = _root_id(q)
    out = {str(q.get("id") or ""), root}
    for x in (doc.get("questions") or []):
        if isinstance(x, dict) and str(x.get("revision_of") or "") == root:
            out.add(str(x.get("id") or ""))
    return out - {""}


def active_answer(doc: dict, q: dict, applying=()) -> tuple:
    """(вопрос, дословный `raw`) ДЕЙСТВУЮЩЕГО ответа цепочки: от `q`
    идём по ссылкам `revised_by` до последнего звена — пересмотренный
    ответ не действует, действует тот, что его сменил. Возврат
    (None, причина словами) — действующего ответа у цепочки нет."""
    seen: set = set()
    cur = q
    while True:
        qid = str(cur.get("id") or "")
        if qid in seen:
            return None, (f"цепочка пересмотров вопроса {qid!r} "
                          f"закольцована — действующего ответа нет")
        seen.add(qid)
        ans = cur.get("answer") if isinstance(cur.get("answer"), dict) \
            else None
        if ans is None:
            return None, (f"действующий ответ цепочки — на вопрос {qid!r}, "
                          f"а его ещё нет")
        status = str(ans.get("status") or "")
        if status in APPLIED_STATUSES or qid in set(applying):
            from cdpm import answer_parsing
            parsed_problems = answer_parsing.problems(cur)
            if parsed_problems:
                return None, f"{qid}: " + "; ".join(parsed_problems)
            return cur, str(ans.get("raw") or "")
        if status != REVISED_STATUS:
            return None, (f"действующий ответ цепочки — на вопрос {qid!r} "
                          f"в состоянии {status!r}, а не применён")
        nxt_id = str(ans.get("revised_by") or "")
        nxt = _question_by_id(doc, nxt_id) if nxt_id else None
        if nxt is None:
            return None, (f"ответ на вопрос {qid!r} пересмотрен, а вопроса-"
                          f"пересмотра {nxt_id!r} в анкете нет — "
                          f"действующего ответа у цепочки нет")
        cur = nxt


def resolve_source(doc: dict, source: dict, applying=()) -> tuple:
    """(вопрос, дословный `raw` точной версии ответа) по происхождению
    ячейки. Поиск — по ТОЧНОЙ идентичности: идентификатор издания,
    отпечаток издания, отпечаток версии ответа. Любое несовпадение —
    `ProvenanceRefusal` (код `answer-provenance`) со словами.

    `applying` — идентификаторы вопросов, чей ответ прямо сейчас
    применяется (приём заплаты: статус «применён» ещё не проставлен,
    поправка §12.3); для прочих вопросов требуется применённый ответ.
    Названы РОВНО отвечаемые вопросы: корень цепочки и соседний
    пересмотр применённым не считаются — иначе заплата на `X-R`
    оправдывала бы значение ответом на пересмотренный `X`.
    """
    if not isinstance(source, dict) or str(source.get("kind") or "") \
            != KIND_ANSWER:
        raise ProvenanceRefusal(
            "происхождение ячейки не названо ответом авиакомпании — "
            "судить точную версию нечем")
    want_q = source.get("question") if isinstance(source.get("question"),
                                                  dict) else {}
    qid = str(want_q.get("id") or "")
    if not qid:
        raise ProvenanceRefusal(
            "в происхождении ячейки нет идентификатора издания вопроса")
    q = _question_by_id(doc, qid)
    if q is None:
        raise ProvenanceRefusal(
            f"вопроса {qid} нет в анкете — значение ссылается на ответ, "
            f"которого в документе не существует", items=[qid])
    ed = question_edition(q)
    want_fp = str(want_q.get("fingerprint") or "")
    if not want_fp:
        raise ProvenanceRefusal(
            f"в происхождении ячейки нет отпечатка издания вопроса {qid}",
            items=[qid])
    if want_fp != ed["fingerprint"]:
        raise ProvenanceRefusal(
            f"издание вопроса {qid} другое: ячейка ссылается на отпечаток "
            f"{want_fp}, у вопроса анкеты {ed['fingerprint']}", items=[qid])
    want_rev = want_q.get("revision")
    if not isinstance(want_rev, int) or isinstance(want_rev, bool) \
            or want_rev < 1:
        raise ProvenanceRefusal(
            f"в происхождении ячейки номер издания вопроса {qid} "
            f"не задан положительным целым числом: {want_rev!r}",
            items=[qid])
    if want_rev != ed["revision"]:
        raise ProvenanceRefusal(
            f"издание вопроса {qid} другое: ячейка ссылается на издание "
            f"{want_rev}, у вопроса анкеты {ed['revision']}", items=[qid])
    ans = q.get("answer") if isinstance(q.get("answer"), dict) else None
    if ans is None:
        raise ProvenanceRefusal(
            f"на вопрос {qid} ответа нет — значение внесено без ответа",
            items=[qid])
    status = str(ans.get("status") or "")
    if status not in APPLIED_STATUSES and qid not in set(applying):
        raise ProvenanceRefusal(
            f"ответ на вопрос {qid} в состоянии {status!r}, а не применён "
            f"— значение из него в посадке не стоит", items=[qid])
    raw = str(ans.get("raw") or "")
    from cdpm import answer_versions, answer_parsing
    version_problems = answer_versions.problems(ans) + answer_parsing.problems(q)
    if version_problems:
        raise ProvenanceRefusal("; ".join(version_problems), items=[qid])
    want_ver = str((source.get("version") or {}).get("fingerprint") or "") \
        if isinstance(source.get("version"), dict) else ""
    if not want_ver:
        raise ProvenanceRefusal(
            f"в происхождении ячейки нет отпечатка версии ответа на "
            f"вопрос {qid}", items=[qid])
    got_ver = answer_version_fingerprint(raw)
    if want_ver != got_ver:
        raise ProvenanceRefusal(
            f"версия ответа на вопрос {qid} другая: ячейка ссылается на "
            f"версию {want_ver}, у ответа анкеты {got_ver} — значение "
            f"прочитано не из этого текста", items=[qid])
    return q, raw


# ── закон ячейки (контракт §2.3 п. 3, §12.3) ─────────────────────────

def _norm(s) -> str:
    from cdpm import model as m
    return m._fit_norm_text(s)


def _unit_of(family, name, cell) -> str:
    from cdpm import family_params as fp
    spec = fp._param_spec(str(family), str(name)) if family else {}
    return str(spec.get("unit") or (cell.get("unit") if isinstance(cell, dict)
                                    else "") or "")


def _legacy_raw(doc: dict, cell: dict, applying=()) -> tuple:
    """(вопрос, raw) для ячейки СТАРОЙ формы (только строка `answer`).

    Точной версии такая ячейка не несёт — её даёт миграция 17б; до неё
    «точная версия» — ДЕЙСТВУЮЩИЙ ответ цепочки: от названного вопроса
    идём по `revised_by` до последнего звена. Так форма ячейки перестаёт
    быть обходом закона: пересмотренный ответ не оправдывает значение,
    и цитата с числом судятся против текста того ответа, который
    действует. Возврат (None, причина) — отказ словами.

    Метка читается КАК ЕСТЬ (`answer_marker`): отказ называет то
    значение, которое в ячейке стоит, а не пустую строку вместо него —
    прежде метка `0` (схема такую ячейку и так краснит, но слова о ней
    говорил провенанс) превращалась в `''` приведением `... or ""`
    (косметическая находка третьего ревью)."""
    qid = answer_marker(cell)
    q = _question_by_id(doc, qid)
    if q is None:
        return None, (f"помечен ответом {qid!r}, которого нет в анкете")
    act, raw = active_answer(doc, q, applying=applying)
    if act is None:
        return None, f"помечен ответом {qid!r}: {raw}"
    return act, raw


def _target_problems(doc: dict, q: dict, rid, family, name) -> list[str]:
    """Адресат значения (закон 4): ответ обязан быть ОБ ЭТОЙ ячейке.

    Вопрос параметра семьи несёт адресата в своём структурном ключе
    (семья + параметр) — он и сверяется с посадкой и именем ячейки.
    Прочий вопрос адресует СТРОКИ: носитель ячейки обязан быть среди
    строк, названных цепочкой вопроса (`rule_refs` корня и пересмотров)
    либо названных ЖУРНАЛОМ СЛЕДСТВИЙ её применённых ответов
    (`answer.effects[].rule`). Вопрос, не адресующий ни одной строки,
    не обосновывает ячейку вовсе.

    Из журнала берутся следствия СОДЕРЖАНИЯ ответа, а не служебные следы
    кода: запись о поле `blockers` (`SERVICE_EFFECT_FIELDS`) не создаёт
    адресата (находка третьего ревью). Блокировку строки ставит и
    снимает код — сам факт «вопрос блокировал строку, и применение
    ответа блокировку сняло» говорит только о том, что строка ЖДАЛА
    ответа, а не о том, что ответ о ней. Иначе адресат возвращался бы
    через журнал ровно тот, который закон уже отверг как зависящий от
    момента: вопрос, называющий одну строку и блокирующий другую,
    оправдывал бы значение в чужой строке.

    Набор адресатов НЕ ЗАВИСИТ ОТ ВРЕМЕНИ (находка второго ревью):
    прежде сюда входили строки, ЗАБЛОКИРОВАННЫЕ вопросом, а блокировка
    снимается применением ответа — на приёме заплаты она ещё стоит, на
    выпуске её уже нет, и об одном своде выходило два вердикта (общий
    закон П0 № 4). `rule_refs` цепочки и журнал следствий во времени не
    убывают: что принято, то и выпускается."""
    from cdpm import family_params as fp
    qid = str(q.get("id") or "")
    if "edition" in q:
        from cdpm import question_editions
        try:
            scope = question_editions.targets(q)
        except question_editions.EditionRefusal as exc:
            return [str(exc)]
        if str(rid) not in scope:
            return [f"ответ на вопрос {qid}: строка {rid} вне области издания"]
    tgt = fp.question_target(q)
    if tgt is not None:
        if (str(tgt[0]), str(tgt[1])) != (str(family or ""), str(name)):
            return [f"ответ на вопрос {qid} — о параметре {tgt[1]!r} семьи "
                    f"{tgt[0]!r}, а не о {str(name)!r} семьи "
                    f"{str(family or '')!r}: значение одного параметра "
                    f"обосновано ответом о другом"]
        return []
    ids = _chain_ids(doc, q)
    refs: set = set()
    for x in (doc.get("questions") or []):
        if not isinstance(x, dict) or str(x.get("id") or "") not in ids:
            continue
        refs |= {str(t) for t in (x.get("rule_refs") or [])}
        ans = x.get("answer") if isinstance(x.get("answer"), dict) else {}
        for e in (ans.get("effects") or []):
            if not isinstance(e, dict) or not e.get("rule"):
                continue
            if str(e["rule"]) == OBJECTIVES_KEY:
                continue
            if str(e.get("field") or "") in SERVICE_EFFECT_FIELDS:
                continue
            refs.add(str(e["rule"]))
    if str(rid) not in refs:
        return [f"ответ на вопрос {qid} адресует строки "
                + (", ".join(sorted(refs)[:4]) if refs else "«ни одной»")
                + f", а не {rid}: значение обосновано ответом о другом "
                  f"предмете"]
    return []


def _source_shape_problems(q: dict, name, cell, src: dict) -> list[str]:
    """Один факт — одно место (закон 5): адрес эффекта, корень
    родословной и прежняя строка `answer` сверяются с ячейкой и её
    вопросом. Расхождение — отказ, а не выбор «кому верить»."""
    out: list[str] = []
    eff = src.get("effect") if isinstance(src.get("effect"), dict) else None
    if eff is None:
        out.append("в происхождении ячейки нет адреса эффекта (effect)")
    else:
        if str(eff.get("rule") or "") != str(name):
            out.append(f"адрес эффекта происхождения называет "
                       f"{str(eff.get('rule') or '')!r}, а ячейка — "
                       f"{str(name)!r}")
        if str(eff.get("field") or "") != "value":
            out.append(f"адрес эффекта происхождения меняет поле "
                       f"{str(eff.get('field') or '')!r}, а не значение "
                       f"параметра (value)")
    root = _root_id(q)
    if str(src.get("root") or "") != root:
        out.append(f"корень родословной происхождения "
                   f"{str(src.get('root') or '')!r} — не корень вопроса "
                   f"{root!r}")
    marker = cell.get("answer") if isinstance(cell, dict) else None
    if marker is not None and str(marker) != root:
        out.append(f"прежняя строка answer {str(marker)!r} называет не тот "
                   f"вопрос, что корень происхождения {root!r}")
    return out


def patch_problems(prev_rule, new_rule) -> list[str]:
    """Закон заплаты (закон 2а): ячейка, помеченная ответом, заводится
    и меняется ТОЛЬКО вместе с точным происхождением.

    Заплата обработчика и заплата обмена вправе оставить ячейку старой
    формы такой, какая она была, но не вправе завести новую без
    `source`, снять происхождение у имеющейся и изменить значение,
    цитату или метку ячейки старой формы: иначе форма ячейки — обход
    закона точной версии (вектор B1 ревью).

    Ячейка, названная ответом, ОСТАЁТСЯ названной ответом: снять метку
    (переписать её клаузной ячейкой `{value, quote}`), сменить вид
    происхождения (`source.kind`) или ВОВСЕ УБРАТЬ ячейку из посадки
    заплата не вправе — только вместе с точным происхождением (находки
    второго и третьего ревью: двери «снятие метки», «смена вида
    источника» и «снятие ячейки»). Иначе значение из ответа выходило бы
    из-под закона точной версии сменой ВИДА ячейки, а не основанием, —
    или тем же снятием метки, но в ДВА шага: одна заплата выбрасывает
    ячейку ответа, следующая заводит её клаузной парой, и ни один из
    шагов по отдельности прежний обход не видел (он шёл только по
    ячейкам НОВОЙ строки).

    СТРОКА с такими ячейками тоже не снимается и не меняет вид
    заплатой (шаг 0, находка четвёртого ревью). Ячейку ответа несёт
    ПОСАДКА, а посадку несёт только машинный вид (`FIT_KINDS`): у OUT
    (форма `remove`) и у DATA/DEF/CASE её снимает код
    (`align_fit_with_kind`) — тот же выброс ячейки, только чужими
    руками. Судить одни лишь ячейки мало: «снятие в два хода» — первая
    заплата уводит строку в OUT либо меняет вид (ячейка исчезает
    работой кода), вторая возвращает строку машинной с клаузной парой
    `{value, quote}` на месте ответа, и у ПРЕЖНЕЙ строки второго хода
    посадки уже нет, так что пропажу шагу 0б видеть не на чем. Потому
    вид строки судится ЗДЕСЬ, до работы кода, и одинаково у обоих
    приёмников — и у полной заплаты, и у заплаты вида (`kind_patch`),
    и у формы `remove` (общий закон П0 № 4: расчёт один).

    Ячейку, внесённую ответом, снимает КОД и только код: пересмотр
    ответа (`-R`) и решение авиакомпании «не применяется». Честный
    путь не тупик: `open_revision` заводит `-R` и на снятой строке,
    а ответ «не применяется» ячейку убирает. Обход по старой строке
    идёт НЕЗАВИСИМО от того, есть ли у ячейки точное происхождение:
    ячейка старой формы тоже внесена ответом. Пусто = заплата
    допустима."""
    def _cells(rule):
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        if not isinstance(fit, dict) or not isinstance(fit.get("params"),
                                                       dict):
            return {}
        return {str(k): v for k, v in fit["params"].items()}

    out: list[str] = []
    old = _cells(prev_rule or {})
    new = _cells(new_rule or {})
    rid = str((new_rule or {}).get("id")
              or (prev_rule or {}).get("id") or "")
    answered = [n for n in sorted(old)
                if isinstance(old[n], dict) and is_answer_cell(old[n])]
    # (0) Строка с ячейками ответа не СНИМАЕТСЯ и не МЕНЯЕТ ВИД
    # заплатой: и то и другое отнимает у строки посадку, а с нею —
    # значение авиакомпании (см. преамбулу). Вид, заплатой не
    # названный, наследуется от прежней строки (оба приёмника
    # наследуют посадку и вес тем же порядком); вид, не известный ни
    # той ни другой, не судится — судить нечего.
    kind = (new_rule or {}).get("kind")
    if kind is None:
        kind = (prev_rule or {}).get("kind")
    removed = (new_rule or {}).get("remove") is True
    unlanded = not isinstance((new_rule or {}).get("template_fit"), dict)
    kind_off = kind is not None and str(kind) not in FIT_KINDS
    row_gone = bool(answered) and (removed or unlanded or kind_off)
    if row_gone:
        how = ("снята заплатой (remove)" if removed
               else f"переведена заплатой в вид {str(kind)!r}" if kind_off
               else "оставлена заплатой без посадки")
        out.append(f"{rid}: строка {how}, а её посадка несёт ячейки, "
                   f"внесённые ответом ({', '.join(answered)}) — строка с "
                   f"ячейками, внесёнными ответом, снимается или меняет вид "
                   f"только после пересмотра (-R) либо решения "
                   f"«не применяется»")
    # (0б) Ячейка ответа не ИСЧЕЗАЕТ заплатой поодиночке. Обход старой
    # строки идёт первым: пропажа ячейки — самый тихий из подлогов (в
    # новой строке её попросту нет, и судить нечего), и назвать её надо
    # прежде, чем разбирать уцелевшие ячейки. Ушла вся строка — случай
    # уже назван шагом (0), второй раз о том же не говорим.
    for name, was in old.items():
        if row_gone or name in new or not isinstance(was, dict) \
                or not is_answer_cell(was):
            continue
        out.append(f"{rid}: ячейка параметра {name!r}, внесённая ответом, "
                   f"снята заплатой — снять её может только код "
                   f"(пересмотр, решение «не применяется»)")
    for name, cell in new.items():
        was = old.get(name)
        if isinstance(was, dict) and is_answer_cell(was):
            # (i) метка ответа снимается только вместе с точным
            # происхождением: клаузная ячейка на месте ячейки ответа —
            # подлог вида, а не основание.
            if not is_answer_cell(cell):
                out.append(f"{rid}: у параметра {name!r} заплатой снята "
                           f"метка ответа — снять метку ответа или "
                           f"изменить вид источника можно только вместе с "
                           f"точным происхождением")
                continue
            # (ii) вид происхождения тоже не меняется заплатой: метка
            # осталась, а вид стал иным — та же дверь с другой стороны.
            new_kind = str((cell_source(cell) or {}).get("kind") or "")
            if new_kind and new_kind != KIND_ANSWER:
                out.append(f"{rid}: у параметра {name!r} заплатой изменён "
                           f"вид источника на {new_kind!r} — снять метку "
                           f"ответа или изменить вид источника можно "
                           f"только вместе с точным происхождением")
                continue
        if not is_answer_cell(cell) or cell_source(cell) is not None:
            continue
        if not isinstance(was, dict) or not is_answer_cell(was):
            out.append(f"{rid}: параметр {name!r} заведён заплатой без "
                       f"точного происхождения ответа — новые ячейки "
                       f"только с точным происхождением")
        elif cell_source(was) is not None:
            out.append(f"{rid}: у параметра {name!r} заплатой снято точное "
                       f"происхождение ответа — происхождение снимать "
                       f"нельзя")
        else:
            changed = [k for k in ("value", "quote", "answer")
                       if was.get(k) != cell.get(k)]
            if changed:
                out.append(f"{rid}: ячейка параметра {name!r} старой формы "
                           f"изменена заплатой ({', '.join(changed)}) без "
                           f"точного происхождения ответа — изменение "
                           f"только с точным происхождением")
    return out


def cell_problems(doc: dict, rid, family, name, cell,
                  applying=()) -> list[str]:
    """Причины отказа по ОДНОЙ ячейке посадки, внесённой ответом
    (пусто = происхождение подтверждено). Судит: точную идентичность
    (новая форма) либо действующий ответ цепочки (старая форма), сверку
    адреса эффекта, корня и прежней строки `answer` с ячейкой, адресата
    ответа (о ЭТОЙ ли ячейке он), дословность цитаты в `raw` именно той
    версии и читаемость значения из цитаты грамматикой K02."""
    if not is_answer_cell(cell):
        return []
    head = f"{rid}: параметр {name!r}"
    src = cell_source(cell)
    if src is not None:
        try:
            q, raw = resolve_source(doc, src, applying=applying)
        except ProvenanceRefusal as e:
            return [f"{head} — {e}"]
        shape = _source_shape_problems(q, name, cell, src)
        if shape:
            return [f"{head} — {p}" for p in shape]
    else:
        q, raw = _legacy_raw(doc, cell, applying=applying)
        if q is None:
            return [f"{head} {raw}"]
    addressee = _target_problems(doc, q, rid, family, name)
    if addressee:
        return [f"{head} — {p}" for p in addressee]
    return [f"{head} {p}" for p in value_problems(family, name, cell, raw)]


def value_problems(family, name, cell, raw) -> list[str]:
    """Цитата и значение ячейки против дословного текста ответа:
    цитата — подстрока `raw`, значение — читается ИЗ ЦИТАТЫ
    грамматикой K02. Контекстные отказы судятся по ПОЛНОМУ raw,
    иначе подстрока отбрасывает условие или период исходного ответа.
    Судится и при полном документе, и по срезу (тексту ответа) — один закон."""
    from cdpm import family_params as fp
    out: list[str] = []
    quote = str((cell.get("quote") if isinstance(cell, dict) else "") or "")
    if not quote.strip():
        return ["внесён ответом без цитаты"]
    if _norm(quote) not in _norm(raw):
        return [f"цитата {quote[:60]!r} не является дословной подстрокой "
                f"ответа, на который ячейка ссылается"]
    unit = _unit_of(family, name, cell)
    if not unit:
        return [f"единица параметра не известна каталогу — значение "
                f"{cell.get('value')!r} из цитаты не читается"]
    from cdpm import answer_subjects
    subject_problem = answer_subjects.problem(raw, family, name, unit)
    if subject_problem:
        return [subject_problem]
    got = answer_subjects.read_value(quote, family, name, unit, fp.default_domain(unit))
    if got is None:
        out.append(f"значение {cell.get('value')!r} из цитаты "
                   f"{quote[:60]!r} не читается: цитата подтверждает "
                   f"подлинность текста, но не значение")
        return out
    context_problem = answer_subjects.value_context_problem(raw, family, name, unit)
    if context_problem:
        return [f"полный ответ {context_problem}; вырезанная цитата "
                "не обосновывает безусловное значение ячейки"]
    full_value = answer_subjects.read_value(raw, family, name, unit, fp.default_domain(unit))
    if full_value is None or full_value[0] != got[0]:
        return ["полный ответ не подтверждает одно значение из цитаты; "
                "нельзя вырезать только один из несогласованных пределов"]
    value = got[0]
    if value != cell.get("value"):
        out.append(f"значение ячейки {cell.get('value')!r} не равно "
                   f"прочитанному из цитаты {value!r} — основание не "
                   f"подтверждает значение")
    return out


def rule_problems(doc: dict, rule: dict, applying=()) -> list[str]:
    """Причины отказа по всем ячейкам ОДНОЙ строки свода."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict) or not isinstance(fit.get("params"), dict):
        return []
    out: list[str] = []
    for name, cell in fit["params"].items():
        out += cell_problems(doc, rule.get("id"), fit.get("family"),
                             name, cell, applying=applying)
    return out


def package_refusal(doc: dict) -> tuple:
    """(причины словами, перечень «строка:параметр») по всему документу
    — один обход для рубежа выпуска и гейта пакета (общий закон П0
    № 4: у производителя и приёмника один расчёт)."""
    words: list[str] = []
    items: list[str] = []
    from cdpm import answer_versions, answer_parsing
    for q in doc.get("questions", []):
        got = answer_versions.problems(q.get("answer")) if isinstance(q, dict) else []
        got += answer_parsing.problems(q)
        if got:
            words += [f"{q.get('id', '?')}: {p}" for p in got]
            items.append(str(q.get("id") or "?"))
    for rid, fam, name, cell in answer_cells(doc):
        got = cell_problems(doc, rid, fam, name, cell)
        if got:
            words += got
            items.append(f"{rid}:{name}")
    return words, items


def package_problems(doc: dict) -> list[str]:
    """Причины отказа по всему документу словами."""
    return package_refusal(doc)[0]
