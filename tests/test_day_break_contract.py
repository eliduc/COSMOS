# -*- coding: utf-8 -*-
"""Литерал DAY_BREAK_THRESHOLD_MIN — контракт Kotlin по умолчанию профиля
ВНЕ пакета; зеркало на стороне Python — webapp.chain_seed.SOLVER_DAY_BREAK_MIN
(затравка и колонки вне пакетного пути). Для ПАКЕТНЫХ прогонов граница
«стыковка / отдых» одна у построителя, решателя и проверки по ИДАК и
выводится из правил пакета (vmp.sit_boundary; решение владельца
06.09.2026): кнопка «Пейринг» передаёт её построителю как --sit-max.

Тест держит три точки в согласии: константа Kotlin = зеркало Python, и
источник границы пакета её не читает; построитель с границей из правил
пакета НЕ склеивает в смену паузу не короче неё; без --sit-max верх окна
— пол отдыха станции."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KT = (REPO / "kotlin-solver" / "src" / "main" / "kotlin" / "org" / "acme"
      / "flighcrewscheduling" / "solver"
      / "FlightCrewSchedulingConstraintProvider.kt")


def _kotlin_day_break_min() -> int:
    src = KT.read_text(encoding="utf-8")
    m = re.search(r"const val DAY_BREAK_THRESHOLD_MIN\s*=\s*([0-9*+ ()]+)", src)
    assert m, "в решателе нет DAY_BREAK_THRESHOLD_MIN"
    expr = m.group(1).strip()
    assert re.fullmatch(r"[0-9*+ ()]+", expr)
    return int(eval(expr))  # noqa: S307 — только цифры и * + ( )


def test_зеркало_константы_решателя_совпадает_и_пакетом_не_читается():
    """Литерал профиля вне пакета один у Kotlin и Python; путь пакета
    (кнопка, источник границы) к нему не обращается."""
    import ast
    from webapp import chain_seed as cs
    assert cs.SOLVER_DAY_BREAK_MIN == _kotlin_day_break_min() == 360
    # источник границы пакета: ни импорта зеркала, ни числа 360 в коде
    # (докстринг называет литерал словами — это не чтение)
    tree = ast.parse((REPO / "vmp" / "sit_boundary.py")
                     .read_text(encoding="utf-8"))
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom))
                   and "webapp" in ast.dump(n) for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Constant) and n.value == 360
                   for n in ast.walk(tree))
    btn_src = (REPO / "vmp" / "run" / "button.py").read_text(encoding="utf-8")
    assert "from webapp import chain_seed" not in btn_src
    assert "SOLVER_DAY_BREAK_MIN)" not in btn_src


def test_построитель_режет_смену_границей_из_правил_пакета(monkeypatch):
    """Без правила максимальной стыковки кнопка --sit-max не подаёт, и
    верх окна стыковки построителя — пол отдыха станции (720 на базе /
    600 вне базы) — ровно by_place источника границы (полы 720 / 600,
    ручки нет → показанием T = 600); с ручкой max_sit_duration = 240
    кнопка подаёт --sit-max=240 — верх окна 240 на любой станции."""
    import _pairing_builder as pb
    from vmp import sit_boundary as sb

    class _A:
        rest_base = 720
        rest_off = 600
        sit_max = None

    cls = getattr(pb, "PairingBuilder", None) or getattr(pb, "Builder", None)
    assert cls is not None, "класс построителя не найден"
    b = cls.__new__(cls)
    b.args = _A()
    b.bases = {"PLY", "SVG", "GRT"}
    b.report = {}
    got = sb.derive({"min_rest_home_min": 720, "min_rest_outstation_min": 600,
                     "day_break_threshold_min": 0})
    assert got["value"] == 600 and got["basis"] == "rest_floor"
    assert got["by_place"] == {"home": 720, "outstation": 600}
    assert b.sit_cap_at("PLY") == float(got["by_place"]["home"]) == 720.0
    assert b.sit_cap_at("AAA") == float(got["by_place"]["outstation"]) == 600.0
    assert "по полу отдыха" in b.report["sit_window"]["mode"]
    assert b.report["sit_window"]["cap_base_min"] == 720
    assert b.report["sit_window"]["cap_outstation_min"] == 600
    # с ручкой max_sit_duration порог — из неё, на любой станции
    got2 = sb.derive({"min_rest_home_min": 720, "min_rest_outstation_min": 600,
                      "day_break_threshold_min": 240})
    assert got2["value"] == 240 and got2["basis"] == "knob"
    assert got2["assumption"] is None
    b.args.sit_max = got2["value"]
    b.report = {}
    assert b.sit_cap_at("PLY") == 240.0 and b.sit_cap_at("AAA") == 240.0
    assert b.report["sit_window"]["mode"].startswith("задан --sit-max")
    assert "максимальной стыковки" in b.report["sit_window"]["mode"]
    assert b.report["sit_window"]["cap_min"] == 240
