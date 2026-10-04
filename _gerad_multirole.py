"""Фаза 2b (multirole): оркестратор независимых пер-ролевых lane'ов.

Каждая роль однородной сигнатуры получает СВОЙ полный одночленный конвейер
(stage-1 колонная генерация -> stage-2 ростеринг) над своим представлением
payload'а (_gerad_role_views), затем слияние: объединённый flight-level
ростер, покрытие существующим _coverage_validator.check против ИСХОДНОГО
payload'а, слитая пер-сотрудниковая физика существующим
check_employee_sequence, и fail-closed вердикт-конъюнкция.

Существующие движки не правятся вовсе: lane'ы гоняют штатные CLI в их
generic-режиме под метками <label>-role-<роль>.

Usage:
  python _gerad_multirole.py --payload P.json --label slug
                             [--stage1-budget-min N] [budget_s] [seed]

Артефакты: final-report/gerad_multirole_<label>.json (вердикт+слияние),
data/gerad/p2_multirole_solution_<label>.json (слитый ростер), lane-артефакты
под их собственными метками (штатные имена generic-режима).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from _gerad_role_views import (  # noqa: E402
    MultiroleUnsupported, canonical_ordinal, overlapping_ids, role_slug,
    role_views)

FINAL_REPORT = HERE / "final-report"
SCRATCH = HERE / "data" / "gerad"
EPOCH = datetime(2000, 1, 1)
PY = sys.executable


# ------------------------------------------------------------------ merge

def _ref(v, key):
    return v.get(key) if isinstance(v, dict) else v


def merge_solutions(payload: dict, lane_solutions: dict) -> dict:
    """role -> lane flight-level solution  =>  слитый ростер исходного
    payload'а: indexInFlight восстановлен в канонический ordinal сигнатуры,
    идентичности фазы 3 зеркалят ИСХОДНЫЙ спрос (штамп только там, где спрос
    сам их нёс — урок ревью фазы 3), ids перенумерованы."""
    ordinal = canonical_ordinal(payload)
    demand_identity = {}
    for a in payload.get("flightAssignments") or []:
        if not isinstance(a, dict):
            continue
        # тем же разрешением номера, что и _leg_identity.leg_id (ревью: спрос
        # может нести flightNumber вместо flight-ссылки — off-contract, но
        # ключ не должен схлопываться в (None, role))
        fn = _ref(a.get("flight"), "flightNumber") or a.get("flightNumber")
        ident = {
            "flightLegId": a.get("flightLegId"),
            "crewSlotId": a.get("crewSlotId"),
        }
        # Пакет B: при повторных номерах fn-ключ схлопнул бы идентичности
        # разных вхождений — первичен flightLegId; fn-ключ остаётся для
        # решений без штампов (смешанная материализация), но при
        # неоднозначности (два спроса одной роли на один номер) честно
        # выбрасывается, а не отдаёт чужую идентичность.
        if a.get("flightLegId"):
            demand_identity[("leg", a["flightLegId"],
                             a.get("requiredSkill"))] = ident
        fn_key = ("fn", fn, a.get("requiredSkill"))
        if fn_key in demand_identity:
            demand_identity[fn_key] = None       # неоднозначно
        else:
            demand_identity[fn_key] = ident
    merged = []
    counter = 1
    for role in sorted(lane_solutions, key=lambda r: ordinal.get(r, 99)):
        sol = lane_solutions[role]
        for a in sol.get("flightAssignments", []):
            fn = _ref(a.get("flight"), "flightNumber")
            row = {
                "id": str(counter),
                "flight": fn,
                "indexInFlight": ordinal.get(role, 0),
                "requiredSkill": role,
                "employee": _ref(a.get("employee"), "id"),
            }
            ident = (demand_identity.get(("leg", a.get("flightLegId"), role))
                     or demand_identity.get(("fn", fn, role)) or {})
            if ident.get("flightLegId"):
                row["flightLegId"] = ident["flightLegId"]
            if ident.get("crewSlotId"):
                row["crewSlotId"] = ident["crewSlotId"]
            merged.append(row)
            counter += 1
    return {
        "airports": payload.get("airports") or [],
        "employees": payload.get("employees") or [],
        "flights": payload.get("flights") or [],
        "flightAssignments": merged,
    }


def merged_physics(payload: dict, lane_trajs: dict) -> dict:
    """Слитая пер-сотрудниковая физика поверх ВСЕХ lane'ов существующим
    check_employee_sequence (при дизъюнктных пулах инкремента 1 совпадает с
    lane-проверками — защита в глубину на будущее пересечение пулов)."""
    from _gerad_trajectory_check import check_employee_sequence, pairing_crew
    # Пакет B: fns lane-траекторий — wire-имена; строим fl над
    # re-key-нутым payload'ом (identity-путь для payload'ов без повторов).
    from _leg_rekey import rekey_or_passthrough
    payload, _wm = rekey_or_passthrough(payload)
    fl = {}
    for f in payload.get("flights") or []:
        from _canonical_time import parse_utc
        dep = parse_utc(f["departureUTCDateTime"])
        arr = parse_utc(f["arrivalUTCDateTime"])
        fl[f["flightNumber"]] = (
            int((dep - EPOCH).total_seconds() // 60),
            int((arr - EPOCH).total_seconds() // 60),
            f["departureAirport"], f["arrivalAirport"])
    v: dict = defaultdict(int)
    by_emp: dict = defaultdict(list)
    for role, traj in lane_trajs.items():
        for p in traj.get("pairings", []):
            active = set(p.get("active_fns", p.get("fns", [])))
            for _idx, _skill, eid in pairing_crew(p):
                if eid is None:
                    continue
                for fn in p.get("fns", []):
                    if fn not in fl:
                        v["unknown_leg_in_lane"] += 1
                        continue
                    d, a, dc, ac = fl[fn]
                    by_emp[eid].append(
                        (d, a, dc, ac, fn not in active,
                         f"{role}:{p.get('pid', '')}"))
    for eid, seq in by_emp.items():
        seq.sort()
        check_employee_sequence(seq, v)
    return {"violations_total": int(sum(v.values())),
            "violations": dict(sorted(v.items())),
            "employees": len(by_emp)}


_SEVERITY = ["OK", "INFEASIBLE_INPUT_OR_MODEL", "INFEASIBLE_INPUT_UNSUPPORTED",
             "INFEASIBLE_INCUMBENT"]


def decide_verdict(lane_statuses: dict, merged_cov: dict,
                   physics: dict, composition: dict | None = None) -> str:
    """Fail-closed конъюнкция: OK только если каждый lane OK, покрытие
    исходного payload'а полно, слитая физика чиста и (при включённых
    правилах состава, инкремент 4) состав экипажа участков чист; иначе —
    худший из lane-вердиктов (слитые проблемы = INFEASIBLE_INCUMBENT)."""
    worst = "OK"
    for st in lane_statuses.values():
        cand = st if st in _SEVERITY else "INFEASIBLE_INCUMBENT"
        if _SEVERITY.index(cand) > _SEVERITY.index(worst):
            worst = cand
    if worst == "OK" and not merged_cov.get("fully_covered"):
        worst = "INFEASIBLE_INCUMBENT"
    if worst == "OK" and physics.get("violations_total"):
        worst = "INFEASIBLE_INCUMBENT"
    if worst == "OK" and composition and composition.get("enabled") \
            and composition.get("violations_total"):
        worst = "INFEASIBLE_INCUMBENT"
    return worst


# -------------------------------------------------- joint mode (инкремент 3)

def _joint_prefix(k: int) -> str:
    return f"MRJ{k}-"


def build_joint_payload(payload: dict, views: dict, selected_by_role: dict,
                        role_order: list) -> dict:
    """Конкатенация роль-меченых синтетических payload'ов lane'ов в ОДИН
    solve: pid'ы и id слотов префиксуются по роли (уникальность), экипаж —
    ПОЛНЫЙ исходный пул (двойные квалификации в диапазоне значений обеих
    ролей; жёсткие ограничения GERAD2P — requiredSkill, overlap, rest,
    monthly block — сами исключают межролевой double-booking)."""
    from _gerad_stage2_roster import build_synthetic_payload
    from _leg_rekey import rekey_or_passthrough
    flights, assignments = [], []
    for k, role in enumerate(role_order):
        # Пакет B: каталоги lane'ов — wire-пространство stage-1; синтетика
        # строится над re-key-нутым представлением той же биекцией.
        view_wire, _wm = rekey_or_passthrough(views[role])
        syn = build_synthetic_payload(view_wire, selected_by_role[role])
        pref = _joint_prefix(k)
        for f in syn["flights"]:
            f = dict(f)
            f["flightNumber"] = pref + f["flightNumber"]
            flights.append(f)
        for a in syn["flightAssignments"]:
            a = dict(a)
            a["id"] = pref + a["id"]
            a["flight"] = pref + a["flight"]
            assignments.append(a)
    # Ревью пакета: Kotlin матчит preferredFlights против legIds synthetic-
    # рейсов, которые для повторных номеров — WIRE-имена; экипаж берём из
    # re-key-нутого payload'а (префы развёрнуты в wire), иначе префы
    # дублированных участков молча теряются в joint-solve.
    payload_wire, _pw = rekey_or_passthrough(payload)
    return {
        "airports": payload.get("airports") or [],
        "employees": payload_wire.get("employees") or [],
        "flights": flights,
        "flightAssignments": assignments,
    }


def split_joint_solved(solved: dict, role_order: list) -> dict:
    """role -> его часть совместного решения с ОТРЕЗАННЫМИ префиксами (pid'ы
    снова C_%06d — ровно та нумерация, что ждёт сертификат линковки)."""
    parts = {role: {"flights": [], "flightAssignments": []}
             for role in role_order}
    pref_of = {_joint_prefix(k): role for k, role in enumerate(role_order)}

    def _cut(name):
        for pref, role in pref_of.items():
            if isinstance(name, str) and name.startswith(pref):
                return role, name[len(pref):]
        return None, name

    for f in solved.get("flights", []):
        role, fn = _cut(f.get("flightNumber"))
        if role is None:
            continue
        g = dict(f)
        g["flightNumber"] = fn
        parts[role]["flights"].append(g)
    for a in solved.get("flightAssignments", []):
        ref = a.get("flight")
        ref = ref.get("flightNumber") if isinstance(ref, dict) else ref
        role, fn = _cut(ref)
        if role is None:
            continue
        b = dict(a)
        b["flight"] = fn
        if isinstance(b.get("id"), str):
            _r, b["id"] = _cut(b["id"])
        parts[role]["flightAssignments"].append(b)
    return parts


def solve_joint(joint_payload: dict, budget_s: int, seed: int) -> dict:
    """Один solve GERAD2P над конкатенацией lane-каталогов (как stage-2,
    тот же изолированный JVM на STAGE2_PORT)."""
    import copy as _copy
    import os
    import httpx
    sys.path.insert(0, str(HERE / "src"))
    from _compr_isolated import IsolatedJvmFleet
    from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR
    port = int(os.environ.get("STAGE2_PORT", "9096"))
    with IsolatedJvmFleet([port], KOTLIN_SOLVER_JAR, heap_xmx="8g"):
        with httpx.Client(timeout=budget_s * 2 + 120.0) as cli:
            r = cli.post(f"http://127.0.0.1:{port}/schedules/single",
                         json=_copy.deepcopy(joint_payload),
                         params={"acceptor": "LA",
                                 "spentLimitSeconds": budget_s,
                                 "randomSeed": seed,
                                 "rules": "GERAD2P"})
            r.raise_for_status()
            return r.json()


def assemble_lane(role: str, lane: str, view: dict, vpath: Path,
                  catalog_path: Path, solved_role: dict, joint_score,
                  budget_s: int, seed: int) -> dict:
    """Прямая сборка lane-артефактов из части совместного решения. Сборка
    НЕ доверяет сама себе: линковку с каталогом и физику проверяет
    СУЩЕСТВУЮЩИЙ сертификат (check_inst), покрытие — существующий check,
    вердикт — существующий gate_result_status. Возвращает lane-запись."""
    from _coverage_validator import check as coverage_check
    from _gerad_phase2_direct import personalization_metrics
    from _gerad_stage2_roster import (_crew_of_pairing,
                                      expand_to_flight_level,
                                      gate_result_status, journal_write)
    from _gerad_trajectory_check import check_inst
    from _leg_rekey import (rekey_or_passthrough, restore_solution,
                            wire_to_leg_ids)

    sel_dump = json.loads(catalog_path.read_text())
    selected = sel_dump["selected"]
    # Пакет B (joint): каталог lane'а — wire-пространство stage-1;
    # представление роли re-key-уется той же детерминированной биекцией,
    # штамп каталога сверяется (stale-каталог = fail-closed lane).
    view_wire, wm = rekey_or_passthrough(view)
    cat_wm = sel_dump.get("wire_map") or {}
    # двусторонняя сверка (ревью): каталог СО штампом при представлении
    # БЕЗ повторов — тоже stale (иначе сырой KeyError ниже)
    if (wm or cat_wm) and cat_wm != wm:
        raise ValueError(
            f"lane «{role}»: каталог stage-1 не от этого представления "
            f"(wire_map расходится) — перегоните stage-1")
    expanded = expand_to_flight_level(view_wire, solved_role)
    crew_of = _crew_of_pairing(solved_role)

    pairings = []
    for i, rec in enumerate(selected):
        if not rec.get("active_fns", rec.get("fns")):
            continue
        pid = f"C_{i:06d}"
        slots = crew_of.get(pid) or [(0, "Captain", None)]
        pairings.append({
            "pid": pid, "employee": slots[0][2],
            "crew": [{"indexInFlight": idx, "requiredSkill": skill,
                      "employee": eid} for idx, skill, eid in slots],
            "fns": rec["fns"],
            "active_fns": rec.get("active_fns", rec["fns"])})
    traj = {"inst": None, "label": lane, "seed": seed, "budget_s": budget_s,
            "stage2_score": joint_score,
            "catalog": {"file": catalog_path.name,
                        "ip_objective": sel_dump.get("ip_objective"),
                        "source": sel_dump.get("source")},
            "pairings": pairings}

    # Паттерн промоушена (ревью 2026-07-19: паритет с run_one) — артефакты
    # пишутся как *.pending.*, валидируются, штампуются вердиктом и только
    # затем атомарно публикуются; INFEASIBLE_INCUMBENT дополнительно уходит
    # в runs/stage2-diagnostics/. Крах между записями не оставляет
    # канонического артефакта без вердикта.
    sol_p = SCRATCH / f"p2_two_stage_solution_{lane}.json"
    traj_p = FINAL_REPORT / f"gerad_stage2_assignment_{lane}.json"
    sol_pending = sol_p.with_suffix(".pending.json")
    traj_pending = traj_p.with_suffix(".pending.json")
    # P1-09: joint-режим пишет lane-артефакты сам (мимо stage-2 CLI) —
    # штампуем run_id и здесь, чтобы _fresh сверял происхождение точно.
    # Пакет B: публикуемое решение — восстановленное (исходные номера),
    # траектория остаётся в wire со штампом wire_map (паритет с run_one).
    expanded_pub = restore_solution(expanded, wm)
    _rid = os.environ.get("CREW_RUN_ID")
    if _rid:
        if isinstance(expanded_pub, dict):
            expanded_pub["run_id"] = _rid
        traj["run_id"] = _rid
    if wm:
        traj["wire_map"] = wm
    sol_pending.write_text(json.dumps(expanded_pub), encoding="utf-8")
    traj_pending.write_text(json.dumps(traj))

    cov = coverage_check(view, expanded_pub)
    m = personalization_metrics(expanded)
    try:
        rep = check_inst(lane, traj_path=traj_pending, sol_path=sol_pending,
                         catalog_path=catalog_path, payload_path=vpath)
        traj_violations = rep["violations_total"]
    except (ValueError, FileNotFoundError, KeyError) as exc:
        rep = {"certificate_error": str(exc)}
        traj_violations = -1            # сертификат падает -> fail-closed
    # Пакет B: proven_impossible каталога — wire-имена, missing покрытия —
    # leg_id исходного пространства (паритет с run_one).
    allowed_missing = wire_to_leg_ids(
        set(sel_dump.get("proven_impossible_legs") or []), wm)
    status = gate_result_status(joint_score, m["unassigned"], cov,
                                allowed_missing,
                                trajectory_violations=traj_violations)
    traj["result_status"] = status
    traj["trajectory_check"] = rep
    traj_pending.write_text(json.dumps(traj))
    if status == "INFEASIBLE_INCUMBENT":
        diag = FINAL_REPORT / "runs" / "stage2-diagnostics"
        diag.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (diag / f"{lane}-{stamp}-assignment.json").write_text(
            json.dumps(traj))
        (diag / f"{lane}-{stamp}-solution.json").write_text(
            json.dumps(expanded_pub), encoding="utf-8")
    traj_pending.replace(traj_p)
    sol_pending.replace(sol_p)

    results_path = FINAL_REPORT / "gerad_phase2_two_stage.json"
    row = {"inst": None, "label": lane,
           "legs": len(view.get("flights") or []),
           "pairings": len(pairings), "result_status": status,
           "trajectory_violations": traj_violations,
           "missing_legs": cov["missing"], "stage2_score": joint_score,
           "joint_mode": True, "seed": seed, "budget_s": budget_s, **m}
    journal_write(results_path, row)
    print(f"  joint lane [{role}] -> {status} "
          f"(missing={cov['missing']}, unassigned={m['unassigned']}, "
          f"traj={traj_violations})", flush=True)
    return {"lane": lane, "result_status": status,
            "catalog": {"file": catalog_path.name}, "view": vpath.name}


# ------------------------------------------------------------ orchestration

def _run(cmd: list, env=None) -> int:
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call([str(c) for c in cmd], cwd=str(HERE), env=env)


def _fresh(path: Path, since: float) -> bool:
    """Артефакт записан ЭТИМ прогоном (защита от stale-чтения вердикта
    предыдущего multirole-прогона той же метки: упавший lane не должен
    отчитываться чужим OK).

    Аудит 2026-07-19 (P1-09): первично сверяется run_id, который писатель
    штампует в сам артефакт (CREW_RUN_ID сквозной: jobs -> multirole ->
    lane stage-2). Артефакт без штампа (стар или ручной CLI) — строгий
    mtime >= since: прежний допуск -1 c принимал чужой артефакт возрастом
    до секунды, и новый крах наследовал старый OK."""
    try:
        st = path.stat()
    except OSError:
        return False
    rid = os.environ.get("CREW_RUN_ID")
    if rid:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stamped = data.get("run_id") if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return False
        if stamped is not None:
            return stamped == rid
    return st.st_mtime >= since


def run_multirole(payload_path: Path, label: str, stage1_budget_min: int,
                  budget_s: int, seed: int) -> str:
    t0 = time.time()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    # Инкремент 3: пересекающиеся пулы (двойные квалификации) допустимы и
    # переключают stage-2 в СОВМЕСТНЫЙ режим (один solve над конкатенацией
    # каталогов — жёсткие ограничения солвера исключают межролевой
    # double-booking); дизъюнктные пулы идут независимыми lane'ами.
    views = role_views(payload, allow_overlap=True)
    overlap = overlapping_ids(payload)
    mode = "joint" if overlap else "independent"
    # Пакет B: независимые lane'ы наследуют re-key из штатных stage-1/
    # stage-2; joint-режим получил ту же дисциплину двух пространств имён
    # в build_joint_payload/assemble_lane (wire при solve, восстановление
    # при публикации, штампы wire_map) — гард снят.
    if overlap:
        print(f"=== multirole: пересекающиеся пулы ({len(overlap)} чел. с "
              f"двойной квалификацией) — СОВМЕСТНЫЙ stage-2 ===", flush=True)
    run_dir = FINAL_REPORT / "runs" / "multirole" / label
    run_dir.mkdir(parents=True, exist_ok=True)

    # Разделитель '--role--' инъективен (ревью, medium): в upload-метке '--'
    # встречается РОВНО один раз (user_slug--name_slug; slugify схлопывает
    # серии дефисов внутри частей), поэтому lane-метка с двумя '--' не может
    # совпасть ни с одной пользовательской меткой и не кланяет чужие
    # артефакты/журнал/бест-каталог.
    lanes: dict = {}
    lane_ready: dict = {}      # role -> (lane, vpath, catalog) для stage-2
    for role, view in views.items():
        lane = f"{label}--role--{role_slug(role)}"
        vpath = run_dir / f"view_{role_slug(role)}.json"
        vpath.write_text(json.dumps(view), encoding="utf-8")
        print(f"=== multirole lane [{role}] -> {lane} "
              f"({len(view['employees'])} чел., "
              f"{len(view['flightAssignments'])} мест) ===", flush=True)
        # Замечание (ревью): падение первого lane НЕ прерывает остальные —
        # осознанно: один прогон даёт диагностику ПО ВСЕМ ролям (вердикт всё
        # равно fail-closed конъюнкция), ценой бюджета оставшихся lane'ов.
        rc = _run([PY, "_gerad_colgen.py", "0", "--payload", vpath,
                   "--profile", "v2", "--name", lane,
                   "--dump-selected", "--dump-pool", "--cert-budget", "60",
                   "--total-cap", str(stage1_budget_min * 60)])
        if rc != 0:
            print(f"  !! stage-1 lane [{role}] rc={rc} — fail-closed",
                  flush=True)
            lanes[role] = {"lane": lane, "result_status":
                           "INFEASIBLE_INPUT_UNSUPPORTED" if rc == 3
                           else "INFEASIBLE_INCUMBENT"}
            continue
        catalog = FINAL_REPORT / f"gerad_colgen_selected_{lane}.json"
        if not catalog.exists():
            print(f"  !! lane [{role}]: каталог не промоутился — fail-closed",
                  flush=True)
            lanes[role] = {"lane": lane,
                           "result_status": "INFEASIBLE_INCUMBENT"}
            continue
        lane_ready[role] = (lane, vpath, catalog)

    if mode == "independent":
        for role, (lane, vpath, catalog) in lane_ready.items():
            # Канонический каталог = ЛУЧШИЙ известный для метки (семантика
            # промоушена): непромоутившийся свежий прогон честно оставляет
            # предыдущий бест, а несоответствие каталога payload'у ловят
            # сертификат траектории и покрытие lane'а. Провенанс в артефакт.
            cat_provenance = {"file": catalog.name,
                              "mtime": time.strftime(
                                  "%Y-%m-%d %H:%M:%S",
                                  time.localtime(catalog.stat().st_mtime)),
                              "fresh_this_run": _fresh(catalog, t0)}
            rc = _run([PY, "_gerad_stage2_roster.py", "--payload", vpath,
                       "--catalog", catalog, "--label", lane,
                       str(budget_s), str(seed)])
            traj_p = FINAL_REPORT / f"gerad_stage2_assignment_{lane}.json"
            status = "INFEASIBLE_INCUMBENT"
            if _fresh(traj_p, t0):
                try:
                    status = json.loads(traj_p.read_text()).get(
                        "result_status", status)
                except (OSError, json.JSONDecodeError):
                    pass
            elif traj_p.exists():
                print(f"  !! lane [{role}]: артефакт stage-2 НЕ обновлён "
                      f"этим прогоном (stale) — вердикт lane fail-closed",
                      flush=True)
            lanes[role] = {"lane": lane, "result_status": status,
                           "catalog": cat_provenance,
                           "view": vpath.name,
                           "stage2_rc": rc}
    elif lane_ready:
        # СОВМЕСТНЫЙ stage-2: один solve, затем прямая сборка lane-артефактов
        # (существующие сертификат/покрытие/гейт валидируют сборку).
        role_order = [r for r in views if r in lane_ready]
        sel_docs = {r: json.loads(lane_ready[r][2].read_text())
                    for r in role_order}
        selected_by_role = {r: sel_docs[r]["selected"] for r in role_order}
        # Ревью пакета: stale-каталог обнаруживается ДО solve (иначе бюджет
        # сгорает, а ValueError из сборки валит весь прогон трейсбеком).
        from _leg_rekey import rekey_or_passthrough as _rkp
        for r in role_order:
            _vw, _wm = _rkp(views[r])
            _cwm = sel_docs[r].get("wire_map") or {}
            if (_wm or _cwm) and _cwm != _wm:
                print(f"!! joint: каталог lane «{r}» не от этого "
                      f"представления (wire_map расходится) — перегоните "
                      f"stage-1; прогон отменён (fail-closed)", flush=True)
                return "INFEASIBLE_INPUT_UNSUPPORTED"
        joint = build_joint_payload(payload, views, selected_by_role,
                                    role_order)
        print(f"=== multirole JOINT solve: {len(joint['flights'])} пар, "
              f"{len(joint['employees'])} чел. (в т.ч. "
              f"{len(overlap)} двойных), {budget_s}s ===", flush=True)
        solved = solve_joint(joint, budget_s, seed)
        joint_score = solved.get("score")
        print(f"  joint score: {joint_score}", flush=True)
        parts = split_joint_solved(solved, role_order)
        for role in role_order:
            lane, vpath, catalog = lane_ready[role]
            try:
                lanes[role] = assemble_lane(role, lane, views[role], vpath,
                                            catalog, parts[role],
                                            joint_score, budget_s, seed)
            except ValueError as exc:
                # fail-closed lane вместо трейсбека всего прогона (ревью)
                print(f"  !! joint lane [{role}]: {exc}", flush=True)
                lanes[role] = {"lane": lane,
                               "result_status": "INFEASIBLE_INCUMBENT",
                               "catalog": {"file": catalog.name},
                               "view": vpath.name,
                               "assemble_error": str(exc)}

    lane_solutions, lane_trajs = {}, {}
    for role, rec in lanes.items():
        sp = SCRATCH / f"p2_two_stage_solution_{rec['lane']}.json"
        tp = FINAL_REPORT / f"gerad_stage2_assignment_{rec['lane']}.json"
        if _fresh(sp, t0):
            try:
                lane_solutions[role] = json.loads(
                    sp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                # битое/недописанное решение lane -> без решения -> вердикт
                # fail-closed; оркестратор обязан дописать артефакт, а не
                # умереть трейсбеком после потраченного бюджета (ревью)
                print(f"  !! lane [{role}]: решение нечитаемо ({exc}) — "
                      f"fail-closed", flush=True)
        if _fresh(tp, t0):
            try:
                lane_trajs[role] = json.loads(tp.read_text())
            except (OSError, json.JSONDecodeError):
                pass

    from _coverage_validator import check as coverage_check
    from _crew_composition import check_composition
    if lane_solutions:
        merged = merge_solutions(payload, lane_solutions)
        cov = coverage_check(payload, merged)
        phys = merged_physics(payload, lane_trajs)
        comp = check_composition(payload, merged)
        if comp.get("enabled"):
            print(f"  состав экипажа: нарушений "
                  f"{comp.get('violations_total')} "
                  f"({comp.get('violations')})", flush=True)
        (SCRATCH / f"p2_multirole_solution_{label}.json").write_text(
            json.dumps(merged), encoding="utf-8")
    else:
        merged = None
        cov = {"fully_covered": False, "missing": -1,
               "note": "ни один lane не дал решения"}
        phys = {"violations_total": -1, "violations": {}}
        # enabled обязан отражать КОНФИГ, а не число решений (ревью:
        # стаб с enabled=False врал, что правила выключены)
        comp = check_composition(payload, {"flightAssignments": []})

    verdict = decide_verdict(
        {r: rec["result_status"] for r, rec in lanes.items()}, cov, phys,
        composition=comp)
    out = {
        "label": label,
        "roles": list(views),
        "mode": mode,
        "overlapping_crew": sorted(overlap),
        "lanes": lanes,
        "merged": {
            "coverage": {k: cov.get(k) for k in
                         ("required", "assigned", "missing", "missing_slots",
                          "skill_mismatches", "duplicates", "unknown_flights",
                          "unassigned_slots", "fully_covered")},
            "physics": phys,
            "composition": comp,
        },
        "result_status": verdict,
        "seed": seed, "budget_s": budget_s,
        "stage1_budget_min": stage1_budget_min,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # P1-09: происхождение сводки доказывает run_id, не время файла.
    if os.environ.get("CREW_RUN_ID"):
        out["run_id"] = os.environ["CREW_RUN_ID"]
    ap = FINAL_REPORT / f"gerad_multirole_{label}.json"
    tmp = ap.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1))
    tmp.replace(ap)
    # Active-roster manifest (аудит-2 P1-06): per-run запись родительской
    # метки БЕЗ авто-промоции — multirole промотируется только явным
    # действием (и только при OK): merged-решение flight-level и не
    # потребляется ростерными читателями напрямую.
    try:
        import rules_config as _rc
        from _roster_manifest import (build_run_manifest, publish,
                                      sha256_file as _shaf, _rel)
        # Пути каталогов — относительные к репо (ревью пакета: абсолютные
        # машинные пути делали манифест непереносимым между контурами).
        _cats = {r: {"path": _rel(lane_ready[r][2]),
                     "sha256": _shaf(Path(lane_ready[r][2]))}
                 for r in lane_ready}
        _msol = SCRATCH / f"p2_multirole_solution_{label}.json"
        _manifest = build_run_manifest(
            label=label, mode="multirole", result_status=verdict,
            payload_path=payload_path, rules_snapshot=_rc.effective(),
            assignment_path=ap,
            solution_path=_msol if _msol.exists() else None,
            catalogs=_cats,
            multirole={"mode": mode, "roles": list(views),
                       "overlapping_crew": sorted(overlap),
                       "lanes": {r: rec.get("result_status")
                                 for r, rec in lanes.items()}},
            limitations=["score_non_comparable",
                         "no_single_trajectory_certificate",
                         "repair_unavailable"],
            seed=seed, budget_s=budget_s)
        publish(_manifest, auto_promote=False)
    except Exception as exc:   # noqa: BLE001
        print(f"  !! манифест multirole не записан: {exc}", flush=True)
    print(f"=== multirole [{label}]: {verdict}  "
          f"(lanes: {', '.join(f'{r}={rec['result_status']}' for r, rec in lanes.items())}; "
          f"coverage covered={cov.get('fully_covered')}; "
          f"physics={phys.get('violations_total')}; "
          f"composition={'выкл' if not comp.get('enabled') else comp.get('violations_total')}) ===",
          flush=True)
    print(f"  saved -> {ap.name}", flush=True)
    return verdict


def main():
    ap = argparse.ArgumentParser(prog="_gerad_multirole.py")
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--stage1-budget-min", type=int, default=45)
    ap.add_argument("pos", nargs="*", type=int,
                    help="[budget_s] [seed] для stage-2 каждого lane")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        ap.error(f"--label должен быть [a-z0-9-]+, получено '{args.label}'")
    if re.fullmatch(r"inst[1-7]", args.label):
        ap.error("--label inst1..inst7 зарезервированы")
    if not args.payload.exists():
        print(f"!! payload не существует: {args.payload}", flush=True)
        return 2
    budget_s = args.pos[0] if len(args.pos) > 0 else 600
    seed = args.pos[1] if len(args.pos) > 1 else 7
    try:
        verdict = run_multirole(args.payload.resolve(), args.label,
                                args.stage1_budget_min, budget_s, seed)
    except MultiroleUnsupported as exc:
        print("!! payload не поддерживается multirole-инкрементом 1 "
              f"(fail-closed): {exc}", flush=True)
        return 3
    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
