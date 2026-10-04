# -*- coding: utf-8 -*-
"""Обёртка шага 2 над каталогом шаблонов — ТОНКАЯ ПРОЕКЦИЯ
(07.09.2026; шаг 7, часть 7-Б — 08.09.2026).

Реестр констант (семьи механик, словарь «параметр → семья», вид и зона
семьи, единица нарушения, классы критериев) ушёл в данные — catalog/
(читатель template_catalog, дверь vmp/catalog_access). Здесь — только
формы проекций для потребителей COSMOS, registry_rev (с шага 3 —
отпечаток каталога) и контрольные пары контура. Своего закона у обёртки
нет: каждая проекция выводится из одного экземпляра каталога, второй
формулы отпечатка и запаса из кода тут нет.

Шаг 7, часть 7-Б (08.09.2026): снят РЕЖИМ СРЕЗА базы шаблонов
(переменная `CREW_VMP_TEMPLATE_BASE`, ветки `families()`,
`registry_rev()`, `families_meta()` и вся сборка «поля среза первичны,
недостающее — из каталога»). Причина принципиальная: у одной вещи не
бывает двух мерок — правила сажает каталог, и второй, необъявленный
источник рядом с ним делал прогон непроверяемым. Срез базы остаётся
артефактом НАБОРА (`etalon/NSH-H0.json`): его выводят ИЗ этих проекций
генератором `open-bench/polyaris/build_nsh_h0.py` и сверяют покрытие —
но им больше ничего не подменяют. CDPM обёртку не читает вовсе (её
мост — `cdpm/catalog_bridge.py`).

Проекции — снимок ЭКЗЕМПЛЯРА каталога, не процесса: шесть констант
строятся один раз на экземпляр (ключ — сам экземпляр и отметка файлов,
которых читатель не видит: порядок семей transition/families_order.json и
файл привязок vmp/bindings.json) и отдаются тем же объектом, пока файлы не
менялись; смена на диске даёт новые объекты у всех потребителей сразу.
Константы отдаются модульным __getattr__ (PEP 562) и в globals() не
кладутся. Подмена констант в тестах — только каталогом (tmp-копия через
tests/catalog_copies.py + CREW_TEMPLATE_CATALOG), не setattr: имя,
положенное в globals(), заморозило бы проекцию.

Без каталога, без читателя или при кривом каталоге любая проекция —
отказ словами (catalog_access.CATALOG_ERRORS); запаса из кода нет.
Проекции — свойства с отказом: getattr(tr, имя, умолчание) и
hasattr(tr, имя) от отказа каталога не защищают (поднимается
CatalogError, не AttributeError) — читатель ловит CATALOG_ERRORS.
Порядок: константы и families_map() — по transition/families_order.json,
VIOLATION_UNITS — порядок словаря каталога, families()/families_meta() —
порядок читателя (по id); потребители порядком не пользуются.

Шаг 4 (08.09.2026): исполнители семей и признак исполнителя у критерия
берутся из ПРИВЯЗОК продукта (vmp/bindings.json через дверь:
bindings_executors — строки «есть» «вид · символ · файл» в порядке файла;
bindings_states — состояние клетки «запись × вид» по продуктам). Прежний
переходный носитель transition/executors_legacy.json дверью не читается
и остаётся на диске СВИДЕТЕЛЕМ описи — шаг 7-Б его оставил: он левая
сторона гейта «ничего не потеряно» (tests/vmp/test_bindings.py, п. 13;
руководство 13.12), и другого носителя этой правды в дереве нет. criteria_spec отдаёт в
vmp.criteria.SPEC только записи с формой экземпляра (instance_form):
метрики решателя формы экземпляра не имеют — их место в целевой
(objectives[].metric), не в блоке criterion пакета.

Файл-носитель контрольных пар контура:
<data_root>/vmp-criterion-control-pairs.json (конвенция 2026-08-29:
form_version / customer / _comment; «нет файла = механизм спит»,
«кривой = честный отказ словами»). Пара — арифметическая
самопроверка исполнителя на известном примере контура: расхождение
= код и данные разъехались, расчёт не начинается.
"""
from __future__ import annotations

import json
from pathlib import Path

from vmp import catalog_access as _ca

CONTROL_PAIRS_FILE = "vmp-criterion-control-pairs.json"
CATALOG_ENV = _ca.CATALOG_ENV
catalog = _ca.current          # реэкспорт: текущий экземпляр каталога
forget = _ca.forget
complements = _ca.complements

