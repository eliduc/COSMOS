"""Job manager: pipeline stages as subprocesses (API contract v1).

One job = one or more commands run sequentially (validate is two
validators) with cwd = repo root and the venv python; stdout+stderr merged
into webapp/jobs/<id>.log, state persisted to webapp/jobs/<id>.json after
every transition. A watcher thread per job waits on the processes and
finalizes the status — no polling of the child from request handlers.

Gate semantics (contract): a nonzero exit from stage 2 is NOT a crash —
_gerad_stage2_roster.py returns the number of non-OK instances by design
(round-6 audit rule). If the run completed its artifact, the job is
"succeeded" with "gate_nonzero": true; the verdict lives in the results.
Same for stage 1: _pairing_builder.py exits 4 when the catalog IS
published but some legs are provably impossible to cover (a property of
the data, recorded in the coverage evidence) — with a freshly published
catalog that is a verdict, not a failure.

Stage-2 isolation ports: pool 9092-9098, allocated to a port that is both
unclaimed by a running job and actually free (bind probe — long-lived
JVMs from other experiments may squat on the pool), released on job end.

Stop = kill the whole process tree: taskkill /T /F on Windows (the stage
scripts spawn JVMs), os.kill fallback elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import _data_paths
from . import registry
from .registry import REPO_ROOT

JOBS_DIR = REPO_ROOT / "webapp" / "jobs"
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():                     # non-Windows venv layout
    VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
# Tier-1 airline rules: every job subprocess gets CREW_RULES_CONFIG pointing
# at the config that applies TO THIS INSTANCE, so the Python
# engines/validators and the Kotlin GERAD profiles (all reading rules_config
# / the same env) actually APPLY what the user saved in the UI.
# Путь выбирает config_scope — единственное объявление уровней (P1-10).

STAGE2_PORT_POOL = list(range(9092, 9099))
LOG_TAIL_LINES = 40
# Скомпилированные ресурсные правила Яруса 2б для прогона (P0-04).
RESOURCE_RULES_DIR = REPO_ROOT / "webapp" / "config" / "resource-rules"


def _eligible_rule_packs(inst: dict | None):
    """Пакеты правил, действующие ДЛЯ ЭТОГО набора.

    Повторный аудит 2026-07-26 (P0-04): прежде компилировались ВСЕ записи
    глобального реестра пакетов — включая черновики и пакеты другой
    юрисдикции. Черновик «еженедельного отдыха» одного арендатора попадал
    в действующие правила чужого набора.

    Действующим считается: пакет со статусом «approved», чья страна
    совпадает со страной набора (пакет без страны действует везде), и в
    нём — только правила со статусом «approved». Черновик по определению
    ещё не решение."""
    try:
        from . import rule_packs
    except Exception:                              # noqa: BLE001
        return
    for meta in rule_packs.list_packs():
        if meta.get("status") != "approved":
            continue
        # Привязка к набору (пятый аудит, R5-04): отбор по стране —
        # не изоляция арендаторов. Пакет набора действует только на свой
        # набор; нормативный — на наборы своей юрисдикции.
        if not rule_packs.pack_applies_to(meta, inst):
            continue
        pack = rule_packs.get_pack(meta["id"])
        if not pack:
            continue
        yield {**pack, "rules": [r for r in (pack.get("rules") or [])
                                 if r.get("status") == "approved"]}


import _data_paths  # noqa: E402 — каталог данных контура


def _statement_or_source_rules(inst: dict):
    """Файл правил для команды расчёта.

    Приоритет у ДОКУМЕНТА ПОСТАНОВКИ, загруженного на вкладке «Данные»
    (`<данные>/rules/postanovka.md`): заказчик присылает один документ,
    и расчёт обязан считать по нему, а не по служебному файлу, который
    кто-то положил рядом с набором раньше. Служебный файл остаётся
    запасным путём для контуров, где документа нет.

    Строгий режим (`--source-rules-strict`) действует в обоих случаях:
    неполные правила — отказ, а не умолчания другой авиакомпании.
    """
    doc = _data_paths.data_root() / "rules" / "postanovka.md"
    if doc.exists():
        return doc
    return registry.FINAL_REPORT / f"source_rules_{inst['label']}.json"


def _resource_rules_file(inst: dict, run_id: str):
    """Скомпилировать ресурсные правила действующих Пакетов правил в файл
    для этапа 2. Возвращает путь либо None, если исполнимых правил нет.

    Заявленные, но неисполнимые правила публикуются в переменной
    CREW_RULES_DECLARED_RESOURCES: этап 2 обязан увидеть, что правило
    объявлено, и не выдавать «ноль жёстких нарушений» за соответствие."""
    try:
        from . import rule_packs
    except Exception:                              # noqa: BLE001
        return None
    document: dict = {}
    origin: dict = {}
    for pack in _eligible_rule_packs(inst):
        comp = rule_packs.compile_resource_rules(pack)
        for name, value in (comp.get("document") or {}).items():
            if name in document and document[name] != value:
                # Третий аудит 2026-07-26 (§5.2): тихий last-write-wins
                # позволял одному пакету молча переписать действующее
                # ограничение другого. Столкновение — это конфликт
                # ПОСТАНОВКИ, разрешать его порядком обхода нельзя.
                raise JobError(
                    f"ресурсное правило «{name}» задано двумя пакетами "
                    f"с разными значениями («{origin[name]}» и "
                    f"«{pack.get('name') or pack.get('id')}») — запуск "
                    f"отклонён; оставьте одно определение или согласуйте "
                    f"значения", http_status=409)
            document[name] = value
            origin[name] = pack.get("name") or pack.get("id")
    if not document:
        return None
    body = json.dumps(document, ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(body.encode()).hexdigest()
    # Файл ПРОГОНА, а не метки набора (пятый аудит, R5-02): прежде имя
    # несло 32 случайных бита и создавалось обычной записью — вынужденное
    # совпадение переписывало чужой файл, а cleanup отсутствовал вовсе
    # (по расчёту аудита ~20 000 файлов одной метки дают около 4,5 %
    # вероятности совпадения). Теперь каталог прогона, полный
    # идентификатор и ИСКЛЮЧИТЕЛЬНОЕ создание: совпадение имени —
    # немедленная ошибка, а не тихая перезапись.
    run_dir = RESOURCE_RULES_DIR / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "resource-rules.json"
    with open(path, "x", encoding="utf-8") as fh:      # O_EXCL
        fh.write(body)
    return path, fp


def drop_resource_rules(run_id: str) -> None:
    """Убрать файл правил прогона: он часть жизненного цикла прогона, а
    не вечный мусор (R5-02 — «cleanup отсутствует»)."""
    import shutil
    shutil.rmtree(RESOURCE_RULES_DIR / str(run_id), ignore_errors=True)


def _declared_resources(inst: dict | None = None) -> list[str]:
    """Имена ресурсных правил, объявленных в ДЕЙСТВУЮЩИХ для набора
    Пакетах правил — тем же отбором, что и компиляция: иначе черновик
    объявлял бы правило, которое никогда не будет применено, и каждый
    прогон вечно считался бы неполным."""
    try:
        from . import rule_packs
    except Exception:                              # noqa: BLE001
        return []
    names: set[str] = set()
    for pack in _eligible_rule_packs(inst):
        names.update(rule_packs.compile_resource_rules(pack)["declared"])
    return sorted(names)

JOB_TYPES = ("stage1", "complete", "stage2", "validate", "repair",
             "multirole")

_lock = threading.Lock()
_jobs: dict[str, dict] = {}          # id -> job dict (contract shape + pid)
_procs: dict[str, subprocess.Popen] = {}
_stop_requested: set[str] = set()
_ports_in_use: dict[str, int] = {}   # job id -> stage2 port
_loaded = False


class JobError(Exception):
    """Request-level job error; http_status tells app.py what to return."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


