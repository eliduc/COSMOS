# -*- coding: utf-8 -*-
"""Проверки постановки (vmp/formulation_check.py) — краевые случаи,
которые ступени стенда не разыгрывают: данные отсутствуют, поле
перечня кодов структурное, штраф у жёсткого, ярус не число, метрика
вне словаря, отчёт."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "open-bench" / "vmp-stand"))

import stand  # noqa: E402
from vmp import formulation_check as fc  # noqa: E402


def _healthy() -> dict:
    pkg, _exp = stand.step_C0()
    return pkg


def _classes(result, level=None):
    return sorted((f["class"], f["id"], f["field"]) for f in result["findings"]
                  if level is None or f["level"] == level)


def test_здоровая_принята_и_факты_данных_посчитаны():
    res = fc.check(_healthy())
    assert res["verdict"] == fc.ACCEPTED and res["findings"] == []
    assert res["data"]["bases"] == ["KGD", "LED"]
    assert res["data"]["airports"] == ["ARH", "KGD", "LED"]
    assert res["data"]["aircraft_types"] == ["ЛТ-100"]
    assert res["data"]["horizon_min"] == 3 * 24 * 60
    assert res["data"]["crew_count"] == 8


def test_без_данных_сверка_кодов_названа_предупреждением():
    pkg = _healthy()
    pkg["clean_data"] = None
    res = fc.check(pkg)
    assert res["verdict"] == fc.ACCEPTED
    assert _classes(res, fc.WARN) == [(fc.CODE_NOT_IN_DATA, None, "clean_data")]


def test_структурное_поле_перечня_кодов_читается_раньше_слов():
    """Заявка CDPM: machine.codes у определения-перечня. Слова
    формулировки при нём не разбираются."""
    pkg = _healthy()
    d = pkg["form"]["common"]["definitions"][0]
    d["statement"] = "Базы приписки экипажей перечислены в приложении."
    d["machine"]["codes"] = ["LED", "KGD", "SVX"]
    res = fc.check(pkg)
    hits = [f for f in res["findings"] if f["class"] == fc.CODE_NOT_IN_DATA]
    assert len(hits) == 1 and hits[0]["codes"] == ["SVX"]
    # Форма CDPM (решение 03.09): объекты {"code", "quote"}; смесь со
    # строками допустима.
    d["machine"]["codes"] = [{"code": "LED", "quote": "LED (Пулково)"},
                             {"code": "OVB", "quote": "OVB (Толмачёво)"},
                             "KGD"]
    res = fc.check(pkg)
    hits = [f for f in res["findings"] if f["class"] == fc.CODE_NOT_IN_DATA]
    assert len(hits) == 1 and hits[0]["codes"] == ["OVB"]


def test_определение_должностей_не_перечень_типов_судов():
    """Расширенный H0 (03.09): «Должности пилотов: КВС и Второй пилот»
    судилось как перечень типов судов из-за подстроки «вс» в «Второй» —
    слова обязаны совпадать целиком; определение парка при этом
    остаётся перечнем типов."""
    pkg = _healthy()
    defs = pkg["form"]["common"]["definitions"]
    defs.append({"id": "D-ROLES", "title": "Должности пилотов",
                 "statement": "Должности пилотов — КВС и Второй пилот.",
                 "sources": [{"sid": "S1", "anchor": "п.1.3"}], "role": "term",
                 "machine": {"kind": "DEF", "codes": ["КВС"]}})
    res = fc.check(pkg)
    assert res["verdict"] == fc.ACCEPTED, res["findings"]
    assert fc._codes_of_definition(defs[-1]) is None
    park = {"id": "D-PARK", "title": "Парк воздушных судов",
            "statement": "Парк — воздушные суда типа ЛТ-100.",
            "machine": {"codes": [{"code": "ЛТ-100", "quote": "ЛТ-100"}]}}
    assert fc._codes_of_definition(park) == ("aircraft_type", {"ЛТ-100"})
    bases = {"id": "D-B", "title": "Базы приписки",
             "statement": "Базы приписки — LED, KGD."}
    assert fc._codes_of_definition(bases) == ("base", {"LED", "KGD"})


def test_типы_судов_из_параметра_правила_допуска():
    """Связь «правило допуска ↔ данные» через параметр fleet_types /
    fleet_type (единицы «перечень кодов» / «код»), независимо от
    определений: названный тип обязан быть в колонке «Тип ВС»."""
    pkg = _healthy()
    res = fc.check(pkg)
    assert res["verdict"] == fc.ACCEPTED, res["findings"]      # ЛТ-100 в книге
    rule = next(r for r in pkg["model"]["rules"] if r["id"] == "W-FLEET")
    # Этот тест — прежняя дорога params[] без посадки. Здоровая ступень
    # содержит оба носителя; изменение только старого значения теперь
    # НЕ меняет эффективное значение исполнения (первична посадка).
    # Их расхождение отдельно держит A05; обе дороги и их приоритет
    # проверяет test_cdpm_answer_codes_consumer.py.
    rule.pop("template_fit", None)
    rule["params"][0]["value"] = ["ЛТ-100", "ЛТ-900"]            # перечень списком
    res = fc.check(pkg)
    hits = [f for f in res["findings"] if f["field"] == "params.fleet_types"]
    assert len(hits) == 1 and hits[0]["codes"] == ["ЛТ-900"] and hits[0]["level"] == fc.STOP
    rule["params"][0] = {"name": "fleet_type", "value": "ЛТ-700",
                         "unit": "code", "relation": "eq"}
    res = fc.check(pkg)
    assert [f["field"] for f in res["findings"]] == ["params.fleet_type"]
    pkg["clean_data"] = stand.base_data(type_column=False)
    res = fc.check(pkg)
    warn = [f for f in res["findings"] if f["field"] == "params.fleet_type"]
    assert warn and warn[0]["level"] == fc.WARN and res["verdict"] == fc.ACCEPTED


def test_типы_судов_без_колонки_в_данных_предупреждение():
    pkg = _healthy()
    pkg["clean_data"] = stand.base_data(type_column=False)
    res = fc.check(pkg)
    assert res["verdict"] == fc.ACCEPTED
    # Без колонки «Тип ВС» не сверить ни определение парка, ни
    # параметр правила допуска — оба предупреждения, не стопы.
    assert _classes(res, fc.WARN) == [
        (fc.CODE_NOT_IN_DATA, "D-FLEET", "statement"),
        (fc.CODE_NOT_IN_DATA, "W-FLEET", "template_fit.params.fleet_types")]


def test_штраф_у_жёсткого_и_единица_вне_словаря():
    pkg = _healthy()
    pkg["form"]["pairing"]["hard"][0]["penalty"] = {"coefficient": 1,
                                                     "violation_unit": "night"}
    pkg["form"]["pairing"]["soft"][0]["penalty"]["violation_unit"] = "week"
    res = fc.check(pkg)
    assert res["verdict"] == fc.REJECTED
    assert _classes(res, fc.STOP) == [
        (fc.VIOLATION_UNIT, "H-DUTY", "penalty"),
        (fc.VIOLATION_UNIT, "S-LAYOVER", "penalty.violation_unit")]


def test_коэффициент_не_целое_и_ярус_не_число():
    pkg = _healthy()
    pkg["form"]["pairing"]["soft"][0]["penalty"]["coefficient"] = 2.5
    pkg["form"]["rostering"]["criteria"][0]["tier"] = "первый"
    res = fc.check(pkg)
    assert (fc.VIOLATION_UNIT, "S-LAYOVER", "penalty.coefficient") \
        in _classes(res, fc.STOP)
    assert (fc.TIERS_DISORDERED, "O-R1", "tier") in _classes(res, fc.STOP)


def test_метрика_вне_словаря_направление_пусто_допуск_у_первого_яруса():
    pkg = _healthy()
    c = pkg["form"]["pairing"]["criteria"][0]
    c["metric"] = "happiness"
    c["direction"] = "best"
    c["tolerance"] = {"value": 1, "unit": "percent"}
    res = fc.check(pkg)
    assert _classes(res, fc.STOP) == [
        (fc.CRITERION_NO_METRIC, "O-P1", "direction"),
        (fc.CRITERION_NO_METRIC, "O-P1", "metric"),
        (fc.TIERS_DISORDERED, "O-P1", "tolerance")]


def test_нет_первого_яруса():
    pkg = _healthy()
    pkg["form"]["pairing"]["criteria"][0]["tier"] = 2
    pkg["form"]["pairing"]["criteria"][0]["tolerance"] = {"value": 1,
                                                          "unit": "absolute"}
    res = fc.check(pkg)
    assert (fc.TIERS_DISORDERED, "O-P1", "tier") in _classes(res, fc.STOP)


def test_исключённое_до_данных_не_противоречит():
    pkg = _healthy()
    dup = copy.deepcopy(pkg["model"]["rules"][-3])          # H-REST-HOME
    assert dup["id"] == "H-REST-HOME"
    dup["id"] = "H-REST-HOME-OLD"
    dup["params"][0]["value"] = 600
    dup["excluded_until_data"] = {"question": "Q-1", "revision": 1}
    pkg["model"]["rules"].append(dup)
    res = fc.check(pkg)
    assert res["verdict"] == fc.ACCEPTED, res["findings"]


def test_противоречие_называет_первую_по_порядку_строку():
    pkg, _exp = stand.step_C1a()
    res = fc.check(pkg)
    hit = [f for f in res["findings"] if f["class"] == fc.CONTRADICTION][0]
    assert hit["rules"] == ["H-REST-HOME", "H-REST-HOME-2"]
    assert hit["values"] == [720.0, 660.0]
    assert "строка H-REST-HOME против строки H-REST-HOME-2" in hit["message"]


def test_отчёт_читаем_и_считает():
    pkg, _exp = stand.step_C6()
    res = fc.check(pkg)
    text = fc.report(res)
    assert text.startswith("ПОСТАНОВКА: ОТВЕРГНУТА (стоп 3, предупреждений 0)")
    assert "pairing/S-LAYOVER.penalty.violation_unit" in text
    assert res["counts"] == {fc.VIOLATION_UNIT: 3}


def test_командная_строка_без_аргументов():
    assert fc.main([]) == 2


def test_сжатый_вердикт_приёмника_показание_не_замок():
    """summary — показание для карточки и канала: без формы «не
    проверено» (checked=False), с формой — вердикт и находки."""
    pkg9, _e = stand.step_C9()
    s9 = fc.summary(pkg9)
    assert s9["checked"] is False and s9["verdict"] == fc.REJECTED
    pkg3, _e = stand.step_C3()
    s3 = fc.summary(pkg3)
    assert s3["checked"] is True and s3["verdict"] == fc.REJECTED
    assert s3["stops"] == 4 and s3["findings_total"] == 4
    assert {f["id"] for f in s3["findings"]} == {"D-BASES", "D-FLEET",
                                                  "H-DUTY-MMK", "W-FLEET-X"}
    pkg0, _e = stand.step_C0()
    s0 = fc.summary(pkg0)
    assert s0 == {"checked": True, "verdict": fc.ACCEPTED, "stops": 0,
                  "warnings": 0, "counts": {}, "findings": [],
                  "findings_total": 0}


def test_состояние_конвейера_несёт_показание_постановки(tmp_path,
                                                        monkeypatch):
    """flow.readiness_state: ключ formulation рядом с матрицей —
    пакет ступени С3 упакован как принятый zip."""
    import hashlib
    import json
    import zipfile
    from webapp import flow, task_channel as tc
    pkg3, _e = stand.step_C3()
    members = {"model.json": pkg3["model"], "objective.json": pkg3["objective"],
               "clean-data.json": pkg3["clean_data"],
               "vmp-form.json": pkg3["form"]}
    raw = {n: json.dumps(b, ensure_ascii=False).encode("utf-8")
           for n, b in members.items()}
    manifest = dict(pkg3["manifest"], files=[
        {"name": n, "sha256": hashlib.sha256(b).hexdigest()}
        for n, b in raw.items()])
    z = tmp_path / "package.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for n, b in raw.items():
            zf.writestr(n, b)
    monkeypatch.setattr(tc, "current_package", lambda: {"path": str(z)})
    monkeypatch.setattr(flow, "_READY_CACHE", {"key": None, "value": None})
    st = flow.readiness_state()
    assert st["present"], st
    f = st["formulation"]
    assert f["checked"] is True and f["verdict"] == fc.REJECTED
    assert f["stops"] == 4 and f["counts"] == {fc.CODE_NOT_IN_DATA: 4}
