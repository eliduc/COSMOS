# -*- coding: utf-8 -*-
"""Извлечение и сведение правил CDPM (пакет работ П-3).

Конвейер: комплект заморозки (клаузы с якорями, пакет П-2) → кандидаты
правил и вопросов от языковой модели → детерминированная сборка машинной
модели (контракт П-1) → рендеры свода и анкеты → гейты.

Разделение ответственности — принципиальное:

- языковая модель ПРЕДЛАГАЕТ кандидатов (правила, развилки чтения,
  вопросы постановки, противоречия) и ничего не решает;
- сборка — чистый код: стабильные идентификаторы из якорей источника,
  склейка вопросов-дублей, блокировки, автопривязка SOFT-правил к
  вопросу о целевой функции; кандидат, нарушивший контракт (якорь вне
  комплекта, развилка без ветвей, часть вне перечня), ОТБРАКОВЫВАЕТСЯ с
  названной причиной и попадает в отчёт отбраковки — молча не исчезает;
- собранная модель обязана пройти инварианты П-1 до записи на диск —
  иначе отказ (fail-closed), а не «почти корректный» артефакт.

Уроки, зашитые в промпт и сборку: спорное чтение — развилка, не выбор
(У-07); императив не понижается до SOFT; разрешение «можно X» не
превращается в SOFT без вопроса (урок B2); выводимое из источника не
переспрашивается (урок B4); ветвь результата — на каждый ответ (B7);
веса целевой функции — только из ответов.

Запуск отдельно от приложения:
    python -m cdpm.extract КОМПЛЕКТ_ЗАМОРОЗКИ --out-dir КАТАЛОГ \
        --customer "Название авиакомпании" [--scope any|local-only]
"""
from __future__ import annotations

import hashlib
import argparse
import copy
import json
import re
import sys
from pathlib import Path

from cdpm import contradiction as contradiction_mod
from cdpm import family_params as family_params_mod
from cdpm import llm as llm_mod
from cdpm import model as m
from cdpm import render

CDPM_VERSION = "0.1"
CHUNK_SIZE = 40          # клауз в одном запросе к модели
CONTRA_CHUNK = 80        # правил в одном запросе поиска противоречий
# Приписка к вопросу, понижённому замком части 1 в часть 2: читатель
# анкеты обязан видеть, что перед ним уточнение, а не спор источников.
_CONTRA_DOWNGRADE_NOTE = ("Расхождение значений машинно не подтверждено "
                          "— уточнение, не противоречие.")

#: Жёсткое требование к дословным цитатам задания извлечения (по образцу
#: `model.ID_IMMUTABILITY_RULE`). Живая находка кругов 10.09.2026
#: (run-0143 и run-0035 на Test): модель склеивала в `as_written` две
#: фразы одной клаузы, и гейт выпуска панели честно отказывал
#: измерителем — цитата-носитель обязана быть подстрокой клаузы.
_AS_WRITTEN_RULE = """

ЖЁСТКОЕ ПРАВИЛО ДОСЛОВНЫХ ЦИТАТ (нарушение = машинное значение
отбраковывается сверкой с клаузой, и правило остаётся без веса,
критерия, ставки или параметра): всякая цитата — as_written веса,
критерия и ставки, quote параметра посадки — есть ДОСЛОВНАЯ ПОДСТРОКА
ОДНОЙ клаузы-источника, скопированная подряд. ЗАПРЕЩЕНО склеивать две
фразы клаузы в одну цитату, брать слова из разных клауз, менять числа,
единицы, знаки препинания и порядок слов, пересказывать своими словами
и дополнять цитату пояснением. Нужного места клауза не содержит —
значения не выдумывай: поставь null, и его спросит анкета. Цитата из
двух разных мест ОДНОЙ клаузы законна только разделённой многоточием
(«…»), и каждый её фрагмент — тоже дословная подстрока."""


class ExtractError(ValueError):
    """Честный отказ извлечения: причина всегда названа."""


def empty_result_problem(doc: dict, coverage_report: dict) -> str | None:
    """Пустой результат не объясняет клаузы без вердикта покрытия.

    Это не классификатор нормативности и не замена замка содержания:
    используются только существующие строки/цели/вопросы и coverage.missing.
    """
    if any(doc.get(key) for key in ("rules", "objectives", "questions")):
        return None
    missing = coverage_report.get("missing") or []
    if not missing:
        return None
    addresses = ", ".join(f"{row['sid']}/{row['anchor']}" for row in missing)
    return ("пустой результат: нет правил, целевых функций и вопросов; "
            f"нет вердикта покрытия для клауз: {addresses}")


# ---------------------------------------------------------------------------
# Промпты (деловой русский; ответ — строго один JSON-объект)
# ---------------------------------------------------------------------------

_LOGIC_SCOPE_RULE = """

ТОЧНОСТЬ ОБЛАСТИ ЛОГИЧЕСКОГО ПРЕДИКАТА. Предикат выражает именно
свой машинный смысл; цитата as_written не сужает его область действия.
marked_unavailable означает общую отмеченную недоступность сотрудника
и НЕ имеет аргумента, задающего причину. Запрет только на дни отпуска,
болезни или иной отдельной причины нельзя заменить запретом на любые
дни недоступности. Не добавляй несуществующий аргумент причины и не
маскируй потерянный ограничитель цитатой или произвольным in_list.
Если точный ограничитель исходной нормы не выражается закрытой схемой,
верни null для logic. Исходное правило, его формулировка и след источника
сохраняются; отсутствие точной логики не снимает норму и не устраняет
реальный пробел C или потребность в новом шаблоне. Общая норма о любой
отмеченной недоступности может использовать marked_unavailable; null
нужен при невыразимости, а не просто при отсутствии числового параметра.
"""

_PROMPT_CANDIDATES = """Ты — формализатор правил авиакомпании для задач
пейринга (построение цепочек рейсов) и ростеринга (назначение экипажей).
Ниже — клаузы одного документа, каждая начинается якорем в квадратных
скобках. Верни СТРОГО один JSON-объект без пояснений:

{{"candidates": [{{
  "anchor": "<якорь клаузы-источника, ровно как в списке>",
  "title": "<короткое имя правила>",
  "statement": "<формальная запись правила>",
  "zone": "PAIRING" | "ROSTERING" | "BOTH",
  "kind": "HARD" | "SOFT" | "DEF" | "DATA" | "CASE" | "OUT" | "УСЛОВНЫЙ",
  "weight": null | {{"value": <целое ≥ 0>, "as_written": "<ДОСЛОВНЫЙ фрагмент клаузы с числом веса/штрафа>"}} — у SOFT, а также у УСЛОВНЫЙ с ветвью SOFT (вес вступит при выборе этой ветви); только если число веса (штрафа, надбавки) названо в самой клаузе; иначе null,
  "note": null | "<замечание к качеству источника: что в тексте и как прочитано>",
  "params": null | [{{"name": "<имя СТРОГО из ЗАКРЫТОГО СЛОВАРЯ ИМЁН (перечень в конце задания) — только при точном смысловом соответствии, иначе параметр опусти>",
    "value": <число: длительности и время суток — В МИНУТАХ (12:00 →
720), количества — целым>,
    "unit": "duration_minutes"|"time_of_day_minutes"|"count"|"hours_per_credit_hour"|"credit_hours_per_hour"|"credit_hours",
    "as_written": "<число ровно как в тексте правила, напр. 12:00>",
    "relation": "max"|"min"|"eq"|"window",
    "qualifier": null | "<уточнитель контекста, если правило задаёт
несколько значений одного имени: код базы у нормы резерва и т.п.;
для области действия ТОЛЬКО канонические слова «база» либо
«вне базы» (не «в базе», не название аэропорта)>",
    "tz": null | "UTC" | "base-local" (ОБЯЗАТЕЛЬНО при time_of_day)}}, …],
  "logic": null | {{"<машинная логика правила ПРОИЗВОЛЬНОГО вида
(условного/календарного/композитного) — ТОЛЬКО если правило не
сводится к одному порогу params. Словари закрытые, чужие имена
будут отброшены:
subject: assignment|flight|duty|reserve_duty|rest|sit|employee|base|day|month|rolling_window|day_off|qualification;
condition: список атомов {{predicate, аргументы, as_written=дословный
фрагмент формулировки}}; предикаты: starts_in(from_param,to_param),
longer_than(param), shorter_than(param), at_base, at_outstation,
consecutive(unit=day|duty, param), in_window(window),
follows(subject), has_qualification_for(type_value),
overlaps_in_time, marked_unavailable, in_list(allowed);
demand: {{kind: count_within(relation,param[,universe])|
duration_within(relation,param,measure) — measure ОБЯЗАТЕЛЕН:
span=размах смены | block=налёт|exists_person(subject)|
exists_universe(subject,universe)|not_exists(subject)|
combination_forbidden(subject), as_written}};
universe ТОЛЬКО из перечня: employee×month, employee×day,
base×day, employee×rolling_window; окно in_window — узлом
{{kind: month}} либо {{kind: rolling, unit: day, param}};
ЧИСЛА ТОЛЬКО ссылками на параметры этого же правила: именем-
строкой при ЕДИНСТВЕННОЙ записи имени, объектом
{{name, qualifier}} при нескольких записях (резерв по базам,
продление с согласия); число в требовании exists/not_exists
без параметра запрещено — используй count_within;
source_params: ПОЛНЫЙ перечень применённых ссылок — те же
строки/объекты, что стоят в condition/demand/window
(расхождение в любую сторону = блок отброшен); enforce:
required (правило обязано исполняться решателем) | post-check
(проверка после счёта — когда данных заведомо нет)>"}},
  "question": null | {{
    "part": "2" | "3a" | "3b",
    "title": "<короткое имя вопроса>",
    "text": "<вопрос авиакомпании>",
    "answer": {{"type": "choice"|"value"|"registry"|"text",
               "options": [{{"key": "а", "text": "…"}}, …],
               "note": "<единица или форма ответа>"}},
    "branches": {{"а": "SOFT", "б": "HARD", …}}
  }},
  "template_fit": null | {{
    "category": "A" | "B" | "C" | "D",
    "family": "<семья из ПЕРЕЧНЯ ШАБЛОНОВ БАЗЫ ниже — только A и B>",
    "params": {{"<имя из закрытого словаря, своей семьи>":
      {{"value": <число в единице словаря>,
        "quote": "<ДОСЛОВНАЯ цитата из текста клаузы>"}}, …}},
    "assumption": "<ровно одно явное допущение — только B>",
    "gap": "<какой механики не хватает — только C>",
    "candidates": ["<семья>", "<семья>", …] — только D, минимум две,
    "confidence": <целое 0-100>
  }}
}}, …],
 "objectives": [{{
  "anchor": "<якорь клаузы-источника>",
  "title": "<короткое имя целевой>",
  "statement": "<формальная запись метрики>",
  "zone": "PAIRING" | "ROSTERING" | "BOTH",
  "direction": "minimize" | "maximize",
  "metric": "<имя из закрытого словаря машинных метрик, ЕСЛИ смысл
целевой ему точно соответствует; иначе поле опусти:
total_pairing_cost (стоимость пейрингов), layover_nights_total
(ночи вне базы), credit_hours_spread (разброс кредитных часов),
positioning_cost (стоимость перегонов), uncovered_seats (непокрытые
места), preferred_days_off_violations (нарушения выходных)>",
  "criterion": null | {{"kind": "<машинный критерий из закрытого
словаря: MIN_PERSON_DAYS (минимум человеко-дней командировок) |
NORMALIZED_COST (нормированная стоимость по рангам)>",
    "rate_per_duty": <целое > 0 — ставка за календарный день (только
MIN_PERSON_DAYS)>,
    "rates": {{"flight": <число > 0>, "work": <число > 0>}} — ставки
рангов (только NORMALIZED_COST),
    "per": "<за что начисляется ставка — дословно из клаузы, например
«календарный день командировки»; в клаузе не названо — опусти>",
    "version": "<версия ставок>",
    "effective_from": "<дата начала действия ставок>",
    "as_written": "<ДОСЛОВНЫЙ фрагмент клаузы с критерием>"}} — только
если клауза САМА называет вид критерия, ставку (ставки), версию и дату
действия; чего-то из этого в клаузе нет — null (недостающее спросит
анкета, не выдумывай),
  "rate": null | {{"value": <число > 0>,
    "per": "<за что начисляется ставка — дословно из клаузы>",
    "as_written": "<ДОСЛОВНЫЙ фрагмент клаузы со ставкой>"}} — ставка
НЕмашинного критерия (когда criterion null): только если клауза САМА
называет ставку в валюте ВМП («1 условная единица за час разброса»);
иначе null
}}, …],
 "not_rule": [{{
  "anchor": "<якорь клаузы, не породившей ни кандидата, ни целевой>",
  "reason": "<заголовок | оглавление | шапка таблицы | преамбула |
мета-предложение о самом ВМП | пример | примечание | повтор уже
покрытой нормы | иное: почему клауза не несёт нормы планирования>"
}}, …]}}

Правила работы:
- каждый кандидат обязан ссылаться на якорь из списка; якорь пишется
  БЕЗ квадратных скобок (скобки в списке — только обрамление);
- КАЖДАЯ клауза списка получает вердикт: кандидат, целевая либо запись
  в not_rule с причиной; молчание по клаузе недопустимо — баланс
  потребления предъявит её как клаузу без вердикта. Клаузы с пометкой
  «(вердикт кода: …)» уже разобраны кодом — их в not_rule не пиши;
- предложения о САМОМ ВМП, а не о планировании (титул, валюта ставок и
  коэффициентов, база шаблонов, способ сложения целевой функции
  «критерий + сумма штрафов») — не правила и не целевые: их читает код,
  твой вердикт — not_rule с причиной «мета-предложение»; целевая — это
  конкретный критерий с направлением (что минимизируем), а не описание
  способа сложения;
- определительная клауза (перечень с кодами, определение термина) без
  слов обязательности — DEF общего раздела, не HARD: ограничение домена
  возникает только правилом, которое на перечень ссылается;
- спорное чтение — ВСЕГДА вопрос-развилка (part "2", type "choice",
  минимум два варианта), никогда «наиболее разумный выбор»;
- неоднозначная модальность («желательно», «не планировать», «можно») —
  kind "УСЛОВНЫЙ" + вопрос part "2" c branches: ветвь-тип на КАЖДЫЙ
  ключ варианта; явный императив источника — HARD, не понижать;
  разрешение «можно X» не превращать в SOFT без вопроса;
- незаданная источником единица, граница или тип окна — вопрос part
  "3a" (пейринг) или "3b" (ростеринг); НЕ переспрашивай то, что в
  клаузе задано явно;
- SOFT — штрафуемое предпочтение; вес — ТОЛЬКО как написано в клаузе
  (поле weight с дословной цитатой числа); число веса в клаузе не
  названо — weight null, вес придёт из ответа авиакомпании; вес не
  выдумывай и не переноси из другой клаузы;
- ЦЕЛЕВАЯ ФУНКЦИЯ ≠ мягкое правило (ЦФ-1): клауза, задающая ЦЕЛЬ
  оптимизации («минимизировать стоимость», «максимизировать
  справедливость») — элемент "objectives" с направлением, НЕ
  кандидат-SOFT; штрафуемое ограничение («желательно не более…») —
  SOFT; целевая — мера качества без понятия «нарушение»;
- сомнительное место источника — опечатка, склейка слов, ссылка на
  несуществующий пункт — читается по смыслу, но НЕ молча: заполни
  "note" по форме «в источнике: … — прочитано: …»; чистой клаузе
  note не пишется;
- комплект заказчика содержит ТОЛЬКО документы: {kit}. Клауза,
  ссылающаяся на документ или приложение ВНЕ этого перечня, даёт
  kind "OUT" + вопрос part "3a" или "3b" (по зоне, answer type
  "text") с просьбой предоставить отсутствующий документ;
  содержимое отсутствующего документа не выдумывается.

{fit_listing}

{dictionary}

Документ {sid} ({file}), клауз {n}:
{clauses}""" + _AS_WRITTEN_RULE + _LOGIC_SCOPE_RULE + m.ID_IMMUTABILITY_RULE

