"""Отчёт сравнения ростеров — «наш ростер против исторического»
(2026-07-17, дорожная карта №5 документа COSMOS-DATA-FORMALIZATION-RU,
этап Ф8: историческая репликация и акт приёмки).

Две стороны, одинаковые метрики над ОДНИМ payload:
  * «наш» — назначения второй стадии конвейера (артефакт
    gerad_stage2_assignment_*) с наложенными ручными правками доски;
  * «исторический» — flightAssignments[].employee канонического payload
    (то, что фактически летало у заказчика; заполняется адаптером
    импорта; у сид-данных пусто — тогда стороне честно ставится
    available=false).

Метрики стороны: покрытие рейсов, число пейрингов/экипажей, блок-часы
(кресло-часы), кредит-часы и оплачиваемые часы (max(кредит, гарантия) на
сотрудника — pay_credit), стоимость (costing), нарушения жёстких правил
(roster_board._validate: покрытие, тип ВС, недоступность, база, перехлёст,
отдых).

Честные ограничения метода (включаются в отчёт notes):
  * исторические последовательности сотрудника сворачиваются в пейринги по
    паузе >= min_rest_min, поэтому нарушение «rest» для исторической
    стороны невыявимо по построению (короткая пауза остаётся внутри
    пейринга и учитывается кредитом через разбиение на duty);
  * проверка home_base для исторической стороны отключена (база пейринга
    приравнена базе экипажа): историческая цепочка не якорена на базу так,
    как пейринги генератора колонок;
  * доска и отчёт считают ту тройку правил, что и inline-легальность —
    полный движок правил (Ярус 1 целиком) остаётся за солвером.

Markdown-сравнение (markdown_comparison) — диагностический отчёт теневого
прогона: планирование / эксплуатация / кадры.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import costing, pay_credit, registry, roster_board


def _by_fn(payload: dict) -> dict:
    """flightNumber -> flight dict. NOTE: the canon references flights by
    NUMBER (flightAssignments[].flight, pairing fns), so a schedule with
    daily flights (one number on several dates) collapses to the last
    occurrence here — the historical side cannot attribute a slot to a
    specific date. `_duplicate_numbers` detects this and the report flags
    it honestly (metrics become approximate for such datasets)."""
    return {f.get("flightNumber"): f for f in payload.get("flights") or []
            if isinstance(f, dict)}


def _duplicate_numbers(payload: dict) -> int:
    seen: dict[str, int] = {}
    for f in payload.get("flights") or []:
        if isinstance(f, dict):
            seen[f.get("flightNumber")] = seen.get(f.get("flightNumber"),
                                                   0) + 1
    return sum(1 for n in seen.values() if n > 1)


def _dur_h(flight: dict) -> float:
    try:
        h = (_dt(flight["arrivalUTCDateTime"])
             - _dt(flight["departureUTCDateTime"])).total_seconds() / 3600.0
    except (KeyError, TypeError, ValueError):
        return 0.0
    return h if h > 0 else 0.0


def _pairing_leg(leg: dict) -> dict:
    """Resolved pairing leg (fn/dep/arr/dep_utc/arr_utc/ac) -> a canonical
    flight dict pay_credit/costing consume — using the artifact's OWN leg
    times, so daily-flight numbers are not re-collapsed through _by_fn."""
    return {"flightNumber": leg.get("fn"),
            "departureAirport": leg.get("dep"),
            "arrivalAirport": leg.get("arr"),
            "departureUTCDateTime": leg.get("dep_utc"),
            "arrivalUTCDateTime": leg.get("arr_utc"),
            "aircraftType": leg.get("ac") or None}


def _crew_from_payload(payload: dict) -> dict:
    out = {}
    for e in payload.get("employees") or []:
        eid = str(e.get("id") or "")
        if not eid:
            continue
        out[eid] = {"id": eid, "name": e.get("name") or eid,
                    "base": e.get("homeAirport"),
                    "seniority": e.get("seniority"),
                    "role": None,
                    "type_ratings": list(e.get("typeRatings") or []),
                    "unavailable": set(e.get("unavailableDays") or [])}
    return out


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _leg_days(leg: dict) -> list[str]:
    try:
        lo = _dt(leg["departureUTCDateTime"]).date()
        hi = _dt(leg["arrivalUTCDateTime"]).date()
    except (KeyError, ValueError):
        return []
    return [(lo + timedelta(days=i)).isoformat()
            for i in range((hi - lo).days + 1)]


def _sorted_legs(legs: list[dict]) -> list[dict]:
    return sorted(legs, key=lambda f: f.get("departureUTCDateTime") or "")


def _hist_records(payload: dict, rest_min: float) -> list[dict]:
    """flightAssignments -> per-employee pairings, split where the gap is a
    legal rest (>= rest_min). Each record: {pid, employee, legs, active}."""
    by_fn = _by_fn(payload)
    per_emp: dict[str, list[dict]] = {}
    for a in payload.get("flightAssignments") or []:
        emp = a.get("employee")
        leg = by_fn.get(a.get("flight"))
        if emp and leg is not None:
            per_emp.setdefault(str(emp), []).append(leg)
    recs = []
    for emp, legs in sorted(per_emp.items()):
        legs = _sorted_legs(legs)
        chunk: list[dict] = []
        k = 0
        for leg in legs:
            if chunk:
                try:
                    gap = (_dt(leg["departureUTCDateTime"])
                           - _dt(chunk[-1]["arrivalUTCDateTime"])
                           ).total_seconds() / 60
                except (KeyError, ValueError):
                    gap = 0
                if gap >= rest_min:
                    recs.append({"pid": f"hist-{emp}-{k}", "employee": emp,
                                 "legs": chunk,
                                 "active": {c.get("flightNumber")
                                            for c in chunk}})
                    k += 1
                    chunk = []
            chunk.append(leg)
        if chunk:
            recs.append({"pid": f"hist-{emp}-{k}", "employee": emp,
                         "legs": chunk,
                         "active": {c.get("flightNumber") for c in chunk}})
    return recs


def _ours_records(inst: dict, payload: dict) -> list[dict] | None:
    """Stage-2 pairings with the board's manual moves overlaid — the FULL
    set (no board truncation). None when there is no stage-2 roster."""
    resolved = roster_board._resolve(inst)
    pairings = resolved[0]
    if pairings is None:
        return None
    moves = ((roster_board._load().get(inst["id"]) or {}).get("moves")
             or {})
    # active_fns lives in the RAW artifact (a dict {..., "pairings": [...]}),
    # not the resolved pairing. Read it keyed by pid; a pairing that carries
    # an explicit (possibly EMPTY) active_fns is honoured verbatim — an empty
    # list means a fully-deadhead pairing, NOT "everything flies".
    doc = roster_board._assignment(inst) or {}
    active_by_pid: dict = {}
    for r in doc.get("pairings") or []:
        if not isinstance(r, dict):
            continue
        if "active_fns" in r:
            active_by_pid[r.get("pid")] = set(r.get("active_fns") or [])
        elif "fns" in r:
            active_by_pid[r.get("pid")] = set(r.get("fns") or [])
    recs = []
    for p in pairings:
        emp = moves.get(p["pid"], p.get("solver_employee"))
        legs = [_pairing_leg(leg) for leg in p.get("legs") or []]
        active = (active_by_pid[p["pid"]] if p["pid"] in active_by_pid
                  else {leg.get("flightNumber") for leg in legs})
        recs.append({"pid": p["pid"], "employee": emp,
                     "legs": _sorted_legs(legs), "active": active,
                     "_resolved": p})
    return recs


def _validate_shape(rec: dict, crew_by_id: dict, hist: bool) -> dict:
    """One record -> the pairing dict roster_board._validate consumes."""
    src = rec.get("_resolved")
    if src is not None:
        return {**src, "employee": rec["employee"]}
    legs = rec["legs"]
    days: set[str] = set()
    aircraft: set[str] = set()
    for leg in legs:
        days.update(_leg_days(leg))
        if leg.get("aircraftType"):
            aircraft.add(leg["aircraftType"])
    crew = crew_by_id.get(rec["employee"]) or {}
    return {
        "pid": rec["pid"], "employee": rec["employee"],
        # historical chains are not base-anchored — silence home_base
        "base": crew.get("base") if hist else None,
        "aircraft": sorted(aircraft), "days": sorted(days),
        "dep_utc": legs[0].get("departureUTCDateTime") if legs else "",
        "arr_utc": legs[-1].get("arrivalUTCDateTime") if legs else "",
        "legs": legs,
    }


def _leg_overlaps(rec: dict) -> int:
    """Intra-pairing leg overlaps («сотрудник в двух местах сразу») — the
    classic historical-data defect. The board validator compares only
    BETWEEN pairings, so inside a chain it is checked here."""
    n = 0
    legs = rec["legs"]
    for a, b in zip(legs, legs[1:]):
        try:
            if _dt(b["departureUTCDateTime"]) < _dt(a["arrivalUTCDateTime"]):
                n += 1
        except (KeyError, ValueError):
            continue
    return n


def _side_metrics(recs: list[dict], payload: dict, crew_by_id: dict,
                  pay_cfg: dict, cost_cfg: dict, rest_min: float,
                  hist: bool) -> dict:
    total_flights = len(payload.get("flights") or [])
    vpairings = [_validate_shape(r, crew_by_id, hist) for r in recs]
    violations = roster_board._validate(vpairings, crew_by_id)
    vcounts: dict[str, int] = {}
    for v in violations:
        vcounts[v["type"]] = vcounts.get(v["type"], 0) + 1
    leg_over = sum(_leg_overlaps(r) for r in recs if r["employee"])
    if leg_over:
        vcounts["leg_overlap"] = leg_over
    violations_total = len(violations) + leg_over

    by_fn = _by_fn(payload)
    covered: set[str] = set()
    credit_by_emp: dict[str, float] = {}
    cost_total = 0.0
    for r in recs:
        if not r["employee"] or not r["legs"]:
            continue
        covered |= {fn for fn in r["active"] if fn}
        pc = pay_credit.pairing_credit(r["legs"], pay_cfg, rest_min,
                                       active_fns=r["active"])
        credit_by_emp[r["employee"]] = (credit_by_emp.get(r["employee"], 0.0)
                                        + pc["credit_h"])
        cost_total += costing.pairing_costing(r["legs"], cost_cfg)[
            "total_cost"]

    # block_h = OPERATED flight-hours, counted ONCE per distinct covered
    # flight, so the two sides share a denominator (the historical side has
    # one flightAssignment PER SEAT, so per-pairing block would double-count
    # multi-crew flights; credit_h/paid_h/cost stay per-crew — their
    # difference across rosters is exactly what the comparison surfaces).
    block_h = sum(_dur_h(by_fn[fn]) for fn in covered if fn in by_fn)

    guarantee = float(pay_cfg.get("monthly_guarantee_h") or 0.0)
    paid_h = sum(max(c, guarantee) for c in credit_by_emp.values())
    shortfall = sum(1 for c in credit_by_emp.values() if c < guarantee)
    vals = sorted(credit_by_emp.values())
    return {
        "available": True,
        "pairings": len(recs),
        "assigned_pairings": sum(1 for r in recs if r["employee"]),
        "uncovered_pairings": sum(1 for r in recs if not r["employee"]),
        "employees_used": len(credit_by_emp),
        "flights_total": total_flights,
        "flights_covered": len(covered),
        "coverage_pct": round(100.0 * len(covered) / total_flights, 1)
        if total_flights else 0.0,
        "block_h": round(block_h, 1),
        "credit_h": round(sum(vals), 1),
        "credit_median_h": round(vals[len(vals) // 2], 1) if vals else 0.0,
        "paid_h": round(paid_h, 1),
        "guarantee_shortfall_crews": shortfall,
        "cost_total": round(cost_total, 2),
        "violations": vcounts,
        "violations_total": violations_total,
    }


_EMPTY_SIDE = {"available": False}


def build_report(inst_id: str, inst: dict) -> dict:
    payload = registry.load_payload(registry.instance_payload_path(inst))
    pay_cfg = pay_credit.load_pay_config(inst)
    cost_cfg = costing.load_cost_config(inst)
    rest_min = float(pay_credit.min_rest_minutes(inst))

    notes = [
        "исторические цепочки свёрнуты в пейринги по паузе >= "
        f"{rest_min:g} мин — нарушение «rest» для исторической стороны "
        "невыявимо по построению",
        "проверка home_base для исторической стороны отключена "
        "(цепочки не якорены на базу)",
        "нарушения считает inline-валидатор доски (покрытие, тип ВС, "
        "недоступность, база, перехлёст, отдых) — не полный движок правил",
        "блок-часы — операционные (каждый покрытый рейс учтён один раз); "
        "кредит-часы, оплата и стоимость — на экипаж (их различие между "
        "ростерами и есть предмет сравнения)",
    ]
    dups = _duplicate_numbers(payload)
    if dups:
        notes.append(
            f"в наборе {dups} номеров рейсов встречаются на нескольких датах "
            "(ежедневные рейсы): назначения ссылаются на номер, поэтому "
            "исторические метрики для таких рейсов приблизительны")

    ours_recs = _ours_records(inst, payload)
    if ours_recs is None:
        ours = dict(_EMPTY_SIDE, reason="no_stage2_roster")
    else:
        crew = roster_board._resolve(inst)[1] or _crew_from_payload(payload)
        ours = _side_metrics(ours_recs, payload, crew, pay_cfg, cost_cfg,
                             rest_min, hist=False)

    hist_recs = _hist_records(payload, rest_min)
    if not hist_recs:
        hist = dict(_EMPTY_SIDE, reason="no_historical_assignments")
        notes.append("исторический ростер пуст: в flightAssignments нет "
                     "заполненных employee (сид-данные или импорт без "
                     "исторических назначений)")
    else:
        hist = _side_metrics(hist_recs, payload, _crew_from_payload(payload),
                             pay_cfg, cost_cfg, rest_min, hist=True)

    delta = None
    if ours.get("available") and hist.get("available"):
        delta = {k: round(ours[k] - hist[k], 2)
                 for k in ("coverage_pct", "block_h", "credit_h", "paid_h",
                           "cost_total", "employees_used",
                           "violations_total")}
    return {
        "instance": inst_id,
        "label": inst.get("label"),
        "ours": ours, "historical": hist, "delta": delta,
        "notes": notes,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
    }


_ROWS = (
    ("coverage_pct", "Покрытие рейсов, %"),
    ("flights_covered", "Рейсов покрыто"),
    ("pairings", "Пейрингов"),
    ("uncovered_pairings", "Непокрытых пейрингов"),
    ("employees_used", "Экипажей задействовано"),
    ("block_h", "Блок-часы (кресло-часы)"),
    ("credit_h", "Кредит-часы"),
    ("credit_median_h", "Медиана кредита на экипаж, ч"),
    ("paid_h", "Оплачиваемые часы (с гарантией)"),
    ("guarantee_shortfall_crews", "Экипажей ниже гарантии"),
    ("cost_total", "Стоимость, у.е."),
    ("violations_total", "Нарушений (inline-валидатор)"),
)


def markdown_comparison(report: dict) -> str:
    """Диагностическое сравнение ростеров (H08: НЕ юридический акт приёмки —
    ролевая/слотовая легальность requiredSkill здесь не сертифицируется).
    Аудит 2026-07-19 (P2-03): раздел «Подписи» удалён, а «актовая»
    терминология вычищена из имени функции и кнопок всех локализаций —
    таблица подписей превращала диагностический документ в акт."""
    o, h = report["ours"], report["historical"]

    def cell(side, key):
        return str(side.get(key, "—")) if side.get("available") else "—"

    lines = [
        "# Диагностическое сравнение ростеров (Ф8 — историческая репликация)",
        "",
        "> **Это диагностическое сравнение, а не юридический акт приёмки.** "
        "Ролевая/слотовая легальность (requiredSkill: занят ли каждый слот "
        "квалифицированным членом экипажа) здесь НЕ проверяется — документ "
        "показывает покрытие, часы, стоимость и нарушения по правилам доски, "
        "но не заверяет полную легальность ростера.",
        "",
        f"Экземпляр: **{report['label'] or report['instance']}** "
        f"(`{report['instance']}`)  ",
        f"Сформирован: {report['generated_at']}",
        "",
        "| Метрика | Наш ростер | Исторический | Дельта |",
        "|---|---|---|---|",
    ]
    delta = report.get("delta") or {}
    for key, title in _ROWS:
        d = delta.get(key)
        lines.append(f"| {title} | {cell(o, key)} | {cell(h, key)} | "
                     f"{d if d is not None else '—'} |")
    for side, name in ((o, "наш ростер"), (h, "исторический ростер")):
        if side.get("available") and side.get("violations"):
            kinds = ", ".join(f"{k}: {n}" for k, n in
                              sorted(side["violations"].items()))
            lines.append("")
            lines.append(f"Нарушения ({name}): {kinds}")
        elif not side.get("available"):
            lines.append("")
            lines.append(f"Сторона «{name}» недоступна: "
                         f"{side.get('reason')}")
    lines += ["", "## Ограничения метода", ""]
    lines += [f"- {n}" for n in report.get("notes") or []]
    return "\n".join(lines) + "\n"


# Обратная совместимость импортов (имя до 2026-07-19).
markdown_act = markdown_comparison
