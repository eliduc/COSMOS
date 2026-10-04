# -*- coding: utf-8 -*-
"""Ремонт по находкам кода (план «прямой ход» 2.6): код называет
клаузу, непотреблённую величину и перечень законных слотов; модель
заполняет ровно этот слот с дословной цитатой; код проверяет и вносит.
До трёх попыток на находку, третья — другим семейством моделей (если
дано); после — находка остаётся в отчёте (вопрос или остановка решает
вызывающая сторона). Ремонт ведёт КОД, а не второй языковой судья:
это отличие от цикла «аудитор — обработчик», где обе стороны угадывают.

Слоты v1: у целевой — rate {value, per, as_written} (немашинный
критерий), criterion.per; у правила — weight (число из клаузы) и
параметр посадки (имя из семьи, значение, цитата). Пары «величина —
слот» модели не подсказываются (проверка С4): даётся перечень
законных слотов строки.

    repair(doc, entries, findings, llm, alt_llm=None, max_attempts=3) -> отчёт
"""
from __future__ import annotations

import copy
import json
import re

from cdpm import llm as llm_mod
from cdpm import model as m

_PROMPT = """Ты — формализатор правил авиакомпании. Код нашёл в клаузе
документа величину, которую машинные поля строки свода не потребили.
Заполни РОВНО ОДИН недостающий слот строки по тексту клаузы — с
дословной цитатой. Ничего не выдумывай: если величина в клаузе не
относится ни к одному из законных слотов, верни {{"slot": null,
"reason": "<почему>"}}.

Клауза [{anchor}]:
{clause}

Строка свода (машинная форма):
{row}

Непотреблённая величина: {value!r} (класс {cls})

Законные слоты этой строки (выбери один):
{slots}

Верни СТРОГО один JSON-объект:
{{"slot": "<имя слота из перечня>",
  "value": <число либо строка — как требует слот>,
  "per": "<для ставки: за что начисляется, дословно из клаузы; иначе опусти>",
  "quote": "<ДОСЛОВНЫЙ фрагмент клаузы с величиной; несколько дословных фрагментов — через «...»>",
  "reason": null}}
Для слота criterion дополнительно: "kind" (метка критерия из словаря),
"rate_per_duty" (целое, ставка за единицу — для min_person_days),
"version" и "effective_from" — как написано в клаузе."""


