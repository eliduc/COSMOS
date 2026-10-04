"""Historical sender evidence, not a new receipt protocol or audit verdict.

The pinned 64d10ea1 channel below is the complete read-only git source, with
LF line endings, executed in a temporary in-memory module. Only _urlopen is
injected. No git/subprocess/network is used during tests.
Negative replies are deliberately synthetic transport data, not receiver
verdicts. The healthy control uses the actual exporter/gate/receiver/store.
The last test also proves information loss: remote SHA/identity discarded by
the old sender cannot be recovered from an otherwise identical saved mark.
"""
import ast
import copy
import hashlib
import json
from types import ModuleType

import pytest

from cdpm import channel, delivery_mark, run_record, webui
import test_cdpm_delivery_reply_agent as reply_tests
from test_cdpm_delivery_reply_agent import Reply, memory_receiver  # noqa: F401
from test_cdpm_package_publish_agent import publication  # noqa: F401
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from webapp import task_channel


LEGACY_COMMIT = "64d10ea1be29b4c954062fa97465e5ce020c5536"
LEGACY_GIT_BLOB = "812ee0b0ecfc6f4cee9073915ee9eeb84d8bc653"
LEGACY_LF_SHA256 = "FFE1AB88DDFBA360BA674FDEDEBC72EA7E744FA268E6AC9D16AD9A32F81F0131"
LEGACY_CHANNEL_SOURCE = r'''# -*- coding: utf-8 -*-
"""Канал ВМП+МГ (поручение владельца 2026-08-29): доставка пакета
постановки приёмнику COSMOS ПРЯМЫМ каналом контура машины.

Контракт согласован обеими сессиями (форма COSMOS + шесть уточнений
CDPM): транспорт — HTTP внутри контура, пакет ОСТАЁТСЯ байтовым
артефактом (sha по БАЙТАМ zip); приёмник немедленно верифицирует
матрицу готовности, но РАСЧЁТ НЕ ЗАПУСКАЕТ — кнопка за человеком;
файловый путь остаётся запасным. Канал管 не ходит за пределы контура
машины: пакеты с данными заказчика наружу не отправляются без
отдельного слова владельца.

Тем же каналом (тот же адрес, тот же токен) панель ЧИТАЕТ среду
COSMOS для головы записи о прогоне — `environment()`, договор с COSMOS
11.09.2026: второго адреса COSMOS у контура нет, и заводить его ради
записи незачем.

Выключатели (fail-closed): нет CREW_CHANNEL_URL или
CREW_CHANNEL_TOKEN в окружении — канал ВЫКЛЮЧЕН, отправка честно
отказывает словами (пакет остаётся файлом). Токен закрыт от
агентов-исполнителей стражем окружения обеих сторон."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# 3 попытки с растущей паузой; таймаут запроса 120 с (пакеты контура
# ~0,5 МБ — запас многократный, предел приёмника 64 МБ).
_RETRY_PAUSES_S = (0.0, 2.0, 6.0)
_TIMEOUT_S = 120

#: Ручка среды COSMOS (договор с COSMOS 11.09.2026): её спрашивает
#: ХОЗЯИН записи ДО чеканки головы — панель и раннер наборов.
_ENVIRONMENT_ROUTE = "/api/run/environment"
#: Схема ответа ручки среды.
_ENVIRONMENT_SCHEMA = "cosmos-run-environment/v1"
#: Предел ожидания среды: объявление прогона не имеет права висеть на
#: сети — человек стоит у кнопки «Создать прогон». Повторов нет: у
#: панели человек ждёт, а не пакет едет (у раннера предел 10 с).
_ENVIRONMENT_TIMEOUT_S = 5


def channel_enabled() -> bool:
    return bool(str(os.environ.get("CREW_CHANNEL_URL") or "").strip()
                and str(os.environ.get("CREW_CHANNEL_TOKEN")
                        or "").strip())


def send_task_package(zip_bytes: bytes, spec_version: str,
                      source_run: str,
                      _urlopen=urllib.request.urlopen,
                      _sleep=time.sleep) -> dict:
    """Отправка пакета приёмнику. Возвращает отметку доставки:
    {sent, sha, attempts, verdict?, mg?, mismatch?, already_accepted?,
    detail} — отметка пишется вызывающим в запись прогона; при
    неудаче отправки пакет остаётся файлом (честная строка в detail).
    Сеть/отказ приёмника НЕ исключение — канал не роняет выпуск."""
    sha = hashlib.sha256(zip_bytes).hexdigest()
    mark = {"sent": False, "sha": sha, "attempts": 0,
            "detail": ""}
    url = str(os.environ.get("CREW_CHANNEL_URL") or "").strip()
    token = str(os.environ.get("CREW_CHANNEL_TOKEN") or "").strip()
    if not url or not token:
        mark["detail"] = ("канал выключен (CREW_CHANNEL_URL/"
                          "CREW_CHANNEL_TOKEN не заданы) — пакет "
                          "остаётся файлом, доставка руками")
        return mark
    req_url = url.rstrip("/") + "/api/task-package/receive"
    last_err = ""
    for pause in _RETRY_PAUSES_S:
        if pause:
            _sleep(pause)
        mark["attempts"] += 1
        req = urllib.request.Request(
            req_url, data=zip_bytes, method="POST",
            headers={
                "Content-Type": "application/zip",
                "X-Package-Sha256": sha,
                "X-Spec-Version": str(spec_version or ""),
                "X-Source-Run": str(source_run or ""),
                "X-Channel-Token": token,
            })
        try:
            with _urlopen(req, timeout=_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            mark["sent"] = True
            # cosmos_run — прогон COSMOS, в который лёг пакет (система
            # прогонов COSMOS 07.09.2026: при repeat — держатель уже
            # принятого пакета; хранилище не заведено — null).
            for k in ("verdict", "mg", "mismatch", "already_accepted",
                      "detail", "cosmos_run", "repeat"):
                if k in body:
                    mark[k] = body[k]
            return mark
        except urllib.error.HTTPError as e:
            # Отказ приёмника — НЕ ретраится (он детерминирован:
            # кривой токен/sha/гейт); сетевые ошибки — ретраятся.
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:                             # noqa: BLE001
                detail = ""
            mark["detail"] = (f"приёмник отказал: HTTP {e.code}"
                              + (f" — {detail}" if detail else "")
                              + "; пакет остаётся файлом")
            return mark
        except Exception as e:                            # noqa: BLE001
            last_err = str(e)
    mark["detail"] = (f"доставка не удалась за {mark['attempts']} "
                      f"попытки ({last_err}); пакет остаётся файлом")
    return mark


def environment(scope: str | None = None,
                _urlopen=urllib.request.urlopen) -> tuple[dict | None, str]:
    """Среда COSMOS ручкой `GET /api/run/environment` — ДО чеканки
    головы записи (договор с COSMOS 11.09.2026).

    Зачем. Голову чеканит ХОЗЯИН записи, и своих моделей COSMOS
    (сертификатор, читатель ИДАК) он не знает: до 11.09.2026 они в
    голову не попадали вовсе. Панель спрашивает их у COSMOS и кладёт
    рядом со своими словом `system: "cosmos"`; не дождалась — честные
    слова в `models_note` головы, а не выдуманные модели.

    Возвращает `(среда, "")` при ответе по договору и `(None, причина)`
    иначе. Наружу не летит НИЧЕГО: опрос среды — летопись, и она не
    вправе отменить объявление прогона. Повторов нет и предел ожидания
    короткий (`_ENVIRONMENT_TIMEOUT_S`): у кнопки стоит человек.

    `scope` — допуск моделей НАБОРА (`any` | `local-only`): его знает
    хозяин записи, а не COSMOS, и по нему COSMOS выбирает сертификатора
    и читателя ИДАК — так же, как при нажатии. Не назван — COSMOS
    возьмёт самый узкий (local-only), а не самый широкий."""
    if not channel_enabled():
        return None, ("канал выключен (CREW_CHANNEL_URL/CREW_CHANNEL_TOKEN "
                      "не заданы) — модели COSMOS в голову не попадут")
    url = str(os.environ.get("CREW_CHANNEL_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("CREW_CHANNEL_TOKEN") or "").strip()
    req_url = url + _ENVIRONMENT_ROUTE
    if str(scope or "").strip():
        req_url += "?" + urllib.parse.urlencode({"scope": str(scope)})
    req = urllib.request.Request(
        req_url, method="GET",
        headers={"Accept": "application/json", "X-Channel-Token": token})
    try:
        with _urlopen(req, timeout=_ENVIRONMENT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return None, f"COSMOS отказал: HTTP {e.code}"
    except Exception as e:                                # noqa: BLE001
        return None, f"COSMOS не ответил: {e}"
    try:
        body = json.loads(raw)
    except ValueError as e:
        return None, f"ответ COSMOS не разобран как JSON: {e}"
    if not isinstance(body, dict) or body.get("schema") != _ENVIRONMENT_SCHEMA:
        return None, (f"ответ COSMOS не по договору: ждали схему "
                      f"{_ENVIRONMENT_SCHEMA}")
    return body, ""
'''


