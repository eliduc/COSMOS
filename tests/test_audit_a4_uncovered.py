"""Аудит A4 (04.09.2026): непокрытые рейсы — «не найдено / поиск не
завершён / доказано невозможно».

Класс ошибки: эвристический зонд (прямой перегон либо одна пересадка)
выдавался за доказательство невозможности; INFEASIBLE_INPUT ставился по
нему, а потребители (проверка покрытия, гейт этапа 2) прощали недостачу
по голому списку имён. Стенды ниже с известным ответом: на прежнем коде
падают первые четыре (цепочка через две пересадки объявлялась
невозможной; срыв бюджета эскалации — «невозможно»; INFEASIBLE_INPUT при
недоказанных; прощение без доказательства).
"""
from __future__ import annotations

import json
import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pb = pytest.importorskip("_pairing_builder")
from test_pairing_builder import _args, _leg, _payload  # noqa: E402

import _coverage_validator as _cv  # noqa: E402


def _chain_world():
    """База→X→Y→Z→W→база, рейсы через 36 часов (пауза между рейсами
    длиннее суток — зонд «одна пересадка со стыковкой до 1440 мин» не
    видит ни одного шага): до Z нет ни прямого перегона, ни перегона с
    одной пересадкой, но пятидневная цепочка законна и в размахе."""
    return [_leg("F1", "BASE", "XXX", "2026-08-01T06:00:00", 120),
            _leg("F2", "XXX", "YYY", "2026-08-02T18:00:00", 120),
            _leg("F3", "YYY", "ZZZ", "2026-08-04T06:00:00", 120),
            _leg("F4", "ZZZ", "WWW", "2026-08-05T18:00:00", 120),
            _leg("F5", "WWW", "BASE", "2026-08-07T06:00:00", 120)]


def _tiny_search(**over):
    """Перебор, заведомо не строящий пятидневную цепочку."""
    return _args(depth=1, max_segs=1, branch_chain=1, branch_duty=1,
                 per_duty=1, dh_options=0, rescue_dh=0, rescue_per_leg=0,
                 **over)


# --- стенд 1: две пересадки не делают рейс «невозможным» -----------------

def test_A4_цепочка_через_две_пересадки_не_объявляется_невозможной():
    b = _pb.Builder(_payload(_chain_world()), _args())
    b.build_duties(None)
    r = b.station_reachability("F4")
    assert r["reach"] and r["back"], r
    out = b.classify_uncovered({"F4"})
    assert out["proven_impossible_legs"] == []
    assert out["uncovered_not_built_legs"] == ["F4"]
    why = out["uncovered_not_built_reasons"]["F4"]
    assert why["search_incomplete"] is True
    assert why["reason"] == "вариант не найден в проверенном пространстве"
    assert out["search_incomplete"] is True


def test_A4_полный_перебор_строит_цепочку_через_две_пересадки():
    """Малая задача: результат построителя совпадает с ответом полного
    перебора — цепочка существует и строится, непокрытых нет."""
    catalog, report = _pb.build(_payload(_chain_world()), _args())
    assert report["uncovered_legs"] == 0
    assert catalog["coverage"]["verdict"] == "OK"
    assert catalog["proven_impossible_legs"] == []
    assert catalog["proven_impossible_proofs"] == []


# --- стенд 2: предел бюджета/глубины эскалации ≠ невозможно --------------

def test_A4_срыв_бюджета_эскалации_даёт_поиск_не_завершён():
    b = _pb.Builder(_payload(_chain_world()), _args())
    b.build_duties(None)
    b.report["escalation"] = {"proved": [], "budget_hit": ["F4"],
                              "not_certified": []}
    out = b.classify_uncovered({"F4"})
    assert out["proven_impossible_legs"] == []
    assert out["uncovered_not_built_reasons"]["F4"]["reason"] \
        == "предел времени"


def test_A4_усечения_эскалации_дают_предел_глубины():
    b = _pb.Builder(_payload(_chain_world()), _args())
    b.build_duties(None)
    b.report["escalation"] = {
        "proved": [], "budget_hit": [],
        "not_certified": [{"leg": "F4", "truncations": ["dh_options"]}]}
    out = b.classify_uncovered({"F4"})
    assert out["proven_impossible_legs"] == []
    why = out["uncovered_not_built_reasons"]["F4"]
    assert why["reason"] == "предел глубины"
    assert "dh_options" in why["detail"]


