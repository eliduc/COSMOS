# -*- coding: utf-8 -*-
"""Детерминизация ярлыков формализации (решение владельца 2026-09-02).

Живой корень — прогон №2 обратной задачи H0 (run-0007): три
расхождения с эталоном (VN-003 DATA вместо DEF, VN-081 и VN-D91 —
зоны) были ярлыками модели при верном содержании правил; ни цикл, ни
анкета их не двигали. Вид и зону правила с посадкой ставит код по
атрибутам семьи; вид DATA законен только у правил из файлов данных;
дубли кандидатов и чужие якоря судит код."""
import copy
import json
import re

from cdpm import exchange as ex, extract, freeze, model as m
from cdpm.sample import sample_model

REV = m.registry_fingerprint()
TRAITS = m.template_family_traits()


def _contract_doc():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    return doc


def _hard(doc):
    return next(r for r in doc["rules"] if r["kind"] == "HARD")


def _fit(family, params=None, quote=None, category="A"):
    fit = {"category": category, "registry_rev": REV, "family": family}
    if params:
        fit["params"] = params
    if quote:
        fit["quote"] = quote
    if category == "B":
        fit["assumption"] = "допущение"
    return fit


def test_traits_come_from_the_catalog():
    """Контракт с каталогом шаблонов (шаг 7: прежде — families_meta()
    обёртки решателя): вид и зона правила — свойства ЗАПИСИ семьи.
    Три семьи названы поимённо как поведенческие якоря, весь остальной
    ответ сверяется с живым каталогом через мост."""
    assert TRAITS["crew_composition"] == {"kind": "HARD", "zone": "BOTH"}
    assert TRAITS["calendar_windows"] == {"kind": "SOFT", "zone": "PAIRING"}
    assert TRAITS["balanced_workload"] == {"kind": "SOFT",
                                           "zone": "ROSTERING"}
    from cdpm import catalog_bridge as bridge
    traits = bridge.family_traits()
    # Каталог держится закона формы модели, поэтому сверка кода ничего
    # не срезает: вид и зона записи проходят в ярлык как есть.
    assert {s["kind"] for s in traits.values()} <= {"HARD", "SOFT"}
    assert {s["zone"] for s in traits.values()} <= set(m.ZONES)
    assert TRAITS == {f: {"kind": s["kind"], "zone": s["zone"]}
                      for f, s in traits.items()}


def test_zone_from_family_is_deterministic_and_idempotent():
    """VN-081 (памятка): модель дала PAIRING, семья crew_composition —
    BOTH; ярлык ставит код со следом, повторный вызов пуст."""
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "PAIRING"
    r["template_fit"] = _fit("crew_composition", {"crew_size": {
        "value": 2, "quote": "два пилота"}})
    changes = m.derive_rule_traits(doc, r, "тест")
    assert r["zone"] == "BOTH"
    assert any("зона PAIRING→BOTH" in c for c in changes)
    assert "ярлыки кодом" in r["note"]
    assert m.derive_rule_traits(doc, r, "тест") == []


def test_kind_from_family_both_directions():
    """Семья мягкой механики делает строку SOFT, жёсткой — HARD (вес
    снимается): вид — свойство шаблона, не чтение модели."""
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "BOTH"
    r["template_fit"] = _fit("calendar_windows",
                             quote="ночёвки в выходные")
    changes = m.derive_rule_traits(doc, r, "тест")
    assert r["kind"] == "SOFT" and r["zone"] == "PAIRING"
    assert any("вид HARD→SOFT" in c for c in changes)
    # Обратно: мягкая строка с весом на жёсткой семье.
    r2 = copy.deepcopy(r)
    r2["kind"] = "SOFT"
    r2["weight"] = 7
    r2["template_fit"] = _fit("duty_limits", {"max_duty_duration": {
        "value": 720, "quote": "12:00"}})
    m.derive_rule_traits(doc, r2, "тест")
    assert r2["kind"] == "HARD" and "weight" not in r2


def _fork_question(qid, rid, branches):
    return {"id": qid, "part": "2", "title": "Модальность",
            "text": "Пожелание или запрет?",
            "answer_format": {"type": "choice", "options": [
                {"key": k, "text": v} for k, v in branches.items()]},
            "rule_refs": [rid],
            "source_refs": [{"sid": "S1", "anchor": "a1"}]}


