# -*- coding: utf-8 -*-
"""ИДАК H0-К — набор H0 без критерия оптимизации (поручение владельца
2026-09-04). Проверяется: (1) комплект собран правильно — правила и
якоря H0 буква в букву, целевых нет, книга закрывает только вопросы
о целевой; (2) матрица корректности читает ответ «критерия
оптимизации нет» как подтверждённый отказ и несёт сообщение о режиме
взвешенных штрафов; (3) вопрос о целевой и подсказка обработчика
называют этот вариант ответа явно."""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
H0 = REPO / "open-bench" / "polyaris-h0"
H0K = REPO / "open-bench" / "polyaris-h0k"


def _load_objectives_tests():
    spec = importlib.util.spec_from_file_location(
        "t_cdpm_objectives", Path(__file__).with_name("test_cdpm_objectives.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- комплект
def test_h0k_kit_is_h0_without_criteria():
    man = _read(H0K / "kit-manifest.json")
    assert man["strict_questions"] is False
    assert man["data_class"] == "synthetic" and man["llm_scope"] == "any"
    for rel in man["rules_files"] + man["data_files"] + [man["answer_book"]]:
        assert (H0K / rel).is_file(), rel
    assert (H0K / man["agents_layout"]).resolve().is_file()
    et = _read(REPO / man["etalon_vmp"])
    et_h0 = _read(H0 / "etalon" / "VMP-H0.json")
    assert et["objectives"] == []
    assert et["questions"] == []
    assert [r["id"] for r in et["rules"]] == [r["id"] for r in et_h0["rules"]]
    assert len(et["rules"]) == 16
    form = _read(REPO / man["etalon_form"])
    assert form["pairing"]["criteria"] == [] and form["rostering"]["criteria"] == []
    # Якоря правил — те же, что у H0 (критерии жили только в jpg).
    amap, amap_h0 = _read(H0K / "etalon" / "anchors-map.json"), _read(H0 / "etalon" / "anchors-map.json")
    for rid, anchors in amap.items():
        if not rid.startswith("_"):
            assert amap_h0[rid] == anchors, rid
    assert _read(H0K / "etalon" / "jpg-lines.json") == [
        "Часть 1. Пейринг — в) критерии оптимизации", "(нет)",
        "Часть 2. Ростеринг — в) критерии оптимизации", "(нет)"]
    # Ожидания прогона — машинные, по ним судит раннер.
    exp = man["expect"]
    # 1.17.2: истории в книге H0-К нет, а правила ростеринга (пол отдыха)
    # судятся с остатками — вопрос линии данных законен и ожидаем.
    # Каталог 0.5.0 (по слову CDPM 11.09 05:0x): вопросы об истории — по виду
    # чтения семей эталона (balanced_workload → accumulator, rest_floors →
    # boundary), порядок — как у ensure_history_questions.
    assert exp["questions"] == ["Q-3a-GOAL", "Q-3b-GOAL",
                                "Q-3b-история-accumulator", "Q-3b-история-boundary"]
    assert set(exp["allowed_modes"]["pairing"]) == {"feasibility-soft", "feasibility-hard"}
    assert exp["readiness_ladder"] == {"pairing": "feasible_soft", "rostering": "feasible_soft"}
    assert exp["optimization_state"] == "отказ подтверждён"
    assert exp["objectives"] == 0


def test_h0k_answer_book_declines_criterion_only():
    from cdpm import model as m
    book = _read(H0K / "etalon" / "ANSWER-BOOK-MACHINE.json")
    assert set(book["exact"]) == {"Q-3a-GOAL", "Q-3b-GOAL"}
    assert book["prefix"]["Q-3b-история-"] == "б"
    assert book["regex"] == []
    # Три исхода по истории (1.17.2): «б» — продолжить с нулевыми остатками.
    assert book["prefix"]["Q-3b-история-"] == "б"
    for qid, ans in book["exact"].items():
        if not qid.endswith("GOAL"):
            continue
        low = ans.lower()
        assert any(p in low for p in m._GOAL_DECLINE_PHRASES)
        assert "feasibility-soft" in low
        # Отрицания рядом нет — фраза не «нас не устраивает».
        assert "не устраив" not in low


def test_h0k_builder_is_deterministic(tmp_path):
    """Сборщик в чужой каталог даёт те же строки/якоря, что в комплекте."""
    import sys
    sys.path.insert(0, str(H0K))
    import build_kit_h0k as b
    res = b.build(tmp_path)
    assert res["rules"] == 16 and res["objectives"] == 0 and res["map"] == 16
    assert _read(tmp_path / "etalon" / "anchors-map.json") == _read(H0K / "etalon" / "anchors-map.json")
    assert _read(tmp_path / "etalon" / "jpg-lines.json") == _read(H0K / "etalon" / "jpg-lines.json")
    got = _read(tmp_path / "etalon" / "VMP-H0K.json")
    want = _read(H0K / "etalon" / "VMP-H0K.json")
    assert got["rules"] == want["rules"] and got["objectives"] == want["objectives"]


# ------------------------------------------------------------------ матрица
def _goal_doc(raw: str, goal_decline=None) -> dict:
    t = _load_objectives_tests()
    doc = t._base_doc()
    ans = {"status": "применён", "raw": raw}
    if goal_decline is not None:
        ans["goal_decline"] = goal_decline
    doc["questions"] = [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "Целевые",
        "text": "т", "answer_format": {"type": "registry"},
        "rule_refs": ["R-H"], "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": ans}]
    return doc


def test_matrix_reads_no_criterion_as_confirmed_decline():
    from cdpm import model as m
    book = _read(H0K / "etalon" / "ANSWER-BOOK-MACHINE.json")
    doc = _goal_doc(book["exact"]["Q-3a-GOAL"])
    cell = m.readiness_matrix(doc)["pairing"]
    assert cell["optimization"]["state"] == "отказ подтверждён"
    assert cell["optimization"]["ready"] is False
    assert cell["feasibility-soft"]["ready"] is True
    note = cell["optimization"]["note"]
    assert note == m._NO_CRITERION_NOTE
    assert "критерия оптимизации нет" in note
    assert "feasibility-soft" in note and "взвешенной сумме штрафов" in note
    assert "всем жёстким ограничениям" in note
    # Машинный вердикт обработчика главнее фразы — и в обе стороны.
    assert m.readiness_matrix(_goal_doc("критерия нет", True))["pairing"][
        "optimization"]["state"] == "отказ подтверждён"
    assert m.readiness_matrix(_goal_doc("критерия оптимизации нет", False))[
        "pairing"]["optimization"]["state"] != "отказ подтверждён"
    # Отрицание рядом — не отказ.
    assert m.readiness_matrix(_goal_doc(
        "нет, «критерия оптимизации нет» нас не устраивает"))[
        "pairing"]["optimization"]["state"] != "отказ подтверждён"
    # Ответ, который критерий НАЗЫВАЕТ, отказом не считается (голое
    # «критерия нет» — ложное срабатывание, ревью 04.09).
    assert m.readiness_matrix(_goal_doc(
        "Другого критерия нет, кроме стоимости — минимизировать стоимость"))[
        "pairing"]["optimization"]["state"] != "отказ подтверждён"


def test_allowed_modes_without_criterion_are_soft_and_hard():
    from cdpm import export_task_package as etp
    doc = _goal_doc("Критерия оптимизации нет.")
    modes, objectives = etp.allowed_modes(doc)
    assert modes["pairing"] == ["feasibility-soft", "feasibility-hard"]
    assert objectives["pairing"] == []


def test_readiness_ladder_carries_no_criterion_note(monkeypatch):
    """Сообщение о режиме едет в пакете: notes столбца лестницы (COSMOS
    показывает его при нажатии кнопки)."""
    from cdpm import export_task_package as etp, model as m
    monkeypatch.setattr(etp, "_clean_sections",
                        lambda cd: {"рейсы", "экипажи", "недоступности"})
    lad = etp.readiness_ladder(_goal_doc("Критерия оптимизации нет."), clean_data={})
    col = lad["columns"]["pairing"]
    assert col["state"] == "feasible_soft"
    assert col["grounds"]["criterion"]["defined"] is False
    assert m._NO_CRITERION_NOTE in col["notes"]
    # Открытый вопрос о целевой — переходное состояние: столбец решаем,
    # но заметки о режиме нет (иначе лестница старых пакетов без
    # целевой части менялась бы задним числом — ревью 04.09).
    doc_open = _goal_doc("х")
    doc_open["questions"][0].pop("answer")
    col = etp.readiness_ladder(doc_open, clean_data={})["columns"]["pairing"]
    assert col["state"] == "feasible_soft" and col["notes"] == []
    assert lad["columns"]["rostering"]["notes"] == []
    # Без данных — stop, сообщения о режиме нет (решать нечего).
    monkeypatch.setattr(etp, "_clean_sections", lambda cd: set())
    col = etp.readiness_ladder(_goal_doc("Критерия оптимизации нет."))["columns"]["pairing"]
    assert col["state"] == "stop"
    assert m._NO_CRITERION_NOTE not in col["notes"]


def test_goal_answer_declines_helper_and_no_criterion_question():
    """Подтверждённый отказ — одна правда: матрица и вопрос о машинном
    критерии пейринга (ensure_criterion_question) читают её одной
    функцией. После отказа вопрос о критерии не ставится (живой дефект
    ревью 04.09: он рождал вопрос без ответа в книге H0-К)."""
    from cdpm import model as m
    assert m.goal_answer_declines(None) is False
    assert m.goal_answer_declines({"answer": {"status": "разобран", "raw": "критерия оптимизации нет"}}) is False
    assert m.goal_answer_declines({"answer": {"status": "применён", "raw": "критерия оптимизации нет"}}) is True
    assert m.goal_answer_declines({"answer": {"status": "применён", "raw": "х", "goal_decline": True}}) is True
    assert m.goal_answer_declines({"answer": {"status": "применён", "raw": "любое допустимое", "goal_decline": False}}) is False
    assert m.goal_answer_declines({"answer": {"status": "применён", "raw": "любое допустимое нас не устраивает"}}) is False
    # Эталон H0-К + применённые отказы обеих частей → вопросов о критерии нет.
    doc = _read(H0K / "etalon" / "VMP-H0K.json")
    book = _read(H0K / "etalon" / "ANSWER-BOOK-MACHINE.json")
    doc["questions"] = [
        {"id": qid, "part": part, "title": "Целевая функция", "text": "т",
         "answer_format": {"type": "registry"}, "rule_refs": [],
         "source_refs": [{"sid": "S1", "anchor": "абз.5"}],
         "answer": {"status": "применён", "raw": book["exact"][qid], "goal_decline": True}}
        for qid, part in (("Q-3a-GOAL", "3a"), ("Q-3b-GOAL", "3b"))]
    assert m.ensure_criterion_question(doc) == []
    assert [q["id"] for q in doc["questions"]] == ["Q-3a-GOAL", "Q-3b-GOAL"]
    # Без отказа (вопрос открыт) прежнее поведение: вопрос о критерии ставится.
    doc["questions"][0].pop("answer")
    assert m.ensure_criterion_question(doc) == ["Q-3a-критерий-пейринга"]


def test_empty_section_marker_gets_code_verdict():
    """«(нет)» под заголовком раздела формы — структура, вердикт кодом;
    иначе покрытие клауз jpg H0-К зависело бы от модели."""
    from cdpm import structure as s
    lines = _read(H0K / "etalon" / "jpg-lines.json")
    entries = [{"sid": "S3", "clauses": [{"anchor": f"стр-1-абз-{i}", "text": t}
                                          for i, t in enumerate(lines, start=1)]}]
    v = s.code_verdicts(entries)
    assert all(v[("S3", f"стр-1-абз-{i}")]["verdict"] == s.NOT_RULE
               for i in range(1, len(lines) + 1))
    assert v[("S3", "стр-1-абз-2")]["reason"].startswith("пустой раздел формы")
    assert v[("S3", "стр-1-абз-1")]["reason"] == "заголовок раздела формы"


# ------------------------------------------------------ стадия answers раннера
def _runner_with_stub_panel(tmp_path, statuses: list[dict], posts: list):
    """Раннер H0 с заглушкой панели: status() отдаёт statuses по очереди
    (последний — навсегда), post() пишет в posts и отвечает 200."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "run_local_pipeline_h0", H0 / "run_local_pipeline.py")
    rlp = importlib.util.module_from_spec(spec)
    sys.modules["run_local_pipeline_h0"] = rlp
    spec.loader.exec_module(rlp)
    rn = object.__new__(rlp.Runner)
    rn.out = tmp_path
    rn.kit = H0K
    rn.manifest = _read(H0K / "kit-manifest.json")
    rn.max_rounds = 1
    rn.state = {"run": "run-0001", "stages": {}}
    rn.state_path = tmp_path / "run-state.json"
    model = _read(H0K / "etalon" / "VMP-H0K.json")
    model["questions"] = [
        {"id": qid, "part": part, "title": "Целевая функция",
         "text": "В документах не обнаружено целевой функции. Заполните таблицу…",
         "answer_format": {"type": "registry"}, "rule_refs": [],
         "source_refs": [{"sid": "S1", "anchor": "абз.5"}]}
        for qid, part in (("Q-3a-GOAL", "3a"), ("Q-3b-GOAL", "3b"))]
    run_dir = tmp_path / "runs" / "run-0001"
    (run_dir / "extract").mkdir(parents=True)
    (run_dir / "extract" / "model.json").write_text(
        json.dumps(model, ensure_ascii=False), encoding="utf-8")
    seq = list(statuses)

    def status():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def post(self, url, **kw):
            posts.append((url, kw))
            return _Resp()

    rn.client = _Client()
    rn.status = status
    rn.wait = lambda busy, timeout_s, label: status()
    rn.run_dir = lambda: run_dir
    rn.gate = lambda tag, model_path, pkg_path=None: {"verdict": "EQUIVALENT", "tag": tag}
    rn.code = {}
    return rn


def test_stage_answers_closes_on_last_round_and_judges_state(tmp_path):
    """Анкета закрыта последним разрешённым кругом — успех (не ложный
    стоп); книга подана одним файлом .xlsx на ручку ответов."""
    posts: list = []
    rn = _runner_with_stub_panel(tmp_path, [
        {"state": "извлечено", "readiness": {"vmp_open_questions": 2}},
        {"state": "ответы применены", "readiness": {"vmp_open_questions": 0}},
    ], posts)
    rn.stage_answers()
    st = rn.state["stages"]["answers_status"]
    assert st["open_after"] == 0 and st["book"] is True
    assert [r["filled"] for r in st["rounds"]] == [2]
    assert len(posts) == 1
    url, kw = posts[0]
    assert url == "/api/runs/run-0001/answers" and kw["params"] == {"scope": "any"}
    field, (name, blob, ctype) = kw["files"][0]
    assert field == "file" and name.endswith(".xlsx") and len(blob) > 0


def test_stage_answers_stops_on_failed_application(tmp_path):
    import pytest
    posts: list = []
    rn = _runner_with_stub_panel(tmp_path, [
        {"state": "извлечено", "readiness": {"vmp_open_questions": 2}},
        {"state": "отказ применения ответов", "error": "модель недоступна",
         "readiness": {"vmp_open_questions": 2}},
    ], posts)
    with pytest.raises(SystemExit, match="отказ применения ответов"):
        rn.stage_answers()
    assert len(posts) == 1
    assert rn.state["stages"]["answers_status"]["stop"] == "отказ применения ответов"


def test_stage_answers_empty_book_is_not_fatal(tmp_path):
    """Набор H0 (строгий, книга пуста): открытые вопросы — дефект,
    который судит гейт; стадия не падает и ничего не подаёт."""
    posts: list = []
    rn = _runner_with_stub_panel(tmp_path, [
        {"state": "извлечено", "readiness": {"vmp_open_questions": 1}},
    ], posts)
    # Пустая книга ответов (в книге H0 с 1.17.2 есть ответ линии данных —
    # берём заведомо пустую).
    empty = tmp_path / "empty-answer-book.json"
    empty.write_text('{"exact": {}, "prefix": {}, "regex": []}', encoding="utf-8")
    rn.manifest = dict(rn.manifest, answer_book=str(empty))
    rn.stage_answers()
    assert posts == []
    assert rn.state["stages"]["answers_status"]["book"] is False


# ------------------------------------------------------------- формулировки
def test_goal_question_and_prompt_name_no_criterion_option():
    from cdpm import answers, model as m
    text = m._GOAL_TABLE_SECTION.format(task="ростеринга")
    assert "не обнаружено целевой функции ростеринга" in text
    assert "критерия оптимизации нет" in text
    assert "feasibility-soft" in text and "взвешенных штрафов" in text
    assert "критерия оптимизации нет" in answers._PROMPT_GOAL
    assert "критерия оптимизации нет" in m._GOAL_DECLINE_PHRASES
