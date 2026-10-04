"""CDPM-251: a scalar answer must not silently discard applicability.

Synthetic in-memory question -> answer -> carrier -> consumer checks.
This is a refusal guard for declared scope syntax, not a condition compiler.
"""
import copy

import pytest

from cdpm import answers, family_params as fp, model
from cdpm.sample import sample_model
from vmp import execution_manifest as execution
from _k02_work_budget import k02_work_budget


VALUES = [
    ("duration_minutes", "720 минут", 720),
    ("time_of_day_minutes", "09:00", 540),
    ("count", "3", 3),
    ("credit_hours", "75 часов кредита", 75),
    ("hours_per_credit_hour", "1,5", 1.5),
]
SCOPES = [
    "для базы SVO", "для бортпроводников", "при согласии экипажа",
    "по вторникам", "по понедельникам", "в среду", "в четверг",
    "по пятницам", "по субботам", "по воскресеньям",
    "если экипаж согласен", "когда рейс ночной", "кроме базы SVO",
    "за исключением базы DME", "только ночью", "лишь зимой",
    "в случае задержки", "в зависимости от роли", "на базе SVO",
    "с согласия экипажа", "без согласия экипажа",
]
POSITIONS = ["{value} {scope}", "{scope}: {value}",
             "{value}. {scope}.", "{scope}. {value}",
             "{value}\n({scope})"]


@pytest.mark.parametrize("unit,raw,want", VALUES)
@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("position", POSITIONS)
def test_scope_anywhere_cannot_become_an_unconditional_scalar(
        unit, raw, want, scope, position):
    answer = position.format(value=raw, scope=scope)
    assert fp.parse_value(answer, unit, fp.default_domain(unit)) is None


def _prepared(unit):
    family, param, given = {
        "duration_minutes": ("rest_floors", "min_rest_home",
                             {"min_rest_outstation": 600}),
        "time_of_day_minutes": ("night_series", "night_duty_end",
                                {"max_consecutive_night_duties": 3,
                                 "night_duty_start": 1320}),
        "count": ("days_off_floor", "days_off_window_days",
                  {"min_days_off_in_window": 2}),
        "credit_hours": ("balanced_workload", "workload_spread_threshold", {}),
    }[unit]
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    # Remove unrelated sample legacy parameters so the consumer assertion
    # tests this exact answer's carrier, not another source's precedence.
    rule.pop("params", None)
    rule["template_fit"] = {
        "category": "A", "registry_rev": model.registry_fingerprint(),
        "family": family,
        "params": {name: {"value": val, "quote": "synthetic baseline"}
                   for name, val in given.items()},
    }
    fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"]
             if fp.question_target(q) == (family, param))
    assert model.is_open_question(q) and q["id"] in rule["blockers"]
    return doc, rule, q, param


@pytest.mark.parametrize("unit,raw,want", VALUES[:4])
@pytest.mark.parametrize("scope", ["для базы SVO", "для бортпроводников",
                                  "по вторникам", "при согласии экипажа"])
def test_answer_keeps_raw_open_question_blocker_and_no_consumer_value(
        unit, raw, want, scope):
    doc, rule, q, param = _prepared(unit)
    before = copy.deepcopy(rule)
    raw += " " + scope
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert q["answer"]["raw"] == raw
    assert q["answer"]["status"] == "не разобран"
    assert "оговорк" in q["answer"]["note"]
    assert scope in q["answer"]["note"]
    assert model.is_open_question(q) and q["id"] in rule["blockers"]
    assert rule == before
    assert param not in {p["name"] for p in execution.rule_params(rule)}


@pytest.mark.parametrize("unit,raw,want", VALUES[:4])
def test_plain_answer_reaches_consumer_with_original_value(unit, raw, want):
    doc, rule, q, param = _prepared(unit)
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert not model.is_open_question(q) and q["id"] not in rule["blockers"]
    cell = rule["template_fit"]["params"][param]
    assert cell["value"] == want and cell["quote"] == raw
    received = next(p for p in execution.rule_params(rule) if p["name"] == param)
    assert received["value"] == want and received["quote"] == raw


@pytest.mark.parametrize("unit,raw,want", VALUES)
def test_canonical_string_and_repair_do_not_bypass_scope(unit, raw, want):
    value, problem = fp.canonical_value(raw + " для базы SVO", "", unit)
    assert value is None and problem


def test_legacy_applied_answer_is_not_restored_without_its_scope():
    doc, rule, q, param = _prepared("duration_minutes")
    q["answer"] = {"raw": "720 минут для базы SVO",
                   "status": "применён", "revision": 1}
    notes = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert param not in rule["template_fit"]["params"]
    assert any("не удалось разобрать" in note for note in notes)


@pytest.mark.parametrize("raw", ["720 минут в неделю", "720 минут за смену"])
def test_period_still_has_its_specific_refusal(raw):
    doc, rule, q, param = _prepared("duration_minutes")
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert "период" in q["answer"]["note"]
    assert param not in rule["template_fit"]["params"]


