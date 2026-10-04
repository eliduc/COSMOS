# -*- coding: utf-8 -*-
"""Кнопка «Пейринг» и граница «стыковка / отдых» ИЗ ПРАВИЛ ПАКЕТА
(решение владельца 06.09.2026, P3-1 единой версии; уточнения GPT-053 /
CDPM-038): граница вычисляется один раз при нажатии из применённых
порогов решателя для этого пакета (vmp.sit_boundary) — правило
максимальной стыковки (ручка max_sit_duration → day_break_threshold_min
> 0) едет построителю ключом --sit-max; без него ключ не подаётся —
построитель режет смену по полу отдыха станции (--rest-base /
--rest-off из тех же applied), и это заявлено как модельное упрощение в
провенансе и в assumptions выдачи; литерал контракта решателя
(chain_seed.SOLVER_DAY_BREAK_MIN = 360) для пакета не читается; ни
ручки, ни пола — остановка словами, подстановок нет."""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_package_runs import (  # noqa: F401 — фикстуры и оснастка
    FakeSolver, _CATALOG_OK, _env, _make_current, _pairing_kwargs)
from tests.test_run_button import _APPLIED


def _press(tmp_path, applied: dict, applied_rules: dict | None = None):
    """Нажатие «Пейринг» на стендовом пакете с заданными применёнными
    порогами решателя; возврат (состояние, аргументы построителя | None,
    builder-input.json | None, delivery.json)."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    fakes["coverage_fn"] = lambda solver, zb, task: {
        "uncovered": [], "objectives": [], "applied": dict(applied),
        "applied_rules": applied_rules}
    seen: dict = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-payload.json").write_text(
            json.dumps(payload), encoding="utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    st = pr.press("pairing", solver=FakeSolver(), pairing_builder=builder,
                  start_thread=False, sleep=lambda *_: None, **fakes)
    wd = pr.workdir_for(pr.status()["package_sha"])
    bi_path = wd / "button-pairing" / "builder-input.json"
    bi = (json.loads(bi_path.read_text(encoding="utf-8"))
          if bi_path.is_file() else None)
    delivery = json.loads((wd / "button-pairing" / "delivery.json")
                          .read_text(encoding="utf-8"))
    return st, seen.get("args"), bi, delivery


def test_без_ручки_граница_по_полу_отдыха_пакета_и_упрощение_заявлено(tmp_path):
    """Пакет без ручки max_sit_duration (все Н0): полы отдыха 720 дома /
    600 вне базы → --sit-max НЕ подаётся (построитель режет по полу отдыха
    станции из тех же applied: --rest-base 720 / --rest-off 600), показанием
    T = 600 (меньший пол), провенанс «выведено из пола минимального отдыха
    600 мин (правила …)» с модельным упрощением словами; литерала 360 нет;
    допущение — в assumptions выдачи (sit_boundary) рядом с историей."""
    from vmp import sit_boundary as sb
    from webapp import chain_seed as cs
    st, args, bi, delivery = _press(
        tmp_path, _APPLIED,
        applied_rules={"min_rest_home_min": ["VN-023"],
                       "min_rest_outstation_min": ["VN-024"]})
    assert st["verdict"] == "пейринг построен", st
    assert args and "--sit-max" not in args, args
    assert str(cs.SOLVER_DAY_BREAK_MIN) not in args
    assert args[args.index("--rest-base") + 1] == "720"
    assert args[args.index("--rest-off") + 1] == "600"
    prov = bi["provenance"]["--sit-max"]
    assert prov["value"] is None and prov["passed"] is False
    assert prov["boundary_min"] == 600 and prov["basis"] == "rest_floor"
    assert prov["reading"] == "min_rest_outstation_min"
    assert prov["by_place"] == {"home": 720, "outstation": 600}
    assert "выведено из пола минимального отдыха 600 мин" in prov["from"]
    assert "VN-024" in prov["from"] and "VN-023" in prov["from"]
    assert "min_rest_home_min=720" in prov["from"]
    assert "модельное упрощение" in prov["from"]
    assert prov["assumption"] == sb.ASSUMPTION \
        and "МОДЕЛЬНОЕ УПРОЩЕНИЕ" in prov["assumption"]
    assert "ключ не подан" in prov["note"] and prov["terms"] == sb.TERMS
    assert "контракт решателя" not in prov["from"] and "360" not in prov["from"]
    assert set(prov["rules"]) == {"VN-023", "VN-024"}
    # допущение видно в выдаче и состоянии (A7-механика assumptions)
    for holder in (delivery["assumptions"], st["assumptions"]):
        sbnd = holder["sit_boundary"]
        assert sbnd["basis"] == "rest_floor" and sbnd["boundary_min"] == 600
        assert sbnd["assumption"] == sb.ASSUMPTION
        assert sbnd["by_place"] == {"home": 720, "outstation": 600}
    assert "history" in delivery["assumptions"]      # запись A7 на месте


def test_кнопка_берёт_порог_из_ручки_max_sit_duration(tmp_path):
    """Пакет с правилом максимальной стыковки (ручка решателя
    max_sit_duration → day_break_threshold_min=240): --sit-max=240 у
    построителя, провенанс «правило пакета (max_sit_duration)» с именем
    правила; модельного упрощения нет — порог объявлен пакетом."""
    st, args, bi, delivery = _press(
        tmp_path, dict(_APPLIED, day_break_threshold_min=240),
        applied_rules={"day_break_threshold_min": ["VN-030"],
                       "min_rest_outstation_min": ["VN-024"]})
    assert st["verdict"] == "пейринг построен", st
    assert args[args.index("--sit-max") + 1] == "240"
    assert args.count("--sit-max") == 1
    prov = bi["provenance"]["--sit-max"]
    assert prov["value"] == 240 and prov["passed"] is True
    assert prov["basis"] == "knob" and prov["reading"] == "day_break_threshold_min"
    assert "правило пакета (max_sit_duration)" in prov["from"]
    assert "максимальная стыковка" in prov["from"]
    assert "VN-030" in prov["from"] and prov["rules"] == ["VN-030"]
    assert prov["assumption"] is None and prov["note"] is None
    assert delivery["assumptions"]["sit_boundary"]["basis"] == "knob"
    assert delivery["assumptions"]["sit_boundary"]["assumption"] is None


def test_без_ручки_и_без_пола_отдыха_кнопка_останавливается_словами(tmp_path):
    """Ни ручки max_sit_duration, ни пола отдыха в применённых порогах —
    остановка «граница смены не определена правилами пакета»; построитель
    не запускается, литерал не подставляется."""
    from vmp import sit_boundary as sb
    applied = {k: v for k, v in _APPLIED.items()
               if not k.startswith("min_rest")}
    st, args, bi, delivery = _press(tmp_path, applied)
    assert st["verdict"] == "остановка: " + sb.UNDEFINED, st
    assert args is None and bi is None
    assert sb.UNDEFINED in delivery["detail"]
    assert "day_break_threshold_min" in delivery["detail"] \
        and "min_rest_home_min" in delivery["detail"]
    assert "360" not in delivery["detail"]
