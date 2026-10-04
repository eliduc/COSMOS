"""Independent coverage validator (audit package B, 2026-07-12).

Reads RAW inputs (payload flights) and a SOLUTION (flight-level roster
JSON) and reports, per instance: required legs, assigned legs, missing
legs (named), duplicate assignments, unknown flights. No solver state is
trusted — only the two files.

Usage: python _coverage_validator.py payload.json solution.json
       python _coverage_validator.py --gerad [inst ...]   (batch mode)
Output (batch): final-report/gerad_coverage_check.json
"""
from __future__ import annotations

import _data_paths
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _rid(v, key):
    return v.get(key) if isinstance(v, dict) else v


def check(payload: dict, solution: dict) -> dict:
    """Seat-honest coverage (C01): the demand is the payload's explicit crew
    seats (flightAssignments), and a leg is covered only when the number of
    crew filled on it MEETS the number of seats it requires. A leg needing
    Captain+FirstOfficer whose roster fills only the Captain therefore reads
    NOT fully_covered — the old flightNumber-set predicate wrongly said OK.

    Coverage is counted per LEG (seats required vs seats filled), NOT per
    (leg, indexInFlight) slot. That distinction matters: the two-stage
    expander hardcodes `indexInFlight:0` on every regenerated supply seat
    (`_gerad_stage2_roster.expand_to_flight_level`), so keying on the raw
    index would mismatch any legitimately-covered single-seat upload whose
    seat carried a nonzero index (1-based seat numbering, captain-only
    exports). Counting seats per leg is index-agnostic and still catches an
    under-filled multi-seat leg (filled < required). Verified byte-for-byte
    isomorphic to the previous logic on single-crew GERAD data via
    `_coverage_validator.py --gerad`.

    Return keys and their meanings are unchanged (`missing_legs` stays
    LEG-level) so gate_result_status / results.py need no edit.

    A payload WITHOUT explicit flightAssignments (test fixtures / degenerate
    inputs) falls back to the legacy one-slot-per-flight predicate.
    """
    from _leg_identity import leg_id   # repo-root helper (phase 1)

    # M12: an assigned employee must EXIST in the payload's pool (a coordinated
    # E999 swap used to pass the membership gap). Enforced only when the
    # payload lists employees — no pool means it cannot be checked and must not
    # false-fail (some GERAD academic instances carry no employees list).
    emp_ids = {e.get("id") for e in payload.get("employees", [])
               if isinstance(e, dict)}
    check_emp = bool(emp_ids)

    # C01 phase 2: an assigned crew member must actually HOLD the seat's
    # requiredSkill (a Captain seat filled by an FO-only employee is not
    # coverage). Enforced only when the pool declares skills AND the seat
    # names a skill — GERAD (all employees skills=['Captain'], every seat
    # Captain) passes unchanged, so the --gerad gate stays byte-identical.
    emp_skills = {e.get("id"): set(e.get("skills") or [])
                  for e in payload.get("employees", []) if isinstance(e, dict)}
    check_skill = any(emp_skills.values())

    demand = payload.get("flightAssignments") or []
    if not demand:
        # ---- legacy fallback: implicit one crew slot per flight ----
        required = {f["flightNumber"] for f in payload["flights"]}
        assigned = Counter()
        unknown, unknown_employees, unassigned_slots = [], [], 0
        for a in solution.get("flightAssignments", []):
            fn = _rid(a.get("flight"), "flightNumber")
            eid = _rid(a.get("employee"), "id")
            if fn not in required:
                unknown.append(fn)
                continue
            if eid is None:
                unassigned_slots += 1
                continue
            if check_emp and eid not in emp_ids:
                unknown_employees.append(eid)
                continue
            assigned[fn] += 1
        missing = sorted(required - set(assigned))
        dups = {fn: c for fn, c in assigned.items() if c > 1}
        return {"required": len(required), "assigned": len(assigned),
                "skill_mismatches": 0,
                "missing": len(missing), "missing_legs": missing,
                "missing_slots": len(missing), "duplicates": len(dups),
                "unknown_flights": len(unknown),
                "unknown_employees": len(unknown_employees),
                "unassigned_slots": unassigned_slots,
                "fully_covered": (not missing and not unknown and not dups
                                  and not unknown_employees)}

    # ---- seat-honest path: explicit per-seat crew demand, counted per leg ----
    # The demand — how many seats each leg needs AND what skill each seat
    # requires — is reconstructed HERE from the payload's flightAssignments.
    # The solution's own `requiredSkill` is NEVER read (C01/C02 phase-2 review,
    # 2026-07-19, hardening): a hand-edited / external solution that relabels a
    # FirstOfficer seat as 'Captain' — or nulls the field to skip the check —
    # must not be able to certify coverage it does not have. "No solver state
    # is trusted." Seats are still COUNTED per leg (index-agnostic) so a
    # legitimate single-seat upload whose sole seat carries a nonzero index is
    # unaffected (test_single_seat_nonzero_index_still_covered).
    need = Counter()                       # seats required per leg
    demand_slot_skill: dict = {}           # (leg, idx) -> requiredSkill
    demand_leg_skills: dict = defaultdict(list)   # leg -> [requiredSkill, ...]
    for a in demand:
        lid = leg_id(a)
        need[lid] += 1
        idx = a.get("indexInFlight", 0) if isinstance(a, dict) else 0
        rsk = a.get("requiredSkill") if isinstance(a, dict) else None
        demand_slot_skill[(lid, idx)] = rsk
        demand_leg_skills[lid].append(rsk)

    def _seat_skill(lid, idx):
        """Authoritative required skill of the seat a supply row occupies,
        read from the PAYLOAD demand (never the solution row). Positional by
        (leg, indexInFlight); a single-seat leg is index-agnostic (the two-
        stage expander hardcodes indexInFlight:0). A multi-seat leg whose row
        carries an index absent from the demand cannot be authoritatively
        validated -> (None, False), which the caller fail-closes as no-cover
        whenever skills are being checked. Returns (skill, resolved)."""
        if (lid, idx) in demand_slot_skill:
            return demand_slot_skill[(lid, idx)], True
        if need[lid] == 1:
            return demand_leg_skills[lid][0], True
        return None, False                 # unrecognized multi-seat position

    filled = Counter()          # DISTINCT crew validly seated per leg
    seen_on_leg: dict = defaultdict(set)   # leg -> employee ids already seated
    repeated_legs: set = set()  # legs where ONE employee took two seats
    unknown, unknown_employees, unassigned_slots = [], [], 0
    skill_mismatches = []
    for a in solution.get("flightAssignments", []):
        lid = leg_id(a)
        eid = _rid(a.get("employee"), "id")
        idx = a.get("indexInFlight", 0) if isinstance(a, dict) else 0
        if lid not in need:
            unknown.append(lid)
            continue
        if eid is None:
            unassigned_slots += 1
            continue
        if check_emp and eid not in emp_ids:
            unknown_employees.append(eid)
            continue
        # One physical crew member cannot fill two seats of the SAME leg (a
        # dual-skilled employee seated as both Captain and FirstOfficer). The
        # repeat is a DUPLICATE, not a second fill (phase-2 review 2026-07-19).
        if eid in seen_on_leg[lid]:
            repeated_legs.add(lid)
            continue
        rskill, resolved = _seat_skill(lid, idx)
        if check_skill and (not resolved or (
                rskill and eid in emp_skills
                and rskill not in emp_skills[eid])):
            # wrong-skill (or unverifiable) crew does not cover the seat
            skill_mismatches.append(eid)
            continue
        seen_on_leg[lid].add(eid)
        filled[lid] += 1
    # a leg is missing if fewer seats are filled than required; over-filled
    # (more distinct crew than seats) OR one employee twice is a duplicate.
    missing_legs = sorted(str(lid) for lid in need if filled[lid] < need[lid])
    over_legs = {lid for lid in filled if filled[lid] > need[lid]}
    dup_legs = over_legs | repeated_legs
    missing_slots = sum(max(0, need[lid] - filled[lid]) for lid in need)
    covered_legs = {lid for lid in need if filled[lid] >= need[lid]}
    return {"required": len(need), "assigned": len(covered_legs),
            "skill_mismatches": len(skill_mismatches),
            "missing": len(missing_legs), "missing_legs": missing_legs,
            "missing_slots": missing_slots, "duplicates": len(dup_legs),
            "unknown_flights": len(unknown),
            "unknown_employees": len(unknown_employees),
            "unassigned_slots": unassigned_slots,
            "fully_covered": (not missing_legs and not dup_legs
                              and not unknown and not unknown_employees)}


