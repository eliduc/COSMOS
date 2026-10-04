# -*- coding: utf-8 -*-
"""Ревизия 1.15 пакета постановки (аудит 04.09.2026, контракт «ведомость
исполнения правил»): применимость правил, требуемые данные, канон
параметров посадки, отпечаток данных; гейт принимает 1.14 и 1.15."""
import hashlib
import json

from cdpm import export_task_package as tp
from cdpm import model as m


def _rule(rid, kind, zone, **extra):
    r = {"id": rid, "title": f"Правило {rid}", "statement": f"Текст {rid}",
         "zone": zone, "kind": kind,
         "sources": [{"sid": "S1", "anchor": f"абз.{len(rid)}"}]}
    r.update(extra)
    return r


def test_applicability_by_kind_and_weight():
    hard = _rule("R-H", "HARD", "PAIRING")
    case = _rule("R-C", "CASE", "BOTH")
    soft_w = _rule("R-SW", "SOFT", "ROSTERING", weight=15)
    soft_0 = _rule("R-S0", "SOFT", "PAIRING")
    dfn = _rule("R-D", "DEF", "BOTH")
    data = _rule("R-DATA", "DATA", "ROSTERING")
    out = _rule("R-OUT", "OUT", "PAIRING")
    excl = _rule("R-X", "HARD", "PAIRING", excluded_until_data=True)
    assert tp.applicability_of(hard) == {
        "tasks": ["pairing"], "modes": list(m.TASK_MODES),
        "note": "жёсткое правило — судится во всех режимах"}
    assert tp.applicability_of(case)["tasks"] == ["pairing", "rostering"]
    assert tp.applicability_of(case)["modes"] == list(m.TASK_MODES)
    assert tp.applicability_of(soft_w)["modes"] == ["optimization",
                                                    "feasibility-soft"]
    assert tp.applicability_of(soft_0)["modes"] == ["optimization"]
    for r in (dfn, data, out, excl):
        assert tp.applicability_of(r)["modes"] == []
    assert "определение" in tp.applicability_of(dfn)["note"]
    assert "данным" in tp.applicability_of(data)["note"]
    assert "исключено" in tp.applicability_of(excl)["note"]


def test_required_data_by_tasks():
    assert tp.required_data_of(_rule("R-H", "HARD", "PAIRING")) == [
        "рейсы", "экипажи"]
    assert tp.required_data_of(_rule("R-H", "HARD", "BOTH")) == [
        "рейсы", "экипажи", "недоступности"]
    assert tp.required_data_of(_rule("R-D", "DEF", "BOTH")) == []
    # DATA — не судится исполнителем, но разделы данных требует по зоне.
    assert tp.required_data_of(_rule("R-DATA", "DATA", "ROSTERING")) == [
        "рейсы", "экипажи", "недоступности"]


def test_fit_params_canon_from_legacy_params():
    r = _rule("R-P", "HARD", "PAIRING",
              params=[{"name": "min_turnaround", "value": 45,
                       "unit": "duration_minutes", "relation": "min",
                       "qualifier": "база", "as_written": "45 мин"},
                      {"name": "min_turnaround", "value": 40,
                       "unit": "duration_minutes", "relation": "min",
                       "qualifier": "вне базы", "as_written": "40 мин"},
                      {"name": "max_duty_duration", "value": 720,
                       "unit": "duration_minutes", "relation": "max",
                       "as_written": "12:00"}],
              template_fit={"category": "A", "family": "connection_windows",
                            "registry_rev": "x",
                            "params": {"min_turnaround": {"value": 40,
                                                          "quote": "40 мин"},
                                       "max_duty_duration": {"value": 720,
                                                             "quote": "12:00"},
                                       "unknown": {"value": 1,
                                                   "quote": "1"}}})
    changed = tp.canon_fit_params(r)
    cells = r["template_fit"]["params"]
    # Две записи одного имени: берётся запись с тем же значением.
    assert cells["min_turnaround"] == {"value": 40, "quote": "40 мин",
                                       "unit": "duration_minutes",
                                       "relation": "min",
                                       "condition": "вне базы"}
    assert cells["max_duty_duration"] == {"value": 720, "quote": "12:00",
                                          "unit": "duration_minutes",
                                          "relation": "max"}
    assert cells["unknown"] == {"value": 1, "quote": "1"}   # не угадываем
    assert changed == 5
    assert tp.canon_fit_params(r) == 0                        # идемпотентно


