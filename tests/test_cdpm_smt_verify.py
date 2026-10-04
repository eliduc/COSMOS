# -*- coding: utf-8 -*-
"""СМТ-1: проверка непротиворечивости свода через Z3 на подменной
модели-переводчике.

Охраняемое: конфликт двух правил даёт «unsat» с ядром в терминах
идентификаторов правил; совместные правила — «sat»; кривой SMT
получает ровно один повтор, затем честный отказ; чужие идентификаторы
в переводе отбрасываются."""
import json

import pytest

from cdpm import smt_verify as sv


def _doc():
    return {"rules": [
        {"id": "R-A", "kind": "HARD", "statement": "duty ≤ 12:00"},
        {"id": "R-B", "kind": "HARD", "statement": "duty ≥ 12:30"},
        {"id": "R-C", "kind": "SOFT", "statement": "minimize x"},
    ]}


def _reply(asserts, decls=None):
    return json.dumps({
        "decls": decls or ["(declare-fun duty_minutes () Int)"],
        "asserts": asserts}, ensure_ascii=False)


def test_conflict_yields_unsat_core_with_rule_ids():
    def llm(prompt):
        assert "R-A | duty" in prompt          # SOFT в промпт не идёт
        assert "R-C" not in prompt
        # 12.09.2026 (A01): чужой идентификатор больше не отбрасывается
        # молча — это ошибка формы с повтором и отказом (см.
        # test_cdpm_a01_smt_complete::test_foreign_id_is_named_retried_and_then_refused);
        # здесь предмет теста — ядро конфликта, ответ полный и точный.
        return _reply([
            {"rule": "R-A", "smt": "(<= duty_minutes 720)"},
            {"rule": "R-B", "smt": "(>= duty_minutes 750)"},
        ])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "unsat"
    # Ядро называет правила ТЕМИ ЖЕ идентификаторами, что свод и анкета
    # (11.09.2026): прежняя замена дефиса подчёркиванием давала «R_A»,
    # которого в своде нет, — и слова отказа выпуска пакета отсылали к
    # несуществующей строке.
    assert sorted(got["core"]) == ["R-A", "R-B"]
    assert got["n_translated"] == 2
    assert "КОНФЛИКТ" in got["status"]


def test_compatible_rules_yield_sat():
    def llm(prompt):
        return _reply([
            {"rule": "R-A", "smt": "(<= duty_minutes 720)"},
            {"rule": "R-B", "smt": "(>= duty_minutes 600)"},
        ])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "sat"
    assert got["core"] == []
    assert "непротиворечиво" in got["status"]
    assert any(t["rule"] == "R-A" for t in got["translations"])


def test_broken_smt_gets_one_retry_then_honest_failure():
    """Кривой SMT получает ровно один повтор. (12.09.2026, A01: ответы
    переводчика здесь полные — по обоим жёстким правилам; неполный ответ
    судится отдельно, см. test_cdpm_a01_smt_complete.)"""
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply([{"rule": "R-A", "smt": "(кривой"},
                           {"rule": "R-B", "smt": "(>= duty_minutes 600)"}])
        return _reply([{"rule": "R-A", "smt": "(<= duty_minutes 720)"},
                       {"rule": "R-B", "smt": "(>= duty_minutes 600)"}])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "sat"
    assert calls["n"] == 2

    def always_broken(prompt):
        return _reply([{"rule": "R-A", "smt": "(всегда кривой"},
                       {"rule": "R-B", "smt": "(>= duty_minutes 600)"}])
    with pytest.raises(sv.SmtVerifyError, match="не разобрался"):
        sv.verify(_doc(), always_broken)


def test_report_carries_model_fingerprint_and_translator():
    """Отчёт привязан к своду отпечатком канонической модели и
    называет переводчика: по отпечатку гейт выпуска пакета решает,
    свеж ли вердикт, а по переводчику манифест говорит, чья это
    работа."""
    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= duty_minutes 720)"},
                       {"rule": "R-B", "smt": "(>= duty_minutes 600)"}])
    canon = json.dumps(_doc(), ensure_ascii=False).encode("utf-8")
    sha = sv.model_fingerprint(canon)
    got = sv.verify(_doc(), llm, model_sha256=sha)
    assert got["model_sha256"] == sha and len(sha) == 64
    assert got["checked_at"] and got["n_rules"] == 2
    # У подменного канала отпечатка нет — имя модели не выдумывается.
    assert got["translator"] == {"role": "smt_translator",
                                 "provider": None, "model": None}


def test_report_freshness_is_judged_by_model_fingerprint(tmp_path):
    """Свежесть отчёта — равенство отпечатков, а не наличие файла:
    отчёт о прежнем своде и отчёт без отпечатка (сделан до 11.09.2026)
    свежими не считаются."""
    run = tmp_path / "run-0001"
    (run / "extract").mkdir(parents=True)
    assert sv.read_report(run) is None
    # 12.09.2026 (A01): свежий отчёт — ещё и в действующей форме
    # (`schema_version`); отчёт старой формы о полноте перевода не
    # говорит и пересчитывается.
    sv.write_report(run, {"verdict": "sat", "model_sha256": "a" * 64,
                          "schema_version": sv.REPORT_SCHEMA})
    assert sv.report_path(run).name == "smt-report.json"
    assert sv.read_report(run)["verdict"] == "sat"
    assert sv.is_fresh(sv.read_report(run), "a" * 64)
    assert not sv.is_fresh(sv.read_report(run), "b" * 64)
    assert not sv.is_fresh({"verdict": "sat", "model_sha256": "a" * 64},
                           "a" * 64)
    assert not sv.is_fresh({"verdict": "sat"}, "a" * 64)
    assert not sv.is_fresh(None, "a" * 64)


def test_no_hard_rules_report_counts_zero_rules():
    """Свод без жёстких правил: проверять нечего — счётчик правил
    нулевой (по нему пакет называет непротиворечивость видом «none»),
    модель не зовётся."""
    def never(prompt):                                    # pragma: no cover
        raise AssertionError("модель звать не за чем")
    got = sv.verify({"rules": [{"id": "R-S", "kind": "SOFT"}]}, never)
    assert got["n_rules"] == 0 and got["n_translated"] == 0
    assert got["verdict"] == "sat" and "нечего" in got["status"]
