# -*- coding: utf-8 -*-
"""Мост CDPM → каталог шаблонов (шаг 7 переезда, стадии 1а–1б; проект
final-report/KATALOG-SHAG-7-CDPM-NOSITELI-PROEKT-CLD.md §0, §2).

КАТАЛОГ ОБЯЗАТЕЛЕН И ЕДИНСТВЕН. Словарь машинных параметров правил
(PARAMS), закрытые перечни (единицы, природа, отношения, пояса,
уточнители области, категории посадки), карта «семья → имена
параметров», ЗАПИСИ СЕМЕЙ (заголовок, вид, зона, природа, единица
нарушения, виды исполнителей) и лексикон семей читаются ТОЛЬКО из
каталога через ОДИН экземпляр на процесс (`template_catalog.current()`)
— тот же, что у производителя пакета: один каталог = один отпечаток у
посадок и манифеста. Со стадии 1б этих словарей в коде CDPM НЕТ ВОВСЕ
(машинный сторож — `tests/test_cdpm_catalog_source.py`): каталога нет,
он нечитаем или не проходит проверку — отказ словами
(`CatalogRequired`, код «catalog-unavailable»), как у COSMOS с шага 2.

Путь каталога — настройка продукта `CREW_TEMPLATE_CATALOG`; не задана
— папка `catalog/` рядом с пакетом читателя (та же раскладка у
установщика и дословно та же строка, что у двери COSMOS
`vmp/catalog_access.catalog_path()`: один ключ кэша — один экземпляр).

Отказ ловят РУБЕЖИ, а не внутренности (приложение схемы, п. 29):
`problem_or_none()` — одна строка проблемы на входе `validate_model` и
`vmp_form.validate_form`; продуктовые команды переводят
`CATALOG_ERRORS` в свой отказ словами; инструменты разработчика
поднимают `CatalogRequired` как есть.

Правила отбора (согласованы с COSMOS 07.09):
  * параметры — все имена каталога с семьёй или без неё, КРОМЕ имён с
    источником «словарь решателя» (они не словарь CDPM, §14 п.11);
  * порядок PARAMS и перечней — порядок ЧИТАТЕЛЯ (по имени): один
    источник — один порядок; прежний «порядок кода для известных имён»
    снят вместе с константами;
  * переменной режима среза базы шаблонов у CDPM больше нет: мост её не
    читает вовсе — ни уважает, ни отвергает (переходное правило §3
    проекта до части COSMOS 7-Б; имя переменной в `cdpm/**` запрещено
    гейтом источника).

Проверка каталога — ОДИН раз на экземпляр, той же формулой слов, что у
двери COSMOS (`vmp/catalog_access.current()`): ошибки `severity ==
"error"`, первые пять, «(и ещё N)». Переходная двойная проверка одного
экземпляра (мост и дверь) названа в проекте и снимается, когда
читатель положит память вердикта у себя (§11 п. 8).
"""
from __future__ import annotations

import copy
import os
import threading

SOLVER_ONLY_SOURCE = "словарь решателя"
ENV_CATALOG = "CREW_TEMPLATE_CATALOG"

#: Признак порядка словаря в паспорте прогона (шаг 7): с этого шага
#: листинг словаря в задании модели идёт порядком ЧИТАТЕЛЯ каталога, а
#: не порядком кода. По признаку повтор узнаёт журналы, записанные при
#: прежнем порядке: повтор ИЗ ЖУРНАЛА у них расходится на первом вызове
#: извлечения (листинг входит в текст запроса), доступен повтор заново.
DICTIONARY_ORDER = "catalog"

try:
    import template_catalog as tc
except ImportError:                                   # pragma: no cover
    tc = None


class CatalogRequired(RuntimeError):
    """Каталог шаблонов обязателен, а его нет: папки нет, она нечитаема,
    читатель не установлен либо каталог не проходит проверку.

    `code` — машинное слово для панели и ручек (то же, что у COSMOS);
    `words` — ПРИЧИНА без обращения продукта: рубежи ставят её в свой
    текст («каталог шаблонов недоступен: <причина>»), не повторяя
    «CDPM: каталог шаблонов обязателен» дважды."""

    code = "catalog-unavailable"

    def __init__(self, words: str, problems=None) -> None:
        super().__init__(f"CDPM: каталог шаблонов обязателен: {words}")
        self.words = words
        self.problems = list(problems or [])


#: Классы отказа каталога, которые рубежи переводят словами. Читатель
#: поднимает свои (`CatalogError` и наследники) на адресных обращениях —
#: они тоже отказ каталога, а не порок документа.
CATALOG_ERRORS: tuple = ((CatalogRequired, tc.CatalogError) if tc
                         else (CatalogRequired,))

#: Хвост одной строки проблемы на рубежах: без словаря не судится ни
#: один параметр — честнее сказать это, чем промолчать.
NOT_JUDGED = " — словарная дисциплина и контракт посадки не судятся"

LOCK = threading.RLock()
# "cat": проверенный экземпляр; "problems": итог validate() экземпляра;
# "bad": (экземпляр, исключение) — запомненный отказ проверки;
# "proj": (экземпляр, снимок проекций).
_STATE: dict = {}


def words_of(e) -> str:
    """Причина отказа словами: у `CatalogRequired` — без обращения
    продукта, у отказов читателя — его собственный текст с адресом."""
    return str(getattr(e, "words", None) or e)


def catalog_path() -> str | None:
    """Путь каталога из настройки продукта; None — умолчание читателя."""
    v = (os.environ.get(ENV_CATALOG) or "").strip()
    return v or None