def _slots_for(row: dict, is_objective: bool, fams: dict) -> list[dict]:
    if is_objective:
        out = []
        if isinstance(row.get("criterion"), dict):
            if not row["criterion"].get("per"):
                out.append({"name": "criterion.per",
                            "what": "за что начисляется ставка машинного критерия (строка)"})
        else:
            # Машинный критерий, названный документом (прогон №5 H0: ставка
            # 100 была ставкой критерия MIN_PERSON_DAYS, а ремонт предлагал
            # только слот немашинной ставки — модель трижды честно
            # отказалась).
            out.append({"name": "criterion",
                        "what": "машинный критерий, названный документом: kind из словаря "
                                f"{sorted(m.CRITERION_KINDS)}, rate_per_duty (целое > 0), "
                                "per — за что, version, effective_from; quote — дословный "
                                "фрагмент клаузы с критерием и ставкой"})
            if not isinstance(row.get("rate"), dict):
                out.append({"name": "rate",
                            "what": "ставка немашинного критерия: value — число > 0 в валюте "
                                    "ВМП, per — за что начисляется"})
            elif not row["rate"].get("per"):
                out.append({"name": "rate.per", "what": "за что начисляется ставка (строка)"})
        return out
    out = []
    fit = row.get("template_fit") if isinstance(row.get("template_fit"), dict) else {}
    fam = fit.get("family")
    if row.get("kind") == "SOFT" and row.get("weight") is None:
        out.append({"name": "weight", "what": "коэффициент штрафа — целое число из клаузы"})
    if fam and fit.get("category") in ("A", "B"):
        # С-1, ТРЕТЬЕ место того же закона (находка аудитора GPT174).
        # Прежнее `fit.get("params") or {}` спасало только ЛОЖНЫЕ формы:
        # `0`, `[]`, `None` дают `{}`, а ИСТИННОЕ не-хранилище остаётся
        # собой — и `pname not in 1` роняет `TypeError` ЗДЕСЬ, ДО рубежа
        # в `_apply`. Проверено: `1`, `7.5`, `True` — падение; `0` —
        # единственное число, которое проскакивало, и именно его взяла
        # прежняя проба, поэтому дыру не нашла.
        #
        # Кривая форма = ячеек НЕТ: слоты предлагаются, а запись потом
        # получает названный отказ рубежа `_apply` — fail-closed со
        # словами, а не трассировка (закон C-1: «не даёт, но и не
        # роняет расчёт»).
        ячейки = fit.get("params")
        ячейки = ячейки if isinstance(ячейки, dict) else {}
        for pname in sorted(fams.get(fam) or []):
            if pname not in ячейки:
                out.append({"name": f"param:{pname}",
                            "what": f"параметр семьи {fam} ({m.PARAMS.get(pname, '?')}); "
                                    f"длительности и время — в минутах"})
    # Посадка, предложенная моделью и отброшенная кодом за неполноту
    # (нет цитаты-носителя у беспараметрной семьи, нет params у семьи со
    # словарём) — ремонтируется адресно: семья названа, недостаёт слота
    # (проход №6 H0: VN-050 → C без цитаты, хотя семья верна).
    # Посадка из независимого второго чтения — внутренний слот кода
    # (моделью не заполняется): у строки без посадки A/B принимается
    # посадка второго читателя теми же проверками, что у извлечения.
    if fit.get("category") not in ("A", "B") \
            and "спор о чтении" not in str(fit.get("gap") or ""):
        out.append({"name": "fit_from_second", "internal": True,
                    "what": "посадка из второго чтения (только подсказкой кода)"})
    proposed = rejected_family(fit)
    if proposed:
        if fams.get(proposed):
            for pname in sorted(fams[proposed]):
                out.append({"name": f"param:{pname}",
                            "what": f"параметр отброшенной посадки на семью {proposed} "
                                    f"({m.PARAMS.get(pname, '?')}); длительности и время "
                                    f"— в минутах; принятие восстанавливает посадку"})
        else:
            out.append({"name": f"fit_quote:{proposed}",
                        "what": f"цитата-носитель нормы для беспараметрной семьи "
                                f"{proposed}: дословный фрагмент клаузы, несущий норму "
                                f"(quote); принятие восстанавливает посадку"})
    return out


_REJECTED_FAMILY_RE = re.compile(
    r"семья '([a-z_]+)'(?: со словарными параметрами)? — "
    r"(?:params обязателен|обязателен params либо quote)")


def rejected_family(fit: dict) -> str | None:
    """Семья, предложенная моделью и отброшенная кодом за неполноту
    посадки (не за спор о чтении): из пробела C-посадки."""
    if not isinstance(fit, dict) or fit.get("category") != "C":
        return None
    gap = str(fit.get("gap") or "")
    if not gap.startswith("посадка модели отброшена"):
        return None
    mm = _REJECTED_FAMILY_RE.search(gap)
    return mm.group(1) if mm else None


