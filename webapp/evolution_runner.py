"""COSMOS Evolution orchestrator: nightly research scan + the
Test -> Stage -> Prod improvement pipeline.

Data-contour invariant (user directive, 2026-07-13):
  * EXTERNAL contour = the research agent and the Test install
    (cosmos-test): public data only (GERAD/BTS seed instances,
    publications); cloud LLMs are ALLOWED. The Claude Code CLI is
    invoked EXCLUSIVELY with cwd inside COSMOS_TEST_DIR — both the
    nightly scan and the implementation phase.
  * INTERNAL contour = Prod (this install) and Stage (cosmos-stage):
    customer data never leaves the machine; NO external AI calls.
    Stage/Prod phases run only local git / pytest / benchmark commands.
    The only outbound traffic they cause is the admin's own Telegram
    notification (pipeline metadata and public-benchmark metrics, never
    customer data content).

Autonomy contract (explicitly approved by the admin): after the
"Внедрить на Test" button, the coding agent runs UNATTENDED with the
permission gate disabled — ONLY inside the isolated cosmos-test clone
(its own folder/venv; git-ignored customer uploads are absent from
clones by construction). Stage and Prod receive code exclusively via
git merge of what was already written and reviewed by the pipeline; no
AI edits happen there.

Every phase is resumable through the improvements store statuses and is
wrapped so a failure marks <phase>_failed and notifies the admin instead
of killing the worker thread.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path

from . import evidence, settings
from .registry import REPO_ROOT

_TF_BASE = r"C:\work\Sandbox\Testing\TimeFold"

CONFIG_DIR = REPO_ROOT / "webapp" / "config"
LOG_PATH = CONFIG_DIR / "evolution.log"
LAST_SCAN_PATH = CONFIG_DIR / "evolution-last-scan.txt"
# Отметка «когда контур наблюдал в последний раз» для страницы «Контуры».
# Отдельный файл от LAST_SCAN_PATH намеренно: тот хранит КАЛЕНДАРНЫЙ ДЕНЬ и
# служит суточным предохранителем расписания (ручной скан его не трогает,
# иначе подавил бы ночной), а этот — полную метку времени УДАВШЕГОСЯ скана,
# ручного и ночного, включая скан, не нашедший ничего нового. Провалившийся
# скан отметку НЕ обновляет: см. run_scan.
LAST_SCAN_AT_PATH = CONFIG_DIR / "evolution-last-scan-at.txt"

SCAN_TIMEOUT_S = 40 * 60
IMPLEMENT_TIMEOUT_S = 4 * 3600
QUICK_BENCH_TIMEOUT_S = 4 * 3600
FULL_BENCH_TIMEOUT_S = 16 * 3600
PYTEST_TIMEOUT_S = 3600

_lock = threading.Lock()
_scheduler_started = False

# Serializes ALL working-tree mutation across the pipeline: the scan (CLI
# in test_dir) and the test/stage/deploy phases each git-checkout/merge and
# run benchmarks in shared clones. Phases are long, a single admin drives
# them, and the machine is CPU-bound to one battery at a time anyway — so a
# global lock is both correct and cheap. Held for a whole phase.
_pipeline_lock = threading.Lock()


class ContourViolation(RuntimeError):
    """The external-contour invariant would be broken (the sandbox agent
    would run inside an internal-contour checkout)."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    return _env("CREW_EVOLUTION_ENABLED") == "1"


def prod_dir() -> Path:
    return Path(_env("COSMOS_PROD_DIR", str(REPO_ROOT)))


def prod_port() -> str:
    return _env("COSMOS_PROD_PORT", "8090")


def stage_dir() -> Path:
    return Path(_env("COSMOS_STAGE_DIR", _TF_BASE + r"\cosmos-stage"))


def test_dir() -> Path:
    return Path(_env("COSMOS_TEST_DIR", _TF_BASE + r"\cosmos-test"))


def claude_cli() -> str:
    return _env("CREW_CLAUDE_CLI", "claude")


def scan_hour() -> int:
    return int(_env("CREW_EVOLUTION_SCAN_HOUR", "3"))


def _venv_python(repo: Path) -> str:
    return str(repo / ".venv" / "Scripts" / "python.exe")


def _assert_external_contour() -> None:
    """Refuse to run the sandbox agent unless the Test checkout is a
    distinct directory from Prod AND Stage — the CLI runs with the
    permission gate disabled, so it must never touch an internal-contour
    tree that holds customer uploads or the live prod/stage code."""
    test = test_dir().resolve()
    for other, name in ((prod_dir().resolve(), "prod"),
                        (stage_dir().resolve(), "stage")):
        if test == other or other in test.parents or test in other.parents:
            raise ContourViolation(
                f"COSMOS_TEST_DIR ({test}) overlaps the {name} install "
                f"({other}); the sandbox agent must run in an isolated "
                f"external-contour checkout")


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(f"EVOLUTION: {line}", flush=True)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass                       # the journal must never break a phase


# ----------------------------------------------------- scan activity feed

# Live feed of what the research agent is doing (search queries, fetched
# URLs, reasoning snippets, findings) so the «Лаборатория» tab can show
# progress instead of silence. In-memory ring buffer with a monotonic
# sequence so the frontend can poll incrementally.
_activity_lock = threading.Lock()
_activity: list[dict] = []
_activity_seq = 0
_activity_state = {"running": False, "started_at": None, "finished_at": None}
_ACTIVITY_MAX = 400
_ACTIVITY_DETAIL_MAX = 500


def _activity_reset() -> None:
    global _activity_seq
    with _activity_lock:
        _activity.clear()
        _activity_seq = 0
        _activity_state["running"] = True
        _activity_state["started_at"] = datetime.now().isoformat(
            timespec="seconds")
        _activity_state["finished_at"] = None


def _activity_add(kind: str, detail: str = "", data: dict | None = None) -> None:
    """Append one live-feed event. `detail` is a ready Russian string (used
    by Telegram and as a fallback); `data`, when present, is a structured
    payload the web UI localizes into the operator's language (so the
    per-instance summaries and humanized progress read in all 5 languages,
    not only Russian)."""
    global _activity_seq
    with _activity_lock:
        _activity_seq += 1
        ev = {
            "seq": _activity_seq,
            "kind": kind,
            "detail": (detail or "")[:_ACTIVITY_DETAIL_MAX],
        }
        if data is not None:
            ev["data"] = data
        _activity.append(ev)
        if len(_activity) > _ACTIVITY_MAX:
            del _activity[0]


def _activity_finish() -> None:
    with _activity_lock:
        _activity_state["running"] = False
        _activity_state["finished_at"] = datetime.now().isoformat(
            timespec="seconds")


def get_scan_activity(since: int = 0) -> dict:
    """Snapshot of the live scan feed for the UI. `since` is the last seq
    the client already has; only newer events are returned."""
    with _activity_lock:
        events = [e for e in _activity if e["seq"] > since]
        return {
            "running": _activity_state["running"],
            "started_at": _activity_state["started_at"],
            "finished_at": _activity_state["finished_at"],
            "last_seq": _activity_seq,
            "events": events,
        }


# ------- user-initiated stop of a running Test phase (item 1) -------------
# A single phase runs one subprocess at a time; we track it so an admin's
# "stop" can kill the tree, and a cancel Event so the phase salvages the
# partial results already computed instead of reporting a hard failure.
_cancel = threading.Event()
_proc_lock = threading.Lock()
_active_proc: subprocess.Popen | None = None


def _set_active(proc: subprocess.Popen) -> None:
    global _active_proc
    with _proc_lock:
        _active_proc = proc


def _clear_active(proc: subprocess.Popen) -> None:
    global _active_proc
    with _proc_lock:
        if _active_proc is proc:
            _active_proc = None


def request_stop(imp_id: int) -> bool:
    """Signal the running Test phase to stop and report partial results.
    Only valid while the improvement is actually 'testing'. Returns True
    when a stop was signalled."""
    from . import evolution
    if evolution.get(imp_id)["status"] != evolution.STATUS_TESTING:
        return False
    _cancel.set()
    _activity_add("note", "запрошена остановка — завершаю текущий шаг…")
    with _proc_lock:
        proc = _active_proc
    if proc is not None:
        _kill_tree(proc)
    return True


def _build_comparison(base_doc: dict | None, cand_doc: dict | None) -> list:
    """Structured per-instance/per-stage metric comparison for the detail
    panel: [{inst, stages: [{stage, error(base/cand), rows: [{metric,
    base, cand, delta_pct, cls}]}]}]. Polarity and the metric catalog come
    from _evolution_bench (the single source the compare gate itself uses),
    so the panel can never disagree with the verdict."""
    import _evolution_bench as eb
    base_i = (base_doc or {}).get("instances") or {}
    cand_i = (cand_doc or {}).get("instances") or {}
    labels = sorted(set(base_i) | set(cand_i),
                    key=lambda s: (len(s), s))          # inst1..instN
    out = []
    for label in labels:
        b_inst, c_inst = base_i.get(label) or {}, cand_i.get(label) or {}
        stages = []
        for stage, metrics in (("stage1", eb.STAGE1_METRICS),
                               ("stage2", eb.STAGE2_METRICS)):
            b_s, c_s = b_inst.get(stage) or {}, c_inst.get(stage) or {}
            rows = []
            for m in metrics:
                b, c = b_s.get(m), c_s.get(m)
                if b is None and c is None:
                    continue
                cls = eb._classify(m, b, c)
                delta = None
                if (cls in ("better", "worse", "equal")
                        and not isinstance(b, bool)
                        and not isinstance(c, bool)
                        and isinstance(b, (int, float))
                        and isinstance(c, (int, float)) and b):
                    delta = round((c - b) / abs(b) * 100.0, 2)
                rows.append({"metric": m, "base": b, "cand": c,
                             "delta_pct": delta, "cls": cls})
            stages.append({"stage": stage,
                           "base_error": b_s.get("error"),
                           "cand_error": c_s.get("error"),
                           "rows": rows})
        out.append({"inst": label, "stages": stages})
    return out


