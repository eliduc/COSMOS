# -*- coding: utf-8 -*-
"""T8: выгрузка и загрузка каталога одним файлом.

Единый файл — способ передать каталог целиком (в письме, в срезе, между
машинами). Загрузка — действие владельца и всегда полная замена: она
судится теми же правилами, что и сборка, и приводит переходный носитель
порядка семей к загруженному составу (иначе дверь COSMOS не откроется).
"""
from __future__ import annotations

import json

import template_catalog as tc
from template_catalog import export as texport
from tests.catalog_service.conftest import detail, sample_family


def _single(cat) -> dict:
    return json.loads(texport.export_single(cat))


def _upload(svc, doc, **params):
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    return svc.post("/api/import", params=params,
                    files={"file": ("catalog.json", raw, "application/json")})


def test_export_equals_the_reader(service):
    cat = service.catalog()
    r = service.get("/api/export")
    assert r.status_code == 200
    assert r.text == texport.export_single(cat)
    name = f"template-catalog-{cat.version()}-{cat.fingerprint()[:12]}.json"
    assert name in r.headers["content-disposition"]
    print(f"выгружен файл {name}")


def test_roundtrip_keeps_the_fingerprint(service):
    cat = service.catalog()
    before = cat.fingerprint()
    r = _upload(service, _single(cat))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced"] is True and body["fingerprint"] == before
    assert service.catalog().fingerprint() == before


def test_file_without_requests_or_versions_is_refused(service):
    doc = _single(service.catalog())
    doc.pop("requests")
    r = _upload(service, doc)
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "invalid-single-file"
    assert "requests" in words["words"]


def test_foreign_fingerprint_is_named_by_its_own_code(service):
    doc = _single(service.catalog())
    doc["catalog"]["fingerprint"] = "0" * 64
    r = _upload(service, doc)
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "fingerprint-mismatch"
    assert any(p["code"] == "H02" for p in words["problems"])


def test_version_downgrade_needs_force(service):
    cat = service.catalog()
    doc = _single(cat)
    doc["catalog"]["version"] = "0.1.0-draft.1"
    r = _upload(service, doc)
    assert r.status_code == 409
    words = detail(r)
    assert words["code"] == "version-downgrade"
    assert "force=1" in words["words"]
    assert service.catalog().version() == cat.version()
    forced = _upload(service, doc, force=1)
    assert forced.status_code == 200, forced.text
    assert service.catalog().version() == "0.1.0-draft.1"


def test_import_of_a_smaller_catalog_fixes_the_families_order(make_service,
                                                              tmp_path):
    from vmp import catalog_access as ca
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path, families={"probe_lishnyaya": {}})
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    assert "probe_lishnyaya" in cat.families()
    doc = _single(cat)
    doc["data"]["families"] = [r for r in doc["data"]["families"]
                               if r["id"] != "probe_lishnyaya"]
    doc["catalog"]["fingerprint"] = tc.fingerprint_of(doc["data"])
    r = _upload(svc, doc)
    assert r.status_code == 200, r.text
    after = svc.catalog()
    assert "probe_lishnyaya" not in after.families()
    order = json.loads((folder / "transition" / "families_order.json")
                       .read_text(encoding="utf-8"))
    assert "probe_lishnyaya" not in order
    assert set(order) == set(after.families())
    ca.forget()
    try:
        assert set(ca.families_order(after)) == set(after.families())
    finally:
        ca.forget()
    print("порядок семей приведён к загруженному составу: "
          f"{len(order)} семей")


def test_import_is_refused_while_drafts_wait(service):
    cat = service.catalog()
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items() if k != "history"}
    draft["title"] = rec["title"] + " x"
    service.put(f"/api/drafts/families/{rec['id']}",
                json={"record": draft, "note": "проба"})
    r = _upload(service, _single(cat))
    assert r.status_code == 409 and detail(r)["code"] == "drafts-pending"


def test_analyst_may_not_import(make_service):
    svc = make_service("analyst")
    r = _upload(svc, _single(svc.catalog()))
    assert r.status_code == 403 and detail(r)["code"] == "owner-only"
    assert svc.get("/api/export").status_code == 200, \
        "выгрузка доступна всем — это чтение"