def test_declared_structural_qualifier_is_not_sent_to_free_text_guard():
    spec = fp._param_spec("reserve_floors", "reserve_crews")
    assert spec["qualifier"] == "base_code"
    cell = {"value": {"SVO": 3, "DME": 2}}
    before = copy.deepcopy(cell)
    assert fp._cell_problem(cell, spec) is None
    assert cell == before


@pytest.mark.parametrize("unit,raw,want", [
    *VALUES, ("duration_minutes", "не более 720 минут", 720),
    ("duration_minutes", "Да, требование есть. Отдых вне базы — 9 часов.", 540),
    ("duration_minutes", "600 минут. Основание — п. 4.2.", 600),
    ("time_of_day_minutes", "с 08:00", 480),
    ("time_of_day_minutes", "08:00", 480),
    ("count", "не более 3 смен подряд", 3),
    ("duration_minutes", "Норма по среднему значению: 720 минут", 720),
])
def test_healthy_numeric_syntax_and_explanations_are_preserved(unit, raw, want):
    assert fp.parse_value(raw, unit, fp.default_domain(unit))[0] == want


@pytest.mark.parametrize("raw", ["B210", "A320, B738", "Airbus A320"])
def test_typed_designations_remain_opaque(raw):
    assert fp.canonical_value(raw, "", "code_list") == (raw, None)


def test_guard_is_reached_by_the_real_answer_path(monkeypatch):
    # A causal witness, not merely a direct helper check: bypass exactly the
    # new guard and the original silent application reappears at the consumer.
    doc, rule, q, param = _prepared("duration_minutes")
    monkeypatch.setattr(fp, "applicability_qualifier", lambda raw, unit: "")
    result = answers.apply_answers(doc, {q["id"]: "720 минут для базы SVO"})
    assert result["applied"] == 1
    received = next(p for p in execution.rule_params(rule) if p["name"] == param)
    assert received["value"] == 720 and received["condition"] is None


@pytest.mark.parametrize("raw", [
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 12:00.",
    "Базовый предел — 10 часов. С согласия экипажа допускается продление до 720 минут.",
])
def test_separate_scoped_extension_does_not_qualify_the_labelled_base(raw):
    assert fp.parse_value(raw, "duration_minutes")[0] == 600


@pytest.mark.parametrize("raw", [
    "600 минут. С согласия экипажа допускается продление до 12:00.",
    "Базовый предел для базы SVO — 600 минут. С согласия экипажа допускается продление до 12:00.",
    "Базовый предел — 600 минут; с согласия экипажа допускается продление до 12:00.",
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 08:00.",
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 12:90.",
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 12:00 для базы SVO.",
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 12:00. Для базы SVO.",
    "Базовый предел — 600 минут. С согласия экипажа допускается продление до 12:00. С согласия командира.",
])
def test_extension_exception_cannot_hide_an_unbound_or_second_scope(raw):
    assert fp.parse_value(raw, "duration_minutes") is None


@pytest.mark.parametrize("scope", [
    "для всех", "для всех баз", "для всех членов экипажа",
    "для каждого члена экипажа", "для каждого ЧЭ", "для любого экипажа",
    "для экипажа",
])
@pytest.mark.parametrize("position", ["{value} {scope}", "{scope}: {value}"])
@pytest.mark.parametrize("unit,raw,want", VALUES)
def test_unqualified_universal_scope_keeps_the_scalar(scope, position, unit, raw, want):
    answer = position.format(value=raw, scope=scope)
    got = fp.parse_value(answer, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


@pytest.mark.parametrize("scope", [
    "для всех бортпроводников", "для каждого ЧЭ увеличенного состава",
    "для экипажа базы SVO", "для всех баз в России",
    "для каждого члена экипажа ночного рейса",
    "для любого экипажа, кроме базы SVO",
    "для всех баз, расположенных в России",
])
def test_universal_quantifier_does_not_erase_a_restricted_domain(scope):
    raw = "720 минут " + scope
    assert fp.parse_value(raw, "duration_minutes") is None


@pytest.mark.parametrize("scope", ["для каждого ЧЭ", "для всех баз", "для экипажа"])
def test_universal_answer_reaches_consumer_without_losing_the_quote(scope):
    doc, rule, q, param = _prepared("duration_minutes")
    raw = "не менее 720 минут " + scope
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    received = next(p for p in execution.rule_params(rule) if p["name"] == param)
    assert received["value"] == 720 and received["quote"] == raw
    assert not model.is_open_question(q) and q["id"] not in rule["blockers"]


def test_repeated_universal_clauses_keep_the_linear_work_budget(k02_work_budget):
    raw = "720 минут. " + "Для всех. " * 1000
    with k02_work_budget(raw) as work:
        assert fp.parse_value(raw, "duration_minutes")[0] == 720
    assert work.work <= work.limit
