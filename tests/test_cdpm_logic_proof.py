# -*- coding: utf-8 -*-
"""Ступень 3, 9б-3: доказательства логики — детерминированный
перевод в проверку сертификатора, контрпримерные пары с механическим
гейтом, сверщик пересказа с блокировкой в анкету.

Охраняемое: перевод без модели (расходиться нечему); пара «ровно
одно нарушение именно этого правила / ноль»; молчание сверщика =
расхождение (fail-closed); расхождение блокирует правило вопросом
части 2, а не гейтом («сводка погоды» запрещена)."""
import json

from cdpm import logic_proof as lp
from cdpm import model as m


def _rule_duration(rel="max", subj="duty"):
    return {"id": "R-D", "title": "Смена", "statement":
            "Продолжительность полётной смены не более 12:00.",
            "zone": "BOTH", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "params": [{"name": "max_duty_duration", "value": 720,
                        "unit": "duration_minutes",
                        "as_written": "12:00", "relation": rel}],
            "logic": {"subject": subj,
                      "source_params": ["max_duty_duration"],
                      "demand": {"kind": "duration_within",
                                 "relation": rel,
                                 "param": "max_duty_duration",
                                 "measure": "span",
                                 "as_written": "не более 12:00"}}}


def _rule_count():
    return {"id": "R-C", "title": "Состав", "statement":
            "Экипаж рейса — ровно 2 пилота.",
            "zone": "BOTH", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.2"}],
            "params": [{"name": "crew_size", "value": 2,
                        "unit": "count", "as_written": "2",
                        "relation": "eq"}],
            "logic": {"subject": "flight",
                      "source_params": ["crew_size"],
                      "demand": {"kind": "count_within",
                                 "relation": "eq",
                                 "param": "crew_size",
                                 "as_written": "ровно 2"}}}


def _rule_days_off():
    return {"id": "R-DO", "title": "Выходные", "statement":
            "не менее 8 выходных в календарный месяц",
            "zone": "ROSTERING", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.5"}],
            "params": [{"name": "min_days_off", "value": 8,
                        "unit": "count", "as_written": "8",
                        "relation": "min"}],
            "logic": {"subject": "day_off",
                      "source_params": ["min_days_off"],
                      "demand": {"kind": "count_within",
                                 "relation": "min",
                                 "param": "min_days_off",
                                 "universe": "employee×month",
                                 "as_written": "не менее 8"},
                      "enforce": "required"}}


def test_days_off_template_executor_and_edges():
    """12а: шаблон «выходных-в-месяце» — счёт по клетке
    сотрудник×полный месяц; полночная запись занимает обе даты;
    неполный месяц — честный отказ «универсум пуст», не догадка."""
    from cdpm import data_checks as dc
    spec, why = lp.logic_to_check_spec(_rule_days_off())
    assert why == "" and spec["template"] == "выходных-в-месяце"
    assert spec["params"]["min_days_off"] == 8
    pair, why = lp.generate_pair(_rule_days_off())
    assert pair is not None, why
    n, f_bad = dc.run_spec(spec, pair["dataset_bad"])
    n2, f_good = dc.run_spec(spec, pair["dataset_good"])
    assert len(f_bad) == 1 and f_good == []
    assert "выходных 7 при норме ≥ 8" in f_bad[0]["evidence"]
    # Полночная смена занимает обе даты: занято 3 суток января.
    ds = {"sources": pair["dataset_good"]["sources"],
          "tables": {"SOL": {"Смены": {
              "columns": ["Табельный номер", "Начало", "Конец"],
              "rows": [{"Табельный номер": "EX-1",
                        "Начало": "2030-01-01T22:00",
                        "Конец": "2030-01-02T04:00"},
                       {"Табельный номер": "EX-1",
                        "Начало": "2030-01-31T08:00",
                        "Конец": "2030-01-31T12:00"}]}}}}
    _n, f_mid = dc.run_spec(spec, ds)
    assert f_mid == []
    # Диапазон без полного месяца — отказ с причиной.
    part = {"sources": pair["dataset_good"]["sources"],
            "tables": {"SOL": {"Смены": {
                "columns": ["Табельный номер", "Начало", "Конец"],
                "rows": pair["dataset_good"]["tables"]["SOL"]
                ["Смены"]["rows"][1:-1]}}}}
    try:
        dc.run_spec(spec, part)
        raise AssertionError("ожидался отказ «универсум пуст»")
    except dc.DataChecksError as e:
        assert "универсум пуст" in str(e)
    # Вне первой волны честно: relation не min; чужой универсум.
    r2 = _rule_days_off()
    r2["logic"]["demand"]["relation"] = "eq"
    r2["params"][0]["relation"] = "eq"
    s2, why2 = lp.logic_to_check_spec(r2)
    assert s2 is None and "только min" in why2
    r3 = _rule_days_off()
    r3["logic"]["demand"]["universe"] = "base×day"
    s3, why3 = lp.logic_to_check_spec(r3)
    assert s3 is None and "employee" in why3


