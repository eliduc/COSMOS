# -*- coding: utf-8 -*-
"""Целевой проход производства logic (ступень 3, 2026-08-24).

Охраняемое: узкий вопрос идёт ТОЛЬКО по жёстким правилам без logic и
без блокеров; приёмка «чистый или ничего» со следом; честный null —
не находка; рубеж 1 вшит в проход — молчание сверщика блокирует
правило вопросом части 2; модель и свод перезаписываются только
целиком прошедшими инварианты."""
import json

from cdpm import extract, model as m


def _doc():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "R-A", "title": "Отдых после продлённой",
             "statement": "после смены длиннее 12:00 отдых не менее "
                          "14:00",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}],
             "params": [
                 {"name": "long_duty_threshold", "value": 720,
                  "unit": "duration_minutes", "as_written": "12:00",
                  "relation": "max"},
                 {"name": "min_rest_after_long", "value": 840,
                  "unit": "duration_minutes", "as_written": "14:00",
                  "relation": "min"}]},
            {"id": "R-B", "title": "Смена", "statement": "смена не "
             "более 12:00", "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.2"}],
             "params": [{"name": "max_duty_duration", "value": 720,
                         "unit": "duration_minutes",
                         "as_written": "12:00", "relation": "max"}]},
            {"id": "R-C", "title": "Данные", "statement": "т",
             "zone": "BOTH", "kind": "DATA",
             "sources": [{"sid": "S1", "anchor": "п.3"}]},
        ],
        "questions": [], "objectives": []}


_CLEAN = {"subject": "rest",
          "source_params": ["long_duty_threshold",
                            "min_rest_after_long"],
          "condition": [
              {"predicate": "follows", "subject": "duty",
               "as_written": "после смены"},
              {"predicate": "longer_than",
               "param": "long_duty_threshold",
               "as_written": "длиннее 12:00"}],
          "demand": {"kind": "duration_within", "relation": "min",
                     "param": "min_rest_after_long",
                     "measure": "span",
                     "as_written": "отдых не менее 14:00"},
          "enforce": "required"}


def _write(tmp_path, doc):
    out = tmp_path / "extract"
    out.mkdir(parents=True, exist_ok=True)
    m.dump_model(doc, out / "model.json")
    (out / "svod.md").write_text("старый свод", encoding="utf-8")
    (out / "anketa.md").write_text("старая анкета", encoding="utf-8")
    return out


def test_pass_attaches_clean_drops_dirty_null_honest(tmp_path):
    doc = _doc()
    out = _write(tmp_path, doc)
    asked = []

    def llm(prompt):
        rid = json.loads(prompt[prompt.find("{"):
                                prompt.find("\n\nСловари")])["id"]
        asked.append(rid)
        if rid == "R-A":
            return json.dumps({"logic": _CLEAN}, ensure_ascii=False)
        return json.dumps({"logic": None})

    cache = tmp_path / "cache"
    summary = extract.produce_logic(out, llm, cache_dir=cache)
    # Правило вида DATA не спрашивается; null у R-B — не находка.
    assert sorted(asked) == ["R-A", "R-B"], asked
    assert summary["asked"] == 2
    assert summary["attached"] == 1 and summary["dropped"] == 0
    # Кэш пишется и переживает повтор без новых запросов (живой урок
    # run-0042: путь с cache_dir падал на пропавшем импорте, тесты
    # без кэша этого не видели).
    assert list(cache.glob("logic-*.json")), "кэш прохода пуст"
    asked.clear()
    summary2 = extract.produce_logic(out, llm, cache_dir=cache)
    assert asked == [], "повтор обязан идти из кэша, не в канал"
    assert summary2["asked"] == 1, "цель одна: R-B (R-A уже с logic)"
    saved = json.loads((out / "model.json")
                       .read_text(encoding="utf-8"))
    ra = next(r for r in saved["rules"] if r["id"] == "R-A")
    assert ra["logic"] == _CLEAN
    assert "R-B" not in [r["id"] for r in saved["rules"]
                         if isinstance(r.get("logic"), dict)]
    # Свод перерендерен из модели, не остался старым.
    assert "старый свод" not in (out / "svod.md") \
        .read_text(encoding="utf-8")
    assert m.validate_model(saved) == []