def _where(path) -> str:
    if path:
        return f"{ENV_CATALOG}={path!r}"
    return f"каталог по умолчанию {tc.DEFAULT_CATALOG_DIR}"


def catalog():
    """ПРОВЕРЕННЫЙ экземпляр каталога (один на процесс, перечитывание
    читателем по mtime). Каталога нет, он нечитаем или не проходит
    проверку — `CatalogRequired` словами: запаса из кода нет."""
    if tc is None:                                    # pragma: no cover
        raise CatalogRequired(
            "читатель template_catalog не установлен рядом с пакетом "
            "cdpm — установка неполна (каталог приезжает клоном)")
    path = catalog_path()
    with LOCK:
        try:
            cat = tc.current(path)
        except tc.CatalogError as e:
            raise CatalogRequired(f"{_where(path)}: {e}") from None
        if cat is _STATE.get("cat"):
            return cat
        bad = _STATE.get("bad")
        if bad is not None and bad[0] is cat:
            raise bad[1]
        problems = cat.validate()
        errors = [p for p in problems if p.severity == "error"]
        if errors:
            head = "; ".join(str(p) for p in errors[:5])
            more = f" (и ещё {len(errors) - 5})" if len(errors) > 5 else ""
            exc = CatalogRequired(
                f"{_where(path)}: каталог {cat.path} не проходит проверку: "
                f"{head}{more}", problems)
            _STATE.clear()
            _STATE["bad"] = (cat, exc)
            raise exc
        _STATE.clear()
        _STATE["cat"] = cat
        _STATE["problems"] = list(problems)
        return cat


def criterion_direction(kind: str) -> str:
    """The shared catalogue rule; no CDPM fallback direction table."""
    from vmp.objective_projection import criterion_direction as direction_of
    from vmp.objective_projection import ObjectiveProjectionError
    try:
        return direction_of(catalog(), kind)
    except ObjectiveProjectionError as exc:
        raise CatalogRequired(str(exc)) from exc


def ensure_valid():
    """Проверенный экземпляр каталога — имя рубежа для читателей,
    которым нужен не словарь, а сам факт годности (проверка идёт один
    раз на экземпляр внутри `catalog()`)."""
    return catalog()


def catalog_available() -> bool:
    """Есть ли годный каталог. Слова отказа НЕ теряются: это признак
    для показа, а решение об отказе принимают рубежи."""
    try:
        catalog()
    except CATALOG_ERRORS:
        return False
    return True


def problem_or_none(tail: str = NOT_JUDGED) -> str | None:
    """ОДНА строка проблемы для рубежей (`validate_model`,
    `vmp_form.validate_form`) либо None. Гейт пакета обязан отдать
    список проблем, а не трассировку."""
    try:
        catalog()
    except CATALOG_ERRORS as e:
        return f"каталог шаблонов недоступен: {words_of(e)}{tail}"
    return None


def _projections(cat) -> dict:
    """Снимок проекций ЭТОГО экземпляра: строится один раз, ключ — сам
    экземпляр (тождество `is`), как у обёртки COSMOS. Дросселя опроса
    нет: `current()` стоит 0,18 мс, а дроссель дал бы окно, в котором
    мост отдаёт прежний экземпляр, а дверь уже новый."""
    with LOCK:
        got = _STATE.get("proj")
        if got is not None and got[0] is cat:
            return got[1]
        proj: dict = {}
        _STATE["proj"] = (cat, proj)
        return proj


def _dictionaries(cat) -> dict:
    proj = _projections(cat)
    got = proj.get("dictionaries")
    if got is None:
        got = cat.dictionaries() or {}
        proj["dictionaries"] = got
    return got


