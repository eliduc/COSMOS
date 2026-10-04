# -*- coding: utf-8 -*-
"""A01 (аудит «четыре принципа ВМП», CONS-01, fcec7a2e): неполный
SMT-перевод не должен проходить выпуск.

Ожидания заданы независимо от реализации — по контракту П0 §1.1
(`P0-CONTRACTS-v1-20260911.md`) и контрпримеру аудитора
(`scratchpad/cdpm-audit-20260911/consistency/probe_consistency.py`,
сценарии partial / missing_smt), с ИНВЕРСИЕЙ дефектного поведения:
раньше перевод одного правила из двух давал «sat» и пакет выпускался.

Охраняемое:
- ответ переводчика обязан назвать КАЖДОЕ жёсткое правило: либо перевод
  с непустым S-выражением, либо явный статус «untranslatable» с
  причиной; `smt: "true"` и отсутствующее поле `smt` — ошибка формы,
  а не «правило переведено»;
- на ошибку формы — ровно один повтор с перечнем недостающих; после
  него отчёт честно неполон: вердикта нет («none»), статус называет
  непереведённые правила;
- рубеж выпуска на неполном отчёте отказывает кодом `smt-incomplete`
  с перечнем правил, а не выпускает пакет;
- гейт производителя красит «sat» с числом переводов меньше числа
  правил;
- отчёт старой формы (без `schema_version`) свежим не считается.

Вердикт независимого аудитора VERDICT-A01-ed53ef3a (12.09.2026):
- A01-F1 (P1): рубеж судит ВЫПУСКАЕМЫЙ свод — модель после
  преобразований сборки (правило, ставшее жёстким по каталогу), а не
  канонический свод кнопки; при изменении предмета проверки — пересчёт
  отчётом пакета либо честный отказ, никогда не выпуск на старых
  счётчиках «sat 1/1» / «none 0/0»; отчёт едет с пакетом, и гейт
  производителя сверяет его строки с жёсткими правилами model.json;
- A01-F2 (P2): записи вне формы в ответе переводчика (null, число,
  строка, список, bool в `asserts`; не строка в `decls`) — ошибка
  формы с одним общим повтором, затем отчёт без вердикта с позицией и
  видом, отказ выпуска `smt-incomplete` и красный гейт.
Контролируемый переводчик — подменная функция (ограничение: сама
верность перевода здесь не судится, только полнота)."""
import hashlib
import json
import pathlib
import re

import pytest

from cdpm import export_task_package as tp
from cdpm import smt_verify as sv
from cdpm.gates import gate_task_package


def _doc():
    return {"rules": [
        {"id": "R-A", "kind": "HARD", "statement": "x <= 1"},
        {"id": "R-B", "kind": "HARD", "statement": "x <= 5"},
        {"id": "R-S", "kind": "SOFT", "statement": "minimize x"},
    ]}


def _reply(asserts, decls=None):
    return json.dumps({"decls": decls or ["(declare-fun x () Int)"],
                       "asserts": asserts}, ensure_ascii=False)


def _canon(doc):
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def test_partial_translation_is_incomplete_not_sat():
    """Контрпример аудитора «partial»: переведено одно правило из двух.
    Раньше — «sat»; теперь — отчёт без вердикта, называющий R-B."""
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        if calls["n"] == 2:
            # повтор обязан перечислить недостающие правила
            assert "R-B" in prompt
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"}])
    got = sv.verify(_doc(), llm)
    assert calls["n"] == 2                     # ровно один повтор
    assert got["schema_version"] == sv.REPORT_SCHEMA == "smt-report/2"
    assert got["verdict"] == "none"
    assert got["n_rules"] == 2 and got["n_translated"] == 1
    assert got["n_untranslatable"] == 0
    assert got["untranslated"] == ["R-B"]
    assert "неполон" in got["status"] and "R-B" in got["status"]
    assert [t["rule"] for t in got["translations"]] == ["R-A", "R-B"]
    assert got["translations"][0]["status"] == "translated"
    # статус «нет записи» — с причиной словами (закон fail-closed словами)
    assert got["translations"][1]["status"] == "missing"
    assert "нет записи" in got["translations"][1]["note"]


def test_missing_smt_body_is_a_form_error_with_one_retry():
    """Контрпример «missing_smt»: утверждение без поля `smt` раньше
    молча становилось `true`. Теперь это ошибка формы: один повтор, и
    если повтор полон — вердикт; если нет — честная неполнота."""
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply([{"rule": "R-A"}, {"rule": "R-B"}])
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"}])
    got = sv.verify(_doc(), llm)
    assert calls["n"] == 2 and got["verdict"] == "sat"
    assert got["n_translated"] == 2 and got["untranslated"] == []

    def always_missing(prompt):
        return _reply([{"rule": "R-A"}, {"rule": "R-B"}])
    got = sv.verify(_doc(), always_missing)
    assert got["verdict"] == "none" and got["n_translated"] == 0
    assert got["untranslated"] == ["R-A", "R-B"]
    assert "true" not in (got.get("script") or "")


def test_smt_true_is_not_a_translation():
    """`smt: "true"` (прежний способ сказать «не переводится») больше
    не считается переводом: правило остаётся непереведённым."""
    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "true"}])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "none"
    assert got["n_translated"] == 1 and got["untranslated"] == ["R-B"]


def test_explicit_untranslatable_is_counted_separately_and_blocks_verdict():
    """Честное «не переводится в логику» — явный статус с причиной; оно
    не считается переводом и вердикт не выносится."""
    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "status": "untranslatable",
                        "note": "описание процесса, не ограничение"}])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "none"
    assert got["n_translated"] == 1 and got["n_untranslatable"] == 1
    assert got["untranslated"] == ["R-B"]
    row = next(t for t in got["translations"] if t["rule"] == "R-B")
    assert row["status"] == "untranslatable" and "процесса" in row["note"]
    assert "smt" not in row


def test_complete_translation_still_yields_a_verdict():
    """Положительный контроль: полный перевод даёт вердикт как раньше,
    отчёт несёт статус каждого правила."""
    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"}])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "sat" and got["untranslated"] == []
    assert all(t["status"] == "translated" for t in got["translations"])
    assert got["n_translated"] == got["n_rules"] == 2


def test_release_refuses_incomplete_report_with_code_and_rule_list(tmp_path):
    """Рубеж выпуска: свежий, но неполный отчёт — отказ `smt-incomplete`
    с перечнем непереведённых правил (раньше «sat» одного правила из
    двух выпускал пакет)."""
    doc = _doc()
    canon = _canon(doc)
    run = tmp_path / "run-0001"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)

    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"}])
    report = sv.verify(doc, llm, model_sha256=sv.model_fingerprint(canon))
    sv.write_report(run, report)
    assert sv.is_fresh(sv.read_report(run), sv.model_fingerprint(canon))
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, canon)
    assert err.value.code == tp.SMT_INCOMPLETE == "smt-incomplete"
    assert "R-B" in str(err.value)
    assert err.value.items == ["R-B"]


def test_release_accepts_complete_report_unchanged(tmp_path):
    """Положительный контроль рубежа: полный «sat» выпускается, а раздел
    манифеста сохраняет прежние ключи (контракт с COSMOS)."""
    doc = _doc()
    canon = _canon(doc)
    run = tmp_path / "run-0002"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)

    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"}])
    sv.write_report(run, sv.verify(doc, llm,
                                   model_sha256=sv.model_fingerprint(canon)))
    cons = tp.consistency_of(run, canon)
    # 12.09.2026 (A01-F1): раздел называет предмет проверки — отпечаток
    # жёстких правил выпускаемого свода и слово «canonical» (предмет
    # равен каноническому, вердикт — отчётом кнопки).
    assert set(cons) == {"verdict", "model_sha256", "n_rules",
                         "n_translated", "translator", "report",
                         "subject_sha256", "subject"}
    assert cons["verdict"] == "sat" and cons["n_translated"] == 2
    assert cons["subject"] == "canonical" and cons["report"] == sv.REPORT_REL
    assert cons["subject_sha256"] == sv.subject_fingerprint(doc)


