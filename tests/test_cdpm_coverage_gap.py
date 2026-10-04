# -*- coding: utf-8 -*-
"""Пробел покрытия — «стоп → заявка → шаблон» (слово владельца
09.09.2026, набор Н0-C): пока у машинного правила нет шаблона (посадка
категории C): (1) линия данных не задаёт ему вопрос «нет данных», след
в отчёте данных, устаревший открытый вопрос снимается кодом; (2) пакет
постановки не выдаётся — готовность ниже «в» с основанием и подписью,
матрица готовности держит задачу зоны, выпуск отказывает словами."""
from cdpm import export_task_package as etp
from cdpm import model as m

REV = m.registry_fingerprint()


def _rule(rid, zone, kind="HARD"):
    return {"id": rid, "title": "Т", "statement": f"правило {rid}",
            "zone": zone, "kind": kind,
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def _doc(*rules):
    return {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
            "rules": list(rules), "questions": [], "objectives": []}


def _doc_with_gap(kind: str = "HARD"):
    """Свод из двух машинных строк: пейринг с посадкой A, ростеринг без
    шаблона (посадка C) — как VN-026 в Н0-C."""
    p = _rule("R-P", "PAIRING")
    p["template_fit"] = {"category": "A", "registry_rev": REV,
                         "family": "duty_limits",
                         "params": {"max_duty_duration": {
                             "value": 720, "quote": "не более 12:00"}}}
    r = _rule("R-R", "ROSTERING", kind)
    r["template_fit"] = {"category": "C", "registry_rev": REV,
                         "gap": "нет механики серии смен подряд"}
    return _doc(p, r), r


def test_coverage_gap_rules_are_machine_c_fits_only():
    doc, r = _doc_with_gap()
    assert [x["id"] for x in m.coverage_gap_rules(doc)] == [r["id"]]
    r["kind"] = "DEF"
    assert m.coverage_gap_rules(doc) == []
    r["kind"] = "HARD"
    r["template_fit"] = {"category": "A", "registry_rev": REV,
                         "family": "rest_floors",
                         "params": {"min_rest_home": {"value": 720,
                                                      "quote": "08:00"}}}
    assert m.coverage_gap_rules(doc) == []


def test_readiness_below_v_with_reason_and_words():
    doc, r = _doc_with_gap()                    # зона ROSTERING
    got = m.readiness(doc)
    assert got["state"] == "б"
    assert got["pairing_ready"] and not got["rostering_ready"]
    assert got["rostering_open"] == {m.COVERAGE_GAP_TAG: 1}
    assert "нет шаблона" in got["label"] and r["id"] in got["label"]
    # Посадка на семью с ПОЛНЫМ набором параметров (K01-барьер 12.09.2026:
    # семья с одним параметром из двух держала бы готовность сама по
    # себе — «параметр … не задан и не спрошен»).
    r["template_fit"] = {"category": "A", "registry_rev": REV,
                         "family": "rest_floors",
                         "params": {"min_rest_home": {"value": 720,
                                                      "quote": "08:00"},
                                    "min_rest_outstation": {
                                        "value": 600, "quote": "10:00"}}}
    assert m.readiness(doc)["state"] == "в"


def test_readiness_matrix_holds_task_of_the_zone():
    doc, r = _doc_with_gap()
    matrix = m.readiness_matrix(doc)
    hard = matrix["rostering"]["feasibility-hard"]
    assert not hard["ready"]
    assert hard["reasons"] == {m.COVERAGE_GAP_TAG: 1}
    assert matrix["pairing"]["feasibility-hard"]["ready"]
    # Мягкая строка без шаблона держит режим штрафов, не выполнимость.
    doc2, r2 = _doc_with_gap(kind="SOFT")
    matrix2 = m.readiness_matrix(doc2)
    assert matrix2["rostering"]["feasibility-hard"]["ready"]
    assert not matrix2["rostering"]["feasibility-soft"]["ready"]
    assert m.COVERAGE_GAP_TAG in matrix2["rostering"]["feasibility-soft"]["reasons"]


def test_no_data_question_not_asked_for_rule_without_template():
    doc, r = _doc_with_gap()
    report = {"checks": [
        {"id": "C-1", "status": "не скомпилирована",
         "reason": "нет листа «Смены»", "basis": {"rule": r["id"]}}],
        "findings": []}
    assert m.ensure_missing_data_questions(doc, report) == []
    assert not any(str(q["id"]).startswith(("Q-3a-нет-данных-",
                                             "Q-3b-нет-данных-"))
                   for q in doc["questions"])
    assert report["coverage_gap"][0]["rule"] == r["id"]
    assert "шаблона в каталоге нет" in report["coverage_gap"][0]["note"]
    assert not r.get("blockers")
    assert m.validate_model(doc) == []


def test_stale_no_data_question_withdrawn_when_rule_lost_template():
    r = _rule("R-R", "ROSTERING")               # без посадки — вопрос законен
    doc = _doc(r)
    report = {"checks": [
        {"id": "C-1", "status": "не скомпилирована",
         "reason": "нет листа «Смены»", "basis": {"rule": r["id"]}}],
        "findings": []}
    created = m.ensure_missing_data_questions(doc, report)   # без посадки — вопрос есть
    assert len(created) == 1 and r["blockers"] == created
    r["template_fit"] = {"category": "C", "registry_rev": REV,
                         "gap": "нет механики"}
    report2 = {"checks": list(report["checks"]), "findings": []}
    assert m.ensure_missing_data_questions(doc, report2) == []
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    # A04: a code decision has its own record, never a fabricated airline answer.
    from cdpm import question_resolutions
    assert not m.is_open_question(q) and "answer" not in q
    assert question_resolutions.complete(q)
    assert q["code_resolutions"][0]["data"]["outcome"] == "coverage_gap"
    assert "пакет не выдаётся" in q["code_resolutions"][0]["reason"]
    assert report2["withdrawn_no_data_questions"] == created
    assert not r.get("blockers")
    assert m.validate_model(doc) == []


def test_package_refused_while_template_missing():
    doc, r = _doc_with_gap()
    words = etp.coverage_gap_problem(doc)
    assert words and r["id"] in words and "не выдаётся" in words
    r["template_fit"] = {"category": "A", "registry_rev": REV,
                         "family": "rest_floors",
                         "params": {"min_rest_home": {"value": 720,
                                                      "quote": "08:00"}}}
    assert etp.coverage_gap_problem(doc) is None
