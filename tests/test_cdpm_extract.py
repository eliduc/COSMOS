# -*- coding: utf-8 -*-
"""Извлечение и сведение CDPM (П-3) на подменной языковой модели.

Языковая модель в тестах — детерминированная функция с заготовленными
ответами: конвейер проверяется без сети и ключей, а его контракт — по
классам поведения: сборка стабильных идентификаторов, развилки и ветви,
автопривязка SOFT к целевой функции, противоречия частью 1 с
блокировкой обеих строк, отбраковка кандидата с чужим якорем (не
молчаливая), повторный запрос на кривом JSON и честный отказ на втором."""
import json
import re
from pathlib import Path

import pytest

from cdpm import extract, freeze, llm as llm_mod, model as m
from cdpm.gates import gate_extract

_RULES_A = """1.1. Пейринг не длиннее четырёх «дней».

1.9. Отдых на базе не менее 10 часов.

3.2. Желательно минимизировать ночёвки вне базы.

2.3. Не планировать явку ранее 08:00 после ночного рейса.
"""

_RULES_B = """2.2. Отдых на базе не менее 12 часов.

7.1. Не более 80 лётных часов в месяц.
"""

# Заготовленные кандидаты подменной модели, по якорям комплекта.
_CANNED = {
    "п.1.1": {
        "anchor": "п.1.1", "title": "Лимит длительности пейринга",
        "statement": "duration(pairing) ≤ 4 «дней»; единица не задана",
        "zone": "PAIRING", "kind": "HARD",
        "question": {
            "part": "3a", "title": "Единица «дня» лимита пейринга",
            "text": "Что считается «днём» лимита длительности пейринга?",
            "answer": {"type": "choice", "options": [
                {"key": "а", "text": "календарные сутки"},
                {"key": "б", "text": "96 часов от явки"}]},
        },
    },
    "п.1.9": {
        "anchor": "п.1.9", "title": "Минимальный отдых на базе (ОМ-А)",
        "statement": "rest_home ≥ 10 ч", "zone": "PAIRING",
        "kind": "HARD", "question": None,
    },
    "п.3.2": {
        "anchor": "п.3.2", "title": "Минимизация ночёвок вне базы",
        "statement": "minimize(nights_out_of_base)", "zone": "PAIRING",
        "kind": "SOFT", "question": None,
    },
    "п.2.3": {
        "anchor": "п.2.3", "title": "Ранние явки после ночного рейса",
        "statement": "«не планировать явку ранее 08:00» — модальность "
                     "неоднозначна",
        "zone": "ROSTERING", "kind": "УСЛОВНЫЙ",
        "question": {
            "part": "2", "title": "Модальность запрета ранних явок",
            "text": "Это пожелание или запрет?",
            "answer": {"type": "choice", "options": [
                {"key": "а", "text": "пожелание (SOFT)"},
                {"key": "б", "text": "запрет (HARD)"}]},
            "branches": {"а": "SOFT", "б": "HARD"},
        },
    },
    "п.2.2": {
        "anchor": "п.2.2", "title": "Минимальный отдых на базе "
                                    "(колдоговор)",
        "statement": "rest_home ≥ 12 ч", "zone": "PAIRING",
        "kind": "HARD", "question": None,
    },
    "п.7.1": {
        "anchor": "п.7.1", "title": "Месячный лимит лётного времени",
        "statement": "flight_time(month) ≤ 80 ч; тип окна не задан",
        "zone": "ROSTERING", "kind": "HARD",
        "question": {
            "part": "3b", "title": "Тип окна месячного лимита",
            "text": "Календарный месяц или скользящее окно 30 суток?",
            "answer": {"type": "choice", "options": [
                {"key": "а", "text": "календарный месяц"},
                {"key": "б", "text": "скользящее окно 30 суток"}]},
        },
    },
}


