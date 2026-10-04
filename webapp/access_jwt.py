"""Strict re-validation of Cloudflare Access JWTs (Cf-Access-Jwt-Assertion).

Cloudflare Access (team <team>.cloudflareaccess.com) authenticates users
with email OTP in front of the app; cloudflared additionally validates the
JWT (originRequest.access). The app still REVALIDATES the assertion itself
and never trusts plain headers like Cf-Access-Authenticated-User-Email:

  * compact JWT only, bounded length, strict base64url segments;
  * header: alg RS256 only (nothing else, ever), valid kid, typ JWT;
  * claims: iss == the configured team domain, aud contains the configured
    application tag (CREW_ACCESS_AUD), type == "app", iat/nbf/exp integers
    checked with a fixed 60 s clock skew;
  * email claim validated against a conservative address grammar and
    returned in canonical lowercase form;
  * signature verified with the `cryptography` library against the team's
    published RSA keys (https://<team>.cloudflareaccess.com/cdn-cgi/access/
    certs), cached for 6 h with a 24 h stale tolerance and a bounded
    response size.

The verifier takes an injectable cert fetcher and clock so unit tests run
with a locally generated keypair and no network. Every rejection raises
AccessJwtError with a specific message — loud failures, no silent None.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

MAX_JWT_LENGTH = 16_384
MAX_CERT_RESPONSE_BYTES = 1_000_000
CLOCK_SKEW_S = 60
CERT_CACHE_S = 6 * 3600           # normal cert cache lifetime
CERT_STALE_S = 24 * 3600          # tolerated staleness during CF outages
FETCH_TIMEOUT_S = 5.0
RETRY_BACKOFF_S = 30.0            # do not hammer the cert endpoint

DEFAULT_TEAM = "blackmini"

_B64URL = re.compile(r"[A-Za-z0-9_-]+")
_TEAM_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")
_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class AccessJwtError(ValueError):
    """Any reason an Access assertion is rejected (format, claims, keys,
    signature). One type on purpose: callers must fail closed regardless
    of which check tripped, and the message carries the specifics."""


@dataclass(frozen=True)
class AccessIdentity:
    """Validated, non-secret identity fields from one Access assertion."""

    email: str
    subject: str
    issued_at: int
    not_before: int
    expires_at: int
    key_id: str


# A cert fetcher maps (url, timeout_seconds) -> parsed JSON envelope.
CertFetcher = Callable[[str, float], Mapping[str, Any]]


# --------------------------------------------------------------- primitives

def _decode_base64url(segment: str, label: str) -> bytes:
    """Strict, canonical base64url decode of one JWT segment."""
    if len(segment) % 4 == 1:
        raise AccessJwtError(f"JWT {label} is invalid base64url")
    encoded = segment.encode("ascii")
    padded = encoded + (b"=" * (-len(encoded) % 4))
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AccessJwtError(f"JWT {label} is invalid base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=")
    if canonical != encoded:
        raise AccessJwtError(f"JWT {label} is non-canonical base64url")
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json_object(segment: str, label: str) -> dict[str, Any]:
    raw = _decode_base64url(segment, label)
    try:
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AccessJwtError(f"JWT {label} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AccessJwtError(f"JWT {label} must be a JSON object")
    return value


def _compact_segments(token: object) -> tuple[str, str, str]:
    if not isinstance(token, str) or not token or len(token) > MAX_JWT_LENGTH:
        raise AccessJwtError("Access JWT length is invalid")
    segments = token.split(".")
    if len(segments) != 3 or any(not s for s in segments):
        raise AccessJwtError(
            "Access JWT must contain exactly three non-empty segments")
    if any(_B64URL.fullmatch(s) is None for s in segments):
        raise AccessJwtError("Access JWT is not strict base64url")
    return segments[0], segments[1], segments[2]


def _numeric_date(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccessJwtError(
            f"Access JWT {name} must be a non-negative integer")
    return value


def normalize_access_email(value: object) -> str:
    """Return the canonical lowercase ASCII form of an Access email claim."""
    if not isinstance(value, str) or not 3 <= len(value) <= 320:
        raise AccessJwtError("Access JWT email is missing or invalid")
    if (not value.isascii() or value != value.strip()
            or any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127
                   for ch in value)):
        raise AccessJwtError("Access JWT email is missing or invalid")
    if value.count("@") != 1:
        raise AccessJwtError("Access JWT email is missing or invalid")
    local, domain = value.rsplit("@", 1)
    if (_EMAIL_LOCAL.fullmatch(local) is None
            or local.startswith(".") or local.endswith(".") or ".." in local):
        raise AccessJwtError("Access JWT email is missing or invalid")
    if not 1 <= len(domain) <= 255:
        raise AccessJwtError("Access JWT email is missing or invalid")
    labels = domain.split(".")
    if any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels):
        raise AccessJwtError("Access JWT email is missing or invalid")
    return f"{local.lower()}@{domain.lower()}"


def _audiences(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = (value,)
    elif isinstance(value, list) and 1 <= len(value) <= 64:
        raw = tuple(value)
    else:
        raise AccessJwtError("Access JWT aud is missing or invalid")
    if any(not isinstance(item, str) or not item or len(item) > 512
           or item != item.strip() for item in raw):
        raise AccessJwtError("Access JWT aud is missing or invalid")
    return raw


def _parse_jwks(envelope: Mapping[str, Any]) -> dict[str, rsa.RSAPublicKey]:
    """Parse the certs endpoint response into {kid: RSA public key}."""
    if not isinstance(envelope, Mapping):
        raise AccessJwtError("cert response must be a JSON object")
    raw_keys = envelope.get("keys")
    if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 32:
        raise AccessJwtError("cert response has no usable keys")
    keys: dict[str, rsa.RSAPublicKey] = {}
    for raw in raw_keys:
        if not isinstance(raw, dict):
            raise AccessJwtError("JWK must be an object")
        key_id = raw.get("kid")
        if (not isinstance(key_id, str)
                or _KEY_ID.fullmatch(key_id) is None
                or key_id in keys
                or raw.get("kty") != "RSA"
                or raw.get("alg") not in (None, "RS256")
                or raw.get("use") not in (None, "sig")):
            raise AccessJwtError("JWK metadata is invalid")
        modulus = _jwk_integer(raw.get("n"), "modulus")
        exponent = _jwk_integer(raw.get("e"), "exponent")
        if not 2048 <= modulus.bit_length() <= 8192:
            raise AccessJwtError("JWK RSA modulus size is invalid")
        if exponent < 3 or exponent % 2 == 0 or exponent > 2 ** 32:
            raise AccessJwtError("JWK RSA exponent is invalid")
        keys[key_id] = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    return keys


def _jwk_integer(value: object, label: str) -> int:
    if not isinstance(value, str) or _B64URL.fullmatch(value) is None:
        raise AccessJwtError(f"JWK RSA {label} is invalid")
    raw = _decode_base64url(value, f"JWK {label}")
    if not raw or len(raw) > 1024 or (len(raw) > 1 and raw[0] == 0):
        raise AccessJwtError(f"JWK RSA {label} is invalid")
    return int.from_bytes(raw, "big")


def _default_fetcher(url: str, timeout: float) -> Mapping[str, Any]:
    """HTTPS fetch of the team certs with a hard response-size cap."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "crew-demo-access-verifier/1",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise OSError(
                f"cert endpoint returned HTTP {response.status}")
        raw = response.read(MAX_CERT_RESPONSE_BYTES + 1)
    if len(raw) > MAX_CERT_RESPONSE_BYTES:
        raise OSError("cert response is too large")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("cert response must be a JSON object")
    return value