def test_enrich_is_idempotent_and_schema_valid():
    doc = {"meta": {"customer": "Тест", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.docx",
                        "sha256": "0" * 64}],
           "rules": [_rule("R-H", "HARD", "PAIRING"),
                     _rule("R-S", "SOFT", "ROSTERING", weight=3)],
           "questions": [], "objectives": []}
    assert len(tp.enrich_rules_for_package(doc)) == 2
    assert tp.enrich_rules_for_package(doc) == []
    assert doc["rules"][0]["applicability"]["tasks"] == ["pairing"]
    assert doc["rules"][1]["required_data"] == ["рейсы", "экипажи",
                                                "недоступности"]
    # Поля разрешены схемой модели.
    assert not [p for p in m.validate_schema(doc)
                if "applicability" in p or "required_data" in p]


def test_data_fingerprint_deterministic_and_per_sheet():
    clean = {"customer": "Тест", "tables": {
        "S2": {"рейсы": {"columns": ["a"], "rows": [{"a": 1}, {"a": 2}]},
               "экипажи": {"columns": ["b"], "rows": []}}}}
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    fp1 = tp.data_fingerprint_of(text, clean)
    fp2 = tp.data_fingerprint_of(text, json.loads(text))
    assert fp1 == fp2
    assert fp1["clean_data_sha256"] == hashlib.sha256(
        text.encode("utf-8")).hexdigest()
    assert set(fp1["sheets"]) == {"S2/рейсы", "S2/экипажи"}
    # Меняется строка листа — меняется его отпечаток и общий.
    clean2 = json.loads(text)
    clean2["tables"]["S2"]["рейсы"]["rows"][0]["a"] = 9
    text2 = json.dumps(clean2, ensure_ascii=False, sort_keys=True)
    fp3 = tp.data_fingerprint_of(text2, clean2)
    assert fp3["sheets"]["S2/рейсы"] != fp1["sheets"]["S2/рейсы"]
    assert fp3["sheets"]["S2/экипажи"] == fp1["sheets"]["S2/экипажи"]


def test_spec_version_is_1_15_and_gate_accepts_both(tmp_path):
    """Гейт судит заявленную версию: пакет 1.14 без новых полей законен,
    пакет 1.15 обязан нести их и совпадать с пересчётом."""
    from cdpm.gates import gate_task_package as gate
    assert tp._spec_at_least(tp.SPEC_VERSION, "1.15")
    doc = {"meta": {"customer": "Тест", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}],
           "rules": [_rule("R-H", "HARD", "PAIRING")],
           "questions": [], "objectives": []}
    old_doc = json.loads(json.dumps(doc, ensure_ascii=False))
    new_doc = json.loads(json.dumps(doc, ensure_ascii=False))
    tp.enrich_rules_for_package(new_doc)
    ok_15 = _gate_1_15_problems(gate, tmp_path / "a.zip", "1.15", new_doc)
    bad_15 = _gate_1_15_problems(gate, tmp_path / "b.zip", "1.15", old_doc)
    ok_14 = _gate_1_15_problems(gate, tmp_path / "c.zip", "1.14", old_doc)
    assert ok_15 == [], ok_15
    assert bad_15 and "applicability" in bad_15[0], bad_15
    assert ok_14 == []


def _gate_1_15_problems(gate, path, version: str, doc: dict) -> list[str]:
    """Гейт на минимальном пакете из model.json + manifest.json (без
    комплекта заморозки): возвращает только замечания ревизии 1.15."""
    import zipfile
    model = (json.dumps(doc, ensure_ascii=False, indent=1)).encode("utf-8")
    manifest = {"spec_version": version, "customer": "Тест",
                "source_run": "run-0001", "revision": 1,
                "files": [{"name": "model.json",
                           "sha256": hashlib.sha256(model).hexdigest(),
                           "bytes": len(model)}],
                "readiness": None, "allowed_modes": {}, "objectives": {},
                "has_data": False, "coverage": None}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("model.json", model)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    problems, _counters = gate.check(path)
    return [p for p in problems if "1.15" in p]
