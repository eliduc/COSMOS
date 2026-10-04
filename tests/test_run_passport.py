# -*- coding: utf-8 -*-
"""Паспорт прогона (vmp/run_passport.py): параметры прогона объявляются
и сверяются ДО его начала — слово владельца 10.09.2026.

Судится ровно то, из-за чего паспорт заведён:

  * каталог правил и привязки исполнителей — на СИНТЕТИЧЕСКОМ каталоге
    (копия catalog/ через tests/catalog_copies.py): согласованные
    привязки проходят, несогласованные отказывают словами, а привязки
    ДЕРЕВА под чужим каталогом отказывают отдельными словами «привязки
    для каталога X не названы» — тот самый случай Н0-C 09.09.2026, где
    сторож дрейфа отказал на середине прогона;
  * раскладка моделей — путь ОТ КОРНЯ репозитория находится и у набора,
    скопированного вне open-bench (прежний путь от папки набора там
    рвался), отсутствующая раскладка отказывает и называет ОБА пути;
  * документы и книга данных — заморозка обязана совпасть с объявленным;
  * панель CDPM со СВОИМ каталогом — отказ ДО старта (подставная служба
    на свободном порту);
  * ключ --passport-only — собрать и сверить, ничего не запуская;
  * секретов в паспорте и журнале нет: учётные данные адреса решателя
    убираются, значения переменных с ключами не читаются вовсе.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
порт подставной службы — 0 (её же фактический номер читается после
старта), адреса — в зоне `.invalid`, отпечатки каталога берутся из
копии, а не пишутся числом.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
from vmp import run_passport as rp                                # noqa: E402

#: Версия синтетического каталога копии: заведомо не версия дерева —
#: иначе «привязки дерева под чужим каталогом» не воспроизвести.
COPY_VERSION = "0.0.1"
#: Адрес в зоне, которой не бывает: проба обязана честно не соединиться.
DEAD_HOST = "http://solver.invalid"
#: Учётные данные в адресе — ИСКОМОЕ теста о секретах: их в паспорте
#: быть не должно.
USER_SECRET = "тайное-слово-пробы"


@pytest.fixture(autouse=True)
def _forget_catalog():
    """Кэш двери каталога — свой у каждого теста (правило помощника
    копий): иначе следующий тест читает каталог предыдущего."""
    from vmp import catalog_access as ca
    ca.forget()
    yield
    ca.forget()


# ------------------------------------------------------------ помощники
def make_kit(tmp_path, *, catalog=None, layout=..., name="kit",
             rules=("ПРАВИЛА-1.txt",), data=("ДАННЫЕ-1.txt",)) -> Path:
    """Синтетический набор: документы правил, книга данных, раскладка и
    манифест. Настоящие наборы H0 сюда не тянутся — паспорт судится
    своими файлами, а не выпуском комплекта.

    `layout` не задан — набор несёт свою раскладку и называет её
    абсолютным путём (набор годен целиком); `layout=None` — ключа нет
    вовсе; строка — ровно она и уезжает в манифест."""
    kit = Path(tmp_path) / name
    (kit / "docs").mkdir(parents=True, exist_ok=True)
    (kit / "data").mkdir(parents=True, exist_ok=True)
    for i, fn in enumerate(rules, start=1):
        (kit / "docs" / fn).write_text(f"правило {i}", encoding="utf-8")
    for i, fn in enumerate(data, start=1):
        (kit / "data" / fn).write_text(f"строка данных {i}", encoding="utf-8")
    manifest = {
        "customer": "Проба-паспорт",
        "stage": "проба паспорта прогона",
        "rules_files": [f"docs/{fn}" for fn in rules],
        "data_files": [f"data/{fn}" for fn in data],
    }
    if catalog is not None:
        manifest["catalog"] = catalog
    if layout is ...:
        layout = str(make_layout(kit))
    if layout is not None:
        manifest[rp.LAYOUT_KEY_REPO] = layout
    (kit / "kit-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return kit


def make_layout(tmp_path, name="раскладка.json") -> Path:
    """Файл раскладки моделей по ролям: две роли, два канала."""
    p = Path(tmp_path) / name
    p.write_text(json.dumps({
        "form_version": 1, "customer": "Проба-паспорт",
        "extractor": {"channels": {"cloud": {"provider": "cloud",
                                             "model": "модель-извлечения"}}},
        "auditor": {"channels": {"cloud": {"provider": "openai",
                                           "model": "модель-аудитора",
                                           "effort": "high"}}},
    }, ensure_ascii=False), encoding="utf-8")
    return p


def catalog_of(folder) -> dict:
    """{version, fingerprint} копии каталога — читаются из неё же."""
    doc = json.loads((Path(folder) / "catalog.json").read_text(encoding="utf-8"))
    return {"version": str(doc["version"]), "fingerprint": str(doc["fingerprint"])}


class _Panel(BaseHTTPRequestHandler):
    """Подставная панель CDPM: отвечает на ручку каталога тем, что ей
    положили в `answer`. Порт — свободный (0), номер читается после
    старта."""
    answer: dict = {}

    def do_GET(self):                                     # noqa: N802
        body = json.dumps(self.answer).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                            # noqa: D102
        pass


@pytest.fixture
def panel():
    """Подставная панель на свободном порту: отдаёт `srv.answer`."""
    handler = type("_H", (_Panel,), {"answer": {}})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address[0], srv.server_address[1]
    srv.base_url = "http://" + str(host) + ":" + str(port)
    srv.handler = handler
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------- каталог правил и его привязки
def test_каталог_и_привязки_копии_сходятся(tmp_path, monkeypatch):
    """Каталог прогона назван переменной контура, привязки — под него:
    паспорт чист, и оба параметра названы словами, а не молчанием."""
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    want = catalog_of(folder)
    kit = make_kit(tmp_path, catalog=want)
    p = rp.build(kit_dir=kit, until="package", probe=False)
    assert p["problems"] == [], p["problems"]
    assert p["catalog"]["version"] == want["version"]
    assert p["catalog"]["fingerprint"] == want["fingerprint"]
    assert rp.CATALOG_ENV in p["catalog"]["source"]
    assert p["bindings"]["catalog_version"] == want["version"]
    assert rp.BINDINGS_ENV in p["bindings"]["source"]
    assert rp.refusal(p) is None


def test_привязки_дерева_под_чужим_каталогом_отказ_словами(tmp_path,
                                                            monkeypatch):
    """Живой случай Н0-C 09.09.2026: каталог взят снимком, а привязки
    остались дерева — сторож дрейфа отказывал на СЕРЕДИНЕ прогона.
    Паспорт отказывает ДО старта и называет обе стороны."""
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)     # привязки дерева
    want = catalog_of(folder)
    kit = make_kit(tmp_path, catalog=want)
    p = rp.build(kit_dir=kit, until="package", probe=False)
    words = rp.refusal(p)
    assert words is not None
    assert "привязки для каталога" in words and "не названы" in words
    assert "--bindings" in words
    assert "объявлено" in words and "на деле" in words
    assert want["version"] in words


def test_привязки_названные_но_чужие_отказ_словами(tmp_path, monkeypatch):
    """Ключ --bindings назван, а файл собран по другому каталогу: то же
    расхождение, но слова другие — источник назван, значит виноват не
    «не назвали», а «назвали не то»."""
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    want = catalog_of(folder)
    alien = tmp_path / "привязки-чужие.json"
    doc = json.loads((Path(folder) / cc.BINDINGS_FILE).read_text(encoding="utf-8"))
    doc["catalog_version"] = "9.9.9"
    doc["catalog_fingerprint"] = "0" * 64
    alien.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(cc.BINDINGS_ENV, str(alien))
    kit = make_kit(tmp_path, catalog=want)
    p = rp.build(kit_dir=kit, until="package", probe=False,
                 bindings_key=str(alien))
    words = rp.refusal(p)
    assert words is not None and "привязки COSMOS" in words
    assert "--bindings" in words and "9.9.9" in words
    assert "не названы" not in words


def test_каталог_набора_не_тот_отказ_словами(tmp_path, monkeypatch):
    """Манифест набора объявляет каталог, которым набор выпущен: другой
    каталог у продукта — отказ до старта, обе версии в словах."""
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    kit = make_kit(tmp_path, catalog={"version": "9.9.9",
                                      "fingerprint": "0" * 64})
    p = rp.build(kit_dir=kit, until="package", probe=False)
    words = rp.refusal(p)
    assert words is not None and "каталог правил" in words
    assert "9.9.9" in words and catalog_of(folder)["version"] in words
    assert "манифест набора" in words


def test_каталог_дерева_назван_словами(tmp_path, monkeypatch):
    """«Умолчание из дерева» — законная конфигурация, но НАЗВАННАЯ:
    паспорт пишет «каталог дерева <версия> (<отпечаток>)»."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    kit = make_kit(tmp_path)
    p = rp.build(kit_dir=kit, until="package", probe=False)
    assert p["catalog"]["source"] == rp.SRC_TREE
    assert p["catalog"]["words"].startswith("каталог дерева ")
    assert p["catalog"]["version"] in p["catalog"]["words"]
    # набор каталог не закрепляет — так и сказано, а не «сверка прошла»
    assert "не закрепляет" in p["catalog"]["declared_words"]
    assert p["problems"] == [], p["problems"]


