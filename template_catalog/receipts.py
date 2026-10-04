# -*- coding: utf-8 -*-
"""Закон квитанции заявки (`receipt/1`) — ОДНА функция на обоих клиентов.

Квитанция — прикладной носитель ответа службы каталога на подачу заявки
(П0 §4.1, K07). Заявка считается ПОДАННОЙ только по телу этой схемы с её
идентичностью: любой иной 2xx, страница входа площадки, пустое тело,
204, чужой номер и ответ старой службы без `schema_version` квитанцией
не являются — исход «неизвестен», файл остаётся в очереди, повтор идёт
ТЕМ ЖЕ номером (номер содержательный, второго не бывает).

Почему закон живёт здесь, а не у каждого клиента своей копией: клиентов
двое — `cdpm/catalog_requests.deliver_one` (зона CDPM) и
`catalog_service/import_requests.one` (зона COSMOS), и стоит им завести
по своему перечню проверок, как перечни разойдутся при первой же правке
(так уже случилось: слова руководства обещали проверку схемой, а код её
не делал). Схема и закон лежат в общем пакете читателя каталога, как
`completeness.py` и `objective_projection.py` (П0 §11.1): владелец файла
— COSMOS, CDPM импортирует и проверяет своими стендами.

Публичное:
    RECEIPT_SCHEMA_VERSION — «receipt/1»
    REPEAT_NOTE — слова повторной выдачи («уже подана»)
    receipt_states() — закрытый перечень слов состояния (enum схемы)
    validate_receipt(body, *, sent=None) -> list[str]
"""
from __future__ import annotations

import json
from pathlib import Path

from .requests import REQUEST_ID_RE, draft_canon

#: Носитель схемы квитанции; у службы каталога лежит байт-в-байт копия
#: (`catalog_service/schemas/receipt.schema.json`) — как у схемы самого
#: каталога (`catalog/schema/catalog.schema.json`).
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" \
    / "receipt.schema.json"
#: Версия схемы квитанции (П0 §0.1). Тело без неё — ответ СТАРОЙ службы,
#: и это не квитанция (fail-closed по замыслу: площадка со старой
#: службой обязана обновляться одним раскатом).
RECEIPT_SCHEMA_VERSION = "receipt/1"
#: Слова повторной выдачи той же квитанции (200): ими клиент отличает
#: «доставлено ранее» от «записано сейчас» (201).
REPEAT_NOTE = "уже подана"
#: Поля тела, без которых квитанции не бывает (минимум П0 §4.1). Тот же
#: перечень стоит в `required` схемы — здесь он кодом, чтобы тело
#: судилось и там, где `jsonschema` не установлен.
REQUIRED_FIELDS = ("schema_version", "request_id", "state", "note", "repeat",
                   "catalog", "section", "record_id", "canon", "decision",
                   "journal_ref", "issued_at")


def _schema() -> dict:
    """Схема квитанции с диска; файла нет либо он не читается — `{}`.

    Пустая схема не выдаёт негодное тело за годное: структурный минимум
    ниже судится кодом и без неё."""
    try:
        doc = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):                           # pragma: no cover
        return {}
    return doc if isinstance(doc, dict) else {}


def receipt_states() -> tuple:
    """Закрытый перечень слов состояния заявки — ИЗ СХЕМЫ квитанции.

    Единственный источник перечня для клиента — `enum` поля `state` в
    схеме: словарь каталога (`request_states`) клиенту недоступен — он
    судит ответ службы, каталога которой у него может не быть вовсе.
    Своей копии перечня ни у службы, ни у CDPM нет; равенство перечня
    схемы словарю живого каталога судит стенд
    (`tests/test_w17b_catalog_service_receipt.py`)."""
    state = ((_schema().get("properties") or {}).get("state") or {})
    return tuple(str(w) for w in (state.get("enum") or ()))


