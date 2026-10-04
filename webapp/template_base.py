# -*- coding: utf-8 -*-
"""Инструмент работы с базой шаблонов (требование владельца
2026-08-30, постановка P-N-SHD раздел 6а).

Просмотр: карта семей с контрактами (vmp.template_registry.
families_map) + зависимые правила ТЕКУЩЕГО принятого ВМП (по тем же
машинным признакам, которыми авто-вывод реестра судит покрытие, —
vmp.registry_derive) + привязки исполнителей и итог стенда по каждой
записи (шаг 4 каталога, 08.09.2026: дверь vmp.catalog_access —
bindings_summary / bindings_states / bindings).

Правка и снятие записи идут НЕ голым редактированием и (с шага 6
каталога, 08.09.2026) НЕ заявкой очереди CR: запись каталога правит
владелец в СЛУЖБЕ каталога — правка становится черновиком аналитика,
снятие ставит статус «снята». Причина принципиальная: заявка каталога у
существующей записи бывает только на ВИД ИСПОЛНИТЕЛЯ (правило R02
читателя: «запись с таким id уже есть»), поэтому «заявка каталога на
правку» невозможна по построению, а прежняя заявка очереди клала в
очередь текст, который читает облачная модель. Экран поэтому ведёт на
карточку записи в службе, а поимённый перечень зависимых правил
действующего ВМП остаётся здесь — словами."""
from __future__ import annotations


class TemplateBaseError(ValueError):
    """Отказ инструмента словами; .dependents — поимённый перечень."""

    def __init__(self, message: str, dependents: list | None = None):
        self.dependents = list(dependents or ())
        super().__init__(message)


def _current_model() -> dict | None:
    """Модель текущего принятого ВМП (единый резолвер канала);
    пакета нет или он не читается — None (зависимых нет)."""
    from webapp import task_channel, task_package
    cur = task_channel.current_package()
    if cur is None:
        return None
    try:
        pkg = task_package.load(cur["path"], run_gate=False)
    except Exception:                                  # noqa: BLE001
        return None
    return pkg.get("model") or {}


def rule_family_links(model: dict | None) -> dict[str, list[dict]]:
    """Семья -> правила модели, опирающиеся на неё, по тем же
    машинным признакам, что у авто-вывода реестра: мягкое правило —
    поле export_solver.family; жёсткое/определяющее — имена
    параметров закрытого словаря PARAM_TO_FAMILY."""
    from vmp.template_registry import PARAM_TO_FAMILY
    out: dict[str, list[dict]] = {}
    for rule in (model or {}).get("rules") or ():
        if not isinstance(rule, dict):
            continue
        kind = rule.get("kind")
        fams: list[str] = []
        if kind == "SOFT":
            fam = (rule.get("export_solver") or {}).get("family")
            if fam:
                fams.append(str(fam))
        elif kind in ("HARD", "DEF"):
            for p in rule.get("params") or ():
                name = p.get("name") if isinstance(p, dict) else None
                fam = PARAM_TO_FAMILY.get(str(name))
                if fam and fam not in fams:
                    fams.append(fam)
        for fam in fams:
            out.setdefault(fam, []).append({
                "rule_id": str(rule.get("id")),
                "title": (rule.get("title") or "").strip(),
                "kind": kind,
            })
    return out


#: Пустой итог стенда у записи, о которой привязки ничего не сказали.
EMPTY_STAND = {"passed": 0, "failed": 0, "skipped": 0}


def stand_counts(bindings_doc: dict | None) -> dict:
    """Итог стендовых задач по записям привязок: (раздел, id) ->
    {passed, failed, skipped}. Исходы — закрытый перечень файла
    результатов стенда (§5.5 проекта шага 4); запись без строк стенда
    получает нули, и это видно на экране как «стенд не прогонялся»."""
    out: dict = {}
    for rec in (bindings_doc or {}).get("bindings") or ():
        if not isinstance(rec, dict):
            continue
        counts = dict(EMPTY_STAND)
        for row in rec.get("stand_verified") or ():
            result = row.get("result") if isinstance(row, dict) else None
            if result in counts:
                counts[result] += 1
        out[(str(rec.get("section")), str(rec.get("id")))] = counts
    return out


