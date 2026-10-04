"""Light crew-cost layer (2026-07-15): allowances-style costing + hotel/
layover analytics over the stage-1 pairing catalog.

Industrial context: full pay-and-credit (duty/trip rigs, guarantees) is the
P0 gap #1 and will come later; this layer implements the components that ARE
simple, honest money — the same "allowances, deadheads and hotel stays"
family industrial systems (Jeppesen/Sabre/NetLine) report and optimize:

  pay        = block hours x block_hour_rate
  per-diem   = TAFB hours x per_diem_hour           (time away from base)
  hotels     = layover nights x station hotel rate  (per-station contracts)

plus the hotel headcount forecast (rooms by station x date) used to contract
room blocks in advance.

Approximations (documented in the UI): times are UTC (no local-timezone
acclimatization until the FTL engine lands); a "night" is a UTC calendar-date
boundary inside a layover gap; one pairing = one crew unit (rooms are counted
in crews, not heads). Config is per-airline (webapp/config/cost_config.json),
validated loudly, PUT is admin-only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .registry import (REPO_ROOT, get_instance, instance_payload_path,
                       load_payload, _load_json_cached, BEST_DIR,
                       FINAL_REPORT)

# Путь к ставкам установки объявлен ОДИН раз — в config_scope (P1-10).
# Здесь его больше нет: два объявления расходились бы, и тестовая изоляция
# чинила бы каждое по отдельности.

# Defaults are deliberately round, editable per airline in the UI.
COST_DEFAULTS: dict = {
    "currency": "USD",
    "block_hour_rate": 120.0,     # pay per block (flight) hour, per crew
    "per_diem_hour": 3.5,         # allowance per TAFB hour, per crew
    "hotel_default_rate": 110.0,  # per room-night when no station override
    "hotel_rates": {},            # {"ATL": 95.0, ...} station overrides
    "deadhead_factor": 0.5,       # pay factor for deadhead hours (future #3)
}
_NUM_FIELDS = ("block_hour_rate", "per_diem_hour", "hotel_default_rate",
               "deadhead_factor")


def load_cost_config(inst=None) -> dict:
    """Ставки, действующие для экземпляра (P1-10, 2026-07-26).

    Без экземпляра — ставки установки, как и было. С экземпляром путь
    выбирает `config_scope`: у набора могут быть свои ставки, и это не
    удобство, а требование — ставки коммерческая тайна авиакомпании, и
    считать стоимость одного набора по ставкам, введённым для другого,
    нельзя."""
    from . import config_scope
    return load_cost_config_from(
        config_scope.path_for(config_scope.DOMAIN_COST, inst))


def load_cost_config_from(path) -> dict:
    """Тот же разбор по ЯВНОМУ пути (симметрично `rules_config.load_from`):
    читатели снимков прогона обязаны нормализовать значения точно так же,
    как писатель, иначе отпечатки расходятся навсегда."""
    if path is None:
        return _merge_cost(None)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return _merge_cost(raw)


def _merge_cost(raw) -> dict:
    cfg = dict(COST_DEFAULTS)
    if isinstance(raw, dict):
        for k in COST_DEFAULTS:
            if k in raw:
                cfg[k] = raw[k]
    # hotel_rates always a clean {str: float}
    rates = cfg.get("hotel_rates")
    cfg["hotel_rates"] = ({str(k).upper(): float(v)
                           for k, v in rates.items()}
                          if isinstance(rates, dict) else {})
    return cfg


def validate_cost_config(body: dict) -> list[str]:
    """Loud field validation for PUT; returns error strings."""
    errors: list[str] = []
    if not isinstance(body, dict):
        return ["config must be a JSON object"]
    cur = body.get("currency")
    if cur is not None and (not isinstance(cur, str)
                            or not (1 <= len(cur.strip()) <= 8)):
        errors.append("'currency' must be a short string (e.g. USD)")
    for f in _NUM_FIELDS:
        v = body.get(f)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            errors.append(f"'{f}' must be a non-negative number")
    rates = body.get("hotel_rates")
    if rates is not None:
        if not isinstance(rates, dict):
            errors.append("'hotel_rates' must be an object {station: rate}")
        else:
            for k, v in rates.items():
                if not isinstance(k, str) or not (2 <= len(k.strip()) <= 5):
                    errors.append(f"hotel_rates: station {k!r} must be a "
                                  f"2-5 char code")
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or v < 0:
                    errors.append(f"hotel_rates[{k}] must be a non-negative "
                                  f"number")
    return errors


def save_cost_config(body: dict) -> dict:
    """Merge + persist (atomic write) and return the effective config."""
    cfg = load_cost_config()
    for k in COST_DEFAULTS:
        if k in body:
            cfg[k] = body[k]
    cfg["hotel_rates"] = {str(k).upper(): float(v)
                          for k, v in (cfg.get("hotel_rates") or {}).items()}
    from . import config_scope
    target = config_scope.install_path(config_scope.DOMAIN_COST)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(target)
    return cfg


# ------------------------------------------------------------------ costing

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def hotel_rate(station: str, cfg: dict) -> float:
    return float(cfg["hotel_rates"].get(str(station).upper(),
                                        cfg["hotel_default_rate"]))


def _nights_between(arr: datetime, dep: datetime) -> list[str]:
    """UTC calendar nights spent in a layover gap [arr, dep): the night of
    date D is counted when the stay covers D 23:59 -> D+1. Approximation: a
    date boundary inside the gap = one night."""
    nights = []
    d = arr.date()
    while True:
        boundary = datetime.combine(d + timedelta(days=1), datetime.min.time())
        if boundary <= dep:
            nights.append(d.isoformat())
            d = d + timedelta(days=1)
        else:
            break
    return nights


def pairing_costing(legs: list[dict], cfg: dict) -> dict:
    """Cost components for one pairing (legs = payload flight dicts in
    chronological order). Returns block/tafb hours, per-diem, hotel nights
    [{station, date, rate}], costs and total."""
    legs = sorted(legs, key=lambda f: f["departureUTCDateTime"])
    block_h = 0.0
    for f in legs:
        block_h += (_dt(f["arrivalUTCDateTime"])
                    - _dt(f["departureUTCDateTime"])).total_seconds() / 3600
    start = _dt(legs[0]["departureUTCDateTime"])
    end = _dt(legs[-1]["arrivalUTCDateTime"])
    tafb_h = (end - start).total_seconds() / 3600
    nights: list[dict] = []
    for prev, nxt in zip(legs, legs[1:]):
        arr = _dt(prev["arrivalUTCDateTime"])
        dep = _dt(nxt["departureUTCDateTime"])
        station = prev.get("arrivalAirport") or ""
        for d in _nights_between(arr, dep):
            nights.append({"station": station, "date": d,
                           "rate": hotel_rate(station, cfg)})
    pay = block_h * float(cfg["block_hour_rate"])
    per_diem = tafb_h * float(cfg["per_diem_hour"])
    hotels = sum(n["rate"] for n in nights)
    return {
        "block_h": round(block_h, 2),
        "tafb_h": round(tafb_h, 2),
        "nights": nights,
        "pay_cost": round(pay, 2),
        "per_diem_cost": round(per_diem, 2),
        "hotel_cost": round(hotels, 2),
        "total_cost": round(pay + per_diem + hotels, 2),
    }


def _catalog_doc(inst: dict) -> dict | None:
    """Полный документ каталога (best побеждает) — включая wire_map
    Пакета B (ревью пакета: без него деньги/спрос молча теряли
    re-key-нутые участки)."""
    label = inst["label"]
    for p in (BEST_DIR / f"gerad_best_catalog_{label}.json",
              FINAL_REPORT / f"gerad_colgen_selected_{label}.json"):
        doc = _load_json_cached(p)
        if isinstance(doc, dict) and isinstance(doc.get("selected"), list):
            return doc
        if isinstance(doc, list):                     # best-catalog dumps
            return {"selected": doc}
    return None


def _catalog_pairings(inst: dict) -> list[dict] | None:
    """Selected pairings for the instance's label (best catalog wins),
    each {'fns': [...]}. None when no catalog exists yet."""
    doc = _catalog_doc(inst)
    return doc["selected"] if doc else None


def instance_costing(inst_id: str) -> dict | None:
    """Full costing report for an instance's current pairing catalog:
    totals, per-pairing rows (top by cost), hotel nights by station and the
    rooms forecast (station x date). None when instance/catalog missing.

    Разрешает id через реестр — это ОДИН полный обход выгрузок. Тот, у
    кого запись реестра уже на руках (ручка после проверки владения,
    перечень наборов), зовёт `instance_costing_for(inst)` напрямую."""
    inst = get_instance(inst_id)
    if inst is None:
        return None
    return instance_costing_for(inst)


def instance_costing_for(inst: dict) -> dict:
    """Тот же отчёт по УЖЕ разрешённой записи реестра.

    К реестру не обращается: запись — единица работы, а разрешение id в
    запись — дело границы (ручки, перечня). Прежде расчёт разрешал id сам,
    и отчёт по парку получал ПОЛНЫЙ обход всех выгрузок на КАЖДЫЙ набор:
    при 573 выгрузках на машине разработчика один отчёт занимал минуты и
    выглядел зависанием (test_kpi_endpoints, 2026-09-04)."""
    inst_id = inst["id"]
    pairings = _catalog_pairings(inst)
    if not pairings:
        return {"available": False, "reason": "no_catalog"}
    cfg = load_cost_config(inst)
    # What-if scenarios may override headline rates (lazy import — the
    # scenarios module imports costing at module level).
    from . import scenarios
    for k, v in scenarios.cost_overrides_for(inst_id).items():
        cfg[k] = v
    payload = load_payload(instance_payload_path(inst))
    from . import roster_board
    by_fn = roster_board.flights_by_fn(payload, _catalog_doc(inst))

    rows = []
    tot = {"block_h": 0.0, "tafb_h": 0.0, "pay_cost": 0.0,
           "per_diem_cost": 0.0, "hotel_cost": 0.0, "total_cost": 0.0,
           "nights": 0}
    by_station: dict[str, dict] = {}
    forecast: dict[str, dict[str, int]] = {}   # date -> station -> rooms
    skipped = 0
    for i, p in enumerate(pairings):
        fns = p.get("fns") or []
        legs = [by_fn[x] for x in fns if x in by_fn]
        if len(legs) < 1 or len(legs) != len(fns):
            skipped += 1
            continue
        c = pairing_costing(legs, cfg)
        for k in ("block_h", "tafb_h", "pay_cost", "per_diem_cost",
                  "hotel_cost", "total_cost"):
            tot[k] += c[k]
        tot["nights"] += len(c["nights"])
        for n in c["nights"]:
            st = by_station.setdefault(
                n["station"], {"nights": 0, "cost": 0.0,
                               "rate": n["rate"]})
            st["nights"] += 1
            st["cost"] += n["rate"]
            forecast.setdefault(n["date"], {})
            forecast[n["date"]][n["station"]] = \
                forecast[n["date"]].get(n["station"], 0) + 1
        rows.append({"pairing": i + 1, "legs": len(legs), **{
            k: c[k] for k in ("block_h", "tafb_h", "pay_cost",
                              "per_diem_cost", "hotel_cost", "total_cost")},
            "nights": len(c["nights"])})

    rows.sort(key=lambda r: -r["total_cost"])
    for k in tot:
        tot[k] = round(tot[k], 2)
    stations = [{"station": s, "nights": v["nights"],
                 "rate": round(v["rate"], 2), "cost": round(v["cost"], 2)}
                for s, v in sorted(by_station.items(),
                                   key=lambda kv: -kv[1]["cost"])]
    forecast_rows = [{"date": d,
                      "stations": dict(sorted(sts.items())),
                      "rooms": sum(sts.values())}
                     for d, sts in sorted(forecast.items())]
    return {
        "available": True,
        "instance": inst_id,
        "label": inst["label"],
        "currency": cfg["currency"],
        "config": {k: cfg[k] for k in ("block_hour_rate", "per_diem_hour",
                                        "hotel_default_rate")},
        "pairings": len(pairings),
        "skipped": skipped,
        "totals": tot,
        "top_pairings": rows[:15],
        "hotel_by_station": stations,
        "rooms_forecast": forecast_rows,
        "note_utc": True,     # UI shows the UTC-nights approximation note
    }
