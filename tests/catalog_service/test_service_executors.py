# -*- coding: utf-8 -*-
"""T9: «Исполнители по продуктам» — таблица, свежесть, итоги.

Главное: служба не считает состояние клетки сама. Здесь доказывается
равенство трёх ответов на один вопрос — службы, читателя
(`template_catalog.bindings.best_states`) и двери COSMOS
(`vmp.catalog_access.bindings_states`) — на ОДНИХ И ТЕХ ЖЕ файлах.
Тест вправе импортировать `vmp`: он сверяет службу с дверью.
"""
from __future__ import annotations

import json

from template_catalog import bindings as tb
from tests.catalog_service.conftest import sample_family


def _cells(table: dict) -> dict:
    return {(r["section"], r["id"], kind): cell["best"]
            for r in table["rows"] for kind, cell in r["cells"].items()}


def test_service_reader_and_door_agree_on_every_cell(make_service, tmp_path,
                                                     monkeypatch):
    from vmp import catalog_access as ca
    from tests.catalog_copies import copy_catalog, use_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    table = svc.get("/api/executors").json()
    docs = [tb.load_bindings(folder / "vmp-bindings.json"),
            tb.load_bindings(svc.settings.cdpm_bindings)]
    reader = tb.best_states(cat, *docs)
    mine = _cells(table)
    assert mine, "таблица исполнителей пуста"
    for (section, rid, kind), best in mine.items():
        by = reader.get(section, {}).get(rid, {}).get(kind, {})
        expected = _best(by.values())
        assert best == expected, f"{section}/{rid}[{kind}]"
    use_catalog(monkeypatch, folder)
    ca.forget()
    try:
        cat_door = ca.current()
        чужие, слово, почему = ca.cdpm_bindings_state(cat_door)
        door = ca.bindings_states(cat_door)
    finally:
        ca.forget()
    # Дверь читает ЧУЖОЙ файл сама и вправе его не принять. На ветке с
    # кандидатом копия — «живой ⊕ кандидаты дерева» (0.3 проекта
    # паритета), а файл CDPM описывает ЖИВОЙ каталог: читатель отвергает
    # его словами «одна версия каталога — разные данные», и чужих клеток
    # у двери законно нет. Тогда сверяются клетки продуктов, чьи файлы
    # дверь прочитала, а причина печатается: молчаливого пропуска здесь
    # нет. На чистом дереве кандидатов нет, дверь читает оба файла, и
    # сверка идёт по всем клеткам, как прежде.
    без: set = set()
    if чужие is None:
        без = {str(docs[1].get("product") or "?")}
        print(f"дверь не приняла чужие привязки ({слово}): {почему} — "
              f"клетки продукта {', '.join(sorted(без))} не сверяются")
    for (section, rid, kind), best in mine.items():
        by = door.get(section, {}).get(rid, {}).get(kind, {})
        if by:
            ждём = best if not без else _best(
                [s for p, s in reader.get(section, {}).get(rid, {})
                 .get(kind, {}).items() if p not in без])
            assert ждём == _best(by.values()), \
                f"служба и дверь разошлись на {section}/{rid}[{kind}]"
    print(f"клеток сверено со читателем и дверью: {len(mine)}")


def test_two_rows_of_one_product_give_the_best_state(make_service, tmp_path):
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path)
    path = folder / "vmp-bindings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    row = next(r for r in doc["bindings"]
               if any(e.get("state") == "есть" for e in r["executors"]))
    good = next(e for e in row["executors"] if e.get("state") == "есть")
    row["executors"].append({"kind": good["kind"], "state": "нет",
                             "lang": None, "file": None, "symbol": None,
                             "note": "ручка-синоним: исполнителя нет",
                             "cr_key": None})
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    svc = make_service("owner", folder=folder)
    table = svc.get("/api/executors").json()
    cell = _cells(table)[(row["section"], row["id"], good["kind"])]
    assert cell == "есть", "строка «нет» последней в файле спрятала «есть»"


def test_missing_cdpm_file_is_a_state_not_a_refusal(make_service):
    svc = make_service("owner", cdpm_bindings=False)
    table = svc.get("/api/executors").json()
    cdpm = next(p for p in table["products"] if p["product"] == "CDPM")
    assert cdpm["found"] is False
    assert cdpm["freshness"] == "missing"
    assert cdpm["freshness_words"] == "не объявлены"
    cosmos = next(p for p in table["products"] if p["product"] == "COSMOS")
    assert cosmos["found"] is True and cosmos["freshness"] == "match"
    assert cosmos["freshness_words"] == "сошлось"
    assert cosmos["catalog_version"] == svc.catalog().version()
    print("продуктов в шапке экрана: 2 (COSMOS найден, CDPM не объявлен)")


