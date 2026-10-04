# -*- coding: utf-8 -*-
"""Слепой читатель шестой проверки: компиляция машинных проверок
решения ИЗ КЛАУЗ ЗАМОРОЖЕННЫХ ДОКУМЕНТОВ (vmp/source_check исполняет
числа детерминированно).

Независимость (класс протокола независимости решателей BA): промпт
не содержит ни ВМП, ни словарей и промптов конвейера формализации —
только тексты клауз с якорями и ЗАКРЫТЫЙ словарь видов проверок.
Маршрутизация модели — llm_scope контура данных (local-only данные
заказчика никогда не уходят в облако); точка вызова объявлена в
реестре ИИ-контура (llm_call_registry, страж-тест)."""
from __future__ import annotations

import json
import re
from decimal import Decimal

from vmp.source_check import CHECK_KINDS, SOFT_EVENTS

#: Версия компилятора проверок — часть ключа кэша скомпилированных
#: проверок (vmp.source_check_run._compile_key): правка промпта или
#: словаря видов обязана обнулять старые компиляции (стык S06 плана
#: единой версии 06.09.2026, §3.9 п.2). Меняется рукой при правке
#: _SYSTEM / _KIND_CONTRACT / CHECK_KINDS; отпечаток текста промпта
#: и словаря считается сам (compiler_fingerprint).
COMPILER_VERSION = "2026-09-22.2"

#: Изменение машинного семантического рубежа тоже меняет ключ кэша:
#: прежнее успешное JSON-прочтение не является доказательством смысла.
SOFT_EVENT_GUARD_VERSION = "source-domain-units/2"

#: Каналы читателя, объявляемые вызывающим: «cli» — агентный CLI
#: подписки с --model (ключ API на выбор не влияет), «api» — Messages
#: API точной моделью (без ухода в CLI), «local» — локальная модель
#: установки; None — прежний путь llm_backend.complete (канал не
#: наблюдается). Явно выбранный канал не подменяется другим при
#: отказе (S06: fallback запрещён).
READER_CHANNELS = ("cli", "api", "local")

#: Контракт параметров видов — часть слепого промпта.
_KIND_CONTRACT = """
- duty_span_max {limit_min:int, rest_split_min:int} — каждая рабочая
  смена не длиннее limit_min минут; rest_split_min — минимальный
  разрыв (минуты), который считается отдыхом между сменами.
- rest_min {home_min:int, outstation_min:int} — отдых между сменами
  не короче пола: дома home_min, вне базы outstation_min (минуты).
- turnaround_min {min_min:int, rest_split_min:int} — стыковка между
  рейсами внутри смены не короче min_min минут.
- landings_per_duty_max {limit:int, rest_split_min:int} — посадок за
  смену не больше limit.
- crew_size_exact {n:int} — на каждом рейсе ровно n различных членов
  экипажа.
- skill_required {} — каждое место рейса закрыто сотрудником с
  требуемой ролью.
- unavailability {} — нет назначений в недоступные дни сотрудника
  (отпуска и т.п.).
- type_rating {} — сотрудник допущен к типу судна рейса.
- pairing_span_max_days {limit_days:int} — размах цепочки
  (командировки) не больше limit_days календарных дней.
- reserve_daily_min {per_base:{КОД_БАЗЫ:int,...}} — каждый день в
  каждой базе не меньше нормы свободных (незанятых и доступных)
  людей.
- soft_event_count {event:%s, days:[дни недели англ.]?,
  threshold_min:int?, rest_split_min:int?} — МЯГКОЕ правило (штраф,
  предпочтение): жёсткой проверке не подлежит, считается только
  ФАКТ события.
  days — только полные значения: monday, tuesday, wednesday,
  thursday, friday, saturday, sunday; сокращения, номера и русские
  названия недопустимы. Укажи ровно дни исходной клаузы. Заголовок
  «конец недели» не расширяет явно названные пятницу и субботу
  до воскресенья. Для layover_nights_in_window days обязателен;
  для layover_nights календарного окна нет.
- not_machine_checkable {reason:str} — правило машинно не
  проверяется по готовому расписанию (нужны отсутствующие данные,
  процедура согласия, оговорка «не установлено» и т.п.) — назови
  причину.
""" % (SOFT_EVENTS,)

