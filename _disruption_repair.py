"""Disruption repair driver (Phase B, 2026-07-16): neighbourhood pairing
rebuild + pinned re-assignment, run as a background job (webapp/jobs.py
type "repair").

Input: --meta <path> written by webapp.disruption.prepare_rebuild().
Steps (each honest about partial failure):

  1. NEIGHBOURHOOD STAGE 1 — column generation (_gerad_colgen.py) over a
     sub-payload holding ONLY the surviving legs of the dissolved
     pairings. All artifacts go under the repair workdir (--out anchors
     runs/ and best/ there), so benchmark catalogs are never polluted.
  2. MERGE — untouched pairings carried over UNCHANGED (same relative
     order, crew frozen via "employee" + "pinned") + the new
     neighbourhood pairings. Written to the canonical catalog location,
     so stage-2/validate/board machinery works on the derived instance
     with zero special cases.
  3. STAGE 2 — _gerad_stage2_roster.py over the derived payload with the
     merged catalog; build_synthetic_payload passes employee/pinned
     through, Timefold's @PlanningPin freezes the untouched world and
     re-crews only the new pairings. Fallback when the solver jar is
     missing or crashes: a stub artifact + the Phase-A greedy repair
     (reserves first, depth-1 swaps), clearly labeled REPAIR_GREEDY.
  4. REPORT — before/after JSON (broken vs new pairings, pid map, crew
     changes, uncovered legs) published next to the assignment artifact.

Phase C (2026-07-16, same file): ITERATIVE NEIGHBOURHOOD EXPANSION.
When a round leaves legs uncovered — the restricted pool simply cannot
form a legal pairing around them — the driver unfreezes the few
untouched pairings with a STITCH POINT next to an uncovered leg (same
aircraft type, airport/time adjacency), adds their active legs to the
pool and re-runs the column generator. Bounded by EXPAND_MAX_ROUNDS and
EXPAND_MAX_UNFREEZE per round: stability beats optimality, every
unfrozen pairing is churn and the report names each one. The heuristic
only PICKS candidates — legality stays with the column generator.

Exit 0 when an assignment artifact AND a report were published (even if
the stage-2 gate said INFEASIBLE_* — that verdict is IN the report);
exit 2 on hard failure."""
from __future__ import annotations

import _data_paths
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

ROOT = Path(__file__).resolve().parent
PY = sys.executable

NEIGH_TOTAL_CAP_S = 180        # colgen wall budget for the tiny sub-problem
NEIGH_PRICE_CAP_S = 30
NEIGH_FINAL_TL_S = 60

EXPAND_MAX_ROUNDS = 3          # 1 base round + up to 2 expansion rounds
EXPAND_MAX_UNFREEZE = 4        # untouched pairings unfrozen per round
EXPAND_ADJ_WINDOW_MIN = 48 * 60   # stitch-point adjacency window, minutes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[repair] {msg}", flush=True)


# ------------------------------------------------------------ stage 1

def build_neighbourhood_payload(derived_payload: dict,
                                recover_fns: list[str]) -> dict:
    """Sub-payload for column generation: ONLY the legs to re-cover (they
    already carry the delayed re-timings — they come from the derived
    payload). Employees ride along for schema shape; colgen reads flights."""
    # Трек occurrence-идентичности: recover_fns — addr-пространство
    # (уникальный номер ЛИБО flightLegId); матчим рейс по любому из двух —
    # legId стабилен между base- и derived-пространствами.
    #
    # Ревью пакета (CRITICAL): sub-payload ПЕРЕИМЕНОВЫВАЕТ рейсы в их addr —
    # имена в подпространстве всегда уникальны, поэтому (а) два вхождения
    # повторного номера не упираются в rekey-блокер «без flightAssignments»,
    # (б) одно вхождение не возвращается из colgen голым номером: каталог
    # окрестности СРАЗУ в addr-пространстве, без суб-биекции.
    want = set(recover_fns)

    def _match(f: dict) -> bool:
        if str(f.get("flightNumber")) in want:
            return True
        lid = f.get("flightLegId")
        return isinstance(lid, str) and lid in want

    flights = []
    for f in derived_payload.get("flights") or []:
        if not _match(f):
            continue
        g = dict(f)
        lid = g.get("flightLegId")
        addr = (lid if isinstance(lid, str) and lid
                and lid in want else str(g.get("flightNumber")))
        g["flightNumber"] = addr
        flights.append(g)
    return {
        "airports": derived_payload.get("airports") or [],
        "employees": derived_payload.get("employees") or [],
        "flights": flights,
        "flightAssignments": [],
    }


