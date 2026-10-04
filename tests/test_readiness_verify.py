# -*- coding: utf-8 -*-
"""Верификация матрицы готовности ВМП стороной COSMOS.

Контракт (директива владельца 2026-08-29): матрица приходит из CDPM
(манифест 1.12), COSMOS верифицирует тремя сторонами — пересчёт
клетки из содержимого пакета, реестр исполнителей зоны, фактическая
полнота данных; расхождение любой стороны = запуск запрещён.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
for _p in (str(HERE), str(HERE / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cdpm import export_task_package as tp  # noqa: E402
from template_catalog import requests as law  # noqa: E402
from vmp import readiness_verify as rv  # noqa: E402
from vmp import registry_check as rc  # noqa: E402
from tests.test_cdpm_task_package import _ready_doc  # noqa: E402


def _clean(sheets: dict) -> dict:
    return {"customer": "Т", "tables": {"D1": {
        name: {"columns": list(cols), "rows": list(rows)}
        for name, (cols, rows) in sheets.items()}}}


_SHEETS = {
    "Рейсы": (["id"], [{"id": "F1"}]),
    "Экипажи": (["emp"], [{"emp": "E1"}]),
    "Отпуска": (["emp", "day"], []),
}


def _registry(tmp_path, rule_ids=("R-H", "R-S")) -> Path:
    reg = {
        "created": "2026-08-29T09:00+03:00 (тест)",
        "source": "тестовый реестр",
        "scope": "мини-мир",
        "code_fingerprints": rc.code_fingerprints(HERE),
        "rules": [{"rule_id": rid, "title": rid, "kind": "HARD",
                   "verdict": "covered_full",
                   "components": ["судья"],
                   "evidence": "тест", "verified": "2026-08-29"}
                  for rid in rule_ids],
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg, ensure_ascii=True),
                 encoding="utf-8")
    return p


def _manifest(doc, clean) -> dict:
    return {"spec_version": tp.SPEC_VERSION,
            "readiness_matrix": tp.readiness_ladder(doc, clean)}


def test_зелёная_верификация_обеих_клеток(tmp_path):
    doc, clean = _ready_doc(), _clean(_SHEETS)
    ver = rv.verify(_manifest(doc, clean), doc, clean, HERE,
                    _registry(tmp_path))
    assert ver["verdict"] == "OK"
    p = ver["columns"]["pairing"]
    r = ver["columns"]["rostering"]
    assert p["declared_state"] == "optimization"
    assert p["launch_allowed"] and p["cell_match"]
    assert r["declared_state"] == "feasible_soft"
    assert r["launch_allowed"]
    assert p["criteria_tiers"] == [] and p["optimization_allowed"]


def test_ярусы_критериев_клетки_задачи(tmp_path):
    """Структура ВМП (владелец 2026-09-02): форма пакета с несколькими
    критериями части или ярусом > 1 — оптимизация клетки запрещена
    (решатель исполняет один критерий), режимы выполнимости не задеты;
    ярусы другой части клетку не трогают."""
    doc, clean = _ready_doc(), _clean(_SHEETS)
    form = {"pairing": {"criteria": [{"id": "O-1", "tier": 1},
                                     {"id": "O-2", "tier": 2,
                                      "tolerance": {"value": 5}}]},
            "rostering": {"criteria": [{"id": "O-R", "tier": 1}]}}
    ver = rv.verify(_manifest(doc, clean), doc, clean, HERE,
                    _registry(tmp_path), form=form)
    p = ver["columns"]["pairing"]
    r = ver["columns"]["rostering"]
    assert p["launch_allowed"] and not p["optimization_allowed"]
    assert p["criteria_tiers"] and "один критерий" in p["criteria_tiers"][0]
    assert r["criteria_tiers"] == [] and r["optimization_allowed"] == \
        r["launch_allowed"]
    assert p["registry"]["rules_in_zone"] == 2
    assert r["registry"]["rules_in_zone"] == 1


def test_подделка_клетки_манифеста(tmp_path):
    doc, clean = _ready_doc(), _clean(_SHEETS)
    man = _manifest(doc, clean)
    man["readiness_matrix"]["columns"]["rostering"]["state"] = \
        "optimization"
    ver = rv.verify(man, doc, clean, HERE, _registry(tmp_path))
    assert ver["verdict"] == "MISMATCH"
    cell = ver["columns"]["rostering"]
    assert not cell["launch_allowed"]
    assert any("не совпала" in w for w in cell["why_blocked"])


def test_пробел_реестра_держит_задачу(tmp_path):
    doc, clean = _ready_doc(), _clean(_SHEETS)
    ver = rv.verify(_manifest(doc, clean), doc, clean, HERE,
                    _registry(tmp_path, rule_ids=("R-H",)))
    cell = ver["columns"]["pairing"]
    assert ver["verdict"] == "OK"        # клетки манифеста честны
    assert cell["registry"]["gaps"] == 1  # но реестр зоны не полон
    assert not cell["launch_allowed"]
    assert any("реестр" in w for w in cell["why_blocked"])
    # ростеринг зоны ROSTERING+BOTH реестром покрыт (одно правило)
    assert ver["columns"]["rostering"]["launch_allowed"]
    # Шаг 6 каталога: число пробелов ОСТАЛОСЬ числом (по нему судит
    # клетка), рядом — строки для панели и остановки, с номером заявки.
    rows = cell["registry"]["gap_rows"]
    assert isinstance(cell["registry"]["gaps"], int)
    assert [r["rule_id"] for r in rows] == ["R-S"]
    assert rows[0]["kind"] == "rule_unknown" and rows[0]["note"]
    rid = law.request_id_of(law.request_key(
        "families", "SOFT", "PAIRING", doc["rules"][1]["statement"]))
    assert rows[0]["form"], "форма пробела названа: панель показывает её"
    assert rows[0]["request"]["request_id"] == rid, \
        "номер назван, состояния нет — заявки в каталоге ещё нет"
    assert rows[0]["request"]["state"] is None
    assert "key=" in (rows[0]["request"]["link"] or ""), \
        "ссылка ручной подачи несёт ключ — по нему служба даст тот же номер"
    assert ver["columns"]["rostering"]["registry"]["gap_rows"] == []


def test_строка_пробела_называет_состояние_заявки_каталога(tmp_path,
                                                            monkeypatch):
    """Заявка на этот пробел уже в каталоге — строка несёт её состояние:
    человек видит «принята», а не ищет заявку сам."""
    import catalog_copies as cc
    from template_catalog.export import dump_json
    from vmp import catalog_access as ca
    doc, clean = _ready_doc(), _clean(_SHEETS)
    key = law.request_key("families", "SOFT", "PAIRING",
                          doc["rules"][1]["statement"])
    rid = law.request_id_of(key)
    folder = cc.copy_catalog(tmp_path)
    fams = json.loads((folder / "families.json")
                      .read_text(encoding="utf-8"))["families"]
    draft = {k: v for k, v in fams[0].items()
             if k not in ("stand_tasks", "status", "history")}
    draft.update({"id": "family_" + key[:12], "params": [], "param_sets": [],
                  "conditions": [], "complements": [], "check_templates": [],
                  "subject_words": None, "rule_form": None,
                  "kind": "SOFT", "zone": "PAIRING",
                  "executor_kinds": ["kotlin_knob"]})
    (folder / "requests.json").write_bytes(dump_json({
        "form_version": 1,
        "requests": [{
            "request_id": rid, "state": "принята", "section": "families",
            "record_id": draft["id"],
            "submitted_at": "2026-09-12T10:15:00+03:00",
            "submitted_by": "CDPM",
            "source": {"kind": "посадка категории C", "document_class": None,
                       "run_class": "прогон CDPM",
                       "ref": "run-0088#" + key[:12]},
            "draft": draft, "executor_kinds_requested": None,
            "decision": {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
                         "words": "механика общая, исполнителя написать"},
            "cr_key": None}]}).encode("utf-8"))
    cc.use_catalog(monkeypatch, folder)
    try:
        ver = rv.verify(_manifest(doc, clean), doc, clean, HERE,
                        _registry(tmp_path, rule_ids=("R-H",)))
    finally:
        ca.forget()
    rows = ver["columns"]["pairing"]["registry"]["gap_rows"]
    assert rows[0]["request"]["request_id"] == rid
    assert rows[0]["request"]["state"] == "принята"


def test_отказ_каталога_не_роняет_колонку_реестра(tmp_path, monkeypatch):
    """Классификатор читает каталог; его отказ — не исключение колонки:
    строки пробелов остаются, номер заявки в них пуст (контракт verify
    «никаких исключений»)."""
    from vmp import catalog_access as ca
    empty = tmp_path / "пустой-каталог"
    empty.mkdir()
    monkeypatch.setenv(ca.CATALOG_ENV, str(empty))
    ca.forget()
    doc, clean = _ready_doc(), _clean(_SHEETS)
    try:
        rows = rv.gap_rows([{"rule_id": "R-S", "kind": "rule_unknown",
                             "note": "правила нет в реестре"}], doc)
    finally:
        ca.forget()
    assert rows == [{"rule_id": "R-S", "kind": "rule_unknown",
                     "note": "правила нет в реестре",
                     "form": None, "request": None}]


def test_нехватка_данных_фактом(tmp_path):
    doc = _ready_doc()
    clean = _clean({k: v for k, v in _SHEETS.items()
                    if k != "Отпуска"})
    ver = rv.verify(_manifest(doc, clean), doc, clean, HERE,
                    _registry(tmp_path))
    cell = ver["columns"]["rostering"]
    assert cell["declared_state"] == "stop"       # честная клетка
    assert cell["data_actual_missing"] == ["недоступности"]
    assert not cell["launch_allowed"]
    assert ver["columns"]["pairing"]["launch_allowed"]


def test_лестница_режимов():
    assert rv.state_allows("optimization", "optimization")
    assert rv.state_allows("optimization", "feasibility-hard")
    assert rv.state_allows("feasible_hard", "feasibility-hard")
    assert not rv.state_allows("feasible_hard", "optimization")
    assert not rv.state_allows("stop", "feasibility-hard")
    assert not rv.state_allows(None, "feasibility-hard")
    assert not rv.state_allows("optimization", "чужой-режим")


def test_колонка_недоступностей_считается_разделом():
    clean = {"customer": "Т", "tables": {"D1": {
        "Экипажи": {"columns": ["emp", "unavailableDays"],
                    "rows": []},
        "Рейсы": {"columns": ["id"], "rows": []}}}}
    assert "недоступности" in rv.actual_sections(clean)


def test_матрица_в_состоянии_конвейера(tmp_path, monkeypatch):
    """Панель конвейера несёт верифицированную матрицу: пакет 1.12 из
    CREW_TASK_PACKAGE + реестр из CREW_VMP_REGISTRY; кэш по mtime."""
    from tests.test_cdpm_task_package import _data_dirs, _run_dir
    from tests.test_cdpm_task_package import _FULL_SHEETS
    run = _run_dir(tmp_path, _ready_doc())
    _data_dirs(run, _FULL_SHEETS)
    pkg_path = tmp_path / "package.zip"
    pkg_path.write_bytes(tp.export_zip(run))
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(pkg_path))
    monkeypatch.setenv("CREW_VMP_REGISTRY",
                       str(_registry(tmp_path)))
    from webapp import flow
    flow._READY_CACHE["key"] = None
    st = flow.readiness_state()
    assert st["present"] and st["verdict"] == "OK"
    assert st["columns"]["pairing"]["state"] == "optimization"
    assert st["columns"]["pairing"]["launch_allowed"]
    assert st["columns"]["rostering"]["state"] == "feasible_soft"
    # повторный вызов — из кэша (тот же объект)
    assert flow.readiness_state() is st


def test_панель_без_пакета_честно_пуста(monkeypatch):
    from webapp import flow
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    flow._READY_CACHE["key"] = None
    st = flow.readiness_state()
    assert st == {"present": False}


def test_A1_исполнитель_покрывает_правило_только_с_порогом(tmp_path):
    """Внешний аудит 04.09.2026, A1: реестр говорит «исполнитель есть»
    (duty_limit_ok), но исполнитель судит только по переданному порогу
    ВМП. Ш-1п2 с applied: порог правила не передан — правило не
    покрыто, запуск задачи запрещён с именем правила и параметра; с
    порогом — клетка зелёная. Без applied (служба без П-1) сторона
    порогов не судится (прежнее поведение вызывающих)."""
    doc, clean = _ready_doc(), _clean(_SHEETS)
    doc["rules"][0]["params"] = [
        {"name": "max_duty_duration", "value": 690,
         "unit": "duration_minutes", "relation": "max",
         "as_written": "11:30"}]
    man = _manifest(doc, clean)
    reg = _registry(tmp_path)
    ver0 = rv.verify(man, doc, clean, HERE, reg)
    assert ver0["columns"]["pairing"]["launch_allowed"]
    ver1 = rv.verify(man, doc, clean, HERE, reg,
                     applied={"min_sit_home_min": 60})
    cell = ver1["columns"]["pairing"]
    assert not cell["launch_allowed"]
    assert cell["thresholds_missing"], cell
    assert any("R-H" in w and "max_duty_duration" in w
               for w in cell["why_blocked"]), cell["why_blocked"]
    ver2 = rv.verify(man, doc, clean, HERE, reg,
                     applied={"max_duty_span_min": 690})
    assert ver2["columns"]["pairing"]["launch_allowed"]
    assert ver2["columns"]["pairing"]["thresholds_missing"] == []
