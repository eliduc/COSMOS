# -*- coding: utf-8 -*-
"""Общий экран «Все прогоны» обеих систем (webapp/runs_all_api.py и
ручка `GET /api/runs-all`).

Основание — слово владельца 11.09.2026 («Нужно») и проект
`final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы 4 и 12. Судится
ровно то, ради чего экран заведён:

  * строки собираются из ДВУХ источников — записи, которые знает COSMOS
    (индекс дерева), и прогоны хранителя-панели, которых в индексе ещё
    нет; прогон, известный обеим, в списке ОДИН;
  * у строки названы номера прогона у каждой системы, заказчик,
    площадка, кто объявил, дата объявления, состояние, системы-
    летописцы и признак золотого; чего панель не сказала — пусто, а не
    выдумано;
  * панель недоступна (и панель не объявлена) — список из своих записей
    ПЛЮС строка словами: молчать о недоступности нельзя;
  * отбор и поиск — те же, что у ручки дерева, и судят обе половины
    списка; порядок — новые сверху; «показать ещё» — `limit`/`offset`
    при честном полном счёте;
  * неизвестный параметр — отказ 422 словами.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
каталоги — только `tmp_path`, адрес панели — вымышленная зона
`.invalid`, служб не поднимается, сеть подставлена.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_gate import client                      # noqa: E402,F401
from tests.test_run_tree import put, record                 # noqa: E402
from vmp import run_record as rr                            # noqa: E402

#: Ручка общего экрана.
RUNS_ALL = "/api/runs-all"
#: База ручек хранителя: вымышленная зона — сеть не трогается.
PANEL = "http://панель.invalid"
#: Публичный адрес панели для ссылок человека.
PANEL_PUBLIC = "http://панель-страницы.invalid"

BY_RUNNER = "раннер наборов пробы"
BY_HUMAN = "лицо смены"


@pytest.fixture(autouse=True)
def _all_env(tmp_path, monkeypatch):
    """Каталог данных, корни обхода и сроки годности — свои у каждого
    теста: значение с машины сделало бы прогон недетерминированным."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "данные"))
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)
    monkeypatch.delenv("CDPM_PUBLIC_URL", raising=False)
    monkeypatch.setenv("CREW_RUN_TREE_REBUILD_S", "0")
    from webapp import run_tree_api, runs_all_api
    run_tree_api.forget()
    runs_all_api.forget()


# ----------------------------------------------------------- помощники
def _panel_runs(tmp_path, monkeypatch) -> Path:
    """Каталог прогонов панели на этой машине — второй корень обхода."""
    folder = tmp_path / "прогоны-панели"
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(folder))
    return folder


def _own(run_id: str, **over) -> Path:
    """Частный прогон COSMOS в папке нажатий с маркером держателя: по
    нему у строки появляется номер прогона COSMOS."""
    from webapp import cosmos_runs
    folder = cosmos_runs.package_runs_root() / ("a" * 64)
    put(folder, record(run_id, **over))
    (folder / "holder.json").write_text(
        json.dumps({"run": "run-0008", "sha": "a" * 64},
                   ensure_ascii=False), encoding="utf-8")
    return folder


class _Resp:
    """Ответ хранителя для подставленного `urlopen`."""

    def __init__(self, body):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = 200

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _keeper(monkeypatch, answer, seen: list | None = None):
    """Подстановка сети у хранителя: `answer` — ответ либо исключение."""
    from webapp import runs_all_api

    def _open(req, timeout=None):
        if seen is not None:
            seen.append({"url": req.full_url, "method": req.get_method()})
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(runs_all_api._urlrequest, "urlopen", _open)


def _panel_row(run: str, **over) -> dict:
    """Строка списка прогонов панели — в её полях (`GET /api/runs`)."""
    row = {"run": run, "state": "оформление", "title": "постановка пробы",
           "customer": "Заказчик-проба", "created_at": "2026-09-08T09:00:00",
           "position": 0}
    row.update(over)
    return row


def _by_id(doc: dict) -> dict:
    out = {}
    for item in doc["items"]:
        out[item.get("record_id") or item.get("cdpm_run")] = item
    return out


