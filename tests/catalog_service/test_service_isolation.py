# -*- coding: utf-8 -*-
"""T2: границы службы — импорты, места записи, тишина витрины.

Три шва, которые нельзя нарушать (§«Дорожки реализации» проекта):
служба не импортирует `cdpm`, `vmp`, `webapp`, `_data_paths`; при импорте
пакета не читаются ни файлы, ни переменные среды; в режиме просмотра
служба не пишет на диск НИЧЕГО — ни замка, ни журнала, ни рабочей папки.
"""
from __future__ import annotations

import ast
import builtins
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.catalog_service.conftest import ROOT

PACKAGE = ROOT / "catalog_service"
PY = sys.executable
#: Что службе позволено импортировать сверх стандартной библиотеки.
ALLOWED = {"template_catalog", "fastapi", "starlette", "pydantic", "uvicorn",
           "catalog_service"}
BANNED = {"cdpm", "vmp", "webapp", "_data_paths"}
#: Ленивый импорт линта маркеров — единственное исключение, и только
#: внутри функции одного модуля.
LAZY_ONLY = {"tools": "markers.py"}
#: Замороженные места записи службы: модуль → функция → вызовы.
#: Новое место записи — осознанная правка этого перечня, а не строка,
#: тихо появившаяся в обработчике.
WRITE_SITES = {
    ("app.py", "_work_dir_ready", "mkdir"),
    # Загрузка единым файлом: присланный файл кладётся во ВРЕМЕННУЮ папку
    # машины (tempfile.TemporaryDirectory) и уносится вместе с ней —
    # единственное место записи вне рабочей папки, папки каталога и
    # замка; оно названо в контракте (§5, POST /api/import) и судится
    # корнями в test_every_write_lands_under_the_declared_roots.
    ("app.py", "_import", "write_bytes"),
    ("journal.py", "append", "mkdir"),
    ("journal.py", "append", "open"),
    ("store.py", "write_draft", "mkdir"),
    ("store.py", "write_draft", "write_bytes"),
    ("store.py", "write_draft", "os.replace"),
    ("store.py", "delete_draft", "unlink"),
    ("store.py", "clear_drafts", "unlink"),
    ("store.py", "acquire_catalog_lock", "os.open"),
    # Шаг 6. Сборка вносит файл-кандидата конвейера в записи и убирает
    # его: путь строит сама служба (`catalog/candidates/<раздел>/<id>.json`),
    # снаружи он не принимается, и лежит он под папкой каталога — тот же
    # корень, что у остальных записей службы.
    ("request_queue.py", "drop_candidate", "unlink"),
    # Инструмент приёма очередей (`python -m catalog_service
    # import-requests`) — не служба: это отдельная программа рабочего
    # места владельца, и пишет она файлы-ответы РЯДОМ с файлами очереди
    # продукта (`<имя>.reply.json`), а под `catalog/` не пишет ничего —
    # каталог правит только служба, к которой инструмент ходит по HTTP.
    # Обещание «витрина не пишет ни байта» это не трогает: витрина
    # инструмента не запускает.
    ("import_requests.py", "run", "write_text"),
}
#: Методы пути, пишущие сами по себе.
_PATH_WRITE = {"write_bytes", "write_text", "mkdir", "unlink", "touch"}
#: Имена, которые пишут ТОЛЬКО у os/shutil: у строки `.replace` — не
#: запись, и путать их значило бы ловить ложные места.
_OS_WRITE = {"replace", "open", "makedirs", "remove", "rename", "rmtree"}


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE.glob("*.py"))


def test_import_graph_of_the_service_is_narrow():
    stdlib = set(sys.stdlib_module_names)
    checked = 0
    for py in _modules():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for full in names:
                root = full.split(".")[0]
                checked += 1
                assert root not in BANNED, f"{py.name}: import {full}"
                if root in LAZY_ONLY:
                    assert py.name == LAZY_ONLY[root], \
                        f"{py.name}: {root} допускается только в " \
                        f"{LAZY_ONLY[root]}"
                    assert _inside_function(tree, node), \
                        f"{py.name}: {root} обязан импортироваться лениво"
                    continue
                assert root in stdlib or root in ALLOWED, \
                    f"{py.name}: посторонний импорт {full}"
    print(f"модулей службы проверено: {len(_modules())}; импортов: {checked}")


def _inside_function(tree, target) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(child is target for child in ast.walk(node)):
                return True
    return False


