"""Display-only confusable diagnostics on real consumer paths; synthetic data."""
import copy
from types import SimpleNamespace
import pytest
from unicode_data import confusable_hint, internal_skeleton, visually_confusable
from vmp import formulation_check as fc, conditions_export as ce, bindings_gate as bg
from cdpm import history_section as hs, catalog_bridge as cb

PAIRS = [("B210", "В210"), ("SVO", "SVО"), ("A320", "Α320"),
         ("CODE", "СОDЕ"), ("m", "rn"), ("𝐀320", "A320"),
         ("Å", "A\u030a"), ("SVO", "S\u200bVO")]


@pytest.mark.parametrize("left,right", PAIRS)
def test_full_unicode_pairs_are_diagnostic_not_identity(left, right):
    assert visually_confusable(left, right)
    assert left != right
    hint = confusable_hint(left, [right])
    assert "может выглядеть похоже" in hint and "U+" in hint
    assert "Код не заменён" in hint
    assert not confusable_hint(right, [right])


@pytest.mark.parametrize("prefix", ["Код не найден", "Код не найден.",
                                   "Код не найден (типы данных: В210)"])
def test_hint_has_a_visible_separator_without_relying_on_rendered_whitespace(prefix):
    message = prefix + confusable_hint("B210", ["В210"])
    assert message.startswith(prefix + " — Обозначение ")
    assert prefix + confusable_hint("В210", ["В210"]) == prefix
    assert prefix + confusable_hint("B999", ["В210"]) == prefix


@pytest.mark.parametrize("left,right", [
    ("B999", "В210"), ("B738", "B739"), ("A320", "Airbus A320"),
    ("A320", "A321"), ("A320,B738", "типы: A320 и B738"),
    ("😀", "😀\u0301"), (" B210", "В210"),
])
def test_unrelated_text_is_not_visually_confusable(left, right):
    assert not visually_confusable(left, right)
    assert not confusable_hint(left, [right])


def facts(known):
    return {"present": True, "airports": set(known), "bases": set(known),
            "aircraft_types": set(known), "has_type_column": True}


@pytest.mark.parametrize("path", ["definition-base", "definition-type", "rule-type", "qualifier-base"])
@pytest.mark.parametrize("value,known,warn", [
    ("B210", "В210", True), ("В210", "В210", False),
    ("A320", "A320", False), ("B999", "В210", False),
])
def test_formulation_missing_codes_keep_outcome_and_add_hint(path, value, known, warn):
    if path == "qualifier-base":
        value, known = (("SVO", "SVО") if warn else ("SVO", "SVO"))
    form, model = {}, {"rules": []}
    if path.startswith("definition"):
        form = {"common": {"definitions": [{"id": "D", "title":
            "Базы" if path.endswith("base") else "Типы судов",
            "machine": {"codes": [value]}}]}}
    else:
        params = ([{"name": "fleet_types", "value": value}] if path == "rule-type"
                  else [{"name": "limit", "value": 720, "qualifier": "база " + value}])
        model["rules"] = [{"id": "R", "kind": "HARD", "params": params}]
    before = copy.deepcopy((form, model))
    hits = fc.check_codes(form, model, facts([known]))
    assert bool(hits) == (value != known)
    assert ("может выглядеть похоже" in str(hits)) == warn
    if hits:
        assert hits[0]["level"] == fc.STOP
        assert hits[0]["codes"] == [value]
    if warn:
        assert " — Обозначение " in hits[0]["message"]
    assert (form, model) == before


def dataset(value, known, station=None):
    row = {"Табельный номер": value, "Начало": "2026-09-01 00:00",
           "Конец": "2026-09-01 01:00", "Станция": station or "SVO", "Вид": "факт"}
    return {"tables": {"S": {
        "Экипажи": {"columns": ["Табельный номер"],
                   "rows": [{"Табельный номер": known}]},
        "Проверка": {"columns": list(row), "rows": [row]},
        "Рейсы": {"columns": ["Вылет", "Прилёт"],
                  "rows": [{"Вылет": "SVO", "Прилёт": "LED"}]},
    }}}


@pytest.mark.parametrize("template", ["история-журнал-табельный",
    "история-присутствие-табельный", "история-табельный"])
@pytest.mark.parametrize("value,known,warn", [
    ("B210", "В210", True), ("В210", "В210", False), ("B999", "В210", False),
])
def test_all_employee_id_readers_explain_without_repair(template, value, known, warn):
    data = dataset(value, known)
    before = copy.deepcopy(data)
    count, errors = hs.run_check(template, {"sheet": "S/Проверка"}, data)
    assert count == 1 and bool(errors) == (value != known)
    assert ("может выглядеть похоже" in str(errors)) == warn
    assert data == before


@pytest.mark.parametrize("value,warn", [("SVО", True), ("SVO", False), ("XXX", False)])
def test_presence_station_explanation(value, warn, monkeypatch):
    # Controlled network boundary; the actual parser and checker run unchanged.
    monkeypatch.setattr(hs, "network_stations", lambda tables: {"SVO", "LED"})
    data = dataset("B210", "B210", value)
    before = copy.deepcopy(data)
    count, errors = hs.run_check("история-присутствие-станция", {"sheet": "S/Проверка"}, data)
    assert count == 1 and bool(errors) == (value != "SVO")
    assert ("может выглядеть похоже" in str(errors)) == warn
    assert data == before


