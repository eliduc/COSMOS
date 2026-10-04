# -*- coding: utf-8 -*-
"""Штрафная сверка — пункт 3 определения Д4 плана прямого хода
(final-report/CDPM-PLAN-PRYAMOJ-HOD-CLD.md; часть COSMOS, раздел 7 п. 4):

    для мягких строк и целевых штрафная составляющая, пересчитанная из
    ВМП по выходу решателя, равна повкладовому разбору счёта решателя.

Четыре источника, ни один не копируется:
  * форма ВМП — коэффициент мягкой строки (penalty.coefficient);
  * отчёт применённого решателя (lastRun) — веса, которые он применил
    (appliedWeights: ограничение -> вес), его же объявление «правило ->
    ограничение» (weightRules) и целевая (objective: constraint,
    weight, sharesConstraintWithPenalty);
  * повкладовый разбор счёта решателя (score-breakdown: ограничение ->
    hard, soft, matches, weight);
  * сертификат решения (отчёт линии данных): прогнанные проверки
    (checks: basis.rule, status) и находки с rule_refs — число нарушений
    каждого правила, посчитанное НЕЗАВИСИМО от решателя.

По каждой мягкой строке три равенства: вес решателя = коэффициент
формы; число совпадений решателя = число нарушений сертификата;
штраф решателя (совпадения × вес) = штраф по ВМП (нарушения ×
коэффициент). Целевая: её вклад в ограничение = совпадения × вес
целевой. Итог: сумма вкладов по разбору = мягкий счёт решателя.

Вердикт трёхзначный: «сошлось», «разошлось», «несравнимо» (нет
объявления ограничения, проверка сертификата не прогнана, ограничения
нет в разборе) — несравнимое не считается сошедшимся: сертификат
«чист» по Д4 только при «сошлось» по всем строкам.

Перечни семей сверка своей рукой не держит (10.09.2026): «семьи события
ночи» отдаёт запись каталога шаблонов (единица нарушения `night` —
night_event_families), а «строка измерена, а не посчитана» говорит сам
сертификат (поле `measure` проверки либо раздел `measures` —
measured_rules). Рукописный перечень не знает семьи, которой ещё не
было, и молчит о ней.
"""
from __future__ import annotations

from typing import Any

OK = "сошлось"
DIVERGE = "разошлось"
INCOMPARABLE = "несравнимо"

PARTS = ("pairing", "rostering")


def soft_rows(form: dict | None, part: str | None = None) -> dict:
    """Мягкие строки формы: id -> {part, coefficient, unit, family}.
    Строки, исключённые до получения данных, не участвуют."""
    out: dict = {}
    for p in PARTS:
        if part and p != part:
            continue
        for row in (((form or {}).get(p) or {}).get("soft") or []):
            if not isinstance(row, dict):
                continue
            mark = row.get("excluded_until_data")
            if isinstance(mark, dict) and mark.get("question"):
                continue
            pen = row.get("penalty") if isinstance(row.get("penalty"), dict) else {}
            tpl = row.get("template") if isinstance(row.get("template"), dict) else {}
            out[str(row.get("id"))] = {
                "part": p,
                "coefficient": pen.get("coefficient"),
                "unit": pen.get("violation_unit"),
                "family": tpl.get("family"),
            }
    return out


def measured_rules(certificate: dict | None) -> set:
    """Правила, у которых сертификат дал МЕРУ, а не счёт находок.

    Носитель признака — сам сертификат: у проверки семьи-меры есть поле
    `measure` (контракт CDPM 03.09.2026: {kind, value, raw, n, mean}),
    а измеренные величины решения приходят разделом `measures`. Перечня
    «семьи-меры» здесь не держат: до 10.09.2026 он был рукописным
    (balanced_workload), и семья-мера, пришедшая конвейером пополнения,
    в него бы не попала — сверка назвала бы величину «нарушениями».
    """
    out: set = set()
    for c in ((certificate or {}).get("checks") or []):
        if not isinstance(c, dict):
            continue
        rid = (c.get("basis") or {}).get("rule")
        m = c.get("measure")
        if rid and str(c.get("status") or "") == "прогнана" \
                and isinstance(m, dict):
            v = m.get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(str(rid))
    for rid, m in ((certificate or {}).get("measures") or {}).items():
        v = m.get("value") if isinstance(m, dict) else m
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(str(rid))
    return out


