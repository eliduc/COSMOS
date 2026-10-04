"""Universal data import: arbitrary files -> canonical payload (2026-07-14).

Flow (user-approved design):
  1. ANALYZE: parse the uploaded files deterministically (CSV / XLSX / JSON /
     plain text) into headers + rows; take a SAMPLE (headers + first rows).
  2. If the input is already a canonical payload, skip the AI entirely.
  3. Otherwise ask an AI model for a MAPPING PLAN only (which file plays
     which role, which column feeds which canonical field, date formats).
     The model sees the sample, never the whole file; the backend is chosen
     from the dataset's L/C attribute (llm_scope) — 'local-only' datasets
     never reach a cloud model.
  4. APPLY the plan with deterministic code over the full files (no data
     ever passes through the model), merge multi-file sets into one payload,
     synthesize flightAssignments when absent.
  5. CHECK: canonical schema + referential checks (registry.validate_payload)
     plus semantic checks (arrival after departure, duplicates, durations).
  6. The result is held as a PENDING import; the human reviews the mapping,
     preview and checks in the UI and only then commits (saves) it.

The mapping plan is the security boundary on the AI side: it may only name
columns/constants and formats; every value that lands in the payload is read
from the user's file by this code.

Sample masking (strict-mode hardening, data-formalization doc §8-bis): the
sample rows ARE customer data, so their VALUES are masked shape-preservingly
(letter->X/x, digit->9, separators kept) whenever the sample would leave the
contour — always for the cloud backend, and also for the local one when
CREW_IMPORT_MASK_SAMPLES=1 (paranoid installs). Headers stay raw: they are
the mapping vocabulary, not row data.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime

from . import llm_backend, settings

MAX_TOTAL_BYTES = 30 * 1024 * 1024
SAMPLE_ROWS = 30
PREVIEW_ROWS = 8
PENDING_TTL_S = 30 * 60
ANALYZE_TIMEOUT_S = 180.0

ROLES = ("flights", "employees", "assignments", "airports", "ignore")

# Canonical fields the plan may target, per role (anything else is dropped).
ROLE_FIELDS = {
    "flights": ("flightNumber", "departureAirport", "departureUTCDateTime",
                "arrivalAirport", "arrivalUTCDateTime", "aircraftType"),
    "employees": ("id", "name", "homeAirport", "skills", "languages",
                  "unavailableDays", "typeRatings", "seniority", "isReserve",
                  "preferredDaysOff", "undesiredAirports", "preferredFlights"),
    "assignments": ("id", "flight", "indexInFlight", "requiredSkill",
                    "employee"),
    "airports": ("code", "name", "latitude", "longitude"),
}
LIST_FIELDS = {"skills", "languages", "unavailableDays", "typeRatings",
               "preferredDaysOff", "undesiredAirports", "preferredFlights"}
REQUIRED_FIELDS = {
    "flights": ("flightNumber", "departureAirport", "departureUTCDateTime",
                "arrivalAirport", "arrivalUTCDateTime"),
    "employees": ("id", "homeAirport"),
    "assignments": ("flight", "requiredSkill"),
    "airports": ("code",),
}


class ImportError_(ValueError):
    """User-facing import failure (bad file, bad plan, no usable data)."""


# ------------------------------------------------------------------ parsing

def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _parse_csv(text: str) -> tuple[list[str], list[list[str]], bool]:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    # Does row 0 look like a real header, or is it already data? Uncertain
    # (sniff failure) counts as NOT a header, so the cloud-masking path errs
    # toward masking row 0 rather than leaking a data row as "headers".
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = False
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ImportError_("файл пуст")
    headers = [h.strip() for h in rows[0]]
    return headers, rows[1:], has_header


def _parse_xlsx(raw: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ImportError_(
            "поддержка Excel недоступна: пакет openpyxl не установлен") from exc
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True,
                                data_only=True)
    ws = wb.worksheets[0]
    rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            rows.append(cells)
    wb.close()
    if not rows:
        raise ImportError_("лист Excel пуст")
    headers = rows[0]
    return headers, rows[1:]


def _parse_json_records(text: str) -> tuple[list[str], list[list[str]], object]:
    """Foreign JSON: a top-level list of flat records (or a dict holding
    one). Returns (headers, rows) tabularized; raw parsed object as third."""
    data = json.loads(text)
    records = None
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # single list-valued key (e.g. {"flights": [...]})
        lists = [v for v in data.values() if isinstance(v, list) and v]
        if len(lists) == 1:
            records = lists[0]
    if not records or not all(isinstance(r, dict) for r in records):
        raise ImportError_(
            "JSON не удалось привести к таблице записей (ожидается список "
            "объектов)")
    headers: list[str] = []
    for r in records:
        for k in r:
            if k not in headers:
                headers.append(k)
    rows = [["" if r.get(h) is None else
             (json.dumps(r.get(h), ensure_ascii=False)
              if isinstance(r.get(h), (list, dict)) else str(r.get(h)))
             for h in headers] for r in records]
    return headers, rows, data


def _is_canonical(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(data, dict) and \
            {"airports", "employees", "flights"} <= set(data.keys()):
        return data
    return None


def parse_file(name: str, raw: bytes) -> dict:
    """One uploaded file -> {name, kind, headers, rows} (or kind='canonical'
    with the parsed payload). Plain text goes through the CSV sniffing path
    (whitespace/;-delimited tables usually survive it)."""
    lower = name.lower()
    if lower.endswith(".xlsx"):
        headers, rows = _parse_xlsx(raw)
        return {"name": name, "kind": "xlsx", "headers": headers,
                "rows": rows, "has_header": True}
    text = _decode_text(raw)
    if lower.endswith(".json") or text.lstrip()[:1] in ("{", "["):
        canonical = _is_canonical(text)
        if canonical is not None:
            return {"name": name, "kind": "canonical", "payload": canonical}
        headers, rows, _ = _parse_json_records(text)
        return {"name": name, "kind": "json", "headers": headers,
                "rows": rows, "has_header": True}   # JSON keys are names
    headers, rows, has_header = _parse_csv(text)
    return {"name": name, "kind": "csv", "headers": headers, "rows": rows,
            "has_header": has_header}


# ------------------------------------------------------------ mapping plan

_PLAN_PROMPT = """Ты — инженер данных. Пользователь загрузил файлы для задачи
планирования экипажей (пейринг/ростеринг). Ниже — образцы файлов (заголовки и
первые строки). Определи для КАЖДОГО файла его роль и правила преобразования в
канонический формат.