# ------------------------------------------------------------------ verifier

class AccessVerifier:
    """Verify Access JWTs against the team's published signing keys.

    Team and audience are deployment configuration, never learned from a
    token. Keys are cached for ``cache_seconds``; if a refresh fails, keys
    no older than ``stale_seconds`` keep working (short Cloudflare outage
    tolerance); an unknown kid forces a refresh and fails closed.
    """

    def __init__(self, team: str = DEFAULT_TEAM, audience: str | None = None,
                 *, fetcher: CertFetcher | None = None,
                 clock: Callable[[], float] | None = None,
                 cache_seconds: float = CERT_CACHE_S,
                 stale_seconds: float = CERT_STALE_S,
                 timeout_seconds: float = FETCH_TIMEOUT_S,
                 retry_backoff_seconds: float = RETRY_BACKOFF_S) -> None:
        if not isinstance(team, str) or _TEAM_NAME.fullmatch(team) is None:
            raise ValueError(f"invalid Cloudflare team name: {team!r}")
        if not isinstance(audience, str) or not audience.strip():
            raise ValueError("audience (CREW_ACCESS_AUD) must be a "
                             "non-empty string")
        if not 60 <= float(cache_seconds) <= float(stale_seconds):
            raise ValueError("cache_seconds must be >= 60 and "
                             "<= stale_seconds")
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs"
        self.audience = audience.strip()
        self.cache_seconds = float(cache_seconds)
        self.stale_seconds = float(stale_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._fetcher: CertFetcher = fetcher or _default_fetcher
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._fetched_at = float("-inf")
        self._last_attempt = float("-inf")

    # -- public API ----------------------------------------------------------

    def verify(self, token: str, *, now: float | None = None) -> AccessIdentity:
        """Full verification: format, header, claims, then RS256 signature."""
        current = float(self._clock() if now is None else now)
        header_seg, payload_seg, signature_seg = _compact_segments(token)
        header = _decode_json_object(header_seg, "header")
        payload = _decode_json_object(payload_seg, "payload")
        signature = _decode_base64url(signature_seg, "signature")
        if not signature:
            raise AccessJwtError("JWT signature segment must not be empty")

        if header.get("alg") != "RS256":
            raise AccessJwtError("Access JWT alg must be RS256")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None:
            raise AccessJwtError("Access JWT kid is missing or invalid")
        typ = header.get("typ")
        if typ is not None and typ != "JWT":
            raise AccessJwtError("Access JWT typ must be JWT when present")

        if payload.get("iss") != self.issuer:
            raise AccessJwtError("Access JWT issuer does not match the "
                                 "configured team")
        if self.audience not in _audiences(payload.get("aud")):
            raise AccessJwtError("Access JWT audience does not match "
                                 "CREW_ACCESS_AUD")
        if payload.get("type") != "app":
            raise AccessJwtError("Access JWT type must be app")

        issued_at = _numeric_date(payload, "iat")
        not_before = _numeric_date(payload, "nbf")
        expires_at = _numeric_date(payload, "exp")
        if issued_at > expires_at or not_before > expires_at:
            raise AccessJwtError("Access JWT time claims are inconsistent")
        if current > expires_at + CLOCK_SKEW_S:
            raise AccessJwtError("Access JWT has expired")
        if current + CLOCK_SKEW_S < not_before:
            raise AccessJwtError("Access JWT is not valid yet")
        if current + CLOCK_SKEW_S < issued_at:
            raise AccessJwtError("Access JWT was issued in the future")

        email = normalize_access_email(payload.get("email"))
        subject = payload.get("sub")
        if not isinstance(subject, str) or not 1 <= len(subject) <= 512:
            raise AccessJwtError("Access JWT sub is missing or invalid")

        signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
        key = self._key(key_id)
        try:
            key.verify(signature, signing_input, padding.PKCS1v15(),
                       hashes.SHA256())
        except InvalidSignature as exc:
            raise AccessJwtError("Access JWT signature is invalid") from exc

        return AccessIdentity(email=email, subject=subject,
                              issued_at=issued_at, not_before=not_before,
                              expires_at=expires_at, key_id=key_id)

    # -- key cache ------------------------------------------------------------

    def _key(self, key_id: str) -> rsa.RSAPublicKey:
        with self._lock:
            now = float(self._clock())
            age = now - self._fetched_at
            cached = self._keys.get(key_id)
            if cached is not None and age <= self.cache_seconds:
                return cached
            # Cache expired or the kid is unknown: try to refresh, unless a
            # refresh just failed (backoff window).
            if now - self._last_attempt < self.retry_backoff_seconds:
                if cached is not None and age <= self.stale_seconds:
                    return cached
                raise AccessJwtError(
                    "Cloudflare Access signing keys are temporarily "
                    "unavailable (refresh backoff)")
            self._last_attempt = now
            try:
                envelope = self._fetcher(self.certs_url, self.timeout_seconds)
                self._keys = _parse_jwks(envelope)
                self._fetched_at = float(self._clock())
                self._last_attempt = float("-inf")   # success clears backoff
            except Exception as exc:
                # Stale tolerance: a recent snapshot keeps working through
                # a short cert-endpoint outage; anything older fails closed.
                if cached is not None and age <= self.stale_seconds:
                    return cached
                raise AccessJwtError(
                    f"cannot fetch Cloudflare Access signing keys: "
                    f"{exc}") from exc
            key = self._keys.get(key_id)
            if key is None:
                raise AccessJwtError(
                    "Access JWT signing key id is unknown to the team "
                    "cert endpoint")
            return key


def verifier_from_env(*, fetcher: CertFetcher | None = None) -> AccessVerifier:
    """Build a verifier from CREW_ACCESS_TEAM / CREW_ACCESS_AUD.

    CREW_ACCESS_AUD is REQUIRED in access mode: without the audience tag
    any Access application on the team would be accepted. Loud failure.
    """
    team = os.environ.get("CREW_ACCESS_TEAM", "").strip() or DEFAULT_TEAM
    audience = os.environ.get("CREW_ACCESS_AUD", "").strip()
    if not audience:
        raise RuntimeError(
            "CREW_ACCESS_AUD is not set: the Access application audience "
            "tag is required in access auth mode")
    return AccessVerifier(team=team, audience=audience, fetcher=fetcher)
