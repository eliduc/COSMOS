"""Small A08 preview group, not execution/readiness acceptance.

The AST census checks wiring/completeness only. Separate runtime tests exercise
the helper and real package -> receiver -> manifest_check -> persisted journal.
The latter injects catalog unavailability at product_stamp after real package
acceptance, then stops at the public progress callback before registry/executor.
No positive judge verdict, journal writer, HTTP, solver or process is mocked/run.
"""
import ast
from collections import Counter
import copy
import inspect

import pytest

from cdpm.gates import gate_certify, gate_task_package as producer
from test_a08_journal_boundaries_agent import (
    NoExecution,
    _package,
    _saved,
    isolated_catalog_queue,  # noqa: F401 (fixture)
)
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from vmp import catalog_access
from vmp.run import button, roster_certify
from webapp import task_package as receiver


@pytest.mark.parametrize("limit", [200, 300, 400])
def test_preview_helper_runtime_boundaries_without_changing_source(limit):
    for length in (0, 54, limit - 1, limit, limit + 1, 637):
        source = ("Причина / detail; " * 50)[:length]
        original = source
        actual = button._journal_preview(source, limit)
        assert source == original
        assert len(actual) <= limit
        if length <= limit:
            assert actual == original
            assert not actual.endswith("…")
        else:
            assert actual == original[:limit - 1] + "…"


def test_static_census_preserves_eight_sources_and_limits_plus_existing_stop():
    """Structural coverage is deliberately not claimed as eight live paths."""
    tree = ast.parse(inspect.getsource(button.orchestrate)
                     + inspect.getsource(button._certificate_result))
    actual = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id in {"_log", "log"} and len(call.args) >= 3):
            continue
        for expression in ast.walk(call.args[2]):
            if (isinstance(expression, ast.Call)
                    and isinstance(expression.func, ast.Name)
                    and expression.func.id == "_journal_preview"):
                actual.append((call.args[0].value,
                               ast.unparse(expression.args[0]),
                               ast.literal_eval(expression.args[1])))
    assert Counter(actual) == Counter([
        ("Ш-1кш", "str(e)", 200),
        ("Ш-2с", "_assume['history']['note']", 300),
        ("Ш-2с", "str(e)", 300),
        ("Ш-4", "str(v.get('note') or '')", 200),
        ("Ш-3р", "str(e)", 200),
        ("Ш-4п", "str(e)", 200),
        ("Ш-4", "str(v.get('note') or '')", 200),
        ("остановка", "e.detail", 300),
        ("Ш-4г", "'; '.join(problems)", 400),
    ])


def test_real_certificate_gate_keeps_full_problems_but_marks_callback_preview():
    # Deliberately incomplete certificate: the real gate must discover each
    # lost HARD rule. This does not fabricate a positive certification result.
    model = {"rules": [{"id": f"R-PREVIEW-{n}", "kind": "HARD"}
                       for n in range(6)]}
    report = {"checks": [], "findings": [], "certificate": {
        "certified": False, "checks_run": 0, "checks_not_run": 0,
        "findings": 0, "violated_rules": [], "rules_considered": 0}}
    before = copy.deepcopy((model, report))
    expected, _ = gate_certify.check(model, roster_certify.feasibility_view(report))
    complete = "; ".join(expected)
    assert len(complete) > 400
    for rule in model["rules"]:
        assert any(rule["id"] in problem for problem in expected)
    callbacks = []
    result, problems = button._certificate_result(
        model, report, optimality="not_proven", search_incomplete=False,
        log=lambda *entry: callbacks.append(entry))
    assert (model, report) == before
    assert problems == expected
    assert result["feasibility"] == "not_established"
    assert callbacks == [("Ш-4г", "гейт сертификата КРАСНЫЙ", complete[:399] + "…")]


@pytest.mark.parametrize("length", [199, 200, 201])
def test_real_catalog_unavailability_preview_reaches_disk_and_callback(
        tmp_path, monkeypatch, length):
    path = _package(tmp_path, monkeypatch, "1.20")
    assert producer.check(path)[0] == []
    assert receiver.load(path, run_gate=False)["objective_projection"]["judged"] is True
    package_bytes = path.read_bytes()
    detail = ("Не удалось прочитать каталог / " * 10)[:length]
    assert len(detail) == length
    unavailable = catalog_access.CatalogUnavailable(detail)
    stamp_calls, callbacks, loaded = [], [], []
    service = NoExecution()

    def unavailable_stamp():
        stamp_calls.append(True)
        raise unavailable

    def load(source):
        package = receiver.load(source, run_gate=False)
        loaded.append(package)
        return package

    def progress(step, status, words):
        callbacks.append((step, status, words))
        if step == "Ш-1к":
            # Real package judges have already completed. The actual
            # manifest_check now encounters an unavailable product source.
            monkeypatch.setattr(catalog_access, "product_stamp", unavailable_stamp)
        if step == "Ш-1кш":
            raise button.ButtonStop("offline stop after catalog warning",
                                    "No registry or executor reached")

    result = button.orchestrate(
        tmp_path, service, task="pairing", mode="feasibility-hard",
        package_path=path, out_dir="catalog-warning",
        coverage_fn=lambda *_: None, load_pkg=load,
        to_payload=lambda _package: {}, log=progress)
    journal = _saved(tmp_path, "catalog-warning", result, callbacks)
    assert service.health_calls == 1
    assert stamp_calls == [True]
    assert len(loaded) == 1
    assert loaded[0]["objective_projection"]["judged"] is True
    assert path.read_bytes() == package_bytes
    assert str(unavailable) == detail  # The source exception stays complete.
    assert result["verdict"] == "offline stop after catalog warning"
    assert result["detail"] == "No registry or executor reached"
    assert not (tmp_path / "registry").exists()
    for step in ("Ш-0", "Ш-1", "Ш-1ц"):
        assert any(row["step"] == step and row["status"] == "пройден"
                   for row in journal)
    row = next(row for row in journal if row["step"] == "Ш-1кш")
    assert row["status"] == "пропущен"
    expected = detail if length <= 200 else detail[:199] + "…"
    assert row["detail"] == "каталог шаблонов недоступен: " + expected