def certificate_counts(certificate: dict | None) -> tuple[dict, dict]:
    """(правило -> число находок, правило -> статус проверки) из
    сертификата (отчёт линии данных): статус — «прогнана», если хоть
    одна проверка правила прогнана, иначе первый встреченный статус."""
    counts: dict[str, int] = {}
    status: dict[str, str] = {}
    measured: dict[str, float] = {}
    for c in ((certificate or {}).get("checks") or []):
        if not isinstance(c, dict):
            continue
        rid = (c.get("basis") or {}).get("rule")
        if not rid:
            continue
        st = str(c.get("status") or "")
        # Контракт меры CDPM (2026-09-03): у проверки семьи-меры поле
        # measure = {kind, value (усечено), raw, n, mean}; сверяется
        # value с единицами решателя (unfairness().toLong()).
        m = c.get("measure")
        if st == "прогнана" and isinstance(m, dict):
            v = m.get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                measured[str(rid)] = int(v) if float(v).is_integer() else v
        if status.get(rid) == "прогнана":
            continue
        status[rid] = st if st else status.get(rid, "")
    for f in ((certificate or {}).get("findings") or []):
        if not isinstance(f, dict):
            continue
        for rid in (f.get("rule_refs") or []):
            counts[str(rid)] = counts.get(str(rid), 0) + 1
    for rid, st in status.items():
        if st == "прогнана":
            counts.setdefault(rid, 0)
    # Мера в записи проверки (контракт CDPM) заменяет счёт находок.
    for rid, v in measured.items():
        counts[rid] = v
    # Меры сертификата (семьи-меры): certificate.measures = {правило:
    # {"value": N} | N} — измеренная величина заменяет счёт находок и
    # считается прогнанной проверкой.
    for rid, m in ((certificate or {}).get("measures") or {}).items():
        v = m.get("value") if isinstance(m, dict) else m
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        counts[str(rid)] = int(v) if float(v).is_integer() else v
        status[str(rid)] = "прогнана"
    return counts, status


def _num(v) -> int | float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if float(v).is_integer() else float(v)


