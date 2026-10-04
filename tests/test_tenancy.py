"""Per-user tenancy of data, jobs and results (webapp, dev auth mode).

The app runs with CREW_AUTH_MODE=dev and the identity is switched between
requests by rewriting CREW_DEV_USER (the mode resolves it per request):
two regular users A and B plus the primary admin. The user catalog and the
ownership map are redirected to a tmp directory — the real webapp/config
is never touched; upload payload files land in the real
data/uploads/<user-slug>/ (that path is fixed by the contract) and are
removed by the fixtures.

Covered, per the tenancy spec:
  * A's upload is visible to A (list + GET + results) and invisible to B
    (missing from the list; direct GET / results / roster / job start all
    answer 404 — never 403, existence must not leak);
  * the admin sees A's upload, annotated with its owner;
  * seed data (gerad-1) belongs to the primary admin: the admin sees it,
    a plain user does not (empty workspace until they upload);
  * job listing/detail/log isolation: owner-or-admin;
  * a job on the owner's seed data is 404 for a plain user, allowed for the
    admin, and the response warns that the seed's canonical artifacts will
    be overwritten;
  * uploads/jobs_run per-user counters are bumped;
  * legacy flat files in data/uploads are backfilled to the primary admin.

No pipeline subprocess is ever started: the one job-start test stubs
jobs.start_job (command building/execution is covered elsewhere).
"""
import copy
import json
import os
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

REPO_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = REPO_ROOT / "data" / "uploads"

ADMIN = ADMIN_EMAIL                  # seeded default admin
USER_A = "alice.tenant@example.com"
USER_B = "bob.tenant@example.com"

from webapp import registry as _registry_mod  # noqa: E402

# Since audit 2026-07-19 (P0-03) the user slug embeds an email digest, so it
# is derived, not hardcoded (identities with no legacy ownership footprint —
# like these test users — always get the hashed form). Derived LAZILY, not
# at import time: user_slug grandfathers legacy rows from OWNERSHIP_PATH,
# and at import the path still points at the LIVE machine map — a targeted
# run of this module on a dev machine with old alice-rows produced a slug
# that no longer matched what the server (already monkeypatched to a clean
# tmp map) answered (false red, 2026-08-31).
def _slug_a() -> str:
    return _registry_mod.user_slug(USER_A)


UPLOAD_NAME = "tenancy-data"


# id joins user slug and upload slug with '--' (registry.UPLOAD_LABEL_SEP):
# a bare hyphen would alias distinct (user, name) pairs.
def _inst_a_id() -> str:
    return f"upload-{_slug_a()}--{UPLOAD_NAME}"


def _upload_file_a() -> Path:
    return UPLOADS_DIR / _slug_a() / f"{UPLOAD_NAME}.json"

# Minimal schema-valid canonical payload (same shape as test_webapp_api).
MINIMAL_VALID_PAYLOAD = {
    "formatVersion": "1",
    "airports": [
        {"code": "AAA"},
        {"code": "BBB"},
    ],
    "employees": [
        {"id": "E1", "homeAirport": "AAA", "skills": ["Pilot"]},
    ],
    "flights": [
        {
            "flightNumber": "F1",
            "departureAirport": "AAA",
            "departureUTCDateTime": "2026-07-01T06:00:00Z",
            "arrivalAirport": "BBB",
            "arrivalUTCDateTime": "2026-07-01T08:00:00Z",
        },
    ],
    "flightAssignments": [
        {"id": "F1-0", "flight": "F1", "indexInFlight": 0,
         "requiredSkill": "Pilot"},
    ],
}