def _apply(row: dict, is_objective: bool, slot: str, reply: dict,
           clause_text: str, doc: dict | None = None) -> str | None:
    """Внесение слота с проверкой кодом; None — внесено, иначе причина.

    `doc` — ПОЛНЫЙ документ модели: цитатный слой посадки судит
    происхождение значений, внесённых ответами анкеты, и требует
    анкету (контракт П0 §2.3 п. 2); срез строки для этого непригоден."""
    quote = " ".join(str(reply.get("quote") or "").split())
    anchored_value = reply.get("value") if slot in ("weight", "rate") \
        else reply.get("rate_per_duty") if slot == "criterion" else None
    if not quote or not (m.quote_in_text(quote, clause_text)
                         or m.value_quote_in_text(quote, anchored_value, clause_text)):
        return "цитата не из клаузы"
    if slot == "fit_from_second":
        fit2, _conf, note = m.accept_fit_candidate(
            {"id": row.get("id"), "kind": row.get("kind"), "template_fit": reply.get("fit")},
            clause_text, new_output=True)
        if not isinstance(fit2, dict) or fit2.get("category") not in ("A", "B"):
            return f"посадка второго чтения не принята: {note or (fit2 or {}).get('gap')}"
        # Принимается вся строка, а не только новая посадка: прежний
        # params не должен стать второй противоречащей правдой.
        problems = (m.reconcile_carriers(row)
                    or m.reconcile_carriers({**row, "template_fit": fit2}))
        if problems:
            return "посадка второго чтения не принята: " + "; ".join(problems)
        row["template_fit"] = fit2
        return None
    if slot == "criterion":
        crit = {"kind": reply.get("kind") or reply.get("value"),
                "rate_per_duty": reply.get("rate_per_duty"),
                "per": reply.get("per"), "version": reply.get("version"),
                "effective_from": reply.get("effective_from"), "as_written": quote}
        block, why = m.accept_objective_criterion(crit, clause_text=clause_text)
        if block is None:
            return why
        row["criterion"] = block
        row.pop("rate", None)                 # ставка немашинного критерия несовместима
        return None
    if slot == "rate":
        block, why = m.accept_objective_rate(
            {"value": reply.get("value"), "per": reply.get("per"), "as_written": quote},
            clause_text=clause_text)
        if block is None:
            return why
        row["rate"] = block
        return None
    if slot in ("rate.per", "criterion.per"):
        per = " ".join(str(reply.get("per") or reply.get("value") or "").split())
        if not per or m._fit_norm_text(per) not in m._fit_norm_text(clause_text):
            return "«за что» не из клаузы"
        target = row["criterion"] if slot == "criterion.per" else row["rate"]
        target["per"] = per
        return None
    if slot == "weight":
        v = reply.get("value")
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return "вес не целое ≥ 0"
        if str(v) not in clause_text:
            return "числа веса нет в клаузе"
        row["weight"] = v
        return None
    if slot.startswith("fit_quote:"):
        fam = slot.split(":", 1)[1]
        fit, _conf, note = m.accept_fit_candidate(
            {"id": row.get("id"), "kind": row.get("kind"),
             "template_fit": {"category": "A", "family": fam, "quote": quote}},
            clause_text, new_output=True)
        if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
            return f"посадка на {fam!r} не принята: {note or (fit or {}).get('gap')}"
        problems = (m.reconcile_carriers(row)
                    or m.reconcile_carriers({**row, "template_fit": fit}))
        if problems:
            return "посадка не принята: " + "; ".join(problems)
        row["template_fit"] = fit
        return None
    if slot.startswith("param:"):
        pname = slot.split(":", 1)[1]
        v = reply.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return "значение параметра не число и не строка"
        fit = row.get("template_fit") if isinstance(row.get("template_fit"), dict) else {}
        proposed = rejected_family(fit)
        if proposed:
            # Восстановление отброшенной посадки: семья из пробела,
            # параметр из ответа; приём — тем же кодом, что у извлечения.
            # С-1, ТРЕТЬЕ место ТОЙ ЖЕ дороги (находка неавторского
            # ревью 13.09.2026). `dict(x or {})` спасает только ЛОЖНЫЕ
            # формы: `0`, `[]`, `None` дают `{}`, а истинное
            # не-отображение остаётся собой — `dict("нет")` роняет
            # `ValueError`, `dict(5)` и `dict([1, 2])` — `TypeError`.
            # Ветвь стоит ВЫШЕ рубежа `_apply` и возвращает до него,
            # поэтому тот её не прикрывал.
            #
            # Спрашиваем форму ТЕМ ЖЕ законом и отказываем ТЕМИ ЖЕ
            # словами, что обе соседние половины: один закон — одни
            # слова. Восстановление отброшенной посадки строит НОВУЮ
            # посадку, и молча выбросить при этом кривые ячейки значило
            # бы затереть чужую форму — ровно то, что С-1 запрещает.
            # Отсутствие ключа и `params: null` — РАЗНОЕ (тот же закон,
            # что у `_cells_writable`): ключа нет — заводим пустые;
            # ключ есть со значением не-словарём — кривая форма.
            ячейки = fit.get("params")
            if "params" in fit and not isinstance(ячейки, dict):
                return (f"ячейки посадки правила {row.get('id')} не "
                        f"объект — значение записать некуда; сначала "
                        f"поправьте посадку")
            params = dict(ячейки or {})
            params[pname] = {"value": v, "quote": quote}
            new_fit, _conf, note = m.accept_fit_candidate(
                {"id": row.get("id"), "kind": row.get("kind"),
                 "template_fit": {"category": "A", "family": proposed, "params": params}},
                clause_text, new_output=True)
            if not isinstance(new_fit, dict) or new_fit.get("category") not in ("A", "B"):
                return f"посадка на {proposed!r} не принята: {note or (new_fit or {}).get('gap')}"
            problems = (m.reconcile_carriers(row)
                        or m.reconcile_carriers({**row, "template_fit": new_fit}))
            if problems:
                return "посадка не принята: " + "; ".join(problems)
            row["template_fit"] = new_fit
            return None
        # С-1, второй корень (находка неавторского ревью 13.09.2026):
        # `setdefault` у СУЩЕСТВУЮЩЕГО ключа отдаёт ПРЕЖНЕЕ значение, и
        # на `params: null` ремонт падал `TypeError`, на `params: []` —
        # тоже. Закон C-1 объявлен в продукте общо
        # (`family_params._landing_cells`: кривая форма ячеек «не даёт,
        # но и НЕ РОНЯЕТ РАСЧЁТ трассировкой»), и ремонт ему
        # противоречил. Дорога живая: слоты такой строке предлагаются
        # (`_slots_for` читает `fit.get("params") or {}`), а `_apply`
        # зовётся без `try/except`.
        #
        # Спрашиваем форму ДО первого `setdefault`: отказавший проход
        # не должен оставлять за собой ни `template_fit: {}`, ни
        # пустой `params` («до присвоения значения не дошли» не равно
        # «документ не изменили»). Слова — те же, что у живого пути
        # анкеты (`family_params.apply_param_answer`): один закон,
        # одни слова.
        есть = row.get("template_fit")
        if isinstance(есть, dict) and "params" in есть                 and not isinstance(есть["params"], dict):
            return (f"ячейки посадки правила {row.get('id')} не объект — "
                    f"значение записать некуда; сначала поправьте посадку")
        # Заполняется новый слот, не переписывается прежнее свидетельство.
        # Подготовка и все проверки идут на копии; отказ не оставляет
        # пустых словарей и не снимает ранее сохранённую ячейку.
        from cdpm import catalog_bridge, family_params
        from cdpm.extract import _whole_value_quote
        from template_catalog import landing
        if isinstance(есть, dict) and pname in (есть.get("params") or {}):
            return f"параметр {pname} уже заполнен — нужен явный пересмотр"
        if not _whole_value_quote(clause_text, quote):
            return f"{pname}: цитата обрезает число/единицу или содержит номер ссылки"
        trial = copy.deepcopy(row)
        cell = {"value": v, "quote": quote}
        if isinstance(есть, dict) and есть.get("schema_version") == "landing/2":
            try:
                cell = landing.prepare_cell(row, pname, {
                    **cell, "source": {"kind": "rule", "rule_id": row.get("id")}},
                    catalog_bridge.catalog())
            except catalog_bridge.CATALOG_ERRORS as error:
                return f"{pname}: typed-ячейка не построена: {error}"
            # Судится только НОВАЯ ячейка по исходному pin. Соседние
            # значения и их источники ответа/миграции не пересоздаются.
            probe_fit = {**copy.deepcopy(есть), "params": {pname: cell}}
            checked, _conf, note = m.accept_fit_candidate(
                {"id": row.get("id"), "kind": row.get("kind"),
                 "template_fit": probe_fit}, clause_text, new_output=True)
            if not isinstance(checked, dict) or checked.get("category") not in ("A", "B"):
                return f"{pname}: значение не принято: {note or (checked or {}).get('gap')}"
            cell = checked["params"][pname]
        else:
            # Совместимая старая форма остаётся старой, но значение
            # всё равно должно читаться из самой цитаты, не только
            # встречаться где-то в клаузе.
            spec = family_params._param_spec(str(fit.get("family") or ""), pname) or {}
            unit = spec.get("unit")
            if not unit:
                return f"{pname}: параметр не объявлен у семьи посадки"
            parsed = family_params.parse_rule_quote(quote, str(unit or ""),
                                                    source=catalog_bridge.catalog().path)
            if parsed is None or parsed[0] != v:
                return f"{pname}: значение не подтверждено цитатой клаузы"
        problem = family_params.write_cell(trial, pname, cell)
        if problem:
            return problem
        probs = m.validate_fit_quotes(
            {"questions": list((doc or {}).get("questions") or []),
             "rules": [trial]},
            {(s.get("sid"), s.get("anchor")): clause_text
             for s in row.get("sources") or []
             if isinstance(s, dict)})
        if probs:
            return probs[0]
        row.clear()
        row.update(trial)
        return None
    return f"неизвестный слот {slot!r}"


