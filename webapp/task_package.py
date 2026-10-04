"""Мост CDPM -> COSMOS, этап А: приём «Пакета постановки задачи».

Пакет — единица обмена между модулем формализации документов
авиакомпании (CDPM) и решателем: правила, целевые функции, веса мягких
правил, разрешённые режимы задач и очищенные операционные данные одним
самодостаточным артефактом с контрольными суммами. Формат закреплён
спецификацией `docs/spec-postanovka-cosmos-v1.md`; её владелец — сессия
CDPM, здесь формат только ЧИТАЕТСЯ.

Обязанности этого модуля (спецификация §7, условия МУ-02/МУ-04):

* принять пакет только через ГЕЙТ производителя
  (`python -m cdpm.gates.gate_task_package`) — он сверяет суммы всех
  файлов, схему модели и ПЕРЕСЧИТЫВАЕТ разрешённые режимы из модели,
  поэтому ловится и подмена самого манифеста;
* превратить очищенные данные пакета в набор решателя, а неполноту
  назвать поимённо, а не подставить умолчания;
* дать отпечаток постановки для подписи прогона.

Всякий отказ — [TaskPackageRefused] с причиной по-русски. Молчаливых
умолчаний в этом модуле нет: пакет либо принят целиком, либо отвергнут.
Даты всех дато-колонок книги («Дата», «С», «По», «Действует с/до»)
читает один разборщик [parse_package_date] (аудит A7D, 04.09.2026):
неизвестное не превращается в отсутствие ограничения — непустое
нечитаемое значение это отказ с адресом ячейки, а не открытое окно
допуска и не пропавший отпуск.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from webapp import rule_maps
from vmp.data_contract import (
    SHEET_FLIGHTS, SHEET_CREW, SHEET_QUALS, SHEET_VACATIONS, SHEET_DUTIES,
)

SPEC_MAJOR = 1
GATE_MODULE = "cdpm.gates.gate_task_package"

# Листы книги данных, которые умеет читать конвертер этапа А, и их
# обязательные колонки. Лист, которого нет в этом перечне, в набор не
# переносится и называется в отчёте — «формализовано, не исполняется».
#: Верхняя граница ОТКРЫТОГО окна допуска (минуты эпохи UTC) — та же, что
#: у другого ввода данных (airline_ingest._OPEN_HI): «срок не указан» не
#: значит «просрочен», и подставлять сюда сегодняшнюю дату нельзя.
QUAL_OPEN_END_MIN = 4_000_000_000 // 60

# Колонка типа воздушного судна на листе рейсов: появилась в
# книгах данных с ревизии наборов v3. Старые книги её не несут —
# правило допуска по типу тогда исполнять не на чем, и это
# называется в отчёте, а не подменяется умолчанием.
COL_AIRCRAFT_TYPE = "Тип ВС"

REQUIRED_COLUMNS = {
    SHEET_FLIGHTS: ("Номер рейса", "Дата", "Вылет", "Время вылета",
                    "Прилёт", "Время прилёта"),
    SHEET_CREW: ("Табельный номер", "База", "Должность"),
}

# Правила модели, из которых берётся ЧИСЛЕННОСТЬ экипажа места. Состав
# мест в книге данных не задан, а придумывать его нельзя: численность —
# нормативное свойство постановки, и она приходит из правил пакета.
# ЛЕГАСИ-таблица: пакет ревизии 1.14 несёт перечень этих правил СЕКЦИЕЙ
# rule_maps.crew_size_rules манифеста, и тогда сканируется она (см.
# crew_size_from_model); кортеж остаётся для пакетов без секции.
CREW_SIZE_RULES = ("R-S1-табл-2-строка-3-кол-3", "R-S3-стр-1-абз-7")
# Формулировки свода порождает языковая модель и МЕЖДУ ПРОГОНАМИ они
# меняются законно: одно и то же правило приходило как «crew_size = 2»,
# «|пилоты в экипаже| = 2», «pilot_count(flight_duty) = 2» и «Экипаж
# состоит из 2 пилотов». Идентификатор правила при этом не меняется,
# поэтому образцы держат РАЗНЫЕ записи одного смысла. Широкий образец
# стоит ПОСЛЕДНИМ: он же и самый неразборчивый.
# ЗАПАСНОЙ ПУТЬ. С версии 1.2 численность приходит машинным полем
# manifest.crew_size, и распознавание — у производителя (его свод, его
# словарь). Образцы остаются только для пакетов версии до 1.2; чинить
# их под новые формулировки больше не нужно и не следует.
CREW_SIZE_PATTERNS = (
    r"crew_size\s*=\s*(\d+)",
    r"pilot_count\s*\([^)]*\)\s*=\s*(\d+)",
    r"\|\s*пилот[^|]*\|\s*=\s*(\d+)",
    r"\|\s*экипаж[^|]*\|\s*=\s*(\d+)",
    r"(?:состоит из|входят|входит|составляет)\s*(\d+)\s*пилот",
    r"(\d+)\s*пилот",
)

# Порядок ролей в составе: старшая позиция первой — этап 2 сажает
# экипаж на места по позиции.
ROLE_ORDER = ("Captain", "FirstOfficer", "FlightAttendant")

# Должность в книге данных -> роль места в наборе решателя.
ROLE_BY_POSITION = {
    "командир": "Captain",
    "кв": "Captain",
    "квс": "Captain",
    "командир воздушного судна": "Captain",
    "второй пилот": "FirstOfficer",
    "бортпроводник": "FlightAttendant",
    "старший бортпроводник": "FlightAttendant",
}


class TaskPackageRefused(RuntimeError):
    """Пакет не принят. Вызывающий обязан отказать прогону с этой
    причиной, а не продолжать на умолчаниях."""

    def __init__(self, *args, code: str | None = None, problems=()):
        super().__init__(*args)
        self.code = code
        self.problems = list(problems)


def check_family_projection(pkg: dict) -> list[dict]:
    """Re-read received model/catalogue, independently of producer gate.

    Legacy absence is explicitly unjudged, never a claim of completeness.
    This check also runs when load(run_gate=False) reads the actual package.
    """
    from cdpm import catalog_bridge
    from cdpm import family_manifest
    manifest = pkg["manifest"]
    if "families" not in manifest:
        pkg["family_projection"] = {
            "judged": False, "words": "Полнота семей в старом пакете не объявлена"}
        return []
    if not isinstance(pkg.get("model"), dict):
        raise TaskPackageRefused("model.json не объект — полноту семей сверить нечем",
                                 code=family_manifest.MISMATCH)
    try:
        problems = family_manifest.check(pkg["model"], manifest)
    except catalog_bridge.CATALOG_ERRORS as exc:
        raise TaskPackageRefused(str(exc), code="catalog-unavailable") from exc
    if problems:
        raise TaskPackageRefused("; ".join(p["words"] for p in problems),
                                 code=problems[0]["code"], problems=problems)
    pkg["family_projection"] = {
        "judged": True, "problems": [],
        "words": "Полнота семей сверена по модели и каталогу; это не допуск исполнения"}
    return []


def check_support_projection(pkg: dict) -> list[dict]:
    """Re-read local evidence even when the separate producer gate is off."""
    from cdpm import support_manifest
    manifest = pkg["manifest"]
    if not support_manifest.judged(manifest):
        pkg["support_projection"] = {
            "judged": False, "words": "Свидетельство support в старом пакете не судится"}
        return []
    if not isinstance(pkg.get("model"), dict):
        raise TaskPackageRefused("model.json не объект — support сверить нечем",
                                 code=support_manifest.MISMATCH)
    problems = support_manifest.check(pkg["model"], manifest)
    if problems:
        raise TaskPackageRefused("; ".join(p["words"] for p in problems),
                                 code=problems[0]["code"], problems=problems)
    pkg["support_projection"] = {
        "judged": True, "problems": [],
        "words": "Снимок support сверён с местным свидетельством; это не допуск исполнения"}
    return []


def check_objective_projection(pkg: dict, *, catalog=None) -> list[dict]:
    """Проверка своего model/objective даже без отдельного producer gate.

    Ревизии до 1.20 — явно несудимое архивное чтение (§12.9), не PASS.
    Это подготовка приёмника; выпуск 1.20 включается только после каталога.
    """
    from vmp import catalog_access as ca, objective_projection as op
    spec = (pkg.get("manifest") or {}).get("spec_version")
    if not op.judged_for_spec(spec):
        pkg["objective_projection"] = {
            "judged": False, "spec_version": spec,
            "words": op.archive_words(spec)}
        return []
    if not isinstance(pkg.get("model"), dict):
        raise TaskPackageRefused(
            "model.json не объект — сверить целевую нечем",
            code=op.CODE_MISMATCH)
    try:
        cat = ca.current() if catalog is None else catalog
        # Recompute from model.json via the same reader as the producer.
        # The objective being checked must never supply its own expected rate.
        from cdpm import model as model_reader
        context = {"criterion": model_reader.derive_pairing_criterion(pkg["model"])}
        problems = op.compare(pkg.get("objective"), pkg["model"], cat, context=context)
        # Human explanations must come from the same trusted model/catalogue,
        # not the unchecked direction_basis prose in the submitted objective.
        expected = op.project(pkg["model"], cat, context=context)
    except (op.ObjectiveProjectionError, *ca.CATALOG_ERRORS) as exc:
        raise TaskPackageRefused(str(exc), code=op.CODE_DIRECTION) from exc
    if problems:
        code = (op.CODE_DIRECTION if any(p["code"] == op.CODE_DIRECTION
                                        for p in problems) else op.CODE_MISMATCH)
        raise TaskPackageRefused(op.problem_words(problems), code=code,
                                 problems=problems)
    pkg["objective_projection"] = {
        "judged": True, "spec_version": spec, "problems": [],
        "catalog": expected["catalog"],
        "words": "Проекция сверена в судимой части; это не является разрешением исполнения.",
        "directions": [{"task": task, "direction": expected[task]["direction"],
                        "basis": expected[task]["direction_basis"],
                        "criterion": ({"kind": expected[task]["criterion"]["kind"],
                                       "direction": expected[task]["criterion"]["direction"]}
                                      if expected[task].get("criterion") else None)}
                       for task in op.TASKS]}
    return []


# --------------------------------------------------------------- чтение

def projection_status(path) -> dict:
    """Read-only presentation for run cards, never execution authorization.

    Re-read the actual package, not a stale button log or a submitted explanation.
    An unavailable/invalid package leaves the historical run record readable,
    with an explicit unknown/refused projection instead of a fabricated success.
    """
    if path is None or not Path(path).is_file():
        return {"judged": None, "code": "package-bytes-missing",
                "words": "Статус сверки проекции не установлен: файл пакета недоступен"}
    try:
        return load(path, run_gate=False)["objective_projection"]
    except Exception as exc:  # A display failure must not hide the run record.
        return {"judged": None, "code": getattr(exc, "code", "package-unreadable"),
                "words": str(exc)}


def _read_members(path: Path) -> dict[str, bytes]:
    """Файлы пакета: каталог или zip. Корень пакета плоский; вложенные
    записи (дополнение к 1.17 от 06.09.2026: клаузы заморозки
    freeze/clauses/<sid>.json) доступны по полному имени с «/», как их
    называет manifest.files, и по короткому имени — первым, если оно не
    занято файлом корня."""
    def _put(out: dict, rel: str, body: bytes) -> None:
        rel = rel.replace("\\", "/").lstrip("/")
        out.setdefault(rel, body)
        short = rel.rsplit("/", 1)[-1]
        if "/" in rel:
            out.setdefault(short, body)
        else:
            out[short] = body
    if path.is_dir():
        out: dict[str, bytes] = {}
        for p in sorted(path.rglob("*")):
            if p.is_file():
                _put(out, str(p.relative_to(path)), p.read_bytes())
        return out
    try:
        with zipfile.ZipFile(path) as z:
            out = {}
            for info in z.infolist():
                if info.is_dir():
                    continue
                _put(out, info.filename, z.read(info))
            return out
    except (OSError, zipfile.BadZipFile) as exc:
        raise TaskPackageRefused(
            "пакет не читается: %s (%s)" % (path, exc)) from exc


def run_producer_gate(path: Path, python: str | None = None, *, landing_compat=None) -> str:
    """Обязательный гейт производителя. Возвращает его вывод; красный
    гейт — отказ прогона (спецификация §8, условие МУ-02).

    Гейт запускается ТЕМ ЖЕ интерпретатором и из корня репозитория:
    модуль cdpm лежит там, и запуск из чужого каталога дал бы «модуль не
    найден» — отказ по неверной причине хуже, чем отсутствие проверки.
    """
    repo = Path(__file__).resolve().parents[1]
    exe = python or sys.executable
    command = [exe, "-m", GATE_MODULE, str(path)]
    if landing_compat is not None:
        if type(landing_compat) is not int or landing_compat != 1:
            raise TaskPackageRefused("landing_compat допускает только целое 1",
                                     code="landing-version")
        command += ["--landing-compat", "1"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True, cwd=str(repo), timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaskPackageRefused(
            "гейт пакета не запустился: %s" % exc) from exc
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    if proc.returncode != 0:
        raise TaskPackageRefused(
            "гейт пакета отверг постановку (код %d):\n%s"
            % (proc.returncode, out.strip()[-2000:]))
    return out


def check_support_execution(pkg: dict, *, task: str, mode: str) -> None:
    """K06 family/criterion proof before a selected COSMOS executor receives data."""
    from cdpm import support_manifest
    from vmp.version import PRODUCT, PRODUCT_VERSION
    problems = support_manifest.execution_problems(
        pkg.get("model") or {}, pkg.get("manifest") or {},
        task=task, mode=mode, product=PRODUCT, product_version=PRODUCT_VERSION)
    if problems:
        raise TaskPackageRefused("; ".join(p["words"] for p in problems),
            code=problems[0]["code"], problems=problems)


def check_landing_projection(pkg: dict, *, landing_compat=None, for_execution=False) -> list[dict]:
    """Refuse landing-version/landing-incomplete without repairing raw data."""
    from cdpm import landing_contract
    result = landing_contract.inspect(pkg.get("model"), pkg.get("manifest"),
        landing_compat=landing_compat, for_execution=for_execution)
    if result["problems"]:
        raise TaskPackageRefused("; ".join(p["words"] for p in result["problems"]),
            code=result["problems"][0]["code"], problems=result["problems"])
    pkg["landing_projection"] = {
        **result, "words": ("Архивное чтение landing/1; исполнение запрещено"
                            if result["archive_only"] else
                            "Форма landing/2 сверена; это не разрешение исполнения")}
    return []


def load(path: str | Path, *, run_gate: bool = True, landing_compat=None) -> dict:
    """Принять пакет: гейт производителя, версия, суммы, разбор.

    Возвращает словарь с манифестом, критерием, моделью, очищенными
    данными и отпечатком постановки (SHA-256 манифеста).
    """
    p = Path(path)
    if not p.exists():
        raise TaskPackageRefused("пакет не найден: %s" % p)
    gate_output = (run_producer_gate(p, landing_compat=landing_compat)
                   if run_gate else None)

    members = _read_members(p)
    if {"component-manifest.json", "declared-world.json", "declared-expected.json"} & set(members):
        raise TaskPackageRefused(
            "component-only: component input is not an executable task package",
            code="component-only")
    raw_manifest = members.get("manifest.json")
    if raw_manifest is None:
        raise TaskPackageRefused("в пакете нет manifest.json")
    from cdpm import model as m
    try:
        manifest = m.loads_strict(raw_manifest)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TaskPackageRefused("manifest.json не разбирается: %s" % exc)
    if not isinstance(manifest, dict):
        raise TaskPackageRefused("manifest.json не объект", code="landing-contract")
    if manifest.get("schema") == "component-input/1" or manifest.get("scope") == "component-only":
        raise TaskPackageRefused(
            "component-only: renamed component input is not an executable task package",
            code="component-only")

    spec = str(manifest.get("spec_version") or "")
    major = spec.split(".")[0]
    if not major.isdigit() or int(major) != SPEC_MAJOR:
        raise TaskPackageRefused(
            "версия спецификации «%s» не поддержана: этот код понимает "
            "старшую версию %d" % (spec, SPEC_MAJOR))

    # Сверка сумм повторяется здесь, а не доверяется гейту: гейт —
    # отдельный процесс, и между его выходом и нашим чтением файл может
    # смениться. Проверка дешёвая, окно подмены закрывается.
    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise TaskPackageRefused("в манифесте нет перечня files")
    checked = 0
    for entry in listed:
        name = (entry or {}).get("name")
        want = (entry or {}).get("sha256")
        if not name or not want:
            raise TaskPackageRefused("в перечне files запись без name/sha256")
        body = members.get(name)
        if body is None:
            raise TaskPackageRefused(
                "файл %s объявлен манифестом, но отсутствует в пакете" % name)
        got = hashlib.sha256(body).hexdigest()
        if got.lower() != str(want).lower():
            raise TaskPackageRefused(
                "контрольная сумма %s не совпала: манифест %s, факт %s"
                % (name, want, got))
        checked += 1

    def _json(name: str, required: bool = True):
        raw = members.get(name)
        if raw is None:
            if required:
                raise TaskPackageRefused("в пакете нет %s" % name)
            return None
        try:
            # Strict parsing only: loads_model would migrate the evidence.
            return m.loads_strict(raw) if name == "model.json" else json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TaskPackageRefused("%s не разбирается: %s" % (name, exc))

    pkg = {
        "path": str(p),
        "fingerprint": hashlib.sha256(raw_manifest).hexdigest(),
        "manifest": manifest,
        "objective": _json("objective.json"),
        "model": _json("model.json"),
        "clean_data": _json("clean-data.json", required=False),
        # Форма ВМП пакета (структура владельца 2026-09-02): ярусы
        # критериев судит матрица готовности и приёмник; пакеты до
        # формы её не несут — None.
        "form": _json("vmp-form.json", required=False),
        "files_checked": checked,
        "gate_output": gate_output,
    }
    check_landing_projection(pkg, landing_compat=landing_compat)
    check_family_projection(pkg)
    check_support_projection(pkg)
    check_objective_projection(pkg)
    return pkg


# ------------------------------------------------------------ конвертер

# Даты в книге данных пакета (аудит A7D, 04.09.2026). Класс ошибки:
# соседние дато-колонки одного пакета судились разными правилами —
# «Дата» рейса при нечитаемости отказывала, нечитаемая «Действует до»
# молча давала ОТКРЫТОЕ окно допуска, нечитаемый отпуск молча
# пропадал. Неизвестное превращалось в отсутствие ограничения:
# сотрудник в отпуске становился «доступным», просроченный допуск —
# «бессрочным». Теперь все дато-колонки читает ОДИН разборщик.
#
# Канонические формы — ровно те две, что принимает гейт данных CDPM
# (cdpm.data_checks.parse_date): «дд.мм.гггг» (так
# cdpm.data_freeze._cell_str замораживает ячейки-даты Excel) и ISO
# «гггг-мм-дд» (так пишут текстовые книги). Хвост времени допустим
# только нулевой («00:00», «T00:00:00»): дато-колонка несёт СУТКИ, а
# не момент, и «31.03.2027 12:00» в «Действует до» означало бы не то,
# что подумает читатель. Пустая ячейка — None, смысл пустоты решает
# вызывающий (у «Действует до» это открытое окно, у отпуска — отказ);
# непустое нечитаемое — отказ с листом, строкой и колонкой.
_DATE_FORMS = (
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "%d.%m.%Y"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
)
_ZERO_TIME_RE = re.compile(r"^00:00(?::00)?$")
#: Дато-колонки пакета, которые читает конвертер, по листам.
DATE_COLUMNS = {
    SHEET_FLIGHTS: ("Дата",),
    SHEET_QUALS: ("Действует с", "Действует до"),
    SHEET_VACATIONS: ("С", "По"),
}
_EPOCH = datetime(1970, 1, 1)


def _addr(sheet: str, row_no: int, column: str | None = None) -> str:
    """Адрес ячейки книги словами: счёт строк — по строкам ДАННЫХ,
    без заголовка (номеров исходных строк Excel очищенные данные
    не несут)."""
    where = "лист «%s», строка данных %d" % (sheet, row_no)
    return where + (", колонка «%s»" % column if column else "")


def parse_package_date(value, *, sheet: str, row: int,
                       column: str) -> datetime | None:
    """Дата дато-колонки пакета: None — ячейка пуста; datetime —
    полночь указанных суток; непустое нечитаемое — TaskPackageRefused
    с адресом. Единственный разборщик дат конвертера: у «Дата» рейса,
    «С»/«По» отпуска и «Действует с/до» допуска одно и то же правило."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    head = re.split(r"[ T]", text, maxsplit=1)[0]
    tail = text[len(head):].strip(" T")
    for pattern, fmt in _DATE_FORMS:
        if pattern.match(head):
            try:
                day = datetime.strptime(head, fmt)
            except ValueError as exc:
                raise TaskPackageRefused(
                    "%s: дата «%s» не существует в календаре (%s)"
                    % (_addr(sheet, row, column), text, exc)) from exc
            if tail and not _ZERO_TIME_RE.match(tail):
                raise TaskPackageRefused(
                    "%s: «%s» — момент времени, а не дата; дато-колонка "
                    "несёт сутки, время в ней должно быть пустым или "
                    "нулевым" % (_addr(sheet, row, column), text))
            return day
    raise TaskPackageRefused(
        "%s: нечитаемая дата «%s» — ожидается «дд.мм.гггг» или "
        "«гггг-мм-дд» (канонические формы пакета CDPM)"
        % (_addr(sheet, row, column), text))


