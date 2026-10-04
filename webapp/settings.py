"""Global application settings for the super admin (2026-07-18).

A single **super admin** (``SUPER_ADMIN_EMAIL`` — the FIRST address of the
``CREW_SUPER_ADMIN`` environment variable, comma-separated; no default in
code: unset/empty → ``None`` and nobody is the super admin) can set runtime
knobs from the
"Установки"/"Settings" UI tab: which Claude model each AI use case uses, the
Claude API key that powers cloud model calls, the default backend, the local
Ollama model/URL, and the LLM-import sample masking policy.

Two files under ``webapp/config/`` (both gitignored, per-deployment):

  * ``app_settings.json`` — non-secret knobs. Safe to return from the API.
  * ``claude_api_key``     — the raw Anthropic API key (a *usable* secret).
    NEVER returned by any endpoint; a GET reports only ``{configured, hint}``.

**Contour invariant.** The API key powers a CLOUD path. It only fires where
the data-contour already permits cloud (``llm_scope="any"`` / the external
Test contour). Internal-contour customer-data calls still route to the local
Ollama model regardless of what is configured here — this module only chooses
WHICH model a call *already allowed to use cloud* will use.

The module reads only the filesystem + environment; it MUST NOT import
``llm_backend`` (llm_backend imports this), so there is no import cycle.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"
SETTINGS_PATH = CONFIG_DIR / "app_settings.json"
API_KEY_PATH = CONFIG_DIR / "claude_api_key"

# Собственный платформенный флаг вместо чтения os.name в ветках ниже.
# Тесты эмулируют Windows подменой ИМЕННО этого флага: подмена
# глобального os.name протекала в диспетчеризацию классов pathlib —
# Path() на Linux начинал строить WindowsPath, и ротация ключа падала
# на несуществующем «обратнослэшном» имени файла.
_IS_WINDOWS = os.name == "nt"

# The super admin is identified by email, NOT a DB role (the users.db CHECK
# constraint only allows 'admin'|'user'). Значение — только из данных
# развёртывания: первый адрес CREW_SUPER_ADMIN (список через запятую — тот
# же носитель, из которого users.py сеет администраторов первого запуска;
# пишет установщик). Переменной нет/пуста → None: супер-функций нет ни у
# кого, места употребления отвечают отказом 403 «super admin only».


def _super_admin_from_env() -> str | None:
    entries = [p.strip().lower()
               for p in os.environ.get("CREW_SUPER_ADMIN", "").split(",")
               if p.strip()]
    return entries[0] if entries else None


SUPER_ADMIN_EMAIL = _super_admin_from_env()
# The token-mode operator holds the deployment bearer token = full trust, so
# it is treated as the super admin too (otherwise Settings would be
# unreachable in token/dev mode). Access mode (prod) restricts to the email.
TOKEN_OPERATOR_EMAIL = "operator@token.local"

# AI use cases the super admin can assign a Claude model to. Entries in
# USAGE_CLI are the agentic-CLI (coding) sites, where a chosen model is passed
# as a --model flag rather than through the completion API.
USAGES = ("coding", "text", "ask_ai", "advisor", "agents",
          "source_check")
USAGE_CLI = frozenset({"coding"})

# Sample-masking policy for the LLM import. The CLOUD backend ALWAYS masks
# regardless; these modes only tune the local side, so masking can never be
# weakened below the contour floor:
#   None     -> default: cloud masked, local per CREW_IMPORT_MASK_SAMPLES
#   "cloud"  -> cloud masked, local raw (overrides the env flag)
#   "always" -> mask both cloud and local
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_MASK_MODES = ("cloud", "always")
_BACKENDS = ("local", "cloud")

_lock = threading.RLock()
_cache: dict | None = None
_cache_mtime: float | None = None


# ------------------------------------------------------------------ identity

def is_super_admin(user: dict | None) -> bool:
    """True iff ``user`` is the single super admin. Email compare only — no
    role column is involved (that would break the users.db CHECK constraint).
    SUPER_ADMIN_EMAIL is None (CREW_SUPER_ADMIN не задана) → супер-админа
    нет: True остаётся только у оператора token-режима."""
    if not isinstance(user, dict):
        return False
    email = (user.get("email") or "").strip().lower()
    if not email:
        return False
    return email == TOKEN_OPERATOR_EMAIL or (
        SUPER_ADMIN_EMAIL is not None and email == SUPER_ADMIN_EMAIL)


# --------------------------------------------------------------- config store

def _defaults() -> dict:
    return {
        "models": {u: None for u in USAGES},
        "default_backend": None,      # None -> env/default
        "ollama_model": None,
        "ollama_url": None,
        "mask_samples": None,         # None | "off" | "cloud" | "always"
        "models_cache": None,         # {fetched_at, models:[{id,display_name,created_at}]}
    }


def _coerce(stored: dict) -> dict:
    """Merge a stored blob over the defaults, dropping anything malformed."""
    data = _defaults()
    if not isinstance(stored, dict):
        return data
    models = {u: None for u in USAGES}
    raw_models = stored.get("models")
    if isinstance(raw_models, dict):
        for u in USAGES:
            v = raw_models.get(u)
            models[u] = v if isinstance(v, str) and v.strip() else None
    data["models"] = models
    db = stored.get("default_backend")
    data["default_backend"] = db if db in _BACKENDS else None
    for key in ("ollama_model", "ollama_url"):
        v = stored.get(key)
        data[key] = v.strip() if isinstance(v, str) and v.strip() else None
    ms = stored.get("mask_samples")
    data["mask_samples"] = ms if ms in _MASK_MODES else None
    mc = stored.get("models_cache")
    if isinstance(mc, dict) and isinstance(mc.get("models"), list):
        data["models_cache"] = {
            "fetched_at": str(mc.get("fetched_at") or ""),
            "models": [m for m in mc["models"] if isinstance(m, dict)],
        }
    return data


def _load() -> dict:
    """Current settings, cached by file mtime (falls back to defaults)."""
    global _cache, _cache_mtime
    try:
        mtime = SETTINGS_PATH.stat().st_mtime
    except OSError:
        mtime = None
    with _lock:
        if _cache is not None and mtime == _cache_mtime:
            return json.loads(json.dumps(_cache))
        data = _defaults()
        if mtime is not None:
            try:
                data = _coerce(json.loads(SETTINGS_PATH.read_text("utf-8")))
            except (OSError, json.JSONDecodeError):
                data = _defaults()
        _cache = data
        _cache_mtime = mtime
        return json.loads(json.dumps(data))


def get_all() -> dict:
    """The full non-secret settings blob (safe to return from the API)."""
    return _load()


def _write(data: dict) -> None:
    global _cache, _cache_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(SETTINGS_PATH)
    with _lock:
        _cache = None
        _cache_mtime = None


def save(patch: dict) -> dict:
    """Validate + merge a partial update into ``app_settings.json`` (atomic).

    Accepted keys: ``models`` (dict usage->model-id|null), ``default_backend``,
    ``ollama_model``, ``ollama_url``, ``mask_samples``. Raises ``ValueError``
    on any invalid value (the endpoint maps it to 422). Unknown keys ignored.
    """
    with _lock:
        cur = _load()
        if "models" in patch:
            models = patch["models"]
            if not isinstance(models, dict):
                raise ValueError("models must be an object")
            for usage, val in models.items():
                if usage not in USAGES:
                    raise ValueError(f"unknown use case: {usage}")
                if val is None or (isinstance(val, str) and not val.strip()):
                    cur["models"][usage] = None
                elif isinstance(val, str) and _MODEL_ID_RE.match(val.strip()):
                    cur["models"][usage] = val.strip()
                else:
                    raise ValueError(f"invalid model id for {usage}: {val!r}")
        if "default_backend" in patch:
            db = patch["default_backend"]
            if db in (None, ""):
                cur["default_backend"] = None
            elif db in _BACKENDS:
                cur["default_backend"] = db
            else:
                raise ValueError(f"invalid default_backend: {db!r}")
        for key in ("ollama_model", "ollama_url"):
            if key in patch:
                v = patch[key]
                if v in (None, "") or (isinstance(v, str) and not v.strip()):
                    cur[key] = None
                elif isinstance(v, str):
                    cur[key] = (assert_local_model_url(v.strip())
                                if key == "ollama_url" else v.strip())
                else:
                    raise ValueError(f"invalid {key}: {v!r}")
        if "mask_samples" in patch:
            ms = patch["mask_samples"]
            if ms in (None, ""):
                cur["mask_samples"] = None
            elif ms in _MASK_MODES:
                cur["mask_samples"] = ms
            else:
                raise ValueError(f"invalid mask_samples: {ms!r}")
        _write(cur)
        return cur


def set_models_cache(models: list, fetched_at: str) -> None:
    """Store the last-fetched model catalog (non-secret; shown in the UI)."""
    with _lock:
        cur = _load()
        cur["models_cache"] = {"fetched_at": fetched_at,
                               "models": list(models)}
        _write(cur)


# ------------------------------------------------------------------ getters

def get_default_backend() -> str | None:
    b = _load().get("default_backend")
    return b if b in _BACKENDS else None


def get_ollama_model() -> str | None:
    v = _load().get("ollama_model")
    return v if isinstance(v, str) and v.strip() else None


def get_ollama_url() -> str | None:
    v = _load().get("ollama_url")
    return v if isinstance(v, str) and v.strip() else None


def get_mask_samples() -> str | None:
    v = _load().get("mask_samples")
    return v if v in _MASK_MODES else None


def _valid_model_id(v) -> str | None:
    """A stored model id, re-validated against _MODEL_ID_RE at READ time too
    (save() already gates writes; this guards against a hand-edited/corrupt
    app_settings.json ever surfacing a malformed value into a --model argv)."""
    return v.strip() if isinstance(v, str) and _MODEL_ID_RE.match(v.strip()) \
        else None


def model_for(usage: str | None, backend: str) -> str | None:
    """The Claude model id chosen for ``usage`` — relevant to the CLOUD
    backend only. Local calls always use the (global) Ollama model, so this
    returns None for the local backend (and for an unknown/None usage)."""
    if backend != "cloud" or not usage:
        return None
    return _valid_model_id((_load().get("models") or {}).get(usage))


#: Умолчания моделей агентного CLI по назначению, когда настройка площадки
#: пуста. «coding» (агент реализации запросов на изменение) — claude-opus-5:
#: слово владельца 09.09.2026 («Opus для реализации, Fable для суждения»);
#: до этого агент шёл с моделью по умолчанию CLI, то есть без названной
#: модели, что нарушало правило именованных моделей на каждой точке прогона.
#: Настройка площадки (`models.coding`) по-прежнему главнее умолчания.
DEFAULT_CLI_MODELS: dict[str, str] = {"coding": "claude-opus-5"}


def cli_model(usage: str) -> str | None:
    """Действующая модель агентного CLI для ``usage``: настройка площадки,
    иначе умолчание кода (`DEFAULT_CLI_MODELS`), иначе None — тогда CLI
    идёт со своей моделью по умолчанию (её имя площадке неизвестно)."""
    v = _valid_model_id((_load().get("models") or {}).get(usage))
    return v or DEFAULT_CLI_MODELS.get(usage)


def cli_model_args(usage: str) -> list[str]:
    """``["--model", id]`` for the agentic-CLI (coding) sites when a model is
    configured for ``usage`` or a code default exists (`cli_model`); ``[]``
    otherwise."""
    v = cli_model(usage)
    return ["--model", v] if v else []


# --------------------------------------------------------------- api key I/O

def _read_api_key_file() -> str:
    try:
        return API_KEY_PATH.read_text("utf-8").strip()
    except OSError:
        return ""


def get_api_key() -> str:
    """Raw Anthropic API key (a usable secret). File first, env fallback.
    NEVER return this value from an endpoint."""
    return _read_api_key_file() or os.environ.get(
        "CREW_ANTHROPIC_API_KEY", "").strip()


def has_api_key() -> bool:
    return bool(get_api_key())


def api_key_hint() -> str:
    """A non-reversible display hint: the last 4 characters, masked."""
    k = get_api_key()
    if not k:
        return ""
    return "…" + (k[-4:] if len(k) >= 4 else k)


def api_key_source() -> str:
    if _read_api_key_file():
        return "file"
    if os.environ.get("CREW_ANTHROPIC_API_KEY", "").strip():
        return "env"
    return "none"


def set_api_key(key: str) -> None:
    """Persist the raw key to the gitignored ``claude_api_key`` file (atomic,
    best-effort 0600). Rejects anything that is not an Anthropic key."""
    key = (key or "").strip()
    if not key:
        raise ValueError("empty api key")
    if not key.startswith("sk-ant-"):
        raise ValueError(
            "does not look like an Anthropic API key (expected sk-ant-…)")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Порядок (четвёртый аудит, §4.4): права сужаются на ПУСТОМ временном
    # файле ДО записи секрета — прежняя версия публиковала секрет с
    # широкими наследуемыми правами и лишь потом суживала их, оставляя
    # окно чтения. Отказ сужения — отказ ротации: прежний ключ и его
    # права остаются нетронутыми (замены ещё не было).
    #
    # Имя временного файла УНИКАЛЬНО и создаётся исключительно (пятый
    # аудит, R5-01): общий предсказуемый `claude_api_key.tmp` позволял
    # двум одновременным ротациям смешать содержимое, а подмена пути
    # между сужением прав и записью — записать секрет мимо защищённого
    # файла. Дескриптор открывается ОДИН раз и держится до конца.
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=str(CONFIG_DIR),
                                    prefix=".claude_api_key-", suffix=".tmp")
    tmp = Path(tmp_name)
    fd_owned = True                          # пока fdopen не принял дескриптор
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        _restrict_windows_acl(tmp)          # на ПУСТОМ файле
        fd_owned = False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)                   # тот же дескриптор, не путь
        _assert_narrow_acl(tmp)             # права проверены ДО замены
        tmp.replace(API_KEY_PATH)
    except BaseException:
        # Windows не удаляет открытый файл: сначала закрыть дескриптор.
        if fd_owned:
            try:
                os.close(fd)
            except OSError:
                pass
        tmp.unlink(missing_ok=True)
        raise


def assert_local_model_url(url: str) -> str:
    """Адрес ЛОКАЛЬНОЙ модели: только петля или частная сеть, только http/s.

    Пятый аудит (R5-07) и P1-11: адрес принимался любой, а к запросу
    прикреплялся токен доступа — вместе с широкими правами на файл
    настроек это давало локальный путь увода секрета (синтетика ушла на
    `169.254.169.254`, служебный адрес облачных метаданных). Проверяются
    и имя, и ВСЕ адреса, в которые оно разрешается: имя вроде
    `localhost.attacker.tld` разрешается наружу.

    Отказ громкий: неразрешимое имя тоже отклоняется — «не смогли
    проверить» не равно «безопасно»."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"адрес локальной модели должен быть http(s), получено "
            f"{parsed.scheme or '<без схемы>'!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("в адресе локальной модели нет имени узла")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80,
                                   proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(
            f"имя {host!r} не разрешается ({exc}) — проверить, что адрес "
            f"локальный, невозможно, поэтому он отклонён") from exc
    outside = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_loopback or ip.is_private):
            outside.append(str(ip))
    if outside:
        raise ValueError(
            f"адрес {url!r} разрешается в НЕлокальные {sorted(set(outside))}: "
            f"к запросу прикрепляется токен доступа, наружу он уходить не "
            f"должен")
    return url


