# -*- coding: utf-8 -*-
"""Калибровка посадок против эталона М0: сверка категорий/уверенностей
журнала с истиной family эталона, кривая по бинам, честные пропуски
(extra-правила, неразмеченный эталон), отказ словами на кривом журнале."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tools import fit_calibration as fc  # noqa: E402

ETALON = {"rules": [
    {"id": "M-010", "family": "duty_limits",
     "sources": [{"sid": "S1", "anchor": "табл.3, строка 2, кол 3"}]},
    {"id": "M-003",  # без family — вне калибровки, поимённо
     "sources": [{"sid": "S1", "anchor": "табл.2, строка 2, кол 3"}]},
]}

MODEL = {"rules": [
    {"id": "R-duty", "template_fit": {"category": "A",
                                      "family": "duty_limits"},
     "sources": [{"sid": "S1", "anchor": "табл.3, строка 2, кол 3"}]},
    {"id": "R-wrong", "template_fit": {"category": "A",
                                       "family": "rest_floors"},
     "sources": [{"sid": "S1", "anchor": "табл.3, строка 2, кол 3"}]},
    {"id": "R-extra", "template_fit": {"category": "C"},
     "sources": [{"sid": "S2", "anchor": "лист «Рейсы», строка 2"}]},
]}


def _row(rule, cat, conf, role="worker", model="m1"):
    return {"at": "t", "rule": rule, "role": role, "category": cat,
            "confidence": conf, "model": model, "registry_rev": "r"}


def test_попадание_промах_и_бины():
    journal = [
        _row("R-duty", "A", 90),     # верная семья -> hit, бин 85-101
        _row("R-wrong", "A", 88),    # чужая семья -> miss, бин 85-101
        _row("R-duty", "C", 40),     # C при истине family -> miss
    ]
    rep = fc.calibrate(MODEL, journal, ETALON)
    ch = rep["per_channel"]["worker/m1"]
    assert ch["hits"] == 1 and ch["misses"] == 2
    assert ch["bins"]["85-101"] == {"n": 2, "hits": 1}
    assert ch["bins"]["0-50"] == {"n": 1, "hits": 0}


def test_каналы_раздельно_по_роли_и_модели():
    journal = [_row("R-duty", "A", 90, role="worker", model="m1"),
               _row("R-duty", "A", 70, role="auditor", model="m2")]
    rep = fc.calibrate(MODEL, journal, ETALON)
    assert set(rep["per_channel"]) == {"worker/m1", "auditor/m2"}


def test_пропуски_честные():
    journal = [_row("R-extra", "C", 50),      # вне эталона
               _row("R-ghost", "A", 60)]      # нет в model.json
    rep = fc.calibrate(MODEL, journal, ETALON)
    whys = [s["why"] for s in rep["skipped"]]
    assert any("вне эталона" in w for w in whys)
    assert any("нет в model.json" in w for w in whys)
    assert rep["etalon_unmarked"] == ["M-003"]


def test_кривой_журнал_отказ_словами(tmp_path):
    p = tmp_path / "fit-journal.jsonl"
    p.write_text('{"rule": "R-1"}\n', encoding="utf-8")
    with pytest.raises(fc.FitCalibrationError):
        fc.load_journal(p)
    with pytest.raises(fc.FitCalibrationError):
        fc.load_journal(tmp_path / "нет-такого.jsonl")


def test_рендер_содержит_точность_и_неразмеченные():
    journal = [_row("R-duty", "A", 90)]
    rep = fc.calibrate(MODEL, journal, ETALON)
    text = fc.render(rep)
    assert "worker/m1" in text and "M-003" in text
    assert "100%" in text


def test_живой_эталон_нано_размечен():
    et = json.loads(
        (HERE / "open-bench" / "polyaris-nano" / "etalon"
         / "M0-model.json").read_text(encoding="utf-8"))
    truth, unmarked = fc.truth_by_anchor(et)
    # 10 из 11 правил размечены family; M-003 — вне калибровки
    assert unmarked == ["M-003"]
    assert truth["S1|табл.3, строка 2, кол 3"] == "duty_limits"
    from vmp.template_registry import RULE_MECHANIC_TYPES
    assert set(truth.values()) <= set(RULE_MECHANIC_TYPES)
