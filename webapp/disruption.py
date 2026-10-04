"""Last-minute disruption recovery, slice A (2026-07-16): greedy roster
repair when a crew member falls sick (or specific pairings must be
re-crewed) AFTER pairing/rostering is computed.

Industry framing (airline crew recovery, Lettovský 2000 / Continental
2003): after a disruption the objective flips from optimality to
STABILITY — cover the broken flying with the MINIMUM number of changes
and affected crew, honouring the same hard rules as always. Suboptimal
is fine; fast and legal is mandatory.

This slice:

  - a disruption EVENT is stored per instance (kind "sick": employee +
    inclusive day range). The board resolver overlays sick days onto the
    crew's unavailability, so the interactive board, its validator and
    every consumer (fair-share, cabinet) see the sickness immediately;
  - REPAIR is a deterministic greedy pass over the affected pairings
    (earliest departure first — urgency order):
      1) direct reassignment to a RESERVE (crew with no flying in the
         window; lowest credit first), else any legal crew;
      2) otherwise ONE depth-1 swap: free a candidate by legally moving
         one of their pairings to a third crew;
    legality = the exact board rules (type rating, home base,
    unavailability incl. sickness, overlap, tier-1 inter-pairing rest)
    via fair_share._move_legal, and the final plan is re-checked with
    roster_board._validate — it must strictly reduce the violation count;
  - the plan is APPLIED AS ORDINARY BOARD MOVES in raw execution order
    (every prefix legal), fully reversible with the board's reset. A
    pinned broken pairing is unpinned first (sickness beats a pin) and
    the plan says so honestly;
  - pairings that cannot be covered are reported as `uncovered` — the
    module never hides a hole.

Phase B (same file, section "flight surgery" below): flight-level
events — cancelled flights ("flights"), an aircraft out of service
("aircraft", resolved to its flights via tailNumber) and delays
("delay") — dissolve every pairing that carries an affected leg. The
repair is a NEIGHBOURHOOD REBUILD, not a full re-run:

  1) a DERIVED instance is created (base payload minus cancelled
     flights, delayed legs re-timed, sick days baked into
     unavailableDays) — the untouched world is bit-identical;
  2) stage-1 column generation runs over ONLY the surviving legs of the
     dissolved pairings (a tiny sub-payload — seconds, not hours);
  3) the merged catalog = untouched pairings carried over as FIXED
     columns (their crew frozen via `pinned`) + the new neighbourhood
     pairings — written to the canonical catalog location, so the
     ordinary stage-2/validate/board machinery works unchanged;
  4) crew re-assignment via Timefold (@PlanningPin on untouched slots),
     with the Phase-A greedy repair as an honest fallback when the
     solver jar is unavailable;
  5) a before/after report (broken vs new pairings, crew changes,
     uncovered legs) is published next to the assignment artifact.

The heavy steps run as an ordinary background job (type "repair",
driver _disruption_repair.py) — visible in the jobs feed, stoppable,
logged.

Still NOT here: reserve lines as first-class data, multi-day rolling
horizons.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import rules_config

import _data_paths
from . import registry, roster_board
from .fair_share import _effective_state, _move_legal, _credits_by_crew

DISRUPTIONS_PATH = registry.REPO_ROOT / "webapp" / "config" / "disruptions.json"

MAX_REPAIRS = 100                 # runaway guard per plan (apply rows)
MAX_ALTS = 5                      # отвергнутых вариантов на место в записи
                                  # эпизода: candidates() перебирает ВЕСЬ
                                  # экипаж и на загруженном экземпляре
                                  # вернёт тысячи имён (волна 2)
MAX_SWAP_PROBES = 20000           # legality checks budget for swap search
                                  # (quadratic on upload-scale instances —
                                  # audit 2026-07-16, D2)

_lock = threading.Lock()


class DisruptionError(ValueError):
    """Invalid disruption input/state; message is safe to show."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- store

def _load() -> dict:
    """Defensive read: the store is folded into EVERY board resolve via the
    sickness overlay, so a corrupt byte here must degrade to 'no events',
    never 500 the whole roster stack (audit 2026-07-16, D1)."""
    if not DISRUPTIONS_PATH.exists():
        return {}
    try:
        data = json.loads(DISRUPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    """os.replace onto a file a concurrent thread holds open for read
    fails with PermissionError on Windows (readers lack FILE_SHARE_DELETE)
    — the polled stores are read lock-free, so retry briefly."""
    import time
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(data: dict) -> None:
    DISRUPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DISRUPTIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),  # census-ok: журнал модуля, не набор
                   encoding="utf-8")
    _replace_with_retry(tmp, DISRUPTIONS_PATH)


def events_for(inst_id: str) -> list[dict]:
    rows = _load().get(inst_id) or []
    return [r for r in rows if isinstance(r, dict)]


def _expand_days(day_from: str, day_to: str) -> list[str]:
    try:
        d0, d1 = date.fromisoformat(day_from), date.fromisoformat(day_to)
    except ValueError as exc:
        raise DisruptionError(f"bad date: {exc}") from exc
    if d1 < d0:
        raise DisruptionError("day_to is before day_from")
    if (d1 - d0).days > 62:
        raise DisruptionError("disruption window longer than 62 days")
    return [date.fromordinal(d0.toordinal() + i).isoformat()
            for i in range((d1 - d0).days + 1)]


def sick_days(inst_id: str) -> dict[str, set[str]]:
    """emp -> set of sick days from ACTIVE events of this instance. This is
    the overlay roster_board._resolve folds into crew unavailability."""
    out: dict[str, set[str]] = {}
    for ev in events_for(inst_id):
        if ev.get("kind") != "sick":
            continue
        emp = str(ev.get("employee") or "")
        if not emp:
            continue
        try:
            days = _expand_days(str(ev.get("day_from") or ""),
                                str(ev.get("day_to") or ""))
        except DisruptionError:
            continue                      # malformed row: skip, never 500
        out.setdefault(emp, set()).update(days)
    return out


def add_sick(inst_id: str, inst: dict, employee: str, day_from: str,
             day_to: str, note: str = "", by: str | None = None) -> dict:
    """Register a sickness event. Validates the employee against the
    instance payload; the days are validated by expansion."""
    payload = registry.load_payload(registry.instance_payload_path(inst))
    ids = {str(e.get("id")) for e in payload.get("employees") or []}
    employee = str(employee or "")
    if employee not in ids:
        raise DisruptionError(f"unknown employee {employee!r}")
    _expand_days(day_from, day_to)                 # validate the range
    ev = {"id": "dz" + secrets.token_hex(4), "kind": "sick",
          "employee": employee, "day_from": day_from, "day_to": day_to,
          "note": (note or "")[:200], "created_at": _now(), "by": by}
    with _lock:
        data = _load()
        data.setdefault(inst_id, []).append(ev)
        _save_locked(data)
    return ev