# ------------------------------------------------- раскладка моделей
def test_раскладка_находится_по_пути_от_корня_репозитория(tmp_path,
                                                           monkeypatch):
    """Корень отказа 09.09.2026: раскладка искалась от ПАПКИ НАБОРА, и
    копия набора вне open-bench её не находила. Путь от корня
    репозитория находится откуда угодно."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    rel = "open-bench/polyaris-h0/agents-layout-h0.json"
    kit = make_kit(tmp_path, layout=rel)     # набор лежит ВНЕ open-bench
    p = rp.build(kit_dir=kit, until="package", probe=False)
    assert p["problems"] == [], p["problems"]
    layout = p["agents_layout"]
    assert layout["file"] == rel
    assert rp.LAYOUT_KEY_REPO in layout["source"]
    assert layout["sha256"] and layout["roles"]
    assert "extractor" in layout["roles"]


def test_раскладка_абсолютным_путём_и_модели_по_ролям(tmp_path, monkeypatch):
    """Абсолютный путь принимается как есть; в паспорт едут МОДЕЛИ по
    ролям, а не только имя файла."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    layout = make_layout(tmp_path)
    kit = make_kit(tmp_path, layout=str(layout))
    p = rp.build(kit_dir=kit, until="package", probe=False)
    roles = p["agents_layout"]["roles"]
    assert set(roles) == {"extractor", "auditor"}
    assert roles["extractor"]["cloud"]["model"] == "модель-извлечения"
    assert roles["auditor"]["cloud"]["effort"] == "high"
    # служебные ключи файла ролями не считаются
    assert "form_version" not in roles and "customer" not in roles


