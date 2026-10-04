"""Instance registry over the canonical payloads (API contract v1).

Three sources, fixed by the contract:
  data/gerad/payloads/inst{1..7}_p2.json  -> id gerad-N, label instN
  data/bts_schedules/DL_ATL_2024-08-15.json -> id bts-day
  data/uploads/<user-slug>/<slug>.json    -> id upload-<user-slug>--<slug>
  data/uploads/*.json (legacy flat files) -> id upload-<slug>

The identity/name join uses a DOUBLE hyphen (UPLOAD_LABEL_SEP): a bare '-'
is not injective because both halves may themselves contain hyphens
(user 'bob-evil'+name 'x' and user 'bob'+name 'evil-x' both flatten to
'bob-evil-x'), whereas slugify never emits '--' inside either half, so the
pair splits unambiguously (audit 2026-07-15). Uploads registered before
this change keep their historical single-hyphen id (see list_instances).

Tenancy: GERAD and BTS instances are SHARED (owner None, visible to every
authenticated user). Uploads are per-user: the owner email is recorded in
webapp/config/ownership.json (atomic writes), files live under a per-user
directory named by the email local part slug. Legacy flat upload files are
backfilled to the primary admin on first scan (migration note, 2026-07-13).

Payloads are loaded lazily and cached by (path, mtime): the GERAD months
are 5-25 MB and the instance list must not re-parse them on every call.
The same cache serves results.py (roster.csv needs flight times).

Validation is jsonschema against webapp/schema/canonical_payload.schema.json
plus the referential checks the schema cannot express (airport codes,
assignment->flight references). Failures are returned verbatim — no
silent coercion.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys as _sys  # noqa: E402
if str(REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(REPO_ROOT))
import _data_paths  # noqa: E402

GERAD_PAYLOADS = REPO_ROOT / "data" / "gerad" / "payloads"
BTS_PAYLOAD = REPO_ROOT / "data" / "bts_schedules" / "DL_ATL_2024-08-15.json"
UPLOADS_DIR = REPO_ROOT / "data" / "uploads"
# Артефакты расчётов — в каталоге данных ВНЕ репозитория (№50, этап 2).
# Отслеживаемые свидетельства исследований остаются в
# `REPO_ROOT/final-report` — их читают через EVIDENCE_DIR.
FINAL_REPORT = _data_paths.results_dir()
EVIDENCE_DIR = REPO_ROOT / "final-report"
BEST_DIR = FINAL_REPORT / "best"
TWO_STAGE_JOURNAL = FINAL_REPORT / "gerad_phase2_two_stage.json"
SCHEMA_PATH = REPO_ROOT / "webapp" / "schema" / "canonical_payload.schema.json"
OWNERSHIP_PATH = REPO_ROOT / "webapp" / "config" / "ownership.json"
# Per-dataset LLM eligibility (solver-advisor knowledge base, 2026-07-14).
# Governs which LLM backend may be consulted for solver recommendations on
# this instance: "local-only" forbids any cloud call (customer data stays on
# the machine — the safe default for uploads); "any" additionally permits an
# external cloud LLM (the owner has declared the data non-sensitive — the
# default for the PUBLIC seed benchmarks). Only the dataset owner/admin may
# change it; the consult path never sends raw PII regardless of scope.
LLM_SCOPE_PATH = REPO_ROOT / "webapp" / "config" / "llm_scope.json"
LLM_SCOPES = ("local-only", "any")

# COUNTRY attribute: which national jurisdiction (plus international bodies)
# regulates the airline of this dataset — drives the regulatory agent's
# per-country digest. "INTL" = international rules only (the seed default).
# Stored like llm_scope; ISO-3166 alpha-2 or the literal INTL.
COUNTRY_PATH = REPO_ROOT / "webapp" / "config" / "instance_country.json"
COUNTRY_RE = re.compile(r"^(?:INTL|[A-Z]{2})$")

# Migration owner for pre-tenancy flat files in data/uploads (see module
# docstring). Also the fallback owner for legacy jobs persisted without one.
# Deployment data, not code (owner decision 2026-08-30): the address comes
# from the environment; the neutral default keeps tests and fresh installs
# working without naming any real person. Production launchers set
# CREW_PRIMARY_ADMIN explicitly.
PRIMARY_ADMIN = (os.environ.get("CREW_PRIMARY_ADMIN", "").strip().lower()
                 or "admin@example.com")

MAX_VALIDATION_ERRORS = 20

_cache_lock = threading.Lock()
_payload_cache: dict[str, tuple[float, dict]] = {}   # path -> (mtime, payload)
_json_cache: dict[str, tuple[float, object]] = {}    # small artifacts, same keying


def load_payload(path: Path) -> dict:
    """Parse a canonical payload, cached by mtime."""
    key = str(path)
    mtime = path.stat().st_mtime
    with _cache_lock:
        hit = _payload_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    with _cache_lock:
        _payload_cache[key] = (mtime, payload)
    return payload


def invalidate_payload_cache(path: Path) -> None:
    """Drop a path from the payload cache. Windows file times tick at ~15.6 ms
    — two rapid writes can share an mtime, so a WRITER must invalidate
    explicitly or the next load under the same tick returns STALE data (lost
    update; caught by test_concurrent_applies_serialized, 2026-07-19)."""
    with _cache_lock:
        _payload_cache.pop(str(path), None)


def canonicalize_for_store(payload: dict, where: str) -> dict:
    """Привести набор к хранимой форме и отказать, если это не удалось.

    Единственный допустимый способ подготовить набор к записи. Прежде
    канонизация жила ВНУТРИ `claim_upload` — функции про атомарность и
    владение, — и поэтому распространялась только на новые загрузки. Пять
    производных путей (сценарий «что если», заземление по допускам,
    присуждение отпусков, правка времени рейса оператором, подготовка
    ремонта) писали набор мимо неё и наследовали любую форму, какая была
    (внешний аудит R9, P0-03).
    """
    import _canonical_time
    _canonical_time.canonicalize_payload(payload)
    _canonical_time.assert_canonical(payload, where)
    return payload


def write_payload(target, payload: dict, where: str) -> None:
    """Записать набор на диск через единственную границу.

    Канонизация — свойство ГРАНИЦЫ ЗАПИСИ, а не обязанность каждого
    писателя помнить о ней. Здесь же сбрасывается кеш чтения: писатель,
    забывший это сделать, получал устаревшие данные при двух записях в
    один тик файловых часов Windows.
    """
    canonicalize_for_store(payload, where)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    invalidate_payload_cache(target)


def _load_json_cached(path: Path):
    """mtime-cached parse for the small final-report artifacts; None if absent."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _cache_lock:
        hit = _json_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    with _cache_lock:
        _json_cache[key] = (mtime, data)
    return data


