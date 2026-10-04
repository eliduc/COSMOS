# -*- coding: utf-8 -*-
"""Правила validate() каталога шаблонов (§11): каждое правило — на
нарочно испорченной копии каталога во временной папке. validate() не
бросает и не пишет; зелёный каталог шага 1 даёт только предупреждения X07
(стендовых задач ещё нет)."""
from __future__ import annotations

import json
import shutil
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
import catalog_copies as cc                                       # noqa: E402
from template_catalog.canon import fingerprint_of                 # noqa: E402

CATALOG = ROOT / "catalog"
FILES = ("catalog.json", "families.json", "criteria.json", "dictionaries.json",
         "requests.json", "versions.json")


def _broken(tmp_path: Path, mutate, *, refresh: bool = True) -> Path:
    """Копия каталога с правкой документов; отпечаток шапки пересчитывается
    (кроме refresh=False — для проверки H02).

    Копия — ЧЕРНОВИК: журнал версий и папки выпусков живого каталога в
    неё не переносятся. Тело копии правится и отпечаток пересчитывается,
    а запись журнала о выпуске указывает на прежний отпечаток — H03
    «отпечаток записи журнала не равен шапке» заслонял бы проверяемое
    правило."""
    dst = tmp_path / "cat"
    if dst.exists():
        cc.rmtree_writable(dst)
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns(
        "tools", "schema", "__pycache__", "releases"))
    docs = {n: json.loads((dst / n).read_text(encoding="utf-8")) for n in FILES}
    docs["versions.json"] = {"form_version": 1, "versions": []}
    mutate(docs)
    if refresh:
        body = {"families": docs["families.json"].get("families", []),
                "criteria": docs["criteria.json"].get("criteria", []),
                "dictionaries": {k: v for k, v in docs["dictionaries.json"].items()
                                 if k != "form_version"}}
        try:
            docs["catalog.json"]["fingerprint"] = fingerprint_of(body)
        except ValueError:
            pass
    for n, doc in docs.items():
        (dst / n).write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return dst


def _problems(tmp_path, mutate, *, refresh=True, forbidden_terms=()):
    return tc.load(_broken(tmp_path, mutate, refresh=refresh)).validate(
        forbidden_terms=forbidden_terms)


def _codes(problems, severity="error"):
    return {p.code for p in problems if severity is None or p.severity == severity}


def _fam(docs, fid):
    return next(f for f in docs["families.json"]["families"] if f["id"] == fid)


def _crit(docs, cid):
    return next(c for c in docs["criteria.json"]["criteria"] if c["id"] == cid)


def _класс_документа(docs=None):
    """Класс документа-источника — из ЖИВОЙ копии словарей, если словарь
    `document_classes` уже заведён (шаг 6, 0.14), иначе из перечня
    читателя. Литерал развалил бы тесты в день появления словаря."""
    from template_catalog import requests as _rq
    словарь = None
    if isinstance(docs, dict):
        словарь = (docs.get("dictionaries.json") or {}).get("document_classes")
    return (словарь or list(_rq.DOCUMENT_CLASSES))[0]


def _request(record_id="sit_overage_steps", state="подана", draft=None, **extra):
    req = {"request_id": "req-0001", "state": state, "section": "families",
           "record_id": record_id, "submitted_at": "2026-09-10T15:40:00+03:00",
           "submitted_by": "CDPM",
           "source": {"kind": "посадка категории C",
                      "document_class": _класс_документа(),
                      "run_class": "прогон CDPM", "ref": "run-0090"},
           "draft": draft, "executor_kinds_requested": None,
           "decision": {"by": None, "at": None, "words": None}, "cr_key": None}
    req.update(extra)
    return req


def _draft(docs, new_id="sit_overage_steps"):
    d = json.loads(json.dumps(_fam(docs, "layover_penalty"), ensure_ascii=False))
    for k in ("status", "history", "stand_tasks"):
        del d[k]
    d["id"] = new_id
    d["complements"] = []
    return d


# ------------------------------------------------------------ зелёный
def test_green_catalog_has_no_problems_at_all():
    """Шаг 4: у каждой записи есть стендовые задачи, и живой каталог
    проходит validate() без единой проблемы — ни ошибки, ни
    предупреждения (прежде оставались 18 предупреждений X07)."""
    problems = tc.load(CATALOG).validate()
    assert [str(p) for p in problems] == []


def test_x07_warns_when_record_has_no_stand_tasks(tmp_path):
    """Предупреждение X07 никуда не делось: запись «есть» без задач его
    получает — гейт полноты задач живёт деревом, а не флагом."""
    def m(d):
        _fam(d, "duty_limits")["stand_tasks"] = []
    probs = _problems(tmp_path, m)
    assert _codes(probs) == set()
    x07 = [p for p in probs if p.code == "X07"]
    assert len(x07) == 1 and x07[0].where.endswith(".stand_tasks")
    assert x07[0].severity == "warning"