def test_modality_fork_on_fixed_kind_family_is_removed_by_code():
    """Ревью r54: развилка «пожелание или запрет?» у правила на семье с
    фиксированным видом беспредметна — ответ авиакомпании отменялся бы
    семьёй сразу после применения. Код снимает развилку и вопрос, вид
    — вид семьи; зона тоже от семьи."""
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "BOTH"
    r["conditional"] = {"question": "Q-2-X", "branches": {"а": "SOFT",
                                                         "б": "HARD"}}
    r["blockers"] = ["Q-2-X"]
    r["template_fit"] = _fit("calendar_windows", quote="выходные")
    doc["questions"].append(_fork_question("Q-2-X", r["id"],
                                           {"а": "SOFT", "б": "HARD"}))
    changes = m.derive_rule_traits(doc, r, "тест")
    assert r["kind"] == "SOFT" and r["zone"] == "PAIRING"
    assert "conditional" not in r and "blockers" not in r
    assert not any(q["id"] == "Q-2-X" for q in doc["questions"])
    assert any("развилка Q-2-X снята" in c for c in changes)
    assert m.derive_rule_traits(doc, r, "тест") == []


def test_applicability_fork_waits_for_airline():
    """Развилка о применимости (ветвь OUT) предметна: до ответа вид не
    трогается; валидатор её не бракует."""
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "PAIRING"
    r["conditional"] = {"question": "Q-2-Y", "branches": {"а": "HARD",
                                                         "б": "OUT"}}
    r["blockers"] = ["Q-2-Y"]
    r["template_fit"] = _fit("calendar_windows", quote="выходные")
    doc["questions"].append(_fork_question("Q-2-Y", r["id"],
                                           {"а": "HARD", "б": "OUT"}))
    m.derive_rule_traits(doc, r, "тест")
    assert r["kind"] == "HARD" and r["conditional"]
    assert not any("расходится с семьёй 'calendar_windows'" in p
                   or "развилка модальности" in p
                   for p in m.validate_template_fit(doc))


def test_validator_flags_open_modality_fork_on_fixed_family():
    doc = _contract_doc()
    r = _hard(doc)
    r["conditional"] = {"question": "Q-2-X", "branches": {"а": "SOFT",
                                                         "б": "HARD"}}
    r["blockers"] = ["Q-2-X"]
    r["template_fit"] = _fit("calendar_windows", quote="выходные")
    assert any("развилка модальности у семьи 'calendar_windows'" in p
               for p in m.validate_template_fit(doc))


def test_answer_note_when_family_overrules_resolved_fork():
    """Остаточный путь: развилка о применимости разрешена ответом в
    HARD у мягкой семьи — семья берёт верх, но ответ не подменяется
    молча: след в ответе анкеты."""
    from cdpm import answers as ans
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "PAIRING"
    r["conditional"] = {"question": "Q-2-Y", "branches": {"а": "HARD",
                                                         "б": "OUT"}}
    r["blockers"] = ["Q-2-Y"]
    r["template_fit"] = _fit("calendar_windows", quote="выходные")
    doc["questions"].append(_fork_question("Q-2-Y", r["id"],
                                           {"а": "HARD", "б": "OUT"}))
    ans.apply_answers(doc, {"Q-2-Y": "а"})
    q = next(x for x in doc["questions"] if x["id"] == "Q-2-Y")
    assert r["kind"] == "SOFT"
    assert "учтён как справка" in q["answer"].get("note", "")


def test_data_only_from_data_files():
    """VN-003: «справочное определение» из документа правил — DEF;
    та же строка при источнике-файле данных остаётся DATA."""
    doc = _contract_doc()
    r = next(x for x in doc["rules"] if x["kind"] == "DATA")
    sid = r["sources"][0]["sid"]
    changes = m.derive_rule_traits(doc, r, "тест")
    assert r["kind"] == "DEF"
    assert any("DATA→DEF" in c for c in changes)
    doc2 = _contract_doc()
    doc2["meta"]["data_sids"] = [sid]
    r2 = next(x for x in doc2["rules"] if x["kind"] == "DATA")
    assert m.derive_rule_traits(doc2, r2, "тест") == []
    assert r2["kind"] == "DATA"
    assert m.validate_schema(doc2) == []