@pytest.fixture
def legacy_sender():
    assert hashlib.sha256(LEGACY_CHANNEL_SOURCE.encode("utf-8")).hexdigest().upper() == LEGACY_LF_SHA256
    module = ModuleType("historical_cdpm_channel_64d10ea1")
    tree = ast.parse(LEGACY_CHANNEL_SOURCE)
    exec(compile(tree, f"git:{LEGACY_COMMIT}:cdpm/channel.py", "exec"), module.__dict__)
    return module.send_task_package


@pytest.mark.parametrize("reply_kind", ["empty-json", "refused-verdict", "invalid-mismatch"])
def test_old_sender_incomplete_or_contradictory_mark_is_not_current_delivery(
        publication, monkeypatch, legacy_sender, reply_kind):
    state = publication
    run = state.out.parent
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-historical-probe")
    replies, old_marks, requests = [], [], []

    def historic_send(body, revision, source_run):
        actual_sha = hashlib.sha256(body).hexdigest()
        reply = task_channel._brief("OK", sha=actual_sha,
            detail="Synthetic historical transport input, not operational acceptance",
            spec_version=revision, source_run=source_run)
        if reply_kind == "empty-json":
            reply = {}
        elif reply_kind == "refused-verdict":
            reply["verdict"] = "REFUSED"
        else:
            reply["mismatch"] = False

        def transport(request, timeout):
            assert request.data == state.package.read_bytes() == body
            headers = {key.lower(): value for key, value in request.header_items()}
            assert headers["x-package-sha256"] == actual_sha
            requests.append(request)
            replies.append(copy.deepcopy(reply))
            return Reply(json.dumps(reply, ensure_ascii=False).encode("utf-8"))

        mark = legacy_sender(body, revision, source_run, _urlopen=transport,
            _sleep=lambda _seconds: pytest.fail("deterministic reply must not retry"))
        old_marks.append(copy.deepcopy(mark))
        return mark

    monkeypatch.setattr(channel, "send_task_package", historic_send)
    state.invoke()  # Real registered handler, package gate and atomic mark writer.
    receipt = state.out / "delivery.json"
    stored_bytes = receipt.read_bytes()
    stored = json.loads(stored_bytes)
    sha = hashlib.sha256(state.package.read_bytes()).hexdigest()
    assert len(requests) == len(old_marks) == 1
    assert old_marks[0]["sent"] is True and old_marks[0]["sha"] == sha
    assert all(stored[key] == value for key, value in old_marks[0].items())
    assert state.checks and all(not errors for _path, errors in state.checks)
    current = delivery_mark.read_current(run)
    endpoint = webui.channel_delivery(run.name)
    record = run_record.build(run)["results"]
    persisted = run_record.on_disk(run)["results"]
    observed = {"input_reply": replies[0], "old_sender_mark": old_marks[0],
        "current_mark": current, "endpoint": endpoint,
        "rebuilt": record, "persisted": persisted}
    print(json.dumps(observed, ensure_ascii=False, sort_keys=True))
    assert state.source.read_bytes() == state.source_before
    assert receipt.read_bytes() == stored_bytes, "Historical evidence must stay immutable"
    violations = []
    if current.get("sent") is not False or endpoint.get("sent") is not False:
        violations.append("incomplete/contradictory historical brief confirms delivery")
    if record["deliveries"] or persisted["deliveries"]:
        violations.append("historical false confirmation still supplies current deliveries")
    if record["state"] == "завершён" or persisted["state"] == "завершён":
        violations.append("run becomes completed solely from the bad historical mark")
    if any(record["verdicts"][key] is not None for key in
           ("package", "readiness_matrix", "cosmos_run")):
        violations.append("unconfirmed historical verdict metadata leaks into current results")
    assert not violations, {"violations": violations, "observed": observed}


