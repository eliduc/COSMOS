# -*- coding: utf-8 -*-
"""Расчёт по пакету постановки в COSMOS (webapp.package_runs) —
стенд переезда кнопки из CDPM (решение владельца 04.09.2026: CDPM
заканчивается выпуском и экспортом ВМП).

Известные ответы: (1) кнопка нажимается на ТЕКУЩЕМ пакете без каталога
прогона CDPM — модель читается из байтов пакета; (2) движок отбора
пейринга — только здесь, чистым пробросом; (3) артефакты и ссылки —
по тем же алиасам, что раньше у панели; (4) ручки COSMOS: загрузка
пакета человеком, нажатие, состояние, файл, карта ростера; чужие
алиасы и пустые состояния — отказ словами."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_button import (  # noqa: E402
    FakeSolver, _builder_fakes, _fakes, _CATALOG_OK)
from tests.test_task_channel import _pkg_bytes  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.delenv("CREW_VMP_REGISTRY", raising=False)
    monkeypatch.delenv("CREW_SOLVER_URL", raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    # Реестр исполнителей при нажатии — как в стендах кнопки: фикстура
    # закрывает все правила свода живыми отпечатками кода.
    from vmp import registry_check as _regc
    from vmp.run import button as btn
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)


def _make_current(tmp_path, doc: dict | None = None) -> str:
    """Пакет принимается тем же путём, что загрузка/канал, и становится
    текущим; возвращает sha. doc — модель стенда (умолчание — _ready_doc
    канала)."""
    import hashlib
    from webapp import task_channel
    body = _pkg_bytes(tmp_path / "src", doc)
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha)
    assert code in (200, 201, 202), brief
    cur = task_channel.current_package()
    assert cur and hashlib.sha256(Path(cur["path"]).read_bytes()).hexdigest() == sha, cur
    return sha


def _pairing_kwargs(catalog=None):
    fakes = _builder_fakes(_fakes(True), catalog)
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    fakes["pairing_certify_fn"] = None          # сертификат пейринга пропущен
    return fakes


def test_нажатие_на_пакете_без_каталога_cdpm(tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    assert pr.status()["present"] and pr.status()["task"] is None
    # Предупреждение до нажатия: у ростеринга стендового пакета критерия
    # нет (клетка матрицы готовности criterion.defined=false), у
    # пейринга есть — страница предупреждает поимённо.
    assert pr.status()["no_criterion"] == {"rostering": True}
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)      # свой построитель ниже
    builder_calls = []

    def builder(payload, args, out):
        builder_calls.append(list(args))
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)   # как настоящий построитель
        return raw

    st = pr.press("pairing", engine="cplex", solver=FakeSolver(),
                  pairing_builder=builder, start_thread=False,
                  sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["error"] is None, st
    assert st["verdict"] == "пейринг построен", st
    assert st["task"] == "pairing" and st["engine"] == "cplex"
    assert st["package_sha"] == sha and st["tasks_done"] == ["pairing"]
    assert builder_calls, "построитель не вызван"
    # Артефакты — в каталоге данных package-runs/<sha>/button-pairing/,
    # ссылки — по прежним алиасам.
    wd = pr.workdir_for(sha)
    for name in ("delivery.json", "log.json", "package.zip",
                 "builder-catalog.json"):
        assert (wd / "button-pairing" / name).is_file(), name
    assert "каталог-цепочек" in st["files"]["pairing"]
    assert "пакет-кнопки-пейринга" in st["files"]["pairing"]
    path, name = pr.artifact_path("выдача-кнопки-пейринга")
    assert path.is_file() and name == "delivery.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["verdict"] == "пейринг построен"
    # Режим задачи словами — в выдаче и состоянии (Ш-1к): манифест
    # разрешает пейрингу только feasibility-hard.
    assert d["mode"] == "feasibility-hard" and st["mode"] == "feasibility-hard"
    assert "любое решение, выполняющее все жёсткие" in st["mode_note"]
    # Журнал: Ш-1 прошёл по байтам пакета (экспорта из прогона нет).
    log = json.loads((wd / "button-pairing" / "log.json")
                     .read_text(encoding="utf-8"))
    assert any(e["step"] == "Ш-1" and e["status"] == "пройден" for e in log)
    assert any(e["step"] == "Ш-1к" and e["status"] == "режим задачи"
               for e in log)
    # Пакет, ушедший решателю, — байт в байт текущий пакет.
    cur = pr.current()
    assert (wd / "button-pairing" / "package.zip").read_bytes() \
        == Path(cur["path"]).read_bytes()


def test_пакет_без_критерия_режим_допустимости_со_штрафами(tmp_path):
    """Задание владельца 04.09 (ИДАК H0-К — H0 без критерия
    оптимизации): нажатие не останавливается, а сообщает словами, что
    критерия нет и возможен только поиск допустимого решения с целевой
    = взвешенной суммой штрафов (feasibility-soft), и ведёт задачу в
    этом режиме. Описание поиска не гарантирует его исход (W-20, единая
    версия 06.09.2026); режим и пояснение — в выдаче, журнале и
    состоянии."""
    from webapp import package_runs as pr
    from webapp import task_channel
    from vmp.run import button as btn
    from tests.test_task_channel import _machined_doc
    import zipfile
    # Аудит A1: ведомость исполнения судит мягкое правило зоны по
    # МОДЕЛИ ПАКЕТА — семья и вес обязаны дойти до цели построителя
    # (режим feasibility-soft — целевая из штрафов). Модель стенда несёт
    # машинную семью (классификатор экспорта) и вес; подмена загрузчика
    # пакета оставляет настоящую модель из байтов пакета (прежде фикстура
    # прятала модель, и штраф правила молча выпадал из цели — тот самый
    # класс ошибки, который ловит ведомость).
    _doc = _machined_doc()
    _doc["rules"][1]["weight"] = 10
    _make_current(tmp_path, _doc)
    _model = json.loads(zipfile.ZipFile(task_channel.current_package()["path"])
                        .read("model.json"))
    fakes = _pairing_kwargs()
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-soft", "feasibility-hard"],
                          "rostering": ["feasibility-soft"]},
        "objectives": {}, "spec_version": "1.14"}, "model": _model}
    seen = []

    def builder(payload, args, out):
        seen.append(list(args))
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    fakes.pop("pairing_builder", None)
    st = pr.press("pairing", solver=FakeSolver(), pairing_builder=builder,
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["error"] is None, st
    assert st["verdict"] == "пейринг построен"
    assert st["mode"] == "feasibility-soft"
    assert "критерия оптимизации в пакете нет" in st["mode_note"]
    assert "взвешенной сумме штрафов" in st["mode_note"]
    d = json.loads(pr.artifact_path("выдача-кнопки-пейринга")[0]
                   .read_text(encoding="utf-8"))
    assert d["mode"] == "feasibility-soft" and d["objective"] is None
    log = json.loads((pr.workdir_for(st["package_sha"]) / "button-pairing"
                      / "log.json").read_text(encoding="utf-8"))
    note = [e for e in log if e["step"] == "Ш-1к"]
    assert note and "критерия оптимизации в пакете нет" in note[0]["detail"]
    # Описание режима до запуска не выдаётся за свидетельство
    # допустимости или оптимума (W-20): один текст в журнале, выдаче и
    # состоянии, без «выполнены» и «оптимально».
    assert note[0]["detail"] == d["mode_note"] == st["mode_note"]
    assert "Допустимость подтверждается только после расчёта и проверки" in note[0]["detail"]
    assert "оптимум не гарантируется" in note[0]["detail"]
    assert "жёсткие ограничения выполнены" not in note[0]["detail"]
    assert "решение оптимально" not in note[0]["detail"]
    # Словарь режимов — один для журнала, выдачи и страницы.
    assert "оптимизация по критерию" in btn.mode_statement("optimization", "O-1")
    assert "feasibility-hard" in btn.mode_statement("feasibility-hard", None)


@pytest.mark.parametrize("mode,objective", [
    ("optimization", "O-1"), ("feasibility-soft", None), ("feasibility-hard", None)])
def test_описание_режима_до_запуска_не_удостоверяет_результат(mode, objective):
    """W-20 (единая версия 06.09.2026, тест GPT как есть): описание
    режима — про то, что ищется, а не про то, что найдено."""
    from vmp.run import button as btn
    text = btn.mode_statement(mode, objective)
    assert "Допустимость подтверждается только после расчёта и проверки" in text
    assert "стоимость и качество поиска оцениваются отдельно" in text
    assert "оптимум не гарантируется" in text
    assert "жёсткие ограничения выполнены" not in text
    assert "решение оптимально" not in text
    if objective:
        assert objective in text
    else:
        assert mode in text


def test_движок_только_здесь_и_только_из_словаря(tmp_path):
    from webapp import package_runs as pr
    from vmp.run import button as btn
    _make_current(tmp_path)
    with pytest.raises(pr.PackageRunError):
        pr.press("pairing", engine="gurobi", solver=FakeSolver(),
                 start_thread=False, **_pairing_kwargs())
    with pytest.raises(pr.PackageRunError):
        pr.press("planning", solver=FakeSolver(), start_thread=False)
    # Проброс движка построителю — ключом --select-engine, без ветвлений.
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text(json.dumps(_CATALOG_OK), encoding="utf-8")

        class P:
            stderr = ""
            stdout = ""
        return P()
    import subprocess
    orig = subprocess.run
    subprocess.run = fake_run
    try:
        build = btn.subprocess_pairing_builder("t", select_engine="highs")
        build({"x": 1}, ["--level", "3"], tmp_path)
    finally:
        subprocess.run = orig
    cmd = seen["cmd"]
    assert cmd[cmd.index("--select-engine") + 1] == "highs"
    build2 = btn.subprocess_pairing_builder("t")
    subprocess.run = fake_run
    try:
        build2({"x": 1}, ["--level", "3"], tmp_path)
    finally:
        subprocess.run = orig
    assert "--select-engine" not in seen["cmd"]


def test_без_пакета_и_чужой_алиас_отказ_словами(tmp_path):
    from webapp import package_runs as pr
    assert pr.status() == {"present": False}
    with pytest.raises(pr.PackageRunError):
        pr.press("pairing", solver=FakeSolver(), start_thread=False)
    _make_current(tmp_path)
    with pytest.raises(pr.PackageRunError):
        pr.artifact_path("модель")            # артефакт CDPM, не кнопки
    with pytest.raises(pr.PackageRunError):
        pr.artifact_path("каталог-цепочек")   # ещё не построен
    with pytest.raises(pr.PackageRunError):
        pr.roster_map()


def test_допуск_моделей_из_манифеста_пакета(tmp_path):
    """kit.llm_scope манифеста — действующий круг, вычисленный CDPM
    (единственная реализация правила); COSMOS его не пересчитывает:
    класс данных без llm_scope здесь ничего не разрешает, отсутствие
    ключа — только локальные модели."""
    import io
    import zipfile
    from webapp import package_runs as pr
    body = _pkg_bytes(tmp_path / "src")
    p = tmp_path / "a.zip"
    p.write_bytes(body)
    assert pr.package_scope(p) == "local-only"

    def _with_kit(kit):
        zin = zipfile.ZipFile(io.BytesIO(body))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zout:
            for n in zin.namelist():
                data = zin.read(n)
                if n == "manifest.json":
                    man = json.loads(data)
                    man["kit"] = kit
                    data = json.dumps(man, ensure_ascii=False).encode("utf-8")
                zout.writestr(n, data)
        q = tmp_path / "k.zip"
        q.write_bytes(out.getvalue())
        return q
    # класс без действующего круга — не разрешение (правило считает CDPM)
    assert pr.package_scope(_with_kit({"data_class": "synthetic"})) \
        == "local-only"
    assert pr.package_scope(_with_kit({"data_class": "synthetic",
                                       "llm_scope": "any",
                                       "llm_scope_source":
                                       "умолчание по классу данных"})) == "any"
    assert pr.package_scope(_with_kit({"data_class": "proprietary",
                                       "llm_scope": "local-only"})) \
        == "local-only"
    assert pr.package_scope(_with_kit({"llm_scope": "public"})) == "local-only"


def test_повторное_нажатие_чистит_старые_артефакты_и_несёт_сертификатора(tmp_path):
    """Заявки CDPM 04.09: (1) старый сертификат/выдача не остаются
    живыми после нового нажатия той же задачи; (2) состояние и шапка
    сертификата называют канал и модель сертификатора."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    stale = wd / "button-pairing"
    stale.mkdir(parents=True)
    (stale / "certificate.json").write_text('{"stale": true}', encoding="utf-8")
    (wd / "export-mps").mkdir()
    (wd / "export-mps" / "model.mps").write_text("x", encoding="ascii")
    fakes = _pairing_kwargs()          # сертификат пейринга пропущен
    fakes.pop("pairing_builder", None)

    def builder(payload, args, out):
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    st = pr.press("pairing", solver=FakeSolver(), pairing_builder=builder,
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["verdict"] == "пейринг построен", st
    assert not (wd / "export-mps").exists(), "экспорт прежнего нажатия убран"
    assert not (stale / "certificate.json").exists(), \
        "старый сертификат остался живым"
    assert (stale / "delivery.json").is_file()
    assert st["certifier"] and st["certifier"]["scope"] in ("any", "local-only")
    assert set(st["certifier"]) >= {"scope", "backend", "model"}
    # Состояние страницы называет и того, кто сертифицирует СЛЕДУЮЩЕЕ
    # нажатие (настройки площадки сейчас), не только последнее.
    now = pr.status()["certifier_now"]
    assert now["scope"] == st["certifier"]["scope"] and "channel" in now
    ident = pr.certifier_identity("local-only")
    assert ident["backend"] == "local" and ident["scope"] == "local-only"


def test_модель_сертификатора_только_явная_по_api(tmp_path, monkeypatch):
    """Решение владельца 04.09 «Должна быть модель claude sonnet 5»:
    при допуске «any» сертификатор идёт только по API с моделью из
    настройки установки; нет настройки или ключа — отказ до старта;
    identity называет модель; «local-only» — локальная модель."""
    from webapp import package_runs as pr
    from webapp import llm_backend, settings as _settings
    monkeypatch.delenv(pr.CERTIFIER_MODEL_ENV, raising=False)
    monkeypatch.delenv(pr.CERTIFIER_CHANNEL_ENV, raising=False)
    ident = pr.certifier_identity("any")
    assert ident["model"] is None and "не задана" in ident["error"]
    monkeypatch.setenv(pr.CERTIFIER_MODEL_ENV, "claude-sonnet-5")
    # Канал по умолчанию — CLI с явной моделью (решение владельца
    # «Давай CLI с моделью»); без найденного CLI — отказ.
    monkeypatch.setattr(llm_backend, "resolve_claude_cli", lambda: None)
    ident = pr.certifier_identity("any")
    assert ident["channel"] == "cli" and "CLI не найден" in ident["error"]
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "C:/claude.cmd")
    ident = pr.certifier_identity("any")
    assert ident == {"scope": "any", "backend": "cloud", "channel": "cli",
                     "model": "claude-sonnet-5"}
    # Канал api — только при ключе API COSMOS; ключ CDPM прокидывается
    # в процесс тем же sync_cloud_key_to_env (здесь — заглушка).
    from cdpm import providers as providers_mod
    synced = []
    monkeypatch.setattr(providers_mod, "sync_cloud_key_to_env",
                        lambda: synced.append(1) or False)
    monkeypatch.setenv(pr.CERTIFIER_CHANNEL_ENV, "api")
    monkeypatch.setattr(_settings, "has_api_key", lambda: False)
    ident = pr.certifier_identity("any")
    assert ident["channel"] == "api" and "ключ API" in ident["error"]
    monkeypatch.setattr(_settings, "has_api_key", lambda: True)
    assert pr.certifier_identity("any") == {
        "scope": "any", "backend": "cloud", "channel": "api",
        "model": "claude-sonnet-5"}
    assert synced, "ключ CDPM прокидывается в процесс при канале api"
    monkeypatch.setenv(pr.CERTIFIER_CHANNEL_ENV, "telepathy")
    assert "вне словаря" in pr.certifier_identity("any")["error"]
    monkeypatch.delenv(pr.CERTIFIER_CHANNEL_ENV)
    local = pr.certifier_identity("local-only")
    assert local["backend"] == "local" and local["channel"] == "local"
    # Адрес решателя — только явная настройка площадки; умолчания порта
    # контура нет (решение владельца 04.09 «полная симметрия»): без
    # строки — честный отказ словами, не чужой решатель.
    monkeypatch.delenv("CREW_SOLVER_URL", raising=False)
    with pytest.raises(pr.PackageRunError, match="адрес решателя не задан"):
        pr.solver_url()
    monkeypatch.setenv("CREW_SOLVER_URL", "  ")
    with pytest.raises(pr.PackageRunError):
        pr.solver_url()
    monkeypatch.setenv("CREW_SOLVER_URL", "http://127.0.0.1:9099/")
    assert pr.solver_url() == "http://127.0.0.1:9099/"
    # Канал cli зовёт CLI ровно с этой моделью (--model), без подстановок.
    seen = {}

    def fake_cli(prompt, timeout_s, cwd=None, model=None):
        seen["model"] = model
        return "ok"
    monkeypatch.setattr(llm_backend, "_cli_generate", fake_cli)
    assert pr.cloud_certifier_channel("claude-sonnet-5", "cli")("p") == "ok"
    assert seen["model"] == "claude-sonnet-5"
    # Нажатие с собственной сертификацией без модели — отказ словами,
    # состояние не меняется.
    monkeypatch.delenv(pr.CERTIFIER_MODEL_ENV)
    import io
    import zipfile
    from webapp import task_channel
    body = _pkg_bytes(tmp_path / "src")
    zin = zipfile.ZipFile(io.BytesIO(body))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "manifest.json":
                man = json.loads(data)
                man["kit"] = {"data_class": "synthetic", "llm_scope": "any",
                              "llm_scope_source": "признак комплекта",
                              "llm_scope_basis": "стенд COSMOS"}
                data = json.dumps(man, ensure_ascii=False).encode("utf-8")
            zout.writestr(n, data)
    import hashlib
    raw = out.getvalue()
    code, brief = task_channel.receive(raw, hashlib.sha256(raw).hexdigest())
    assert code in (200, 201, 202), brief
    with pytest.raises(pr.PackageRunError) as e:
        pr.press("pairing", solver=FakeSolver(), start_thread=False,
                 load_pkg=lambda p: {"manifest": {}}, to_payload=lambda p: {})
    assert "модель сертификатора" in str(e.value)
    assert pr.status()["task"] is None


