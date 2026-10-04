# -*- coding: utf-8 -*-
"""Писатель каталога шаблонов: форма файлов, отпечаток, проверка ДО
записи, атомарная замена (§2.7 проекта шага 5).

Зачем в читателе, а не в службе: форму файлов (LF, отступ 1, ключи по
алфавиту), отпечаток канона (H02) и производные инварианты
(`refresh_lexicon_order`, `refresh_families_order`, формула L10) уже
знает читатель; третья копия этого знания в службе противоречила бы
правилу «одно вычисление для всех сторон» (§9 п.5 схемы записи).

Гарантии пакета сохраняются: только стандартная библиотека, ни одного
импорта из cdpm/vmp/webapp/tools, при импорте не читается ни файла и не
трогается ни одной переменной среды.

Закон записи — «сначала суд, потом диск»:
  1) `canon.normalize(body)` — нецелое число или чужой раздел даёт
     CatalogWriteError словами с адресом;
  2) отпечаток шапки пересчитывается из тела (H02 сходится всегда);
  3) кандидат судится `validate(forbidden_terms)`; ошибки — отказ
     CatalogWriteError с перечнем Problem, на диске НИ ОДНОГО байта;
  4) тексты кладутся рядом файлами `<имя>.json.tmp-<pid>` и заменяют
     цель `os.replace`-ом с окном ожидания;
  5) `catalog.json` заменяется ПОСЛЕДНИМ.

Окно несогласованности — между заменой разделов и шапки (миллисекунды):
читатель, увидевший старую шапку и новые разделы, получит H02 на один
экземпляр — честный сигнал «идёт запись», а не тихая порча; следующая
отметка (замена `catalog.json`) даёт новый годный экземпляр.

Замена под открытым читателем (принцип cdpm/file_replace.py — повторён
здесь, а не импортирован: читатель не зависит от продуктов): на Windows
`os.replace` отказывает, пока цель держит чужой обычный `open()`, поэтому
переименование идёт с семантикой POSIX
(`SetFileInformationByHandle(FileRenameInfoEx)`), а повторы отмеряются ПО
ВРЕМЕНИ (окно `window_s`, паузы 20 мс), после чего — PermissionError
словами: какой файл, сколько ждали, сколько попыток.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import canon
from .errors import CatalogError
from .export import dump_json
from .reader import Catalog, load
from .validate import schema_path_for_version

#: Окно ожидания чужого читателя при замене файла, секунд (по времени, а
#: не по числу попыток: медленная машина с антивирусом получает столько
#: же секунд, сколько быстрая).
REPLACE_WINDOW_S = 10.0
#: Пауза между попытками замены, секунд (§2.7 проекта).
RETRY_PAUSE_S = 0.02
#: Имена файлов разделов по умолчанию; шапка вправе назвать свои
#: (`sections`), и писатель пишет ровно те, что назовёт она.
DEFAULT_SECTION_FILES = {"families": "families.json",
                         "criteria": "criteria.json",
                         "dictionaries": "dictionaries.json",
                         "requests": "requests.json"}
VERSIONS_FILE = "versions.json"
HEADER_FILE = "catalog.json"
ORDER_FILE = "transition/families_order.json"
SCHEMA_COPY = "schema/catalog.schema.json"
_WIN = sys.platform == "win32"


class CatalogWriteError(CatalogError):
    """Запись не сделана: ничего на диск не легло. `.problems` — перечень
    Problem, если отказ пришёл от validate()."""

    def __init__(self, message: str, problems=()) -> None:
        super().__init__(message)
        self.problems = list(problems)


# ------------------------------------------------- атомарная замена
#: Коды Win32, при которых семантика POSIX недоступна (не NTFS, старая
#: система, неверная форма имени) — тогда запасной `os.replace`.
_FALLBACK_WINERRORS = frozenset({1, 17, 50, 87, 123, 206})


def _win_posix_replace(src: Path, dst: Path) -> None:      # pragma: no cover
    """Переименование поверх цели с семантикой POSIX
    (`SetFileInformationByHandle(FileRenameInfoEx)`; Windows 10 /
    Server 2016 и новее, NTFS): открытые читатели дочитывают прежнее
    содержимое, новое видно со следующего открытия.

    `ctypes` берётся ЗДЕСЬ, а не при импорте модуля: его импорт тянет
    `sysconfig`, а тот читает переменные среды, и гарантия пакета «при
    импорте ни файла и ни одной переменной среды» была бы нарушена."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = k32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    set_info = k32.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                         wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    close = k32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    GENERIC_READ, DELETE = 0x80000000, 0x00010000
    SHARE_ALL = 0x1 | 0x2 | 0x4                    # READ | WRITE | DELETE
    OPEN_EXISTING, ATTR_NORMAL = 3, 0x80
    INVALID = wintypes.HANDLE(-1).value
    FILE_RENAME_INFO_EX = 22
    REPLACE_IF_EXISTS, POSIX_SEMANTICS = 0x1, 0x2

    name = os.path.abspath(str(dst))
    n = len(name)

    class _Rename(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD),
                    ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD),
                    ("FileName", wintypes.WCHAR * (n + 1))]

    info = _Rename(REPLACE_IF_EXISTS | POSIX_SEMANTICS, None, n * 2, name)
    h = create(str(src), DELETE | GENERIC_READ, SHARE_ALL, None,
               OPEN_EXISTING, ATTR_NORMAL, None)
    if h is None or h == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not set_info(h, FILE_RENAME_INFO_EX, ctypes.byref(info),
                        ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(h)


def replace_file(src, dst) -> None:
    """Атомарная замена `dst` файлом `src` (один том): на Windows —
    семантика POSIX, где она недоступна — `os.replace`."""
    if not _WIN:
        os.replace(src, dst)
        return
    try:                                                   # pragma: no cover
        _win_posix_replace(Path(src), Path(dst))
    except OSError as e:                                   # pragma: no cover
        if getattr(e, "winerror", None) in _FALLBACK_WINERRORS:
            os.replace(src, dst)
        else:
            raise


def replace_json(path, text: str, *,
                 window_s: float = REPLACE_WINDOW_S) -> None:
    """Общий приём замены: временный файл `<имя>.tmp-<pid>` рядом →
    атомарная замена с окном ожидания. Байтами, не текстом: `write_text`
    на Windows переводил бы «\\n» в «\\r\\n», и форма файлов зависела бы
    от ОС (§9 п.6 схемы — LF везде)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp-{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    try:
        _replace_with_retry(tmp, p, window_s=window_s)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _replace_with_retry(src: Path, dst: Path, *, window_s: float) -> None:
    window = max(0.0, float(window_s))
    deadline = time.monotonic() + window
    attempt = 0
    while True:
        try:
            replace_file(src, dst)
            return
        except PermissionError as e:
            attempt += 1
            if time.monotonic() >= deadline:
                raise PermissionError(
                    e.errno,
                    f"замена файла {dst.name} не удалась за {window:g} с "
                    f"({attempt} попыток): файл держит другой читатель "
                    f"(антивирус, индексатор, чужой процесс); исходная "
                    f"ошибка: {e.strerror}", str(dst)) from e
            time.sleep(RETRY_PAUSE_S)


# --------------------------------------------- производные инварианты
def refresh_lexicon_order(order, families) -> list:
    """Порядок лексикона: прежний порядок ∩ семьи «есть» со словами-
    носителями ∪ новые (по id) в конец. Правило D02 (`validate.py`
    :507–517) и прежняя формула помощника копий тестов — одно место.
    Повторы прежнего порядка снимаются: D02 требует перестановки."""
    with_words = sorted({str(r.get("id")) for r in (families or [])
                         if isinstance(r, dict) and r.get("status") == "есть"
                         and isinstance(r.get("subject_words"), list)})
    present = set(with_words)
    out: list = []
    seen: set = set()
    for fid in (order or []):
        fid = str(fid)
        if fid in present and fid not in seen:
            seen.add(fid)
            out.append(fid)
    out += [fid for fid in with_words if fid not in seen]
    return out


def refresh_complements(families, *, before=None) -> list[str]:
    """X01: снятая запись уходит из дополнений; ДОБАВЛЕННОЕ дополнение
    дописывается второй стороне (§2.4 п.3 проекта шага 5).

    Добавление симметризуется, СНЯТИЕ дополнения — нет: пара «дополняют
    друг друга» — решение о ДВУХ записях, и писатель не станет решать за
    вторую. Односторонний убор даёт честный X01 по адресу второй семьи —
    правьте обе или не правьте ни одной. Исключение одно: запись «снята»
    убирается у всех, потому что её больше нет в живом составе.

    `before` — прежние записи по id (или прежние перечни дополнений):
    пара, которая БЫЛА, добавлением не считается. Снимок берётся сразу,
    до первой правки, поэтому вызывающему не нужно копировать записи.

    Записи правятся на месте; возврат — слова о том, что пересчитано
    (пустой перечень значит «ничего не изменилось»)."""
    words: list[str] = []
    by_id = {str(r.get("id")): r for r in (families or [])
             if isinstance(r, dict)}
    dropped = {fid for fid, r in by_id.items() if r.get("status") == "снята"}
    was = {str(fid): _complements_of(v)
           for fid, v in (before or {}).items()}
    for fid, r in by_id.items():
        comps = {str(c) for c in (r.get("complements") or [])}
        if fid in dropped:
            if comps:
                r["complements"] = []
                words.append(f"complements: у снятой families/{fid} "
                             f"дополнения очищены")
            continue
        keep = {c for c in comps if c in by_id and c not in dropped and c != fid}
        gone = sorted(comps - keep)
        r["complements"] = sorted(keep)
        if gone:
            words.append(f"complements: families/{fid} −" + ", −".join(gone))
    for fid, r in by_id.items():
        if fid in dropped:
            continue
        for c in sorted(r.get("complements") or []):
            other = by_id.get(c)
            if other is None or c in dropped:
                continue
            if fid in was.get(c, set()):
                continue                       # пара была — это не добавление
            comps = {str(x) for x in (other.get("complements") or [])}
            if fid not in comps:
                other["complements"] = sorted(comps | {fid})
                words.append(f"complements: families/{c} += {fid} "
                             f"(дополнение симметрично)")
    return words


def _complements_of(value) -> set:
    """Прежние дополнения записи: сама запись либо уже её перечень."""
    if isinstance(value, dict):
        value = value.get("complements")
    return {str(c) for c in (value or [])}


def refresh_unbound_params(dictionaries: dict, families) -> list[str]:
    """D03: имя, ставшее параметром или условием записи, уходит из
    `unbound_params` — так параметр обретает семью.

    Правая сторона правила — ровно то, что читает `validate._dict_checks`
    (:611–621): имена `params` и `conditions` СЕМЕЙ. Словарь правится на
    месте; возврат — слова о том, что убрано."""
    ub = dictionaries.get("unbound_params") if isinstance(dictionaries, dict) \
        else None
    if not isinstance(ub, dict):
        return []
    bound: set = set()
    for r in (families or []):
        if not isinstance(r, dict):
            continue
        for key in ("params", "conditions"):
            for p in r.get(key) or []:
                if isinstance(p, dict):
                    bound.add(str(p.get("name")))
    gone = sorted(n for n in ub if n in bound)
    for n in gone:
        ub.pop(n, None)
    return ["dictionaries.unbound_params −" + ", −".join(gone)] if gone else []


def refresh_body(families, dictionaries: dict, *, before=None) -> list[str]:
    """Производные инварианты ТЕЛА в одном месте: симметрия дополнений
    (X01), уход связанного имени из `unbound_params` (D03), порядок
    лексикона (D02) — в том же порядке, что у сборки службы.

    Зачем здесь, а не в службе (решение 0.8 пакета паритета): временный
    каталог кандидата обязан быть равен тому, что позже соберёт служба
    из этого же кандидата. Пока инварианты жили только в
    `catalog_service/store.py`, кандидат, связавший имя из
    `unbound_params`, был красен D03 на временном каталоге и зелен у
    службы — два закона на один переход.

    Порядок семей (`refresh_families_order`) и старая ревизия шапки
    (`legacy_registry_rev`) сюда не входят: они живут не в теле, а в
    переходном носителе и в шапке — их зовут отдельно, тем же писателем.

    Записи и словари правятся на месте; возврат — слова о пересчитанном."""
    words = refresh_complements(families, before=before)
    words += refresh_unbound_params(dictionaries, families)
    old = list(dictionaries.get("lexicon_order") or [])
    new = refresh_lexicon_order(old, families)
    dictionaries["lexicon_order"] = new
    if new != old:
        words.append("dictionaries.lexicon_order пересчитан по семьям "
                     "«есть» со словами-носителями (D02)")
    return words


def refresh_families_order(order, present_ids) -> list:
    """Переходный порядок семей: прежний порядок ∩ present ∪ новые (по
    id) в конец. Дверь COSMOS требует равенства множеств."""
    present = sorted({str(x) for x in (present_ids or [])})
    known = set(present)
    out: list = []
    seen: set = set()
    for fid in (order or []):
        fid = str(fid)
        if fid in known and fid not in seen:
            seen.add(fid)
            out.append(fid)
    out += [fid for fid in present if fid not in seen]
    return out


def legacy_registry_rev(families_map: dict) -> str:
    """Старая 12-значная ревизия реестра — формула гейта L10
    (`tests/test_template_catalog_parity.py`): sha256 канонической
    сериализации карты `Catalog.families()`, первые 12 знаков."""
    text = json.dumps(families_map, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_header(base: dict, *, version: str, released_by: str,
                 released_at: str, note: str, body: dict,
                 legacy_registry_rev: str | None) -> dict:
    """Копия шапки `base` с новыми полями; `fingerprint` считается из
    тела (H02 сходится всегда). `legacy_registry_rev=None` пишет в поле
    null (схема его допускает); чтобы сохранить прежнее значение,
    передайте `base.get("legacy_registry_rev")`."""
    head = _copy_header(base)
    head["version"] = str(version)
    head["released_by"] = str(released_by)
    head["released_at"] = str(released_at)
    head["note"] = str(note)
    head["legacy_registry_rev"] = legacy_registry_rev
    head["fingerprint"] = _fingerprint(body)
    return head


def _copy_header(base) -> dict:
    """Глубокая копия шапки: писатель не мутирует того, что ему дали, и
    не берёт в файл значений, которых JSON не знает."""
    if not isinstance(base, dict):
        raise CatalogWriteError("шапка каталога — объект JSON")
    try:
        return json.loads(json.dumps(base, ensure_ascii=False))
    except (TypeError, ValueError) as e:
        raise CatalogWriteError(
            f"шапка каталога не сериализуется: {e}") from None


def _fingerprint(body: dict) -> str:
    try:
        return canon.fingerprint_of(body)
    except ValueError as e:
        raise CatalogWriteError(f"отпечаток не вычислим — {e}") from None


# ------------------------------------------------------------ запись
def write_catalog(folder, *, header: dict, families: list, criteria: list,
                  dictionaries: dict, requests: list, versions: list,
                  families_order: list, schema_copy: bool = True,
                  forbidden_terms=(),
                  window_s: float = REPLACE_WINDOW_S) -> Catalog:
    """Записать каталог целиком: суд до диска, файлы в форме читателя,
    `catalog.json` последним. Возврат — `load(folder)` (новый экземпляр,
    прочитанный с диска, а не то, что было в памяти)."""
    folder = Path(folder)
    body = {"families": list(families or []), "criteria": list(criteria or []),
            "dictionaries": {k: v for k, v in (dictionaries or {}).items()
                             if k != "form_version"}}
    try:
        normed = canon.normalize(body)
    except ValueError as e:
        raise CatalogWriteError(f"канон не строится — {e}") from None
    head = _copy_header(header)
    head["fingerprint"] = _fingerprint(normed)
    docs = {"families": {"form_version": 1, "families": normed["families"]},
            "criteria": {"form_version": 1, "criteria": normed["criteria"]},
            "dictionaries": {"form_version": 1, **normed["dictionaries"]},
            "requests": {"form_version": 1, "requests": list(requests or [])}}
    vdoc = {"form_version": 1, "versions": list(versions or [])}
    candidate = Catalog(path=folder, header=head, docs=docs, versions_doc=vdoc,
                        single_file=True)
    problems = candidate.validate(forbidden_terms=forbidden_terms)
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        head_words = "; ".join(str(p) for p in errors[:5])
        more = f" (и ещё {len(errors) - 5})" if len(errors) > 5 else ""
        raise CatalogWriteError(
            f"каталог не записан: проверка дала {len(errors)} ошибок — "
            f"{head_words}{more}", problems)
    sections = head.get("sections") if isinstance(head.get("sections"), dict) \
        else {}
    plan: list = []
    for sec in ("families", "criteria", "dictionaries", "requests"):
        name = sections.get(sec)
        name = name if isinstance(name, str) and name \
            else DEFAULT_SECTION_FILES[sec]
        plan.append((name, dump_json(docs[sec])))
    plan.append((VERSIONS_FILE, dump_json(vdoc)))
    plan.append((ORDER_FILE, dump_json(list(families_order or []))))
    if schema_copy:
        selected_schema = schema_path_for_version(head.get("version"))
        plan.append((SCHEMA_COPY,
                     selected_schema.read_text(encoding="utf-8")
                     .replace("\r\n", "\n")))
        # Keep the established compatibility filename, but identify which
        # contract it contains without requiring a reader to inspect the schema.
        plan.append(("schema/contract.json", dump_json({
            "catalog_version": head.get("version"),
            "schema_contract": selected_schema.name,
            "schema_file": Path(SCHEMA_COPY).name})))
    # Шапка — последней: пока она старая, читатель видит прежнюю версию;
    # её замена и есть момент, когда каталог стал новым.
    plan.append((HEADER_FILE, dump_json(head)))
    for rel, text in plan:
        replace_json(folder / rel, text, window_s=window_s)
    return load(folder)


def write_requests(folder, requests: list, *,
                   window_s: float = REPLACE_WINDOW_S) -> None:
    """Только `requests.json` (заявки в отпечаток не входят — шапка не
    трогается, отпечаток каталога прежний)."""
    if not isinstance(requests, list):
        raise CatalogWriteError("заявки — список записей")
    folder = Path(folder)
    name = _section_files(folder)["requests"]
    replace_json(folder / name,
                 dump_json({"form_version": 1, "requests": list(requests)}),
                 window_s=window_s)


def _read_header(folder: Path) -> dict:
    """Шапка каталога с диска — только чтобы узнать имена файлов
    разделов; нечитаемая шапка даёт умолчания, а не отказ (её негодность
    судит читатель при следующем `load`)."""
    try:
        head = json.loads((folder / HEADER_FILE).read_text(
            encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return head if isinstance(head, dict) else {}


def _section_files(folder: Path) -> dict:
    """Имена файлов разделов, объявленные шапкой на диске (иначе
    умолчания)."""
    sections = _read_header(folder).get("sections")
    sections = sections if isinstance(sections, dict) else {}
    out = {}
    for sec, default in DEFAULT_SECTION_FILES.items():
        name = sections.get(sec)
        out[sec] = name if isinstance(name, str) and name else default
    return out


def _release_files(folder: Path) -> tuple:
    """Шесть файлов читателя — их несёт снимок выпуска."""
    named = _section_files(folder)
    return (HEADER_FILE, named["families"], named["criteria"],
            named["dictionaries"], named["requests"], VERSIONS_FILE)


def snapshot_release(folder, version: str) -> Path:
    """Неизменяемая копия выпуска: шесть файлов читателя + `schema/` +
    `transition/families_order.json` → `<folder>/releases/<version>/`.
    Папка уже есть — CatalogWriteError (выпуски не переписываются)."""
    folder = Path(folder)
    dst = folder / "releases" / str(version)
    if dst.exists():
        raise CatalogWriteError(
            f"папка выпуска releases/{version} уже есть: выпуски "
            f"неизменяемы — перезапись не делается")
    tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        (tmp / "schema").mkdir(parents=True, exist_ok=True)
        (tmp / "transition").mkdir(parents=True, exist_ok=True)
        for rel in _release_files(folder):
            src = folder / rel
            if not src.is_file():
                raise CatalogWriteError(
                    f"снимок выпуска {version} не сделан: файла {rel} нет")
            shutil.copyfile(src, tmp / rel)
        for rel in (SCHEMA_COPY, ORDER_FILE):
            src = folder / rel
            if src.is_file():
                shutil.copyfile(src, tmp / rel)
        os.replace(tmp, dst)
    except CatalogWriteError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise CatalogWriteError(
            f"снимок выпуска {version} не сделан: {e}") from None
    return dst
