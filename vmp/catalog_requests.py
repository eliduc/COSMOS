# -*- coding: utf-8 -*-
"""Заявки каталога шаблонов со стороны продукта COSMOS (шаг 6, вход «пробел
при нажатии»).

Кнопка видит пробел покрытия ДО расчёта (отчёт покрытия решателя Ш-1п,
вердикты реестра Ш-1р/Ш-2м, глубокая сверка Ш-1п2, отказ рубежа выпуска
пакета `support-missing` на Ш-1). Пробел бывает двух разных природ, и
смешивать их нельзя:

  (а) пробел ОТНОСИТЕЛЬНО ЗАПИСИ — семья или критерий в каталоге есть,
      нужный вид исполнителя записью объявлен, но исполнителя нет
      («заявлен» либо «нет» в привязках). Такую заявку COSMOS подаёт
      сам: в ней нет ни одного слова авиакомпании — раздел, запись,
      набор видов и имя прогона;
  (б) пробел БЕЗ ЗАПИСИ — правила не на что сажать (посадка категории C,
      неизвестная семья, неизвестный критерий). Черновик записи COSMOS
      НЕ составляет: обобщение механики — работа составителя CDPM на
      документах прогона либо человека на экране службы. COSMOS считает
      номер заявки по общему закону и говорит словами, заявлен ли уже
      этот пробел и кем; иначе даёт ссылку на ручную подачу.

Предмет пробела — обычно ПРАВИЛО (`classify_gaps`), но у отказа рубежа
выпуска пакета правила нет: он называет сами записи («families/<id>»), и
их классифицирует `classify_records` тем же законом и той же формой
строки. Природы (а) и (б) от этого не меняются.

Отсюда закон модуля: пишет он ТОЛЬКО очередь продукта
(`<data_root>/cosmos-catalog-requests/outbox`, ключ `CREW_CATALOG_OUTBOX`)
и квитанцию нажатия; в `catalog/` не пишет ничего и никогда — разделы
каталога пишет только служба. Доставка в службу — по HTTP машинным
каналом (заголовки продукта и токена); служба не поднята или адрес не
задан — заявка остаётся в очереди, и это сказано словами, а не молча.

Правило «без данных заказчика» (P-N-SHD) исполняется ФОРМОЙ файла: в
записи заявки нет ни формулировки правила, ни его идентификатора, ни
источников — только раздел, запись, виды и `run-NNNN#<12 знаков ключа>`.
Ключ заявки на вид считается от записи и набора видов, поэтому его хвост
не раскрывает ничего о тексте правила. Сторож этого — тест
`tests/vmp/test_catalog_requests.py`.

Закон ключа и номера — один на два продукта и три машины
(`template_catalog.requests`): служба может быть не поднята, а номера
обязаны сойтись без обмена.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from template_catalog import requests as law

#: Имя продукта-подателя в записи заявки и в машинном заголовке канала
#: (значение из словаря `products` каталога).
PRODUCT = "COSMOS"
#: Вид источника заявки этого входа (словарь `request_source_kinds`).
SOURCE_KIND = "пробел при нажатии"
#: Класс прогона-источника (словарь свободный, слова — контракт §1.2).
RUN_CLASS = "прогон COSMOS"
#: Состояние вновь поданной заявки (словарь `request_states`).
STATE_SUBMITTED = "подана"
#: Состояния, при которых заявка по записи считается ОТКРЫТОЙ: пока она
#: открыта, вторая заявка по той же записи не подаётся ни под каким
#: набором видов — иначе очередь владельца забивалась бы дублями одного
#: пробела из каждого нажатия.
OPEN_STATES = ("подана", "принята", "исполнитель заявлен")
#: Состояние клетки привязок, означающее живого исполнителя.
STATE_PRESENT = "есть"

ENV_API_URL = "CREW_CATALOG_API_URL"
ENV_TOKEN = "CREW_CATALOG_TOKEN"
ENV_UI_URL = "CREW_CATALOG_URL"
ENV_OUTBOX = "CREW_CATALOG_OUTBOX"
ENV_AUTO = "CREW_CATALOG_REQUESTS_AUTO"

#: Адрес службы на рабочем месте владельца — УМОЛЧАНИЕ ЧЕК-ЛИСТА раската
#: (Test 8106, Stage 8107), а не умолчание кода: незаданная переменная
#: значит «машинного канала здесь нет» (площадка, машина заказчика), и
#: подставлять за неё локальный адрес — значит стучаться в чужой порт при
#: каждом нажатии. Fail-closed, как у канала пакета.
DEFAULT_API_URL = "http://127.0.0.1:8105"

#: Имя файла квитанции в папке артефактов нажатия.
RECEIPT_NAME = "catalog-requests.json"

# ───────────────────────────────────────────────────── формы пробелов

#: Заявка на вид исполнителя у существующей записи — подаётся COSMOS.
FORM_KIND = "kind"
#: Новая запись каталога — заявку составляет CDPM либо человек.
FORM_NEW = "new"
#: Правка записи (вид записью не объявлен) — экран службы, старшая версия.
FORM_EDIT = "edit"
#: Исполнители записи все на месте — пробел не в каталоге, а в коде.
FORM_DEFECT = "defect"
#: Заявка не подаётся: брак свода, брак пакета либо среда.
FORM_NONE = "none"

#: Вид пробела, которым кнопка помечает строки предпрогонного отчёта
#: покрытия решателя: у них своего вида нет, есть причина словами.
GAP_COVERAGE = "coverage"

#: Приставка, которой глубокая сверка помечает пробел О ПРАВИЛЕ: вердикт
#: реестра едет в вид пробела как «rule_<вердикт>»
#: (`registry_check._registry_gap`). Перечень вердиктов ОТКРЫТ — реестр
#: файл, и вердикт в нём может быть любым словом, — поэтому судим по
#: приставке, а не по перечню; исключения названы в NOT_CATALOG_GAPS.
RULE_PREFIX = "rule_"
#: Пробелы, судимые по ПРАВИЛУ и приходящие БЕЗ приставки: отчёт
#: покрытия решателя (свой вид) и вердикты вывода реестра, когда их
#: читают со стороны матрицы готовности, а не глубокой сверки.
RULE_GAPS = (GAP_COVERAGE, "family_unknown", "param_unknown", "no_mechanic")
#: Пробелы, судимые по КРИТЕРИЮ задачи.
CRITERION_GAPS = ("criterion_unknown", "criterion_no_executor")
#: Пробелы, которые заявкой каталога не закрываются: брак свода (две
#: правды о семье, кривая посадка), устаревшая пометка реестра, ярусы
#: критериев, дрейф кода и среда — их лечат в своде, в реестре, в коде
#: и на площадке, а не решением владельца о записи каталога.
NOT_CATALOG_GAPS = ("family_conflict", "rule_family_conflict",
                    "rule_fit_conflict", "fit_invalid",
                    "rule_excluded_stale", "criteria_tiers_unsupported",
                    "drift", "catalog_unavailable", "no_registry",
                    "registry_malformed")

#: Вид исполнителя, которым COSMOS считает целевую пейринга: запись
#: критерия без него в `computable_by` требует ПРАВКИ записи, а не заявки
#: на вид (заявка на вид — только из объявленных, правило R02 читателя).
CRITERION_KIND = "builder_method"


class CatalogRequestError(RuntimeError):
    """Отказ подачи заявки словами (очередь, форма записи, среда)."""


# ─────────────────────────────────────────────────────── настройки среды

def outbox_root() -> Path:
    """Корень очереди заявок продукта: `CREW_CATALOG_OUTBOX`, иначе —
    рядом с прочими данными расчётов (`<корень данных>/
    cosmos-catalog-requests/outbox`). Очередь живёт ВНЕ репозитория, как
    и все артефакты: заявка, рождённая на площадке, переносится на
    рабочее место владельца копией папки, а не коммитом."""
    env = (os.environ.get(ENV_OUTBOX) or "").strip()
    if env:
        return Path(env) / "outbox"
    import _data_paths
    return _data_paths.data_root() / "cosmos-catalog-requests" / "outbox"


def auto_enabled() -> bool:
    """Подавать ли заявки на вид АВТОМАТИЧЕСКИ при остановке нажатия.
    «0» — только кнопкой «Заявить в каталог»; переменная не задана —
    автоматически (пробел, увиденный кнопкой, иначе теряется вместе с
    вкладкой браузера)."""
    return (os.environ.get(ENV_AUTO) or "").strip() != "0"


def configured() -> dict:
    """Настройки машинного канала: адрес службы, токен, адрес для ссылок.
    Канал считается настроенным при АДРЕСЕ И ТОКЕНЕ вместе (тот же закон,
    что у CDPM `service_configured`): без токена служба всё равно ответит
    403 «машинный канал выключен»."""
    api = (os.environ.get(ENV_API_URL) or "").strip()
    token = (os.environ.get(ENV_TOKEN) or "").strip()
    return {"api_url": api or None, "token": token or None,
            "ui_url": (os.environ.get(ENV_UI_URL) or "").strip() or None,
            "on": bool(api and token)}


def service_words_off() -> str:
    """Почему доставка не состоится (для квитанции и экрана)."""
    cfg = configured()
    if not cfg["api_url"]:
        return ("адрес службы каталога не задан (%s): заявка осталась в "
                "очереди продукта" % ENV_API_URL)
    return ("токен машинного канала не задан (%s): заявка осталась в "
            "очереди продукта" % ENV_TOKEN)


# ──────────────────────────────────────────────── клиент службы каталога

class service_client:
    """Машинный канал службы каталога — ТОЛЬКО стандартная библиотека.

    Имя строчными буквами — контракт проекта шага 6 (§5.4): вызовы
    выглядят как `service_client.submit(doc)` и у продукта, и у очереди
    изменений. Экземпляра у клиента нет: настройки читаются из среды при
    каждом вызове, потому что переменные контура меняются перезапуском
    службы, а не жизнью объекта.

    Повтор — только там, где он честен: сеть и «служба занята записью»
    (409 `write-busy`) — три попытки; прочие отказы 4xx повторять
    бессмысленно, они о содержании заявки.
    """

    TIMEOUT_S = 10.0
    RETRIES = 3
    RETRY_PAUSE_S = 0.5

    # ---------------------------------------------------------- нижний слой
    @classmethod
    def _call(cls, method: str, path: str, body=None) -> dict:
        """Один вызов: {"status": int|None, "doc": dict|None,
        "error": str|None}. Исключений наружу не отдаёт — отказ службы
        это РЕЗУЛЬТАТ вызова, а не авария нажатия."""
        import urllib.error
        import urllib.request
        cfg = configured()
        if not cfg["on"]:
            return {"status": None, "doc": None,
                    "error": service_words_off()}
        url = cfg["api_url"].rstrip("/") + path
        data = None if body is None else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Catalog-Product", PRODUCT)
        req.add_header("X-Catalog-Token", cfg["token"])
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=cls.TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8") or "null"
                return {"status": int(resp.status), "error": None,
                        "doc": json.loads(raw)}
        except urllib.error.HTTPError as e:                  # noqa: PERF203
            try:
                doc = json.loads(e.read().decode("utf-8") or "null")
            except (ValueError, OSError):
                doc = None
            return {"status": int(e.code), "doc": doc, "error": None}
        except (urllib.error.URLError, OSError, ValueError,
                TimeoutError) as e:
            return {"status": None, "doc": None,
                    "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    @staticmethod
    def _detail(res: dict) -> dict:
        """Отказ службы: `{"detail": {"code", "words", "problems", "extra"}}`
        (контракт §8 п.1). Форма нарушена — слова об этом, не молчание."""
        doc = res.get("doc")
        det = (doc or {}).get("detail") if isinstance(doc, dict) else None
        if isinstance(det, dict):
            return det
        return {"code": None, "words": None, "problems": [], "extra": {}}

    @classmethod
    def _words_of(cls, res: dict, default: str) -> str:
        det = cls._detail(res)
        words = det.get("words") or default
        problems = [str((p or {}).get("message") or p)
                    for p in (det.get("problems") or ())]
        return words + ("; " + "; ".join(problems[:3]) if problems else "")

    @classmethod
    def _retryable(cls, res: dict) -> bool:
        """Повторяем сеть, «служба занята записью» и отказы самой машины
        (5xx): содержание заявки от повтора не изменится, а замок файла и
        разорванное соединение — изменятся."""
        if res.get("error") is not None:
            return True
        status = res.get("status")
        if status == 409:
            return cls._detail(res).get("code") == "write-busy"
        return isinstance(status, int) and status >= 500

    @classmethod
    def _call_retrying(cls, method: str, path: str, body=None,
                       *, sleep=time.sleep) -> dict:
        if not configured()["on"]:
            # Канал выключен — это не сеть: повторять нечего, и три
            # паузы только задержали бы нажатие.
            return cls._call(method, path, body)
        res = {"status": None, "doc": None, "error": "вызов не состоялся"}
        for attempt in range(cls.RETRIES):
            res = cls._call(method, path, body)
            if not cls._retryable(res):
                return res
            if attempt + 1 < cls.RETRIES:
                sleep(cls.RETRY_PAUSE_S)
        return res

    # ---------------------------------------------------------- ручки
    @classmethod
    def session(cls) -> dict | None:
        """Кто мы для службы и в какой каталог она смотрит: `{"mode":
        "edit"|"view", "fingerprint": "<64 hex>"}`. Служба не настроена
        либо не отвечает — None (экран скажет «служба недоступна»)."""
        res = cls._call("GET", "/api/session")
        doc = res.get("doc")
        if res.get("status") != 200 or not isinstance(doc, dict):
            return None
        catalog = doc.get("catalog") if isinstance(doc.get("catalog"),
                                                   dict) else {}
        return {"mode": doc.get("mode"),
                "fingerprint": catalog.get("fingerprint")}

    @classmethod
    def submit(cls, doc: dict, *, sleep=time.sleep) -> dict:
        """Подача заявки машинным каналом. Возврат:
        `{"ok", "state", "words", "request_id"}` — 201 «принята», 200
        «уже подана» (идемпотентность закона ключа), прочее — отказ
        словами службы."""
        rid = str((doc or {}).get("request_id") or "")
        res = cls._call_retrying("POST", "/api/requests", doc, sleep=sleep)
        status, body = res.get("status"), res.get("doc")
        body = body if isinstance(body, dict) else {}
        if status in (200, 201):
            state = str(body.get("state") or STATE_SUBMITTED)
            note = body.get("note")
            return {"ok": True, "state": state, "request_id":
                    str(body.get("request_id") or rid),
                    "words": ("заявка %s: %s%s"
                              % (rid, state,
                                 " — %s" % note if note else ""))}
        if res.get("error") is not None:
            return {"ok": False, "state": None, "request_id": rid,
                    "words": (res["error"] if not configured()["on"]
                              else "служба каталога недоступна: %s"
                                   % res["error"])}
        return {"ok": False, "state": None, "request_id": rid,
                "words": cls._words_of(
                    res, "служба отказала кодом %s" % status)}

    @classmethod
    def put_cr_key(cls, request_id: str, cr_key: str,
                   *, sleep=time.sleep) -> dict:
        """Запись ключа CR в заявку («принята» → «исполнитель заявлен»).
        Ключ считается по закону, служба его пересчитывает и судит."""
        rid = str(request_id)
        res = cls._call_retrying("PUT", "/api/requests/%s/cr_key" % rid,
                                 {"cr_key": str(cr_key)}, sleep=sleep)
        status, body = res.get("status"), res.get("doc")
        body = body if isinstance(body, dict) else {}
        if status in (200, 201):
            return {"ok": True, "state": str(body.get("state")
                                             or "исполнитель заявлен"),
                    "words": "ключ CR %s записан в заявку %s"
                             % (cr_key, rid)}
        if res.get("error") is not None:
            return {"ok": False, "state": None,
                    "words": "служба каталога недоступна: %s" % res["error"]}
        return {"ok": False, "state": None,
                "words": cls._words_of(
                    res, "служба отказала кодом %s" % status)}

    @classmethod
    def get(cls, request_id: str) -> dict | None:
        """Запись заявки из службы (None — нет либо служба недоступна)."""
        res = cls._call("GET", "/api/requests/%s" % str(request_id))
        doc = res.get("doc")
        return doc if res.get("status") == 200 and isinstance(doc, dict) \
            else None


# ──────────────────────────────────────────────────────── классификатор

def _best_state(cell) -> str | None:
    """Лучшее состояние клетки «запись × вид» по всем продуктам:
    «есть» сильнее «заявлен», «заявлен» сильнее «нет». Клетки нет —
    None (ни один продукт о виде не сказал)."""
    if not isinstance(cell, dict) or not cell:
        return None
    rank = {"есть": 0, "заявлен": 1, "нет": 2}
    return min((str(s) for s in cell.values()),
               key=lambda s: rank.get(s, 9))


def _kinds_without_executor(section: str, record: dict, states: dict,
                            declared: list) -> list:
    """Объявленные записью виды, у которых исполнителя нет ни у одного
    продукта. Порядок — как объявлен записью (набор в ключе сортируется
    законом, а словам человека привычнее порядок записи)."""
    cells = ((states or {}).get(section) or {}).get(str(record.get("id"))) \
        or {}
    return [k for k in declared
            if _best_state(cells.get(k)) != STATE_PRESENT]


def _open_request_of(cat, section: str, record_id: str) -> dict | None:
    """Открытая заявка по записи — ЛЮБОГО подателя и любой формы.
    Судится ПЕРВОЙ, до расчёта ключа: пока владелец решает по одной
    заявке о записи, вторая о ней же — дубль, а не новый пробел."""
    for rq in cat.requests() or ():
        if not isinstance(rq, dict):
            continue
        if str(rq.get("section")) != section \
                or str(rq.get("record_id")) != str(record_id):
            continue
        if str(rq.get("state")) in OPEN_STATES:
            return rq
    return None


def _existing(rq: dict | None) -> dict | None:
    if not isinstance(rq, dict):
        return None
    return {"request_id": rq.get("request_id"), "state": rq.get("state"),
            "submitted_by": rq.get("submitted_by"),
            "kinds": list(rq.get("executor_kinds_requested") or [])}


def _already_words(ex: dict, address: str) -> str:
    kinds = ", ".join(ex.get("kinds") or ()) or "новая запись"
    return ("пробел уже заявлен: %s (состояние «%s», подал %s; виды %s) — "
            "запись %s" % (ex.get("request_id"), ex.get("state"),
                           ex.get("submitted_by"), kinds, address))


def _row(form: str, *, rule_id: str = "", section=None, record=None,
         kinds=None, key=None, request_id=None, state=None,
         existing=None, words: str = "", link=None) -> dict:
    return {"rule_id": rule_id, "form": form, "section": section,
            "record": record, "kinds": list(kinds or []), "key": key,
            "request_id": request_id, "state": state, "existing": existing,
            "words": words, "link": link}


def family_of(rule: dict, cat) -> str | None:
    """Семья записи, на которую посажено правило: посадка A/B первична
    (`template_fit.family`), запас — `export_solver.family` и словарь
    параметров. Порядок тот же, что у вывода реестра (`registry_derive.
    derive_rule`): иначе классификатор и реестр называли бы разные
    семьи одного правила.

    Словарь параметров берётся ИЗ ТОГО ЖЕ экземпляра каталога
    (`cat.params()`), что и записи: проекции обёртки читают каталог
    ДВЕРИ, и на временном каталоге гейта они назвали бы чужую семью."""
    from vmp import registry_derive as _rd
    if not isinstance(rule, dict):
        return None
    fam = _rd.fit_family(rule)
    if fam:
        return fam
    legacy = (rule.get("export_solver") or {}).get("family")
    if legacy:
        return str(legacy)
    names = [str(p.get("name")) for p in rule.get("params") or ()
             if isinstance(p, dict) and p.get("name")]
    if not names:
        return None
    params = cat.params()
    for name in names:
        fam = (params.get(name) or {}).get("family")
        if fam:
            return str(fam)
    return None


def _record_or_none(cat, section: str, record_id: str) -> dict | None:
    """Запись каталога со статусом «есть»; снятая запись — всё равно что
    её нет (её виды никого не исполняют)."""
    from template_catalog.errors import UnknownRecord
    try:
        rec = cat.family(record_id) if section == "families" \
            else cat.criterion(record_id)
    except UnknownRecord:
        return None
    return rec if str((rec or {}).get("status")) == "есть" else None


def new_record_link(section: str, key: str, *, run_name=None) -> str:
    """Ссылка ручной подачи на экране службы: раздел, вид источника,
    класс прогона, ссылка на прогон и КЛЮЧ — служба нумерует заявку тем
    же законом, и номер сойдётся с тем, что назвала кнопка."""
    from urllib.parse import quote
    parts = ["section=" + quote(section),
             "source=" + quote(SOURCE_KIND),
             "run_class=" + quote(RUN_CLASS)]
    if run_name:
        parts.append("ref=" + quote(law.ref_of(str(run_name), key), safe=""))
    parts.append("key=" + key)
    return "#/requests/new?" + "&".join(parts)


def classify(gap: dict, rule: dict | None, cat, states: dict,
             *, run_name: str | None = None, task: str | None = None) -> dict:
    """Что COSMOS понимает по одному пробелу и что с ним делает.

    `gap` — строка пробела (вид, правило, слова) от реестра, глубокой
    сверки либо отчёта покрытия; `rule` — машинная форма ПРЕДМЕТА
    пробела из модели пакета (правило либо критерий; None — предмета в
    модели нет); `cat` — каталог двери; `states` — таблица состояний
    клеток `catalog_access.bindings_states(cat)` (считается вызывающей
    стороной один раз на нажатие). Возврат — строка формы §1.2 проекта:
    форма, адрес записи, виды, ключ и номер заявки, открытая заявка,
    слова и ссылка. Ничего не пишет и в сеть не ходит."""
    kind = str((gap or {}).get("kind") or "")
    rule_id = str((gap or {}).get("rule_id") or "")
    note = str((gap or {}).get("note") or (gap or {}).get("reason") or "")
    if kind in NOT_CATALOG_GAPS:
        return _row(FORM_NONE, rule_id=rule_id, words=note or kind)
    if kind in CRITERION_GAPS:
        return _criterion_row(gap, rule, cat, states, rule_id=rule_id,
                              run_name=run_name, task=task, note=note)
    if kind in RULE_GAPS or kind.startswith(RULE_PREFIX) or not kind:
        return _rule_row(gap, rule, cat, states, rule_id=rule_id,
                         run_name=run_name, note=note)
    return _row(FORM_NONE, rule_id=rule_id,
                words="вид пробела %s классификатору неизвестен: "
                      "заявка не подаётся" % kind)


#: Разделы каталога, у записей которых бывают виды исполнителей, и поле
#: записи, которым раздел объявляет виды. Одно место на весь модуль:
#: прежде поле писалось у каждого вызова, и третий вызов (пробел по
#: ЗАПИСИ) стал бы третьей копией закона.
RECORD_SECTIONS = {"families": "executor_kinds", "criteria": "computable_by"}


def _declared_of(section: str, record: dict) -> list:
    """Виды, объявленные записью раздела: семья объявляет их полем
    `executor_kinds`, критерий — полем `computable_by`."""
    field = RECORD_SECTIONS.get(str(section))
    return list((record or {}).get(field) or []) if field else []


def _kind_form_row(cat, states, *, section: str, record: dict,
                   declared: list, rule_id: str, note: str) -> dict:
    """Общая часть обеих записей: заявка на вид исполнителя либо честный
    отказ подавать её (все виды на месте — дефект кода; видов запись не
    объявила — правка записи)."""
    rid = str(record.get("id"))
    address = "%s/%s" % (section, rid)
    ex = _existing(_open_request_of(cat, section, rid))
    if ex is not None:
        # Судится ПЕРВОЙ, до расчёта ключа: номер уже есть, и он чужой.
        return _row(FORM_KIND, rule_id=rule_id, section=section, record=rid,
                    kinds=ex.get("kinds"), request_id=ex.get("request_id"),
                    state=ex.get("state"), existing=ex,
                    words=_already_words(ex, address),
                    link="#/requests/%s" % ex.get("request_id"))
    if not declared:
        return _row(FORM_EDIT, rule_id=rule_id, section=section, record=rid,
                    words="запись %s не объявила ни одного вида "
                          "исполнителя — заявка на вид невозможна "
                          "(объявленные виды правит владелец на экране "
                          "службы каталога)" % address,
                    link="#/records/%s/%s#executors" % (section, rid))
    kinds = _kinds_without_executor(section, record, states, declared)
    if not kinds:
        return _row(FORM_DEFECT, rule_id=rule_id, section=section,
                    record=rid, kinds=declared,
                    words="все виды записи %s уже «есть» — пробел не в "
                          "каталоге, а в коде исполнителя: доклад "
                          "владельцу (заявка каталога не подаётся)%s"
                          % (address, "; %s" % note if note else ""))
    key = law.kind_request_key(section, rid, kinds)
    return _row(FORM_KIND, rule_id=rule_id, section=section, record=rid,
                kinds=kinds, key=key, request_id=law.request_id_of(key),
                words="заявка на вид исполнителя %s для %s: %s"
                      % (", ".join(kinds), address,
                         law.request_id_of(key)),
                link="#/requests/%s" % law.request_id_of(key))


def _rule_row(gap, rule, cat, states, *, rule_id: str, run_name, note: str):
    fam = family_of(rule, cat) if isinstance(rule, dict) else None
    record = _record_or_none(cat, "families", fam) if fam else None
    if record is not None:
        return _kind_form_row(cat, states, section="families",
                              record=record,
                              declared=_declared_of("families", record),
                              rule_id=rule_id, note=note)
    # Записи нет: COSMOS черновика не составляет — считает номер по
    # закону и говорит, заявлен ли уже этот пробел.
    if not isinstance(rule, dict) or not str(rule.get("statement") or ""):
        return _row(FORM_NONE, rule_id=rule_id,
                    words="пробел без записи каталога, а формулировки "
                          "правила в модели пакета нет — номер заявки не "
                          "считается: разберитесь со сводом (брак "
                          "согласованности отчёта покрытия и ВМП)%s"
                          % ("; %s" % note if note else ""))
    key = law.request_key("families", str(rule.get("kind") or ""),
                          str(rule.get("zone") or ""),
                          str(rule.get("statement") or ""))
    return _new_record_row(cat, "families", key, rule_id=rule_id,
                           run_name=run_name)


def _criterion_row(gap, criterion, cat, states, *, rule_id: str, run_name,
                   task, note: str):
    # Вид критерия — он же имя записи раздела `criteria` (реестр кладёт
    # вид в поле rule_id пробела).
    cid = str((criterion or {}).get("kind") or rule_id or "")
    record = _record_or_none(cat, "criteria", cid) if cid else None
    if record is not None:
        declared = _declared_of("criteria", record)
        if CRITERION_KIND not in declared:
            return _row(FORM_EDIT, rule_id=rule_id, section="criteria",
                        record=cid, kinds=declared,
                        words="вид %s записью criteria/%s не объявлен — "
                              "правка записи на экране службы каталога"
                              % (CRITERION_KIND, cid),
                        link="#/records/criteria/%s#executors" % cid)
        return _kind_form_row(cat, states, section="criteria",
                              record=record, declared=declared,
                              rule_id=rule_id, note=note)
    if not cid:
        return _row(FORM_NONE, rule_id=rule_id,
                    words="вид критерия не назван — номер заявки не "
                          "считается%s" % ("; %s" % note if note else ""))
    # Зона новой записи критерия — ЗАДАЧА нажатия: у критерия, которого
    # в каталоге нет, другой зоны взять неоткуда.
    zone = str(task or "").upper() or "?"
    statement = str((criterion or {}).get("statement")
                    or (criterion or {}).get("title")
                    or (gap or {}).get("title") or cid)
    key = law.request_key("criteria", cid, zone, statement)
    return _new_record_row(cat, "criteria", key, rule_id=rule_id,
                           run_name=run_name)


def _new_record_row(cat, section: str, key: str, *, rule_id: str, run_name):
    """Пробел БЕЗ записи: номер считается, черновик — нет."""
    rid = law.request_id_of(key)
    rq = cat.request(rid)
    if isinstance(rq, dict):
        ex = _existing(rq)
        return _row(FORM_NEW, rule_id=rule_id, section=section, key=key,
                    record=rq.get("record_id"), request_id=rid,
                    state=rq.get("state"), existing=ex,
                    words="пробел уже заявлен из прогона %s: %s (состояние "
                          "«%s»)" % (rq.get("submitted_by"), rid,
                                     rq.get("state")),
                    link="#/requests/%s" % rid)
    return _row(FORM_NEW, rule_id=rule_id, section=section, key=key,
                request_id=rid,
                words="заявка на новую запись раздела %s подаётся из CDPM "
                      "(посадка категории C) либо вручную по ссылке: %s"
                      % (section, rid),
                link=new_record_link(section, key, run_name=run_name))


def classify_gaps(gaps, *, model=None, cat=None, states=None, criterion=None,
                  run_name=None, task=None) -> list:
    """Классификация перечня пробелов одним экземпляром каталога.

    Один вызов — один экземпляр каталога и одна таблица состояний
    (правило шага 2: операция читает каталог один раз). `model` —
    модель пакета, из неё берётся машинная форма правила по его
    идентификатору; `criterion` — критерий задачи (предмет пробелов
    раздела `criteria`)."""
    from vmp import catalog_access as _ca
    cat = cat if cat is not None else _ca.current()
    states = states if states is not None else _ca.bindings_states(cat)
    by_id = {str(r.get("id")): r for r in ((model or {}).get("rules") or ())
             if isinstance(r, dict)}
    out = []
    for gap in gaps or ():
        if not isinstance(gap, dict):
            continue
        subject = criterion if str(gap.get("kind") or "") in CRITERION_GAPS \
            else by_id.get(str(gap.get("rule_id") or ""))
        out.append(classify(gap, subject, cat, states,
                            run_name=run_name, task=task))
    return out


def classify_records(records, *, cat=None, states=None, note: str = "") -> list:
    """Классификация пробелов, названных ЗАПИСЬЮ каталога, а не правилом.

    Вход — адреса точных записей вида «<раздел>/<id>»: так называет
    предмет отказа рубеж выпуска пакета производителя (код
    `support-missing`, контракты П0 §12.11 п. 3). Правила-предмета у
    такого пробела нет — заявка на вид считается по самой записи ТЕМ ЖЕ
    `_kind_form_row`, что и пробел по правилу, и подаётся той же
    `submit_kind_requests`: второго места, где рождаются заявки, не
    появляется. Ключом строки (он же ключ строки квитанции нажатия)
    служит АДРЕС ЗАПИСИ: `rule_id` у отказа рубежа взять неоткуда, а
    молча склеивать записи в одну строку нельзя.

    Адрес не по образцу и запись, которой в каталоге нет (или она
    снята), заявкой не становятся — они едут строкой «не каталог» со
    словами: пропустить их молча значит потерять пробел."""
    from vmp import catalog_access as _ca
    cat = cat if cat is not None else _ca.current()
    states = states if states is not None else _ca.bindings_states(cat)
    out = []
    for item in records or ():
        address = str(item)
        section, _, rid = address.partition("/")
        if not rid or section not in RECORD_SECTIONS:
            out.append(_row(FORM_NONE, rule_id=address,
                            words="предмет отказа «%s» назван не по образцу "
                                  "«<раздел>/<запись>» (разделы: %s) — заявка "
                                  "на вид не строится%s"
                                  % (address, ", ".join(RECORD_SECTIONS),
                                     "; %s" % note if note else "")))
            continue
        record = _record_or_none(cat, section, rid)
        if record is None:
            out.append(_row(FORM_NONE, rule_id=address, section=section,
                            record=rid,
                            words="записи %s в каталоге нет либо она снята — "
                                  "заявка на вид не строится: разберитесь с "
                                  "каталогом прогона%s"
                                  % (address, "; %s" % note if note else "")))
            continue
        out.append(_kind_form_row(cat, states, section=section, record=record,
                                  declared=_declared_of(section, record),
                                  rule_id=address, note=note))
    return out


#: Ключи строки, которые едут в артефакт нажатия и в выдачу: ключ заявки
#: и подробности открытой заявки остаются внутри вычисления.
ARTIFACT_KEYS = ("rule_id", "form", "section", "record", "kinds",
                 "request_id", "state", "words", "link")


def artifact_rows(rows) -> list:
    """Проекция строк классификатора для артефакта и выдачи нажатия."""
    return [{k: row.get(k) for k in ARTIFACT_KEYS} for row in rows or ()]


def requests_of(rows) -> dict:
    """{идентификатор правила: {"request_id", "state"}|None} — обогащение
    строк остановок номером и состоянием заявки (0.16: остановка называет
    номер, а не «разберитесь сами»)."""
    out = {}
    for row in rows or ():
        rid = str(row.get("rule_id") or "")
        if not rid:
            continue
        out[rid] = ({"request_id": row["request_id"],
                     "state": row.get("state")}
                    if row.get("request_id") else None)
    return out


# ──────────────────────────────────────────────── очередь и подача заявок

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _dump(doc: dict) -> bytes:
    """Форма файлов каталога — одна на службу, продукты и очереди."""
    from template_catalog.export import dump_json
    return dump_json(doc).encode("utf-8")


def request_document(row: dict, *, run_name: str,
                     at: str | None = None) -> dict:
    """Запись заявки на вид исполнителя (11 полей §6 схемы).

    Ни формулировки, ни идентификатора правила, ни источников: ссылка
    источника — имя прогона и 12 знаков ключа, а ключ считается от
    записи и набора видов."""
    if row.get("form") != FORM_KIND or not row.get("key"):
        raise CatalogRequestError(
            "заявка подаётся только по пробелу относительно записи "
            "(форма «%s» не подаётся из COSMOS)" % row.get("form"))
    return {
        "request_id": row["request_id"],
        "state": STATE_SUBMITTED,
        "section": row["section"],
        "record_id": row["record"],
        "submitted_at": at or _now(),
        "submitted_by": PRODUCT,
        "source": {"kind": SOURCE_KIND, "document_class": None,
                   "run_class": RUN_CLASS,
                   "ref": law.ref_of(str(run_name), row["key"])},
        "draft": None,
        "executor_kinds_requested": list(row["kinds"]),
        "decision": {"by": None, "at": None, "words": None},
        "cr_key": None,
    }


def _same_kinds(doc: dict, other: dict) -> bool:
    return sorted(set(str(k) for k in (doc.get("executor_kinds_requested")
                                       or ()))) == \
        sorted(set(str(k) for k in (other.get("executor_kinds_requested")
                                    or ())))


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _enqueue(doc: dict, outbox: Path) -> dict:
    """Публикация заявки в очередь ИСКЛЮЧИТЕЛЬНЫМ созданием файла (закон
    очереди CDPM): два одновременных нажатия одного пакета дают один
    номер, и проигравший читает файл-победитель, а не переписывает его."""
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / ("%s.json" % doc["request_id"])
    try:
        with open(path, "xb") as f:
            f.write(_dump(doc))
        return {"queued": True, "words": None}
    except FileExistsError:
        winner = _read_json(path) or {}
        if _same_kinds(winner, doc):
            return {"queued": True,
                    "words": "заявка %s уже подана" % doc["request_id"]}
        ref = str((winner.get("source") or {}).get("ref") or "?")
        return {"queued": False,
                "words": "заявка %s подана иным набором видов из %s; дубль "
                         "не заводится" % (doc["request_id"],
                                           ref.split("#", 1)[0])}


def reply_path(outbox: Path, request_id: str) -> Path:
    """Файл ответа службы на заявку очереди: его наличие значит «этот
    файл доставлен», и повторная доставка (и приём инструментом импорта
    на рабочем месте) его пропускает."""
    return Path(outbox) / ("%s.reply.json" % request_id)


def _deliver_doc(doc: dict, outbox: Path, *, sleep=time.sleep) -> dict:
    res = service_client.submit(doc, sleep=sleep)
    if res.get("ok"):
        reply_path(outbox, doc["request_id"]).write_bytes(_dump({
            "request_id": doc["request_id"], "accepted": True,
            "state": res.get("state"), "words": res.get("words"),
            "at": _now(), "by": "машинный канал COSMOS"}))
    return res


def submit_kind_requests(rows, *, run_name: str, out_dir, outbox=None,
                         auto=None, sleep=time.sleep) -> dict:
    """Подача заявок на вид исполнителя по строкам классификатора.

    Пишет очередь продукта и квитанцию нажатия `catalog-requests.json`
    рядом с прочими артефактами. Строки иных форм (новая запись, правка,
    дефект, «не каталог») не подаются — они едут в квитанцию словами:
    молчаливого пропуска нет.

    `auto=False` (либо `CREW_CATALOG_REQUESTS_AUTO=0`) — файлы очереди не
    создаются вовсе, квитанция говорит «подача кнопкой»: владелец
    оставил подачу за собой."""
    auto = auto_enabled() if auto is None else bool(auto)
    outbox = Path(outbox) if outbox is not None else outbox_root()
    receipt: dict = {"form": 1, "run": str(run_name), "rows": {}}
    if out_dir is not None:
        # Квитанция несёт ВСЕ заявки этого нажатия: рубежи Ш-1п, Ш-2м и
        # Ш-1п2 подают по своим пробелам, и вторая подача не имеет права
        # стереть первую из свидетельства.
        prev = _read_json(Path(out_dir) / RECEIPT_NAME)
        if isinstance(prev, dict) and prev.get("run") == str(run_name) \
                and isinstance(prev.get("rows"), dict):
            receipt["rows"].update(prev["rows"])
    for row in rows or ():
        rid = str(row.get("rule_id") or "")
        item = {"form": row.get("form"), "section": row.get("section"),
                "record": row.get("record"), "kinds": list(row.get("kinds")
                                                           or []),
                "request_id": row.get("request_id"),
                "state": row.get("state"), "delivered": False,
                "words": row.get("words") or ""}
        receipt["rows"][rid] = item
        if row.get("form") != FORM_KIND or row.get("existing") is not None \
                or not row.get("key"):
            continue                       # не наша форма либо уже заявлено
        if not auto:
            item["words"] = (
                "пробел относительно записи %s/%s: заявка на вид %s "
                "подаётся кнопкой «Заявить в каталог» (%s=0)"
                % (row.get("section"), row.get("record"),
                   ", ".join(row.get("kinds") or ()), ENV_AUTO))
            continue
        doc = request_document(row, run_name=run_name)
        queued = _enqueue(doc, outbox)
        if not queued["queued"]:
            item["words"] = queued["words"]
            continue
        item["state"] = STATE_SUBMITTED
        res = _deliver_doc(doc, outbox, sleep=sleep)
        item["delivered"] = bool(res.get("ok"))
        item["words"] = (
            "заявка на вид исполнителя %s для %s/%s подана: %s (в очереди · "
            "доставка — %s)"
            % (", ".join(row.get("kinds") or ()), row.get("section"),
               row.get("record"), doc["request_id"],
               "сошлось" if res.get("ok")
               else "не удалась: %s" % res.get("words"))
            + ("; %s" % queued["words"] if queued["words"] else ""))
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / RECEIPT_NAME).write_bytes(_dump(receipt))
    return receipt


def pending(outbox=None) -> list:
    """Файлы очереди без ответа службы — то, что ждёт доставки либо
    переноса на рабочее место владельца."""
    outbox = Path(outbox) if outbox is not None else outbox_root()
    if not outbox.is_dir():
        return []
    return sorted(p for p in outbox.glob("*.json")
                  if not p.name.endswith(".reply.json")
                  and not reply_path(outbox, p.stem).is_file())


def deliver_outbox(outbox=None, *, sleep=time.sleep) -> dict:
    """Повторная доставка файлов очереди, у которых нет ответа службы.

    Нужна там, где заявка родилась при недоступной службе: содержание
    файла не меняется (номер содержательный), меняется только то, что
    служба о нём знает."""
    outbox = Path(outbox) if outbox is not None else outbox_root()
    out = {"delivered": [], "failed": [], "skipped": [], "problems": []}
    if not configured()["on"]:
        out["problems"].append(service_words_off())
        out["pending"] = len(pending(outbox))
        return out
    for path in pending(outbox):
        doc = _read_json(path)
        if not isinstance(doc, dict) or not doc.get("request_id"):
            out["skipped"].append(path.name)
            out["problems"].append(
                "файл очереди %s не читается как запись заявки" % path.name)
            continue
        res = _deliver_doc(doc, outbox, sleep=sleep)
        (out["delivered"] if res.get("ok") else out["failed"]).append(
            {"request_id": doc["request_id"], "words": res.get("words")})
    out["pending"] = len(pending(outbox))
    return out
