# -*- coding: utf-8 -*-
"""Инструмент извлечения каталога шаблонов из кода и посева (зона COSMOS).

Со шага 4 инструмент НЕ читает проекции обёртки: `vmp/template_registry`
и `vmp/criteria.SPEC` после шага 2 сами проекции каталога (сборка по ним
была бы кругом), а с шага 4 обёртка читает ещё и привязки — сборка
каталога стала бы зависеть от файла привязок, который собирается ПО
каталогу. Входы теперь такие:

  * посев catalog/tools/catalog_seed.json — курируемые поля записей
    (порядок семей, класс/заголовок/вид/зона/единица нарушения, карта
    параметр→семья, виды исполнителей, шаблоны проверок, семантика,
    происхождение, история, миры и стендовые задачи, критерии, гейты)
    И раздел `cdpm_dictionaries` — словари CDPM ДАННЫМИ (единицы
    параметров, перечни, слова-носители, дополнения, порядок лексикона,
    зоны);
  * заявки папки назначения requests.json — свидетель единицы и
    отношения имени, которого нет ни в словаре CDPM, ни в таблицах
    решателя (решение 0.6 пакета паритета: заявка принята владельцем,
    а строку истории записи пишет тот же агент, что и запись);
  * Kotlin-таблицы через vmp/kotlin_tables.py (ручки, ручки стыковки,
    семьи и единицы истории, мягкие семьи, метрики решателя);
  * cdpm/data_checks.py — TEMPLATES, WAVE_SOFT_TEMPLATES,
    HISTORY_FORM_TEMPLATES;
  * vmp/run/roster_certify.py — PEOPLE_FAMILIES, WORLD_PARAM_FAMILIES,
    EXTENSION_CONDITIONS, SOFT_MEASURE_TEMPLATE, WORLD_PARAM_TEMPLATE.

С части 7-А шага 7 инструмент НЕ читает снимок словарей CDPM
final-report/KATALOG-CDPM-SLOVARI-CLD.json и НЕ импортирует cdpm.model /
cdpm.family_lexicon: их содержимое перенесено в посев машинно, и стадия
1б CDPM (снятие _SUBJECT_WORDS, PARAMS, SOFT_FAMILIES и самого снимка)
сборку каталога больше не обрушит. С части 7-Б снимок снят вовсе, и
посев — единственный носитель словарей CDPM; свидетель «каталог ≡
посев» — правила L05, L06 и L07 гейта паритета.

Файл заявок `requests.json` инструмент НЕ ПОРОЖДАЕТ и НЕ СТИРАЕТ (К9
договорённостей с CDPM): заявки лежат вне отпечатка, их ведёт служба
каталога. Инструмент читает их с диска, чтобы судить форму тем же
validate(), в сравнение `--check` не берёт, а на ЧИСТОЙ папке заводит
пустую очередь один раз.

Пишет детерминированно: повторный запуск с теми же аргументами даёт
байт-в-байт те же файлы (перевод строки — LF; сверка --check и гейт L18
к переводу строки нечувствительны: файл, перематериализованный git с
CRLF, расхождением не считается). Семена прогоняются через validate()
читателя до записи; ошибка — отказ словами, ничего не пишется.

    python catalog/tools/build_from_code.py --out catalog [--check]
        [--from-catalog] [--refresh]
        [--version 0.2.0-draft.2] [--released-at 2026-09-08]
        [--released-by "сессия COSMOS"] [--note ...]

Пакет паритета (08.09.2026) сделал посев ДВУСТОРОННИМ: у каталога один
писатель — служба, а посев больше не хранилище правды о записях, а
жёсткие блоки владельца ⊕ машинная проекция каталога.

  * `--refresh` переписывает ТОЛЬКО `tools/catalog_seed.json` и
    `transition/step4_additions.json` по каталогу папки: разделов
    отпечатка, журнала, заявок и `releases/` он не касается, поэтому
    обходом `refuse_write` не является. Жёсткие блоки (`base`,
    `cdpm_dictionaries.params/vocabularies/zones`, `params_relation_set`,
    провенанс `cdpm_stand_tasks`) проекция не трогает: они переносятся из
    прежнего посева как есть. Правит их только слово владельца, а
    стережёт не сравнение посева с самим собой (оно было бы
    тавтологией), а свидетели: блок `base` сверяется с каталогом кода
    заморозки (стенд `tool_guard`), словарь CDPM — со снимком части 7-Б,
    отпечаток проекции — отказом `refreshed()`.
  * `--refresh --check` ничего не пишет и отвечает словами «посев и
    дельта свежи» (код 0) либо называет отставший носитель и команду
    `--refresh` (код 1); то же сравнение проекций делает правило
    паритета L23 — своими руками и без сборки.
  * `--check --from-catalog` судит ЛЮБУЮ папку каталога — живую,
    временный каталог попытки CR, папку выпуска — по закону «каталог ≡
    курируемое(каталог) ⊕ код»: курируемые поля берутся из САМОЙ папки,
    жёсткие блоки — из посева инструмента, а всё выводимое из кода
    (единицы, отношения, уточнители, природы, условия, шаблоны
    проверок, несвязанные имена, метрики) пересобирается заново. Это
    правило паритета L18.

Аргументы версии, даты, автора и заметки, если их не назвали в командной
строке, читаются из шапки папки назначения: второй источник истины о
выпуске инструменту не нужен.

Файл описи исполнителей transition/executors_legacy.json инструментом
БОЛЬШЕ НЕ ПИШЕТСЯ: со шага 4 исполнителей называет vmp/bindings.json, а
опись остаётся на диске СВИДЕТЕЛЕМ — шаг 7-Б её оставил: она левая
сторона гейта «ничего не потеряно» (tests/vmp/test_bindings.py, п. 13;
руководство 13.12), вместе с ней остаётся правило паритета L13. Вместо неё инструмент пишет дельту шага 4
transition/step4_additions.json — переходный носитель вне отпечатка.

Инструмент — не читатель: импорты из cdpm/vmp ему разрешены.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG_DIR = HERE.parent


def repo_root(catalog_dir: Path) -> Path:
    """Корень дерева продуктов для папки каталога.

    Каталог вправе лежать НЕ прямо под корнем: служба каталога держит
    раскладку `<репозиторий>/data/catalog` и сама ищет корень через
    `git rev-parse --show-toplevel` (`catalog_service/releases.py`,
    `API-CONTRACT.md`). Инструмент брал родителя папки каталога, и на
    объявленной раскладке импорт `template_catalog` падал ещё до разбора
    аргументов — судья отвечал «не судился» ценой трассировки.

    Порядок поиска: родитель папки каталога (обычная раскладка дерева —
    ни одного подпроцесса), затем предки папки каталога снизу вверх
    (раскладка `<репозиторий>/data/catalog`), затем корень git (его же
    спрашивает служба), и лишь потом текущая папка процесса. Ни один не
    несёт читателя — остаётся родитель, как прежде."""
    def _is_root(p: Path) -> bool:
        return (p / "template_catalog" / "__init__.py").is_file()

    parent = catalog_dir.parent
    if _is_root(parent):
        return parent
    for up in parent.parents:
        if _is_root(up):
            return up
    try:
        import subprocess
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(catalog_dir), capture_output=True,
                             text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            top = Path(res.stdout.strip())
            if _is_root(top):
                return top
    except Exception:                                        # noqa: BLE001
        pass
    try:
        cwd = Path.cwd()
    except OSError:                                          # pragma: no cover
        return parent
    return cwd if _is_root(cwd) else parent


REPO = repo_root(CATALOG_DIR)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from template_catalog import canon                                    # noqa: E402
from template_catalog.export import dump_json                         # noqa: E402
from template_catalog.reader import Catalog                           # noqa: E402
from template_catalog.validate import schema_path_for_version         # noqa: E402
from vmp import kotlin_tables as ktab                                 # noqa: E402

SEED = HERE / "catalog_seed.json"
SEED_REL = "tools/catalog_seed.json"
DELTA_REL = "transition/step4_additions.json"
SCHEMA_SRC = REPO / "template_catalog" / "schemas" / "catalog.schema.json"

#: Носитель исполнителей со шага 4 — файл привязок COSMOS; опись
#: transition/executors_legacy.json осталась свидетелем L13.
EXECUTORS_SOURCE = "vmp/bindings.json"

#: Перевод строки: файлы каталога пишутся LF, а git с core.autocrlf
#: вправе перематериализовать их с CRLF — сверка к форме нечувствительна.
CRLF = b"\r\n"
LF = b"\n"

#: Слово дельты о происхождении, которого нет: закон паритета досчитывает
#: дельту до конца и называет беду одним правилом, инструмент — отказом.
NO_WITNESS = "свидетеля нет"

#: Разделы посева, которые `--refresh` НЕ переписывает: факты прошлого и
#: слово владельца. Их правит человек и только по слову владельца —
#: расширение словаря CDPM или пересмотр базы заморозки равносильны
#: новому файлу снимка (решение 0.5 пакета паритета).
HARD_SEED_KEYS = ("form_version", "purpose", "base", "cdpm_stand_tasks",
                  "params_relation_set")
#: Жёсткие подразделы словарей CDPM: перечень имён с единицами (факт
#: части 7-Б), закрытые словари-перечни и зоны с провенансом.
HARD_CDPM_KEYS = ("purpose", "source", "snapshot_code", "snapshot_fingerprint",
                  "params", "vocabularies", "zones")
#: Подразделы словарей CDPM, которые правит СЛУЖБА (симметризация
#: дополнений D02/X01 и порядок лексикона): они обязаны быть проекцией,
#: иначе первая же сборка с новой семьёй краснит L06 по построению.
PROJECTED_CDPM_KEYS = ("subject_words", "complements", "lexicon_order")

#: Ключи словаря, которые инструмент выводит САМ (из кода, снимка и
#: посева). Всё, что каталог несёт сверх этого перечня, — словарь
#: службы: проекция переносит его насквозь через `dictionaries_extra`
#: (контракт §4 «служба вправе завести свой словарь, и пересборка его не
#: потеряет»). Перечень сверяется со сборкой в `build()`: разойдись он —
#: отказ словами, а не тихая потеря чужого словаря.
TOOL_DICTIONARY_KEYS = (
    "classes", "kinds", "zones", "family_natures", "violation_units",
    "violation_sizes", "history_read_kinds", "nature_witnesses",
    "param_units", "code_units", "param_relations", "param_tz",
    "area_qualifiers", "qualifier_kinds", "fit_categories", "condition_units",
    "condition_relations", "weekdays", "executor_kinds", "stand_task_kinds",
    "executor_states", "record_statuses", "request_states",
    "request_source_kinds", "request_submitters", "actors", "origin_kinds",
    "history_kinds", "rates_policies", "products", "languages", "task_kinds",
    "stand_results", "param_sources", "check_template_groups",
    "synthetic_codes", "acceptance_default", "lexicon_order", "world_forms",
    "check_templates", "unbound_params")

#: Ключи, которые ДОПИСЫВАЕТ читатель поверх файла словарей
#: (`Catalog.dictionaries` считает `area_param_names` по записям): в
#: `dictionaries.json` их нет, и переносить их проекцией значило бы
#: завести второй носитель у производной величины.
READER_DICTIONARY_KEYS = ("area_param_names",)

DEFAULT_VERSION = "0.2.0-draft.2"
DEFAULT_RELEASED_AT = "2026-09-08"
DEFAULT_RELEASED_BY = "сессия COSMOS"
DEFAULT_NOTE = ("шаг 4, черновик 2 (слово владельца 08.09): отношение «не "
                "больше» у duty_extension_cap, long_duty_threshold и "
                "workload_spread_threshold; раздача семьям трёх имён словаря "
                "— fleet_type, max_sit_duration, max_weekly_block")

#: Необязательные поля шаблонов проверок (data_checks.validate_spec и
#: исполнители: min/max; роль, отношение eq|max|min и колонка различных
#: значений у счёта состава; тройка области у разрыва) — явная таблица
#: инструмента и теста паритета L09 (со шага 2 — константа data_checks).
CHECK_TEMPLATE_OPTIONALS = {
    "значение-в-диапазоне": {"optional_fields": ["min", "max"],
                             "at_least_one_of": ["min", "max"]},
    "счёт-состава-группы": {"optional_fields": ["role_column", "role_value",
                                                "relation", "distinct_column"],
                            "optional_groups": [["role_column", "role_value"]]},
    "минимальный-разрыв-часов": {"optional_groups": [["place_column", "base_column", "area"]]},
}
#: Уточнители параметров, не выводимые из кода: crew_variant — уточнитель
#: «расширенный» (cdpm/model.py restore_crew_variant_qualifiers). area
#: приходит из снимка (area_param_names), base_code — из Kotlin
#: (ручка с keyed = true).
QUALIFIERS = {"crew_size": "crew_variant"}
CONDITION_UNITS = {"in": ("code_list", "in"), "yes": ("flag", "yes")}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
#: Виды чтения истории (словарь `history_read_kinds`, 0.5.0): остаток
#: накопителя, граница последней смены, серия через границу горизонта,
#: календарь суток за скользящее окно.
HISTORY_READ_KINDS = ["accumulator", "boundary", "series", "window"]
#: Чем меряется штраф мягкого правила (`violation_sizes`): числом единиц
#: нарушения либо величиной превышения.
VIOLATION_SIZES = ["count", "magnitude"]
#: Свидетели природы семьи (`nature_witnesses`) — кого спрашивает закон
#: паритета L15: таблицы решателя, перечни сертификата ростера, вердикт
#: судьи заявки, «свидетеля нет» (отрицательное свидетельство).
NATURE_WITNESSES = ["solver_tables", "certifier_registry", "change_request",
                    "none"]
#: Синтетические коды стендовых миров (только они законны в задачах, A03).
SYNTHETIC_STATIONS = [f"ST{i}" for i in range(1, 10)]
SYNTHETIC_FLEETS = [f"T{i}" for i in range(1, 10)]
SYNTHETIC_DATASETS = [f"S{i}" for i in range(1, 10)]
SYNTHETIC_EMPLOYEE = "^P[1-9][0-9]*$"


class BuildError(SystemExit):
    """Отказ сборки словами — ничего не пишется."""


# -------------------------------------------------------------- сборка
def legacy_registry_rev(families_map: dict) -> str:
    """Старая формула registry_rev кода (12 hex, разделители json по
    умолчанию) — над картой {семья: отсортированные параметры}."""
    canon_txt = json.dumps(families_map, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon_txt.encode("utf-8")).hexdigest()[:12]


def compose_world(seed: dict, task: dict, where: str) -> dict:
    """Мир задачи: либо ВСТРОЕННЫЙ («world» прямо в задаче — так пришли
    задачи формы `tables` из cdpm/stand_tables_seed.json, договорённость
    С2: их миры — таблицы линии данных, повторно ими никто не
    пользуется, и именованный мир был бы словарём из одного жильца),
    либо копия именованного мира посева, поверх которой ложится правка
    задачи по верхним ключам (замена, не слияние: перечень параметров
    мира задаёт состав правил стенда, и «доливка» чужих имён была бы
    тихой правкой постановки).

    Оба способа сразу — отказ: у мира обязан быть один источник."""
    inline = task.get("world")
    ref = task.get("world_ref")
    if inline is not None:
        if ref is not None or task.get("world_patch"):
            raise BuildError(f"{where}: у задачи и встроенный мир, и ссылка "
                             f"на именованный — источник мира обязан быть "
                             f"один")
        if not isinstance(inline, dict):
            raise BuildError(f"{where}: встроенный мир не объект")
        return copy.deepcopy(inline)
    worlds = seed.get("stand_worlds") or {}
    if ref not in worlds:
        raise BuildError(f"{where}: мира {ref!r} в посеве нет")
    world = copy.deepcopy(worlds[ref])
    for k, v in (task.get("world_patch") or {}).items():
        world[k] = copy.deepcopy(v)
    return world


def stand_tasks_of(seed: dict, tasks: list, where: str) -> list:
    out = []
    for i, t in enumerate(tasks or []):
        w = f"{where}.stand_tasks[{i}]"
        out.append({"id": t["id"], "world": compose_world(seed, t, w),
                    "expected": copy.deepcopy(t["expected"]),
                    "note": t.get("note")})
    return out


def load_seed(path=None) -> dict:
    """Посев с диска. Умолчание — посев рядом с инструментом; папке с
    собственным `tools/catalog_seed.json` (копия каталога со своим
    посевом) отдаётся её посев."""
    return json.loads(Path(path or SEED).read_text(encoding="utf-8-sig"))


def seed_of(out) -> dict:
    """Посев, которым судится папка `out`: её собственный, если он там
    есть, иначе посев инструмента."""
    own = Path(out) / SEED_REL
    return load_seed(own if own.is_file() else SEED)


def request_params(requests_doc: dict | None) -> dict:
    """{имя параметра: (единица, отношение, номер заявки)} по черновикам
    заявок папки — независимый свидетель имени вне Kotlin (решение 0.6).

    Заявку заводит служба, а принимает владелец: `requests.json` —
    запретный путь агента, поэтому черновик заявки — единственный якорь
    имени, которого не знают ни словарь CDPM, ни решатель. Строка
    истории записи свидетелем не считается: её пишет тот же агент, что и
    запись."""
    out: dict[str, tuple] = {}
    for rq in ((requests_doc or {}).get("requests") or []):
        if not isinstance(rq, dict):
            continue
        if rq.get("state") not in REQUEST_WITNESS_STATES:
            continue
        draft = rq.get("draft")
        if not isinstance(draft, dict):
            continue
        rid = str(rq.get("request_id") or "")
        for p in (draft.get("params") or []):
            if not isinstance(p, dict) or not p.get("name"):
                continue
            out.setdefault(str(p["name"]),
                           (p.get("unit"), p.get("relation"), rid))
    return out


#: Состояния заявки, при которых её черновик — свидетель: заявка принята
#: владельцем и ещё не отвергнута.
REQUEST_WITNESS_STATES = ("принята", "исполнитель заявлен", "есть")


def build(version: str, released_at: str, released_by: str, note: str, *,
          seed: dict | None = None, requests_doc: dict | None = None,
          kt: dict | None = None) -> dict:
    """{относительный путь: текст файла} — детерминированно.

    `kt` — таблицы решателя ДАННЫМИ: умолчание снимает их с Kotlin, а
    закон паритета передаёт свои (стенд «решатель вырос на ручку» судит
    воспроизводимость по тому же свидетелю, что и остальные правила)."""
    from cdpm import data_checks as dc
    from vmp.run import roster_certify as rc
    from vmp import stand_components, stand_tasks

    seed = load_seed() if seed is None else seed
    # Словари CDPM — ДАННЫМИ из посева (часть 7-А шага 7): снимок
    # final-report/KATALOG-CDPM-SLOVARI-CLD.json и импорты cdpm.model /
    # cdpm.family_lexicon сняты, иначе стадия 1б CDPM (снятие
    # _SUBJECT_WORDS, PARAMS, SOFT_FAMILIES и самого снимка) обрушила бы
    # сборку каталога. Перенос был машинный; с части 7-Б снимок снят, и
    # «каталог ≡ посев» стерегут L05, L06 и L07 гейта паритета.
    snap = seed["cdpm_dictionaries"]
    kt = ktab.tables() if kt is None else kt
    voc = snap["vocabularies"]
    area_names = set(voc["area_param_names"])
    order = list(seed["families_order"])
    base = seed["base"]
    req_params = request_params(requests_doc)

    # Карта параметр → семья целиком: проекция каталога. База заморозки
    # (снимок шага 1) живёт в жёстком блоке `base`, а прибавку к ней
    # ВЫЧИСЛЯЕТ дельта — второго носителя одной и той же карты не бывает.
    param_to_family = dict(seed["param_families"])
    by_family: dict[str, list[str]] = {f: [] for f in order}
    for name, fam in param_to_family.items():
        if fam not in by_family:
            raise BuildError(f"параметр {name!r} привязан к неизвестной семье "
                             f"{fam!r}")
        by_family[fam].append(name)

    def unit_of(name: str) -> str:
        if name in snap["params"]:
            return snap["params"][name]
        if name in kt["history_units"]:
            return kt["history_units"][name][0]
        if name in kt["knobs"]:
            return kt["knobs"][name]["unit"]
        if name == kt["sit_param"]:
            units = {k["unit"] for k in kt["sit_knobs"].values()}
            if len(units) == 1:
                return units.pop()
        if name in req_params and req_params[name][0]:
            return str(req_params[name][0])
        raise BuildError(f"единицу параметра {name!r} не подтверждает ни "
                         f"словарь CDPM, ни таблицы решателя, ни заявка")

    # Отношения, назначенные СЛОВОМ ВЛАДЕЛЬЦА именам, которых нет в
    # таблицах решателя (§14 п. 3 схемы, черновик 2 шага 4). Имя,
    # известное Kotlin, здесь незаконно: отношение исполнителя пишет
    # код, и назначение поверх него было бы тихим расхождением.
    relation_set = dict(seed.get("params_relation_set") or {})
    for name, rel in sorted(relation_set.items()):
        if name in kt["knobs"] or name in kt["history_units"] \
                or name == kt["sit_param"]:
            raise BuildError(f"отношение {name!r} назначено посевом, но имя "
                             f"есть в таблицах решателя — отношение обязано "
                             f"приходить из кода")
        if name not in param_to_family:
            raise BuildError(f"отношение назначено имени {name!r} без семьи — "
                             f"отношения имён словаря живут в unbound_params")
        if rel not in voc["param_relations"]:
            raise BuildError(f"отношение {rel!r} имени {name!r} вне словаря "
                             f"param_relations")

    def relation_of(name: str):
        if name == kt["sit_param"]:
            rels = {k["relation"] for k in kt["sit_knobs"].values()}
            return rels.pop() if len(rels) == 1 else None
        if name in kt["knobs"]:
            return kt["knobs"][name]["relation"]
        if name in kt["history_units"]:
            return kt["history_units"][name][1]
        if name in relation_set:
            return relation_set[name]
        if name in req_params and req_params[name][1]:
            return str(req_params[name][1])
        return None

    def qualifier_of(name: str):
        if name in area_names:
            return "area"
        if name in kt["knobs"] and kt["knobs"][name]["keyed"]:
            return "base_code"
        return QUALIFIERS.get(name)

    # семьи
    complements: dict[str, list[str]] = {}
    for a, b in snap["complements"]:
        complements.setdefault(a, []).append(b)
        complements.setdefault(b, []).append(a)
    cap_family = param_to_family.get("duty_extension_cap")
    families = []
    for fid in order:
        sd = seed["families"].get(fid)
        if sd is None:
            raise BuildError(f"в посевном файле нет семьи {fid!r}")
        if fid in rc.WORLD_PARAM_FAMILIES:
            nature = "world_param"
        elif fid in kt["history_families"]:
            nature = "history"
        elif fid in rc.PEOPLE_FAMILIES:
            nature = "people"
        else:
            nature = "schedule"
        if sd["nature"] != nature:
            raise BuildError(f"{fid}: природа по посеву {sd['nature']!r}, по "
                             f"коду {nature!r}")
        param_names = sorted(by_family.get(fid, []))
        param_subjects = sd.get("param_subject_words", {})
        if not isinstance(param_subjects, dict):
            raise BuildError(f"{fid}: param_subject_words должен быть картой параметров")
        unknown_subjects = set(param_subjects) - set(param_names)
        if unknown_subjects:
            raise BuildError(f"{fid}: param_subject_words содержит чужие параметры: "
                             + ", ".join(sorted(map(str, unknown_subjects))))
        params = []
        for name in param_names:
            param = {"name": name, "unit": unit_of(name),
                     "relation": relation_of(name),
                     "qualifier": qualifier_of(name),
                     "note": sd["param_notes"].get(name)}
            # Необязательные слова — курируемые данные, не вывод из кода.
            # Отсутствие остаётся отсутствием; явное значение (в том числе
            # ошибочное) сохраняется для обычной проверки выбранной схемой.
            if name in param_subjects:
                param["subject_words"] = copy.deepcopy(param_subjects[name])
            params.append(param)
        conditions = []
        if fid == cap_family:
            notes = {c["name"]: c.get("note") for c in sd["conditions"]}
            for cname, (_col, rel) in rc.EXTENSION_CONDITIONS.items():
                unit, relation = CONDITION_UNITS[rel]
                conditions.append({"name": cname, "unit": unit,
                                   "relation": relation,
                                   "note": notes.get(cname)})
        kinds = sorted(set(sd["executor_kinds"]))
        history = copy.deepcopy(sd["history"])
        families.append({
            "id": fid, "class": sd["class"], "title": sd["title"],
            "subject": sd["subject"],
            "subject_words": list(snap["subject_words"][fid])
            if fid in snap["subject_words"] else None,
            "complements": sorted(complements.get(fid, [])),
            "kind": sd["kind"], "zone": sd["zone"], "nature": nature,
            "nature_confirmed_by": sd["nature_confirmed_by"],
            "violation_unit": sd["violation_unit"],
            "violation_size": sd["violation_size"],
            "reads_history": copy.deepcopy(sd["reads_history"]),
            "params": params, "param_sets": sd["param_sets"],
            "conditions": conditions, "rule_form": sd["rule_form"],
            "semantics": sd["semantics"],
            "executor_kinds": kinds,
            "check_templates": list(sd["check_templates"]),
            "stand_tasks": stand_tasks_of(seed, sd.get("stand_tasks"),
                                          f"families/{fid}"),
            "acceptance": sd.get("acceptance"), "origin": sd["origin"],
            "status": sd.get("status", "есть"), "history": history,
            "note": sd.get("note")})

    # Направление — курируемое поле контракта 0.6, не вывод из имени
    # метрики. Старая закрытая схема 0.5 его не допускает. Отсутствие
    # в современном посеве не заменяется minimize: его отвергнет validate.
    schema_src = schema_path_for_version(version)
    modern_criteria = schema_src != schema_path_for_version()
    # критерии
    criteria = []
    check_metric_criteria(seed, kt)
    for cid in sorted(seed["criteria"]):
        sd = seed["criteria"][cid]
        rf = sd.get("rates_form")
        criteria.append({
            "id": cid, "class": sd["class"], "title": sd["title"],
            "zone": sd["zone"], "needs": sd.get("needs"),
            "semantics": {"formula": sd.get("formula"),
                          "rates_form": ({"fields": list(rf["fields"]),
                                          "policy": rf["policy"],
                                          "note": rf.get("note")}
                                         if rf else None),
                          "text": sd["semantics_text"]},
            "instance_form": sd["instance_form"],
            "computable_by": list(sd["computable_by"]),
            "refusal": sd["refusal"],
            "stand_tasks": stand_tasks_of(seed, sd.get("stand_tasks"),
                                          f"criteria/{cid}"),
            "acceptance": sd.get("acceptance"), "origin": sd["origin"],
            "status": sd.get("status", "есть"),
            "history": copy.deepcopy(sd["history"]), "note": sd.get("note")})
        if modern_criteria and "direction" in sd:
            criteria[-1]["direction"] = copy.deepcopy(sd["direction"])

    # словари
    check_templates = {}
    for name, fields in dc.TEMPLATES.items():
        if name in dc.WAVE_SOFT_TEMPLATES:
            group = "soft_wave"
        elif name in dc.HISTORY_FORM_TEMPLATES:
            group = "history_form"
        else:
            group = "data"
        opt = CHECK_TEMPLATE_OPTIONALS.get(name, {})
        check_templates[name] = {
            "fields": list(fields),
            "optional_fields": list(opt.get("optional_fields", [])),
            "optional_groups": [list(g) for g in opt.get("optional_groups", [])],
            "at_least_one_of": list(opt.get("at_least_one_of", [])),
            "group": group}
    check_templates[rc.SOFT_MEASURE_TEMPLATE] = {
        "fields": ["unit", "statement"], "optional_fields": [],
        "optional_groups": [], "at_least_one_of": [], "group": "certificate"}
    check_templates[rc.WORLD_PARAM_TEMPLATE] = {
        "fields": ["statement"], "optional_fields": [], "optional_groups": [],
        "at_least_one_of": [], "group": "certificate"}
    for prefix in ("история-*", "горизонт-*"):
        check_templates[prefix] = {
            "fields": ["sheet"], "optional_fields": [], "optional_groups": [],
            "at_least_one_of": [], "group": "builtin_prefix"}

    unbound = {}
    for name, unit in snap["params"].items():
        if name in param_to_family:
            continue
        unbound[name] = {"unit": unit,
                         "relation": kt["knobs"].get(name, {}).get("relation"),
                         "source": "словарь CDPM",
                         "note": seed["unbound_param_notes"].get(name)}
    solver_names = dict(kt["knobs"])
    for name, knob in solver_names.items():
        if name in param_to_family or name in snap["params"]:
            continue
        unbound[name] = {"unit": knob["unit"], "relation": knob["relation"],
                         "source": "словарь решателя",
                         "note": seed["solver_only_param_notes"].get(name)}

    dictionaries = {
        "classes": ["core", "extension"],
        "kinds": ["HARD", "SOFT"],
        "zones": list(snap["zones"]),
        "family_natures": ["schedule", "people", "world_param", "history"],
        "violation_units": dict(seed["violation_units"]),
        "violation_sizes": list(VIOLATION_SIZES),
        "history_read_kinds": list(HISTORY_READ_KINDS),
        "nature_witnesses": list(NATURE_WITNESSES),
        "param_units": list(voc["param_units"]),
        "code_units": list(voc["code_units"]),
        "param_relations": list(voc["param_relations"]),
        "param_tz": list(voc["param_tz"]),
        "area_qualifiers": list(voc["area_qualifiers"]),
        "qualifier_kinds": ["area", "base_code", "crew_variant"],
        "fit_categories": list(voc["fit_categories"]),
        "condition_units": ["code_list", "flag"],
        "condition_relations": ["in", "yes"],
        "weekdays": list(WEEKDAYS),
        "executor_kinds": ["kotlin_constraint", "kotlin_knob", "kotlin_form",
                           "kotlin_metric", "kotlin_logic_form",
                           "builder_method", "condition_fn", "check_template",
                           "check_code", "export_form"],
        # Derive the modern contract from the actual stand adapters, not
        # the wider executor dictionary (condition_fn has no stand kind).
        # Historical closed schemas retain their original six-name contract.
        "stand_task_kinds": (
            sorted(set(stand_tasks.PURE_KINDS) | set(stand_tasks.SOLVER_KINDS)
                   | set(stand_components.KINDS))
            if modern_criteria else
            ["kotlin_constraint", "kotlin_logic_form", "kotlin_metric",
             "builder_method", "check_template", "check_code"]),
        "executor_states": ["есть", "заявлен", "нет"],
        "record_statuses": ["есть", "снята"],
        "request_states": ["подана", "принята", "исполнитель заявлен", "есть",
                           "отклонена"],
        "request_source_kinds": ["посадка категории C", "пробел при нажатии",
                                 "вручную"],
        "request_submitters": ["CDPM", "COSMOS", "аналитик", "владелец"],
        "actors": ["владелец", "сессия COSMOS", "сессия CDPM", "аналитик",
                   "контур самоулучшения"],
        "origin_kinds": ["решение владельца", "заявка", "инвентаризация",
                         "синтетика", "обобщено из ИДАК"],
        "history_kinds": ["создание", "решение владельца", "заявка",
                          "изменение", "инвентаризация", "замечание"],
        "rates_policies": ["MUST_BE_EXTERNAL"],
        "products": ["CDPM", "COSMOS"],
        "languages": ["python", "kotlin"],
        "task_kinds": ["pairing", "rostering"],
        "stand_results": ["passed", "failed", "skipped"],
        "param_sources": ["словарь CDPM", "словарь решателя"],
        "check_template_groups": ["data", "history_form", "soft_wave",
                                  "certificate", "builtin_prefix"],
        "synthetic_codes": {"stations": list(SYNTHETIC_STATIONS),
                            "fleet_types": list(SYNTHETIC_FLEETS),
                            "dataset_ids": list(SYNTHETIC_DATASETS),
                            "employee_pattern": SYNTHETIC_EMPLOYEE},
        "acceptance_default": seed["acceptance_default"],
        "lexicon_order": list(snap["lexicon_order"]),
        "world_forms": seed["world_forms"],
        "check_templates": check_templates,
        "unbound_params": unbound,
    }
    # Ключи словарей, которых инструмент не выводит из кода, он передаёт
    # НАСКВОЗЬ: служба вправе завести свой словарь (например, классы
    # документов), и пересборка не должна его терять. Ключи, которые
    # инструмент считает сам, из этого раздела не берутся — иначе
    # проекция спорила бы с кодом молча.
    if set(dictionaries) != set(TOOL_DICTIONARY_KEYS):
        raise BuildError(
            f"перечень словарей инструмента разошёлся с константой "
            f"TOOL_DICTIONARY_KEYS: лишние "
            f"{sorted(set(dictionaries) - set(TOOL_DICTIONARY_KEYS))}, "
            f"потерянные "
            f"{sorted(set(TOOL_DICTIONARY_KEYS) - set(dictionaries))}")
    for key, value in (seed.get("dictionaries_extra") or {}).items():
        if key in dictionaries:
            raise BuildError(f"словарь {key!r} инструмент выводит из кода — "
                             f"в разделе dictionaries_extra ему не место")
        dictionaries[key] = copy.deepcopy(value)

    body = canon.normalize({"families": families, "criteria": criteria,
                            "dictionaries": dictionaries})
    fingerprint = canon.fingerprint_of(body)
    families_map = {f: sorted(ps) for f, ps in by_family.items()}
    lrr = legacy_registry_rev(families_map)
    header = {
        "form_version": 1, "name": "template-catalog", "version": version,
        "fingerprint": fingerprint,
        "legacy_registry_rev": lrr,
        "released_at": released_at, "released_by": released_by, "note": note,
        "sections": {"families": "families.json", "criteria": "criteria.json",
                     "dictionaries": "dictionaries.json",
                     "requests": "requests.json"},
        "gates": dict(seed["gates"]),
    }
    delta = compute_delta(body, header, base, kt, snap, relation_set,
                          req_params)
    files = {
        "catalog.json": dump_json(header),
        "families.json": dump_json({"form_version": 1,
                                    "families": body["families"]}),
        "criteria.json": dump_json({"form_version": 1,
                                    "criteria": body["criteria"]}),
        "dictionaries.json": dump_json({"form_version": 1,
                                        **body["dictionaries"]}),
        # requests.json инструмент НЕ порождает: заявки лежат вне
        # отпечатка, их пишет служба каталога, и пересборка не вправе
        # их стирать (К9 договорённостей с CDPM, решение 0.11 шага 6).
        # На чистой папке пустой файл заводит main() — один раз.
        "versions.json": dump_json({"form_version": 1, "versions": []}),
        "transition/families_order.json": dump_json(list(order)),
        "transition/step4_additions.json": dump_json(delta),
        "schema/catalog.schema.json": schema_src.read_text(encoding="utf-8"),
    }
    return files


#: Зона критерия-метрики выводится из задач решателя, догадки нет.
ZONE_BY_TASKS = {("pairing", "rostering"): "BOTH", ("pairing",): "PAIRING",
                 ("rostering",): "ROSTERING"}


def check_metric_criteria(seed: dict, kt: dict) -> None:
    """Записи критериев-метрик посева ≡ словарю метрик решателя: перечень
    имён, зона по задачам, отказ дословно. Расхождение — отказ сборки:
    пересказ Kotlin своими словами был бы тихим расхождением каталога с
    решателем.

    Метрику называет САМА ЗАПИСЬ (вид `kotlin_metric` среди
    `computable_by`), а не перечень прибавленного: перечень прибавки
    вычисляется дельтой и определением роста быть не может (решение 0.2
    пакета паритета)."""
    metrics = kt["metrics"]
    named = sorted(cid for cid, sd in seed["criteria"].items()
                   if "kotlin_metric" in (sd.get("computable_by") or []))
    if named != sorted(metrics):
        raise BuildError(f"критерии-метрики посева {named} ≠ словарю "
                         f"метрик решателя {sorted(metrics)}")
    for cid in named:
        sd = seed["criteria"][cid]
        m = metrics[cid]
        zone = ZONE_BY_TASKS.get(tuple(sorted(m["tasks"])))
        if zone is None:
            raise BuildError(f"{cid}: задачи метрики {m['tasks']} — зона не "
                             f"выводится")
        if sd["zone"] != zone:
            raise BuildError(f"{cid}: зона посева {sd['zone']!r}, по задачам "
                             f"решателя {zone!r}")
        if sd["refusal"] != m["refusal"]:
            raise BuildError(f"{cid}: отказ посева не дословен отказу "
                             f"решателя")
        if "kotlin_metric" not in sd["computable_by"]:
            raise BuildError(f"{cid}: запись метрики без вида kotlin_metric")


def _records_of(body: dict) -> list:
    """[(адрес, раздел, запись)] по телу каталога — семьи, затем критерии."""
    out = []
    for section in ("families", "criteria"):
        for rec in body.get(section) or []:
            out.append((f"{section}/{rec['id']}", section, rec))
    return out


def _history_tail(history: list, marker: dict | None, known: bool) -> list:
    """Хвост истории записи после базовой отметки заморозки.

    У записи, которой в базе не было, хвост — вся история: заморозка её
    не знает, и «прибавка» у неё полная. У базовой записи хвост — всё,
    что легло ПОСЛЕ последней отметки шага 1 (её дословный вид — жёсткий
    блок `base.history_marker`); отметки в истории нет — прибавкой
    считается вся история, и правило L17 назовёт это расхождением."""
    entries = [h for h in (history or []) if isinstance(h, dict)]
    if not known or not isinstance(marker, dict):
        return copy.deepcopy(entries)
    last = -1
    for i, h in enumerate(entries):
        if h == marker:
            last = i
    if last < 0:
        return copy.deepcopy(entries)
    return copy.deepcopy(entries[last + 1:])


def compute_delta(body: dict, header: dict, base: dict, kt: dict, snap: dict,
                  relation_set: dict, req_params: dict,
                  strict: bool = True) -> dict:
    """Дельта перехода — ВЫЧИСЛЕННЫЙ переходный носитель вне отпечатка.

    Ожидаемое закона паритета = база заморозки (жёсткий блок `base`
    посева, он же снимок шага 1) ⊕ эта дельта. Дельта НАКОПИТЕЛЬНАЯ: она
    называет всё расстояние от заморозки до нынешнего каталога, а не
    разницу между соседними черновиками. До пакета паритета её ключи
    курировались руками в посеве, и первая же сборка службой краснила
    гейты содержания по построению; теперь каждый ключ — разность
    каталога и базы, и второго применения гейту не нужно.

    Форма 1 и прежние ключи сохранены: их читают обёртка COSMOS, сторож
    дрейфа и правила шага 4. Новых ключа два — `families_added` (семьи
    сверх базы) и `fingerprint` (отпечаток каталога, от которого дельта
    посчитана: по нему правило L23 судит её свежесть).

    `strict` — для ИНСТРУМЕНТА: имя без свидетеля единицы или отношения
    останавливает сборку словами. Закон паритета зовёт дельту с
    `strict=False`: ему нужно досчитать её до конца, чтобы красным стало
    ОДНО правило по существу (L05/L05b), а не полдюжины по цепочке
    «дельта не вычислена»."""
    base_families = set(base.get("families") or [])
    base_criteria = set(base.get("criteria") or [])
    base_p2f = dict(base.get("param_to_family") or {})
    base_kinds = {k: set(v) for k, v in (base.get("executor_kinds")
                                         or {}).items()}
    marker = base.get("history_marker")
    known_solver = (set(kt["knobs"]) | set(kt["history_units"])
                    | {kt["sit_param"]})

    def _param_of(rec, name):
        for p in rec["params"]:
            if p["name"] == name:
                return p
        raise BuildError(f"{rec['id']}: параметра {name!r} в записи нет")

    def _source_of(name: str) -> str:
        if name in snap["params"]:
            return "словарь CDPM"
        if name in known_solver:
            return "словарь решателя"
        if name in req_params:
            return f"заявка {req_params[name][2]}"
        if strict:
            raise BuildError(f"имя {name!r}: единицу не подтверждает ни "
                             f"словарь CDPM, ни таблицы решателя, ни заявка")
        return NO_WITNESS

    params_to_families, relation_added = {}, {}
    kinds_added, history_added, families_added = {}, {}, {}
    tasks, without = {}, []
    for addr, section, rec in _records_of(body):
        ids = [t["id"] for t in rec.get("stand_tasks") or []]
        tasks[addr] = ids
        if not ids:
            without.append(addr)
        known = (rec["id"] in base_families if section == "families"
                 else rec["id"] in base_criteria)
        tail = _history_tail(rec.get("history"), marker, known)
        if tail:
            history_added[addr] = tail
        if section != "families":
            continue
        if not known:
            families_added[rec["id"]] = {
                "class": rec["class"], "title": rec["title"],
                "kind": rec["kind"], "zone": rec["zone"],
                "nature": rec["nature"],
                "violation_unit": rec["violation_unit"]}
        added_kinds = sorted(set(rec["executor_kinds"])
                             - base_kinds.get(rec["id"], set()))
        if added_kinds:
            kinds_added[rec["id"]] = added_kinds
        for p in rec["params"]:
            name = p["name"]
            if name not in base_p2f:
                params_to_families[name] = {
                    "family": rec["id"], "unit": p["unit"],
                    "relation": p["relation"], "qualifier": p["qualifier"],
                    "was": {"source": _source_of(name),
                            "relation": kt["knobs"].get(name, {})
                            .get("relation")}}
            # Отношение, назначенное имени ВНЕ таблиц решателя: слово
            # владельца (жёсткий блок посева) либо принятая заявка. Имя
            # решателя сюда не попадает — его отношение пишет код.
            if p["relation"] is not None and name not in known_solver:
                if name in relation_set:
                    by = "владелец"
                elif name in req_params and req_params[name][1]:
                    by = f"заявка {req_params[name][2]}"
                elif strict:
                    raise BuildError(
                        f"отношение имени {name!r} вне таблиц решателя не "
                        f"подтверждает ни слово владельца, ни заявка")
                else:
                    by = NO_WITNESS
                relation_added[name] = {
                    "family": rec["id"], "unit": p["unit"],
                    "relation": p["relation"],
                    "was": {"relation": kt["knobs"].get(name, {})
                            .get("relation"), "by": by}}

    dicts = body["dictionaries"]
    bound = set(params_to_families)
    dates = sorted({h["date"] for entries in history_added.values()
                    for h in entries})
    return {
        "form_version": 1, "version": header["version"],
        "legacy_registry_rev": header["legacy_registry_rev"],
        "base": {k: base[k] for k in ("code", "fingerprint",
                                      "legacy_registry_rev", "snapshot",
                                      "version")},
        "params_to_families": dict(sorted(params_to_families.items())),
        "params_relation_set": dict(sorted(relation_added.items())),
        "executor_kinds_added": dict(sorted(kinds_added.items())),
        "criteria_added": sorted({rec["id"] for rec in body["criteria"]}
                                 - base_criteria),
        "families_added": dict(sorted(families_added.items())),
        "stand_tasks": dict(sorted(tasks.items())),
        "records_without_stand_tasks": sorted(without),
        "history_added": dict(sorted(history_added.items())),
        "dates": dates,
        "dictionaries": {
            "world_forms_added": sorted(set(dicts.get("world_forms") or {})
                                        - set(base.get("world_forms") or [])),
            "unbound_params_removed": sorted(
                bound & (set(snap["params"]) | set(kt["knobs"]))),
            "synthetic_codes_added": sorted(
                set(dicts.get("synthetic_codes") or {})
                - set(base.get("synthetic_codes") or []))},
        "gates": dict(header["gates"]),
        "executors_source": EXECUTORS_SOURCE,
        "fingerprint": header["fingerprint"],
    }


# -------------------------------------------- обратная проекция каталога
#: Курируемые поля записи семьи — всё, чего инструмент не выводит из кода.
CURATED_FAMILY = ("subject", "title", "class", "kind", "zone", "nature",
                  "nature_confirmed_by", "violation_unit", "violation_size",
                  "reads_history", "status", "executor_kinds",
                  "check_templates", "rule_form", "param_sets", "semantics",
                  "origin", "history", "acceptance", "note")
CURATED_CRITERION = ("title", "class", "zone", "direction", "needs", "instance_form",
                     "computable_by", "refusal", "status", "origin",
                     "history", "acceptance", "note")


def _tasks_of(rec: dict) -> list:
    """Стендовые задачи записи со ВСТРОЕННЫМИ мирами: именованных миров
    посева больше нет — мир задачи правится в записи каталога службой, а
    посев его только отражает."""
    out = []
    for t in rec.get("stand_tasks") or []:
        out.append({"id": t["id"], "world": copy.deepcopy(t["world"]),
                    "expected": copy.deepcopy(t["expected"]),
                    "note": t.get("note")})
    return out


def curated_of(cat, *, hard: dict, order: list) -> dict:
    """Посев по каталогу: жёсткие блоки `hard` ⊕ машинная проекция.

    Обратная проекция записи инструмента: всё, что инструмент выводит из
    кода (единицы, отношения, уточнители, природа, условия, шаблоны
    проверок, несвязанные имена), в посев НЕ пишется — иначе у одной
    правды стало бы два носителя. Пишется то, чего в коде нет: слова
    записи, её история, задачи стенда, карта имён и заметки."""
    families, criteria = {}, {}
    for rec in cat.families_full(status=None):
        sd = {k: copy.deepcopy(rec.get(k)) for k in CURATED_FAMILY}
        sd["executor_kinds"] = sorted(rec.get("executor_kinds") or [])
        sd["param_notes"] = {p["name"]: p.get("note")
                             for p in sorted(rec.get("params") or [],
                                             key=lambda p: str(p["name"]))}
        param_subjects = {p["name"]: copy.deepcopy(p["subject_words"])
                          for p in rec.get("params") or []
                          if "subject_words" in p}
        if param_subjects:
            sd["param_subject_words"] = param_subjects
        sd["conditions"] = [{"name": c["name"], "note": c.get("note")}
                            for c in rec.get("conditions") or []]
        sd["stand_tasks"] = _tasks_of(rec)
        families[str(rec["id"])] = sd
    for rec in cat.criteria(status=None).values():
        sd = {k: copy.deepcopy(rec.get(k)) for k in CURATED_CRITERION
              if k != "direction" or k in rec}
        sem = rec.get("semantics") or {}
        sd["formula"] = sem.get("formula")
        sd["rates_form"] = copy.deepcopy(sem.get("rates_form"))
        sd["semantics_text"] = sem.get("text")
        sd["stand_tasks"] = _tasks_of(rec)
        criteria[str(rec["id"])] = sd

    params = cat.params(status=None)
    dicts = cat.dictionaries()
    unbound = dicts.get("unbound_params") or {}
    lex = cat.lexicon(status=None)
    pairs = sorted({tuple(sorted((a, b))) for a, bs in
                    (lex.get("complements") or {}).items() for b in bs})
    seed = {k: copy.deepcopy(hard[k]) for k in HARD_SEED_KEYS if k in hard}
    cdpm = {k: copy.deepcopy(hard["cdpm_dictionaries"][k])
            for k in HARD_CDPM_KEYS if k in hard["cdpm_dictionaries"]}
    cdpm["subject_words"] = {f: list(w) for f, w
                             in sorted((lex.get("subject_words") or {}).items())}
    cdpm["complements"] = [list(p) for p in pairs]
    cdpm["lexicon_order"] = list(dicts.get("lexicon_order") or [])
    seed["cdpm_dictionaries"] = cdpm
    seed["families_order"] = list(order)
    seed["families"] = families
    seed["criteria"] = criteria
    seed["param_families"] = {n: p["family"] for n, p in params.items()
                              if p["family"] is not None}
    seed["unbound_param_notes"] = {
        n: s.get("note") for n, s in sorted(unbound.items())
        if s.get("source") == "словарь CDPM"}
    seed["solver_only_param_notes"] = {
        n: s.get("note") for n, s in sorted(unbound.items())
        if s.get("source") == "словарь решателя"}
    seed["violation_units"] = dict(dicts.get("violation_units") or {})
    seed["world_forms"] = copy.deepcopy(dicts.get("world_forms") or {})
    seed["acceptance_default"] = dicts.get("acceptance_default")
    seed["gates"] = dict(cat.header.get("gates") or {})
    # Словари, которых инструмент не выводит из кода, проекция переносит
    # НАСКВОЗЬ: служба вправе завести свой (классы документов заведены
    # первой её сборкой — `catalog_service/app.py::OPTIONAL_DICT_KEYS`), и
    # пересборка обязана его сохранить. Пустой раздел терял бы такой
    # словарь: `--check --from-catalog` называл бы расхождение, а
    # `--refresh` отказывался бы отпечатком.
    seed["dictionaries_extra"] = {
        k: copy.deepcopy(v) for k, v in sorted(dicts.items())
        if k not in TOOL_DICTIONARY_KEYS and k not in READER_DICTIONARY_KEYS}
    return seed


def order_of(out: Path, cat) -> list:
    """Порядок семей папки: переходный файл, а без него — порядок записей
    каталога (папка выпуска порядок несёт, копия-черновик может и нет)."""
    p = Path(out) / "transition" / "families_order.json"
    if p.is_file():
        order = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(order, list) and order:
            return [str(x) for x in order]
    return [str(r["id"]) for r in cat.families_full(status=None)]


def refreshed(out: Path, *, kt: dict | None = None) -> tuple[str, str]:
    """(текст посева, текст дельты) по каталогу папки `out` — то, что
    записал бы `--refresh`. Ничего не пишет.

    Считаются ПРОЕКЦИИ, и каталог здесь НЕ собирается. Прежде функция
    делала полную сборку по таблицам решателя и отказывала, если её
    отпечаток разошёлся с диском. На ветке класса «рост таблиц решателя»
    расхождение законно и ожидаемо: новая ручка объявлена записью
    КАНДИДАТА, а живой каталог её ещё не знает — и `--refresh --check`
    (а с ним правило L23 и `pytest -q` клона) краснел по построению, хотя
    посев и дельта дерева были свежи. Свежесть носителей и паритет
    каталога с кодом — разные суждения: второе судят `--check
    --from-catalog` (L18) и правила L05/L05b, и они называют беду по
    существу. Ту же формулу зовёт правило L23 — носитель истины один."""
    from template_catalog import load as _load
    cat = _load(Path(out))
    hard = seed_of(out)
    seed = curated_of(cat, hard=hard, order=order_of(out, cat))
    requests_doc, _words = read_requests(Path(out))
    body = {"families": cat.families_full(status=None),
            "criteria": list(cat.criteria(status=None).values()),
            "dictionaries": cat.dictionaries()}
    delta = compute_delta(body, cat.header, hard["base"],
                          kt if kt is not None else ktab.tables(),
                          hard["cdpm_dictionaries"],
                          dict(hard.get("params_relation_set") or {}),
                          request_params(requests_doc), strict=False)
    return dump_json(seed), dump_json(delta)


#: Пустая очередь заявок — форма файла, а не его содержание: заявки
#: приходят от службы, инструмент их только читает.
EMPTY_REQUESTS = {"form_version": 1, "requests": []}


def read_requests(out: Path) -> tuple[dict, str | None]:
    """(документ заявок папки назначения, слова о беде). Файла нет —
    пустая очередь без слов (чистая папка законна). Файл не читается —
    пустая очередь И слова: подменять негодный файл пустым молча
    нельзя, но и падать инструменту незачем — разделы отпечатка он
    собирает независимо от заявок."""
    p = Path(out) / "requests.json"
    if not p.is_file():
        return copy.deepcopy(EMPTY_REQUESTS), None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return copy.deepcopy(EMPTY_REQUESTS), f"{p} не читается: {e}"
    if not isinstance(doc, dict) or not isinstance(doc.get("requests"), list):
        return copy.deepcopy(EMPTY_REQUESTS), \
            f"{p}: нет корневого ключа requests со списком заявок"
    return doc, None


def validate_built(files: dict, requests_doc: dict | None = None) -> list:
    """Семена через validate() читателя до записи. Заявки берутся С
    ДИСКА (инструмент их не порождает): их форма и обезличенность
    судятся тем же validate(), что и разделы отпечатка."""
    def load(name):
        return json.loads(files[name])
    docs = {"families": load("families.json"), "criteria": load("criteria.json"),
            "dictionaries": load("dictionaries.json"),
            "requests": (requests_doc if isinstance(requests_doc, dict)
                         else copy.deepcopy(EMPTY_REQUESTS))}
    cat = Catalog(path=CATALOG_DIR, header=load("catalog.json"), docs=docs,
                  versions_doc=load("versions.json"), single_file=True)
    return cat.validate()


#: Код возврата защиты каталога, который уже ведёт служба (§2.12 проекта
#: шага 5). Отличается и от «расхождения» (1), и от «не собран» (2):
#: вызывающий обязан отличать «инструмент отказался писать» от ошибки
#: сборки.
GUARD_RC = 3


def refuse_write(out: Path) -> str | None:
    """Слова отказа, если в папке `out` каталог ведёт СЛУЖБА, — иначе
    None. Инструмент пишет `requests.json` и `versions.json` всегда
    пустыми (см. build()): без этой защиты первая же пересборка после
    выпуска стёрла бы журнал версий и заявки, а папки `releases/`
    осиротели бы. Ключа принуждения нет: спасённый журнал важнее
    удобства пересборки; для сверки есть `--check`, для новой сборки —
    `--out <временная папка>`.

    Судится fail-closed: файл, который есть, но не читается, считается
    непустым — доказать, что в нём пусто, нечем."""
    hint = ("каталог ведёт служба каталога (шаг 5): инструмент извлечения "
            "— только --check и --out <временная папка>")
    if (out / "releases").exists():
        return (f"запись в {out} не сделана: в папке есть releases/ — "
                f"{hint}")
    for name, key in (("versions.json", "versions"),
                      ("requests.json", "requests")):
        p = out / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
            items = doc.get(key) if isinstance(doc, dict) else None
        except (OSError, ValueError):
            return (f"запись в {out} не сделана: {name} не читается — "
                    f"пусто ли в нём, доказать нечем; {hint}")
        if items is None or (isinstance(items, list) and items):
            what = "непуст" if items else "без корневого ключа"
            return (f"запись в {out} не сделана: {name} {what} — {hint}")
    return None


def _compare_files(out: Path, files: dict, requests_words, fp: str) -> list:
    """Расхождения папки с пересобранными файлами.

    `requests.json` в сравнение не входит: его пишет служба, и
    «расхождение» с пустым перечнем инструмента было бы ложным (К9). Его
    форма судится тем же validate(), а нечитаемый файл называется
    расхождением словами. `versions.json` — по той же причине и с выпуска
    0.2.0: журнал версий ведёт ТОЛЬКО служба, он лежит вне отпечатка, а
    инструмент заводит его пустым; сравнивать пустой журнал с настоящим
    значило бы объявлять расхождением сам факт выпуска. Дельта
    сравнивается, только если она посчитана ОТ ЭТОГО каталога (ключ
    `fingerprint`): отставшую дельту называет правило L23, и второй голос
    о том же был бы шумом."""
    diff = []
    for rel, text in files.items():
        if rel == "versions.json":
            continue
        p = out / rel
        if rel == DELTA_REL and not _delta_is_of(p, fp):
            continue
        if not p.is_file():
            diff.append(f"нет файла {p}")
        elif p.read_bytes().replace(CRLF, LF) != text.encode("utf-8"):
            diff.append(f"отличается {p}")
    if requests_words:
        diff.append(requests_words)
    return diff


def check_from_catalog(out, *, kt: dict | None = None) -> tuple:
    """Закон L18 в процессе: (код, расхождения, числа) для папки `out`.

    Курируемые поля — из САМОЙ папки, жёсткие блоки — из посева
    инструмента, всё выводимое из кода — заново. Отказ сборки словами
    даёт код 2 и одну строку расхождения: воспроизвести папку не удалось."""
    from template_catalog import load as _load
    out = Path(out)
    try:
        cat = _load(out)
    except Exception as e:                                    # noqa: BLE001
        return 2, [f"каталог {out} не прочитан: {e}"], {}
    requests_doc, requests_words = read_requests(out)
    d = header_defaults(out)
    try:
        seed = curated_of(cat, hard=seed_of(out), order=order_of(out, cat))
        files = build(d["version"], d["released_at"], d["released_by"],
                      d["note"], seed=seed, requests_doc=requests_doc, kt=kt)
    except BuildError as e:
        return 2, [f"каталог не собран: {e}"], {}
    problems = validate_built(files, requests_doc)
    errors = [pr for pr in problems if pr.severity == "error"]
    if errors:
        return 2, [f"каталог не собран: {pr}" for pr in errors[:5]], {}
    head = json.loads(files["catalog.json"])
    tasks = sum(len(r["stand_tasks"])
                for name in ("families.json", "criteria.json")
                for r in json.loads(files[name])[name.split(".")[0]])
    diff = _compare_files(out, files, requests_words, head["fingerprint"])
    info = {"fingerprint": head["fingerprint"],
            "legacy_registry_rev": head["legacy_registry_rev"],
            "stand_tasks": tasks, "warnings": len(problems) - len(errors)}
    return (1 if diff else 0), diff, info


def header_defaults(out: Path) -> dict:
    """Версия, дата, автор и заметка из шапки папки назначения. Шапки нет
    (чистая папка) — прежние умолчания инструмента: второго источника
    истины о выпуске у инструмента быть не должно."""
    p = Path(out) / "catalog.json"
    fallback = {"version": DEFAULT_VERSION, "released_at": DEFAULT_RELEASED_AT,
                "released_by": DEFAULT_RELEASED_BY, "note": DEFAULT_NOTE}
    if not p.is_file():
        return fallback
    try:
        head = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback
    if not isinstance(head, dict):
        return fallback
    return {k: (head.get(k) if head.get(k) is not None else fallback[k])
            for k in fallback}


def refuse_refresh(out: Path) -> str | None:
    """Слова отказа `--refresh`, если посев папки писать нельзя.

    Папка выпуска неизменяема (в ней лежит снимок версии, а не рабочий
    каталог), а папка без `tools/` посева не ведёт: писать ей посев
    значило бы заводить второй носитель там, где его не было."""
    out = Path(out)
    if "releases" in out.parts:
        return (f"посев {out} не переписан: папка выпуска неизменяема — "
                f"освежайте посев на рабочем каталоге")
    if not (out / "tools").is_dir():
        return (f"посев {out} не переписан: в папке нет tools/ — посев "
                f"ведёт только рабочий каталог, а копии судит "
                f"--check --from-catalog")
    return None


def _refresh(out: Path, check: bool) -> int:
    """`--refresh` и `--refresh --check`: посев и дельта по каталогу."""
    words = refuse_refresh(out)
    if words is not None:
        print(words)
        return GUARD_RC
    try:
        seed_text, delta_text = refreshed(out)
    except BuildError as e:
        print(f"посев не пересчитан: {e}")
        return 2
    # Сторожа «жёсткие блоки изменились бы — код 2» здесь НЕТ намеренно:
    # `curated_of` копирует их из того же посева, с которым сравнение и
    # шло (`refreshed` → `seed_of(out)`), поэтому перечень расхождений
    # был пуст по построению, а код 2 — недостижим. Жёсткие блоки
    # стерегут не сравнением с самим собой: `base` сверяется с кодом
    # заморозки (`tests/test_template_catalog_tool_guard.py::
    # test_блок_base_равен_каталогу_кода_заморозки`), словарь CDPM — со
    # снимком части 7-Б, а отпечаток проекции — отказом `refreshed()`.
    targets = {SEED_REL: seed_text, DELTA_REL: delta_text}
    diff = []
    for rel, text in targets.items():
        p = out / rel
        if not p.is_file():
            diff.append(f"нет файла {p}")
        elif p.read_bytes().replace(b"\r\n", b"\n") != text.encode("utf-8"):
            diff.append(f"отстал от каталога {p}")
    delta = json.loads(delta_text)
    # Имя без свидетеля единицы дельта проставляет словами (`strict`
    # выключен, чтобы свежесть носителей судилась отдельно от паритета с
    # кодом) — но молчать об этом нельзя: строка называет имена и того,
    # кто их судит.
    без = sorted(n for n, v in (delta.get("params_to_families") or {}).items()
                 if (v.get("was") or {}).get("source") == NO_WITNESS)
    if без:
        print(f"внимание: у имён {без} источника единицы нет — их судят "
              f"--check --from-catalog и правила паритета L05/L05b")
    if check:
        # Слова ответа — те, что обещают руководство 13.4 и контракт:
        # «посев и дельта свежи» при нуле расхождений. Число расхождений
        # печатается рядом — по нему читают ответ и человек, и правила.
        print(f"свежесть носителей {out}: версия {delta['version']} | "
              f"отпечаток {delta['fingerprint']} | "
              f"расхождений {len(diff)}"
              + (" — посев и дельта свежи" if not diff else ""))
        for d in diff:
            print("  " + d)
        if diff:
            print("  выполните: python catalog/tools/build_from_code.py "
                  "--refresh --out " + str(out))
        return 1 if diff else 0
    for rel, text in targets.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    print(f"освежены носители {out}: версия {delta['version']} | "
          f"отпечаток {delta['fingerprint']} | файлов {len(targets)} | "
          f"было расхождений {len(diff)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(CATALOG_DIR))
    ap.add_argument("--version", default=None)
    ap.add_argument("--released-at", default=None)
    ap.add_argument("--released-by", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--check", action="store_true",
                    help="сравнить с файлами на диске, ничего не писать")
    ap.add_argument("--from-catalog", action="store_true",
                    help="курируемые поля брать из самой папки (закон L18)")
    ap.add_argument("--refresh", action="store_true",
                    help="переписать посев и дельту по каталогу папки")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out = Path(a.out)
    if a.refresh:
        if a.from_catalog:
            print("--refresh уже читает саму папку: --from-catalog с ним не "
                  "сочетается")
            return 2
        return _refresh(out, a.check)
    if a.from_catalog and not a.check:
        print("--from-catalog судит папку и ничего не пишет: он законен "
              "только с --check")
        return 2
    if not a.check:
        words = refuse_write(out)
        if words is not None:
            print(words)
            return GUARD_RC
    defaults = header_defaults(out)
    version = a.version if a.version is not None else defaults["version"]
    released_at = (a.released_at if a.released_at is not None
                   else defaults["released_at"])
    released_by = (a.released_by if a.released_by is not None
                   else defaults["released_by"])
    note = a.note if a.note is not None else defaults["note"]
    requests_doc, requests_words = read_requests(out)
    seed = None
    if a.from_catalog:
        # Закон L18: папка воспроизводится из СВОИХ курируемых полей ⊕
        # код. Жёсткие блоки — из посева инструмента: словарь CDPM и
        # слово владельца принадлежат не папке, а владельцу.
        from template_catalog import load as _load
        try:
            cat = _load(out)
        except Exception as e:                                # noqa: BLE001
            print(f"каталог {out} не прочитан: {e}")
            return 2
        seed = curated_of(cat, hard=seed_of(out), order=order_of(out, cat))
    try:
        files = build(version, released_at, released_by, note,
                      seed=seed, requests_doc=requests_doc)
    except BuildError as e:
        print(f"каталог не собран: {e}")
        return 2
    problems = validate_built(files, requests_doc)
    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]
    if errors:
        print(f"каталог не собран: {len(errors)} ошибок validate()")
        for p in errors:
            print("  " + str(p))
        return 2
    fp = json.loads(files["catalog.json"])["fingerprint"]
    lrr = json.loads(files["catalog.json"])["legacy_registry_rev"]
    tasks = sum(len(r["stand_tasks"])
                for name in ("families.json", "criteria.json")
                for r in json.loads(files[name])[name.split(".")[0]])
    n_requests = len(requests_doc.get("requests") or [])
    if a.check:
        diff = _compare_files(out, files, requests_words, fp)
        print(f"проверка {out}: отпечаток {fp} | старый registry_rev {lrr} | "
              f"стендовых задач {tasks} | заявок {n_requests} | "
              f"предупреждений validate() {len(warnings)} | "
              f"расхождений {len(diff)}")
        for d in diff:
            print("  " + d)
        return 1 if diff else 0
    for rel, text in files.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    # Чистая папка обязана стать полным каталогом (читателю нужны шесть
    # файлов): пустая очередь заводится ОДИН раз — на папке, где её нет.
    seeded = ""
    reqs = out / "requests.json"
    if not reqs.is_file():
        reqs.write_bytes(dump_json(EMPTY_REQUESTS).encode("utf-8"))
        seeded = " | заведена пустая очередь заявок"
    print(f"записан каталог {out}: версия {version} | отпечаток {fp} | "
          f"старый registry_rev {lrr} | стендовых задач {tasks} | "
          f"файлов {len(files)} | заявок {n_requests} | "
          f"предупреждений validate() {len(warnings)}{seeded}")
    return 0


def _delta_is_of(path: Path, fingerprint: str) -> bool:
    """Дельта на диске посчитана от каталога с этим отпечатком? Файла
    нет либо он не читается — сравнивать нечего (скажет L23)."""
    if not Path(path).is_file():
        return False
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("fingerprint") == fingerprint


if __name__ == "__main__":
    raise SystemExit(main())
