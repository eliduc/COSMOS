# -*- coding: utf-8 -*-
"""T10: кто вправе — три роли на одиннадцати операциях записи.

Роль задаётся ПРИ ЗАПУСКЕ, входа и паролей у службы нет. Отказ всегда
словами: в просмотре — `read-only`, у аналитика на операции владельца —
`owner-only`. Кнопки интерфейс не прячет, а гасит с причиной в подсказке
— поэтому права отдаются и в `GET /api/session`.
"""
from __future__ import annotations

import json

import pytest

from catalog_service import identity
from tests.catalog_service.conftest import detail, sample_family

#: Одиннадцать операций записи контракта: (операция, вызов). Пересчёт
#: свидетельства поддержки (волна 17б, K06) — операция ЗАПИСИ: служба
#: кладёт файл `catalog/support.json`, и в режиме просмотра он не
#: пишется, как и всё остальное.
OPERATIONS = (
    ("draft-save", "put", "/api/drafts/families/{fid}", "record"),
    ("draft-status", "put", "/api/drafts/families/{fid}", "withdraw"),
    ("draft-discard", "delete", "/api/drafts/families/{fid}", None),
    ("request-submit", "post", "/api/requests", "request"),
    ("assemble", "post", "/api/versions/assemble", "note"),
    ("request-decide", "post", "/api/requests/req-0001/decide", "decision"),
    ("request-cr-key", "put", "/api/requests/req-0001/cr_key", "cr"),
    ("request-adopt", "post", "/api/requests/req-0001/adopt", "empty"),
    ("support-refresh", "post", "/api/support/refresh", "empty"),
    ("release", "post", "/api/versions/release", "release"),
    ("import", "post", "/api/import", "file"),
)


def _call(svc, op, method, url, body_kind):
    cat = svc.catalog()
    rec = sample_family(cat)
    url = url.format(fid=rec["id"])
    record = {k: v for k, v in rec.items() if k != "history"}
    bodies = {
        "record": {"record": dict(record, title=rec["title"] + " x"),
                   "note": "проба прав"},
        "withdraw": {"record": dict(record, status="снята"),
                     "note": "снятие"},
        "request": {"section": "families", "record_id": "probe_prava",
                    "source": {"kind": "вручную", "document_class": None,
                               "run_class": None, "ref": None},
                    "executor_kinds_requested": [rec["executor_kinds"][0]]},
        "note": {"note": "проба прав"},
        "decision": {"decision": "принята", "words": "проба"},
        "cr": {"cr_key": "cr1a2b3c4d"},
        "empty": {},
        "release": {"version": "0.9.0", "note": "проба прав"},
    }
    if body_kind == "file":
        from template_catalog import export as texport
        raw = texport.export_single(cat).encode("utf-8")
        return svc.post(url, files={"file": ("c.json", raw,
                                             "application/json")})
    if body_kind is None:
        return getattr(svc, method)(url)
    return getattr(svc, method)(url, json=bodies[body_kind])


def test_view_mode_refuses_every_write_with_the_same_words(make_service):
    svc = make_service(None)
    for op, method, url, body in OPERATIONS:
        r = _call(svc, op, method, url, body)
        assert r.status_code == 403, f"{op}: {r.status_code}"
        words = detail(r)
        assert words["code"] == "read-only", op
        assert words["words"] == identity.READ_ONLY_WORDS
    assert not svc.settings.work_dir.exists()
    print(f"операций записи отказано в просмотре: {len(OPERATIONS)}")


def test_analyst_gets_owner_only_where_the_owner_decides(make_service):
    svc = make_service("analyst")
    refused, allowed = [], []
    for op, method, url, body in OPERATIONS:
        r = _call(svc, op, method, url, body)
        if op in identity.OWNER_ONLY:
            assert r.status_code == 403, f"{op}: {r.status_code}"
            words = detail(r)
            assert words["code"] == "owner-only"
            assert "аналитик" in words["words"] and "владелец" in words["words"]
            refused.append(op)
        else:
            assert r.status_code != 403, f"{op}: {r.text}"
            allowed.append(op)
    assert set(refused) == set(identity.OWNER_ONLY)
    assert len(allowed) == len(OPERATIONS) - len(identity.OWNER_ONLY)
    print(f"аналитику доступно {len(allowed)} операций, отказано "
          f"{len(refused)}")


def test_owner_is_refused_by_no_role_check(make_service):
    svc = make_service("owner")
    for op, method, url, body in OPERATIONS:
        r = _call(svc, op, method, url, body)
        assert r.status_code != 403, f"{op}: {r.text}"
    print(f"владельцу доступны все {len(OPERATIONS)} операций записи")


def test_session_reports_role_mode_and_rights(make_service):
    for role, mode in (("owner", "edit"), ("analyst", "edit"), (None, "view")):
        svc = make_service(role, name=f"каталог-{role}", work=f"work-{role}")
        body = svc.get("/api/session").json()
        assert body["mode"] == mode
        assert body["read_only"] is (mode == "view")
        assert set(body["rights"]) == set(identity.OPERATIONS)
        for op, right in body["rights"].items():
            assert right["allowed"] == identity.can(svc.settings, op)
            assert (right["words"] is None) == right["allowed"]
    print(f"ролей проверено: 3; операций в карте прав: "
          f"{len(identity.OPERATIONS)}")


def test_read_only_key_wins_over_the_role(make_service, tmp_path):
    svc = make_service("owner", read_only=True)
    assert svc.get("/api/session").json()["mode"] == "view"
    assert svc.settings.actor == "владелец" and not svc.settings.is_owner()
    r = svc.post("/api/versions/assemble", json={"note": "проба"})
    assert r.status_code == 403 and detail(r)["code"] == "read-only"


def test_operations_table_is_closed(make_service):
    """Перечень операций закрыт: новая операция — правка контракта и
    таблицы прав, а не строка «если роль…» в обработчике."""
    assert set(op for op, *_ in OPERATIONS) == set(identity.OPERATIONS)
    svc = make_service("owner")
    with pytest.raises(KeyError):
        identity.can(svc.settings, "выдумка")
