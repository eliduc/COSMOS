# -*- coding: utf-8 -*-
"""Измеритель постановки БЕЗ ЭТАЛОНА (зона CDPM по решению владельца 03.09 — final-report/RAZDELENIE-CDPM-COSMOS-CLD.md; принят из vmp/measure.py): правильность считается из самих
клауз документов и машинных полей свода.

Задача (план 2026-09-02, «правильность CDPM без эталона»): у реальной
авиакомпании эталона нет, значит вердикт «верно / неверно» обязан
вычисляться из источника. Модуль считает три структурные величины и
даёт по каждой поимённый перечень:

* ОСТАТОК ПОТРЕБЛЕНИЯ — значащие величины нормативной клаузы (числа с
  единицами, времена, длительности, доли, коды перечней, дни недели,
  отношения, база начисления), не потреблённые ни одним машинным полем
  строк, построенных на этой клаузе. «Ничего не потеряно».
* ПОЛЯ БЕЗ НОСИТЕЛЯ — машинные значения строки, у которых нет цитаты,
  являющейся подстрокой ТЕКСТА КЛАУЗЫ-источника. «Ничего не выдумано».
* СТРОКИ НА НЕНОРМАТИВНЫХ КЛАУЗАХ — строки свода, построенные на
  клаузе, не прошедшей машинный тест нормативности (нет модальности,
  нет измеримой величины, нет перечня кодов, нет определяющей связки).
  «Ничего не выдумано из служебного текста».

Все три величины считаются на НОСИТЕЛЕ, а не на адресе. Носитель — это
наименьший фрагмент источника, из которого норма выводима целиком: для
абзаца им служит сам абзац, для таблицы — строка вместе с шапкой, а
клетка есть адрес внутри носителя (см. compose_carriers). Иначе всякая
проверка, читающая текст, слепа на табличных документах: модальность
живёт в шапке колонки, величина — в одной клетке, предмет — в соседней.

Плюс служебное: ЗНАМЕНАТЕЛЬ РАЗБОРА — всякий числоподобный знак текста
обязан быть либо заявлен величиной, либо явно отклонён; неразобранные
знаки считаются отдельно и означают слепоту измерителя, а не чистоту
свода (пропущенная величина дала бы сошедшийся баланс — тот самый
класс, который лечим).

Модуль ИЗМЕРЯЕТ и никого не останавливает: замки строятся поверх него
отдельным решением. Разбор величин здесь — эталонная реализация
измерителя; когда конвейер получит свой разборщик (cdpm/values.py),
измеритель обязан перейти на него, чтобы смысл величины был один.

    python -m cdpm.measure МОДЕЛЬ.json КЛАУЗЫ.json [--out отчёт.md]

КЛАУЗЫ.json: [{"sid": "S1", "anchor": "абз.5", "text": "…"}, …] либо
{"clauses": [...]} — форма комплекта заморозки.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

#: Модальные операторы долженствования и запрета (закрытый словарь;
#: жанр нормативного текста, не признак конкретной авиакомпании).
#: Самостоятельные — норму несут сами по себе.
MODAL_VERBS = (
    "обязан", "обязана", "обязано", "обязаны", "должен", "должна",
    "должно", "должны", "не допускается", "допускается", "запрещ",
    "разрешается", "не более", "не менее", "не ранее", "не позднее",
    "не может", "нельзя", "требуется", "ровно",
)
#: Тематические — норму несут ТОЛЬКО вместе с величиной: отдельно
#: стоящее слово «штраф», «предел», «норма» есть тема заголовка, а не
#: норма (план 2026-09-02, И-4).
THEMATIC = ("минимальн", "максимальн", "предел", "норма", "штраф",
            "надбавка", "порог", "коэффициент", "ставка")
MODALITY = MODAL_VERBS + THEMATIC
#: Те же роли по-английски: руководства по производству полётов у
#: многих перевозчиков ведутся на английском, и норма от этого нормой
#: быть не перестаёт. Язык — свойство записи, а не смысла: тест
#: нормативности один, словари объявляются по языкам.
MODAL_VERBS_EN = (
    "shall", "must", "may not", "is not permitted", "is prohibited",
    "not exceed", "at least", "no later than", "no earlier than",
    "required", "mandatory", "no more than", "no less than",
)
THEMATIC_EN = ("minimum", "maximum", "limit", "penalty", "threshold",
               "rate", "allowance")
#: Словари ищутся ПО НАЧАЛУ СЛОВА, а не подстрокой: «продолжение»
#: содержит «должен», «наставник» — «ставка» (ловушка найдена
#: лестницей Л2, 2026-09-02).
_MODAL_RE = re.compile(
    r"(?<![а-яёa-z])(?:" + "|".join(re.escape(w) for w in MODAL_VERBS) + ")")
_THEMATIC_RE = re.compile(
    r"(?<![а-яёa-z])(?:" + "|".join(re.escape(w) for w in THEMATIC) + ")")
_MODAL_EN_RE = re.compile(
    r"(?<![a-zа-яё])(?:" + "|".join(re.escape(w) for w in MODAL_VERBS_EN)
    + r")(?![a-z])")
_THEMATIC_EN_RE = re.compile(
    r"(?<![a-zа-яё])(?:" + "|".join(re.escape(w) for w in THEMATIC_EN)
    + r")(?![a-z])")
#: Предписание настоящим временем по-английски: страдательный залог
#: («is counted», «are planned») и глаголы описания порядка («applies»,
#: «consists of», «includes»).
_PRESCRIPTIVE_EN_RE = re.compile(
    r"(?<![a-z])(?:(?:is|are)\s+[a-z]{3,}(?:ed|en)|applies|apply|"
    r"consists|consist|includes|include|counts|count)(?![a-z])")
#: Запрет грамматикой: «не» + глагол настоящего времени («не
#: назначается», «не выполняются») — норма без словарного слова.
_NEGATION_RE = re.compile(
    r"(?<![а-яё])не\s+[а-яё]{3,}(?:ется|ются|ается|яется|ают|ает|аются)"
    r"(?![а-яё])")
#: Предписание настоящим временем: страдательный залог 3-го лица
#: («засчитывается в служебное время», «планируются все вместе»). Самый
#: частый залог регламентной прозы: норма есть, модального слова нет.
#: Признак засчитывается только развёрнутой клаузе — подпись и
#: колонтитул сказуемого не имеют (найдено на замороженных комплектах
#: 2026-09-03).
_PRESCRIPTIVE_RE = re.compile(
    r"(?<![а-яё])[а-яё]{4,}(?:ется|ются|ается|яется|аются)(?![а-яё])")
#: Отсылка к внешнему документу: клауза сообщает, что норма установлена
#: в другом месте. Нормы она не несёт, но и молчать о ней нельзя —
#: конвейер обязан завести вопрос авиакомпании.
_REFERENCE_RE = re.compile(
    r"(?<![а-яё])(?:см\.\s|смотри\s|в соответствии с|согласно\s|"
    r"установлен[ыао]?\s+(?:приложением|разделом|документом|"
    r"инструкцией|положением)|определя[ею]тся\s+(?:приложением|"
    r"разделом|документом)|приведен[ыао]?\s+в\s+приложении)")
#: Связки определения («X — это Y»). Двоеточие связкой НЕ считается:
#: им пишутся заголовки и реквизиты («Валюта ставок: условные
#: единицы», «База шаблонов: …»), а определение требует сказуемого.
#: Дефис в роли тире («КВС - командир воздушного судна») равноправен:
#: типографский знак — дело набора, а не смысла (найдено на
#: замороженных комплектах 2026-09-03).
DEFINING = (" — ", " – ", " - ", " означает ", " является ", " есть ",
            " means ", " is defined as ", " stands for ")
#: Шапка ОБЪЯВЛЯЕТ назначение колонки. Если колонка названа правилом,
#: требованием или определением, строка такой таблицы несёт норму по
#: ПОЛОЖЕНИЮ, даже когда записана утвердительным настоящим временем без
#: модального слова и без величины («Бортпроводники планируются все
#: вместе»). Слова шапки — жанр делового документа, не признак
#: авиакомпании.
_RULE_COLUMN_RE = re.compile(
    r"(?<![а-яё])(?:правил|требован|ограничен|критери|услови|норматив|"
    r"регламент|формулировк|предписан|запрет|"
    r"rule|requirement|restriction|criteri|condition|provision|wording)",
    re.IGNORECASE)
_DEF_COLUMN_RE = re.compile(
    r"(?<![а-яё])(?:определен|термин|расшифров|обозначен|значен\w*\s+"
    r"термина|definition|term\b|abbreviation|meaning)", re.IGNORECASE)

_DIGIT_RUN = re.compile(r"\d+(?:[.,]\d+)?")
#: Реквизиты записи, списываемые по причине (закрытый перечень; жанр
#: нормативного документа, не признак авиакомпании): идентификатор
#: строки/раздела, номер и дата редакции, скобочный повтор величины в
#: других единицах, ссылка на пункт.
#: Опознаватель записи: «R-12», «КТ-001», «R.1», «П.1.1.3» — номер
#: строки документа, а не величина нормы. Разделитель бывает дефисом и
#: точкой: способ набора номера меняется от документа к документу.
_IDENT_RE = re.compile(
    r"\b[A-ZА-Я]{1,4}[.-]\d+(?:[.-][A-Za-zА-Яа-я0-9]+)*\b|"
    r"\b[A-ZА-Я]{1,4}-[A-Za-zА-Яа-я0-9]+(?:-[A-Za-z0-9]+)*\b")
_VERSION_RE = re.compile(r"\b[A-ZА-Я]-?\d{2,4}[/-][A-Za-zА-Яа-я0-9.-]+\b")
#: Отметка времени записи: дата вместе со временем — ОДИН реквизит.
#: Время внутри отметки нормой не является («2026-07-02 00:00:00» —
#: когда запись занесена в журнал, а не норма расписания); ложное
#: срабатывание найдено на журнале набора H0 2026-09-03.
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b"
    r"|\b\d{1,2}\.\d{2}\.\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b")
_PAREN_REPEAT_RE = re.compile(r"\(([^()]*\d[^()]*)\)")
#: Дни недели: русское написание ↔ машинное значение формы.
WEEKDAY_RU_EN = {
    "понедельник": "monday", "вторник": "tuesday", "сред": "wednesday",
    "четверг": "thursday", "пятниц": "friday", "суббот": "saturday",
    "воскресень": "sunday",
}
#: День недели ищется ЦЕЛЫМ словом с законным окончанием: основа «сред»
#: внутри слова «Средний» днём недели не является (ложное срабатывание
#: найдено на журнале набора H0 2026-09-03).
_WEEKDAY_RE = re.compile(
    r"(?<![а-яё])(?:понедельник|вторник|сред(?:а|у|ы|е|ой|ам|ами)?|"
    r"четверг|пятниц(?:а|у|ы|е|ей|ам)?|суббот(?:а|у|ы|е|ой|ам)?|"
    r"воскресень(?:е|я|ю|ем|ям)?)(?![а-яё])", re.IGNORECASE)
#: Величины. Порядок важен: длинные формы раньше коротких.
_VALUE_PATTERNS = (
    ("time_of_day", re.compile(r"\b\d{1,2}:\d{2}\b")),
    ("duration_hm", re.compile(r"\b\d{1,3}:\d{2}\b")),
    ("percent", re.compile(r"\b\d+(?:[.,]\d+)?\s*%")),
    ("number_with_unit", re.compile(
        r"\b\d+(?:[.,]\d+)?\s+(?:[а-яёa-z]+\s+){0,2}"
        r"(?:минут\w*|часов|часа|час\b|суток|сутки|дней|дня|день|"
        r"единиц\w*|экипаж\w*|пилот\w*|рейс\w*|смен\w*|ноч\w*|"
        r"кредитн\w+\s+час\w*|календарн\w+\s+д\w+|"
        r"minutes?|hours?|days?|nights?|weeks?|months?|sectors?|"
        r"crews?|pilots?|flights?|duties|duty|points?|credit\s+hours?)"
        r"(?![a-zа-яё])", re.IGNORECASE)),
    ("bare_number", _DIGIT_RUN),
)
#: Деловые подписи видов величин — для сообщений замка содержания,
#: задания аудитору и отчёта измерителя (правило владельца: язык без
#: жаргона; машинный вид остаётся в данных протокола).
VALUE_KIND_RU = {
    "number_with_unit": "число с единицей",
    "time_of_day": "время суток",
    "duration_hm": "длительность",
    "percent": "доля",
    "bare_number": "число",
    "code": "код перечня",
    "weekday": "день недели",
    "requisite": "реквизит",
    "unclassified": "неразобранный знак",
}


def kind_ru(kind) -> str:
    """Подпись вида величины по-русски; неизвестный вид — как есть."""
    return VALUE_KIND_RU.get(str(kind or ""), str(kind or ""))


#: База начисления: «за <что>» после числа/ставки.
_PER_RE = re.compile(r"\bза\s+(?:кажд\w+\s+)?([а-яё]+(?:\s+[а-яё]+){0,3})")
#: Коды перечней: 2–4 заглавных латинских/цифровых знака как отдельное
#: слово (коды баз, типов судов; жанровый признак, не наш набор).
_CODE_RE = re.compile(r"\b[A-Z0-9]{2,4}\b")


def _norm(s) -> str:
    return " ".join(str(s or "").replace(" ", " ").split()).casefold()


class Value:
    """Значащая величина клаузы: вид, текст, границы, канон."""

    __slots__ = ("kind", "text", "start", "end", "canon")

    def __init__(self, kind: str, text: str, start: int, end: int):
        self.kind = kind
        self.text = text
        self.start = start
        self.end = end
        self.canon = canon_value(text)

    def __repr__(self):  # pragma: no cover - диагностика
        return f"Value({self.kind}, {self.text!r}, {self.start}:{self.end})"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text,
                "at": [self.start, self.end], "canon": self.canon}


def canon_value(text) -> object:
    """Канон значения: «12:00» -> 720 (минуты), «100» -> 100, «1,5» ->
    1.5, иное — строка без пробелов в нижнем регистре. Число берётся
    как число (ноль — законное значение параметра, а не пустота)."""
    if isinstance(text, bool):
        return text
    if isinstance(text, (int, float)):
        return int(text) if float(text).is_integer() else float(text)
    s = str("" if text is None else text).strip()
    m = re.match(r"^(\d{1,3}):(\d{2})$", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(\d+(?:[.,]\d+)?)", s)
    if m:
        num = m.group(1).replace(",", ".")
        f = float(num)
        return int(f) if f.is_integer() else f
    return " ".join(s.casefold().split())


def iter_values(text: str) -> list[Value]:
    """Значащие величины клаузы с границами. Знаменатель разбора: все
    числоподобные знаки обязаны попасть хотя бы в одну величину;
    непокрытые возвращаются видом «unclassified» — это слепота
    измерителя, а не чистота текста.

    Реквизиты (идентификатор записи, версия, дата редакции, скобочный
    повтор величины в других единицах) заявляются видом «requisite» и
    потребления не требуют — списание по закрытому перечню причин."""
    src = str(text or "")
    out: list[Value] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in taken)

    # Реквизиты — первыми: они «съедают» свои знаки до разбора величин.
    for rx in (_VERSION_RE, _IDENT_RE, _DATE_RE):
        for m in rx.finditer(src):
            if not overlaps(m.start(), m.end()):
                taken.append((m.start(), m.end()))
                out.append(Value("requisite", m.group(0), m.start(), m.end()))
    for m in _PAREN_REPEAT_RE.finditer(src):
        a, b = m.start(1), m.end(1)
        if not overlaps(a, b):
            taken.append((a, b))
            out.append(Value("requisite", m.group(1), a, b))
    for kind, rx in _VALUE_PATTERNS:
        for m in rx.finditer(src):
            a, b = m.start(), m.end()
            if overlaps(a, b):
                continue
            taken.append((a, b))
            out.append(Value(kind, m.group(0).strip(), a, b))
    # Коды перечней и дни недели — величины без цифр.
    for m in _CODE_RE.finditer(src):
        if not overlaps(m.start(), m.end()):
            taken.append((m.start(), m.end()))
            out.append(Value("code", m.group(0), m.start(), m.end()))
    for m in _WEEKDAY_RE.finditer(src):
        if not overlaps(m.start(), m.end()):
            taken.append((m.start(), m.end()))
            out.append(Value("weekday", m.group(0), m.start(), m.end()))
    # Знаменатель: числоподобные знаки вне заявленных величин.
    for m in _DIGIT_RUN.finditer(src):
        if not overlaps(m.start(), m.end()):
            out.append(Value("unclassified", m.group(0), m.start(), m.end()))
    out.sort(key=lambda v: v.start)
    return out


# ------------------------------------------------------- нормативность

#: Разделитель «шапка ¦ строка» внутри собранного носителя. Шапка
#: задаёт СМЫСЛ колонок (модальность, единицу, назначение), но величин
#: строке не поставляет: иначе каждая строка таблицы наследовала бы
#: числа и коды заголовка (ложное срабатывание найдено на журнале
#: набора H0 2026-09-03).
CARRIER_SEP = " ¦ "


def carrier_body(text: str) -> str:
    """Часть носителя, принадлежащая самой строке: величины считаются
    отсюда, а слова-признаки — из носителя целиком."""
    s = str(text or "")
    return s.split(CARRIER_SEP)[-1] if CARRIER_SEP in s else s


def normativity(text: str, role: str | None = None) -> dict:
    """Машинный тест нормативности клаузы: несёт ли она норму.

    role — положение носителя в источнике, когда оно известно заморозке
    («header» — шапка таблицы). Положение равноправно словам: шапка
    задаёт смысл колонок, а не норму.

    Признаки (жанр нормативного текста, независимы от авиакомпании):
    измеримая величина с единицей либо время/длительность/доля;
    модальный оператор долженствования или запрета; перечень кодов;
    определяющая связка. Клауза без признаков нормой не является —
    заголовок, преамбула, оглавление, колонтитул, подпись."""
    src = str(text or "")
    low = _norm(src)
    body = carrier_body(src)          # величины — из строки, не из шапки
    values = [v for v in iter_values(body)
              if v.kind in ("time_of_day", "duration_hm", "percent",
                            "number_with_unit")]
    codes = [v for v in iter_values(body) if v.kind == "code"]
    has_modality = bool(_MODAL_RE.search(low)) \
        or bool(_NEGATION_RE.search(low)) \
        or bool(_MODAL_EN_RE.search(low)) \
        or (bool(values) and (bool(_THEMATIC_RE.search(low))
                              or bool(_THEMATIC_EN_RE.search(low))))
    reference = bool(_REFERENCE_RE.search(low))
    # Определение: связка со сказуемым. Заголовок раздела тоже носит
    # тире («Часть 1 «Пейринг» — а) Жёсткие ограничения»), поэтому
    # связка засчитывается лишь при развёрнутой правой части: не менее
    # четырёх слов после связки и не менее восьми во всей клаузе.
    has_defining = False
    for d in DEFINING:
        i = src.find(d)
        if i < 0:
            continue
        tail = src[i + len(d):]
        if len(tail.split()) >= 4 and len(low.split()) >= 8:
            has_defining = True
            break
    prescriptive = ((bool(_PRESCRIPTIVE_RE.search(low))
                     or bool(_PRESCRIPTIVE_EN_RE.search(low)))
                    and len(low.split()) >= 6)
    signs = {
        "измеримая величина": bool(values),
        "модальность": has_modality,
        "перечень кодов": len(codes) >= 2,
        "определяющая связка": has_defining,
        "предписание настоящим временем": prescriptive,
    }
    # Структурный носитель (заголовок раздела, титул, колонтитул):
    # короткая клауза без завершающей точки, без величины и без
    # модального глагола. Жанровый признак печатного документа —
    # заголовок предложением не оканчивается.
    words = len(low.split())
    heading = (not src.strip().endswith((".", ";", ":"))
               and not values
               and not has_modality
               and words <= 12)
    ok = any(signs.values())
    # Величина сама по себе есть ДАННОЕ, а не норма: «11:30» без
    # предмета — клетка справочника или колонтитул. Норма требует, чтобы
    # рядом с величиной стоял предмет (слова) либо модальность, перечень
    # кодов, связка. Ловушка найдена ступенью Л4 (2026-09-03).
    lone_value = (signs["измеримая величина"] and not has_modality
                  and len(codes) < 2 and not has_defining)
    if lone_value and words - len(values) < 3:
        ok = False
        signs["измеримая величина"] = False
    if heading:
        ok = False
    # Положение в источнике — такой же признак, как слово. Шапка таблицы
    # нормы не несёт ни при какой формулировке: её слова («предельное
    # значение», «не более») задают СМЫСЛ КОЛОНКИ и попадают в носителя
    # каждой строки. Ловушка найдена ступенью Л4 (2026-09-03).
    if role == "header":
        ok = False
    # Положение сообщает НАЗНАЧЕНИЕ носителя: строка таблицы, чью
    # колонку шапка назвала правилом или определением, несёт норму, если
    # в ней есть содержательный текст, а не один адрес.
    if role in ("rule_row", "definition_row") and words >= 6:
        ok = True
    # Отсылка гасит норму, ТОЛЬКО когда других признаков нет: клауза
    # сообщает, что норма установлена в другом документе. Норма со
    # ссылкой в хвосте («не должна превышать значений Приложения 3»)
    # остаётся нормой и вдобавок требует вопроса авиакомпании
    # (найдено на замороженных комплектах 2026-09-03).
    if reference and not any(signs.values()):
        ok = False
    missing = [k for k, v in signs.items() if not v]
    if heading:
        missing = ["структурный заголовок"] + missing
    if reference:
        missing = ["отсылка к внешнему документу"] + missing
    if role == "header":
        missing = ["шапка таблицы"] + missing
    return {"normative": bool(ok), "signs": signs, "missing": missing,
            "heading": heading, "reference": reference, "words": words,
            "role": role}


# ------------------------------------------------- машинные поля строк

def _anchor_keys(item: dict) -> list[str]:
    out = []
    for s in item.get("sources") or ():
        if isinstance(s, dict) and s.get("sid"):
            out.append(f"{s.get('sid')}|{s.get('anchor')}")
    return out


def field_values(item: dict) -> list[dict]:
    """Машинные поля строки, НЕСУЩИЕ ЗНАЧЕНИЕ, с их цитатами.

    Цитата-носитель обязана покрывать величину; дословная цитата всей
    формулировки потребителем не считается (её отсекает проверка длины
    относительно текста клаузы в consumption())."""
    from cdpm import provenance as _prov
    out: list[dict] = []
    fit = item.get("template_fit")
    if isinstance(fit, dict):
        for name, cell in (fit.get("params") or {}).items():
            val = cell.get("value") if isinstance(cell, dict) else cell
            quote = cell.get("quote") if isinstance(cell, dict) else None
            row = {"field": f"params.{name}", "value": val, "quote": quote}
            # Полнота параметров семьи (09.09.2026): значение внесено
            # ответом анкеты — носитель величины ответ, не клауза
            # (существование и применённость ответа судит контракт свода).
            # Предикат — ОБЩИЙ с законом происхождения (A03-мин/K05-мин,
            # ревью 12.09.2026): иначе ячейка с меткой ответа и чужим
            # `source.kind` у измерителя «несома ответом», а у закона
            # происхождения — не ячейка ответа вовсе, и два читателя
            # видят одну ячейку по-разному.
            if _prov.is_answer_cell(cell):
                row["carrier_answer"] = _prov.answer_marker(cell)
            out.append(row)
    if item.get("kind") == "SOFT" and item.get("weight") is not None:
        w = item["weight"]
        quote = item.get("weight_quote")      # слот носителя веса (03.09)
        if isinstance(w, dict):
            quote = quote or w.get("as_written")
            w = w.get("value")
        out.append({"field": "weight", "value": w, "quote": quote})
    crit = item.get("criterion")
    if isinstance(crit, dict):
        for f in ("version", "effective_from"):
            if crit.get(f):
                out.append({"field": f"criterion.{f}", "value": crit[f],
                            "quote": crit.get("as_written")})
        if crit.get("rate_per_duty") is not None:
            out.append({"field": "criterion.rate_per_duty",
                        "value": crit["rate_per_duty"],
                        "quote": crit.get("as_written")})
        rates = crit.get("rates")
        if isinstance(rates, dict):
            for k, v in rates.items():
                out.append({"field": f"criterion.rates.{k}", "value": v,
                            "quote": crit.get("as_written")})
        if crit.get("per"):
            out.append({"field": "criterion.per", "value": crit["per"],
                        "quote": crit.get("as_written")})
    if item.get("direction"):
        # Направление целевой — машинное поле: его носитель обязан быть
        # в клаузе словом («минимум/минимизировать», «максимум»), иначе
        # целевая построена на тексте, который цели не ставит.
        out.append({"field": "direction", "value": item["direction"],
                    "quote": None, "carrier_words":
                    ("минимиз", "минимум", "наименьш") if
                    item["direction"] == "minimize" else
                    ("максимиз", "максимум", "наибольш")})
    rate = item.get("rate")
    if isinstance(rate, dict):
        if rate.get("value") is not None:
            out.append({"field": "rate.value", "value": rate["value"],
                        "quote": rate.get("as_written")})
        if rate.get("per"):
            out.append({"field": "rate.per", "value": rate["per"],
                        "quote": rate.get("as_written")})
    if item.get("tier") is not None:
        # Ярус критерия — машинное значение, названное в клаузе словом
        # «ярус N»; слота цитаты в схеме у него нет (пробел контракта).
        out.append({"field": "tier", "value": item["tier"], "quote": None})
    es = item.get("export_solver")
    if isinstance(es, dict) and es.get("days"):
        for d in es["days"]:
            # Окно дней решателя — машинное поле, и носитель у него
            # СЛОВОМ, как у направления целевой: день назван в клаузе
            # («пятница», friday), иначе окно построено на тексте,
            # который дня не называет. Основа — потому что окончание
            # есть падеж, а не смысл.
            stems = tuple(ru for ru, en in WEEKDAY_RU_EN.items()
                          if en == d) + (str(d),)
            out.append({"field": "export_solver.days", "value": d,
                        "quote": None, "carrier_words": stems})
    answer_cells = {name: cell for name, cell in (fit.get("params") or {}).items()
                    if _prov.is_answer_cell(cell)} if isinstance(fit, dict) else {}
    divergent = set()
    if answer_cells and item.get("params"):
        from cdpm import model as model_mod
        divergent = {p["param"] for p in model_mod.carrier_divergences(item)}
    for p in item.get("params") or ():
        if isinstance(p, dict) and p.get("name"):
            row = {"field": f"params[].{p['name']}", "value": p.get("value"),
                   "quote": p.get("as_written")}
            cell = answer_cells.get(p["name"])
            # Export's legacy view does not invent a second clause source.
            # Inherit only from the same answer cell after the shared carrier
            # comparison, and only for a trace actually inside its quote.
            # The full answer identity is still judged by answer_carriers.
            if (cell is not None and fit.get("category") in ("A", "B")
                    and p["name"] not in divergent
                    and quote_verbatim(p.get("as_written"), cell.get("quote"))):
                row["carrier_answer"] = _prov.answer_marker(cell)
            out.append(row)
    return out


def _covers(value: Value, quote: str, clause: str) -> bool:
    """Цитата покрывает величину: она есть в тексте клаузы и её границы
    накрывают границы величины."""
    if not quote:
        return False
    q = _norm(quote)
    src = _norm(clause)
    if not q or q not in src:
        return False
    # Цитата всей формулировки потребителем не считается.
    if len(q) >= 0.9 * len(src):
        return False
    i = src.find(q)
    vt = _norm(value.text)
    return vt in src[i:i + len(q)]


def _per_base_parts(text) -> list[tuple[str, object]]:
    """Разбор канонической строки значений по базам «GRT:1,PLY:2» ->
    [(GRT, 1), (PLY, 2)]; иначе пусто."""
    out = []
    for part in str(text or "").split(","):
        bits = part.strip().split(":")
        if len(bits) == 2 and bits[0].strip():
            out.append((bits[0].strip(), canon_value(bits[1])))
    return out


#: Приведение единиц времени к минутам. Машинное поле пишется в
#: минутах, документ говорит часами и сутками; без приведения верно
#: взятая величина выглядит остатком, и баланс потребления врёт в
#: сторону «потеряно» (найдено на замороженных комплектах 2026-09-03).
_UNIT_TO_MINUTES = (
    (re.compile(r"(?<![а-яёa-z])(?:час\w*|hours?)(?![a-z])",
                re.IGNORECASE), 60),
    (re.compile(r"(?<![а-яёa-z])(?:сут\w*|дн\w*|день|days?)(?![a-z])",
                re.IGNORECASE), 1440),
    (re.compile(r"(?<![а-яёa-z])(?:недел\w*|weeks?)(?![a-z])",
                re.IGNORECASE), 10080),
    (re.compile(r"(?<![а-яёa-z])(?:минут\w*|minutes?)(?![a-z])",
                re.IGNORECASE), 1),
)


def value_scales(text, canon) -> set:
    """Все законные шкалы величины: само число и его перевод в минуты,
    если при числе стоит единица времени. Сравнение величины с машинным
    полем идёт по пересечению шкал, а не по одной записи."""
    out = set()
    if isinstance(canon, (int, float)) and not isinstance(canon, bool):
        out.add(canon)
        low = _norm(text)
        for rx, factor in _UNIT_TO_MINUTES:
            if rx.search(low):
                got = canon * factor
                out.add(int(got) if float(got).is_integer() else got)
                break
    elif canon not in (None, ""):
        out.add(canon)
    return out


def _weekday_machine(text: str) -> str | None:
    """Машинное написание дня недели по ОСНОВЕ русского слова.

    Словарь ведётся основами («пятниц»), а величина клаузы приходит в
    падеже («пятница», «пятницу»): окончание — свойство записи, а не
    смысла. Точное сравнение делало измеритель слепым к дням окна
    календаря — они висели в остатке потребления при живом машинном
    поле export_solver.days (набор H0, 2026-09-03). Тот же канон уже
    стоял в cdpm.balance (основы через startswith)."""
    low = _norm(text)
    for ru, en in WEEKDAY_RU_EN.items():
        if low.startswith(ru):
            return en
    return None


def _value_matches_field(value: Value, field: dict) -> bool:
    """Величина потреблена полем: канон значения поля совпадает с
    каноном величины (минуты «12:00» ≡ 720), либо значение поля
    содержит текст величины (перечни, дни), либо величина есть элемент
    значения по базам («GRT:1,PLY:2,SVG:1»), либо день недели совпал
    после перевода в машинное написание."""
    fv = field.get("value")
    if fv is None:
        return False
    if canon_value(fv) == value.canon:
        return True
    # Одна и та же величина в разных единицах: «11 часов» ≡ 660 минут.
    if value_scales(value.text, value.canon) & value_scales(
            field.get("quote") or fv, canon_value(fv)):
        return True
    s = _norm(fv)
    v = _norm(value.text)
    if value.kind == "weekday":
        machine = WEEKDAY_RU_EN.get(v) or _weekday_machine(v)
        if machine and machine == s:
            return True
    parts = _per_base_parts(fv)
    if parts:
        if value.kind == "code" and any(v.upper() == k.upper()
                                        for k, _x in parts):
            return True
        if any(_x == value.canon for _k, _x in parts):
            return True
    return bool(v) and (v in s or (value.kind == "code" and v.upper() in
                                   str(fv).upper()))


def registry_codes(model: dict) -> set:
    """Коды, ОБЪЯВЛЕННЫЕ определениями и данными свода (перечни баз,
    типов, должностей). Упоминание такого кода в другой клаузе есть
    ссылка на запись перечня, а не новая величина: её потребило
    определение. Тот же канон уже стоит в cdpm.balance."""
    out: set = set()
    for r in (model or {}).get("rules") or ():
        if not isinstance(r, dict) or r.get("kind") not in ("DEF", "DATA"):
            continue
        for c in r.get("codes") or ():
            if isinstance(c, dict) and c.get("code"):
                out.add(_norm(c["code"]))
        for c in codes_of_text(str(r.get("statement") or "")):
            out.add(_norm(c))
    return out


def codes_of_text(text: str) -> list:
    """Коды перечня в тексте клаузы — распознавателем ядра (cdpm.model),
    чтобы измеритель и свод читали коды ОДНИМ кодом."""
    from cdpm import model as _m
    return [c["code"] for c in _m.codes_from_clause(text or "")]


def consumption(clause_text: str, items: list[dict],
                declared_codes: set | None = None) -> dict:
    """Баланс потребления клаузы: величины, потреблённые полями строк,
    и остаток. Возврат: {values, consumed, unconsumed, unclassified}."""
    values = iter_values(carrier_body(clause_text))
    fields = [f for it in items for f in field_values(it)]
    # Определение и данные машинных полей не несут по контракту
    # (определение — правило без посадки на семью): их величины —
    # содержание определения, потребления не требуют.
    only_definitions = bool(items) and all(
        it.get("kind") in ("DEF", "DATA") for it in items)
    consumed, unconsumed = [], []
    for v in values:
        if v.kind in ("unclassified", "requisite"):
            continue
        if only_definitions:
            consumed.append(dict(v.as_dict(), field="определение: величина "
                                                    "есть содержание"))
            continue
        hit = None
        for f in fields:
            if _value_matches_field(v, f):
                hit = f
                break
        if hit is None and v.kind == "code" \
                and _norm(v.text) in (declared_codes or ()):
            # Код, объявленный определением общего раздела: упоминание
            # в другой клаузе — ссылка на запись перечня, потреблённая
            # определением, а не потерянная величина.
            consumed.append(dict(v.as_dict(),
                                 field="определение: код перечня"))
            continue
        (consumed if hit else unconsumed).append(
            dict(v.as_dict(), field=(hit or {}).get("field")))
    return {
        "values": [v.as_dict() for v in values
                   if v.kind not in ("unclassified", "requisite")],
        "consumed": consumed,
        "unconsumed": unconsumed,
        "requisites": [v.as_dict() for v in values if v.kind == "requisite"],
        "unclassified": [v.as_dict() for v in values
                         if v.kind == "unclassified"],
    }


#: Поля, у которых в сегодняшних схемах НЕТ места для цитаты-носителя
#: (коэффициент штрафа, окно дней решателя). Это пробел контракта, а не
#: ложь свода: измеритель считает их отдельным классом «нет слота
#: носителя» — он закрывается введением слота, а не правкой свода.
NO_CARRIER_SLOT = ("weight", "export_solver.days", "tier")

_ELLIPSIS_RE = re.compile(r"\.{3}|…")
_SEMICOLON_RE = re.compile(r";")


def _quote_in(quote, low: str) -> bool:
    """Дословность цитаты в нормализованном тексте клаузы: подстрока
    целиком либо несколько фрагментов через многоточие, каждый —
    подстрока, в порядке текста (та же дисциплина, что у цитат посадки
    в model.quote_in_text; живой случай 09.09.2026: as_written
    критерия из JPG «… (версия M-036/demo-1)... действует с 2026-08-01»
    посадкой принят, а измеритель считал поля сиротами)."""
    q = _norm(quote)
    if not q:
        return False
    if q in low:
        return True
    # Фрагменты через многоточие; запасной шов — «;» (живой случай на
    # Test 09.09.2026: модель извлечения склеила два фрагмента клаузы JPG
    # через «; » — «…(версия M-036/demo-1); действует с 2026-08-01»).
    for splitter in (_ELLIPSIS_RE, _SEMICOLON_RE):
        parts = [p.strip() for p in splitter.split(q) if p.strip()]
        if len(parts) < 2:
            continue
        pos = 0
        ok = True
        for part in parts:
            i = low.find(part, pos)
            if i < 0:
                ok = False
                break
            pos = i + len(part)
        if ok:
            return True
    return False


def quote_verbatim(quote, clause_text) -> bool:
    """ЗАКОН НОСИТЕЛЯ: дословна ли цитата в тексте клаузы — подстрока
    целиком либо фрагменты (через многоточие, запасным швом «;») в
    порядке текста; нормализация пробелов и регистра.

    Этим законом измеритель судит носителей при выпуске пакета
    (`carriers`), и им же приём извлечения судит цитату величины
    (`model.value_quote_in_text`): приём не вправе быть СТРОЖЕ гейта —
    иначе он отбрасывал бы значения, которые выпуск принимает, — и не
    вправе быть мягче, иначе отказ приходит поздно, на выпуске. Закон
    один; читателей у него двое."""
    return _quote_in(quote, _norm(clause_text))


def carriers(clause_text: str, item: dict) -> list[dict]:
    """Поля строки без носителя: значение есть, а цитаты, покрывающей
    его в тексте клаузы, нет. Различаются два класса: «нет слота
    носителя в схеме» (пробел контракта) и «цитата не из клаузы»
    (ложь свода)."""
    out = []
    low = _norm(clause_text)
    for f in field_values(item):
        if f.get("value") is None:
            continue
        if f.get("carrier_answer") is not None:
            # Носитель — применённый ответ анкеты (параметр семьи по
            # закону полноты 09.09.2026): в клаузе величины нет по
            # построению, и это не сирота. Признак ставится ПРЕДИКАТОМ
            # происхождения, а не пустотой имени: ячейка, назвавшая
            # ответ, остаётся несомой ответом даже с пустым именем —
            # её судит закон происхождения, а не цитатный слой клауз.
            continue
        words = f.get("carrier_words")
        if words:
            # Носитель словом (направление целевой): достаточно, чтобы
            # слово из закрытого перечня стояло в клаузе.
            if any(w in low for w in words):
                continue
            out.append({"rule_id": str(item.get("id")), "field": f["field"],
                        "value": f.get("value"), "quote": None,
                        "kind": "нет слова-носителя в клаузе"})
            continue
        q = f.get("quote")
        ok = bool(q) and _quote_in(q, low)
        if ok:
            continue
        slot = f["field"] not in NO_CARRIER_SLOT
        out.append({"rule_id": str(item.get("id")), "field": f["field"],
                    "value": f.get("value"), "quote": q,
                    "kind": "цитата не из клаузы" if slot
                    else "нет слота носителя в схеме"})
    return out


# --------------------------------------------------------------- носитель

#: Координаты клетки таблицы в якоре заморозки: «табл.2, строка 4, кол 3»
#: (при вложенности берутся координаты САМОЙ ГЛУБОКОЙ таблицы) и
#: «лист «Реестр», строка 7». Грамматика адреса — жанр заморозки, а не
#: признак авиакомпании.
_ANCHOR_TABLE_RE = re.compile(
    r"(?:табл\.?\s*(?P<t>\d+)|лист\s*[«\"'](?P<s>[^»\"']+)[»\"'])"
    r"[^,]*,\s*строка\s*(?P<r>\d+)(?:\s*,\s*кол\w*\s*(?P<c>\d+))?",
    re.IGNORECASE)
#: Тот же адрес, вынесенный читателем в НАЧАЛО текста клаузы
#: («[табл.2, строка 4, кол 3] …») — второй способ записи того же.
_TEXT_COORD_RE = re.compile(r"^\s*\[(?P<addr>[^\]]*строка[^\]]*)\]\s*")


def cell_address(anchor: str, text: str = "") -> tuple | None:
    """Адрес клетки таблицы: (таблица, строка, колонка) или None, если
    клауза не клетка. Читается и из якоря, и из координатного префикса
    текста: способ записи адреса зависит от читателя, смысл — нет."""
    src = str(anchor or "")
    m = list(_ANCHOR_TABLE_RE.finditer(src))
    if not m:
        pre = _TEXT_COORD_RE.match(str(text or ""))
        if not pre:
            return None
        src = pre.group("addr")
        m = list(_ANCHOR_TABLE_RE.finditer(src))
        if not m:
            return None
    last = m[-1]                      # самая глубокая таблица адреса
    label = (f"табл.{last.group('t')}" if last.group("t")
             else f"лист «{last.group('s')}»")
    table = src[:last.start()] + label
    col = last.group("c")
    return (" ".join(table.split()), int(last.group("r")),
            int(col) if col is not None else None)


#: Имя колонки — короткое: «Правило», «Формулировка», «Определение».
#: Предложение именем колонки не бывает. Различение важно: в таблице
#: ПАСПОРТА документа первая строка — не шапка, а запись («Документ |
#: Правила оптимизации планирования экипажей»), и слово «Правила» в
#: названии документа не делает таблицу таблицей правил (ложное
#: срабатывание найдено на наборе H0 2026-09-03).
COLUMN_NAME_WORDS = 3


def _column_named(head_cells: list, pattern) -> bool:
    """Объявляет ли ХОТЬ ОДНА колонка назначение строки: заголовок этой
    колонки короток, как имя, и подходит под словарь назначения."""
    for cell in head_cells or ():
        text = str(cell or "").replace("*", " ").strip()
        if not text or len(text.split()) > COLUMN_NAME_WORDS:
            continue
        if pattern.search(text):
            return True
    return False


def _cell_text(c: dict) -> str:
    """Текст клетки без координатного префикса: координата — адрес, а не
    содержание."""
    t = str(c.get("text") or "")
    return _TEXT_COORD_RE.sub("", t).strip()


def compose_carriers(by_anchor: dict) -> tuple[dict, dict, dict, dict]:
    """НОСИТЕЛЬ НОРМЫ — наименьший фрагмент источника, из которого норма
    выводима целиком (план 2026-09-02, И-2). Для абзаца носитель есть сам
    абзац. Для таблицы носителем служит СТРОКА вместе с шапкой таблицы:
    модальность, величина и её единица разложены по соседним клеткам и
    заголовку колонки, поэтому отдельная клетка — только адрес внутри
    носителя. Читатель, выпускающий одни клетки, лишает конвейер
    носителя, и всякая проверка, читающая текст, слепнет.

    Возвращает: клауза → носитель, носитель → текст, носитель → состав,
    носитель → роль («header» для шапки таблицы)."""
    # Адреса всех клауз: клетка (есть колонка) и готовая строка таблицы
    # (колонки нет — так выпускает строки читатель книги). Оба вида
    # получают шапку своей таблицы: тип источника задаёт читателя, но не
    # смысл (И-6).
    cells: dict[tuple, list] = {}
    whole_rows: dict[tuple, str] = {}
    tables: dict[tuple, set] = {}
    for key, c in by_anchor.items():
        addr = cell_address(c.get("anchor"), c.get("text"))
        if addr is None:
            continue                  # абзац
        sid = str(c.get("sid"))
        table, row, col = addr
        tables.setdefault((sid, table), set()).add(row)
        if col is None:
            whole_rows[(sid, table, row)] = key
        else:
            cells.setdefault((sid, table, row), []).append((col, key))

    heads: dict[tuple, str] = {}
    head_cells: dict[tuple, list] = {}
    head_rows: set = set()
    for (sid, table), rows_seen in tables.items():
        first = min(rows_seen)
        head_rows.add((sid, table, first))
        members = cells.get((sid, table, first))
        if members:
            texts = [_cell_text(by_anchor[k]) for _, k in sorted(members)]
            heads[(sid, table)] = " | ".join(texts)
            head_cells[(sid, table)] = texts
        elif (sid, table, first) in whole_rows:
            text = _cell_text(by_anchor[whole_rows[(sid, table, first)]])
            heads[(sid, table)] = text
            head_cells[(sid, table)] = [p.strip() for p in text.split("|")]

    carrier_of: dict[str, str] = {}
    carrier_text: dict[str, str] = {}
    members_of: dict[str, list] = {}
    roles: dict[str, str] = {}
    for key, c in by_anchor.items():
        addr = cell_address(c.get("anchor"), c.get("text"))
        if addr is None:
            carrier_of[key] = key
            carrier_text[key] = str(c.get("text") or "")
            members_of[key] = [key]
            continue
        sid = str(c.get("sid"))
        ck = f"{sid}|{addr[0]}, строка {addr[1]}"
        carrier_of[key] = ck
        members_of.setdefault(ck, []).append(key)

    def _finish(sid, table, row, body, keys):
        ck = f"{sid}|{table}, строка {row}"
        head = heads.get((sid, table), "")
        is_head = (sid, table, row) in head_rows
        carrier_text[ck] = (body if is_head or not head or head == body
                            else f"{head}{CARRIER_SEP}{body}")
        # Состав носителя ДОБИРАЕТСЯ, а не переписывается: одну и ту же
        # строку таблицы читатель вправе выпустить и клетками, и целой
        # строкой (книга — строкой, docx — клетками), и тогда клауза
        # «табл.N, строка M» тоже входит в состав. Перезапись теряла её
        # из состава, и всякий, кто спрашивает у отчёта «о каких клаузах
        # комплекта он судит», недосчитывался одной (ревью A11, п. 5).
        prior = [k for k in members_of.get(ck, ()) if k not in keys]
        members_of[ck] = keys + prior
        names = head_cells.get((sid, table), [])
        if is_head:
            roles[ck] = "header"
        elif _column_named(names, _RULE_COLUMN_RE):
            roles[ck] = "rule_row"
        elif _column_named(names, _DEF_COLUMN_RE):
            roles[ck] = "definition_row"

    for (sid, table, row), members in cells.items():
        _finish(sid, table, row,
                " | ".join(_cell_text(by_anchor[k])
                           for _, k in sorted(members)),
                [k for _, k in sorted(members)])
    for (sid, table, row), key in whole_rows.items():
        if (sid, table, row) in cells:
            continue                  # строка уже собрана из клеток
        _finish(sid, table, row, _cell_text(by_anchor[key]), [key])
    return carrier_of, carrier_text, members_of, roles


# ------------------------------------------------------------- адресаты

def addressees(model: dict, carrier_of: dict) -> tuple[dict, dict]:
    """Вопросы авиакомпании как адресаты остатка.

    Отсутствие величины в постановке законно ровно тогда, когда о ней
    спрошено или объявлен честный стоп (план 2026-09-02, И-5). Здесь
    считается доступная машине часть: вопрос, сославшийся на клаузу
    (source_refs), и вопрос, процитировавший саму величину в тексте
    вопроса либо в дословном ответе.

    Возвращает: носитель → список опознавателей вопросов; опознаватель
    вопроса → его текст вместе с ответом (для поиска величины)."""
    q_by_clause: dict[str, list[str]] = {}
    q_texts: dict[str, str] = {}
    for q in (model.get("questions") or ()):
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "")
        parts = [str(q.get("title") or ""), str(q.get("text") or "")]
        ans = q.get("answer")
        if isinstance(ans, dict):
            parts.append(str(ans.get("raw") or ""))
        fmt = q.get("answer_format")
        if isinstance(fmt, dict):
            parts.append(str(fmt.get("note") or ""))
        q_texts[qid] = " ".join(parts)
        for ref in (q.get("source_refs") or ()):
            if not isinstance(ref, dict):
                continue
            key = f"{ref.get('sid')}|{ref.get('anchor')}"
            ck = carrier_of.get(key, key)
            q_by_clause.setdefault(ck, []).append(qid)
    return q_by_clause, q_texts


#: Префикс адресата-вердикта в перечне unconsumed_addressed: величина
#: закрыта не вопросом, а словом аудитора цикла (coverage.json, строка
#: с полем value).
VERDICT_ADDRESSEE = "вердикт аудитора"


def value_verdict_index(verdicts: dict | None, carrier_of: dict) -> dict:
    """Вердикты по величинам (ключ (sid, anchor, величина) из
    cdpm.coverage.load_value_verdicts) → {(носитель, величина): вердикт}.
    Якорь вердикта может быть клеткой таблицы — приводится к носителю
    так же, как ссылка строки свода."""
    from cdpm import coverage as cov
    out: dict = {}
    for key, v in (verdicts or {}).items():
        if not (isinstance(key, tuple) and len(key) == 3):
            continue
        # Слово вне закрытого перечня вердиктом не является: величина
        # остаётся без адресата (fail-closed).
        if (v or {}).get("verdict") not in cov.VALUE_VERDICTS:
            continue
        sid, anchor, val = key
        ck = carrier_of.get(f"{sid}|{anchor}", f"{sid}|{anchor}")
        out[(ck, _norm(val))] = v
    return out


def clause_verdict_index(verdicts: dict | None, carrier_of: dict,
                         members_of: dict) -> dict:
    """Вердикты «не правило» по клаузам → {носитель: причина}: носитель
    закрыт вердиктом, если вердикт стоит на нём самом либо на КАЖДОЙ
    его клетке (строка таблицы, у которой все клетки — не норма)."""
    from cdpm import coverage as cov
    on_key: dict = {}
    for key, v in (verdicts or {}).items():
        if not (isinstance(key, tuple) and len(key) == 2):
            continue
        if (v or {}).get("verdict") != cov.NOT_RULE:
            continue
        on_key[f"{key[0]}|{key[1]}"] = v
    out: dict = {}
    for ck, members in members_of.items():
        if ck in on_key:
            out[ck] = on_key[ck]
            continue
        cells = [k for k in members if k != ck]
        if cells and all(k in on_key for k in cells):
            out[ck] = on_key[cells[0]]
    return out


def _addressee_of(value: dict, clause: str, q_by_clause: dict,
                  q_texts: dict, value_verdicts: dict | None = None) -> tuple:
    """Вопрос, взявший величину на себя, и строгость зачёта.

    Строгий зачёт — вопрос назвал саму величину (в тексте вопроса или в
    дословном ответе). Щедрый — вопрос стоит на той же клаузе, но о
    какой её величине спрошено, машине неизвестно. Обе величины
    считаются и предъявляются порознь: разница между ними есть мера
    неопределённости самого учёта, и прятать её нельзя.

    Вердикт аудитора по самой величине (замок содержания) — строгий
    адресат: он назвал величину дословно."""
    text = _norm(value.get("text"))
    v = (value_verdicts or {}).get((clause, text))
    if v:
        return f"{VERDICT_ADDRESSEE}: {v.get('verdict')}", True
    if text:
        for qid, body in q_texts.items():
            if text in _norm(body):
                return qid, True      # вопрос назвал величину
    for qid in q_by_clause.get(clause, ()):
        return qid, False             # вопрос стоит на клаузе
    return None, False


# ------------------------------------------------------------- измерение

def answer_carriers(model: dict) -> list[dict]:
    """Named answer evidence, judged against the full document (P0 2.3).

    Clause structure and answer identity are different judgements. Preserve
    the exact source alongside its result instead of silently skipping it.
    """
    from copy import deepcopy
    from cdpm import provenance
    records = []
    for rid, family, name, cell in provenance.answer_cells(model):
        problems = provenance.cell_problems(model, rid, family, name, cell)
        records.append({
            "rule_id": rid, "field": "params." + name, "carrier": "answer",
            "answer": provenance.answer_marker(cell),
            "source": deepcopy(cell.get("source")),
            "ok": not problems, "problems": problems,
        })
    return records


def measure(model: dict, clauses: list[dict],
            verdicts: dict | None = None) -> dict:
    """Отчёт измерителя по своду и клаузам источника.

    clauses: [{sid, anchor, text}]. Строки свода привязываются к
    клаузам по якорям заморозки (sid|anchor), а СУДЯТСЯ на носителе:
    клетка таблицы объединяется в строку с шапкой (compose_carriers).

    verdicts (cdpm.coverage.load_all_verdicts): вердикты аудитора по
    клаузам и по величинам. Вердикт по величине — адресат остатка
    (величина не потеряна молча: о ней сказано словами); вердикт «не
    правило» по клаузе закрывает нормативный по машинному тесту
    носитель без строк (слово аудитора старше эвристики). Без
    вердиктов отчёт прежний.

    Нормативный носитель без строк (normative_without_rows) закрыт
    также ОТКРЫТЫМ вопросом анкеты, стоящим на носителе (source_refs):
    отсутствие строки законно, пока о клаузе спрошено и ответа нет
    (план 2026-09-02, И-5). Открытость — по cdpm.model.is_open_question
    (нет ответа либо ответ не разобран). Применённый ответ и ответ,
    ожидающий обработчика, носитель НЕ закрывают: вопрос отработан, а
    строки по клаузе так и нет — носитель предъявляется замку снова.
    Для величин (unconsumed_*) адресатом остаётся вопрос в любом
    состоянии — там ответ читается дословно и величину называет."""
    by_anchor: dict[str, dict] = {}
    for c in clauses or ():
        if isinstance(c, dict) and c.get("sid"):
            by_anchor[f"{c.get('sid')}|{c.get('anchor')}"] = c
    items = [r for r in (model.get("rules") or []) if isinstance(r, dict)]
    items += [o for o in (model.get("objectives") or []) if isinstance(o, dict)]
    #: Коды перечней общего раздела — их упоминания в других клаузах
    #: потребило определение (баланс величин, канон cdpm.balance).
    declared = registry_codes(model)

    # Клетки таблицы собираются в носителя-строку: судится носитель.
    carrier_of, carrier_text, members_of, roles = compose_carriers(by_anchor)

    on_clause: dict[str, list[dict]] = {}
    orphan_rows: list[dict] = []
    for it in items:
        # Строка свода вправе сослаться и на клетку (адрес), и на самого
        # носителя — строку таблицы: это одна и та же ссылка, записанная
        # с разной точностью, и сиротой она не делает.
        keys = [k for k in _anchor_keys(it)
                if k in by_anchor or k in members_of]
        if not keys:
            orphan_rows.append({"id": str(it.get("id")),
                                "anchors": _anchor_keys(it)})
            continue
        for k in keys:
            on_clause.setdefault(k, []).append(it)
    on_carrier: dict[str, list[dict]] = {}
    seen_ids: dict[str, set] = {}
    for k, rows in on_clause.items():
        ck = carrier_of.get(k, k)
        bucket = on_carrier.setdefault(ck, [])
        seen = seen_ids.setdefault(ck, set())
        for r in rows:
            if id(r) not in seen:
                seen.add(id(r))
                bucket.append(r)

    # Адресаты остатка: вопрос авиакомпании, сославшийся на клаузу либо
    # процитировавший саму величину (см. addressees), и вердикт
    # аудитора по самой величине (замок содержания).
    q_by_clause, q_texts = addressees(model, carrier_of)
    # Открытые вопросы — единственное определение в cdpm.model (ИР-1):
    # только они закрывают нормативный носитель без строк.
    from cdpm import model as _mdl
    open_qids = {str(q.get("id") or "") for q in _mdl.open_questions(model)}
    vv_index = value_verdict_index(verdicts, carrier_of)
    not_rule_index = clause_verdict_index(verdicts, carrier_of, members_of)

    clause_reports = []
    rows_on_nonnormative: list[dict] = []
    unconsumed_total = 0
    unconsumed_orphan: list[dict] = []
    unconsumed_loose: list[dict] = []
    unconsumed_by_verdict: list[dict] = []
    normative_not_rule: list[dict] = []
    unclassified_total = 0
    no_carrier: list[dict] = []
    covered = 0
    composed = sum(1 for ck, m in members_of.items() if len(m) > 1
                   or ck not in by_anchor)
    for key in members_of:
        text = str(carrier_text.get(key) or "")
        rows = on_carrier.get(key, [])
        norm = normativity(text, roles.get(key))
        cons = consumption(text, rows, declared) if rows \
            else consumption(text, [], declared)
        if rows:
            covered += 1
            if not norm["normative"]:
                for r in rows:
                    rows_on_nonnormative.append(
                        {"rule_id": str(r.get("id")), "clause": key,
                         "missing_signs": norm["missing"],
                         "text": text[:160]})
            for r in rows:
                if r.get("kind") in ("DEF", "DATA"):
                    continue      # определение машинных полей не несёт
                no_carrier += [dict(x, clause=key) for x in carriers(text, r)]
        unconsumed = cons["unconsumed"] if rows else []
        unconsumed_total += len(unconsumed)
        unclassified_total += len(cons["unclassified"])
        # Остаток без адресата: величина клаузы, которую не взяло ни
        # одно машинное поле И о которой не спрошено. Именно она есть
        # молчаливая потеря; остаток с адресатом — законный ход.
        addressed = []
        for v in unconsumed:
            qid, strict = _addressee_of(v, key, q_by_clause, q_texts,
                                        vv_index)
            if qid:
                addressed.append(qid)
            if qid and qid.startswith(VERDICT_ADDRESSEE):
                unconsumed_by_verdict.append({"clause": key,
                                              "value": v.get("text"),
                                              "kind": v.get("kind"),
                                              "verdict": qid})
            if not strict:
                unconsumed_loose.append({"clause": key,
                                         "value": v.get("text"),
                                         "kind": v.get("kind"),
                                         "question": qid})
            if not qid:
                unconsumed_orphan.append({"clause": key,
                                          "value": v.get("text"),
                                          "kind": v.get("kind")})
        # Нормативный по машинному тесту носитель без строк, закрытый
        # вердиктом «не правило» (код структуры или аудитор цикла), —
        # адресован: причина предъявляется, в замок не идёт.
        not_rule = not_rule_index.get(key) if (norm["normative"]
                                               and not rows) else None
        if not_rule:
            normative_not_rule.append({"clause": key,
                                       "reason": not_rule.get("reason"),
                                       "by": not_rule.get("by")})
        # ОТКРЫТЫЙ вопрос анкеты, стоящий на носителе, — адресат и для
        # носителя без строк: отсутствие строки законно, пока о клаузе
        # спрошено и ответа нет (план 2026-09-02, И-5). Применённый
        # ответ носитель не закрывает: вопрос отработан, строки нет.
        asked_on = list(q_by_clause.get(key, ()))
        asked_open = [qid for qid in asked_on if qid in open_qids]
        clause_reports.append({
            "clause": key,
            "sid": key.split("|", 1)[0],
            "members": list(members_of.get(key, [key])),
            "normative": norm["normative"],
            "missing_signs": norm["missing"],
            "cells": len(members_of.get(key, [key])),
            "rows": [str(r.get("id")) for r in rows],
            "values": len(cons["values"]),
            "consumed": len(cons["consumed"]),
            "unconsumed": unconsumed,
            "unconsumed_addressed": addressed,
            "not_rule_verdict": bool(not_rule),
            "questions": asked_on,
            "questions_open": asked_open,
            "unclassified": len(cons["unclassified"]),
            "text": text[:200],
        })
    normative_without_rows = [r["clause"] for r in clause_reports
                              if r["normative"] and not r["rows"]
                              and not r["not_rule_verdict"]
                              and not r["questions_open"]]
    lied = [x for x in no_carrier
            if x["kind"] in ("цитата не из клаузы",
                             "нет слова-носителя в клаузе")]
    no_slot = [x for x in no_carrier if x["kind"] != "цитата не из клаузы"]
    return {
        "counts": {
            "clauses": len(by_anchor),
            "carriers": len(members_of),
            "carriers_composed": composed,
            "clauses_with_rows": covered,
            "normative_without_rows": len(normative_without_rows),
            "rows": len(items),
            "rows_orphan": len(orphan_rows),
            "rows_on_nonnormative": len(rows_on_nonnormative),
            "fields_without_carrier": len(lied),
            "fields_without_carrier_slot": len(no_slot),
            "unconsumed_values": unconsumed_total,
            "unconsumed_without_addressee": len(unconsumed_orphan),
            "unconsumed_without_named_addressee": len(unconsumed_loose),
            "unconsumed_by_verdict": len(unconsumed_by_verdict),
            "normative_not_rule": len(normative_not_rule),
            "questions": len(model.get("questions") or []),
            "unclassified_tokens": unclassified_total,
        },
        "unconsumed_without_addressee": unconsumed_orphan,
        "unconsumed_without_named_addressee": unconsumed_loose,
        "unconsumed_by_verdict": unconsumed_by_verdict,
        "rows_on_nonnormative": rows_on_nonnormative,
        "fields_without_carrier": no_carrier,
        "normative_without_rows": normative_without_rows,
        "normative_not_rule": normative_not_rule,
        "orphan_rows": orphan_rows,
        "clauses": clause_reports,
        "answer_carriers": answer_carriers(model),
    }


def verdict(report: dict) -> tuple[bool, list[str]]:
    """Структурные классы, которые обязаны быть нулевыми на ЛЮБОМ
    наборе: строки на ненормативных клаузах, поля без носителя, строки
    без клаузы-источника. Остаток потребления и нормативные клаузы без
    строк — измеряются и предъявляются, но порог по ним задаётся
    ступенью лестницы, а не кодом."""
    c = report["counts"]
    problems = []
    if c["rows_on_nonnormative"]:
        problems.append(
            "строк на ненормативных клаузах: %d (%s)"
            % (c["rows_on_nonnormative"],
               ", ".join(x["rule_id"] for x in report["rows_on_nonnormative"])))
    lied = [x for x in report["fields_without_carrier"]
            if x["kind"] != "нет слота носителя в схеме"]
    if lied:
        problems.append(
            "полей без носителя в клаузе: %d (%s)"
            % (len(lied), ", ".join("%s.%s" % (x["rule_id"], x["field"])
                                    for x in lied[:10])))
    if c["rows_orphan"]:
        problems.append("строк без клаузы-источника: %d (%s)"
                        % (c["rows_orphan"],
                           ", ".join(x["id"] for x in report["orphan_rows"])))
    return (not problems), problems


def content_lock(report: dict) -> list[str]:
    """Замок содержания (аудит 2026-09-04, п. 2): классы остатка, при
    которых пакет постановки НЕ выпускается, — словами, с именем
    клаузы и величины. (1) Величина клаузы без адресата: не взята ни
    одним машинным полем, не спрошена в анкете, и аудитор цикла не дал
    по ней вердикта «не норма / повтор / справочно». (2) Нормативный
    носитель без единой строки свода, не закрытый ни вердиктом «не
    правило», ни ОТКРЫТЫМ вопросом анкеты на самом носителе (вопрос с
    применённым ответом носитель не закрывает — см. measure()).
    Пусто — замок открыт. Одна функция для сборщика пакета и его гейта:
    пересчёт по протоколу измерителя обязан давать те же слова. Вид
    величины печатается деловой подписью (kind_ru), не машинным
    именем."""
    problems: list[str] = []
    orphan = list(report.get("unconsumed_without_addressee") or [])
    if orphan:
        heads = "; ".join(f"{x.get('clause')}: {kind_ru(x.get('kind'))} "
                          f"«{x.get('value')}»" for x in orphan[:5])
        problems.append(
            f"величин клауз без адресата {len(orphan)} — каждая величина "
            f"документа правил обязана быть взята машинным полем строки, "
            f"спрошена в анкете либо получить вердикт аудитора "
            f"«не норма / повтор / справочно» с причиной (аудит-цикл, "
            f"coverage.json): {heads}")
    bare = list(report.get("normative_without_rows") or [])
    if bare:
        texts = {r.get("clause"): str(r.get("text") or "")[:60]
                 for r in (report.get("clauses") or [])}
        heads = "; ".join(f"{k}: {texts.get(k, '')}" for k in bare[:5])
        problems.append(
            f"нормативных носителей без строк свода {len(bare)} — клауза "
            f"с признаками нормы обязана дать строку свода либо получить "
            f"вердикт аудитора «не правило» с причиной либо открытый "
            f"вопрос анкеты на носителе: {heads}")
    return problems


def render_report(report: dict, header: str = "") -> str:
    c = report["counts"]
    ok, problems = verdict(report)
    lines = ["# Измеритель постановки: свод против клауз источника", ""]
    if header:
        lines += [header, ""]
    lines += [
        f"Вердикт структурных классов: **{'ЧИСТО' if ok else 'ДЕФЕКТЫ'}**", "",
        f"- клауз: {c['clauses']}; со строками: {c['clauses_with_rows']}; "
        f"нормативных без строк: {c['normative_without_rows']}",
        f"- строк свода: {c['rows']}; без клаузы-источника: "
        f"{c['rows_orphan']}; на ненормативных клаузах: "
        f"{c['rows_on_nonnormative']}",
        f"- полей с цитатой не из клаузы: {c['fields_without_carrier']}; "
        f"полей без слота носителя в схеме: "
        f"{c['fields_without_carrier_slot']}",
        f"- остаток непотреблённых величин: {c['unconsumed_values']}; "
        f"без адресата (не взято и не спрошено): "
        f"{c.get('unconsumed_without_addressee', 0)}; без ИМЕНОВАННОГО "
        f"адресата (вопрос величину не назвал): "
        f"{c.get('unconsumed_without_named_addressee', 0)} при "
        f"{c.get('questions', 0)} вопросах; закрыто вердиктом аудитора "
        f"по величине: {c.get('unconsumed_by_verdict', 0)}; нормативных "
        f"носителей без строк, закрытых вердиктом «не правило»: "
        f"{c.get('normative_not_rule', 0)}",
        f"- неразобранных числоподобных знаков (слепота измерителя): "
        f"{c['unclassified_tokens']}",
        "",
    ]
    if problems:
        lines += ["Структурные дефекты:"] + [f"- {p}" for p in problems] + [""]
    lock = content_lock(report)
    if lock:
        lines += ["Замок содержания (пакет не выпускается):"] \
            + [f"- {p}" for p in lock] + [""]
    if report["rows_on_nonnormative"]:
        lines += ["Строки на ненормативных клаузах:"]
        for x in report["rows_on_nonnormative"]:
            lines.append(f"- {x['rule_id']} на {x['clause']}: нет признаков "
                         f"{', '.join(x['missing_signs'])} — «{x['text']}»")
        lines.append("")
    if report["fields_without_carrier"]:
        lines += ["Поля без носителя (значение есть, цитаты в клаузе нет):"]
        for x in report["fields_without_carrier"]:
            lines.append(f"- [{x['kind']}] {x['rule_id']}.{x['field']} = "
                         f"{x['value']!r} (клауза {x['clause']}, цитата "
                         f"{x['quote']!r})")
        lines.append("")
    unc = [(r["clause"], r["unconsumed"]) for r in report["clauses"]
           if r["unconsumed"]]
    if unc:
        lines += ["Непотреблённые величины нормативных клауз:"]
        for key, items in unc:
            lines.append("- %s: %s" % (key, "; ".join(
                "%s «%s»" % (kind_ru(v["kind"]), v["text"]) for v in items)))
        lines.append("")
    if report["normative_without_rows"]:
        lines += ["Нормативные клаузы без единой строки свода:",
                  "- " + ", ".join(report["normative_without_rows"]), ""]
    return "\n".join(lines) + "\n"


def load_clauses(path: Path) -> list[dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        doc = doc.get("clauses") or doc.get("items") or []
    out = []
    for c in doc:
        if not isinstance(c, dict):
            continue
        out.append({"sid": c.get("sid"), "anchor": c.get("anchor"),
                    "text": c.get("text") or c.get("body") or ""})
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Измеритель постановки без эталона: свод против "
                    "клауз источника")
    ap.add_argument("model", help="model.json свода")
    ap.add_argument("clauses", help="клаузы заморозки (JSON)")
    ap.add_argument("--out", default=None, help="файл отчёта (Markdown)")
    ap.add_argument("--json-out", default=None, help="файл отчёта (JSON)")
    a = ap.parse_args(argv)
    model = json.loads(Path(a.model).read_text(encoding="utf-8"))
    clauses = load_clauses(Path(a.clauses))
    rep = measure(model, clauses)
    text = render_report(rep, header=f"Свод: {a.model}")
    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(text)
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(rep, ensure_ascii=False, indent=1))
    print(text)
    ok, _ = verdict(rep)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