def remove_event(inst_id: str, event_id: str) -> bool:
    with _lock:
        data = _load()
        rows = data.get(inst_id) or []
        keep = [r for r in rows if r.get("id") != event_id]
        if len(keep) == len(rows):
            return False
        if keep:
            data[inst_id] = keep
        else:
            data.pop(inst_id, None)
        _save_locked(data)
    return True


# ------------------------------------------------------------------ impact

def _pairing_seats(p) -> list[dict]:
    """Места пейринга (посадочная модель P0-4); хэндовый пейринг без seats
    получает синтетическое первое место."""
    return p.get("seats") or [
        {"slot": 0, "requiredSkill": None, "employee": p.get("employee"),
         "solver_employee": p.get("solver_employee")}]


def _seat_broken(p, s, sick: dict[str, set[str]], extra_pids: set[str],
                 pdays: set[str]) -> bool:
    emp = s.get("employee")
    return (p["pid"] in extra_pids
            or emp is None
            or (emp in sick and bool(sick[emp] & pdays)))


def _broken_seats(pairings, sick: dict[str, set[str]],
                  extra_pids: set[str]) -> list[tuple]:
    """(pairing, seat) — МЕСТА, требующие переэкипировки: сотрудник места
    болен в любой день пейринга, место пусто, или пейринг явно назван.
    P0-4 (2026-07-20): фаза A видит болезнь на ЛЮБОМ месте, не только на
    первом. Порядок срочности — по вылету, затем по слоту."""
    out: list[tuple] = []
    for p in pairings:
        pdays = set(p["days"])
        for s in _pairing_seats(p):
            if _seat_broken(p, s, sick, extra_pids, pdays):
                out.append((p, s))
    out.sort(key=lambda ps: (ps[0]["dep_utc"], ps[0]["pid"],
                             ps[1].get("slot") if ps[1].get("slot")
                             is not None else 0))
    return out


def _broken_pairings(pairings, sick: dict[str, set[str]],
                     extra_pids: set[str]) -> list[dict]:
    """Pairings with at least one broken seat (для отображения ущерба).
    Порядок срочности (earliest departure first)."""
    seen: dict = {}
    for p, _s in _broken_seats(pairings, sick, extra_pids):
        seen.setdefault(p["pid"], p)
    broken = list(seen.values())
    broken.sort(key=lambda p: (p["dep_utc"], p["pid"]))
    return broken


def impact(inst_id: str, inst: dict) -> dict:
    """Current damage picture; no writes."""
    state = _effective_state(inst_id, inst)
    if state is None:
        return {"available": False, "reason": "no_roster",
                "events": events_for(inst_id)}
    pairings, crew_by_id, _pay = state
    sick = sick_days(inst_id)
    for emp, days in sick.items():
        if emp in crew_by_id:
            crew_by_id[emp]["unavailable"] |= days
    broken = _broken_pairings(pairings, sick, set())
    out = {
        "available": True,
        "events": events_for(inst_id),
        "broken": [{"pid": p["pid"], "employee": p.get("employee"),
                    "dep_utc": p["dep_utc"], "arr_utc": p["arr_utc"],
                    "days": p["days"], "credit_h": round(p["credit_h"], 1),
                    "pinned": p["locked"]} for p in broken],
    }
    # Phase B: the flight-surgery 'before' picture + last rebuild status,
    # so the panel needs a single GET. Defensive: a missing catalog must
    # not break the sickness view.
    try:
        out["surgery"] = surgery_preview(inst_id, inst)
    except DisruptionError as exc:
        out["surgery"] = {"active": True, "error": str(exc)}
    out["rebuild"] = rebuild_status(inst_id)
    # Трек occurrence-идентичности: подсказка вхождений для повторных
    # номеров — форма событий адресует конкретное вхождение (flightLegId),
    # UI показывает «номер · дата время» вместо голого legId.
    try:
        payload = _payload_for(inst)
        from _leg_identity import fn_counts
        counts = fn_counts(payload)
        occ = []
        for f in payload.get("flights") or []:
            fn = str(f.get("flightNumber"))
            if counts.get(fn, 0) > 1 and f.get("flightLegId"):
                occ.append({"addr": str(f["flightLegId"]), "fn": fn,
                            "dep_utc": f.get("departureUTCDateTime")})
        if occ:
            out["occurrences"] = occ
    except Exception:   # noqa: BLE001 — подсказка не должна ронять панель
        pass
    return out


# ------------------------------------------------------------------ repair

