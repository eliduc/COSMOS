# -*- coding: utf-8 -*-
"""Читатель каталога шаблонов: load() и класс Catalog (только чтение).

Побочных эффектов нет: ничего не пишет, переменных среды не читает,
глобального кэша нет; при импорте файлы не читаются. Кривой JSON и дубль
ключа — CatalogFormatError при load(); отсутствующий раздел —
CatalogNotFound; отсутствующее поле записи — не подставляется, а
судится validate() (S02).
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from . import canon
from .errors import (CatalogError, CatalogFormatError, CatalogInvalid,
                     CatalogNotFound, UnknownRecord)

DEFAULT_CATALOG_DIR: Path = Path(__file__).resolve().parent.parent / "catalog"
SECTION_FILES = ("catalog.json", "families.json", "criteria.json",
                 "dictionaries.json", "requests.json", "versions.json")
#: Свидетельство поддержки записей (K06): файл ВНЕ отпечатка и вне
#: перечня разделов — каталог без него полноценен, а пишет его только
#: служба каталога (`Catalog.support()`).
SUPPORT_FILE = "support.json"
FINGERPRINT_SECTIONS = canon.FINGERPRINT_SECTIONS

_DEFAULT_SECTIONS = {"families": "families.json", "criteria": "criteria.json",
                     "dictionaries": "dictionaries.json",
                     "requests": "requests.json"}
_ROOT_KEY = {"families": "families", "criteria": "criteria",
             "dictionaries": None, "requests": "requests",
             "versions": "versions"}
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-draft\.([1-9]\d*))?$")
#: Состояния заявки, при которых черновик виден как запись «заявлен».
REQUEST_PENDING_STATES = ("подана", "принята", "исполнитель заявлен")
STATUS_ALL = None


def _pairs_hook(label: str):
    def hook(pairs):
        out: dict = {}
        for k, v in pairs:
            if k in out:
                raise CatalogFormatError(
                    f"{label}: дубль ключа {k!r} в объекте JSON")
            out[k] = v
        return out
    return hook


def parse_json_text(text: str, label: str):
    """JSON из текста; кривой текст — CatalogFormatError словами."""
    try:
        return json.loads(text, object_pairs_hook=_pairs_hook(label))
    except json.JSONDecodeError as e:
        raise CatalogFormatError(
            f"{label}: кривой JSON — {e.msg} (строка {e.lineno}, "
            f"позиция {e.colno})") from None


def read_json_file(path: Path):
    """Файл JSON (UTF-8, BOM терпится); нет файла — CatalogNotFound."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise CatalogNotFound(f"файла нет: {path}") from None
    except OSError as e:
        raise CatalogFormatError(f"{path}: не читается: {e}") from None
    except UnicodeDecodeError as e:
        raise CatalogFormatError(
            f"{path}: не UTF-8 (позиция {e.start})") from None
    return parse_json_text(text, str(path))


def _require_object(doc, label: str, root_key: str | None) -> None:
    if not isinstance(doc, dict):
        raise CatalogFormatError(f"{label}: корень файла не объект JSON")
    if "form_version" not in doc:
        raise CatalogFormatError(f"{label}: нет поля form_version")
    if root_key and not isinstance(doc.get(root_key), list):
        raise CatalogFormatError(
            f"{label}: нет корневого ключа {root_key!r} со списком записей")


def _resolve_version(version: str) -> Path:
    head = DEFAULT_CATALOG_DIR / "catalog.json"
    current = None
    if head.is_file():
        try:
            current = str((read_json_file(head) or {}).get("version"))
        except CatalogError:
            current = None
    if current == version:
        return DEFAULT_CATALOG_DIR
    rel = DEFAULT_CATALOG_DIR / "releases" / version
    if rel.is_dir():
        return rel
    raise CatalogNotFound(
        f"версии каталога шаблонов {version!r} нет: текущий каталог "
        f"{DEFAULT_CATALOG_DIR} несёт версию {current!r}, папки "
        f"{rel} нет")