Роли: flights (рейсы), employees (экипажи), assignments (слоты назначения),
airports (аэропорты), ignore (не относится к задаче).

Канонические поля по ролям:
- flights: flightNumber, departureAirport, departureUTCDateTime,
  arrivalAirport, arrivalUTCDateTime, aircraftType
- employees: id, name, homeAirport, skills, languages, seniority, isReserve,
  unavailableDays, typeRatings, preferredDaysOff
- assignments: id, flight, indexInFlight, requiredSkill, employee
- airports: code, name, latitude, longitude

Правило для каждого поля — один из вариантов:
  {"column": "<заголовок>"} — взять из колонки;
  {"columns": ["<a>", "<b>"], "join": " "} — склеить несколько колонок;
  {"const": <значение>} — константа для всех строк.
Для полей-дат/времени укажи "datetime_format" файла в синтаксисе Python
strptime (например "%d.%m.%Y %H:%M"). Для списковых полей (skills, languages)
укажи "list_separator" (например ",").

Если файлов с ролью assignments нет, предложи "assignments_synthesis":
{"seats": [{"requiredSkill": "..."}, ...]} — какие места нужны на каждый рейс,
исходя из увиденных навыков экипажей (обычно 2 пилота).

ОБРАЗЦЫ ФАЙЛОВ:
{samples}