def test_экспорт_mps_по_последнему_нажатию_пейринга(tmp_path):
    """Экспорт точной модели для внешнего CPLEX идёт по артефактам
    последнего нажатия «Пейринг» с теми же аргументами плюс
    --export-only; файлы — по перечню манифеста; без нажатия — отказ."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    with pytest.raises(pr.PackageRunError):
        pr.export_mps(start_thread=False)
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)

    def builder(payload, args, out):
        # как настоящий подпроцесс-построитель: payload, вход, каталог
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-payload.json").write_text(
            json.dumps(payload), encoding="utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        (Path(out) / "builder-input.json").write_text(
            json.dumps({"args": list(args)}), encoding="utf-8")
        return raw
    pr.press("pairing", solver=FakeSolver(), pairing_builder=builder,
             start_thread=False, sleep=lambda *_: None, **fakes)
    seen = {}

    def factory(label):
        def build(payload, args, out):
            seen["args"] = list(args)
            i = args.index("--export-mps")
            mps = Path(args[i + 1])
            mps.write_text("NAME exact_cover\nROWS\nENDATA\n", encoding="ascii")
            Path(args[args.index("--export-lp") + 1]).write_text(
                "Minimize\n obj: \nEnd\n", encoding="ascii")
            Path(str(mps) + ".map.json").write_text(json.dumps({
                "variables": {"x0": {"fns": ["G-1"], "dh": [], "cost": 1,
                                     "crew": 2}},
                "constraints": {"c0": {"kind": "cover", "flight": "G-1"}},
                "objective": {"sense": "min", "kind": "тест"},
                "pool": 1, "exact": True, "obligations": 1,
                "uncoverable_flights": [], "fixed_zero_vars": []}),
                encoding="utf-8")
            return b""
        return build
    st = pr.export_mps(start_thread=False, builder_factory=factory)
    assert st["export_mps_state"] == "экспорт готов", st
    assert "--export-only" in seen["args"]
    names = st["export_mps_files"]
    assert any(n.endswith("-partial-set-cover.mps") for n in names), names
    assert "export-manifest.json" in names
    for n in names:
        assert pr.export_file(n).is_file(), n
    with pytest.raises(pr.PackageRunError):
        pr.export_file("hack.txt")


# ------------------------------------------------- аудит A10 (04.09.2026)
# Изоляция запусков кнопки. Класс ошибки — «проверил одно поле, а меняю
# другое»: защита от повторного нажатия судила по ТЕКСТУ состояния
# («кнопка: идёт»), а запись хода перезаписывала тот же текст, не трогая
# done; проверка занятости и запись «идёт» шли двумя захватами замка.
# Стенды с известным ответом: (1) повторное нажатие после первой записи
# хода — отказ, каталог идущего расчёта не тронут; (2) два одновременных
# нажатия — ровно одно занимает; (3) сирота (done=False без живого
# потока) помечается «прервано перезапуском», кнопка не заперта навсегда.


def _catalog_builder(hook=None):
    def builder(payload, args, out):
        if hook:
            hook(Path(out))
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    return builder


def test_A10_повторное_нажатие_после_записи_хода_отказ_и_каталог_цел(tmp_path):
    """Второе нажатие ПОСЛЕ первой записи хода (state уже не «кнопка:
    идёт» дословно в старом коде) обязано отказать «кнопка уже идёт» и
    не чистить каталог идущего расчёта; ход виден отдельным ключом
    progress, state остаётся «кнопка: идёт» до конца."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    seen: dict = {}

    def hook(out):
        # Мы внутри Ш-2: журнал уже получил Ш-0, Ш-1, Ш-1к, Ш-2 —
        # текст состояния в старом коде уже «кнопка: Ш-2 — …».
        (out / "marker.txt").write_text("идущий расчёт", encoding="utf-8")
        st = pr.status()
        seen["state"] = st.get("state")
        seen["progress"] = st.get("progress")
        seen["done"] = st.get("done")
        nested = _pairing_kwargs()
        nested.pop("pairing_builder", None)
        try:
            pr.press("pairing", solver=FakeSolver(),
                     pairing_builder=_catalog_builder(), start_thread=False,
                     sleep=lambda *_: None, **nested)
            seen["nested"] = None
        except pr.PackageRunError as e:
            seen["nested"] = str(e)
        seen["marker_alive"] = (out / "marker.txt").is_file()
        seen["package_alive"] = (out / "package.zip").is_file()

    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    st = pr.press("pairing", solver=FakeSolver(),
                  pairing_builder=_catalog_builder(hook), start_thread=False,
                  sleep=lambda *_: None, **fakes)
    assert seen["done"] is False
    assert seen["state"] == "кнопка: идёт", seen
    assert seen["progress"] and "Ш-2" in seen["progress"], seen
    assert seen["nested"] and "уже идёт" in seen["nested"], seen
    assert seen["marker_alive"] and seen["package_alive"], \
        "повторное нажатие вычистило каталог идущего расчёта"
    assert st["done"] is True and st["verdict"] == "пейринг построен", st
    assert st["progress"] is None
    assert (wd / "button-pairing" / "marker.txt").is_file()