#: Имена проекций, отдаваемых __getattr__ (в globals() их нет).
_NAMES = ("RULE_MECHANIC_TYPES", "PARAM_TO_FAMILY", "FAMILY_KIND_ZONE",
          "FAMILY_VIOLATION_UNIT", "VIOLATION_UNITS", "CRITERION_TYPES")
#: (экземпляр каталога, отметка порядка семей и привязок, {имя: объект})
#: — одним кортежем.
_CACHE: tuple | None = None


def _build(cat) -> dict:
    """Шесть проекций из одного экземпляра каталога, порядка семей и
    привязок продукта. Порядок семей — transition/families_order.json
    (дверь проверила равенство множеств); исполнители — строки «есть»
    привязок в порядке файла (шаг 4); PARAM_TO_FAMILY — по семьям в этом
    порядке, внутри семьи — по алфавиту; VIOLATION_UNITS — порядок
    словаря. Рядом с шестью именами кладутся две служебные карты
    критериев (заголовок записи и признак исполнителя по привязкам) —
    их отдают families_map() и lookup(); в _NAMES их нет, модульным
    __getattr__ они не видны."""
    meta = cat.families_meta()
    recs = {r["id"]: r for r in cat.families_full()}
    order = _ca.families_order(cat)
    executors = _ca.bindings_executors(cat)
    states = _ca.bindings_states(cat)
    ids = list(order)
    rmt = {f: {"class": recs[f]["class"], "title": recs[f]["title"],
               "executors": list(executors.get(f) or [])} for f in ids}
    p2f = {n: f for f in ids for n in meta[f]["params"]}
    fkz = {f: (meta[f]["kind"], meta[f]["zone"]) for f in ids}
    fvu = {f: meta[f]["violation_unit"] for f in ids
           if meta[f]["violation_unit"]}
    # Словарь единиц нарушения с выпуска 0.5.0 — КАРТА «код → слово»:
    # членство судится по КЛЮЧАМ, и перечень реестра — это её ключи в
    # порядке словаря. Слово единицы живёт рядом с кодом у каталога
    # (`vmp.chain_price.unit_words`), второй карты перевода в COSMOS
    # нет. Перечень (форма 0.4.0 и снимков выпусков до 0.5.0) читается
    # как есть: порядок и состав у обеих форм одни и те же.
    vu = cat.dictionaries()["violation_units"]
    units = tuple(str(u) for u in (vu.keys() if isinstance(vu, dict) else vu))
    crits = cat.criteria()
    ct = {cid: {"class": r["class"]} for cid, r in crits.items()}
    crit_states = (states.get("criteria") or {})
    titles = {cid: r.get("title") for cid, r in crits.items()}
    bound = {cid: _has_present(crit_states.get(cid)) for cid in crits}
    return {"RULE_MECHANIC_TYPES": rmt, "PARAM_TO_FAMILY": p2f,
            "FAMILY_KIND_ZONE": fkz, "FAMILY_VIOLATION_UNIT": fvu,
            "VIOLATION_UNITS": units, "CRITERION_TYPES": ct,
            "_criteria_titles": titles, "_criteria_bound": bound}


#: Состояние клетки «запись × вид» в привязках, означающее исполнителя.
STATE_PRESENT = "есть"


def _has_present(kinds: dict | None) -> bool:
    """Есть ли у записи хоть один вид, исполняемый хоть одним продуктом
    («есть» в привязках). Форма довода — {вид: {продукт: состояние}}."""
    for products in (kinds or {}).values():
        if any(st == STATE_PRESENT for st in (products or {}).values()):
            return True
    return False


def _projections() -> dict:
    global _CACHE
    cat = _ca.current()
    ts = _ca.transition_stamp(cat)
    hit = _CACHE
    if hit is not None and hit[0] is cat and hit[1] == ts:
        return hit[2]
    with _ca.LOCK:
        hit = _CACHE
        if hit is not None and hit[0] is cat and hit[1] == ts:
            return hit[2]
        values = _build(cat)
        _CACHE = (cat, ts, values)
        return values


def __getattr__(name):
    if name in _NAMES:
        return _projections()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_NAMES))