Ответь СТРОГО одним JSON-объектом:
{"files": [{"file": "<имя>", "role": "<роль>",
            "mapping": {"<поле>": <правило>, ...},
            "datetime_format": "<strptime или null>",
            "list_separator": "<строка или null>",
            "notes": "<кратко по-русски>"}],
 "assignments_synthesis": {"seats": [{"requiredSkill": "..."}]} | null,
 "rationale": "<2-3 предложения по-русски>"}
"""


def _mask_value(s: str) -> str:
    """Shape-preserving mask: any letter -> X/x (case kept, any script),
    any digit -> 9, everything else (separators, punctuation, spaces) stays.
    The model still sees value SHAPES ("9999-99-99 99:99", "Xxxxxx X.X.")
    and can assign roles/fields, but no real customer value egresses."""
    out = []
    for ch in s:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    return "".join(out)


def _samples_block(files: list[dict], mask: bool = False) -> str:
    parts = []
    cell = (lambda c: _mask_value(str(c))) if mask else str
    for f in files:
        if f["kind"] == "canonical":
            continue
        # When masking, a file WITHOUT a confident header row has its "headers"
        # as a real data row (a header-less crew CSV starts with an employee
        # record) — mask them too, otherwise §8-бис leaks that row to the
        # cloud. A real header row is the mapping vocabulary and stays raw.
        header_is_data = mask and not f.get("has_header", True)
        head = " | ".join((_mask_value(str(h)) if header_is_data else str(h))
                          for h in f["headers"])
        lines = [" | ".join(cell(c) for c in r) for r in f["rows"][:SAMPLE_ROWS]]
        if not mask:
            note = ""
        elif header_is_data:
            note = ("ПРИМЕЧАНИЕ: значения замаскированы (буква→X/x, цифра→9); "
                    "в файле НЕТ строки заголовков — первая строка тоже "
                    "данные, маппинг по позиции/форме колонок.\n")
        else:
            note = ("ПРИМЕЧАНИЕ: значения ячеек замаскированы (буква→X/x, "
                    "цифра→9, разделители сохранены); заголовки настоящие.\n")
        parts.append(f"### {f['name']} ({f['kind']}, строк: {len(f['rows'])})\n"
                     + note + f"ЗАГОЛОВКИ: {head}\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _mask_samples_for(backend: str) -> bool:
    """The cloud backend NEVER sees raw sample values (contour floor); the
    local model sees them only when the super-admin Settings policy or the
    install env forces masking everywhere."""
    if backend == "cloud":
        return True
    # The deployment env flag is an IRREVERSIBLE floor (M02): the super-admin
    # Settings policy can only RAISE masking, never lower it below what the
    # install forced. So "cloud" (local-raw) cannot turn off an env-set floor.
    env_floor = os.environ.get("CREW_IMPORT_MASK_SAMPLES", "") == "1"
    policy = settings.get_mask_samples()          # None | "cloud" | "always"
    if policy == "always" or env_floor:
        return True
    return False


def _extract_plan(text: str) -> dict | None:
    from .solver_advisor import _extract_json_objects
    for d in reversed(_extract_json_objects(text)):
        if isinstance(d.get("files"), list):
            return d
    return None


def sanitize_plan(raw: dict, files: list[dict]) -> dict:
    """Whitelist the AI plan: known roles, known fields, rules that reference
    real columns only. Anything unknown is dropped (never executed)."""
    by_name = {f["name"]: f for f in files}
    out_files = []
    for pf in raw.get("files") or []:
        if not isinstance(pf, dict):
            continue
        name = str(pf.get("file") or "")
        src = by_name.get(name)
        if src is None or src["kind"] == "canonical":
            continue
        role = str(pf.get("role") or "ignore")
        if role not in ROLES:
            role = "ignore"
        headers = set(src["headers"])
        mapping: dict = {}
        for field, rule in (pf.get("mapping") or {}).items():
            if role == "ignore" or field not in ROLE_FIELDS.get(role, ()):
                continue
            if not isinstance(rule, dict):
                continue
            if isinstance(rule.get("column"), str) and rule["column"] in headers:
                mapping[field] = {"column": rule["column"]}
            elif (isinstance(rule.get("columns"), list)
                  and rule["columns"]
                  and all(isinstance(c, str) and c in headers
                          for c in rule["columns"])):
                mapping[field] = {"columns": list(rule["columns"]),
                                  "join": str(rule.get("join", " "))[:4]}
            elif "const" in rule and isinstance(
                    rule["const"], (str, int, float, bool, list)):
                mapping[field] = {"const": rule["const"]}
        out_files.append({
            "file": name, "role": role, "mapping": mapping,
            "datetime_format": (str(pf["datetime_format"])[:64]
                                if pf.get("datetime_format") else None),
            "list_separator": (str(pf["list_separator"])[:4]
                               if pf.get("list_separator") else None),
            "notes": str(pf.get("notes") or "")[:400],
        })
    synth = raw.get("assignments_synthesis")
    seats = []
    if isinstance(synth, dict):
        for s in (synth.get("seats") or [])[:6]:
            if isinstance(s, dict) and isinstance(s.get("requiredSkill"), str):
                seats.append({"requiredSkill": s["requiredSkill"][:40]})
    return {"files": out_files,
            "assignments_synthesis": ({"seats": seats} if seats else None),
            "rationale": str(raw.get("rationale") or "")[:800]}


def propose_mapping(files: list[dict], scope: str,
                    backend_request: str | None = None) -> dict:
    """Ask the scope-allowed AI model for a mapping plan over the SAMPLES.
    Returns {plan, backend, model, source, llm_error, samples_masked}."""
    backend = llm_backend.resolve_backend(scope, backend_request)
    masked = _mask_samples_for(backend)
    prompt = _PLAN_PROMPT.replace("{samples}",
                                  _samples_block(files, mask=masked))
    res = llm_backend.complete(prompt, backend=backend,
                               timeout_s=ANALYZE_TIMEOUT_S, usage="text")
    if res.get("ok"):
        raw = _extract_plan(res["text"])
        if raw:
            return {"plan": sanitize_plan(raw, files), "backend": backend,
                    "model": res.get("model"), "source": "llm",
                    "llm_error": None, "samples_masked": masked}
        err = "ответ модели не распарсился как план маппинга"
    else:
        err = res.get("error")
    return {"plan": _heuristic_plan(files), "backend": backend,
            "model": res.get("model"), "source": "fallback",
            "llm_error": err, "samples_masked": masked}


_HEUR = {
    "flights": {
        "flightNumber": ("flightnumber", "flight_no", "flight", "рейс",
                         "номер рейса", "flt"),
        "departureAirport": ("departureairport", "origin", "from", "dep",
                             "откуда", "вылет из"),
        "arrivalAirport": ("arrivalairport", "destination", "to", "arr",
                           "куда", "прилет в", "прилёт в"),
        "departureUTCDateTime": ("departureutcdatetime", "departure",
                                 "dep_time", "std", "вылет"),
        "arrivalUTCDateTime": ("arrivalutcdatetime", "arrival", "arr_time",
                               "sta", "прилет", "прилёт"),
        "aircraftType": ("aircrafttype", "aircraft", "type", "вс", "тип"),
    },
    "employees": {
        "id": ("id", "employee_id", "empid", "табельный", "код"),
        "name": ("name", "fullname", "фио", "имя"),
        "homeAirport": ("homeairport", "base", "home", "база"),
        "skills": ("skills", "skill", "position", "role", "должность",
                   "квалификация"),
        "seniority": ("seniority", "стаж"),
    },
}


def _heuristic_plan(files: list[dict]) -> dict:
    """No-AI fallback: match headers against a small synonym table so the
    human still gets an editable draft plan instead of a dead end."""
    out = []
    for f in files:
        if f["kind"] == "canonical":
            continue
        low = {h.lower().strip(): h for h in f["headers"]}
        best_role, best_map = "ignore", {}
        for role, fields in _HEUR.items():
            m = {}
            for field, syns in fields.items():
                for s in syns:
                    if s in low:
                        m[field] = {"column": low[s]}
                        break
            need = set(REQUIRED_FIELDS[role])
            if len([k for k in m if k in need]) >= max(2, len(need) - 2) \
                    and len(m) > len(best_map):
                best_role, best_map = role, m
        out.append({"file": f["name"], "role": best_role, "mapping": best_map,
                    "datetime_format": None, "list_separator": ",",
                    "notes": "черновик без модели ИИ — проверьте вручную"})
    return {"files": out, "assignments_synthesis": None,
            "rationale": "Модель ИИ недоступна — черновой маппинг по "
                         "словарю синонимов. Проверьте каждое поле."}


# ------------------------------------------------------------ apply plan

_DT_FALLBACKS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                 "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M",
                 "%Y-%m-%dT%H:%M")


def _parse_dt(value: str, fmt: str | None) -> str:
    v = value.strip()
    if not v:
        raise ImportError_("пустая дата/время")
    for f in ([fmt] if fmt else []) + list(_DT_FALLBACKS):
        try:
            return datetime.strptime(v, f).strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            continue
    try:                       # ISO with timezone/fraction
        return datetime.fromisoformat(v.replace("Z", "+00:00")) \
            .strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ImportError_(f"не удалось разобрать дату/время: {v!r}")


def _cell(row: dict, rule: dict) -> object:
    if "const" in rule:
        return rule["const"]
    if "columns" in rule:
        return rule.get("join", " ").join(
            str(row.get(c, "")).strip() for c in rule["columns"]).strip()
    return str(row.get(rule.get("column"), "")).strip()


def _apply_role(src: dict, pf: dict) -> list[dict]:
    headers = src["headers"]
    fmt = pf.get("datetime_format")
    sep = pf.get("list_separator") or ","
    out = []
    errors = 0
    for raw_row in src["rows"]:
        row = {h: (raw_row[i] if i < len(raw_row) else "")
               for i, h in enumerate(headers)}
        rec: dict = {}
        try:
            for field, rule in pf["mapping"].items():
                val = _cell(row, rule)
                if field.endswith("UTCDateTime"):
                    val = _parse_dt(str(val), fmt)
                elif field in LIST_FIELDS:
                    if isinstance(val, list):
                        val = [str(x).strip() for x in val if str(x).strip()]
                    else:
                        val = [s.strip() for s in str(val).split(sep)
                               if s.strip()]
                elif field == "seniority":
                    val = float(val) if str(val).strip() else None
                elif field == "indexInFlight":
                    val = int(float(val)) if str(val).strip() else 0
                elif field == "isReserve":
                    val = str(val).strip().lower() in ("1", "true", "yes", "да")
                elif field in ("latitude", "longitude"):
                    val = float(val) if str(val).strip() else None
                else:
                    val = str(val).strip()
                if val not in (None, ""):
                    rec[field] = val
        except ImportError_:
            errors += 1
            continue
        if rec:
            out.append(rec)
    if errors and errors >= max(3, len(src["rows"]) // 2):
        raise ImportError_(
            f"{src['name']}: больше половины строк ({errors}) не "
            f"преобразовались — проверьте формат даты/времени в плане")
    return out


def build_payload(files: list[dict], plan: dict) -> tuple[dict, list[str]]:
    """Deterministically apply the plan; returns (payload, warnings)."""
    warnings: list[str] = []
    # canonical file present -> it IS the payload (multi-file merge with a
    # canonical base is out of scope; honest warning if extra files exist).
    canon = [f for f in files if f["kind"] == "canonical"]
    if canon:
        if len(files) > 1:
            warnings.append("канонический JSON объединён быть не может — "
                            "остальные файлы проигнорированы")
        return canon[0]["payload"], warnings

    by_name = {f["name"]: f for f in files}
    collected: dict[str, list[dict]] = {
        "flights": [], "employees": [], "assignments": [], "airports": []}
    for pf in plan.get("files") or []:
        src = by_name.get(pf.get("file"))
        if src is None or pf.get("role") in (None, "ignore"):
            continue
        missing = [f for f in REQUIRED_FIELDS[pf["role"]]
                   if f not in (pf.get("mapping") or {})]
        if missing:
            raise ImportError_(
                f"{pf['file']}: в плане нет обязательных полей "
                f"{', '.join(missing)} для роли {pf['role']}")
        collected[pf["role"]].extend(_apply_role(src, pf))

    flights = collected["flights"]
    employees = collected["employees"]
    if not flights:
        raise ImportError_("ни один файл не дал рейсов (роль flights)")
    if not employees:
        raise ImportError_("ни один файл не дал экипажей (роль employees)")

    # Crew-seat roles are needed BEFORE defaulting employee skills: when we
    # synthesize the seats (the roles are our invention), a crew with no
    # declared skills must default to a set that actually matches those seats,
    # or the phase-2 requiredSkill check (coverage + GERAD2P hard constraint)
    # would reject every assignment and the imported roster would be
    # unsolvable. With an explicit assignments file the seats come from data.
    assignments = collected["assignments"]
    synth_seats = None
    if not assignments:
        synth_seats = ((plan.get("assignments_synthesis") or {}).get("seats")
                       or [{"requiredSkill": "Captain"},
                           {"requiredSkill": "FirstOfficer"}])

    # employees: schema requires skills — default honestly when absent.
    default_skills = (sorted({s["requiredSkill"] for s in synth_seats})
                      if synth_seats else ["Pilot"])
    filled_default = False
    for e in employees:
        if not e.get("skills"):
            e["skills"] = list(default_skills)
            filled_default = True
    if filled_default:
        if synth_seats:
            warnings.append("у части экипажей не было навыков — подставлены "
                            "навыки под синтезированные места экипажа: "
                            + ", ".join(default_skills))
        else:
            warnings.append("у части экипажей не было навыков — подставлен "
                            "навык по умолчанию Pilot")

    # airports: explicit file wins; otherwise derive from flight/base codes.
    airports = collected["airports"]
    if not airports:
        codes = sorted(
            ({f.get("departureAirport") for f in flights}
             | {f.get("arrivalAirport") for f in flights}
             | {e.get("homeAirport") for e in employees}) - {None, ""})
        airports = [{"code": c} for c in codes if c]
        warnings.append("список аэропортов выведен из кодов в рейсах и базах")

    # C01/C02 phase 3: materialize the first-class leg identity on every
    # imported flight (recipe: flightNumber|departureUTCDateTime — exactly
    # the key semantic_checks dedups on). The canonical passthrough returns
    # earlier and is NEVER touched: a stored payload re-uploads byte-identical.
    # A row with a BLANK flight number still reaches validation, where the
    # schema reports it honestly — materialization must not crash on it
    # (review finding 4/16: KeyError turned a clean 422 into a 500).
    for f in flights:
        if (not f.get("flightLegId") and f.get("flightNumber")
                and f.get("departureUTCDateTime")):
            f["flightLegId"] = (f"{f['flightNumber']}|"
                                f"{f['departureUTCDateTime']}")
    _legs_of_fn: dict = {}
    for f in flights:
        if f.get("flightLegId") and f.get("flightNumber"):
            _legs_of_fn.setdefault(f["flightNumber"], []).append(
                f["flightLegId"])

    if synth_seats is not None:
        n = 0
        for f in flights:
            if not f.get("flightNumber"):
                # blank number: the flight itself is schema-invalid and gets
                # reported by validation — synthesizing seats for it would
                # only crash here (second-round review 2026-07-19).
                continue
            for i, seat in enumerate(synth_seats):
                n += 1
                a = {"id": str(n), "crewSlotId": str(n),
                     "flight": f["flightNumber"],
                     "indexInFlight": i,
                     "requiredSkill": seat["requiredSkill"]}
                if f.get("flightLegId"):
                    a["flightLegId"] = f["flightLegId"]
                assignments.append(a)
        warnings.append(
            f"слоты назначений сгенерированы: {len(synth_seats)} на рейс "
            f"({', '.join(s['requiredSkill'] for s in synth_seats)})")
    else:
        for i, a in enumerate(assignments):
            a.setdefault("id", str(i + 1))
            a.setdefault("indexInFlight", 0)
        # crewSlotId only when source ids are unique (a duplicate id would
        # otherwise flip a previously-importable file into a validation
        # error); assignment flightLegId only when the flight ref is
        # UNambiguous (repeated numbers cannot be resolved honestly).
        ids = [a.get("id") for a in assignments]
        if len(ids) == len(set(ids)):
            for a in assignments:
                a.setdefault("crewSlotId", a["id"])
        unique_leg = {fn: legs[0] for fn, legs in _legs_of_fn.items()
                      if len(legs) == 1}
        for a in assignments:
            if not a.get("flightLegId"):
                lid = unique_leg.get(a.get("flight"))
                if lid:
                    a["flightLegId"] = lid

    payload = {
        "formatVersion": "1",
        "provenance": {"adapter": "data_import/v1",
                       "source": ", ".join(sorted(by_name))},
        "airports": airports, "employees": employees, "flights": flights,
        "flightAssignments": assignments,
    }
    return payload, warnings


# --------------------------------------------------------- semantic checks

def semantic_checks(payload: dict) -> dict:
    """Data-plausibility checks on top of the schema: {errors, warnings}."""
    errors: list[str] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}
    for i, f in enumerate(payload.get("flights") or []):
        fn = f.get("flightNumber")
        try:
            dep = datetime.fromisoformat(f["departureUTCDateTime"])
            arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
            bad = arr <= dep
        except (KeyError, ValueError, TypeError):
            # Форму (наивный ISO без смещения) держит pattern канонической
            # схемы; TypeError — смешение наивного и зонного значений, его
            # тоже назовёт схема.
            continue
        if bad:
            errors.append(f"рейс {fn}: прилёт не позже вылета "
                          f"({f['departureUTCDateTime']} → "
                          f"{f['arrivalUTCDateTime']})")
        else:
            dur = (arr - dep).total_seconds() / 3600
            if dur > 20:
                warnings.append(f"рейс {fn}: длительность {dur:.1f} ч — "
                                f"проверьте часовые пояса")
            elif dur < 10 / 60:
                warnings.append(f"рейс {fn}: длительность меньше 10 минут")
        if f.get("departureAirport") == f.get("arrivalAirport"):
            warnings.append(f"рейс {fn}: вылет и прилёт в одном аэропорту")
        key = f"{fn}|{f.get('departureUTCDateTime')}"
        seen[key] = seen.get(key, 0) + 1
    dups = [k for k, n in seen.items() if n > 1]
    if dups:
        errors.append(f"дубликаты рейсов (номер+время вылета): "
                      f"{', '.join(d.split('|')[0] for d in dups[:10])}")
    if len(errors) > 30:
        errors = errors[:30] + [f"… и ещё {len(errors) - 30}"]
    if len(warnings) > 30:
        warnings = warnings[:30] + [f"… и ещё {len(warnings) - 30}"]
    return {"errors": errors, "warnings": warnings}


# ------------------------------------------------------------ pending store

_pending_lock = threading.Lock()
_pending: dict[str, dict] = {}


def _prune_locked(now: float) -> None:
    for tok in [t for t, p in _pending.items()
                if now - p["created"] > PENDING_TTL_S]:
        del _pending[tok]


def stash_pending(entry: dict) -> str:
    token = secrets.token_urlsafe(16)
    with _pending_lock:
        _prune_locked(time.time())
        entry["created"] = time.time()
        _pending[token] = entry
    return token


def pop_pending(token: str, email: str) -> dict | None:
    """Commit is per-uploader: the pending entry is bound to the analyzing
    user so one user can never commit another's import."""
    with _pending_lock:
        _prune_locked(time.time())
        entry = _pending.get(token)
        if entry is None or entry.get("email") != email:
            return None
        return _pending.pop(token)


