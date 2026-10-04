# -*- coding: utf-8 -*-
"""T7 и С2: «Выпустить версию» — двенадцать предпроверок и снимок.

Двенадцатую (паритет с кодом COSMOS) судит соседний файл
`test_service_parity.py`; здесь она зелена заглушкой обвязки.

Выпуск делает только владелец, и любая красная предпроверка означает,
что на диск не легло НИЧЕГО. Смысл поля `code`: дерево каталога и
читателя чисто, значит HEAD уже несёт тело выпуска; коммит самого
выпуска (шапка, журнал, папка) — следующий.
"""
from __future__ import annotations

import json
import re
import subprocess

import template_catalog as tc
from catalog_service import releases
from template_catalog import diff as tdiff
from template_catalog import requests as law
from template_catalog import writer
from tests.catalog_copies import BINDINGS_FILE
from tests.catalog_service.conftest import (MARKER, detail, folder_bytes,
                                            git_commit, sample_family)

HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _release(svc, version="0.2.0", note="первый выпуск службой", **kw):
    body = {"version": version, "note": note}
    body.update(kw)
    return svc.post("/api/versions/release", json=body)


def test_first_release_writes_journal_snapshot_and_code(service, git_repo):
    head = git_repo(service.folder)
    before = service.catalog()
    r = _release(service)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "0.2.0" and body["code"] == head
    assert HEX40.match(body["code"])
    assert body["release_dir"] == "releases/0.2.0"
    # Чек-лист после выпуска (0.14 пакета паритета): `--refresh` ПЕРВЫМ
    # (посев и дельта получают версию выпуска вычислением), коммит
    # выпуска — вторым, `parity.py` — последней командой.
    assert body["next"][0].startswith(releases.asm.REFRESH_COMMAND)
    assert body["next"][1].startswith("git add catalog")
    commands = [line for line in body["next"] if ".venv" in line]
    assert commands[-1].startswith(releases.asm.PARITY_COMMAND)
    after = service.catalog()
    assert after.version() == "0.2.0" and after.is_draft() is False
    assert after.header["released_by"] == "владелец"
    assert after.fingerprint() == before.fingerprint(), \
        "выпуск не меняет тела — только имя"
    assert not [p for p in after.validate() if p.severity == "error"]
    entry = after.versions()[-1]
    assert entry["version"] == "0.2.0" and entry["code"] == head
    assert entry["fingerprint"] == after.fingerprint()
    assert entry["released_by"] == "владелец"
    expected = tdiff.as_journal(tdiff.diff_bodies(None, after.body()))
    assert [(c["section"], c["id"], c["kind"]) for c in entry["changes"]] == \
        [(c["section"], c["id"], c["kind"]) for c in expected]
    assert {c["kind"] for c in entry["changes"]} == {"добавлена"}
    # §2.6 проекта: у ПЕРВОГО выпуска базы нет, и заметка каждой строки
    # говорит о происхождении снимка, а не о правке, которой не было.
    assert {c["note"] for c in entry["changes"]} == {releases.FIRST_RELEASE_NOTE}
    assert "инструментом извлечения" in releases.FIRST_RELEASE_NOTE
    snap = tc.load(service.folder / "releases" / "0.2.0")
    assert snap.version() == "0.2.0"
    assert snap.fingerprint() == after.fingerprint()
    assert snap.versions()[-1]["version"] == "0.2.0"
    assert not [p for p in snap.validate() if p.severity == "error"]
    assert service.get("/api/validate").json()["releases"] == [
        {"version": "0.2.0", "ok": True, "words": "сошлось"}]
    print(f"записей в журнале выпуска: {len(entry['changes'])}; "
          f"папка releases/0.2.0 прочитана")


