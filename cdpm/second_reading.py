# -*- coding: utf-8 -*-
"""Слой 2 против самосогласованного неверного чтения — перекрёстное
чтение (план «прямой ход» 2.7.2, решение владельца 02.09).

Две модели РАЗНЫХ семейств независимо читают одни и те же порции клауз
(извлечение и роль `second_reader`); обе выдачи проходят один и тот же
код сборки (посадка, ярлыки, дубли). Код сравнивает по каждому носителю
(первичная клауза) канонический слой обеих сборок — вид, зона, семья,
параметры в канонических единицах, вес; у целевых — зона, направление,
метрика, вид критерия, ставка, «за что». Совпало — строка принята с
пометкой двух чтений; разошлось — ЕДИНСТВЕННЫЙ маршрут: вопрос части 2 с
двумя чтениями и блокировкой строк носителя; арбитр семью не выбирает.

    compare(doc1, doc2, verdicts1, verdicts2) -> отчёт
    add_questions(asm, report, clause_text_by_key) -> число вопросов
"""
from __future__ import annotations

import json
import re

from cdpm import model as m


def _canon_rule(r: dict) -> dict:
    fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
    sig = m.fit_signature(r)
    # Зона у строк без исполнения (определения, данные, снятые) — место в
    # форме, не часть задачи решателя: в канон сравнения не входит
    # (проход №13 H0: BOTH против ROSTERING у перечня баз).
    zone = r.get("zone") if r.get("kind") in ("HARD", "SOFT", "CASE") else None
    return {"kind": r.get("kind"), "zone": zone,
            "category": fit.get("category"),
            "family": fit.get("family") if fit.get("category") in ("A", "B") else None,
            "params": dict(sig[1]) if sig else {},
            "weight": r.get("weight")}


_PER_LEAD_RE = re.compile(r"^(?:за\s+)?(?:кажд(?:ый|ую|ое|ые)\s+|один\s+|одну\s+|одно\s+)?")


def _norm_per(s) -> str:
    """Канон «за что»: без ведущего «за (каждый|один)» — «за час разброса»
    и «час разброса» одно и то же (проход №7 H0: единственное различие
    двух чтений O-ROST)."""
    t = m._fit_norm_text(s)
    return _PER_LEAD_RE.sub("", t, count=1).strip()


def _canon_number(v):
    """РВ-113: ставка числом — 100 и «100» одно значение (тот же
    читатель числа, что у подписи посадки)."""
    num = m._fit_value_number(v)
    if isinstance(num, float) and num.is_integer():
        num = int(num)
    return num if num is not None else v


def _canon_objective(o: dict) -> dict:
    c = o.get("criterion") if isinstance(o.get("criterion"), dict) else {}
    rate = o.get("rate") if isinstance(o.get("rate"), dict) else {}
    return {"zone": o.get("zone"), "direction": o.get("direction"),
            "metric": o.get("metric"), "criterion": c.get("kind"),
            "rate": _canon_number(c.get("rate_per_duty", rate.get("value"))),
            "per": _norm_per(c.get("per") or rate.get("per") or "")}


def _core(c: dict) -> dict:
    """Ядро чтения без посадки: то, что обе стороны утверждают о норме
    независимо от того, удалось ли её формализовать семьёй."""
    if "direction" in c:
        return c
    return {k: c.get(k) for k in ("kind", "zone", "weight")}


def _is_weaker(c1: dict, c2: dict) -> bool:
    """Чтение 2 слабее чтения 1: то же ядро, но посадки нет (C/D без
    семьи) там, где чтение 1 дало A/B. Это не спор о смысле — второй
    читатель не формализовал; вопрос не ставится, строка идёт без
    подтверждения слоя 2 (проход №7 H0: три строки беспараметрных семей
    без цитаты у второго читателя)."""
    if "direction" in c1 or "direction" in c2:
        return False
    if _core(c1) != _core(c2):
        return False
    strong = c1.get("category") in ("A", "B") and c1.get("family")
    weak = c2.get("category") not in ("A", "B") and not c2.get("family")
    return bool(strong and weak)


def _unformalized(c: dict) -> bool:
    return "direction" not in c and c.get("category") not in ("A", "B") \
        and not c.get("family")


