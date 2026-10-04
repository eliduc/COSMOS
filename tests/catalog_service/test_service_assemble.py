# -*- coding: utf-8 -*-
"""T5: «Собрать черновик» — версия, история, производные инварианты.

Сборка — единственное место, где содержание каталога меняется. Поэтому
здесь судятся: формула версии, сходимость H02, пересчёт
`legacy_registry_rev`, пять полей строки истории, четыре производных
инварианта (по случаю на правило), детерминизм записи, предпроверка B01
и то, что отказ НЕ оставляет следов на диске.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from catalog_service import assemble as asm
from template_catalog import writer
from tests.catalog_service.conftest import (add_release, detail,
                                            folder_bytes, sample_family)


def _draft_of(cat, rec: dict, **changes) -> dict:
    out = {k: v for k, v in rec.items() if k != "history"}
    out.update(changes)
    return out


def _save(svc, rec: dict, note: str = "проба сборки"):
    return svc.put(f"/api/drafts/families/{rec['id']}",
                   json={"record": rec, "note": note})


def test_assemble_writes_version_history_and_fingerprint(service):
    cat = service.catalog()
    rec = sample_family(cat)
    before_version = cat.version()
    before_history = len(rec.get("history") or [])
    draft = _draft_of(cat, rec, title=rec["title"] + " (собрано службой)")
    assert _save(service, draft, "заголовок уточнён").status_code == 200
    r = service.post("/api/versions/assemble",
                     json={"note": "проба сборки службой"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1
    triple, n = before_version.split("-draft.")
    assert body["version"] == f"{triple}-draft.{int(n) + 1}", \
        "та же тройка — номер черновика растёт на единицу"
    # Цепочка после сборки (0.14 пакета паритета): `--refresh` первой,
    # стенд и привязки ОБЕИХ сторон (COSMOS — vmp, CDPM — cdpm; с
    # 09.09.2026 генераторы CDPM в той же цепочке, до гейта, который
    # читает оба файла привязок), `parity.py` последней.
    assert body["next"] == list(asm.NEXT_COMMANDS)
    assert body["next"][0] == asm.REFRESH_COMMAND
    assert body["next"][-1] == asm.PARITY_COMMAND
    middle = body["next"][1:-1]
    assert all(("vmp." in c) or ("cdpm" in c) for c in middle)
    cdpm_rows = [i for i, c in enumerate(middle) if "cdpm" in c and "vmp." not in c]
    gate = next(i for i, c in enumerate(middle) if "bindings_gate" in c)
    assert len(cdpm_rows) == 2 and max(cdpm_rows) < gate, \
        "стенд таблиц и привязки CDPM — до гейта привязок"
    after = service.catalog()
    assert after.version() == body["version"]
    assert after.fingerprint() == body["fingerprint"]
    assert after.declared_fingerprint() == after.fingerprint()   # H02
    assert not [p for p in after.validate() if p.severity == "error"]
    assert after.header["legacy_registry_rev"] == \
        writer.legacy_registry_rev(after.families())
    assert after.header["released_by"] == "владелец"
    assert after.header["note"] == "проба сборки службой"
    hist = after.family(rec["id"])["history"]
    assert len(hist) == before_history + 1
    line = hist[-1]
    assert set(line) == {"date", "kind", "by", "note", "ref"}
    assert line["kind"] == "изменение" and line["by"] == "владелец"
    assert line["note"] == "заголовок уточнён" and line["ref"] is None
    assert service.get("/api/drafts").json()["items"] == []
    assert not list((service.settings.drafts_dir / "families").glob("*.json"))
    print(f"собрана версия {body['version']}; строк истории добавлено: 1")


def test_version_rule_uses_the_floor_of_the_level(service):
    """Старшая правка поднимает СРЕДНЕЕ число (0.x), и номер черновика
    начинается заново."""
    add_release(service.folder, "0.2.0")
    service.store.forget()
    cat = service.catalog()
    rec = next(r for r in cat.families_full() if not r.get("complements"))
    draft = _draft_of(cat, rec, status="снята")
    assert _save(service, draft, "снятие записи").status_code == 200
    hint = service.get("/api/drafts").json()["suggested"]
    assert hint["level"] == "major" and hint["version"] == "0.3.0-draft.1"
    r = service.post("/api/versions/assemble", json={"note": "снятие записи"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "0.3.0-draft.1"
    print("случаев правила версии проверено: 2 (та же тройка → N+1; "
          "старшая правка → пол уровня и N=1)")


def test_explicit_version_must_be_newer(service):
    cat = service.catalog()
    rec = sample_family(cat)
    _save(service, _draft_of(cat, rec, title=rec["title"] + " x"))
    r = service.post("/api/versions/assemble",
                     json={"note": "проба", "version": cat.version()})
    assert r.status_code == 409
    words = detail(r)
    assert words["code"] == "version-not-newer"
    assert cat.version() in words["words"] and "предложено" in words["words"]
    low = service.post("/api/versions/assemble",
                       json={"note": "проба", "version": "0.2.0"})
    assert low.status_code == 409          # выпуск, а не черновик
    ok = service.post("/api/versions/assemble",
                      json={"note": "проба", "version": "0.4.0-draft.7"})
    assert ok.status_code == 200 and ok.json()["version"] == "0.4.0-draft.7"


def test_derived_lexicon_order_for_a_new_family(service):
    cat = service.catalog()
    template = sample_family(cat)
    new = _draft_of(cat, template, id="probe_novaya",
                    title="Пробная семья службы", params=[], param_sets=[],
                    conditions=[], complements=[], rule_form=None,
                    stand_tasks=[], subject_words=["пробное слово"],
                    status="есть")
    r = service.put("/api/drafts/families/probe_novaya",
                    json={"record": new, "note": "новая семья со словами"})
    assert r.status_code == 200, r.text
    got = service.post("/api/versions/assemble",
                       json={"note": "новая семья"})
    assert got.status_code == 200, got.text
    after = service.catalog()
    order = after.dictionaries()["lexicon_order"]
    assert order[-1] == "probe_novaya"
    assert set(order) == {str(r.get("id")) for r in after.families_full()
                          if isinstance(r.get("subject_words"), list)}
    hist = after.family("probe_novaya")["history"]
    assert len(hist) == 1 and hist[0]["kind"] == "создание"
    assert any("lexicon_order" in d for d in got.json()["derived"])


def test_derived_unbound_params_lose_the_name(service):
    cat = service.catalog()
    unbound = cat.dictionaries()["unbound_params"]
    name, spec = sorted(unbound.items())[0]
    rec = sample_family(cat)
    params = list(rec.get("params") or []) + [
        {"name": name, "unit": spec.get("unit"),
         "relation": spec.get("relation"), "qualifier": None,
         "note": "имя переехало из unbound_params"}]
    draft = _draft_of(cat, rec, params=params)
    r = service.put(f"/api/drafts/families/{rec['id']}",
                    json={"record": draft, "note": f"параметр {name}"})
    assert r.status_code == 200, r.text
    got = service.post("/api/versions/assemble", json={"note": "параметр"})
    assert got.status_code == 200, got.text
    after = service.catalog()
    assert name not in after.dictionaries()["unbound_params"]
    assert any("unbound_params" in d for d in got.json()["derived"])
    print(f"имя {name} ушло из unbound_params при сборке (D03)")


def test_withdrawing_a_family_keeps_the_door_open(service):
    """Снятие семьи: она уходит из порядка семей, лексикона и дополнений
    остальных — дверь COSMOS открывается на копии без правок руками."""
    from vmp import catalog_access as ca
    cat = service.catalog()
    rec = next(r for r in cat.families_full() if r.get("complements"))
    other = str(rec["complements"][0])
    draft = _draft_of(cat, rec, status="снята")
    assert _save(service, draft, "снятие записи").status_code == 200
    got = service.post("/api/versions/assemble", json={"note": "снятие"})
    assert got.status_code == 200, got.text
    after = service.catalog()
    assert after.family(rec["id"])["status"] == "снята"
    assert after.family(rec["id"])["complements"] == []
    assert rec["id"] not in after.family(other)["complements"]
    order = json.loads((service.folder / "transition"
                        / "families_order.json").read_text(encoding="utf-8"))
    assert rec["id"] not in order
    assert set(order) == set(after.families())
    hist = after.family(rec["id"])["history"][-1]
    assert hist["kind"] == "решение владельца"
    ca.forget()
    try:
        assert set(ca.families_order(after)) == set(after.families())
    finally:
        ca.forget()
    print(f"снята семья {rec['id']}; дверь COSMOS открылась на копии")


def test_nothing_to_assemble(service):
    r = service.post("/api/versions/assemble", json={"note": "нечего"})
    assert r.status_code == 409
    assert detail(r)["code"] == "nothing-to-assemble"
    cat = service.catalog()
    rec = sample_family(cat)
    assert _save(service, _draft_of(cat, rec), "черновик без правок"
                 ).status_code == 200
    again = service.post("/api/versions/assemble", json={"note": "нечего"})
    assert again.status_code == 409
    assert "не меняют канон" in detail(again)["words"]


def test_broken_draft_leaves_the_folder_untouched(service):
    """Рабочая папка правлена мимо службы — сборка отказывает, и на диск
    не ложится ничего: байты папки каталога те же."""
    cat = service.catalog()
    rec = sample_family(cat)
    path = service.settings.drafts_dir / "families" / f"{rec['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"form_version": 1, "section": "families", "id": rec["id"],
           "record": _draft_of(cat, rec, zone="НЕБО"),
           "note": "правка мимо службы", "by": "владелец",
           "saved_at": "2026-09-08T10:00:00+03:00", "request_id": None}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    before = folder_bytes(service.folder)
    r = service.post("/api/versions/assemble", json={"note": "проба"})
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "invalid-catalog"
    assert any(p["code"] == "D01" for p in words["problems"])
    assert folder_bytes(service.folder) == before


def test_two_assemblies_of_the_same_drafts_are_byte_identical(make_service,
                                                              tmp_path):
    from tests.catalog_copies import copy_catalog
    out = []
    for i in (1, 2):
        folder = copy_catalog(tmp_path, name=f"catalog-{i}")
        svc = make_service("owner", folder=folder, work=f"work-{i}")
        cat = svc.catalog()
        rec = sample_family(cat)
        svc.put(f"/api/drafts/families/{rec['id']}",
                json={"record": _draft_of(cat, rec,
                                          title=rec["title"] + " одинаково"),
                      "note": "одна и та же правка"})
        got = svc.post("/api/versions/assemble", json={"note": "одна и та же"})
        assert got.status_code == 200, got.text
        out.append({k: v for k, v in folder_bytes(folder).items()
                    if k.endswith(".json") and "/" not in k})
    assert out[0] == out[1], "сборка одного набора черновиков недетерминирована"
    for name, raw in out[0].items():
        assert b"\r\n" not in raw, f"{name}: перевод строки не LF"
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name}: BOM"
    print(f"файлов сверено байт в байт: {len(out[0])}")


def test_bindings_stop_is_named_before_writing(service):
    """B01 «стоп сторожа защищает выпуски»: после сборки дверь продукта
    остановилась бы — служба говорит это ДО записи."""
    path = service.folder / "vmp-bindings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["catalog_version"] = "0.1.0"          # выпуск, не черновик
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    cat = service.catalog()
    rec = sample_family(cat)
    _save(service, _draft_of(cat, rec, title=rec["title"] + " y"))
    before = folder_bytes(service.folder)
    r = service.post("/api/versions/assemble", json={"note": "проба"})
    assert r.status_code == 412
    words = detail(r)
    assert words["code"] == "bindings-stop"
    assert words["extra"]["product"] == "COSMOS"
    assert words["extra"]["catalog_version"] == "0.1.0"
    assert "перегенерируйте привязки" in words["words"]
    assert folder_bytes(service.folder) == before
    ok = service.post("/api/versions/assemble",
                      json={"note": "проба", "confirm_stop": True})
    assert ok.status_code == 200 and ok.json()["confirmed_stop"] is True


def test_parallel_write_is_refused_with_words(service, monkeypatch):
    import catalog_service.store as store_mod
    monkeypatch.setattr(store_mod, "WRITE_LOCK_TIMEOUT_S", 0.05)
    cat = service.catalog()
    rec = sample_family(cat)
    _save(service, _draft_of(cat, rec, title=rec["title"] + " z"))
    held = threading.Event()
    release = threading.Event()

    def hold():
        with service.store.writing():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        r = service.post("/api/versions/assemble", json={"note": "проба"})
        assert r.status_code == 409 and detail(r)["code"] == "write-busy"
    finally:
        release.set()
        t.join(5)


def test_temporary_files_are_not_json(service, monkeypatch):
    """Отметка кэша читателя судит `*.json` верхней папки: временный файл
    писателя обязан НЕ подходить под этот образец, иначе продукт увидел
    бы полузаписанный каталог."""
    seen: list = []
    orig = writer.replace_file

    def watching(src, dst):
        seen.append(Path(src).name)
        return orig(src, dst)
    monkeypatch.setattr(writer, "replace_file", watching)
    cat = service.catalog()
    rec = sample_family(cat)
    _save(service, _draft_of(cat, rec, title=rec["title"] + " w"))
    assert service.post("/api/versions/assemble",
                        json={"note": "проба"}).status_code == 200
    assert seen, "запись не прошла через замену файлов"
    assert not any(n.endswith(".json") for n in seen), seen
    assert not list(service.folder.glob("*.json.tmp-*"))
    print(f"временных имён проверено: {len(seen)}")


def test_explicit_version_below_the_floor_is_refused_as_not_newer(service):
    """§2.4: любой отказ ЯВНОЙ версии сборки — 409 `version-not-newer`.
    `version-too-low` — код выпуска, и его в сборке быть не должно:
    интерфейс и документы ветвятся по кодам."""
    add_release(service.folder, "0.2.0")
    service.store.forget()
    cat = service.catalog()
    rec = sample_family(cat)
    assert _save(service, _draft_of(cat, rec, title=rec["title"] + " y")
                 ).status_code == 200
    hint = service.get("/api/drafts").json()["suggested"]
    assert hint["version"].startswith("0.3.0"), hint
    low = service.post("/api/versions/assemble",
                       json={"note": "проба", "version": "0.2.5-draft.1"})
    assert low.status_code == 409, low.text
    words = detail(low)
    assert words["code"] == "version-not-newer"
    assert "0.3.0" in words["words"] and "предложено" in words["words"]
    print("отказ явной версии ниже пола: 409 version-not-newer")