def criteria_spec(cat=None) -> dict:
    """Проекция для vmp.criteria.SPEC (§10 схемы записи): записи критериев
    «есть» с исполнителем-построителем ("builder_method" ∈ computable_by)
    И С ФОРМОЙ ЭКЗЕМПЛЯРА (instance_form): SPEC — словарь видов БЛОКА
    `criterion` пакета, а вид без формы экземпляра в этом блоке
    невыразим. Отбор по форме заведён шагом 4 (08.09.2026) вместе с
    критериями-метриками решателя: метрика живёт в целевой
    (`objectives[].metric`), экземпляра не имеет и в SPEC не попадает —
    иначе судья критерия объявлял бы пробел там, где вида в блоке
    пакета быть не может. Форма кода дословно — {id: {title, needs[,
    semantics: {formula[, rates_form]}]}}: ключ semantics только при
    formula, rates_form — вложен, text записи не переносится."""
    import copy
    out: dict = {}
    for cid, rec in (cat if cat is not None else _ca.current()) \
            .criteria().items():
        if "builder_method" not in (rec.get("computable_by") or []):
            continue
        if rec.get("instance_form") is None:
            continue
        entry = {"title": rec.get("title"), "needs": rec.get("needs")}
        sem = rec.get("semantics") or {}
        if sem.get("formula") is not None:
            entry["semantics"] = {"formula": sem["formula"]}
            if sem.get("rates_form") is not None:
                entry["semantics"]["rates_form"] = copy.deepcopy(
                    sem["rates_form"])
        out[cid] = entry
    return out


# ------------------------------------------------ контрольные пары контура

class ControlPairError(ValueError):
    """Контрольная пара контура не сходится либо файл-носитель кривой."""


def _data_root() -> Path:
    import _data_paths
    return Path(_data_paths.data_root())


def load_control_pairs(path: Path | None = None) -> dict | None:
    """Носитель контрольных пар контура. Нет файла — None (механизм
    спит); кривой файл — честный отказ словами."""
    p = path or (_data_root() / CONTROL_PAIRS_FILE)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ControlPairError(
            f"файл контрольных пар {p} не читается: {e}") from e
    if not isinstance(doc, dict) \
            or not isinstance(doc.get("pairs"), list) \
            or not doc.get("customer"):
        raise ControlPairError(
            f"файл контрольных пар {p} без обязательных полей "
            f"(customer, pairs) — форма конвенции 2026-08-29")
    return doc


def check_control_pair(kind: str, deci: dict,
                       path: Path | None = None) -> str:
    """Самопроверка исполнителя критерия на паре контура.

    Возврат: 'silent' (файла нет), 'no_pair' (файл есть, пары для
    вида нет), 'ok' (сошлась). Расхождение — ControlPairError:
    код и данные контура разъехались, расчёт не начинается."""
    doc = load_control_pairs(path)
    if doc is None:
        return "silent"
    for pair in doc["pairs"]:
        if not isinstance(pair, dict) \
                or pair.get("criterion_kind") != kind:
            continue
        need = ("flight_min", "dh_min", "crew",
                "expected_total_deci")
        if not all(isinstance(pair.get(k), int) for k in need):
            raise ControlPairError(
                f"пара вида {kind!r} без целых полей {need} — "
                f"форма носителя нарушена")
        got = (deci["deci_work"] * pair["flight_min"]
               + deci["deci_dh"] * pair["dh_min"]) * pair["crew"]
        if got != pair["expected_total_deci"]:
            raise ControlPairError(
                f"контрольная пара вида {kind!r} не сходится: "
                f"вычислено {got} деци против ожидания "
                f"{pair['expected_total_deci']} (носитель "
                f"{doc.get('customer')!r}) — исполнитель и данные "
                f"контура разъехались, расчёт не начинается")
        return "ok"
    return "no_pair"


# ------------------------------------------------------- карта и поиск