@pytest.mark.parametrize("value,known,warn", [
    ("Α320", "A320", True), ("A320", "A320", False), ("B999", "A320", False),
])
def test_export_unknown_keys_are_not_newly_normalized(value, known, warn):
    original = {value: {"limit": 720}}
    before = copy.deepcopy(original)
    result, notes = ce._canon_fleet_keys(original, [known])
    assert result == original and original == before
    assert ("может выглядеть похоже" in str(notes)) == warn


def test_export_preserves_existing_cyrillic_normalization():
    result, notes = ce._canon_fleet_keys({"B210": 720}, ["В210"])
    assert result == {"В210": 720} and "канонизирован" in str(notes)


@pytest.mark.parametrize("value,known,warn", [
    ("CODE", "СОDЕ", True), ("CODE", "CODE", False), ("UNKNOWN", "CODE", False),
])
def test_constraint_literal_diagnostic_keeps_the_binding_gate(value, known, warn, monkeypatch):
    monkeypatch.setattr(bg, "source", lambda file: 'fun test() = asConstraint("' + value + '")')
    monkeypatch.setattr(bg, "tables", lambda: SimpleNamespace(constraint_literals=lambda: {known}))
    errors = bg.check_kotlin_constraint("Provider.kt", "test", value)
    assert bool(errors) == (value != known)
    assert ("может выглядеть похоже" in str(errors)) == warn


@pytest.mark.parametrize("value,known,warn", [
    ("CODE", "СОDЕ", True), ("UNKNOWN", "CODE", False),
])
def test_unknown_family_keeps_unsupported(value, known, warn, monkeypatch):
    monkeypatch.setattr(cb, "family_records", lambda: {known: {"id": known}})
    monkeypatch.setattr(cb, "support_table", lambda: {
        "files": {p: {} for p in cb.SUPPORT_PRODUCTS}, "words": []})
    result = cb.family_support(value)
    assert result["supported"] is False
    assert result["evidence"]["family"] == value
    assert ("может выглядеть похоже" in result["evidence"]["words"]) == warn

def test_all_unicode_mapping_and_ignorable_vectors_match_real_typescript():
    import json
    from pathlib import Path
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "unicode_data/confusables.json").read_text(encoding="utf-8"))
    values = [chr(int(cp)) for cp in data["mappings"]] + list(data["mappings"].values())
    for start, end in data["defaultIgnorableRanges"]:
        values.extend("A" + chr(cp) + "320" for cp in (start - 1, start, end, end + 1)
                      if 0 <= cp <= 0x10ffff)
    values.extend(s for pair in PAIRS for s in pair)
    node = shutil.which("node")
    assert node, "Node is required; Unicode parity must not be skipped"
    check = subprocess.run([node, "unicode_data/generate.mjs", "--check"],
        cwd=root, text=True, capture_output=True, encoding="utf-8", timeout=30)
    assert check.returncode == 0, check.stdout + check.stderr
    code = """
import { readFileSync } from 'node:fs';
import { internalSkeleton } from './cdpm-ui/src/unicodeConfusables.ts';
const values = JSON.parse(readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(values.map(internalSkeleton)));
"""
    result = subprocess.run([node, "--input-type=module", "-e", code],
        cwd=root, input=json.dumps(values, ensure_ascii=True), text=True,
        capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [internal_skeleton(v) for v in values]
    assert data["mappingCount"] == len(data["mappings"]) == 6565


def test_dataset_covers_the_pinned_source_without_handpicked_omissions():
    import hashlib
    import json
    from pathlib import Path
    folder = Path(__file__).resolve().parents[1] / "unicode_data"
    data = json.loads((folder / "confusables.json").read_text(encoding="utf-8"))
    for name, expected in data["sources"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest().upper() == expected
    expected_mapping = {}
    for line in (folder / "confusables-17.0.0.txt").read_text(encoding="utf-8").splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        source, target, status = (v.strip() for v in line.split(";"))
        assert status == "MA"
        expected_mapping[str(int(source, 16))] = "".join(chr(int(c, 16)) for c in target.split())
    assert data["mappings"] == expected_mapping
    expected_ignorables = set()
    for line in (folder / "DerivedCoreProperties-17.0.0.txt").read_text(encoding="utf-8").splitlines():
        fields = [v.strip() for v in line.partition("#")[0].split(";")]
        if len(fields) < 2 or fields[1] != "Default_Ignorable_Code_Point":
            continue
        bounds = [int(v, 16) for v in fields[0].split("..")]
        expected_ignorables.update(range(bounds[0], bounds[-1] + 1))
    actual_ignorables = {cp for start, end in data["defaultIgnorableRanges"]
                         for cp in range(start, end + 1)}
    assert actual_ignorables == expected_ignorables
    for cp in expected_ignorables:
        assert internal_skeleton("A" + chr(cp) + "320") == internal_skeleton("A320")