def test_pass_drops_dirty_with_trace(tmp_path):
    doc = _doc()
    out = _write(tmp_path, doc)
    dirty = dict(_CLEAN, subject="выдумка")

    def llm(prompt):
        return json.dumps({"logic": dirty}, ensure_ascii=False)

    summary = extract.produce_logic(out, llm)
    assert summary["attached"] == 0
    assert summary["dropped"] == 2
    saved = json.loads((out / "model.json")
                       .read_text(encoding="utf-8"))
    for r in saved["rules"]:
        assert not isinstance(r.get("logic"), dict)
        if r["kind"] == "HARD":
            assert "логика отброшена (целевой проход)" \
                in r.get("note", ""), r
    assert m.validate_model(saved) == []


def test_pass_wires_retell_silence_blocks(tmp_path):
    """Рубеж 1 вшит: сверщик молчит о правиле с logic — правило
    блокируется вопросом части 2 (fail-closed)."""
    doc = _doc()
    out = _write(tmp_path, doc)

    def llm(prompt):
        rid = json.loads(prompt[prompt.find("{"):
                                prompt.find("\n\nСловари")])["id"]
        if rid == "R-A":
            return json.dumps({"logic": _CLEAN}, ensure_ascii=False)
        return json.dumps({"logic": None})

    def verifier(prompt):
        return json.dumps({"verdicts": []})

    summary = extract.produce_logic(out, llm, verifier=verifier)
    assert summary["retell"]["blocked"] == ["R-A"]
    saved = json.loads((out / "model.json")
                       .read_text(encoding="utf-8"))
    ra = next(r for r in saved["rules"] if r["id"] == "R-A")
    assert ra.get("blockers"), "молчание сверщика не заблокировало"
    q = next(x for x in saved["questions"]
             if x["id"] == ra["blockers"][0])
    assert q["part"] == "2"
    assert m.validate_model(saved) == []


def test_pass_skips_existing_logic_and_blocked(tmp_path):
    doc = _doc()
    doc["rules"][1]["logic"] = {
        "subject": "duty",
        "source_params": ["max_duty_duration"],
        "demand": {"kind": "duration_within", "relation": "max",
                   "param": "max_duty_duration", "measure": "span",
                   "as_written": "не более 12:00"}}
    out = _write(tmp_path, doc)
    asked = []

    def llm(prompt):
        rid = json.loads(prompt[prompt.find("{"):
                                prompt.find("\n\nСловари")])["id"]
        asked.append(rid)
        return json.dumps({"logic": None})

    extract.produce_logic(out, llm)
    assert asked == ["R-A"], "правило с готовым logic переспрошено"


def test_retell_skips_human_adjudicated(tmp_path):
    """Правило, чей вопрос «свод против пересказа» уже отвечен и
    применён, рассужено человеком: повторный пересказ не переблокирует
    его тем же вопросом (иначе инвариант «применён, но заблокирован»
    красный — живой урок run-0050)."""
    doc = _doc()
    doc["rules"][1]["logic"] = {
        "subject": "duty",
        "source_params": ["max_duty_duration"],
        "demand": {"kind": "duration_within", "relation": "max",
                   "param": "max_duty_duration", "measure": "span",
                   "as_written": "не более 12:00"}}
    qid = m.stable_question_id("2", "лог-R-B")
    doc["questions"].append({
        "id": qid, "part": "2",
        "title": "Логика правила R-B: свод против пересказа",
        "text": "Машинный пересказ логики разошёлся с формулировкой "
                "свода. Какое чтение верно?",
        "answer_format": {"type": "choice", "options": [
            {"key": "а", "text": "формулировка свода"},
            {"key": "б", "text": "машинный пересказ"}]},
        "rule_refs": ["R-B"],
        "source_refs": [{"sid": "S1", "anchor": "п.2"}],
        "answer": {"status": "применён", "raw": "а", "key": "а",
                   "revision": 1}})
    out = _write(tmp_path, doc)

    def llm(prompt):
        rid = json.loads(prompt[prompt.find("{"):
                                prompt.find("\n\nСловари")])["id"]
        if rid == "R-A":
            return json.dumps({"logic": _CLEAN}, ensure_ascii=False)
        return json.dumps({"logic": None})

    def verifier(prompt):
        assert "R-B" not in prompt, "рассуженное правило переспрошено"
        return json.dumps({"verdicts": [
            {"rule": "R-A", "equivalent": True}]})

    summary = extract.produce_logic(out, llm, verifier=verifier)
    assert summary["retell"]["blocked"] == []
    assert summary["retell"]["checked"] == 1
    assert summary["retell"]["adjudicated"] == ["R-B"]
    saved = json.loads((out / "model.json")
                       .read_text(encoding="utf-8"))
    rb = next(r for r in saved["rules"] if r["id"] == "R-B")
    assert not rb.get("blockers"), "рассуженное правило переблокировано"
    assert m.validate_model(saved) == []