def families_map() -> dict:
    """Карта покрытия семей — машинный перечень базы шаблонов
    (п. 4 постановки P-N-SHD, основа инструмента владельца 6а):
    единый источник для экрана администратора, отчёта покрытия и
    заявок конвейера. Для каждой семьи механик — класс, заголовок,
    исполнители (строки «есть» привязок) и машинные признаки
    сопоставления (поле export_solver.family правила и/или имена
    параметров закрытого словаря); для критериев — класс, заголовок
    ЗАПИСИ каталога и наличие исполнителя. Результат — свежие копии.

    Шаг 4: заголовок критерия берётся из записи, а не из SPEC, и
    признак исполнителя — «код COSMOS (criteria.EXECUTORS) ЛИБО «есть»
    любого вида в привязках». Иначе шесть критериев-метрик решателя
    (в SPEC их нет — формы экземпляра у них нет, см. criteria_spec)
    остались бы на экране без названия и с ложным «исполнителя нет»
    при живом исполнителе-решателе."""
    p = _projections()
    params_by_family: dict[str, list[str]] = {}
    for pname, fam in p["PARAM_TO_FAMILY"].items():
        params_by_family.setdefault(fam, []).append(pname)
    rule_families = {}
    for fam, spec in p["RULE_MECHANIC_TYPES"].items():
        rule_families[fam] = {
            "class": spec.get("class"),
            "title": spec.get("title"),
            "executors": list(spec.get("executors") or ()),
            "mapped_by": {
                # приёмник COSMOS кроет мягкое правило ЛЮБОЙ семьи
                # базы полем export_solver.family (derive_rule)
                "family_field": True,
                "params": sorted(params_by_family.get(fam, [])),
            },
        }
    from vmp import criteria
    titles = p["_criteria_titles"]
    bound = p["_criteria_bound"]
    crits = {}
    for kind, spec in p["CRITERION_TYPES"].items():
        crits[kind] = {
            "class": spec.get("class"),
            "title": titles.get(kind),
            "has_executor": (criteria.EXECUTORS.get(kind) is not None
                             or bool(bound.get(kind))),
        }
    return {"rule_families": rule_families, "criteria": crits}


def lookup(kind: str) -> dict | None:
    """Тип шаблона по имени: критерии и семьи механик правил.

    У критерия `known_to_spec` — «вид известен блоку criterion пакета»
    (SPEC), `has_executor` — тем же правилом, что у families_map(): код
    COSMOS либо «есть» любого вида в привязках. Два признака различны с
    шага 4: метрика решателя исполнителя имеет, а в блоке пакета не
    выражается."""
    p = _projections()
    if kind in p["CRITERION_TYPES"]:
        from vmp import criteria
        out = dict(p["CRITERION_TYPES"][kind])
        out["section"] = "criterion"
        out["known_to_spec"] = kind in criteria.SPEC
        out["has_executor"] = (kind in criteria.EXECUTORS
                               or bool(p["_criteria_bound"].get(kind)))
        return out
    if kind in p["RULE_MECHANIC_TYPES"]:
        out = dict(p["RULE_MECHANIC_TYPES"][kind])
        out["section"] = "rule_mechanic"
        return out
    return None


# ------------------------------------------ проекции базы шаблонов

def families() -> dict[str, list[str]]:
    """Семьи базы шаблонов -> отсортированные имена параметров словаря
    (форма template_families() CDPM): записи «есть» каталога в порядке
    читателя. Проекция двери, своего закона у обёртки нет."""
    return {f: list(ps) for f, ps in _ca.current().families().items()}


def registry_rev() -> str:
    """Отпечаток базы шаблонов — ОТПЕЧАТОК КАТАЛОГА, полные 64 hex
    (шаг 3 переезда, 07.09.2026; §10 схемы каталога, строка «3»).

    До шага 3 здесь считалась своя формула над семьями (12 hex) —
    вторая формула отпечатка рядом с отпечатком каталога. Двух мерок у
    одной вещи не бывает: строки посадки (`template_fit.registry_rev`),
    форма ВМП и ключ пакета `manifest.catalog` называют один и тот же
    отпечаток, и сторож дрейфа сверяет их без перевода. Прежнее
    значение осталось свидетелем неизменности данных: шапка каталога
    несёт его полем `legacy_registry_rev`, гейт паритета пересчитывает
    формулу сам (L10). Имя поля не меняется (§10)."""
    return _ca.current().fingerprint()


def families_meta() -> dict[str, dict]:
    """Семьи базы с атрибутами для детерминизации формализации
    (решение владельца 2026-09-02): записи «есть» каталога, 8 ключей
    читателя (params, kind, zone, class, violation_unit, nature,
    subject_words, complements). Шаг 7 (08.09.2026): режима СРЕЗА базы
    (`CREW_VMP_TEMPLATE_BASE`) больше нет — у обёртки одна сторона,
    каталог, и ветки «поля из среза, недостающее из каталога» с ней
    ушли. Срез базы остаётся артефактом НАБОРА (`etalon/NSH-H0.json`,
    генератор `open-bench/polyaris/build_nsh_h0.py`): его по-прежнему
    ВЫВОДЯТ из этих проекций, но им больше ничего не подменяют."""
    return {f: dict(m) for f, m in _ca.current().families_meta().items()}