def test_раскладки_нет_отказ_называет_оба_пути(tmp_path, monkeypatch):
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    kit = make_kit(tmp_path, layout="нет-такой-папки/раскладка.json")
    p = rp.build(kit_dir=kit, until="package", probe=False)
    words = rp.refusal(p)
    assert words is not None and "раскладка моделей по ролям" in words
    assert "нет-такой-папки/раскладка.json" in words
    assert rp.LAYOUT_DEFAULT in words            # второй испробованный путь
    assert "файла нет ни по одному из путей" in words


def test_раскладка_наборов_дерева_находится_у_копии_набора(tmp_path):
    """Все наборы формы H0 в дереве обязаны называть раскладку путём от
    КОРНЯ: копия набора, положенная вне open-bench, её находит."""
    import shutil
    found = []
    for name in ("polyaris-h0", "polyaris-h0b", "polyaris-h0c",
                 "polyaris-h0k"):
        src = ROOT / "open-bench" / name
        man = json.loads((src / "kit-manifest.json").read_text(encoding="utf-8"))
        assert man.get(rp.LAYOUT_KEY_REPO), name
        copy = tmp_path / name
        shutil.copytree(src, copy)             # набор ВНЕ open-bench
        info = rp.resolve_layout(copy, man, ROOT)
        assert info["file"] is not None and info["file"].is_file(), name
        assert rp.LAYOUT_KEY_REPO in info["source"], name
        found.append(name)
    assert len(found) == 4


# ------------------------------ документы и данные: объявление и факт
def test_отпечатки_документов_и_данных_идут_в_паспорт(tmp_path, monkeypatch):
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    kit = make_kit(tmp_path)
    p = rp.build(kit_dir=kit, until="package", probe=False)
    assert [r["name"] for r in p["rules_documents"]] == ["ПРАВИЛА-1.txt"]
    assert [r["name"] for r in p["data_documents"]] == ["ДАННЫЕ-1.txt"]
    assert p["rules_documents"][0]["sha256"] == rp.sha256_file(
        kit / "docs" / "ПРАВИЛА-1.txt")
    assert p["rules_documents"][0]["bytes"] > 0


