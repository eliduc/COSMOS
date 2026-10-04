# -*- coding: utf-8 -*-
"""Канал приёма пакета постановки (CDPM -> COSMOS) и карточка
«текущий ВМП»: POST /api/task-package/receive,
GET /api/task-package/current, резолвер task_channel.current_package().

Охраняемая граница (согласована планом пакета): токен канала — только
из env CREW_CHANNEL_TOKEN, без переменной канал выключен с отказом
словами (fail-closed); sha считается по байтам zip; идемпотентный
повтор возвращает тот же краткий вердикт с already_accepted=true и не
перегоняет гейт; расчёт приёмом НЕ стартует; env CREW_TASK_PACKAGE
сохраняет приоритет в резолвере (обратная совместимость); журнал
receipts.jsonl — только добавлением; прочие /api/* без личности
по-прежнему 401 (машинный путь — единственное исключение middleware).

Пакет в тестах синтетический — сборка cdpm.export_task_package на
фикстурах test_cdpm_task_package; данных заказчика нет и быть не
может. Каталог данных каждого теста — в tmp (контракт «результаты
вне git»).
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from cdpm import export_task_package as ctp  # noqa: E402
from tests.test_cdpm_task_package import (  # noqa: E402
    _FULL_SHEETS, _data_dirs, _ready_doc, _run_dir)
from tests.test_readiness_verify import _registry  # noqa: E402
from tests.test_task_package import _repack  # noqa: E402

ADMIN = ADMIN_EMAIL
TOKEN = "test-channel-token-2026"
RECEIVE = "/api/task-package/receive"
CURRENT = "/api/task-package/current"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient в dev-режиме (образец test_static_version): каталог
    пользователей — в tmp, перенос конфигурации на старте выключен."""
    from webapp import users
    cfg = tmp_path_factory.mktemp("channel-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    # Фикстура модульная, а autouse-изоляция conftest — функционная:
    # старт приложения идёт ДО неё, фоновый перенос глушится здесь же.
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


@pytest.fixture(autouse=True)
def _channel_env(tmp_path, monkeypatch):
    """Каждому тесту — свой каталог данных и чистые переменные канала:
    никакого наследства окружения машины (залежавшийся
    CREW_TASK_PACKAGE затенял бы канал); кэши конвейера и карточки
    сброшены — их ключи включают путь пакета."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    # изоляция conftest отключает инбокс пустым значением — тестам
    # канала он нужен живым, в своём каталоге данных
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    monkeypatch.delenv("CREW_VMP_REGISTRY", raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(flow._READY_CACHE, "value", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "value", None)


def _pkg_bytes(base: Path, doc: dict | None = None) -> bytes:
    """Синтетический пакет 1.14 с матрицей готовности и данными."""
    run = _run_dir(base, doc if doc is not None else _ready_doc())
    _data_dirs(run, _FULL_SHEETS)
    return ctp.export_zip(run)


def _machined_doc() -> dict:
    """Модель с машинными признаками контракта P-N-SHD: жёсткая несёт
    имя параметра закрытого словаря, мягкая распознаётся классовым
    классификатором семей при сборке пакета (assign_soft_families —
    рукотворное поле export_solver он пересчитывает). Приём выводит
    реестр исполнителей из этих признаков; без них выведенный реестр
    честно держит запуск пробелами (не ошибка, состояние)."""
    doc = _ready_doc()
    doc["rules"][0]["params"] = [
        {"name": "max_duty_duration", "value": 690,
         "unit": "duration_minutes", "relation": "max",
         "as_written": "11:30"}]
    doc["rules"][1]["statement"] = \
        "штраф за каждую ночёвку вне базы: вес 10"
    return doc


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _post(client, body: bytes, token: str | None = TOKEN,
          sha: str | None = None):
    headers = {"X-Package-Sha256": _sha(body) if sha is None else sha}
    if token is not None:
        headers["X-Channel-Token"] = token
    return client.post(RECEIVE, content=body, headers=headers)


def _inbox(tmp_path: Path) -> Path:
    return tmp_path / "data-root" / "task-inbox"


def _receipts(tmp_path: Path) -> list[dict]:
    p = _inbox(tmp_path) / "receipts.jsonl"
    if not p.is_file():
        return []
    return [json.loads(x) for x in
            p.read_text(encoding="utf-8").splitlines() if x]


# --------------------------------------------------- аутентификация канала

def test_канал_без_переменной_выключен_отказом_словами(client, tmp_path):
    # Верный заголовок не помогает: канала без env не существует.
    resp = _post(client, b"x", token=TOKEN, sha="0" * 64)
    assert resp.status_code == 403
    assert "канал выключен" in resp.json()["detail"]
    assert "CREW_CHANNEL_TOKEN" in resp.json()["detail"]
    assert not _inbox(tmp_path).exists(), "отказ обязан ничего не писать"


def test_кривой_или_отсутствующий_токен_отказ_без_деталей(
        client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    for presented in ("wrong-token", None):
        resp = _post(client, b"x", token=presented, sha="0" * 64)
        assert resp.status_code == 403
        # Причина не детализируется: неверный и отсутствующий токен
        # отвечают одинаково, слова «канал выключен» не утекают.
        assert "канал выключен" not in resp.json()["detail"]
    assert not _inbox(tmp_path).exists()


def test_чужие_api_без_личности_по_прежнему_401(client, tmp_path,
                                                monkeypatch):
    """Риск 1 плана: исключение middleware — ТОЛЬКО машинный путь.
    Личность убирается (пустой CREW_DEV_USER), обычные /api/* отвечают
    401, а канал по токену продолжает работать."""
    monkeypatch.setenv("CREW_DEV_USER", "")
    assert client.get("/api/flow").status_code == 401
    assert client.get(CURRENT).status_code == 401
    # Машинный путь жив без личности: с токеном дело доходит до
    # проверки sha (422), а не до отказа в аутентификации.
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    resp = _post(client, b"x", sha="0" * 64)
    assert resp.status_code == 422


# ------------------------------------------------------------------ приём

def test_несовпавший_sha_отказ_и_ничего_не_записано(client, tmp_path,
                                                    monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    body = _pkg_bytes(tmp_path / "pkg")
    resp = _post(client, body, sha="0" * 64)
    assert resp.status_code == 422
    b = resp.json()
    assert b["verdict"] == "REFUSED"
    assert "sha не совпал" in b["detail"]
    assert b["sha"] == _sha(body), "в ответе — фактическая сумма байтов"
    assert b["already_accepted"] is False
    # Байты не подтверждены отправителем — не записано НИЧЕГО,
    # включая журнал.
    assert not _inbox(tmp_path).exists()


def test_успешный_приём_вердикт_каталог_журнал_указатель(
        client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    # Реестра исполнителей ДО приёма нет: приём обязан вывести его
    # сам из базы шаблонов (P-N-SHD) — рука реестр не издаёт.
    derived_reg = tmp_path / "derived-registry.json"
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(derived_reg))
    # Сторож: приём не имеет права стартовать расчёт.
    from webapp import jobs
    started: list = []
    monkeypatch.setattr(jobs, "start_job",
                        lambda *a, **k: started.append(a))
    body = _pkg_bytes(tmp_path / "pkg", _machined_doc())
    sha = _sha(body)
    resp = _post(client, body)
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["verdict"] == "OK"
    assert b["already_accepted"] is False
    assert b["sha"] == sha
    assert b["spec_version"] == ctp.SPEC_VERSION
    assert b["source_run"] == "run-9001"
    assert b["mismatch"] == []
    # МГ — верифицированная матрица: числа фикстуры _ready_doc.
    assert b["mg"]["pairing"] == {"state": "optimization",
                                  "launch_allowed": True}
    assert b["mg"]["rostering"] == {"state": "feasible_soft",
                                    "launch_allowed": True}
    # Прогон COSMOS, принявший ЭТОТ приём (07.09.2026): текущего не было —
    # создан сам; вердикт — свойство пакета, прогона в verdict.json нет.
    assert b["cosmos_run"] == "run-0001"
    # Каталог приёма: пакет байт-в-байт + вердикт-маркер завершения.
    pkg_dir = _inbox(tmp_path) / sha
    assert (pkg_dir / "package.zip").read_bytes() == body
    assert (pkg_dir / "verdict.json").is_file()
    assert "cosmos_run" not in json.loads(
        (pkg_dir / "verdict.json").read_text(encoding="utf-8"))
    # Реестр исполнителей выведен приёмом из базы шаблонов: файл
    # появился, помечен derived, вердикты — от машинных признаков.
    reg_doc = json.loads(derived_reg.read_text(encoding="utf-8"))
    assert reg_doc["derived"] is True
    reg_verdicts = {r["rule_id"]: r["verdict"]
                    for r in reg_doc["rules"]}
    assert reg_verdicts == {"R-H": "covered_full",
                            "R-S": "covered_full"}
    # Указатель канала обновлён.
    cur = json.loads((_inbox(tmp_path) / "current.json")
                     .read_text(encoding="utf-8"))
    assert cur["sha"] == sha
    assert cur["source"] == "channel"
    assert cur["path"] == str(pkg_dir / "package.zip")
    assert cur["spec_version"] == ctp.SPEC_VERSION
    # Журнал приёмов — одна строка, не повтор.
    rows = _receipts(tmp_path)
    assert len(rows) == 1
    assert rows[0]["sha"] == sha and rows[0]["verdict"] == "OK"
    assert rows[0]["repeat"] is False
    assert rows[0]["cosmos_run"] == "run-0001"
    assert rows[0]["mg"] == {"pairing": "optimization",
                             "rostering": "feasible_soft"}
    # Расчёт НЕ стартовал.
    assert started == []
    # Конвейер видит канальный пакет через единый резолвер (без env).
    from webapp import flow
    st = flow.readiness_state()
    assert st["present"] and st["verdict"] == "OK"
    assert st["columns"]["pairing"]["launch_allowed"]
    # Примечания клетки CDPM и признак критерия едут на страницу
    # (ИДАК H0-К: «критерия оптимизации нет — только feasibility-soft»).
    for task in ("pairing", "rostering"):
        col = st["columns"][task]
        assert isinstance(col["notes"], list) and "criterion_defined" in col
    assert st["columns"]["rostering"]["criterion_defined"] in (False, None)
    # Без явного файла реестра страница судит по реестру, выведенному
    # под модель пакета — тому же, что у кнопки при нажатии (находка
    # H0-К 04.09: «реестра нет» на странице при проходящем нажатии).
    monkeypatch.delenv("CREW_VMP_REGISTRY")
    flow._READY_CACHE["key"] = None
    st2 = flow.readiness_state()
    assert st2["present"], st2
    from webapp import package_runs as pr
    cur = pr.current()
    sha = cur.get("sha") or cur.get("sha256") or pr._sha256(Path(cur["path"]))
    derived = (pr.workdir_for(sha) / "registry"
               / "vmp-executors-registry.page.json")
    assert derived.is_file()
    for task in ("pairing", "rostering"):
        col = st2["columns"][task]
        assert col["registry_rules"] is not None
        assert not any("реестра исполнителей нет" in w
                       for w in col["why_blocked"]), col["why_blocked"]
    # Карточка конвейера «Проверка соответствия» судит по тому же
    # выведенному реестру, что страница и кнопка (находка H0-К на Prod:
    # «ожидает данных» после снятия статического файла площадки).
    rs = flow._registry_state()
    assert rs["present"] and rs["derived"] and rs["rules"] > 0, rs


def test_повтор_того_же_sha_идемпотентен_и_без_гейта(client, tmp_path,
                                                     monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    # Счётчик гейта: повтор не имеет права перегонять проверку.
    from cdpm.gates import gate_task_package as gate
    real_check = gate.check
    calls = {"n": 0}

    def counting(path):
        calls["n"] += 1
        return real_check(path)

    monkeypatch.setattr(gate, "check", counting)
    body = _pkg_bytes(tmp_path / "pkg")
    sha = _sha(body)
    first = _post(client, body)
    assert first.status_code == 200 and calls["n"] == 1
    pkg_file = _inbox(tmp_path) / sha / "package.zip"
    mtime_before = pkg_file.stat().st_mtime_ns
    second = _post(client, body)
    assert second.status_code == 200
    assert calls["n"] == 1, "гейт на повторе перегонять нельзя"
    b2 = second.json()
    assert b2["already_accepted"] is True
    # Тот же краткий вердикт — вплоть до поля already_accepted.
    expect = dict(first.json())
    expect["already_accepted"] = True
    assert b2 == expect
    # Оба приёма принял один прогон (текущий не менялся).
    assert b2["cosmos_run"] == first.json()["cosmos_run"] == "run-0001"
    # Второй записи пакета нет.
    assert pkg_file.stat().st_mtime_ns == mtime_before
    assert sorted(p.name for p in pkg_file.parent.iterdir()) == \
        ["package.zip", "verdict.json"]
    # Журнал получил строку повтора — добавлением, не перезаписью.
    rows = _receipts(tmp_path)
    assert [r["repeat"] for r in rows] == [False, True]
    assert rows[1]["sha"] == sha and rows[1]["verdict"] == "OK"
    assert rows[1]["cosmos_run"] == "run-0001" and rows[1]["current_switched"]


def test_смена_текущего_прогона_между_приёмами_даёт_иной_cosmos_run(
        client, tmp_path, monkeypatch):
    """Прогон — свойство ПРИЁМА: после смены текущего прогона повтор того
    же sha ложится в новый прогон, и квитанция называет его; прежние
    ключи квитанции не меняются."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    body = _pkg_bytes(tmp_path / "pkg")
    first = _post(client, body)
    assert first.status_code == 200 and first.json()["cosmos_run"] == "run-0001"
    r = client.post("/api/cosmos-runs", json={"title": "второй"})
    assert r.status_code == 200 and r.json()["run"] == "run-0002"
    second = _post(client, body)
    assert second.status_code == 200
    b2 = second.json()
    assert b2["already_accepted"] is True and b2["cosmos_run"] == "run-0002"
    expect = dict(first.json(), already_accepted=True, cosmos_run="run-0002")
    assert b2 == expect
    from webapp import cosmos_runs
    assert cosmos_runs.read_run("run-0002")["packages"][0]["workdir"] == \
        "package-runs/%s/runs/run-0002" % _sha(body)
    cur = json.loads((_inbox(tmp_path) / "current.json").read_text(encoding="utf-8"))
    assert cur["sha"] == _sha(body) and cur["repeat"] is True
    rows = _receipts(tmp_path)
    assert [r["cosmos_run"] for r in rows] == ["run-0001", "run-0002"]


def test_сбой_присоединения_квитанция_null_зеркало_на_месте(
        client, tmp_path, monkeypatch):
    """Сбой записи прогона приём не рушит: cosmos_run null, код причины и
    слова в detail, строка журнала с run_note; зеркало канала записано —
    площадка видит пакет; повторная отправка лечит."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    from webapp import cosmos_runs
    real = cosmos_runs.attach_package
    calls = {"n": 0}

    def failing(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"run": None, "error_code": "run-not-written",
                    "error": "диск занят"}
        return real(*a, **k)
    monkeypatch.setattr(cosmos_runs, "attach_package", failing)
    body = _pkg_bytes(tmp_path / "pkg")
    resp = _post(client, body)
    assert resp.status_code == 200
    b = resp.json()
    assert b["verdict"] == "OK" and b["cosmos_run"] is None
    assert b["run_note_code"] == "run-not-written"
    assert "прогон не записан: диск занят" in b["detail"]
    assert b["current_switched"] is False
    cur = json.loads((_inbox(tmp_path) / "current.json").read_text(encoding="utf-8"))
    assert cur["sha"] == _sha(body)
    rows = _receipts(tmp_path)
    assert rows[-1]["cosmos_run"] is None and rows[-1]["run_note"] == "диск занят"
    again = _post(client, body)
    assert again.json()["already_accepted"] and again.json()["cosmos_run"] == "run-0001"


def test_повтор_без_каталога_шаблонов_называет_несостоявшийся_перевывод(
        client, tmp_path, monkeypatch):
    """Шаг 2 каталога шаблонов (часть 13.6): вывод реестра исполнителей
    читает каталог; на повторе сбой перевывода не глотается — краткий
    вердикт повтора и строка журнала называют причину словами (как в
    первичном приёме), приём повтора остаётся принятым, реестр — прежним.
    Подмена — каталогом (пустая папка в CREW_TEMPLATE_CATALOG)."""
    import catalog_copies as cc
    from vmp import catalog_access as ca
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    derived_reg = tmp_path / "derived-registry.json"
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(derived_reg))
    body = _pkg_bytes(tmp_path / "pkg", _machined_doc())
    sha = _sha(body)
    first = _post(client, body)
    assert first.status_code == 200 and first.json()["verdict"] == "OK"
    reg_before = derived_reg.read_bytes()
    empty = tmp_path / "empty-catalog"
    empty.mkdir()
    cc.use_catalog(monkeypatch, empty)
    try:
        second = _post(client, body)
    finally:
        ca.forget()
    assert second.status_code == 200, second.text
    b2 = second.json()
    assert b2["already_accepted"] is True and b2["verdict"] == "OK"
    assert "вывод реестра исполнителей не удался" in b2["detail"]
    assert "каталог" in b2["detail"] and str(empty) in b2["detail"]
    # первичный текст вердикта сохранён, заметка дописана
    assert b2["detail"].startswith(first.json()["detail"])
    # реестр остался прежним — нажатие скажет «реестр устарел», причина
    # уже названа здесь
    assert derived_reg.read_bytes() == reg_before
    rows = _receipts(tmp_path)
    assert [r["repeat"] for r in rows] == [False, True]
    assert "registry_note" not in rows[0]
    assert rows[1]["sha"] == sha
    assert rows[1]["registry_note"].startswith(
        "вывод реестра исполнителей не удался")
    assert str(empty) in rows[1]["registry_note"]


def test_repeat_of_an_accepted_package_makes_it_current_again(client, tmp_path,
                                                              monkeypatch):
    """Проход H0-КИ (05.09.2026): после приёма второго пакета повтор первого
    возвращал already_accepted, но текущим оставался второй — нажать
    первый было нельзя. Идемпотентность — про приём (гейт не гоняется,
    verdict.json не трогается), указатель «текущий» — про намерение
    отправителя: повтор переставляет current.json на повторённый sha."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    body_a = _pkg_bytes(tmp_path / "a")
    body_b = _pkg_bytes(tmp_path / "b", _machined_doc())
    sha_a, sha_b = _sha(body_a), _sha(body_b)
    assert sha_a != sha_b
    assert _post(client, body_a).status_code == 200
    assert _post(client, body_b).status_code == 200
    cur = json.loads((_inbox(tmp_path) / "current.json").read_text(encoding="utf-8"))
    assert cur["sha"] == sha_b
    verdict_a = (_inbox(tmp_path) / sha_a / "verdict.json")
    mtime_before = verdict_a.stat().st_mtime_ns
    again = _post(client, body_a)
    assert again.status_code == 200 and again.json()["already_accepted"] is True
    cur = json.loads((_inbox(tmp_path) / "current.json").read_text(encoding="utf-8"))
    assert cur["sha"] == sha_a and cur["repeat"] is True
    assert cur["path"].endswith("package.zip") and sha_a in cur["path"]
    assert verdict_a.stat().st_mtime_ns == mtime_before
    from webapp import task_channel
    # резолвер текущего пакета видит повторённый sha (форма ответа —
    # вложенная, поэтому сверка по содержимому)
    assert sha_a in json.dumps(task_channel.current_package() or {})
    assert sha_b not in json.dumps(task_channel.current_package() or {})


def test_кривой_zip_отказ_словами_ничего_не_сохранено(client, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    body = "это не zip, а мусорные байты".encode("utf-8")
    resp = _post(client, body)          # sha честный — дело в самом zip
    assert resp.status_code == 422
    b = resp.json()
    assert b["verdict"] == "REFUSED"
    assert "не читается" in b["detail"]
    # Пакет не сохранён: ни каталога <sha>/, ни указателя канала.
    assert not (_inbox(tmp_path) / _sha(body)).exists()
    assert not (_inbox(tmp_path) / "current.json").exists()
    # Отказ в журнале назван (append-строка) и повтором не помечен.
    rows = _receipts(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "REFUSED"
    assert rows[0]["repeat"] is False


def test_подделка_клетки_матрицы_останавливает_приём(client, tmp_path,
                                                     monkeypatch):
    """Паттерн test_readiness_verify: клетка манифеста подкручена
    (rostering -> optimization), содержимое пакета прежнее. Гейт
    производителя пересчитывает матрицу и краснеет — приём отказывает,
    запуск запрещён, пакет не сохраняется."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    body = _pkg_bytes(tmp_path / "pkg")
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        z.extractall(unpacked)
    mf = unpacked / "manifest.json"
    man = json.loads(mf.read_text(encoding="utf-8"))
    man["readiness_matrix"]["columns"]["rostering"]["state"] = \
        "optimization"
    mf.write_text(json.dumps(man, ensure_ascii=False),
                  encoding="utf-8")
    bad = _repack(unpacked, tmp_path / "tampered.zip").read_bytes()
    resp = _post(client, bad)
    assert resp.status_code == 422
    b = resp.json()
    assert b["verdict"] == "GATE_RED"
    assert b["mismatch"], "расхождение обязано быть названо"
    assert b["mg"]["rostering"]["launch_allowed"] is False
    assert not (_inbox(tmp_path) / _sha(bad)).exists()
    assert not (_inbox(tmp_path) / "current.json").exists()


def test_лимит_тела_канала_413(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    from webapp import app as app_module
    monkeypatch.setattr(app_module, "CHANNEL_BODY_MAX_BYTES", 64)
    resp = _post(client, b"x" * 200, sha="0" * 64)
    assert resp.status_code == 413
    assert not _inbox(tmp_path).exists()


# ------------------------------------------- один слот текущего пакета

def _hold_press(wd, attempt_id="A1"):
    """Занять линию нажатия по рабочему каталогу — ровно тем же
    занятием, каким её занимает кнопка (`_claim_line`): запись «занята»
    на диске плюс отметка живой нити в реестре процесса."""
    from webapp import package_runs as pr
    pr._claim_line(wd, "press", "кнопка уже идёт",
                   task="pairing", state="кнопка: идёт", done=False,
                   progress=None, error=None, verdict=None,
                   attempt_id=attempt_id, actor="user:стенд")


def _release_press(wd):
    """Закончить попытку: итог записан, реестр живых нитей пуст."""
    from webapp import package_runs as pr
    pr._RUNNING.pop(pr._line_key(wd, "press"), None)
    pr._mutate_state(wd, lambda st: st.update(state="кнопка: допустимо",
                                              done=True, verdict="допустимо"))


def test_приём_нового_пакета_при_живой_попытке_отказывает_словами(
        client, tmp_path, monkeypatch):
    """Слот текущего пакета ОДИН (живой случай 11.09.2026): пока по нему
    идёт незаконченная попытка нажатия, новый пакет не подменяет его
    молча — отказ 409 словами с номером попытки и отпечатком пакета.
    Тот же пакет подменой не считается: повтор отправки проходит."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    from webapp import package_runs as pr
    first = _pkg_bytes(tmp_path / "pkg1", _machined_doc())
    assert _post(client, first).status_code == 200
    res = pr.resolve_current()
    assert res is not None and res.sha == _sha(first)
    _hold_press(res.wd)
    try:
        second = _pkg_bytes(tmp_path / "pkg2")
        resp = _post(client, second)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["verdict"] == "REFUSED"
        assert body["code"] == pr.CODE_SLOT_BUSY
        assert "слот занят" in body["detail"]
        assert "A1" in body["detail"], "номер попытки назван"
        assert _sha(first)[:12] in body["detail"], "пакет попытки назван"
        # Отказ ничего не записал: слот и указатель прежние.
        assert not (_inbox(tmp_path) / _sha(second)).exists()
        assert pr.resolve_current().sha == _sha(first)
        # Тот же пакет — не подмена: повтор принят и при живой попытке.
        again = _post(client, first)
        assert again.status_code == 200, again.text
        assert again.json()["already_accepted"] is True
    finally:
        _release_press(res.wd)


def test_законченная_попытка_освобождает_слот(client, tmp_path, monkeypatch):
    """Итог попытки слот освобождает — новый пакет принимается; тот же
    закон у загрузки человеком: вход в слот один на оба пути."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    from webapp import package_runs as pr
    first = _pkg_bytes(tmp_path / "pkg1", _machined_doc())
    assert _post(client, first).status_code == 200
    res = pr.resolve_current()
    _hold_press(res.wd, attempt_id="A2")
    second = _pkg_bytes(tmp_path / "pkg2")
    assert _post(client, second).status_code == 409
    _release_press(res.wd)
    assert _post(client, second).status_code == 200
    assert pr.resolve_current().sha == _sha(second)


def test_осиротевшая_попытка_слот_не_держит(client, tmp_path, monkeypatch):
    """Служба умерла посреди счёта — запись «идёт» осталась без живой
    нити. Сторож сирот метит её «прервано перезапуском», и слот
    свободен: иначе площадка запиралась бы навсегда."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    from webapp import package_runs as pr
    first = _pkg_bytes(tmp_path / "pkg1", _machined_doc())
    assert _post(client, first).status_code == 200
    res = pr.resolve_current()
    _hold_press(res.wd, attempt_id="A3")
    # Нить погибла, не записав итога: запись «занята» осталась.
    pr._RUNNING.pop(pr._line_key(res.wd, "press"), None)
    second = _pkg_bytes(tmp_path / "pkg2")
    assert _post(client, second).status_code == 200, "сирота слот не держит"
    assert pr.resolve_current().sha == _sha(second)


# --------------------------------------------------------------- резолвер

def test_резолвер_env_приоритетнее_канала(client, tmp_path, monkeypatch):
    """Обратная совместимость: CREW_TASK_PACKAGE («файл руками»)
    затеняет пакет, принятый каналом; карточка честно показывает
    источник «файл». Без переменной — источник «канал»."""
    from webapp import task_channel
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    body = _pkg_bytes(tmp_path / "a")
    assert _post(client, body).status_code == 200
    env_pkg = tmp_path / "env-pkg.zip"
    # Те же байты, что принял канал (повторная сборка стенда не
    # байт-идентична: метки времени книг) — файл «руками» с тем же sha.
    env_pkg.write_bytes(body)
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(env_pkg))
    cur = task_channel.current_package()
    assert cur["source"]["kind"] == "file"
    assert cur["path"] == str(env_pkg)
    # те же байты, что принял канал: маркер держателя базового каталога
    # называет прогон — у пакета «файлом» НЕЯВНЫЙ прогон (одно правило
    # принадлежности), а не вторая правда
    assert cur["sha"] == _sha(env_pkg.read_bytes()) == _sha(body)
    assert cur["run"] == {"run": "run-0001", "title": None,
                          "workdir": "package-runs/" + _sha(body),
                          "implicit": True}
    card = client.get(CURRENT).json()
    assert card["present"] and card["source"]["kind"] == "file"
    assert card["source"]["name"] == "env-pkg.zip"
    assert card["run"] == {"run": "run-0001", "title": None, "implicit": True}
    # Переменная снята — единый резолвер отдаёт пакет текущего прогона.
    monkeypatch.delenv("CREW_TASK_PACKAGE")
    cur2 = task_channel.current_package()
    assert cur2["source"]["kind"] == "channel"
    assert cur2["path"] == \
        str(_inbox(tmp_path) / _sha(body) / "package.zip")
    assert cur2["source"]["at"], "время приёма обязано быть названо"
    assert cur2["sha"] == _sha(body)
    assert cur2["run"] == {"run": "run-0001", "title": None,
                           "workdir": "package-runs/" + _sha(body),
                           "implicit": False}
    card2 = client.get(CURRENT).json()
    assert card2["source"]["kind"] == "channel"
    assert card2["run"] == {"run": "run-0001", "title": None, "implicit": False}


# --------------------------------------------------------------- карточка

def test_карточка_без_пакета_честно_пуста(client):
    resp = client.get(CURRENT)
    assert resp.status_code == 200
    assert resp.json() == {"present": False}


def test_карточка_с_канальным_пакетом(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(_registry(tmp_path)))
    body = _pkg_bytes(tmp_path / "pkg")
    assert _post(client, body).status_code == 200
    resp = client.get(CURRENT)
    assert resp.status_code == 200
    card = resp.json()
    assert card["present"] is True
    assert card["sha256"] == _sha(body)
    assert card["source"]["kind"] == "channel"
    assert card["source"]["at"]
    man = card["manifest"]
    assert man["spec_version"] == ctp.SPEC_VERSION
    assert man["source_run"] == "run-9001"
    assert man["has_data"] is True
    names = {f["name"] for f in man["files"]}
    assert {"model.json", "objective.json", "clean-data.json"} <= names
    # Правила по зонам: сумма равна числу правил модели (_ready_doc:
    # R-H в BOTH, R-S в PAIRING).
    zones = card["rules_by_zone"]
    assert zones == {"PAIRING": 1, "ROSTERING": 0, "BOTH": 1, "OUT": 0}
    assert sum(zones.values()) == 2
    # МГ — та же верифицированная матрица, что на чипах конвейера.
    assert card["mg"]["present"] and card["mg"]["verdict"] == "OK"
    assert card["mg"]["columns"]["pairing"]["state"] == "optimization"
    # Машинного ответа-критерия и карт правил в постановке нет —
    # честные null, не догадки. Покрытие клауз и перечень исключённых
    # правил — тоже null: лабораторный пакет собран без комплекта
    # заморозки и без пометок.
    assert card["criterion"] is None
    assert card["rule_maps"] is None
    assert man["coverage"] is None
    assert man["excluded_until_data"] == []
    # Хвост журнала — карточке («источник получения + время»).
    assert card["receipts"] and card["receipts"][-1]["verdict"] == "OK"


def test_карточка_критерий_и_карты_правил(client, tmp_path, monkeypatch):
    """Риск 10 плана: критерий идёт через приватный
    _criterion_from_answer — форма закрепляется тестом. Карты правил
    (манифест 1.14) поднимаются из носителя CDPM_RULE_MAPS."""
    doc = _ready_doc()
    doc["questions"].append({
        "id": "Q-3a-критерий-пейринга", "part": "3a",
        "title": "Критерий", "text": "т",
        "answer_format": {"type": "value"},
        "rule_refs": ["R-S"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "answer": {"raw": (
            'Целевая — нормированная стоимость по рангам.\n'
            '{"criterion": "NORMALIZED_COST", "rule": "R-CRIT-01",'
            ' "rates_param": "PARAM-T-01",'
            ' "rates": {"КВС": {"flight": 1.0, "work": 0.7},'
            '           "ВП": {"flight": 1.0, "work": 0.7}},'
            ' "label": "NORMALIZED_COST",'
            ' "version": "PARAM-T-01/base-1",'
            ' "effective_from": "2026-08-24", "effective_to": null}'),
            "status": "применён", "revision": 2}})
    maps = tmp_path / "rule-maps.json"
    maps.write_text(json.dumps({
        # семейство носителей 2026-08-29: customers — СПИСОК
        # точных значений (подстрока разъезжала миры)
        "customers": ["Т"],
        "rule_to_constraint": {"R-H": "судья-жёсткого",
                               "R-S": "штраф-ночёвок"}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_RULE_MAPS", str(maps))
    pkg = tmp_path / "env-pkg.zip"
    pkg.write_bytes(_pkg_bytes(tmp_path / "pkg", doc))
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(pkg))
    card = client.get(CURRENT).json()
    assert card["present"] and card["source"]["kind"] == "file"
    crit = card["criterion"]
    assert crit["label"] == "NORMALIZED_COST"
    assert crit["source_rule"] == "R-CRIT-01"
    assert crit["rates"] == {"flight": 1.0, "work": 0.7}
    assert crit["rates_version"] == {"source": "PARAM-T-01",
                                     "version": "PARAM-T-01/base-1",
                                     "effective_from": "2026-08-24"}
    # Карты правил — имена подсекций со счётом записей.
    assert card["rule_maps"] == {"rule_to_constraint": 2}
