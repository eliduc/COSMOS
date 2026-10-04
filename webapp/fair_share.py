"""Fair-share workload balancing (#9) + pay-and-credit Phase C
(2026-07-15): guarantee-aware credit redistribution over the CURRENT
roster.

The Phase A pay layer exposed the leak this module attacks: payroll is
sum(max(credit_i, guarantee)) — concentrating credit on a few crew while
others idle burns the monthly guarantee twice (the busy crew is paid
above the floor, the idle crew is paid the floor for nothing). Because
total assigned credit is fixed by the pairing selection, payroll is
minimized by SPREADING credit so nobody exceeds the guarantee ceiling —
the same direction as the European fair-share equity model (even
workload distribution).

Slice 1 (this module): a deterministic local search over LEGAL
single-pairing reassignments of the CURRENT effective roster (solver
output + existing manual board moves; PINNED pairings are never touched):

  - a move is legal iff the target crew matches the pairing's home base
    and type ratings, is available on its days, and the pairing fits the
    target's timeline without overlap and with the tier-1 inter-pairing
    rest — the SAME rules the interactive board enforces (the final plan
    is re-checked with roster_board._validate and must not add a single
    violation, else the plan is discarded loudly);
  - objective, lexicographic: payroll hours down first, then the credit
    coefficient of variation (fairness) down with payroll-neutral moves;
  - the result is APPLIED AS BOARD MOVES (the existing overlay): visible
    on the board / in the cabinet / in the pay card, and fully
    reversible with the board's "Сбросить правки".

NOT considered (same honesty note as the board): the solver's full FTL
rulebook (monthly block cap, duty limits) and soft preferences — a
rebalanced roster is a planner PROPOSAL, not a legality certificate.
Slice 2 (later): the same objective as a native Timefold soft constraint
so future stage-2 runs balance natively.
"""
from __future__ import annotations

from datetime import datetime

import rules_config

from . import registry, roster_board
from .pay_credit import (load_pay_config, load_cost_config,
                         min_rest_minutes, pairing_credit)

MAX_MOVES = 300          # hard cap per plan (runaway guard)


class FairShareError(ValueError):
    """Invalid fair-share input/state; message is safe to show."""


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ------------------------------------------------------------------ state

def _effective_state(inst_id: str, inst: dict):
    """The board's truth: pairings with their EFFECTIVE employee (solver +
    stored moves), pins, crew table, per-pairing credit hours."""
    pairings, crew_by_id, _days = roster_board._resolve(inst)
    if pairings is None:
        return None
    ed = roster_board._reconcile(inst_id, {p["pid"] for p in pairings})
    moves = ed.get("moves") or {}
    pins = set(ed.get("pins") or [])

    payload = registry.load_payload(registry.instance_payload_path(inst))
    assign_doc = roster_board._assignment(inst) or {}
    by_fn = roster_board.flights_by_fn(payload, assign_doc)
    active_by_pid = {
        str(pr.get("pid") or ""): ((pr.get("fns") or [])
                                   if pr.get("active_fns") is None
                                   else pr["active_fns"])
        for pr in assign_doc.get("pairings", []) if isinstance(pr, dict)}

    pay_cfg = load_pay_config(inst)
    rest_min = min_rest_minutes(inst)
    for p in pairings:
        # P0-4 (ревью 2026-07-20): оверлей накладывается ПО МЕСТАМ тем же
        # соглашением, что board() — иначе fair-share не видел занятость и
        # кредит вторых мест multi-crew пейрингов и добавлял реальные
        # нарушения вопреки собственной гарантии.
        for i, s in enumerate(p.get("seats") or []):
            key = (p["pid"] if i == 0
                   else f"{p['pid']}#{s.get('slot')}")
            s["employee"] = (moves[key] if key in moves
                             else s.get("solver_employee"))
        seats = p.get("seats") or []
        p["employee"] = (seats[0]["employee"] if seats
                         else (moves[p["pid"]] if p["pid"] in moves
                               else p["solver_employee"]))
        p["locked"] = p["pid"] in pins
        fns = [leg["fn"] for leg in p["legs"]]
        legs = [by_fn[fn] for fn in fns if fn in by_fn]
        a = active_by_pid.get(p["pid"])
        active = fns if a is None else a
        p["credit_h"] = (pairing_credit(legs, pay_cfg, rest_min,
                                        active_fns=active)["credit_h"]
                         if legs else 0.0)
    return pairings, crew_by_id, pay_cfg


def _seat_holders(p) -> set:
    """Занятые места пейринга (после посадочного оверлея)."""
    out = {s.get("employee") for s in (p.get("seats") or [])}
    out.discard(None)
    if not out and p.get("employee"):
        out = {p["employee"]}
    return out


