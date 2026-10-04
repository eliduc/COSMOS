# -*- coding: utf-8 -*-
"""Живой прогон Kotlin-видов стендовых задач каталога шаблонов.

Путь 1 проекта шага 4 (§5.6): один процесс решателя, СВОЙ пакет
постановки на задачу. Мир стенда переводится в задачу решателя
(`vmp.stand_adapters.world_to_problem`), правила стенда — в настоящий
пакет постановки библиотекой CDPM (`cdpm.export_task_package.export_zip`),
пакет уходит прогоном по идентификатору, и повкладовый счёт ПОД ПАКЕТОМ
ЭТОГО прогона даёт числа, которые стенд сверяет с ожиданием записи.

Ни одной новой ручки решателя здесь нет и не заявляется: используются
`POST /schedules/run`, `GET /schedules/run/{id}/status` и `POST
/schedules/run/{id}/score-breakdown` — те же, которыми ходит кнопка. HTTP
-клиент тоже НЕ свой: берётся `vmp.run.button.SolverHttp`, второго
клиента решателя в дереве быть не должно.

Правила, за которыми стоит следить:

  * адрес решателя приходит ТОЛЬКО ключом `--with-solver <url>`; умолчания
    нет и здесь его тоже нет (решение владельца 04.09);
  * состав пакета — правило §5.6: посадки категории A получают ВСЕ семьи,
    чьи параметры названы миром (без посадки семьи её пороги до решателя
    не доедут), мягкие семьи идут с весом 1, семьи истории — посадкой с
    параметрами (решатель выводит форму логики из посадки), а задача вида
    `kotlin_metric` кладёт метрику в `objectives[].metric`;
  * ФОРМА логики (вид `kotlin_logic_form`) измеряется показаниями
    ПРОГОНА (`applied-rules` → `lastRun.logic.plans[]`), а не берётся из
    ожидания: имя порождённого ограничения выводится из идентификатора
    ПРАВИЛА, поэтому две задачи одной семьи судятся под одним именем, и
    копия формы из ожидания делала бы строку зелёной по построению;
  * прогон КРОШЕЧНЫЙ: предел 1 с. Числа разбора счёта от найденного
    решения не зависят вовсе — счёт считается по НАЗНАЧЕНИЯМ СТЕНДА,
    которые уезжают телом запроса разбора;
  * любое несостоявшееся звено — отказ СЛОВАМИ (`StandSolverError`), а не
    ноль и не пропуск: решатель не отвечает, пакет отвергнут гейтом
    решателя, ограничения нет в разборе — всё это `failed` с причиной у
    вызывающего судьи.

Два отступления от буквы §5.6, названные вслух (оба — в приложении схемы,
раздел «Отступления реализации шага 4/7»):

1. У задач `kotlin_metric` прогон идёт режимом `optimization` (а не
   `mode=None`, как записано в строке-эскизе вызова). Причина — сам §5.2:
   ожидание метрики есть «модуль вклада ограничения целевой ПРИ ВЕСЕ
   ЦЕЛЕВОЙ 1 под пакетом прогона», а вес целевой прибавляется к весам
   ограничения только в режиме `optimization` (`TaskModes.resolve`:
   целевая резолвится лишь при нём, и только там
   `effective[constraint] += objective.weight`). При `mode=None` решатель
   берёт `feasibility-soft`, целевую не резолвит вовсе, и метрика
   получила бы вес 0 — ожидание проверялось бы ни о чём. Прочие
   Kotlin-виды выбирают разрешённый режим из manifest: наличие SOFT
   требует feasibility-soft; чисто жёсткий состав допускает feasibility-hard.

2. Детерминированный профиль Д1 (`seed 0`, `step_limit 1`) прогоном по
   умолчанию НЕ ЗАДАЁТСЯ: на этой сборке решателя он останавливает
   прогон НЕ НА ВСЕХ мирах стенда (`D1_FINDING` ниже — измерено, не
   предположено), а общей остановкой на всех мирах служат только
   стенные часы. Прогон идёт ими с пределом 1 с. Воспроизводимость
   измеряемых чисел этим не задета: разбор счёта считает НАЗНАЧЕНИЯ
   СТЕНДА под пакетом прогона — чистая функция (пакет, задача); решение
   прогона стенд не читает. ПЕРЕКЛЮЧАТЕЛЬ у профиля есть — ключ
   `--deterministic` команды стенда (умолчание ВЫКЛ): включают его на
   решателе С ПРАВКОЙ остановки Д1 (страховочный предел стенных часов
   внутри Д1, сделана 08.09.2026 на стороне решателя). Такой решатель
   называет признак остановки полем `termination` ответа о состоянии
   (`D1_STOP_SIGN`, `TERMINATION_KINDS`), и стенд берёт его ОТТУДА, а
   не из своей константы; служба без правки отвечает без поля — стенд
   говорит это словами (`termination_words`), а не молчит. Живёт
   признак ПОЛЕМ СТРОКИ (`termination_row`), а не словами `note`: вид
   остановки зависит от секундомера прогона (страховка судится раньше
   счётного предела), а `note` уезжает в `stand_verified` привязок и
   обязан быть каноном КАТАЛОГА, а не паспортом прогона.

Ещё два публичных обещания этого модуля — оба по просьбам проекта
пакета «Kotlin-исполнители агентом»:

  * `GOT_FORMS` (Ж-2) — формы ПОЛУЧЕННОГО трёх видов решателя одним
    источником: конвейер пополнения каталога берёт их импортом, а не
    вторым литералом;
  * правило состава §5.6 о секциях пакета (Ж-5) — таблицей
    `stand_adapters.WORLD_TO_PACKAGE_SECTION` и носителями
    `SECTION_CARRIERS`: новая секция даётся строкой таблицы, семья вне
    таблицы — отказ словами `require_section`, а не пакет без формы.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

from vmp import stand_adapters as sa

#: Профиль Д1 (зерно 0, один счётный шаг) — тот, который назван проектом
#: §5.6 и которым прогон стенда идти НЕ МОЖЕТ на всех мирах стенда: на
#: части из них счётный предел не наступает вовсе (см. `D1_FINDING`).
#: Значение оставлено ЯВНЫМ, чтобы находка была видна в коде, а не
#: только в докладе: вернуть профиль — один ключ, когда остановка
#: починена.
DETERMINISTIC = {"seed": 0, "step_limit": 1}

#: Где живёт признак остановки прогона у ПРАВЛЕНОГО решателя. Правку
#: сделала дорожка решателя (`kotlin-solver`, 08.09.2026): счётные
#: пределы Д1 остались главными, а рядом с ними в той же ИЛИ-составной
#: остановке живёт СТРАХОВОЧНЫЙ предел стенных часов — он же и есть
#: ранний выход, потому что признака «исполнимых ходов нет» Timefold
#: 2.0 наружу не даёт. Признак приходит ОТВЕТОМ ПРОГОНА, а не константой
#: стенда: константа рассказывала бы о правке, а поле — о ЭТОМ прогоне.
D1_STOP_SIGN = ("поле `termination` ответов /status и /applied-rules "
                "(вид `kind`, признак `reproducible`, слова `words`)")

#: Закрытый перечень видов остановки — контракт правки Д1 решателя.
#: Вид ВНЕ перечня стенд называет словами «вид вне закрытого перечня», а
#: не проглатывает: разошедшийся контракт должен быть виден.
TERMINATION_KINDS = {
    "step_count": "счётный предел шагов (воспроизводимо)",
    "unimproved_step_count": ("счётный предел неулучшающих шагов "
                              "(воспроизводимо)"),
    "wall_clock_safety": ("страховка стенных часов ВНУТРИ Д1: счётный "
                          "предел не наступил"),
    "wall_clock": "обычный прогон, свой предел времени",
    "search_exhausted": "фаза поиска кончилась сама раньше любого предела",
    "cancelled": "прогон отменён ручкой",
}

#: Формы ПОЛУЧЕННОГО (`got`) трёх видов решателя — что стенд кладёт в
#: строку результата, измерив живой прогон (`measure`). Публичны по
#: просьбе Ж-2: конвейер пополнения каталога (`webapp/catalog_pipeline`)
#: держал те же формы вторым ЛИТЕРАЛОМ, а второй источник закона о
#: форме без свидетеля молчалив — теперь источник один, и его сверяет с
#: живым каталогом тест-свидетель (ключи формы = ключи ожиданий этих
#: видов в `catalog/families.json`, `catalog/criteria.json`).
#:
#: Значения — ОБРАЗЦЫ формы, а не числа задачи: по ним агент видит тип
#: каждого поля. `amount: None` у формы логики сказан намеренно — вид
#: `kotlin_logic_form` меру не считает (ожидание `null` значит «этим
#: видом не судится»), а ключ `form` стенд кладёт лишь тогда, когда
#: служба отдала показания прогона: без них судья честно говорит
#: «исполнитель значения не дал», а не подставляет ожидание.
GOT_FORMS: dict = {
    "kotlin_constraint": {"constraint": "…", "matches": 0, "amount": 0},
    "kotlin_logic_form": {"form": "…", "matches": 0, "amount": None},
    "kotlin_metric": {"value": 0, "matches": 0, "refusal": False},
}

#: Находка живого прогона 08.09.2026 (решатель fb7614b, Timefold 2.0.0,
#: экземпляры 9099/9098/9097). МЕХАНИЗМ, а не только следствие: счётный
#: предел Timefold судится ТОЛЬКО между завершёнными шагами поиска, а
#: стенные часы — на каждом ходе. Поэтому Д1 ведёт себя на мирах стенда
#: по-разному, и от этого зависит правка на стороне решателя (страховка
#: стенными часами внутри Д1 либо ранний выход «исполнимых ходов нет»).
D1_FINDING = (
    "детерминированный режим Д1 (остановка по счётным событиям) "
    "останавливает прогон НЕ НА ВСЕХ мирах стенда. На мире БЕЗ "
    "исполнимого хода (один сотрудник, все назначения заданы) первый "
    "шаг поиска не завершается никогда — «No doable selected move» не "
    "наступает, ни один ход не принимается, — а счётный предел судится "
    "только между завершёнными шагами: прогон занимает поток пула "
    "навсегда (измерено на st-layover-1 при step_limit 1, 200 и "
    "unimproved_step_limit 1: «считает» и через 20 с, «Solving ended» в "
    "журнале нет). На мире, где принятый ход есть, шаг завершается и "
    "предел срабатывает (st-duty-1: «готов» за 0,53 с, «Local Search "
    "phase (1) ended … step total (1)», разбор даёт те же числа). "
    "Общей остановкой на всех мирах служат сегодня только стенные часы "
    "(они судятся на каждом ходе), и стенд идёт ими. Журнал механизма: "
    "_repro/step4solver/jvm9098.log, строки 13, 20, 26, 38")

#: Предел прогона стенными часами, секунды. Один — меньше ручка не
#: принимает, и больше стенду не нужно: РЕШЕНИЕ прогона стенд не читает
#: вовсе. Числа берутся повкладовым счётом НАЗНАЧЕНИЙ СТЕНДА под пакетом
#: этого прогона — чистой функцией (пакет, задача), от хода поиска, зерна
#: и предела не зависящей. Воспроизводимость измеряемых чисел этим
#: сохранена; невоспроизводимым остаётся только решение прогона, которого
#: стенд не касается.
LIMIT_S = 1

#: Ожидание состояния «готов»: шаг опроса и потолок. Прогон в один шаг
#: заканчивается за доли секунды — потолок здесь на случай занятого пула
#: прогонов чужой работой, и превышение его тоже отказ словами.
#: Значения читаются В МОМЕНТ ВЫЗОВА (`wait_ready` берёт их при
#: отсутствии аргумента), а не при определении функции: иначе это не
#: ручка — ни подмена в тесте, ни укорочение потолка стендом не
#: действовали бы, а тест «потолок срабатывает» честно ждал бы минуту.
POLL_S = 0.2
WAIT_S = 60.0

#: Потолок ожидания ПЕРВОГО прогона серии — проба пула. Пул прогонов
#: службы мал (`newFixedThreadPool(2)`), и занятый пул опознаётся именно
#: здесь: первый прогон, не дошедший до «готов» за этот срок, значит
#: «считать некому», и остальные строки видов решателя красные СЛОВАМИ
#: без единого нового прогона (иначе стенд поставил бы в чужую очередь
#: два десятка прогонов и ждал каждый полный потолок). Срок не секунда:
#: первый прогон свежей службы несёт разбор пакета и сборку фабрики
#: ограничений, и слишком короткая проба звала бы занятым живой пул.
PROBE_WAIT_S = 15.0

#: Кто выпустил пакет стенда. Заказчика у стенда нет и быть не может:
#: миры синтетические (коды ST/T/P/S), данных авиакомпании тут нет.
CUSTOMER = "стенд"
SOURCE_RUN = "run-stand"

#: Зона записи -> задача прогона. Зона BOTH судится ростерингом: у
#: пейринга оптимизация требует ещё и машинного критерия данных
#: авиакомпании (спецификация 1.9), а у стенда его нет ни у одной задачи,
#: кроме той, что критерий несёт миром.
ZONE_TASK = {"PAIRING": "pairing", "ROSTERING": "rostering",
             "BOTH": "rostering"}

#: Листы данных пакета: разделы, которых требует матрица готовности
#: (рейсы, экипажи, недоступности). Строки — из мира стенда, лист
#: недоступностей несёт явные crews[].unavailable_days, когда они объявлены.
UNAVAIL_SHEET = "Отпуска"


class StandSolverError(RuntimeError):
    """Живой прогон Kotlin-вида не состоялся — причина названа словами.

    Отказ ПОСЛЕ старта прогона несёт `run_id`: «ограничения нет в
    разборе», «форма логики не порождена», «повкладовый счёт не отдан»
    случаются, когда прогон в реестре службы УЖЕ есть, и красная строка
    обязана прослеживаться до него так же, как зелёная. Без этого
    идентификатор терялся ровно там, где прогон нужнее всего.

    `queued` — прогон остался в ОЧЕРЕДИ службы (ожидание не дождалось
    «готов»): факт о состоянии пула, по которому стенд решает не ставить
    в ту же очередь остальные прогоны.

    `unanswered` — ЖИВОГО ОТВЕТА СЛУЖБЫ об этом прогоне нет: сеть не
    дала ответа на постановке, состояние прогона не отдано, потолок
    ожидания истёк. По нему и только по нему считается ранний обрыв
    серии (просьба Ж-3): три таких подряд значат, что считать некому.
    Отказ ГЕЙТА решателя (пакет отвергнут) сюда НЕ входит — это законный
    ответ живой службы.

    `run_failed` — служба ОТВЕТИЛА: прогон упал на СВОЁМ пакете
    (состояние «упал»). Ранний обрыв на него не ставится сознательно
    (правка по ревью 08.09.2026): падение приходит первым же опросом и
    времени не стоит, значит обрыв ничего не экономит, а свидетельство
    прочих строк теряет; три подряд у одной записи — не случайность, а
    корреляция миров задач, и слова «решатель не отвечает» были бы
    ложью о живой службе.

    `out_of_time` — остаток бюджета команды (`--budget-s`) кончился
    ПОСРЕДИ строки: прогон дальше не ведётся, и слова строки судья
    выдаёт бюджетные.
    """

    def __init__(self, message, *, run_id=None, queued: bool = False,
                 unanswered: bool = False, run_failed: bool = False,
                 out_of_time: bool = False):
        super().__init__(message)
        self.run_id = run_id
        self.queued = bool(queued)
        self.unanswered = bool(unanswered)
        self.run_failed = bool(run_failed)
        self.out_of_time = bool(out_of_time)


def _with_run(exc: StandSolverError, run_id: str) -> StandSolverError:
    """Приписать отказу прогон, если он ещё не назван: звенья глубже
    (разбор счёта, показания) прогона не знают, а вызывающий знает."""
    if getattr(exc, "run_id", None) is None:
        exc.run_id = run_id
    return exc


# ------------------------------------------------------- имена и разбор

def logic_constraint_id(rule_id: str) -> str:
    """Имя порождённого ограничения формы логики: `logic <8 hex>` от
    идентификатора правила (`LogicCompiler.constraintIdFor`). Правило
    стенда одно на семью, поэтому имя выводится из его идентификатора, а
    не угадывается по форме."""
    digest = hashlib.sha256(str(rule_id).encode("utf-8")).hexdigest()
    return "logic " + digest[:8]


def _score_pair(text: str) -> tuple[int, int]:
    """«-120hard/0soft» -> (-120, 0). Форма счёта решателя — HardSoft."""
    hard = soft = 0
    for part in str(text).split("/"):
        part = part.strip()
        try:
            if part.endswith("hard"):
                hard = int(part[:-4])
            elif part.endswith("soft"):
                soft = int(part[:-4])
        except ValueError:
            raise StandSolverError(
                f"счёт ограничения «{text}» не читается как HardSoft: "
                f"разбор счёта отдал форму, которой стенд не знает")
    return hard, soft


def amount_of(row: dict) -> int:
    """Мера ограничения по строке разбора: |вклад| / вес.

    Вклад и вес приходят одной парой (жёсткая либо мягкая половина
    счёта), поэтому делится та половина, у которой вес не ноль. Вес
    ноль при ненулевом вкладе — не «мера ноль», а нечитаемая строка:
    делить не на что, и это отказ словами.
    """
    w_hard, w_soft = _score_pair(row.get("weight"))
    hard = int(row.get("hard") or 0)
    soft = int(row.get("soft") or 0)
    if w_hard:
        return abs(hard) // abs(w_hard)
    if w_soft:
        return abs(soft) // abs(w_soft)
    if not hard and not soft:
        return 0
    raise StandSolverError(
        f"вес ограничения нулевой ({row.get('weight')!r}), а вклад нет "
        f"({row.get('score')!r}): меру ограничения из такой строки не "
        f"вывести")


def constraint_row(breakdown: dict, name: str) -> dict:
    """Строка разбора по имени ограничения либо отказ словами.

    Ограничения нет в разборе — это РАСХОЖДЕНИЕ, а не пропуск: имя
    ожидания записи должно существовать у решателя, иначе ожидание
    неисполнимо и об этом надо сказать, а не показать ноль.
    """
    rows = breakdown.get("constraints")
    if not isinstance(rows, dict):
        raise StandSolverError(
            "разбор счёта не несёт карты constraints: ответ решателя не "
            "той формы, которую объявляет ручка score-breakdown")
    row = rows.get(name)
    if not isinstance(row, dict):
        known = ", ".join(sorted(rows)[:12]) or "пусто"
        raise StandSolverError(
            f"ограничения «{name}» нет в разборе счёта под пакетом "
            f"прогона; разбор назвал: {known}"
            + (" …" if len(rows) > 12 else ""))
    return row


# -------------------------------------------------------------- пакет

def _sheets(world: dict) -> dict:
    """Листы чистых данных для матрицы готовности пакета: рейсы,
    экипажи, недоступности. Явные даты отсутствия едут без потерь;
    старый мир без носителя сохраняет прежний пустой лист."""
    crews = sa.crews_of(world)
    flights = sa.flights_of(world)
    unavailability = sa.unavailability_table(world)
    return {
        "Рейсы": {"columns": ["Номер рейса", "Дата"],
                  "rows": [{"Номер рейса": str(f["id"]),
                            "Дата": sa._dt(f["dep"]).date().isoformat()}
                           for f in flights]},
        "Экипажи": {"columns": ["Табельный номер", "База приписки"],
                    "rows": [{"Табельный номер": str(c["id"]),
                              "База приписки": str(c["base"])}
                             for c in crews]},
        UNAVAIL_SHEET: (unavailability if unavailability is not None else
                       {"columns": ["Табельный номер", "Дата"], "rows": []}),
    }


def _goal_question(part: str, rule_ids: list, *, decline: bool) -> dict:
    """Вопрос о целевой части 3а/3б применённым ответом.

    Отказ от оптимизации (`goal_decline`) — законное состояние стенда у
    задач, где целевой нет: без применённого ответа матрица готовности
    держит столбец в `stop`, и пакет не выпускается вовсе.
    """
    raw = "Синтетическая декларация стенда (не ответ авиакомпании): " + (
        "любое допустимое решение" if decline
        else "критерий объявлен целевой пакета")
    task = {"3a": "пейринга", "3b": "ростеринга"}[part]
    question = {"id": f"Q-{part}-GOAL", "part": part,
            "title": f"Целевая {task}", "text": f"целевая функция {task}",
            "answer_format": {"type": "registry"},
            "rule_refs": list(rule_ids),
            "source_refs": [{"sid": "S1", "anchor": "стенд"}]}
    # This new revision-1 declaration comes from code, not an airline book.
    # Use the common provenance writer; never relabel saved customer answers.
    from cdpm.model import close_question_by_code
    close_question_by_code({"meta": {"revision": 1}}, question, raw=raw,
                           note="Целевая объявлена исходным синтетическим миром стенда")
    question["answer"]["goal_decline"] = bool(decline)
    return question


def objective_for(record: dict, *, world: dict) -> dict:
    """Целевая пакета для задачи вида `kotlin_metric`: метрика записи
    каталога в машинном поле `metric`.

    Целевая пейринга дополнительно несёт блок `criterion` мира, когда мир
    его объявил: без машинного критерия данных режим `optimization`
    пейринга снимается спецификацией 1.9, и отказ метрики (тот, который
    задача и ждёт словами) не успел бы прозвучать — вместо него пришёл бы
    отказ «режим не разрешён пакетом», то есть другое утверждение.
    """
    metric = str(record.get("id"))
    zone = str(record.get("zone") or "BOTH")
    sem = record.get("semantics") if isinstance(record.get("semantics"),
                                                dict) else {}
    out = {"id": f"O-STAND-{metric}", "zone": zone,
           "title": record.get("title") or metric,
           "statement": sem.get("text") or f"метрика {metric}",
           "direction": record.get("direction"), "metric": metric,
           "sources": [{"sid": "S1", "anchor": "стенд"}]}
    if out["direction"] not in ("minimize", "maximize"):
        raise StandSolverError(f"критерий {metric}: каталог не объявляет direction")
    # The declared synthetic source is the only authority for a metric rate.
    # Preserve the full carrier (including invalid data for the normal gates),
    # never derive a rate from an expected score or silently invent unit price.
    if "objective_rate" in world:
        out["rate"] = copy.deepcopy(world["objective_rate"])
    crit = world.get("criterion")
    if zone in ("PAIRING", "BOTH") and isinstance(crit, dict):
        block = dict(crit)
        block.setdefault("version", "1")
        block.setdefault("effective_from", "2026-10-01")
        block.setdefault("as_written", "критерий мира стенда")
        out["criterion"] = block
    return out


def _param_cell(cell) -> dict:
    """Клетка параметра посадки в форме ВМП: значение и ЦИТАТА.

    Форма CDPM хранит клетку парой (значение, цитата) и при обратном
    чтении ставит пустую строку вместо `null` — клетка без `quote`
    уезжала бы в пакет «с потерями», и сборка честно отказывала бы. У
    стенда цитаты источника нет (источник — сам мир), поэтому цитатой
    служит записанное значение.
    """
    value = cell.get("value") if isinstance(cell, dict) else cell
    quote = cell.get("quote") if isinstance(cell, dict) else None
    if not isinstance(quote, str) or not quote:
        quote = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False)
    out = copy.deepcopy(cell) if isinstance(cell, dict) else {"value": value}
    out["quote"] = quote
    return out


def _typed_stand_rule(rule: dict, world: dict | None, catalog) -> None:
    """New cells from a structured stand world, not migration of saved VMP."""
    from template_catalog import family_contract, landing, semantics
    from template_catalog.errors import CatalogError

    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        raise StandSolverError(f"{rule.get('id')}: стенд требует посадку A/B")
    family = fit.get("family")
    if fit.get("schema_version") != "landing/2":
        # Only the actual stand constructor's input may acquire a new pin.
        # An existing legacy model is not silently relanded by this adapter.
        if (not isinstance(world, dict) or rule.get("id") != f"R-STAND-{family}"
                or fit.get("registry_rev") != catalog.fingerprint()
                or "schema_version" in fit or "family_contract" in fit):
            raise StandSolverError(f"{rule.get('id')}: новая посадка требует "
                                   "исходный мир и точный каталог стенда")
        raw = sa.params_of(world)
        conditions = sa._world_conditions(world)
        cells = fit.get("params")
        if not isinstance(cells, dict):
            raise StandSolverError(f"{rule.get('id')}: params не объект")
        fit["schema_version"] = "landing/2"
        try:
            fit["family_contract"] = family_contract.snapshot(catalog, family, cells)
            for name, old in list(cells.items()):
                reading = semantics.cell_reading(catalog, "families", family, name)
                source_field = "conditions" if reading["kind"] == semantics.CELL_CONDITION else "params"
                origin = conditions if source_field == "conditions" else raw
                if name not in origin:
                    raise StandSolverError(f"{rule.get('id')}/{name}: нет значения в мире")
                value = copy.deepcopy(origin[name])
                legacy = sa._param_value(name, value)
                if (not isinstance(old, dict) or set(old) - {"value", "quote"}
                        or type(old.get("value")) is not type(legacy)
                        or old.get("value") != legacy):
                    raise StandSolverError(f"{rule.get('id')}/{name}: правило расходится с миром")
                unit = reading["fields"]["unit"]
                quote = json.dumps(value, ensure_ascii=False, allow_nan=False)
                if unit in ("duration_minutes", "time_of_day_minutes") and type(value) is int:
                    # A new synthetic source has an explicit catalogue unit.
                    # The compatible params reader also verifies its trace;
                    # a bare number would lose that unit and rightly fail.
                    # Existing landing/2 evidence is never rewritten here.
                    quote = f"{value} мин"
                cell = {"value": value,
                        "quote": quote,
                        "source": {"kind": "code", "note":
                                   f"vmp.stand_adapters: synthetic world.{source_field}.{name}"}}
                if unit == "time_of_day_minutes":
                    # The existing schedule adapter uses the base-local scale;
                    # no UTC conversion or guessed offset is introduced here.
                    cell["tz"] = "base-local"
                cells[name] = landing.prepare_cell(rule, name, cell, catalog)
            if family == "crew_composition" and "crew_size" in cells:
                # The catalogue describes both ordinary AND extended crews.
                # Its generic prose is not the stand world's global rule:
                # copying "расширенный" makes the real crew-size reader
                # correctly classify it as a conditional variant and omit it.
                rule["statement"] = (
                    f"Синтетический мир: численность места crew_size = "
                    f"{cells['crew_size']['value']} человек, без условия варианта.")
            # These are newly generated CODE sources, not airline quotations.
            # Render the exact adapter/typed-cell quotes as part of their own
            # source statement. A quote such as "780 мин" must not merely
            # point at JSON fields value=780/unit=duration_minutes elsewhere.
            if fit.get("quote"):
                rule["statement"] += "\nИсточник синтетического правила: " + str(fit["quote"])
            if cells:
                rule["statement"] += (
                    "\nЦитаты параметров исходного мира: "
                    + "; ".join(f"{name} = {cell['quote']}" for name, cell in sorted(cells.items())))
                # SMT's actual subject is id|statement, not template_fit or
                # params[]. A generic family description therefore loses the
                # world values even though the consumer's typed cells have them.
                # Render only this newly constructed world's verified cells;
                # never rewrite an existing landing/2 or infer task.expected.
                record = catalog.family(family)
                specs = {spec["name"]: spec for group in ("params", "conditions")
                         for spec in record.get(group) or ()}
                stated = []
                for name, cell in sorted(cells.items()):
                    reading = semantics.cell_reading(catalog, "families", family, name)
                    item = {"name": name, "kind": reading["kind"],
                            "qualifier": reading["fields"]["qualifier"],
                            **{key: copy.deepcopy(cell[key]) for key in
                               ("value", "unit", "relation", "condition")}}
                    if "tz" in cell:
                        item["tz"] = cell["tz"]
                    if (specs.get(name, {}).get("note")
                            and (family, name) != ("crew_composition", "crew_size")):
                        # E.g. night_duty_end is exclusive despite relation=max;
                        # do not invent <= or turn a window into a scalar bound.
                        # crew_size's generic note mentions an extended variant;
                        # the explicit unconditional stand declaration above must
                        # not acquire that different applicability condition.
                        item["note"] = specs[name]["note"]
                    stated.append(item)
                rule["statement"] += (
                    "\nПараметры исходного синтетического мира, уточняющие только это правило: "
                    + json.dumps(stated, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False))
        except (CatalogError, family_contract.ReferenceError, ValueError) as exc:
            raise StandSolverError(f"{rule.get('id')}: landing/2: {exc}") from exc
    problems = landing.problems(rule, catalog)
    if problems:
        raise StandSolverError("; ".join(p["words"] for p in problems))


def _stand_source(world, rules, objective, *, catalog=None) -> bytes:
    if catalog is None:
        from cdpm import catalog_bridge
        catalog = catalog_bridge.catalog()
    source_rules, _ = _stand_rules(rules, world, catalog)
    return json.dumps({"стенд": {"kind": "synthetic-code-world",
                       "world": world, "rules": source_rules, "objective": objective}},
                      ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _calendar_days_quote(rule: dict, world: dict) -> bool:
    """Дни окна семьи `calendar_windows` — в ЦИТАТУ правила пакета.

    Возвращает «повёз или нет»: пустые дни — не повёз, и молчаливым это
    не остаётся (судит `_carry_section`, правка по ревью 08.09.2026 —
    прежде носитель возвращался молча, а вызывающий считал секцию
    доехавшей по одному факту вызова).

    Рукотворное поле `export_solver` сборка пакета ПЕРЕСЧИТЫВАЕТ
    (`cdpm.model.assign_soft_families`), а дни окна выводит из
    формулировки и цитаты по закрытому словарю дней недели
    (`SOFT_FAMILY_DAYS`). Положить дни только в `export_solver` — значит
    отдать пакет без формы окна: `threshold-forms.json` не соберётся, и
    ограничение календарного окна у решателя останется без фактов
    (счёт 0 по ложной причине, а не «правила нет»).

    Слова дней берутся из ТОГО ЖЕ словаря CDPM обратным чтением — своих
    слов стенд не сочиняет; мир объявляет дни, носитель их довозит.
    """
    days = list((world.get("rule_form") or {}).get("days") or ())
    if not days:
        return False
    from cdpm.model import SOFT_FAMILY_DAYS
    back = {}
    for ru, eng in SOFT_FAMILY_DAYS.items():
        back.setdefault(eng, ru)
    words = [back[str(d)] for d in days if str(d) in back]
    if len(words) != len(days):
        raise StandSolverError(
            f"дни окна мира {days} вне закрытого словаря дней CDPM "
            f"({', '.join(sorted(set(SOFT_FAMILY_DAYS.values())))}): "
            f"довезти форму окна до решателя нечем")
    rule["template_fit"]["quote"] = (
        str(rule["template_fit"].get("quote") or "стенд")
        + ": " + ", ".join(words))
    return True


#: Носители секций пакета: имя из таблицы `stand_adapters
#: .WORLD_TO_PACKAGE_SECTION` -> функция, которая довозит поле мира до
#: посадки правила. Имён здесь ровно столько, сколько назвала таблица;
#: строка таблицы с носителем, которого тут нет, — отказ словами, а не
#: молчаливый пропуск (просьба Ж-5).
SECTION_CARRIERS = {"quote_days": _calendar_days_quote}


def _carry_section(rule: dict, world: dict, family: str) -> bool:
    """Довезти объявленную МИРОМ форму правила до секции пакета.

    Что именно везётся и куда — сказано ОДНОЙ таблицей
    (`stand_adapters.WORLD_TO_PACKAGE_SECTION`), а не условием здесь:
    новая секция пакета (вид `kotlin_form`) судится близнецами без
    правки стенда. Возвращает «повёз или нет» ПО ФАКТУ — тем, что вернул
    носитель, а не по факту его вызова (правка по ревью 08.09.2026:
    прежде «повёз» ставилось безусловно, и поле мира, которого строка
    таблицы не называет, уходило молча — пакет без секции, близнецы с
    одинаковыми числами).

    Семью вне таблицы здесь молчаливо пропускаем НЕ ЗРЯ — у пакета семей
    столько, сколько параметров назвал мир, и форму правила несёт ровно
    одна из них; судит это `require_section` разом на задачу.
    """
    row = sa.package_section_row(family)
    if row is None:
        return False
    try:
        name = sa.section_carrier(family, world)
    except sa.StandWorldError as e:
        # Слова адаптера точнее наших: он называет и поля мира, и место,
        # где заводится строка таблицы.
        raise StandSolverError(str(e)) from None
    if not name:
        return False
    carrier = SECTION_CARRIERS.get(name)
    if carrier is None:
        raise StandSolverError(
            f"секция пакета «{row.get('section')}» из мира стенда в пакет "
            f"не собирается: таблица «поле мира → секция пакета» назвала "
            f"носителя {name!r}, а в стенде его нет — стенд знает "
            f"{', '.join(sorted(SECTION_CARRIERS)) or 'ни одного'}")
    if not carrier(rule, world):
        raise StandSolverError(
            f"секция пакета «{row.get('section')}» из мира стенда в пакет "
            f"не собирается: носитель {name!r} поля мира "
            f"{row.get('world_field')!r} не повёз ничего, хотя мир объявил "
            f"форму правила ({', '.join(sa.world_section_fields(world))}) "
            f"— молчаливого пропуска у секции пакета не бывает")
    return True


def promised_sections(world: dict, rules: list) -> list:
    """Секции `threshold-forms.json`, которые пакет ОБЯЗАН нести.

    Считается по той же таблице и тому же своду, которыми собирается
    пакет: семья свода названа таблицей, а мир объявил форму правила —
    значит, секция обязана быть В ПАКЕТЕ, а не только в обещании
    носителя. Проверку исполняет `package_bytes`: посадка правила
    пересчитывается сборкой CDPM, и «носитель вызван» ещё не значит
    «секция собралась».
    """
    if not sa.world_section_fields(world):
        return []
    out = set()
    for rule in rules:
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        family = str((fit or {}).get("family") or "")
        row = sa.package_section_row(family) if family else None
        if row:
            out.add(str(row.get("section")))
    return sorted(out)


def require_section(world: dict, family: str | None, *,
                    carried: bool = False) -> None:
    """Мир объявил форму правила — значит, секция пакета обязана быть.

    Один суд на задачу (просьба Ж-5): семья задачи есть в таблице «поле
    мира → секция пакета» — форма доезжает; семьи в таблице нет (и ни
    одно правило пакета формы не повезло) — отказ СЛОВАМИ «секция … из
    мира стенда в пакет не собирается». Молчаливый пропуск здесь дал бы
    близнецам вида `kotlin_form` одинаковые числа: пакет ушёл бы без
    формы, ограничение решателя осталось бы без фактов, и обе строки
    зеленели бы по построению.
    """
    fields = sa.world_section_fields(world)
    if not fields or carried:
        return
    if family:
        # Семья задачи названа — суд точный и словами адаптера: он
        # называет и поля мира, и место, где заводится строка таблицы.
        try:
            sa.section_carrier(family, world)
        except sa.StandWorldError as e:
            raise StandSolverError(str(e)) from None
        row = sa.package_section_row(family)
        if row is not None:
            # Семья В ТАБЛИЦЕ, а везти было нечем: виноват СОСТАВ СВОДА
            # пакета, а не таблица (правка по ревью 08.09.2026 — прежде
            # совет был «назовите там семью задачи», хотя она названа, и
            # читателя это вело не туда).
            raise StandSolverError(
                f"секция пакета «{row.get('section')}» (поле мира "
                f"{row.get('world_field')}) из мира стенда в пакет не "
                f"собирается: семья «{family}» таблицей «поле мира → "
                f"секция пакета» НАЗВАНА, но правила этой семьи в своде "
                f"пакета нет — везти секцию некому. Свод собирается по "
                f"семьям, чьи параметры назвал мир задачи "
                f"(stand_tasks.families_for): проверьте параметры мира и "
                f"саму запись семьи в каталоге")
    named = ", ".join(sorted(sa.WORLD_TO_PACKAGE_SECTION)) or "ни одной"
    raise StandSolverError(
        f"секция пакета для формы правила (поля мира: {', '.join(fields)}) "
        f"из мира стенда в пакет не собирается: ни одно правило пакета её "
        f"не повезло, а таблица «поле мира → секция пакета» "
        f"(vmp.stand_adapters.WORLD_TO_PACKAGE_SECTION) называет семьи "
        f"{named} — назовите там семью задачи, поле мира, секцию "
        f"threshold-forms.json и носителя")


def _stand_rules(rules: list, world: dict | None, catalog) -> tuple[list, bool]:
    """One derivation for emitted rules and their real synthetic source."""
    out_rules = []
    carried = False
    for rule in rules:
        r = copy.deepcopy(rule)
        r["sources"] = [{"sid": "S1", "anchor": "стенд"}]
        if r.get("kind") != "SOFT":
            # Вес — свойство МЯГКОЙ строки: у жёсткой форма ВМП его не
            # держит, и пакет собирался бы «с потерями» (гейт формы
            # CDPM ловит это отказом, а не молча).
            r.pop("weight", None)
        fit = r.get("template_fit")
        if (isinstance(fit, dict) and fit.get("schema_version") != "landing/2"
                and isinstance(fit.get("params"), dict)):
            fit["params"] = {name: _param_cell(cell)
                             for name, cell in fit["params"].items()}
        if isinstance(fit, dict) and fit.get("family") \
                and isinstance(world, dict):
            # Секции пакета, которые стенд несёт ИЗ МИРА, — по таблице
            # (Ж-5), а не условием по имени семьи: новая секция даётся
            # строкой таблицы, а не правкой сборщика пакета.
            carried = _carry_section(r, world, str(fit.get("family"))) \
                or carried
        out_rules.append(r)
    for rule in out_rules:
        _typed_stand_rule(rule, world, catalog)
    return out_rules, carried


def model_doc(rules: list, *, world: dict | None = None,
              objective: dict | None = None,
              family: str | None = None, catalog=None) -> dict:
    """Native stand model; emitted source and model share actual typed rules.

    No stored model is repaired. New rules are derived only from the declared
    world/catalogue, never from task expectations or a previous measurement.
    """
    if catalog is None:
        from cdpm import catalog_bridge
        catalog = catalog_bridge.catalog()
    out_rules, carried = _stand_rules(rules, world, catalog)
    if isinstance(world, dict):
        require_section(world, family, carried=carried)
    ids = [str(r.get("id")) for r in out_rules] or ["R-STAND"]
    obj_zone = str((objective or {}).get("zone") or "")
    doc = {"meta": {"customer": CUSTOMER, "mode": 1, "cdpm_version": "0.1",
                    "revision": 1},
           "sources": [{"sid": "S1", "file": "stand-world.json",
                        "sha256": hashlib.sha256(_stand_source(
                            world, rules, objective, catalog=catalog)).hexdigest()}],
           "rules": out_rules,
           "questions": [
               _goal_question("3a", ids,
                              decline=obj_zone not in ("PAIRING", "BOTH")),
               _goal_question("3b", ids,
                              decline=obj_zone not in ("ROSTERING", "BOTH"))],
           "objectives": [copy.deepcopy(objective)] if objective else []}
    return doc


def _write_run(base: Path, doc: dict, sheets: dict, *, source: bytes | None = None) -> Path:
    run = Path(base) / SOURCE_RUN
    (run / "extract").mkdir(parents=True, exist_ok=True)
    if source is not None:
        (run / "extract" / "stand-world.json").write_bytes(source)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text(
        "# свод стенда каталога шаблонов\n", encoding="utf-8")
    (run / "data-freeze").mkdir(exist_ok=True)
    (run / "data-report").mkdir(exist_ok=True)
    (run / "data-freeze" / "dataset.json").write_text(
        json.dumps({"tables": {"D1": sheets}}, ensure_ascii=False),
        encoding="utf-8")
    (run / "data-report" / "data-report.json").write_text(
        json.dumps({"findings": [], "checks": []}, ensure_ascii=False),
        encoding="utf-8")
    return run


#: Файл машинных форм пакета: там живут секции, которые стенд несёт из
#: мира задачи (таблица `stand_adapters.WORLD_TO_PACKAGE_SECTION`).
FORMS_FILE = "threshold-forms.json"


def _require_sections_in_package(files: dict, world: dict,
                                 rules: list) -> None:
    """Обещанная таблицей секция обязана быть В СОБРАННОМ ПАКЕТЕ.

    Носитель кладёт факт мира в посадку правила, а секцию машинных форм
    собирает уже CDPM (`assign_soft_families` пересчитывает рукотворное
    `export_solver`): «носитель вызван» и «секция собралась» — разные
    утверждения, и до правки по ревью 08.09.2026 стенд судил только
    первое. Пустая либо отсутствующая секция — отказ СЛОВАМИ: пакет без
    формы оставил бы ограничение решателя без фактов, и близнецы вида
    `kotlin_form` зеленели бы по построению.
    """
    want = promised_sections(world, rules)
    if not want:
        return
    try:
        forms = json.loads((files.get(FORMS_FILE) or b"{}").decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        raise StandSolverError(
            f"машинные формы пакета ({FORMS_FILE}) не читаются "
            f"({type(e).__name__}): {e}")
    forms = forms if isinstance(forms, dict) else {}
    missing = [name for name in want if not forms.get(name)]
    if missing:
        built = ", ".join(sorted(forms)) or "ни одной"
        raise StandSolverError(
            f"секция пакета «{', '.join(missing)}» из мира стенда до "
            f"{FORMS_FILE} не доехала (поля мира: "
            f"{', '.join(sa.world_section_fields(world))}; собранные "
            f"секции: {built}): ограничение решателя осталось бы без "
            f"фактов, и близнецы вида kotlin_form дали бы одинаковые "
            f"числа — проверьте носителя строки таблицы «поле мира → "
            f"секция пакета»")


def package_bytes(world: dict, rules: list, *, stamp: dict, base: Path,
                  objective: dict | None = None,
                  family: str | None = None, catalog=None,
                  spec_version: str | None = None) -> bytes:
    """Байты НАСТОЯЩЕГО пакета постановки для мира стенда.

    Сборка — библиотекой CDPM (`export_task_package.export_zip`), как у
    любого пакета продукта. Выбранный каталог и каталог производителя
    должны совпадать ДО сборки; чужая квитанция каталога не наклеивается
    на уже собранный пакет. Каталог/поддержка/форма остаются обязательными.
    По решению владельца 18.09 явный untranslatable с причиной допускается
    только здесь, в синтетическом стенде: отчёт остаётся без вердикта,
    пакет помечается stand_only и отвергается обычным клиентским гейтом.
    Ошибка канала/формы/парсера и противоречивая переведённая часть не
    получают такого допуска. Контекст создаётся вызовом, не входным JSON.
    """
    from cdpm import export_task_package as tp
    catalog = _producer_catalog(stamp, catalog)
    run = _write_run(Path(base),
                     model_doc(rules, world=world, objective=objective,
                               family=family, catalog=catalog),
                     _sheets(world), source=_stand_source(world, rules, objective))
    try:
        context = tp._StandExportContext(
            run, (run / "extract" / "model.json").read_bytes(),
            (run / "extract" / "stand-world.json").read_bytes())
        body = tp.export_zip(run, spec_version=spec_version, _stand_context=context)
    except Exception as e:                                   # noqa: BLE001
        raise StandSolverError(
            f"пакет постановки стенда не собрался ({type(e).__name__}): {e}")
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    _require_sections_in_package(files, world, rules)
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    if manifest.get("catalog") != stamp:
        raise StandSolverError("производитель вернул иной каталог: пакет не перемаркирован")
    return body


def _producer_catalog(stamp: dict, selected=None):
    from cdpm import catalog_bridge
    try:
        current = catalog_bridge.catalog()
    except catalog_bridge.CatalogRequired as exc:
        raise StandSolverError(f"каталог производителя не прочитан: {exc}") from exc
    wanted = stamp_of(selected) if selected is not None else stamp
    if stamp != wanted or stamp_of(current) != wanted:
        path = str(selected.path) if selected is not None else "<путь --catalog>"
        raise StandSolverError(
            f"каталог стенда {wanted} не совпадает с каталогом производителя "
            f"{stamp_of(current)}; задайте CREW_TEMPLATE_CATALOG={path} "
            "до запуска стенда; manifest не перемаркируется")
    return current if selected is None else selected


# ------------------------------------------------------------- решатель

#: Потолок ОДНОГО обращения к службе, секунды. Своим остатком бюджета
#: строка может его только УКОРОТИТЬ (`set_call_timeout`), но не
#: удлинить: предел команды — не разрешение ждать чужую службу дольше,
#: чем ждёт кнопка.
CALL_TIMEOUT_S = 120.0


def solver_client(url: str, *, timeout_s: float = CALL_TIMEOUT_S):
    """Клиент решателя — ТОТ ЖЕ, которым ходит кнопка.

    Второго HTTP-клиента решателя в дереве нет и заводить его нельзя:
    ручки, коды отказа и форма ответа должны быть одни на все пути.
    """
    if not str(url or "").strip():
        raise StandSolverError(
            "адреса решателя нет: живой прогон Kotlin-видов идёт только "
            "ключом --with-solver <url>, умолчания у адреса не бывает")
    from vmp.run.button import SolverHttp
    return SolverHttp(str(url).strip(), timeout_s=timeout_s)


def _refusal_text(exc: Exception) -> str:
    """Слова отказа решателя из тела ответа: разбирается JSON ошибки,
    иначе текст как есть."""
    text = str(getattr(exc, "detail", "") or exc)
    try:
        body = json.loads(text)
    except Exception:                                        # noqa: BLE001
        return text
    if isinstance(body, dict):
        for key in ("detail", "message", "error", "reason"):
            if isinstance(body.get(key), str) and body[key].strip():
                return body[key]
    return text


def start_run(client, problem: dict, package: bytes, *, task: str,
              mode: str | None, objective_id: str | None,
              deterministic: dict | None = None) -> dict:
    """Прогон по идентификатору: пакет + задача решателя.

    Отказ гейта решателя (пакет отвергнут, режим не разрешён, метрика
    отказывает) поднимается СЛОВАМИ решателя — свои формулировки поверх
    чужого отказа стенд не сочиняет.

    `deterministic` уезжает решателю ТОЛЬКО когда он задан ключом
    (профиль Д1, по умолчанию выключен): пустой параметр в запросе — это
    другой запрос, и умолчание «как было» обязано быть побайтово прежним.

    ОТКАЗ ГЕЙТА И МОЛЧАНИЕ СЛУЖБЫ РАЗЛИЧАЮТСЯ (правка по ревью
    08.09.2026): `ButtonStop` клиент поднимает на ответ службы (HTTP не
    200, режим Д1 без счётного предела) — это живой ответ, и серию он не
    обрывает; всё остальное (соединение не встало, чтение не дождалось)
    — молчание службы, признак `unanswered`, по которому считается
    ранний обрыв. Прежде транспортный отказ приходил без признака и
    СБРАСЫВАЛ счётчик: стенд ждал по потолку клиента на каждой из
    оставшихся строк — ровно в самом буквальном случае «решатель не
    отвечает».
    """
    mode = _package_mode(package, task=task, mode=mode, objective_id=objective_id)
    zip_b64 = base64.b64encode(package).decode("ascii")
    extra = {"deterministic": dict(deterministic)} if deterministic else {}
    try:
        return client.start(problem, zip_b64, mode, objective_id, LIMIT_S,
                            task=task, **extra)
    except Exception as e:                                   # noqa: BLE001
        from vmp.run.button import ButtonStop
        raise StandSolverError(_refusal_text(e),
                               unanswered=not isinstance(e, ButtonStop))


def _package_mode(package: bytes, *, task: str, mode, objective_id):
    """Read the real package's permissions; never demote a soft/metric test."""
    from cdpm.model import loads_strict
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as z:
            manifest = loads_strict(z.read("manifest.json").decode("utf-8"))
            doc = loads_strict(z.read("model.json").decode("utf-8"))
        allowed = manifest["allowed_modes"][task]
        rules = doc["rules"]
        if (not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed)
                or not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules)):
            raise ValueError("неверная форма allowed_modes/rules")
        soft = any(r.get("kind") == "SOFT" and r.get("zone") in
                   (task.upper(), "BOTH") for r in rules)
        wanted = mode or ("feasibility-soft" if soft or "feasibility-soft" in allowed
                          else "feasibility-hard")
        if objective_id is not None:
            if wanted != "optimization" or objective_id not in manifest["objectives"][task]:
                raise ValueError("метрика требует разрешённую целевую и optimization")
        elif wanted == "optimization":
            raise ValueError("optimization метрики требует objective_id")
        if wanted not in allowed:
            raise ValueError(f"режим {wanted} не разрешён пакетом для {task}; "
                             "смысл проверки не заменяется другим режимом")
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise StandSolverError(f"режим пакета стенда: {exc}") from exc
    # Keep the established wire default only when its exact meaning is allowed.
    return None if mode is None and wanted == "feasibility-soft" else wanted


