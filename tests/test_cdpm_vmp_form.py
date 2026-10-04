# -*- coding: utf-8 -*-
"""Форма ВМП (общий раздел + пейринг/ростеринг × а/б/в), решения
владельца 2026-09-02: проекция модели в форму и обратно без потерь,
схема формы, проверки композиции и места строк, рендер документа.
После аудита №1 реализации: носители scope/tier/tolerance/rate/per,
целевая зоны BOTH, посадка C из формы, пометка у определений, единица
нарушения по словарю и семье, ставка у каждого критерия, один критерий
на ярусе, открытые вопросы = без применённого ответа."""
import copy
import json
from pathlib import Path

from cdpm import model as m, vmp_form as vf
from cdpm.sample import sample_model

REPO = Path(__file__).resolve().parent.parent
ETALON = REPO / "open-bench" / "polyaris-h0" / "etalon" / "VMP-H0.json"
REV = m.registry_fingerprint()


def _fit(family, params=None, quote=None):
    fit = {"category": "A", "registry_rev": REV, "family": family}
    if params:
        fit["params"] = params
    if quote:
        fit["quote"] = quote
    return fit


def _etalon_with_rates() -> dict:
    """Эталон H0 со ставками у обеих целевых (решение 5: строка в) несёт
    ставку; носители — criterion у машинного критерия, rate у
    немашинного). Перевыпуск файла эталона — за генератором COSMOS."""
    doc = json.loads(ETALON.read_text(encoding="utf-8"))
    for o in doc["objectives"]:
        if o["id"] == "O-PAIR" and not o.get("criterion"):
            o["criterion"] = {"kind": "min_person_days", "rate_per_duty": 100,
                              "per": "календарный день командировки",
                              "version": "M-036/demo-1",
                              "effective_from": "2026-08-01",
                              "as_written": "ставка 100 условных единиц за "
                                            "календарный день командировки"}
        elif o["id"] == "O-ROST" and not (o.get("criterion") or o.get("rate")):
            o["rate"] = {"value": 1, "per": "час разброса кредитных часов"}
    return doc


def test_roundtrip_sample_model_is_lossless():
    doc = sample_model()
    form = vf.to_form(doc)
    assert vf.validate_form_schema(form) == []
    assert vf.roundtrip_problems(doc) == []
    # Виды разложены по местам: DEF/DATA — определения, OUT — снятые,
    # SOFT — раздел б) своей части, CASE — раздел а) с пометкой.
    kinds = {r["kind"] for r in doc["rules"]}
    assert "DEF" in kinds and "OUT" in kinds
    roles = {d["role"] for d in form["common"]["definitions"]}
    assert roles == {"term", "registry"}
    assert form["withdrawn"] and form["withdrawn"][0]["reason"] is not None
    soft_rows = form["pairing"]["soft"] + form["rostering"]["soft"] \
        + form["common"]["world"]
    assert any("penalty" in r for r in soft_rows)
    assert any(r.get("case") for r in form["pairing"]["hard"]
               + form["rostering"]["hard"] + form["common"]["world"])