_PROMPT_CONTRADICTIONS = """Ты — аудитор формализованного свода правил
авиакомпании. Ниже — строки свода (идентификатор | зона | правило |
источник). Найди ПРОТИВОРЕЧИЯ: пары/группы строк, которые нельзя
выполнить одновременно. Требования одного смысла с РАЗНЫМИ значениями
из разных мест — противоречие; правила о разном — нет. Верни СТРОГО
один JSON-объект без пояснений:

{{"contradictions": [{{
  "rule_ids": ["<id>", "<id>", …],
  "title": "<короткое имя противоречия>",
  "text": "<вопрос авиакомпании: какое требование действует>",
  "options": [{{"key": "а", "text": "…"}}, {{"key": "б", "text": "…"}}, …]
}}, …]}}

Пустой список — законный ответ, выдумывать противоречия нельзя.

Строки свода ({n}):
{rules}""" + m.ID_IMMUTABILITY_RULE


def _ask(llm: llm_mod.LLMFn, prompt: str) -> dict:
    """Один вопрос модели с ДВУМЯ повторными попытками на кривом JSON;
    третья неудача — отказ с названной причиной (fail-closed). Две
    попытки было мало: перегруженный канал дважды подряд обрезал JSON
    и ронял часовое извлечение (живой прогон UniA 2026-08-20)."""
    retry = prompt + ("\n\nПредыдущий ответ не разобрался. Верни "
                      "СТРОГО один JSON-объект по схеме выше, без "
                      "какого-либо текста вокруг.")
    for attempt, ask in enumerate((prompt, retry, retry), start=1):
        try:
            return llm_mod.parse_json_reply(llm(ask))
        except llm_mod.LLMReplyError:
            if attempt == 3:
                raise
    raise AssertionError("недостижимо")


# ---------------------------------------------------------------------------
# Кандидаты: проверка и отбраковка
# ---------------------------------------------------------------------------

def _normalize_anchor(anchor) -> str:
    """Якорь кандидата без обрамления: в списке клауз якоря показаны в
    квадратных скобках, и модель честно возвращала их СО скобками —
    первый живой прогон отбраковал так сотни настоящих кандидатов.
    Скобки — форматирование, не содержание якоря."""
    return str(anchor or "").strip().removeprefix("[").removesuffix("]") \
        .strip()


def _check_candidate(c: dict, known_anchors: set[str]) -> str | None:
    """Причина отбраковки кандидата либо None. Контракт П-1 охраняется
    ЗДЕСЬ, на входе: сломанный кандидат не должен доживать до сборки."""
    anchor = c.get("anchor")
    if anchor not in known_anchors:
        return f"якорь {anchor!r} отсутствует в комплекте заморозки"
    if not (c.get("title") or "").strip() or \
            not (c.get("statement") or "").strip():
        return "пустое имя или формальная запись"
    if c.get("zone") not in m.ZONES:
        return f"зона {c.get('zone')!r} вне перечня {m.ZONES}"
    kind = c.get("kind")
    if kind not in m.KINDS + ("УСЛОВНЫЙ",):
        return f"тип {kind!r} вне перечня"
    # Вес из текста (П7, 2026-09-02): форма поля судится на входе —
    # целое ≥ 0 с дословной цитатой; вес у немягкого вида — брак формы.
    w = c.get("weight")
    if w is not None:
        if not isinstance(w, dict):
            return f"weight не объект: {repr(w)[:40]}"
        val = w.get("value")
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return f"weight.value не целое ≥ 0: {val!r}"
        if not str(w.get("as_written") or "").strip():
            return "weight без дословной цитаты as_written"
        if kind not in ("SOFT", "УСЛОВНЫЙ"):
            return f"weight у вида {kind!r} — вес несёт только SOFT"
    q = c.get("question")
    if kind == "УСЛОВНЫЙ":
        if not q:
            return "УСЛОВНЫЙ тип без вопроса-развилки"
        if not c["question"].get("branches"):
            return "УСЛОВНЫЙ тип без ветвей branches"
    if q:
        if q.get("part") not in ("2", "3a", "3b"):
            return f"часть вопроса {q.get('part')!r} вне перечня 2/3a/3b"
        fmt = q.get("answer") or {}
        if fmt.get("type") not in m.ANSWER_TYPES:
            return f"формат ответа {fmt.get('type')!r} вне перечня"
        options = fmt.get("options") or []
        keys = [o.get("key") for o in options]
        if fmt.get("type") == "choice" and len(options) < 2:
            return "развилка из менее чем двух вариантов"
        if q.get("part") == "2" and fmt.get("type") != "choice":
            return "неоднозначность (часть 2) без развилки choice"
        branches = q.get("branches")
        if branches:
            if sorted(branches) != sorted(keys):
                return (f"ветви {sorted(branches)} не совпадают с "
                        f"вариантами {sorted(keys)} — ветвь нужна "
                        f"каждому ответу")
            bad = [v for v in branches.values() if v not in m.KINDS]
            if bad:
                return f"ветвь даёт тип вне перечня: {bad}"
    return None


# ---------------------------------------------------------------------------
# Сборка модели
# ---------------------------------------------------------------------------

