# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A2.5 — замок измерителя и проверки
постановки на кнопке COSMOS (слово владельца 04.09 «делать A2.5»;
ключи пакета — спецификация 1.15, CDPM).

Стенды с известным ответом (fail-closed):
  1. measure.ok=false — остановка словами измерителя;
  2. measure.available=false — остановка «измеритель не отработал»;
  3. coverage.values_without_addressee > 0 / normative_without_rows > 0
     — остановка;
  4. пакет без ключей measure/coverage (младше 1.14) — прежний закон,
     не стоп;
  5. форма пакета с остановкой проверки постановки (критерий без
     метрики) — остановка; form.problems — показание, не замок.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_run_button import (FakeSolver, _pairing_fakes_min,   # noqa: E402
                                   btn)

STOP_VERDICT = ("остановка: измеритель или проверка постановки "
                "запрещают запуск")


def _manifest(**extra) -> dict:
    base = {"allowed_modes": {"pairing": ["feasibility-hard"],
                              "rostering": ["optimization"]},
            "objectives": {"rostering": ["G-1"]},
            "spec_version": "1.15"}
    base.update(extra)
    return base


def _press(tmp_path, manifest: dict, form=None):
    run, fakes = _pairing_fakes_min(tmp_path)
    fakes["load_pkg"] = lambda path: {"manifest": manifest, "model": {},
                                      "clean_data": None, "form": form}
    return btn.orchestrate(run, FakeSolver(), task="pairing",
                           sleep=lambda *_: None, **fakes)


def test_measure_not_ok_stops_with_the_measurers_words(tmp_path):
    d = _press(tmp_path, _manifest(measure={
        "available": True, "ok": False,
        "problems": ["строка П-3: значение 12 без адресата"]}))
    assert d["verdict"] == STOP_VERDICT, d
    assert "без адресата" in str(d.get("detail") or ""), d


def test_measure_unavailable_stops(tmp_path):
    d = _press(tmp_path, _manifest(measure={
        "available": False, "reason": "измеритель упал на листе 3"}))
    assert d["verdict"] == STOP_VERDICT, d
    assert "не отработал" in str(d.get("detail") or ""), d


@pytest.mark.parametrize("key,words", [
    ("values_without_addressee", "без адресата"),
    ("normative_without_rows", "нормативных клауз без строк")])
def test_coverage_gaps_stop(tmp_path, key, words):
    cov = {"clauses": 10, "covered_by_rows": 9, "not_rule": 1, "missing": 0,
           "values_without_addressee": 0, "normative_without_rows": 0}
    cov[key] = 2
    d = _press(tmp_path, _manifest(
        measure={"available": True, "ok": True, "problems": []},
        coverage=cov))
    assert d["verdict"] == STOP_VERDICT, d
    assert words in str(d.get("detail") or ""), d


def test_clean_measure_and_coverage_pass_the_lock(tmp_path):
    d = _press(tmp_path, _manifest(
        measure={"available": True, "ok": True, "problems": []},
        coverage={"clauses": 10, "covered_by_rows": 10, "not_rule": 0,
                  "missing": 0, "values_without_addressee": 0,
                  "normative_without_rows": 0}))
    assert d["verdict"] != STOP_VERDICT, d


def test_package_without_keys_is_judged_by_the_old_law(tmp_path):
    d = _press(tmp_path, _manifest(spec_version="1.6"))
    assert d["verdict"] != STOP_VERDICT, d


def test_formulation_check_stop_level_locks_the_button(tmp_path):
    """Критерий без метрики — уровень stop проверки постановки
    (vmp/formulation_check.check_criteria): кнопка останавливается."""
    form = {"pairing": {"criteria": [
        {"id": "C-1", "title": "критерий без метрики",
         "direction": "minimize"}]}}
    d = _press(tmp_path, _manifest(
        measure={"available": True, "ok": True, "problems": []}), form=form)
    assert d["verdict"] == STOP_VERDICT, d
    assert "проверка постановки" in str(d.get("detail") or ""), d