def test_roundtrip_etalon_h0_is_lossless_and_placed_by_zone():
    doc = json.loads(ETALON.read_text(encoding="utf-8"))
    form = vf.to_form(doc)
    assert vf.validate_form_schema(form) == []
    assert vf.roundtrip_problems(doc) == []
    ids = lambda rows: [r["id"] for r in rows]          # noqa: E731
    # Раскладка судится ПРАВИЛОМ (вид и зона строки), а не перечнем
    # идентификаторов: набор растёт, и перечень делал бы тест
    # хранителем состава. Правило записано здесь отдельно от to_form.
    def by(kind, zone):
        return {r["id"] for r in doc["rules"]
                if r["kind"] in kind and r["zone"] == zone}

    assert not by(("SOFT",), "BOTH"),         "мягкое правило зоны BOTH: раскладка такого случая не описана"
    assert set(ids(form["common"]["definitions"])) ==         {r["id"] for r in doc["rules"] if r["kind"] in ("DEF", "DATA")}
    assert set(ids(form["common"]["world"])) == by(("HARD",), "BOTH")
    assert set(ids(form["pairing"]["hard"])) == by(("HARD",), "PAIRING")
    assert set(ids(form["rostering"]["hard"])) == by(("HARD",), "ROSTERING")
    assert set(ids(form["pairing"]["soft"])) == by(("SOFT",), "PAIRING")
    assert set(ids(form["rostering"]["soft"])) == by(("SOFT",), "ROSTERING")
    assert ids(form["pairing"]["criteria"]) == ["O-PAIR"]
    assert ids(form["rostering"]["criteria"]) == ["O-ROST"]
    soft = next(r for r in form["pairing"]["soft"] if r["id"] == "VN-011")
    assert soft["penalty"]["coefficient"] == 15
    # Единица нарушения — КОД словаря каталога (с 0.5.0 словарь записан
    # картой «код → слово»; членство судится по коду, как и прежде).
    assert soft["penalty"]["violation_unit"] in vf.violation_units()
    assert set(vf.violation_units()) == set(vf.violation_unit_words())
    assert vf.unit_word(soft["penalty"]["violation_unit"]) \
        == vf.violation_unit_words()[soft["penalty"]["violation_unit"]]
    assert soft["params"][0]["quote"]
    # Семьи, срез и вопросы: эталон ИДАК-0 вопросов не несёт (носители
    # машинных условий — в модели: criterion у O-PAIR, rate у O-ROST).
    # Отпечаток базы шаблонов едет из эталона, а не литералом: база
    # растёт (семья флота 03.09), и литерал сторожил бы устаревшее
    # число вместо свойства «форма несёт отпечаток эталона».
    fits = [r["template_fit"]["registry_rev"] for r in doc["rules"]
            if isinstance(r.get("template_fit"), dict)]
    assert form["registry_rev"] == fits[0] and len(set(fits)) == 1
    assert not form["answered_questions"] and not form["open_questions"]
    crit = form["pairing"]["criteria"][0]
    assert crit["kind"] == "min_person_days" and crit["rate"]["value"] == 100
    assert form["rostering"]["criteria"][0]["rate"]["value"] == 1


def test_validate_form_reports_composition_and_place_defects():
    doc = _etalon_with_rates()
    form = vf.to_form(doc)
    assert vf.validate_form(form, strict=True) == []
    # Чужая цитата параметра — валидатор формы видит.
    bad0 = copy.deepcopy(form)
    bad0["pairing"]["hard"][0]["params"][0]["quote"] = "фраза из другого документа"
    assert any("не является фрагментом формулировки" in p
               for p in vf.validate_form(bad0))
    # Ошибка места: жёсткое правило пейринга в разделе ростеринга.
    bad = copy.deepcopy(form)
    row = bad["pairing"]["hard"].pop(0)              # VN-010 duty_limits
    bad["rostering"]["hard"].append(row)
    assert any("место задаёт семья" in p for p in vf.validate_form(bad))
    # Мягкое без единицы нарушения и жёсткое с коэффициентом.
    bad2 = copy.deepcopy(form)
    bad2["pairing"]["soft"][0]["penalty"]["violation_unit"] = None
    bad2["pairing"]["hard"][0]["penalty"] = {"coefficient": 3,
                                             "violation_unit": "night"}
    p2 = vf.validate_form(bad2)
    assert any("единица размера нарушения не задана" in p for p in p2)
    assert any("коэффициент штрафа у жёсткого" in p for p in p2)
    # Потерянный вес (weight_missing → коэффициент 0) — нарушение б).
    bad2b = copy.deepcopy(form)
    bad2b["pairing"]["soft"][0]["penalty"]["coefficient"] = 0
    bad2b["pairing"]["soft"][0]["machine"]["weight_missing"] = True
    assert any("мягкое правило без коэффициента" in p
               for p in vf.validate_form(bad2b))
    # Ярус 2 без допуска и пропуск яруса.
    bad3 = copy.deepcopy(form)
    c2 = copy.deepcopy(bad3["pairing"]["criteria"][0])
    c2["id"] = "O-PAIR-2"
    c2["tier"] = 3
    bad3["pairing"]["criteria"].append(c2)
    p3 = vf.validate_form(bad3)
    assert any("без допуска" in p for p in p3)
    assert any("ярусы критериев с пропусками" in p for p in p3)
    # Два критерия на одном ярусе части — целевая не определена.
    bad3b = copy.deepcopy(form)
    c3 = copy.deepcopy(bad3b["pairing"]["criteria"][0])
    c3["id"] = "O-PAIR-B"
    bad3b["pairing"]["criteria"].append(c3)
    assert any("несколько критериев на одном ярусе" in p
               for p in vf.validate_form(bad3b))
    # Критерий без ставки — нарушение (решение 5), с kind и без.
    bad3c = copy.deepcopy(form)
    bad3c["pairing"]["criteria"][0].pop("rate")
    bad3c["rostering"]["criteria"][0].pop("rate")
    p3c = vf.validate_form(bad3c)
    assert sum("критерий без ставки в валюте формы" in p for p in p3c) == 2
    # Строгий режим: открытые вопросы — нарушение.
    bad4 = copy.deepcopy(form)
    bad4["open_questions"] = [{"id": "Q-x", "title": "т"}]
    assert any("готовый ВМП вопросов не несёт" in p
               for p in vf.validate_form(bad4, strict=True))