# ----------------------------------------------------------- persistence

def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _log_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.log"


# Служебные поля, переживающие перезапуск сервера. Личность прогона —
# имя файла машинного следа схождения: без неё график завершённого
# прогона после перезапуска приложения не найти (вопрос владельца
# 2026-08-13: «как снова увидеть график пейринга»). Момент старта —
# секунды эпохи, для живого времени из бюджета. Оба хранятся в
# отдельном блоке `_internal`, чтобы публичный ответ API не менялся:
# `_public` отсекает любой ключ на подчёркивании.
_PERSIST_INTERNAL = ("_run_id", "_started_ts")


def _save(job: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in job.items() if not k.startswith("_")}
    keep = {k: job[k] for k in _PERSIST_INTERNAL if job.get(k) is not None}
    if keep:
        public["_internal"] = keep
    tmp = _job_path(job["id"]).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(public, indent=1), encoding="utf-8")
    tmp.replace(_job_path(job["id"]))


def _recover_running(job: dict) -> dict:
    """Судьба задачи, пережившей перезапуск сервера, — по ФАКТУ.

    Расчёт живёт отдельным процессом и перезапуск приложения переживает.
    Прежде любая запись «выполняется» при загрузке объявлялась
    остановленной: логика честная в намерении («не выдумывать успех»),
    но неверная по факту — на боевом прогоне она пометила остановленным
    расчёт, который считался ещё час и завершился нормально. Пользователь
    видел мёртвую задачу при живом процессе.

    Поэтому вместо предположения проверяются два свидетельства:
    жив ли процесс с сохранённым идентификатором и появились ли
    артефакты прогона. «Успешно» по-прежнему не выдумывается: без
    артефактов исход остаётся неизвестным.
    """
    pid = job.get("pid")
    alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except (OSError, ProcessLookupError, PermissionError):
            alive = bool(job.get("_assume_alive"))
    if alive:
        return {"status": "running",
                "note": "сервер перезапускался; процесс расчёта жив"}
    log = _log_path(job["id"])
    tail = ""
    try:
        tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        pass
    if "ГЕЙТ" in tail or "готов" in tail or "OPTIMAL" in tail \
            or "FEASIBLE" in tail:
        return {"status": "finished",
                "note": "сервер перезапускался; расчёт завершился, "
                        "результат см. в разделе «Результаты»"}
    return {"status": "unknown",
            "note": "сервер перезапускался, процесс не найден и признаков "
                    "завершения в журнале нет — исход неизвестен"}


