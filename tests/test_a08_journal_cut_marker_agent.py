"""A08-JOURNAL-CUT-UNMARKED: a marked preview is not the full refusal.

The long case uses the real producer and receiver on isolated package bytes;
only the temporary transport copy is corrupted after the producer check.
The small boundary controls stop at the explicit service-health seam. They
test journal formatting, not real service availability or executor readiness.
No judge, journal writer, network, solver, or process is substituted/run.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from cdpm.gates import gate_task_package as producer
from test_a08_journal_boundaries_agent import (
    TAIL_RULE,
    _add_long_mismatch,
    _package,
    _press_until_mode,
    _rewrite_objective,
    _saved,
    isolated_catalog_queue,  # noqa: F401 (fixture)
)
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from vmp.run import button
from webapp import task_package as receiver


def test_real_receiver_long_refusal_marks_only_the_persisted_preview(
        tmp_path, monkeypatch):
    path = _package(tmp_path, monkeypatch, "1.20")
    assert producer.check(path)[0] == []
    assert receiver.load(path, run_gate=False)["objective_projection"]["judged"] is True
    original = path.read_bytes()
    refusals = []

    def corrupt_transport(source):
        source = Path(source)
        assert source == tmp_path / "refused" / "package.zip"
        assert source.read_bytes() == original
        source.write_bytes(_rewrite_objective(original, _add_long_mismatch))
        try:
            return receiver.load(source, run_gate=False)
        except receiver.TaskPackageRefused as exc:
            refusals.append(exc)
            raise

    result, callbacks, converted = _press_until_mode(
        tmp_path, path, out_dir="refused", load_pkg=corrupt_transport)
    journal = _saved(tmp_path, "refused", result, callbacks)
    assert path.read_bytes() == original
    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal.code == result["code"] == "objective-mismatch"
    assert result["verdict"] == "остановка: пакет постановки не принят"
    assert result["detail"] == str(refusal)
    assert result["problems"] == refusal.problems
    assert len(result["detail"]) == 637  # The reported real receiver case.
    assert len(result["problems"]) == 5
    assert TAIL_RULE in result["detail"]
    assert any(p["where"] == "pairing.components[" + TAIL_RULE + "]"
               for p in result["problems"])
    for step in ("Ш-0", "Ш-1"):
        assert any(row["step"] == step and row["status"] == "пройден"
                   for row in journal)
    assert not converted
    assert not any(row["step"] in {"Ш-1ц", "Ш-1к"} for row in journal)
    assert journal[-1]["step"] == "остановка"
    preview = journal[-1]["detail"]
    assert len(preview) == 300
    assert TAIL_RULE not in preview
    assert preview == result["detail"][:299] + "…"


@pytest.mark.parametrize("length", [54, 96, 172, 299, 300, 301])
def test_stop_preview_preserves_short_text_and_marks_only_actual_cut(tmp_path, length):
    # The health seam supplies a detail of known character length; the actual
    # orchestrator catches ButtonStop and writes both JSON artifacts itself.
    detail = ("Причина отказа / ABC; " * 20)[:length]
    assert len(detail) == length
    service = SimpleNamespace(health=lambda: False, base=detail)
    callbacks = []
    result = button.orchestrate(
        tmp_path, service, out_dir="boundary",
        log=lambda *entry: callbacks.append(entry))
    journal = _saved(tmp_path, "boundary", result, callbacks)
    assert result["verdict"] == "остановка: решатель недоступен"
    assert result["detail"] == detail
    assert len(journal) == 1
    assert journal[0]["step"] == "остановка"
    preview = journal[0]["detail"]
    assert len(preview) <= 300
    if length <= 300:
        assert preview == detail
        assert not preview.endswith("…")
    else:
        assert preview == detail[:299] + "…"
