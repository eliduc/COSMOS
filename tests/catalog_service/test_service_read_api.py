# -*- coding: utf-8 -*-
"""T3: ручки чтения — списки, карточка, словари, показ версии.

Ручка чтения обязана отвечать ровно то же, что публичный интерфейс
читателя: служба ничего не разбирает сама. Ожидания берутся из КОПИИ
каталога, а не литералами.
"""
from __future__ import annotations

from pathlib import Path

from tests.catalog_service.conftest import detail, make_release


def test_records_equal_reader_families_and_criteria(service):
    cat = service.catalog()
    fams = service.get("/api/records?section=families").json()
    assert fams["total"] == len(cat.families_full())
    assert [i["id"] for i in fams["items"]] == \
        [str(r.get("id")) for r in cat.families_full()]
    crits = service.get("/api/records?section=criteria").json()
    assert crits["total"] == len(cat.criteria())
    assert {i["id"] for i in crits["items"]} == set(cat.criteria())
    print(f"записей сверено с читателем: {fams['total'] + crits['total']}")


def test_status_filter_shows_withdrawn_record(make_service, tmp_path):
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path,
                          families={"probe_snyata": {"status": "снята"}})
    svc = make_service("owner", folder=folder)
    есть = svc.get("/api/records?section=families&status=есть").json()
    снята = svc.get("/api/records?section=families&status=снята").json()
    all_ = svc.get("/api/records?section=families&status=all").json()
    assert "probe_snyata" not in {i["id"] for i in есть["items"]}
    assert [i["id"] for i in снята["items"]] == ["probe_snyata"]
    assert all_["total"] == есть["total"] + снята["total"]
    print(f"статусов проверено: 3 (есть {есть['total']}, снята "
          f"{снята['total']}, все {all_['total']})")


def test_every_filter_drops_at_least_one_record(service):
    cat = service.catalog()
    base = service.get("/api/records?section=families&status=all").json()
    total = base["total"]
    dropped = 0
    for name, key in (("kind", "kinds"), ("zone", "zones"),
                      ("class", "classes")):
        for value in cat.dictionaries().get(key) or []:
            got = service.get(
                f"/api/records?section=families&status=all&{name}={value}"
            ).json()
            if got["total"] < total:
                dropped += 1
                break
        else:                                          # pragma: no cover
            raise AssertionError(f"фильтр {name} не отсеял ни одной записи")
    assert dropped == 3
    q = service.get("/api/records?section=families&q=duty").json()
    assert 0 < q["total"] < total
    # Состояние исполнителя в адресе — через дефис; фильтр судит ХУДШЕЕ
    # состояние записи, поэтому суммы по состояниям дают все записи.
    by_state = {}
    for word in ("есть", "заявлен", "нет", "не-объявлено"):
        by_state[word] = service.get(
            f"/api/records?section=families&status=all&state={word}"
        ).json()["total"]
    assert sum(by_state.values()) == total, by_state
    assert service.get("/api/records?section=families&state=ага"
                       ).status_code == 400
    print(f"фильтров проверено: 5 (вид, зона, класс, поиск, состояние "
          f"исполнителя) на {total} записях")


def test_bad_filter_value_is_refused_with_the_dictionary(service):
    r = service.get("/api/records?section=families&zone=НЕБО")
    assert r.status_code == 400
    words = detail(r)
    assert words["code"] == "bad-filter"
    for zone in service.catalog().dictionaries()["zones"]:
        assert zone in words["words"]
    assert service.get("/api/records?section=людишки").status_code == 400
    assert service.get("/api/records?section=families&state=ага"
                       ).status_code == 400


