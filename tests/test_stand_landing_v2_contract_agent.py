"""K2: real stand rule construction and typed readers, no HTTP/JVM/solve.

The isolated catalogue is an explicit vocabulary fixture, not approved support.
ZIP mode controls below exercise transport preparation, not package admission.
"""
import copy
import hashlib
import io
import json
import shutil
import zipfile

import pytest

from cdpm import catalog_bridge as cb, model as m
from cdpm import export_task_package as tp
from template_catalog import landing
import template_catalog as tc
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st
from test_k03_unit_vocabulary import selected


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    cb.forget()
    try:
        yield selected(tmp_path, monkeypatch)
    finally:
        cb.forget()


def world_with(params):
    return {"form": "schedule", "params": copy.deepcopy(params),
            "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
            "flights": [{"id": "F1", "from": "ST1", "to": "ST2",
                         "dep": "2026-10-01T06:00", "arr": "2026-10-01T08:00",
                         "fleet_type": "T1"}],
            "horizon": {"from": "2026-10-01", "to": "2026-10-31"}}


def rule_of(catalog, family, world):
    info = st._family_meta(catalog)[family]
    return sa._rule_of(world, family, info, info["params"])


@pytest.mark.parametrize("family,params", [
    ("duty_limits", {"max_duty_duration": 720, "max_landings_per_duty": 3,
                     "max_pairing_days": 7}),
    ("crew_composition", {"crew_size": 2}),
    ("fleet_exclusions", {"fleet_types": ["T1", "T2"]}),
    ("reserve_floors", {"reserve_crews": {"ST1": 2, "ST2": 1}}),
    ("night_series", {"max_consecutive_night_duties": 2,
                      "night_duty_start": 1320, "night_duty_end": 360}),
])
def test_generated_cells_are_typed_and_keep_world_values(catalog, family, params):
    world = world_with(params)
    rule = rule_of(catalog, family, world)
    before = copy.deepcopy((rule, world))
    doc = ss.model_doc([rule], world=world, family=family)
    fit = doc["rules"][0]["template_fit"]
    assert fit.get("schema_version") == "landing/2"
    assert fit["registry_rev"] == catalog.fingerprint()
    pin = fit["family_contract"]
    assert pin["family_id"] == family
    assert pin["catalog_version"] == catalog.version()
    assert pin["catalog_fingerprint"] == catalog.fingerprint()
    assert {name: cell["value"] for name, cell in fit["params"].items()} == params
    assert all(cell["source"]["kind"] == "code" for cell in fit["params"].values())
    assert landing.problems(doc["rules"][0], catalog) == []
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    if family == "crew_composition":
        assert tp.crew_size_sources(doc) == {2: rule["id"]}
    if family == "night_series":
        assert fit["params"]["night_duty_start"]["tz"] == "base-local"
    assert (rule, world) == before


def test_metric_source_resolves_to_the_synthetic_world(catalog):
    record = next(iter(catalog.criteria().values()))
    world = world_with({"crew_size": 2})
    objective = ss.objective_for(record, world=world)
    assert objective["sources"] == [{"sid": "S1", "anchor": "стенд"}]
    doc = ss.model_doc([rule_of(catalog, "crew_composition", world)],
                       world=world, objective=objective)
    assert {s["sid"] for s in doc["sources"]} == {"S1"}


class StartTransport:
    def __init__(self):
        self.calls = []

    def start(self, problem, zipped, mode, objective_id, limit, **kwargs):
        self.calls.append((mode, objective_id))
        return {"runId": "transport-only"}


