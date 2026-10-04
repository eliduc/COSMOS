# -*- coding: utf-8 -*-
"""Конвейер пополнения каталога шаблонов на стороне COSMOS (шаг 6,
дорожка B): ручки вида «Каталог», сводка экрана, сверка при старте и в
нити планировщика, строки заявок нажатия.

Правило прогонов: живой `catalog/` репозитория тесты НЕ трогают — все
пробы идут на копии (`tests/catalog_copies`), и последний тест файла это
проверяет побайтно.
"""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import tests.catalog_copies as cc
from webapp import catalog_pipeline as cp
from webapp import change_requests as crq
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
#: Адрес службы каталога для стендов — вымышленный (зона `.invalid`
#: закреплена RFC 6761 и не разрешается никогда). Стенды сверку не
#: выполняют — она подменяется, — и зависеть от того, что слушает
#: порт на машине разработчика, не вправе.
SERVICE_URL = "http://catalog.invalid"
USER = "someone@example.com"
LIVE = cc.CATALOG


def _request(record_id="duty_limits", kinds=("check_code",),
             state="принята", cr_key=None):
    from template_catalog import requests as law
    key = law.kind_request_key("families", record_id, kinds)
    return {"request_id": law.request_id_of(key), "state": state,
            "section": "families", "record_id": record_id,
            "submitted_at": "2026-09-12T10:15:00+03:00",
            "submitted_by": "COSMOS",
            "source": {"kind": "пробел при нажатии", "document_class": None,
                       "run_class": "прогон COSMOS",
                       "ref": "run-0031#" + key[:12]},
            "draft": None,
            "executor_kinds_requested": sorted(set(kinds)),
            "decision": {"by": "владелец",
                         "at": "2026-09-12T10:00:00+03:00",
                         "words": "нужна механика"},
            "cr_key": cr_key}


@pytest.fixture()
def catalog_copy(tmp_path, monkeypatch):
    from vmp import catalog_access as ca

    def make(requests):
        path = cc.copy_catalog(tmp_path)
        (path / "requests.json").write_text(
            json.dumps({"form_version": 1, "requests": list(requests)},
                       ensure_ascii=False), encoding="utf-8")
        cc.use_catalog(monkeypatch, path)
        return path
    yield make
    ca.forget()


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "crs.json")
    yield tmp_path


@pytest.fixture()
def client(tmp_path, monkeypatch, iso):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


# ------------------------------------------------------------- ручки

def test_ручки_каталога_только_для_админа(client, monkeypatch):
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/cr/catalog").status_code == 403
    assert client.post("/api/cr/catalog/sync").status_code == 403
    assert client.post("/api/cr/catalog/deliver").status_code == 403
    assert client.post("/api/task-package/current/press/catalog-requests",
                       json={}).status_code == 403


def test_кнопка_нажатия_не_принимает_токен_канала(client, monkeypatch):
    """Путь кнопки лежит под машинным префиксом ручек нажатия (раннеры
    площадок ходят туда токеном канала), но подача заявки в каталог —
    действие администратора: отказ словами, а не 500 «личности нет»."""
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "channel-secret")
    r = client.post("/api/task-package/current/press/catalog-requests",
                    json={}, headers={"X-Channel-Token": "channel-secret"})
    assert r.status_code == 403
    assert "администратора" in r.json()["detail"]


def test_номер_заявки_не_по_закону_даёт_слова_а_не_500(client,
                                                       catalog_copy):
    """Номер прежней формы (`req-0001`) читатель пропускает (R01), а
    закон ключа — нет: вид «Каталог» обязан сказать словами.

    Прежде `cr_key_of` поднимал ValueError прямо в ручке и в нити
    сверки: вид отдавал 500, нить гасла строкой журнала (ревью волны 2).
    Такой номер приходит правкой `requests.json` руками или загрузкой
    файла службы прежней волны."""
    rq = dict(_request(), request_id="req-0001")
    catalog_copy([rq])
    doc = client.get("/api/cr/catalog")
    assert doc.status_code == 200, doc.text
    row = doc.json()["requests"][0]
    assert row["request_id"] == "req-0001"
    assert row["cr_key_expected"] is None and row["cr_status"] is None
    assert "req-0001" in row["cr_key_words"]
    out = cp.sync_catalog_requests(write_back=False)
    assert out["opened"] == [] and out["keyed"] == []
    assert any("req-0001" in p for p in out["problems"]), out["problems"]


