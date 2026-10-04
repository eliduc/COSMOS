"""Raw receipt bytes must not lose contradictory values during JSON decoding.

These are synthetic transport/stored receipts bound to a minimal artifact.
They exercise the real sender and local reader, not package gates, a service,
or operational acceptance. Healthy controls retain ordinary legacy fields.
"""
import hashlib
import io
import json
import zipfile

import pytest

from cdpm import channel, delivery_mark


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-fixture-token")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "spec_version": "1.21", "source_run": "fixture-parent"}))
    package = buffer.getvalue()
    brief = {"sent": True, "sha": hashlib.sha256(package).hexdigest(),
             "verdict": "OK", "spec_version": "1.21",
             "source_run": "fixture-parent", "already_accepted": False,
             "mg": {"pairing": {"state": "stop", "launch_allowed": False}},
             "mismatch": [], "detail": "Synthetic receipt, no execution",
             "cosmos_run": None}
    run = tmp_path / "run"
    directory = run / "channel"
    directory.mkdir(parents=True)
    (directory / "task-package.zip").write_bytes(package)
    return run, package, brief


def _check(artifact, path, raw):
    run, package, _ = artifact
    if path == "stored":
        receipt = run / "channel" / "delivery.json"
        receipt.write_bytes(raw)
        result = delivery_mark.read_current(run)
        assert receipt.read_bytes() == raw, "Reading must retain original evidence"
    else:
        calls = []

        def transport(request, timeout):
            calls.append(request)
            assert request.data == package
            return Reply(raw)

        def no_sleep(_):
            pytest.fail("A deterministic unusable receipt must not cause retries")

        result = channel.send_task_package(package, "1.21", "fixture-parent",
                                           _urlopen=transport, _sleep=no_sleep)
        assert len(calls) == 1
    assert (run / "channel" / "task-package.zip").read_bytes() == package
    return result


@pytest.mark.parametrize("path", ["wire", "stored"])
@pytest.mark.parametrize("damage", [
    "sha-before", "sha-after", "verdict-before", "verdict-after",
    "escaped-source", "nested-duplicate", "duplicate-sent",
    "nan", "infinity", "float-overflow",
])
def test_ambiguous_or_nonfinite_receipt_cannot_confirm_delivery(artifact, path, damage):
    _, _, brief = artifact
    text = json.dumps(brief, ensure_ascii=False)
    if damage.startswith("sha-") or damage.startswith("verdict-"):
        field, order = damage.split("-")
        other = "0" * 64 if field == "sha" else "GATE_RED"
        entry = json.dumps(field) + ": " + json.dumps(other)
        text = ("{" + entry + "," + text[1:] if order == "before"
                else text[:-1] + "," + entry + "}")
    elif damage == "escaped-source":
        text = '{"source_\\u0072un": "foreign",' + text[1:]
    elif damage == "nested-duplicate":
        text = text.replace('"launch_allowed": false',
                            '"launch_allowed": true, "launch_allowed": false')
    elif damage == "duplicate-sent":
        text = '{"sent": false,' + text[1:]
    else:
        token = {"nan": "NaN", "infinity": "Infinity",
                 "float-overflow": "1e9999"}[damage]
        text = text[:-1] + ',"diagnostic": ' + token + "}"
    result = _check(artifact, path, text.encode("utf-8"))
    assert result["sent"] is False, (damage, path, result)
    assert "не подтверждена" in result["detail"]
    assert "verdict" not in result and "mg" not in result


@pytest.mark.parametrize("path", ["wire", "stored"])
@pytest.mark.parametrize("legacy", [False, True])
def test_unambiguous_receipt_and_nonfinite_words_remain_valid(artifact, path, legacy):
    _, _, brief = artifact
    brief["detail"] = 'Words NaN Infinity and quoted "sha" are not JSON numbers or keys'
    if legacy:
        brief.pop("spec_version")
        brief.pop("source_run")
    result = _check(artifact, path, json.dumps(brief).encode("utf-8"))
    assert result["sent"] is True
    assert result["detail"] == brief["detail"]
    assert result["sha"] == brief["sha"]
    assert result["mg"] == brief["mg"]