# ------------------------------------------------------------ сложение
def test_строки_складываются_из_индекса_и_из_списка_панели(tmp_path,
                                                           monkeypatch):
    """Два источника: записи, которые знает COSMOS, и прогоны панели,
    которых в индексе ЕЩЁ НЕТ. Прогон, известный обеим системам, в
    списке один: удвоенная строка врала бы о числе прогонов."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0001", record("зап-1", at="2026-09-01T09:00:00+03:00",
                                   created_by=BY_RUNNER))
    _keeper(monkeypatch, _Resp([_panel_row("run-0001"),
                                _panel_row("run-0002",
                                           created_at="2026-09-08T10:00:00")]))
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)

    doc = runs_all_api.view()
    assert doc["total"] == 2, "прогон обеих систем сложился в одну строку"
    rows = _by_id(doc)
    assert set(rows) == {"зап-1", "run-0002"}
    assert rows["зап-1"]["source"] == runs_all_api.SOURCE_COSMOS
    assert rows["зап-1"]["cdpm_run"] == "run-0001"
    assert rows["run-0002"]["source"] == runs_all_api.SOURCE_KEEPER
    assert rows["run-0002"]["record_id"] is None, "чего панель не сказала — пусто"
    assert rows["run-0002"]["contour"] is None
    assert doc["keeper"]["reachable"] is True
    assert doc["keeper"]["url"] == PANEL + "/api/runs?brief=1"
    assert doc["words"] == []


def test_столбцы_строки_из_индекса(tmp_path, monkeypatch):
    """У строки COSMOS названы обе системы: номер записи, номер прогона
    панели (по месту записи), номер прогона COSMOS (по маркеру
    держателя), заказчик, площадка, кто объявил, дата, состояние,
    системы-летописцы и признак золотого."""
    from webapp import run_tree_api, runs_all_api
    _own("зап-своя", created_by=BY_HUMAN)
    _keeper(monkeypatch, OSError("сети нет"))

    row = runs_all_api.view()["items"][0]
    assert row["record_id"] == "зап-своя"
    assert row["cosmos_run"] == "run-0008"
    assert row["cdpm_run"] is None, "частный прогон панели не знает"
    assert row["customer"] == "Проба-дерево"
    assert row["contour"] == rr.CONTOURS[0]
    assert row["declared_by"] == run_tree_api.DECLARED_HUMAN
    assert row["created_by"] == BY_HUMAN
    assert row["state"] == rr.STATE_DONE
    assert row["golden"] is False
    assert row["systems"] == [], "журнал записи систем ещё не называл"


def test_системы_летописцы_видны_в_строке(tmp_path, monkeypatch):
    """Столбец «какие системы отметились» читается по журналу записи —
    через перечень систем узла дерева."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    doc = record("зап-систем")
    rr.append_stage(doc, {"stage": "пейринг", "outcome": rr.OUTCOME_OK,
                          "system": rr.SYSTEM_COSMOS})
    put(panel / "run-0003", doc)
    _keeper(monkeypatch, OSError("сети нет"))
    row = runs_all_api.view()["items"][0]
    assert row["systems"] == [rr.SYSTEM_COSMOS]


def test_ссылка_на_страницу_прогона_в_панели(tmp_path, monkeypatch):
    """Адрес страниц панели — `CDPM_PUBLIC_URL`, а без него хост
    машинных ручек; базы нет — нет и ссылки: выдумывать адрес чужой
    службы нельзя."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0004", record("зап-ссылка"))
    _keeper(monkeypatch, OSError("сети нет"))

    assert runs_all_api.view()["items"][0]["cdpm_url"] is None
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    runs_all_api.forget()
    assert runs_all_api.view()["items"][0]["cdpm_url"] == \
        PANEL + "/#/runs/run-0004"
    monkeypatch.setenv("CDPM_PUBLIC_URL", PANEL_PUBLIC)
    runs_all_api.forget()
    assert runs_all_api.view()["items"][0]["cdpm_url"] == \
        PANEL_PUBLIC + "/#/runs/run-0004"


# --------------------------------------------------------- хранитель
def test_недоступная_панель_не_ломает_экран(tmp_path, monkeypatch):
    """Список из своих записей ПЛЮС строка словами: молчать о
    недоступности нельзя — человек решил бы, что прогонов панели нет."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0005", record("зап-5"))
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    _keeper(monkeypatch, OSError("сеть недоступна"))

    doc = runs_all_api.view()
    assert doc["total"] == 1 and doc["items"][0]["record_id"] == "зап-5"
    assert doc["keeper"]["reachable"] is False
    assert doc["words"] and "панель недоступна" in doc["words"][0]
    assert "только записи COSMOS" in doc["words"][0]