_ANCHOR_RULE = """

ЖЁСТКОЕ ПРАВИЛО ИДЕНТИФИКАТОРОВ: якорь (anchor) и sid копируй
БАЙТ-В-БАЙТ из выданных клауз; выдумывать якоря или дополнять их
суффиксами и пометками запрещено — проверка с несуществующим якорем
отбраковывается машинной сверкой."""

_SYSTEM = """Ты — независимый проверяющий готовых экипажных
расписаний. Тебе даны ВЫДЕРЖКИ (клаузы) исходных документов
авиакомпании, каждая с якорем. Твоя задача — составить машинные
проверки готового решения по ЗАКРЫТОМУ словарю видов ниже. Ты не
знаешь и не должен знать, как эти документы читали другие системы:
суди только по тексту клауз.

Правила работы:
1. Каждую клаузу-НОРМУ планирования преврати в одну или несколько
   проверок словаря; anchor проверки = якорь клаузы.
2. Числа бери ТОЛЬКО из текста клауз; время переводи в минуты.
   Если действуют две нормы об одном и том же (например, документ и
   памятка расходятся) — сделай проверку на КАЖДУЮ с её якорем.
   БАЗОВЫЙ ПРЕДЕЛ С ПРОДЛЕНИЕМ: если одна клауза задаёт базовый
   предел смены, а другая допускает его превышение (продление) до
   потолка со штрафом или как предпочтение, то жёсткая проверка
   duty_span_max — ТОЛЬКО по потолку продления (limit_min = потолок,
   якорь клаузы потолка); базовый предел кодируй как soft_event_count
   {event: duty_over_threshold, threshold_min: база} с якорем клаузы
   базы — превышение базы законно и учитывается счётом, а не
   нарушением.
3. Мягкие правила (штраф, «нежелательно», предпочтение) — вид
   soft_event_count, а если факт события по словарю не считается —
   not_machine_checkable с причиной «мягкое: …».
   duty_over_threshold считает длительность ОДНОЙ смены в минутах,
   не кредитные часы, месячную нагрузку или разброс между людьми.
   Кредитный час не переводится в минуты смены. Для таких показателей
   в словаре нет события — назови отсутствие машинной проверки.
4. Правило, не ложащееся в словарь, или требующее данных, которых в
   расписании нет, — not_machine_checkable с внятной причиной.
   Заголовки, определения, справочные строки — пропускай молча.
5. Ответ — ТОЛЬКО JSON-массив объектов
   {"id": "...", "kind": "...", "anchor": "...", "params": {...},
    "note": "краткое основание из текста"}
   без каких-либо пояснений вокруг.

Словарь видов и их параметров:
""" + _KIND_CONTRACT + _ANCHOR_RULE


def clauses_prompt(clauses: list[dict]) -> str:
    lines = ["Клаузы документов авиакомпании:"]
    for c in clauses:
        anchor = c.get("anchor") or "?"
        sid = c.get("sid") or "?"
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{sid}:{anchor}] {text}")
    lines.append("")
    lines.append("Составь проверки по словарю. Только JSON-массив.")
    return "\n".join(lines)


def _extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise ValueError("в ответе читателя нет JSON-массива")
    return json.loads(m.group(0))


def _soft_source_text(row: dict, clauses: list[dict]) -> str | None:
    """Только исходная клауза, не note/id ответа и не кандидат ВМП.

    Промпт печатает [sid:anchor]; принимается и эта точная запись,
    и прежние sid:anchor / голый anchor. Только один исходный носитель
    может совпасть; скобки не снимаются эвристически, sid не игнорируется.
    Неизвестный/неоднозначный якорь не даёт положительного факта.
    """
    anchor = str(row.get("anchor") or "")
    sid = row.get("sid")
    matches = [c for c in clauses
               if (sid is None or str(c.get("sid")) == str(sid))
               and anchor in (str(c.get("anchor") or ""),
                              f"{c.get('sid')}:{c.get('anchor')}",
                              f"[{c.get('sid')}:{c.get('anchor')}]")]
    if not anchor or len(matches) != 1:
        return None
    return str(matches[0].get("text") or "").casefold().replace("ё", "е")


