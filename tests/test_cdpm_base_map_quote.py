"""CDPM-384: base/count pairs need evidence in their own source quotation."""
import copy
import json

import pytest

from cdpm import model as m, export_task_package as tp
from cdpm import family_params as fp
from test_cdpm_a05_base_map_projection import _rule, _record, VALUES, QUOTE
from test_cdpm_task_package import _ready_doc, _run_dir
from test_cdpm_a05_carriers_barrier import _repack, _gate_problems


BAD = [
    (VALUES, "Резерв держать по нормативу."),
    ({**VALUES, "XXX": 2}, QUOTE),
    ({"PLY": 1, "SVG": 2, "GRT": 0}, QUOTE),
    (VALUES, QUOTE.replace("PLY", "XPLY")),
    (VALUES, QUOTE.replace("PLY ≥ 2", "PLY ≥ 20")),
    (VALUES, QUOTE.replace("PLY ≥ 2", "PLY ≥ 2.5")),
    (VALUES, QUOTE.replace("PLY ≥ 2", "PLY ≥ -2")),
    ({"PLY": 2}, "Ранее PLY ≥ 2 экипажа, теперь PLY ≥ 3 экипажа."),
    ({"PLY": 2}, "Резерв PLY -2 экипажа"),
    ({"PLY": 2}, "Резерв PLY - 2 экипажа"),
    ({"PLY": 2}, "Резерв PLY ≥ 2e3 экипажа"),
    ({"PLY": 2}, "Резерв PLY ≥ 2 000 экипажей"),
    ({"PLY": 2}, "Резерв PLY ≥ 2–3 экипажа"),
    ({"PLY": 2}, "Резерв PLY ≥ 2/3 экипажа"),
    ({"PLY": 2}, "Резерв PLY ≥ 2 : 30"),
    ({"PLY": 2}, "Резерв PLY ≥ 2 + 3 экипажа"),
]


@pytest.mark.parametrize("digits", ["1" * 25, "1" * 100])
def test_base_count_quote_obeys_the_shared_numeric_budget(digits):
    # The old direct Decimal conversion accepted a token beyond K02's limit.
    assert fp.base_map_quote_problems({"SVO": int(digits)}, f"SVO: {digits}")


@pytest.mark.parametrize("digits,value", [
    ("0" * 5000 + "7", 7), ("0" * 5000, 0), ("1" * 24, int("1" * 24)),
])
def test_base_count_quote_keeps_permitted_lengths(digits, value):
    assert fp.base_map_quote_problems({"SVO": value}, f"SVO: {digits}") == []


@pytest.mark.parametrize("value,quote", BAD)
@pytest.mark.parametrize("reader", ["projection", "logic", "model"])
@pytest.mark.parametrize("carrier", ["dict", "packed"])
def test_unproven_base_count_is_refused_by_all_readers(value, quote, reader, carrier):
    if carrier == "packed":
        value = ",".join(f"{base}:{number}" for base, number in value.items())
    rule = _rule(value=value, quote=quote)
    before = copy.deepcopy(rule)
    if reader == "projection":
        with pytest.raises(tp.TaskPackageError) as error:
            tp.derive_params_from_landing(rule)
        assert error.value.items == ["R-RES/reserve_crews"]
    elif reader == "logic":
        _records, problems = m.logic_param_records(rule, selected_names={"reserve_crews"})
        assert problems
    else:
        problems = m.validate_template_fit({"rules": [rule]})
        assert any("R-RES" in p and "reserve_crews" in p for p in problems)
    assert rule == before


@pytest.mark.parametrize("quote", [QUOTE,
    "Ежедневно резерв: GRT ≥ 0, SVG ≥ 1, PLY ≥ 2 экипажа.",
    "GRT:0,PLY:2,SVG:1", "PLY — 2; SVG — 1; GRT — 0",
    "PLY не менее 2 экипажей, SVG не менее 1, GRT не менее 0",
    "Ежедневно резерв: PLY\u00a0≥\u00a02 экипажа, SVG ≥ 1, GRT ≥ 0.\nСутки местные."])
@pytest.mark.parametrize("carrier", ["dict", "packed"])
def test_explicit_base_pairs_preserve_the_valid_map_and_exact_trace(quote, carrier):
    value = VALUES if carrier == "dict" else "GRT:0,PLY:2,SVG:1"
    rule = _rule(value=value, quote=quote)
    before = copy.deepcopy(rule)
    assert m.validate_template_fit({"rules": [rule]}) == []
    records = tp.derive_params_from_landing(rule)
    assert {r["qualifier"]: r["value"] for r in records} == VALUES
    assert all(r["as_written"] == quote for r in records)
    assert rule == before


def test_old_record_cannot_keep_an_unrelated_trace_when_map_quote_is_valid():
    rule = _rule(legacy=[_record("PLY", 2, as_written="Резерв по нормативу.")])
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)


@pytest.mark.parametrize("carrier", ["dict", "packed"])
@pytest.mark.parametrize("quote", ["Резерв держать по нормативу.",
    "Ежедневно резерв: PLY ≥ 1 экипаж, SVG ≥ 2, GRT ≥ 0."])
def test_package_and_rehashed_gate_refuse_unproven_map_without_changing_input(tmp_path, carrier, quote):
    value = VALUES if carrier == "dict" else "GRT:0,PLY:2,SVG:1"
    doc = _ready_doc()
    doc["rules"].append(_rule(value=value))
    run = _run_dir(tmp_path / "healthy", doc)
    original = (run / "extract/model.json").read_bytes()
    files = tp.build_package(run)
    assert _gate_problems(tmp_path, files, "healthy.zip") == []
    assert (run / "extract/model.json").read_bytes() == original

    bad = copy.deepcopy(doc)
    bad["rules"][-1]["template_fit"]["params"]["reserve_crews"]["quote"] = quote
    bad_run = _run_dir(tmp_path / "bad", bad)
    bad_original = (bad_run / "extract/model.json").read_bytes()
    with pytest.raises(tp.TaskPackageError, match="цитата не подтверждает норму базы"):
        tp.build_package(bad_run)
    assert (bad_run / "extract/model.json").read_bytes() == bad_original

    # An altered package must reach semantic checks, not fail only on its hash.
    packaged = json.loads(files["model.json"])
    row = next(r for r in packaged["rules"] if r["id"] == "R-RES")
    row["template_fit"]["params"]["reserve_crews"]["quote"] = quote
    problems = _gate_problems(tmp_path, _repack(files, packaged), "altered.zip")
    assert any("R-RES" in p and "reserve_crews" in p and
               "цитата не подтверждает норму базы" in p for p in problems), problems
    assert not any("SHA" in p or "sha256" in p for p in problems), problems