def test_logic_to_check_spec_deterministic():
    spec, why = lp.logic_to_check_spec(_rule_duration())
    assert why == "" and spec["template"] == \
        "максимальная-длительность-часов"
    assert spec["params"]["max_hours"] == 12.0
    assert spec["basis"] == {"rule": "R-D"}
    spec2, _ = lp.logic_to_check_spec(_rule_duration())
    assert spec == spec2                       # детерминизм
    # Вид вне первой волны — честный отказ с причиной, не догадка.
    r = _rule_duration()
    r["logic"]["demand"]["kind"] = "exists_universe"
    r["logic"]["demand"]["universe"] = "base×day"
    r["logic"]["demand"]["subject"] = "reserve_duty"
    # CDPM-292: the old fixture also put a duration in a count role.
    # Preserve that input as a type-error control; use a count for the
    # independent unsupported-template assertion below.
    wrong_spec, wrong_why = lp.logic_to_check_spec(r)
    assert wrong_spec is None and "duration_minutes" in wrong_why
    assert "count" in wrong_why
    r = _rule_count()
    r["logic"]["demand"].update(kind="exists_universe",
                                universe="base×day", subject="reserve_duty")
    spec3, why3 = lp.logic_to_check_spec(r)
    assert spec3 is None and "вне первой волны" in why3


def test_pair_generation_and_mechanical_gate():
    """Пара: нарушающее — ровно одно нарушение именно этого правила,
    соблюдающее — ноль; гейт механический, без модели."""
    for rule in (_rule_duration(), _rule_count(), _rule_days_off()):
        pair, why = lp.generate_pair(rule)
        assert pair is not None, (rule["id"], why)
        assert lp.check_pair(pair, rule["id"], rule=rule) == [], rule["id"]
    # Minimum SPAN was formerly (incorrectly) proved by an adjacent GAP.
    # A10 requires an explicit unproven result, not a proof of another rule.
    pair, why = lp.generate_pair(_rule_duration("min"))
    assert pair is None and "min/span не доказан" in why
    # Испорченная пара краснит гейт (мутационная дисциплина).
    pair, _ = lp.generate_pair(_rule_duration())
    pair["dataset_bad"] = pair["dataset_good"]
    probs = lp.check_pair(pair, "R-D")
    assert any("единиц вместо" in p for p in probs)


def test_retell_check_blocks_on_discrepancy_and_silence():
    """Сверщик: совпадение допускает дальше; расхождение и МОЛЧАНИЕ
    блокируют правило вопросом части 2 (fail-closed)."""
    doc = {"meta": {"customer": "Т", "mode": 1,
                    "cdpm_version": "0.1"},
           "sources": [{"sid": "S1", "file": "a.md",
                        "sha256": "0" * 64}],
           "rules": [_rule_duration(), _rule_count()],
           "questions": [], "objectives": []}

    def verifier(prompt):
        return json.dumps({"verdicts": [
            {"rule": "R-D", "equivalent": True},
            {"rule": "R-C", "equivalent": False,
             "discrepancy": "в своде «ровно 2», в пересказе «не "
                            "менее 2»"},
        ]}, ensure_ascii=False)

    summary = lp.retell_check(doc, verifier)
    assert summary["checked"] == 2
    assert summary["equivalent"] == 1
    assert summary["blocked"] == ["R-C"]
    rc = next(r for r in doc["rules"] if r["id"] == "R-C")
    assert rc["blockers"], "расхождение не заблокировало правило"
    q = next(x for x in doc["questions"]
             if x["id"] == rc["blockers"][0])
    assert q["part"] == "2"
    assert "свод" in json.dumps(q, ensure_ascii=False)
    assert m.validate_model(doc) == [], m.validate_model(doc)[:3]
    # Молчание сверщика — тоже блокировка.
    doc2 = {"meta": {"customer": "Т", "mode": 1,
                     "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "a.md",
                         "sha256": "0" * 64}],
            "rules": [_rule_duration()],
            "questions": [], "objectives": []}
    summary2 = lp.retell_check(doc2, lambda _:
                               json.dumps({"verdicts": []}))
    assert summary2["blocked"] == ["R-D"]


