"""User catalog + admin API (webapp). The auth-core agent crashed before
writing this file; it locks in the catalog invariants (seeding, role/status
mutation, last-admin protection, email normalization, revoke) and the admin
HTTP surface (admin-only, CRUD, 409 on last-admin removal, audit log).

Пакет «умолчания-в-данные» (2026-08-29): вшитых адресов в коде больше
нет — посев каталога идёт из CREW_SUPER_ADMIN, поимённый allow-list из
CREW_ALLOWLIST_EMAILS. Здесь оба носителя подменяются СИНТЕТИЧЕСКИМИ
адресами через monkeypatch env + пересбор модульных констант теми же
боевыми парсерами (_admins_from_env/_allowlist_emails_from_env).

The user DB and admin audit log are redirected to a tmp dir — the real
webapp/config is never touched. Admin API is exercised in dev auth mode.
"""
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]

# Синтетические личности (не живые адреса): тройка админов первого
# запуска и один поимённо допущенный гость.
ADMIN_1 = "admin.one@unit.example"
ADMIN_2 = "admin.two@unit.example"
ADMIN_3 = "admin.three@unit.example"
DEFAULT_ADMINS = {ADMIN_1, ADMIN_2, ADMIN_3}
GUEST = "invited.guest@unit.example"


def _seed_env(monkeypatch, users):
    """Синтетические носители в env + пересбор констант БОЕВЫМИ парсерами
    (значение течёт через тот же код, что и при старте приложения);
    monkeypatch откатывает и env, и константы."""
    monkeypatch.setenv("CREW_SUPER_ADMIN",
                       ",".join((ADMIN_1, ADMIN_2, ADMIN_3)))
    monkeypatch.setenv("CREW_ALLOWLIST_EMAILS", f"{GUEST}:admin")
    monkeypatch.setattr(users, "DEFAULT_ADMIN_EMAILS",
                        users._admins_from_env())
    monkeypatch.setattr(users, "ALLOWLIST_EMAILS",
                        users._allowlist_emails_from_env())


@pytest.fixture()
def users_mod(tmp_path, monkeypatch):
    """Fresh users module with a tmp SQLite catalog, seeded with the three
    synthetic env admins."""
    from webapp import users
    _seed_env(monkeypatch, users)
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    return users


# --------------------------------------------------------------- env parsing

def test_no_env_means_no_seeded_admins(tmp_path, monkeypatch):
    """Пусто/нет CREW_SUPER_ADMIN и CREW_ALLOWLIST_EMAILS → пустые
    носители: каталог сеется БЕЗ админов, allow-list адресов нет."""
    from webapp import users
    monkeypatch.setenv("CREW_SUPER_ADMIN", "")
    monkeypatch.setenv("CREW_ALLOWLIST_EMAILS", "")
    assert users._admins_from_env() == ()
    assert users._allowlist_emails_from_env() == {}
    monkeypatch.setattr(users, "DEFAULT_ADMIN_EMAILS", ())
    monkeypatch.setattr(users, "ALLOWLIST_EMAILS", {})
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    assert users.list_all() == []
    assert users.authorize(ADMIN_1) is None


def test_malformed_env_is_refused(monkeypatch):
    """Кривые данные → отказ при старте, не догадка."""
    from webapp import users
    monkeypatch.setenv("CREW_ALLOWLIST_EMAILS", "no.role@unit.example")
    with pytest.raises(ValueError, match="email:role"):
        users._allowlist_emails_from_env()
    monkeypatch.setenv("CREW_ALLOWLIST_EMAILS", "x@unit.example:tsar")
    with pytest.raises(ValueError, match="рол"):
        users._allowlist_emails_from_env()
    monkeypatch.setenv("CREW_SUPER_ADMIN", "not-an-email")
    with pytest.raises(ValueError):
        users._admins_from_env()


# --------------------------------------------------------------- catalog unit

def test_seeds_three_default_admins(users_mod):
    listed = {u["email"]: u for u in users_mod.list_all()}
    assert set(listed) == DEFAULT_ADMINS
    assert all(u["role"] == "admin" and u["status"] == "active"
               for u in listed.values())


def test_email_normalization(users_mod):
    users_mod.add("Guest.User@Example.COM", "user", by=ADMIN_1)
    assert users_mod.authorize("guest.user@example.com") is not None
    # A duplicate under a different case is still a duplicate.
    with pytest.raises(users_mod.UserExistsError):
        users_mod.add("guest.user@example.com", "user", by=ADMIN_1)


def test_revoke_blocks_authorize(users_mod):
    users_mod.add("temp@example.com", "user", by=ADMIN_1)
    assert users_mod.authorize("temp@example.com") is not None
    users_mod.set_status("temp@example.com", "revoked")
    assert users_mod.authorize("temp@example.com") is None