def test_gate_reds_sat_with_fewer_translations_than_rules():
    """Гейт производителя: «sat» при переведённых утверждениях меньше
    числа жёстких правил — подделка справки или обход рубежа.
    (12.09.2026, A01-F1: гейт судит ключ вместе с отчётом из пакета и
    model.json пакета — форма ключа несёт предмет проверки.)"""
    doc = _doc()
    report = {"schema_version": sv.REPORT_SCHEMA, "verdict": "sat",
              "model_sha256": "a" * 64,
              "subject_sha256": sv.subject_fingerprint(doc),
              "translations": [
                  {"rule": "R-A", "status": "translated", "smt": "(<= x 1)"},
                  {"rule": "R-B", "status": "translated", "smt": "(<= x 5)"}]}
    files = {"smt-report.json": json.dumps(report).encode("utf-8")}
    cons = {"verdict": "sat", "model_sha256": "a" * 64, "n_rules": 2,
            "n_translated": 1,
            "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None,
                           "model": None},
            "report": sv.REPORT_REL,
            "subject_sha256": sv.subject_fingerprint(doc),
            "subject": "canonical"}
    problems = gate_task_package._check_consistency(
        {"consistency": cons}, {}, files, doc)
    assert problems and any("1" in p and "2" in p for p in problems)
    cons["n_translated"] = 2
    assert gate_task_package._check_consistency(
        {"consistency": cons}, {}, files, doc) == []


def test_contradictory_duplicate_is_not_a_translation():
    """Сценарий «duplicate» аудитора: две записи для одного правила.
    Какая верна — неизвестно: после повтора правило считается
    непереведённым с причиной, отчёт неполон, а не «первая из двух»."""
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-A", "smt": "(<= x 2)"},
                       {"rule": "R-B", "smt": "(<= x 5)"}])
    got = sv.verify(_doc(), llm)
    assert calls["n"] == 2
    assert got["verdict"] == "none" and got["untranslated"] == ["R-A"]
    assert got["n_translated"] == 1 and got["n_rules"] == 2
    row = next(t for t in got["translations"] if t["rule"] == "R-A")
    assert row["status"] == "missing" and "повторная" in row["note"]
    # отчёт без вердикта всегда называет непереведённое (инвариант формы)
    assert got["n_translated"] < got["n_rules"]


def test_foreign_id_is_named_retried_and_then_refused():
    """Лишний идентификатор — нарушение формы: один повтор с его именем;
    если он сохраняется, точного набора нет — отчёт без вердикта, лишнее
    названо в `dropped` (REPLY-AUDITOR-01/A01: без молчаливого
    отбрасывания), выпуск отказывает `smt-incomplete`."""
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        if calls["n"] == 2:
            assert "ЧУЖОЙ" in prompt
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"},
                       {"rule": "ЧУЖОЙ", "smt": "false"}])
    got = sv.verify(_doc(), llm)
    assert calls["n"] == 2
    assert got["verdict"] == "none" and got["dropped"] == ["ЧУЖОЙ"]
    assert got["untranslated"] == [] and "ЧУЖОЙ" in got["status"]

    def llm_fixed(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"}])
    assert sv.verify(_doc(), llm_fixed)["verdict"] == "sat"


def test_release_refuses_report_with_a_surviving_foreign_id(tmp_path):
    """Реальный отчёт `verify()` с сохранившимся чужим идентификатором
    (`dropped`) на рубеже выпуска — отказ `smt-incomplete` с именем
    чужого правила в `items`, а не «вердикта нет» без перечня
    (замечание З1 третьего ревью)."""
    doc = _doc()
    canon = _canon(doc)
    run = tmp_path / "run-0005"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)

    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "(<= x 5)"},
                       {"rule": "ЧУЖОЙ", "smt": "(<= x 9)"}])
    report = sv.verify(doc, llm, model_sha256=sv.model_fingerprint(canon))
    assert report["verdict"] == "none" and report["dropped"] == ["ЧУЖОЙ"]
    sv.write_report(run, report)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, canon)
    assert err.value.code == "smt-incomplete"
    assert "ЧУЖОЙ" in err.value.items and "ЧУЖОЙ" in str(err.value)


def test_false_is_a_translation_that_yields_a_conflict():
    """Явный `false` — законный перевод невыполнимого правила: результат
    решателя «unsat» с ядром из этого правила (REPLY-AUDITOR-01/02,
    П-01), а не неполнота."""
    def llm(prompt):
        return _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                       {"rule": "R-B", "smt": "false"}])
    got = sv.verify(_doc(), llm)
    assert got["verdict"] == "unsat" and got["core"] == ["R-B"]
    assert got["n_translated"] == 2 and got["untranslated"] == []


def test_release_checks_the_rule_set_against_the_exported_svod(tmp_path):
    """Рубеж не верит счётчикам: отчёт с равными счётчиками, но чужим,
    пропущенным или повторным правилом относительно выпускаемого свода
    — отказ `smt-incomplete` с именами (REPLY-AUDITOR-01/A01)."""
    doc = _doc()
    canon = _canon(doc)
    run = tmp_path / "run-0004"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)

    def report(rows):
        return {"schema_version": "smt-report/2", "status": "sat",
                "verdict": "sat", "core": [], "n_rules": 2, "n_translated": 2,
                "n_untranslatable": 0, "untranslated": [], "dropped": [],
                # 12.09.2026 (A01-F1): отчёт называет СВОЙ предмет.
                "subject_sha256": sv.subject_fingerprint(doc),
                "translations": rows, "model_sha256": sv.model_fingerprint(canon),
                "checked_at": "2026-09-12T00:00:00+02:00",
                "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None,
                               "model": None}}
    ok = [{"rule": "R-A", "status": "translated", "smt": "(<= x 1)"},
          {"rule": "R-B", "status": "translated", "smt": "(<= x 5)"}]
    sv.write_report(run, report(ok))
    assert tp.consistency_of(run, canon)["verdict"] == "sat"
    cases = {
        "чужое вместо пропущенного": [ok[0], {"rule": "R-X", "status": "translated", "smt": "(<= x 9)"}],
        "повтор вместо пропущенного": [ok[0], ok[0]],
        "повтор при полном наборе": ok + [ok[0]],
        "тело true": [ok[0], {"rule": "R-B", "status": "translated", "smt": "true"}],
        "лишнее при полном наборе": ok + [{"rule": "R-X", "status": "translated", "smt": "(<= x 9)"}],
    }
    for name, rows in cases.items():
        sv.write_report(run, report(rows))
        with pytest.raises(tp.TaskPackageError) as err:
            tp.consistency_of(run, canon)
        assert err.value.code == "smt-incomplete", name
        assert err.value.items, name
        if "лишнее" in name or "чужое" in name:
            assert "R-X" in err.value.items, name
        if "пропущенного" in name or "true" in name:
            assert "R-B" in err.value.items, name
        if name == "повтор при полном наборе":
            assert "R-A" in err.value.items, name      # дубль при полном наборе


