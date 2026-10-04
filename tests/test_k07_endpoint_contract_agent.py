"""Registered POST /api/requests handler, called directly without ASGI.

The seam is route.endpoint(body=dict, request=Starlette Request): routing,
HTTP parsing/middleware, exception-response rendering, server startup and
resource/process locks are NOT exercised. create_app, header authorization,
role checks, Store/Journal, draft judge, writer and JSONResponse are real.
No TestClient, event loop, socket, subprocess, startup or verdict substitution.
The reused fixture copies only the base catalog into tmp_path; no release,
candidate materialization, binding build or parity tool is run.

Law: API-CONTRACT.md sections 2.2 and 8.1; P0 contracts 4.1 (receipt/1).
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from catalog_service import identity, request_queue
from catalog_service.app import create_app
from catalog_service.errors import ServiceError
from template_catalog.receipts import validate_receipt
from tests.test_k07_submit_integral_agent import (
    _tree_bytes,
    submission,  # noqa: F401 -- explicit fixture import for --noconftest
)


TOKEN = "synthetic-k07-token"


def _app(submission, **settings_patch):
    settings = submission.settings.with_(**settings_patch)
    app = create_app(settings)
    assert app.state.store.settings is settings
    assert app.state.journal.path == settings.journal_path
    assert app.state.journal.enabled is settings.is_edit()
    routes = [r for r in app.routes
              if getattr(r, "path", None) == "/api/requests"
              and "POST" in getattr(r, "methods", set())]
    assert len(routes) == 1
    route = routes[0]
    assert route.name == "submit_request" and route.status_code == 201
    assert not inspect.iscoroutinefunction(route.endpoint)
    return app, route.endpoint


def _request(app, product=None, token=None):
    headers = [(b"content-type", b"application/json")]
    for name, value in ((identity.PRODUCT_HEADER, product),
                        (identity.TOKEN_HEADER, token)):
        if value is not None:
            headers.append((name.lower().encode("ascii"),
                            value.encode("ascii")))
    # In-memory Request metadata only; no receive/send or HTTP client exists.
    return Request({"type": "http", "http_version": "1.1", "app": app,
                    "method": "POST", "scheme": "http",
                    "path": "/api/requests", "raw_path": b"/api/requests",
                    "query_string": b"", "headers": headers})


def _storage(app):
    settings = app.state.settings
    return (_tree_bytes(settings.catalog_dir),
            _tree_bytes(settings.work_dir), settings.work_dir.exists())


def _receipt(response, sent, status):
    assert isinstance(response, JSONResponse)
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(response.body)
    body = json.loads(response.body)
    assert validate_receipt(body, sent=sent) == []
    assert body["repeat"] is (status == 200)
    assert body["note"] == ("уже подана" if status == 200 else None)
    assert body["state"] == "подана"
    return body


@pytest.mark.parametrize("actor", ["owner", "analyst"])
@pytest.mark.parametrize("product", ["CDPM", "COSMOS"])
def test_registered_handler_authenticates_and_returns_201_then_200_receipt(
        submission, actor, product):
    app, endpoint = _app(submission, actor=actor)
    store, journal = app.state.store, app.state.journal
    assert identity.can(app.state.settings, "request-submit")
    assert product in store.catalog().dictionaries()["products"]
    sent = copy.deepcopy(submission.doc)
    sent["submitted_by"] = product
    before = _tree_bytes(submission.folder)
    count = len(store.catalog().requests())
    fingerprint = store.catalog().fingerprint()

    first = _receipt(endpoint(body=sent, request=_request(app, product, TOKEN)),
                     sent, 201)
    after = _tree_bytes(submission.folder)
    assert {p for p in before.keys() | after.keys()
            if before.get(p) != after.get(p)} == {"requests.json"}
    assert len(store.catalog().requests()) == count + 1
    saved = request_queue.find(store.catalog().requests(), sent["request_id"])
    assert saved == first["request"] and saved["submitted_by"] == product
    assert saved["submitted_at"] == sent["submitted_at"]
    assert first["submitted_by"] == product
    assert store.catalog().fingerprint() == fingerprint
    rows = journal.read()
    assert len(rows) == 1 and rows[0]["actor"] == app.state.settings.actor
    assert product in rows[0]["words"] and sent["request_id"] in rows[0]["words"]
    assert first["journal_ref"] == {
        "op": "request-submit", "at": rows[0]["at"], "actor": rows[0]["actor"]}

    after_first = _storage(app)
    again = _receipt(endpoint(body=copy.deepcopy(sent),
                              request=_request(app, product, TOKEN)), sent, 200)
    assert again["journal_ref"] == first["journal_ref"]
    assert again["request"] == first["request"]
    assert _storage(app) == after_first


@pytest.mark.parametrize("product,token,configured_token,status,code", [
    pytest.param("CDPM", None, TOKEN, 401, "bad-token", id="missing-token"),
    pytest.param("CDPM", "", TOKEN, 401, "bad-token", id="empty-token"),
    pytest.param("CDPM", "wrong", TOKEN, 401, "bad-token", id="wrong-token"),
    pytest.param(None, TOKEN, TOKEN, 400, "submitter-not-allowed",
                 id="missing-product"),
    pytest.param("", TOKEN, TOKEN, 400, "submitter-not-allowed",
                 id="empty-product"),
    pytest.param("UNKNOWN", TOKEN, TOKEN, 400, "submitter-not-allowed",
                 id="unknown-product"),
    pytest.param("UNKNOWN", "wrong", TOKEN, 401, "bad-token",
                 id="token-before-product"),
    pytest.param("COSMOS", TOKEN, TOKEN, 400, "bad-form",
                 id="body-cannot-impersonate-other-product"),
    pytest.param(None, None, TOKEN, 400, "submitter-not-allowed",
                 id="no-machine-headers-screen-rejects-product-body"),
    pytest.param("CDPM", TOKEN, None, 403, "machine-channel-off",
                 id="disabled-channel"),
    pytest.param("UNKNOWN", "wrong", None, 403, "machine-channel-off",
                 id="disabled-before-token-and-product"),
])
def test_registered_handler_refuses_machine_identity_without_storage_changes(
        submission, product, token, configured_token, status, code):
    app, endpoint = _app(submission, machine_token=configured_token)
    before = _storage(app)
    with pytest.raises(ServiceError) as caught:
        endpoint(body=copy.deepcopy(submission.doc),
                 request=_request(app, product, token))
    error = caught.value
    assert (error.status, error.code) == (status, code)
    assert error.words and error.detail()["code"] == code
    # This checks the real exception, not a fabricated middleware response.
    assert _storage(app) == before
    assert app.state.journal.read() == []
    assert request_queue.get(app.state.store.catalog().requests(),
                             submission.doc["request_id"]) is None


@pytest.mark.parametrize("actor,read_only", [
    pytest.param("owner", True, id="owner-read-only"),
    pytest.param("analyst", True, id="analyst-read-only"),
    pytest.param(None, False, id="no-role-implies-view"),
])
def test_registered_handler_valid_machine_identity_cannot_bypass_view_role(
        submission, actor, read_only):
    app, endpoint = _app(submission, actor=actor, read_only=read_only)
    assert not identity.can(app.state.settings, "request-submit")
    assert app.state.settings.machine_channel_on()
    before = _storage(app)
    with pytest.raises(ServiceError) as caught:
        endpoint(body=copy.deepcopy(submission.doc),
                 request=_request(app, "CDPM", TOKEN))
    error = caught.value
    assert (error.status, error.code) == (403, "read-only")
    assert error.words == identity.READ_ONLY_WORDS
    assert _storage(app) == before
    assert not app.state.settings.work_dir.exists()
    assert not app.state.settings.journal_path.exists()


@pytest.mark.parametrize("actor", ["owner", "analyst"])
def test_no_machine_headers_is_screen_identity_even_when_channel_is_off(
        submission, actor):
    app, endpoint = _app(submission, actor=actor, machine_token=None)
    sent = copy.deepcopy(submission.doc)
    del sent["submitted_by"]
    # A screen form is submitted as the configured role, never as a product.
    body = _receipt(endpoint(body=sent, request=_request(app)), sent, 201)
    assert body["submitted_by"] == app.state.settings.actor
    assert body["request"]["submitted_by"] == app.state.settings.actor
    assert app.state.journal.read()[0]["actor"] == app.state.settings.actor