def reconcile(form: dict | None, last_run: dict | None,
              breakdown: dict | None, certificate: dict | None,
              part: str | None = None) -> dict:
    """Сверка по всем мягким строкам части и целевой. Возврат: rows,
    objective, total, verdict."""
    last_run = last_run or {}
    applied = last_run.get("appliedWeights") or {}
    weight_rules = last_run.get("weightRules") or {}
    objective = last_run.get("objective") if isinstance(last_run.get("objective"), dict) else None
    cons = (breakdown or {}).get("constraints") or {}
    counts, status = certificate_counts(certificate)
    measured = measured_rules(certificate)

    rows: list[dict] = []
    for rid, spec in soft_rows(form, part).items():
        row: dict[str, Any] = {"rule": rid, "part": spec["part"],
                               "coefficient": spec["coefficient"],
                               "unit": spec["unit"]}
        constraint = weight_rules.get(rid)
        row["constraint"] = constraint
        if not constraint:
            row.update(verdict=INCOMPARABLE,
                       note="решатель не объявил ограничение для правила "
                            "(weightRules) — вес некуда сверять")
            rows.append(row)
            continue
        coef = _num(spec["coefficient"])
        w = _num(applied.get(constraint))
        row["solver_weight"] = w
        rec = cons.get(constraint) if isinstance(cons.get(constraint), dict) else None
        st = status.get(rid)
        cert_n = counts.get(rid) if st == "прогнана" else None
        row["certificate_violations"] = cert_n
        row["certificate_status"] = st
        if rec is None:
            row.update(verdict=INCOMPARABLE,
                       note="ограничения «%s» нет в повкладовом разборе "
                            "счёта" % constraint)
            rows.append(row)
            continue
        matches = _num(rec.get("matches"))
        soft = _num(rec.get("soft"))
        row["solver_matches"] = matches
        row["solver_soft"] = soft
        problems: list[str] = []
        if coef is None:
            problems.append("коэффициент формы не число")
        if w is None:
            problems.append("вес у решателя не объявлен (appliedWeights)")
        elif coef is not None and w != coef:
            problems.append("вес решателя %s ≠ коэффициент формы %s"
                            % (w, coef))
        if cert_n is None:
            row.update(verdict=INCOMPARABLE,
                       note="; ".join(problems + [
                           "сертификат не прогнал проверку правила "
                           "(статус «%s»)" % (st or "нет проверки")]))
            rows.append(row)
            continue
        if matches is None or soft is None:
            row.update(verdict=INCOMPARABLE,
                       note="; ".join(problems + [
                           "разбор счёта без matches/soft у «%s»" % constraint]))
            rows.append(row)
            continue
        # Единицы решателя: у ограничений с множителем (ночи, смены —
        # ревизия семантики 2026-09-03) одно совпадение несёт несколько
        # единиц, а мера (равномерность: √Σ(x−x̄)², усечение) — одно
        # совпадение на всё расписание. Поэтому единицы всегда =
        # −soft / ДЕЙСТВУЮЩИЙ вес, где действующий вес = вес правила +
        # доля целевой, если целевая делит с правилом ограничение
        # (сертификация прохода 26, 03.09: мера 174 при soft −696 и весе
        # 3 + 1 — совпадениями было бы 1). Совпадения — только запасной
        # путь при нулевом действующем весе.
        shares = bool(objective and objective.get("constraint") == constraint
                      and objective.get("sharesConstraintWithPenalty"))
        obj_w = (_num(objective.get("weight")) or 0) if shares else 0
        eff = _num((last_run.get("effectiveWeights") or {}).get(constraint))
        if eff is None:
            eff = (w or 0) + obj_w
        elif w is not None and eff != w + obj_w:
            problems.append("действующий вес %s ≠ вес правила %s + доля "
                            "целевой %s" % (eff, w, obj_w))
        if eff:
            units = -soft / eff
            units = int(units) if float(units).is_integer() else units
        else:
            units = matches
        row["solver_units"] = units
        if units != cert_n:
            problems.append("единиц у решателя %s ≠ %s по сертификату %s"
                            % (units, "мере" if rid in measured else "нарушений",
                               cert_n))
        if coef is not None and w is not None:
            expected = cert_n * coef
            solver_penalty = units * w
            row["penalty_vmp"] = expected
            row["penalty_solver"] = solver_penalty
            if expected != solver_penalty:
                problems.append("штраф по ВМП %s ≠ штраф решателя %s"
                                % (expected, solver_penalty))
            # Вклад целевой в то же ограничение — отдельное слагаемое
            # по тем же единицам.
            share = units * obj_w
            row["objective_share"] = share
            if soft != -(solver_penalty + share):
                problems.append("soft разбора %s ≠ −(штраф %s + вклад "
                                "целевой %s)" % (soft, solver_penalty, share))
        row["verdict"] = DIVERGE if problems else OK
        if problems:
            row["note"] = "; ".join(problems)
        rows.append(row)

    # Ограничения вне ВМП: ненулевой счёт у ограничения, в которое не
    # лёг вес ни одного правила пакета и которое не исполняет целевую, —
    # решатель судил по литералу, о котором постановка молчит
    # (принцип владельца 2026-08-28: пространство решений ограничивает
    # только ВМП). Жёсткие литералы — тоже сюда: они не в области
    # штрафа, но они ограничение вне постановки.
    mapped = set(weight_rules.values())
    if objective and objective.get("constraint"):
        mapped.add(objective["constraint"])
    for name, rec in sorted(cons.items()):
        if name in mapped or not isinstance(rec, dict):
            continue
        hard, soft = _num(rec.get("hard")), _num(rec.get("soft"))
        if not hard and not soft:
            continue
        rows.append({"rule": None, "part": part, "constraint": name,
                     "solver_matches": _num(rec.get("matches")),
                     "solver_soft": soft, "solver_hard": hard,
                     "verdict": DIVERGE,
                     "note": "ограничение вне ВМП: счёт %s hard / %s soft по "
                             "литералу решателя, правила пакета за ним нет"
                             % (hard or 0, soft or 0)})

    obj_row = None
    if objective:
        constraint = objective.get("constraint")
        rec = cons.get(constraint) if isinstance(cons.get(constraint), dict) else None
        obj_row = {"id": objective.get("id"), "constraint": constraint,
                   "metric": objective.get("metric"),
                   "weight": _num(objective.get("weight"))}
        if rec is None:
            obj_row.update(verdict=INCOMPARABLE,
                           note="ограничения целевой «%s» нет в разборе"
                                % constraint)
        else:
            matches = _num(rec.get("matches"))
            soft = _num(rec.get("soft"))
            obj_row["solver_matches"] = matches
            obj_row["solver_soft"] = soft
            eff = _num((last_run.get("effectiveWeights") or {}).get(constraint))
            # Единицы целевой — те же, что у правила-соседа: −soft /
            # действующий вес; они обязаны быть целыми (совпадения с
            # множителем и мера — целые величины решателя).
            rule_w = 0
            if objective.get("sharesConstraintWithPenalty"):
                rule_w = _num(applied.get(constraint)) or 0
            obj_w = _num(objective.get("weight")) or 0
            if matches is None or soft is None or eff is None:
                obj_row.update(verdict=INCOMPARABLE,
                               note="разбор без matches/soft либо "
                                    "effectiveWeights без ограничения целевой")
            elif eff != rule_w + obj_w:
                obj_row.update(verdict=DIVERGE,
                               note="действующий вес %s ≠ вес правила %s + "
                                    "ставка целевой %s" % (eff, rule_w, obj_w))
            elif eff and not float(-soft / eff).is_integer():
                obj_row.update(verdict=DIVERGE,
                               note="soft %s не делится на действующий вес %s "
                                    "— единицы не целые" % (soft, eff))
            else:
                units = int(-soft / eff) if eff else matches
                obj_row["solver_units"] = units
                obj_row["objective_contribution"] = units * obj_w
                obj_row["verdict"] = OK

    total = None
    if breakdown:
        soft_total = _num(breakdown.get("soft"))
        parts_sum = sum((_num(r.get("soft")) or 0) for r in cons.values()
                        if isinstance(r, dict))
        total = {"soft": soft_total, "sum_of_constraints": parts_sum}
        if soft_total is None:
            total["verdict"] = INCOMPARABLE
        else:
            total["verdict"] = OK if soft_total == parts_sum else DIVERGE

    verdicts = [r["verdict"] for r in rows]
    if obj_row:
        verdicts.append(obj_row["verdict"])
    if total:
        verdicts.append(total["verdict"])
    if not rows and obj_row is None:
        verdict = OK if not verdicts or all(v == OK for v in verdicts) else (
            DIVERGE if DIVERGE in verdicts else INCOMPARABLE)
    elif DIVERGE in verdicts:
        verdict = DIVERGE
    elif INCOMPARABLE in verdicts:
        verdict = INCOMPARABLE
    else:
        verdict = OK
    # Контракт Б, политика P-417 (единая версия 06.09.2026, §3.10.3;
    # группы H11 и P): поверх числовой сверки строк — полнота стоимости
    # и сверка области учёта ночёвок с решателем (cost_scope_policy);
    # сошедшаяся известная часть при неизвестном остатке остаётся
    # «сошлось», полная стоимость при этом не подтверждена.
    out: dict[str, Any] = {
        "verdict": verdict, "rows": rows, "objective": obj_row, "total": total,
        "counts": {v: verdicts.count(v) for v in (OK, DIVERGE, INCOMPARABLE)}}
    night = night_event_families()
    night_constraints = {weight_rules.get(rid) for rid, spec in soft_rows(form, part).items()
                         if spec.get("family") in night and weight_rules.get(rid)}
    out.update(cost_scope_policy(certificate, form, part, rows, obj_row, total,
                                 verdict, night_constraints))
    return out


