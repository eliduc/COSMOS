"""Real unit vocabulary in an explicit 0.7 draft; no release or live calls."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, family_params as fp
from template_catalog import units
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken, CATALOG, FILES

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "catalog/transition/unit_words_060.json"


def vocabulary():
    return json.loads(DRAFT.read_text(encoding="utf-8"))


def attach(documents, words):
    from template_catalog.unit_vocabulary import attach as prepare
    return prepare(documents, words)


def documents():
    docs = {name: json.loads((CATALOG / name).read_text(encoding="utf-8")) for name in FILES}
    modern_docs(docs)
    return docs


def selected(tmp_path, monkeypatch):
    def prepare(docs):
        modern_docs(docs)
        updated = attach(docs, vocabulary())
        docs.clear()
        docs.update(updated)
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    return cb.catalog()


@pytest.fixture(autouse=True)
def forget():
    yield
    cb.forget()


def test_complete_draft_is_pure_and_does_not_change_numeric_contract():
    docs, words = documents(), vocabulary()
    before, original_words = copy.deepcopy(docs), copy.deepcopy(words)
    result = attach(docs, words)
    d = result["dictionaries.json"]
    assert set(d["unit_words"]) == set(d["param_units"] + d["condition_units"])
    assert len(d["unit_words"]) == 9
    result["dictionaries.json"]["unit_words"] = before["dictionaries.json"]["unit_words"]
    assert result == before and docs == before and words == original_words


@pytest.mark.parametrize("value,word,target,want", [
    (1, "минута", "duration_minutes", 1), (1, "минуты", "duration_minutes", 1),
    (1, "мин.", "duration_minutes", 1), (1, "ч.", "duration_minutes", 60),
    ("10,5", "часов", "duration_minutes", 630),
    ("0.1", "часа", "duration_minutes", 6),
    (1, "сутки", "duration_minutes", 1440), (1, "дней", "duration_minutes", 1440),
    (3, "суток", "count", 3), (3, "посадок", "count", 3),
    ("23:59", "чч:мм", "time_of_day_minutes", 1439),
    ("1,5", "кредитных часов", "credit_hours", 1.5),
    ("1.5", "часов на час кредита", "hours_per_credit_hour", 1.5),
    ("0.5", "кредитных часов на час", "credit_hours_per_hour", 0.5),
])
def test_real_reader_and_shared_converter(tmp_path, monkeypatch, value, word, target, want):
    cat = selected(tmp_path, monkeypatch)
    assert not [p for p in cat.validate() if p.severity == "error"]
    assert units.to_canonical(value, word, target, source=cat.path) == want


@pytest.mark.parametrize("value,word,target", [
    ("24:00", "чч:мм", "time_of_day_minutes"),
    ("10:90", "чч:мм", "duration_minutes"),
    ("0." + "9" * 60, "час", "duration_minutes"),
    (1, "неделя", "duration_minutes"), (1, "час", "count"),
    (0, "часов на час кредита", "hours_per_credit_hour"),
])
def test_shared_converter_preserves_refusals(tmp_path, monkeypatch, value, word, target):
    cat = selected(tmp_path, monkeypatch)
    with pytest.raises(units.UnitsError):
        units.to_canonical(value, word, target, source=cat.path)


def test_question_hint_uses_selected_catalogue_not_a_second_word_list(tmp_path, monkeypatch):
    cat = selected(tmp_path, monkeypatch)
    hint = fp.unit_words("duration_minutes")
    for word in cat.dictionaries()["unit_words"]["duration_minutes"]["words"]:
        assert word in hint
    assert "минута" in hint
    assert "кредитный час на час" in fp.unit_words("credit_hours_per_hour")


@pytest.mark.parametrize("damage", ["missing", "extra", "empty", "scale", "collision", "infinite"])
def test_malformed_draft_refuses_atomically(damage):
    from template_catalog.unit_vocabulary import UnitVocabularyError
    docs, words = documents(), vocabulary()
    entry = words["unit_words"]["duration_minutes"]
    if damage == "missing":
        del words["unit_words"]["flag"]
    elif damage == "extra":
        words["unit_words"]["invented"] = copy.deepcopy(entry)
    elif damage == "empty":
        entry["words"] = []
    elif damage == "scale":
        entry["scale"]["неизвестно"] = 60
    elif damage == "collision":
        entry["words"].append("ч.")
        entry["scale"]["ч."] = 1
    elif damage == "infinite":
        entry["scale"]["час"] = float("inf")
    before = copy.deepcopy(docs)
    with pytest.raises(UnitVocabularyError):
        attach(docs, words)
    assert docs == before


def test_legacy_catalogue_is_not_upgraded_implicitly():
    from template_catalog.unit_vocabulary import UnitVocabularyError
    docs = documents()
    docs["catalog.json"]["version"] = "0.5.0"
    before = copy.deepcopy(docs)
    with pytest.raises(UnitVocabularyError, match="0.7"):
        attach(docs, vocabulary())
    assert docs == before


@pytest.mark.parametrize("without_jsonschema", [False, True])
@pytest.mark.parametrize("damage", ["collision", "infinite"])
def test_runtime_loader_also_refuses_ambiguous_or_nonfinite_scales(
        tmp_path, monkeypatch, damage, without_jsonschema):
    import importlib
    import template_catalog as tc
    rules = importlib.import_module("template_catalog.validate")
    if without_jsonschema:
        monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    def prepare(docs):
        modern_docs(docs)
        docs["dictionaries.json"]["unit_words"] = vocabulary()["unit_words"]
        entry = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
        if damage == "collision":
            entry["words"].append("ч.")
            entry["scale"]["ч."] = 1
        else:
            entry["scale"]["час"] = float("inf")
    folder = _broken(tmp_path, prepare)
    problems = tc.load(folder).validate()
    assert any(p.code == "X21" and "unit_words" in p.where for p in problems)
    with pytest.raises(tc.CatalogInvalid):
        tc.current(folder, validated=True)


def test_legacy_hint_retains_its_explicit_migration_boundary(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    cb.forget()
    assert cb.catalog().version() == "0.5.0"
    assert fp.unit_words("duration_minutes") == fp._UNIT_WORDS["duration_minutes"]


def test_unknown_modern_unit_is_not_explained_by_legacy_fallback(tmp_path, monkeypatch):
    selected(tmp_path, monkeypatch)
    with pytest.raises(cb.CatalogRequired, match="invented"):
        fp.unit_words("invented")