_DUTY_WORD = r"\b(?:смен\w*|дежурств\w*|duty)\b"
_CREDIT_WORD = r"\b(?:кредит\w*|зачетн\w*|credit\w*)\b"
_AGGREGATE_WORD = (r"\b(?:разброс\w*|равномер\w*|дисперси\w*|spread|"
                   r"variance|deviation|месяц\w*|месячн\w*|month\w*|"
                   r"недельн\w*|weekly|годов\w*|annual\w*)\b")
_NUMBER = r"(?<![\w:.,+\-])\d+(?:[.,]\d+)?"
_HOUR = r"(?:час(?:а|ов)?|ч|hours?|hrs?|h)\b\.?"
_MINUTE = r"(?:мин(?:ут[а-я]*)?|minutes?|mins?)\b\.?"
_DAY_WORDS = {
    "monday": r"понедельник\w*|monday",
    "tuesday": r"вторник\w*|tuesday",
    "wednesday": r"сред[ауеы]|wednesday",
    "thursday": r"четверг\w*|thursday",
    "friday": r"пятниц\w*|friday",
    "saturday": r"суббот\w*|saturday",
    "sunday": r"воскресень\w*|sunday",
}


def _source_durations(text: str) -> set[Decimal]:
    """Явные длительности: HH:MM, минуты, часы (в т.ч. дробные).

    Это лишь контроль единиц/числа после контроля области; вес штрафа
    без единицы времени сюда не попадает. Неизвестная запись — отказ,
    не догадка о величине и не новый толкователь норм.
    """
    values = set()
    for hour, minute in re.findall(r"(?<![\w:+\-])([0-9]+):([0-5][0-9])(?![\w:])",
                                   text):
        values.add(Decimal(hour) * 60 + Decimal(minute))
    hour_spans = []
    for match in re.finditer(
            rf"({_NUMBER})\s*{_HOUR}(?:\s*({_NUMBER})\s*{_MINUTE})?", text):
        hour, minute = match.groups()
        hour_spans.append(match.span())
        values.add(Decimal(hour.replace(",", ".")) * 60
                   + Decimal((minute or "0").replace(",", ".")))
    for match in re.finditer(rf"({_NUMBER})\s*{_MINUTE}", text):
        if not any(start <= match.start() < end for start, end in hour_spans):
            values.add(Decimal(match[1].replace(",", ".")))
    return values


def _soft_event_problem(row: dict, clauses: list[dict]) -> str | None:
    """Консервативный fail-closed рубеж закрытого словаря событий.

    Не доказывает произвольную эквивалентность естественного языка.
    Неузнанная область/единицы остаются явно непроверяемыми. ВМП,
    каталог шаблонов и решение решателя здесь не читаются.
    """
    text = _soft_source_text(row, clauses)
    if text is None:
        return "исходная клауза события не определена однозначным якорем"
    params = row.get("params")
    if not isinstance(params, dict) or params.get("event") not in SOFT_EVENTS:
        return "событие или его параметры вне закрытого словаря"
    if re.search(_CREDIT_WORD, text):
        return ("кредитные часы/их разброс не являются длительностью смены "
                "или числом ночевок; события для этой метрики в словаре нет")
    if re.search(_AGGREGATE_WORD, text):
        return "агрегатная/периодная метрика не является событием словаря"
    event = params["event"]
    if event == "duty_over_threshold":
        if not re.search(_DUTY_WORD, text):
            return "область клаузы не подтверждает длительность одной смены"
        threshold = params.get("threshold_min")
        if type(threshold) is not int or threshold < 0:
            return "порог длительности смены не задан целыми минутами"
        # Число должно принадлежать фразе о смене, не соседней фразе
        # о штрафе/отдыхе. Точка в десятичном числе фразу не разрезает.
        phrases = re.split(r"[;\n!?]|(?<!\d)\.|\.(?!\d)", text)
        durations = set().union(*(_source_durations(p) for p in phrases
                                 if re.search(_DUTY_WORD, p)))
        if Decimal(threshold) not in durations:
            return "порог в минутах не подтвержден исходной фразой о смене"
        return None
    if not (re.search(r"\b(?:ночев\w*|layover\s+nights?|overnights?)\b", text)
            and re.search(r"вне\s+(?:\w+\s+){0,2}баз\w*|"
                          r"(?:away\s+from|outside)\s+(?:home\s+)?base|"
                          r"\boutstation\b", text)):
        return "клауза не подтверждает событие ночевки вне базы"
    days = {day for day, words in _DAY_WORDS.items()
            if re.search(rf"\b(?:{words})\b", text)}
    if re.search(r"\b(?:weekends?|выходн\w*)\b", text):
        days.update(("saturday", "sunday"))
    if event == "layover_nights":
        if days or params.get("days"):
            return "календарное окно нельзя подменять счетом всех ночевок"
    else:
        requested = params.get("days")
        if (not isinstance(requested, list) or not requested
                or any(not isinstance(d, str) for d in requested)
                or {d.casefold() for d in requested} != days):
            return "дни события не подтверждены календарным окном клаузы"
    return None