def run_neighbourhood_colgen(meta: dict, workdir: Path,
                             derived_payload: dict, pool: list[str],
                             rnd: int = 1) -> tuple[list, list, list]:
    """One column-generation round over the given leg pool: (new records,
    uncovered fns, proven-impossible fns). Never raises on solver trouble —
    an empty neighbourhood catalog degrades to 'all legs uncovered', which
    the report states plainly. Proven-impossible legs (exact certification
    in the restricted repair space) ride through to the merged catalog so
    the stage-2 gate can classify the outcome as INFEASIBLE_INPUT_OR_MODEL
    — a property of the disruption, not a broken incumbent."""
    recover = list(pool)
    if not recover:
        log("no surviving legs to re-cover — skipping stage 1")
        return [], [], []
    payload_p = workdir / f"neigh_payload_r{rnd}.json"
    neigh = build_neighbourhood_payload(derived_payload, recover)
    if len(neigh["flights"]) != len(recover):
        missing = set(recover) - {str(f.get("flightNumber"))
                                  for f in neigh["flights"]}
        log(f"WARNING: {len(missing)} recover legs absent from the derived "
            f"payload: {sorted(missing)[:5]}")
    payload_p.write_text(json.dumps(neigh, indent=1), encoding="utf-8")

    name = f"nb{rnd}"
    out_p = workdir / f"colgen_results_r{rnd}.json"
    cmd = [PY, str(ROOT / "_gerad_colgen.py"), "0",
           "--payload", str(payload_p),
           "--name", name,
           "--profile", str(meta.get("profile") or "v2"),
           "--pricer", "legs",
           "--dump-selected",
           "--total-cap", str(NEIGH_TOTAL_CAP_S),
           "--price-cap", str(NEIGH_PRICE_CAP_S),
           "--final-tl", str(NEIGH_FINAL_TL_S),
           "--out", str(out_p)]
    if meta.get("bases"):
        cmd += ["--bases", str(meta["bases"])]
    log(f"stage 1 (neighbourhood, round {rnd}): " + " ".join(cmd[1:]))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        log(f"WARNING: neighbourhood colgen exited {proc.returncode}")

    candidates = sorted(
        workdir.glob(f"runs/stage1/*/gerad_colgen_selected_{name}.json"),
        key=lambda p: p.stat().st_mtime)
    if not candidates:
        log("WARNING: neighbourhood produced no catalog — all legs uncovered")
        return [], sorted(recover), []
    try:
        doc = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # a colgen killed mid-write leaves a truncated dump — degrade to
        # the honest all-uncovered outcome, never crash the whole repair
        log(f"WARNING: neighbourhood catalog unreadable ({exc}) — "
            "all legs uncovered")
        return [], sorted(recover), []
    records = [r for r in doc.get("selected") or [] if isinstance(r, dict)]
    # Трек occurrence-идентичности: sub-payload окрестности может нести
    # повторные номера -> colgen ре-key-ит его СВОЕЙ биекцией (sub-wire),
    # которая НЕ совпадает с wire derived-payload'а (если в пул попало одно
    # вхождение из двух, номер там уникален). Нормализуем записи каталога
    # и вердикты в ADDR-пространство сразу — merge_catalog затем переводит
    # addr -> derived-wire единообразно.
    sub_wm = doc.get("wire_map") or {}

    def _to_addr(fns) -> list:
        out = []
        for x in (fns or []):
            rec = sub_wm.get(str(x))
            out.append(rec["flightLegId"] if rec else str(x))
        return out

    for r in records:
        r["fns"] = _to_addr(r.get("fns"))
        if r.get("active_fns") is not None:
            r["active_fns"] = _to_addr(r.get("active_fns"))
    covered: set[str] = set()
    for r in records:
        active = r.get("active_fns")
        active = r.get("fns") if active is None else active
        covered.update(str(x) for x in active or [])
    uncovered = sorted(set(recover) - covered)
    proven = sorted(set(_to_addr(doc.get("proven_impossible_legs")))
                    & set(recover))
    log(f"stage 1 round {rnd} done: {len(records)} new pairings, "
        f"{len(uncovered)} uncovered legs "
        f"({len(proven)} proven impossible in the restricted pool)")
    return records, uncovered, proven


# ------------------------------------------------- Phase C: expansion

def _parse_dt(s: str):
    from _canonical_time import parse_utc
    return parse_utc(s)