def fake_llm(prompt: str) -> str:
    if "Строки свода" in prompt:
        ids = re.findall(r"R-\S+", prompt)
        a = next((i for i in ids if "п-1-9" in i), None)
        b = next((i for i in ids if "п-2-2" in i), None)
        if a and b:
            return json.dumps({"contradictions": [{
                "rule_ids": [a, b],
                "title": "Минимальный отдых на базе: 10 или 12 часов",
                "text": "Какое требование о минимальном отдыхе на базе "
                        "действует?",
                "options": [{"key": "а", "text": "10 часов"},
                            {"key": "б", "text": "12 часов"}],
            }]}, ensure_ascii=False)
        return json.dumps({"contradictions": []})
    anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
    cands = [_CANNED[a] for a in anchors if a in _CANNED]
    return json.dumps({"candidates": cands}, ensure_ascii=False)


@pytest.fixture()
def kit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit_dir = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit_dir)
    return kit_dir


@pytest.fixture()
def extracted(kit, tmp_path):
    out = tmp_path / "out"
    summary = extract.run_extract(kit, out, fake_llm, "Полярис (тест)")
    return kit, out, summary


def test_end_to_end_counts_and_gate(extracted, capsys):
    kit_dir, out, s = extracted
    assert s["clauses_fed"] == 6
    assert s["rules"] == 6
    # ЦФ-1: единый вопрос зоны Q-*-GOAL (склейка 2026-08-21) —
    # целевая обязана быть извлечена или спрошена, веса мягких — тем
    # же вопросом.
    assert s["questions"] == 6
    assert s["questions_per_part"] == {"1": 1, "2": 1, "3a": 2,
                                       "3b": 2, "4": 0}
    assert s["rejected"] == 0
    rc = gate_extract.run(str(kit_dir), str(out))
    outtxt = capsys.readouterr().out
    assert rc == 0, outtxt
    assert "правил 6, вопросов 6" in outtxt
    assert "ВЕРДИКТ: результат извлечения соответствует контракту" \
        in outtxt


def test_conditional_rule_is_wired_and_blocked(extracted):
    _, out, _ = extracted
    doc = m.load_model(out / "model.json")
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-п-2-3")
    qid = rule["conditional"]["question"]
    assert rule["conditional"]["branches"] == {"а": "SOFT", "б": "HARD"}
    assert qid in rule["blockers"]
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert q["part"] == "2"


def test_contradiction_becomes_part1_and_blocks_both_rules(extracted):
    _, out, _ = extracted
    doc = m.load_model(out / "model.json")
    q = next(q for q in doc["questions"] if q["part"] == "1")
    assert set(q["rule_refs"]) == {"R-S1-п-1-9", "R-S2-п-2-2"}
    assert len(q["source_refs"]) == 2
    for rid in q["rule_refs"]:
        rule = next(r for r in doc["rules"] if r["id"] == rid)
        assert q["id"] in rule["blockers"]


def test_confirmed_contradiction_carries_machine_evidence(extracted):
    """Замок части 1 (11.09.2026): вопрос части 1 несёт машинное
    свидетельство — норму и значения строк-носителей с адресами; в
    сводке извлечения предложение сочтено подтверждённым."""
    _, out, s = extracted
    doc = m.load_model(out / "model.json")
    q = next(q for q in doc["questions"] if q["part"] == "1")
    ev = q["contradiction"]
    assert ev["norm"] == {"subject": "rest_home", "param": "rest_home"}
    assert [(v["rule_id"], v["value"], v["unit"]) for v in ev["values"]] \
        == [("R-S1-п-1-9", "10", "ч"), ("R-S2-п-2-2", "12", "ч")]
    assert s["contradictions"] == {"proposed": 1, "confirmed": 1,
                                   "downgraded": 0,
                                   "downgrade_reasons": []}
    rej = json.loads((out / "rejections.json").read_text(encoding="utf-8"))
    assert rej["contradictions"]["confirmed"][0]["question"] == q["id"]