def build_repair_plan(inst_id: str, inst: dict,
                      extra_pids: list[str] | None = None,
                      frozen_pids: set[str] | None = None) -> dict:
    """Deterministic greedy repair; no writes. Objective: cover every
    broken pairing with the minimum number of changes (direct first, then
    one depth-1 swap), reserves preferred, credit-fairness as tiebreak.

    frozen_pids (Phase-B greedy fallback): pairings the merged repair
    catalog promised were frozen with their crew. They are treated as
    locked ON TOP of the board pins, so the depth-1 swap search can never
    move them (audit 2026-07-16: catalog pins were not materialized as
    board pins, letting swaps re-crew 'frozen' pairings)."""
    state = _effective_state(inst_id, inst)
    if state is None:
        return {"available": False, "reason": "no_roster"}
    pairings, crew_by_id, _pay = state
    if frozen_pids:
        for p in pairings:
            if p["pid"] in frozen_pids:
                p["locked"] = True
    sick = sick_days(inst_id)
    for emp, days in sick.items():
        if emp in crew_by_id:
            crew_by_id[emp]["unavailable"] |= days
    rest_min = rules_config.effective().get("min_rest_min", 600)

    base_viol = len(roster_board._validate(
        [dict(p) for p in pairings], crew_by_id))

    # P0-4 (2026-07-20): занятость — по ВСЕМ местам (второе место тоже
    # держит крыло в расписании сотрудника). Ревью хвоста: каждый пейринг
    # входит в список сотрудника РОВНО ОДИН раз (множество держателей) —
    # иначе dup_crew-состояние (один экипаж на двух местах) оставляло бы
    # дубль в by_emp, и `.remove(p)` в commit убирал лишь одно вхождение.
    by_emp: dict[str, list] = {}
    for p in pairings:
        for emp in {s.get("employee") for s in _pairing_seats(p)} - {None}:
            by_emp.setdefault(emp, []).append(p)
    credit = _credits_by_crew(pairings, crew_by_id)

    targets = _broken_seats(pairings, sick, set(extra_pids or []))
    broken = _broken_pairings(pairings, sick, set(extra_pids or []))
    broken_pids = {p["pid"] for p in broken}
    window_days: set[str] = set()
    for p, _s in targets:
        window_days.update(p["days"])

    def _holds(emp, pr) -> bool:
        return any(s.get("employee") == emp for s in _pairing_seats(pr))

    def _seat_key(p, seat) -> str:
        seats = p.get("seats") or []
        return (p["pid"] if (seats and seat is seats[0])
                else f"{p['pid']}#{seat.get('slot')}")

    def candidates(p, seat) -> list[str]:
        """Legal targets for THIS seat, reserves first, then lowest credit;
        сотрудники на других местах пейринга исключены (dup_crew)."""
        rs = seat.get("requiredSkill")
        held = {s.get("employee") for s in _pairing_seats(p) if s is not seat}
        res, active = [], []
        for cid in crew_by_id:
            if cid == seat.get("employee") or cid in held:
                continue
            if not _move_legal(p, cid, crew_by_id, by_emp, rest_min,
                               required_skill=rs):
                continue
            busy = any(set(q["days"]) & window_days
                       for q in by_emp.get(cid, []))
            (active if busy else res).append(cid)
        res.sort(key=lambda c: (credit[c], c))
        active.sort(key=lambda c: (credit[c], c))
        return res + active

    def commit(p, seat, to):
        frm = seat.get("employee")
        seat["employee"] = to
        seats = p.get("seats") or []
        if seats and seat is seats[0]:
            p["employee"] = to
        if p not in by_emp.setdefault(to, []):
            by_emp[to].append(p)
        credit[to] = credit.get(to, 0.0) + p["credit_h"]
        # прежний держатель теряет крыло и кредит, только если больше НЕ
        # сидит на этом пейринге (другое место того же экипажа)
        if frm and not _holds(frm, p):
            if p in by_emp.get(frm, []):
                by_emp[frm].remove(p)
            if frm in credit:
                credit[frm] -= p["credit_h"]

    repairs: list[dict] = []
    apply_sequence: list[dict] = []
    uncovered: list[str] = []
    swap_probes = 0                  # shared legality-check budget (D2)

    def situation_key(p, seat) -> str:
        """Стабильный ключ СИТУАЦИИ ремонта — по содержимому пейринга.

        Намеренно НЕ pid: тот — позиционный индекс каталога (`C_000004`),
        он перенумеровывается при каждой пересборке, и группировка по нему
        склеила бы неродственные пейринги разных прогонов — ровно та
        ошибка, что уже случилась с кластерами волны 1. Экземпляр входит в
        ключ по той же причине: табельные номера у разных авиакомпаний
        совпадают сплошь."""
        legs = "|".join(
            f"{leg.get('fn')}@{leg.get('dep_utc')}"
            for leg in (p.get("legs") or []))
        raw = f"{inst_id}\x1f{legs}\x1f{seat.get('slot')}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    for p, seat in targets:
        # a swap appends TWO rows: reserve room for both (audit D4)
        if len(apply_sequence) + 2 > MAX_REPAIRS:
            uncovered.append(_seat_key(p, seat))
            continue
        cands = candidates(p, seat)
        if cands:
            to = cands[0]
            repairs.append({"pid": p["pid"], "slot": seat.get("slot"),
                            "from": seat.get("employee"),
                            "to": to, "via": "direct",
                            # ОТВЕРГНУТЫЕ варианты. Полный ранжированный
                            # список считался и тут же выбрасывался на
                            # каждом ремонте — а это единственное место,
                            # где видно, ЧТО планировщику предлагали
                            # помимо принятого, и восполнить его задним
                            # числом нельзя. Ограничен: candidates()
                            # перебирает весь экипаж и на загруженном
                            # экземпляре вернёт тысячи.
                            "alts": cands[1:1 + MAX_ALTS],
                            "alts_total": len(cands) - 1,
                            "skey": situation_key(p, seat),
                            "credit_h": round(p["credit_h"], 1),
                            "unpin": p["locked"]})
            apply_sequence.append({"pid": p["pid"], "slot": seat.get("slot"),
                                   "to": to, "unpin": p["locked"]})
            commit(p, seat, to)
            continue
        # depth-1 swap: free a crew member by legally moving ONE of their
        # (unpinned, unbroken) pairings to a third crew. The search is
        # quadratic in crew size, so it runs on a shared PROBE BUDGET —
        # exhausted budget degrades to honest `uncovered`, never to a
        # minutes-long request (audit 2026-07-16, D2).
        rs = seat.get("requiredSkill")
        held = {s.get("employee") for s in _pairing_seats(p) if s is not seat}
        swap_done = False
        for c in sorted(crew_by_id, key=lambda c: (credit.get(c, 0.0), c)):
            if (c == seat.get("employee") or c in held
                    or swap_probes >= MAX_SWAP_PROBES):
                continue
            for q in sorted(by_emp.get(c, []),
                            key=lambda q: (q["credit_h"], q["pid"])):
                if q["locked"] or q["pid"] in broken_pids:
                    continue
                q_seat = next((s for s in _pairing_seats(q)
                               if s.get("employee") == c), None)
                if q_seat is None:
                    continue
                for r in sorted(crew_by_id,
                                key=lambda r: (credit.get(r, 0.0), r)):
                    if r in (c, seat.get("employee")):
                        continue
                    swap_probes += 1
                    if swap_probes >= MAX_SWAP_PROBES:
                        break
                    if not _move_legal(q, r, crew_by_id, by_emp, rest_min,
                                       required_skill=q_seat.get(
                                           "requiredSkill")):
                        continue
                    # simulate q leaving c, then test p's seat -> c
                    by_emp[c].remove(q)
                    ok = _move_legal(p, c, crew_by_id, by_emp, rest_min,
                                     required_skill=rs)
                    by_emp[c].append(q)
                    if not ok:
                        continue
                    # commit both, q first (its prefix must stay legal)
                    commit(q, q_seat, r)
                    apply_sequence.append({"pid": q["pid"],
                                           "slot": q_seat.get("slot"),
                                           "to": r, "unpin": False})
                    repairs.append({"pid": q["pid"],
                                    "slot": q_seat.get("slot"),
                                    "from": c, "to": r, "via": "swap_out",
                                    "credit_h": round(q["credit_h"], 1),
                                    "unpin": False})
                    frm_seat = seat.get("employee")
                    commit(p, seat, c)
                    apply_sequence.append({"pid": p["pid"],
                                           "slot": seat.get("slot"),
                                           "to": c, "unpin": p["locked"]})
                    # У обмена своего ранжированного списка нет: сюда
                    # доходят только места, для которых ПРЯМОЙ кандидат не
                    # нашёлся вовсе. Пустой alts здесь — это факт («выбора
                    # не было»), а не потеря данных.
                    repairs.append({"pid": p["pid"], "slot": seat.get("slot"),
                                    "from": frm_seat, "to": c,
                                    "via": "swap_in", "alts": [],
                                    "alts_total": 0,
                                    "skey": situation_key(p, seat),
                                    "credit_h": round(p["credit_h"], 1),
                                    "unpin": p["locked"]})
                    swap_done = True
                    break
                if swap_done:
                    break
            if swap_done:
                break
        if not swap_done:
            uncovered.append(_seat_key(p, seat))

    after_viol = len(roster_board._validate(
        [dict(p) for p in pairings], crew_by_id))
    if after_viol > base_viol:
        raise DisruptionError(
            f"repair produced violations ({base_viol} -> {after_viol})"
            " — plan discarded")

    affected = {m["to"] for m in repairs} | {
        m["from"] for m in repairs if m["from"]}
    return {
        "available": True,
        "instance": inst_id,
        "events": events_for(inst_id),
        "broken_count": len(broken),
        "repairs": repairs,
        "apply_sequence": apply_sequence,
        "uncovered": uncovered,
        "changes_count": len(apply_sequence),
        "affected_crew": len(affected),
        "violations_before": base_viol,
        "violations_after": after_viol,
    }