def test_validate_path_never_raises(tmp_path):
    problems = tc.validate_path(tmp_path / "нет")
    assert [p.code for p in problems] == ["S01"] and "нет" in problems[0].message
    assert _codes(tc.validate_path(CATALOG)) == set()


def _corruptions():
    """Порчи, на которых правила строят set()/in над элементами перечней
    или обращаются к .get у записи: validate() обязан вернуть ошибку
    словами, а не упасть."""
    def fam_is_string(d):
        d["families.json"]["families"][0] = "duty_limits"

    def complements_nested(d):
        _fam(d, "duty_limits")["complements"] = [["duty_extension_penalty"]]

    def executor_kinds_object(d):
        _fam(d, "duty_limits")["executor_kinds"] = [{"kind": "builder_method"}]

    def check_templates_nested(d):
        _fam(d, "connection_windows")["check_templates"] = [["уникальность"]]

    def computable_by_nested(d):
        _crit(d, "min_person_days")["computable_by"] = [["builder_method"]]

    def lexicon_order_nested(d):
        d["dictionaries.json"]["lexicon_order"][0] = [d["dictionaries.json"]["lexicon_order"][0]]

    def tokens_nested(d):
        d["dictionaries.json"]["actors"].append(["владелец"])

    def stations_nested(d):
        d["dictionaries.json"]["synthetic_codes"]["stations"] = [["ST1"], "ST2"]

    def world_form_fields_nested(d):
        wf = d["dictionaries.json"]["world_forms"]
        next(iter(wf.values()))["fields"] = [["form"]]

    def param_sets_nested(d):
        ns = _fam(d, "night_series")
        ns["param_sets"][0]["params"] = [ns["param_sets"][0]["params"]]

    def rule_form_name_list(d):
        _fam(d, "calendar_windows")["rule_form"]["fields"][0]["name"] = ["days"]

    def instance_form_name_object(d):
        _crit(d, "min_person_days")["instance_form"]["fields"][0]["name"] = {"n": 1}

    def stand_task_id_and_form_not_strings(d):
        _fam(d, "duty_limits")["stand_tasks"] = [
            {"id": ["st-1"], "world": {"form": ["schedule"]},
             "expected": {"builder_method": {"value": 1, "unit": "min"}}, "note": None}]

    def unbound_params_list(d):
        d["dictionaries.json"]["unbound_params"] = ["max_monthly_block"]

    def request_kinds_nested(d):
        d["requests.json"]["requests"] = [
            _request(record_id="duty_limits", state="исполнитель заявлен",
                     executor_kinds_requested=[["kotlin_metric"]], cr_key="CR-1",
                     decision={"by": "владелец", "at": "2026-09-11", "words": "да"})]

    def draft_is_list(d):
        d["requests.json"]["requests"] = [_request(draft=["x"])]

    def header_sections_list(d):
        d["catalog.json"]["sections"] = ["families.json"]

    def versions_not_list(d):
        d["versions.json"]["versions"] = {"0.1.0": 1}

    return [fam_is_string, complements_nested, executor_kinds_object,
            check_templates_nested, computable_by_nested, lexicon_order_nested,
            tokens_nested, stations_nested, world_form_fields_nested,
            param_sets_nested, rule_form_name_list, instance_form_name_object,
            stand_task_id_and_form_not_strings, unbound_params_list,
            request_kinds_nested, draft_is_list, header_sections_list,
            versions_not_list]


@pytest.mark.parametrize("mutate", _corruptions(), ids=lambda f: f.__name__)
def test_validate_never_raises_on_wrong_element_types(tmp_path, mutate):
    dst = _broken(tmp_path, mutate)
    problems = tc.validate_path(dst)                     # не бросает
    errors = [p for p in problems if p.severity == "error"]
    assert errors, "порча не замечена"
    assert not any("прервана внутренней ошибкой" in p.message for p in problems), \
        "правило упало — сработала страховка, а не адресная ошибка:\n" + \
        "\n".join(str(p) for p in problems if "прервана" in p.message)


def test_validate_safety_net_turns_internal_error_into_problem(tmp_path,
                                                               monkeypatch):
    """Страховка контракта «validate() не бросает»: ошибка внутри любого
    правила — Problem S02 поверх уже накопленных, не трассировка. Живой
    каталог со шага 4 чист, поэтому накопленное берётся с копии, где у
    одной записи сняты стендовые задачи (X07)."""
    import importlib
    tv = importlib.import_module("template_catalog.validate")   # модуль, не обёртка validate()

    def m(d):
        _fam(d, "duty_limits")["stand_tasks"] = []
    dst = _broken(tmp_path, m)

    def boom(ctx, cat, header, versions_doc):
        raise RuntimeError("нарочно")
    monkeypatch.setattr(tv, "_header_checks", boom)
    problems = tc.load(dst).validate()
    net = [p for p in problems if p.code == "S02" and "прервана" in p.message]
    assert len(net) == 1 and "RuntimeError" in net[0].message and "нарочно" in net[0].message
    assert any(p.code == "X07" for p in problems)         # накопленное сохранено