def _credits_by_crew(pairings, crew_by_id) -> dict[str, float]:
    """P0-4: кредит пейринга начисляется КАЖДОМУ занятому месту (single-crew
    поведение не меняется — одно место)."""
    credit = {cid: 0.0 for cid in crew_by_id}
    for p in pairings:
        for emp in _seat_holders(p):
            if emp in credit:
                credit[emp] += p["credit_h"]
    return credit


def _payroll(credit: dict[str, float], guarantee: float) -> float:
    return sum(max(c, guarantee) for c in credit.values())


def _cv(credit: dict[str, float]) -> float | None:
    vals = list(credit.values())
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return (var ** 0.5) / mean


# --------------------------------------------------------------- legality

_SKILL_FIRST_SEAT = object()      # sentinel: проверять роль ПЕРВОГО места


def _move_legal(p, target_id, crew_by_id, by_target, rest_min,
                required_skill=_SKILL_FIRST_SEAT) -> bool:
    """May pairing p be flown by target crew? Mirrors the board validator:
    required skill of the seat, type rating, home base, unavailability, and
    the target's timeline (no overlap, inter-pairing rest on both sides).

    required_skill: по умолчанию — роль первого места; передайте роль
    конкретного места (или None), чтобы проверить занятие ИМЕННО его
    (посадочный ремонт фазы A, 2026-07-20)."""
    crew = crew_by_id.get(target_id)
    if crew is None:
        return False
    # P0-4 (аудит документации 2026-07-20): требуемая роль места —
    # сотрудник только с ролью FirstOfficer не может легально принять
    # Captain-место; None у legacy-мест = требование не записано.
    if required_skill is _SKILL_FIRST_SEAT:
        seats = p.get("seats") or []
        rs = seats[0].get("requiredSkill") if seats else None
    else:
        rs = required_skill
    if rs and rs not in (crew.get("skills") or []):
        return False
    for ac in p["aircraft"]:
        if ac and ac not in crew["type_ratings"]:
            return False
    cbase = crew.get("base")
    if cbase and p.get("base") and p["base"] != cbase:
        return False
    if crew["unavailable"] & set(p["days"]):
        return False
    dep, arr = _dt(p["dep_utc"]), _dt(p["arr_utc"])
    for q in by_target.get(target_id, []):
        q_dep, q_arr = _dt(q["dep_utc"]), _dt(q["arr_utc"])
        if dep < q_arr and q_dep < arr:            # time overlap
            return False
        gap_min = ((q_dep - arr) if arr <= q_dep
                   else (dep - q_arr)).total_seconds() / 60
        if gap_min < rest_min:
            return False
    return True


# ------------------------------------------------------------------- plan