def test_violation_unit_must_be_in_dictionary_and_match_family():
    """Единица размера нарушения — атрибут семьи из закрытого словаря
    базы: «ночь» (вне словаря) и «hour» у layover_penalty (семья —
    night) — нарушения; «night» — чисто."""
    doc = _etalon_with_rates()
    form = vf.to_form(doc)
    row = next(r for r in form["pairing"]["soft"] if r["id"] == "VN-030")
    assert row["template"]["family"] == "layover_penalty"
    assert row["penalty"]["violation_unit"] == "night"
    bad = copy.deepcopy(form)
    next(r for r in bad["pairing"]["soft"]
         if r["id"] == "VN-030")["penalty"]["violation_unit"] = "ночь"
    assert any("вне словаря" in p for p in vf.validate_form(bad))
    bad2 = copy.deepcopy(form)
    next(r for r in bad2["pairing"]["soft"]
         if r["id"] == "VN-030")["penalty"]["violation_unit"] = "hour"
    assert any("не совпадает с единицей семьи 'layover_penalty' ('night')"
               in p for p in vf.validate_form(bad2))
    # Единица параметра — словарная.
    bad3 = copy.deepcopy(form)
    bad3["pairing"]["hard"][0]["params"][0]["unit"] = "hours"
    assert any("единица параметра" in p and "≠ словарной" in p
               for p in vf.validate_form(bad3))


def test_objective_zone_both_stands_in_both_parts_and_returns_both():
    """Целевая зоны BOTH — одна запись в разделе в) обеих частей: не
    «повтор идентификатора»; обратное построение (и без machine.zone)
    возвращает зону BOTH; разное содержание под одним id — нарушение."""
    doc = _etalon_with_rates()
    both = {"id": "O-BOTH", "title": "Общая целевая",
            "statement": "Минимум ночёвок вне базы в обеих задачах.",
            "zone": "BOTH", "direction": "minimize",
            "metric": "layover_nights_total",
            "rate": {"value": 3, "per": "ночь вне базы"},
            "tier": 2, "tolerance": {"value": 5, "unit": "percent"},
            "sources": [dict(doc["objectives"][0]["sources"][0])]}
    doc["objectives"].append(both)
    assert m.validate_schema(doc) == []
    form = vf.to_form(doc)
    assert [c["id"] for c in form["pairing"]["criteria"]] == ["O-PAIR", "O-BOTH"]
    assert [c["id"] for c in form["rostering"]["criteria"]] == ["O-ROST", "O-BOTH"]
    assert vf.validate_form(form, strict=True) == []
    assert vf.roundtrip_problems(doc) == []
    back = vf.from_form(form)
    o = next(x for x in back["objectives"] if x["id"] == "O-BOTH")
    assert o["zone"] == "BOTH" and o["tier"] == 2 \
        and o["tolerance"] == {"value": 5, "unit": "percent"} \
        and o["rate"] == {"value": 3, "per": "ночь вне базы"}
    # Форма, составленная руками: без machine.zone — тоже BOTH.
    hand = copy.deepcopy(form)
    for part in ("pairing", "rostering"):
        for c in hand[part]["criteria"]:
            c.pop("machine", None)
    back2 = vf.from_form(hand)
    zones = {x["id"]: x["zone"] for x in back2["objectives"]}
    assert zones == {"O-PAIR": "PAIRING", "O-ROST": "ROSTERING",
                     "O-BOTH": "BOTH"}
    # Разное содержание под одним идентификатором в двух частях.
    bad = copy.deepcopy(form)
    bad["rostering"]["criteria"][1]["direction"] = "maximize"
    assert any("в обеих частях с разным содержанием" in p
               for p in vf.validate_form(bad))