def load(source: str | os.PathLike | None = None, *,
         strict: bool = False) -> "Catalog":
    """Каталог по источнику: None — папка по умолчанию; путь к папке;
    путь к catalog.json; путь к единому файлу выгрузки; строка semver —
    текущий каталог, если версия совпадает, иначе releases/<версия>."""
    if source is None:
        base = DEFAULT_CATALOG_DIR
    elif isinstance(source, str) and SEMVER_RE.match(source):
        base = _resolve_version(source)
    else:
        p = Path(source)
        if p.is_dir():
            base = p
        elif p.is_file():
            if p.name == "catalog.json":
                base = p.parent
            else:
                return _load_single(p, strict=strict)
        else:
            raise CatalogNotFound(f"каталога шаблонов нет по пути {p}")
    if not base.is_dir():
        raise CatalogNotFound(f"папки каталога шаблонов нет: {base}")
    head = base / "catalog.json"
    if not head.is_file():
        raise CatalogNotFound(f"в папке {base} нет шапки catalog.json")
    header = read_json_file(head)
    _require_object(header, str(head), None)
    if not isinstance(header.get("version"), str) or not header["version"]:
        raise CatalogFormatError(f"{head}: шапка без поля version")
    sections = header.get("sections")
    sections = sections if isinstance(sections, dict) else {}
    docs: dict = {}
    for sec, default in _DEFAULT_SECTIONS.items():
        fname = sections.get(sec)
        fname = fname if isinstance(fname, str) and fname else default
        p = base / fname
        if not p.is_file():
            raise CatalogNotFound(
                f"раздела {sec!r} нет: файл {p} отсутствует")
        doc = read_json_file(p)
        _require_object(doc, str(p), _ROOT_KEY[sec])
        docs[sec] = doc
    vpath = base / "versions.json"
    vdoc = None
    if vpath.is_file():
        vdoc = read_json_file(vpath)
        _require_object(vdoc, str(vpath), "versions")
    cat = Catalog(path=base, header=header, docs=docs, versions_doc=vdoc)
    if strict:
        cat.raise_if_invalid()
    return cat


def _load_single(path: Path, *, strict: bool) -> "Catalog":
    doc = read_json_file(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("catalog"), dict) \
            or not isinstance(doc.get("data"), dict):
        raise CatalogFormatError(
            f"{path}: не единый файл выгрузки каталога (нужны объекты "
            f"catalog и data)")
    header = doc["catalog"]
    if not isinstance(header.get("version"), str) or not header["version"]:
        raise CatalogFormatError(f"{path}: шапка без поля version")
    data = doc["data"]
    docs = {}
    for sec in ("families", "criteria"):
        recs = data.get(sec)
        if not isinstance(recs, list):
            raise CatalogFormatError(
                f"{path}: в data нет раздела {sec!r} со списком записей")
        docs[sec] = {"form_version": 1, sec: recs}
    dicts = data.get("dictionaries")
    if not isinstance(dicts, dict):
        raise CatalogFormatError(f"{path}: в data нет раздела dictionaries")
    docs["dictionaries"] = {"form_version": 1, **dicts}
    # Заявки и журнал версий обязательны и в едином файле (§9 п.1: читатель
    # ничего не подставляет) — иначе испорченная выгрузка теряла бы их
    # без следа.
    for key in ("requests", "versions"):
        if not isinstance(doc.get(key), list):
            raise CatalogFormatError(
                f"{path}: в едином файле нет списка {key!r} (заявки и журнал "
                f"версий обязательны, пустые — [])")
    docs["requests"] = {"form_version": 1, "requests": doc["requests"]}
    vdoc = {"form_version": 1, "versions": doc["versions"]}
    cat = Catalog(path=path, header=header, docs=docs, versions_doc=vdoc,
                  single_file=True)
    if strict:
        cat.raise_if_invalid()
    return cat