def stamp() -> dict:
    """Отметка каталога — версия и отпечаток ТОГО экземпляра, которым
    сейчас работает процесс: единственный источник ответа «каким
    каталогом это сделано» для посадок правил
    (`template_fit.registry_rev`), манифеста пакета (`manifest.catalog`)
    и паспорта прогона. Без каталога — отказ словами (не None и не
    выдумка); половинчатой отметки не бывает."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("stamp")
    if got is None:
        try:
            got = {"version": str(cat.version()),
                   "fingerprint": str(cat.fingerprint())}
        except Exception as e:                            # noqa: BLE001
            raise CatalogRequired(
                f"каталог {getattr(cat, 'path', '?')} не отдал версию или "
                f"отпечаток ({str(e)[:160]})") from None
        if not got["version"] or not got["fingerprint"]:
            raise CatalogRequired(
                f"каталог {getattr(cat, 'path', '?')} не отдал версию или "
                f"отпечаток — половинчатой отметки не бывает")
        proj["stamp"] = got
    return dict(got)


def stamp_or_error() -> dict:
    """Отметка каталога ДЛЯ ПАСПОРТА прогона: при годном каталоге —
    `{version, fingerprint, dictionary_order}`, при отказе —
    `{version: None, fingerprint: None, error: <слова>}`. Паспорт —
    чтение, он не падает и не выдумывает: беда называется словами.

    `dictionary_order` (шаг 7) отвечает на вопрос повтора «каким
    порядком словаря записан журнал»: паспорт прогона БЕЗ этого поля
    записан прежним порядком (порядок кода), и повтор из его журнала
    расходится на первом вызове извлечения — слова об этом даёт
    `replay.availability()` до нажатия кнопки."""
    try:
        got = stamp()
    except CATALOG_ERRORS as e:
        return {"version": None, "fingerprint": None, "error": words_of(e)}
    return {"version": got["version"], "fingerprint": got["fingerprint"],
            "dictionary_order": DICTIONARY_ORDER}


def warnings_count() -> int:
    """Число предупреждений проверки ТЕКУЩЕГО экземпляра каталога
    (ошибок быть не может: с ними `catalog()` отказывает). Каталог не
    прочитан — 0."""
    with LOCK:
        problems = _STATE.get("problems") or []
    return sum(1 for p in problems if getattr(p, "severity", "") != "error")


def probe() -> dict:
    """Проба готовности одним ответом — для ручки панели `GET
    /api/catalog`, чек-листа раската и проверки установки.

    Секретов не отдаёт: версия, отпечаток, путь, число предупреждений и
    слова. Признак назван двумя именами по одному значению: `ok` (проба
    площадки и установщика) и `available` (панель) — чтобы ни одному
    читателю не пришлось угадывать чужое слово."""
    try:
        cat = catalog()
    except CATALOG_ERRORS as e:
        words = words_of(e)
        return {"ok": False, "available": False, "version": None,
                "fingerprint": None, "path": None, "warnings": 0,
                "code": CatalogRequired.code, "error": words,
                "words": f"каталог шаблонов недоступен: {words}"}
    got = stamp()
    return {"ok": True, "available": True, "version": got["version"],
            "fingerprint": got["fingerprint"],
            "path": str(getattr(cat, "path", "") or "") or None,
            "warnings": warnings_count(), "code": None, "error": None,
            "words": (f"каталог шаблонов {got['version']} "
                      f"{got['fingerprint'][:12]}")}


#: Исполнитель, который делает критерий каталога МАШИННОЙ метрикой
#: постановки CDPM (закрытый словарь `metric` целевой, спецификация 1.1):
#: критерий со статусом «есть», у которого этот вид в `computable_by`.
METRIC_EXECUTOR_KIND = "kotlin_metric"
#: Задачи по зоне записи каталога — тот же закон, что у целевых.
METRIC_ZONE_TASKS = {"PAIRING": ("pairing",), "ROSTERING": ("rostering",),
                     "BOTH": ("pairing", "rostering")}


def metrics_of(cat, words: dict | None = None) -> dict:
    """ЧИСТАЯ проекция метрик по ЭКЗЕМПЛЯРУ каталога `cat` (любой: живой,
    временный каталог гейта, кандидат) — закон один: критерий «есть» с
    `kotlin_metric` в `computable_by`, задачи по зоне записи; `title`/`note`
    — слова CDPM по имени (`words`; по умолчанию слова модели CDPM), у
    метрики без слов CDPM — заголовок каталога и без заметки. Нужна
    свидетелю паритета COSMOS, который судит временный каталог, а не
    каталог по переменной среды (просьба COSMOS 09.09 08:4x). Порядок —
    читателя (по имени). Кэша нет — снимок держит `metrics()`."""
    if words is None:
        from cdpm import model as _model            # ленивый: model импортирует мост
        words = getattr(_model, "_METRIC_WORDS", {}) or {}
    got: dict = {}
    for cid, rec in cat.criteria().items():
        kinds = rec.get("computable_by") or ()
        if METRIC_EXECUTOR_KIND not in kinds:
            continue
        tasks = METRIC_ZONE_TASKS.get(str(rec.get("zone")))
        if not tasks:
            continue
        own = words.get(cid) or {}
        entry = {"tasks": tasks,
                 "title": str(own.get("title") or rec.get("title") or cid)}
        if own.get("note"):
            entry["note"] = str(own["note"])
        got[str(cid)] = entry
    return got


def metrics(words: dict | None = None) -> dict:
    """METRICS: имя метрики → {tasks, title, note} — ПРОЕКЦИЯ критериев
    каталога (просьба 2 извещения о паритете, 08.09): имена и задачи —
    от каталога (критерий «есть» с `kotlin_metric` в `computable_by`,
    задачи по зоне), `title`/`note` — слова CDPM из `words` по имени;
    у метрики без слов CDPM заголовок — заголовок каталога, заметки нет.
    Один объект снимка на экземпляр каталога; порядок — читателя (по
    имени). Закон — `metrics_of`."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("metrics")
    if got is None:
        got = metrics_of(cat, words)
        proj["metrics"] = got
    return got


