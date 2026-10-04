# -*- coding: utf-8 -*-
"""Паспорт прогона CDPM — рецепт воспроизведения.

Слова владельца 07.09.2026 (через COSMOS, дословно): «Прогон должен хранить
также модели и солверы, которые использовались (и в CDPM — так же)» и
принцип «информация, хранящаяся о каждом прогоне, должна давать
возможность его однозначно воспроизвести».

Паспорт складывается из того, что уже лежит на диске прогона, и из
окружения панели, записанного при рождении прогона:
  * код и окружение — коммит панели, версия спецификации пакета, версии
    Python и решателей проверок (z3-solver, ortools, openpyxl,
    jsonschema), версия и отпечаток каталога шаблонов;
  * входы по отпечаткам — документы правил (манифест заморозки, SHA-256),
    книга операционных данных (манифест заморозки данных, SHA-256),
    книга ответов анкеты (последняя ревизия, SHA-256);
  * модели по стадиям — журнал каналов стадий (`stage_channels`): роль,
    класс политики, стадия, провайдер, модель, вид канала, усилие,
    температура, источник конфигурации и коммит кода, которым стадия шла;
  * журналы для повтора «из журнала» — кэш извлечения (ответы модели по
    чанкам), обмены аудит-цикла (замечания и ответы по раундам), ревизии
    ответов анкеты; повтор «заново» — те же входы и раскладка, новые вызовы.
Паспорт — только чтение; ничего не выдумывается: чего нет на диске, того
нет в паспорте (null / пустой список).
"""
from __future__ import annotations

import functools
import hashlib
import json
import platform
import subprocess
from pathlib import Path

#: Пакеты решателей и библиотек, версии которых входят в паспорт
#: (имена дистрибутивов pip).
SOLVER_PACKAGES = ("z3-solver", "ortools", "openpyxl", "jsonschema")

#: Роли для краткой строки моделей в списке прогонов (в порядке показа).
SUMMARY_ROLES = ("extractor", "worker", "auditor", "arbiter")
ROLE_TITLES = {"extractor": "извлечение", "worker": "обработчик",
               "replier": "обработчик (ответы в обменах)",
               "auditor": "аудитор", "arbiter": "арбитр",
               "applier": "применитель",
               "vision": "зрение", "logic_verifier": "сверщик логики",
               "answer_matcher": "разборщик ответов",
               "answer_applier": "внесение ответов",
               "data_compiler": "компилятор проверок",
               "data_reviewer": "сверщик находок",
               "smt_translator": "переводчик SMT",
               "mode2_checker": "аудитор режима 2",
               "catalog_composer": "составитель заявок каталога"}


def _git_head(repo: Path) -> dict:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        sha = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return {"commit": sha or None, "short": (sha[:7] if sha else None)}


def _dist_version(name: str):
    try:
        import importlib.metadata as md
        return md.version(name)
    except Exception:                                     # noqa: BLE001
        return None


def _catalog_stamp() -> dict:
    """Блок `catalog` паспорта: версия и отпечаток каталога шаблонов,
    которым работает панель, плюс признак порядка словаря.

    Отметка — у одной двери моста (`catalog_bridge.stamp_or_error()`),
    той же, что уходит в посадки правил и в `manifest.catalog` пакета:
    один каталог — один ответ на всех. Каталог обязателен (шаг 7), но
    ПАСПОРТ от его отсутствия не падает: беда называется словами —
    `{version: null, fingerprint: null, error: "…"}`.

    Признак `dictionary_order` (шаг 7) — для повтора: паспорт БЕЗ него
    записан прежним порядком листинга словаря, и повтор из журнала
    такого прогона расходится на первом вызове извлечения (слова у
    кнопки даёт `replay.availability()`)."""
    try:
        from cdpm import catalog_bridge as bridge
        return bridge.stamp_or_error()
    except Exception as e:                                # noqa: BLE001
        # Мост не импортировался (установка неполна) — это тоже отказ
        # каталога, и он называется, а не прячется под null.
        return {"version": None, "fingerprint": None,
                "error": f"мост каталога не прочитан: {str(e)[:160]}"}


