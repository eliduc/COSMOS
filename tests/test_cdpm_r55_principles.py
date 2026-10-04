# -*- coding: utf-8 -*-
"""Пакет r55 (критика принципов против кода, 2026-09-02): о написанном
в документе не спрашивают.

- П7: вес мягкого правила — из текста клаузы машинным полем weight с
  дословной цитатой; грязная цитата — отброшен со следом; вес у
  жёсткого вида — брак формы кандидата;
- П6/П11: машинный критерий пейринга — носителем является целевая
  (блок criterion); вопрос критерия при нём не ставится;
- П6: темы условий и конфликты параметров видят параметры посадки;
- П12: носители тем контура с applies_to применяются только к своему
  заказчику; след носителей — в сводке;
- П4: семья мягкого правила для решателя — из посадки A/B, регэксп —
  только без посадки; расхождение — нарушение;
- П3/П5: параметр ничьей семьи и недоступная база — нарушения;
- П4: патч ответов, меняющий вид против семьи, бракуется словами."""
import copy
import json
import re

import pytest

from cdpm import answers as ans, extract, freeze, model as m
from cdpm.sample import sample_model

REV = m.registry_fingerprint()


def _fit(family, params=None, quote=None):
    fit = {"category": "A", "registry_rev": REV, "family": family}
    if params:
        fit["params"] = params
    if quote:
        fit["quote"] = quote
    return fit


# ----------------------------------------------------------- извлечение

_RULES = """3.1. Штраф 10 условных единиц за каждую ночёвку вне базы приписки.

3.2. Минимум суммарной стоимости пейрингов; машинный критерий MIN_PERSON_DAYS, ставка 100 условных единиц за календарный день командировки (версия M-036/demo-1).

3.3. Предел смены 12:00.
"""


def _llm(prompt: str) -> str:
    if "Строки свода" in prompt:
        return json.dumps({"contradictions": []})
    anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
    cands, objs = [], []
    for a in anchors:
        if a.endswith("3.1"):
            cands.append({
                "anchor": a, "title": "Ночёвки вне базы",
                "statement": "Штраф за каждую ночёвку вне базы приписки.",
                "zone": "PAIRING", "kind": "SOFT", "question": None,
                "weight": {"value": 10,
                           "as_written": "Штраф 10 условных единиц"},
                "template_fit": {"category": "A",
                                 "family": "layover_penalty",
                                 "quote": "за каждую ночёвку вне базы",
                                 "confidence": 90}})
        elif a.endswith("3.2"):
            objs.append({
                "anchor": a, "title": "Стоимость пейрингов",
                "statement": "Минимум суммарной стоимости пейрингов.",
                "zone": "PAIRING", "direction": "minimize",
                "metric": "total_pairing_cost",
                "criterion": {"kind": "MIN_PERSON_DAYS",
                              "rate_per_duty": 100,
                              "version": "M-036/demo-1",
                              "effective_from": "2026-08-01",
                              "as_written": "машинный критерий "
                                            "MIN_PERSON_DAYS, ставка 100"}})
        elif a.endswith("3.3"):
            cands.append({
                "anchor": a, "title": "Предел смены",
                "statement": "duty ≤ 12:00", "zone": "PAIRING",
                "kind": "HARD", "question": None,
                "weight": {"value": 5, "as_written": "12:00"},
                "template_fit": {"category": "A", "family": "duty_limits",
                                 "params": {"max_duty_duration": {
                                     "value": 720, "quote": "12:00"}},
                                 "confidence": 90}})
    return json.dumps({"candidates": cands, "objectives": objs},
                      ensure_ascii=False)


@pytest.fixture()
def extracted(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES.md").write_text(_RULES, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES.md"], kit)
    out = tmp_path / "out"
    summary = extract.run_extract(kit, out, _llm, "Полярис (тест)")
    return m.load_model(out / "model.json"), out, summary


def test_weight_from_clause_becomes_machine_field(extracted):
    doc, out, _s = extracted
    soft = next(r for r in doc["rules"] if r["kind"] == "SOFT")
    assert soft["weight"] == 10
    # Взвешенная мягкая строка не блокируется вопросом весов.
    assert not any(q["id"].startswith("Q-3a-GOAL")
                   and soft["id"] in q.get("rule_refs", [])
                   and soft["id"] in [b for r in doc["rules"]
                                      for b in r.get("blockers", [])]
                   for q in doc["questions"])
    assert "blockers" not in soft