def params() -> dict:
    """PARAMS: имя → единица. Все имена каталога (семей и `unbound`),
    КРОМЕ имён с источником «словарь решателя»; порядок читателя.
    Возвращается ОДИН объект снимка на экземпляр каталога — два
    обращения дают тот же словарь, пересборки на вызов нет."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("params")
    if got is None:
        table = cat.params()
        got = {str(n): str(spec.get("unit")) for n, spec in table.items()
               if isinstance(spec, dict) and spec.get("unit")
               and spec.get("source") != SOLVER_ONLY_SOURCE}
        proj["params"] = got
    return got


def vocab(key: str) -> tuple:
    """Закрытый перечень словаря каталога `key` кортежем, порядок
    читателя. Словарь-КАРТА (с 0.5.0 так записан `violation_units`:
    «код единицы → слово») отдаёт КЛЮЧИ в порядке записи: членство
    судится по коду, и контракт перечня от смены вида записи не
    меняется. Словаря нет — отказ словами: молчаливого перечня из кода
    больше не бывает."""
    cat = catalog()
    proj = _projections(cat)
    voc = proj.get("vocab")
    if voc is None:
        voc = {}
        proj["vocab"] = voc
    if key not in voc:
        vals = _dictionaries(cat).get(key)
        if isinstance(vals, dict):
            vals = list(vals)
        if not isinstance(vals, list) or not vals:
            raise CatalogRequired(
                f"в каталоге {cat.version()} нет словаря {key!r} "
                f"(путь {cat.path}) — перечень значений брать негде")
        voc[key] = tuple(str(v) for v in vals)
    return voc[key]


#: Словарь каталога, записанный КАРТОЙ «код единицы размера нарушения →
#: русское слово единицы» (каталог 0.5.0). Перечень старой формы (0.4.0)
#: словом единицы не располагает, и второй карты перевода у CDPM нет.
VIOLATION_UNITS_KEY = "violation_units"


def violation_unit_words() -> dict:
    """Слова единиц размера нарушения: код → русское слово, порядок
    записи каталога. Единственный источник перевода единицы на язык
    документа: рукописной карты в коде CDPM нет.

    Словарь старой формы (перечень без слов) — отказ словами: выдумать
    слово единицы неоткуда, а молча показать код значило бы подменить
    язык документа машинным именем."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("violation_unit_words")
    if got is None:
        vals = _dictionaries(cat).get(VIOLATION_UNITS_KEY)
        if not isinstance(vals, dict) or not vals:
            raise CatalogRequired(
                f"в каталоге {cat.version()} словарь "
                f"{VIOLATION_UNITS_KEY!r} записан не картой «код единицы "
                f"→ слово» (путь {cat.path}) — слова единицы брать негде")
        out = {}
        for code, word in vals.items():
            if not (isinstance(word, str) and word.strip()):
                raise CatalogRequired(
                    f"в каталоге {cat.version()} у единицы {code!r} нет "
                    f"слова (путь {cat.path}) — назвать единицу в "
                    f"документе нечем")
            out[str(code)] = word.strip()
        got = out
        proj["violation_unit_words"] = got
    return got


def families() -> dict:
    """Карта «семья → имена параметров» по записям каталога со статусом
    «есть». Без каталога — отказ словами."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("families")
    if got is None:
        out: dict = {}
        for fid, rec in cat.families().items():
            # Читатель отдаёт семью либо перечнем имён параметров, либо
            # записью с params[] — принимаются обе формы.
            items = rec.get("params") if isinstance(rec, dict) else rec
            names = []
            for p in (items or []):
                n = p.get("name") if isinstance(p, dict) else p
                if n:
                    names.append(str(n))
            out[str(fid)] = names
        proj["families"] = out
        got = out
    return got


def param_families() -> dict:
    """Карта «имя параметра → семья» по записям каталога: у имени из
    `unbound_params` семьи нет, и в карту оно не входит. Это ответ на
    вопрос листинга словаря «кто исполняет имя» — прежде его давала
    обёртка решателя (`PARAM_TO_FAMILY`), теперь только каталог."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("param_families")
    if got is None:
        got = {str(n): str(s["family"]) for n, s in cat.params().items()
               if isinstance(s, dict) and s.get("family")}
        proj["param_families"] = got
    return got


def family_records() -> dict:
    """Записи семей каталога со статусом «есть»: id → запись целиком
    (титул, вид, зона, природа, единица нарушения, виды исполнителей,
    шаблоны проверок, параметры, смысл). Снимок ЭКЗЕМПЛЯРА: словарь и
    записи в нём общие для всех читателей CDPM — на месте их не меняют.
    Прежде эти поля приходили проекциями обёртки решателя
    (`RULE_MECHANIC_TYPES`, `FAMILY_KIND_ZONE`, `FAMILY_VIOLATION_UNIT`,
    `families_meta()`), теперь — одной записью каталога."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("records")
    if got is None:
        got = {str(r.get("id")): r for r in cat.families_full()
               if isinstance(r, dict) and r.get("id")}
        proj["records"] = got
    return got


def family_titles() -> dict:
    """Заголовки семей: id → титул записи ОДНОЙ строкой (пробелы
    схлопнуты) — титул идёт в задание модели листингом семей, а
    перенос строки в задании читается как конец пункта."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("titles")
    if got is None:
        got = {fid: " ".join(str(rec.get("title") or "").split())
               for fid, rec in family_records().items()}
        proj["titles"] = got
    return got


def family_traits(family: str | None = None) -> dict:
    """Свойства семей по записям каталога: id → {kind, zone, nature,
    nature_confirmed_by, violation_unit, violation_size, reads_history,
    class}. По имени семьи — свойства только её (у неизвестной семьи —
    пусто: свойства читают там, где семью уже назвали, и молчание
    честнее выдумки).

    С каталога 0.5.0 признаков три новых: `reads_history` — null либо
    объект {need, kind} («что семья читает из истории до горизонта» и
    вид чтения словаря `history_read_kinds`); `violation_size` — чем
    меряется штраф (count | magnitude, задан ровно у мягких);
    `nature_confirmed_by` — свидетель природы семьи. Чтение истории
    отдаётся КОПИЕЙ: снимок признаков общий для всех читателей CDPM, и
    вложенный объект на месте не правят."""
    cat = catalog()
    proj = _projections(cat)
    got = proj.get("traits")
    if got is None:
        got = {fid: {"kind": rec.get("kind"), "zone": rec.get("zone"),
                     "nature": rec.get("nature"),
                     "nature_confirmed_by": rec.get("nature_confirmed_by"),
                     "violation_unit": rec.get("violation_unit"),
                     "violation_size": rec.get("violation_size"),
                     "reads_history": copy.deepcopy(rec.get("reads_history")),
                     "class": rec.get("class")}
               for fid, rec in family_records().items()}
        proj["traits"] = got
    if family is None:
        return got
    return copy.deepcopy(got.get(str(family)) or {})


#: Вид исполнителя, который НЕ делает семью исполняемой: шаблон
#: проверки судит входную книгу данных, а ограничением решения не
#: является (закон 1 §11 п. 5 проекта шага 7).
CHECK_TEMPLATE_KIND = "check_template"