def wait_ready(client, run_id: str, *, wait_s: float | None = None,
               poll_s: float | None = None, sleep=time.sleep) -> dict:
    """Ждать состояния «готов»; «упал» и истёкшее ожидание — отказ.

    Потолок и шаг опроса берутся В МОМЕНТ ВЫЗОВА: `WAIT_S`/`POLL_S` —
    ручки модуля, а не значения, застывшие при определении функции.
    Прежняя редакция брала их умолчанием аргумента, поэтому подмена
    ручки не действовала ни в тесте, ни у вызывающего.

    Признаки отказов РАЗНЫЕ (правка по ревью 08.09.2026): состояние не
    отдано и потолок истёк — `unanswered` (живого ответа об этом прогоне
    нет, по нему считается ранний обрыв серии); «упал» — `run_failed`
    (служба ОТВЕТИЛА о неудаче ЭТОГО пакета, обрыва серии на нём нет).
    """
    wait_s = WAIT_S if wait_s is None else float(wait_s)
    poll_s = POLL_S if poll_s is None else float(poll_s)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            status = client.status(run_id)
        except Exception as e:                               # noqa: BLE001
            raise StandSolverError(
                f"состояние прогона не отдано: {_refusal_text(e)}",
                run_id=run_id, unanswered=True)
        state = status.get("state")
        if state == "готов":
            return status
        if state == "упал":
            raise StandSolverError(
                "прогон упал: "
                f"{status.get('failure') or 'причина не названа'}",
                run_id=run_id, run_failed=True)
        if time.monotonic() > deadline:
            raise StandSolverError(
                f"прогон не дошёл до состояния «готов» за {wait_s:g} с: "
                f"состояние «{state}»; прогон остался в очереди решателя "
                f"и держит поток его пула",
                run_id=run_id, queued=True, unanswered=True)
        sleep(poll_s)


