"""Fixture-location controls; these are not extra semantic A05 evidence."""
import hashlib

import pytest

import test_a05_saved_repair_sequence_agent as sequence


def test_explicit_absolute_fixture_root_is_not_checkout_relative(tmp_path, monkeypatch):
    root = tmp_path / "external evidence" / "cdpm-audit-20260911"
    monkeypatch.setenv("COSMOS_AUDIT_ROOT", str(root))
    assert sequence._audit_root() == root.resolve()


@pytest.mark.parametrize("configured", ["", "relative/evidence"])
def test_invalid_explicit_root_does_not_fall_back(monkeypatch, configured):
    monkeypatch.setenv("COSMOS_AUDIT_ROOT", configured)
    with pytest.raises(ValueError, match="COSMOS_AUDIT_ROOT.*absolute"):
        sequence._audit_root()


def test_absent_fixture_is_named_and_not_accepted(tmp_path):
    absent = tmp_path / "missing-pinned-input"
    with pytest.raises(AssertionError, match="Saved A05 evidence is missing.*COSMOS_AUDIT_ROOT"):
        sequence._verified_text(absent, "0" * 64)


def test_configured_input_still_requires_the_exact_hash(tmp_path):
    path = tmp_path / "pinned-input"
    changed = b"changed evidence"
    expected = hashlib.sha256(b"original evidence").hexdigest()
    actual = hashlib.sha256(changed).hexdigest()
    path.write_bytes(changed)
    with pytest.raises(AssertionError) as failure:
        sequence._verified_text(path, expected)
    # Pytest may abbreviate the tuple containing a long temporary path.
    # The actual hash comparison must still report both exact digests.
    assert expected in str(failure.value) and actual in str(failure.value)
    assert path.read_bytes() == changed


def test_valid_utf8_input_is_returned_without_rewriting(tmp_path):
    raw = "Точная цитата\r\n".encode("utf-8")
    path = tmp_path / "pinned-input"
    path.write_bytes(raw)
    assert sequence._verified_text(path, hashlib.sha256(raw).hexdigest()) == raw.decode("utf-8")
    assert path.read_bytes() == raw
