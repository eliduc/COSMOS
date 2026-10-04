# -*- coding: utf-8 -*-
"""Контрольный проход 6 H0-К (05.09.2026) — два дефекта сертификатора,
закрытые кодом:

  1. правило семьи-параметра мира (шкала времени) шло компилятору, тот
     честно отказывал («свойство представления, не колонка»), а строгое
     покрытие A8 считало проверку нескомпилированной — сертификат
     пейринга не выдавался. Теперь: компилятору не подаётся, в отчёте —
     запись «применена конфигурацией» со сдвигом шкалы словами;
  2. компилятор-модель выбрал единицу счёта состава «смена»; идентификатор
     смены решения — на сотрудника, и «в смене» всегда один пилот — 31
     ложное нарушение при честном 0hard решателя. Теперь единица задана
     семьёй crew_composition (рейс листа «Назначения», различные
     табельные номера) — предложение компилятора не принимается.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp.run import roster_certify as rc          # noqa: E402


def _svod():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-TZ", "title": "Шкала времени",
             "statement": "все времена — местное время базы PLY, сдвиг 0",
             "zone": "BOTH", "kind": "HARD",
             "template_fit": {"category": "A", "family": "time_scale",
                              "params": {"base_tz_offset_min": {"value": 0}}},
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            # Семья известна, а численность НЕ посажена (нет crew_size) —
            # правило идёт компилятору; единицу счёта задаёт семья.
            # (Посаженное правило компилятору не подаётся вовсе —
            # tests/test_audit_fit_checks.py.)
            {"id": "R-CREW", "title": "Состав экипажа",
             "statement": "на каждом рейсе ровно два пилота",
             "zone": "ROSTERING", "kind": "HARD",
             "template_fit": {"category": "A", "family": "crew_composition"},
             "sources": [{"sid": "S1", "anchor": "п.2"}]},
        ],
        "questions": [], "objectives": [],
    }


def _solution():
    legs = [("PL101", "2026-01-15T06:00", "2026-01-15T08:00", "PLY", "SVG"),
            ("PL102", "2026-01-15T10:00", "2026-01-15T12:00", "SVG", "PLY")]
    assignments = []
    duties = []
    for emp in ("E1", "E2"):
        for fn, t0, t1, dep, arr in legs:
            assignments.append({"employee": emp, "flight": fn,
                                "dep_time": t0, "arr_time": t1,
                                "dep_airport": dep, "arr_airport": arr,
                                "duty_id": "%s/D1" % emp, "base": "PLY"})
        duties.append({"employee": emp, "id": "%s/D1" % emp,
                       "start": legs[0][1], "end": legs[-1][2],
                       "base": "PLY", "last_arr_airport": "PLY"})
    return {"solution": "тестовый ростер", "assignments": assignments,
            "duties": duties}


PROMPTS: list[str] = []


def _compiler(prompt):
    """Компилятор предлагает состав «в смене» — как в проходе 6."""
    PROMPTS.append(prompt)
    return json.dumps({
        "checks": [
            {"rule": "R-CREW", "template": "счёт-состава-группы",
             "params": {"sheet": "SOL/Назначения", "group_column": "Смена",
                        "count": 2, "distinct_column": "Табельный номер"},
             "statement": "в каждой смене ровно два пилота", "unit": "смена",
             "severity": "БЛОКИРУЮЩЕЕ"}],
        "skipped": [],
    }, ensure_ascii=False)


def test_world_param_rule_is_applied_by_configuration_not_compiled():
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(), _compiler,
                        thresholds={"applied": [
                            {"threshold": "base_tz_offset_min", "value": 0,
                             "rules": ["R-TZ"]}]})
    v = report["certificate"]
    assert all("R-TZ" not in p for p in PROMPTS), "правило мира ушло компилятору"
    assert v["world_params"] == ["R-TZ"]
    assert v["checks_not_compiled"] == 0 and v["rules_uncovered"] == []
    world = [c for c in report["checks"] if c["template"] == rc.WORLD_PARAM_TEMPLATE]
    assert len(world) == 1 and world[0]["status"] == rc.WORLD_PARAM_STATUS
    assert "применена конфигурацией" in world[0]["reason"]
    assert "+0 мин" in world[0]["reason"] and "порог прогона" in world[0]["reason"]
    # Запись живёт в теле и считается «не прогнанной» — шапка сходится
    # с телом, гейт сертификата (CDPM) зелёный, сертификат выдан.
    assert v["checks_not_run"] == 1 and v["certified"] is True
    from cdpm.gates import gate_certify
    assert gate_certify.check(_svod(), report)[0] == []


def test_composition_unit_comes_from_the_family_not_the_compiler():
    report = rc.certify(_svod(), _solution(), _compiler)
    v = report["certificate"]
    crew = next(c for c in report["checks"]
                if (c.get("basis") or {}).get("rule") == "R-CREW")
    assert crew["status"] == "прогнана", crew
    # Запись проверки в отчёте параметров не несёт — единица и итог
    # говорят за неё: с группой «Смена» было бы два нарушения (E1/D1 и
    # E2/D1 по одному пилоту), с группой «Рейс» — ноль.
    assert crew["unit"] == "рейс"
    assert crew["rows_checked"] == 4
    assert v["findings"] == 0 and v["violated_rules"] == []
    assert any("R-CREW" in n and "«Смена» → «Рейс»" in n
               for n in report["composition_notes"])
    assert v["certified"] is True, v
    from cdpm.gates import gate_certify
    assert gate_certify.check(_svod(), report)[0] == []


def test_composition_normalization_still_catches_a_real_shortfall():
    sol = _solution()
    sol["assignments"] = [a for a in sol["assignments"]
                          if not (a["employee"] == "E2" and a["flight"] == "PL102")]
    report = rc.certify(_svod(), sol, _compiler)
    v = report["certificate"]
    assert v["violated_rules"] == ["R-CREW"] and v["findings"] == 1
    f = report["findings"][0]
    assert "PL102" in f["evidence"] and "различных «Табельный номер» 1" in f["evidence"]
