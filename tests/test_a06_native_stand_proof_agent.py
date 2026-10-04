"""Actual declared duty stand worlds: bounded local translation + real Z3.

No synthetic approval: both binding builders and support table are real and
unsigned. Local translation is deliberately limited to the exact duty_limits
statement grammar of the pinned catalogue. It is not a general translator or
an LLM-quality claim. Native exporter/gates are not replaced; no cloud/JVM.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest
import z3
import template_catalog as tc
from template_catalog import bindings
from cdpm import bindings_build as cbb, catalog_bridge as cb, export_task_package as tp
from cdpm import model as m, smt_verify as sv
from cdpm.gates import gate_task_package as gate
from vmp import bindings_build as vbb, catalog_access as ca
from vmp import stand_solver as ss, stand_tasks as st
from webapp import task_package as receiver
from test_a06_k15_direction_tokens import CATALOG, FINGERPRINT, _hashes


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "89dd5ed3a5264ccb80030ad2cd9d114ba71db615"
# Reviewed literal grammar of the pinned draft.3/draft.6 duty semantics.
# Do not obtain this expected text from the production renderer under test.
PREFIX = (
    "Описание: пределы рабочей смены: длительность, число посадок за смену, число суток командировки\n"
    "Измерение: смена длиннее предела, посадок больше предела, командировка дольше предела суток — недопустимо\n"
    "Область: каждая рабочая смена цепочки и каждая командировка\n"
    "Единица: минута длительности смены, посадка, сутки командировки"
)
MARK = "\nПараметры исходного синтетического мира, уточняющие только это правило: "
FIELDS = {"max_duty_duration": ("duty_minutes", "duration_minutes"),
          "max_landings_per_duty": ("landings", "count"),
          "max_pairing_days": ("pairing_days", "count")}
NOTES = {"max_duty_duration": "базовый предел длительности смены",
         "max_landings_per_duty": "предел посадок за смену",
         "max_pairing_days": "предел суток командировки"}


def translate_duty_statement(statement):
    """Fail-closed local translation of this reviewed finite grammar only."""
    prefix, separator, raw = statement.partition(MARK)
    if not separator:
        raise ValueError("unsupported duty statement grammar")
    cells = m.loads_strict(raw)
    if not isinstance(cells, list) or not cells:
        raise ValueError("missing duty parameter declarations")
    seen, terms, decls = set(), [], []
    for cell in cells:
        name = cell.get("name")
        if name not in FIELDS or name in seen:
            raise ValueError("unknown or duplicate duty parameter")
        seen.add(name)
        symbol, unit = FIELDS[name]
        if (cell.get("unit") != unit or cell.get("relation") != "max"
                or cell.get("kind") != "параметр" or cell.get("qualifier") is not None
                or cell.get("note") != NOTES[name]
                or cell.get("condition") is not None or type(cell.get("value")) is not int
                or set(cell) != {"name", "kind", "qualifier", "value", "unit", "relation", "condition", "note"}):
            raise ValueError("unsupported duty parameter semantics")
        # No invented lower bound: this translates precisely the stated upper
        # bounds. Physical instances are tested separately below.
        decls.append(f"(declare-const {symbol} Int)")
        terms.append(f"(<= {symbol} {cell['value']})")
    quotes = "; ".join(
        f"{cell['name']} = {cell['value']}" + (" мин" if cell["unit"] == "duration_minutes" else "")
        for cell in sorted(cells, key=lambda cell: cell["name"]))
    if prefix != (PREFIX + "\nИсточник синтетического правила: стенд"
                  + "\nЦитаты параметров исходного мира: " + quotes):
        raise ValueError("unsupported duty statement grammar")
    return decls, "(and " + " ".join(terms) + ")"


def local_report(doc, raw):
    subjects = sv.subject_of(doc)
    if len(subjects) != 1 or subjects[0]["id"] != "R-STAND-duty_limits":
        raise ValueError("local proof covers only one declared duty_limits rule")
    subject = subjects[0]
    declarations, expression = translate_duty_statement(subject["statement"])
    calls = []
    def local_translation(prompt):
        assert subject["id"] + " | " + subject["statement"] in prompt
        calls.append(prompt)
        return json.dumps({"decls": declarations, "asserts": [
            {"rule": subject["id"], "smt": expression}]})
    report = sv.verify(doc, local_translation, model_sha256=sv.model_fingerprint(raw))
    assert len(calls) == 1
    assert report["translator"]["provider"] is None and report["translator"]["model"] is None
    assert report["n_rules"] == report["n_translated"] == 1
    assert report["verdict"] == "sat"
    assert report["subject_sha256"] == sv.subject_fingerprint(doc)
    return report


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    before = _hashes()
    original_bindings = {p: p.read_bytes() for p in (
        ROOT / "cdpm/bindings.json", ROOT / "vmp/bindings.json", ROOT / "vmp/bindings_seed.json")}
    folder = tmp_path / "draft3"
    shutil.copytree(CATALOG, folder)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    for key in ("CREW_BINDINGS_SEED", "COSMOS_TEST_CATALOG_GROWN", "CREW_SOLVER_URL",
                "CREW_CHANNEL_URL", "CREW_CHANNEL_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    cb.forget(); ca.forget(); tc.forget_current()
    cat = tc.current(folder, validated=True)
    assert cat.fingerprint() == FINGERPRINT
    paths = {}
    for product, builder in (("CDPM", cbb), ("COSMOS", vbb)):
        doc = builder.build(cat, commit=COMMIT, generated_at="2026-09-19T20:05:00Z", stand_results=None)
        assert all(not row["stand_verified"] for row in doc["bindings"])
        path = tmp_path / (product + "-unsigned-bindings.json")
        path.write_text(builder.dump(doc), encoding="utf-8")
        paths[product] = path
        monkeypatch.setenv(cb.SUPPORT_ENV[product], str(path))
    table = bindings.support_table(cat, paths, stand=None, approved_by=None,
                                   computed_at="2026-09-19T20:05:00Z")
    assert table["cells"] and all(c["approved"] is None and c["state"] != "есть" for c in table["cells"])
    (folder / "support.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("external SMT factory must not be called")
    monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
    yield cat
    cb.forget(); ca.forget(); tc.forget_current()
    assert _hashes() == before
    assert all(p.read_bytes() == raw for p, raw in original_bindings.items())


def prepare_world(cat, base, task_id):
    declared = next(t for t in cat.family("duty_limits")["stand_tasks"] if t["id"] == task_id)
    world = copy.deepcopy(declared["world"])
    meta = st._family_meta(cat)
    families = st.families_for(world, "duty_limits", meta)
    assert families == ["duty_limits"]  # no filtering of other HARD obligations
    rules = ss._rules_for(world, families, meta)
    doc = ss.model_doc(rules, world=world, family="duty_limits", catalog=cat)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    run = ss._write_run(base, doc, ss._sheets(world), source=ss._stand_source(world, rules, None))
    raw = (run / "extract/model.json").read_bytes()
    report = local_report(doc, raw)
    sv.write_report(run, report)
    return world, rules, doc, run, raw, report


@pytest.mark.parametrize("task_id", ["st-duty-1", "st-duty-3"])
@pytest.mark.parametrize("spec", ["1.20", "1.21"])
def test_real_declared_world_native_zip_uses_exact_local_z3_proof(prepared, tmp_path, task_id, spec):
    base = tmp_path / task_id
    world, rules, doc, run, raw, report = prepare_world(prepared, base, task_id)
    report_bytes = sv.report_path(run).read_bytes()
    body = ss.package_bytes(world, rules, stamp=ss.stamp_of(prepared), base=base,
                            catalog=prepared, family="duty_limits", spec_version=spec)
    assert (run / "extract/model.json").read_bytes() == raw
    assert sv.report_path(run).read_bytes() == report_bytes
    path = tmp_path / "native-unsigned.zip"
    path.write_bytes(body)
    assert gate.check(path)[0] == []
    received = receiver.load(path, run_gate=False)
    assert received["manifest"]["spec_version"] == spec
    assert received["manifest"]["consistency"]["verdict"] == "sat"
    assert not received["manifest"].get("stand_only")
    assert received["manifest"]["consistency"]["subject_sha256"] == report["subject_sha256"]
    assert all(c["approved"] is None for c in received["manifest"]["support"]["cells"])
    with pytest.raises(receiver.TaskPackageRefused) as refusal:
        receiver.check_support_execution(received, task="pairing", mode="feasibility-hard")
    assert refusal.value.code == "support-missing"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        exported_report = json.loads(archive.read("smt-report.json"))
    assert exported_report == report
    assert exported_report["model_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("name,limit", [("max_duty_duration", 780), ("max_landings_per_duty", 4), ("max_pairing_days", 7)])
def test_original_world_formula_obeys_each_threshold_and_detects_counterexample(prepared, tmp_path, name, limit):
    _, _, doc, _, _, report = prepare_world(prepared, tmp_path / "proof", "st-duty-1")
    statement = sv.subject_of(doc)[0]["statement"]
    decls, expression = translate_duty_statement(statement)
    symbol = FIELDS[name][0]
    for value, expected in ((limit - 1, z3.sat), (limit, z3.sat), (limit + 1, z3.unsat)):
        solver = z3.Solver()
        solver.add(z3.parse_smt2_string("\n".join(decls) + f"\n(assert {expression})"))
        solver.add(z3.Int(symbol) == value)
        assert solver.check() == expected


@pytest.mark.parametrize("damage", ["model", "subject"])
def test_stale_local_report_cannot_bypass_exact_input_binding(prepared, tmp_path, damage):
    _, _, doc, run, raw, report = prepare_world(prepared, tmp_path / "proof", "st-duty-1")
    report["model_sha256" if damage == "model" else "subject_sha256"] = "0" * 64
    sv.write_report(run, report)
    with pytest.raises(tp.TaskPackageError) as refusal:
        tp.consistency_of(run, raw)
    assert refusal.value.code == tp.SMT_UNAVAILABLE
    assert "external SMT factory must not be called" in str(refusal.value)


@pytest.mark.parametrize("damage", ["prose", "relation", "value", "condition", "unknown", "note", "missing-condition", "duplicate"])
def test_bounded_translator_refuses_unknown_or_changed_semantics(prepared, tmp_path, damage):
    _, _, doc, _, _, _ = prepare_world(prepared, tmp_path / "proof", "st-duty-1")
    statement = sv.subject_of(doc)[0]["statement"]
    prefix, _, raw = statement.partition(MARK)
    cells = json.loads(raw)
    if damage == "prose": prefix += "; дополнительное требование"
    elif damage == "relation": cells[0]["relation"] = "min"
    elif damage == "value": cells[0]["value"] = True
    elif damage == "condition": cells[0]["condition"] = {"base": "ST1"}
    elif damage == "note": cells[0]["note"] = "дополнительное условие"
    elif damage == "missing-condition": del cells[0]["condition"]
    elif damage == "duplicate": cells.append(copy.deepcopy(cells[0]))
    else: cells[0]["name"] = "unrecognized"
    with pytest.raises(ValueError):
        translate_duty_statement(prefix + MARK + json.dumps(cells, ensure_ascii=False))


@pytest.mark.parametrize("label", ["Описание", "Измерение", "Область", "Единица"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_bounded_translator_requires_each_exact_semantic_field(prepared, label, damage):
    task = next(t for t in prepared.family("duty_limits")["stand_tasks"] if t["id"] == "st-duty-1")
    world = copy.deepcopy(task["world"])
    rules = ss._rules_for(world, ["duty_limits"], st._family_meta(prepared))
    doc = ss.model_doc(rules, world=world, family="duty_limits", catalog=prepared)
    statement = sv.subject_of(doc)[0]["statement"]
    translate_duty_statement(statement)  # The unchanged exact subject is supported.
    lines = statement.splitlines()
    selected = [line for line in lines if line.startswith(label + ": ")]
    assert len(selected) == 1
    line = selected[0]
    if damage == "missing":
        lines.remove(line)
    else:
        lines[lines.index(line)] = line + "; иное условие"
    with pytest.raises(ValueError, match="unsupported duty statement grammar"):
        translate_duty_statement("\n".join(lines))
