# -*- coding: utf-8 -*-
"""ЦФ-1: целевые функции и матрица корректности 3×2 (постановка
владельца 2026-08-21).

Охраняемое: целевая — отдельная коллекция с трассировкой, зоной и
направлением (инварианты контракта); зона без целевых получает
обязательный вопрос Q-*-GOAL; матрица режимов вложена (optimization ⊆
feasibility-soft ⊆ feasibility-hard), GOAL-вопрос режимы выполнимости
не блокирует; «отказ подтверждён» — только ПРИМЕНЁННЫЙ ответ GOAL с
фразой опции «любое допустимое» при пустых целевых."""
import json

import pytest

from cdpm import extract, freeze, model as m
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


def _base_doc():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "PAIRING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
        "questions": [], "objectives": [],
    }


def test_objective_invariants():
    doc = _base_doc()
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
                          "statement": "minimize cost",
                          "zone": "PAIRING", "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "п.9"}]}]
    assert m.validate_model(doc) == []
    doc["objectives"][0]["direction"] = "вбок"
    assert any("направление" in p for p in m.validate_model(doc))
    doc["objectives"][0]["direction"] = "minimize"
    doc["objectives"][0]["sources"] = []
    assert any("без трассировки" in p for p in m.validate_model(doc))


def test_matrix_nesting_and_goal_semantics():
    doc = _base_doc()
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    got = m.readiness_matrix(doc)
    pairing = got["pairing"]
    # GOAL-вопрос не блокирует режимы выполнимости…
    assert pairing["feasibility-hard"]["ready"] is True
    assert pairing["feasibility-soft"]["ready"] is True
    # …но optimization без целевых не готов.
    assert pairing["optimization"]["ready"] is False
    assert "не определена" in pairing["optimization"]["note"]

    # Применённый ответ GOAL при пустых целевых = отказ подтверждён.
    doc["questions"][0]["answer"] = {"status": "применён",
                                     "raw": "любое допустимое"}
    got = m.readiness_matrix(doc)
    assert got["pairing"]["optimization"]["state"] \
        == "отказ подтверждён"

    # Целевые есть и soft чист → optimization готов.
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
                          "statement": "cost", "zone": "BOTH",
                          "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    got = m.readiness_matrix(doc)
    assert got["pairing"]["optimization"]["ready"] is True

    # Блокированная жёсткая строка валит все три режима зоны.
    doc["rules"][0]["blockers"] = ["Q-3a-GOAL"]
    got = m.readiness_matrix(doc)
    assert got["pairing"]["feasibility-hard"]["ready"] is False
    assert got["pairing"]["optimization"]["ready"] is False