# ---- Общий контракт «доказанно непокрываемый рейс» (аудит A4, 04.09.2026)
#
# Класс ошибки A4: эвристический зонд («прямой перегон либо одна
# пересадка») выдавался за доказательство невозможности, а потребители
# (проверка покрытия, гейт этапа 2) прощали недостачу по голому списку
# имён. Теперь исключение из покрытия принимается ТОЛЬКО с приложенным
# доказательством, и доказательство привязано к отпечатку данных: при
# смене набора (добавлен рейс, сдвинуто время) оно недействительно.
#
# Запись доказательства (proven_impossible_proofs[i]):
#   {"leg": <имя участка в пространстве каталога>,
#    "kind": "graph_unreachable" | "escalation_proved",
#    "detail": <словами, для человека>,
#    "horizon": {"pairing_span_min": ..., "bases": [...],
#                "data_from": ..., "data_to": ...},
#    "data_fingerprint": <sha256 физических фактов набора>}
#
# Виды: graph_unreachable — станция участка недостижима из базы (или
# база недостижима после него) обходом графа рейсов без предела числа
# перегонов в пределах размаха командировки (необходимое условие: любой
# законный маршрут — путь в этом графе, значит пустота графа — строгое
# доказательство); escalation_proved — доказательная ступень эскалации
# построителя обошла окно размаха полностью, без усечений и срыва по
# времени, и цепочки с участком не нашла.