def test_unconfirmed_contradiction_becomes_part2_and_blocks_nobody(
        kit, tmp_path):
    """Предложение модели, у которого код НЕ видит разных значений одной
    нормы, вопросом части 1 не становится: часть 2 с припиской словами,
    строки свода продолжают действовать."""
    def other_pair(prompt):
        if "Строки свода" not in prompt:
            return fake_llm(prompt)
        ids = re.findall(r"R-\S+", prompt)
        a = next((i for i in ids if "п-1-1" in i), None)
        b = next((i for i in ids if "п-1-9" in i), None)
        if not (a and b):
            return json.dumps({"contradictions": []})
        return json.dumps({"contradictions": [{
            "rule_ids": [a, b],
            "title": "Лимит пейринга против отдыха на базе",
            "text": "Какое требование действует?",
            "options": [{"key": "а", "text": "лимит пейринга"},
                        {"key": "б", "text": "отдых на базе"}],
        }]}, ensure_ascii=False)

    out = tmp_path / "out"
    s = extract.run_extract(kit, out, other_pair, "Полярис (тест)")
    assert s["questions_per_part"]["1"] == 0
    assert s["rejected"] == 0
    assert s["contradictions"]["proposed"] == 1
    assert s["contradictions"]["confirmed"] == 0
    assert s["contradictions"]["downgraded"] == 1
    assert "формальные записи" in s["contradictions"]["downgrade_reasons"][0]
    doc = m.load_model(out / "model.json")
    q = next(q for q in doc["questions"]
             if q["id"] == "Q-2-п-1-1-п-1-9")
    assert q["part"] == "2" and "contradiction" not in q
    assert ("Расхождение значений машинно не подтверждено — уточнение, "
            "не противоречие.") in q["text"]
    for rid in ("R-S1-п-1-1", "R-S1-п-1-9"):
        rule = next(r for r in doc["rules"] if r["id"] == rid)
        assert q["id"] not in (rule.get("blockers") or [])
    rej = json.loads((out / "rejections.json").read_text(encoding="utf-8"))
    assert rej["contradictions"]["downgraded"][0]["question"] == q["id"]


def test_soft_rule_is_bound_to_objective_question(extracted):
    """Склейка ЦФ-1: мягкое правило без веса блокируется ЕДИНЫМ
    вопросом зоны Q-*-GOAL (отдельного Q-*-OBJ больше нет)."""
    _, out, _ = extracted
    doc = m.load_model(out / "model.json")
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-п-3-2")
    assert rule["blockers"] == ["Q-3a-GOAL"]
    assert not any(q["id"] == "Q-3a-OBJ" for q in doc["questions"])
    q = next(q for q in doc["questions"] if q["id"] == "Q-3a-GOAL")
    assert q["part"] == "3a"
    assert q["answer_format"]["type"] == "registry"
    assert "R-S1-п-3-2" in q["rule_refs"]


def test_bracketed_anchor_is_normalized_not_rejected(kit, tmp_path):
    """Модель возвращает якорь в квадратных скобках — как он выглядел в
    списке клауз. Скобки — форматирование: кандидат принимается, а не
    отбраковывается (первый живой прогон потерял так сотни
    кандидатов)."""
    def bracketed(prompt):
        out = fake_llm(prompt)
        d = json.loads(out)
        for c in d.get("candidates", []):
            c["anchor"] = f"[{c['anchor']}]"
        return json.dumps(d, ensure_ascii=False)
    out = tmp_path / "out"
    s = extract.run_extract(kit, out, bracketed, "Полярис (тест)")
    assert s["rules"] == 6
    assert s["rejected"] == 0


def test_candidate_with_foreign_anchor_is_rejected_loudly(kit, tmp_path):
    def noisy(prompt):
        out = fake_llm(prompt)
        if "Документ S1" in prompt:
            d = json.loads(out)
            d["candidates"].append(
                {"anchor": "п.99", "title": "х", "statement": "х",
                 "zone": "PAIRING", "kind": "HARD", "question": None})
            return json.dumps(d, ensure_ascii=False)
        return out
    out = tmp_path / "out"
    s = extract.run_extract(kit, out, noisy, "Полярис (тест)")
    assert s["rules"] == 6                     # чужой кандидат не пролез
    assert s["rejected"] == 1
    rej = json.loads((out / "rejections.json")
                     .read_text(encoding="utf-8"))
    assert rej["rejected"][0]["anchor"] == "п.99"
    assert "отсутствует в комплекте" in rej["rejected"][0]["reason"]


