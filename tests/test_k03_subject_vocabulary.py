"""Full local subject draft: exact coverage and real-reader integration.

Coverage is mechanical evidence, not semantic expert acceptance. Russian
phrases below are independent examples; no publication or live data changes.
"""
import copy
import json
from pathlib import Path

import pytest

from cdpm import answer_subjects, catalog_bridge as cb, model
from template_catalog.subject_vocabulary import attach, SubjectVocabularyError
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken, FILES, CATALOG
from test_k03_subject_application import apply
from test_cdpm_a03m_provenance import _cell, _q, QID

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "catalog/transition/parameter_subject_words_060.json"


def vocabulary():
    return json.loads(VOCAB.read_text(encoding="utf-8"))


def documents():
    docs = {name: json.loads((CATALOG / name).read_text(encoding="utf-8")) for name in FILES}
    modern_docs(docs)
    return docs


def selected_catalogue(tmp_path, monkeypatch):
    def prepare(docs):
        modern_docs(docs)
        result = attach(docs, vocabulary())
        # Reader tests need the real unit vocabulary, not modern_docs'
        # minimal schema-plumbing map containing only singular unit words.
        from template_catalog.unit_vocabulary import attach as attach_units
        from test_k03_unit_vocabulary import vocabulary as unit_words
        result = attach_units(result, unit_words())
        docs.clear()
        docs.update(result)
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    cat = cb.catalog()
    assert cat.version() == "0.7.0"
    assert not [p for p in cat.validate() if p.severity == "error"]
    return cat


@pytest.fixture(autouse=True)
def clear_cache():
    yield
    cb.forget()


def test_draft_covers_exactly_all_current_parameters_without_changing_contract():
    docs, words = documents(), vocabulary()
    before, original_words = copy.deepcopy(docs), copy.deepcopy(words)
    result = attach(docs, words)
    assert docs == before and words == original_words
    records = result["families.json"]["families"]
    assert len(records) == 17
    assert sum(bool(f["params"]) for f in records) == 15
    assert sum(len(f["params"]) for f in records) == 31
    for family in records:
        for param in family["params"]:
            assert param.pop("subject_words")
    assert result == before  # no other field, including version/fingerprint, changes


def test_every_curated_phrase_resolves_only_to_its_own_parameter(tmp_path, monkeypatch):
    cat = selected_catalogue(tmp_path, monkeypatch)
    count = 0
    for family, params in vocabulary()["families"].items():
        assert set(cat.subjects(family)) == set(params)
        for name, phrases in params.items():
            for phrase in phrases:
                raw = phrase.upper()
                hits = answer_subjects.mentions(raw, family)
                assert {h.parameter for h in hits} == {name}, (family, name, phrase, hits)
                assert all(raw[h.start:h.end] == h.quote for h in hits)
                count += 1
    assert count > 100  # only minimum coverage, not a claim of all inflections


@pytest.mark.parametrize("family,raw,target", [
    ("rest_floors", "Дома — 10 часов", "min_rest_home"),
    ("rest_floors", "Вне базы — 10 часов", "min_rest_outstation"),
    ("night_series", "Конца ночного окна не меняем", "night_duty_end"),
    ("night_series", "Начала ночного окна не меняем", "night_duty_start"),
    ("span_accumulators", "Месячного налёта — 100 часов", "max_monthly_block"),
    ("duty_series_limit", "Рабочих смен подряд — 5", "max_duty_series_length"),
    ("days_off_floor", "Выходных дней в месяц — 8", "min_days_off"),
    ("days_off_floor", "Выходных дней в скользящем окне — 3", "min_days_off_in_window"),
])
def test_independent_semantic_examples(tmp_path, monkeypatch, family, raw, target):
    selected_catalogue(tmp_path, monkeypatch)
    assert {h.parameter for h in answer_subjects.mentions(raw, family)} == {target}


@pytest.mark.parametrize("raw,applied", [("Вне базы — 10 часов.", True),
                                        ("Дома — 10 часов.", False)])
def test_actual_answer_application_uses_draft_data(tmp_path, monkeypatch, raw, applied):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert bool(result["applied"]) is applied
    assert model.is_open_question(_q(doc, QID), doc=doc) is not applied
    if applied:
        assert _cell(doc)["value"] == 600
    else:
        assert answer["parsed"]["problems"]


@pytest.mark.parametrize("damage", ["missing_family", "extra_family", "missing_parameter",
                                     "extra_parameter", "empty_words", "upper", "duplicate"])
def test_stale_or_malformed_vocabulary_refuses_without_partial_changes(damage):
    docs, words = documents(), vocabulary()
    home = words["families"]["rest_floors"]
    if damage == "missing_family":
        del words["families"]["rest_floors"]
    elif damage == "extra_family":
        words["families"]["invented"] = {}
    elif damage == "missing_parameter":
        del home["min_rest_home"]
    elif damage == "extra_parameter":
        home["invented"] = ["слово"]
    elif damage == "empty_words":
        home["min_rest_home"] = []
    elif damage == "upper":
        home["min_rest_home"] = ["ДОМА"]
    elif damage == "duplicate":
        home["min_rest_home"] = ["дома", "дома"]
    before = copy.deepcopy(docs)
    with pytest.raises(SubjectVocabularyError):
        attach(docs, words)
    assert docs == before


def test_no_implicit_catalogue_upgrade():
    docs = documents()
    docs["catalog.json"]["version"] = "0.5.0"
    before = copy.deepcopy(docs)
    with pytest.raises(SubjectVocabularyError, match="0.7"):
        attach(docs, vocabulary())
    assert docs == before


def test_missing_non_parameter_family_is_not_a_missing_vocabulary():
    docs = documents()
    docs["families.json"]["families"] = [f for f in docs["families.json"]["families"] if f["params"]]
    assert attach(docs, vocabulary())
