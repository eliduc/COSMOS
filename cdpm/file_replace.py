# -*- coding: utf-8 -*-
"""Замена файла под открытыми читателями — общий помощник CDPM.

На Windows `os.replace` отказывает («Access is denied» / «используется
другим процессом»), пока хоть одна нить или процесс держит целевой файл
открытым обычным `open()`. Манифест журнала вызовов и статус прогона
читают ручки панели ровно тогда, когда нить стадии их заменяет; на двух
ядрах раннера GitHub горячие читатели не оставляют писателю ни мгновения,
и 40 повторов по 25–100 мс кончались PermissionError (CI среза eafe537,
07.09.2026; воспроизведено локально с привязкой процесса к двум ядрам —
1 падение из 8, каждый прогон 35–47 с вместо секунды).

Средство — два согласованных шага, оба только на Windows:
  * читатель открывает файл с FILE_SHARE_DELETE (`read_bytes_shared`,
    `open_shared`);
  * писатель переименовывает временный файл поверх целевого с семантикой
    POSIX (`SetFileInformationByHandle(FileRenameInfoEx,
    FILE_RENAME_FLAG_POSIX_SEMANTICS | REPLACE_IF_EXISTS)`; Windows 10 /
    Server 2016 и новее, NTFS): открытые читатели дочитывают прежнее
    содержимое, новое видно со следующего открытия.
Опыт 07.09: 200 замен под тремя горячими читателями — 0 отказов за 1,1 с.
Обычный `open()` читателя блокирует и POSIX-переименование (WinError 32),
и переименование поверх читателя с FILE_SHARE_DELETE через `os.replace`
тоже отказывает (WinError 5) — поэтому нужны оба шага сразу. Чужие
читатели (антивирус, индексатор, другой процесс со старым `open()`) могут
держать файл по-прежнему — `replace_with_retry` ждёт окно ПО ВРЕМЕНИ, а
не по числу попыток, и сдаётся словами.

Вне Windows всё сводится к `Path.read_bytes` и `os.replace`.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

#: Окно ожидания чужого читателя при замене, секунд (по времени: медленный
#: раннер с антивирусом получает столько же секунд, сколько быстрая машина).
REPLACE_WINDOW_S = 10.0
_WIN = sys.platform == "win32"

if _WIN:                                                  # pragma: no cover
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _k32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _SetFileInformationByHandle = _k32.SetFileInformationByHandle
    _SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    _SetFileInformationByHandle.restype = wintypes.BOOL
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _SHARE_ALL = 0x1 | 0x2 | 0x4            # READ | WRITE | DELETE
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _FileRenameInfoEx = 22
    _RENAME_REPLACE_IF_EXISTS = 0x1
    _RENAME_POSIX_SEMANTICS = 0x2
    #: Коды Win32, при которых семантика POSIX недоступна (не NTFS, старая
    #: система, неверная форма имени) — тогда запасной `os.replace`.
    _FALLBACK_WINERRORS = {1, 17, 50, 87, 123, 206}


def _win_error() -> OSError:                              # pragma: no cover
    """Ошибка Win32 как OSError нужного подкласса: 2/3 → FileNotFoundError,
    5/32 → PermissionError (Python сам сопоставляет winerror и errno)."""
    return ctypes.WinError(ctypes.get_last_error())


def open_shared(path):
    """Файл на чтение (двоичный). На Windows — с FILE_SHARE_DELETE: писатель
    может заменить файл, не дожидаясь этого читателя; читатель дочитывает
    то, что открыл. Ошибки — как у `open()`."""
    p = Path(path)
    if not _WIN:
        return open(p, "rb")
    h = _CreateFileW(str(p), _GENERIC_READ, _SHARE_ALL, None,      # pragma: no cover
                     _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == _INVALID_HANDLE:                          # pragma: no cover
        raise _win_error()
    try:                                                            # pragma: no cover
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    except Exception:                                               # pragma: no cover
        _CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")                                      # pragma: no cover


def read_bytes_shared(path) -> bytes:
    """Содержимое файла через `open_shared`."""
    with open_shared(path) as f:
        return f.read()


def _win_posix_replace(src: Path, dst: Path) -> None:             # pragma: no cover
    name = os.path.abspath(str(dst))
    n = len(name)

    class _Rename(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD),
                    ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD),
                    ("FileName", wintypes.WCHAR * (n + 1))]

    info = _Rename(_RENAME_REPLACE_IF_EXISTS | _RENAME_POSIX_SEMANTICS,
                   None, n * 2, name)
    h = _CreateFileW(str(src), _DELETE | _GENERIC_READ, _SHARE_ALL, None,
                     _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == _INVALID_HANDLE:
        raise _win_error()
    try:
        if not _SetFileInformationByHandle(h, _FileRenameInfoEx,
                                           ctypes.byref(info),
                                           ctypes.sizeof(info)):
            raise _win_error()
    finally:
        _CloseHandle(h)


def replace_file(src, dst) -> None:
    """Атомарная замена `dst` файлом `src` (один том). Windows — семантика
    POSIX; где она недоступна — `os.replace`. Целевой файл, который держит
    чужой читатель обычным `open()`, — PermissionError, как у `os.replace`."""
    if not _WIN:
        os.replace(src, dst)
        return
    try:                                                            # pragma: no cover
        _win_posix_replace(Path(src), Path(dst))
    except OSError as e:                                            # pragma: no cover
        if getattr(e, "winerror", None) in _FALLBACK_WINERRORS:
            os.replace(src, dst)
        else:
            raise


def claim_file(src, dst) -> bool:
    """ЗАНЯТЬ место `dst` файлом `src`, НЕ ЗАМЕНЯЯ занятое: True — место
    было свободно и занято нами, False — там уже лежит чужой файл.

    Отличие от `replace_file` принципиальное: неделимая замена молча
    затирает победителя, а публикация в общую очередь обязана сохранить
    ПЕРВОЕ принятое содержимое. Windows: `os.rename` на занятое имя сам
    отвечает `FileExistsError` — проверка и занятие уже одна операция.
    POSIX: `os.rename` затирает молча, потому «занять, если свободно» —
    жёсткая ссылка `os.link` с последующим удалением источника (обычный
    приём неделимой публикации); файловая система без жёстких ссылок —
    запасной путь с проверкой."""
    src, dst = Path(src), Path(dst)
    if _WIN:                                                # pragma: no cover
        try:
            os.rename(src, dst)
            return True
        except FileExistsError:
            return False
    try:
        os.link(src, dst)
    except FileExistsError:
        return False
    except OSError:
        if dst.exists():
            return False
        os.rename(src, dst)
        return True
    os.unlink(src)
    return True


def claim_with_retry(src, dst, *, window_s: float | None = None,
                     on_busy=None) -> bool:
    """`claim_file` с ожиданием чужого читателя (Windows не переименует
    файл, который держит открытым другой процесс): повторы ПО ВРЕМЕНИ,
    как у `replace_with_retry`; после окна — PermissionError словами."""
    window = REPLACE_WINDOW_S if window_s is None else float(window_s)
    deadline = time.monotonic() + max(0.0, window)
    attempt = 0
    while True:
        try:
            return claim_file(src, dst)
        except PermissionError as e:
            attempt += 1
            if on_busy is not None:
                on_busy(attempt)
            if time.monotonic() >= deadline:
                raise PermissionError(
                    e.errno,
                    f"занять место {dst} не удалось за {window:g} с "
                    f"({attempt} попыток): файл держит другой процесс "
                    f"(антивирус, индексатор, чужой читатель); исходная "
                    f"ошибка: {e.strerror}", str(dst)) from e
            time.sleep(0.01 * (1 + attempt % 5))


def replace_with_retry(src, dst, *, window_s: float | None = None,
                       on_busy=None) -> None:
    """`replace_file` с ожиданием чужого читателя: повторы ПО ВРЕМЕНИ
    (`window_s` секунд, умолчание — `REPLACE_WINDOW_S` на момент вызова;
    паузы 10–50 мс), после окна — PermissionError словами: какой файл,
    сколько ждали, сколько попыток. `on_busy(attempt)` зовётся при каждом
    отказе (журнал, тесты); его исключение прерывает ожидание."""
    window = REPLACE_WINDOW_S if window_s is None else float(window_s)
    deadline = time.monotonic() + max(0.0, window)
    attempt = 0
    while True:
        try:
            replace_file(src, dst)
            return
        except PermissionError as e:
            attempt += 1
            if on_busy is not None:
                on_busy(attempt)
            if time.monotonic() >= deadline:
                raise PermissionError(
                    e.errno,
                    f"замена файла {dst} не удалась за {window:g} с "
                    f"({attempt} попыток): файл держит другой процесс "
                    f"(антивирус, индексатор, чужой читатель); исходная "
                    f"ошибка: {e.strerror}", str(dst)) from e
            time.sleep(0.01 * (1 + attempt % 5))