def test_requests_mix_drafts_of_families_and_criteria(make_service, tmp_path):
    """`requests=1` подмешивает черновики открытых заявок как «заявлен» —
    и для семей (умеет читатель), и для критериев (у `criteria()` такого
    параметра нет, служба подмешивает тем же законом)."""
    from template_catalog import writer

    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    fam_draft = _family_draft(cat, "probe_zayavka_semya")
    crit_draft = _criterion_draft(cat, "probe_zayavka_kriterij")
    requests = [_request("req-0001", "families", fam_draft),
                _request("req-0002", "criteria", crit_draft)]
    writer.write_requests(folder, requests)
    svc.store.forget()
    fams = svc.get("/api/records?section=families&requests=1").json()
    crits = svc.get("/api/records?section=criteria&requests=1").json()
    fam_row = next(i for i in fams["items"] if i["id"] == "probe_zayavka_semya")
    crit_row = next(i for i in crits["items"]
                    if i["id"] == "probe_zayavka_kriterij")
    assert fam_row["status"] == "заявлен" and fam_row["request_id"] == "req-0001"
    assert crit_row["status"] == "заявлен" and crit_row["request_id"] == "req-0002"
    without = svc.get("/api/records?section=criteria").json()
    assert "probe_zayavka_kriterij" not in {i["id"] for i in without["items"]}
    print("черновиков заявок подмешано: 2 (семья, критерий)")


def test_unknown_record_names_the_known_ones(service):
    r = service.get("/api/records/families/нетакой")
    assert r.status_code == 404
    words = detail(r)
    assert words["code"] == "unknown-record"
    known = [str(x) for x in service.catalog().families()]
    assert all(k in words["words"] for k in known[:3])


def test_record_card_carries_draft_executors_stand_and_problems(service):
    cat = service.catalog()
    rid = str(next(iter(cat.families())))
    body = service.get(f"/api/records/families/{rid}").json()
    assert body["record"]["id"] == rid
    assert body["draft"] is None and body["changes"] == []
    assert set(body["shown"]) == {"version", "fingerprint"}
    assert body["shown"]["version"] == cat.version()
    assert body["pinned_words"] is None
    declared = set(cat.family(rid).get("executor_kinds") or [])
    assert set(body["executors"]) <= declared
    assert set(body["stand"]) >= {"passed", "failed", "skipped", "rows"}
    print(f"полей карточки проверено: 8 (запись {rid})")


def test_version_view_reads_release_folder_inside_tmp(service):
    cat = service.catalog()
    rid = str(next(iter(cat.families())))
    make_release(service.folder, "0.1.0")
    body = service.get(f"/api/records/families/{rid}?version=0.1.0"
                       "&fingerprint=" + "0" * 64).json()
    assert body["shown"]["version"] == "0.1.0"
    assert body["pinned_words"] and "0.1.0" in body["pinned_words"]
    assert Path(service.folder, "releases", "0.1.0").is_dir()
    assert Path(service.folder, "releases", "0.1.0").is_relative_to(
        service.folder)
    missing = service.get(f"/api/records/families/{rid}?version=0.9.0")
    assert missing.status_code == 404
    assert detail(missing)["code"] == "unknown-version"
    assert "releases/0.9.0" in detail(missing)["words"]


def test_version_parameter_is_judged_by_the_pattern_before_the_path(service):
    """Номер версии из адреса в путь `releases/<строка>` как есть не
    подставляется: `..` открыл бы саму папку каталога «версией»."""
    cat = service.catalog()
    rid = str(next(iter(cat.families())))
    for bad in ("..", "не-версия", "0.1"):
        r = service.get(f"/api/records/families/{rid}?version={bad}")
        assert r.status_code == 404, bad
        words = detail(r)
        assert words["code"] == "unknown-version", bad
        assert "X.Y.Z" in words["words"], words["words"]
    card = service.get("/api/versions/не-версия")
    assert card.status_code == 404
    assert detail(card)["code"] == "unknown-version"
    from catalog_service import releases
    assert releases.release_folder(service.folder, "..") is None
    assert releases.release_folder(service.folder, "0.2.0") is not None
    print("нечисловых номеров версии отвергнуто: 4 (карточка записи и "
          "карточка версии)")


def test_version_card_answers_the_form_of_the_contract(service):
    """Карточка версии — `{version, entry, header, changes, loadable,
    is_current}`: шапка выпуска лежит ПОД ключом `header`, и экран берёт
    отпечаток, дату, автора и заметку оттуда."""
    from tests.catalog_service.conftest import add_release
    entry = add_release(service.folder, "0.3.0")
    service.store.forget()
    body = service.get("/api/versions/0.3.0").json()
    assert set(body) == {"version", "entry", "header", "changes", "loadable",
                         "is_current"}
    assert body["is_current"] is False and body["loadable"] is True
    assert body["header"]["fingerprint"] == entry["fingerprint"]
    for field in ("released_at", "released_by", "note", "version"):
        assert body["header"].get(field), field
    assert body["entry"]["code"] == entry["code"]
    print(f"полей карточки версии проверено: {len(body)} "
          f"(шапка под ключом header)")