def test_A10_два_одновременных_нажатия_занимает_одно(tmp_path):
    """Гонка: два нажатия проходят предварительные проверки одновременно
    (барьер в certifier_identity — точка между проверкой и записью в
    старом коде); занять кнопку обязано ровно одно, второе — отказ
    «кнопка уже идёт»; расчёт при этом один."""
    import threading
    from webapp import package_runs as pr
    _make_current(tmp_path)
    barrier = threading.Barrier(2, timeout=10)
    real_identity = pr.certifier_identity
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def slow_identity(scope):
        # Барьер — только на двух первых обращениях (это проверки двух
        # нажатий до занятия); status() зовёт ту же функцию позже.
        with calls_lock:
            calls["n"] += 1
            n = calls["n"]
        if n <= 2:
            barrier.wait()
        return real_identity(scope)
    pr.certifier_identity = slow_identity
    go = threading.Event()
    builds = []

    def hook(out):
        builds.append(str(out))
        assert go.wait(10), "стенд не отпустил построитель"
    outcomes: dict = {}

    def one(name):
        fakes = _pairing_kwargs()
        fakes.pop("pairing_builder", None)
        try:
            pr.press("pairing", solver=FakeSolver(),
                     pairing_builder=_catalog_builder(hook),
                     start_thread=True, sleep=lambda *_: None, **fakes)
            outcomes[name] = "занял"
        except pr.PackageRunError as e:
            outcomes[name] = "отказ: " + str(e)
    try:
        ts = [threading.Thread(target=one, args=(n,)) for n in ("a", "b")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
    finally:
        pr.certifier_identity = real_identity
        go.set()
    got = list(outcomes.values())
    assert len(got) == 2 and got.count("занял") == 1 \
        and sum(g.startswith("отказ: кнопка уже идёт") for g in got) == 1, \
        outcomes
    deadline = time.time() + 15
    while pr.status()["done"] is not True and time.time() < deadline:
        time.sleep(0.05)
    st = pr.status()
    assert st["done"] is True and st["verdict"] == "пейринг построен", st
    assert len(builds) == 1, builds


def test_A10_сирота_помечается_прервано_перезапуском(tmp_path, monkeypatch):
    """Состояние done=False без живого потока (гибель процесса посреди
    расчёта) — при первом status()/press() помечается «прервано
    перезапуском» словами, done=True; кнопка не заперта навсегда.
    Живой расчёт сиротой не считается.

    Чужой номер процесса стенда объявляется МЁРТВЫМ явно: с 10.09.2026
    сторож спрашивает операционную систему, жив ли процесс, и случайное
    совпадение номера с чужой живой программой сделало бы стенд
    плавающим (чужой живой процесс — уже не сирота, см. стенд ниже)."""
    import os
    import threading
    from webapp import package_runs as pr
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: int(pid) == os.getpid())
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / pr.STATE_NAME).write_text(json.dumps({
        "task": "pairing", "state": "кнопка: идёт", "done": False,
        "progress": "Ш-2 — построитель запущен",
        "pid": os.getpid() + 100003, "started_at": "2026-09-04T10:00:00"},
        ensure_ascii=False), encoding="utf-8")
    st = pr.status()
    assert st["done"] is True, st
    assert st["state"] == "кнопка: прервано перезапуском", st
    assert st["error"] and "прерван" in st["error"] \
        and "10:00:00" in st["error"], st
    assert st["progress"] is None and st["verdict"] is None
    # Тот же pid, но потока в реестре нет (процесс перезапущен с тем же
    # номером либо поток погиб, не записав итог) — тоже сирота.
    (wd / pr.STATE_NAME).write_text(json.dumps({
        "task": "rostering", "state": "кнопка: идёт", "done": False,
        "pid": os.getpid(), "started_at": "2026-09-04T11:00:00"},
        ensure_ascii=False), encoding="utf-8")
    lines = pr.recover_orphans()
    assert len(lines) == 1 and sha[:12] in lines[0], lines
    assert pr.read_state(wd)["done"] is True
    assert pr.recover_orphans() == []
    # После пометки кнопка нажимается снова.
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    go = threading.Event()
    started = threading.Event()

    def hook(out):
        started.set()
        assert go.wait(10)
    st = pr.press("pairing", solver=FakeSolver(),
                  pairing_builder=_catalog_builder(hook), start_thread=True,
                  sleep=lambda *_: None, **fakes)
    assert st["done"] is False and st["error"] is None, st
    assert started.wait(10)
    # Живой расчёт: сторож его не трогает, status() честно «идёт».
    assert pr.recover_orphans() == []
    st = pr.status()
    assert st["done"] is False and st["state"] == "кнопка: идёт", st
    assert st["pid"] == os.getpid() and st["started_at"]
    go.set()
    deadline = time.time() + 15
    while pr.status()["done"] is not True and time.time() < deadline:
        time.sleep(0.05)
    assert pr.status()["verdict"] == "пейринг построен"