def test_s02_schema_addresses_record_and_draft(tmp_path):
    """Ошибка, найденная только схемой (образец pname), получает адрес
    families/<id>.params[0].name; у черновика — requests/<id>/draft.…"""
    def m(d):
        f = _fam(d, "duty_limits")
        f["params"][0]["name"] = "Bad"
        for s in f["param_sets"]:
            s["params"] = ["Bad" if n == "max_duty_duration" else n for n in s["params"]]
        draft = _draft(d)
        draft["params"] = [{"name": "Bad", "unit": "count", "relation": None,
                            "qualifier": None, "note": None}]
        draft["subject_words"] = ["Ночёвка"]
        d["requests.json"]["requests"] = [_request(draft=draft)]
    probs = _problems(tmp_path, m)
    s02 = {p.where for p in probs if p.code == "S02"}
    assert "families/duty_limits.params[0].name" in s02
    assert "requests/req-0001/draft.params[0].name" in s02
    assert "requests/req-0001/draft.subject_words[0]" in s02
    assert not any(".draft" in p.where for p in probs), \
        [p.where for p in probs if ".draft" in p.where]


# -------------------------------------------------------------- S
def test_s01_form_version(tmp_path):
    def m(d):
        d["families.json"]["form_version"] = 2
    assert "S01" in _codes(_problems(tmp_path, m))


def test_s02_missing_extra_and_wrong_type(tmp_path):
    def m(d):
        f = _fam(d, "duty_limits")
        del f["subject"]
        f["extra"] = 1
        _fam(d, "rest_floors")["kind"] = 5
    probs = _problems(tmp_path, m)
    wheres = {p.where for p in probs if p.code == "S02"}
    assert "families/duty_limits.subject" in wheres
    assert "families/duty_limits.extra" in wheres
    assert "families/rest_floors.kind" in wheres


