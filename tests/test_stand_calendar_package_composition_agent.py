"""Calendar form through an actual unsigned draft stand-package composition.

This is a NEW SOFT-only synthetic world, not a declared full stand task with
HARD rules removed. Catalogue, fresh dual bindings and unsigned support reuse
the existing isolated fixture. No approval/stand result is manufactured.
Calendar days are rule_form data (the family has no numeric params); the real
stand carrier and producer derive the calendar_window_penalties section.

Positive proof: explicit SPEC -> real ZIP -> actual gate -> Python reader.
The Python reader returns the model, not threshold-forms: section bytes are
inspected from the same ZIP and compared with the values in the read model.
This is not Kotlin execution, a solver result, or permission to run SOFT.
"""
import copy
import hashlib
import io
import json
import zipfile

import pytest

from cdpm import model as m, smt_verify
from cdpm import export_task_package as producer
from cdpm.gates import gate_task_package as gate
from template_catalog import landing
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st
from webapp import task_package as receiver
from test_stand_draft_package_composition_agent import draft_composition  # noqa: F401


FAMILY = "calendar_windows"
SECTION = "calendar_window_penalties"
RULE_ID = "R-STAND-calendar_windows"
WORLD_NAME = "synthetic-soft-calendar-package-only"
DAYS = ["thursday", "saturday"]


def _calendar_world():
    # Deliberately constructed independently of the catalogue's declared task.
    return {"form": "schedule", "params": {},
            "rule_form": {"days": list(DAYS)},
            "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
            "flights": [{"id": "F1", "from": "ST1", "to": "ST2",
                         "dep": "2026-10-01T06:00", "arr": "2026-10-01T08:00",
                         "fleet_type": "T1"}],
            "horizon": {"from": "2026-10-01", "to": "2026-10-31"}}


def _forbidden(*_args, **_kwargs):
    pytest.fail("SOFT calendar composition must not call a translator, channel or Z3")