def test_inert_without_fit_contract():
    """Прежние модели (без meta.fit_contract) не затрагиваются."""
    doc = sample_model()
    r = _hard(doc)
    r["zone"] = "PAIRING"
    r["template_fit"] = _fit("crew_composition", {"crew_size": {
        "value": 2, "quote": "два"}})
    assert m.align_fit_with_kind(doc, r, "тест") == []
    assert r["zone"] == "PAIRING"
    assert m.derive_all_rule_traits(doc, "тест") == []


def test_validator_flags_labels_that_bypassed_code():
    """Путь, забывший код ярлыков, красен: зона/вид против семьи и DATA
    из документа — нарушения контракта посадки."""
    doc = _contract_doc()
    r = _hard(doc)
    r["zone"] = "PAIRING"
    r["template_fit"] = _fit("crew_composition", {"crew_size": {
        "value": 2, "quote": "два"}})
    problems = m.validate_template_fit(doc)
    assert any("расходится с семьёй 'crew_composition' (BOTH)" in p
               for p in problems)
    assert any("вид DATA у правила из документа правил" in p
               for p in problems)
    m.derive_all_rule_traits(doc, "тест")
    problems = m.validate_template_fit(doc)
    assert not any("расходится с семьёй" in p for p in problems)
    assert not any("вид DATA" in p for p in problems)


def _mini():
    return {
        "meta": {"customer": "т", "mode": 1, "cdpm_version": "t",
                 "fit_contract": True},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-S1-a1", "title": "предел", "statement": "смена ≤ 12:00",
             "zone": "PAIRING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "a1"}],
             "template_fit": _fit("duty_limits", {"max_duty_duration": {
                 "value": 720, "quote": "12:00"}})},
            {"id": "R-S1-a2", "title": "стыковка", "statement": "sit ≥ 45",
             "zone": "PAIRING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "a2"}],
             "template_fit": _fit("connection_windows", {"min_turnaround": {
                 "value": 45, "quote": "45"}})},
        ],
        "questions": [],
        "objectives": [{"id": "O-S1-a3", "title": "ц", "statement": "мин",
                        "zone": "PAIRING", "direction": "minimize",
                        "sources": [{"sid": "S1", "anchor": "a3"}]}],
    }


def test_fit_signature_and_duplicate_rule():
    doc = _mini()
    twin = copy.deepcopy(doc["rules"][0])
    twin["id"] = "R-S1-a1-2"
    twin["template_fit"]["params"]["max_duty_duration"]["value"] = "12:00"
    assert m.fit_signature(twin) == m.fit_signature(doc["rules"][0])
    assert m.find_duplicate_rule(doc, twin) == "R-S1-a1"
    # Другая семья на той же клаузе (предел и продление) — законна.
    other = copy.deepcopy(twin)
    other["template_fit"] = _fit("duty_extension_penalty", {
        "duty_extension_cap": {"value": 780, "quote": "13:00"}})
    assert m.find_duplicate_rule(doc, other) is None
    # Другое значение — не дубль; посадка C — не судится.
    other2 = copy.deepcopy(twin)
    other2["template_fit"]["params"]["max_duty_duration"]["value"] = 660
    assert m.find_duplicate_rule(doc, other2) is None
    assert m.fit_signature({"template_fit": {"category": "C",
                                             "gap": "х"}}) is None


def test_alien_anchors_against_rules_and_objectives():
    doc = _mini()
    prev = doc["rules"][0]
    new = copy.deepcopy(prev)
    new["sources"] = prev["sources"] + [{"sid": "S1", "anchor": "a2"},
                                        {"sid": "S1", "anchor": "a3"},
                                        {"sid": "S1", "anchor": "a9"}]
    assert m.alien_anchors(doc, new, prev) == [("a2", "R-S1-a2"),
                                                ("a3", "O-S1-a3")]
    assert m.alien_anchors(doc, prev, prev) == []