def _same_norm_nested_quote(a: dict, b: dict) -> bool:
    """Одна норма беспараметрной семьи, взятая двумя отрезками одной
    клаузы: всё, кроме цитаты, совпало, а одна цитата вложена в другую
    (проход №16 H0: «ночёвку вне базы, … суббота» против «надбавка 25 …
    ночёвку вне базы, … суббота»)."""
    if "direction" in a or "direction" in b:
        return False
    if any(a.get(k) != b.get(k) for k in ("kind", "zone", "weight", "category", "family")):
        return False
    pa, pb = dict(a.get("params") or {}), dict(b.get("params") or {})
    qa, qb = pa.pop("quote", None), pb.pop("quote", None)
    if pa != pb or not qa or not qb:
        return False
    return qa in qb or qb in qa


def _nested_quotes_only(c1: list, c2: list) -> bool:
    """Списки чтений совпадают с точностью до вложенных отрезков цитат
    беспараметрных семей — согласие, не «слабее»."""
    if len(c1) != len(c2):
        return False
    r2 = [json.loads(x) for x in c2]
    for x in c1:
        a = json.loads(x)
        mate = next((b for b in r2 if b == a or _same_norm_nested_quote(a, b)), None)
        if mate is None:
            return False
        r2.remove(mate)
    return True


def _weaker_pair(c1: list, c2: list) -> bool:
    """Расхождение без спора о задаче решателя: совпавшие записи убраны;
    остаток — либо лишняя неформализованная строка (C без семьи) у одной
    стороны при пустом остатке другой, либо попарно «одно чтение слабее»
    в любую сторону (проходы №7, №10, №11 H0)."""
    r1 = [json.loads(x) for x in c1 if x not in c2]
    r2 = [json.loads(x) for x in c2 if x not in c1]
    # Вложенные отрезки одной цитаты — та же норма: такие пары считаются
    # совпавшими и из остатков убираются.
    for a in list(r1):
        mate = next((b for b in r2 if _same_norm_nested_quote(a, b)), None)
        if mate is not None:
            r1.remove(a)
            r2.remove(mate)
    if not r1 and not r2:
        return True
    if (not r1 and all(_unformalized(c) for c in r2)) \
            or (not r2 and all(_unformalized(c) for c in r1)):
        return True
    if len(r1) != len(r2):
        return False
    return all(_is_weaker(a, b) or _is_weaker(b, a) for a, b in zip(r1, r2))


def _by_anchor(doc: dict) -> dict:
    out: dict = {}
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict) or r.get("kind") == "OUT":
            continue
        out.setdefault(m._primary_ref(r), []).append(("rule", r))
    for o in doc.get("objectives", []) or []:
        if isinstance(o, dict):
            out.setdefault(m._primary_ref(o), []).append(("objective", o))
    return out


def _canon_list(items: list) -> list:
    return sorted(json.dumps(_canon_rule(x) if t == "rule" else _canon_objective(x),
                             ensure_ascii=False, sort_keys=True) for t, x in items)


def describe(canon_json_list: list) -> str:
    """Человекочитаемое чтение носителя из канонических записей."""
    if not canon_json_list:
        return "нормы не увидено"
    parts = []
    for s in canon_json_list:
        c = json.loads(s)
        if "direction" in c:
            parts.append(f"целевая {c.get('zone')} · {c.get('direction')} · "
                         f"{c.get('metric') or '—'} · критерий {c.get('criterion') or '—'} · "
                         f"ставка {c.get('rate') if c.get('rate') is not None else '—'}"
                         f"{' за ' + c['per'] if c.get('per') else ''}")
        else:
            params = ", ".join(f"{k}={v}" for k, v in sorted((c.get("params") or {}).items()))
            parts.append(f"{c.get('kind')} · {c.get('zone')} · "
                         f"{(c.get('family') or 'без семьи (' + str(c.get('category')) + ')')}"
                         f"{'{' + params + '}' if params else ''} · вес "
                         f"{c.get('weight') if c.get('weight') is not None else '—'}")
    return "; ".join(parts)