def apply_repair(inst_id: str, inst: dict, email: str | None = None,
                 extra_pids: list[str] | None = None) -> dict:
    """Recompute the plan on the CURRENT state and write it as ordinary
    board moves in RAW order (every prefix legal). A pinned broken pairing
    is unpinned first — sickness beats a pin, and the plan reports it."""
    plan = build_repair_plan(inst_id, inst, extra_pids)
    if not plan.get("available"):
        return plan
    applied = 0
    for mv in plan["apply_sequence"]:
        try:
            if mv.get("unpin"):
                roster_board.set_pin(inst_id, inst, mv["pid"], False, email)
            # P0-4 (2026-07-20): правка адресует конкретное место (slot);
            # None/первое место пишут плоский ключ pid (совместимость).
            roster_board.set_move(inst_id, inst, mv["pid"], mv["to"], email,
                                  slot=mv.get("slot"))
            applied += 1
        except roster_board.RosterBoardError:
            break                    # applied prefix is legal + reversible
    plan["applied_moves"] = applied
    # Наблюдение волны 2: что предлагалось помимо принятого. Пишем ТОЛЬКО
    # применённый префикс — отвергнутый целиком план человек не выбирал.
    # Сбой журнала не должен рушить сам ремонт: ремонт уже произошёл и
    # обратим кнопкой сброса доски, а потерянная запись — меньшее зло.
    try:
        from . import repair_episodes
        repair_episodes.record_plan(inst_id, plan, by=email, applied=applied)
    except Exception:                                    # noqa: BLE001
        pass
    return plan


# ==================================================== Phase B: flight surgery

MAX_EVENT_FNS = 200               # cancelled/delayed flights per event
MIN_SHIFT_MIN, MAX_SHIFT_MIN = -1440, 2880   # delay window: -24h .. +48h

REPAIRS_PATH = registry.REPO_ROOT / "webapp" / "config" / "repairs.json"
REPAIR_RUNS = _data_paths.results_dir(create=False) / "runs" / "repair"

REBUILD_BUDGET_DEFAULT = 120      # stage-2 seconds; neighbourhood colgen
REBUILD_BUDGET_MAX = 900          # is budgeted separately inside the driver


def _payload_for(inst: dict) -> dict:
    return registry.load_payload(registry.instance_payload_path(inst))


def _known_fns(payload: dict) -> set[str]:
    return {str(f.get("flightNumber"))
            for f in payload.get("flights") or []}


def _addr_index(payload: dict) -> dict:
    """addr -> flight (см. _leg_identity.build_addr_index): уникальный
    номер ИЛИ flightLegId. Единственный резолвер адресов событий."""
    from _leg_identity import GeradPipelineUnsupported, build_addr_index
    try:
        return build_addr_index(payload)
    except GeradPipelineUnsupported as exc:
        raise DisruptionError("; ".join(exc.reasons)) from exc


def _clean_fns(fns, payload: dict) -> list[str]:
    """Normalized, validated, deduplicated ADDRESS list.

    Трек occurrence-идентичности (2026-07-19): строка адресует КОНКРЕТНОЕ
    вхождение — уникальный flightNumber (как всегда) либо flightLegId для
    повторного номера. Голый повторный номер раньше проходил валидацию и
    «отмена F100» тихо гасила ОБА вхождения — теперь это громкий отказ."""
    if isinstance(fns, str):
        fns = [x for x in fns.replace(";", ",").split(",")]
    cleaned = sorted({str(x).strip() for x in (fns or []) if str(x).strip()})
    if not cleaned:
        raise DisruptionError("no flights listed")
    if len(cleaned) > MAX_EVENT_FNS:
        raise DisruptionError(f"more than {MAX_EVENT_FNS} flights in one event")
    index = _addr_index(payload)
    known = _known_fns(payload)
    unknown, ambiguous = [], []
    from _leg_identity import addr_of, fn_counts
    counts = fn_counts(payload)
    resolved: list[str] = []
    for fn in cleaned:
        flight = index.get(fn)
        if flight is None:
            (ambiguous if fn in known else unknown).append(fn)
            continue
        # канонизация: событие хранит КАНОНИЧЕСКИЙ адрес (уникальный номер
        # либо flightLegId повторного) — даже если пользователь ввёл
        # flightLegId уникального рейса
        resolved.append(addr_of(flight, counts) or fn)
    if ambiguous:
        raise DisruptionError(
            "номер летает несколько раз — укажите конкретное вхождение "
            "(flightLegId): " + ", ".join(ambiguous[:5]))
    if unknown:
        raise DisruptionError(
            "unknown flights: " + ", ".join(unknown[:5])
            + ("…" if len(unknown) > 5 else ""))
    return sorted(set(resolved))