def test_weight_on_hard_candidate_is_rejected_by_form(extracted):
    doc, out, _s = extracted
    rej = json.loads((out / "rejections.json").read_text(encoding="utf-8"))
    assert any("вес несёт только SOFT" in x["reason"]
               for x in rej["rejected"])
    assert not any(r["title"] == "Предел смены" for r in doc["rules"])


def test_weight_with_foreign_quote_is_dropped_with_trace():
    asm = extract._Assembler("т", [{"sid": "S1", "file": "a", "sha256":
                                    "0" * 64}])
    asm.add_candidate("S1", {
        "anchor": "п.1", "title": "Ночёвки", "statement": "штраф",
        "zone": "PAIRING", "kind": "SOFT",
        "weight": {"value": 7, "as_written": "семь единиц"}},
        fit={"category": "C", "registry_rev": REV, "gap": "нет"},
        clause_text="Штраф за ночёвку вне базы.")
    r = asm.doc["rules"][0]
    assert "weight" not in r
    assert "вес 7 отброшен" in r["note"]


_GLUED_RULES = """6.1. Штраф 20 условных единиц за каждую ночёвку вне базы приписки; потолок продления смены — 13:00.

6.2. Штраф 30 условных единиц за каждую раннюю явку экипажа.
"""


def _glue_llm(prompt: str) -> str:
    """Модель, склеившая в as_written две фразы клаузы 6.1 (первые слова
    после числа сходятся, хвост взят из другого места), и она же —
    честная дословная цитата в клаузе 6.2."""
    if "Строки свода" in prompt:
        return json.dumps({"contradictions": []})
    cands = []
    for a in re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M):
        if a.endswith("6.1"):
            cands.append({
                "anchor": a, "title": "Ночёвки вне базы",
                "statement": "Штраф за каждую ночёвку вне базы приписки.",
                "zone": "PAIRING", "kind": "SOFT", "question": None,
                "weight": {"value": 20,
                           "as_written": "Штраф 20 условных единиц за "
                                         "каждую ночёвку потолок продления "
                                         "смены 13:00"}})
        elif a.endswith("6.2"):
            cands.append({
                "anchor": a, "title": "Ранние явки",
                "statement": "Штраф за каждую раннюю явку экипажа.",
                "zone": "ROSTERING", "kind": "SOFT", "question": None,
                "weight": {"value": 30,
                           "as_written": "Штраф 30 условных единиц за "
                                         "каждую раннюю явку"}})
    return json.dumps({"candidates": cands, "objectives": []},
                      ensure_ascii=False)