def test_A4_доказательство_ступени_3_попадает_в_список_доказанных():
    """Строгое доказательство эскалации прежде уходило только в отчёт;
    теперь оно — запись proven_impossible_proofs вида escalation_proved."""
    b = _pb.Builder(_payload(_chain_world()), _args())
    b.build_duties(None)
    b.report["escalation"] = {
        "proved": [{"leg": "F4", "window_duties": 5,
                    "note": "полный обход окна завершён без усечений"}],
        "budget_hit": [], "not_certified": []}
    out = b.classify_uncovered({"F4"})
    assert out["proven_impossible_legs"] == ["F4"]
    (p,) = out["proven_impossible_proofs"]
    assert p["kind"] == "escalation_proved"
    assert p["data_fingerprint"] == b.data_fingerprint()
    assert p["horizon"]["pairing_span_min"] == _args().pairing_span


# --- стенд 3: INFEASIBLE_INPUT только при доказанности всех --------------

def test_A4_INFEASIBLE_INPUT_только_когда_все_непокрытые_доказаны():
    """Мир: доказуемо непокрываемый F9 (чужие станции) плюс цепочка через
    две пересадки, которую урезанный перебор не строит. Прежний код
    объявлял ВСЕ шесть участков «невозможными» и ставил INFEASIBLE_INPUT;
    честный вердикт — INCOMPLETE_SEARCH, доказан только F9."""
    fs = _chain_world() + [
        _leg("F9", "XX1", "XX2", "2026-08-08T06:00:00", 90)]
    catalog, report = _pb.build(_payload(fs), _tiny_search())
    cov = catalog["coverage"]
    assert report["uncovered_legs"] == 6
    assert catalog["proven_impossible_legs"] == ["F9"]
    assert cov["verdict"] == "INCOMPLETE_SEARCH"
    assert cov["search_incomplete"] is True
    assert cov["excluded_proven"] == ["F9"]
    assert cov["complete"] is False
    assert set(cov["not_built_reasons"]) == {"F1", "F2", "F3", "F4", "F5"}
    (p,) = catalog["proven_impossible_proofs"]
    assert p["leg"] == "F9" and p["kind"] == "graph_unreachable"
    assert p["data_fingerprint"] == _cv.payload_data_fingerprint(
        _payload(fs))


def test_A4_вердикт_каталога_по_трём_исходам():
    assert _pb.coverage_verdict(0, 0) == "OK"
    assert _pb.coverage_verdict(3, 3) == "INFEASIBLE_INPUT"
    assert _pb.coverage_verdict(3, 2) == "INCOMPLETE_SEARCH"
    assert _pb.coverage_verdict(1, 0) == "INCOMPLETE_SEARCH"


def test_A4_только_доказанные_непокрытые_дают_INFEASIBLE_INPUT():
    fs = [_leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
          _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
          _leg("F9", "XX1", "XX2", "2026-08-06T06:00:00", 90)]
    catalog, _ = _pb.build(_payload(fs), _args())
    cov = catalog["coverage"]
    assert cov["verdict"] == "INFEASIBLE_INPUT"
    assert cov["excluded_proven"] == ["F9"] and cov["complete"] is False
    assert cov["not_built_reasons"] == {}


# --- стенд 4: обход графа совпадает с полным перебором путей -------------

def _brute_reachable(legs, bases, lid, span_min, pre_gap_min, post_gap_min):
    """Полный перебор путей в графе рейсов (те же границы, что у обхода):
    существует ли путь база→…→dep(l) и arr(l)→…→база."""
    l = legs[lid]
    lo = l["t0"] - timedelta(minutes=span_min)
    hi = l["t0"] + timedelta(minutes=span_min)

    def back(station, limit, used):
        if station in bases:
            return True
        for x in legs.values():
            if x["id"] in used or x["arr"] != station:
                continue
            if x["t1"] <= limit and x["t0"] >= lo \
                    and back(x["dep"], x["t0"], used | {x["id"]}):
                return True
        return False

    def fwd(station, limit, used):
        if station in bases:
            return True
        for x in legs.values():
            if x["id"] in used or x["dep"] != station:
                continue
            if x["t0"] >= limit and x["t1"] <= hi \
                    and fwd(x["arr"], x["t1"], used | {x["id"]}):
                return True
        return False

    return (back(l["dep"], l["t0"] - timedelta(minutes=pre_gap_min), set()),
            fwd(l["arr"], l["t1"] + timedelta(minutes=post_gap_min), set()))


def test_A4_обход_графа_совпадает_с_полным_перебором_путей():
    rng = random.Random(4)
    stations = ["BASE", "AAA", "BBB", "CCC", "DDD", "EEE"]
    fs = []
    for i in range(40):
        dep, arr = rng.sample(stations, 2)
        day = rng.randint(1, 6)
        hour = rng.randint(0, 20)
        fs.append(_leg(f"L{i}", dep, arr, f"2026-08-0{day}T{hour:02d}:00:00",
                       rng.choice([60, 90, 120, 180])))
    args = _args(pairing_span=3 * 1440)
    b = _pb.Builder(_payload(fs), args)
    b.build_duties(None)
    for lid in b.legs:
        got = b.station_reachability(lid)
        want = _brute_reachable(b.legs, b.bases, lid, args.pairing_span,
                                args.min_rest_sit, b.dh_board_min())
        assert (got["reach"], got["back"]) == want, (lid, got, want)