def test_second_release_diffs_against_the_first(service, git_repo):
    git_repo(service.folder)
    assert _release(service).status_code == 200
    git_commit(service.folder, "каталог: выпуск 0.2.0")
    cat = service.catalog()
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items() if k != "history"}
    draft["title"] = rec["title"] + " (после выпуска)"
    assert service.put(f"/api/drafts/families/{rec['id']}",
                       json={"record": draft,
                             "note": "правка после выпуска"}).status_code == 200
    got = service.post("/api/versions/assemble", json={"note": "после выпуска"})
    assert got.status_code == 200, got.text
    # Строка истории — младшая правка (§2.8), поэтому средняя версия
    # растёт: 0.2.0 → 0.3.0-draft.1.
    assert got.json()["version"] == "0.3.0-draft.1", got.json()["version"]
    git_commit(service.folder, "каталог: собран черновик")
    # Привязки копии остались на прежней версии: в жизни их перегенерируют
    # цепочкой §5 тем же коммитом, здесь — осознанное продолжение.
    r = _release(service, version="0.3.0", note="второй выпуск",
                 confirm_stop=True)
    assert r.status_code == 200, r.text
    after = service.catalog()
    entry = after.versions()[-1]
    prev = tc.load(service.folder / "releases" / "0.2.0")
    assert entry["changes"] == tdiff.as_journal(
        tdiff.diff_bodies(prev.body(), after.body()))
    assert [c["kind"] for c in entry["changes"]] == ["изменена"]
    assert len(after.versions()) == 2
    print("выпусков сделано: 2; дифф второго считан от первого")


def test_drafts_pending_stops_the_release(service, git_repo):
    git_repo(service.folder)
    cat = service.catalog()
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items() if k != "history"}
    draft["title"] = rec["title"] + " x"
    service.put(f"/api/drafts/families/{rec['id']}",
                json={"record": draft, "note": "проба"})
    r = _release(service)
    assert r.status_code == 409
    words = detail(r)
    assert words["code"] == "drafts-pending"
    assert "записей 1" in words["words"] and "словарей 0" in words["words"]


def test_markers_are_a_fail_closed_precondition(make_service, git_repo):
    svc = make_service("owner", markers=False)
    git_repo(svc.folder)
    r = _release(svc)
    assert r.status_code == 412
    words = detail(r)
    assert words["code"] == "markers-missing"
    assert "обезличенности" in words["words"]
    assert not (svc.folder / "releases").exists()


def test_marker_inside_the_catalog_refuses_the_release(make_service, tmp_path,
                                                       git_repo):
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(
        tmp_path, families={"duty_limits": {"note": f"пример из {MARKER}"}})
    svc = make_service("owner", folder=folder)
    git_repo(folder)
    r = _release(svc)
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "invalid-catalog"
    assert any(p["code"] == "A02" and p["where"].startswith("families/")
               for p in words["problems"]), words["problems"]


def test_version_rules_of_the_release(service, git_repo):
    git_repo(service.folder)
    low = _release(service, version="0.1.1")
    assert low.status_code == 422
    assert detail(low)["code"] == "version-too-low"
    assert "0.2.0" in detail(low)["words"]
    draft = _release(service, version="0.3.0-draft.1")
    assert draft.status_code == 422
    assert "черновиком" in detail(draft)["words"]
    assert _release(service, version="0.2.0").status_code == 200
    again = _release(service, version="0.2.0", note="назад")
    assert again.status_code == 422
    assert "журнала" in detail(again)["words"]
    print("правил номера выпуска проверено: 3")


def test_git_is_required_and_must_be_clean(make_service, tmp_path, git_repo):
    bare = make_service("owner", name="без-git", work="work-1")
    r = _release(bare)
    assert r.status_code == 412 and detail(r)["code"] == "no-git"
    assert "git" in detail(r)["words"]

    svc = make_service("owner", name="с-git", work="work-2")
    git_repo(svc.folder)
    (svc.folder / "README.md").write_text("грязь\n", encoding="utf-8")
    dirty = _release(svc)
    assert dirty.status_code == 412
    words = detail(dirty)
    assert words["code"] == "git-dirty"
    assert "README.md" in words["words"]
    assert "закоммитьте" in words["words"]

    # Правка ОТСЛЕЖИВАЕМОГО файла: её строка порцелана начинается с
    # пробела (« M имя»), и имя обязано дойти до владельца целиком — по
    # этому перечню он решает, что коммитить.
    (svc.folder / "README.md").unlink()
    header = svc.folder / "catalog.json"
    header.write_text(header.read_text(encoding="utf-8") + "\n",
                      encoding="utf-8")
    changed = detail(_release(svc))
    assert changed["code"] == "git-dirty"
    assert f"{svc.folder.name}/catalog.json" in changed["words"], \
        f"имя изменённого файла пришло обрезанным: {changed['words']}"
    print("случаев git проверено: 3 (нет репозитория, посторонний файл, "
          "правка отслеживаемого — имя целиком)")