def test_release_judges_a_hand_written_report_by_contract_form(tmp_path):
    """Рубеж судит отчёт по форме контракта, а не по тому, кто его
    написал: рукописный отчёт `smt-report/2` с одним непереведённым
    правилом — отказ `smt-incomplete` с его именем; такой же полный —
    выпуск."""
    doc = _doc()
    canon = _canon(doc)
    run = tmp_path / "run-0003"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)
    base = {"schema_version": "smt-report/2", "status": "перевод неполон",
            "verdict": "none", "core": [], "n_rules": 2, "n_translated": 1,
            "n_untranslatable": 1, "untranslated": ["R-B"], "dropped": [],
            # 12.09.2026 (A01-F1): отчёт называет СВОЙ предмет.
            "subject_sha256": sv.subject_fingerprint(doc),
            "translations": [
                {"rule": "R-A", "status": "translated", "smt": "(<= x 1)"},
                {"rule": "R-B", "status": "untranslatable",
                 "note": "описание процесса"}],
            "model_sha256": sv.model_fingerprint(canon),
            "checked_at": "2026-09-12T00:00:00+02:00",
            "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None,
                           "model": None}}
    sv.write_report(run, base)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, canon)
    assert err.value.code == "smt-incomplete" and err.value.items == ["R-B"]
    full = dict(base, status="непротиворечиво", verdict="sat",
                n_translated=2, n_untranslatable=0, untranslated=[],
                translations=[
                    {"rule": "R-A", "status": "translated", "smt": "(<= x 1)"},
                    {"rule": "R-B", "status": "translated", "smt": "(<= x 5)"}])
    sv.write_report(run, full)
    assert tp.consistency_of(run, canon)["verdict"] == "sat"


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_real_package_build_refuses_report_about_other_rules(tmp_path):
    """Реальный путь выпуска (`build_package` — сборка пакета целиком, а
    не одна функция рубежа): свежий отчёт с равными счётчиками, но о
    ЧУЖОМ правиле вместо жёсткого правила выпускаемого свода — отказ
    `smt-incomplete`, называющий и пропущенное, и лишнее правило
    (GPT-090: связь отчёта с фактически выпускаемой ВМП)."""
    run = _plant(tmp_path, _duration_doc())
    canon = (run / "extract" / "model.json").read_bytes()
    hard = [r["id"] for r in sv._hard_rules(json.loads(canon))]
    assert hard == ["R-H"]
    sv.write_report(run, {
        "schema_version": sv.REPORT_SCHEMA, "status": "sat", "verdict": "sat",
        "core": [], "n_rules": 1, "n_translated": 1, "n_untranslatable": 0,
        "untranslated": [], "dropped": [],
        # 12.09.2026 (A01-F1): отчёт называет СВОЙ предмет — предмет
        # выпускаемого свода; строки перевода при этом о чужом правиле.
        "subject_sha256": sv.subject_fingerprint(json.loads(canon)),
        "translations": [{"rule": "R-X", "status": "translated",
                          "smt": _DUTY_SMT}],
        "model_sha256": sv.model_fingerprint(canon),
        "checked_at": "2026-09-12T00:00:00+02:00",
        "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None,
                       "model": None}})
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-incomplete"
    assert set(err.value.items) == {"R-H", "R-X"}
    # тот же отчёт о правильном правиле — пакет собирается
    sv.write_report(run, {
        "schema_version": sv.REPORT_SCHEMA, "status": "sat", "verdict": "sat",
        "core": [], "n_rules": 1, "n_translated": 1, "n_untranslatable": 0,
        "untranslated": [], "dropped": [],
        "subject_sha256": sv.subject_fingerprint(json.loads(canon)),
        "translations": [{"rule": "R-H", "status": "translated",
                          "smt": _DUTY_SMT}],
        "model_sha256": sv.model_fingerprint(canon),
        "checked_at": "2026-09-12T00:00:00+02:00",
        "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None,
                       "model": None}})
    files = tp.build_package(run)
    assert json.loads(files["manifest.json"])["consistency"]["n_translated"] == 1


def test_empty_svod_report_is_fresh_in_new_form():
    """Свод без жёстких правил: отчёт несёт версию схемы и свеж."""
    def never(prompt):                                    # pragma: no cover
        raise AssertionError("модель звать не за чем")
    got = sv.verify({"rules": [{"id": "R-S", "kind": "SOFT"}]}, never,
                    model_sha256="a" * 64)
    assert got["schema_version"] == sv.REPORT_SCHEMA
    assert got["untranslated"] == [] and got["n_untranslatable"] == 0
    assert sv.is_fresh(got, "a" * 64)


def test_old_report_without_schema_version_is_not_fresh(tmp_path):
    """Отчёт старой формы (до smt-report/2) свежим не считается: о
    полноте перевода он ничего не говорит, пересчёт обязателен."""
    old = {"verdict": "sat", "model_sha256": "a" * 64, "n_rules": 2,
           "n_translated": 2}
    assert not sv.is_fresh(old, "a" * 64)
    new = dict(old, schema_version=sv.REPORT_SCHEMA, untranslated=[],
               n_untranslatable=0,
               translations=[{"rule": "R-A", "status": "translated",
                              "smt": "(<= x 1)"},
                             {"rule": "R-B", "status": "translated",
                              "smt": "(<= x 5)"}])
    assert sv.is_fresh(new, "a" * 64)
    assert not sv.is_fresh(new, "b" * 64)


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_report_that_does_not_name_its_subject_is_not_fresh_at_release(
        tmp_path, monkeypatch):
    """A01-F1: отчёт нынешней версии схемы, но БЕЗ отпечатка предмета
    (форма первой сдачи A01), вердиктом о выпускаемом наборе правил не
    является — рубеж проверяет свод сам и записывает отчёт с
    предметом. Иначе в пакет уехала бы копия отчёта, чьи строки
    привязаны к именам правил, но не к их формулировкам."""
    run = _plant(tmp_path, _duration_doc())
    canon = (run / "extract" / "model.json").read_bytes()
    subjectless = sv.verify(_duration_doc(), _duration_translator([]),
                            model_sha256=sv.model_fingerprint(canon))
    subjectless.pop("subject_sha256")
    assert subjectless["verdict"] == "sat"
    assert not sv.is_fresh(subjectless, sv.model_fingerprint(canon),
                           sv.subject_fingerprint(_duration_doc()))
    sv.write_report(run, subjectless)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator(calls))
    files = tp.build_package(run)
    assert len(calls) == 1                       # пересчёт, а не доверие
    shipped = json.loads(files["smt-report.json"])
    cons = json.loads(files["manifest.json"])["consistency"]
    assert shipped["subject_sha256"] == cons["subject_sha256"]
    assert gate_task_package.check(_rezip(files, tmp_path, "subjectless.zip")
                                   )[0] == []


# ── A01-F1 (вердикт аудитора 12.09.2026, P1): рубеж судит ВЫПУСКАЕМЫЙ свод ──
# Контрпример аудитора (`test_final_export_does_not_add_untranslated_hard_rule`):
# правило с устаревшим ярлыком SOFT посажено на семью duty_limits настоящего
# каталога — сборка пакета (`derive_all_rule_traits`) делает его жёстким, и
# прежний рубеж проверял канонический свод БЕЗ него: пакет уходил со
# справкой «sat 1/1» (или «none 0/0») и зелёным гейтом.

HARD_KINDS = ("HARD", "DATA", "DEF")


@pytest.fixture
def a01_modern_promotion_catalog(tmp_path, monkeypatch):
    """Real isolated unit vocabulary, not a release or support approval.

    Only the explicitly marked modern-input controls use this fixture. The package
    gates remain real, including their independent support requirements.
    """
    from cdpm import catalog_bridge
    from test_k03_unit_vocabulary import selected

    catalog_bridge.forget()
    try:
        yield selected(tmp_path, monkeypatch)
    finally:
        catalog_bridge.forget()


def _promotion_doc(with_existing_hard: bool, promoted: bool = True) -> dict:
    """Свод контрпримера аудитора: R-PROMOTED (x >= 2) с ярлыком SOFT на
    семье duty_limits; R-A (x <= 1) — уже жёсткое. Вместе они невыполнимы:
    полный перевод выпускаемого свода даёт настоящий конфликт."""
    from cdpm import catalog_bridge, family_contract
    stamp = catalog_bridge.stamp()

    def fit(rid):
        # This is newly authored synthetic fixture data, not migration of a
        # saved model. Keep the original family/value/quote and SMT statements;
        # only make their existing landing explicit under the current contract.
        return {"schema_version": "landing/2", "category": "A",
                "registry_rev": stamp["fingerprint"],
                "family": "duty_limits",
                "family_contract": family_contract.snapshot(
                    "duty_limits", ["max_duty_duration"]),
                "params": {"max_duty_duration": {"value": 720,
                    "unit": "duration_minutes", "relation": "max",
                    "condition": None, "quote": "720 минут",
                    "source": {"kind": "rule", "rule_id": rid}}}}
    rows = []
    if with_existing_hard:
        rows.append({"id": "R-A", "kind": "HARD", "zone": "PAIRING",
                     "title": "Верхняя граница",
                     "statement": "x <= 1, 720 минут",
                     "sources": [{"sid": "S1", "anchor": "1"}],
                     "template_fit": fit("R-A")})
    rows.append({"id": "R-PROMOTED", "kind": "SOFT" if promoted else "HARD",
                 "zone": "PAIRING", "title": "Вторая граница",
                 "statement": "x >= 2, 720 минут",
                 "sources": [{"sid": "S1", "anchor": "2"}],
                 "template_fit": fit("R-PROMOTED")})
    return {"meta": {"customer": "SYNTHETIC-A01", "mode": 1, "revision": 1,
                     "cdpm_version": "0.1", "fit_contract": True},
            "sources": [{"sid": "S1", "file": "synthetic.md",
                         "sha256": "0" * 64}],
            "rules": rows, "questions": [], "objectives": []}