def adopt_stronger_fits(doc1: dict, doc2: dict, clause_text_by_key: dict | None = None,
                       *, rejections: list | None = None) -> list:
    """Строка первого чтения без посадки A/B (посадка отброшена за форму
    или не выполнена), у которой второе чтение несёт посадку A/B с тем же
    ядром (вид, зона, вес), принимает посадку второго чтения — теми же
    проверками кода по клаузе, что у извлечения. Не применяется к спору о
    чтении (лексический конфликт). Возвращает перечень принятых.
    Отказы по носителям, если запрошены, добавляются в rejections:
    отдельно от неизменённых отвергнутых строк и перечня принятых."""
    a, b = _by_anchor(doc1), _by_anchor(doc2)
    adopted: list = []
    for k, items in a.items():
        for t, r in items:
            if t != "rule":
                continue
            fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
            if fit.get("category") in ("A", "B"):
                continue
            gap = str(fit.get("gap") or "")
            # Только посадка, отброшенная КОДОМ за форму (или не
            # выполненная моделью): суждение первого читателя «C по
            # смыслу» и развилка D чужой посадкой не перекрываются
            # (проверка плана №7).
            if not (gap.startswith("посадка модели отброшена")
                    or gap.startswith("модель не выполнила посадку")):
                continue
            if "спор о чтении" in gap:
                continue
            from cdpm import repair as _rep
            wanted = _rep.rejected_family(fit)
            core = _core(_canon_rule(r))
            for t2, r2 in b.get(k, []):
                if t2 != "rule":
                    continue
                c2 = _canon_rule(r2)
                if c2.get("category") not in ("A", "B") or _core(c2) != core:
                    continue
                if wanted and c2.get("family") != wanted:
                    continue               # семья второго чтения — иная, чем отброшенная
                source_fit = r2["template_fit"]
                fit2 = {kk: vv for kk, vv in source_fit.items()
                        if kk != "registry_rev" or source_fit.get("schema_version") == "landing/2"}
                new_fit, _conf, note = m.accept_fit_candidate(
                    {"id": r.get("id"), "kind": r.get("kind"), "template_fit": fit2},
                    (clause_text_by_key or {}).get(k, ""), new_output=True)
                if isinstance(new_fit, dict) and new_fit.get("category") in ("A", "B"):
                    # Сохранённые параметры первого чтения тоже входят
                    # в принимаемую строку; конфликт не чинится молча.
                    carrier_reasons = (m.reconcile_carriers(r)
                                       or m.reconcile_carriers({**r, "template_fit": new_fit}))
                    if carrier_reasons:
                        if rejections is not None:
                            rejections.append({"anchor": list(k), "row": r.get("id"),
                                               "candidate_row": r2.get("id"),
                                               "family": new_fit.get("family"),
                                               "code": "param-carriers-diverge",
                                               "reasons": carrier_reasons})
                        continue
                    r["template_fit"] = new_fit
                    r["note"] = ((str(r.get("note") or "") + "; посадка принята из "
                                  "второго чтения (проверена кодом по клаузе)").strip("; "))
                    adopted.append({"anchor": list(k), "row": r.get("id"),
                                    "family": new_fit.get("family")})
                    break
    return adopted


def adopt_missing_rows(doc1: dict, doc2: dict) -> list:
    """Носитель, на котором первое чтение не дало НИ ОДНОЙ записи (ни
    строки, ни целевой), а второе независимое чтение — теми же проверками
    кода — дало строку правила, получает строку второго чтения с пометкой
    в note. Живой случай 06.09.2026 (run-0084, демо Н0): первый читатель
    вернул 4 кандидата из 11 по документу S1, второй — все 11, и
    извлечение завершалось «извлечено» с семью пропущенными носителями
    (coverage.missing 7, слой 2 «disagree 7» без строки-адресата). Строка
    второго чтения проходит те же инварианты модели при записи; аудит-цикл
    судит её наравне с остальными. Носители, где первое чтение что-то
    дало (в т. ч. только целевую), не трогаются — это спор о чтении, его
    путь — вопрос части 2. Возвращает перечень принятых."""
    import copy as _copy
    a, b = _by_anchor(doc1), _by_anchor(doc2)
    rules1 = doc1.setdefault("rules", [])
    existing_ids = {r.get("id") for r in rules1 if isinstance(r, dict)}
    adopted: list = []
    for k, items in b.items():
        if k in a:
            continue
        for t2, r2 in items:
            if t2 != "rule" or not isinstance(r2, dict):
                continue
            if r2.get("id") in existing_ids:
                continue
            row = _copy.deepcopy(r2)
            row["note"] = ((str(row.get("note") or "")
                            + "; строка принята из второго чтения: первое "
                              "чтение носитель пропустило").strip("; "))
            rules1.append(row)
            existing_ids.add(row.get("id"))
            adopted.append({"anchor": list(k), "row": row.get("id")})
    return adopted


