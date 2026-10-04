"""Lossless synthetic family prose; no translator, provider, solve or approval.

The committed catalogue supplies semantics and declared input worlds. Expected
stand outcomes are never used to construct rules, cells, or the SMT subject.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from cdpm import catalog_bridge as cb, export_task_package as tp, model, providers
from cdpm import smt_verify
import template_catalog as tc
from template_catalog import landing
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st


CATALOG = Path(__file__).resolve().parents[1] / "catalog-integration/h0c-pregap03r7-01/0.8.0"
CATALOG_FP = "2733c31fb805a9e4b434e1b00d5f78e1411210b55061319d2e53df44fd8f530f"
FAMILIES = (
    "balanced_workload", "calendar_windows", "connection_windows",
    "crew_composition", "crew_unavailability", "days_off_floor", "deadhead_rules",
    "duty_extension_penalty", "duty_limits", "duty_series_limit", "fleet_exclusions",
    "layover_penalty", "night_series", "reserve_floors", "rest_floors",
    "rolling_rest", "span_accumulators", "time_scale",
)
FIELDS = (("text", "Описание"), ("measure", "Измерение"),
          ("scope", "Область"), ("unit", "Единица"))
META_KEYS = {"params", "kind", "zone", "title", "statement", "registry_rev"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("semantics projection tests forbid providers, network and child processes")

    for name in ("connect", "connect_ex", "bind", "listen", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in ("role_channel", "load_settings", "sync_cloud_key_to_env"):
        monkeypatch.setattr(providers, name, forbidden)
    monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "verify", forbidden)


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    cb.forget()
    try:
        cat = tc.load(CATALOG)
        assert cat.version() == "0.8.0"
        assert cat.fingerprint() == CATALOG_FP
        assert {r["id"] for r in cat.families_full()} == set(FAMILIES)
        yield cat
    finally:
        cb.forget()


class Records:
    """Only the metadata reader's two methods; no admissible catalogue claim."""
    def __init__(self, rows):
        self.rows = rows

    def families_full(self):
        return self.rows

    def fingerprint(self):
        return "f" * 64


def statement_for(semantics, *, title="Резервный заголовок"):
    record = {"id": "example", "title": title, "semantics": semantics,
              "kind": "HARD", "zone": "ROSTERING", "params": []}
    before = copy.deepcopy(record)
    meta = st._family_meta(Records([record]))["example"]
    assert record == before
    assert set(meta) == META_KEYS
    return meta["statement"]


def rule_of(catalog, family, world):
    meta = st._family_meta(catalog)[family]
    return sa._rule_of(world, family, meta, meta["params"])


def declared(catalog, family, task_id):
    return copy.deepcopy(next(t for t in catalog.family(family)["stand_tasks"]
                              if t["id"] == task_id))


def typed(catalog, family, world):
    rule = rule_of(catalog, family, world)
    before = copy.deepcopy((world, rule))
    doc = ss.model_doc([rule], world=world, family=family, catalog=catalog)
    source = ss._stand_source(world, [rule], None, catalog=catalog)
    assert (world, rule) == before
    assert model.validate_schema(doc) == []
    assert model.validate_template_fit(doc) == []
    assert landing.problems(doc["rules"][0], catalog) == []
    assert json.loads(source)["стенд"]["rules"] == doc["rules"]
    assert doc["sources"][0]["sha256"] == hashlib.sha256(source).hexdigest()
    return rule, doc, source


@pytest.mark.parametrize("task_id", ["st-fleet-1", "st-fleet-healthy-1"])
@pytest.mark.parametrize("stage", ["rule", "typed", "source", "smt"])
def test_causal_fleet_measure_reaches_every_actual_subject_stage(catalog, task_id, stage):
    task = declared(catalog, "fleet_exclusions", task_id)
    rule, doc, source = typed(catalog, "fleet_exclusions", task["world"])
    statements = {
        "rule": rule["statement"],
        "typed": doc["rules"][0]["statement"],
        "source": json.loads(source)["стенд"]["rules"][0]["statement"],
        "smt": smt_verify.subject_of(doc)[0]["statement"],
    }
    sem = catalog.family("fleet_exclusions")["semantics"]
    assert sem["measure"] == "назначение без действующего допуска на тип судна — недопустимо"
    assert sem["scope"] == "назначенные сотрудники и рейсы названных типов судов"
    for field in ("measure", "scope", "unit", "text"):
        assert sem[field] in statements[stage], (task_id, stage, field)
    cells = doc["rules"][0]["template_fit"]["params"]
    assert set(cells) == {"fleet_types"}, "do not invent an expiry condition"
    assert cells["fleet_types"]["value"] == task["world"]["params"]["fleet_types"] == ["T2"]
    assert cells["fleet_types"]["source"]["kind"] == "code"


