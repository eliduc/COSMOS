# -*- coding: utf-8 -*-
"""Отбор прогонов в разделе «Дерево прогонов» (webapp/run_tree_api.py и
ручка `GET /api/run-tree`).

Основание — слово владельца 11.09.2026 («Нужно») и проект
`final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, раздел 12. Судится ровно
то, ради чего отбор заведён:

  * отбор идёт НА СЕРВЕРЕ по полям головы записи — набор/заказчик,
    площадка, состояние, кто объявил, золотой, период по дате
    объявления, корень дерева — и текстовым поиском по номеру записи и
    номеру прогона; интерфейс только передаёт параметры;
  * форма ответа не меняется: отобранный индекс сходится со своей
    схемой, а корень без единой оставшейся ветви уходит целиком;
  * отказы обхода отбором не скрываются: они говорят не о прогонах;
  * параметр, которого ручка не знает, и значение не по словарю —
    отказы 422 СЛОВАМИ, а не молчаливый полный список.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
каталоги — только `tmp_path`, номера прогонов вымышленные, служб не
поднимается, сеть не трогается.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_gate import client                      # noqa: E402,F401
from tests.test_run_tree import put, record                 # noqa: E402
from vmp import run_record as rr                            # noqa: E402
from vmp import run_tree as rt                              # noqa: E402

#: Ручка индекса дерева.
TREE = "/api/run-tree"

#: Кто объявил прогон — слова тех, кто их пишет: раннер наборов, панель
#: CDPM и человек (в COSMOS объявляет личность сессии).
BY_RUNNER = "раннер наборов пробы"
BY_PANEL = "панель CDPM"
BY_HUMAN = "лицо смены"


@pytest.fixture(autouse=True)
def _tree_env(tmp_path, monkeypatch):
    """Каталог данных и корни обхода — свои у каждого теста."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "данные"))
    monkeypatch.delenv("CREW_CDPM_RUNS_DIR", raising=False)
    monkeypatch.setenv("CREW_RUN_TREE_REBUILD_S", "0")
    from webapp import run_tree_api
    run_tree_api.forget()


def _tree(tmp_path) -> dict:
    """Четыре прогона: два на одном корне (разные площадки и состояния),
    третий — другого заказчика на своём корне, четвёртый — золотой."""
    folder = tmp_path / "прогоны"
    put(folder / "зап-1", record(
        "зап-1", at="2026-09-01T09:00:00+03:00", created_by=BY_RUNNER))
    put(folder / "зап-2", record(
        "зап-2", at="2026-09-05T09:00:00+03:00", created_by=BY_PANEL,
        contour={"name": rr.CONTOURS[3], "services": {}}, model="модель-б"))
    doc3 = record("зап-3", at="2026-09-09T09:00:00+03:00",
                  created_by=BY_HUMAN,
                  airline={"name": "Заказчик-другой", "data_class": "synthetic",
                           "llm_scope": "any"},
                  answers=(rr.sha256_text("иные ответы"),),
                  rules_documents=[{"name": "ИНЫЕ.docx", "kind": "docx",
                                    "sha256": rr.sha256_text("иные правила"),
                                    "size": 10}])
    put(folder / "зап-3", doc3)
    doc4 = record("зап-4", at="2026-09-10T09:00:00+03:00", created_by=BY_PANEL,
                  contour={"name": rr.CONTOURS[3], "services": {}},
                  engine="cplex")
    rt.mark_golden(doc4, by=BY_HUMAN, words="образец площадки")
    put(folder / "зап-4", doc4)
    return rt.build([folder])


def _ids(tree: dict) -> list:
    return sorted(n["run_id"] for n in rt.nodes(tree))


def _pick(tree: dict, **flt) -> list:
    from webapp import run_tree_api
    return _ids(run_tree_api.filter_tree(tree, run_tree_api.filters_from(flt)))