#: Код единицы нарушения каталога, которым названо событие ночи вне
#: базы. Признак семьи — у записи каталога, а не в перечне здесь.
NIGHT_UNIT = "night"

#: Кэш перечня семей событий ночи: (экземпляр каталога, перечень).
#: Ключ — сам экземпляр двери: читатель отдаёт новый объект, когда файлы
#: каталога сменились.
_NIGHT_CACHE: tuple | None = None


def night_event_families() -> frozenset:
    """Семьи мягких строк, чьи единицы — те же события (ночь вне базы),
    что у независимой оценки области учёта (`vmp/run/cost_scope`):
    совпадение событий с решателем подтверждает ТОЛЬКО этот член цены.

    НОСИТЕЛЬ — запись каталога шаблонов: единица нарушения семьи
    (`violation_unit`) равна `night`. Рукописного перечня здесь нет
    (10.09.2026): семья, пришедшая конвейером пополнения с той же
    единицей, попадала бы в цену, но не в сверку области учёта — и
    «полная стоимость подтверждена» говорилось бы о неполном наборе
    членов цены. Каталог недоступен — отказ читателя наружу: запаса из
    кода у COSMOS нет.
    """
    global _NIGHT_CACHE
    from vmp import catalog_access as ca
    cat = ca.current()
    hit = _NIGHT_CACHE
    if hit is not None and hit[0] is cat:
        return hit[1]
    out = frozenset(
        fid for fid, meta in cat.families_meta().items()
        if meta.get("violation_unit") == NIGHT_UNIT)
    _NIGHT_CACHE = (cat, out)
    return out