def test_exchange_rejects_duplicate_and_alien_anchor_patches():
    """Заплаты обмена: добавление дубля и приписывание чужой клаузы
    бракуются словами, живая модель не меняется."""
    doc = _mini()
    valid = {("S1", "a1"), ("S1", "a2"), ("S1", "a3")}
    pack = {"remarks": [{"id": "Z-1", "source_refs": [
        {"sid": "S1", "anchor": "a1"}]}, {"id": "Z-2", "source_refs": [
            {"sid": "S1", "anchor": "a1"}]}]}
    dup = copy.deepcopy(doc["rules"][0])
    dup.pop("id")
    alien = copy.deepcopy(doc["rules"][0])
    alien["sources"] = alien["sources"] + [{"sid": "S1", "anchor": "a2"}]
    resp = {"answers": [
        {"remark": "Z-1", "verdict": "ПРИНЯТО",
         "patch": {"rule_id": "+", "rule": dup}},
        {"remark": "Z-2", "verdict": "ПРИНЯТО",
         "patch": {"rule_id": "R-S1-a1", "rule": alien}}]}
    new_doc, accepted, rejected, _tokens = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    reasons = [r for x in rejected for r in x["reasons"]]
    assert any("дубль строки R-S1-a1" in r for r in reasons)
    assert any("чужой якорь 'a2'" in r and "R-S1-a2" in r
               for r in reasons)
    assert accepted == set()
    assert [r["id"] for r in new_doc["rules"]] == ["R-S1-a1", "R-S1-a2"]


def test_kind_patch_overruled_by_family_is_rejected_loudly():
    """Заплата вида HARD→SOFT у строки на жёсткой семье: код вернул бы
    HARD молча — вместо этого отказ с адресом (менять посадку)."""
    doc = _mini()
    reasons = ex._apply_kind_patch(doc, {"rule_id": "R-S1-a1",
                                         "kind": "SOFT", "weight": 5})
    assert reasons and "отменён ярлыками кода" in reasons[0]
    assert doc["rules"][0]["kind"] == "HARD"


_RULES = """1.1. Смена не более 12:00.

1.2. Состав экипажа: два пилота.
"""


def _fake_llm(prompt: str) -> str:
    if "Строки свода" in prompt:
        return json.dumps({"contradictions": []})
    anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
    cands = []
    for a in anchors:
        if a.endswith("1.1"):
            base = {"anchor": a, "title": "Предел смены",
                    "statement": "duty ≤ 12:00", "zone": "PAIRING",
                    "kind": "HARD", "question": None,
                    "template_fit": {"category": "A",
                                     "family": "duty_limits",
                                     "params": {"max_duty_duration": {
                                         "value": 720, "quote": "12:00"}},
                                     "confidence": 90}}
            cands.append(base)
            twin = copy.deepcopy(base)
            twin["title"] = "Предел смены (повтор)"
            cands.append(twin)
        elif a.endswith("1.2"):
            cands.append({"anchor": a, "title": "Состав экипажа",
                          "statement": "crew = 2", "zone": "PAIRING",
                          "kind": "HARD", "question": None,
                          "template_fit": {"category": "A",
                                           "family": "crew_composition",
                                           "params": {"crew_size": {
                                               "value": 2,
                                               "quote": "два пилота"}},
                                           "confidence": 90}})
    return json.dumps({"candidates": cands}, ensure_ascii=False)