_DUTY_STATEMENT = "Длительность рабочей смены не более 720 минут."
_DUTY_SMT = "(<= duty_duration_minutes 720)"


def _duration_doc():
    """Concrete replacement of the old undefined x fixture, not its migration.

    A single HARD/PAIRING rule explicitly means a 720-minute duty upper bound.
    The already tested constructor supplies the same real family pin and cell;
    the source identity is changed together with the rule identity. No SOFT or
    objective rule is needed by these eight consistency/report lifecycle tests.
    """
    doc = _promotion_doc(with_existing_hard=False, promoted=False)
    rule = doc["rules"][0]
    rule.update(id="R-H", title="Предел длительности смены",
                statement=_DUTY_STATEMENT)
    rule["template_fit"]["params"]["max_duty_duration"]["source"] = {
        "kind": "rule", "rule_id": "R-H"}
    return doc


def _duration_translator(calls):
    """Exact deterministic translation of the concrete duration fixture only."""
    def fn(prompt):
        calls.append(prompt)
        assert re.findall(r"(?m)^(R-[A-Z]+) \| ", prompt) == ["R-H"]
        assert f"R-H | {_DUTY_STATEMENT}" in prompt
        return _reply([{"rule": "R-H", "smt": _DUTY_SMT}],
                      decls=["(declare-fun duty_duration_minutes () Int)"])
    return fn


def _plant(tmp_path, doc, name="run-0100"):
    run = tmp_path / name
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(_canon(doc))
    return run


def _arith_translator(calls):
    """Переводчик по заданию рубежа: R-A → x <= 1, прочие → x >= 2."""
    def fn(prompt):
        calls.append(prompt)
        ids = re.findall(r"(?m)^(R-[A-Z]+) \| ", prompt)
        return _reply([{"rule": rid,
                        "smt": "(<= x 1)" if rid == "R-A" else "(>= x 2)"}
                       for rid in ids])
    return fn


def _refusing_channel(run_dir):
    """Канал потомка повтора «из журнала»: запроса переводчика в журнале
    источника нет — канал не строится."""
    raise RuntimeError("запроса с таким отпечатком в журнале источника нет")


def _replay_pair(tmp_path, doc):
    """Источник и потомок повтора «из журнала» с одним сводом байт в байт."""
    source, child = tmp_path / "run-9001", tmp_path / "run-9002"
    for run in (source, child):
        (run / "extract").mkdir(parents=True)
        (run / "extract" / "model.json").write_bytes(_canon(doc))
    (child / "status.json").write_text(json.dumps(
        {"passport": {"replay": {"mode": "replay-journal",
                                 "source_run": source.name}}},
        ensure_ascii=False), encoding="utf-8")
    return source, child


def test_subject_of_is_exactly_what_the_translator_reads():
    """Предмет проверки — идентификаторы, виды и формулировки жёстких
    правил (то, что идёт в задание переводчику), в каноническом
    порядке; остальные поля правила и мягкие правила предмет не
    меняют."""
    doc = _doc()
    assert sv.subject_of(doc) == [
        {"id": "R-A", "kind": "HARD", "statement": "x <= 1"},
        {"id": "R-B", "kind": "HARD", "statement": "x <= 5"}]
    same = json.loads(json.dumps(doc))
    same["rules"][0]["note"] = "ярлыки кодом"
    same["rules"][0]["applicability"] = {"x": 1}
    same["rules"][2]["export_solver"] = {"family": "f"}
    same["rules"].reverse()
    assert sv.subject_fingerprint(same) == sv.subject_fingerprint(doc)
    promoted = json.loads(json.dumps(doc))
    promoted["rules"][2]["kind"] = "HARD"
    assert sv.subject_fingerprint(promoted) != sv.subject_fingerprint(doc)
    assert sv.subject_changes(doc, promoted) == [
        "R-S — стало жёстким при сборке (SOFT→HARD)"]
    reworded = json.loads(json.dumps(doc))
    reworded["rules"][1]["statement"] = "x <= 6"
    reworded["rules"][0]["kind"] = "DEF"
    assert sv.subject_changes(doc, reworded) == [
        "R-A — вид изменился при сборке (HARD→DEF)",
        "R-B — формулировка изменилась при сборке"]
    assert sv.subject_changes(promoted, doc) == [
        "R-S — перестало быть жёстким при сборке (HARD→SOFT)"]


def test_subject_of_a_broken_svod_is_words_not_a_crash():
    """Предмет проверки считается и на КРИВОМ своде (запись правила не
    объект, `rules` не список): такой свод незаконен и краснится схемой
    модели, но ни предмет, ни сверка отчёта не падают разбором — гейт
    производителя судит чужой пакет словами."""
    broken = {"rules": [{"id": "R-A", "kind": "HARD", "statement": "x <= 1"},
                        None, 17, ["R-B"]]}
    assert sv.subject_of(broken) == [
        {"id": "R-A", "kind": "HARD", "statement": "x <= 1"}]
    assert sv.report_completeness({"translations": []}, broken)["missing"] \
        == ["R-A"]
    assert sv.subject_of({"rules": "R-A"}) == []
    assert sv.subject_fingerprint({"rules": None}) \
        == sv.subject_fingerprint({"rules": []})


_NO_KEY = object()


@pytest.mark.parametrize("rid", [_NO_KEY, None, "", "   ", 17, ["R-A"]],
                         ids=["no_key", "null", "empty", "blank", "number",
                              "list"])