def test_ручка_вида_каталога_отдаёт_сводку(client, catalog_copy):
    rq = _request()
    catalog_copy([rq])
    doc = client.get("/api/cr/catalog").json()
    assert [r["request_id"] for r in doc["requests"]] == [rq["request_id"]]
    row = doc["requests"][0]
    from template_catalog import requests as law
    assert row["cr_key_expected"] == law.cr_key_of(rq["request_id"])
    assert row["kinds"] == ["check_code"] and row["cr_status"] is None
    assert doc["service"]["api_url"] is None      # канал изолирован
    assert doc["service"]["token"] is False       # секрета в ответе нет
    assert doc["outbox"]["pending"] == 0
    assert doc["auto"] is False                   # изоляция conftest


def test_ручка_сверки_отвечает_словами_без_службы(client, catalog_copy):
    catalog_copy([_request()])
    out = client.post("/api/cr/catalog/sync").json()
    assert out["opened"] == [] and out["keyed"] == []
    assert any("служба каталога недоступна" in p for p in out["problems"])


def test_ручка_доставки_говорит_почему_канал_выключен(client):
    out = client.post("/api/cr/catalog/deliver").json()
    assert out["delivered"] == [] and out["problems"]
    assert "адрес службы каталога не задан" in out["problems"][0]


def test_кнопка_нажатия_без_артефакта_отвечает_404(client):
    r = client.post("/api/task-package/current/press/catalog-requests",
                    json={"attempt": None, "rule_ids": None})
    assert r.status_code == 404


# ---------------------------------------------------- вид и кандидаты

def test_вид_каталога_сводит_заявки_строки_и_кандидатов(
        iso, catalog_copy, monkeypatch, tmp_path):
    from template_catalog import requests as law
    from vmp import catalog_access as ca
    rq = _request(state="исполнитель заявлен",
                  cr_key=law.cr_key_of(_request()["request_id"]))
    catalog_copy([rq])
    row = cp.build_row(rq, ca.current())
    row["impl"] = {"candidate": "catalog/candidates/families/x.json",
                   "branch": "cr-" + row["id"], "evidence_commit": "abc1234"}
    crq._add_crs([row], unique_by_id=True)
    view = cp.catalog_view()
    assert view["requests"][0]["cr_status"] == "accepted"
    assert view["requests"][0]["cr_key"] == row["id"]
    assert [c["cr_key"] for c in view["candidates"]] == [row["id"]]
    # файла в дереве нет — вид честно говорит, что кандидат в ветке
    assert "не слита" in view["candidates"][0]["where"]


def test_строки_нажатия_пересчитывают_ключ_и_ловят_расхождение(
        catalog_copy):
    from template_catalog import requests as law
    catalog_copy([])
    key = law.kind_request_key("families", "duty_limits", ["check_code"])
    good = {"rule_id": "R-1", "form": "kind", "section": "families",
            "record": "duty_limits", "kinds": ["check_code"],
            "request_id": law.request_id_of(key), "state": None,
            "words": "", "link": None}
    bad = {**good, "rule_id": "R-2", "request_id": "req-" + "0" * 20}
    other = {**good, "rule_id": "R-3", "form": "new"}
    rows, problems = cp.press_rows({"catalog": [good, bad, other]})
    assert [r["rule_id"] for r in rows] == ["R-1"]
    assert rows[0]["key"] == key
    assert len(problems) == 1 and "не сходится с законом ключа" in problems[0]
    # отбор по правилам нажатия
    rows2, _ = cp.press_rows({"catalog": [good]}, ["R-9"])
    assert rows2 == []


# --------------------------------------------------- старт и планировщик

def test_старт_приложения_зовёт_сверку_нитью_и_переживает_отказ(
        monkeypatch, capsys):
    monkeypatch.setattr(cp, "START_DELAY_S", 0.0)
    monkeypatch.delenv("CREW_CATALOG_API_URL", raising=False)
    assert cp.start_sync_thread() is None          # канал не настроен
    monkeypatch.setenv("CREW_CATALOG_API_URL", SERVICE_URL)

    def boom(*a, **kw):
        raise RuntimeError("служба легла")
    monkeypatch.setattr(cp, "sync_catalog_requests", boom)
    t = cp.start_sync_thread()
    assert t is not None
    t.join(timeout=5)
    assert not t.is_alive()
    assert "сверка заявок каталога не выполнена" in capsys.readouterr().out


def test_ночная_работа_планировщика_несёт_сверку(iso, monkeypatch):
    called = []
    monkeypatch.setattr(crq, "run_feedback_agent",
                        lambda triggered_by=None: called.append("feedback"))
    monkeypatch.setattr(cp, "sync_catalog_requests",
                        lambda *a, **kw: called.append("catalog") or {})
    import datetime as _dt
    out = crq.run_daily_jobs(_dt.datetime(2026, 9, 12, 4, 35))  # пятница
    assert called == ["feedback", "catalog"]
    assert "regulatory" not in out                # регуляторика — понедельник
    called.clear()
    monkeypatch.setattr(crq, "run_regulatory_agent",
                        lambda triggered_by=None: called.append("regulatory"))
    crq.run_daily_jobs(_dt.datetime(2026, 9, 14, 4, 35))         # понедельник
    assert called == ["feedback", "regulatory", "catalog"]


