# -*- coding: utf-8 -*-
"""Гейт привязок COSMOS: существуют ли обещанные символы и закрыты ли
виды записей (шаг 4 каталога, §2.2 проекта
final-report/KATALOG-SHAG-4-PROEKT-CLD.md).

Читатель каталога судит ФОРМУ привязок (B01–B05); здесь судится то,
чего читателю знать неоткуда, — КОД:

* исполнитель на Python «есть» — модуль импортируется, символ по
  точечному пути находится и вызывается;
* исполнитель на Kotlin «есть» — метод и литералы стоят в исходниках
  решателя (Kotlin здесь только ЧИТАЕТСЯ: ни строки правок, ни
  пересборки jar). Таблицы решателя даёт `vmp/kotlin_tables.py` (§8.1),
  а тексты файлов читаются ПО ПУТИ, НАЗВАННОМУ В СТРОКЕ ПРИВЯЗКИ:
  привязка обещает исполнителя в конкретном файле, и судить надо
  именно его;
* «параметр записи ⇒ исполнитель-читатель»: имя, названное записью
  каталога, обязано иметь читателя среди строк «есть» (иначе запись
  обещает то, чего код не читает);
* полнота видов по ЗОНЕ ОТВЕТСТВЕННОСТИ продукта (правило B04
  дополнения к проекту): «есть» хотя бы у одного продукта закрывает
  вид; иначе явную строку обязан дать каждый продукт, в чьей зоне вид;
  вид вне всех зон и без «есть» — ошибка. Отсутствие
  `cdpm/bindings.json` — не остановка, а слова «привязки CDPM не
  объявлены»: зона COSMOS — все десять видов словаря, ответственный
  есть всегда.

    python -m vmp.bindings_gate [vmp/bindings.json] [cdpm/bindings.json]

печатает матрицу «запись × вид → есть/нет/заявлен/— (не объявлено)» и
возвращает 0 (чисто) либо 1.
"""
from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

KOTLIN_DIR = ("kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/"
              "solver")
PROVIDER_FILE = f"{KOTLIN_DIR}/FlightCrewSchedulingConstraintProvider.kt"
RUN_THRESHOLDS_FILE = f"{KOTLIN_DIR}/RunThresholds.kt"
TASK_PACKAGE_FILE = f"{KOTLIN_DIR}/TaskPackage.kt"
LOGIC_FORMS_FILE = f"{KOTLIN_DIR}/LogicForms.kt"
TASK_MODES_FILE = f"{KOTLIN_DIR}/TaskModes.kt"

#: Виды исполнителей на Kotlin (у строки «есть» язык обязан быть kotlin).
KOTLIN_KINDS = ("kotlin_constraint", "kotlin_form", "kotlin_knob",
                "kotlin_logic_form", "kotlin_metric")

#: Ручки решателя, чьё имя параметра каталог держит в `unbound_params`:
#: у записи семьи такого имени ещё нет, и читатель законно предупреждает
#: (B03 «ручка семьи без параметра в записи»). Перечень ОБЯЗАН
#: уменьшаться при раздаче имени семье (§4.1 проекта): лишнее имя здесь
#: было бы молчаливым пропуском, поэтому гейт сверяет его с
#: предупреждениями читателя точным равенством.
#: Пусто с каталога 0.2.0-draft.2 (слово владельца 08.09): три имени —
#: fleet_type, max_sit_duration, max_weekly_block — получили семьи
#: (fleet_exclusions, connection_windows, span_accumulators), и
#: предупреждений B03 у читателя не осталось ни одного.
UNBOUND_KNOBS = ()

#: Ранг состояния клетки: чем меньше, тем «сильнее». Лучшее из строк
#: одного продукта — то, чем закрывается вид и что показывает экран.
STATE_RANK = {"есть": 0, "заявлен": 1, "нет": 2}


class GateError(Exception):
    """Отказ самого гейта (нет исходников решателя, нет символа) —
    словами: молчаливого пропуска не бывает."""


# ------------------------------------------------------- чтение файлов
_TEXT: dict = {}


