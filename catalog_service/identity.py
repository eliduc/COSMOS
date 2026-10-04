# -*- coding: utf-8 -*-
"""Кто вправе: роль рабочего места и права по операциям.

Входа и паролей у службы нет — она слушает 127.0.0.1, как панель CDPM, а
удалённая правка исключена самим устройством (площадки запускаются с
`--read-only` и не пишут ничего). Роль задаётся ПРИ ЗАПУСКЕ ASCII-ключом
`--actor owner|analyst`; ровно два ключа, третьего значения не бывает —
иначе отказ старта словами (`settings._actor_word`).

Слово 06.09.2026: «заявки принимает и версии выпускает только владелец».
Аналитик ведёт черновики (кроме смены `status`), подаёт заявки и
собирает черновик.
"""
from __future__ import annotations

import hmac

from .errors import ServiceError

#: Ровно две роли. Ключ — ASCII (переменные среды через .cmd в кодировке
#: OEM кириллицу теряют), слово — из словаря `actors` каталога.
ACTORS = {"owner": "владелец", "analyst": "аналитик"}

#: Операции записи. Перечень закрыт: новая операция — правка контракта и
#: таблицы прав, а не строка «если роль…» в обработчике.
OPERATIONS = (
    "draft-save",        # сохранить черновик записи или ключа словаря
    "draft-status",      # черновик со сменой status («снять запись»)
    "draft-discard",     # отменить черновик
    "request-submit",    # подать заявку
    "assemble",          # собрать черновик
    "request-decide",    # принять / отклонить заявку
    "request-cr-key",    # заявить исполнителя (ключ CR)
    "request-adopt",     # перевести черновик заявки в записи
    "support-refresh",   # пересчитать свидетельство поддержки (K06)
    "release",           # выпустить версию
    "import",            # загрузить каталог одним файлом
)

#: Операции, доступные только владельцу; остальные — аналитику и выше.
OWNER_ONLY = frozenset({"draft-status", "request-decide", "request-cr-key",
                        "request-adopt", "release", "import"})

READ_ONLY_WORDS = (
    "служба каталога в режиме просмотра: правки, заявки, сборка и выпуск — "
    "на рабочем месте владельца (запуск с --actor owner)")


def owner_only_words(actor: str | None) -> str:
    return ("заявки принимает, записи снимает и версии выпускает только "
            "владелец (слово 06.09); служба запущена с ролью "
            f"«{actor or 'без роли'}»")


def can(settings, operation: str) -> bool:
    """Вправе ли текущая роль выполнить операцию записи."""
    if operation not in OPERATIONS:
        raise KeyError(f"операция {operation!r} вне перечня прав службы")
    if not settings.is_edit():
        return False
    if operation in OWNER_ONLY:
        return settings.actor == ACTORS["owner"]
    return settings.actor in set(ACTORS.values())


def refuse(settings, operation: str) -> ServiceError:
    """Готовый отказ словами: просмотр — `read-only`, аналитик на
    операции владельца — `owner-only`."""
    if not settings.is_edit():
        return ServiceError("read-only", READ_ONLY_WORDS, 403)
    return ServiceError("owner-only", owner_only_words(settings.actor), 403)


def require(settings, operation: str) -> None:
    """Проверка права перед действием; отказ — исключение службы."""
    if not can(settings, operation):
        raise refuse(settings, operation)


#: Заголовки машинного канала заявок (шаг 6, решение 0.13). Имена —
#: контракт с продуктами: COSMOS и CDPM шлют ровно их.
PRODUCT_HEADER = "X-Catalog-Product"
TOKEN_HEADER = "X-Catalog-Token"

MACHINE_OFF_WORDS = (
    "машинный канал заявок выключен: задайте CATALOG_MACHINE_TOKEN на "
    "рабочем месте владельца (на площадках и у заказчика служба стоит в "
    "режиме просмотра, и заявки копятся в очереди продукта)")


def machine_channel(settings, product: str | None, token: str | None,
                    products: list | tuple | set) -> str:
    """Кто зовёт машинную ручку: продукт по заголовкам или отказ словами.

    Порядок отказов закрыт и назван в контракте: канал выключен (403) →
    чужой токен (401) → продукт вне словаря `products` (400). Сначала
    судится КАНАЛ, а не токен: без `CATALOG_MACHINE_TOKEN` сравнивать
    нечего, и «неверный токен» был бы неправдой о причине.

    Токен сверяется `hmac.compare_digest` — сравнением ПОСТОЯННОГО
    ВРЕМЕНИ: служба слушает петлевой адрес, но это единственный секрет
    канала, и обычное `!=` с ранним выходом выдавало бы длиной ответа
    совпавший префикс (лучшая мировая практика сверки секретов)."""
    if not settings.machine_channel_on():
        raise ServiceError("machine-channel-off", MACHINE_OFF_WORDS, 403)
    if not token or not hmac.compare_digest(
            str(token).encode("utf-8"),
            str(settings.machine_token).encode("utf-8")):
        raise ServiceError(
            "bad-token",
            "токен машинного канала не тот: заголовок "
            f"{TOKEN_HEADER} обязан нести CATALOG_MACHINE_TOKEN службы", 401)
    name = str(product or "").strip()
    if name not in {str(p) for p in products or ()}:
        raise ServiceError(
            "submitter-not-allowed",
            f"продукт {name!r} вне словаря products каталога "
            f"({', '.join(sorted(str(p) for p in products or ()))}): "
            f"заголовок {PRODUCT_HEADER} называет подателя", 400)
    return name


def rights(settings) -> dict:
    """Карта прав для интерфейса: кнопка недоступна — с причиной в
    подсказке, а не спрятана (контракт панелей)."""
    out = {}
    for op in OPERATIONS:
        allowed = can(settings, op)
        out[op] = {"allowed": allowed,
                   "words": None if allowed else refuse(settings, op).words}
    return out