def test_card_of_the_current_version_opens_before_the_first_release(service):
    """Ссылка паспорта CDPM ведёт на `#/versions/<версия штампа>`, а до
    первого выпуска штамп несёт ЧЕРНОВИК: отказ «версии нет: текущая —
    она же» был бы неправдой о самом себе."""
    cat = service.catalog()
    assert cat.is_draft() and not cat.versions()
    body = service.get(f"/api/versions/{cat.version()}").json()
    assert body["is_current"] is True and body["loadable"] is True
    assert body["entry"] is None
    assert body["header"]["fingerprint"] == cat.fingerprint()
    assert body["header"]["is_draft"] is True
    assert isinstance(body["changes"], list) and body["changes"], \
        "у текущего черновика без базы дифф — весь каталог"
    missing = service.get("/api/versions/0.9.0")
    assert missing.status_code == 404
    print(f"карточка текущей версии {cat.version()}: изменений "
          f"{len(body['changes'])}")


def test_dictionaries_equal_reader_and_carry_usages(service):
    cat = service.catalog()
    listing = service.get("/api/dictionaries").json()
    keys = {k["key"] for k in listing["keys"]}
    expected = {k for k in cat.dictionaries() if k != "area_param_names"}
    assert keys == expected
    assert listing["derived"] == ["area_param_names"]
    kinds = {k["key"]: k["kind"] for k in listing["keys"]}
    assert kinds["lexicon_order"] == "order"
    assert kinds["acceptance_default"] == "text"
    # 0.5.0: единицы нарушения — карта «код → слово» (объект), перечни —
    # словари слов (history_read_kinds); вид служба судит по значению.
    assert kinds["violation_units"] == "object"
    assert kinds["history_read_kinds"] == "list"
    assert kinds["check_templates"] == "object"
    one = service.get("/api/dictionaries/violation_units").json()
    assert one["value"] == cat.dictionaries()["violation_units"]
    assert one["usages"], "единицы нарушения обязаны где-то использоваться"
    assert all(u["section"] in ("families", "criteria") for u in one["usages"])
    assert service.get("/api/dictionaries/нетакого").status_code == 404
    print(f"ключей словарей сверено с читателем: {len(keys)}")


def test_catalog_and_validate_handles(service):
    cat = service.catalog()
    head = service.get("/api/catalog").json()
    assert head["version"] == cat.version()
    assert head["counts"]["families"] == len(cat.families_full(status=None))
    assert head["counts"]["dictionaries_keys"] == \
        len([k for k in cat.dictionaries() if k != "area_param_names"])
    assert head["problems"]["errors"] == 0
    assert head["releases_ok"] is True
    v = service.get("/api/validate").json()
    assert v["errors"] == 0 and isinstance(v["problems"], list)
    assert v["releases"] == []          # выпусков в копии ещё нет
    session = service.get("/api/session").json()
    assert session["mode"] == "edit" and session["actor"] == "владелец"
    assert session["catalog"]["dir_name"] == service.folder.name
    print("ручек состояния проверено: 3 (/api/catalog, /api/validate, "
          "/api/session)")


# ─────────────────────────────────────────────────────── помощники

def _family_draft(cat, fid: str) -> dict:
    rec = cat.family(str(next(iter(cat.families()))))
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update({"id": fid, "params": [], "param_sets": [], "conditions": [],
                  "complements": [], "subject_words": None,
                  "rule_form": None})
    return draft


def _criterion_draft(cat, cid: str) -> dict:
    rec = next(iter(cat.criteria().values()))
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft["id"] = cid
    return draft


def _request(rid: str, section: str, draft: dict) -> dict:
    return {"request_id": rid, "state": "подана", "section": section,
            "record_id": draft["id"],
            "submitted_at": "2026-09-08T10:00:00+03:00",
            "submitted_by": "аналитик",
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None},
            "draft": draft, "executor_kinds_requested": None,
            "decision": {"by": None, "at": None, "words": None},
            "cr_key": None}
