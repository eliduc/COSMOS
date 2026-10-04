# -*- coding: utf-8 -*-
"""Таблицы решателя, прочитанные из исходников Kotlin (шаг 4 каталога).

Один читатель на всё дерево: инструмент извлечения каталога
(`catalog/tools/build_from_code.py`), генератор привязок
(`vmp/bindings_build.py`), гейт символов (`vmp/bindings_gate.py`) и стенд
(`vmp/stand_tasks.py`) берут ручки, формы логики, метрики и литералы
ограничений отсюда, а не своими выражениями. Гейт паритета
(`tests/test_template_catalog_parity.py`) СВОЙ читатель сохраняет —
независимый свидетель.

Модуль ничего не меняет в Kotlin и ничего не импортирует из каталога и
из `cdpm`: только чтение файлов
`kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/solver/`
регулярными выражениями и разбором парных скобок. Kotlin в шаге 4 не
правится — модуль читает то, что уже есть.

Отказы — словами (`KotlinTableError`): «в RunThresholds.kt не найден блок
…», «аргументы ручки … не разобраны». Молчаливого пропуска нет: не
прочитанная таблица — отказ, а не пустой словарь.

    from vmp import kotlin_tables as kt
    kt.knobs()["max_duty_duration"]["threshold"]   # "max_duty_span_min"
    kt.metrics()["credit_hours_spread"]["tasks"]   # ["rostering"]
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: Папка исходников решателя (зона только для чтения).
KOTLIN_DIR = (REPO / "kotlin-solver" / "src" / "main" / "kotlin" / "org"
              / "acme" / "flighcrewscheduling" / "solver")

PROVIDER = "FlightCrewSchedulingConstraintProvider.kt"
RUN_THRESHOLDS = "RunThresholds.kt"
LOGIC_MODEL = "LogicModel.kt"
LOGIC_FORMS = "LogicForms.kt"
TASK_PACKAGE = "TaskPackage.kt"
TASK_MODES = "TaskModes.kt"


class KotlinTableError(RuntimeError):
    """Таблица Kotlin не прочитана — отказ словами, не пустой словарь."""


# ------------------------------------------------------------- чтение
_CACHE: dict = {}


def read(name: str) -> str:
    """Текст файла решателя; кэш по (размер, отметка времени) — правка
    файла на диске перечитывается, повторный разбор в одном процессе
    файл не перечитывает."""
    p = KOTLIN_DIR / name
    try:
        st = p.stat()
    except OSError as e:
        raise KotlinTableError(f"файл решателя {p} не читается: {e}") from e
    key = (str(p), st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(str(p))
    if hit is not None and hit[0] == key:
        return hit[1]
    text = p.read_text(encoding="utf-8")
    _CACHE[str(p)] = (key, text)
    return text


def forget() -> None:
    """Сбросить кэш текстов (тесты, правящие файлы на диске)."""
    _CACHE.clear()


# ------------------------------------------------- разбор общего вида
def _paired(text: str, start: int, opener: str = "(", closer: str = ")") -> str:
    """Текст от позиции ПОСЛЕ открывающей скобки до парной закрывающей со
    счётом вложенности; строковые литералы и построчные заметки при счёте
    пропускаются."""
    depth, i, n = 1, start, len(text)
    while i < n and depth:
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
            continue
        if ch == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
        i += 1
    if depth:
        raise KotlinTableError(f"скобка {opener!r} не закрыта в Kotlin "
                               f"(позиция {start})")
    return text[start:i - 1]


def _skip_string(text: str, i: int) -> int:
    """Позиция сразу за строковым литералом, начинающимся на i."""
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    raise KotlinTableError("строковый литерал Kotlin не закрыт")


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "$": "$",
            "'": "'"}


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(_ESCAPES.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return "".join(out)


_STR_START = re.compile(r'\s*"')


def string_expr(text: str, pos: int) -> tuple[str, int]:
    """Строковое выражение Kotlin с позиции pos: один литерал либо
    несколько, склеенных «+» (в том числе через перевод строки).
    Возвращает (значение, позиция за выражением). Регулярное выражение по
    ОДНОЙ строке такие тексты не собирает — заметки метрик написаны
    склейкой."""
    parts: list[str] = []
    i = pos
    while True:
        m = _STR_START.match(text, i)
        if not m:
            break
        start = m.end() - 1
        end = _skip_string(text, start)
        parts.append(_unescape(text[start + 1:end - 1]))
        i = end
        nxt = re.match(r"\s*\+", text[i:])
        if not nxt:
            break
        i += nxt.end()
    if not parts:
        raise KotlinTableError(f"строковое выражение Kotlin не найдено "
                               f"(позиция {pos})")
    return "".join(parts), i


def block(text: str, marker: str, *, where: str) -> str:
    """Аргументы конструкции, начинающейся с marker (marker кончается
    открывающей скобкой), до парной закрывающей."""
    i = text.find(marker)
    if i < 0:
        raise KotlinTableError(f"в {where} не найден блок {marker!r}")
    return _paired(text, i + len(marker))


# ------------------------------------------------------------- ручки
_KNOB_HEAD_RE = re.compile(r'"(\w+)"\s+to\s+Knob\(')
#: Первые три аргумента Knob: порог (литерал либо константа), единица,
#: отношение.
_KNOB_ARGS_RE = re.compile(
    r'\s*("(?:[^"\\]|\\.)*"|[\w.]+)\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"')
_ALSO_ACCEPTS_RE = re.compile(r"\balsoAccepts\s*=\s*setOf\(([^)]*)\)")


def _knob_map(block_text: str, consts: dict | None = None) -> dict:
    out: dict = {}
    for m in _KNOB_HEAD_RE.finditer(block_text):
        args = _paired(block_text, m.end())
        a = _KNOB_ARGS_RE.match(args)
        if not a:
            raise KotlinTableError(f"аргументы ручки {m.group(1)!r} не "
                                   f"разобраны: {args[:80]!r}")
        accepts = _ALSO_ACCEPTS_RE.search(args)
        raw = a.group(1).strip()
        out[m.group(1)] = {
            "threshold": _threshold_value(raw, consts),
            "threshold_ref": raw,
            "unit": a.group(2),
            "relation": a.group(3),
            "keyed": re.search(r"\bkeyed\s*=\s*true\b", args) is not None,
            "accepts": re.findall(r'"(\w+)"', accepts.group(1)) if accepts
            else [],
        }
    if not out:
        raise KotlinTableError("в блоке ручек Kotlin не прочитано ни одной "
                               "ручки")
    return out


def _threshold_value(raw: str, consts: dict | None) -> str:
    """Порог ручки: литерал в кавычках — как есть; ссылка на константу
    (`DefaultProviderRules.RESERVE_FAMILY`) — её значение. Не разрешённая
    ссылка — отказ словами, а не имя константы вместо порога."""
    if raw.startswith('"'):
        return _unescape(raw.strip('"'))
    name = raw.rsplit(".", 1)[-1]
    table = consts if consts is not None else constants()
    if name in table and isinstance(table[name], str):
        return table[name]
    raise KotlinTableError(f"порог ручки {raw!r} — ни литерал, ни известная "
                           f"константа решателя")


def knobs() -> dict:
    """`RunThresholds.KNOBS` — {имя параметра пакета: {"threshold",
    "unit", "relation", "keyed", "accepts"}}. Порог у ключевой ручки —
    имя семейства ключей (`reserve_crews_min`), не литерал значения."""
    return _knob_map(block(read(RUN_THRESHOLDS),
                           "val KNOBS: Map<String, Knob> = linkedMapOf(",
                           where=RUN_THRESHOLDS), constants())


def sit_knobs() -> dict:
    """`RunThresholds.SIT_KNOBS` — {область ("home" | "outstation"):
    та же форма}. Параметр у обеих один — `sit_param()`."""
    return _knob_map(block(
        read(RUN_THRESHOLDS),
        "private val SIT_KNOBS: Map<String, Knob> = linkedMapOf(",
        where=RUN_THRESHOLDS), constants())


def sit_param() -> str:
    """`RunThresholds.PARAM_SIT` — имя параметра стыковки."""
    m = re.search(r'private const val PARAM_SIT = "(\w+)"',
                  read(RUN_THRESHOLDS))
    if not m:
        raise KotlinTableError(f"в {RUN_THRESHOLDS} не найдена константа "
                               f"PARAM_SIT")
    return m.group(1)


_CONSTANTS = (
    ("FLEET_THRESHOLD", RUN_THRESHOLDS),
    ("DUTY_EXTENSION_THRESHOLD", RUN_THRESHOLDS),
    ("WORKLOAD_SPREAD_THRESHOLD", RUN_THRESHOLDS),
    ("RESERVE_FAMILY", PROVIDER),
)


def constants() -> dict:
    """Именованные пороги-константы решателя: `FLEET_THRESHOLD`,
    `DUTY_EXTENSION_THRESHOLD`, `WORKLOAD_SPREAD_THRESHOLD` (RunThresholds),
    `RESERVE_FAMILY` (провайдер) и перечень `FLEET_PARAMS` (имена
    ручки-перечня и её синонима-одиночки)."""
    out: dict = {}
    for name, fname in _CONSTANTS:
        m = re.search(r'const val %s\s*=\s*"([^"]*)"' % name, read(fname))
        if not m:
            raise KotlinTableError(f"в {fname} не найдена константа {name}")
        out[name] = m.group(1)
    m = re.search(r"val FLEET_PARAMS: Set<String> = setOf\(([^)]*)\)",
                  read(RUN_THRESHOLDS))
    if not m:
        raise KotlinTableError(f"в {RUN_THRESHOLDS} не найден FLEET_PARAMS")
    out["FLEET_PARAMS"] = re.findall(r'"(\w+)"', m.group(1))
    return out


# ------------------------------------------------------------ история
def history() -> dict:
    """`LogicModel.HISTORY_FIT_FAMILIES` и `HISTORY_FIT_UNITS` —
    {"families": [семьи истории], "units": {имя: (единица, отношение)}}."""
    lm = read(LOGIC_MODEL)
    m = re.search(r"val HISTORY_FIT_FAMILIES: Set<String> = setOf\(([^)]*)\)",
                  lm)
    if not m:
        raise KotlinTableError(f"в {LOGIC_MODEL} не найден "
                               f"HISTORY_FIT_FAMILIES")
    families = re.findall(r'"(\w+)"', m.group(1))
    units = {}
    blk = block(lm, "HISTORY_FIT_UNITS: Map<String, Pair<String, String>> = "
                    "mapOf(", where=LOGIC_MODEL)
    for mm in re.finditer(r'"(\w+)" to \("(\w+)" to "(\w+)"\)', blk):
        units[mm.group(1)] = (mm.group(2), mm.group(3))
    if not families or not units:
        raise KotlinTableError(f"в {LOGIC_MODEL} таблицы истории пусты")
    return {"families": families, "units": units}


def family_to_constraint() -> dict:
    """`TaskPackage.FAMILY_TO_CONSTRAINT` — {мягкая семья: ограничение}."""
    out = dict(re.findall(r'"(\w+)" to "([^"]+)"', block(
        read(TASK_PACKAGE),
        "val FAMILY_TO_CONSTRAINT: Map<String, String> = mapOf(",
        where=TASK_PACKAGE)))
    if not out:
        raise KotlinTableError(f"в {TASK_PACKAGE} FAMILY_TO_CONSTRAINT пуста")
    return out


def logic_forms() -> dict:
    """`LogicForms.FORM_*` — {имя формы (значение литерала): имя
    константы}. Форма правила логики называется значением, гейт сверяет
    пару «константа = значение»."""
    out = {}
    for m in re.finditer(r'const val FORM_(\w+)\s*=\s*"([^"]+)"',
                         read(LOGIC_FORMS)):
        out[m.group(2)] = "FORM_" + m.group(1)
    if not out:
        raise KotlinTableError(f"в {LOGIC_FORMS} не найдено ни одной FORM_*")
    return out


# ------------------------------------------------------------- метрики
_METRIC_HEAD_RE = re.compile(r'"(\w+)"\s+to\s+MetricExecutor\(')
_ARG_RE = re.compile(r"\s*(\w+)\s*=\s*")


def metrics() -> dict:
    """`TaskModes.METRIC_EXECUTORS` — {метрика: {"constraint", "tasks",
    "approximation", "refusal"}}. Тексты собираются склейкой соседних
    строковых литералов: заметки метрик написаны через «+», и выражение
    по одной строке их не собрало бы."""
    blk = block(read(TASK_MODES),
                "val METRIC_EXECUTORS: Map<String, MetricExecutor> = mapOf(",
                where=TASK_MODES)
    out: dict = {}
    for m in _METRIC_HEAD_RE.finditer(blk):
        args = _paired(blk, m.end())
        rec = {"constraint": None, "tasks": [], "approximation": None,
               "refusal": None}
        i = 0
        while i < len(args):
            a = _ARG_RE.match(args, i)
            if not a:
                nxt = args.find(",", i)
                if nxt < 0:
                    break
                i = nxt + 1
                continue
            name, i = a.group(1), a.end()
            if name == "tasks":
                j = args.find("setOf(", i)
                if j < 0:
                    raise KotlinTableError(f"метрика {m.group(1)!r}: задачи "
                                           f"не разобраны")
                inner = _paired(args, j + len("setOf("))
                rec["tasks"] = re.findall(r'"(\w+)"', inner)
                i = j + len("setOf(") + len(inner) + 1
            elif name in ("constraint", "approximation", "refusal"):
                rec[name], i = string_expr(args, i)
            else:
                nxt = args.find(",", i)
                i = len(args) if nxt < 0 else nxt + 1
        if rec["constraint"] is None or not rec["tasks"]:
            raise KotlinTableError(f"метрика {m.group(1)!r}: не прочитаны "
                                   f"ограничение или задачи")
        out[m.group(1)] = rec
    if not out:
        raise KotlinTableError(f"в {TASK_MODES} METRIC_EXECUTORS пуста")
    return out


# ------------------------------------------------- литералы ограничений
_AS_CONSTRAINT_RE = re.compile(r"\.asConstraint\(\s*")
_ADJACENCY_RE = re.compile(r"perEmployeeAdjacency\(")


def constraint_literals() -> set:
    """Имена ограничений провайдера: литералы `asConstraint("…")` и имена,
    переданные `perEmployeeAdjacency(` (у них `asConstraint` зовётся
    внутри помощника — литерала на месте правила нет)."""
    text = read(PROVIDER)
    out: set = set()
    for m in _AS_CONSTRAINT_RE.finditer(text):
        try:
            value, _ = string_expr(text, m.end())
        except KotlinTableError:
            continue          # asConstraint(имя-переменной) — не литерал
        out.add(value)
    for m in _ADJACENCY_RE.finditer(text):
        args = _paired(text, m.end())
        for lit in re.finditer(r'"([^"\\]*)"', args):
            out.add(lit.group(1))
    if not out:
        raise KotlinTableError(f"в {PROVIDER} не прочитано ни одного имени "
                               f"ограничения")
    return out


# ------------------------------------------------------------- всё сразу
def tables() -> dict:
    """Все таблицы одним словарём — вход инструмента извлечения и
    генератора привязок."""
    h = history()
    return {"knobs": knobs(), "sit_knobs": sit_knobs(),
            "sit_param": sit_param(), "constants": constants(),
            "history_families": h["families"], "history_units": h["units"],
            "family_to_constraint": family_to_constraint(),
            "logic_forms": logic_forms(), "metrics": metrics()}