def _assert_narrow_acl(path) -> None:
    """Проверить ФАКТИЧЕСКИЙ список доступа перед заменой цели.

    Пятый аудит (R5-01): итоговые права не перепроверялись — «сузили и
    поверили». Если после сужения широкая группа осталась (наследование
    восстановлено, запись выполнил кто-то ещё), замену делать нельзя."""
    if not _IS_WINDOWS:
        return
    import subprocess
    r = subprocess.run(["icacls", str(path)], capture_output=True,
                       timeout=30, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"права {path} прочитать не удалось — "
                           f"ротация отклонена (rc={r.returncode})")
    text = (r.stdout or b"").decode("ascii", "replace")
    broad = [w for w in ("BUILTIN\\Users", "Authenticated Users", "Everyone")
             if w in text]
    if broad:
        raise RuntimeError(
            f"после сужения у {path} остались широкие права {broad} — "
            f"ротация отклонена, прежний ключ не тронут")


def process_token_sid() -> str:
    """Код безопасности (SID) ФАКТИЧЕСКОГО маркера этого процесса.

    Пятый аудит 2026-07-27 (R5-01): права выдавались имени, собранному из
    `USERDOMAIN` и `USERNAME`. Это изменяемый текст запускающего
    окружения, а не удостоверение процесса: на самой машине обычный
    сеанс дал `WORKGROUP\\lev`, которого в списках доступа нет, и ротация
    падала кодом 1332. Пустое окружение молча пропускало выдачу прав
    вовсе.

    `whoami /user` печатает SID из маркера процесса — то самое, чему
    выдаются права. Имена учётных записей ненадёжны (локализация, рабочая
    группа против домена), SID — нет."""
    if not _IS_WINDOWS:
        return ""
    import subprocess
    try:
        r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                           capture_output=True, timeout=30, check=False)
        if r.returncode != 0:
            return ""
        text = (r.stdout or b"").decode("ascii", "replace")
        # формат: "ДОМЕН\пользователь","S-1-5-21-..."
        parts = [p.strip().strip('"') for p in text.strip().split(",")]
        for p in parts:
            if p.upper().startswith("S-1-"):
                return p
    except (OSError, subprocess.SubprocessError):
        return ""
    return ""