def source(rel: str) -> str:
    """Текст файла исходников ПО ПУТИ ОТ КОРНЯ РЕПОЗИТОРИЯ — тому, что
    названа строкой привязки (кэш на процесс)."""
    if rel not in _TEXT:
        path = REPO / rel
        try:
            _TEXT[rel] = _strip_comments(path.read_text(encoding="utf-8"))
        except OSError as e:
            raise GateError(
                f"исходник {rel} не читается ({e}) — привязку к решателю "
                f"проверить не по чему") from None
    return _TEXT[rel]


def tables():
    """Таблицы решателя — `vmp/kotlin_tables.py` (§8.1 проекта)."""
    return importlib.import_module("vmp.kotlin_tables")


def forget() -> None:
    """Сброс кэша текстов (тесты, правка исходников в процессе)."""
    _TEXT.clear()
    try:
        tables().forget()
    except Exception:                                         # noqa: BLE001
        pass


def _skip_quoted(text: str, i: int) -> int:
    """Позиция сразу за литералом, начинающимся в i («…» либо '…'):
    строковым либо СИМВОЛЬНЫМ. Символьные литералы Kotlin ('[' и ']' в
    RunThresholds) обязаны пропускаться так же, как строковые, иначе
    счёт скобок разъезжается на первом же из них."""
    quote, n = text[i], len(text)
    j = i + 1
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote:
            return j + 1
        j += 1
    return n


def _strip_comments(text: str) -> str:
    """Текст без комментариев Kotlin; строковые и символьные литералы
    сохраняются целиком (в них законно встречаются и «//», и запятые, и
    скобки)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = _skip_quoted(text, i)
            out.append(text[i:j])
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _balanced(text: str, open_at: int) -> str:
    """Тело скобок, открытых в позиции `open_at` (без самих скобок)."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    close = pairs[text[open_at]]
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_quoted(text, i)
            continue
        if c in pairs:
            depth += 1
        elif c == close and depth == 1:
            return text[open_at + 1:i]
        elif c in ")}]":
            depth -= 1
        i += 1
    raise GateError(f"незакрытая скобка в исходнике (позиция {open_at})")


# ------------------------------------------------ существование кода
def _module_name(file: str) -> str:
    """Путь файла от корня репозитория → имя модуля
    (`vmp/run/fit_checks.py` → `vmp.run.fit_checks`)."""
    return file.replace("\\", "/").removesuffix(".py").replace("/", ".")