def overview() -> dict:
    """Данные экрана: карта семей и критериев + зависимые правила
    текущего ВМП + источник пакета + каталог шаблонов (версия,
    отпечаток, путь, число предупреждений проверки) + привязки
    исполнителей (шаг 4) — прозрачность снимка.

    Привязки на экране двумя слоями: сводка `bindings` над таблицами
    (файл, состояние, счётчики клеток и строк, итог стенда, слово о
    привязках CDPM) и у каждой записи — `executor_states`
    ({вид: {продукт: состояние}}, лучшее состояние клетки считает
    дверь) и `stand` ({passed, failed, skipped}). Экран показывает
    состояния, а не судит их: суд — гейт продукта
    (`python -m vmp.bindings_gate`) и проверка читателя.

    БЕЗ ФАЙЛА ПРИВЯЗОК ЭКРАН ОТКАЗЫВАЕТ ЦЕЛИКОМ, а не показывает
    таблицы со строкой «файла нет»: `families_map()` строит проекции по
    привязкам, и без них запись каталога не называет исполняющего её
    кода — показывать половину правды хуже, чем отказать словами
    (`vmp.catalog_access.bindings` → CatalogNotFound с путём и командой
    сборки; рубеж переводит это в 503). Состояние сводки «файла нет»
    (`missing`) поэтому живёт не здесь, а там, где сводку читают без
    проекций: блок `bindings` реестра исполнителей и паспорт прогона.
    Слово о чужих привязках — из закрытого перечня двери: «объявлены |
    не объявлены | не читаются» (причина нечитаемости — в
    `cdpm_note`)."""
    from vmp import catalog_access as _ca
    from vmp.template_registry import families_map
    from webapp import task_channel
    model = _current_model()
    links = rule_family_links(model)
    doc = families_map()
    cat = _ca.current()
    from webapp import catalog_pipeline
    states = _ca.bindings_states(cat)
    asked = _ca.bindings_requests(cat)
    stand = stand_counts(_ca.bindings(cat))
    for section, key in (("families", "rule_families"),
                         ("criteria", "criteria")):
        by_id = states.get(section) or {}
        asked_by_id = asked.get(section) or {}
        for rid, spec in doc[key].items():
            spec["executor_states"] = by_id.get(rid) or {}
            # Адрес карточки заявки строит СЕРВЕР одной формулой
            # (`catalog_pipeline.request_link`) — та же, что у вида
            # «Каталог»: чип «заявлен» ведёт на карточку, а не на список.
            spec["executor_requests"] = {
                kind: {**row,
                       "link": catalog_pipeline.request_link(
                           str(row.get("request_id") or ""))}
                for kind, row in (asked_by_id.get(rid) or {}).items()}
            spec["stand"] = stand.get((section, rid), dict(EMPTY_STAND))
    for fam, spec in doc["rule_families"].items():
        spec["dependents"] = links.get(fam, [])
    cur = task_channel.current_package()
    doc["package"] = ({"present": True, "source": cur.get("source"),
                       "sha": cur.get("sha")}
                      if cur else {"present": False})
    doc["catalog"] = {**_ca.stamp(),
                      "warnings": sum(1 for p in _ca.problems()
                                      if p.severity != "error")}
    doc["bindings"] = _ca.bindings_summary()
    # Служба каталога (шаг 5, §6.2 п.1): адрес для ссылок экрана. Без
    # адреса ссылки не строятся, и экран говорит об этом словами — а не
    # ведёт в никуда.
    doc["catalog_service"] = {"url": catalog_pipeline.configured()["ui_url"]}
    doc["requests"] = requests_summary(cat)
    return doc


def requests_summary(cat) -> dict:
    """Сводка заявок каталога для экрана (шаг 6): сколько открыто и по
    каким состояниям, у скольких записан ключ CR.

    Считается ТОЛЬКО по каталогу — очередь CR здесь не читается: ключ CR
    у заявки и есть след строки очереди (закон `cr_key_of`), и один
    источник правды честнее двух, способных разойтись."""
    states: dict = {}
    keys = 0
    for rq in cat.requests():
        if not isinstance(rq, dict):
            continue
        states[str(rq.get("state"))] = states.get(str(rq.get("state")), 0) + 1
        if rq.get("cr_key"):
            keys += 1
    open_n = sum(n for st, n in states.items()
                 if st in ("подана", "принята", "исполнитель заявлен"))
    return {"open": open_n, "states": states, "cr_keys": keys}


def record_link(section: str, record_id: str | None) -> str | None:
    """Адрес карточки записи в интерфейсе службы каталога (§6.1 шага 5);
    служба не настроена (`CREW_CATALOG_URL` пуст) — None."""
    from webapp import catalog_pipeline
    ui = catalog_pipeline.configured()["ui_url"]
    if not ui or not record_id:
        return None
    return f"{ui.rstrip('/')}/ui/#/records/{section}/{record_id}"


def change_refusal(section: str = "families",
                   record_id: str | None = None) -> dict:
    """Честный отказ прежней ручки заявки на правку/снятие: одни слова
    для ручки, экрана и руководства (шаг 6, решение 0.8)."""
    link = record_link(section, record_id)
    return {
        "error": ("правка и снятие записи каталога — на экране службы "
                  "каталога: правка становится черновиком аналитика, "
                  "снятие ставит статус «снята» словами владельца; "
                  "заявка каталога у существующей записи бывает только "
                  "на вид исполнителя"),
        "link": link,
        "dependents": [],
    }
