# -*- coding: utf-8 -*-
"""С3: приём очередей заявок с площадок — `import-requests`.

Почтового ящика у службы нет (решение 0.12): на площадках она стоит в
режиме просмотра и не пишет ни байта, а заявки копятся в очередях
продуктов и переносятся копией папок. Инструмент — единственное, что
эти папки читает, и он НЕ пишет каталог сам: он ходит в службу тем же
машинным каналом, что и продукты.

Здесь доказывается: файл-ответ ложится рядом в форме, которую CDPM уже
читает (`accepted`, `state`, `problems[].message`); повторный прогон
поданное не подаёт заново; отказ службы — это ответ с проблемами, а не
молчание; служба не поднята — код 2 словами; под `catalog/` инструмент
не пишет ничего.
"""
from __future__ import annotations

import json
import urllib.error

from catalog_service import import_requests as tool
from template_catalog import requests as law
# Explicitly expose only the required fixture for the offline --noconftest
# runner; loading the complete fixture plugin would also run its git probe.
from tests.catalog_service.conftest import MARKER, folder_bytes, make_service
from tests.catalog_service.test_service_requests_machine import (
    HEADERS, LAW_ID, TOKEN, request_doc)


def through(svc):
    """Подставной POST: тот же машинный канал, но по клиенту теста.

    Свой HTTP-слой инструмента судится отдельно (случай «служба не
    поднята»); здесь судится ПРОТОКОЛ: заголовки, разбор ответа, файлы."""
    def post(url, headers, body):
        path = "/api" + url.split("/api", 1)[1]
        resp = svc.post(path, content=body, headers=headers)
        return (resp.status_code, resp.json(),
                resp.headers.get("Content-Type"))
    return post


def queue(tmp_path, *docs):
    folder = tmp_path / "cosmos-catalog-requests" / "outbox"
    folder.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        (folder / f"{doc['request_id']}.json").write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1)
            + "\n", encoding="utf-8")
    return folder


def machine_service(make_service):
    return make_service("owner", machine_token=TOKEN)


def test_inbox_is_accepted_and_the_reply_lands_beside_the_file(
        make_service, tmp_path):
    svc = machine_service(make_service)
    cat = svc.catalog()
    good = request_doc(cat, "probe_ochered_odin")
    bad = request_doc(cat, "probe_ochered_dva",
                      request_id=law.request_id_of(law.manual_key(
                          "families", "probe_ochered_dva", "второй")),
                      ref=f"run-0091#{MARKER}",
                      title="Заявка со словом заказчика")
    folder = queue(tmp_path, good, bad)
    before = folder_bytes(svc.folder)

    # Адрес — умолчание САМОГО инструмента, а не число из теста: живой
    # службы здесь нет (запрос уходит в подставной post), и зависеть от
    # того, что слушает порт на машине, стенд не вправе.
    got = tool.run(folder, api_url=tool.DEFAULT_API_URL, token=TOKEN,
                   reply=True, post=through(svc))

    assert got["seen"] == 2 and got["sent"] == 2
    assert got["accepted"] == 1 and got["refused"] == 1
    ok_reply = json.loads(
        (folder / f"{good['request_id']}.reply.json").read_text(
            encoding="utf-8"))
    assert ok_reply["accepted"] is True
    assert ok_reply["catalog_request_id"] == LAW_ID
    assert ok_reply["state"] == "подана"
    assert ok_reply["by"] == tool.IMPORTER
    assert ok_reply["request_id"] == good["request_id"]
    # Слова в ответе — СЛУЖБЫ, а не пересказ инструмента: площадка
    # читает ровно то, что сказал каталог.
    assert ok_reply["words"] == (f"заявка от продукта CDPM принята: "
                                 f"{LAW_ID} (подана); решение — за владельцем")
    bad_reply = json.loads(
        (folder / f"{bad['request_id']}.reply.json").read_text(
            encoding="utf-8"))
    assert bad_reply["accepted"] is False
    assert bad_reply["catalog_request_id"] is None
    assert "invalid-request" in bad_reply["words"]
    assert bad_reply["problems"], "отказ без проблем — молчание, не ответ"
    assert any("source.ref" in str(p.get("where"))
               for p in bad_reply["problems"])
    # Единственный писатель каталога — служба: инструмент кладёт только
    # ответы рядом с очередью.
    after = folder_bytes(svc.folder)
    assert set(after) - set(before) == {"requests.json"} or \
        after["requests.json"] != before["requests.json"]
    assert svc.catalog().requests()[0]["request_id"] == LAW_ID
    print(f"файлов очереди принято: {got['accepted']}, отказано: "
          f"{got['refused']}")


def test_second_run_does_not_submit_what_already_has_a_reply(make_service,
                                                             tmp_path):
    svc = machine_service(make_service)
    folder = queue(tmp_path, request_doc(svc.catalog(), "probe_povtor_ochered"))
    calls: list = []

    def counting(url, headers, body):
        calls.append(url)
        return through(svc)(url, headers, body)

    first = tool.run(folder, token=TOKEN, reply=True, post=counting)
    second = tool.run(folder, token=TOKEN, reply=True, post=counting)
    assert first["sent"] == 1 and second["sent"] == 0
    assert second["skipped"] == [f"{LAW_ID}.json"]
    assert len(calls) == 1, "повторный прогон подал заявку заново"
    print(f"вызовов службы за два прогона: {len(calls)}; пропущено с "
          f"ответом: {len(second['skipped'])}")


