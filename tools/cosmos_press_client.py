# -*- coding: utf-8 -*-
"""Клиент ручек COSMOS «Пакет постановки» для стендов и раннеров серий.

Граница (решение владельца 04.09.2026): CDPM заканчивается пакетом
ВМП; расчёт пейринга и ростеринга, сертификация решений, карта ростера
и выгрузка — у COSMOS. Контракт — ЖИВЫЕ ручки webapp/app.py (коммиты
46b98f3, 6c2a604), не черновик:

    POST /api/task-package/upload            multipart file=<zip>
         → краткий вердикт приёма канала: {verdict, sha, mg, mismatch,
           detail, spec_version, source_run, already_accepted, …}
    POST /api/task-package/current/press?task=pairing|rostering
         &engine=&spentLimitSeconds=&unimprovedSpentLimitSeconds=
         &mode=&objective_id=&pairingRunId=&acceptedUncovered=
         &pollSeconds=                        → состояние (как GET)
    GET  /api/task-package/current/press
         → {present: false} | {present, package_sha, task, engine, state,
            done, error, verdict, tasks_done, llm_scope, files, …}
    GET  /api/task-package/current/press/file/{alias}   (задача — в алиасе)
    GET  /api/task-package/current/roster-map

Клиенту всё равно, площадка это (httpx.Client с base_url) или
приложение в процессе (TestClient): нужны только .post и .get. Вход в
COSMOS — личность площадки (dev-режим, сессия или Cloudflare Access);
заголовки входа задаёт вызывающая сторона при создании http-клиента,
клиент их не выдумывает.

Охраняемое: нажатие судится по ОТПЕЧАТКУ пакета (package_sha состояния
должен совпадать с sha загрузки — «текущий» пакет площадки глобален),
ожидание — по done, а не по словам состояния; «пакета нет» и состояние
без полей — немедленный отказ, не час ожидания.
"""
from __future__ import annotations

import time
from urllib.parse import quote

UPLOAD = "/api/task-package/upload"
PRESS = "/api/task-package/current/press"
ROSTER_MAP = "/api/task-package/current/roster-map"
TASKS = ("pairing", "rostering")
ENGINES = ("cpsat", "cplex", "highs")

SLEEP = time.sleep   # подменяются стендами (ожидание без сна, часы)
CLOCK = time.time


class CosmosPressError(RuntimeError):
    """Честный отказ словами: ручка ответила не тем или не вовремя."""


def _fail(what: str, r) -> None:
    raise CosmosPressError("%s: HTTP %d %s" % (what, r.status_code,
                                                 (r.text or "")[:400]))


def launch_gate(brief: dict, task: str) -> str | None:
    """Причина, по которой нажимать задачу НЕЛЬЗЯ по вердикту приёма
    (матрица готовности канала: mg[task].launch_allowed; расхождение
    матрицы — mismatch). None — гейт открыт или площадка о нём молчит."""
    if brief.get("mismatch"):
        return "расхождение матрицы готовности при приёме: %s" % (
            brief.get("detail") or brief.get("mismatch"))
    cell = (brief.get("mg") or {}).get(task) or {}
    if cell and cell.get("launch_allowed") is False:
        return "гейт запуска закрыт для %s: %s" % (
            task, cell.get("reason") or cell.get("state") or "матрица готовности")
    return None