def test_duplicate_question_text_is_merged_not_duplicated(kit, tmp_path):
    same_text = _CANNED["п.1.1"]["question"]["text"]

    def dup(prompt):
        out = fake_llm(prompt)
        if "Документ S2" in prompt:
            d = json.loads(out)
            for c in d["candidates"]:
                if c["anchor"] == "п.2.2":
                    c["question"] = {
                        "part": "3a", "title": "Дубль",
                        "text": same_text,
                        "answer": {"type": "choice", "options": [
                            {"key": "а", "text": "календарные сутки"},
                            {"key": "б", "text": "96 часов от явки"}]},
                    }
            return json.dumps(d, ensure_ascii=False)
        return out
    out = tmp_path / "out"
    s = extract.run_extract(kit, out, dup, "Полярис (тест)")
    assert s["questions"] == 6                 # дубль склеен, не добавлен (4 + 2 GOAL)
    assert s["merged_questions"] == 1
    doc = m.load_model(out / "model.json")
    q = next(q for q in doc["questions"]
             if q["id"] == "Q-3a-S1-п-1-1")
    assert "R-S2-п-2-2" in q["rule_refs"]


def test_bad_json_is_retried_once_then_refused(kit, tmp_path):
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return "это не JSON"
        return fake_llm(prompt)
    s = extract.run_extract(kit, tmp_path / "out1", flaky,
                            "Полярис (тест)")
    assert s["rules"] == 6                     # первая порча пережита

    def broken(prompt):
        return "это не JSON"
    with pytest.raises(llm_mod.LLMReplyError):
        extract.run_extract(kit, tmp_path / "out2", broken,
                            "Полярис (тест)")


def test_extract_is_deterministic(kit, tmp_path):
    extract.run_extract(kit, tmp_path / "o1", fake_llm, "Полярис (тест)")
    extract.run_extract(kit, tmp_path / "o2", fake_llm, "Полярис (тест)")
    assert (tmp_path / "o1" / "model.json").read_bytes() == \
        (tmp_path / "o2" / "model.json").read_bytes()
    assert (tmp_path / "o1" / "svod.md").read_bytes() == \
        (tmp_path / "o2" / "svod.md").read_bytes()


def test_gate_red_on_tampered_svod(extracted, capsys):
    kit_dir, out, _ = extracted
    svod = out / "svod.md"
    svod.write_text(svod.read_text(encoding="utf-8")
                    .replace("10 ч", "11 ч"), encoding="utf-8")
    rc = gate_extract.run(str(kit_dir), str(out))
    assert rc == 1
    assert "мимо модели" in capsys.readouterr().out


def test_gate_red_on_traceability_outside_kit(extracted, capsys):
    kit_dir, out, _ = extracted
    doc = m.load_model(out / "model.json")
    doc["rules"][0]["sources"][0]["anchor"] = "п.НЕТ"
    m.dump_model(doc, out / "model.json")
    (out / "svod.md").write_text(
        __import__("cdpm.render", fromlist=["render_svod"])
        .render_svod(doc), encoding="utf-8")
    (out / "anketa.md").write_text(
        __import__("cdpm.render", fromlist=["render_anketa"])
        .render_anketa(doc), encoding="utf-8")
    rc = gate_extract.run(str(kit_dir), str(out))
    assert rc == 1
    assert "не существует в комплекте заморозки" in \
        capsys.readouterr().out


def test_cli_main_with_injected_model(kit, tmp_path, capsys):
    out = tmp_path / "out"
    rc = extract.main([str(kit), "--out-dir", str(out), "--customer",
                       "Полярис (тест)"], llm_override=fake_llm)
    outtxt = capsys.readouterr().out
    assert rc == 0
    assert "клауз подано 6" in outtxt
    # Замок части 1: судьба предложений о противоречиях — в печати.
    assert ("предложений о противоречиях 1: подтверждено свидетельством "
            "1, понижено в часть 2 0") in outtxt
    assert str((out / "model.json").resolve()) in outtxt


