# -*- coding: utf-8 -*-
"""Обвязка тестов службы каталога.

Живой `catalog/` репозитория тесты НЕ трогают: каждая служба поднимается
над tmp-копией (`tests/catalog_copies.copy_catalog`), рабочая папка — во
временной папке теста, перечень маркеров — свой файл с одним словом,
которого в каталоге нет. Страж этого правила — `test_service_consumers`
(байты живой папки до и после набора).

Ожидания версий, отпечатков и размеров берутся ИЗ ЖИВОЙ КОПИИ, а не
литералами: литерал устареет при первой же сборке службой.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
from catalog_service.app import create_app                        # noqa: E402
from catalog_service.settings import Settings                     # noqa: E402
from tests.catalog_copies import (BINDINGS_FILE, copy_catalog,    # noqa: E402
                                  bump_mtime)

#: Слово-маркер перечня: в каталоге его нет и быть не может.
MARKER = "ФРЕГЕНОЛЁТ"
LIVE_CDPM_BINDINGS = ROOT / "cdpm" / "bindings.json"

#: Заглушка закона паритета для стендов службы (§5 проекта пакета
#: «паритет на временном каталоге»: «инструмент подменяется файлом-
#: заглушкой tools/parity.py в tmp-дереве»). Настоящий движок читает
#: таблицы решателя и код CDPM — службе он не нужен ни на одном стенде:
#: её закон в том, КАК она читает вердикт и коды возврата, а не в том,
#: как вердикт получен. Заглушка отвечает договором
#: `catalog/tools/PARITY-CONTRACT.md`: версия и отпечаток — из шапки
#: судимой папки (литералов нет), а свой вызов (argv, переменные среды,
#: рабочая папка) она кладёт файлом по пути `PARITY_STUB_LOG`, если он
#: задан, — так стенд доказывает, что служба отдала подпроцессу
#: `CREW_TEMPLATE_CATALOG` и файл привязок COSMOS.
PARITY_STUB = '''# -*- coding: utf-8 -*-
"""Заглушка закона паритета: стенды службы каталога (см. conftest)."""
import argparse
import json
import os
import sys
from pathlib import Path

RULES = __RULES__
RC = __RC__
CRASH = __CRASH__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--bindings", default=None)
    ap.add_argument("--with-candidates", action="store_true")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    log = os.environ.get("PARITY_STUB_LOG")
    if log:
        Path(log).write_text(json.dumps(
            {"tool": sys.argv[0], "argv": sys.argv[1:], "cwd": os.getcwd(),
             "catalog_env": os.environ.get("CREW_TEMPLATE_CATALOG"),
             "utf8_env": os.environ.get("PYTHONUTF8"),
             "bindings": a.bindings, "quiet": bool(a.quiet)},
            ensure_ascii=False), encoding="utf-8")
    if CRASH:
        print("заглушка паритета: судить нечем", file=sys.stderr)
        return RC
    head = {}
    if a.catalog:
        try:
            head = json.loads(
                (Path(a.catalog) / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            head = {}
    red = sorted(k for k, v in RULES.items() if v.get("ok") is False)
    doc = {"rc": RC, "ok": not red,
           "catalog": {"version": head.get("version"),
                       "fingerprint": head.get("fingerprint"),
                       "path": str(Path(a.catalog or ".").resolve())},
           "counts": {"rules": len(RULES),
                      "green": sum(1 for v in RULES.values()
                                   if v.get("ok") is True),
                      "red": len(red),
                      "skipped": sum(1 for v in RULES.values()
                                     if v.get("ok") is None),
                      "warning": sum(1 for v in RULES.values()
                                     if v.get("warning"))},
           "rules": RULES,
           "words": "; ".join(str(RULES[k].get("words") or k) for k in red)
                    or None}
    if a.json_path:
        Path(a.json_path).write_text(json.dumps(doc, ensure_ascii=False),
                                     encoding="utf-8")
    if not a.quiet:
        print(json.dumps(doc, ensure_ascii=False))
    return RC


if __name__ == "__main__":
    raise SystemExit(main())
'''
#: Зелёный вердикт заглушки: два обычных правила и один честный
#: `skipped` БЕЗ предупреждения (пропуск вердикт не красит и
#: подтверждения не требует).
GREEN_RULES = {
    "L05": {"ok": True, "words": None,
            "numbers": {"params": 46, "cdpm": 44}},
    "L18": {"ok": True, "words": None},
    "L23": {"ok": None,
            "skipped": "папка без tools/ — свежесть носителей судит дерево"},
}


def write_parity_tool(folder: Path, *, rules: dict | None = None,
                      rc: int = 0, crash: bool = False) -> Path:
    """Положить заглушку закона паритета в `<копия>/tools/parity.py`.

    Закон живёт ВНУТРИ папки каталога (`catalog/tools/parity.py`), и
    служба зовёт его именно оттуда — копии каталога он едет вместе с
    данными, которые судит."""
    rules = GREEN_RULES if rules is None else rules
    tool = Path(folder) / "tools" / "parity.py"
    tool.parent.mkdir(parents=True, exist_ok=True)
    text = (PARITY_STUB
            .replace("__RULES__", repr(rules))
            .replace("__RC__", str(int(rc)))
            .replace("__CRASH__", "True" if crash else "False"))
    tool.write_text(text, encoding="utf-8")
    return tool


#: Правила заглушки: красное L05 (выпуск обязан отказать `parity-red`).
RED_RULES = {
    "L05": {"ok": False,
            "words": "ручка решателя max_rest_after_night "
                     "(duration_minutes, min) — параметра нет ни в записи, "
                     "ни в словаре"},
    "L18": {"ok": True, "words": None},
}
#: Правила заглушки: зелёный вердикт с предупреждением L08b (CDPM ещё не
#: знает метрику) — выпуск требует подтверждения владельца.
WARNING_RULES = {
    "L08": {"ok": True, "words": None, "numbers": {"metrics": 6}},
    "L08b": {"ok": None, "warning": True,
             "skipped": "CDPM ещё не знает метрику credit_hours_spread"},
}


def write_markers(path: Path, values=(MARKER,)) -> Path:
    """Перечень маркеров заказчика в форме `tools/customer_markers`."""
    path.write_text(json.dumps(
        {"checks": [{"name": "проба службы", "kind": "token",
                     "values": list(values)}]},
        ensure_ascii=False), encoding="utf-8")
    return path


#: Номер копии каталога у стендов службы — сценарий стендов (первый
#: выпуск 0.2.0, второй 0.3.0), а не номер живого каталога.
SERVICE_DRAFT_VERSION = "0.2.0-draft.1"


def draft_version() -> str:
    """Номер копии каталога для службы — ЧЕРНОВИК, и всегда один и тот же.

    Служба живёт циклом «черновик → сборка → выпуск», и её стенды обязаны
    начинать с черновика: журнал копии пуст, папок выпусков у неё нет,
    поэтому номер копии — дело сценария стендов (первый выпуск 0.2.0,
    второй 0.3.0, номера ниже и черновики — отказы словами), а не живого
    каталога. Прежде номер брался у живого каталога («та же тройка с
    суффиксом черновика»), и выпуск живого каталога 0.3.0 (09.09.2026)
    покрасил тринадцать стендов выпуска и паритета словами «выпуск не
    может быть младше 0.3.0»: сценарий с литералами и подвижный номер
    копии несовместимы, а тело копии от номера не зависит (историю
    записей с номером выпуска читатель не сверяет). Кому нужен другой
    номер — ключ version= фабрики."""
    return SERVICE_DRAFT_VERSION


def write_cdpm_bindings(folder: Path, dst: Path) -> Path:
    """Синтетические привязки CDPM к копии каталога: строки живого файла
    CDPM по записям, оставшимся в копии, шапка — с версией и отпечатком
    КОПИИ."""
    try:
        cat = tc.load(folder)
        known = {("families", str(r.get("id")))
                 for r in cat.families_full(status=None)}
        known |= {("criteria", cid) for cid in cat.criteria(status=None)}
        version, fingerprint = cat.version(), cat.fingerprint()
    except tc.CatalogError:
        # Копия испорчена НАРОЧНО (проба отказа службы) — чужой файл
        # привязок при этом всё равно должен существовать: его судьба
        # тоже часть проверяемого поведения.
        known, version, fingerprint = set(), "0.0.1", "0" * 64
    if LIVE_CDPM_BINDINGS.is_file():
        doc = json.loads(LIVE_CDPM_BINDINGS.read_text(encoding="utf-8"))
    else:                                       # pragma: no cover
        doc = {"product": "CDPM", "product_version": "1.18",
               "executor_kinds": ["check_template"], "bindings": [],
               "generated_at": "2026-09-08T00:00:00+03:00",
               "generated_from": "0" * 40}
    doc["bindings"] = [r for r in doc.get("bindings") or []
                       if (str(r.get("section")), str(r.get("id"))) in known]
    doc["catalog_version"] = version
    doc["catalog_fingerprint"] = fingerprint
    dst.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n", encoding="utf-8")
    return dst


class Service:
    """Клиент службы плюс то, что тесту нужно о ней знать."""

    def __init__(self, client, settings, app) -> None:
        self.client = client
        self.settings = settings
        self.app = app
        self.store = app.state.store
        self.folder = Path(settings.catalog_dir)

    # короткие обёртки: тесты читаются как разговор со службой
    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def put(self, url, **kw):
        return self.client.put(url, **kw)

    def post(self, url, **kw):
        return self.client.post(url, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, **kw)

    def catalog(self):
        tc.forget_current()
        return tc.load(self.folder)

    def record(self, section: str, rid: str) -> dict:
        cat = self.catalog()
        return cat.family(rid) if section == "families" \
            else cat.criterion(rid)


@pytest.fixture()
def make_service(tmp_path, monkeypatch):
    """Фабрика служб: роль, копия каталога, наличие перечня маркеров и
    чужих привязок — по случаю теста."""
    from fastapi.testclient import TestClient

    made: list = []

    def build(role: str | None = "owner", *, folder: Path | None = None,
              read_only: bool = False, markers: bool = True,
              cdpm_bindings: bool = True, cosmos_bindings: bool = True,
              work: str = "work", machine_token: str | None = None,
              parity: bool | dict = True, parity_rc: int = 0,
              **copy_kw) -> Service:
        # machine_token — признак машинного канала заявок шага 6
        # (CATALOG_MACHINE_TOKEN). Умолчание None значит «канал выключен»:
        # площадки и установка заказчика — именно этот случай, и тесты
        # обязаны видеть его первым.
        copy_kw.setdefault("version", draft_version())
        # Очередь заявок копии ПУСТА: стенды службы подают заявки сами, а
        # живая очередь дерева (с 09.09.2026 в ней заявка duty_series_limit
        # «исполнитель заявлен») красила предпроверки выпуска и счёт
        # заявок «по построению».
        copy_kw.setdefault("requests", False)
        target = folder if folder is not None else copy_catalog(
            tmp_path, name=copy_kw.pop("name", "catalog"), **copy_kw)
        # Закон паритета (предпроверка выпуска 12) — заглушкой в копии:
        # умолчание ЗЕЛЁНОЕ, потому что остальные стенды судят не
        # паритет. `parity=False` — «инструмента рядом нет»: честный
        # исход «не судился», который выпуск требует подтвердить.
        if parity:
            write_parity_tool(Path(target), rc=parity_rc,
                              rules=parity if isinstance(parity, dict)
                              else None)
        work_dir = tmp_path / work
        # markers=False — перечня на машине НЕТ: путь задан и указывает
        # на несуществующий файл. Оставить None нельзя: умолчание
        # службы — перечень линта машины владельца, и он там есть.
        markers_file = (write_markers(tmp_path / "customer-markers.json")
                        if markers else tmp_path / "перечня-нет.json")
        cdpm_path = tmp_path / "cdpm-bindings.json"
        if cdpm_bindings:
            write_cdpm_bindings(target, cdpm_path)
        cosmos_path = (Path(target) / BINDINGS_FILE if cosmos_bindings
                       else tmp_path / "нет-привязок.json")
        settings = Settings(catalog_dir=Path(target), work_dir=work_dir,
                            actor=role, read_only=read_only,
                            cosmos_bindings=cosmos_path,
                            cdpm_bindings=cdpm_path,
                            markers_file=markers_file,
                            machine_token=machine_token)
        tc.forget_current()
        app = create_app(settings)
        svc = Service(TestClient(app), settings, app)
        made.append(svc)
        return svc

    yield build
    for svc in made:
        svc.client.close()
        svc.store.release_catalog_lock()
    tc.forget_current()


@pytest.fixture()
def service(make_service):
    """Служба владельца над свежей копией каталога."""
    return make_service("owner")


@pytest.fixture()
def git_repo(tmp_path):
    """Репозиторий git вокруг копии каталога с одним коммитом: поле
    `code` журнала версий — срез кода, и без репозитория выпуска не
    бывает."""
    def init(folder: Path) -> str:
        repo = Path(folder).parent
        env_args = [
            ("init", "-q"),
            ("config", "user.email", "test@example.invalid"),
            ("config", "user.name", "проба"),
            ("add", "-A", str(Path(folder).name)),
            ("commit", "-q", "-m", "каталог: проба"),
        ]
        for args in env_args:
            subprocess.run(["git", *args], cwd=str(repo), check=True,
                           capture_output=True, text=True, encoding="utf-8")
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                             capture_output=True, text=True, check=True,
                             encoding="utf-8")
        return out.stdout.strip()
    return init


def git_commit(folder: Path, message: str = "каталог: правка") -> str:
    """Закоммитить папку каталога в репозитории копии и вернуть HEAD."""
    repo = Path(folder).parent
    subprocess.run(["git", "add", "-A", Path(folder).name], cwd=str(repo),
                   check=True, capture_output=True, text=True,
                   encoding="utf-8")
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(repo),
                   check=True, capture_output=True, text=True,
                   encoding="utf-8")
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return out.stdout.strip()


def detail(response) -> dict:
    """Тело отказа: `{"code","words","problems"?,"extra"?}`."""
    body = response.json()
    assert isinstance(body, dict) and isinstance(body.get("detail"), dict), \
        f"отказ не по форме контракта: {body!r}"
    return body["detail"]


def sample_family(cat) -> dict:
    """Семья «есть» со словами-носителями — на ней удобно проверять
    правила лексикона."""
    for rec in cat.families_full():
        if isinstance(rec.get("subject_words"), list):
            return rec
    return cat.families_full()[0]                       # pragma: no cover


def make_release(folder: Path, version: str) -> Path:
    """Рукотворная папка выпуска `releases/<version>` из текущих файлов
    копии: тело то же (отпечаток сходится), шапка названа выпуском.
    Настоящий путь выпуска судит T7 — здесь нужен лишь адресат `?version=`."""
    from template_catalog import writer
    dst = writer.snapshot_release(folder, version)
    head = json.loads((dst / "catalog.json").read_text(encoding="utf-8"))
    head.update({"version": version, "released_by": "владелец"})
    (dst / "catalog.json").write_bytes(
        (json.dumps(head, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    return dst


def add_release(folder: Path, version: str, *, code: str | None = None
                ) -> dict:
    """Рукотворный выпуск в копии: папка `releases/<version>` и запись
    журнала версий. Нужен там, где проверяется правило, требующее БАЗЫ
    (подсказка уровня, пол версии), а сам путь выпуска судит T7."""
    make_release(folder, version)
    cat = tc.load(folder)
    entry = {"version": version, "fingerprint": cat.fingerprint(),
             "released_at": "2026-09-01", "released_by": "владелец",
             "code": code or ("a" * 40), "changes": []}
    doc = json.loads((folder / "versions.json").read_text(encoding="utf-8"))
    doc["versions"] = list(doc.get("versions") or []) + [entry]
    (folder / "versions.json").write_bytes(
        (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    tc.forget_current()
    return entry


def folder_bytes(folder: Path) -> dict:
    """Байты всех файлов папки — страж «ничего не записано». Кэш
    интерпретатора (`__pycache__`) не в счёт: он не данные каталога."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(folder)).replace("\\", "/")] = p.read_bytes()
    return out