def _declared_scope(assessment: dict) -> bool:
    """Оценка области учёта с ОБЪЯВЛЕННОЙ областью (H11-R2): источник
    declared_scope либо названные границы области (scope_start/scope_end)
    при источнике не legacy. Прежний путь по «Сменам» (source
    legacy_duties_sheet, границ нет) и голое заявление {task, complete}
    без границ — не объявленная область."""
    source = assessment.get("source")
    if source == "declared_scope":
        return True
    if source == "legacy_duties_sheet":
        return False
    return (assessment.get("scope_start") is not None
            or assessment.get("scope_end") is not None)


def cost_scope_policy(certificate: dict | None, form: dict | None, part: str | None,
                      rows: list, obj_row: dict | None, total: dict | None,
                      verdict: str, night_constraints: set | None = None) -> dict:
    """Политика P-417 (единая версия 06.09.2026, план §3.10.3; H11-7):
    поверх числовой сверки строк читается оценка области учёта ночёвок
    сертификата (certificate.cost_assessment, задача rostering):

      * реальное расхождение области или событий с решателем
        (solver_comparison DIVERGE) → «разошлось» — одинаковая сумма при
        разных ночах есть расхождение;
      * отсутствующее / повреждённое / несопоставимое обязательное
        свидетельство решателя (solver_comparison не OK) при сошедшихся
        числах → «несравнимо»;
      * известная часть сошлась, но остаток неизвестен (complete False)
        → вердикт НЕ понижается: стоимость «подтверждена на области
        учёта» (partial), full_cost_confirmed False, certified не
        отзывается автоматически;
      * сверка событий не проводилась (область не объявлена — прежний
        путь по «Сменам», solver_comparison None) → вердикт не трогается,
        полнота не подтверждена.

    full_cost_confirmed = True — только при полном независимом покрытии
    ВСЕХ членов объявленной цены с общей областью и успешной сверкой;
    полное совпадение одних ночёвок — night_cost_confirmed (узкая
    price_scope «nights»), члены цены вне области событий (продление
    смены, равномерность, целевая по другому ограничению) перечисляются в
    price_members_uncovered. arithmetic_subtotal_verdict — вердикт чисто
    числовой сверки, известная часть — known_subtotal_verdict.

    Без оценки области учёта задачи rostering (сертификат до единой
    версии; сертификат без мер ночёвок) полнота цены price_complete
    читается из шапки сертификата cost_certainty (full / partial —
    группа P, P-415): это показание сертификата, не независимое
    подтверждение — full_cost_confirmed остаётся False (GPT-014)."""
    cert = certificate if isinstance(certificate, dict) else {}
    inner = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
    assessment = cert.get("cost_assessment")
    if assessment is None:
        assessment = inner.get("cost_assessment")
    out: dict[str, Any] = {
        "arithmetic_subtotal_verdict": verdict,
        "known_subtotal_verdict": verdict,
        "cost_scope_comparison": None,
        "price_complete": None,
        "night_cost_confirmed": False,
        "full_cost_confirmed": False,
        "price_scope": None,
        "price_members_uncovered": [],
        "cost_assessment": assessment if isinstance(assessment, dict) else None,
        "verdict": verdict,
    }
    if not isinstance(assessment, dict) or assessment.get("task") != "rostering":
        certainty = cert.get("cost_certainty")
        if certainty is None:
            certainty = inner.get("cost_certainty")
        if certainty in ("full", "partial"):
            out["price_complete"] = certainty == "full"
        if isinstance(assessment, dict):
            out["cost_scope_note"] = ("оценка области учёта задачи «%s» в политику "
                                      "штрафной сверки не входит" % assessment.get("task"))
        return out
    comparison = assessment.get("solver_comparison")
    complete = assessment.get("complete") is True
    out["cost_scope_comparison"] = comparison if isinstance(comparison, dict) else None
    out["price_complete"] = complete
    if isinstance(comparison, dict) and comparison.get("verdict") in ("OK", "DIVERGE",
                                                                      "INCOMPARABLE"):
        scope_verdict = comparison.get("verdict")
        if scope_verdict == "DIVERGE":
            verdict = DIVERGE
            out["cost_scope_note"] = ("область учёта либо события ночёвок расходятся с "
                                      "записью решателя: %s"
                                      % ", ".join(map(str, comparison.get("reasons") or [])))
        elif scope_verdict != "OK" and verdict == OK:
            verdict = INCOMPARABLE
            out["cost_scope_note"] = ("запись решателя о ночёвках отсутствует либо "
                                      "несопоставима: %s"
                                      % ", ".join(map(str, comparison.get("reasons") or [])))
    elif _declared_scope(assessment):
        # H11-R2 (GPT-022): у оценки с ОБЪЯВЛЕННОЙ областью сверка с
        # записью решателя — обязательное свидетельство; отсутствующее
        # (None/нет поля), повреждённое (не объект) или без вердикта
        # из словаря — «несравнимо», не «сошлось с пометкой».
        # Показание legacy-пути («область не объявлена») сюда не
        # относится и этот случай не маскирует.
        out["cost_scope_comparison"] = None
        if verdict == OK:
            verdict = INCOMPARABLE
        if comparison is None:
            state = "отсутствует"
        elif not isinstance(comparison, dict):
            state = "повреждена (не объект)"
        else:
            state = "повреждена (вердикт %r вне словаря)" % (comparison.get("verdict"),)
        out["cost_scope_note"] = (
            "обязательное свидетельство отсутствует: сверка событий ночёвок "
            "с записью решателя (solver_comparison) у оценки с объявленной "
            "областью учёта " + state)
    else:
        out["cost_scope_note"] = ("сверка событий с записью решателя не проводилась: "
                                  + str(assessment.get("note") or "область учёта не объявлена"))
    if not complete and "cost_scope_note" not in out:
        out["cost_scope_note"] = ("известная часть сошлась, остаток области учёта "
                                  "неизвестен (%s событий) — полная стоимость не "
                                  "подтверждена" % assessment.get("unknown_count"))
    out["known_subtotal_verdict"] = verdict
    out["verdict"] = verdict
    night_ok = bool(complete and verdict == OK and isinstance(comparison, dict)
                    and comparison.get("verdict") == "OK")
    out["night_cost_confirmed"] = night_ok
    # Члены цены вне области событий: мягкие строки других семей и
    # целевая по ограничению, которое не исполняет ночёвки.
    specs = soft_rows(form, part)
    night = night_event_families()
    uncovered = sorted(rid for rid, spec in specs.items()
                       if spec.get("family") not in night)
    if obj_row is not None and obj_row.get("constraint") not in (night_constraints or set()):
        uncovered.append("objective:%s" % (obj_row.get("id") or obj_row.get("constraint")))
    out["price_members_uncovered"] = uncovered
    out["price_scope"] = "nights" if night_ok else None
    out["full_cost_confirmed"] = bool(
        night_ok and not uncovered
        and all(r.get("verdict") == OK for r in rows)
        and (obj_row is None or obj_row.get("verdict") == OK)
        and (total is None or total.get("verdict") == OK))
    return out


