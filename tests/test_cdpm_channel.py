# -*- coding: utf-8 -*-
"""Канал ВМП+МГ, сторона-отправитель (поручение владельца 2026-08-29).

Охраняемое: без адреса/токена канал ВЫКЛЮЧЕН и честно говорит это
словами (пакет остаётся файлом); успешная доставка несёт краткий
вердикт приёмника; отказ приёмника детерминирован и НЕ ретраится;
сетевые ошибки ретраятся трижды с честной строкой в конце; канал
никогда не роняет выпуск пакета исключением."""
import io
import hashlib
import json
import urllib.error

import pytest

from cdpm import channel


@pytest.fixture(autouse=True)
def _isolated_channel_environment(monkeypatch):
    """The narrow --noconftest profile must not inherit a live channel."""
    monkeypatch.delenv("CREW_CHANNEL_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)


def _resp(payload: dict):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _R(json.dumps(payload).encode("utf-8"))


def test_канал_выключен_без_окружения():
    mark = channel.send_task_package(b"zip", "1.14", "run-X")
    assert mark["sent"] is False and mark["attempts"] == 0
    assert "канал выключен" in mark["detail"]
    assert "пакет остаётся файлом" in mark["detail"]
    assert len(mark["sha"]) == 64


def test_успешная_доставка_несёт_краткий_вердикт(monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "т")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        return _resp({"verdict": "OK",
                      "sha": hashlib.sha256(req.data).hexdigest(),
                      "spec_version": "1.14", "source_run": "run-X",
                      "detail": "пакет принят",
                      "mg": {"pairing": {"state": "optimization", "launch_allowed": True},
                             "rostering": {"state": "stop", "launch_allowed": False}},
                      "mismatch": [], "already_accepted": False,
                      "cosmos_run": "run-0033", "repeat": False})

    mark = channel.send_task_package(
        b"zip-bytes", "1.14", "run-X", _urlopen=fake_urlopen)
    assert mark["sent"] is True and mark["attempts"] == 1
    assert mark["verdict"] == "OK"
    assert mark["mg"]["rostering"] == {"state": "stop", "launch_allowed": False}
    # Система прогонов COSMOS (07.09.2026): прогон-получатель пакета едет
    # в отметке доставки; прежние ключи без изменений.
    assert mark["cosmos_run"] == "run-0033" and mark["repeat"] is False
    assert seen["url"].endswith("/api/task-package/receive")
    # Заголовки контракта: sha по байтам zip, токен, версия, источник.
    hdr = {k.lower(): v for k, v in seen["headers"].items()}
    assert hdr["x-package-sha256"] == mark["sha"]
    assert hdr["x-channel-token"] == "т"
    assert hdr["x-spec-version"] == "1.14"
    assert hdr["x-source-run"] == "run-X"


def test_отказ_приёмника_не_ретраится(monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "т")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {},
            io.BytesIO("красный гейт".encode("utf-8")))

    mark = channel.send_task_package(
        b"z", "1.14", "run-X", _urlopen=fake_urlopen)
    assert mark["sent"] is False and calls["n"] == 1
    assert "HTTP 422" in mark["detail"]
    assert "пакет остаётся файлом" in mark["detail"]
    assert mark["detail"] == (
        "приёмник отказал: HTTP 422 — красный гейт; пакет остаётся файлом")


@pytest.mark.parametrize("verdict", ["REFUSED", "GATE_RED"])
@pytest.mark.parametrize("long_body", [False, True], ids=["original", "over-4k"])
def test_http_refusal_preserves_complete_received_diagnostics(monkeypatch, verdict, long_body):
    """Keep the received carrier, not merely an unmarked 400-char preview.

    The offline transport supplies the existing task-channel brief shape;
    this tests sender preservation, not the receiver's package/gate verdict.
    P0 §3.7 preserves refusal words, and the A08 preview boundary separates
    a shortened journal display from the complete delivery/detail carrier.
    """
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "synthetic-token")
    package = b"synthetic-package-bytes"
    reasons = [
        f"R-{number:02d}: компонент synthetic-{number:02d} отсутствует в исходной модели"
        + ("; синтетические подробности" * 32 if long_body else "")
        for number in range(13)
    ]
    tail = "КОНЕЦ-ПОЛНОЙ-ДИАГНОСТИКИ-13"
    payload = {
        "verdict": verdict, "mg": {},
        "mismatch": reasons if verdict == "GATE_RED" else [],
        "detail": "; ".join(reasons + [tail]),
        "sha": hashlib.sha256(package).hexdigest(),
        "spec_version": "1.21", "source_run": "synthetic-run",
        "already_accepted": False, "formulation": {},
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    words = raw.decode("utf-8")
    assert len(words) > 400 and tail not in words[:400]
    if long_body:
        # COSMOS Z-2: a 2000-character truncation survived the shorter fixture.
        # Exact mark equality below also detects any other lossy body rewrite.
        assert len(words) > 4096 and tail not in words[:4096]
    seen = []
    pauses = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.data)
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {"Content-Type": "application/json"},
            io.BytesIO(raw))

    mark = channel.send_task_package(
        package, "1.21", "synthetic-run", _urlopen=fake_urlopen,
        _sleep=pauses.append)

    assert seen == [package] and pauses == [], "a deterministic refusal is not retried"
    assert mark["sent"] is False and mark["attempts"] == 1
    missing = [reason for reason in reasons if reason not in mark["detail"]]
    assert missing == [], {"lost_reasons": missing, "actual": mark["detail"]}
    assert tail in mark["detail"], "the received diagnostic tail must remain available"
    assert mark == {
        "sent": False, "sha": hashlib.sha256(package).hexdigest(), "attempts": 1,
        "detail": f"приёмник отказал: HTTP 422 — {words}; пакет остаётся файлом",
    }, "preserve the existing delivery-mark shape and the entire received body"
    assert json.dumps(payload, ensure_ascii=False).encode("utf-8") == raw


def test_http_refusal_body_read_error_stays_unsent_without_retry(monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "synthetic-token")
    calls = []
    pauses = []

    class BrokenBody(io.BytesIO):
        def read(self, *args, **kwargs):
            raise OSError("synthetic response read failure")

    def fake_urlopen(req, timeout=None):
        calls.append(req.data)
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {}, BrokenBody())

    mark = channel.send_task_package(
        b"synthetic-package", "1.21", "synthetic-run", _urlopen=fake_urlopen,
        _sleep=pauses.append)
    assert calls == [b"synthetic-package"] and pauses == []
    assert mark == {
        "sent": False, "sha": hashlib.sha256(b"synthetic-package").hexdigest(),
        "attempts": 1, "detail": "приёмник отказал: HTTP 422; пакет остаётся файлом",
    }


def test_сетевые_ошибки_ретраятся_трижды(monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "т")
    pauses = []

    def fake_urlopen(req, timeout=None):
        raise OSError("связи нет")

    mark = channel.send_task_package(
        b"z", "1.14", "run-X", _urlopen=fake_urlopen,
        _sleep=pauses.append)
    assert mark["sent"] is False and mark["attempts"] == 3
    assert pauses == [2.0, 6.0], "растущая пауза между попытками"
    assert "3 попытки" in mark["detail"] or "3 попыток" in mark["detail"]
    assert "связи нет" in mark["detail"]
