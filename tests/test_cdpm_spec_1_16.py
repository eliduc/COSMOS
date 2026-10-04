# -*- coding: utf-8 -*-
"""Ревизия 1.16 пакета постановки (аудит A9): объявление системы времени
в манифесте — шкала меток из правила семьи time_scale, пояс базы из листа
«Аэропорты»; гейт судит заявленную версию."""
import hashlib
import json
import zipfile

from cdpm import export_task_package as tp


def _time_rule(offset=0):
    return {"id": "R-TZ", "title": "Шкала времени данных", "zone": "BOTH",
            "kind": "HARD",
            "statement": f"Все времена — местное время базы; смещение "
                         f"шкалы данных {offset} минут.",
            "sources": [{"sid": "S1", "anchor": "абз.10"}],
            "template_fit": {"category": "A", "family": "time_scale",
                             "registry_rev": "x",
                             "params": {"base_tz_offset_min": {
                                 "value": offset,
                                 "quote": f"смещение шкалы данных {offset} минут"}}}}


def _clean(zones, props=None):
    tables = {"D1": {"Аэропорты": {"columns": ["Код", "timeZone"],
                                   "rows": [{"Код": c, "timeZone": z}
                                            for c, z in zones.items()]}}}
    if props:
        tables["D1"]["Свойства"] = {"columns": ["ключ", "значение"],
                                    "rows": [{"ключ": k, "значение": v}
                                             for k, v in props.items()]}
    return {"customer": "Тест", "tables": tables}


def test_time_scale_and_single_zone():
    doc = {"rules": [_time_rule(0)]}
    t = tp.time_declaration_of(doc, _clean({"PLY": "Europe/Moscow",
                                            "SVG": "Europe/Moscow"}))
    assert t == {"time_scale": "base-local", "base_tz_offset_min": 0,
                 "source_rule": "R-TZ", "base_time_zone": "Europe/Moscow",
                 "base_time_zone_basis":
                     "единственный пояс всех аэропортов листа"}


def test_shifted_scale_and_main_base_property():
    doc = {"rules": [_time_rule(-180)]}
    zones = {"PLY": "Europe/Moscow", "NSK": "Asia/Novosibirsk"}
    t = tp.time_declaration_of(doc, _clean(zones, {"главная база": "NSK"}))
    assert t["time_scale"] == "shifted" and t["base_tz_offset_min"] == -180
    assert t["base_time_zone"] == "Asia/Novosibirsk"
    assert "NSK" in t["base_time_zone_basis"]


def test_ambiguous_zone_is_not_guessed_and_no_rule_gives_none():
    doc = {"rules": [_time_rule(0)]}
    t = tp.time_declaration_of(doc, _clean({"PLY": "Europe/Moscow",
                                            "NSK": "Asia/Novosibirsk"}))
    assert "base_time_zone" not in t and t["time_scale"] == "base-local"
    assert tp.time_declaration_of({"rules": []}, _clean({"PLY": "X"})) is None
    # Без данных — шкала есть, пояса нет.
    assert "base_time_zone" not in tp.time_declaration_of(doc, None)


def test_gate_checks_time_only_from_1_16(tmp_path):
    from cdpm.gates import gate_task_package as gate
    assert tp._spec_at_least(tp.SPEC_VERSION, "1.16")
    doc = {"meta": {"customer": "Тест", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}],
           "rules": [_time_rule(0)], "questions": [], "objectives": []}
    tp.enrich_rules_for_package(doc)
    want = tp.time_declaration_of(doc, None)

    def problems(version, time_value, path):
        model = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
        manifest = {"spec_version": version, "customer": "Тест",
                    "source_run": "run-0001", "revision": 1,
                    "files": [{"name": "model.json",
                               "sha256": hashlib.sha256(model).hexdigest(),
                               "bytes": len(model)}],
                    "readiness": None, "allowed_modes": {},
                    "objectives": {}, "has_data": False, "coverage": None}
        if time_value is not None:
            manifest["time"] = time_value
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("model.json", model)
            z.writestr("manifest.json",
                       json.dumps(manifest, ensure_ascii=False))
        probs, _ = gate.check(path)
        return [p for p in probs if "1.16" in p]

    assert problems("1.16", want, tmp_path / "ok.zip") == []
    assert problems("1.16", None, tmp_path / "missing.zip")
    wrong = dict(want, time_scale="shifted")
    assert problems("1.16", wrong, tmp_path / "wrong.zip")
    assert problems("1.15", None, tmp_path / "old.zip") == []