def test_адрес_панели_не_объявлен_словами(tmp_path, monkeypatch):
    """Адреса хранителя нет — это состояние площадки, и оно называется
    словами вместе с именем переменной, а не пустым списком."""
    from webapp import run_gate, runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0006", record("зап-6"))
    _keeper(monkeypatch, AssertionError("сети тут быть не должно"))

    doc = runs_all_api.view()
    assert doc["keeper"]["reachable"] is False
    assert doc["keeper"]["url"] is None
    assert doc["words"] and run_gate.RECORD_API_ENV in doc["words"][0]
    assert doc["total"] == 1


def test_панель_ответила_не_перечнем(tmp_path, monkeypatch):
    """Ответ не по договору — не перечень прогонов: строка словами, а не
    исключение наружу."""
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    _keeper(monkeypatch, _Resp({"что-то": "иное"}))
    doc = runs_all_api.view()
    assert doc["keeper"]["reachable"] is False and doc["words"]


def test_к_панели_ходят_раз_в_срок_годности(tmp_path, monkeypatch):
    """Переключение пилюль отбора не гоняет сеть: один поход к панели
    обслуживает любой отбор в пределах срока годности."""
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    seen: list = []
    _keeper(monkeypatch, _Resp([_panel_row("run-0007")]), seen)
    runs_all_api.view()
    runs_all_api.view({"contour": rr.CONTOURS[0]})
    assert len(seen) == 1, "второй отбор снова сходил к панели"
    assert seen[0]["method"] == "GET" and seen[0]["url"].endswith("/api/runs?brief=1")
    runs_all_api.view({"refresh": "1"})
    assert len(seen) == 2, "прямая просьба обновить к панели не сходила"