def test_glued_as_written_drops_the_value_with_a_named_reason(tmp_path):
    """`as_written` — ДОСЛОВНАЯ подстрока ОДНОЙ клаузы: цитата-подстрока
    проходит, склейка двух фраз клаузы — отбраковка с причиной словами
    (живая находка кругов 10.09.2026, run-0143 и run-0035 на Test:
    склеенную цитату посадка принимала, а гейт выпуска панели честно
    отказывал измерителем). Правило при этом живёт дальше — без веса:
    его спросит анкета."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES.md").write_text(_GLUED_RULES, encoding="utf-8")
    kit, out = tmp_path / "kit", tmp_path / "out"
    freeze.freeze([src / "RULES.md"], kit)
    extract.run_extract(kit, out, _glue_llm, "Полярис (тест)")
    doc = m.load_model(out / "model.json")
    glued = next(r for r in doc["rules"] if r["title"] == "Ночёвки вне базы")
    verbatim = next(r for r in doc["rules"] if r["title"] == "Ранние явки")
    # Склейка: значения нет, причина названа — и в примечании строки…
    assert "weight" not in glued and "weight_quote" not in glued
    assert "вес 20 отброшен: as_written не дословно из клаузы" in glued["note"]
    # …и в отчёте отбраковки, как остальные причины.
    rej = json.loads((out / "rejections.json").read_text(encoding="utf-8"))
    dropped = [x for x in rej["rejected"]
               if "as_written не дословно из клаузы" in x["reason"]]
    assert len(dropped) == 1
    assert dropped[0]["anchor"] == glued["sources"][0]["anchor"]
    # Дословная цитата проходит: вес и его цитата — машинные поля строки.
    assert verbatim["weight"] == 30
    assert verbatim["weight_quote"] == ("Штраф 30 условных единиц за каждую "
                                        "раннюю явку")
    # Задание извлечения само требует дословности — до отбраковки.
    assert "ЖЁСТКОЕ ПРАВИЛО ДОСЛОВНЫХ ЦИТАТ" in extract._PROMPT_CANDIDATES
    assert "склеивать две" in extract._PROMPT_CANDIDATES


def test_criterion_carried_by_objective_silences_question(extracted):
    doc, _out, _s = extracted
    obj = next(o for o in doc["objectives"] if o["zone"] == "PAIRING")
    assert obj["criterion"] == {"kind": "min_person_days",
                                "rate_per_duty": 100,
                                "version": "M-036/demo-1",
                                "effective_from": "2026-08-01",
                                "as_written": "машинный критерий "
                                              "MIN_PERSON_DAYS, ставка 100"}
    crit = m.derive_pairing_criterion(doc)
    assert crit["kind"] == "min_person_days" and crit["rate_per_duty"] == 100
    assert crit["source_objective"] == obj["id"]
    assert not any(q["id"].startswith("Q-3a-критерий")
                   for q in doc["questions"])
    assert m.ensure_criterion_question(doc) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_criterion_outside_dictionary_is_omitted_with_trace():
    block, why = m.accept_objective_criterion(
        {"kind": "MAX_PROFIT", "as_written": "x"}, "x")
    assert block is None and "вне словаря" in why
    block, why = m.accept_objective_criterion(
        {"kind": "MIN_PERSON_DAYS", "rate_per_duty": 0, "version": "v",
         "effective_from": "d", "as_written": "ставка"}, "ставка 0")
    assert block is None and "rate_per_duty" in why
    block, why = m.accept_objective_criterion(
        {"kind": "min_person_days", "as_written": "нет в клаузе"},
        "другой текст")
    assert block is None and "не из клаузы" in why


def test_weight_of_conditional_waits_in_fork_and_applies_on_soft():
    """П7: вес у условного правила ждёт в развилке и вступает, когда
    правило становится мягким — кодом (семья мягкая) или ответом."""
    asm = extract._Assembler("т", [{"sid": "S1", "file": "a", "sha256":
                                    "0" * 64}])
    fork = {"part": "2", "title": "Модальность", "text": "?",
            "answer": {"type": "choice", "options": [
                {"key": "а", "text": "пожелание"},
                {"key": "б", "text": "запрет"}]},
            "branches": {"а": "SOFT", "б": "HARD"}}
    # Посадка на мягкую семью — развилка беспредметна, вес вступает.
    asm.add_candidate("S1", {
        "anchor": "п.1", "title": "Продление", "statement": "штраф 15",
        "zone": "PAIRING", "kind": "УСЛОВНЫЙ", "question": fork,
        "weight": {"value": 15, "as_written": "штраф 15"}},
        fit=_fit("duty_extension_penalty", {"duty_extension_cap": {
            "value": 780, "quote": "13:00"}}),
        clause_text="Продление до 13:00, штраф 15.")
    r = asm.doc["rules"][0]
    assert r["kind"] == "SOFT" and r["weight"] == 15
    assert "conditional" not in r
    # Без посадки — вес ждёт в развилке; ответ «пожелание» вносит его.
    asm.add_candidate("S1", {
        "anchor": "п.2", "title": "Явки", "statement": "штраф 4",
        "zone": "ROSTERING", "kind": "УСЛОВНЫЙ", "question": fork,
        "weight": {"value": 4, "as_written": "штраф 4"}},
        fit={"category": "C", "registry_rev": REV, "gap": "нет"},
        clause_text="Ранние явки нежелательны, штраф 4.")
    r2 = asm.doc["rules"][1]
    assert r2["kind"] == "HARD" and "weight" not in r2
    assert r2["conditional"]["weight"] == 4
    doc = asm.doc
    doc["meta"].setdefault("customer", "т")
    assert m.validate_schema(doc) == []
    qid = r2["conditional"]["question"]
    ans.apply_answers(doc, {qid: "а"})
    assert r2["kind"] == "SOFT" and r2["weight"] == 4


# --------------------------------------------- параметры посадки как носитель

def test_rule_param_cells_merges_fit_and_legacy():
    r = {"template_fit": _fit("rest_floors", {"min_rest_home": {
        "value": "12:00", "quote": "12:00"}}),
         "params": [{"name": "min_turnaround", "value": 45}]}
    assert m.rule_param_cells(r) == {"min_rest_home": 720,
                                     "min_turnaround": 45}


def test_conditions_topic_silent_when_fit_carries_param(monkeypatch,
                                                        tmp_path):
    carrier = tmp_path / "topics.json"
    carrier.write_text(json.dumps({
        "form_version": 1, "customer": "т",
        "topics": [{
            "key": "условия-отдых-в-базе",
            "trigger_regex": "отдых\\w*[^.]{0,40}(?:дом|в\\s+баз)",
            "title": "Отдых дома", "text": "Сколько?", "note": "минуты",
            "param_name": "min_rest_home",
            "param_unit": "duration_minutes",
            "param_relation": "min"}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(carrier))
    doc = {"meta": {"revision": 1, "customer": "т"}, "rules": [
        {"id": "R-1", "kind": "HARD", "zone": "ROSTERING",
         "title": "Отдых дома",
         "statement": "Минимальный отдых между сменами дома — 12:00.",
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "template_fit": _fit("rest_floors", {"min_rest_home": {
             "value": 720, "quote": "12:00"}})}], "questions": []}
    assert m.ensure_conditions_questions(doc) == []
    doc["rules"][0].pop("template_fit")
    assert m.ensure_conditions_questions(doc) == ["Q-3a-условия-отдых-в-базе"]


def test_param_conflict_sees_fit_params():
    doc = sample_model()
    a = next(r for r in doc["rules"] if r["kind"] == "HARD")
    a.pop("blockers", None)        # конфликт судится у незаблокированных
    a["template_fit"] = _fit("rest_floors", {"min_rest_home": {
        "value": 720, "quote": "12:00"}})
    b = copy.deepcopy(a)
    b["id"] = a["id"] + "-2"
    b.pop("template_fit")
    b["params"] = [{"name": "min_rest_home", "value": 750,
                    "unit": "duration_minutes", "as_written": "12:30",
                    "relation": "min"}]
    doc["rules"].append(b)
    created = m.ensure_param_conflict_questions(doc)
    assert created == ["Q-1-парам-min_rest_home"]


def test_contour_carrier_applies_to_gates_by_customer(monkeypatch, tmp_path):
    carrier = tmp_path / "topics.json"
    carrier.write_text(json.dumps({
        "form_version": 1, "customer": "контур BA",
        "applies_to": ["ЛИНИЯ-2"],
        "topics": [{"key": "условия-акклиматизация",
                    "trigger_regex": "акклиматизаци",
                    "title": "т", "text": "?", "note": "н"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(carrier))
    monkeypatch.setenv("CDPM_ENSURE_QUESTIONS", "")
    assert m._conditions_topics("Полярис-H0") == ()
    assert len(m._conditions_topics("линия-2")) == 1
    rep = m.contour_carriers_report("Полярис-H0")
    assert rep == [{"file": "topics.json", "customer": "контур BA",
                    "applies_to": ["ЛИНИЯ-2"], "applied": False,
                    "form_error": None, "topics": 1}]
    # Спецификации параметров тем — под тем же ограждением.
    assert m._topic_param_specs("CDPM_CONDITIONS_TOPICS", "x.json",
                                "Полярис-H0") == {}
    # applies_to строкой — форма нарушена: честный отказ словами.
    carrier.write_text(json.dumps({
        "form_version": 1, "customer": "к", "applies_to": "ЛИНИЯ-2",
        "topics": [{"key": "условия-акклиматизация",
                    "trigger_regex": "акклиматизаци",
                    "title": "т", "text": "?", "note": "н"}]},
        ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        m._conditions_topics("ЛИНИЯ-2")
    assert m.contour_carriers_report("ЛИНИЯ-2")[0]["form_error"]
    # Без applies_to носитель общий (прежнее поведение контуров).
    carrier.write_text(json.dumps({
        "form_version": 1, "customer": "к",
        "topics": [{"key": "условия-акклиматизация",
                    "trigger_regex": "акклиматизаци",
                    "title": "т", "text": "?", "note": "н"}]},
        ensure_ascii=False), encoding="utf-8")
    assert len(m._conditions_topics("кто угодно")) == 1


def test_incomplete_document_criterion_is_not_a_carrier():
    """Ревью r55: блок критерия без ставки/версии неисполним — документ
    его не несёт, ответ анкеты не перекрывается, вопрос ставится."""
    block, why = m.accept_objective_criterion(
        {"kind": "MIN_PERSON_DAYS", "version": "v1",
         "effective_from": "2026-01-01", "as_written": "критерий"},
        "критерий")
    assert block is None and "rate_per_duty" in why
    block, why = m.accept_objective_criterion(
        {"kind": "MIN_PERSON_DAYS", "rate_per_duty": 100,
         "as_written": "критерий"}, "критерий")
    assert block is None and "version" in why
    block, why = m.accept_objective_criterion(
        {"kind": "NORMALIZED_COST", "version": "v1",
         "effective_from": "2026-01-01", "as_written": "критерий"},
        "критерий")
    assert block is None and "rates" in why
    block, why = m.accept_objective_criterion(
        {"kind": "NORMALIZED_COST", "version": "v1",
         "effective_from": "2026-01-01", "as_written": "критерий",
         "rates": {"flight": 1.0, "work": 0.7}}, "критерий")
    assert why is None and block["rates"] == {"flight": 1.0, "work": 0.7}
    # Неполный блок в своде (чужой ревизии) — не носитель и нарушение.
    doc = sample_model()
    doc["objectives"] = [{"id": "O-1", "title": "ц", "statement": "с",
                          "zone": "PAIRING", "direction": "minimize",
                          "sources": [{"sid": "S1", "anchor": "П.1"}],
                          "criterion": {"kind": "min_person_days",
                                        "version": "v", "effective_from":
                                        "d", "as_written": "x"}}]
    assert m.criterion_from_objectives(doc) is None
    assert any("блок criterion неполон" in p
               for p in m.validate_template_fit(doc))
    doc["objectives"][0]["criterion"]["rate_per_duty"] = 100
    got = m.criterion_from_objectives(doc)
    assert got["rate_per_duty"] == 100 and got["rates_version"]["version"] == "v"
    assert not any("criterion" in p for p in m.validate_template_fit(doc))


_RULES_W = """4.1. Нежелательны ночёвки вне базы приписки.
"""


def _llm_fake_weight(prompt: str) -> str:
    if "Строки свода" in prompt:
        return json.dumps({"contradictions": []})
    anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
    return json.dumps({"candidates": [{
        "anchor": a, "title": "Ночёвки", "statement": "штраф за ночёвку",
        "zone": "PAIRING", "kind": "SOFT", "question": None,
        "weight": {"value": 999, "as_written": "ВЫДУМАННАЯ ЦИТАТА 999"},
        "template_fit": {"category": "C", "gap": "нет", "confidence": 50}}
        for a in anchors]}, ensure_ascii=False)


def test_extract_drops_weight_with_foreign_quote_on_live_path(tmp_path):
    """Ревью r55: гейт цитаты веса обязан работать на боевом пути
    run_extract (текст клаузы передаётся в сборку)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES.md").write_text(_RULES_W, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES.md"], kit)
    out = tmp_path / "out"
    extract.run_extract(kit, out, _llm_fake_weight, "Полярис (тест)")
    doc = m.load_model(out / "model.json")
    [r] = doc["rules"]
    assert "weight" not in r
    assert "вес 999 отброшен" in r["note"]


