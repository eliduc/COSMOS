# -*- coding: utf-8 -*-
"""T11: потребители каталога — что они видят и чего не видят.

Каталог читают продукты: COSMOS дверью `vmp.catalog_access`, CDPM мостом.
Правила слоя: сборка видна им БЕЗ перезапуска (отметка кэша читателя),
черновик рабочей папки не виден никому, а потребитель, закреплённый на
папке выпуска, не видит вообще ничего нового. И главное — живой
`catalog/` репозитория набор тестов службы не трогает.
"""
from __future__ import annotations

import json

import template_catalog as tc
from tests.catalog_service.conftest import (ROOT, bump_mtime,
                                            catalog_git_status,
                                            draft_version, folder_bytes,
                                            sample_family)


def _assemble(svc, note="проба потребителей"):
    cat = svc.catalog()
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items() if k != "history"}
    draft["title"] = rec["title"] + " (для потребителя)"
    assert svc.put(f"/api/drafts/families/{rec['id']}",
                   json={"record": draft, "note": note}).status_code == 200
    got = svc.post("/api/versions/assemble", json={"note": note})
    assert got.status_code == 200, got.text
    return got.json()


def test_assembly_is_visible_to_reader_and_door_without_restart(
        make_service, tmp_path, monkeypatch):
    from vmp import catalog_access as ca
    from tests.catalog_copies import copy_catalog, use_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    use_catalog(monkeypatch, folder)
    ca.forget()
    tc.forget_current()
    try:
        before_reader = tc.current(folder)
        before_door = ca.current()
        assert before_door.version() == before_reader.version()
        got = _assemble(svc)
        bump_mtime(folder / "catalog.json")
        after_reader = tc.current(folder)
        after_door = ca.current()
        assert after_reader is not before_reader, "экземпляр читателя тот же"
        assert after_reader.version() == got["version"]
        assert after_door.version() == got["version"]
        assert after_door.fingerprint() == got["fingerprint"]
    finally:
        ca.forget()
        tc.forget_current()
    print(f"потребителей увидели сборку без перезапуска: 2 "
          f"(читатель, дверь) — версия {got['version']}")


def test_a_draft_is_invisible_to_reader_and_door(make_service, tmp_path,
                                                 monkeypatch):
    from vmp import catalog_access as ca
    from tests.catalog_copies import copy_catalog, use_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    use_catalog(monkeypatch, folder)
    ca.forget()
    tc.forget_current()
    try:
        before = tc.current(folder)
        rec = sample_family(before)
        draft = {k: v for k, v in rec.items() if k != "history"}
        draft["title"] = rec["title"] + " (черновик)"
        assert svc.put(f"/api/drafts/families/{rec['id']}",
                       json={"record": draft,
                             "note": "черновик вне пакета"}).status_code == 200
        assert svc.settings.drafts_dir.is_dir()
        assert not svc.settings.work_dir.is_relative_to(folder), \
            "рабочая папка обязана лежать вне пакета каталога"
        after = tc.current(folder)
        assert after is before, "черновик изменил экземпляр читателя"
        assert after.family(rec["id"])["title"] == rec["title"]
        assert ca.current().family(rec["id"])["title"] == rec["title"]
    finally:
        ca.forget()
        tc.forget_current()


def test_consumer_pinned_to_a_release_sees_no_assemblies(make_service,
                                                         tmp_path,
                                                         monkeypatch,
                                                         git_repo):
    from tests.catalog_copies import copy_catalog
    # Копия — ЧЕРНОВИК той же тройки: служба выпускает её сама, и
    # стартовать с уже выпущенного номера значило бы проверять не
    # закрепление потребителя, а сторож версий привязок.
    folder = copy_catalog(tmp_path, version=draft_version())
    svc = make_service("owner", folder=folder)
    git_repo(folder)
    assert svc.post("/api/versions/release",
                    json={"version": "0.2.0",
                          "note": "выпуск для потребителя"}).status_code == 200
    release_dir = folder / "releases" / "0.2.0"
    pinned_before = tc.load(release_dir)
    got = _assemble(svc, "правка после выпуска")
    assert got["version"] != "0.2.0"
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(release_dir))
    tc.forget_current()
    try:
        pinned = tc.current(release_dir)
        assert pinned.version() == "0.2.0"
        assert pinned.fingerprint() == pinned_before.fingerprint()
    finally:
        tc.forget_current()
    assert tc.load(folder).version() == got["version"]
    print("закреплённый на releases/0.2.0 потребитель сборок не увидел")


def test_live_catalog_of_the_repository_is_untouched(live_catalog_bytes):
    """Страж правила: тесты службы работают только на tmp-копиях.

    Оба свидетеля СРАВНИТЕЛЬНЫЕ — «до прогона и после»: судится то, что
    сделали тесты, а не то, закоммичено ли дерево. Прежний git-свидетель
    требовал чистоты данных каталога и краснел, когда каталог законно
    перевыпущен инструментом и ещё не закоммичен, — это была неправда о
    предмете стража."""
    before, git_before = live_catalog_bytes["bytes"], live_catalog_bytes["git"]
    now = folder_bytes(ROOT / "catalog")
    assert set(now) == set(before), \
        "в живом каталоге появились или исчезли файлы"
    changed = [name for name, raw in now.items() if before[name] != raw]
    assert not changed, f"живой каталог изменён тестами: {changed}"
    git_now = catalog_git_status()
    assert git_now == git_before, (
        f"git видит по данным живого каталога иное, чем до прогона:\n"
        f"было:\n{git_before}\nстало:\n{git_now}")
    # Папки выпусков живой каталог несёт с 08.09 (выпуск 0.2.0) —
    # сравнивается их ПЕРЕЧЕНЬ до и после, а не «нет ни одной»: страж
    # судит то, что сделали тесты.
    releases_now = sorted(p.name for p in (ROOT / "catalog" / "releases")
                          .iterdir()) \
        if (ROOT / "catalog" / "releases").is_dir() else []
    assert releases_now == live_catalog_bytes["releases"], \
        "тесты службы тронули папки выпусков живого каталога"
    print(f"файлов живого каталога сверено: {len(now)}; строк git: "
          f"{len(git_now.splitlines())} (столько же, сколько до прогона)")
