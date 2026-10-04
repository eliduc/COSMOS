# -*- coding: utf-8 -*-
"""Модели COSMOS в ГОЛОВЕ записи панели (волна 15, пункт 3; договор с
COSMOS 11.09.2026).

Находка К5: голову чеканит ХОЗЯИН записи, и до 11.09.2026 в ней стояли
только модели CDPM — сертификатор и читатель ИДАК не попадали в голову
вовсе, а значит не входили и в её отпечаток. Теперь панель спрашивает
среду COSMOS ручкой `GET /api/run/environment` ДО чеканки и кладёт его
модели рядом со своими словом `system`.

Охраняемое здесь:

  * `channel.environment` — один запрос, предел 5 с, токен канала в
    заголовке, допуск набора параметром `scope`; канал выключен, сеть
    молчит, отказ HTTP, тело не JSON, чужая схема — всё это `(None,
    слова причины)` и НИ ОДНОГО исключения наружу: опрос среды —
    летопись, и она не вправе отменить объявление прогона;
  * `run_record.cosmos_models` — строки головы по ответу COSMOS
    (зеркало `cosmos_head_models` раннера): система строки — `cosmos`
    принудительно, лишние ключи не копируются, пусто — СЛОВА причины
    теми же словами, что у раннера;
  * `new_declaration` — модели обеих систем в одной голове, коммит
    COSMOS от самого COSMOS, `models_note` ТОЛЬКО со словами. Ключевое
    следствие правила `OMITTED_WHEN_EMPTY` словаря: отпечаток панели
    (по сырой голове) обязан равняться отпечатку COSMOS (по
    нормализованной) — пустой `models_note` или пустая `system` их бы
    развели;
  * сверка «объявлено / на деле» судит ТОЛЬКО свои модели: чужая модель
    COSMOS в голове — не повод отказать в рождении прогона;
  * предпросмотр `GET /api/declaration-preview` и рождение прогона из
    браузера: та же голова, что ляжет на диск.

Стенд без среды: канал выключен фикстурой изоляции conftest, ответы
COSMOS — подменные, сети не бывает.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cdpm import channel
from cdpm import run_record as rr
import vmp.run_record as vrr

# Панель на тестовом клиенте и создание прогона — общие со стендом
# записи (шаг 2): второй такой же фикстуры заводить незачем.
from test_cdpm_run_record import _create, panel  # noqa: F401

#: Ответ ручки среды COSMOS — по договору (`cosmos-run-environment/v1`).
COSMOS_URL = "http://127.0.0.1:9"
COSMOS_TOKEN = "токен-канала"


def _env_answer(models=None, commit="косм-777") -> dict:
    rows = [{"role": "сертификатор", "model": "модель-сертификатора",
             "provider": "anthropic", "channel": "api", "effort": "high",
             "system": "cosmos"},
            {"role": "читатель ИДАК", "model": None, "provider": None,
             "channel": None, "effort": None, "system": "cosmos",
             # Слова причины у роли, которую сейчас назвать нельзя, —
             # в голову они не едут: словарь закрыт.
             "words": "читателя ИДАК при этом допуске нет"}]
    return {"schema": "cosmos-run-environment/v1", "contour": "local",
            "llm_scope": "any", "code": {"cosmos_commit": commit},
            "catalog": {"name": "template-catalog", "version": "0.5.0",
                        "fingerprint": "ca69", "path": None},
            "models": rows if models is None else models}


def _resp(text: str):
    """Ответ канала: тот же вид, что у стенда доставки пакета."""
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _R(text.encode("utf-8"))


@pytest.fixture()
def channel_on(monkeypatch):
    """Канал включён (адрес и токен контура): фикстура изоляции
    conftest гасит его каждому тесту — стенд канала задаёт своё сам."""
    monkeypatch.setenv("CREW_CHANNEL_URL", COSMOS_URL + "/")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", COSMOS_TOKEN)


# ───────────────────────────── ручка среды по каналу ─────────────────
def test_среда_спрашивается_одним_запросом_с_токеном_и_допуском(channel_on):
    """Ответ по договору — среда и пустая причина; в запросе токен
    канала, допуск набора и предел ожидания 5 с, повторов нет."""
    seen = {"calls": 0}

    def fake_urlopen(req, timeout=None):
        seen["calls"] += 1
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["method"] = req.get_method()
        return _resp(json.dumps(_env_answer(), ensure_ascii=False))

    env, why = channel.environment(scope="any", _urlopen=fake_urlopen)
    assert why == "" and env["schema"] == "cosmos-run-environment/v1"
    assert [m["role"] for m in env["models"]] == ["сертификатор",
                                                  "читатель ИДАК"]
    assert seen["calls"] == 1 and seen["method"] == "GET"
    assert seen["url"] == COSMOS_URL + "/api/run/environment?scope=any"
    assert seen["headers"]["x-channel-token"] == COSMOS_TOKEN
    assert seen["headers"]["accept"] == "application/json"
    # Предел ожидания короткий: у кнопки «Создать прогон» стоит человек.
    assert seen["timeout"] == channel._ENVIRONMENT_TIMEOUT_S == 5


def test_допуск_не_назван_вопросом_не_становится(channel_on):
    """Пустой допуск в запрос не едет: COSMOS возьмёт самый узкий сам."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _resp(json.dumps(_env_answer(), ensure_ascii=False))

    env, why = channel.environment(scope=None, _urlopen=fake_urlopen)
    assert why == "" and isinstance(env, dict)
    assert seen["url"] == COSMOS_URL + "/api/run/environment"


