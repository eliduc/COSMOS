"""GET /api/artifacts/{name} is the PUBLIC reference library (audit 2026-07-15).

Unlike the per-tenant workspace route /api/results/{id} (owner-or-admin),
/api/artifacts serves the published GERAD/BTS benchmark corpus that the
onboarding demos hand to EVERY authenticated user (demo.py renders these very
files as download links and shows their roster/KPIs inline). It is therefore
intentionally NOT gated per-owner — the two routes are different surfaces.

The boundary that matters — a tenant's private upload is never downloadable
through this route — is enforced structurally by results.ARTIFACT_WHITELIST, a
positive allowlist of fixed GERAD/BTS names that can never name an upload.
These tests pin both halves: a re-gate must not silently break onboarding, and
a whitelist edit must not silently expose tenant data.

Dev auth mode; the identity is switched between requests by rewriting
CREW_DEV_USER (resolved per request). A fresh tmp users.db + ownership map keep
the real webapp/config untouched.
"""
import os

import pytest
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL          # seeded primary admin
PLAIN = "plain.reader@example.com"  # a regular, non-admin tenant


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    try:
        from webapp import registry, users
        from webapp.app import app
    except Exception as exc:  # noqa: BLE001 — surface the real import error
        pytest.fail(f"webapp.app is not importable: {exc!r}")
    from fastapi.testclient import TestClient

    cfg_dir = tmp_path_factory.mktemp("artifact-lib-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setattr(users, "USERS_DB_PATH", cfg_dir / "users.db")
    mp.setattr(registry, "OWNERSHIP_PATH", cfg_dir / "ownership.json")
    try:
        with TestClient(app) as c:
            users.add(PLAIN, "user", by="pytest-artifact-lib")
            yield c
    finally:
        mp.undo()


def _as(email: str) -> None:
    os.environ["CREW_DEV_USER"] = email


def test_plain_user_downloads_public_reference_corpus(client):
    # A plain, non-admin tenant may download the published benchmark files —
    # exactly what onboarding relies on — even though the per-tenant workspace
    # hides the same seed instance (empty workspace: /api/results/gerad-5 404).
    from webapp import results

    inst_roster = "gerad_stage2_assignment_inst5.json"   # public GERAD roster
    aggregate = "gerad_stage1_results.json"              # public GERAD report
    # Guard: the reference artifacts must actually exist, else this is vacuous.
    assert results.artifact_path(inst_roster) is not None, inst_roster
    assert results.artifact_path(aggregate) is not None, aggregate

    _as(PLAIN)
    assert client.get("/api/results/gerad-5").status_code == 404   # workspace
    assert client.get(f"/api/artifacts/{inst_roster}").status_code == 200
    assert client.get(f"/api/artifacts/{aggregate}").status_code == 200


def test_tenant_upload_never_downloadable_via_artifacts(client):
    # The invariant that matters: an upload's stage artifacts are named by its
    # upload label (never inst1..7), so they are not whitelist keys and 404 for
    # everyone — a plain tenant AND the admin — through this public route (the
    # admin reaches its own uploads via the gated /api/results, not here).
    from webapp import results

    upload_artifact = "gerad_stage2_assignment_acme-tenant-schedule.json"
    assert results.artifact_path(upload_artifact) is None, upload_artifact
    for email in (PLAIN, ADMIN):
        _as(email)
        assert client.get(
            f"/api/artifacts/{upload_artifact}").status_code == 404, email


def test_whitelist_contains_no_tenant_upload_name():
    # Defense in depth: every allowlist entry is a fixed GERAD/BTS reference
    # name; none is upload-derived. A future edit that added an upload name
    # (exposing tenant data through this ungated route) fails here.
    from webapp import results

    for name in results.ARTIFACT_WHITELIST:
        assert name == "experiment-manifest.json" \
            or name.startswith("gerad_"), \
            f"unexpected non-reference artifact in the public allowlist: {name}"
        assert not name.startswith("upload"), name
