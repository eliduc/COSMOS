"""Тесты webapp/pairing_viz.py на СИНТЕТИЧЕСКОМ каталоге (без живых
артефактов): смены из duty_fns против эвристики разрыва, перегон =
fns − active_fns, вывод непокрытых с учётом transportOnly и
auto_plan_excluded, разрешение wire-имён, серверный каппинг цепочек с
полным агрегатом дуг, кэш по (пути, mtime).

pairing_view вызывается напрямую (unit): payload_path в inst — абсолютный
(registry.resolve_rel принимает абсолютные записи как есть), каталог
кладётся в tmp-FINAL_REPORT через monkeypatch модуля. Маршрут и живой
артефакт inst1 покрыты в tests/test_webapp_api.py, тенантный гейт — в
tests/test_tenancy.py."""
import json

import pytest

from webapp import pairing_viz


def _f(fn, dep, dep_utc, arr, arr_utc, **extra):
    return {"flightNumber": fn, "departureAirport": dep,
            "departureUTCDateTime": dep_utc, "arrivalAirport": arr,
            "arrivalUTCDateTime": arr_utc, **extra}


# Payload: AAA — база; F5 — транспорт (не обязательство), F6 — вне
# автоматического планирования, F7 — доказуемо непокрываемый, F8 живёт в
# каталоге под wire-именем FW1@1, F9 — единственный рейс усечённой позже
# цепочки (уникальная дуга CCC->BBB для проверки полноты агрегата).
PAYLOAD = {
    "airports": [{"code": c} for c in ("AAA", "BBB", "CCC")],
    "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Pilot"]}],
    "flights": [
        _f("F1", "AAA", "2026-07-01T06:00:00Z", "BBB",
           "2026-07-01T08:00:00Z"),
        _f("F2", "BBB", "2026-07-01T09:00:00Z", "CCC",
           "2026-07-01T10:00:00Z"),
        _f("F3", "CCC", "2026-07-02T08:00:00Z", "AAA",
           "2026-07-02T10:00:00Z"),
        _f("F4", "BBB", "2026-07-01T12:00:00Z", "AAA",
           "2026-07-01T13:00:00Z"),
        _f("F5", "AAA", "2026-07-01T14:00:00Z", "CCC",
           "2026-07-01T16:00:00Z", transportOnly=True),
        _f("F6", "CCC", "2026-07-01T11:00:00Z", "BBB",
           "2026-07-01T12:00:00Z"),
        _f("F7", "AAA", "2026-07-03T06:00:00Z", "BBB",
           "2026-07-03T08:00:00Z"),
        _f("F8", "BBB", "2026-07-02T12:00:00Z", "AAA",
           "2026-07-02T13:00:00Z"),
        _f("F9", "CCC", "2026-07-01T18:00:00Z", "BBB",
           "2026-07-01T19:00:00Z"),
    ],
    "flightAssignments": [],
}

# Форма (а) — построитель цепочек: duty_fns (готовые смены), coverage с
# auto_plan_excluded, proven_impossible_legs, wire_map. F2 едет в U1
# ПЕРЕГОНОМ (в fns, вне active_fns) — покрытием это не является.
CATALOG_BUILDER = {
    "label": "pviz-a", "run_id": "pytest-run", "ip_status": "FEASIBLE",
    "ip_objective": 1501.0, "profile": "pairing-builder",
    "coverage": {"legs_total": 9, "uncovered": 3, "verdict": "OK",
                 "auto_plan_excluded": ["F6"]},
    "proven_impossible_legs": ["F7"],
    "wire_map": {"FW1@1": {"flightNumber": "F8", "flightLegId": None}},
    "selected": [
        {"fns": ["F1", "F2", "F3"], "active_fns": ["F1", "F3"],
         "duty_fns": [["F1"], ["F3"]], "cost": 1000, "sourceUnit": "U1"},
        {"fns": ["FW1@1"], "active_fns": ["FW1@1"],
         "duty_fns": [["FW1@1"]], "cost": 500, "sourceUnit": "U2"},
        {"fns": ["F9"], "active_fns": ["F9"],
         "duty_fns": [["F9"]], "cost": 1, "sourceUnit": "U3"},
    ],
}