# ------------------------------------------------------------- отбор
def test_отбор_судит_обе_половины_списка(tmp_path, monkeypatch):
    """Отбор — общий с ручкой дерева и применяется и к своим записям, и
    к строкам панели."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0010", record("зап-10", at="2026-09-02T09:00:00+03:00",
                                   created_by=BY_RUNNER))
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    _keeper(monkeypatch, _Resp([
        _panel_row("run-0011", customer="Заказчик-другой",
                   created_at="2026-09-06T09:00:00")]))

    assert runs_all_api.view()["total"] == 2
    assert [i["cdpm_run"] for i in
            runs_all_api.view({"customer": "Заказчик-другой"})["items"]] == \
        ["run-0011"]
    assert runs_all_api.view({"declared_by": "runner"})["total"] == 1
    assert runs_all_api.view({"since": "2026-09-05"})["total"] == 1
    assert runs_all_api.view({"q": "run-0011"})["total"] == 1
    assert runs_all_api.view({"golden": "да"})["total"] == 0


def test_порядок_новые_сверху_и_показать_ещё(tmp_path, monkeypatch):
    """Новые сверху; страница — `limit`/`offset`, а полный счёт строк
    отдаётся всегда: по нему человек видит, сколько осталось."""
    from webapp import runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    for n in range(1, 4):
        put(panel / ("run-002%d" % n),
            record("зап-2%d" % n, at="2026-09-0%d" % n + "T09:00:00+03:00"))
    _keeper(monkeypatch, OSError("сети нет"))

    doc = runs_all_api.view()
    assert [i["record_id"] for i in doc["items"]] == ["зап-23", "зап-22",
                                                      "зап-21"]
    page = runs_all_api.view({"limit": "2"})
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 0
    more = runs_all_api.view({"limit": "2", "offset": "2"})
    assert [i["record_id"] for i in more["items"]] == ["зап-21"]
    assert more["total"] == 3


# ------------------------------------------------------------- отказы
def test_неизвестный_параметр_отказ_словами(tmp_path, monkeypatch):
    from webapp import run_tree_api as rta
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    with pytest.raises(rta.RunTreeApiError) as e:
        runs_all_api.view({"страница": "2"})
    assert e.value.status == 422 and e.value.code == rta.CODE_FILTER_UNKNOWN
    assert "страница" in e.value.text()


def test_страница_не_число_отказ_словами(tmp_path, monkeypatch):
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    with pytest.raises(Exception) as e:
        runs_all_api.view({"limit": "много"})
    assert "limit" in str(e.value) and "много" in str(e.value)


# --------------------------------------------------------------- ручка
def test_ручка_общего_экрана(client, tmp_path, monkeypatch):
    """`GET /api/runs-all` отдаёт `{items, total, words, keeper}` и
    отказывает 422 словами на параметр, которого не знает."""
    from webapp import run_tree_api, runs_all_api
    panel = _panel_runs(tmp_path, monkeypatch)
    put(panel / "run-0030", record("зап-30"))
    run_tree_api.forget()
    runs_all_api.forget()
    _keeper(monkeypatch, OSError("сети нет"))

    r = client.get(RUNS_ALL)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert set(doc) >= {"items", "total", "words", "keeper"}
    assert doc["total"] == 1 and doc["items"][0]["record_id"] == "зап-30"
    assert doc["keeper"]["reachable"] is False

    r = client.get(RUNS_ALL, params={"contour": rr.CONTOURS[0]})
    assert r.status_code == 200 and r.json()["total"] == 1
    r = client.get(RUNS_ALL, params={"contour": rr.CONTOURS[3]})
    assert r.json()["total"] == 0, "отбор идёт на сервере"

    r = client.get(RUNS_ALL, params={"страница": "2"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == run_tree_api.CODE_FILTER_UNKNOWN
    assert "страница" in r.json()["detail"]


def test_поля_записи_из_списка_панели_подхватываются(tmp_path, monkeypatch):
    """С волны 15 панель отдаёт в списке прогонов поля записи: номер
    записи, площадку, кто объявил, золотой и номер прогона COSMOS. Строка
    «только в панели» несёт их как есть; чего панель не назвала (null) —
    клетка пуста, догадок нет."""
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    _keeper(monkeypatch, _Resp([
        _panel_row("run-0009", record_id="зап-9", contour="test",
                   created_by=BY_RUNNER, cosmos_run="run-0035",
                   golden={"confirmed": True, "by": "владелец",
                           "at": "2026-09-11T07:00:00+02:00", "words": "слово"}),
        _panel_row("run-0010", record_id=None, contour=None, created_by=None,
                   golden=None, cosmos_run=None)]))
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)

    rows = _by_id(runs_all_api.view())
    full = rows["зап-9"]
    assert full["source"] == runs_all_api.SOURCE_KEEPER
    assert full["cdpm_run"] == "run-0009"
    assert full["cosmos_run"] == "run-0035"
    assert full["contour"] == "test"
    assert full["created_by"] == BY_RUNNER
    assert full["declared_by"] == "runner"
    assert full["golden"] is True
    empty = rows["run-0010"]
    assert empty["record_id"] is None and empty["contour"] is None
    assert empty["declared_by"] is None and empty["cosmos_run"] is None
    assert empty["golden"] is False



def test_быстрый_список_панели_рв145(tmp_path, monkeypatch):
    """РВ-145: экран ходит к панели за быстрым списком (`brief=1`) — без
    пересчёта готовности каждого прогона. Строка экрана строится лишь из
    полей записи и состояния; они — договор быстрого списка с панелью."""
    from webapp import runs_all_api
    _panel_runs(tmp_path, monkeypatch)
    monkeypatch.setenv("CREW_CDPM_RECORD_API", PANEL)
    seen: list = []
    _keeper(monkeypatch, _Resp([_panel_row("run-0011", record_id="зап-11", contour="prod",
                                           created_by=BY_RUNNER, cosmos_run="run-0040",
                                           golden=None)]), seen)
    rows = _by_id(runs_all_api.view())
    assert seen[0]["url"] == PANEL + "/api/runs?brief=1"
    row = rows["зап-11"]
    assert (row["cdpm_run"], row["cosmos_run"], row["contour"], row["state"]) == (
        "run-0011", "run-0040", "prod", "оформление")
    # Договор: какие поля строки панели читает экран — быстрый список обязан их нести.
    import inspect
    src = inspect.getsource(runs_all_api._keeper_item)
    for field in ("run", "record_id", "cosmos_run", "customer", "contour",
                  "created_by", "created_at", "state", "golden", "title"):
        assert 'row.get("%s")' % field in src, field