def test_A10_реестр_нитей_не_теряет_запись_второго_нажатия(tmp_path,
                                                           monkeypatch):
    """Дефект 09.09.2026 (местная служба): нажатие «ростер» сразу за
    пейрингом помечалось «прервано перезапуском» ЛОЖНО — процесс тот
    же, нить жила до конца, вердикт лёг поверх пометки об ошибке.

    Причина — гонка «проверил — снял» в реестре живых нитей: нить
    прошлого нажатия проверяла, её ли запись в реестре, а между этой
    проверкой и снятием ту же линию занимало следующее нажатие —
    снятие стирало ЖИВУЮ запись нового. Здесь тот же порядок
    воспроизводится без сервера и решателя: пауза вставлена между
    чтением записи реестра и её снятием."""
    import threading
    from webapp import package_runs as pr

    wd = tmp_path / "рабочий-каталог"
    первая: dict = {"нить": None, "взведена": False}
    пауза_началась = threading.Event()

    class _РеестрСПаузой(dict):
        """Реестр живых нитей, отдающий значение ДО паузы: нить успевает
        решить «запись моя», а снимает её уже после чужого занятия."""

        def get(self, key, *default):
            значение = dict.get(self, key, *default)
            if первая["взведена"] \
                    and threading.current_thread() is первая["нить"]:
                первая["взведена"] = False
                пауза_началась.set()
                time.sleep(1.0)
            return значение

    monkeypatch.setattr(pr, "_RUNNING", _РеестрСПаузой(pr._RUNNING))
    # Первое нажатие: занятие линии и нить, которая честно дописывает
    # итог и уходит снимать свою запись из реестра.
    pr._claim_line(wd, "press", "кнопка уже идёт",
                   task="pairing", state="кнопка: идёт", done=False)

    def работа_первого():
        первая["нить"] = threading.current_thread()
        pr._update_state(wd, state="кнопка: пейринг построен", done=True,
                         verdict="пейринг построен")
        первая["взведена"] = True

    pr._run_line(wd, "press", работа_первого, True)
    assert пауза_началась.wait(10), "нить первого нажатия не дошла до снятия"
    # Второе нажатие занимает ту же линию, пока первая нить стоит между
    # проверкой и снятием, и запускает свою длинную работу.
    закончить = threading.Event()
    вторая_пошла = threading.Event()
    pr._claim_line(wd, "press", "кнопка уже идёт",
                   task="rostering", state="кнопка: идёт", done=False,
                   verdict=None, error=None)

    def работа_второго():
        вторая_пошла.set()
        закончить.wait(20)

    pr._run_line(wd, "press", работа_второго, True)
    assert вторая_пошла.wait(10), "нить второго нажатия не стартовала"
    try:
        первая["нить"].join(15)
        assert not первая["нить"].is_alive(), "первая нить не завершилась"
        # Линия занята ЖИВОЙ нитью того же процесса: сторож сирот её не
        # трогает, состояние остаётся «идёт».
        assert pr._line_alive(wd, "press") is True, \
            "живая нить второго нажатия потеряна реестром"
        st = pr._read_state_swept(wd)
        assert st["done"] is False and st.get("error") is None, st
        assert st["state"] == "кнопка: идёт", st
    finally:
        закончить.set()


def test_A10_экспорт_mps_занятие_атомарно_и_сирота(tmp_path, monkeypatch):
    """Тот же класс для экспорта модели: занятость — по флагу с живым
    потоком; сирота «экспорт идёт» без потока помечается словами.
    Чужой номер процесса стенда объявляется мёртвым явно (см. стенд
    сироты выше)."""
    import os
    from webapp import package_runs as pr
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: int(pid) == os.getpid())
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    src = wd / "button-pairing"
    src.mkdir(parents=True)
    for n in ("builder-payload.json", "builder-input.json", "package.zip"):
        (src / n).write_text('{"args": []}', encoding="utf-8")
    (wd / pr.STATE_NAME).write_text(json.dumps({
        "task": "pairing", "state": "кнопка: пейринг построен", "done": True,
        "export_mps_state": "экспорт идёт", "export_mps_pid": os.getpid() + 7,
        "export_mps_started_at": "2026-09-04T12:00:00"},
        ensure_ascii=False), encoding="utf-8")
    st = pr.status()
    assert st["export_mps_state"] == "экспорт: прерван перезапуском", st
    assert "12:00:00" in (st["export_mps_error"] or ""), st
    # Занятие экспортом живым потоком — второй экспорт отказывает.
    import threading
    go = threading.Event()

    def factory(label):
        def build(payload, args, out):
            assert go.wait(10)
            raise RuntimeError("стенд: модель не выгружена")
        return build
    st = pr.export_mps(start_thread=True, builder_factory=factory)
    assert st["export_mps_state"] == "экспорт идёт", st
    with pytest.raises(pr.PackageRunError, match="экспорт уже идёт"):
        pr.export_mps(start_thread=True, builder_factory=factory)
    # Нажатие «Пейринг» убирает каталог экспорта — при живом экспорте
    # оно ждёт его конца (тот же класс: чужой каталог не трогать).
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    with pytest.raises(pr.PackageRunError, match="экспорт модели ещё идёт"):
        pr.press("pairing", solver=FakeSolver(),
                 pairing_builder=_catalog_builder(), start_thread=False,
                 sleep=lambda *_: None, **fakes)
    assert pr.status()["done"] is True, "отказ не занял кнопку"
    go.set()
    deadline = time.time() + 15
    while pr.status()["export_mps_state"] == "экспорт идёт" \
            and time.time() < deadline:
        time.sleep(0.05)
    assert pr.status()["export_mps_state"] == "экспорт: отказ"


# ── Кто нажал: актор попытки (разбор 09.09.2026) ──
# На Prod 09.09.2026 ростер был нажат вторым актором из браузера, и
# разобрать по записям, кто это, было нельзя. Известные ответы: актор
# записан в попытку, виден в списке попыток и в записи одной попытки,
# назван в отказе «занято»; внутренний вызов без личности — поле
# словами «неизвестен», не пусто.


def test_актор_нажатия_словами_по_личности_запроса():
    """Слова актора строятся по личности ручки: человек в панели,
    режим разработчика площадки, машинный канал по токену. Сам токен и
    его части в записи не появляются — только отпечаток."""
    from webapp import package_runs as pr
    assert pr.actor_words({"email": "o@x.y", "role": "admin"},
                          mode="access") == "user:o@x.y"
    assert pr.actor_words({"email": "o@x.y", "role": "user"},
                          mode="dev") == "dev:o@x.y"
    token = "очень-секретный-токен-канала"
    words = pr.actor_words({"email": "channel", "role": "machine"},
                           channel_token=token, mode="token")
    assert words.startswith("channel:") and len(words) == len("channel:") + 12
    assert token not in words and token[:6] not in words
    # два разных токена различимы, один и тот же — одинаков
    assert words == pr.actor_words({"email": "channel", "role": "machine"},
                                   channel_token=token, mode="token")
    assert words != pr.actor_words({"email": "channel", "role": "machine"},
                                   channel_token=token + "2", mode="token")
    # личности нет — слова, а не пусто
    assert pr.actor_words(None) == pr.ACTOR_UNKNOWN
    assert pr.actor_words({"role": "admin"}) == pr.ACTOR_UNKNOWN
    assert pr._actor_of(None) == pr.ACTOR_UNKNOWN and pr._actor_of("  ") \
        == pr.ACTOR_UNKNOWN