def _expansion_candidates(uncovered: list[str], untouched: list[dict],
                          flights_by_fn: dict, limit: int) -> list[int]:
    """Indexes of the untouched pairings most likely to help cover the
    uncovered legs when unfrozen: same aircraft type AND an airport/time
    stitch point (a leg of the pairing arrives where an uncovered leg
    departs shortly before it, or departs where it arrives shortly after).
    Scoring is a heuristic ONLY — the column generator decides legality;
    a bad pick costs one wasted round, never a wrong roster.

    Active-leg stitches weigh double (Phase-C audit + live calibration):
    unfreezing contributes only active_fns to the pool, so an active
    adjacency is directly realizable; a deadhead adjacency is kept at
    half weight as a route-proximity proxy — on the GERAD benchmark the
    pairings that actually re-stitched the holes were found through it.
    A bad pick still only costs one rolled-back round."""
    scored: list[tuple[int, int]] = []
    for idx, u in enumerate(untouched):
        pts = 0
        active = set(str(x) for x in (u.get("active_fns")
                                      if u.get("active_fns") is not None
                                      else u.get("fns")) or [])
        for fn in u.get("fns") or []:
            f = flights_by_fn.get(str(fn))
            if not f:
                continue
            w = 2 if str(fn) in active else 1     # active stitch weighs 2x
            for ufn in uncovered:
                g = flights_by_fn.get(str(ufn))
                if not g:
                    continue
                if ((f.get("aircraftType") or "")
                        != (g.get("aircraftType") or "")):
                    continue
                try:
                    f_dep = _parse_dt(f["departureUTCDateTime"])
                    f_arr = _parse_dt(f["arrivalUTCDateTime"])
                    g_dep = _parse_dt(g["departureUTCDateTime"])
                    g_arr = _parse_dt(g["arrivalUTCDateTime"])
                except (KeyError, ValueError):
                    continue
                gap = (g_dep - f_arr).total_seconds() / 60
                if (f.get("arrivalAirport") == g.get("departureAirport")
                        and 0 < gap <= EXPAND_ADJ_WINDOW_MIN):
                    pts += w
                gap = (f_dep - g_arr).total_seconds() / 60
                if (f.get("departureAirport") == g.get("arrivalAirport")
                        and 0 < gap <= EXPAND_ADJ_WINDOW_MIN):
                    pts += w
        if pts:
            scored.append((-pts, idx))
    scored.sort()
    return [idx for _, idx in scored[:limit]]


