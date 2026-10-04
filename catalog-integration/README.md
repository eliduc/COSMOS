# Isolated A06 integration catalogue

## K12 candidate — 0.7.0-draft.3

`0.7.0-draft.3/` is a separate, explicitly selected test candidate. It adds
parameter-subject vocabulary for all 31 declared parameters. Its body hash is
`dd8687d5f8574f2f3573b5a908a15c7c7bb8b1142991bb1297c2e445cbfaa02e`.
The source vocabulary is `catalog/transition/parameter_subject_words_060.json`;
the `long_duty_threshold` phrases are corrected to the existing hard-base alias
contract. The obsolete soft-threshold phrases are not accepted synonyms.
Removing only the new parameter vocabulary yields the exact draft.2 body.

Draft.2 and all historical releases are retained, not rewritten. The new
version has no inherited owner approval, support claim, bindings, release act,
or activation. `released_by=аналитик` is required draft metadata, not acceptance.
Schema validity alone does not establish readiness for parameter answers:
`tests/test_a06_k12_catalog_answers.py` checks all addresses and the ordinary
answer/application/provenance/restoration path. The subject guard stays strict.

## Earlier draft.2 input and original characterization

`0.7.0-draft.2/` is an explicit test input, not the default catalogue and not
an approved release. Its canonical body fingerprint is
`07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357`.

The nine draft snapshot files preserve the supplied exchange draft exactly.
The local tools and executor witness are copied from the integration code;
only the two derived seed/delta carriers were refreshed from this body.
The five existing historical releases (0.2.0 through 0.6.0) are exact copies
from the draft's original catalogue source. They are not newly issued here.
No draft.1 release snapshot, owner act, marker data, bindings or support claim
has been fabricated or added.

Select this folder explicitly when performing the separately authorized
integration checks. Existing default-catalogue tests still inspect `catalog/`.
The focused profile is `tests/test_a06_integration_catalog.py`.

Current characterization: the body/direction and missing-snapshot controls
pass, but the healthy historical-release gate fails on the current reader.
The historical bodies have matching journal versions/fingerprints; current
schema/semantic validation rejects 0.2, 0.3, 0.4 and 0.6. This is an open
compatibility finding, not a passed gate or authority to change those bodies.
`releases_gate` serves historical release inspection endpoints; it is not
itself one of the twelve `release()` prechecks. Parity, live prechecks, the
owner's release act and post-release acceptance remain separate obligations.