def test_актор_записан_в_попытку_виден_в_списке_и_назван_в_отказе(tmp_path):
    """Актор нажатия — в записи попытки и в состоянии линии; список
    попыток его отдаёт; отказ занятой кнопки называет, КТО держит
    линию («занято попыткой …, актор user:…»)."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    seen: dict = {}

    def hook(out):
        st = pr.status()
        seen["actor"] = st.get("actor")
        seen["attempt_actor"] = (st.get("attempts") or [{}])[0].get("actor")
        nested = _pairing_kwargs()
        nested.pop("pairing_builder", None)
        try:
            pr.press("pairing", solver=FakeSolver(), actor="user:второй@x.y",
                     pairing_builder=_catalog_builder(), start_thread=False,
                     sleep=lambda *_: None, **nested)
            seen["отказ"] = None
        except pr.PackageRunError as e:
            seen["отказ"] = str(e)

    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    st = pr.press("pairing", solver=FakeSolver(), actor="user:владелец@x.y",
                  pairing_builder=_catalog_builder(hook), start_thread=False,
                  sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["verdict"] == "пейринг построен", st
    assert seen["actor"] == "user:владелец@x.y", seen
    assert seen["attempt_actor"] == "user:владелец@x.y", seen
    # Отказ 409 называет держателя линии поимённо.
    assert seen["отказ"] and "кнопка уже идёт" in seen["отказ"], seen
    assert "занято попыткой" in seen["отказ"] \
        and "user:владелец@x.y" in seen["отказ"], seen
    # Список попыток и запись одной попытки отдают актора.
    st = pr.status()
    assert st["actor"] == "user:владелец@x.y"
    rec = st["attempts"][0]
    assert rec["actor"] == "user:владелец@x.y"
    one = pr.status(attempt=rec["id"])
    assert one["attempt"]["actor"] == "user:владелец@x.y"
    # Внутренний вызов без личности — поле словами, не пусто.
    wd = pr.workdir_for(sha)
    st2 = pr.press("pairing", solver=FakeSolver(),
                   pairing_builder=_catalog_builder(), start_thread=False,
                   sleep=lambda *_: None, **_pairing_kwargs_no_builder())
    assert st2["actor"] == pr.ACTOR_UNKNOWN and st2["actor"], st2
    assert st2["attempts"][0]["actor"] == pr.ACTOR_UNKNOWN
    # Попытка ПРЕЖНЕЙ версии (ключа actor в записи нет) наружу уходит
    # словами «попытка до записи актора», а не пустым полем.
    old = pr.read_state(wd)
    old["attempts"].append({"id": "20260101T000000-old", "task": "pairing",
                            "state": "завершено", "done": True})
    pr._write_state(wd, old)
    rec = next(r for r in pr.status()["attempts"]
               if r["id"] == "20260101T000000-old")
    assert rec["actor"] == pr.ACTOR_PRE_VERSION


def _pairing_kwargs_no_builder():
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    return fakes


# ── Линию держит другой ЖИВОЙ процесс службы (разбор 09.09.2026) ──
# Чужой номер процесса в записи — ещё не смерть: живой чужой процесс
# бывает при перезапуске службы с перекрытием и при двух службах на
# разных портах с ОДНИМ каталогом данных. Тогда линия ЗАНЯТА, а не
# осиротела, и чистить её каталог нельзя.


def _busy_state(pid: int, started: str = "2026-09-09T23:10:00",
                **extra) -> dict:
    return {"task": "rostering", "state": "кнопка: идёт", "done": False,
            "progress": "Ш-2 — решатель считает", "pid": pid,
            "started_at": started, "attempt_id": "20260909T231000-aaa111",
            "attempts": [{"id": "20260909T231000-aaa111", "task": "rostering",
                          "state": "идёт", "done": False,
                          "actor": "user:владелец@x.y"}], **extra}


def test_чужой_живой_процесс_держит_линию_и_не_сирота(tmp_path, monkeypatch):
    """Чужой номер процесса + процесс ЖИВ — линия не сирота: пометка
    словами «линию держит другой живой процесс службы», состояние
    остаётся «идёт», нажатие отказывает теми же словами."""
    import os
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    wd.mkdir(parents=True, exist_ok=True)
    чужой = os.getpid() + 100003
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: int(pid) == чужой
                        or int(pid) == os.getpid())
    # Процесс стартовал РАНЬШЕ занятия линии — номер не переиспользован.
    monkeypatch.setattr(pr, "_pid_created_at",
                        lambda pid: "2026-09-09T20:00:00")
    pr._write_state(wd, _busy_state(чужой))
    st = pr.status()
    assert st["done"] is False, "чужой живой процесс объявлен сиротой"
    assert st["state"] == "кнопка: идёт" and st["error"] is None, st
    assert st["holder_note"] and "другой живой процесс службы" in st["holder_note"]
    assert str(чужой) in st["holder_note"] and "23:10:00" in st["holder_note"]
    assert "остановите тот процесс" in st["holder_note"]
    assert pr.recover_orphans() == [], "сторож тронул чужой живой процесс"
    fakes = _pairing_kwargs_no_builder()
    with pytest.raises(pr.PackageRunError) as e:
        pr.press("pairing", solver=FakeSolver(),
                 pairing_builder=_catalog_builder(), start_thread=False,
                 sleep=lambda *_: None, **fakes)
    assert "другой живой процесс службы" in str(e.value) \
        and str(чужой) in str(e.value), str(e.value)
    assert e.value.reason_code == "run-busy"
    # Артефакты идущего расчёта отказ не тронул: каталог нажатия цел.
    assert not (wd / "button-pairing").exists()


def test_чужой_мёртвый_процесс_и_свой_pid_без_нити_сироты(tmp_path,
                                                          monkeypatch):
    """Чужой номер, процесса нет — прежняя пометка сироты; свой номер
    без живой нити — тоже сирота (нить погибла, не записав итог)."""
    import os
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    wd.mkdir(parents=True, exist_ok=True)
    чужой = os.getpid() + 100003
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: int(pid) == os.getpid())
    pr._write_state(wd, _busy_state(чужой))
    st = pr.status()
    assert st["done"] is True and st["state"] == "кнопка: прервано перезапуском"
    assert st["holder_note"] is None, st
    assert "23:10:00" in (st["error"] or ""), st
    assert st["attempts"][0]["state"] == "прервано перезапуском"
    # Свой номер, живой нити нет — сирота (реестр нитей пуст).
    pr._write_state(wd, _busy_state(os.getpid()))
    st = pr.status()
    assert st["done"] is True and st["state"] == "кнопка: прервано перезапуском"
    assert st["holder_note"] is None, st


def test_переиспользованный_номер_процесса_считается_мёртвым(tmp_path,
                                                             monkeypatch):
    """Номера процессов ОС переиспользует: живой номер признаётся
    держателем только тогда, когда время не опровергает его — момент
    старта процесса совпал с записанным при занятии, а без записи не
    оказался ПОЗЖЕ момента занятия линии."""
    import os
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    wd.mkdir(parents=True, exist_ok=True)
    чужой = os.getpid() + 100003
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: True)
    # (1) записи о старте процесса нет, а живой процесс с этим номером
    # стартовал ПОСЛЕ занятия линии — номер достался другой программе.
    monkeypatch.setattr(pr, "_pid_created_at",
                        lambda pid: "2026-09-09T23:40:00")
    pr._write_state(wd, _busy_state(чужой))
    assert pr.status()["state"] == "кнопка: прервано перезапуском"
    # (2) запись о старте есть и НЕ совпала — тот же вывод.
    monkeypatch.setattr(pr, "_pid_created_at",
                        lambda pid: "2026-09-09T19:00:00")
    pr._write_state(wd, _busy_state(чужой, pid_created="2026-09-08T07:00:00"))
    assert pr.status()["state"] == "кнопка: прервано перезапуском"
    # (3) запись о старте совпала — это тот самый живой процесс службы.
    pr._write_state(wd, _busy_state(чужой, pid_created="2026-09-09T19:00:00"))
    st = pr.status()
    assert st["done"] is False and st["state"] == "кнопка: идёт", st
    assert "другой живой процесс службы" in (st["holder_note"] or "")
    # (4) момент старта процесса узнать нельзя — верим живому номеру:
    # ошибиться в сторону «занято» безопаснее, чем чистить чужой каталог.
    monkeypatch.setattr(pr, "_pid_created_at", lambda pid: None)
    pr._write_state(wd, _busy_state(чужой))
    st = pr.status()
    assert st["done"] is False and "другой живой процесс" in (st["holder_note"] or "")


def test_занятие_линии_записывает_момент_старта_своего_процесса(tmp_path):
    """При занятии линии в состояние пишется момент старта СВОЕГО
    процесса: по нему чужая служба отличит живой процесс-держатель от
    чужой программы с тем же номером."""
    import os
    from webapp import package_runs as pr
    wd = tmp_path / "рабочий-каталог"
    pr._claim_line(wd, "press", "кнопка уже идёт",
                   task="pairing", state="кнопка: идёт", done=False)
    st = pr.read_state(wd)
    assert st["pid"] == os.getpid() and st["started_at"]
    assert st["pid_created"] == pr._pid_created_at(os.getpid())
    assert st.get("holder_note") is None
    pr._update_state(wd, done=True, state="кнопка: пейринг построен")


# ------------------------------------------------------------ ручки

ADMIN = ADMIN_EMAIL


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from webapp import users
    cfg = tmp_path_factory.mktemp("pkg-runs-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    mp.setattr(users, "USERS_DB_PATH", cfg / "users.db")
    users.ensure_db()
    import importlib
    from webapp import app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        mp.undo()


def test_ручки_загрузка_состояние_файл_карта(client, tmp_path, monkeypatch):
    from webapp import package_runs as pr
    r = client.get("/api/task-package/current/press")
    assert r.status_code == 200 and r.json() == {"present": False}
    body = _pkg_bytes(tmp_path / "src")
    r = client.post("/api/task-package/upload",
                    files={"file": ("h0.zip", body, "application/zip")})
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/task-package/current").json()["present"]
    r = client.get("/api/task-package/current/press")
    assert r.json()["present"] and r.json()["task"] is None
    assert r.json()["engines"] == ["cpsat", "cplex", "highs"]
    # Не zip — отказ словами.
    r = client.post("/api/task-package/upload",
                    files={"file": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400
    # Нажатие: ручка зовёт press() с параметрами как есть.
    seen = {}

    def fake_press(task, **kw):
        seen.update(task=task, **kw)
        return {"present": True, "task": task, "state": "кнопка: идёт"}
    monkeypatch.setattr(pr, "press", fake_press)
    r = client.post("/api/task-package/current/press?task=pairing"
                    "&engine=cplex&spentLimitSeconds=30")
    assert r.status_code == 200, r.text
    assert seen["task"] == "pairing" and seen["engine"] == "cplex"
    assert seen["spent_limit_s"] == 30
    monkeypatch.setattr(pr, "press", lambda *a, **k: (_ for _ in ()).throw(
        pr.PackageRunError("кнопка уже идёт")))
    r = client.post("/api/task-package/current/press?task=pairing")
    assert r.status_code == 409 and "уже идёт" in r.text
    # Файл: чужой алиас и непостроенный артефакт — 404 словами.
    assert client.get("/api/task-package/current/press/file/модель")\
        .status_code == 404
    assert client.get("/api/task-package/current/press/file/каталог-цепочек")\
        .status_code == 404
    # Карта ростера без решения — 409.
    assert client.get("/api/task-package/current/roster-map").status_code == 409


def test_ручка_нажатия_называет_актора_запроса(client, tmp_path, monkeypatch):
    """Ручка нажатия строит актора по личности запроса и передаёт его
    кнопке: сессия площадки — «dev:<почта>» в режиме разработчика,
    машинный канал — «channel:<отпечаток токена>»; сам токен в запись
    не уходит (разбор 09.09.2026)."""
    from webapp import package_runs as pr
    body = _pkg_bytes(tmp_path / "src")
    r = client.post("/api/task-package/upload",
                    files={"file": ("h0.zip", body, "application/zip")})
    assert r.status_code in (200, 201), r.text
    seen: dict = {}
    monkeypatch.setattr(pr, "press", lambda task, **kw:
                        seen.update(task=task, **kw) or {"present": True})
    r = client.post("/api/task-package/current/press?task=pairing")
    assert r.status_code == 200, r.text
    assert seen["actor"] == "dev:" + ADMIN, seen
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "t-2026")
    r = client.post("/api/task-package/current/press?task=pairing",
                    headers={"X-Channel-Token": "t-2026"})
    assert r.status_code == 200, r.text
    assert seen["actor"].startswith("channel:"), seen
    assert "t-2026" not in seen["actor"], "токен канала попал в запись"


def test_машинный_вход_по_токену_канала(client, tmp_path, monkeypatch):
    """Раннеры на площадках идут без сессии: X-Channel-Token как у
    /receive; неверный токен — 403; без заголовка — обычная сессия."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "t-2026")
    ok = {"X-Channel-Token": "t-2026"}
    bad = {"X-Channel-Token": "wrong-token"}
    r = client.get("/api/task-package/current/press", headers=ok)
    assert r.status_code == 200 and r.json() == {"present": False}
    assert client.get("/api/task-package/current/press",
                      headers=bad).status_code == 403
    body = _pkg_bytes(tmp_path / "src")
    r = client.post("/api/task-package/upload", headers=ok,
                    files={"file": ("h0.zip", body, "application/zip")})
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/task-package/current/press/file/каталог-цепочек",
                      headers=bad).status_code == 403
    assert client.get("/api/task-package/current/press/file/каталог-цепочек",
                      headers=ok).status_code == 404       # ещё не построен
    assert client.post("/api/task-package/current/export-mps",
                       headers=ok).status_code == 409      # нет нажатия
    # Канал выключен (нет CREW_CHANNEL_TOKEN) — токен не открывает вход.
    monkeypatch.delenv("CREW_CHANNEL_TOKEN")
    assert client.get("/api/task-package/current/press",
                      headers=ok).status_code == 403