def executor_kinds(family: str) -> tuple:
    """Виды исполнителей, ОБЪЯВЛЕННЫЕ записью семьи (порядок записи);
    у неизвестной семьи — пусто."""
    rec = family_records().get(str(family)) or {}
    return tuple(str(k) for k in (rec.get("executor_kinds") or ()))


def family_has_executor(family: str) -> bool:
    """Закон 1 «у семьи есть исполнитель» (§11 п. 5 проекта шага 7): у
    записи каталога объявлен хотя бы один вид исполнителя, кроме шаблона
    проверки данных. Такая семья ограничивает РЕШЕНИЕ, и её соблюдение
    судит сертификат решения, а не входная книга: линия данных о ней не
    спрашивает авиакомпанию (шаблон проверки данных исключён именно
    потому, что его исполняет сама линия данных CDPM).

    Закон 1 — про ОБЪЯВЛЕНИЕ (кто СУДИТ правило), закон 2
    (`family_support`) — про ПОДТВЕРЖДЕНИЕ (исполним ли вид на деле, по
    привязкам обоих продуктов). Они отвечают на разные вопросы и не
    подменяют друг друга: объявленная, но не подтверждённая семья
    остановлена K06-барьером со своим следом, а не превращается в
    вопрос линии данных (поправка 12.11, находка F2 неавторского
    ревью)."""
    return any(k != CHECK_TEMPLATE_KIND for k in executor_kinds(family))


# --------------------------------------------------------------------
# Закон 2 — подтверждённая поддержка точной записи (K06-барьер,
# контракт П0 §3.5, §9 и поправка 12.11 от 12.09.2026)
# --------------------------------------------------------------------

#: Состояние клетки привязок, которое единственно даёт поддержку.
SUPPORT_STATE = "есть"
#: Продукты, чьи файлы привязок судят поддержку (и порядок слов в
#: свидетельстве). Вид ИСПОЛНИМ, если «есть» стоит хотя бы у одного из
#: них: часть видов записи каталога исполняет код CDPM (шаблоны проверок
#: линии данных), и по одному файлу COSMOS семь семей каталога 0.5.0
#: выглядели бы без исполнителя — четыре из них входят во все три набора
#: круга (разведка COSMOS `k06-barrier-cosmos-recon.md`, §12.11 п. 1).
SUPPORT_PRODUCTS = ("COSMOS", "CDPM")
#: Где лежит свидетельство поддержки продукта — клетки привязок
#: продукта к записям каталога: переменная (та же, что у двери COSMOS
#: `vmp/catalog_access.BINDINGS_ENV` и у раннера) либо файл дерева
#: рядом с пакетом. Файл — ДАННЫЕ продукта (как каталог): CDPM его
#: читает читателем каталога, `vmp/**` не импортирует.
SUPPORT_ENV = {"COSMOS": "CREW_COSMOS_BINDINGS", "CDPM": "CDPM_BINDINGS_FILE"}
_SUPPORT_FILES = {"COSMOS": ("vmp", "bindings.json"),
                  "CDPM": ("cdpm", "bindings.json")}
#: Правила привязок, которые судит только гейт продукта, а не читатель
#: свидетельства (полнота по зоне и стендовые задачи) — как у двери.
_SUPPORT_GATE_ONLY = ("B04", "B05")
#: Слово о продукте, у которого клетки этого вида нет вовсе (в отличие
#: от «нет» — явного отказа продукта исполнять вид).
SUPPORT_NO_CELL = "—"
#: Раздел каталога, по записям которого судится поддержка семьи.
SUPPORT_SECTION = "families"