def build_plan(inst_id: str, inst: dict) -> dict:
    """Analysis + deterministic rebalancing plan. Does NOT write anything."""
    state = _effective_state(inst_id, inst)
    if state is None:
        return {"available": False, "reason": "no_roster"}
    pairings, crew_by_id, pay_cfg = state
    guarantee = pay_cfg["monthly_guarantee_h"]
    rate = float(load_cost_config(inst)["block_hour_rate"])
    # LEGALITY rest boundary must come from the SAME source the board
    # validator uses (rules_config), not the pay module's file read — a
    # runtime airline-config PUT made the two diverge and the safety net
    # 500'ed the feature (audit 2026-07-15)
    rest_min = rules_config.effective().get("min_rest_min", 600)

    # baseline violations: the plan must never add one
    base_viol = len(roster_board._validate(
        [dict(p) for p in pairings], crew_by_id))

    credit = _credits_by_crew(pairings, crew_by_id)
    total_credit = sum(credit.values())
    payroll0 = _payroll(credit, guarantee)
    cv0 = _cv(credit)
    floor = max(total_credit, guarantee * len(crew_by_id))

    # P0-4: занятость строится по ВСЕМ местам — таймлайн-проверка
    # _move_legal видит и вторые места multi-crew пейрингов
    by_emp: dict[str, list] = {}
    for p in pairings:
        for emp in _seat_holders(p):
            by_emp.setdefault(emp, []).append(p)

    def try_moves(allow_neutral_fairness: bool) -> list[dict]:
        """One greedy pass; mutates credit/by_emp/pairings in place."""
        done: list[dict] = []
        improved = True
        while improved and len(moves_out) + len(done) < MAX_MOVES:
            improved = False
            # donors: most-overpaid first (deterministic ordering)
            donors = sorted(
                (cid for cid, c in credit.items() if c > guarantee),
                key=lambda cid: (-credit[cid], cid))
            for donor in donors:
                # P0-4: донором переноса может быть только пейринг, где
                # donor держит ПЕРВОЕ место — set_move правит первое место,
                # и перенос не должен трогать чужое (второе) место
                cand = sorted(
                    (p for p in by_emp.get(donor, [])
                     if not p["locked"] and p.get("employee") == donor),
                    key=lambda p: (-p["credit_h"], p["pid"]))
                moved = False
                for p in cand:
                    x = p["credit_h"]
                    if x <= 0:
                        continue
                    receivers = sorted(
                        (cid for cid, c in credit.items()
                         if cid != donor and c < guarantee),
                        key=lambda cid: (credit[cid], cid))
                    for rcpt in receivers:
                        a, b = credit[donor], credit[rcpt]
                        delta = (max(a - x, guarantee)
                                 + max(b + x, guarantee)
                                 - max(a, guarantee) - max(b, guarantee))
                        if delta > 1e-9:
                            continue
                        if delta > -1e-9 and not allow_neutral_fairness:
                            continue
                        if delta > -1e-9:          # payroll-neutral: only
                            if b + x >= a - x:     # take if it levels
                                continue
                        if not _move_legal(p, rcpt, crew_by_id, by_emp,
                                           rest_min):
                            continue
                        # commit the move in memory (первое место — та же
                        # правка, что запишет set_move; страховочный
                        # recheck _validate видит реальную посадку)
                        by_emp[donor].remove(p)
                        by_emp.setdefault(rcpt, []).append(p)
                        credit[donor] -= x
                        credit[rcpt] += x
                        p["employee"] = rcpt
                        if p.get("seats"):
                            p["seats"][0]["employee"] = rcpt
                        done.append({"pid": p["pid"], "from": donor,
                                     "to": rcpt, "credit_h": x})
                        improved = moved = True
                        break
                    if moved:
                        break
                if moved:
                    break
        return done

    moves_out: list[dict] = []
    moves_out += try_moves(allow_neutral_fairness=False)   # payroll pass
    moves_out += try_moves(allow_neutral_fairness=True)    # fairness pass

    # RAW execution order: every prefix of this sequence is legal by
    # construction of the in-memory search. Application MUST follow it —
    # applying the coalesced list transits an illegal intermediate roster
    # when a pairing's second hop depends on another move vacating its
    # target (audit 2026-07-15, reproduced).
    apply_sequence = [{"pid": m["pid"], "to": m["to"]} for m in moves_out]

    # ...while the REPORT shows one final move per pairing (original owner
    # -> final owner), dropping round-trips
    final: dict[str, dict] = {}
    for mv in moves_out:
        if mv["pid"] in final:
            final[mv["pid"]]["to"] = mv["to"]
        else:
            final[mv["pid"]] = dict(mv)
    moves_out = [m for m in final.values() if m["from"] != m["to"]]

    # the whole plan must not add a single board violation
    after_viol = len(roster_board._validate(
        [dict(p) for p in pairings], crew_by_id))
    if after_viol > base_viol:
        raise FairShareError(
            f"rebalancer produced violations ({base_viol} -> {after_viol})"
            " — plan discarded")

    payroll1 = _payroll(credit, guarantee)
    cv1 = _cv(credit)
    return {
        "available": True,
        "instance": inst_id,
        "guarantee_h": guarantee,
        "total_crew": len(crew_by_id),
        "total_credit_h": round(total_credit, 1),
        "floor_h": round(floor, 1),
        "before": {"payroll_h": round(payroll0, 1),
                   "burn_h": round(payroll0 - total_credit, 1),
                   "cv": round(cv0, 3) if cv0 is not None else None},
        "after": {"payroll_h": round(payroll1, 1),
                  "burn_h": round(payroll1 - total_credit, 1),
                  "cv": round(cv1, 3) if cv1 is not None else None},
        "savings_h": round(payroll0 - payroll1, 1),
        "savings_cost": round((payroll0 - payroll1) * rate, 2),
        "headroom_left_h": round(payroll1 - floor, 1),
        "moves": moves_out,
        "apply_sequence": apply_sequence,
        "violations_before": base_viol,
        "violations_after": after_viol,
    }


def apply_plan(inst_id: str, inst: dict, email: str | None = None) -> dict:
    """Recompute the plan on the CURRENT state (no staleness) and write it
    as ordinary board moves — visible everywhere, reversible via the
    board's reset. Returns the applied plan."""
    plan = build_plan(inst_id, inst)
    if not plan.get("available"):
        return plan
    applied = 0
    # apply in RAW execution order: every prefix is legal by construction
    # (the coalesced report order is NOT — audit 2026-07-15)
    for mv in plan["apply_sequence"]:
        try:
            roster_board.set_move(inst_id, inst, mv["pid"], mv["to"],
                                  email)
            applied += 1
        except roster_board.RosterBoardError:
            # a pin raced us mid-apply: stop honestly — the applied RAW
            # prefix is a legal state and fully reversible
            break
    plan["applied_moves"] = applied
    return plan