def expand_neighbourhood(meta: dict, workdir: Path):
    """Phase C driver loop: (untouched, unfrozen, records, uncovered,
    proven, rounds) of the BEST round found within the budget.

    Round 1 is the plain Phase-B neighbourhood. While legs stay uncovered
    and adjacent untouched pairings exist, unfreeze at most
    EXPAND_MAX_UNFREEZE of them (their crews return to the free pool, their
    ACTIVE legs join the re-cover pool — deadheads are owned elsewhere) and
    re-run. Unfreezing a pairing dumps its legs into the pool, so a single
    round may leave MORE holes than it fills until a later round gives the
    generator enough freed crews to re-pair everything — so the loop rides
    THROUGH such a valley and KEEPS THE BEST round seen (fewest uncovered),
    not the last. If the best is round 1, nothing is unfrozen: zero churn,
    expansion simply did not help. Stability first: bounded rounds (≤
    EXPAND_MAX_UNFREEZE × (EXPAND_MAX_ROUNDS−1) pairings ever unfrozen),
    every unfrozen pairing named in the report."""
    try:
        derived_payload = json.loads(
            Path(meta["derived_payload"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # without the derived payload there is nothing to repair — honor
        # the module contract (exit 2 = hard failure with a diagnostic)
        log(f"FATAL: derived payload unreadable ({exc}) — cannot repair")
        sys.exit(2)
    # Трек occurrence-идентичности: индекс addr -> flight (уникальный номер
    # И flightLegId) — эвристика смежности не должна считать соседство не
    # того вхождения при повторных номерах.
    from _leg_identity import GeradPipelineUnsupported, build_addr_index
    try:
        flights_by_fn = build_addr_index(derived_payload)
    except GeradPipelineUnsupported as exc:
        log(f"FATAL: адресная коллизия derived payload'а: {exc}")
        sys.exit(2)
    untouched = list(meta.get("untouched") or [])
    pool = sorted(set(str(x) for x in meta.get("recover_fns") or []))
    unfrozen: list[dict] = []
    if not pool:
        log("no surviving legs to re-cover — skipping stage 1")
        return untouched, unfrozen, [], [], [], 0

    best = None            # (untouched, unfrozen, records, uncov, proven, rnd)
    proven_all: set[str] = set()   # certificates are monotone across rounds
    rnd = 0
    while rnd < EXPAND_MAX_ROUNDS:
        rnd += 1
        records, uncovered, proven = run_neighbourhood_colgen(
            meta, workdir, derived_payload, pool, rnd)
        proven_all.update(str(x) for x in proven)
        # snapshot BEFORE the next unfreeze: these untouched/unfrozen lists
        # are exactly the state whose pool produced `records`
        if best is None or len(uncovered) < len(best[3]):
            best = (list(untouched), list(unfrozen), records,
                    uncovered, proven, rnd)
        if not uncovered or rnd >= EXPAND_MAX_ROUNDS:
            break
        cand = _expansion_candidates(uncovered, untouched, flights_by_fn,
                                     EXPAND_MAX_UNFREEZE)
        if not cand:
            log("expansion: no untouched pairing has a stitch point next "
                "to the uncovered legs — stopping honestly")
            break
        for idx in sorted(cand, reverse=True):     # pop from the tail first
            u = untouched.pop(idx)
            unfrozen.append(u)
            pool.extend(str(x) for x in u.get("active_fns") or [])
        pool = sorted(set(pool))
        log(f"expansion after round {rnd}: unfroze {len(cand)} pairing(s) "
            f"({', '.join(u['old_pid'] for u in unfrozen[-len(cand):])}) "
            f"— pool grows to {len(pool)} legs")

    bu, bf, br, buc, bp, brd = best
    # A leg certified PROVEN_IMPOSSIBLE by ANY round is a fortiori
    # impossible in the kept round's (subset) pool — fold the discarded
    # rounds' certificates back in, so the stage-2 gate can classify the
    # outcome as a property of the disruption, not a broken incumbent.
    bp = sorted(set(bp) | (proven_all & set(buc)))
    if brd < rnd:
        log(f"kept best round {brd} ({len(buc)} uncovered, {len(bf)} "
            f"unfrozen) over the later round(s) that widened the holes")
    return bu, bf, br, buc, bp, brd


# ------------------------------------------------------------ merge

def merge_catalog(meta: dict, new_records: list, uncovered: list[str],
                  proven_impossible: list[str] | None = None) -> dict:
    """Untouched first (original relative order → deterministic positional
    pids), then the neighbourhood pairings. Employee/pinned ride on the
    untouched records; stage-2 passes them through to Timefold.

    Трек occurrence-идентичности: вход (meta/записи) — addr-пространство,
    выход — WIRE-пространство derived payload'а со штампом wire_map: тогда
    stage-2-ребёнок сверяет штамп и весь его конвейер (синтетика,
    траектория, restore при публикации) работает штатно, без правок."""
    from _leg_rekey import rekey_or_passthrough
    try:
        derived_payload = json.loads(
            Path(meta["derived_payload"]).read_text(encoding="utf-8"))
        wire_payload, wm = rekey_or_passthrough(derived_payload)
    except (KeyError, OSError, json.JSONDecodeError):
        # легаси-meta без derived_payload (старые тесты/потоки по
        # уникальным номерам): passthrough без перевода и валидации
        wire_payload, wm = None, {}
    # полная карта legId -> wire-имя (покрывает и legId УНИКАЛЬНЫХ рейсов —
    # событие могло адресовать их идентичностью)
    leg_to_wire: dict = {}
    valid_wire: set = set()
    for f in (wire_payload or {}).get("flights") or []:
        wfn = str(f.get("flightNumber"))
        valid_wire.add(wfn)
        lid = f.get("flightLegId")
        if isinstance(lid, str) and lid:
            leg_to_wire[lid] = wfn

    def _to_wire(fns) -> list:
        out = [leg_to_wire.get(str(x), str(x)) for x in fns or []]
        if wire_payload is not None:
            bad = [x for x in out if x not in valid_wire]
            if bad:
                raise RuntimeError(
                    f"merge_catalog: адрес не переводится в wire derived "
                    f"payload'а (баг адресации): {bad[:5]}")
        return out

    # Путь Б (ревью 2026-07-24, P2): вендорные каталоги с пер-записным
    # составом («seats») ремонтом пока НЕ поддерживаются — заморозка
    # untouched-экипажа в seats-ветке синтетики не реализована, а молчаливое
    # отбрасывание seats деградировало бы дочерний stage-2 в C01-отказ,
    # замаскированный под крах, и дальше в greedy-стаб. Честный громкий
    # отказ вместо тихой деградации.
    if any(isinstance(u, dict) and u.get("seats")
           for u in meta.get("untouched") or []):
        raise RuntimeError(
            "merge_catalog: ремонт каталогов с пер-записным составом "
            "(seats, путь Б) пока не поддерживается — пересоберите ростер "
            "полным этапом 2 вместо ремонта")
    selected = []
    for u in meta.get("untouched") or []:
        selected.append({
            "fns": _to_wire(u.get("fns")),
            "active_fns": _to_wire(u.get("active_fns")),
            "cost": u.get("cost"),
            "employee": u.get("employee"),
            "pinned": bool(u.get("pinned")),
        })
    for r in new_records:
        selected.append({
            "fns": _to_wire(r.get("fns")),
            "active_fns": _to_wire(r.get("active_fns")
                                   if r.get("active_fns") is not None
                                   else r.get("fns") or []),
            "cost": r.get("cost"),
        })
    out_doc = {
        "label": meta.get("derived_label"),
        "source": "disruption-repair",
        "base_label": meta.get("base_label"),
        "ip_model": "repair_merge",
        # lane inheritance: a rebuild OF a repair instance must run its
        # neighbourhood in the same space as the original catalog
        "profile": meta.get("profile"),
        "bases": (str(meta["bases"]).split(",")
                  if meta.get("bases") else None),
        "created_at": _now(),
        "cancelled": list(meta.get("cancelled") or []),
        "delayed": dict(meta.get("delayed") or {}),
        # uncovered/proven — в wire (как у colgen-каталогов): гейт stage-2
        # переводит их обратно в leg-id через штамп wire_map.
        "uncovered_legs": _to_wire(uncovered),
        # The stage-2 gate reads this: missing legs that are individually
        # PROVEN impossible in the (restricted) repair space classify the
        # run as INFEASIBLE_INPUT_OR_MODEL, not as a broken incumbent.
        "proven_impossible_legs": _to_wire(proven_impossible or []),
        "selected": selected,
    }
    if wm:
        out_doc["wire_map"] = wm
    return out_doc


# ------------------------------------------------------------ stage 2

def kotlin_jar() -> Path:
    return ROOT / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"


def run_stage2(meta: dict, catalog_p: Path) -> dict | None:
    """Манифест ЭТОГО прогона этапа 2 (dict) или None, если этап 2 не
    опубликовал вердикт (настоящий крах).

    P0-1 (аудит документации 2026-07-20): при не-OK вердикте канонический
    артефакт больше не переписывается, поэтому признак завершения — per-run
    манифест (публикуется при любом вердикте). Не-OK вердикт этапа 2 —
    это ВЕРДИКТ, а не крах: greedy-фолбэк поверх него не запускается."""
    label = meta["derived_label"]
    started = time.time()
    cmd = [PY, str(ROOT / "_gerad_stage2_roster.py"),
           "--payload", meta["derived_payload"],
           "--catalog", str(catalog_p),
           "--label", label,
           str(int(meta.get("budget_s") or 120)),
           str(int(meta.get("seed") or 7))]
    log("stage 2 (pinned re-assignment): " + " ".join(cmd[1:]))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    log(f"stage 2 exited {proc.returncode}")

    from _roster_manifest import run_manifest_dir
    mdir = run_manifest_dir(label)

    def _load(p: Path) -> dict | None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    rid = os.environ.get("CREW_RUN_ID")
    if rid:
        p = mdir / f"{rid}.json"
        if p.exists():
            return _load(p)
        # Ревью P0 (2026-07-20): отказ записи манифеста ребёнком не должен
        # трактоваться как крах (иначе greedy лёг бы ПОВЕРХ успешного
        # прогона) — фолбэк P1-09: штамп run_id в каноническом артефакте
        # (не-OK ребёнок канон не трогает, чужой прогон не реанимируется).
        art = _data_paths.results_dir() / f"gerad_stage2_assignment_{label}.json"
        data = _load(art)
        if data is not None and data.get("run_id") == rid:
            return {"result_status": data.get("result_status"),
                    "artifacts": {"assignment_path":
                                  f"final-report/{art.name}"},
                    "manifest_missing": True}
        return None
    # ручной CLI без CREW_RUN_ID: свежайший манифест метки этого запуска
    try:
        cands = [p for p in mdir.glob("*.json")
                 if p.stat().st_mtime >= started]
    except OSError:
        cands = []
    if cands:
        return _load(max(cands, key=lambda p: p.stat().st_mtime))
    # прогоны до фикса (ребёнок без манифестов): канонический артефакт
    # по строгому mtime — синтезируем минимальный манифест
    artifact = _data_paths.results_dir() / f"gerad_stage2_assignment_{label}.json"
    try:
        if artifact.stat().st_mtime >= started:
            data = _load(artifact) or {}
            return {"result_status": data.get("result_status"),
                    "artifacts": {"assignment_path":
                                  f"final-report/{artifact.name}"},
                    "legacy_synthesized": True}
    except OSError:
        pass
    return None


def greedy_fallback(meta: dict, merged: dict) -> dict:
    """No solver jar (or stage 2 crashed): build a stub roster —
    untouched crew kept, new pairings unassigned — then run the Phase-A
    greedy repair (employee=None counts as broken there) and bake its
    assignments in. Honest label: REPAIR_GREEDY.

    P0-1: stub пишется в ДИАГНОСТИЧЕСКИЙ путь (runs/stage2-diagnostics),
    канонический артефакт не переписывается, манифест публикуется с
    auto_promote=False — greedy-ремонт больше не подменяет активный
    ростер; его результат виден в отчёте ремонта и предупреждении
    «последний прогон не-OK» в Результатах."""
    label = meta["derived_label"]
    diag = _data_paths.results_dir() / "runs" / "stage2-diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    artifact = diag / (f"{label}-{time.strftime('%Y%m%d-%H%M%S')}"
                       f"-assignment.json")
    pairings = []
    for i, rec in enumerate(merged["selected"]):
        pairings.append({
            "pid": f"C_{i:06d}",
            "employee": rec.get("employee"),
            "fns": list(rec.get("fns") or []),
            "active_fns": list(rec.get("active_fns") or []),
        })
    doc = {
        "inst": None,
        "label": label,
        "stage2_score": None,
        "catalog": {"file": Path(meta["catalog_out"]).name,
                    "ip_objective": None, "source": "disruption-repair"},
        "pairings": pairings,
        "result_status": "REPAIR_GREEDY",
        "repair_mode": "greedy",
    }
    # Трек occurrence-идентичности: fns stub-дока — wire merged-каталога;
    # без штампа wire_map доска derived-инстанса потеряла бы участки.
    if merged.get("wire_map"):
        doc["wire_map"] = merged["wire_map"]
    artifact.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    from webapp import disruption, registry
    inst = registry.get_instance(meta["derived_id"])
    if inst is None:
        log("WARNING: derived instance not visible to the registry — "
            "greedy pass skipped, new pairings stay unassigned")
        _publish_greedy_manifest(meta, artifact, doc)
        return doc
    # the catalog's frozen pairings must stay frozen in the greedy path
    # too — the swap search may not re-crew them (Phase-B audit)
    frozen = {f"C_{i:06d}" for i, rec in enumerate(merged["selected"])
              if rec.get("pinned") and rec.get("employee")}
    try:
        plan = disruption.build_repair_plan(meta["derived_id"], inst,
                                            frozen_pids=frozen)
    except disruption.DisruptionError as exc:
        log(f"WARNING: greedy plan failed ({exc}) — new pairings stay "
            "unassigned")
        _publish_greedy_manifest(meta, artifact, doc)
        return doc
    if plan.get("available"):
        # Ревью хвоста (2026-07-20): плоский стаб держит ОДНО место на
        # пейринг; при нескольких сломанных местах одного пейринга берём
        # ПЕРВОЕ (slot 0 — идёт первым по сортировке целей), чтобы правка
        # второго места не затирала первое (setdefault, не last-wins).
        to_by_pid: dict = {}
        for mv in plan.get("apply_sequence") or []:
            to_by_pid.setdefault(mv["pid"], mv["to"])
        hit = 0
        for p in pairings:
            if p["pid"] in to_by_pid:
                p["employee"] = to_by_pid[p["pid"]]
                p["via"] = "greedy"
                hit += 1
        log(f"greedy fallback assigned {hit} pairings "
            f"({len(plan.get('uncovered') or [])} uncovered)")
        artifact.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    _publish_greedy_manifest(meta, artifact, doc)
    return doc


def _publish_greedy_manifest(meta: dict, artifact: Path, doc: dict) -> None:
    """Per-run манифест greedy-пути. P0-1: auto_promote=False — стаб с
    вердиктом REPAIR_GREEDY не подменяет активный ростер (канонический
    артефакт не переписывается, прежний годный ростер остаётся целым);
    видимость greedy-ремонта обеспечивают отчёт ремонта и предупреждение
    last_run_not_ok активного ростера."""
    try:
        import rules_config as _rc
        from _roster_manifest import build_run_manifest, publish
        manifest = build_run_manifest(
            label=meta["derived_label"], mode="repair", engine="greedy",
            result_status=str(doc.get("result_status")),
            payload_path=Path(meta["derived_payload"]),
            rules_snapshot=_rc.effective(),
            assignment_path=artifact, solution_path=None,
            catalog_path=Path(meta["catalog_out"])
            if meta.get("catalog_out") else None)
        publish(manifest, auto_promote=False)
    except Exception as exc:   # noqa: BLE001
        log(f"WARNING: манифест greedy-ремонта не записан: {exc}")


# ------------------------------------------------------------ report

def build_report(meta: dict, merged: dict, uncovered: list[str],
                 mode: str, unfrozen: list[dict] | None = None,
                 rounds: int = 1, doc: dict | None = None) -> dict:
    """P0-1: док ЭТОГО прогона передаётся явно (greedy-возврат или чтение
    по манифесту) — канонический артефакт при не-OK не переписывается и
    может нести старый прогон; чтение файла — только фолбэк."""
    label = meta["derived_label"]
    if doc is None:
        artifact = (_data_paths.results_dir()
                    / f"gerad_stage2_assignment_{label}.json")
        try:
            doc = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
    solved = {str(p.get("pid")): p for p in doc.get("pairings") or []
              if isinstance(p, dict)}

    n_untouched = len(meta.get("untouched") or [])
    pid_map, crew_changes = {}, {}

    def _chg(emp):
        return crew_changes.setdefault(
            emp, {"employee": emp, "lost": [], "gained": []})

    for i, u in enumerate(meta.get("untouched") or []):
        new_pid = f"C_{i:06d}"
        pid_map[u.get("old_pid")] = new_pid
        was = u.get("employee")
        now = (solved.get(new_pid) or {}).get("employee")
        if was != now:                    # unpinned-because-ill re-crews
            if was:
                _chg(was)["lost"].append(new_pid)
            if now:
                _chg(now)["gained"].append(new_pid)
    for b in meta.get("broken") or []:
        if b.get("employee"):
            _chg(b["employee"])["lost"].append(b.get("old_pid"))
    for u in unfrozen or []:                  # Phase C: unfrozen crews
        if u.get("employee"):
            _chg(u["employee"])["lost"].append(u.get("old_pid"))

    # Отчёт — человеко-читаемый И в ОДНОМ пространстве (ревью пакета:
    # раньше uncovered был в addr, proven — в wire, fns — в исходных
    # номерах): всё переводится в исходные номера — wire (F100@2) через
    # штамп, addr-legId через обратную карту того же штампа.
    _wm = merged.get("wire_map") or {}
    _hum = {}
    for w, rec in _wm.items():
        _hum[w] = rec.get("flightNumber", w)
        if rec.get("flightLegId"):
            _hum[rec["flightLegId"]] = rec.get("flightNumber", w)

    def _human(fns) -> list:
        return [_hum.get(str(x), str(x)) for x in (fns or [])]

    new_pairings, unassigned = [], []
    for i in range(n_untouched, len(merged["selected"])):
        pid = f"C_{i:06d}"
        sp = solved.get(pid) or {}
        emp = sp.get("employee")
        rec = merged["selected"][i]
        new_pairings.append({
            "pid": pid, "employee": emp,
            "fns": _human(rec.get("fns")),
            "via": sp.get("via") or mode,
        })
        if emp:
            _chg(emp)["gained"].append(pid)
        else:
            unassigned.append(pid)

    return {
        "base": {"id": meta.get("base_id"), "label": meta.get("base_label")},
        "derived": {"id": meta.get("derived_id"), "label": label},
        "created_at": _now(),
        "mode": mode,
        "events": {"cancelled": _human(meta.get("cancelled")),
                   "delayed": {_hum.get(str(k), str(k)): v
                               for k, v in
                               (meta.get("delayed") or {}).items()}},
        "before": {
            "pairings_total": (n_untouched + len(unfrozen or [])
                               + len(meta.get("broken") or [])),
            "untouched": n_untouched,
            "broken": [{"pid": b.get("old_pid"),
                        "employee": b.get("employee"),
                        "legs": len(b.get("fns") or [])}
                       for b in meta.get("broken") or []],
        },
        # Phase C: which frozen pairings the expansion gave back to the
        # pool, and how many colgen rounds it took
        "expansion": {
            "rounds": rounds,
            "unfrozen": [{"pid": u.get("old_pid"),
                          "employee": u.get("employee"),
                          "legs": len(u.get("fns") or [])}
                         for u in unfrozen or []],
        },
        "after": {
            "pairings_total": len(merged["selected"]),
            # count pins actually HELD in the published artifact, not the
            # plan's intent — an honest number when anything re-crewed
            "pinned": sum(
                1 for i, u in enumerate(meta.get("untouched") or [])
                if u.get("pinned")
                and (solved.get(f"C_{i:06d}") or {}).get("employee")
                    == u.get("employee")),
            "new_pairings": new_pairings,
            "unassigned": unassigned,
            "uncovered_legs": _human(uncovered),
            "proven_impossible": list(
                merged.get("proven_impossible_legs") or []),
        },
        "pid_map": pid_map,
        "crew_changes": sorted(
            (c for c in crew_changes.values() if c["lost"] or c["gained"]),
            key=lambda c: c["employee"]),
        "stage2": {"score": doc.get("stage2_score"),
                   "result_status": doc.get("result_status")},
    }


# ------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meta", required=True,
                    help="meta.json from webapp.disruption.prepare_rebuild")
    args = ap.parse_args(argv)
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    # Трек occurrence-идентичности: повторные номера поддержаны (события в
    # addr-пространстве, окрестность по addr, merged-каталог в derived-wire
    # со штампом). Отказ остаётся ТОЛЬКО когда биекция не строится.
    try:
        from _leg_rekey import needs_rekey, rekey_blockers
        _pl = json.loads(Path(meta["derived_payload"])
                         .read_text(encoding="utf-8"))
        if needs_rekey(_pl):
            _blk = rekey_blockers(_pl)
            if _blk:
                log("!! повторные номера derived payload'а не адресуемы: "
                    + "; ".join(_blk[:3]))
                return 3
    except (OSError, json.JSONDecodeError, KeyError):
        pass                          # честные отказы ниже по конвейеру
    workdir = Path(meta["workdir"])
    workdir.mkdir(parents=True, exist_ok=True)
    label = meta["derived_label"]
    log(f"rebuild {meta.get('base_label')} -> {label}: "
        f"{len(meta.get('cancelled') or [])} cancelled, "
        f"{len(meta.get('delayed') or {})} delayed, "
        f"{len(meta.get('broken') or [])} pairings dissolved, "
        f"{len(meta.get('recover_fns') or [])} legs to re-cover")

    (untouched, unfrozen, new_records,
     uncovered, proven, rounds) = expand_neighbourhood(meta, workdir)
    # everything downstream sees the POST-expansion untouched list: the
    # unfrozen pairings are dissolved, their crews free, their legs re-cut
    meta_eff = {**meta, "untouched": untouched}
    merged = merge_catalog(meta_eff, new_records, uncovered, proven)
    catalog_p = Path(meta["catalog_out"])
    catalog_p.parent.mkdir(parents=True, exist_ok=True)
    catalog_p.write_text(json.dumps(merged, indent=1), encoding="utf-8")
    log(f"merged catalog: {len(merged['selected'])} pairings "
        f"({len(untouched)} untouched-pinned, {len(unfrozen)} unfrozen, "
        f"{len(new_records)} new) -> {catalog_p.name}")

    run_doc: dict | None = None
    if kotlin_jar().exists():
        run_manifest = run_stage2(meta_eff, catalog_p)
        mode = "timefold" if run_manifest else "greedy"
        if run_manifest:
            rs = run_manifest.get("result_status")
            ap_rel = (run_manifest.get("artifacts") or {}).get(
                "assignment_path")
            if ap_rel:
                try:
                    run_doc = json.loads(
                        (ROOT / ap_rel).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Ревью P0: артефакт манифеста нечитаем — отчёт строится
                    # из ПУСТОГО дока, а не из старого канонического файла
                    # (build_report(doc=None) читал бы чужой прогон)
                    log(f"WARNING: артефакт прогона нечитаем: {ap_rel}")
                    run_doc = {}
            if rs != "OK":
                log(f"stage 2 verdict {rs} — канонический артефакт не "
                    f"обновлён, активен прежний ростер; диагностика: "
                    f"{ap_rel}")
        else:
            log("stage 2 published no verdict — falling back to greedy")
            run_doc = greedy_fallback(meta_eff, merged)
    else:
        log(f"solver jar missing ({kotlin_jar()}) — greedy fallback")
        mode = "greedy"
        run_doc = greedy_fallback(meta_eff, merged)

    report = build_report(meta_eff, merged, uncovered, mode,
                          unfrozen=unfrozen, rounds=rounds, doc=run_doc)
    report_p = Path(meta["report_path"])
    report_p.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    log(f"report -> {report_p.name} (mode={mode}, "
        f"new={len(report['after']['new_pairings'])}, "
        f"unassigned={len(report['after']['unassigned'])}, "
        f"uncovered={len(uncovered)})")

    # P0-1: успех = «этот прогон опубликовал вердикт» (манифест либо
    # greedy-док) — существование канонического файла больше не критерий
    # (первый не-OK прогон derived-метки честно возвращал бы 2)
    return 0 if run_doc is not None else 2


if __name__ == "__main__":
    sys.exit(main())