def load_schema() -> dict:
    schema = _load_json_cached(SCHEMA_PATH)
    if schema is None:
        raise RuntimeError(f"canonical schema missing/unreadable: {SCHEMA_PATH}")
    return schema


# ---------------------------------------------------------------- validation

def validate_payload(payload) -> dict:
    """Schema + referential checks. Returns the contract's validation object:
    {"ok": true, "stats": {...}} or {"ok": false, "errors": [str, ...]} with
    at most MAX_VALIDATION_ERRORS entries."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["payload must be a JSON object"]}

    validator = jsonschema.Draft202012Validator(load_schema())
    for err in validator.iter_errors(payload):
        if len(errors) >= MAX_VALIDATION_ERRORS:
            break
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"schema: {where}: {err.message}")

    # Referential checks (contract): flight airports exist, assignment
    # flights exist, employee homeAirport exists. Only meaningful once the
    # containers are lists of dicts — guard so a schema-invalid payload
    # does not crash the checker.
    def _codes(key, field):
        # Only STRING values participate in referential membership — any
        # other type is already reported by the schema pass, and hashing a
        # list/dict here would crash the checker into a 500 (second-round
        # review 2026-07-19).
        items = payload.get(key)
        if not isinstance(items, list):
            return set()
        return {it.get(field) for it in items
                if isinstance(it, dict) and isinstance(it.get(field), str)}

    airport_codes = _codes("airports", "code")
    flight_numbers = _codes("flights", "flightNumber")

    def add(msg):
        if len(errors) < MAX_VALIDATION_ERRORS:
            errors.append(msg)

    # Non-string reference values are skipped in every membership test below
    # (the schema pass reports their type honestly; probing a set with an
    # unhashable list/dict must never 500 the endpoint).
    flights = payload.get("flights")
    if isinstance(flights, list):
        for i, f in enumerate(flights):
            if not isinstance(f, dict):
                continue
            for field in ("departureAirport", "arrivalAirport"):
                code = f.get(field)
                if isinstance(code, str) and code not in airport_codes:
                    add(f"flights[{i}] ({f.get('flightNumber')}): "
                        f"{field} '{code}' not in airports")
            # Хронология — на приёме, а не в решателе (внешний аудит R6,
            # P0-03): формат обоих времён держит pattern схемы, здесь же
            # ловится «прилёт не позже вылета». Сравнение — единым
            # разборщиком (смещение пересчитывается в UTC, не отрезается);
            # нестроковые/кривые значения уже названы схемой — не дублируем.
            dep_s, arr_s = f.get("departureUTCDateTime"), f.get("arrivalUTCDateTime")
            if isinstance(dep_s, str) and isinstance(arr_s, str):
                try:
                    from _canonical_time import parse_utc
                    if parse_utc(arr_s) <= parse_utc(dep_s):
                        add(f"flights[{i}] ({f.get('flightNumber')}): прилёт "
                            f"не позже вылета ({dep_s} → {arr_s})")
                except ValueError as exc:
                    # Форму держит pattern схемы, но он структурный:
                    # «30 февраля», «25:00» и смещение «+25:00» проходят
                    # его и падают только здесь. Глотать это — впускать
                    # календарно невозможное время в расчёт (внешний
                    # аудит R7, P0-03).
                    add(f"flights[{i}] ({f.get('flightNumber')}): {exc}")
    employees = payload.get("employees")
    if isinstance(employees, list):
        for i, e in enumerate(employees):
            if not isinstance(e, dict):
                continue
            home = e.get("homeAirport")
            if isinstance(home, str) and home not in airport_codes:
                add(f"employees[{i}] ({e.get('id')}): homeAirport "
                    f"'{home}' not in airports")
    slots = payload.get("flightAssignments")
    if isinstance(slots, list):
        for i, s in enumerate(slots):
            if not isinstance(s, dict):
                continue
            fn = s.get("flight")
            if isinstance(fn, str) and fn not in flight_numbers:
                add(f"flightAssignments[{i}] (id={s.get('id')}): flight "
                    f"'{fn}' not in flights")

    # C01/C02 phase 3 — OPTIONAL first-class identities. The checks below
    # fire ONLY when the field is present, so BTS/delta payloads (no
    # flightLegId, legitimately repeated flightNumbers across dates) pass
    # exactly as before. Non-string values are covered by the SCHEMA errors
    # above and skipped here (a list/dict id must not crash the referential
    # pass with an unhashable TypeError — review 2026-07-18, finding 1).
    leg_id_set: set = set()
    owner_fn: dict = {}          # leg id -> the flightNumber that carries it
    if isinstance(flights, list):
        for i, f in enumerate(flights):
            if not isinstance(f, dict):
                continue
            lid = f.get("flightLegId")
            if not isinstance(lid, str):
                continue
            if lid in leg_id_set:
                add(f"flights[{i}] ({f.get('flightNumber')}): flightLegId "
                    f"'{lid}' duplicated — leg identity must be unique")
            leg_id_set.add(lid)
            fn = f.get("flightNumber")
            if isinstance(fn, str):
                owner_fn.setdefault(lid, fn)
    if isinstance(slots, list):
        for i, s in enumerate(slots):
            if not isinstance(s, dict):
                continue
            slid = s.get("flightLegId")
            if isinstance(slid, str):
                if slid not in leg_id_set:
                    add(f"flightAssignments[{i}] (id={s.get('id')}): "
                        f"flightLegId '{slid}' not in flights[].flightLegId")
                else:
                    # The referenced leg id must BELONG to the flight this
                    # slot names — otherwise a precise CNL would strip crew
                    # from the wrong flight (review finding 9). Keyed by the
                    # leg's OWNER (leg ids are unique), so a borrowed id is
                    # caught even when the slot's named flight itself carries
                    # no materialized leg id (second-round review).
                    fn = s.get("flight")
                    if (isinstance(fn, str) and slid in owner_fn
                            and owner_fn[slid] != fn):
                        add(f"flightAssignments[{i}] (id={s.get('id')}): "
                            f"flightLegId '{slid}' does not belong to flight "
                            f"'{fn}'")
        # Seat identity: the SOLVER's planning id is crewSlotId-or-id (the
        # Kotlin fallback), so uniqueness must hold over the EFFECTIVE
        # namespace — but only once ANY slot opts into crewSlotId (legacy
        # payloads with duplicate ids keep validating as before).
        if any(isinstance(s, dict) and isinstance(s.get("crewSlotId"), str)
               for s in slots):
            seen_slot: set = set()
            seen_raw: set = set()
            for i, s in enumerate(slots):
                if not isinstance(s, dict):
                    continue
                eff = s.get("crewSlotId")
                if not isinstance(eff, str):
                    eff = s.get("id")
                if not isinstance(eff, str):
                    continue
                if eff in seen_slot:
                    add(f"flightAssignments[{i}]: seat identity '{eff}' "
                        f"duplicated (crewSlotId-or-id must be unique)")
                seen_slot.add(eff)
                # Kotlin equals/hashCode and every self-join guard key on the
                # RAW id even though the planning id is crewSlotId-or-id: two
                # slots with one raw id but distinct crewSlotId become
                # distinct planning entities that compare equal, silently
                # disabling pair constraints (audit 2026-07-19 P1-02b).
                rid = s.get("id")
                if isinstance(rid, str):
                    if rid in seen_raw:
                        add(f"flightAssignments[{i}]: raw id '{rid}' "
                            f"duplicated (solver equality keys on raw id "
                            f"once crewSlotId is in use)")
                    seen_raw.add(rid)

    # v2 entities (qualifications / contracts / dutyHistory) reference
    # employees; a dangling reference is as broken as an unknown airport.
    employee_ids = _codes("employees", "id")
    for key in ("qualifications", "contracts", "dutyHistory"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            emp = it.get("employee")
            if isinstance(emp, str) and emp not in employee_ids:
                add(f"{key}[{i}]: employee '{emp}' not in employees")
    contracts = payload.get("contracts")
    if isinstance(contracts, list):
        seen_c: set = set()
        for i, it in enumerate(contracts):
            if not isinstance(it, dict):
                continue
            emp = it.get("employee")
            if not isinstance(emp, (str, type(None))):
                continue                    # schema reports the type
            if emp in seen_c:
                add(f"contracts[{i}]: duplicate contract for employee "
                    f"'{emp}' (at most one per employee)")
            seen_c.add(emp)
    duty_hist = payload.get("dutyHistory")
    if isinstance(duty_hist, list):
        seen_h: set = set()
        for i, it in enumerate(duty_hist):
            if not isinstance(it, dict):
                continue
            emp, month = it.get("employee"), it.get("month")
            if not isinstance(emp, (str, type(None))) \
                    or not isinstance(month, (str, type(None))):
                continue                    # schema reports the type
            k = (emp, month)
            if k in seen_h:
                add(f"dutyHistory[{i}]: duplicate entry for employee "
                    f"'{k[0]}' month {k[1]}")
            seen_h.add(k)

    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "stats": payload_stats(payload)}


def payload_stats(payload: dict) -> dict:
    return {
        "legs": len(payload.get("flights") or []),
        "employees": len(payload.get("employees") or []),
        "slots": len(payload.get("flightAssignments") or []),
    }


# ----------------------------------------------------------------- ownership

_ownership_lock = threading.Lock()


def _legacy_slug_emails(us: str, canon: str | None = None) -> set[str]:
    """Emails that already own uploads under the given user-directory slug,
    read from the ownership map (upload ids look like 'upload-<us>--<name>').

    Rows owned by PRIMARY_ADMIN are counted ONLY when the caller IS the
    primary admin: list_instances backfills orphan payload files onto the
    admin, and such a synthetic row under someone else's slug must not
    flip that user's grandfathering decision over time (package review
    2026-07-19 — determinism of user_slug). An unreadable map RAISES:
    silently deriving a different slug would fork the user's namespace."""
    owners = load_ownership()      # loud RuntimeError on a corrupt map
    pref = f"upload-{us}{UPLOAD_LABEL_SEP}"
    emails = {v.strip().lower() for k, v in owners.items()
              if k.startswith(pref)}
    admin = PRIMARY_ADMIN.strip().lower()
    if canon != admin:
        emails.discard(admin)
    return emails


