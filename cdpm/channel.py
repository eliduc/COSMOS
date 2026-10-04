# -*- coding: utf-8 -*-
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
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

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


def _package_identity(zip_bytes: bytes) -> dict[str, str]:
    """Read artifact identity, never substitute this invocation's wire headers.

    Legacy/missing identity remains unknown; this is not another package gate.
    The existing accepted brief and exact byte SHA are still required.
    """
    from cdpm.model import loads_strict

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            manifest = loads_strict(archive.read("manifest.json"))
    except Exception:  # noqa: BLE001 — optional metadata, including decompressor errors
        return {}
    if not isinstance(manifest, dict):
        return {}
    return {key: manifest[key] for key in ("spec_version", "source_run")
            if isinstance(manifest.get(key), str)}


def _loads_receipt(raw):
    """Do not choose a winning value in ambiguous delivery evidence.

    Reuse the existing strict JSON reader; this does not add another receipt
    schema or infer remote identities missing from historical marks.
    """
    from cdpm.model import loads_strict
    return loads_strict(raw)


def _receipt_problem(body, sha: str, identity: dict[str, str]) -> str | None:
    """Validate the existing task-channel brief, not catalogue receipt/1."""
    if not isinstance(body, dict):
        return "квитанция не является объектом"
    if body.get("sha") != sha:
        return "квитанция не подтверждает sha отправленного пакета"
    if body.get("verdict") not in ("OK", "MISMATCH"):
        return "квитанция не содержит вердикт принятого пакета OK/MISMATCH"
    for key, kind in (("mg", dict), ("mismatch", list), ("detail", str),
                      ("already_accepted", bool)):
        if type(body.get(key)) is not kind:
            return f"неверная форма поля {key} в квитанции"
    if any(not isinstance(words, str) for words in body["mismatch"]):
        return "неверная форма строк mismatch в квитанции"
    # _brief permits absent/null legacy identities. Present fields describe
    # the artifact, on both first acceptance and cached same-SHA repeats.
    # A replay child's headers may name the child; its manifest names its parent.
    for key in ("spec_version", "source_run"):
        if body.get(key) is not None:
            if not isinstance(body[key], str):
                return f"неверная форма поля {key} в квитанции"
            if key in identity and body[key] != identity[key]:
                return f"поле {key} квитанции не совпало с отправленным пакетом"
    if body.get("run_note_code") == "package-bytes-missing":
        return ("приёмник сообщил отсутствие байтов ранее принятого пакета "
                "(package-bytes-missing)")
    if "repeat" in body and type(body["repeat"]) is not bool:
        return "неверная форма поля repeat в квитанции"
    if body.get("cosmos_run") is not None and not isinstance(body["cosmos_run"], str):
        return "неверная форма поля cosmos_run в квитанции"
    return None


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
    identity = _package_identity(zip_bytes)
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
                raw = resp.read()
            try:
                body = _loads_receipt(raw)
            except (ValueError, UnicodeError):
                mark["detail"] = ("доставка не подтверждена: квитанция не читается "
                                  "как JSON; пакет остаётся файлом")
                return mark
            problem = _receipt_problem(body, sha, identity)
            if problem:
                # A deterministic bad reply is not proof of remote rejection;
                # do not manufacture success or repeatedly resend it here.
                mark["detail"] = (f"доставка не подтверждена: {problem}; "
                                  "пакет остаётся файлом")
                return mark
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
                # This becomes the stored delivery diagnostic, not a preview:
                # keep every reason already returned by the receiver.
                detail = e.read().decode("utf-8", "replace")
            except Exception:                             # noqa: BLE001
                detail = ""
            mark["detail"] = (f"приёмник отказал: HTTP {e.code}"
                              + (f" — {detail}" if detail else "")
                              + "; пакет остаётся файлом")
            return mark
        except Exception as e:                            # noqa: BLE001
            last_err = str(e)
    mark["detail"] = (f"доставка не подтверждена за {mark['attempts']} "
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
