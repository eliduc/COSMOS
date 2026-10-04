# -*- coding: utf-8 -*-
"""Два замка четырёх принципов ВМП (владелец 2026-09-02, «ок,
исправляй»): (1) покрытие клауз — явный вердикт аудитора по каждому
якорю задания полноты, молчание не вердикт, без вердикта нет ни
«успеха» цикла, ни пакета; (2) инвариант цикла — заплата не уменьшает
информацию посаженной строки без основания в её клаузе, принятый
«выбор чтения» становится вопросом анкеты, не правкой."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import coverage as cov, exchange as ex, model as m

REPO = Path(__file__).resolve().parent.parent
ETALON = REPO / "open-bench" / "polyaris-h0" / "etalon" / "VMP-H0.json"


def _h0():
    return json.loads(ETALON.read_text(encoding="utf-8"))


def _clause_texts(doc: dict) -> dict:
    """Тексты клауз = формулировки строк эталона (цитаты посадок —
    фрагменты формулировок по построению эталона)."""
    out = {}
    for r in doc["rules"]:
        for s in r["sources"]:
            out[(s["sid"], s["anchor"])] = r["statement"]
    for o in doc["objectives"]:
        for s in o["sources"]:
            out[(s["sid"], s["anchor"])] = o["statement"]
    return out


def _remark(rid, target, klass="искажение", anchor=None, sid="S1"):
    return {"id": rid, "severity": "СЕРЬЁЗНОЕ", "defect_class": klass,
            "target": {"rule": target, "question": None},
            "source_refs": [{"sid": sid, "anchor": anchor or "абз.13"}],
            "text": "т", "evidence": "ц", "proposal": "п"}


def _answer(rid, rule, basis="основание"):
    return {"remark": rid, "verdict": "ПРИНЯТО", "basis": basis,
            "change": "правка", "patch": {"rule_id": rule["id"], "rule": rule}}


# ------------------------------------------------------------ замок цикла

def test_downgrade_of_quoted_fit_is_rejected():
    doc = _h0()
    texts = _clause_texts(doc)
    vn011 = next(r for r in doc["rules"] if r["id"] == "VN-011")
    patched = copy.deepcopy(vn011)
    patched["template_fit"]["category"] = "B"
    patched["template_fit"]["assumption"] = "потолок читается как рекомендация"
    pack = {"round": 1, "remarks": [_remark("A1-001", "VN-011")]}
    resp = {"round": 1, "answers": [_answer("A1-001", patched)]}
    new_doc, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=set(texts), clause_texts=texts)
    assert rejected and any("понижение посадки A→B" in r
                            for r in rejected[0]["reasons"])
    assert accepted == set()
    after = next(r for r in new_doc["rules"] if r["id"] == "VN-011")
    assert after["template_fit"]["category"] == "A" and after["weight"] == 15


def test_lost_param_and_family_change_are_rejected():
    doc = _h0()
    texts = _clause_texts(doc)
    vn011 = next(r for r in doc["rules"] if r["id"] == "VN-011")
    lost = copy.deepcopy(vn011)
    lost["template_fit"]["params"].pop("duty_extension_cap")
    fam = copy.deepcopy(vn011)
    fam["template_fit"]["family"] = "duty_limits"
    fam["template_fit"]["params"] = {"max_duty_duration": {
        "value": 720, "quote": "Превышение базового предела 12:00 (720 минут)"}}
    pack = {"round": 1, "remarks": [_remark("A1-001", "VN-011"),
                                    _remark("A1-002", "VN-011")]}
    resp = {"round": 1, "answers": [_answer("A1-001", lost),
                                    _answer("A1-002", fam)]}
    _nd, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=set(texts), clause_texts=texts)
    reasons = "; ".join(x for r in rejected for x in r["reasons"])
    assert "потеря параметров посадки ['duty_extension_cap']" in reasons
    assert "смена семьи 'duty_extension_penalty'→'duty_limits'" in reasons
    assert accepted == set()


def test_weight_change_needs_number_in_own_clause():
    doc = _h0()
    texts = _clause_texts(doc)
    vn011 = next(r for r in doc["rules"] if r["id"] == "VN-011")
    # 20 — числа нет в клаузе продления; 720 есть (потолок/предел).
    bad = copy.deepcopy(vn011)
    bad["weight"] = 20
    gone = copy.deepcopy(vn011)
    gone["weight"] = None
    pack = {"round": 1, "remarks": [_remark("A1-001", "VN-011"),
                                    _remark("A1-002", "VN-011")]}
    resp = {"round": 1, "answers": [_answer("A1-001", bad),
                                    _answer("A1-002", gone)]}
    _nd, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=set(texts), clause_texts=texts)
    reasons = "; ".join(x for r in rejected for x in r["reasons"])
    assert "вес 15→20: числа 20 нет в клаузах строки" in reasons
    assert "вес 15 мягкого правила снят заплатой" in reasons
    assert accepted == set()
    # Заплата, не уменьшающая информацию (уточнение названия), проходит.
    ok = copy.deepcopy(vn011)
    ok["title"] = "Продление полётной смены"
    pack2 = {"round": 1, "remarks": [_remark("A1-003", "VN-011")]}
    resp2 = {"round": 1, "answers": [_answer("A1-003", ok)]}
    nd2, acc2, rej2, _t = ex._apply_patches(
        doc, pack2, resp2, 1, valid_anchors=set(texts), clause_texts=texts)
    assert rej2 == [] and acc2 == {"VN-011"}
    assert next(r for r in nd2["rules"] if r["id"] == "VN-011")["title"] \
        == "Продление полётной смены"


def test_accepted_reading_choice_becomes_question_not_patch():
    doc = _h0()
    texts = _clause_texts(doc)
    vn010 = next(r for r in doc["rules"] if r["id"] == "VN-010")
    reread = copy.deepcopy(vn010)
    reread["statement"] = "Предел полётной смены — 13:00 (780 минут)."
    rm = _remark("A1-001", "VN-010", klass="выбор чтения", anchor="абз.10")
    rm["text"] = "предел 12:00 или 13:00 — документ допускает оба чтения"
    rm["proposal"] = "предел 13:00 с согласия экипажа"
    pack = {"round": 1, "remarks": [rm]}
    resp = {"round": 1, "answers": [_answer("A1-001", reread,
                                            basis="12:00 — базовый предел")]}
    new_doc, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=set(texts), clause_texts=texts)
    assert rejected == []
    qid = resp["answers"][0]["converted_to_question"]
    q = next(x for x in new_doc["questions"] if x["id"] == qid)
    assert q["part"] == "2" and q["rule_refs"] == ["VN-010"]
    assert [o["key"] for o in q["answer_format"]["options"]] == ["а", "б"]
    after = next(r for r in new_doc["rules"] if r["id"] == "VN-010")
    assert after["statement"] == vn010["statement"]      # чтение не выбрано
    assert after["blockers"] == [qid] and accepted == {"VN-010"}
    assert ex.check_diff_only(doc, new_doc, accepted) == []
    assert m.validate_schema(new_doc) == [] and m.validate_model(new_doc) == []
    # Исполнение арбитража («аудитор прав») в вопрос не переводится.
    nd2, _a, rej2, _t = ex._apply_patches(
        doc, pack, {"answers": [_answer("A1-001", reread)]}, 99,
        valid_anchors=set(texts), clause_texts=texts,
        convert_reading_choice=False)
    assert "converted_to_question" not in {"answers": [_answer("A1-001", reread)]}["answers"][0]
    assert rej2 == [] and next(r for r in nd2["rules"]
                                if r["id"] == "VN-010")["statement"] \
        == reread["statement"]


# --------------------------------------------------------- замок покрытия

def _kit_entries(doc: dict) -> list:
    """Комплект: клаузы правил + две «лишние» клаузы (заголовок и
    справка), которых свод не покрывает."""
    texts = _clause_texts(doc)
    by_sid: dict = {}
    for (sid, anchor), t in texts.items():
        by_sid.setdefault(sid, []).append({"anchor": anchor, "text": t})
    by_sid["S1"].insert(0, {"anchor": "абз.1", "text": "ВМП — заголовок"})
    by_sid["S1"].append({"anchor": "абз.99", "text": "Справка о документе."})
    return [{"sid": sid, "file": f"{sid}.docx", "sha256": "0" * 64,
             "clauses": cl} for sid, cl in sorted(by_sid.items())]


def test_audit_round_records_verdicts_and_reports_silence(tmp_path):
    doc = _h0()
    entries = _kit_entries(doc)
    calls = []

    def auditor(prompt: str) -> str:
        calls.append(prompt)
        # Аудитор даёт вердикт только заголовку; справка остаётся без
        # вердикта — молчание.
        body = {"remarks": [], "fit_votes": [],
                "coverage": [{"anchor": "абз.1", "verdict": "не правило",
                              "reason": "заголовок"},
                             {"anchor": "абз.500", "verdict": "не правило",
                              "reason": "вне задания"}]}
        if "абз.1]" not in prompt:
            body["coverage"] = []
        return json.dumps(body, ensure_ascii=False)

    pack, rejected = ex._audit_round(auditor, doc, entries, 1,
                                     cache_dir=tmp_path / "c")
    assert any("абз.1" in p and "абз.99" in p for p in calls)   # задание
    assert pack["coverage"] == [{"sid": "S1", "anchor": "абз.1",
                                 "verdict": "не правило",
                                 "reason": "заголовок", "round": 1}]
    assert pack["coverage_missing"] == [{"sid": "S1", "anchor": "абз.99"}]
    assert any("вне задания полноты" in r for rj in rejected
               for r in rj["reasons"])
    metrics = ex.metrics_of(pack)
    assert metrics["coverage_missing"] == 1 and metrics["weighty"] == (0, 0, 0)
    # Два чистых полных раунда с клаузой без вердикта — НЕ успех.
    hist = [dict(metrics, mode="полный"), dict(metrics, mode="полный")]
    assert ex.decide(hist, 9, {}) == "продолжать"
    clean = dict(metrics, coverage_missing=0, mode="полный")
    assert ex.decide([clean, clean], 9, {}) == "успех"
    # Вердикт прежнего раунда закрывает якорь: задание его не повторяет.
    verdicts = {("S1", "абз.1"): {"verdict": cov.NOT_RULE, "reason": "заголовок",
                                  "round": 1}}
    left = ex.uncovered_clause_anchors(doc, "S1", entries[0]["clauses"],
                                       verdicts)
    assert left == ["абз.99"]


def test_omission_remark_counts_as_verdict():
    doc = _h0()
    entries = _kit_entries(doc)

    def auditor(prompt: str) -> str:
        rems = []
        if "абз.99]" in prompt:
            rems = [{"severity": "СЕРЬЁЗНОЕ", "defect_class": "пропуск",
                     "target": {"rule": None, "question": None},
                     "source_refs": [{"sid": "S1", "anchor": "абз.99"}],
                     "text": "норма пропущена", "evidence": "Справка о документе.",
                     "proposal": "добавить правило"}]
        return json.dumps({"remarks": rems, "fit_votes": [], "coverage": [
            {"anchor": "абз.1", "verdict": "не правило", "reason": "заголовок"}]},
            ensure_ascii=False)

    pack, _rej = ex._audit_round(auditor, doc, entries, 1)
    assert pack["coverage_missing"] == []
    assert [rm["defect_class"] for rm in pack["remarks"]] == ["пропуск"]


def test_coverage_report_and_package_gate(tmp_path):
    doc = _h0()
    entries = _kit_entries(doc)
    rep = cov.report(entries, doc, {})
    # Числа выводятся из комплекта: строк эталона плюс две намеренно
    # лишние клаузы (_kit_entries). Литерал сделал бы тест хранителем
    # состава набора, который растёт (расширение H0 03.09).
    want_rows = len(doc["rules"]) + len(doc["objectives"])
    assert rep["clauses"] == want_rows + 2
    assert rep["covered_by_rows"] == want_rows
    assert [x["anchor"] for x in rep["missing"]] == ["абз.1", "абз.99"]
    verdicts = {("S1", "абз.1"): {"verdict": cov.NOT_RULE, "reason": "заголовок",
                                  "round": 1}}
    rep2 = cov.report(entries, doc, verdicts)
    assert rep2["not_rule"] == 1 and len(rep2["missing"]) == 1
    # Пакет: комплект заморозки + модель; без вердикта у справки — отказ,
    # с вердиктами — сборка и сводка покрытия в манифесте.
    from cdpm import export_task_package as etp
    run = tmp_path / "run-x"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    kit = run / "freeze"
    (kit / "clauses").mkdir(parents=True)
    (kit / "manifest.json").write_text(json.dumps({"files": [
        {"sid": e["sid"], "file": e["file"], "sha256": e["sha256"]}
        for e in entries]}), encoding="utf-8")
    for e in entries:
        (kit / "clauses" / f"{e['sid']}.json").write_text(
            json.dumps(e, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(etp.TaskPackageError) as ei:
        etp.build_package(run)
    assert ("клауз без вердикта покрытия 2 из %d" % (want_rows + 2))         in str(ei.value)
    cov.save_verdicts(run / "exchange", {
        ("S1", "абз.1"): {"verdict": cov.NOT_RULE, "reason": "заголовок", "round": 1},
        ("S1", "абз.99"): {"verdict": cov.NOT_RULE, "reason": "справка", "round": 1}})
    assert cov.load_verdicts(run / "exchange")[("S1", "абз.99")]["reason"] == "справка"
    files = etp.build_package(run)
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    # Замок содержания (аудит 2026-09-04, п. 2): сводка несёт и слой
    # величин — после сборки остаток без адресата и нормативные
    # носители без строк обязаны быть нулями (иначе сборки не было бы).
    # A12-заявления (12.09.2026): рядом с фактами покрытия манифест
    # называет их ПРЕДЕЛ — словами одного носителя, а не признаком.
    assert manifest["coverage"] == {"clauses": want_rows + 2,
                                    "covered_by_rows": want_rows,
                                    "not_rule": 2, "missing": 0,
                                    "values_without_addressee": 0,
                                    "normative_without_rows": 0,
                                    "values_by_verdict": 0,
                                    "normative_not_rule": 0,
                                    "meaning_completeness": cov.MEANING_GAP}
    assert cov.run_report(run)["missing"] == []


def test_principles_block_on_run_status(tmp_path, monkeypatch):
    """Карточка прогона несёт покрытие клауз и блок четырёх принципов
    по машинным фактам: на эталоне H0 с полным покрытием все четыре
    зелёные; клауза без вердикта гасит биекцию."""
    from fastapi.testclient import TestClient
    from cdpm import principles as pr, webui
    doc = _h0()
    entries = _kit_entries(doc)
    (tmp_path / "extract").mkdir()
    (tmp_path / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    kit = tmp_path / "freeze"
    (kit / "clauses").mkdir(parents=True)
    (kit / "manifest.json").write_text(json.dumps({"files": [
        {"sid": e["sid"], "file": e["file"], "sha256": e["sha256"]}
        for e in entries]}), encoding="utf-8")
    for e in entries:
        (kit / "clauses" / f"{e['sid']}.json").write_text(
            json.dumps(e, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "status.json").write_text('{"state": "извлечено"}',
                                          encoding="utf-8")
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    body = TestClient(webui.app).get("/api/runs/x").json()
    p = body["principles"]
    assert [p[k]["ok"] for k in ("P1", "P2", "P3")] == [True, True, True]
    # Посадок категории A ровно столько, сколько правил с посадкой в
    # эталоне; правил без посадки быть не должно (определения посадки
    # не несут и в счёт не идут).
    want_a = sum(1 for r in _h0()["rules"] if r.get("template_fit"))
    assert p["P2"]["fit"]["A"] == want_a and p["P2"]["fit"]["none"] == 0
    assert p["P4"]["ok"] is False and body["coverage"]["missing"]
    assert p["P4"]["coverage"]["missing"] == 2
    cov.save_verdicts(tmp_path / "exchange", {
        ("S1", "абз.1"): {"verdict": cov.NOT_RULE, "reason": "заголовок", "round": 1},
        ("S1", "абз.99"): {"verdict": cov.NOT_RULE, "reason": "справка", "round": 1}})
    p2 = TestClient(webui.app).get("/api/runs/x").json()["principles"]
    assert p2["P4"]["ok"] is True and p2["P4"]["rows_without_source"] == 0
    # Без комплекта заморозки покрытие судить нечем — честный None.
    assert pr.summary(doc)["P4"]["coverage"] is None