def test_sanitize_drops_options_of_non_choice(kit, tmp_path):
    """Зачистка сборки (живой отказ «Полярис» 2026-08-20): варианты у
    вопроса не-развилки отбрасываются кодом с записью в отбраковку —
    один дефектный вопрос не сжигает извлечение целиком."""
    from cdpm.extract import _sanitize_assembly
    doc = {"questions": [
        {"id": "Q-3a-OBJ", "answer_format": {
            "type": "registry", "note": "веса",
            "options": [{"key": "а"}, {"key": "б"}]}},
        {"id": "Q-2-х", "answer_format": {
            "type": "choice",
            "options": [{"key": "а"}, {"key": "б"}]}},
    ]}
    rejected: list = []
    fixed = _sanitize_assembly(doc, rejected)
    assert fixed == 1
    assert "options" not in doc["questions"][0]["answer_format"]
    assert doc["questions"][1]["answer_format"]["options"]
    assert rejected and "нормализовано" in rejected[0]["reason"]


def test_extract_chunk_cache_prevents_refetch(kit, tmp_path):
    """Кэш чанков извлечения: повторный прогон читает ответы с диска
    (сбой поздней стадии не сжигает оплаченную работу)."""
    from test_cdpm_extract import fake_llm as _fake
    calls = {"n": 0}

    def counting(prompt):
        calls["n"] += 1
        return _fake(prompt)

    from cdpm import extract as ex_mod
    cache = tmp_path / "cache"
    out1 = tmp_path / "out1"
    s1 = ex_mod.run_extract(kit, out1, counting, "Полярис (тест)",
                            cache_dir=cache)
    first = calls["n"]
    assert first > 0 and list(cache.glob("*.json"))
    out2 = tmp_path / "out2"
    s2 = ex_mod.run_extract(kit, out2, counting, "Полярис (тест)",
                            cache_dir=cache)
    assert calls["n"] == first, "повтор обязан идти из кэша"
    assert s2["rules"] == s1["rules"]


def test_extract_progress_reports_chunks(kit, tmp_path):
    """Прогресс чанков (директива владельца 2026-08-20): вызовы идут с
    растущим done и постоянным total ≥ done."""
    from test_cdpm_extract import fake_llm as _fake
    seen: list = []
    from cdpm import extract as ex_mod
    ex_mod.run_extract(kit, tmp_path / "out", _fake, "Полярис (тест)",
                       progress=lambda st, d, t, r:
                       seen.append((st, d, t, r)))
    assert seen, "прогресс не вызывался"
    dones = [d for _s, d, _t, _r in seen]
    assert dones == sorted(dones)
    assert all(d <= t for _s, d, t, _r in seen)


# ---------------------------------------------------------------------------
# С-3/С-4 (анализ Лайта-v3): след сомнительного места и отсутствующий
# документ комплекта
# ---------------------------------------------------------------------------

def test_c3_note_flows_to_rule_svod_and_counter(kit, tmp_path):
    """Замечание к качеству источника от модели («битая ссылка LL-1»)
    доезжает до строки свода, колонки «Примечание» и счётчика сводки —
    молчаливой нормализации больше нет."""
    def llm(prompt):
        if "Строки свода" in prompt:
            return fake_llm(prompt)
        out = json.loads(fake_llm(prompt))
        for c in out["candidates"]:
            if c["anchor"] == "п.1.9":
                c["note"] = ("в источнике: «не менее 10 часов согласно "
                             "п. 9.4» — пункта 9.4 в документе нет; "
                             "прочитано: не менее 10 часов")
        return json.dumps(out, ensure_ascii=False)

    out_dir = tmp_path / "out-note"
    summary = extract.run_extract(kit, out_dir, llm, "Полярис (тест)")
    doc = json.loads((out_dir / "model.json").read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"]
                if r["sources"][0]["anchor"] == "п.1.9")
    assert "пункта 9.4 в документе нет" in rule["note"]
    assert summary["quality_notes"] == 1
    svod = (out_dir / "svod.md").read_text(encoding="utf-8")
    assert "пункта 9.4 в документе нет" in svod