def test_hard_rule_without_an_identifier_is_words_not_a_crash(
        tmp_path, monkeypatch, rid):
    """Жёсткое правило БЕЗ пригодного идентификатора: перевести и
    назвать его нечем, а выдумывать имя запрещено (A01-F2). Проверка
    отвечает словами с позицией, рубеж выпуска — честным отказом
    `smt-unavailable`; молчаливого пропуска правила нет."""
    rule = {"kind": "HARD", "statement": "x <= 1"}
    if rid is not _NO_KEY:
        rule["id"] = rid
    doc = {"rules": [rule, {"id": "R-B", "kind": "HARD",
                            "statement": "x <= 5"}]}
    with pytest.raises(sv.SmtVerifyError) as err:
        sv.verify(doc, lambda p: _reply([]))
    assert "без идентификатора" in str(err.value) and "0" in str(err.value)
    run = _plant(tmp_path, doc, name="run-0200")
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: (lambda p: _reply([])))
    with pytest.raises(tp.TaskPackageError) as ferr:
        tp.consistency_of(run, _canon(doc))
    assert ferr.value.code == "smt-unavailable"
    assert "без идентификатора" in str(ferr.value)


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_promoted_rule_is_checked_in_released_svod_real_conflict(
        tmp_path, monkeypatch):
    """Контрпример аудитора «one_to_two_hard»: R-A жёсткое, R-PROMOTED
    становится жёстким при сборке. Рубеж переводит ВЫПУСКАЕМЫЙ свод
    (оба правила, один вызов) и находит настоящий конфликт — прежде
    пакет уходил со справкой «sat 1/1»."""
    doc = _promotion_doc(with_existing_hard=True)
    run = _plant(tmp_path, doc)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _arith_translator(calls))
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-contradiction"
    words = str(err.value)
    assert "R-PROMOTED" in words and "R-A" in words
    assert "R-PROMOTED — стало жёстким при сборке (SOFT→HARD)" in words
    assert len(calls) == 1 and "R-PROMOTED |" in calls[0] and "R-A |" in calls[0]
    # Отчёт ПАКЕТА записан отдельно и называет отличие предмета; отчёта
    # кнопки не появилось (её отчёт — о каноническом своде).
    pkg = sv.read_report(run, sv.PACKAGE)
    assert pkg["verdict"] == "unsat" and set(pkg["core"]) == {"R-A", "R-PROMOTED"}
    assert pkg["subject_changes"] == [
        "R-PROMOTED — стало жёстким при сборке (SOFT→HARD)"]
    assert pkg["model_sha256"] == sv.model_fingerprint(_canon(doc))
    assert sv.read_report(run, sv.CANONICAL) is None


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_promoted_rule_is_checked_in_released_svod_single_compatible(
        tmp_path, monkeypatch):
    """Контрпример аудитора «zero_to_one_hard»: в каноническом своде
    жёстких правил нет, в выпускаемом — R-PROMOTED. Пакет выпускается
    только с переводом R-PROMOTED («sat 1/1» о выпускаемом наборе, а не
    «none 0/0»), отчёт пакета едет с пакетом, гейт зелен, второй выпуск
    модель не зовёт."""
    doc = _promotion_doc(with_existing_hard=False)
    run = _plant(tmp_path, doc)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _arith_translator(calls))
    files = tp.build_package(run)
    exported = json.loads(files["model.json"])
    hard_ids = {r["id"] for r in exported["rules"] if r["kind"] in HARD_KINDS}
    assert hard_ids == {"R-PROMOTED"}
    cons = json.loads(files["manifest.json"])["consistency"]
    assert cons["verdict"] == "sat"
    assert cons["n_rules"] == 1 and cons["n_translated"] == 1
    assert cons["subject"] == "package"
    assert cons["report"] == sv.PACKAGE_REPORT_REL
    assert cons["subject_sha256"] == sv.subject_fingerprint(exported)
    assert cons["subject_sha256"] != sv.subject_fingerprint(doc)
    assert cons["model_sha256"] == sv.model_fingerprint(_canon(doc))
    assert len(calls) == 1
    # Отчёт пакета — второй, явно названный файл; отчёта кнопки нет.
    package_report = run / "extract" / "smt-report-package.json"
    assert package_report.is_file()
    assert not (run / "extract" / "smt-report.json").exists()
    report = sv.read_report(run)             # отчёт о выпуске — отчёт пакета
    assert report["subject_sha256"] == cons["subject_sha256"]
    assert {r["rule"] for r in report["translations"]
            if r["status"] == "translated"} >= hard_ids
    assert files["smt-report.json"] == package_report.read_bytes()
    pkg = tmp_path / "promoted.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, counters = gate_task_package.check(pkg)
    assert problems == [] and counters["consistency"] == "sat"
    # Второй выпуск: отчёт пакета свеж для того же предмета — модель не
    # зовётся, манифест байт в байт прежний (Ф-1.7).
    assert tp.build_package(run)["manifest.json"] == files["manifest.json"]
    assert len(calls) == 1


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_fresh_button_report_does_not_release_a_promoted_rule(
        tmp_path, monkeypatch):
    """Свежий отчёт кнопки «sat 1/1» о каноническом своде (R-A) не
    выпускает пакет, в котором R-PROMOTED стало жёстким: предмет
    разошёлся — рубеж проверяет выпускаемый свод и находит конфликт;
    отчёт кнопки не переписывается."""
    doc = _promotion_doc(with_existing_hard=True)
    run = _plant(tmp_path, doc)
    canon = _canon(doc)
    button = sv.verify(doc, lambda p: _reply([{"rule": "R-A", "smt": "(<= x 1)"}]),
                       model_sha256=sv.model_fingerprint(canon))
    assert button["verdict"] == "sat"
    assert sv.is_fresh(button, sv.model_fingerprint(canon))
    sv.write_report(run, button)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _arith_translator(calls))
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-contradiction" and len(calls) == 1
    assert sv.read_report(run, sv.CANONICAL) == button


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_replay_child_refuses_when_source_has_no_package_report(
        tmp_path, monkeypatch):
    """Потомок повтора «из журнала»: модель звать нечем, у источника —
    только отчёт кнопки о каноническом своде, а выпускаемый свод
    отличается (R-PROMOTED стало жёстким). Честный отказ
    `smt-unavailable` называет изменившееся правило, повтор и совет
    (выпустить пакет в источнике) — не выпуск на старых счётчиках."""
    doc = _promotion_doc(with_existing_hard=False)
    source, child = _replay_pair(tmp_path, doc)
    canon = _canon(doc)
    sv.write_report(source, sv.verify(doc, lambda p: _reply([]),
                                      model_sha256=sv.model_fingerprint(canon)))
    monkeypatch.setattr(tp, "smt_channel_factory", _refusing_channel)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(child)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "R-PROMOTED — стало жёстким при сборке (SOFT→HARD)" in words
    assert "повтор прогона run-9001" in words
    assert "Выпустите пакет В ИСТОЧНИКЕ" in words
    assert not (child / "extract" / "smt-report-package.json").exists()


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_replay_child_takes_package_report_from_source(tmp_path, monkeypatch):
    """У источника есть отчёт ПАКЕТА о том же выпускаемом своде — потомок
    берёт его (свод байт в байт исходный), модель не зовётся, раздел
    манифеста тот же; водитель повтора берёт его заранее тем же
    законом."""
    doc = _promotion_doc(with_existing_hard=False)
    source, child = _replay_pair(tmp_path, doc)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _arith_translator(calls))
    src_files = tp.build_package(source)
    assert len(calls) == 1
    src_report = (source / "extract" / "smt-report-package.json").read_bytes()
    monkeypatch.setattr(tp, "smt_channel_factory", _refusing_channel)
    files = tp.build_package(child)
    assert (child / "extract" / "smt-report-package.json").read_bytes() == src_report
    assert files["smt-report.json"] == src_report
    assert json.loads(files["manifest.json"])["consistency"] \
        == json.loads(src_files["manifest.json"])["consistency"]
    assert not (child / "extract" / "smt-report.json").exists()
    (child / "extract" / "smt-report-package.json").unlink()
    assert tp.take_replay_source_verdict(child) is True
    assert (child / "extract" / "smt-report-package.json").read_bytes() == src_report


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_unchanged_subject_uses_button_report_once_and_ships_it(
        tmp_path, monkeypatch):
    """Обычный случай: сборка предмет не меняет — вердикт отчётом кнопки
    (один вызов модели на свод), отчёта пакета нет, копия отчёта кнопки
    едет с пакетом, гейт зелен, второй выпуск байт в байт."""
    run = _plant(tmp_path, _duration_doc())
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator(calls))
    files = tp.build_package(run)
    cons = json.loads(files["manifest.json"])["consistency"]
    assert cons["subject"] == "canonical" and cons["report"] == sv.REPORT_REL
    assert cons["subject_sha256"] \
        == sv.subject_fingerprint(json.loads(files["model.json"])) \
        == sv.subject_fingerprint(_duration_doc())
    assert not (run / "extract" / "smt-report-package.json").exists()
    assert files["smt-report.json"] == (run / "extract" / "smt-report.json").read_bytes()
    assert sv.read_report(run)["subject_sha256"] == cons["subject_sha256"]
    assert tp.build_package(run)["manifest.json"] == files["manifest.json"]
    assert len(calls) == 1
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate_task_package.check(pkg)[0] == []


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_stale_package_report_does_not_survive_a_canonical_release(
        tmp_path, monkeypatch):
    """Жизненный цикл (A01-F1): отчёт ПАКЕТА от ПРЕЖНЕГО выпуска, чей
    предмет к нынешнему выпуску не относится (каталог шаблонов
    сменился, и правило больше не становится жёстким при сборке), не
    остаётся в прогоне: свод тот же (отпечаток модели совпадает), и
    иначе читатель отчёта О ВЫПУСКЕ взял бы его вместо отчёта кнопки,
    которым вынесен вердикт, — «зелёное» производное о другом наборе
    правил."""
    run = _plant(tmp_path, _duration_doc())
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator(calls))
    files = tp.build_package(run)
    button = sv.read_report(run, sv.CANONICAL)
    # Отчёт пакета прежнего выпуска: тот же свод (отпечаток модели
    # совпадает — по нему он «свеж»), но предмет чужой.
    stale = dict(button, subject_sha256="0" * 64,
                 subject_changes=["R-НЕТ — стало жёстким при сборке "
                                  "(SOFT→HARD)"])
    sv.write_report(run, stale, sv.PACKAGE)
    assert sv.read_report(run) == stale          # без снятия читался бы он
    again = tp.build_package(run)
    assert json.loads(again["manifest.json"])["consistency"]["subject"] \
        == "canonical"
    assert again["manifest.json"] == files["manifest.json"]
    assert not (run / "extract" / "smt-report-package.json").exists()
    assert sv.read_report(run) == button         # отчёт о выпуске — кнопки
    assert sv.read_report(run, sv.CANONICAL) == button
    assert again["smt-report.json"] == \
        (run / "extract" / "smt-report.json").read_bytes()
    assert len(calls) == 1                       # модель не звали заново


