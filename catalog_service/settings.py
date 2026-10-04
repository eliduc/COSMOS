# -*- coding: utf-8 -*-
"""Настройки службы каталога: строятся ЯВНО, не при импорте.

`Settings` — неизменяемая запись; `Settings.from_env(argv)` собирает её
из ключей командной строки и переменных среды (ключ сильнее переменной).
Правило «роль и режим» живёт в одном месте — `__post_init__`: роли нет
или задан `--read-only` → режим просмотра; иначе — правка. Так
несогласованного состояния («режим правки без роли») не бывает по
построению, кто бы запись ни строил — старт службы или тест.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .identity import ACTORS

#: Корень репозитория: <корень>/catalog_service/settings.py.
REPO = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8105
DEFAULT_HOST = "127.0.0.1"


def data_root() -> Path:
    """Корень данных расчётов: `CREW_DATA_DIR`, иначе соседний с
    репозиторием каталог `<имя репозитория>-data`.

    Формула ПОВТОРЕНА из `_data_paths.py:27–29` намеренно, а не
    импортирована: служба не зависит от продуктовых модулей (гейт T2
    «служба не импортирует cdpm, vmp, webapp, _data_paths»). Повтор —
    пять строк и один смысл; импорт был бы зависимостью службы от
    продукта ради пяти строк."""
    env = os.environ.get("CREW_DATA_DIR")
    return Path(env) if env else REPO.parent / (REPO.name + "-data")


def default_catalog_dir() -> Path:
    """Папка каталога по умолчанию — та же, что у читателя."""
    from template_catalog import DEFAULT_CATALOG_DIR
    return Path(DEFAULT_CATALOG_DIR)


class SettingsError(ValueError):
    """Отказ старта словами: роль вне таблицы, порт не число и т.п."""


@dataclass(frozen=True)
class Settings:
    """Что служба знает о себе. Пути — абсолютные, наружу не отдаются."""

    catalog_dir: Path
    work_dir: Path
    actor: str | None = None
    read_only: bool = False
    mode: str = "view"
    cosmos_bindings: Path = field(
        default_factory=lambda: REPO / "vmp" / "bindings.json")
    cdpm_bindings: Path = field(
        default_factory=lambda: REPO / "cdpm" / "bindings.json")
    markers_file: Path | None = None
    machine_token: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog_dir", Path(self.catalog_dir))
        object.__setattr__(self, "work_dir", Path(self.work_dir))
        object.__setattr__(self, "cosmos_bindings", Path(self.cosmos_bindings))
        object.__setattr__(self, "cdpm_bindings", Path(self.cdpm_bindings))
        if self.markers_file is not None:
            object.__setattr__(self, "markers_file", Path(self.markers_file))
        object.__setattr__(self, "actor", _actor_word(self.actor))
        object.__setattr__(
            self, "mode",
            "view" if (self.read_only or not self.actor) else "edit")
        token = str(self.machine_token or "").strip()
        object.__setattr__(self, "machine_token", token or None)

    # ------------------------------------------------------- удобства
    @property
    def drafts_dir(self) -> Path:
        return self.work_dir / "drafts"

    @property
    def journal_path(self) -> Path:
        return self.work_dir / "actions.jsonl"

    @property
    def releases_dir(self) -> Path:
        return self.catalog_dir / "releases"

    def is_edit(self) -> bool:
        return self.mode == "edit"

    def machine_channel_on(self) -> bool:
        """Включён ли машинный канал заявок (шаг 6, решение 0.13).

        Канал держится на одном признаке — заданном `CATALOG_MACHINE_TOKEN`
        рабочего места. Площадки и установка заказчика запускают службу
        без него, и подача от продукта отказывает fail-closed словами,
        а не «проходит, потому что токен пуст с обеих сторон»."""
        return bool(self.machine_token)

    def is_owner(self) -> bool:
        return self.is_edit() and self.actor == ACTORS["owner"]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)

    # ------------------------------------------------------------ вход
    @classmethod
    def from_env(cls, argv: list[str] | None = None) -> "Settings":
        """Ключи командной строки и переменные среды. Ключ сильнее
        переменной; неизвестное значение роли — отказ словами."""
        args = parse_args(argv)
        catalog = args.catalog or os.environ.get("CREW_TEMPLATE_CATALOG")
        catalog_dir = Path(catalog) if catalog else default_catalog_dir()
        work = args.work_dir or os.environ.get("CATALOG_WORK_DIR")
        work_dir = Path(work) if work else data_root() / "catalog-work"
        actor = args.actor or os.environ.get("CATALOG_ACTOR") or None
        read_only = bool(args.read_only) or \
            _flag(os.environ.get("CATALOG_READ_ONLY"))
        cosmos = os.environ.get("CREW_COSMOS_BINDINGS")
        cdpm = os.environ.get("CREW_CDPM_BINDINGS")
        markers = os.environ.get("CATALOG_MARKERS_FILE")
        return cls(
            catalog_dir=catalog_dir, work_dir=work_dir, actor=actor,
            read_only=read_only,
            cosmos_bindings=Path(cosmos) if cosmos else REPO / "vmp" / "bindings.json",
            cdpm_bindings=Path(cdpm) if cdpm else REPO / "cdpm" / "bindings.json",
            markers_file=Path(markers) if markers else None,
            machine_token=os.environ.get("CATALOG_MACHINE_TOKEN"),
            host=args.host, port=args.port)


def _flag(value: str | None) -> bool:
    """Переменная-флаг: пусто и «0» — выключено, прочее — включено."""
    return bool(value) and str(value).strip() not in ("0", "")


def _actor_word(actor: str | None) -> str | None:
    """Роль словами по ASCII-ключу (`owner`, `analyst`) либо уже словом.

    Кириллица в переменных среды через `.cmd` ненадёжна (кодировка OEM),
    поэтому ключ запуска — ASCII, а слова словаря `actors` живут в
    данных. Значение вне таблицы — отказ словами, а не молчаливый
    просмотр: «служба запущена не тем, кем думали» обязано быть видно."""
    if actor is None or actor == "":
        return None
    text = str(actor).strip()
    if text in ACTORS:
        return ACTORS[text]
    if text in set(ACTORS.values()):
        return text
    raise SettingsError(
        f"роль службы — owner либо analyst; дано {text!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбор ключей запуска; ошибка разбора — SettingsError словами
    (argparse по умолчанию завершил бы процесс кодом 2 без слов
    о том, что именно служба ждала)."""
    parser = argparse.ArgumentParser(
        prog="python -m catalog_service",
        description="Служба каталога шаблонов: витрина и рабочее место.",
        add_help=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"порт (умолчание {DEFAULT_PORT})")
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help="адрес прослушивания (умолчание 127.0.0.1: входа и паролей у "
             "службы нет, публикуемое умолчание обязано быть безопасным; "
             "для доверенной сети — явно 0.0.0.0)")
    parser.add_argument("--actor", default=None,
                        help="роль рабочего места: owner | analyst")
    parser.add_argument("--read-only", action="store_true",
                        help="режим просмотра (площадки, установка заказчика)")
    parser.add_argument("--catalog", default=None,
                        help="папка каталога (иначе CREW_TEMPLATE_CATALOG)")
    parser.add_argument("--work-dir", default=None,
                        help="рабочая папка службы (иначе CATALOG_WORK_DIR)")
    try:
        return parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:                                    # noqa: PERF203
        if e.code == 0:                       # --help напечатал и вышел
            raise
        raise SettingsError(
            "ключи запуска не разобраны: ожидались --port, --host, --actor, "
            "--read-only, --catalog, --work-dir") from None


def startup_words(settings: Settings, catalog_words: str) -> str:
    """Строка, которую служба печатает при старте."""
    place = ("режим просмотра" if not settings.is_edit()
             else f"рабочее место: {settings.actor}")
    channel = ("машинный канал заявок включён"
               if settings.machine_channel_on()
               else "машинный канал заявок выключен")
    return (f"Каталог шаблонов: http://{settings.host}:{settings.port}/ ; "
            f"каталог {settings.catalog_dir} {catalog_words} ; {place} ; "
            f"{channel}")
