# -*- coding: utf-8 -*-
"""Повтор прогона CDPM (проект повтора, этап Р2;
`final-report/CDPM-POVTOR-PROEKT-CLD.md`, разделы 2.7–2.10, 2.12, 2.15).

«Повтор из журнала» — доказательство: новый прогон-потомок с теми же
входами байт в байт, той же раскладкой ролей и признаком комплекта;
действия источника исполняются заново в том же порядке, ответы моделей
берутся из журнала внешних вызовов источника (модели не вызываются, сети
нет), детерминированные части считаются заново. Ожидание — артефакты
байт в байт после явной нормализации; расхождение называется словами и
первым расходящимся файлом по порядку конвейера. Источник повтор не
трогает — только указатель на потомков в его статусе.

Единица воспроизведения — прогон целиком (цепочка действий журнала
`journal/actions.jsonl`; у прогона, рождённого до журнала, цепочка
восстанавливается по артефактам и так и называется в отчёте).

«Повтор заново» (этап Р3) — измерение: те же входы и раскладка, все
внешние вызовы заново; сравнение по итогам (счётчики извлечения и
посадок, вопросы, вердикты цикла и арбитра, ответы, отчёт данных, SMT,
манифест пакета, эквивалентность ВМП тем же гейтом, что в обратной
задаче), а не по байтам: различие моделей у недетерминированных стадий
законно и объясняется словами, а не выдаётся за ошибку.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from cdpm import actions_journal as aj
from cdpm import call_journal as cj

SCHEMA = "cdpm-replay-report/v1"
ENV_DISABLED = "CDPM_REPLAY"
REPORT_REL = "journal/replay-report.json"
MODES = {"journal": "replay-journal", "fresh": "replay-fresh"}
#: Действия-стадии и линия статуса, освобождения которой ждёт водитель.
STAGE_LINES = {"extract": ("state",), "answers": ("state", "data_state"),
               "mode2": ("state", "data_state"), "exchange": ("state",),
               "full-cycle": ("state",), "arbiter": ("state",),
               "data-run": ("data_state", "state"),
               "data-review": ("data_state", "state"),
               "smt-verify": ("smt_state",),
               # Шаг 6 каталога шаблонов: побочная линия заявок.
               "catalog-request": ("catalog_state",)}
SKIP_ACTIONS = {"control": "control-not-replayed"}
#: Код строки `skipped` для действия РУБЕЖА непротиворечивости
#: (11.09.2026): проверка не игралась заново — вердикт взят из отчёта
#: источника, как его берёт сам рубеж у потомка повтора.
SMT_GATE_CODE = "smt-verdict-from-source"
#: Порядок конвейера для строк отчёта: самый ранний файл объясняет
#: все последующие.
ORDER = ("src", "freeze", "agents.json", "files-config.json", "data-src",
         "data-freeze", "extract", "exchange", "answers", "third-party",
         "mode2", "data-report", "data-review", "smt", "catalog-requests")
EXCLUDE_TOP = {"journal", "extract-cache", "channel"}
#: `run-record.json` — запись о прогоне (шаг 1 проекта, 10.09.2026):
#: телеметрия с моментами и временем машины, а не артефакт конвейера —
#: повтор судит артефакты и журнал, запись у потомка своя.
#: `declaration.json` — ГОЛОВА той же записи (шаг 2): объявление
#: прогона несёт момент объявления, номер прогона и пути площадки —
#: у потомка повтора она своя, как и запись.
EXCLUDE_FILES = {"status.json", "status.lock", "control.json",
                 "run-record.json", "declaration.json"}
EXCLUDE_DIRNAMES = {"audit-cache", "respond-cache"}
#: Явный список нормализации: единственные метки времени панели в
#: артефактах (скан run-0087, 79 файлов) — `at` в fit-journal обеих стадий;
#: книги xlsx несут время записи (docProps/core.xml и заголовки zip) —
#: сравниваются по членам архива с фиксированным временем.
NORMALIZE = {"extract/fit-journal.jsonl": ("jsonl", ("at",)),
             "exchange/fit-journal.jsonl": ("jsonl", ("at",)),
             "*.xlsx": ("xlsx", ())}
_XLSX_TIME = re.compile(r"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:)")


def _xlsx_canon(data: bytes) -> bytes:
    """Канон книги: члены архива по именам, время zip фиксировано, метки
    времени docProps/core.xml заменены на <t>; иной zip — как есть."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = {name: z.read(name) for name in z.namelist()}
    except zipfile.BadZipFile:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for name in sorted(members):
            body = members[name]
            if name == "docProps/core.xml":
                body = _XLSX_TIME.sub(r"\1<t>\2", body.decode("utf-8")).encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            z.writestr(info, body)
    return out.getvalue()


def _rule_for(rel: str):
    rule = NORMALIZE.get(rel)
    if rule:
        return rule
    if rel.endswith(".xlsx"):
        return NORMALIZE["*.xlsx"]
    return None
STATUS_KEYS = ("state", "data_state", "smt_state", "exchange", "layout",
               "files", "customer", "data_files")
STATUS_SUFFIXES = ("_counters", "_summary", "_problems")
POLL_S = 0.5
MIN_WAIT_S = 600.0


class ReplayRefused(RuntimeError):
    """Повтор невозможен — код причины (2.10) и слова."""

    def __init__(self, code: str, words: str):
        super().__init__(words)
        self.code = code


# ------------------------------------------------------------ помощники

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _read_json(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def disabled_by_site() -> bool:
    return (os.environ.get(ENV_DISABLED) or "").strip() == "0"


def report_path(run_dir) -> Path:
    return Path(run_dir) / REPORT_REL


def replay_of(status: dict | None) -> dict | None:
    """Паспорт повтора у потомка (None у первичного прогона)."""
    born = (status or {}).get("passport")
    if not isinstance(born, dict):
        return None
    rep = born.get("replay")
    return rep if isinstance(rep, dict) else None


def source_dir_of(child_dir, status: dict | None) -> Path | None:
    """Каталог прогона-ИСТОЧНИКА у потомка повтора (None — прогон
    первичный). Правило одно у водителя повтора и у гейта выпуска
    пакета (`export_task_package.consistency_of`): путь из паспорта, а
    если его нет — соседний каталог по имени источника в корне прогонов
    (корень — родитель каталога потомка: `webui._run_dir` собирает путь
    как `runs_root()/<прогон>`)."""
    rep = replay_of(status)
    if rep is None:
        return None
    return Path(rep.get("source_dir")
                or (Path(child_dir).parent / (rep.get("source_run") or "")))


# ------------------------------------------------------------ артефакты

def _rank(rel: str) -> tuple:
    head = rel.split("/", 1)[0]
    try:
        return (ORDER.index(head), rel)
    except ValueError:
        return (len(ORDER), rel)


def artifacts(run_dir) -> list[str]:
    """Относительные пути артефактов прогона в порядке конвейера: без
    журналов, кэшей, канала и служебных файлов статуса."""
    root = Path(run_dir)
    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] in EXCLUDE_TOP or rel in EXCLUDE_FILES:
            continue
        if any(part in EXCLUDE_DIRNAMES for part in parts[:-1]):
            continue
        if rel.endswith(".tmp"):
            continue
        out.append(rel)
    return sorted(out, key=_rank)