@functools.lru_cache(maxsize=1)
def _stable_environment() -> dict:
    """Часть окружения, неизменная за жизнь процесса: код панели,
    версия спецификации, Python, версии решателей."""
    repo = Path(__file__).resolve().parents[1]
    try:
        from cdpm import export_task_package as etp
        spec = getattr(etp, "SPEC_VERSION", None)
    except Exception:                                     # noqa: BLE001
        spec = None
    return {
        "code": _git_head(repo),
        "spec_version": spec,
        "python": platform.python_version(),
        "packages": {name: _dist_version(name) for name in SOLVER_PACKAGES},
    }


def environment() -> dict:
    """Код и окружение панели. Неизменная часть считается один раз на
    процесс (панель живёт на одном коммите до перезапуска), а блок
    `catalog` — ВНЕ кэша: отказ каталога не должен запоминаться на всю
    жизнь процесса (площадку чинят, не перезапуская панель), и годная
    отметка после починки обязана попасть в паспорт следующего
    прогона."""
    return {**_stable_environment(), "catalog": _catalog_stamp()}


def code_short() -> str | None:
    return environment()["code"].get("short")


def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inputs_of(run_dir: Path) -> dict:
    """Входы прогона по отпечаткам — из манифестов заморозки и последней
    ревизии ответов; отсутствующее — пусто."""
    docs = []
    man = _read_json(run_dir / "freeze" / "manifest.json")
    if isinstance(man, dict):
        for f in man.get("files") or []:
            if isinstance(f, dict):
                docs.append({"sid": f.get("sid"), "file": f.get("file"),
                             "sha256": f.get("sha256"),
                             "clauses": f.get("clause_count")})
    data = []
    dman = _read_json(run_dir / "data-freeze" / "manifest.json")
    if isinstance(dman, dict):
        for f in dman.get("files") or []:
            if isinstance(f, dict):
                data.append({"sid": f.get("sid"), "file": f.get("file"),
                             "sha256": f.get("sha256"),
                             "rows": f.get("rows_seen")})
    answers = None
    adir = run_dir / "answers"
    if adir.is_dir():
        revs = sorted(p for p in adir.iterdir()
                      if p.is_dir() and p.name.startswith("rev-"))
        if revs:
            last = revs[-1]
            books = sorted(last.glob("*.xlsx"))
            answers = {"revision": last.name,
                       "file": books[0].name if books else None,
                       "sha256": _sha256(books[0]) if books else None}
    return {"documents": docs, "data": data, "answers": answers}


def stages_of(status: dict) -> list:
    """Модели по стадиям — из журнала каналов стадий статуса."""
    journal = status.get("stage_channels") or {}
    born = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    replay = born.get("replay") if isinstance(born.get("replay"), dict) else {}
    replay_mode = replay.get("mode")
    rows = []
    for key, entry in journal.items():
        if not isinstance(entry, dict):
            continue
        role, _, klass = str(key).partition("/")
        eff = entry.get("effective") if isinstance(entry.get("effective"), dict) else {}
        rows.append({
            "role": role, "role_title": ROLE_TITLES.get(role, role),
            "klass": klass or None, "stage": entry.get("stage"),
            "provider": eff.get("provider", entry.get("provider")),
            "model": eff.get("model", entry.get("model")),
            "channel": eff.get("channel"),
            "effort": entry.get("effort"),
            "temperature": entry.get("temperature"),
            "config_source": entry.get("config_source"),
            "code": entry.get("code"),
            # Проект повтора: у повтора из журнала модель не вызывалась.
            "calls_code": "replayed-from-journal"
            if replay_mode == "replay-journal" else "live",
        })
    order = {r: i for i, r in enumerate(ROLE_TITLES)}
    rows.sort(key=lambda r: (order.get(r["role"], 99), r["klass"] or ""))
    return rows


def journals_of(run_dir: Path) -> dict:
    """Что сохранено для повтора «из журнала»: кэш извлечения (ответы
    модели по чанкам), раунды аудит-цикла, ревизии ответов анкеты; с
    этапа Р1 проекта повтора — журнал внешних вызовов (`calls`, его
    полнота) и журнал действий (`actions`)."""
    def _count(path: Path, pattern: str) -> int:
        return len(list(path.glob(pattern))) if path.is_dir() else 0
    from cdpm import actions_journal, call_journal
    man = call_journal.manifest(run_dir)
    return {
        "calls": call_journal.count(run_dir),
        "calls_complete": (None if man is None
                           else bool(man.get("complete")) and not man.get("degraded")),
        "actions": actions_journal.count(run_dir),
        "extract_cache": _count(run_dir / "extract-cache", "*.json"),
        "exchange_rounds": _count(run_dir / "exchange" / "rounds", "round-*"),
        "answers_revisions": _count(run_dir / "answers", "rev-*"),
    }