def _append_event(inst_id: str, ev: dict) -> dict:
    with _lock:
        data = _load()
        data.setdefault(inst_id, []).append(ev)
        _save_locked(data)
    return ev


def add_flights(inst_id: str, inst: dict, fns, note: str = "",
                by: str | None = None, source: str | None = None) -> dict:
    """Cancelled-flights event: the listed legs are removed from service.
    `source` (optional) is a machine tag for programmatic callers (the ops
    plan-fact auto-generator) so they can find/clear their own events
    without relying on the user-writable note."""
    cleaned = _clean_fns(fns, _payload_for(inst))
    return _append_event(inst_id, {
        "id": "dz" + secrets.token_hex(4), "kind": "flights",
        "fns": cleaned, "note": (note or "")[:200], "source": source,
        "created_at": _now(), "by": by})


def add_aircraft(inst_id: str, inst: dict, tail: str, day_from: str,
                 day_to: str, note: str = "", by: str | None = None) -> dict:
    """Aircraft-out-of-service event: resolved to the tail's flights inside
    the inclusive day window AT EVENT TIME (the snapshot is stored, so the
    preview and the rebuild always agree on what is cancelled)."""
    payload = _payload_for(inst)
    tail = str(tail or "").strip()
    if not tail:
        raise DisruptionError("tail number is required")
    days = set(_expand_days(day_from, day_to))
    tails_seen = False
    fns = []
    from _leg_identity import addr_of, fn_counts
    _counts = fn_counts(payload)
    for f in payload.get("flights") or []:
        t = str(f.get("tailNumber") or "").strip()
        if t:
            tails_seen = True
        if t != tail:
            continue
        dep = str(f.get("departureUTCDateTime") or "")[:10]
        if dep in days:
            # снапшот в addr-пространстве: конкретное вхождение, не номер
            addr = addr_of(f, _counts)
            if addr is None:
                raise DisruptionError(
                    f"повторный номер {f.get('flightNumber')!r} без "
                    f"flightLegId — вхождение не адресуемо")
            fns.append(addr)
    if not tails_seen:
        raise DisruptionError(
            "this payload carries no tailNumber data — use a "
            "cancelled-flights event instead")
    if not fns:
        raise DisruptionError(
            f"tail {tail!r} has no flights in {day_from}..{day_to}")
    return _append_event(inst_id, {
        "id": "dz" + secrets.token_hex(4), "kind": "aircraft",
        "tail": tail, "day_from": day_from, "day_to": day_to,
        "fns": sorted(fns), "note": (note or "")[:200],
        "created_at": _now(), "by": by})


def add_delay(inst_id: str, inst: dict, fns, shift_min: int, note: str = "",
              by: str | None = None, source: str | None = None) -> dict:
    """Delay/shift event: the listed legs move by shift_min minutes.
    `source` (optional) is a machine tag for programmatic callers."""
    try:
        shift = int(shift_min)
    except (TypeError, ValueError) as exc:
        raise DisruptionError("shift_min must be an integer") from exc
    if shift == 0 or not (MIN_SHIFT_MIN <= shift <= MAX_SHIFT_MIN):
        raise DisruptionError(
            f"shift_min must be non-zero within "
            f"{MIN_SHIFT_MIN}..{MAX_SHIFT_MIN}")
    cleaned = _clean_fns(fns, _payload_for(inst))
    return _append_event(inst_id, {
        "id": "dz" + secrets.token_hex(4), "kind": "delay",
        "fns": cleaned, "shift_min": shift, "note": (note or "")[:200],
        "source": source, "created_at": _now(), "by": by})


def flight_disruptions(inst_id: str) -> tuple[set[str], dict[str, int]]:
    """(cancelled fns, delayed fn -> shift minutes) from ACTIVE events.
    Later delay events win per flight; cancellation beats delay."""
    cancelled: set[str] = set()
    delayed: dict[str, int] = {}
    for ev in events_for(inst_id):
        kind = ev.get("kind")
        if kind in ("flights", "aircraft"):
            cancelled.update(str(x) for x in ev.get("fns") or [])
        elif kind == "delay":
            try:
                shift = int(ev.get("shift_min"))
            except (TypeError, ValueError):
                continue
            for fn in ev.get("fns") or []:
                delayed[str(fn)] = shift
    for fn in cancelled:
        delayed.pop(fn, None)
    return cancelled, delayed


# ------------------------------------------------------- catalog surgery

