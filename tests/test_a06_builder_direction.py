"""A06: actual builder and coverage reject direction before changing weights."""
import copy

import pytest
import template_catalog as tc
from vmp import catalog_access as ca, criteria
from test_w17b_objective_projection import _catalog, CATALOG, KIND


def criterion():
    return {"kind": KIND, "direction": "minimize", "label": "test",
            "source_rule": "O-1", "rate_per_duty": 123,
            "rates_version": {"source": "O-1", "version": "1",
                              "effective_from": "2030-01-01"}}


def use_catalog(monkeypatch, cat):
    monkeypatch.setattr(ca, "current", lambda: cat)


@pytest.mark.parametrize("direction", ["maximize", "", None, False, 0, ["minimize"]])
def test_bad_declared_direction_never_changes_builder_args(tmp_path, monkeypatch, direction):
    use_catalog(monkeypatch, _catalog(tmp_path))
    c = criterion()
    c["direction"] = direction
    args, prov = ["--existing", "1"], {"existing": {"value": 1}}
    before = copy.deepcopy((args, prov))
    with pytest.raises(criteria.CriteriaError, match="направлен|direction"):
        criteria.apply_criterion(c, args, prov)
    assert (args, prov) == before
    result = criteria.coverage(c)
    assert result["computable"] is False and "направлен" in result["reason"]


def test_catalog_maximize_cannot_use_minimizing_executor(tmp_path, monkeypatch):
    use_catalog(monkeypatch, _catalog(tmp_path, directions={KIND: "maximize"}))
    c = criterion()
    c["direction"] = "maximize"
    args, prov = [], {}
    with pytest.raises(criteria.CriteriaError, match="исполнитель"):
        criteria.apply_criterion(c, args, prov)
    assert args == [] and prov == {}
    assert criteria.coverage(c)["computable"] is False


def test_catalog_without_direction_is_not_implicitly_minimize(monkeypatch):
    use_catalog(monkeypatch, tc.load(CATALOG))
    assert "direction" not in ca.current().criteria()[KIND]
    args, prov = [], {}
    with pytest.raises(criteria.CriteriaError, match="направлен"):
        criteria.apply_criterion(criterion(), args, prov)
    assert args == [] and prov == {}
    assert criteria.coverage(criterion())["computable"] is False


@pytest.mark.parametrize("explicit", [False, True])
def test_healthy_direction_keeps_exact_cost_and_legacy_inference(tmp_path, monkeypatch, explicit):
    use_catalog(monkeypatch, _catalog(tmp_path))
    c = criterion()
    if not explicit:
        del c["direction"]  # Legacy absent field is derived from the catalogue.
    args, prov = [], {}
    assert criteria.apply_criterion(c, args, prov) is None
    assert args[args.index("--w-span-day") + 1] == "123"
    assert prov["--w-span-day"]["value"] == 123
    assert criteria.coverage(c)["computable"] is True


def test_known_kind_without_executor_keeps_gap_route(tmp_path, monkeypatch):
    use_catalog(monkeypatch, _catalog(tmp_path))
    executors = dict(criteria.EXECUTORS)
    del executors[KIND]
    monkeypatch.setattr(criteria, "EXECUTORS", executors)
    c = criterion()
    c["direction"] = "maximize"
    args, prov = [], {}
    assert isinstance(criteria.apply_criterion(c, args, prov), criteria.CriterionGap)
    assert args == [] and prov == {}
    assert "пробел шаблона" in criteria.coverage(c)["reason"]