def catalog_git_status() -> str:
    """Что git видит по ДАННЫМ живого каталога (без `catalog/tools/` —
    это зона инструмента извлечения, к службе отношения не имеет).
    Свидетель сравнительный: судится не чистота дерева (правки соседней
    дорожки до коммита законны), а неизменность за время прогона."""
    paths = [str(p.relative_to(ROOT)).replace("\\", "/")
             for p in [*(ROOT / "catalog").glob("*.json"),
                       ROOT / "catalog" / "transition",
                       ROOT / "catalog" / "schema",
                       ROOT / "catalog" / "releases"] if p.exists()]
    out = subprocess.run(["git", "status", "--porcelain", "--", *paths],
                         cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8")
    return out.stdout if out.returncode == 0 else ""


@pytest.fixture(scope="session", autouse=True)
def live_catalog_bytes():
    """Снимок живого `catalog/` репозитория на начало прогона: с ним
    сверяется страж «тесты службы живой каталог не трогают».

    Фикстура АВТОИСПОЛЬЗУЕМАЯ: снимок обязан быть снят ДО первого теста
    службы, иначе сверка «до и после» судила бы сама себя (единственный
    потребитель снимал бы снимок в свою же секунду)."""
    releases = ROOT / "catalog" / "releases"
    return {"bytes": folder_bytes(ROOT / "catalog"),
            "git": catalog_git_status(),
            "releases": sorted(p.name for p in releases.iterdir())
            if releases.is_dir() else []}


__all__ = ["GREEN_RULES", "MARKER", "RED_RULES", "ROOT", "Service",
           "WARNING_RULES", "add_release", "bump_mtime",
           "catalog_git_status", "detail", "folder_bytes", "git_commit",
           "make_release", "sample_family", "write_cdpm_bindings",
           "write_markers", "write_parity_tool"]