def breakdown_of(client, run_id: str, problem: dict) -> dict:
    """Повкладовый счёт назначений стенда ПОД ПАКЕТОМ этого прогона."""
    try:
        out = client.score_breakdown(run_id, problem)
    except Exception as e:                                   # noqa: BLE001
        raise StandSolverError(
            "повкладовый счёт прогона не отдан: "
            f"{_refusal_text(e)}", run_id=run_id)
    if not isinstance(out, dict):
        raise StandSolverError(
            "повкладовый счёт прогона пришёл не объектом",
            run_id=run_id)
    return out


def logic_form_of(client, run_id: str, name: str) -> tuple:
    """ФОРМА порождённого ограничения — из показаний ПРОГОНА.

    Возвращает `(форма, почему не измерена)`: ровно одно из двух не
    `None`. Форму отдаёт сама служба — снимок плана компилятора логики в
    `GET /schedules/run/{id}/applied-rules` → `lastRun.logic.plans[]`
    (`LogicPlan.snapshot`: `constraint`, `form`, …). Имя ограничения
    выводится из ИДЕНТИФИКАТОРА ПРАВИЛА, поэтому две задачи одной семьи
    судятся под одним именем, и без плана форма ожидания оставалась бы
    непроверенной: разные формы (`DAYS_OFF_PER_MONTH_MIN` и
    `DAYS_OFF_IN_ROLLING_WINDOW_MIN`) давали бы одну зелёную строку.

    Показаний служба не дала (старая ручка, отказ) — это НЕ отказ вида:
    форма просто не измерена, и вызывающий не кладёт её в полученное —
    судья честно скажет «исполнитель значения не дал». А вот планы,
    среди которых нужного имени НЕТ, — расхождение словами: ограничение
    порождено, а плана у него нет, и такие показания молча пропускать
    нельзя.
    """
    try:
        applied = client.applied(run_id)
    except Exception as e:                                   # noqa: BLE001
        return None, ("показания прогона не отданы: " + _refusal_text(e))
    last = (applied or {}).get("lastRun")
    logic = (last or {}).get("logic") if isinstance(last, dict) else None
    plans = (logic or {}).get("plans") if isinstance(logic, dict) else None
    if not isinstance(plans, list):
        return None, ("показания прогона не несут планов логики "
                      "(lastRun.logic.plans)")
    for plan in plans:
        if isinstance(plan, dict) and str(plan.get("constraint")) == name:
            return plan.get("form"), None
    known = ", ".join(sorted(
        str(p.get("constraint")) for p in plans
        if isinstance(p, dict))) or "ни одного"
    raise StandSolverError(
        f"плана порождённого ограничения «{name}» нет в показаниях "
        f"прогона; показания назвали планы: {known}", run_id=run_id)