def user_slug(email: str) -> str:
    """Identity -> path-safe [a-z0-9-] slug for the per-user upload namespace.

    Until 2026-07-19 this was the bare email local part, so 'alice@d1' and
    'alice@d2' collapsed into ONE tenant namespace — same instance id, label
    and payload path (audit P0-03; more than one allow-listed domain is live,
    so the collision is reachable by real identities). The slug now appends a
    short digest of the FULL canonical email. Identities that already own
    uploads under the historical local-part slug are grandfathered (their
    ids, labels and label-keyed artifacts must not be orphaned); if a
    pre-fix collision already happened, min() picks the keeper
    deterministically and the other identity moves to the hashed slug.
    Loud failure on an email whose local part slugifies to nothing."""
    import hashlib
    canon = email.strip().lower()
    local = slugify(canon.split("@", 1)[0])
    if not local:
        raise ValueError(f"email {email!r} yields an empty user slug")
    legacy = _legacy_slug_emails(local, canon)
    if legacy and canon == min(legacy):
        return local
    digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()
    for n in (6, 12, 40):
        head = local[:SLUG_MAX - n - 1].rstrip("-")
        cand = f"{head}-{digest[:n]}" if head else digest[:n]
        taken_by = _legacy_slug_emails(cand, canon)
        if not taken_by or taken_by == {canon}:
            return cand
    raise ValueError(f"cannot derive a unique user slug for {email!r}")