def mode_package(modes, *, rules=(), objectives=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("manifest.json", json.dumps({"allowed_modes": {"rostering": modes},
                    "objectives": {"rostering": list(objectives)}}))
        z.writestr("model.json", json.dumps({"rules": list(rules)}))
    return output.getvalue()


def test_hard_only_manifest_does_not_request_unavailable_soft():
    client = StartTransport()
    ss.start_run(client, {}, mode_package(["feasibility-hard"]),
                 task="rostering", mode=None, objective_id=None)
    assert client.calls == [("feasibility-hard", None)]


def test_typed_metadata_is_preserved_not_recreated(catalog):
    world = world_with({"min_turnaround": 45})
    rule = rule_of(catalog, "connection_windows", world)
    typed = ss.model_doc([rule], world=world)["rules"][0]
    cell = typed["template_fit"]["params"]["min_turnaround"]
    cell.update(condition={"kind": "area", "value": "база"}, quote="45 минут на базе",
                source={"kind": "code", "note": "explicit synthetic scoped fact"})
    assert landing.problems(typed, catalog) == []
    before = copy.deepcopy(typed)
    output = ss.model_doc([typed], world=world)
    assert output["rules"][0]["template_fit"] == before["template_fit"]
    assert typed == before


@pytest.mark.parametrize("field,value", [("quote", None), ("unit", "code"),
                                         ("source", {"kind": "answer"})])
def test_invalid_typed_evidence_is_not_repaired(catalog, field, value):
    world = world_with({"crew_size": 2})
    typed = ss.model_doc([rule_of(catalog, "crew_composition", world)],
                         world=world)["rules"][0]
    typed["template_fit"]["params"]["crew_size"][field] = value
    before = copy.deepcopy(typed)
    with pytest.raises(ss.StandSolverError, match="crew_size"):
        ss.model_doc([typed], world=world)
    assert typed == before


@pytest.mark.parametrize("value", [True, "2", 2.5, -1])
def test_bad_canonical_count_is_not_reinterpreted(catalog, value):
    world = world_with({"crew_size": value})
    rule = rule_of(catalog, "crew_composition", world)
    with pytest.raises(ss.StandSolverError, match="crew_size"):
        ss.model_doc([rule], world=world)


def test_extension_boolean_and_scope_come_from_world(catalog):
    world = world_with({"duty_extension_cap": 780, "long_duty_threshold": 720})
    world["conditions"] = {"extension_bases": ["ST1"], "extension_consent": "no"}
    rule = rule_of(catalog, "duty_extension_penalty", world)
    typed = ss.model_doc([rule], world=world)["rules"][0]
    cells = typed["template_fit"]["params"]
    assert cells["extension_bases"]["value"] == ["ST1"]
    assert cells["extension_consent"]["value"] is False
    assert cells["extension_consent"]["unit"] == "flag"
    assert cells["extension_consent"]["source"]["kind"] == "code"
    assert landing.problems(typed, catalog) == []


@pytest.mark.parametrize("damage", ["different-world", "saved-legacy", "foreign-rule-pin"])
def test_new_pin_cannot_hide_a_different_source(catalog, damage):
    world = world_with({"crew_size": 2})
    rule = rule_of(catalog, "crew_composition", world)
    if damage == "different-world":
        world["params"]["crew_size"] = 3
    elif damage == "saved-legacy":
        rule["template_fit"]["schema_version"] = "landing/1"
    else:
        rule["template_fit"]["registry_rev"] = "f" * 64
    with pytest.raises(ss.StandSolverError):
        ss.model_doc([rule], world=world)


@pytest.mark.parametrize("change", ["version", "fingerprint"])
def test_catalog_mismatch_stops_before_export_without_writing(catalog, tmp_path, change):
    clone = tmp_path / "selected-other"
    shutil.copytree(catalog.path, clone)
    header = json.loads((clone / "catalog.json").read_text(encoding="utf-8"))
    if change == "version":
        header["version"] = "0.7.1"
    else:
        families_path = clone / "families.json"
        families = json.loads(families_path.read_text(encoding="utf-8"))
        families["families"][0]["title"] += " — isolated different catalogue"
        families_path.write_text(json.dumps(families, ensure_ascii=False), encoding="utf-8")
        header["fingerprint"] = tc.load(clone).fingerprint()
    (clone / "catalog.json").write_text(json.dumps(header), encoding="utf-8")
    other = tc.load(clone)
    assert not [p for p in other.validate() if p.severity == "error"]
    world = world_with({"crew_size": 2})
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ss.StandSolverError, match="CREW_TEMPLATE_CATALOG="):
        ss.package_bytes(world, [rule_of(catalog, "crew_composition", world)],
                         stamp=ss.stamp_of(other), catalog=other, base=destination)
    assert not destination.exists()


def test_same_catalogue_bytes_at_another_path_are_not_a_mismatch(catalog, tmp_path):
    clone = tmp_path / "same-catalogue"
    shutil.copytree(catalog.path, clone)
    other = tc.load(clone)
    assert ss._producer_catalog(ss.stamp_of(other), other) is other


def test_synthetic_source_hash_is_of_the_saved_source(catalog, tmp_path):
    world = world_with({"crew_size": 2})
    rules = [rule_of(catalog, "crew_composition", world)]
    doc = ss.model_doc(rules, world=world)
    source = ss._stand_source(world, rules, None)
    run = ss._write_run(tmp_path, doc, ss._sheets(world), source=source)
    saved = (run / "extract" / doc["sources"][0]["file"]).read_bytes()
    assert saved == source
    assert doc["sources"][0]["sha256"] == hashlib.sha256(saved).hexdigest()
    assert json.loads(saved)["стенд"]["world"] == world
    assert all("не ответ авиакомпании" in q["answer"]["raw"] for q in doc["questions"])


@pytest.mark.parametrize("mode,objective,rules,allowed,objective_ids", [
    (None, None, [{"kind": "SOFT", "zone": "ROSTERING"}], ["feasibility-hard"], []),
    ("feasibility-soft", None, [], ["feasibility-hard"], []),
    ("optimization", "O-1", [], ["feasibility-hard"], ["O-1"]),
    ("optimization", "O-foreign", [], ["optimization"], ["O-1"]),
])
def test_unavailable_meaning_is_not_silently_downgraded(mode, objective, rules,
                                                       allowed, objective_ids):
    client = StartTransport()
    with pytest.raises(ss.StandSolverError, match="режим пакета"):
        ss.start_run(client, {}, mode_package(allowed, rules=rules, objectives=objective_ids),
                     task="rostering", mode=mode, objective_id=objective)
    assert client.calls == []


@pytest.mark.parametrize("mode,objective,modes,ids,expected", [
    (None, None, ["feasibility-hard", "feasibility-soft"], [], (None, None)),
    ("optimization", "O-1", ["optimization"], ["O-1"], ("optimization", "O-1")),
])
def test_available_transport_mode_preserves_intent(mode, objective, modes, ids, expected):
    client = StartTransport()
    ss.start_run(client, {}, mode_package(modes, objectives=ids),
                 task="rostering", mode=mode, objective_id=objective)
    assert client.calls == [expected]
