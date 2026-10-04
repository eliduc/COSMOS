# -*- coding: utf-8 -*-
"""Подписи прямого хода (vmp/signatures.py): Д1 профиль, Д2 подпись
выхода, Д3 подпись задачи. Главный тест плана: замена формулировок при
неизменном каноне не меняет подпись задачи; смена числа — меняет."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "open-bench" / "vmp-stand"))

import stand  # noqa: E402
from vmp import signatures as sg  # noqa: E402


def _pkg():
    pkg, _e = stand.step_C0()
    return pkg


def _profile_r():
    return sg.deterministic_profile("rostering", seed=0, step_limit=5000,
                                    unimproved_step_limit=500)


# ------------------------------------------------------------ Д1

def test_профиль_поимённый_и_со_штампом():
    p = _profile_r()
    assert p["engine"] == "timefold" and p["move_threads"] == 1
    assert p["environment_mode"] == "NO_ASSERT"
    assert p["termination"] == {"kind": "step_count", "step_limit": 5000,
                                "unimproved_step_limit": 500}
    assert p["stamp"] == sg.profile_stamp(p)
    q = sg.deterministic_profile("pairing", seed=7, deterministic_time=30.0)
    assert q["workers"] == 1 and q["python_hash_seed"] == 0
    assert q["termination"] == {"kind": "deterministic_time", "value": 30.0}
    assert q["stamp"] != p["stamp"]
    with pytest.raises(ValueError):
        sg.deterministic_profile("rostering")
    with pytest.raises(ValueError):
        sg.deterministic_profile("pairing")
    with pytest.raises(ValueError):
        sg.deterministic_profile("другое", step_limit=1)


def test_штамп_профиля_не_зависит_от_порядка_ключей():
    p = _profile_r()
    shuffled = {k: p[k] for k in reversed(list(p))}
    assert sg.profile_stamp(shuffled) == p["stamp"]


# ------------------------------------------------------------ Д2

def _solved(order=(0, 1, 2)):
    fa = [
        {"employee": {"id": "H01"}, "flight": {"flightNumber": "PL101|2026-09-04T08:00:00"}},
        {"employee": "H02", "flight": "PL101|2026-09-04T08:00:00", "deadhead": True},
        {"employee": None, "flight": {"flightNumber": "PL102|2026-09-04T11:00:00"}},
    ]
    return {"flightAssignments": [fa[i] for i in order],
            "score": "0hard/-15soft", "solverStatus": "SOLVING_COMPLETED",
            "flights": [{"flightNumber": "PL101|2026-09-04T08:00:00",
                         "departureUTCDateTime": "2026-09-04T08:00:00"}]}


def test_ростер_канон_не_зависит_от_порядка_и_формы_ссылок():
    a = sg.canonical_roster(_solved((0, 1, 2)))
    b = sg.canonical_roster(_solved((2, 1, 0)))
    assert a == b
    assert a["assignments"] == [
        ["H01", "PL101|2026-09-04T08:00:00", "2026-09-04T08:00:00", False],
        ["H02", "PL101|2026-09-04T08:00:00", "2026-09-04T08:00:00", True]]
    assert a["unassigned"] == 1
    assert a["score"] == {"hard": 0, "soft": -15}
    assert a["status"] == "SOLVING_COMPLETED"
    assert sg.output_signature(a) == sg.output_signature(b)


def test_ростер_подпись_меняется_от_назначения_и_счёта_но_не_от_времени():
    base = sg.output_signature(sg.canonical_roster(_solved()))
    moved = _solved()
    moved["flightAssignments"][0]["employee"] = {"id": "H03"}
    assert sg.output_signature(sg.canonical_roster(moved)) != base
    scored = _solved()
    scored["score"] = "0hard/-16soft"
    assert sg.output_signature(sg.canonical_roster(scored)) != base
    noisy = _solved()
    noisy["wallTimeMs"] = 12345
    noisy["runDir"] = "run-0007"
    assert sg.output_signature(sg.canonical_roster(noisy)) == base


def test_вектор_счёта_по_ограничениям_входит_в_подпись():
    cons = {"Отдых дома": {"hard": -1, "soft": 0, "matches": 1,
                          "weight": "1hard/0soft", "score": "-1hard/0soft"},
            "Ночёвки": {"hard": 0, "soft": -10, "matches": 2}}
    a = sg.canonical_roster(_solved(), constraints=cons)
    assert a["constraints"] == [["Ночёвки", 0, -10, 2],
                                ["Отдых дома", -1, 0, 1]]
    b = sg.canonical_roster(_solved(), constraints=dict(reversed(list(cons.items()))))
    assert sg.output_signature(a) == sg.output_signature(b)
    assert sg.output_signature(a) != sg.output_signature(sg.canonical_roster(_solved()))


def test_разбор_счёта():
    assert sg.parse_score("-3init/0hard/-5soft") == {"init": -3, "hard": 0,
                                                     "soft": -5}
    assert sg.parse_score({"hard": 1, "soft": None}) == {"hard": 1}
    assert sg.parse_score(None) == {}


def _catalog(order=(0, 1)):
    sel = [
        {"fns": ["L1", "L2", "L3"], "active_fns": ["L1", "L3"],
         "duty_fns": [["L1"], ["L2", "L3"]], "cost": 120, "seats": 2,
         "sourceUnit": "pairing-builder-l3:7"},
        {"fns": ["L4"], "duty_fns": [["L4"]], "cost": 10.0, "crew": 2},
    ]
    return {"selected": [sel[i] for i in order], "ip_objective": 130,
            "ip_status": "OPTIMAL", "run_id": "run-x", "label": "H0",
            "coverage": {"uncovered": 0, "verdict": "OK", "legs_total": 4}}


def test_пейринг_канон_не_зависит_от_порядка_и_служебных_полей():
    a = sg.canonical_pairing(_catalog((0, 1)))
    b = sg.canonical_pairing(_catalog((1, 0)))
    assert a == b
    chain = next(c for c in a["chains"] if c["legs"] == ["L1", "L2", "L3"])
    assert chain["deadhead"] == ["L2"] and chain["seats"] == 2
    assert chain["cost"] == 120
    other = next(c for c in a["chains"] if c["legs"] == ["L4"])
    assert other["seats"] == 2 and other["cost"] == 10 and other["deadhead"] == []
    assert a["objective"] == 130 and a["status"] == "OPTIMAL"
    assert a["coverage_verdict"] == "OK" and a["uncovered"] == 0
    noisy = _catalog()
    noisy["run_id"] = "run-y"
    noisy["selected"][0]["sourceUnit"] = "другой"
    assert sg.output_signature(sg.canonical_pairing(noisy)) == sg.output_signature(a)
    changed = _catalog()
    changed["selected"][1]["fns"] = ["L4", "L5"]
    assert sg.output_signature(sg.canonical_pairing(changed)) != sg.output_signature(a)


# ------------------------------------------------------------ Д3

def test_замена_формулировок_при_неизменном_каноне_не_меняет_подпись():
    pkg = _pkg()
    prof = _profile_r()
    base = sg.task_signature(pkg, prof)
    other = copy.deepcopy(pkg)
    for r in other["model"]["rules"]:
        r["statement"] = "Другая формулировка того же."
        r["title"] = "Другое имя"
        r["id"] = "X-" + r["id"]
        r["sources"] = [{"sid": "S9", "anchor": "п.99", "quote": "иное"}]
        if isinstance(r.get("template_fit"), dict):
            for cell in r["template_fit"].get("params", {}).values():
                cell["quote"] = "иная цитата"
    for o in other["model"]["objectives"]:
        o["statement"] = "Иначе сказано."
        o["id"] = "X-" + o["id"]
        o["sources"] = []
    other["manifest"]["source_run"] = "run-9999"
    # Единица нарушения берётся из формы по идентификатору строки —
    # идентификаторы формы переписаны согласованно.
    for part in ("pairing", "rostering"):
        for row in other["form"][part]["soft"]:
            row["id"] = "X-" + row["id"]
    got = sg.task_signature(other, prof)
    assert got["sha256"] == base["sha256"], (base, got)
    assert got["contract_sha256"] == base["contract_sha256"]


def test_подпись_задачи_меняется_от_числа_веса_единицы_данных_и_профиля():
    pkg = _pkg()
    prof = _profile_r()
    base = sg.task_signature(pkg, prof)["sha256"]

    p1 = copy.deepcopy(pkg)
    rule = next(r for r in p1["model"]["rules"] if r["id"] == "H-DUTY")
    rule["template_fit"]["params"]["max_duty_duration"]["value"] = 781
    rule["params"][0]["value"] = 781
    assert sg.task_signature(p1, prof)["sha256"] != base

    p2 = copy.deepcopy(pkg)
    next(r for r in p2["model"]["rules"] if r["id"] == "S-LAYOVER")["weight"] = 11
    assert sg.task_signature(p2, prof)["sha256"] != base

    p3 = copy.deepcopy(pkg)
    p3["form"]["pairing"]["soft"][0]["penalty"]["violation_unit"] = "duty"
    assert sg.task_signature(p3, prof)["sha256"] != base

    p4 = copy.deepcopy(pkg)
    p4["clean_data"]["tables"]["D1"]["Экипажи"]["rows"].pop()
    s4 = sg.task_signature(p4, prof)
    assert s4["sha256"] != base and s4["contract_sha256"] == \
        sg.task_signature(pkg, prof)["contract_sha256"]

    other_prof = sg.deterministic_profile("rostering", seed=1, step_limit=5000)
    assert sg.task_signature(pkg, other_prof)["sha256"] != base
    assert sg.task_signature(pkg, None)["profile_sha256"] is None


def test_отпечаток_данных_не_зависит_от_порядка_строк_и_отчёта_очистки():
    pkg = _pkg()
    d = copy.deepcopy(pkg["clean_data"])
    d["tables"]["D1"]["Рейсы"]["rows"].reverse()
    d["cleaning"] = {"rows_removed": 3}
    d["customer"] = "Другой"
    assert sg.data_fingerprint(d) == sg.data_fingerprint(pkg["clean_data"])
    assert sg.data_fingerprint(None) is None


def test_снятые_строки_вне_контракта_а_ярусы_и_ставки_внутри():
    pkg = _pkg()
    prof = _profile_r()
    base = sg.task_signature(pkg, prof)["sha256"]
    p = copy.deepcopy(pkg)
    p["model"]["rules"].append({"id": "R-OUT", "kind": "OUT", "zone": "PAIRING",
                                "title": "снято", "statement": "снято",
                                "sources": []})
    assert sg.task_signature(p, prof)["sha256"] == base
    q = copy.deepcopy(pkg)
    q["model"]["objectives"][0]["tier"] = 2
    assert sg.task_signature(q, prof)["sha256"] != base
    w = copy.deepcopy(pkg)
    w["model"]["objectives"][0]["rate"]["value"] = 101
    assert sg.task_signature(w, prof)["sha256"] != base
