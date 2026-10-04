"""Local artifact evidence reader: uncertainty is not an empty delivery history.

These are explicitly synthetic stored marks and a minimal ZIP, not evidence
of an operational delivery or package-gate acceptance. No file is rewritten
by the reader. Full sender/receiver integration is tested separately.
"""
import hashlib
import io
import json
import zipfile

import pytest

from cdpm import delivery_mark, file_replace


@pytest.fixture
def evidence(tmp_path):
    run = tmp_path / "run-0042"
    directory = run / "channel"
    directory.mkdir(parents=True)
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("fixture.txt", "artifact identity only")
    package = directory / "task-package.zip"
    package.write_bytes(body.getvalue())
    mark = {"sent": True, "sha": hashlib.sha256(body.getvalue()).hexdigest(),
            "verdict": "OK", "mg": {"pairing": {"state": "stop"}},
            "mismatch": [], "already_accepted": False,
            "cosmos_run": "historical-fixture-run", "detail": "synthetic history"}
    receipt = directory / "delivery.json"
    receipt.write_text(json.dumps(mark), encoding="utf-8")
    return run, package, receipt, mark


@pytest.mark.parametrize("sent", [True, False])
def test_matching_artifact_retains_original_mark_without_inventing_success(evidence, sent):
    run, package, receipt, mark = evidence
    mark["sent"] = sent
    receipt.write_text(json.dumps(mark), encoding="utf-8")
    before = package.read_bytes(), receipt.read_bytes()
    assert delivery_mark.read_current(run) == mark
    assert (package.read_bytes(), receipt.read_bytes()) == before


@pytest.mark.parametrize("damage", [
    "absent-receipt", "invalid-json", "invalid-utf8", "non-object", "numeric-sent",
    "missing-sent", "foreign-sha", "absent-package", "unreadable-package",
])
def test_unusable_evidence_is_explicitly_unconfirmed_and_keeps_raw_files(
        evidence, monkeypatch, damage):
    run, package, receipt, mark = evidence
    if damage == "absent-receipt":
        receipt.unlink()
    elif damage == "invalid-json":
        receipt.write_bytes(b'{"sent":')
    elif damage == "invalid-utf8":
        receipt.write_bytes(b"\xff\xfe\xff")
    elif damage == "non-object":
        receipt.write_bytes(b"[]")
    elif damage == "absent-package":
        package.unlink()
    else:
        if damage == "numeric-sent":
            mark["sent"] = 1
        elif damage == "missing-sent":
            del mark["sent"]
        elif damage == "foreign-sha":
            mark["sha"] = "0" * 64
        elif damage == "unreadable-package":
            real_read = file_replace.read_bytes_shared

            def blocked(path):
                if path == package:
                    raise PermissionError("isolated ZIP reader refusal")
                return real_read(path)

            monkeypatch.setattr(file_replace, "read_bytes_shared", blocked)
        receipt.write_text(json.dumps(mark), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (package, receipt) if path.exists()}
    result = delivery_mark.read_current(run)
    assert result["sent"] is False
    assert result["detail"].startswith("текущая доставка не подтверждена: ")
    assert set(result) == {"sent", "detail"}, "Do not expose stale verdict/identity fields"
    assert {path.name: path.read_bytes() for path in (package, receipt) if path.exists()} == before


def test_failed_delivery_for_another_zip_does_not_relabel_its_error(evidence):
    run, package, receipt, mark = evidence
    stale = {"sent": False, "sha": "0" * 64,
             "detail": "old ZIP: remote receiver unavailable"}
    receipt.write_text(json.dumps(stale), encoding="utf-8")
    before = package.read_bytes(), receipt.read_bytes()
    assert delivery_mark.read_current(run) == {
        "sent": False,
        "detail": "текущая доставка не подтверждена: квитанция от другого ZIP",
    }, "Never attach a previous ZIP's SHA or failure detail to the current ZIP"
    assert (package.read_bytes(), receipt.read_bytes()) == before


def test_no_artifacts_is_the_only_empty_history_case(tmp_path):
    run = tmp_path / "run-not-created"
    assert delivery_mark.read_current(run) == {}
    assert not run.exists()