def _catalog_doc(inst: dict) -> dict:
    """The stage-1 catalog document; its 'selected' order DEFINES the
    board's positional pids C_000000..."""
    rel = inst.get("catalog_path")
    if not rel:
        raise DisruptionError(
            "no stage-1 pairing catalog for this instance — run stage 1 "
            "before a rebuild")
    try:
        doc = json.loads(registry.resolve_rel(rel).read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DisruptionError(f"catalog unreadable: {exc}") from exc
    if not isinstance(doc.get("selected"), list):
        raise DisruptionError("catalog has no 'selected' records")
    return doc


def _catalog_records(inst: dict) -> list[dict]:
    """Validated catalog records. Non-dict entries (a truncated or
    hand-recovered dump) are dropped, not 500-ed — same D1 rule as the
    events store: corruption degrades, never kills the panel."""
    return [r for r in _catalog_doc(inst)["selected"] if isinstance(r, dict)]


def _check_catalog_matches_roster(records: list[dict],
                                  by_pid: dict[str, dict]) -> None:
    """The positional pid join is only sound when the stage-2 artifact was
    produced FROM this catalog. A stage-1 re-run after the last stage-2
    silently shifts the mapping (promotion rewrites the catalog in place),
    and freezing crews onto pairings they never flew must be refused, not
    published (audit 2026-07-16, Phase B round 1). Both sides carry fns —
    compare them."""
    for i, rec in enumerate(records):
        p = by_pid.get(f"C_{i:06d}")
        if p is None:
            continue
        art_fns = {leg.get("fn") for leg in p.get("legs") or []}
        if art_fns and art_fns != {str(x) for x in rec.get("fns") or []}:
            raise DisruptionError(
                "the stage-1 catalog was rebuilt after the last stage-2 "
                "run — the roster no longer matches it. Re-run stage 2, "
                "then rebuild")


def _stage1_lane(inst: dict, doc: dict) -> tuple[str, str | None]:
    """(profile, bases) for the neighbourhood stage 1 — the SAME lane the
    base catalog was built with. The catalog document itself is the source
    of truth (colgen dumps both keys; merged repair catalogs carry them
    too, so repair-of-repair inherits the lane). Fallback for catalogs
    predating the keys mirrors jobs._build_commands: scenarios resolve to
    their non-scenario ROOT (audit 2026-07-16, Phase B round 1)."""
    profile = doc.get("profile")
    if profile in ("gerad", "v2"):
        bases = doc.get("bases")
        return profile, (",".join(str(b) for b in bases) if bases else None)
    is_gerad = inst.get("source") == "gerad"
    if not is_gerad:
        from . import scenarios
        root = scenarios.root_base_instance(inst.get("id") or "")
        is_gerad = bool(root) and root.get("source") == "gerad"
    if is_gerad:
        return "gerad", "BASE1,BASE2,BASE3"
    return "v2", None


def split_catalog(records: list[dict], affected: set[str]) -> tuple[list, list]:
    """(untouched, broken) catalog indexes. A pairing is broken when ANY of
    its legs — owned or deadhead — is cancelled or re-timed: a deadhead on
    a cancelled leg breaks the pairing's ground trajectory just as surely
    as a flying leg."""
    untouched, broken = [], []
    for i, rec in enumerate(records):
        fns = set(str(x) for x in rec.get("fns") or [])
        (broken if fns & affected else untouched).append(i)
    return untouched, broken


def split_catalog_addr(records: list[dict], affected: set[str],
                       wire_map: dict | None) -> tuple[list, list]:
    """split_catalog в addr-пространстве: fns записей каталога могут быть
    wire-именами re-key (F100@1) — переводятся в flightLegId перед
    пересечением с addr-множеством события (трек occurrence-идентичности).
    Для каталогов без wire_map — прежнее поведение байт-в-байт."""
    from _leg_rekey import wire_to_leg_ids
    untouched, broken = [], []
    for i, rec in enumerate(records):
        fns = wire_to_leg_ids([str(x) for x in rec.get("fns") or []],
                              wire_map or {})
        (broken if fns & affected else untouched).append(i)
    return untouched, broken


def _validate_event_addrs(payload: dict, cancelled, delayed) -> None:
    """Ревью пакета: сохранённое событие могло адресовать номер, который
    ПОСЛЕ дельты стал повторным (или рейс исчез) — тихий no-op запрещён,
    пользователь пересоздаёт событие с конкретным вхождением."""
    index = _addr_index(payload)
    bad = sorted(a for a in (set(cancelled) | set(delayed))
                 if a not in index)
    if bad:
        raise DisruptionError(
            "события адресуют несуществующие или неоднозначные рейсы "
            "(после дельты номер мог стать повторным) — пересоздайте "
            "события: " + ", ".join(bad[:5]))


def surgery_preview(inst_id: str, inst: dict) -> dict:
    """The 'before' picture for flight events; no writes, cheap."""
    cancelled, delayed = flight_disruptions(inst_id)
    if not cancelled and not delayed:
        return {"active": False}
    _validate_event_addrs(_payload_for(inst), cancelled, delayed)
    records = _catalog_records(inst)
    # Каталог повторного payload'а живёт в wire-пространстве — его штамп
    # wire_map переводит fns в addr для пересечения с событиями.
    from .costing import _catalog_doc
    from _leg_rekey import wire_to_leg_ids
    wire_map = (_catalog_doc(inst) or {}).get("wire_map") or {}
    affected = cancelled | set(delayed)
    untouched, broken = split_catalog_addr(records, affected, wire_map)
    recover: set[str] = set()
    for i in broken:
        rec = records[i]
        active = rec.get("active_fns")
        active = rec.get("fns") if active is None else active
        recover.update(wire_to_leg_ids([str(x) for x in active or []],
                                       wire_map))
    recover -= cancelled
    state = _effective_state(inst_id, inst)
    emp_by_pid = {}
    if state:
        by_pid = {p["pid"]: p for p in state[0]}
        _check_catalog_matches_roster(records, by_pid)
        emp_by_pid = {pid: p.get("employee") for pid, p in by_pid.items()}
    return {
        "active": True,
        "cancelled": sorted(cancelled),
        "delayed": {fn: delayed[fn] for fn in sorted(delayed)},
        "pairings_total": len(records),
        "untouched_count": len(untouched),
        "broken": [{"pid": f"C_{i:06d}",
                    "employee": emp_by_pid.get(f"C_{i:06d}"),
                    "legs": len(records[i].get("fns") or [])}
                   for i in broken],
        "recover_legs": len(recover),
    }


# ------------------------------------------------------- derived instance

def _shift_iso(ts: str, shift_min: int) -> str:
    from datetime import timedelta
    dt = datetime.fromisoformat(ts)
    return (dt + timedelta(minutes=shift_min)).isoformat()


def build_repair_payload(payload: dict, cancelled: set[str],
                         delayed: dict[str, int],
                         sick: dict[str, set[str]]) -> dict:
    """The derived payload: cancelled flights removed, delayed flights
    re-timed, sick days baked into unavailableDays. Everything else —
    employees, airports, untouched flights — bit-identical."""
    import copy
    from _leg_identity import addr_of, fn_counts
    out = copy.deepcopy(payload)
    _counts = fn_counts(out)
    # Трек occurrence-идентичности: сопоставление по addr (уникальный номер
    # или flightLegId) — отмена одного вхождения не трогает второе.
    flights = []
    cancelled_leg_ids: set = set()
    for f in out.get("flights") or []:
        addr = addr_of(f, _counts)
        fn = str(f.get("flightNumber"))
        if addr in cancelled:
            lid = f.get("flightLegId")
            if isinstance(lid, str) and lid:
                cancelled_leg_ids.add(lid)
            continue
        if addr in delayed:
            try:
                f["departureUTCDateTime"] = _shift_iso(
                    f["departureUTCDateTime"], delayed[addr])
                f["arrivalUTCDateTime"] = _shift_iso(
                    f["arrivalUTCDateTime"], delayed[addr])
            except (KeyError, ValueError) as exc:
                raise DisruptionError(
                    f"flight {fn} has unparseable timestamps: {exc}") from exc
        flights.append(f)
    if not flights:
        raise DisruptionError("every flight is cancelled — nothing to repair")
    out["flights"] = flights

    def _slot_cancelled(a: dict) -> bool:
        lid = a.get("flightLegId")
        if isinstance(lid, str) and lid:
            return lid in cancelled or lid in cancelled_leg_ids
        return str(a.get("flight")) in cancelled

    out["flightAssignments"] = [
        a for a in out.get("flightAssignments") or []
        if not (isinstance(a, dict) and _slot_cancelled(a))]
    for e in out.get("employees") or []:
        eid = str(e.get("id"))
        if eid in sick:
            e["unavailableDays"] = sorted(
                set(e.get("unavailableDays") or []) | sick[eid])
    prov = out.get("provenance")
    out["provenance"] = {
        **(prov if isinstance(prov, dict) else {}),
        "adapter": "disruption-repair",
        "convertedAt": _now(),
    }
    return out


# ------------------------------------------------------------ rebuild job

def _load_repairs() -> dict:
    if not REPAIRS_PATH.exists():
        return {}
    try:
        data = json.loads(REPAIRS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_repairs_locked(data: dict) -> None:
    REPAIRS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPAIRS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),  # census-ok: журнал модуля, не набор
                   encoding="utf-8")
    _replace_with_retry(tmp, REPAIRS_PATH)