def test_calendar_windows_fit_without_days_is_flagged():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    soft = next(r for r in doc["rules"] if r["kind"] == "SOFT")
    soft["statement"] = "Надбавка к штрафу за ночёвку вне базы в выходные."
    soft["template_fit"] = _fit("calendar_windows", quote="в выходные")
    m.assign_soft_families(doc)
    assert soft["export_solver"] == {"family": "calendar_windows"}
    assert any("без окна дней недели" in p
               for p in m.validate_template_fit(doc))
    # Эталон без export_solver, но с днями в формулировке — чист.
    soft["statement"] = ("Надбавка к штрафу за ночёвку вне базы в "
                         "пятницу или субботу.")
    soft.pop("export_solver", None)
    assert not any("окна дней" in p for p in m.validate_template_fit(doc))


def test_worker_patch_keeping_stale_kind_is_not_blamed():
    """Ревью r55: свод до детерминизации несёт SOFT на жёсткой семье;
    патч, вид не трогавший, не бракуется за выравнивание кода."""
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    for r in doc["rules"]:
        if r["kind"] in ("HARD", "SOFT") and "template_fit" not in r:
            r["template_fit"] = {"category": "C", "registry_rev": REV,
                                 "gap": "тест"}
    soft = next(r for r in doc["rules"] if r["kind"] == "SOFT")
    soft["template_fit"] = _fit("duty_limits", {"max_duty_duration": {
        "value": 720, "quote": "12:00"}})
    soft["zone"] = "PAIRING"
    ans.apply_answers(doc, {"Q-3a-OBJ": "вес 10"})
    q = next(x for x in doc["questions"] if x["id"] == "Q-3a-OBJ")
    q["rule_refs"] = [soft["id"]]
    patched = copy.deepcopy(soft)          # вид SOFT, как в своде
    patched["weight"] = 10
    patched.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: json.dumps({"patches": [
            {"question": "Q-3a-OBJ", "change": "х", "rules": [patched]}]},
            ensure_ascii=False))
    assert not any("отменён ярлыками кода" in r
                   for x in summary["worker_rejected"] for r in x["reasons"])