def test_A8_состояние_страницы_несёт_три_статуса(tmp_path):
    """Аудит A8 (04.09.2026): состояние кнопки на странице «Пакет
    постановки» несёт трёхчастный результат последней задачи и перечень
    непроверенных правил, а штатный сертификатор вызывается строго.
    Пейринг без сертификатора — допустимость честно «не установлена»."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    fakes = _pairing_kwargs()
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["error"] is None, st
    assert st["verdict"] == "пейринг построен", st
    res = st["result"]
    assert res["feasibility"] == "not_established"
    assert res["optimality"] == "not_proven"
    assert res["coverage_complete"] is None
    assert res["search_incomplete"] is False
    assert st["uncovered"] == [] and st["certificate_gate"] == []
    d = json.loads(pr.artifact_path("выдача-кнопки-пейринга")[0]
                   .read_text(encoding="utf-8"))
    assert d["result"] == res
    # Штатный сертификатор — строго: вызов с ослаблением отказывает.
    import inspect
    src = inspect.getsource(pr.press)
    assert "strict_coverage=True" in src


def test_A1_штатное_нажатие_подаёт_контракт_ВМП_с_отпечатком(tmp_path):
    """Внешний аудит 04.09.2026, A1: штатное нажатие (страница/ручка →
    package_runs.press → orchestrate) подаёт построителю контракт правил
    --source-rules с отпечатком и профиль пакета; ведомость исполнения
    правил — артефакт нажатия, доступный по алиасу страницы. Прежде
    флаги-включатели оркестратора на штатном пути не передавались, и
    контракт оставался в следе."""
    import hashlib
    from webapp import package_runs as pr
    _make_current(tmp_path)
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    builder_calls = []

    def builder(payload, args, out):
        builder_calls.append(list(args))
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw

    st = pr.press("pairing", solver=FakeSolver(), pairing_builder=builder,
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["verdict"] == "пейринг построен", st
    args = builder_calls[0]
    rules_p = Path(args[args.index("--source-rules") + 1])
    assert rules_p.is_file() and rules_p.name == "source-rules.json"
    assert args[args.index("--source-rules-fp") + 1] \
        == hashlib.sha256(rules_p.read_bytes()).hexdigest()
    assert "--vmp-package" in args
    assert "--max-segs" in args and "--pairing-span" in args
    # Ведомость исполнения правил — по алиасу страницы.
    assert "ведомость-исполнения-пейринга" in st["files"]["pairing"]
    path, name = pr.artifact_path("ведомость-исполнения-пейринга")
    assert name == "execution-manifest.json" and path.is_file()
    man = json.loads(path.read_text(encoding="utf-8"))
    assert man["blocking"] == []
    assert {r["rule_id"] for r in man["rules"]} == {"R-H", "R-S"}


def test_шаг3_ростеринг_на_пейринге_только_по_утверждённому_прогону(tmp_path):
    """План 05.09.2026, шаг 3: второе нажатие называет прогон пейринга
    ЯВНО, и COSMOS сверяет его с выдачей нажатия pairing этого же пакета
    — без выдачи, при чужом прогоне и при неутверждённом пейринге отказ
    словами до занятия кнопки; молчаливой подстановки «последнего
    пейринга» нет. Состояние страницы несёт утверждённый прогон."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    assert pr.approved_pairing(wd) is None
    assert pr.status()["pairing_run"] is None
    common = dict(start_thread=False, solver=FakeSolver(),
                  certify_fn=None, pairing_certify_fn=None)
    with pytest.raises(pr.PackageRunError, match="нет выдачи нажатия pairing"):
        pr.press("rostering", pairing_run_id="r-2", **common)
    (wd / "button-pairing").mkdir(parents=True, exist_ok=True)
    (wd / "button-pairing" / "delivery.json").write_text(json.dumps({
        "verdict": "пейринг построен", "chains": 9, "catalog_sha256": "c" * 64,
        "schedule": {"solver_run_id": "r-1", "score": "0hard/0soft"}},
        ensure_ascii=False), encoding="utf-8")
    ap = pr.approved_pairing(wd)
    assert ap == {"solver_run_id": "r-1", "verdict": "пейринг построен",
                  "catalog_sha256": "c" * 64, "chains": 9, "approved": True}
    assert pr.status()["pairing_run"] == ap
    with pytest.raises(pr.PackageRunError, match="не соответствует прогону пейринга"):
        pr.press("rostering", pairing_run_id="r-2", **common)
    (wd / "button-pairing" / "delivery.json").write_text(json.dumps({
        "verdict": "остановка: построитель пейринга отказал",
        "schedule": {"solver_run_id": "r-1"}}, ensure_ascii=False), encoding="utf-8")
    assert pr.approved_pairing(wd)["approved"] is False
    with pytest.raises(pr.PackageRunError, match="не утверждён"):
        pr.press("rostering", pairing_run_id="r-1", **common)
    # кнопка не занята ни одним из отказов
    assert pr.status()["task"] is None


