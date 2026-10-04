"""A06/01: exact draft.3 direction tokens, including the public shared reader.

Only read the candidate and historical catalogue; mutations are rehashed in
pytest tmp_path. No release, bindings, service, JVM, network or subprocess.
"""
import hashlib
import json
from pathlib import Path

import pytest

import template_catalog as tc
from template_catalog.canon import fingerprint_of
from cdpm import catalog_bridge as cb
from vmp import objective_projection as op
from test_a06_integration_catalog import CRITERIA, SNAPSHOT_FILES


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration" / "0.7.0-draft.3"
FINGERPRINT = "dd8687d5f8574f2f3573b5a908a15c7c7bb8b1142991bb1297c2e445cbfaa02e"


def _hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (CATALOG, ROOT / "catalog")
            for p in folder.rglob("*") if p.is_file()
            and "__pycache__" not in p.parts}


@pytest.fixture(scope="module", autouse=True)
def immutable_catalogues():
    before = _hashes()
    yield
    assert _hashes() == before


@pytest.fixture(autouse=True)
def clean_cache():
    cb.forget()
    tc.forget_current()
    yield
    cb.forget()
    tc.forget_current()


def _candidate(tmp_path, kind, direction, *, missing=False):
    folder = tmp_path / "catalog"
    for name in SNAPSHOT_FILES:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CATALOG / name).read_bytes())
    path = folder / "criteria.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    row = next(row for row in doc["criteria"] if row["id"] == kind)
    if missing:
        del row["direction"]
    else:
        row["direction"] = direction
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    header = tc.load(folder).header.copy()
    header["fingerprint"] = fingerprint_of(tc.load(folder).body())
    (folder / "catalog.json").write_text(json.dumps(header), encoding="utf-8")
    return folder


def _model(kind, direction):
    return {"meta": {"spec_version": "1.20"}, "rules": [], "questions": [],
            "sources": [{"sid": "S1"}], "objectives": [
                {"id": "O1", "zone": "PAIRING", "metric": kind,
                 "direction": direction, "sources": [{"sid": "S1"}]}]}


def test_actual_draft3_has_all_eight_exact_declared_directions():
    cat = tc.current(CATALOG, validated=True)
    assert cat.version() == "0.7.0-draft.3"
    assert cat.fingerprint() == fingerprint_of(cat.body()) == FINGERPRINT
    assert set(cat.criteria(status=None)) == CRITERIA
    assert sorted(cat.dictionaries()["directions"]) == ["maximize", "minimize"]
    assert all(row["status"] == "есть" and row["direction"] == "minimize"
               for row in cat.criteria(status=None).values())
    assert json.loads((CATALOG / "schema/contract.json").read_text(encoding="utf-8")) == {
        "catalog_version": cat.version(), "schema_contract": "catalog-0.7.schema.json",
        "schema_file": "catalog.schema.json"}
    legacy = tc.current(ROOT / "catalog", validated=True)
    assert legacy.version() == "0.5.0"
    assert legacy.fingerprint() == "5534196f824aff80156e33cd5fa741c869c5fd8d23e48b39e295d15d73e8b7d9"


@pytest.mark.parametrize("kind", sorted(CRITERIA))
@pytest.mark.parametrize("direction", ["minimize", "maximize"])
def test_each_declared_direction_reaches_reader_projection_and_bridge(
        tmp_path, monkeypatch, kind, direction):
    folder = _candidate(tmp_path, kind, direction)
    cat = tc.current(folder, validated=True)
    model = _model(kind, direction)
    for source in (folder, cat):
        assert op.criterion_direction(source, kind) == direction
        projected = op.project(model, source)
        assert projected["pairing"]["direction"] == direction
        assert projected["pairing"]["objectives"][0]["metric_direction"] == direction
        assert op.compare(projected, model, source) == []
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    assert cb.criterion_direction(kind) == direction


BAD = [
    pytest.param(None, True, id="missing"),
    pytest.param(None, False, id="null"),
    pytest.param("sideways", False, id="unknown"),
    pytest.param("MINIMIZE", False, id="uppercase-minimize"),
    pytest.param(" minimize ", False, id="padded-minimize"),
    pytest.param("MAXIMIZE", False, id="uppercase-maximize"),
    pytest.param(" maximize ", False, id="padded-maximize"),
    pytest.param(True, False, id="boolean"),
    pytest.param(["minimize"], False, id="array"),
    pytest.param({"direction": "minimize"}, False, id="object"),
]


@pytest.mark.parametrize("kind", sorted(CRITERIA))
@pytest.mark.parametrize("direction,missing", BAD)
def test_invalid_catalogue_token_is_not_repaired_by_the_shared_reader(
        tmp_path, monkeypatch, kind, direction, missing):
    folder = _candidate(tmp_path, kind, direction, missing=missing)
    cat = tc.load(folder)
    errors = [p for p in cat.validate() if p.severity == "error"]
    assert not any(p.code == "H02" for p in errors), errors
    assert any(p.code == "X20" and kind in p.where and "direction" in p.where
               for p in errors), errors
    with pytest.raises(tc.CatalogInvalid):
        tc.current(folder, validated=True)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    with pytest.raises(cb.CatalogRequired):
        cb.criterion_direction(kind)
    model = _model(kind, "maximize" if "MAXIMIZE" == direction or
                   " maximize " == direction else "minimize")
    for source in (folder, cat):
        with pytest.raises(op.ObjectiveProjectionError, match=kind):
            op.criterion_direction(source, kind)
        with pytest.raises(op.ObjectiveProjectionError, match=kind):
            op.project(model, source)
        problems = op.compare({}, model, source)
        assert len(problems) == 1 and problems[0]["code"] == "criterion-direction"
        assert kind in problems[0]["words"]