# ------------------------------------------------ полная заплата обмена

def _mini_exchange_doc():
    return {
        "meta": {"customer": "т", "mode": 1, "cdpm_version": "t",
                 "fit_contract": True},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-S1-a1", "title": "определение",
             "statement": "Базы приписки: AAA, BBB.", "zone": "BOTH",
             "kind": "DEF", "sources": [{"sid": "S1", "anchor": "a1"}]},
            {"id": "R-S1-a2", "title": "продление",
             "statement": "Штраф 15 за смену с превышением.",
             "zone": "PAIRING", "kind": "SOFT", "weight": 15,
             "sources": [{"sid": "S1", "anchor": "a2"}],
             "template_fit": _fit("duty_extension_penalty", {
                 "duty_extension_cap": {"value": 780, "quote": "13:00"}})},
        ],
        "questions": [], "objectives": [],
    }


def test_exchange_full_patch_inherits_machine_fields_and_keeps_kind_law():
    """Прогон №3 H0 run-0008: полная заплата обмена стёрла вес 15 и
    перевела определение в HARD с посадкой C в обход перечня миграций.
    Теперь: поля, не повторённые заплатой, наследуются; смена вида —
    только по перечню миграций либо по семье посадки A/B."""
    from cdpm import exchange as ex
    doc = _mini_exchange_doc()
    valid = {("S1", "a1"), ("S1", "a2")}
    pack = {"remarks": [{"id": "Z-1", "source_refs": [
        {"sid": "S1", "anchor": "a2"}]}, {"id": "Z-2", "source_refs": [
            {"sid": "S1", "anchor": "a1"}]}]}
    # Заплата продления без weight/template_fit — поля наследуются.
    soft = {"id": "R-S1-a2", "title": "продление (уточнено)",
            "statement": "Штраф за смену с превышением сверх 12:00.",
            "zone": "PAIRING", "kind": "SOFT",
            "sources": [{"sid": "S1", "anchor": "a2"}]}
    # Заплата определения → HARD без посадки на жёсткую семью.
    hard = {"id": "R-S1-a1", "title": "перечень баз",
            "statement": "Базы приписки: AAA, BBB.", "zone": "BOTH",
            "kind": "HARD", "sources": [{"sid": "S1", "anchor": "a1"}]}
    resp = {"answers": [
        {"remark": "Z-1", "verdict": "ПРИНЯТО",
         "patch": {"rule_id": "R-S1-a2", "rule": soft}},
        {"remark": "Z-2", "verdict": "ПРИНЯТО",
         "patch": {"rule_id": "R-S1-a1", "rule": hard}}]}
    new_doc, accepted, rejected, _t = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    got_soft = next(r for r in new_doc["rules"] if r["id"] == "R-S1-a2")
    assert got_soft["weight"] == 15
    assert got_soft["template_fit"]["family"] == "duty_extension_penalty"
    assert "R-S1-a2" in accepted
    got_def = next(r for r in new_doc["rules"] if r["id"] == "R-S1-a1")
    assert got_def["kind"] == "DEF"
    assert any("смена вида 'DEF'→'HARD'" in r
               for x in rejected for r in x["reasons"])
    # DEF→HARD с посадкой A на жёсткую семью — законно (вид по семье).
    hard2 = dict(hard, template_fit=_fit("crew_composition", {
        "crew_size": {"value": 2, "quote": "два"}}),
                 statement="Экипаж — два пилота.")
    resp2 = {"answers": [{"remark": "Z-2", "verdict": "ПРИНЯТО",
                          "patch": {"rule_id": "R-S1-a1", "rule": hard2}}]}
    new_doc2, accepted2, rejected2, _t = ex._apply_patches(
        doc, pack, resp2, 1, valid_anchors=valid)
    assert "R-S1-a1" in accepted2, rejected2
    assert next(r for r in new_doc2["rules"]
                if r["id"] == "R-S1-a1")["kind"] == "HARD"