def close_client(client) -> None:
    """Закрыть пул соединений клиента решателя.

    У `SolverHttp` своего `close()` нет, а его файл — файл кнопки: новый
    метод там заводится по слову владельца, не правкой стенда. Поэтому
    закрывается внутренний клиент, и молча это не делается — функция
    названа и вызывается ровно там, где серия прогонов кончилась.
    """
    inner = getattr(client, "_c", None)
    if inner is not None and hasattr(inner, "close"):
        with contextlib.suppress(Exception):
            inner.close()


# ------------------------------------------------------------- прогон вида

def _rules_for(world: dict, families: list, meta: dict) -> list:
    svod, _ = sa.world_to_certificate(world, families, meta=meta)
    return list(svod.get("rules") or ())


def _task_of(zone: str) -> str:
    return ZONE_TASK.get(str(zone or "BOTH"), "rostering")


def termination_words(status) -> str:
    """Признак остановки ЭТОГО прогона словами либо честное «не назван».

    Поле `termination` появилось правкой Д1 решателя (`D1_STOP_SIGN`).
    Службы без правки отвечают без него — и это НЕ отказ вида: стенд
    говорит «признак не назван: сборка без правки Д1», а не молчит и не
    выдумывает вид остановки.

    Слова эти живут ПОЛЕМ строки (`termination_row`), а НЕ в `note`
    (правка по ревью 08.09.2026). Причина — секундомер: страховка
    стенных часов внутри Д1 судится РАНЬШЕ счётного предела (зазор 250
    мс правки решателя), и один и тот же профиль на одном мире даёт то
    `step_count`/`воспроизводимо: да`, то `wall_clock_safety`/`нет` — от
    прогона к прогону. `note` уезжает в `stand_verified` привязок и
    обязан быть каноном КАТАЛОГА: изменчивых слов в нём нет так же, как
    изменчивых чисел (шаги, миллисекунды сюда не берутся и подавно).
    """
    block = status.get("termination") if isinstance(status, dict) else None
    if not isinstance(block, dict):
        return ("признак остановки службой не назван: сборка решателя без "
                "правки Д1 (поля termination в ответе нет)")
    kind = str(block.get("kind") or "")
    known = TERMINATION_KINDS.get(kind)
    out = "остановка: " + (f"{kind} — {known}" if known else
                           f"{kind!r} — вид вне закрытого перечня стенда "
                           f"({', '.join(sorted(TERMINATION_KINDS))})")
    if "reproducible" in block:
        out += "; воспроизводимо: %s" % (
            "да" if block.get("reproducible") else "нет")
    return out