def test_bindings_stop_before_the_release(service, git_repo):
    path = service.folder / "vmp-bindings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["catalog_version"] = "0.1.0"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    git_repo(service.folder)
    before = folder_bytes(service.folder)
    r = _release(service)
    assert r.status_code == 412
    assert detail(r)["code"] == "bindings-stop"
    assert folder_bytes(service.folder) == before
    ok = _release(service, confirm_stop=True)
    assert ok.status_code == 200


def test_release_folder_is_never_overwritten(service, git_repo):
    git_repo(service.folder)
    assert _release(service).status_code == 200
    git_commit(service.folder, "каталог: выпуск")
    again = _release(service, version="0.2.0")
    assert again.status_code == 422        # версия не больше журнала
    assert detail(again)["code"] == "version-too-low"
    # Средняя версия впереди привязок — законная остановка сторожа;
    # осознанно продолжаем, чтобы дойти до восьмой предпроверки.
    (service.folder / "releases" / "0.3.0").mkdir(parents=True)
    r = _release(service, version="0.3.0", confirm_stop=True)
    assert r.status_code == 409 and detail(r)["code"] == "release-exists"


def test_tampered_release_folder_turns_validate_red(service, git_repo):
    git_repo(service.folder)
    assert _release(service).status_code == 200
    path = service.folder / "releases" / "0.2.0" / "families.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["families"][0]["title"] = doc["families"][0]["title"] + " подмена"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    got = service.get("/api/validate").json()
    assert got["releases"][0]["ok"] is False
    assert "отпечаток" in got["releases"][0]["words"]
    assert service.get("/api/catalog").json()["releases_ok"] is False


def test_analyst_may_not_release(make_service, git_repo):
    svc = make_service("analyst")
    git_repo(svc.folder)
    r = _release(svc)
    assert r.status_code == 403 and detail(r)["code"] == "owner-only"
    assert not (svc.folder / "releases").exists()


def test_dry_run_shows_all_checks_and_writes_nothing(service, git_repo):
    """Сухой прогон выпуска: двенадцать строк чек-листа словами, ни
    одного байта на диск. Диалог «Выпустить версию» (§4.5 проекта шага 5,
    §4 проекта шага 6, 0.13 проекта паритета) обязан показать владельцу
    маркеры, чистоту дерева git, остановку сторожа, кандидатов конвейера,
    полноту исполнителей и вердикт паритета ДО нажатия, а знает о них
    только служба."""
    git_repo(service.folder)
    before = folder_bytes(service.folder)
    got = service.get("/api/versions/release/checks").json()
    keys = [c["key"] for c in got["checks"]]
    assert keys == ["role", "drafts", "markers", "validate", "version",
                    "git", "bindings", "folder", "candidates", "executors",
                    "in_work", "parity"], keys
    assert [c["n"] for c in got["checks"]] == list(range(1, 13))
    assert got["requests_in_work"] == [] and got["warnings"] == []
    assert got["ok"] is True and got["confirm_stop_needed"] is False
    assert got["confirm_parity_needed"] is False
    assert got["version"] == got["suggested"]["version"]
    assert all(c["words"] and c["title"] for c in got["checks"]), \
        "строка чек-листа без слов"
    assert all(c["code"] is None for c in got["checks"])
    assert folder_bytes(service.folder) == before, \
        "сухой прогон тронул папку каталога"
    print(f"строк чек-листа выпуска: {len(got['checks'])}, все зелёные, "
          f"версия {got['version']}")


