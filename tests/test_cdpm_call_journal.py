# -*- coding: utf-8 -*-
"""Журнал внешних вызовов (проект повтора, этап Р1): запись, отпечаток без
личности канала, очередь одинаковых запросов, ошибки, тела по содержимому
(gzip), параллельные нити, повтор из журнала (без вызова канала, отказ
словами с разницей текстов запроса), повтор заново, выключатель тел,
зрение, секреты, закрытие."""
import gzip
import json
import os
import threading

import pytest

from cdpm import actions_journal as aj
from cdpm import call_journal as cj
from cdpm import llm as llm_mod


@pytest.fixture(autouse=True)
def _fresh():
    cj.forget()
    aj.forget()
    yield
    cj.forget()
    aj.forget()


def _run(tmp_path, name="run-0001", state="извлечение идёт"):
    d = tmp_path / name
    d.mkdir()
    (d / "status.json").write_text(json.dumps({"state": state}, ensure_ascii=False),
                                   encoding="utf-8")
    return d


def _channel(model="m-test", role="extractor", klass="cloud"):
    calls: list = []

    def fn(prompt):
        calls.append(prompt)
        return "ответ: " + prompt[-3:]
    fn.identity = json.dumps({"role": role, "klass": klass, "provider": "cloud",
                              "model": model, "config_source": "site-default",
                              "effective": {"provider": "cloud", "model": model,
                                            "channel": "api"}},
                             ensure_ascii=False)
    fn.calls = calls
    return fn


def test_record_mode_writes_index_bodies_and_manifest(tmp_path):
    run = _run(tmp_path)
    fn = _channel()
    w = cj.wrap(run, fn, "extractor", "cloud", action_n=3)
    assert w.identity == fn.identity and w.journal_wrapped
    assert cj.wrap(run, w, "x", None) is w          # повторной обёртки нет
    assert w("вопрос 001") == "ответ: 001"
    assert w("вопрос 001") == "ответ: 001"          # тот же запрос — occurrence 2
    assert w("вопрос 002") == "ответ: 002"
    assert fn.calls == ["вопрос 001", "вопрос 001", "вопрос 002"]
    idx = cj.read_index(run)
    assert [r["n"] for r in idx] == [1, 2, 3]
    assert [r["occurrence"] for r in idx] == [1, 2, 1]
    assert idx[0]["input"]["fingerprint"] == idx[1]["input"]["fingerprint"]
    assert idx[0]["input"]["fingerprint"] != idx[2]["input"]["fingerprint"]
    assert idx[0]["role"] == "extractor" and idx[0]["klass"] == "cloud"
    assert idx[0]["action_n"] == 3 and idx[0]["stage"] == "извлечение"
    assert idx[0]["kind"] == "llm" and idx[0]["served_from"] is None
    assert idx[0]["identity"]["model"] == "m-test"
    assert idx[0]["identity"]["channel"] == "api"
    assert cj.read_body(run, idx[0]["input"]["prompt_sha256"]) == "вопрос 001".encode("utf-8")
    assert cj.read_body(run, idx[0]["reply_sha256"]) == "ответ: 001".encode("utf-8")
    assert idx[0]["reply_bytes"] == len("ответ: 001".encode("utf-8"))
    man = cj.manifest(run)
    assert man["schema"] == cj.SCHEMA and man["mode"] == "record"
    assert man["complete"] is True and man["degraded"] is None
    assert man["counts"]["llm"] == 3 and man["counts"]["errors"] == 0
    assert man["versions"]["roles"]["extractor/cloud"]["model"] == "m-test"
    assert man["versions"]["panel"]["python"]
    assert cj.count(run) == 3
    # Отпечаток — без личности канала: та же роль/класс/запрос → тот же.
    fp, psha, nbytes = cj.fingerprint("extractor", "cloud", "вопрос 001")
    assert fp == idx[0]["input"]["fingerprint"]
    assert psha == idx[0]["input"]["prompt_sha256"]
    assert nbytes == idx[0]["input"]["prompt_bytes"]
    other = _channel(model="другая-модель")
    w2 = cj.wrap(run, other, "extractor", "cloud")
    w2("вопрос 001")
    assert cj.read_index(run)[3]["input"]["fingerprint"] == fp
    assert cj.read_index(run)[3]["occurrence"] == 3


