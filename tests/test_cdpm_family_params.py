# -*- coding: utf-8 -*-
"""Полнота параметров семьи (закон CDPM 09.09.2026, набор Н0-B):
параметр шаблона, не заданный ни одним правилом свода, спрашивается
анкетой кодом (структурный ключ Q-3a/3b-параметр-{семья}-{параметр},
носители блокируются), ответ применяется кодом в посадку первого
носителя записью {value, quote, answer}; «не применяется» — решение
ответом; неразборчивое — «не разобран». Закон полноты — по каталогу:
param_sets семьи = минимальные полные наборы, без объявления — все
параметры (fail-closed)."""
import json
from pathlib import Path

import pytest

from cdpm import answers as answers_mod
from cdpm import exchange as exchange_mod
from cdpm import family_params as fp
from cdpm import model as m
from cdpm import provenance as prov
from cdpm.sample import sample_model

REV = m.registry_fingerprint()
QID = "Q-3b-параметр-rest_floors-min_rest_outstation"
RAW = ("Минимальный отдых между сменами вне базы — 10:00 (600 минут). "
       "Это второй параметр того же правила о полах отдыха: дома — "
       "12:00 (720 минут), вне базы — 10:00 (600 минут).")
CLAUSES = {("S1", "П.2.3"):
           "«не планировать явку ранее 08:00 после ночного рейса»"}


def _doc_with_home_floor():
    """Свод образца: правило ростеринга R-S1-П-2-3 посажено на
    rest_floors с одним параметром из двух (как VN-023 в Н0-B)."""
    doc = sample_model()
    r = next(x for x in doc["rules"] if x["id"] == "R-S1-П-2-3")
    r["template_fit"] = {"category": "A", "registry_rev": REV,
                         "family": "rest_floors",
                         "params": {"min_rest_home": {
                             "value": 720, "quote": "ранее 08:00"}}}
    return doc, r


def _q(doc, qid):
    return next(x for x in doc["questions"] if x["id"] == qid)


# ── закон полноты ────────────────────────────────────────────────────

def test_missing_params_law_by_catalog_sets():
    """Семья без param_sets требует все параметры (fail-closed); семья с
    наборами полна, когда покрыт хотя бы один; недостающие — по набору,
    ближайшему к заданному."""
    assert fp.missing_params("rest_floors", {"min_rest_home"}) \
        == ["min_rest_outstation"]
    assert fp.missing_params("rest_floors",
                             {"min_rest_home", "min_rest_outstation"}) == []
    assert fp.missing_params("days_off_floor", {"min_days_off"}) == []
    assert fp.missing_params("days_off_floor", {"min_days_off_in_window"}) \
        == ["days_off_window_days"]
    assert fp.missing_params("night_series", {
        "max_consecutive_night_duties", "night_duty_start",
        "night_duty_end"}) == []
    # Беспараметрная и неизвестная семьи — ничего не требуют.
    assert fp.missing_params("layover_penalty", set()) == []
    assert fp.missing_params("no_such_family", set()) == []


def test_coverage_is_judged_over_the_whole_svod():
    """H0: два носителя rest_floors по одному параметру — семья полна;
    посадка C носителем не является."""
    doc, r = _doc_with_home_floor()
    other = next(x for x in doc["rules"] if x["id"] == "R-S2-П-7-1")
    other["template_fit"] = {"category": "A", "registry_rev": REV,
                             "family": "rest_floors",
                             "params": {"min_rest_outstation": {
                                 "value": 600, "quote": "80 ч"}}}
    cov = fp.family_coverage(doc)["rest_floors"]
    assert cov["carriers"] == ["R-S1-П-2-3", "R-S2-П-7-1"]
    assert cov["missing"] == []
    assert fp.ensure_family_param_questions(doc) == []
    other["template_fit"] = {"category": "C", "registry_rev": REV,
                             "gap": "нет механики"}
    assert fp.family_coverage(doc)["rest_floors"]["missing"] \
        == ["min_rest_outstation"]