def test_объявленного_файла_набора_нет_отказ(tmp_path, monkeypatch):
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    kit = make_kit(tmp_path)
    (kit / "docs" / "ПРАВИЛА-1.txt").unlink()
    p = rp.build(kit_dir=kit, until="package", probe=False)
    words = rp.refusal(p)
    assert words is not None and "набор правил авиакомпании" in words
    assert "файла нет" in words


def test_заморозка_сверяется_с_объявленным(tmp_path, monkeypatch):
    """Вторая сверка: то, что конвейер ЗАМОРОЗИЛ, обязано быть тем, что
    объявлено паспортом. Манифеста заморозки нет — молчим; отпечаток
    другой — слова «прогон заморозил не то, что объявлено»."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    kit = make_kit(tmp_path)
    p = rp.build(kit_dir=kit, until="package", probe=False)
    run_dir = tmp_path / "runs" / "run-0001"
    # заморозки ещё нет — сверять нечего
    assert rp.freeze_problems(p, run_dir) == []
    (run_dir / "freeze").mkdir(parents=True)
    (run_dir / "data-freeze").mkdir(parents=True)
    good = {"files": [{"sid": "S1", "file": "ПРАВИЛА-1.txt",
                       "sha256": p["rules_documents"][0]["sha256"]}]}
    (run_dir / "freeze" / "manifest.json").write_text(
        json.dumps(good, ensure_ascii=False), encoding="utf-8")
    (run_dir / "data-freeze" / "manifest.json").write_text(json.dumps(
        {"files": [{"sid": "D1", "file": "ДАННЫЕ-1.txt",
                    "sha256": p["data_documents"][0]["sha256"]}]},
        ensure_ascii=False), encoding="utf-8")
    assert rp.freeze_problems(p, run_dir) == []
    # подменённый документ
    bad = {"files": [dict(good["files"][0], sha256="1" * 64)]}
    (run_dir / "freeze" / "manifest.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    got = rp.freeze_problems(p, run_dir)
    assert got and "прогон заморозил не то, что объявлено" in got[0]
    assert "ПРАВИЛА-1.txt" in got[0]
    assert "объявлено" in got[0] and "на деле" in got[0]
    # лишний замороженный файл — тоже расхождение
    extra = {"files": good["files"] + [{"sid": "S2", "file": "ЛИШНИЙ.txt",
                                        "sha256": "2" * 64}]}
    (run_dir / "freeze" / "manifest.json").write_text(
        json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    got = rp.freeze_problems(p, run_dir)
    assert got and "ЛИШНИЙ.txt" in got[0] and "не объявлен" in got[0]


# ---------------------------------------------------------- панель CDPM
def test_панель_со_своим_каталогом_отказ_до_старта(tmp_path, monkeypatch,
                                                   panel):
    """Панель-служба со СВОИМ каталогом посадила бы правила на чужие
    шаблоны: паспорт сверяет её ручку каталога и отказывает до старта."""
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    want = catalog_of(folder)
    panel.handler.answer = {"available": True, "version": "9.9.9",
                            "fingerprint": "0" * 64, "path": None}
    kit = make_kit(tmp_path, catalog=want)
    p = rp.build(kit_dir=kit, until="package", base_url=panel.base_url)
    words = rp.refusal(p)
    assert words is not None and "каталог панели CDPM" in words
    assert "9.9.9" in words and want["version"] in words
    assert p["panel"]["catalog"]["version"] == "9.9.9"
    # та же панель с тем же каталогом — паспорт чист
    panel.handler.answer = {"available": True, "version": want["version"],
                            "fingerprint": want["fingerprint"], "path": None}
    p2 = rp.build(kit_dir=kit, until="package", base_url=panel.base_url)
    assert p2["problems"] == [], p2["problems"]
    assert p2["panel"]["in_process"] is False


def test_панель_в_процессе_названа_словами(tmp_path, monkeypatch):
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    p = rp.build(kit_dir=make_kit(tmp_path), until="package", probe=False)
    assert p["panel"]["in_process"] is True
    assert "панель в процессе" in p["panel"]["words"]


# ------------------------------------------------------------ решатель
def test_решатель_не_требуется_стадиям_cdpm(tmp_path, monkeypatch):
    """Решателя касаются НАЖАТИЯ COSMOS, а не стадии CDPM: недоступный
    решатель прогон не останавливает, но и не замалчивается — паспорт
    пишет «не требуется до стадии X»."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    monkeypatch.setenv(rp.SOLVER_ENV, DEAD_HOST)
    p = rp.build(kit_dir=make_kit(tmp_path), until="package", probe=False)
    assert p["problems"] == [], p["problems"]
    assert p["solver"]["required"] is False
    assert "не требуется до стадии package" in p["solver"]["words"]
    assert p["solver"]["version_endpoint_code"] == "no-version-endpoint"


