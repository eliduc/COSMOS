"""Cache busting: / and /index.html stamp local script/style URLs with
?v=<static version> and forbid caching of the document itself, so a deploy
becomes visible on a plain refresh (2026-07-16)."""
import re

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL

LOCAL_ASSETS = ("style.css", "i18n.js", "app.js", "onboarding.js",
                "demo.js", "training.js")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    import importlib
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_index_is_versioned_and_uncacheable(client):
    r = client.get("/")
    assert r.status_code == 200
    # the no_cache_static middleware forces revalidation of the document
    assert r.headers["cache-control"] == "no-cache, must-revalidate"
    versions = set()
    for name in LOCAL_ASSETS:
        m = re.search(r'"%s\?v=([0-9a-f]+)"' % re.escape(name), r.text)
        assert m, f"{name} is not version-stamped"
        versions.add(m.group(1))
    # one process — one version, identical on every asset
    assert len(versions) == 1
    # /index.html goes through the same handler
    r2 = client.get("/index.html")
    assert r2.text == r.text


def test_versioned_asset_still_served(client):
    r = client.get("/")
    m = re.search(r'"app\.js\?v=([0-9a-f]+)"', r.text)
    r2 = client.get("/app.js?v=" + m.group(1))
    assert r2.status_code == 200
    assert "TAB_GROUPS" in r2.text


def test_no_external_urls_stamped(client):
    # the regex must touch only bare local filenames, never absolute URLs
    r = client.get("/")
    assert "http?v=" not in r.text and "//?v=" not in r.text