def test_scope_rate_per_and_answer_source_survive_roundtrip():
    """Область параметра мира, ставка за что (per), источник-ответ
    целевой (origin) и цитата источника — носители в модели, форма →
    модель → форма без потерь."""
    doc = _etalon_with_rates()
    world = next(r for r in doc["rules"] if r["zone"] == "BOTH"
                 and r["kind"] in ("HARD", "SOFT"))
    world["scope"] = {"pairing": "применяется к сменам",
                      "rostering": "не применяется"}
    world["sources"][0]["quote"] = "фрагмент клаузы"
    o = next(x for x in doc["objectives"] if x["id"] == "O-PAIR")
    o["origin"] = {"question": "Q-3a-GOAL", "revision": 2}
    assert m.validate_schema(doc) == []
    form = vf.to_form(doc)
    assert vf.validate_form_schema(form) == []
    row = next(r for r in form["common"]["world"] if r["id"] == world["id"])
    assert row["scope"] == world["scope"]
    assert row["sources"][0]["quote"] == "фрагмент клаузы"
    crit = next(c for c in form["pairing"]["criteria"] if c["id"] == "O-PAIR")
    assert crit["rate"]["per"] == "календарный день командировки"
    assert {"question": "Q-3a-GOAL", "revision": 2} in crit["sources"]
    assert vf.roundtrip_problems(doc) == []
    back = vf.from_form(form)
    assert next(r for r in back["rules"] if r["id"] == world["id"])["scope"] \
        == world["scope"]
    bo = next(x for x in back["objectives"] if x["id"] == "O-PAIR")
    assert bo["origin"] == {"question": "Q-3a-GOAL", "revision": 2}
    assert all("sid" in s for s in bo["sources"])
    assert bo["criterion"]["per"] == "календарный день командировки"
    # Форма → модель → форма: та же форма.
    assert vf.to_form(back) == form


def test_zone_by_place_and_gap_row_becomes_category_c():
    """Обратное построение: зона — по месту строки (перенос строки в
    другую часть меняет зону, machine.zone не источник); строка без
    шаблона с gap — посадка C с заявкой."""
    doc = _etalon_with_rates()
    form = vf.to_form(doc)
    moved = copy.deepcopy(form)
    row = next(r for r in moved["pairing"]["hard"] if r["id"] == "VN-020")
    moved["pairing"]["hard"].remove(row)
    row["template"] = None
    row["gap"] = "нужна семья явки перед сменой"
    row.pop("params", None)
    (row.get("machine") or {}).pop("template_fit", None)
    moved["rostering"]["hard"].append(row)
    back = vf.from_form(moved)
    r = next(x for x in back["rules"] if x["id"] == "VN-020")
    assert r["zone"] == "ROSTERING" and r["kind"] == "HARD"
    assert r["template_fit"]["category"] == "C"
    assert r["template_fit"]["gap"] == "нужна семья явки перед сменой"
    assert r["template_fit"]["registry_rev"] == form["registry_rev"]
    # Обратно в форму — снова template null + gap, без потерь.
    again = vf.to_form(back)
    row2 = next(x for x in again["rostering"]["hard"] if x["id"] == "VN-020")
    assert row2["template"] is None and row2["gap"] == row["gap"]
    assert vf.canonical_diff(back, vf.from_form(again)) == []


def test_excluded_mark_on_definition_and_withdrawn_survives():
    """Пометка «исключено до получения данных» у строки определения
    (DATA) и снятой строки: форма несёт, возвращает, canonical_diff
    видит потерю, рендер печатает."""
    doc = sample_model()
    data = next(r for r in doc["rules"] if r["kind"] == "DATA")
    out = next(r for r in doc["rules"] if r["kind"] == "OUT")
    mark = {"question": "Q-3b-нет-данных-x", "revision": 1}
    data["excluded_until_data"] = dict(mark)
    out["excluded_until_data"] = dict(mark)
    form = vf.to_form(doc)
    assert vf.validate_form_schema(form) == []
    d = next(x for x in form["common"]["definitions"] if x["id"] == data["id"])
    w = next(x for x in form["withdrawn"] if x["id"] == out["id"])
    assert d["excluded_until_data"] == mark and w["excluded_until_data"] == mark
    assert vf.roundtrip_problems(doc) == []
    lost = copy.deepcopy(doc)
    next(r for r in lost["rules"] if r["id"] == data["id"]).pop("excluded_until_data")
    assert any("excluded_until_data" in p for p in vf.canonical_diff(doc, lost))
    assert "исключено из расчёта" in vf.render_row_line(d, "definitions")
    assert "исключено из расчёта" in vf.render_form_md(form)


