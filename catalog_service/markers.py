# -*- coding: utf-8 -*-
"""Перечень маркеров заказчика для правила A02 читателя.

Каталог шаблонов обезличен по построению (P-N-SHD): в нём нет ни имени
авиакомпании, ни её баз, бортов и табельных номеров. Проверку ведёт
читатель — `validate(forbidden_terms=…)`; перечень образцов даёт линт
маркеров `tools.customer_markers` (единый источник правды на машине,
живёт ВНЕ публикуемого репозитория).

Единственный допускаемый импорт службы вне читателя — и только здесь,
и только лениво: на площадке и у заказчика перечня нет, и служба обязана
работать без него, говоря об этом словами, а не молча.

Правило подачи (решение 10 журнала критики): перечень подаётся ВСЕГДА,
когда он прочитан, — при сохранении черновика, сборке, заявках и
загрузке. Нет перечня — предупреждение `markers-missing` в ответе и
строка журнала; и только ВЫПУСК закрыт наглухо: обезличенность выпуска
без проверки не удостоверяется.
"""
from __future__ import annotations

from pathlib import Path


def markers_path(settings) -> Path | None:
    """Явный Settings сильнее env; общий закон пути принадлежит линту."""
    try:
        from tools import customer_markers as cm
    except Exception:                                        # noqa: BLE001
        return None
    return cm.markers_path(settings.markers_file)


def read(settings) -> tuple[list | None, str | None]:
    """(скомпилированные образцы | None, причина словами | None).

    Причин «перечня нет» четыре, и они РАЗНЫЕ для того, кто чинит:
    линта на машине нет, файла нет, файл не читается, в файле нет ни
    одной проверки. Одно общее «не найден» посылало бы владельца искать
    файл, который лежит на месте."""
    path = markers_path(settings)
    if path is None:
        return None, ("линт маркеров tools.customer_markers недоступен на "
                      "этой машине")
    name = Path(path).name
    if not path.is_file():
        return None, f"перечень маркеров заказчика не найден: {name}"
    try:
        from tools import customer_markers as cm
    except Exception as e:                                   # noqa: BLE001
        return None, (f"линт маркеров tools.customer_markers недоступен: "
                      f"{type(e).__name__}")
    try:
        spec = cm.load_checks(path)
    except Exception as e:                                   # noqa: BLE001
        return None, (f"перечень маркеров заказчика не читается: {name} "
                      f"({type(e).__name__})")
    if not spec:
        return None, f"перечень маркеров заказчика пуст: {name}"
    pats: list = []
    for check in (spec.get("checks") or spec.get("markers") or []):
        if not isinstance(check, dict):
            continue
        try:
            pats += cm._patterns(check)
        except Exception:                                    # noqa: BLE001
            continue
    if not pats:
        return None, (f"перечень маркеров заказчика пуст: {name} — ни одной "
                      f"проверки")
    return pats, None


def load(settings) -> list | None:
    """Образцы для `forbidden_terms`; None — перечня нет (причину словами
    даёт `read`)."""
    return read(settings)[0]


def terms(settings) -> tuple:
    """`forbidden_terms` для `validate()`: перечень либо пустой кортеж."""
    pats = load(settings)
    return tuple(pats) if pats else ()


def reason(settings) -> str:
    """Почему перечня нет — словами; перечень прочитан — так и сказано."""
    return read(settings)[1] or "перечень маркеров заказчика прочитан"


def missing_words(settings) -> str:
    return f"{reason(settings)} — проверка обезличенности не выполнялась"


def release_refusal_words(settings) -> str:
    return (f"{reason(settings)} — выпуск без проверки обезличенности не "
            f"делается (решение 0.11 схемы)")