def test_fingerprint_depends_on_role_class_prompt_and_images():
    a = cj.fingerprint("extractor", "cloud", "п")[0]
    assert cj.fingerprint("extractor", "cloud", "п")[0] == a
    assert cj.fingerprint("auditor", "cloud", "п")[0] != a
    assert cj.fingerprint("extractor", "local", "п")[0] != a
    assert cj.fingerprint("extractor", "cloud", "п ")[0] != a
    assert cj.fingerprint("extractor", "cloud", "п", [{"sha256": "a" * 64}])[0] != a


def test_errors_are_recorded_and_reraised(tmp_path):
    run = _run(tmp_path)

    def bad(prompt):
        raise RuntimeError("сеть недоступна")
    w = cj.wrap(run, bad, "auditor", "cloud")
    with pytest.raises(RuntimeError):
        w("вопрос")
    r = cj.read_index(run)[0]
    assert r["error"] == {"type": "RuntimeError", "words": "сеть недоступна"}
    assert r["reply_sha256"] is None
    assert cj.manifest(run)["counts"]["errors"] == 1
    # Запрос записан и при отказе — по нему судят повтор.
    assert cj.read_body(run, r["input"]["prompt_sha256"]) == "вопрос".encode("utf-8")


def test_big_bodies_are_gzipped_and_readable(tmp_path):
    run = _run(tmp_path)
    big = "строка\n" * 20000                              # > 64 КБ
    w = cj.wrap(run, lambda p: "ок", "extractor", "cloud")
    w(big)
    r = cj.read_index(run)[0]
    sha = r["input"]["prompt_sha256"]
    gz = cj.journal_dir(run) / "bodies" / (sha + ".gz")
    assert gz.is_file() and not (cj.journal_dir(run) / "bodies" / sha).exists()
    assert gzip.decompress(gz.read_bytes()) == big.encode("utf-8")
    assert cj.read_body(run, sha) == big.encode("utf-8")


def test_parallel_threads_record_every_call(tmp_path):
    run = _run(tmp_path)
    w = cj.wrap(run, lambda p: "ответ " + p, "extractor", "cloud")
    errors: list = []

    def worker(k):
        try:
            for i in range(5):
                w(f"чанк {(k + i) % 3}")                  # одни и те же тела
        except Exception as e:                           # noqa: BLE001
            errors.append(repr(e))
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    idx = cj.read_index(run)
    assert len(idx) == 40 and sorted(r["n"] for r in idx) == list(range(1, 41))
    by_fp: dict = {}
    for r in idx:
        by_fp.setdefault(r["input"]["fingerprint"], []).append(r["occurrence"])
    assert len(by_fp) == 3
    for occ in by_fp.values():
        assert sorted(occ) == list(range(1, len(occ) + 1))
    assert cj.manifest(run)["counts"]["llm"] == 40


def _record_source(tmp_path):
    src = _run(tmp_path, "run-0001")
    fn = _channel()
    w = cj.wrap(src, fn, "extractor", "cloud")
    w("вопрос A")
    w("вопрос A")
    w("вопрос B")
    cj.close(src)
    return src


def test_replay_journal_serves_without_calling_channel(tmp_path):
    src = _record_source(tmp_path)
    child = _run(tmp_path, "run-0002")
    cj.set_mode(child, "replay-journal", src)
    assert cj.mode_of(child) == "replay-journal"

    def never(prompt):
        raise AssertionError("канал вызван при повторе из журнала")
    never.identity = _channel(model="другая").identity
    w = cj.wrap(child, never, "extractor", "cloud", action_n=2)
    assert w("вопрос A") == "ответ: с A"
    assert w("вопрос B") == "ответ: с B"
    assert w("вопрос A") == "ответ: с A"
    idx = cj.read_index(child)
    assert [r["served_from"] for r in idx] == ["journal"] * 3
    assert [r["source_n"] for r in idx] == [1, 3, 2]
    assert idx[0]["identity_source"]["model"] == "m-test"
    assert idx[0]["identity"]["model"] == "другая"       # своя личность записана
    man = cj.close(child)
    assert man["mode"] == "replay-journal" and man["counts"]["served_from_journal"] == 3
    assert man["counts"]["unconsumed"] == 0
    # Тела потомка — те же байты (по содержимому).
    assert cj.read_body(child, idx[0]["reply_sha256"]) == "ответ: с A".encode("utf-8")


