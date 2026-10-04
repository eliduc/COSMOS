"""Единый неизменяемый манифест ПОСТАНОВКИ прогона (аудит R10, P0-03).

До него личность постановки была размазана: заморозка конфигурации несла
три домена, подпись построителя — ручки поиска, каталог ехал на этап 2
голым путём, а хэш сборки решателя жил только в /versionz. Аудитор
показал следствие: два прогона с разными базовыми каталогами получали
одну подпись, и исторические пулы разных постановок смешивались.

Манифест создаётся РОВНО ОДИН раз при постановке задачи (jobs.start_job),
после заморозки конфигурации, и содержит контент-хэши всего, что образует
задачу: набора данных, правил источника, ресурсных правил, базового и
пин-каталогов, входного каталога этапа 2, отпечатков доменов заморозки,
сборки решателя и фактических параметров команд. Его идентификатор —
sha256 канонического тела; файл пишется с O_EXCL и не переписывается.

Дочерние процессы получают путь и идентификатор через окружение
(CREW_PROBLEM_MANIFEST / CREW_PROBLEM_MANIFEST_ID), штампуют идентификатор
в свои артефакты (config_sig каталога, манифест ростера), а этап 2 и
сертификаты сверяют цепочку: каталог, построенный под одной постановкой,
не принимается прогоном другой.

Хэш каждого файла считается от ОДНОГО чтения байтов; если команда уже
несёт отпечаток этого файла (--source-rules-fp и т.п.), несовпадение с
только что прочитанными байтами — отказ постановки, а не тихая замена.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import registry

SCHEMA = "cosmos-problem-manifest/v1"
FILENAME = "problem-manifest.json"

# Флаг команды -> имя записи в манифесте. Все файлы, образующие постановку.
_FILE_FLAGS = {
    "--payload": "payload",
    "--source-rules": "source_rules",
    "--resource-rules": "resource_rules",
    "--base-catalog": "base_catalog",
    "--pin-catalog": "pin_catalog",
    "--catalog": "stage1_catalog",
}
# Флаг отпечатка, которым команда УЖЕ связывает файл (если есть).
_FP_FLAGS = {
    "--source-rules": "--source-rules-fp",
    "--resource-rules": "--resource-rules-fp",
    "--catalog": "--catalog-fp",
}


class ManifestError(RuntimeError):
    """Манифест не построен, не записан или не сходится. Вызывающий обязан
    отказать постановке (fail-closed), а не продолжать без личности."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def manifest_id(body: dict) -> str:
    return _sha256_bytes(_canonical(body))