def compare(doc1: dict, doc2: dict, verdicts1: dict | None = None,
            verdicts2: dict | None = None) -> dict:
    """Сравнение канонического слоя двух сборок по носителям."""
    a, b = _by_anchor(doc1), _by_anchor(doc2)
    verdicts1, verdicts2 = verdicts1 or {}, verdicts2 or {}
    anchors = sorted(set(a) | set(b), key=lambda k: (str(k[0]), str(k[1])))
    agree: list = []
    disagree: list = []
    weaker: list = []
    incomplete: list = []
    for k in anchors:
        c1, c2 = _canon_list(a.get(k, [])), _canon_list(b.get(k, []))
        row = {"anchor": list(k), "rows": [x.get("id") for _t, x in a.get(k, [])],
               "reading_1": c1, "reading_2": c2,
               "verdict_1": (verdicts1.get(k) or {}).get("reason"),
               "verdict_2": (verdicts2.get(k) or {}).get("reason")}
        if c1 == c2 or _nested_quotes_only(c1, c2):
            agree.append(row)
        elif _weaker_pair(c1, c2):
            weaker.append(row)
        elif c1 and not c2 and k not in verdicts2:
            # Второй читатель по носителю не сказал ничего — ни строки, ни
            # вердикта «не правило» (или его вердикт отброшен кодом как
            # незаконный). Это неполнота второго чтения, а не спор о
            # существовании нормы: считается и печатается поимённо; спор —
            # только при явном вердикте против строки (проход №21/№23 H0).
            incomplete.append(row)
        else:
            disagree.append(row)
    return {"anchors": len(anchors), "agree": len(agree),
            "weaker": len(weaker), "disagree": len(disagree),
            "incomplete_2": len(incomplete), "incomplete_readings": incomplete,
            "weaker_readings": weaker, "disagreements": disagree}


def add_questions(asm, report: dict, clause_text_by_key: dict | None = None) -> int:
    """Расхождение чтений → вопрос части 2 с двумя чтениями; строки
    носителя блокируются вопросом. Возвращает число вопросов."""
    n = 0
    rule_ids = {r["id"] for r in asm.doc["rules"]}
    report["unasked"] = []
    for d in report.get("disagreements") or []:
        sid, anchor = d["anchor"]
        clause = (clause_text_by_key or {}).get((sid, anchor), "")
        clause = " ".join(str(clause).split())
        refs = [rid for rid in d.get("rows") or [] if rid in rule_ids]
        if not refs:
            # Носитель — целевая (или строки нет): вопрос анкеты обязан
            # ссылаться на строку свода; расхождение остаётся в отчёте
            # слоя (П1 видит disagree > 0), путь к анкете — вопрос GOAL.
            report["unasked"].append({"anchor": d["anchor"], "rows": d.get("rows") or []})
            continue
        text = (f"Клауза [{sid} {anchor}]"
                f"{' «' + clause[:140] + ('…' if len(clause) > 140 else '') + '»' if clause else ''} "
                f"прочитана двумя независимыми моделями по-разному. "
                f"а) чтение извлечения: {describe(d['reading_1'])}. "
                f"б) второе чтение: {describe(d['reading_2'])}. Какое чтение верно?")
        qid = asm.add_question(
            "2", f"second_reading:{sid}:{anchor}",
            "Расхождение двух независимых чтений", text,
            {"type": "choice", "options": [
                {"key": "а", "text": "верно чтение извлечения"},
                {"key": "б", "text": "верно второе чтение"},
                {"key": "в", "text": "ни одно — поясните, как читать"}]},
            refs, [{"sid": sid, "anchor": anchor}])
        for r in asm.doc["rules"]:
            if r["id"] in refs:
                bl = r.setdefault("blockers", [])
                if qid not in bl:
                    bl.append(qid)
        n += 1
    return n
