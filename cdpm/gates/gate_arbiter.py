# -*- coding: utf-8 -*-
"""Гейт арбитража тупиков CDPM (решение владельца 2026-08-18).

Проверяет каталог цикла ПОСЛЕ арбитража:

1. самотест: контрольный невалидный вердикт ловится;
2. полнота: у каждого тупика вердикта цикла РОВНО ОДИН исход — вердикт
   арбитра из перечня с непустым основанием, запись «не разрешено»
   (unresolved) с причиной словами (A09, контракт П0 §1.3 и §12.2)
   ЛИБО запись «ждёт ответа владельца» (awaiting_owner, §12.8: спор
   уже передан владельцу открытым вопросом анкеты и до его слова
   арбитру не предъявляется); два исхода разом, повторы в любом из
   перечней и записи по неизвестному тупику — нарушение. Нестроковый
   идентификатор тупика называется словами с местом записи, а не
   роняет гейт трассой (X-02);
3. счётчики by_verdict сходятся с пересчётом по списку вердиктов;
4. реестр: состояние каждой тупиковой находки соответствует
   ИСПОЛНЕННОМУ вердикту арбитра: «аудитор прав» закрыт только
   принятой заплатой (тупик в enforced_deadlocks, строка-цель
   изменена), «в анкету» — только созданным или переиспользованным
   вопросом; вердикт без исполнения — нарушение, находка обязана
   остаться в состоянии «тупик» (аудит A3.5, 2026-09-04); тупик из
   unresolved обязан нести состояние «не разрешено арбитром» (A09);
   у тупика, дозревшего из терминальной отбраковки замком кода
   (§12.8), «аудитор прав» исполняется не заплатой, а передачей спора
   владельцу: причина при находке, вопрос анкеты, строка в unenforced
   и живое состояние «тупик» — все четыре следа разом, иначе красный;
   спор, названный «ждёт ответа владельца», обязан быть под замком
   кода, жить состоянием «тупик», не числиться исполненным, а его
   вопрос — существовать в модели после арбитража и быть ОТКРЫТЫМ;
   находки ОДНОГО ключа обязаны нести один исход (спор — это ключ,
   A09/X-03); счётчик enforced сходится с перечнем исполненных тупиков;
5. diff-only: модель после арбитража меняет ТОЛЬКО строки исполненных
   замечаний («аудитор прав» с принятой заплатой) и строки-носители
   вопросов «в анкету» (блокировка); новые вопросы — только из
   added_questions;
6. «в анкету»: каждый добавленный вопрос — часть 2, развилка ровно из
   двух чтений (а/б), строка-носитель заблокирована этим вопросом;
   вердикт без вопроса (нового или переиспользованного) — нарушение,
   объяснение в patch_rejections его не извиняет;
7. модель после арбитража проходит контракт П-1 (схема + инварианты).

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_arbiter КАТАЛОГ_ЦИКЛА
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdpm import exchange as ex, model as m


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _selftest() -> None:
    assert "не решено" not in ex.ARBITER_VERDICTS, \
        "самотест: перечень вердиктов арбитра подменён"
    rep = {"deadlocks": 1,
           "verdicts": [{"deadlock": "X", "verdict": "не решено",
                         "basis": ""}],
           "by_verdict": {v: 0 for v in ex.ARBITER_VERDICTS},
           "enforced": 0, "added_questions": [],
           "patch_rejections": []}
    probs = _verdict_problems(rep, [{"id": "X"}])
    assert any("вне перечня" in p for p in probs), \
        "самотест: невалидный вердикт арбитра не пойман"
    assert any("без основания" in p for p in probs), \
        "самотест: вердикт без основания не пойман"


def _rows(report: dict, field: str) -> tuple[list[dict], list[str]]:
    """Перечень отчёта арбитража — только записи-объекты; всё
    остальное (строка, null, число, объект вместо списка) названо
    СЛОВАМИ и местом в перечне.

    Подделанная форма — такое же нарушение, как состояние реестра вне
    перечня: гейт обязан покраснеть, а не упасть трассой (падение
    читается как «отказ арбитража» — беда инструмента, а не
    подделка отчёта). Отсутствие перечня — законная старая форма
    отчёта: пустой перечень, без нарушения."""
    raw = report.get(field)
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"перечень {field} отчёта арбитража не список: "
                    f"{type(raw).__name__}"]
    rows: list[dict] = []
    bad: list[str] = []
    for n, r in enumerate(raw, 1):
        if isinstance(r, dict):
            rows.append(r)
        else:
            bad.append(f"запись #{n} перечня {field} отчёта "
                       f"арбитража не объект: {repr(r)[:60]}")
    return rows, bad


def _list_of(report: dict, field: str,
             problems: list[str]) -> list:
    """Перечень отчёта как СПИСОК; подделанная форма (строка, число,
    объект) названа словами, а не роняет гейт `len(None)` трассой
    (X-02). Отсутствие перечня — законная старая форма отчёта."""
    raw = report.get(field)
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(f"перечень {field} отчёта арбитража не список: "
                        f"{type(raw).__name__}")
        return []
    return raw


def _map_of(report: dict, field: str, problems: list[str]) -> dict:
    """Соответствие отчёта как ОБЪЕКТ — тем же законом, что перечни."""
    raw = report.get(field)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"соответствие {field} отчёта арбитража не "
                        f"объект: {type(raw).__name__}")
        return {}
    return raw


def _by_deadlock(rows: list[dict], field: str,
                 problems: list[str]) -> dict:
    """Записи перечня по идентификатору тупика.

    Идентификатор — СЛОВА (строка), и только они (ревью A09 №2,
    находка 4): подделанный список или объект на его месте ронял гейт
    трассой «unhashable type», а упавший гейт читается как беда
    инструмента — подделка уходила из вида. Нестроковый идентификатор
    называется словами с МЕСТОМ записи и берётся в ключ своим
    текстовым видом: с настоящим идентификатором он совпасть не может,
    поэтому запись честно станет «по неизвестному тупику»."""
    out: dict = {}
    for n, r in enumerate(rows, 1):
        did = r.get("deadlock")
        if not isinstance(did, str):
            problems.append(
                f"запись #{n} перечня {field} отчёта арбитража: "
                f"идентификатор тупика не строка "
                f"({type(did).__name__}): {repr(did)[:60]}")
            did = repr(did)
        out[did] = r
    return out


def _verdict_problems(report: dict, deadlocks: list[dict]) -> list[str]:
    """Исход по каждому тупику — ровно один: решение арбитра, запись
    «не разрешено» (A09) ЛИБО запись «ждёт ответа владельца» (§12.8 —
    спор уже передан владельцу открытым вопросом анкеты и до его
    слова арбитру не предъявляется). Плюс валидность вердиктов и
    сходимость by_verdict."""
    verd_rows, form_bad = _rows(report, "verdicts")
    unres_rows, unres_bad = _rows(report, "unresolved")
    await_rows, await_bad = _rows(report, "awaiting_owner")
    problems: list[str] = form_bad + unres_bad + await_bad
    got = _by_deadlock(verd_rows, "verdicts", problems)
    if len(got) != len(verd_rows):
        problems.append("повторные вердикты по одному тупику")
    unres = _by_deadlock(unres_rows, "unresolved", problems)
    if len(unres) != len(unres_rows):
        problems.append("повторные записи «не разрешено» по одному "
                        "тупику")
    waits = _by_deadlock(await_rows, "awaiting_owner", problems)
    if len(waits) != len(await_rows):
        problems.append("повторные записи «ждёт ответа владельца» по "
                        "одному тупику")
    for did in sorted(set(got) & set(unres), key=str):
        problems.append(f"тупик {did!r}: и решение арбитра, и запись "
                        f"«не разрешено» — исход обязан быть один")
    for did in sorted((set(got) | set(unres)) & set(waits), key=str):
        problems.append(f"тупик {did!r}: и запись «ждёт ответа "
                        f"владельца», и другой исход — исход обязан "
                        f"быть один")
    for f in deadlocks:
        v = got.get(f["id"])
        u = unres.get(f["id"])
        w = waits.get(f["id"])
        if w is not None and u is None and v is None:
            # Спор передан владельцу и ждёт его слова: решения по нему
            # нет и быть не должно, но причина и сам вопрос обязаны
            # быть названы — иначе «ожидание» стало бы способом
            # молча спрятать спор.
            reason = w.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"тупик {f['id']}: запись «ждёт ответа "
                                f"владельца» без причины словами "
                                f"({type(reason).__name__})")
            qid = w.get("question")
            if not isinstance(qid, str) or not qid.strip():
                problems.append(f"тупик {f['id']}: запись «ждёт ответа "
                                f"владельца» без названного вопроса "
                                f"анкеты ({type(qid).__name__})")
            continue
        if u is not None:
            # Спор честно оставлен неразрешённым: решения нет, причина
            # обязана быть названа СЛОВАМИ — тип судится явно, до
            # приведения (ревью A09, находка 8): `str(...)` принимал
            # список или словарь, и «причиной» становилось `[1, 2]`.
            reason = u.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"тупик {f['id']}: запись «не "
                                f"разрешено» без причины словами "
                                f"({type(reason).__name__})")
            continue
        if v is None:
            problems.append(f"тупик {f['id']}: нет ни вердикта "
                            f"арбитра, ни записи «не разрешено»")
            continue
        if v.get("verdict") not in ex.ARBITER_VERDICTS:
            problems.append(f"тупик {f['id']}: вердикт "
                            f"{v.get('verdict')!r} вне перечня")
        # Основание — СЛОВА, и только они (ревью A09, находка 8): за
        # `str(...)` проходило любое значение, и решение «обоснованное»
        # числом или списком считалось полным.
        basis = v.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            problems.append(f"тупик {f['id']}: вердикт без основания "
                            f"словами ({type(basis).__name__})")
    known = {f["id"] for f in deadlocks}
    for did in got:
        if did not in known:
            problems.append(f"вердикт по неизвестному тупику {did!r}")
    # Лишний (неизвестный) идентификатор ответа арбитра не скрывается:
    # он доходит до отчёта записью «не разрешено» и краснит гейт.
    for did in unres:
        if did not in known:
            problems.append(f"запись «не разрешено» по неизвестному "
                            f"тупику {did!r}")
    for did in waits:
        if did not in known:
            problems.append(f"запись «ждёт ответа владельца» по "
                            f"неизвестному тупику {did!r}")
    recount = {v: sum(1 for e in verd_rows if e.get("verdict") == v)
               for v in ex.ARBITER_VERDICTS}
    if recount != report.get("by_verdict"):
        problems.append("счётчики by_verdict не сходятся с пересчётом "
                        "по списку вердиктов")
    return problems


_STATE_BY_VERDICT = {
    "обработчик прав": "арбитр: обработчик прав",
    "аудитор прав": "арбитр: аудитор прав",
    "в анкету": "арбитр: в анкету",
}


def check(out_dir: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    c = {"deadlocks": 0, "verdicts": 0, "unresolved": 0, "awaiting": 0,
         "enforced": 0, "questions": 0, "rules_diffed": 0}
    rep_path = out_dir / "arbiter.json"
    if not rep_path.is_file():
        return [f"отчёт арбитража {rep_path} не существует"], c
    report = _load(rep_path)
    verdict_summary = _load(out_dir / "verdict.json")
    deadlocks = verdict_summary.get("deadlocks", [])
    # Ключи тупиков из verdict.json прежнего кода — к текущей
    # канонизации (В2-3), иначе досье не найдёт замечание.
    ex.normalize_registry_keys({"findings": deadlocks})
    c["deadlocks"] = len(deadlocks)
    c["verdicts"] = len(_rows(report, "verdicts")[0])
    c["unresolved"] = len(_rows(report, "unresolved")[0])
    c["awaiting"] = len(_rows(report, "awaiting_owner")[0])
    c["enforced"] = report.get("enforced", 0)
    # `or []`: подделанный null/строка на месте перечня — нарушение
    # СЛОВАМИ (X-02), а не падение счётчика трассой.
    c["questions"] = len(_list_of(report, "added_questions", problems))

    problems += _verdict_problems(report, deadlocks)
    by_id = {str(v.get("deadlock")): v.get("verdict")
             for v in _rows(report, "verdicts")[0]}
    # Исполненные вердикты «аудитор прав» — по перечню принятых заплат
    # отчёта; счётчик enforced обязан сходиться с ним, и каждый
    # исполненный тупик обязан нести вердикт «аудитор прав».
    enforced_ids = [str(i) for i in
                    _list_of(report, "enforced_deadlocks", problems)]
    if report.get("enforced", 0) != len(enforced_ids):
        problems.append("счётчик enforced не сходится с перечнем "
                        "исполненных тупиков enforced_deadlocks")
    for did in enforced_ids:
        if by_id.get(did) != "аудитор прав":
            problems.append(f"тупик {did}: числится исполненным при "
                            f"вердикте {by_id.get(did)!r}")
    enforced = set(enforced_ids)
    reuse = {k: v for k, v in
             _map_of(report, "question_reuse", problems).items()
             if isinstance(v, str)}
    added_q = set()
    for n, qid in enumerate(_list_of(report, "added_questions",
                                     problems), 1):
        if isinstance(qid, str):
            added_q.add(qid)
        else:
            problems.append(f"запись #{n} перечня added_questions "
                            f"отчёта арбитража: идентификатор вопроса "
                            f"не строка ({type(qid).__name__})")
    questioned = {f["id"] for f in deadlocks
                  if m.stable_question_id("2", f"арб-{f['id']}") in added_q
                  or f["id"] in reuse}

    # Досье тупиков — ДО суда о реестре (ревью A09, находка 1): в нём
    # третий, независимый от арбитража носитель запрета кода — файлы
    # отбраковки раундов. Производные поля реестра пишет тот же
    # арбитраж, чью честность и судят: подделавший реестр снимет и их.
    dossiers = {f["id"]: ex._deadlock_dossier(out_dir, f)
                for f in deadlocks}
    # Реестр: тупики закрыты состояниями, соответствующими вердиктам.
    # Миграция ключей — в памяти (В2-3): реестр прежнего кода иначе
    # не сопоставится с досье, построенным текущим remark_key.
    # A07 (четвёртое неавтор-ревью, находка 2): негодная форма реестра
    # — красный гейт СЛОВАМИ, как у gate_exchange, а не трассировка
    # ExchangeError из CLI: аналитик читает причину, а не стек.
    # A07-R5: сюда доходят только после исполненного арбитража (без
    # arbiter.json проверка вышла словами выше) — реестр обязан быть.
    # Исчезнувший registry.json прежде читался пустым, и все тупики
    # «сходились» с пустым перечнем находок: гейт зеленел.
    try:
        registry = ex.load_registry(out_dir / "registry.json",
                                    expect_file=True)
    except ValueError as e:
        problems.append(str(e))
        return problems, c
    ex.normalize_registry_keys(registry)
    # Сопоставление тупика с записью реестра — ОДНОЙ функцией с судом
    # (`ex.match_listed_deadlocks`, ревью A09 №2, находка 6): пара
    # (id, ключ), а для перечней без ключей — запасной проход по id.
    # Прежде у гейта был свой проход, а у арбитража — только пара: по
    # старому `verdict.json` арбитр не судил ничего, пока гейт судил
    # находку, и предмет у них расходился.
    entry_of = ex.match_listed_deadlocks(registry, deadlocks)
    unresolved_ids = {str(u.get("deadlock"))
                      for u in _rows(report, "unresolved")[0]}
    unenforced_ids = {str(u.get("deadlock"))
                      for u in _rows(report, "unenforced")[0]}
    awaiting_of = {str(a.get("deadlock")): a
                   for a in _rows(report, "awaiting_owner")[0]}
    for f in deadlocks:
        entry = entry_of.get(f["id"]) or {}
        state = entry.get("state")
        v = by_id.get(f["id"])
        # A09: спор, оставшийся без однозначного решения, живёт в
        # реестре своим состоянием — не «тупик» (спор разбирался) и не
        # «арбитр: …» (решения нет). Причина обязана быть при находке.
        if f["id"] in unresolved_ids:
            if state != "не разрешено арбитром":
                problems.append(f"тупик {f['id']}: состояние реестра "
                                f"{state!r} не соответствует записи "
                                f"«не разрешено» "
                                f"('не разрешено арбитром')")
            elif not str(entry.get("arbiter_unresolved") or "").strip():
                problems.append(f"тупик {f['id']}: состояние «не "
                                f"разрешено арбитром» без причины "
                                f"словами в реестре")
            continue
        if state == "не разрешено арбитром":
            problems.append(f"тупик {f['id']}: состояние реестра «не "
                            f"разрешено арбитром» без записи "
                            f"«не разрешено» в отчёте арбитража")
            continue
        # §12.8 + ревью A09 №2, находка 3: спор, переданный владельцу
        # вопросом анкеты, ждёт его слова — арбитру он в этом заходе
        # не предъявлялся. Ожидание законно только у спора, которому
        # заплату запрещает замок кода, и только пока он ЖИВОЙ: иначе
        # «ожидание» стало бы способом закрыть находку без решения.
        # Сам вопрос (существует ли он и открыт ли) судится ниже, по
        # модели после арбитража.
        if f["id"] in awaiting_of:
            if not ex.code_locked_dispute(entry,
                                          dossiers.get(f["id"])):
                problems.append(f"тупик {f['id']}: запись «ждёт ответа "
                                f"владельца» по спору, которому замок "
                                f"кода заплату не запрещал")
            if state != "тупик":
                problems.append(f"тупик {f['id']}: состояние реестра "
                                f"{state!r} закрыто, хотя спор ждёт "
                                f"ответа владельца")
            if f["id"] in enforced:
                problems.append(f"тупик {f['id']}: числится "
                                f"исполненным, хотя спор ждёт ответа "
                                f"владельца")
            continue
        # §12.8: тупик, дозревший из ТЕРМИНАЛЬНОЙ отбраковки замком
        # кода, законной заплаты не имеет — «аудитор прав» по нему
        # исполняется передачей спора владельцу: причина при находке,
        # вопрос анкеты, живое состояние «тупик» и строка в unenforced.
        # Любой другой способ не исполнить «аудитор прав» остаётся
        # нарушением; сам ярлык вида тупика ничего не разрешает —
        # нужны ВСЕ четыре следа разом.
        #
        # Запрет судится по ЛЮБОМУ носителю (ревью A09, находка 1):
        # поля реестра ИЛИ терминальная отбраковка в файлах раундов.
        # Прежде гейт верил одному реестру — подделка ОБОИХ носителей
        # (реестр закрыл находку решением, отчёт объявил исполнение)
        # уводила спор на обычную дорогу, а проверку «строка-цель
        # изменена» проходил законный след вопроса владельцу
        # (блокировка строки-носителя), и гейт был зелёным.
        if v == "аудитор прав" \
                and ex.code_locked_dispute(entry,
                                           dossiers.get(f["id"])):
            if f["id"] in enforced:
                # Заплаты по такому тупику не существует: объявленное
                # исполнение — подделка, даже если модель после
                # арбитража изменилась (её законно меняет вопрос
                # владельцу, блокируя строку-носитель).
                problems.append(f"тупик {f['id']}: числится "
                                f"исполненным заплатой, которую "
                                f"запрещает замок кода")
                continue
            if f["id"] not in questioned:
                problems.append(f"тупик {f['id']}: заплату запрещает "
                                f"замок кода, а вопрос владельцу не "
                                f"поставлен — спор потерян")
            if not str(entry.get("arbiter_unenforced") or "").strip():
                problems.append(f"тупик {f['id']}: заплата запрещена "
                                f"замком кода, а причина при находке "
                                f"(arbiter_unenforced) не названа")
            if f["id"] not in unenforced_ids:
                problems.append(f"тупик {f['id']}: неисполнимый "
                                f"вердикт «аудитор прав» не назван в "
                                f"перечне unenforced отчёта")
            if state != "тупик":
                problems.append(f"тупик {f['id']}: состояние реестра "
                                f"{state!r} закрыто, хотя заплата "
                                f"запрещена замком кода и спор ждёт "
                                f"владельца")
            continue
        # Вердикт без исполнения: заплата не принята / вопрос не
        # создан — нарушение само по себе; закрытое состояние при
        # этом — второе нарушение (находка закрыта без исполнения).
        executed = (v == "аудитор прав" and f["id"] in enforced) \
            or (v == "в анкету" and f["id"] in questioned) \
            or v == "обработчик прав"
        if v in ("аудитор прав", "в анкету") and not executed:
            what = ("заплата не принята" if v == "аудитор прав"
                    else "вопрос анкеты не создан")
            problems.append(f"тупик {f['id']}: вердикт «{v}» не "
                            f"исполнен — {what}; находка остаётся "
                            f"открытой")
            if state != "тупик":
                problems.append(f"тупик {f['id']}: состояние реестра "
                                f"{state!r} закрыто без исполнения "
                                f"вердикта")
            continue
        if state == "тупик":
            problems.append(f"тупик {f['id']}: остался в состоянии "
                            f"«тупик» после арбитража")
        expected = _STATE_BY_VERDICT.get(v)
        if expected and state != expected:
            problems.append(f"тупик {f['id']}: состояние реестра "
                            f"{state!r} не соответствует вердикту "
                            f"({expected!r})")

    # A09/X-03: СПОР — ЭТО КЛЮЧ. Находки одного ключа несут ОДИН исход.
    # Пока спор зрел, каждый повтор открывал отдельную находку; предъяви
    # их арбитру порознь — он давал им разные решения, ключ считался
    # решённым и реестр очищался: прямой контрпример §1.5 контракта П0
    # («ни одно решение не принимается при наличии противоречащего») на
    # уровне спора. Решение на одной находке и молчание на другой —
    # то же нарушение: ключ гасят обе.
    outcome_by_key: dict = {}
    for f in deadlocks:
        entry = entry_of.get(f["id"]) or {}
        key = entry.get("key") or f.get("key")
        if not key:
            continue
        if f["id"] in unresolved_ids:
            outcome = "не разрешено"
        elif f["id"] in awaiting_of:
            outcome = "ждёт ответа владельца"
        else:
            outcome = by_id.get(f["id"])
        outcome_by_key.setdefault(key, {})[f["id"]] = outcome
    for key, per in sorted(outcome_by_key.items()):
        if len({str(o) for o in per.values()}) > 1:
            problems.append(
                "спор одного ключа рассужен по-разному: "
                + ", ".join(f"{i} → {o!r}"
                            for i, o in sorted(per.items()))
                + " — спор это ключ, решение одно на все его находки")

    # Diff-only: до/после арбитража.
    old_doc = _load(out_dir / "versions"
                    / verdict_summary["final_version"])
    new_doc = _load(out_dir / "versions" / report["final_version"])
    c["rules_diffed"] = len(new_doc.get("rules", []))
    allowed: set[str] = set()
    old_rules_by_id = {r["id"]: r for r in old_doc.get("rules", [])}
    new_rules_by_id = {r["id"]: r for r in new_doc.get("rules", [])}
    for f in deadlocks:
        rm = dossiers[f["id"]]["remark"] or {}
        target = (rm.get("target") or {}).get("rule")
        if by_id.get(f["id"]) != "аудитор прав" \
                or f["id"] not in enforced:
            continue
        # Объявленное исполнение запрещённой замком заплаты уже названо
        # нарушением выше; строку-цель оно НЕ обеляет: разрешать её в
        # диффе значило бы пропустить саму незаконную правку мимо
        # замка diff-only (ревью A09, находка 1).
        entry = entry_of.get(f["id"]) or {}
        if ex.code_locked_dispute(entry, dossiers.get(f["id"])):
            continue
        # Законная цель диффа — только ИСПОЛНЕННЫЙ вердикт; и след
        # исполнения обязан быть в модели: строка-цель изменена
        # (пропуск «+» — появилась новая строка). Отчёт, назвавший
        # тупик исполненным при неизменной модели, — нарушение.
        target = target if target else "+"
        allowed.add(target)
        if target == "+":
            changed = any(rid not in old_rules_by_id
                          for rid in new_rules_by_id)
        else:
            changed = old_rules_by_id.get(target) \
                != new_rules_by_id.get(target)
        if not changed:
            problems.append(f"тупик {f['id']}: числится исполненным, "
                            f"но строка-цель {target!r} в модели "
                            f"после арбитража не изменена")
    q_rules = set()
    for q in new_doc.get("questions", []):
        if q.get("id") in added_q:
            q_rules.update(q.get("rule_refs", []))
    problems += [f"после арбитража: {p}" for p in
                 ex.check_diff_only(old_doc, new_doc, allowed | q_rules)]
    old_q = {q["id"] for q in old_doc.get("questions", [])}
    for q in new_doc.get("questions", []):
        if q["id"] not in old_q and q["id"] not in added_q:
            problems.append(f"вопрос {q['id']} добавлен вне списка "
                            f"арбитража")

    # «В анкету»: развилка из двух чтений, строка-носитель заблокирована.
    new_q = {q["id"]: q for q in new_doc.get("questions", [])}
    new_rules = {r["id"]: r for r in new_doc.get("rules", [])}
    for qid in added_q:
        q = new_q.get(qid)
        if q is None:
            problems.append(f"вопрос арбитража {qid} отсутствует в "
                            f"модели")
            continue
        if q.get("part") != "2":
            problems.append(f"вопрос {qid}: часть {q.get('part')!r}, "
                            f"ожидалась «2»")
        keys = [o.get("key")
                for o in q.get("answer_format", {}).get("options", [])]
        if keys != ["а", "б"]:
            problems.append(f"вопрос {qid}: развилка {keys} вместо "
                            f"двух чтений (а/б)")
        for rid in q.get("rule_refs", []):
            if qid not in new_rules.get(rid, {}).get("blockers", []):
                problems.append(f"вопрос {qid}: строка {rid} не "
                                f"заблокирована до ответа")
    # §12.8 + ревью A09 №2, находка 3: «ждёт ответа владельца» законно,
    # только пока вопрос владельцу ЖИВ и ОТКРЫТ. Отвечённый вопрос
    # снимает ожидание — спор обязан вернуться к арбитру; вопроса,
    # которого нет в модели, нет и для спора.
    for did, row in sorted(awaiting_of.items()):
        qid = row.get("question")
        q = new_q.get(qid) if isinstance(qid, str) else None
        if q is None:
            problems.append(f"тупик {did}: вопрос владельцу {qid!r}, "
                            f"которым спор передан, отсутствует в "
                            f"модели после арбитража")
        elif not m.is_open_question(q):
            problems.append(f"тупик {did}: вопрос владельцу {qid} уже "
                            f"отвечен — спор обязан вернуться к "
                            f"арбитру, а не ждать")

    model_qids = {q.get("id") for q in new_doc.get("questions", [])}
    for f in deadlocks:
        # След переиспользования судится у ЛЮБОГО тупика, который его
        # несёт: вопрос рождают два исхода — «в анкету» и «аудитор
        # прав» при запрете замка кода (§12.8), — и запись о
        # несуществующем вопросе одинаково лжива в обоих.
        # Переиспользование: тупик закрыт СУЩЕСТВУЮЩИМ вопросом-дублем
        # по формулировке — след question_reuse, вопрос обязан жить в
        # модели (живой урок UniA run-0051). Вердикт без вопроса
        # (нового или переиспользованного) уже назван нарушением в
        # блоке реестра выше — объяснение в отбраковке его не извиняет.
        reused_q = reuse.get(f["id"])
        if reused_q is not None and reused_q not in model_qids:
            problems.append(f"тупик {f['id']}: переиспользованный "
                            f"вопрос {reused_q} отсутствует в модели")

    problems += [f"модель после арбитража: {p}"
                 for p in m.validate_schema(new_doc)
                 + m.validate_model(new_doc)]
    return problems, c


def run(out_dir: str) -> int:
    _selftest()
    problems, c = check(Path(out_dir))
    print(f"рассмотрено: тупиков {c['deadlocks']}, вердиктов "
          f"{c['verdicts']}, не разрешено {c['unresolved']}, "
          f"ждут владельца {c.get('awaiting', 0)}, "
          f"исполнений {c['enforced']}, вопросов {c['questions']}, "
          f"строк в диффе {c['rules_diffed']} (самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: арбитраж соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(description="Гейт арбитража CDPM")
    ap.add_argument("out", help="каталог цикла (exchange)")
    args = ap.parse_args(argv)
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
