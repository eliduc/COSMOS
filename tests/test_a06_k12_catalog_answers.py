"""K12: actual integration catalogue through ordinary answer/provenance readers.

K12_CATALOG selects BASE or candidate without changing any test expectation.
No LLM, service, solver, release, approval or old answer migration is invoked.
Every selected source file remains byte-identical. Schema-valid is not the
same as answer-ready; this profile checks every declared parameter address.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import template_catalog as tc
from cdpm import answer_parsing, answer_subjects, catalog_bridge as cb, family_params as fp
from cdpm import model, provenance
from test_k03_subject_application import apply
from test_cdpm_a03m_provenance import _cell, _q, QID, FAMILY, PARAM


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "catalog-integration" / "0.7.0-draft.3"


@pytest.fixture
def selected(monkeypatch):
    path = Path(os.environ.get("K12_CATALOG", str(DEFAULT))).resolve(strict=True)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in path.rglob("*") if p.is_file()}
    monkeypatch.setenv(cb.ENV_CATALOG, str(path))
    cb.forget()
    cat = tc.current(path, validated=True)
    assert cat.is_draft() and not [p for p in cat.validate() if p.severity == "error"]
    try:
        yield cat
    finally:
        cb.forget()
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob("*") if p.is_file()} == before


def test_entire_declared_parameter_vocabulary_is_answer_ready(selected):
    families = selected.body()["families"]
    addresses = [(f["id"], p["name"]) for f in families for p in f["params"]]
    assert len(addresses) == 31
    for family in families:
        if family["params"]:
            words = selected.subjects(family["id"])
            assert set(words) == {p["name"] for p in family["params"]}
            for parameter, phrases in words.items():
                for phrase in phrases:
                    assert {h.parameter for h in answer_subjects.mentions(
                        phrase.upper(), family["id"])} == {parameter}


@pytest.mark.parametrize("raw,value", [
    ("600 минут", 600), ("Вне базы — 10 часов.", 600),
    ("Внебазовый отдых — не менее 10 часов.", 600), ("10,5 часов", 630),
])
def test_actual_application_preserves_home_and_exact_answer_source(selected, raw, value):
    doc, answer, result = apply(raw)
    assert result["applied"] == 1 and result["unparsed"] == 0, answer
    assert _cell(doc)["value"] == value
    assert _cell(doc, name="min_rest_home")["value"] == 720
    assert not model.is_open_question(_q(doc, QID), doc=doc)
    assert answer["raw"] == raw
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, _cell(doc)) == []
    assert model.validate_schema(doc) == []
    restored = model.loads_model(json.dumps(doc, ensure_ascii=False))
    next(r for r in restored["rules"] if r["id"] == "R-R")["template_fit"]["params"].pop(PARAM)
    fp.ensure_family_param_questions(restored)
    assert _cell(restored) == _cell(doc)
    assert provenance.cell_problems(restored, "R-R", FAMILY, PARAM, _cell(restored)) == []
    wrong = copy.deepcopy(_cell(doc))
    wrong["value"] += 1
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, wrong)


@pytest.mark.parametrize("raw", [
    "Дома — 10 часов.",
    "Дома — 10 часов. Вне базы — 10 часов.",
    "Вне базы — 10 часов. Дома — 10 часов.",
    "На неизвестной площадке — 10 часов.",
    "Вне базы — 10 часов только для пилотов.",
    "Вне базы — 10 часов. Вне базы — 12 часов.",
    "Вне базы — не более 10 часов.",
    "Вне базы — 600 километров.",
])
def test_wrong_subject_relation_condition_and_unit_remain_unapplied(selected, raw):
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    params = next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    assert PARAM not in params and params["min_rest_home"]["value"] == 720
    assert model.is_open_question(_q(doc, QID), doc=doc)
    assert answer["parsed"]["problems"] and answer["raw"] == raw


def test_hard_base_alias_does_not_admit_obsolete_soft_subject(selected):
    family, parameter = "duty_extension_penalty", "long_duty_threshold"
    assert answer_subjects.problem("Базовый предел смены — 12 часов.",
                                   family, parameter, "duration_minutes") is None
    assert answer_subjects.problem("Мягкий порог продлённой смены — 12 часов.",
                                   family, parameter, "duration_minutes")
    assert answer_subjects.problem("Потолок продления — 16 часов.",
                                   family, parameter, "duration_minutes")


def test_every_numeric_subject_preserves_a_healthy_value_and_provenance(selected):
    values = {"duration_minutes": ("10 часов", 600),
              "time_of_day_minutes": ("22:00", 1320),
              "count": ("3", 3), "credit_hours": ("75 часов", 75)}
    checked = 0
    for family in selected.body()["families"]:
        for parameter in family["params"]:
            unit, name = parameter["unit"], parameter["name"]
            if unit not in values:
                continue  # code/code_list have separate unchanged grammar
            tail, value = values[unit]
            question = {"id": f"Q-3b-параметр-{family['id']}-{name}"}
            for phrase in selected.subjects(family["id"])[name]:
                raw = f"{phrase} — {tail}"
                parsed = answer_parsing.parameter(question, raw)
                assert not parsed["ambiguous"] and parsed["value"] == value, parsed
                assert parsed["quote"] == raw
                assert provenance.value_problems(family["id"], name,
                    {"value": value, "quote": raw}, raw) == []
                checked += 1
    assert checked == 154


def test_old_catalogue_is_retained_and_new_body_is_not_an_approval(selected):
    old = tc.current(ROOT / "catalog-integration" / "0.7.0-draft.2", validated=True)
    assert old.fingerprint() == "07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357"
    assert selected.version() == "0.7.0-draft.3"
    assert selected.fingerprint() != old.fingerprint()
    body = selected.body()
    for family in body["families"]:
        for parameter in family["params"]:
            parameter.pop("subject_words")
    assert body == old.body()  # vocabulary only, no weakened numeric contract
    assert selected.versions() == old.versions()
    assert not (selected.path / "support.json").exists()
    assert not (selected.path / "bindings.json").exists()


def test_draft3_retains_history_and_has_fresh_local_projection(selected):
    from catalog_service.releases import releases_gate
    import importlib.util

    old = ROOT / "catalog-integration" / "0.7.0-draft.2"
    allowed = {"catalog.json", "families.json", "schema/contract.json",
               "tools/build_from_code.py", "tools/catalog_seed.json",
               "transition/step4_additions.json"}
    old_files = {p.relative_to(old).as_posix(): p.read_bytes()
                 for p in old.rglob("*") if p.is_file()}
    new_files = {p.relative_to(selected.path).as_posix(): p.read_bytes()
                 for p in selected.path.rglob("*") if p.is_file()}
    assert set(new_files) == set(old_files)
    assert {name for name in old_files if new_files[name] != old_files[name]} == allowed
    assert len(old_files) == 55
    assert new_files["tools/build_from_code.py"].replace(b"\r\n", b"\n") == (
        ROOT / "catalog/tools/build_from_code.py").read_bytes().replace(b"\r\n", b"\n")
    gates = releases_gate(selected)
    assert len(gates) == 5 and all(row["ok"] for row in gates), gates
    spec = importlib.util.spec_from_file_location("k12_draft_tool", selected.path / "tools/build_from_code.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    # Genuine local read-only projection. No release/parity/service invocation.
    seed, delta = tool.refreshed(selected.path)
    assert json.loads(seed) == json.loads(new_files["tools/catalog_seed.json"])
    assert json.loads(delta) == json.loads(new_files["transition/step4_additions.json"])
    assert json.loads(delta)["fingerprint"] == selected.fingerprint()
