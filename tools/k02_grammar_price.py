# -*- coding: utf-8 -*-
"""ЦЕНА ГРАММАТИКИ ЗНАЧЕНИЯ (K02) В ЧИСЛАХ — один сопоставимый замер.

Зачем. Цену закона нельзя называть на память: один и тот же замер круга
11 был назван двумя разными числами (руководство 10 — «потеряно 1152,
верных не потеряно ни одного»; запись коммита 688f8a35 — «потеряно 1277,
из них верных 6»). Скрипт делает замер ВОСПРОИЗВОДИМЫМ: корпус объявлен
здесь же, единицы перечислены, команда одна, а исход — таблица переходов,
а не одно слово.

Что меряется. Для КАЖДОЙ пары (вход × единица каталога) берутся ДВА
замера.

ЗНАЧЕНИЕ — исход `family_params.parse_value(вход, единица,
default_domain(единица))`, сведённый к строке: «-» (значения нет) либо
«значение». Каждое расхождение относится к одному из четырёх переходов:

  значение → нет      ПОТЕРЯ прежнего исхода
  нет → значение      ПРИОБРЕТЕНИЕ (появилось значение там, где его не было)
  значение → другое   ПОДМЕНА значения
  нет → нет           не бывает (равные исходы в расхождения не попадают)

СТАТУС — тот же исход СКВОЗЬ применение ответа (тринадцатое неавторское
ревью, находка С-3; квитанция аудитора GPT-165 §2). Замер одного только
`parse_value` НЕДОСТАТОЧЕН: ответ, ставший БЕЗУСЛОВНЫМ ОТКАЗОМ, значения
не даёт ни до правки, ни после — таблица значений показывает «-» → «-»,
— а параметр при этом переходит из ОТКРЫТ в РЕШЕНИЕ, и блокировка с
носителей СНИМАЕТСЯ. Это самый дорогой исход разбора, и он был
инструменту невидим. Статус берётся теми же двумя вызовами, какими его
берёт `apply_param_answer`:

  РЕШЕНИЕ   безусловный отказ (`_decline_parts` без помехи) — параметр
            не задаётся, вопрос ЗАКРЫТ, блокировка снята
  ЗНАЧЕНИЕ  `parse_value` вернул значение — вопрос закрыт значением
  ОТКРЫТ    ни то, ни другое — вопрос открыт, носители заблокированы

Что инструмент НЕ удостоверяет, названо вслух: он меряет ГРАММАТИКУ
ответа, а не весь конвейер. Вне области — заметка отказа (её текст),
журнал следствий, посадка значения в правило-носитель и сама запись
блокировок в документ: они меряются тестами, а не этим перебором.

ОШИБКА ЗАМЕРА — НЕ ЗНАЧЕНИЕ И НЕ ОБЪЯСНЁННАЯ ПОТЕРЯ (С-5, замечание
аудитора). Исключение на входе даёт «!ИмяИсключения». Равные ошибки по
обеим сторонам прежде УДОСТОВЕРЯЛИ нулевую цену: расхождения нет —
значит и цены нет. Теперь ошибки считаются отдельно и НУЛЕВАЯ ЦЕНА ПРИ
НИХ НЕ ОБЪЯВЛЯЕТСЯ: сравнение кончается ненулевым кодом возврата.

ОСНОВАНИЕ отнесения потери к ВЕРНОЙ. Машина не знает, что прочитал бы
человек, поэтому потери группируются по ПРИЧИНЕ, которую новый код
называет отвечающему (та же, что уходит в заметку отказа): чужое слово
величины, слово двумя алфавитами, номер строки анкеты, цифры вне
числового выражения, знаки вне прочитанных обозначений, фраза отказа.
Названная причина — ДИАГНОСТИКА ПРОДУКТА, а не независимое доказательство
того, что потерянный исход был неверным. Обоснованность каждого класса
потерь устанавливается контрактом и независимыми контрпримерами, в том
числе здоровыми. Потери без причины дополнительно печатаются поимённо;
ноль в этой группе не означает «здоровых потерь нет».

Корпус (см. ниже) — перебор записей значения, величин, разделителей,
обёрток, обозначений и фраз отказа во всех трёх написаниях (кириллица,
смешанное, целиком латинское) плюс строки-ответы явно выбранного снимка
книг наборов. Размер корпуса печатается всегда: число замера без числа
корпуса несопоставимо.

КОРПУС ОБЯЗАН НЕСТИ ОБЕ СТОРОНЫ КАЖДОГО ЗАКОНА (С-4). Он был
односторонен: входов, где слово величины стоит при числе ВПЛОТНУЮ, а
род окна дальше по строке, в нём не было НИ ОДНОГО; входов «вводный
оборот + тире вплотную + обозначение» — тоже ни одного. На таком
корпусе правка этих двух законов давала 0 расхождений, и «потерь без
причины 0» не означало «ни один законный ответ не потерян» — оно
означало лишь, что корпус слеп. Классы объявлены ниже (OWN_WORDS,
PERIOD_TAILS, DASH_HEADS, DASH_TAILS), и у каждого в корпусе стоят ОБЕ
стороны: та, что обязана читаться, и та, что обязана отказывать.

СОПОСТАВИМОСТЬ ЗАМЕРОВ сверяется по СОДЕРЖИМОМУ, а не по размеру: у
корпуса и у живых книг берётся отпечаток, наборы ключей обеих матриц
сверяются ПОИМЁННО. Дамп, снятый иным корпусом (в том числе прежним,
узким) или без живых книг, сравнивать нельзя — сравнение кончается
кодом 2 и называет расхождение.

ПРЕДМЕТ СРАВНЕНИЯ — две разные редакции кода cdpm/ + template_catalog/.
Равенство всего этого набора (без учёта регистра hex и порядка записей)
даёт код2 ДО таблиц цены, даже при разных адресах деревьев. Правка только
зависимости допустима. Печатаются отпечатки family_params и набора кода.
Один путь допустим для замеров разных редакций в разное время;
равенство результатов разных редакций остаётся законной нулевой ценой.

Как звать (пути — через прямые слэши):

    python tools/k02_grammar_price.py dump \
        --package-root <дерево> --books-root <стенд> --out <файл.json>
    python tools/k02_grammar_price.py compare --was <A.json> --now <B.json>

`--package-root` кладётся в начало `sys.path`, поэтому один и тот же
скрипт меряет любую копию пакета `cdpm` — свою, прежнюю, чужую. Живые
книги берутся из `--books-root` (по умолчанию — package-root), но НЕ
становятся путём импорта. Каталог берётся из package-root/catalog либо
явного --catalog-root; переменная окружения не выбирает его скрыто.
Дамп k02-price/2 несёт исходные строки, манифест файлов книг, хеши кода,
каталога и измерителя. Неизвестная структура/пустой корпус — код2,
ошибки измерения — код3, код0 означает завершённый сопоставимый замер,
а НЕ правильность всей грамматики. Существующий файл не перезаписывается.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

#: Единицы каталога 0.5.0, значение которых читает грамматика K02.
UNITS = ("duration_minutes", "time_of_day_minutes", "count",
         "credit_hours", "hours_per_credit_hour", "code", "code_list")

#: Кириллическая буква → её латинский близнец (обратная свёртка
#: `family_params._CONFUSABLE`). Нужна, чтобы построить оба опасных
#: написания: СМЕШАННОЕ (одна буква латинская) и ЦЕЛИКОМ ЛАТИНСКОЕ.
TWIN = {"а": "a", "А": "A", "с": "c", "С": "C", "е": "e", "Е": "E",
        "о": "o", "О": "O", "р": "p", "Р": "P", "х": "x", "Х": "X",
        "у": "y", "У": "Y", "к": "k", "К": "K", "м": "m", "М": "M",
        "т": "t", "Т": "T", "В": "B", "Н": "H"}

# --- корпус ----------------------------------------------------------

#: Записи числа: целое, дробь через запятую и точку, длительность.
NUMBERS = ("2", "3", "10", "0,5", "1.5", "600", "75", "14")
#: Разделитель между числом и словом величины: вплотную, через пробел,
#: через одно определение, через два, через три (три — вне окна закона).
GAPS = ("", " ", " календарных ", " полных календарных ",
        " в течение каждого полного ")
#: Слова величины (свои и чужие) и существительные при числе —
#: положительный контроль: существительное единицей не является.
WORDS = ("минут", "часов", "суток", "дней", "недель", "месяцев",
         "кварталов", "лет", "секунд", "декад", "процентов", "%",
         "промилле", "долей", "смен", "рейсов", "экипажа",
         "weeks", "months", "days", "hours", "minutes")
#: Обёртки ответа: голое значение, оговорка, заголовок, номер строки.
LEADS = ("", "Не менее ", "не более ", "Норма: ", "1) ")

#: ОКНО ЗА СОБСТВЕННЫМ СЛОВОМ ВЕЛИЧИНЫ (тринадцатое неавторское ревью,
#: находка С-4). Корпус был ОДНОСТОРОНЕН: входов, где слово величины
#: стоит при числе ВПЛОТНУЮ, а род окна — ДАЛЬШЕ по строке, в нём не
#: было НИ ОДНОГО (ни в переборе, ни в 107 строках живых книг), и
#: «потерь без причины 0» ничего не говорило о классе, где правка стоит
#: денег. Класс объявлен здесь: слово ПРИ ЧИСЛЕ (своё, чужое, предмет
#: счёта, пусто) × хвост со сроком, формой выражения и определением.
OWN_WORDS = ("часов", "минут", "суток", "дней", "недель", "процентов",
             "раз", "рейсов", "")
#: Хвост за словом при числе: срок («в месяц»), ФОРМА ВЫРАЖЕНИЯ («в
#: процентах» — та же ошибка в сто раз, если её прочесть сроком),
#: определение без предлога (окно закона Б2) и хвост вне окна.
PERIOD_TAILS = (" в месяц", " в неделю", " в квартал", " в год",
                " в сутки", " в полугодие", " в секунду",
                " в процентах", " в неделях", " в календарный месяц",
                " налёта в месяц", " в течение каждого полного месяца",
                " календарных недель", "")

# Compound prepositions/adverbs are a separate measured axis. The old
# 11108-row corpus is preserved as a subset; a period in another sentence
# must not become invisible merely because only the first value is read.
COMPOUND_PERIODS = ("в течение каждого полного месяца", "на протяжении недели",
                    "в пределах недели", "ежечасно", "поминутно",
                    "в течение одного месяца", "за 2 календарные недели")

# COSMOS/CDPM-202: keep both magnitude and true periodic-norm variants.
MAGNITUDE_ANSWERS = tuple(
    f"{lead} {prep} {n} {word}"
    for lead in ("Перерыв", "Интервал", "Резерв")
    for prep in ("в", "за") for n in ("2", "10") for word in ("минут", "часов"))

# A separately named penalty weight and an unbound rate must not be conflated.
PENALTY_ANSWERS = tuple(
    f"{value}. Штраф (вес {weight} за {event} с превышением)."
    for value in ("600 минут", "12:00 (720 минут)")
    for weight in ("15", "2,5") for event in ("смену", "рейс", "полёт"))

#: ВВОДНЫЙ ОБОРОТ ЧЕРЕЗ ТИРЕ БЕЗ ПРОБЕЛОВ (С-4, вторая половина). Тот
#: же пробел в корпусе: входов «оборот + тире вплотную + обозначение»
#: не было ни одного. Основы — и вводные обороты, и кириллические
#: ОСНОВЫ ТИПА («Ту», «Ил», «Боинг»): по знакам они различаются лишь
#: тем, чем открывается хвост, и оба конца класса обязаны быть в
#: корпусе.
DASH_HEADS = ("Тип судна", "Типы", "Тип", "Флот", "Ту", "Ил", "Боинг",
              "МС", "Як")
DASH_TAILS = ("A320", "A320, B738", "737", "154", "MAX", "737-MAX",
              "B738")

#: Обозначения справочника и их опасные соседи.
CODES = ("A320", "A320, B738", "A320-200", "737-MAX", "737", "320, 737",
         "A_320", "n/a", "Ту-154", "Ил-96", "Боинг-737", "МС-21",
         "Як-42", "А320", "п. 4.2", "10:00",
         "ATR72-500-600-700-800-900-1000", "A320, B738 и Ту-154")
CODE_LEADS = ("", "Типы: ", "Типы:", "Тип судна — ", "1) ")
CODE_CONTEXTS = tuple(
    f"{lead}{sep}A320" for lead in (
        "Не", "Нет", "Кроме", "Исключая", "Ту", "Без",
        "За исключением", "Возможно", "Не только", "Вместо")
    for sep in (" ", ": ", "—", "–")) + (
        "A320 не используем", "A320 кроме B738", "A320 и не B738",
        "A320. Кроме B738.", "Не этот тип. A320.", "A320. Неверно.",
        "A320 только при согласовании", "Типы: A320 (кроме B738)",
        "¬ A320", "!= A320")
# CDPM-217: measured word/code ambiguity, not a manufacturer dictionary.
# Both the refusal boundary and its healthy explicit-list alternatives
# belong in the corpus; do not claim that these exhaust human wording.
CODE_WORD_PHRASES = (
    "Airbus A320", "AIRBUS A320", "airbus a320", "Airbus — A320",
    "Airbus: A320", "Airbus\nA320", "Airbus (A320)", "A320 Airbus",
    "Boeing 737-800", "Embraer E190-E2", "ATR 72-600", "737 MAX",
    "Base SVO", "BASE SVO", "base svo", "UnknownLabel X9",
    "Airbus A320, Boeing 737", "Airbus A320 и Boeing 738",
    "A320, Airbus B738", "Типы: Airbus A320", "A320. Airbus B738.",
    "SVO DME", "Alpha Bravo", "SVO, DME", "SVO; DME", "SVO и DME",
    "A320 B738", "A320\nB738", "320 737", "Alpha, Bravo",
)
#: Знаки тире, которыми набирают одно и то же обозначение.
DASHES = ("-", "‐", "‑", "‒", "–", "—",
          "­", "－", "−")
#: Основы обозначения, разделяемые тире, — по одной на каждый знак.
DASHED = ("A320{d}200", "Ту{d}154", "737{d}MAX", "7{d}ми суток",
          "10{d}12 минут")

#: Фразы отказа и то, что рядом с ними ломает безусловность.
DECLINES = ("Не применяется", "Неприменимо", "Нет такого требования",
            "Требования нет", "Не применимо")
DECLINE_TAILS = ("", " для базы SVO", ". В остальных базах 600 минут",
                 "; 600 минут", ". Нет такого требования")
DECLINE_HEADS = ("", "600 минут. ", "1) ", "10:00. ")

#: Цифры не из 0–9 — тот же омоглиф, только у цифр.
ODD_DIGITS = ("３０ минут", "٣٠ минут", "５",
              "１０:００")


def latin_all(s: str) -> str:
    """Ответ, набранный ЛАТИНИЦЕЙ во всех буквах, у которых есть
    латинский близнец: на вид тот же, по знакам другой."""
    return "".join(TWIN.get(ch, ch) for ch in s)


def latin_first(s: str) -> str:
    """Ответ с ОДНОЙ подменённой буквой — смешанное слово."""
    for i, ch in enumerate(s):
        if ch in TWIN:
            return s[:i] + TWIN[ch] + s[i + 1:]
    return s


def _word_twin(word: str, caps: bool) -> str:
    """Слово ЦЕЛИКОМ латиницей либо оно же, если целиком не выходит.
    `caps`: букву без строчного близнеца берёт прописной («нет» →
    «Het»), как её и набирают на латинской раскладке."""
    out = []
    for ch in word:
        twin = TWIN.get(ch)
        if twin is None and caps:
            twin = TWIN.get(ch.upper())
        if twin is None:
            return word
        out.append(twin)
    return "".join(out)


def _by_words(s: str, caps: bool) -> str:
    out, word = [], []
    for ch in s:
        if ch.isalpha():
            word.append(ch)
            continue
        if word:
            out.append(_word_twin("".join(word), caps))
            word = []
        out.append(ch)
    if word:
        out.append(_word_twin("".join(word), caps))
    return "".join(out)


def latin_words(s: str) -> str:
    """Ответ, где ЦЕЛИКОМ латинскими стали слова, которые выходят
    целиком: «Не применяется» → «He применяется». Смешанного слова тут
    НЕТ — и потому такой ответ проходил мимо закона находки 3."""
    return _by_words(s, caps=False)


def latin_words_caps(s: str) -> str:
    """То же, но буква без строчного близнеца берётся прописной:
    «Требования нет» → «Требования Het»."""
    return _by_words(s, caps=True)


def corpus() -> list[str]:
    """Объявленный перебор входов. Порядок устойчив, повторы сняты."""
    out: list[str] = []
    for lead in LEADS:
        for num in NUMBERS:
            for gap in GAPS:
                for word in WORDS:
                    raw = lead + num + gap + word
                    out.append(raw)
                    if lead or gap not in (" ", " календарных "):
                        continue
                    # Те же записи в трёх опасных написаниях: одна
                    # подменённая буква (смешанное слово) и слово
                    # ЦЕЛИКОМ латиницей в двух видах набора.
                    out.append(latin_first(raw))
                    out.append(latin_words(raw))
                    out.append(latin_words_caps(raw))
    # Окно за собственным словом величины (С-4): слово при числе стоит
    # ВПЛОТНУЮ, род окна — дальше по строке.
    for lead in LEADS:
        for num in NUMBERS:
            for own in OWN_WORDS:
                for tail in PERIOD_TAILS:
                    out.append(lead + num + (" " + own if own else "") + tail)
    for lead in CODE_LEADS:
        for code in CODES:
            out.append(lead + code)
    out.extend(CODE_CONTEXTS)
    out.extend(CODE_WORD_PHRASES)
    out.extend(MAGNITUDE_ANSWERS)
    out.extend(raw + " в неделю" for raw in MAGNITUDE_ANSWERS)
    out.extend(PENALTY_ANSWERS)
    out.extend(raw + " В неделю." for raw in PENALTY_ANSWERS)
    for period in COMPOUND_PERIODS:
        for value in ("10 часов", "600 минут", "10:30", "2 экипажа"):
            out.extend((value + " " + period, period + ": " + value,
                        value + ". " + period + "."))
    for dash in DASHES:
        for pat in DASHED:
            out.append(pat.format(d=dash))
        # Вводный оборот и основа типа через ТО ЖЕ тире вплотную (С-4).
        for head in DASH_HEADS:
            for tail in DASH_TAILS:
                out.append(head + dash + tail)
                out.append(head + " " + dash + " " + tail)
    for head in DECLINE_HEADS:
        for phrase in DECLINES:
            for tail in DECLINE_TAILS:
                raw = head + phrase + tail + "."
                out.append(raw)
                out.append(latin_first(raw))      # смешанное слово
                out.append(latin_words(raw))      # «He применяется»
                out.append(latin_words_caps(raw))  # «Требования Het»
                out.append(latin_all(raw))
    out.extend(ODD_DIGITS)
    seen, uniq = set(), []
    for raw in out:
        if raw not in seen:
            seen.add(raw)
            uniq.append(raw)
    return uniq


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("повтор ключа JSON: %r" % key)
        out[key] = value
    return out


# Explicit measurement dialect, not a classifier for arbitrary customer prose.
# Metadata belongs outside exact/prefix or outside the Markdown answer table.
# Nonreserved question IDs/topics remain unrestricted (in particular, not Q-only).
_BOOK_SERVICE_KEYS = frozenset({
    "итого", "всего", "сумма", "итого строк", "всего строк",
    "итого ответов", "всего ответов", "итого вопросов", "всего вопросов",
    "версия анкеты", "версия книги", "источник",
})
_BOOK_QUESTION_COLUMNS = frozenset({
    "вопрос", "тема", "тема (дефект)", "id вопроса", "идентификатор вопроса",
})
_BOOK_ANSWER_COLUMNS = frozenset({
    "ответ", "правильный ответ", "правильный ответ авиакомпании",
})


def _book_answer_key(key, answer=None):
    """Refuse reserved metadata/header rows in either format; never drop a row."""
    normalized = " ".join(key.split()).casefold()
    if not normalized:
        raise ValueError("пустой ключ вопроса/темы")
    if normalized.startswith("_") or normalized in _BOOK_SERVICE_KEYS:
        raise ValueError("служебный ключ в разделе ответов: %r; "
                         "метаданные должны быть вне ответов" % key)
    if (normalized in _BOOK_QUESTION_COLUMNS and isinstance(answer, str)
            and " ".join(answer.split()).casefold() in _BOOK_ANSWER_COLUMNS):
        raise ValueError("повтор шапки внутри ответов без разделителя таблицы")


def _json_answers(text):
    doc = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(doc, dict) or not any(k in doc for k in ("exact", "prefix", "regex")):
        raise ValueError("нет объявленной структуры exact/prefix/regex")
    out = []
    for key in ("exact", "prefix"):
        cell = doc.get(key, {})
        if not isinstance(cell, dict):
            raise ValueError("%s должен быть объектом" % key)
        for answer_key, answer in cell.items():
            # A literal empty PREFIX is the supported catch-all route, not an
            # empty question ID; keep match_answer's exact/prefix semantics.
            if not (key == "prefix" and answer_key == ""):
                _book_answer_key(answer_key, answer)
            out.append(answer)
    regex = doc.get("regex", [])
    if not isinstance(regex, list):
        raise ValueError("regex должен быть списком")
    for item in regex:
        if not isinstance(item, dict) or not isinstance(item.get("pattern"), str):
            raise ValueError("нет строкового regex.pattern")
        out.append(item.get("answer"))
    if any(not isinstance(value, str) for value in out):
        raise ValueError("ответ обязан быть строкой; приведение str() запрещено")
    return out


def _md_cells(line):
    """Declared pipe-table dialect; escaped pipes/backslashes stay in their cell."""
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        raise ValueError("строка таблицы должна начинаться и заканчиваться |")
    cells, chars, i = [], [], 1
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] in "\\|":
            chars.append(line[i + 1])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(chars).strip())
            chars = []
        else:
            chars.append(ch)
        i += 1
    if chars:
        raise ValueError("нет закрывающего разделителя таблицы")
    return cells


def _markdown_answers(text):
    lines, out, column, width, i = text.splitlines(), [], None, 0, 0
    question_column, header = None, None
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            column = None
            i += 1
            continue
        cells = _md_cells(lines[i])
        following = (_md_cells(lines[i + 1]) if i + 1 < len(lines)
                     and lines[i + 1].lstrip().startswith("|") else [])
        if following and all(re.fullmatch(r":?-{3,}:?", c) for c in following):
            names = [" ".join(c.split()).casefold() for c in cells]
            matches = [n for n, name in enumerate(names)
                       if name in _BOOK_ANSWER_COLUMNS]
            if len(matches) != 1 or len(following) != len(cells):
                raise ValueError("нет единственной именованной колонки ответа")
            questions = [n for n, name in enumerate(names)
                         if name in _BOOK_QUESTION_COLUMNS]
            if len(questions) != 1:
                raise ValueError("нет единственной именованной колонки вопроса/темы")
            column, width = matches[0], len(cells)
            question_column, header = questions[0], names
            i += 2
            continue
        if column is None or len(cells) != width:
            raise ValueError("строка вне объявленной таблицы или неверное число колонок")
        if [c.casefold() for c in cells] == header:
            raise ValueError("повтор шапки внутри ответов без разделителя таблицы")
        _book_answer_key(cells[question_column], cells[column])
        out.append(cells[column])
        i += 1
    return out


def _json_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def book_snapshot(root: Path) -> dict:
    """Read once; bind every source byte, preserve duplicate answers, fail on loss.

    Scope is the explicitly selected root, with no link traversal. Empty cells
    are counted, not converted to values. An empty/unreadable book is not a
    valid price measurement, even if an empty book is legal in the product.
    """
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("нет корня книг: %s" % root)
    paths = []

    def unreadable(exc):
        raise ValueError("не удалось обойти книги: %s" % exc) from exc

    for directory, dirs, files in os.walk(root, onerror=unreadable, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("ссылка-каталог вне области замера: %s" % path)
        paths.extend(Path(directory) / name for name in files if name.startswith("ANSWER-BOOK"))
    if not paths:
        raise ValueError("не найдено ни одной ANSWER-BOOK в %s" % root)
    lines, manifest = [], []
    for path in sorted(paths):
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("ссылка вне области замера")
            data = path.read_bytes()
            text = data.decode("utf-8-sig")
            if path.suffix.lower() == ".json":
                answers = _json_answers(text)
            elif path.suffix.lower() == ".md":
                answers = _markdown_answers(text)
            else:
                raise ValueError("неподдержанный формат книги")
            nonempty = [value for value in answers if value.strip()]
            if not nonempty:
                raise ValueError("нет непустых ответов")
        except (OSError, ValueError) as exc:
            raise ValueError("%s: %s" % (path, exc)) from exc
        lines.extend(nonempty)
        manifest.append({"path": path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "answers": len(nonempty),
                         "blank_answers": len(answers) - len(nonempty)})
    return {"lines": lines, "manifest": manifest, "manifest_sha": _json_sha(manifest)}


def book_lines(root: Path) -> list[str]:
    return book_snapshot(root)["lines"]


def _books_count(root: Path) -> int:
    return len(book_snapshot(root)["manifest"])


# --- замер -----------------------------------------------------------

def _outcome(fp, raw: str, unit: str) -> str:
    try:
        got = fp.parse_value(raw, unit, fp.default_domain(unit))
    except Exception as exc:                       # noqa: BLE001
        return "!" + type(exc).__name__
    return "-" if got is None else repr(got[0])


def _status(fp, raw: str, unit: str) -> str:
    """Исход СКВОЗЬ применение ответа (С-3): РЕШЕНИЕ / ЗНАЧЕНИЕ /
    ОТКРЫТ. Берётся теми же двумя вызовами и в том же ПОРЯДКЕ, что в
    `apply_param_answer`: сперва разбор отказа (безусловный отказ
    закрывает вопрос РЕШЕНИЕМ и снимает блокировку — до всякого поиска
    значения), затем `parse_value` с областью параметра.

    Отказ с помехой (условие, пояснение, значение рядом) решением НЕ
    является — вопрос остаётся ОТКРЫТ, носители заблокированы; здесь он
    и попадает в ОТКРЫТ, как в применении."""
    try:
        parts = fp._decline_parts(raw)
        if parts is not None:
            return "РЕШЕНИЕ" if not parts[1] else "ОТКРЫТ"
        got = fp.parse_value(raw, unit, fp.default_domain(unit))
    except Exception as exc:                       # noqa: BLE001
        return "!" + type(exc).__name__
    return "ОТКРЫТ" if got is None else "ЗНАЧЕНИЕ"


def _cause(fp, raw: str, unit: str) -> str:
    """Причина отказа, которую новый код НАЗЫВАЕТ отвечающему, — тем же
    порядком, что заметка apply_param_answer."""
    try:
        if fp.line_number_record(raw):
            return "номер строки анкеты"
        if fp.mixed_script_word(raw):
            return "слово двумя алфавитами"
        # Historical snapshots predate the explicit period guard. Absence
        # there is not an import failure and must not fabricate a cause.
        if hasattr(fp, "period_qualifier") and fp.period_qualifier(raw, unit):
            return "неподдержанный период нормы"
        if fp.foreign_unit_word(raw, unit):
            return "чужое слово величины"
        if fp.unread_digits(raw, unit):
            return "цифры вне числового выражения"
        if fp.unread_code_records(raw, unit):
            return "знаки вне прочитанных обозначений"
        if fp._decline_parts(raw) is not None:
            return "фраза отказа"
        if hasattr(fp, "code_context_problem") and fp.code_context_problem(raw, unit):
            return "непрочитанный контекст обозначений"
    except Exception as exc:                       # noqa: BLE001
        return "!" + type(exc).__name__
    return ""


def cmd_dump(args) -> int:
    root = Path(args.package_root).resolve()
    books_root = Path(args.books_root or args.package_root).resolve()
    try:
        snapshot = book_snapshot(books_root)
    except ValueError as exc:
        print("ОШИБКА ВХОДА КНИГ:", exc, file=sys.stderr)
        return 2
    # Books are data, never a Python import path. Catalog defaults to the
    # measured package, not the books folder or an inherited environment.
    previous_path = sys.path[:]
    previous_catalog = os.environ.get("CREW_TEMPLATE_CATALOG")
    catalog = Path(args.catalog_root or root / "catalog").resolve()
    try:
        sys.path.insert(0, str(root))
        os.environ["CREW_TEMPLATE_CATALOG"] = str(catalog)
        from cdpm import family_params as fp      # noqa: PLC0415
        return _dump_loaded(args, fp, root, books_root, catalog, snapshot)
    finally:
        sys.path[:] = previous_path
        if previous_catalog is None:
            os.environ.pop("CREW_TEMPLATE_CATALOG", None)
        else:
            os.environ["CREW_TEMPLATE_CATALOG"] = previous_catalog


def _dump_loaded(args, fp, root, books_root, catalog, snapshot):
    loaded = Path(fp.__file__).resolve()
    print("копия модуля ЗАГРУЖАЕТСЯ:", loaded)
    if root not in loaded.parents:
        print("ОШИБКА: загружен не тот пакет", file=sys.stderr)
        return 2
    environment = _measurement_environment(root, catalog)
    lines = snapshot["lines"]
    inputs = corpus()
    body = {
        "schema": "k02-price/2",
        "package_root": str(root),
        "module_sha256": hashlib.sha256(loaded.read_bytes()).hexdigest(),
        "catalog_root": str(catalog),
        "environment": environment,
        "books_root": str(books_root),
        "books": len(snapshot["manifest"]),
        "books_manifest": snapshot["manifest"],
        "books_manifest_sha": snapshot["manifest_sha"],
        "book_lines": len(lines),
        "book_distinct": len(set(lines)),
        "corpus": len(inputs),
        "units": list(UNITS),
        "corpus_sha": _rows_sha(inputs),
        "books_sha": _rows_sha(lines),
        "corpus_rows": inputs,
        "book_rows": lines,
        "corpus_pairs": {},
        "book_pairs": {},
        "corpus_status": {},
        "book_status": {},
        "causes": {},
    }
    for kind, skind, rows in (("corpus_pairs", "corpus_status", inputs),
                              ("book_pairs", "book_status", lines)):
        for raw in rows:
            for unit in UNITS:
                key = json.dumps([raw, unit], ensure_ascii=False)
                body[kind][key] = _outcome(fp, raw, unit)
                body[skind][key] = _status(fp, raw, unit)
                body["causes"][key] = _cause(fp, raw, unit)
    _validate_dump(body)
    if _measurement_environment(root, catalog) != environment:
        raise ValueError("код/каталог/измеритель изменился во время замера")
    if book_snapshot(books_root) != snapshot:
        raise ValueError("книги изменились во время замера")
    with Path(args.out).open("x", encoding="utf-8") as output:
        json.dump(body, output, ensure_ascii=False, indent=1)
    print("корпус: %d входов × %d единиц = %d пар, снято %d"
          % (body["corpus"], len(UNITS), body["corpus"] * len(UNITS),
             len(body["corpus_pairs"])))
    print("живые книги: %d книг, %d строк-ответов (%d различных), %d пар, снято %d"
          % (body["books"], body["book_lines"], body["book_distinct"],
             body["book_distinct"] * len(UNITS), len(body["book_pairs"])))
    print("ОШИБОК ЗАМЕРА (исключений):", _errors(body))
    print("записано:", args.out)
    return 3 if _errors(body) else 0


def _measurement_environment(root, catalog):
    """Bind source dependencies as well as the target module; no mixed imports."""
    for name, module in list(sys.modules.items()):
        if not (name == "cdpm" or name.startswith("cdpm.")
                or name == "template_catalog" or name.startswith("template_catalog.")):
            continue
        origin = getattr(module, "__file__", None)
        if not origin or not Path(origin).resolve().is_relative_to(root):
            raise ValueError("смешанное окружение импорта: %s из %s" % (name, origin))
    sources = []
    for label, folder, pattern in (("cdpm", root / "cdpm", "*.py"),
                                    ("template_catalog", root / "template_catalog", "*.py"),
                                    ("catalog", catalog, "*.json")):
        paths = sorted(folder.rglob(pattern))
        if not paths:
            raise ValueError("нет зависимости измерителя: %s" % folder)
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("внешняя ссылка в зависимости: %s" % path)
            sources.append({"path": label + "/" + path.relative_to(folder).as_posix(),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"python": sys.version, "sources": sources,
            "catalog_sha": _json_sha([p for p in sources if p["path"].startswith("catalog/")]),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _product_code_sha(body) -> str:
    """Identity of validated product sources, independent of checkout/ordering.

    Catalog data, Python and measuring-tool identities are separate equality
    requirements in cmd_compare; changing them is not a product-code change.
    """
    return _json_sha(sorted(
        (source["path"], source["sha256"].lower())
        for source in body["environment"]["sources"]
        if source["path"].startswith(("cdpm/", "template_catalog/"))))


def _rows_sha(rows) -> str:
    """Отпечаток НАБОРА входов: сам корпус, а не его размер. Два разных
    перебора одного размера «сопоставимыми» больше не считаются."""
    h = hashlib.sha256()
    for raw in rows:
        h.update(raw.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _errors(body: dict) -> int:
    """Сколько замеров дампа кончились ИСКЛЮЧЕНИЕМ. Ошибка — не значение
    и не объяснённая потеря (С-5)."""
    n = 0
    for kind in ("corpus_pairs", "book_pairs", "corpus_status",
                 "book_status", "causes"):
        n += sum(1 for v in (body.get(kind) or {}).values()
                 if str(v).startswith("!"))
    return n


def _validate_dump(body):
    """An identically truncated pair is not complete merely because it matches."""
    if body.get("schema") != "k02-price/2":
        raise ValueError("нужен полный дамп k02-price/2")
    environment = body["environment"]
    if not isinstance(environment, dict) or not isinstance(environment.get("python"), str) or not environment["python"]:
        raise ValueError("нет доказательства среды исполнения")
    for digest in (environment["tool_sha256"], environment["catalog_sha"], body["module_sha256"]):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("нет отпечатка кода/каталога/измерителя")
    sources = environment["sources"]
    if not isinstance(sources, list) or not sources or any(not isinstance(s, dict) for s in sources):
        raise ValueError("нет манифеста зависимостей")
    source_hashes = {}
    for source in sources:
        path, digest = source["path"], source["sha256"]
        if (not isinstance(path, str) or path in source_hashes or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise ValueError("неверная запись зависимости")
        source_hashes[path] = digest
    catalog_files = [s for s in sources if s["path"].startswith("catalog/")]
    if not catalog_files or environment["catalog_sha"] != _json_sha(catalog_files):
        raise ValueError("отпечаток каталога не совпадает с файлами")
    if source_hashes.get("cdpm/family_params.py") != body["module_sha256"]:
        raise ValueError("отпечаток модуля не совпадает с зависимостями")
    if not any(s.startswith("template_catalog/") for s in source_hashes):
        raise ValueError("нет доказательства читателя каталога")
    units = body["units"]
    if not units or len(units) != len(set(units)) or not all(isinstance(u, str) for u in units):
        raise ValueError("неверный список единиц")
    manifest = body["books_manifest"]
    if not manifest or len(manifest) != body["books"] or _json_sha(manifest) != body["books_manifest_sha"]:
        raise ValueError("неполный манифест книг")
    if len({entry["path"] for entry in manifest}) != len(manifest):
        raise ValueError("повтор пути книги")
    if any(not re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]) for entry in manifest):
        raise ValueError("неверный отпечаток книги")
    if sum(entry["answers"] for entry in manifest) != body["book_lines"]:
        raise ValueError("число ответов не совпадает с манифестом")
    keys = set()
    for prefix, count, sha in (("corpus", "corpus", "corpus_sha"),
                               ("book", "book_lines", "books_sha")):
        rows = body[prefix + "_rows"]
        if (not isinstance(rows, list) or not rows or len(rows) != body[count]
                or any(not isinstance(r, str) or not r.strip() for r in rows)
                or _rows_sha(rows) != body[sha]):
            raise ValueError("неполные исходные строки %s" % prefix)
        if prefix == "corpus" and len(set(rows)) != len(rows):
            raise ValueError("повтор в переборе")
        if prefix == "book" and len(set(rows)) != body["book_distinct"]:
            raise ValueError("неверное число различных ответов")
        expected = {json.dumps([raw, unit], ensure_ascii=False) for raw in rows for unit in units}
        keys.update(expected)
        for suffix in ("_pairs", "_status"):
            matrix = body[prefix + suffix]
            if set(matrix) != expected or any(not isinstance(v, str) for v in matrix.values()):
                raise ValueError("неполная матрица %s%s" % (prefix, suffix))
        if any(value not in ("ОТКРЫТ", "РЕШЕНИЕ", "ЗНАЧЕНИЕ") and not value.startswith("!")
               for value in body[prefix + "_status"].values()):
            raise ValueError("неизвестный статус")
    if set(body["causes"]) != keys or any(not isinstance(v, str) for v in body["causes"].values()):
        raise ValueError("неполная матрица причин")


def _same_keys(was: dict, now: dict, title: str) -> bool:
    """Совпадают ли НАБОРЫ ключей двух матриц — поимённо, а не числом.

    Прежде `_table` брал `now.get(key, before)`: пропавший ключ молча
    объявлялся «не изменился», а НОВЫЙ ключ не смотрели вовсе — обход
    шёл по `was`. Пустой замер был неотличим от «ничего не изменилось»:
    дамп, снятый без живых книг, давал «ЖИВЫЕ КНИГИ: 511 пар,
    расхождений: 0» (С-5)."""
    a, b = set(was), set(now)
    if a == b:
        return True
    gone, fresh = sorted(a - b), sorted(b - a)
    print("ОШИБКА: наборы ключей «%s» не совпадают — пропало %d, "
          "появилось %d (было %d пар, стало %d)"
          % (title, len(gone), len(fresh), len(was), len(now)),
          file=sys.stderr)
    for label, keys in (("ПРОПАЛ", gone), ("НОВЫЙ", fresh)):
        for key in keys[:10]:
            print("    [%s] %s" % (label, key), file=sys.stderr)
        if len(keys) > 10:
            print("    [%s] … и ещё %d" % (label, len(keys) - 10),
                  file=sys.stderr)
    return False


def _table(was: dict, now: dict, causes: dict, title: str) -> dict:
    lost, gained, swapped, broke = [], [], [], []
    for key, before in was.items():
        after = now[key]              # наборы ключей уже сверены (С-5)
        if str(before).startswith("!") or str(after).startswith("!"):
            broke.append(key)
            continue
        if after == before:
            continue
        if before != "-" and after == "-":
            lost.append(key)
        elif before == "-" and after != "-":
            gained.append(key)
        else:
            swapped.append(key)
    by_cause: dict = {}
    for key in lost:
        by_cause.setdefault(causes.get(key) or "ПРИЧИНА НЕ НАЗВАНА",
                            []).append(key)
    print()
    print("=== %s: %d пар (было %d, стало %d) ==="
          % (title, len(was), len(was), len(now)))
    print("расхождений: %d" % (len(lost) + len(gained) + len(swapped)))
    print("  значение → нет     (ПОТЕРЯ)    : %d" % len(lost))
    print("  нет → значение     (приобретение): %d" % len(gained))
    print("  значение → другое  (ПОДМЕНА)   : %d" % len(swapped))
    print("  ОШИБОК ЗАМЕРА (не значение и не потеря): %d" % len(broke))
    if by_cause:
        print("  потери по причине, которую называет новый код:")
        for cause, keys in sorted(by_cause.items(),
                                  key=lambda kv: -len(kv[1])):
            print("    %-38s %d" % (cause, len(keys)))
    print("  потерь БЕЗ названной причины (о них судит человек): %d"
          % len(by_cause.get("ПРИЧИНА НЕ НАЗВАНА", [])))
    for label, keys in (("ПОДМЕНА", swapped), ("приобретение", gained),
                        ("ОШИБКА ЗАМЕРА", broke),
                        ("ПРИЧИНА НЕ НАЗВАНА",
                         by_cause.get("ПРИЧИНА НЕ НАЗВАНА", []))):
        for key in keys[:40]:
            raw, unit = json.loads(key)
            print("    [%s] %-22s %r: %s → %s"
                  % (label, unit, raw, was[key], now[key]))
        if len(keys) > 40:
            print("    [%s] … и ещё %d" % (label, len(keys) - 40))
    return {"lost": len(lost), "gained": len(gained),
            "swapped": len(swapped), "broken": len(broke),
            "unexplained": len(by_cause.get("ПРИЧИНА НЕ НАЗВАНА", []))}


def _status_table(was: dict, now: dict, title: str) -> dict:
    """Таблица переходов СТАТУСА (С-3). Дороже всех переход в РЕШЕНИЕ:
    вопрос закрывается и блокировка с носителей снимается, а таблица
    ЗНАЧЕНИЙ на нём показывает «-» → «-» и молчит."""
    moves: dict = {}
    broke = 0
    for key, before in was.items():
        after = now[key]
        if str(before).startswith("!") or str(after).startswith("!"):
            broke += 1
            continue
        if after != before:
            moves.setdefault((before, after), []).append(key)
    total = sum(len(v) for v in moves.values())
    print()
    print("=== %s, СТАТУС: %d пар ===" % (title, len(was)))
    print("сдвигов статуса: %d" % total)
    print("  ОШИБОК ЗАМЕРА: %d" % broke)
    for (before, after), keys in sorted(moves.items(),
                                        key=lambda kv: -len(kv[1])):
        mark = "  СНИМАЕТ БЛОКИРОВКУ" if (
            after in ("РЕШЕНИЕ", "ЗНАЧЕНИЕ")
            and before == "ОТКРЫТ") else ""
        print("  %-9s → %-9s %5d%s" % (before, after, len(keys), mark))
        for key in keys[:8]:
            raw, unit = json.loads(key)
            print("      [%s] %r" % (unit, raw))
        if len(keys) > 8:
            print("      … и ещё %d" % (len(keys) - 8))
    return {"moved": total, "broken": broke}


def cmd_compare(args) -> int:
    was = json.loads(Path(args.was).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    now = json.loads(Path(args.now).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(was, dict) or not isinstance(now, dict):
        raise ValueError("дамп должен быть объектом")
    print("было :", was["package_root"])
    print("стало:", now["package_root"])
    print("корпус: %d входов, единиц %d; живые книги: %d книг, %d строк "
          "(%d различных)"
          % (was["corpus"], len(was["units"]), was["books"],
             was["book_lines"], was.get("book_distinct", 0)))
    print("стало: корпус %d, книг %d, строк %d (%d различных)"
          % (now["corpus"], now["books"], now["book_lines"],
             now.get("book_distinct", 0)))
    # СОПОСТАВИМОСТЬ ЗАМЕРОВ (С-5). Прежде сверялись только размер
    # корпуса и список единиц; всё прочее — набор входов, живые книги,
    # сами наборы ключей — могло разойтись молча.
    bad = []
    if was["units"] != now["units"]:
        bad.append("список единиц")
    for field in ("corpus", "corpus_sha", "books", "book_lines",
                  "book_distinct", "books_sha", "books_manifest", "books_manifest_sha"):
        if was.get(field) != now.get(field):
            bad.append("%s (%r против %r)"
                       % (field, was.get(field), now.get(field)))
    for field in ("corpus_status", "book_status"):
        if field not in was or field not in now:
            bad.append("%s: дамп снят прежним замером без статуса" % field)
    if bad:
        print("ОШИБКА: замеры несопоставимы — " + "; ".join(bad),
              file=sys.stderr)
        return 2
    for name, title in (("corpus_pairs", "ПЕРЕБОР"),
                        ("book_pairs", "ЖИВЫЕ КНИГИ"),
                        ("corpus_status", "ПЕРЕБОР"),
                        ("book_status", "ЖИВЫЕ КНИГИ")):
        if not _same_keys(was[name], now[name], title + "/" + name):
            return 2
    try:
        _validate_dump(was)
        _validate_dump(now)
    except (KeyError, TypeError, ValueError) as exc:
        print("ОШИБКА: неполный или недостоверный дамп:", exc, file=sys.stderr)
        return 2
    if "environment" in was or "environment" in now:
        if not isinstance(was.get("environment"), dict) or not isinstance(now.get("environment"), dict):
            raise ValueError("нет сопоставимого доказательства среды")
        for field in ("python", "catalog_sha", "tool_sha256"):
            if was.get("environment", {}).get(field) != now.get("environment", {}).get(field):
                print("ОШИБКА: несопоставимая среда/измеритель:", field, file=sys.stderr)
                return 2
    # Distinct artifact names/checkouts do not prove a distinct product.
    # Validate each manifest first; hex casing is not a code change.
    print("module_sha256 было :", was["module_sha256"])
    print("module_sha256 стало:", now["module_sha256"])
    was_code, now_code = _product_code_sha(was), _product_code_sha(now)
    print("product_code_sha256 было :", was_code)
    print("product_code_sha256 стало:", now_code)
    if was_code == now_code:
        print("ОШИБКА: одинаковый код модуля и зависимостей — "
              "сравнение правки не выполнено; нужны разные редакции "
              "cdpm/ или template_catalog/", file=sys.stderr)
        return 2
    rc = 0
    for name, title in (("corpus_pairs", "ПЕРЕБОР"),
                        ("book_pairs", "ЖИВЫЕ КНИГИ")):
        if _table(was[name], now[name], now["causes"], title)["broken"]:
            rc = 3
    for name, title in (("corpus_status", "ПЕРЕБОР"),
                        ("book_status", "ЖИВЫЕ КНИГИ")):
        if _status_table(was[name], now[name], title)["broken"]:
            rc = 3
    errs = _errors(was) + _errors(now)
    print()
    print("ОШИБОК ЗАМЕРА всего (было + стало): %d" % errs)
    if errs:
        print("ОШИБКА: замер содержит исключения — нулевая цена по таким "
              "парам НЕ УДОСТОВЕРЯЕТСЯ (С-5)", file=sys.stderr)
        rc = 3
    return rc

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="снять исходы одной копии пакета")
    d.add_argument("--package-root", required=True)
    d.add_argument("--books-root", default=None)
    d.add_argument("--catalog-root", default=None,
                   help="каталог данных; умолчание <package-root>/catalog, не окружение")
    d.add_argument("--out", required=True)
    d.set_defaults(func=cmd_dump)
    c = sub.add_parser("compare", help="таблица переходов между дампами")
    c.add_argument("--was", required=True)
    c.add_argument("--now", required=True)
    c.set_defaults(func=cmd_compare)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("ОШИБКА ЗАМЕРА:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _cli_console import force_utf8_console
    force_utf8_console()
    raise SystemExit(main())