def test_open_questions_are_all_without_applied_answer():
    """Открыт всякий вопрос без применённого ответа: «ожидает
    обработчика» и «не разобран» — в open_questions, строгий режим их
    останавливает; в answered_questions — только «применён»."""
    doc = _etalon_with_rates()
    # Эталон ИДАК-0 вопросов не несёт — берём два вопроса из построения
    # нано-эталона (build() их несёт до переноса носителей в модель).
    import sys as _sys
    _sys.path.insert(0, str(ETALON.parent.parent.parent / "polyaris"))
    import build_vmp_nano as _bv
    qs = copy.deepcopy(_bv.build()["questions"])
    assert len(qs) == 2
    doc["questions"] = qs
    qs[0]["answer"]["status"] = "ожидает обработчика"
    form = vf.to_form(doc)
    assert [q["id"] for q in form["open_questions"]] == [qs[0]["id"]]
    assert [q["id"] for q in form["answered_questions"]] == [qs[1]["id"]]
    assert any("готовый ВМП вопросов не несёт" in p
               for p in vf.validate_form(form, strict=True))
    assert vf.roundtrip_problems(doc) == []


def test_missing_data_question_yes_marks_rule_excluded_no_keeps_block():
    """Правило без данных (решение владельца 2026-09-02): вопрос
    «считать до получения данных?» блокирует носителей до ответа; «а»
    → пометка excluded_until_data и разблокировка; «б» → блокировка
    остаётся; поступление данных снимает пометку и блокировку."""
    from cdpm import answers as ans
    doc = sample_model()
    hard = [r for r in doc["rules"] if r["kind"] == "HARD"][:2]
    for r in hard:
        r.pop("blockers", None)
    report = {"checks": [
        {"id": "C-1", "status": "не прогнана",
         "reason": "нет листа «Пояса»", "basis": {"rule": hard[0]["id"]}},
        {"id": "C-2", "status": "не прогнана",
         "reason": "нет колонки «Допуск»", "basis": {"rule": hard[1]["id"]}},
    ], "findings": []}
    created = m.ensure_missing_data_questions(doc, report)
    assert len(created) == 2
    new_qs = [q for q in doc["questions"] if q["id"] in created]
    q1 = next(q for q in new_qs if hard[0]["id"] in q["rule_refs"])
    q2 = next(q for q in new_qs if hard[1]["id"] in q["rule_refs"])
    assert hard[0]["blockers"] == [q1["id"]] and hard[1]["blockers"] == [q2["id"]]
    ans.apply_answers(doc, {q1["id"]: "а", q2["id"]: "б"})
    assert hard[0]["excluded_until_data"] == {"question": q1["id"],
                                              "revision": 2}
    assert "blockers" not in hard[0]
    assert hard[1]["blockers"] == [q2["id"]] and "excluded_until_data" not in hard[1]
    assert q2["answer"]["status"] == "применён" and "ждём данных" in q2["answer"]["note"]
    assert m.validate_schema(doc) == []
    # Форма несёт пометку и возвращает её обратно.
    form = vf.to_form(doc)
    row = next(r for sec in ("hard", "soft")
               for part in ("pairing", "rostering")
               for r in form[part][sec] if r["id"] == hard[0]["id"])
    assert row["excluded_until_data"]["question"] == q1["id"]
    assert vf.roundtrip_problems(doc) == []
    # Данных всё ещё нет: проверка «не скомпилирована» по иной причине
    # (строка блокирована вопросом) либо отсутствует в отчёте — пометка
    # и блокировка ОСТАЮТСЯ (fail-closed; аудит C2/M17).
    report_no = {"checks": [
        {"id": "C-2", "status": "не скомпилирована",
         "reason": "строка свода блокирована вопросом анкеты",
         "basis": {"rule": hard[1]["id"]}}], "findings": []}
    assert m.ensure_missing_data_questions(doc, report_no) == []
    assert hard[0]["excluded_until_data"]["question"] == q1["id"]
    assert hard[1]["blockers"] == [q2["id"]]
    # Данные поступили: проверки «прогнана» — пометка и блокировка сняты.
    report2 = {"checks": [{"id": "C-1", "status": "прогнана",
                           "basis": {"rule": hard[0]["id"]}},
                          {"id": "C-2", "status": "прогнана",
                           "basis": {"rule": hard[1]["id"]}}],
               "findings": []}
    assert m.ensure_missing_data_questions(doc, report2) == []
    assert "excluded_until_data" not in hard[0] and "blockers" not in hard[1]
    assert "данные поступили" in hard[0]["note"]