def _rezip(files: dict, tmp_path, name: str):
    """Пакет из файлов с пересчитанным перечнем `manifest.files` (как у
    сборщика): подмена одного файла не должна краснить гейт суммами."""
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = [
        {"name": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
        for n, b in sorted(files.items()) if n != "manifest.json"]
    files = dict(files)
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2)
                              + "\n").encode("utf-8")
    pkg = tmp_path / name
    pkg.write_bytes(tp.write_zip(files))
    return pkg


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_gate_reds_package_whose_report_does_not_cover_its_hard_rules(
        tmp_path, monkeypatch):
    """Гейт производителя (и аудитор по настоящему ZIP) сверяет отчёт из
    пакета с жёсткими правилами model.json пакета и предметом: жёсткое
    правило без строки перевода, чужой предмет, запись вне формы,
    отсутствие отчёта, форма ключа до 12.09.2026 и «none 0/0» при
    жёстком правиле — красные."""
    run = _plant(tmp_path, _duration_doc())
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator([]))
    files = tp.build_package(run)
    assert gate_task_package.check(_rezip(files, tmp_path, "ok.zip"))[0] == []
    # (а) жёсткое правило пакета без строки перевода — как у контрпримера
    # аудитора, где сборка добавила HARD, а справка осталась «sat 1/1».
    doc = json.loads(files["model.json"])
    doc["rules"].append({"id": "R-EXTRA", "title": "Добавленное",
                         "statement": "Длительность рабочей смены не менее 750 минут.",
                         "zone": "PAIRING", "kind": "HARD",
                         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    tampered = dict(files, **{"model.json": json.dumps(
        doc, ensure_ascii=False, indent=1).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "extra.zip"))[0]
    assert any("R-EXTRA" in p and "без перевода" in p for p in problems), problems
    assert any("ДРУГОМ наборе правил" in p for p in problems), problems
    # (б) записи вне формы в отчёте (A01-F2) — красный, хотя правила названы.
    report = json.loads(files["smt-report.json"])
    report["malformed"] = [{"field": "asserts", "index": 1, "kind": "null",
                            "text": "null"}]
    tampered = dict(files, **{"smt-report.json": json.dumps(
        report, ensure_ascii=False, indent=1).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "mal.zip"))[0]
    assert any("asserts[1]: не объект (null)" in p for p in problems), problems
    # (в) отчёта в пакете нет — сверять нечем.
    without = {k: v for k, v in files.items() if k != "smt-report.json"}
    problems = gate_task_package.check(_rezip(without, tmp_path, "no.zip"))[0]
    assert any("smt-report.json отсутствует" in p for p in problems), problems
    # (г) справка о другом предмете.
    man = json.loads(files["manifest.json"])
    man["consistency"]["subject_sha256"] = "0" * 64
    tampered = dict(files, **{"manifest.json": json.dumps(man).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "subj.zip"))[0]
    assert any("ДРУГОМ наборе правил" in p for p in problems), problems
    # (д) форма ключа до 12.09.2026 — без предмета и отчёта: красный словами.
    man = json.loads(files["manifest.json"])
    man["consistency"].pop("subject_sha256")
    man["consistency"].pop("subject")
    tampered = dict(files, **{"manifest.json": json.dumps(man).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "old.zip"))[0]
    assert any("форма до 12.09.2026" in p for p in problems), problems
    # (е) «none 0/0» при жёстком правиле в model.json пакета (второй
    # контрпример аудитора).
    man = json.loads(files["manifest.json"])
    man["consistency"].update(verdict="none", n_rules=0, n_translated=0)
    tampered = dict(files, **{"manifest.json": json.dumps(man).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "none.zip"))[0]
    assert any("«none»" in p and "R-H" in p for p in problems), problems
    # (ж) отчёт, не назвавший своего предмета (поле снято, чтобы уйти
    # от сверки): именами правил строки перевода совпадают, но с какими
    # ФОРМУЛИРОВКАМИ они переведены — недоказуемо. Красный.
    report = json.loads(files["smt-report.json"])
    report.pop("subject_sha256")
    tampered = dict(files, **{"smt-report.json": json.dumps(
        report, ensure_ascii=False, indent=1).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "nosub.zip"))[0]
    assert any("не о том предмете проверки" in p for p in problems), problems
    # (з) отчёт о ЧУЖОМ предмете при тех же именах правил — тот же
    # красный (перевод другой формулировки того же правила).
    report = json.loads(files["smt-report.json"])
    report["subject_sha256"] = "0" * 64
    tampered = dict(files, **{"smt-report.json": json.dumps(
        report, ensure_ascii=False, indent=1).encode("utf-8")})
    problems = gate_task_package.check(_rezip(tampered, tmp_path, "othersub.zip"))[0]
    assert any("не о том предмете проверки" in p for p in problems), problems


# ── A01-F2 (вердикт аудитора 12.09.2026, P2): записи вне формы ──────────
# Ответ с двумя верными переводами и лишним `null`/`17`/`"R-X"` терял
# лишний элемент молча: один вызов, SAT 2/2, dropped=[]. Теперь это
# нарушение формы: один общий повтор, затем отчёт без вердикта с позицией
# и видом записи, отказ выпуска и красный гейт.

_GOOD = [{"rule": "R-A", "smt": "(<= x 1)"},
         {"rule": "R-B", "smt": "(<= x 5)"}]


@pytest.mark.parametrize("extra, kind", [
    (None, "null"), (17, "number"), ("R-X", "string"), (["R-X"], "list"),
    (True, "bool")], ids=["null", "number", "string", "list", "bool"])
def test_nonobject_assert_record_is_a_form_error_not_a_silent_drop(
        tmp_path, extra, kind):
    calls: list = []

    def llm(prompt):
        calls.append(prompt)
        return _reply(_GOOD + [extra])
    doc = _doc()
    canon = _canon(doc)
    report = sv.verify(doc, llm, model_sha256=sv.model_fingerprint(canon))
    assert len(calls) == 2                      # один повтор
    assert f"asserts[2]: не объект ({kind})" in calls[1]   # записка повтора
    assert report["verdict"] == "none"
    assert report["malformed"] == [
        {"field": "asserts", "index": 2, "kind": kind,
         "text": json.dumps(extra, ensure_ascii=False)}]
    assert f"asserts[2]: не объект ({kind})" in report["status"]
    assert report["untranslated"] == [] and report["dropped"] == []
    # выдуманного идентификатора у записи без идентификатора нет
    assert [t["rule"] for t in report["translations"]] == ["R-A", "R-B"]
    run = _plant(tmp_path, doc)
    sv.write_report(run, report)
    assert sv.report_completeness(report, doc)["malformed"] == ["asserts[2]"]
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, canon)
    assert err.value.code == "smt-incomplete"
    assert err.value.items == ["asserts[2]"]
    assert f"asserts[2]: не объект ({kind})" in str(err.value)


def test_nonstring_decl_is_a_form_error_with_one_retry(tmp_path):
    calls: list = []

    def llm(prompt):
        calls.append(prompt)
        return _reply(_GOOD, decls=["(declare-fun x () Int)", 5])
    doc = _doc()
    report = sv.verify(doc, llm, model_sha256=sv.model_fingerprint(_canon(doc)))
    assert len(calls) == 2 and report["verdict"] == "none"
    assert "decls[1]: не строка (number)" in calls[1]
    assert report["malformed"] == [
        {"field": "decls", "index": 1, "kind": "number", "text": "5"}]
    assert "decls[1]: не строка (number)" in report["status"]
    assert sv.report_completeness(report, doc)["malformed"] == ["decls[1]"]
    run = _plant(tmp_path, doc)
    sv.write_report(run, report)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, _canon(doc))
    assert err.value.code == "smt-incomplete" and err.value.items == ["decls[1]"]


def test_asserts_not_a_list_is_named_as_such():
    def llm(prompt):
        return json.dumps({"decls": ["(declare-fun x () Int)"],
                           "asserts": {"rule": "R-A", "smt": "(<= x 1)"}})
    report = sv.verify(_doc(), llm)
    assert report["verdict"] == "none"
    assert report["malformed"] == [
        {"field": "asserts", "index": None, "kind": "object",
         "text": json.dumps({"rule": "R-A", "smt": "(<= x 1)"})}]
    assert "asserts: не список (object)" in report["status"]
    assert report["untranslated"] == ["R-A", "R-B"]