class _Assembler:
    def __init__(self, customer: str, sources: list[dict]):
        self.doc = {
            "meta": {"customer": customer, "mode": 1,
                     "cdpm_version": CDPM_VERSION,
                     "note": "собрано конвейером cdpm.extract"},
            "sources": [{"sid": s["sid"], "file": s["file"],
                         "sha256": s["sha256"]} for s in sources],
            "rules": [], "questions": [], "objectives": [],
        }
        self._rule_seq: dict[tuple[str, str], int] = {}
        self._q_by_norm: dict[tuple[str, str], dict] = {}
        self._q_ids: set[str] = set()
        self.merged_questions = 0
        self.quality_notes = 0
        # Недоказанные цитатой машинные значения последнего кандидата:
        # слова для отчёта отбраковки (приёмный цикл их забирает).
        self.quote_drops: list[str] = []
        # Замок части 1 (слово владельца 11.09.2026): судьба КАЖДОГО
        # предложения модели о противоречии — подтверждено машинным
        # свидетельством либо понижено в часть 2 с названной причиной.
        self.contradictions_confirmed: list[dict] = []
        self.contradictions_downgraded: list[dict] = []
        # Посадка на шаблоны: контракт действует у новых сборок
        # (validate_model требует template_fit у HARD/SOFT — приём
        # fail-closed гарантирует полноту синтезом C), телеметрия
        # уверенности — журналом прогона, не полем модели.
        self.doc["meta"]["fit_contract"] = True
        self.fit_journal: list[dict] = []
        self.fit_dropped = 0
        # Детерминизация ярлыков (2026-09-02): счёт правок вида/зоны,
        # сделанных кодом по семье посадки и типу источника; атрибуты
        # семей читаются один раз на сборку.
        self.traits_derived = 0
        self._traits = m.template_family_traits()

    def take_quote_drops(self) -> list[str]:
        """Забрать слова об отброшенных недоказанных значениях (цитата
        не дословна): приёмный цикл кладёт их в отчёт отбраковки
        `rejections.json`, как остальные причины. Забранное копится
        заново — второй раз одна причина не предъявляется."""
        drops, self.quote_drops = self.quote_drops, []
        return drops

    def _next_rule_id(self, sid: str, anchor: str) -> str:
        """Identity of the next accepted row, without reserving a skipped one."""
        return m.stable_rule_id(sid, anchor, self._rule_seq.get((sid, anchor), 0))

    def _rule_id(self, sid: str, anchor: str) -> str:
        rid = self._next_rule_id(sid, anchor)
        n = self._rule_seq.get((sid, anchor), 0)
        self._rule_seq[(sid, anchor)] = n + 1
        return rid

    def _question_id(self, part: str, key: str) -> str:
        qid = m.stable_question_id(part, key)
        n = 1
        while qid in self._q_ids:
            n += 1
            qid = m.stable_question_id(part, key, n)
        return qid

    def add_question(self, part: str, key: str, title: str, text: str,
                     answer: dict, rule_refs: list[str],
                     source_refs: list[dict]) -> str:
        """Вопрос со склейкой дублей: совпавшая нормализованная
        формулировка не рождает второй вопрос, а расширяет ссылки
        первого (обязательное свойство 4 постановки)."""
        # В2-8: ключ склейки включает ЧАСТЬ — одинаковый текст вопросов
        # разных частей (например, «предоставьте документ X» из 3а и
        # 3б) не должен сливаться в чужую часть анкеты.
        norm = (part, m.normalized_question_text(text))
        if norm in self._q_by_norm:
            q = self._q_by_norm[norm]
            for rid in rule_refs:
                if rid not in q["rule_refs"]:
                    q["rule_refs"].append(rid)
            for ref in source_refs:
                if ref not in q["source_refs"]:
                    q["source_refs"].append(ref)
            self.merged_questions += 1
            return q["id"]
        q = {"id": self._question_id(part, key), "part": part,
             "title": title, "text": text, "answer_format": answer,
             "rule_refs": list(rule_refs),
             "source_refs": list(source_refs)}
        self.doc["questions"].append(q)
        self._q_by_norm[norm] = q
        self._q_ids.add(q["id"])
        return q["id"]

    def add_candidate(self, sid: str, c: dict, fit: dict = None,
                      fit_conf: int = None,
                      fit_note: str = None,
                      clause_text: str = "") -> None:
        anchor = c["anchor"]
        rid = self._rule_id(sid, anchor)
        rule = {"id": rid, "title": c["title"].strip(),
                "statement": c["statement"].strip(),
                "zone": c["zone"],
                "kind": "HARD" if c["kind"] == "УСЛОВНЫЙ" else c["kind"],
                "sources": [{"sid": sid, "anchor": anchor}]}
        note = c.get("note")
        if isinstance(note, str) and note.strip():
            # С-3 (анализ Лайта-v3): сомнительное место источника
            # оставляет след — примечание строки свода; молчаливая
            # нормализация (битая ссылка LL-1) следа не оставляла.
            rule["note"] = note.strip()
            self.quality_notes += 1
        # Вес мягкого правила — как написано (П7, 2026-09-02): машинное
        # поле weight с дословной цитатой числа из клаузы; цитата не из
        # клаузы — вес отброшен со следом (не выдуман, не перенесён);
        # у условного вида вес ждёт разрешения модальности — хранится
        # и вступает, когда строка станет SOFT.
        w = c.get("weight")
        pending_weight = None
        pending_quote = None
        if isinstance(w, dict):
            aw = str(w.get("as_written") or "")
            # Fail-closed (ревью r55): без текста клаузы цитату сверить
            # нечем — вес не принимается на веру. Дословность судится
            # ОТ ВЕЛИЧИНЫ ДО КОНЦА цитаты (m.value_quote_in_text):
            # склейка двух фраз клаузы весом не становится — измеритель
            # отказал бы ею уже на выпуске пакета (живая находка кругов
            # 10.09.2026, run-0143 и run-0035 на Test).
            if not clause_text or not m.value_quote_in_text(
                    aw, w.get("value"), clause_text):
                extra = (f"вес {w.get('value')} отброшен: as_written не "
                         f"дословно из клаузы ({aw[:40]!r})")
                rule["note"] = ((rule.get("note", "") + "; " + extra)
                                .strip("; "))
                self.quality_notes += 1
                # След — и в отчёт отбраковки, как остальные причины:
                # примечание строки видит читатель свода, отбраковку —
                # читатель качества извлечения.
                self.quote_drops.append(extra)
            else:
                pending_weight = int(w["value"])
                pending_quote = " ".join(aw.split())
        # Ревизия 1.3/1.4: машинные параметры правила — общая
        # очистка m.sanitize_params (чужое и дубли — со следом, не
        # молча и не гибелью прогона; канонизация уточнителя
        # области). Тот же помощник работает на пути патча
        # обработчика — пути не расходятся.
        # Значения несёт посадка (template_fit.params с цитатами из
        # клаузы); устаревший список params модели при ней избыточен и
        # не судится — иначе каждая строка несла шум «параметр отброшен:
        # след не канонизируется» (проходы №9–№11 H0).
        legacy_params = c.get("params")
        if isinstance(fit, dict) and fit.get("params"):
            legacy_params = None
        kept_params, notes = m.sanitize_params(
            legacy_params, rule["kind"],
            rule_text=(str(rule.get("formal") or "") + " "
                       + str(rule.get("statement") or "")))
        for extra in notes:
            rule["note"] = ((rule.get("note", "") + "; " + extra)
                            .strip("; "))
            self.quality_notes += 1
        if kept_params:
            rule["params"] = kept_params
        # Ревизия 1.5: блок logic принимается только чистым по
        # validate_logic (словари, ссылки на params, след у атома);
        # грязный — отброшен ЦЕЛИКОМ со следом в примечание: половина
        # логики опаснее её отсутствия (правило остаётся текстом и
        # проверяется сертификатором, а не исполняется криво).
        c_logic = c.get("logic")
        if c_logic is not None:
            errs_l = m.validate_logic(c_logic, rule)
            if not errs_l:
                rule["logic"] = c_logic
            else:
                extra = f"логика отброшена: {errs_l[0]}"
                rule["note"] = ((rule.get("note", "") + "; "
                                 + extra).strip("; "))
                self.quality_notes += 1
        q = c.get("question")
        moot_note = None
        if q and c["kind"] == "УСЛОВНЫЙ" and isinstance(fit, dict) \
                and fit.get("category") in ("A", "B"):
            # Ярлыки кодом (ревью r54): развилка только о модальности
            # (ветви HARD/SOFT) у семьи с фиксированным видом
            # беспредметна — авиакомпании её не задают, вид ставит код.
            fk = (self._traits.get(fit.get("family")) or {}).get("kind")
            branches = q.get("branches") or {}
            if fk and branches \
                    and set(branches.values()) <= {"HARD", "SOFT"}:
                rule["kind"] = fk
                moot_note = (f"развилка модальности не задана: вид "
                             f"{fk} задаёт семья {fit.get('family')}")
                q = None
        if q:
            fmt = {"type": q["answer"]["type"]}
            if q["answer"].get("options"):
                fmt["options"] = q["answer"]["options"]
            if q["answer"].get("note"):
                fmt["note"] = q["answer"]["note"]
            qid = self.add_question(
                q["part"], f"{sid}-{anchor}", q["title"].strip(),
                q["text"].strip(), fmt, [rid],
                [{"sid": sid, "anchor": anchor}])
            rule["blockers"] = [qid]
            if c["kind"] == "УСЛОВНЫЙ":
                rule["conditional"] = {"question": qid,
                                       "branches": q["branches"]}
        # Вес вступает машинным полем у мягкой строки; у условной —
        # ждёт в развилке (conditional.weight) и вступает, когда
        # развилка разрешится в SOFT ответом авиакомпании или кодом.
        if pending_weight is not None:
            # Доказательный слой веса едет вместе с числом (RAZDELENIE
            # раздел 3 п.2: цитатный слой на все машинные поля).
            if rule["kind"] == "SOFT":
                rule["weight"] = pending_weight
                if pending_quote:
                    rule["weight_quote"] = pending_quote
            elif rule.get("conditional"):
                rule["conditional"]["weight"] = pending_weight
                if pending_quote:
                    rule["conditional"]["weight_quote"] = pending_quote
        if rule["kind"] in ("DEF", "DATA") and clause_text:
            # Перечень кодов определения — структурным полем из клаузы
            # (заявка COSMOS 03.09): коды с цитатами ставит код.
            # Поле есть ДОКАЗАТЕЛЬНЫЙ слой «какие коды названы
            # клаузой», а не полный перечень сущностей мира: приёмник
            # требует присутствия каждого НАЗВАННОГО кода в данных и
            # полноты не обещает (подтверждено COSMOS 03.09). Поэтому
            # порога «два и более» нет: определение парка из одного
            # типа — такой же перечень, а прежний порог набирался у
            # него лишь номером записи, то есть по ошибке.
            codes = m.codes_from_clause(clause_text)
            if codes:
                rule["codes"] = codes
        if fit is not None:
            rule["template_fit"] = fit
            self.fit_journal.append({
                "rule": rid, "role": "worker",
                "category": fit.get("category"),
                "confidence": fit_conf,
                "registry_rev": fit.get("registry_rev")})
        if fit_note:
            # След посадки — отдельный канал: quality_notes считает
            # замечания к КАЧЕСТВУ ИСТОЧНИКА, не отбраковки посадки.
            rule["note"] = ((rule.get("note", "") + "; " + fit_note)
                            .strip("; "))
            self.fit_dropped += 1
        # Контракт посадки держит САМ сборщик (полный набор 1.09:
        # прямые вызовы add_candidate мимо приёмного цикла давали
        # машинные правила без посадки) — честная C от кода.
        if moot_note:
            rule["note"] = ((rule.get("note", "") + "; " + moot_note)
                            .strip("; "))
            self.fit_journal.append({"rule": rid, "role": "code",
                                     "traits": [moot_note]})
            self.traits_derived += 1
        changes = m.align_fit_with_kind(self.doc, rule,
                                        "посадка не выполнялась (прямая "
                                        "сборка)")
        if changes:
            # Ярлыки кодом — отдельная роль журнала посадки: видно, что
            # именно код переставил у правила (вид/зона) и почему.
            self.fit_journal.append({"rule": rid, "role": "code",
                                     "traits": list(changes)})
            self.traits_derived += len(changes)
        self.doc["rules"].append(rule)

    def add_objective(self, sid: str, o: dict) -> str | None:
        """ЦФ-1: целевая функция — отдельная коллекция модели.
        Возвращает причину отбраковки либо None. Повтор той же
        целевой (тот же якорь либо та же нормализованная запись при
        той же зоне и направлении) склеивается добавлением источника,
        а не второй записью — дубль целевой в модели чистый шум."""
        anchor = o.get("anchor")
        title = o.get("title")
        statement = o.get("statement")
        if not isinstance(title, str) or not title.strip() \
                or not isinstance(statement, str) \
                or not statement.strip():
            return "целевая без имени или формальной записи"
        for fld in ("title", "statement"):
            fixed_text, fixes = m.normalize_mixed_script(o[fld])
            if fixes:
                o[fld] = fixed_text
                self.quality_notes += 1
        title, statement = o["title"], o["statement"]
        if o.get("zone") not in m.ZONES:
            return f"зона целевой {o.get('zone')!r} вне перечня"
        if o.get("direction") not in m.DIRECTIONS:
            return (f"направление целевой {o.get('direction')!r} "
                    f"вне перечня {m.DIRECTIONS}")
        norm = " ".join(statement.lower().split())
        for x in self.doc["objectives"]:
            # Только тождество записи: один якорь законно несёт две
            # РАЗНЫЕ целевые, а вот та же запись из другого чанка —
            # дубль, склеиваемый добавлением источника.
            if (x.get("zone") == o["zone"]
                    and x.get("direction") == o["direction"]
                    and " ".join(x.get("statement", "")
                                 .lower().split()) == norm):
                src = {"sid": sid, "anchor": anchor}
                if src not in x.get("sources", []):
                    x.setdefault("sources", []).append(src)
                return None
        oid = "O-" + m.stable_rule_id(sid, anchor)[2:]
        n = 1
        base = oid
        while any(x.get("id") == oid
                  for x in self.doc["objectives"]):
            n += 1
            oid = f"{base}-{n}"
        entry = {
            "id": oid, "title": title.strip(),
            "statement": statement.strip(),
            "zone": o["zone"], "direction": o["direction"],
            "sources": [{"sid": sid, "anchor": anchor}],
        }
        # Спецификация 1.1: машинная метрика — только из закрытого
        # словаря; чужое значение отбрасывается со следом (не отказ
        # целевой: поле необязательно).
        metric = o.get("metric")
        applicable = metric in m.METRICS and (
            set(m._ZONE_TASKS.get(o["zone"], ()))
            & set(m.METRICS[metric]["tasks"]))
        if applicable:
            entry["metric"] = metric
        elif metric:
            # Чужая метрика опускается со следом в примечании модели
            # (счётчик заметок качества) — не молча.
            entry["note"] = (f"метрика {str(metric)[:40]!r} вне "
                             f"словаря — опущена")
            self.quality_notes += 1
        # Носитель машинного критерия — целевая (П6/П11, 2026-09-02):
        # критерий, названный документом, ложится машинным блоком по
        # закрытому словарю приёмника; грязный блок — опущен со следом
        # (целевая остаётся), не выдуман.
        crit = o.get("criterion")
        if crit is not None:
            block, why = m.accept_objective_criterion(
                crit, clause_text=o.get("_clause_text") or "")
            if block is not None:
                entry["criterion"] = block
            else:
                entry["note"] = ((entry.get("note", "") + "; критерий "
                                  f"опущен: {why}").strip("; "))
                self.quality_notes += 1
        # Ставка немашинного критерия (форма ВМП: строка в) несёт ставку;
        # носитель objectives[].rate) — как написано, с цитатой из клаузы.
        rate = o.get("rate")
        if rate is not None and "criterion" not in entry:
            block, why = m.accept_objective_rate(
                rate, clause_text=o.get("_clause_text") or "")
            if block is not None:
                entry["rate"] = block
            else:
                entry["note"] = ((entry.get("note", "") + "; ставка "
                                  f"опущена: {why}").strip("; "))
                self.quality_notes += 1
        self.doc["objectives"].append(entry)
        return None

    def ensure_goal_questions(self) -> None:
        """ЦФ-1 (постановка владельца 2026-08-21, склейка вопросов
        2026-08-21): целевая функция обязана быть либо извлечена,
        либо спрошена, а мягкое правило — иметь путь к весу. ЕДИНЫЙ
        вопрос зоны Q-*-GOAL (таблица целевых с опцией «любое
        допустимое решение» и/или веса мягких строк) строится общим
        строителем model.ensure_goal_question — тем же, что чинит
        привязку при разрешении условных строк в ответах.

        Перед строителем — детерминированная миграция классификации
        (репетиция демо 2026-08-29): штрафную строку без веса
        извлечение изменчиво кладёт то мягким правилом, то целевой;
        миграция возвращает её мягким правилом ДО постановки вопроса
        весов — кадр и вопрос не зависят от настроения модели."""
        m.restore_soft_penalty_rules(self.doc)
        for part in ("3a", "3b"):
            m.ensure_goal_question(self.doc, part)
        # P-N-SHD 2026-08-30: семьи мягких правил — классификатором
        # уже в первой сборке модели (та же точка, что вопрос весов:
        # семья и вес — два поля самодостаточности мягкой строки).
        m.assign_soft_families(self.doc)

    def add_contradiction(self, con: dict) -> str | None:
        """Предложение модели о противоречии → вопрос анкеты. Часть 1 с
        блокировкой обеих строк — ТОЛЬКО при машинном свидетельстве
        расхождения значений одной нормы (`cdpm.contradiction`); без
        свидетельства — вопрос части 2 текстом модели, строки не
        блокируются. Возвращает причину отбраковки либо None."""
        by_id = {r["id"]: r for r in self.doc["rules"]}
        ids = con.get("rule_ids") or []
        if len(ids) < 2:
            return "противоречие из менее чем двух строк"
        missing = [i for i in ids if i not in by_id]
        if missing:
            return f"противоречие ссылается на несуществующие строки " \
                   f"{missing}"
        options = con.get("options") or []
        if len(options) < 2:
            return "противоречие без развилки из двух и более вариантов"
        # Конвенция кода: строки семей, дополняющих друг друга по
        # построению базы (предел и его продление), противоречием не
        # являются — модель видит «720 против 780» и спрашивает, хотя
        # продление существует ради превышения предела (проход №6 H0:
        # ложный вопрос части 1 абз.10/абз.13 на однозначном входе).
        # Определение и данные ТРЕБОВАНИЙ НЕ НЕСУТ: они именуют
        # сущности мира (базы, должности, парк). Противоречие возможно
        # только между требованиями — «нельзя выполнить одновременно»
        # к именованию неприменимо. Живой ложный вопрос набора H0
        # 03.09: определение должностей «КВС и Второй пилот» против
        # правила «должности при комплектовании не различаются» —
        # модель увидела спор там, где разные уровни речи. Пробел
        # ссылки правила на несуществующую запись перечня ловится
        # другим механизмом (коды перечней, покрытие), не этим.
        defs = [i for i in ids if by_id[i].get("kind") in ("DEF", "DATA")]
        if defs:
            return (f"строки {defs} — определение или данные: они именуют "
                    f"сущности и требований не несут, противоречия нет")
        from cdpm import family_lexicon as _lex
        fams_of = []
        for i in ids:
            fit = by_id[i].get("template_fit")
            fams_of.append(fit.get("family") if isinstance(fit, dict)
                           and fit.get("category") in ("A", "B") else None)
        if len(ids) == 2 and all(fams_of) \
                and _lex.complementary_families(fams_of[0], fams_of[1]):
            return (f"семьи {fams_of[0]!r} и {fams_of[1]!r} дополняют друг друга "
                    f"(предел и его продление) — противоречия нет, конвенция кода")
        source_refs = []
        for i in ids:
            for ref in by_id[i]["sources"]:
                if ref not in source_refs:
                    source_refs.append(ref)
        key = "-".join(r["sources"][0]["anchor"]
                       for r in (by_id[i] for i in ids[:2]))
        title = (con.get("title") or "Противоречие").strip()
        text = (con.get("text") or "").strip() or "Какое требование " \
                                                  "действует?"
        # ЗАМОК ЧАСТИ 1 (слово владельца 11.09.2026): принцип 1 ВМП
        # держится кодом, а не чтением модели. Вопрос, блокирующий обе
        # строки, рождается, только когда код САМ ВИДИТ у них одну норму
        # с разными значениями; прочее чтение модели — законный вопрос
        # авиакомпании части 2, строки продолжают действовать.
        ev, why = contradiction_mod.evidence([by_id[i] for i in ids])
        if ev is None:
            qid = self.add_question(
                "2", key, title,
                f"{text} {_CONTRA_DOWNGRADE_NOTE} Причина: {why}.",
                {"type": "choice", "options": options}, list(ids),
                source_refs)
            self.contradictions_downgraded.append(
                {"question": qid, "rule_ids": list(ids),
                 "reason": why})
            return None
        if len(source_refs) < 2:
            return "у противоречия менее двух мест источников"
        qid = self.add_question(
            "1", key, title, text,
            {"type": "choice", "options": options}, list(ids),
            source_refs)
        for q in self.doc["questions"]:
            if q["id"] == qid:
                q.setdefault("contradiction", ev)
                break
        for i in ids:
            blockers = by_id[i].setdefault("blockers", [])
            if qid not in blockers:
                blockers.append(qid)
        self.contradictions_confirmed.append(
            {"question": qid, "rule_ids": list(ids), "norm": ev["norm"]})
        return None