# ── вопрос ───────────────────────────────────────────────────────────

def test_question_structural_key_part_blockers_idempotent():
    doc, r = _doc_with_home_floor()
    created = fp.ensure_family_param_questions(doc)
    assert created == [QID]
    q = _q(doc, QID)
    assert q["part"] == "3b"                     # зона семьи — ROSTERING
    assert q["answer_format"]["type"] == "value"
    assert q["rule_refs"] == ["R-S1-П-2-3"]
    assert q["source_refs"] == [{"sid": "S1", "anchor": "П.2.3"}]
    assert "min_rest_outstation" in q["text"] and "rest_floors" in q["text"]
    assert "не применяется" in q["text"]
    assert QID in r["blockers"]                  # fail-closed до ответа
    assert m.is_open_question(q)
    assert fp.ensure_family_param_questions(doc) == []
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    assert exchange_mod.is_code_built_question(QID)
    assert fp.question_target(q) == ("rest_floors", "min_rest_outstation")


def test_no_question_without_carriers():
    doc = sample_model()
    assert fp.ensure_family_param_questions(doc) == []
    assert not any(fp.is_param_question(q["id"]) for q in doc["questions"])


# ── ответ ────────────────────────────────────────────────────────────

def test_value_answer_lands_in_first_carrier_with_marker():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    summary = answers_mod.apply_answers(doc, {QID: RAW})
    assert summary["applied"] == 1 and summary["unparsed"] == 0
    assert summary["resolved_rules"] == ["R-S1-П-2-3"]
    cell = r["template_fit"]["params"]["min_rest_outstation"]
    # Точное происхождение (A03-мин, контракт П0 §2.3, 12.09.2026):
    # рядом с прежней строкой `answer` (родословная) ячейка несёт
    # издание отвеченного вопроса и отпечаток ТОЙ версии ответа,
    # из которой значение прочитано.
    assert cell == {"value": 600,
                    "quote": "Минимальный отдых между сменами вне базы "
                             "— 10:00 (600 минут)",
                    "answer": QID,
                    "source": {
                        "kind": "answer",
                        "question": prov.question_edition(_q(doc, QID)),
                        "version": {"fingerprint":
                                    prov.answer_version_fingerprint(RAW)},
                        "effect": {"rule": "min_rest_outstation",
                                   "field": "value"},
                        "root": QID}}
    assert QID not in (r.get("blockers") or [])
    q = _q(doc, QID)
    assert q["answer"]["status"] == "применён"
    assert {(e["rule"], e["field"]) for e in q["answer"]["effects"]} \
        == {("R-S1-П-2-3", "blockers"), ("R-S1-П-2-3", "template_fit")}
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    # Цитата судится по тексту ответа, не по клаузе.
    assert m.validate_fit_quotes(doc, CLAUSES) == []
    assert fp.family_coverage(doc)["rest_floors"]["missing"] == []
    assert fp.ensure_family_param_questions(doc) == []


def test_declined_answer_records_decision_without_cell():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    summary = answers_mod.apply_answers(
        doc, {QID: "Не применяется: такого требования у нас нет."})
    assert summary["applied"] == 1
    assert "min_rest_outstation" not in r["template_fit"]["params"]
    assert QID not in (r.get("blockers") or [])
    cov = fp.family_coverage(doc)["rest_floors"]
    assert cov["declined"] == {"min_rest_outstation"} and cov["missing"] == []
    assert fp.ensure_family_param_questions(doc) == []
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("raw", [
    "Зависит от типа рейса.",                 # значения нет
    "Дома — 720 минут, вне базы — 600 минут.",  # два разных в одном предложении
    "12:00 (600 минут)",                      # противоречивая запись
])
def test_unparsed_answer_is_reasked_and_keeps_blockers(raw):
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    summary = answers_mod.apply_answers(doc, {QID: raw})
    assert summary["unparsed"] == 1 and summary["applied"] == 0
    q = _q(doc, QID)
    assert q["answer"]["status"] == "не разобран"
    assert "одно значение" in q["answer"]["note"]
    assert m.is_open_question(q)
    assert QID in r["blockers"]
    assert "min_rest_outstation" not in r["template_fit"]["params"]
    assert m.validate_model(doc) == []