# ----------------------------------------------------------------- analyze

def decode_files(items: list[dict]) -> list[dict]:
    """Wire format -> parsed files. Each item: {name, text | content_b64}."""
    total = 0
    files = []
    for it in items:
        name = str(it.get("name") or "file")
        if isinstance(it.get("text"), str):
            raw = it["text"].encode("utf-8")
        elif isinstance(it.get("content_b64"), str):
            try:
                raw = base64.b64decode(it["content_b64"], validate=True)
            except Exception as exc:                # noqa: BLE001
                raise ImportError_(f"{name}: не удалось раскодировать "
                                   f"содержимое") from exc
        else:
            raise ImportError_(f"{name}: нет содержимого (text/content_b64)")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ImportError_("суммарный размер файлов больше 30 МБ")
        parsed = parse_file(name, raw)
        # keep the exact source bytes: the commit step writes them into the
        # immutable raw zone (data/raw/<inst>/ + SHA-256 manifest, Ф3)
        parsed["raw"] = raw
        files.append(parsed)
    if not files:
        raise ImportError_("не передано ни одного файла")
    return files


def analyze(items: list[dict], scope: str, email: str,
            backend_request: str | None = None) -> dict:
    """Full ANALYZE step -> pending token + everything the UI shows."""
    from . import registry
    files = decode_files(items)
    needs_ai = any(f["kind"] != "canonical" for f in files)
    if needs_ai:
        prop = propose_mapping(files, scope, backend_request)
        plan = prop["plan"]
    else:
        prop = {"backend": None, "model": None, "source": "canonical",
                "llm_error": None, "samples_masked": False}
        plan = {"files": [], "assignments_synthesis": None,
                "rationale": "файл уже в каноническом формате"}
    payload, warnings = build_payload(files, plan)
    validation = registry.validate_payload(payload)
    sem = semantic_checks(payload)
    from . import anomaly_report
    anomalies = anomaly_report.report(payload)
    token = stash_pending({"payload": payload, "scope": scope,
                           "email": email, "validation": validation,
                           "sem_errors": sem["errors"],
                           "raw_files": [{"name": f["name"],
                                          "data": f["raw"]}
                                         for f in files if "raw" in f]})
    preview = {
        "flights": (payload.get("flights") or [])[:PREVIEW_ROWS],
        "employees": (payload.get("employees") or [])[:PREVIEW_ROWS],
        "assignments": (payload.get("flightAssignments") or [])[:PREVIEW_ROWS],
    }
    stats = {
        "flights": len(payload.get("flights") or []),
        "employees": len(payload.get("employees") or []),
        "assignments": len(payload.get("flightAssignments") or []),
        "airports": len(payload.get("airports") or []),
    }
    return {
        "token": token, "plan": plan, "stats": stats, "preview": preview,
        "validation": validation, "semantic": sem, "warnings": warnings,
        "anomalies": anomalies,
        "scope": scope, "backend": prop["backend"], "model": prop["model"],
        "source": prop["source"], "llm_error": prop["llm_error"],
        "samples_masked": prop.get("samples_masked", False),
        "files": [{"name": f["name"], "kind": f["kind"],
                   "rows": len(f.get("rows") or [])} for f in files],
    }
