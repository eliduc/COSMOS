# -*- coding: utf-8 -*-
"""Дверь COSMOS к каталогу шаблонов (шаг 2 переезда, 07.09.2026).

Одно место, где решается «какой каталог»: путь — настройка продукта
CREW_TEMPLATE_CATALOG (дословно та же формула, что у моста CDPM
cdpm/catalog_bridge.catalog_path(), — один ключ кэша читателя, один
экземпляр Catalog на процесс у обоих продуктов); не задана — папка
catalog/ рядом с пакетом читателя. Новый экземпляр проверяется один раз
(validate(): бывшие assert-ы импорта реестра — X03/D01/H02 и прочие);
негодный экземпляр — запомненный отказ без повторной проверки; кривой,
отсутствующий каталог или отсутствующий читатель — отказ словами
(CATALOG_ERRORS) на первой операции. Запаса из кода у COSMOS нет.

Порядок семей живёт в переходном носителе catalog/transition/
families_order.json, исполнители — в привязках продукта vmp/bindings.json
(шаг 4); читатель ни того, ни другого не видит (отметка current() — только
JSON верхнего уровня папки каталога), поэтому у обоих файлов здесь своя
отметка. Единый файл выгрузки на шаге 2 отвергается (порядка семей у него
нет); мост CDPM тот же файл принимает — асимметрия названа в тексте отказа.

Дверью пользуются обёртка vmp/template_registry.py, vmp/criteria.py,
vmp/run/roster_certify.py (дополняющие семьи), сторож дрейфа реестра
исполнителей (stamp) и границы перевода отказов. При импорте файлы не
читаются, переменные среды не трогаются. Имя модуля переживает шаг 7.

Шаг 3 (07.09.2026): здесь же сторож дрейфа КАТАЛОГА при нажатии —
product_stamp() («каким каталогом идёт продукт») и manifest_check()
(ключ пакета manifest.catalog против него, четыре исхода CHECK_*).
Слова судит читатель, решение — нажатие. Шаг 7, часть 7-Б
(08.09.2026): режим СРЕЗА базы шаблонов (CREW_VMP_TEMPLATE_BASE) снят —
у продукта один источник, каталог; дверь переменную не читает.

Шаг 4 (08.09.2026): привязки исполнителей — bindings() (документ,
проверенный читателем), bindings_executors() (строки «есть» для обёртки
и экрана), bindings_states() («запись × вид × продукт» для экрана),
bindings_requests() (заявка, из-за которой клетка «заявлен», — для
ссылки экрана на её карточку) и
bindings_summary() (сводка с отпечатком канона — её же сверяет сторож
дрейфа реестра). Что судится ЗДЕСЬ: форма файла, версия и отпечаток
каталога (B01), существование записей (B02), поля строк (B03). Что НЕ
судится здесь: полнота видов (B04) и стендовые подтверждения (B05) —
это гейт продукта python -m vmp.bindings_gate: дверь открывают на каждой
операции, а гейт зовут, когда судят. Привязки CDPM (cdpm/bindings.json)
дверь читает ТОЛЬКО чтобы сказать словами «объявлены | не объявлены |
не читаются» и показать состояния чужих клеток на экране: чужой файл не
может остановить COSMOS — нечитаемый документ снимается с показа, а
причина уходит в сводку третьим словом, и слово «объявлены» говорится
лишь о ПРОЧИТАННОМ и прошедшем читателя документе.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CATALOG_ENV = "CREW_TEMPLATE_CATALOG"
TRANSITION_DIR = "transition"
#: Опись исполнителей шага 1. Дверью БОЛЬШЕ НЕ ЧИТАЕТСЯ (с шага 4
#: исполнителей называют привязки), но файл остаётся на диске: он —
#: ЛЕВАЯ СТОРОНА гейта «ничего не потеряно» (tests/vmp/test_bindings.py,
#: п. 13) и свидетель L13. Имя здесь — адрес того файла для помощника
#: копий каталога и гейтов.
EXECUTORS_FILE = "executors_legacy.json"
ORDER_FILE = "families_order.json"

#: Файл привязок продукта (шаг 4). Подмена в тестах — переменной
#: BINDINGS_ENV: копия каталога кладёт рядом свой файл, и дверь обязана
#: читать именно его, а не файл дерева.
BINDINGS_FILE = REPO / "vmp" / "bindings.json"
BINDINGS_ENV = "CREW_COSMOS_BINDINGS"
#: Привязки второго продукта — только для слов «объявлены | не
#: объявлены» и показа чужих состояний на экране. Полноту по ним судит
#: гейт, не дверь.
CDPM_BINDINGS_FILE = REPO / "cdpm" / "bindings.json"
#: Та же настройка площадки, что у cdpm.catalog_bridge и cdpm.run_record.
CDPM_BINDINGS_ENV = "CDPM_BINDINGS_FILE"

#: Состояния клетки «запись × вид» по силе: лучшее из строк одного
#: продукта — то, что показывает экран (у читателя своя таблица для
#: B04, executor_states() не меняется).
STATE_RANK = {"есть": 0, "заявлен": 1, "нет": 2}
#: Состояние сводки привязок.
BIND_MATCH = "match"              # отпечаток каталога сошёлся
BIND_STALE = "stale-draft"        # черновик: отпечаток привязок отстал
BIND_MISSING = "missing"          # файла нет

#: Слова о привязках ЧУЖОГО продукта — закрытый перечень из трёх, а не
#: из двух: файл может лежать и не читаться, и тогда «объявлены» было бы
#: неправдой, а отказ COSMOS из-за правки в чужой зоне — несоразмерен.
CDPM_YES = "объявлены"
CDPM_NO = "не объявлены"
CDPM_BAD = "не читаются"

try:
    import template_catalog as tc
except ImportError:                                   # pragma: no cover
    tc = None


class CatalogUnavailable(RuntimeError):
    """Читатель template_catalog не установлен рядом с пакетом vmp —
    установка неполна."""


#: Классы отказа каталога, которые границы переводят словами.
CATALOG_ERRORS: tuple = ((tc.CatalogError, CatalogUnavailable) if tc
                         else (CatalogUnavailable,))
LOCK = threading.RLock()
# "cat": проверенный экземпляр; "problems": итог validate() экземпляра;
# "bad": (экземпляр, исключение) — запомненный отказ;
# "transition": (экземпляр, отметка, executors, order) — опись шага 1;
# "order": (экземпляр, отметка, порядок семей);
# "bindings": (экземпляр, отметка, документ, состояние).
_STATE: dict = {}


def catalog_path() -> str | None:
    """Путь каталога из настройки продукта; None — умолчание читателя.
    ДОСЛОВНО как cdpm/catalog_bridge.catalog_path(): одинаковая строка —
    один ключ кэша current() — один экземпляр на процесс."""
    return (os.environ.get(CATALOG_ENV) or "").strip() or None


def _where(path) -> str:
    if path:
        return f"{CATALOG_ENV}={path!r}"
    return f"каталог по умолчанию {tc.DEFAULT_CATALOG_DIR}"


def _rewrap(e, prefix: str):
    """Тот же класс отказа с адресом двери впереди (CatalogInvalid несёт
    перечень проблем — сохраняется)."""
    if tc is not None and isinstance(e, tc.CatalogInvalid):
        return tc.CatalogInvalid(f"{prefix}: {e}", e.problems)
    try:
        return type(e)(f"{prefix}: {e}")
    except TypeError:                                 # pragma: no cover
        return tc.CatalogError(f"{prefix}: {e}")


def current():
    """Текущий экземпляр каталога — один на процесс, тот же, что у моста
    CDPM (tc.current(catalog_path()) без strict: иной ключ дал бы второй
    экземпляр). Папка обязательна (шаг 2: переходные носители до шага 4).
    Новый экземпляр проверяется один раз; невалидный — запомненный отказ
    без повторной проверки; смена файлов на диске — новый экземпляр у
    читателя и новая проверка здесь."""
    if tc is None:
        raise CatalogUnavailable(
            "каталог шаблонов: читатель template_catalog не установлен "
            "рядом с пакетом vmp — установка неполна, операция не "
            "начинается")
    path = catalog_path()
    with LOCK:
        try:
            cat = tc.current(path)
        except tc.CatalogError as e:
            raise _rewrap(e, f"каталог шаблонов недоступен ({_where(path)})") \
                from None
        if cat is _STATE.get("cat"):
            return cat
        bad = _STATE.get("bad")
        if bad is not None and bad[0] is cat:
            raise bad[1]
        if getattr(cat, "single_file", False):
            exc = tc.CatalogFormatError(
                f"{cat.path}: единый файл выгрузки — COSMOS требует папку "
                f"каталога: порядок семей лежит в "
                f"{TRANSITION_DIR}/{ORDER_FILE}, исполнители — в "
                f"vmp/bindings.json, и единый файл ни того, ни другого не "
                f"несёт; мост CDPM тот же файл принимает")
            _STATE.clear()
            _STATE["bad"] = (cat, exc)
            raise exc
        problems = cat.validate()
        errors = [p for p in problems if p.severity == "error"]
        if errors:
            head = "; ".join(str(p) for p in errors[:5])
            more = (f" (и ещё {len(errors) - 5})" if len(errors) > 5
                    else "")
            exc = tc.CatalogInvalid(
                f"{_where(path)}: каталог {cat.path} не проходит проверку: "
                f"{head}{more}", problems)
            _STATE.clear()
            _STATE["bad"] = (cat, exc)
            raise exc
        _STATE.clear()
        _STATE["cat"] = cat
        _STATE["problems"] = list(problems)
        return cat


def problems() -> list:
    """Итог validate() текущего экземпляра (из кэша двери) — для экрана;
    у годного каталога здесь только предупреждения."""
    with LOCK:
        cat = current()
        if _STATE.get("cat") is cat and "problems" in _STATE:
            return list(_STATE["problems"])
        return list(cat.validate())                   # pragma: no cover


def stamp() -> dict:
    """Версия, отпечаток и путь текущего каталога — блок catalog реестра
    исполнителей («реестр выведен под этим каталогом») и экрана."""
    cat = current()
    return {"version": cat.version(), "fingerprint": cat.fingerprint(),
            "path": str(cat.path)}


#: Исходы сверки ключа пакета manifest.catalog с каталогом продукта
#: (§8 схемы каталога, шаг 3). Закрытый перечень: состояние нажатия,
#: журнал и реестр исполнителей называют ровно эти слова.
CHECK_MATCH = "match"                     # версия и отпечаток сошлись
#: Данные каталога другие: версия та же либо разница patch (а на
#: версиях от 1.0.0 — и minor); вердикт читателя при этом —
#: предупреждение, стопа нет.
CHECK_MISMATCH = "mismatch-fingerprint"
CHECK_NOT_DECLARED = "not-declared"       # версия каталога не объявлена
CHECK_STOP = "stop"                       # расчёт не начинается
#: Машинный код отказа нажатия при стопе сторожа (система прогонов).
STOP_REASON_CODE = "catalog-version-mismatch"

#: Слова статуса журнальной строки нажатия (Ш-1кш). Выводятся из
#: ВЕРДИКТА читателя (check_status), а не из равенства отпечатков:
#: patch и minor от 1.0.0 отпечаток менять не обязаны, но читатель
#: судит их предупреждением — и строка журнала обязана сказать то же.
STATUS_STOP = "остановка"
STATUS_MATCH = "сошлось"
STATUS_WARNING = "предупреждение"


def product_stamp() -> dict:
    """«Каким каталогом идёт продукт» — вторая сторона сверки с пакетом:
    версия, отпечаток, путь и машинное имя источника (`catalog`).

    Шаг 7, часть 7-Б (08.09.2026): режим СРЕЗА базы шаблонов
    (`CREW_VMP_TEMPLATE_BASE`) снят — источник у продукта один, каталог,
    и ветки «отметка из полей среза» больше нет. Поле `source` осталось:
    им подписана мерка исхода в паспорте прогона и реестре исполнителей,
    и записи прежних прогонов (`source: "slice"`) читаются как прежде.
    Отказ каталога — исключение CATALOG_ERRORS: сверять не с чем, и это
    не дело сторожа, а рубежей нажатия."""
    st = stamp()
    return {"version": st["version"], "fingerprint": st["fingerprint"],
            "source": "catalog", "path": st["path"]}


def manifest_check(manifest: dict | None) -> dict:
    """Сторож дрейфа каталога шаблонов ПЕРЕД НАЖАТИЕМ (§8 схемы, шаг 3):
    ключ пакета `manifest.catalog` против каталога, которым идёт
    продукт. Слова судит читатель (template_catalog.manifest), решение
    «считать или не считать» — нажатие.

    Возврат: {"state", "severity", "words", "quiet", "package",
    "product"} — `product` называет ту самую мерку, по которой вынесен
    исход (версия, отпечаток, источник `catalog`): без неё запись
    «сошлось» в паспорте и реестре не проверить. `quiet` — «читателю
    сказать нечего» (см. check_status).
      match                — версия и отпечаток сошлись (у каталога-
                             черновика к этому добавляются слова
                             «каталог в черновике»: у черновика одна
                             версия законно несёт разные данные);
      not-declared         — версия каталога пакетом не объявлена
                             (пакет до «Дополнения к 1.18»):
                             предупреждение, нажатие идёт;
      mismatch-fingerprint — данные каталога другие (версия та же либо
                             разница patch, а на версиях от 1.0.0 — и
                             minor): предупреждение в состоянии и
                             журнале, нажатие идёт;
      stop                 — разница СТАРШЕГО числа версии (в любую
                             сторону), а на версиях 0.x — и среднего;
                             несравнимая или кривая версия; одна версия
                             у двух ВЫПУСКОВ с разными данными: расчёт
                             не начинается, слова называют обе стороны.
    Каталог недоступен — исключение CATALOG_ERRORS: это не дело
    сторожа, отказ каталога судят рубежи нажатия своими словами."""
    from template_catalog import manifest as _mf
    pkg = (manifest or {}).get("catalog") if isinstance(manifest, dict) \
        else None
    prod = product_stamp()
    problem = _mf.check_manifest_catalog(
        pkg, {"catalog_version": prod["version"],
              "catalog_fingerprint": prod["fingerprint"]})
    if pkg is None:
        state = CHECK_NOT_DECLARED
    elif problem is not None and problem.severity == "error":
        state = CHECK_STOP
    elif (pkg or {}).get("fingerprint") != prod["fingerprint"]:
        state = CHECK_MISMATCH
    else:
        state = CHECK_MATCH
    words = None if problem is None else problem.message
    if words:
        # Слова читателя называют обе стороны («пакет собран по
        # каталогу X, исполнители привязаны к Y»); здесь добавляется
        # только имя охраняемого — сам каталог шаблонов.
        words = "каталог шаблонов: " + words
    # «Тихий» исход — только когда читателю сказать нечего либо
    # единственная его пометка «каталог в черновике» при РАВНЫХ
    # отпечатках и той же тройке чисел (у черновика одна версия
    # законно несёт разные данные). Разница patch (и minor от 1.0.0)
    # отпечаток менять не обязана, но читатель судит её
    # предупреждением — тишиной она не бывает.
    same_triple = False
    if state == CHECK_MATCH:
        try:
            same_triple = _mf.compare_versions(
                str((pkg or {}).get("version")),
                prod["version"]).level == "same"
        except ValueError:                                # pragma: no cover
            same_triple = False
    prod_out = {k: prod[k] for k in ("version", "fingerprint", "source")}
    return {"state": state,
            "severity": (None if problem is None else problem.severity),
            "words": words,
            "quiet": bool(problem is None or same_triple),
            "package": pkg if isinstance(pkg, dict) else None,
            "product": prod_out}


def check_status(check: dict | None) -> str:
    """Слово статуса журнальной строки нажатия (Ш-1кш) по ВЕРДИКТУ
    читателя: «остановка» — стоп; «сошлось» — читателю сказать нечего
    (либо единственная пометка «каталог в черновике» при равных
    отпечатках: совпадение не шумит); всё прочее — «предупреждение».
    По равенству отпечатков статус выводить нельзя: у patch и minor от
    1.0.0 отпечаток может совпасть, а вердикт — предупреждение, и
    строка «сошлось» рядом со словами «продолжаем с предупреждением»
    противоречила бы сама себе."""
    state = (check or {}).get("state")
    if state == CHECK_STOP:
        return STATUS_STOP
    return STATUS_MATCH if (check or {}).get("quiet") else STATUS_WARNING


def _order_path(cat) -> Path:
    return Path(cat.path) / TRANSITION_DIR / ORDER_FILE


def bindings_path() -> Path:
    """Файл привязок продукта: подмена контура (BINDINGS_ENV) либо
    vmp/bindings.json дерева. Одно место, где решается «какой файл», —
    как и у каталога."""
    override = (os.environ.get(BINDINGS_ENV) or "").strip()
    return Path(override) if override else BINDINGS_FILE


def cdpm_bindings_path() -> Path:
    """Привязки CDPM: явный путь площадки либо файл дерева.

    Нечитаемый override не заменяется файлом по умолчанию: состояние
    чужого файла и отметка проекций обязаны относиться к одному пути.
    """
    override = (os.environ.get(CDPM_BINDINGS_ENV) or "").strip()
    return Path(override) if override else CDPM_BINDINGS_FILE


def _stamp_of(path: Path) -> tuple:
    """(абсолютный путь, mtime_ns, size); нет файла — тот же путь и None.

    Basename не различает два контура с одинаковыми именами и stat.
    absolute() сохраняет identity даже у отсутствующего файла, не
    добавляя обязательного разрешения символических ссылок.
    """
    identity = str(path.absolute())
    try:
        st = path.stat()
        return (identity, st.st_mtime_ns, st.st_size)
    except OSError:
        return (identity, None, None)


def transition_stamp(cat) -> tuple:
    """Отметка трёх файлов, которых читатель не видит: порядка семей
    (transition/families_order.json), привязок продукта и привязок CDPM.
    Ею живёт кэш проекций обёртки — правка любого из них на диске обязана
    дать новые проекции без перезапуска процесса. Опись исполнителей шага
    1 сюда больше не входит: дверь её не читает.

    Файл CDPM в отметке потому, что его читает bindings_states() (признак
    исполнителя у критерия в проекциях обёртки): без него выпуск или
    правка cdpm/bindings.json оставляла бы проекцию прежней МОЛЧА. Сегодня
    CDPM объявляет только check_template и только у семей, но закон
    отметки не должен зависеть от сегодняшнего состава чужого файла."""
    return (_stamp_of(_order_path(cat)), _stamp_of(bindings_path()),
            _stamp_of(cdpm_bindings_path()))


def families_order(cat=None) -> list:
    """Порядок семей переходного носителя transition/families_order.json,
    проверенный: форма list[str] (иначе CatalogFormatError с путём),
    множество совпадает с записями «есть» каталога и без повторов (иначе
    CatalogFormatError с перечнем лишних, пропущенных и повторов). Нет
    файла — CatalogNotFound с путём. Кэш по (экземпляр, отметка)."""
    cat = cat if cat is not None else current()
    path = _order_path(cat)
    ts = _stamp_of(path)
    with LOCK:
        hit = _STATE.get("order")
        if hit is not None and hit[0] is cat and hit[1] == ts:
            return list(hit[2])
        from template_catalog.reader import read_json_file
        order = read_json_file(path)
        if not isinstance(order, list) or not all(isinstance(x, str)
                                                  for x in order):
            raise tc.CatalogFormatError(
                f"{path}: переходный носитель порядка семей не по форме "
                f"[семья…]")
        known = set(cat.families())
        extra = sorted(set(order) - known)
        missing = sorted(known - set(order))
        if extra or missing or len(set(order)) != len(order):
            raise tc.CatalogFormatError(
                f"{path}: порядок семей не совпадает с записями «есть» "
                f"каталога {cat.version()}: лишние {extra}, пропущены "
                f"{missing}, повторы "
                f"{sorted({x for x in order if order.count(x) > 1})}")
        order = [str(x) for x in order]
        _STATE["order"] = (cat, ts, order)
        return list(order)


#: Правила читателя, которые дверь НЕ судит: полнота видов и стендовые
#: подтверждения — дело гейта продукта, а не каждой операции.
GATE_ONLY_CODES = ("B04", "B05")


def bindings(cat=None) -> dict:
    """Документ привязок COSMOS, проверенный читателем каталога (B01–B03;
    B04/B05 — гейт). Нет файла — CatalogNotFound с путём и командой
    сборки; кривая форма, запись не из каталога, старшая разница версий
    или «одна версия каталога — разные данные» — CatalogFormatError
    словами. Предупреждение B01 черновика («отпечаток привязок отстал»)
    отказом НЕ является: у черновика одна версия законно несёт разные
    данные, а сводка называет это состоянием «stale-draft». Кэш по
    (экземпляр, отметка файла)."""
    cat = cat if cat is not None else current()
    path = bindings_path()
    ts = _stamp_of(path)
    with LOCK:
        hit = _STATE.get("bindings")
        if hit is not None and hit[0] is cat and hit[1] == ts:
            return hit[2]
        if not path.is_file():
            raise tc.CatalogNotFound(
                f"{path}: привязок исполнителей COSMOS нет — соберите их "
                f"(python -m vmp.bindings_build --out vmp/bindings.json); "
                f"без них запись каталога не называет исполняющего её кода")
        from template_catalog.bindings import check_bindings, load_bindings
        doc = load_bindings(path)
        problems = [p for p in check_bindings(cat, doc)
                    if p.code not in GATE_ONLY_CODES]
        errors = [f"{p.where}: {p.message}" for p in problems
                  if p.severity == "error"]
        if errors:
            raise tc.CatalogFormatError(
                f"{path}: привязки COSMOS не по правилам каталога "
                f"{cat.version()}: " + "; ".join(errors[:5])
                + (f" (и ещё {len(errors) - 5})" if len(errors) > 5 else ""))
        state = (BIND_STALE if any(p.code == "B01" for p in problems)
                 else BIND_MATCH)
        _STATE["bindings"] = (cat, ts, doc, state)
        return doc


def bindings_state(cat=None) -> str:
    """Состояние файла привязок машинным словом: BIND_MATCH (сошлось),
    BIND_STALE (черновик: отпечаток привязок отстал), BIND_MISSING
    (файла нет)."""
    try:
        bindings(cat)
    except tc.CatalogNotFound:
        return BIND_MISSING
    return _STATE.get("bindings", (None, None, None, BIND_MATCH))[3]


def cdpm_bindings(cat=None) -> dict | None:
    """Привязки CDPM, если она их объявила и они читаются, — только для
    слов сводки и показа чужих клеток на экране; полноту по паре файлов
    судит гейт.

    ЧУЖОЙ ФАЙЛ НЕ ОСТАНАВЛИВАЕТ COSMOS. Нет файла — None и законное «не
    объявлены». Файл есть, но не читается (кривой JSON, не та форма,
    запись не из каталога, полузаписанный файл в момент выпуска CDPM) —
    тоже None, а причина уходит в сводку третьим словом «не читаются»:
    иначе правка в чужой зоне роняла бы проекции COSMOS и все нажатия.
    Слово «объявлены» решается не существованием файла, а ТЕМ, ЧТО
    ФАЙЛ ПРОЧИТАН и прошёл читателя (B01–B03): показывать чужие клетки,
    не проверив документ, значило бы выдавать непрочитанное за
    объявленное. Кэш — по отметке файла (она уже входит в
    transition_stamp), чтобы чужой документ не читался на каждой
    операции."""
    return _cdpm_bindings_state(cat)[0]


def cdpm_bindings_state(cat=None) -> tuple:
    """(документ | None, слово, причина | None) о чужих привязках:
    CDPM_YES / CDPM_NO / CDPM_BAD. Слово — из закрытого перечня, причина
    — свободные слова читателя (её показывают подсказкой)."""
    return _cdpm_bindings_state(cat)


def _cdpm_bindings_state(cat=None) -> tuple:
    path = cdpm_bindings_path()
    ts = _stamp_of(path)
    if not path.is_file():
        # Файла нет — каталог для этого не нужен: слово известно сразу.
        return (None, CDPM_NO, None)
    # Каталог берётся ДО суда, и его отказ НЕ переводится в «чужой файл
    # не читается»: отказ каталога — свой и поднимается как есть.
    cat = cat if cat is not None else current()
    with LOCK:
        hit = _STATE.get("cdpm_bindings")
        if hit is not None and hit[0] is cat and hit[1] == (str(path), ts):
            return hit[2]
    # Чтение и суд — вне замка: читателя зовут и другие потоки.
    try:
        from template_catalog.bindings import check_bindings, load_bindings
        doc = load_bindings(path)
        errors = [f"{p.where}: {p.message}"
                  for p in check_bindings(cat, doc)
                  if p.severity == "error" and p.code not in GATE_ONLY_CODES]
        if errors:
            out = (None, CDPM_BAD, "; ".join(errors[:3])
                   + (f" (и ещё {len(errors) - 3})" if len(errors) > 3 else ""))
        else:
            out = (doc, CDPM_YES, None)
    except Exception as e:                                    # noqa: BLE001
        out = (None, CDPM_BAD, f"{type(e).__name__}: {e}")
    with LOCK:
        _STATE["cdpm_bindings"] = (cat, (str(path), ts), out)
    return out


def bindings_executors(cat=None) -> dict:
    """{семья: ["<вид> · <символ> · <файл>", …]} — строки «есть» привязок
    В ПОРЯДКЕ ФАЙЛА (его задал генератор ключом §1.1, и обёртка
    показывает ровно его). Семья без строк «есть» получает пустой
    перечень — законный пробел, не отказ; состояния критериев берут из
    bindings_states()."""
    out: dict = {}
    for row in bindings(cat).get("bindings") or []:
        if not isinstance(row, dict) or row.get("section") != "families":
            continue
        out[str(row.get("id"))] = [
            f"{ex.get('kind')} · {ex.get('symbol')} · {ex.get('file')}"
            for ex in row.get("executors") or []
            if isinstance(ex, dict) and ex.get("state") == "есть"]
    return out


def _merge_states(out: dict, doc: dict) -> None:
    """Лучшее состояние клетки по строкам ОДНОГО продукта: у вида законно
    несколько строк (ручка «есть» и ручка «нет» одной семьи), и «нет»,
    оказавшееся в файле последним, не должно прятать «есть»."""
    product = str(doc.get("product") or "?")
    for row in doc.get("bindings") or []:
        if not isinstance(row, dict):
            continue
        cell = out.setdefault(str(row.get("section")), {}) \
                  .setdefault(str(row.get("id")), {})
        for ex in row.get("executors") or []:
            if not isinstance(ex, dict) or not ex.get("kind"):
                continue
            by_product = cell.setdefault(str(ex["kind"]), {})
            state, best = str(ex.get("state")), by_product.get(product)
            if best is None or STATE_RANK.get(state, 9) < \
                    STATE_RANK.get(best, 9):
                by_product[product] = state


def bindings_states(cat=None) -> dict:
    """{раздел: {id: {вид: {продукт: состояние}}}} для экрана — по строкам
    файлов обоих продуктов (свой обязателен, чужой — если объявлен) плюс
    заявки каталога «исполнитель заявлен» под именем подавшего. Считается
    ЗДЕСЬ, а не читателем: у читателя executor_states() оставляет
    последнюю строку клетки (его не трогаем — договорённость с CDPM), а
    экрану нужно лучшее."""
    cat = cat if cat is not None else current()
    out: dict = {}
    _merge_states(out, bindings(cat))
    other = cdpm_bindings(cat)
    if other is not None:
        _merge_states(out, other)
    for rq in cat.requests():
        if rq.get("state") != "исполнитель заявлен":
            continue
        for kind in rq.get("executor_kinds_requested") or []:
            out.setdefault(str(rq.get("section")), {}) \
               .setdefault(str(rq.get("record_id")), {}) \
               .setdefault(str(kind), {})[str(rq.get("submitted_by"))] = \
                "заявлен"
    return out


def bindings_requests(cat=None) -> dict:
    """{раздел: {id: {вид: {request_id, state, submitted_by, cr_key}}}} —
    ЗАЯВКА, из-за которой клетка «заявлен».

    Тот же перебор заявок, что у `bindings_states` (один источник —
    `cat.requests()`), только вместо слова состояния клетка получает имя
    заявки: экран ведёт чип «заявлен» на КАРТОЧКУ заявки, а не на список
    (руководства 13.10 и 03 §12п обещают карточку — ревью волны 2).
    Заявка на новую запись видов не называет (`executor_kinds_requested`
    у неё пуст), и клеток у неё нет по построению."""
    cat = cat if cat is not None else current()
    out: dict = {}
    for rq in cat.requests():
        if rq.get("state") != "исполнитель заявлен":
            continue
        row = {"request_id": rq.get("request_id"), "state": rq.get("state"),
               "submitted_by": rq.get("submitted_by"),
               "cr_key": rq.get("cr_key")}
        for kind in rq.get("executor_kinds_requested") or []:
            out.setdefault(str(rq.get("section")), {}) \
               .setdefault(str(rq.get("record_id")), {})[str(kind)] = row
    return out


def bindings_summary() -> dict:
    """Сводка привязок для реестра нажатия и экрана: чем и по какому
    каталогу собран файл, отпечаток его КАНОНА (по нему сторож дрейфа
    видит правку привязок, не замечая смены момента сборки), счётчики
    клеток и строк, итог стенда и слово о привязках CDPM. Нет файла —
    не отказ, а состояние «missing» с нулями: сводка обязана сказать это
    словами, а не промолчать. Состояние «missing» до ЭКРАНА базы
    шаблонов не доходит — без привязок экран отказывает целиком (§3
    двери: bindings() поднимает CatalogNotFound); сводку с «missing»
    читают реестр исполнителей и паспорт прогона.

    Итог стенда различает ЗАДАЧИ и СТРОКИ: у задачи строк столько,
    сколько видов она судит (29 задач дают 57 строк), и одно число под
    двумя именами читалось бы неправдой."""
    path = bindings_path()
    cat = current()
    _doc, cdpm_word, cdpm_note = _cdpm_bindings_state()
    try:
        doc = bindings(cat)
    except tc.CatalogNotFound:
        return {"file": str(path), "product_version": None,
                "generated_from": None, "generated_at": None,
                "catalog_version": None, "catalog_fingerprint": None,
                "fingerprint": None, "state": BIND_MISSING, "cells": 0,
                "present_rows": 0, "absent_rows": 0, "declared_rows": 0,
                "stand": {"tasks": 0, "rows": 0, "passed": 0, "failed": 0,
                          "skipped": 0},
                "cdpm": cdpm_word, "cdpm_note": cdpm_note}
    from vmp.bindings_build import fingerprint as _fingerprint
    by_state = {"есть": 0, "нет": 0, "заявлен": 0}
    stand = {"tasks": 0, "rows": 0, "passed": 0, "failed": 0, "skipped": 0}
    tasks: set = set()
    for row in doc.get("bindings") or []:
        if not isinstance(row, dict):
            continue
        for ex in row.get("executors") or []:
            if isinstance(ex, dict) and str(ex.get("state")) in by_state:
                by_state[str(ex["state"])] += 1
        for sv in row.get("stand_verified") or []:
            stand["rows"] += 1
            if not isinstance(sv, dict):
                continue
            tasks.add((str(row.get("section")), str(row.get("id")),
                       str(sv.get("task"))))
            if sv.get("result") in stand:
                stand[str(sv["result"])] += 1
    stand["tasks"] = len(tasks)
    cells = sum(len(r.get("executor_kinds") or [])
                for r in cat.families_full())
    cells += sum(len(r.get("computable_by") or [])
                 for r in cat.criteria().values())
    return {"file": str(path),
            "product_version": doc.get("product_version"),
            "generated_from": doc.get("generated_from"),
            "generated_at": doc.get("generated_at"),
            "catalog_version": doc.get("catalog_version"),
            "catalog_fingerprint": doc.get("catalog_fingerprint"),
            "fingerprint": _fingerprint(doc),
            "state": _STATE.get("bindings",
                                (None, None, None, BIND_MATCH))[3],
            "cells": cells, "present_rows": by_state["есть"],
            "absent_rows": by_state["нет"],
            "declared_rows": by_state["заявлен"], "stand": stand,
            "cdpm": cdpm_word, "cdpm_note": cdpm_note}


def complements() -> dict[str, tuple[str, ...]]:
    """Дополняющие семьи по записям каталога кортежами (симметрия — X01)."""
    comps = current().lexicon().get("complements") or {}
    return {str(k): tuple(str(x) for x in (v or ())) for k, v in comps.items()}


def forget() -> None:
    """Сброс кэша двери и читателя (тесты, смена каталога в процессе)."""
    with LOCK:
        _STATE.clear()
        if tc is not None:
            tc.forget_current()