def test_revision_rolls_back_cell_by_journal_and_reapplies():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    answers_mod.apply_answers(doc, {QID: RAW})
    rep = answers_mod.open_revision(doc, QID)
    assert rep["restored"] and rep["effects_reverted"] == 2
    assert "min_rest_outstation" not in r["template_fit"]["params"]
    assert rep["created"] == QID + "-R" and rep["created"] in r["blockers"]
    assert m.validate_model(doc) == []
    summary = answers_mod.apply_answers(doc, {rep["created"]: "9 часов"})
    assert summary["applied"] == 1
    cell = r["template_fit"]["params"]["min_rest_outstation"]
    # K05: ответ на вопрос-пересмотр пишет СВОЁ издание и СВОЮ
    # версию; корень хранится отдельно (source.root и прежняя
    # строка answer) — родословная, не источник.
    assert cell == {"value": 540, "quote": "9 часов", "answer": QID,
                    "source": {
                        "kind": "answer",
                        "question": prov.question_edition(
                            _q(doc, rep["created"])),
                        "version": {"fingerprint":
                                    prov.answer_version_fingerprint("9 часов")},
                        "effect": {"rule": "min_rest_outstation",
                                   "field": "value"},
                        "root": QID}}
    assert cell["source"]["question"]["id"] == rep["created"]
    # -R и корень сняты с носителя (чужая развилка образца остаётся).
    assert not any(fp.is_param_question(b) for b in r.get("blockers") or [])
    assert m.validate_model(doc) == []


def test_lost_cell_is_healed_from_applied_answer_not_reasked():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    answers_mod.apply_answers(doc, {QID: RAW})
    r["template_fit"]["params"].pop("min_rest_outstation")   # заплата переиздала посадку
    assert fp.ensure_family_param_questions(doc) == []
    assert r["template_fit"]["params"]["min_rest_outstation"]["answer"] == QID
    assert r["template_fit"]["params"]["min_rest_outstation"]["value"] == 600


def test_withdraw_when_param_appears_and_reopen_when_lost():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    r["template_fit"]["params"]["min_rest_outstation"] = {
        "value": 600, "quote": "600"}
    assert fp.ensure_family_param_questions(doc) == []
    q = _q(doc, QID)
    assert not m.is_open_question(q)
    assert q["answer"]["raw"] == fp.CODE_CLOSED_RAW
    assert QID not in (r.get("blockers") or [])
    assert m.validate_model(doc) == []
    r["template_fit"]["params"].pop("min_rest_outstation")
    assert fp.ensure_family_param_questions(doc) == [QID]
    assert m.is_open_question(q) and QID in r["blockers"]
    assert m.validate_model(doc) == []


def test_multi_carrier_value_lands_on_first_carrier_but_others_remain_blocked():
    doc, r = _doc_with_home_floor()
    other = next(x for x in doc["rules"] if x["id"] == "R-S2-П-7-1")
    other["template_fit"] = {"category": "A", "registry_rev": REV,
                             "family": "rest_floors",
                             "params": {"min_rest_home": {
                                 "value": 720, "quote": "80 ч"}}}
    created = fp.ensure_family_param_questions(doc)
    assert created == [QID]
    q = _q(doc, QID)
    assert q["rule_refs"] == ["R-S1-П-2-3", "R-S2-П-7-1"]
    assert QID in r["blockers"] and QID in other["blockers"]
    answers_mod.apply_answers(doc, {QID: "600 минут"})
    assert "min_rest_outstation" in r["template_fit"]["params"]
    assert "min_rest_outstation" not in other["template_fit"]["params"]
    # A02 / CDPM-395: the first-carrier writer stays, but family coverage
    # does not substitute for a result on the other row named by the question.
    assert QID in (other.get("blockers") or [])
    assert q["answer"]["status"] == m.PARTIAL_ANSWER_STATUS
    assert m.validate_model(doc) == []