def _hint_reply(slot: str, hint: dict) -> dict | None:
    """Ответ «как от модели» из подсказки второго чтения для слота —
    либо None, если подсказка слот не несёт."""
    fit = hint.get("fit") if isinstance(hint.get("fit"), dict) else {}
    if slot == "weight" and isinstance(hint.get("weight"), int) \
            and not isinstance(hint.get("weight"), bool):
        return {"value": hint["weight"], "quote": str(hint["weight"])}
    if slot == "fit_from_second":
        if fit.get("category") in ("A", "B") and fit.get("family"):
            return {"fit": {k: v for k, v in fit.items() if k != "registry_rev"},
                    "quote": fit.get("quote") or next(
                        (str(c.get("quote")) for c in (fit.get("params") or {}).values()
                         if isinstance(c, dict) and c.get("quote")), "")}
        return None
    if slot.startswith("fit_quote:"):
        fam = slot.split(":", 1)[1]
        if fit.get("family") == fam and fit.get("quote"):
            return {"quote": fit["quote"]}
        return None
    if slot.startswith("param:"):
        pname = slot.split(":", 1)[1]
        cell = (fit.get("params") or {}).get(pname)
        if isinstance(cell, dict) and cell.get("quote") is not None:
            return {"value": cell.get("value"), "quote": cell["quote"]}
        return None
    crit = hint.get("criterion") if isinstance(hint.get("criterion"), dict) else None
    rate = hint.get("rate") if isinstance(hint.get("rate"), dict) else None
    if slot == "criterion" and crit:
        return {"kind": crit.get("kind"), "rate_per_duty": crit.get("rate_per_duty"),
                "per": crit.get("per"), "version": crit.get("version"),
                "effective_from": crit.get("effective_from"),
                "quote": crit.get("as_written")}
    if slot == "rate" and rate:
        return {"value": rate.get("value"), "per": rate.get("per"),
                "quote": rate.get("as_written")}
    if slot == "criterion.per" and crit and crit.get("per"):
        return {"per": crit["per"], "quote": crit["per"]}
    if slot == "rate.per" and rate and rate.get("per"):
        return {"per": rate["per"], "quote": rate["per"]}
    return None