def test_карта_артефактов_несёт_квитанцию_заявок_каталога():
    """Шаг 6 каталога: квитанция подачи заявок на вид исполнителя —
    артефакт нажатия наравне с прочими, и её имя — ОДНО с именем,
    которое пишет продуктовый слой заявок (две правды разошлись бы
    молча: ссылка вела бы в пустоту)."""
    from webapp import package_runs as pr
    from vmp import catalog_requests as vcr
    sub, name = pr.ALLOWED_FILES["заявки-каталога-пейринга"]
    assert (sub, name) == ("button-pairing", vcr.RECEIPT_NAME)
    assert name == "catalog-requests.json"
    print("алиасов артефактов в перечне: %d" % len(pr.ALLOWED_FILES))


# ---- расход моделей, решатели с параметрами и происхождение попытки ----
# Проект «Запись о прогоне» (10.09.2026), решение владельца № 1: расход
# моделей считается В ТОКЕНАХ по каждой задействованной модели — цены на
# момент прогона и на момент просмотра различаются, поэтому денег в
# записи нет вовсе.

def _llm_with_usage(rows):
    """Подставной канал сертификатора, который, как настоящая труба,
    объявляет расход каждого вызова атрибутом `last_usage`."""
    state = {"n": 0}

    def _call(prompt: str) -> str:
        state["n"] += 1
        _call.last_usage = dict(rows[min(state["n"], len(rows)) - 1])
        return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)
    _call.last_usage = None
    _call.calls = state
    return _call


def _roster_llm_fakes():
    """Стенд ростеринга со СВОЕЙ сертификацией: без certify_fn кнопка
    зовёт настоящий путь сертификации подставным каналом модели."""
    fakes = _fakes(True)
    fakes.pop("certify_fn")
    return fakes


class _SolverWithProfile(FakeSolver):
    """Стенд решателя, принимающий предел «без улучшений» и профиль Д1
    (настоящая служба их принимает; базовый стенд — нет): нажатие с
    полным набором параметров должно доходить до счёта, чтобы запись
    попытки несла их фактом, а не объявлением."""

    def start(self, payload, zip_b64, mode, objective_id, limit_s,
              task="rostering", pairing_run_id=None, moves=None,
              unimproved_limit_s=None, deterministic=None):
        self.unimproved_limit_s = unimproved_limit_s
        self.deterministic = deterministic
        return super().start(payload, zip_b64, mode, objective_id, limit_s,
                             task=task, pairing_run_id=pairing_run_id,
                             moves=moves)


def test_расход_моделей_решатели_и_происхождение_в_записи_попытки(tmp_path):
    """Запись попытки несёт три новых блока (проект записи о прогоне):
    `resources` — токены по каждой модели с ролью и каналом плюс стенное
    время счёта; `solvers[]` — движок с версией, сборкой и параметрами
    ФАКТОМ нажатия; `lineage` — отпечаток пакета, прогон-источник CDPM и
    номер записи о прогоне (сегодня его CDPM не кладёт — пусто с кодом
    причины, а не молчание)."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    llm = _llm_with_usage([
        {"provider": "anthropic", "model": "модель-стенда", "channel": "api",
         "counted": True, "input_tokens": 1500, "output_tokens": 220}])
    st = pr.press("rostering", solver=_SolverWithProfile(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  spent_limit_s=7, unimproved_spent_limit_s=3,
                  moves="ходы-стенда",
                  deterministic={"seed": 11, "step_limit": 40,
                                 "unimproved_step_limit": 5,
                                 "deterministic_time": 2},
                  **_roster_llm_fakes())
    assert st["error"] is None, st
    rec = st["attempts"][0]

    # (1) Расход: строка на роль × модель, токены сложены, денег нет.
    res = rec["resources"]
    cert = [m for m in res["models"] if m["role_code"] == "certifier"]
    assert len(cert) == 1, res["models"]
    row = cert[0]
    assert row["role"] == "сертификатор"
    assert (row["provider"], row["model"], row["channel"]) == (
        "anthropic", "модель-стенда", "api")
    assert row["calls"] == llm.calls["n"] >= 1
    assert row["counted"] is True
    assert row["input_tokens"] == 1500 * row["calls"]
    assert row["output_tokens"] == 220 * row["calls"]
    assert not any(k in row for k in ("cost", "price", "usd", "рубли"))
    # Стенное время счёта — из журнала внешних вызовов, числом секунд.
    assert isinstance(res["solver_seconds"], float)
    assert res["solver_seconds"] >= 0

    # (2) Решатели с параметрами — ФАКТ попытки.
    solvers = rec["solvers"]
    assert len(solvers) == 1
    s = solvers[0]
    assert s["task"] == "rostering" and s["engine"] == "timefold"
    assert s["spent_limit_s"] == 7 and s["unimproved_spent_limit_s"] == 3
    assert s["moves"] == "ходы-стенда"
    assert (s["seed"], s["step_limit"]) == (11, 40)
    assert (s["unimproved_step_limit"], s["deterministic_time"]) == (5, 2)
    assert s["accepted_uncovered"] == []
    # Версия и отпечаток сборки решателя — из файлов сборки; их может не
    # быть на стенде, и тогда рядом стоит код причины, а не пустота.
    assert "build_sha256" in s and s["version_source_code"]

    # (3) Происхождение входов.
    lin = rec["lineage"]
    assert lin["package_sha256"] == sha
    assert lin["source_run"] == pr.status()["source_run"]
    assert lin["parent_attempt"] is None
    # Номер записи о прогоне — из манифеста пакета (CDPM кладёт его с
    # 10.09.2026 вместе с отпечатком объявления); пакет без номера
    # честно помечен кодом причины, а не пустотой.
    if lin["record_id"]:
        assert "record_id_code" not in lin
        assert isinstance(lin["record_id"], str) and lin["record_id"]
        assert "declaration_sha256" in lin
    else:
        assert lin["record_id_code"] == "package-without-record-id"

    # Ручка списка попыток отдаёт те же блоки (карточка их и читает).
    listed = pr.status()["attempts"][0]
    assert listed["resources"] == res and listed["solvers"] == solvers
    assert listed["lineage"] == lin
    # Рецепт попытки (карточка прогона COSMOS) — тоже.
    from webapp import cosmos_runs as cr
    recipe = cr.attempt_recipe(pr.workdir_for(sha), rec)
    assert recipe["lineage"] == lin and recipe["solvers"] == solvers
    assert recipe["resources"] == res
    assert recipe["solvers_code"] is None


def test_расход_канала_без_токенов_называется_словами(tmp_path):
    """Канал CLI подписки usage не отдаёт. Ноль читался бы как
    измеренная величина, поэтому в своде стоит `counted: false` с
    причиной словами и числом несчитанных вызовов."""
    from webapp import llm_backend, package_runs as pr
    _make_current(tmp_path)
    llm = _llm_with_usage([llm_backend.uncounted_usage(
        "anthropic", "модель-стенда", "cli", llm_backend.CLI_UNCOUNTED_NOTE)])
    st = pr.press("rostering", solver=FakeSolver(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  **_roster_llm_fakes())
    assert st["error"] is None, st
    row = st["attempts"][0]["resources"]["models"][0]
    assert row["counted"] is False and row["calls"] >= 1
    assert row["uncounted_calls"] == row["calls"]
    assert row["note"] == llm_backend.CLI_UNCOUNTED_NOTE
    assert not any(k in row for k in llm_backend.USAGE_KEYS)


def test_расход_пейринга_без_модели_называет_пробел_кодом(tmp_path):
    """Пейринг стенда идёт без сертификации: моделей не звали, и запись
    честно говорит это кодом, а не пустым перечнем без объяснения.
    Параметры движка пейринга — в `solvers[]` со своей версией."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    fakes = _pairing_kwargs()
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None,
                  spent_limit_s=5, **fakes)
    assert st["error"] is None, st
    rec = st["attempts"][0]
    assert rec["resources"]["models"] == []
    assert rec["resources"]["models_code"] == "no-model-calls"
    s = rec["solvers"][0]
    assert s["task"] == "pairing" and s["engine"] == "cpsat"
    assert s["spent_limit_s"] == 5
    assert s["version_source_code"] == "interpreter-package"
    assert s["build_source_code"] == "in-process-engine"
    # Версия движка — версия его пакета в этом же интерпретаторе.
    from importlib import metadata as _md
    assert s["version"] == _md.version("ortools")