def reconcile_pairing(catalog: dict | None, report: dict | None = None,
                      recomputed: list | None = None) -> dict:
    """Сверка целевой пейринга по каталогу построителя: целевая
    (ip_objective) = Σ cost выбранных цепочек = total_cost отчёта;
    цепочка без числовой цены — несравнимо. Разбора по ограничениям у
    построителя нет: его целевая складывается из цен цепочек, и
    именно это равенство подлежит проверке.

    A6 (05.09.2026): сверка ПО МЕРАМ, не только по суммам. У цепочки с
    расшифровкой цены (price: критерий, ночёвки, строки мягких правил)
    проверяется арифметика расшифровки и — при переданном независимом
    пересчёте recomputed[i] (запись vmp.chain_price.comparison_record
    от price(0, …) по исходным периодам и подписи) — совпадение области
    учёта, событий, определённости, весов и мер каталога с пересчётом
    сертификатора. Потерянный или подменённый штраф расходится словами,
    а не прячется в равной сумме.

    Контракт Б, политика P-417 (единая версия 06.09.2026, §3.10.3).
    Триада вердикта: «разошлось» — реальное расхождение сумм, арифметики,
    области учёта или событий; «несравнимо» — отсутствующее либо
    несопоставимое обязательное свидетельство (подпись цены чужой
    модели или без области учёта, цепочка без расшифровки, нет
    независимого пересчёта либо он не адресует каждую цепочку, пересчёт
    только числами без области, отчёт без модели цены, нет свода
    определённости); «сошлось» — известная часть сверена по области и
    событиям. Отдельно: known_subtotal_verdict (вердикт известной
    части), price_complete (полнота цены по своду), full_cost_confirmed
    — только при «сошлось» И полной области учёта всех цепочек;
    совпавшая известная часть при неизвестном остатке — «сошлось» с
    пометкой «полная стоимость не установлена», не расхождение и не
    отзыв сертификата. Сводка определённости каталога и отчёта
    (price_certainty) обязана совпасть с агрегатом по цепочкам
    (aggregate_certainty) — подделка сводки расходится."""
    from vmp import chain_price as _cp
    catalog = catalog or {}
    chains = [c for c in (catalog.get("selected") or []) if isinstance(c, dict)]
    costs = [_num(c.get("cost")) for c in chains]
    objective = _num(catalog.get("ip_objective"))
    total = _num((report or {}).get("total_cost")) if report else None
    out: dict[str, Any] = {"chains": len(chains), "objective": objective,
                           "sum_of_chains": None, "report_total_cost": total,
                           "price_model": _cp.VERSION,
                           "signature_verified": False,
                           "price_complete": None,
                           "known_subtotal_verdict": INCOMPARABLE,
                           "full_cost_confirmed": False}
    problems: list[str] = []
    missing: list[str] = []
    sig = catalog.get("config_sig") if isinstance(catalog.get("config_sig"), dict) else None
    context = None
    try:
        context = _cp.pricing_context_from_signature(sig)
    except _cp.ChainPriceError as exc:
        missing.append("подпись цены: %s" % exc)
    out["signature_verified"] = context is not None
    if not chains:
        out.update(verdict=INCOMPARABLE, note="в каталоге нет выбранных цепочек")
        return out
    if any(c is None or not _cp._finite(c) for c in costs):
        out.update(verdict=INCOMPARABLE,
                   note="у %d цепочек цена не число"
                        % sum(1 for c in costs if c is None or not _cp._finite(c)))
        return out
    s = sum(costs)
    out["sum_of_chains"] = int(s) if float(s).is_integer() else s
    if objective is None or not _cp._finite(objective):
        out.update(verdict=INCOMPARABLE,
                   note="каталог без ip_objective — известная часть не подтверждена")
        return out
    if abs(objective - s) > 1e-6:
        problems.append("целевая %s ≠ сумма цен цепочек %s" % (objective, out["sum_of_chains"]))
    if report is not None:
        if total is None:
            problems.append("отчёт построителя без total_cost")
        elif abs(total - s) > 1e-6:
            problems.append("total_cost отчёта %s ≠ сумма цен цепочек %s"
                            % (total, out["sum_of_chains"]))
        if context is not None:
            rep_model = report.get("price_model")
            if rep_model is None:
                missing.append("отчёт построителя без модели цены (price_model)")
            elif rep_model != _cp.VERSION:
                problems.append("отчёт построителя с чужой моделью цены %r ≠ %s"
                                % (rep_model, _cp.VERSION))
    # Независимый пересчёт обязан адресовать КАЖДУЮ выбранную цепочку.
    if recomputed is None:
        missing.append("независимого пересчёта нет — сверка только по суммам "
                       "и самопроверке расшифровок")
    elif len(recomputed) != len(chains):
        missing.append("независимый пересчёт адресует %d цепочек из %d"
                       % (len(recomputed), len(chains)))
    # A6: сверка по мерам — самопроверка расшифровки и независимый пересчёт.
    priced = 0
    rows_out: list[dict] = []
    sig_scope = (sig or {}).get("price_scope") if context is not None else None
    for k, c in enumerate(chains):
        pr = c.get("price")
        fns = list(c.get("fns") or ())
        if not isinstance(pr, dict):
            missing.append("цепочка %d: нет расшифровки цены/области учёта" % k)
            rows_out.append({"chain": k, "fns": fns, "verdict": INCOMPARABLE,
                             "issues": [], "missing": ["нет расшифровки цены/области учёта"]})
            continue
        priced += 1
        rec = recomputed[k] if recomputed is not None and k < len(recomputed) else None
        if not isinstance(rec, dict) or not rec:
            rec = None
        row_missing: list[str] = []
        if pr.get("price_model") != _cp.VERSION:
            # Расшифровка другой модели: арифметику и числа сверить можно,
            # область учёта и события — нет; это несравнимо, не расхождение.
            issues = _cp.arithmetic_problems(pr) + _cp.numeric_comparison(pr, rec)
            row_missing.append("расшифровка цены другой модели (%r): сверка только чисел"
                               % pr.get("price_model"))
        else:
            issues = _cp.compare_rows(pr, rec)
            if sig_scope is not None:
                chain_scope = dict(pr.get("accounting_scope") or {})
                chain_scope.pop("periods", None)
                chain_scope.pop("criterion_kind", None)
                if chain_scope != sig_scope:
                    issues.append("область учёта цепочки отличается от подписи каталога "
                                  "(price_scope)")
        if isinstance(pr.get("cost"), (int, float)) and costs[k] is not None \
                and abs(pr["cost"] - costs[k]) > 1e-6:
            issues.append("cost цепочки %s ≠ цена расшифровки %s"
                          % (costs[k], pr["cost"]))
        if issues and c.get("sourceLegsMissing"):
            issues.append("цепочка урезана арбитражем покрытия "
                          "(sourceLegsMissing=%s)" % c.get("sourceLegsMissing"))
        if recomputed is not None:
            if rec is None:
                row_missing.append("нет независимого пересчёта цепочки")
            elif "accounting_scope" not in rec and "soft" not in rec:
                row_missing.append("пересчёт только числами — без области учёта и событий")
        rows_out.append({"chain": k, "fns": fns,
                         "verdict": DIVERGE if issues else (
                             INCOMPARABLE if row_missing or recomputed is None else OK),
                         "issues": issues, "missing": row_missing})
        for msg in issues:
            problems.append("цепочка %d: %s" % (k, msg))
        for msg in row_missing:
            missing.append("цепочка %d: %s" % (k, msg))
    out["priced_chains"] = priced
    out["recomputed"] = bool(recomputed)
    out["measure_rows"] = rows_out
    out["measure_divergences"] = sum(1 for r in rows_out if r["verdict"] == DIVERGE)
    # Свод определённости: агрегат по цепочкам против сводок каталога и
    # отчёта — подделка сводки (исчезнувшие неизвестные) расходится.
    unknown_count = None
    if context is not None:
        certainty = _cp.aggregate_certainty(c.get("price") for c in chains)
        out["price_certainty"] = certainty
        out["price_complete"] = bool(certainty["complete"])
        unknown_count = certainty["unknown_count"]
        for label, carrier in (("каталог", catalog), ("отчёт", report)):
            if carrier is None:
                continue
            got = carrier.get("price_certainty")
            if got is None:
                missing.append(label + ": нет свода определённости цены (price_certainty)")
            elif got != certainty:
                problems.append(label + ": свод определённости/областей цены "
                                        "не совпадает с агрегатом по цепочкам")
    out["known_subtotal_verdict"] = DIVERGE if problems else (INCOMPARABLE if missing else OK)
    out["verdict"] = out["known_subtotal_verdict"]
    out["full_cost_confirmed"] = out["verdict"] == OK and out["price_complete"] is True
    notes = problems + missing
    if out["verdict"] == OK and not out["full_cost_confirmed"]:
        notes.append("известная часть стоимости пейринга сверена; полная стоимость "
                     "не установлена: неизвестных событий/интервалов %s"
                     % (unknown_count if unknown_count is not None else "?"))
    if notes:
        out["note"] = "; ".join(notes)
    return out