def test_c3_junk_note_is_dropped_not_fatal(kit, tmp_path):
    """Мусорный note (не строка) отбрасывается зачисткой формы, не
    роняя извлечение — валидатор терпит любой JSON модели."""
    def llm(prompt):
        if "Строки свода" in prompt:
            return fake_llm(prompt)
        out = json.loads(fake_llm(prompt))
        for c in out["candidates"]:
            c["note"] = {"мусор": 1}
        return json.dumps(out, ensure_ascii=False)

    out_dir = tmp_path / "out-junk-note"
    summary = extract.run_extract(kit, out_dir, llm, "Полярис (тест)")
    doc = json.loads((out_dir / "model.json").read_text(encoding="utf-8"))
    # Мусорный note исчез; следы ПОСАДКИ (пакет 2026-09-01: фейковая
    # модель посадку не даёт — честная C со следом) законны.
    assert not any("мусор" in str(r.get("note") or "")
                   for r in doc["rules"])
    assert all("посадка" in str(r.get("note"))
               for r in doc["rules"] if r.get("note"))
    assert summary["quality_notes"] == 0


def test_c4_prompt_names_kit_and_missing_doc_mandate(kit, tmp_path):
    """Промпт кандидатов называет СОСТАВ комплекта и мандат про
    документ вне перечня — модель не может знать состав иначе."""
    seen = []

    def llm(prompt):
        seen.append(prompt)
        return fake_llm(prompt)

    extract.run_extract(kit, tmp_path / "out-kit", llm, "Полярис (тест)")
    cand_prompts = [p for p in seen if "Строки свода" not in p]
    assert cand_prompts, "ни одного промпта кандидатов"
    for p in cand_prompts:
        assert "S1 — RULES-A.md" in p and "S2 — RULES-B.md" in p
        assert "ВНЕ этого перечня" in p
        assert "предоставить отсутствующий документ" in p
        assert "note" in p


def test_c4_missing_doc_candidate_assembles_and_validates(kit, tmp_path):
    """Кандидат «клауза ссылается на отсутствующее приложение»: kind
    OUT + вопрос части 3b типа text проходит сборку и инварианты —
    путь ловушки LG-1 существует."""
    def llm(prompt):
        if "Строки свода" in prompt:
            return fake_llm(prompt)
        out = json.loads(fake_llm(prompt))
        for c in out["candidates"]:
            if c["anchor"] == "п.7.1":
                c["kind"] = "OUT"
                c["question"] = {
                    "part": "3b",
                    "title": "Предоставьте приложение 2",
                    "text": "Клауза ссылается на приложение 2, "
                            "отсутствующее в комплекте — просим "
                            "предоставить документ.",
                    "answer": {"type": "text"},
                }
        return json.dumps(out, ensure_ascii=False)

    out_dir = tmp_path / "out-missing-doc"
    extract.run_extract(kit, out_dir, llm, "Полярис (тест)")
    doc = json.loads((out_dir / "model.json").read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"]
                if r["sources"][0]["anchor"] == "п.7.1")
    assert rule["kind"] == "OUT"
    assert rule["blockers"], "вопрос о документе не привязан к строке"
    q = next(q for q in doc["questions"] if q["id"] in rule["blockers"])
    assert q["part"] == "3b"
    assert q["answer_format"]["type"] == "text"
    assert "приложение 2" in q["text"]


def test_b2_same_text_different_parts_not_merged(kit, tmp_path):
    """В2-8: одинаковый текст вопросов РАЗНЫХ частей анкеты не
    склеивается — просьба о документе из 3б не должна утонуть в 3а."""
    asm = extract._Assembler("Тест", [])
    q1 = asm.add_question("3a", "K1", "Документ", "Просим предоставить "
                          "приложение 2.", {"type": "text"}, [],
                          [{"sid": "S1", "anchor": "п.1"}])
    q2 = asm.add_question("3b", "K2", "Документ", "Просим предоставить "
                          "приложение 2.", {"type": "text"}, [],
                          [{"sid": "S1", "anchor": "п.2"}])
    assert q1 != q2
    assert asm.merged_questions == 0
    # Тот же текст в ТОЙ ЖЕ части — по-прежнему склейка.
    q3 = asm.add_question("3a", "K3", "Документ", "Просим предоставить "
                          "приложение 2.", {"type": "text"}, [],
                          [{"sid": "S1", "anchor": "п.3"}])
    assert q3 == q1
    assert asm.merged_questions == 1