def test_extract_drops_duplicate_candidate_and_derives_zone(tmp_path):
    """Извлечение: второй кандидат клаузы предела с той же посадкой —
    дубль (след в rejections.json); зона состава экипажа — BOTH по
    семье, хотя модель сказала PAIRING; счётчик и журнал ролью code."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES.md").write_text(_RULES, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES.md"], kit)
    out = tmp_path / "out"
    summary = extract.run_extract(kit, out, _fake_llm, "Полярис (тест)")
    doc = m.load_model(out / "model.json")
    assert summary["rules"] == 2
    assert summary["traits_derived"] >= 1
    rej = json.loads((out / "rejections.json").read_text(encoding="utf-8"))
    assert any("дубль кандидата" in x["reason"] for x in rej["rejected"])
    crew = next(r for r in doc["rules"]
                if r["template_fit"].get("family") == "crew_composition")
    assert crew["zone"] == "BOTH"
    journal = (out / "fit-journal.jsonl").read_text(encoding="utf-8")
    assert '"role": "code"' in journal
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_listing_names_slice_families_with_traits():
    text = m.template_fit_listing()
    assert "crew_composition" in text and "HARD/BOTH" in text
    assert "Ярлыки ставит код" in text


def test_kind_patch_def_to_data_from_document_is_rejected_as_empty():
    """Ревью r54: DEF→DATA у строки из документа правил — пустой
    переход (код возвращает DEF); «применённая» пустая заплата
    кормила бы аудитора тем же замечанием круг за кругом."""
    doc = _mini()
    doc["rules"].append({"id": "R-S1-a4", "title": "опр", "statement":
                         "определение", "zone": "BOTH", "kind": "DEF",
                         "sources": [{"sid": "S1", "anchor": "a4"}]})
    reasons = ex._apply_kind_patch(doc, {"rule_id": "R-S1-a4",
                                         "kind": "DATA"})
    assert reasons and "пуст под контрактом посадки" in reasons[0]
    assert doc["rules"][-1]["kind"] == "DEF"
    assert "миграция вида" not in doc["rules"][-1].get("note", "")


def test_full_patch_changing_kind_against_family_is_rejected():
    """Полная заплата строки «сделать SOFT» на жёсткой семье бракуется
    так же громко, как kind_patch, — не принимается «без эффекта»."""
    doc = _mini()
    valid = {("S1", "a1"), ("S1", "a2")}
    pack = {"remarks": [{"id": "Z-3", "source_refs": [
        {"sid": "S1", "anchor": "a1"}]}]}
    soft = copy.deepcopy(doc["rules"][0])
    soft["kind"] = "SOFT"
    soft["weight"] = 5
    resp = {"answers": [{"remark": "Z-3", "verdict": "ПРИНЯТО",
                         "patch": {"rule_id": "R-S1-a1", "rule": soft}}]}
    new_doc, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    assert accepted == set()
    assert any("вид 'SOFT' отменён ярлыками кода" in r
               for x in rejected for r in x["reasons"])
    assert new_doc["rules"][0]["kind"] == "HARD"


def test_paramless_family_signature_distinguishes_quotes():
    """Две нормы одной клаузы на беспараметрной семье (ночёвки в
    выходные / в праздники) — не дубли: подпись несёт цитату."""
    a = {"template_fit": _fit("calendar_windows",
                              quote="ночёвки в выходные")}
    b = {"template_fit": _fit("calendar_windows",
                              quote="ночёвки в праздники")}
    c = {"template_fit": _fit("calendar_windows",
                              quote="Ночёвки  в выходные")}
    assert m.fit_signature(a) != m.fit_signature(b)
    assert m.fit_signature(a) == m.fit_signature(c)


_RULES_FORK = """2.1. Смена может быть продлена с согласия экипажа до 13:00.
"""


def _fake_llm_fork(prompt: str) -> str:
    if "Строки свода" in prompt:
        return json.dumps({"contradictions": []})
    anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
    cands = []
    for a in anchors:
        cands.append({
            "anchor": a, "title": "Продление смены с согласия",
            "statement": "duty ≤ 13:00 с согласия", "zone": "PAIRING",
            "kind": "УСЛОВНЫЙ",
            "question": {"part": "2", "title": "Модальность продления",
                         "text": "Пожелание или запрет?",
                         "answer": {"type": "choice", "options": [
                             {"key": "а", "text": "пожелание"},
                             {"key": "б", "text": "запрет"}]},
                         "branches": {"а": "SOFT", "б": "HARD"}},
            "template_fit": {"category": "A",
                             "family": "duty_extension_penalty",
                             "params": {"duty_extension_cap": {
                                 "value": 780, "quote": "13:00"}},
                             "confidence": 80}})
    return json.dumps({"candidates": cands}, ensure_ascii=False)


def test_extract_does_not_ask_modality_fork_on_fixed_kind_family(tmp_path):
    """Извлечение: УСЛОВНЫЙ кандидат с посадкой на мягкую семью —
    вопрос авиакомпании не рождается, вид SOFT от семьи, след в
    примечании и журнале ролью code."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES.md").write_text(_RULES_FORK, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES.md"], kit)
    out = tmp_path / "out"
    extract.run_extract(kit, out, _fake_llm_fork, "Полярис (тест)")
    doc = m.load_model(out / "model.json")
    [r] = [x for x in doc["rules"]
           if x.get("template_fit", {}).get("family")
           == "duty_extension_penalty"]
    assert r["kind"] == "SOFT" and "conditional" not in r
    assert "развилка модальности не задана" in r["note"]
    assert not any(q["part"] == "2" and "Модальность" in q["title"]
                   for q in doc["questions"])
    journal = (out / "fit-journal.jsonl").read_text(encoding="utf-8")
    assert "развилка модальности не задана" in journal
