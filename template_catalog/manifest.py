# -*- coding: utf-8 -*-
"""Ключ пакета manifest.catalog и сторож дрейфа версий (§8 схемы).

Сравнение версий — тройкой целых без суффикса черновика; признак
черновика отдаётся отдельно. На 0.x разница младшей версии трактуется
как старшая (соглашение semver для 0.x) — КРОМЕ случая, когда черновики
с обеих сторон: остановка защищает выпуски, а не черновые номера
(`is_stop`, шаг 4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .validate import Problem

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-draft\.([1-9]\d*))?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class VersionDiff:
    level: str          # "same" | "patch" | "minor" | "major"
    zero_major: bool    # обе версии 0.x — minor трактуется как major
    draft_a: bool
    draft_b: bool


def parse_version(s: str) -> tuple[tuple[int, int, int], int | None]:
    """((MAJOR, MINOR, PATCH), N черновика | None); кривая строка —
    ValueError словами."""
    m = _SEMVER_RE.match(s if isinstance(s, str) else "")
    if not m:
        raise ValueError(f"версия {s!r} не вида MAJOR.MINOR.PATCH "
                         f"с необязательным суффиксом -draft.N")
    triple = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return triple, (int(m.group(4)) if m.group(4) else None)


def compare_versions(a: str, b: str) -> VersionDiff:
    ta, da = parse_version(a)
    tb, db = parse_version(b)
    if ta == tb:
        level = "same"
    elif ta[0] != tb[0]:
        level = "major"
    elif ta[1] != tb[1]:
        level = "minor"
    else:
        level = "patch"
    return VersionDiff(level=level, zero_major=(ta[0] == 0 and tb[0] == 0),
                       draft_a=da is not None, draft_b=db is not None)


def is_stop(diff: VersionDiff) -> bool:
    """Единственный закон остановки по версиям каталога — «стоп сторожа
    защищает ВЫПУСКИ».

    Стоп: разница в старшем числе всегда; разница в среднем числе на 0.x —
    когда хотя бы одна сторона выпуск (не черновик). Разница в среднем
    числе на 0.x между ДВУМЯ ЧЕРНОВИКАМИ — не стоп: черновик тем и
    черновик, что данные под ним меняются, и остановка ради чернового
    номера потребовала бы переиздания живых пакетов производителем.
    Патч и равные тройки — не стоп (отпечаток судится отдельно).

    Закон живёт в ОДНОМ месте: `check_manifest_catalog` (сторож перед
    нажатием) и B01 `bindings.check_bindings` зовут эту функцию, ни один
    продукт её не дублирует."""
    if diff.level == "major":
        return True
    return (diff.level == "minor" and diff.zero_major
            and not (diff.draft_a and diff.draft_b))


def catalog_stamp(catalog) -> dict:
    """{"version", "fingerprint"} для ключа manifest.catalog."""
    return {"version": catalog.version(), "fingerprint": catalog.fingerprint()}


def check_manifest_catalog(stamp: dict | None, bindings: dict) -> Problem | None:
    """Сторож перед нажатием: None — предупреждение «не объявлена»;
    стоп — по `is_stop` (старшая разница; младшая при 0.x, если хотя бы
    одна сторона выпуск); младшая при 0.x между двумя черновиками —
    предупреждение с обоими отпечатками; младшая/патч — предупреждение;
    равные выпуски с разными отпечатками — стоп; черновик той же тройки —
    предупреждение."""
    where = "manifest.catalog"
    if stamp is None:
        return Problem(where, "B01", "версия каталога шаблонов пакетом "
                       "не объявлена", "warning")
    if not isinstance(stamp, dict) or not isinstance(stamp.get("version"), str):
        return Problem(where, "B01", "ключ manifest.catalog не объект с "
                       "полем version", "error")
    if not isinstance(stamp.get("fingerprint"), str) \
            or not _SHA256_RE.match(stamp["fingerprint"]):
        return Problem(where, "B01", "manifest.catalog.fingerprint не "
                       "64 шестнадцатеричных знака", "error")
    bv = bindings.get("catalog_version") if isinstance(bindings, dict) else None
    bf = bindings.get("catalog_fingerprint") if isinstance(bindings, dict) else None
    try:
        diff = compare_versions(stamp["version"], str(bv))
    except ValueError as e:
        return Problem(where, "B01", f"версия не сравнима: {e}", "error")
    if is_stop(diff):
        return Problem(where, "B01",
                       f"пакет собран по каталогу {stamp['version']}, "
                       f"исполнители привязаны к {bv}: старшая разница "
                       f"версий — расчёт не начинается", "error")
    if diff.level == "minor" and diff.zero_major:
        # Два черновика: остановка защищает выпуски, а не черновые номера.
        return Problem(where, "B01",
                       f"пакет собран по черновику каталога "
                       f"{stamp['version']} (отпечаток "
                       f"{stamp['fingerprint'][:12]}…), исполнители "
                       f"привязаны к черновику {bv} (отпечаток "
                       f"{str(bf)[:12]}…): младшая разница версий на 0.x "
                       f"между двумя черновиками — расчёт продолжается с "
                       f"предупреждением; перевыпуск пакета — "
                       f"производителем; выпуск любой из сторон сделал бы "
                       f"это остановкой", "warning")
    if diff.level in ("minor", "patch"):
        return Problem(where, "B01",
                       f"пакет собран по каталогу {stamp['version']}, "
                       f"исполнители привязаны к {bv}: {diff.level} — "
                       f"продолжаем с предупреждением", "warning")
    if diff.draft_a or diff.draft_b:
        # Черновик той же базовой версии — предупреждение, не стоп
        # (решение 0.9). Отпечаток при этом всё равно называется: у
        # черновика одна версия законно несёт разные данные, и «какие
        # именно» видно только по отпечатку.
        same = stamp["fingerprint"] == bf
        return Problem(where, "B01", "каталог в черновике "
                       f"({stamp['version']} / {bv}): отпечаток "
                       + (f"тот же {str(bf)[:12]}…" if same else
                          f"пакета {stamp['fingerprint'][:12]}…, "
                          f"привязок {str(bf)[:12]}… — данные каталога "
                          f"разные"), "warning")
    if stamp["fingerprint"] != bf:
        return Problem(where, "B01",
                       f"одна версия каталога {stamp['version']} — разные "
                       f"данные (отпечаток пакета {stamp['fingerprint'][:12]}…, "
                       f"привязок {str(bf)[:12]}…)", "error")
    return None