# ── контракт ─────────────────────────────────────────────────────────

def test_marker_requires_applied_question_and_quote_from_answer():
    doc, r = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)      # вопрос есть, ответа нет
    r["template_fit"]["params"]["min_rest_outstation"] = {
        "value": 600, "quote": "600 минут", "answer": "Q-нет-такого"}
    assert m.validate_schema(doc) == []
    assert any("которого нет в анкете" in p for p in m.validate_model(doc))
    r["template_fit"]["params"]["min_rest_outstation"]["answer"] = QID
    assert any("а не применённым" in p for p in m.validate_model(doc))
    answers_mod.apply_answers(doc, {QID: "10 часов"})
    assert m.validate_model(doc) == []
    r["template_fit"]["params"]["min_rest_outstation"]["quote"] = "чужой текст"
    assert any("подстрокой ответа" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))
    # ИНВЕРСИЯ ОЖИДАНИЯ (A03, контракт П0 §2.3 п. 2): голый срез
    # строк анкеты не несёт — судить происхождение нечем, и это
    # ОШИБКА КОНТРАКТА, а не молчаливое доверие признаку (именно
    # оно давало выдуманному значению фиктивное основание).
    with pytest.raises(ValueError) as e:
        m.validate_fit_quotes({"rules": [r]}, CLAUSES)
    assert "questions" in str(e.value)


@pytest.mark.parametrize("unit,raw,want", [
    ("duration_minutes", "10:00", 600),
    ("duration_minutes", "600 минут", 600),
    ("duration_minutes", "10 ч 30 мин", 630),
    ("duration_minutes", "Да, требование есть. Отдых вне базы — 9 часов.", 540),
    ("count", "не более 3 смен подряд", 3),
    ("count", "3 или 4", None),
    ("time_of_day_minutes", "с 22:00", 1320),
    ("time_of_day_minutes", "с 22:00 до 06:00", None),
    ("code", "A320", "A320"),
    ("code", "A320, B738", None),
    ("code_list", "B738, A320, A320", "A320, B738"),
    ("credit_hours", "75 часов кредита", 75),
    ("hours_per_credit_hour", "1,5", 1.5),
])
def test_parse_value_by_unit(unit, raw, want):
    got = fp.parse_value(raw, unit)
    assert (got[0] if got else None) == want


def test_declined_wording():
    assert fp.is_declined("не применяется")
    assert fp.is_declined("Не применимо.")
    assert fp.is_declined("такого требования нет")
    assert not fp.is_declined("600 минут")


def test_h0b_answer_book_yields_etalon_cell():
    """Книга ответов набора Н0-B (COSMOS) и его эталон ВМП: разбор
    ответа кодом даёт ровно запись эталона у VN-023."""
    kit = Path(__file__).resolve().parents[1] / "open-bench" / "polyaris-h0b"
    if not kit.is_dir():
        pytest.skip("набора polyaris-h0b нет в срезе")
    book = json.loads((kit / "etalon" / "ANSWER-BOOK-MACHINE.json")
                      .read_text(encoding="utf-8"))
    etalon = json.loads((kit / "etalon" / "VMP-H0B.json")
                        .read_text(encoding="utf-8"))
    raw = book["exact"][QID]
    vn = next(r for r in etalon["rules"] if r["id"] == "VN-023")
    cell = vn["template_fit"]["params"]["min_rest_outstation"]
    value, quote = fp.parse_value(raw, "duration_minutes")
    assert {"value": value, "quote": quote, "answer": QID} == cell