class Catalog:
    """Каталог шаблонов в памяти: только чтение, копии наружу."""

    def __init__(self, *, path: Path, header: dict, docs: dict,
                 versions_doc: dict | None, single_file: bool = False):
        self.path = Path(path)
        self.header = copy.deepcopy(header)
        self.single_file = single_file
        self._docs = {"header": header, **docs, "versions": versions_doc}
        self._families = list(docs["families"]["families"])
        self._criteria = list(docs["criteria"]["criteria"])
        self._dictionaries = dict(docs["dictionaries"])
        self._requests = list(docs["requests"]["requests"])
        self._versions = list((versions_doc or {}).get("versions") or [])
        self._fingerprint: str | None = None
        #: Память вердикта validate() этого экземпляра (Ш2 шага 7):
        #: {перечень маркеров: (проблемы, отказ | None)}.
        self._verdict: dict = {}

    # ---------------------------------------------------------- шапка
    def version(self) -> str:
        return str(self.header.get("version"))

    def is_draft(self) -> bool:
        m = SEMVER_RE.match(self.version())
        return bool(m and m.group(4))

    def declared_fingerprint(self) -> str:
        return str(self.header.get("fingerprint") or "")

    def body(self) -> dict:
        """Тело канона §9 (копия): families, criteria, dictionaries."""
        return {"families": copy.deepcopy(self._families),
                "criteria": copy.deepcopy(self._criteria),
                "dictionaries": {k: copy.deepcopy(v) for k, v
                                 in self._dictionaries.items()
                                 if k != "form_version"}}

    def fingerprint(self) -> str:
        """Отпечаток, ВЫЧИСЛЕННЫЙ из данных (лениво, кэш в экземпляре)."""
        if self._fingerprint is None:
            try:
                self._fingerprint = canon.fingerprint_of(self.body())
            except ValueError as e:
                raise CatalogFormatError(
                    f"{self.path}: отпечаток не вычислим — {e}") from None
        return self._fingerprint

    def canonical_json(self) -> str:
        try:
            return canon.canonical_json(self.body())
        except ValueError as e:
            raise CatalogFormatError(
                f"{self.path}: канон не строится — {e}") from None

    # --------------------------------------------------------- записи
    @staticmethod
    def _select(records, status):
        if status is STATUS_ALL:
            return [r for r in records if isinstance(r, dict)]
        wanted = set(status)
        return [r for r in records
                if isinstance(r, dict) and r.get("status") in wanted]

    def families(self, status=("есть",)) -> dict[str, list[str]]:
        """{семья: отсортированные имена параметров} — форма реестра."""
        out: dict[str, list[str]] = {}
        for r in sorted(self._select(self._families, status),
                        key=lambda r: str(r.get("id"))):
            out[str(r.get("id"))] = sorted(
                str(p.get("name")) for p in (r.get("params") or [])
                if isinstance(p, dict))
        return out

    def families_meta(self, status=("есть",)) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in sorted(self._select(self._families, status),
                        key=lambda r: str(r.get("id"))):
            out[str(r.get("id"))] = {
                "params": sorted(str(p.get("name"))
                                 for p in (r.get("params") or [])
                                 if isinstance(p, dict)),
                "kind": r.get("kind"), "zone": r.get("zone"),
                "class": r.get("class"),
                "violation_unit": r.get("violation_unit"),
                "violation_size": r.get("violation_size"),
                "nature": r.get("nature"),
                "nature_confirmed_by": r.get("nature_confirmed_by"),
                "reads_history": copy.deepcopy(r.get("reads_history")),
                "subject_words": copy.deepcopy(r.get("subject_words")),
                "complements": list(r.get("complements") or []),
            }
        return out

    def families_full(self, status=("есть",), *,
                      include_requests: bool = False) -> list[dict]:
        out = [copy.deepcopy(r) for r in
               sorted(self._select(self._families, status),
                      key=lambda r: str(r.get("id")))]
        if include_requests:
            for rq in self._requests:
                if not isinstance(rq, dict) or rq.get("section") != "families":
                    continue
                if rq.get("state") not in REQUEST_PENDING_STATES:
                    continue
                draft = rq.get("draft")
                if not isinstance(draft, dict):
                    continue
                rec = copy.deepcopy(draft)
                rec["status"] = "заявлен"
                rec["history"] = []
                rec.setdefault("stand_tasks", [])
                rec["request_id"] = rq.get("request_id")
                out.append(rec)
        return out

    def family(self, id: str) -> dict:
        for r in self._families:
            if isinstance(r, dict) and r.get("id") == id:
                return copy.deepcopy(r)
        known = ", ".join(sorted(str(r.get("id")) for r in self._families
                                 if isinstance(r, dict)))
        raise UnknownRecord(f"в каталоге {self.version()} нет семьи "
                            f"{id!r}; есть: {known}")

    def params(self, status=("есть",)) -> dict[str, dict]:
        """{имя: unit, relation, qualifier, family, family_status, source,
        tz_required, note} — параметры семей заданных статусов ∪
        unbound_params словарей."""
        out: dict[str, dict] = {}
        for r in self._select(self._families, status):
            for p in r.get("params") or []:
                if not isinstance(p, dict):
                    continue
                out[str(p.get("name"))] = {
                    "unit": p.get("unit"), "relation": p.get("relation"),
                    "qualifier": p.get("qualifier"),
                    "family": r.get("id"), "family_status": r.get("status"),
                    "source": "семья",
                    "tz_required": p.get("unit") == "time_of_day_minutes",
                    "note": p.get("note")}
        unbound = self._dictionaries.get("unbound_params")
        if isinstance(unbound, dict):
            for name, spec in unbound.items():
                if name in out or not isinstance(spec, dict):
                    continue
                out[str(name)] = {
                    "unit": spec.get("unit"), "relation": spec.get("relation"),
                    "qualifier": None, "family": None, "family_status": None,
                    "source": spec.get("source"),
                    "tz_required": spec.get("unit") == "time_of_day_minutes",
                    "note": spec.get("note")}
        return dict(sorted(out.items()))

    def conditions(self, status=("есть",)) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self._select(self._families, status):
            for c in r.get("conditions") or []:
                if isinstance(c, dict):
                    out[str(c.get("name"))] = {
                        "family": r.get("id"), "unit": c.get("unit"),
                        "relation": c.get("relation"), "note": c.get("note")}
        return dict(sorted(out.items()))

    def subjects(self, family: str) -> dict[str, tuple[str, ...]]:
        """Слова предметов параметров; не family.subject_words и не note.

        Отсутствующее необязательное поле не создаёт запись. Общая фраза
        сохраняется у каждого параметра: неоднозначность решает разбор,
        а читатель не выбирает первый параметр. Данные каталога не меняются.
        """
        record = self.family(family)
        out = {}
        for parameter in record.get("params") or []:
            if not isinstance(parameter, dict):
                raise CatalogFormatError(f"семья {family!r}: параметр не объект")
            name = parameter.get("name")
            words = parameter.get("subject_words")
            if words is None:
                continue
            where = f"семья {family!r}, параметр {name!r}, subject_words"
            if not isinstance(name, str) or not name.strip():
                raise CatalogFormatError(f"{where}: имя параметра не задано")
            if (not isinstance(words, list) or not words
                    or any(not isinstance(word, str) or not word.strip() for word in words)):
                raise CatalogFormatError(f"{where}: нужен непустой список непустых слов")
            if name in out:
                raise CatalogFormatError(f"{where}: повтор имени параметра")
            out[name] = tuple(words)
        if not out:
            raise CatalogFormatError(
                f"семья {family!r}: в каталоге {self.version()} нет словаря предметов параметров")
        return out

    def lexicon(self, status=("есть",)) -> dict:
        words: dict[str, list] = {}
        comps: dict[str, list] = {}
        for r in sorted(self._select(self._families, status),
                        key=lambda r: str(r.get("id"))):
            fid = str(r.get("id"))
            if isinstance(r.get("subject_words"), list):
                words[fid] = list(r["subject_words"])
            if r.get("complements"):
                comps[fid] = sorted(str(c) for c in r["complements"])
        return {"subject_words": words, "complements": comps,
                "order": list(self._dictionaries.get("lexicon_order") or [])}

    def criteria(self, status=("есть",)) -> dict[str, dict]:
        return {str(r.get("id")): copy.deepcopy(r)
                for r in sorted(self._select(self._criteria, status),
                                key=lambda r: str(r.get("id")))}

    def criterion(self, id: str) -> dict:
        for r in self._criteria:
            if isinstance(r, dict) and r.get("id") == id:
                return copy.deepcopy(r)
        known = ", ".join(sorted(str(r.get("id")) for r in self._criteria
                                 if isinstance(r, dict)))
        raise UnknownRecord(f"в каталоге {self.version()} нет критерия "
                            f"{id!r}; есть: {known}")

    def area_param_names(self, status=("есть",)) -> list[str]:
        """Вычисленный перечень: имена параметров с уточнителем area."""
        names = set()
        for r in self._select(self._families, status):
            for p in r.get("params") or []:
                if isinstance(p, dict) and p.get("qualifier") == "area":
                    names.add(str(p.get("name")))
        return sorted(names)

    def dictionaries(self) -> dict:
        out = {k: copy.deepcopy(v) for k, v in self._dictionaries.items()
               if k != "form_version"}
        out["area_param_names"] = self.area_param_names()
        return out

    def check_templates(self) -> dict:
        return copy.deepcopy(self._dictionaries.get("check_templates") or {})

    def requests(self) -> list[dict]:
        return copy.deepcopy(self._requests)

    def support(self) -> dict | None:
        """Свидетельство поддержки записей — `<каталог>/support.json`.

        Файл ВНЕ отпечатка (как `requests.json`) и пишет его только
        служба каталога одной функцией
        (`template_catalog.bindings.support_table`); здесь он ЧИТАЕТСЯ —
        и CDPM, и дверью COSMOS, и самой службой, чтобы «исполнимо»
        выводилось у всех троих одинаково (П0 §3.5, сторона COSMOS 4.4).

        Файла нет — `None` («свидетельства не вычислено»), а не пустая
        таблица: «ячейки нет» и «ячейка со словом нет» — разные ответы,
        и подменять первый вторым значило бы объявить непроверенное
        проверенным. Единый файл выгрузки свидетельства не несёт.

        Читается ПРИ ВЫЗОВЕ, а не при `load()`: каталог обязан грузиться
        и без свидетельства, а сам файл несёт свою шапку `catalog`
        (версия и отпечаток), по которой читатель и судит, тому ли
        каталогу оно написано — сверять его свежесть по времени чтения
        никому не нужно.

        Носитель судится ЗДЕСЬ и по одному закону
        (`bindings.support_form_problems`): неизвестная версия схемы и
        иная форма — отказ читателя словами, а не «годное
        свидетельство». Общий закон 5 П0 требует этого от всякого
        носителя, а у этого читателей трое (служба, дверь COSMOS, CDPM),
        и fail-open унаследовали бы все трое.

        Чужой ОТПЕЧАТОК отказом не является: файл, написанный для
        другого каталога, — годный носитель с негодным для нас
        содержанием, и об этом говорит `bindings.support_of`
        («ячейки показаны, исполнимостью не считаются»)."""
        if self.single_file:
            return None
        path = self.path / SUPPORT_FILE
        if not path.is_file():
            return None
        doc = read_json_file(path)
        from .bindings import support_form_problems
        problems = support_form_problems(doc)
        if problems:
            raise CatalogFormatError(f"{path}: " + "; ".join(problems))
        return doc

    def request(self, request_id: str) -> dict | None:
        """Заявка по номеру копией; такой заявки нет — None. Отказа тут
        нет намеренно: «заявки с этим номером не заведено» — законный
        ответ продукту, который ищет свой пробел (шаг 6, §2.1)."""
        for rq in self._requests:
            if isinstance(rq, dict) and rq.get("request_id") == request_id:
                return copy.deepcopy(rq)
        return None

    def versions(self) -> list[dict]:
        return copy.deepcopy(self._versions)

    # ------------------------------------------------------- проверка
    def validate(self, *, forbidden_terms=()) -> list:
        from .validate import validate_catalog
        return validate_catalog(self, forbidden_terms=forbidden_terms)

    def ensure_valid(self, *, forbidden_terms=()) -> list:
        """Годность этого экземпляра — ОДИН РАЗ на экземпляр (шаг 7,
        Ш2). Годный каталог отдаёт перечень проблем (у годного там одни
        предупреждения); негодный при каждом обращении бросает ТОТ ЖЕ
        `CatalogInvalid` теми же словами — дверь COSMOS и мост CDPM
        зовут одно и отказывают одинаково, а повторная проверка на
        каждое обращение перестаёт быть классом зависаний.

        Память вердикта живёт на экземпляре: правка файлов на диске
        даёт НОВЫЙ экземпляр у `current()` — и новую проверку."""
        key = tuple(str(t) for t in (forbidden_terms or ()))
        memo = getattr(self, "_verdict", None)
        if memo is None:
            memo = {}
            self._verdict = memo
        got = memo.get(key)
        if got is not None:
            problems, exc = got
            if exc is not None:
                raise exc
            return list(problems)
        problems = self.validate(forbidden_terms=forbidden_terms)
        errors = [p for p in problems if p.severity == "error"]
        exc = None
        if errors:
            head = "; ".join(str(p) for p in errors[:5])
            more = f" (и ещё {len(errors) - 5})" if len(errors) > 5 else ""
            exc = CatalogInvalid(
                f"каталог {self.path} не проходит проверку: {head}{more}",
                problems)
        memo[key] = (list(problems), exc)
        if exc is not None:
            raise exc
        return list(problems)

    def raise_if_invalid(self, *, forbidden_terms=()) -> None:
        """Те же слова отказа, что у `ensure_valid()` — закон один."""
        self.ensure_valid(forbidden_terms=forbidden_terms)


def validate_path(source, *, forbidden_terms=()) -> list:
    """Обёртка для проверки установки: ошибки load() — Problem S01,
    исключений не бросает (любая иная ошибка — тоже Problem S01 словами,
    а не трассировка)."""
    from .validate import Problem
    try:
        cat = load(source)
    except CatalogError as e:
        return [Problem("catalog", "S01", str(e), "error")]
    except Exception as e:                                   # noqa: BLE001
        return [Problem("catalog", "S01",
                        f"загрузка прервана внутренней ошибкой: "
                        f"{type(e).__name__}: {e}", "error")]
    return cat.validate(forbidden_terms=forbidden_terms)
