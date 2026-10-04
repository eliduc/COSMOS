"""Read-only release-history checks, independent of release side effects.

An immutable snapshot carries the schema of its time.  Only a snapshot whose
embedded form matches the reader selected for its version is judged by today's
semantic rules.  Identity, fingerprints, journal and counted changes are always
checked; a missing schema is not evidence of a different historical form.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import diff
from .errors import CatalogError, CatalogFormatError
from .manifest import parse_version
from .reader import load
from .validate import schema_path_for_version, _uses_schema_070


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_UNAVAILABLE = object()


def _schema_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
        schema = json.loads(text)
    except (OSError, UnicodeError, ValueError) as error:
        raise CatalogFormatError("схема формы выпуска не читается: "
                                 + type(error).__name__) from None
    if not isinstance(schema, dict) or not isinstance(schema.get("$defs"), dict) \
            or not schema["$defs"]:
        raise CatalogFormatError("схема формы выпуска не содержит определения $defs")
    # Checkout line endings are not a change of the historical record form.
    return text.splitlines()


def same_schema_form(snapshot) -> bool:
    """Compare embedded form with the reader for this exact declared version.

    This is not schema authentication or a release/owner-approval decision.
    It relies on the immutable snapshot boundary, like the T18 contract.
    """
    embedded = Path(snapshot.path) / "schema" / "catalog.schema.json"
    return _schema_lines(embedded) == _schema_lines(
        schema_path_for_version(snapshot.version()))


def _change_triples(changes) -> set[tuple[str, str, str]]:
    if not isinstance(changes, list):
        raise ValueError("правки журнала не список")
    triples = set()
    for change in changes:
        if not isinstance(change, dict) or any(
                not isinstance(change.get(key), str) or not change[key]
                for key in ("section", "id", "kind")):
            raise ValueError("правка журнала без section/id/kind")
        triples.add((change["section"], change["id"], change["kind"]))
    return triples


def release_history(catalog) -> list[dict]:
    """One honest result per journal entry; no services, writes or subprocesses."""
    results = []
    base = Path(catalog.path) / "releases"
    previous = None  # Only the first actual release is compared with no body.
    previous_version = None
    named = set()
    for index, entry in enumerate(catalog.versions()):
        if not isinstance(entry, dict):
            results.append({"version": f"<{index + 1}>", "ok": False,
                            "words": "запись журнала версий не объект"})
            previous = _UNAVAILABLE
            continue
        version = str(entry.get("version"))
        words = []
        try:
            triple, draft = parse_version(version)
            if draft is not None or version != ".".join(str(n) for n in triple):
                raise ValueError("журнал содержит не номер выпуска")
        except ValueError:
            results.append({"version": version, "ok": False,
                            "words": "номер выпуска в журнале не release semver"})
            previous = _UNAVAILABLE
            continue
        if previous_version is not None and triple <= previous_version:
            words.append("версии журнала не строго возрастают")
        previous_version = triple
        named.add(version)
        folder = base / version
        if not folder.is_dir():
            results.append({"version": version, "ok": False,
                            "words": f"папки releases/{version} нет"})
            previous = _UNAVAILABLE
            continue
        try:
            snapshot = load(folder)
            body = snapshot.body()
            computed = snapshot.fingerprint()
        except (CatalogError, OSError, ValueError, TypeError, KeyError) as error:
            results.append({"version": version, "ok": False,
                            "words": "папка выпуска не читается: "
                            + type(error).__name__})
            previous = _UNAVAILABLE
            continue
        if snapshot.version() != version:
            words.append(f"версия папки {snapshot.version()} ≠ {version}")
        if not _SHA256.fullmatch(str(entry.get("fingerprint") or "")):
            words.append("отпечаток журнала не 64 hex")
        if computed != entry.get("fingerprint"):
            words.append("отпечаток папки не равен записи журнала")
        if computed != snapshot.declared_fingerprint():
            words.append("вычисленный отпечаток папки не равен шапке выпуска")
        if not _COMMIT.fullmatch(str(entry.get("code") or "")):
            words.append("поле code журнала не срез кода")
        own_journal = snapshot.versions()
        if not own_journal or own_journal[-1] != entry:
            words.append("последняя запись журнала папки не равна полной записи журнала каталога")
        try:
            declared_changes = _change_triples(entry.get("changes"))
            if previous is _UNAVAILABLE:
                words.append("разница выпусков не вычислена: предыдущий снимок недоступен")
            else:
                actual_changes = {(change.section, change.id, change.kind)
                                  for change in diff.diff_bodies(previous, body)}
                if declared_changes != actual_changes:
                    words.append("правки журнала не равны разнице тел: "
                                 f"приписано {len(declared_changes - actual_changes)}, "
                                 f"пропущено {len(actual_changes - declared_changes)}")
        except (CatalogError, ValueError, TypeError, KeyError) as error:
            words.append("разница выпусков не проверена: " + type(error).__name__)
        try:
            if same_schema_form(snapshot):
                problems = [p for p in snapshot.validate() if p.severity == "error"]
                if problems:
                    words.append(f"проверка папки дала {len(problems)} ошибок")
            elif _uses_schema_070(snapshot.version()):
                # A new-format release cannot claim the historical exemption
                # by substituting its embedded schema with an older form.
                words.append("схема нового выпуска не равна контракту его версии")
        except CatalogError as error:
            words.append(str(error))
        results.append({"version": version, "ok": not words,
                        "words": "; ".join(words) or "сошлось"})
        previous = body
    try:
        extra = sorted(path.name for path in base.iterdir()
                       if path.is_dir() and path.name not in named) if base.is_dir() else []
    except OSError:
        results.append({"version": "<releases>", "ok": False,
                        "words": "перечень папок выпусков не читается"})
    else:
        for version in extra:
            results.append({"version": version, "ok": False,
                            "words": "папка выпуска отсутствует в журнале каталога"})
    return results