def load_ownership() -> dict[str, str]:
    """webapp/config/ownership.json -> {instance_id: owner_email}.

    Absent file = no owned instances yet. An EXISTING but malformed file is
    a config error and raises (loud failure) — silently treating owned
    uploads as shared would leak them."""
    try:
        raw = json.loads(OWNERSHIP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"ownership map {OWNERSHIP_PATH} exists but is unreadable/"
            f"malformed: {exc}") from exc
    if not isinstance(raw, dict) or \
            not all(isinstance(v, str) for v in raw.values()):
        raise RuntimeError(
            f"ownership map {OWNERSHIP_PATH} must be a JSON object "
            f"{{instance_id: owner_email}}")
    return dict(raw)


def _save_ownership_locked(owners: dict[str, str]) -> None:
    """Atomic write (tmp + replace) under _ownership_lock."""
    OWNERSHIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OWNERSHIP_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(owners, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(OWNERSHIP_PATH)


def set_owner(inst_id: str, email: str) -> None:
    with _ownership_lock:
        owners = load_ownership()
        owners[inst_id] = email
        _save_ownership_locked(owners)
    materialize_instance_config(inst_id)


def materialize_instance_config(inst_id: str) -> None:
    """Выдать новому набору ЕГО СОБСТВЕННУЮ конфигурацию (P1-10).

    Подстановка значений по умолчанию — событие, у которого есть время:
    она происходит один раз, при появлении набора. Иначе набор при каждом
    расчёте читал бы то, что в файле установки лежит В ЭТОТ МОМЕНТ, и один
    и тот же набор, посчитанный в разные месяцы, считался бы по разным
    нормам — без следа в артефакте.

    Идемпотентно (набор с файлом не трогается) и не роняет создание
    набора: без файла поведение прежнее — шаблон установки, и на экране
    это честно подписано."""
    from . import config_scope
    try:
        config_scope.materialize_instance(inst_id)
    except (OSError, ValueError):
        pass


class UploadExists(Exception):
    """The upload id / payload path is already claimed."""


def claim_upload(inst_id: str, target, payload_obj, email: str) -> None:
    """Atomically create a NEW upload: ownership check, exclusive payload
    write and the ownership row happen under one lock. Before this, the
    check -> write_text -> set_owner sequence in the upload endpoint was not
    a transaction: two colliding requests could both pass the check, then
    overwrite each other's payload and flip the owner (audit 2026-07-19
    P0-03). Raises UploadExists when the id or the file is already taken —
    with the digest-bearing user slug that can only be the caller's own
    namespace, so the error leaks nothing about other tenants."""
    # Канонизация времени НА ВХОДЕ (внешний аудит R8, P0-02; R9, P0-03):
    # смещение пересчитывается в наивный UTC один раз при приёме, и весь
    # расчётный слой видит единственную форму.
    #
    # Прежде здесь стоял предикат «есть ли в строке плюс или зет». Он и был
    # дефектом: отрицательного смещения (`-05:00`) он не видел, и такая
    # метка сохранялась живой — а затем роняла расчёт стоимости и буфер
    # стыковки на вычитании наивного и зонированного времени. Предикат
    # заменён безусловным разбором: решает разборщик, а не подстрока.
    import _canonical_time
    _canonical_time.canonicalize_payload(payload_obj)
    _canonical_time.assert_canonical(payload_obj, "claim_upload")
    target = Path(target)
    with _ownership_lock:
        owners = load_ownership()
        if inst_id in owners:
            raise UploadExists(inst_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "x", encoding="utf-8") as fh:
                fh.write(json.dumps(payload_obj, indent=1))
        except FileExistsError:
            raise UploadExists(inst_id) from None
        except BaseException:
            # Частично записанный файл без ownership-строки навсегда
            # блокировал бы имя 422-ой (ревью пакета 2026-07-19).
            target.unlink(missing_ok=True)
            raise
        owners[inst_id] = email
        _save_ownership_locked(owners)
    # Конфигурация выдаётся ПОСЛЕ снятия замка: запись в чужой каталог не
    # должна удерживать замок владения и не должна отменять уже
    # состоявшуюся выгрузку (P1-10).
    materialize_instance_config(inst_id)


def remove_owner(inst_id: str) -> None:
    with _ownership_lock:
        owners = load_ownership()
        if owners.pop(inst_id, None) is not None:
            _save_ownership_locked(owners)


# ----------------------------------------------------------- llm eligibility

_llm_scope_lock = threading.Lock()


def _default_scope(source: str) -> str:
    """Scope for an instance without an explicit row. Uploads default to the
    safest option (local-only); the PUBLIC seed benchmarks (gerad/bts) may
    use any backend."""
    return "local-only" if source == "upload" else "any"


def load_llm_scopes() -> dict[str, str]:
    """webapp/config/llm_scope.json -> {instance_id: scope}. Absent = no
    overrides (every instance falls back to its per-source default). An
    EXISTING but malformed file is a config error and raises (loud failure):
    silently defaulting an upload to a permissive scope could leak data."""
    try:
        raw = json.loads(LLM_SCOPE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"llm scope map {LLM_SCOPE_PATH} exists but is unreadable/"
            f"malformed: {exc}") from exc
    if not isinstance(raw, dict) or \
            not all(v in LLM_SCOPES for v in raw.values()):
        raise RuntimeError(
            f"llm scope map {LLM_SCOPE_PATH} must be a JSON object "
            f"{{instance_id: {'|'.join(LLM_SCOPES)}}}")
    return dict(raw)


def _save_llm_scopes_locked(scopes: dict[str, str]) -> None:
    LLM_SCOPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LLM_SCOPE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(scopes, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(LLM_SCOPE_PATH)


def set_llm_scope(inst_id: str, scope: str) -> None:
    if scope not in LLM_SCOPES:
        raise ValueError(
            f"llm scope must be one of {LLM_SCOPES}, got {scope!r}")
    with _llm_scope_lock:
        scopes = load_llm_scopes()
        scopes[inst_id] = scope
        _save_llm_scopes_locked(scopes)


def remove_llm_scope(inst_id: str) -> None:
    with _llm_scope_lock:
        scopes = load_llm_scopes()
        if scopes.pop(inst_id, None) is not None:
            _save_llm_scopes_locked(scopes)


_country_lock = threading.Lock()


def load_countries() -> dict[str, str]:
    """webapp/config/instance_country.json -> {instance_id: COUNTRY}.
    Absent = no overrides (every instance defaults to INTL). Malformed
    raises loudly."""
    try:
        raw = json.loads(COUNTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"country map {COUNTRY_PATH} unreadable/malformed: {exc}"
        ) from exc
    if not isinstance(raw, dict) or \
            not all(isinstance(v, str) and COUNTRY_RE.match(v)
                    for v in raw.values()):
        raise RuntimeError(
            f"country map {COUNTRY_PATH} must be {{instance_id: 'INTL'|"
            f"ISO-2}}")
    return dict(raw)


def set_country(inst_id: str, country: str) -> None:
    country = (country or "").strip().upper()
    if not COUNTRY_RE.match(country):
        raise ValueError(f"country must be INTL or an ISO-2 code, "
                         f"got {country!r}")
    with _country_lock:
        m = load_countries()
        m[inst_id] = country
        COUNTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = COUNTRY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(m, indent=1, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(COUNTRY_PATH)


# Delimiter between the uploader's slug and the upload's own slug inside a
# per-user id/label. A DOUBLE hyphen is safe as a boundary marker because
# slugify collapses every run of 2+ hyphens to one and strips the ends
# (see slugify) — so neither half can contain '--', and the flattened
# `us + SEP + slug` is injective in (us, slug). A bare '-' is NOT: it feeds
# the id (cross-tenant existence leak on the collision-check 422) AND the
# label (shared artifact / --name namespace). '--' also stays within the
# solver's [a-z0-9-]+ label charset, so no CLI contract changes (audit
# 2026-07-15).
UPLOAD_LABEL_SEP = "--"


def upload_target(email: str, slug: str) -> tuple[str, str, Path]:
    """(instance_id, label, payload_path) for a NEW upload by this user.

    The label doubles as the global artifact name suffix for stage runs, so
    it is prefixed with the user slug: two users may both upload 'acme'
    without colliding on final-report artifacts. Identity and name are joined
    by UPLOAD_LABEL_SEP so the (user, name) pair flattens injectively — a bare
    hyphen aliased distinct tenants (audit 2026-07-15). The payload PATH keys
    on the directory separator and so was already unambiguous; only the
    flattened id/label needed the two-character delimiter."""
    us = user_slug(email)
    label = f"{us}{UPLOAD_LABEL_SEP}{slug}"
    return f"upload-{label}", label, UPLOADS_DIR / us / f"{slug}.json"


def visible_to(inst: dict, user: dict) -> bool:
    """Owner-or-admin. Nothing is globally shared (2026-07-13): every
    instance — including the seed benchmarks, owned by the primary admin —
    has an owner. `owner is None` is only a defensive fallback and no code
    path produces it. Callers translate invisibility to 404 — never 403 —
    so the existence of another user's instance is not leaked."""
    owner = inst.get("owner")
    return (owner is None or user.get("role") == "admin"
            or owner == user.get("email"))


# ------------------------------------------------------------------ registry

def _gate_status_by_label() -> dict[str, str]:
    """Last result_status per label from the two-stage journal.

    Journal rows are keyed label-or-inst (stage-2 generalization): numeric
    GERAD rows carry {"inst": N} (their label is "instN"), generic rows
    carry {"label": slug, "inst": null}."""
    journal = _load_json_cached(TWO_STAGE_JOURNAL)
    out: dict[str, str] = {}
    if isinstance(journal, list):
        for row in journal:          # later rows win: the journal appends
            if not isinstance(row, dict):
                continue
            status = row.get("result_status")
            if status is None:
                continue
            label = row.get("label")
            if label is None and row.get("inst") is not None:
                label = f"inst{int(row['inst'])}"
            if label is not None:
                out[str(label)] = status
    return out


def _catalog_objective(label: str) -> int | None:
    meta = _load_json_cached(BEST_DIR / f"meta_{label}.json")
    if isinstance(meta, dict):
        obj = meta.get("ip_objective")
        return int(obj) if isinstance(obj, (int, float)) else None
    return None


def repo_rel(path: Path) -> str:
    """ЛОГИЧЕСКИЙ путь для выдачи наружу — никогда не хостовый.

    Артефакты расчётов живут в каталоге данных ВНЕ репозитория (№50,
    этап 2), и прежний фолбэк отдавал их абсолютным путём хоста прямо в
    API (внешний аудит R6, P2-01). Корень данных зеркалит раскладку
    репозитория, поэтому относительная запись одна и та же для обоих
    корней; читают её через resolve_rel(). Файл вне обоих корней
    сводится к имени — раскладка файловой системы наружу не уходит."""
    for root in (REPO_ROOT, _data_paths.data_root()):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def resolve_rel(rel: str) -> Path:
    """Обратная сторона repo_rel: логическая запись -> существующий файл.

    Порядок проб: корень данных, затем репозиторий — В ЭТОМ порядке,
    потому что в репозитории существует одноимённый final-report с
    отслеживаемыми свидетельствами, и обратный порядок молча подхватил
    бы не тот файл. Абсолютные строки прежних сохранённых состояний
    принимаются как есть."""
    p = Path(rel)
    # «..» в логической записи — не путь, а попытка выйти из корней
    # (внешний аудит R8, P2-10): сегодня записи порождает только сам
    # реестр, но результат уходит и в unlink — барьер обязан быть.
    if ".." in p.parts:
        raise ValueError(f"логическая запись не может содержать '..': "
                         f"{rel!r}")
    if p.is_absolute():
        return p
    for root in (_data_paths.data_root(), REPO_ROOT):
        cand = root / p
        if cand.exists():
            return cand
    return REPO_ROOT / p


def _catalog_is_publishable(p: Path) -> bool:
    """Каталог годен для этапа 2, только если несёт свидетельство
    покрытия и оно не говорит «перебор не достроил» (аудит 2026-07-26,
    P0-05). Раньше готовность определялась одним фактом существования
    файла, и частичный каталог молча становился рабочим.

    Каталоги без блока `coverage` — прежние выпуски генерации колонок и
    вендорные каталоги импорта; их поведение не меняем, иначе правка
    обесценила бы уже посчитанные наборы."""
    # Свидетельство гейта читается БЕЗ кэша (третий аудит 2026-07-26,
    # §5.6): кэш ключуется по mtime, а mtime контролирует писатель файла —
    # подмена содержимого с возвратом прежней отметки времени оставляла
    # закэшированный «годен» навсегда. Файл маленький, чтение дёшево.
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(doc, dict):
        return False
    cov = doc.get("coverage")
    if not isinstance(cov, dict):
        # Каталог БЕЗ блока coverage — прежний выпуск генерации колонок.
        # Люк сужен (внешний аудит R6, P1-15): годным считается только
        # каталог, прошедший гейт продвижения, — рядом обязана лежать
        # meta продвижения с ТЕМ ЖЕ отпечатком пространства. Новые
        # продвижения пишут coverage сами; произвольный файл без
        # свидетельства больше не публикуется.
        label = doc.get("label")
        if not label:
            return False
        fp = doc.get("fingerprint")
        for meta_p in (p.parent / f"meta_{label}.json",
                       p.parent / "best" / f"meta_{label}.json"):
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict) or meta.get(
                    "covered_legs_semantics") != "all except proven-impossible":
                continue
            # Связь meta ИМЕННО с этим каталогом (внешний аудит R7,
            # P1-04): совпадения одной строки семантики мало — соседний
            # рукописный JSON открывал бы публикацию. Сверяются метка и
            # целевое значение (оба поля пишет только гейт продвижения и
            # оба есть в самом каталоге), а отпечаток — когда каталог
            # его несёт.
            if meta.get("label") != label:
                continue
            # None == None — не совпадение, а отсутствие обоих значений
            # (внешний аудит R8, P1-04): рукописная meta без целевого
            # значения не должна открывать публикацию.
            if doc.get("ip_objective") is None                     or meta.get("ip_objective") != doc.get("ip_objective"):
                continue
            if fp is None or meta.get("fingerprint") == fp:
                return True
        return False
    # Каталог СО свидетельством судится по нему строго: годными считаются
    # только известные годные вердикты. Повторный аудит 2026-07-26:
    # прежнее `verdict != "INCOMPLETE_SEARCH"` пропускало НЕИЗВЕСТНЫЙ
    # вердикт (опечатка, будущее значение, повреждённый файл) — любой
    # мусор в поле автоматически получал привилегию годного.
    return cov.get("verdict") in ("OK", "INFEASIBLE_INPUT")


def _catalog_path(label: str) -> str | None:
    """Repo-relative path to the instance's pairing catalog, or None.

    Prefer the best catalog over the canonical selected dump (contract:
    stage-2 generalization). A stage-1 run for an upload promotes one of
    these files and thereby flips stage2_available."""
    for p in (BEST_DIR / f"gerad_best_catalog_{label}.json",
              FINAL_REPORT / f"gerad_colgen_selected_{label}.json"):
        if p.exists() and _catalog_is_publishable(p):
            return repo_rel(p)
    return None


def _instance(inst_id: str, label: str, source: str, path: Path,
              gate: dict[str, str], owner: str | None = None,
              scopes: dict[str, str] | None = None,
              countries: dict[str, str] | None = None,
              known_scenarios=None) -> dict:
    payload = load_payload(path)
    stats = payload_stats(payload)
    catalog_path = _catalog_path(label)
    scope = (scopes or {}).get(inst_id) or _default_scope(source)
    # Происхождение правил — в САМОМ перечне (P1-10): иначе экран мог бы
    # назвать его только для того набора, который уже открывали, а
    # остальные молчали бы, хотя правила у них есть. Здесь только проверки
    # существования файлов — значения не разбираются.
    from . import config_scope
    rules_origin = config_scope.origin_for(
        config_scope.DOMAIN_RULES, inst_id) if known_scenarios is None else \
        config_scope.resolve(config_scope.DOMAIN_RULES, inst_id,
                             known_scenarios)[1]
    country = (countries or {}).get(inst_id, "INTL")
    return {
        "id": inst_id,
        "label": label,
        "source": source,
        # Tenancy: every instance has an owner (seed data -> primary admin,
        # uploads -> uploader). The owner annotation is safe to serve: a
        # plain user only ever receives their own instances.
        "owner": owner,
        "shared": owner is None,
        "payload_path": repo_rel(path),
        **stats,
        "has_best_catalog": (BEST_DIR / f"gerad_best_catalog_{label}.json").exists(),
        "catalog_objective": _catalog_objective(label),
        "catalog_path": catalog_path,
        # Stage 2 runs for ANY instance whose label has a catalog (the
        # generic runner takes --payload/--catalog/--label).
        "stage2_available": catalog_path is not None,
        "last_gate_status": gate.get(label),
        # Откуда взяты правила яруса 1 ЭТОГО набора (P1-10): встроенные /
        # шаблон установки / правила набора / правила сценария.
        "rules_origin": rules_origin,
        # LLM eligibility for solver-advisor consults (see LLM_SCOPE_PATH).
        "llm_scope": scope,
        # National jurisdiction for the regulatory agent (see COUNTRY_PATH).
        "country": country,
    }


# Cyrillic -> Latin transliteration (Russian + Kazakh extras) so names typed
# in the UI's primary languages yield usable slugs: labels feed artifact
# filenames and solver CLI args, which must stay ASCII. Scripts not covered
# here (e.g. Arabic) still slug to empty — callers needing a guaranteed slug
# use slugify_or_hash.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ә": "a", "ғ": "g", "қ": "q", "ң": "n", "ө": "o", "ұ": "u", "ү": "u",
    "һ": "h", "і": "i",
}


# Cap the slug so name-derived filenames (data/uploads/<user>/<slug>.json,
# final-report artifact names, the solver --name arg) never blow past the
# Windows 260-char MAX_PATH mid-run (audit 2026-07-15).
SLUG_MAX = 64


def slugify(name: str) -> str:
    low = name.strip().lower()
    low = "".join(_TRANSLIT.get(ch, ch) for ch in low)
    slug = re.sub(r"[^a-z0-9-]+", "-", low).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:SLUG_MAX].rstrip("-")


