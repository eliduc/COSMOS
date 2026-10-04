"""Данные визуализации пейринга («лента цепочек» + «карта сети»).

Всё здесь — read-only поверх ГОТОВЫХ артефактов этапа 1: каталога
цепочек (gerad_colgen_selected_<label>.json) и payload'а набора. Ничего
не пересчитывается и не приукрашивается; нет каталога — секции нет
(маршрут отвечает 404, не фабрикует пустышку).

Источник выбирается КАК У КАРТОЧКИ ЭТАПА 1 (results._stage1 читает
канонический дамп): сперва FINAL_REPORT/gerad_colgen_selected_<label>.json,
затем — реестровый catalog_path (best-каталог) как фолбэк. Иначе стоимость
на ленте могла бы разойтись с ip_objective карточки, когда best и
канонический дамп — разные файлы.

В природе ДВЕ формы записей selected[]:
  (а) построитель цепочек — fns, active_fns, duty_fns (готовые смены),
      cost, sourceUnit + шапка coverage/proven_impossible_legs/wire_map;
  (б) линия генерации колонок — ТОЛЬКО fns, active_fns, cost.
Для формы (б) смены делятся эвристикой разрыва >= REST_MIN минут — тем же
порогом RV_REST_MIN=600, которым UI уже делит ростер на дежурства
(webapp/static/app.js). Эвристика ЧЕСТНО помечается в ответе
(meta.duty_split = 'gap600'), чтобы не выдать её за структуру источника.

Перегон (deadhead) = fn из fns, не входящий в active_fns — та же
семантика, что в results.build_roster_csv/roster_view.

Непокрытые выводятся МНОЖЕСТВОМ (обязательства payload − объединение
active_fns всех цепочек): рейсы-транспорт (transportOnly) и типы вне
автоматического планирования (coverage.auto_plan_excluded) обязательствами
не являются. В старых colgen-каталогах auto_plan_excluded нет — там
исключаются только transportOnly.

Дуги карты (arcs) агрегируются по ВСЕМ цепочкам ДО каппинга ленты плюс по
непокрытым — иначе карта врала бы при обрезанном Ганте.
"""
from __future__ import annotations

import threading
from datetime import date, datetime

from . import registry, results
from .registry import FINAL_REPORT

# Кап строк ленты: uz-full несёт 2330 цепочек — без серверного каппинга
# и SVG, и JSON распухают. Карта (arcs/nodes/uncovered) считается по
# полному набору, обрезаются только chains.
MAX_CHAINS = 600

# Порог межсменного отдыха (минуты) для формы (б) — ровно RV_REST_MIN
# из webapp/static/app.js: обе стороны обязаны делить одинаково.
REST_MIN = 600

_cache_lock = threading.Lock()
# str(catalog path) -> (ключ (пути+mtime_ns), готовый ответ) — по одному
# слоту на каталог, как у соседних mtime-кешей реестра.
_view_cache: dict[str, tuple[tuple, dict]] = {}


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _gap_min(prev_arr: str, next_dep: str) -> float:
    return (_dt(next_dep) - _dt(prev_arr)).total_seconds() / 60.0


def _catalog_file(inst: dict):
    """Файл каталога цепочек: канонический дамп, затем реестровый
    catalog_path (см. докстринг модуля). None, когда каталога нет."""
    p = FINAL_REPORT / f"gerad_colgen_selected_{inst['label']}.json"
    if p.is_file():
        return p
    rel = inst.get("catalog_path")
    if rel:
        q = registry.resolve_rel(rel)
        if q.is_file():
            return q
    return None


def _duties_from_catalog(legs: list[dict], duty_of: dict[str, int]) -> None:
    """Разметить legs[i]['duty'] по duty_fns построителя. Перегоны в
    duty_fns не входят — они наследуют смену хронологического соседа
    (ведущие — первую известную), дыр разметка не оставляет."""
    known = [duty_of.get(x["leg"]) for x in legs]
    first = next((d for d in known if d is not None), 0)
    cur = first
    for i, x in enumerate(legs):
        if known[i] is not None:
            cur = known[i]
        x["duty"] = cur


def _duties_by_gap(legs: list[dict]) -> None:
    """Разметить legs[i]['duty'] эвристикой разрыва >= REST_MIN минут."""
    duty = 0
    for i, x in enumerate(legs):
        if i and _gap_min(legs[i - 1]["arr_utc"], x["dep_utc"]) >= REST_MIN:
            duty += 1
        x["duty"] = duty