def normalized_bytes(run_dir, rel: str) -> tuple[bytes, bool]:
    data = (Path(run_dir) / rel).read_bytes()
    rule = _rule_for(rel)
    if not rule:
        return data, False
    kind, keys = rule
    if kind == "xlsx":
        return _xlsx_canon(data), True
    if kind == "jsonl":
        lines = []
        for line in data.decode("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                lines.append(line)
                continue
            if isinstance(obj, dict):
                for k in keys:
                    if k in obj:
                        obj[k] = "<t>"
            lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
        return ("\n".join(lines) + "\n").encode("utf-8"), True
    return data, False


def _json_first_diff(a, b, path="$") -> dict | None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return {"where": f"{path}.{k}", "source": None,
                        "replay": _short(b[k]), "note": "ключа нет у источника"}
            if k not in b:
                return {"where": f"{path}.{k}", "source": _short(a[k]),
                        "replay": None, "note": "ключа нет у повтора"}
            got = _json_first_diff(a[k], b[k], f"{path}.{k}")
            if got:
                return got
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a):
                return {"where": f"{path}[{i}]", "source": None,
                        "replay": _short(b[i]), "note": "элемента нет у источника"}
            if i >= len(b):
                return {"where": f"{path}[{i}]", "source": _short(a[i]),
                        "replay": None, "note": "элемента нет у повтора"}
            got = _json_first_diff(a[i], b[i], f"{path}[{i}]")
            if got:
                return got
        return None
    if a != b:
        return {"where": path, "source": _short(a), "replay": _short(b)}
    return None


def _short(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 300 else s[:300] + "…"


def first_difference(rel: str, a: bytes, b: bytes) -> dict:
    """Первое расхождение словами: JSON — путь, текст — строка, архив
    (xlsx, zip) — первый расходящийся член, двоичный — смещение."""
    if rel.endswith((".xlsx", ".zip")):
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(a)) as za, \
                    zipfile.ZipFile(io.BytesIO(b)) as zb:
                na, nb = set(za.namelist()), set(zb.namelist())
                for name in sorted(na | nb):
                    if name not in na or name not in nb:
                        return {"path": f"{rel}/{name}", "kind": "only-in-"
                                + ("replay" if name not in na else "source")}
                    x, y = za.read(name), zb.read(name)
                    if x != y:
                        got = first_difference(f"{rel}/{name}", x, y)
                        got["path"] = f"{rel}/{name}"
                        return got
        except zipfile.BadZipFile:
            pass
    if rel.endswith((".json", ".xml")):
        try:
            got = _json_first_diff(json.loads(a.decode("utf-8")),
                                   json.loads(b.decode("utf-8")))
            if got:
                return {"path": rel, "kind": "json", **got}
        except (ValueError, UnicodeDecodeError):
            pass
    try:
        ta, tb = a.decode("utf-8"), b.decode("utf-8")
    except UnicodeDecodeError:
        n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y),
                 min(len(a), len(b)))
        return {"path": rel, "kind": "binary", "offset": n,
                "source": a[n:n + 24].hex(), "replay": b[n:n + 24].hex()}
    la, lb = ta.splitlines(), tb.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else None
        y = lb[i] if i < len(lb) else None
        if x != y:
            return {"path": rel, "kind": "text", "line": i + 1,
                    "source": _short(x) if x is not None else None,
                    "replay": _short(y) if y is not None else None}
    return {"path": rel, "kind": "text", "line": None,
            "source": None, "replay": None,
            "note": "тексты равны построчно, различие в переводах строк"}


def status_view(status: dict) -> dict:
    out = {}
    for k, v in (status or {}).items():
        if k in STATUS_KEYS or any(k.endswith(s) for s in STATUS_SUFFIXES):
            out[k] = v
    return out


def compare(source_dir, child_dir) -> tuple[list[dict], dict | None]:
    """Гейт байт в байт: строки артефактов в порядке конвейера и первое
    расхождение (самый ранний файл по конвейеру)."""
    source_dir, child_dir = Path(source_dir), Path(child_dir)
    src, rep = artifacts(source_dir), artifacts(child_dir)
    rows: list[dict] = []
    first: dict | None = None
    for rel in sorted(set(src) | set(rep), key=_rank):
        if rel not in rep:
            rows.append({"path": rel, "same": False, "code": "only-in-source"})
            first = first or {"path": rel, "kind": "only-in-source"}
            continue
        if rel not in src:
            rows.append({"path": rel, "same": False, "code": "only-in-replay"})
            first = first or {"path": rel, "kind": "only-in-replay"}
            continue
        a, na = normalized_bytes(source_dir, rel)
        b, nb = normalized_bytes(child_dir, rel)
        same = a == b
        rows.append({"path": rel, "source_sha256": _sha(a),
                     "replay_sha256": _sha(b), "same": same,
                     "normalized": na or nb})
        if not same and first is None:
            first = first_difference(rel, a, b)
    # Статус — по выбранным ключам.
    try:
        sa = status_view(_read_json(source_dir / "status.json"))
        sb = status_view(_read_json(child_dir / "status.json"))
    except (OSError, ValueError):
        sa, sb = {}, {}
    for k in sorted(set(sa) | set(sb)):
        same = _canon(sa.get(k)) == _canon(sb.get(k))
        rows.append({"path": f"status:{k}", "same": same,
                     "source_sha256": _sha(_canon(sa.get(k))),
                     "replay_sha256": _sha(_canon(sb.get(k)))})
        if not same and first is None:
            got = _json_first_diff(sa.get(k), sb.get(k), f"$.{k}") or {}
            first = {"path": f"status:{k}", "kind": "json", **got}
    return rows, first


def package_rows(source_dir, child_dir) -> tuple[list[dict], dict | None]:
    """Пакет постановки: у потомка «из журнала» zip обязан совпасть с
    исходным (2.15). Источник выпускал пакет каналом — сверка с его
    байтами; иначе — свежая сборка обеих сторон."""
    from cdpm import export_task_package as etp
    source_dir, child_dir = Path(source_dir), Path(child_dir)
    rows: list[dict] = []
    first = None
    if not (source_dir / "extract" / "model.json").is_file():
        return rows, None
    try:
        child_zip = etp.export_zip(child_dir)
        child_err = None
    except Exception as e:                                 # noqa: BLE001
        child_zip, child_err = None, str(e)[:300]
    sent = source_dir / "channel" / "task-package.zip"
    if sent.is_file():
        src_zip, src_err = sent.read_bytes(), None
        origin = "channel/task-package.zip"
    else:
        try:
            src_zip, src_err = etp.export_zip(source_dir), None
        except Exception as e:                             # noqa: BLE001
            src_zip, src_err = None, str(e)[:300]
        origin = "сборка"
    if src_err or child_err:
        same = (src_err == child_err) and src_zip is None and child_zip is None
        rows.append({"path": "package.zip", "same": same, "origin": origin,
                     "source_error": src_err, "replay_error": child_err})
        if not same:
            first = {"path": "package.zip", "kind": "package",
                     "source": src_err or "собран", "replay": child_err or "собран"}
        return rows, first
    same = _sha(src_zip) == _sha(child_zip)
    rows.append({"path": "package.zip", "same": same, "origin": origin,
                 "source_sha256": _sha(src_zip), "replay_sha256": _sha(child_zip)})
    if not same:
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(src_zip)) as za, \
                zipfile.ZipFile(io.BytesIO(child_zip)) as zb:
            names = sorted(set(za.namelist()) | set(zb.namelist()))
            for name in names:
                if name not in za.namelist() or name not in zb.namelist():
                    first = {"path": f"package/{name}", "kind": "only-in-"
                             + ("replay" if name not in za.namelist() else "source")}
                    break
                a, b = za.read(name), zb.read(name)
                if a != b:
                    first = first_difference(f"package/{name}", a, b)
                    break
    return rows, first