def slugify_or_hash(name: str) -> str:
    """slugify, but NEVER empty: names in scripts the transliteration table
    does not cover (e.g. Arabic) get a stable short digest instead, so any
    UI language can name things."""
    slug = slugify(name)
    if slug:
        return slug
    import hashlib
    return "n" + hashlib.sha1(name.strip().encode("utf-8")).hexdigest()[:8]


def list_instances() -> list[dict]:
    gate = _gate_status_by_label()
    # Реестр сценариев разбирается ОДИН раз на весь перечень: происхождение
    # правил спрашивается у каждого набора, а наборов на боевом контуре
    # сотни (P1-10).
    from . import config_scope as _cs
    known_scenarios = _cs.known_scenario_ids()
    scopes = load_llm_scopes()
    countries = load_countries()
    out: list[dict] = []
    # Ownership model (2026-07-13): NOTHING is globally shared. The seed
    # benchmark data (GERAD 1-7, BTS day) and all its computed artifacts
    # belong to the primary admin; a brand-new plain user sees an EMPTY
    # workspace until they upload. Admins see everything (owner-annotated).
    for n in range(1, 8):
        path = GERAD_PAYLOADS / f"inst{n}_p2.json"
        if path.exists():
            out.append(_instance(f"gerad-{n}", f"inst{n}", "gerad", path,
                                 gate=gate, owner=PRIMARY_ADMIN, scopes=scopes,
                                 countries=countries,
                                 known_scenarios=known_scenarios))
    if BTS_PAYLOAD.exists():
        out.append(_instance("bts-day", "bts-day", "bts", BTS_PAYLOAD,
                             gate=gate, owner=PRIMARY_ADMIN, scopes=scopes,
                             countries=countries,
                             known_scenarios=known_scenarios))
    if UPLOADS_DIR.is_dir():
        owners = load_ownership()
        backfill: dict[str, str] = {}
        entries: list[tuple[str, str, Path]] = []   # (id, label, path)
        # Legacy flat files (pre-tenancy layout): id keeps the historical
        # upload-<slug> shape; a missing ownership row is backfilled to the
        # primary admin (migration note).
        for path in sorted(UPLOADS_DIR.glob("*.json")):
            if path.name.endswith(".conversion_check.json"):
                continue   # SSIM-import audit artifact, not a payload
            entries.append((f"upload-{path.stem}", path.stem, path))
        # Per-user directories: data/uploads/<user-slug>/<slug>.json.
        for udir in sorted(p for p in UPLOADS_DIR.iterdir() if p.is_dir()):
            for path in sorted(udir.glob("*.json")):
                if path.name.endswith(".conversion_check.json"):
                    continue
                label = f"{udir.name}{UPLOAD_LABEL_SEP}{path.stem}"
                inst_id = f"upload-{label}"
                # Backward compat: uploads registered before the separator
                # became '--' live on their historical single-hyphen id. If
                # that legacy id owns an ownership row, keep serving the file
                # under it — its ownership/scope/country rows and its
                # label-keyed artifacts are all keyed by the legacy id/label,
                # so switching would orphan them (and hand the file to the
                # primary admin via the backfill below). New uploads have no
                # legacy row and take the unambiguous '--' id.
                if inst_id not in owners:
                    legacy_label = f"{udir.name}-{path.stem}"
                    legacy_id = f"upload-{legacy_label}"
                    if legacy_id in owners:
                        label, inst_id = legacy_label, legacy_id
                entries.append((inst_id, label, path))
        for inst_id, label, path in entries:
            owner = owners.get(inst_id)
            if owner is None:
                # Orphan payload without an ownership row: owned by the
                # primary admin so it never silently becomes shared.
                owner = PRIMARY_ADMIN
                backfill[inst_id] = owner
            try:
                inst = _instance(inst_id, label, "upload", path,
                                 gate=gate, owner=owner, scopes=scopes,
                                 countries=countries,
                                 known_scenarios=known_scenarios)
            except FileNotFoundError:
                # Выгрузку удалили между обходом каталога и чтением файла
                # (гонка «перечисление против удаления», 2026-07-26).
                # Экземпляра больше нет — пропускаем его, но НЕ роняем
                # весь перечень: иначе одно удаление обрушивает витрину
                # показателей и любой экран со списком наборов.
                backfill.pop(inst_id, None)
                continue
            out.append(inst)
        if backfill:
            with _ownership_lock:
                merged = load_ownership()
                merged.update(backfill)
                _save_ownership_locked(merged)
    return out


def list_instances_for(user: dict) -> list[dict]:
    """Registry view for one authenticated user: shared instances plus the
    caller's own uploads; admins additionally see every upload (annotated
    with its owner, as all instances are)."""
    return [i for i in list_instances() if visible_to(i, user)]


def get_instance(inst_id: str) -> dict | None:
    for inst in list_instances():
        if inst["id"] == inst_id:
            return inst
    return None


def get_instance_for(inst_id: str, user: dict) -> dict | None:
    """get_instance gated by visibility: another user's instance resolves
    to None exactly like a nonexistent one (callers answer 404, not 403)."""
    inst = get_instance(inst_id)
    if inst is None or not visible_to(inst, user):
        return None
    return inst


def instance_payload_path(inst: dict) -> Path:
    return resolve_rel(inst["payload_path"])


def gerad_inst_number(inst: dict) -> int | None:
    """gerad-N -> N; None for every other source (those use the generic
    --payload/--catalog/--label mode of the stage-2 runner)."""
    if inst["source"] != "gerad":
        return None
    m = re.fullmatch(r"gerad-([1-7])", inst["id"])
    return int(m.group(1)) if m else None