def _restrict_windows_acl(path) -> None:
    """Сузить права файла: прервать наследование, ЯВНО выдать полный
    доступ учётной записи приложения, снять широкие группы.

    Третий аудит 2026-07-26 (§5.5): на Windows `os.chmod(0o600)` списка
    доступа не меняет, а `Path.replace` приносит файлу НАСЛЕДУЕМЫЕ права
    каталога. Четвёртый (§4.4): (1) сужать надо ДО записи секрета — этим
    занимается вызывающий; (2) без явной выдачи записи приложения файл
    после сужения держался только на членстве в группе администраторов,
    которое у неповышенного процесса запрещающее; (3) коды возврата
    icacls обязаны проверяться — все отказы делают ротацию неуспешной,
    а не тихой."""
    if not _IS_WINDOWS:
        return
    import subprocess

    # 1332 = «нет сопоставления имени учётной записи и кода безопасности»:
    # снимаемой группы на этой машине не существует (локализованное имя на
    # англоязычной системе и наоборот). Снимать нечего — это не отказ
    # сужения. Любой ДРУГОЙ ненулевой код фатален.
    ERR_NO_SUCH_PRINCIPAL = 1332

    def run(args, benign=()):
        r = subprocess.run(["icacls", str(path)] + args,
                           capture_output=True, timeout=30, check=False)
        if r.returncode != 0 and r.returncode not in benign:
            raise RuntimeError(
                f"icacls {' '.join(args)} -> rc={r.returncode}: "
                f"{(r.stderr or r.stdout or b'').decode('ascii', 'replace').strip()}")
    app_account = process_token_sid()
    try:
        if not app_account:
            raise RuntimeError(
                "не удалось определить код безопасности процесса — выдать "
                "явные права приложению нечему")
        run(["/grant", f"*{app_account}:(F)"])
        run(["/inheritance:d"])
        for who in ("BUILTIN\\Users", "NT AUTHORITY\\Authenticated Users",
                    "Пользователи", "Прошедшие проверку"):
            run(["/remove:g", who], benign=(ERR_NO_SUCH_PRINCIPAL,))
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise RuntimeError(
            f"права {path} сузить не удалось — ротация отклонена, прежний "
            f"ключ не тронут: {exc}") from exc


def clear_api_key() -> None:
    """Remove the stored key file. The env fallback (if any) is untouched.

    Пятый аудит (R5-01): прежняя версия глотала ЛЮБОЙ `OSError`, и точка
    удаления писала в журнал «ок», даже если файл остался на месте, —
    оператор считал ключ отозванным, а он продолжал работать. Отсутствие
    файла — успех; невозможность удалить — громкий отказ."""
    try:
        API_KEY_PATH.unlink()
    except FileNotFoundError:
        return                              # уже нет — это успех
    except OSError as exc:
        raise RuntimeError(
            f"ключ не удалён ({exc}) — он ПРОДОЛЖАЕТ действовать; "
            f"считать его отозванным нельзя") from exc