def test_form_error_recovers_after_one_retry_and_shares_the_budget():
    """Положительный контроль: второй ответ чист — вердикт как обычно.
    Бюджет повтора общий: запись вне формы потратила повтор, неполный
    второй ответ — третьего вызова нет, отчёт честно без вердикта."""
    calls: list = []

    def llm(prompt):
        calls.append(prompt)
        return _reply(_GOOD + [None]) if len(calls) == 1 else _reply(_GOOD)
    report = sv.verify(_doc(), llm)
    assert len(calls) == 2 and report["verdict"] == "sat"
    assert report["malformed"] == [] and report["n_translated"] == 2

    calls.clear()

    def llm2(prompt):
        calls.append(prompt)
        return _reply(_GOOD + [17]) if len(calls) == 1 else _reply(_GOOD[:1])
    report = sv.verify(_doc(), llm2)
    assert len(calls) == 2 and report["verdict"] == "none"
    assert report["untranslated"] == ["R-B"] and report["malformed"] == []


@pytest.mark.parametrize("row, shown", [
    ({"smt": "(<= x 10)"}, "null"),
    ({"rule": None, "smt": "(<= x 10)"}, "null"),
    ({"rule": "", "smt": "(<= x 10)"}, '""'),
    ({"rule": "   ", "smt": "(<= x 10)"}, '"   "'),
    ({"rule": 17, "smt": "(<= x 10)"}, "17"),
    ({"rule": ["R-A"], "smt": "(<= x 10)"}, '["R-A"]'),
], ids=["no_key", "null", "empty", "blank", "number", "list"])
def test_record_without_an_identifier_is_named_by_position_not_invented(
        tmp_path, row, shown):
    """Запись-объект, не назвавшая правила (`rule` нет, пуст, не строка),
    зовётся ИСХОДНОЙ ПОЗИЦИЕЙ в ответе — «asserts[2]», а не выдуманным
    именем («None» прежней сборки — такое же выдуманное имя, как чужое:
    вердикт аудитора A01-F2 запрещает приписывать идентификатор записи
    без идентификатора). Позиция — в исходном массиве ответа, а не в
    отфильтрованном: до неё стоит запись ВНЕ формы."""
    calls: list = []

    def llm(prompt):
        calls.append(prompt)
        return _reply([_GOOD[0], None, _GOOD[1], row])
    doc = _doc()
    report = sv.verify(doc, llm, model_sha256=sv.model_fingerprint(_canon(doc)))
    assert len(calls) == 2                      # один общий повтор
    assert report["verdict"] == "none"
    # позиция 3 — в ИСХОДНОМ массиве (в отфильтрованном она была бы 2)
    assert report["dropped"] == ["asserts[3]"]
    assert f"asserts[3]: запись без идентификатора правила " \
           f"(rule = {shown})" in calls[1]
    assert "asserts[3]" in report["status"]
    assert "None" not in json.dumps(report, ensure_ascii=False)
    # выдуманного правила в строках отчёта нет — только правила свода
    assert [t["rule"] for t in report["translations"]] == ["R-A", "R-B"]
    # запись вне формы названа отдельно от записи без идентификатора
    assert report["malformed"] == [{"field": "asserts", "index": 1,
                                    "kind": "null", "text": "null"}]
    run = _plant(tmp_path, doc)
    sv.write_report(run, report)
    assert sv.report_completeness(report, doc)["foreign"] == ["asserts[3]"]
    with pytest.raises(tp.TaskPackageError) as err:
        tp.consistency_of(run, _canon(doc))
    assert err.value.code == "smt-incomplete"
    assert err.value.items == ["asserts[1]", "asserts[3]"] \
        or err.value.items == ["asserts[3]", "asserts[1]"]
    assert "asserts[3]" in str(err.value)


def test_two_records_without_identifiers_are_named_separately():
    """Две записи без идентификатора — две разные позиции: перечень не
    схлопывается в одно имя, и по нему видно, сколько записей ответа не
    о правилах свода."""
    def llm(prompt):
        return _reply([{"smt": "(<= x 1)"}, _GOOD[0], {"smt": "(<= x 5)"},
                       _GOOD[1]])
    report = sv.verify(_doc(), llm)
    assert report["dropped"] == ["asserts[0]", "asserts[2]"]
    assert report["verdict"] == "none" and report["untranslated"] == []


def test_positionless_call_of_the_classifier_uses_the_list_order():
    """Разбор зовётся и без позиций (прямой вызов, старые читатели): тогда
    позицией считается номер в переданном списке — имя всё равно не
    выдумывается."""
    rows, problems, missing, dropped = sv.classify_translation(
        [{"rule": "R-A", "smt": "(<= x 1)"}, {"smt": "(<= x 5)"}],
        ["R-A", "R-B"])
    assert dropped == ["asserts[1]"]
    assert missing["R-B"] == "нет записи в ответе переводчика"
    assert any("asserts[1]" in p for p in problems)
    assert rows["R-A"] == {"status": "translated", "smt": "(<= x 1)"}


# ── Беда САМОЙ проверки: отчёт некуда положить (находка неавторского
# ревью 12.09.2026, П0 §1.4/§12.1) ────────────────────────────────────
# Каталог (или иной не-файл) на месте extract/smt-report.json давал
# PermissionError МИМО TaskPackageError: ручка панели ловит только
# отказы рубежа, и наружу уходил HTTP 500 — уже после потраченного
# вызова модели и уже после записи «отказ 409» в журнал действий.
# Закон: беда проверки — отказ словами с кодом `smt-unavailable`, а
# путь отчёта, видный заранее, судится ДО вызова модели.

def _counting_factory(built, calls, monkeypatch, translator=_arith_translator):
    """Канал переводчика со счётчиком: `built` — построения канала,
    `calls` — задания, ушедшие модели."""
    def factory(run_dir):
        built.append(str(run_dir))
        return translator(calls)
    monkeypatch.setattr(tp, "smt_channel_factory", factory)


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_directory_instead_of_the_button_report_refuses_before_the_model(
        tmp_path, monkeypatch):
    """Канонический путь выпуска: на месте `extract/smt-report.json` —
    каталог. Отказ `smt-unavailable` словами, называющими путь, ДО
    вызова модели (канал не строится, задание не уходит); наружу из
    сборки PermissionError не выходит."""
    run = _plant(tmp_path, _duration_doc())
    (run / "extract" / "smt-report.json").mkdir()
    built, calls = [], []
    _counting_factory(built, calls, monkeypatch, translator=_duration_translator)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "некуда положить" in words and "smt-report.json" in words
    assert built == [] and calls == []      # вызов модели не потрачен


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_directory_instead_of_the_package_report_refuses_before_the_model(
        tmp_path, monkeypatch):
    """Тот же закон на пути ОТЧЁТА ПАКЕТА (предмет выпуска разошёлся с
    каноническим): каталог на месте `extract/smt-report-package.json` —
    отказ `smt-unavailable` до вызова модели."""
    doc = _promotion_doc(with_existing_hard=False)
    run = _plant(tmp_path, doc)
    (run / "extract" / "smt-report-package.json").mkdir()
    built, calls = [], []
    _counting_factory(built, calls, monkeypatch)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "некуда положить" in words and "smt-report-package.json" in words
    assert built == [] and calls == []


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_replay_child_with_an_occupied_report_path_refuses_in_words(
        tmp_path, monkeypatch):
    """Потомок повтора: вердикт источника есть, а положить его некуда —
    на месте отчёта потомка каталог. Отказ `smt-unavailable` словами
    (путь и беда ОС названы), а не PermissionError наружу; помощник
    повтора (`take_replay_source_verdict`) от той же беды не падает и
    честно отвечает «не взял»."""
    doc = _promotion_doc(with_existing_hard=False)
    source, child = _replay_pair(tmp_path, doc)
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _arith_translator(calls))
    tp.build_package(source)                  # у источника отчёт ПАКЕТА
    assert len(calls) == 1
    (child / "extract" / "smt-report-package.json").mkdir()
    monkeypatch.setattr(tp, "smt_channel_factory", _refusing_channel)
    assert tp.take_replay_source_verdict(child) is False
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(child)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "не записан" in words and "smt-report-package.json" in words
    assert len(calls) == 1                    # модель второй раз не звали


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_unwritable_report_path_refuses_when_the_write_fails(tmp_path,
                                                             monkeypatch):
    """Беда записи, заранее НЕ видная (путь свободен, а запись падает —
    кончился диск, файл только для чтения): отказ тот же
    `smt-unavailable` словами, называющими путь и беду ОС."""
    run = _plant(tmp_path, _duration_doc())
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator(calls))
    real_write_text = pathlib.Path.write_text

    def no_space(self, *a, **k):
        if self.name.startswith("smt-report"):
            raise OSError(28, "No space left on device")
        return real_write_text(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "write_text", no_space)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "не записан" in words and "OSError" in words
    assert "No space left on device" in words
    assert len(calls) == 1                    # вердикт был получен