def test_freshness_words_for_stale_draft_and_stop(make_service, tmp_path):
    """Состояние «черновик отстал» бывает только у ЧЕРНОВИКА: у
    выпуска одна версия с другими данными — остановка сторожа. Копия
    поэтому берёт черновой номер (живой каталог с 08.09 — выпуск)."""
    from tests.catalog_copies import copy_catalog
    from tests.catalog_service.conftest import draft_version
    folder = copy_catalog(tmp_path, version=draft_version())
    path = folder / "vmp-bindings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["catalog_fingerprint"] = "0" * 64          # черновик, отпечаток отстал
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    svc = make_service("owner", folder=folder)
    stale = next(p for p in svc.get("/api/executors").json()["products"]
                 if p["product"] == "COSMOS")
    assert stale["freshness"] == "stale-draft"
    assert stale["freshness_words"] == "черновик отстал"
    assert any(p["code"] == "B01" for p in stale["problems"])

    doc["catalog_version"] = "0.1.0"               # выпуск другой средней
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    svc2 = make_service("owner", folder=folder, work="work-2")
    stop = next(p for p in svc2.get("/api/executors").json()["products"]
                if p["product"] == "COSMOS")
    assert stop["freshness"] == "stop"
    assert stop["freshness_words"] == "остановка сторожа"
    print("состояний свежести проверено: 4 (сошлось, черновик отстал, "
          "остановка сторожа, не объявлены)")


def test_declared_executor_from_a_request_appears_under_the_submitter(
        make_service, tmp_path):
    from template_catalog import writer
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    rec = sample_family(cat)
    kind = rec["executor_kinds"][0]
    writer.write_requests(folder, [{
        "request_id": "req-0003", "state": "исполнитель заявлен",
        "section": "families", "record_id": rec["id"],
        "submitted_at": "2026-09-01T09:00:00+03:00", "submitted_by": "CDPM",
        "source": {"kind": "вручную", "document_class": None,
                   "run_class": None, "ref": None},
        "draft": None, "executor_kinds_requested": [kind],
        "decision": {"by": "владелец", "at": "2026-09-02T10:00:00+03:00",
                     "words": "принято"},
        "cr_key": "cr1a2b3c4d"}])
    svc.store.forget()
    table = svc.get("/api/executors").json()
    row = next(r for r in table["rows"] if r["id"] == rec["id"])
    assert "CDPM" in row["cells"][kind]["by"]
    assert row["cells"][kind]["by"]["CDPM"]["state"] in ("заявлен", "есть")


def test_totals_equal_a_recount_and_filters_narrow_the_table(service):
    table = service.get("/api/executors").json()
    totals = table["totals"]
    recount = {"cells": 0, "есть": 0, "заявлен": 0, "нет": 0,
               "не объявлено": 0}
    for row in table["rows"]:
        for cell in row["cells"].values():
            recount["cells"] += 1
            word = cell["best"] if cell["best"] in ("есть", "заявлен", "нет") \
                else "не объявлено"
            recount[word] += 1
    assert totals == recount
    assert totals["cells"] == sum(totals[k] for k in
                                  ("есть", "заявлен", "нет", "не объявлено"))
    one = service.get("/api/executors?product=COSMOS&state=есть").json()
    assert one["totals"]["есть"] == one["totals"]["cells"] > 0
    assert one["totals"]["cells"] <= totals["cells"]
    assert service.get("/api/executors?product=НЕТ").status_code == 400
    assert service.get("/api/executors?state=ага").status_code == 400
    print(f"клеток в таблице: {totals['cells']}; «есть» {totals['есть']}, "
          f"«нет» {totals['нет']}, не объявлено {totals['не объявлено']}")


def _best(states) -> str | None:
    rank = tb.STATE_RANK          # ранг — таблица читателя, копий нет
    best, best_rank = None, -1
    for s in states:
        r = rank.get(str(s), -1)
        if r > best_rank:
            best, best_rank = str(s), r
    return best


def test_completeness_is_judged_by_zones_of_all_files(make_service, tmp_path):
    """Полнота по видам (B04) судится ЗОНАМИ ВСЕХ файлов привязок сразу —
    правило шага 4 (§2.1 п.2 его проекта). Счёт по одному файлу объявлял
    бы чужую зону несуществующей: у CDPM зона — `check_template`, и все
    прочие виды становились бы «ни в чьей зоне».

    Замечания раскладываются по адресу: `bindings[<продукт>]…` — в
    карточку продукта, остальные — в общий перечень экрана."""
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    table = svc.get("/api/executors").json()
    docs = [tb.load_bindings(folder / "vmp-bindings.json"),
            tb.load_bindings(svc.settings.cdpm_bindings)]
    поодиночке = [p for p in tb.check_bindings(cat, docs[1])
                  if p.code == "B04"]
    вместе = [p for p in tb.check_bindings(cat, *docs) if p.code == "B04"]
    assert поодиночке, "у чужого файла нет ни одного B04 — проба бессмысленна"
    assert len(вместе) < len(поодиночке), \
        "счёт по обоим файлам обязан снимать ложные B04 чужой зоны"
    без_зоны = [p for p in table["problems"]
                if "ни в чьей зоне" in p["message"]]
    assert not без_зоны, f"ложные B04 остались: {без_зоны[:2]}"
    for card in table["products"]:
        чужие = [p for p in card["problems"]
                 if not p["where"].startswith(f"bindings[{card['product']}]")]
        assert not чужие, f"в карточке {card['product']} чужие адреса: {чужие}"
    ожидание = [p for p in tb.check_bindings(cat, *docs)
                if not str(p.where).startswith("bindings[")]
    assert len(table["problems"]) == len(ожидание)
    узкий = svc.get("/api/executors?product=CDPM").json()
    assert len(узкий["problems"]) == len(table["problems"]), \
        "фильтр экрана изменил суждение о полноте"
    print(f"B04 по одному файлу CDPM: {len(поодиночке)}, по обоим: "
          f"{len(вместе)}; общих замечаний экрана: {len(table['problems'])}")