def termination_row(status) -> dict | None:
    """Признак остановки ПОЛЕМ строки либо None (служба поля не дала).

    Поле, а не слова `note` (правка по ревью 08.09.2026): вид остановки
    и признак воспроизводимости зависят от секундомера прогона, а `note`
    уезжает в `stand_verified` привязок — канон КАТАЛОГА дрожать не
    должен. Поля строки (`run_id`, `package_fingerprint`, `termination`)
    — паспорт ПРОГОНА: их `bindings_build` не копирует, и печатная
    таблица показывает их хвостом строки.
    """
    block = status.get("termination") if isinstance(status, dict) else None
    if not isinstance(block, dict):
        return None
    out = {"kind": str(block.get("kind") or ""),
           "words": termination_words(status)}
    if "reproducible" in block:
        out["reproducible"] = bool(block.get("reproducible"))
    if str(block.get("words") or "").strip():
        # Слова САМОЙ службы — рядом, а не вместо: контракт решателя
        # объясняет остановку подробнее закрытого перечня стенда.
        out["solver_words"] = str(block["words"]).strip()
    return out


def d1_words(*, refusal: bool = False) -> str:
    """Слова о профиле Д1 для строки стенда — только УСТОЙЧИВАЯ голова.

    Признак остановки сюда НЕ приписывается (правка по ревью
    08.09.2026): он живёт полем строки (`termination_row`), потому что
    зависит от секундомера — при `LIMIT_S = 1` тот же профиль на том же
    мире даёт то счётный предел, то страховку стенных часов внутри Д1.
    Стенд это ПРИНИМАЕТ сознательно: решения прогона он не читает вовсе,
    числа берутся разбором счёта назначений стенда под пакетом прогона.
    """
    head = "профиль Д1 задан ключом --deterministic (%s)" % ", ".join(
        f"{k} {v}" for k, v in sorted(DETERMINISTIC.items()))
    if refusal:
        return (f"{head}; правка остановки Д1 на стороне решателя есть "
                f"({D1_STOP_SIGN}) — служба, отвечающая без этого поля, "
                f"старой сборки, и тогда отказ может быть находкой "
                f"D1_FINDING, а не мёртвой службой")
    return head