# --------------------------------------------------- семья мягкого правила

def test_soft_family_comes_from_fit_not_regex():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    soft = next(r for r in doc["rules"] if r["kind"] == "SOFT")
    soft["statement"] = ("Надбавка 25 условных единиц к штрафу за ночёвку "
                         "вне базы, календарный день начала которой (по "
                         "местному времени базы) — пятница или суббота.")
    soft["template_fit"] = _fit("calendar_windows",
                                quote="ночёвку вне базы в пятницу или субботу")
    notes = m.assign_soft_families(doc)
    assert soft["export_solver"] == {"family": "calendar_windows",
                                     "days": ["friday", "saturday"],
                                     "tz": "base-local"}
    assert any("(посадка)" in n for n in notes)
    # Регэксп один дал бы layover_penalty («базы», не «базов»).
    assert m.classify_soft_family(soft)["family"] == "layover_penalty"
    # Две правды — нарушение контракта.
    soft["export_solver"] = {"family": "layover_penalty"}
    assert any("семья решателя 'layover_penalty' расходится" in p
               for p in m.validate_template_fit(doc))
    m.assign_soft_families(doc)
    assert soft["export_solver"]["family"] == "calendar_windows"


def test_soft_family_extension_from_fit():
    r = {"kind": "SOFT", "statement": "Превышение предела — штраф.",
         "template_fit": _fit("duty_extension_penalty", {
             "duty_extension_cap": {"value": 780, "quote": "13:00"}})}
    assert m.soft_family_from_fit(r) == {"family": "duty_extension_penalty"}
    assert m.classify_soft_family(r) is None