def _remove_derived_repair(derived_id: str, derived_label: str) -> None:
    """Best-effort cleanup of a superseded repair instance + artifacts +
    per-instance rows (board edits, disruption events) that would
    otherwise become permanent ghosts once the payload is gone."""
    if not derived_id:
        return
    di = registry.get_instance(derived_id)
    if di is not None:
        try:
            registry.instance_payload_path(di).unlink(missing_ok=True)
        except OSError:
            pass
    registry.remove_owner(derived_id)
    registry.remove_llm_scope(derived_id)
    try:                                  # board edits of the derived board
        roster_board.reset(derived_id, None, True)
    except Exception:                     # noqa: BLE001 — cleanup only
        pass
    data = _load()
    if derived_id in data:                # its own disruption events
        data.pop(derived_id, None)
        _save_locked(data)
    if derived_label:
        for p in (
            registry.FINAL_REPORT
                / f"gerad_colgen_selected_{derived_label}.json",
            registry.FINAL_REPORT
                / f"gerad_stage2_assignment_{derived_label}.json",
            registry.FINAL_REPORT / f"gerad_repair_report_{derived_label}.json",
            _data_paths.gerad_data_dir()
                / f"p2_two_stage_solution_{derived_label}.json",
            # P0-1 (ревью 2026-07-20): указатель активного ростера метки
            registry.FINAL_REPORT / f"active_roster_{derived_label}.json",
        ):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        # P0-1: пер-запусковые манифесты и диагностические копии метки —
        # без уборки диск рос бы неограниченно призраками derived-меток
        import re as _re
        import shutil as _sh
        _sh.rmtree(registry.FINAL_REPORT / "runs" / "roster-manifests"
                   / derived_label, ignore_errors=True)
        _diag = registry.FINAL_REPORT / "runs" / "stage2-diagnostics"
        _tail = _re.compile(r"^\d{8}-\d{6}-(assignment|solution)\.json$")
        try:
            for p in _diag.glob(f"{derived_label}-*"):
                if _tail.match(p.name[len(derived_label) + 1:]):
                    p.unlink(missing_ok=True)
        except OSError:
            pass


PENDING_CLAIM_TTL_S = 600     # a pending claim older than this is stale
                              # (crashed preparation) and may be replaced


