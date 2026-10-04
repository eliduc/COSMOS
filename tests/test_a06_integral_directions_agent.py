"""A06: missing directions through both real, validated catalogue doors.

Temporary catalogue copies only; monkeypatch selects their environment path.
No judge, reader, readiness, builder or projection function is replaced. The
consumer check is its objective boundary, not a whole ZIP/execution verdict.
Explicit synthetic 1.20 input does not change the producer's SPEC_VERSION.
The legacy control uses explicit 1.18, not the already accepted default fix.
No package release, SMT/LLM, process, server, JVM or solver is invoked.
"""
import copy
import json

import pytest
import template_catalog as tc

from cdpm import catalog_bridge as cb, export_solver, model
from vmp import catalog_access as ca, criteria, objective_projection as op
from webapp import task_package as consumer
from test_a06_model_catalogue import document
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken


KIND = "min_person_days"


def _forget():
    cb.forget()
    ca.forget()
    tc.forget_current()


@pytest.fixture(autouse=True)
def isolated_catalogue_cache():
    _forget()
    yield
    _forget()


def _select(monkeypatch, folder):
    assert cb.ENV_CATALOG == ca.CATALOG_ENV
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    _forget()


def _criterion():
    return {
        "kind": KIND, "direction": "minimize", "label": "A06 catalogue proof",
        "source_rule": "O-1", "rate_per_duty": 77,
        "rates_version": {
            "source": "O-1", "version": "v7", "effective_from": "2030-01-01"},
    }


def _has_missing_dictionary(problems):
    return any(p.code == "D02" and p.where == "dictionaries.directions"
               and p.severity == "error" for p in problems)


def test_healthy_060_reaches_both_real_doors_and_objective_boundary(tmp_path, monkeypatch):
    folder = _broken(tmp_path, modern_docs)
    _select(monkeypatch, folder)
    cdpm_cat, cosmos_cat = cb.catalog(), ca.current()
    assert cdpm_cat is cosmos_cat
    assert cdpm_cat.version() == "0.7.0"
    assert not [p for p in cdpm_cat.validate() if p.severity == "error"]
    assert cb.criterion_direction(KIND) == "minimize"

    doc = document()
    before = copy.deepcopy(doc)
    assert model.objective_direction_problems(doc["objectives"]) == []
    # Actual producer objective bytes, not a substitute projection fixture.
    objective = json.loads(export_solver.build_objective_json(doc, spec_version="1.20"))
    pkg = {"manifest": {"spec_version": "1.20"}, "model": doc,
           "objective": objective}
    assert consumer.check_objective_projection(pkg) == []
    assert pkg["objective_projection"]["judged"] is True
    assert pkg["objective_projection"]["catalog"]["fingerprint"] == cdpm_cat.fingerprint()
    assert doc == before

    args, provenance = [], {}
    assert criteria.apply_criterion(_criterion(), args, provenance) is None
    assert args[args.index("--w-span-day") + 1] == "77"
    assert provenance["--w-span-day"]["value"] == 77


@pytest.mark.parametrize("first_door", ["cdpm", "cosmos"])
def test_060_missing_dictionary_refuses_both_doors_without_reader_substitutes(
        tmp_path, monkeypatch, first_door):
    # First prove a healthy catalogue reaches both doors in this process;
    # their shared reader/cache must not conceal the subsequent bad input.
    healthy = _broken(tmp_path / "healthy", modern_docs)
    _select(monkeypatch, healthy)
    assert cb.catalog() is ca.current()
    assert cb.criterion_direction(KIND) == "minimize"

    def remove_dictionary(docs):
        modern_docs(docs)
        del docs["dictionaries.json"]["directions"]

    broken = _broken(tmp_path / "missing", remove_dictionary)
    _select(monkeypatch, broken)
    raw = tc.load(broken)
    assert raw.version() == "0.7.0"
    assert "directions" not in raw.dictionaries()
    # Only the dictionary is absent; missing record directions are a
    # different case already covered by test_a06_builder_direction.py.
    assert all(row["direction"] == "minimize" for row in raw.criteria().values())
    problems = raw.validate()
    assert _has_missing_dictionary(problems), problems
    assert not any(p.code in {"H02", "H03"} for p in problems), problems

    doors = {"cdpm": (cb.catalog, cb.CatalogRequired),
             "cosmos": (ca.current, tc.CatalogInvalid)}
    order = [first_door, "cosmos" if first_door == "cdpm" else "cdpm"]
    for name in order:
        read, error = doors[name]
        with pytest.raises(error) as refused:
            read()
        assert _has_missing_dictionary(refused.value.problems), name
        assert "directions" in str(refused.value), name

    with pytest.raises(cb.CatalogRequired, match="directions"):
        cb.criterion_direction(KIND)
    direction_problems = model.objective_direction_problems(document()["objectives"])
    assert any("criterion-direction" in p and "directions" in p
               for p in direction_problems), direction_problems

    args, provenance = ["--existing", "1"], {"existing": {"value": 1}}
    before = copy.deepcopy((args, provenance))
    with pytest.raises(ca.CATALOG_ERRORS, match="directions"):
        criteria.apply_criterion(_criterion(), args, provenance)
    assert (args, provenance) == before
    coverage = criteria.coverage(_criterion())
    assert coverage["computable"] is False
    assert "directions" in coverage["reason"]

    pkg = {"manifest": {"spec_version": "1.20"},
           "model": document(), "objective": {}}
    with pytest.raises(consumer.TaskPackageRefused) as refused:
        consumer.check_objective_projection(pkg)
    assert refused.value.code == "criterion-direction"
    assert "directions" in str(refused.value)
    assert "objective_projection" not in pkg


def test_050_is_readable_archive_not_direction_or_execution_approval(tmp_path, monkeypatch):
    folder = _broken(tmp_path, lambda docs: None)
    _select(monkeypatch, folder)
    cdpm_cat, cosmos_cat = cb.catalog(), ca.current()
    assert cdpm_cat is cosmos_cat
    assert cdpm_cat.version() == "0.5.0"
    assert "directions" not in cdpm_cat.dictionaries()
    assert "direction" not in cdpm_cat.criteria()[KIND]
    assert not [p for p in cdpm_cat.validate() if p.severity == "error"]

    doc = document()
    before = copy.deepcopy(doc)
    # Preserve the pinned archive path; this is not permission to execute it.
    objective = json.loads(export_solver.build_objective_json(doc, spec_version="1.18"))
    pkg = {"manifest": {"spec_version": "1.18"}, "model": doc,
           "objective": objective}
    assert consumer.check_objective_projection(pkg) == []
    status = pkg["objective_projection"]
    assert status["judged"] is False
    assert status["words"] == op.archive_words("1.18")
    assert "не есть разрешение исполнения" in status["words"]
    assert doc == before

    with pytest.raises(cb.CatalogRequired, match="направлен"):
        cb.criterion_direction(KIND)
    args, provenance = ["--existing", "1"], {"existing": {"value": 1}}
    before = copy.deepcopy((args, provenance))
    with pytest.raises(criteria.CriteriaError, match="направлен"):
        criteria.apply_criterion(_criterion(), args, provenance)
    assert (args, provenance) == before
    assert criteria.coverage(_criterion())["computable"] is False
