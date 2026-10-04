"""CDPM-429: landed citations, not every synonym of their unit.

Valid temporary catalogues; actual completeness reader. No external services.
"""
import copy

import pytest

from cdpm import family_params as fp
from template_catalog import semantics as sem
from test_k01_semantic_transition import (
    FAMILY, PARAM, pair, change_scale, clean_bridge,
)


def change_minute(docs):
    entry = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
    for word, scale in list(entry["scale"].items()):
        if scale == 1:
            entry["scale"][word] = 2


@pytest.mark.parametrize("quote,edit,expected", [
    ("960 минут", change_scale, fp.CONTRACT_COMPATIBLE),
    ("16 ч", change_scale, fp.CONTRACT_INCOMPATIBLE),
    ("960 минут", change_minute, fp.CONTRACT_INCOMPATIBLE),
    ("960 МИНУТ.", change_scale, fp.CONTRACT_COMPATIBLE),
    ("960минут", change_scale, fp.CONTRACT_COMPATIBLE),
    ("16 Ч.", change_scale, fp.CONTRACT_INCOMPATIBLE),
    ("16ч", change_scale, fp.CONTRACT_INCOMPATIBLE),
    (None, change_scale, fp.CONTRACT_UNPROVEN),
    ("960", change_scale, fp.CONTRACT_UNPROVEN),
    ("960 неизвестных", change_scale, fp.CONTRACT_UNPROVEN),
])
def test_citation_change_pair(tmp_path, monkeypatch, quote, edit, expected):
    doc, _old, _new = pair(tmp_path, monkeypatch, edit)
    doc["rules"][-1]["template_fit"]["params"][PARAM]["quote"] = quote
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == expected, got["contract"]
    assert bool(got["contract"]["differences"]) is (expected == fp.CONTRACT_INCOMPATIBLE)
    assert got["curable"] is (expected != fp.CONTRACT_INCOMPATIBLE)


@pytest.mark.parametrize("reverse", [False, True])
def test_same_revision_citations_do_not_hide_incompatible_neighbour(tmp_path, monkeypatch, reverse):
    doc, _old, _new = pair(tmp_path, monkeypatch, change_scale)
    neighbour = copy.deepcopy(doc["rules"][-1])
    neighbour["id"] = "R-SECOND-CITATION"
    neighbour["template_fit"]["params"][PARAM]["quote"] = "16 ч"
    doc["rules"].append(neighbour)
    if reverse:
        doc["rules"].reverse()
    assert fp.family_completeness(doc)[FAMILY]["contract_state"] == fp.CONTRACT_INCOMPATIBLE


def test_record_comparison_without_citations_still_checks_all_words(tmp_path, monkeypatch):
    _doc, old, new = pair(tmp_path, monkeypatch, change_scale)
    got = sem.readings_compare(sem.record_reading(old, "families", FAMILY),
                               sem.record_reading(new, "families", FAMILY), carried={PARAM})
    assert got["verdict"] == sem.INCOMPATIBLE


@pytest.mark.parametrize("used", [False, True])
def test_removed_word_only_affects_citation_using_it(tmp_path, monkeypatch, used):
    def remove(docs):
        unit = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
        unit["words"].remove("часов")
        unit["scale"].pop("часов")
    doc, _old, _new = pair(tmp_path, monkeypatch, remove)
    doc["rules"][-1]["template_fit"]["params"][PARAM]["quote"] = "16 часов" if used else "960 минут"
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == (fp.CONTRACT_UNPROVEN if used else fp.CONTRACT_COMPATIBLE)
    assert got["contract"]["differences"] == []


@pytest.mark.parametrize("quotes,used,complete", [
    (["16ч30минут"], {"ч", "минут"}, True),
    (["1 кредитных часов"], {"кредитных часов"}, True),
    (["16 часов"], {"часов"}, True),
    (["960 минут", None], {"минут"}, False),
    (["960 минут, 16 неизвестных"], {"минут"}, False),
    (["960 минутный"], set(), False),
])
def test_citation_word_boundaries_are_conservative(quotes, used, complete):
    assert sem._citation_unit_words(
        quotes, {"ч", "час", "часов", "минут", "кредитных часов"}) == (used, complete)


@pytest.mark.parametrize("quotes", [{PARAM: None}, {PARAM: []}, {}])
def test_explicit_missing_citation_is_not_global_contract_comparison(tmp_path, monkeypatch, quotes):
    _doc, old, new = pair(tmp_path, monkeypatch, change_scale)
    got = sem.readings_compare(sem.record_reading(old, "families", FAMILY),
                               sem.record_reading(new, "families", FAMILY),
                               carried={PARAM}, citations=quotes)
    assert got["verdict"] == sem.UNPROVEN


@pytest.mark.parametrize("quotes", [[None], ["960"]])
def test_added_synonym_does_not_require_old_citation(quotes):
    old = {"token": "duration_minutes", "canon": "минута", "form": "integer_nonneg",
           "words": {"минут": 1, "ч": 60}, "resolved": True}
    new = copy.deepcopy(old)
    new["words"]["часов"] = 60
    differences = []
    sem._compare_unit(PARAM, "units.duration_minutes", old, new, differences, quotes)
    assert differences == []