def set_call_timeout(client, seconds) -> None:
    """Потолок ОДНОГО обращения к службе — от остатка бюджета строки.

    Боевой клиент кнопки умеет менять потолок на лету
    (`SolverHttp.set_timeout`, добавлено правкой по ревью 08.09.2026):
    без этого начатая у самой границы бюджета строка ждала бы полный
    потолок клиента (120 с) на каждом из трёх обращений — «стенд
    укладывается в предел команды сам» было бы неправдой.

    Потолок только УКОРАЧИВАЕТСЯ: больше `CALL_TIMEOUT_S` он не станет
    ни при каком остатке — большой бюджет команды не разрешение ждать
    чужую службу дольше, чем ждёт кнопка.

    Двойник теста метода может не иметь — тогда потолок остаётся его
    собственным, и это не отказ: строку сторожат ещё и стендовые крайние
    сроки (`wait_s`, остаток `left_s`).
    """
    setter = getattr(client, "set_timeout", None)
    if seconds is None or setter is None:
        return
    with contextlib.suppress(Exception):
        setter(max(0.05, min(float(seconds), CALL_TIMEOUT_S)))


def _left_or_stop(deadline, *, stage: str, run_id=None):
    """Остаток бюджета СТРОКИ перед звеном либо отказ словами."""
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise StandSolverError(
            f"остаток бюджета команды кончился {stage}: строка дальше не "
            f"ведётся", run_id=run_id, out_of_time=True)
    return left