# ── Замки машиной держат и под `python -O` (находка неавторского
# ревью 12.09.2026) ───────────────────────────────────────────────────
# `assert` под ключом -O не исполняется: замок, стерегущий ВЫПУСК,
# обязан быть отказом словами, а не утверждением, которого на площадке
# может не быть.

@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_subject_lock_after_the_gate_refuses_in_words(tmp_path, monkeypatch):
    """Замок предмета: между рубежом и манифестом «встало»
    преобразование правил (предмет считается иначе) — выпуск ОТКАЗЫВАЕТ
    словами `TaskPackageError`, а не `AssertionError`; слова называют
    оба отпечатка."""
    run = _plant(tmp_path, _duration_doc())
    calls: list = []
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda rd: _duration_translator(calls))
    real_fingerprint, real_gate = sv.subject_fingerprint, tp.consistency_of
    passed = {"gate": False}

    def gate(*a, **kw):
        got = real_gate(*a, **kw)
        passed["gate"] = True                 # рубеж позади — предмет «поплыл»
        return got

    def drifting(doc):
        return "f" * 64 if passed["gate"] else real_fingerprint(doc)
    monkeypatch.setattr(tp, "consistency_of", gate)
    monkeypatch.setattr(sv, "subject_fingerprint", drifting)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "изменился предмет проверки" in words
    assert "f" * 64 in words


def test_release_locks_are_explicit_raises_not_assertions():
    """Ни одного `assert` в модулях рубежа выпуска: под `python -O`
    утверждения не исполняются, и замок, стерегущий выпуск, исчез бы
    вместе с ними. Судится разбором исходников (ast), а не словами
    комментария."""
    import ast
    root = pathlib.Path(tp.__file__).resolve().parent
    guarded = ["export_task_package.py", "smt_verify.py",
               "gates/gate_task_package.py"]
    left = {}
    for rel in guarded:
        src = (root / rel).read_text(encoding="utf-8")
        lines = [n.lineno for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Assert)]
        if lines:
            left[rel] = lines
    assert left == {}, f"замки на assert (исчезают под -O): {left}"


def test_a_verdictless_report_without_a_reason_is_words_not_an_assert(
        monkeypatch):
    """Замок формы отчёта в `smt_verify` (прежде `assert`): отчёт без
    вердикта обязан назвать причину — непереведённое правило, чужую
    запись либо запись вне формы. Причины нет — проверка объявляется
    несостоявшейся словами `SmtVerifyError`, и под `python -O` этот
    замок остаётся."""
    real = sv.classify_translation

    def hides_the_reason(asserts, ids, positions=None):
        rows = real(asserts, ids, positions)[0]
        # Все правила переведены, но разбор «нашёл» нарушение формы и ни
        # одной причины не назвал: отчёт вышел бы без вердикта и без слов.
        return rows, ["нарушение формы без причины"], {}, []
    monkeypatch.setattr(sv, "classify_translation", hides_the_reason)
    full = _reply([{"rule": "R-A", "smt": "(<= x 1)"},
                   {"rule": "R-B", "smt": "(<= x 5)"}])
    with pytest.raises(sv.SmtVerifyError) as err:
        sv.verify(_doc(), lambda p: full)
    assert "не назвал причины" in str(err.value)


@pytest.mark.parametrize("rid", [_NO_KEY, "   "], ids=["no_key", "blank"])
@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_hard_rule_without_an_identifier_refuses_the_real_release(
        tmp_path, monkeypatch, rid):
    """Наблюдение неавторского ревью 12.09.2026: жёсткое правило БЕЗ
    ключа `id` роняло настоящую сборку пакета `KeyError: 'id'` в
    `vmp_form._rule_to_row` — раньше рубежа, который сказал бы о нём
    словами (правило с ПУСТЫМ идентификатором до рубежа доходило).
    Теперь оба вида дают ОДИН отказ словами с позицией, модель не
    зовётся, и трассировка наружу не выходит."""
    # The same valid modern carrier passes the eight controls above. Damage
    # only its identity; do not also feed an obsolete unlanded fixture.
    doc = _duration_doc()
    for rule in doc["rules"]:
        if rule.get("kind") == "HARD":
            if rid is _NO_KEY:
                rule.pop("id", None)
            else:
                rule["id"] = rid
    run = _plant(tmp_path, doc)
    built, calls = [], []
    _counting_factory(built, calls, monkeypatch)
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    assert err.value.code == "smt-unavailable"
    words = str(err.value)
    assert "без идентификатора" in words
    assert "позиции среди жёстких правил: 0" in words
    assert built == [] and calls == []


@pytest.mark.usefixtures("a01_modern_promotion_catalog")
def test_blank_identity_promoted_by_real_healing_refuses_before_channel(
        tmp_path, monkeypatch, record_property):
    """Z13: whitespace passes the real schema, then SOFT becomes HARD.

    R4 is an opt-in, memory-only removal of the second identity check.
    Delegating spies observe phases; they never supply a verdict or bypass
    the loader, family catalog, healing, provenance or projection guards.
    """
    import ast
    import inspect
    import os
    from cdpm import model as m

    doc = _duration_doc()
    rule = doc["rules"][0]
    rule.update(id="   ", kind="SOFT")
    rule["template_fit"]["params"]["max_duty_duration"]["source"]["rule_id"] = "   "
    assert m.validate_schema(doc) == []
    loaded = tp._load_modern_package_model(_canon(doc))
    assert loaded["rules"][0]["kind"] == "SOFT"
    assert loaded["rules"][0]["id"] == "   "
    assert sv.nameless_hard_positions(loaded) == []

    phases, healed_kinds, built = [], [], []
    real_refuse, real_heal = tp.refuse_nameless_hard_rules, tp.heal_for_package

    def observe_refusal(value):
        phases.append([(r["id"], r["kind"]) for r in value["rules"]])
        return real_refuse(value)

    def observe_healing(value):
        result = real_heal(value)
        healed_kinds.append([(r["id"], r["kind"]) for r in value["rules"]])
        return result

    def never_construct_channel(run_dir):
        built.append(str(run_dir))
        raise RuntimeError("test sentinel: identity must refuse before channel construction")

    monkeypatch.setattr(tp, "refuse_nameless_hard_rules", observe_refusal)
    monkeypatch.setattr(tp, "heal_for_package", observe_healing)
    monkeypatch.setattr(tp, "smt_channel_factory", never_construct_channel)
    mutation = os.environ.get("A01_NAMELESS_MUTATION")
    assert mutation in (None, "drop_late")
    if mutation == "drop_late":
        # Recompile only this exact production function; no source file is
        # modified. Every statement except its second named call is retained.
        module = ast.parse(inspect.getsource(tp._build_package))
        function, = module.body
        matches = [node for node in function.body
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "refuse_nameless_hard_rules"]
        assert len(matches) == 2
        assert ast.dump(matches[0].value) == ast.dump(matches[1].value)
        function.body.remove(matches[1])
        namespace = dict(tp.__dict__)
        exec(compile(module, tp.__file__, "exec"), namespace)
        monkeypatch.setattr(tp, "_build_package", namespace[function.name])

    run = _plant(tmp_path, doc)
    model_path = run / "extract" / "model.json"
    original = model_path.read_bytes()
    with pytest.raises(tp.TaskPackageError) as err:
        tp.build_package(run)
    record_property("mutation", mutation or "ORIGINAL")
    record_property("refusal_code", str(err.value.code))
    record_property("refusal_words", str(err.value))
    record_property("identity_phases", repr(phases))
    record_property("healed_kinds", repr(healed_kinds))
    record_property("channel_constructions", len(built))
    assert model_path.read_bytes() == original
    assert healed_kinds == [[("   ", "HARD")]]
    assert err.value.code == "smt-unavailable"
    assert "без идентификатора" in str(err.value)
    assert "позиции среди жёстких правил: 0" in str(err.value)
    assert built == []
    assert phases == [[("   ", "SOFT")], [("   ", "HARD")]]