def support_path(product: str = "COSMOS"):
    """Путь файла свидетельства поддержки продукта (None — продукт
    неизвестен). Переменная площадки старше файла дерева."""
    from pathlib import Path
    parts = _SUPPORT_FILES.get(str(product))
    if parts is None:
        return None
    env = (os.environ.get(SUPPORT_ENV.get(str(product), "")) or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def support_digest(path) -> str | None:
    """SHA-256 СОДЕРЖИМОГО файла привязок (None — файла нет или он не
    читается). Ключ кэша свидетельства — содержимое, а не отметка
    (mtime, размер): копирование с сохранением отметки (`robocopy
    /COPY:DAT`, `cp -p`), откат файла и двухсекундная точность FAT
    возвращали ПРЕЖНИЙ вердикт по новым байтам — свидетельство судило
    бы не те клетки, которыми выпущен пакет (находка F4 неавторского
    ревью 12.09.2026). Тот же SHA-256 идёт строкой в паспорт прогона
    (§12.11 п. 5), так что «чем судили» и «что в кэше» — одно число."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _support_file(product: str, cat, own: dict, path, digest) -> dict:
    """Один файл привязок: прочитанный и проверенный читателем каталога
    документ при каталоге ТОГО ЖЕ отпечатка либо беда словами
    (`ok: False`, `doc: None`). Несвежий, отсутствующий, чужой и
    нечитаемый файл клеток НЕ ДАЁТ (fail-closed), но исключением не
    становится: второй продукт судится дальше (§12.11 п. 1).

    СВЕЖЕСТЬ судит ЗОВ единственной функции закона —
    `template_catalog.bindings.matches_catalog`: числовая ТРОЙКА версии
    и полный вычисленный канонический отпечаток. Своей копии закона
    здесь нет (адресное поручение GPT161/A, 13.09.2026): прежнее точное
    сравнение СТРОКИ версии было ТРЕТЬЕЙ копией и было СТРОЖЕ
    действующей — файл, выпущенный из того же тела каталога под снятым
    черновым суффиксом, объявлялся «о другом каталоге», хотя тройка и
    отпечаток сходились (версия в канон каталога не входит, выпуск
    меняет только метку). Экран «Исполнители по продуктам»
    (`catalog_service.executors.freshness`) и ячейка свидетельства
    поддержки обязаны отвечать ОДНО о том, для какого каталога написан
    файл продукта. Слова беды опираются на `own` — отметку ТОГО
    экземпляра каталога, которым работает процесс."""
    got: dict = {"product": str(product),
                 "path": str(path) if path is not None else None,
                 "sha256": digest, "ok": False, "doc": None,
                 "catalog_version": None, "catalog_fingerprint": None,
                 "product_version": None, "generated_from": None,
                 "words": None}
    if path is None:
        got["words"] = f"продукт {product!r} неизвестен — свидетельства нет"
        return got
    if digest is None:
        got["words"] = (f"свидетельства поддержки {product} нет: файла "
                        f"привязок {path} нет")
        return got
    try:
        from template_catalog.bindings import (check_bindings, load_bindings,
                                               matches_catalog)
        doc = load_bindings(path)
    except Exception as e:                                # noqa: BLE001
        got["words"] = (f"свидетельство поддержки {product} не читается "
                        f"({path}): {str(e)[:200]}")
        return got
    got["catalog_version"] = doc.get("catalog_version")
    got["catalog_fingerprint"] = doc.get("catalog_fingerprint")
    got["product_version"] = doc.get("product_version")
    got["generated_from"] = doc.get("generated_from")
    if str(doc.get("product") or "") != str(product):
        got["words"] = (f"файл {path} — привязки продукта "
                        f"{doc.get('product')!r}, а нужен {product}")
        return got
    if not matches_catalog(cat, doc):
        got["words"] = (
            f"свидетельство поддержки {product} о другом каталоге: "
            f"привязки к {doc.get('catalog_version')} "
            f"{str(doc.get('catalog_fingerprint') or '')[:12]}, "
            f"действует {own['version']} {own['fingerprint'][:12]}")
        return got
    try:
        errors = [f"{p.where}: {p.message}"
                  for p in check_bindings(cat, doc)
                  if p.severity == "error" and p.code not in _SUPPORT_GATE_ONLY]
    except Exception as e:                                # noqa: BLE001
        errors = [f"проверка привязок не выполнена: {str(e)[:160]}"]
    if errors:
        got["words"] = (f"свидетельство поддержки {product} не по правилам "
                        f"каталога: " + "; ".join(errors[:3])
                        + (f" (и ещё {len(errors) - 3})" if len(errors) > 3
                           else ""))
        return got
    got["ok"] = True
    got["doc"] = doc
    return got


def support_table() -> dict:
    """Свидетельство поддержки ОБОИХ продуктов одним снимком:
    `{files: {продукт: {path, sha256, ok, catalog_version,
    catalog_fingerprint, product_version, generated_from, words}},
    states: {раздел: {id: {вид: {продукт: состояние}}}}, words: [беды
    файлов]}`.

    Таблица состояний — нейтральная функция читателя каталога
    `template_catalog.bindings.best_states(catalog, *файлы)`: ранг
    каталога «есть > заявлен > нет» решает за клетку одной идентичности,
    и порядок строк и файлов вердикта не меняет (находка F1: словарь
    «id → строка» терял первую из двух строк одной записи — вердикт
    зависел от порядка). Тот же закон живёт в двери COSMOS
    (`vmp/catalog_access._merge_states`) и в службе каталога — третьей
    копии не бывает.

    Кэш — по экземпляру каталога, его версии с отпечатком и SHA-256
    СОДЕРЖИМОГО обоих файлов: правка любого из них читается без
    перезапуска, а на каждую готовность файлы не перечитываются."""
    cat = catalog()
    own = stamp()
    paths = {p: support_path(p) for p in SUPPORT_PRODUCTS}
    digests = {p: (support_digest(paths[p]) if paths[p] is not None else None)
               for p in SUPPORT_PRODUCTS}
    key = (own["version"], own["fingerprint"],
           tuple((p, str(paths[p]), digests[p]) for p in SUPPORT_PRODUCTS))
    with LOCK:
        hit = _STATE.get("support")
        if hit is not None and hit[0] is cat and hit[1] == key:
            return hit[2]
    files = {p: _support_file(p, cat, own, paths[p], digests[p])
             for p in SUPPORT_PRODUCTS}
    docs = [files[p]["doc"] for p in SUPPORT_PRODUCTS
            if files[p].get("doc") is not None]
    try:
        from template_catalog.bindings import best_states
        states = best_states(cat, *docs)
    except Exception as e:                                # noqa: BLE001
        states = {}
        for p in SUPPORT_PRODUCTS:
            files[p]["ok"] = False
            files[p]["doc"] = None
        got = {"files": files, "states": states,
               "words": [f"таблица привязок не собралась: {str(e)[:200]}"]}
    else:
        got = {"files": files, "states": states,
               "words": [files[p]["words"] for p in SUPPORT_PRODUCTS
                         if files[p].get("words")]}
    with LOCK:
        _STATE["support"] = (cat, key, got)
    return got


def kinds_without_executor(section: str, record: dict, states: dict,
                           declared) -> list:
    """Объявленные записью виды, у которых исполнителя «есть» нет ни у
    одного продукта. Порядок — как объявлен записью.

    ШОВ (§12.11 п. 2): COSMOS поднимает свою
    `vmp/catalog_requests._kinds_without_executor` в нейтральный
    `template_catalog.bindings` рядом с `best_states` — тогда тело этой
    функции становится ОДНОЙ строкой `return list(
    tcb.kinds_without_executor(section, record, states, declared))`, и
    закон остаётся один на CDPM, кнопку COSMOS и службу каталога.
    Сигнатура здесь СОВПАДАЕТ с сигнатурой COSMOS ради этой одной
    строки."""
    cells = ((states or {}).get(str(section)) or {}).get(
        str(record.get("id"))) or {}
    out = []
    for kind in declared:
        by_product = cells.get(str(kind)) or {}
        if not any(str(s) == SUPPORT_STATE for s in by_product.values()):
            out.append(str(kind))
    return out


def support_states_words(kind: str, by_product: dict) -> str:
    """Слово о виде с состоянием у КАЖДОГО продукта:
    `kotlin_knob (COSMOS: нет; CDPM: —)`. Прочерк — клетки у продукта
    нет вовсе (в отличие от «нет» — явного отказа исполнять вид)."""
    said = "; ".join(
        f"{p}: {(by_product or {}).get(p) or SUPPORT_NO_CELL}"
        for p in SUPPORT_PRODUCTS)
    return f"{kind} ({said})"


#: Переходная строгость барьера словами — её называет каждый отказ:
#: каталог 0.5.0 не делит виды исполнителя по режимам задачи, поэтому
#: требуются ВСЕ объявленные записью виды; деление придёт со схемой
#: `support/1` (волна 18).
SUPPORT_TRANSITIONAL = ("переходно требуются все объявленные виды записи "
                        "(support/1 — волна 18)")


def family_support(family: str, product: str = "COSMOS",
                   mode: str | None = None) -> dict:
    """Закон 2 «у семьи есть исполнитель» (K06-барьер, контракт П0 §3.5,
    §9 и поправка 12.11 от 12.09.2026): семья ПОДДЕРЖАНА, если у её
    ТОЧНОЙ записи каталога (раздел `families`, id) КАЖДЫЙ объявленный
    записью вид исполнителя имеет состояние «есть» хотя бы у одного
    продукта по ОБЪЕДИНЕНИЮ двух файлов привязок — `vmp/bindings.json`
    (COSMOS) и `cdpm/bindings.json` (CDPM) — через нейтральную функцию
    `template_catalog.bindings.best_states` (ранг каталога «есть >
    заявлен > нет» при конфликте клеток одной идентичности; читатель
    B01–B03 отвергает файл с подделанным «есть» без lang/file/symbol).

    Исключений ПО ИМЕНИ ВИДА нет: шаблон проверки данных — такой же
    объявленный вид, и исполняет его код CDPM своим файлом привязок
    (по одному файлу COSMOS семь семей каталога 0.5.0 выглядели бы без
    исполнителя — ложный отказ всем трём наборам круга).

    Свежесть КАЖДОГО файла — числовая ТРОЙКА `catalog_version` и полный
    `catalog_fingerprint` действующего каталога одной функцией закона
    `template_catalog.bindings.matches_catalog` (одна смена допустимого
    суффикса версии свежести не лишает: версия в канон каталога не
    входит); несвежий, отсутствующий, чужой или нечитаемый файл клеток
    не даёт (fail-closed, беда словами в свидетельстве), но исключением
    не становится. «Собственные ячейки» — клетки ЭТОЙ записи: вид,
    подтверждённый у ДРУГОЙ семьи, этой не помогает.
    Заявка, черновик записи, «заявлен», null, слово вне
    словаря — не поддержка; семьи нет среди записей каталога со
    статусом «есть» — тоже.

    `mode` — режим задачи (optimization / feasibility-soft /
    feasibility-hard); пока каталог не делит виды исполнителей по
    режимам, требуются ВСЕ объявленные виды записи, а режим
    записывается в свидетельство и в слова. Ручка службы каталога
    `GET /api/records/{section}/{id}/support` (`support/1`) — носитель
    той же правды на стороне COSMOS; CDPM читает файлы при каталоге.
    `product` — продукт, чей выпуск судится (идёт в свидетельство и в
    слова); закон объединения от него не зависит.

    Возвращает `{supported: bool, evidence: {family, record, product,
    products, mode, files: {продукт: {path, sha256, ok, catalog_version,
    catalog_fingerprint, product_version, generated_from, words}},
    declared, needed, kinds: {вид: {продукт: состояние}}, missing,
    missing_words, stand: {passed, failed, skipped}, words}}`;
    `words` — причина отказа (пусто при поддержке)."""
    from unicode_data import confusable_hint
    fam = str(family)
    table = support_table()
    evidence: dict = {
        "family": fam, "record": f"{SUPPORT_SECTION}/{fam}",
        "product": str(product), "products": list(SUPPORT_PRODUCTS),
        "mode": mode,
        "files": {p: {k: v for k, v in table["files"][p].items()
                      if k != "doc"} for p in SUPPORT_PRODUCTS},
        "declared": [], "needed": [], "kinds": {}, "missing": [],
        "missing_words": [],
        "stand": {"passed": 0, "failed": 0, "skipped": 0},
        "words": ""}
    # Беды файлов называются ВСЕГДА (и при поддержке тоже — читающий
    # обязан знать, что один из носителей не учтён).
    trouble = "; ".join(table.get("words") or ())
    rec = family_records().get(fam)
    if rec is None:
        evidence["words"] = (f"семьи {fam} нет среди записей каталога со "
                             f"статусом «есть» — черновик или заявка "
                             f"исполнимости не дают"
                             + confusable_hint(fam, family_records()))
        return {"supported": False, "evidence": evidence}
    declared = list(executor_kinds(fam))
    evidence["declared"] = list(declared)
    evidence["needed"] = list(declared)          # переходно: все виды
    cells = ((table["states"].get(SUPPORT_SECTION) or {}).get(fam)) or {}
    evidence["kinds"] = {k: dict(cells.get(k) or {}) for k in declared}
    for p in SUPPORT_PRODUCTS:
        doc = table["files"][p].get("doc")
        for row in (doc or {}).get("bindings") or ():
            if not isinstance(row, dict) or row.get("section") \
                    != SUPPORT_SECTION or str(row.get("id")) != fam:
                continue
            for sv in row.get("stand_verified") or ():
                res = str((sv or {}).get("result")) if isinstance(sv, dict) \
                    else ""
                if res in evidence["stand"]:
                    evidence["stand"][res] += 1
    if not declared:
        evidence["words"] = (f"запись {fam} не объявляет видов исполнителя"
                             + (f"; {trouble}" if trouble else ""))
        return {"supported": False, "evidence": evidence}
    missing = kinds_without_executor(SUPPORT_SECTION, rec, table["states"],
                                     declared)
    evidence["missing"] = missing
    evidence["missing_words"] = [support_states_words(k, cells.get(k))
                                 for k in missing]
    if missing:
        evidence["words"] = (
            "виды без исполнителя «есть» ни у одного продукта: "
            + ", ".join(evidence["missing_words"])
            + (f"; {trouble}" if trouble else ""))
        return {"supported": False, "evidence": evidence}
    evidence["words"] = ""
    return {"supported": True, "evidence": evidence}


def conditions(family: str | None = None) -> dict:
    """Условия исключения семей каталога (записи `conditions[]` семьи):
    имя → {family, unit, relation, note}; по семье — только её условия.
    В PARAMS условия не входят (ответ CDPM по схеме каталога, §7 п.1)."""
    cat = catalog()
    proj = _projections(cat)
    all_conds = proj.get("conditions")
    if all_conds is None:
        all_conds = {}
        for name, spec in (cat.conditions() or {}).items():
            if not isinstance(spec, dict):
                continue
            all_conds[str(name)] = {"family": spec.get("family"),
                                    "unit": spec.get("unit"),
                                    "relation": spec.get("relation"),
                                    "note": spec.get("note")}
        proj["conditions"] = all_conds
    if family is None:
        return dict(all_conds)
    return {n: dict(s) for n, s in all_conds.items()
            if s.get("family") == family}


def _lexicon(cat) -> dict:
    proj = _projections(cat)
    got = proj.get("lexicon")
    if got is None:
        got = cat.lexicon() or {}
        proj["lexicon"] = got
    return got


def subject_words(family: str) -> tuple:
    """Слова-носители предмета семьи кортежем (у семьи без слов и у
    неизвестной — пустой: лексический контракт семью не судит)."""
    words = (_lexicon(catalog()).get("subject_words") or {})
    return tuple(str(w).lower() for w in (words.get(str(family)) or ()))


def subjects(family: str) -> dict[str, tuple[str, ...]]:
    """Предметы ПАРАМЕТРОВ из общего читателя, не слова всей семьи.

    Старый читатель не заменяется локальной догадкой по name/note.
    Правило данных и отказ при их отсутствии принадлежат каталогу.
    """
    reader = getattr(catalog(), "subjects", None)
    if not callable(reader):
        raise CatalogRequired(
            f"семья {family!r}: читатель каталога не предоставляет "
            "словарь предметов параметров (subjects)")
    try:
        return reader(family)
    except tc.CatalogError as exc:
        raise CatalogRequired(str(exc)) from exc


def complements() -> dict:
    """Дополняющие пары семей: семья → перечень дополняющих."""
    return dict(_lexicon(catalog()).get("complements") or {})


def lexicon_order() -> tuple:
    """Порядок перебора семей лексиконом (словарь `lexicon_order`)."""
    return tuple(str(f) for f in (_lexicon(catalog()).get("order") or ()))


def forget() -> None:
    """Сброс кэша каталога, снимка проекций и вердикта (тесты, смена
    настройки контура)."""
    with LOCK:
        _STATE.clear()
    if tc is not None:
        try:
            tc.forget_current()
        except Exception:                                 # noqa: BLE001
            pass


# --------------------------------------------------------------------
# Команда-проба: `python -m cdpm.catalog_bridge`
# --------------------------------------------------------------------

def main(argv=None) -> int:
    """Проба каталога одной строкой — для проверки установки
    (`install_cosmos.py`), чек-листа раската и человека на площадке.

    Код 0 — каталог читается и проходит проверку; первое слово строки
    машинное и ASCII (`CATALOG OK`), дальше версия, отпечаток, число
    предупреждений и путь: раскат сверяет площадки между собой глазами
    и машиной. Код 2 — отказ СЛОВАМИ (`CATALOG UNAVAILABLE: …`), без
    трассировки: беда установки, а не порок документа.

    `vmp/**` эта команда не импортирует — CDPM от продукта COSMOS не
    зависит; отметку COSMOS установщик берёт своей второй командой и
    сверяет отпечатки."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:                       # русские слова на площадке с cp1251
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):                 # pragma: no cover
            pass
    got = probe()
    if not got["ok"]:
        print(f"CATALOG UNAVAILABLE: {got['error']}", flush=True)
        return 2
    print(f"CATALOG OK {got['version']} {got['fingerprint']} "
          f"warnings={got['warnings']} {got['path']}", flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    import sys as _sys

    _sys.exit(main(_sys.argv[1:]))