def is_clean(result: dict) -> bool:
    """«Чист» по пункту 3 Д4: только «сошлось» по всем строкам."""
    return result.get("verdict") == OK


def report(result: dict) -> str:
    lines = ["ШТРАФНАЯ СВЕРКА: %s (сошлось %d, разошлось %d, несравнимо %d)"
             % (result["verdict"].upper(), result["counts"][OK],
                result["counts"][DIVERGE], result["counts"][INCOMPARABLE])]
    for r in result["rows"]:
        lines.append("  %s %s/%s → %s: нарушений %s × %s | совпадений %s × %s%s"
                     % (r["verdict"], r.get("part"), r["rule"],
                        r.get("constraint") or "?",
                        r.get("certificate_violations"), r.get("coefficient"),
                        r.get("solver_matches"), r.get("solver_weight"),
                        (" — " + r["note"]) if r.get("note") else ""))
    o = result.get("objective")
    if o:
        lines.append("  целевая %s → %s: %s%s"
                     % (o.get("id"), o.get("constraint"), o["verdict"],
                        (" — " + o["note"]) if o.get("note") else ""))
    t = result.get("total")
    if t:
        lines.append("  итог soft %s против суммы вкладов %s: %s"
                     % (t.get("soft"), t.get("sum_of_constraints"), t["verdict"]))
    return "\n".join(lines)