def test_dry_run_says_the_same_words_as_the_refusal(service, git_repo):
    """Красная строка чек-листа несёт тот же код и те же слова, что и
    отказ самого выпуска: закон один — те же функции проверок."""
    git_repo(service.folder)
    cat = service.catalog()
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items() if k != "history"}
    draft["title"] = rec["title"] + " (черновик)"
    assert service.put(f"/api/drafts/families/{rec['id']}",
                       json={"record": draft,
                             "note": "черновик перед выпуском"}
                       ).status_code == 200
    got = service.get("/api/versions/release/checks").json()
    row = next(c for c in got["checks"] if c["key"] == "drafts")
    assert got["ok"] is False and row["ok"] is False
    assert row["code"] == "drafts-pending"
    words = detail(_release(service))
    assert words["code"] == row["code"] and words["words"] == row["words"]
    # Дерево копии грязно черновиком? Нет: черновики живут вне пакета —
    # красной обязана быть ровно одна строка.
    red = [c["key"] for c in got["checks"] if not c["ok"]]
    assert red == ["drafts"], red
    print(f"красных строк чек-листа: {len(red)} ({red[0]}), слова совпали с "
          f"отказом выпуска")


def test_dry_run_is_read_and_works_without_the_owner(make_service, tmp_path):
    """Чек-лист — чтение: он открыт и в режиме просмотра, и роль честно
    названа первой же строкой (кнопка выпуска у неё неактивна)."""
    svc = make_service(None)
    got = svc.get("/api/versions/release/checks")
    assert got.status_code == 200, got.text
    row = next(c for c in got.json()["checks"] if c["key"] == "role")
    assert row["ok"] is False and row["code"] == "read-only"
    print(f"режим просмотра: чек-лист доступен, роль — {row['words'][:48]}…")


def test_reader_is_judged_from_the_repository_root(make_service, tmp_path):
    """Чистота дерева судится от КОРНЯ репозитория, а не от родителя
    папки каталога: каталог вправе лежать не прямо под корнем, и тогда
    читатель молча выпал бы из проверки — выпуск прошёл бы при грязном
    `template_catalog`."""
    from tests.catalog_copies import copy_catalog
    from tests.catalog_service.conftest import draft_version

    # копия — черновик сценария стендов (номер живого выпуска 0.3.0
    # с 09.09.2026 дал бы «выпуск не может быть младше»), очередь пуста
    folder = copy_catalog(tmp_path, name="repo/data/catalog",
                          version=draft_version(), requests=False)
    repo = tmp_path / "repo"
    (repo / "template_catalog").mkdir(parents=True, exist_ok=True)
    (repo / "template_catalog" / "probe.py").write_text("# проба\n",
                                                        encoding="utf-8")
    for args in (("init", "-q"),
                 ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "проба"),
                 ("add", "-A", "data"),
                 ("commit", "-q", "-m", "каталог: проба")):
        subprocess.run(["git", *args], cwd=str(repo), check=True,
                       capture_output=True, text=True, encoding="utf-8")
    svc = make_service("owner", folder=folder, work="work-root")
    from catalog_service import releases
    assert releases.repo_of(folder) == repo.resolve()
    assert releases.catalog_pathspec(folder) == "data/catalog"
    assert releases.unchecked_paths(folder) == []
    r = _release(svc)
    assert r.status_code == 412, r.text
    words = detail(r)
    assert words["code"] == "git-dirty"
    assert "template_catalog" in words["words"], words["words"]
    assert any("template_catalog" in name for name in words["extra"]["dirty"])
    print("корень дерева найден git'ом: читатель проверен вместе с каталогом")


def test_missing_reader_folder_is_named_with_words(service, git_repo):
    """`template_catalog` в дереве копии нет — это сказано словами, а не
    молча выпало из проверки."""
    git_repo(service.folder)
    from catalog_service import releases
    assert releases.unchecked_paths(service.folder) == ["template_catalog"]
    row = next(c for c in service.get("/api/versions/release/checks").json()
               ["checks"] if c["key"] == "git")
    assert row["ok"] is True
    assert "template_catalog" in row["words"] and "не проверял" in row["words"]
    print(f"строка git чек-листа: {row['words']}")


# ────────────── С2: предпроверки 9–11 конвейера пополнения (шаг 6)