def prepare_rebuild(inst_id: str, inst: dict, email: str,
                    budget_s: int = REBUILD_BUDGET_DEFAULT) -> dict:
    """Create the derived repair instance and launch the rebuild job.

    Split the catalog around the affected legs, freeze the untouched
    pairings with their EFFECTIVE crew (board moves included; a crew that
    became unavailable — sick overlay or payload — is NOT frozen, so the
    solver re-crews their pairing too), and hand the heavy steps to the
    background driver. Replaces any prior repair of this instance.

    Ordering hardened by the Phase-B audit (2026-07-16): the repair slot
    is CLAIMED under the lock (pending entry in the repairs registry)
    before any file is created — a rejected double-submit leaves zero
    orphans, and two concurrent submits cannot both pass the check and
    then delete each other's derived payload mid-run. Every failure after
    the claim rolls the claim and the derived instance back."""
    from . import jobs

    try:
        budget_s = int(budget_s or REBUILD_BUDGET_DEFAULT)
    except (TypeError, ValueError) as exc:
        raise DisruptionError("budget_s must be an integer") from exc
    budget_s = max(30, min(budget_s, REBUILD_BUDGET_MAX))
    cancelled, delayed = flight_disruptions(inst_id)
    if not cancelled and not delayed:
        raise DisruptionError(
            "no flight events — add a cancelled-flights, aircraft or "
            "delay event first")
    doc = _catalog_doc(inst)
    records = [r for r in doc["selected"] if isinstance(r, dict)]
    state = _effective_state(inst_id, inst)
    if state is None:
        raise DisruptionError("no roster to repair — run stage 2 first")
    # Трек occurrence-идентичности: ранний гард ДО claim'а — повторные
    # номера решаемы, только если биекция строится (иначе — отказ без
    # создания сирот); meta живёт в addr-пространстве, каталог — в wire.
    payload_probe = _payload_for(inst)
    from _leg_rekey import needs_rekey, rekey_blockers
    if needs_rekey(payload_probe):
        blockers = rekey_blockers(payload_probe)
        if blockers:
            raise DisruptionError(
                "повторные номера не адресуемы: " + "; ".join(blockers[:3]))
    _validate_event_addrs(payload_probe, cancelled, delayed)
    wire_map = (doc or {}).get("wire_map") or {}

    def _rec_addr(fns) -> list:
        # перевод wire->addr С СОХРАНЕНИЕМ ПОРЯДКА: fns пейринга —
        # хронологический маршрут, сортировка сломала бы его
        out = []
        for x in (fns or []):
            rec = wire_map.get(str(x))
            out.append(rec["flightLegId"] if rec else str(x))
        return out

    pairings, crew_by_id, _pay = state
    sick = sick_days(inst_id)
    for emp, days in sick.items():
        if emp in crew_by_id:
            crew_by_id[emp]["unavailable"] |= days
    by_pid = {p["pid"]: p for p in pairings}
    _check_catalog_matches_roster(records, by_pid)

    affected = cancelled | set(delayed)
    untouched_idx, broken_idx = split_catalog_addr(records, affected,
                                                   wire_map)
    if not broken_idx:
        raise DisruptionError(
            "no pairing carries an affected flight — nothing to rebuild")

    untouched, broken = [], []
    for i in untouched_idx:
        rec = records[i]
        old_pid = f"C_{i:06d}"
        p = by_pid.get(old_pid)
        emp = p.get("employee") if p else None
        ill = bool(p and emp and (crew_by_id.get(emp, {})
                                  .get("unavailable", set())
                                  & set(p.get("days") or [])))
        untouched.append({
            "fns": _rec_addr(rec.get("fns")),
            "active_fns": _rec_addr(rec.get("active_fns")
                                    if rec.get("active_fns") is not None
                                    else rec.get("fns") or []),
            "cost": rec.get("cost"),
            "employee": None if (emp is None or ill) else emp,
            "pinned": bool(emp) and not ill,
            "old_pid": old_pid,
        })
    recover: set[str] = set()
    for i in broken_idx:
        rec = records[i]
        old_pid = f"C_{i:06d}"
        p = by_pid.get(old_pid)
        active = rec.get("active_fns")
        active = rec.get("fns") if active is None else active
        recover.update(_rec_addr(active))
        broken.append({"old_pid": old_pid,
                       "employee": p.get("employee") if p else None,
                       "fns": _rec_addr(rec.get("fns")),
                       "active_fns": _rec_addr(active)})
    recover -= cancelled

    # ---- claim the repair slot BEFORE creating anything ---------------
    with _lock:
        repairs = _load_repairs()
        prior = repairs.get(inst_id)
        if prior:
            if prior.get("pending"):
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(
                           prior.get("created_at") or _now())).total_seconds()
                if age < PENDING_CLAIM_TTL_S:
                    raise DisruptionError(
                        "a rebuild for this instance is already being "
                        "prepared — retry in a moment")
            else:
                pj = jobs.get_job(str(prior.get("job_id") or ""))
                if pj and pj.get("status") == "running":
                    raise DisruptionError(
                        "a rebuild for this instance is already running "
                        f"({prior['job_id']})")
        # supersede the prior (finished) repair while still holding the
        # claim — nobody else can be mid-run on it after the checks above
        if prior and not prior.get("pending") and prior.get("derived_id"):
            _remove_derived_repair(str(prior.get("derived_id") or ""),
                                   str(prior.get("derived_label") or ""))
        repairs[inst_id] = {"pending": True, "created_at": _now(),
                            "by": email}
        _save_repairs_locked(repairs)

    def _rollback(derived_id: str = "", derived_label: str = "") -> None:
        if derived_id:
            _remove_derived_repair(derived_id, derived_label)
        with _lock:
            repairs = _load_repairs()
            cur = repairs.get(inst_id)
            if cur and cur.get("pending"):
                repairs.pop(inst_id, None)
                _save_repairs_locked(repairs)

    derived_id = derived_label = ""
    try:
        payload = _payload_for(inst)
        derived_payload = build_repair_payload(payload, cancelled, delayed,
                                               sick)

        base_slug = registry.slugify_or_hash(inst.get("label") or inst["id"])
        slug = f"repair-{base_slug}-{secrets.token_hex(3)}"
        derived_id, derived_label, target = registry.upload_target(email,
                                                                   slug)
        registry.write_payload(target, derived_payload, "disruption_repair")
        registry.set_owner(derived_id, email)
        registry.set_llm_scope(derived_id, inst.get("llm_scope",
                                                    "local-only"))

        profile, bases = _stage1_lane(inst, doc)
        workdir = REPAIR_RUNS / derived_label
        workdir.mkdir(parents=True, exist_ok=True)
        report_path = (registry.FINAL_REPORT
                       / f"gerad_repair_report_{derived_label}.json")
        meta = {
            "base_id": inst_id, "base_label": inst.get("label"),
            "derived_id": derived_id, "derived_label": derived_label,
            "derived_payload": str(target.resolve()),
            "workdir": str(workdir.resolve()),
            "cancelled": sorted(cancelled),
            "delayed": {fn: delayed[fn] for fn in sorted(delayed)},
            "recover_fns": sorted(recover),
            "untouched": untouched,
            "broken": broken,
            "profile": profile,
            "bases": bases,
            "budget_s": budget_s, "seed": 7,
            "catalog_out": str((registry.FINAL_REPORT
                                / f"gerad_colgen_selected_{derived_label}"
                                  ".json").resolve()),
            "report_path": str(report_path.resolve()),
            "created_at": _now(), "by": email,
        }
        meta_path = workdir / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                             encoding="utf-8")

        derived_inst = registry.get_instance(derived_id)
        if derived_inst is None:                  # registry wiring broke
            raise DisruptionError("derived instance did not register")

        try:
            job_id = jobs.start_job("repair", derived_inst,
                                    {"meta_path": str(meta_path)},
                                    owner=email)
        except jobs.JobError as exc:
            raise DisruptionError(str(exc)) from exc
    except BaseException:
        _rollback(derived_id, derived_label)
        raise

    with _lock:
        repairs = _load_repairs()
        repairs[inst_id] = {
            "derived_id": derived_id, "derived_label": derived_label,
            "job_id": job_id, "meta_path": str(meta_path),
            "report_path": str(report_path), "created_at": _now(),
            "by": email,
        }
        _save_repairs_locked(repairs)

    return rebuild_status(inst_id)


def rebuild_status(inst_id: str) -> dict:
    """Join of the repair registry entry, its job and the published report."""
    from . import jobs
    with _lock:
        entry = _load_repairs().get(inst_id)
    if not entry:
        return {"status": "none"}
    if entry.get("pending"):
        # claimed, preparation in flight — the UI treats it like running
        return {"status": "running", "created_at": entry.get("created_at")}
    job = jobs.get_job(str(entry.get("job_id") or ""))
    status = job.get("status") if job else "unknown"
    out = {
        "status": status,
        "job_id": entry.get("job_id"),
        "derived_id": entry.get("derived_id"),
        "derived_label": entry.get("derived_label"),
        "created_at": entry.get("created_at"),
    }
    rp = entry.get("report_path")
    if rp:
        try:
            out["report"] = json.loads(
                Path(rp).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return out