@pytest.mark.parametrize("family", FAMILIES)
def test_all_eighteen_families_keep_exact_semantics_and_metadata(catalog, family):
    record = catalog.family(family)
    before = copy.deepcopy(record)
    meta = st._family_meta(catalog)[family]
    assert set(meta) == META_KEYS
    assert {k: meta[k] for k in ("kind", "zone", "title")} == {
        k: record[k] for k in ("kind", "zone", "title")}
    assert meta["params"] == sorted(p["name"] for p in record["params"])
    assert meta["registry_rev"] == catalog.fingerprint()
    # These are the independently declared four semantic fields, in source
    # order. Different counts/formulas remain descriptions, not new demands.
    lines, seen = [], set()
    for key, label in FIELDS:
        value = record["semantics"].get(key)
        if isinstance(value, str) and value.strip() and value not in seen:
            lines.append(f"{label}: {value}")
            seen.add(value)
    assert meta["statement"] == "\n".join(lines)
    world = copy.deepcopy(record["stand_tasks"][0]["world"])
    rule = rule_of(catalog, family, world)
    assert rule["statement"] == meta["statement"]
    assert rule["kind"] == record["kind"] and rule["zone"] == record["zone"]
    assert record == before


@pytest.mark.parametrize("semantics", [None, {}, "not an object", [],
    {"text": None, "measure": None, "scope": None, "unit": None},
    {"text": "", "measure": " \t", "scope": "\n", "unit": "\r\n"},
    {"text": 0, "measure": False, "scope": ["not prose"], "unit": {"unit": "x"}},
    {"unknown": "not an authorized semantic field"},
])
def test_empty_null_or_non_text_semantics_fall_back_to_title(semantics):
    assert statement_for(semantics) == "Резервный заголовок"


def test_absent_semantics_and_absent_title_do_not_invent_text():
    record = {"id": "example", "params": []}
    assert st._family_meta(Records([record]))["example"]["statement"] is None
    assert statement_for({}, title=None) is None


@pytest.mark.parametrize("field,label", FIELDS)
def test_each_nonempty_semantic_field_survives_without_text(field, label):
    assert statement_for({field: "точное значение"}) == f"{label}: точное значение"


@pytest.mark.parametrize("semantics,expected", [
    ({"text": "повтор", "measure": "повтор", "scope": "повтор", "unit": "повтор"},
     "Описание: повтор"),
    ({"text": None, "measure": "повтор", "scope": "повтор", "unit": "единица"},
     "Измерение: повтор\nЕдиница: единица"),
    ({"text": "текст", "measure": "Текст", "scope": "текст ", "unit": " текст"},
     "Описание: текст\nИзмерение: Текст\nОбласть: текст \nЕдиница:  текст"),
    ({"text": "длинный текст", "measure": "текст", "scope": "область", "unit": "шт"},
     "Описание: длинный текст\nИзмерение: текст\nОбласть: область\nЕдиница: шт"),
])
def test_only_exact_repeated_values_are_deduplicated(semantics, expected):
    assert statement_for(semantics) == expected


def test_metadata_or_expected_fields_are_not_semantic_sources():
    record = {"id": "example", "title": "fallback", "kind": "HARD", "zone": "BOTH",
              "params": [{"name": "p", "note": "DO-NOT-PROJECT-PARAM-NOTE"}],
              "note": "DO-NOT-PROJECT-RECORD-NOTE", "history": ["DO-NOT-PROJECT-HISTORY"],
              "origin": "DO-NOT-PROJECT-ORIGIN", "expected": "DO-NOT-PROJECT-EXPECTED",
              "semantics": {"text": "описание", "measure": "измерение",
                            "unknown": "DO-NOT-PROJECT-UNKNOWN"}}
    meta = st._family_meta(Records([record]))["example"]
    assert meta["statement"] == "Описание: описание\nИзмерение: измерение"
    assert "DO-NOT-PROJECT" not in meta["statement"]
    assert meta["params"] == ["p"]


def test_changed_declared_measure_changes_subject_not_a_hardcoded_fleet_patch(catalog):
    first = copy.deepcopy(catalog.family("fleet_exclusions"))
    second = copy.deepcopy(first)
    second["semantics"]["measure"] = "Другая явно объявленная мера для контрфактической записи"
    world = declared(catalog, "fleet_exclusions", "st-fleet-1")["world"]
    subjects = []
    for record in (first, second):
        info = st._family_meta(Records([record]))[record["id"]]
        rule = sa._rule_of(world, record["id"], info, info["params"])
        subjects.append(smt_verify.subject_of({"rules": [rule]}))
    assert subjects[0] != subjects[1]
    assert first["semantics"]["measure"] in subjects[0][0]["statement"]
    assert second["semantics"]["measure"] in subjects[1][0]["statement"]
    assert first["semantics"]["measure"] not in subjects[1][0]["statement"]