def test_queue_file_without_the_submitter_is_refused_with_words(make_service,
                                                                tmp_path):
    svc = machine_service(make_service)
    doc = request_doc(svc.catalog(), "probe_bez_podatelya")
    doc.pop("submitted_by")
    folder = queue(tmp_path, doc)
    got = tool.run(folder, token=TOKEN, reply=True, post=through(svc))
    assert got["refused"] == 1
    reply = json.loads((folder / f"{LAW_ID}.reply.json").read_text(
        encoding="utf-8"))
    assert reply["accepted"] is False
    assert "submitted_by" in reply["words"]
    assert not svc.catalog().requests()
    print("файл без подателя: отказ словами, заявки не заведено")


def test_channel_off_is_told_by_the_service_not_guessed(make_service,
                                                        tmp_path):
    """Канал выключен — это ответ СЛУЖБЫ (403), и он ложится в файл."""
    svc = make_service("owner")                      # без токена канала
    folder = queue(tmp_path, request_doc(svc.catalog(), "probe_kanal_vykl"))
    got = tool.run(folder, token=TOKEN, reply=True, post=through(svc))
    assert got["refused"] == 1
    reply = json.loads((folder / f"{LAW_ID}.reply.json").read_text(
        encoding="utf-8"))
    assert "machine-channel-off" in reply["words"]
    assert "CATALOG_MACHINE_TOKEN" in reply["words"]
    print("канал выключен: слова службы дошли до файла-ответа")


def test_service_not_running_returns_two_and_says_so(tmp_path, capsys,
                                                     make_service,
                                                     monkeypatch):
    svc = machine_service(make_service)
    folder = queue(tmp_path, request_doc(svc.catalog(), "probe_net_sluzhby"))
    # Изолируем отказ связи на сетевой границе: настоящий http_post и
    # main остаются, но закрытый порт машины не считается тестовым API.
    # Это проверка обработки URLError, не свидетельство живого сокета.
    def unavailable(request, *, timeout):
        assert request.full_url == "http://127.0.0.1:1/api/requests"
        assert timeout == 30
        raise urllib.error.URLError("synthetic connection refused")

    monkeypatch.setattr(tool.urllib.request, "urlopen", unavailable)
    code = tool.main(["--inbox", str(folder), "--api-url",
                      "http://127.0.0.1:1", "--token", TOKEN, "--reply"])
    assert code == 2
    said = capsys.readouterr().out
    assert "служба каталога не отвечает" in said
    assert not (folder / f"{LAW_ID}.reply.json").exists(), \
        "ответа нет — служба не отвечала"
    print("служба не поднята: код 2 словами, ответов не написано")


def test_missing_inbox_returns_two(tmp_path, capsys):
    code = tool.main(["--inbox", str(tmp_path / "нет-такой-папки")])
    assert code == 2
    assert "папки очереди нет" in capsys.readouterr().out
    print("папки очереди нет: код 2 словами")


def test_main_prints_the_count_and_returns_one_on_refusals(make_service,
                                                           tmp_path, capsys,
                                                           monkeypatch):
    svc = machine_service(make_service)
    cat = svc.catalog()
    folder = queue(tmp_path,
                   request_doc(cat, "probe_itog_odin"),
                   request_doc(cat, "probe_itog_dva",
                               request_id=law.request_id_of(law.manual_key(
                                   "families", "probe_itog_dva", "второй")),
                               ref=f"run-0092#{MARKER}",
                               title="Вторая заявка со словом заказчика"))
    monkeypatch.setattr(tool, "http_post", through(svc))
    code = tool.main(["--inbox", str(folder), "--token", TOKEN, "--reply"])
    said = capsys.readouterr().out
    assert code == 1, "были отказы — код 1"
    assert "файлов рассмотрено: 2" in said
    assert "принято: 1" in said and "отказано: 1" in said
    assert LAW_ID in said
    print("итог инструмента напечатан числами; код возврата 1 при отказах")


def test_tool_writes_nothing_under_the_catalog(make_service, tmp_path):
    """Страж границы: единственный писатель каталога — служба."""
    svc = machine_service(make_service)
    folder = queue(tmp_path, request_doc(svc.catalog(), "probe_granica"))
    seen: list = []

    def watching(url, headers, body):
        seen.append(url)
        return 403, {"detail": {"code": "read-only", "words": "витрина"}}

    got = tool.run(folder, token=TOKEN, reply=True, post=watching)
    assert got["refused"] == 1 and seen
    assert not svc.catalog().requests(), "инструмент завёл заявку сам"
    made = {p.name for p in folder.iterdir()}
    assert made == {f"{LAW_ID}.json", f"{LAW_ID}.reply.json"}
    print(f"инструмент написал файлов вне очереди: 0; в очереди: "
          f"{len(made) - 1} ответ")


def test_headers_of_the_tool_are_the_channel_headers(make_service, tmp_path):
    svc = machine_service(make_service)
    folder = queue(tmp_path, request_doc(svc.catalog(), "probe_zagolovki"))
    seen: dict = {}

    def watching(url, headers, body):
        seen.update(headers)
        return through(svc)(url, headers, body)

    tool.run(folder, token=TOKEN, post=watching)
    assert seen["X-Catalog-Product"] == "CDPM", "податель берётся из файла"
    assert seen["X-Catalog-Token"] == TOKEN
    assert seen["Content-Type"] == "application/json"
    assert set(HEADERS) <= set(seen)
    print(f"заголовков канала: {len(seen)} ({', '.join(sorted(seen))})")
