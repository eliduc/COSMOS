"""K03 / P0 §2.2, §3.3: parameter subjects are catalogue data.

Synthetic dictionaries test the interface, not a published 0.7.0 release.
The contract's home/outstation examples are independent expected subjects.
No model mutation, service, LLM or product calculation is performed.
"""
from __future__ import annotations

import copy

import pytest

import template_catalog as tc
from cdpm import catalog_bridge as bridge


@pytest.fixture
def catalog(tmp_path):
    return tc.Catalog(
        path=tmp_path, header={"version": "0.7.0"}, versions_doc=None,
        docs={
            "families": {"families": [{
                "id": "rest_floors", "status": "есть",
                "subject_words": ["отдых", "rest"],
                "params": [
                    {"name": "min_rest_home", "note": "DO NOT infer words",
                     "subject_words": ["дома", "home base"]},
                    {"name": "min_rest_outstation",
                     "subject_words": ["вне базы", "away"]},
                    {"name": "unlabelled", "note": "только выдуманные слова"},
                ],
            }]},
            "criteria": {"criteria": []}, "dictionaries": {},
            "requests": {"requests": []},
        })


def test_reader_returns_parameter_words_not_family_words(catalog):
    assert catalog.subjects("rest_floors") == {
        "min_rest_home": ("дома", "home base"),
        "min_rest_outstation": ("вне базы", "away"),
    }


def test_reader_does_not_mutate_or_expose_catalogue(catalog):
    before = copy.deepcopy(catalog.body())
    got = catalog.subjects("rest_floors")
    got["min_rest_home"] += ("injected",)
    got.clear()
    assert catalog.subjects("rest_floors")["min_rest_home"] == ("дома", "home base")
    assert catalog.body() == before


def test_unknown_family_has_named_refusal(catalog):
    with pytest.raises(tc.UnknownRecord, match="unknown_family"):
        catalog.subjects("unknown_family")


def test_absent_dictionary_is_not_inferred_from_notes_or_family(catalog):
    for parameter in catalog._families[0]["params"]:
        parameter.pop("subject_words", None)
    with pytest.raises(tc.CatalogError, match="rest_floors.*предмет"):
        catalog.subjects("rest_floors")


@pytest.mark.parametrize("bad", ["дома", 7, {}, [], [""], ["  "], [False], [None], ["дома", 2]])
def test_malformed_dictionary_is_not_silently_dropped(catalog, bad):
    catalog._families[0]["params"][0]["subject_words"] = bad
    with pytest.raises(tc.CatalogError, match="min_rest_home.*subject_words"):
        catalog.subjects("rest_floors")


def test_shared_word_retains_both_subjects(catalog):
    for parameter in catalog._families[0]["params"][:2]:
        parameter["subject_words"] = ["отдых"]
    got = catalog.subjects("rest_floors")
    assert got == {"min_rest_home": ("отдых",), "min_rest_outstation": ("отдых",)}


def test_optional_null_does_not_borrow_another_subject(catalog):
    catalog._families[0]["params"][0]["subject_words"] = None
    assert catalog.subjects("rest_floors") == {"min_rest_outstation": ("вне базы", "away")}


def test_bridge_delegates_to_selected_catalogue(catalog, monkeypatch):
    monkeypatch.setattr(bridge, "catalog", lambda: catalog)
    assert bridge.subjects("rest_floors") == catalog.subjects("rest_floors")


def test_bridge_old_reader_refuses_in_words(monkeypatch):
    monkeypatch.setattr(bridge, "catalog", lambda: object())
    with pytest.raises(bridge.CatalogRequired, match="предмет"):
        bridge.subjects("rest_floors")


def test_public_wrapper_uses_supplied_source(catalog, monkeypatch):
    source = object()
    def load(got):
        assert got is source
        return catalog
    monkeypatch.setattr(tc, "load", load)
    assert tc.subjects("rest_floors", source=source) == catalog.subjects("rest_floors")


@pytest.mark.parametrize("raw, expected", [
    ("ДОМА 600 минут", ["min_rest_home"]),
    ("Вне\nбазы — 720 минут", ["min_rest_outstation"]),
    ("дома 600, вне базы 720", ["min_rest_home", "min_rest_outstation"]),
    ("вне базы 720, дома 600", ["min_rest_home", "min_rest_outstation"]),
    ("домашний отдых 600", []),
    ("somewhereaway 720", []),
    ("home base: 600", ["min_rest_home"]),
    ("отдых 600", []),
])
def test_mentions_use_only_complete_catalogue_words(catalog, monkeypatch, raw, expected):
    from cdpm.answer_subjects import mentions
    monkeypatch.setattr(bridge, "catalog", lambda: catalog)
    got = mentions(raw, "rest_floors")
    assert sorted({hit.parameter for hit in got}) == expected
    assert all(raw[hit.start:hit.end] == hit.quote for hit in got)


def test_mentions_do_not_choose_first_for_collision(catalog, monkeypatch):
    from cdpm.answer_subjects import mentions
    for parameter in catalog._families[0]["params"][:2]:
        parameter["subject_words"] = ["отдых"]
    monkeypatch.setattr(bridge, "catalog", lambda: catalog)
    got = mentions("отдых 600", "rest_floors")
    assert {hit.parameter for hit in got} == {"min_rest_home", "min_rest_outstation"}
    assert {(hit.start, hit.end, hit.quote) for hit in got} == {(0, 5, "отдых")}


def test_bridge_preserves_reader_refusal_and_family(catalog, monkeypatch):
    monkeypatch.setattr(bridge, "catalog", lambda: catalog)
    with pytest.raises(bridge.CatalogRequired, match="unknown_family") as error:
        bridge.subjects("unknown_family")
    assert isinstance(error.value.__cause__, tc.UnknownRecord)


def test_mentions_escape_catalogue_punctuation(catalog, monkeypatch):
    from cdpm.answer_subjects import mentions
    catalog._families[0]["params"][0]["subject_words"] = ["home.base"]
    monkeypatch.setattr(bridge, "catalog", lambda: catalog)
    assert not mentions("homexbase 600", "rest_floors")
    assert {hit.quote for hit in mentions("home.base 600", "rest_floors")} == {"home.base"}


def test_mentions_reject_nontext_before_reading_catalogue(monkeypatch):
    from cdpm.answer_subjects import mentions
    def forbidden():
        raise AssertionError("nontext must not read catalogue")
    monkeypatch.setattr(bridge, "catalog", forbidden)
    with pytest.raises(ValueError, match="строкой"):
        mentions(None, "rest_floors")


@pytest.mark.parametrize("name", [None, "", "  ", False, 7])
def test_word_bearing_parameter_requires_name(catalog, name):
    catalog._families[0]["params"][0]["name"] = name
    with pytest.raises(tc.CatalogError, match="имя параметра"):
        catalog.subjects("rest_floors")


def test_duplicate_parameter_does_not_overwrite_words(catalog):
    catalog._families[0]["params"][1]["name"] = "min_rest_home"
    with pytest.raises(tc.CatalogError, match="повтор имени"):
        catalog.subjects("rest_floors")


def test_malformed_parameter_does_not_hide_dictionary(catalog):
    catalog._families[0]["params"].append("not an object")
    with pytest.raises(tc.CatalogError, match="параметр не объект"):
        catalog.subjects("rest_floors")
