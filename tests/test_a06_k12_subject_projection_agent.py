"""K12: parameter subjects survive the real curate/build/refresh algorithms.

All catalogue preparation and generated files stay in memory. Current source
documents, schema and Kotlin tables are read, never executed or rewritten.
Refresh's four filesystem inputs are supplied as in-memory objects; its
projection and delta calculations, and the real output validator, are intact.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

import template_catalog
from template_catalog import canon
from template_catalog.reader import Catalog
from catalog.tools import build_from_code as bfc
from test_catalog_060_versioned_schema import modern_docs


ROOT = Path(__file__).resolve().parents[1]
WORDS = {
    "duty_limits": {"max_duty_duration": ["смен", "рабочая смена"],
                    "max_pairing_days": ["командиров"]},
    "fleet_exclusions": {"fleet_types": ["тип судна"]},
}


def _catalog(words=None, *, modern=True):
    docs = {name: json.loads((ROOT / "catalog" / name).read_text(encoding="utf-8"))
            for name in ("catalog.json", "families.json", "criteria.json",
                         "dictionaries.json", "requests.json", "versions.json")}
    if modern:
        modern_docs(docs, "0.7.0-draft.3")
    docs["versions.json"] = {"form_version": 1, "versions": []}
    for family in docs["families.json"]["families"]:
        for param in family["params"]:
            param.pop("subject_words", None)
            own = (words or {}).get(family["id"], {})
            if param["name"] in own:
                param["subject_words"] = deepcopy(own[param["name"]])
    body = {key: docs[key + ".json"][key] for key in ("families", "criteria")}
    body["dictionaries"] = {key: value for key, value in docs["dictionaries.json"].items()
                            if key != "form_version"}
    docs["catalog.json"]["fingerprint"] = canon.fingerprint_of(body)
    return Catalog(path=ROOT / "catalog", header=docs["catalog.json"],
        docs={key: docs[key + ".json"] for key in ("families", "criteria", "dictionaries", "requests")},
        versions_doc=docs["versions.json"], single_file=True)


def _seed(cat):
    return bfc.curated_of(cat, hard=bfc.load_seed(),
                          order=[r["id"] for r in cat.families_full(status=None)])


def _build(cat, seed):
    return bfc.build(cat.version(), "2026-09-19", cat.header["released_by"], "in-memory K12 test",
                     seed=seed, kt=bfc.ktab.tables())


def _subjects(files):
    return {row["id"]: {p["name"]: p["subject_words"] for p in row["params"]
                         if "subject_words" in p}
            for row in json.loads(files["families.json"])["families"]
            if any("subject_words" in p for p in row["params"])}


def test_curated_projection_preserves_owned_parameter_subjects_without_aliasing():
    cat = _catalog(WORDS)
    before = cat.canonical_json()
    seed = _seed(cat)
    for family, own in WORDS.items():
        assert seed["families"][family].get("param_subject_words") == own
    seed["families"]["duty_limits"]["param_subject_words"]["max_duty_duration"].append("changed")
    assert cat.canonical_json() == before


def test_build_preserves_explicit_seed_subjects_without_adding_other_parameter_fields():
    cat = _catalog()
    seed = _seed(cat)
    for family, own in WORDS.items():
        seed["families"][family]["param_subject_words"] = deepcopy(own)
    before = deepcopy(seed)
    files = _build(cat, seed)
    assert _subjects(files) == WORDS
    assert seed == before
    assert not [p for p in bfc.validate_built(files) if p.severity == "error"]


def test_full_parameter_roundtrip_keeps_subject_order_and_numeric_contract():
    bare = _catalog()
    expected = {r["id"]: {p["name"]: ["предмет " + p["name"], "вторая основа"]
                           for p in r["params"]}
                for r in bare.families_full(status=None) if r["params"]}
    cat = _catalog(expected)
    original = cat.families_full(status=None)
    seed = _seed(cat)
    files = _build(cat, seed)
    assert _subjects(files) == expected
    assert sum(len(v) for v in expected.values()) == 31
    assert json.loads(files["families.json"])["families"] == canon.normalize(cat.body())["families"]
    assert cat.families_full(status=None) == original
    assert _build(cat, seed) == files


def test_actual_draft3_subjects_and_body_roundtrip_without_writing_catalogue():
    source = ROOT / "catalog-integration" / "0.7.0-draft.3"
    docs = {name: json.loads((source / name).read_text(encoding="utf-8"))
            for name in ("catalog.json", "families.json", "criteria.json",
                         "dictionaries.json", "requests.json", "versions.json")}
    cat = Catalog(path=source, header=docs["catalog.json"],
        docs={key: docs[key + ".json"] for key in ("families", "criteria", "dictionaries", "requests")},
        versions_doc=docs["versions.json"], single_file=True)
    expected = {r["id"]: {p["name"]: p["subject_words"] for p in r["params"]}
                for r in cat.families_full(status=None) if r["params"]}
    assert sum(len(v) for v in expected.values()) == 31
    files = _build(cat, _seed(cat))
    assert _subjects(files) == expected
    assert json.loads(files["catalog.json"])["fingerprint"] == cat.fingerprint()
    assert json.loads(files["families.json"])["families"] == canon.normalize(cat.body())["families"]
    assert not [p for p in bfc.validate_built(files) if p.severity == "error"]


@pytest.mark.parametrize("modern", [False, True])
def test_absent_optional_words_remain_absent_not_null_or_empty(modern):
    cat = _catalog(modern=modern)
    seed = _seed(cat)
    assert all("param_subject_words" not in row for row in seed["families"].values())
    files = _build(cat, seed)
    assert _subjects(files) == {}
    assert all("subject_words" not in p for row in json.loads(files["families.json"])["families"]
               for p in row["params"])
    assert not [p for p in bfc.validate_built(files) if p.severity == "error"]


@pytest.mark.parametrize("bad", [None, [], [""], ["UPPER"], ["same", "same"], "word", {"bad": "word"}])
def test_explicit_bad_parameter_words_survive_to_real_validation(bad):
    expected = {"duty_limits": {"max_duty_duration": bad}}
    cat = _catalog(expected)
    files = _build(cat, _seed(cat))
    assert _subjects(files) == expected  # no silent deletion/repair/default
    errors = [p for p in bfc.validate_built(files) if p.severity == "error"]
    assert any("subject_words" in str(p) for p in errors), errors


@pytest.mark.parametrize("bad", [None, [], "words"])
def test_malformed_seed_subject_map_is_addressed_refusal(bad):
    cat = _catalog()
    seed = _seed(cat)
    seed["families"]["duty_limits"]["param_subject_words"] = bad
    with pytest.raises(bfc.BuildError, match="duty_limits.*param_subject_words"):
        _build(cat, seed)


def test_seed_subject_for_nonmember_parameter_is_not_silently_lost():
    cat = _catalog()
    seed = _seed(cat)
    seed["families"]["duty_limits"]["param_subject_words"] = {"foreign_parameter": ["предмет"]}
    with pytest.raises(bfc.BuildError, match="duty_limits.*foreign_parameter"):
        _build(cat, seed)


def test_refresh_uses_the_same_lossless_projection_with_memory_io(monkeypatch):
    cat = _catalog(WORDS)
    hard = bfc.load_seed()
    order = [r["id"] for r in cat.families_full(status=None)]
    before = cat.canonical_json()
    # Supply filesystem inputs only; neither curated_of nor build nor the
    # actual refresh/delta/validation mechanism is replaced.
    monkeypatch.setattr(template_catalog, "load", lambda _out: cat)
    monkeypatch.setattr(bfc, "seed_of", lambda _out: deepcopy(hard))
    monkeypatch.setattr(bfc, "order_of", lambda _out, _cat: list(order))
    monkeypatch.setattr(bfc, "read_requests", lambda _out: (deepcopy(bfc.EMPTY_REQUESTS), None))
    seed_text, delta_text = bfc.refreshed(ROOT / "catalog", kt=bfc.ktab.tables())
    seed = json.loads(seed_text)
    assert {f: seed["families"][f].get("param_subject_words") for f in WORDS} == WORDS
    assert _subjects(_build(cat, seed)) == WORDS
    assert json.loads(delta_text)["fingerprint"] == cat.fingerprint()
    assert cat.canonical_json() == before