def test_сверка_переживает_упавшую_ночную_работу(iso, monkeypatch):
    """Сбой одной работы не отменяет остальных — итог говорит словами."""
    def boom(triggered_by=None):
        raise crq.ChangeRequestError("модель недоступна")
    monkeypatch.setattr(crq, "run_feedback_agent", boom)
    seen = []
    monkeypatch.setattr(cp, "sync_catalog_requests",
                        lambda *a, **kw: seen.append(1) or {"opened": []})
    out = crq.run_daily_jobs()
    assert "не отработал" in out["feedback"] and seen == [1]


# ------------------------------------------------------------ изоляция

#: Идентификатор НОВОЙ записи в стендах заявки на новую запись — вымышленный:
#: живой id (duty_series_limit) на ветке CR уже есть в копии кандидатом
#: дерева, и заявка «принята» с черновиком на него — законный отказ R02
#: «запись с таким id уже есть» (попытка #4, 09.09.2026). Стенд не
#: вправе зависеть от того, какие кандидаты лежат рядом с каталогом.
NEW_ID = "phantom_series_limit"


def _draft(kinds=None, *, criterion=False, cat=None):
    """Черновик записи по образцу заявки CDPM (09.09.2026, семья
    duty_series_limit): виды исполнителей объявлены полем ЗАПИСИ
    (`executor_kinds` у семьи, `computable_by` у критерия), поля
    привязок `executors` у записи нет. С `cat` — полный черновик по
    схеме (запись семьи каталога без status/history/stand_tasks, R03),
    без него — короткий, для чистых функций."""
    if cat is not None:
        import copy
        rec = copy.deepcopy(cat.family("night_series"))
        d = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
        d.update({"id": NEW_ID, "class": "extension",
                  "title": "Лимит серии рабочих смен подряд",
                  "params": [], "param_sets": [], "conditions": [],
                  "complements": [], "subject_words": None, "rule_form": None,
                  "check_templates": [],      # шаблоны проверок ⇔ вид check_template
                  "note": "заявка CDPM из посадки категории C"})
    else:
        d = {"id": NEW_ID, "class": "extension",
             "title": "Лимит серии рабочих смен подряд", "kind": "HARD",
             "zone": "ROSTERING", "nature": "history", "params": [],
             "note": "заявка CDPM из посадки категории C"}
    if kinds is not None:
        d["computable_by" if criterion else "executor_kinds"] = list(kinds)
    return d


def test_виды_заявки_на_новую_запись_читаются_из_черновика_записи():
    """Первая заявка CDPM на новую семью шла с видами только в черновике
    (`executor_kinds_requested` пустое) и рождала «нужен человек: видов
    не названо»: COSMOS читал поле привязок `executors`, которого у записи
    каталога нет."""
    rq = dict(_request(kinds=()), draft=_draft(["kotlin_logic_form", "check_code"]),
              executor_kinds_requested=None)
    assert cp._kinds_of(rq, None) == ["check_code", "kotlin_logic_form"]
    crit = dict(rq, section="criteria", draft=_draft(["kotlin_metric"], criterion=True))
    assert cp._kinds_of(crit, None) == ["kotlin_metric"]
    # явный перечень заявки главнее объявленного черновиком
    explicit = dict(rq, executor_kinds_requested=["check_code"])
    assert cp._kinds_of(explicit, None) == ["check_code"]
    # черновик без видов — видов нет (строка родится «нужен человек»)
    assert cp._kinds_of(dict(rq, draft=_draft(None)), None) == []
    # запись-привязка продукта (перечень исполнителей) читается по-прежнему
    assert cp._kinds_of(dict(rq, draft=None),
                        {"executors": [{"kind": "check_code"}]}) == ["check_code"]