def test_сверка_с_идак_дописывает_расход_читателя_в_попытку(tmp_path):
    """Сверка с ИДАК идёт ОТДЕЛЬНЫМ вызовом, вне попытки нажатия: её
    расход живёт в снимке сверки и оттуда дописывается в запись попытки
    — рядом с расходом сертификатора. Повторная привязка того же снимка
    расход не удваивает."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    llm = _llm_with_usage([
        {"provider": "anthropic", "model": "модель-стенда", "channel": "api",
         "counted": True, "input_tokens": 100, "output_tokens": 10}])
    st = pr.press("rostering", solver=FakeSolver(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  **_roster_llm_fakes())
    rec = st["attempts"][0]
    wd = pr.workdir_for(sha)
    delivery = json.loads((wd / rec["catalog"] / "delivery.json")
                          .read_text(encoding="utf-8"))
    run_id = delivery["schedule"]["solver_run_id"]
    snap = {"at": "2026-09-10T12:00:00", "reader": "cloud/читатель-стенда",
            "reader_channel": "api", "counts": {"ok": 3},
            "resources": {"models": [
                {"role": "читатель ИДАК", "role_code": "source_check_reader",
                 "provider": "anthropic", "model": "читатель-стенда",
                 "channel": "api", "calls": 1, "counted": True,
                 "input_tokens": 900, "output_tokens": 60}]}}
    got = pr.note_source_check(wd, "rostering", run_id, None, snap)
    assert got == rec["id"]
    after = pr.status()["attempts"][0]
    assert after["source_check"]["resources"] == snap["resources"]
    roles = {m["role_code"]: m for m in after["resources"]["models"]}
    assert set(roles) == {"certifier", "source_check_reader"}
    assert roles["source_check_reader"]["input_tokens"] == 900
    assert roles["source_check_reader"]["role"] == "читатель ИДАК"
    assert roles["certifier"]["input_tokens"] == 100 * llm.calls["n"]
    # Вторая привязка того же снимка — строка заменяется, не удваивается.
    pr.note_source_check(wd, "rostering", run_id, None, snap)
    again = pr.status()["attempts"][0]["resources"]["models"]
    assert len([m for m in again
                if m["role_code"] == "source_check_reader"]) == 1


def _reader_row(at: str, **over) -> dict:
    """Снимок сверки с одной строкой расхода читателя ИДАК."""
    row = {"role": "читатель ИДАК", "role_code": "source_check_reader",
           "provider": "anthropic", "model": "читатель-стенда",
           "channel": "api", "calls": 1, "counted": True,
           "input_tokens": 900, "output_tokens": 60}
    row.update(over)
    return {"at": at, "reader": "cloud/читатель-стенда",
            "reader_channel": "api", "counts": {"ok": 3},
            "resources": {"models": [row]}}


def test_сверки_одной_попытки_складываются_а_не_заменяют(tmp_path):
    """Сверок у одной попытки бывает несколько: повторная сверка тех же
    клауз идёт ИЗ КЭША компиляции — вызовов ноль, токенов ноль, и этот
    ноль сосчитан. Расход роли СКЛАДЫВАЕТСЯ: замена стирала живой расход
    первой сверки нулём второй, и попытка выглядела бесплатной.
    Несосчитанное слагаемое метит сумму несосчитанной — половина
    расхода, выданная за целое, хуже честного «не считается»."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    llm = _llm_with_usage([
        {"provider": "anthropic", "model": "модель-стенда", "channel": "api",
         "counted": True, "input_tokens": 100, "output_tokens": 10}])
    st = pr.press("rostering", solver=FakeSolver(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  **_roster_llm_fakes())
    rec = st["attempts"][0]
    wd = pr.workdir_for(sha)
    delivery = json.loads((wd / rec["catalog"] / "delivery.json")
                          .read_text(encoding="utf-8"))
    run_id = delivery["schedule"]["solver_run_id"]

    def _reader():
        rows = [m for m in pr.status()["attempts"][0]["resources"]["models"]
                if m["role_code"] == "source_check_reader"]
        assert len(rows) == 1, rows
        return rows[0]

    # живая сверка: канал назвал токены
    pr.note_source_check(wd, "rostering", run_id, None,
                         _reader_row("2026-09-11T12:00:00"))
    # вторая сверка той же задачи — проверки из кэша, модель не звалась
    pr.note_source_check(wd, "rostering", run_id, None, _reader_row(
        "2026-09-11T12:05:00", provider=None, calls=0, input_tokens=0,
        output_tokens=0, note="чтение из кэша: модель не вызывалась"))
    row = _reader()
    assert row["calls"] == 1, "ноль из кэша затёр вызов живой сверки"
    assert (row["input_tokens"], row["output_tokens"]) == (900, 60)
    assert row["counted"] is True and "чтение из кэша" in row["note"]
    assert row["provider"] == "anthropic" and row["model"] == "читатель-стенда"
    # расход сертификатора суммой читателя не тронут
    roles = {m["role_code"]: m for m in
             pr.status()["attempts"][0]["resources"]["models"]}
    assert roles["certifier"]["input_tokens"] == 100 * llm.calls["n"]

    # третья сверка каналом без учёта: сумма честно несосчитана, причины
    # обеих строк названы, а выдуманных нулей не появилось
    pr.note_source_check(wd, "rostering", run_id, None, _reader_row(
        "2026-09-11T12:09:00", counted=False, input_tokens=None,
        output_tokens=None, note="канал читателя расхода не назвал"))
    row = _reader()
    assert row["counted"] is False and row["calls"] == 2
    assert (row["input_tokens"], row["output_tokens"]) == (900, 60)
    assert "чтение из кэша" in row["note"] and "не назвал" in row["note"]

    # повтор ТОГО ЖЕ снимка сам с собой не складывается
    same = _reader_row("2026-09-11T12:09:00", counted=False,
                       input_tokens=None, output_tokens=None,
                       note="канал читателя расхода не назвал")
    pr.note_source_check(wd, "rostering", run_id, None, same)
    pr.note_source_check(wd, "rostering", run_id, None, same)
    row = _reader()
    assert row["calls"] == 1 and row["counted"] is False
    assert row["input_tokens"] is None and row["output_tokens"] is None


def test_отказ_сверки_расход_берёт_а_свод_не_подменяет(tmp_path):
    """Отказавшая сверка (`status: error`) расход читателя в попытку
    ОТДАЁТ — он потрачен, — а свод сверки попытки не подменяет:
    счётчиков у отказа нет, и запись без счётчиков читалась бы «сверка
    прошла, нарушений нет» (оценка ИДАК судит именно их). Зелёная оценка
    от несостоявшейся сверки хуже её отсутствия; прошлая сверка той же
    выдачи от отказа повторной ложью не становится."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    llm = _llm_with_usage([
        {"provider": "anthropic", "model": "модель-стенда", "channel": "api",
         "counted": True, "input_tokens": 100, "output_tokens": 10}])
    st = pr.press("rostering", solver=FakeSolver(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  **_roster_llm_fakes())
    rec = st["attempts"][0]
    wd = pr.workdir_for(sha)
    delivery = json.loads((wd / rec["catalog"] / "delivery.json")
                          .read_text(encoding="utf-8"))
    run_id = delivery["schedule"]["solver_run_id"]
    good = _reader_row("2026-09-11T12:00:00")
    assert pr.note_source_check(wd, "rostering", run_id, None, good) == rec["id"]

    bad = _reader_row("2026-09-11T12:20:00")
    bad.update({"status": "error", "error": "читатель ответил не по договору"})
    bad.pop("counts")
    assert pr.note_source_check(wd, "rostering", run_id, None, bad) == rec["id"]
    after = pr.status()["attempts"][0]
    # свод удачной сверки остался на месте — отказ его не затёр
    assert after["source_check"]["counts"] == {"ok": 3}
    assert after["source_check"]["at"] == "2026-09-11T12:00:00"
    # а вызов отказавшей сверки сочтён: расход роли — сумма двух сверок
    row, = [m for m in after["resources"]["models"]
            if m["role_code"] == "source_check_reader"]
    assert row["calls"] == 2 and row["counted"] is True
    assert (row["input_tokens"], row["output_tokens"]) == (1800, 120)


def test_расход_в_записи_попытки_без_секретов(tmp_path, monkeypatch):
    """Учёт токенов не должен протащить в файлы попытки ни ключа, ни
    токена площадки: расход — числа и имена моделей, ничего больше."""
    from webapp import package_runs as pr
    secret = "ключ-стенда-НЕ-ДЛЯ-ЗАПИСИ-2026"
    monkeypatch.setenv("CREW_API_KEY", secret)
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", secret + "-канал")
    sha = _make_current(tmp_path)
    llm = _llm_with_usage([
        {"provider": "anthropic", "model": "модель-стенда", "channel": "api",
         "counted": True, "input_tokens": 12, "output_tokens": 3}])
    st = pr.press("rostering", solver=FakeSolver(), llm=llm,
                  start_thread=False, sleep=lambda *_: None,
                  **_roster_llm_fakes())
    rec = st["attempts"][0]
    assert rec["resources"]["models"][0]["counted"] is True
    root = pr.workdir_for(sha)
    hits = [p for p in root.rglob("*")
            if p.is_file() and secret.encode("utf-8") in p.read_bytes()]
    assert hits == [], hits