def test_канал_выключен_среда_не_спрашивается_вовсе(monkeypatch):
    """Нет адреса и токена — канал ВЫКЛЮЧЕН: слова причины и ни одного
    обращения к сети (fail-closed, как у доставки пакета)."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):            # pragma: no cover
        calls["n"] += 1
        raise AssertionError("выключенный канал не ходит в сеть")

    env, why = channel.environment(scope="any", _urlopen=fake_urlopen)
    assert env is None and calls["n"] == 0
    assert "канал выключен" in why
    # Токен есть, адреса нет — по-прежнему выключен.
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", COSMOS_TOKEN)
    assert channel.environment(_urlopen=fake_urlopen)[0] is None


@pytest.mark.parametrize("beda, слово", [
    ("http", "COSMOS отказал: HTTP 503"),
    ("сеть", "COSMOS не ответил"),
    ("не-json", "не разобран как JSON"),
    ("чужая-схема", "не по договору"),
])
def test_неответ_среды_это_слова_причины_а_не_исключение(channel_on, beda,
                                                         слово):
    """Отказ, сеть, чужое тело и чужая схема — `(None, слова)`: опрос
    среды не имеет права уронить объявление прогона."""
    def fake_urlopen(req, timeout=None):
        if beda == "http":
            raise urllib.error.HTTPError(req.full_url, 503, "занят", {}, None)
        if beda == "сеть":
            raise OSError("соединение отклонено")
        if beda == "не-json":
            return _resp("<html>не json</html>")
        return _resp(json.dumps({"schema": "чужая-схема/v1", "models": []},
                                ensure_ascii=False))

    env, why = channel.environment(scope="any", _urlopen=fake_urlopen)
    assert env is None and слово in why


# ──────────────────────────── строки моделей COSMOS ──────────────────
def test_строки_моделей_COSMOS_с_системой_и_без_лишних_ключей():
    """Среды нет — пусто и слова «не опрошен»; ответ без моделей — пусто
    и слова «не назвал моделей»; строки — со словом системы `cosmos` и
    ровно полями словаря (слова причины у роли не копируются)."""
    assert rr.cosmos_models(None) == ([], rr.COSMOS_NOT_ASKED_NOTE)
    assert rr.cosmos_models("не словарь") == ([], rr.COSMOS_NOT_ASKED_NOTE)
    assert rr.cosmos_models(_env_answer(models=[])) == \
        ([], rr.COSMOS_SILENT_NOTE)
    # Строка без роли — не строка: роль и есть имя места в раскладке.
    assert rr.cosmos_models(_env_answer(models=[{"role": " "}])) == \
        ([], rr.COSMOS_SILENT_NOTE)
    rows, note = rr.cosmos_models(_env_answer())
    assert note is None and len(rows) == 2
    assert all(r["system"] == "cosmos" for r in rows)
    assert all(set(r) == {"role", "model", "provider", "channel", "effort",
                          "system"} for r in rows)
    assert rows[0]["model"] == "модель-сертификатора"
    assert rows[1]["role"] == "читатель ИДАК" and rows[1]["model"] is None
    # Систему называет панель, а не ответ: чужое слово не проходит.
    чужое, _ = rr.cosmos_models(_env_answer(models=[
        {"role": "сертификатор", "system": "cdpm"}]))
    assert чужое[0]["system"] == "cosmos"


def test_слова_заметки_те_же_что_у_раннера():
    """Голова панели и голова раннера говорят об одном ОДНИМИ словами:
    читатель дерева прогонов разбирает одну формулировку, а не две."""
    assert rr.COSMOS_NOT_ASKED_NOTE == "COSMOS не опрошен при объявлении"
    assert rr.COSMOS_SILENT_NOTE == "COSMOS не назвал моделей при объявлении"


# ──────────────────────────────── голова прогона ─────────────────────
_AGENTS = {"extractor": {"effort": "high",
                         "channels": {"cloud": {"model": "м-а",
                                                "provider": "cloud"}}},
           "auditor": {"channels": {"local": {"model": "м-л",
                                              "provider": "local"}}}}
_ENV = {"code": {"commit": "панель-1234"},
        "catalog": {"version": "0.5.0", "fingerprint": "ca69"},
        "python": "3.14.4", "packages": {"ortools": "9.15"}}


def _head(tmp_path, cosmos_env=None) -> dict:
    return rr.new_declaration(
        tmp_path / "run-0042",
        created_at="2026-09-11T10:00:00+02:00", created_by=rr.PANEL_ACTOR,
        purpose="рабочий",
        airline={"name": "Полярис", "data_class": "synthetic",
                 "llm_scope": "any"},
        rules_documents=[{"name": "ID-1.md", "kind": "md",
                          "sha256": "f" * 64, "size": 10}],
        planned_stages=["create", "package"], stop_after="package",
        expectations={"kind": None, "ref": None}, answer_policy="человек",
        environment=_ENV, models=rr.layout_models(_AGENTS),
        cosmos_environment=cosmos_env)


def test_раскладка_панели_называет_свою_систему():
    """Каждая строка моделей панели — со словом `system: "cdpm"`:
    голова однородна, и рядом с ними встают строки COSMOS."""
    rows = rr.layout_models(_AGENTS)
    assert [(r["role"], r["channel"], r["system"]) for r in rows] == [
        ("auditor", "local", "cdpm"), ("extractor", "cloud", "cdpm")]
    assert rr.layout_models(None) == [] and rr.layout_models("нет") == []


def test_голова_несёт_модели_обеих_систем_и_коммит_COSMOS(tmp_path):
    """Среда COSMOS спрошена: его модели лежат в голове рядом с
    моделями панели, коммит COSMOS — от самого COSMOS, а ключа
    `models_note` НЕТ ВОВСЕ (раскладка полна)."""
    head = _head(tmp_path, _env_answer())
    систем = [m["system"] for m in head["models"]]
    assert систем == ["cdpm", "cdpm", "cosmos", "cosmos"]
    assert [m["role"] for m in head["models"]][2:] == ["сертификатор",
                                                       "читатель ИДАК"]
    assert head["code"] == {"cdpm_commit": "панель-1234",
                            "cosmos_commit": "косм-777",
                            "runner_fingerprint": None}
    assert "models_note" not in head


def test_без_среды_голова_называет_неполноту_словами(tmp_path):
    """COSMOS не спрошен — модели только свои, а неполнота НАЗВАНА:
    молчать о пропавших ролях нельзя. Коммит COSMOS — как прежде,
    коммит панели («одно дерево»)."""
    head = _head(tmp_path)
    assert [m["system"] for m in head["models"]] == ["cdpm", "cdpm"]
    assert head["models_note"] == rr.COSMOS_NOT_ASKED_NOTE
    assert head["code"]["cosmos_commit"] == "панель-1234"
    # Ответ без моделей — другие слова, тоже в голове.
    молчун = _head(tmp_path, _env_answer(models=[]))
    assert молчун["models_note"] == rr.COSMOS_SILENT_NOTE
    assert [m["system"] for m in молчун["models"]] == ["cdpm", "cdpm"]


def test_отпечаток_панели_сходится_с_отпечатком_словаря(tmp_path):
    """ГЛАВНОЕ следствие правила `OMITTED_WHEN_EMPTY`: панель считает
    отпечаток по СЫРОЙ голове, COSMOS — по нормализованной. Панель
    никогда не пишет `models_note: null` и `system: null` — иначе два
    счёта разошлись бы, и всякая запись читалась бы «изменённой после
    старта». Обе головы принимает словарь, и отпечатки их различны."""
    полная = _head(tmp_path, _env_answer())
    одинокая = _head(tmp_path)
    for head in (полная, одинокая):
        assert rr.declaration_sha256(head) == vrr.declaration_sha256(head)
        assert rr.declaration_problems(head) == []
        rec = vrr.new_record(head)
        # Нормализация систему строк сохраняет, а не гасит.
        assert [m.get("system") for m in rec["declaration"]["models"]] == \
            [m["system"] for m in head["models"]]
        assert rec["declaration"].get("models_note") == head.get("models_note")
    assert rr.declaration_sha256(полная) != rr.declaration_sha256(одинокая)
    # Пустых значений в голове не бывает: ни ключа `models_note` без
    # слов, ни строки модели без системы.
    assert all(m.get("system") for m in полная["models"])
    assert "models_note" not in полная


# ───────────────────────── сверка «объявлено / на деле» ──────────────
def test_сверка_судит_только_модели_панели(tmp_path):
    """Голова с моделями COSMOS и совпавшими моделями панели проходит;
    чужая модель ПАНЕЛИ — слово; чужая модель COSMOS — слов нет: её
    судит гейт COSMOS «от пакета», а панель о чужой раскладке ничего не
    знает."""
    head = _head(tmp_path, _env_answer())
    свои = rr.layout_models(_AGENTS)
    env = {"catalog": {}, "code": {}}
    assert rr.environment_mismatch(head, env, {}, свои) == []
    # Чужая модель COSMOS — панель молчит.
    чужой_космос = json.loads(json.dumps(head))
    чужой_космос["models"][2]["model"] = "совсем другая модель"
    assert rr.environment_mismatch(чужой_космос, env, {}, свои) == []
    # Чужая модель ПАНЕЛИ — слово.
    чужая_панель = json.loads(json.dumps(head))
    чужая_панель["models"][0]["model"] = "не та модель"
    слова = rr.environment_mismatch(чужая_панель, env, {}, свои)
    assert len(слова) == 1 and "модель роли auditor" in слова[0]
    # Голова ТОЛЬКО с моделями COSMOS — «модели ролей CDPM не объявлены»:
    # чужие строки своими не считаются.
    без_своих = dict(head, models=[m for m in head["models"]
                                   if m["system"] == "cosmos"])
    слова = rr.environment_mismatch(без_своих, env, {}, свои)
    assert слова == ["модели ролей CDPM не объявлены, а раскладка панели "
                     "их знает"]
    # Записи ДО 11.09.2026 — без слова системы: они читаются как свои.
    старая = dict(head, models=[{k: v for k, v in m.items() if k != "system"}
                                for m in свои])
    assert rr.environment_mismatch(старая, env, {}, свои) == []


# ───────────────────────────── предпросмотр и рождение ───────────────
def _preview(panel, **params):
    p = {"data_class": "synthetic"}
    p.update(params)
    return panel.get("/api/declaration-preview", params=p).json()


def test_предпросмотр_показывает_модели_обеих_систем(panel, monkeypatch):
    """Предпросмотр — та же голова, что ляжет на диск: строки COSMOS со
    своим словом, коммит COSMOS из среды, ключа `models_note` нет, и
    отдельным ключом сказано, что COSMOS спрошен."""
    from cdpm import webui
    видано = {}

    def среда(scope=None, _urlopen=None):
        видано["scope"] = scope
        return _env_answer(), ""
    monkeypatch.setattr(webui.channel_mod, "environment", среда)

    got = _preview(panel)
    d = got["declaration"]
    # Допуск набора едет в COSMOS: им он выбирает сертификатора.
    assert видано["scope"] == "any"
    свои = [m for m in d["models"] if m["system"] == "cdpm"]
    чужие = [m for m in d["models"] if m["system"] == "cosmos"]
    assert свои and [m["role"] for m in чужие] == ["сертификатор",
                                                   "читатель ИДАК"]
    assert all("words" not in m for m in чужие)
    assert d["code"]["cosmos_commit"] == "косм-777"
    assert d["code"]["cdpm_commit"] and d["code"]["cdpm_commit"] != "косм-777"
    assert "models_note" not in d
    assert got["cosmos"] == {"asked": True, "detail": ""}
    assert got["ok"] is True


def test_предпросмотр_без_канала_называет_неполноту_но_не_отказывает(panel):
    """COSMOS недоступен — это НЕ проблема гейта: прогон объявляется,
    слов о COSMOS в `words` нет, а неполнота названа в голове."""
    got = _preview(panel)
    d = got["declaration"]
    assert [m["system"] for m in d["models"]] == ["cdpm"] * len(d["models"])
    assert d["models_note"] == rr.COSMOS_NOT_ASKED_NOTE
    assert got["cosmos"]["asked"] is False
    assert "канал выключен" in got["cosmos"]["detail"]
    assert got["ok"] is True
    assert not [w for w in got["words"] if "COSMOS" in w]


def test_рождение_прогона_кладёт_на_диск_голову_обеих_систем(panel,
                                                             monkeypatch):
    """Прогон из браузера: голова на диске несёт слово системы у своих
    моделей и строки COSMOS, её отпечаток сходится со счётом словаря, а
    при выключенном канале в ней стоят слова о неопрошенном COSMOS."""
    from cdpm import webui

    def _head_on_disk(run_id: str) -> dict:
        return json.loads((panel.runs / run_id / rr.DECLARATION_FILE)
                          .read_text(encoding="utf-8"))

    # Канал выключен (как у всякого теста набора) — голова со словами.
    doc = _head_on_disk(_create(panel).json()["run"])
    head = doc["declaration"]
    assert head["models"] and all(m["system"] == "cdpm"
                                  for m in head["models"])
    assert head["models_note"] == rr.COSMOS_NOT_ASKED_NOTE
    assert doc["declaration_sha256"] == vrr.declaration_sha256(head)
    # COSMOS ответил — его строки в той же голове.
    monkeypatch.setattr(webui.channel_mod, "environment",
                        lambda scope=None, _urlopen=None: (_env_answer(), ""))
    doc = _head_on_disk(_create(panel).json()["run"])
    head = doc["declaration"]
    assert [m["system"] for m in head["models"]][-2:] == ["cosmos", "cosmos"]
    assert all(m["system"] == "cdpm" for m in head["models"][:-2])
    assert head["code"]["cosmos_commit"] == "косм-777"
    assert "models_note" not in head
    assert doc["declaration_sha256"] == vrr.declaration_sha256(head)
    assert vrr.new_record(head)["declaration_sha256"] == \
        doc["declaration_sha256"]


# ──────────── голова от хозяина: среду COSMOS не спрашивают (волна 16) ─
def _owner_head(env: dict, items: list, rid: str) -> dict:
    """Голова ЦЕЛИКОМ по образцу раннера наборов (вариант «б»):
    объявимую часть среды хозяин берёт из предпросмотра, и модели обеих
    систем в ней уже стоят."""
    return {
        "run_id": rid, "created_at": "2026-09-11T12:15:00+02:00",
        "created_by": "раннер наборов H0", "purpose": "регресс",
        "contour": {"name": env["contour"]["name"],
                    "services": env["contour"]["services"]},
        "airline": {"name": "Полярис-H0", "data_class": "synthetic",
                    "llm_scope": "any"},
        "rules_documents": rr.rules_documents_of(items),
        "data_files": [], "answers": [],
        "catalog": env["catalog"], "catalog_extension": None,
        "bindings": env["bindings"], "models": env["models"],
        "solvers": [], "code": env["code"], "libraries": env["libraries"],
        "planned_stages": list(rr.PANEL_STAGES), "stop_after": "package",
        "expectations": {"kind": None, "ref": None},
        "lineage": {"root_sha256": "9" * 64, "root_answers_sha256": None,
                    "parent_run": None, "branch_of": None,
                    "package_sha256": None, "source_run": None},
        "answer_policy": "книга",
    }


def test_голова_хозяина_не_тянет_за_собой_опроса_среды(panel, monkeypatch):
    """Волна 16: голову объявил хозяин ЦЕЛИКОМ (поле формы
    `declaration`) — его модели COSMOS уже в ней, панель кладёт голову
    КАК ЕСТЬ и среду НЕ спрашивает ВОВСЕ: ни одного вызова
    `channel.environment`. Спрашивать не за кого, а при недоступном
    COSMOS это были бы 5 с ожидания у кнопки хозяина на ровном месте.
    Предпросмотр и рождение из браузера — как прежде: ровно один опрос
    на запрос."""
    from cdpm import webui
    from test_cdpm_extract import _RULES_A, _RULES_B
    счёт = {"n": 0}

    def среда(scope=None, _urlopen=None):
        счёт["n"] += 1
        return _env_answer(), ""
    monkeypatch.setattr(webui.channel_mod, "environment", среда)

    # Объявимую часть среды хозяин берёт у предпросмотра — тот
    # спрашивает COSMOS, как и прежде.
    env = _preview(panel)["declaration"]
    assert счёт["n"] == 1
    items = [("RULES-A.md", _RULES_A.encode("utf-8")),
             ("RULES-B.md", _RULES_B.encode("utf-8"))]
    head = _owner_head(env, items, "20260911-121500-9c6f40ad")

    счёт["n"] = 0
    got = panel.post("/api/runs", files=[
        ("files", (name, body, "text/markdown")) for name, body in items
    ], params={"data_class": "synthetic", "customer": "Полярис-H0"},
        data={"record_run_id": head["run_id"],
              "created_by": head["created_by"],
              "declaration": json.dumps(head, ensure_ascii=False,
                                        sort_keys=True),
              "declaration_sha256": rr.declaration_sha256(head)})
    assert got.status_code == 200, got.text
    assert счёт["n"] == 0
    # Голова на диске — та самая, с моделями обеих систем ОТ ХОЗЯИНА.
    doc = json.loads((panel.runs / got.json()["run"] / rr.DECLARATION_FILE)
                     .read_text(encoding="utf-8"))
    assert doc["declaration"] == head
    assert [m["system"] for m in doc["declaration"]["models"]][-2:] == \
        ["cosmos", "cosmos"]

    # Рождение из браузера (головы нет — её чеканит панель): опрос один.
    got = _create(panel)
    assert got.status_code == 200, got.text
    assert счёт["n"] == 1


def test_непрошенная_среда_называет_причину_словами(panel, monkeypatch):
    """Слова причины при `ask_cosmos=False` — не «канал выключен», а
    «голову объявил хозяин целиком»: читатель отличает молчание COSMOS
    от вопроса, которого не задавали. Отказ головы (отпечаток не сошёлся)
    среды тоже не спрашивает: гейт судит ДО рождения папки."""
    from cdpm import webui
    счёт = {"n": 0}

    def среда(scope=None, _urlopen=None):        # pragma: no cover
        счёт["n"] += 1
        return _env_answer(), ""
    monkeypatch.setattr(webui.channel_mod, "environment", среда)

    ks = {"effective": "any"}
    decl = webui._declaration_preflight({}, ks, ask_cosmos=False)
    assert decl["cosmos_environment"] is None and счёт["n"] == 0
    assert decl["cosmos_environment_detail"] == webui.COSMOS_NOT_ASKED_OWNER
    assert "хозяин" in decl["cosmos_environment_detail"]
    # Голова прислана и не сошлась отпечатком — 400 и по-прежнему ни
    # одного опроса: папки прогона нет, номер не расходуется.
    from test_cdpm_extract import _RULES_A
    было = sorted(p.name for p in panel.runs.glob("run-*")) \
        if panel.runs.exists() else []
    got = panel.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
    ], params={"data_class": "synthetic", "customer": "Полярис-H0"},
        data={"declaration": json.dumps({"run_id": "нет"}),
              "declaration_sha256": "0" * 64})
    assert got.status_code == 400 and счёт["n"] == 0
    assert (sorted(p.name for p in panel.runs.glob("run-*"))
            if panel.runs.exists() else []) == было