def test_allowlist_domain_autoprovisions_admin(users_mod):
    """Any *@ramax.ru identity is authorized as admin on first login and
    becomes a real, catalog-visible row (auto-provision)."""
    assert users_mod.get_user("someone@ramax.ru") is None
    who = users_mod.authorize("Someone@Ramax.RU")     # case-insensitive
    assert who == {"email": "someone@ramax.ru", "role": "admin"}
    # a durable row now exists, marked as allow-list provisioned
    row = users_mod.get_user("someone@ramax.ru")
    assert row["role"] == "admin" and row["status"] == "active"
    assert row["created_by"] == users_mod.ALLOWLIST_BY
    # second login just finds the existing row (no duplicate, no error)
    assert users_mod.authorize("someone@ramax.ru")["role"] == "admin"
    assert len([u for u in users_mod.list_all()
                if u["email"] == "someone@ramax.ru"]) == 1


def test_allowlist_specific_email_autoprovisions_admin(users_mod):
    who = users_mod.authorize(GUEST)
    assert who == {"email": GUEST, "role": "admin"}
    # a different address of the same domain is NOT allow-listed
    assert users_mod.authorize("random.person@unit.example") is None


def test_allowlist_partner_domains_are_admin(users_mod):
    """r-labs.ru and integrotechnologies.ru join ramax.ru as admin domains
    (2026-07-17)."""
    for dom in ("r-labs.ru", "integrotechnologies.ru"):
        who = users_mod.authorize(f"user@{dom}")
        assert who == {"email": f"user@{dom}", "role": "admin"}, dom
        assert users_mod.get_user(f"user@{dom}")["created_by"] == \
            users_mod.ALLOWLIST_BY


def test_allowlist_does_not_resurrect_a_revoke(users_mod):
    """An explicit revoke wins over the domain allow-list: a revoked
    ramax.ru user stays out until an admin reactivates them."""
    users_mod.authorize("staffer@ramax.ru")           # provisions
    users_mod.set_status("staffer@ramax.ru", "revoked")
    assert users_mod.authorize("staffer@ramax.ru") is None   # not revived
    users_mod.set_status("staffer@ramax.ru", "active")
    assert users_mod.authorize("staffer@ramax.ru")["role"] == "admin"


def test_non_allowlisted_domain_is_denied(users_mod):
    assert users_mod.authorize("outsider@example.com") is None


def test_last_admin_protection(users_mod):
    # Demote two of the three admins, leaving exactly one.
    users_mod.set_role(ADMIN_2, "user")
    users_mod.set_role(ADMIN_3, "user")
    # The last active admin cannot be demoted, revoked or deleted.
    with pytest.raises(users_mod.LastAdminError):
        users_mod.set_role(ADMIN_1, "user")
    with pytest.raises(users_mod.LastAdminError):
        users_mod.set_status(ADMIN_1, "revoked")
    with pytest.raises(users_mod.LastAdminError):
        users_mod.delete(ADMIN_1)
    assert users_mod.authorize(ADMIN_1)["role"] == "admin"


# --------------------------------------------------------------- admin HTTP

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_1)
    from webapp import users, admin_api
    _seed_env(monkeypatch, users)
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(admin_api, "AUDIT_LOG_PATH", tmp_path / "audit.log")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, users, admin_api


def _as(email, monkeypatch=None):
    os.environ["CREW_DEV_USER"] = email


def test_admin_lists_and_adds_user(client):
    c, users, _ = client
    resp = c.get("/api/admin/users")
    assert resp.status_code == 200
    assert {u["email"] for u in resp.json()["users"]} == DEFAULT_ADMINS
    add = c.post("/api/admin/users",
                 json={"email": "newbie@example.com", "role": "user"})
    assert add.status_code == 201
    assert add.json()["user"]["role"] == "user"


def test_plain_user_forbidden_on_admin_api(client):
    c, users, _ = client
    users.add("plain@example.com", "user", by=ADMIN_1)
    _as("plain@example.com")
    assert c.get("/api/admin/users").status_code == 403
    assert c.post("/api/admin/users",
                  json={"email": "x@y.com", "role": "user"}).status_code == 403
    _as(ADMIN_1)


def test_admin_delete_last_admin_conflicts(client):
    c, users, _ = client
    c.patch(f"/api/admin/users/{ADMIN_2}", json={"role": "user"})
    c.patch(f"/api/admin/users/{ADMIN_3}", json={"role": "user"})
    resp = c.delete(f"/api/admin/users/{ADMIN_1}")
    assert resp.status_code == 409


def test_admin_mutations_audit_logged(client):
    c, _, admin_api = client
    c.post("/api/admin/users",
           json={"email": "audit@example.com", "role": "user"})
    log = admin_api.AUDIT_LOG_PATH.read_text(encoding="utf-8")
    assert "add_user" in log and "audit@example.com" in log
    assert f"actor={ADMIN_1}" in log
