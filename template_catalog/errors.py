# -*- coding: utf-8 -*-
"""Исключения читателя каталога шаблонов.

Любой отказ — словами, с путём файла или адресом записи. Читатель
только читает: исключения поднимаются при загрузке (`load`) и при
адресном обращении (`family`, `criterion`); `validate()` исключений не
бросает — возвращает список проблем.
"""
from __future__ import annotations


class CatalogError(Exception):
    """База исключений каталога шаблонов; сообщение всегда с адресом."""


class CatalogNotFound(CatalogError):
    """Нет папки, файла раздела или версии каталога."""


class CatalogFormatError(CatalogError):
    """Кривой JSON (путь, строка, позиция), дубль ключа, нет раздела или
    корневого ключа, шапка без версии, нецелое число в теле канона."""


class CatalogInvalid(CatalogError):
    """Только при load(..., strict=True): validate() дал ошибки."""

    def __init__(self, message: str, problems) -> None:
        super().__init__(message)
        self.problems = list(problems)


class UnknownRecord(CatalogError, KeyError):
    """family()/criterion(): записи с таким id нет; перечень известных —
    в сообщении."""

    def __str__(self) -> str:          # KeyError печатает repr — нам нужны слова
        return str(self.args[0]) if self.args else ""