def test_нужен_человек_рождения_без_видов_переоткрывается_сверкой(
        catalog_copy, iso, client, monkeypatch):
    """Строка «видов не названо» закрыта мгновенно, без работы агента;
    когда виды появились (заявка правлена в службе либо чтение видов
    починено), сверка переоткрывает её принятой — решение по заявке
    владелец уже принял, второй отмашки не просим. Строку с веткой
    (агент работал) сверка молча не переоткрывает."""
    # внешний контур: строки очереди открываются только вне prod/stage
    monkeypatch.setenv("COSMOS_PROD_DIR", str(iso / "prod-none"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(iso / "stage-none"))
    import template_catalog as tc
    from template_catalog import requests as law
    live = tc.load(LIVE)
    rq = dict(_request(record_id=NEW_ID, kinds=("check_code",)),
              draft=_draft(["kotlin_logic_form", "check_code"], cat=live),
              executor_kinds_requested=None)
    path = catalog_copy([rq])
    cat = tc.load(path)
    cid = law.cr_key_of(rq["request_id"])
    # строка, какой её родил прежний код (виды из черновика не прочитаны)
    stale = cp.build_row(rq, cat)
    stale.update({"status": "needs_human",
                  "title": "Исполнитель записи families/duty_series_limit: "
                           "видов не названо",
                  "impl": {"needs_human": True,
                           "report": "заявка не называет ни одного вида "
                                     "исполнителя: реализовывать нечего"}})
    assert cid == stale["id"]
    assert crq._add_crs([stale], unique_by_id=True)
    assert cp._birth_needs_human(stale) == cp.NO_KIND_REOPEN_WORDS
    assert cp._birth_needs_human(dict(stale, impl={**stale["impl"], "branch": "cr-x"})) is None
    assert cp._birth_needs_human(dict(stale, status="accepted")) is None
    assert cp._birth_needs_human({"status": "needs_human",
                                  "impl": {"report": "агент упал на сборке"}}) is None
    r = client.post("/api/cr/catalog/sync")
    assert r.status_code == 200, r.text
    assert cid in r.json()["refreshed"], r.json()
    row = crq.get_cr(cid)
    assert row["status"] == "accepted" and row["impl"] is None
    assert "check_code, kotlin_logic_form" in row["title"]
    assert row["journal"][-1]["words"] == cp.NO_KIND_REOPEN_WORDS
    # повтор сверки ничего не меняет
    r = client.post("/api/cr/catalog/sync")
    assert cid not in r.json()["opened"]
    assert crq.get_cr(cid)["status"] == "accepted"


def test_смена_каталога_под_открытой_строкой_обновляет_привязку(
        catalog_copy, iso, client, monkeypatch, tmp_path):
    """Выпуск каталога между открытием CR и реализацией: сверка обновляет
    привязку открытой строки к живому каталогу (record.catalog_*) со
    словами в журнале — иначе предпосылка реализации отказывает «каталог
    в клоне не тот, по которому открыт CR» (09.09.2026, CR
    duty_series_limit: открыт на 0.2.0, выпуск 0.3.0)."""
    monkeypatch.setenv("COSMOS_PROD_DIR", str(iso / "prod-none"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(iso / "stage-none"))
    import template_catalog as tc
    from template_catalog import requests as law
    from vmp import catalog_access as ca
    live = tc.load(LIVE)
    rq = dict(_request(record_id=NEW_ID, kinds=("check_code",)),
              draft=_draft(["kotlin_logic_form", "check_code"], cat=live),
              executor_kinds_requested=None)
    catalog_copy([rq])
    cid = law.cr_key_of(rq["request_id"])
    r = client.post("/api/cr/catalog/sync")
    assert r.status_code == 200 and cid in r.json()["opened"], r.text
    before = crq.get_cr(cid)["record"]["catalog_fingerprint"]
    # каталог сменился: другая копия с правкой записи (иной отпечаток)
    path2 = cc.copy_catalog(tmp_path, name="catalog2",
                            families={"duty_limits": {"note": "правка после выпуска"}},
                            requests=False)
    (path2 / "requests.json").write_text(
        json.dumps({"form_version": 1, "requests": [rq]}, ensure_ascii=False),
        encoding="utf-8")
    cc.use_catalog(monkeypatch, path2)
    ca.forget()
    after_fp = tc.load(path2).fingerprint()
    assert after_fp != before
    r = client.post("/api/cr/catalog/sync")
    assert r.status_code == 200 and cid in r.json()["refreshed"], r.json()
    row = crq.get_cr(cid)
    assert row["status"] == "accepted"
    assert row["record"]["catalog_fingerprint"] == after_fp
    assert "каталог сменился" in row["journal"][-1]["words"]
    # повтор — без изменений
    r = client.post("/api/cr/catalog/sync")
    assert cid not in r.json()["refreshed"]


def test_живой_каталог_репозитория_не_тронут(catalog_copy, iso, client):
    """Правило прогонов: пробы идут на копии. Проверка побайтная — по
    ВСЕМ файлам верхнего уровня живого каталога."""
    before = {p.name: p.read_bytes() for p in sorted(LIVE.glob("*.json"))}
    assert before, "живой каталог не найден — проверка была бы пустой"
    catalog_copy([_request()])
    client.get("/api/cr/catalog")
    client.post("/api/cr/catalog/sync")
    after = {p.name: p.read_bytes() for p in sorted(LIVE.glob("*.json"))}
    assert after == before
    print(f"файлов живого каталога сверено: {len(before)}")
