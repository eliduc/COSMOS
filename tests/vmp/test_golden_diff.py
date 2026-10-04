# -*- coding: utf-8 -*-
# В конце файла — тесты посадочного слоя (пакет A/B/C/D 2026-09-01).
"""Дифф свода против золотого эталона (vmp/golden_diff): покрытие по
якорям, совместимость видов, числовая эвристика, вердикты, живой
эталон нано читается и даёт отчёт."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import golden_diff as gd  # noqa: E402

ETALON = {
    "rules": [
        {"id": "M-001", "title": "Предел смены",
         "statement": "duty ≤ 12:00; посадок ≤ 3", "kind": "HARD",
         "sources": [{"sid": "S1", "anchor": "т.3, с.2"}]},
        {"id": "M-002", "title": "Ночёвки вне базы",
         "statement": "штраф за ночь", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "т.3, с.6"}]},
    ]
}


def _run_model(kind2="SOFT", val=720, extra=None):
    rules = [
        {"id": "R-1", "title": "Предел смены", "kind": "HARD",
         "statement": "предел смены; не более 3 посадок",
         "params": [{"name": "max_duty_duration", "value": val}],
         "sources": [{"sid": "S1", "anchor": "т.3, с.2"}]},
        {"id": "R-2", "title": "Ночёвки", "kind": kind2,
         "statement": "штраф", "sources": [
             {"sid": "S1", "anchor": "т.3, с.6"}]},
    ]
    if extra:
        rules.append(extra)
    return {"rules": rules}


def test_зелёный_когда_всё_покрыто_и_совместимо():
    res = gd.diff(_run_model(), ETALON)
    assert res["verdict"] == "GREEN"
    assert res["counts"]["covered"] == 2


def test_красный_на_непокрытом_и_несовместимом_виде():
    poor = {"rules": [_run_model()["rules"][0]]}
    assert gd.diff(poor, ETALON)["verdict"] == "RED"
    soft = _run_model(kind2="DATA")     # SOFT эталона стал данными
    assert gd.diff(soft, ETALON)["verdict"] == "RED"


def test_числа_частично_дают_янтарь_и_попадают_в_отчёт():
    res = gd.diff(_run_model(val=690), ETALON)
    assert res["verdict"] == "AMBER"
    row = res["rows"][0]
    assert row["numbers"] == "частичное"
    txt = gd.render_report(res)
    assert "ЧИСЛА ЧАСТИЧНО" in txt and "AMBER" in txt


def test_сверхэталонное_жёсткое_видно_и_янтарит():
    extra = {"id": "R-9", "title": "Лишнее", "kind": "HARD",
             "statement": "x", "sources": [
                 {"sid": "S9", "anchor": "нет в эталоне"}]}
    res = gd.diff(_run_model(extra=extra), ETALON)
    assert res["verdict"] == "AMBER"
    assert res["counts"]["extra_hard"] == 1


def test_живой_эталон_нано_читается():
    et_dir = HERE / "open-bench" / "polyaris-nano" / "etalon"
    et = gd.load_etalon(et_dir)
    res = gd.diff({"rules": []}, et)
    assert res["verdict"] == "RED"          # пустой свод не покрывает
    assert res["counts"]["etalon_rules"] >= 10


def test_кривые_входы_отказ_словами():
    with pytest.raises(gd.GoldenDiffError):
        gd.diff([], ETALON)
    with pytest.raises(gd.GoldenDiffError):
        gd.load_etalon(Path("нет-такого-каталога"))


# ---- Посадочный слой (пакет A/B/C/D, решение владельца 2026-09-01) --

ETALON_FIT = {
    "rules": [
        {"id": "M-001", "title": "Предел смены",
         "statement": "duty ≤ 12:00", "kind": "HARD",
         "family": "duty_limits",
         "sources": [{"sid": "S1", "anchor": "т.3, с.2"}]},
    ]
}


def _fit_rule(family, category="A", kind="HARD"):
    return {"id": "R-f", "title": "Предел", "kind": kind,
            "statement": "предел",
            "template_fit": {"category": category, "family": family,
                             "registry_rev": "r"},
            "sources": [{"sid": "S1", "anchor": "т.3, с.2"}]}


def test_посадка_точная_семья_сильнее_вида():
    # вид SOFT при эталоне HARD был бы красным, но точная посадка
    # семьи судит канонично
    res = gd.diff({"rules": [_fit_rule("duty_limits", kind="SOFT")]},
                  ETALON_FIT)
    row = res["rows"][0]
    assert row["fit"] == "точная" and row["kind_ok"] is True
    assert res["verdict"] != "RED"


def test_посадка_в_чужую_семью_красная_даже_при_совместимом_виде():
    res = gd.diff({"rules": [_fit_rule("rest_floors", kind="HARD")]},
                  ETALON_FIT)
    row = res["rows"][0]
    assert row["fit"].startswith("чужая семья")
    assert row["kind_ok"] is False and res["verdict"] == "RED"
    text = gd.render_report(res)
    assert "ПОСАДКА В ЧУЖУЮ СЕМЬЮ" in text


def test_без_посадки_прежний_путь_по_видам():
    rule = _fit_rule("duty_limits", kind="SOFT")
    del rule["template_fit"]
    res = gd.diff({"rules": [rule]}, ETALON_FIT)
    row = res["rows"][0]
    assert row["fit"] is None and row["kind_ok"] is False


def test_эталон_без_family_не_судит_посадку():
    et = {"rules": [dict(ETALON_FIT["rules"][0])]}
    del et["rules"][0]["family"]
    res = gd.diff({"rules": [_fit_rule("rest_floors")]}, et)
    assert res["rows"][0]["fit"] is None