def test_same_norm_for_fleet_twins_does_not_depend_on_rating_outcome(catalog):
    bad = declared(catalog, "fleet_exclusions", "st-fleet-1")["world"]
    healthy = declared(catalog, "fleet_exclusions", "st-fleet-healthy-1")["world"]
    assert bad["crews"][0]["ratings"] == ["T1"]
    assert healthy["crews"][0]["ratings"] == ["T1", "T2"]
    _, bad_doc, bad_source = typed(catalog, "fleet_exclusions", bad)
    _, healthy_doc, healthy_source = typed(catalog, "fleet_exclusions", healthy)
    assert bad_source != healthy_source, "the actual source records the different data"
    assert bad_doc["rules"] == healthy_doc["rules"]
    assert smt_verify.subject_of(bad_doc) == smt_verify.subject_of(healthy_doc)


@pytest.mark.parametrize("expected", [None, {"kotlin_constraint": {"amount": 999, "matches": 999}},
                                      {"statement": "INVENTED-NORM-FROM-EXPECTED"}])
def test_world_expected_cannot_change_statement_cells_or_smt_subject(catalog, expected):
    task = declared(catalog, "fleet_exclusions", "st-fleet-1")
    _, before, original_source = typed(catalog, "fleet_exclusions", task["world"])
    task["expected"] = copy.deepcopy(expected)
    task["world"]["expected"] = copy.deepcopy(expected)
    _, after, new_source = typed(catalog, "fleet_exclusions", task["world"])
    assert before["rules"] == after["rules"]
    assert smt_verify.subject_of(before) == smt_verify.subject_of(after)
    assert smt_verify.subject_fingerprint(before) == smt_verify.subject_fingerprint(after)
    # The honest source includes its entire world: adding a field changes its
    # bytes, but it cannot become a norm or a manufactured result.
    assert original_source != new_source


@pytest.mark.parametrize("family,task_id", [
    ("balanced_workload", "st-spread-native-1"),
    ("calendar_windows", "st-window-1"),
    ("duty_extension_penalty", "st-extension-1"),
    ("layover_penalty", "st-layover-1"),
])
def test_neighbor_soft_semantics_do_not_become_hard_demands(catalog, family, task_id):
    task = declared(catalog, family, task_id)
    _, doc, _ = typed(catalog, family, task["world"])
    row = doc["rules"][0]
    assert row["kind"] == "SOFT" and row["weight"] == 1
    assert smt_verify.subject_of(doc) == []
    sem = catalog.family(family)["semantics"]
    assert f"Измерение: {sem['measure']}" in row["statement"]
    assert f"Область: {sem['scope']}" in row["statement"]


def test_typed_crew_override_remains_explicit_and_unconditional(catalog):
    world = declared(catalog, "fleet_exclusions", "st-fleet-1")["world"]
    world["params"] = {"crew_size": 2}
    rule, doc, _ = typed(catalog, "crew_composition", world)
    assert catalog.family("crew_composition")["semantics"]["text"] in rule["statement"]
    statement = doc["rules"][0]["statement"]
    assert statement.startswith("Синтетический мир: численность места crew_size = 2 человек, без условия варианта.")
    assert "расширенн" not in statement.lower()
    assert tp.crew_size_sources(doc) == {2: rule["id"]}
    assert doc["rules"][0]["template_fit"]["params"]["crew_size"]["condition"] is None


def test_world_parameter_keeps_its_carrier_and_is_not_a_hard_constraint(catalog):
    world = declared(catalog, "fleet_exclusions", "st-fleet-1")["world"]
    world["params"] = {"base_tz_offset_min": 0}
    _, doc, _ = typed(catalog, "time_scale", world)
    row = doc["rules"][0]
    assert catalog.family("time_scale")["semantics"]["measure"] is None
    assert row["kind"] == "HARD"
    assert "Измерение:" not in row["statement"]
    assert smt_verify.subject_of(doc) == []
    assert tp.time_declaration_of(doc, None) == {
        "time_scale": "base-local", "base_tz_offset_min": 0,
        "source_rule": "R-STAND-time_scale"}


def test_saved_typed_statement_is_not_rewritten(catalog):
    world = declared(catalog, "fleet_exclusions", "st-fleet-1")["world"]
    _, doc, _ = typed(catalog, "fleet_exclusions", world)
    row = copy.deepcopy(doc["rules"][0])
    row["statement"] = "Уже сохранённая формулировка с собственной историей"
    before = copy.deepcopy(row)
    after = ss.model_doc([row], world=world, family="fleet_exclusions", catalog=catalog)
    assert row == before and after["rules"][0] == before