def _load_persisted() -> None:
    """Read-only load on restart. A job persisted as "running" lost its
    watcher when the server died — its true outcome is unknown, so it is
    surfaced as "stopped" in memory (the file is left untouched: read-only
    means read-only, and inventing a "succeeded" would be dishonest)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not JOBS_DIR.is_dir():
        return
    for path in JOBS_DIR.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(job, dict) or "id" not in job:
            continue
        # Служебный блок разворачивается обратно в поля на подчёркивании:
        # так личность прогона (имя файла следа схождения) и момент
        # старта переживают перезапуск, а публичный ответ API их не несёт.
        internal = job.pop("_internal", None)
        if isinstance(internal, dict):
            for key in _PERSIST_INTERNAL:
                if internal.get(key) is not None:
                    job[key] = internal[key]
        if job.get("status") == "running":
            job.update(_recover_running(job))
        # Tenancy migration: jobs persisted before owner tracking belong to
        # the primary admin (in-memory annotation only — the file on disk
        # stays untouched, read-only means read-only).
        job.setdefault("owner", registry.PRIMARY_ADMIN)
        _jobs.setdefault(job["id"], job)


# ----------------------------------------------------------- port pool

def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _allocate_stage2_port() -> int:
    for port in STAGE2_PORT_POOL:
        if port in _ports_in_use.values():
            continue
        if _port_is_free(port):
            return port
    raise JobError("no free STAGE2 port in pool 9092-9098", http_status=409)


# ----------------------------------------------------------- command build

def _int_param(params: dict, key: str, default: int) -> int:
    """Целочисленный параметр задачи; мусор -> JobError 400, не 500
    (ревью 2026-07-19: int('7; del *') ронял POST /api/jobs трейсбеком)."""
    try:
        return int(params.get(key, default))
    except (TypeError, ValueError) as exc:
        raise JobError(f"параметр {key} должен быть целым числом: "
                       f"{params.get(key)!r}") from exc


def _child_path(rel: str, what: str) -> str:
    """Логическая запись реестра -> АБСОЛЮТНЫЙ путь для дочернего процесса.

    Реестр хранит пути логически («final-report/x.json»), а физически
    файл лежит либо в корне данных, либо в репозитории — знание об этом
    держит `registry.resolve_rel`, и оно обязано применяться ДО того,
    как путь уйдёт в команду. Дочерний процесс разворачивает голую
    относительную запись от своего рабочего каталога (корень репозитория)
    и на контуре с вынесенными данными файла не находит: ровно так
    ростеринг из интерфейса падал «required input does not exist», хотя
    каталог существовал и его отпечаток совпадал (найдено владельцем
    2026-08-13).

    Отсутствие файла — отказ ЗДЕСЬ, с именем входа и полным путём, а не
    невнятная смерть дочернего процесса кодом 2."""
    path = Path(registry.resolve_rel(rel))
    if not path.exists():
        raise JobError(f"{what} не найден: {rel} (искали в корне данных и "
                       f"в репозитории; последняя проба — {path})")
    return str(path)


def _build_commands(jtype: str, inst: dict, params: dict,
                    run_id: str = "manual") -> list[list[str]]:
    """Command lines per the contract. Raises JobError(400) when the stage
    does not apply to the instance."""
    py = str(VENV_PYTHON)
    n = registry.gerad_inst_number(inst)

    # Solver-advisor knobs: whitelisted + range-checked (an LLM/human can only
    # set known flags in known ranges — never inject an arbitrary argv). Absent
    # knobs fall back to the tuned defaults, so a plain run is byte-identical to
    # before the knowledge base existed.
    from . import solver_advisor
    knobs = solver_advisor.sanitize_params(params)

    if jtype == "stage1":
        budget_min = _int_param(params, "budget_min", 75)
        if budget_min <= 0:
            raise JobError("budget_min must be positive")
        # Построитель цепочек с уровнями свободы: отдельная линия этапа 1
        # для наборов, где структура работы приходит от авиакомпании.
        # Уровни 1-2 опираются на её структуру (каталог рядом с набором),
        # уровень 3 строит смены и цепочки заново из участков.
        level = knobs.get("pairing_level")
        if level:
            cmd = [py, "_pairing_builder.py",
                   "--payload", _child_path(inst["payload_path"], "набор"),
                   "--label", inst["label"],
                   "--level", str(level),
                   "--method", knobs.get("pairing_method", "enumerate"),
                   "--budget-s", str(budget_min * 60),
                   "--seed", str(knobs.get("seed", 7))]
            for flag, key in (("--w-night", "w_night"), ("--w-dh", "w_dh"),
                              ("--solve-s", "solve_s"),
                              ("--rescue-dh", "rescue_dh"),
                              ("--rescue-per-leg", "rescue_per_leg"),
                              # rescue_s был документирован в договоре
                              # API, но терялся по пути (третий аудит,
                              # P2-03/P2-04): санитайзер его пропускал,
                              # а команда не несла
                              ("--rescue-s", "rescue_s"),
                              ("--select-engine", "select_engine"),
                              # Д1: детерминированный профиль построителя
                              ("--deterministic-time", "deterministic_time")):
                if key in knobs:
                    cmd += [flag, str(knobs[key])]
            if knobs.get("deterministic_time"):
                # Профиль Д1: один поток поиска; --workers ниже по команде
                # не ставится, последний ключ побеждал бы профиль.
                cmd += ["--workers", "1"]
            if int(level) in (1, 2):
                base = (registry.FINAL_REPORT
                        / f"pairing_base_catalog_{inst['label']}.json")
                if not base.exists():
                    raise JobError(
                        f"уровень {level} требует структуру заказчика: "
                        f"файл {base.name} рядом с набором отсутствует — "
                        f"импортируйте её или выберите уровень 3")
                cmd += ["--base-catalog", str(base)]
            # Правила системы-источника (внешний аудит R6, P0-02): раньше
            # богатая постановка существовала только в CLI, а веб-задание
            # считало по правилам яруса 1, молча выдавая результат другой
            # постановки. Артефакт правил лежит рядом с набором; путь и
            # sha256 едут в команде атомарно, неполный файл в продуктовом
            # запуске — отказ, а не умолчания другой авиакомпании.
            src_rules = _statement_or_source_rules(inst)
            if src_rules.exists():
                fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
                cmd += ["--source-rules", str(src_rules),
                        "--source-rules-fp", fp,
                        "--source-rules-strict"]
            return [cmd]
        # Правила системы-источника для ВСЕХ линий этапа 1 (аудит R10,
        # отсрочка №1): раньше их получал только построитель цепочек, а
        # colgen-линии строили постановку без правил — половины прогона
        # судили по разным моделям. Файл лежит рядом с набором; путь и
        # sha256 едут в команде атомарно.
        src_rules = _statement_or_source_rules(inst)
        src_args: list[str] = []
        if src_rules.exists():
            fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
            src_args = ["--source-rules", str(src_rules),
                        "--source-rules-fp", fp, "--source-rules-strict"]
        if inst["source"] == "gerad":
            # Corrected-space duty run (same shape as the accepted battery
            # lanes); budget_min caps the CG loop via --total-cap.
            return [[py, "_gerad_colgen.py", str(n),
                     "--bases", "BASE1,BASE2,BASE3",
                     "--pricer", knobs.get("pricer", "duty"),
                     "--max-landings", str(knobs.get("max_landings", 5)),
                     "--mp", str(knobs.get("mp", 10)),
                     "--price-cap", str(knobs.get("price_cap", 600)),
                     "--final-tl", str(knobs.get("final_tl", 900)),
                     "--dump-selected", "--dump-pool",
                     "--cert-budget", "60",
                     "--total-cap", str(budget_min * 60)] + src_args]
        # What-if scenarios cloned from a GERAD reference instance run the
        # SAME lane as their base (anchored bases + duty pricer + gerad
        # profile) — otherwise base-vs-scenario objectives would differ by
        # run configuration, not by the rule change under test.
        from . import scenarios
        meta = scenarios.meta_for(inst["id"])
        if meta is not None:
            # Resolve the base chain to its non-scenario ROOT: a scenario can
            # be based on another scenario, and it must still run the root's
            # lane so the compare delta reflects the rule change, not the run
            # configuration (audit 2026-07-15).
            base = scenarios.root_base_instance(inst["id"])
            if base is not None and base.get("source") == "gerad":
                return [[py, "_gerad_colgen.py", "0",
                         "--payload", _child_path(inst["payload_path"],
                                                  "набор"),
                         "--name", inst["label"],
                         "--profile", "gerad",
                         "--bases", "BASE1,BASE2,BASE3",
                         "--pricer", knobs.get("pricer", "duty"),
                         "--max-landings", str(knobs.get("max_landings", 5)),
                         "--mp", str(knobs.get("mp", 10)),
                         "--price-cap", str(knobs.get("price_cap", 600)),
                         "--final-tl", str(knobs.get("final_tl", 900)),
                         "--dump-selected", "--dump-pool",
                         "--cert-budget", "60",
                         "--total-cap", str(budget_min * 60)] + src_args]
        # Generic payloads: dump the selected catalog + pool and certify
        # missing legs, otherwise stage 2 has no catalog to roster over
        # (found by the first end-to-end SSIM run, 2026-07-13).
        cmd = [py, "_gerad_colgen.py", "0",
               "--payload", _child_path(inst["payload_path"], "набор"),
               "--profile", knobs.get("profile", "v2"), "--name", inst["label"],
               "--dump-selected", "--dump-pool", "--cert-budget", "60",
               "--total-cap", str(budget_min * 60)] + src_args
        # KB-recommended stage-1 tuning for generic uploads: appended only when
        # explicitly present so a default run keeps its exact command line.
        for flag, key in (("--pricer", "pricer"), ("--mp", "mp"),
                          ("--price-cap", "price_cap"),
                          ("--final-tl", "final_tl"),
                          ("--max-landings", "max_landings")):
            if key in knobs:
                cmd += [flag, str(knobs[key])]
        return [cmd]

    if jtype == "complete":
        if n not in (5, 6, 7):
            raise JobError("complete applies to gerad-5..7 only")
        return [[py, "_gerad_complete_catalogs.py", str(n)]]

    if jtype == "stage2":
        budget_s = _int_param(params, "budget_s", 600)
        seed = _int_param(params, "seed", 7)
        if budget_s <= 0:
            raise JobError("budget_s must be positive")
        # Ростеринговая часть пакета «выбор движка» (2026-08-28):
        # cpsat — прямая модель назначения v1 поверх каталога этапа 1;
        # объём судимых правил движок декларирует в отчёте поимённо.
        if knobs.get("roster_engine") == "cpsat":
            catalog = inst.get("catalog_path")
            if not catalog:
                raise JobError(
                    "движок ростеринга cpsat требует каталога этапа 1 "
                    "— выполните stage1")
            out = registry.FINAL_REPORT / (
                "roster_cpsat_%s.json" % inst["label"])
            return [[py, "_roster_cpsat.py",
                     "--payload", _child_path(inst["payload_path"],
                                              "набор"),
                     "--catalog", str(catalog),
                     "--out", str(out),
                     "--budget-s", str(budget_s),
                     "--seed", str(seed)]]
        if n is not None:
            # Numeric GERAD mode, positional CLI as always. Правила
            # источника — те же, что видел этап 1 (внешний аудит R8,
            # P0-01): numeric-путь их не получал вообще.
            cmd = [py, "_gerad_stage2_roster.py", str(n), str(n),
                   str(budget_s), str(seed)]
            src_rules = _statement_or_source_rules(inst)
            if src_rules.exists():
                fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
                cmd += ["--source-rules", str(src_rules),
                        "--source-rules-fp", fp, "--source-rules-strict"]
            return [cmd]
        # Generic mode: any instance whose label has a stage-1 catalog.
        catalog = inst.get("catalog_path")
        if not catalog:
            raise JobError(
                f"stage2 for '{inst['id']}' needs a stage-1 pairing catalog "
                f"(final-report/gerad_colgen_selected_{inst['label']}.json "
                f"or best/gerad_best_catalog_{inst['label']}.json) — run "
                f"stage1 first")
        # Контент-отпечаток каталога (аудит R10, отсрочка №3 / P1-02):
        # каталог ехал на этап 2 голым путём, и подмена файла между
        # постановкой задачи и чтением была невидима; теперь отпечаток
        # едет в самой команде, атомарно с путём.
        try:
            cat_fp = hashlib.sha256(
                Path(registry.resolve_rel(catalog)).read_bytes()).hexdigest()
        except OSError as exc:
            raise JobError(
                f"каталог этапа 1 нечитаем ({catalog}): {exc} — запустите "
                f"stage1 заново") from exc
        cmd = [py, "_gerad_stage2_roster.py",
               "--payload", _child_path(inst["payload_path"], "набор"),
               "--catalog", _child_path(catalog, "каталог этапа 1"),
               "--catalog-fp", cat_fp,
               "--label", inst["label"]]
        # Ресурсные правила Яруса 2б (еженедельный отдых и подобные) до
        # этой правки в движок не попадали вовсе: флаг существовал, но
        # продуктовый запуск его не передавал (аудит 2026-07-26, P0-04).
        rr = _resource_rules_file(inst, run_id)
        if rr is not None:
            rr_path, rr_fp = rr
            # Отпечаток содержимого едет В САМОЙ команде, атомарно с путём
            # (не общим состоянием — это была бы та же гонка): этап 2
            # сверяет прочитанный документ с отпечатком и отказывает, если
            # файл подменён между стартом задачи и чтением (R4 §4.2).
            cmd += ["--resource-rules", str(rr_path),
                    "--resource-rules-fp", rr_fp]
        # Правила системы-источника — те же, что видел этап 1 (внешний
        # аудит R7, P0-02): прежде этап 2 судил цепочки по иной модели,
        # чем та, по которой они строились, — эквивалентные станции и
        # ярусные пределы расходились молча.
        #
        # Приоритет у ДОКУМЕНТА ПОСТАНОВКИ — той же функцией, что у
        # этапа 1 и валидации (2026-08-12): generic-ветка одна ходила
        # мимо неё, брала только служебный source_rules_<label>.json, и
        # на контуре заказчика (документ загружен, служебного файла нет)
        # этап 2 стартовал бы без правил — и был бы отвергнут сверкой с
        # каталогом, который несёт отпечаток документа.
        src_rules = _statement_or_source_rules(inst)
        if src_rules.exists():
            # Отпечаток и строгий режим — как у этапа 1 (внешний аудит
            # R8, P0-01): путь без sha допускал подмену файла между
            # стартом задачи и чтением, а нестрогий режим подставлял
            # зашитые пары станций другой авиакомпании.
            fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
            cmd += ["--source-rules", str(src_rules),
                    "--source-rules-fp", fp, "--source-rules-strict"]
        return [cmd + [str(budget_s), str(seed)]]

    if jtype == "validate":
        if n is not None:
            traj_cmd = [py, "_gerad_trajectory_check.py", str(n)]
            src_rules = _statement_or_source_rules(inst)
            if src_rules.exists():
                fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
                traj_cmd += ["--source-rules", str(src_rules),
                             "--source-rules-fp", fp]
            return [[py, "_coverage_validator.py", "--gerad", str(n)],
                    traj_cmd]
        # Generic instances: BOTH certificates, same as the numeric branch.
        # Audit 2026-07-19 (P2-01): trajectory linkage alone passed a
        # coordinated ghost employee (membership/skills are the coverage
        # validator's job) — a validate job that runs one certificate is
        # not a validation. Honest 400 if the artifacts do not exist yet —
        # there is nothing to validate before a stage-2 run has published
        # them.
        label = inst["label"]
        traj_p = registry.FINAL_REPORT / f"gerad_stage2_assignment_{label}.json"
        sol_p = _data_paths.gerad_data_dir() / f"p2_two_stage_solution_{label}.json"
        catalog = inst.get("catalog_path")
        missing = [p.name for p in (traj_p, sol_p) if not p.exists()]
        if not catalog:
            missing.append(f"gerad_colgen_selected_{label}.json (catalog)")
        if missing:
            raise JobError(
                f"validate for '{inst['id']}': no stage-2 artifacts yet "
                f"(missing: {', '.join(missing)}) — run stage1 and stage2 "
                f"first")
        # Эквивалентные станции — из ТОГО ЖЕ файла правил источника, что
        # видел этап 1 (внешний аудит R7, P0-02): без них сертификат судил
        # смену станции как разрыв по иной модели, чем построитель.
        src_rules = (registry.FINAL_REPORT
                     / f"source_rules_{inst['label']}.json")
        if src_rules.exists():
            # Отпечаток сверяется В САМОЙ команде валидации (внешний аудит
            # R8, P0-01): сертификат обязан судить по тем же байтам правил,
            # что и этапы 1-2, а не по тому, что лежит в файле к моменту
            # запуска.
            sr_fp = hashlib.sha256(src_rules.read_bytes()).hexdigest()
            # ОДНО чтение внутри команды: байты читаются один раз, по ним
            # же считается отпечаток, и ОНИ ЖЕ идут в загрузчик (внешний
            # аудит R9, P0-02). Прежде здесь было два обращения к файлу —
            # одно ради отпечатка, другое ради данных, — и подмена между
            # ними проходила незамеченной.
            eq_expr = (
                "(lambda _p, _fp: (lambda _raw: ("
                "_чек(_raw, _fp), "
                "load_equivalent_stations(_raw, strict=True))[1])"
                "(Path(_p).read_bytes()))(%r, %r)"
                % (str(src_rules), sr_fp))
            eq_prelude = (
                "import hashlib as _hl; "
                "_чек = lambda _raw, _fp: (_ for _ in ()).throw("
                "SystemExit('файл правил источника подменён')) "
                "if _hl.sha256(_raw).hexdigest() != _fp "
                "else None; ")
        else:
            eq_expr, eq_prelude = "None", ""
        inline = (
            "import json; from pathlib import Path; "
            "from _gerad_trajectory_check import check_inst, "
            "load_equivalent_stations; " + eq_prelude +
            f"r = check_inst({label!r}, "
            f"traj_path=Path({str(traj_p)!r}), "
            f"sol_path=Path({str(sol_p)!r}), "
            f"catalog_path=Path({str(registry.resolve_rel(catalog))!r}), "
            f"payload_path=Path("
            f"{str(registry.resolve_rel(inst['payload_path']))!r}), "
            f"equivalent_stations={eq_expr}); "
            "print(json.dumps(r, indent=1)); "
            "import sys; "
            "hard = sum(n for k, n in (r.get('violations') or {}).items() "
            "if not k.startswith('info:') and k != 'unassigned_pairing'); "
            "sys.exit(2 if hard else 0)"
        )
        # Ревью пакета 2026-07-19: недостающие участки, ДОКАЗАННО невозможные
        # в каталоге, — свойство входа, а не дефект ростера:
        # INFEASIBLE_INPUT_OR_MODEL-экземпляры не должны валить validate.
        # Провал только на реальных дефектах: мисматч ролей, дубли,
        # призраки, необъяснённые недостачи.
        # Аудит A4 (04.09.2026): прощение ТОЛЬКО по приложенному
        # доказательству (proven_impossible_proofs) с отпечатком данных
        # этого payload'а — голое имя из proven_impossible_legs не
        # прощается; исключение остаётся видимым в отчёте покрытия
        # (complete=false, excluded_proven поимённо).
        inline_cov = (
            "import json, sys; from pathlib import Path; "
            "from _coverage_validator import check, proven_exclusions; "
            "payload = json.loads(Path("
            f"{str(registry.resolve_rel(inst['payload_path']))!r}"
            ").read_text(encoding='utf-8')); "
            f"sol = json.loads(Path({str(sol_p)!r})"
            ".read_text(encoding='utf-8')); "
            f"cat = json.loads(Path({str(registry.resolve_rel(catalog))!r})"
            ".read_text(encoding='utf-8')); "
            "wm = cat.get('wire_map') or {}; "
            "pe = proven_exclusions(cat, payload); "
            "allowed = set((wm.get(x) or {}).get('flightLegId', x) "
            "for x in pe['allowed']); "
            "r = check(payload, sol); "
            "missing = set(r.get('missing_legs') or []); "
            "r['complete'] = not missing; "
            "r['excluded_proven'] = sorted(missing & allowed); "
            "r['missing_unproven'] = sorted(missing - allowed); "
            "r['proofs_rejected'] = pe['rejected']; "
            "print(json.dumps(r, indent=1, ensure_ascii=False)); "
            "bad = (r.get('skill_mismatches') or r.get('duplicates') "
            "or r.get('unknown_flights') or r.get('unknown_employees') "
            "or (missing - allowed)); "
            "sys.exit(2 if bad else 0)"
        )
        return [[py, "-c", inline_cov], [py, "-c", inline]]

    if jtype == "multirole":
        # Фаза 2b: независимые пер-ролевые lane'ы (оркестратор гоняет
        # штатные stage1/stage2 CLI над ролевыми представлениями и сливает).
        # Только generic-экземпляры: GERAD-сиды одночленные по определению.
        if n is not None:
            raise JobError("multirole применим только к загруженным "
                           "наборам с multi-crew составом (Captain+FO)")
        budget_min = _int_param(params, "budget_min", 45)
        budget_s = _int_param(params, "budget_s", 600)
        seed = _int_param(params, "seed", 7)
        if budget_min <= 0 or budget_s <= 0:
            raise JobError("budget_min и budget_s должны быть положительными")
        return [[py, "_gerad_multirole.py",
                 "--payload", _child_path(inst["payload_path"], "набор"),
                 "--label", inst["label"],
                 "--stage1-budget-min", str(budget_min),
                 str(budget_s), str(seed)]]

    if jtype == "repair":
        # Disruption rebuild (Phase B): the whole neighbourhood pipeline —
        # colgen over surviving legs, catalog merge with pins, stage 2 —
        # lives in one driver keyed by the meta file prepare_rebuild wrote.
        # The path IS user-reachable via POST /api/jobs, so bind the meta
        # to the resolved instance: the job runs on the DERIVED repair
        # instance (already gated owner-or-admin upstream), and the meta
        # must name exactly that instance — otherwise any tenant could
        # replay another tenant's meta and read/overwrite their artifacts
        # (audit 2026-07-16, Phase B round 1).
        meta_path = Path(str(params.get("meta_path") or ""))
        try:
            valid = meta_path.is_file() and meta_path.resolve().is_relative_to(
                (_data_paths.results_dir(create=False) / "runs" / "repair").resolve())
        except OSError:
            valid = False
        if not valid:
            raise JobError("repair job needs a meta file under "
                           "final-report/runs/repair/")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobError(f"repair meta unreadable: {exc}") from exc
        if not isinstance(meta, dict) or meta.get("derived_id") != inst["id"]:
            raise JobError("repair meta does not belong to this instance")
        return [[py, "_disruption_repair.py", "--meta",
                 str(meta_path.resolve())]]

    raise JobError(f"unknown job type '{jtype}'")


def _stage2_artifact_completed(inst: dict, started_ts: float,
                               jtype: str = "stage2",
                               run_id: str | None = None) -> bool:
    """True if this run (re)wrote its verdict artifact — the signal that a
    nonzero exit is a gate verdict, not a crash. Artifacts are named by
    label in both modes (numeric labels are instN); multirole publishes its
    own merged-verdict artifact.

    Audit 2026-07-19 (P1-09): the primary proof is the run_id the child
    process stamps INTO the artifact (jobs passes it via CREW_RUN_ID) —
    file times are not lineage. Artifacts without a stamp (pre-fix runs)
    fall back to STRICT mtime >= started_ts: the old 1-second slack
    accepted a previous run's artifact as fresh, letting a new crash
    inherit a stale OK.

    P0-1 (аудит документации 2026-07-20): при не-OK вердикте канонический
    артефакт больше НЕ переписывается — первичным признаком завершения
    stage2-гейта служит per-run манифест <run_id>.json (он публикуется при
    любом вердикте); проверка канонического файла остаётся фолбэком
    (multirole-jtype и прогоны до фикса)."""
    if run_id and jtype != "multirole":
        try:
            from _roster_manifest import run_manifest_dir
            if (run_manifest_dir(inst["label"]) / f"{run_id}.json").exists():
                return True
        except Exception:   # noqa: BLE001 — фолбэк на канонический файл
            pass
    name = (f"gerad_multirole_{inst['label']}.json" if jtype == "multirole"
            else f"gerad_stage2_assignment_{inst['label']}.json")
    path = registry.FINAL_REPORT / name
    try:
        st = path.stat()
    except OSError:
        return False
    if run_id:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stamped = data.get("run_id") if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return False
        if stamped is not None:
            return stamped == run_id
    return st.st_mtime >= started_ts


def _stage1_catalog_completed(inst: dict, started_ts: float,
                              run_id: str | None = None) -> bool:
    """True если ЭТОТ прогон (пере)записал канонический каталог этапа 1 —
    признак, что ненулевой код построителя — вердикт покрытия, а не сбой.

    Контракт кодов возврата _pairing_builder: 4 = «непокрытое есть, но всё
    оно непокрываемо в принципе; каталог опубликован, признак внутри».
    Канонический файл пишется ТОЛЬКО при опубликованном вердикте (белый
    список у самого писателя, внешний аудит R7 P0-05; неопубликованное
    уезжает в .pending) — поэтому свежий канонический каталог и есть
    доказательство публикации. Личность прогона — штамп run_id внутри
    каталога (jobs передаёт CREW_RUN_ID); без штампа (ручные прогоны и
    каталоги до фикса) — строгий mtime >= started_ts, как у этапа 2."""
    path = (registry.FINAL_REPORT
            / f"gerad_colgen_selected_{inst['label']}.json")
    try:
        st = path.stat()
    except OSError:
        return False
    if run_id:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stamped = data.get("run_id") if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return False
        if stamped is not None:
            return stamped == run_id
    return st.st_mtime >= started_ts


def _freeze_run_config(run_id: str, inst: dict | None) -> dict | None:
    """Снимок конфигурации прогона, или None если снять не удалось.

    None вызывающий превращает в ОТКАЗ ЗАПУСКА (503): прогон по живым
    файлам запрещён с повторного аудита 2026-07-26 — «иногда замороженный»
    контракт хуже отсутствия контракта. (Пятый аудит, P2-04: прежняя
    редакция этой строки обещала обратное и расходилась с кодом.)"""
    from . import config_scope
    try:
        return config_scope.freeze_run(run_id, inst)
    except (OSError, ValueError) as exc:
        print(f"jobs: снимок конфигурации прогона {run_id} не сделан: {exc}",
              flush=True)
        return None


def _base_env(inst: dict | None = None) -> dict:
    """Environment for EVERY job subprocess: the inherited env plus
    CREW_RULES_CONFIG -> webapp/config/airline_default.json when that file
    exists. This is what makes the UI-saved airline config actually applied
    (rules_config.py on the Python side, the Kotlin GERAD profiles read the
    same variable).

    What-if scenarios (2026-07-15): a scenario instance carries ITS OWN
    rule file — it wins over the global airline config, so every stage of a
    scenario run is legal under the scenario's rules, not production's.

    Правила ЭКЗЕМПЛЯРА (P1-10, 2026-07-26): выбор файла целиком отдан
    `config_scope` — он же знает про уровень набора между установкой и
    сценарием. Без файла набора поведение прежнее: файл установки."""
    from . import config_scope
    env = os.environ.copy()
    path = config_scope.path_for(config_scope.DOMAIN_RULES, inst)
    if path is not None:
        env["CREW_RULES_CONFIG"] = str(path.resolve())
    # Объявленные ресурсные правила видит этап 2 — даже если исполнить их
    # некому: тогда его вердикт не может быть OK (P0-04).
    declared = _declared_resources(inst)
    if declared:
        env["CREW_RULES_DECLARED_RESOURCES"] = ",".join(declared)
    if config_scope.is_strict(config_scope.DOMAIN_RULES, inst):
        # Strict mode: the scenario's explicit sit/rest beat the stage-1
        # v2 profile presets (which otherwise silently override them —
        # the very rule change a what-if exists to test would be lost).
        env["CREW_RULES_STRICT"] = "1"
    return env


# ----------------------------------------------------------- lifecycle

def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        # /T: the stage scripts spawn JVM children that must die too.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def _watch(job: dict, commands: list[list[str]], env: dict) -> None:
    job_id = job["id"]
    exit_code = None
    failed = False
    log = _log_path(job_id)
    try:
        with open(log, "ab") as fh:
            for cmd in commands:
                with _lock:
                    if job_id in _stop_requested:
                        break
                fh.write(("=== run: " + " ".join(cmd) + "\n").encode("utf-8"))
                fh.flush()
                proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT),
                                        stdout=fh, stderr=subprocess.STDOUT,
                                        env=env)
                with _lock:
                    _procs[job_id] = proc
                    # Идентификатор процесса сохраняется НА ДИСК: после
                    # перезапуска сервера это единственное свидетельство,
                    # по которому можно узнать, жив ли расчёт, вместо
                    # того чтобы объявлять его остановленным вслепую.
                    job["pid"] = proc.pid
                    _save(job)
                exit_code = proc.wait()
                with _lock:
                    _procs.pop(job_id, None)
                if exit_code != 0:
                    failed = True
                    break
    except Exception as exc:              # noqa: BLE001 — must reach the log
        failed = True
        try:
            with open(log, "ab") as fh:
                fh.write(f"!! job runner error: {exc}\n".encode("utf-8"))
        except OSError:
            pass
    with _lock:
        stopped = job_id in _stop_requested
        _stop_requested.discard(job_id)
        _ports_in_use.pop(job_id, None)
        job["exit_code"] = exit_code
        job["ended_at"] = _now()
        if stopped:
            job["status"] = "stopped"
        elif not failed:
            job["status"] = "succeeded"
        elif (job["type"] in ("stage2", "multirole") and exit_code is not None
              and exit_code > 0
              and _stage2_artifact_completed(job["_inst"], job["_started_ts"],
                                             job["type"],
                                             job.get("_run_id"))):
            # Gate semantics: nonzero exit with completed artifacts is a
            # verdict, not a crash (contract, "Nonzero exit != crash").
            job["status"] = "succeeded"
            job["gate_nonzero"] = True
        elif (job["type"] == "stage1" and exit_code == 4
              and _stage1_catalog_completed(job["_inst"], job["_started_ts"],
                                            job.get("_run_id"))):
            # Та же gate-семантика для этапа 1: код 4 построителя — вердикт
            # покрытия (каталог опубликован, весь недобор доказуемо
            # непокрываем), а не сбой. Ровно код 4: коды 2/3 канонический
            # каталог не пишут, любой другой ненулевой код — настоящий сбой.
            job["status"] = "succeeded"
            job["gate_nonzero"] = True
        else:
            job["status"] = "failed"
        _save(job)
    _record_run_profile(job)


# Бюджет ЭТАПА в секундах — по типу задачи, а не по первому найденному
# параметру. Ревью волны 1: контур сравнивает бюджет с ПОЛНЫМ временем
# процесса задачи, поэтому сравнение честно только там, где процесс — это
# ровно один бюджетируемый этап: stage1 (весь колген под budget_min) и
# stage2 (весь солв под budget_s).
_BUDGET_PARAM: dict[str, tuple[str, float]] = {
    "stage1": ("budget_min", 60.0),
    "stage2": ("budget_s", 1.0),
}


def _budget_seconds(job: dict) -> float | None:
    """Отведённый бюджет времени задачи в секундах, если он применим.

    Для многоэтапных типов укладываемость НЕ ИЗМЕРИМА, и метрика честно
    отсутствует (None). У multirole в params лежат ОБА бюджета
    (budget_min на stage-1 и budget_s на stage-2), причём каждый — на одну
    ролевую полосу; число полос определяется составом экипажа в наборе, а
    не параметрами задачи, поэтому сумму этапов из params не восстановить.
    Прежний код брал budget_s и сравнивал бюджет одного этапа одной полосы
    со временем всего конвейера — укладываемость выходила вечным 0% и
    рождала ложное предложение «поднять бюджет». Лучше без метрики, чем с
    заведомо неверной. То же для complete/validate/repair: у них бюджета
    времени нет вовсе (repair-драйвер сам порождает свои этапы)."""
    key_factor = _BUDGET_PARAM.get(str(job.get("type") or ""))
    if key_factor is None:
        return None
    key, factor = key_factor
    value = (job.get("params") or {}).get(key)
    if value is None:
        return None
    try:
        return float(value) * factor
    except (TypeError, ValueError):
        return None


# Флаги командной строки, чьи значения ОПИСЫВАЮТ конфигурацию прогона.
# Всё остальное (пути, имена, признаки дампа) — про «что считаем», а не
# «как», и в описание конфигурации не входит.
_CONFIG_FLAGS = ("--pricer", "--profile", "--mp", "--price-cap",
                 "--final-tl", "--max-landings", "--total-cap",
                 "--cert-budget", "--bases",
                 # Настройки построителя цепочек (аудит 2026-07-26, P1-16):
                 # без них два разных прогона первого этапа получали ОДНУ
                 # подпись `config_sig`, и сравнить их было не по чему —
                 # ни в истории задач, ни при повторном использовании пула.
                 "--level", "--method", "--budget-s", "--seed",
                 "--solve-s", "--w-night", "--w-dh",
                 "--rescue-dh", "--rescue-per-leg", "--rescue-s",
                 # R5-03: отпечаток ресурсных правил — часть постановки
                 "--resource-rules-fp",
                 # R6 P0-02: отпечаток правил системы-источника — часть
                 # постановки этапа 1
                 "--source-rules-fp",
                 # R10 (отсрочка №3): контент-отпечаток каталога этапа 1 —
                 # часть постановки этапа 2
                 "--catalog-fp")


def resolved_config(commands: list[list[str]]) -> dict:
    """Фактическая конфигурация прогона — из САМИХ командных строк.

    Наблюдение волны 2. Раньше конфигурация не записывалась нигде:
    умолчания разрешались на лету (`knobs.get("pricer", "duty")`) и
    обратно в задачу не попадали, поэтому пустой `params` означал «то,
    что было умолчанием ТОГДА». Прогон полугодовой давности становился
    неотличим от вчерашнего, и сравнивать два прогона было не по чему.

    Берём значения не из знаний о ветвлениях `_build_commands`, а из его
    результата: команда — это и есть истина о том, что запустилось, и у
    неё нет восьми точек возврата, каждую из которых можно забыть."""
    out: dict = {}
    for cmd in commands or []:
        argv = [str(a) for a in cmd]
        script = next((a for a in argv if a.endswith(".py")), None)
        if script:
            out.setdefault("script", Path(script).name)
        for i, arg in enumerate(argv):
            if arg in _CONFIG_FLAGS and i + 1 < len(argv):
                out[arg.lstrip("-").replace("-", "_")] = argv[i + 1]
        # R5-03: у этапа 2 бюджет и зерно ПОЗИЦИОННЫЕ — флагами их не
        # поймать, а прогоны 600,7 и 900,9 давали одну подпись задачи.
        if script and script.endswith("_gerad_stage2_roster.py"):
            tail = [a for a in argv[1:] if a.replace(".", "").isdigit()]
            if len(tail) >= 2:
                out["stage2_budget_s"], out["stage2_seed"] = tail[-2], tail[-1]
    return out


def config_signature(cfg: dict) -> str:
    """Короткая подпись конфигурации — ключ для будущего сравнения.

    Две задачи с одной подписью запускались одинаково; с разными —
    по-разному, и сравнивать их результаты как «один и тот же прогон»
    нельзя."""
    canon = json.dumps(cfg, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _record_run_profile(job: dict) -> None:
    """Наблюдение контура «Надёжность стенда».

    Сбор статистики НИКОГДА не роняет задачу: её исход уже зафиксирован и
    сохранён выше, а телеметрия — вспомогательный сигнал. Любая ошибка
    (битое хранилище, отказ записи) молча проглатывается."""
    try:
        from . import run_profile
        started = job.get("_started_ts")
        run_profile.record_run(
            jtype=job.get("type"),
            label=(job.get("_inst") or {}).get("label") or job.get("id"),
            instance_id=job.get("instance_id"),
            config_sig=job.get("config_sig"),
            resolved_config=job.get("resolved_config"),
            budget_s=_budget_seconds(job),
            wall_s=(max(0.0, time.time() - started)
                    if isinstance(started, (int, float)) else None),
            outcome=job.get("status"),
            exit_code=job.get("exit_code"),
            gate_nonzero=bool(job.get("gate_nonzero")))
    except Exception:                     # noqa: BLE001 — телеметрия молчит
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------- public API

def _reserve_or_conflict(jtype: str, inst: dict) -> None:
    """Отказать дубликату ДО материализации чего бы то ни было (R5-02).

    Ту же проверку повторяет основной замок ниже — она дешёвая, а этот
    ранний проход нужен, чтобы отклонённый запрос не успел создать файл
    ресурсных правил."""
    with _lock:
        for other in _jobs.values():
            if other.get("status") != "running":
                continue
            if other.get("instance_id") != inst["id"]:
                continue
            if other.get("type") == jtype:
                raise JobError(
                    f"a {jtype} job for {inst['id']} is already running "
                    f"({other['id']})", http_status=409)


def start_job(jtype: str, inst: dict, params: dict, owner: str) -> str:
    """owner: email of the authenticated user starting the run. Every job
    record carries it; listing/log/stop access is owner-or-admin (enforced
    in app.py against request.state.user). Runs on SHARED instances are
    owned by the runner too — but the run's RESULTS land in the shared
    final-report artifacts and stay visible to everyone."""
    _load_persisted()
    if jtype not in JOB_TYPES:
        raise JobError(f"unknown job type '{jtype}'")
    if not isinstance(owner, str) or not owner:
        raise JobError("job owner email is required")   # wiring bug, loud
    params = dict(params or {})
    # СНАЧАЛА резервирование, потом материализация (пятый аудит, R5-02):
    # прежде команды строились ДО замка, и отклонённый дубликат успевал
    # создать файл ресурсных правил — отказанный запрос оставлял артефакт.
    # Проверка «такая задача уже идёт» вынесена в отдельный проход под тем
    # же замком, до единственного построения команд.
    _reserve_or_conflict(jtype, inst)
    run_id = uuid.uuid4().hex[:12]
    try:
        commands = _build_commands(jtype, inst, params, run_id)
    except BaseException:
        drop_resource_rules(run_id)          # ничего не оставляем за собой
        raise
    _resolved = resolved_config(commands)

    with _lock:
        for other in _jobs.values():
            if other.get("status") != "running":
                continue
            if other.get("instance_id") != inst["id"]:
                continue
            if other.get("type") == jtype:
                drop_resource_rules(run_id)
                raise JobError(
                    f"a {jtype} job for {inst['id']} is already running "
                    f"({other['id']})", http_status=409)
            # stage2 and repair both publish the SAME label-named stage-2
            # artifacts (last writer wins) — running them concurrently on
            # one instance corrupts report provenance (Phase-B audit).
            if {jtype, other.get("type")} == {"stage2", "repair"}:
                raise JobError(
                    f"a {other.get('type')} job for {inst['id']} is "
                    f"already running ({other['id']}) — stage2 and repair "
                    f"are mutually exclusive per instance", http_status=409)

        env = _base_env(inst)
        if jtype in ("stage2", "repair", "multirole"):
            # repair's and multirole's drivers spawn their own stage-2
            # subprocesses, which inherit STAGE2_PORT from this env — same
            # isolation contract (multirole lanes run SEQUENTIALLY on it).
            port = _allocate_stage2_port()
            env["STAGE2_PORT"] = str(port)
            if jtype in ("stage2", "multirole"):
                params.setdefault("budget_s", 600)
                params.setdefault("seed", 7)
        elif jtype == "stage1":
            params.setdefault("budget_min", 75)
            if isinstance(params, dict) and params.get("deterministic_time"):
                # Д1 плана прямого хода: зерно хэша процесса-построителя
                # ставит ЗАПУСКАЮЩИЙ; построитель записывает факт в
                # профиль (python_hash_seed_env).
                env["PYTHONHASHSEED"] = "0"

        started_ts = time.time()
        # Audit 2026-07-19 (P1-09): every run gets a random id, passed to
        # the child via CREW_RUN_ID; the child stamps it into its verdict
        # artifacts, and completion checks compare ids, not file times.
        # ОДИН id на прогон (R5): тот же, под которым материализован файл
        # ресурсных правил, — второй uuid здесь давал бы два разных id
        # одному прогону.
        env["CREW_RUN_ID"] = run_id
        # P1-10 (2026-07-26): прогон читает СНИМОК конфигурации, а не живой
        # файл. Правку, сделанную между этапом 1 и этапом 2, половины
        # системы иначе увидят по-разному, и расхождение будет тихим.
        frozen = _freeze_run_config(run_id, inst)
        if frozen is None:
            # Отказ снимка = отказ запуска (повторный аудит 2026-07-26).
            # Прежде прогон молча продолжал по живым файлам — ровно то
            # тихое расхождение половин, ради которого заморозка и
            # существует; «иногда замороженный» контракт хуже отсутствия
            # контракта, потому что на него полагаются.
            raise JobError("не снят снимок конфигурации прогона — запуск "
                           "отклонён, чтобы этапы не разошлись по правилам; "
                           "подробности в журнале приложения",
                           http_status=503)
        env["CREW_RULES_CONFIG"] = frozen["files"]["rules"]
        env["CREW_RUN_CONFIG_DIR"] = str(frozen["dir"])
        # Единый манифест постановки (аудит R10, P0-03): контент-хэши
        # всего, что образует задачу, записанные ОДИН раз и неизменяемо.
        # Отказ манифеста = отказ запуска — прогон без личности запрещён
        # тем же правилом, что прогон без снимка конфигурации.
        from . import problem_manifest
        try:
            pm = problem_manifest.build(jtype, inst, commands, run_id,
                                        frozen, _resolved)
            pm_path = problem_manifest.write(frozen["dir"], pm)
        except problem_manifest.ManifestError as exc:
            raise JobError(
                f"манифест постановки не построен: {exc} — запуск "
                f"отклонён, чтобы прогон не остался без личности",
                http_status=503) from exc
        env["CREW_PROBLEM_MANIFEST"] = str(pm_path)
        env["CREW_PROBLEM_MANIFEST_ID"] = pm["manifest_id"]
        job_id = f"{jtype}-{inst['id']}-{time.strftime('%H%M%S')}"
        if job_id in _jobs:              # same second re-submit
            raise JobError("job id collision; retry in a second",
                           http_status=409)
        job = {
            "id": job_id,
            "type": jtype,
            "instance_id": inst["id"],
            "owner": owner,
            "status": "running",
            "exit_code": None,
            "started_at": _now(),
            "ended_at": None,
            "params": params,
            # `params` — то, что ЗАДАЛ человек; `resolved_config` — то, с
            # чем прогон реально пошёл, вместе с умолчаниями. Пустой
            # params больше не означает «неизвестно как».
            "resolved_config": _resolved,
            "config_sig": config_signature(_resolved),
            # Личность постановки — видна в API задач и в витрине.
            "problem_manifest_id": pm["manifest_id"],
            "_inst": inst,
            "_started_ts": started_ts,
            "_run_id": run_id,
        }
        if jtype in ("stage2", "repair", "multirole"):
            _ports_in_use[job_id] = port
            job["stage2_port"] = port
        _jobs[job_id] = job
        _save(job)

    threading.Thread(target=_watch, args=(job, commands, env),
                     daemon=True, name=f"job-{job_id}").start()
    return job_id


def list_jobs() -> list[dict]:
    _load_persisted()
    with _lock:
        jobs = [_public(j) for j in _jobs.values()]
    # Newest first; started_at is second-resolution so tie-break on id.
    return sorted(jobs, key=lambda j: (j.get("started_at") or "", j["id"]),
                  reverse=True)


def get_job(job_id: str) -> dict | None:
    _load_persisted()
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        out = _public(job)
    out["log_tail"] = _tail(_log_path(job_id), LOG_TAIL_LINES)
    return out


def read_log(job_id: str) -> str | None:
    _load_persisted()
    with _lock:
        if job_id not in _jobs:
            return None
    try:
        return _log_path(job_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def stop_job(job_id: str) -> dict:
    _load_persisted()
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise JobError("no such job", http_status=404)
        if job.get("status") != "running":
            raise JobError(f"job is not running (status={job['status']})",
                           http_status=409)
        _stop_requested.add(job_id)
        proc = _procs.get(job_id)
    if proc is not None:
        _kill_tree(proc)
    return {"ok": True}


def delete_job(job_id: str) -> dict:
    """Удаление ЗАПИСИ прогона из истории задач (проверка инструкции
    2026-08-12: неудачные прогоны копились в списке без возможности
    прибраться). Удаляются только запись и журнал ВЫПОЛНЕНИЯ; артефакты
    результатов (каталоги, решения, вердикты, след схождения) — не
    трогаются: они самостоятельные измерительные записи. Работающую
    задачу удалить нельзя — сначала остановка."""
    _load_persisted()
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise JobError("no such job", http_status=404)
        if job.get("status") == "running":
            raise JobError("job is running — stop it first",
                           http_status=409)
        _jobs.pop(job_id, None)
        _procs.pop(job_id, None)
        _ports_in_use.pop(job_id, None)
    for p in (_job_path(job_id), _log_path(job_id)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": True, "deleted": job_id}


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def progress_meta(job_id: str) -> dict:
    """Прошедшее время и бюджет прогона для графика схождения
    (замечание владельца 2026-08-12: на длинных тихих участках график
    обязан показывать «1340/3600»). Считается СЕРВЕРОМ от _started_ts —
    часовые зоны браузера и площадки не участвуют. elapsed_s отдаётся
    только для работающей задачи; завершённая берёт время из следа."""
    _load_persisted()
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return {}
        meta: dict = {}
        b = _budget_seconds(job)
        if b:
            meta["budget_s"] = b
        ts = job.get("_started_ts")
        if job.get("status") == "running" and ts:
            meta["elapsed_s"] = round(time.time() - float(ts), 1)
        return meta


def _run_id_by_manifest(manifest_id: str) -> str | None:
    """Личность прогона по идентификатору его постановки.

    Манифест постановки лежит в каталоге конфигурации прогона, ИМЕНЕМ
    которого и служит личность, а сам идентификатор постановки хранится
    в публичной записи задачи. Это позволяет вернуть личность записям,
    сделанным до того, как она стала сохраняться, — то есть вернуть
    график схождения прогонам, пережившим перезапуск сервера."""
    try:
        from . import config_scope, problem_manifest
        root = config_scope.RUNS_CONFIG_DIR
        for d in sorted(root.iterdir(), reverse=True):
            path = d / problem_manifest.FILENAME
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict) and doc.get("manifest_id") == manifest_id:
                return d.name
    except (OSError, AttributeError, ImportError):
        return None
    return None


def run_id_of(job_id: str) -> str | None:
    """Личность прогона (CREW_RUN_ID) для серверных читателей артефактов
    прогона (след схождения); наружу в публичный контракт не выдаётся.

    Записи прежних прогонов личности не несут (до 2026-08-13 она жила
    только в памяти) — для них она восстанавливается по идентификатору
    постановки и ЗАПИСЫВАЕТСЯ: восстановление разовое, а не при каждом
    открытии графика."""
    _load_persisted()
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        rid = job.get("_run_id")
        if rid:
            return rid
        manifest_id = job.get("problem_manifest_id")
    if not manifest_id:
        return None
    rid = _run_id_by_manifest(str(manifest_id))
    if rid:
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                job["_run_id"] = rid
                _save(job)
    return rid


def _tail(path: Path, n: int) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 64 * 1024))
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]