def _as(email: str) -> None:
    """Switch the dev-mode identity for the NEXT request(s). The module
    fixture set CREW_DEV_USER via MonkeyPatch, so teardown still restores
    the original environment after direct assignments here."""
    os.environ["CREW_DEV_USER"] = email


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient in dev mode with catalog + ownership map in tmp.

    The fresh tmp users.db seeds the three default admins; A and B are
    added as regular (role=user) catalog rows.
    """
    try:
        from webapp import registry, users
        from webapp.app import app
    except Exception as exc:  # noqa: BLE001 — report the real import error
        pytest.fail(f"webapp.app is not importable: {exc!r}")
    from fastapi.testclient import TestClient

    cfg_dir = tmp_path_factory.mktemp("tenancy-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setattr(users, "USERS_DB_PATH", cfg_dir / "users.db")
    mp.setattr(registry, "OWNERSHIP_PATH", cfg_dir / "ownership.json")
    # Первичный админ — данные развёртывания (CREW_PRIMARY_ADMIN,
    # 2026-08-31): тест задаёт согласованную пару «личность =
    # первичный админ» сам, не полагаясь на окружение машины.
    mp.setattr(registry, "PRIMARY_ADMIN", ADMIN)
    try:
        with TestClient(app) as c:
            users.add(USER_A, "user", by="pytest-tenancy")
            users.add(USER_B, "user", by="pytest-tenancy")
            yield c
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def inst_a(client):
    """User A's upload, shared by the whole module; file removed at end."""
    _as(USER_A)
    resp = client.post(
        "/api/instances/upload",
        json={"name": UPLOAD_NAME,
              "payload": copy.deepcopy(MINIMAL_VALID_PAYLOAD)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == _inst_a_id()
    yield _inst_a_id()
    _upload_file_a().unlink(missing_ok=True)
    try:                       # drop the per-user dir if the test owned it
        _upload_file_a().parent.rmdir()
    except OSError:
        pass                   # non-empty: real user data, keep it


def _ids(client) -> set:
    resp = client.get("/api/instances")
    assert resp.status_code == 200
    return {i["id"] for i in resp.json()}


# --- instance visibility ------------------------------------------------------


def test_owner_sees_own_upload(client, inst_a):
    _as(USER_A)
    assert inst_a in _ids(client)
    resp = client.get(f"/api/instances/{inst_a}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == USER_A
    assert body["shared"] is False
    assert body["validation"]["ok"] is True
    # Results are reachable for the owner (empty sections, but 200).
    assert client.get(f"/api/results/{inst_a}").status_code == 200


def test_other_user_cannot_see_or_touch(client, inst_a):
    _as(USER_B)
    # Not listed...
    assert inst_a not in _ids(client)
    # ...and every direct route answers 404 (not 403: no existence leak).
    assert client.get(f"/api/instances/{inst_a}").status_code == 404
    assert client.get(f"/api/results/{inst_a}").status_code == 404
    assert client.get(f"/api/results/{inst_a}/roster.csv").status_code == 404
    assert client.get(f"/api/results/{inst_a}/pairing").status_code == 404
    resp = client.post("/api/jobs", json={"type": "validate",
                                          "instance_id": inst_a,
                                          "params": {}})
    assert resp.status_code == 404


def test_admin_sees_upload_with_owner_annotation(client, inst_a):
    _as(ADMIN)
    listed = {i["id"]: i for i in client.get("/api/instances").json()}
    assert inst_a in listed
    assert listed[inst_a]["owner"] == USER_A
    assert listed[inst_a]["shared"] is False
    assert client.get(f"/api/instances/{inst_a}").status_code == 200
    assert client.get(f"/api/results/{inst_a}").status_code == 200


def test_seed_data_owned_by_primary_admin(client):
    # Ownership model (2026-07-13): the seed benchmark (gerad-1) belongs to
    # the primary admin — the admin sees it, a plain user does NOT (empty
    # workspace until they upload their own).
    _as(ADMIN)
    listed = {i["id"]: i for i in client.get("/api/instances").json()}
    assert "gerad-1" in listed
    assert listed["gerad-1"]["owner"] == ADMIN
    assert listed["gerad-1"]["shared"] is False
    assert client.get("/api/results/gerad-1").status_code == 200

    for email in (USER_A, USER_B):
        _as(email)
        ids = {i["id"] for i in client.get("/api/instances").json()}
        assert "gerad-1" not in ids, f"gerad-1 leaked to plain user {email}"
        # 404, not 403 — existence of the owner's data must not leak.
        assert client.get("/api/results/gerad-1").status_code == 404


def test_upload_stat_bumped_for_owner(client, inst_a):
    from webapp import users
    assert users.get_user(USER_A)["uploads"] == 1
    assert users.get_user(USER_B)["uploads"] == 0


# --- jobs isolation -------------------------------------------------------------


@pytest.fixture()
def fake_jobs(client):
    """Two finished in-memory job records owned by A and B (never saved to
    disk: only jobs.start_job/_watch persist)."""
    from webapp import jobs
    records = {
        "pytest-tenancy-job-a": {
            "id": "pytest-tenancy-job-a", "type": "validate",
            "instance_id": "gerad-1", "owner": USER_A,
            "status": "succeeded", "exit_code": 0,
            "started_at": "2026-07-13 00:00:01", "ended_at": None,
            "params": {},
        },
        "pytest-tenancy-job-b": {
            "id": "pytest-tenancy-job-b", "type": "validate",
            "instance_id": "gerad-1", "owner": USER_B,
            "status": "succeeded", "exit_code": 0,
            "started_at": "2026-07-13 00:00:02", "ended_at": None,
            "params": {},
        },
    }
    jobs._load_persisted()
    with jobs._lock:
        _jobs_backup = {k: jobs._jobs.get(k) for k in records}
        jobs._jobs.update(records)
    yield records
    with jobs._lock:
        for k, old in _jobs_backup.items():
            if old is None:
                jobs._jobs.pop(k, None)
            else:
                jobs._jobs[k] = old


def test_jobs_listing_isolated_by_owner(client, fake_jobs):
    _as(USER_A)
    ids_a = {j["id"] for j in client.get("/api/jobs").json()}
    assert "pytest-tenancy-job-a" in ids_a
    assert "pytest-tenancy-job-b" not in ids_a
    # A sees ONLY own jobs (any persisted legacy jobs belong to the admin).
    owners_a = {j["owner"] for j in client.get("/api/jobs").json()}
    assert owners_a <= {USER_A}

    _as(USER_B)
    ids_b = {j["id"] for j in client.get("/api/jobs").json()}
    assert "pytest-tenancy-job-b" in ids_b
    assert "pytest-tenancy-job-a" not in ids_b

    _as(ADMIN)
    ids_admin = {j["id"] for j in client.get("/api/jobs").json()}
    assert {"pytest-tenancy-job-a", "pytest-tenancy-job-b"} <= ids_admin


def test_job_detail_and_log_owner_or_admin(client, fake_jobs):
    _as(USER_B)
    # Another user's job id is indistinguishable from a nonexistent one.
    assert client.get("/api/jobs/pytest-tenancy-job-a").status_code == 404
    assert client.get("/api/jobs/pytest-tenancy-job-a/log").status_code == 404
    assert client.post("/api/jobs/pytest-tenancy-job-a/stop").status_code == 404

    _as(USER_A)
    resp = client.get("/api/jobs/pytest-tenancy-job-a")
    assert resp.status_code == 200
    assert resp.json()["owner"] == USER_A
    assert client.get("/api/jobs/pytest-tenancy-job-a/log").status_code == 200
    # Stopping an already-finished own job is an honest 409, not 404.
    assert client.post("/api/jobs/pytest-tenancy-job-a/stop").status_code == 409

    _as(ADMIN)
    assert client.get("/api/jobs/pytest-tenancy-job-b").status_code == 200


def test_seed_job_start_owner_only(client, monkeypatch):
    """A plain user cannot start a job on the owner's seed data (404 — no
    existence leak); the owner/admin can, and the response warns that the
    seed's canonical artifacts will be overwritten. jobs.start_job is
    stubbed — no subprocess in the battery."""
    from webapp import jobs, users

    calls = {}

    def fake_start_job(jtype, inst, params, owner):
        calls.update(jtype=jtype, inst_id=inst["id"], owner=owner)
        return "pytest-tenancy-fake-id"

    monkeypatch.setattr(jobs, "start_job", fake_start_job)

    # Plain user B: gerad-1 belongs to the admin -> 404.
    _as(USER_B)
    assert client.post("/api/jobs", json={
        "type": "validate", "instance_id": "gerad-1",
        "params": {}}).status_code == 404
    assert calls == {}

    # Admin: allowed, job owned by the runner, overwrite note present.
    jobs_before = users.get_user(ADMIN)["jobs_run"]
    _as(ADMIN)
    resp = client.post("/api/jobs", json={"type": "validate",
                                          "instance_id": "gerad-1",
                                          "params": {}})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["job_id"] == "pytest-tenancy-fake-id"
    assert calls == {"jtype": "validate", "inst_id": "gerad-1",
                     "owner": ADMIN}
    assert "эталон" in body.get("note", "")
    assert users.get_user(ADMIN)["jobs_run"] == jobs_before + 1


# --- legacy flat-file migration --------------------------------------------------


def test_legacy_flat_upload_backfilled_to_primary_admin(client):
    from webapp import registry
    legacy = UPLOADS_DIR / "pytest-tenancy-legacy.json"
    try:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps(MINIMAL_VALID_PAYLOAD),
                          encoding="utf-8")
        _as(ADMIN)
        listed = {i["id"]: i for i in client.get("/api/instances").json()}
        inst = listed.get("upload-pytest-tenancy-legacy")
        assert inst is not None, "legacy flat upload missing from registry"
        assert inst["owner"] == registry.PRIMARY_ADMIN
        # The backfill is persisted in the ownership map (tmp in tests).
        owners = json.loads(
            registry.OWNERSHIP_PATH.read_text(encoding="utf-8"))
        assert owners["upload-pytest-tenancy-legacy"] == \
            registry.PRIMARY_ADMIN
        # And it is NOT visible to a regular non-owner user.
        _as(USER_B)
        assert "upload-pytest-tenancy-legacy" not in _ids(client)
    finally:
        legacy.unlink(missing_ok=True)


# --- upload id/label injectivity (audit 2026-07-15) --------------------------


def test_upload_id_separator_is_injective():
    """(identity, name) -> (id, label, path) must be injective.

    Under the old bare-hyphen join these two DISTINCT tenants aliased: user
    'bob-evil' naming an upload 'x' and user 'bob' naming one 'evil-x' both
    produced id 'upload-bob-evil-x' and label 'bob-evil-x' — a cross-tenant
    existence leak (the collision-check 422 reveals another user's instance)
    plus artifact / --name namespace squatting. '--' can never occur inside a
    slug (slugify collapses hyphen runs and strips the ends; the digest
    suffix joins with a single hyphen), so the pair flattens uniquely."""
    from webapp import registry
    id1, label1, path1 = registry.upload_target("bob-evil@x.com", "x")
    id2, label2, path2 = registry.upload_target("bob@x.com", "evil-x")
    assert (id1, label1) != (id2, label2)
    assert path1 != path2            # already distinct (dir sep) — still holds
    # The '--' separator occurs exactly once in each label (lane parsing and
    # the (user, name) unflattening both rely on it).
    assert label1.count("--") == 1 and label2.count("--") == 1
    # Neither reproduces the historical collision value.
    assert "upload-bob-evil-x" not in (id1, id2)


def test_user_slug_distinct_domains_never_collide():
    """audit 2026-07-19 P0-03: same local part on different (allow-listed)
    domains must map to different upload namespaces — id, label AND path."""
    from webapp import registry
    t1 = registry.upload_target("alice@r-labs.ru", "ops")
    t2 = registry.upload_target("alice@integrotechnologies.ru", "ops")
    t3 = registry.upload_target("alice@ramax.ru", "ops")
    assert len({t1[0], t2[0], t3[0]}) == 3      # ids
    assert len({t1[1], t2[1], t3[1]}) == 3      # labels
    assert len({t1[2], t2[2], t3[2]}) == 3      # payload paths
    # Deterministic: the same identity always resolves to the same target.
    assert registry.upload_target("alice@r-labs.ru", "ops") == t1


def test_user_slug_grandfathers_existing_owner(monkeypatch, tmp_path):
    """An identity that already owns uploads under the historical local-part
    slug keeps it (ids/labels/artifacts must not be orphaned); a DIFFERENT
    identity with the same local part still gets the hashed slug."""
    from webapp import registry
    own = tmp_path / "ownership.json"
    own.write_text(json.dumps(
        {"upload-walter--acme": "walter@old-domain.ru"}), encoding="utf-8")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", own)
    assert registry.user_slug("walter@old-domain.ru") == "walter"
    other = registry.user_slug("walter@new-domain.ru")
    assert other != "walter" and other.startswith("walter-")


def test_claim_upload_is_atomic_and_exclusive(monkeypatch, tmp_path):
    """Second claim of the same id fails loudly; the first owner and payload
    survive intact (the old check->write->own sequence let the second writer
    overwrite both — audit 2026-07-19 P0-03)."""
    from webapp import registry
    own = tmp_path / "ownership.json"
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", own)
    target = tmp_path / "up" / "acme.json"
    registry.claim_upload("upload-w--acme", target, {"v": 1}, "w@d1.ru")
    with pytest.raises(registry.UploadExists):
        registry.claim_upload("upload-w--acme", target, {"v": 2}, "w@d2.ru")
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
    owners = json.loads(own.read_text(encoding="utf-8"))
    assert owners["upload-w--acme"] == "w@d1.ru"


def test_list_instances_keeps_legacy_hyphen_upload(monkeypatch, tmp_path):
    """Backward compat: a per-user upload registered before the '--' separator
    keeps its historical single-hyphen id. Its ownership row (and any
    label-keyed artifacts) are keyed there, so list_instances must serve it
    under that id — never orphan it to the primary admin nor silently rename
    it — while a file with no legacy row takes the new unambiguous id."""
    from webapp import registry
    us = "pytest-compat"
    updir = UPLOADS_DIR / us
    legacy_file = updir / "a-b.json"          # slug 'a-b' -> the ambiguous case
    fresh_file = updir / "c-d.json"
    legacy_id = f"upload-{us}-a-b"             # historical single-hyphen id
    new_style_of_legacy = f"upload-{us}--a-b"  # what the file would get today
    fresh_id = f"upload-{us}--c-d"
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "ownership.json")
    try:
        updir.mkdir(parents=True, exist_ok=True)
        legacy_file.write_text(json.dumps(MINIMAL_VALID_PAYLOAD),
                               encoding="utf-8")
        fresh_file.write_text(json.dumps(MINIMAL_VALID_PAYLOAD),
                              encoding="utf-8")
        registry.set_owner(legacy_id, USER_A)      # pre-existing legacy row

        by_id = {i["id"]: i for i in registry.list_instances()}
        # Legacy upload kept on its historical id/label, owner preserved.
        assert legacy_id in by_id
        assert by_id[legacy_id]["owner"] == USER_A
        assert by_id[legacy_id]["label"] == f"{us}-a-b"
        assert new_style_of_legacy not in by_id    # not renamed / double-listed
        # A file with no legacy ownership row takes the new '--' id.
        assert fresh_id in by_id
        assert by_id[fresh_id]["label"] == f"{us}--c-d"
    finally:
        legacy_file.unlink(missing_ok=True)
        fresh_file.unlink(missing_ok=True)
        try:
            updir.rmdir()
        except OSError:
            pass


def test_user_slug_stable_despite_admin_backfill(monkeypatch, tmp_path):
    """Ревью пакета 2026-07-19: бэкфилл сироты на PRIMARY_ADMIN под чужим
    слагом не должен переключать grandfathering владельца (детерминизм
    user_slug во времени)."""
    from webapp import registry
    # Первичный админ — данные развёртывания (CREW_PRIMARY_ADMIN);
    # тест фиксирует КЛАСС поведения на синтетическом адресе.
    monkeypatch.setattr(registry, "PRIMARY_ADMIN", "prima@example.com")
    own = tmp_path / "ownership.json"
    own.write_text(json.dumps({
        "upload-walter--acme": "walter@old-domain.ru",
        # синтетическая строка бэкфилла сироты на первичного админа
        "upload-walter--orphan": registry.PRIMARY_ADMIN,
    }), encoding="utf-8")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", own)
    assert registry.user_slug("walter@old-domain.ru") == "walter"
    # а сам первичный админ grandfathering по своим строкам не теряет
    own.write_text(json.dumps(
        {"upload-prima--x": registry.PRIMARY_ADMIN}), encoding="utf-8")
    assert registry.user_slug(registry.PRIMARY_ADMIN) == "prima"


def test_user_slug_unreadable_map_fails_closed(monkeypatch, tmp_path):
    """Нечитаемая карта владения — громкий отказ, а не тихая вилка
    неймспейса на hashed-слаг (ревью пакета 2026-07-19)."""
    from webapp import registry
    own = tmp_path / "ownership.json"
    own.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", own)
    with pytest.raises(RuntimeError):
        registry.user_slug("walter@old-domain.ru")


def test_claim_upload_failed_write_does_not_block_name(monkeypatch, tmp_path):
    """Сбой сериализации не оставляет частичный файл, блокирующий имя
    (ревью пакета 2026-07-19)."""
    from webapp import registry
    own = tmp_path / "ownership.json"
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", own)
    target = tmp_path / "up" / "acme.json"
    with pytest.raises(TypeError):
        registry.claim_upload("upload-w--acme", target,
                              {"bad": object()}, "w@d1.ru")
    assert not target.exists()
    # имя свободно: повторная попытка с валидным payload проходит
    registry.claim_upload("upload-w--acme", target, {"v": 1}, "w@d1.ru")
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