PROOF_KINDS = ("graph_unreachable", "escalation_proved")


def _canon_time(value) -> str | None:
    """Метка времени в канонической форме (наивный UTC). Нечитаемая
    метка входит в отпечаток сырой строкой: это лишь делает отпечаток
    строже (иначе записанная та же метка → другой отпечаток → отказ),
    но никогда не приравнивает разные данные."""
    if value is None:
        return None
    try:
        from _canonical_time import canonical_str
        return canonical_str(value)
    except (ValueError, ImportError):
        return str(value)


def payload_data_fingerprint(payload: dict) -> str:
    """Отпечаток ФИЗИЧЕСКИХ фактов набора: множество рейсов (станции,
    время вылета/прилёта, признак транспорта) и домашние базы людей.
    Имена рейсов в отпечаток не входят: переименование в wire-имена
    (`_leg_rekey`) факты не меняет, а вот добавленный, убранный или
    сдвинутый рейс меняет отпечаток — и все прежние доказательства
    становятся недействительными."""
    import hashlib
    flights = []
    for f in (payload.get("flights") or []):
        if not isinstance(f, dict):
            continue
        flights.append([
            str(f.get("departureAirport")), str(f.get("arrivalAirport")),
            _canon_time(f.get("departureUTCDateTime")),
            _canon_time(f.get("arrivalUTCDateTime")),
            bool(f.get("transportOnly"))])
    flights.sort(key=json.dumps)
    homes = sorted({str(e.get("homeAirport"))
                    for e in (payload.get("employees") or [])
                    if isinstance(e, dict) and e.get("homeAirport")})
    canon = {"flights": flights, "home_airports": homes, "v": 1}
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()