def _hash_file(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as first:
        # Логическая запись вне репозитория (каталог данных): та же
        # процедура разрешения, что у остальных читателей.
        try:
            raw = Path(registry.resolve_rel(str(path))).read_bytes()
        except OSError as exc:
            raise ManifestError(
                f"файл постановки нечитаем: {path} ({first}; после "
                f"разрешения: {exc})") from exc
    return {"path": str(path), "sha256": _sha256_bytes(raw)}


def _files_from_commands(commands: list[list[str]]) -> dict:
    """Контент-хэши всех файлов постановки, упомянутых в командах.

    Один файл под одним именем; повторное упоминание с ДРУГИМ содержимым
    невозможно по построению (хэш от байтов первого чтения сверяется)."""
    out: dict[str, dict] = {}
    for cmd in commands or []:
        argv = [str(a) for a in cmd]
        for i, arg in enumerate(argv):
            name = _FILE_FLAGS.get(arg)
            if name is None or i + 1 >= len(argv):
                continue
            p = Path(argv[i + 1])
            entry = _hash_file(p)
            fp_flag = _FP_FLAGS.get(arg)
            if fp_flag and fp_flag in argv:
                j = argv.index(fp_flag)
                if j + 1 < len(argv) and argv[j + 1] != entry["sha256"]:
                    raise ManifestError(
                        f"файл {p.name} не совпадает с отпечатком в "
                        f"команде ({fp_flag}) — файл подменён между "
                        f"построением команды и манифестом")
            prev = out.get(name)
            if prev is not None and prev["sha256"] != entry["sha256"]:
                raise ManifestError(
                    f"постановка ссылается на два разных содержимого "
                    f"файла «{name}»")
            out[name] = entry
    return out


def _solver_build() -> dict | None:
    """Личность сборки решателя, если она устанавливается. Отсутствие
    сборки — честный None (python-only прогон), а не пустой хэш."""
    try:
        from . import build_identity
        info = build_identity.build_identity()
    except Exception:                                   # noqa: BLE001
        return None
    solver = (info or {}).get("solver") if isinstance(info, dict) else None
    if not isinstance(solver, dict):
        return None
    keep = {k: solver.get(k) for k in ("jar_sha256", "manifest_jar_sha256")
            if solver.get(k)}
    return keep or None


def _task_package_fingerprint() -> dict | None:
    """Личность пакета постановки CDPM, если прогон идёт по нему.

    Пакет берётся ЕДИНЫМ резолвером (webapp.task_channel): приоритет —
    CREW_TASK_PACKAGE (прежнее поведение), без переменной — пакет,
    принятый каналом (task-inbox/current.json); канальный пакет входит
    в отпечаток постановки наравне с «файлом руками». Нет ни того, ни
    другого — честный None (прогон без пакета). НЕГОДНЫЙ пакет —
    ManifestError: постановка, сославшаяся на пакет, обязана его
    прочитать, а не тихо продолжить без него.
    """
    from . import task_channel
    cur = task_channel.current_package()
    if cur is None:
        return None
    raw = str(cur["path"])
    try:
        from . import task_package
        pkg = task_package.load(raw, run_gate=False)
    except Exception as exc:                            # noqa: BLE001
        src = ("CREW_TASK_PACKAGE"
               if (cur.get("source") or {}).get("kind") == "file"
               else "из канала приёма")
        raise ManifestError(
            f"пакет постановки {src} не принят: {exc}") from exc
    man = pkg.get("manifest") or {}
    return {
        "fingerprint": pkg.get("fingerprint"),
        "spec_version": man.get("spec_version"),
        "source_run": man.get("source_run"),
        "revision": man.get("revision"),
        "path": str(raw),
    }


def build(jtype: str, inst: dict, commands: list[list[str]],
          run_id: str, frozen: dict, resolved: dict) -> dict:
    """Собрать манифест постановки. Бросает ManifestError — вызывающий
    отказывает запуску."""
    files = _files_from_commands(commands)
    if "payload" not in files:
        files["payload"] = _hash_file(
            Path(registry.resolve_rel(inst["payload_path"])))
    body = {
        "schema": SCHEMA,
        "run_id": str(run_id),
        "job_type": str(jtype),
        "instance": str(inst.get("id")),
        "label": str(inst.get("label")),
        "files": files,
        # Мост CDPM -> COSMOS (МУ-04): отпечаток пакета постановки —
        # часть личности прогона. Без него два прогона по РАЗНЫМ
        # постановкам CDPM (иные веса, иные режимы) получили бы одну
        # подпись — тот самый дефект, ради которого манифест и заведён.
        "task_package": _task_package_fingerprint(),
        "config_domains": dict(frozen.get("fingerprints") or {}),
        "solver_build": _solver_build(),
        "resolved_config": dict(resolved or {}),
        "upstream_manifest_id": None,
    }
    # Цепочка постановок: каталог этапа 1 несёт идентификатор СВОЕЙ
    # постановки в config_sig — этап 2 записывает его как upstream, и
    # личности этапов связываются в цепь, а не существуют порознь.
    cat = files.get("stage1_catalog")
    if cat is not None:
        try:
            p = Path(cat["path"])
            if not p.exists():
                p = Path(registry.resolve_rel(cat["path"]))
            doc = json.loads(p.read_text("utf-8"))
            sig = doc.get("config_sig") or {}
            up = sig.get("problem_manifest_id")
            if isinstance(up, str) and up:
                body["upstream_manifest_id"] = up
        except (OSError, json.JSONDecodeError, AttributeError):
            body["upstream_manifest_id"] = None
    return {"body": body, "manifest_id": manifest_id(body)}


def write(run_config_dir: Path, manifest: dict) -> Path:
    """Записать манифест НЕИЗМЕНЯЕМО (O_EXCL): второй манифест того же
    прогона — ошибка постановки, а не перезапись."""
    path = Path(run_config_dir) / FILENAME
    record = {"manifest_id": manifest["manifest_id"],
              "body": manifest["body"]}
    raw = json.dumps(record, ensure_ascii=False, indent=1)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ManifestError(
            f"манифест постановки уже существует: {path} — прогон не "
            f"может получить вторую личность") from exc
    except OSError as exc:
        raise ManifestError(f"манифест не записан: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
    except OSError as exc:
        raise ManifestError(f"манифест не записан: {exc}") from exc
    return path


def load(path: Path) -> dict:
    """Прочитать манифест и пересчитать идентификатор. Любое расхождение —
    ManifestError: подписи без проверки не существует."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"манифест нечитаем: {path} ({exc})") from exc
    body = record.get("body")
    declared = record.get("manifest_id")
    if not isinstance(body, dict) or not isinstance(declared, str):
        raise ManifestError(f"манифест повреждён: {path}")
    actual = manifest_id(body)
    if actual != declared:
        raise ManifestError(
            f"идентификатор манифеста не сходится с телом: {path} — "
            f"файл правился после записи")
    if body.get("schema") != SCHEMA:
        raise ManifestError(f"чужая схема манифеста: {body.get('schema')}")
    return record
