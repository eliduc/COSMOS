# -*- coding: utf-8 -*-
"""Сверка спорного моделью (линия данных, ДЛ-7)."""
import copy
import json

import pytest

from cdpm import data_report as dr
from cdpm import data_review
from cdpm.gates import gate_data_review
from cdpm.sample import sample_model


def _report_and_svod():
    """Образец «Полярис» + свод; в отчёте одна rule-находка (F-0001)
    и одна находка встроенной проверки (F-0002)."""
    return dr.sample_data_report(), sample_model()


def _reply(reviews):
    return json.dumps({"reviews": reviews}, ensure_ascii=False)


def test_select_findings_only_rule_based_unblocked():
    report, svod = _report_and_svod()
    got = data_review.select_findings(report, svod)
    assert [f["id"] for f in got] == ["F-0001"]   # F-0002 — встроенная
    # Блокировка строки-основания выводит находку из сверки: её уже
    # решает вопрос анкеты.
    blocked = copy.deepcopy(svod)
    for r in blocked["rules"]:
        if r["id"] == "R-S2-DATA-QUAL":
            r["blockers"] = ["Q-X"]
    assert data_review.select_findings(report, blocked) == []
    # Уже сверенная находка второй раз не сверяется.
    reviewed = copy.deepcopy(report)
    reviewed["findings"][0]["review"] = {
        "verdict": "законное исключение", "basis": "основание"}
    assert data_review.select_findings(reviewed, svod) == []
    # Сужение до перечня проверок.
    assert data_review.select_findings(
        report, svod, check_ids=["C-нет-такой"]) == []


def test_run_review_applies_verdicts_and_counters():
    report, svod = _report_and_svod()

    def llm(prompt):
        assert "F-0001" in prompt
        assert "правило R-S2-DATA-QUAL" in prompt
        return _reply([{"finding": "F-0001",
                        "verdict": "похоже на настоящее нарушение",
                        "basis": "допуск истёк до даты рейса"}])

    summary = data_review.run_review(svod, report, llm)
    assert summary["candidates"] == 1
    assert summary["reviewed"] == 1
    assert summary["reviewed_total"] == 1
    assert summary["by_verdict"] == {
        "похоже на настоящее нарушение": 1}
    assert summary["skipped"] == []
    assert report["findings"][0]["review"]["verdict"] \
        == "похоже на настоящее нарушение"
    assert report["counters"]["review_by_verdict"] == {
        "похоже на настоящее нарушение": 1}
    assert dr.validate_report(report, svod) == []


def test_run_review_rejects_bad_verdicts_fail_closed():
    """Вердикт вне перечня и вердикт без обоснования отбраковываются;
    находка честно остаётся несверённой с причиной."""
    report, svod = _report_and_svod()
    summary = data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-0001",
                           "verdict": "скорее всего нормально",
                           "basis": "хочется верить"}]))
    assert summary["reviewed"] == 0
    assert "вне перечня" in summary["skipped"][0]["reason"]
    assert "review" not in report["findings"][0]

    report, svod = _report_and_svod()
    summary = data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-0001",
                           "verdict": "законное исключение",
                           "basis": "  "}]))
    assert "без обоснования" in summary["skipped"][0]["reason"]


def test_run_review_unparsable_chunk_and_alien_answers():
    """Канал без разборного JSON дважды — чанк честно «не сверено»;
    ответ о чужой находке отбраковывается счётчиком."""
    report, svod = _report_and_svod()
    summary = data_review.run_review(svod, report,
                                     lambda p: "план готов, сверяю")
    assert summary["reviewed"] == 0
    assert "не дал разборного JSON" in summary["skipped"][0]["reason"]

    report, svod = _report_and_svod()
    summary = data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-9999",
                           "verdict": "законное исключение",
                           "basis": "чужое"}]))
    assert summary["alien_answers"] == 1
    assert "не дал ответа" in summary["skipped"][0]["reason"]


