# -*- coding: utf-8 -*-
"""Сравнение попытки-повтора с попыткой-источником (система прогонов
COSMOS, проект 07.09.2026, раздел 17.3).

Два режима:
- «из журнала» — гейт байт в байт: сравниваются ВСЕ файлы снимков
  `attempts/<src>/` и `attempts/<new>/` (кроме `<out>/external-calls/**`
  — сам журнал, сравнивается счётчиками — и `replay/**`) по явному
  списку нормализации `NORMALIZE` (единственные поля времени); первое
  различие называется словами по самому раннему файлу конвейера;
- «заново» — таблица итогов (вердикты, результат, счётчики ВМП,
  цепочки, стоимость, покрытие, счёт), различие наборов цепочек
  (`pairing_diff`) и различия назначений ростера по сотрудникам
  (`roster_diff`); класс воспроизводимости объясняет законное различие.

В обоих режимах — блок `environment.differs`: отличия среды двух
попыток (код панели, каталог шаблонов, версии движков, адрес и сборка
решателя, сертификатор, реестр без `created`, каталог данных, ОС,
Python, класс). Отчёт — единственный файл `attempts/<new>/replay/report.json`;
сводка — в запись попытки. Исключение сравнения — вердикт
`not-comparable` словами, попытка завершается штатно.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "cosmos-replay-report/v1"
REPORT_DIR = "replay"
REPORT_NAME = "report.json"
#: Каталоги снимка, не входящие в гейт байт в байт.
SKIP_DIRS = ("external-calls", REPORT_DIR)

_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")

#: Явный список нормализации — единственные поля времени в артефактах
#: нажатия (проверено по коду: часы панели пишут только `_log` кнопки и
#: `derive_registry`). Появление новой метки времени в артефакте ломает
#: гейт, и список правится осознанно, а не расширяется молча.
NORMALIZE = (
    ("<out>/delivery.json", "journal[*].at", "time"),
    ("<out>/delivery.json", "journal[*].detail", "iso-substrings"),
    ("<out>/log.json", "[*].at", "time"),
    ("<out>/log.json", "[*].detail", "iso-substrings"),
    ("registry/vmp-executors-registry.json", "created", "time"),
    ("<out>/llm-exchanges/index.jsonl", "at", "time"),
    ("<out>/llm-exchanges/index.jsonl", "elapsed_ms", "zero"),
    # Расход модели в токенах (решение владельца № 1 от 10.09.2026) —
    # не артефакт расчёта, а потребление ресурса: повтор ИЗ ЖУРНАЛА
    # модель не зовёт и токенов не тратит, поэтому строка расхода у
    # записи и у повтора разная ПО ОПРЕДЕЛЕНИЮ — тот же класс, что `at`
    # и `elapsed_ms`. Гейт «байты те же» судит ответы модели (их
    # отпечатки в той же строке), а не счёт израсходованного.
    ("<out>/llm-exchanges/index.jsonl", "usage", "spent"),
)

#: Порядок конвейера: первым называется САМЫЙ РАННИЙ расходящийся файл.
_ORDER = (
    ("package.zip",), ("builder-payload.json",),
    ("source-rules.json", "conditions.json", "conditions-gaps.json",
     "history-pins.json", "builder-input.json", "execution-manifest.json",
     "vmp-rule-gaps.json"),
    ("builder-catalog.json", "pairing_builder_check_", "builder-catalog-",
     "select-", ".pending.json"),
    ("solver-status.json", "applied-rules.json", "solved.json"),
    ("solution.json",), ("certificate.json", "certificate.md"),
    ("delivery.json",), ("log.json",),
)


# ------------------------------------------------------------ помощники

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None


def _short(v, limit: int = 160) -> str:
    try:
        s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=repr)
    except Exception:                                      # noqa: BLE001
        s = repr(v)
    return s if len(s) <= limit else s[:limit] + "…"


def _order_key(rel: str) -> tuple:
    name = rel.rsplit("/", 1)[-1]
    if "/llm-exchanges/" in rel:
        return (len(_ORDER) + 1, rel)
    if rel.startswith("registry/"):
        return (len(_ORDER) + 2, rel)
    for i, names in enumerate(_ORDER):
        for pat in names:
            if name == pat or (pat.endswith(("-", "_")) and name.startswith(pat)) \
                    or (pat.startswith(".") and name.endswith(pat)):
                return (i, rel)
    return (len(_ORDER) + 3, rel)


def _out_of(rel: str) -> str | None:
    head = rel.split("/", 1)[0]
    return head if head.startswith("button") else None


def normalization_rules(rel: str) -> list[str]:
    """Правила списка NORMALIZE, применимые к файлу снимка."""
    out_name = _out_of(rel)
    got = []
    for pattern, field, _ in NORMALIZE:
        want = pattern.replace("<out>", out_name) if out_name else pattern
        if pattern.startswith("<out>") and not out_name:
            continue
        if rel == want:
            got.append("%s: %s" % (rel, field))
    return got


_CREATED_RE = re.compile(r'("created"\s*:\s*")(?:[^"\\]|\\.)*(")')

#: Форматы JSON-писателей панели, среди которых узнаётся формат файла:
#: `_deliver`/`_log` кнопки — indent=1; прочие — на случай смены писателя.
_JSON_FORMATS = ({"indent": 1}, {"indent": 2}, {"indent": 4}, {"indent": None},
                 {"indent": None, "separators": (",", ":")})


def _dump(doc, fmt: dict) -> bytes:
    text = json.dumps(doc, ensure_ascii=False,
                      **{k: v for k, v in fmt.items() if k in ("indent", "separators")})
    if fmt.get("crlf"):
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8") + fmt.get("suffix", b"")


def _json_format_of(doc, data: bytes) -> dict | None:
    """Формат, которым записан файл (отступ, разделители, перевод строки,
    хвост): единственный, что воспроизводит исходные байты из
    разобранного документа; ни один не воспроизводит — None (формат
    неизвестен, файл сравнивается байт в байт, нормализация не
    применяется)."""
    body = data.rstrip(b"\r\n")
    suffix = data[len(body):]
    crlf = b"\r\n" in body
    for base in _JSON_FORMATS:
        fmt = dict(base, crlf=crlf, suffix=suffix)
        if _dump(doc, fmt) == data:
            return fmt
    return None


def _normalized_json(data: bytes, mutate) -> bytes | None:
    """Разобрать файл, узнать формат писателя, подменить поля времени
    функцией mutate(doc) и записать обратно ТЕМ ЖЕ форматом: после подмены
    файл остаётся теми же байтами. Порядок ключей и отступы не
    канонизируются — их смена означает смену кода выдачи и обязана быть
    видна гейту (17.3: «всё остальное — байты как есть»)."""
    doc = json.loads(data.decode("utf-8"))
    fmt = _json_format_of(doc, data)
    if fmt is None:
        return None
    mutate(doc)
    return _dump(doc, fmt)


def normalize(rel: str, data: bytes) -> bytes | None:
    """Нормализованное содержимое по списку NORMALIZE либо None (файл
    сравнивается байт в байт). Подменяются РОВНО перечисленные поля
    времени, сериализация — в формате писателя каждого файла (не
    канонический JSON): перестановка ключей или другой отступ у писателя
    остаются различием (ревью реализации 07.09)."""
    if not normalization_rules(rel):
        return None
    name = rel.rsplit("/", 1)[-1]

    def _entries(entries) -> None:
        for e in entries:
            if isinstance(e, dict):
                if "at" in e:
                    e["at"] = "<t>"
                if isinstance(e.get("detail"), str):
                    e["detail"] = _ISO_RE.sub("<t>", e["detail"])
    try:
        if name == "delivery.json":
            return _normalized_json(data, lambda doc: _entries(
                (doc.get("journal") or []) if isinstance(doc, dict) else []))
        if name == "log.json":
            return _normalized_json(data, lambda doc: _entries(
                doc if isinstance(doc, list) else []))
        if name == "vmp-executors-registry.json":
            # Значение `created` подменяется в сыром тексте (первое
            # вхождение — верхний уровень реестра, registry_derive пишет его
            # первым ключом); файл не перечитывается и не пересобирается.
            doc = json.loads(data.decode("utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("created"), str):
                text = data.decode("utf-8")
                return _CREATED_RE.sub(r"\1<t>\2", text, count=1).encode("utf-8")
            return data
        if name == "index.jsonl":
            # Формат писателя индекса обменов (Recorder._index): строка —
            # json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n";
            # строка другого формата — файл не нормализуется (байт в байт).
            out: list[str] = []
            for line in data.decode("utf-8").split("\n"):
                if not line:
                    continue
                row = json.loads(line)
                if json.dumps(row, ensure_ascii=False, sort_keys=True) != line:
                    return None
                if isinstance(row, dict):
                    if "at" in row:
                        row["at"] = "<t>"
                    if "elapsed_ms" in row:
                        row["elapsed_ms"] = 0
                    if "usage" in row:
                        row["usage"] = "<расход>"
                out.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
            return ("\n".join(out) + "\n").encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return None


def _walk(root: Path) -> dict[str, Path]:
    root = Path(root)
    got: dict[str, Path] = {}
    if not root.is_dir():
        return got
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] == REPORT_DIR or any(x == "external-calls" for x in parts[:-1]):
            continue
        got[rel] = p
    return got


def _first_json_difference(a, b, path: str = "$") -> dict | None:
    from vmp.run.call_journal import first_json_difference
    return first_json_difference(a, b, path)


def _is_text(rel: str) -> bool:
    return rel.rsplit(".", 1)[-1].lower() in ("md", "txt", "jsonl", "log",
                                              "json", "csv")


def first_difference(rel: str, a: bytes, b: bytes) -> dict:
    """Первое различие словами: JSON — путь при обходе с сортировкой
    ключей; текст — номер строки и обе строки; двоичные — смещение и
    24 байта контекста в hex."""
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".json"):
        try:
            da = json.loads(a.decode("utf-8"))
            db = json.loads(b.decode("utf-8"))
            got = _first_json_difference(da, db)
            if got:
                return {"file": rel, "reason_code": "json-differs",
                        "path": got["path"], "source": got["source"],
                        "replay": got["replay"]}
            return {"file": rel, "reason_code": "json-differs", "path": "$",
                    "source": "<равны как JSON, различие в байтах>",
                    "replay": "<равны как JSON, различие в байтах>"}
        except (ValueError, UnicodeDecodeError):
            pass
    if _is_text(rel):
        try:
            la = a.decode("utf-8").splitlines()
            lb = b.decode("utf-8").splitlines()
            for i in range(max(len(la), len(lb))):
                sa = la[i] if i < len(la) else "<строки нет у источника>"
                sb = lb[i] if i < len(lb) else "<строки нет у повтора>"
                if sa != sb:
                    return {"file": rel, "reason_code": "text-differs",
                            "line": i + 1, "source": sa[:200],
                            "replay": sb[:200]}
            return {"file": rel, "reason_code": "text-differs", "line": None,
                    "source": "<строки равны, различие в переводах строк>",
                    "replay": "<строки равны, различие в переводах строк>"}
        except UnicodeDecodeError:
            pass
    n = min(len(a), len(b))
    off = next((i for i in range(n) if a[i] != b[i]), n)
    return {"file": rel, "reason_code": "bytes-differ", "offset": off,
            "source": a[off:off + 24].hex(), "replay": b[off:off + 24].hex()}


def compare_snapshots(src_root: Path, new_root: Path) -> dict:
    """Гейт байт в байт по двум снимкам: {"compared", "same",
    "normalized_same", "different", "first_difference", "normalization"}."""
    src = _walk(src_root)
    new = _walk(new_root)
    names = sorted(set(src) | set(new), key=_order_key)
    same = 0
    normalized: list[str] = []
    different: list[dict] = []
    rules: list[str] = []
    for rel in names:
        if rel not in src:
            different.append({"file": rel, "reason_code": "only-in-replay",
                              "first_difference": {"file": rel,
                                                   "reason_code": "only-in-replay"}})
            continue
        if rel not in new:
            different.append({"file": rel, "reason_code": "only-in-source",
                              "first_difference": {"file": rel,
                                                   "reason_code": "only-in-source"}})
            continue
        a = src[rel].read_bytes()
        b = new[rel].read_bytes()
        if a == b:
            same += 1
            continue
        na, nb = normalize(rel, a), normalize(rel, b)
        if na is not None and nb is not None and na == nb:
            normalized.append(rel)
            rules.extend(normalization_rules(rel))
            continue
        if na is not None and nb is not None:
            fd = first_difference(rel, na, nb)
        else:
            fd = first_difference(rel, a, b)
        different.append({"file": rel, "reason_code": fd["reason_code"],
                          "first_difference": fd})
    return {"compared": len(names), "same": same,
            "normalized_same": normalized, "different": different,
            "first_difference": (different[0]["first_difference"]
                                 if different else None),
            "normalization": sorted(set(rules))}


# ------------------------------------------------------------ итоги «заново»

def _score_parts(score) -> tuple:
    if not isinstance(score, str):
        return (None, None)
    m = re.match(r"^\s*(-?\d+)hard/(-?\d+)soft", score)
    if not m:
        return (score, None)
    return (int(m.group(1)), int(m.group(2)))


def _row(name: str, source_file: str, a, b, *, present_a: bool, present_b: bool,
         legit: bool = False) -> dict:
    row: dict = {"name": name, "source": a, "replay": b,
                 "source_file": source_file}
    if not present_a or not present_b:
        row["same"] = None
        row["note_code"] = "artifact-missing"
    else:
        row["same"] = (a == b)
    if legit:
        row["legit_difference"] = True
    return row


def _len_or(v):
    if isinstance(v, list):
        return len(v)
    return v


def _check_report(out: Path) -> dict | None:
    files = sorted(Path(out).glob("pairing_builder_check_*.json"))
    if not files:
        cat = _read_json(Path(out) / "builder-catalog.json")
        if isinstance(cat, dict) and isinstance(cat.get("builder_report"), dict):
            return cat["builder_report"]
        return None
    return _read_json(files[-1])


def outcomes_table(kind: str, src_out: Path, new_out: Path,
                   src_rec: dict, new_rec: dict) -> list[dict]:
    """Таблица итогов «заново»: каждая строка названа файлом-источником;
    отсутствие файла у одной из попыток — `artifact-missing`."""
    rows: list[dict] = []
    da, db = _read_json(src_out / "delivery.json"), _read_json(new_out / "delivery.json")
    pa, pb = isinstance(da, dict), isinstance(db, dict)
    da, db = da if pa else {}, db if pb else {}

    def dv(name, fn, legit=False):
        rows.append(_row(name, "delivery.json", fn(da), fn(db),
                         present_a=pa, present_b=pb, legit=legit))

    dv("verdict", lambda d: d.get("verdict"))
    dv("mode", lambda d: d.get("mode"))
    dv("result.feasibility", lambda d: (d.get("result") or {}).get("feasibility"))
    dv("result.optimality", lambda d: (d.get("result") or {}).get("optimality"))
    dv("result.coverage_complete",
       lambda d: (d.get("result") or {}).get("coverage_complete"))
    dv("certified", lambda d: d.get("certified"))
    dv("findings", lambda d: d.get("findings"))
    dv("uncovered", lambda d: _len_or(d.get("uncovered")))
    dv("certificate_gate", lambda d: _len_or(d.get("certificate_gate")))
    dv("solver_run_id", lambda d: (d.get("schedule") or {}).get("solver_run_id"),
       legit=True)
    ca, cb = _read_json(src_out / "certificate.json"), _read_json(new_out / "certificate.json")
    qa, qb = isinstance(ca, dict), isinstance(cb, dict)
    ca = (ca.get("certificate") or {}) if qa else {}
    cb = (cb.get("certificate") or {}) if qb else {}
    for key in ("counters", "rules_partially_covered"):
        rows.append(_row("certificate." + key, "certificate.json",
                         ca.get(key), cb.get(key), present_a=qa, present_b=qb))
    for key in ("expected", "evaluated", "unknown"):
        rows.append(_row("certificate.window_coverage." + key, "certificate.json",
                         (ca.get("window_coverage") or {}).get(key),
                         (cb.get("window_coverage") or {}).get(key),
                         present_a=qa, present_b=qb))
    if kind == "pairing":
        dv("chains", lambda d: d.get("chains"))
        dv("coverage_verdict", lambda d: d.get("coverage_verdict"))
        dv("coverage.complete", lambda d: (d.get("coverage") or {}).get("complete"))
        dv("proven_impossible_legs", lambda d: d.get("proven_impossible_legs"))
        dv("catalog_sha256", lambda d: d.get("catalog_sha256"))
        ka, kb = _read_json(src_out / "builder-catalog.json"), _read_json(new_out / "builder-catalog.json")
        xa, xb = isinstance(ka, dict), isinstance(kb, dict)
        ka, kb = ka if xa else {}, kb if xb else {}
        for key in ("ip_status", "ip_objective", "solver_status"):
            rows.append(_row(key, "builder-catalog.json", ka.get(key), kb.get(key),
                             present_a=xa, present_b=xb))
        rows.append(_row("selected", "builder-catalog.json",
                         len(ka.get("selected") or []), len(kb.get("selected") or []),
                         present_a=xa, present_b=xb))
        ra, rb = _check_report(src_out), _check_report(new_out)
        for key in ("total_cost", "person_nights"):
            rows.append(_row(key, "pairing_builder_check_*.json",
                             (ra or {}).get(key), (rb or {}).get(key),
                             present_a=isinstance(ra, dict),
                             present_b=isinstance(rb, dict)))
    else:
        sa = _score_parts((da.get("schedule") or {}).get("score"))
        sb = _score_parts((db.get("schedule") or {}).get("score"))
        rows.append(_row("score.hard", "delivery.json", sa[0], sb[0],
                         present_a=pa, present_b=pb))
        rows.append(_row("score.soft", "delivery.json", sa[1], sb[1],
                         present_a=pa, present_b=pb))
        dv("solver_hard", lambda d: d.get("solver_hard"))
        dv("solver_hard_breakdown",
           lambda d: sorted((d.get("solver_hard_breakdown") or {}).keys())
           if isinstance(d.get("solver_hard_breakdown"), dict) else None)
        dv("cost_assessment", lambda d: d.get("cost_assessment"))
        dv("cost_certainty", lambda d: d.get("cost_certainty"))
        dv("counted_share", lambda d: d.get("counted_share"))
        dv("deadheads.used", lambda d: (d.get("deadheads") or {}).get("used")
           if isinstance(d.get("deadheads"), dict) else None)
        dv("pairing_run_id", lambda d: d.get("pairing_run_id"))
        dv("pairing_chains", lambda d: d.get("pairing_chains"))
    # ИДАК — отдельное действие; у повтора её нет.
    sc = (src_rec or {}).get("source_check")
    rows.append({"name": "idak.counts", "source_file": "запись попытки",
                 "source": (sc or {}).get("counts") if isinstance(sc, dict) else None,
                 "replay": None, "same": None, "note_code": "not-run-for-replay"})
    return rows


def pairing_diff(src_out: Path, new_out: Path) -> dict | None:
    ka, kb = _read_json(src_out / "builder-catalog.json"), _read_json(new_out / "builder-catalog.json")
    if not isinstance(ka, dict) or not isinstance(kb, dict):
        return None

    def chains(cat):
        got = []
        for ch in cat.get("selected") or []:
            if isinstance(ch, dict):
                legs = ch.get("fns") or ch.get("active_fns") or []
                got.append(tuple(str(x) for x in legs))
        return got
    a, b = chains(ka), chains(kb)
    sa, sb = set(a), set(b)
    ra, rb = _check_report(src_out), _check_report(new_out)
    ca = (ra or {}).get("total_cost") if isinstance(ra, dict) else None
    cb = (rb or {}).get("total_cost") if isinstance(rb, dict) else None
    delta = (cb - ca) if isinstance(ca, (int, float)) and isinstance(cb, (int, float)) \
        and not isinstance(ca, bool) and not isinstance(cb, bool) else None
    return {"source_chains": len(a), "replay_chains": len(b),
            "common": len(sa & sb), "only_in_source": len(sa - sb),
            "only_in_replay": len(sb - sa),
            "only_in_source_list": [list(x) for x in sorted(sa - sb)][:50],
            "only_in_replay_list": [list(x) for x in sorted(sb - sa)][:50],
            "total_cost": {"source": ca, "replay": cb, "delta": delta}}


def _employee_of(row) -> str | None:
    if isinstance(row, dict):
        for k in ("employee", "id", "Табельный номер", "employee_id"):
            if row.get(k) not in (None, ""):
                return str(row[k])
        return None
    if isinstance(row, str):
        return row
    return None


def _assignments(solution) -> dict[str, set]:
    got: dict[str, set] = {}
    for a in (solution.get("assignments") or []) if isinstance(solution, dict) else []:
        if not isinstance(a, dict):
            continue
        emp = _employee_of(a)
        if emp is None:
            continue
        got.setdefault(emp, set()).add((str(a.get("flight")), str(a.get("role") or "")))
    return got


def _window(solution) -> set:
    got = set()
    for row in (solution.get("window_employees") or []) if isinstance(solution, dict) else []:
        emp = _employee_of(row)
        if emp is not None:
            got.add(emp)
    return got


def _seats(solved) -> dict[str, dict]:
    got: dict[str, dict] = {}
    for i, fa in enumerate((solved.get("flightAssignments") or [])
                           if isinstance(solved, dict) else []):
        if not isinstance(fa, dict):
            continue
        key = fa.get("crewSlotId") or fa.get("id")
        key = str(key) if key not in (None, "") else "#%d" % i
        got[key] = fa
    return got


def _chains_per_employee(solved, solution) -> dict[str, int]:
    got: dict[str, set] = {}
    for fa in (solved.get("flightAssignments") or []) if isinstance(solved, dict) else []:
        if isinstance(fa, dict) and fa.get("employee") and fa.get("chainId"):
            got.setdefault(str(fa["employee"]), set()).add(str(fa["chainId"]))
    if not got and isinstance(solution, dict):
        for p in solution.get("pairings") or []:
            if isinstance(p, dict) and p.get("employee"):
                got.setdefault(str(p["employee"]), set()).add(str(p.get("id")))
    return {k: len(v) for k, v in got.items()}


def roster_diff(src_out: Path, new_out: Path) -> dict | None:
    """Различия назначений ростера по сотрудникам (алгоритм 17.3)."""
    sa, sb = _read_json(src_out / "solution.json"), _read_json(new_out / "solution.json")
    if not isinstance(sa, dict) or not isinstance(sb, dict):
        return None
    va, vb = _read_json(src_out / "solved.json"), _read_json(new_out / "solved.json")
    aa, ab = _assignments(sa), _assignments(sb)
    employees = sorted(set(aa) | set(ab) | _window(sa) | _window(sb))
    ca, cb = _chains_per_employee(va, sa), _chains_per_employee(vb, sb)
    per: list[dict] = []
    unchanged = changed = lost_total = gained_total = 0
    only_src = only_new = 0
    for e in employees:
        in_a, in_b = e in aa, e in ab
        a, b = aa.get(e, set()), ab.get(e, set())
        row = {"employee": e, "chains": {"source": ca.get(e, 0),
                                         "replay": cb.get(e, 0)}}
        if in_a and not in_b and a:
            row["status"] = "only-in-source"
            row["lost"] = len(a)
            row["gained"] = 0
            only_src += 1
        elif in_b and not in_a and b:
            row["status"] = "only-in-replay"
            row["lost"] = 0
            row["gained"] = len(b)
            only_new += 1
        elif a == b:
            row["status"] = "unchanged"
            row["lost"] = row["gained"] = 0
            unchanged += 1
            per.append(row)
            continue
        else:
            row["status"] = "changed"
            row["lost"] = len(a - b)
            row["gained"] = len(b - a)
        changed += 1
        lost_total += row["lost"]
        gained_total += row["gained"]
        row["lost_list"] = sorted(list(x) for x in (a - b))[:20]
        row["gained_list"] = sorted(list(x) for x in (b - a))[:20]
        per.append(row)
    ma, mb = _seats(va), _seats(vb)
    seats = {"total": len(set(ma) | set(mb)), "same_employee": 0, "other_employee": 0,
             "filled_now": 0, "emptied_now": 0, "deadhead_changed": 0,
             "unassigned_before": 0, "unassigned_after": 0}
    for key in set(ma) | set(mb):
        fa, fb = ma.get(key) or {}, mb.get(key) or {}
        ea, eb = fa.get("employee") or None, fb.get("employee") or None
        if ea is None:
            seats["unassigned_before"] += 1
        if eb is None:
            seats["unassigned_after"] += 1
        if ea is not None and eb is not None:
            if str(ea) == str(eb):
                seats["same_employee"] += 1
            else:
                seats["other_employee"] += 1
        elif ea is None and eb is not None:
            seats["filled_now"] += 1
        elif ea is not None and eb is None:
            seats["emptied_now"] += 1
        if bool(fa.get("deadhead")) != bool(fb.get("deadhead")):
            seats["deadhead_changed"] += 1
    per.sort(key=lambda r: (-(r.get("lost", 0) + r.get("gained", 0)), r["employee"]))
    words = ("сотрудников %d: без изменений %d, изменилось %d (потеряно %d "
             "рейсо-мест, добавлено %d); мест %d: тот же сотрудник %d, другой %d, "
             "незанятых %d → %d"
             % (len(employees), unchanged, changed, lost_total, gained_total,
                seats["total"], seats["same_employee"], seats["other_employee"],
                seats["unassigned_before"], seats["unassigned_after"]))
    return {"employees_total": len(employees), "unchanged": unchanged,
            "changed": changed, "lost": lost_total, "gained": gained_total,
            "only_in_source": only_src, "only_in_replay": only_new,
            "seats": seats, "words": words, "employees": per}


# ------------------------------------------------------------ среда

_ENV_KEYS = ("code.commit", "code.tree_dirty", "template_catalog.version",
             "template_catalog.fingerprint", "engine_versions", "solver.url",
             "solver.build", "certifier.model", "certifier.channel",
             "certifier.provider", "registry.sha256", "panel.data_root",
             "panel.platform", "panel.python", "reproducibility.class")


def _env_view(rec: dict, manifest: dict | None) -> dict:
    rec = rec or {}
    versions = (manifest or {}).get("versions") or {}
    solver_v = versions.get("solver") if isinstance(versions.get("solver"), dict) else {}
    panel = versions.get("panel") if isinstance(versions.get("panel"), dict) else {}
    code = rec.get("code") or {}
    cat = rec.get("template_catalog") or {}
    cert = rec.get("certifier") or {}
    solver = rec.get("solver") or {}
    reg = rec.get("registry") or {}
    build = solver_v.get("build")
    return {
        "code.commit": code.get("commit"),
        "code.tree_dirty": code.get("tree_dirty"),
        "template_catalog.version": cat.get("version"),
        "template_catalog.fingerprint": cat.get("fingerprint"),
        "engine_versions": rec.get("engine_versions"),
        "solver.url": solver.get("url") or solver_v.get("url"),
        "solver.build": build if build else None,
        "solver.build_code": ("build-files" if build else "no-build-files"),
        "certifier.model": cert.get("model"),
        "certifier.channel": cert.get("channel"),
        "certifier.provider": cert.get("provider"),
        "registry.sha256": reg.get("sha256"),
        "panel.data_root": panel.get("data_root"),
        "panel.platform": panel.get("platform"),
        "panel.python": panel.get("python"),
        "reproducibility.class": (rec.get("reproducibility") or {}).get("class"),
    }


def environment_block(src_rec: dict, new_rec: dict, src_manifest: dict | None,
                      new_manifest: dict | None) -> dict:
    a, b = _env_view(src_rec, src_manifest), _env_view(new_rec, new_manifest)
    differs = [k for k in _ENV_KEYS if a.get(k) != b.get(k)]
    notes = []
    if a.get("solver.build") is None and b.get("solver.build") is None:
        notes.append("сборка решателя неизвестна (no-build-files): отчёт не "
                     "может утверждать, что решатель не менялся")
    return {"source": a, "replay": b, "differs": differs, "notes": notes}


# ------------------------------------------------------------ отчёт

def attempt_kind(record: dict) -> str:
    if (record or {}).get("task") == "pairing":
        return "pairing"
    if (record or {}).get("pairing_selection") or str(
            (record or {}).get("catalog") or "").endswith("button-on-pairing"):
        return "rostering_on_pairing"
    return "rostering"


def out_name_of(record: dict) -> str:
    cat = str((record or {}).get("catalog") or "")
    tail = cat.rsplit("/", 1)[-1]
    if tail.startswith("button"):
        return tail
    kind = attempt_kind(record)
    return {"pairing": "button-pairing", "rostering": "button",
            "rostering_on_pairing": "button-on-pairing"}[kind]


def _explanation(src_rec: dict) -> tuple[str, str]:
    cls = ((src_rec or {}).get("reproducibility") or {}).get("class") or "wall-clock"
    if cls == "deterministic":
        return cls, "deterministic-expected-same"
    return cls, "wall-clock-legit-difference"


def summary_of(report: dict) -> dict:
    """Сводка отчёта для записи попытки (`replay.of.report`)."""
    files = report.get("files") or {}
    return {"verdict": report.get("verdict"), "words": report.get("words"),
            "first_difference": report.get("first_difference"),
            "files_compared": files.get("compared"),
            "outcomes_differ": [r["name"] for r in (report.get("outcomes") or [])
                                if r.get("same") is False and not r.get("legit_difference")],
            "environment_differs": list((report.get("environment") or {})
                                        .get("differs") or [])}


def run(mode: str, wd: Path, source_attempt: str, attempt_id: str,
        source_record: dict, new_record: dict, *, run: str | None = None,
        journal_dirs: tuple[Path | None, Path | None] | None = None) -> dict:
    """Сравнение и отчёт `attempts/<new>/replay/report.json`; возвращает
    отчёт целиком (сводка — summary_of)."""
    from vmp.run import call_journal as cj
    wd = Path(wd)
    src_root = wd / "attempts" / source_attempt
    new_root = wd / "attempts" / attempt_id
    out_name = out_name_of(source_record)
    src_out, new_out = src_root / out_name, new_root / out_name
    kind = attempt_kind(source_record)
    src_j = journal_dirs[0] if journal_dirs else src_out / cj.DIR_NAME
    new_j = journal_dirs[1] if journal_dirs else new_out / cj.DIR_NAME
    src_man = cj.read_manifest(src_j) if src_j else None
    new_man = cj.read_manifest(new_j) if new_j else None
    report: dict = {"schema": SCHEMA, "mode": mode, "run": run,
                    "source_attempt": source_attempt, "attempt": attempt_id,
                    "task": (source_record or {}).get("task"), "kind": kind,
                    "at": cj.now_iso(), "verdict": None, "words": None,
                    "first_difference": None, "files": None, "outcomes": None,
                    "pairing_diff": None, "roster_diff": None,
                    "reproducibility": None, "journal": None, "idak": None,
                    "environment": None, "normalization": []}
    try:
        cls, code = _explanation(source_record)
        report["reproducibility"] = {"class": cls, "explanation_code": code,
                                     "deterministic_gate": None}
        report["environment"] = environment_block(source_record, new_record,
                                                  src_man, new_man)
        src_counts = (src_man or {}).get("counts") or {}
        recorded = (sum(int(src_counts.get(k) or 0)
                        for k in ("solver", "pairing_builder", "llm"))
                    if src_man else None)
        new_counts = (new_man or {}).get("counts") or {}
        report["journal"] = {
            "recorded": recorded,
            "served": new_counts.get("served_from_journal") if new_man else None,
            "unconsumed": new_counts.get("unconsumed") if new_man else None,
            "served_from": (new_man or {}).get("served_from"),
            "mismatch": ((new_record or {}).get("journal") or {}).get("mismatch")}
        sc = (source_record or {}).get("source_check")
        report["idak"] = {"source": sc if isinstance(sc, dict) else None,
                          "replay": None, "note_code": "not-run-for-replay"}
        if mode == "journal":
            files = compare_snapshots(src_root, new_root)
            report["files"] = {"compared": files["compared"], "same": files["same"],
                               "normalized_same": files["normalized_same"],
                               "different": files["different"]}
            report["normalization"] = files["normalization"]
            report["first_difference"] = files["first_difference"]
            if files["different"]:
                fd = files["first_difference"]
                where = fd.get("path") or (
                    "строка %s" % fd.get("line") if fd.get("line") is not None
                    else ("смещение %s" % fd.get("offset")
                          if fd.get("offset") is not None else fd.get("reason_code")))
                words = "расходится: %s — %s" % (fd.get("file"), where)
                if fd.get("source") is not None and fd.get("reason_code") not in (
                        "only-in-source", "only-in-replay"):
                    words += ": %s → %s" % (_short(fd.get("source"), 120),
                                            _short(fd.get("replay"), 120))
                elif fd.get("reason_code") in ("only-in-source", "only-in-replay"):
                    words += (" (файл только у источника)"
                              if fd["reason_code"] == "only-in-source"
                              else " (файл только у повтора)")
                env = report["environment"]["differs"]
                if env:
                    words += "; среда: " + ", ".join(env)
                report["verdict"] = "differs"
                report["words"] = words
            else:
                report["verdict"] = "same"
                report["words"] = ("совпадает: %d файлов байт в байт, %d — после "
                                   "нормализации полей времени"
                                   % (files["same"], len(files["normalized_same"])))
        else:
            rows = outcomes_table(kind, src_out, new_out, source_record, new_record)
            report["outcomes"] = rows
            if kind == "pairing":
                report["pairing_diff"] = pairing_diff(src_out, new_out)
            else:
                report["roster_diff"] = roster_diff(src_out, new_out)
            differ = [r["name"] for r in rows
                      if r.get("same") is False and not r.get("legit_difference")]
            if cls == "deterministic":
                gate_files = []
                for name in ("solved.json", "builder-catalog.json"):
                    pa, pb = src_out / name, new_out / name
                    if pa.is_file() and pb.is_file():
                        gate_files.append({"file": "%s/%s" % (out_name, name),
                                           "same": pa.read_bytes() == pb.read_bytes()})
                bad = [g["file"] for g in gate_files if not g["same"]]
                report["reproducibility"]["deterministic_gate"] = {
                    "files": gate_files, "same": not bad,
                    "words": ("профиль Д1 не воспроизвёл решение: %s" % ", ".join(bad)
                              if bad else "профиль Д1: решение воспроизведено байт в байт")}
            if differ:
                report["verdict"] = "differs"
                words = "расходится по итогам: " + ", ".join(differ)
                if cls == "wall-clock":
                    words += (" — класс «стенные часы»: другой ростер и другой "
                              "каталог при той же постановке законны")
                rd = report.get("roster_diff")
                if isinstance(rd, dict):
                    words += "; " + rd["words"]
                report["words"] = words
            else:
                report["verdict"] = "same-outcome"
                report["words"] = ("итоги совпадают: строк %d, различий нет"
                                   % len([r for r in rows if r.get("same") is not None]))
    except Exception as e:                                 # noqa: BLE001
        report["verdict"] = "not-comparable"
        report["words"] = ("сравнение не проведено: %s: %s"
                           % (type(e).__name__, str(e)[:300]))
    try:
        dst = new_root / REPORT_DIR
        dst.mkdir(parents=True, exist_ok=True)
        (dst / REPORT_NAME).write_bytes(
            (json.dumps(report, ensure_ascii=False, indent=1, default=repr) + "\n")
            .encode("utf-8"))
    except OSError as e:
        report["words"] = (report.get("words") or "") + \
            "; отчёт не записан: %s" % str(e)[:200]
    return report