def environment_differs(source_status: dict, child_dir) -> list[dict]:
    """Различия окружения: код панели, спецификация, Python, пакеты,
    каталог; модели ролей — по манифестам журналов."""
    from cdpm import run_passport as rp
    out: list[dict] = []
    born = (source_status.get("passport") or {}).get("environment") or {}
    env = rp.environment()
    pairs = [("код панели", (born.get("code") or {}).get("commit"),
              (env.get("code") or {}).get("commit")),
             ("версия спецификации", born.get("spec_version"), env.get("spec_version")),
             ("python", born.get("python"), env.get("python"))]
    for name, a, b in pairs:
        if a != b:
            out.append({"what": name, "source": a, "replay": b})
    for pkg in sorted(set(born.get("packages") or {}) | set(env.get("packages") or {})):
        a = (born.get("packages") or {}).get(pkg)
        b = (env.get("packages") or {}).get(pkg)
        if a != b:
            out.append({"what": f"пакет {pkg}", "source": a, "replay": b})
    ca, cb = born.get("catalog") or {}, env.get("catalog") or {}
    if (ca.get("fingerprint"), ca.get("version")) != (cb.get("fingerprint"), cb.get("version")):
        out.append({"what": "каталог шаблонов",
                    "source": f"{ca.get('version')} {ca.get('fingerprint')}",
                    "replay": f"{cb.get('version')} {cb.get('fingerprint')}"})
    return out


def roles_differ(source_dir, child_dir) -> list[dict]:
    a = ((cj.manifest(source_dir) or {}).get("versions") or {}).get("roles") or {}
    b = ((cj.manifest(child_dir) or {}).get("versions") or {}).get("roles") or {}
    out = []
    for key in sorted(set(a) | set(b)):
        if key in a and key in b and _canon(a[key]) != _canon(b[key]):
            out.append({"what": f"модель роли {key}",
                        "source": f"{a[key].get('provider')}/{a[key].get('model')}",
                        "replay": f"{b[key].get('provider')}/{b[key].get('model')}"})
    return out


# ------------------------------------------------------------ доступность

def _line_busy(state) -> bool:
    s = str(state or "")
    return s.endswith("идёт") or s.endswith("идут") or s.startswith("на паузе")


def _catalog_words() -> str | None:
    """Слова отказа каталога шаблонов либо None. Повтор — чтение: он
    не поднимает отказ, а называет его у кнопки."""
    from cdpm import catalog_bridge as bridge
    try:
        bridge.ensure_valid()
    except bridge.CATALOG_ERRORS as e:
        return (f"каталог шаблонов недоступен: {bridge.words_of(e)} — "
                f"повтор не построит задание модели")
    return None


#: Слова у кнопки «из журнала» для источника, записанного при ПРЕЖНЕМ
#: порядке словаря каталога (шаг 7 переезда: листинг словаря в задании
#: модели пошёл порядком читателя). Это предупреждение, не запрет:
#: решение за владельцем, а расхождение названо ДО нажатия.
DICTIONARY_ORDER_WORDS = (
    "журнал записан при другом порядке словаря каталога — повтор из "
    "журнала даст «расходится» на первом вызове извлечения; доступен "
    "повтор заново")


def _source_dictionary_order(status: dict) -> str | None:
    """Признак порядка словаря каталога у ИСТОЧНИКА — из блока
    `catalog` паспорта, записанного при рождении прогона. Нет
    признака (прогоны до шага 7) — None."""
    born = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    env = born.get("environment") if isinstance(born.get("environment"), dict) else {}
    cat = env.get("catalog") if isinstance(env.get("catalog"), dict) else {}
    got = cat.get("dictionary_order")
    return str(got) if got else None


def availability(run_dir, status: dict | None = None) -> dict:
    """Доступность кнопок при чтении (без чтения тел журнала)."""
    run_dir = Path(run_dir)
    if status is None:
        try:
            status = _read_json(run_dir / "status.json")
        except (OSError, ValueError):
            status = {}

    def _no(code, words):
        return {"available": False, "reason_code": code, "reason_words": words}
    catalog_words = _catalog_words()
    both = None
    if disabled_by_site():
        both = _no("disabled-by-site", f"повтор выключен на площадке ({ENV_DISABLED}=0)")
    elif catalog_words is not None:
        # Каталог обязателен (шаг 7): без словарей повтор не построит ни
        # задание модели, ни посадки — обе кнопки отказывают СЛОВАМИ
        # контура, а не падают на первой стадии потомка.
        both = _no("catalog-unavailable", catalog_words)
    elif replay_of(status):
        both = _no("replay-of-replay", "источник повтора обязан быть первичным "
                   "прогоном — повторяйте его источник")
    elif any(_line_busy(status.get(k)) for k in
             ("state", "data_state", "smt_state", "catalog_state")):
        both = _no("run-busy", "прогон занят стадией — дождитесь её завершения")
    else:
        acts = [a for a in aj.actions(run_dir) if not a.get("refused")]
        pre_journal = not acts or acts[0].get("action") != "create"
        if pre_journal:
            # Прогон до журнала: «из журнала» невозможен (ответов моделей
            # нет), «заново» — по цепочке, восстановленной из артефактов.
            acts = reconstruct_actions(run_dir, status)
        missing = _missing_inputs(run_dir, acts) if acts else ["src/"]
        if missing:
            both = _no("inputs-missing", "входы источника не найдены: "
                       + "; ".join(missing[:3]))
        elif pre_journal:
            journal = _no("pre-journal", "прогон рождён до журнала действий — "
                          "ответов моделей для повтора из журнала нет")
            fresh = {"available": True, "reason_code": None,
                     "reason_words": "цепочка действий восстановлена по "
                                     "артефактам прогона (журнала действий нет)",
                     "estimate": estimate_of(run_dir, acts)}
            layout = status.get("layout") if isinstance(status.get("layout"), dict) else {}
            if layout.get("cloud_gate") == "blocked":
                fresh = _no("layout-undeclared",
                            "облачная раскладка ролей не объявлена — повтор "
                            "заново не построит облачные стадии")
            return {"journal": journal, "fresh": fresh}
    if both:
        return {"journal": dict(both), "fresh": dict(both)}
    journal = {"available": True, "reason_code": None, "reason_words": None}
    man = cj.manifest(run_dir)
    stage_ran = any(a.get("action") in STAGE_LINES and a.get("done")
                    and not a.get("refused") for a in acts)
    if stage_ran and (man is None or man.get("degraded")):
        journal = _no("journal-incomplete",
                      "журнал внешних вызовов неполон: " + str(
                          (man or {}).get("degraded") or "стадии шли без журнала"))
    elif stage_ran and _source_dictionary_order(status) is None:
        # Шаг 7 переезда: у прогонов, записанных прежним порядком
        # словаря, повтор ИЗ ЖУРНАЛА расходится на первом вызове
        # извлечения — листинг словаря входит в текст запроса модели, а
        # журнал отвечает по отпечатку текста. Кнопка остаётся
        # доступной (решение владельца: предупреждение, не запрет), но
        # расхождение названо заранее — принцип воспроизводимости.
        journal = {"available": True, "reason_code": "dictionary-order",
                   "reason_words": DICTIONARY_ORDER_WORDS}
    fresh = {"available": True, "reason_code": None, "reason_words": None,
             "estimate": estimate_of(run_dir, acts)}
    layout = status.get("layout") if isinstance(status.get("layout"), dict) else {}
    if stage_ran and layout.get("cloud_gate") == "blocked":
        fresh = _no("layout-undeclared",
                    "облачная раскладка ролей не объявлена — повтор заново "
                    "не построит облачные стадии: " + str(layout.get("detail") or ""))
    return {"journal": journal, "fresh": fresh}