def pairing_view(inst: dict) -> dict | None:
    """Ответ GET /api/results/{id}/pairing (см. докстринг модуля и
    webapp/API-CONTRACT.md). None, когда каталога цепочек ещё нет —
    маршрут отвечает 404, фронт показывает пустое состояние."""
    path = _catalog_file(inst)
    if path is None:
        return None
    payload_path = registry.instance_payload_path(inst)
    try:
        key = (str(path), path.stat().st_mtime_ns,
               str(payload_path), payload_path.stat().st_mtime_ns)
    except OSError:
        return None
    ck = str(path)
    with _cache_lock:
        hit = _view_cache.get(ck)
        if hit and hit[0] == key:
            return hit[1]

    catalog = registry._load_json_cached(path)
    if not isinstance(catalog, dict) or \
            not isinstance(catalog.get("selected"), list):
        return None
    payload = registry.load_payload(payload_path)
    flights = results._flights_by_identity(payload, catalog)

    records = [r for r in catalog["selected"] if isinstance(r, dict)]
    chains: list[dict] = []
    covered: set[str] = set()          # канонические flightNumber
    arc_map: dict[tuple[str, str], list[int]] = {}   # (a,b) -> [n, ndh, nunc]
    unresolved = 0
    catalog_duties = bool(records)     # все ли смены пришли из duty_fns
    for idx, rec in enumerate(records):
        fns = rec.get("fns") or []
        active = set(rec.get("active_fns", fns))
        legs = []
        for fn in fns:
            f = flights.get(fn)
            if f is None:
                # Разрыв ссылочности артефакт-payload: не роняем витрину,
                # считаем и показываем числом (meta.unresolved_legs).
                unresolved += 1
                continue
            dh = fn not in active
            if not dh:
                covered.add(f["flightNumber"])
            legs.append({
                "leg": fn,
                "dep": f["departureAirport"],
                "dep_utc": f["departureUTCDateTime"],
                "arr": f["arrivalAirport"],
                "arr_utc": f["arrivalUTCDateTime"],
                "dh": dh,
            })
            a = arc_map.setdefault(
                (f["departureAirport"], f["arrivalAirport"]), [0, 0, 0])
            a[0] += 1
            if dh:
                a[1] += 1
        if not legs:
            continue
        legs.sort(key=lambda x: x["dep_utc"])
        duty_fns = rec.get("duty_fns")
        if isinstance(duty_fns, list) and duty_fns:
            duty_of = {fn: di for di, dl in enumerate(duty_fns)
                       for fn in (dl or [])}
            _duties_from_catalog(legs, duty_of)
        else:
            catalog_duties = False
            _duties_by_gap(legs)
        span = (date.fromisoformat(legs[-1]["arr_utc"][:10])
                - date.fromisoformat(legs[0]["dep_utc"][:10])).days + 1
        chains.append({
            "cid": rec.get("sourceUnit") or f"P{idx + 1}",
            "base": legs[0]["dep"],
            "cost": rec.get("cost"),
            "nlegs": len(legs),
            "ndh": sum(1 for x in legs if x["dh"]),
            "nights": len({x["duty"] for x in legs}) - 1,
            "span": span,
            "legs": legs,
        })

    # Непокрытые: обязательства payload − покрытое. Имена каталога
    # (auto_plan_excluded, proven_impossible_legs) могут жить в любом из
    # пространств имён — сводим к каноническому через тот же join.
    def _canon(names) -> set[str]:
        return {flights[x]["flightNumber"] for x in (names or [])
                if x in flights}

    cov = catalog.get("coverage")
    cov = cov if isinstance(cov, dict) else {}
    excluded = _canon(cov.get("auto_plan_excluded"))
    proven = _canon(catalog.get("proven_impossible_legs"))
    uncovered = []
    for f in payload.get("flights", []):
        fid = f["flightNumber"]
        if f.get("transportOnly") or fid in excluded or fid in covered:
            continue
        uncovered.append({
            "leg": fid,
            "dep": f["departureAirport"],
            "dep_utc": f["departureUTCDateTime"],
            "arr": f["arrivalAirport"],
            "arr_utc": f["arrivalUTCDateTime"],
            "why": "proven_impossible" if fid in proven else None,
        })
        a = arc_map.setdefault(
            (f["departureAirport"], f["arrivalAirport"]), [0, 0, 0])
        a[0] += 1
        a[2] += 1
    uncovered.sort(key=lambda x: (x["dep_utc"], x["leg"]))

    bases = catalog.get("bases") or sorted(
        {e.get("homeAirport") for e in payload.get("employees", [])
         if e.get("homeAirport")})
    base_set = set(bases)
    arcs = [{"a": a, "b": b, "n": v[0], "ndh": v[1], "nunc": v[2]}
            for (a, b), v in sorted(arc_map.items())]
    codes = sorted({c for ab in arc_map for c in ab})
    nodes = [{"code": c, "base": c in base_set} for c in codes]

    all_legs = [x for ch in chains for x in ch["legs"]] + uncovered
    chains.sort(key=lambda c: (c["base"], c["legs"][0]["dep_utc"], c["cid"]))
    chains_total = len(chains)

    view = {
        "label": inst["label"],
        "run_id": catalog.get("run_id"),
        "ip_status": catalog.get("ip_status"),
        "objective": catalog.get("ip_objective"),
        "meta": {
            "bases": list(bases),
            "t0": min((x["dep_utc"] for x in all_legs), default=None),
            "t1": max((x["arr_utc"] for x in all_legs), default=None),
            "chains_total": chains_total,
            "chains_truncated": chains_total > MAX_CHAINS,
            "legs_total": cov.get("legs_total", inst.get("legs")),
            "duty_split": "catalog" if catalog_duties else "gap600",
            "unresolved_legs": unresolved,
        },
        "chains": chains[:MAX_CHAINS],
        "uncovered": uncovered,
        "arcs": arcs,
        "nodes": nodes,
    }
    with _cache_lock:
        _view_cache[ck] = (key, view)
    return view