def test_package_carries_pairs_and_names_unproven(tmp_path):
    """Пакет: пара едет секцией logic-examples и проходит гейт;
    правило с logic без пары — честно в manifest.logic_unproven;
    молчаливое «доказано» (logic без пары и без записи) — красный."""
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    doc = {"meta": {"customer": "Т", "mode": 1,
                    "cdpm_version": "0.1"},
           "sources": [{"sid": "S1", "file": "a.md",
                        "sha256": "0" * 64}],
           "rules": [_rule_duration(), {
               "id": "R-U", "title": "Универсум",
               "statement": "выходных не менее 8 в месяц",
               "zone": "ROSTERING", "kind": "HARD",
               "sources": [{"sid": "S1", "anchor": "п.3"}],
               "params": [{"name": "max_pairing_days", "value": 8,
                           "unit": "count", "as_written": "8",
                           "relation": "min"}],
               "logic": {"subject": "day_off",
                         "source_params": ["max_pairing_days"],
                         "demand": {"kind": "count_within",
                                    "relation": "min",
                                    "param": "max_pairing_days",
                                    "universe": "employee×month",
                                    "as_written": "не менее 8"}}}, {
               "id": "R-B", "title": "Налёт",
               "statement": "месячный налёт не более 85:00",
               "zone": "ROSTERING", "kind": "HARD",
               "sources": [{"sid": "S1", "anchor": "п.4"}],
               "params": [{"name": "max_monthly_block",
                           "value": 5100,
                           "unit": "duration_minutes",
                           "as_written": "85:00",
                           "relation": "max"}],
               "logic": {"subject": "duty",
                         "source_params": ["max_monthly_block"],
                         "demand": {"kind": "duration_within",
                                    "relation": "max",
                                    "param": "max_monthly_block",
                                    "measure": "block",
                                    "as_written": "не более "
                                                  "85:00"}}}],
           "questions": [], "objectives": []}
    assert m.validate_model(doc) == []
    run = tmp_path / "run-9100"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    files = tp.build_package(run)
    manifest = json.loads(files["manifest.json"])
    examples = json.loads(files["logic-examples.json"])
    assert "R-D" in examples
    # 12а: универсумная форма выходных ДОКАЗУЕМА — пара строится.
    assert "R-U" in examples
    assert [u["rule"] for u in manifest["logic_unproven"]] == ["R-B"]
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    # Молчаливое «доказано»: убираем запись unproven — красный.
    manifest.pop("logic_unproven")
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    del files["logic-examples.json"]
    pkg2 = tmp_path / "p2.zip"
    pkg2.write_bytes(tp.write_zip(files))
    probs, _ = gate.check(pkg2)
    assert any("молчаливое" in p for p in probs)


def test_conditions_refuse_translation_fail_closed():
    """Находка run-0046: пара, построенная с игнорированием
    condition-атомов, доказывает более широкое правило («ночных
    подряд» без starts_in = «любых смен подряд») — перевод первой
    волны обязан честно отказать."""
    r = _rule_duration()
    r["logic"]["condition"] = [
        {"predicate": "longer_than", "param": "max_duty_duration",
         "as_written": "свыше 12:00"}]
    r["logic"]["source_params"] = ["max_duty_duration"]
    spec, why = lp.logic_to_check_spec(r)
    assert spec is None
    assert "condition-атомы" in why and "longer_than" in why
    pair, why2 = lp.generate_pair(r)
    assert pair is None and "condition-атомы" in why2
