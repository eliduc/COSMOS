# -*- coding: utf-8 -*-
"""Стенд искусственных ВМП (open-bench/vmp-stand): каждая ступень
обязана сходиться с приговором, написанным вместе с материалом.
Изменение проверок постановки, дающее лишнюю находку или пропуск,
роняет этот набор, а не обнаруживается на живом пакете."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
STAND = HERE / "open-bench" / "vmp-stand"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(STAND))

import stand  # noqa: E402
import evaluate_stand as ev  # noqa: E402
from vmp import formulation_check as fc  # noqa: E402

_STEPS = {name: fn for name, fn in stand.STEPS}


@pytest.mark.parametrize("name", sorted(_STEPS))
def test_ступень_сходится_с_приговором(name):
    pkg, expectation = _STEPS[name]()
    got = ev.evaluate(pkg, expectation)
    assert got["ok"], ("ступень %s: вердикт %s (ожидался %s); пропущено %s; "
                       "лишнее %s" % (name, got["verdict"],
                                      got["expected_verdict"],
                                      got["missing"], got["extra"]))


def test_ступени_покрывают_все_классы_проверки():
    """Каждый класс проверки закреплён хотя бы одной ступенью — класс
    без ступени не имеет судьи."""
    covered = set()
    for _name, fn in stand.STEPS:
        _pkg, expectation = fn()
        covered |= {f["class"] for f in expectation["findings"]}
    assert covered == set(fc.CLASSES), set(fc.CLASSES) - covered


def test_здоровая_ступень_проходит_схему_формы_cdpm():
    """Форма стенда обязана быть формой ВМП по схеме производителя —
    иначе стенд судит не тот предмет."""
    pkg, _exp = stand.step_C0()
    from cdpm import vmp_form as vf
    assert vf.validate_form_schema(pkg["form"]) == []


def test_оценщик_ловит_лишнюю_и_недостающую_находку():
    pkg, expectation = stand.step_C0()
    extra = dict(expectation, findings=[
        {"class": "degenerate", "level": "warn", "id": "S-LAYOVER",
         "field": "penalty.coefficient", "part": "pairing"}])
    got = ev.evaluate(pkg, extra)
    assert not got["ok"] and got["missing"] and not got["extra"]
    pkg3, exp3 = stand.step_C3()
    got3 = ev.evaluate(pkg3, dict(exp3, findings=[]))
    assert not got3["ok"] and len(got3["extra"]) == 4


def test_сборка_и_оценка_по_файлам(tmp_path):
    for name, pkg, expectation in stand.build_all():
        stand.write_step(tmp_path, name, pkg, expectation)
    assert not (tmp_path / "С9" / "vmp-form.json").exists()
    assert (tmp_path / "С0" / "vmp-form.json").is_file()
    exp = json.loads((tmp_path / "С3" / "expectation.json")
                     .read_text(encoding="utf-8"))
    assert exp["verdict"] == "rejected"
    assert ev.run_all(tmp_path) == 0
    assert fc.main([str(tmp_path / "С0")]) == 0
    assert fc.main([str(tmp_path / "С3"), "--json"]) == 1


def test_ступень_решателя_объявлена_честно():
    _pkg, exp = stand.step_C2()
    assert exp["requires_solver"] and exp["solver_verdict"] == "INFEASIBLE"
    assert exp["verdict"] == "accepted" and exp["findings"] == []