def test_real_healthy_legacy_receipt_survives_but_lost_remote_identity_is_unrecoverable(
        memory_receiver, monkeypatch, legacy_sender):
    probe = memory_receiver
    # This changes only which real sender the fixture calls; its receiver,
    # transport, bytes, persistence and readiness judges remain real.
    monkeypatch.setattr(reply_tests, "REAL_SEND", legacy_sender)
    sent = probe.state.invoke()
    run = probe.state.out.parent
    body = probe.state.package.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    actual_reply = probe.receipts[0]
    assert actual_reply["sha"] == sha and actual_reply["verdict"] == "OK"
    assert (probe.inbox / sha / "package.zip").read_bytes() == body
    assert sent["sent"] is True and sent["sha"] == sha
    assert delivery_mark.read_current(run) == sent
    assert webui.channel_delivery(run.name)["sent"] is True
    assert run_record.build(run)["results"]["deliveries"] == [
        {"kind": "task-package", "path": "channel/task-package.zip", "sha256": sha}]
    assert run_record.build(run)["results"]["state"] == "завершён"
    before = (run / "channel" / "delivery.json").read_bytes()

    def old_projection(reply):
        return legacy_sender(body, actual_reply["spec_version"],
            actual_reply["source_run"],
            _urlopen=lambda _request, timeout: Reply(json.dumps(reply).encode("utf-8")),
            _sleep=lambda _seconds: pytest.fail("A parsed reply must not retry"))

    healthy_projection = old_projection(actual_reply)
    assert healthy_projection["sent"] is True
    for key, foreign_value in (("sha", "0" * 64),
                               ("spec_version", "9.999"),
                               ("source_run", "another-receiver-artifact-run"),
                               ("run_note_code", "package-bytes-missing")):
        foreign_reply = copy.deepcopy(actual_reply)
        assert foreign_reply.get(key) != foreign_value
        foreign_reply[key] = foreign_value
        # The bytes of this saved projection would be identical. A reader
        # cannot reject one and preserve the other without missing evidence.
        foreign_projection = old_projection(foreign_reply)
        assert foreign_projection == healthy_projection, key
        assert json.dumps(foreign_projection, sort_keys=True) == json.dumps(
            healthy_projection, sort_keys=True)
        assert foreign_projection["sha"] == sha
    assert (run / "channel" / "delivery.json").read_bytes() == before
    assert probe.state.source.read_bytes() == probe.state.source_before