def run_world(world: dict, *, families: list, meta: dict, stamp: dict,
              base: Path, client, task: str, mode: str | None = None,
              objective: dict | None = None,
              wait_s: float | None = None,
              family: str | None = None,
              deterministic: dict | None = None,
              left_s: float | None = None, catalog=None,
              spec_version: str | None = None) -> dict:
    """Один живой прогон стенда: пакет, задача, ожидание, разбор счёта.

    spec_version выбирает контракт пакета, не меняя глобальное умолчание
    производителя, требования поддержки или порядок утверждения выпуска.

    Возвращает `{"run_id", "package_fingerprint", "breakdown", "rules",
    "problem", "status"}`. Все отказы — `StandSolverError` словами, и
    отказ ПОСЛЕ старта несёт `run_id`: красная строка обязана
    прослеживаться до прогона службы так же, как зелёная.

    `left_s` — ОСТАТОК бюджета команды (`--budget-s`) на эту строку
    (правка по ревью 08.09.2026). Из него считается крайний срок строки:
    каждое звено получает потолок не больше остатка (`set_call_timeout`),
    ожидание «готов» — не дольше остатка, а кончившийся посреди строки
    остаток — отказ словами с признаком `out_of_time`. Прежде бюджет
    судился ТОЛЬКО перед постановкой прогона, и начатая у границы строка
    могла идти ещё до ~360 с сверх предела — обещание «стенд укладывается
    в предел команды сам» держалось на удаче.
    """
    rules = _rules_for(world, families, meta)
    package = package_bytes(world, rules, stamp=stamp, base=Path(base),
                            objective=objective, family=family, catalog=catalog,
                            **({"spec_version": spec_version}
                               if spec_version is not None else {}))
    problem = sa.world_to_problem(world)
    deadline = None if left_s is None else time.monotonic() + float(left_s)
    set_call_timeout(client, _left_or_stop(
        deadline, stage="перед постановкой прогона"))
    started = start_run(client, problem, package, task=task, mode=mode,
                        objective_id=(objective or {}).get("id"),
                        deterministic=deterministic)
    run_id = str(started.get("runId") or "")
    if not run_id:
        raise StandSolverError(
            "решатель принял пакет, но не назвал runId: продолжать нечем")
    try:
        left = _left_or_stop(deadline, stage="перед ожиданием «готов»",
                             run_id=run_id)
        set_call_timeout(client, left)
        status = wait_ready(client, run_id, wait_s=_capped_wait(wait_s, left))
        left = _left_or_stop(deadline, stage="перед разбором счёта",
                             run_id=run_id)
        set_call_timeout(client, left)
        breakdown = breakdown_of(client, run_id, problem)
    except StandSolverError as e:
        if deterministic and (getattr(e, "unanswered", False)
                              or getattr(e, "run_failed", False)):
            # Отказ ожидания ПОД профилем Д1 называет профиль словами:
            # иначе читатель ищет мёртвую службу там, где виновата
            # известная находка остановки.
            e = StandSolverError(
                f"{e}; {d1_words(refusal=True)}",
                run_id=getattr(e, "run_id", None),
                queued=getattr(e, "queued", False),
                unanswered=getattr(e, "unanswered", False),
                run_failed=getattr(e, "run_failed", False))
        raise _with_run(e, run_id)
    return {"run_id": run_id,
            "package_fingerprint": started.get("packageFingerprint"),
            "breakdown": breakdown, "rules": rules, "problem": problem,
            "status": status}


def _capped_wait(wait_s, left):
    """Потолок ожидания «готов» с оглядкой на остаток бюджета строки."""
    if left is None:
        return wait_s
    return min(WAIT_S if wait_s is None else float(wait_s), float(left))