def _present_request(svc, rec: dict, kind: str) -> str:
    """Заявка на вид у существующей записи, доведённая до «есть».

    Это конец пути заявки: запись в разделе есть, решение владельца
    записано, ключ CR назван. Из него и судятся предпроверки 9 и 10."""
    rid = law.request_id_of(law.kind_request_key("families", rec["id"],
                                                 [kind]))
    writer.write_requests(svc.folder, [{
        "request_id": rid, "state": "есть", "section": "families",
        "record_id": rec["id"], "submitted_at": "2026-09-01T09:00:00+03:00",
        "submitted_by": "COSMOS",
        "source": {"kind": "пробел при нажатии", "document_class": None,
                   "run_class": "прогон COSMOS",
                   "ref": "run-0031#33729a630c94"},
        "draft": None, "executor_kinds_requested": [kind],
        "decision": {"by": "владелец", "at": "2026-09-02T10:00:00+03:00",
                     "words": "исполнителя пишем"},
        "cr_key": law.cr_key_of(rid)}])
    svc.store.forget()
    return rid


def _put_candidate(svc, rec: dict, rid: str):
    record = {k: v for k, v in rec.items() if k != "history"}
    path = svc.folder / "candidates" / "families" / f"{rec['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"form_version": 1, "section": "families", "id": rec["id"],
         "record": record, "note": "исполнитель по CR",
         "by": "контур самоулучшения",
         "saved_at": "2026-09-02T18:00:00+03:00",
         "request_id": rid, "cr_key": law.cr_key_of(rid)},
        ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8")
    return path


def _strip_bindings(svc, record_id: str) -> int:
    """Убрать строки записи из обоих файлов привязок — так выглядит
    запись, у которой исполнитель не назван ни одним продуктом."""
    gone = 0
    for path in (svc.folder / BINDINGS_FILE, svc.settings.cdpm_bindings):
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = [r for r in doc.get("bindings") or []
                if str(r.get("id")) != record_id]
        gone += len(doc.get("bindings") or []) - len(rows)
        doc["bindings"] = rows
        path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                   indent=1) + "\n", encoding="utf-8")
    return gone


def test_a_left_candidate_stops_the_release(service, git_repo):
    """9: кандидат конвейера у записи выпуска — не импортирован либо не
    убран сборкой. Файл лежит в коммите ветки CR, дерево чисто — красной
    обязана быть ровно строка кандидатов."""
    git_repo(service.folder)
    cat = service.catalog()
    rec = sample_family(cat)
    rid = _present_request(service, rec, str(rec["executor_kinds"][0]))
    path = _put_candidate(service, rec, rid)
    git_commit(service.folder, "каталог: кандидат из ветки CR")
    got = service.get("/api/versions/release/checks").json()
    row = next(c for c in got["checks"] if c["key"] == "candidates")
    assert row["ok"] is False and row["code"] == "candidate-pending", got
    assert f"families/{rec['id']}" in row["words"]
    assert law.cr_key_of(rid) in row["words"]
    words = detail(_release(service))
    assert words["code"] == "candidate-pending"
    assert words["words"] == row["words"], "слова отказа и чек-листа разошлись"
    assert words["extra"]["candidates"][0]["file"] == \
        f"candidates/families/{rec['id']}.json"
    # Убрали файл — выпуск идёт.
    path.unlink()
    git_commit(service.folder, "каталог: кандидат убран сборкой")
    after = service.get("/api/versions/release/checks").json()
    assert next(c for c in after["checks"]
                if c["key"] == "candidates")["ok"] is True
    print("красных строк с кандидатом: 1; после удаления файла — 0")


def test_executors_incomplete_is_not_the_bindings_stop(service, git_repo):
    """10: вид записи из заявки «есть» без строки — свой код и свои слова.

    Сторож версий (7) при этом зелен: свежесть файлов привязок и полнота
    исполнителей — разные вопросы, и один код на двоих был бы неправдой."""
    cat = service.catalog()
    rec = sample_family(cat)
    kind = str(rec["executor_kinds"][0])
    rid = _present_request(service, rec, kind)
    gone = _strip_bindings(service, rec["id"])
    git_repo(service.folder)
    got = service.get("/api/versions/release/checks").json()
    row = next(c for c in got["checks"] if c["key"] == "executors")
    assert row["ok"] is False, got
    assert row["code"] == "executors-incomplete"
    assert kind in row["words"] and f"families/{rec['id']}" in row["words"]
    assert next(c for c in got["checks"] if c["key"] == "bindings")["ok"] \
        is True, "сторож версий не должен краснеть от полноты исполнителей"
    words = detail(_release(service))
    assert words["code"] == "executors-incomplete"
    assert any(kind in r["words"] for r in words["extra"]["rows"])
    assert service.catalog().requests()[0]["request_id"] == rid
    print(f"строк привязок убрано: {gone}; вид без строки: {kind}")