def test_s03_fractional_number(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["rule_form"] = {
            "fields": [{"name": "days", "values": None, "required": 1.5, "note": None}],
            "note": None}
    probs = _problems(tmp_path, m)
    assert "S03" in _codes(probs)
    assert any(p.code == "H02" for p in probs)          # отпечаток не вычислим


def test_s04_nfd_warning_and_s05_order_warning(tmp_path):
    def m(d):
        f = _fam(d, "duty_limits")
        f["subject"] = unicodedata.normalize("NFD", "рабочая смена с ночёвкой")
        assert f["subject"] != unicodedata.normalize("NFC", f["subject"])
        f["executor_kinds"].reverse()
        d["families.json"]["families"].reverse()
    probs = _problems(tmp_path, m)
    assert _codes(probs) == set()
    assert {"S04", "S05"} <= _codes(probs, "warning")


def test_s06_without_jsonschema(monkeypatch):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    probs = tc.load(CATALOG).validate()
    assert "S06" in _codes(probs, "warning") and _codes(probs) == set()


# -------------------------------------------------------------- D
def test_d01_value_outside_dictionary(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["class"] = "мягкий"
        _fam(d, "rest_floors")["params"][0]["unit"] = "hours"
        _crit(d, "min_person_days")["zone"] = "EVERYWHERE"
    probs = _problems(tmp_path, m)
    d01 = {p.where for p in probs if p.code == "D01"}
    assert "families/duty_limits.class" in d01
    assert any(w.startswith("families/rest_floors.params[") and w.endswith(".unit") for w in d01)
    assert "criteria/min_person_days.zone" in d01
    assert 'неизвестн' in str([p for p in probs if p.code == "D01"][0]) or "вне словаря" in str(
        [p for p in probs if p.code == "D01"][0])


def test_d02_dictionary_shape(tmp_path):
    def m(d):
        del d["dictionaries.json"]["kinds"]
        d["dictionaries.json"]["code_units"].append("weird")
        d["dictionaries.json"]["lexicon_order"].pop()
        d["dictionaries.json"]["actors"].append("владелец")
    probs = _problems(tmp_path, m)
    d02 = {p.where for p in probs if p.code == "D02"}
    assert {"dictionaries.kinds", "dictionaries.code_units",
            "dictionaries.lexicon_order", "dictionaries.actors"} <= d02


def test_d03_d04_unbound_params(tmp_path):
    def m(d):
        ub = d["dictionaries.json"]["unbound_params"]
        ub["max_duty_duration"] = {"unit": "duration_minutes", "relation": None,
                                   "source": "словарь CDPM", "note": None}
        ub["max_block_time"]["relation"] = "около"
        ub["trip_rig"]["source"] = "с потолка"
    probs = _problems(tmp_path, m)
    assert {"D03", "D04"} <= _codes(probs)


# -------------------------------------------------------------- X
def test_x01_complements(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["complements"] = ["duty_limits", "nope"]
        _fam(d, "rest_floors")["complements"] = ["layover_penalty"]
    msgs = [p.message for p in _problems(tmp_path, m) if p.code == "X01"]
    assert any("самоссылка" in m_ for m_ in msgs)
    assert any("'nope'" in m_ for m_ in msgs)
    assert any("симметрично" in m_ for m_ in msgs)


def test_x02_duplicate_param_name(tmp_path):
    def m(d):
        _fam(d, "rest_floors")["params"].append(
            {"name": "max_duty_duration", "unit": "duration_minutes",
             "relation": "max", "qualifier": None, "note": None})
    assert "X02" in _codes(_problems(tmp_path, m))

    # условие с именем параметра той же записи — тоже X02 (§1.1:
    # уникальность по params ∪ conditions ∪ unbound_params)
    def m2(d):
        f = _fam(d, "duty_extension_penalty")
        f["conditions"][0]["name"] = f["params"][0]["name"]
    probs = _problems(tmp_path, m2)
    hits = [p for p in probs if p.code == "X02"]
    assert hits and any("совпадает" in p.message and ".conditions[0].name" in p.where
                        for p in hits)

    # черновик заявки судится X02 по всему каталогу, но имён не занимает
    def m3(d):
        draft = _draft(d)
        draft["params"] = [{"name": "max_duty_duration", "unit": "duration_minutes",
                            "relation": "max", "qualifier": None, "note": None},
                           {"name": "max_block_time", "unit": "duration_minutes",
                            "relation": "max", "qualifier": None, "note": None}]
        second = _draft(d, "second_draft")
        second["params"] = [{"name": "max_block_time", "unit": "duration_minutes",
                             "relation": "max", "qualifier": None, "note": None}]
        d["requests.json"]["requests"] = [
            _request(draft=draft),
            _request(record_id="second_draft", draft=second, request_id="req-0002")]
    probs = _problems(tmp_path, m3)
    where = {(p.where, p.code, p.severity) for p in probs}
    assert ("requests/req-0001/draft.params[0].name", "X02", "error") in where
    assert ("requests/req-0001/draft.params[1].name", "D03", "warning") in where
    assert not any(p.where.startswith("requests/req-0002") and p.code == "X02"
                   for p in probs), "черновик занял имя для следующей заявки"


def test_x03_violation_unit_vs_kind(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["violation_unit"] = "duty"
        _fam(d, "layover_penalty")["violation_unit"] = None
    probs = _problems(tmp_path, m)
    assert {p.where for p in probs if p.code == "X03"} == {
        "families/duty_limits.violation_unit", "families/layover_penalty.violation_unit"}


def test_x04_empty_executors(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["executor_kinds"] = []
        _crit(d, "min_person_days")["computable_by"] = []
    probs = _problems(tmp_path, m)
    assert len([p for p in probs if p.code == "X04"]) == 2


def test_x05_stand_task_form(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["stand_tasks"] = [
            {"id": "st-1", "world": {"form": "schedule", "flights": [], "crews": [],
                                     "params": {}, "history": None, "horizon": None},
             "expected": {"kotlin_knob": {"matches": 1}, "kotlin_constraint": {"x": 1}},
             "note": None},
            {"id": "st-1", "world": {"form": "nope"}, "expected": {"builder_method": {"value": 1, "unit": "min"}},
             "note": None}]
    msgs = [p.message for p in _problems(tmp_path, m) if p.code == "X05"]
    assert any("стендом не судится" in m_ for m_ in msgs)
    assert any("нужны поля" in m_ for m_ in msgs)
    assert any("повтор id" in m_ for m_ in msgs)
    assert any("неизвестна" in m_ for m_ in msgs)


def test_x06_subject_words(tmp_path):
    def m(d):
        _fam(d, "rest_floors")["subject_words"] = ["Отдых", "отдых", "резерв"]
        _fam(d, "duty_limits")["subject_words"] = []
        d["dictionaries.json"]["lexicon_order"].remove("duty_limits")
    msgs = [p.message for p in _problems(tmp_path, m) if p.code == "X06"]
    assert any("заглавной" in m_ for m_ in msgs)
    assert any("уже у семьи" in m_ for m_ in msgs)
    assert any("пустой список" in m_ for m_ in msgs)


def test_x06_x07_gates(tmp_path):
    """Включённые гейты делают X06/X07 ошибками. Задачи со шага 4 есть у
    всех записей, поэтому X07 показывают ДВЕ записи с очищенными
    задачами: включение флага на живом каталоге сегодня не даёт ни одной
    ошибки — это и есть повод не включать его раньше 1.0.0."""
    def m(d):
        d["catalog.json"]["gates"] = {"stand_tasks_required": True,
                                      "subject_words_required": True}
        _fam(d, "duty_limits")["stand_tasks"] = []
        _crit(d, "min_person_days")["stand_tasks"] = []
    folder = _broken(tmp_path, m)
    cat = tc.load(folder)
    probs = cat.validate()
    assert {"X06", "X07"} <= _codes(probs)
    assert len([p for p in probs if p.code == "X07"]) == 2
    # Ожидание X06 — ИЗ КАТАЛОГА копии, а не литералом: число семей без
    # слов-носителей растёт с каталогом (0.4.0: первая семья конвейера
    # duty_series_limit пришла без subject_words — литерал «3» краснел
    # ростом каталога, а не правилом).
    without_words = [f["id"] for f in cat.families_full()
                     if not f.get("subject_words")]
    assert without_words, "в копии нет семьи без слов-носителей — стенд пуст"
    assert sorted(p.where.split(".")[0].split("/")[-1]
                  for p in probs if p.code == "X06") == sorted(without_words)

    def only_gates(d):
        d["catalog.json"]["gates"] = {"stand_tasks_required": True,
                                      "subject_words_required": False}
    assert "X07" not in _codes(_problems(tmp_path, only_gates), None)


def test_x08_history_order(tmp_path):
    def m(d):
        f = _fam(d, "duty_limits")
        f["history"].reverse()
        _fam(d, "rest_floors")["history"] = []
    probs = _problems(tmp_path, m)
    assert len([p for p in probs if p.code == "X08"]) >= 2


def test_x09_qualifier_vs_unit_and_dictionary(tmp_path):
    """X09: уточнитель согласован с единицей параметра; area требует
    непустого словаря area_qualifiers."""
    def m(d):
        cw = _fam(d, "connection_windows")
        next(p for p in cw["params"] if p["name"] == "min_turnaround")["unit"] = "count"
        rf = _fam(d, "reserve_floors")
        next(p for p in rf["params"] if p["name"] == "reserve_crews")["qualifier"] = "area"
    probs = _problems(tmp_path, m)
    x09 = {p.where for p in probs if p.code == "X09"}
    assert any(w.startswith("families/connection_windows.params[") for w in x09)
    assert any(w.startswith("families/reserve_floors.params[") for w in x09)
    assert all(w.endswith(".qualifier") for w in x09)

    def m2(d):
        d["dictionaries.json"]["area_qualifiers"] = []
    probs = _problems(tmp_path, m2)
    assert {"D02", "X09"} <= _codes(probs)
    assert any(p.code == "X09" and "area_qualifiers" in p.message for p in probs)

    def m3(d):                                            # неизвестный уточнитель — только D01
        rf = _fam(d, "reserve_floors")
        next(p for p in rf["params"] if p["name"] == "reserve_crews")["qualifier"] = "нет"
    probs = _problems(tmp_path, m3)
    assert "D01" in _codes(probs) and "X09" not in _codes(probs)


def test_x10_x11_forms_and_check_templates(tmp_path):
    def m(d):
        cw = _fam(d, "calendar_windows")
        cw["rule_form"]["fields"].append(dict(cw["rule_form"]["fields"][0]))
        _fam(d, "duty_limits")["check_templates"] = ["нет-такого"]
        _fam(d, "connection_windows")["check_templates"] = ["уникальность"]
        _fam(d, "rest_floors")["check_templates"] = []
    probs = _problems(tmp_path, m)
    assert "X10" in _codes(probs)
    x11 = {p.where for p in probs if p.code == "X11"}
    assert "families/duty_limits.check_templates[0]" in x11
    assert "families/connection_windows.check_templates" in x11
    assert "families/rest_floors.check_templates" in x11


def test_x12_param_sets(tmp_path):
    def m(d):
        ns = _fam(d, "night_series")
        ns["param_sets"][0]["params"] = ["night_duty_start"]
        ns["param_sets"][1] = {"id": "consecutive", "params": ["нет"], "note": None}
    msgs = [p.message for p in _problems(tmp_path, m) if p.code == "X12"]
    assert any("повтор id" in m_ for m_ in msgs)
    assert any("не из params" in m_ for m_ in msgs)
    assert any("без набора" in m_ for m_ in msgs)


def test_x13_nature_constraints(tmp_path):
    def m(d):
        _fam(d, "night_series")["executor_kinds"] = ["check_code", "check_template"]
        ts = _fam(d, "time_scale")
        ts["kind"], ts["violation_unit"] = "SOFT", "event"
    probs = _problems(tmp_path, m)
    assert {p.where for p in probs if p.code == "X13"} == {
        "families/night_series.executor_kinds", "families/time_scale.kind"}


def test_x14_instance_form(tmp_path):
    def m(d):
        c = _crit(d, "rank_normalized_cost")
        c["instance_form"]["fields"].append(dict(c["instance_form"]["fields"][0]))
        c["instance_form"]["fields"] = [f for f in c["instance_form"]["fields"]
                                        if f["name"] != "rates"]
    probs = _problems(tmp_path, m)
    msgs = [p.message for p in probs if p.code == "X14"]
    assert any("повтор" in m_ for m_ in msgs)
    assert any("поля ставок" in m_ for m_ in msgs)


def test_карта_единиц_нарушения_судится_d01(tmp_path):
    """Ловушка выпуска 0.5.0: единицы нарушения — КАРТА «код → слово», и
    членство судится по её КЛЮЧАМ.

    Читай `tokens()` только списки — карта дала бы пустой перечень,
    `check_token` посчитал бы сам словарь предметом D02 и пропустил
    ЛЮБОЕ значение: D01 по единице нарушения замолчал бы, а молчащее
    правило хуже отсутствующего."""
    def m(d):
        единицы = d["dictionaries.json"]["violation_units"]
        d["dictionaries.json"]["violation_units"] = {
            k: v for k, v in единицы.items() if k != "night"}
    probs = _problems(tmp_path, m)
    where = {(p.where, p.code) for p in probs}
    assert ("families/layover_penalty.violation_unit", "D01") in where
    сообщение = next(p.message for p in probs if p.code == "D01"
                     and p.where == "families/layover_penalty.violation_unit")
    assert "'night'" in сообщение and "day" in сообщение, сообщение
    assert "сутки" not in сообщение, (
        "словарь судится кодами; слово единицы живёт рядом с кодом")

    # прежний вид словаря (перечень) — беда формы, а не значения
    def m2(d):
        d["dictionaries.json"]["violation_units"] = sorted(
            d["dictionaries.json"]["violation_units"])
    беды = {(p.where, p.code) for p in _problems(tmp_path, m2)}
    assert ("dictionaries.violation_units", "D02") in беды

    # слово единицы пустым не бывает: переводить нечем
    def m3(d):
        d["dictionaries.json"]["violation_units"]["night"] = ""
    беды = {(p.where, p.code) for p in _problems(tmp_path, m3)}
    assert ("dictionaries.violation_units.night", "D02") in беды


def test_x19_x15_чтение_истории(tmp_path):
    """X19: семья истории обязана назвать, что читает. X15: форма
    чтения — вид словарём (D01) и потребность непустым текстом."""
    def m(d):
        _fam(d, "night_series")["reads_history"] = None
        _fam(d, "rolling_rest")["reads_history"]["kind"] = "иной"
        _fam(d, "calendar_windows")["reads_history"]["need"] = "   "
    probs = _problems(tmp_path, m)
    where = {(p.where, p.code) for p in probs}
    assert ("families/night_series.reads_history", "X19") in where
    assert ("families/rolling_rest.reads_history.kind", "D01") in where
    assert ("families/calendar_windows.reads_history.need", "X15") in where
    # Обратное X19 не требуется: семья границы горизонта читает историю
    # при природе «расписание» — rest_floors в живом каталоге такова.
    чистый = _problems(tmp_path, lambda d: None)
    assert not [p for p in чистый if p.code in ("X19", "X15")]
    assert tc.load(CATALOG).family("rest_floors")["nature"] == "schedule"
    assert tc.load(CATALOG).family("rest_floors")["reads_history"]


def test_x16_размер_нарушения(tmp_path):
    """Размер нарушения задан ⇔ вид SOFT — как единица нарушения (X03)."""
    def m(d):
        _fam(d, "duty_limits")["violation_size"] = "count"
        _fam(d, "layover_penalty")["violation_size"] = None
        _fam(d, "balanced_workload")["violation_size"] = "огромный"
    probs = _problems(tmp_path, m)
    assert {p.where for p in probs if p.code == "X16"} == {
        "families/duty_limits.violation_size",
        "families/layover_penalty.violation_size"}
    assert ("families/balanced_workload.violation_size", "D01") in (
        {(p.where, p.code) for p in probs})


def test_x17_свидетель_природы(tmp_path):
    """Природу подтверждает НЕЗАВИСИМЫЙ свидетель по таблице, а не сама
    запись; истории свидетель заявки не положен (решение §5 проекта
    выпуска: историю читает решатель, и без его формы семья истории не
    работает)."""
    def m(d):
        _fam(d, "night_series")["nature_confirmed_by"] = "change_request"
        _fam(d, "crew_composition")["nature_confirmed_by"] = "solver_tables"
        _fam(d, "duty_limits")["nature_confirmed_by"] = "certifier_registry"
        _fam(d, "time_scale")["nature_confirmed_by"] = "кто-нибудь"
    probs = _problems(tmp_path, m)
    assert {p.where for p in probs if p.code == "X17"} == {
        "families/night_series.nature_confirmed_by",
        "families/crew_composition.nature_confirmed_by",
        "families/duty_limits.nature_confirmed_by"}
    история = next(p.message for p in probs if p.code == "X17"
                   and p.where.startswith("families/night_series"))
    assert "подтверждает свидетель solver_tables;" in история, история
    # значение вне словаря — одна беда и один доклад: D01, не D01 ⊕ X17
    вне = [p for p in probs if p.where.startswith("families/time_scale")]
    assert [p.code for p in вне] == ["D01"], [str(p) for p in вне]


def test_x18_запись_из_заявки_не_опирается_на_перечни(tmp_path):
    """Замечание CDPM 10.09.2026 (принято): запись, пришедшую конвейером
    пополнения, перечни сертификата ростера не подтверждают — их ведёт
    тот же контур, и природа новой семьи держалась бы на её собственном
    слове. Свидетель такой записи — вердикт судьи заявки."""
    def m(d):
        люди = _fam(d, "crew_composition")
        люди["origin"] = dict(люди["origin"], kind="заявка")
    беды = {(p.where, p.code) for p in _problems(tmp_path, m)}
    assert ("families/crew_composition.nature_confirmed_by", "X18") in беды

    def m2(d):
        люди = _fam(d, "crew_composition")
        люди["origin"] = dict(люди["origin"], kind="заявка")
        люди["nature_confirmed_by"] = "change_request"
    assert not [p for p in _problems(tmp_path, m2)
                if p.code in ("X17", "X18")]

    # Семья РАСПИСАНИЯ, пришедшая заявкой, законна с отрицательным
    # свидетельством: «ни в таблицах решателя, ни в перечнях
    # сертификатора» проверяет код (L15), и подделать это заявка не
    # может. Такие записи в каталоге есть, и X18 о них молчит.
    живой = tc.load(CATALOG)
    заявкой = [f for f, r in живой.families_meta(status=None).items()
               if живой.family(f)["origin"]["kind"] == "заявка"]
    assert заявкой, "в каталоге нет ни одной записи из заявки"
    assert not [p for p in _problems(tmp_path, lambda d: None)
                if p.code == "X18"]


# -------------------------------------------------------------- H
def test_h01_header_rules(tmp_path):
    def m(d):
        h = d["catalog.json"]
        h["version"] = "0.1.0"                          # выпуск не владельцем
        h["released_by"] = "сессия COSMOS"
        h["released_at"] = "07.09.2026"
        del h["gates"]["stand_tasks_required"]
    probs = _problems(tmp_path, m)
    h01 = {p.where for p in probs if p.code == "H01"}
    assert {"catalog.json/released_by", "catalog.json/released_at",
            "catalog.json/gates.stand_tasks_required"} <= h01

    def m2(d):
        d["catalog.json"]["version"] = "0.1"
        d["catalog.json"]["released_by"] = "кто-то"
    assert "H01" in _codes(_problems(tmp_path, m2))


def test_h02_fingerprint_mismatch(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["subject"] = "другой предмет"
    probs = _problems(tmp_path, m, refresh=False)
    assert [p.where for p in probs if p.code == "H02"] == ["catalog.json/fingerprint"]
    assert "правлен мимо инструмента" in [p for p in probs if p.code == "H02"][0].message


def test_h03_sections_and_versions(tmp_path):
    def m(d):
        d["catalog.json"]["sections"]["requests"] = "нет.json"
        d["versions.json"]["versions"] = [
            {"version": "0.1.0", "fingerprint": "0" * 64, "released_at": "2026-09-07",
             "released_by": "владелец", "code": "4df755e", "changes": []},
            {"version": "0.1.0-draft.2", "fingerprint": "0" * 64, "released_at": "2026-09-08",
             "released_by": "владелец", "code": "4df755e", "changes": []},
            {"version": "0.0.9", "fingerprint": "0" * 64, "released_at": "2026-09-09",
             "released_by": "владелец", "code": "4df755e", "changes": []}]
    dst = _broken(tmp_path, m)
    with pytest.raises(tc.CatalogNotFound):
        tc.load(dst)                                   # нет файла раздела — не читается
    (dst / "нет.json").write_text('{"form_version": 1, "requests": []}', encoding="utf-8")
    probs = tc.load(dst).validate()
    msgs = [p.message for p in probs if p.code == "H03"]
    assert any("черновики" in m_ for m_ in msgs)
    assert any("возрастать" in m_ for m_ in msgs)


# -------------------------------------------------------------- R
def test_r01_r02_request_states(tmp_path):
    def m(d):
        d["requests.json"]["requests"] = [
            _request(draft=_draft(d)),
            _request(draft=_draft(d), request_id="req-0001"),           # повтор id
            _request(record_id="duty_limits", state="принята", request_id="req-0002",
                     draft=None),                                        # без решения, без видов
            _request(record_id="ghost", state="есть", request_id="req-0003",
                     decision={"by": "владелец", "at": "2026-09-11", "words": "да"}),
            _request(record_id="duty_limits", state="исполнитель заявлен", request_id="req-0004",
                     executor_kinds_requested=["kotlin_metric"],
                     decision={"by": "владелец", "at": "2026-09-11", "words": "да"})]
    probs = _problems(tmp_path, m)
    assert "R01" in _codes(probs)
    r02 = [p.message for p in probs if p.code == "R02"]
    assert any("решения владельца" in m_ for m_ in r02)
    assert any("требует записи" in m_ for m_ in r02)
    assert any("ключа CR" in m_ for m_ in r02)
    assert any("не объявлены записью" in m_ for m_ in r02)
    assert any("без черновика" in m_ for m_ in r02)


def test_r02_запись_с_тем_же_id_при_заявке_в_работе_законна(tmp_path):
    """Кандидат конвейера в судимом каталоге («живой ⊕ кандидаты»):
    запись уже есть, а заявка с черновиком — «исполнитель заявлен» — не
    R02 (09.09.2026: первый кандидат новой семьи duty_series_limit красил
    временный каталог этой строкой, гейты CR не судились); та же заявка
    «подана» — R02 «запись с таким id уже есть»."""
    def m(d):
        dr = _draft(d)
        dr["id"] = "duty_limits"
        d["requests.json"]["requests"] = [
            _request(record_id="duty_limits", state="подана",
                     request_id="req-0001", draft=dict(dr)),
            _request(record_id="duty_limits", state="исполнитель заявлен",
                     request_id="req-0002", draft=dict(dr),
                     cr_key="cr12345678",
                     decision={"by": "владелец", "at": "2026-09-11", "words": "да"})]
    probs = _problems(tmp_path, m)
    same_id = [p.where for p in probs if p.code == "R02" and "уже есть" in p.message]
    assert same_id and all(w.startswith("requests/req-0001") for w in same_id), \
        [(p.where, p.message) for p in probs if p.code == "R02"]


def test_r03_draft_is_judged(tmp_path):
    def m(d):
        draft = _draft(d)
        draft["id"] = "other_id"
        draft["semantics"]["text"] = "штраф считает Builder.chain_cost(x) по цепочке"
        draft["kind"] = "MEDIUM"
        d["requests.json"]["requests"] = [_request(draft=draft)]
    probs = _problems(tmp_path, m)
    wheres = {p.where: p.code for p in probs if p.where.startswith("requests/req-0001/draft")}
    assert wheres.get("requests/req-0001/draft.id") == "R03"
    assert wheres.get("requests/req-0001/draft.semantics.text") == "A05"
    assert wheres.get("requests/req-0001/draft.kind") == "D01"


# -------------------------------------------------------------- A
@pytest.mark.parametrize("text,code", [
    ("отдых в базе SVX не менее нормы", "A01"),
    ("отдых в базе ТАС не менее нормы", "A01"),          # кириллический код станции
    ("допуск на A320 и B737", "A01"),
    ("см. п. 3.4 положения", "A01"),
    ("ключ R-S12-rest", "A01"),
    ("доплата 1500 руб. за ночёвку", "A01"),
    ("приказ от 01.09.2026 № 12", "A01"),
    ("«" + "а" * 90 + "»", "A01"),
    ("считает Builder.chain_cost(x)", "A05"),
    ("см. roster_certify.py", "A05"),
    ("ключ --max-segs", "A05"),
    ("метод dutyExtensionOf решателя", "A05"),
    ("файл vmp/run/logic_checks", "A05"),
    ("смена не более 12 ч", "A06"),
])
def test_a_rules_in_title(tmp_path, text, code):
    def m(d):
        _fam(d, "duty_limits")["title"] = "предел: " + text
    probs = _problems(tmp_path, m)
    assert any(p.code == code and p.where == "families/duty_limits.title"
               and p.severity == "error" for p in probs), [str(p) for p in probs if p.code != "X07"]


def test_a06_is_warning_in_history(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["history"][0]["note"] = "порог был не более 12 ч по документу"
    probs = _problems(tmp_path, m)
    hits = [p for p in probs if p.code == "A06"]
    assert hits and all(p.severity == "warning" for p in hits)


def test_a02_forbidden_terms(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["semantics"]["text"] = "как у перевозчика UZ"
    probs = _problems(tmp_path, m, forbidden_terms=("UZ", "HY"))
    assert any(p.code == "A02" for p in probs)
    assert not any(p.code == "A02" for p in _problems(tmp_path, m))


def test_a03_world_codes_synthetic_only(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["stand_tasks"] = [
            {"id": "st-1",
             "world": {"form": "schedule", "params": {}, "history": None, "horizon": None,
                       "flights": [{"id": "f1", "from": "SVX", "to": "ST2", "dep": 0,
                                    "arr": 60, "fleet_type": "A320"}],
                       "crews": [{"id": "12345", "base": "ST1", "rank": "CP", "ratings": ["T1"]}]},
             "expected": {"kotlin_constraint": {"matches": 0}}, "note": None}]
    msgs = [p.message for p in _problems(tmp_path, m) if p.code == "A03"]
    assert len(msgs) == 3
    good = {"id": "st-1",
            "world": {"form": "schedule", "params": {}, "history": None, "horizon": None,
                      "flights": [{"id": "f1", "from": "ST1", "to": "ST2", "dep": 0,
                                   "arr": 60, "fleet_type": "T1"}],
                      "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}]},
            "expected": {"kotlin_constraint": {"matches": 0}}, "note": None}

    def m2(d):
        _fam(d, "duty_limits")["stand_tasks"] = [good]
    probs = _problems(tmp_path, m2)
    assert _codes(probs) == set()
    assert "families/duty_limits.stand_tasks" not in {p.where for p in probs}


def test_a04_generalized_origin_names_document_class(tmp_path):
    def m(d):
        _fam(d, "duty_limits")["origin"] = {"kind": "обобщено из ИДАК", "date": "2026-09-01",
                                            "note": "так сложилось", "confirmed_by": "владелец"}
    assert "A04" in _codes(_problems(tmp_path, m))

    def m2(d):
        _fam(d, "duty_limits")["origin"] = {"kind": "обобщено из ИДАК", "date": "2026-09-01",
                                            "note": "обобщение положений о рабочем времени",
                                            "confirmed_by": "владелец"}
    assert "A04" not in _codes(_problems(tmp_path, m2))
