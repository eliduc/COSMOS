"""Контур «Отклик планировщика» (2026-07-20).

Ручные переделки доски ростера уже сохраняются (roster_board: кто, какой
пейринг, какое место, с кого на кого) — но БЕЗ ПРИЧИНЫ. Правка без причины
как сигнал бесполезна: по ней нельзя отличить «в модели не хватает
правила» от «оператору так привычнее». Этот модуль хранит эпизоды правок
ВМЕСТЕ с указанной причиной и детерминированно ищет ПОВТОРЯЮЩИЕСЯ —
повтор одной и той же переделки с одной и той же причиной и есть
единственное, что здесь считается сигналом.

ДЕТЕРМИНИЗМ — жёсткое требование пакета. Ни одного вызова языковой
модели: эпизоды содержат ФИО и табельные номера (внутренний контур
Prod/Stage, облако запрещено). Кластеризация — по ТОЧНЫМ ключам
(причина + сотрудник либо причина + пейринг), а не по смыслу текста;
свободный комментарий переносится в выборку как есть и НИКОГДА не
участвует в группировке.

Модуль НЕ пишет в рабочие настройки. Единственный его выход наружу —
build_crs(): список ЧЕРНОВИКОВ предложений (CR со статусом draft), которые
регистрирует вызывающий. Решение остаётся за администратором.

Дисциплина хранилища зеркалит ops_log.py: модульный замок, атомарная
запись tmp->replace с ретраем PermissionError под Windows и ЗАЩИТНОЕ
чтение — битый файл деградирует в «нет эпизодов», а не роняет 500.
Хранилище — runtime-состояние в webapp/config/override_log.json.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import registry

OVERRIDE_PATH = registry.REPO_ROOT / "webapp" / "config" / "override_log.json"

# Коды причин переделки. Набор закрытый и УЗКИЙ намеренно: длинный список
# заставляет оператора выбирать наугад, и кластеры расползаются.
REASONS = ("missing_rule", "wrong_cost", "crew_request", "ops_decision",
           "data_error", "other")

REASON_LABELS = {
    "missing_rule": "Правило не учтено",
    "wrong_cost": "Цена или приоритет неверны",
    "crew_request": "Просьба сотрудника",
    "ops_decision": "Оперативное решение",
    "data_error": "Ошибка в данных",
    "other": "Иное",
}

# Потолок на экземпляр: журнал правок — сигнал, а не архив. Старые эпизоды
# обрезаются, иначе год работы одной авиакомпании раздувает файл, который
# читается целиком на каждый запрос кластеров.
MAX_EPISODES = 5000
# ГЛОБАЛЬНЫЙ потолок (ревью волны 1): потолка на экземпляр мало — экземпляры
# заводятся свободно, и сотня экземпляров по 5000 эпизодов даёт полмиллиона
# строк в ОДНОМ файле, который целиком читается и целиком переписывается на
# КАЖДУЮ правку доски и на КАЖДУЮ загрузку «Контуров». Стоимость записи
# росла бы линейно от всей истории; с этим потолком она ограничена сверху
# независимо от того, сколько экземпляров и как давно работает стенд.
MAX_EPISODES_TOTAL = 20000
MAX_NOTE = 200

# Срезы кластеризации: по сотруднику (кого снимали/ставили) и по пейрингу.
FACETS = ("employee", "pairing", "systemic")

# Системный срез («сколько раз вообще выбрали эту причину») — улика
# СЛАБЕЕ адресных: он не указывает ни на человека, ни на пейринг, а лишь
# говорит «планировщики часто упираются в одно и то же». Поэтому порог у
# него кратно выше. Проверка на живом стенде (2026-07-20) показала, зачем
# он нужен: девять переделок с причиной «правило не учтено» по РАЗНЫМ
# пейрингам и людям не давали ни одного кластера — самый частый в жизни
# случай был невидим.
SYSTEMIC_FACTOR = 3

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    """inst_id -> список эпизодов; любое повреждение = пустой журнал.

    В отличие от ops_log здесь чтение НЕ обязано падать на битом файле:
    журнал откликов — вспомогательный сигнал, и его потеря не искажает ни
    план, ни факт. Молчаливая деградация в пусто предпочтительнее 500 на
    экране доски.
    """
    if not OVERRIDE_PATH.exists():
        return {}
    raw = ""
    for attempt in range(5):
        try:
            raw = OVERRIDE_PATH.read_text(encoding="utf-8")
            break
        except OSError:
            # шаринг-нарушение Windows во время чужого tmp->replace: это не
            # порча, а гонка — повторить
            if attempt == 4:
                return {}
            time.sleep(0.05)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, list)}


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(data: dict) -> None:
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, OVERRIDE_PATH)


def _clean_str(value, limit: int) -> str:
    return str(value if value is not None else "")[:limit]


def _enforce_global_cap(data: dict) -> None:
    """Ужать журнал ЦЕЛИКОМ до MAX_EPISODES_TOTAL. Мутирует data на месте.

    Режем у САМОГО БОЛЬШОГО экземпляра, а внутри него — самый старый эпизод
    (голову списка: порядок в списке = порядок записи). Не «самый старый по
    всему журналу» — по двум причинам. Во-первых, отметка времени секундная,
    и при пакете правок в одну секунду «самый старый» неоднозначен, а обрезка
    обязана быть детерминированной. Во-вторых, по возрасту один активный
    экземпляр вытеснил бы историю всех остальных, и адресные кластеры
    спокойных авиакомпаний перестали бы набираться — то есть потолок ломал
    бы ровно тот сигнал, ради которого журнал ведётся. Тай-брейк при равных
    размерах — имя экземпляра.

    Перебор идёт по числу ЭКЗЕМПЛЯРОВ на каждый вытесняемый эпизод, а
    вытесняется обычно один: стоимость записи ограничена сверху потолком и
    не растёт с накопленной историей.
    """
    sizes = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    excess = sum(sizes.values()) - MAX_EPISODES_TOTAL
    if excess <= 0:
        return
    live = [k for k, n in sizes.items() if n]
    for _ in range(excess):
        if not live:
            return
        fattest = min(live, key=lambda k: (-len(data[k]), k))
        data[fattest].pop(0)
        if not data[fattest]:
            live.remove(fattest)
            data.pop(fattest, None)


def record(inst_id: str, *, pid, slot, frm, to, reason_code, reason_text,
           by) -> dict:
    """Записать эпизод ручной переделки с причиной.

    Неизвестный reason_code МОЛЧА становится "other": запись факта важнее
    строгости. Правка уже произошла на доске — уронить журнал из-за кода
    причины значит потерять сигнал ради валидации.
    """
    code = reason_code if reason_code in REASONS else "other"
    episode = {
        "id": "ov" + secrets.token_hex(4),
        "inst": str(inst_id or ""),
        "pid": _clean_str(pid, 120),
        "slot": slot,
        "from": _clean_str(frm, 120) or None,
        "to": _clean_str(to, 120) or None,
        "reason": code,
        "note": _clean_str(reason_text, MAX_NOTE),
        "by": _clean_str(by, 200) or None,
        "at": _now(),
    }
    key = episode["inst"]
    with _lock:
        data = _load()
        rows = data.get(key)
        if not isinstance(rows, list):
            rows = []
        rows.append(episode)
        # обрезаем СТАРЫЕ: свежие переделки и есть актуальный сигнал
        data[key] = rows[-MAX_EPISODES:]
        _enforce_global_cap(data)
        _save_locked(data)
    return episode


def episodes(inst_id: str | None = None) -> list[dict]:
    """Эпизоды одного экземпляра либо всех (защитно: мусорные строки
    отбрасываются, не роняя вызывающего)."""
    data = _load()
    keys = [str(inst_id)] if inst_id is not None else sorted(data)
    out: list[dict] = []
    for k in keys:
        for row in data.get(k) or []:
            if isinstance(row, dict):
                out.append(row)
    return out


def _facet_values(ep: dict, facet: str) -> list[str]:
    """Значения признака для одного эпизода.

    Для среза "employee" эпизод даёт ДВА значения — с кого сняли и кого
    поставили: повторяющийся отклик может быть «этого всегда убирают» и
    «этого всегда ставят», и оба одинаково интересны. Эпизод попадёт в оба
    кластера — это осознанно, кластеры не разбиение, а срезы.
    """
    if facet == "employee":
        vals = [v for v in (ep.get("from"), ep.get("to")) if v]
        # снятие и постановка одного и того же человека — один голос
        return sorted(set(str(v) for v in vals))
    if facet == "systemic":
        # один общий ключ на причину: срез «эту причину выбирают часто»,
        # безотносительно того, кого и где переставляли
        return [""]
    return [str(ep.get("pid") or "")] if ep.get("pid") else []


# Метка межэкземплярного среза в ключе кластера. Адресные срезы несут в этом
# поле конкретный экземпляр, системный — «*».
ANY_INSTANCE = "*"


def _facet_scope(ep: dict, facet: str) -> str:
    """Экземплярная область ключа кластера.

    Адресные срезы (employee, pairing) ОБЯЗАНЫ включать экземпляр: табельные
    (EMP001) и коды пейрингов (C_000001) генерируются одинаково в каждом
    экземпляре и совпадают сплошь и рядом. Без экземпляра в ключе три
    несвязанные переделки трёх РАЗНЫХ авиакомпаний склеивались в один
    «кластер» и порождали ложный CR — сигнала за ним нет никакого.

    Системный срез оставлен МЕЖЭКЗЕМПЛЯРНЫМ осознанно: он отвечает не «этого
    человека всё время переставляют», а «планировщики вообще часто упираются
    в эту причину», и такой вопрос имеет смысл ровно поверх всех экземпляров
    сразу. Чтобы это не выглядело недосмотром, область стоит в самом ключе
    (instance == ANY_INSTANCE) и проговаривается в тексте CR.
    """
    return ANY_INSTANCE if facet == "systemic" else str(ep.get("inst") or "")


def clusters(inst_id: str | None = None, min_size: int = 3) -> list[dict]:
    """Повторяющиеся переделки: группировка по ТОЧНОМУ ключу.

    Ключ — (причина, признак, ЭКЗЕМПЛЯР, значение) по трём срезам:
    причина+сотрудник, причина+пейринг и системный. Никакого сравнения по
    смыслу: одинаковыми считаются только буквально совпавшие ключи.
    Сортировка по размеру убыв., затем по самому ключу — при равных размерах
    порядок обязан быть устойчивым между запусками (иначе тот же журнал даёт
    разные черновики CR).
    """
    groups: dict[tuple, list[dict]] = {}
    for ep in episodes(inst_id):
        reason = ep.get("reason")
        if reason not in REASONS:
            reason = "other"
        for facet in FACETS:
            scope = _facet_scope(ep, facet)
            for value in _facet_values(ep, facet):
                groups.setdefault((reason, facet, scope, value),
                                  []).append(ep)

    out = []
    for (reason, facet, scope, value), eps in groups.items():
        need = (min_size * SYSTEMIC_FACTOR if facet == "systemic"
                else min_size)
        if len(eps) < need:
            continue
        out.append({
            "key": {"reason": reason, "facet": facet, "instance": scope,
                    "value": value},
            "reason": reason,
            "size": len(eps),
            "sample": eps[:3],
            "instances": sorted({str(e.get("inst") or "") for e in eps}),
        })
    out.sort(key=lambda c: (-c["size"], c["key"]["reason"],
                            c["key"]["facet"], c["key"]["instance"],
                            c["key"]["value"]))
    return out


MAX_SUMMARY = 600

# Табельные номера и коды пейрингов коротки; но record() принимает до 120
# символов, а такое имя раздувает шаблон summary за лимит в 600 и хвостовой
# срез снёс бы оговорку об ошибке оператора. Режем ЗДЕСЬ, в тексте.
MAX_FACET_VALUE = 32


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _facet_phrase(cluster: dict, budget: int = MAX_SUMMARY) -> str:
    """Место кластера словами, ужатое до budget символов.

    Срез стал экземплярным (см. _facet_scope), поэтому адресная фраза несёт
    ДВА длинных поля — значение и экземпляр; общий budget сверху обязателен,
    иначе разросшаяся фраза съедала бы конец шаблона вместе с оговоркой об
    ошибке оператора.
    """
    if cluster["key"]["facet"] == "systemic":
        # межэкземплярность системного среза проговариваем прямо в тексте:
        # читатель CR обязан видеть, что счёт шёл поверх всех экземпляров, а
        # не внутри одного (в отличие от адресных срезов ниже)
        return _clip("по разным пейрингам и людям, СЧЁТ ПОВЕРХ ВСЕХ "
                     "ЭКЗЕМПЛЯРОВ", budget)
    value = _clip(cluster["key"]["value"], MAX_FACET_VALUE)
    # адресные срезы всегда живут внутри одного экземпляра — он и назван
    inst = _clip(cluster["key"].get("instance") or "—", MAX_FACET_VALUE)
    if cluster["key"]["facet"] == "employee":
        return _clip(f"вокруг сотрудника {value} в экземпляре {inst}", budget)
    return _clip(f"по пейрингу {value} в экземпляре {inst}", budget)

# Разбор причин с оговоркой об ошибке оператора — САМА СУТЬ черновика, и он
# обязан уцелеть при любых данных. Поэтому summary собирается из шаблона:
# сначала место занимает разбор, а перечень экземпляров ужимается под
# остаток лимита (а не наоборот, когда длинное имя экземпляра срезало бы
# оговорку хвостовым [:600]).
_SUMMARY_TMPL = (
    "Ручная переделка повторилась {size} раз {facet}; причина везде "
    "«{label}». Экземпляры: {inst}. Объяснений три, и журнал их не "
    "различает: (1) в модели не хватает правила, которое планировщик держит "
    "в голове; (2) правило есть, но цена или приоритет заданы неверно; "
    "(3) правило и цена верны, а неудобен интерфейс, и переделка обходит "
    "неудачный шаг работы. Повтор НЕ доказывает правоту оператора: его "
    "решение тоже может быть ошибкой или привычкой — тогда верен разбор с "
    "планировщиком, а не правка настроек."
)


def _instances_phrase(cluster: dict, budget: int) -> str:
    """Экземпляры кластера в текст, ужатый до budget символов."""
    names = [n for n in cluster["instances"] if n]
    if not names or budget <= 1:
        return "—"
    text = (", ".join(names[:4]) + (f" и ещё {len(names) - 4}"
                                    if len(names) > 4 else ""))
    if len(text) <= budget:
        return text
    return text[:budget - 1] + "…"


def build_crs(min_size: int = 5) -> list[dict]:
    """Черновики предложений по устойчивым кластерам переделок.

    Возвращает список строк CR; САМ ничего не регистрирует и не пишет в
    рабочие настройки — это делает вызывающий. Порог здесь выше порога
    показа кластеров: показать оператору стоит и слабый повтор, а тревожить
    очередь изменений — только устойчивый.

    Формулировка summary намеренно НЕ утверждает, что прав оператор:
    повтор ручной правки — это вопрос, а не диагноз.
    """
    rows: list[dict] = []
    for cluster in clusters(min_size=min_size):
        size = cluster["size"]
        label = REASON_LABELS.get(cluster["reason"], REASON_LABELS["other"])
        title = (f"Отклик планировщика: {size} раз одна и та же переделка "
                 f"(причина: {label.lower()})")
        # Бюджет раздаётся сверху вниз, и оговорка получает его ПЕРВОЙ:
        # сначала неизменяемый каркас шаблона, потом место кластера, и лишь
        # остаток — перечню экземпляров. Обратный порядок при длинных именах
        # давал summary >600, и хвостовой срез отрезал именно оговорку об
        # ошибке оператора — то единственное, ради чего этот текст пишется.
        skeleton = len(_SUMMARY_TMPL.format(size=size, facet="", label=label,
                                            inst=""))
        facet = _facet_phrase(cluster, max(1, MAX_SUMMARY - skeleton - 1))
        fixed = _SUMMARY_TMPL.format(size=size, facet=facet, label=label,
                                     inst="")
        summary = _SUMMARY_TMPL.format(
            size=size, facet=facet, label=label,
            inst=_instances_phrase(cluster, MAX_SUMMARY - len(fixed)))
        now = _now()
        rows.append({
            "id": "cr" + secrets.token_hex(4),
            "agent": "override",
            "title": title[:90],
            "summary": summary[:600],
            "priority": min(10, size),
            "effort": 5,
            "status": "draft",
            "source": None,
            "country": None,
            "created_at": now,
            "updated_at": now,
        })
    return rows