def test_replay_journal_mismatch_names_first_differing_line(tmp_path):
    src = _run(tmp_path, "run-0001")
    w = cj.wrap(src, lambda p: "ок", "extractor", "cloud")
    w("строка 1\nстрока 2\nстрока 3")
    child = _run(tmp_path, "run-0002")
    cj.set_mode(child, "replay-journal", src)
    w2 = cj.wrap(child, lambda p: "не должен", "extractor", "cloud")
    with pytest.raises(cj.ReplayMismatch) as e:
        w2("строка 1\nстрока X\nстрока 3")
    words = str(e.value)
    assert "роль extractor" in words and "строке 2" in words
    assert "источник «строка 2»" in words and "повтор «строка X»" in words
    assert e.value.detail["nearest"]["n"] == 1
    r = cj.read_index(child)[0]
    assert r["error"]["type"] == "ReplayMismatch"
    # Очередь на отпечаток исчерпана — тоже расхождение.
    w2("строка 1\nстрока 2\nстрока 3")
    with pytest.raises(cj.ReplayMismatch):
        w2("строка 1\nстрока 2\nстрока 3")
    # Роли, которой у источника не было, — свои слова.
    w3 = cj.wrap(child, lambda p: "не должен", "arbiter", "cloud")
    with pytest.raises(cj.ReplayMismatch) as e2:
        w3("что угодно")
    assert "нет запросов этой роли" in str(e2.value)


def test_replay_journal_reproduces_recorded_error(tmp_path):
    src = _run(tmp_path, "run-0001")

    def bad(prompt):
        raise RuntimeError("сеть недоступна")
    with pytest.raises(RuntimeError):
        cj.wrap(src, bad, "auditor", "cloud")("вопрос")
    child = _run(tmp_path, "run-0002")
    cj.set_mode(child, "replay-journal", src)
    w = cj.wrap(child, lambda p: "не должен", "auditor", "cloud")
    with pytest.raises(llm_mod.LLMCallError) as e:
        w("вопрос")
    assert "(из журнала, вызов 1) RuntimeError: сеть недоступна" in str(e.value)
    assert cj.read_index(child)[0]["error"]["type"] == "RuntimeError"


def test_replay_journal_requires_source_journal(tmp_path):
    child = _run(tmp_path, "run-0002")
    with pytest.raises(ValueError):
        cj.set_mode(child, "replay-journal", tmp_path / "нет-такого")
    with pytest.raises(ValueError):
        cj.set_mode(child, "кривой-режим")


def test_replay_fresh_calls_channel_and_records(tmp_path):
    src = _record_source(tmp_path)
    child = _run(tmp_path, "run-0002")
    cj.set_mode(child, "replay-fresh", src)
    fn = _channel(model="свежая")
    w = cj.wrap(child, fn, "extractor", "cloud")
    assert w("вопрос A") == "ответ: с A"
    assert fn.calls == ["вопрос A"]
    r = cj.read_index(child)[0]
    assert r["served_from"] is None and r["identity"]["model"] == "свежая"
    assert cj.manifest(child)["counts"]["served_from_journal"] == 0


def test_bodies_switch_off_marks_journal_degraded(tmp_path, monkeypatch):
    monkeypatch.setenv(cj.ENV_BODIES, "0")
    run = _run(tmp_path)
    w = cj.wrap(run, lambda p: "ок", "extractor", "cloud")
    w("вопрос")
    r = cj.read_index(run)[0]
    assert r["input"]["prompt_sha256"] and r["reply_sha256"]
    assert not list((cj.journal_dir(run) / "bodies").iterdir())
    man = cj.manifest(run)
    assert man["complete"] is False and "отключены площадкой" in man["degraded"]
    assert cj.read_body(run, r["reply_sha256"]) is None
    # Закрытие не делает журнал полным.
    assert cj.close(run)["complete"] is False


def test_vision_wrapper_fingerprints_image(tmp_path):
    run = _run(tmp_path, state="заморозка идёт")
    img = tmp_path / "скан.jpg"
    img.write_bytes(b"\xff\xd8" + "картинка".encode("utf-8"))
    seen: list = []

    def vision(prompt, path):
        seen.append((prompt, path))
        return "распознано"
    w = cj.wrap_vision(run, vision, action_n=1)
    assert w("прочитай", img) == "распознано"
    assert seen == [("прочитай", img)]
    r = cj.read_index(run)[0]
    assert r["kind"] == "vision" and r["role"] == "vision" and r["stage"] == "заморозка"
    assert r["input"]["images"] == [{"name": "скан.jpg",
                                     "sha256": __import__("hashlib").sha256(
                                         b"\xff\xd8" + "картинка".encode("utf-8")).hexdigest()}]
    assert cj.manifest(run)["counts"]["vision"] == 1
    # Другой файл с тем же именем — другой отпечаток.
    img.write_bytes("другая".encode("utf-8"))
    w("прочитай", img)
    idx = cj.read_index(run)
    assert idx[0]["input"]["fingerprint"] != idx[1]["input"]["fingerprint"]


