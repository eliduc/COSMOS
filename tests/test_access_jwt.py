"""Unit tests for webapp/access_jwt.py — strict Cloudflare Access JWT
re-validation with a locally generated RSA keypair and a fake cert fetcher
(no network). Covers: the happy path, expired/not-yet-valid tokens, wrong
audience/issuer, wrong algorithm, garbage/oversize input, bad signatures,
unknown kid, cert caching and the 24 h stale tolerance.
"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from webapp import access_jwt
from webapp.access_jwt import AccessJwtError, AccessVerifier
from _stand_identity import ADMIN_EMAIL

TEAM = "blackmini"
ISSUER = f"https://{TEAM}.cloudflareaccess.com"
AUD = "a" * 64
KID = "test-key-1"
NOW = 1_780_000_000  # fixed wall-clock base for deterministic tests


# --------------------------------------------------------------- helpers

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_int(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid=KID) -> dict:
    numbers = private_key.public_key().public_numbers()
    return {"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig",
            "n": _b64u_int(numbers.n), "e": _b64u_int(numbers.e)}


def _make_jwt(private_key, *, claims=None, header=None, kid=KID,
              alg="RS256", signer_key=None) -> str:
    payload = {
        "iss": ISSUER, "aud": [AUD], "type": "app",
        # адрес в пропуске — НАРОЧНО заглавными: проверка обязана
        # привести его к нижнему регистру, поэтому ожидание тестов —
        # тот же ADMIN_EMAIL, а не отдельный литерал
        "email": ADMIN_EMAIL.upper(), "sub": "subject-1",
        "iat": NOW - 10, "nbf": NOW - 10, "exp": NOW + 3600,
    }
    payload.update(claims or {})
    payload = {k: v for k, v in payload.items() if v is not None}
    head = {"alg": alg, "kid": kid, "typ": "JWT"}
    head.update(header or {})
    head = {k: v for k, v in head.items() if v is not None}
    h = _b64u(json.dumps(head).encode())
    p = _b64u(json.dumps(payload).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    signature = (signer_key or private_key).sign(
        signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64u(signature)}"


class _Clock:
    def __init__(self, t=float(NOW)):
        self.t = float(t)

    def __call__(self):
        return self.t


class _Fetcher:
    """Fake cert fetcher: counts calls, can be told to start failing."""

    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = 0
        self.fail = False

    def __call__(self, url, timeout):
        self.calls += 1
        if self.fail:
            raise OSError("simulated cert endpoint outage")
        return self.envelope


def _verifier(keypair, **kwargs):
    fetcher = _Fetcher({"keys": [_jwk(keypair)]})
    clock = kwargs.pop("clock", _Clock())
    v = AccessVerifier(team=TEAM, audience=AUD, fetcher=fetcher,
                       clock=clock, **kwargs)
    return v, fetcher, clock


# ------------------------------------------------------------- happy path

def test_valid_token_returns_normalized_identity(keypair):
    v, fetcher, _ = _verifier(keypair)
    identity = v.verify(_make_jwt(keypair))
    assert identity.email == ADMIN_EMAIL   # lowercased
    assert identity.key_id == KID
    assert identity.subject == "subject-1"
    assert fetcher.calls == 1


def test_aud_as_plain_string_also_accepted(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"aud": AUD})
    assert v.verify(token).email == ADMIN_EMAIL


# ---------------------------------------------------------------- rejects

def test_expired_token_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"exp": NOW - 3600, "iat": NOW - 7200,
                                       "nbf": NOW - 7200})
    with pytest.raises(AccessJwtError, match="expired"):
        v.verify(token)


def test_expired_within_60s_skew_accepted(keypair):
    v, _, _ = _verifier(keypair)
    # A realistic token (issued in the past, iat < exp) that expired 30s ago
    # is still accepted inside the 60s clock-skew allowance. iat=NOW with
    # exp=NOW-30 would be genuinely malformed (iat > exp) and is rejected by
    # the consistency check — that is correct, so the claim set is realistic.
    token = _make_jwt(keypair, claims={"iat": NOW - 300, "nbf": NOW - 300,
                                       "exp": NOW - 30})
    assert v.verify(token).email == ADMIN_EMAIL


def test_not_yet_valid_token_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"nbf": NOW + 3600, "iat": NOW + 3600,
                                       "exp": NOW + 7200})
    with pytest.raises(AccessJwtError, match="not valid yet"):
        v.verify(token)


def test_issued_in_the_future_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"iat": NOW + 600})
    with pytest.raises(AccessJwtError, match="issued in the future"):
        v.verify(token)


def test_wrong_audience_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"aud": ["b" * 64]})
    with pytest.raises(AccessJwtError, match="audience"):
        v.verify(token)


def test_wrong_issuer_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={
        "iss": "https://evil.cloudflareaccess.com"})
    with pytest.raises(AccessJwtError, match="issuer"):
        v.verify(token)


def test_wrong_type_claim_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, claims={"type": "org"})
    with pytest.raises(AccessJwtError, match="type must be app"):
        v.verify(token)


def test_wrong_alg_rejected_even_with_valid_rsa_signature(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, alg="RS512")
    with pytest.raises(AccessJwtError, match="alg must be RS256"):
        v.verify(token)


def test_alg_none_rejected(keypair):
    v, _, _ = _verifier(keypair)
    header = _b64u(json.dumps({"alg": "none", "kid": KID}).encode())
    payload = _b64u(json.dumps({"iss": ISSUER}).encode())
    with pytest.raises(AccessJwtError):
        v.verify(f"{header}.{payload}.{_b64u(b'x')}")


def test_missing_kid_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, header={"kid": None})
    with pytest.raises(AccessJwtError, match="kid"):
        v.verify(token)


def test_unknown_kid_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, kid="rotated-away")
    with pytest.raises(AccessJwtError, match="unknown"):
        v.verify(token)


def test_signature_from_wrong_key_rejected(keypair, other_keypair):
    v, _, _ = _verifier(keypair)
    token = _make_jwt(keypair, signer_key=other_keypair)
    with pytest.raises(AccessJwtError, match="signature is invalid"):
        v.verify(token)


def test_bad_email_claim_rejected(keypair):
    v, _, _ = _verifier(keypair)
    for bad in ("not-an-email", "two@@ats.com", "a b@x.com", "", None, 42):
        token = _make_jwt(keypair, claims={"email": bad})
        with pytest.raises(AccessJwtError, match="email"):
            v.verify(token)


@pytest.mark.parametrize("garbage", [
    "", "not-a-jwt", "a.b", "a.b.c.d", "..", "a..c",
    "?!bad.b64.segments", "a.b." ,
])
def test_garbage_tokens_rejected(keypair, garbage):
    v, _, _ = _verifier(keypair)
    with pytest.raises(AccessJwtError):
        v.verify(garbage)


def test_oversize_token_rejected(keypair):
    v, _, _ = _verifier(keypair)
    token = "a." + "b" * access_jwt.MAX_JWT_LENGTH + ".c"
    with pytest.raises(AccessJwtError, match="length"):
        v.verify(token)


def test_duplicate_json_keys_rejected(keypair):
    v, _, _ = _verifier(keypair)
    # Handcraft a payload with a duplicated claim key.
    head = _b64u(json.dumps({"alg": "RS256", "kid": KID}).encode())
    payload = _b64u(b'{"iss":"x","iss":"y"}')
    with pytest.raises(AccessJwtError, match="JSON"):
        v.verify(f"{head}.{payload}.{_b64u(b'sig')}")


# --------------------------------------------------------- cert cache/stale

def test_certs_are_cached_between_verifications(keypair):
    v, fetcher, _ = _verifier(keypair)
    v.verify(_make_jwt(keypair))
    v.verify(_make_jwt(keypair))
    assert fetcher.calls == 1


def test_cache_refreshes_after_expiry(keypair):
    v, fetcher, clock = _verifier(keypair)
    v.verify(_make_jwt(keypair))
    clock.t += access_jwt.CERT_CACHE_S + 60
    token = _make_jwt(keypair, claims={
        "iat": int(clock.t) - 10, "nbf": int(clock.t) - 10,
        "exp": int(clock.t) + 3600})
    v.verify(token)
    assert fetcher.calls == 2


def test_stale_keys_tolerated_within_24h_outage(keypair):
    v, fetcher, clock = _verifier(keypair)
    v.verify(_make_jwt(keypair))
    fetcher.fail = True
    clock.t += access_jwt.CERT_CACHE_S + 3600   # 7h: cache expired, stale ok
    token = _make_jwt(keypair, claims={
        "iat": int(clock.t) - 10, "nbf": int(clock.t) - 10,
        "exp": int(clock.t) + 3600})
    assert v.verify(token).email == ADMIN_EMAIL


def test_stale_keys_rejected_after_24h(keypair):
    v, fetcher, clock = _verifier(keypair)
    v.verify(_make_jwt(keypair))
    fetcher.fail = True
    clock.t += access_jwt.CERT_STALE_S + 3600   # 25h: beyond stale tolerance
    token = _make_jwt(keypair, claims={
        "iat": int(clock.t) - 10, "nbf": int(clock.t) - 10,
        "exp": int(clock.t) + 3600})
    with pytest.raises(AccessJwtError, match="cannot fetch"):
        v.verify(token)


def test_first_fetch_failure_fails_closed(keypair):
    fetcher = _Fetcher({"keys": [_jwk(keypair)]})
    fetcher.fail = True
    v = AccessVerifier(team=TEAM, audience=AUD, fetcher=fetcher,
                       clock=_Clock())
    with pytest.raises(AccessJwtError, match="cannot fetch"):
        v.verify(_make_jwt(keypair))


def test_bad_cert_envelope_rejected(keypair):
    fetcher = _Fetcher({"keys": []})
    v = AccessVerifier(team=TEAM, audience=AUD, fetcher=fetcher,
                       clock=_Clock())
    with pytest.raises(AccessJwtError):
        v.verify(_make_jwt(keypair))


def test_non_rsa_jwk_rejected(keypair):
    jwk = _jwk(keypair)
    jwk["kty"] = "EC"
    fetcher = _Fetcher({"keys": [jwk]})
    v = AccessVerifier(team=TEAM, audience=AUD, fetcher=fetcher,
                       clock=_Clock())
    with pytest.raises(AccessJwtError):
        v.verify(_make_jwt(keypair))


# ----------------------------------------------------------- configuration

def test_verifier_requires_audience(keypair):
    with pytest.raises(ValueError, match="audience"):
        AccessVerifier(team=TEAM, audience="")


def test_verifier_rejects_bad_team_name():
    with pytest.raises(ValueError, match="team"):
        AccessVerifier(team="Bad_Team!", audience=AUD)


def test_verifier_from_env_requires_aud(monkeypatch):
    monkeypatch.delenv("CREW_ACCESS_AUD", raising=False)
    with pytest.raises(RuntimeError, match="CREW_ACCESS_AUD"):
        access_jwt.verifier_from_env()


def test_verifier_from_env_defaults_team_blackmini(monkeypatch):
    monkeypatch.setenv("CREW_ACCESS_AUD", AUD)
    monkeypatch.delenv("CREW_ACCESS_TEAM", raising=False)
    v = access_jwt.verifier_from_env()
    assert v.issuer == "https://blackmini.cloudflareaccess.com"
    assert v.certs_url.endswith("/cdn-cgi/access/certs")


def test_normalize_access_email():
    assert (access_jwt.normalize_access_email("User.Name@EXAMPLE.Com")
            == "user.name@example.com")
    for bad in ("no-at", " lead@x.com", "a@", "@x.com", "a..b@x.com"):
        with pytest.raises(AccessJwtError):
            access_jwt.normalize_access_email(bad)