def test_решателя_нет_в_адресе_но_он_назван_переменной(tmp_path, monkeypatch):
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    monkeypatch.delenv(rp.SOLVER_ENV, raising=False)
    p = rp.build(kit_dir=make_kit(tmp_path), until="package", probe=False)
    assert p["solver"]["url"] is None and p["solver"]["source"] is None
    assert "не назван" in p["solver"]["words"]


# --------------------------------------------- окружение и библиотеки
def test_все_библиотеки_и_их_отпечаток(tmp_path, monkeypatch):
    """«Все библиотеки» — слово владельца: перечень пакетов с версиями
    отсортирован, и у него есть отпечаток для сверки двух прогонов."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    p = rp.build(kit_dir=make_kit(tmp_path), until="package", probe=False)
    py = p["python"]
    names = [r["name"] for r in py["packages"]]
    assert names == sorted(names) and len(names) == py["packages_count"]
    assert py["packages_count"] > 0 and py["version"]
    assert len(py["packages_sha256"]) == 64
    listing = "\n".join(f"{r['name']}=={r['version']}" for r in py["packages"])
    assert py["packages_sha256"] == rp.sha256_text(listing)


# -------------------------------------------------------------- секреты
def test_секретов_в_паспорте_и_журнале_нет(tmp_path, monkeypatch):
    """Учётные данные адреса решателя убираются, а значения переменных с
    ключами паспорт не читает вовсе — ни в документе, ни в журнале."""
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    monkeypatch.setenv("CREW_ANTHROPIC_API_KEY", USER_SECRET)
    monkeypatch.setenv("CREW_CATALOG_TOKEN", USER_SECRET)
    monkeypatch.setenv(rp.SOLVER_ENV,
                       "http://пользователь:" + USER_SECRET + "@solver.invalid/")
    p = rp.build(kit_dir=make_kit(tmp_path), until="package", probe=False)
    doc = json.dumps(p, ensure_ascii=False)
    said = "\n".join(rp.render(p))
    assert USER_SECRET not in doc, "секрет уехал в паспорт"
    assert USER_SECRET not in said, "секрет уехал в журнал"
    assert p["solver"]["url"] == "http://solver.invalid/"
    assert "solver.invalid" in said


def test_учётные_данные_убираются_из_слов_отказа():
    assert rp.strip_userinfo("http://кто:" + USER_SECRET
                             + "@solver.invalid/x") == "http://solver.invalid/x"
    assert rp.strip_userinfo("http://solver.invalid/x") \
        == "http://solver.invalid/x"
    assert rp.strip_userinfo(None) is None
    said = rp.scrub_text("отказ на http://кто:" + USER_SECRET
                         + "@solver.invalid/x — соединение не встало")
    assert USER_SECRET not in said and "solver.invalid" in said


# ------------------------------------------------- ключ --passport-only
def _runner():
    import importlib.util
    p = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_local_pipeline_h0"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_passport_only_собирает_и_сверяет_не_запуская(tmp_path, monkeypatch,
                                                       capsys):
    """--passport-only: прогон не стартует, панель в процессе не
    поднимается; код 0 — сошёлся, 2 — расхождения; файл паспорта лежит
    в --out."""
    r = _runner()
    monkeypatch.setattr(r, "Runner", lambda *a, **kw:
                        pytest.fail("--passport-only поднял раннер"))
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    out = tmp_path / "out"
    kit = make_kit(tmp_path)
    assert r.main(["--out", str(out), "--kit-dir", str(kit),
                   "--passport-only"]) == 0
    p = json.loads((out / rp.PASSPORT_FILE).read_text(encoding="utf-8"))
    assert p["schema"] == rp.SCHEMA and p["problems"] == []
    said = capsys.readouterr().out
    assert "ПАСПОРТ ПРОГОНА" in said and "паспорт прогона целиком" in said
    assert "сошёлся" in said
    # расхождение — код 2 и слова «объявлено … / на деле …»
    bad = make_kit(tmp_path, name="kit-bad",
                   catalog={"version": "9.9.9", "fingerprint": "0" * 64})
    assert r.main(["--out", str(out), "--kit-dir", str(bad),
                   "--passport-only"]) == 2
    said = capsys.readouterr().out
    assert "ПАСПОРТ ПРОГОНА НЕ СОШЁЛСЯ" in said
    assert "объявлено" in said and "на деле" in said


def test_двери_контура_ставятся_ключами(tmp_path, monkeypatch):
    """Ключ сильнее переменной: прогон называет каталог, привязки и
    решатель сам, а не наследует их от предыдущего."""
    r = _runner()
    folder = tmp_path / "каталог"
    folder.mkdir()
    bind = tmp_path / "привязки.json"
    bind.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(rp.CATALOG_ENV, "старое-значение")
    monkeypatch.setenv(rp.BINDINGS_ENV, "старое-значение")
    monkeypatch.delenv(rp.SOLVER_ENV, raising=False)
    r.apply_doors(str(folder), str(bind), DEAD_HOST)
    import os
    assert os.environ[rp.CATALOG_ENV] == str(folder.resolve())
    assert os.environ[rp.BINDINGS_ENV] == str(bind.resolve())
    assert os.environ[rp.SOLVER_ENV] == DEAD_HOST
    # ключ не назван — переменная контура остаётся как есть
    r.apply_doors(None, None, None)
    assert os.environ[rp.CATALOG_ENV] == str(folder.resolve())


# ------------------------------- паспорт как голова записи о прогоне
# Паспорт — первое воплощение объявления записи (проект «Запись о
# прогоне», раздел 8): те же параметры под именами словаря записи.
# Здесь судится СТЫК двух модулей на паспорте НАСТОЯЩЕЙ СБОРКИ; сам
# словарь полей судит tests/test_run_record.py.

def test_паспорт_переводится_в_объявление_записи(tmp_path, monkeypatch):
    """Настоящий паспорт целиком ложится головой записи: каталог,
    привязки, документы, данные, книга ответов, модели по ролям,
    решатель, библиотеки и код — без второго сбора параметров."""
    from vmp import run_record as rr
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    want = catalog_of(folder)
    kit = make_kit(tmp_path, catalog=want)
    code = {"commit": "коммит-пробы", "fingerprint": "отпечаток-пробы"}
    p = rp.build(kit_dir=kit, until="package", probe=False, code=code)
    assert p["problems"] == [], p["problems"]

    d = rp.declaration(p, run_id="проба-паспорт-1", purpose="приёмка",
                       contour="local", planned_stages=["create", "package"])
    # выход паспорта и перевод имён — одно и то же место
    assert d == rr.passport_to_declaration(
        p, run_id="проба-паспорт-1", purpose="приёмка", contour="local",
        planned_stages=["create", "package"], at=d["created_at"])
    assert d["catalog"]["version"] == want["version"]
    assert d["catalog"]["fingerprint"] == want["fingerprint"]
    assert d["bindings"]["catalog_version"] == want["version"]
    assert [x["name"] for x in d["rules_documents"]] == ["ПРАВИЛА-1.txt"]
    assert [x["name"] for x in d["data_files"]] == ["ДАННЫЕ-1.txt"]
    assert d["rules_documents"][0]["sha256"] == p["rules_documents"][0]["sha256"]
    assert {m["role"] for m in d["models"]} == {"extractor", "auditor"}
    assert d["libraries"]["packages_sha256"] == p["python"]["packages_sha256"]
    # Репозиторий единой версии: коммит у обеих систем один и тот же.
    assert d["code"]["cdpm_commit"] == code["commit"]
    assert d["code"]["cosmos_commit"] == code["commit"]
    assert d["code"]["runner_fingerprint"] == code["fingerprint"]
    assert d["stop_after"] == "package"
    assert d["expectations"]["ref"] == p["kit"]["manifest"]
    assert d["airline"]["name"] == p["kit"]["customer"]
    # запись из такой головы принимается без расхождений
    record = rr.new_record(d)
    assert rr.validate(record) == []
    assert record["results"]["state"] == rr.STATE_DECLARED


def test_passport_only_кладёт_запись_с_одним_объявлением(tmp_path, monkeypatch,
                                                          capsys):
    """Ключ --passport-only: рядом с паспортом ложится запись о прогоне с
    ОДНИМ объявлением — журнал пуст, состояние «объявлен». Паспорт не
    сошёлся — записи нет: запись с несошедшимся объявлением была бы
    неправдой."""
    from vmp import run_record as rr
    r = _runner()
    monkeypatch.delenv(rp.CATALOG_ENV, raising=False)
    monkeypatch.delenv(cc.BINDINGS_ENV, raising=False)
    out = tmp_path / "out"
    kit = make_kit(tmp_path, name="kit-запись")
    assert r.main(["--out", str(out), "--kit-dir", str(kit),
                   "--passport-only", "--purpose", "приёмка"]) == 0
    record = rr.load(out)
    assert rr.validate(record) == []
    assert record["journal"] == []
    assert record["results"]["state"] == rr.STATE_DECLARED
    assert record["declaration"]["purpose"] == "приёмка"
    assert record["declaration"]["run_id"]
    assert record["declaration"]["planned_stages"][0] == "create"
    assert [x["name"] for x in record["declaration"]["rules_documents"]] \
        == ["ПРАВИЛА-1.txt"]
    capsys.readouterr()
    bad_out = tmp_path / "out-bad"
    bad = make_kit(tmp_path, name="kit-запись-плохой",
                   catalog={"version": "9.9.9", "fingerprint": "0" * 64})
    assert r.main(["--out", str(bad_out), "--kit-dir", str(bad),
                   "--passport-only"]) == 2
    assert not (bad_out / rr.RECORD_FILE).exists()
    capsys.readouterr()


def test_книга_ответов_набора_ссылка_паспорта_а_корень_с_ответами_по_ревизиям(
        tmp_path, monkeypatch):
    """Ответы на анкеты — исходные документы (раздел 7 проекта), но К
    ОБЪЯВЛЕНИЮ они неизвестны: ревизии ответов пишет панель по книге,
    собранной под прогон, а не по книге набора (живая находка 10.09.2026,
    run-0141: объявленный по книге корень с ответами не сходился с
    ревизиями). Книга набора остаётся ссылкой паспорта с отпечатком,
    политика ответов — «книга», answers[] и корень с ответами пусты до
    закрытия ответов; явно переданные ревизии дают корень с ответами,
    который меняется от одного ответа при том же корне по документам."""
    from vmp import run_record as rr
    folder = cc.copy_catalog(tmp_path, version=COPY_VERSION)
    cc.use_catalog(monkeypatch, folder)
    kit = make_kit(tmp_path, catalog=catalog_of(folder))
    book = kit / "ответы.json"
    book.write_text(json.dumps({"ответы": []}, ensure_ascii=False),
                    encoding="utf-8")
    manifest = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
    manifest["answer_book"] = book.name
    (kit / "kit-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    p = rp.build(kit_dir=kit, until="package", probe=False)
    assert p["kit"]["references"]["answer_book"]["sha256"] == rp.sha256_file(book)
    d = rr.passport_to_declaration(p, run_id="проба-паспорт-2",
                                   purpose="регресс", contour="local")
    assert d["answer_policy"] == "книга"
    assert d["answers"] == []
    first = d["lineage"]["root_sha256"]
    assert first and d["lineage"]["root_answers_sha256"] is None
    # ревизии ответов, закрытые по ходу прогона, — корень С ОТВЕТАМИ;
    # другой ответ — другая ветвь при том же корне по документам и данным
    rev = [{"revision": 1, "sha256": "a" * 64, "source": "книга набора",
            "at": "2026-09-10T12:00:00+02:00"}]
    d1 = rr.passport_to_declaration(p, run_id="проба-паспорт-3",
                                    purpose="регресс", contour="local",
                                    answers=rev)
    other = [{**rev[0], "sha256": "b" * 64}]
    d2 = rr.passport_to_declaration(p, run_id="проба-паспорт-4",
                                    purpose="регресс", contour="local",
                                    answers=other)
    assert d1["lineage"]["root_sha256"] == first == d2["lineage"]["root_sha256"]
    assert d1["lineage"]["root_answers_sha256"] and \
        d1["lineage"]["root_answers_sha256"] != d2["lineage"]["root_answers_sha256"]