def test_journal_carries_no_environment_secrets(tmp_path, monkeypatch):
    secret = "s3cr3t-token-value-0xDEADBEEF"
    monkeypatch.setenv("CDPM_TEST_API_TOKEN", secret)
    monkeypatch.setenv("CDPM_TEST_SECRET_KEY", secret + "-key")
    run = _run(tmp_path)
    w = cj.wrap(run, lambda p: "ответ", "extractor", "cloud")
    w("вопрос без секретов")
    cj.close(run)
    for p in cj.journal_dir(run).rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if p.suffix == ".gz":
                data = gzip.decompress(data)
            assert secret.encode("utf-8") not in data, p


def test_close_writes_end_line_and_manifest(tmp_path):
    run = _run(tmp_path)
    assert cj.close(run) is None                         # журнала ещё нет
    cj.wrap(run, lambda p: "ок", "extractor", "cloud")("вопрос")
    man = cj.close(run)
    assert man["closed_at"] and man["complete"] is True
    lines = cj.index_path(run).read_bytes().decode("utf-8").splitlines()
    assert json.loads(lines[-1]) == {"kind": "end", "complete": True, "count": 1}
    assert cj.count(run) == 1                             # строка end не вызов
    # Состояние на процесс сбрасывается; счётчики восстанавливаются с диска.
    cj.forget(run)
    cj.wrap(run, lambda p: "ок", "extractor", "cloud")("вопрос")
    idx = cj.read_index(run)
    assert idx[-1]["n"] == 2 and idx[-1]["occurrence"] == 2
    assert os.environ.get(cj.ENV_BODIES) is None or cj.bodies_enabled()


def test_manifest_counters_survive_concurrent_readers(tmp_path):
    """Живой урок run-0088 → run-0091: ручки статуса читают манифест журнала
    в тот миг, когда нить стадии его заменяет (Windows: PermissionError), и
    обновление счётчика терялось молча. Теперь замена повторяется, а
    счётчики сходятся с индексом при параллельных читателях."""
    run = _run(tmp_path)
    w = cj.wrap(run, lambda p: "ответ " + p, "extractor", "cloud")
    stop = threading.Event()
    reads: list = []

    def reader():
        while not stop.is_set():
            m = cj.manifest(run)
            if m is not None:
                reads.append(m["counts"]["llm"])
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    errors: list = []

    def worker(k):
        try:
            for i in range(15):
                w(f"запрос {k}-{i}")
        except Exception as e:                           # noqa: BLE001
            errors.append(repr(e))
    workers = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    stop.set()
    for t in readers:
        t.join()
    assert errors == []
    assert reads and max(reads) <= 60
    assert cj.count(run) == 60
    assert cj.manifest(run)["counts"]["llm"] == 60


# ── Замена манифеста под открытыми читателями (CI среза eafe537, Windows) ──
from cdpm import file_replace as fr  # noqa: E402  (раздел после уроков CI)
import time  # noqa: E402
from pathlib import Path  # noqa: E402


def test_manifest_replace_wins_over_hot_readers(tmp_path):
    """Три горячих читателя манифеста не мешают писателю: 120 замен без
    исключений и без ожидания окна целиком (на Windows — семантика POSIX и
    FILE_SHARE_DELETE у читателя; красный CI eafe537: 40 попыток по 25–100 мс
    на двух ядрах кончались PermissionError)."""
    run = _run(tmp_path)
    mp = cj.manifest_path(run)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(b'{"counts": {"llm": 0}}')
    stop = threading.Event()
    seen: list = []

    def reader():
        while not stop.is_set():
            m = cj.manifest(run)
            if m is not None:
                seen.append(m["counts"]["llm"])
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    busy: list = []
    t0 = time.perf_counter()
    try:
        for i in range(1, 121):
            tmp = mp.with_name(f"{mp.name}.{i}.tmp")
            tmp.write_bytes(json.dumps({"counts": {"llm": i}}).encode("utf-8"))
            fr.replace_with_retry(tmp, mp, on_busy=busy.append)
    finally:
        stop.set()
        for t in readers:
            t.join()
    elapsed = time.perf_counter() - t0
    assert cj.manifest(run)["counts"]["llm"] == 120
    assert seen and max(seen) <= 120
    assert elapsed < 15, f"120 замен под читателями заняли {elapsed:.1f} с"