def proven_exclusions(catalog: dict, payload: dict) -> dict:
    """Какие непокрытые участки каталога ДОКАЗАННО непокрываемы на ЭТИХ
    данных. Возврат: {"allowed": set(имён в пространстве каталога),
    "rejected": [{"leg", "why"}], "fingerprint": <отпечаток payload>}.

    Принимается только запись из proven_impossible_proofs с известным
    видом доказательства и отпечатком, равным отпечатку переданного
    набора. Имя из proven_impossible_legs без такой записи — НЕ
    доказательство (каталог прежнего выпуска, линия без доказательств,
    иные данные) и в allowed не попадает; причина — словами в rejected."""
    fp = payload_data_fingerprint(payload)
    names = [str(x) for x in (catalog.get("proven_impossible_legs") or [])]
    proofs = catalog.get("proven_impossible_proofs")
    by_leg: dict = {}
    for p in (proofs if isinstance(proofs, list) else []):
        if isinstance(p, dict) and p.get("leg") is not None:
            by_leg.setdefault(str(p["leg"]), []).append(p)
    allowed: set = set()
    rejected: list = []
    for leg in sorted(set(names) | set(by_leg)):
        recs = by_leg.get(leg) or []
        if not recs:
            rejected.append({"leg": leg, "why": (
                "в каталоге нет записи доказательства "
                "(proven_impossible_proofs) — голое имя не доказательство")})
            continue
        why = None
        for p in recs:
            if p.get("kind") not in PROOF_KINDS:
                why = f"неизвестный вид доказательства {p.get('kind')!r}"
                continue
            if p.get("data_fingerprint") != fp:
                why = ("доказательство получено на других данных "
                       f"(отпечаток {str(p.get('data_fingerprint'))[:12]} "
                       f"≠ {fp[:12]})")
                continue
            why = None
            break
        if why is None:
            allowed.add(leg)
        else:
            rejected.append({"leg": leg, "why": why})
    return {"allowed": allowed, "rejected": rejected, "fingerprint": fp}



def main():
    args = sys.argv[1:]
    if args and args[0] == "--gerad":
        insts = [int(a) for a in args[1:]] or list(range(1, 8))
        out = []
        for inst in insts:
            payload = json.loads(
                (HERE / "data" / "gerad" / "payloads" / f"inst{inst}_p2.json")
                .read_text(encoding="utf-8"))
            for kind, path in [
                    ("direct", HERE / "data" / "gerad" /
                     f"p2_direct_solution_inst{inst}.json"),
                    ("two_stage", HERE / "data" / "gerad" /
                     f"p2_two_stage_solution_inst{inst}.json")]:
                if not path.exists():
                    continue
                r = check(payload,
                          json.loads(path.read_text(encoding="utf-8")))
                # checked_at makes stale rows visible when a subset run
                # merges with prior rows (audit round 6).
                import time
                r.update({"inst": inst, "config": kind,
                          "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                r["missing_legs"] = (r["missing_legs"]
                                     if r["missing"] <= 50 else r["missing"])
                out.append(r)
                print(f"inst{inst} {kind}: required={r['required']} "
                      f"assigned={r['assigned']} missing={r['missing']} "
                      f"dups={r['duplicates']} covered={r['fully_covered']}",
                      flush=True)
        p = _data_paths.results_dir() / "gerad_coverage_check.json"
        # Audit fix (2026-07-12, round 5): MERGE with existing rows — a
        # subset run must not shrink the artifact (it regressed 14 -> 6).
        try:
            prior = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            prior = []
        keep = [r for r in prior
                if (r.get("inst"), r.get("config")) not in
                {(n["inst"], n["config"]) for n in out}]
        merged = sorted(keep + out,
                        key=lambda r: (r.get("inst", 0), r.get("config", "")))
        p.write_text(json.dumps(merged, indent=1))
        print(f"saved -> {p.name} ({len(merged)} rows)", flush=True)
        return 0
    payload = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    solution = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    print(json.dumps(check(payload, solution), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