def test_importing_the_package_reads_no_files_and_no_env():
    script = r"""
import builtins, io, os, sys
class NoEnv(dict):
    def __getitem__(self, k): raise AssertionError("environ read: " + str(k))
    def get(self, k, d=None): raise AssertionError("environ get: " + str(k))
    def __contains__(self, k): raise AssertionError("environ in: " + str(k))
def deny(*a, **kw): raise AssertionError("open at import: %r" % (a[:1],))
builtins.open = deny
io.open = deny
os.environ = NoEnv()
sys.path.insert(0, sys.argv[1])
import catalog_service
assert catalog_service.__version__
for name in ("cdpm", "vmp", "webapp", "_data_paths"):
    assert name not in sys.modules, name
print("ok")
"""
    res = subprocess.run([PY, "-c", script, str(ROOT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120,
                         env={"PYTHONUTF8": "1", "SYSTEMROOT": "C:\\Windows",
                              "PATH": ""})
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


def test_write_sites_are_frozen():
    """Все места записи службы — в замороженном перечне."""
    found = set()
    for py in _modules():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(func):
                name = _write_call(node)
                if name:
                    found.add((py.name, func.name, name))
    assert found == WRITE_SITES, (
        f"новые места записи: {sorted(found - WRITE_SITES)}; "
        f"исчезли: {sorted(WRITE_SITES - found)}")
    print(f"мест записи в службе: {len(found)}")


def _write_call(node) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name) and base.id in ("os", "shutil"):
            if func.attr in _OS_WRITE:
                return f"{base.id}.{func.attr}"
            return None
        if func.attr in _PATH_WRITE:
            return func.attr
    if isinstance(func, ast.Name) and func.id == "open":
        mode = ""
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        return "open" if any(c in mode for c in "wax+") else None
    return None


def test_view_mode_writes_nothing_at_all(make_service, monkeypatch):
    """Витрина не пишет на диск ни байта — ни замка, ни журнала, ни
    рабочей папки. Считаются все обращения к записи, не только свои."""
    svc = make_service(None)
    cat = svc.catalog()
    rid = str(next(iter(cat.families())))
    writes: list = []
    for name in ("write_bytes", "write_text", "mkdir", "unlink", "touch"):
        orig = getattr(Path, name)

        def counting(self, *a, _orig=orig, _n=name, **kw):
            writes.append(f"{_n}:{self}")
            return _orig(self, *a, **kw)
        monkeypatch.setattr(Path, name, counting)
    for mod, name in ((os, "replace"), (os, "open"), (os, "makedirs"),
                      (os, "remove")):
        orig = getattr(mod, name)

        def counting(*a, _orig=orig, _n=name, **kw):
            writes.append(f"os.{_n}:{a[:1]}")
            return _orig(*a, **kw)
        monkeypatch.setattr(mod, name, counting)
    real_open = builtins.open

    def watching(file, mode="r", *a, **kw):
        if any(c in str(mode) for c in "wax+"):
            writes.append(f"open:{file}")
        return real_open(file, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", watching)

    urls = ["/healthz", "/readyz", "/versionz", "/api/session", "/api/catalog",
            "/api/validate", "/api/records?section=families",
            "/api/records?section=criteria", f"/api/records/families/{rid}",
            "/api/drafts", "/api/dictionaries",
            "/api/dictionaries/violation_units", "/api/requests",
            "/api/executors", "/api/versions",
            "/api/versions/compare?a=current&b=current",
            # Сухой прогон выпуска — тоже чтение: он обязан отвечать и в
            # витрине, и не писать при этом ни байта.
            "/api/versions/release/checks", "/api/export",
            "/api/journal"]
    for url in urls:
        assert svc.get(url).status_code in (200, 503), url
    assert writes == [], f"витрина писала на диск: {writes}"
    assert not svc.settings.work_dir.exists()
    assert not svc.store.lock_path().exists() or \
        svc.store._lock_fd is None
    print(f"ручек чтения пройдено в просмотре: {len(urls)}; записей 0")


def test_every_write_lands_under_the_declared_roots(service, tmp_path,
                                                    monkeypatch):
    """Перечень мест записи (выше) говорит ГДЕ в коде пишут; этот гейт —
    КУДА. Полный обход ручек записи владельца под подменёнными
    `Path.write_*` / `os.replace` / `open`: каждый путь обязан лежать под
    рабочей папкой, папкой каталога, файлом замка либо во временной папке
    загрузки (её служба заводит сама и убирает за собой)."""
    paths: list[str] = []

    def watch(name, value):
        paths.append(f"{name}:{value}")

    for name in ("write_bytes", "write_text", "mkdir", "unlink", "touch"):
        orig = getattr(Path, name)

        def counting(self, *a, _orig=orig, _n=name, **kw):
            watch(_n, self)
            return _orig(self, *a, **kw)
        monkeypatch.setattr(Path, name, counting)
    for name in ("replace", "open", "makedirs", "remove"):
        orig = getattr(os, name)

        def counting(*a, _orig=orig, _n=name, **kw):
            watch(f"os.{_n}", a[0] if a else "")
            if _n == "replace" and len(a) > 1:
                watch("os.replace-to", a[1])
            return _orig(*a, **kw)
        monkeypatch.setattr(os, name, counting)
    real_open = builtins.open

    def watching(file, mode="r", *a, **kw):
        if any(c in str(mode) for c in "wax+"):
            watch("open", file)
        return real_open(file, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", watching)

    cat = service.catalog()
    rid = str(next(iter(cat.families())))
    rec = {k: v for k, v in cat.family(rid).items() if k != "history"}
    rec["title"] = rec["title"] + " (проба мест записи)"
    single = service.get("/api/export").content
    assert service.put(f"/api/drafts/families/{rid}",
                       json={"record": rec, "note": "проба"}).status_code == 200
    assert service.post("/api/versions/assemble",
                        json={"note": "проба мест записи"}).status_code == 200
    assert service.post("/api/import",
                        files={"file": ("single.json", single,
                                        "application/json")},
                        params={"force": 1}).status_code == 200
    assert service.delete("/api/drafts").status_code == 200
    service.store.acquire_catalog_lock()

    roots = [Path(service.settings.work_dir).resolve(),
             Path(service.settings.catalog_dir).resolve(),
             Path(service.store.lock_path()).resolve(),
             # Загрузка единым файлом кладёт присланный файл во временную
             # папку машины и уносит её за собой (§2.10): место названо.
             Path(tempfile.gettempdir()).resolve()]
    stray = []
    for row in paths:
        name, _, value = row.partition(":")
        if not value:
            continue
        try:
            target = Path(value).resolve()
        except (OSError, ValueError):                    # pragma: no cover
            stray.append(row)
            continue
        if not any(target == root or root in target.parents for root in roots):
            stray.append(row)
    assert not stray, f"запись мимо объявленных корней: {stray[:10]}"
    assert paths, "ни одной записи не замечено — обход ручек не сработал"
    print(f"обращений к записи на полном обходе: {len(paths)}; все под "
          f"рабочей папкой, папкой каталога, замком и временной папкой")


def test_view_over_a_work_dir_with_a_draft_shows_no_draft(make_service,
                                                          tmp_path):
    """Витрина над ТОЙ ЖЕ рабочей папкой (у владельца она одна на оба
    запуска) черновиков не показывает: карточка без `draft`, «что
    изменилось» пусто — как и говорят её счётчики."""
    owner = make_service("owner", work="общая-работа")
    cat = owner.catalog()
    rid = str(next(iter(cat.families())))
    rec = {k: v for k, v in cat.family(rid).items() if k != "history"}
    rec["title"] = rec["title"] + " (черновик владельца)"
    assert owner.put(f"/api/drafts/families/{rid}",
                     json={"record": rec, "note": "проба"}).status_code == 200
    view = make_service(None, folder=owner.folder, work="общая-работа")
    assert view.settings.work_dir == owner.settings.work_dir
    assert view.get("/api/session").json()["drafts"] == {
        "families": 0, "criteria": 0, "dictionaries": 0}
    card = view.get(f"/api/records/families/{rid}").json()
    assert card["draft"] is None and card["changes"] == []
    assert view.get("/api/drafts").json() == {"items": [], "suggested": None,
                                              "bindings_stop": None}
    assert owner.get(f"/api/records/families/{rid}").json()["draft"] is not None
    print("витрина над рабочей папкой с черновиком: карточка без черновика")


def test_service_env_is_isolated_by_the_autouse_fixture():
    """Autouse-фикстура `tests/conftest.py` уводит рабочую папку во
    временную и снимает роль: иначе набор судил бы права того, кто
    запустил pytest, и писал бы черновики владельцу."""
    assert os.environ.get("CATALOG_WORK_DIR"), "рабочая папка не уведена"
    assert "pytest" in os.environ["CATALOG_WORK_DIR"].replace("\\", "/") \
        or "Temp" in os.environ["CATALOG_WORK_DIR"]
    for name in ("CATALOG_ACTOR", "CATALOG_READ_ONLY", "CREW_CATALOG_URL",
                 "CREW_COSMOS_BINDINGS", "CREW_CDPM_BINDINGS",
                 "CATALOG_MARKERS_FILE", "CATALOG_MACHINE_TOKEN"):
        assert name not in os.environ, name
    from catalog_service.settings import Settings
    s = Settings.from_env([])
    assert s.mode == "view" and s.actor is None
    assert str(s.work_dir) == os.environ["CATALOG_WORK_DIR"]
    assert not s.machine_channel_on(), (
        "машинный канал обязан быть выключен: токен рабочего места снят")


def test_data_root_formula_repeats_data_paths(monkeypatch):
    """Формула корня данных повторена, а не импортирована (гейт границ);
    повтор обязан совпадать с оригиналом до знака."""
    import _data_paths
    from catalog_service import settings as st
    monkeypatch.delenv("CREW_DATA_DIR", raising=False)
    assert st.data_root() == _data_paths.data_root()
    monkeypatch.setenv("CREW_DATA_DIR", str(ROOT / "проба-данных"))
    assert st.data_root() == _data_paths.data_root()