def test_A4_доказательство_недостижимости_строгое():
    """Всё, что обход объявил недостижимым, недостижимо и для полного
    перебора: доказательство — необходимое условие, не эвристика."""
    fs = _chain_world() + [
        _leg("F9", "XX1", "XX2", "2026-08-06T06:00:00", 90),
        # прилёт есть, а обратно из QQQ в размахе — нет
        _leg("F7", "BASE", "QQQ", "2026-08-02T06:00:00", 120)]
    b = _pb.Builder(_payload(fs), _args())
    b.build_duties(None)
    out = b.classify_uncovered(set(b.legs))
    assert out["proven_impossible_legs"] == ["F7", "F9"]
    for p in out["proven_impossible_proofs"]:
        want = _brute_reachable(b.legs, b.bases, p["leg"],
                                _args().pairing_span, _args().min_rest_sit,
                                b.dh_board_min())
        assert not (want[0] and want[1]), p


# --- стенд 5: доказательство привязано к отпечатку данных -----------------

def test_A4_доказательство_недействительно_при_смене_данных():
    fs = [_leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
          _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
          _leg("F9", "XX1", "XX2", "2026-08-06T06:00:00", 90)]
    payload = _payload(fs)
    catalog, _ = _pb.build(payload, _args())
    ok = _cv.proven_exclusions(catalog, payload)
    assert ok["allowed"] == {"F9"} and ok["rejected"] == []
    # переименование рейсов отпечаток не меняет (физические факты те же)
    renamed = json.loads(json.dumps(payload))
    for f in renamed["flights"]:
        f["flightNumber"] = "W-" + f["flightNumber"]
    assert _cv.payload_data_fingerprint(renamed) \
        == _cv.payload_data_fingerprint(payload)
    # добавленный рейс делает прежнее доказательство неприменимым
    more = _payload(fs + [_leg("F8", "BASE", "XX1", "2026-08-05T06:00:00",
                               120)])
    bad = _cv.proven_exclusions(catalog, more)
    assert bad["allowed"] == set()
    assert bad["rejected"][0]["leg"] == "F9"
    assert "других данных" in bad["rejected"][0]["why"]


def test_A4_голое_имя_без_доказательства_не_прощается():
    payload = _payload([_leg("F1", "BASE", "AAA", "2026-08-01T06:00:00",
                             120)])
    out = _cv.proven_exclusions({"proven_impossible_legs": ["F1"]}, payload)
    assert out["allowed"] == set()
    assert out["rejected"] == [{"leg": "F1", "why": out["rejected"][0]["why"]}]
    assert "нет записи доказательства" in out["rejected"][0]["why"]
    fp = _cv.payload_data_fingerprint(payload)
    out2 = _cv.proven_exclusions(
        {"proven_impossible_legs": ["F1"],
         "proven_impossible_proofs": [{"leg": "F1", "kind": "probe",
                                       "data_fingerprint": fp}]}, payload)
    assert out2["allowed"] == set()
    assert "неизвестный вид" in out2["rejected"][0]["why"]


# --- гейт этапа 2 принимает только доказанные исключения ------------------

def test_A4_гейт_этапа_2_прощает_только_по_доказательству():
    from _gerad_stage2_roster import allowed_missing_from_catalog
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F9", "XX1", "XX2", "2026-08-06T06:00:00", 90)])
    fp = _cv.payload_data_fingerprint(payload)
    proof = {"leg": "F9", "kind": "graph_unreachable", "data_fingerprint": fp}
    allowed, rejected = allowed_missing_from_catalog(
        {"proven_impossible_legs": ["F9"],
         "proven_impossible_proofs": [proof]}, payload, {})
    assert allowed == {"F9"} and rejected == []
    allowed, rejected = allowed_missing_from_catalog(
        {"proven_impossible_legs": ["F9"]}, payload, {})
    assert allowed == set() and [r["leg"] for r in rejected] == ["F9"]
    # wire-имена переводятся картой, как и прежде
    wm = {"W9": {"flightLegId": "F9"}}
    allowed, _ = allowed_missing_from_catalog(
        {"proven_impossible_legs": ["W9"],
         "proven_impossible_proofs": [dict(proof, leg="W9")]}, payload, wm)
    assert allowed == {"F9"}