def test_render_adds_review_columns_only_when_reviewed():
    """Обратная совместимость: несверённый отчёт рендерится без колонок
    сверки (байт-в-байт со старыми артефактами), сверенный — с ними."""
    report, svod = _report_and_svod()
    plain = dr.render_data_report(report)
    assert "Вердикт сверки" not in plain
    assert "review_by_verdict" not in report["counters"]
    data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-0001",
                           "verdict": "неясно — к владельцу",
                           "basis": "материалов мало"}]))
    md = dr.render_data_report(report)
    assert "Вердикт сверки" in md
    assert "неясно — к владельцу" in md
    assert "- сверка спорного: неясно — к владельцу 1" in md
    # Несверённая находка встроенной проверки — прочерк в колонке.
    assert "| — |" in md


def test_gate_data_review_green_and_red(tmp_path):
    report, svod = _report_and_svod()
    summary = data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-0001",
                           "verdict": "законное исключение",
                           "basis": "проверка мерила не тот период"}]))
    report_path = tmp_path / "data-report.json"
    md_path = tmp_path / "data-report.md"
    summary_path = tmp_path / "review-summary.json"
    svod_path = tmp_path / "model.json"
    dr.dump_report(report, report_path)
    md_path.write_text(dr.render_data_report(report), encoding="utf-8")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    svod_path.write_text(json.dumps(svod, ensure_ascii=False),
                         encoding="utf-8")
    problems, counters = gate_data_review.check(
        report_path, summary_path, svod_path, md_path)
    assert problems == []
    assert counters["reviewed"] == 1
    assert counters["by_verdict"] == {"законное исключение": 1}
    assert gate_data_review.run(str(report_path), str(summary_path),
                                str(svod_path), str(md_path)) == 0

    # Порча вердикта в отчёте мимо модели — красный по md-сверке.
    tampered = md_path.read_text(encoding="utf-8").replace(
        "законное исключение", "всё в порядке")
    md_path.write_text(tampered, encoding="utf-8")
    problems, _ = gate_data_review.check(report_path, summary_path,
                                         svod_path, md_path)
    assert any("мимо модели" in p for p in problems)

    # Сводка, разъехавшаяся с отчётом, — красный.
    summary_path.write_text(json.dumps(
        {**summary, "by_verdict": {"неясно — к владельцу": 5}},
        ensure_ascii=False), encoding="utf-8")
    md_path.write_text(dr.render_data_report(report), encoding="utf-8")
    problems, _ = gate_data_review.check(report_path, summary_path,
                                         svod_path, md_path)
    assert any("не сходится с пересчётом" in p for p in problems)


def test_gate_selftest_runs():
    gate_data_review._selftest()


def test_review_checkpoint_called():
    """ПС-1: контрольная точка зовётся перед каждым чанком сверки."""
    report, svod = _report_and_svod()
    calls = []
    data_review.run_review(
        svod, report,
        lambda p: _reply([{"finding": "F-0001",
                           "verdict": "неясно — к владельцу",
                           "basis": "мало материалов"}]),
        control=lambda: calls.append(1))
    assert calls == [1]


def test_cli_roundtrip(tmp_path, monkeypatch):
    """Командная строка: сверка образца подменной моделью, гейт
    зелёный на выходных артефактах."""
    report, svod = _report_and_svod()
    report_path = tmp_path / "in-report.json"
    svod_path = tmp_path / "model.json"
    dr.dump_report(report, report_path)
    svod_path.write_text(json.dumps(svod, ensure_ascii=False),
                         encoding="utf-8")
    monkeypatch.setattr(
        data_review.llm_mod, "cosmos_llm",
        lambda **kw: lambda p: _reply(
            [{"finding": "F-0001",
              "verdict": "похоже на настоящее нарушение",
              "basis": "прямое чтение строки"}]))
    out = tmp_path / "out"
    rc = data_review.main([str(svod_path), str(report_path),
                           "--out-dir", str(out)])
    assert rc == 0
    assert gate_data_review.run(
        str(out / "data-report.json"),
        str(out / "review-summary.json"),
        str(svod_path), str(out / "data-report.md")) == 0


def test_metrics_backward_compatible_for_existing_reports():
    """Отчёты до ДЛ-7 (без review) сохраняют счётчики без нового
    ключа — старые артефакты остаются зелёными для своих гейтов."""
    report, _ = _report_and_svod()
    m = dr.metrics_of(report)
    assert "review_by_verdict" not in m
    assert report["counters"] == m
