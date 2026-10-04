"""Native draft.6 inputs for an explicitly separate JVM component probe.

No JVM/server/channel is started. No support approval or admission override.
The duty proof translator is finite and must never be reused for another HARD
family. Calendar companions use a separately closed, exact numeric grammar;
unknown statements, metadata or scopes refuse instead of fabricating proof.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import z3
import template_catalog as tc
from template_catalog import bindings
from cdpm import bindings_build as cbb, catalog_bridge as cb
from cdpm import export_task_package as tp, model as model, smt_verify as sv
from cdpm.gates import gate_task_package
from vmp import bindings_build as vbb, catalog_access as ca
from vmp import stand_adapters, stand_solver, stand_tasks
from webapp import task_package
from test_a06_native_stand_proof_agent import local_report, MARK, PREFIX as DUTY_PREFIX


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration/0.7.0-draft.6"
# This is the worktree's base, not an exact-SHA acceptance of uncommitted inputs.
BASE_COMMIT = "f96e0e12e93e07fa037e1a39d8c6371a8667e679"

# Reviewed grammar of exactly the companion HARD rules in st-window-1.
# A null condition on min_turnaround means BOTH named areas, never one
# representative area. Separate symbols also prevent conflating the rest floors.
WINDOW_GRAMMAR = {
    "R-STAND-connection_windows": (
        "Описание: стыковки: минимальные, максимальные, пороговые окна станций; область — уточнителем параметра (база / вне базы), без уточнителя — обе области\n"
        "Измерение: стыковка короче минимума своей области — недопустимо\n"
        "Область: промежутки между соседними рейсами внутри смены\n"
        "Единица: минута стыковки",
        {"min_turnaround": ("duration_minutes", "min", "area",
                            "минимальная стыковка; уточнитель области: база / вне базы; без уточнителя — обе области",
                            ("turnaround_home_min", "turnaround_outstation_min"))}),
    "R-STAND-duty_limits": (
        DUTY_PREFIX,
        {"max_duty_duration": ("duration_minutes", "max", None, "базовый предел длительности смены", ("duty_minutes",)),
         "max_pairing_days": ("count", "max", None, "предел суток командировки", ("pairing_days",))}),
    "R-STAND-rest_floors": (
        "Описание: полы отдыха между сменами: в базе и вне базы, длинные, ярусные\n"
        "Измерение: отдых короче пола своей области (база / вне базы) — недопустимо\n"
        "Область: промежутки между соседними сменами одного экипажа\n"
        "Единица: минута отдыха",
        {"min_rest_home": ("duration_minutes", "min", None, "пол отдыха в базе", ("home_rest_min",)),
         "min_rest_outstation": ("duration_minutes", "min", None, "пол отдыха вне базы", ("outstation_rest_min",))}),
}
CELL_KEYS = {"name", "kind", "qualifier", "value", "unit", "relation", "condition", "note"}


def translate_window_subject(subject):
    """Finite explicit numeric world grammar, NOT arbitrary prose semantics."""
    if (not isinstance(subject, list) or len(subject) != len(WINDOW_GRAMMAR)
            or {row.get("id") for row in subject} != set(WINDOW_GRAMMAR)):
        raise ValueError("calendar component requires all three exact HARD subjects")
    decls, assertions = [], []
    for row in subject:
        if set(row) != {"id", "kind", "statement"} or row["kind"] != "HARD":
            raise ValueError("unsupported subject shape/kind")
        prefix, separator, raw = row["statement"].partition(MARK)
        wanted_prefix, metadata = WINDOW_GRAMMAR[row["id"]]
        if not separator:
            raise ValueError("unsupported window companion statement grammar")
        cells = model.loads_strict(raw)
        if (not isinstance(cells, list) or len(cells) != len(metadata)
                or any(not isinstance(c, dict) for c in cells)
                or {c.get("name") for c in cells} != set(metadata)):
            raise ValueError("missing, duplicate or additional companion parameter")
        terms = []
        for cell in cells:
            unit, relation, qualifier, note, symbols = metadata[cell["name"]]
            if (set(cell) != CELL_KEYS or cell["kind"] != "параметр"
                    or cell["unit"] != unit or cell["relation"] != relation
                    or cell["qualifier"] != qualifier or cell["note"] != note
                    or cell["condition"] is not None
                    or type(cell["value"]) is not int or cell["value"] <= 0):
                raise ValueError("unsupported companion parameter metadata/value/scope")
            operator = ">=" if relation == "min" else "<="
            for symbol in symbols:
                decls.append(f"(declare-const {symbol} Int)")
                terms.append(f"({operator} {symbol} {cell['value']})")
        quotes = "; ".join(
            f"{cell['name']} = {cell['value']}" + (" мин" if cell["unit"] == "duration_minutes" else "")
            for cell in sorted(cells, key=lambda cell: cell["name"]))
        if prefix != (wanted_prefix + "\nИсточник синтетического правила: стенд"
                      + "\nЦитаты параметров исходного мира: " + quotes):
            raise ValueError("unsupported window companion statement grammar")
        assertions.append({"rule": row["id"], "smt": "(and " + " ".join(terms) + ")"})
    return decls, assertions


def calendar_local_report(doc, raw):
    subject = sv.subject_of(doc)
    decls, assertions = translate_window_subject(subject)
    calls = []

    def local_translation(prompt):
        assert all(row["id"] + " | " + row["statement"] in prompt for row in subject)
        calls.append(prompt)
        return json.dumps({"decls": decls, "asserts": assertions})

    report = sv.verify(doc, local_translation, model_sha256=sv.model_fingerprint(raw))
    assert len(calls) == 1
    assert report["n_rules"] == report["n_translated"] == 3
    assert report["n_untranslatable"] == 0 and report["verdict"] == "sat"
    assert report["subject_sha256"] == sv.subject_fingerprint(doc)
    assert report["model_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["translator"]["provider"] is None and report["translator"]["model"] is None
    return report


def _hashes(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@pytest.fixture
def component_catalog(tmp_path, monkeypatch):
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    original = list(CATALOG.rglob("*"))
    original = [p for p in original if p.is_file()]
    original += [ROOT / "cdpm/bindings.json", ROOT / "vmp/bindings.json",
                 ROOT / "vmp/bindings_seed.json"]
    before = _hashes(original)
    folder = tmp_path / "draft6"
    shutil.copytree(CATALOG, folder)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    for key in ("CREW_BINDINGS_SEED", "COSMOS_TEST_CATALOG_GROWN", "CREW_SOLVER_URL",
                "CREW_CHANNEL_URL", "CREW_CHANNEL_TOKEN", "CREW_VMP_DETECTORS"):
        monkeypatch.delenv(key, raising=False)
    cb.forget(); ca.forget(); tc.forget_current()
    try:
        cat = tc.current(folder, validated=True)
        assert cat.version() == "0.7.0-draft.6"
        assert cat.fingerprint() == cat.declared_fingerprint()
        paths = {}
        for product, builder in (("CDPM", cbb), ("COSMOS", vbb)):
            doc = builder.build(cat, commit=BASE_COMMIT, generated_at=generated_at, stand_results=None)
            assert all(not row["stand_verified"] for row in doc["bindings"])
            path = tmp_path / (product + "-unsigned-bindings.json")
            path.write_text(builder.dump(doc), encoding="utf-8")
            paths[product] = path
            monkeypatch.setenv(cb.SUPPORT_ENV[product], str(path))
        support = bindings.support_table(cat, paths, approved_by=None,
                                         computed_at=generated_at)
        assert support["cells"] and all(c["approved"] is None and c["state"] != "есть"
                                        for c in support["cells"])
        (folder / "support.json").write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")

        def forbidden(*args, **kwargs):
            raise AssertionError("external SMT channel is forbidden in native component preparation")

        monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
        yield cat
    finally:
        cb.forget(); ca.forget(); tc.forget_current()
        assert _hashes(original) == before


def test_prepare_native_duty_and_preserve_calendar_support_refusal(component_catalog, tmp_path, record_property):
    cat = component_catalog
    target = tmp_path / "component-inputs"
    target.mkdir()
    record_property("a06_component_inputs", str(target))
    for family, task_id in (("duty_limits", "st-duty-1"), ("calendar_windows", "st-window-1")):
        declared = next(t for t in cat.family(family)["stand_tasks"] if t["id"] == task_id)
        world = copy.deepcopy(declared["world"])
        meta = stand_tasks._family_meta(cat)
        families = stand_tasks.families_for(world, family, meta)
        rules = stand_solver._rules_for(world, families, meta)
        doc = stand_solver.model_doc(rules, world=world, family=family, catalog=cat)
        assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
        base = tmp_path / (task_id + "-source")
        run = stand_solver._write_run(base, doc, stand_solver._sheets(world),
                                     source=stand_solver._stand_source(world, rules, None))
        raw = (run / "extract/model.json").read_bytes()
        subject = sv.subject_of(doc)
        record_property(task_id + "_smt_subject", json.dumps(subject, ensure_ascii=False))
        if family == "duty_limits":
            assert families == ["duty_limits"]
            report = local_report(doc, raw)
            sv.write_report(run, report)
        else:
            report = calendar_local_report(doc, raw)
            sv.write_report(run, report)
            # Actual discovery: this native world closes both task modes until
            # support is approved. Preserve that gate as a NEGATIVE control;
            # no calendar artifact/measurement/acceptance is claimed here.
            with pytest.raises(stand_solver.StandSolverError, match="поддержка не подтверждена"):
                stand_solver.package_bytes(world, rules, stamp=stand_solver.stamp_of(cat),
                                           base=base, catalog=cat, family=family, spec_version="1.20")
            record_property("calendar_native_component_blocked", "SUPPORT_MISSING; no ZIP produced")
            assert world == declared["world"]
            assert (run / "extract/model.json").read_bytes() == raw
            continue
        package = stand_solver.package_bytes(world, rules, stamp=stand_solver.stamp_of(cat),
                                             base=base, catalog=cat, family=family, spec_version="1.20")
        directory = target / task_id
        directory.mkdir()
        archive = directory / (task_id + ".zip")
        archive.write_bytes(package)
        assert gate_task_package.check(archive)[0] == []
        received = task_package.load(archive, run_gate=False)
        assert not received["manifest"].get("stand_only")
        assert all(cell["approved"] is None for cell in received["manifest"]["support"]["cells"])
        assert world == declared["world"]
        assert (run / "extract/model.json").read_bytes() == raw
        if family == "duty_limits":
            assert received["manifest"]["allowed_modes"]["pairing"] == []
        problem = stand_adapters.world_to_problem(world)
        (directory / (task_id + ".problem.json")).write_text(json.dumps(problem, ensure_ascii=False), encoding="utf-8")
        (directory / (task_id + ".world.json")).write_text(json.dumps(
            {"world": world, "expected": declared["expected"]}, ensure_ascii=False), encoding="utf-8")
        identity = {"catalog_version": cat.version(), "catalog_fingerprint": cat.fingerprint(),
                    "task": task_id, "admission": "component-only; unsigned support; not authorized execution",
                    "provenance": {"base_commit": BASE_COMMIT, "scope": "base-plus-worktree; not exact-SHA acceptance"},
                    "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}}
        (directory / "identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
        record_property(task_id + "_identity_sha256", hashlib.sha256((directory / "identity.json").read_bytes()).hexdigest())


def _calendar_doc(cat):
    task = next(t for t in cat.family("calendar_windows")["stand_tasks"] if t["id"] == "st-window-1")
    world = copy.deepcopy(task["world"])
    meta = stand_tasks._family_meta(cat)
    families = stand_tasks.families_for(world, "calendar_windows", meta)
    assert set(families) == {"calendar_windows", "connection_windows", "duty_limits", "rest_floors", "time_scale"}
    return stand_solver.model_doc(stand_solver._rules_for(world, families, meta),
                                 world=world, family="calendar_windows", catalog=cat)


@pytest.mark.parametrize("symbol,limit,minimum", [
    ("turnaround_home_min", 45, True), ("turnaround_outstation_min", 45, True),
    ("duty_minutes", 780, False), ("pairing_days", 7, False),
    ("home_rest_min", 660, True), ("outstation_rest_min", 700, True),
])
def test_calendar_finite_proof_each_boundary_and_violation(component_catalog, symbol, limit, minimum):
    subject = sv.subject_of(_calendar_doc(component_catalog))
    decls, assertions = translate_window_subject(subject)
    assert len(decls) == len(set(decls)) == 6
    # Every OTHER variable stays at its independently listed healthy boundary.
    # Violating either area must fail, even when its counterpart is healthy.
    healthy = {"turnaround_home_min": 45, "turnaround_outstation_min": 45,
               "duty_minutes": 780, "pairing_days": 7,
               "home_rest_min": 660, "outstation_rest_min": 700}
    script = "\n".join(decls + [f"(assert {a['smt']})" for a in assertions])
    for delta in (-1, 0, 1):
        solver = z3.Solver()
        solver.add(z3.parse_smt2_string(script))
        for name, boundary in healthy.items():
            solver.add(z3.Int(name) == (limit + delta if name == symbol else boundary))
        expected = z3.unsat if (delta < 0 if minimum else delta > 0) else z3.sat
        assert solver.check() == expected, (symbol, delta)


@pytest.mark.parametrize("damage", ["prefix", "relation", "unit", "qualifier", "condition",
                                    "note", "missing_condition", "extra_field", "extra_param",
                                    "missing_param", "duplicate_param", "bool_value", "str_value",
                                    "fractional_value", "unknown_param", "missing_rule", "extra_rule"])
def test_calendar_finite_proof_refuses_unknown_grammar_without_dropping_scope(component_catalog, damage):
    subject = sv.subject_of(_calendar_doc(component_catalog))
    row = next(r for r in subject if r["id"] == "R-STAND-connection_windows")
    prefix, _, raw = row["statement"].partition(MARK)
    cells = json.loads(raw)
    cell = cells[0]
    if damage == "prefix": prefix += "; дополнительное требование"
    elif damage == "relation": cell["relation"] = "max"
    elif damage == "unit": cell["unit"] = "count"
    elif damage == "qualifier": cell["qualifier"] = None
    elif damage == "condition": cell["condition"] = {"kind": "area", "value": "база"}
    elif damage == "note": cell["note"] += "; только в базе"
    elif damage == "missing_condition": del cell["condition"]
    elif damage == "extra_field": cell["unrecognized"] = True
    elif damage == "extra_param": cells.append({**cell, "name": "max_turnaround"})
    elif damage == "missing_param": cells.clear()
    elif damage == "duplicate_param": cells.append(copy.deepcopy(cell))
    elif damage == "bool_value": cell["value"] = True
    elif damage == "str_value": cell["value"] = "45"
    elif damage == "fractional_value": cell["value"] = 45.5
    elif damage == "unknown_param": cell["name"] = "unknown"
    elif damage == "missing_rule": subject.remove(row)
    elif damage == "extra_rule": subject.append({**row, "id": "R-UNTRANSLATED"})
    row["statement"] = prefix + MARK + json.dumps(cells, ensure_ascii=False)
    with pytest.raises(ValueError):
        translate_window_subject(subject)


def test_calendar_report_proves_exact_whole_subject_and_raw_model(component_catalog):
    doc = _calendar_doc(component_catalog)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    report = calendar_local_report(doc, raw)
    assert {r["rule"] for r in report["translations"]} == set(WINDOW_GRAMMAR)
    assert report["dropped"] == report["untranslated"] == report["malformed"] == []


@pytest.mark.parametrize("rule_id", sorted(WINDOW_GRAMMAR))
@pytest.mark.parametrize("label", ["Описание", "Измерение", "Область", "Единица"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_calendar_finite_proof_requires_each_exact_semantic_field(component_catalog, rule_id, label, damage):
    subject = sv.subject_of(_calendar_doc(component_catalog))
    translate_window_subject(subject)  # Keep the exact healthy subject accepted.
    row = next(r for r in subject if r["id"] == rule_id)
    lines = row["statement"].splitlines()
    selected = [line for line in lines if line.startswith(label + ": ")]
    assert len(selected) == 1
    line = selected[0]
    if damage == "missing":
        lines.remove(line)
    else:
        lines[lines.index(line)] = line + "; иное условие"
    row["statement"] = "\n".join(lines)
    with pytest.raises(ValueError, match="unsupported window companion statement grammar"):
        translate_window_subject(subject)