def compiler_fingerprint() -> str:
    """Отпечаток компилятора для ключа кэша: версия + текст системного
    промпта + закрытый словарь видов + версия семантического рубежа.
    Правка контракта — новое прочтение, старый кэш не удостоверяет."""
    import hashlib
    h = hashlib.sha256()
    h.update(str(COMPILER_VERSION).encode("utf-8"))
    h.update(b"\n")
    h.update(SOFT_EVENT_GUARD_VERSION.encode("utf-8"))
    h.update(b"\n")
    h.update(_SYSTEM.encode("utf-8"))
    h.update(b"\n")
    h.update(json.dumps(list(CHECK_KINDS), ensure_ascii=False,
                        sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def compile_checks(clauses: list[dict], scope: str,
                   requested: str | None = None,
                   model: str | None = None,
                   timeout_s: float = 300.0,
                   effort: str = "medium",
                   channel: str | None = None) -> dict:
    """Скомпилировать проверки слепым читателем. Возврат:
    {specs, problems, backend, model, channel, raw_len}; кривой
    ответ — ValueError (отказ словами, не молчаливый пропуск).

    channel — канал читателя, объявленный вызывающим (S06 плана
    единой версии, 06.09.2026): «cli» — ровно эта модель через
    агентный CLI подписки (наличие ключа API канал НЕ меняет —
    llm_backend.complete при ключе ушёл бы в API молча); «api» —
    Messages API той же моделью без ухода в CLI; «local» —
    локальная модель установки; None — прежний путь complete (канал
    не наблюдается, в возврате channel=None). Неполная идентичность
    (cli/api без облачного контура или без модели, api без ключа,
    канал вне словаря) — ValueError ДО транспорта; отказ канала —
    ValueError с именем канала и модели, без повтора другим каналом
    или моделью. Допуск контура (ScopeViolation) судится раньше
    любого транспорта.

    effort обязателен по классу (живой урок пилота 2026-08-31):
    модели пятого поколения размышляют ПО УМОЛЧАНИЮ, и без явного
    бюджета блок размышлений съедает весь max_tokens — ответ
    приходит пустым (stop_reason=max_tokens, только thinking-блок).
    Явный effort включает механику budget+total трубы: текст
    гарантированно отделён от бюджета размышлений. Канал cli
    effort/max_tokens не принимает (оговорка аудита GPT).

    В возврате есть `usage` — расход этого вызова читателя в токенах
    (решение владельца № 1 от 10.09.2026); канал cli токенов не отдаёт,
    и там стоит `counted: false` с причиной словами."""
    from webapp import llm_backend
    backend = llm_backend.resolve_backend(scope, requested)
    llm_backend.take_last_usage()          # несняток чужого вызова — прочь
    if channel not in (None, *READER_CHANNELS):
        raise ValueError(
            f"канал читателя {channel!r} вне словаря "
            f"({'|'.join(READER_CHANNELS)})")
    if channel in ("cli", "api") and (backend != "cloud" or not model):
        raise ValueError(
            f"канал {channel} требует облачный контур и явную модель "
            f"(контур {backend}, модель {model!r}) — читатель не зовётся")
    if channel == "local" and backend != "local":
        raise ValueError(
            f"канал local объявлен, а контур разрешён как {backend} — "
            f"читатель не зовётся")
    prompt = clauses_prompt(clauses)
    if channel == "cli":
        # Явный CLI: точная модель флагом --model; ключ API на выбор
        # не влияет; после отказа — никакого другого канала/модели.
        try:
            text = llm_backend._cli_generate(
                _SYSTEM + "\n\n" + prompt, timeout_s, model=model)
        except llm_backend.LLMError as exc:
            raise ValueError(
                f"читатель не ответил (cloud/{model}/cli): {exc}") from exc
        got = {"ok": True, "text": text, "backend": backend,
               "model": model, "channel": "cli"}
    elif channel == "api":
        try:
            has_key = bool(llm_backend._settings.has_api_key())
        except Exception:                                  # noqa: BLE001
            has_key = False
        if not has_key:
            raise ValueError(
                f"канал api ({model}): ключ API облака не задан в "
                f"настройках COSMOS — читатель не зовётся")
        try:
            text = llm_backend._api_generate(
                prompt, model=model, timeout_s=timeout_s,
                system=_SYSTEM, effort=effort, max_tokens=8000)
        except llm_backend.LLMError as exc:
            raise ValueError(
                f"читатель не ответил (cloud/{model}/api): {exc}") from exc
        got = {"ok": True, "text": text, "backend": backend,
               "model": model, "channel": "api"}
    else:
        got = llm_backend.complete(
            prompt, backend=backend, system=_SYSTEM,
            usage="source_check", model=model, timeout_s=timeout_s,
            max_tokens=8000, effort=effort)
        got = dict(got or {})
        got["channel"] = channel      # None либо явное «local»
    # Расход вызова читателя: конверт complete его уже несёт, у явных
    # каналов cli/api он снимается с трубы (та кладёт его на каждый
    # вызов). Один вызов — одна строка расхода.
    got.setdefault("usage", llm_backend.peek_last_usage())
    if not got.get("ok"):
        raise ValueError(
            f"читатель не ответил ({got.get('backend')}/"
            f"{got.get('model')}"
            + (f"/{got.get('channel')}" if got.get("channel") else "")
            + f"): {got.get('error')}")
    rows = _extract_json_array(got.get("text") or "")
    specs: list[dict] = []
    problems: list[str] = []
    seen_ids: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"[{i}] не объект")
            continue
        kind = row.get("kind")
        if kind not in CHECK_KINDS:
            problems.append(f"[{i}] вид {kind!r} вне словаря")
            continue
        cid = str(row.get("id") or f"src-{i}")
        if cid in seen_ids:
            cid = f"{cid}-{i}"
        seen_ids.add(cid)
        spec = {"id": cid, "kind": kind,
                "anchor": str(row.get("anchor") or ""),
                "params": row.get("params") or {},
                "note": str(row.get("note") or "")}
        if kind == "soft_event_count":
            problem = _soft_event_problem(row, clauses)
            if problem:
                reason = "мягкое: семантика события не подтверждена — " + problem
                spec.update(kind="not_machine_checkable",
                            params={"reason": reason}, reason=reason,
                            reader_candidate=row)
                problems.append(f"[{i}] {reason}")
        if kind == "not_machine_checkable":
            spec["reason"] = (str(row.get("params", {})
                                  .get("reason") or "")
                              or str(row.get("reason") or "")
                              or spec["note"] or "причина не названа")
        specs.append(spec)
    if not specs:
        raise ValueError(
            "читатель не дал ни одной проверки словаря; проблемы: "
            + "; ".join(problems))
    return {"specs": specs, "problems": problems,
            "backend": got.get("backend"), "model": got.get("model"),
            "channel": got.get("channel"), "usage": got.get("usage"),
            "raw_len": len(got.get("text") or "")}