def _epoch_min(moment: datetime) -> int:
    """Минуты эпохи UTC — единица окон допусков решателя."""
    return int((moment - _EPOCH).total_seconds() // 60)


def _parse_dt(date_s, time_s, *, row: int, column: str) -> str:
    """«2000-01-01»/«01.01.2000» + «12:00» -> ISO-момент набора решателя.

    Дата — общим разборщиком (аудит A7D); пустая дата у рейса — отказ:
    рейс без суток в расписание не ставится.
    """
    day = parse_package_date(date_s, sheet=SHEET_FLIGHTS, row=row,
                             column="Дата")
    if day is None:
        raise TaskPackageRefused(
            "%s: пуста «Дата» рейса" % _addr(SHEET_FLIGHTS, row, "Дата"))
    try:
        hh, mm = str(time_s).strip()[:5].split(":")
        return (day + timedelta(hours=int(hh), minutes=int(mm))).isoformat()
    except (ValueError, TypeError) as exc:
        raise TaskPackageRefused(
            "%s: нечитаемые дата/время «%s %s»: %s"
            % (_addr(SHEET_FLIGHTS, row, column), date_s, time_s, exc))


def _rows(tables: dict, sheet: str) -> list[dict]:
    return [r for _, r in _rows_addr(tables, sheet)]


_LAT_KEYS = ("Широта", "latitude", "Latitude", "lat", "Lat")
_LON_KEYS = ("Долгота", "longitude", "Longitude", "lon", "Lon", "lng")


def _airport_point(row: dict) -> tuple[float, float] | None:
    """Координаты строки листа «Аэропорты»: пара «Широта»/«Долгота»
    (или английские имена) целиком, числом, в границах; иначе None —
    половинку или мусор в набор не кладём (не выдумываем географию)."""
    def _num(keys):
        for k in keys:
            v = row.get(k)
            if v is None or str(v).strip() == "":
                continue
            try:
                return float(str(v).strip().replace(",", "."))
            except ValueError:
                return None
        return None
    lat, lon = _num(_LAT_KEYS), _num(_LON_KEYS)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return (lat, lon)


def _rows_addr(tables: dict, sheet: str) -> list[tuple[int, dict]]:
    """Строки листа с номерами строк ДАННЫХ (с 1, без заголовка) —
    для адресных отказов. Нумерация сквозная по источникам в порядке
    книги: так же считает цикл рейсов."""
    out: list[tuple[int, dict]] = []
    for sheets in (tables or {}).values():
        block = (sheets or {}).get(sheet)
        if isinstance(block, dict):
            for r in (block.get("rows") or []):
                if isinstance(r, dict):
                    out.append((len(out) + 1, r))
    return out


def _columns(tables: dict, sheet: str) -> list[str]:
    for sheets in (tables or {}).values():
        block = (sheets or {}).get(sheet)
        if isinstance(block, dict):
            return list(block.get("columns") or [])
    return []


def crew_size_from_model(pkg: dict) -> tuple[int, str]:
    """Численность экипажа места и правило-источник.

    ПЕРВЫЙ путь (спецификация 1.2): машинное поле `manifest.crew_size`
    = {"value": N, "rule": "<id>"} — распознавание у ПРОИЗВОДИТЕЛЯ.
    Так и должно быть: формулировки свода пишет его формализатор, ему
    и знать свой словарь. Наши образцы ломались дважды — на
    «pilot_count(flight_duty) = 2» и на «Число пилотов в экипаже
    полётной смены = 2» (цифра после слова, ни один образец не берёт).

    ЗАПАСНОЙ путь: разбор образцами по правилам модели — для пакетов
    версии ≤1.1, где машинного поля ещё нет. Перечень правил-носителей
    для разбора: подсекция rule_maps.crew_size_rules манифеста
    (ревизия 1.14, карта пакета) — когда она есть; иначе легаси-кортеж
    CREW_SIZE_RULES. Карта пакета сильнее кортежа: пакет с картой
    судится ТОЛЬКО ею, зашитые координаты при ней не читаются.

    Ни того, ни другого — отказ: состав, выведенный из ничего, сделал
    бы задачу другой, а расчёт всё равно вернул бы «решение».
    """
    machine = (pkg.get("manifest") or {}).get("crew_size")
    if isinstance(machine, dict):
        value, rule = machine.get("value"), machine.get("rule")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TaskPackageRefused(
                "манифест несёт crew_size с негодной численностью %r — "
                "постановка неисполнима" % (value,))
        if not isinstance(rule, str) or not rule.strip():
            raise TaskPackageRefused(
                "манифест несёт crew_size без ссылки на правило-источник: "
                "численность без трассировки не принимается")
        return value, rule.strip()

    scan_rules = rule_maps.crew_size_rules(
        pkg.get("manifest") or {}, error=TaskPackageRefused)
    if scan_rules is None:
        scan_rules = CREW_SIZE_RULES
    rules = (pkg.get("model") or {}).get("rules") or []
    by_id = {str(r.get("id")): r for r in rules if isinstance(r, dict)}
    for rid in scan_rules:
        rule = by_id.get(rid)
        if not rule:
            continue
        text = " ".join(str(rule.get(k) or "")
                        for k in ("statement", "formal", "title"))
        for pat in CREW_SIZE_PATTERNS:
            m = re.search(pat, text, re.I)
            if m:
                n = int(m.group(1))
                if n <= 0:
                    raise TaskPackageRefused(
                        "правило %s задаёт численность экипажа %d — "
                        "постановка неисполнима" % (rid, n))
                return n, rid
    raise TaskPackageRefused(
        "численность экипажа не задана: машинного поля crew_size в "
        "манифесте нет (пакет версии до 1.2?), и в правилах %s её "
        "не нашли образцами — состав места вывести не из чего"
        % ", ".join(scan_rules))


def _crew_and_seats(pkg: dict, tables: dict
                    ) -> tuple[list, list, str, str, dict]:
    """Экипаж набора, состав места, правило-источник численности, его
    происхождение и ведомость переноса ограничений сотрудника.

    Порядок проверок намеренный: сначала должности (о них сообщение
    точное и действенное), потом численность. Обратный порядок давал
    расплывчатое «состав не выводится» там, где настоящая причина —
    неизвестная должность одного человека.

    Ведомость переноса (аудит A7D, 04.09.2026): каждое ограничение
    листов «Допуски» и «Отпуска» либо перенесено в набор, либо ЯВНО
    отклонено с адресом и причиной — молчаливого пропуска нет. Пока
    единственная причина отклонения — владелец не на листе «Экипажи»:
    такому ограничению в задаче некого держать, а выдумывать
    сотрудника нельзя. Нечитаемое, перевёрнутое или бесхозное
    ограничение — не отклонение, а ОТКАЗ пакету.
    """
    quals: dict[str, list[str]] = {}
    # Допуски: тип судна И СРОК. Прежде колонка «Действует до» терялась
    # на входе — решатель видел «допуск есть» и не мог проверить, не
    # просрочен ли он: правило свода о свежести допуска было неисполнимо
    # не по бедности решателя, а потому что данные до него не доезжали
    # (найдено при сверке словарей ступени 3, 2026-08-23).
    #
    # Форма окон — та же, что у другого ввода данных (airline_ingest):
    # код -> список [начало, конец] в МИНУТАХ ЭПОХИ UTC; несколько
    # строк одного допуска — несколько окон, то есть интервал С
    # РАЗРЫВАМИ, и решатель требует, чтобы весь рейс лежал в одном окне
    # (Employee.holdsQualification). Начало — «Действует с» (полночь
    # указанных суток); колонки нет или ячейка пуста — 0, «ограничения
    # снизу нет», и это заявляется в отчёте конвертера. Конец —
    # последняя минута суток «Действует до» включительно; ПУСТАЯ
    # ячейка — ОТКРЫТОЕ окно: «срок не указан» это не «просрочен».
    # Аудит A7D (04.09.2026): нечитаемое непустое значение прежде тоже
    # давало открытое окно — просроченный допуск становился бессрочным;
    # теперь это отказ с адресом, как у даты рейса.
    qual_windows: dict[str, dict[str, list[list[int]]]] = {}
    qual_addrs: dict[str, list[str]] = {}
    quals_dated = quals_open = quals_from = 0
    for row_no, r in _rows_addr(tables, SHEET_QUALS):
        tab = str(r.get("Табельный номер") or "").strip()
        typ = str(r.get("Тип ВС") or "").strip()
        if not (tab and typ):
            raise TaskPackageRefused(
                "%s: допуск без «Табельный номер» или «Тип ВС» — "
                "ограничение без владельца или без предмета переносить "
                "некуда, а отбросить молча нельзя"
                % _addr(SHEET_QUALS, row_no))
        since = parse_package_date(r.get("Действует с"), sheet=SHEET_QUALS,
                                   row=row_no, column="Действует с")
        until = parse_package_date(r.get("Действует до"), sheet=SHEET_QUALS,
                                   row=row_no, column="Действует до")
        if since is not None and until is not None and until < since:
            raise TaskPackageRefused(
                "%s: окно допуска перевёрнуто — «Действует до» %s раньше "
                "«Действует с» %s" % (_addr(SHEET_QUALS, row_no),
                                       until.date(), since.date()))
        lo = _epoch_min(since) if since is not None else 0
        if until is not None:
            hi = _epoch_min(until + timedelta(days=1)) - 1
            quals_dated += 1
        else:
            hi = QUAL_OPEN_END_MIN
            quals_open += 1
        if since is not None:
            quals_from += 1
        if typ not in quals.setdefault(tab, []):
            quals[tab].append(typ)
        qual_windows.setdefault(tab, {}).setdefault(typ, []).append([lo, hi])
        qual_addrs.setdefault(tab, []).append(_addr(SHEET_QUALS, row_no))

    # Отпуска: «С»–«По» включительно, сутками. Аудит A7D (04.09.2026):
    # прежде нечитаемая граница или перевёрнутый отпуск молча
    # пропадали (continue без счёта) — сотрудник в отпуске становился
    # «доступным», и сертификат мог дать «допустимо» при жёстком
    # запрете. Теперь пустая/нечитаемая граница, «По» раньше «С» и
    # отпуск без табельного номера — отказ с адресом строки.
    unavailable: dict[str, list[str]] = {}
    vac_addrs: dict[str, list[str]] = {}
    vac_days = 0
    for row_no, r in _rows_addr(tables, SHEET_VACATIONS):
        tab = str(r.get("Табельный номер") or "").strip()
        if not tab:
            raise TaskPackageRefused(
                "%s: отпуск без табельного номера — ограничение без "
                "владельца некому нести" % _addr(SHEET_VACATIONS, row_no))
        d0 = parse_package_date(r.get("С"), sheet=SHEET_VACATIONS,
                                row=row_no, column="С")
        d1 = parse_package_date(r.get("По"), sheet=SHEET_VACATIONS,
                                row=row_no, column="По")
        if d0 is None or d1 is None:
            raise TaskPackageRefused(
                "%s: у отпуска сотрудника %s пуста граница «%s» — отпуск "
                "без начала или без конца постановкой не описан, "
                "подставлять границу нельзя"
                % (_addr(SHEET_VACATIONS, row_no), tab,
                   "С" if d0 is None else "По"))
        if d1 < d0:
            raise TaskPackageRefused(
                "%s: отпуск сотрудника %s перевёрнут — «По» %s раньше "
                "«С» %s" % (_addr(SHEET_VACATIONS, row_no), tab,
                            d1.date(), d0.date()))
        day = d0
        while day <= d1:
            unavailable.setdefault(tab, []).append(day.date().isoformat())
            vac_days += 1
            day += timedelta(days=1)
        vac_addrs.setdefault(tab, []).append(_addr(SHEET_VACATIONS, row_no))

    employees: list[dict] = []
    unknown_positions: list[str] = []
    roles_present: list[str] = []
    for row_no, r in _rows_addr(tables, SHEET_CREW):
        tab = str(r.get("Табельный номер") or "").strip()
        if not tab:
            raise TaskPackageRefused(
                "%s: строка без табельного номера"
                % _addr(SHEET_CREW, row_no))
        position = str(r.get("Должность") or "").strip().lower()
        role = ROLE_BY_POSITION.get(position)
        if role is None:
            unknown_positions.append("%s: «%s»" % (tab, position))
            continue
        if role not in roles_present:
            roles_present.append(role)
        base = str(r.get("База") or "").strip()
        if not base:
            # Пустая база — не «без базы», а ссылка на аэропорт с кодом «»,
            # которой решатель не разрешит; а «без базы» стыковку и отдых
            # дома не отличить от внебазовых. Отказ с именем, не 500.
            raise TaskPackageRefused(
                "на листе «%s» у сотрудника %s пустая «База»: база приписки "
                "обязательна — по ней решатель различает стыковку и отдых "
                "дома и вне базы" % (SHEET_CREW, tab))
        rec = {
            "id": tab,
            "name": str(r.get("Фамилия И.О.") or tab).strip(),
            "homeAirport": base,
            "skills": [role],
            "typeRatings": quals.get(tab, []),
            "unavailableDays": unavailable.get(tab, []),
        }
        # Окна допусков едут только там, где они есть: решатель различает
        # «допуска нет» (typeRatings) и «допуск не действует на дату рейса»
        # (qualificationWindows) — это разные нарушения с разными причинами.
        if qual_windows.get(tab):
            rec["qualificationWindows"] = qual_windows[tab]
        employees.append(rec)
    if unknown_positions:
        raise TaskPackageRefused(
            "неизвестные должности в книге данных (роль места вывести "
            "не из чего):\n  - " + "\n  - ".join(unknown_positions))
    if not employees:
        raise TaskPackageRefused("в книге данных нет ни одного члена экипажа")

    # Ведомость переноса: ограничение, чей владелец не на листе
    # «Экипажи», в набор не попадает — но не молча, а с адресом и
    # причиной; счёт едет в выдачу конвертера (conversion_report).
    known = {e["id"] for e in employees}
    declined: list[dict] = []
    for tab, addrs in vac_addrs.items():
        if tab not in known:
            declined.extend({"where": a, "constraint": "отпуск", "owner": tab,
                             "why": "сотрудник не на листе «%s» — "
                                    "ограничение некому нести" % SHEET_CREW}
                            for a in addrs)
    for tab, addrs in qual_addrs.items():
        if tab not in known:
            declined.extend({"where": a, "constraint": "допуск", "owner": tab,
                             "why": "сотрудник не на листе «%s» — "
                                    "ограничение некому нести" % SHEET_CREW}
                            for a in addrs)
    carry = {
        "qualifications": {
            "rows": sum(len(a) for a in qual_addrs.values()),
            "carried": sum(len(a) for t, a in qual_addrs.items()
                           if t in known),
            "dated_until": quals_dated,
            "open_until": quals_open,
            "dated_from": quals_from,
        },
        "vacations": {
            "rows": sum(len(a) for a in vac_addrs.values()),
            "carried": sum(len(a) for t, a in vac_addrs.items()
                           if t in known),
            "days": vac_days,
        },
        "declined": declined,
    }

    crew_size, crew_rule = crew_size_from_model(pkg)
    if isinstance((pkg.get("manifest") or {}).get("crew_size"), dict):
        crew_src = "машинное поле манифеста"
    elif rule_maps.crew_size_rules(pkg.get("manifest") or {},
                                   error=TaskPackageRefused) is not None:
        crew_src = "разбор образцом по карте пакета (rule_maps)"
    else:
        crew_src = "разбор образцом (пакет до версии 1.2)"
    seat_roles = sorted(
        roles_present,
        key=lambda x: ROLE_ORDER.index(x) if x in ROLE_ORDER else 99)
    if len(seat_roles) != crew_size:
        raise TaskPackageRefused(
            "численность экипажа по правилу %s равна %d, а в книге данных "
            "различных ролей %d (%s): состав места однозначно не выводится "
            "— вопрос к постановке, не к расчёту"
            % (crew_rule, crew_size, len(seat_roles),
               ", ".join(seat_roles) or "нет"))
    return employees, seat_roles, crew_rule, crew_src, carry


#: Поля истории сотрудника (ревизия 1.17) → поля набора решателя
#: (Employee.kt): месячные остатки, последняя смена, отдых до горизонта,
#: серии, местоположение. Имена набора — плоские, чтобы Jackson
#: связывал их напрямую.
HISTORY_FIELDS = (
    ("month_to_date", "block_min", "historyMonthBlockMin"),
    ("month_to_date", "work_min", "historyMonthWorkMin"),
    ("month_to_date", "flight_time_min", "historyMonthFlightTimeMin"),
    ("month_to_date", "days_worked", "historyMonthDaysWorked"),
    ("month_to_date", "landings", "historyMonthLandings"),
    ("last_duty", "end", "historyLastDutyEnd"),
    ("last_duty", "end_station", "historyLastDutyStation"),
    ("last_duty", "work_min", "historyLastDutyWorkMin"),
    ("last_duty", "span_min", "historyLastDutySpanMin"),
    ("last_duty", "night", "historyLastDutyNight"),
    (None, "rest_before_horizon_min", "historyRestBeforeHorizonMin"),
    ("series", "consecutive_duty_days", "historyConsecutiveDutyDays"),
    ("series", "consecutive_night_duties", "historyConsecutiveNightDuties"),
    ("series", "days_since_day_off", "historyDaysSinceDayOff"),
    (None, "location", "historyLocation"),
)


def entry_has_history(entry: dict | None) -> bool:
    """Запись раздела несёт историю — хотя бы один блок со значением
    (одно правило с замком кнопки: vmp.history_assumption)."""
    from vmp.history_assumption import entry_has_history as _has
    return _has(entry)


def history_record(entry: dict | None) -> dict | None:
    """Запись истории сотрудника раздела 1.17 → плоская запись набора
    (имена HISTORY_FIELDS + окна rolling); None — истории у сотрудника
    нет (явно): записи нет либо все её блоки null."""
    if not entry_has_history(entry):
        return None
    out: dict = {}
    for group, key, name in HISTORY_FIELDS:
        src = entry.get(group) if group else entry
        v = src.get(key) if isinstance(src, dict) else None
        out[name] = v
    rolling = entry.get("rolling")
    out["historyRolling"] = {str(k): v for k, v in rolling.items()} \
        if isinstance(rolling, dict) else {}
    # Ревизия 1.17.2 (проект): посуточный календарь до горизонта —
    # переносится как есть (date, kind, night), решатель судит по нему
    # скользящие окна счётчиков через границу горизонта.
    cal = entry.get("calendar")
    out["historyCalendar"] = [
        {"date": str(d.get("date") or ""), "kind": str(d.get("kind") or "unknown"),
         "night": d.get("night")}
        for d in cal if isinstance(d, dict)] if isinstance(cal, list) else []
    return out


def _attach_history(pkg: dict, employees: list[dict]) -> dict | None:
    """Раздел manifest.history (1.17) → по сотруднику history = запись
    либо null явно; возвращает шапку раздела для набора: срез,
    покрытие, признак assume_zero_when_missing, поимённо сотрудники без
    истории. Раздела нет → None и поля у сотрудников не появляются."""
    hist = (pkg.get("manifest") or {}).get("history")
    if not isinstance(hist, dict):
        return None
    by_id = {str(e.get("employee_id")): e
             for e in (hist.get("employees") or []) if isinstance(e, dict)}
    missing: list[str] = []
    for rec in employees:
        entry = by_id.get(str(rec.get("id")))
        rec["history"] = history_record(entry)
        if rec["history"] is None:
            missing.append(str(rec.get("id")))
        else:
            rec["historyAsOf"] = hist.get("as_of")
    return {"as_of": hist.get("as_of"),
            "coverage": hist.get("coverage"),
            "assume_zero_when_missing": bool(hist.get("assume_zero_when_missing")),
            # Решение пользователя по вопросу истории (1.17.2) — вход
            # сертификатора читает его как политику окон через границу
            # (continue_zero → по допущению, continue_partial → не судятся).
            "user_decision": (hist.get("user_decision")
                              if isinstance(hist.get("user_decision"), dict) else None),
            "employees_without_history": missing}


def _approved_date(value) -> str | None:
    """«Дата» утверждённого назначения → ISO-дата; формы книги данных
    (дд.мм.гггг) и ISO; нечитаемое — None."""
    s = str(value or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    try:
        return datetime.fromisoformat(s[:19]).date().isoformat()
    except ValueError:
        return None


def _apply_approved_assignments(pkg: dict, employees: list[dict],
                                flights: list[dict],
                                assignments: list[dict]) -> list[dict]:
    """Утверждённые назначения раздела истории (1.17) → закрепления
    набора решателя: место рейса той же роли получает сотрудника и
    pinned=true (тот же контракт, что у восстановления после сбоя).
    Fail-closed: рейс+дата вне набора, роль без свободного места или
    неизвестный сотрудник — отказ словами, не молчаливый пропуск.
    Возвращает перечень закреплений для шапки раздела."""
    hist = (pkg.get("manifest") or {}).get("history")
    if not isinstance(hist, dict):
        return []
    role_of = {str(e.get("id")): (e.get("skills") or [None])[0] for e in employees}
    by_key: dict[tuple[str, str], list[str]] = {}
    for f in flights:
        fid = str(f.get("flightNumber") or "")
        number = fid.split("|", 1)[0]
        day = str(f.get("departureUTCDateTime") or "")[:10]
        by_key.setdefault((number, day), []).append(fid)
    seats: dict[str, list[dict]] = {}
    for a in assignments:
        seats.setdefault(str(a.get("flight")), []).append(a)
    pins: list[dict] = []
    problems: list[str] = []
    for e in hist.get("employees") or []:
        if not isinstance(e, dict):
            continue
        emp = str(e.get("employee_id") or "")
        for ap in e.get("approved_assignments") or []:
            if not isinstance(ap, dict):
                continue
            number = str(ap.get("Рейс") or "").strip()
            day = _approved_date(ap.get("Дата"))
            where = "сотрудник %s, рейс «%s», дата «%s»" % (emp, number, ap.get("Дата"))
            if emp not in role_of:
                problems.append(where + ": сотрудника нет на листе «Экипажи»")
                continue
            if not number or not day:
                problems.append(where + ": рейс или дата не читаются")
                continue
            fids = by_key.get((number, day)) or []
            if not fids:
                problems.append(where + ": рейса с этой датой в наборе нет")
                continue
            if len(fids) > 1:
                problems.append(where + ": рейсов с этой датой несколько (%s) — "
                                "нужна колонка времени вылета" % ", ".join(fids))
                continue
            fid = fids[0]
            role = role_of[emp]
            seat = next((a for a in seats.get(fid) or []
                         if a.get("requiredSkill") == role and not a.get("pinned")),
                        None)
            if seat is None:
                problems.append(where + ": на рейсе нет свободного места роли %s "
                                "(рейс вне обязательств покрытия либо все места "
                                "роли уже закреплены)" % role)
                continue
            seat["employee"] = emp
            seat["pinned"] = True
            pins.append({"employee": emp, "flight": fid, "seat": seat["id"],
                         "role": role, "provenance": ap.get("provenance")})
    if problems:
        raise TaskPackageRefused(
            "утверждённые назначения раздела истории не переносятся как "
            "закрепления:\n  - " + "\n  - ".join(problems))
    return pins


def apply_history_pins(pkg: dict, payload: dict) -> list[dict]:
    """Закрепления из утверждённых назначений раздела истории — в набор
    решателя ЗАДАЧИ РОСТЕРИНГА (кнопка зовёт перед стартом решателя);
    пейринг и карта конвейера набор не закрепляют. Перечень — в шапке
    payload.history.approved_assignments_pinned; отказ словами —
    TaskPackageRefused."""
    pins = _apply_approved_assignments(
        pkg, payload.get("employees") or [], payload.get("flights") or [],
        payload.get("flightAssignments") or [])
    if isinstance(payload.get("history"), dict):
        payload["history"]["approved_assignments_pinned"] = pins
    return pins


def history_pin_catalog(pkg: dict, payload: dict) -> dict | None:
    """Утверждённые назначения раздела истории (1.17.1) → каталог
    закреплённых цепочек построителя ПЕЙРИНГА (--pin-catalog): что
    заказчик утвердил заранее, входит в решение неприкосновенным со
    своей честной ценой, поиск идёт по остальному (A5: закрепления —
    члены единой модели ресурсов; слово владельца 05.09.2026).

    Единица закрепления — смена: рейсы одного сотрудника одной даты
    (канон «Рейс»+«Дата») образуют одну смену, смены одного сотрудника
    — одну цепочку. У цепочек людей нет: сотрудники с одинаковой
    структурой (экипаж одного рейса) дают ОДНУ запись; частичное
    пересечение структур двух сотрудников в точном разбиении
    невозможно — отказ словами с именами, не молчаливый выбор.
    Fail-closed, как у закреплений ростеринга: рейс+дата вне набора,
    неоднозначная дата или неизвестный сотрудник — отказ.
    None — раздела истории или утверждённых назначений нет."""
    hist = (pkg.get("manifest") or {}).get("history")
    if not isinstance(hist, dict):
        return None
    known_emp = {str(e.get("id")) for e in payload.get("employees") or []}
    by_key: dict[tuple[str, str], list[str]] = {}
    t0: dict[str, str] = {}
    for f in payload.get("flights") or []:
        fid = str(f.get("flightNumber") or "")
        number = fid.split("|", 1)[0]
        dep = str(f.get("departureUTCDateTime") or "")
        by_key.setdefault((number, dep[:10]), []).append(fid)
        t0[fid] = dep
    per_emp: dict[str, dict[str, set[str]]] = {}
    problems: list[str] = []
    for e in hist.get("employees") or []:
        if not isinstance(e, dict):
            continue
        emp = str(e.get("employee_id") or "")
        for ap in e.get("approved_assignments") or []:
            if not isinstance(ap, dict):
                continue
            number = str(ap.get("Рейс") or "").strip()
            day = _approved_date(ap.get("Дата"))
            where = "сотрудник %s, рейс «%s», дата «%s»" % (emp, number, ap.get("Дата"))
            if emp not in known_emp:
                problems.append(where + ": сотрудника нет на листе «Экипажи»")
                continue
            if not number or not day:
                problems.append(where + ": рейс или дата не читаются")
                continue
            fids = by_key.get((number, day)) or []
            if not fids:
                problems.append(where + ": рейса с этой датой в наборе нет")
                continue
            if len(fids) > 1:
                problems.append(where + ": рейсов с этой датой несколько (%s) — "
                                "нужна колонка времени вылета" % ", ".join(fids))
                continue
            per_emp.setdefault(emp, {}).setdefault(day, set()).add(fids[0])
    if problems:
        raise TaskPackageRefused(
            "утверждённые назначения раздела истории не переносятся в "
            "закрепления пейринга:\n  - " + "\n  - ".join(problems))
    if not per_emp:
        return None
    # Одинаковая структура у нескольких сотрудников — экипаж одной
    # цепочки; частичное пересечение — конфликт постановки.
    structures: dict[tuple, list[str]] = {}
    for emp, days in sorted(per_emp.items()):
        key = tuple((day, tuple(sorted(legs, key=lambda x: (t0.get(x, ""), x))))
                    for day, legs in sorted(days.items()))
        structures.setdefault(key, []).append(emp)
    legs_of = {k: {x for _, g in k for x in g} for k in structures}
    keys = list(structures)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            clash = legs_of[a] & legs_of[b]
            if clash:
                raise TaskPackageRefused(
                    "утверждённые назначения раздела истории противоречат "
                    "друг другу: у сотрудников %s и %s структуры закреплений "
                    "пересекаются по рейсам (%s), но не совпадают — в точном "
                    "разбиении на цепочки такие закрепления несовместимы; "
                    "уточните утверждённые назначения"
                    % ("+".join(structures[a]), "+".join(structures[b]),
                       ", ".join(sorted(clash)[:5])))
    selected = []
    for key, emps in structures.items():
        duty_fns = [list(g) for _, g in key]
        fns = [x for g in duty_fns for x in g]
        selected.append({"fns": fns, "active_fns": list(fns),
                         "duty_fns": duty_fns,
                         "sourceUnit": "history:" + "+".join(emps),
                         "employees": list(emps),
                         "days": [d for d, _ in key]})
    selected.sort(key=lambda r: (t0.get(r["fns"][0], ""), r["sourceUnit"]))
    return {"selected": selected,
            "origin": "manifest.history.approved_assignments (1.17.1)",
            "as_of": hist.get("as_of")}


#: Источник объявленного горизонта у набора решателя, когда раздела
#: manifest.horizon нет и окно берётся из «Свойств» книги — строка
#: ДОСЛОВНО та же, что у входа сертификатора (webapp/solution_export.
#: declared_window): по ней сверка цены узнаёт объявленное окно.
WINDOW_SOURCE_PLAN_PROPS = \
    "package data properties planSince/planUntil (inclusive minute)"
WINDOW_SOURCE_MANIFEST = "manifest.horizon"


def declared_window_horizon(manifest: dict | None, plan_since,
                            plan_until) -> dict | None:
    """Объявленный горизонт учёта набора решателя — поле `window_horizon`
    (H14-WIRE-2 плана единой версии). Форма ключей — ровно та, что
    читает решатель (kotlin-solver …/domain/FlightCrewSchedule.kt,
    класс AccountingWindow: start_inclusive, end_exclusive, source;
    моменты — наивные ISO, как времена рейсов набора).

    Приоритет — раздел `manifest.horizon` пакета 1.18 (§1.18.1):
    читатель берёт `start` и `end_exclusive` КАК ЕСТЬ (минута
    производителем уже прибавлена, повторно не прибавляется; `tz`
    может быть null — не читается). Раздел есть, но нечитаем либо
    перевёрнут — отказ словами: объявленный, но негодный горизонт —
    дефект пакета, а не повод молча считать окно «не объявленным».

    Раздела нет — плановое окно книги planSince/planUntil тем же
    правилом, что вход сертификатора (solution_export.declared_window):
    planUntil включительный с точностью до минуты → end_exclusive =
    planUntil + 1 минута; окно не из целых суток, перевёрнутое либо
    отсутствующее — None (поле не кладётся: честно «не объявлен», и
    решатель сам называет область выведенной из программы рейсов).
    """
    horizon = (manifest or {}).get("horizon") if isinstance(manifest, dict) \
        else None
    if horizon is not None:
        if not isinstance(horizon, dict):
            raise TaskPackageRefused(
                "раздел manifest.horizon пакета не словарь: %r" % (horizon,))
        start_s = str(horizon.get("start") or "").strip()
        end_s = str(horizon.get("end_exclusive") or "").strip()
        if not start_s or not end_s:
            raise TaskPackageRefused(
                "раздел manifest.horizon пакета без start либо end_exclusive "
                "(спецификация 1.18.1): %s" % json.dumps(
                    horizon, ensure_ascii=False)[:300])
        try:
            start = datetime.fromisoformat(start_s)
            end = datetime.fromisoformat(end_s)
        except ValueError as exc:
            raise TaskPackageRefused(
                "раздел manifest.horizon пакета нечитаем: start=%r, "
                "end_exclusive=%r: %s" % (start_s, end_s, exc))
        if not start < end:
            raise TaskPackageRefused(
                "раздел manifest.horizon пакета перевёрнут: start=%r не "
                "раньше end_exclusive=%r" % (start_s, end_s))
        return {"start_inclusive": start_s, "end_exclusive": end_s,
                "source": WINDOW_SOURCE_MANIFEST}
    try:
        start = datetime.fromisoformat(str(plan_since or ""))
        end = (datetime.fromisoformat(str(plan_until or ""))
               + timedelta(minutes=1))
    except (ValueError, TypeError):
        return None
    if (start >= end or start.time() != datetime.min.time()
            or end.time() != datetime.min.time()):
        return None
    return {"start_inclusive": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "source": WINDOW_SOURCE_PLAN_PROPS}


def duty_consent_time(value, *, where: str, source_cell=False,
                      error=TaskPackageRefused) -> str:
    """Exact native-clock label; never convert consent alone to UTC.

    Like flight labels, these LocalDateTimes live in the package's declared
    scale. ISO offsets are not that wire contract; stripping one would invent
    an identity. Source Excel cells may use the existing dd.mm.yyyy form.
    """
    if not isinstance(value, str):
        raise error(f"{where}: ожидалась метка времени строкой")
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", text):
            stamp = datetime.fromisoformat(text)
        elif source_cell and re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?", text):
            stamp = datetime.strptime(text, "%d.%m.%Y %H:%M:%S" if text.count(":") == 2 else "%d.%m.%Y %H:%M")
        elif source_cell and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
            # data_freeze._cell_str emits this form for an Excel midnight.
            stamp = datetime.strptime(text, "%d.%m.%Y")
        else:
            raise ValueError("ожидалась полная дата/время без offset в общей шкале набора")
    except ValueError as exc:
        raise error(f"{where}: нечитаемая или несогласованная метка {value!r}: {exc}") from exc
    return stamp.isoformat()


def duty_consent_index(employees, *, error=TaskPackageRefused) -> dict:
    """Validate the actual wire facts; absent fact is not false consent."""
    if not any(isinstance(e, dict) and "dutyExtensionConsents" in e for e in employees):
        return {}
    by_key, ids = {}, set()
    for employee in employees:
        if not isinstance(employee, dict):
            raise error("dutyExtensionConsents: сотрудник не объект")
        eid = employee.get("id")
        if not isinstance(eid, str) or not eid.strip() or eid in ids:
            raise error(f"dutyExtensionConsents: пустой или повторный employee id {eid!r}")
        ids.add(eid)
        if "dutyExtensionConsents" not in employee:
            continue
        facts = employee["dutyExtensionConsents"]
        if not isinstance(facts, list):
            raise error(f"{eid}.dutyExtensionConsents: ожидался список")
        for number, fact in enumerate(facts, 1):
            where = f"{eid}.dutyExtensionConsents[{number}]"
            if not isinstance(fact, dict) or type(fact.get("consent")) is not bool:
                raise error(f"{where}: consent обязан быть явным boolean")
            if not isinstance(fact.get("source"), str) or not fact["source"].strip():
                raise error(f"{where}: отсутствует адрес исходного согласия source")
            where += f" ({fact['source']})"
            start = duty_consent_time(fact.get("start"), where=where + ".start", error=error)
            end = duty_consent_time(fact.get("end"), where=where + ".end", error=error)
            if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
                raise error(f"{where}: конец смены не позже начала")
            key = (eid, start, end)
            if key in by_key and by_key[key]["consent"] is not fact["consent"]:
                raise error(f"{where}: конфликт согласия с {by_key[key]['source']}")
            by_key.setdefault(key, fact)
    return by_key


def _attach_duty_consents(tables: dict, employees: list[dict]) -> dict:
    """Only explicit clean-data facts, carrying a source address per row."""
    by_id = {e["id"]: e for e in employees}
    counts = {"rows": 0, "carried": 0, "unknown": 0,
              "reading": "employee + exact start/end in the same payload time scale; no UTC conversion"}
    for sid, sheets in tables.items():
        if not isinstance(sheets, dict) or SHEET_DUTIES not in sheets:
            continue
        block = sheets[SHEET_DUTIES]
        where = f"{sid}/{SHEET_DUTIES}"
        if not isinstance(block, dict) or not isinstance(block.get("rows"), list):
            raise TaskPackageRefused(f"{where}: ожидался лист со списком rows")
        for number, row in enumerate(block["rows"], 1):
            address = f"{where}: строка данных {number}"
            counts["rows"] += 1
            if not isinstance(row, dict):
                raise TaskPackageRefused(f"{address}: строка не объект")
            raw = row.get("Согласие на продление")
            if raw is None or isinstance(raw, str) and not raw.strip():
                counts["unknown"] += 1
                continue
            if type(raw) is bool:
                consent = raw
            elif isinstance(raw, str) and raw.strip().lower() in ("да", "нет", "true", "false"):
                consent = raw.strip().lower() in ("да", "true")
            else:
                raise TaskPackageRefused(f"{address}, колонка «Согласие на продление»: "
                                         f"ожидалось да/нет или boolean, получено {raw!r}")
            eid = row.get("Табельный номер")
            if not isinstance(eid, str) or eid.strip() not in by_id:
                raise TaskPackageRefused(f"{address}, колонка «Табельный номер»: неизвестный сотрудник {eid!r}")
            start = duty_consent_time(row.get("Начало"), where=address + ", колонка «Начало»", source_cell=True)
            end = duty_consent_time(row.get("Конец"), where=address + ", колонка «Конец»", source_cell=True)
            by_id[eid.strip()].setdefault("dutyExtensionConsents", []).append(
                {"start": start, "end": end, "consent": consent, "source": address})
            counts["carried"] += 1
    duty_consent_index(employees)
    if counts["carried"]:
        # Existing flight parser slices HH:MM. Do not let an offset-bearing
        # flight silently make the new consent fact appear to share its clock.
        for number, row in _rows_addr(tables, SHEET_FLIGHTS):
            for column in ("Время вылета", "Время прилёта"):
                if re.search(r"[zZ]|[+-]\d", str(row.get(column) or "")):
                    raise TaskPackageRefused(f"{_addr(SHEET_FLIGHTS, number, column)}: "
                                             "offset времени не согласован со шкалой согласия смены")
    return counts


def to_payload(pkg: dict) -> dict:
    """Очищенные данные пакета -> набор решателя (формат payload).

    Неполнота обязательных полей — отказ с ПЕРЕЧНЕМ недостающего
    (постановка §3 п.3): решать задачу на догадках хуже, чем не решать.
    """
    # Rejudge actual model/manifest, not the caller-editable display flag.
    # Compatibility permits archive inspection only, never solver input.
    check_landing_projection(pkg, for_execution=True)
    check_support_projection(pkg)
    data = pkg.get("clean_data")
    if not isinstance(data, dict):
        raise TaskPackageRefused(
            "в пакете нет очищенных данных (has_data=false): решать "
            "нечего — данные книги в прогон CDPM не загружались")
    tables = data.get("tables") or {}

    missing: list[str] = []
    for sheet, cols in REQUIRED_COLUMNS.items():
        have = _columns(tables, sheet)
        if not have:
            missing.append("нет листа «%s»" % sheet)
            continue
        for c in cols:
            if c not in have:
                missing.append("на листе «%s» нет колонки «%s»" % (sheet, c))
    if missing:
        raise TaskPackageRefused(
            "очищенные данные неполны для постановки задачи:\n  - "
            + "\n  - ".join(missing))

    # --- аэропорты: из рейсов, без придумывания справочника ---------
    codes: list[str] = []
    for r in _rows(tables, SHEET_FLIGHTS):
        for key in ("Вылет", "Прилёт"):
            code = str(r.get(key) or "").strip()
            if code and code not in codes:
                codes.append(code)
    for r in _rows(tables, SHEET_CREW):
        base = str(r.get("База") or "").strip()
        if base and base not in codes:
            codes.append(base)
    # Ревизия 1.10 (шов пояса, 2026-08-27): построитель судит окна
    # матрицы в поясе БАЗЫ и берёт его ИЗ ДАННЫХ — канонический лист
    # «Аэропорты» (колонки «Код», «timeZone»: IANA-имя вида
    # Europe/Berlin) пробрасывается полем timeZone аэропорта набора.
    # Листа нет — поле не кладётся (построитель скажет вслух;
    # выдумывать пояс нельзя — чужой пояс объявил бы ночными смены
    # другой авиакомпании).
    # Ревизия 05.09.2026 (ночь, принцип координат — общий с CDPM):
    # колонки «Широта»/«Долгота» того же листа (или latitude/longitude,
    # lat/lon) — координаты станций ИЗ КНИГИ; кладутся полями домена
    # решателя latitude/longitude и первичны для карты (webapp/geo.py:
    # справочник по коду — только у классов open/proprietary). Пара
    # берётся лишь целиком и в границах; иначе поле не кладётся.
    tz_by_code: dict[str, str] = {}
    pt_by_code: dict[str, tuple[float, float]] = {}
    for r in _rows(tables, "Аэропорты"):
        code = str(r.get("Код") or "").strip()
        tzname = str(r.get("timeZone") or "").strip()
        if code and tzname:
            tz_by_code.setdefault(code, tzname)
        if code and code not in pt_by_code:
            pt = _airport_point(r)
            if pt is not None:
                pt_by_code[code] = pt
    airports = []
    for c in codes:
        a: dict = {"code": c, "name": c}
        if c in tz_by_code:
            a["timeZone"] = tz_by_code[c]
        if c in pt_by_code:
            a["latitude"], a["longitude"] = pt_by_code[c]
        airports.append(a)

    # --- состав места: сначала экипаж, потом места -------------------
    employees, seat_roles, crew_rule, crew_src, carry = \
        _crew_and_seats(pkg, tables)
    duty_consents = _attach_duty_consents(tables, employees)
    # Ревизия 1.17 (ступень L аудита A7): раздел «история сотрудника» —
    # остатки на начало периода — едет в набор решателя по сотруднику;
    # сотрудник без истории получает history=null ЯВНО (не ноль).
    history_meta = _attach_history(pkg, employees)

    # --- обязательства покрытия и транспорт ---------------------------
    # Правка постановки по поимённой сверке PCDPM+ с эталоном PLLM
    # (2026-08-27): обязательство покрытия — рейс с «Перегон»=false и
    # вылетом в плановом интервале книги (ключи planSince/planUntil
    # листа «Свойства»). Всё прочее (пометка «Перегон», чужой интервал)
    # — ТРАНСПОРТ: рейс остаётся в наборе и доступен пассажиром, но
    # обязательством не считается и рабочих мест не получает. Словарь
    # согласован с решателем: признак transportOnly=true на рейсе
    # (обязательства построителя — список рейсов минус признак), места
    # только у обязательств (обязательства судьи — места каталога).
    # Книга без «Свойств» и без колонки «Перегон» — прежнее поведение:
    # каждый рейс — обязательство.
    props: dict[str, str] = {}
    for r in _rows(tables, "Свойства"):
        key = str(r.get("ключ") or "").strip()
        if key:
            props[key] = str(r.get("значение") or "").strip()
    plan_since = plan_until = None
    if props.get("planSince") and props.get("planUntil"):
        try:
            plan_since = datetime.fromisoformat(props["planSince"])
            plan_until = datetime.fromisoformat(props["planUntil"])
        except ValueError:
            raise TaskPackageRefused(
                "плановый интервал листа «Свойства» нечитаем: "
                "planSince=%r, planUntil=%r" % (props.get("planSince"),
                                                props.get("planUntil")))

    # --- рейсы и места ------------------------------------------------
    # Личность рейса на проводе — идентификатор УЧАСТКА (номер + момент
    # вылета): у решателя тождество объекта Jackson стоит на номере, и
    # повтор номера роняет разбор намеренно (см. Flight.kt). Приём этой
    # конвенции здесь делает набор пригодным и для многодневных книг.
    flights: list[dict] = []
    assignments: list[dict] = []
    by_leg: dict[str, dict] = {}
    duplicates_exact = 0
    conflicts: list[str] = []
    transport_reasons: dict[str, int] = {}
    transport_carriers: dict[str, int] = {}
    obligation_carriers: dict[str, int] = {}
    for i, r in enumerate(_rows(tables, SHEET_FLIGHTS), 1):
        number = str(r.get("Номер рейса") or "").strip()
        if not number:
            raise TaskPackageRefused(
                "в строке %d листа «%s» пуст номер рейса" % (i, SHEET_FLIGHTS))
        dep = _parse_dt(r.get("Дата"), r.get("Время вылета"),
                        row=i, column="Время вылета")
        arr = _parse_dt(r.get("Дата"), r.get("Время прилёта"),
                        row=i, column="Время прилёта")
        if arr <= dep:                       # рейс через полночь
            arr = (datetime.fromisoformat(arr)
                   + timedelta(days=1)).isoformat()
        leg_id = "%s|%s" % (number, dep)
        peregon_raw = r.get("Перегон")
        peregon = (peregon_raw is True
                   or str(peregon_raw or "").strip().lower()
                   in ("true", "истина", "да", "1"))
        transport_why = None
        if peregon:
            transport_why = "колонка «Перегон»"
        elif plan_since is not None and not (
                plan_since <= datetime.fromisoformat(dep) <= plan_until):
            transport_why = "вылет вне планового интервала"
        carrier = str(r.get("Перевозчик") or "").strip()
        rec = {
            "flightNumber": leg_id,
            "flightLegId": leg_id,
            "departureAirport": str(r.get("Вылет")).strip(),
            "departureUTCDateTime": dep,
            "arrivalAirport": str(r.get("Прилёт")).strip(),
            "arrivalUTCDateTime": arr,
            # Тип судна: из книги, когда она его несёт. Когда колонки
            # нет — тип ПУСТОЙ, а не умолчание домена («A320»): чужое
            # умолчание молча сделало бы правило допуска по типу ложно
            # жёстким (ни у кого из экипажа такого допуска нет, и любое
            # назначение стало бы нарушением). Отсутствие колонки
            # названо в отчёте конвертации.
            "aircraftType": str(r.get(COL_AIRCRAFT_TYPE) or "").strip(),
        }
        if transport_why:
            rec["transportOnly"] = True
        prev = by_leg.get(leg_id)
        if prev is not None:
            # Точный повтор строки сведений не несёт — сворачивается со
            # счётом. Повтор ключа с ДРУГИМИ данными — вопрос к
            # авиакомпании, и досчитывать его нельзя.
            if prev == rec:
                duplicates_exact += 1
                continue
            conflicts.append(
                "%s: строка %d расходится с ранее прочитанной" % (leg_id, i))
            continue
        by_leg[leg_id] = rec
        flights.append(rec)
        if transport_why:
            transport_reasons[transport_why] = \
                transport_reasons.get(transport_why, 0) + 1
            if carrier:
                transport_carriers[carrier] = \
                    transport_carriers.get(carrier, 0) + 1
        elif carrier:
            obligation_carriers[carrier] = \
                obligation_carriers.get(carrier, 0) + 1

    if conflicts:
        raise TaskPackageRefused(
            "в очищенных данных повторяются ключи рейсов с РАЗНЫМ "
            "содержимым — какой рейс настоящий, решает авиакомпания:\n  - "
            + "\n  - ".join(conflicts))
    if not flights:
        raise TaskPackageRefused("в книге данных нет ни одного рейса")
    obligations = [f for f in flights if not f.get("transportOnly")]
    if not obligations:
        raise TaskPackageRefused(
            "все рейсы книги — транспорт (пометка «Перегон» либо вылет "
            "вне планового интервала): обязательств покрытия нет — "
            "решать нечего")
    # Охрана однородности: машинного признака «своего перевозчика» в
    # книге нет (опора — «Перегон» и плановый интервал), поэтому
    # смешение РАЗНЫХ перевозчиков в обязательствах — вопрос к
    # авиакомпании, а не догадка конвертера.
    if len(obligation_carriers) > 1:
        raise TaskPackageRefused(
            "в обязательствах покрытия смешаны разные перевозчики — "
            "какой из них свой, решает авиакомпания: "
            + ", ".join("%s (%d)" % (c, n) for c, n in
                        sorted(obligation_carriers.items())))

    for i, f in enumerate(obligations, 1):
        for idx, role in enumerate(seat_roles):
            assignments.append({
                "id": "A%03d-%d" % (i, idx),
                "flight": f["flightNumber"],
                "indexInFlight": idx,
                "requiredSkill": role,
            })

    # Утверждённые назначения раздела истории закрепляются ОТДЕЛЬНЫМ
    # шагом apply_history_pins — только у задачи ростеринга (ревью CDPM
    # 05.09: отказ закрепления валил и нажатие пейринга, и карту).

    # H14-WIRE-2 (единая версия 06.09.2026): объявленный горизонт учёта
    # — факт набора `window_horizon` для решателя (K-4/K-32): без него
    # решатель выводит область из программы рейсов (source=inferred),
    # отчёт ночёвок несёт declared_accounting_horizon_unknown, а вход
    # сертификатора при этом даёт ОБЪЯВЛЕННОЕ окно из planSince/
    # planUntil — сверка цены расходилась на каждом прогоне
    # (INCOMPARABLE). Поле кладётся только когда горизонт объявлен.
    window_horizon = declared_window_horizon(
        pkg.get("manifest"), props.get("planSince"), props.get("planUntil"))

    out = {
        "airports": airports,
        "employees": employees,
        "flights": flights,
        "flightAssignments": assignments,
        # Ревизия 1.17: шапка раздела истории (срез, покрытие, признак
        # производителя о нулевой подстановке) — None, когда раздела нет.
        "history": history_meta,
        # Служебное: как выведен состав места — попадает в отчёт
        # конвертации, чтобы число мест не выглядело взявшимся ниоткуда.
        "_seatRoles": seat_roles,
        "_crewSizeRule": crew_rule,
        "_crewSizeSource": crew_src,
        "_duplicatesCollapsed": duplicates_exact,
        # Ведомость переноса ограничений сотрудника (аудит A7D):
        # перенесено / отклонено с адресом и причиной — в отчёт.
        "_constraintsCarry": carry,
        "_dutyConsentsCarry": duty_consents,
        "_coverage": {
            "obligations": len(obligations),
            "transport": len(flights) - len(obligations),
            "transport_by_reason": transport_reasons,
            "transport_carriers": transport_carriers,
            "plan_since": props.get("planSince") or None,
            "plan_until": props.get("planUntil") or None,
        },
    }
    if window_horizon is not None:
        out["window_horizon"] = window_horizon
    return out


def conversion_report(pkg: dict, payload: dict) -> dict:
    """Что перенесено в набор и что осталось за бортом.

    Спецификация §7 п.5: правила и данные без исполнителя — в ЯВНЫЙ
    отчёт; молчаливый пропуск запрещён.
    """
    tables = (pkg.get("clean_data") or {}).get("tables") or {}
    carry = payload.get("_constraintsCarry") or {
        "qualifications": {"dated_until": 0, "open_until": 0, "dated_from": 0},
        "vacations": {}, "declined": []}
    seen_sheets: list[str] = []
    for sheets in tables.values():
        seen_sheets.extend(sheets.keys())
    carried = [SHEET_FLIGHTS, SHEET_CREW, SHEET_QUALS, SHEET_VACATIONS]
    # Only this projection of Смены is implemented, not all sheet columns.
    if (payload.get("_dutyConsentsCarry") or {}).get("carried"):
        carried.append(SHEET_DUTIES)
    skipped = [s for s in seen_sheets if s not in carried]
    return {
        "airports": len(payload["airports"]),
        "employees": len(payload["employees"]),
        "flights": len(payload["flights"]),
        "seats": len(payload["flightAssignments"]),
        "seat_roles": list(payload.get("_seatRoles") or []),
        "crew_size_rule": payload.get("_crewSizeRule"),
        "crew_size_source": payload.get("_crewSizeSource"),
        "duplicate_rows_collapsed":
            payload.get("_duplicatesCollapsed", 0),
        # Обязательства покрытия против транспорта (правка постановки
        # 2026-08-27): числа в отчёт, чтобы «рейсов меньше мест» не
        # выглядело потерей данных — транспорт назван с причинами.
        "coverage_obligations": payload.get("_coverage"),
        # H14-WIRE-2: объявленный горизонт учёта набора и его источник
        # (manifest.horizon либо planSince/planUntil книги); None —
        # горизонт не объявлен, решатель назовёт область выведенной.
        "window_horizon": payload.get("window_horizon"),
        "sheets_carried": [s for s in carried if s in seen_sheets],
        "sheets_not_carried": skipped,
        # Правила, которым в этом наборе не к чему привязаться. Не
        # «пропущено», а названо: спецификация §7 п.5.
        "rules_without_data": (
            ["допуск по типу воздушного судна: в книге данных нет "
             "колонки «%s» у рейса" % COL_AIRCRAFT_TYPE]
            if (SHEET_QUALS in seen_sheets
                and COL_AIRCRAFT_TYPE not in _columns(tables, SHEET_FLIGHTS))
            else []),
        "aircraft_type_from_book":
            COL_AIRCRAFT_TYPE in _columns(tables, SHEET_FLIGHTS),
        "rows_removed_by_cleaning":
            len(((pkg.get("clean_data") or {}).get("cleaning")
                 or {}).get("removed") or []),
        # Сроки допусков: сколько людей несут датированные окна и как
        # прочитаны границы. Прежде «Действует до» терялось молча, и
        # правило о свежести допуска было неисполнимо по отсутствию
        # данных, а не по решению. Счётчики окон (аудит A7D,
        # 04.09.2026) — из ведомости переноса: прежде они считались и
        # выбрасывались, и непустое нечитаемое было неотличимо от
        # пустого.
        "qualification_windows": {
            "employees_with_windows": sum(
                1 for e in payload["employees"] if e.get("qualificationWindows")),
            "dated_until": carry["qualifications"]["dated_until"],
            "open_until": carry["qualifications"]["open_until"],
            "dated_from": carry["qualifications"]["dated_from"],
            "reading": ("«Действует до» прочитано как конец суток включительно; "
                        "«Действует с» — полночь суток, без колонки или при "
                        "пустой ячейке нижняя граница окна не ограничена; "
                        "ПУСТОЕ «Действует до» — окно ОТКРЫТОЕ («не указан» ≠ "
                        "«просрочен»); непустое нечитаемое значение любой "
                        "дато-колонки — отказ пакету с адресом ячейки; "
                        "несколько строк одного допуска — окна с разрывами"),
        },
        # Ведомость переноса ограничений сотрудника (аудит A7D): всё,
        # что не перенесено, названо с адресом и причиной.
        "constraints_carried": {
            "qualifications": carry["qualifications"],
            "vacations": carry["vacations"],
        },
        "constraints_declined": len(carry["declined"]),
        "constraints_declined_rows": list(carry["declined"]),
        "duty_extension_consents": payload.get("_dutyConsentsCarry"),
    }


def allowed_modes(pkg: dict, task: str) -> list[str]:
    """Разрешённые режимы задачи по манифесту (гейт запуска)."""
    modes = (pkg.get("manifest") or {}).get("allowed_modes") or {}
    got = modes.get(task)
    return [str(m) for m in got] if isinstance(got, list) else []


def check_mode(pkg: dict, task: str, mode: str) -> None:
    """Отказать, если режим не разрешён пакетом для этой задачи."""
    allowed = allowed_modes(pkg, task)
    if mode not in allowed:
        raise TaskPackageRefused(
            "режим %s не разрешён пакетом для задачи %s: allowed_modes=%s"
            % (mode, task, ", ".join(allowed) if allowed else "пусто"))


# --------------------------------------------------- целевые (этап Б)

#: Закрытый словарь машинных метрик спецификации (§5а, версия 1.1).
#: Значение — задачи, которым метрика применима. Имя вне словаря
#: означает, что наш словарь старее пакета: догадываться запрещено.
METRICS: dict[str, tuple[str, ...]] = {
    "total_pairing_cost": ("pairing",),
    "layover_nights_total": ("pairing", "rostering"),
    "credit_hours_spread": ("rostering",),
    "positioning_cost": ("pairing", "rostering"),
    "uncovered_seats": ("pairing", "rostering"),
    "preferred_days_off_violations": ("rostering",),
}


def objectives(pkg: dict, task: str) -> list[dict]:
    """Целевые зоны как их объявил производитель.

    Возвращаются машинные поля: идентификатор, направление, метрика.
    Проза `statement` остаётся человеку и машиной не разбирается —
    числа из свободного текста мы не берём.
    """
    zone = (pkg.get("objective") or {}).get(task) or {}
    out: list[dict] = []
    for o in zone.get("objectives") or []:
        if not isinstance(o, dict):
            continue
        out.append({
            "id": o.get("id"),
            "title": o.get("title") or "",
            "direction": (o.get("direction") or "").strip().lower(),
            "metric": o.get("metric") or None,
            "statement": o.get("statement") or "",
        })
    return out


def check_objective(pkg: dict, task: str, objective_id: str) -> dict:
    """Отказать, если целевую нельзя исполнить, — до запуска решателя.

    Порядок проверок тот же, что у решателя: целевая обязана быть
    объявлена СВОЕЙ зоной; направление берётся машинным полем;
    метрика — из закрытого словаря и применимая этой задаче. Двойная
    проверка не лишняя: приложение отказывает дёшево и внятно, не
    потратив ни секунды счёта.
    """
    declared = objectives(pkg, task)
    ids = [str(o["id"]) for o in declared if o.get("id")]
    if not objective_id:
        raise TaskPackageRefused(
            "режим optimization требует явной целевой (objective_id): "
            "каждая целевая — отдельная задача. Целевые задачи %s: %s"
            % (task, ", ".join(ids) if ids else "нет"))
    found = next((o for o in declared if str(o.get("id")) == objective_id),
                 None)
    if found is None:
        raise TaskPackageRefused(
            "целевая %s не объявлена пакетом для задачи %s: %s"
            % (objective_id, task,
               "есть " + ", ".join(ids) if ids else "целевых нет"))
    direction = found["direction"]
    if direction not in ("minimize", "maximize"):
        raise TaskPackageRefused(
            "у целевой %s нечитаемое направление %r"
            % (objective_id, direction))
    metric = found["metric"]
    if metric is not None:
        tasks = METRICS.get(metric)
        if tasks is None:
            raise TaskPackageRefused(
                "метрика %s вне известного словаря; известные: %s"
                % (metric, ", ".join(sorted(METRICS))))
        if task not in tasks:
            raise TaskPackageRefused(
                "метрика %s не применима задаче %s: словарь объявляет её "
                "для %s" % (metric, task, ", ".join(tasks)))
    return found