def resolve_python(file: str, symbol: str):
    """Объект исполнителя на Python: импорт модуля по пути файла и
    `getattr` по точечному пути. Нет модуля или символа — GateError
    словами: привязка обещает код, которого нет."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        obj = importlib.import_module(_module_name(file))
    except Exception as e:                                    # noqa: BLE001
        raise GateError(f"модуль {file} не импортируется: {e}") from None
    for part in str(symbol).split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise GateError(f"в {file} нет символа {symbol!r} (не нашлось "
                            f"{part!r})") from None
    return obj


def _fun_body(text: str, method: str) -> str | None:
    """Объявление метода `fun <method>(` до следующего `fun` — область,
    где литерал ограничения ещё принадлежит этому методу."""
    m = re.search(r"\bfun\s+" + re.escape(method) + r"\s*\(", text)
    if m is None:
        return None
    nxt = re.search(r"\bfun\s+[A-Za-z_]", text[m.end():])
    return text[m.start():m.end() + (nxt.start() if nxt else len(text))]


def check_kotlin_constraint(file: str, symbol: str, constraint,
                            label=None) -> list[str]:
    """Метод провайдера объявлен и даёт ограничение с этим именем (либо
    `.asConstraint("…")`, либо аргументом `perEmployeeAdjacency`); при
    заявленной подписи — она стоит в `hardWeights()`."""
    out: list[str] = []
    text = source(file)
    method = str(symbol).split(".")[-1]
    body = _fun_body(text, method)
    if body is None:
        return [f"{file}: нет метода `fun {method}(`"]
    if f'"{constraint}"' not in body:
        out.append(f"{file}:{method}: имени ограничения \"{constraint}\" в "
                   f"теле метода нет (ни .asConstraint, ни аргументом "
                   f"perEmployeeAdjacency)")
    from unicode_data import confusable_hint
    known = tables().constraint_literals()
    if constraint not in known:
        out.append(f"{file}: имени ограничения \"{constraint}\" нет среди "
                   f"литералов ограничений провайдера"
                   + confusable_hint(constraint, known))
    if label:
        weights = ""
        m = re.search(r"\bfun\s+hardWeights\s*\(", text)
        if m is not None:
            nxt = re.search(r"\b(?:linkedMapOf|mapOf)\s*\(", text[m.end():])
            if nxt is not None:
                weights = _balanced(text, m.end() + nxt.end() - 1)
        if f'"{label}"' not in weights:
            out.append(f"{file}: подписи \"{label}\" нет в hardWeights()")
    return out


def _knob_row(param: str) -> dict | None:
    """Строка таблиц решателя по имени параметра: `KNOBS` либо (для
    параметра стыковки) обе ручки `SIT_KNOBS` разом."""
    tbl = tables()
    if param == tbl.sit_param():
        sit = list(tbl.sit_knobs().values())
        if not sit:
            return None
        return {"threshold": sorted(v["threshold"] for v in sit),
                "unit": sit[0]["unit"], "relation": sit[0]["relation"],
                "keyed": any(v["keyed"] for v in sit),
                "accepts": sorted({a for v in sit for a in v["accepts"]})}
    return tbl.knobs().get(param)


def check_kotlin_knob(symbol: str, param, threshold, keyed=None, unit=None,
                      relation=None) -> list[str]:
    """Ручка решателя: символ объявлен в `object RunThresholds`, имя
    параметра стоит литералом в RunThresholds либо TaskPackage, каждый
    ключ порога — литерал RunThresholds либо константа семейства
    резерва в провайдере. У ручек таблиц KNOBS/SIT_KNOBS сверяются и
    поля строки (порог, единица, отношение, признак семейства ключей):
    их генератор берёт из Kotlin, и расхождение означало бы правку
    файла привязок мимо генератора."""
    out: list[str] = []
    rt, tp, prov = (source(RUN_THRESHOLDS_FILE), source(TASK_PACKAGE_FILE),
                    source(PROVIDER_FILE))
    name = str(symbol).split(".")[-1]
    obj = re.search(r"\bobject\s+RunThresholds\s*\{", rt)
    scope = _balanced(rt, obj.end() - 1) if obj else rt
    if not re.search(r"\b(?:const\s+val|val|var|fun)\s+" + re.escape(name)
                     + r"\b", scope):
        out.append(f"{RUN_THRESHOLDS_FILE}: в object RunThresholds нет "
                   f"объявления {name!r}")
    if f'"{param}"' not in rt and f'"{param}"' not in tp:
        out.append(f"имени параметра \"{param}\" нет литералом ни в "
                   f"{RUN_THRESHOLDS_FILE}, ни в {TASK_PACKAGE_FILE}")
    keys = list(threshold) if isinstance(threshold, list) else [threshold]
    reserve = tables().constants().get("RESERVE_FAMILY")
    for key in keys:
        if not key:
            out.append(f"ручка {param!r}: ключ порога не назван")
        elif f'"{key}"' in rt:
            continue
        elif key == reserve and f'"{key}"' in prov:
            continue
        else:
            out.append(f"ключа порога \"{key}\" нет литералом в "
                       f"{RUN_THRESHOLDS_FILE} (и это не RESERVE_FAMILY "
                       f"провайдера)")
    row = _knob_row(str(param))
    if row is not None:
        want = row["threshold"]
        got = sorted(keys) if isinstance(threshold, list) else keys[0]
        if (sorted(want) if isinstance(want, list) else want) != got:
            out.append(f"ручка {param!r}: порог в привязке {threshold!r}, в "
                       f"таблице решателя {want!r}")
        if unit is not None and unit != row["unit"]:
            out.append(f"ручка {param!r}: единица {unit!r}, в таблице "
                       f"решателя {row['unit']!r}")
        if relation is not None and relation != row["relation"]:
            out.append(f"ручка {param!r}: отношение {relation!r}, в таблице "
                       f"решателя {row['relation']!r}")
        if keyed is not None and bool(keyed) != bool(row["keyed"]):
            out.append(f"ручка {param!r}: признак семейства ключей "
                       f"{bool(keyed)}, в таблице решателя {row['keyed']}")
    return out


def check_kotlin_form(file: str, symbol: str, form) -> list[str]:
    """Форма порогов пакета: метод разбора объявлен, имя секции стоит
    литералом в том же файле."""
    out: list[str] = []
    text = source(file)
    method = str(symbol).split(".")[-1]
    if _fun_body(text, method) is None:
        out.append(f"{file}: нет метода `fun {method}(`")
    if f'"{form}"' not in text:
        out.append(f"{file}: имени формы \"{form}\" нет литералом")
    return out


def check_kotlin_logic_form(file: str, symbol: str, form) -> list[str]:
    """Форма правила логики: пара «константа `FORM_<форма>` = <форма>» и
    метод, её исполняющий."""
    out: list[str] = []
    text = source(file)
    method = str(symbol).split(".")[-1]
    if _fun_body(text, method) is None:
        out.append(f"{file}: нет метода `fun {method}(`")
    if tables().logic_forms().get(str(form)) != f"FORM_{form}":
        out.append(f"{file}: нет константы `const val FORM_{form} = "
                   f"\"{form}\"`")
    return out


def check_kotlin_metric(file: str, symbol: str, metric, constraint=None,
                        tasks=None, refusal=None,
                        approximation=None) -> list[str]:
    """Метрика решателя: имя есть в словаре METRIC_EXECUTORS, а
    ограничение, задачи, отказ и приближение равны его записи дословно."""
    out: list[str] = []
    text = source(file)
    name = str(symbol).split(".")[-1]
    if not re.search(r"\bval\s+" + re.escape(name) + r"\b", text):
        out.append(f"{file}: нет объявления `val {name}`")
    row = tables().metrics().get(str(metric))
    if row is None:
        return out + [f"{file}: метрики \"{metric}\" нет в METRIC_EXECUTORS"]
    if constraint is not None and constraint != row["constraint"]:
        out.append(f"метрика {metric!r}: ограничение {constraint!r}, в "
                   f"METRIC_EXECUTORS {row['constraint']!r}")
    if tasks is not None and sorted(tasks) != sorted(row["tasks"]):
        out.append(f"метрика {metric!r}: задачи {sorted(tasks)}, в "
                   f"METRIC_EXECUTORS {sorted(row['tasks'])}")
    if (refusal or None) != (row["refusal"] or None):
        out.append(f"метрика {metric!r}: отказ решателя в привязке не "
                   f"дословен тексту METRIC_EXECUTORS")
    if row["approximation"] and approximation != row["approximation"]:
        out.append(f"метрика {metric!r}: приближение в привязке не дословно "
                   f"тексту METRIC_EXECUTORS")
    return out


def resolve(ex: dict) -> list[str]:
    """Диспетчер по виду: пусто — исполнитель на месте. Строки «нет» и
    «заявлен» кода не обещают и не судятся."""
    if not isinstance(ex, dict) or ex.get("state") != "есть":
        return []
    kind, file, symbol = ex.get("kind"), ex.get("file"), ex.get("symbol")
    if not (isinstance(file, str) and file and isinstance(symbol, str)
            and symbol):
        return [f"вид {kind}: у строки «есть» нет файла или символа"]
    if kind in KOTLIN_KINDS:
        if ex.get("lang") != "kotlin":
            return [f"{file}:{symbol}: вид {kind} — язык kotlin, а объявлен "
                    f"{ex.get('lang')!r}"]
        try:
            if kind == "kotlin_constraint":
                return check_kotlin_constraint(file, symbol,
                                               ex.get("constraint"),
                                               ex.get("label"))
            if kind == "kotlin_knob":
                return check_kotlin_knob(symbol, ex.get("param"),
                                         ex.get("threshold"), ex.get("keyed"),
                                         ex.get("unit"), ex.get("relation"))
            if kind == "kotlin_form":
                return check_kotlin_form(file, symbol, ex.get("form"))
            if kind == "kotlin_logic_form":
                return check_kotlin_logic_form(file, symbol, ex.get("form"))
            return check_kotlin_metric(file, symbol, ex.get("metric"),
                                       ex.get("constraint"), ex.get("tasks"),
                                       ex.get("refusal"),
                                       ex.get("approximation"))
        except Exception as e:                                # noqa: BLE001
            return [f"{file}:{symbol}: таблицы решателя не прочитаны ({e})"]
    if ex.get("lang") != "python":
        return [f"{file}:{symbol}: вид {kind} — язык python, а объявлен "
                f"{ex.get('lang')!r}"]
    try:
        obj = resolve_python(file, symbol)
    except GateError as e:
        return [str(e)]
    if not callable(obj):
        return [f"{file}:{symbol} — не вызываемый объект "
                f"({type(obj).__name__})"]
    return []


# ------------------------------------------------------------- гейт
@dataclass
class GateReport:
    """Итог гейта: ошибки словами, матрица «запись × вид», перечень
    продуктов и то, объявил ли свои привязки CDPM."""

    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    matrix: dict = field(default_factory=dict)
    products: list = field(default_factory=list)
    zones: dict = field(default_factory=dict)
    cdpm_declared: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


def zone_of(doc: dict) -> set:
    """Зона ответственности продукта: поле `executor_kinds` шапки, а без
    поля — виды ВСЕХ строк файла (в том числе «нет»: продукт с одними
    «нет» иначе оказался бы вне зоны — дополнение §3 п.4)."""
    declared = doc.get("executor_kinds")
    if isinstance(declared, list) and declared:
        return {str(k) for k in declared}
    return {str(ex.get("kind")) for row in (doc.get("bindings") or [])
            if isinstance(row, dict)
            for ex in (row.get("executors") or [])
            if isinstance(ex, dict) and ex.get("kind")}


def best_states(doc: dict) -> dict:
    """{(section, id): {kind: лучшее состояние строк продукта}} —
    «есть» > «заявлен» > «нет»; порядок строк на клетку не влияет."""
    out: dict = {}
    for row in doc.get("bindings") or []:
        if not isinstance(row, dict):
            continue
        cell = out.setdefault((str(row.get("section")), str(row.get("id"))), {})
        for ex in row.get("executors") or []:
            if not isinstance(ex, dict) or not ex.get("kind"):
                continue
            kind, state = str(ex["kind"]), str(ex.get("state"))
            best = cell.get(kind)
            if best is None or STATE_RANK.get(state, 9) < \
                    STATE_RANK.get(best, 9):
                cell[kind] = state
    return out


def records_of(catalog) -> list:
    """Записи каталога со статусом «есть»: семьи по id, затем критерии."""
    return ([("families", r) for r in catalog.families_full()]
            + [("criteria", r) for r in catalog.criteria().values()])


def declared_kinds(record: dict, section: str) -> list:
    """Виды исполнителей, ОБЪЯВЛЕННЫЕ записью (у критериев поле зовётся
    `computable_by`)."""
    key = "executor_kinds" if section == "families" else "computable_by"
    return [str(k) for k in (record.get(key) or [])]


def gate(catalog, cosmos: dict, cdpm: dict | None = None) -> GateReport:
    """Полный гейт продукта: символы, «параметр ⇒ читатель», полнота
    видов по зонам. Каталог и документы уже прочитаны — гейт ничего не
    открывает сам."""
    rep = GateReport(cdpm_declared=cdpm is not None)
    docs = [(str(cosmos.get("product") or "COSMOS"), cosmos)]
    if cdpm is not None:
        docs.append((str(cdpm.get("product") or "CDPM"), cdpm))
    rep.products = [p for p, _ in docs]
    rep.zones = {p: zone_of(d) for p, d in docs}
    states = {p: best_states(d) for p, d in docs}

    # существование кода — только свои строки: чужой код гейт не судит
    for row in cosmos.get("bindings") or []:
        if not isinstance(row, dict):
            continue
        addr = f"{row.get('section')}/{row.get('id')}"
        for j, ex in enumerate(row.get("executors") or []):
            for word in resolve(ex):
                rep.errors.append(f"{addr} исполнитель {j}: {word}")

    # «параметр записи ⇒ исполнитель-читатель»
    readers: dict = {}
    for row in cosmos.get("bindings") or []:
        if not isinstance(row, dict) or row.get("section") != "families":
            continue
        names = readers.setdefault(str(row.get("id")), set())
        for ex in row.get("executors") or []:
            if not isinstance(ex, dict) or ex.get("state") != "есть":
                continue
            if ex.get("kind") == "kotlin_knob" and ex.get("param"):
                names.add(str(ex["param"]))
            for p in ex.get("params") or []:
                names.add(str(p))
    for section, rec in records_of(catalog):
        if section != "families":
            continue
        rid = str(rec.get("id"))
        for p in rec.get("params") or []:
            name = str((p or {}).get("name"))
            if name not in readers.get(rid, set()):
                rep.errors.append(
                    f"families/{rid}: параметр {name!r} записан каталогом, а "
                    f"строки «есть» с его чтением в привязках COSMOS нет "
                    f"(гейт «параметр записи ⇒ исполнитель-читатель»)")

    # полнота видов по зонам (правило B04 дополнения §3)
    for section, rec in records_of(catalog):
        rid = str(rec.get("id"))
        key = (section, rid)
        for kind in declared_kinds(rec, section):
            cells = {p: states[p].get(key, {}).get(kind) for p in rep.products}
            rep.matrix.setdefault(f"{section}/{rid}", {})[kind] = dict(cells)
            if any(s == "есть" for s in cells.values()):
                continue
            resp = [p for p in rep.products if kind in rep.zones[p]]
            if not resp:
                rep.errors.append(
                    f"{section}/{rid}: вид {kind} никем не исполняется и ни "
                    f"в чьей зоне (зоны: " + "; ".join(
                        f"{p} — {sorted(rep.zones[p])}" for p in rep.products)
                    + ")")
                continue
            if any(cells.get(p) is None for p in resp):
                words = ", ".join(
                    f"{p} — " + (cells[p] if cells.get(p) else
                                 ("в зоне, но не объявил"
                                  if kind in rep.zones[p] else "вне зоны"))
                    for p in rep.products)
                rep.errors.append(f"{section}/{rid}: вид {kind}: {words}")
    if cdpm is None:
        rep.notes.append("привязки CDPM не объявлены (cdpm/bindings.json "
                         "нет) — B04 по зоне COSMOS")
    return rep


# ----------------------------------------------------------- запуск
def _load(path: Path) -> dict:
    from template_catalog.bindings import load_bindings
    return load_bindings(path)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cosmos_path = Path(argv[0]) if argv else REPO / "vmp" / "bindings.json"
    cdpm_path = (Path(argv[1]) if len(argv) > 1
                 else REPO / "cdpm" / "bindings.json")
    try:
        from vmp import catalog_access as ca
        catalog = ca.current()
        cosmos = _load(cosmos_path)
        cdpm = _load(cdpm_path) if cdpm_path.is_file() else None
        rep = gate(catalog, cosmos, cdpm)
    except Exception as e:                                    # noqa: BLE001
        print(f"гейт привязок COSMOS не пройден: {e}")
        return 1
    print(f"гейт привязок COSMOS: каталог {catalog.version()} | отпечаток "
          f"{catalog.fingerprint()} | записей {len(rep.matrix)} | продукты "
          f"{', '.join(rep.products)}")
    width = max([len(a) for a in rep.matrix] + [6])
    for addr in sorted(rep.matrix):
        shown = []
        for kind in sorted(rep.matrix[addr]):
            seen = [s for s in rep.matrix[addr][kind].values() if s]
            mark = (min(seen, key=lambda s: STATE_RANK.get(s, 9)) if seen
                    else "— (не объявлено)")
            shown.append(f"{kind}: {mark}")
        print(f"  {addr.ljust(width)}  " + " | ".join(shown))
    for note in rep.notes:
        print("  " + note)
    for word in rep.errors:
        print("  ОШИБКА " + word)
    print(f"итог: ошибок {len(rep.errors)}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
