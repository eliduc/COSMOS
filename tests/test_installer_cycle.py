# -*- coding: utf-8 -*-
"""Сквозной цикл: установка → снос, и что установщик записывает о себе.

Здесь закреплены дефекты, которые НЕ ловятся проверкой отдельных частей:
их видно только когда установка и снос отрабатывают целиком. Установка
идёт из поддельного местного репозитория, без сети; тяжёлые шаги
(загрузка JDK и Maven, сборка решателя) не разрешены и пропускаются.

Ничего настоящего не затрагивается: и цель, и каталог журналов лежат во
временном каталоге теста.

Проверка установки (раздел «ПРОВЕРКА») проходит ЦЕЛИКОМ — импорт
модулей, подъём приложения его пусковым файлом, отклик на порту, пароль
входа, каталог шаблонов и его служба — на заглушках из стандартной
библиотеки: приложение, uvicorn и модули из перечня импортов проверки
лежат в поддельном репозитории строками этого файла. Без них установщик
до проверки не доходил (шаг «зависимости» падал на поддельном
pyproject.toml), и весь путь проверки жил без единого теста.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import install_cosmos as ic                                      # noqa: E402
import uninstall_cosmos as un                                    # noqa: E402
import catalog_copies as cc                                      # noqa: E402

# Заглушка службы каталога шаблонов: только стандартная библиотека.
# В поддельном окружении нет ни fastapi, ни uvicorn, а проверка
# установки обязана поднять службу пусковым файлом и получить отклик —
# иначе проверялось бы не то, чем будут пользоваться.
CATALOG_SERVICE_INIT = '''# -*- coding: utf-8 -*-
"""Заглушка службы каталога шаблонов для теста цикла установки."""
__version__ = "0.0.0-test"
'''

CATALOG_SERVICE_APP = '''# -*- coding: utf-8 -*-
"""Заглушка приложения службы: /healthz средствами stdlib."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode("utf-8")
        code = 200 if self.path.rstrip("/") in ("/healthz", "") else 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(host, port):
    ThreadingHTTPServer((host, port), Handler).serve_forever()
'''

CATALOG_SERVICE_MAIN = '''# -*- coding: utf-8 -*-
"""Точка входа заглушки: python -m catalog_service --port N --read-only."""
import argparse

from .app import serve


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8105)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--actor", default=None)
    ap.add_argument("--read-only", action="store_true")
    a = ap.parse_args(argv)
    print("catalog service (stub) on %s:%d" % (a.host, a.port), flush=True)
    serve(a.host, a.port)


if __name__ == "__main__":
    main()
'''

# pyproject поддельного репозитория. Перечень пакетов — ЯВНЫЙ: при
# авто-обнаружении setuptools отказывает «Multiple top-level packages
# discovered in a flat-layout» (пакетов верхнего уровня несколько), шаг
# «зависимости» (`pip install -e .`) падает, и установщик до проверки
# не доходит вовсе — так и жила дыра покрытия: «шагов выполнено: 13 из
# 14», раздела «ПРОВЕРКА» в выводе нет, verify() не вызвана.
# Заглушка `uvicorn` в перечень не входит: пусковой файл делает
# `cd /d <цель>`, и `python -m uvicorn` находит её по текущему
# каталогу — тем же путём проверка импортирует и модули приложения.
PYPROJECT = '''[project]
name = "cosmos-proba"
version = "0"

[project.optional-dependencies]
dev = []

[tool.setuptools]
packages = ["webapp", "cdpm", "catalog_service", "template_catalog", "vmp"]
'''

# Заглушка приложения. Пароль входа делается так же, как у настоящего
# (webapp/auth.py, вызов из lifespan в webapp/app.py): при ПЕРВОМ
# ЗАПУСКЕ службы, а не при импорте, — иначе пароль ушёл бы в вывод
# проверки импорта, а не в журнал приложения, откуда его читает
# установщик, и проверка честно сказала бы «пароль не найден».
WEBAPP_APP = '''# -*- coding: utf-8 -*-
"""Заглушка приложения для теста цикла установки.

Приложение — то, что заглушка uvicorn отдаёт по HTTP: маршрут -> (код,
документ). Пароль входа печатается один раз при первом запуске службы
в том же виде, что у настоящего приложения (webapp/auth.py).
"""
import hashlib
import json
import secrets
from pathlib import Path