def _bal_consumed(finding: dict, row: dict, *, clause_text_by_key=None, doc=None) -> bool:
    from cdpm import balance as _bal
    probe = {"class": finding.get("class"), "value": finding.get("value"),
             "text": finding.get("text")}
    return _bal._consumed(probe, _bal._row_pools(
        row, clause_text_by_key=clause_text_by_key, doc=doc))


def repair(doc: dict, entries: list[dict], findings: list[dict], llm,
           alt_llm=None, max_attempts: int = 3, hints: dict | None = None) -> dict:
    """Пытается закрыть находки баланса адресными запросами модели.
    `hints` — подсказки второго чтения по носителям {(sid, anchor):
    [{weight, fit} | {criterion, rate}]}: слот из подсказки вносится
    теми же проверками кода без обращения к модели (попытка «по второму
    чтению»). Возвращает {"fixed": [...], "left": [...], "attempts": n}."""
    clause_by_key = {(str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
                     for e in entries for c in e.get("clauses") or []}
    rows_by_id = {r.get("id"): (r, False) for r in doc.get("rules", [])}
    rows_by_id.update({o.get("id"): (o, True) for o in doc.get("objectives", [])})
    fams = m.template_families()
    fixed: list[dict] = []
    left: list[dict] = []
    attempts = 0
    for f in findings:
        if f.get("class") in ("operator",) or not f.get("rows"):
            left.append(dict(f, repair="нет строки-носителя или не величина"))
            continue
        clause_text = clause_by_key.get((str(f["sid"]), str(f["anchor"])), "")
        done = False
        reasons: list[str] = []
        # Величина, потреблённая ремонтом соседней находки той же клаузы
        # («100 условных единиц» и «100» — один слот), модели не задаётся.
        from cdpm import balance as _bal
        probe = {"class": f.get("class"), "value": f.get("value"), "text": f.get("text")}
        if any(rows_by_id.get(rid) and _bal._consumed(probe, _bal._row_pools(
                rows_by_id[rid][0], clause_text_by_key=clause_by_key, doc=doc))
               for rid in f["rows"]):
            fixed.append(dict(f, repair="потреблена слотом, внесённым ремонтом соседней "
                                        "находки"))
            continue
        for rid in f["rows"]:
            row, is_obj = rows_by_id.get(rid, (None, False))
            if row is None:
                continue
            slots = _slots_for(row, is_obj, fams)
            if not slots:
                reasons.append(f"{rid}: законных пустых слотов нет")
                continue
            # Попытка по второму чтению — без модели.
            for hint in (hints or {}).get((str(f["sid"]), str(f["anchor"])), []):
                for s in slots:
                    reply = _hint_reply(s["name"], hint)
                    if reply is None:
                        continue
                    trial = copy.deepcopy(row)
                    err = _apply(trial, is_obj, s["name"], reply,
                                 clause_text, doc)
                    if err:
                        reasons.append(f"по второму чтению ({s['name']}): {err}")
                        continue
                    if not _bal_consumed(f, trial, clause_text_by_key=clause_by_key, doc=doc):
                        continue           # слот внесён бы, но величину не потребляет
                    row.clear()
                    row.update(trial)
                    fixed.append(dict(f, repair=f"{rid}: слот {s['name']} внесён по "
                                                f"второму чтению"))
                    done = True
                    break
                if done:
                    break
            if done:
                break
            for attempt in range(1, max_attempts + 1):
                chan = alt_llm if (attempt == max_attempts and alt_llm is not None) else llm
                attempts += 1
                public_slots = [s for s in slots if not s.get("internal")]
                if not public_slots:
                    reasons.append(f"{rid}: законных слотов для модели нет")
                    break
                prompt = _PROMPT.format(
                    anchor=f["anchor"], clause=clause_text,
                    row=json.dumps({k: v for k, v in row.items()
                                    if k not in ("logic", "blockers")},
                                   ensure_ascii=False),
                    value=f.get("text"), cls=f.get("class"),
                    slots="\n".join(f"- {s['name']}: {s['what']}" for s in public_slots))
                try:
                    reply = llm_mod.parse_json_reply(chan(prompt))
                except Exception as e:                       # noqa: BLE001
                    reasons.append(f"попытка {attempt}: ответ не разобран ({str(e)[:60]})")
                    continue
                slot = reply.get("slot")
                if not slot:
                    reasons.append(f"попытка {attempt}: модель отказалась: "
                                   f"{str(reply.get('reason') or '')[:80]}")
                    continue
                if slot not in {s["name"] for s in slots if not s.get("internal")}:
                    reasons.append(f"попытка {attempt}: слот {slot!r} вне перечня")
                    continue
                trial = copy.deepcopy(row)
                err = _apply(trial, is_obj, slot, reply, clause_text, doc)
                if err:
                    reasons.append(f"попытка {attempt}: {err}")
                    continue
                row.clear()
                row.update(trial)
                fixed.append(dict(f, repair=f"{rid}: слот {slot} внесён (попытка {attempt})"))
                done = True
                break
            if done:
                break
        if not done:
            left.append(dict(f, repair="; ".join(reasons)[:300]))
    return {"fixed": fixed, "left": left, "attempts": attempts}