# ----------------------------------------------------------- поля отбора
def test_отбор_по_заказчику_площадке_и_состоянию(tmp_path):
    """Три поля головы записи: набор/заказчик, площадка и состояние.
    Заказчик сравнивается без оглядки на регистр — человек набирает имя
    руками."""
    tree = _tree(tmp_path)
    assert _pick(tree) == ["зап-1", "зап-2", "зап-3", "зап-4"]
    assert _pick(tree, customer="Заказчик-другой") == ["зап-3"]
    assert _pick(tree, customer="заказчик-другой") == ["зап-3"]
    assert _pick(tree, contour=rr.CONTOURS[3]) == ["зап-2", "зап-4"]
    assert _pick(tree, contour=rr.CONTOURS[0]) == ["зап-1", "зап-3"]
    assert _pick(tree, state=rr.STATE_DONE) == ["зап-1", "зап-2", "зап-3",
                                                "зап-4"]
    assert _pick(tree, state=rr.STATE_RUNNING) == []


def test_отбор_по_тому_кто_объявил(tmp_path):
    """Три вида объявившего: раннер наборов, панель CDPM и человек. Вид
    судится приметой слова, а не точным именем: у каждого набора свой
    раннер, и точный перечень устарел бы на следующем наборе."""
    from webapp import run_tree_api as rta
    tree = _tree(tmp_path)
    assert _pick(tree, declared_by=rta.DECLARED_RUNNER) == ["зап-1"]
    assert _pick(tree, declared_by=rta.DECLARED_PANEL) == ["зап-2", "зап-4"]
    assert _pick(tree, declared_by=rta.DECLARED_HUMAN) == ["зап-3"]
    assert rta.declared_by({"created_by": None}) is None


def test_отбор_по_золотому_и_по_периоду(tmp_path):
    """Золотой — да и нет; период — днями по дате ОБЪЯВЛЕНИЯ, обе
    границы включительно."""
    tree = _tree(tmp_path)
    assert _pick(tree, golden="да") == ["зап-4"]
    assert _pick(tree, golden="0") == ["зап-1", "зап-2", "зап-3"]
    assert _pick(tree, since="2026-09-05") == ["зап-2", "зап-3", "зап-4"]
    assert _pick(tree, until="2026-09-05") == ["зап-1", "зап-2"]
    assert _pick(tree, since="2026-09-05", until="2026-09-09") == ["зап-2",
                                                                  "зап-3"]


def test_отбор_по_корню_дерева_и_поиском(tmp_path):
    """Корень берётся и коротким отпечатком (в карточке он показан
    коротким), поиск — по номеру записи."""
    tree = _tree(tmp_path)
    root = rt.find_node(tree, "зап-3")["root_sha256"]
    assert _pick(tree, root=root) == ["зап-3"]
    assert _pick(tree, root=root[:12]) == ["зап-3"]
    assert _pick(tree, q="зап-4") == ["зап-4"]
    assert _pick(tree, q="ЗАП-4") == ["зап-4"], "поиск не смотрит на регистр"
    assert _pick(tree, q="такого номера нет") == []


def test_несколько_полей_отбора_складываются(tmp_path):
    tree = _tree(tmp_path)
    assert _pick(tree, contour=rr.CONTOURS[3], golden="нет") == ["зап-2"]


# ------------------------------------------------------- форма ответа
def test_отобранный_индекс_сходится_со_своей_схемой(tmp_path):
    """Отбор только УБИРАЕТ ветви: форма ответа та же, и корень без
    единой оставшейся ветви уходит целиком — показывать корень, у
    которого нечего показать, значило бы соврать про отбор."""
    from webapp import run_tree_api
    tree = _tree(tmp_path)
    assert len(tree["roots"]) == 2
    got = run_tree_api.filter_tree(
        tree, run_tree_api.filters_from({"customer": "Заказчик-другой"}))
    assert rt.validate_index(got) == []
    assert got["schema"] == rt.SCHEMA and got["built_at"] == tree["built_at"]
    assert len(got["roots"]) == 1
    assert [n["run_id"] for n in got["roots"][0]["runs"]] == ["зап-3"]
    # исходный индекс не тронут: правится копия перечней
    assert len(tree["roots"]) == 2 and len(rt.nodes(tree)) == 4