def passport_of(run_dir: Path, status: dict) -> dict:
    """Полный паспорт прогона для страницы прогона."""
    born = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    return {
        "created": born.get("environment"),
        # Проект повтора (Р2): режим, источник, вердикт у потомка.
        "replay": born.get("replay") if isinstance(born.get("replay"), dict) else None,
        "created_at": status.get("created_at"),
        "inputs": inputs_of(run_dir),
        "stages": stages_of(status),
        "journals": journals_of(run_dir),
    }


def summary_of(status: dict) -> dict:
    """Краткая строка для карточки списка: модели ключевых ролей,
    решатели, код."""
    born = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    env = born.get("environment") if isinstance(born.get("environment"), dict) else {}
    models: dict = {}
    for row in stages_of(status):
        role = row["role"]
        # Ответчик аудит-цикла — обработчик (дитя роли worker): в
        # краткой строке он и есть «обработчик», если сам worker не шёл.
        if role == "replier":
            role = "worker"
        if role in SUMMARY_ROLES and role not in models and row.get("model"):
            label = str(row["model"])
            if row.get("effort"):
                label += f" ({row['effort']})"
            models[role] = label
    pk = env.get("packages") or {}
    solvers = [f"{n} {v}" for n, v in pk.items() if v and n in ("z3-solver", "ortools")]
    code = (env.get("code") or {}).get("short")
    cat = env.get("catalog") or {}
    return {"models": models, "solvers": solvers, "code": code,
            "catalog": cat.get("version")}

# Роли в поле «чем сделан пакет» — имена контракта с COSMOS (07.09.2026):
# извлечение / обработчик (и его ответчик в обменах) / аудитор / арбитр.
MADE_BY_ROLES = {"extractor": "extraction", "worker": "processor",
                 "replier": "processor", "auditor": "auditor",
                 "arbiter": "arbiter"}


def made_by_of(run_dir: Path, status: dict) -> dict | None:
    """Поле манифеста пакета `made_by` («чем сделан пакет», Дополнение к
    1.18, форма согласована с COSMOS 07.09.2026): продукт, коммит панели,
    версия спецификации, прогон, дата рождения прогона (стабильная —
    метки времени экспорта в манифесте нет по Ф-1.7), модели ролей
    формализации по журналу каналов, версии инструментов проверок, входы
    по отпечаткам. Версия/отпечаток каталога сюда не кладутся — их место
    `manifest.catalog` (один источник). Без статуса прогона — None."""
    if not isinstance(status, dict) or not status:
        return None
    born = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    # Правило 2.15 проекта повтора: потомок «из журнала» выпускает пакет
    # байт в байт с исходным — made_by целиком из снимка источника.
    replay = born.get("replay") if isinstance(born.get("replay"), dict) else {}
    if replay.get("mode") == "replay-journal" \
            and isinstance(replay.get("source_made_by"), dict):
        return dict(replay["source_made_by"])
    env = born.get("environment") if isinstance(born.get("environment"), dict) else {}
    models = []
    for row in stages_of(status):
        role = MADE_BY_ROLES.get(row["role"])
        if not role or not row.get("model"):
            continue
        models.append({"role": role, "provider": row.get("provider"),
                       "model": row.get("model"), "channel": row.get("channel"),
                       "effort": row.get("effort"),
                       "temperature": row.get("temperature"),
                       "source": row.get("config_source")})
    inputs = inputs_of(run_dir)
    answers = inputs.get("answers") or {}
    return {
        "product": "CDPM",
        "code": (env.get("code") or {}).get("commit"),
        "spec_version": env.get("spec_version"),
        "run": run_dir.name,
        "run_created": status.get("created_at"),
        "models": models,
        "tools": dict(env.get("packages") or {}),
        "inputs": {
            "rules": [{"name": d["file"], "sha256": d["sha256"]}
                      for d in inputs.get("documents", [])],
            "data": [{"name": d["file"], "sha256": d["sha256"]}
                     for d in inputs.get("data", [])],
            "answers": answers.get("sha256"),
        },
    }
