# -*- coding: utf-8 -*-
"""Режим 2 — сверка формализации третьей стороны (пакет РМ2)."""
import json

from cdpm import mode2
from cdpm import model as m
from cdpm.gates import gate_mode2
from cdpm.sample import sample_model

_THIRD = """1.1. Отдых на базе перед сменой — не менее 14 часов.

2.4. Полёты в дни солнечных затмений запрещены внутренним стандартом
перевозчика.

3.1. Пейринг не длиннее четырёх «дней» подряд.
"""


def _entry(tmp_path):
    path = tmp_path / "THIRD.md"
    path.write_text(_THIRD, encoding="utf-8")
    return mode2.load_third_party(path)


def test_load_third_party_freezes_claims(tmp_path):
    entry = _entry(tmp_path)
    claims = mode2._claims_of(entry)
    assert len(claims) == 3
    assert all(c["anchor"] for c in claims)


def test_run_mode2_builds_part4_questions(tmp_path):
    svod = sample_model()
    entry = _entry(tmp_path)
    rid = svod["rules"][0]["id"]

    def llm(prompt):
        assert "СВОД" in prompt and rid in prompt
        return json.dumps({"checks": [
            {"anchor": mode2._claims_of(entry)[0]["anchor"],
             "verdict": "расходится", "rule": rid,
             "note": "у нас минимум 10 часов, у третьей стороны 14"},
            {"anchor": mode2._claims_of(entry)[1]["anchor"],
             "verdict": "в источниках отсутствует", "rule": rid,
             "note": "затмений в источниках нет"},
            {"anchor": mode2._claims_of(entry)[2]["anchor"],
             "verdict": "соответствует", "rule": rid},
        ]}, ensure_ascii=False)

    report, questions = mode2.run_mode2(svod, entry, llm)
    assert report["checked"] == 3 and report["skipped"] == []
    assert report["by_verdict"] == {"расходится": 1,
                                    "в источниках отсутствует": 1,
                                    "соответствует": 1}
    assert len(questions) == 2
    added = mode2.apply_questions(svod, questions)
    assert added == 2
    assert svod["meta"]["mode"] == 2
    assert m.validate_schema(svod) + m.validate_model(svod) == []
    q4 = [q for q in svod["questions"] if q["part"] == "4"]
    assert all(q["third_party_refs"][0]["file"] == "THIRD.md"
               for q in q4)
    # Повторное применение тех же вопросов — дублей нет.
    assert mode2.apply_questions(svod, questions) == 0


def test_run_mode2_fail_closed(tmp_path):
    svod = sample_model()
    entry = _entry(tmp_path)
    report, questions = mode2.run_mode2(
        svod, entry,
        lambda p: json.dumps({"checks": [
            {"anchor": mode2._claims_of(entry)[0]["anchor"],
             "verdict": "вроде верно"},
            {"anchor": mode2._claims_of(entry)[1]["anchor"],
             "verdict": "расходится", "rule": "R-НЕТ", "note": "…"},
            {"anchor": mode2._claims_of(entry)[2]["anchor"],
             "verdict": "расходится", "rule": svod["rules"][0]["id"],
             "note": ""},
        ]}, ensure_ascii=False))
    assert report["checked"] == 0
    reasons = " | ".join(s["reason"] for s in report["skipped"])
    assert "вне перечня" in reasons
    assert "вне свода" in reasons
    assert "без сути" in reasons
    assert questions == []
    # Молчание канала — честное «не сверено» всем чанком.
    report, _ = mode2.run_mode2(svod, entry, lambda p: "не могу")
    assert len(report["skipped"]) == 3


def test_report_md_and_gate(tmp_path):
    svod = sample_model()
    entry = _entry(tmp_path)
    rid = svod["rules"][0]["id"]
    report, questions = mode2.run_mode2(
        svod, entry,
        lambda p: json.dumps({"checks": [
            {"anchor": c["anchor"], "verdict": "расходится",
             "rule": rid, "note": "числа расходятся"}
            for c in mode2._claims_of(entry)]}, ensure_ascii=False))
    mode2.apply_questions(svod, questions)
    model_path = tmp_path / "model.json"
    report_path = tmp_path / "third-party-report.json"
    md_path = tmp_path / "third-party-report.md"
    m.dump_model(svod, model_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False),
                           encoding="utf-8")
    md_path.write_text(mode2.render_report_md(report),
                       encoding="utf-8")
    assert gate_mode2.run(str(model_path), str(report_path),
                          str(md_path)) == 0
    # Порча md мимо модели — красный.
    md_path.write_text(md_path.read_text(encoding="utf-8")
                       .replace("расходится", "сходится", 1),
                       encoding="utf-8")
    problems, _ = gate_mode2.check(model_path, report_path, md_path)
    assert any("мимо модели" in p for p in problems)


def test_mode2_checkpoint_called(tmp_path):
    """ПС-1: контрольная точка зовётся перед каждым чанком сверки."""
    svod = sample_model()
    entry = _entry(tmp_path)
    calls = []
    mode2.run_mode2(svod, entry, lambda p: "мимо",
                    control=lambda: calls.append(1))
    assert calls == [1]