def test_replace_with_retry_gives_up_with_words(monkeypatch, tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_bytes(b"1")
    dst.write_bytes(b"0")
    calls: list = []

    def refuse(_s, _d):
        calls.append(1)
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(fr, "replace_file", refuse)
    with pytest.raises(PermissionError) as e:
        fr.replace_with_retry(src, dst, window_s=0.2)
    words = str(e.value)
    assert str(dst) in words and "0.2" in words and "попыток" in words
    assert len(calls) >= 2, "окно по времени должно дать больше одной попытки"


def test_atomic_write_removes_tmp_when_giving_up(monkeypatch, tmp_path):
    def refuse(_s, _d):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(fr, "replace_file", refuse)
    monkeypatch.setattr(fr, "REPLACE_WINDOW_S", 0.1)
    target = tmp_path / "manifest.json"
    with pytest.raises(PermissionError):
        cj._atomic_write(target, b"{}")
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"временные файлы остались: {leftovers}"


def test_atomic_write_content_addressed_accepts_existing_body(monkeypatch,
                                                              tmp_path):
    def refuse(_s, _d):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(fr, "replace_file", refuse)
    target = tmp_path / "abc"
    target.write_bytes(b"body")
    cj._atomic_write(target, b"body", content_addressed=True)
    assert target.read_bytes() == b"body"
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_manifest_reader_retries_transient_errors(monkeypatch, tmp_path):
    run = _run(tmp_path)
    mp = cj.manifest_path(run)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(b'{"counts": {"llm": 7}}')
    state = {"left": 2}

    def flaky(path):
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(13, "Access is denied")
        return Path(path).read_bytes()
    monkeypatch.setattr(fr, "read_bytes_shared", flaky)
    assert cj.manifest(run)["counts"]["llm"] == 7
    assert state["left"] == 0


def test_stage_of_a_synchronous_action_is_named_by_the_action(tmp_path):
    """Стадия вызова при СВОБОДНЫХ линиях статуса — слова текущего
    действия журнала (живая находка run-0166: вызов рубежа выпуска
    пакета лежал в индексе со стадией null, хотя запись о прогоне его
    считала). Занятая линия статуса старше: стадию называет она."""
    run = _run(tmp_path, state="пакет собран")          # линия свободна
    n = aj.note(run, "smt-verify", params={"origin": "гейт выпуска пакета"},
                sync=True)
    w = cj.wrap(run, _channel(role="smt_translator"), "smt_translator",
                "cloud", action_n=n)
    assert w("правило в SMT") == "ответ: SMT"
    idx = cj.read_index(run)
    # Те же слова, какими зовёт себя проверка у кнопки панели.
    assert [(r["action_n"], r["stage"]) for r in idx] == [(n, "SMT-проверка")]

    # Действие, которого словарь записи не знает, зовётся своим именем —
    # стадия не выдумывается и не теряется.
    other = aj.note(run, "своё-действие", sync=True)
    w2 = cj.wrap(run, _channel(role="worker"), "worker", "cloud",
                 action_n=other)
    assert w2("вопрос 001") == "ответ: 001"
    assert cj.read_index(run)[-1]["stage"] == "своё-действие"

    # Занятая линия статуса старше действия.
    (run / "status.json").write_text(
        json.dumps({"state": "извлечение идёт"}, ensure_ascii=False),
        encoding="utf-8")
    w3 = cj.wrap(run, _channel(), "extractor", "cloud", action_n=n)
    assert w3("вопрос 002") == "ответ: 002"
    assert cj.read_index(run)[-1]["stage"] == "извлечение"

    # Вызов вне действия и при свободных линиях — стадии нет (как было).
    (run / "status.json").write_text(
        json.dumps({"state": "пакет собран"}, ensure_ascii=False),
        encoding="utf-8")
    w4 = cj.wrap(run, _channel(), "extractor", "cloud")
    assert w4("вопрос 003") == "ответ: 003"
    assert cj.read_index(run)[-1]["stage"] is None
