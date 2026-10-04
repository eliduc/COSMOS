"""Crew cost / utilization KPIs (2026-07-15, gap item #12).

The KPI set follows what industrial crew systems put on management
dashboards, restricted to what our data honestly supports today:

  unit cost per block hour   — THE industry unit-economics number
  productivity (block/TAFB)  — share of paid away-time actually flown
  cost mix                   — pay / per-diem / hotel shares
  avg pairing block, nights per pairing
  roster coverage %, unassigned, vacation violations, preference hits
  crew utilization           — block hours per rostered crew member

Plan-vs-fact is NOT pretended: we have no operations feed yet (gap #17), so
the fleet view compares instances/catalogs side by side instead. Sources:
costing.instance_costing (catalog money), the stage-2 journal
(gerad_phase2_two_stage.json, last row per label) and payload stats.
"""
from __future__ import annotations

from . import costing
from .registry import (TWO_STAGE_JOURNAL, _load_json_cached, get_instance,
                       list_instances)


def _stage2_row(label: str) -> dict | None:
    """Last stage-2 journal row for the label (same convention as the
    registry gate lookup: later rows win)."""
    journal = _load_json_cached(TWO_STAGE_JOURNAL)
    out = None
    if isinstance(journal, list):
        for row in journal:
            if not isinstance(row, dict):
                continue
            row_label = row.get("label")
            if row_label is None and row.get("inst") is not None:
                row_label = f"inst{int(row['inst'])}"
            if row_label == label:
                out = row
    return out


def _pct(num, den):
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)) \
            or den <= 0:
        return None
    return round(num / den * 100.0, 1)


def _div(num, den, digits=2):
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)) \
            or den <= 0:
        return None
    return round(num / den, digits)


def instance_kpi(inst_id: str) -> dict | None:
    """KPI record for one instance. None when the instance is unknown;
    {'available': False} when there is no stage-1 catalog yet.

    Разрешает id через реестр (один полный обход выгрузок). С записью на
    руках зовите `instance_kpi_for(inst)` — см. там."""
    inst = get_instance(inst_id)
    if inst is None:
        return None
    return instance_kpi_for(inst)


def instance_kpi_for(inst: dict) -> dict:
    """KPI по УЖЕ разрешённой записи реестра; реестр не трогает.

    Принцип: запись реестра — единица работы, id разрешается один раз на
    границе (ручка, перечень). Отчёт по парку прежде звал `instance_kpi`
    по id, а тот — `get_instance` и `costing.instance_costing` по id, то
    есть ДВА полных обхода реестра на каждый набор: при 573 выгрузках
    (остатки тестов в data/uploads) один отчёт шёл минуты, и
    test_kpi_endpoints выглядел зависшим (2026-09-04)."""
    inst_id = inst["id"]
    cost = costing.instance_costing_for(inst)
    if not cost.get("available"):
        return {"available": False, "instance": inst_id,
                "reason": (cost or {}).get("reason", "no_catalog")}
    t = cost["totals"]
    s2 = _stage2_row(inst["label"])
    slots = inst.get("slots")
    unassigned = s2.get("unassigned") if s2 else None
    assigned = (slots - unassigned
                if isinstance(slots, int) and isinstance(unassigned, int)
                else None)
    employees = inst.get("employees")
    kpi = {
        "available": True,
        "instance": inst_id,
        "label": inst["label"],
        "currency": cost["currency"],
        # money
        "total_cost": t["total_cost"],
        "unit_cost_block_h": _div(t["total_cost"], t["block_h"]),
        "cost_mix_pay_pct": _pct(t["pay_cost"], t["total_cost"]),
        "cost_mix_perdiem_pct": _pct(t["per_diem_cost"], t["total_cost"]),
        "cost_mix_hotel_pct": _pct(t["hotel_cost"], t["total_cost"]),
        # pairing structure / productivity
        "block_h": t["block_h"],
        "tafb_h": t["tafb_h"],
        "productivity_pct": _pct(t["block_h"], t["tafb_h"]),
        "pairings": cost["pairings"],
        "avg_block_per_pairing_h": _div(t["block_h"], cost["pairings"]),
        "nights_per_pairing": _div(t["nights"], cost["pairings"]),
        # roster side (may be absent before a stage-2 run)
        "roster_available": s2 is not None,
        "coverage_pct": _pct(assigned, slots),
        "unassigned": unassigned,
        "vacation_violations": s2.get("vacation_violations") if s2 else None,
        "pref_hits": s2.get("pref_hits") if s2 else None,
        "gate_status": s2.get("result_status") if s2 else None,
        "crew_utilization_block_h": _div(t["block_h"], employees, 1),
        "employees": employees,
    }
    return kpi


def fleet_kpi(user: dict | None = None) -> list[dict]:
    """KPI rows for every instance visible to the user (None = all — the
    caller must have authorized the wider scope). Instances without a
    stage-1 catalog are skipped (nothing to measure).

    Реестр обходится РОВНО один раз; каждая запись считается по себе
    (`instance_kpi_for`), без повторного разрешения по id — стоимость
    отчёта линейна по числу наборов. Гейт: test_fleet_kpi_scans_registry_once."""
    from .registry import visible_to
    rows = []
    for inst in list_instances():
        if user is not None and not visible_to(inst, user):
            continue
        k = instance_kpi_for(inst)
        if k.get("available"):
            rows.append(k)
    rows.sort(key=lambda r: -(r.get("total_cost") or 0))
    return rows