def test_отказы_обхода_отбором_не_скрываются(tmp_path):
    """Отказ обхода говорит не о прогоне, а о том, чего обход НЕ
    прочитал: прятать его отбором значило бы скрыть беду."""
    from webapp import run_tree_api
    folder = tmp_path / "прогоны-с-мусором"
    put(folder / "добрый", record("добрый"))
    (folder / "мусор").mkdir(parents=True, exist_ok=True)
    (folder / "мусор" / rr.RECORD_FILE).write_text("не json",
                                                   encoding="utf-8")
    tree = rt.build([folder])
    assert len(tree["refusals"]) == 1
    got = run_tree_api.filter_tree(tree,
                                   run_tree_api.filters_from({"q": "нет"}))
    assert rt.nodes(got) == [] and len(got["refusals"]) == 1


# ------------------------------------------------------------- отказы
def test_неизвестный_параметр_отказ_словами():
    """Молча показать полный список там, где просили отбор, нельзя:
    человек решил бы, что отбор пуст."""
    from webapp import run_tree_api as rta
    with pytest.raises(rta.RunTreeApiError) as e:
        rta.check_params(["contour", "заказчик"], ("rebuild",))
    assert e.value.status == 422 and e.value.code == rta.CODE_FILTER_UNKNOWN
    assert "заказчик" in e.value.text() and "contour" in e.value.text()
    rta.check_params(list(rta.FILTER_PARAMS) + ["rebuild"], ("rebuild",))


@pytest.mark.parametrize("param,value", [
    ("contour", "площадка-которой-нет"),
    ("state", "состояние-которого-нет"),
    ("declared_by", "кто-то"),
    ("golden", "может быть"),
    ("since", "вчера"),
    ("until", "2026-13"),
])
def test_значение_не_по_словарю_отказ_словами(param, value):
    from webapp import run_tree_api as rta
    with pytest.raises(rta.RunTreeApiError) as e:
        rta.filters_from({param: value})
    assert e.value.status == 422 and e.value.code == rta.CODE_FILTER_VALUE
    assert param in e.value.text() and value in e.value.text()


def test_пустое_значение_параметра_отбором_не_считается():
    """«Все» интерфейс шлёт тем же адресом — пустым значением."""
    from webapp import run_tree_api as rta
    assert rta.filters_from({"contour": "", "q": "  "}) == {}


# --------------------------------------------------------------- ручка
def test_ручка_дерева_отбирает_на_сервере(client, tmp_path, monkeypatch):
    """Ручка `GET /api/run-tree` принимает те же параметры и отдаёт
    ОТОБРАННЫЙ индекс: интерфейс только передаёт параметры, отбора в
    браузере нет (одно место правды)."""
    from webapp import run_tree_api
    _tree(tmp_path)
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(tmp_path / "прогоны"))
    run_tree_api.forget()
    run_tree_api.rebuild("стенд")

    r = client.get(TREE)
    assert r.status_code == 200, r.text
    assert _ids(r.json()) == ["зап-1", "зап-2", "зап-3", "зап-4"]

    r = client.get(TREE, params={"contour": rr.CONTOURS[3], "golden": "да"})
    assert r.status_code == 200, r.text
    assert _ids(r.json()) == ["зап-4"]
    assert rt.validate_index(r.json()) == []

    r = client.get(TREE, params={"q": "зап-1", "declared_by": "runner"})
    assert _ids(r.json()) == ["зап-1"]


def test_ручка_дерева_отказывает_словами(client, tmp_path, monkeypatch):
    """Неизвестный параметр и значение не по словарю — 422 СЛОВАМИ с
    кодом: `detail` строкой читают сегодняшние клиенты, рядом — код и
    слова перечнем."""
    from webapp import run_tree_api as rta
    _tree(tmp_path)
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(tmp_path / "прогоны"))
    rta.forget()

    r = client.get(TREE, params={"заказчик": "кто-нибудь"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == rta.CODE_FILTER_UNKNOWN
    assert "заказчик" in r.json()["detail"]
    assert r.json()["words"], "отказ без слов не отказ"

    r = client.get(TREE, params={"contour": "площадка-которой-нет"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == rta.CODE_FILTER_VALUE
    for name in rr.CONTOURS:
        assert name in r.json()["detail"]
