"""Post-ZIP publication interruptions, with actual delivery consumers.

The old sent:true mark is explicit historical fixture data bound to a real
old ZIP, not a simulated successful current send. The current handler uses
the real exporter/gate and disabled channel. No HTTP, service or executor.
Historical marks below are explicit fixture data, not a current send verdict.
"""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from cdpm import file_replace, run_record, webui
from test_cdpm_package_publish_agent import publication  # noqa: F401
from test_k01_semantic_transition import clean_bridge  # noqa: F401


@pytest.mark.parametrize("failure", ["partial", "replace", "cleanup", "refresh"])
def test_delivery_write_interruption_preserves_evidence_without_relabelling_zip(
        publication, monkeypatch, failure):
    state = publication
    run = state.out.parent
    receipt = state.out / "delivery.json"
    old_sha = hashlib.sha256(state.before["task-package.zip"]).hexdigest()
    old_mark = json.loads(state.before["delivery.json"])
    old_mark.update(sent=True, attempts=1, verdict="OK",
                    detail="Synthetic historical receipt for the previous ZIP",
                    mismatch=[], already_accepted=False,
                    mg={"pairing": {"state": "feasible_soft"}},
                    cosmos_run="previous-package-receiver-run")
    assert old_mark["sha"] == old_sha
    old_bytes = json.dumps(old_mark, ensure_ascii=False).encode("utf-8")
    receipt.write_bytes(old_bytes)
    expected_old_delivery = {"kind": "task-package",
        "path": "channel/task-package.zip", "sha256": old_sha}
    assert webui._delivery_mark(run) == old_mark
    assert run_record.refresh(run)["results"]["deliveries"] == [expected_old_delivery]
    assert run_record.on_disk(run)["results"]["deliveries"] == [expected_old_delivery]
    channel_endpoint = next(route.endpoint for route in webui.app.routes
                            if getattr(route, "path", "") == "/api/runs/{run_id}/channel")
    assert channel_endpoint is webui.channel_delivery
    assert channel_endpoint(run.name)["sent"] is True

    real_write = Path.write_text
    real_replace = file_replace.replace_file
    real_unlink = Path.unlink
    real_record_write = run_record._write_json
    writes = []
    cleanup_attempts = []
    refresh_attempts = []
    primary = OSError("interrupted final delivery mark write")

    def interrupt_receipt(path, text, *args, **kwargs):
        if path.parent == state.out and "delivery" in path.name:
            # The new ZIP has already passed the real gate and been replaced;
            # only its final delivery mark publication is interrupted.
            body = state.package.read_bytes()
            assert body != state.before["task-package.zip"]
            assert len(state.calls) == 1 and state.calls[0][0] == body
            assert state.checks and state.checks[-1][1] == []
            assert receipt.read_bytes() == old_bytes
            writes.append(path)
            if failure == "replace":
                return real_write(path, text, *args, **kwargs)
            real_write(path, text[:17], *args, **kwargs)
            raise primary
        return real_write(path, text, *args, **kwargs)

    def interrupt_replace(source, destination):
        if Path(destination) == receipt:
            assert json.loads(Path(source).read_text(encoding="utf-8"))["sha"] == \
                hashlib.sha256(state.package.read_bytes()).hexdigest()
            raise PermissionError(13, "interrupted final delivery mark replace")
        return real_replace(source, destination)

    def interrupt_cleanup(path, *args, **kwargs):
        if path.parent == state.out and "delivery" in path.name and path != receipt:
            cleanup_attempts.append(path)
            raise PermissionError("secondary delivery cleanup error")
        return real_unlink(path, *args, **kwargs)

    def interrupt_refresh(path, value):
        if path == run / run_record.RECORD_FILE:
            refresh_attempts.append(path)
            raise OSError("secondary run-record refresh error")
        return real_record_write(path, value)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "write_text", interrupt_receipt)
        if failure == "replace":
            fault.setattr(file_replace, "replace_file", interrupt_replace)
            fault.setattr(file_replace, "REPLACE_WINDOW_S", 0)
        if failure == "cleanup":
            fault.setattr(Path, "unlink", interrupt_cleanup)
        if failure == "refresh":
            fault.setattr(run_record, "_write_json", interrupt_refresh)
        with pytest.raises((OSError, HTTPException)) as error:
            state.invoke()
    if failure == "replace":
        assert "interrupted final delivery mark replace" in str(error.value)
    else:
        assert error.value is primary, "cleanup/refresh must not hide the primary error"
    assert len(writes) == 1
    current_sha = hashlib.sha256(state.package.read_bytes()).hexdigest()
    assert current_sha != old_sha
    broken_bytes = receipt.read_bytes()
    observed = {
        "old_sha": old_sha, "current_sha": current_sha,
        "receipt_bytes_preserved": broken_bytes == old_bytes,
        "mark_after_interruption": webui._delivery_mark(run),
        "channel_after_interruption": webui.channel_delivery(run.name),
        "persisted_deliveries": run_record.on_disk(run)["results"]["deliveries"],
        "rebuilt_deliveries": run_record.build(run)["results"]["deliveries"],
    }

    # The unchanged old receipt must no longer describe the current ZIP.
    # These are actual readers, not mocked mark/state/verdict projections.
    mark = webui._delivery_mark(run)
    rebuilt_result = run_record.build(run)["results"]
    rebuilt = rebuilt_result["deliveries"]
    print(json.dumps(observed, ensure_ascii=False, sort_keys=True))
    assert state.source.read_bytes() == state.source_before
    violations = []
    if not observed["receipt_bytes_preserved"]:
        violations.append("interruption destroyed the previous receipt bytes")
    if mark.get("sent") and mark.get("sha") != current_sha:
        violations.append("current delivery consumer confirms a different ZIP")
    if any(row.get("path") == "channel/task-package.zip"
           and row.get("sha256") != current_sha for row in rebuilt):
        violations.append("run_record binds an old delivery SHA to the current ZIP path")
    assert not violations, {"violations": violations, "observed": observed}
    assert mark["sent"] is False
    assert "текущая доставка не подтверждена" in mark["detail"]
    assert "квитанция от другого ZIP" in mark["detail"]
    assert "доставок по каналу ещё не было" not in mark["detail"]
    assert not {"sha", "verdict", "mg", "cosmos_run"} & mark.keys()
    assert channel_endpoint(run.name) == {"run": run.name, "configured": False, **mark}
    assert rebuilt == []
    for key in ("package", "readiness_matrix", "cosmos_run"):
        assert rebuilt_result["verdicts"][key] is None
    if failure == "refresh":
        # Both writes were denied: persisted derived data cannot be repaired
        # then. A new build is honest; retry below repairs the disk record.
        assert refresh_attempts == [run / run_record.RECORD_FILE]
        assert observed["persisted_deliveries"] == [expected_old_delivery]
    else:
        assert observed["persisted_deliveries"] == []
        persisted = run_record.on_disk(run)["results"]["verdicts"]
        assert all(persisted[key] is None for key in
                   ("package", "readiness_matrix", "cosmos_run"))
    assert receipt.read_bytes() == old_bytes, "readers must preserve historical bytes"
    leftovers = list(state.out.glob(".delivery-*.json"))
    if failure == "cleanup":
        assert cleanup_attempts == writes and leftovers == writes
        abandoned_bytes = leftovers[0].read_bytes()
    else:
        assert leftovers == []

    # Actual disabled send again: it publishes complete current evidence,
    # never treats the abandoned stage as authoritative and never invents OK.
    retried = state.invoke()
    assert len(state.calls) == 2
    assert retried["sent"] is False and retried["attempts"] == 0
    assert retried["sha"] == hashlib.sha256(state.package.read_bytes()).hexdigest()
    assert json.loads(receipt.read_text(encoding="utf-8")) == retried
    assert webui._delivery_mark(run) == retried
    assert channel_endpoint(run.name)["detail"] == retried["detail"]
    assert run_record.on_disk(run)["results"]["deliveries"] == []
    assert run_record.on_disk(run)["results"]["verdicts"]["cosmos_run"] is None
    assert list(state.out.glob(".delivery-*.json")) == leftovers
    if leftovers:
        assert leftovers[0].read_bytes() == abandoned_bytes
    assert state.source.read_bytes() == state.source_before


def test_delivery_mark_transient_replace_uses_real_retry(publication, monkeypatch):
    """The existing file-replacement policy also applies to the final mark."""
    state = publication
    receipt = state.out / "delivery.json"
    replace = file_replace.replace_file
    attempts = []

    def temporarily_busy(source, destination):
        if Path(destination) == receipt:
            attempts.append(Path(source))
            assert receipt.read_bytes() == state.before["delivery.json"]
            if len(attempts) == 1:
                raise PermissionError(13, "temporary delivery reader")
        return replace(source, destination)

    monkeypatch.setattr(file_replace, "replace_file", temporarily_busy)
    mark = state.invoke()
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert mark["sent"] is False and mark["attempts"] == 0
    assert mark["sha"] == hashlib.sha256(state.package.read_bytes()).hexdigest()
    assert webui._delivery_mark(state.out.parent) == mark
    assert json.loads(receipt.read_text(encoding="utf-8")) == mark
    assert list(state.out.glob(".delivery-*.json")) == []
    assert len(state.calls) == 1 and state.checks[-1][1] == []
    assert state.source.read_bytes() == state.source_before
