"""Машинная личность выпуска: что за код, какой решатель, какая схема.

Пять раундов аудита подряд повторяли одно (P2-05, R4 §4.8, R5-09):
принадлежность работающего процесса срезу кода выводилась косвенно — по
времени перезапуска и совпадению рабочего дерева. `/health`, `/ready`,
`/version` не существовали вовсе, а запрос к ним попадал на общий
обработчик статики: неаутентифицированный — переадресация на страницу
отказа, аутентифицированный — сама страница приложения с кодом 200. Для
наивного наблюдателя это выглядело здоровым откликом всегда, включая
случай наглухо сломанного контура.

Здесь — три ответа на три РАЗНЫХ вопроса, и путать их нельзя:

* «жив ли процесс» (`/healthz`) — отвечает сам факт ответа; проверка не
  трогает ни диск, ни базу, чтобы не сделать наблюдателя источником
  нагрузки и не привязать живость к внешним зависимостям;
* «готов ли обслуживать» (`/readyz`) — проверяются зависимости, без
  которых работа бессмысленна: схема, база пользователей, каталог правил;
* «что именно развёрнуто» (`/versionz`) — срез кода, отпечаток решателя
  Kotlin и его манифест сборки, отпечаток договора правил.

Все три БЕЗ аутентификации и намеренно: наблюдение за живостью, которое
требует входа, бесполезно. Поэтому наружу идут только отпечатки и
состояния — ни имён файлов заказчика, ни путей, ни значений настроек.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KOTLIN_DIR = REPO_ROOT / "kotlin-solver"
KOTLIN_APP_DIR = KOTLIN_DIR / "target" / "quarkus-app"
KOTLIN_JAR = KOTLIN_APP_DIR / "quarkus-run.jar"
KOTLIN_MANIFEST = REPO_ROOT / "final-report" / "kotlin-build-manifest.json"
# Паспорт прогона COSMOS (07.09.2026): у решателя нет ручки версии, версия
# — из файлов сборки на машине панели: jar приложения, перечень
# зависимостей сборки (строки timefold-solver-core / quarkus arc), при его
# отсутствии — pom.xml; конфигурация решателя (random-seed, spent-limit)
# — отпечатком, это часть рецепта воспроизведения.
KOTLIN_DEPS = KOTLIN_APP_DIR / "quarkus-app-dependencies.txt"
KOTLIN_POM = KOTLIN_DIR / "pom.xml"
KOTLIN_CONFIG = KOTLIN_DIR / "src" / "main" / "resources" / "application.properties"

_cache: dict | None = None


def _app_jar() -> Path | None:
    try:
        jars = sorted((KOTLIN_APP_DIR / "app").glob("*.jar"))
    except OSError:
        return None
    return jars[0] if jars else None


def _solver_versions() -> dict:
    """Версии Timefold и Quarkus по файлам сборки: перечень зависимостей
    (точная сборка), иначе pom.xml (объявленная); нет обоих — null с
    источником None."""
    import re
    out = {"timefold_version": None, "quarkus_version": None,
           "versions_source": None}
    try:
        text = KOTLIN_DEPS.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"timefold-solver-core::jar:([\w.\-]+)", text)
        q = re.search(r"io\.quarkus\.arc:arc::jar:([\w.\-]+)", text)
        if m or q:
            out.update(timefold_version=m.group(1) if m else None,
                       quarkus_version=q.group(1) if q else None,
                       versions_source="dependencies")
            return out
    except OSError:
        pass
    try:
        text = KOTLIN_POM.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<version\.ai\.timefold\.solver>([^<]+)<", text)
        q = re.search(r"<version\.io\.quarkus>([^<]+)<", text)
        if m or q:
            out.update(timefold_version=m.group(1).strip() if m else None,
                       quarkus_version=q.group(1).strip() if q else None,
                       versions_source="pom")
    except OSError:
        pass
    return out


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _git_head() -> dict:
    """Срез рабочего дерева и его чистота. Грязное дерево называется
    вслух: на площадке это норма (живые артефакты расчётов), но выпуск,
    объявленный «ровно таким-то срезом», обязан это уточнять."""
    def git(*args):
        try:
            # Тот же защитный набор флагов, что и на контуре самоулучшения
            # (внешний аудит R9, P0-04): этот вызов читает рабочее дерево
            # под записью процесса-подписанта, и оставить его без защиты
            # значило бы сохранить исполняемый механизм ровно там, где мы
            # объявили, что его больше нет.
            from .evolution_runner import _git_safe_flags
            r = subprocess.run(["git", *_git_safe_flags(), *args],
                               cwd=str(REPO_ROOT),
                               capture_output=True, timeout=15, check=False)
            return (r.stdout or b"").decode("utf-8", "replace").strip() \
                if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    return {"commit": head or None,
            "commit_short": head[:7] if head else None,
            "tree_dirty": bool(dirty),
            "dirty_paths": len(dirty.splitlines()) if dirty else 0}


def _rules_contract_fingerprint() -> str | None:
    """Отпечаток ВСТРОЕННОГО договора правил яруса 1 — не действующих
    значений набора: наружу уходит версия контракта, а не конфигурация
    авиакомпании."""
    try:
        import rules_config
        body = json.dumps(rules_config.DEFAULTS, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]
    except Exception:                                    # noqa: BLE001
        return None


def build_identity(refresh: bool = False) -> dict:
    """Полная личность выпуска. Кэшируется: отпечаток JAR — это чтение
    сорока мегабайт, наблюдателю незачем делать это каждую минуту."""
    global _cache
    if _cache is not None and not refresh:
        return dict(_cache)
    jar_sha = _sha256_file(KOTLIN_JAR)
    app_jar = _app_jar()
    manifest: dict = {}
    try:
        manifest = json.loads(KOTLIN_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    # Имя ключа взято из фактического манифеста (_kotlin_build_manifest.py),
    # а не угадано: перепутанное имя дало бы вечное «не совпадает».
    artifacts = manifest.get("artifacts") if isinstance(
        manifest.get("artifacts"), dict) else manifest
    manifest_jar = (artifacts or {}).get("quarkus_run_jar_sha256")
    data = {
        "schema": "cosmos-build-identity/v1",
        "source": _git_head(),
        "solver": {
            "jar_sha256": jar_sha,
            "jar_present": jar_sha is not None,
            # Совпадает ли РАЗВЁРНУТЫЙ решатель с тем, что записан в
            # манифесте сборки. Пятый аудит (R5-08) нашёл фактическое
            # расхождение: JAR был старше исходников Kotlin, а зелёные
            # тесты Kotlin этого не показывали, потому что компилировали
            # исходники заново. Теперь несовпадение видно снаружи.
            "manifest_jar_sha256": manifest_jar,
            "matches_manifest": bool(jar_sha and manifest_jar
                                     and jar_sha == manifest_jar),
            "manifest_commit_short": manifest.get("commit_short"),
            # Паспорт прогона COSMOS (07.09.2026): отпечаток jar приложения,
            # версии Timefold/Quarkus из файлов сборки, отпечаток
            # конфигурации решателя — рецепт воспроизведения; ничего из
            # этого не выдаёт ни путей, ни данных заказчика.
            "app_jar_sha256": _sha256_file(app_jar) if app_jar else None,
            "config_sha256": _sha256_file(KOTLIN_CONFIG),
            **_solver_versions(),
        },
        "rules_contract_fingerprint": _rules_contract_fingerprint(),
    }
    _cache = data
    return dict(data)


def readiness() -> dict:
    """Зависимости, без которых обслуживание бессмысленно.

    Проверки дешёвые и по существу: наличие схемы канонического формата,
    базы пользователей и каталога правил. Отказ ЛЮБОЙ делает контур
    неготовым — «частично готов» для наблюдателя не существует."""
    checks: dict = {}
    from . import registry
    schema = registry.SCHEMA_PATH
    checks["payload_schema"] = schema.is_file()
    try:
        from . import users
        checks["users_db"] = Path(users.USERS_DB_PATH).is_file()
    except Exception:                                    # noqa: BLE001
        checks["users_db"] = False
    try:
        from . import config_scope
        checks["rules_resolver"] = bool(
            config_scope.effective(config_scope.DOMAIN_RULES, None))
    except Exception:                                    # noqa: BLE001
        checks["rules_resolver"] = False
    return {"ready": all(checks.values()), "checks": checks}