# ------------------------------------------------------------- валидатор

def test_param_of_no_family_is_flagged():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    r = next(x for x in doc["rules"] if x["kind"] == "HARD")
    r["template_fit"] = _fit("duty_limits", {"crew_size": {
        "value": 2, "quote": "два"}})
    assert any("принадлежит другой семье 'crew_composition'" in p
               for p in m.validate_template_fit(doc))


def test_missing_template_base_is_fail_closed(monkeypatch):
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    monkeypatch.setattr(m, "template_families", lambda: {})
    assert any("база шаблонов недоступна" in p
               for p in m.validate_template_fit(doc))
    doc2 = sample_model()          # без контракта — молчит
    assert not any("база шаблонов недоступна" in p
                   for p in m.validate_template_fit(doc2))


# --------------------------------------------------------- путь ответов

def test_worker_patch_changing_kind_against_family_is_rejected():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    for r in doc["rules"]:
        if r["kind"] in ("HARD", "SOFT") and "template_fit" not in r:
            r["template_fit"] = {"category": "C", "registry_rev": REV,
                                 "gap": "тест"}
    hard = next(r for r in doc["rules"] if r["kind"] == "HARD")
    hard["template_fit"] = _fit("duty_limits", {"max_duty_duration": {
        "value": 720, "quote": "12:00"}})
    hard["zone"] = "PAIRING"
    ans.apply_answers(doc, {"Q-3a-OBJ": "вес 10"})
    q = next(x for x in doc["questions"] if x["id"] == "Q-3a-OBJ")
    affected = ans._affected_rules(doc, q)
    target = next((r for r in affected if r["id"] == hard["id"]), None)
    if target is None:
        # Вопрос весов не затрагивает жёсткую строку образца — привяжем.
        q["rule_refs"] = [hard["id"]]
        target = hard
    patched = copy.deepcopy(target)
    patched["kind"] = "SOFT"
    patched["weight"] = 10
    patched.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: json.dumps({"patches": [
            {"question": "Q-3a-OBJ", "change": "х", "rules": [patched]}]},
            ensure_ascii=False))
    assert summary["worker_applied"] == 0
    assert any("отменён ярлыками кода" in r
               for x in summary["worker_rejected"] for r in x["reasons"])
    assert hard["kind"] == "HARD"
