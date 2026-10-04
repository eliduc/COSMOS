"""Actual catalogue/bindings readers across configured path changes.

Identity-only temporary copies of the real shipped bindings: no stand, solver,
HTTP or product-verdict replacement. B04/B05 remain the product gate's scope,
as documented by catalog_access; this door suite does not claim acceptance.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

import template_catalog as tc
from template_catalog import bindings as tcb
from vmp import catalog_access as ca
from vmp import template_registry as registry


ROOT = Path(__file__).resolve().parents[1]
ENV = {"COSMOS": "CREW_COSMOS_BINDINGS", "CDPM": "CDPM_BINDINGS_FILE"}
FILES = {"COSMOS": ROOT / "vmp/bindings.json", "CDPM": ROOT / "cdpm/bindings.json"}
FIXED_NS = 1_700_000_000_123_456_700


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return path


def _read(product, cat):
    return ca.bindings(cat) if product == "COSMOS" else ca.cdpm_bindings(cat)


@pytest.fixture
def doors(tmp_path, monkeypatch):
    monkeypatch.setenv(ca.CATALOG_ENV, str(ROOT / "catalog"))
    for variable in ENV.values():
        monkeypatch.delenv(variable, raising=False)
    ca.forget()
    registry._CACHE = None
    cat = ca.current()
    docs, defaults = {}, {}
    for product, source in FILES.items():
        document = copy.deepcopy(tcb.load_bindings(source))
        assert document["catalog_version"] == cat.version()
        assert document["catalog_fingerprint"] == cat.fingerprint()
        document["generated_from"] = "a" * 40
        problems = tcb.check_bindings(cat, document)
        assert not [p for p in problems if p.code == "S06" or (
            p.severity == "error" and p.code not in ca.GATE_ONLY_CODES
        )], problems
        docs[product] = document
        defaults[product] = _write(tmp_path / "default" / product / "bindings.json", document)
    monkeypatch.setattr(ca, "BINDINGS_FILE", defaults["COSMOS"])
    monkeypatch.setattr(ca, "CDPM_BINDINGS_FILE", defaults["CDPM"])
    yield cat, docs, defaults
    ca.forget()
    registry._CACHE = None


def _same_stat_pair(tmp_path, cat, document):
    a = _write(tmp_path / "left" / "bindings.json", document)
    changed = copy.deepcopy(document)
    changed["generated_from"] = "b" * 40
    b = _write(tmp_path / "right" / "bindings.json", changed)
    for path in (a, b):
        os.utime(path, ns=(FIXED_NS, FIXED_NS))
        reread = tcb.load_bindings(path)
        assert not [p for p in tcb.check_bindings(cat, reread)
                    if p.severity == "error" and p.code not in ca.GATE_ONLY_CODES]
    assert a.name == b.name and a != b
    assert (a.stat().st_size, a.stat().st_mtime_ns) == (b.stat().st_size, b.stat().st_mtime_ns)
    return a, b


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
def test_equal_stat_configured_paths_select_the_actual_second_document(doors, tmp_path, monkeypatch, product):
    cat, docs, _ = doors
    a, b = _same_stat_pair(tmp_path, cat, docs[product])
    monkeypatch.setenv(ENV[product], str(a))
    first = _read(product, cat)
    assert first["generated_from"] == "a" * 40
    monkeypatch.setenv(ENV[product], str(b))
    second = _read(product, cat)
    assert second["generated_from"] == "b" * 40, (
        f"{ENV[product]} switched to {b}, but reader retained {second['generated_from']}"
    )
    assert second is not first


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
def test_equal_stat_path_switch_invalidates_actual_registry_projection(doors, tmp_path, monkeypatch, product):
    cat, docs, _ = doors
    a, b = _same_stat_pair(tmp_path, cat, docs[product])
    monkeypatch.setenv(ENV[product], str(a))
    stamp_a = ca.transition_stamp(cat)
    first = registry.RULE_MECHANIC_TYPES
    assert first["duty_limits"]["executors"], "healthy real family projection required"
    monkeypatch.setenv(ENV[product], str(b))
    second = registry.RULE_MECHANIC_TYPES
    assert second is not first, "projection cache must observe the selected absolute path"
    stamp_b = ca.transition_stamp(cat)
    index = 1 if product == "COSMOS" else 2
    assert stamp_a[index] != stamp_b[index]
    assert all(stamp_a[i] == stamp_b[i] for i in range(3) if i != index)


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
def test_missing_paths_retain_distinct_projection_identity(doors, tmp_path, monkeypatch, product):
    cat, _, _ = doors
    a, b = tmp_path / "absent-left/bindings.json", tmp_path / "absent-right/bindings.json"
    monkeypatch.setenv(ENV[product], str(a))
    stamp_a = ca.transition_stamp(cat)
    monkeypatch.setenv(ENV[product], str(b))
    stamp_b = ca.transition_stamp(cat)
    index = 1 if product == "COSMOS" else 2
    assert stamp_a[index][1:] == stamp_b[index][1:] == (None, None)
    assert stamp_a[index] != stamp_b[index], "missing files still have distinct configured identities"


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_bad_override_never_falls_back_to_healthy_default(doors, tmp_path, monkeypatch, product, damage):
    cat, _, defaults = doors
    assert _read(product, cat) is not None, "default must be a healthy control"
    path = tmp_path / "override/bindings.json"
    if damage == "corrupt":
        path.parent.mkdir()
        path.write_bytes(b'{"product":')
    reads = []
    real_load = tcb.load_bindings

    def counting(candidate):
        reads.append(Path(candidate))
        return real_load(candidate)

    monkeypatch.setattr(tcb, "load_bindings", counting)
    monkeypatch.setenv(ENV[product], str(path))
    if product == "COSMOS":
        with pytest.raises(tc.CatalogError) as error:
            ca.bindings(cat)
        assert str(path) in str(error.value)
    else:
        document, word, reason = ca.cdpm_bindings_state(cat)
        assert document is None, "invalid/missing override must not expose the default document"
        assert word == (ca.CDPM_NO if damage == "missing" else ca.CDPM_BAD)
        assert (reason is None) if damage == "missing" else bool(reason)
    assert defaults[product] not in reads
    assert reads == ([] if damage == "missing" else [path])


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
@pytest.mark.parametrize("setting", [None, "", " \t "])
def test_unset_empty_whitespace_use_default(doors, monkeypatch, product, setting):
    cat, docs, defaults = doors
    if setting is None:
        monkeypatch.delenv(ENV[product], raising=False)
    else:
        monkeypatch.setenv(ENV[product], setting)
    assert _read(product, cat) == docs[product]
    index = 1 if product == "COSMOS" else 2
    assert ca.transition_stamp(cat)[index] == ca._stamp_of(defaults[product])


@pytest.mark.parametrize("product", ["COSMOS", "CDPM"])
def test_healthy_cache_reads_once_and_normal_file_change_is_seen(doors, monkeypatch, product):
    cat, docs, defaults = doors
    path = defaults[product]
    monkeypatch.setenv(ENV[product], str(path))
    reads = []
    real_load = tcb.load_bindings

    def counting(candidate):
        reads.append(Path(candidate))
        return real_load(candidate)

    monkeypatch.setattr(tcb, "load_bindings", counting)
    first = _read(product, cat)
    for _ in range(4):
        assert _read(product, cat) is first
    assert reads == [path]
    before = path.stat().st_mtime_ns
    changed = copy.deepcopy(docs[product])
    changed["generated_from"] = "c" * 40
    _write(path, changed)
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))
    assert _read(product, cat)["generated_from"] == "c" * 40
    assert reads == [path, path]


def test_module_body_does_not_read_environment_or_files_at_import(monkeypatch):
    source = ROOT / "vmp/catalog_access.py"
    code = compile(source.read_bytes(), str(source), "exec")
    probe = ModuleType("catalog_access_import_probe")
    probe.__file__ = str(source)

    class NoEnv(dict):
        def get(self, *args):
            raise AssertionError(f"environment read at import: {args}")

        def __getitem__(self, key):
            raise AssertionError(f"environment read at import: {key}")

        def __contains__(self, key):
            raise AssertionError(f"environment read at import: {key}")

    def no_file(*args, **kwargs):
        raise AssertionError("file read at module import")

    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", NoEnv())
        patch.setattr(Path, "open", no_file)
        # Dependencies are already imported: only this real module body is
        # judged here; the historical fresh-process check is not run offline.
        exec(code, probe.__dict__)
    assert callable(probe.bindings) and callable(probe.transition_stamp)
