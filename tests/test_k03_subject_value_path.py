"""Catalogue subjects reach scalar application without deleting other scope."""
import copy
import json

import pytest

from cdpm import answer_parsing, answers, catalog_bridge as cb, family_params as fp, model, provenance
from test_cdpm_a03m_provenance import _doc
from test_k03_subject_vocabulary import selected_catalogue, vocabulary


VALUES = {"duration_minutes": ("10 часов", 600), "time_of_day_minutes": ("22:00", 1320),
          "count": ("3", 3), "credit_hours": ("75 часов", 75)}


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


def question(family, name):
    return {"id": f"Q-3b-параметр-{family}-{name}"}


def test_all_numeric_phrases_preserve_their_healthy_bare_value(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    failed, checked, excluded = [], 0, []
    for family, params in vocabulary()["families"].items():
        for name, phrases in params.items():
            unit = fp._param_spec(family, name)["unit"]
            if unit not in VALUES:
                excluded.append((family, name, unit, len(phrases)))
                continue
            tail, value = VALUES[unit]
            q = question(family, name)
            baseline = answer_parsing.parameter(q, tail)
            assert baseline["value"] == value and not baseline["ambiguous"], (family, name, baseline)
            for phrase in phrases:
                raw = f"{phrase} — {tail}"
                parsed = answer_parsing.parameter(q, raw)
                checked += 1
                if parsed["ambiguous"] or parsed["value"] != value:
                    failed.append((family, name, phrase, parsed["problems"]))
                    continue
                assert parsed["quote"] == raw and "condition" not in parsed
                assert provenance.value_problems(family, name, {"value": value, "quote": raw}, raw) == []
    assert checked == 152  # 164 minus 5 scalar-code and 7 code-list phrases
    assert sum(x[3] for x in excluded) == 12
    assert not failed, failed


@pytest.mark.parametrize("family,name,raw,value", [
    ("rest_floors", "min_rest_home", "В базе — 10 часов.", 600),
    ("rest_floors", "min_rest_home", "На базе — 10 часов.", 600),
    ("days_off_floor", "min_days_off", "Выходных дней в месяц — 3.", 3),
    ("deadhead_rules", "max_deadheads_per_day", "Перегонов пассажиром за сутки — 3.", 3),
    ("span_accumulators", "max_monthly_block", "Налёт за месяц — 10 часов.", 600),
    ("rest_floors", "min_rest_home", "Не менее 10 часов. Не менее 10 часов.", 600),
    ("rest_floors", "min_rest_home", "не менее 10 часов, не менее 10 часов", 600),
    ("rest_floors", "min_rest_home", "не менее 10 часов и не менее 10 часов", 600),
])
def test_apply_source_retry_and_restore_share_subject_aware_value(
        tmp_path, monkeypatch, family, name, raw, value):
    selected_catalogue(tmp_path, monkeypatch)
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule["template_fit"] = {"category": "A", "registry_rev": model.registry_fingerprint(),
                            "family": family, "params": {}}
    fp.ensure_family_param_questions(doc)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    q = next(q for q in doc["questions"] if fp.question_target(q) == (family, name))
    qid = q["id"]
    baseline = copy.deepcopy(doc)
    tail, _ = VALUES[fp._param_spec(family, name)["unit"]]
    assert answers.apply_answers(baseline, {qid: tail})["applied"] == 1
    result = answers.apply_answers(doc, {qid: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0, result
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    cell = rule["template_fit"]["params"][name]
    assert cell["value"] == value and cell["quote"] in raw
    assert provenance.cell_problems(doc, "R-R", family, name, cell) == []
    receipt, expected = copy.deepcopy(q["answer"]), copy.deepcopy(cell)
    answers.apply_answers(doc, {qid: raw})
    assert q["answer"] == receipt
    rule["template_fit"]["params"].pop(name)
    fp.ensure_family_param_questions(doc)
    assert rule["template_fit"]["params"][name] == expected
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("raw", [
    "не менее 10 часов и не более 10 часов",
    "не менее 10 часов, не менее 12 часов",
    "Не менее 10 часов. Не менее 12 часов.",
])
def test_repeated_bounds_do_not_hide_conflicting_relation_or_value(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    parsed = answer_parsing.parameter(question("rest_floors", "min_rest_home"), raw)
    assert parsed["ambiguous"] and parsed["value"] is None
    assert provenance.value_problems("rest_floors", "min_rest_home",
                                     {"value": 600, "quote": "10 часов"}, raw)


@pytest.mark.parametrize("family,name,raw", [
    ("rest_floors", "min_rest_home", "В базе SVO — 10 часов"),
    ("rest_floors", "min_rest_home", "На базе только для пилотов — 10 часов"),
    ("deadhead_rules", "max_deadheads_per_day", "Перегонов пассажиром за сутки — 3 за неделю"),
    ("days_off_floor", "min_days_off", "Выходных дней в месяц — 3 только для пилотов"),
    ("days_off_floor", "min_days_off", "Выходных дней в месяц — 3. Только для пилотов."),
    ("span_accumulators", "max_monthly_block", "Налёт за месяц — 10 часов за неделю"),
])
def test_additional_qualification_is_not_part_of_the_catalogue_subject(tmp_path, monkeypatch, family, name, raw):
    selected_catalogue(tmp_path, monkeypatch)
    parsed = answer_parsing.parameter(question(family, name), raw)
    assert parsed["ambiguous"] and parsed["value"] is None
    # A genuine numeric substring cannot erase the full source's restriction.
    unit = fp._param_spec(family, name)["unit"]
    quote, value = VALUES[unit]
    if quote in raw:
        assert provenance.value_problems(family, name, {"value": value, "quote": quote}, raw)