def measure(kind: str, *, world: dict, expected: dict, families: list,
            meta: dict, stamp: dict, base: Path, client,
            record: dict | None = None, family: str | None = None,
            wait_s: float | None = None,
            deterministic: dict | None = None,
            left_s: float | None = None, catalog=None,
            spec_version: str | None = None) -> dict:
    """Ожидание Kotlin-вида -> ПОЛУЧЕННОЕ живым решателем.

    Возвращает `{"got": {...}, "run_id": …, "package_fingerprint": …,
    "note": …, "termination": …}`; несостоявшееся звено —
    `StandSolverError` словами. Ключи `got` объявлены `GOT_FORMS` —
    публично, одним источником на стенд и конвейер пополнения каталога
    (просьба Ж-2). `termination` — признак остановки прогона ПОЛЕМ (при
    профиле Д1 и правленом решателе), а не словами `note`: он зависит от
    секундомера, а `note` уезжает каноном в привязки.
    """
    record = record if isinstance(record, dict) else {}
    if kind == "kotlin_metric":
        return _measure_metric(world=world, expected=expected,
                               families=families, meta=meta, stamp=stamp,
                               base=base, client=client, record=record,
                               wait_s=wait_s, deterministic=deterministic,
                               left_s=left_s, catalog=catalog,
                               **({"spec_version": spec_version}
                                  if spec_version is not None else {}))
    if kind not in ("kotlin_constraint", "kotlin_logic_form"):
        raise StandSolverError(
            f"вид {kind!r} живым решателем не судится: стенд знает "
            f"kotlin_constraint, kotlin_logic_form, kotlin_metric")
    zone = record.get("zone") or (meta.get(family) or {}).get("zone")
    run = run_world(world, families=families, meta=meta, stamp=stamp,
                    base=base, client=client, task=_task_of(zone),
                    wait_s=wait_s, family=family,
                    deterministic=deterministic, left_s=left_s, catalog=catalog,
                    **({"spec_version": spec_version}
                       if spec_version is not None else {}))
    breakdown = run["breakdown"]
    try:
        if kind == "kotlin_constraint":
            name = str(expected.get("constraint") or "")
            if not name:
                raise StandSolverError(
                    "ожидание вида kotlin_constraint не называет "
                    "ограничения: судить нечего")
            row = constraint_row(breakdown, name)
            got = {"constraint": name,
                   "matches": int(row.get("matches") or 0),
                   "amount": amount_of(row)}
            note = (f"ограничение «{name}»: счёт {row.get('score')}, вес "
                    f"{row.get('weight')}")
        else:
            rid = _logic_rule_id(run["rules"], family)
            name = logic_constraint_id(rid)
            generated = breakdown.get("generated")
            if isinstance(generated, list) and name not in generated:
                raise StandSolverError(
                    f"форма логики правила {rid} решателем не порождена: "
                    f"разбор объявил порождёнными "
                    f"{sorted(generated) or 'ничего'} — форма ожидания "
                    f"записи до исполнителя не доехала")
            row = constraint_row(breakdown, name)
            # ФОРМА измеряется показаниями прогона, а не копируется из
            # ожидания: имя ограничения выводится из идентификатора
            # ПРАВИЛА, и две задачи одной семьи судятся под одним именем —
            # копия формы делала бы строку зелёной по построению.
            form, why = logic_form_of(client, run["run_id"], name)
            got = {"matches": int(row.get("matches") or 0), "amount": None}
            if why is None:
                got["form"] = form
            note = (f"порождённое ограничение «{name}» правила {rid}: счёт "
                    f"{row.get('score')}; форма плана "
                    + (f"{form!r}" if why is None
                       else f"НЕ ИЗМЕРЕНА — {why}"))
    except StandSolverError as e:
        raise _with_run(e, run["run_id"])
    if deterministic:
        note = f"{note}; {d1_words()}"
    return {"got": got, "run_id": run["run_id"],
            "package_fingerprint": run["package_fingerprint"], "note": note,
            "termination": termination_row(run.get("status"))}


def _logic_rule_id(rules: list, family: str | None) -> str:
    """Идентификатор правила семьи в своде стенда: имя порождённого
    ограничения выводится ИЗ НЕГО, а не из формы ожидания."""
    for rule in rules:
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        if isinstance(fit, dict) and fit.get("family") == family:
            return str(rule.get("id"))
    raise StandSolverError(
        f"в своде стенда нет правила семьи {family!r}: имя порождённого "
        f"ограничения выводить не из чего")


def _measure_metric(*, world, expected, families, meta, stamp, base, client,
                    record, wait_s=None, deterministic=None,
                    left_s=None, catalog=None, spec_version=None) -> dict:
    """Вид `kotlin_metric`: целевая пакета исполняется решателем.

    Ожидание `refusal: true` — ЗАКОННЫЙ исход: словарь метрик решателя
    объявляет отказ, и он обязан прозвучать теми же словами, что записаны
    у критерия каталога. Отказ с ЧУЖИМИ словами — расхождение.
    """
    metric = str(record.get("id"))
    objective = objective_for(record, world=world)
    task = _task_of(record.get("zone"))
    try:
        run = run_world(world, families=families, meta=meta, stamp=stamp,
                        base=base, client=client, task=task,
                        mode="optimization", objective=objective,
                        wait_s=wait_s, deterministic=deterministic,
                        left_s=left_s, catalog=catalog,
                        **({"spec_version": spec_version}
                           if spec_version is not None else {}))
    except StandSolverError as e:
        if not expected.get("refusal"):
            raise
        words = str(record.get("refusal") or "").strip()
        text = str(e)
        # Прогон и состояние очереди у чужого отказа НЕ ТЕРЯЮТСЯ: отказ
        # мог случиться после старта (и тогда прогон существует), а
        # незавершённое ожидание значит занятый пул — и то и другое
        # нужно вызывающему судье.
        keep = {"run_id": getattr(e, "run_id", None),
                "queued": getattr(e, "queued", False),
                "unanswered": getattr(e, "unanswered", False),
                "run_failed": getattr(e, "run_failed", False),
                "out_of_time": getattr(e, "out_of_time", False)}
        if not words:
            raise StandSolverError(
                f"решатель отказал словами «{text}», а запись критерия "
                f"{metric} своего текста отказа не несёт: сверять не с чем",
                **keep)
        if words not in text:
            raise StandSolverError(
                f"решатель отказал ДРУГИМИ словами: ждали «{words}», "
                f"получено «{text}»", **keep)
        return {"got": {"value": None, "matches": None, "refusal": True},
                "run_id": keep["run_id"], "package_fingerprint": None,
                "note": f"решатель отказал словами записи: {text}"}
    if expected.get("refusal"):
        raise StandSolverError(
            f"запись критерия {metric} ждёт отказа решателя, а прогон "
            "состоялся: отказ не прозвучал", run_id=run["run_id"])
    try:
        constraint = metric_constraint(metric)
        row = constraint_row(run["breakdown"], constraint)
        got = {"value": amount_of(row),
               "matches": int(row.get("matches") or 0), "refusal": False}
    except StandSolverError as e:
        raise _with_run(e, run["run_id"])
    note = (f"целевая {objective['id']} -> ограничение «{constraint}»: "
            f"счёт {row.get('score')}, вес {row.get('weight')}")
    if deterministic:
        note = f"{note}; {d1_words()}"
    return {"got": got, "run_id": run["run_id"],
            "package_fingerprint": run["package_fingerprint"], "note": note,
            "termination": termination_row(run.get("status"))}


def metric_constraint(metric: str) -> str:
    """Имя ограничения метрики — ИЗ Kotlin, тем же читателем, которым
    живёт генератор привязок (`vmp.kotlin_tables.metrics()` читает
    `TaskModes.METRIC_EXECUTORS`).

    Рукописной копии этой карты у стенда нет и быть не должно: два места
    для одного имени рано или поздно разойдутся, и переименование
    ограничения в решателе стенд объявил бы отказом «ограничения нет в
    разборе» — честными словами по ЛОЖНОЙ причине (виновата была бы
    копия, а не решатель).
    """
    from vmp import kotlin_tables as kt
    try:
        rows = kt.metrics()
    except Exception as e:                                   # noqa: BLE001
        raise StandSolverError(
            f"словарь метрик решателя (TaskModes.METRIC_EXECUTORS) не "
            f"прочитан: {e} — сопоставить метрике ограничение нечем")
    row = rows.get(str(metric))
    if row is None:
        raise StandSolverError(
            f"метрики {metric!r} нет в TaskModes.METRIC_EXECUTORS: "
            f"решатель знает {', '.join(sorted(rows)) or 'ни одной'}")
    name = str(row.get("constraint") or "")
    if not name:
        raise StandSolverError(
            f"метрике {metric} в TaskModes.METRIC_EXECUTORS не сопоставлено "
            f"ограничение (её запись несёт отказ: "
            f"{row.get('refusal') or 'без слов'}): считать нечего")
    return name


def stamp_of(cat) -> dict:
    """Отметка каталога, которым судит стенд: версия и отпечаток."""
    return {"version": cat.version(), "fingerprint": cat.fingerprint()}


def run_base(tmp_dir, task_id: str) -> Path:
    """Папка сборки пакета одной задачи: своя на задачу, чтобы пакеты
    задач не наслаивались друг на друга.

    Папка ОБЯЗАТЕЛЬНА: прежняя редакция при её отсутствии молча писала
    `./packages/<задача>` в рабочий каталог процесса — и против правила
    временных файлов (либо `tmp_path` теста, либо папка разбора рядом с
    деревом), и против «ничего молча». Умолчания у места нет.
    """
    if not str(tmp_dir or "").strip():
        raise StandSolverError(
            "папка прогона не задана: пакеты стенда пишутся только в "
            "папку ключа --tmp-dir (без ключа команда заводит её сама "
            "под _repro/stand), а в рабочий каталог — никогда")
    out = Path(tmp_dir) / "packages" / str(task_id or "task")
    out.mkdir(parents=True, exist_ok=True)
    return out