AUTH_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "auth.json"


class App:
    def startup(self):
        if AUTH_CONFIG_PATH.exists():
            return
        token = secrets.token_urlsafe(32)
        AUTH_CONFIG_PATH.write_text(json.dumps(
            {"token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest()}),
            encoding="utf-8")
        # flush обязателен: службу гасят taskkill /F, буфер пропал бы.
        print("  WEBAPP ACCESS TOKEN (shown once; only its sha256 is stored):",
              flush=True)
        print("      %s" % token, flush=True)

    def __call__(self, path):
        if path.rstrip("/") == "/api/instances":
            return 200, []
        return 404, {"detail": "Not Found"}


app = App()
'''

# Заглушка uvicorn: пусковой файл зовёт `python -m uvicorn webapp.app:app
# --host … --port … --log-level warning`, и в поддельном окружении на эту
# командную строку обязан ответить сервер из стандартной библиотеки —
# проверка поднимает приложение именно пусковым файлом.
UVICORN_INIT = '''# -*- coding: utf-8 -*-
"""Заглушка uvicorn для теста цикла установки: сервер стандартной
библиотеки под командную строку пускового файла."""
'''

UVICORN_MAIN = '''# -*- coding: utf-8 -*-
"""Точка входа заглушки uvicorn: та же командная строка, что у настоящего."""
import argparse
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("app")                          # webapp.app:app
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    mod_name, _, attr = a.app.partition(":")
    app = getattr(importlib.import_module(mod_name), attr)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, doc = app(self.path)
            body = json.dumps(doc).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    app.startup()                                   # как lifespan у настоящего
    print("uvicorn (stub) %s on %s:%d" % (a.app, a.host, a.port), flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
'''

# Модули из перечня импортов проверки установки (install_cosmos.verify):
# заглушки из одной строки документации — проверка импортирует их по
# имени, и без них она честно отказала бы «приложение не импортируется».
# Перечень повторяет перечень установщика намеренно: новый модуль в
# проверке без заглушки здесь уронит тест словами установщика — дописать
# заглушку осознанно лучше, чем не заметить, что проверка перестала
# проходить.
STUB_MODULE = '''# -*- coding: utf-8 -*-
"""Заглушка модуля %s для теста цикла установки: проверка установки
импортирует его по имени; содержимого у заглушки нет."""
'''
VERIFY_STUBS = ("webapp/__init__.py", "webapp/task_package.py",
                "webapp/solution_export.py", "webapp/flow.py",
                "cdpm/webui.py", "vmp/__init__.py", "vmp/readiness_verify.py",
                "vmp/registry_check.py", "vmp/template_registry.py",
                "_select_engines.py", "_roster_cpsat.py")

pytestmark = pytest.mark.skipif(os.name != "nt",
                                reason="комплект установки рассчитан на Windows")


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)


def make_fake_repo(src: Path) -> Path:
    """Собрать поддельный репозиторий в каталоге src (см. fake_repo).

    Обычная функция, а не только фикстура: ту же установку можно
    прогнать руками и прочитать полный вывод установщика.
    """
    (src / "webapp" / "config").mkdir(parents=True)
    (src / "webapp" / "app.py").write_text(WEBAPP_APP, encoding="utf-8")
    (src / "webapp" / "config" / "airline_default.json.example").write_text(
        "{}\n", encoding="utf-8")
    # Ревизия 1.18.5 (S06-R2): образец носителя раскладки моделей ролей —
    # установка обязана превратить его в <цель>-data/agents-default.json.
    (src / "cdpm").mkdir()
    (src / "cdpm" / "agents-default.json.example").write_text(
        '{"form_version": 1, "worker": {"channels": {"cloud": '
        '{"provider": "cloud", "model": "test-model"}}}}\n', encoding="utf-8")
    # Шаг 7 каталога: мост CDPM и его команда-проба. Кладём ровно два
    # файла — пакет `cdpm` целиком в поддельный комплект не влезает (у
    # него зависимости), а проба обязана работать без них: мост читает
    # только `template_catalog` и стандартную библиотеку.
    for name in ("__init__.py", "catalog_bridge.py"):
        shutil.copy2(REPO / "cdpm" / name, src / "cdpm" / name)
    (src / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    # Заглушки под проверку установки: модули из её перечня импортов и
    # uvicorn под командную строку пускового файла.
    for rel in VERIFY_STUBS:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(STUB_MODULE % rel[:-3].replace("/", "."),
                     encoding="utf-8")
    (src / "uvicorn").mkdir()
    (src / "uvicorn" / "__init__.py").write_text(UVICORN_INIT, encoding="utf-8")
    (src / "uvicorn" / "__main__.py").write_text(UVICORN_MAIN, encoding="utf-8")
    # Шаг 5 каталога шаблонов: служба каталога, её читатель и годный
    # каталог данных. Установка обязана создать пусковой файл службы,
    # а проверка — проверить каталог и получить отклик службы.
    (src / "catalog_service").mkdir()
    (src / "catalog_service" / "__init__.py").write_text(
        CATALOG_SERVICE_INIT, encoding="utf-8")
    (src / "catalog_service" / "app.py").write_text(
        CATALOG_SERVICE_APP, encoding="utf-8")
    (src / "catalog_service" / "__main__.py").write_text(
        CATALOG_SERVICE_MAIN, encoding="utf-8")
    shutil.copytree(REPO / "template_catalog", src / "template_catalog",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # Каталог — копией помощника: живой каталог репозитория тесты не
    # трогают, а копия годна по построению (отпечаток пересчитан).
    cc.copy_catalog(src, bindings=False)
    _git("init", "-q", cwd=src)
    _git("add", "-A", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "t",
         cwd=src)
    return src


@pytest.fixture(scope="module")
def fake_repo(tmp_path_factory):
    """Крошечный репозиторий вместо настоящего: установка должна пройти
    свои шаги и ВСЮ проверку, не выходя в сеть за чем-либо, кроме pip."""
    return make_fake_repo(tmp_path_factory.mktemp("src") / "fake-repo")


def free_port() -> int:
    """Свободный порт на момент вызова — для служб проверки установки.

    Фиксированный номер службы каталога (8105) на рабочем месте владельца
    занят его же местной службой: проверка установки била по чужому
    слушателю и краснела в каждой цепочке CR (09.09.2026); порт — решение
    замысла (ключ --catalog-port), а тест берёт свободный."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_installer(tmp_path, fake_repo, target, state, port="8399",
                  catalog_port=None):
    """Установка без диалога; возвращает (код возврата, полный вывод).

    Код возврата — часть проверки: 0 у установщика значит «проверка
    пройдена», 3 — «установка НЕ подтверждена»."""
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"lang": "ru", "target": str(target)}),
                       encoding="utf-8")
    env = dict(os.environ, LOCALAPPDATA=str(state))
    cmd = [sys.executable, str(REPO / "install_cosmos.py"), "--dev-legacy",
           "--target", str(target), "--repo", str(fake_repo),
           "--answers", str(answers), "--port", port]
    if catalog_port:
        cmd += ["--catalog-port", str(catalog_port)]
    p = subprocess.run(cmd, capture_output=True, env=env, timeout=900)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def run_uninstaller(target, state, *extra):
    env = dict(os.environ, LOCALAPPDATA=str(state))
    p = subprocess.run(
        [sys.executable, str(REPO / "uninstall_cosmos.py"),
         "--target", str(target), "--lang", "ru", *extra],
        capture_output=True, env=env, cwd=str(Path.home()), timeout=900)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


@pytest.fixture(scope="module")
def installed(tmp_path_factory, fake_repo):
    """ОДНА установка на весь файл.

    Установка занимает десятки секунд (окружение python, зависимости),
    поэтому неразрушающие проверки делят её между собой: восемь
    установок подряд удорожили бы набор тестов без единой новой находки.
    Тесты, которым нужно сносить, ставят себе отдельно.
    """
    base = tmp_path_factory.mktemp("cycle")
    state = base / "state"
    target = base / "cosmos"
    catalog_port = free_port()
    rc, out = run_installer(base, fake_repo, target, state,
                            catalog_port=catalog_port)
    if not target.exists():
        pytest.skip("установка не прошла в этой среде: %s" % out[-400:])
    return {"base": base, "state": state, "target": target, "out": out,
            "rc": rc, "catalog_port": catalog_port}


def test_layout_carrier_created_from_sample_and_journaled(installed):
    """Ревизия 1.18.5 (S06-R2; GPT-060 P2): установка создаёт носитель
    раскладки моделей ролей из образца в каталоге данных и записывает
    его в журнал как созданный объект — иначе снос по журналу считал бы
    каталог данных непустым и отказывал."""
    state, target, out = installed["state"], installed["target"], installed["out"]
    carrier = target.parent / (target.name + "-data") / "agents-default.json"
    assert carrier.is_file(), out[-1500:]
    assert json.loads(carrier.read_text(encoding="utf-8"))["worker"]["channels"]["cloud"]["model"] == "test-model"
    assert "раскладка моделей ролей из образца" in out
    js = journals(state)
    assert js, "журнал установки не найден"
    rows = [json.loads(line) for p in js
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    created = [r for r in rows if r.get("запись") == "создан объект"
               and r.get("вид") == "носитель раскладки ролей"]
    assert created, rows
    assert Path(created[0]["путь"]) == carrier
    # Отпечаток байтов в журнале — снос удалит носитель, только пока он
    # тот же (GPT-062): изменённый владельцем носитель — его данные.
    import hashlib
    assert created[0]["sha256"] == hashlib.sha256(carrier.read_bytes()).hexdigest()


def journals(state):
    d = state / "cosmos-install"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def manifests(state):
    d = state / "cosmos-install"
    return sorted(d.glob("*.jsonl.gz")) if d.is_dir() else []


# ---------------------------------------------------------------- проверки

def test_install_writes_journal_and_manifest(installed):
    """Контрольные точки: без них снос вынужден угадывать состав
    установки, а угадывание — риск задеть чужое."""
    state, target = installed["state"], installed["target"]
    js = journals(state)
    assert len(js) == 1, "журнал установки не записан"
    rows = [json.loads(l) for l in js[0].read_text(encoding="utf-8").splitlines()
            if l.strip()]
    kinds = [r["запись"] for r in rows]
    assert "начало" in kinds
    assert "создан объект" in kinds
    created = [r["путь"] for r in rows if r["запись"] == "создан объект"]
    assert str(target) in created, "каталог цели не отмечен как созданный"

    ms = manifests(state)
    assert len(ms) == 1, "опись установленного не записана"
    with gzip.open(ms[0], "rt", encoding="utf-8") as fh:
        head = json.loads(fh.readline())
    assert head["корень"] == str(target)
    assert head["формат"] == 2


def test_manifest_is_compact(installed):
    """Опись живёт в профиле пользователя, поэтому её размер — не
    мелочь: относительные пути, короткая строка, сжатие."""
    state, target = installed["state"], installed["target"]
    man = manifests(state)[0]
    objects = sum(1 for _ in target.rglob("*"))
    assert objects > 0
    # На объект дерева — заметно меньше сотни байт описи.
    assert man.stat().st_size < objects * 100, (
        "опись слишком велика: %d байт на %d объектов"
        % (man.stat().st_size, objects))


def test_local_clone_leaves_no_hardlinks_into_source(installed):
    """ДЕФЕКТ: git при клонировании из МЕСТНОГО пути делает объекты
    жёсткими ссылками на исходный репозиторий, и удаление трогало бы
    общую для обоих имён запись файловой системы — то есть чужой файл."""
    target = installed["target"]
    multi = [p for p in (target / ".git").rglob("*")
             if p.is_file() and p.stat().st_nlink > 1]
    assert multi == [], "объекты клона делят запись с исходным репозиторием"


def test_catalog_service_launcher_created_and_journaled(installed):
    """Шаг 5 каталога: служба каталога приезжает вместе с исходными
    текстами, но открыть её заказчику было нечем. Пусковой файл обязан
    быть создан, нести свой порт и попасть в журнал ОТДЕЛЬНЫМ видом:
    порт приложения снос берёт у записи вида «пусковой файл», и общее
    имя увело бы имя задачи и правило брандмауэра на чужой номер."""
    state, target, out = installed["state"], installed["target"], installed["out"]
    launcher = target / "run-catalog.cmd"
    assert launcher.is_file(), out[-1500:]
    body = launcher.read_text(encoding="utf-8")
    assert "-m catalog_service" in body
    assert "--port %d" % installed["catalog_port"] in body
    assert "--read-only" in body, "на установке заказчика — только просмотр"
    assert "catalog-server.log" in body

    rows = [json.loads(line) for p in journals(state)
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    created = [r for r in rows if r.get("запись") == "создан объект"
               and r.get("вид") == "пусковой файл службы каталога"]
    assert created, [r.get("вид") for r in rows
                     if r.get("запись") == "создан объект"]
    assert Path(created[0]["путь"]) == launcher
    assert int(created[0]["порт"]) == installed["catalog_port"]
    # Номер службы каталога не пересекается с портом приложения (тест
    # ставит его на 8399) и с портом панели данных.
    assert ic.CATALOG_PORT not in ic.PORT_RANGE
    assert ic.CATALOG_PORT != ic.CDPM_PORT
    app_ports = {int(r["порт"]) for r in rows
                 if r.get("запись") == "создан объект"
                 and r.get("вид") == "пусковой файл" and r.get("порт")}
    assert ic.CATALOG_PORT not in app_ports, app_ports


def test_catalog_is_checked_and_service_answers(installed, capsys):
    """Проверка установки судит сам каталог обещанной обёрткой читателя
    и поднимает службу ЕЁ ПУСКОВЫМ ФАЙЛОМ — тем, чем будет пользоваться
    человек, а не интерпретатором напрямую.

    Общая проверка установки проходит целиком в самой установке (см.
    test_install_is_verified_end_to_end). Здесь та же часть зовётся
    напрямую ради того, чего по печатному выводу не видно: возвращаемое
    число проверок, пустые заметки, отпущенный порт. Отдельный вызов
    доказывает ровно то, что добавлено: «ошибок 0» посчитано, отклик
    получен, процесс погашен.

    Шаг 7 каталога: тем же вызовом проверяется проба МОСТА CDPM — той
    самой командой `python -m cdpm.catalog_bridge`, что стоит в
    чек-листе раската: для панели данных каталог шаблонов —
    единственный источник словарей правил, и установка не из клона
    отказала бы у заказчика при первом нажатии.
    """
    target = installed["target"]
    py = target / ".venv/Scripts/python.exe"
    assert py.exists()
    notes: list[str] = []
    done = ic._verify_catalog(target, py, notes)
    out = capsys.readouterr().out
    assert notes == [], notes
    assert done == 3, out          # каталог, мост CDPM и отклик службы
    assert "каталог шаблонов: ошибок 0" in out, out
    assert "отклик службы каталога" in out, out
    # Порт пробы — порт пускового файла (свободный порт замысла), а не
    # 8105 по умолчанию: иначе зелёный здесь давала бы чужая живая
    # служба на 8105 (CI № 73, 09.09.2026 — красный на чистом раннере).
    assert ("отклик службы каталога (пусковой файл, порт %d)"
            % installed["catalog_port"]) in out, out
    assert "служба каталога не входит в комплект" not in out
    # Проба моста: версия и первые 12 знаков отпечатка — те же, что у
    # каталога комплекта (сверять площадки между собой можно строкой).
    bridge_line = [ln for ln in out.splitlines() if "мост CDPM" in ln]
    assert len(bridge_line) == 1, out
    assert "мост CDPM не входит в комплект" not in bridge_line[0]
    got = json.loads((target / "catalog" / "catalog.json")
                     .read_text(encoding="utf-8"))
    assert got["version"] in bridge_line[0], bridge_line
    assert got["fingerprint"][:12] in bridge_line[0], bridge_line
    # Процесс службы не пережил проверку: гасится дерево целиком.
    snap = un.snapshot_processes()
    ours = un.classify_processes(
        snap, target, [str(target / n) for n in un.LAUNCHERS])["наши"]
    assert ours == [], "после проверки остались процессы службы: %r" % ours
    assert ic._port_free(installed["catalog_port"]), \
        "порт службы каталога не отпущен"


def test_catalog_probe_port_comes_from_launcher(installed, capsys, monkeypatch,
                                                tmp_path):
    """Урок CI № 73 (09.09.2026): проба отклика службы каталога брала
    порт по умолчанию (8105), а пусковой файл ставил службу на порт из
    замысла — локально красное скрывала чужая живая служба на 8105,
    на чистом раннере тест был красным. Носитель порта у пробы один —
    пусковой файл; порт, названный проверке, обязан с ним совпасть,
    иначе заметка с обоими числами и отказ от пробы, а не угадывание;
    пусковой файл без порта — тоже заметка, не проба 8105."""
    target = installed["target"]
    py = target / ".venv/Scripts/python.exe"
    launcher = target / "run-catalog.cmd"
    assert ic._catalog_launcher_port(launcher) == installed["catalog_port"]
    assert ic._catalog_launcher_port(target / "net-takogo.cmd") is None
    # Расхождение носителей: заметка с обоими числами, службы не поднимали.
    notes: list[str] = []
    wrong = installed["catalog_port"] + 1
    done = ic._verify_catalog(target, py, notes, None, wrong)
    out = capsys.readouterr().out
    assert done == 3, out
    assert len(notes) == 1, notes
    assert str(wrong) in notes[0] and str(installed["catalog_port"]) in notes[0]
    assert "отклик службы каталога (пусковой файл" not in out, out
    assert ic._port_free(installed["catalog_port"])
    # Пусковой файл без порта: заметка, службы не поднимали. Установленный
    # пусковой файл НЕ трогаем даже байт в байт: опись сноса (общие
    # установка и снос для всех тестов модуля) судит и время записи —
    # переписанный файл стал бы «посторонним». Чтение порта проверяется
    # на отдельном файле, ветка проверки — подменой читателя.
    stray = tmp_path / "run-catalog.cmd"
    stray.write_text("@echo off\r\npython -m catalog_service --read-only\r\n",
                     encoding="utf-8")
    assert ic._catalog_launcher_port(stray) is None
    monkeypatch.setattr(ic, "_catalog_launcher_port", lambda p: None)
    notes = []
    done = ic._verify_catalog(target, py, notes)
    out = capsys.readouterr().out
    assert done == 3, out
    assert len(notes) == 1 and "нет порта" in notes[0], notes
    assert "отклик службы каталога (пусковой файл" not in out, out


def test_install_is_verified_end_to_end(installed):
    """Установка из поддельного репозитория проходит ВСЕ шаги и ВСЮ
    проверку: раздел «ПРОВЕРКА» целиком — импорт модулей, подъём
    приложения его пусковым файлом, отклик на порту, пароль входа из
    журнала приложения, каталог шаблонов, мост CDPM и служба каталога;
    итог — «Готово.», код возврата 0, отчёт и журнал говорят то же.

    ДЫРА ПОКРЫТИЯ: шаг «зависимости» падал на поддельном pyproject.toml
    (setuptools: несколько пакетов верхнего уровня), установщик считал
    «13 из 14» и verify() не звал вовсе — весь путь проверки не был
    покрыт ни одним тестом, а набор тестов был зелёным.
    """
    target, state = installed["target"], installed["state"]
    out, rc = installed["out"], installed["rc"]
    assert "шагов выполнено: 14 из 14" in out, out[-1500:]
    assert "=== 4. ПРОВЕРКА ===" in out, out[-1500:]
    section = out.split("=== 4. ПРОВЕРКА ===", 1)[1]
    print(section)
    for line in ("[OK  ] интерпретатор окружения",
                 "[OK  ] импорт приложения",
                 "[OK  ] отклик приложения (пусковой файл, порт 8399)",
                 "[OK  ] каталог шаблонов: ошибок 0",
                 "[OK  ] мост CDPM: каталог ",
                 "[OK  ] отклик службы каталога (пусковой файл, порт %d)"
                 % installed["catalog_port"]):
        assert line in section, (line, section)
    assert "[нет ]" not in section, section
    # Шесть проверок: интерпретатор, импорт, отклик приложения, каталог,
    # мост CDPM, отклик службы каталога. Меньше — проверка чего-то не
    # сделала; больше — счёт или перечень разошлись с этим тестом.
    assert "проверок выполнено: 6" in section, section
    # Пароль входа: показан человеку, тот же, что приложение напечатало
    # в свой журнал при первом запуске, и его отпечаток — тот, что
    # приложение сохранило. Заметки «пароль не найден» нет.
    m = re.search(r"ПАРОЛЬ ДЛЯ ВХОДА В ПРОГРАММУ:\s+(\S+)", section)
    assert m, section
    pw = m.group(1)
    log = (target / "webapp-server.log").read_text(encoding="utf-8",
                                                   errors="replace")
    assert "WEBAPP ACCESS TOKEN" in log and pw in log, log[-500:]
    auth = json.loads((target / "webapp" / "config" / "auth.json")
                      .read_text(encoding="utf-8"))
    assert auth["token_sha256"] == hashlib.sha256(pw.encode("utf-8")).hexdigest()
    assert "пароль входа не найден" not in out
    # Итог установщика: подтверждена, код возврата 0, «как запускать».
    assert "Готово." in out and "НЕ подтверждена" not in out, out[-800:]
    assert "КАК ЗАПУСКАТЬ ПРОГРАММУ" in out
    assert rc == 0, out[-1500:]
    # Отчёт об установке и журнал говорят то же, что экран.
    report = target / "installation-report.md"
    assert report.is_file(), out[-800:]
    assert "Приложение отвечает." in report.read_text(encoding="utf-8")
    rows = [json.loads(line) for p in journals(state)
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    finished = [r for r in rows if r.get("запись") == "установка завершена"]
    assert [r.get("итог") for r in finished] == ["успех"], finished


def test_verification_leaves_no_process_behind(installed):
    """ДЕФЕКТ: проверка поднимала приложение через `start /b`, и
    оболочка-родитель переживала установку, ничего не слушая — то есть
    была невидима любой проверке по портам."""
    target, state = installed["target"], installed["state"]
    # Проверка «процессов нет» не пуста: журнал установки подтверждает,
    # что проверка ДЕЙСТВИТЕЛЬНО поднимала оба пусковых файла — прежде
    # verify() не вызывалась вовсе, и этот тест проходил, ничего не
    # проверив.
    rows = [json.loads(line) for p in journals(state)
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    started = [r for r in rows if r.get("запись") == "запущен процесс проверки"]
    assert sorted(Path(r["командная_строка"]).name for r in started) \
        == ["run-catalog.cmd", "run-cosmos.cmd"], started
    stopped = [r for r in rows
               if r.get("запись") == "процесс проверки остановлен"]
    assert len(stopped) == len(started), (started, stopped)
    snap = un.snapshot_processes()
    launchers = [str(target / n) for n in un.LAUNCHERS]
    # Со шага 5 проверка поднимает ещё и службу каталога: её пусковой
    # файл обязан быть в перечне, иначе проверка «процессов нет»
    # смотрела бы мимо третьей службы пакета.
    assert str(target / "run-catalog.cmd") in launchers
    ours = un.classify_processes(snap, target, launchers)["наши"]
    assert ours == [], "после установки остались её процессы: %r" % ours
    assert ic._port_free(8399), "порт приложения не отпущен"
    # Порт службы каталога у проверки — свой (ключ --catalog-port), не
    # общий 8105: иначе тест судил бы и гасил чужого слушателя.
    assert installed["catalog_port"] != ic.CATALOG_PORT
    assert ic._port_free(installed["catalog_port"]), \
        "порт службы каталога не отпущен"
    launcher_text = (target / "run-catalog.cmd").read_text(encoding="utf-8")
    assert ("--port %d" % installed["catalog_port"]) in launcher_text


def test_dry_run_changes_nothing(installed):
    """Умолчание — холостой прогон: без явного ключа не должно
    измениться ничего."""
    state, target = installed["state"], installed["target"]
    before = sorted(str(p) for p in target.rglob("*"))

    rc, out = run_uninstaller(target, state)
    assert rc == 0, out[-1500:]
    assert target.exists()
    assert sorted(str(p) for p in target.rglob("*")) == before


def test_full_cycle_removes_target_and_spares_the_source(installed, fake_repo):
    """Снос убирает установку целиком и не задевает то, откуда её взяли.

    Идёт ПОСЛЕДНИМ среди неразрушающих: он уносит общую установку.
    """
    state, target = installed["state"], installed["target"]
    assert target.exists()

    rc, out = run_uninstaller(target, state, "--apply")
    assert rc == 0, out[-1500:]
    # «Пусто ИЛИ нет»: содержимое обязано исчезнуть, а сама запись
    # каталога иногда держится системой до выхода программы (наблюдалось
    # во временном каталоге, где файлы проверяет служба безопасности).
    # На настоящей установке каталог исчезает целиком.
    assert not list(target.rglob("*")) if target.exists() else True, (
        "внутри цели что-то осталось: %r" % [str(p) for p in target.rglob("*")][:5])
    data = Path(str(target) + "-data")
    assert not list(data.rglob("*")) if data.exists() else True
    assert fake_repo.exists(), "пострадал исходный репозиторий"


def test_uninstaller_started_from_inside_the_target(tmp_path, fake_repo):
    """ДЕФЕКТ: деинсталлятор приезжает ВНУТРИ установки, человек
    естественно запускает его оттуда, а каталог, который для процесса
    текущий, система удалить не даёт.

    Своя установка: тест разрушающий, общую он бы унёс раньше срока.
    """
    state = tmp_path / "state"
    target = tmp_path / "cosmos"
    _rc, _out = run_installer(tmp_path, fake_repo, target, state)
    if not target.exists():
        pytest.skip("установка не прошла в этой среде")
    env = dict(os.environ, LOCALAPPDATA=str(state))
    p = subprocess.run(
        [sys.executable, str(REPO / "uninstall_cosmos.py"),
         "--target", str(target), "--lang", "ru", "--apply"],
        capture_output=True, env=env, cwd=str(target), timeout=900)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert p.returncode == 0, out[-1500:]
    assert not any(target.rglob("*")) if target.exists() else True, (
        "внутри цели что-то осталось: %r" % [str(p) for p in target.rglob("*")][:5])


def test_interrupted_install_is_removable(tmp_path, fake_repo):
    """Установка, прерванная на середине, обязана сноситься так же
    просто: журнал пишется по ходу дела, а не в конце."""
    state = tmp_path / "state"
    target = tmp_path / "cosmos"
    _rc, _out = run_installer(tmp_path, fake_repo, target, state)
    if not target.exists():
        pytest.skip("установка не прошла в этой среде")

    # Прерванность подделываем честно: убираем отметку о завершении и
    # опись — ровно так выглядит журнал, если установку убили.
    j = journals(state)[0]
    rows = [l for l in j.read_text(encoding="utf-8").splitlines()
            if l.strip() and "установка завершена" not in l
            and "манифест дерева" not in l]
    j.write_text("\n".join(rows) + "\n", encoding="utf-8")
    for m in manifests(state):
        m.unlink()

    rc, out = run_uninstaller(target, state, "--apply")
    assert rc == 0, out[-1500:]
    assert not any(target.rglob("*")) if target.exists() else True, (
        "внутри цели что-то осталось: %r" % [str(p) for p in target.rglob("*")][:5])