def validate_receipt(body, *, sent=None) -> list[str]:
    """Чем тело ответа НЕ является квитанцией; пустой перечень — является.

    `body` — разобранное тело ответа службы (объект JSON).
    `sent` — отправленная заявка (запись очереди) либо `None`. Когда она
    передана, сверяется ИДЕНТИЧНОСТЬ: номер, раздел, запись и канон
    черновика. Без неё судится только форма: клиент, у которого заявки
    под рукой нет (переборка очереди, миграция линии 3), всё равно
    отличит квитанцию от страницы входа.

    Код ответа, тип содержимого и согласие `repeat` с кодом — дело
    ТРАНСПОРТА и судятся у клиента (`import_requests.receipt_problems`,
    `deliver_one`): тело о своём HTTP-коде не знает. Здесь судится то,
    что несёт само тело, — в том числе согласие `note` и `repeat` между
    собой.

    Перечень возвращается СЛОВАМИ, а не булевым: «не принята» без
    причины отправляет человека читать логи службы."""
    беды: list[str] = []
    if not isinstance(body, dict):
        return [f"тело ответа не объект JSON: {type(body).__name__}"]
    # 1. Структурный минимум кодом — он же работает без jsonschema.
    #    Пропущенные поля называются ОДНОЙ строкой: перечень уходит
    #    словами в файл-ответ площадки, и двенадцать строк «нет поля»
    #    прочитать труднее, чем одну.
    отсутствуют = [поле for поле in REQUIRED_FIELDS if поле not in body]
    if отсутствуют:
        беды.append("в теле нет полей: " + ", ".join(отсутствуют))
    if "schema_version" in body \
            and body.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        беды.append(f"schema_version = {body.get('schema_version')!r}, "
                    f"а квитанция — {RECEIPT_SCHEMA_VERSION!r}")
    rid = body.get("request_id")
    if "request_id" in body \
            and (not isinstance(rid, str) or not REQUEST_ID_RE.match(rid)):
        беды.append(f"номер заявки {rid!r} не по закону читателя "
                    f"(req-<20 цифр>)")
    states = receipt_states()
    if "state" in body and states and body.get("state") not in states:
        беды.append(f"состояние {body.get('state')!r} вне перечня "
                    f"{', '.join(states)}")
    # 2. Согласие слов повторной выдачи с признаком повтора: «уже подана»
    #    без `repeat` (и наоборот) — тело, о котором клиент не сможет
    #    сказать, приняли его сейчас или раньше.
    note, repeat = body.get("note"), body.get("repeat")
    if isinstance(repeat, bool) and (note == REPEAT_NOTE) != repeat:
        беды.append(f"note = {note!r} не согласовано с repeat = {repeat!r} "
                    f"(повторная выдача — «{REPEAT_NOTE}» и repeat: true)")
    # 3. Форма целиком — схемой квитанции (тот же файл у службы и у CDPM).
    from .validate import schema_messages                   # noqa: PLC0415
    беды.extend(schema_messages(body, _schema(), "квитанция"))
    # 4. Идентичность: ТА ЛИ это заявка.
    беды.extend(_identity_problems(body, sent))
    return беды


def _identity_problems(body: dict, sent) -> list[str]:
    """Сверка квитанции с отправленной заявкой: номер, раздел, запись,
    канон черновика.

    Номер — каноническая идентичность (П0 §4.1 п. «б»), но одного его
    мало: служба вправе ответить своей квитанцией на чужую заявку только
    по ошибке, и такую ошибку клиент обязан увидеть раньше, чем сочтёт
    свой файл доставленным."""
    if not isinstance(sent, dict):
        return []
    беды: list[str] = []
    отправлен = sent.get("request_id")
    if отправлен is not None and body.get("request_id") != отправлен:
        беды.append(f"номер в ответе {body.get('request_id')!r} не тот, что "
                    f"отправлен ({отправлен!r})")
    for поле in ("section", "record_id"):
        было = sent.get(поле)
        if было is not None and body.get(поле) != было:
            беды.append(f"{поле} в ответе {body.get(поле)!r}, а в заявке "
                        f"{было!r}")
    if "draft" in sent:
        канон = draft_canon(sent.get("draft"))
        if body.get("canon") != канон:
            беды.append(f"канон черновика в ответе {body.get('canon')!r} не "
                        f"равен канону отправленного ({канон!r})")
    return беды