def estimate_of(run_dir, acts: list[dict]) -> dict:
    """Оценка для подтверждения «повтора заново» (решение владельца 07.09):
    вызовов моделей и длительность стадий у источника — стоимость как у
    полного прогона; у прогона до журнала вызовы неизвестны."""
    stages = [a for a in acts if a.get("action") in STAGE_LINES
              and a.get("done") and not a.get("refused")]
    known = [float(a["duration_s"]) for a in stages
             if a.get("duration_s") is not None]
    calls = cj.count(run_dir) if cj.index_path(run_dir).is_file() else None
    return {"calls": calls,
            # Длительность — только по известным стадиям; у восстановленной
            # цепочки её нет — честное null, не ноль.
            "duration_s": round(sum(known), 1) if known else None,
            "stages": [a.get("action") for a in stages],
            "reconstructed": any(a.get("reconstructed") for a in acts)}


def reconstruct_actions(run_dir, status: dict) -> list[dict]:
    """Цепочка действий прогона, рождённого ДО журнала действий, — по
    артефактам: рождение (документы src/), извлечение, аудит-цикл или
    сквозной цикл (параметры из статуса), книги ответов по ревизиям,
    книги данных и линия данных, сверка данных, файл третьей стороны и
    сверка формализации, проверка SMT. Порядок — порядок конвейера;
    точный порядок нажатий источника неизвестен, и отчёт так и говорит
    (`reconstructed`). Одно свидетельство порядок уточняет: книга
    ответов, отвечающая на вопрос линии данных (`Q-2-дл-…`, `Q-3a/3b-нет-
    данных-…`, `Q-3a/3b-данные-…`, `Q-3b-история-…` —
    `answers.is_data_line_question`), заведомо
    подана ПОСЛЕ сверки данных — иначе такого вопроса в своде не было,
    и при повторе ответ пропал бы как «ответ на неизвестный вопрос»
    (находка COSMOS 08.09 на повторе «заново» run-0089). Поэтому книги
    данных и сверка встают перед первой такой книгой; книги без ответов
    линии данных остаются перед линией данных. Состояний «после» у
    таких действий нет — водитель ждёт стадию и не судит состояние."""
    from cdpm import answers as answers_mod
    run_dir = Path(run_dir)
    src = run_dir / "src"
    if not src.is_dir():
        return []
    files = []
    for p in sorted(src.iterdir()):
        if p.is_file():
            data = p.read_bytes()
            files.append(aj.file_entry(p.name, data, f"src/{p.name}"))
    kit = status.get("kit") if isinstance(status.get("kit"), dict) else {}
    acts: list[dict] = [{
        "n": 1, "action": "create", "reconstructed": True, "done": True,
        "refused": False, "files": files,
        "params": {"customer": status.get("customer"),
                   "data_class": kit.get("data_class"),
                   "llm_scope": kit.get("llm_scope"),
                   "llm_scope_basis": kit.get("llm_scope_basis"),
                   "cloud_images": True}}]

    def _add(action, params=None, files_=None):
        acts.append({"n": len(acts) + 1, "action": action,
                     "reconstructed": True, "done": True, "refused": False,
                     "params": params or {}, "files": files_ or [],
                     "state_after": None, "duration_s": None})
    if (run_dir / "extract" / "model.json").is_file():
        _add("extract", {"customer": "", "scope": None})
    if (run_dir / "exchange" / "rounds").is_dir():
        ex = status.get("exchange") if isinstance(status.get("exchange"), dict) else {}
        params = {"auditor": ex.get("auditor") or "cloud",
                  "worker": ex.get("worker") or "cloud", "scope": None,
                  "max_rounds": int(ex.get("max_rounds") or 5)}
        if isinstance(status.get("full_cycle"), dict):
            params["max_megacycles"] = int(status["full_cycle"].get("max_megacycles") or 3)
            _add("full-cycle", params)
        else:
            params["continue_from_stop"] = False
            _add("exchange", params)
    answers = run_dir / "answers"
    answer_books: list[tuple[str, Path]] = []
    if answers.is_dir():
        for rev in sorted(p for p in answers.iterdir() if p.is_dir()):
            book = rev / "anketa-otvety.xlsx"
            if book.is_file():
                answer_books.append((rev.name, book))
    data_src = run_dir / "data-src"
    data_books = [p for p in sorted(data_src.iterdir()) if p.is_file()] \
        if data_src.is_dir() else []
    data_done = False

    def _add_data_line():
        nonlocal data_done
        if data_done or not data_books:
            return
        data_done = True
        _add("data", {}, [aj.file_entry(p.name, p.read_bytes(),
                                        f"data-src/{p.name}")
                          for p in data_books])
        if (run_dir / "data-report").is_dir():
            _add("data-run", {"scope": None})
        if (run_dir / "data-review").is_dir():
            _add("data-review", {"scope": None, "checks": None})

    for rev_name, book in answer_books:
        if not data_done and (run_dir / "data-report").is_dir() and any(
                answers_mod.is_data_line_question(qid)
                for qid in answers_mod.answered_question_ids(book)):
            _add_data_line()
        _add("answers", {"scope": None}, [aj.file_entry(
            book.name, book.read_bytes(),
            f"answers/{rev_name}/{book.name}")])
    _add_data_line()
    tp = run_dir / "third-party"
    if tp.is_dir():
        for p in sorted(tp.iterdir()):
            if p.is_file():
                _add("third-party", {}, [aj.file_entry(
                    p.name, p.read_bytes(), f"third-party/{p.name}")])
                if (run_dir / "mode2").is_dir():
                    _add("mode2", {"scope": None})
                break
    if status.get("smt_report") or status.get("smt_state"):
        _add("smt-verify", {"scope": None})
    return acts