def _package(cat, base, monkeypatch, revision):
    record = cat.family(FAMILY)
    assert record["kind"] == "SOFT" and record["zone"] == "PAIRING"
    assert record["params"] == []
    fields = {f["name"]: f for f in record["rule_form"]["fields"]}
    assert fields["days"]["required"] is True and set(DAYS) <= set(fields["days"]["values"])
    assert fields["tz"]["required"] is False and fields["tz"]["values"] == ["base-local"]
    world = _calendar_world()
    meta = st._family_meta(cat)
    assert st.families_for(world, FAMILY, meta) == [FAMILY]
    rules = [sa._rule_of(world, FAMILY, meta[FAMILY], meta[FAMILY]["params"])]
    assert [r["kind"] for r in rules] == ["SOFT"]
    before = copy.deepcopy((world, rules))
    doc = ss.model_doc(rules, world=world, family=FAMILY, catalog=cat)
    rule = doc["rules"][0]
    assert rule["template_fit"]["schema_version"] == "landing/2"
    assert rule["template_fit"]["family_contract"]["catalog_fingerprint"] == cat.fingerprint()
    assert rule["template_fit"]["params"] == {}
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert landing.problems(rule, cat) == m.reconcile_carriers(rule) == []
    assert ss.promised_sections(world, rules) == [SECTION]
    source = ss._stand_source(world, rules, None)
    run = ss._write_run(base, doc, ss._sheets(world), source=source)
    raw = (run / "extract" / "model.json").read_bytes()
    monkeypatch.setattr(producer, "smt_channel_factory", _forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", _forbidden)
    report = smt_verify.verify(doc, _forbidden, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)

    body = ss.package_bytes(world, rules, stamp=ss.stamp_of(cat), base=base,
                            catalog=cat, family=FAMILY, spec_version=revision)
    assert (world, rules) == before
    assert (run / "extract" / "model.json").read_bytes() == raw
    assert (run / "extract" / "stand-world.json").read_bytes() == source
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    path = base / (WORLD_NAME + "-" + revision + ".zip")
    path.write_bytes(body)
    problems, counters = gate.check(path)
    assert problems == [], problems
    assert counters["spec_version"] == revision
    loaded = receiver.load(path, run_gate=False)  # actual gate above; no subprocess CLI
    assert loaded["manifest"]["spec_version"] == revision
    assert loaded["manifest"]["catalog"] == ss.stamp_of(cat)
    assert loaded["landing_projection"]["archive_only"] is False
    assert loaded["model"]["sources"][0]["sha256"] == hashlib.sha256(source).hexdigest()
    assert path.read_bytes() == body
    return world, rules, doc, files, loaded


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
def test_calendar_window_section_and_read_model_keep_exact_typed_meaning(
        draft_composition, tmp_path, monkeypatch, revision):
    cat, documents, binding_paths, support = draft_composition
    protected = {p: p.read_bytes() for p in [*binding_paths.values(), support]}
    world, rules, doc, files, loaded = _package(cat, tmp_path / WORLD_NAME,
                                               monkeypatch, revision)
    forms = m.loads_strict(files[ss.FORMS_FILE])
    assert forms[SECTION] == [{"days": ["thursday", "saturday"], "tz": "base-local",
                               "action": "layover_penalty", "weight": 1, "rule": RULE_ID,
                               "as_written": doc["rules"][0]["statement"]}]
    assert ss._require_sections_in_package(files, world, rules) is None
    read_rule = loaded["model"]["rules"][0]
    assert read_rule["template_fit"] == doc["rules"][0]["template_fit"]
    assert read_rule["export_solver"]["family"] == FAMILY
    assert read_rule["export_solver"]["days"] == ["thursday", "saturday"]
    assert read_rule["export_solver"]["tz"] == "base-local"
    cells = loaded["manifest"]["support"]["cells"]
    assert cells and {c["record_id"] for c in cells} == {FAMILY}
    assert {c["product"] for c in cells} == {"CDPM", "COSMOS"}
    assert all(c["approved"] is None and c["state"] != "есть" for c in cells)
    for cell in cells:
        assert cell["bindings_file"]["product_version"] == documents[cell["product"]]["product_version"]
    assert "feasibility-soft" not in loaded["manifest"]["allowed_modes"]["pairing"]
    with pytest.raises(receiver.TaskPackageRefused) as failure:
        receiver.check_support_execution(loaded, task="pairing", mode="feasibility-soft")
    assert failure.value.code == "support-missing"
    for path, raw in protected.items():
        assert path.read_bytes() == raw, str(path)


@pytest.mark.parametrize("damage", ["missing_section", "different_valid_day"])
def test_rehashed_calendar_section_damage_is_not_hidden_by_a_valid_zip(
        draft_composition, tmp_path, monkeypatch, damage):
    cat, _, _, _ = draft_composition
    world, rules, _doc, files, _loaded = _package(cat, tmp_path / WORLD_NAME,
                                                monkeypatch, "1.20")
    original = dict(files)
    forms = m.loads_strict(files[ss.FORMS_FILE])
    if damage == "missing_section":
        del forms[SECTION]
    else:
        # A legal weekday with a different meaning, not malformed JSON/schema.
        forms[SECTION][0]["days"] = ["friday"]
    changed = dict(files)
    changed[ss.FORMS_FILE] = (json.dumps(forms, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if damage == "missing_section":
        with pytest.raises(ss.StandSolverError, match=SECTION):
            ss._require_sections_in_package(changed, world, rules)
    else:
        # This guard promises presence only; semantic equality belongs to gate.
        assert ss._require_sections_in_package(changed, world, rules) is None
    manifest = m.loads_strict(changed["manifest.json"])
    entry, = [e for e in manifest["files"] if e["name"] == ss.FORMS_FILE]
    entry.update(sha256=hashlib.sha256(changed[ss.FORMS_FILE]).hexdigest(),
                 bytes=len(changed[ss.FORMS_FILE]))
    changed["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    assert gate._check_files(changed)[0] == [], "negative must not depend on stale file hashes"
    path = tmp_path / (damage + ".zip")
    path.write_bytes(producer.write_zip(changed))
    problems, _ = gate.check(path)
    assert any("threshold-forms.json не совпадает с пересборкой из model.json" in p
               for p in problems), problems
    assert not any("sha256 не совпадает" in p for p in problems), problems
    assert files == original
    assert {name for name in changed if changed[name] != original[name]} == {
        "manifest.json", ss.FORMS_FILE}