# Форма (б) — линия генерации колонок: ТОЛЬКО fns/active_fns/cost, без
# duty_fns/coverage/run_id. Разрыв G2->G3 (21 ч) >= 600 мин делит смены.
CATALOG_COLGEN = {
    "label": "pviz-b", "ip_status": "OPTIMAL", "ip_objective": 77,
    "bases": ["AAA"],
    "selected": [
        {"fns": ["G1", "G2", "G3"], "active_fns": ["G1", "G2", "G3"],
         "cost": 77},
    ],
}

PAYLOAD_B = {
    "airports": [{"code": c} for c in ("AAA", "BBB")],
    "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Pilot"]}],
    "flights": [
        _f("G1", "AAA", "2026-07-01T06:00:00Z", "BBB",
           "2026-07-01T07:00:00Z"),
        _f("G2", "BBB", "2026-07-01T08:00:00Z", "AAA",
           "2026-07-01T09:00:00Z"),
        _f("G3", "AAA", "2026-07-02T06:00:00Z", "BBB",
           "2026-07-02T07:00:00Z"),
    ],
    "flightAssignments": [],
}


@pytest.fixture()
def stand(tmp_path, monkeypatch):
    """tmp-FINAL_REPORT с двумя каталогами + payload'ы; кэш вида — свой
    на тест (иначе тест каппинга увидел бы ответ, собранный до подмены
    MAX_CHAINS)."""
    monkeypatch.setattr(pairing_viz, "FINAL_REPORT", tmp_path)
    monkeypatch.setattr(pairing_viz, "_view_cache", {})
    paths = {}
    for label, catalog, payload in (
            ("pviz-a", CATALOG_BUILDER, PAYLOAD),
            ("pviz-b", CATALOG_COLGEN, PAYLOAD_B)):
        (tmp_path / f"gerad_colgen_selected_{label}.json").write_text(
            json.dumps(catalog), encoding="utf-8")
        pp = tmp_path / f"payload-{label}.json"
        pp.write_text(json.dumps(payload), encoding="utf-8")
        paths[label] = {"id": f"pytest-{label}", "label": label,
                        "source": "upload", "payload_path": str(pp),
                        "legs": len(payload["flights"]),
                        "catalog_path": None}
    return paths


def _chain(view, cid):
    match = [c for c in view["chains"] if c["cid"] == cid]
    assert match, f"chain {cid} missing"
    return match[0]


def test_builder_catalog_duties_deadhead_and_costs(stand):
    view = pairing_viz.pairing_view(stand["pviz-a"])
    assert view is not None
    assert view["label"] == "pviz-a"
    assert view["run_id"] == "pytest-run"
    assert view["objective"] == 1501.0
    # Смены — ИЗ duty_fns каталога, не эвристикой.
    assert view["meta"]["duty_split"] == "catalog"
    u1 = _chain(view, "U1")
    # Хронология, перегон = fns − active_fns, перегон наследует смену
    # хронологического соседа; ночёвка = межсменный разрыв.
    assert [x["leg"] for x in u1["legs"]] == ["F1", "F2", "F3"]
    assert [x["dh"] for x in u1["legs"]] == [False, True, False]
    assert [x["duty"] for x in u1["legs"]] == [0, 0, 1]
    assert u1["nights"] == 1 and u1["span"] == 2
    assert u1["base"] == "AAA" and u1["cost"] == 1000
    assert u1["nlegs"] == 3 and u1["ndh"] == 1
    assert view["meta"]["legs_total"] == 9        # из coverage.legs_total
    assert view["meta"]["bases"] == ["AAA"]       # из employees (нет bases)
    assert view["meta"]["unresolved_legs"] == 0