def test_extract_collects_objectives_and_goal_questions(tmp_path):
    """Целевая из документа собирается в коллекцию (SOFT ею не
    становится); зона без целевых получает Q-*-GOAL."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)

    def llm(prompt):
        if "Строки свода" in prompt:
            return fake_llm(prompt)
        out = json.loads(fake_llm(prompt))
        if "[п.1.1]" in prompt:
            out["objectives"] = [{
                "anchor": "п.1.1",
                "title": "Минимум стоимости пейринга",
                "statement": "minimize pay_and_credit",
                "zone": "PAIRING", "direction": "minimize"}]
        return json.dumps(out, ensure_ascii=False)

    out_dir = tmp_path / "out"
    s = extract.run_extract(kit, out_dir, llm, "Полярис (тест)")
    doc = json.loads((out_dir / "model.json").read_text("utf-8"))
    assert s["objectives"] == 1
    assert doc["objectives"][0]["id"].startswith("O-S1-")
    assert doc["objectives"][0]["direction"] == "minimize"
    by_id = {q["id"]: q for q in doc["questions"]}
    # Склейка ЦФ-1: пейринг закрыт целевой, но мягкое правило без
    # веса зоны держит единый вопрос — с секцией весов и БЕЗ секции
    # таблицы целевых; у ростеринга — секция таблицы.
    assert "Q-3a-OBJ" not in by_id and "Q-3b-OBJ" not in by_id
    q3a = by_id["Q-3a-GOAL"]
    assert "не обнаружено целевой функции" not in q3a["text"]
    assert "обязаны иметь штрафы" in q3a["text"]
    q3b = by_id["Q-3b-GOAL"]
    assert "не обнаружено целевой функции ростеринга" in q3b["text"]
    svod = (out_dir / "svod.md").read_text("utf-8")
    assert "Целевые функции" in svod
    assert "минимизировать" in svod


# ── Фиксы по состязательному ревью ЦФ-1 (2026-08-21) ──


def test_goal_of_other_task_does_not_leak_across():
    """Перекрёстная утечка GOAL: открытый Q-3b-GOAL со ссылкой на
    BOTH-строку не должен валить hard/soft-ячейки ПЕЙРИНГА."""
    doc = _base_doc()
    doc["rules"][0]["zone"] = "BOTH"
    doc["questions"] = [{
        "id": "Q-3b-GOAL", "part": "3b", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    got = m.readiness_matrix(doc)
    assert got["pairing"]["feasibility-hard"]["ready"] is True
    assert got["rostering"]["feasibility-hard"]["ready"] is True


def test_goal_does_not_block_abc_readiness_and_export():
    """Регресс из ревью: GOAL-вопрос не деградирует а/б/в и не
    закрывает экспорт — он относится только к optimization."""
    from cdpm import export_solver
    doc = _base_doc()
    doc["rules"][0]["zone"] = "BOTH"
    doc["questions"] = [
        {"id": qid, "part": part, "title": "Целевые", "text": "т",
         "answer_format": {"type": "registry"}, "rule_refs": ["R-H"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]}
        for qid, part in (("Q-3a-GOAL", "3a"), ("Q-3b-GOAL", "3b"))]
    ready = m.readiness(doc)
    assert ready["state"] == "в"
    export_solver.check_exportable(doc)   # не бросает ExportNotReady


def test_goal_answer_awaiting_worker_is_not_decline():
    """Ложный «отказ подтверждён»: таблица целевых, ждущая
    обработчика, отказом не является."""
    doc = _base_doc()
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"status": "ожидает обработчика",
                   "raw": "стоимость | минимизировать | PAIRING"}}]
    got = m.readiness_matrix(doc)
    opt = got["pairing"]["optimization"]
    assert opt["state"] != "отказ подтверждён"
    assert opt["ready"] is False

    # Применённый ответ БЕЗ фразы отказа — тоже не отказ: целевые в
    # модель не внесены, отказ авиакомпании не выдумывается.
    doc["questions"][0]["answer"] = {
        "status": "применён",
        "raw": "стоимость | минимизировать | PAIRING"}
    got = m.readiness_matrix(doc)
    opt = got["pairing"]["optimization"]
    assert opt["state"] != "отказ подтверждён"
    assert "не внесены" in opt["note"]


def test_objectives_after_applied_decline_are_flagged():
    """Целевые, появившиеся после применённого отказа, не перекрывают
    его молча — матрица требует подтверждения."""
    doc = _base_doc()
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"status": "применён",
                   "raw": "любое допустимое решение"}}]
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
                          "statement": "cost", "zone": "PAIRING",
                          "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    got = m.readiness_matrix(doc)
    assert "отказ" in got["pairing"]["optimization"]["note"]


def test_validate_model_survives_garbage_objectives():
    """Контракт типоустойчивости: мусорные типы в objectives —
    нарушения, не падение валидатора."""
    doc = _base_doc()
    doc["objectives"] = "мусор"
    assert any("не список" in p for p in m.validate_model(doc))
    doc["objectives"] = ["строка-вместо-объекта"]
    assert any("не объект" in p for p in m.validate_model(doc))
    doc["objectives"] = [{"id": "O-1", "title": "Т", "statement": 7,
                          "zone": "PAIRING", "direction": "minimize",
                          "sources": 0}]
    problems = m.validate_model(doc)
    assert any("формальной записи" in p for p in problems)
    assert any("sources" in p or "трассировки" in p for p in problems)
    m.readiness_matrix(doc)   # и матрица не падает


def test_objective_blockers_validated_and_gate_optimization():
    """blockers целевых: ссылка на несуществующий вопрос — нарушение;
    целевая под открытым вопросом не даёт готовности optimization."""
    doc = _base_doc()
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
                          "statement": "cost", "zone": "PAIRING",
                          "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "п.1"}],
                          "blockers": ["Q-НЕТ"]}]
    assert any("несуществующий вопрос" in p
               for p in m.validate_model(doc))
    doc["questions"] = [{
        "id": "Q-1-W", "part": "1", "title": "Вес", "text": "т",
        "answer_format": {"type": "value"}, "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    doc["objectives"][0]["blockers"] = ["Q-1-W"]
    got = m.readiness_matrix(doc)
    opt = got["pairing"]["optimization"]
    assert opt["ready"] is False
    assert "под открытыми вопросами" in opt["note"]
    # Ответ применён → блокировка снята, целевая снова считается.
    doc["questions"][0]["answer"] = {"status": "применён", "raw": "3"}
    doc["rules"][0]["blockers"] = []
    got = m.readiness_matrix(doc)
    assert got["pairing"]["optimization"]["objectives"] == ["O-1"]


def test_matrix_question_without_refs_blocks_hard_fail_closed():
    """Вопрос без пригодной привязки к строкам блокирует hard обеих
    задач — согласованно с readiness() (fail-closed)."""
    doc = _base_doc()
    doc["questions"] = [{
        "id": "Q-1-X", "part": "1", "title": "Х", "text": "т",
        "answer_format": {"type": "value"}, "rule_refs": [],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    got = m.readiness_matrix(doc)
    assert got["pairing"]["feasibility-hard"]["ready"] is False
    assert got["rostering"]["feasibility-hard"]["ready"] is False
    assert m.readiness(doc)["state"] == "а"


def test_add_objective_merges_duplicates_keeps_distinct():
    """Дедуп целевых: та же запись из другого чанка склеивается
    добавлением источника; другой текст с того же якоря — вторая
    целевая."""
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    o = {"anchor": "п.1", "title": "Стоимость",
         "statement": "minimize cost", "zone": "PAIRING",
         "direction": "minimize"}
    assert asm.add_objective("S1", dict(o)) is None
    assert asm.add_objective("S1", dict(o, anchor="п.2")) is None
    assert len(asm.doc["objectives"]) == 1
    assert len(asm.doc["objectives"][0]["sources"]) == 2
    assert asm.add_objective(
        "S1", dict(o, statement="minimize hotel_nights")) is None
    assert len(asm.doc["objectives"]) == 2
    # Мусорный тип — отбраковка, не падение.
    assert asm.add_objective("S1", dict(o, title=7)) is not None


def test_minizinc_objective_penalties_filtered_by_zone():
    """MiniZinc: в заготовку целевой входят штрафы только её зоны
    (+ BOTH) — каждая целевая отдельная задача своей зоны."""
    from cdpm import export_solver
    doc = _base_doc()
    doc["rules"] += [
        {"id": "R-SP", "title": "Мягкое П", "statement": "s",
         "zone": "PAIRING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]},
        {"id": "R-SR", "title": "Мягкое Р", "statement": "s",
         "zone": "ROSTERING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]},
    ]
    doc["objectives"] = [{"id": "O-P", "title": "Стоимость",
                          "statement": "cost", "zone": "PAIRING",
                          "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    text = export_solver.export_minizinc(doc)
    line = next(l for l in text.splitlines()
                if l.startswith("% solve minimize o_p"))
    assert "pen_r_sp" in line
    assert "pen_r_sr" not in line
    # Общий активный solve собирает все штрафы.
    assert "solve minimize pen_r_sp + pen_r_sr;" in text


def test_is_open_question_garbage_answer_fail_closed():
    assert m.is_open_question({"answer": "мусор"}) is True
    assert m.is_open_question({}) is True
    assert m.is_open_question(
        {"answer": {"status": "применён"}}) is False


# ── ЦФ-1 фаза Б: применение ответа GOAL (отмашка владельца) ──


def _goal_doc():
    """Модель с мягким правилом, заблокированным единым вопросом
    Q-3a-GOAL, и полученным ответом «ожидает обработчика»."""
    doc = _base_doc()
    doc["meta"]["revision"] = 2
    doc["rules"].append(
        {"id": "R-S", "title": "Ночёвки вне базы",
         "statement": "penalty(overnight)", "zone": "PAIRING",
         "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "blockers": ["Q-3a-GOAL"]})
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a",
        "title": "Целевая функция пейринга: состав и веса",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-S"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"raw": "стоимость | минимизировать; ночёвки вес 10",
                   "status": "ожидает обработчика", "revision": 2}}]
    return doc


def test_goal_answer_table_becomes_objectives_and_weights():
    from cdpm import answers as ans, render

    def llm(prompt):
        assert "Q-3a-GOAL" in prompt
        return json.dumps({
            "decline": False,
            "objectives": [{"title": "Стоимость пейрингов",
                            "statement": "minimize total_cost",
                            "zone": "PAIRING",
                            "direction": "minimize",
                            "metric": "total_pairing_cost"}],
            "weights": [{"rule": "R-S", "weight": 10,
                         "statement": "penalty(overnight) = 10"}],
        }, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got == {"worker_applied": 1, "worker_rejected": []}
    q = doc["questions"][0]
    assert q["answer"]["status"] == "применён"
    assert q["answer"]["goal_decline"] is False
    assert len(doc["objectives"]) == 1
    o = doc["objectives"][0]
    assert o["direction"] == "minimize"
    assert o["origin"] == {"question": "Q-3a-GOAL", "revision": 2}
    assert o["sources"] == [{"sid": "S1", "anchor": "п.1"}]
    rule = next(r for r in doc["rules"] if r["id"] == "R-S")
    assert rule["statement"] == "penalty(overnight) = 10"
    assert "blockers" not in rule
    assert m.validate_model(doc) == []
    # Матрица: целевая внесена, optimization готов.
    got_m = m.readiness_matrix(doc)
    assert got_m["pairing"]["optimization"]["ready"] is True
    # Свод честно печатает происхождение целевой из ответа.
    svod = render.render_svod(doc)
    assert "ответ авиакомпании на Q-3a-GOAL" in svod


def test_goal_answer_decline_sets_machine_flag():
    from cdpm import answers as ans

    def llm(prompt):
        return json.dumps({
            "decline": True, "objectives": [],
            "weights": [{"rule": "R-S", "weight": 10,
                         "statement": "penalty(overnight) = 10"}],
        }, ensure_ascii=False)

    doc = _goal_doc()
    # Формулировка отказа БЕЗ канонической фразы — признак машинный.
    doc["questions"][0]["answer"]["raw"] = \
        "оптимизация не нужна; ночёвки вес 10"
    got = ans.apply_worker_patches(doc, llm)
    assert got["worker_applied"] == 1
    assert doc["questions"][0]["answer"]["goal_decline"] is True
    assert doc["objectives"] == []
    got_m = m.readiness_matrix(doc)
    assert got_m["pairing"]["optimization"]["state"] \
        == "отказ подтверждён"


def test_goal_answer_missing_weights_reasked_not_deadlocked():
    """Вес внесён не всем заблокированным строкам — ответ ПРИМЕНЯЕТСЯ
    (целевая внесена), а невзвешенные строки перевешиваются на НОВЫЙ
    вопрос весов следующей ревизии (ревью фазы Б: вечный откат
    применённого вопроса был дедлоком — вопрос не попадал в анкету)."""
    from cdpm import answers as ans

    def llm(prompt):
        return json.dumps({"decline": False,
                           "objectives": [{
                               "title": "Стоимость",
                               "statement": "minimize cost",
                               "zone": "PAIRING",
                               "direction": "minimize",
                               "metric": "total_pairing_cost"}],
                           "weights": []}, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got == {"worker_applied": 1, "worker_rejected": []}
    assert len(doc["objectives"]) == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-S")
    assert rule["blockers"] == ["Q-3a-GOAL-W"]
    wq = next(x for x in doc["questions"]
              if x["id"] == "Q-3a-GOAL-W")
    assert m.is_open_question(wq)
    assert wq["rule_refs"] == ["R-S"]
    assert m.validate_model(doc) == []


def test_goal_answer_decline_without_weights_reasks_weights():
    """Отказ от оптимизации без весов: отказ фиксируется, веса мягких
    переспрашиваются отдельным вопросом (мягкое без штрафа не
    остаётся молча, а/к не остаётся без анкеты)."""
    from cdpm import answers as ans

    def llm(prompt):
        return json.dumps({"decline": True, "objectives": [],
                           "weights": []}, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got["worker_applied"] == 1
    assert doc["questions"][0]["answer"]["goal_decline"] is True
    assert m.readiness_matrix(doc)["pairing"]["optimization"][
        "state"] == "отказ подтверждён"
    wq = next(x for x in doc["questions"]
              if x["id"] == "Q-3a-GOAL-W")
    assert m.is_open_question(wq)
    assert m.validate_model(doc) == []


def test_goal_answer_garbage_weights_rejected_not_crash():
    """Мусор обработчика в weights — отказ с причиной, не падение
    (ревью фазы Б: не-dict элемент и нехешируемый rule роняли весь
    проход применения)."""
    from cdpm import answers as ans

    for weights in (["R-S: вес 5"],
                    [{"rule": {"x": 1}, "statement": "s"}],
                    [{"rule": ["R-S"], "statement": "s"}]):
        def llm(prompt, _w=weights):
            return json.dumps({"decline": False, "objectives": [],
                               "weights": _w}, ensure_ascii=False)
        doc = _goal_doc()
        got = ans.apply_worker_patches(doc, llm)
        assert got["worker_applied"] == 0
        assert got["worker_rejected"][0]["question"] == "Q-3a-GOAL"
        assert doc.get("objectives", []) == []


def test_goal_answer_foreign_zone_is_rejected():
    from cdpm import answers as ans

    def llm(prompt):
        return json.dumps({"decline": False,
                           "objectives": [{
                               "title": "Справедливость",
                               "statement": "maximize fairness",
                               "zone": "ROSTERING",
                               "direction": "maximize"}],
                           "weights": [{
                               "rule": "R-S",
                               "statement": "penalty = 10"}]},
                          ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got["worker_applied"] == 0
    assert "зона" in got["worker_rejected"][0]["reasons"][0]


def test_both_soft_rule_blocked_by_both_goal_questions():
    """BOTH-SOFT строка блокируется ОБОИМИ вопросами зон (ревью фазы
    Б: после 3a строка выпадала из кандидатов 3b — регресс против
    прежней привязки)."""
    doc = _base_doc()
    doc["rules"].append(
        {"id": "R-B", "title": "Мягкое обеих зон", "statement": "s",
         "zone": "BOTH", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    doc["rules"][0]["zone"] = "BOTH"
    m.ensure_goal_question(doc, "3a")
    m.ensure_goal_question(doc, "3b")
    rule = next(r for r in doc["rules"] if r["id"] == "R-B")
    assert sorted(rule["blockers"]) == ["Q-3a-GOAL", "Q-3b-GOAL"]
    q3b = next(x for x in doc["questions"] if x["id"] == "Q-3b-GOAL")
    assert "R-B" in q3b["rule_refs"]
    assert m.validate_model(doc) == []


def test_late_soft_after_applied_goal_gets_new_weights_question():
    """Поздний SOFT (условная ветка разрешилась после применения
    GOAL): применённый вопрос не переоткрывается и не блокирует —
    строка уходит в новый вопрос весов; уже взвешенная строка не
    переблокируется (ревью фазы Б: было красное «применён, но строки
    заблокированы» без пути разрешения)."""
    doc = _goal_doc()
    q = doc["questions"][0]
    q["answer"] = {"raw": "ночёвки вес 10", "status": "применён",
                   "revision": 2, "goal_decline": False}
    rule = next(r for r in doc["rules"] if r["id"] == "R-S")
    rule["statement"] = "penalty(overnight) = 10"
    # P-N-SHD 2026-08-30: «вес внесён» = МАШИННОЕ поле weight (так
    # пишет применение ответа); членство в rule_refs применённого
    # вопроса путём к весу больше не является.
    rule["weight"] = 10
    rule.pop("blockers", None)          # вес внесён, блокировка снята
    doc["rules"].append(
        {"id": "R-LATE", "title": "Поздняя мягкая",
         "statement": "s", "zone": "PAIRING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    got = m.ensure_goal_question(doc, "3a")
    assert got == "Q-3a-GOAL-W"
    assert "blockers" not in next(r for r in doc["rules"]
                                  if r["id"] == "R-S")
    late = next(r for r in doc["rules"] if r["id"] == "R-LATE")
    assert late["blockers"] == ["Q-3a-GOAL-W"]
    assert m.validate_model(doc) == []
    # Идемпотентность и уникальность id повторного запроса.
    doc["rules"].append(
        {"id": "R-LATE2", "title": "Ещё позже", "statement": "s",
         "zone": "PAIRING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    got2 = m.ensure_goal_question(doc, "3a")
    assert got2 == "Q-3a-GOAL-W-2"
    assert m.validate_model(doc) == []


def test_matrix_legacy_decline_phrase_negated_is_not_decline():
    """Легаси-эвристика фразы отказа не срабатывает при отрицании
    («любое допустимое нас не устраивает»)."""
    doc = _base_doc()
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"status": "применён", "revision": 2,
                   "raw": "нет, любое допустимое решение нас не "
                          "устраивает"}}]
    got = m.readiness_matrix(doc)
    assert got["pairing"]["optimization"]["state"] \
        != "отказ подтверждён"


def test_ensure_goal_question_merged_builder():
    """Единый строитель: зона без целевых и с мягким без веса — один
    вопрос с обеими секциями; повторный вызов идемпотентен; зона с
    целевой и без мягких — вопроса нет."""
    doc = _base_doc()
    doc["rules"].append(
        {"id": "R-S", "title": "Мягкое", "statement": "s",
         "zone": "PAIRING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL"
    q = doc["questions"][0]
    assert "не обнаружено целевой функции" in q["text"]
    assert "обязаны иметь штрафы" in q["text"]
    assert q["rule_refs"] == ["R-S"]
    rule = doc["rules"][1]
    assert rule["blockers"] == ["Q-3a-GOAL"]
    # Идемпотентность: блокировка не дублируется.
    m.ensure_goal_question(doc, "3a")
    assert rule["blockers"] == ["Q-3a-GOAL"]
    assert len(doc["questions"]) == 1
    # Зона с целевой и без незаблокированных мягких — вопрос не нужен.
    doc2 = _base_doc()
    doc2["objectives"] = [{"id": "O-1", "title": "Т",
                           "statement": "s", "zone": "PAIRING",
                           "direction": "minimize",
                           "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    assert m.ensure_goal_question(doc2, "3a") is None
    assert doc2["questions"] == []


# ── Спецификация 1.1: машинная метрика целевой (словарь) ──


def test_metric_validation_dictionary_and_zone():
    doc = _base_doc()
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
                          "statement": "minimize cost",
                          "zone": "PAIRING", "direction": "minimize",
                          "metric": "total_pairing_cost",
                          "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    assert m.validate_model(doc) == []
    doc["objectives"][0]["metric"] = "выдумка"
    assert any("вне словаря" in p for p in m.validate_model(doc))
    doc["objectives"][0]["metric"] = "credit_hours_spread"
    assert any("неприменима" in p for p in m.validate_model(doc))


def test_add_objective_keeps_dictionary_metric_drops_foreign():
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    ok = {"anchor": "п.1", "title": "Стоимость",
          "statement": "minimize cost", "zone": "PAIRING",
          "direction": "minimize", "metric": "total_pairing_cost"}
    assert asm.add_objective("S1", dict(ok)) is None
    assert asm.doc["objectives"][0]["metric"] == "total_pairing_cost"
    bad = dict(ok, anchor="п.2", statement="minimize hotels",
               metric="своя_метрика")
    assert asm.add_objective("S1", bad) is None
    o2 = asm.doc["objectives"][1]
    assert "metric" not in o2
    assert "вне словаря" in o2["note"]
    # Неприменимая зоне — тоже опускается со следом.
    alien = dict(ok, anchor="п.3", statement="spread",
                 metric="credit_hours_spread")
    assert asm.add_objective("S1", alien) is None
    assert "metric" not in asm.doc["objectives"][2]


def test_goal_answer_metric_flows_to_model_and_export():
    from cdpm import answers as ans, export_solver

    def llm(prompt):
        assert "закрытого словаря" in prompt
        return json.dumps({
            "decline": False,
            "objectives": [{"title": "Стоимость пейрингов",
                            "statement": "minimize total_cost",
                            "zone": "PAIRING",
                            "direction": "minimize",
                            "metric": "total_pairing_cost"}],
            "weights": [{"rule": "R-S", "weight": 10,
                         "statement": "штраф за ночь: вес 10"}],
        }, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got["worker_applied"] == 1
    assert doc["objectives"][0]["metric"] == "total_pairing_cost"
    assert m.validate_model(doc) == []
    payload = json.loads(export_solver.build_objective_json(doc))
    assert payload["pairing"]["objectives"][0]["metric"] \
        == "total_pairing_cost"


def test_goal_answer_requires_machine_weight():
    """Третий урок класса «число в свободном тексте»: вес обязан
    прийти машинным полем weight, а не только в формальной записи —
    и лечь в машинное поле строки; экспорт читает его прежде
    распознавания текста."""
    from cdpm import answers as ans, export_solver

    def llm_no_weight(prompt):
        return json.dumps({"decline": False, "objectives": [],
                           "weights": [{"rule": "R-S",
                                        "statement":
                                            "штраф хитрый: X = 10"}]},
                          ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm_no_weight)
    assert got["worker_applied"] == 0
    assert "weight" in got["worker_rejected"][0]["reasons"][0]

    def llm_ok(prompt):
        return json.dumps({"decline": False, "objectives": [],
                           "weights": [{"rule": "R-S", "weight": 10,
                                        "statement":
                                            "штраф хитрый: X_w -> 10"
                                        }]}, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm_ok)
    assert got["worker_applied"] == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-S")
    assert rule["weight"] == 10
    assert m.validate_model(doc) == []
    # Экспорт берёт вес из поля даже при нечитаемой формулировке.
    payload = json.loads(export_solver.build_objective_json(doc))
    comp = payload["pairing"]["components"][0]
    assert comp["weight"] == 10


def test_worker_patch_carries_machine_weight():
    """Вес, приходящий СТРОЧНЫМ вопросом (не GOAL), тоже ложится
    машинным полем weight через обычные патчи обработчика — третий
    урок «числа в тексте» закрыт на обоих путях применения."""
    from cdpm import answers as ans, export_solver

    doc = _base_doc()
    doc["meta"]["revision"] = 2
    doc["rules"].append(
        {"id": "R-S", "title": "Ночёвки",
         "statement": "штраф за ночь вне базы",
         "zone": "PAIRING", "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "blockers": ["Q-3a-W"]})
    doc["questions"] = [{
        "id": "Q-3a-W", "part": "3a", "title": "Вес ночёвки",
        "text": "Какова величина штрафа?",
        "answer_format": {"type": "value", "note": "баллы"},
        "rule_refs": ["R-S"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"raw": "10", "status": "ожидает обработчика",
                   "revision": 2}}]

    def llm(prompt):
        assert "weight (ТОЛЬКО у kind" in prompt
        return json.dumps({"patches": [{
            "question": "Q-3a-W",
            "change": "внесён вес",
            "rules": [{"id": "R-S", "title": "Ночёвки",
                       "statement": "штраф жуткой формулой: W → 10",
                       "zone": "PAIRING", "kind": "SOFT",
                       "weight": 10,
                       "sources": [{"sid": "S1", "anchor": "п.1"}]}],
        }]}, ensure_ascii=False)

    got = ans.apply_worker_patches(doc, llm)
    assert got == {"worker_applied": 1, "worker_rejected": []}
    rule = next(r for r in doc["rules"] if r["id"] == "R-S")
    assert rule["weight"] == 10
    assert m.validate_model(doc) == []
    payload = json.loads(export_solver.build_objective_json(doc))
    assert payload["pairing"]["components"][0]["weight"] == 10


def test_goal_answer_releases_non_soft_without_weight():
    """run-0046: строка, ставшая OUT после блокировки вопросом
    целевой (конфликт источников), вес не носит — её блокировка
    снимается без веса со следом, а разбор целевой НЕ падает."""
    from cdpm import answers as ans

    def llm(prompt):
        # Модель называет вес только действующей мягкой строке;
        # OUT-строки в кандидатах быть не должно.
        assert "R-OUT" not in prompt
        return json.dumps({
            "decline": False,
            "objectives": [{"title": "Стоимость",
                            "statement": "minimize cost",
                            "zone": "PAIRING",
                            "direction": "minimize",
                            "metric": "total_pairing_cost"}],
            "weights": [{"rule": "R-S", "weight": 10,
                         "statement": "penalty(overnight) = 10"}],
        }, ensure_ascii=False)

    doc = _goal_doc()
    doc["rules"].append(
        {"id": "R-OUT", "title": "Отменённое",
         "statement": "правило отменено конфликтом", "zone": "PAIRING",
         "kind": "OUT",
         "sources": [{"sid": "S1", "anchor": "п.2"}],
         "blockers": ["Q-3a-GOAL"]})
    doc["questions"][0]["rule_refs"] = ["R-S", "R-OUT"]
    got = ans.apply_worker_patches(doc, llm)
    assert got == {"worker_applied": 1, "worker_rejected": []}, got
    r_out = next(r for r in doc["rules"] if r["id"] == "R-OUT")
    assert "blockers" not in r_out
    assert r_out.get("weight") is None
    assert "снят без веса" in r_out.get("note", "")
    assert m.validate_model(doc) == []


def test_goal_answer_objective_requires_machine_metric():
    """Живой затык run-0002 (ночь 30→31.08): целевая БЕЗ машинной
    метрики рождалась «голой», чистка осколков её снимала, а
    сквозная проверка того же прохода ставила второй добор —
    контракт дублей валил применение целиком. Метрика обязательна:
    отказ применения с причиной и словарём зоны; вопрос остаётся
    открытым (второй добор не рождается по построению)."""
    from cdpm import answers as ans

    def llm(prompt):
        return json.dumps({
            "decline": False,
            "objectives": [{"title": "Равномерность кредитных часов",
                            "statement": "минимизировать разброс",
                            "zone": "PAIRING",
                            "direction": "minimize"}],
            "weights": [],
        }, ensure_ascii=False)

    doc = _goal_doc()
    got = ans.apply_worker_patches(doc, llm)
    assert got["worker_applied"] == 0
    reasons = "; ".join(r for rj in got["worker_rejected"]
                        for r in rj.get("reasons", []))
    assert "без машинной метрики" in reasons
    assert "total_pairing_cost" in reasons, "словарь зоны в причине"
    q = next(x for x in doc["questions"] if x["id"] == "Q-3a-GOAL")
    assert q["answer"]["status"] == "ожидает обработчика", \
        "вопрос не применён — откат с причиной"
    assert doc.get("objectives") in ([], None)