def get_test_report(imp_id: int) -> dict:
    """The stored Test-phase comparison report plus the analytical review
    AND a structured baseline-vs-candidate comparison, for the detail panel
    (item 3). Raises if the improvement id is unknown; a missing report
    file is an empty string, a missing metrics side an empty comparison."""
    from . import evolution
    row = evolution.get(imp_id)
    try:
        md = (CONFIG_DIR / f"evolution-report-{imp_id}-test.md").read_text(
            encoding="utf-8")
    except OSError:
        md = ""
    baseline_doc = None
    try:
        baseline_doc = json.loads(
            (CONFIG_DIR / "evolution-baseline-quick.json").read_text(
                encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    cand_doc = row.get("test_metrics")
    try:
        comparison = _build_comparison(baseline_doc, cand_doc)
    except Exception as exc:                            # noqa: BLE001
        _log(f"comparison build failed for {imp_id}: {exc}")
        comparison = []
    return {"title": row["title"], "status": row["status"],
            "review": row.get("review"), "report_md": md,
            "baseline_commit": (baseline_doc or {}).get("commit"),
            "candidate_commit": (cand_doc or {}).get("commit"),
            "comparison": comparison}


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child AND its descendants. proc.kill() alone terminates
    only the cmd.exe trampoline of a .cmd entrypoint (claude.cmd): the
    node grandchild survives, keeps the inherited pipe handles open, and
    the parent's reads/writes stay blocked past every deadline."""
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
    except OSError:
        pass


# The candidate's unit tests must run in the SAME clean environment a
# developer or continuous-integration job uses. The Prod webapp process
# carries live runtime environment (CREW_AUTH_MODE=access,
# CREW_EVOLUTION_ENABLED=1, CREW_TG_PRIMARY=1, ...); inherited into
# `pytest -q` it flips the auth/evolution defaults the suite assumes and
# reddens tests that expect the unset default — a FALSE Test/Stage/Prod
# gate failure unrelated to the candidate. Scrub these prefixes for gate
# test runs (never for the agent CLI, which needs CLAUDE_CODE_OAUTH_TOKEN).
_GATE_ENV_SCRUB: tuple[str, ...] = ("CREW_", "CLAUDE_", "COSMOS_")

# Секреты, которые агенту внедрения НЕ нужны и потому ему не достаются
# (аудит 2026-07-26, P0-01). Полной изоляции это не заменяет — она
# требует отдельной учётной записи или одноразовой машины, то есть
# инфраструктуры, а не правки кода, — но убирает самое дешёвое:
# служебные токены туннеля и ключ облачной модели из окружения процесса,
# который исполняет недоверенный план. `CLAUDE_CODE_OAUTH_TOKEN` остаётся:
# без него не запустится сам агент.
_AGENT_ENV_DENY: tuple[str, ...] = (
    "TUNNEL_SERVICE_TOKEN_ID", "TUNNEL_SERVICE_TOKEN_SECRET",
    "ANTHROPIC_API_KEY", "CREW_CLAUDE_API_KEY", "TELEGRAM_BOT_TOKEN",
    "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET",
    # Повторный аудит 2026-07-26: эти два жили вне списка. Ключ локальной
    # модели и настоящий токен Telegram-бота (модуль читает CREW_TG_*,
    # а не TELEGRAM_*) агенту не нужны и не положены.
    "CREW_OLLAMA_KEY", "CREW_TG_BOT_TOKEN",
    # Третий аудит 2026-07-26 (§5.3): продукт читает ключ Anthropic из
    # CREW_ANTHROPIC_API_KEY (webapp/settings.py), а пароль почты — из
    # CREW_SMTP_PASS (webapp/mailer.py). Мой же контрольный скрипт
    # проверял выдуманное имя ANTHROPIC_API_KEY — тот самый класс дефекта,
    # который я записал уроком днём раньше. Список сверен с настоящими
    # чтениями grep-ом по webapp/, и страж ниже держит его в синхроне.
    "CREW_ANTHROPIC_API_KEY", "CREW_SMTP_PASS",
    # Путь к ключу подписи свидетельств (пятый аудит, пакет C1). Найдено
    # СТРАЖЕМ этого же списка сразу после появления `webapp/evidence.py`:
    # знай агент, где лежит ключ, он подписал бы себе любое свидетельство
    # — и весь пакет C1 обесценился бы в тот же день, когда написан.
    "COSMOS_EVIDENCE_KEY",
    # Машинный токен канала пакета постановки (2026-08-29). Найдено
    # СТРАЖЕМ при появлении webapp/task_channel.py: с токеном агент мог
    # бы скармливать приёмнику свои «пакеты постановки» мимо человека.
    "CREW_CHANNEL_TOKEN",
)

# Пути, которых изменение кандидата касаться не должно: в них живут сами
# правила проверки кандидата, доступ и развёртывание (аудит 2026-07-26,
# P0-02). Кандидат, правящий батарею тестов или конвейер продвижения, сам
# определяет доказательство своей пригодности.
_DIFF_FORBIDDEN: tuple[str, ...] = (
    "webapp/evolution_runner.py", "webapp/evolution.py",
    "webapp/users.py", "webapp/settings.py", "webapp/admin_api.py",
    "webapp/telegram_bot.py", "_evolution_bench.py",
    "tests/conftest.py", "requirements.txt", "requirements-lock.txt",
    ".github/", ".claude/",
    # Пятый аудит (P0-02): перехват исполнения ДО всякого теста. Корневой
    # conftest и sitecustomize исполняются раньше любого кода набора;
    # pytest.ini/pyproject задают, что вообще будет собрано; tools/ —
    # сам контрольный список; mvnw* — сборка решателя; хуки git
    # исполняются на каждую операцию.
    "conftest.py", "sitecustomize.py", "pytest.ini", "setup.cfg",
    "pyproject.toml", "tox.ini", "tools/",
    "kotlin-solver/mvnw", "kotlin-solver/mvnw.cmd",
    "kotlin-solver/.mvn/", ".git/",
    # Внешний аудит R7 (P0-06б): свидетельства, посредник, авторизация,
    # маршрутизация приложения и личность сборки — кандидату менять
    # нельзя: правка любого из них обесценивает саму границу доверия.
    "webapp/evidence.py", "webapp/agent_broker.py", "webapp/auth.py",
    "webapp/app.py", "webapp/build_identity.py",
    # R8 (P0-04в): правка ignore-правил прячет файлы от контроля чистоты
    ".gitignore", ".gitattributes",
)


# Доверенная батарея проверки (аудит 2026-07-26, P0-02; шаг 3 плана
# укрепления). Код продукта даёт кандидат, но ВСЁ, чем его судят, всегда
# берётся из доверенной базовой ревизии: так кандидат физически не может
# подменить то, чем его меряют. Принцип SLSA — «сборку определяет служба
# сборки, а не исходник».
#
# Список намеренно УЗКИЙ. `tests/` целиком сюда не входит: кандидат обязан
# уметь добавить тесты на свою же правку, иначе проверка зелена ровно там,
# где ничего не проверено. Запрещается менять то, что решает, ЧТО и КАК
# запускается и чем меряется:
_TRUSTED_HARNESS: tuple[str, ...] = (
    "tests/conftest.py",      # сбор тестов, фикстуры, исключения
    "_evolution_bench.py",    # измерительный прибор
    "pyproject.toml",         # зависимости и настройки прогона
)


def _restore_trusted_harness(repo, base_head: str) -> list[str]:
    """Вернуть файлы доверенной батареи из базовой ревизии в рабочее
    дерево кандидата. Возвращает список возвращённых путей."""
    restored: list[str] = []
    for path in _TRUSTED_HARNESS:
        code, _out = _run(["git", *_git_safe_flags(), "cat-file", "-e", f"{base_head}:{path}"],
                          cwd=repo, timeout_s=30)
        if code != 0:
            continue                       # файла в базе нет — нечего брать
        code, _out = _run(["git", *_git_safe_flags(), "checkout", base_head, "--", path],
                          cwd=repo, timeout_s=60)
        if code == 0:
            restored.append(path)
    return restored


def _release_trusted_harness(repo) -> None:
    """Вернуть рабочее дерево к состоянию коммита кандидата: проверка
    закончена, и следующий шаг не должен видеть подменённые файлы."""
    for path in _TRUSTED_HARNESS:
        _run(["git", *_git_safe_flags(), "checkout", "HEAD", "--", path], cwd=repo, timeout_s=60)


def _lay_trusted_harness(dst, repo, base_sha: str) -> list[str]:
    """Файлы доверенной батареи из АТТЕСТОВАННОЙ базовой ревизии — в
    материализованное дерево кандидата (пакет C, аудит R10 P0-04).

    Через `git archive` выбранных путей: бинарно-безопасно и без чекаута
    в дерево кандидата — репозиторий здесь только читается."""
    import zipfile
    present = [p for p in _TRUSTED_HARNESS
               if _git(Path(repo), "cat-file", "-e",
                       f"{base_sha}:{p}")[0] == 0]
    if not present:
        return []
    bundle = Path(dst) / "_trusted-harness.zip"
    code, out = _git(Path(repo), "archive", "--format=zip", "-o",
                     str(bundle), base_sha, "--", *present)
    if code != 0 or not bundle.exists():
        raise MaterializeError(
            f"доверенная батарея из {base_sha[:12]} не извлечена: "
            f"{(out or '')[:200]}")
    try:
        with zipfile.ZipFile(bundle) as z:
            z.extractall(Path(dst))
    finally:
        bundle.unlink(missing_ok=True)
    return present


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _broker_exec(argv: list, cwd, run_id: str,
                 timeout_s: float) -> tuple[int, str]:
    """Исполнить команду ПОД ЗАПИСЬЮ АГЕНТА через посредника (пакет C,
    аудит R10 P0-04): проверка кандидата не должна идти под подписантом.

    Предел назван вслух: посредник ограничивает УЧЁТНУЮ ЗАПИСЬ (без
    секретов, ключа свидетельств и деревьев Stage/Prod), а целостность
    кода завершения внутри прогона остаётся на той же записи — это
    строго меньшая привилегия, чем прежний запуск под приложением."""
    from . import agent_broker
    script = ("$ErrorActionPreference = 'Continue'; "
              "Set-Location " + _ps_quote(cwd) + "; "
              "& " + " ".join(_ps_quote(a) for a in argv) + "; "
              "exit $LASTEXITCODE")
    hand = agent_broker.launch(
        "exec: " + " ".join(str(a) for a in argv),
        run_id=run_id, exec_script=script)
    code, out, diag = _broker_wait(hand, timeout_s=timeout_s)
    return code, (out or diag or "")


# Предполётная проверка изоляции (шаг 0 плана укрепления). Агент
# внедрения исполняет недоверенный план, притом текст находки приходит из
# веба. Пока он работает под учётной записью, которая видит секреты,
# рабочую базу пользователей и деревья Prod/Stage, никакая проверка кода
# после факта этого не компенсирует. Проверка ОТКАЗЫВАЕТ по умолчанию;
# снять отказ можно осознанно, переменной, и это попадает в журнал.
_UNISOLATED_OK = "COSMOS_EVOLUTION_ALLOW_UNISOLATED"


def _unisolated_allowed_by_env() -> bool:
    """Снят ли отказ изоляции переменной среды.

    Только явное «1» (повторный аудит 2026-07-26): прежняя проверка
    `os.environ.get(...)` принимала ЛЮБУЮ непустую строку, включая «0» —
    оператор, выключающий обход нулём, на деле его включал."""
    return (os.environ.get(_UNISOLATED_OK) or "").strip() == "1"
_SENSITIVE_FOR_AGENT = ("webapp/config/claude_api_key",
                        "webapp/config/users.db",
                        "run-webapp.cmd")


def _acl_lets_ordinary_users_write(path) -> bool:
    """Правит ли файл любой локально аутентифицированный пользователь."""
    code, out = _run(["icacls", str(path)], cwd=str(REPO_ROOT), timeout_s=30)
    if code != 0:
        return False                        # нет файла — нечего охранять
    risky = ("BUILTIN\\Users", "Пользователи", "Authenticated Users",
             "Прошедшие проверку")
    for line in (out or "").splitlines():
        if any(who in line for who in risky) and \
                any(right in line for right in ("(F)", "(M)", "(W)")):
            return True
    return False


def _acl_lets_agent_write(path) -> bool:
    """Правит ли файл ЗАЯВЛЕННАЯ запись агента — ЯВНОЙ выдачей.

    Слепое пятно, найденное на живой площадке (2026-08-07): проверка
    выше ищет широкие ГРУППЫ, а скрипт укрепления когда-то выдал
    `cosmos-agent` полные права на всё дерево Test — после отключения
    наследования на users.db осталась явная строка `cosmos-agent:(F)`,
    и проверка молчала. Запрет (deny) при этом не срабатывает ложно:
    строки с DENY icacls печатает с пометкой, а право в них не
    разрешение."""
    from . import agent_broker
    declared = agent_broker.declared_account()
    if not declared:
        return False
    short = declared.split("\\")[-1].strip().casefold()
    if not short:
        return False
    code, out = _run(["icacls", str(path)], cwd=str(REPO_ROOT), timeout_s=30)
    if code != 0:
        return False
    for line in (out or "").splitlines():
        low = line.casefold()
        if short not in low:
            continue
        if "(deny)" in low or "(n)" in low:
            continue                        # запрет — не разрешение
        if any(right in line for right in ("(F)", "(M)", "(W)")):
            return True
    return False


def current_process_account() -> str:
    """Учётная запись, под которой РЕАЛЬНО работает этот процесс.

    USERDOMAIN/USERNAME — соглашения Windows; на POSIX их нет, и без
    запасного источника функция возвращала пустоту, отчего страж
    «объявленная запись совпадает с фактической» там был молча мёртв
    (вскрыто прогоном на Linux CI 2026-08-16)."""
    domain = os.environ.get("USERDOMAIN") or ""
    user = os.environ.get("USERNAME") or ""
    if not user:
        import getpass
        try:
            user = getpass.getuser()     # LOGNAME/USER/LNAME, затем pwd
        except (OSError, KeyError):
            user = ""                    # UID вне /etc/passwd — честная пустота
    return (f"{domain}\\{user}" if domain and user else user).strip()


def _same_account(declared: str, actual: str) -> bool:
    """Совпадают ли объявленная и фактическая записи. Сравнение по
    короткому имени: объявить могут и `АГЕНТ`, и `ДОМЕН\\АГЕНТ`."""
    def short(v: str) -> str:
        return (v or "").split("\\")[-1].split("@")[0].strip().casefold()
    return bool(short(declared)) and short(declared) == short(actual)


def isolation_problems() -> list[str]:
    """Что мешает считать запуск агента изолированным. Пустой список —
    можно запускать.

    ГЛАВНОЕ (повторный аудит 2026-07-26): проверка обязана смотреть на
    ФАКТИЧЕСКОГО владельца процесса, а не на наличие переменной. Прежняя
    версия проверяла непустоту `COSMOS_AGENT_ACCOUNT` и возвращала «проблем
    нет», тогда как кандидат запускался обычным `subprocess.Popen` из
    веб-процесса и наследовал его учётную запись. Настройка объявлена —
    граница не построена; отчёт «изоляция в порядке» был ложным.

    Пока запуск под отдельной записью не реализован, эта проверка обязана
    сообщать проблему ВСЕГДА — так честнее, чем зелёный свет."""
    problems: list[str] = []
    for rel in _SENSITIVE_FOR_AGENT:
        p = REPO_ROOT / rel
        if p.exists() and _acl_lets_ordinary_users_write(p):
            problems.append(
                f"{rel}: файл может изменить любой локально "
                f"аутентифицированный пользователь")
        elif p.exists() and _acl_lets_agent_write(p):
            problems.append(
                f"{rel}: файл может изменить запись агента (явная выдача "
                f"в ACL) — уберите её: icacls <файл> /remove:g "
                f"<запись агента>")
    # Посредник (пятый аудит, пакет C2) — единственный способ сделать
    # объявление границей: работу выполняет задача планировщика,
    # зарегистрированная от имени отдельной записи, и владелец РЕАЛЬНО
    # запущенного процесса сверяется после старта. Готов посредник —
    # претензии к учётной записи снимаются; не готов — он сам называет
    # причину, и она честнее прежнего «не реализовано».
    from . import agent_broker
    broker = agent_broker.problems()
    if broker:
        declared = agent_broker.declared_account()
        actual = current_process_account()
        if not declared:
            problems.append(
                "не объявлена отдельная учётная запись агента "
                "(COSMOS_AGENT_ACCOUNT): агент работает под той же учётной "
                "записью, что и приложение, и видит Prod, Stage и секреты")
            broker = [b for b in broker
                      if "COSMOS_AGENT_ACCOUNT" not in b]
        elif _same_account(declared, actual):
            problems.append(
                f"объявлена учётная запись агента «{declared}», но процесс "
                f"работает под «{actual}» — это одна и та же запись")
        problems.extend(f"посредник запуска: {b}" for b in broker)
    return problems


def _is_forbidden_path(path: str) -> bool:
    """Попадает ли путь под запрет.

    Три правила, и третье добавлено пятым аудитом. Запись со слешем на
    конце — каталог; запись с путём — ровно этот файл; запись БЕЗ слешей
    (`conftest.py`, `sitecustomize.py`) — файл с таким именем В ЛЮБОМ
    каталоге. Иначе запрет обходится переносом: `webapp/conftest.py`
    исполняется pytest-ом так же, как корневой, а `sitecustomize.py`
    подхватывается из любого места на пути импорта."""
    p = path.replace("\\", "/").strip().lstrip("./")
    name = p.rsplit("/", 1)[-1]
    for f in _DIFF_FORBIDDEN:
        if f.endswith("/"):
            if p == f.rstrip("/") or p.startswith(f):
                return True
        elif "/" in f:
            if p == f:
                return True
        elif name == f:
            return True
    return False


def _forbidden_changes(repo, base_head: str, head: str) -> list[str]:
    """Пути из запретного списка, которых коснулся кандидат."""
    code, out = _run(["git", *_git_safe_flags(), "diff", "--name-only", f"{base_head}..{head}"],
                     cwd=repo, timeout_s=60)
    if code != 0:
        # Не смогли прочитать дифф — считаем это поводом остановиться,
        # а не поводом пропустить проверку.
        return ["<не удалось прочитать состав изменений>"]
    touched = [ln.strip().replace("\\", "/")
               for ln in (out or "").splitlines() if ln.strip()]
    return sorted({p for p in touched if _is_forbidden_path(p)})


def _worktree_hazards(repo) -> list[str]:
    """НЕзакоммиченные перехватчики исполнения в рабочем дереве.

    Дифф base..HEAD видит только коммиты; неотслеживаемый conftest.py
    исполняется pytest'ом раньше любого теста под учётной записью
    приложения-подписанта (внешний аудит R7, P0-06а). Контур Test законно
    несёт неотслеживаемые runtime-файлы (run-webapp.cmd, метрики), поэтому
    судится не «есть ли мусор», а «есть ли мусор, который ИСПОЛНЯЕТСЯ
    проверкой»: запретный список + любой изменённый отслеживаемый файл.
    """
    # --untracked-files=all: без него git схлопывает каталог в одну
    # строку «dir/», и вложенный исполняемый файл невидим (внешний аудит
    # R8, P0-04б).
    code, out = _run(["git", *_git_safe_flags(), "status", "--porcelain",
                      "--untracked-files=all"], cwd=repo, timeout_s=60)
    if code != 0:
        return ["<не удалось прочитать состояние рабочего дерева>"]
    # .git/info/exclude прячет файл от git status, не убирая его с диска
    # (R8, P0-04в): непустой exclude — сам по себе опасность.
    excl = Path(repo) / ".git" / "info" / "exclude"
    try:
        lines = [ln.strip() for ln in
                 excl.read_text(encoding="utf-8",
                                errors="replace").splitlines()]
        if any(ln and not ln.startswith("#") for ln in lines):
            return [".git/info/exclude содержит правила — скрытие файлов "
                    "от проверки"]
    except OSError:
        pass
    bad: set[str] = set()
    for ln in (out or "").splitlines():
        if len(ln) < 4:
            continue
        st, path = ln[:2], ln[3:].strip().replace("\\", "/")
        if st == "??":
            name = path.rsplit("/", 1)[-1]
            hazard = (_is_forbidden_path(path) or path.endswith(".pth")
                      # Неотслеживаемый ТЕСТ исполняется pytest'ом под
                      # подписантом (R8, P0-04а): кандидат обязан
                      # коммитить тесты, а не подкладывать их.
                      or name.startswith("test_") and name.endswith(".py")
                      or name.endswith("_test.py")
                      or (path.startswith("tests/")
                          and path.endswith(".py"))
                      or path.endswith("/"))
            if hazard:
                bad.add(path)
        else:
            # изменённый/переименованный ОТСЛЕЖИВАЕМЫЙ файл: дерево не
            # соответствует ревизии, которую подпишет свидетельство
            bad.add(f"{st.strip()} {path}")
    bad.update(_repo_config_hazards(repo))
    return sorted(bad)


# Ключи конфигурации репозитория, позволяющие кандидату либо исполнить
# произвольную команду под записью подписанта, либо скрыть изменение от
# проверки. Гашение флагами закрывает НАШИ вызовы; здесь проверяется сам
# ИСТОЧНИК — потому что тем же репозиторием пользуются и другие
# инструменты, а «механизм есть» не равно «непреодолим».
_HAZARD_CONFIG_KEYS = ("core.hookspath", "core.fsmonitor",
                       "core.excludesfile", "core.attributesfile")
_HAZARD_CONFIG_PREFIXES = ("filter.", "diff.")


def _repo_config_hazards(repo) -> list[str]:
    """Опасности, лежащие в САМОМ репозитории кандидата.

    Три обхода, воспроизведённые внешним аудитом R9, жили именно здесь:
    внешний файл исключений прятал неотслеживаемый тест от проверки
    состояния; драйверы фильтров и файловый монитор исполняют
    произвольную команду; биты `assume-unchanged` и `skip-worktree`
    заставляют git пропустить сравнение отслеживаемого файла, из-за чего
    изменённый тест исполняется, а состояние дерева выглядит чистым.
    """
    out: list[str] = []
    cfg = Path(repo) / ".git" / "config"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    section = ""
    for raw in text.splitlines():
        ln = raw.strip()
        if ln.startswith("[") and ln.endswith("]"):
            section = ln.strip("[]").strip().replace('"', "").replace(" ", ".")
            continue
        if "=" not in ln or ln.startswith("#") or ln.startswith(";"):
            continue
        key = f"{section}.{ln.split('=', 1)[0].strip()}".lower()
        if key in _HAZARD_CONFIG_KEYS or any(
                key.startswith(p) for p in _HAZARD_CONFIG_PREFIXES):
            out.append(f".git/config задаёт {key} — исполнение или сокрытие")
    # Биты индекса, скрывающие изменения отслеживаемых файлов.
    code, listing = _run(["git", *_git_safe_flags(), "ls-files", "-v"],
                         cwd=repo, timeout_s=60)
    if code == 0:
        for ln in (listing or "").splitlines():
            if ln[:1] in ("h", "s", "S"):      # assume-unchanged / skip-worktree
                out.append(f"индекс скрывает изменения: {ln.strip()}")
    hooks = Path(repo) / ".git"
    for d in ("hooks", "hooks-disabled"):
        p = hooks / d
        try:
            if p.is_dir() and any(x.is_file() and not x.name.endswith(".sample")
                                  for x in p.iterdir()):
                out.append(f".git/{d} содержит исполняемые перехватчики")
        except OSError:
            pass
    return out


def _child_env(env_extra: dict | None = None,
               scrub_prefixes: tuple[str, ...] = ()) -> dict:
    """Environment for a spawned child: a copy of the process environment
    with UTF-8 output forced, optionally minus every key whose name starts
    with one of `scrub_prefixes`, then `env_extra` applied last."""
    env = dict(os.environ)
    if scrub_prefixes:
        for key in list(env):
            if key.startswith(scrub_prefixes):
                del env[key]
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return env


def _drop_secrets(env: dict, deny: tuple[str, ...]) -> dict:
    """Убрать из окружения перечисленные ключи (P0-01)."""
    for key in deny:
        env.pop(key, None)
    return env


def _stream_proc(cmd: list[str], cwd: str | Path, timeout_s: float,
                 input_text: str | None, handle_line,
                 env_extra: dict | None = None,
                 scrub_prefixes: tuple[str, ...] = (),
                 env_deny: tuple[str, ...] = ()) -> int:
    """Streaming subprocess core: merged stdout+stderr, one handle_line
    call per non-empty line as it arrives. Deadline discipline (each point
    below was a live-reproduced hang before it was enforced):
    - the watchdog thread starts BEFORE anything that can block;
    - the stdin prompt is written by a helper thread (the Windows
      anonymous pipe holds only ~4KB — a stalled child that never drains
      stdin would block the caller's write forever);
    - kills go through _kill_tree (a plain kill of a .cmd trampoline
      leaves the node grandchild holding the pipes open).
    Returns the exit code; 124 when the deadline killed the tree."""
    env = _drop_secrets(_child_env(env_extra, scrub_prefixes),
                        env_deny)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdin=(subprocess.PIPE if input_text is not None
                   else subprocess.DEVNULL),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        try:
            handle_line(f"SPAWN FAILURE: {exc}")
        except Exception:                           # noqa: BLE001
            pass
        return 127

    _set_active(proc)                 # so an admin stop can kill this tree
    killed = {"v": False}

    def _watchdog() -> None:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if proc.poll() is not None:
                return
            time.sleep(2)
        if proc.poll() is None:
            killed["v"] = True
            _kill_tree(proc)

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    def _feed_stdin() -> None:
        try:
            proc.stdin.write(input_text)
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    if input_text is not None and proc.stdin is not None:
        threading.Thread(target=_feed_stdin, daemon=True).start()

    try:
        for line in proc.stdout:                    # blocks per line
            line = line.strip()
            if not line:
                continue
            try:
                handle_line(line)
            except Exception:                       # noqa: BLE001
                pass
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
        _clear_active(proc)
    if killed["v"]:
        return 124
    code = proc.returncode
    return code if code is not None else 1


def _run(cmd: list[str], cwd: str | Path, timeout_s: float,
         env_extra: dict | None = None,
         input_text: str | None = None,
         on_line=None,
         scrub_prefixes: tuple[str, ...] = ()) -> tuple[int, str]:
    """Single subprocess point (tests monkeypatch this). Merged output.
    With on_line, every output line is ALSO delivered as it arrives (the
    live progress feed for benchmarks).

    Prompts MUST go through input_text, never as an argv element: on
    Windows a .cmd trampoline (claude.cmd) is parsed by cmd.exe, which
    truncates the command line at the first newline — a multi-line argv
    prompt silently drops every following flag.

    Hand-rolled timeout instead of subprocess.run(timeout=...): on
    TimeoutExpired the stdlib kills only the direct child and then joins
    the pipe readers with NO deadline — a surviving node grandchild would
    block the caller for its whole lifetime."""
    if on_line is not None:
        lines: list[str] = []

        def _collect(line: str) -> None:
            lines.append(line)
            on_line(line)

        code = _stream_proc(cmd, cwd, timeout_s, input_text, _collect,
                            env_extra=env_extra, scrub_prefixes=scrub_prefixes)
        out = "\n".join(lines)
        if code == 124:
            out = f"TIMEOUT after {timeout_s}s: {' '.join(cmd)}\n" + out
        return code, out
    env = _child_env(env_extra, scrub_prefixes)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdin=(subprocess.PIPE if input_text is not None
                   else subprocess.DEVNULL),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return 127, f"SPAWN FAILURE: {exc}"
    _set_active(proc)                 # so an admin stop can kill this tree
    try:
        out, _ = proc.communicate(input=input_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=30)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            out = ""
        return 124, (f"TIMEOUT after {timeout_s}s: {' '.join(cmd)}\n"
                     + (out or "")[-2000:])
    finally:
        _clear_active(proc)
    return proc.returncode, out or ""


def _stream_event_to_activity(evt: dict) -> None:
    """Translate one Claude Code stream-json event into a live-feed item.
    Only the meaningful, non-sensitive parts are surfaced (search queries,
    fetched URLs, short reasoning snippets)."""
    etype = evt.get("type")
    if etype == "assistant":
        content = (evt.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name") or ""
                inp = block.get("input") or {}
                if name == "WebSearch":
                    _activity_add("search", str(inp.get("query") or ""))
                elif name == "WebFetch":
                    _activity_add("fetch", str(inp.get("url") or ""))
                else:
                    _activity_add("tool", name)
            elif btype == "text":
                text = (block.get("text") or "").strip()
                if text:
                    _activity_add("thinking", text)
    elif etype == "result":
        # final text handled by the caller; note completion here
        _activity_add("note", "агент завершил исследование")


def _run_streaming(cmd: list[str], cwd: str | Path, timeout_s: float,
                   input_text: str | None = None,
                   env_deny: tuple[str, ...] = ()
                   ) -> tuple[int, str, str]:
    """Run the CLI in stream-json mode, feeding the live activity store as
    events arrive. Returns (exit_code, final_result_text, diag_tail) where
    diag_tail holds the last raw lines that were NOT valid stream-json —
    empty on a healthy run, the CLI's plain-text/error output otherwise.
    Deadline and stdin discipline live in _stream_proc."""
    result = {"text": ""}
    raw_tail: deque[str] = deque(maxlen=3)

    def _handle(line: str) -> None:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            raw_tail.append(line[:200])
            return
        if not isinstance(evt, dict):
            raw_tail.append(line[:200])
            return
        if evt.get("type") == "result":
            result["text"] = str(evt.get("result") or "")
        _stream_event_to_activity(evt)

    code = _stream_proc(cmd, cwd, timeout_s, input_text, _handle,
                        env_deny=env_deny)
    return code, result["text"], " | ".join(raw_tail)


def _notify_finding(row: dict) -> None:
    try:
        from . import telegram_bot
        telegram_bot.notify_evolution_finding(row)
    except Exception as exc:                       # noqa: BLE001
        _log(f"WARNING: finding notification failed: {exc}")


def _notify_report(row: dict, phase: str, text: str) -> None:
    try:
        from . import telegram_bot
        telegram_bot.notify_evolution_report(row, phase, text)
    except Exception as exc:                       # noqa: BLE001
        _log(f"WARNING: report notification failed: {exc}")


# ---------------------------------------------------------------- scheduler

def _should_scan(now: datetime, last_scan_day: str | None) -> bool:
    """Pure daily-trigger rule: fire in the HH:30..HH:59 window of the
    configured hour, at most once per calendar day."""
    if now.hour != scan_hour() or now.minute < 30:
        return False
    return last_scan_day != now.date().isoformat()


def _read_last_scan_day() -> str | None:
    try:
        return LAST_SCAN_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_last_scan_day(day: date) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SCAN_PATH.write_text(day.isoformat(), encoding="utf-8")


def last_scan_at() -> str | None:
    """Момент окончания последнего скана (UTC ISO), или None, если контур
    ещё ни разу не отработал в этой установке. Публичный: его читает
    сводка контуров."""
    try:
        return LAST_SCAN_AT_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_last_scan_at() -> None:
    """Не даём сбою отметки сорвать сам скан: находки уже записаны, и
    потерянная метка времени — меньшее зло, чем исключение из finally."""
    from . import users
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SCAN_AT_PATH.write_text(users._now_iso(), encoding="utf-8")
    except OSError as exc:
        _log(f"WARNING: last-scan timestamp not written: {exc}")


def _recover_interrupted() -> None:
    """Phases run as threads of THIS process: a server restart mid-phase
    leaves the finding stuck in a running status forever (testing/staging/
    deploying), with the pipeline lock gone. At boot, mark such rows as
    failed so the admin can simply re-approve them."""
    from . import evolution
    # Both the in-flight statuses AND the approved-but-not-yet-started ones:
    # approve_action sets approved_for_* and THEN spawns the phase thread,
    # which only flips to a running status once it wins _pipeline_lock. A
    # restart in that window (a long phase can hold the lock for hours)
    # would otherwise strand the finding in approved_for_* with no UI
    # buttons — permanently dead. Fail it so the admin can re-approve.
    stuck_to_failed = {
        evolution.STATUS_APPROVED_FOR_TEST: evolution.STATUS_TEST_FAILED,
        evolution.STATUS_TESTING: evolution.STATUS_TEST_FAILED,
        evolution.STATUS_APPROVED_FOR_STAGE: evolution.STATUS_STAGE_FAILED,
        evolution.STATUS_STAGING: evolution.STATUS_STAGE_FAILED,
        evolution.STATUS_APPROVED_FOR_PROD: evolution.STATUS_DEPLOY_FAILED,
        evolution.STATUS_DEPLOYING: evolution.STATUS_DEPLOY_FAILED,
    }
    for row in evolution.list_all():
        failed = stuck_to_failed.get(row["status"])
        if failed is None:
            continue
        _log(f"improvement {row['id']}: {row['status']} interrupted by "
             f"a restart — marked {failed}")
        evolution.set_status(
            row["id"], failed,
            error="фаза прервана перезапуском сервера — запустите её "
                  "повторно")


def start_scheduler_thread() -> threading.Thread | None:
    """Nightly-scan scheduler; no-op unless CREW_EVOLUTION_ENABLED=1."""
    global _scheduler_started
    if not enabled():
        print("EVOLUTION: disabled (CREW_EVOLUTION_ENABLED != 1) — "
              "scheduler not started", flush=True)
        return None
    with _lock:
        if _scheduler_started:
            return None
        _scheduler_started = True
    try:
        _recover_interrupted()
    except Exception as exc:                        # noqa: BLE001
        _log(f"WARNING: interrupted-phase recovery failed: {exc}")
    thread = threading.Thread(target=_scheduler_loop, daemon=True,
                              name="cosmos-evolution-scheduler")
    thread.start()
    _log(f"scheduler started (scan at {scan_hour():02d}:30 local)")
    return thread


def _scheduler_loop() -> None:
    while True:
        time.sleep(60)
        try:
            if _should_scan(datetime.now(), _read_last_scan_day()):
                _write_last_scan_day(date.today())
                _log("nightly scan window reached — starting run_scan()")
                run_scan()
        except Exception as exc:                   # noqa: BLE001
            _log(f"ERROR: scheduler iteration failed: {exc}")


# ---------------------------------------------------------- manual trigger

_scan_thread: threading.Thread | None = None


def _scan_safely() -> None:
    try:
        run_scan()
    except Exception as exc:                        # noqa: BLE001
        _log(f"ERROR: manual scan failed: {exc}")


def scan_running() -> bool:
    with _lock:
        return _scan_thread is not None and _scan_thread.is_alive()


def pipeline_busy() -> bool:
    """True when a phase (or a scan) currently holds _pipeline_lock. Probed
    non-blockingly; a False result may go stale immediately, which is
    harmless — the worst case is the scan waits for the lock, and it resets
    the feed only after acquiring it (never over a live phase feed)."""
    if _pipeline_lock.acquire(blocking=False):
        _pipeline_lock.release()
        return False
    return True


def trigger_scan() -> bool:
    """Start a research scan off-schedule in a background daemon thread
    (the «Начать R&D» button). Returns True if a new scan was launched,
    False if evolution is disabled, a scan is already running, or a
    pipeline phase is active. Refusing while a phase runs keeps the two
    writers off the single shared activity feed at once (the scan would
    otherwise reset the phase's live feed)."""
    global _scan_thread
    if not enabled():
        return False
    with _lock:
        if _scan_thread is not None and _scan_thread.is_alive():
            return False
        if pipeline_busy():
            return False
        _scan_thread = threading.Thread(
            target=_scan_safely, daemon=True, name="cosmos-evolution-scan")
        _scan_thread.start()
    _log("manual R&D scan triggered")
    return True


_approve_lock = threading.Lock()


def approve_action(imp_id: int, action: str, decided_by: str,
                   acknowledged_unisolated: bool = False,
                   countersign: str | None = None) -> dict:
    """One pipeline decision from the web UI (mirrors the Telegram
    buttons): t/s/p approve the next phase and launch it off-thread,
    x rejects. The transition gate is evolution.ACTION_VALID_FROM — the
    same table the Telegram handler enforces, so a stale press can never
    drive a second concurrent phase over the shared working tree. The
    check-then-set runs under _approve_lock: unlike the single-threaded
    Telegram poller, the web endpoint can deliver a double-click as two
    truly concurrent requests, and both would pass the gate.
    Запуск фазы Test — это запуск агента внедрения. Если среда не
    изолирована, решение НЕ принимается за администратора: действие
    возвращается с `error="isolation_unconfirmed"` и перечнем препятствий,
    интерфейс показывает предупреждение, и повторное нажатие приходит уже
    с `acknowledged_unisolated=True`. Состояние при этом не меняется —
    отказ в подтверждении не должен оставлять находку в подвешенном
    статусе.

    Returns {"ok": True, "status": <new>} or
    {"ok": False, "error": "not_found"|"invalid_status"|
     "isolation_unconfirmed", ...}."""
    from . import evolution
    with _approve_lock:
        try:
            row = evolution.get(imp_id)
        except evolution.ImprovementNotFoundError:
            return {"ok": False, "error": "not_found"}
        current = row["status"]
        if action not in evolution.ACTION_VALID_FROM \
                or current not in evolution.ACTION_VALID_FROM[action]:
            return {"ok": False, "error": "invalid_status",
                    "current": current}
        if action == "x":
            evolution.set_status(imp_id, evolution.STATUS_REJECTED,
                                 decided_by=decided_by)
            _log(f"improvement {imp_id}: rejected by {decided_by}")
            return {"ok": True, "status": evolution.STATUS_REJECTED}
        # Фаза Test — это запуск агента внедрения. Неизолированная среда
        # не отменяет решение, а требует его: показываем препятствия и
        # ждём осознанного подтверждения. Статус не трогаем.
        if action == "t" and not acknowledged_unisolated:
            problems = isolation_problems()
            if problems and not _unisolated_allowed_by_env():
                return {"ok": False, "error": "isolation_unconfirmed",
                        "problems": problems}
        # Контрподпись человека проверяется ДО смены статуса (аудит R10,
        # P0-04): прежде рабочего пути «токен → фаза» не существовало —
        # интерфейс не принимал токен, статус переключался, и фаза молча
        # умирала на гейте. Теперь токен обязателен на самом действии, а
        # гейт внутри фазы остаётся второй линией.
        if action in ("s", "p"):
            phase = {"s": "stage", "p": "prod"}[action]
            try:
                _assert_promotion_countersigned(imp_id, phase, countersign)
            except CountersignatureRequired as exc:
                return {"ok": False, "error": "countersign_required",
                        "message": str(exc)}
        approved, target, name = {
            "t": (evolution.STATUS_APPROVED_FOR_TEST, run_test_phase,
                  "cosmos-evo-test"),
            "s": (evolution.STATUS_APPROVED_FOR_STAGE, run_stage_phase,
                  "cosmos-evo-stage"),
            "p": (evolution.STATUS_APPROVED_FOR_PROD, deploy_prod,
                  "cosmos-evo-prod"),
        }[action]
        evolution.set_status(imp_id, approved, decided_by=decided_by)
    _log(f"improvement {imp_id}: {approved} by {decided_by}")
    kwargs = ({"acknowledged_unisolated": True}
              if action == "t" and acknowledged_unisolated else {})
    if action in ("s", "p"):
        kwargs["countersign"] = countersign
    if acknowledged_unisolated and action == "t":
        _log(f"improvement {imp_id}: запуск в неизолированной среде "
             f"ПОДТВЕРЖДЁН {decided_by}")
    threading.Thread(target=target, args=(imp_id,), kwargs=kwargs,
                     daemon=True, name=name).start()
    return {"ok": True, "status": approved}


# --------------------------------------------------------------------- scan

_SCAN_PROMPT = """\
Ты — ночной агент-исследователь системы COSMOS (открытый конвейер
оптимизации экипажей: генерация колонок на Python, точный выбор
маршрутов OR-Tools CP-SAT, ростеринг Timefold; задачи airline crew
pairing / rostering).

Задача: найди в интернете НОВЫЕ (примерно за последние 10 дней)
публикации, препринты, библиотеки с открытым кодом, релизы и
алгоритмические приёмы, которые могли бы улучшить наш конвейер.
Темы: column generation, branch-and-price, pricing/shortest path with
resource constraints, dual stabilization, set covering/partitioning at
scale, CP-SAT / HiGHS / SCIP advances, Timefold improvements, crew
pairing/rostering optimization.

УЖЕ ИЗВЕСТНЫЕ находки (пропускай их и их дубликаты):
{known}

Для каждой ДЕЙСТВИТЕЛЬНО новой и перспективной находки подготовь:
title (короткое название), source_url (прямая ссылка),
summary (2-3 предложения: что это), integration_plan (конкретные шаги
внедрения в НАШ стек), expected_gain (какую метрику и насколько может
улучшить: целевая функция этапа 1, зазор, покрытие, время, качество
ростера).

Финальный ответ — ТОЛЬКО ограждённый блок ```json с массивом объектов
{{"title", "source_url", "summary", "integration_plan", "expected_gain"}}.
Если ничего нового нет — пустой массив [].
"""


def _extract_json_array(text: str):
    """The agent's final text -> the LAST fenced json array (tolerates
    prose around it; a bare JSON array also accepted)."""
    fences = re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidates = fences[::-1] + [text.strip()]
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


_MAX_FINDINGS_PER_SCAN = 20
_MAX_FIELD_CHARS = 2000


def _clip_str(value: object) -> str | None:
    """Coerce an agent-supplied field to a bounded string or None. Guards
    against adversarial web content: non-strings become None (not a crash),
    lengths are capped."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:_MAX_FIELD_CHARS] if value else None


def run_scan() -> list[dict]:
    """Nightly research scan. EXTERNAL contour: the CLI runs inside the
    Test install and touches public information only. Streams a live
    activity feed (search queries, fetched URLs, reasoning) so the UI can
    show what the agent is doing."""
    from . import evolution
    _assert_external_contour()
    try:
        with _pipeline_lock:
            # Reset the feed ONLY after winning the lock: a phase may be
            # mid-run streaming into the same shared store, and an early
            # reset would wipe its events and desync the running flag.
            _activity_reset()
            _activity_add("start", "запуск исследования")
            known = evolution.known_findings()
            known_lines = "\n".join(
                f"- {k['title']} ({k['source_url'] or 'без ссылки'})"
                for k in known) or "- (пока ничего)"
            prompt = _SCAN_PROMPT.format(known=known_lines)
            _log(f"scan: starting (known findings: {len(known)})")
            _activity_add("note",
                          f"известных находок в базе: {len(known)}")
            code, result_text, diag = _run_streaming(
                [claude_cli(), "-p",
                 "--output-format", "stream-json", "--verbose",
                 "--allowedTools", "WebSearch WebFetch"]
                + settings.cli_model_args("coding"),
                cwd=test_dir(), timeout_s=SCAN_TIMEOUT_S,
                input_text=prompt)
        if code == 124:
            _log("ERROR: scan CLI hit the deadline and was killed")
            _activity_add("error",
                          "тайм-аут: агент остановлен по дедлайну")
            return []
        if code != 0:
            _log(f"ERROR: scan CLI exited {code}; tail: {diag}")
            _activity_add("error", f"агент завершился с кодом {code}"
                          + (f": {diag[-160:]}" if diag else ""))
            return []
        items = _extract_json_array(result_text or "")
        if items is None:
            tail = (diag or result_text or "")[-160:]
            _log(f"ERROR: scan produced no parsable JSON array; "
                 f"tail: {tail}")
            _activity_add("error", "не удалось разобрать ответ агента"
                          + (f" (вывод: {tail})" if tail else ""))
            return []
        # Наблюдение СОСТОЯЛОСЬ: агент отработал и вернул разобранный
        # результат — даже пустой. Отметка ставится ИМЕННО здесь, а не в
        # finally: скан, упавший по тайм-ауту, коду возврата или неразбор-
        # чивому ответу, ничего не наблюдал, и обновлять ему свежесть
        # значило бы показывать намертво сломанный контур бодрым.
        _write_last_scan_at()
        created_rows = []
        # Cap the batch: adversarial fetched content must not drive
        # thousands of DB inserts and Telegram messages.
        for item in items[:_MAX_FINDINGS_PER_SCAN]:
            if not isinstance(item, dict):
                continue
            title = _clip_str(item.get("title"))
            summary = _clip_str(item.get("summary"))
            if not title or not summary:
                continue
            try:
                created, row = evolution.add_finding(
                    title=title, summary=summary,
                    source_url=_clip_str(item.get("source_url")),
                    integration_plan=_clip_str(item.get("integration_plan")),
                    expected_gain=_clip_str(item.get("expected_gain")))
            except (ValueError, evolution.UsersError) as exc:
                _log(f"scan: skipped malformed finding: {exc}")
                continue
            if created:
                created_rows.append(row)
                _activity_add("finding", title)
                _notify_finding(row)
        _log(f"scan: done — {len(items)} reported, "
             f"{len(created_rows)} new")
        _activity_add("done", f"новых находок: {len(created_rows)}")
        return created_rows
    except Exception as exc:                        # noqa: BLE001
        _log(f"ERROR: run_scan failed: {exc}")
        _activity_add("error", f"внутренняя ошибка: {exc}")
        return []
    finally:
        _activity_finish()


# ------------------------------------------------------------- test phase

_IMPLEMENT_PROMPT = """\
Ты работаешь в ИЗОЛИРОВАННОЙ тестовой копии COSMOS (конвейер
оптимизации экипажей). Это песочница: пользовательских данных здесь
нет, ломать production невозможно.

Внедри следующее улучшение:

Название: {title}
Суть: {summary}
Источник: {source_url}
План интеграции: {plan}
Ожидаемый эффект: {gain}

Требования:
1. Минимальный точечный диф; не переписывай архитектуру.
2. Прогони юнит-тесты (.venv/Scripts/python.exe -m pytest -q) и добейся
   зелёного результата.
3. СНАЧАЛА КОММИТ ГОТОВОГО. Как только интеграция работает (пусть через
   фолбэк на прежнее поведение) и тесты зелёные — сделай git-коммит
   СРАЗУ, не откладывая его до конца экспериментов. Коммит с честным
   фолбэком ценнее несостоявшегося идеала.
4. Сессия ОДНОРАЗОВАЯ: у тебя нет «потом». Конец твоего хода — конец
   процесса; фоновые ожидания, мониторы и будильники не сработают —
   уведомления придут в процесс, которого уже не будет. Длинный
   эксперимент (свип, обучение модели), не влезающий в этот заход, в
   фазу не помещается: закоммить интеграцию вместе со скриптом сбора
   данных и напиши в финальном ответе, что осталось сделать.
5. Если внедрение невозможно или бессмысленно — НЕ делай коммит и начни
   финальный ответ строкой "ABORT: <причина>".
6. Третьего исхода нет: у работы ровно два законных конца — коммит либо
   "ABORT: <причина>". Завершение без того и другого считается сбоем
   фазы, и твой финальный ответ попадёт администратору как объяснение.
"""


def _hooks_void() -> str:
    """Пустой каталог ВНЕ любого дерева кандидата — под перехватчики git.

    Прежде путь перехватчиков указывался относительным
    `.git/hooks-disabled`, а он разрешается от корня рабочего дерева, то
    есть ВНУТРИ каталога, куда пишет кандидат. Каталога не существовало —
    и кандидату достаточно было создать его самому, чтобы получить
    исполнение своего `post-checkout` на каждом укреплённом вызове git
    (внешний аудит R9, P0-04). Абсолютный путь во временный каталог
    процесса-подписанта кандидату недоступен.
    """
    global _HOOKS_VOID
    if _HOOKS_VOID is None:
        import tempfile
        d = Path(tempfile.gettempdir()) / "cosmos-git-hooks-void"
        d.mkdir(parents=True, exist_ok=True)
        _HOOKS_VOID = str(d)
    return _HOOKS_VOID


_HOOKS_VOID: str | None = None


def _git_safe_flags() -> list[str]:
    """Единый набор флагов, гасящих механизмы git, способные исполнить
    код или скрыть изменение в дереве кандидата.

    Набор именно ЕДИНЫЙ и применяется на всех вызовах: прежде гасился
    только механизм перехватчиков, а соседние — файловый монитор,
    драйверы фильтров, внешний файл исключений и файл атрибутов —
    оставались доступны кандидату через его же `.git/config`.
    """
    return [
        "-c", "core.hooksPath=" + _hooks_void(),
        "-c", "core.fsmonitor=",
        "-c", "core.excludesFile=",
        "-c", "core.attributesFile=",
        "-c", "protocol.ext.allow=never",
    ]


def _git(repo: Path, *args: str, timeout_s: float = 300) -> tuple[int, str]:
    """ЕДИНСТВЕННАЯ точка вызова git на контуре самоулучшения.

    Перепись по исходным текстам (`tests/test_candidate_boundary.py`)
    следит, чтобы прямых вызовов мимо этой функции не появлялось: прежде
    их было два — в модуле свидетельств и среди литеральных вызовов
    самого контура, — и утверждение «хуки погашены на всех вызовах» было
    буквально неверным.
    """
    return _run(["git", *_git_safe_flags(), *args],
                cwd=repo, timeout_s=timeout_s)


def _head(repo: Path) -> str:
    _code, out = _git(repo, "rev-parse", "HEAD")
    return out.strip()


# Неотслеживаемые файлы среды, БЕЗ которых материализованное дерево считает
# по другим правилам. Их отсутствие — не косметика: настройки авиакомпании
# не под контролем версий, и дерево, собранное из ревизии, молча уходит на
# встроенные умолчания (проверено: переменная CREW_RULES_CONFIG
# выставляется дочернему процессу только при наличии файла). Для замера это
# означает расчёт по ЧУЖИМ правилам — прямая угроза числам заказчика.
_RUNTIME_CARRY = ("webapp/config/airline_default.json",)


class MaterializeError(RuntimeError):
    """Не удалось получить дерево ревизии без кандидатского `.git`."""


class CountersignatureRequired(RuntimeError):
    """Продвижение требует подтверждения человека, а его нет."""


COUNTERSIGN_ENV = "COSMOS_PROMOTION_SECRET"


def countersign_expected(imp_id: int, head_sha: str, phase: str) -> str:
    """Ожидаемое подтверждение продвижения для КОНКРЕТНОЙ правки и среза.

    Подпись привязана к тройке «правка, срез, фаза»: подтверждение,
    выданное для одного среза, не подходит другому, поэтому «однажды
    разрешили» не превращается в постоянный доступ.

    Секрет держит человек и задаёт переменной окружения; в репозитории и
    в дереве кандидата его нет. Это НЕ делает гейт независимым в полном
    смысле — для этого нужен отдельный держатель ключа вне машины
    (пакет C). Но автономное продвижение становится невозможным: код
    ничего не подписывает сам.
    """
    import hmac
    secret = os.environ.get(COUNTERSIGN_ENV) or ""
    if not secret:
        raise CountersignatureRequired(
            f"переменная {COUNTERSIGN_ENV} не задана — продвижение без "
            f"подтверждения человека невозможно")
    msg = f"{int(imp_id)}|{head_sha}|{phase}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def promotion_challenge(imp_id: int, phase: str) -> str:
    """Срез, который человек подтверждает для этой правки и фазы.

    Отдельная функция нужна оператору: он должен знать, ЧТО подписывает,
    и подписывать именно проверенный срез, а не «текущее состояние
    ветки» — иначе подтверждение молча распространится на следующий
    коммит.
    """
    from . import evolution
    row = evolution.get(imp_id) or {}
    return str(row.get("head_sha") or row.get("reviewed_sha")
               or row.get("commit") or "")


def _assert_promotion_countersigned(imp_id: int, phase: str,
                                    token: str | None) -> None:
    """Гейт продвижения: без подтверждения человека фаза не стартует.

    Срез берётся из записи правки, а не из текущего состояния дерева:
    иначе подтверждение, выданное для проверенного среза, молча
    распространилось бы на любой следующий коммит той же ветки.
    """
    if not token:
        # Отсутствие токена — первый и стабильный отказ: вычисление среза
        # ниже может не удаться по своим причинам, и они не должны
        # заслонять главное — «нужна контрподпись человека».
        raise CountersignatureRequired(
            f"фаза {phase!r} правки {imp_id} требует подтверждения "
            f"человека: продвижение не выполняется автоматически")
    head = promotion_challenge(imp_id, phase)
    verify_countersignature(imp_id, head, phase, token)
    _log(f"improvement {imp_id}: подтверждение человека для фазы "
         f"{phase} принято")


def verify_countersignature(imp_id: int, head_sha: str, phase: str,
                            token: str | None) -> None:
    """Проверить подтверждение; отказать громко при любом расхождении."""
    import hmac
    if not token:
        raise CountersignatureRequired(
            f"фаза {phase!r} правки {imp_id} требует подтверждения "
            f"человека: продвижение не выполняется автоматически")
    if not hmac.compare_digest(countersign_expected(imp_id, head_sha, phase),
                               str(token).strip()):
        raise CountersignatureRequired(
            f"подтверждение для правки {imp_id} не совпало со срезом "
            f"{head_sha[:12]} и фазой {phase!r}")


def materialize(repo: Path, sha: str, dest: Path) -> dict:
    """Разложить РОВНО ревизию `sha` в `dest` без каталога `.git`.

    Смысл — убрать саму возможность исполнения кандидатского кода через
    его же метаданные системы контроля версий: перехватчики, драйверы
    фильтров, файловый монитор и биты индекса живут в `.git`, а здесь его
    физически нет. Список запрещённых путей при этом перестаёт быть
    единственным рубежом (внешний аудит R9, P0-04).

    Неотслеживаемые файлы среды переносятся ЯВНО и пересчитываются в
    отчёте: молчаливый перенос был бы такой же подменой, как молчаливое
    умолчание.
    """
    import zipfile

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    bundle = dest.parent / (dest.name + ".zip")
    code, out = _git(repo, "archive", "--format=zip", "-o", str(bundle), sha)
    if code != 0 or not bundle.exists():
        raise MaterializeError(
            f"git archive для {sha[:12]} не удался: {out[:200]}")
    try:
        with zipfile.ZipFile(bundle) as z:
            z.extractall(dest)
    finally:
        bundle.unlink(missing_ok=True)
    if (dest / ".git").exists():
        raise MaterializeError(
            "в материализованном дереве оказался каталог .git — "
            "материализация не выполнила своего единственного назначения")
    carried, missing = [], []
    for rel in _RUNTIME_CARRY:
        src_p = Path(repo) / rel
        if src_p.exists():
            dst_p = dest / rel
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            dst_p.write_bytes(src_p.read_bytes())
            carried.append(rel)
        else:
            missing.append(rel)
    return {"dest": str(dest), "carried_runtime": carried,
            "missing_runtime": missing, "has_git": False}


def _fail(imp_id: int, status: str, message: str) -> None:
    from . import evolution
    _log(f"improvement {imp_id}: {status} — {message}")
    _activity_add("error", message[:200])
    _activity_finish()
    row = evolution.set_status(imp_id, status, error=message[:2000])
    _notify_report(
        row, "final",
        f"❌ COSMOS Evolution — «{row['title']}»: {status}\n\n{message[:1500]}")


def _instance_summary(out_path: Path, label: str) -> str | None:
    """Short per-instance result line for the live feed, read from the
    metrics document the bench rewrites after EVERY instance."""
    try:
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        inst = data["instances"][label]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    s1 = inst.get("stage1") or {}
    s2 = inst.get("stage2") or {}
    if "error" in s1:
        return f"{label}: этап 1 — ошибка"
    parts = [f"{label}: этап 1 —"]
    obj = s1.get("ip_objective")
    parts.append(f"целевая {obj}" if obj is not None else "готов")
    gap = s1.get("gap_vs_lp_pct")
    if gap is not None:
        parts.append(f"(зазор {gap}%)")
    if s1.get("wall_s") is not None:
        parts.append(f"за {int(s1['wall_s'])} с")
    if "error" in s2:
        parts.append("· этап 2 — ошибка")
    elif s2:
        parts.append(f"· этап 2 — {s2.get('result_status') or 'готов'}")
        if s2.get("stage2_score") is not None:
            parts.append(f"счёт {s2['stage2_score']}")
    return " ".join(str(p) for p in parts)


def _inst_ru(label: str) -> str:
    m = re.match(r"inst(\d+)", label)
    return f"Инстанс {m.group(1)}" if m else label


def _grp_ru(n) -> str:                        # space-grouped thousands (nbsp)
    return f"{int(round(n)):,}".replace(",", " ")


def _pct(base, cand) -> float | None:
    """Signed percent change base -> cand, or None if incomparable."""
    if base in (None, 0) or cand is None:
        return None
    return (cand - base) / abs(base) * 100.0


def _translate_progress(label: str, text: str):
    """Turn one raw solver progress line (forwarded by the bench as
    '[instN] progress: <line>') into (russian_detail, data), or None to
    drop it. `data` is a structured payload the web UI localizes into any
    of the 5 languages; the russian string is the Telegram/fallback text.
    Dual-level wording: a plain-language phrase a newcomer understands, with
    the exact optimization term/number kept in parentheses for a specialist.
    """
    t = text.strip()
    inst = _inst_ru(label)

    m = re.search(r"it\s+(\d+):\s*LP\s+([\d.]+).*?\+\s*(\d+)\s+cols"
                  r".*?pool\s+([\d,]+)", t)
    if m:
        it, lp, cols, pool = m.groups()
        pool_n = int(pool.replace(",", ""))
        lp_r = round(float(lp))
        return (
            f"{inst} · Этап 1 — подбор смен экипажа: рассмотрено "
            f"{_grp_ru(pool_n)} вариантов (генерация колонок, итер. {it}; "
            f"нижняя граница стоимости {_grp_ru(lp_r)})",
            {"t": "cg_iter", "inst": label, "it": int(it),
             "pool": pool_n, "cols": int(cols), "lp": lp_r})
    m = re.search(r"CP-SAT:\s*(\d+)\s+selected\s*\(([^)]+)\)"
                  r".*?obj=([\-\d.]+)", t)
    if m:
        n, st, obj = m.groups()
        obj_r = round(float(obj))
        return (
            f"{inst} · Этап 1 — выбран набор из {n} смен ({st}); "
            f"минимальная стоимость {_grp_ru(obj_r)} (целочисленный отбор)",
            {"t": "cg_select", "inst": label, "n": int(n),
             "status": st, "obj": obj_r})
    if t.startswith("CG done"):
        return (f"{inst} · Этап 1 — перебор вариантов завершён "
                f"(генерация колонок)", {"t": "cg_done", "inst": label})
    if "Construction Heuristic" in t and "ended" in t:
        m = re.search(r"best score \(([^)]+)\)", t)
        sc = m.group(1) if m else None
        return (f"{inst} · Этап 2 — построен первый вариант расписания"
                + (f" (эвристика, счёт {sc})" if sc else " (эвристика)"),
                {"t": "s2_ch", "inst": label, "score": sc})
    if "Local Search phase" in t and "ended" in t:
        m = re.search(r"best score \(([^)]+)\)", t)
        sc = m.group(1) if m else None
        return (f"{inst} · Этап 2 — расписание улучшено локальным поиском"
                + (f" (счёт {sc})" if sc else ""),
                {"t": "s2_ls", "inst": label, "score": sc})
    m = re.search(r"stage2=([0-9\-]+hard/[0-9\-]+soft)", t)
    if m:
        return (f"{inst} · Этап 2 — текущий счёт расписания {m.group(1)}",
                {"t": "s2_score", "inst": label, "score": m.group(1)})
    m = re.search(r"pref_hits=(\d+)/(\d+)", t)
    if m:
        return (f"{inst} · Этап 2 — учтено предпочтений экипажа "
                f"{m.group(1)} из {m.group(2)}",
                {"t": "s2_pref", "inst": label,
                 "hit": int(m.group(1)), "total": int(m.group(2))})
    return None


def _instance_verdict(cand_path: Path, base_path: Path, label: str):
    """Per-instance completion summary WITH a comparison to the baseline:
    solution quality (stage1 ip_objective, lower is better) and wall time.
    Returns (russian_detail, data) — `data.cls` is better/worse/equal/na so
    the UI can pin and colour it. Quality dominates the verdict; time only
    tips an otherwise-equal instance when it moves more than 1% (noise)."""
    try:
        cand = json.loads(Path(cand_path).read_text(
            encoding="utf-8"))["instances"][label]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    try:
        base = json.loads(Path(base_path).read_text(
            encoding="utf-8"))["instances"].get(label) or {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        base = {}
    cs1 = cand.get("stage1") or {}
    bs1 = base.get("stage1") or {}
    if "error" in cs1:
        return (f"{_inst_ru(label)} — этап 1 не удался (ошибка расчёта)",
                {"t": "inst_summary", "inst": label, "cls": "error"})
    c_obj, b_obj = cs1.get("ip_objective"), bs1.get("ip_objective")
    c_wall, b_wall = cs1.get("wall_s"), bs1.get("wall_s")
    q_cls, q_pct = "na", None
    if c_obj is not None and b_obj is not None:
        if c_obj == b_obj:
            q_cls = "equal"
        else:
            q_pct = _pct(b_obj, c_obj)          # negative = cheaper = better
            q_cls = "better" if c_obj < b_obj else "worse"
    t_pct = _pct(b_wall, c_wall)
    if q_cls in ("better", "worse"):
        cls = q_cls
    elif q_cls == "equal":
        cls = ("better" if t_pct < 0 else "worse") \
            if (t_pct is not None and abs(t_pct) >= 1.0) else "equal"
    else:
        cls = "na"
    s2 = cand.get("stage2") or {}
    data = {"t": "inst_summary", "inst": label, "cls": cls, "q_cls": q_cls,
            "q_pct": round(q_pct, 1) if q_pct is not None else None,
            "t_pct": round(t_pct, 1) if t_pct is not None else None,
            "obj": c_obj, "wall": c_wall, "base_wall": b_wall,
            "s2": s2.get("result_status")
            or ("error" if "error" in s2 else None)}
    if q_cls == "equal":
        q_ru = "качество как у текущей версии"
    elif q_cls == "better":
        q_ru = f"качество лучше (стоимость {q_pct:+.1f}%)"
    elif q_cls == "worse":
        q_ru = f"качество хуже (стоимость {q_pct:+.1f}%)"
    else:
        q_ru = "качество: не с чем сравнить"
    if t_pct is None:
        t_ru = ""
    elif abs(t_pct) < 1.0:
        t_ru = f"; время {t_pct:+.1f}% (в пределах шума)"
    else:
        t_ru = (f"; время {t_pct:+.1f}% "
                f"({'быстрее' if t_pct < 0 else 'медленнее'})")
    tech = []
    if c_obj is not None:
        tech.append(f"цел. {_grp_ru(c_obj)}")
    if c_wall is not None:
        tech.append(f"{c_wall:.0f} с"
                    + (f" (было {b_wall:.0f})" if b_wall else ""))
    tech_ru = f" ({'; '.join(tech)})" if tech else ""
    return (f"{_inst_ru(label)} — {q_ru}{t_ru}{tech_ru}", data)


def _bench_on_line(out_path: Path, base_path: Path | None = None):
    """Progress relay for `_evolution_bench.py run`: each '[instN] …'
    stdout line becomes a feed event. 'progress:' carries humanized
    within-instance solver progress (kind 'bench', scrolls). On 'done:',
    when `base_path` is given (the CANDIDATE run), the instance is compared
    to the baseline and emitted as kind 'summary' — the pinned per-instance
    verdict the UI keeps visible; without a baseline (the BASELINE run) it
    is a plain 'bench' line."""
    def on_line(line: str) -> None:
        m = re.match(
            r"^\[(inst\d+)\] (stage1:|stage2:|done:|progress:)\s*(.*)$",
            line)
        if not m:
            return
        label, kind, rest = m.groups()
        if kind == "done:":
            if base_path is not None:
                res = _instance_verdict(out_path, base_path, label)
                if res:
                    _activity_add("summary", res[0], res[1])
                    return
            _activity_add("bench",
                          _instance_summary(out_path, label)
                          or f"{label}: {rest}"[:200])
        elif kind == "progress:":
            res = _translate_progress(label, rest)
            if res:
                _activity_add("bench", res[0][:200], res[1])
        else:
            _activity_add("bench", f"{label}: {kind} {rest}"[:200])
    return on_line


def ensure_baseline(profile: str) -> Path | None:
    """Benchmark of UNMODIFIED origin/main, cached in the prod config dir
    and keyed by the EXACT origin/main SHA it was built from (fetched
    fresh). Candidate branches are cut from the same origin/main, so both
    sides share a base and the compare attributes the delta to the change
    alone. Runs in the Test install (public seed data only). Callers hold
    _pipeline_lock (this mutates the Test working tree). Returns the
    baseline path, or None on failure."""
    baseline = CONFIG_DIR / f"evolution-baseline-{profile}.json"
    repo = test_dir()
    code, out = _git(repo, "fetch", "origin", "main", timeout_s=600)
    if code != 0:
        _log(f"ERROR: baseline git fetch failed: {out[-300:]}")
        return None
    _code, main_sha = _git(repo, "rev-parse", "origin/main")
    main_sha = main_sha.strip()
    if baseline.is_file():
        try:
            cached = json.loads(baseline.read_text(encoding="utf-8"))
            if cached.get("commit") == main_sha:
                _activity_add("note", "базовый замер актуален (из кэша)")
                return baseline
        except (OSError, json.JSONDecodeError):
            pass
    _log(f"baseline({profile}): refreshing for origin/main {main_sha[:12]}")
    _activity_add("note", f"базовый замер ({profile}) для "
                          f"origin/main {main_sha[:10]}")
    # -f: the Test install is a disposable sandbox (the agent always
    # COMMITS its work; runtime artifacts regenerate). Force past any dirty
    # working tree so a stray uncommitted file can never stall the pipeline
    # AND so the baseline is genuinely of UNMODIFIED origin/main (a dirty
    # tracked file — e.g. webapp/config/airline_default.json — would
    # otherwise silently skew it).
    code, out = _git(repo, "checkout", "-f", "-B", "baseline-run",
                     "origin/main", timeout_s=600)
    if code != 0:
        _log(f"ERROR: baseline git checkout failed: {out[-300:]}")
        return None
    out_path = repo / f"evolution-metrics-baseline-{profile}.json"
    timeout = (QUICK_BENCH_TIMEOUT_S if profile == "quick"
               else FULL_BENCH_TIMEOUT_S)
    code, out = _run(
        [_venv_python(repo), "_evolution_bench.py", "run",
         "--profile", profile, "--out", str(out_path)],
        cwd=repo, timeout_s=timeout, on_line=_bench_on_line(out_path))
    if code != 0 or not out_path.is_file():
        _log(f"ERROR: baseline bench failed ({code}): {out[-300:]}")
        return None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    baseline.write_bytes(out_path.read_bytes())
    return baseline


def _compare(baseline: Path, candidate: Path, report_md: Path) -> dict | None:
    """Run the compare subcommand in the PROD venv (local computation
    only) and return the machine summary from report.json."""
    code, out = _run(
        [_venv_python(prod_dir()), "_evolution_bench.py", "compare",
         "--baseline", str(baseline), "--candidate", str(candidate),
         "--out", str(report_md)],
        cwd=prod_dir(), timeout_s=600)
    if code != 0:
        _log(f"ERROR: compare failed ({code}): {out[-300:]}")
        return None
    try:
        return json.loads(report_md.with_suffix(".json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"ERROR: compare summary unreadable: {exc}")
        return None


def _reviewed_commit_path(imp_id: int) -> Path:
    return CONFIG_DIR / f"evolution-reviewed-{imp_id}.txt"


_LLM_REVIEW_TIMEOUT_S = 240
_REC_LABELS = {"deploy": "продвинуть на Stage", "hold": "не продвигать",
               "reject": "отклонить"}


def _parse_llm_review(text: str) -> dict | None:
    """Pull the {recommendation, rationale} JSON object out of the CLI's
    reply (last valid one wins). Returns None if nothing usable."""
    for blob in reversed(re.findall(
            r"\{[^{}]*\"recommendation\"[^{}]*\}", text, re.DOTALL)):
        try:
            d = json.loads(blob)
        except json.JSONDecodeError:
            continue
        rec = str(d.get("recommendation", "")).lower().strip()
        if rec in ("deploy", "hold", "reject") and str(d.get("rationale") or "").strip():
            return {"recommendation": rec,
                    "rationale": str(d["rationale"]).strip()[:1200]}
    return None


def _fallback_review(summary: dict, stopped: bool) -> dict:
    """Metric-based recommendation used only when the LLM is unreachable.
    Deliberately conservative — leans to 'hold' unless there is a clean
    improvement with no regressions."""
    better = int(summary.get("better") or 0)
    worse = int(summary.get("worse") or 0)
    if worse == 0 and better >= 1:
        rec, why = "deploy", "Есть улучшения без регрессий."
    elif better == 0 and worse == 0:
        rec, why = ("hold", "Отличия в пределах шума — перформанс-нейтрально; "
                    "двигать без явной причины не стоит.")
    else:
        rec, why = ("hold", f"Смешанный результат (лучше {better}, хуже "
                    f"{worse}); выигрыш не подтверждён — нужна ручная оценка.")
    if stopped:
        why = "Прогон остановлен досрочно (частичные результаты). " + why
    return {"recommendation": rec, "source": "fallback",
            "rationale": why + " [LLM недоступна — эвристический вывод]"}


def _llm_deploy_review(summary: dict, report_text: str,
                       stopped: bool) -> dict:
    """Analytical deploy judgement (item 2): ask the external-contour LLM
    to weigh the WHOLE benchmark comparison and recommend deploy/hold/reject
    with a rationale — instead of the naive 'any single metric improved'
    rule. Input is PUBLIC benchmark data (GERAD/BTS seed), so the cloud CLI
    is contour-safe. Falls back to a metric heuristic on any CLI failure."""
    prompt = (
        "Ты — старший инженер по оптимизации. Оцени результаты бенчмарка "
        "изменения решателя расписаний экипажей (кандидат против текущей "
        "боевой версии) и реши, стоит ли продвигать изменение на предпрод "
        "(Stage).\n\n"
        "Правила оценки:\n"
        "- Качество решения (целевая стоимость) важнее времени счёта.\n"
        "- Различия во времени в пределах ~1% — это шум прогон-к-прогону, "
        "а не улучшение.\n"
        "- Регрессии учитывай серьёзно: одно крошечное улучшение среди "
        "нескольких ухудшений — не повод для деплоя.\n"
        "- Если изменение перформанс-нейтрально (качество то же, время в "
        "пределах шума) — рекомендуй 'hold'.\n"
        + ("- ВАЖНО: прогон ОСТАНОВЛЕН досрочно, результаты частичные (не все "
           "инстансы посчитаны) — учти это.\n" if stopped else "")
        + f"\nСводка классов метрик: лучше={summary.get('better')}, "
        f"хуже={summary.get('worse')}, без изменений={summary.get('equal')}, "
        f"вердикт правила={summary.get('verdict')!r}.\n\n"
        "Полный отчёт сравнения (инстанс × метрика):\n"
        + report_text[:4000] + "\n\n"
        "Ответь СТРОГО одним JSON-объектом, без текста вокруг:\n"
        '{"recommendation": "deploy"|"hold"|"reject", '
        '"rationale": "<2-4 предложения по-русски: почему такой вывод>"}\n')
    try:
        code, out = _run([claude_cli(), "-p"]
                         + settings.cli_model_args("coding"), cwd=test_dir(),
                         timeout_s=_LLM_REVIEW_TIMEOUT_S, input_text=prompt)
    except Exception as exc:                        # noqa: BLE001
        _log(f"LLM review call failed: {exc}")
        return _fallback_review(summary, stopped)
    rec = _parse_llm_review(out) if code == 0 else None
    if rec:
        rec["source"] = "llm"
        return rec
    _log(f"LLM review unparsable (code {code}); using fallback")
    return _fallback_review(summary, stopped)


_GRADE_LABELS = {"full": "Улучшение", "partial": "Частичное улучшение",
                 "none": "Нет улучшения"}


def _grade(summary: dict) -> str:
    """full = gains and no regressions; partial = both; none = no gains."""
    better = int(summary.get("better") or 0)
    worse = int(summary.get("worse") or 0)
    if better >= 1 and worse == 0:
        return "full"
    if better >= 1:
        return "partial"
    return "none"


def _review_ru(review: dict) -> str:
    src = " (быстрая оценка)" if review.get("source") == "fallback" else ""
    grade = review.get("grade")
    gline = f"— Итог: {_GRADE_LABELS.get(grade, grade)}\n" if grade else ""
    return (gline
            + f"— Рекомендация{src}: {_REC_LABELS.get(review['recommendation'], review['recommendation'])}\n"
            f"— Обоснование: {review['rationale']}")


def _finalize_test(imp_id: int, summary: dict, report_md: Path,
                   candidate: Path, stopped: bool) -> None:
    """Close the Test phase (normal completion OR early stop): store the
    candidate metrics, run the analytical LLM deploy review, set the
    metric-based status (advisory model — the status stays metric-based so
    both test_improved and test_no_gain keep the Stage button; the LLM
    recommendation is stored and shown, never gating), send the report and
    the final verdict event."""
    from . import evolution
    evolution.attach_metrics(
        imp_id, "test",
        json.loads(Path(candidate).read_text(encoding="utf-8")))
    _activity_add("note", "анализирую результаты (LLM)…")
    report_text = report_md.read_text(encoding="utf-8")
    review = _llm_deploy_review(summary, report_text, stopped)
    review["stopped"] = stopped
    # Honest three-way grade on the metric comparison: full improvement only
    # when NOTHING regressed and something improved; partial when there are
    # both gains and regressions; none otherwise. (Counts are per changed
    # metric — the same tally shown in the report.)
    review["grade"] = _grade(summary)
    review["metric_verdict"] = summary.get("verdict")
    evolution.attach_review(imp_id, review)
    row = evolution.set_status(
        imp_id,
        evolution.STATUS_TEST_IMPROVED if review["grade"] != "none"
        else evolution.STATUS_TEST_NO_GAIN)
    _notify_report(
        row, "test",
        ("⚠️ ОСТАНОВЛЕНО ДОСРОЧНО — частичные результаты\n\n" if stopped else "")
        + report_text + "\n\n" + _review_ru(review))
    rec = review["recommendation"]
    head = "Test остановлен (частично)" if stopped else "Test завершён"
    _activity_add(
        "done",
        f"{head}: {_GRADE_LABELS.get(review['grade'], review['grade'])} · "
        f"рекомендация: {_REC_LABELS.get(rec, rec)}",
        {"t": "verdict", "recommendation": rec, "grade": review["grade"],
         "rationale": review["rationale"], "stopped": stopped,
         "source": review.get("source"),
         "metric_verdict": summary.get("verdict")})
    _activity_finish()
    _log(f"improvement {imp_id}: test phase "
         f"{'stopped' if stopped else 'done'} "
         f"({summary.get('verdict')}, llm={rec}, src={review.get('source')})")


def _stop_finalize(imp_id: int, baseline: Path | None,
                   candidate: Path, report_md: Path) -> None:
    """Close an early-stopped Test run. If the candidate bench produced at
    least one completed instance AND a baseline exists, compare those and
    run the LLM review (marked stopped); otherwise mark stopped with no
    comparable results — the whole point of the stop button (item 1)."""
    from . import evolution
    _activity_add("note", "остановлено — собираю частичные результаты…")
    insts: dict = {}
    if Path(candidate).is_file():
        try:
            insts = (json.loads(Path(candidate).read_text(encoding="utf-8"))
                     .get("instances") or {})
        except (OSError, json.JSONDecodeError):
            insts = {}
    if insts and baseline is not None and Path(baseline).is_file():
        summary = _compare(baseline, candidate, report_md)
        if summary is not None:
            _finalize_test(imp_id, summary, report_md, candidate,
                           stopped=True)
            return
    row = evolution.set_status(imp_id, evolution.STATUS_TEST_NO_GAIN)
    review = {"recommendation": "hold", "source": "stopped-empty",
              "stopped": True, "grade": "none",
              "rationale": "Прогон остановлен до получения сравнимых "
              "результатов — оценивать нечего."}
    evolution.attach_review(imp_id, review)
    _notify_report(row, "test",
                   "⚠️ Test остановлен досрочно — результатов для сравнения нет.")
    _activity_add("done", "Test остановлен — результатов нет",
                  {"t": "verdict", "recommendation": "hold", "stopped": True,
                   "rationale": review["rationale"], "source": "stopped-empty"})
    _activity_finish()
    _log(f"improvement {imp_id}: test stopped (no comparable results)")


def run_test_phase(imp_id: int,
                   acknowledged_unisolated: bool = False) -> None:
    """EXTERNAL contour. Baseline refresh -> agent implements on a branch
    of the Test clone (autonomous, sandbox-only — approved) -> unit tests
    -> quick benchmark -> comparison -> admin report. Serialized by
    _pipeline_lock (shared Test working tree).

    `acknowledged_unisolated` — администратор увидел предупреждение о
    неизолированной среде и подтвердил запуск. Решение принимает человек,
    но оно должно быть ЯВНЫМ и попасть в журнал."""
    from . import evolution
    try:
        # Refuse to run the sandbox agent if Test overlaps an internal
        # contour — checked BEFORE taking the lock so a misconfiguration
        # fails fast and loud.
        _assert_external_contour()
        # Изоляция среды проверяется ДО запуска агента (шаг 0 плана
        # укрепления). Отказ здесь — последний рубеж: обычный путь через
        # интерфейс показывает предупреждение и спрашивает подтверждение,
        # и сюда приходит уже принятое человеком решение. Без него
        # (прямой вызов, сценарий) — не запускаем.
        problems = isolation_problems()
        if problems and not acknowledged_unisolated \
                and not _unisolated_allowed_by_env():
            _fail(imp_id, evolution.STATUS_TEST_FAILED,
                  "среда агента не изолирована: " + "; ".join(problems)
                  + ". Запуск подтверждают в интерфейсе (кнопка «Реализовать» "
                    "покажет предупреждение) либо укрепляют машину — "
                    "docs/ops/agent-isolation-RU.md.")
            return
        note = None
        if problems:
            who = ("подтверждено администратором" if acknowledged_unisolated
                   else f"разрешено переменной {_UNISOLATED_OK}")
            _log(f"EVOLUTION: агент запущен в НЕИЗОЛИРОВАННОЙ среде ({who}): "
                 + "; ".join(problems))
            # Пометку несём ВНУТРЬ: лента фазы сбрасывается уже за замком,
            # и добавленное здесь было бы стёрто.
            note = f"внимание: среда агента не изолирована — {who}"
        with _pipeline_lock:
            _run_test_phase_locked(imp_id, unisolated_note=note)
    except Exception as exc:                        # noqa: BLE001
        try:
            from . import evolution as _evo
            _fail(imp_id, _evo.STATUS_TEST_FAILED,
                  f"внутренняя ошибка фазы Test: {exc}")
        except Exception:                           # noqa: BLE001
            _log(f"ERROR: test phase double-fault for {imp_id}: {exc}")


def _broker_wait(hand: dict, timeout_s: float) -> tuple[int, str, str]:
    """Дождаться агента, запущенного посредником: done.txt несёт код
    завершения, out.txt — вывод. Остановка фазы снимает процесс агента."""
    import time as _t
    from . import agent_broker
    root = Path(hand["handoff"])
    done = root / "done.txt"
    end = _t.monotonic() + timeout_s
    while _t.monotonic() < end:
        if _cancel.is_set():
            agent_broker.kill(hand["pid"])
            break
        if done.exists():
            break
        _t.sleep(2.0)
    else:
        agent_broker.kill(hand["pid"])
        return 124, "", "агент не уложился в отведённое время"
    out = ""
    try:
        out = (root / "out.txt").read_text(encoding="utf-8",
                                           errors="replace")
    except OSError:
        pass
    try:
        code = int(done.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        code = 1
    return code, out, ""


def _run_test_phase_locked(imp_id: int,
                           unisolated_note: str | None = None) -> None:
    from . import evolution
    row = evolution.get(imp_id)
    branch = f"improvement-{imp_id}"
    repo = test_dir()
    _cancel.clear()                   # fresh stop flag for this phase
    candidate = repo / f"evolution-metrics-{imp_id}-quick.json"
    report_md = CONFIG_DIR / f"evolution-report-{imp_id}-test.md"
    evolution.set_status(imp_id, evolution.STATUS_TESTING, branch=branch)
    _activity_reset()
    _activity_add("start", f"фаза Test №{imp_id}: {row['title'][:80]}")
    if unisolated_note:
        _activity_add("note", unisolated_note)

    # Baseline FIRST (needs a clean checkout of origin/main).
    baseline = ensure_baseline("quick")
    if _cancel.is_set():
        _stop_finalize(imp_id, baseline, candidate, report_md)
        return
    if baseline is None:
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              "не удалось построить базовый замер (baseline quick)")
        return

    for step in (("fetch", "origin", "main"),
                 ("checkout", "-f", "-B", branch, "origin/main")):
        code, out = _git(repo, *step, timeout_s=600)
        if code != 0:
            _fail(imp_id, evolution.STATUS_TEST_FAILED,
                  f"git {step[0]}: {out[-300:]}")
            return
    base_head = _head(repo)
    if _cancel.is_set():
        _stop_finalize(imp_id, baseline, candidate, report_md)
        return

    prompt = _IMPLEMENT_PROMPT.format(
        title=row["title"], summary=row["summary"],
        source_url=row.get("source_url") or "—",
        plan=row.get("integration_plan") or "—",
        gain=row.get("expected_gain") or "—")
    _activity_add("note", f"агент внедряет изменение (ветка {branch})")
    # Autonomy contract: gate disabled ONLY here, inside cosmos-test.
    # stream-json so the agent's steps land in the live feed; the prompt
    # goes via stdin — a multi-line argv element dies in cmd.exe.
    #
    # Инструменты ОЖИДАНИЯ отключены механически (находка 27, дважды):
    # сессия одноразовая, но Monitor/ScheduleWakeup агенту ДОСТУПНЫ — и
    # «запущу свип и подожду уведомлений» выглядит для него законным
    # планом. Оба раза он делал рабочую интеграцию, уходил ждать, сессия
    # умирала, коммита не было. Промпт-запрета недостаточно: доступность
    # инструмента убедительнее инструкции. Недоступность — честнее.
    # Маршрут запуска (внешний аудит R6, P0-07): готовый посредник —
    # ЕДИНСТВЕННЫЙ путь; прямой subprocess остаётся только неизолированным
    # фолбэком, который уже подтверждён выше вслух. Модуль-посредник без
    # фактического вызова был бумажной границей: isolation_problems()
    # зеленел от одной правильной регистрации задачи.
    from . import agent_broker
    isolation_mode = "unisolated-subprocess"
    if agent_broker.available():
        isolation_mode = "broker"
        _activity_add("note", "запуск через посредника: отдельная "
                              "учётная запись агента, владелец процесса "
                              "сверяется")
        try:
            hand = agent_broker.launch(prompt, run_id=f"imp-{imp_id}")
            code, result_text, diag = _broker_wait(
                hand, timeout_s=IMPLEMENT_TIMEOUT_S)
        except agent_broker.BrokerError as exc:
            _fail(imp_id, evolution.STATUS_TEST_FAILED,
                  f"посредник агента: {exc}")
            return
    else:
        code, result_text, diag = _run_streaming(
            [claude_cli(), "-p", "--output-format", "stream-json",
             "--verbose", "--dangerously-skip-permissions",
             "--disallowedTools", "Monitor ScheduleWakeup"]
            + settings.cli_model_args("coding"),
            cwd=repo, timeout_s=IMPLEMENT_TIMEOUT_S, input_text=prompt,
            env_deny=_AGENT_ENV_DENY)
    if _cancel.is_set():
        _stop_finalize(imp_id, baseline, candidate, report_md)
        return
    if code != 0:
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              f"агент внедрения завершился с кодом {code}: "
              f"{(result_text or diag)[-400:]}")
        return
    abort = re.search(r"^ABORT:.*$", result_text, re.MULTILINE)
    if abort:
        _fail(imp_id, evolution.STATUS_TEST_FAILED, abort.group(0))
        return
    if _head(repo) == base_head:
        # Хвост финального ответа агента обязан попасть в ошибку. Сбой
        # находки 27 (2026-07-23): агент отработал 45 минут, вышел с кодом
        # 0 без коммита и без «ABORT:» — и ЕДИНСТВЕННОЕ место, где он
        # объяснил почему, было выброшено. Админ видел «изменений нет» и
        # не мог отличить «уже реализовано» от «не смог» и от «забыл
        # закоммитить».
        tail = (result_text or diag or "").strip()[-400:]
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              "агент не сделал коммита (изменений нет)"
              + (f"; его финальный ответ: …{tail}" if tail else ""))
        return

    # Состав изменений проверяется ДО того, как что-либо из кандидата
    # будет исполнено (аудит 2026-07-26, P0-02): правка батареи тестов,
    # конвейера продвижения, доступа или зависимостей означает, что
    # кандидат сам определяет доказательство своей пригодности.
    forbidden = _forbidden_changes(repo, base_head, _head(repo))
    if forbidden:
        _activity_add("note", "запретные пути в изменении кандидата")
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              "кандидат изменил пути, определяющие его же проверку: "
              + ", ".join(forbidden)
              + ". Такое изменение проводится только человеком.")
        return
    # Чистота рабочего дерева (внешний аудит R7, P0-06а): дифф видит
    # только коммиты, а неотслеживаемый conftest.py исполнился бы
    # pytest'ом под учётной записью подписанта до первого теста.
    hazards = _worktree_hazards(repo)
    if hazards:
        _activity_add("note", "рабочее дерево кандидата не чисто")
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              "рабочее дерево несёт незакоммиченные перехватчики "
              "исполнения или правки отслеживаемых файлов: "
              + ", ".join(hazards[:8])
              + ". Проверка судит только закоммиченное состояние.")
        return

    # Кандидат исполняется в МАТЕРИАЛИЗОВАННОЙ ревизии без каталога .git
    # (пакет C, аудит R10 P0-04): перехватчики, драйверы фильтров, биты
    # индекса недостижимы по построению, а не по списку запретов.
    # Доверенная батарея (сбор тестов, измерительный прибор, зависимости)
    # кладётся из АТТЕСТОВАННОЙ базовой ревизии — подменить то, чем его
    # меряют, кандидат не может. При готовом посреднике pytest и замер
    # идут под ОТДЕЛЬНОЙ учётной записью агента: код кандидата не
    # исполняется под подписантом.
    import shutil as _sh
    head_now = _head(repo)
    mat_dir = repo / "candidate-runs" / f"imp-{imp_id}"
    _sh.rmtree(mat_dir, ignore_errors=True)
    try:
        mat = materialize(repo, head_now, mat_dir)
        laid = _lay_trusted_harness(mat_dir, repo, base_head)
    except MaterializeError as exc:
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              f"материализация ревизии кандидата: {exc}")
        return
    if mat["missing_runtime"]:
        _activity_add("note", "не перенесены настройки среды: "
                              + ", ".join(mat["missing_runtime"]))
    _activity_add("note", "материализованная ревизия без .git"
                  + (f"; доверенная батарея: {', '.join(laid)}"
                     if laid else ""))
    runner_mode = ("broker-exec" if isolation_mode == "broker"
                   else "subprocess")
    try:
        _activity_add("note", "юнит-тесты кандидата"
                      + (" (под записью агента)"
                         if runner_mode == "broker-exec" else ""))
        py_argv = [_venv_python(repo), "-m", "pytest", "-q"]
        if runner_mode == "broker-exec":
            code, out = _broker_exec(py_argv, mat_dir,
                                     run_id=f"imp-{imp_id}-pytest",
                                     timeout_s=PYTEST_TIMEOUT_S)
        else:
            code, out = _run(py_argv, cwd=mat_dir,
                             timeout_s=PYTEST_TIMEOUT_S,
                             scrub_prefixes=_GATE_ENV_SCRUB)
        if _cancel.is_set():
            _stop_finalize(imp_id, baseline, candidate, report_md)
            return
        if code != 0:
            _fail(imp_id, evolution.STATUS_TEST_FAILED,
                  f"юнит-тесты красные:\n{out[-600:]}")
            return
        _activity_add("note",
                      "юнит-тесты зелёные — быстрая батарея кандидата")

        bench_argv = [_venv_python(repo), "_evolution_bench.py", "run",
                      "--profile", "quick", "--out", str(candidate)]
        if runner_mode == "broker-exec":
            code, out = _broker_exec(bench_argv, mat_dir,
                                     run_id=f"imp-{imp_id}-bench",
                                     timeout_s=QUICK_BENCH_TIMEOUT_S)
        else:
            code, out = _run(bench_argv, cwd=mat_dir,
                             timeout_s=QUICK_BENCH_TIMEOUT_S,
                             on_line=_bench_on_line(candidate, baseline))
        if _cancel.is_set():
            _stop_finalize(imp_id, baseline, candidate, report_md)
            return
        if code != 0 or not candidate.is_file():
            _fail(imp_id, evolution.STATUS_TEST_FAILED,
                  f"быстрая батарея не прошла ({code}): {out[-400:]}")
            return
    finally:
        # Одноразовая среда прогона убирается в любом исходе.
        _sh.rmtree(mat_dir, ignore_errors=True)

    summary = _compare(baseline, candidate, report_md)
    if summary is None:
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              "сравнение с базой не удалось")
        return

    # Свидетельство пройденной фазы (пятый аудит, пакет C1). До сих пор
    # Stage верил Test на слово: следом проверки была строка состояния в
    # базе, лежащей в досягаемости агента. Теперь Test подписывает РОВНО
    # то, что проверил, ключом вне дерева кандидата, а Stage
    # пересчитывает отпечаток у себя и сверяет.
    try:
        head = _head(repo)
        evidence.issue(imp_id, "test", base_sha=base_head,
                       candidate_sha=head,
                       diff_hash=evidence.diff_fingerprint(
                           repo, base_head, head),
                       checks={"forbidden_paths": "clean",
                               "unit_tests": "green",
                               "bench_profile": "quick",
                               # Способ запуска — часть свидетельства
                               # (внешний аудит R6, P0-07): прямой
                               # subprocess под записью приложения не
                               # образует границы доверия, и Stage обязан
                               # это видеть, а не узнавать из журналов.
                               "isolation": isolation_mode,
                               # Пакет C (аудит R10 P0-04): проверка шла в
                               # материализованной ревизии без .git; чем
                               # исполнялась батарея — тоже часть
                               # свидетельства.
                               "materialized": True,
                               "runner": runner_mode})
    except evidence.EvidenceError as exc:
        _fail(imp_id, evolution.STATUS_TEST_FAILED,
              f"не удалось выписать свидетельство проверки: {exc}")
        return
    _finalize_test(imp_id, summary, report_md, candidate, stopped=False)


# ------------------------------------------------------------ stage phase

def run_stage_phase(imp_id: int, countersign: str | None = None) -> None:
    """INTERNAL contour: local git merge of the already-written branch,
    full unit tests, full benchmark. NO AI involvement here. Serialized by
    _pipeline_lock. Records the EXACT reviewed commit so Prod deploys that
    same SHA, not whatever the branch tip later becomes.

    Требует подтверждения человека (`countersign`): автоматика не может
    выписать его себе сама, поэтому автономного продвижения на внутренний
    контур не существует (внешний аудит R9, P0-04, пакет C).
    """
    from . import evolution
    try:
        with _pipeline_lock:
            _assert_promotion_countersigned(imp_id, "stage", countersign)
            _run_stage_phase_locked(imp_id)
    except Exception as exc:                        # noqa: BLE001
        try:
            from . import evolution as _evo
            _fail(imp_id, _evo.STATUS_STAGE_FAILED,
                  f"внутренняя ошибка фазы Stage: {exc}")
        except Exception:                           # noqa: BLE001
            _log(f"ERROR: stage phase double-fault for {imp_id}: {exc}")


def _run_stage_phase_locked(imp_id: int) -> None:
    from . import evolution
    row = evolution.get(imp_id)
    branch = row.get("branch") or f"improvement-{imp_id}"
    repo = stage_dir()
    evolution.set_status(imp_id, evolution.STATUS_STAGING)
    _activity_reset()
    _activity_add("start", f"фаза Stage №{imp_id}: {row['title'][:80]}")

    # Свидетельство фазы Test проверяется на подпись ДО полного замера:
    # он идёт часами, а подделанная или отсутствующая запись отменяет
    # работу в любом случае. Ревизия и состав изменений сверяются ниже —
    # для них нужна выкаченная ветка.
    try:
        evidence.verify(imp_id, "test")
    except evidence.EvidenceError as exc:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"свидетельство фазы Test не принято: {exc}")
        return

    baseline = ensure_baseline("full")
    if baseline is None:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              "не удалось построить полный базовый замер")
        return

    for step in (("fetch", str(test_dir()), branch),
                 ("checkout", "-B", branch, "FETCH_HEAD")):
        code, out = _git(repo, *step, timeout_s=600)
        if code != 0:
            _fail(imp_id, evolution.STATUS_STAGE_FAILED,
                  f"git {step[0]}: {out[-300:]}")
            return
    # Pin the reviewed commit: Prod will deploy EXACTLY this SHA, even if
    # the branch is later rewritten by a Test re-run.
    reviewed = _head(repo)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _reviewed_commit_path(imp_id).write_text(reviewed, encoding="utf-8")

    # Stage не верит Test на слово (пятый аудит, пакет C1): свидетельство
    # проверено подписью, ревизия сверена с выкаченной, а состав изменений
    # ПЕРЕСЧИТАН здесь и сверен с подписанным. Плюс запретные пути
    # проверяются заново — Stage исполняет тот же код и обязан судить
    # сам, а не наследовать вердикт.
    try:
        attested = evidence.verify(imp_id, "test", candidate_sha=reviewed)
        # Неизолированный запуск не переходит в Stage (внешний аудит R6,
        # P0-07): кандидат работал под той же учётной записью, что читает
        # ключ подписи, — его свидетельство не образует границы доверия.
        iso = str((attested.get("checks") or {}).get("isolation") or "")
        if iso != "broker":
            raise evidence.EvidenceError(
                f"фаза Test выполнялась без изоляции ({iso or 'способ '
                'запуска не записан'}) — такое свидетельство в Stage "
                f"не принимается; настройте посредника агента "
                f"(tools/register-agent-task.ps1)")
        base_sha = str(attested.get("base_sha") or "")
        if not base_sha:
            raise evidence.EvidenceError(
                "в свидетельстве нет базовой ревизии")
        code, _o = _git(repo, "cat-file", "-e", base_sha + "^{commit}")
        if code != 0:
            raise evidence.EvidenceError(
                f"базовая ревизия {base_sha[:12]} из свидетельства "
                f"отсутствует в дереве Stage")
        here = evidence.diff_fingerprint(repo, base_sha, reviewed)
        if here != attested.get("diff_hash"):
            raise evidence.EvidenceError(
                "состав изменений на Stage не совпадает с проверенным на "
                "Test")
    except evidence.EvidenceError as exc:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"свидетельство фазы Test не принято: {exc}")
        return
    forbidden = _forbidden_changes(repo, base_sha, reviewed)
    if forbidden:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              "кандидат изменил пути, определяющие его же проверку: "
              + ", ".join(forbidden))
        return

    # Та же доверенная батарея, что и на Test: Stage выкачивает ветку
    # кандидата и без этого исполнял бы его же измерительный прибор.
    # База — АТТЕСТОВАННАЯ ревизия из свидетельства Test (аудит R10,
    # P1-04): имя origin/main разрешается внутри репозитория кандидата,
    # и кандидат, подменивший remote, подсунул бы себя же в роли
    # «доверенной» батареи. base_sha уже проверен выше на существование.
    restored = _restore_trusted_harness(repo, base_sha)
    if restored:
        _activity_add("note", "доверенная батарея: " + ", ".join(restored))

    _activity_add("note", f"полные юнит-тесты (ревизия {reviewed[:10]})")
    code, out = _run([_venv_python(repo), "-m", "pytest", "-q"],
                     cwd=repo, timeout_s=PYTEST_TIMEOUT_S,
                     scrub_prefixes=_GATE_ENV_SCRUB)
    if code != 0:
        _release_trusted_harness(repo)
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"полные юнит-тесты красные:\n{out[-600:]}")
        return
    _activity_add("note", "юнит-тесты зелёные — полная батарея кандидата")

    candidate = repo / f"evolution-metrics-{imp_id}-full.json"
    code, out = _run(
        [_venv_python(repo), "_evolution_bench.py", "run",
         "--profile", "full", "--out", str(candidate)],
        cwd=repo, timeout_s=FULL_BENCH_TIMEOUT_S,
        on_line=_bench_on_line(candidate, baseline))
    _release_trusted_harness(repo)
    if code != 0 or not candidate.is_file():
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"полная батарея не прошла ({code}): {out[-400:]}")
        return

    report_md = CONFIG_DIR / f"evolution-report-{imp_id}-stage.md"
    summary = _compare(baseline, candidate, report_md)
    if summary is None:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              "сравнение с базой не удалось")
        return
    # H16: a candidate that ERRORED on a stage where the baseline succeeded is
    # a fatal regression — refuse promotion regardless of the metric verdict
    # (previously the error was silently counted as n/a and one improved
    # metric elsewhere could still flip the verdict to "improved").
    cand_errors = int(summary.get("candidate_errors") or 0)
    if cand_errors > 0:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"кандидат упал на {cand_errors} этап(ах), где база прошла — "
              "фатальная регрессия, продвижение отменено")
        return
    # Аудит-2 P1-10: Stage проходит только чистый кандидат — ноль худших
    # метрик и ноль дыр в схеме (метрика есть у базы, но нет у кандидата).
    # Раньше no_gain с 4 худшими метриками и пустой кандидат ставили
    # STAGE_PASSED безусловно.
    worse = int(summary.get("worse") or 0)
    holes = int(summary.get("metric_holes") or 0)
    if worse > 0 or holes > 0:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"кандидат не чист: худших метрик {worse}, дыр схемы {holes} — "
              "Stage требует worse=0 и полную схему метрик")
        return
    try:
        evidence.issue(imp_id, "stage", base_sha=base_sha,
                       candidate_sha=reviewed, diff_hash=here,
                       checks={"forbidden_paths": "clean",
                               "unit_tests": "green",
                               "bench_profile": "full",
                               "worse_metrics": 0, "metric_holes": 0})
    except evidence.EvidenceError as exc:
        _fail(imp_id, evolution.STATUS_STAGE_FAILED,
              f"не удалось выписать свидетельство фазы Stage: {exc}")
        return
    evolution.attach_metrics(
        imp_id, "stage",
        json.loads(candidate.read_text(encoding="utf-8")))
    for hl in (summary.get("highlights") or [])[:10]:
        _activity_add("bench", str(hl)[:200])
    row = evolution.set_status(imp_id, evolution.STATUS_STAGE_PASSED)
    _notify_report(row, "stage", report_md.read_text(encoding="utf-8"))
    _activity_add("done",
                  f"Stage пройден ({summary.get('verdict')}) — "
                  f"кандидат готов к Prod")
    _activity_finish()
    _log(f"improvement {imp_id}: stage phase done "
         f"(reviewed {reviewed[:12]}, {summary.get('verdict')})")


# ------------------------------------------------------------ prod deploy

def _detached_self_restart() -> None:
    """Restart the prod webapp from OUTSIDE this process (we are it).
    Targets ONLY the prod uvicorn by its port so cosmos-stage's server
    (and any other python on the machine) is never touched."""
    prod = str(prod_dir())
    port = prod_port()
    script = (
        "Start-Sleep 5; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'uvicorn' -and "
        f"$_.CommandLine -match '--port {port}' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
        "Start-Sleep 2; "
        "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        "-Arguments @{ CommandLine = 'cmd /c " + prod +
        "\\run-webapp.cmd'; CurrentDirectory = '" + prod + "' }")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                     creationflags=flags, close_fds=True)


def deploy_prod(imp_id: int, countersign: str | None = None) -> None:
    """INTERNAL contour: merge the EXACT commit Stage reviewed into prod,
    run the unit tests, restart the service; hard rollback on red tests.
    Serialized by _pipeline_lock.

    Требует отдельного подтверждения человека — своего для боевого
    контура: подтверждение внутреннего контура сюда не подходит, потому
    что подпись привязана к фазе.
    """
    from . import evolution
    try:
        with _pipeline_lock:
            _assert_promotion_countersigned(imp_id, "prod", countersign)
            _deploy_prod_locked(imp_id)
    except Exception as exc:                        # noqa: BLE001
        try:
            from . import evolution as _evo
            _fail(imp_id, _evo.STATUS_DEPLOY_FAILED,
                  f"внутренняя ошибка деплоя: {exc}")
        except Exception:                           # noqa: BLE001
            _log(f"ERROR: deploy double-fault for {imp_id}: {exc}")


def _deploy_prod_locked(imp_id: int) -> None:
    from . import evolution
    row = evolution.get(imp_id)
    branch = row.get("branch") or f"improvement-{imp_id}"
    repo = prod_dir()
    # The commit Stage validated (pinned at stage_passed). Deploying
    # anything else would break the autonomy contract: prod would run code
    # the admin never saw in the Stage report.
    reviewed_path = _reviewed_commit_path(imp_id)
    try:
        reviewed = reviewed_path.read_text(encoding="utf-8").strip()
    except OSError:
        reviewed = ""
    if not reviewed:
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              "не найден зафиксированный на Stage коммит — деплой отменён")
        return

    evolution.set_status(imp_id, evolution.STATUS_DEPLOYING)
    _activity_reset()
    _activity_add("start", f"деплой в Prod №{imp_id}: {row['title'][:80]}")
    prev = _head(repo)

    # Fetch the branch (brings the reviewed object into prod), then merge
    # the EXACT reviewed SHA — not FETCH_HEAD, which may have moved.
    code, out = _git(repo, "fetch", str(test_dir()), branch, timeout_s=600)
    if code != 0:
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              f"git fetch: {out[-300:]}")
        return
    verify, _ = _git(repo, "cat-file", "-e", reviewed + "^{commit}")
    if verify != 0:
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              f"ревизия {reviewed[:12]} отсутствует после fetch — "
              f"ветка была переписана; деплой отменён")
        return

    # Свидетельство фазы Stage (пятый аудит, пакет C1) проверяется ДО
    # слияния: раскатывается ровно та ревизия, которую Stage подписал, а
    # состав её изменений пересчитан здесь. Файл с номером ревизии сам по
    # себе доказательством не был — он лежит рядом с состоянием, которое
    # агент может править.
    try:
        attested = evidence.verify(imp_id, "stage", candidate_sha=reviewed)
        base_sha = str(attested.get("base_sha") or "")
        code, _o = _git(repo, "cat-file", "-e", base_sha + "^{commit}")
        if code != 0:
            raise evidence.EvidenceError(
                f"базовая ревизия {base_sha[:12]} из свидетельства "
                f"отсутствует в дереве Prod")
        here = evidence.diff_fingerprint(repo, base_sha, reviewed)
        if here != attested.get("diff_hash"):
            raise evidence.EvidenceError(
                "состав изменений отличается от подписанного на Stage")
    except evidence.EvidenceError as exc:
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              f"свидетельство фазы Stage не принято: {exc}. Деплой "
              f"отменён до слияния.")
        return
    forbidden = _forbidden_changes(repo, base_sha, reviewed)
    if forbidden:
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              "кандидат изменил пути, определяющие его же проверку: "
              + ", ".join(forbidden))
        return
    code, out = _git(repo, "merge", "--no-edit", reviewed, timeout_s=600)
    if code != 0:
        _git(repo, "merge", "--abort")
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              f"конфликт слияния:\n{out[-500:]}")
        return

    _activity_add("note", f"ревизия {reviewed[:10]} слита — "
                          f"контрольные тесты на prod")
    code, out = _run([_venv_python(repo), "-m", "pytest", "-q"],
                     cwd=repo, timeout_s=PYTEST_TIMEOUT_S,
                     scrub_prefixes=_GATE_ENV_SCRUB)
    if code != 0:
        _git(repo, "reset", "--hard", prev)
        _fail(imp_id, evolution.STATUS_DEPLOY_FAILED,
              f"тесты на prod красные — ОТКАЧЕНО к {prev[:12]}:\n"
              f"{out[-500:]}")
        return

    row = evolution.set_status(imp_id, evolution.STATUS_DEPLOYED,
                               decided_by="telegram-admin")
    _notify_report(
        row, "final",
        f"🚀 COSMOS Evolution — «{row['title']}» задеплоена в Prod "
        f"(коммит {_head(repo)[:12]}). Сервис перезапускается.")
    _activity_add("done", f"задеплоено в Prod (коммит {_head(repo)[:10]}), "
                          f"сервис перезапускается")
    _activity_finish()
    _log(f"improvement {imp_id}: deployed, restarting service")
    _detached_self_restart()
