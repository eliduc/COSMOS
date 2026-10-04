# -*- coding: utf-8 -*-
"""Колонная генерация линий (Г-Д5): пул смен, граф отдыха, прайсер, отбор.

Контракт: линии законны ПО ПОСТРОЕНИЮ (непрерывность, зазоры смены,
мёртвая зона, отдых по месту, месячный налёт), пустые смены-перегоны
не покрывают и не считаются в налёт, всё детерминировано; пороги —
только явными аргументами, умолчаний у механизма нет. Значения порогов
в тестах — СИНТЕТИКА под топологию сети _net(): смены <= 180 мин при
пределе 480, предел налёта 100000 стража не активирует.
"""

import pytest

from webapp import chain_seed as cs
from webapp import line_colgen as lc


def _pywraplp_blocked() -> str:
    """Точная причина недоступности LP-модуля ortools, либо пустая строка.

    2026-08-25: политика Windows Application Control заблокировала
    нативный _pywraplp (CP-SAT цел). Это МАШИННАЯ политика, не код:
    тесты LP-путей пропускаются с громкой причиной, а не красятся;
    любой иной ImportError остаётся честным отказом."""
    try:
        from ortools.linear_solver import pywraplp  # noqa: F401
        return ""
    except ImportError as e:
        if "Application Control" in str(e) or "DLL load failed" in str(e):
            return ("ortools._pywraplp заблокирован политикой Application "
                    "Control машины — машинная политика, не код; решение "
                    "по разблокировке за владельцем (2026-08-25)")
        raise


_LP_BLOCKED = _pywraplp_blocked()
needs_lp = pytest.mark.skipif(bool(_LP_BLOCKED), reason=_LP_BLOCKED)


def _f(num, dep, dep_t, arr, arr_t):
    return {"flightNumber": num, "flightLegId": num,
            "departureAirport": dep, "departureUTCDateTime": dep_t,
            "arrivalAirport": arr, "arrivalUTCDateTime": arr_t}


def _net():
    """Две естественные смены AAA→BBB→AAA (день 1) и AAA→CCC (день 2)."""
    return [
        _f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
        _f("F2", "BBB", "2026-03-01T08:00:00", "AAA", "2026-03-01T09:00:00"),
        _f("F3", "AAA", "2026-03-02T06:00:00", "CCC", "2026-03-02T07:00:00"),
    ]


def test_пул_смен_перечисляет_все_и_чтит_зазоры():
    duties = lc.build_duty_pool(_net(), sit_min=45, max_duty_min=480)
    ids = {d.flight_ids for d in duties}
    # одиночные + сцепка F1-F2 (зазор 60 в [45, 360)); F2-F3 — через ночь,
    # это не внутрисменный зазор
    assert ("F1",) in ids and ("F2",) in ids and ("F3",) in ids
    assert ("F1", "F2") in ids
    assert ("F2", "F3") not in ids
    d12 = next(d for d in duties if d.flight_ids == ("F1", "F2"))
    assert d12.start_airport == "AAA" and d12.end_airport == "AAA"
    assert d12.block_min == 120.0