def test_b2_junk_note_leaves_rejection_trace(kit, tmp_path):
    """В2-7: мусорный note не исчезает молча — след в отбраковке."""
    def llm(prompt):
        if "Строки свода" in prompt:
            return fake_llm(prompt)
        out = json.loads(fake_llm(prompt))
        for c in out["candidates"]:
            if c["anchor"] == "п.1.9":
                c["note"] = {"обёрнуто": "объектом"}
        return json.dumps(out, ensure_ascii=False)

    out_dir = tmp_path / "out-junk-trace"
    extract.run_extract(kit, out_dir, llm, "Полярис (тест)")
    rej = json.loads((out_dir / "rejections.json")
                     .read_text(encoding="utf-8"))
    reasons = [r["reason"] for r in rej["rejected"]]
    assert any("note" in r for r in reasons), reasons


def test_b2_batch_key_salted_by_prompt():
    """В2-6: подпись промпта в ключе кэша — смена шаблона или состава
    комплекта инвалидирует прогретые чанки."""
    a = extract._batch_key("S1", 0, "клаузы", salt="промпт v1")
    b = extract._batch_key("S1", 0, "клаузы", salt="промпт v2")
    c = extract._batch_key("S1", 0, "клаузы", salt="промпт v1")
    assert a != b
    assert a == c


# ---------------------------------------------------------------------------
# ПАР-1: параллельные вызовы модели с детерминированной сборкой
# ---------------------------------------------------------------------------

def test_par1_parallel_extract_is_deterministic(kit, tmp_path,
                                                monkeypatch):
    """Результат извлечения байт-в-байт одинаков при 1 и 4 потоках:
    параллельны только вызовы, сборка идёт в порядке чанков."""
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    extract.run_extract(kit, tmp_path / "seq", fake_llm,
                        "Полярис (тест)")
    monkeypatch.setenv("CDPM_PARALLEL", "4")
    extract.run_extract(kit, tmp_path / "par", fake_llm,
                        "Полярис (тест)")
    a = (tmp_path / "seq" / "model.json").read_text(encoding="utf-8")
    b = (tmp_path / "par" / "model.json").read_text(encoding="utf-8")
    assert a == b


def test_par1_parallel_map_contract():
    """parallel_map: результаты в порядке заданий, on_done в главном
    потоке, исключение задания — наружу (fail-closed)."""
    import threading
    from cdpm import llm as llm_mod
    main_thread = threading.get_ident()
    done_threads = []

    def fn(x):
        return x * 10

    got = llm_mod.parallel_map(
        fn, [3, 1, 2], workers=4,
        on_done=lambda k: done_threads.append(threading.get_ident()))
    assert got == [30, 10, 20]
    assert all(t == main_thread for t in done_threads)

    def boom(x):
        if x == 2:
            raise RuntimeError("отказ чанка")
        return x

    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="отказ чанка"):
        llm_mod.parallel_map(boom, [1, 2, 3], workers=4)
    # workers=1 — прежний последовательный путь.
    assert llm_mod.parallel_map(fn, [5], workers=1) == [50]


def test_задание_генератора_несёт_закрытый_словарь():
    """П.1 системного лечения изменчивости (владелец 31.08): задание
    обработчика-извлечения несёт ЗАКРЫТЫЙ словарь имён (единицы из
    контракта модели, исполнители из базы шаблонов) и правило «не
    ложится — пометка "нужен новый шаблон", не выдуманное имя»."""
    from cdpm import model as m
    from cdpm.extract import _PROMPT_CANDIDATES
    prompt = _PROMPT_CANDIDATES.format(
        sid="S1", file="f.md", n=1, kit="f.md",
        clauses="[п.1] клауза",
        dictionary=m.machine_dictionary_listing(),
        fit_listing=m.template_fit_listing())
    assert "ПЕРЕЧЕНЬ ШАБЛОНОВ БАЗЫ" in prompt
    assert "ЗАКРЫТЫЙ СЛОВАРЬ ИМЁН ПАРАМЕТРОВ" in prompt
    assert "long_duty_threshold" in prompt
    assert "исполнитель: duty_extension_penalty" in prompt
    assert "нужен новый шаблон" in prompt
    assert "СПРАВКА ДАННЫХ" in prompt, "виды с определениями"