def test_wire_names_resolved_via_wire_map(stand):
    view = pairing_viz.pairing_view(stand["pviz-a"])
    u2 = _chain(view, "U2")
    leg = u2["legs"][0]
    assert leg["leg"] == "FW1@1"                  # имя артефакта — как есть
    assert (leg["dep"], leg["arr"]) == ("BBB", "AAA")   # времена/города F8
    assert leg["dep_utc"] == "2026-07-02T12:00:00Z"
    # Покрытие через wire-имя засчитано: F8 не в непокрытых.
    assert "F8" not in {u["leg"] for u in view["uncovered"]}


def test_uncovered_set_semantics(stand):
    """Непокрытые = обязательства − объединение active_fns: транспорт (F5)
    и auto_plan_excluded (F6) обязательствами не являются; перегонная
    поездка (F2 в fns U1) покрытием НЕ считается; why — только у
    доказуемо непокрываемых."""
    view = pairing_viz.pairing_view(stand["pviz-a"])
    unc = {u["leg"]: u for u in view["uncovered"]}
    assert set(unc) == {"F2", "F4", "F7"}
    assert unc["F7"]["why"] == "proven_impossible"
    assert unc["F2"]["why"] is None and unc["F4"]["why"] is None
    # Города/времена непокрытых — из payload.
    assert unc["F4"]["dep"] == "BBB" and unc["F4"]["arr"] == "AAA"


def test_arcs_aggregate_and_nodes(stand):
    view = pairing_viz.pairing_view(stand["pviz-a"])
    arcs = {(a["a"], a["b"]): a for a in view["arcs"]}
    # BBB->CCC несёт перегон F2 (в цепочке) И его же непокрытое
    # обязательство: n=2, ndh=1, nunc=1.
    assert arcs[("BBB", "CCC")]["n"] == 2
    assert arcs[("BBB", "CCC")]["ndh"] == 1
    assert arcs[("BBB", "CCC")]["nunc"] == 1
    # Исключённый F6 (CCC->BBB) дуги НЕ даёт — его дуга только у F9.
    assert arcs[("CCC", "BBB")]["n"] == 1
    assert arcs[("CCC", "BBB")]["nunc"] == 0
    nodes = {n["code"]: n["base"] for n in view["nodes"]}
    assert nodes == {"AAA": True, "BBB": False, "CCC": False}


def test_capping_keeps_full_map(stand, monkeypatch):
    """Кап режет только chains; arcs/uncovered/chains_total считаются по
    полному набору — карта не врёт при обрезанной ленте."""
    monkeypatch.setattr(pairing_viz, "MAX_CHAINS", 2)
    view = pairing_viz.pairing_view(stand["pviz-a"])
    assert view["meta"]["chains_total"] == 3
    assert view["meta"]["chains_truncated"] is True
    assert len(view["chains"]) == 2
    # Порядок (base, t0): AAA-U1, BBB-U2 остаются, CCC-U3 обрезан...
    assert [c["cid"] for c in view["chains"]] == ["U1", "U2"]
    # ...но его уникальная дуга CCC->BBB на карте есть.
    assert ("CCC", "BBB") in {(a["a"], a["b"]) for a in view["arcs"]}


def test_colgen_catalog_gap600_heuristic(stand):
    """Форма (б) без duty_fns: смены — эвристикой разрыва >= 600 мин, и
    ответ честно называет её (duty_split='gap600')."""
    view = pairing_viz.pairing_view(stand["pviz-b"])
    assert view["meta"]["duty_split"] == "gap600"
    assert view["meta"]["chains_total"] == 1
    ch = view["chains"][0]
    assert ch["cid"] == "P1"                     # порядковый: нет sourceUnit
    assert [x["duty"] for x in ch["legs"]] == [0, 0, 1]
    assert ch["nights"] == 1
    assert view["uncovered"] == []               # полное покрытие
    assert view["meta"]["bases"] == ["AAA"]      # из шапки каталога


def test_view_cached_by_path_and_mtime(stand):
    v1 = pairing_viz.pairing_view(stand["pviz-a"])
    v2 = pairing_viz.pairing_view(stand["pviz-a"])
    assert v2 is v1                              # (путь, mtime) не менялись


def test_no_catalog_returns_none(stand):
    inst = dict(stand["pviz-a"], label="pviz-missing")
    assert pairing_viz.pairing_view(inst) is None