def test_граф_отдыха_судит_по_месту():
    duties = lc.build_duty_pool(_net(), sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    d12 = next(d for d in duties if d.flight_ids == ("F1", "F2"))
    succ_ids = {d.flight_ids for d in graph.successors(d12)}
    # конец в базе AAA 09:00 + 720 мин = 21:00 < вылет F3 (06:00 дня 2)
    assert ("F3",) in succ_ids


def test_прайсер_собирает_линию_через_отдых():
    duties = lc.build_duty_pool(_net(), sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    duals = {"F1": 0.6, "F2": 0.6, "F3": 0.6}
    cols = lc.price_lines(graph, duals, max_block_min=100000)
    assert cols, "колонки с отрицательной приведённой стоимостью обязаны найтись"
    best_rc, best_path = cols[0]
    covered = [fid for di in best_path for fid in duties[di].flight_ids]
    assert set(covered) == {"F1", "F2", "F3"}
    assert best_rc == pytest.approx(1.0 - 1.8)


def test_страж_налёта_в_прайсере():
    duties = lc.build_duty_pool(_net(), sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    duals = {"F1": 1.0, "F2": 1.0, "F3": 1.0}
    cols = lc.price_lines(graph, duals, max_block_min=130)
    # сцепка F1-F2 (120) + F3 (60) = 180 > 130 — такой линии быть не должно
    for _, path in cols:
        block = sum(duties[di].block_min for di in path)
        assert block <= 130


def test_пустые_смены_не_покрывают_и_не_летают_в_налёт():
    flights = _net()
    duties = lc.extend_with_deadheads(
        lc.build_duty_pool(flights, sit_min=45, max_duty_min=480),
        flights, sit_min=45, max_duty_min=480)
    empties = [d for d in duties if not d.flights and d.dh_prefix is not None]
    assert len(empties) == len(flights)
    for d in empties:
        assert d.flight_ids == ()
        assert d.block_min == 0.0
        assert d.start_airport == d.dh_prefix["departureAirport"]
        assert d.end_airport == d.dh_prefix["arrivalAirport"]


@needs_lp
def test_отбор_целиком_и_затравка_линиями():
    flights = _net()
    duties = lc.build_duty_pool(flights, sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    lines = lc.solve_cover(flights, duties, graph, sit_min=45,
                           max_duty_min=480, max_block_min=100000,
                           rounds=5, final_seconds=10.0)
    assert sum(len(l) for l in lines) == 3
    assert cs.continuity_defects(lines) == 0
    payload = {"flights": flights,
               "employees": [{"id": "CAP", "homeAirport": "AAA", "skills": ["Captain"]}],
               "flightAssignments": [
                   {"id": "s%d" % i, "flight": f["flightNumber"],
                    "indexInFlight": 0, "requiredSkill": "Captain"}
                   for i, f in enumerate(flights)]}
    seeded, summary = cs.seed_with_lines(payload, lines)
    assert summary["seats_seeded"] >= 2  # хотя бы главная линия роздана
    assert all("employee" not in a or a["employee"] == "CAP"
               for a in seeded["flightAssignments"])


def test_банк_колонн_круговой_путь_и_отбрасывание(tmp_path):
    """Банк: сохранённые колонки загружаются на том же пуле; колонка со
    сменой, которой в пуле нет (пороги сменились), отбрасывается с
    числом, не молча."""
    flights = _net()
    duties = lc.build_duty_pool(flights, sit_min=45, max_duty_min=480)
    bank = tmp_path / "bank.json"
    pool = [(duties[0].idx,), (duties[1].idx,)]
    lc.save_bank(bank, duties, pool)
    loaded = lc.load_bank(bank, duties)
    assert loaded == pool
    # чужая смена в банке — колонка отбрасывается
    import json
    rows = json.loads(bank.read_text(encoding="utf-8"))
    rows.append([{"f": ["НЕТ-ТАКОГО"], "p": None, "s": None}])
    bank.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    msgs = []
    loaded2 = lc.load_bank(bank, duties, log=msgs.append)
    assert loaded2 == pool
    assert any("отброшено несопоставимых 1" in m for m in msgs)


@needs_lp
def test_нырки_дизъюнктны_чтят_предел_и_докрывают():
    """Нырок: выбранные пути попарно дизъюнктны по рейсам, линий не
    больше предела, пере-прайсинг остатка докрывает сеть."""
    flights = _net()
    duties = lc.build_duty_pool(flights, sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    by = {d.flight_ids: d.idx for d in duties}
    # пул нарочно без колонки для F3 — её обязан допрайсить нырок
    pool = [(by[("F1", "F2")],), (by[("F1",)],), (by[("F2",)],)]
    fixed, fresh = lc.dive_cover(flights, duties, graph, pool, max_lines=2,
                                 max_block_min=100000)
    assert len(fixed) <= 2
    covered = [f for p in fixed for di in p
               for f in duties[di].flight_ids]
    assert sorted(covered) == ["F1", "F2", "F3"]     # без повторов и дыр
    assert fresh, "колонка для F3 приходит из пере-прайсинга"
    # повторный вызов детерминирован
    assert lc.dive_cover(flights, duties, graph, pool, max_lines=2,
                         max_block_min=100000) == (fixed, fresh)


@needs_lp
def test_банк_чтит_предел_налёта_прогона(tmp_path):
    """Колонка банка, собранная при большем пределе налёта, отбрасывается
    с числом: банк не проносит нелегальные для ЭТОГО прогона линии."""
    flights = _net()
    duties = lc.build_duty_pool(flights, sit_min=45, max_duty_min=480)
    graph = lc.DutyGraph(duties, rest_base_min=720, rest_out_min=600,
                         bases=frozenset(("AAA",)))
    by = {d.flight_ids: d.idx for d in duties}
    bank = tmp_path / "bank.json"
    # колонка F1-F2 (120 мин налёта) в банке
    lc.save_bank(bank, duties, [(by[("F1", "F2")],)])
    msgs = []
    # Порог 50 короче любого рейса сети (60): он запрещает и банковскую
    # колонку (120), и любую жадную линию — фильтры объявляют отброс
    # вслух; пустой пул завершится честным отказом CP-SAT, и это тоже
    # часть контракта (не молчаливый успех).
    with pytest.raises(RuntimeError, match="0 колонок"):
        lc.solve_cover(flights, duties, graph, sit_min=45, max_duty_min=480,
                       rounds=1, final_seconds=5.0, max_block_min=50,
                       bank_path=str(bank), log=msgs.append)
    assert any("сверх предела налёта 1" in m for m in msgs), msgs


def test_затравка_линиями_с_перегоном_создаёт_кресла_именами_решателя():
    flights = _net()
    lines = [[flights[0], dict(flights[1], __deadhead__=True), flights[2]]]
    payload = {"flights": flights,
               "employees": [{"id": "CAP", "homeAirport": "AAA", "skills": ["Captain"]}],
               "flightAssignments": [
                   {"id": "s%d" % i, "flight": f["flightNumber"],
                    "indexInFlight": 0, "requiredSkill": "Captain"}
                   for i, f in enumerate(flights)]}
    seeded, summary = cs.seed_with_lines(payload, lines, dh_per_flight=2)
    dh = [a for a in seeded["flightAssignments"] if a.get("deadhead")]
    assert len(dh) == 6  # полный набор кресел: 2 на каждый из 3 рейсов
    assert all(a["id"].startswith("DH-") for a in dh)
    used = [a for a in dh if a.get("employee")]
    assert len(used) == 1 and used[0]["flight"] == "F2"
    assert summary["dh_seeded"] == 1


def test_colgen_без_порогов_отказывает_с_перечнем_недостающего():
    """Умолчаний у colgen нет: отсутствующие пороги перечисляются словами,
    источник значений назван; пустое множество баз — тоже отказ."""
    payload = {"flights": _net(),
               "employees": [{"id": "CAP", "homeAirport": "AAA",
                              "skills": ["Captain"]}]}
    with pytest.raises(RuntimeError) as e:
        lc.colgen_lines(payload, sit_min=45, max_duty_min=480)
    msg = str(e.value)
    for name in ("rest_base_min", "rest_out_min", "max_block_min"):
        assert name in msg
    assert "пакета постановки" in msg
    # базы — из homeAirport полезной нагрузки; нет ни одной — отказ
    empty = {"flights": _net(), "employees": [{"id": "X", "skills": []}]}
    with pytest.raises(RuntimeError, match="homeAirport"):
        lc.colgen_lines(empty, sit_min=45, max_duty_min=480,
                        rest_base_min=705, rest_out_min=585,
                        max_block_min=100000)