def _input_bytes(run_dir: Path, entry: dict) -> bytes:
    """Read exact journal input, never silently replay an overwritten book.

    A verified historical backup may be preserved under its ORIGINAL digest
    in journal/input-recovery/sha256/. This does not rewrite the action log,
    change its expected digest, or make different bytes equivalent. Both the
    availability check and the driver validate the bytes they actually use.
    """
    stored = entry.get("stored")
    if not stored:
        raise ReplayRefused("input-missing", "путь файла отсутствует в журнале")
    path = run_dir / stored
    expected = entry.get("sha256")
    if path.is_file():
        data = path.read_bytes()
        if not expected or _sha(data) == expected:
            return data
    if isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected):
        recovered = run_dir / "journal" / "input-recovery" / "sha256" / expected
        if recovered.is_file():
            data = recovered.read_bytes()
            if _sha(data) == expected:
                return data
    if path.is_file():
        raise ReplayRefused("input-fingerprint", f"{stored}: отпечаток не совпадает с журналом")
    raise ReplayRefused("input-missing", f"{stored}: исходный файл отсутствует")


def _missing_inputs(run_dir: Path, acts: list[dict]) -> list[str]:
    missing = []
    for a in acts:
        for f in a.get("files") or []:
            stored = f.get("stored")
            if not stored:
                continue
            try:
                _input_bytes(run_dir, f)
            except ReplayRefused as exc:
                missing.append(f"{exc} (действие {a.get('n')})")
    return missing


# ------------------------------------------------------------ старт

def start(source_dir, mode: str, autostart: bool = True) -> dict:
    """Рождение потомка и запуск водителя. `mode` — journal | fresh."""
    from cdpm import run_passport as rp
    from cdpm import webui as w
    source_dir = Path(source_dir)
    if mode not in MODES:
        raise ReplayRefused("mode-unknown", "режим вне словаря: journal, fresh")
    status = w._read_status(source_dir)
    avail = availability(source_dir, status)[mode]
    if not avail["available"]:
        raise ReplayRefused(avail["reason_code"], avail["reason_words"])
    acts = [a for a in aj.actions(source_dir) if not a.get("refused")]
    reconstructed = not acts or acts[0].get("action") != "create"
    if reconstructed:
        acts = reconstruct_actions(source_dir, status)
    birth = acts[0]
    items = []
    for f in birth.get("files") or []:
        items.append((f["name"], _input_bytes(source_dir, f)))
    params = birth.get("params") or {}
    agents = source_dir / "agents.json"
    replay = {
        "mode_name": MODES[mode], "mode": MODES[mode],
        "source_run": source_dir.name, "source_dir": str(source_dir),
        "source_passport_sha256": _sha(_canon(status.get("passport") or {})),
        "source_made_by": rp.made_by_of(source_dir, status) if mode == "journal" else None,
        "agents_json": agents.read_bytes() if agents.is_file() else None,
        "actions_upto": max(a["n"] for a in acts),
        # Прогон до журнала: восстановленная цепочка едет в паспорт
        # потомка (без байтов файлов) — водитель идёт по ней.
        "reconstructed": reconstructed,
        "actions": [{k: v for k, v in a.items() if k != "files"}
                    | {"files": [{kk: vv for kk, vv in f.items()}
                                 for f in (a.get("files") or [])]}
                    for a in acts] if reconstructed else None,
        "title": ("Повтор из журнала: " if mode == "journal" else "Повтор заново: ")
                 + str(status.get("title") or source_dir.name),
        "started_at": _now(),
    }
    got = w._create_run_from_bytes(
        items, cloud_images=bool(params.get("cloud_images")),
        customer=params.get("customer") or "",
        data_class=params.get("data_class") or "",
        llm_scope=params.get("llm_scope") or "",
        llm_scope_basis=params.get("llm_scope_basis") or "",
        replay=replay)
    child_id = got.get("run") if isinstance(got, dict) else json.loads(
        got.body.decode("utf-8")).get("run")
    child_dir = w._run_dir(child_id)

    def _pointer(st: dict) -> None:
        reps = [r for r in (st.get("replays") or []) if r.get("run") != child_id]
        reps.append({"run": child_id, "mode": MODES[mode], "verdict": None})
        st["replays"] = reps
    w._mutate_status(source_dir, _pointer)
    out = {"run": child_id, "source_run": source_dir.name, "mode": MODES[mode],
           "state": "повтор идёт"}
    if autostart:
        threading.Thread(target=drive, args=(child_dir,), daemon=True).start()
    return out


# ------------------------------------------------------------ водитель

