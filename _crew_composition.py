"""Правила состава экипажа (multirole инкремент 4) — валидатор по РЕАЛЬНЫМ
участкам flight-level ростера. Все правила ВЫКЛЮЧЕНЫ по умолчанию
(rules_config: comp_* = 0) — с дефолтным конфигом проверка инертна и вердикты
байт-идентичны.

Три правила каркаса (общепринятые в отрасли; включаются через
CREW_RULES_CONFIG / правила what-if сценария):
  comp_no_two_reserves=1        — не два резервиста на одном участке;
  comp_min_pair_seniority_weight>0 — КАЖДАЯ пара членов экипажа участка
                                  (зеркало Kotlin forEachUniquePair) должна
                                  иметь сумму seniorityWeight не ниже порога;
                                  формула веса: max(1, 11-(raw-1)//100),
                                  диапазон 2..11 (raw 1..1000); участки с
                                  ОДНИМ занятым местом пропускаются;
  comp_language_mix=1           — хотя бы один член экипажа участка владеет
                                  языками участка (объединение языков
                                  аэропортов вылета/прилёта; пустой список
                                  требований удовлетворён — зеркало Kotlin
                                  speaksAny).

Kotlin-ограничения GERAD2P покрывают со-локационные пары (оба места одного
synthetic-рейса) ВО ВРЕМЯ решения; этот валидатор — точка ИСТИНЫ на слиянии
multirole (в independent/joint режимах КВС и ВП одного участка приходят из
разных pairing-рейсов, солвер их вместе не видит).
"""
from __future__ import annotations

import rules_config


def seniority_weight(raw) -> int:
    """Зеркало Employee.seniorityWeight(): 1..1000 (меньше = старше) ->
    вес 2..11 (больше = старше; raw=1 -> 11, raw=1000 -> 2)."""
    try:
        r = int(raw)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: канон-схема пропускает inf в seniority (1e309 в
        # JSON) — нейтральный вес вместо трейсбека (аудит 2026-07-19 P2-07)
        r = 500
    r = min(1000, max(1, r))
    return max(1, 11 - (r - 1) // 100)


def _ref(v, key):
    return v.get(key) if isinstance(v, dict) else v


def check_composition(payload: dict, solution: dict,
                      rules: dict | None = None) -> dict:
    """{'enabled', 'violations_total', 'violations': {rule: n},
    'legs_checked', 'examples': [...]} — fail-closed потребитель обязан
    считать violations_total > 0 браком, когда enabled."""
    if rules is None:
        rules = rules_config.effective()
    no_two_res = int(rules.get("comp_no_two_reserves") or 0)
    min_pair_w = int(rules.get("comp_min_pair_seniority_weight") or 0)
    lang_mix = int(rules.get("comp_language_mix") or 0)
    enabled = bool(no_two_res or min_pair_w or lang_mix)
    out = {"enabled": enabled, "violations_total": 0, "violations": {},
           "legs_checked": 0, "examples": []}
    if not enabled:
        return out

    emp = {e.get("id"): e for e in payload.get("employees", [])
           if isinstance(e, dict)}
    airports = {a.get("code"): a for a in payload.get("airports", [])
                if isinstance(a, dict)}
    # Ревью пакета (Пакет B): участки индексируются leg-идентичностью
    # (flightLegId предпочтителен) — повторные номера восстановленного
    # ростера НЕ схлопываются в один «участок» (иначе включённый языковой
    # микс маскировался, а «не два резервиста» давал ложные срабатывания
    # от объединённых экипажей двух дат).
    flights: dict = {}
    for f in payload.get("flights", []):
        if not isinstance(f, dict):
            continue
        lid = f.get("flightLegId")
        flights[lid if isinstance(lid, str) and lid
                else f.get("flightNumber")] = f
        flights.setdefault(f.get("flightNumber"), f)

    crew_of_leg: dict = {}
    for a in solution.get("flightAssignments", []):
        if not isinstance(a, dict):
            continue
        fn = _ref(a.get("flight"), "flightNumber")
        lid = a.get("flightLegId")
        key = lid if isinstance(lid, str) and lid else fn
        eid = _ref(a.get("employee"), "id")
        if key is None or eid is None or eid not in emp:
            continue
        crew_of_leg.setdefault(key, []).append(emp[eid])

    v: dict = {}

    def add(rule, fn, detail):
        v[rule] = v.get(rule, 0) + 1
        if len(out["examples"]) < 10:
            out["examples"].append({"rule": rule, "leg": str(fn),
                                    "detail": detail})

    for fn, crew in crew_of_leg.items():
        out["legs_checked"] += 1
        if no_two_res:
            reserves = [e.get("id") for e in crew if e.get("isReserve")]
            if len(reserves) >= 2:
                add("no_two_reserves", fn, ", ".join(map(str, reserves)))
        if min_pair_w and len(crew) >= 2:
            # ПЕР-ПАРНО, как Kotlin forEachUniquePair (ревью 2026-07-19:
            # сумма по всему экипажу из 3+ мест маскировала слабую пару —
            # три веса по 2 давали 6 >= 6 при том, что каждая пара 4 < 6)
            ws = [seniority_weight(e.get("seniority")) for e in crew]
            weak = None
            for a_i in range(len(ws)):
                for b_i in range(a_i + 1, len(ws)):
                    if ws[a_i] + ws[b_i] < min_pair_w:
                        weak = (ws[a_i], ws[b_i])
                        break
                if weak:
                    break
            if weak:
                add("min_pair_seniority_weight", fn,
                    f"пара весов {weak[0]}+{weak[1]} < порога {min_pair_w}")
        if lang_mix:
            f = flights.get(fn) or {}
            req: list = []
            for side in ("departureAirport", "arrivalAirport"):
                ap = airports.get(f.get(side)) or {}
                for lng in ap.get("languages") or []:
                    if lng not in req:
                        req.append(lng)
            if req and crew and not any(
                    set(req) & set(e.get("languages") or []) for e in crew):
                add("language_mix", fn, "требуются: " + ", ".join(req))

    out["violations"] = dict(sorted(v.items()))
    out["violations_total"] = sum(v.values())
    return out