def test_a_claimed_row_with_a_cr_key_counts_as_named(service, git_repo):
    """«заявлен» с ключом CR по образцу — честный исход, а не пробел:
    работа названа и адресована. «заявлен» без ключа — не засчитывается."""
    cat = service.catalog()
    rec = sample_family(cat)
    kind = str(rec["executor_kinds"][0])
    rid = _present_request(service, rec, kind)
    _strip_bindings(service, rec["id"])
    path = service.folder / BINDINGS_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    # Остальные объявленные виды закрываются честным «нет» с заметкой:
    # предпроверка судит КАЖДЫЙ объявленный вид записи, а не один.
    others = [{"kind": str(k), "state": "нет", "lang": None, "file": None,
               "symbol": None, "cr_key": None,
               "note": "исполнителя этого вида у записи не будет",
               "fields": {}}
              for k in rec["executor_kinds"] if str(k) != kind]
    doc["bindings"] = list(doc.get("bindings") or []) + [{
        "section": "families", "id": rec["id"],
        "executors": [{"kind": kind, "state": "заявлен", "lang": "python",
                       "file": None, "symbol": None, "cr_key": None,
                       "note": "исполнителя пишет CR", "fields": {}}] + others,
        "stand": []}]
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    git_repo(service.folder)
    red = service.get("/api/versions/release/checks").json()
    assert next(c for c in red["checks"]
                if c["key"] == "executors")["ok"] is False, \
        "«заявлен» без ключа CR — обещание без адресата"
    doc["bindings"][-1]["executors"][0]["cr_key"] = law.cr_key_of(rid)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    green = service.get("/api/versions/release/checks").json()
    row = next(c for c in green["checks"] if c["key"] == "executors")
    assert row["ok"] is True, row
    print(f"«заявлен» без ключа — красно, с ключом {law.cr_key_of(rid)} — "
          f"зелено")


def test_requests_in_work_are_a_warning_and_do_not_stop_the_release(
        service, git_repo):
    """11: заявки «исполнитель заявлен» выпуск не трогает — но владелец
    обязан видеть, сколько их останется в работе."""
    git_repo(service.folder)
    cat = service.catalog()
    rec = sample_family(cat)
    kind = str(rec["executor_kinds"][0])
    rid = law.request_id_of(law.kind_request_key("families", rec["id"],
                                                 [kind]))
    writer.write_requests(service.folder, [{
        "request_id": rid, "state": "исполнитель заявлен",
        "section": "families", "record_id": rec["id"],
        "submitted_at": "2026-09-01T09:00:00+03:00", "submitted_by": "COSMOS",
        "source": {"kind": "пробел при нажатии", "document_class": None,
                   "run_class": "прогон COSMOS", "ref": "run-0031#33729a63"},
        "draft": None, "executor_kinds_requested": [kind],
        "decision": {"by": "владелец", "at": "2026-09-02T10:00:00+03:00",
                     "words": "исполнителя пишем"},
        "cr_key": law.cr_key_of(rid)}])
    service.store.forget()
    git_commit(service.folder, "каталог: заявка в работе")
    got = service.get("/api/versions/release/checks").json()
    row = next(c for c in got["checks"] if c["key"] == "in_work")
    assert row["ok"] is True, "предупреждение не отказ"
    assert "заявок в работе: 1" in row["words"] and rid in row["words"]
    assert got["ok"] is True and got["requests_in_work"] == [rid]
    assert got["warnings"] and "1" in got["warnings"][0]
    done = _release(service)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["requests_in_work"] == [rid]
    assert body["warnings"] and rid in body["warnings"][0]
    assert service.catalog().requests()[0]["state"] == "исполнитель заявлен", \
        "выпуск заявку в работе не трогает"
    print(f"заявок в работе при выпуске: 1 ({rid}); выпуск состоялся")