# ---------------------------------------------------------------------------
# Конвейер
# ---------------------------------------------------------------------------

def _load_kit(kit_dir: Path) -> list[dict]:
    manifest_path = kit_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ExtractError(f"манифест {manifest_path} не существует — "
                           f"сначала заморозка (cdpm.freeze)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = []
    for f in manifest.get("files", []):
        entries.append(json.loads(
            (kit_dir / "clauses" / f"{f['sid']}.json")
            .read_text(encoding="utf-8")))
    return entries


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _sanitize_assembly(doc: dict, rejected: list[dict]) -> int:
    """Детерминированная зачистка ФОРМЫ перед контрактом (живой отказ
    «Полярис» 2026-08-20: один вопрос с вариантами при формате
    'registry' убил час извлечения целиком). Код нормализует только
    форму, не содержание: лишние варианты у не-развилки отбрасываются
    с записью в отбраковку; ничего не выдумывается. Возвращает число
    нормализаций."""
    fixed = 0
    for q in doc.get("questions", []):
        fmt = q.get("answer_format")
        if isinstance(fmt, dict) and fmt.get("type") != "choice" \
                and fmt.get("options"):
            fmt.pop("options", None)
            rejected.append({
                "sid": "-", "anchor": q.get("id"),
                "reason": "нормализовано: варианты ответа при формате "
                          f"{fmt.get('type')!r} отброшены (варианты "
                          "бывают только у развилки)"})
            fixed += 1
    return fixed


def _batch_key(prefix: str, idx: int, listing: str,
               salt: str = "") -> str:
    """Имя кэш-файла чанка: включает хэш содержимого И подписи промпта
    (`salt`) — устаревший кэш от прежней заморозки, прежнего шаблона
    промпта или прежнего СОСТАВА комплекта просто не совпадёт по
    имени. Без соли кэш, прогретый до С-3/С-4, молча отключал их
    мандаты (состязательная проверка 2026-08-21)."""
    import hashlib
    h = hashlib.sha256((salt + "\x00" + listing)
                       .encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{idx:03d}-{h}.json"


def _not_rule_blocker(text: str) -> str | None:
    """Почему клаузу нельзя закрыть вердиктом модели «не правило»:
    измеримая величина (класс семантики) либо слово-носитель предмета
    какой-либо семьи базы — положительный признак нормы, кандидат
    обязателен. None — препятствий нет."""
    from cdpm import balance as _bal
    from cdpm import family_lexicon as _lex
    vals = [v for v in _bal.values_of(text, set())
            if v["class"] in _bal.SEMANTIC_CLASSES and v["class"] != "code"]
    if vals:
        return (f"клауза несёт измеримую величину «{vals[0]['text']}» "
                f"(класс {vals[0]['class']})")
    for fam in _lex.lexicon_families():
        stem = _lex.subject_hit(fam, text)
        if stem:
            return f"клауза несёт слово предмета планирования «{stem}» (семья {fam})"
    return None


def _assemble(asm: "_Assembler", cand_jobs: list, replies: list, rejected: list,
              model_verdicts: dict, code_verdicts: dict) -> int:
    """Сборка выдачи модели по порциям в сборщик: вердикты «не правило»,
    целевые, кандидаты с посадкой и код-гейтом дублей. Один и тот же код
    для извлечения и для второго чтения (слой 2) — сравниваются сборки,
    а не сырые ответы. Возвращает число полученных кандидатов."""
    candidates_got = 0
    # Код-гейт дублей (2026-09-02): подписи принятых посадок по клаузам
    # — второй кандидат той же клаузы с той же семьёй и параметрами
    # отбраковывается со следом (прогон №2 H0: дубль-кандидат клаузы
    # предела доживал до аудитора).
    seen_fits: set = set()
    for job, reply in zip(cand_jobs, replies):
        sid, known = job["sid"], job["known"]
        # Вердикты «не правило» модели по клаузам порции (замок биекции
        # «не потерять»): якорь — из порции, причина непуста; вердикт по
        # клаузе, которая одновременно породила кандидата/целевую, судит
        # баланс (клауза покрыта строкой — вердикт лишний, не ошибка).
        batch_anchors = {ln.split("]", 1)[0][1:] for ln in job["listing"].split("\n")
                         if ln.startswith("[")}
        for row in (reply.get("not_rule") if isinstance(reply.get("not_rule"), list) else []):
            if not isinstance(row, dict):
                continue
            anchor = _normalize_anchor(row.get("anchor"))
            reason = " ".join(str(row.get("reason") or "").split())
            if anchor not in batch_anchors:
                rejected.append({"sid": sid, "anchor": row.get("anchor"),
                                 "reason": "вердикт «не правило» по якорю вне порции"})
                continue
            if not reason:
                rejected.append({"sid": sid, "anchor": anchor,
                                 "reason": "вердикт «не правило» без причины"})
                continue
            if (sid, anchor) in code_verdicts:
                continue                      # вердикт кода первичен
            # План 2.2: клауза с измеримой величиной или словом предмета
            # планирования — ВСЕГДА кандидат; вердикт модели «не правило»
            # по ней отбраковывается (проверка плана №12: вердикт с любой
            # причиной гасил нормы без чисел и с числами одинаково).
            blocker = _not_rule_blocker(job["texts"].get(anchor, ""))
            if blocker:
                rejected.append({"sid": sid, "anchor": anchor,
                                 "reason": f"вердикт «не правило» отклонён: {blocker}"})
                continue
            model_verdicts[(sid, anchor)] = {"verdict": "не правило",
                                             "reason": reason, "by": "model"}
        # ЦФ-1: целевые функции документа — отдельной коллекцией.
        # «objectives»: null от модели законен (и кэшируем) — не
        # падать (ревью фазы Б 2026-08-21).
        for o in (reply.get("objectives") or []):
            if not isinstance(o, dict):
                continue
            o["anchor"] = _normalize_anchor(o.get("anchor"))
            if o["anchor"] not in known:
                rejected.append({"sid": sid, "anchor": o.get("anchor"),
                                 "reason": "целевая: якорь отсутствует "
                                           "в комплекте заморозки"})
                continue
            o["_clause_text"] = job["texts"].get(o["anchor"], "")
            reason = asm.add_objective(sid, o)
            if reason:
                rejected.append({"sid": sid, "anchor": o.get("anchor"),
                                 "reason": reason})
        for c in reply.get("candidates", []):
            candidates_got += 1
            c["anchor"] = _normalize_anchor(c.get("anchor"))
            # В2-7: мусорный note (не строка) не исчезает МОЛЧА —
            # мандат С-3 «не молча» распространяется и на сам след.
            note = c.get("note")
            if note is not None and not isinstance(note, str):
                rejected.append({
                    "sid": sid, "anchor": c.get("anchor"),
                    "reason": "нормализовано: замечание к качеству "
                              "(note) не строка — отброшено"})
                c["note"] = None
            reason = _check_candidate(c, known)
            if reason:
                rejected.append({"sid": sid,
                                 "anchor": c.get("anchor"),
                                 "reason": reason})
                continue
            # Посадка на шаблоны: приём fail-closed — грязная
            # или отсутствующая посадка машинного правила
            # синтезируется категорией C со следом, правило
            # не гибнет и не проходит на веру.
            ctext = job["texts"].get(c["anchor"], "")
            # Носители посадки к канону формы — со следом в отбраковке
            # (нормализация, не отказ).
            fit0, norm_notes = m.normalize_fit_shape(c.get("template_fit"))
            if norm_notes:
                c["template_fit"] = fit0
                rejected.append({"sid": sid, "anchor": c["anchor"],
                                 "reason": "нормализовано: " + "; ".join(norm_notes)})
            # Смешение алфавитов в формулировке и имени — к алфавиту
            # большинства, со следом (омоглифы ломают распознавание слов).
            for fld in ("title", "statement"):
                if isinstance(c.get(fld), str):
                    fixed_text, fixes = m.normalize_mixed_script(c[fld])
                    if fixes:
                        c[fld] = fixed_text
                        rejected.append({"sid": sid, "anchor": c["anchor"],
                                         "reason": "нормализовано смешение алфавитов "
                                                   f"({fld}): " + ", ".join(fixes)})
            candidate = {**c, "id": asm._next_rule_id(sid, c["anchor"])}
            fit, conf, fit_note = m.accept_fit_candidate(candidate, ctext, new_output=True)
            sig = m.fit_signature({"template_fit": fit})
            if sig is not None:
                key = (sid, c["anchor"], sig)
                if key in seen_fits:
                    rejected.append({
                        "sid": sid, "anchor": c["anchor"],
                        "reason": (f"дубль кандидата: та же "
                                   f"семья {sig[0]!r} и те же "
                                   f"параметры на клаузе — "
                                   f"оставлен первый")})
                    continue
                seen_fits.add(key)
            asm.add_candidate(sid, c, fit=fit, fit_conf=conf,
                              fit_note=fit_note, clause_text=ctext)
            # Недоказанное цитатой значение строки (as_written не
            # дословна) — в отчёт отбраковки со своими словами: правило
            # живёт дальше без него, а причина видна там же, где
            # остальные (раньше её знало только примечание строки).
            for words in asm.take_quote_drops():
                rejected.append({"sid": sid, "anchor": c["anchor"],
                                 "reason": words})
    return candidates_got


def run_extract(kit_dir: Path, out_dir: Path, llm: llm_mod.LLMFn,
                customer: str, chunk_size: int = CHUNK_SIZE,
                llm_by_sid: dict[str, llm_mod.LLMFn] | None = None,
                cross_llm: llm_mod.LLMFn | None = None,
                control=None, cache_dir: Path | None = None,
                progress=None, second_llm: llm_mod.LLMFn | None = None,
                second_note: str | None = None) -> dict:
    """Полный проход: комплект заморозки → модель + свод + анкета +
    отчёт отбраковки в `out_dir`. Возвращает сводку счётчиков.

    Политика обработки по файлам (решение владельца 2026-08-18):
    `llm_by_sid` задаёт канал модели ДЛЯ КАЖДОГО документа — клаузы
    файла с политикой «только локальная модель» не уходят в облако;
    `cross_llm` — канал перекрёстных стадий (поиск противоречий смешивает
    строки разных файлов, поэтому вызывающая сторона обязана передать
    туда канал СТРОЖАЙШЕЙ политики прогона). Без этих параметров всё
    идёт через единый `llm` — прежний контракт командной строки."""
    entries = _load_kit(kit_dir)
    asm = _Assembler(customer, entries)
    rejected: list[dict] = []
    clauses_fed = 0
    candidates_got = 0
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    # Стадия структуры (план «прямой ход» 2.2): вердикты КОДОМ по
    # положительным признакам — заголовки формы и документа, пусто,
    # мета-предложения о самом ВМП (титул, валюта, база шаблонов,
    # композиция); их атрибуты — в meta модели, не строками свода.
    from cdpm import structure as st_mod
    code_verdicts = st_mod.code_verdicts(entries)
    code_meta = st_mod.meta_from_verdicts(code_verdicts)
    if code_meta.get("currency"):
        asm.doc["meta"]["currency"] = code_meta["currency"]
    if code_meta.get("registry_rev"):
        asm.doc["meta"]["registry_rev_declared"] = code_meta["registry_rev"]
    model_verdicts: dict = {}
    # Прогресс (директива владельца 2026-08-20): счёт чанков известен
    # заранее — вызывающая сторона показывает его на блоке.
    total_chunks = sum(
        (len(e["clauses"]) + chunk_size - 1) // chunk_size
        for e in entries)
    done_chunks = 0
    # С-4 (анализ Лайта-v3): модель обязана знать СОСТАВ комплекта —
    # иначе ссылку на отсутствующий документ не отличить от внутренней.
    kit_listing = "; ".join(f"{e['sid']} — {e['file']}" for e in entries)

    # ПАР-1: чанки независимы — вызовы модели идут параллельно, а
    # сборка кандидатов остаётся строго последовательной В ПОРЯДКЕ
    # чанков (идентификаторы, склейка вопросов и отбраковка
    # детерминированы, как в прежнем последовательном пути).
    cand_jobs = []
    for entry in entries:
        sid = entry["sid"]
        entry_llm = (llm_by_sid or {}).get(sid, llm)
        known = {c["anchor"] for c in entry["clauses"]}
        # Посадка на шаблоны: цитаты параметров судятся против
        # ТЕКСТА клаузы-источника — карта якорь→текст едет с job.
        texts = {c["anchor"]: c["text"] for c in entry["clauses"]}
        for idx, batch in enumerate(_chunks(entry["clauses"],
                                            chunk_size)):
            clauses_fed += len(batch)
            # Клаузы с вердиктом кода показываются моделью-контекстом с
            # пометкой: вердикт по ним уже есть, модель их не судит.
            listing = "\n".join(
                f"[{c['anchor']}] (вердикт кода: "
                f"{code_verdicts[(sid, c['anchor'])]['reason']}) {c['text']}"
                if (sid, c["anchor"]) in code_verdicts
                else f"[{c['anchor']}] {c['text']}"
                for c in batch)
            cand_jobs.append({"sid": sid, "file": entry["file"],
                              "llm": entry_llm, "known": known,
                              "texts": texts,
                              "idx": idx, "n": len(batch),
                              "listing": listing})

    def _fetch_candidates(job: dict, chan=None, suffix: str = "") -> dict:
        # Кэш чанков: отказ поздней стадии не сжигает оплаченную
        # работу (живой урок «Поляриса» 2026-08-20 — час извлечения
        # сгорел на отказе сборки).
        # Ключ кэша несёт ВСЕ динамические секции задания (ревью r54:
        # перечень шаблонов среза и словарь менялись, а кэш отдавал
        # ответы модели под старое задание). Второе чтение (слой 2) —
        # свой префикс кэша: ответы двух семейств не смешиваются.
        dictionary = m.machine_dictionary_listing()
        fit_listing = m.template_fit_listing()
        # Соль несёт и отпечаток КАНАЛА, который отвечает на этот чанк
        # (аудит 04.09, A11): провайдер, модель после подстановки
        # умолчания, усилие, класс политики файла, конфигурация
        # действующей роли. Смена модели или политики файла
        # (облако → локально) не отдаёт ответы прежнего канала; у
        # второго читателя — отпечаток его собственного канала.
        chan_used = chan or job["llm"]
        cpath = (cache_dir / _batch_key(
            job["sid"] + suffix, job["idx"], job["listing"],
            salt=(_PROMPT_CANDIDATES + kit_listing + "\x00"
                  + dictionary + "\x00" + fit_listing + suffix + "\x00"
                  + llm_mod.channel_identity(chan_used)))
            if cache_dir else None)
        if cpath is not None and cpath.is_file():
            return json.loads(cpath.read_text(encoding="utf-8"))
        reply = _ask(chan_used, _PROMPT_CANDIDATES.format(
            sid=job["sid"], file=job["file"], n=job["n"],
            kit=kit_listing, clauses=job["listing"],
            dictionary=dictionary, fit_listing=fit_listing))
        if cpath is not None:
            cpath.write_text(json.dumps(reply, ensure_ascii=False),
                             encoding="utf-8")
        return reply

    def _tick(_k: int) -> None:
        nonlocal done_chunks
        done_chunks += 1
        if progress:
            progress("извлечение", done_chunks, total_chunks,
                     len(asm.doc["rules"]))

    # Слой 2 (перекрёстное чтение): второе семейство читает те же порции
    # параллельно с первым; сборки сравниваются кодом после первой.
    # Политику файлов (какие документы вправе уйти второму читателю)
    # решает вызывающая сторона: она знает классы файлов и слот канала.
    second_ok = second_llm is not None
    if second_ok:
        total_chunks += len(cand_jobs)
    replies = llm_mod.parallel_map(_fetch_candidates, cand_jobs,
                                   control=control, on_done=_tick)
    candidates_got += _assemble(asm, cand_jobs, replies, rejected,
                                model_verdicts, code_verdicts)
    second_report: dict | None = None
    asm2 = None
    rejected2: list = []
    verdicts2: dict = {}
    hints_by_anchor: dict = {}
    clause_by_key = {(str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
                     for e in entries for c in e.get("clauses") or []}
    if second_ok:
        replies2 = llm_mod.parallel_map(
            lambda job: _fetch_candidates(job, chan=second_llm, suffix="-second"),
            cand_jobs, control=control, on_done=_tick)
        asm2 = _Assembler(customer, entries)
        _assemble(asm2, cand_jobs, replies2, rejected2, verdicts2, code_verdicts)
        # Второе чтение — независимый источник подсказок ремонту: слот,
        # который первое чтение потеряло, а второе несёт, код вносит по
        # своим же проверкам (цитата в клаузе), не спрашивая модель.
        for r2 in asm2.doc["rules"]:
            key = m._primary_ref(r2)
            if key:
                hints_by_anchor.setdefault(key, []).append(
                    {"weight": r2.get("weight"), "fit": r2.get("template_fit")})
        for o2 in asm2.doc["objectives"]:
            key = m._primary_ref(o2)
            if key:
                hints_by_anchor.setdefault(key, []).append(
                    {"criterion": o2.get("criterion"), "rate": o2.get("rate")})
        # Сравнение сборок — ПОСЛЕ ремонта первой (иначе слот, который
        # ремонт восстановит, читается как спор — проход №8 H0).
    elif second_note:
        second_report = {"skipped": second_note}

    asm.ensure_goal_questions()

    # П-3 пакета посадки: B-посадки получают вопрос-подтверждение
    # со структурным ключом (машинное ключевание анкеты и книги).
    m.ensure_fit_questions(asm.doc)

    # Полнота параметров семьи (09.09.2026, набор Н0-B): параметр
    # шаблона, не заданный ни одним правилом свода, спрашивается
    # анкетой кодом; носители блокируются до ответа.
    family_params_mod.ensure_family_param_questions(asm.doc)

    # Хвост Лайта 15: конфликт одноимённых параметров разных
    # правил — детерминированный вопрос части 1 с блокировкой
    # носителей (по машинным полям, модель не нужна).
    m.ensure_param_conflict_questions(asm.doc)

    # Поиск противоречий — по зонам, чтобы не сравнивать пейринг с
    # ростерингом; пустой ответ законен.
    files_by_sid = {s["sid"]: s["file"] for s in asm.doc["sources"]}
    zone_batches = []
    for zone in m.ZONES:
        rows = [r for r in asm.doc["rules"] if r["zone"] == zone
                and r["kind"] in ("HARD", "SOFT", "DATA", "DEF")]
        for idx, batch in enumerate(_chunks(rows, CONTRA_CHUNK)):
            if len(batch) >= 2:
                zone_batches.append((zone, idx, batch))
    total_chunks += len(zone_batches)

    def _fetch_contradictions(job) -> dict:
        zone, idx, batch = job
        listing = "\n".join(
            f"{r['id']} | {r['zone']} | {r['statement']} | "
            f"{files_by_sid.get(r['sources'][0]['sid'], '?')}, "
            f"{r['sources'][0]['anchor']}"
            for r in batch)
        # Соль — промпт И отпечаток перекрёстного канала (A11).
        contra_chan = cross_llm or llm
        cpath = (cache_dir / _batch_key(
            f"contra-{zone}", idx, listing,
            salt=(_PROMPT_CONTRADICTIONS + "\x00"
                  + llm_mod.channel_identity(contra_chan)))
                 if cache_dir else None)
        if cpath is not None and cpath.is_file():
            return json.loads(cpath.read_text(encoding="utf-8"))
        reply = _ask(contra_chan, _PROMPT_CONTRADICTIONS.format(
            n=len(batch), rules=listing))
        if cpath is not None:
            cpath.write_text(json.dumps(reply, ensure_ascii=False),
                             encoding="utf-8")
        return reply

    def _tick_contra(_k: int) -> None:
        nonlocal done_chunks
        done_chunks += 1
        if progress:
            progress("поиск противоречий", done_chunks, total_chunks,
                     len(asm.doc["rules"]))

    # ПАР-1: зоны независимы — вызовы параллельны, сборка вопросов
    # противоречий последовательна в порядке зон (детерминизм).
    con_replies = llm_mod.parallel_map(
        _fetch_contradictions, zone_batches,
        control=control, on_done=_tick_contra)
    for reply in con_replies:
        for con in reply.get("contradictions", []):
            reason = asm.add_contradiction(con)
            if reason:
                rejected.append({"sid": "-", "anchor": "-",
                                 "reason": reason})

    _sanitize_assembly(asm.doc, rejected)
    problems = m.validate_schema(asm.doc) + m.validate_model(asm.doc)
    if problems:
        raise ExtractError(
            "собранная модель не прошла инварианты контракта — модель "
            "не записана: " + "; ".join(problems))

    out_dir.mkdir(parents=True, exist_ok=True)
    m.dump_model(asm.doc, out_dir / "model.json")
    # Вердикты покрытия извлечения (код + модель) — extract/coverage.json;
    # вердикт модели по клаузе, покрытой строкой, не записывается
    # (клауза покрыта строкой, вердикт «не правило» противоречил бы ей).
    from cdpm import coverage as cov
    covered_now = cov.covered_anchors(asm.doc)
    all_verdicts = {k: dict(v, round=0) for k, v in code_verdicts.items()}
    for k, v in model_verdicts.items():
        if k not in covered_now:
            all_verdicts[k] = dict(v, round=0)
    for k in list(all_verdicts):
        all_verdicts[k].pop("meta", None)
    cov.save_verdicts(out_dir, all_verdicts)
    coverage_report = cov.report(entries, asm.doc, all_verdicts)
    # Содержательный слой покрытия добавляется ниже, после измерения
    # (RAZDELENIE раздел 3 пункт 1): измеритель считает остаток и
    # нормативные носители без строк на НОСИТЕЛЕ, а не на адресе.
    (out_dir / "coverage-report.json").write_text(
        json.dumps(coverage_report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    # Структурное решает код (решение владельца 2026-09-04): зона записи
    # — по разделу документа, когда документы в канонической форме;
    # цитата параметра, поставленного без цитаты, — из клаузы-источника
    # той же машинной сверкой величин, что у баланса. Модель отвечает
    # только за смысл.
    from cdpm import form_structure as struct_mod
    zone_changes = struct_mod.enforce_form_zones(asm.doc, entries)
    quote_fills = struct_mod.backfill_param_quotes(asm.doc, clause_by_key)
    (out_dir / "structure-report.json").write_text(
        json.dumps({"zones_by_section": zone_changes,
                    "param_quotes_from_clause": quote_fills},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if zone_changes or quote_fills:
        m.assign_soft_families(asm.doc)
        m.dump_model(asm.doc, out_dir / "model.json")
    # Баланс потребления величин (план «прямой ход» 2.4): каждая значимая
    # величина клаузы — потреблена слотом, определением, атрибутом ВМП,
    # признана не нормой по классу кода либо предъявлена без судьбы.
    from cdpm import balance as bal_mod
    balance_report = bal_mod.report(entries, asm.doc, all_verdicts)
    # Ремонт по находкам кода (план 2.6): адресный запрос модели на
    # пустой слот с цитатой, ≤3 попыток (третья — каналом перекрёстной
    # стадии, другое семейство при раскладке); незакрытое остаётся в
    # балансе как находка без судьбы.
    repair_report = {"fixed": [], "left": [], "attempts": 0}
    if balance_report["findings"] and llm is not None:
        from cdpm import repair as repair_mod
        repair_report = repair_mod.repair(
            asm.doc, entries, balance_report["findings"], llm,
            alt_llm=cross_llm if cross_llm is not llm else None,
            hints=hints_by_anchor or None)
        if repair_report["fixed"]:
            # Семья решателя — АТРИБУТ ПОСАДКИ, и посадка только что
            # могла измениться ремонтом: без пересчёта в модели остаются
            # две правды о семье, и инвариант «ярлык ставит код» валит
            # весь проход. Живой отказ серии 03.09, дважды подряд:
            # посадка стала calendar_windows, а семья решателя осталась
            # layover_penalty от эвристики первого чтения.
            m.assign_soft_families(asm.doc)
            problems = m.validate_schema(asm.doc) + m.validate_model(asm.doc)
            if problems:
                raise ExtractError(
                    "модель после ремонта по находкам кода не прошла "
                    "инварианты — не записана: " + "; ".join(problems))
            m.dump_model(asm.doc, out_dir / "model.json")
            balance_report = bal_mod.report(entries, asm.doc, all_verdicts)
    # Слой 2: сравнение канонического слоя двух сборок по носителям —
    # после ремонта первой; расхождение по существу — вопрос части 2.
    second_questions = 0
    second_adopted: list = []
    if asm2 is not None:
        from cdpm import second_reading as sr_mod
        # Посадка, потерянная первым чтением за форму, принимается из
        # второго чтения (проверка кодом по клаузе) — до сравнения; баланс
        # после этого пересчитывается (цитата посадки потребляет величины).
        second_adoption_rejections: list = []
        second_adopted = sr_mod.adopt_stronger_fits(
            asm.doc, asm2.doc, clause_by_key, rejections=second_adoption_rejections)
        # Носители, пропущенные первым чтением целиком, получают строки
        # второго чтения (06.09.2026, run-0084: 4 кандидата из 11 по S1 —
        # извлечение не вправе закончиться «извлечено» с дырами, которые
        # слой 2 уже видит). Принятые строки судятся дальше наравне.
        second_missing = sr_mod.adopt_missing_rows(asm.doc, asm2.doc)
        if second_missing:
            second_adopted = list(second_adopted) + second_missing
        if second_adopted:
            # Принятая посадка меняет семью решателя — пересчёт по той
            # же причине, что и после ремонта: одна правда о семье.
            m.assign_soft_families(asm.doc)
            balance_report_new = bal_mod.report(entries, asm.doc, all_verdicts)
            still = {(f["sid"], f["anchor"], f["class"], f["text"])
                     for f in balance_report_new["findings"]}
            repair_report["left"] = [f for f in repair_report["left"]
                                     if (f["sid"], f["anchor"], f["class"], f["text"]) in still]
            balance_report_new["repair"] = balance_report.get("repair", repair_report)
            balance_report = balance_report_new
        second_report = sr_mod.compare(asm.doc, asm2.doc, model_verdicts, verdicts2)
        second_questions = sr_mod.add_questions(asm, second_report, clause_by_key)
        second_report["questions"] = second_questions
        second_report["adopted"] = second_adopted
        second_report["adoption_rejections"] = second_adoption_rejections
        second_report["adoption_rejections_count"] = len(second_adoption_rejections)
        second_report["adopted_missing"] = second_missing
        second_report["rejected_2"] = len(rejected2)
    # Остаток баланса после ремонта — вопрос части 1 с блокировкой строки
    # (план 2.6 «затем вопрос или остановка»): величина без машинного
    # поля не проходит дальше молча; на однозначном наборе такой вопрос
    # — дефект конвейера, и раннер его печатает.
    balance_questions = 0
    balance_unasked: list = []
    rule_ids = {r["id"] for r in asm.doc["rules"]}
    for f in repair_report["left"]:
        if f.get("class") == "operator":
            continue
        refs = [rid for rid in (f.get("rows") or []) if rid in rule_ids]
        if not refs:
            # Носитель — целевая либо строки нет: вопрос анкеты обязан
            # ссылаться на строку свода (постановка §3.2), поэтому
            # находка остаётся в балансе как непотреблённая (П1 красный),
            # а не глохнет; путь к анкете у целевых — вопрос GOAL.
            balance_unasked.append({"anchor": [f["sid"], f["anchor"]],
                                    "text": f["text"], "rows": f.get("rows") or []})
            continue
        rows_txt = ", ".join(f.get("rows") or []) or "—"
        text = (f"В клаузе [{f['sid']} {f['anchor']}] величина «{f['text']}» "
                f"(класс {f['class']}) не потреблена ни одним машинным полем "
                f"строки {rows_txt}; адресный ремонт не удался: "
                f"{str(f.get('repair') or '')[:160]}. Чем является эта величина?")
        # Часть 2 «неоднозначности (развилки чтения)»: величина есть, слот
        # не найден — это спор о чтении, а не противоречие источников.
        qid = asm.add_question(
            "2", f"balance:{f['sid']}:{f['anchor']}:{f['class']}",
            "Величина без машинного поля", text,
            {"type": "choice", "options": [
                {"key": "а", "text": "ставка критерия или целевой (укажите, за что)"},
                {"key": "б", "text": "вес мягкого правила"},
                {"key": "в", "text": "параметр правила (укажите имя параметра семьи)"},
                {"key": "г", "text": "справочная величина, нормой не является"}]},
            refs, [{"sid": f["sid"], "anchor": f["anchor"]}])
        for r in asm.doc["rules"]:
            if r["id"] in refs:
                bl = r.setdefault("blockers", [])
                if qid not in bl:
                    bl.append(qid)
        balance_questions += 1
    if balance_questions or second_questions or second_adopted:
        problems = m.validate_schema(asm.doc) + m.validate_model(asm.doc)
        if problems:
            raise ExtractError(
                "модель с вопросами по остатку баланса / слою 2 не прошла "
                "инварианты — не записана: " + "; ".join(problems))
        m.dump_model(asm.doc, out_dir / "model.json")
    balance_report["repair"] = repair_report
    balance_report["questions"] = balance_questions
    balance_report["unasked"] = balance_unasked
    (out_dir / "balance.json").write_text(
        json.dumps(balance_report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    if second_report is not None:
        (out_dir / "second-reading.json").write_text(
            json.dumps(second_report, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    # Измеритель постановки (зона CDPM с 03.09): нормативность носителей,
    # остаток потребления с адресатом, поля без носителя — extract/measure.json.
    from cdpm import measure as ms_mod
    # Вердикты кода и модели по клаузам — адресаты нормативных носителей
    # без строк (замок содержания судит их так же при сборке пакета).
    ms_report = ms_mod.measure(asm.doc, [{"sid": e["sid"], "anchor": c["anchor"],
                                          "text": c.get("text") or ""}
                                         for e in entries for c in e.get("clauses") or []],
                               verdicts=all_verdicts)
    ms_ok, ms_problems = ms_mod.verdict(ms_report)
    (out_dir / "measure.json").write_text(
        json.dumps(ms_report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "measure.md").write_text(
        ms_mod.render_report(ms_report, header="Измерение постановки после извлечения"),
        encoding="utf-8")
    coverage_report = cov.report(entries, asm.doc, all_verdicts,
                                 measure_report=ms_report)
    (out_dir / "coverage-report.json").write_text(
        json.dumps(coverage_report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    # П-5 пакета посадки: телеметрия уверенности — журналом прогона
    # (extract/fit-journal.jsonl), НЕ полем модели; калибровку по
    # эталону строит решательная сторона (fit_calibration).
    if asm.fit_journal:
        import datetime as _dt
        at = _dt.datetime.now().isoformat(timespec="seconds")
        (out_dir / "fit-journal.jsonl").write_text(
            "\n".join(json.dumps(dict(row, at=at),
                                 ensure_ascii=False)
                      for row in asm.fit_journal) + "\n",
            encoding="utf-8")
    (out_dir / "svod.md").write_text(render.render_svod(asm.doc),
                                     encoding="utf-8")
    (out_dir / "anketa.md").write_text(render.render_anketa(asm.doc),
                                       encoding="utf-8")
    (out_dir / "rejections.json").write_text(
        json.dumps({"rejected": rejected,
                    "merged_questions": asm.merged_questions,
                    # Замок части 1: судьба каждого предложения модели о
                    # противоречии — подтверждено свидетельством либо
                    # понижено в часть 2 с названной причиной.
                    "contradictions": {
                        "confirmed": asm.contradictions_confirmed,
                        "downgraded": asm.contradictions_downgraded}},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    # После второго чтения, принятия пропущенных строк и сохранения всей
    # диагностики: пустота сама по себе не доказывает отсутствие правил.
    empty_problem = empty_result_problem(asm.doc, coverage_report)
    if empty_problem:
        raise ExtractError(empty_problem)
    counts = m.count_objects(asm.doc)
    return {"clauses_fed": clauses_fed, "candidates": candidates_got,
            "rules": counts["rules"], "questions": counts["questions"],
            "questions_per_part": counts["questions_per_part"],
            "rejected": len(rejected),
            "merged_questions": asm.merged_questions,
            "quality_notes": asm.quality_notes,
            # Замок части 1 (11.09.2026): сколько предложений модели о
            # противоречии подтверждено машинным свидетельством и
            # сколько понижено в часть 2 — с причинами.
            "contradictions": {
                "proposed": (len(asm.contradictions_confirmed)
                             + len(asm.contradictions_downgraded)),
                "confirmed": len(asm.contradictions_confirmed),
                "downgraded": len(asm.contradictions_downgraded),
                "downgrade_reasons": [row["reason"] for row
                                      in asm.contradictions_downgraded]},
            "traits_derived": asm.traits_derived,
            "objectives": len(asm.doc["objectives"]),
            "coverage": {"clauses": coverage_report["clauses"],
                         "covered_by_rows": coverage_report["covered_by_rows"],
                         "covered_partially": coverage_report.get("covered_partially"),
                         "values_without_addressee":
                             coverage_report.get("values_without_addressee"),
                         "not_rule": coverage_report["not_rule"],
                         "missing": len(coverage_report["missing"]),
                         "code_verdicts": len(code_verdicts),
                         "model_verdicts": len(model_verdicts)},
            "balance": {"values": balance_report["values"],
                        "consumed": balance_report["consumed"],
                        "unconsumed": balance_report["unconsumed"],
                        "repaired": len(repair_report["fixed"]),
                        "repair_attempts": repair_report["attempts"],
                        "questions": balance_questions},
            "second_reading": ({k: (len(v) if k == "adopted" else v)
                                for k, v in second_report.items()
                                if k not in ("disagreements", "weaker_readings",
                                             "incomplete_readings", "adoption_rejections")}
                               if second_report is not None else None),
            "measure": {"ok": ms_ok, "problems": ms_problems, "counts": ms_report["counts"]}}


_PROMPT_LOGIC_SOFT = """Ты — формализатор правил авиакомпании. Дано
ОДНО МЯГКОЕ (SOFT) правило свода. Реши, ложится ли оно РОВНО в один
из пяти мягких шаблонов волны, и если да — верни машинную логику.

Шаблоны (demand.kind) и их роли-ссылки:
1) "штраф-ступенчатый-за-стыковку" — штраф за каждую ступень
   превышения длины стыковки; роли: threshold_param (порог, имя
   параметра long_sit_threshold, минуты), step_param (шаг ступени,
   long_sit_step, минуты).
2) "штраф-за-разрыв-смены" — единица за СМЕНУ, внутри которой есть
   стыковка длиннее порога; роль: threshold_param
   (duty_split_threshold, минуты).
3) "однородность-цепочки-ночь-день" — единица за цепочку (между
   пребываниями в базе приписки), где смешаны ночные и дневные
   смены; роли: window_from_param / window_to_param (границы
   ночного окна, имена night_duty_start / night_duty_end, минуты
   ОТ ПОЛУНОЧИ местного времени базы).
4) "мягкий-предел-дней-подряд" — единица за каждый рабочий день
   сверх предела серии подряд; роль: bound_param
   (soft_consecutive_days, счёт дней).
5) "неудовлетворённая-выполнимая-заявка" — единица за выполнимую,
   но не удовлетворённую заявку сотрудника на день; ролей нет.

Жёсткие требования формата:
- числа НИКОГДА не пишутся в logic напрямую — только ссылками на
  параметры правила; недостающие параметры верни списком
  params_add: [{{"name": "<имя из перечня выше>", "value": <целое>,
  "as_written": "<дословная цитата значения из правила>",
  "relation": "max" | "min" | "eq" | "window"}}]
  — значения бери ИЗ ТЕКСТА правила (минуты; время суток — минутами
  от полуночи: 02:00 → 120, 05:59 → 359; «4:00» порога — 240);
  relation — смысл величины в правиле (порог «не более» — max,
  «не менее/сверх» — min, точное значение — eq, граница окна —
  window); as_written в params_add — ТОЛЬКО само значение с
  единицей, как оно написано в правиле («60 мин», «30 минут»,
  «12:00»), БЕЗ окружающих слов и формул.
- у каждого атома — след as_written цитатой из правила;
- demand несёт РОЛИ своего шаблона строковыми ИМЕНАМИ параметров и
  след, например: {{"kind": "штраф-ступенчатый-за-стыковку",
  "threshold_param": "long_sit_threshold", "step_param":
  "long_sit_step", "as_written": "<цитата>"}}; роли чужих шаблонов
  не пиши;
- в logic обязателен source_params — точный список ВСЕХ имён
  параметров, на которые ссылается блок (те же имена, что в ролях и
  params_add): след источников точен в обе стороны;
- subject — из словаря предметов (обычно "assignment");
  condition — ПУСТОЙ список (условия волна не выражает: если без
  условия правило шире поставленного — верни null);
- enforce: "required".

Ответ — СТРОГО JSON без пояснений:
{{"logic": {{...}}, "params_add": [...]}} — если правило чисто
ложится в один шаблон; {{"logic": null}} — если не ложится, ложится
частично или требует условий. «Чистый или ничего».

Правило:
{rule}
""" + m.ID_IMMUTABILITY_RULE

_PROMPT_LOGIC = """Ты — формализатор правил авиакомпании. Дано ОДНО
правило свода с его машинными параметрами. Построй блок logic —
машинное представление правила ПРОИЗВОЛЬНОГО вида (условного/
календарного/экзистенциального/композитного) — либо честно верни
null, если правило целиком сводится к порогам своих params (простое
«не более/не менее X» уже исполняется параметром) или не выражается
словарями первой волны.

Правило:
{rule}

Словари ЗАКРЫТЫЕ (чужие имена будут отброшены):
subject: assignment|flight|duty|reserve_duty|rest|sit|employee|base|
day|month|rolling_window|day_off|qualification;
ПРЕДМЕТ — то, ЧЕГО касается требование, не то, где оно записано:
состав/численность РЕЙСА — flight (не duty); счёт по сменам — duty;
выходные — day_off; резервные смены — reserve_duty; отдых — rest
(ручка решателя исполняет требование только при верном предмете);
condition — список атомов {{predicate, аргументы, as_written=дословный
фрагмент правила}}; предикаты: starts_in(from_param,to_param),
longer_than(param), shorter_than(param), at_base, at_outstation,
consecutive(unit=day|duty, param), in_window(window),
follows(subject), has_qualification_for(type_value),
overlaps_in_time, marked_unavailable, in_list(allowed);
demand: {{kind: count_within(relation,param[,universe])|
duration_within(relation,param,measure) — measure ОБЯЗАТЕЛЕН:
span=размах | block=налёт|exists_person(subject)|
exists_universe(subject,universe)|not_exists(subject)|
combination_forbidden(subject), as_written}}; relation требования:
max|min|eq; universe ТОЛЬКО из перечня: employee×month,
employee×day, base×day, employee×rolling_window; окно in_window —
узлом {{kind: month}} либо {{kind: rolling, unit: day, param}};
ЧИСЛА ТОЛЬКО ссылками на параметры ЭТОГО правила: именем-строкой
при единственной записи имени, объектом {{name, qualifier}} при
нескольких записях; число в требовании exists/not_exists без
параметра запрещено — используй count_within; source_params —
ПОЛНЫЙ перечень применённых ссылок (те же строки/объекты, что в
condition/demand/window); enforce: required (обязано исполняться
решателем) | post-check (проверка после счёта — когда данных
заведомо нет); у КАЖДОГО атома след as_written.

ЗНАК УСЛОВИЯ. Правило вида «действие выполняет тот, у кого выполнено
условие C» РАЗРЕШАЕТ действие при C и ЗАПРЕЩАЕТ его при ОТСУТСТВИИ C.
Не переноси условие в запрет как есть: «рейс выполняет пилот, у
которого допуск действует» — это НЕ «сочетание с допуском запрещено».
Если словарь предикатов не позволяет выразить отрицание условия,
верни null: неверный знак хуже отсутствия блока.

ПОСАДКА НА ШАБЛОН. Если механика правила уже задана его посадкой на
семью шаблонов с машинными параметрами (поле template_fit ниже), блок
logic не нужен — верни null: исполнитель семьи применяет правило по
параметрам, а второй, сочинённый слой только разойдётся с ним.

Ответ: СТРОГО один JSON-объект {{"logic": null | {{...}}}} без
какого-либо текста вокруг.""" + _LOGIC_SCOPE_RULE + m.ID_IMMUTABILITY_RULE


# Source references are not magnitudes, even when count/credit permits a bare
# number. This bounded reference grammar does NOT establish a PARAM's counted
# subject. It only prevents a known identifier/fraction from becoming a value.
_SOURCE_REFERENCE_RE = re.compile(
    r"(?<!\w)(?:(?:пункт(?:а|у|ом|е|ы|ов)?|подпункт(?:а|у|ом|е|ы|ов)?|"
    r"раздел(?:а|у|ом|е|ы|ов)?|приложени(?:е|я|ю|ем|и|й)|"
    r"глав(?:а|ы|е|у|ой)|таблиц(?:а|ы|е|у|ей)|"
    r"стать(?:я|и|е|ю|ёй)|рисун(?:ок|ка|ке)|страниц(?:а|ы|е|у)|"
    r"пп?\.|табл\.|рис\.|стр\.|section|paragraph|chapter|appendix|annex|table|figure)"
    r"\s*(?:№\s*)?|№\s*)(?P<number>[0-9]+(?:[./][0-9]+)*)",
    re.IGNORECASE)
_SOURCE_FRACTION_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)?\s*[/⁄∕]\s*[0-9]+(?:[.,][0-9]+)?")


def _whole_value_quote(statement: str, quote: str) -> bool:
    """Keep source records whole; known references are not numeric evidence."""
    spans = [match.span() for rx in (
        family_params_mod._QTY_RE, family_params_mod._HHMM_RE,
        family_params_mod._ANY_UNIT_RE, family_params_mod._GAP_UNIT_RE,
        family_params_mod._RECORD_RE, _SOURCE_FRACTION_RE)
        for match in rx.finditer(statement)]
    references = [match.span("number") for match in _SOURCE_REFERENCE_RE.finditer(statement)]
    for match in re.finditer(r"(?<!\w)" + re.escape(quote) + r"(?!\w)", statement):
        start, end = match.span()
        if any(left < end and start < right for left, right in references):
            continue
        if not any(left < end and start < right and
                   (left < start or right > end) for left, right in spans):
            return True
    return False


def merge_soft_params(rule: dict, adds) -> str | None:
    """Слияние params_add мягкой формы в правило (fail-closed):
    имена ТОЛЬКО из словаря PARAMS, значения — целые ≥ 0; имя, уже
    существующее у правила с ДРУГИМ значением, — отказ (модель не
    вправе молча переписывать извлечённое). Возвращает причину
    отказа либо None. Проверяются все старые записи и ячейки посадки,
    затем весь кандидат; при отказе правило не меняется. Вызывающий
    по-прежнему откатывает успешное добавление, если отвергнута logic.
    Новые параметры объявленной семьи пишет общий write_cell, прочие
    остаются совместимым legacy-путём. Это не миграция на landing/2."""
    if adds in (None, []):
        return None
    if not isinstance(adds, list):
        return f"params_add не список: {adds!r}"
    legacy = rule.get("params")
    if legacy is None:
        legacy = []
    if not isinstance(legacy, list):
        return "исходные params не список"
    existing: dict[str, list[dict]] = {}
    for p in legacy:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            return "исходная запись params не объект с именем"
        existing.setdefault(p["name"], []).append(p)
    # Судить исходные носители ДО любого изменения. В частности, не
    # прятать старое расхождение за совпавшим новым ответом модели.
    problems = m.reconcile_carriers(rule)
    if problems:
        return "; ".join(problems)
    staged = []
    for a in adds:
        if not isinstance(a, dict):
            return f"запись params_add не объект: {a!r}"
        name, value = a.get("name"), a.get("value")
        if not isinstance(name, str) or name not in m.PARAMS:
            return f"имя {name!r} вне словаря PARAMS"
        if not isinstance(value, int) or isinstance(value, bool) \
                or value < 0:
            return f"{name}: значение не целое ≥ 0: {value!r}"
        previous = existing.get(name, [])
        for prev in previous:
            if prev.get("value") != value:
                return (f"{name}: уже извлечено со значением "
                        f"{prev.get('value')!r} — переписывать "
                        f"нельзя")
            if a.get("relation") is not None and prev.get("relation") \
                    and a["relation"] != prev["relation"]:
                return (f"{name}: уже извлечено с отношением "
                        f"{prev['relation']!r} — переписывать нельзя")
        if previous:
            continue
        # Схема параметров требует след и отношение — их даёт МОДЕЛЬ
        # цитатой из правила (сочинять за неё нельзя): без них ставка
        # уронила бы инварианты модели (живой урок run-0050,
        # строка-21).
        as_written, relation = a.get("as_written"), a.get("relation")
        if not isinstance(as_written, str) or not as_written.strip():
            return f"{name}: без следа as_written"
        # Перечень отношений — словарь каталога (шаг 7): копии словаря
        # в коде больше нет, текст проблемы называет сам перечень.
        relations = m.PARAM_RELATIONS
        if relation not in relations:
            return (f"{name}: relation {relation!r} вне перечня "
                    f"{relations}")
        staged.append({"name": name, "value": value,
                       "unit": m.PARAMS[name],
                       "as_written": as_written.strip(),
                       "relation": relation})
        # Следующее добавление того же имени проверяется по тем же
        # правилам, что и существующий параметр, не создаёт дубль.
        existing[name] = [staged[-1]]
    candidate = {**rule, "params": legacy + staged}
    problems = m.reconcile_carriers(candidate)
    if problems:
        return "; ".join(problems)
    if staged:
        # Кандидат выше сверял ВСЕ предложения со старыми носителями.
        # Писатель работает только с копией: поздний отказ не оставляет
        # ни первую ячейку, ни legacy-добавление, ни ложное примечание.
        target = copy.deepcopy(rule)
        fit = target.get("template_fit")
        landed = isinstance(fit, dict) and fit.get("category") in ("A", "B")
        if landed:
            from cdpm import catalog_bridge
            family = fit.get("family")
            if not isinstance(family, str) or family not in catalog_bridge.family_records():
                return f"семья посадки {family!r} не объявлена — запись параметров невозможна"
            if not family_params_mod._cells_writable(target):
                return "params посадки не объект — запись параметров невозможна"
        legacy_staged = []
        for p in staged:
            name = p["name"]
            spec = family_params_mod._param_spec(fit["family"], name) if landed else None
            if not spec:
                legacy_staged.append(p)
                continue  # нет объявленного места: новую семью не придумываем
            cells = family_params_mod._fit_cells(target)
            cell = cells.get(name)
            if name not in cells:
                quote = p["as_written"]
                statement = str(rule.get("statement") or "")
                if quote not in statement:
                    return f"{name}: цитата params_add отсутствует в тексте правила"
                if not _whole_value_quote(statement, quote):
                    return (f"{name}: цитата params_add обрезает числовую запись или единицу "
                            "либо включает номер ссылки в тексте правила; значение не записано")
                try:
                    origin = catalog_bridge.catalog()
                    if fit.get("schema_version") == "landing/2":
                        from template_catalog import semantics
                        pin = fit["family_contract"]
                        origin = semantics.resolve_release(origin,
                            fingerprint=pin["catalog_fingerprint"],
                            version=pin["catalog_version"])
                    parsed = family_params_mod.parse_rule_quote(quote, p["unit"],
                                                               source=origin.path)
                except (catalog_bridge.CATALOG_ERRORS + (KeyError, TypeError, ValueError)) as error:
                    return f"{name}: исходный каталог цитаты не подтверждён: {error}"
                if parsed is None:
                    return (f"{name}: цитата params_add {quote!r} не разобрана "
                            f"в единице {p['unit']}; значение не записано")
                if parsed[0] != p["value"]:
                    return f"{name}: значение params_add не сходится с цитатой правила"
                if not isinstance(rule.get("id"), str) or not rule["id"].strip():
                    return f"{name}: у правила нет идентификатора для источника ячейки"
                cell = {"value": p["value"], "unit": p["unit"],
                        "relation": p["relation"], "quote": quote,
                        "source": {"kind": "rule", "rule_id": rule["id"]}}
                if fit.get("schema_version") == "landing/2":
                    from template_catalog import landing
                    try:
                        cell = landing.prepare_cell(target, name, cell, catalog_bridge.catalog())
                    except catalog_bridge.CATALOG_ERRORS as error:
                        return f"{name}: typed-ячейка не построена: {error}"
            # Существующую согласную ячейку и источник ответа не
            # заменяем источником rule: новый проход не переписывает ответ.
            problem = family_params_mod.write_cell(target, name, cell)
            if problem:
                return problem
        if legacy_staged:
            target["params"] = copy.deepcopy(legacy + legacy_staged)
        problems = m.reconcile_carriers(target)
        if problems:
            return "; ".join(problems)
        note = ("параметры формы добавлены целевым проходом: "
                + ", ".join(f"{s['name']}={s['value']}"
                            for s in staged))
        target["note"] = ((rule.get("note", "") + "; " + note)
                        .strip("; "))
        rule.clear()
        rule.update(target)
    return None


def produce_logic(out_dir: Path, llm: llm_mod.LLMFn,
                  verifier: llm_mod.LLMFn | None = None,
                  control=None, cache_dir: Path | None = None,
                  cache_salt: str = "",
                  progress=None) -> dict:
    """Целевой проход ступени 3 (2026-08-24): общий промпт кандидатов
    производит logic ВЕРОЯТНОСТНО (живая проба нано: 3 чистых блока
    против 0 на одном и том же чанке разными вызовами) — узкий вопрос
    по одному правилу надёжнее. Приёмка прежняя, «чистый или ничего»
    со следом в примечании; затем рубеж 1 — обратный пересказ
    независимым сверщиком (retell_check): расхождение И молчание
    блокируют правило вопросом части 2. Модель и свод перезаписываются
    ТОЛЬКО целиком прошедшими инварианты (fail-closed)."""
    out_dir = Path(out_dir)
    doc = m.load_model(out_dir / "model.json")
    # Этап 2 волны 1 (П-2): целевой проход порождает logic и для
    # SOFT-правил — пятью мягкими шаблонами волны, значения ролей
    # приезжают params_add (имена ТОЛЬКО из словаря PARAMS).
    targets = [r for r in doc["rules"]
               if r.get("kind") in ("HARD", "SOFT")
               and not isinstance(r.get("logic"), dict)
               and not r.get("blockers")]
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    done = 0

    def _tick(_k: int) -> None:
        nonlocal done
        done += 1
        if progress:
            progress("логика правил", done, len(targets), done)

    def _one(rule: dict) -> dict:
        body = {k: rule.get(k) for k in
                ("id", "kind", "title", "statement", "note")
                if rule.get(k) is not None}
        body["params"] = rule.get("params") or []
        # Посадка на семью шаблонов едет в запрос: механика, уже
        # заданную параметрами семьи, второй раз сочинять не надо
        # (набор H0, 03.09: правило допуска по типу покрыто семьёй
        # исключений по типам, а модель построила для него logic с
        # ОБРАТНЫМ знаком условия).
        fit = rule.get("template_fit")
        if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
            body["template_fit"] = {"family": fit.get("family"),
                                    "params": fit.get("params")}
        tmpl = (_PROMPT_LOGIC_SOFT if rule.get("kind") == "SOFT"
                else _PROMPT_LOGIC)
        prompt = tmpl.format(
            rule=json.dumps(body, ensure_ascii=False, indent=1))
        cpath = None
        if cache_dir:
            # Соль канала: ответ модели — функция И промпта, И
            # канала; кэш без соли пережил бы смену модели и
            # подсовывал бы null прежнего воркера (живой урок
            # run-0042). Помимо соли вызывающей стороны — отпечаток
            # самого канала (A11): модель после подстановки умолчания,
            # провайдер, усилие, действующая роль.
            key = hashlib.sha256(
                ("logic-pass|" + cache_salt + "|"
                 + llm_mod.channel_identity(llm) + "|" + prompt)
                .encode("utf-8")).hexdigest()
            cpath = cache_dir / f"logic-{key}.json"
            if cpath.is_file():
                return json.loads(cpath.read_text(encoding="utf-8"))
        reply = _ask(llm, prompt)
        if cpath is not None:
            cpath.write_text(json.dumps(reply, ensure_ascii=False),
                             encoding="utf-8")
        return reply

    replies = llm_mod.parallel_map(_one, targets, control=control,
                                   on_done=_tick)
    attached = dropped = 0
    for rule, reply in zip(targets, replies):
        lg = reply.get("logic") if isinstance(reply, dict) else None
        if not isinstance(lg, dict):
            continue                       # честный null — не находка
        # SOFT-волна: значения ролей приезжают params_add — слить
        # ДО валидации ссылок, при красной валидации ОТКАТИТЬ
        # (сироты-параметры не остаются).
        before_rule = None
        if rule.get("kind") == "SOFT":
            before_rule = copy.deepcopy(rule)
            err = merge_soft_params(
                rule, reply.get("params_add")
                if isinstance(reply, dict) else None)
            if err:
                rule.clear()
                rule.update(before_rule)
                rule["note"] = ((rule.get("note", "") + "; "
                                 + "логика отброшена (params_add): "
                                 + err).strip("; "))
                dropped += 1
                continue
        errs = m.validate_logic(lg, rule)
        if errs:
            if before_rule is not None:
                # Откат всей попытки: не оставлять даже ложное
                # примечание «добавлены» или новый пустой params[].
                rule.clear()
                rule.update(before_rule)
            extra = ("логика отброшена (целевой проход): "
                     + "; ".join(errs[:2]))
            rule["note"] = ((rule.get("note", "") + "; " + extra)
                            .strip("; "))
            dropped += 1
        else:
            rule["logic"] = lg
            attached += 1
    summary = {"asked": len(targets), "attached": attached,
               "dropped": dropped}
    if verifier is not None and attached:
        from cdpm import logic_proof as lp_mod
        summary["retell"] = lp_mod.retell_check(doc, verifier,
                                                control=control)
    problems = m.validate_schema(doc) + m.validate_model(doc)
    if problems:
        raise ExtractError(
            "модель после целевого прохода logic не прошла "
            "инварианты — не записана: " + "; ".join(problems))
    m.dump_model(doc, out_dir / "model.json")
    (out_dir / "svod.md").write_text(render.render_svod(doc),
                                     encoding="utf-8")
    (out_dir / "anketa.md").write_text(render.render_anketa(doc),
                                       encoding="utf-8")
    return summary


def main(argv: list[str] | None = None,
         llm_override: llm_mod.LLMFn | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(
        description="Извлечение правил из комплекта заморозки в модель "
                    "CDPM (свод + анкета)")
    ap.add_argument("kit", help="каталог комплекта заморозки")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--customer", required=True,
                    help="название авиакомпании для шапки артефактов")
    ap.add_argument("--scope", default="any",
                    choices=["any", "local-only"],
                    help="признак набора llm_scope (дисциплина У-03)")
    ap.add_argument("--llm", default=None,
                    choices=["local", "cloud", "auto"],
                    help="просьба о канале модели (cloud — облачная "
                         "Claude); законность решает дисциплина контура")
    ap.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = ap.parse_args(argv)
    llm = llm_override or llm_mod.cosmos_llm(args.scope,
                                             requested=args.llm)
    try:
        s = run_extract(Path(args.kit), Path(args.out_dir), llm,
                        args.customer, args.chunk_size)
    except (ExtractError, llm_mod.LLMReplyError) as e:
        print(f"ОТКАЗ: {e}")
        return 1
    per_part = ", ".join(f"ч.{p}: {n}"
                         for p, n in s["questions_per_part"].items() if n)
    print(f"клауз подано {s['clauses_fed']}, кандидатов "
          f"{s['candidates']}, правил {s['rules']}, вопросов "
          f"{s['questions']} ({per_part or 'нет'}); отбраковано "
          f"{s['rejected']}, склеено вопросов {s['merged_questions']}, "
          f"замечаний к качеству источников {s['quality_notes']}")
    con = s["contradictions"]
    print(f"предложений о противоречиях {con['proposed']}: подтверждено "
          f"свидетельством {con['confirmed']}, понижено в часть 2 "
          f"{con['downgraded']}")
    for reason in con["downgrade_reasons"]:
        print(f"  понижено: {reason}")
    out = Path(args.out_dir)
    for name in ("model.json", "svod.md", "anketa.md",
                 "rejections.json"):
        print(f"записано: {(out / name).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
