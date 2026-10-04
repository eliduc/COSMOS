"""Interactive roster board (2026-07-15, gap #4).

The planner's day-to-day tool: the published stage-2 roster shown as an
editable board (crew x day, one pairing = one chip), where a planner can
MANUALLY reassign a pairing to another crew member or unassign it, PIN/LOCK
critical assignments (a pinned pairing is protected from edits), and see the
legality of every edit INSTANTLY — overlap, aircraft type rating, crew
unavailability, coverage and inter-pairing rest are re-checked after each
move and violations are surfaced inline.

Industrial precedent: Jeppesen Tracking Editor, Lufthansa NetLine/Crew,
Sabre, AIMS, NAVBLUE Pin Mode. This is the M-core: manual edits + pin/lock +
inline legality over the EXISTING stage-2 artifact and tier-1 rules. The
drag-and-drop gesture and "pin -> warm-start re-optimize" (feeding pins to
Timefold as planning pins) are the remaining L portion — they need a Kotlin
solver change and are documented, not built here.

Edits live outside the solver artifact (config/roster_edits.json keyed by
inst_id: {moves: {pid: emp|null}, pins: [pid]}), so the original solver
output is never mutated and a reset restores it.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime

import rules_config

from . import registry

EDITS_PATH = registry.REPO_ROOT / "webapp" / "config" / "roster_edits.json"
ASSIGN_DIR = registry.FINAL_REPORT

_lock = threading.Lock()

# Board size caps (keep the JSON payload and the DOM manageable; larger
# instances are truncated with a flag, same convention as results.roster_view).
MAX_CREW = 60
MAX_PAIRINGS = 500


class RosterBoardError(ValueError):
    """User-facing roster-board failure."""


# --------------------------------------------------------------- storage

def _load() -> dict:
    try:
        raw = json.loads(EDITS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"roster_edits store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    EDITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EDITS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(EDITS_PATH)


def edits_for(inst_id: str) -> dict:
    m = _load().get(inst_id)
    if not isinstance(m, dict):
        # No 'owner' key until first write, so set_*'s setdefault records the
        # real editor (audit 2026-07-15: a preset owner=None made the reset
        # owner check inert).
        return {"moves": {}, "pins": []}
    m.setdefault("moves", {})
    m.setdefault("pins", [])
    return m


def _store(inst_id: str, meta: dict) -> None:
    with _lock:
        data = _load()
        data[inst_id] = meta
        _save_locked(data)


def _reconcile(inst_id: str, valid_pids: set) -> dict:
    """Drop stored moves/pins whose pid is no longer in the artifact (stage-2
    is re-runnable and pids can shift), so counts and the 'edited' flag never
    reflect ghost edits (audit 2026-07-15). Persists only if it changed."""
    ed = edits_for(inst_id)
    # P0-4: ключ места 'pid#slot' жив, пока жив базовый pid (плоский ключ =
    # первое место; пины остаются уровня пейринга)
    moves = {k: v for k, v in (ed.get("moves") or {}).items()
             if k.split("#", 1)[0] in valid_pids}
    pins = [p for p in (ed.get("pins") or []) if p in valid_pids]
    if len(moves) != len(ed.get("moves") or {}) or \
            len(pins) != len(ed.get("pins") or []):
        ed["moves"] = moves
        ed["pins"] = pins
        if not moves and not pins:
            with _lock:
                data = _load()
                data.pop(inst_id, None)
                _save_locked(data)
            return {"moves": {}, "pins": []}
        _store(inst_id, ed)
    return ed


# --------------------------------------------------------------- resolve

def _dt(s: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(s)


def _assignment(inst: dict):
    """Единая воронка ростерного артефакта — через active-roster манифест
    (аудит-2 P1-06/P1-13): активный указатель с проверкой отпечатков,
    легальный legacy-фолбэк для меток без манифеста, fail-closed «stale»
    при любом расхождении. Причины смотрите в roster_source()."""
    from . import active_roster
    res = active_roster.resolve(inst)
    doc = res.get("assignment")
    if isinstance(doc, dict) and isinstance(doc.get("pairings"), list):
        return doc
    return None


def flights_by_fn(payload: dict, doc: dict | None) -> dict:
    """fn -> flight payload'а с доиндексацией wire-имён артефакта (Пакет B,
    ревью пакета): fair-share/pay/costing/base строили by_fn только по
    исходным номерам и МОЛЧА теряли re-key-нутые участки (F100@1) —
    занижая кредит, деньги и спрос по базам без единой ошибки."""
    by_fn = {f["flightNumber"]: f for f in payload.get("flights") or []}
    by_leg = {f.get("flightLegId"): f
              for f in payload.get("flights") or [] if f.get("flightLegId")}
    for wfn, rec in ((doc or {}).get("wire_map") or {}).items():
        if isinstance(rec, dict):
            leg = by_leg.get(rec.get("flightLegId")) \
                or by_fn.get(rec.get("flightNumber"))
            if leg is not None:
                by_fn.setdefault(wfn, leg)
    return by_fn


def roster_source(inst: dict) -> dict:
    """Статус источника ростера для UI: mode/run_id/предупреждения/stale."""
    from . import active_roster
    res = active_roster.resolve(inst)
    return {"status": res["status"], "warnings": res["warnings"],
            "mismatches": res["mismatches"], **(res.get("meta") or {})}


def _resolve(inst: dict):
    """Resolve the stage-2 assignment into board pairings + the crew table.
    Returns (pairings, crew_by_id, days) or (None, ...) when no roster."""
    doc = _assignment(inst)
    if doc is None:
        return None, {}, []
    payload = registry.load_payload(registry.instance_payload_path(inst))
    flights = flights_by_fn(payload, doc)
    employees = payload.get("employees") or []
    crew_by_id = {}
    for e in employees:
        eid = str(e.get("id"))
        crew_by_id[eid] = {
            "id": eid, "name": e.get("name") or eid,
            "base": e.get("homeAirport"),
            "seniority": e.get("seniority"),
            "role": (e.get("skills") or ["—"])[0],
            "skills": [str(x) for x in (e.get("skills") or [])],
            "type_ratings": [str(x) for x in (e.get("typeRatings") or [])],
            "unavailable": set(e.get("unavailableDays") or []),
        }

    # last-minute disruption overlay (2026-07-16): sick days are hard
    # unavailability for the board, its validator and every consumer
    # (fair-share, repair planner, cabinet) — one source of truth.
    from . import disruption
    for eid, days in disruption.sick_days(str(inst.get("id") or "")).items():
        if eid in crew_by_id:
            crew_by_id[eid]["unavailable"] |= days

    pairings = []
    all_days: set[str] = set()
    for pr in doc.get("pairings", []):
        if not isinstance(pr, dict):
            continue
        fns = pr.get("fns") or []
        legs = []
        for fn in fns:
            leg = flights.get(fn)
            if leg is None:
                continue
            legs.append({
                "fn": fn, "dep": leg.get("departureAirport"),
                "arr": leg.get("arrivalAirport"),
                "dep_utc": leg.get("departureUTCDateTime"),
                "arr_utc": leg.get("arrivalUTCDateTime"),
                "ac": str(leg.get("aircraftType") or ""),
            })
        legs = [x for x in legs if x["dep_utc"] and x["arr_utc"]]
        if not legs:
            continue
        legs.sort(key=lambda x: x["dep_utc"])
        d0 = date.fromisoformat(legs[0]["dep_utc"][:10])
        d1 = date.fromisoformat(legs[-1]["arr_utc"][:10])
        days = [(date.fromordinal(d0.toordinal() + i)).isoformat()
                for i in range((d1 - d0).days + 1)]
        all_days.update(days)
        # P0-4 (аудит документации 2026-07-20): ПОЛНЫЙ состав пейринга —
        # каждое место из crew[] артефакта (место = indexInFlight + роль);
        # legacy-артефакт без crew[] даёт одно синтетическое место без
        # требования роли (проверка навыка тогда пропускается).
        raw_crew = pr.get("crew")
        seats = []
        if isinstance(raw_crew, list) and raw_crew:
            # Ревью хвоста (2026-07-20): slot = ПОЗИЦИЯ места (0..N-1) после
            # детерминированной сортировки, а НЕ сырой indexInFlight —
            # тот может быть None, и тогда адрес места 'pid#None' уходил бы
            # в плоский ключ set_move и затирал первое место. Позиция —
            # стабильный, уникальный, никогда-не-None идентификатор места.
            for i, s in enumerate(sorted(
                    (s for s in raw_crew if isinstance(s, dict)),
                    key=lambda s: (s.get("indexInFlight") is None,
                                   s.get("indexInFlight")))):
                seats.append({
                    "slot": i,
                    "requiredSkill": s.get("requiredSkill"),
                    "solver_employee": s.get("employee"),
                })
        if not seats:
            seats = [{"slot": 0, "requiredSkill": None,
                      "solver_employee": pr.get("employee")}]
        pairings.append({
            "pid": str(pr.get("pid") or ""),
            "solver_employee": pr.get("employee"),
            "seats": seats,
            "base": legs[0]["dep"],
            "dep_utc": legs[0]["dep_utc"], "arr_utc": legs[-1]["arr_utc"],
            "nlegs": len(legs),
            "aircraft": sorted({x["ac"] for x in legs if x["ac"]}),
            "days": days, "legs": legs,
        })
    return pairings, crew_by_id, sorted(all_days)


# --------------------------------------------------------------- validate

def _validate(pairings, crew_by_id) -> list[dict]:
    """Hard-rule legality of the CURRENT assignment (pairing['employee']).
    Checks: coverage (unassigned), aircraft type rating, crew unavailability,
    home base, time overlap and inter-pairing rest. Returns a flat violation
    list; each pairing is annotated in place with a 'violations' list.

    NOT checked inline (the solver's full FTL/tier-1 engine, gap #2): monthly
    block-hour cap, duty span / landings-per-duty limits, and preferences —
    the board flags the KEY per-assignment rules, not the whole rulebook, so
    a clean board is not a legality certificate. Inter-pairing continuity is
    subsumed by the home-base check because GERAD pairings are base-anchored
    (start == end == a crew base)."""
    min_rest_min = rules_config.effective().get("min_rest_min", 600)
    out: list[dict] = []
    for p in pairings:
        p["violations"] = []

    # P0-4: coverage / skill / type-rating / unavailability — ПО МЕСТАМ
    # (место = slot из crew[] артефакта); хэндовые пейринги без seats
    # получают синтетическое первое место (совместимость юнит-тестов и
    # legacy-читателей).
    for p in pairings:
        seats = p.get("seats") or [
            {"slot": 0, "requiredSkill": None,
             "employee": p.get("employee"),
             "solver_employee": p.get("solver_employee")}]
        seen_emp_slots: dict[str, object] = {}
        for i, s in enumerate(seats):
            # Ревью P0 (2026-07-20): у вызывателей без посадочного оверлея
            # (fair-share мутирует плоский p['employee']) плоский сотрудник
            # относится ТОЛЬКО к первому месту; остальные места живут по
            # своему солверному назначению — иначе один человек «размножался»
            # на все места и валидатор давал фантомные dup_crew/skill.
            emp = s.get("employee",
                        p.get("employee") if i == 0
                        else s.get("solver_employee"))
            slot = s.get("slot")
            if not emp:
                p["violations"].append("uncovered")
                out.append({"type": "uncovered", "pid": p["pid"],
                            "slot": slot})
                continue
            crew = crew_by_id.get(emp)
            if crew is None:
                p["violations"].append("unknown_crew")
                out.append({"type": "unknown_crew", "pid": p["pid"],
                            "employee": emp, "slot": slot})
                continue
            if emp in seen_emp_slots:
                p["violations"].append("dup_crew")
                out.append({"type": "dup_crew", "pid": p["pid"],
                            "employee": emp, "slot": slot})
            seen_emp_slots[emp] = slot
            # роль места: сотрудник обязан владеть requiredSkill (None у
            # legacy-мест — требование не записано, проверка пропускается)
            rs = s.get("requiredSkill")
            if rs and rs not in (crew.get("skills") or []):
                p["violations"].append("skill")
                out.append({"type": "skill", "pid": p["pid"],
                            "employee": emp, "slot": slot, "detail": rs})
            for ac in p["aircraft"]:
                if ac and ac not in crew["type_ratings"]:
                    p["violations"].append("type_rating")
                    out.append({"type": "type_rating", "pid": p["pid"],
                                "employee": emp, "slot": slot,
                                "detail": ac})
                    break
            # home base: a base-anchored pairing must start at the crew's
            # base (mirrors the solver's HARD homeBase constraint).
            cbase = crew.get("base")
            if cbase and p.get("base") and p["base"] != cbase:
                p["violations"].append("home_base")
                out.append({"type": "home_base", "pid": p["pid"],
                            "employee": emp, "slot": slot,
                            "detail": p["base"] + "≠" + cbase})
            if crew["unavailable"] & set(p["days"]):
                p["violations"].append("unavailable")
                out.append({"type": "unavailable", "pid": p["pid"],
                            "employee": emp, "slot": slot,
                            "detail": sorted(crew["unavailable"]
                                             & set(p["days"]))[0]})

    # overlap + rest (per crew, over their current pairings in time order) —
    # P0-4: сотрудник учитывается на ЛЮБОМ месте пейринга
    by_emp: dict[str, list] = {}
    for p in pairings:
        seats = p.get("seats") or []
        emps = {s.get("employee",
                      p.get("employee") if i == 0
                      else s.get("solver_employee"))
                for i, s in enumerate(seats)}
        emps.discard(None)
        if not emps and p.get("employee"):
            emps = {p["employee"]}
        for emp in emps:
            by_emp.setdefault(emp, []).append(p)
    for emp, prs in by_emp.items():
        prs.sort(key=lambda x: x["dep_utc"])
        for a, b in zip(prs, prs[1:]):
            a_arr, b_dep = _dt(a["arr_utc"]), _dt(b["dep_utc"])
            if b_dep < a_arr:
                for pp in (a, b):
                    if "overlap" not in pp["violations"]:
                        pp["violations"].append("overlap")
                out.append({"type": "overlap", "pid": b["pid"],
                            "employee": emp, "detail": a["pid"]})
            else:
                rest_min = (b_dep - a_arr).total_seconds() / 60
                if rest_min < min_rest_min:
                    for pp in (a, b):
                        if "rest" not in pp["violations"]:
                            pp["violations"].append("rest")
                    out.append({"type": "rest", "pid": b["pid"],
                                "employee": emp,
                                "detail": int(rest_min)})
    return out


# --------------------------------------------------------------- board

def board(inst_id: str, inst: dict) -> dict:
    """Current board: crew rows, pairings with their CURRENT employee (solver
    output overlaid with stored moves), pins, and live violations. None-shaped
    'available': False when the instance has no stage-2 roster yet."""
    pairings, crew_by_id, days = _resolve(inst)
    if pairings is None:
        # Ревью пакета: причина недоступности называется — stale-отпечатки
        # манифеста не должны выглядеть как «ростера просто нет».
        src = roster_source(inst)
        reason = ("stale_fingerprint" if src.get("status") == "stale"
                  else "no_roster")
        return {"available": False, "reason": reason,
                "roster_source": src}
    all_pids = {p["pid"] for p in pairings}
    ed = _reconcile(inst_id, all_pids)   # prune moves/pins for vanished pids
    moves = ed.get("moves") or {}
    pins = set(ed.get("pins") or [])
    for p in pairings:
        # P0-4: оверлей по местам. Плоский ключ pid = правка ПЕРВОГО места
        # (все старые файлы правок валидны); ключ 'pid#slot' — правка
        # конкретного места.
        for i, s in enumerate(p["seats"]):
            key = (p["pid"] if i == 0
                   else f"{p['pid']}#{s.get('slot')}")
            s["employee"] = (moves[key] if key in moves
                             else s["solver_employee"])
            s["moved"] = key in moves
        first = p["seats"][0]
        p["employee"] = first["employee"]
        p["locked"] = p["pid"] in pins
        p["moved"] = any(s["moved"] for s in p["seats"])

    # Self-consistent truncation (audit 2026-07-15): derive the visible crew
    # rows from who CURRENTLY holds pairings, then show ONLY those crews'
    # pairings and validate ONLY over them — so the grid, the pairings tile,
    # the chips and the violation count all describe the SAME population and
    # a violation can never reference a pid/crew the grid cannot render.
    # P0-4: держатель = сотрудник на ЛЮБОМ месте пейринга.
    holders: dict[str, int] = {}
    for p in pairings:
        for s in p["seats"]:
            if s["employee"]:
                holders[s["employee"]] = holders.get(s["employee"], 0) + 1
    holder_ids = sorted(
        holders, key=lambda e: ((crew_by_id.get(e) or {}).get("base") or "",
                                -((crew_by_id.get(e) or {}).get("seniority")
                                  or 0)))
    visible_ids = holder_ids[:MAX_CREW]
    visible = set(visible_ids)
    crew_truncated = len(holder_ids) > MAX_CREW

    shown = [p for p in pairings
             if any(s["employee"] in visible or s["employee"] is None
                    for s in p["seats"])]
    pairings_truncated = len(shown) > MAX_PAIRINGS
    shown = shown[:MAX_PAIRINGS]

    violations = _validate(shown, crew_by_id)

    crew_rows = [{k: (crew_by_id[e]).get(k) for k in (
        "id", "name", "base", "seniority", "role", "type_ratings")}
        for e in visible_ids if e in crew_by_id]
    # full crew list for the reassign picker; P0-4: + skills, чтобы
    # редактор фильтровал кандидатов по требуемой роли места
    crew_all = [{"id": c["id"], "base": c.get("base"),
                 "skills": c.get("skills") or []}
                for c in sorted(crew_by_id.values(),
                                key=lambda c: (c.get("base") or "", c["id"]))]

    pair_out = [{
        "pid": p["pid"], "employee": p["employee"],
        "solver_employee": p["solver_employee"],
        "seats": [{"slot": s.get("slot"),
                   "requiredSkill": s.get("requiredSkill"),
                   "employee": s.get("employee"),
                   "solver_employee": s.get("solver_employee"),
                   "moved": s.get("moved", False)} for s in p["seats"]],
        "locked": p["locked"], "moved": p["moved"],
        "base": p["base"], "nlegs": p["nlegs"],
        "aircraft": p["aircraft"], "days": p["days"],
        "dep_utc": p["dep_utc"], "arr_utc": p["arr_utc"],
        "violations": p["violations"], "legs": p["legs"],
    } for p in shown]

    return {
        "available": True,
        "instance": inst_id,
        "days": days,
        "crew": crew_rows,
        "crew_all": crew_all,
        "crew_truncated": crew_truncated,
        "total_crew": len(holder_ids),
        "pairings": pair_out,
        "pairings_truncated": pairings_truncated,
        "total_pairings": len(pairings),
        "edited": bool(moves) or bool(pins),
        "moves": len(moves),
        "pins": len(pins),
        "violations": violations,
        "violation_count": len(violations),
    }


def set_move(inst_id: str, inst: dict, pid, new_emp, email=None,
             slot=None, reason_code=None, reason_text=None, by=None) -> dict:
    """Reassign a SEAT of pairing `pid` to `new_emp` (None = unassign).
    P0-4: без slot (или slot первого места) правится первое место и ключ
    остаётся плоским pid — все старые файлы правок и вызыватели валидны;
    slot другого места пишет ключ 'pid#slot'. Пин защищает весь пейринг.
    new_emp, равный солверному сотруднику ЭТОГО места, снимает правку.

    Контур «Отклик планировщика» (2026-07-20): reason_code/reason_text/by
    НЕОБЯЗАТЕЛЬНЫ. Эпизод в planner_feedback пишется ТОЛЬКО когда причина
    указана: правка без причины как сигнал бесполезна (по ней не отличить
    нехватку правила от привычки оператора), поэтому автоматические
    вызыватели (fair_share, disruption) причины не передают и журнал
    откликов не засоряют. Эпизод пишется, только если назначение места
    ДЕЙСТВИТЕЛЬНО изменилось (см. ниже)."""
    pairings, crew_by_id, _ = _resolve(inst)
    if pairings is None:
        raise RosterBoardError("instance has no roster to edit")
    pid = str(pid or "")
    # составной ключ 'pid#slot' принимается и напрямую (ручные вызыватели)
    if "#" in pid and slot is None:
        pid, _, s_raw = pid.partition("#")
        try:
            slot = int(s_raw)
        except ValueError:
            raise RosterBoardError(f"bad seat key {s_raw!r}")
    by_pid = {p["pid"]: p for p in pairings}
    if pid not in by_pid:
        raise RosterBoardError(f"unknown pairing {pid!r}")
    seats = by_pid[pid].get("seats") or []
    if slot is not None:
        target = next((s for s in seats if s.get("slot") == slot), None)
        if target is None:
            raise RosterBoardError(f"unknown seat {slot!r} of {pid!r}")
        first = seats[0] if seats else None
        key = pid if (first is not None
                      and target is first) else f"{pid}#{slot}"
        seat_solver = target.get("solver_employee")
    else:
        key = pid
        seat_solver = (seats[0].get("solver_employee") if seats
                       else by_pid[pid]["solver_employee"])
    if new_emp is not None:
        new_emp = str(new_emp)
        if new_emp not in crew_by_id:
            raise RosterBoardError(f"unknown crew {new_emp!r}")
    ed = edits_for(inst_id)
    if pid in set(ed.get("pins") or []):
        raise RosterBoardError("pairing is pinned — unlock it first")
    moves = dict(ed.get("moves") or {})
    # кого правка снимает С МЕСТА: действующее назначение (прежняя ручная
    # правка сильнее солверного), а не всегда решение движка
    prev_emp = moves.get(key, seat_solver) if key in moves else seat_solver
    if new_emp == seat_solver:
        moves.pop(key, None)              # back to the solver assignment
    else:
        moves[key] = new_emp
    ed["moves"] = moves
    if email:
        ed.setdefault("owner", email)
    _store(inst_id, ed)
    if reason_code and new_emp != prev_emp:
        # Эпизод только на РЕАЛЬНОЕ изменение места. Повторное «Применить» с
        # тем же сотрудником — не переделка, а холостое нажатие: считать его
        # накручивало бы размер кластера и в пределе рожало ложный CR из
        # одного щелчка мышью, повторённого нужное число раз.
        #
        # Наблюдение — ПОСЛЕ успешной записи правки и никогда не вместо
        # неё: сбор сигнала не имеет права уронить саму переделку.
        try:
            from . import planner_feedback
            planner_feedback.record(inst_id, pid=key, slot=slot,
                                    frm=prev_emp, to=new_emp,
                                    reason_code=reason_code,
                                    reason_text=reason_text,
                                    by=(by or email))
        except Exception:                                # noqa: BLE001
            pass
    return board(inst_id, inst)


def set_pin(inst_id: str, inst: dict, pid, locked, email=None) -> dict:
    pairings, _crew, _ = _resolve(inst)
    if pairings is None:
        raise RosterBoardError("instance has no roster to edit")
    pid = str(pid or "")
    if pid not in {p["pid"] for p in pairings}:
        raise RosterBoardError(f"unknown pairing {pid!r}")
    ed = edits_for(inst_id)
    pins = set(ed.get("pins") or [])
    if locked:
        pins.add(pid)
    else:
        pins.discard(pid)
    ed["pins"] = sorted(pins)
    if email:
        ed.setdefault("owner", email)
    _store(inst_id, ed)
    return board(inst_id, inst)


def reset(inst_id: str, email: str, is_admin: bool) -> bool:
    """Owner-or-admin discard of all manual edits (restores the solver
    roster). False when nothing stored / not yours."""
    data = _load()
    meta = data.get(inst_id)
    if not isinstance(meta, dict):
        return False
    if not is_admin and meta.get("owner") not in (None, email):
        return False
    with _lock:
        data = _load()
        data.pop(inst_id, None)
        _save_locked(data)
    return True