class CosmosPress:
    def __init__(self, client, sleep=None, clock=None):
        self.client = client
        self._sleep = sleep if sleep is not None else (lambda s: SLEEP(s))
        self._clock = clock if clock is not None else (lambda: CLOCK())

    # ── пакет ──
    def upload(self, zip_bytes: bytes, name: str = "task-package.zip") -> dict:
        """Загрузка пакета человеком/стендом: тот же гейт и матрица, что
        у машинного канала; пакет становится текущим. Возвращает краткий
        вердикт приёма с ключом sha (отпечаток пакета)."""
        r = self.client.post(UPLOAD, files={"file": (name, zip_bytes,
                                                     "application/zip")})
        if r.status_code not in (200, 201):
            _fail("загрузка пакета", r)
        body = r.json()
        sha = body.get("sha") or body.get("sha256")
        if not sha:
            raise CosmosPressError("загрузка пакета: ответ без отпечатка sha: %r"
                                   % (body,))
        body["sha"] = sha
        return body

    # ── нажатие ──
    def press(self, task: str, *, engine: str | None = None,
              spent: int | None = None, unimproved_spent: int | None = None,
              mode: str | None = None, objective_id: str | None = None,
              pairing_run_id: str | None = None,
              accepted_uncovered: str | None = None,
              poll_s: float | None = None,
              moves: str | None = None) -> dict:
        if task not in TASKS:
            raise CosmosPressError("задача %r: кнопка знает %s"
                                   % (task, ", ".join(TASKS)))
        if engine is not None and engine not in ENGINES:
            raise CosmosPressError("движок %r: известны %s"
                                   % (engine, ", ".join(ENGINES)))
        if engine is not None and task != "pairing":
            raise CosmosPressError("движок отбора применим только к пейрингу")
        params: dict = {"task": task}
        for key, val in (("engine", engine), ("spentLimitSeconds", spent),
                         ("unimprovedSpentLimitSeconds", unimproved_spent),
                         ("mode", mode), ("objective_id", objective_id),
                         ("pairingRunId", pairing_run_id),
                         ("acceptedUncovered", accepted_uncovered),
                         ("pollSeconds", poll_s),
                         ("moves", moves)):
            if val is not None:
                params[key] = val
        r = self.client.post(PRESS, params=params)
        if r.status_code != 200:
            _fail("нажатие %s" % task, r)
        return r.json()

    def status(self) -> dict:
        r = self.client.get(PRESS)
        if r.status_code != 200:
            _fail("состояние нажатия", r)
        return r.json()

    def wait(self, task: str, timeout_s: float = 3600.0, poll_s: float = 3.0,
             on_state=None, package_sha: str | None = None,
             attempt_id: str | None = None) -> dict:
        """Ждёт завершения нажатия задачи task: done=true при task этой
        задачи. «Пакета нет», чужой пакет (package_sha не наш) или
        состояние без полей — немедленный отказ; превышение времени —
        отказ словами (нажатие на площадке при этом продолжает жить).
        attempt_id — попытка СВОЕГО нажатия (ответ press): текущая
        попытка площадки стала другой — отказ, чужой done за свой не
        принимается (единая версия 06.09.2026, W-19)."""
        t0 = self._clock()
        last = None
        while True:
            st = self.status()
            if st.get("present") is False:
                raise CosmosPressError("%s: текущего пакета на площадке нет "
                                       "(снят или заменён во время ожидания)" % task)
            if "done" not in st or "state" not in st:
                raise CosmosPressError("%s: состояние нажатия без полей done/state: %r"
                                       % (task, st))
            if attempt_id and st.get("attempt_id") != attempt_id:
                raise CosmosPressError(
                    "%s: текущая попытка заменена; ожидалась %s, получена %s — "
                    "чужое нажатие за своё не принимается"
                    % (task, attempt_id, st.get("attempt_id")))
            if package_sha and st.get("package_sha") \
                    and st["package_sha"] != package_sha:
                raise CosmosPressError(
                    "%s: текущий пакет площадки %s… — не наш (%s…): нажатие "
                    "относится к чужому пакету" % (task, st["package_sha"][:12],
                                                   package_sha[:12]))
            # Ход идущего расчёта — отдельным ключом progress (аудит
            # A10, 04.09.2026): state держит «кнопка: идёт» до конца,
            # шаг для человека печатается рядом.
            state = str(st.get("state") or "")
            if st.get("progress"):
                state += " · " + str(st["progress"])
            if state != last:
                if on_state:
                    on_state(state)
                last = state
            if st.get("done") and st.get("task") == task:
                return st
            if self._clock() - t0 > timeout_s:
                raise CosmosPressError("%s: превышено %d с (состояние: %s)"
                                       % (task, timeout_s, state or "—"))
            self._sleep(poll_s)

    # ── артефакты (задача зашита в алиас: «выдача-кнопки-пейринга» и т.д.) ──
    # attempt — артефакт из снимка попытки по ID (ручка ?attempt=, аудит
    # A10 ступень M); без него — текущий артефакт, как прежде.
    def _file(self, alias: str, attempt: str | None):
        return self.client.get("%s/file/%s" % (PRESS, quote(alias, safe="")),
                               params={"attempt": attempt} if attempt else None)

    def file_bytes(self, alias: str, *, attempt: str | None = None) -> bytes:
        r = self._file(alias, attempt)
        if r.status_code != 200:
            _fail("артефакт %s" % alias, r)
        return r.content

    def file_json(self, alias: str, *, attempt: str | None = None) -> dict:
        r = self._file(alias, attempt)
        if r.status_code != 200:
            _fail("артефакт %s" % alias, r)
        return r.json()

    def roster_map(self) -> dict:
        r = self.client.get(ROSTER_MAP)
        if r.status_code != 200:
            _fail("карта ростера", r)
        return r.json()