def test_package_carries_form_and_exclusions(tmp_path):
    """Пакет постановки несёт форму (json + документ), проверки формы и
    перечень исключённых правил в манифесте; исключённые правила не
    попадают в машинные формы для решателя."""
    from cdpm import export_task_package as etp
    doc = _etalon_with_rates()
    run = tmp_path / "run-x"
    (run / "extract").mkdir(parents=True)
    hard = next(r for r in doc["rules"] if r["kind"] == "HARD"
                and r["zone"] == "ROSTERING")
    hard["excluded_until_data"] = {"question": "Q-3b-нет-данных-x",
                                   "revision": 2}
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    files = etp.build_package(run)
    assert "vmp-form.json" in files and "vmp-form.md" in files
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    assert manifest["excluded_until_data"] == [hard["id"]]
    assert manifest["form"]["problems"] == []
    assert manifest["form"]["sections"]["pairing"]["hard"] == 2
    form = json.loads(files["vmp-form.json"].decode("utf-8"))
    row = next(r for r in form["rostering"]["hard"] if r["id"] == hard["id"])
    assert row["excluded_until_data"]["question"] == "Q-3b-нет-данных-x"
    text = files["vmp-form.md"].decode("utf-8")
    assert "## Часть 2. Ростеринг" in text and "исключено из расчёта" in text


