# -*- coding: utf-8 -*-
"""Запись о прогоне под открытыми читателями (находка 12.09.2026 при
разборе плавающего теста повтора `test_cdpm_replay.py`).

`run_record._write_json` заменял `run-record.json` (и `declaration.json`)
через ОБЩИЙ временный файл `<имя>.tmp` и обычный `os.replace`. На Windows
такая замена отказывает «Access is denied» (WinError 5), пока хоть один
читатель держит файл открытым обычным `open()` — а так его держали и
свои читатели (`_read_json`: `on_disk`, панель дерева, ручки записи), и
чужие (антивирус, индексатор). Отказ ронял `refresh`, и с ним терялся
блок `resources` статуса (`webui._refresh_run_record` печатал «запись о
прогоне не обновлена» и выходил); два процесса панели над одной папкой
прогонов толкались на одном имени временного файла.

Закон: запись пишется через `cdpm.file_replace.replace_with_retry` с
временным файлом, своим у каждого писателя (`<имя>.<pid>-<нить>.tmp`,
как у статуса прогона `webui._write_status`), а читается через
`read_bytes_shared` (FILE_SHARE_DELETE) — как манифест журнала вызовов
(образец: `test_cdpm_call_journal.py::
test_manifest_counters_survive_concurrent_readers`).

Обратимость (Windows, прежний код): проба с читателями падает
PermissionError у писателя под тремя горячими читателями; проба с
восемью писателями — на общем имени временного файла; гейт по ast
краснеет на любой площадке. На Linux `os.replace` не отказывает, и на
прежнем коде там краснеют только гейт по ast и проверка имени.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import threading
import time
from pathlib import Path

from cdpm import file_replace as fr
from cdpm import run_record as rr


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _run(tmp_path: Path) -> Path:
    """Папка прогона панели без объявления (голова реконструируется по
    статусу и журналу действий): этого достаточно, чтобы `refresh`
    собрал и записал `run-record.json`."""
    run = tmp_path / "run-0042"
    _write(run / "status.json", {
        "state": "извлечено", "customer": "Проба", "data_class": "synthetic",
        "scope": "any", "created_at": "2026-09-12T07:00:00+02:00",
        "files": ["ПРАВИЛА.docx"], "extract_counters": {"rules": 3}})
    acts = [{"n": 1, "action": "extract", "at": "2026-09-12T07:00:00+02:00",
             "kind": "start", "files": []},
            {"n": 1, "kind": "end", "at": "2026-09-12T07:00:05+02:00",
             "duration_s": 5.0, "state_after": {"state": "извлечено"},
             "error": None}]
    (run / "journal").mkdir(parents=True)
    (run / "journal" / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in acts) + "\n",
        encoding="utf-8")
    return run


def _tmp_files(run: Path) -> list[str]:
    return sorted(p.name for p in run.iterdir() if p.name.endswith(".tmp"))


def test_record_refresh_survives_hot_readers(tmp_path):
    """Три горячих читателя записи (`on_disk` — тот же путь, каким её
    читают панель дерева и ручки записи) не мешают писателю: 60
    обновлений `refresh` без исключений и без ожидания окна целиком; а
    читатель всякий раз видит ЦЕЛУЮ запись — ни «записи нет», ни
    рваного JSON (замена атомарна, файл создан до старта читателей).

    Прежний код на Windows: `os.replace` под открытым читателем —
    PermissionError, `refresh` падает на первых же обновлениях."""
    run = _run(tmp_path)
    first = rr.refresh(run)
    run_id = first["declaration"]["run_id"]
    assert rr.on_disk(run) is not None
    stop = threading.Event()
    seen: list = []              # что вернул on_disk: None либо номер записи
    reader_errors: list = []

    def reader():
        try:
            while not stop.is_set():
                rec = rr.on_disk(run)
                seen.append(None if rec is None
                            else rec["declaration"]["run_id"])
        except Exception as e:                           # noqa: BLE001
            reader_errors.append(repr(e))
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    writer_errors: list = []
    t0 = time.perf_counter()
    try:
        for _ in range(60):
            try:
                rr.refresh(run)
            except Exception as e:                       # noqa: BLE001
                writer_errors.append(repr(e))
                break
    finally:
        stop.set()
        for t in readers:
            t.join()
    elapsed = time.perf_counter() - t0
    assert writer_errors == [], writer_errors
    assert reader_errors == [], reader_errors
    assert seen and None not in seen, \
        f"читатель видел «записи нет» {seen.count(None)} раз из {len(seen)}"
    assert set(seen) == {run_id}
    assert rr.on_disk(run)["declaration"]["run_id"] == run_id
    assert _tmp_files(run) == []
    assert elapsed < 20, f"60 обновлений под читателями заняли {elapsed:.1f} с"


def test_record_temp_file_name_carries_process_and_thread(tmp_path,
                                                          monkeypatch):
    """Имя временного файла замены — `<имя>.<pid>-<нить>.tmp`, как у
    статуса прогона (`webui._write_status`), и замена идёт через
    `cdpm.file_replace.replace_with_retry`; после записи временных
    файлов в папке нет."""
    run = tmp_path / "run-0001"
    run.mkdir()
    path = run / rr.RECORD_FILE
    names: list = []
    real = fr.replace_with_retry

    def spy(src, dst, **kw):
        names.append(Path(src).name)
        return real(src, dst, **kw)
    # Перехват — на самом модуле `cdpm.file_replace` (запись зовёт его по
    # псевдониму): запись мимо него оставляет `names` пустым, и краснеет
    # собственное утверждение пробы, а не доступ к псевдониму.
    monkeypatch.setattr(fr, "replace_with_retry", spy)
    rr._write_json(path, {"n": 1})
    assert names == [f"{rr.RECORD_FILE}.{os.getpid()}-"
                     f"{threading.get_ident()}.tmp"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"n": 1}
    assert _tmp_files(run) == []


def test_record_writers_do_not_share_the_temp_file(tmp_path):
    """Восемь нитей, пишущих одну запись разом, не толкаются на одном
    имени временного файла: ни исключений, ни оставленных временных
    файлов, а в файле — целая запись той нити, что закончила последней
    (её последняя, сороковая, запись).

    Прежний код на Windows: общий `<имя>.tmp` — вторая нить открывает
    его на запись, пока первая переименовывает, и `os.replace` падает
    PermissionError, либо файл первой уезжает из-под второй."""
    run = tmp_path / "run-0002"
    run.mkdir()
    path = run / rr.RECORD_FILE
    errors: list = []

    def writer(k: int):
        try:
            for i in range(40):
                rr._write_json(path, {"writer": k, "i": i})
        except Exception as e:                           # noqa: BLE001
            errors.append(repr(e))
    threads = [threading.Thread(target=writer, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got["writer"] in range(8) and got["i"] == 39, got
    assert _tmp_files(run) == []


def test_record_reader_retries_the_instant_of_replacement(tmp_path,
                                                          monkeypatch):
    """Повтор чтения при PermissionError — миг замены на площадке без
    семантики POSIX (запасной `os.replace`): три отказа подряд — документ
    отдан с четвёртого открытия; отказ не проходит — после шести попыток
    None, а не исключение и не вечное ожидание; иной OSError (файла нет)
    — None с первого раза, без повторов. На NTFS и Linux повтор не
    срабатывает вовсе (замена атомарна), поэтому клауза судится подменой
    чтения (находка неавторского ревью 13.09.2026: клауза без пробы)."""
    path = tmp_path / rr.RECORD_FILE
    path.write_text(json.dumps({"n": 7}), encoding="utf-8")
    calls: list = []
    real = fr.read_bytes_shared

    def flaky(p):
        calls.append(Path(p).name)
        if len(calls) <= 3:
            raise PermissionError(13, "миг замены")
        return real(p)
    monkeypatch.setattr(fr, "read_bytes_shared", flaky)
    assert rr._read_json(path) == {"n": 7}
    assert len(calls) == 4, calls

    calls.clear()

    def stuck(p):
        calls.append(Path(p).name)
        raise PermissionError(13, "чужой процесс держит файл")
    monkeypatch.setattr(fr, "read_bytes_shared", stuck)
    t0 = time.perf_counter()
    assert rr._read_json(path) is None
    assert len(calls) == 6, calls
    assert time.perf_counter() - t0 < 5

    calls.clear()
    monkeypatch.setattr(fr, "read_bytes_shared",
                        lambda p: (calls.append(Path(p).name), real(p))[1])
    assert rr._read_json(tmp_path / "нет.json") is None
    assert len(calls) == 1, calls


def test_record_files_go_only_through_file_replace():
    """Гейт кода (по ast, не по grep): в модуле записи нет ни одного
    `os.replace`; единственный писатель документов — `_write_json`, и он
    зовёт `replace_with_retry`, строя имя временного файла из номера
    процесса и нити; `_read_json` читает `read_bytes_shared`, а не
    `read_text`/`open`. Краснеет на любой площадке, где закон снят, — и
    на Linux, где пробы с читателями зелены и на прежнем коде."""
    tree = ast.parse(inspect.getsource(rr))
    replaces = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "replace"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "os"]
    assert replaces == [], f"os.replace в записи о прогоне: строки {replaces}"
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def called(fn) -> set[str]:
        out: set[str] = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                if isinstance(n.func, ast.Attribute):
                    out.add(n.func.attr)
                elif isinstance(n.func, ast.Name):
                    out.add(n.func.id)
        return out
    document_writers = sorted(name for name, fn in fns.items()
                              if "write_text" in called(fn)
                              or "write_bytes" in called(fn))
    assert document_writers == ["_write_json"], document_writers
    w = called(fns["_write_json"])
    assert {"replace_with_retry", "getpid", "get_ident"} <= w, sorted(w)
    r = called(fns["_read_json"])
    assert "read_bytes_shared" in r, sorted(r)
    assert not ({"read_text", "read_bytes", "open"} & r), sorted(r)
