"""Z-28: preserve exact schema bytes across Git's checkout EOL policy.

No normalization of the raw-byte/hash oracle is allowed here. Actual Git
checkout-filter BASE/AFTER evidence is captured separately, because this pure
test profile must not spawn subprocesses. No historical source is rewritten.
"""

import hashlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHA = "95c397f01cca661ffacb9bd462b9ad83d58d496f0e6eb3ef4a2082c1d0b549fa"
MODERN_SHA = "79785d6b7b5b15a0b867b92dfb8e196734c17f95af0bbe2c4e63a4632e0d655d"


def test_integration_schema_checkout_policy_explicitly_pins_lf():
    # Configuration contract only; the separate real Git-filter probe checks
    # that Git actually applies it to the draft and both historical copies.
    attributes = ROOT / ".gitattributes"
    assert attributes.is_file(), "integration schema copies need an explicit LF checkout policy"
    rules = [line.split() for line in attributes.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert ["catalog-integration/**/schema/catalog.schema.json", "text", "eol=lf"] in rules


@pytest.mark.parametrize("relative,expected_sha", [
    ("template_catalog/schemas/catalog.schema.json", LEGACY_SHA),
    ("template_catalog/schemas/catalog-0.7.schema.json", MODERN_SHA),
    ("catalog-integration/0.7.0-draft.2/schema/catalog.schema.json", MODERN_SHA),
    ("catalog-integration/0.7.0-draft.2/releases/0.5.0/schema/catalog.schema.json", LEGACY_SHA),
    ("catalog-integration/0.7.0-draft.2/releases/0.6.0/schema/catalog.schema.json", LEGACY_SHA),
])
def test_schema_raw_sha_oracle_is_not_relaxed_for_checkout_portability(relative, expected_sha):
    raw = (ROOT / relative).read_bytes()
    assert b"\n" in raw and b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == expected_sha
    # These intentionally different byte sequences must NOT pass the oracle:
    # neither CRLF conversion nor an otherwise valid JSON whitespace change.
    assert hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest() != expected_sha
    assert hashlib.sha256(raw + b" ").hexdigest() != expected_sha