def _wait_idle(w, child_dir: Path, keys: tuple, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        st = w._read_status(child_dir)
        if not any(_line_busy(st.get(k)) for k in keys):
            return st
        if time.monotonic() > deadline:
            raise TimeoutError(f"стадия не завершилась за {int(timeout_s)} с")
        time.sleep(POLL_S)


def _stop_requested(w, child_dir: Path) -> bool:
    return w._read_command(child_dir) == "stop"


def _last_mismatch(child_dir: Path) -> dict | None:
    for r in reversed(cj.read_index(child_dir)):
        err = r.get("error") or {}
        if err.get("type") == "ReplayMismatch":
            return {"kind": "prompt", "stage": r.get("stage"), "role": r.get("role"),
                    "n": r.get("n"), "words": err.get("words")}
    return None


def _apply_action(w, child_id: str, child_dir: Path, source_dir: Path, a: dict):
    """Исполнение действия источника у потомка теми же функциями ручек."""
    name, p = a.get("action"), a.get("params") or {}
    if name == "files-config":
        return w.put_files_config(child_id, p.get("body") or {})
    if name == "agents":
        return w.put_agents(child_id, p.get("body") or {})
    if name == "kit-scope":
        return w.put_kit_scope(child_id, p.get("body") or {})
    if name == "extract":
        return w.start_extract(child_id, customer=p.get("customer") or "",
                               scope=p.get("scope"))
    if name == "answers":
        f = (a.get("files") or [None])[0] or {}
        return w._upload_answers_bytes(child_id, f.get("name"),
                                       _input_bytes(source_dir, f),
                                       p.get("scope"))
    if name == "third-party":
        f = (a.get("files") or [None])[0] or {}
        return w._upload_third_party_bytes(child_id, f.get("name"),
                                           _input_bytes(source_dir, f))
    if name == "mode2":
        return w.start_mode2(child_id, scope=p.get("scope"))
    if name == "exchange":
        return w.start_exchange(child_id, auditor=p.get("auditor") or "cloud",
                                worker=p.get("worker") or "cloud",
                                scope=p.get("scope"),
                                max_rounds=int(p.get("max_rounds") or 5),
                                continue_from_stop=bool(p.get("continue_from_stop")))
    if name == "full-cycle":
        return w.start_full_cycle(child_id, auditor=p.get("auditor") or "cloud",
                                  worker=p.get("worker") or "cloud",
                                  scope=p.get("scope"),
                                  max_rounds=int(p.get("max_rounds") or 5),
                                  max_megacycles=int(p.get("max_megacycles") or 3))
    if name == "reconsider":
        return w.reconsider_question(child_id, p.get("qid"))
    if name == "kind-migrations":
        return w.apply_kind_migrations(child_id, p.get("migrations") or [])
    if name == "exchange-void":
        return w.void_unfinished_exchange(child_id)
    if name == "exchange-resync":
        return w.resync_exchange_canon(child_id, src=p.get("src") or "exchange")
    if name == "arbiter":
        return w.start_arbiter(child_id, scope=p.get("scope"))
    if name == "data":
        incoming = [(f["name"], _input_bytes(source_dir, f))
                    for f in (a.get("files") or [])]
        return w._upload_data_bytes(child_id, incoming)
    if name == "data-delete":
        return w.delete_data_file(child_id, p.get("filename"))
    if name == "data-run":
        return w.start_data_run(child_id, scope=p.get("scope"))
    if name == "data-review":
        return w.start_data_review(child_id, scope=p.get("scope"),
                                   checks=p.get("checks"))
    if name == "smt-verify":
        return w.start_smt_verify(child_id, scope=p.get("scope"))
    if name == "catalog-request":
        # Шаг 6 каталога: действие ВОСПРОИЗВОДИТСЯ (пропуск прятал бы
        # исход и красил сверку файлов). При `trigger="replay"` ручка
        # пишет только папку прогона и индекс: очередь не трогается,
        # доставки нет; `mode` источника исполняется безусловно —
        # условия автозапуска живут в `after` стадии.
        return w.submit_catalog_requests(
            child_id, rule_ids=p.get("rule_ids"),
            request_ids=p.get("request_ids"),
            document_class=p.get("document_class"),
            mode=p.get("mode") or "all",
            confirm_service_c=bool(p.get("confirm_service_c")),
            trigger="replay")
    if name == "conditions-questions":
        return w.refresh_conditions_questions(child_id)
    if name == "reset":
        return w.reset_run(child_id)
    raise ReplayRefused("action-unknown", f"действие журнала вне словаря: {name!r}")


def is_release_gate_check(a: dict) -> bool:
    """Действие журнала — проверка РУБЕЖА выпуска пакета, а не нажатие
    кнопки «Проверить непротиворечивость»: имя одно («smt-verify»),
    различает происхождение (`params.origin`). Кнопку повтор играет,
    как играл."""
    return a.get("action") == "smt-verify" \
        and (a.get("params") or {}).get("origin") == aj.SMT_GATE_ORIGIN


def _take_source_verdict(child_dir: Path) -> None:
    """Проверку рубежа потомок не играет заново: он берёт вердикт
    ИСТОЧНИКА — тем же законом, каким берёт его сам рубеж
    (`export_task_package.take_replay_source_verdict`: своды потомка и
    источника байт в байт, Ф-1.7, и отпечаток отчёта это доказывает).

    Почему не заново: повтор «из журнала» позвал бы модель (а запрос
    рубежа у источника отвечается один раз), переписал бы отчёт СВОИМ
    моментом проверки — и `extract/smt-report.json` разошёлся бы у
    источника и потомка, — да ещё занял бы линию SMT статуса, которой у
    источника нет. Отказ помощника повтор не роняет: у потомка просто
    не будет отчёта заранее, и рубеж при выпуске пакета возьмёт его
    сам."""
    from cdpm import export_task_package as etp
    try:
        etp.take_replay_source_verdict(child_dir)
    except Exception as e:                                 # noqa: BLE001
        print(f"CDPM: {Path(child_dir).name}: вердикт источника не взят "
              f"({str(e)[:200]})", flush=True)


def drive(child_dir) -> dict:
    """Водитель повтора (фоновая нить): действия источника по порядку,
    ожидание стадий, сравнение, отчёт, паспорт, указатель источника."""
    from cdpm import webui as w
    child_dir = Path(child_dir)
    child_id = child_dir.name
    status = w._read_status(child_dir)
    rep = replay_of(status) or {}
    source_dir = source_dir_of(child_dir, status) or child_dir.parent
    mode_name = rep.get("mode") or "replay-journal"
    # Режим журнала — на этот процесс (после перезапуска панели тоже).
    if mode_name == "replay-journal":
        cj.set_mode(child_dir, "replay-journal", source_dir)
    else:
        cj.set_mode(child_dir, "replay-fresh", source_dir)
    if rep.get("reconstructed") and rep.get("actions"):
        acts = list(rep["actions"])
    else:
        acts = [a for a in aj.actions(source_dir)
                if not a.get("refused") and a.get("n", 0) <= int(rep.get("actions_upto") or 0)]
    todo = [a for a in acts if a.get("action") != "create"]
    replayed, skipped = 0, []
    first: dict | None = None
    divergences: list = []
    stopped = False
    for i, a in enumerate(todo, start=1):
        if _stop_requested(w, child_dir):
            stopped = True
            break
        name = a.get("action")
        w._update_status(child_dir, replay_progress={
            "step": i, "of": len(todo), "action": name, "n": a.get("n")})
        if name in SKIP_ACTIONS:
            skipped.append({"n": a.get("n"), "action": name,
                            "code": SKIP_ACTIONS[name]})
            continue
        if is_release_gate_check(a):
            _take_source_verdict(child_dir)
            skipped.append({"n": a.get("n"), "action": name,
                            "code": SMT_GATE_CODE})
            continue
        try:
            _apply_action(w, child_id, child_dir, source_dir, a)
        except Exception as e:                             # noqa: BLE001
            detail = getattr(e, "detail", None) or str(e)
            first = {"kind": "action", "n": a.get("n"), "action": name,
                     "words": f"действие {name} у повтора отказано: {detail}"[:600]}
            break
        lines = STAGE_LINES.get(name)
        if lines:
            src_dur = float(a.get("duration_s") or 0.0)
            try:
                st = _wait_idle(w, child_dir, lines, max(MIN_WAIT_S, 10 * src_dur))
            except TimeoutError as e:
                first = {"kind": "timeout", "n": a.get("n"), "action": name,
                         "words": str(e)}
                break
            expected = (a.get("state_after") or {})
            got = {k: st.get(k) for k in lines}
            if any(str(st.get(k) or "").startswith("остановлен") for k in lines) \
                    or _stop_requested(w, child_dir):
                stopped = True
                break
            # У восстановленной цепочки состояний «после» нет — судится
            # только отказ стадии; у «заново» отказ — тоже расхождение.
            if not expected:
                diff = [k for k in lines if str(st.get(k) or "").startswith("отказ")]
            else:
                diff = [k for k in lines if expected.get(k) != st.get(k)]
            if diff:
                mm = _last_mismatch(child_dir)
                if mm:
                    div = {**mm, "action": name, "action_n": a.get("n")}
                else:
                    k = diff[0]
                    div = {"kind": "state", "n": a.get("n"), "action": name,
                           "where": k, "source": expected.get(k),
                           "replay": got.get(k),
                           "words": str(st.get("error") or "")[:600] or None}
                refused = any(str(st.get(k) or "").startswith("отказ")
                              for k in lines)
                if mode_name == "replay-fresh" and not refused:
                    # «Заново»: модели отвечают иначе, и строка состояния
                    # недетерминированной стадии вправе разойтись («…;
                    # серьёзных в бэклоге: 5» после сквозного цикла — живой
                    # урок 09.09: остановка здесь оставила потомка без линии
                    # данных и ответов, пакет ушёл stop/stop). Расхождение
                    # запоминается (первое — в вердикт, все — в отчёт), а
                    # цепочка идёт дальше: судятся итоги. Стоп — только
                    # отказ стадии, ошибка действия, таймаут и кнопка.
                    if first is None:
                        first = div
                    divergences.append(div)
                    replayed += 1
                    continue
                first = div
                break
        replayed += 1
    return finish(child_dir, source_dir, mode_name, replayed, skipped,
                  first, stopped, divergences)


def _counter(items) -> dict:
    out: dict = {}
    for k in items:
        key = str(k)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _model_outcomes(run_dir: Path) -> dict:
    p = run_dir / "extract" / "model.json"
    if not p.is_file():
        return {}
    try:
        doc = _read_json(p)
    except (OSError, ValueError):
        return {}
    rules = [r for r in doc.get("rules") or [] if isinstance(r, dict)]
    qs = [q for q in doc.get("questions") or [] if isinstance(q, dict)]
    out = {
        "правил по видам": _counter(r.get("kind") for r in rules),
        "правил по зонам": _counter(r.get("zone") for r in rules),
        "категорий посадки": _counter(
            (r.get("template_fit") or {}).get("category")
            for r in rules if isinstance(r.get("template_fit"), dict)),
        "вопросов всего": len(qs),
        "вопросов открытых": sum(1 for q in qs
                                 if not (q.get("answer") or {}).get("status")),
        "целевых": len(doc.get("objectives") or []),
    }
    rej = run_dir / "extract" / "rejections.json"
    if rej.is_file():
        try:
            got = _read_json(rej)
            out["отброшенных"] = len(got) if hasattr(got, "__len__") else got
        except (OSError, ValueError):
            pass
    return out


def _exchange_outcomes(run_dir: Path, status: dict) -> dict:
    out: dict = {}
    ex = status.get("exchange_summary")
    if isinstance(ex, dict):
        out["вердикт цикла"] = ex.get("verdict")
        out["раундов"] = ex.get("rounds")
    rounds = run_dir / "exchange" / "rounds"
    if rounds.is_dir():
        metr = []
        for rd in sorted(p for p in rounds.iterdir() if p.is_dir()):
            mp = rd / "metrics.json"
            if mp.is_file():
                try:
                    m = _read_json(mp)
                    metr.append({k: m.get(k) for k in ("B", "K", "S", "E", "patched")})
                except (OSError, ValueError):
                    pass
        if metr:
            out["замечаний по раундам (Б/К/С/Е, патч)"] = metr
    for k, v in status.items():
        if str(k).startswith("arbiter") and v is not None:
            out[f"арбитр: {k}"] = v
    return out


def _answers_outcomes(run_dir: Path, status: dict) -> dict:
    out: dict = {}
    ac = status.get("answers_counters")
    if isinstance(ac, dict):
        out["ответы: применено/открыто/ревизия"] = [
            ac.get("applied"), ac.get("open"), ac.get("revision")]
    answers = run_dir / "answers"
    if answers.is_dir():
        revs = []
        for rev in sorted(p for p in answers.iterdir() if p.is_dir()):
            sp = rev / "answers-summary.json"
            if sp.is_file():
                try:
                    sm = _read_json(sp)
                    revs.append({k: sm.get(k) for k in
                                 ("revision", "received", "applied", "awaiting",
                                  "unparsed", "worker_applied")})
                except (OSError, ValueError):
                    pass
        if revs:
            out["ответы по ревизиям"] = revs
    return out


def _data_outcomes(run_dir: Path, status: dict) -> dict:
    out: dict = {}
    dc = status.get("data_counters")
    if isinstance(dc, dict):
        out["данные: строк/проверок/находок"] = [
            dc.get("rows"), dc.get("checks_run"), dc.get("findings")]
    if status.get("data_state") is not None:
        out["состояние отчёта данных"] = status.get("data_state")
    cp = run_dir / "data-report" / "checks.json"
    if cp.is_file():
        try:
            got = _read_json(cp)
            checks = got.get("checks") if isinstance(got, dict) else got
            if isinstance(checks, list):
                out["проверки по исходам"] = _counter(
                    (c.get("status") or c.get("verdict") or c.get("result"))
                    for c in checks if isinstance(c, dict))
        except (OSError, ValueError):
            pass
    return out


def _smt_outcomes(status: dict) -> dict:
    out: dict = {}
    if status.get("smt_state") is not None:
        out["SMT: состояние"] = status.get("smt_state")
    rep = status.get("smt_report")
    if isinstance(rep, dict):
        out["SMT: вердикт"] = rep.get("verdict") or rep.get("status")
    return out


def _catalog_outcomes(run_dir: Path) -> dict:
    """Итоги заявок в каталог для сравнения «заново» (§1.4 проекта шага
    6): номера заявок и состояния составления. Черновики не
    сравниваются — иной черновик у свежей модели законен, номер тот же
    (он от содержания пробела, не от текста модели)."""
    p = run_dir / "catalog-requests" / "index.json"
    if not p.is_file():
        return {}
    try:
        doc = _read_json(p)
    except (OSError, ValueError):
        return {}
    rows = (doc or {}).get("rows") or {}
    if not isinstance(rows, dict):
        return {}
    return {
        "заявки в каталог: номера": sorted(
            str(v.get("request_id")) for v in rows.values()
            if isinstance(v, dict)),
        "заявки в каталог: состояния составления": {
            str(k): (v or {}).get("composed")
            for k, v in sorted(rows.items()) if isinstance(v, dict)},
    }


_MANIFEST_KEYS = ("spec_version", "modes", "objectives", "crew_size", "kit",
                  "catalog", "horizon", "time", "logic_unproven")


def _manifest_outcomes(run_dir: Path) -> tuple[dict, str | None]:
    from cdpm import export_task_package as etp
    try:
        files = etp.build_package(run_dir)
    except Exception as e:                                 # noqa: BLE001
        return {"пакет": f"не собирается: {str(e)[:200]}"}, None
    man = json.loads(files["manifest.json"].decode("utf-8"))
    out = {f"манифест: {k}": man.get(k) for k in _MANIFEST_KEYS if k in man}
    out["манифест: файлов"] = sorted(files)
    fits = man.get("fits_catalog")
    if isinstance(fits, dict):
        out["манифест: единство посадок"] = fits.get("uniform")
    return out, None


def outcomes_of(source_dir: Path, child_dir: Path) -> tuple[list[dict], dict | None, dict | None]:
    """Таблица итогов «заново»: строки {name, source, replay, same,
    source_file}; первое расхождение; вердикт эквивалентности ВМП тем же
    гейтом, что в обратной задаче (vmp/equivalence.py, только чтением)."""
    from cdpm import webui as w
    ss, cs = w._read_status(source_dir), w._read_status(child_dir)
    groups = [
        ("extract/model.json", _model_outcomes(source_dir), _model_outcomes(child_dir)),
        ("status / exchange", _exchange_outcomes(source_dir, ss), _exchange_outcomes(child_dir, cs)),
        ("status / answers", _answers_outcomes(source_dir, ss), _answers_outcomes(child_dir, cs)),
        ("status / data-report", _data_outcomes(source_dir, ss), _data_outcomes(child_dir, cs)),
        ("status / smt", _smt_outcomes(ss), _smt_outcomes(cs)),
        ("catalog-requests/index.json", _catalog_outcomes(source_dir),
         _catalog_outcomes(child_dir)),
        ("manifest.json", _manifest_outcomes(source_dir)[0], _manifest_outcomes(child_dir)[0]),
    ]
    rows: list[dict] = []
    first: dict | None = None
    for src_file, a, b in groups:
        for name in list(a) + [k for k in b if k not in a]:
            va, vb = a.get(name), b.get(name)
            same = _canon(va) == _canon(vb)
            rows.append({"name": name, "source": va, "replay": vb,
                         "same": same, "source_file": src_file})
            if not same and first is None:
                first = {"kind": "outcome", "name": name, "source": _short(va),
                         "replay": _short(vb), "source_file": src_file}
    equivalence = None
    sp, cp = source_dir / "extract" / "model.json", child_dir / "extract" / "model.json"
    if sp.is_file() and cp.is_file():
        try:
            from vmp import equivalence as eq
            src_doc, child_doc = _read_json(sp), _read_json(cp)
            res = eq.equivalence(candidate=child_doc, etalon=src_doc, forms=True)
            # Гейт обратной задачи судит кандидата против ЗАВЕРШЁННОЙ
            # формализации (открытый вопрос у кандидата — нарушение). У
            # повтора источник — такой же прогон, и его собственные
            # незавершённости (открытые вопросы и т. п.) — не расхождение:
            # вычитается опорный перечень «источник против самого себя».
            base = eq.equivalence(candidate=src_doc, etalon=src_doc, forms=True)
            baseline = {str(p) for p in (base.get("problems") or [])}
            problems = [str(p) for p in (res.get("problems") or [])
                        if str(p) not in baseline]
            verdict = "EQUIVALENT" if not problems else "NOT_EQUIVALENT"
            equivalence = {"verdict": verdict, "verdict_raw": res.get("verdict"),
                           "problems": problems[:40],
                           "problems_total": len(problems),
                           "baseline_problems": len(baseline)}
            same = verdict == "EQUIVALENT"
            rows.append({"name": "эквивалентность ВМП", "source": "EQUIVALENT",
                         "replay": verdict, "same": same,
                         "source_file": "extract/model.json (обе стороны)"})
            if not same and first is None:
                first = {"kind": "outcome", "name": "эквивалентность ВМП",
                         "source": "EQUIVALENT", "replay": verdict,
                         "source_file": "vmp/equivalence.py",
                         "words": "; ".join(problems[:3])}
        except Exception as e:                             # noqa: BLE001
            equivalence = {"verdict": None, "problems": [],
                           "error": str(e)[:300]}
    return rows, first, equivalence


def finish(child_dir: Path, source_dir: Path, mode_name: str, replayed: int,
           skipped: list, first: dict | None, stopped: bool,
           divergences: list | None = None) -> dict:
    from cdpm import webui as w
    child_id = child_dir.name
    man = cj.close(child_dir) or {}
    src_status = w._read_status(source_dir)
    rows: list[dict] = []
    outcomes: list[dict] = []
    equivalence = None
    first_div = first
    if not stopped and first is None and mode_name == "replay-journal":
        rows, first_div = compare(source_dir, child_dir)
        prow, pfirst = package_rows(source_dir, child_dir)
        rows.extend(prow)
        if first_div is None:
            first_div = pfirst
    elif not stopped and mode_name == "replay-fresh":
        # «Заново» судится итогами и после расхождений состояния по пути:
        # таблица итогов строится всегда, первое расхождение — то, что
        # встретилось раньше (состояние по пути либо строка итогов).
        outcomes, odiv, equivalence = outcomes_of(source_dir, child_dir)
        if first_div is None:
            first_div = odiv
    counts = man.get("counts") or {}
    src_counts = (cj.manifest(source_dir) or {}).get("counts") or {}
    calls = {"source": cj.count(source_dir),
             "served_from_journal": int(counts.get("served_from_journal", 0)),
             "served_from_cache": 0,
             "fresh": cj.count(child_dir) - int(counts.get("served_from_journal", 0)),
             "unconsumed": int(counts.get("unconsumed", 0)),
             "errors_source": int(src_counts.get("errors", 0)),
             "errors_replay": int(counts.get("errors", 0))}
    if mode_name == "replay-journal" and not stopped and first_div is None \
            and (calls["unconsumed"] or calls["fresh"]):
        first_div = {"kind": "calls", "words": (
            f"вызовов у источника {calls['source']}, у повтора из журнала "
            f"{calls['served_from_journal']}, живых {calls['fresh']}, "
            f"неизрасходованных записей источника {calls['unconsumed']}")}
        rows.append({"path": "calls", "same": False})
    if stopped:
        verdict = "прервано"
    elif mode_name == "replay-fresh":
        bad = first_div is not None or any(not r.get("same") for r in outcomes)
        verdict = "итоги расходятся" if bad else "итоги совпадают"
    elif first_div is not None or any(not r.get("same") for r in rows):
        verdict = "расходится"
    else:
        verdict = "совпадает"
    env = environment_differs(src_status, child_dir) + roles_differ(source_dir, child_dir)
    report = {"schema": SCHEMA, "mode": mode_name, "source_run": source_dir.name,
              "run": child_id, "verdict": verdict,
              "environment": {"differs": env},
              "actions": {"replayed": replayed, "skipped": skipped,
                          "stopped": stopped},
              "calls": calls, "artifacts": rows,
              "first_divergence": first_div,
              "state_divergences": list(divergences or []),
              "outcomes": outcomes, "equivalence": equivalence,
              "note": ("различие ответов моделей у недетерминированных стадий "
                       "законно — сравнение по итогам, не по байтам"
                       if mode_name == "replay-fresh" else None),
              "reconstructed": bool((replay_of(w._read_status(child_dir)) or {})
                                    .get("reconstructed")),
              "finished_at": _now()}
    rp_path = report_path(child_dir)
    rp_path.parent.mkdir(parents=True, exist_ok=True)
    rp_path.write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n")
                        .encode("utf-8"))

    def _child(st: dict) -> None:
        born = st.get("passport") if isinstance(st.get("passport"), dict) else {}
        rep = dict(born.get("replay") or {})
        rep.update({"finished_at": report["finished_at"], "verdict": verdict,
                    "report": REPORT_REL})
        rep.pop("agents_json", None)
        rep.pop("actions", None)          # цепочка отработала — в отчёте
        born["replay"] = rep
        st["passport"] = born
        st.pop("replay_progress", None)
    w._mutate_status(child_dir, _child)

    def _pointer(st: dict) -> None:
        reps = list(st.get("replays") or [])
        for r in reps:
            if r.get("run") == child_id:
                r["verdict"] = verdict
        st["replays"] = reps
    try:
        w._mutate_status(source_dir, _pointer)
    except Exception:                                      # noqa: BLE001
        pass
    return report


def read_report(run_dir) -> dict | None:
    p = report_path(run_dir)
    if not p.is_file():
        return None
    try:
        return _read_json(p)
    except (OSError, ValueError):
        return None