def test_panel_serves_form_json_and_document(tmp_path, monkeypatch):
    """Панель отдаёт форму текущего свода: JSON с проверками и
    тождеством проекции, документ с разделами и итогом проверок."""
    from fastapi.testclient import TestClient
    from cdpm import webui
    (tmp_path / "extract").mkdir()
    (tmp_path / "extract" / "model.json").write_text(
        json.dumps(_etalon_with_rates(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    client = TestClient(webui.app)
    r = client.get("/api/runs/x/form")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["lossless"] is True and body["problems"] == []
    assert [c["id"] for c in body["form"]["pairing"]["criteria"]] == ["O-PAIR"]
    r2 = client.get("/api/runs/x/form.md")
    assert r2.status_code == 200
    assert "Часть 1. Пейринг" in r2.text and "Чиста" in r2.text


def test_self_consistent_form_is_clean():
    """Форма, собранная по спецификации: цитаты из формулировок, единица
    нарушения из словаря и семьи, критерий полон — проверок 0."""
    form = {
        "form_version": 1, "customer": "т", "currency": "условные единицы",
        "registry_rev": REV,
        "sources": [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}],
        "common": {"definitions": [{
            "id": "D-1", "title": "Базы", "statement": "Базы: AAA, BBB.",
            "sources": [{"sid": "S1", "anchor": "абз.1"}], "role": "registry"}],
            "world": [{
                "id": "W-1", "title": "Состав экипажа",
                "statement": "Экипаж смены — 2 пилота.",
                "sources": [{"sid": "S1", "anchor": "абз.2"}],
                "template": {"family": "crew_composition", "category": "A"},
                "params": [{"name": "crew_size", "value": 2, "unit": "count",
                            "quote": "2 пилота"}],
                "scope": {"pairing": "состав смены", "rostering": "состав смены"}}]},
        "pairing": {
            "hard": [{"id": "P-1", "title": "Предел смены",
                      "statement": "Смена не более 12:00.",
                      "sources": [{"sid": "S1", "anchor": "абз.3"}],
                      "template": {"family": "duty_limits", "category": "A"},
                      "params": [{"name": "max_duty_duration", "value": 720,
                                  "unit": "duration_minutes",
                                  "quote": "не более 12:00"}]}],
            "soft": [{"id": "P-2", "title": "Ночёвки",
                      "statement": "Штраф 10 за каждую ночь вне базы.",
                      "sources": [{"sid": "S1", "anchor": "абз.4"}],
                      "template": {"family": "layover_penalty", "category": "A",
                                   "quote": "за каждую ночь вне базы"},
                      "penalty": {"coefficient": 10, "violation_unit": "night"}}],
            "criteria": [{"id": "C-1", "title": "Стоимость",
                          "statement": "Минимум человеко-дней.",
                          "sources": [{"sid": "S1", "anchor": "абз.5"}],
                          "metric": "total_pairing_cost",
                          "direction": "minimize", "kind": "min_person_days",
                          "rate": {"value": 100, "per": "календарный день"},
                          "version": "v1", "effective_from": "2026-01-01",
                          "tier": 1}]},
        "rostering": {"hard": [], "soft": [], "criteria": []},
    }
    assert vf.validate_form(form, strict=True) == []
    model = vf.from_form(form)
    assert {r["id"]: (r["kind"], r["zone"]) for r in model["rules"]} == {
        "D-1": ("DATA", "BOTH"), "W-1": ("HARD", "BOTH"),
        "P-1": ("HARD", "PAIRING"), "P-2": ("SOFT", "PAIRING")}
    assert model["rules"][3]["weight"] == 10
    assert model["rules"][1]["scope"] == {"pairing": "состав смены",
                                          "rostering": "состав смены"}
    assert model["objectives"][0]["criterion"]["rate_per_duty"] == 100
    assert model["objectives"][0]["criterion"]["per"] == "календарный день"
    # И обратно — та же форма по каноническому слою.
    assert vf.canonical_diff(model, vf.from_form(vf.to_form(model))) == []
    text = vf.render_form_md(form)
    for head in ("## Общий раздел", "## Часть 1. Пейринг",
                 "### б) Мягкие ограничения", "### в) Критерии оптимизации"):
        assert head in text
    assert "Штраф: 10 за ночь" in text
    assert "запись файла данных" in vf.render_row_line(
        form["common"]["definitions"][0], "definitions")


def _find_row(node, rid):
    if isinstance(node, dict):
        if node.get("id") == rid and "params" in node:
            return node
        for v in node.values():
            got = _find_row(v, rid)
            if got is not None:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _find_row(v, rid)
            if got is not None:
                return got
    return None


def test_form_carries_family_conditions_with_catalog_units():
    """Условия исключения семьи (каталог, остаток шага 2, 07.09.2026) в
    форме ВМП: строка несёт их среди параметров с единицей записи
    условия (code_list / flag), проверка формы принимает их у
    семьи-владельца и отвергает у чужой; обратная проекция без потерь."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["kind"] == "SOFT")
    rule["zone"] = "PAIRING"
    rule["weight"] = 15          # мягкая строка формы несёт коэффициент
    rule.pop("export_solver", None)
    rule["statement"] = ("Продление смены свыше 12:00 не более 14:00 "
                         "допускается для экипажей баз ABC и XYZ только "
                         "с согласия экипажа")
    rule["template_fit"] = _fit("duty_extension_penalty", params={
        "duty_extension_cap": {"value": 840, "quote": "не более 14:00"},
        "long_duty_threshold": {"value": 720, "quote": "свыше 12:00"},
        "extension_bases": {"value": "ABC, XYZ",
                            "quote": "для экипажей баз ABC и XYZ"},
        "extension_consent": {"value": True, "quote": "с согласия экипажа"}})
    form = vf.to_form(doc)
    row = _find_row(form, rule["id"])
    assert row is not None
    units = {p["name"]: p.get("unit") for p in row["params"]}
    assert units["extension_bases"] == "code_list"
    assert units["extension_consent"] == "flag"
    assert units["duty_extension_cap"] == "duration_minutes"
    problems = vf.validate_form(form)
    mine = [p for p in problems if rule["id"] in p or "/params/" in p]
    assert mine == [], mine
    assert vf.roundtrip_problems(doc) == []
    back = next(r for r in vf.from_form(form)["rules"] if r["id"] == rule["id"])
    got = back["template_fit"]["params"]
    assert got["extension_bases"]["value"] == "ABC, XYZ"
    assert got["extension_consent"]["value"] is True
    # Условие у чужой семьи — нарушение формы.
    rule["template_fit"] = _fit("duty_limits", params={
        "max_duty_duration": {"value": 720, "quote": "свыше 12:00"},
        "extension_consent": {"value": True, "quote": "с согласия экипажа"}})
    rule["kind"] = "HARD"
    problems = vf.validate_form(vf.to_form(doc))
    assert any("условие 'extension_consent' принадлежит семье "
               "'duty_extension_penalty'" in p for p in problems), problems
