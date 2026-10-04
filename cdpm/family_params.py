# -*- coding: utf-8 -*-
"""Полнота параметров семьи — закон CDPM (поручение владельца
09.09.2026, набор Н0-B): у шаблона (семьи каталога) есть параметры;
если документы авиакомпании задают лишь часть из них, недостающий
параметр НЕ подразумевается нулём и НЕ выводится из заданных —
обработчик обязан спросить авиакомпанию анкетой, а ответ садится
в посадку правила-носителя той же семьи.

Что считается «задано». Семья судится по ВСЕМУ своду: параметр
задан, если его несёт посадка (категория A/B) ЛЮБОГО правила этой
семьи (H0: два правила-носителя rest_floors — VN-023 с min_rest_home
и VN-024 с min_rest_outstation; Н0-B: одно правило-носитель с одним
параметром). «Несёт» значит ЧИТАЕТСЯ в канонической единице записи
каталога (`canonical_value`, правка В-2 неавтор-ревью 12.09.2026):
законный ноль и числовая строка — значение; `true`, «abc», NaN,
перечень и объект — нет, и тогда параметр недостаёт со словами о
причине. Решение «не применяется» закрывает параметр, только если оно
БЕЗУСЛОВНО (В-3): «не применяется для базы SVO; в остальных 10:00» —
условие, а не решение. Полнота — по каталогу: запись семьи вправе объявить
`param_sets` — минимальные полные наборы параметров (days_off_floor:
«месячная норма» ИЛИ «норма в окне»; night_series: «серия подряд» ИЛИ
«счётчик в окне»); семья полна, когда заданные параметры покрывают
хотя бы один набор. Семья БЕЗ объявленных наборов требует все свои
параметры (fail-closed: параметр без объявления не бывает
необязательным — так сказал владелец: «второй параметр не
подразумевается»). Недостающие параметры спрашиваются по набору,
ближайшему к заданному (больше заданных, меньше недостающих, порядок
каталога).

Вопрос строит КОД (как вопросы линии данных): структурный ключ
`Q-3a/3b-параметр-{семья}-{параметр}` (часть по зоне семьи: PAIRING —
3а, иначе 3б), носители — правила семьи, блокировка носителей до ответа
(fail-closed: без значения семья исполнялась бы молча-разрешающе).
Ответ применяет КОД (детерминированно, без модели): значение в единице
параметра (минуты, счёт, время суток, обозначения, часы кредита),
прочитанное грамматикой K02 (parse_value, 12.09.2026: токен числа с
границами, точные дроби, ЧЧ:ММ с минутами 0–59, знак — часть токена,
домен параметра — default_domain), — в посадку ПЕРВОГО носителя семьи
записью `{value, quote, answer}`, где
quote — первое предложение ответа (источник значения — ответ
авиакомпании, не клауза), answer — id вопроса; либо БЕЗУСЛОВНОЕ «не
применяется» — решение записывается ответом, параметр не задаётся.
Безусловен ответ, ВЕСЬ состоящий из фраз отказа. Фраза отказа при
не-отказном остатке — условие, значение, основание или номер строки
анкеты рядом — решением не является, и МЕСТО фразы ничего не меняет:
в том же предложении (В-3), соседним (B1), ДО значения и ПОСЛЕ него
(K-1) — исход один (см. is_declined, _decline_parts_of).
Неразборчивый ответ — «не разобран» (вопрос задаётся снова), никакой
догадки. Тем же законом судится НОМЕР СТРОКИ анкеты при ответе («1.»,
«1)»): он не часть ответа, а что названо цифрой — номер или значение,
— ответ не говорит, поэтому нумерованный ответ не разбирается ни у
одной единицы каталога, включая обозначения (см. line_number_record). Вопрос, ставший лишним (параметр внесён заплатой, семья
осталась без носителей), закрывается кодом ключом «неприменимо» и
снова открывается, если нужда вернулась (та же дисциплина, что у
вопроса истории).
"""
from __future__ import annotations

import copy
import re
import unicodedata as _unicodedata
from collections import deque
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

from cdpm import catalog_bridge as _bridge
from cdpm import model as m

PARAM_QUESTION_MARK = "-параметр-"
PARAM_QUESTION_PREFIXES = ("Q-3a-параметр-", "Q-3b-параметр-")
#: Вопрос параметра — формата value (ключей вариантов у него нет по
#: контракту модели), поэтому решение «не применяется» и снятие кодом
#: узнаются по ТЕКСТУ ответа (is_declined / CODE_CLOSED_RAW), а не по
#: ключу.
CODE_CLOSED_RAW = ("снят кодом: параметр семьи задан сводом либо семья "
                   "осталась без правил-носителей")

#: Фраза отказа авиакомпании. Ищется ГДЕ УГОДНО в предложении, а не
#: только в его начале (шестое неавторское ревью K02, находка K-1):
#: закон отказа не зависит от порядка слов и предложений, иначе
#: исключение, названное ПОСЛЕ значения, молча исчезает из выпускаемой
#: постановки. Левая граница — не буква (цифра, знак, кавычка, начало
#: строки годятся: «1) Не применяется», «600 минут; для SVO не
#: применяется»), правая — граница слова.
_DECLINE_RE = re.compile(
    r"(?<![^\W\d_])(?:не\s+применя(?:ется|ем|ю)|не\s+применимо|неприменимо|"
    r"такого\s+требования\s+(?:у\s+\S+\s+)?нет|требования\s+нет|"
    r"нет\s+(?:такого\s+)?(?:требования|ограничения|правила))\b",
    re.IGNORECASE)
# Грамматика значения (K02, контракт П0 §2.2, 12.09.2026). Токен числа
# имеет ГРАНИЦЫ, и границы эти СИММЕТРИЧНЫ (четвёртое неавторское
# ревью, замечание N1): приклеенный к цифрам вплотную дефис, минус,
# плюс или тире (_GLUE) делает запись нечитаемой с ЛЮБОЙ стороны —
# слева «10,5» не читается как «5», «10-12» — не как «12», «A320» — не
# как 320; справа «7-ми», «75-ти», «72-х», «7+», «7–суток» — не 7, 75,
# 72 и 7. Раньше правая граница запрещала лишь точку, запятую и
# двоеточие С ЦИФРОЙ, и знак в приклейке молча отваливался вместе со
# своим смыслом: «75-ти минут» вносилось как 75 часов кредита, «7-ми
# суток» — как счёт 7 там, где «7 суток» отказывает по слову единицы.
# Закон fail-closed: голое число читается, число со знаком в приклейке
# — НЕТ; вся склейка «7-ми» — ОДНА запись, которую грамматика не
# читает и которую называет заметка отказа (_RECORD_RE). Знак СЛЕВА
# остаётся частью токена («-30» → −30) — он там и объявлен.
# Кроме знака, слева от токена не стоит буква, цифра, точка, запятая,
# двоеточие; справа за цифрами не идёт точка/запятая/двоеточие с
# цифрой («10:30» не даёт
# ни «10», ни «30», дата «10.05.2026» — не число). Дробная часть — через
# точку или запятую; значение — Decimal при чтении цифр и Fraction в
# арифметике (точно, без округления float и без округления контекста
# Decimal). Цифры вне прочитанных токенов (в том числе «A320», «п.4.2»,
# дата) делают предложение нечитаемым — отказ, не пропуск (K02-F2).
_SIGN = r"[-+\u2212]?"                       # минус, плюс, U+2212 MINUS SIGN
#: ЦИФРА ЗНАЧЕНИЯ — только 0–9 (одиннадцатое неавторское ревью, своя
#: находка). `\d` в Python — ВСЯКАЯ десятичная цифра Юникода, и
#: «３０ минут» (полноширинные) и «٣٠ минут» (арабо-индийские) читались
#: как 30: Decimal берёт их наравне с ASCII. Это тот же омоглиф, что у
#: букв (_CONFUSABLE), только у цифр: на вид ответ один, а знаки другие,
#: и длина токена считалась по правилам ASCII («０１» — два значащих
#: знака вместо одного: ведущий ноль не снимался). Значение читается
#: только из цифр 0–9; иная цифра остаётся ВНЕ прочитанного токена и
#: отказывает законом охвата (_digits_covered ищет цифру по-прежнему
#: `\d`), а заметка называет непрочитанную запись. Догадки «это тоже
#: тройка» здесь нет: fail-closed.
_D = r"[0-9]"
#: ЗНАКИ ТИРЕ. Прежде склейка знала четыре знака (дефис, плюс, минус
#: U+2212, тире U+2013/U+2014), и исход разбора решало, КАКИМ знаком
#: набрано тире (одиннадцатое неавторское ревью, находка 2): «7‐ми
#: суток» с U+2010 давало счёт 7 там, где «7-ми суток» с дефисом
#: отказывает; «A320‐200» давало перечень «200, A320» вместо одного
#: обозначения. Вид один, исход разный — принцип 2 ВМП. Перечень набран
#: не рукой, а по Юникоду: ВЕСЬ разряд Dash_Punctuation (Pd) плюс
#: мягкий перенос U+00AD (Cf — на вид тире либо ничего) и знак минус
#: U+2212 (Sm, в разряд Pd не входит). Сторож (K02-11-2) пересчитывает
#: разряд Pd по `unicodedata` и сверяет с этой строкой, поэтому ни
#: правка рукой, ни новая версия Юникода не оставят щели.
_DASHES = ("\u002d\u058a\u05be\u1400\u1806\u2010\u2011\u2012\u2013"
           "\u2014\u2015\u2e17\u2e1a\u2e3a\u2e3b\u2e40\u2e5d\u301c"
           "\u3030\u30a0\ufe31\ufe32\ufe58\ufe63\uff0d\U00010d6e"
           "\U00010ead\u00ad\u2212")
#: Знак в приклейке к цифрам: плюс и любое тире. Класс собирается
#: подстановкой, знаки экранирует re.escape.
_GLUE = r"+" + re.escape(_DASHES)
_TOKEN_L = r"(?<![\w.,:" + _GLUE + r"])"     # левая граница токена
_NUM_R = r"(?![.,:]?" + _D + r")(?![" + _GLUE + r"])"   # правая граница числа
# Слова единиц длительности — минимальный набор до словаря unit_words
# каталога 0.6.0: минуты (мин, мин., минута/минуты/минут/минутах — ×1),
# часы (ч, ч., час/часа/часов/часы/часах — ×60), сутки (сут, сут.,
# сутки, суток, день, дня, дней, дн. — ×1440). Слово кончается на
# границе слова: «минимум», «часть», «дневных» единицами не являются
# (цифра вслед за словом допустима: «10ч30мин»).
_UNIT_R = r"(?![^\W\d_])"                    # слово единицы не продолжается буквой
_MIN_W = r"(?:минут(?:а|ы|ах)?|мин\.?)" + _UNIT_R
_HOUR_W = r"(?:час(?:а|ов|ы|ах)?|ч\.?)" + _UNIT_R
_DAY_W = r"(?:сут(?:ки|ок)?\.?|дн(?:я|ей|\.)|день)" + _UNIT_R
#: Токен числа с необязательной единицей длительности; «10 ч 30 мин» —
#: составная запись (целые часы + целые минуты) — один токен. Слово
#: минут составной записи ловится отдельной группой (`hmw`): заметка
#: отказа называет ИМЕННО то слово, что стоит при числе (K02-T2).
_NUM_T = r"(?P<num>" + _SIGN + _D + r"+(?:[.,]" + _D + r"+)?)"
_QTY_RE = re.compile(
    _TOKEN_L + _NUM_T + _NUM_R
    + r"(?:\s*(?:(?P<h>" + _HOUR_W + r")(?:\s*(?P<hm>" + _D + r"+)" + _NUM_R
    + r"\s*(?P<hmw>" + _MIN_W + r"))?|(?P<mi>" + _MIN_W + r")|(?P<d>"
    + _DAY_W + r")))?", re.IGNORECASE)
#: Запись ЧЧ:ММ; минуты 0–59 судит разбор, не регулярка — чтобы «10:90»
#: было отказом, а не «10» и «90» (правая граница пускает запятую и
#: скобку после записи, но не цифру, букву, второе двоеточие и знак в
#: приклейке — «10:00-12:00» не даёт ни 600, ни 720: закон границы
#: один и для числа, и для записи времени, N1).
_HHMM_RE = re.compile(
    _TOKEN_L + r"(?P<sign>" + _SIGN + r")(?P<h>" + _D + r"{1,3}):(?P<m>"
    + _D + r"{2})(?![\w:" + _GLUE + r"]|[.,]" + _D + r")")
#: СЛОВА ВЕЛИЧИНЫ, которых не читает НИ ОДНА единица каталога 0.5.0, —
#: секунды, недели, месяцы, кварталы, годы и доля (процент, промилле,
#: знак «%»), — плюс латинские написания времени. Прежде словарь знал
#: только минуты, часы и сутки, а ВСЯКОЕ иное слово при числе молча
#: отваливалось вместе со своим смыслом (одиннадцатое неавторское
#: ревью, находка 1 — БЛОКИРУЮЩАЯ, и находка 4): ответ «Не менее 2
#: недель» на параметр-счёт (min_days_off — «норма выходных», единица
#: `count`) садился в посадку правила-носителя как 2 при прочитанных
#: человеком 14, «не более 10 %» у часов на час кредита — как 10 при
#: 0,1, и ложное значение доезжало до model.json пакета при ЗЕЛЁНОМ
#: гейте выпуска. Закон один на все числовые единицы: слово величины,
#: стоящее при числе, СУДИТСЯ ЕДИНИЦЕЙ ПАРАМЕТРА (_UNIT_KINDS) — своё
#: читается, чужое отказывает заметкой, называющей слово и ожидаемую
#: единицу (_foreign_word_note). Приведение к единице параметра знает
#: ровно три объявленных преобразования (минуты ×1, часы ×60, сутки
#: ×1440); неделя, месяц, квартал, год и доля НЕ приводятся — они
#: отказывают, а не пересчитываются: месяц не равен постоянному числу
#: минут, а доля вовсе не время. Граница названа вслух: слово величины
#: судится по СЛОВАРЮ, а существительное при числе единицей не
#: является («8 выходных дней» — счёт 8, «5 смен» — счёт 5), поэтому
#: единица, в словарь не попавшая, пройдёт молча; полный словарь
#: `unit_words`/`subject_words` — каталог 0.6.0, волна 17б.
_SEC_W = r"(?:секунд(?:а|ы|у|ам|ах)?|сек\.?)" + _UNIT_R
_WEEK_W = r"(?:недел(?:я|и|ь|ю|е|ей|ям|ях|ями)|нед\.?)" + _UNIT_R
_MONTH_W = r"(?:месяц(?:а|ев|ы|ам|ах|у)?|мес\.?)" + _UNIT_R
_QUARTER_W = r"(?:квартал(?:а|ов|ы|ам|ах|у)?)" + _UNIT_R
#: Прочие доли года, встречающиеся в документах авиакомпаний.
_PERIOD_W = (r"(?:декад(?:а|ы|у|ам|ах)?|полугод(?:ие|ия|ий|иям|иях)|"
             r"полмесяца|полугодие)" + _UNIT_R)
_YEAR_W = r"(?:год(?:а|ов|ы|ам|ах|у)?|лет|г\.)" + _UNIT_R
#: Доля: слово и ЗНАК. Знак «%» буквой не кончается, поэтому _UNIT_R к
#: нему не применяется — иначе «50%ный» ускользнул бы от закона.
_SHARE_W = (r"(?:(?:процент(?:а|ов|ы|ам|ах|у)?|проц\.?|промилле|"
            r"дол(?:я|и|ей|ю|ям|ях|ями))" + _UNIT_R + r"|[%\u2030])")
_LAT_MIN_W = r"(?:min(?:ute)?s?)" + _UNIT_R
_LAT_HOUR_W = r"(?:h|hrs?|hours?)" + _UNIT_R
_LAT_DAY_W = r"(?:days?)" + _UNIT_R
_LAT_WEEK_W = r"(?:wks?|weeks?)" + _UNIT_R
_LAT_MONTH_W = r"(?:mo|months?)" + _UNIT_R
_LAT_YEAR_W = r"(?:yrs?|years?)" + _UNIT_R
_LAT_SEC_W = r"(?:secs?|seconds?)" + _UNIT_R
#: Род величины → его слова. Порядок перебора — порядок этой таблицы.
_KIND_WORDS = (
    ("min", r"(?:" + _MIN_W + r"|" + _LAT_MIN_W + r")"),
    ("hour", r"(?:" + _HOUR_W + r"|" + _LAT_HOUR_W + r")"),
    ("day", r"(?:" + _DAY_W + r"|" + _LAT_DAY_W + r")"),
    ("sec", r"(?:" + _SEC_W + r"|" + _LAT_SEC_W + r")"),
    ("week", r"(?:" + _WEEK_W + r"|" + _LAT_WEEK_W + r")"),
    ("month", r"(?:" + _MONTH_W + r"|" + _LAT_MONTH_W + r")"),
    ("quarter", _QUARTER_W),
    ("period", _PERIOD_W),
    ("year", r"(?:" + _YEAR_W + r"|" + _LAT_YEAR_W + r")"),
    ("share", _SHARE_W),
)
#: Число, за которым ВПЛОТНУЮ (через пробелы) стоит слово величины.
_ANY_UNIT_RE = re.compile(
    _TOKEN_L + _NUM_T + _NUM_R + r"\s*(?P<w>"
    + r"|".join(r"(?P<k_%s>%s)" % (k, p) for k, p in _KIND_WORDS) + r")",
    re.IGNORECASE)
#: РОДА ВЕЛИЧИНЫ, КОТОРЫЕ СУДЯТСЯ И ЧЕРЕЗ ОПРЕДЕЛЕНИЕ (двенадцатое
#: неавторское ревью, находка Б2 — БЛОКИРУЮЩАЯ). Одиннадцатый круг
#: закрыл слово величины, стоящее ВПЛОТНУЮ к числу (`\s*` в
#: _ANY_UNIT_RE), и на том же ответе одно прилагательное между числом и
#: словом снимало весь закон: «Не менее 2 календарных недель» у
#: счётного параметра давало 2 при прочитанных человеком 14, «2 рабочих
#: недели» — 2, «3 календарных месяца» — 3, «не более 10 условных
#: процентов» у часов на час кредита — 10 вместо 0,1 (та же ошибка в
#: сто раз), со статусом «применён» и снятой блокировкой. Половины
#: закона разошлись: «2 недель» отказывает, «2 календарных недель» —
#: значение 2; смысл один, исход разный (принцип 2 ВМП).
#:
#: Окно — 1–2 слова, и оно даётся ТОЛЬКО тем родам, которых не читает
#: НИ ОДНА единица каталога (_UNIT_KINDS не называет их нигде) и
#: которые не бывают ПРЕДМЕТОМ счёта. Минуты, часы и сутки окна не
#: получают: слово «дней» бывает существительным при числе («8 выходных
#: дней» — счёт 8), и окно съело бы законный ответ. Неделя, месяц,
#: квартал, доля года, год, секунда и доля предметом счёта не бывают —
#: их называют величиной, а не считают, — поэтому число, при котором
#: они стоят, своей величины лишено, и голым счётом оно не является ни
#: при какой ширине окна. Приведения при этом не появилось: неделя не
#: становится 14 днями, процент — 0,01. Отказ снимается ТОЛЬКО у
#: распознанной календарной оговорки после слова собственной величины
#: (_calendar_qualified_unit). Это не сверка с периодом параметра.
#: Цена широкого варианта (окно ВСЕМ родам) измерена и он ОТВЕРГНУТ —
#: см. руководство 10 и K02-12-2.
_GAP_KINDS = ("sec", "week", "month", "quarter", "period", "year", "share")
#: Слово между числом и словом величины: буквы (любого алфавита), без
#: цифр и знаков. Цифра, запятая, скобка и точка окно РВУТ — «2, 3
#: недели» и «2 (см. 3) недели» перечнем определений не являются.
_GAP_WORD = r"(?:\s+[^\W\d_]+){1,2}\s+"
_GAP_UNIT_RE = re.compile(
    _TOKEN_L + _NUM_T + _NUM_R + _GAP_WORD + r"(?P<w>"
    + r"|".join(r"(?P<k_%s>%s)" % (k, p) for k, p in _KIND_WORDS
                if k in _GAP_KINDS) + r")",
    re.IGNORECASE)
#: Рода величины, которые ЧИТАЕТ единица параметра. Пустой набор —
#: единица не читает НИ ОДНОГО слова величины: счёт не знает, что он
#: считает (до словаря каталога 0.6.0), а время суток пишется записью
#: ЧЧ:ММ, и слово величины при числе там — чужая запись.
_UNIT_KINDS = {
    "duration_minutes": ("min", "hour", "day"),
    "count": (),
    "time_of_day_minutes": (),
    "credit_hours": ("hour",),
    "hours_per_credit_hour": ("hour",),
}
#: ОМОГЛИФЫ — латинские буквы, неотличимые на вид от кириллических
#: (одиннадцатое неавторское ревью, находка 3). Свёртка 1:1 по знакам,
#: поэтому длина строки не меняется и границы совпадений остаются
#: годными для выдержки ДОСЛОВНО из ответа. Свёртка применяется ТОЛЬКО
#: к узнаванию слова величины (_foreign_unit_at) — то есть в сторону
#: ОТКАЗА: «2 cутok» у счёта отказывает так же, как «2 суток». Читать
#: по свёртке ЗНАЧЕНИЕ нельзя: это была бы догадка о том, что
#: авиакомпания имела в виду, — «2 cутok» у длительности остаётся
#: нечитаемым (число без единицы), а не 2880.
_CONFUSABLE = str.maketrans("aAcCeEoOpPxXyYkKmMtTBH",
                            "аАсСеЕоОрРхХуУкКмМтТВН")
#: Буквы двух алфавитов — для закона смешанного слова.
_LAT_LETTER_RE = re.compile(r"[A-Za-z]")
_CYR_LETTER_RE = re.compile(r"[\u0400-\u04ff]")
_WORDISH_RE = re.compile(r"\w+")


def _folded(s: str) -> str:
    """Ответ со свёрнутыми омоглифами: латинская буква, неотличимая на
    вид от кириллической, заменяется кириллической. Длина не меняется."""
    return s.translate(_CONFUSABLE)


def mixed_script_word(raw) -> str:
    r"""Первое слово ответа, записанное ДВУМЯ алфавитами сразу (в одном
    слове и латиница, и кириллица), — дословно, либо «».

    ЗАКОН (одиннадцатое неавторское ревью, находка 3; принципы 2 и 3
    ВМП): такое слово не читается ВОВСЕ — ни как единица, ни как
    обозначение, ни как фраза отказа, — ответ «не разобран», вопрос
    остаётся открытым, заметка называет слово. Это не догадка о смысле,
    а отказ читать то, что НА ВИД одно, а по знакам другое.

    Цена закона названа: смешанное слово не читается даже там, где
    человек прочёл бы его без труда. Взамен закрыта ложная ПОСАДКА:
    ответ «600 минут. Hе применяется для базы SVO.» с латинской «H»
    садился в посадку правила-носителя значением 600 — фраза отказа не
    узнавалась (_DECLINE_RE набран кириллицей), исключение молча
    исчезало из выпускаемой постановки, статус «применён», блокировка
    снята. Тот же ответ кириллицей отказывает. Вид один, исход разный —
    и разный в сторону fail-open, с НЕВЕРНЫМ значением на конце.

    Слово — прогон `\w+`, поэтому цифры и подчёркивание алфавита не
    задают: «А320» (кириллическая «А») смешанным словом не считается,
    его судит закон охвата обозначений (_unread_code_records), а
    «PDF-файл» и «IT-отдел» — два слова, каждое своего алфавита."""
    text = raw if isinstance(raw, str) else ""
    for mm in _WORDISH_RE.finditer(text):
        w = mm.group()
        if _LAT_LETTER_RE.search(w) and _CYR_LETTER_RE.search(w):
            return w
    return ""


def _foreign_unit_at(s: str, unit: str) -> str:
    """Первое неподдерживаемое слово величины — дословно из ответа.

    Проверяем слово у числа и через окно в 1–2 слова. Своя величина
    сама по себе НЕ разрешает отбросить хвост: «10 часов в процентах»,
    «в секунду», «в неделях» не являются абсолютным числом часов.
    Локальное календарное исключение после своего слова называет
    _CALENDAR_TAIL_RE. Оно НЕ разрешает применение нормы: parse_value
    независимо отвергает период во всём ответе через period_qualifier.
    Секунда и доля в локальное исключение не входят.

    Ответ и свёртка омоглифов судятся каждый целиком в своём тексте;
    свёртка может добавить отказ, но не прочитанное значение.

    Периодическая норма не поддержана текущим контрактом числового
    параметра и остаётся открытой, а не сравнивается по эвристике
    имени. Словари не охватывают произвольную речь. Этот локальный
    судья слова единицы не заменяет общий рубеж сохранения периода.
    """
    allowed = _UNIT_KINDS.get(str(unit or ""))
    if allowed is None:
        return ""
    best, word = None, ""
    for rx in (_ANY_UNIT_RE, _GAP_UNIT_RE):
        # Своими для окна не бывает ни один род (_GAP_KINDS и
        # _UNIT_KINDS не пересекаются), но проверка одна на обоих
        # судей: закон «своё читается, чужое отказывает» здесь один.
        kinds = [k for k in allowed if ("k_" + k) in rx.groupindex]
        for text in (s, _folded(s)):
            for mm in rx.finditer(text):
                if any(mm.group("k_" + k) for k in kinds):
                    continue
                if rx is _GAP_UNIT_RE and _calendar_qualified_unit(text, mm):
                    continue    # только названная календарная оговорка
                if best is None or mm.start("w") < best:
                    best = mm.start("w")
                    word = s[mm.start("w"):mm.end("w")]
    return word if best is not None else ""


_CALENDAR_TAIL_RE = re.compile(
    r"(?:в|за|на|по)\s+(?:неделю|нед\.?|месяц|мес\.?|квартал|"
    r"декаду|полугодие|полмесяца|год|г\.)", re.IGNORECASE)


# Numeric units currently have no declared parameter-period contract. A
# periodic norm cannot be reduced to its magnitude, regardless of whether
# the annotation precedes, follows or occupies a separate sentence.
_PERIOD_NOUN = r"(?:" + "|".join((
    _MIN_W, _HOUR_W, _DAY_W, _SEC_W, _WEEK_W, _MONTH_W, _QUARTER_W,
    _PERIOD_W, _YEAR_W,
    r"минут(?:у|е|ой|ам|ами)?" + _UNIT_R,
    r"час(?:у|е|ам|ами)?" + _UNIT_R,
    r"(?:сут(?:кам|ках|ками)|дн(?:е|ям|ях|ями|ём))" + _UNIT_R,
    r"смен(?:а|ы|у|е|ам|ах)?" + _UNIT_R,
    r"рейс(?:а|ов|ы|у|е)?" + _UNIT_R,
    r"пол[её]т(?:а|ов|ы|у|е)?" + _UNIT_R,
)) + r")"
_PERIOD_MODIFIER = (
    r"(?:кажд[а-яё]*|календарн[а-яё]*|рабоч[а-яё]*|полн[а-яё]*|"
    r"од(?:ин|на|но|ни|ного|ной|ному|ну|ним|ном|ними|них))")
# One declared phrase: a simple/compound preposition, up to four modifier
# or count tokens, then a period noun. The fixed bound also prevents a
# repeated 'каждые' prefix without a noun from causing quadratic searches.
_PERIOD_QUALIFIER_RE = re.compile(
    r"(?P<period>(?:(?<!\w)(?:в\s+(?:течение|пределах)|на\s+протяжении|"
    r"в|во|за|на|по|каждый|каждую|каждое|каждые)\s+|/\s*)"
    r"(?:(?:" + _PERIOD_MODIFIER + r"|[0-9]+(?:[.,][0-9]+)?)\s+){0,4}" + _PERIOD_NOUN
    + r"|(?<!\w)(?:ежедневно|ежесуточно|еженедельно|ежемесячно|ежеквартально|"
    r"ежегодно|ежечасно|ежеминутно|посуточно|понедельно|поминутно)(?!\w))", re.IGNORECASE)


_PERIOD_MAGNITUDE_RE = re.compile(
    r"(?:в|во|за|на|по)\s+[0-9]+(?:[.,][0-9]+)?\s+(?:"
    + "|".join((_MIN_W, _HOUR_W, _DAY_W)) + r")", re.IGNORECASE)
_PERIOD_DIGIT_RE = re.compile(r"\d")
_MAGNITUDE_NEGATION_RE = re.compile(
    r"(?<!\w)(?:не(?!\s+(?:более|менее)(?!\w))|нет|без|кроме)(?!\w)",
    re.IGNORECASE)
_PENALTY_WEIGHT_NOTE_RE = re.compile(
    r"(?<!\w)штраф(?:ом|а)?\s*\(\s*вес\s+"
    r"[0-9]{1,9}(?:[.,][0-9]{1,6})?\s+"
    r"(?P<period>за\s+(?:смену|рейс|пол[её]т))"
    r"(?:\s+с\s+превышением)?\s*\)", re.IGNORECASE)


def _separate_penalty_period(text, unit):
    """One explicitly labelled penalty note after a complete duration sentence.

    This does not discard arbitrary later sentences with their own numbers.
    Only the declared parenthesized weight grammar has a distinct subject.
    Include the note's first letter when splitting the prefix: its last
    sentence is then the unfinished note sentence, never the value sentence.
    At most one note is exempted; scans and preceding sentences stay linear.
    """
    if unit != "duration_minutes":
        return None
    note = _PENALTY_WEIGHT_NOTE_RE.search(text)
    if note is None:
        return None
    before = _sentences(text[:note.start() + 1])
    if len(before) < 2 or _MAGNITUDE_NEGATION_RE.search(before[-1]):
        return None
    for sent in before[:-1]:
        status, _ = _parse_sentence(sent, unit)
        if status == _VALUE:
            return note.span("period")
        if status == _REFUSED or _has_payload(sent, unit):
            return None
    return None


def period_qualifier(raw, unit) -> str:
    """Unsupported period, verbatim; never infer one from a parameter name.

    This declares a bounded vocabulary, not arbitrary natural-language
    understanding. A future period-aware catalog/API must explicitly retain
    and validate the relation before admitting these qualified values.
    A simple preposition introducing the answer's sole numeric magnitude
    ("перерыв в 30 минут") is not a period. Extra numbers or explicit
    periodicity or unresolved negation remain conservative refusals; a later
    qualifier is still read. This is not general condition/negation parsing.
    A separately labelled parenthesized penalty weight can qualify the
    penalty, not an earlier duration; only _separate_penalty_period's
    declared grammar is exempt, never an unbound later periodic norm.
    """
    if not isinstance(raw, str) or str(unit or "") not in _NUMERIC_UNITS:
        return ""
    best = None
    for text in (raw, _folded(raw)):
        penalty_period = _separate_penalty_period(text, str(unit or ""))
        unit_spans = []
        if _PERIOD_QUALIFIER_RE.search(text):
            source = _modern_unit_source()
            if source is not None:
                from .catalogue_numeric import word_spans
                unit_spans = word_spans(text, str(unit), source)
        for match in _PERIOD_QUALIFIER_RE.finditer(text):
            if any(start <= match.start() and match.end() <= end for start, end in unit_spans):
                continue
            if match.span() == penalty_period:
                continue
            if _PERIOD_MAGNITUDE_RE.fullmatch(match.group()):
                first_digit = _PERIOD_DIGIT_RE.search(text)
                if (first_digit is not None and first_digit.start() >= match.start()
                        and _PERIOD_DIGIT_RE.search(text, match.end()) is None
                        and _MAGNITUDE_NEGATION_RE.search(text) is None):
                    continue
            if best is None or match.start() < best.start():
                best = match
            break
    return raw[best.start():best.end()] if best is not None else ""


# Applicability is not represented by parse_value's (scalar, quote) result.
# Until a typed condition writer/consumer is available, these explicit scope
# introductions must refuse the WHOLE answer, including later sentences.
# This is a declared, bounded grammar, not general natural-language parsing:
# no base codes, crew roles or airline-specific vocabulary are enumerated.
_WEEKDAY_SCOPE = (
    r"(?:по\s+(?:понедельникам|вторникам|средам|четвергам|пятницам|"
    r"субботам|воскресеньям|выходным|будням)|"
    r"(?:в|во)\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|"
    r"воскресенье|выходные|будни))")
# Effective dates are applicability, not part of an unconditional magnitude.
# Bounded explicit date grammar; this does not infer arbitrary temporal prose
# or accept a typed date-conditioned norm (no such scalar consumer exists).
_EFFECTIVE_DATE_SCOPE = (
    r"(?:с|до|после|начиная\s+с)\s+(?:"
    r"[0-9]{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|"
    r"августа|сентября|октября|ноября|декабря)|"
    r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|"
    r"[0-9]{1,2}[./][0-9]{1,2}(?:[./][0-9]{2,4})?)")
_APPLICABILITY_INTRO_RE = re.compile(
    r"\b(?:для|при|если|когда|кроме|исключая|только|лишь|без|"
    r"за\s+исключением|в\s+случае|в\s+зависимости\s+от|"
    r"с\s+(?:согласия|разрешения)|"
    r"(?:на|в)\s+(?:базе|базах|аэропорту|аэропортах|типе|типах)|"
    + _WEEKDAY_SCOPE + "|" + _EFFECTIVE_DATE_SCOPE + r")\b",
    re.IGNORECASE)
_BASE_LIMIT_RE = re.compile(r"^базовый\s+предел\b", re.IGNORECASE)
_SEPARATE_EXTENSION_RE = re.compile(
    r"(?P<scope>с\s+согласия\s+[^\W\d_]+(?:\s+[^\W\d_]+){0,3}?)"
    r"\s+допускается\s+продление\s+до\s+(?P<duration>.+)$",
    re.IGNORECASE)
_UNIVERSAL_SCOPE_RE = re.compile(
    r"для\s+(?:всех(?:\s+(?:баз|членов\s+экипажа))?|"
    r"каждого\s+(?:члена\s+экипажа|ЧЭ)|любого\s+экипажа|экипажа)\b",
    re.IGNORECASE)
_SCOPE_CLAUSE_END_RE = re.compile(r"[\r\n!?;]|\.(?![0-9])")


def _universal_scope(text, marker, unit, clause_ends) -> bool:
    """Recognize only an unqualified universal domain, never any 'для всех'.

    'Для каждого ЧЭ увеличенного состава' is still restricted. No arbitrary
    noun/modifier after the explicit universal phrase may disappear. A scalar
    after the phrase is permitted, so prefix/suffix orders agree. Matching
    stays linear across repeated clauses by bounding the inspected fragment.
    """
    phrase = _UNIVERSAL_SCOPE_RE.match(text, marker.start())
    if phrase is None:
        return False
    while clause_ends and clause_ends[0] < phrase.end():
        clause_ends.popleft()
    end = clause_ends[0] if clause_ends else len(text)
    tail = text[phrase.end():end]
    tokens = _sentence_tokens(tail, unit)
    if tokens is None:
        return False
    mask = _covered_mask(tail, [span for _, span in tokens])
    residue = "".join(char for i, char in enumerate(tail) if not mask[i])
    if unit == "credit_hours":
        residue = re.sub(r"\bкредита\b", "", residue, flags=re.IGNORECASE)
    return re.fullmatch(r"[\s,:()\[\]«»\"'=" + re.escape(_DASHES) + r"]*",
                        residue) is not None


def _separate_extension_scope(text, unit):
    """A labelled base limit followed by a separately scoped extension.

    Preserve the existing BOOK_V contract: this reader returns the explicitly
    labelled base, not the extension. Only this complete sentence grammar is
    exempt, never a bare value followed by an applicability clause. Returning
    one marker offset leaves all other clauses subject to the same guard.
    """
    if unit != "duration_minutes":
        return None
    sentences = _sentences(text)
    if len(sentences) < 2 or not _BASE_LIMIT_RE.search(sentences[0]):
        return None
    base = sentences[0]
    if _APPLICABILITY_INTRO_RE.search(base):
        return None
    status, base_value = _parse_sentence(base, unit)
    if status != _VALUE:
        return None
    extension = _SEPARATE_EXTENSION_RE.fullmatch(sentences[1])
    if extension is None:
        return None
    status, extended_value = _parse_sentence(extension.group("duration"), unit)
    if status != _VALUE or extended_value < base_value:
        return None
    # _sentences normalizes whitespace; use the original scan for its offset.
    for marker in _APPLICABILITY_INTRO_RE.finditer(text):
        if re.fullmatch(r"с\s+согласия", marker.group(), re.IGNORECASE):
            return marker.start()
    return None


def applicability_qualifier(raw, unit) -> str:
    """Verbatim unsupported applicability clause, or empty.

    Universal unqualified domains have an explicit grammar (_universal_scope);
    universal quantifiers over a restricted group still refuse. Never ask
    the user to delete a scope and turn a conditional norm unconditional.
    Existing structured qualifier maps bypass this free-text grammar.
    """
    if not isinstance(raw, str) or str(unit or "") not in _NUMERIC_UNITS:
        return ""
    starts = []
    for text in (raw, _folded(raw)):
        exempt = _separate_extension_scope(text, str(unit or ""))
        clause_ends = deque(m.start() for m in _SCOPE_CLAUSE_END_RE.finditer(text))
        for match in _APPLICABILITY_INTRO_RE.finditer(text):
            if match.start() != exempt and not _universal_scope(
                    text, match, str(unit), clause_ends):
                starts.append(match.start())
                break
    if not starts:
        return ""
    start = min(starts)
    end = re.search(r"[\r\n.!?;]", raw[start:])
    return raw[start:start + end.start() if end else len(raw)].strip()


def _calendar_qualified_unit(text: str, match) -> bool:
    """У числа есть единица, а остаток — объявленная форма периода.

    Не выводим период по имени параметра и не превращаем долю/скорость
    в абсолютную величину. Все границы относятся к одному text.
    """
    if not _own_unit_at(text, match.start("num")):
        return False
    own = _ANY_UNIT_RE.match(text, match.start("num"))
    tail = text[own.end("w"):match.end("w")].strip()
    return _CALENDAR_TAIL_RE.fullmatch(tail) is not None


def _own_unit_at(text: str, start: int) -> bool:
    """Стоит ли ВПЛОТНУЮ к числу, начинающемуся в `text` с места
    `start`, слово величины (любого рода) — то есть НЕ лишено ли это
    число своей величины. Это необходимая, но уже не достаточная
    посылка исключения окна: хвост отдельно судит _calendar_qualified_unit.

    ТЕКСТ ПЕРЕДАЁТ ВЫЗЫВАЮЩИЙ, и это РУБЕЖ, а не удобство: отметка
    обязана сниматься по ТОМУ ЖЕ тексту, по которому идёт судья с окном
    (ответ как есть либо свёртка омоглифов). Иначе один исход
    складывался бы из двух разных текстов, и свёртка дарила бы отказ
    там, где в её собственном чтении посылка окна не выполнена. Шов
    достижим, и его держит проба K02-13-S1 («10 cytok в mec»), а не
    молчание — см. _foreign_unit_at.

    Судит тот же _ANY_UNIT_RE, что и первый судья, — закон «слово
    величины при числе» здесь ОДИН, и ширина окна его не меняет. Род
    слова не спрашивается: величина у числа есть и тогда, когда единица
    параметра её не читает, — но там отказ уже вынес первый судья, и
    вынес РАНЬШЕ по месту (слово вплотную стоит левее рода окна),
    поэтому заметка называет то же слово, что и прежде.

    Сравнение по НАЧАЛУ числа, а не по концу: `re.finditer` совпадения
    не перекрывает, и отметка, снятая перебором по всей строке, у
    длинного ответа зависела бы от того, что нашлось левее. Проверка
    ставится на место (`_ANY_UNIT_RE.match(text, start)`); взгляд
    назад (_TOKEN_L) при этом сохраняется — совпадение с середины
    токена не засчитывается."""
    return _ANY_UNIT_RE.match(text, start) is not None


#: ОБОЗНАЧЕНИЕ справочника (десятое неавторское ревью K02, B-1):
#: не длиннее 16 знаков, из букв латиницы, цифр, подчёркивания и дефиса
#: — и обязано нести ХОТЯ БЫ ОДИН буквенно-цифровой знак. Дефис и
#: подчёркивание — знаки ВНУТРИ обозначения («A320-200», «737-MAX»,
#: «O-S1-»), сами по себе они обозначением не являются. Прежде токен из
#: одних дефисов был законным обозначением, и ответ «нет данных - »
#: садился в посадку правила-носителя значением «-», ответ «A320 -
#: B738» — перечнем «-, A320, B738», а живая книга polyaris-nano
#: (Q-3a-критерий-пейринга) давала перечень «-, 100»: в постановку
#: входил тип судна, которого авиакомпания не называла, статус «применён»,
#: блокировка снята — fail-open с НЕВЕРНЫМ значением (принципы 3 и 4
#: ВМП). Исход зависел и от того, какое тире набрано: «A320 — B738» с
#: длинным тире разбирался верно, «A320 - B738» с дефисом — нет
#: (принцип 2). Пустое обозначение исходом разбора не становится: где
#: знаков справочника нет вовсе, значения нет, и вопрос остаётся
#: открытым (см. _parse_sentence, _has_payload).
_CODE_RE = re.compile(
    r"^(?=[A-Za-z0-9_\-]{1,16}$)[A-Za-z0-9_\-]*[A-Za-z0-9][A-Za-z0-9_\-]*$")
_CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]*[A-Za-z0-9][A-Za-z0-9_\-]*")

# Neutral introductions are syntax, not permission to discard arbitrary
# words. An unknown context stays open; no negation/exclusion is inferred.
_CODE_CONTEXT_LEAD_RE = re.compile(
    r"^(?:тип(?:ы)?(?:\s+суд(?:на|ов))?|флот|ответ|перечень(?:\s+типов"
    r"(?:\s+судов)?)?)(?=$|[\s:;,—–-])"
    r"(?:\s*,?\s*(?:а именно|то есть|то бишь|как то))?"
    r"[\s:;,—–-]*", re.IGNORECASE)
_CODE_CONTEXT_PUNCT_RE = re.compile(
    r"[\s,:;()\[\]«»\"'" + re.escape(_DASHES) + r"]*")
_CODE_CONTEXT_AND_RE = re.compile(r"\bи\b", re.IGNORECASE)
_CODE_LIST_BOUNDARY_RE = re.compile(r"[,;]|\bи\b", re.IGNORECASE)
_CODE_WORD_RE = re.compile(r"[A-Za-z]+")

_UNIT_WORDS = {
    "duration_minutes": "длительность в минутах — например «600 минут» "
                        "или «10:00»",
    "count": "целое число — например «3»",
    "time_of_day_minutes": "время суток в виде «чч:мм» — например «06:00»",
    "code": "одно обозначение справочника — например «A320»",
    "code_list": "перечень обозначений через запятую — например "
                 "«A320, B738»",
    "credit_hours": "число часов кредита — например «75»",
    "hours_per_credit_hour": "число часов на один час кредита — "
                             "например «1.5»",
}


_UNIT_FORM_HINTS = {
    "integer_nonneg": "целое число не меньше нуля в основной единице",
    "integer_0_1439_tz": "время суток от 00:00 до 23:59",
    "decimal_nonneg": "число не меньше нуля; дробная часть допустима",
    "decimal_positive": "число больше нуля; дробная часть допустима",
    "code": "одно обозначение справочника",
    "code_list": "обозначения справочника через запятую",
    "boolean": "ответ «да» или «нет»",
}


def _unit_hint_example(entry: dict) -> str:
    form = entry["form"]
    if form == "integer_0_1439_tz":
        return "06:00"
    if form == "code":
        return "A320"
    if form == "code_list":
        return "A320, B738"
    if form == "boolean":
        return "да"
    # Pick an actual word of this catalogue with canonical scale. A sample
    # must not promise a local synonym or imply conversion to another unit.
    scales = entry.get("scale") or {}
    word = next((w for w in entry["words"]
                 if w != "чч:мм" and scales.get(w, 1) == 1), None)
    return f"1 {word}" if word else "1"


def unit_words(unit) -> str:
    """Подсказка формата ответа по единице параметра каталога."""
    from . import catalog_bridge
    from template_catalog.validate import _uses_schema_070
    from template_catalog.units import unit_words as catalogue_words
    cat = catalog_bridge.catalog()
    if _uses_schema_070(cat.version()):
        entry = catalogue_words(cat.path).get(str(unit or ""))
        if entry is None:
            raise catalog_bridge.CatalogRequired(f"карта unit_words не задаёт единицу {unit!r}")
        return (f"канон: {entry['canon']}; формат: {_UNIT_FORM_HINTS[entry['form']]}; "
                f"например «{_unit_hint_example(entry)}»; "
                f"слова записи: {', '.join(entry['words'])}")
    return _UNIT_WORDS.get(str(unit or ""),
                           f"значение в единице каталога «{unit}»")


def is_param_question(qid) -> bool:
    return str(qid or "").startswith(PARAM_QUESTION_PREFIXES)


def _root_id(q: dict) -> str:
    return str(q.get("revision_of") or q.get("id") or "")


def question_target(q: dict, *, families=None) -> tuple[str, str] | None:
    """(семья, параметр) вопроса по его структурному ключу — корневого
    у вопроса-пересмотра. Имена семей и параметров каталога дефиса не
    несут, но разбор всё равно сверяется с каталогом: ключ читается
    только как «семья каталога + её параметр»."""
    root = _root_id(q)
    if not is_param_question(root):
        return None
    fams = _bridge.families() if families is None else families
    keys = [root]
    if q.get("edition_of") or q.get("revision_of"):
        # The complete catalog key wins. Only explicit lineage permits an
        # edition suffix; arbitrary E-looking text in a legacy ID is not cut.
        without_edition = re.sub(r"-E(?:[2-9]|[1-9][0-9]+)$", "", root)
        if without_edition != root:
            keys.append(without_edition)
    for key in keys:
        tail = key.split(PARAM_QUESTION_MARK, 1)[1]
        parts = tail.split("-")
        for i in range(1, len(parts)):
            fam, param = "-".join(parts[:i]), "-".join(parts[i:])
            if fam in fams and param in fams[fam]:
                return fam, param
    return None


def _family_params(fam: str) -> list[dict]:
    rec = _bridge.family_records().get(str(fam)) or {}
    out = []
    for p in (rec.get("params") or []):
        if isinstance(p, dict) and p.get("name"):
            out.append(p)
    return out


def _param_spec(fam: str, param: str) -> dict:
    return next((p for p in _family_params(fam)
                 if str(p.get("name")) == str(param)), {})


def _sets_and_problems(fam: str) -> tuple:
    """Shared catalogue choices, adapted to the historical list-of-names API."""
    from template_catalog.completeness import parameter_sets
    record = _bridge.family_records().get(str(fam))
    if record is None:
        return [], []  # the caller names the absent family contract separately
    choices, problems = parameter_sets(record)
    return [members for _sid, members in choices if members], problems


def _param_sets(fam: str) -> list[list[str]]:
    """Минимальные полные наборы параметров семьи по каталогу; без
    объявления — один набор из всех параметров (fail-closed)."""
    return _sets_and_problems(fam)[0]


def missing_params(fam: str, given) -> list[str]:
    """The same pure set law used by the catalogue reference writer."""
    from template_catalog.completeness import family_completeness
    record = _bridge.family_records().get(str(fam))
    return family_completeness(record, given)["missing"]


def _fit_cells(rule: dict) -> dict:
    """Ячейки посадки правила: словарь `template_fit.params` либо ПУСТО,
    если он не объект (С-1: кривая форма ячеек не даёт, но и не роняет
    расчёт трассировкой)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    cells = fit.get("params") if isinstance(fit, dict) else None
    return cells if isinstance(cells, dict) else {}


def _cells_writable(rule) -> bool:
    """ЧИТАЕТ форму ячеек посадки: примет ли она запись. Ровно предикат
    «`_writable_cells` вернёт словарь», но БЕЗ побочного эффекта — сам
    словарь не заводит. Форму спрашивать этим, а писать —
    `_writable_cells`: проход, который ещё вправе отказать, иначе
    оставит в своде пустой `params: {}` (схема: «minProperties»: 1).

    ОТСУТСТВИЕ КЛЮЧА И `params: null` — РАЗНОЕ, и на их совпадении
    предикат лгал: `fit.get("params")` даёт `None` в обоих случаях, он
    звал `null` записываемым, а `_writable_cells` возвращал на нём
    `None` (`setdefault` у СУЩЕСТВУЮЩЕГО ключа отдаёт прежнее
    значение) — и самолечение падало на `cells[param] = …`. Ключа
    нет — запись его заведёт; ключ есть со значением `null` — это
    кривая форма того же рода, что `params: []`: её судит валидатор, а
    молча подменять на `{}` нельзя (находка GPT-166, 13.09.2026)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict):
        return False
    if "params" not in fit:
        return True                  # ключа НЕТ — запись его заведёт
    return isinstance(fit["params"], dict)


def _writable_cells(rule: dict):
    """Словарь ячеек посадки, КУДА можно записать значение ответа: у
    посадки без `params` он ЗАВОДИТСЯ — это ЗАПИСЬ в свод, а не чтение;
    у посадки с кривым `params` — None (переписывать чужую форму молча
    нельзя; её судит валидатор). Зов = НАМЕРЕНИЕ ПИСАТЬ: звать только
    там, где запись состоится, а форму спрашивать `_cells_writable`."""
    if not _cells_writable(rule):
        return None
    fit = rule["template_fit"]
    if "params" not in fit:
        return fit.setdefault("params", {})   # ЗАПИСЬ: ключ заводится
    return fit["params"]                      # словарь — сюда и пишем


def _carrier_fit(rule: dict) -> dict | None:
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if isinstance(fit, dict) and fit.get("category") in ("A", "B") \
            and fit.get("family"):
        return fit
    return None


def _carrier_consistency_problem(rule: dict) -> str | None:
    """Исходные носители до любой записи ИЛИ снятия ячейки."""
    legacy = rule.get("params")
    if legacy is not None:
        if not isinstance(legacy, list):
            return f"{rule.get('id')}: исходные params не список"
        if any(not isinstance(p, dict) or not isinstance(p.get("name"), str)
               for p in legacy):
            return f"{rule.get('id')}: исходная запись params не объект с именем"
    problems = m.reconcile_carriers(rule)
    return "; ".join(problems) if problems else None


def _refuse_inconsistent_drops(rules, q: dict, raw: str, revision: int) -> bool:
    """Предварительная проверка ВСЕХ снимаемых носителей без изменений."""
    problems = [p for r in rules if (p := _carrier_consistency_problem(r))]
    if not problems:
        return False
    q["answer"] = {"raw": raw, "status": "не разобран", "revision": revision,
                   "note": "ячейки не сняты, ответ не применён: " + "; ".join(problems)}
    return True


def write_cell(rule: dict, param: str, cell: dict) -> str | None:
    """Записать подготовленную ячейку либо вернуть причину без изменений.

    Общий рубеж записи текущей формы: исходные носители проверяются
    ДО кандидата, кандидат — ДО создания даже пустого params. Разбор
    ответа и точный источник готовит вызывающий; происхождение и CAS
    всей операции по-прежнему проверяются на уровне свода. Это не
    миграция на landing/2 и не межзапросная транзакция.
    """
    fit = _carrier_fit(rule)
    if fit is None:
        return "нет посадки A/B на семью — значение записать некуда"
    if "family_contract" in fit:
        from cdpm import family_contract
        reference = family_contract.read(fit)
        if reference["issues"] or reference["verdict"] == CONTRACT_INCOMPATIBLE:
            return "; ".join(reference["issues"] + reference["differences"])
    if not _cells_writable(rule):
        return (f"ячейки посадки правила {rule.get('id')} не объект — "
                "значение записать некуда; сначала поправьте посадку")
    spec = _param_spec(str(fit["family"]), param)
    if not spec and fit.get("schema_version") == "landing/2":
        # Typed condition cells share the atomic writer, but their unit
        # and relation belong to the family's condition record.
        spec = m.family_conditions(str(fit["family"])).get(param)
    if not spec:
        return f"{param}: параметр не объявлен у семьи {fit['family']}"
    problem = _carrier_consistency_problem(rule)
    if problem:
        return problem
    if not isinstance(cell, dict) or "value" not in cell \
            or not isinstance(cell.get("quote"), str) or not cell["quote"].strip():
        return f"{param}: ячейка должна содержать значение и цитату"
    problem = _cell_problem(cell, spec)
    if problem:
        return f"{param}: {problem}"
    # Копия исключает и частичную запись при отказе, и последующую
    # неявную правку источника через ссылку на объект вызывающего.
    staged = copy.deepcopy(cell)
    candidate = {**rule, "template_fit": {
        **fit, "params": {**_fit_cells(rule), param: staged}}}
    if "schema_version" in fit:
        from template_catalog import landing
        typed_problems = landing.problems(candidate, _bridge.catalog())
        if typed_problems:
            return "; ".join(p["words"] for p in typed_problems)
    problems = m.reconcile_carriers(candidate)
    if problems:
        return "; ".join(problems)
    _writable_cells(rule)[param] = staged
    return None


def _answers_by_target(doc: dict) -> dict:
    """{(семья, параметр): [вопросы]} — все вопросы параметров свода
    (корневые и пересмотры), в порядке анкеты."""
    out: dict = {}
    for q in doc.get("questions", []) or []:
        if not isinstance(q, dict) or not is_param_question(_root_id(q)):
            continue
        tgt = question_target(q)
        if tgt is not None:
            out.setdefault(tgt, []).append(q)
    return out


def _is_code_closed(q: dict) -> bool:
    ans = q.get("answer")
    return isinstance(ans, dict) and ans.get("raw") == CODE_CLOSED_RAW




def _is_declined(q: dict) -> bool:
    ans = q.get("answer")
    return isinstance(ans, dict) and ans.get("status") == "применён" \
        and not _is_code_closed(q) and is_declined(ans.get("raw"))


def _is_conditional_decline(q: dict) -> bool:
    """Полученный ответ, который ВЫГЛЯДИТ отказом, но несёт условие
    (В-3): параметр им не закрывается, и вопрос остаётся по существу
    без ответа — об этом говорят слова расчёта."""
    ans = q.get("answer")
    if not isinstance(ans, dict) or ans.get("status") not in (
            "применён", "не разобран") \
            or _is_code_closed(q):
        return False
    parts = _decline_parts(ans.get("raw"))
    return parts is not None and bool(parts[1])


def _is_applied_value(q: dict) -> bool:
    """Применённый ответ-ЗНАЧЕНИЕ: не снят кодом и НЕ НАЧАТ фразой
    отказа. Фраза отказа исключает ответ из значений целиком — и
    безусловный отказ (у него значения нет), и отказ с условием или
    пояснением, лежащий в старом своде решением прежней выдачи
    (пятое неавторское ревью K02, замечание B2). Иначе такой ответ
    выглядел бы значением, самолечение брало бы ИМЕННО ЕГО, разбор
    возвращал бы None — и настоящее значение из пересмотра K04 в
    посадку не возвращалось: параметр молча оставался недостающим.
    Судится ВЕСЬ ответ (_decline_parts), а не is_declined: закон B1
    сделал условный отказ НЕ отказом, и без этой оговорки он снова
    стал бы «значением». Место фразы отказа в ответе не меняет ничего
    и здесь (K-1): «600 минут. Не применяется для базы SVO.» —
    не значение ни при каком исходе, как и обратный порядок тех же
    двух предложений."""
    ans = q.get("answer")
    return isinstance(ans, dict) and ans.get("status") == "применён" \
        and not _is_code_closed(q) and _decline_parts(ans.get("raw")) is None


#: Пара «клетка: значение» в строковой записи нормы по уточнителю
#: («GRT:1,PLY:2,SVG:1» — резерв по базам, набор Н0).
_QUALIFIED_PAIR_RE = re.compile(r"^\s*(?P<key>[^:,]+?)\s*:\s*(?P<val>[^,]+?)\s*$")


def _qualified_cells(raw):
    """Клетки значения параметра С УТОЧНИТЕЛЕМ ({клетка: значение})
    либо None — форма не читается. Формы две: объект `{"GRT": 1, …}`
    (структурная форма контракта, §3.1) и строка «GRT:1,PLY:2» (та, что
    несут посадки наборов H0). Уточнённая норма — не одно число, и
    показывать её грамматике одного значения нельзя."""
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, str) and ":" in raw:
        items = []
        for part in raw.split(","):
            mm = _QUALIFIED_PAIR_RE.match(part)
            if mm is None:
                return None
            items.append((mm.group("key"), mm.group("val")))
    else:
        return None
    out: dict = {}
    for key, val in items:
        if not isinstance(key, str) or not key.strip():
            return None
        out[key.strip()] = val
    return out or None


def _canon_problem(raw, word, unit) -> str | None:
    """None — значение читается в канонической единице записи; иначе
    слова беды. ОДНА дверь к значению носителя — `canonical_value`
    (её ведёт A05): конечность числа она судит сама (с 12.09.2026),
    и местного сторожа `_finite` здесь больше нет — он был дублем."""
    value, trouble = canonical_value(raw, word, unit)
    if trouble is None and value is None:
        trouble = f"значение {raw!r} не число"
    if trouble is None:
        return None
    words = str(trouble)
    # Слова беды всегда называют ЕДИНИЦУ (закон В-2): читатель отказа
    # обязан узнать, в чём значение ждали, а не только что оно плохое.
    if "не читается в единице" not in words:
        words += f" — не читается в единице {unit}"
    return words


def _cell_problem(cell, spec: dict) -> str | None:
    """None — ячейка посадки НЕСЁТ значение параметра: значение
    ЧИТАЕТСЯ в канонической единице записи каталога (`canonical_value`).
    Иначе — слова беды («значение … не читается в единице …»).

    В-2 неавтор-ревью 12.09.2026: прежде заданным считалось любое
    непустое значение любого типа (`true`, `"abc"`, NaN, `[1]`,
    `{"x": 1}`) — семья объявлялась полной, пакет выпускался зелёным, а
    исполнителю ехала бессмыслица. Закон: законный ноль и числовая
    строка («0», «600») — значение; пустое, None, `[]`, `{}`, логическое
    у числовой единицы, нечисло и не-конечное число — НЕ значение
    (параметр недостаёт, слова называют причину).

    Параметр с уточнителем (`qualifier` записи — «резерв по базам»)
    читается СТРУКТУРНО: каждая клетка нормы обязана читаться в единице
    записи; грамматике одного значения такая запись не показывается."""
    raw = cell.get("value") if isinstance(cell, dict) else cell
    name = str((spec or {}).get("name") or "параметр")
    unit = str((spec or {}).get("unit") or "")
    if not unit:
        return (f"единица параметра {name} в записи каталога не "
                f"объявлена — значение {raw!r} судить нечем")
    word = str(cell.get("unit") or "") if isinstance(cell, dict) else ""
    if isinstance(cell, dict) and "tz" in cell:
        if unit != "time_of_day_minutes":
            return "tz допустим только у time_of_day_minutes"
        if cell.get("unit") != "time_of_day_minutes":
            return "tz требует явную каноническую unit=time_of_day_minutes"
        if cell["tz"] not in m.PARAM_TZ:
            return f"tz {cell['tz']!r} вне перечня {m.PARAM_TZ}"
    if (spec or {}).get("qualifier"):
        cells = _qualified_cells(raw)
        if cells is not None:
            bad = sorted(k for k, v in cells.items()
                         if _canon_problem(v, word, unit) is not None)
            if not bad:
                return None
            return (f"значение {raw!r} не читается в единице {unit}: "
                    f"клетки {', '.join(bad)} нормы по уточнителю "
                    f"«{spec.get('qualifier')}» не читаются")
    # Своего сторожа структурных значений здесь больше нет (12.09.2026):
    # `canonical_value` не показывает объект и перечень грамматике числа
    # сама (`is_structural`, находка В-1 A05) — и, в отличие от местного
    # сторожа, не бракует ЗАКОННЫЙ перечень обозначений у единицы
    # `code_list`.
    return _canon_problem(raw, word, unit)


def family_coverage(doc: dict) -> dict:
    """Покрытие семей сводом: семья → {carriers (id носителей в порядке
    свода), cells (ИМЕНА ВСЕХ ячеек посадок семьи — что посадка НЕСЁТ,
    включая имена, которых в действующей записи семьи нет), given
    (заданные параметры — посадками, ячейками со ЗНАЧЕНИЕМ, читаемым в
    единице записи каталога), unreadable ({параметр: слова} — ячейка
    есть, а значение не читается), declined (БЕЗУСЛОВНЫЕ решения «не
    применяется» ответами), conditional ({параметр: id вопроса} — ответ
    выглядит отказом, но несёт условие), missing (недостающие по закону
    полноты; безусловные отказы закрывают параметр)}.

    `cells` шире `given | unreadable` ровно на имена вне действующей
    записи семьи (К-1 неавтор-ревью 12.09.2026): `given`/`unreadable`
    судятся записью (нет записи параметра — судить значение нечем), а
    вопрос «что посадка несёт» записью не судится вовсе. Имя, которого
    в действующей записи НЕТ, — это и есть предмет третьей клаузы
    закона несовместимости (§12.12 п. 2(в): «параметр, который несёт
    посадка, из действующей записи убран»), и считать его по `given`
    значило бы искать убранное среди неубранного.

    Кривой `template_fit.params` (не объект — перечень, строка, число)
    ячеек не даёт вовсе (С-1 неавтор-ревью: прежде здесь падало
    `AttributeError` у готовности, матрицы, гейта и лестницы); саму
    кривую посадку судит валидатор словами «params посадки не непустой
    объект»."""
    out: dict = {}
    for r in doc.get("rules", []) or []:
        fit = _carrier_fit(r)
        if fit is None:
            continue
        fam = str(fit["family"])
        cell = out.setdefault(fam, {"carriers": [], "cells": set(), "cells_by_rev": {},
                                    "citations_by_rev": {}, "references": [],
                                    "given": set(),
                                    "unreadable": {}, "declined": set(),
                                    "conditional": {}, "missing": []})
        cell["carriers"].append(str(r.get("id")))
        if "family_contract" in fit:
            from cdpm import family_contract
            reference = family_contract.read(fit)
            reference["carrier"] = str(r.get("id"))
            cell["references"].append(reference)
        params = fit.get("params")
        if not isinstance(params, dict):
            continue
        for key, c in params.items():
            name = str(key)
            cell["cells"].add(name)      # что посадка НЕСЁТ (К-1)
            from cdpm import parameter_contract
            rev = parameter_contract.cell_revision(doc, r, fam, name, c)
            cell["cells_by_rev"].setdefault(rev, set()).add(name)
            cell["citations_by_rev"].setdefault(rev, {}).setdefault(name, []).append(
                c.get("quote") if isinstance(c, dict) else None)
            spec = _param_spec(fam, name)
            if not spec:
                continue      # имя вне записи семьи — судит валидатор
            problem = _cell_problem(c, spec)
            if problem is None:
                cell["given"].add(name)
            else:
                cell["unreadable"].setdefault(name, problem)
    by_target = _answers_by_target(doc)
    for (fam, param), qs in by_target.items():
        if fam not in out:
            continue
        if any(_is_declined(q) for q in qs):
            out[fam]["declined"].add(param)
            continue
        q = next((x for x in qs if _is_conditional_decline(x)), None)
        if q is not None:
            out[fam]["conditional"][param] = str(q.get("id"))
    for fam, cell in out.items():
        for name in list(cell["unreadable"]):
            if name in cell["given"]:
                cell["unreadable"].pop(name)     # прочла соседняя посадка
        cell["missing"] = missing_params(
            fam, cell["given"] | cell["declined"])
        # Every named choice remains an obligation. An easier alternative
        # added later cannot discharge a different, explicitly chosen set.
        closed = cell["given"] | cell["declined"]
        required = {p for ref in cell["references"]
                    if not ref["issues"] and ref["verdict"] == CONTRACT_COMPATIBLE
                    for p in ref["new_members"]}
        cell["missing"].extend(sorted(required - closed - set(cell["missing"])))
    return out


# ---------------------------------------------------------------------------
# K01-барьер: один расчёт полноты семей для готовности, гейта и выпуска
# ---------------------------------------------------------------------------

#: Код отказа выпуска и гейта по полноте семьи (контракт П0 §5) — одно
#: слово кода на всех читателей расчёта (`model.FAMILY_INCOMPLETE`).
FAMILY_INCOMPLETE = m.FAMILY_INCOMPLETE
#: Вердикты расчёта полноты (контракт П0 §3.2).
VERDICT_COMPLETE = "полна"
VERDICT_INCOMPLETE = "неполна"
VERDICT_RELAND = "перепосадка требуется"
#: Состояния контракта семьи против текущей записи каталога (§3.2).
CONTRACT_SAME = "совпадает"
CONTRACT_COMPATIBLE = "совместимо"
CONTRACT_INCOMPATIBLE = "несовместимо"
#: Отпечаток посадки не числится ни за одной ВЫПУЩЕННОЙ версией (или его
#: нет вовсе): прежней записи семьи взять неоткуда — ни совместимость, ни
#: несовместимость не доказаны (В-1 неавтор-ревью 12.09.2026).
CONTRACT_UNPROVEN = "совместимость не доказана"
CONTRACT_NONE = "нет контракта"
#: Запись семьи УЖЕСТОЧЕНА (клауза (а) §12.15; решение ведущего
#: 12.09.2026 по находке К-2 неавтор-ревью): контракт объявленных
#: параметров ТОТ ЖЕ (единицы, отношения и уточнители не менялись,
#: несомое посадкой имя из записи не убрано), но минимальные полные
#: наборы действующей записи требуют БОЛЬШЕ, чем закрывает посадка с
#: безусловными отказами. Перепосадкой это НЕ является: посадка
#: остаётся годной, ей не хватает значения — ровно НЕДОСТАЧА §12.12
#: п. 1, которую держат готовность, гейт и рубеж выпуска, а лечит
#: ответ анкеты. Требовать здесь повтор прогона по текущему каталогу
#: бессмысленно: повтор построит ту же посадку и задаст тот же вопрос.
CONTRACT_HARDENED = "запись ужесточена"

#: Состояния контракта, при которых семья требует ПЕРЕПОСАДКИ: ответом
#: анкеты её свод не лечится — доказанные клаузы (б)/(в) §12.15 либо
#: записи семьи в каталоге нет вовсе. ОДИН закон на всех читателей
#: (К-1 неавтор-ревью 12.09.2026): вердикт `family_completeness`,
#: молчание анкеты (`_reland_families`), слова недостачи
#: (`_missing_words`) и совет рубежа выпуска
#: (`export_task_package.refuse_by_barriers`) обязаны считать
#: неизлечимость ОДИНАКОВО — иначе одно и то же сообщение говорит
#: «ответом это не лечится» и «ответьте на вопрос анкеты».
RELAND_STATES = (CONTRACT_INCOMPATIBLE, CONTRACT_NONE)


def family_curable(verdict: str, catalog_problems) -> bool:
    """Лечится ли недостача семьи ОТВЕТОМ АНКЕТЫ — ОДИН закон на всех
    читателей (продолжение К-1; правка 12.09.2026).

    Нелечимых причин ДВЕ, и обе — ложное обещание лечения, если о них
    промолчать:

    1. ПЕРЕПОСАДКА (`verdict == VERDICT_RELAND`, состояния
       `RELAND_STATES`): смысл посаженных значений изменился либо
       записи семьи нет вовсе — лечит повтор прогона по текущему
       каталогу, а вопроса по такой семье анкета и не ставит.
    2. Запись семьи каталога ЧИТАЕТСЯ НЕ ЦЕЛИКОМ (`catalog_problems`
       непусты — например, минимальный полный набор называет параметр,
       которого у записи нет, и отброшен целиком): кривую запись
       каталога ответ АВИАКОМПАНИИ не чинит ни в каком случае. Такая
       семья НЕ требует перепосадки (посадка годна, и в `RELAND_STATES`
       этому состоянию не место), но и «Ответьте на вопрос анкеты о
       параметре» о ней — обещание, которого система не выполнит: даже
       с ответом свод держится барьером, пока запись не исправлена.
       Лечит исправление ЗАПИСИ КАТАЛОГА (и повтор прогона по
       исправленному каталогу).

    Читатели закона: слова недостачи (`_missing_words`), поле `curable`
    каждого основания барьера (`family_completeness`) и совет рубежа
    выпуска (`export_task_package.refuse_by_barriers`)."""
    return verdict != VERDICT_RELAND and not catalog_problems

#: Память разрешения отпечатка в записи выпущенного каталога:
#: (отпечаток действующего каталога, отпечаток посадки) → записи семей
#: выпуска либо None. Ключ несёт ОБА отпечатка, поэтому смена каталога
#: (в том числе подменного в стенде) даёт другой ключ, а выпущенная
#: версия неизменна по построению — перечитывать её незачем.
_RELEASE_CACHE: dict = {}


def _release_records(landed: str):
    """Записи семей ВЫПУЩЕННОГО каталога с отпечатком `landed` (id →
    запись) либо None — такой выпущенной версии нет.

    Разрешение — машинной правдой действующего каталога: перечень
    выпусков (`versions.json`: версия → отпечаток) даёт ярлык версии, а
    папка `releases/<версия>` — саму запись. Читатель ищет выпуск рядом
    с ДЕЙСТВУЮЩИМ каталогом (его `releases/`), а не в чужой раскладке;
    нет папки — нет доказательства, и расчёт честно говорит
    «совместимость не доказана» (догадок нет)."""
    landed = str(landed or "").strip()
    if not landed:
        return None
    cat = _bridge.catalog()
    key = (str(_bridge.stamp().get("fingerprint") or ""), landed)
    if key in _RELEASE_CACHE:
        return _RELEASE_CACHE[key]
    got = None
    if landed == key[0]:
        got = _bridge.family_records()
    else:
        version = None
        try:
            for row in cat.versions() or []:
                if isinstance(row, dict) \
                        and str(row.get("fingerprint") or "") == landed:
                    version = str(row.get("version") or "") or None
                    break
        except Exception:                                 # noqa: BLE001
            version = None
        if version:
            got = _release_records_of(cat, version, landed)
    _RELEASE_CACHE[key] = got
    return got


def _release_records_of(cat, version: str, landed: str):
    """Записи семей выпуска `version`, если его отпечаток ДЕЙСТВИТЕЛЬНО
    `landed` (проверяется пересчётом читателя — перечень выпусков не
    свидетельство содержимого папки)."""
    import template_catalog as tc
    from pathlib import Path
    sources = []
    base = getattr(cat, "path", None)
    if base is not None:
        sources.append(Path(base) / "releases" / version)
    sources.append(version)
    for src in sources:
        try:
            if isinstance(src, Path) and not src.is_dir():
                continue
            rel = tc.load(src)
            if str(rel.fingerprint()) != landed:
                continue
            return {str(r.get("id")): r for r in rel.families_full()
                    if isinstance(r, dict) and r.get("id")}
        except Exception:                                 # noqa: BLE001
            continue
    return None


def _contract_shape(rec: dict) -> dict:
    """Проекция наборов полноты и исторического раннего барьера.
    Рабочий смысл судит _semantic_transition общим читателем каталога;
    эта проекция нужна для отдельного расчёта ужесточения наборов.
    Ссылку family_contract.param_set она по-прежнему не разрешает.

    Посадка физически несёт условия в том же template_fit.params,
    но условия НЕ входят в минимальные наборы параметров.
    """
    params = {}
    for p in (rec.get("params") or []):
        if isinstance(p, dict) and p.get("name"):
            params[str(p["name"])] = {
                "unit": p.get("unit"), "relation": p.get("relation"),
                "qualifier": p.get("qualifier")}
    conditions = {}
    for c in (rec.get("conditions") or []):
        if isinstance(c, dict) and c.get("name"):
            conditions[str(c["name"])] = {
                "unit": c.get("unit"), "relation": c.get("relation")}
    names = list(params)
    sets = []
    for s in (rec.get("param_sets") or []):
        if not isinstance(s, dict):
            continue
        ps = [str(x) for x in (s.get("params") or [])]
        if ps and all(p in params for p in ps):
            sets.append(tuple(sorted(ps)))
    if not sets and names:
        sets = [tuple(sorted(names))]
    return {"params": params, "conditions": conditions,
            "sets": sorted(set(sets))}


#: Поля записи параметра, смена которых меняет СМЫСЛ уже посаженного
#: значения (С-3 неавтор-ревью): «600» в минутах и «600» в счёте — разные
#: правды, и штамп посадки этого не показывает.
_CONTRACT_FIELD_WORDS = (("unit", "изменилась единица"),
                         ("relation", "изменилось отношение"),
                         ("qualifier", "изменился уточнитель"))

#: Два локальных поля условия раннего барьера, не доказательство
#: исчерпания его смысла. Закрытая схема условия не имеет qualifier.
_CONDITION_FIELD_WORDS = (("unit", "изменилась единица"),
                          ("relation", "изменилось отношение"))
_KIND_PARAM = {"род": "параметр", "который": "который", "убран": "убран",
               "твор": "параметром"}
_KIND_COND = {"род": "условие", "который": "которое", "убран": "убрано",
              "твор": "условием"}


def _declared_kind(name: str, shape: dict):
    """Род имени; уникальность между params/conditions судит каталог."""
    if name in (shape.get("params") or {}):
        return _KIND_PARAM
    if name in (shape.get("conditions") or {}):
        return _KIND_COND
    return None


def _covers(sets, closed: set) -> bool:
    """Запись ПОКРЫТА: хоть один её минимальный полный набор целиком
    лежит в закрытых параметрах. Записи без наборов вовсе (в контракте
    их не бывает — `_contract_shape` выводит набор из всех имён)
    считаем непокрытыми: угадывать полноту нечем."""
    return any(set(s) <= closed for s in (sets or ()))


def _contract_breaks(old: dict, new: dict, carried) -> list[str]:
    """Различия записи семьи раннего барьера §12.16 В.
    Прежние параметрические клаузы (б)/(в) §12.15 сохраняются:
    у физически несомого параметра, объявленного
    ОБЕИМИ записями, сменились единица, отношение или уточнитель (б);
    параметр, который НЕСЁТ посадка, из записи убран (в). Порядок слов
    не зависит от порядка каталога и правил (Н-2) — перечень сортирован.

    Эти доказанные различия ответом анкеты не лечатся: при (б)
    посаженное значение означает уже не то, что означало («600» в
    минутах и «600» в счёте — разные правды, и штамп посадки этого не
    показывает), при (в) несомого имени в записи больше нет и
    спрашивать о нём нечего. Смена НАБОРОВ сюда НЕ входит — она
    ужесточение, то есть недостача (`_contract_hardening`).

    `carried` — что посадка НЕСЁТ: ИМЕНА ЯЧЕЕК, независимо от того,
    объявляет ли их действующая запись. Этим судится клауза «убран»:
    имя, которого в действующей записи нет, — ровно её предмет. Прежде
    и она считалась по `given | unreadable`, то есть по именам, ВЗЯТЫМ
    ИЗ ДЕЙСТВУЮЩЕЙ записи, — разность с той же записью всегда пуста, и
    клауза была МЁРТВОЙ (К-1).

    §12.16 В расширяет этот временный барьер: у ФИЗИЧЕСКИ НЕСОМОГО
    условия судятся unit/relation, удаление и смена рода имени
    параметр ↔ условие. Позднейшая названная дельта CDPM160/GPT156
    ограничивает и (б) физическими ключами ячеек, НЕ успешно
    прочитанными значениями: нечитаемая ячейка всё ещё несётся.
    Ненесомый параметр может оставаться обычной излечимой недостачей.
    Это не полный канонический судья транзитивного смысла из §12.16 Д.
    """
    out: list[str] = []
    op, np_ = old.get("params") or {}, new.get("params") or {}
    oc, nc = old.get("conditions") or {}, new.get("conditions") or {}
    carried_names = {str(c) for c in (carried or ())}
    for name in sorted(carried_names & set(op) & set(np_)):
        for key, word in _CONTRACT_FIELD_WORDS:
            if op[name].get(key) != np_[name].get(key):
                out.append(f"у параметра {name} {word}: было "
                           f"{op[name].get(key)!r}, стало "
                           f"{np_[name].get(key)!r}")
    for name in sorted(carried_names & set(oc) & set(nc)):
        for key, word in _CONDITION_FIELD_WORDS:
            if oc[name].get(key) != nc[name].get(key):
                out.append(f"у условия {name}, которое несёт посадка, "
                           f"{word}: было {oc[name].get(key)!r}, стало "
                           f"{nc[name].get(key)!r}")
    for name in sorted(carried_names):
        was, now = _declared_kind(name, old), _declared_kind(name, new)
        if was is None:
            continue
        if now is None:
            out.append(f"{was['род']} {name}, {was['который']} несёт "
                       f"посадка, из записи семьи {was['убран']}")
        elif now is not was:
            out.append(f"имя {name}, которое несёт посадка, было "
                       f"{was['твор']} записи семьи, а стало "
                       f"{now['твор']}")
    return sorted(out)


def _contract_hardening(old: dict, new: dict, closed) -> list[str]:
    """УЖЕСТОЧЕНИЕ записи — клауза (а) §12.15: наборы переписаны так,
    что посадка, полная по ПРЕЖНЕЙ записи, перестала покрывать хоть
    один набор действующей, то есть запись стала требовать БОЛЬШЕ, чем
    несёт посадка. Слова объясняют ПРИЧИНУ недостачи и перепосадку НЕ
    доказывают (решение ведущего 12.09.2026, находка К-2 неавтор-ревью).

    Почему не перепосадка: смысл уже посаженных значений не изменился —
    единицы, отношения и уточнители те же, ничего не убрано, — посадке
    просто не хватает соседнего значения. §12.12 п. 1 такую нехватку
    прямо считает ОБЫЧНОЙ недостачей: нарушением контракта она не
    объявляется, её держат готовность, гейт производителя и рубеж
    выпуска кодом `family-incomplete`, а лечит вопрос анкеты. Прежний
    закон объявлял (а) доказанной перепосадкой и советовал повторить
    прогон по текущему каталогу — совет бессмысленный (повтор построит
    ту же посадку и задаст тот же вопрос) и вдобавок ЛОЖНЫЙ: ответ
    анкеты такую «перепосадку» на деле лечил, и посадка, объявленная
    негодной, выпускалась как годная (К-2).

    `closed` — чем параметр ЗАКРЫТ для полноты: заданные ячейки плюс
    БЕЗУСЛОВНЫЕ отказы (ровно то, чем считается `missing`,
    `missing_params(fam, given | declined)`), — один и тот же счёт у
    клаузы, у вердикта и у анкеты (К-1 п. 2). Прежде здесь стояло
    `given | unreadable`: безусловный отказ полноту давал, а клаузе не
    считался (посадка «A задан + B отклонён» при добавленном C уходила
    в анкету, а та же «A и B заданы» — в перепосадку), нечитаемая же
    ячейка полноты не давала, а клаузе считалась.

    Чистое ОСЛАБЛЕНИЕ сюда не попадает: выпуск 0.5.0 РАЗБИЛ общий набор
    `duty_limits` 0.2.0 на три набора по одному (новая запись требует
    МЕНЬШЕ), посадка с одним параметром покрывает набор действующей
    записи — `_covers` истинно, и слов нет. Не попадает и недостача,
    неполная уже по прежней записи: ужесточением объяснять нечего."""
    closed_names = {str(c) for c in (closed or ())}
    if old.get("sets") == new.get("sets") \
            or not _covers(old.get("sets"), closed_names) \
            or _covers(new.get("sets"), closed_names):
        return []
    return [f"минимальный полный набор параметров изменился: было "
            f"{_sets_words(old.get('sets'))}, стало "
            f"{_sets_words(new.get('sets'))} — закрытое посадкой и "
            f"безусловными отказами "
            f"({', '.join(sorted(closed_names)) or '—'}) не покрывает "
            f"ни одного набора действующей записи"]


def _sets_words(sets) -> str:
    return "; ".join("{" + ", ".join(s) + "}" for s in (sets or ())) or "—"


def _live_param_question(qs) -> dict | None:
    """Живой вопрос параметра: открыт (ответа нет либо он не разобран)
    или ждёт обработчика; вопрос, снятый кодом, живым не считается."""
    superseded = {q.get("edition_of") for q in qs}
    for q in qs:
        if q.get("id") in superseded:
            continue
        if _is_code_closed(q):
            continue
        ans = q.get("answer")
        if m.is_open_question(q) or (
                isinstance(ans, dict)
                and ans.get("status") == "ожидает обработчика"):
            return q
    return None


def _has_answered_edition_ancestor(q: dict, qs: list[dict]) -> bool:
    """An unanswered intermediate edition does not widen an older answer."""
    by_id = {x.get("id"): x for x in qs}
    parent = by_id.get(q.get("edition_of"))
    seen = set()
    while parent is not None and parent.get("id") not in seen:
        seen.add(parent.get("id"))
        if (parent.get("answer") or {}).get("status") in ("применён", "применён частично") \
                and not _is_code_closed(parent):
            return True
        parent = by_id.get(parent.get("edition_of"))
    return False


def family_completeness(doc: dict) -> dict:
    """ОДИН расчёт полноты семей (K01-барьер, контракт П0 §3.2 и §9,
    12.09.2026), которым пользуются готовность (`model.readiness`,
    `model.readiness_matrix`), гейт производителя (`gate_task_package`)
    и рубеж выпуска (`export_task_package`, отказ `family-incomplete`):
    вердикт о семье у всех один и тот же, а анкета — СЛЕДСТВИЕ
    расчёта (`ensure_family_param_questions` ставит вопросы по
    `missing`), а не его условие: недостача обязательного параметра
    держит готовность и без вопроса.

    Семья → {contract: {family, catalog_version, catalog_fingerprint,
    landed_revs, differences, unproven_reasons, hardening}, contract_state, carriers, given,
    unreadable, declined, conditional, pending, missing, asked,
    catalog_problems, verdict, curable, problems}:

    - `given` — параметры, чью ячейку посадки можно ПРОЧЕСТЬ в единице
      записи каталога (`_cell_problem`, В-2), у любого носителя семьи
      (категории A/B); `unreadable` — {параметр: слова} по ячейкам,
      значение которых не читается; `declined` — применённые
      БЕЗУСЛОВНЫЕ отказы «не применяется» (В-3); `conditional` —
      {параметр: вопрос} по ответам-отказам с условием (параметр ими не
      закрывается); `pending` — ячейки с открытым пересмотром
      (`pending_revision`, K04); `missing` — обязательные параметры
      ближайшего набора без `given`/`declined`;
    - `asked` — {параметр: id живого вопроса | None} по недостающим;
    - `contract_state` судится общим чтением записи и её зависимостей
      (§12.16 Д), а не штампом: `совпадает` — все носители
      посажены действующим каталогом; `совместимо` — посадка прежним
      каталогом, чей отпечаток разрешён в ВЫПУЩЕННУЮ версию, и смысл
      физически несомых ячеек совместим с действующим, включая
      единицы, отношения, уточнители и словарные зависимости;
      `несовместимо` — общий читатель доказал смену смысла, удаление
      несомого имени или смену его рода. Именованная дельта CDPM160
      сохраняется: несомость определяется до фильтра действующей
      записью. Различия
      названы в `contract.differences` — это ДОКАЗАННАЯ перепосадка, и
      только она едет в валидатор свода; `запись ужесточена` — наборы
      действующей записи требуют БОЛЬШЕ, чем закрывает посадка, клауза
      (а) §12.15 (слова — в `contract.hardening`): перепосадкой это НЕ
      считается (К-2, решение ведущего 12.09.2026) — посаженные
      значения означают ровно то же, недостаёт соседнего, и это
      ОБЫЧНАЯ недостача §12.12 п. 1, которую лечит ответ анкеты;
      `совместимость не доказана` — прежнее чтение не разрешается
      целиком либо переход не доказан (например, в старом выпуске
      отсутствует словарь). Причины в `contract.unproven_reasons`,
      не в доказанных `differences`. Расчёт не выдумывает перепосадку
      или недостачу; существующая недостача остаётся ОБЫЧНОЙ;
    - `verdict` — `полна` | `неполна` | `перепосадка требуется`
      (последний — только при `нет контракта` и ДОКАЗАННОЙ
      несовместимости клауз (б)/(в); ужесточение наборов даёт
      `неполна`);
    - `curable` — лечится ли недостача семьи ОТВЕТОМ АНКЕТЫ
      (`family_curable` — один закон на всех читателей): ложь при
      перепосадке И при кривой записи каталога (`catalog_problems`
      непусты — кривую запись ответ авиакомпании не чинит);
    - `problems` — перечень словами: «параметр X семьи Y не задан и не
      спрошен», «… спрошен (Q), ответа нет», «значение … не читается в
      единице …», «перепосадка требуется: запись семьи … изменилась …»,
      «запись семьи … читается не целиком: …»;
      у каждой записи — `rules` (носители), `zones`, `hard` (среди
      носителей есть жёсткие строки: затронуты все режимы; только
      мягкие — режимы со штрафами), `code`, `proven` (доказанная
      несовместимость — её и только её объявляет нарушением валидатор),
      а также `verdict`, `curable` и `catalog_broken` СЕМЬИ — по ним
      совет рубежа выпуска называет путь лечения, ничего не выводя сам.

    Без каталога расчёт отказывает словами читателя (`CatalogRequired`)
    — как всё, что судит семьи; рубежи переводят отказ сами."""
    coverage = family_coverage(doc)
    by_target = _answers_by_target(doc)
    stamp = _bridge.stamp()
    current = str(stamp.get("fingerprint") or "")
    records = _bridge.family_records()
    rules_by_id = {str(r.get("id")): r for r in doc.get("rules", []) or []
                   if isinstance(r, dict)}
    out: dict = {}
    for fam in sorted(coverage):
        cell = coverage[fam]
        carriers = [rules_by_id[rid] for rid in cell["carriers"]
                    if rid in rules_by_id]
        revs = _landed_revs(cell, rules_by_id)
        pending = sorted(
            str(name) for r in carriers
            for name, c in _fit_cells(r).items()
            if isinstance(c, dict) and c.get("pending_revision"))
        from template_catalog.completeness import family_completeness as shared_completeness
        shared_state = shared_completeness(
            records.get(fam), cell["given"], cell["declined"], pending)
        asked: dict = {}
        for param in cell["missing"]:
            live = _live_param_question(by_target.get((fam, param)) or [])
            asked[param] = str(live.get("id")) if live is not None else None
        # K01b-06: записи семьи в каталоге нет — требований не из чего
        # вывести, и «полна» из пустого списка не выдаётся (fail-closed).
        # Саму посадку на неизвестную семью судит валидатор посадки
        # («семья вне базы шаблонов»), поэтому проблема расчёта в
        # валидатор не дублируется.
        unproven_reasons = []
        no_contract, contract_state, differences, hardening = \
            _family_contract(fam, cell, records, revs, current,
                             unproven_reasons=unproven_reasons)
        if no_contract:
            verdict = VERDICT_RELAND
        elif contract_state == CONTRACT_INCOMPATIBLE:
            # ДОКАЗАННАЯ смена контракта семьи — и только клаузы (б)/(в)
            # §12.15: посаженные значения означают уже не то, что
            # означали (сменилась единица, отношение или уточнитель),
            # либо несомого посадкой имени в записи больше нет. Ответом
            # анкеты это не лечится, нужен новый прогон по действующему
            # каталогу. УЖЕСТОЧЕНИЕ наборов (клауза (а)) сюда не входит:
            # оно обычная недостача (§12.12 п. 1) — ниже по ветке
            # `missing`, со словами о причине (К-2).
            verdict = VERDICT_RELAND
        # Keep the established distinction: all values may be present while
        # a malformed catalogue record still blocks use via catalog_problems.
        # An open revision, unlike that diagnostic distinction, never closes
        # the answer obligation even when the old value is still present.
        elif (cell["missing"] or shared_state["pending"]
              or any(ref["issues"] for ref in cell["references"])):
            verdict = VERDICT_INCOMPLETE
        else:
            verdict = VERDICT_COMPLETE
        ids = sorted(str(r.get("id")) for r in carriers)   # Н-2: порядок
        zones = sorted({str(r.get("zone") or "BOTH") for r in carriers})
        hard = any(r.get("kind") != "SOFT" for r in carriers)
        catalog_problems = shared_state["problems"] if not no_contract \
            else []
        # Лечимость — ОДИН закон (`family_curable`) и ОДНО поле у КАЖДОГО
        # основания семьи: читателю (совету рубежа выпуска) нечего
        # выводить самому. `catalog_broken` — ПРИЧИНА нелечимости,
        # которой нет в вердикте: вердикт знает только про перепосадку.
        reference_issues = [f"{ref['carrier']}: {words}"
                            for ref in cell["references"] for words in ref["issues"]]
        curable = family_curable(verdict, catalog_problems) and not reference_issues
        head = {"code": FAMILY_INCOMPLETE, "family": fam,
                "rules": list(ids), "zones": zones, "hard": hard,
                "verdict": verdict, "curable": curable,
                "catalog_broken": bool(catalog_problems)}
        problems: list[dict] = []
        for words in reference_issues:
            problems.append(dict(head, param="—", question=None, reland=False,
                                 proven=False, reference_broken=True,
                                 words=f"ссылка набора семьи {fam} не доказана: {words}"))
        if no_contract:
            problems.append(
                dict(head, param="—", question=None, reland=False,
                     proven=False, no_contract=True,
                     words=(f"перепосадка требуется: семьи {fam} нет в "
                            f"каталоге {stamp.get('version')} "
                            f"({current[:12]}) — контракта семьи нет, "
                            f"полнота правил {', '.join(ids)} не "
                            f"считается")))
        if contract_state == CONTRACT_INCOMPATIBLE:
            problems.append(
                dict(head, param="—", question=None, reland=True,
                     proven=True, differences=list(differences),
                     words=(f"перепосадка требуется: правила "
                            f"{', '.join(ids)} посажены каталогом "
                            f"{_rev_words(revs)}, а запись семьи {fam} в "
                            f"каталоге {stamp.get('version')} "
                            f"({current[:12]}) изменилась по содержанию — "
                            f"{'; '.join(differences)}; ответом анкеты "
                            f"это не лечится: повторите прогон по "
                            f"текущему каталогу")))
        for words in catalog_problems:
            problems.append(
                dict(head, param="—", question=None, reland=False,
                     proven=False, catalog_record=True,
                     words=(f"запись семьи {fam} каталога "
                            f"{stamp.get('version')} читается не целиком: "
                            f"{words}")))
        for param in pending:
            problems.append(
                dict(head, param=param, question=None, reland=False,
                     proven=False, pending_revision=True,
                     words=(f"параметр {param} семьи {fam} ожидает ответа "
                            "на пересмотр; старое значение не закрывает пересмотр")))
        for param in cell["missing"]:
            problems.append(
                dict(head, param=param, question=asked[param],
                     reland=False, proven=False,
                     words=_missing_words(fam, param, cell, asked[param],
                                          contract_state, revs,
                                          hardening, catalog_problems,
                                          contract_reasons=unproven_reasons)))
        out[fam] = {
            "contract": {"family": fam,
                         "catalog_version": stamp.get("version"),
                         "catalog_fingerprint": current,
                         "landed_revs": revs,
                         "differences": list(differences),
                         "unproven_reasons": list(unproven_reasons),
                         "hardening": list(hardening),
                         "transitions": [f"{ref['carrier']}: {words}"
                                         for ref in cell["references"]
                                         for words in ref.get("transitions", [])],
                         "references": copy.deepcopy(cell["references"])},
            "contract_state": contract_state,
            "carriers": list(cell["carriers"]),
            "given": sorted(cell["given"]),
            "unreadable": dict(cell["unreadable"]),
            "declined": sorted(cell["declined"]),
            "conditional": dict(cell["conditional"]),
            "pending": pending,
            "missing": list(cell["missing"]),
            "asked": asked,
            "catalog_problems": list(catalog_problems),
            "verdict": verdict,
            "curable": curable,
            "problems": problems,
        }
    return out


def _rev_words(revs) -> str:
    """Отпечатки посадок словами (короткой формой, порядок независим)."""
    got = [x[:12] for x in (revs or ()) if x]
    return ", ".join(got) if got else "без ревизии"


def _semantic_transition(fam: str, revision: str, carried, *, citations=None) -> dict:
    """Один общий судья перехода к текущему каталогу (§12.16 Д).

    И полнота посадки, и применение ответа пользуются тем же чтением.
    Старое тело разрешается по точному отпечатку рядом с текущим;
    отсутствие словаря не заменяется словарём сегодняшней версии.
    """
    from template_catalog import semantics as sem
    cat = _bridge.catalog()
    old = sem.release_record_reading(cat, "families", fam,
                                     fingerprint=revision)
    new = sem.record_reading(cat, "families", fam)
    return sem.readings_compare(old, new, carried=carried, citations=citations)


def _contract_state(fam: str, record: dict, revs, current: str,
                    carried, closed, *, cells_by_rev=None, unproven_reasons=None,
                    citations_by_rev=None):
    """(состояние, доказанные различия, отдельное ужесточение).
    Причины недоказанности передаются отдельному optional collector.
    Смысл судит каноническое чтение записи И её словарных зависимостей,
    не локальная проекция и не неравенство отпечатков.

    Старшинство исходов: доказанная перепосадка общего чтения
    (§12.16 Д) сильнее всего — если хоть одна посадка сделана
    выпуском, чья запись разошлась по смыслу, это перепосадка
    независимо от остальных; недоказанность сильнее ужесточения
    (сравнивать было не с чем); ужесточение (клауза (а),
    `_contract_hardening`) перепосадкой НЕ является — это причина
    недостачи, и слова о ней едут с недостачей (К-2, решение ведущего
    12.09.2026).

    `carried` — имена ячеек посадок семьи, `closed` — чем параметры
    закрыты для полноты (К-1)."""
    if not revs or all(rev == current for rev in revs):
        return CONTRACT_SAME, [], []
    new = _contract_shape(record)
    breaks: set = set()
    hardening: set = set()
    unproven: set = set()
    for rev in revs:
        if rev == current:
            continue
        names = carried if cells_by_rev is None else cells_by_rev.get(rev, set())
        result = _semantic_transition(
            fam, rev, names,
            citations=citations_by_rev.get(rev, {}) if citations_by_rev is not None else None)
        for difference in result["differences"]:
            if difference["verdict"] == CONTRACT_INCOMPATIBLE:
                breaks.add(difference["words"])
            elif difference["verdict"] == CONTRACT_UNPROVEN:
                unproven.add(difference["words"])
        old = (_release_records(rev) or {}).get(str(fam))
        if old is not None:
            hardening |= set(_contract_hardening(_contract_shape(old), new, closed))
    if breaks:
        # Ужесточение при ДОКАЗАННОЙ перепосадке не называется: слова
        # недостачи «перепосадки не требуется» стояли бы рядом со
        # словами «перепосадка требуется» — два вердикта об одной семье.
        # Перепосадка старше, и объяснять недостачу нечем: лечить её
        # ответом всё равно нельзя.
        return CONTRACT_INCOMPATIBLE, sorted(breaks), []
    if unproven:
        if unproven_reasons is not None:
            unproven_reasons.extend(sorted(unproven))
        return CONTRACT_UNPROVEN, [], sorted(hardening)
    if hardening:
        return CONTRACT_HARDENED, [], sorted(hardening)
    return CONTRACT_COMPATIBLE, [], []


def _landed_revs(cell: dict, rules_by_id: dict) -> list[str]:
    """Отпечатки каталога, которыми посажены носители семьи (Н-2:
    порядок независим от порядка свода и каталога)."""
    return sorted(set(cell.get("cells_by_rev", {})) | {str((rules_by_id[rid].get("template_fit") or {})
                       .get("registry_rev") or "").strip()
                   for rid in cell["carriers"] if rid in rules_by_id})


def _family_contract(fam: str, cell: dict, records: dict, revs,
                     current: str, *, unproven_reasons=None):
    """(записи семьи нет?, состояние контракта, ДОКАЗАННЫЕ различия,
    слова ужесточения) — ОДНА точка закона §12.15 на обоих читателей:
    расчёт полноты (`family_completeness`) и анкету
    (`ensure_family_param_questions`). Иначе «спрашивать ли» и
    «перепосадка ли» разъехались бы: анкета обещала бы лечение там, где
    его нет (К-2, п. 6 решения ведущего 12.09.2026).

    К-1: две стороны посадки. `carried` — ИМЕНА ЯЧЕЕК (что посадка
    несёт, в том числе имя, которого в действующей записи нет, —
    предмет клаузы «убран»); `closed` — чем параметры закрыты для
    ПОЛНОТЫ, тот же счёт, что и у `missing`."""
    record = records.get(fam)
    if record is None:
        return True, CONTRACT_NONE, [], []
    state, diffs, hardening = _contract_state(
        fam, record, revs, current, set(cell["cells"]),
        set(cell["given"]) | set(cell["declined"]),
        cells_by_rev=cell.get("cells_by_rev"), unproven_reasons=unproven_reasons,
        citations_by_rev=cell.get("citations_by_rev"))
    for ref in cell.get("references", []):
        if ref["verdict"] == CONTRACT_INCOMPATIBLE:
            state = CONTRACT_INCOMPATIBLE
            diffs.extend(f"{ref['carrier']}: {words}" for words in ref["differences"])
        elif ref["issues"]:
            if state not in RELAND_STATES:
                state = CONTRACT_UNPROVEN
            if unproven_reasons is not None:
                unproven_reasons.extend(ref["issues"])
        elif ref["change"] == "expanded":
            hardening.append(f"набор {ref['origin']['param_set']!r} расширен: "
                             f"было {ref['old_members']}, стало {ref['new_members']}")
    if state in RELAND_STATES:
        hardening = []
    return False, state, diffs, hardening


def _reland_families(doc: dict, coverage: dict) -> set:
    """Семьи, чью перепосадку ДОКАЗАЛ контракт (клаузы (б)/(в) §12.15)
    либо у которых записи семьи в каталоге нет вовсе: ответом анкеты
    такой свод не лечится, и вопрос по такой семье был бы ЛОЖНЫМ
    ОБЕЩАНИЕМ — авиакомпания отвечает, а свод остаётся негодным
    (К-2, п. 6). Ужесточение (клауза (а)) сюда НЕ входит: оно
    недостача, и её как раз лечит ответ."""
    out: set = set()
    if not coverage:
        return out      # посадок нет — каталог трогать незачем
    current = str(_bridge.stamp().get("fingerprint") or "")
    records = _bridge.family_records()
    rules_by_id = {str(r.get("id")): r for r in doc.get("rules", []) or []
                   if isinstance(r, dict)}
    for fam, cell in coverage.items():
        _no_contract, state, _diffs, _hard = _family_contract(
            fam, cell, records, _landed_revs(cell, rules_by_id), current)
        # Один закон неизлечимости на всех читателей (К-1): «записи нет»
        # приходит сюда состоянием CONTRACT_NONE, доказанная
        # перепосадка — CONTRACT_INCOMPATIBLE.
        if state in RELAND_STATES or any(ref["issues"] for ref in cell.get("references", [])):
            out.add(fam)
    return out


def _missing_words(fam: str, param: str, cell: dict, qid,
                   contract_state: str, revs, hardening=(),
                   catalog_problems=(), *, contract_reasons=()) -> str:
    """Слова о недостающем параметре: почему его нет и что с ним делать.
    Нечитаемая ячейка (В-2) и условный отказ (В-3) называются прямо —
    «не задан и не спрошен» о них было бы неправдой.

    УЖЕСТОЧЕНИЕ записи (клауза (а) §12.15) тоже называется прямо (К-2):
    «запись семьи стала требовать больше, чем несёт посадка» — это не
    то же, что «параметра никогда не было», и владельцу важно видеть
    разницу; путь при этом один — ответ анкеты.

    НЕИЗЛЕЧИМАЯ недостача называется неизлечимой (К-1 неавтор-ревью
    12.09.2026): у семьи, требующей перепосадки (`RELAND_STATES`), те же
    слова «не задан и не спрошен» звучали как обычная, излечимая
    недостача — и рядом с ними ехал совет «ответьте на вопрос анкеты»,
    хотя вопроса по такой семье анкета не ставит, а ответ на заданный
    ДО смены каталога живой путь панели отвергает целиком. Хвост
    называет и то, и другое.

    Вторая причина неизлечимости того же класса (правка 12.09.2026) —
    запись семьи каталога ЧИТАЕТСЯ НЕ ЦЕЛИКОМ (`catalog_problems`):
    требования читаются по уцелевшему остатку (fail-closed), и
    недостача из этого остатка настоящая, но ОТВЕТ авиакомпании кривую
    запись каталога не чинит — свод держится барьером и с ответом.
    Перепосадкой это не является (посадка годна, `RELAND_STATES` тут ни
    при чём), поэтому хвост свой: причина названа, и назван путь —
    исправить запись каталога."""
    bad = (cell.get("unreadable") or {}).get(param)
    if bad is not None:
        words = (f"параметр {param} семьи {fam}: ячейка посадки есть, но "
                 f"{bad} — значение не задано")
    elif param in (cell.get("conditional") or {}):
        words = (f"параметр {param} семьи {fam}: ответ на вопрос "
                 f"{cell['conditional'][param]} — отказ С УСЛОВИЕМ, "
                 f"безусловным решением «не применяется» он не считается; "
                 f"укажите значение либо ответьте отказом без условий")
    elif qid is not None:
        words = f"параметр {param} семьи {fam} спрошен ({qid}), ответа нет"
    else:
        words = f"параметр {param} семьи {fam} не задан и не спрошен"
    if hardening:
        meaning = ("смысл уже посаженных значений при этом не изменился"
                   if contract_state != CONTRACT_UNPROVEN else
                   "совместимость смысла уже посаженных значений отдельно не доказана")
        words += (f" (запись семьи {fam} в действующем каталоге стала "
                  f"требовать больше, чем несёт посадка каталога "
                  f"{_rev_words(revs)}: {'; '.join(hardening)}; {meaning} — "
                  f"перепосадки не требуется, недостачу лечит ответ "
                  f"анкеты)")
    if contract_state == CONTRACT_UNPROVEN:
        reasons = "; ".join(contract_reasons) or (
            f"прежнее чтение семьи для отпечатка {_rev_words(revs)} не разрешено")
        words += (f" (совместимость посадки не доказана для каталога "
                  f"{_rev_words(revs)}: {reasons})")
    if contract_state in RELAND_STATES:
        # Семья требует перепосадки: ответом анкеты недостача НЕ
        # лечится, и владельцу это сказано теми же словами, что и
        # основанию перепосадки. Ужесточение сюда не примешивается —
        # `_contract_state` при доказанной перепосадке слов ужесточения
        # не выдаёт (два вердикта об одной семье), а без записи семьи
        # их неоткуда взять.
        asked = ("" if qid is None else
                 f", а ответ на заданный ранее {qid} применён не будет")
        words += (f" (перепосадка требуется: вопрос анкеты по семье "
                  f"{fam} не ставится{asked} — ответом эта недостача не "
                  f"лечится; повторите прогон по текущему каталогу)")
    if catalog_problems:
        # Запись каталога кривая: недостача честная, но ответ её не
        # ЗАКРОЕТ для выпуска — барьер держит свод, пока запись не
        # исправлена. Молчать об этом значило бы обещать лечение,
        # которого не будет (тот же класс, что К-1 и К-2).
        words += (f" (запись семьи {fam} в действующем каталоге читается "
                  f"не целиком — требования прочтены по уцелевшему "
                  f"остатку записи; ответом анкеты эта недостача не "
                  f"лечится: исправьте запись семьи в каталоге и "
                  f"повторите прогон по исправленному каталогу)")
    return words


def family_barrier_problems(doc: dict) -> list[dict]:
    """Проблемы полноты семей одним перечнем (K01-барьер) — записи
    `problems` расчёта `family_completeness` по всем использованным
    семьям, порядок семей и параметров каталога."""
    out: list[dict] = []
    for fam, cell in family_completeness(doc).items():
        out.extend(cell["problems"])
    return out


def _first_carrier(doc: dict, fam: str, prefer=None) -> dict | None:
    """Первое правило-носитель семьи (порядок предпочтения — rule_refs
    вопроса, затем порядок свода)."""
    by_id = {r.get("id"): r for r in doc.get("rules", []) or []
             if isinstance(r, dict)}
    for rid in (prefer or []):
        r = by_id.get(rid)
        fit = _carrier_fit(r) if r else None
        if fit is not None and str(fit.get("family")) == str(fam):
            return r
    for r in doc.get("rules", []) or []:
        fit = _carrier_fit(r)
        if fit is not None and str(fit.get("family")) == str(fam):
            return r
    return None


def _set_blockers(doc: dict, qid: str, rids, on: bool) -> None:
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict) or r.get("id") not in rids:
            continue
        bl = [b for b in (r.get("blockers") or []) if b != qid]
        if on:
            bl.append(qid)
        if bl:
            r["blockers"] = bl
        else:
            r.pop("blockers", None)


def _question_text(fam: str, param: str, carriers: list[dict]) -> str:
    spec = _param_spec(fam, param)
    title = _bridge.family_titles().get(str(fam)) or str(fam)
    note = " ".join(str(spec.get("note") or "").split())
    unit = spec.get("unit")
    lead = carriers[0]
    heads = ", ".join(str(r.get("id")) for r in carriers)
    return (f"Правил{'а' if len(carriers) > 1 else 'о'} {heads} "
            f"({lead.get('statement') or ''}) посажен"
            f"{'ы' if len(carriers) > 1 else 'о'} на шаблон «{title}» "
            f"({fam}). У шаблона есть параметр «{param}»"
            + (f" — {note}" if note else "")
            + f", который документами авиакомпании не задан ни одним "
              f"правилом свода. Значение не подразумевается и из других "
              f"параметров не выводится. Укажите значение "
              f"({unit_words(unit)}) либо ответьте «не применяется», "
              f"если такого требования у авиакомпании нет.")


def _new_question(doc: dict, fam: str, param: str, carriers: list[dict],
                  existing: set) -> dict | None:
    zone = _bridge.family_traits(fam).get("zone")
    part = "3a" if zone == "PAIRING" else "3b"
    qid = m.stable_question_id(part, f"параметр-{fam}-{param}")
    if qid in existing:
        return None
    from cdpm import question_editions
    sources = question_editions.normative_rule_sources(carriers)
    if not sources:
        return None            # строку без трассировки судит валидатор
    spec = _param_spec(fam, param)
    from cdpm import parameter_contract
    q = {
        "id": qid, "part": part,
        "parameter_contract": parameter_contract.snapshot(fam, param),
        "title": f"Недостающий параметр шаблона: {fam} — {param}",
        "text": _question_text(fam, param, carriers),
        "answer_format": {"type": "value",
                          "note": unit_words(spec.get("unit"))
                                  + "; либо «не применяется»"},
        "rule_refs": [str(r.get("id")) for r in carriers],
        "source_refs": sources,
    }
    # The book must already contain the bound identity. Freezing for the
    # first time while applying its answer would change the issued identity.
    question_editions.freeze(q)
    return q


def _answer_cell(q: dict, param: str, value, quote: str, raw: str) -> dict:
    """Ячейка посадки, внесённая ответом, с ТОЧНЫМ происхождением
    (контракт П0 §2.3, A03-мин/K05-мин, 12.09.2026).

    `source` — точное издание отвеченного вопроса (у пересмотра
    `Q-…-R` — его собственное, не корня: K05) и отпечаток той версии
    ответа, из которой прочитано значение; корень родословной лежит
    отдельно (`source.root`). Прежняя строка `answer` (id корня)
    остаётся рядом: читатели, знающие только её, не ломаются —
    миграция старых ячеек идёт волной 17б."""
    from cdpm import provenance as _prov
    if "parameter_contract" in q:
        from cdpm import question_editions
        question_editions.require_valid(q)
    return {"value": value, "quote": quote, "answer": _root_id(q),
            "source": _prov.answer_source(q, param, raw)}


def _reapply_values(doc: dict, coverage: dict, notes=None) -> list[str]:
    """Самолечение: применённый ответ-значение есть, а записи параметра
    в посадках семьи нет (посадку переиздала заплата) — значение
    возвращается в посадку первого носителя из того же ответа, не
    переспрашивается. Возвращает id носителей.

    Ответ берётся ДЕЙСТВУЮЩИЙ — тот, что стоит в конце ЦЕПОЧКИ
    пересмотров (`provenance.active_answer`: от корня по ссылкам
    `revised_by`), а не «последний применённый по порядку списка»
    (контракт П0 §2.3 п. 1, §2.4 п. «е»). Порядок записей в анкете —
    не закон: у повреждённого документа с двумя «применён» в одной
    цепочке выбор по списку зависел бы от перестановки, а выбор по
    цепочке — нет.

    Действующего ответа у цепочки нет (пересмотр открыт и не отвечен,
    цепочка оборвана или закольцована) — ячейка НЕ пишется и код не
    молчит: причина словами кладётся в `notes`, а параметр остаётся
    недостающим, то есть вопрос остаётся открытым и носители
    заблокированными. Догадка «возьмём любой применённый» — ровно тот
    подлог, который запрещает закон происхождения.

    Ячейка, у которой значение уже есть, не переписывается никогда."""
    from cdpm import provenance as _prov
    touched: list[str] = []
    for (fam, param), qs in _answers_by_target(doc).items():
        cell = coverage.get(fam)
        if cell is None or param in cell["given"]:
            continue
        superseded = {x.get("edition_of") for x in qs}
        root = next((x for x in qs
                     if str(x.get("id") or "") == _root_id(x)
                     and x.get("id") not in superseded), None)
        if root is None:
            if notes is not None:
                notes.append(f"{fam}.{param}: корневого вопроса цепочки в "
                             f"анкете нет — значение в посадку не "
                             f"возвращено")
            continue
        q, raw = _prov.active_answer(doc, root)
        if q is None:
            if notes is not None:
                notes.append(f"{fam}.{param}: значение в посадку не "
                             f"возвращено — {raw}")
            continue
        if not _is_applied_value(q):
            # Решение «не применяется» и вопрос, снятый кодом,
            # значения не несут: их закрывает свой путь (declined).
            continue
        from cdpm import question_editions
        scope_problems = question_editions.application_problems(doc, q)
        from cdpm import parameter_contract
        contract_problem = parameter_contract.application_problem(q, fam, param)
        if contract_problem:
            scope_problems.append(contract_problem)
        if scope_problems:
            if notes is not None:
                notes.extend(scope_problems)
            continue
        unit = _param_spec(fam, param).get("unit")
        raw = str((q.get("answer") or {}).get("raw") or "")
        from cdpm import answer_parsing
        parsed = answer_parsing.parameter(q, raw)
        got = None if parsed["ambiguous"] or parsed["value"] is None else (
            parsed["value"], parsed["quote"])
        carrier = _first_carrier(doc, fam, q.get("rule_refs"))
        if got is None:
            # A03-мин: ПОЧЕМУ значение не возвращено — обязано быть
            # СКАЗАНО. Три соседних выхода этой петли говорят словами
            # (нет корня цепочки, нет действующего ответа, открыт
            # пересмотр), а этот молчал. Формулировка задана аудитором
            # (GPT156 §5, подтверждена GPT170 п. 3) и здесь не
            # придумана. Путь станет ЧАЩЕ: `got is None` рождает
            # грамматика K02, а её круги сделали отказов больше.
            #
            # «Нет пригодного носителя» и «повреждённый `params`» —
            # ДРУГИЕ причины, не грамматика: этой заметкой они не
            # называются, и их молчание здесь не трогается.
            if notes is not None:
                notes.append(
                    f"Значение {fam}.{param} не восстановлено: "
                    f"действующий ответ {q.get('id')} не удалось "
                    f"разобрать как значение этого параметра; "
                    f"ячейка не записана"
                    + (": " + "; ".join(parsed["problems"])
                       if parsed["problems"] else ""))
            continue
        if carrier is None or not _cells_writable(carrier):
            continue          # С-1: кривые ячейки посадки не переписываем
        value, quote = got
        # РУБЕЖ K04 (§2.4 (б)) ВТОРЫМ ЗАМКОМ, и именно здесь: пока
        # пересмотр параметра открыт, самолечение не возвращает прежнее
        # значение в посадку — иначе прежний ответ оказался бы решением
        # НОВОГО вопроса, а синхронизация следом сняла бы пересмотр как
        # лишний. Первым замком стоит закон происхождения A03-мин выше
        # (действующий ответ цепочки), и он же говорит словами. Порядок
        # выбран композицией 12.09.2026: стой рубеж K04 ДО расчёта
        # происхождения — он бы гасил заметку молчанием, а «почему не
        # возвращено» обязано быть сказано (A03-мин). Здесь рубеж может
        # только ДОБАВИТЬ отказ, и тоже со словами.
        if any(m.is_revision_question(x) and m.is_open_question(x)
               for x in qs):
            if notes is not None:
                notes.append(f"{fam}.{param}: пересмотр параметра открыт "
                             f"— значение в посадку не возвращено, пока "
                             f"нет нового решения авиакомпании")
            continue
        # Общий писатель сверяет исходные носители и кандидат, прежде
        # чем заводить словарь ячеек. При отказе нет даже пустого
        # params и отметки given; причина возвращается вызывающему.
        problem = _write_answer_cell(carrier, q, param, value, quote, raw)
        if problem:
            if notes is not None:
                notes.append(f"{fam}.{param}: значение не восстановлено — {problem}")
            continue
        cell["cells"].add(param)
        cell["given"].add(param)
        touched.append(str(carrier.get("id")))
    if touched:
        # New answer cells may have a different proven catalogue origin.
        coverage.clear()
        coverage.update(family_coverage(doc))
    return touched


def ensure_family_param_questions(doc: dict, notes=None) -> list[str]:
    """Вопросы полноты параметров семей по своду — идемпотентно.
    Создаёт вопрос на каждый недостающий параметр семьи с носителями,
    блокирует носители; закрывает кодом открытые вопросы, ставшие
    лишними; снова открывает снятый кодом вопрос, если нужда вернулась.
    Возвращает id созданных и вновь открытых вопросов.

    `notes` — список ЗОВУЩЕГО, куда самолечение кладёт словами причину,
    по которой значение в посадку не возвращено. Причин НЕСКОЛЬКО, и
    каждая говорит своими словами: корневого вопроса цепочки в анкете
    нет; действующего ответа у цепочки нет; пересмотр параметра открыт
    (рубеж K04); действующий ответ есть, но разбор его не читает
    (13.09.2026, разрешено аудитором). Молчат по-прежнему два случая —
    нет пригодного носителя и повреждённые ячейки посадки: это ДРУГИЕ
    причины, не грамматика, и слова по ним — открытый вопрос шва K02. Поля документа заметки не получают: схема их не
    знает, а выдумывать поле ради заметки нельзя. Зов без `notes` —
    законен, но слова тогда никуда не идут: сток заметок кода в
    интерфейсе — волна 17б; до него носитель остаётся заблокированным,
    а вопрос открытым, и молчания в СВОДЕ нет по построению."""
    coverage = family_coverage(doc)
    from cdpm import question_editions
    scope_editions = []
    for (family, _param), group in _answers_by_target(doc).items():
        carriers = set((coverage.get(family) or {}).get("carriers") or [])
        superseded = {x.get("edition_of") for x in group}
        latest = _live_param_question(group) or next((x for x in group
            if x.get("id") not in superseded and x.get("id") == _root_id(x)), None)
        if latest is None or "edition" not in latest:
            continue
        sources = question_editions.normative_rule_sources(
            [r for r in doc.get("rules", []) if r.get("id") in carriers],
            latest.get("source_refs") or [])
        if carriers - question_editions.targets(latest) or sources != latest.get("source_refs"):
            try:
                new = question_editions.expand(doc, latest, sorted(carriers),
                                               content={"source_refs": sources})
            except question_editions.EditionRefusal as exc:
                if notes is not None:
                    notes.append(str(exc))
            else:
                _set_blockers(doc, str(latest["id"]), carriers, False)
                _set_blockers(doc, str(new["id"]), carriers, True)
                scope_editions.append(str(new["id"]))
    _reapply_values(doc, coverage, notes)
    # К-2, п. 6: у семьи с ДОКАЗАННОЙ перепосадкой ((б)/(в) §12.15 либо
    # записи в каталоге нет) лечения ответом нет — спрашивать нечего.
    reland = _reland_families(doc, coverage)
    questions = doc.setdefault("questions", [])
    existing = {q.get("id") for q in questions if isinstance(q, dict)}
    by_target = _answers_by_target(doc)
    created: list[str] = list(scope_editions)
    needed: set = set()
    by_id = {r.get("id"): r for r in doc.get("rules", []) or []
             if isinstance(r, dict)}
    for fam in sorted(coverage):
        if fam in reland:
            # Вопрос по такой семье — ложное обещание: ответ её не
            # вылечит (валидатор свода отвергнет применённую модель),
            # нужен повтор прогона по текущему каталогу. Уже ОТКРЫТЫЙ
            # вопрос не трогаем: он мог быть задан законно, ДО смены
            # каталога, и снимать его кодом словами «параметр задан
            # сводом» было бы неправдой (ниже он тоже пропускается).
            continue
        cell = coverage[fam]
        carriers = [by_id[rid] for rid in cell["carriers"] if rid in by_id]
        for param in cell["missing"]:
            needed.add((fam, param))
            qs = by_target.get((fam, param)) or []
            live = _live_param_question(qs)
            from cdpm import parameter_contract
            if live is None:
                superseded = {x.get("edition_of") for x in qs}
                # A formerly applied answer whose cell disappeared is not a
                # licence to reinterpret it after a catalogue change. Keep
                # its historical answer and ask a NEW edition instead.
                live = next((x for x in qs if x.get("id") not in superseded
                    and "parameter_contract" in x and not m.is_revision_question(x)
                    and _is_applied_value(x) and not question_editions.problems(x)
                    and parameter_contract.application_problem(x, fam, param)), None)
            if live is not None:
                if parameter_contract.reissue_needed(live, fam, param, carriers):
                    old_id = str(live["id"])
                    try:
                        # Preserve any already printed legacy identity; the
                        # new catalogue context belongs only to a NEW edition.
                        question_editions.freeze(live, legacy=True)
                        live = question_editions.expand(doc, live, list(cell["carriers"]), content={
                            "text": ("Контракт параметра в каталоге изменился; "
                                     "уточните значение по текущему описанию. "
                                     + _question_text(fam, param, carriers)),
                            "answer_format": {"type": "value", "note":
                                unit_words(_param_spec(fam, param).get("unit"))
                                + "; либо «не применяется»"},
                            "parameter_contract": parameter_contract.snapshot(fam, param)})
                    except question_editions.EditionRefusal as exc:
                        if notes is not None:
                            notes.append(str(exc))
                    else:
                        _set_blockers(doc, old_id, set(by_id), False)
                        created.append(str(live["id"]))
                if "edition" in live:
                    from cdpm import question_editions
                    if set(cell["carriers"]) - question_editions.targets(live):
                        old_id = str(live["id"])
                        try:
                            live = question_editions.expand(doc, live, list(cell["carriers"]))
                        except question_editions.EditionRefusal as exc:
                            if notes is not None:
                                notes.append(str(exc))
                        else:
                            _set_blockers(doc, old_id, set(by_id), False)
                            created.append(str(live["id"]))
                _set_blockers(doc, str(live["id"]), cell["carriers"], True)
                continue
            superseded = {x.get("edition_of") for x in qs}
            closed = next((q for q in qs if _is_code_closed(q)
                           and q.get("id") not in superseded
                           and (not m.is_revision_question(q)
                                or q.get("revision_of") not in superseded)), None)
            if closed is not None:
                if "edition" in closed:
                    from cdpm import question_editions
                    # Keep the historical code outcome. Reopening is a new
                    # question, not removal of an answer in a printed edition.
                    latest = closed
                    if m.is_revision_question(closed):
                        # A vanished carrier ended this reconsideration. Its
                        # return asks a NEW parameter question; it must not
                        # enlarge/rebase the historical revision's cell CAS.
                        # Follow the explicit revision root and its edition
                        # chain, keeping the code-closed revision untouched.
                        root = next((x for x in questions
                                     if x.get("id") == closed.get("revision_of")), None)
                        if root is None:
                            if notes is not None:
                                notes.append(f"{closed['id']}: нет корня закрытого пересмотра")
                            continue
                        latest = question_editions.latest(doc, root)
                    new = question_editions.expand(doc, latest, list(cell["carriers"]), content={
                        "text": (f"Возобновление после вопроса {closed['id']}, снятого кодом: "
                                 + _question_text(fam, param, carriers))})
                    _set_blockers(doc, str(new["id"]), cell["carriers"], True)
                    created.append(str(new["id"]))
                    continue
                closed.pop("answer", None)
                closed["text"] = _question_text(fam, param, carriers)
                closed["rule_refs"] = list(cell["carriers"])
                _set_blockers(doc, str(closed["id"]), cell["carriers"], True)
                created.append(str(closed["id"]))
                continue
            q = _new_question(doc, fam, param, carriers, existing)
            if q is None:
                continue
            questions.append(q)
            existing.add(q["id"])
            _set_blockers(doc, q["id"], cell["carriers"], True)
            created.append(q["id"])
    # Открытые вопросы параметров, чья нужда прошла, — снять кодом.
    # K04-барьер (§2.4 (б)): открытый вопрос-ПЕРЕСМОТР код не снимает,
    # пока жив хоть один носитель параметра — наличие старого значения
    # в ячейке нуждой в ответе быть не перестаёт. Единственный кодовый
    # исход — «вопрос больше не требуется» (все носители исчезли);
    # закон считает `m.code_close_refusal`, пишет
    # `m.close_question_by_code` — одно место на все пути синхронизации.
    for (fam, param), qs in by_target.items():
        if (fam, param) in needed or fam in reland:
            continue
        for q in qs:
            if not m.is_open_question(q) or _is_code_closed(q):
                continue
            if _has_answered_edition_ancestor(q, qs) and coverage.get(fam, {}).get("carriers"):
                # A value supplied for the old scope does not answer the new
                # edition merely because it remains somewhere in the family.
                continue
            if m.code_close_refusal(doc, q):
                continue
            m.close_question_by_code(
                doc, q, raw=CODE_CLOSED_RAW,
                note=(f"параметр {param} семьи {fam} задан "
                      f"сводом либо семья без носителей — "
                      f"вопрос снят кодом"))
            _set_blockers(doc, str(q["id"]), set(by_id), False)
    return created


# ---------------------------------------------------------------------------
# Разбор ответа
# ---------------------------------------------------------------------------

#: Содержательный знак: буква, цифра или подчёркивание. Продолжение из
#: одних знаков препинания («Не применяется.», «„Не применяется“,»)
#: содержанием не является.
_WORD_CHAR_RE = re.compile(r"\w")


def _sent_decline(sent: str) -> tuple[str, str] | None:
    """(первая фраза отказа предложения, первая ПОМЕХА безусловности)
    либо None, если фразы отказа в предложении нет.

    Помеха — первый СОДЕРЖАТЕЛЬНЫЙ кусок предложения, фразой отказа не
    являющийся, где бы он ни лежал: ДО фразы («600 минут; для SVO не
    применяется», «1) Не применяется»), МЕЖДУ фразами и ПОСЛЕ них («Не
    применяется, 600 минут»). Помеха пуста, когда предложение состоит
    из фраз отказа и знаков препинания при них: «Не применяется!», «Не
    применяется: такого требования у нас нет» — тот же безусловный
    отказ, сказанный дважды, а не отказ с условием. Проход по
    предложению ОДИН (finditer), поэтому длина ответа входит в цену
    линейно.

    ФРАЗА ОТКАЗА УЗНАЁТСЯ ПО СВЁРТКЕ ОМОГЛИФОВ (двенадцатое неавторское
    ревью, находка Б1 — БЛОКИРУЮЩАЯ). `_DECLINE_RE` набран кириллицей,
    и одиннадцатый круг закрыл только СМЕШАННОЕ написание («Hе» —
    латинская H при кириллической «е»): его ловит mixed_script_word.
    Написание ЦЕЛИКОМ ЛАТИНСКОЕ («He» — обе буквы латинские, на вид
    неотличимо) смешанным словом не является и не ловилось ничем:
    ответ «600 минут. He применяется для базы SVO.» садился в посадку
    правила-носителя значением 600, условие авиакомпании молча
    исчезало из выпускаемой постановки, статус «применён», блокировка
    снята — fail-open с НЕВЕРНЫМ значением (принципы 2, 3 и 4 ВМП).
    Половины закона разошлись: смешанное написание отказывало, целиком
    латинское — нет, а на вид это ОДИН ответ.

    Свёртка (_folded) 1:1 по знакам, длина не меняется, поэтому
    границы совпадений годны для выдержки ДОСЛОВНО, и заметка цитирует
    «He применяется», а не свёрнутое «Не применяется». Сторона закона
    та же, что у слова величины, — ОТКАЗ: свёртка помогает УЗНАТЬ
    отказ, но не читает по свёртке ни значения, ни условия. Слово
    ДВУМЯ алфавитами фразой отказа по-прежнему не становится: его
    закон (mixed_script_word) стоит РАНЬШЕ — см. parse_value и
    apply_param_answer."""
    phrase, gap, pos = "", "", 0
    for mm in _DECLINE_RE.finditer(_folded(sent)):
        head = sent[pos:mm.start()]
        if not gap and _WORD_CHAR_RE.search(head):
            gap = head.strip()     # содержание ДО фразы отказа (K-1)
        if not phrase:
            # ДОСЛОВНО из ответа, а не из свёртки (Б1): свёртка узнаёт
            # фразу, цитирует её ответ.
            phrase = sent[mm.start():mm.end()].strip()
        pos = mm.end()
    if not phrase:
        return None
    rest = sent[pos:]
    if not gap and _WORD_CHAR_RE.search(rest):
        gap = rest.strip()         # содержание ПОСЛЕ фраз отказа (В-3)
    return phrase, gap


def _decline_parts_of(sents: list[str]) -> tuple[str, str, bool] | None:
    """(первая фраза отказа, ПОМЕХА безусловности, помеха в том же
    предложении) ответа, разобранного на предложения, либо None —
    фразы отказа в ответе нет вовсе.

    ЗАКОН (ревью барьера K01, замечание В-3; пятое неавторское ревью
    K02, B1; шестое, K-1; контракт П0 §2.2 «исключения не
    сворачиваются в глобальное „не применяется“; безусловный отказ —
    только отдельная форма без хвоста»): БЕЗУСЛОВНЫЙ ОТКАЗ — ЭТО ВЕСЬ
    ОТВЕТ ИЗ ФРАЗ ОТКАЗА. Помеха пуста только тогда, когда КАЖДОЕ
    предложение ответа состоит из фраз отказа и больше ни из чего;
    любой другой содержательный кусок — условие («кроме базы SVO»),
    значение («600 минут»), основание («п. 4.2»), номер строки анкеты
    («1)», «2.») — становится помехой, ГДЕ БЫ ОН НИ СТОЯЛ: до фразы
    отказа или после неё, в том же предложении или в соседнем.

    Порядок не решает НИЧЕГО (K-1): «В остальных базах 600 минут. Не
    применяется для базы SVO.» и «Не применяется для базы SVO. В
    остальных базах 600 минут.» — один и тот же разлад и один и тот же
    исход «не разобран». Раньше отказ судился по НАЧАЛУ первого
    предложения, и исключение, названное ПОСЛЕ значения, молча
    исчезало: ответ шёл в «применён» значением 600, вопрос закрывался,
    блокировки снимались, а условие авиакомпании не попадало в
    выпускаемую постановку (принцип 4 ВМП), ограниченное значение
    издавалось как общее (принцип 3), и один смысл давал два исхода по
    порядку предложений (принцип 2).

    Помеха называется целиком: куском ТОГО ЖЕ предложения (головой или
    хвостом), когда она лежит при ПЕРВОЙ фразе отказа (В-3), и
    предложением, когда в другом (B1). Проход по предложениям ОДИН и
    кончается, как только известны и фраза, и помеха. Сторона закона —
    fail-closed: значение или условие не теряются молча, вопрос
    остаётся открытым."""
    phrase, obstruction, same = "", "", False
    for sent in sents:
        part = _sent_decline(sent)
        if part is None:
            if not obstruction:
                obstruction, same = sent, False    # предложение — не отказ
        else:
            here = not phrase                      # первая фраза отказа здесь
            if here:
                phrase = part[0]
            if part[1] and not obstruction:
                obstruction, same = (part[1], True) if here else (sent, False)
        if phrase and obstruction:
            break                                  # исход уже решён
    return (phrase, obstruction, same) if phrase else None


def _decline_parts(raw: str) -> tuple[str, str, bool] | None:
    """То же по ответу целиком (см. _decline_parts_of).

    ПОРЯДОК ЗАКОНОВ объявлен здесь, а не выведен из грамматики: слово,
    записанное ДВУМЯ алфавитами сразу, не читается ВОВСЕ — «ни как
    единица, ни как обозначение, НИ КАК ФРАЗА ОТКАЗА»
    (mixed_script_word, одиннадцатое ревью, находка 3). Свёртка
    омоглифов у фразы отказа (находка Б1) узнаёт написание ЦЕЛИКОМ
    латинское, и без этой оговорки она заодно прочла бы СМЕШАННОЕ
    («Hе применяется.») — решением, закрывающим вопрос и снимающим
    блокировку, — то есть сняла бы закон находки 3 у самого дорогого
    его случая. Смешанный ответ по-прежнему «не разобран»: вопрос
    открыт, носители заблокированы."""
    if mixed_script_word(raw):
        return None
    return _decline_parts_of(_sentences(raw))


def is_declined(raw: str) -> bool:
    """«Не применяется» / «такого требования нет» — БЕЗУСЛОВНЫЙ отказ
    от параметра (решение авиакомпании, не молчание). Решение судится
    тем же чтением текста, что значение и цитата (_sentences): ответ,
    повреждённый управляющим знаком или не являющийся текстом,
    предложений не даёт, а значит не несёт и решения («не
    применяется\\x00» вопрос не снимает, K02-R4/R5). Иначе
    повреждённый ответ закрывал бы параметр как решённый — самый
    дорогой из возможных исходов разбора.

    Отказ безусловен, когда ВЕСЬ ОТВЕТ состоит из фраз отказа и больше
    ни из чего (ревью барьера K01, замечание В-3; пятое неавторское
    ревью K02, B1; шестое, K-1): «Не применяется», «Неприменимо!»,
    «Нет такого требования», «Не применяется: такого требования у нас
    нет» (тот же отказ, сказанный дважды), «Не применяется. Нет такого
    требования.». Всё остальное рядом с фразой отказа — условие («Не
    применяется. Кроме базы SVO.»), значение («Не применяется. В
    остальных базах 10:00.»), основание («Не применяется. Основание —
    п. 4.2.»), номер строки анкеты («1) Не применяется», «2. Не
    применяется») — отказом НЕ является, и МЕСТО фразы отказа ничего
    не меняет: хвост в том же предложении («Не применяется для
    сотрудников базы SVO; в остальных базах 10:00», «Не применяется,
    600 минут»), голова перед ней («600 минут; для SVO не
    применяется»), соседнее предложение до значения и после него («600
    минут. Не применяется для базы SVO.»). По §2.2 контракта П0 такой
    ответ — «не разобран»: вопрос остаётся открытым, носители
    заблокированы (см. _decline_parts_of и apply_param_answer). Раньше
    судилось НАЧАЛО первого предложения: параметр закрывался решением
    «не применяется» для ВСЕХ баз, хотя авиакомпания отказалась от
    него лишь для одной, — а при обратном порядке тех же предложений
    молча вносилось значение, названное лишь для остальных баз."""
    parts = _decline_parts(raw)
    return parts is not None and not parts[1]


def decline_tail(raw: str) -> str:
    """Помеха безусловности отказа: содержательный кусок ТОГО ЖЕ
    предложения — до фразы отказа или после неё — либо ПЕРВОЕ
    предложение ответа, целиком из фраз отказа не состоящее («не
    разобран», В-3/B1/K-1). Пусто во всех прочих случаях: фразы отказа
    в ответе нет либо отказ безусловен."""
    parts = _decline_parts(raw)
    return parts[1] if parts is not None else ""


#: Точка сокращения единицы за числом («10 ч.», «600 мин.», «1 сут.»,
#: «1 дн.») — часть СЛОВА единицы: П0 §12.6 объявляет «ч.», «мин.»,
#: «сут.», «дн.» словами единиц, поэтому точка остаётся в слове и в
#: цитате, где бы слово ни оказалось, — иначе объявленное «1 дн.» не
#: читается (K02-F5). Метка _DOT_KEEP несёт эту точку через деление на
#: предложения; во входном тексте её самой быть не может (ответ с
#: управляющим знаком отвергается целиком, см. _CTRL_RE).
_UNIT_ABBR_DOT_RE = re.compile(
    r"(?<=\d)(\s*(?:ч|мин|сут|дн))\.", re.IGNORECASE)
_DOT_KEEP = "\x00"
#: Граница предложения — ОДИН закон для любой точки, в том числе для
#: точки сокращения единицы (_DOT_KEEP): предложение кончается только
#: там, где продолжение способно его НАЧАТЬ — с прописной буквы. Цифра,
#: скобка, знак равенства, строчная буква после точки продолжают ТУ ЖЕ
#: запись значения, поэтому «12 ч. (600 минут)», «600 мин. = 12 часов»,
#: «12 часов. (600 минут)», «600 минут. 720 минут» — одно предложение,
#: где обе записи сверяются между собой и противоречие не теряется за
#: искусственной границей (K02-F1), а «10 ч. 30 мин.» читается целиком.
#: Сокращение при этом не меняет деления: «Отдых вне базы — 10 ч. Дома
#: — 12 ч.» делится так же, как «…10 часов. Дома — 12 часов.» — значение
#: даёт первое предложение, цитата сохраняет точку единицы.
_SENT_BREAK_RE = re.compile(r"(?<=[.!?" + _DOT_KEEP + r"])\s+(?=\S)")
#: Начало нового предложения: прописная БУКВА, до которой стоят разве
#: что открывающие кавычки и скобки («Дома — 12 ч.», «(Пункт 4.2)»).
#: Кавычка и скобка сами предложения не начинают — за ними должна
#: стоять буква, поэтому «(600 минут)» остаётся продолжением записи.
_SENT_START_RE = re.compile(r"[\"'«„“‘(\[{]*(?P<c>[^\W\d_])")
#: Управляющий знак, не являющийся пробелом (метка _DOT_KEEP — первая
#: из них), — повреждение текста: такой ответ не даёт ни предложений,
#: ни цитаты. Иначе ответ подделывал бы разбор своей же меткой («12
#: ч\x00 Дома 600 минут» дал бы 720 с цитатой «12 ч.», которой в ответе
#: не было), а цитата переставала бы быть дословной подстрокой ответа
#: («600\x00минут»). Повреждённая запись отказывает — тот же закон, что
#: у нечитаемого числового токена (K02-F2). Пробельные управляющие
#: (\t \n \v \f \r \x1c–\x1f) остаются обычным пробелом.
_CTRL_RE = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")


def _starts_sentence(s: str, pos: int) -> bool:
    """Способно ли продолжение с позиции pos НАЧАТЬ предложение."""
    mm = _SENT_START_RE.match(s, pos)
    return bool(mm) and mm.group("c").isupper()


def _normalized(raw: str) -> str | None:
    """Нормализованный текст ответа — ОДИН вход для деления на
    предложения и для закона номера строки анкеты: пробелы схлопнуты,
    точка сокращения единицы помечена (_DOT_KEEP). None — ответ
    повреждён управляющим знаком или не является текстом."""
    text = raw if isinstance(raw, str) else ""
    if _CTRL_RE.search(text):
        return None
    s = " ".join(text.split())
    return _UNIT_ABBR_DOT_RE.sub(lambda mm: mm.group(1) + _DOT_KEEP, s)


def _sentence_starts(s: str) -> list[int]:
    """Позиции нормализованного текста, с которых НАЧИНАЮТСЯ его
    предложения (начало ответа и каждая граница предложения)."""
    return [0] + [mm.end() for mm in _SENT_BREAK_RE.finditer(s)
                  if _starts_sentence(s, mm.end())]


def _line_starts(s: str) -> list[int]:
    """Позиции, с которых способна начаться СТРОКА анкеты: начало
    ответа и место за КАЖДОЙ точкой (восклицательным, вопросительным
    знаком) с пробелом — независимо от того, начинается ли там
    предложение.

    Шире, чем _sentence_starts, и намеренно: предложение начинается
    только с прописной БУКВЫ, а строка анкеты начинается с ЦИФРЫ,
    поэтому по закону предложений номер строки не стоит в начале
    ничего и «Смотрите ниже. 1. A320, B738» прятало бы номер внутри
    первого предложения — разбор читал бы перечень «1»."""
    return [0] + [mm.end() for mm in _SENT_BREAK_RE.finditer(s)]


def _raw_line_starts(text: str) -> list[int]:
    """Позиции НОРМАЛИЗОВАННОГО текста, с которых начинается каждая
    непустая СТРОКА сырого текста ответа. Нормализация схлопывает
    переносы строк в пробел (_normalized), а строка анкеты начинается
    с новой строки буквально, поэтому за вводными словами без точки
    («Типы судов:») номер «1)» не стоит ни в начале ответа, ни в
    начале предложения — только в начале строки. Позиции считаются
    по токенам: нормализованный текст — те же токены через один
    пробел, а метка _DOT_KEEP длины не меняет."""
    out, pos = [], 0
    for line in text.splitlines():
        toks = line.split()
        if toks:
            out.append(pos)
            pos += sum(len(t) for t in toks) + len(toks)
    return out


#: Разделитель ПЕРЕЧНЯ — знак или оборот, которым вводная часть ответа
#: КОНЧАЕТСЯ и за которым идёт перечисление: двоеточие, точка с
#: запятой, тире в пробелах, оборот «а именно», «в частности».
#: Девятое неавторское ревью K02 (B-1): постановка дыры называла ДВЕ
#: причины, а закрыта была одна — перенос строки. Тот же ответ, набранный
#: В ОДНУ СТРОКУ («Типы судов: 1) A320, 2) B738», «Типы судов — 1) A320»,
#: «Норма: 10. Это норма»), номера не давал и уезжал в посадку перечнем
#: «1, 2, A320, B738» и счётом 10: один смысл давал два исхода смотря по
#: тому, нажал ли отвечающий Enter (принцип 2 ВМП), а живые параметрные
#: ответы книг — ОДНОСТРОЧНЫЕ. Тире здесь отделено пробелами: дефис
#: ВПЛОТНУЮ соединяет запись («10-12 часов», «A320-200»), а не вводит
#: перечень. Двоеточие ЗАПИСИ ВРЕМЕНИ вводной частью не является:
#: иначе «10:00. Конец» начинало бы перечень с «00.» и время суток,
#: прочитанное верно, отказывало бы номером строки, которого нет
#: (собственный перебор на перегиб, находка A).
#:
#: Десятое ревью, B-2: запрет записи времени стоял НЕ ТАМ — «цифра
#: СЛЕВА от двоеточия», — а у записи времени цифра стоит ЗА двоеточием
#: («10:00»); слева она стоит у обычной вводной части, кончающейся
#: номером вопроса или строки. Запрет гасил перечень у «Вопрос 2: 1.
#: A320» (посадка «1, 2» — обозначение пропадало совсем), «Строка 1:
#: 1) A320» («1, A320»), «Пункт 3: 1) A320, 2) B738» («1, 2, 3, A320,
#: B738»), а двухстрочные близнецы всех трёх отказывали с блокировкой —
#: та самая разноголосица «Enter / не Enter» (принцип 2 ВМП), ради
#: которой правился девятый круг. Теперь запрет стоит по ОБЕ стороны:
#: двоеточие вводной частью не является только там, где цифра и слева,
#: и справа, — то есть ровно у записи времени. ОСТАТОК назван вслух:
#: «Строка 10:1) A320» (цифра по обе стороны, пробела нет) от записи
#: времени «10:1» не отличается ни машиной, ни человеком — там закон
#: молчит, и ответ читается как прежде.
#:
#: Десятое ревью, B-3: список вводных оборотов лексически неполон —
#: словарь синонимов полным не бывает, и «Типы судов то есть 1) A320»,
#: «Типы судов, 1) A320, 2) B738» садились в посадку перечнем «1, 2,
#: A320, B738», тогда как двухстрочные близнецы отказывали. Обороты
#: «то есть» и «как то» в список добавлены, но главная опора —
#: БЕССЛОВАРНЫЙ признак (см. _list_starts): ЗАПЯТАЯ, до которой в
#: ответе не встретилось НИ ОДНОЙ ЦИФРЫ, кончает вводную часть. Цифра
#: до запятой означает, что перечисление значений уже идёт («Флот: 320,
#: 737. Конец», «A320, B738»), и такая запятая вводной частью не
#: является. Признак не зависит ни от какого словаря и потому
#: закрывает обороты, которых в списке нет: «Типы судов, то бишь 1)
#: A320». Цена измерена: на переборе 2640 входов, на 158 законных
#: ответах широкого набора и на 103 ответах 14 живых книг признак не
#: изменил НИ ОДНОГО исхода. ОСТАТОК назван вслух: вводный оборот БЕЗ
#: знака препинания и вне словаря («Типы судов сиречь 1) A320»)
#: перечня не вводит; бессловарный признак «любой пробел до первой
#: цифры» измерен и ОТВЕРГНУТ как перегиб — он меняет 658 исходов
#: перебора из 2640 («Пункт 4. 600 минут», «Раздел 3. 12 часов»,
#: «Приказ №12 от 01.09» стали бы отказами).
_LIST_LEAD_RE = re.compile(
    r"(?:(?:(?<!\d)[:;]|[:;](?!\d))\s*|\s[-\u2013\u2014]\s+"
    r"|\b(?:а именно|в частности|то есть|как то)\s+)(?=\S)",
    re.IGNORECASE)
#: Начало слова и первая цифра ответа: бессловарный признак конца
#: вводной части (B-3, см. _list_starts).
_WORD_RE = re.compile(r"\S+")
_ANY_DIGIT_RE = re.compile(r"\d")


def _list_starts(s: str) -> list[int]:
    """Позиции нормализованного текста, с которых начинается
    ПЕРЕЧИСЛЕНИЕ: сразу за разделителем перечня (_LIST_LEAD_RE) либо —
    БЕССЛОВАРНЫМ признаком (B-3) — на любом слове между ПЕРВОЙ ЗАПЯТОЙ
    ответа и первой его цифрой, если до запятой цифр не было.

    Запятая, до которой в ответе не встретилось ни одной цифры,
    кончает вводную часть: «Типы судов, 1) A320, 2) B738», «Типы судов,
    то бишь 1) A320», «Типы судов, как их называет документ, 1) A320».
    Цифра до запятой означает, что перечисление ЗНАЧЕНИЙ уже идёт
    («Флот: 320, 737. Конец», «Явка в 10:00, вылет в 11:30»), и такая
    запятая вводной частью не является. Дальше первой цифры места не
    открываются: там стоит либо сам знак номера, либо значение.
    Достаточно ПЕРВОЙ запятой — слова за любой следующей суть слова и
    за нею. Ответ читается ОДИН раз, слова перебираются до первой
    цифры: длинный ответ не даёт квадрата (K02-T4)."""
    out = [mm.end() for mm in _LIST_LEAD_RE.finditer(s)]
    first = _ANY_DIGIT_RE.search(s)
    limit = first.start() if first else len(s)
    cut = s.find(",")
    if 0 <= cut < limit:
        for w in _WORD_RE.finditer(s, cut + 1):
            if w.start() > limit:
                break
            out.append(w.start())
    return out


def _sentences(raw: str) -> list[str]:
    """Предложения ответа (пробелы схлопнуты, без завершающей точки;
    точка сокращения единицы сохраняется как часть слова — «1 дн.»,
    «10 ч. 30 мин.»): цитата-источник значения — ОДНО предложение, как
    у цитат клауз. Повреждённый управляющими знаками ответ предложений
    не даёт; не текст — не ответ."""
    s = _normalized(raw)
    if s is None:
        return []
    out, start = [], 0
    for cut in _sentence_starts(s)[1:] + [len(s)]:
        part = s[start:cut]
        start = cut
        part = part.strip().rstrip(".!?").strip().replace(_DOT_KEEP, ".")
        if part:
            out.append(part)
    return out


#: ЗНАК НОМЕРА строки анкеты (восьмое неавторское ревью K02, остаток
#: (а) шестого — у ЕДИНИЦ ОБОЗНАЧЕНИЙ; письменные формы — девятое,
#: K-1): целое при знаке номера, а дальше — содержание ответа. Формы
#: перечислены те, что встречаются в ответах: «1.», «1)», «(1)», «[1]»,
#: «№1», «№ 1» — с пробелом внутри знака и за ним и без него («1.A320»,
#: «1 . A320», «1 ) A320», «№ 1 A320»). Прежде закон знал ДВЕ формы из
#: этих шести и ловил половину знака («1)»), пропуская целый («(1)»), —
#: разноголосица (принцип 2 ВМП) с fail-open и НЕВЕРНЫМ значением:
#: «(1) A320», «[1] A320», «№1 A320» давали перечень «1, A320».
#: Точка НЕ считается знаком номера, когда за ней идёт цифра: «1.75» и
#: «1.5» — десятичная запись, а не строка 1; «10.05.2026» — дата; тем
#: же чтением закрыт «№ 1.5». Номер без содержания за ним номером не
#: является: ответ «3.», «№1» — это само число, там нечего нумеровать.
#: У знака «№» содержание судится строже прочих форм — словом или
#: числом вплотную («№1 A320»), а не любым знаком: точка, скобка и
#: пояснение за ним оставляют «№1» САМИМ ЗНАЧЕНИЕМ («№1.», «№1 — по
#: документу», «Борт №1»), потому что «№» стоит ПЕРЕД числом и числа
#: не закрывает, тогда как «1.», «1)», «(1)» закрывают его сами и всё
#: дальнейшее — уже содержание строки (собственный перебор, находка C).
_LINE_NO_RE = re.compile(
    r"(?:\u2116\s*\d+(?![.,]\d)\s*(?=[^\W_])"
    r"|(?:\(\d+\)|\[\d+\]|\d+\s*(?:\)|\.(?!\d)))\s*(?=\S))")
#: СЛАБЫЙ знак номера — тире и двоеточие (девятое ревью: «1 - A320»
#: давало перечень «-, 1, A320»; «1: A320» — «1, A320»). Слабые
#: они потому, что те же знаки разделяют значение и пояснение
#: («3 — по документу», «3: по документу» — значение 3, ЗАКОННЫЙ
#: ответ из перебора рецензента) и связывают диапазон и время
#: («10-12 часов», «600 мин. - 12 ч.», «10:00»). ОТДЕЛЬНО: половину той
#: находки — «обозначением стал сам дефис» — слабый знак не закрывал
#: вовсе, он лишь ЗАСЛОНЯЛ её там, где срабатывал; дефис перестал
#: быть обозначением только законом обозначения (см. _CODE_RE, B-1
#: десятого ревью).
#:
#: Десятое ревью, K-1: исход РЕШАЛ РЕГИСТР первой буквы пояснения.
#: Закон читал «слово с прописной буквы» как содержание строки, и
#: разошлись 11 пар из 11: «3 — по документу» давало значение 3, а
#: «3 — По документу» — отказ, хотя ответ ОДИН И ТОТ ЖЕ и докстрока
#: сама называла его законным. Регистр — привычка набора, а не закон
#: (принцип 2 ВМП): одно и то же слово с большой и с малой буквы не
#: может давать разные постановки.
#:
#: Теперь признак от регистра НЕ ЗАВИСИТ: за слабым знаком номер
#: узнаётся там, где дальше стоит ОБОЗНАЧЕНИЕ — токен справочника,
#: начинающийся латинской буквой («1 - A320», «1: a320», «1 – B738»).
#: Основание: обозначения каталога 0.5.0 пишутся латиницей и цифрами
#: (см. _CODE_RE), и только обозначение за слабым знаком уехало бы в
#: посадку правила-носителя значением, которого авиакомпания не
#: называла. Русское пояснение за слабым знаком — пояснение, а не
#: содержание строки, и НИ С ПРОПИСНОЙ, НИ СО СТРОЧНОЙ буквы номера не
#: даёт: «3 — по документу» и «3 — По документу» — одно значение 3.
#: Цена прежних границ сохранена целиком: запись времени «10:00» за
#: слабым знаком несёт цифру, а не латинскую букву (84 верных исхода),
#: «3 — по документу» несёт кириллицу (21 верный исход). ЦЕНА НОВОЙ
#: границы названа вслух: номер строки, содержание которой написано
#: ПО-РУССКИ («1 - Это норма», «1: Не применяется»), слабым знаком не
#: узнаётся — он неотличим от законного «⟨значение⟩ — ⟨пояснение⟩», а
#: этой формой отвечают чаще, чем нумеруют строки тире. У СИЛЬНОГО
#: знака («1.», «1)», «(1)», «[1]») такой платы нет: он закрывает
#: число сам, и «1. Это норма» — номер строки при любом содержании.
#: Тире при этом отделено пробелом: дефис ВПЛОТНУЮ — часть обозначения
#: («A320-200», «737-800», «737-MAX»). Граница названа вслух ниже.
_WEAK_NO_RE = re.compile(
    r"(?P<sign>\d+(?:\s*:|\s+[-\u2013\u2014]))\s*"
    r"(?P<c>[A-Za-z][A-Za-z0-9_\-]*)")


def _line_no_at(s: str, pos: int, weak: bool) -> str:
    """Знак номера строки анкеты, стоящий на позиции pos нормализованного
    текста, — ДОСЛОВНО, как его написала авиакомпания, либо «». Сильный
    знак судится сам по себе, слабый — по содержанию за ним (оно обязано
    быть ОБОЗНАЧЕНИЕМ, регистр ничего не решает, K-1) и только там, где
    строка начинается НА САМОМ ДЕЛЕ (weak)."""
    mm = _LINE_NO_RE.match(s, pos)
    if mm:
        return mm.group(0).strip().replace(_DOT_KEEP, ".")
    mm = _WEAK_NO_RE.match(s, pos) if weak else None
    return mm.group("sign").strip() if mm else ""


def line_number_record(raw) -> str:
    """Номер строки анкеты при ответе — ДОСЛОВНО, как его написала
    авиакомпания («1.», «10)»), либо «» — номера нет.

    ЗАКОН (контракт П0 §2.6 «значение равно тому, что прочитал бы
    человек, либо вопрос открыт»; принципы 3 и 4 ВМП): номер строки
    анкеты — НЕ ЧАСТЬ ОТВЕТА, а что названо цифрой — номер строки или
    значение, — ответ не говорит. Поэтому fail-closed: ответ с номером
    строки не разбирается ВОВСЕ (см. parse_value), вопрос остаётся
    открытым, носители заблокированы, а заметка просит назвать значение
    без номера строки. Ни догадки «это номер, отбросим его», ни тихого
    чтения номера значением здесь нет и не будет.

    Тот же закон уже закрыл нумерованный ОТКАЗ («2. Не применяется» —
    номер помеха, отказ не безусловен, см. _decline_parts_of) и
    нумерованное ЧИСЛО у длительности и времени суток («1) 600 минут» —
    цифра вне числового выражения, см. _digits_covered). Восьмое ревью
    нашло ту же дыру там, где закон охвата цифр не действует, потому
    что ЗАПИСЬЮ ЗНАЧЕНИЯ там является голое целое либо любой токен
    справочника: у ОБОЗНАЧЕНИЙ (code — «1. A320» давало код «1»;
    code_list — «1. A320, B738» давало перечень «1», а «1) A320, B738»
    — «1, A320, B738») и у голых чисел (count, credit_hours,
    hours_per_credit_hour — «1. Это норма» давало 1). Номер строки
    уезжал в посадку правила-носителя обозначением или счётом, которого
    авиакомпания не называла, — fail-open с НЕВЕРНЫМ значением.

    Номер ищется в начале ответа и за КАЖДОЙ границей предложения
    (_line_starts), а не только там, где по закону предложений
    начинается предложение: строка анкеты начинается с ЦИФРЫ, а
    предложение — с прописной буквы, поэтому номер строки в начале
    предложения не стоит НИКОГДА. Оба исхода деления накрыты одним
    поиском: «1.» перед прописной буквой предложение рвёт, и номер
    остаётся отдельным предложением, неотличимым от значения
    («1. A320» → «1», «A320»); «1)» и «1.» перед цифрой или строчной
    буквой не рвут ничего, и номер лежит внутри предложения
    («Смотрите ниже. 1. A320, B738»). Поиск идёт по тому же
    нормализованному тексту (_normalized), что и деление, поэтому
    сокращение единицы номером не становится («10 ч. 30 мин.»), а
    повреждённый ответ номера не даёт — он и предложений не даёт
    (K02-R4/R5). Третье место номера — НАЧАЛО СТРОКИ ТЕКСТА ответа:
    строка анкеты начинается с новой строки буквально, а перенос
    строки нормализация схлопывает в пробел, поэтому за «Типы
    судов:» с двоеточием (не граница предложения) номер «1)» не
    стоял ни в одном начале и «Типы судов:\n1) A320\n2) B738» давало
    перечень «1, 2, A320, B738». Начало каждой строки текста — те же
    позиции нормализованного текста (_raw_line_starts) — судится ТЕМ
    ЖЕ поиском, что два первых места, поэтому и номер, стоящий один
    на своей строке («Типы:\n1)\nA320»), виден; строка без
    знака номера или без содержания за ним («3.», «1.5», «600 минут»)
    номером не становится, как и в двух первых местах.

    ЧЕТВЁРТОЕ место — НАЧАЛО ПЕРЕЧИСЛЕНИЯ за вводным оборотом в ОДНУ
    СТРОКУ (девятое ревью): двоеточие, точка с запятой, тире в
    пробелах, обороты «а именно», «в частности», «то есть», «как то», а
    также ЗАПЯТАЯ, до которой в ответе не встретилось ни одной цифры
    (бессловарный признак, десятое ревью, B-3; см. _LIST_LEAD_RE и
    _list_starts). Постановка дыры называла ДВЕ причины — вводные слова
    с двоеточием и схлопнутый перенос строки, — а закрыта была одна:
    «Типы судов:\n1) A320\n2) B738» отказывало, а ТОТ ЖЕ ответ в одну
    строку — «Типы судов: 1) A320, 2) B738» — садился в посадку
    правила-носителя перечнем «1, 2, A320, B738», «Типы судов: 1. A320»
    — кодом «1», «Норма: 10. Это норма» — счётом 10. Один смысл давал
    два исхода смотря по тому, нажал ли отвечающий Enter (принцип 2
    ВМП), причём рабочая форма ответа — как раз однострочная: живые
    параметрные ответы книг Н0-Б и polyaris-h0b набраны одной строкой.
    Десятое ревью нашло у того же места ДВЕ течи: запрет «двоеточие при
    цифре» гасил перечень после «Строка 1:», «Вопрос 2:», «Пункт 3:»
    (B-2), а словарь вводных оборотов был неполон — «Типы судов, 1)
    A320» и «Типы судов то есть 1) A320» текли (B-3). Теперь все четыре
    места судятся ОДНИМ поиском, и однострочный близнец даёт тот же
    исход, что многострочный.

    Граница закона названа вслух. (1) Обозначение, СОСТОЯЩЕЕ ИЗ ЦИФР
    (типы судов каталога 0.5.0 — fleet_type/fleet_types, «коды из
    документа авиакомпании»: «737», «320»), читается как прежде, пока
    при нём нет знака номера: «737» → код 737, «737, 320» → перечень.
    Знак номера при таком коде («737. 320») делает ответ неразборчивым
    — там и человек не скажет, строка это или тип судна. (2) СЛАБЫЙ
    знак — тире и двоеточие — судится только по содержанию за ним (см.
    _WEAK_NO_RE), и содержание это — ОБОЗНАЧЕНИЕ справочника, а не
    слово с прописной буквы: «1 - A320», «1: a320» — номер строки, а
    «3 — по документу», «3 — По документу», «3: по документу», «1 - 12
    часов» — значение 3 и диапазон. Десятое ревью (K-1) нашло, что
    исход решал РЕГИСТР первой буквы: разошлись 11 пар из 11, и
    «3 — По документу» отказывало там, где «3 — по документу» давало 3.
    Регистр — привычка набора, а не закон (принцип 2 ВМП), и теперь его
    не судят вовсе. И судится слабый знак только там, где строка
    начинается НА САМОМ ДЕЛЕ: в начале ответа, строки текста и
    перечисления, — но не за КАЖДОЙ точкой с пробелом, как сильный.
    Место за точкой намеренно шире начала предложения (см.
    _line_starts), и слабый знак съел бы там законную ссылку с
    пояснением: «Согласно п. 4 — A320» дало бы номер «4 —». Цена
    границы названа: номер строки, за которым стоит ЧИСЛО («1 - 600
    минут», «1: 600 минут») или РУССКОЕ пояснение («1 - Это норма»,
    «1 - не применяется»), слабым знаком не узнаётся — там закон молчит
    и ответ читается как раньше. Иначе закон съел бы законный ответ
    «⟨значение⟩ — ⟨пояснение⟩», которым отвечают чаще, чем нумеруют
    строки тире. Цена измерена на переборе рецензента (2640 входов):
    тире без разбора содержания отняло бы 21 ВЕРНЫЙ прежний исход
    («3 — по документу» → 3), двоеточие — 84 (все записи времени:
    «10:00» стало бы номером «10:»), а слабый знак, судимый ещё и за
    каждой точкой, забрал бы 12 ответов вида «Согласно п. 4 — A320»,
    назвав номером строки ссылку на пункт. (3) Кавычка и скобка перед
    номером места не открывают: «Документ гласит: «1. A320»» — цитата
    документа, а не строка анкеты, и читается как прежде."""
    text = raw if isinstance(raw, str) else ""
    s = _normalized(text)
    if s is None:
        return ""
    weak = {0} | set(_raw_line_starts(text)) | set(_list_starts(s))
    for start in sorted(set(_line_starts(s)) | weak):
        record = _line_no_at(s, start, start in weak)
        if record:
            return record
    return ""


def answer_quote(raw: str) -> str:
    """Первое предложение ответа без завершающей точки (точка
    сокращения единицы — часть слова, она остаётся)."""
    sents = _sentences(raw)
    return sents[0] if sents else ""


def _one(values) -> object | None:
    """Единственная величина набора либо None (значений нет или их
    больше одного). Второе РАЗНОЕ значение решает исход, поэтому
    перебор на нём кончается: иначе ответ из 8000 разных чисел сверял
    бы каждое с каждым — тот же квадрат, что был у охвата цифр
    (K02-T4), в том же пути разбора."""
    vals = []
    for v in values:
        if v not in vals:
            if vals:
                return None
            vals.append(v)
    return vals[0] if vals else None


#: Исходы разбора предложения: значение; значений нет; отказ (токен
#: нечитаем или предложение неоднозначно — вопрос остаётся открытым).
_VALUE, _EMPTY, _REFUSED = "value", "empty", "refused"

#: Единицы каталога 0.5.0, у которых все параметры неотрицательны
#: (контракт П0 §2.2, п. 8 плана: домен по умолчанию `nonneg`).
_NONNEG_UNITS = ("duration_minutes", "count", "time_of_day_minutes",
                 "credit_hours", "hours_per_credit_hour")


def default_domain(unit) -> dict | None:
    """Домен параметра по единице каталога 0.5.0, пока запись параметра
    каталога 0.6.0 не несёт поле `domain`: числовые единицы —
    неотрицательные ({"min": 0}); обозначения домена не имеют (None).
    Применяет вызывающий (apply_param_answer, _reapply_values), не
    parse_value: без домена значение читается со знаком, как есть."""
    return {"min": 0} if str(unit or "") in _NONNEG_UNITS else None


def _domain_words(domain: dict) -> str:
    parts = []
    if domain.get("min") is not None:
        parts.append(f"не меньше {domain['min']}")
    if domain.get("max") is not None:
        parts.append(f"не больше {domain['max']}")
    return ", ".join(parts) or "область не задана"


def _in_domain(value, domain) -> bool:
    """Значение в области домена: `min`/`max` включительно; домен без
    ключей или None — принимается как прочитано; обозначения не судятся."""
    if not domain or isinstance(value, bool) \
            or not isinstance(value, (int, float)):
        return True
    lo, hi = domain.get("min"), domain.get("max")
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


#: Предел читаемой длины числового токена — ОДИН закон для всех
#: единиц (K02-R1). Значащими считаются все записанные цифры, кроме
#: ведущих нулей целой части и хвостовых нулей дробной: эти ничего не
#: добавляют («010» = «10», «0,1000…0» = «0,1»), — а нули между
#: разделителем и первой значащей цифрой задают величину и читаются
#: («0,000…01»). Ни одна величина авиакомпании 24 цифр не требует: это
#: минуты за 10^18 лет и доли минуты до 10^-24. Предел стоит далеко от
#: любой осмысленной записи и вместе с тем ниже машинных пределов (4300
#: цифр у str(int), ~10^308 у float), поэтому непомерный токен
#: ОТКАЗЫВАЕТ как нечитаемый: не роняет исключением всю стадию
#: применения ответов (OverflowError у float, ValueError у str(int)) и
#: не вносится в посадку как 10^400 минут.
_MAX_DIGITS = 24


def _significant_digits(body: str) -> int:
    """Сколько цифр несёт токен без знака: ведущие нули целой части и
    хвостовые нули дробной не в счёт."""
    whole, _, frac = body.partition(".")
    return len(whole.lstrip("0")) + len(frac.rstrip("0"))


def _magnitude(tok: str) -> Fraction | None:
    """Точная величина числового токена без знака. Decimal хранит все
    цифры при построении, но abs и арифметика округляют по текущему
    контексту (точность 28): «1.0000000000000000000000000001» стала бы
    единицей ещё до проверки целых минут (K02-F4). Поэтому величина
    сразу переводится в Fraction, и дальше всё считается точно. Длина
    судится ДО перевода: токен длиннее _MAX_DIGITS значащих цифр
    нечитаем — отказ, а не непомерное число (K02-R1)."""
    body = tok.lstrip("+-\u2212").replace(",", ".")
    if _significant_digits(body) > _MAX_DIGITS:
        return None
    try:
        return Fraction(Decimal(body))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _group_digits(tok: str) -> int | None:
    """Целое значение ГРУППЫ ЦИФР записи (минутная часть составной
    записи «10 ч 30 мин», часы и минуты записи ЧЧ:ММ) — по ТОМУ ЖЕ
    закону длины, что у токена числа: больше _MAX_DIGITS значащих цифр
    — группа нечитаема (None), отказ разбора (K02-R1/T1).

    Закон один и помощник один, потому что исключение посреди стадии
    применения ответов роняет не один ответ, а всю стадию: до этой
    правки минутная часть переводилась голым int() и «10 ч <4301 ноль>
    мин» роняло ValueError («Exceeds the limit (4300 digits)»), хотя тот
    же по длине токен числа честно отказывал. Ведущие нули величины не
    добавляют, поэтому снимаются ДО перевода: int() на строке длиннее
    4300 цифр сам по себе — исключение."""
    if _significant_digits(tok) > _MAX_DIGITS:
        return None
    return int(tok.lstrip("0") or "0")


def _is_negative(tok: str) -> bool:
    """Знак — часть лексического токена и судится по нему, а не по
    `num < 0`: «-0 ч 30 мин» отрицателен, хотя его числовая часть — ноль
    (K02-F3); знак относится ко всей составленной величине."""
    return tok[:1] in ("-", "\u2212")


def _is_whole(v: Fraction) -> bool:
    return v.denominator == 1


_DIGIT_RE = re.compile(r"\d")


#: Числовая запись предложения: цифры вместе со всем, что приклеено к
#: ним вплотную, — буквами, цифрами, разделителями и знаком-склейкой
#: (_GLUE) между ними: «600», «4.2», «10:00:30», «10:00x», «01.10.2026»,
#: «7-ми», «75-ти», «10-12», «7+», «7–суток». Буква, отделённая
#: пробелом, в запись не входит: ссылка «п. 4.2» даёт запись «4.2»,
#: «75-ти минут» — запись «75-ти».
#: Запись — единица разговора с авиакомпанией: её называет заметка
#: отказа, когда цифры остались вне прочитанного значения (K02-R3).
#: Знак-склейка входит в запись ЦЕЛИКОМ — и когда за ним есть
#: продолжение («7-ми»), и когда его нет («7+», «7-»): иначе заметка
#: называла бы „7“ на ответ, где написано «7-ми», и отвечающий не
#: увидел бы причины отказа (четвёртое неавторское ревью, N1).
_RECORD_RE = re.compile(
    r"\d[^\W_]*(?:[.,:" + _GLUE + r"][^\W_]+)*[" + _GLUE + r"]?")


def _covered_mask(s: str, spans) -> bytearray:
    """Отметка охвата предложения ПО ПОЗИЦИЯМ: единица там, где символ
    лежит внутри прочитанного токена. Отметка считается ОДИН раз на
    предложение, и по ней судят оба закона охвата — и разбор
    (_digits_covered), и заметка (_stray_digit_records): каждая цифра
    предложения проверяется по своей позиции, за один проход, а не
    перебором границ. Линейны здесь ИМЕННО эти два прохода по
    предложению — не «разбор ответа» вообще: сколько предложений
    прочтёт parse_value, решает его собственный закон (он
    останавливается на первом, где есть значение или отказ).

    Раньше каждая числовая запись перебирала все цифры предложения, а
    каждая цифра — все границы: ответ «3 » + «1 »×8000 (16 КБ, обычная
    ячейка xlsx) считался 22 секунды при разборе и ещё 22 при заметке.
    Перебор «запись × цифра × граница» — квадрат, а закон тут простой:
    цифра либо покрыта, либо нет."""
    mask = bytearray(len(s))
    for a, b in spans:
        if b > a:
            mask[a:b] = b"\x01" * (b - a)
    return mask


def _stray_digit_records(s: str, spans) -> list[str]:
    """Числовые записи предложения, чьи цифры остались ВНЕ прочитанных
    токенов. Успех отдельных совпадений регулярок — ещё не охват всего
    числового выражения: нечитаемая или неподдерживаемая запись рядом с
    читаемой («10:900 (600 минут)», «10:00:30 (600 минут)», «10:00x
    (600 минут)», ссылка «п.4.2», обозначение «A320», дата) — отказ, а
    не молчаливый пропуск (K02-F2). Секунды и прочие необъявленные
    формы не поддерживаются — они отказывают.

    Повтор записи узнаётся МНОЖЕСТВОМ, а не перебором уже названного
    списка (четвёртое неавторское ревью, N2): «rec not in out» на
    списке — квадрат по числу РАЗНЫХ непрочитанных записей, и ответ из
    32 000 разных чисел (180 КБ) стоил 5,1 с на одной только заметке,
    хотя сам разбор того же ответа — доли секунды. Множество `seen`
    равно множеству названного `out`, поэтому порядок (в каком
    встретились) и состав прежние — меняется только цена."""
    mask = _covered_mask(s, spans)
    out: list[str] = []
    seen: set[str] = set()
    for mm in _RECORD_RE.finditer(s):
        a0 = mm.start()
        rec = mm.group()
        if rec in seen:
            continue
        if any(not mask[a0 + d.start()] for d in _DIGIT_RE.finditer(rec)):
            seen.add(rec)
            out.append(rec)
    return out


#: Сколько непрочитанных записей заметка называет поимённо. Заметка —
#: сообщение человеку, а не выгрузка: ответ из 4000 разных чисел давал
#: заметку в 47 КБ, которую никто не прочтёт и которая ложится в свод,
#: в журнал следствий и в выгрузки (четвёртое неавторское ревью, N2).
#: Остальные записи считаются: «и ещё 3988».
_NOTE_RECORDS_MAX = 12

#: Предел ДОСЛОВНОЙ выдержки из ответа в заметке — один для всех
#: выдержек модуля: непрочитанной записи, чужого слова единицы, номера
#: строки анкеты. Заметка есть сообщение человеку, а не выгрузка, и
#: ложится в свод, в журнал следствий и в выгрузки (четвёртое ревью,
#: N2), — поэтому ответ «⟨5000 цифр⟩. Это норма» больше не даёт заметку
#: в 5235 знаков: номер обрезается с многоточием (девятое ревью,
#: остаток). Предел стоит далеко от любой осмысленной выдержки: строк
#: анкеты, слов единицы и числовых записей такой длины не бывает.
_NOTE_QUOTE_MAX = 40


def _note_quote(text) -> str:
    """Дословная выдержка из ответа для заметки, обрезанная по
    _NOTE_QUOTE_MAX с многоточием."""
    s = str(text)
    return s if len(s) <= _NOTE_QUOTE_MAX else s[:_NOTE_QUOTE_MAX] + "…"


def _records_words(records: list[str]) -> str:
    """Перечень непрочитанных записей для заметки: первые
    _NOTE_RECORDS_MAX поимённо, остальные — счётом."""
    head = ", ".join(f"„{_note_quote(x)}“"
                     for x in records[:_NOTE_RECORDS_MAX])
    rest = len(records) - _NOTE_RECORDS_MAX
    return head + (f" и ещё {rest}" if rest > 0 else "")


def _digits_covered(s: str, spans) -> bool:
    """Каждая цифра предложения лежит внутри прочитанного токена — ни
    одна числовая запись не осталась непрочитанной."""
    mask = _covered_mask(s, spans)
    return all(mask[d.start()] for d in _DIGIT_RE.finditer(s))


#: Знак справочника — латинская буква или цифра. Кириллическая буква
#: знаком справочника не является: обозначения каталога 0.5.0 пишутся
#: латиницей и цифрами (_CODE_RE).
_CODE_SIGN_RE = re.compile(r"[A-Za-z0-9]")
#: Знаки, СКЛЕИВАЮЩИЕ знаки справочника в ОДНУ запись: точка, двоеточие,
#: точка с запятой, косые, вертикальная черта, плюс, звёздочка, знак
#: равенства, амперсанд, тильда и ЛЮБОЕ тире (_DASHES). Запятая, скобка
#: и пробел запись НЕ склеивают — ими перечень разделяют, и «A320,B738»
#: остаётся двумя записями.
_CODE_GLUE = ".:;/" + chr(92) + "|+*=&~" + _DASHES
#: ТИРЕ СОЕДИНЯЮЩЕЕ (дефис) — в отличие от разделяющего. Набор не
#: выписан рукой, а СНЯТ С ЮНИКОДА по имени знака: дефис зовётся
#: HYPHEN (HYPHEN-MINUS, NON-BREAKING HYPHEN, SOFT HYPHEN, ARMENIAN
#: HYPHEN, YEZIDI HYPHENATION MARK…), разделитель — DASH, MINUS SIGN,
#: MAQAF, HORIZONTAL BAR. Это ровно та типографская разница, на которую
#: модуль уже опирается вслух: «дефис ВПЛОТНУЮ соединяет запись
#: («10-12 часов», «A320-200»), а не вводит перечень» (_LIST_LEAD_RE).
#: Тот же приём, что у сторожа разряда Pd: правка рукой и новая версия
#: Юникода щели не оставят.
#:
#: ЗАВИСИМОСТЬ, КОТОРУЮ ДЕРЖИТ ПРОБА, А НЕ КЛАУЗА: всякое тире, которое
#: ГЛОТАЕТ САМ ЧИТАТЕЛЬ обозначений (_CODE_TOKEN_RE), обязано быть
#: соединяющим — за таким тире усечённая часть остаётся читаемым
#: обозначением («Ту-154» → «-154»), и разорвать на нём запись значило
#: бы снова открыть дыру Б3. Сегодня это так само собой: читатель
#: глотает ровно ASCII-дефис, а он зовётся HYPHEN-MINUS. Условие в код
#: не вписано — оно было бы мёртвым, — а стоит ПРОБОЙ (K02-13-D2):
#: расширится _CODE_TOKEN_RE — набор покраснеет, и решать будет автор.
_JOIN_DASHES = "".join(
    d for d in _DASHES if "HYPHEN" in _unicodedata.name(d, ""))


def _is_code(tok: str) -> bool:
    """Токен — ОБОЗНАЧЕНИЕ справочника (_CODE_RE). Один закон — одна
    функция: по нему судит и охват (_unread_code_records), и разбор
    перечня (_parse_sentence), поэтому «обозначение» в одиночку и
    «обозначение» в перечне — одно и то же."""
    return bool(_CODE_RE.match(tok))


def _code_records(s: str) -> list:
    """Границы ЗАПИСЕЙ предложения, которые могли бы быть обозначением:
    прогоны буквенно-цифровых знаков (любого алфавита), несущие хотя бы
    один знак справочника и склеенные знаками _CODE_GLUE.

    Прогон из одних кириллических букв записью САМ ПО СЕБЕ не
    считается — это СЛОВО. Сам закон охвата слов не судит; отдельный
    code_context_problem теперь проверяет непрочитанные слова ответа
    («Типы: A320 и B738» остаётся объявленной нейтральной формой). Прогон,
    несущий цифру или латинскую букву, судится: «А320» с кириллической
    «А» — одна запись, «4.2» — одна запись, «n/a» — одна запись.

    СЛОВО, ПРИКЛЕЕННОЕ К ЗАПИСИ ЧЕРЕЗ ТИРЕ, — ЧАСТЬ ЗАПИСИ
    (двенадцатое неавторское ревью, находка Б3 — БЛОКИРУЮЩАЯ).
    Одиннадцатый круг закрыл кириллическую букву ВПЛОТНУЮ к цифрам
    («А320» → отказ), а та же буква ЧЕРЕЗ ДЕФИС проходила мимо: прогон
    без знака справочника пропускался ДО склейки, «Ту» отваливалось, и
    «Ту-154» уезжало в посадку правила-носителя обозначением «-154»
    («Ил-96» → «-96», «Боинг-737» → «-737», «МС-21» → «-21», «Як-42» →
    «-42», «Типы: A320, B738 и Ту-154» → «-154, A320, B738») со
    статусом «применён» и снятой блокировкой: в выпускаемую постановку
    входил тип судна, которого авиакомпания не называла (принципы 2, 3
    и 4 ВМП). Теперь прогон ЛЮБОГО алфавита входит в запись, если
    примыкает к ней через ТИРЕ, и запись «Ту-154» отказывает целиком —
    заметка называет её ДОСЛОВНО, а не куском.

    ГРАНИЦА названа вслух и узка: прогон без знака справочника
    склеивается ТОЛЬКО тире (_DASHES), а не всем набором _CODE_GLUE.
    Иначе двоеточие склеило бы вводный оборот с перечнем, и «Типы:A320»
    (без пробела) стало бы отказом — законный ответ, который читается
    как прежде. Знаки справочника между собой склеиваются всем набором,
    как и раньше («4.2», «n/a», «A320-200»).

    ВВОДНЫЙ ОБОРОТ ЧЕРЕЗ ТИРЕ СКЛЕЙКОЙ НЕ СЪЕДАЕТСЯ (тринадцатое
    неавторское ревью, находка С-2). У ДВОЕТОЧИЯ граница была названа
    вслух и защищена пробой, у ТИРЕ — нет, хотя оборот там тот же:
    «Тип судна—A320» давало A320, а стало отказом (в запись попадало
    «судна—A320»); так же «Типы—A320, B738», «Тип—A320», «Флот—A320,
    B738», «Тип судна–A320». Форма с пробелами («Тип судна — A320»)
    работала и работает: пробел склейку рвёт и так.

    ЗАКОН: ДЕФИС СОЕДИНЯЕТ, ТИРЕ РАЗДЕЛЯЕТ — та же типографская
    разница, на которую модуль уже опирается вслух у разделителя
    перечня («дефис ВПЛОТНУЮ соединяет запись, а не вводит перечень»,
    _LIST_LEAD_RE). Разбор знаков снят с ЮНИКОДА по имени, а не выписан
    рукой (_JOIN_DASHES). ДЕФИС склеивает слово с записью ВСЕГДА:
    «Ту-154», «Ил-96», «Боинг-737», «МС-21», «Як-42» отказывают, как и
    после Б3, а «Тип судна-A320» отказывает тоже — по объявленному
    закону дефис вплотную запись СОЕДИНЯЕТ (до 688f8a35 этот же ответ
    давал обозначение «-A320», которого авиакомпания не называла, и
    возвращать его нельзя). РАЗДЕЛЯЮЩЕЕ тире само по себе рубеж: за ним
    прогон начинается заново. Склейка нужна там лишь затем, чтобы не
    осталась голая ЦИФРОВАЯ хвостовая часть («Ту—154» → «154»), —
    поэтому оно склеивает всё, КРОМЕ прогона, открывающегося ЛАТИНСКОЙ
    буквой. Латиницей пишут обозначение справочника, и оно полно само
    по себе («A320», «B738»); хвост кириллической основы латиницей не
    открывается НИ РАЗУ — «Ту-154», «Ил-96», «Боинг-737», «МС-21»,
    «Як-42» все кончаются цифрами.

    ОСТАТКИ НАЗВАНЫ. «Флот—737» (оборот + чисто цифровое обозначение)
    отказывает — знаков, по которым его отличить от «Ту—154», нет;
    исход fail-closed, вопрос остаётся открытым. «Тип судна-A320»
    (оборот через ДЕФИС) отказывает — см. закон выше.

    ОБРАТНЫЙ ОСТАТОК — НЕ ОДИН ВХОД, А КЛАСС, и он в сторону fail-open:
    разделяющее тире съедает ЛЮБОЙ кириллический оборот вплотную, в том
    числе ОТРИЦАЮЩИЙ, — «Ту—A320», «Кроме—A320», «Не—A320», «Нет—A320»
    читаются как «A320». Прежняя запись («обратный остаток ОДИН»)
    называла класс одним входом и была неверна.

    КЛАСС ЭТОТ НЕ НОВ. С пробелом и с двоеточием те же обороты читались
    так и ДО правки: на b844ecb7 «Не A320», «Кроме A320», «Нет A320»,
    «Не: A320» дают «A320» — слово без знака справочника записью не
    считается, и отрицание в нём код не читает вовсе (это не догадка о
    смысле, а отказ его строить). С-2 добавила к пробелу и двоеточию
    ТИРЕ, то есть расширила давний класс на ещё одну запись, а не
    завела новый. Отличить «Не—A320» от «Тип судна—A320» знаками
    нечем — нужен словарь оборотов, и он тоже за каталогом 0.6.0. Через
    ДЕФИС те же входы («Ту-A320», «Кроме-A320») отказывают — закрыта
    именно та половина, которую набирают на деле."""
    out: list = []
    prev_sign = False
    for mm in _WORDISH_RE.finditer(s):
        a, b = mm.span()
        sign = bool(_CODE_SIGN_RE.search(mm.group()))
        # Тире склеивает ЛЮБОЙ прогон; прочие знаки _CODE_GLUE — только
        # два прогона со знаками справочника (Б3). Прогон, открытый
        # ЛАТИНСКОЙ буквой, к СЛОВУ приклеивает лишь то тире, которое
        # глотает сам читатель (С-2). Условия «prev_sign нет» во второй
        # ветви НЕТ, и оно было бы мёртвым: латинская буква — знак
        # справочника (_CODE_SIGN_RE), поэтому у прогона, ею открытого,
        # sign всегда истинно, и при prev_sign первая ветвь забирает
        # случай раньше. Мутация «вписать условие» исхода не меняет.
        if sign and prev_sign:
            glue = _CODE_GLUE
        elif _LAT_LETTER_RE.match(mm.group()):
            glue = _JOIN_DASHES
        else:
            glue = _DASHES
        prev_sign = sign
        if out and a > out[-1][1] \
                and all(ch in glue for ch in s[out[-1][1]:a]):
            out[-1] = (out[-1][0], b, out[-1][2] or sign)
        else:
            out.append((a, b, sign))
    # Запись без знака справочника — просто слово: охват его не судит.
    return [(a, b) for a, b, sign in out if sign]


def _unread_code_records(s: str) -> list:
    """Записи предложения, чьи знаки остались ВНЕ прочитанных
    обозначений, — причина отказа, которую надо назвать авиакомпании.

    ЗАКОН ОХВАТА У ОБОЗНАЧЕНИЙ (одиннадцатое неавторское ревью, находка
    5). У числовых единиц каждая цифра предложения обязана лежать
    внутри прочитанного токена (_digits_covered), а у обозначений
    такого закона не было вовсе: `_CODE_TOKEN_RE` выбирал из
    предложения ВСЁ, что похоже на знаки справочника, и прочитанное
    частью уезжало в посадку правила-носителя без единого слова.
    «A320, B738 — см. п. 4.2» давало перечень «2, 4, A320, B738» —
    цифры ссылки становились типами судов; «Как в п. 4.2» — перечень
    «2, 4»; «n/a» — перечень «a, n»; «А320» с кириллической «А» —
    обозначение «320»; «A320' + chr(0x2010) + '200» с юникодным тире — «200, A320»
    вместо одного «A320-200». В постановку входили обозначения,
    которых авиакомпания не называла, со статусом «применён» и снятой
    блокировкой — fail-open с НЕВЕРНЫМ значением (принципы 3 и 4 ВМП).

    Теперь закон один: знак ЗАПИСИ, не вошедший в прочитанное
    обозначение, — отказ. Отметка охвата считается тем же
    _covered_mask, что у цифр, за один проход по предложению.

    Охват считается по ОБОЗНАЧЕНИЯМ, а не по всякому прогону знаков
    справочника: токен, обозначением не являющийся (длиннее 16 знаков —
    см. _CODE_RE), покрытия не даёт и называется заметкой сам. Прежде
    единица code такой токен отвергала, а code_list — вносила в
    посадку: один и тот же токен был обозначением в перечне и не был им
    в одиночку."""
    spans = [mm.span() for mm in _CODE_TOKEN_RE.finditer(s)
             if _is_code(mm.group())]
    mask = _covered_mask(s, spans)
    out: list = []
    seen: set = set()
    for a, b in _code_records(s):
        rec = s[a:b]
        if rec in seen:
            continue
        if any(not mask[i] for i in range(a, b)):
            seen.add(rec)
            out.append(rec)
    return out


def _ambiguous_code_phrase(body: str) -> str:
    """A word next to another token is not a proven list boundary.

    Without a registry oracle, 'Airbus A320' / 'Base SVO' may be a
    designation plus context, not two codes. Do not guess a manufacturer
    or strip its name. Require explicit comma/semicolon/conjunction
    boundaries for word-like values. Compact non-word identifiers keep
    the existing whitespace-list syntax ('A320 B738', '320 737').
    """
    for part in _CODE_LIST_BOUNDARY_RE.split(body):
        tokens = _CODE_TOKEN_RE.findall(part)
        if len(tokens) > 1 and any(_CODE_WORD_RE.fullmatch(t) for t in tokens):
            return part.strip()
    return ""


def code_context_problem(raw, unit) -> str:
    """Unread context around codes, across the whole answer.

    The old coverage law only counted designation signs: Cyrillic words
    disappeared, so 'Не A320' became A320. Only declared neutral headers,
    the list conjunction and punctuation may disappear now. Other words
    (negative, conditional, explanatory or unknown) are NOT interpreted.
    A single Latin token still belongs to the declared code alphabet.
    Word-like tokens need explicit list boundaries beside other tokens;
    this does not claim semantic validation against an aircraft registry.
    """
    if str(unit or "") not in m._cat("CODE_UNITS"):
        return ""
    for sent in _sentences(raw):
        if sent.casefold() in {"конец", "смотрите ниже"}:
            continue                # declared standalone navigation markers
        body = _CODE_CONTEXT_LEAD_RE.sub("", sent, count=1)
        rest = _CODE_TOKEN_RE.sub(" ", body)
        rest = _CODE_CONTEXT_AND_RE.sub(" ", rest)
        if not _CODE_CONTEXT_PUNCT_RE.fullmatch(rest):
            word = _WORDISH_RE.search(rest)
            return word.group() if word else rest.strip()
        phrase = _ambiguous_code_phrase(body)
        if phrase:
            return phrase
    return ""


def unread_code_records(raw, unit) -> list:
    """Непрочитанные записи обозначений ответа (см. _unread_code_records)
    — по тому предложению, на котором остановился parse_value. Пусто у
    единиц, значение которых обозначением не является."""
    unit = str(unit or "")
    if unit not in m._cat("CODE_UNITS"):
        return []
    for sent in _sentences(raw):
        stray = _unread_code_records(sent)
        if stray or _has_payload(sent, unit):
            return stray
    return []


def _hhmm_tokens(s: str, signed: bool) -> list | None:
    """Токены ЧЧ:ММ предложения — (минуты, границы совпадения); минуты
    вне 0–59 (и знак там, где он не допустим) — отказ (None): «10:90» не
    читается ни как 690, ни как «10» и «90».

    Цифры групп переводит _group_digits — тот же закон длины, что у
    токена числа. Сегодня форма записи держит их короткими (часы —
    не больше трёх цифр, минуты — ровно две), и помощник тут не
    отказывает ни разу; он стоит здесь, чтобы закон был ОДИН на все
    группы цифр модуля и расширение формы не роняло стадию (K02-T1)."""
    out: list[tuple[Fraction, tuple[int, int]]] = []
    for mm in _HHMM_RE.finditer(s):
        minutes = _group_digits(mm.group("m"))
        hours = _group_digits(mm.group("h"))
        if minutes is None or hours is None:
            return None
        if minutes > 59 or (mm.group("sign") and not signed):
            return None
        val = Fraction(hours * 60 + minutes)
        out.append((-val if _is_negative(mm.group("sign")) else val,
                    mm.span()))
    return out


def _clock_values(s: str, rejected_words: list[str] | None = None) -> list | None:
    """Read clock tokens without silently discarding their attached words.

    HH:MM already supplies the unit. Unlike duration, no following noun is
    a scale. Preserve the existing relation/repetition/universal grammar,
    but do not infer a unit from an unknown suffix. Subject-aware callers
    pass their catalogue-masked view; the original answer remains intact.
    This local boundary does not parse arbitrary legacy prefixes/prose.
    """
    from .answer_subjects import _RELATION_RE
    toks = _hhmm_tokens(s, signed=False)
    if toks is None or _QTY_RE.search(s) or any(v >= 24 * 60 for v, _ in toks):
        return None
    punctuation = " \t\r\n.,;:()[]!?\"'«»„“”‘’" + _DASHES + _DOT_KEEP
    for index, (_, (_, end)) in enumerate(toks):
        next_start = toks[index + 1][1][0] if index + 1 < len(toks) else len(s)
        tail = s[end:next_start]
        spans = [hit.span() for hit in _RELATION_RE.finditer(tail)]
        spans += [hit.span() for hit in _UNIVERSAL_SCOPE_RE.finditer(tail)]
        if index + 1 < len(toks):
            spans += [hit.span() for hit in re.finditer(r"(?<!\w)и(?!\w)", tail, re.I)]
        covered = _covered_mask(tail, spans)
        rest = "".join(" " if covered[i] else char for i, char in enumerate(tail))
        if rest.strip(punctuation):
            if rejected_words is not None:
                rejected_words.append(tail.strip(punctuation))
            return None
    return toks


def _duration_values(s: str, rejected_words: list[str] | None = None) -> list | None:
    """Все значения длительности предложения в минутах — (Fraction,
    границы токена) — либо None при нечитаемом токене: число без
    единицы (предмет не назван), нецелые минуты после точного
    приведения («2,25 минуты», длинная дробь «0,0999…9 часа»), ЧЧ:ММ с
    минутами вне 0–59, дробные часы или минуты вне 0–59 в составной
    записи («1,5 ч 30 мин», «10 ч 90 мин» — как «10:90»), непомерная
    минутная часть («10 ч <4301 ноль> мин» — нечитаема, как и токен из
    стольких же цифр). Знак — у всей величины: «-0 ч 30 мин» → −30."""
    vals = _hhmm_tokens(s, signed=True)
    if vals is None:
        return None
    for mm in _QTY_RE.finditer(s):
        mag = _magnitude(mm.group("num"))
        if mag is None:
            return None
        if mm.group("h"):
            if mm.group("hm"):
                hm = _group_digits(mm.group("hm"))
                if hm is None or not _is_whole(mag) or hm > 59:
                    return None
                mag = mag * 60 + hm
            else:
                mag = mag * 60
        elif mm.group("mi"):
            pass
        elif mm.group("d"):
            mag = mag * 24 * 60
        else:
            if rejected_words is not None:
                next_quantity = _QTY_RE.search(s, mm.end())
                end = next_quantity.start() if next_quantity else len(s)
                rejected_words.append(_quantity_word_tail(s, mm.end(), end))
            return None                 # число без единицы длительности
        if not _is_whole(mag):
            return None                 # нецелые минуты не округляются
        vals.append((-mag if _is_negative(mm.group("num")) else mag,
                     mm.span()))
    return vals


#: Единицы каталога, у которых значение — ЧИСЛО ЧАСОВ: рядом с числом
#: вправе стоять каноническое слово часа («75 часов» → 75).
_HOUR_UNITS = ("credit_hours", "hours_per_credit_hour")

# CDPM-290: explicit hour phrases, not arbitrary words discarded after a
# number. Keep aviation's block/flight/credit hours without accepting
# "75 сегментов" as 75 hours. This does not define the subject of count.
_QUALIFIED_HOUR_RE = re.compile(
    r"\s*(?:(?:блок|кредит)[" + re.escape(_DASHES) + r"]\s*|"
    r"(?:блочн|л[её]тн|кредитн)(?:ый|ых|ые|ого|ом|ыми)\s+)"
    + _HOUR_W, re.IGNORECASE)


def _quantity_word_tail(s: str, start: int, end: int) -> str:
    """Verbatim unread words, without assigning an unknown noun a unit.

    Called only at an actual numeric-reader refusal. Parenthesis/punctuation
    outside the words are not the reason; spelling and inner punctuation stay.
    """
    fragment = s[start:end]
    words = list(re.finditer(r"[^\W\d_]+", fragment))
    return fragment[words[0].start():words[-1].end()] if words else ""


def _number_values(s: str, hour_word: bool,
                   rejected_words: list[str] | None = None) -> list | None:
    """Все числовые токены предложения — (Fraction, границы токена);
    запись ЧЧ:ММ рядом с числом — чужая запись, отказ.

    Слово единицы рядом с числом судится ПО ЕДИНИЦЕ ПАРАМЕТРА (тот же
    закон, что у длительности, и тот же, что у «ЧЧ:ММ рядом с числом»):
    каноническое слово единицы читается (hour_word — слово часа у часов
    кредита), чужое — отказ. Иначе слово молча отбрасывалось бы вместе
    со своим смыслом: «75 минут» вносилось бы как 75 часов кредита,
    «75 ч 30 мин» — как 75, «72 часа» — как счёт 72 (K02-R2)."""
    if _HHMM_RE.search(s):
        return None
    vals: list[tuple[Fraction, tuple[int, int]]] = []
    matches = list(_QTY_RE.finditer(s))
    for index, mm in enumerate(matches):
        if mm.group("mi") or mm.group("d") or mm.group("hm") \
                or (mm.group("h") and not hour_word):
            return None                 # чужое слово единицы при числе
        end = mm.end()
        if hour_word and not mm.group("h"):
            qualified = _QUALIFIED_HOUR_RE.match(s, end)
            if qualified:
                end = qualified.end()
            else:
                # A bare numeric answer is lawful in the question's unit.
                # A named object is not bare: do not silently drop it.
                # Stop at the next quantity so agreeing numeric repetitions
                # such as "75 (75 часов)" keep their existing meaning.
                next_start = (matches[index + 1].start()
                              if index + 1 < len(matches) else len(s))
                tail = s[end:next_start]
                # The existing scope contract explicitly permits an
                # unqualified universal domain. Do not reinterpret it as an
                # unknown object merely because the hour answer is bare.
                # Full match is essential: restricted groups still refuse.
                universal = _UNIVERSAL_SCOPE_RE.fullmatch(tail.strip())
                if not universal and re.search(r"[^\W\d_]", tail):
                    if rejected_words is not None:
                        rejected_words.append(_quantity_word_tail(s, end, next_start))
                    return None
        mag = _magnitude(mm.group("num"))
        if mag is None:
            return None
        vals.append((-mag if _is_negative(mm.group("num")) else mag,
                     (mm.start(), end)))
    return vals


#: Единицы, у которых слово величины при числе судится по единице
#: параметра (K02-R2 и одиннадцатое ревью, находка 1): ВСЕ числовые.
#: Прежде судились только счёт и часы кредита, а у длительности
#: считалось, что «чужого слова там не бывает»; на деле там не бывает
#: чужого слова ТРЁХ известных родов, а «2 недели» и «50 %» проходили
#: мимо закона — у длительности молчком (число без единицы), у счёта —
#: ложным значением.
_WORD_JUDGED_UNITS = ("count", "duration_minutes",
                      "time_of_day_minutes") + _HOUR_UNITS
#: Имя единицы в заметке отказа (родительный падеж): «слово „минут“ у
#: часов кредита не читается как единица».
_UNIT_NAMES = {
    "count": "счётного параметра",
    "credit_hours": "часов кредита",
    "hours_per_credit_hour": "часов на час кредита",
    "duration_minutes": "длительности",
    "time_of_day_minutes": "времени суток",
}


def _as_number(v: Fraction):
    """Значение Python из точной величины либо None, если перевод не
    удался. Непомерная величина отказывает РАЗБОРОМ, а не исключением
    посреди применения ответов (K02-R1): предел _MAX_DIGITS такую
    величину сюда не пускает, это второй рубеж того же закона."""
    try:
        return int(v) if _is_whole(v) else float(v)
    except (OverflowError, ValueError):
        return None


_BARE_NUMBER_NEGATION_RE = re.compile(
    r'(?<!\w)не(?!\w)[\s«"“”\(\[]+(?P<number>[-+\u2212]?[0-9])',
    re.IGNORECASE)


def negated_value_problem(raw, unit) -> str:
    """Refuse explicit 'не <number>', not a relation such as 'не более'.

    This bounded guard neither infers the desired replacement nor parses
    general negation. Callers with catalogue subjects pass their masked view;
    the exact offending fragment is kept for the explanation.
    """
    if str(unit or "") not in _NUMERIC_UNITS or not isinstance(raw, str):
        return ""
    hit = _BARE_NUMBER_NEGATION_RE.search(raw)
    if hit is None:
        return ""
    start = hit.start("number")
    tokens = [pattern.match(raw, start) for pattern in (_QTY_RE, _HHMM_RE)]
    end = max([hit.end()] + [token.end() for token in tokens if token])
    closing = re.match(r'[»"”\)\]]+', raw[end:])
    if closing:
        end += closing.end()
    return (f"содержит отрицание значения «{raw[hit.start():end]}»; "
            "оставьте одно желаемое значение с его единицей, без отвергаемого значения")


_APPROXIMATE_VALUE_RE = re.compile(
    r"(?<!\w)(?:примерно|приблизительно|около|порядка|желательно)(?!\w)",
    re.IGNORECASE)


def approximate_value_problem(raw, unit) -> str:
    """A scalar has no place to retain approximation or a preference.

    Catalogue-aware callers mask subject spans first; a declared name is
    data, not a modifier. Never turn the requested preference into a fact.
    """
    if str(unit or "") not in _NUMERIC_UNITS or not isinstance(raw, str):
        return ""
    hit = _APPROXIMATE_VALUE_RE.search(raw)
    if hit is None:
        return ""
    return (f"предмет: «{hit.group()}» не задаёт точное значение; "
            "уточните точное значение и единицу либо отдельно сформулируйте предпочтение")


def value_context_problem(raw, unit, *, _sentences_cache=None, _period_view=None) -> str:
    """Whole-text restrictions shared by scalar parsing and answer citations.

    A literal substring is not permission to discard a scope, period or
    decline elsewhere in the answer. This is the existing bounded grammar,
    not a subject/negation parser. Typed conditional values need a separate
    representation; this reader may only justify an unqualified scalar.
    """
    unit = str(unit or "")
    if mixed_script_word(raw):
        return "слово записано смешанными алфавитами"
    sentences = _sentences(raw) if _sentences_cache is None else _sentences_cache
    if _decline_parts_of(sentences) is not None:
        # A decline is a decision (or a conditional, unresolved decision),
        # never a scalar. Direct parse_value callers need this guard too.
        return "содержит отказ от требования, который нельзя заменить значением"
    if line_number_record(raw):
        return "содержит номер строки анкеты, не являющийся значением"
    negation = negated_value_problem(raw, unit)
    if negation:
        return negation
    approximation = approximate_value_problem(raw, unit)
    if approximation:
        return approximation
    period = period_qualifier(raw if _period_view is None else _period_view, unit)
    if period:
        return f"содержит необработанный период «{period}»"
    scope = applicability_qualifier(raw, unit)
    if scope:
        return f"содержит необработанную оговорку применимости «{scope}»"
    return code_context_problem(raw, unit)


def parse_value(raw: str, unit, domain=None) -> tuple | None:
    """(значение, цитата) по единице параметра каталога либо None —
    вопрос остаётся открытым (K02: значение равно тому, что прочитал бы
    человек, либо отказ; догадок нет).

    CDPM-251: явная оговорка применимости во всём ответе даёт отказ
    (applicability_qualifier), пока её нельзя сохранить условием.
    Отдельное предложение о продлении после явно названного базового
    предела судится собственной ограниченной грамматикой: возвращается
    базовый предел, а не продление. Полным разбором условий это не является.

    Значение читается ПО ПРЕДЛОЖЕНИЯМ ответа: первое предложение,
    несущее ровно одно значение единицы, даёт значение и становится
    цитатой (ответ авиакомпании вправе пояснять дальше и называть
    соседние величины: «дома — 720, вне базы — 600»). Две записи одного
    значения в предложении согласны («10:00 (600 минут)» → 600, «10 ч.
    (600 минут)» → 600); разные значения — неоднозначность («12:00 (600
    минут)», «10 часов (500 минут)», «12 ч. (600 минут)», «600 мин. = 12
    часов») — None; предложение без значения пропускается; ответ без
    значения — None. Границы предложения не рвут одну запись значения
    (_sentences): точка сокращения единицы — часть слова («ч.», «мин.»,
    «сут.», «дн.»; «1 дн.» читается как 1440), а точка перед тем, что
    не способно начать предложение — цифрой, скобкой, знаком равенства,
    строчной буквой, — не конец предложения («12 часов. (600 минут)» —
    одна противоречивая запись, отказ); конец предложения — точка перед
    прописной буквой («10 часов. Дома — 12 часов.» → 600).

    Грамматика токена (контракт П0 §2.2, 12.09.2026): целое; десятичное
    с точкой или запятой — цифры читает Decimal, считает Fraction:
    приведение к минутам точное («10,5 часов» → 630, «0,1 часа» → 6),
    нецелые минуты — отказ («2,25 минуты», длинная дробь «0,0999…9
    часа» не округляется до 6); знак — часть токена и относится ко всей
    величине («-600 минут» → -600, «-0 ч 30 мин» → -30); токен имеет
    границы (_TOKEN_L/_NUM_R: «10,5 часов» не читается как «5 часов»).
    Числовое выражение читается целиком: каждая цифра предложения
    обязана лежать в прочитанном токене, иначе отказ («10:900 (600
    минут)», «10:00:30 (600 минут)», «10:00x (600 минут)» — не 600);
    непрочитанные записи называет заметка отказа (unread_digits).
    Длина токена — часть читаемости: не больше _MAX_DIGITS значащих
    цифр, длиннее — отказ, а не 10^400 минут (K02-R1); тот же закон
    судит КАЖДУЮ группу цифр записи — минутную часть составной записи и
    группы ЧЧ:ММ, — поэтому «10 ч <4301 ноль> мин» отказывает, а не
    роняет исключением стадию применения ответов (K02-T1). Ответ — ТЕКСТ:
    не текст не даёт ни предложений, ни цитаты, ни значения (K02-R5).
    Длительность: минуты (×1), часы (×60), сутки (×1440) — слова из
    _MIN_W/_HOUR_W/_DAY_W, составная запись «10 ч 30 мин», запись ЧЧ:ММ
    с минутами 0–59 («10:90» — отказ); число без единицы у длительности
    — отказ (предмет не назван). Время суток — ЧЧ:ММ с минутами 0–59 и
    меньше 24:00. Слово единицы рядом с числом судится по единице
    параметра (K02-R2 и одиннадцатое ревью, находка 1): счёт — целое
    без слова величины при нём («72 часа», «3 суток», «2 недели»,
    «50 %» — чужая запись, отказ); часы кредита и часы на час кредита —
    число (целое или дробное) со словом часа или без него («75 часов» →
    75, «75» → 75), чужое слово — отказ («75 минут», «75 ч 30 мин»,
    «10 %»). Словарь величин — _KIND_WORDS (время от секунды до года,
    доля словом и знаком, латинские написания), приведение к единице
    параметра — три объявленных преобразования (минуты ×1, часы ×60,
    сутки ×1440); неделя, месяц, квартал, год и доля НЕ приводятся, а
    отказывают. Слово, записанное ДВУМЯ алфавитами сразу, не читается
    вовсе — ни как единица, ни как обозначение, ни как фраза отказа
    (mixed_script_word, находка 3); слово, набранное латиницей целиком,
    узнаётся свёрткой омоглифов только в сторону отказа. Цифра значения
    — только 0–9; иная цифра остаётся вне токена и отказывает законом
    охвата. Любое ТИРЕ Юникода (_DASHES) приклеивается к цифрам так же,
    как дефис (находка 2). Отказ по слову называет заметка
    (foreign_unit_word, K02-T2): до словаря `unit_words`/`subject_words`
    каталога 0.6.0 счётный параметр не знает, ЧТО он считает, поэтому
    «7 суток» у «окна в днях» отказывает вместо догадки по имени
    параметра; существительное при числе единицей не является — «8
    выходных дней» читается как счёт 8 (слово единицы стоит вплотную к
    числу).
    Обозначения: одно обозначение либо перечень канонически, и у них
    свой ЗАКОН ОХВАТА (одиннадцатое ревью, находка 5) — знак ЗАПИСИ, не
    вошедший в прочитанное обозначение, отказывает и называется
    заметкой («A320, B738 — см. п. 4.2» больше не даёт перечень
    «2, 4, A320, B738»); токен перечня обязан быть ОБОЗНАЧЕНИЕМ по тому
    же закону, что одиночный код (_is_code).

    Ответ С ФРАЗОЙ ОТКАЗА значения не даёт ВОВСЕ, пока отказ не
    безусловен (В-3, B1 и K-1, см. _decline_parts_of), и МЕСТО фразы в
    ответе не меняет ничего: в том же предложении — до значения («600
    минут; для SVO не применяется») и после него («Не применяется для
    сотрудников базы SVO; в остальных базах 10:00», «Не применяется,
    600 минут»); соседним предложением — до значения («Не применяется.
    В остальных базах 10:00.», «Не применяется. Для SVO — 600 минут.»)
    и после него («600 минут. Не применяется для базы SVO.», «10:00.
    Не применяется для базы SVO.», «2. Не применяется»). Ответ и
    отказывает, и называет величину — противоречие, а не значение;
    решением он тоже не является (is_declined). Иначе один и тот же
    ответ давал бы то отказ, то значение — смотря кто спросил и в
    каком ПОРЯДКЕ авиакомпания написала два предложения, — и «10:00»,
    названное для остальных баз, вносилось бы в посадку как общее
    правило. Ответ БЕЗ ЕДИНОЙ фразы отказа читается как прежде: «600
    минут, кроме SVO» — предмет грамматики условий (K03, волна 17б),
    здесь он даёт 600 по первому предложению.

    Ответ С НОМЕРОМ СТРОКИ анкеты значения не даёт ВОВСЕ — у ЛЮБОЙ
    единицы каталога и в любой письменной форме знака («1.», «1)»,
    «(1)», «[1]», «№1», «1:», «1 -»), где бы номер ни стоял: в начале
    ответа («1. A320», «1) A320, B738», «10. Это норма»), в начале
    любого его предложения («Смотрите ниже. 1. A320»), с новой строки
    текста («Типы судов:\n1) A320\n2) B738») и за вводным оборотом в
    ОДНУ строку («Типы судов: 1) A320, 2) B738», «Типы судов — 1)
    A320», «Норма: 10. Это норма»). Номер строки — не часть ответа, а
    что названо цифрой, ответ не говорит: исход fail-closed, вопрос
    остаётся открытым (см. line_number_record — там же граница закона у
    обозначений из одних цифр). Раньше номер молча становился
    значением там, где записью значения служит голое целое или токен
    справочника: «1. A320» → код «1», «1) A320, B738» → перечень
    «1, A320, B738», «10. Это норма» → счёт 10, «Типы судов:\n1)
    A320\n2) B738» → перечень «1, 2, A320, B738» — в посадку уезжало
    обозначение, которого авиакомпания не называла (принципы 3 и 4
    ВМП). У длительности и времени суток тот же ответ отказывал и
    прежде — там номер был цифрой вне числового выражения.

    `domain` — область допустимости из контракта семьи: ключи `min`/
    `max` (включительно); значение вне области — None (вопрос открыт,
    блокировки на месте). Без домена значение принимается как
    прочитано, со знаком; домен по умолчанию единиц каталога 0.5.0 даёт
    default_domain(unit) — его применяет вызывающий, не этот разбор."""
    unit = str(unit or "")
    sents = _sentences(raw)
    if value_context_problem(raw, unit, _sentences_cache=sents):
        return None         # the same whole-text guard applies to quotations
    for sent in sents:
        status, value = _parse_sentence(sent, unit)
        if status == _VALUE:
            if not _in_domain(value, domain):
                return None      # значение вне области — спросить снова
            return value, sent
        if status == _REFUSED or _has_payload(sent, unit):
            return None          # значения есть, но не одно — спросить снова
    return None


def _rule_quote_period_spans(text, unit_spans=()):
    """Unnumbered calendar/duty context, never a quantity or a unit phrase.

    Reuse the bounded period grammar, but not seconds/minutes/hours that
    would silently turn an undeclared rate into an absolute magnitude.
    Count quotations deliberately do not use this exemption. No noun or
    parameter name supplies a conversion, and no digit is masked.
    """
    small_period = re.compile(
        r"(?<!\w)(?:" + _SEC_W + "|" + _MIN_W + "|" + _HOUR_W
        + r"|секунд(?:у|е|ой)|минут(?:у|е|ой)|час(?:у|е|ом)|"
        r"ежечасно|ежеминутно|поминутно)(?!\w)", re.I)
    return [hit.span() for hit in _PERIOD_QUALIFIER_RE.finditer(text)
            if not re.search(r"\d", hit.group())
            and not small_period.search(hit.group())
            and not any(start < hit.end() and hit.start() < end
                        for start, end in unit_spans)]


def parse_rule_quote(raw, unit, domain=None, *, source=None) -> tuple | None:
    """Numeric witness in an existing rule quotation, not a company answer.

    Count clauses may name counted entities outside answer subject_words;
    their existing refusal of a period is retained. Other numeric units
    may carry bounded, unnumbered calendar/duty context, but arbitrary
    trailing nouns, applicability, negation and approximation still refuse.
    Exact pinned conversion, all digits and one value must still agree.
    Read the entire quotation, never the first convenient sentence. Callers
    separately prove the literal source span, value equality and provenance.
    This does not prove arbitrary natural-language meaning or add aliases.
    The original quotation retains its period; this does not infer or
    approve a parameter-period contract. Company parse_value is unchanged.
    """
    unit = str(unit or "")
    text = _normalized(raw)
    if not text:
        return None
    if source is None:
        source = _modern_unit_source()
    else:
        from template_catalog import current
        from template_catalog.validate import _uses_schema_070
        if not _uses_schema_070(current(source, validated=True).version()):
            source = None
    if source is not None:
        from template_catalog import units
        entry = units.unit_words(source).get(unit)
        if not isinstance(entry, dict) or entry.get("form") in units.NON_NUMERIC_FORMS:
            return parse_value(raw, unit, domain)
    elif unit not in _NUMERIC_UNITS:
        return parse_value(raw, unit, domain)
    if unit == "count" and value_context_problem(raw, unit):
        return None
    word_spans = []
    if source is not None:
        from .catalogue_numeric import tokens
        found = tokens(text, unit, source, consumed_words=word_spans,
                       count_rule_quote=unit == "count", rule_quote=unit != "count")
    else:
        periods = _rule_quote_period_spans(text) if unit != "count" else []
        mask = _covered_mask(text, periods)
        view = "".join(" " if mask[i] else char for i, char in enumerate(text))
        if _foreign_unit_at(view, unit):
            return None
        if unit == "duration_minutes":
            found = _duration_values(view)
        elif unit == "time_of_day_minutes":
            found = _clock_values(view)
        else:
            found = _number_values(view, unit in _HOUR_UNITS)
    if found is None or not _digits_covered(text, [span for _, span in found]):
        return None
    if unit != "count":
        # Mask the exact converted words only for the period guard. This
        # avoids reading a pinned ratio word through a different CURRENT
        # catalogue. All other whole-quote guards receive the original text.
        mask = _covered_mask(text, [*word_spans, *_rule_quote_period_spans(text, word_spans)])
        period_view = "".join(" " if mask[i] else char for i, char in enumerate(text))
        guard_unit = unit if unit in _NUMERIC_UNITS else "credit_hours"
        if value_context_problem(raw, guard_unit, _period_view=period_view):
            return None
    value = _one([value for value, _ in found])
    if (value is None or value < 0
            or unit in ("count", "duration_minutes", "time_of_day_minutes") and not _is_whole(value)):
        return None
    number = _as_number(value)
    if number is None or not _in_domain(number, domain):
        return None
    return number, raw


# ---------------------------------------------------------------------------
# A05-барьер: приведение значения носителя к канонической единице семьи
# ---------------------------------------------------------------------------

#: Слово единицы длительности → множитель к минутам. Перечень ТОТ ЖЕ, что
#: у грамматики ответа (_MIN_W/_HOUR_W/_DAY_W — минимальный фиксированный
#: перечень модуля, контракт П0 §12.6); собственного словаря единиц у
#: CDPM нет, словарь `unit_words` каталога 0.6.0 сменит его регрессией.
_UNIT_WORD_SCALES = (
    (re.compile(r"^" + _MIN_W + r"$", re.IGNORECASE), Decimal(1)),
    (re.compile(r"^" + _HOUR_W + r"$", re.IGNORECASE), Decimal(60)),
    (re.compile(r"^" + _DAY_W + r"$", re.IGNORECASE), Decimal(1440)),
)


def _modern_unit_source():
    """Explicit selected catalogue, never an implicit legacy fallback."""
    from . import catalog_bridge
    from template_catalog.validate import _uses_schema_070
    cat = catalog_bridge.catalog()
    return cat.path if _uses_schema_070(cat.version()) else None


def unit_scale(word_or_code, target_unit) -> Decimal | None:
    """Множитель приведения значения в единице `word_or_code` (код единицы
    каталога либо слово минимального перечня K02) к канонической
    единице `target_unit`; пустая единица — значение уже в канонической
    единице (закон носителя без единицы, контракт П0 §12.5). None —
    единица не читается или несовместима с канонической (fail-closed:
    сверка носителей невозможна, а не «считаем совпавшим»)."""
    word = " ".join(str(word_or_code or "").split()).lower()
    target = str(target_unit or "")
    source = _modern_unit_source()
    if source is not None:
        from template_catalog import units
        try:
            words = units.unit_words(source)
            entry = units._entry(target, words)
            scale, _ = units._multiplier(entry, target, word or target, words)
            return Decimal(str(scale))
        except units.UnitsError:
            return None
    if not word:
        return Decimal(1)
    if word == target:
        return Decimal(1)
    if word in m._cat("PARAM_UNITS"):
        return None                     # другой код каталога — несовместимо
    if target == "duration_minutes":
        for rx, scale in _UNIT_WORD_SCALES:
            if rx.match(word):
                return scale
    return None


#: Единица логического условия семьи (записи условий каталога): её
#: значение — логическое И ТОЛЬКО логическое. Числа и слова («1»,
#: «да», «true») к ней не приводятся: словаря слов у CDPM нет, а
#: угадывать его нельзя (контракт П0 §12.5; словарь `unit_words`
#: каталога 0.6.0 сменит этот закон регрессией).
FLAG_UNIT = "flag"


def is_structural(raw) -> bool:
    """Значение носителя — НЕ скаляр (объект, перечень, множество).
    Такое значение грамматике числа не показывают никогда: `str()` от
    объекта `{'AAA': 1}` дал бы ей «1», и два носителя разных баз
    «совпали» бы (находка В-1 неавтор-ревью A05, 12.09.2026).
    Структурные формы контракта (§3.1) читаются структурно —
    `base_map` и канон обозначений, — а не грамматикой."""
    return isinstance(raw, (dict, list, tuple, set))


def base_map(raw) -> dict | None:
    """Значение структурной формы контракта «норма по базам»
    (§3.1: у единицы `count` с уточнителем базы ЕДИНСТВЕННАЯ форма —
    `{"<код базы>": целое}`) словарём {код базы: Decimal}, либо None —
    форма другая (fail-closed, догадок нет)."""
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip():
            return None
        if isinstance(val, bool) or not isinstance(val, (int, float, Decimal)):
            return None
        base = key.strip()
        number = Decimal(str(val))
        # Count is whole and finite; trimming must not silently merge bases.
        if base in out or not number.is_finite() \
                or number != number.to_integral_value():
            return None
        out[base] = number
    return out


def base_map_value(raw) -> dict | None:
    """Read a structural map or the existing exact per-base packed format.

    The legacy grammar is TaskPackage.kt PER_BASE_RE (K-2), not prose or a
    generic numeric conversion. Duplicate bases refuse even when equal.
    The canonical input and quotation are never rewritten by this reader.
    """
    if isinstance(raw, dict):
        return base_map(raw)
    if not isinstance(raw, str) or not re.fullmatch(
            r"[A-ZА-ЯЁ0-9]+:[0-9]+(?:,[A-ZА-ЯЁ0-9]+:[0-9]+)*", raw):
        return None
    out = {}
    for part in raw.split(","):
        base, number = part.split(":")
        if base in out:
            return None
        out[base] = Decimal(number)
    return out


def base_map_quote_problems(table: dict, quote) -> list[str]:
    """Check explicit base/count pairs, not unrelated tokens in one quotation.

    Reads the packed format, labelled counts and simple table cells. Unknown
    prose without a supported pair needs clarification. This checks literal
    base/value binding, not the meaning of relations, negation or conditions.
    """
    if not isinstance(quote, str) or not quote.strip():
        return ["норма по базам без дословного следа"]
    text = " ".join(quote.split())
    problems = []
    for base, value in table.items():
        pattern = (
            r"(?<![\w-])" + re.escape(base) + r"(?![\w-])['\"»]?\s*"
            r"(?:(?i:не\s+менее|не\s+более)\s+|(?:>=|<=|[≥≤=:|])\s*|[–—]\s+)?"
            r"([+-]?\d+(?:[.,]\d+)?)(?!\w|[.,]\d)(?!\s*[-–—−/:+*×]\s*\d)(?!\s+\d)"
        )
        numbers = set()
        for match in re.finditer(pattern, text):
            token = match.group(1)
            magnitude = _magnitude(token)
            if magnitude is None:
                problems.append(f"цитата не подтверждает норму базы {base!r}: "
                                "число не читается по общей грамматике величины")
                continue
            numbers.add(-magnitude if _is_negative(token) else magnitude)
        if numbers != {value}:
            found = ", ".join(str(n) for n in sorted(numbers)) or "не найдено"
            problems.append(f"цитата не подтверждает норму базы {base!r}: "
                            f"ожидалось {value}, по этой базе прочитано {found}")
    return problems


def legacy_structural_value(raw, reading):
    """Upgrade only the two declared legacy serializations, without guessing.

    The caller proves the reading's origin and still judges the complete
    typed cell, its scope, quotation and provenance. Already structured
    values are not normalized here: invalid keys/types must remain visible.
    """
    if not isinstance(raw, str):
        return raw
    fields = reading["fields"]
    if fields["unit"] == "count" and fields["qualifier"] == "base_code" and ":" in raw:
        table = base_map_value(raw)
        if table is None:
            raise ValueError("норма по базам: некорректная упакованная запись или повтор базы")
        # The packed grammar has unsigned integer tokens; no rounding.
        return {base: int(number) for base, number in table.items()}
    if reading["dictionaries"]["unit"]["form"] == "code_list":
        values = [part.strip() for part in raw.split(",")]
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("перечень обозначений содержит пустой элемент или повтор")
        return values
    return raw


def _unit_word(word_or_code) -> str:
    """Слово единицы носителя одной формой (без краевых пробелов, в
    нижнем регистре) — тем же каноном, что у `unit_scale`."""
    return " ".join(str(word_or_code or "").split()).lower()


def _code_tokens(raw):
    """Обозначения носителя перечнем строк либо None — значение не
    читается как обозначение/перечень обозначений (§3.1: `code` —
    строка обозначения, `code_list` — строка через запятую ЛИБО
    перечень строк)."""
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else None
    if isinstance(raw, (list, tuple)):
        if not raw or any(not isinstance(x, str) or not x.strip()
                          for x in raw):
            return None
        return [x.strip() for x in raw]
    return None


def _scale_decimal(value: Decimal, scale: Decimal) -> Decimal:
    """Точное произведение; точность вызывающего кода не меняет норму.

    У конечных Decimal произведению достаточно суммы длин коэффициентов.
    Не повышаем глобальную точность и не возвращаем округлённое значение.
    """
    with localcontext() as context:
        context.prec = max(1, len(value.as_tuple().digits)
                           + len(scale.as_tuple().digits))
        return value * scale


def canonical_value(raw, word_or_code, target_unit) -> tuple:
    """(значение в канонической единице записи семьи, слова беды |
    None) — ЕДИНСТВЕННАЯ дверь к значению носителя (её зовут A05-барьер
    и полнота семьи K01).

    Единица носителя `word_or_code` — код каталога либо слово
    минимального перечня K02; ПУСТАЯ единица означает «значение уже в
    канонической единице» (закон носителя без единицы, §12.5).
    Единица, не приводимая к канонической, — беда словами, а не
    «считаем совпавшим» (fail-closed).

    Значение читается ПО ФОРМЕ канонической единицы (§3.1):
      * `flag` — логическое и только логическое (`True` ≠ 1 ≠ «да»);
      * `code` / `code_list` — строка обозначения либо перечень
        обозначений (строкой через запятую или списком строк), канон —
        `canon_code_list`;
      * числовые единицы — число (умножается на множитель единицы
        носителя точно, Decimal), строка числа — так же, строка записи
        («12:00», «10 ч 30 мин») — грамматикой K02 `parse_value`.
        Запись с СОБСТВЕННОЙ единицей внутри при объявленной единице
        носителя, отличной от канонической, — противоречие ВНУТРИ
        записи (`value: "50 мин"` при `unit: "ч"`): сверка невозможна,
        молча «50» из неё не выводится (находка Н-2 ревью).

    Значение-НЕскаляр (объект, перечень) грамматике числа не
    показывается никогда (находка В-1): у числовой единицы это беда
    словами, и сверку таких значений ведёт структурный закон
    (`base_map`, `model._identical_raw`)."""
    unit = str(target_unit or "")
    if not unit:
        return None, "канонической единицы у имени нет"
    scale = unit_scale(word_or_code, unit)
    if scale is None:
        return None, (f"единица {word_or_code!r} не приводится к "
                      f"канонической ({unit})")
    if unit == FLAG_UNIT:
        if not isinstance(raw, bool):
            return None, (f"значение {raw!r} не логическое "
                          f"(единица {unit} читает только true/false)")
        return raw, None
    if unit in m._cat("CODE_UNITS"):
        codes = _code_tokens(raw)
        if codes is None:
            return None, f"значение {raw!r} не обозначение"
        if unit == "code":
            if len(codes) != 1 or "," in codes[0]:
                return None, f"значение {raw!r} не одно обозначение"
            return codes[0], None
        # P0 §3.1: typed designations are opaque strings. Preserve internal
        # spaces, punctuation and Unicode instead of interpreting the name
        # as prose or applying the free-answer scanner's alphabet/length.
        # Only list structure is judged here; empty elements are not codes.
        if any(not item.strip() for code in codes
               for item in code.split(",")):
            return None, f"значение {raw!r} не перечень целых обозначений"
        return m.canon_code_list(", ".join(codes)), None
    if is_structural(raw):
        return None, (f"значение {raw!r} не число (структурная запись "
                      f"грамматике числа не показывается)")
    if isinstance(raw, bool) or raw is None:
        return None, f"значение {raw!r} не число"
    source = _modern_unit_source()

    def converted(value, word):
        from template_catalog import units
        try:
            word = str(word or "").strip() or unit
            return units.to_canonical(value, word, unit, source=source, exact=True), None
        except units.UnitsError as problem:
            return None, str(problem)

    if isinstance(raw, (int, float, Decimal)):
        # НЕ-КОНЕЧНОЕ число — не значение (Н-4/В-2 неавтор-ревью):
        # `NaN`/`Infinity` питоновский разбор JSON принимает молча, а
        # сравнить их с порогом и передать исполнителю нельзя (NaN не
        # равен даже себе — два носителя «совпали бы» тишиной). Дверь
        # к значению одна, поэтому закон живёт здесь, а не у читателя.
        got = Decimal(str(raw))
        if not got.is_finite():
            return None, (f"значение {raw!r} не конечное число "
                          f"(NaN и бесконечность значением параметра "
                          f"не бывают)")
        if source is not None:
            return converted(got, word_or_code or unit)
        return _scale_decimal(got, scale), None
    text = str(raw).strip()
    if source is not None:
        from template_catalog import units
        if units._CLOCK_RE.fullmatch(text):
            return converted(text, word_or_code)
    if re.fullmatch(_SIGN + r"\d+(?:[.,]\d+)?", text):
        # K02 владеет допустимой длиной числа; канонизация ячейки
        # использует тот же предел, а не удалённый старый _decimal.
        if _magnitude(text) is None:
            return None, f"значение {raw!r} не читается как число"
        num = Decimal(text.replace(",", ".").replace("\u2212", "-"))
        if source is not None:
            return converted(num, word_or_code or unit)
        return _scale_decimal(num, scale), None
    declared = _unit_word(word_or_code)
    if declared and declared != unit:
        return None, (f"запись {raw!r} несёт свою единицу, а носитель "
                      f"объявил {word_or_code!r} — противоречие внутри "
                      f"записи")
    got = parse_value(text, unit)
    if got is None:
        return None, f"значение {raw!r} не читается в единице {unit}"
    if source is not None:
        # The legacy sentence grammar already returned a canonical value.
        # Do not apply its written-unit multiplier a second time. Replacing
        # that grammar by the catalogue word reader is a separate K03 seam.
        return converted(got[0], unit)
    return Decimal(str(got[0])), None


def _has_payload(sent: str, unit: str) -> bool:
    if unit in m._cat("CODE_UNITS"):      # единицы обозначений — по каталогу
        return bool(_CODE_TOKEN_RE.search(sent))
    return bool(re.search(r"\d", sent))


#: Числовые единицы каталога — их значение читает грамматика числа.
_NUMERIC_UNITS = ("duration_minutes", "time_of_day_minutes",
                  "count") + _HOUR_UNITS


def _sentence_tokens(s: str, unit: str) -> list | None:
    """Токены значения предложения по числовой единице — (величина,
    границы токена) — либо None при нечитаемой записи. Один вход и для
    разбора, и для заметки отказа: причину называют по тем же токенам,
    по которым отказали (K02-R3)."""
    source = _modern_unit_source()
    if source is not None and unit in _NUMERIC_UNITS:
        from .catalogue_numeric import tokens
        return tokens(s, unit, source)
    if _foreign_unit_at(s, unit):
        return None                     # чужое слово величины при числе
    if unit == "duration_minutes":
        return _duration_values(s)
    if unit == "time_of_day_minutes":
        return _clock_values(s)
    if unit == "count":
        toks = _number_values(s, hour_word=False)
        if toks is not None and not all(_is_whole(v) for v, _ in toks):
            return None                 # счёт — целое
        return toks
    if unit in _HOUR_UNITS:
        return _number_values(s, hour_word=True)
    return None


def _value_spans(s: str, unit: str) -> list:
    """Границы числовых записей предложения, прочитанных грамматикой
    как ЗАПИСЬ ЗНАЧЕНИЯ этой единицы, — даже если предложение в целом
    отказало (несогласные записи, чужое слово единицы, непомерный
    токен). Запись значения: у длительности предмет называет слово
    единицы, поэтому голое число записью длительности не является
    («…согласно п. 4.2»); у времени суток запись — только ЧЧ:ММ; у
    счёта и часов кредита запись — САМО ЧИСЛО, а спор о слове единицы
    (K02-R2) — другая причина отказа, со своей заметкой
    (foreign_unit_word).

    Отсюда граница закона охвата, названная и в руководстве (K02-T3):
    ссылка «п. 4.2» при счёте и часах кредита — запись значения, как
    всякое голое число, поэтому «3 (п. 4.2)» отказывает как ДВА разных
    значения (у счёта — ещё и нецелых), а не как «цифры вне числового
    выражения», и unread_digits там пуст. Цифры ссылки называются
    только у длительности и времени суток — там запись значения помечена
    словом единицы или формой ЧЧ:ММ, и код отличает ссылку от значения
    не догадкой, а грамматикой."""
    spans = [mm.span() for mm in _HHMM_RE.finditer(s)]
    if unit == "time_of_day_minutes":
        return spans
    for mm in _QTY_RE.finditer(s):
        if unit != "duration_minutes" or mm.group("h") or mm.group("mi") \
                or mm.group("d"):
            spans.append(mm.span())
    return spans


def unread_digits(raw, unit) -> list[str]:
    """Числовые записи ответа, не вошедшие в значение ВОВСЕ, — причина
    отказа, которую надо назвать авиакомпании («цифры вне числового
    выражения: „4.2“»): ссылка на пункт, обозначение с цифрами, дата,
    часовой пояс, повреждённая запись времени. Судится по тому
    предложению, на котором остановился parse_value; пусто, когда
    причина отказа другая (несогласные записи, чужое слово единицы,
    непомерный токен, значения нет) — там заметка прежняя (K02-R3).

    Называет записи только там, где грамматика отличает запись значения
    от посторонней: у длительности её метит слово единицы, у времени
    суток — форма ЧЧ:ММ. У счёта и часов кредита запись значения — само
    число, поэтому ссылка «п. 4.2» читается как второе значение и
    отказывает своей причиной (см. _value_spans, K02-T3)."""
    unit = str(unit or "")
    if unit not in _NUMERIC_UNITS:
        return []
    for sent in _sentences(raw):
        stray = _stray_digit_records(sent, _value_spans(sent, unit))
        if stray or _has_payload(sent, unit):
            return stray
    return []


def foreign_unit_word(raw, unit) -> str:
    """Чужое слово единицы при числе — причина отказа, которую надо
    назвать авиакомпании («слово „суток“ при числе у счётного параметра
    не читается как единица»), — дословно из ответа либо «».

    Судится по тому же предложению, что unread_digits: по тому, на
    котором остановился parse_value. Пусто, когда причина отказа другая
    (цифры вне выражения, две записи, непомерный токен, значения нет).

    Предел, который заметка обязана назвать вслух (K02-T2): у счётного
    параметра слово единицы не читается ВОВСЕ — до словаря
    `unit_words`/`subject_words` каталога 0.6.0 (волна 17б, контракт П0
    §3.3) коду неизвестно, что именно параметр считает, и «7 суток» у
    параметра «окно в днях» отказывает, а не угадывается по имени
    параметра (fail-closed: эвристик по имени тут не будет). Слово
    единицы — то, что стоит ВПЛОТНУЮ к числу, поэтому «8 выходных дней»
    остаётся счётом 8: «дней» отделено от числа словом «выходных» и
    считается существительным при числе, а не единицей."""
    unit = str(unit or "")
    if unit not in _WORD_JUDGED_UNITS:
        return ""
    for sent in _sentences(raw):
        word = _foreign_unit_at(sent, unit)
        if word or _has_payload(sent, unit):
            return word
    return ""


def _foreign_word_note(word: str, unit: str) -> str:
    """Заметка отказа по чужому слову единицы: называет слово из ответа,
    единицу параметра и то, что делать отвечающему."""
    name = _UNIT_NAMES.get(unit, f"единицы «{unit}»")
    word = _note_quote(word)
    if unit in _HOUR_UNITS:
        return (f"слово «{word}» у {name} не читается как единица — "
                f"укажите значение в часах")
    if unit == "duration_minutes":
        return (f"слово «{word}» у {name} не читается как единица — "
                f"укажите длительность в минутах, часах или сутках")
    if unit == "time_of_day_minutes":
        return (f"слово «{word}» у {name} не читается как единица — "
                f"укажите время в формате ЧЧ:ММ")
    return (f"слово «{word}» при числе у {name} не читается как единица "
            f"— укажите число без слова единицы")


def unread_quantity_context(raw, unit) -> str:
    """Name the words rejected by the same reader, not a second noun lexicon.

    This adds no accepted value or unit conversion. Period/applicability have
    their existing dedicated explanations; count remains out of scope.
    """
    source = _modern_unit_source()
    if unit not in _NUMERIC_UNITS or (unit == "count" and source is None):
        return ""
    if period_qualifier(raw, unit) or applicability_qualifier(raw, unit):
        return ""
    for sent in _sentences(raw):
        if not _has_payload(sent, unit):
            continue
        rejected: list[str] = []
        if source is not None:
            from .catalogue_numeric import tokens
            tokens(sent, unit, source, rejected)
        elif unit == "time_of_day_minutes":
            _clock_values(sent, rejected)
        elif unit == "duration_minutes":
            _duration_values(sent, rejected)
        else:
            _number_values(sent, hour_word=True, rejected_words=rejected)
        return rejected[0] if rejected else ""
    return ""


def _clock_word_note(fragment: str) -> str:
    return (f"непрочитанный фрагмент «{_note_quote(fragment)}» после времени суток; "
            "укажите одно время в формате ЧЧ:ММ. Слово единицы нельзя молча "
            "отбросить или пересчитать; если назван другой предмет, уточните "
            "сам вопрос, не удаляйте ограничения ради формата")


def _parse_sentence(s: str, unit: str) -> tuple:
    """(исход, значение) одного предложения по единице (см. parse_value)."""
    if unit in m._cat("CODE_UNITS"):   # единицы обозначений — по каталогу
        if _unread_code_records(s):
            return _REFUSED, None       # знаки записи вне обозначений
        if unit == "code":
            return (_VALUE, s) if _CODE_RE.match(s) else (_EMPTY, None)
        toks = _CODE_TOKEN_RE.findall(s)
        # ВТОРОЙ РУБЕЖ того же закона, объявленный вслух (двенадцатое
        # неавторское ревью, остаток Р1). Сегодня он не срабатывает ни
        # на одном входе: токен, обозначением не являющийся, бывает
        # только длиннее 16 знаков (_CODE_RE требует буквенно-цифровой
        # знак, а _CODE_TOKEN_RE без него не совпадает), покрытия не
        # даёт — и ПЕРВЫЙ рубеж, закон охвата выше, уже вернул отказ.
        # Клауза оставлена именно рубежом, а не украшением: она держит
        # перечень, если охват когда-нибудь ослабнет (иное _CODE_GLUE,
        # иной _CODE_TOKEN_RE, новый род записи). Своя проба у рубежа
        # есть — K02-12-R1 снимает первый рубеж и требует отказа от
        # второго; без неё клауза была бы мёртвой (мутация «снять» не
        # краснила набор).
        if any(not _is_code(t) for t in toks):
            return _REFUSED, None       # не всякий токен — обозначение
        value = m.canon_code_list(", ".join(toks)) if toks else None
        return (_VALUE, value) if value else (_EMPTY, None)
    if unit not in _NUMERIC_UNITS:
        return _EMPTY, None
    toks = _sentence_tokens(s, unit)
    if toks is None or not _digits_covered(s, [sp for _, sp in toks]):
        return _REFUSED, None           # нечитаемый токен или цифры вне токенов
    if not toks:
        return _EMPTY, None
    got = _one([v for v, _ in toks])
    if got is None:
        return _REFUSED, None           # несколько разных значений
    value = _as_number(got)
    return (_VALUE, value) if value is not None else (_REFUSED, None)


def _answer_cells(doc: dict, param: str, root: str, keep=None) -> list:
    """Строки, чья ячейка `param` внесена ответом на корневой вопрос
    `root` (кроме строки `keep`), парами (правило, params посадки).
    Ячейки из клауз документов (без маркера ответа) сюда не попадают:
    их носитель — правило, а не ответ."""
    out: list = []
    for rule in doc.get("rules", []) or []:
        if not isinstance(rule, dict) or rule.get("id") == keep:
            continue
        fit = rule.get("template_fit")
        params = fit.get("params") if isinstance(fit, dict) else None
        if not isinstance(params, dict) or param not in params:
            continue
        if m.cell_answer_root(params[param]) != root:
            continue
        out.append((rule, params))
    return out


def _drop_leaves_landing_empty(doc: dict, param: str, root: str,
                               keep=None) -> list[str]:
    """Строки, у которых снятие ячейки `param` не оставит в посадке НИ
    ОДНОГО параметра. Такая посадка контракту не отвечает («семья со
    словарными параметрами — params обязателен»), поэтому снятие тут не
    делается: предмет решения — не значение, а сама посадка строки."""
    return [str(rule.get("id")) for rule, params
            in _answer_cells(doc, param, root, keep) if len(params) == 1]


def _drop_answer_cells(doc: dict, param: str, root: str,
                       keep=None) -> list[str]:
    """Снять ячейки параметра `param`, внесённые ответом на корневой
    вопрос `root` (кроме строки `keep`). Нужно при применении ответа на
    ПЕРЕСМОТР (K04, §2.4): прежнее значение того же вопроса не остаётся
    жить рядом с новым решением — ни на другом носителе, ни при явном
    «не применяется»."""
    out: list[str] = []
    for rule, params in _answer_cells(doc, param, root, keep):
        params.pop(param, None)
        if not params:
            rule["template_fit"].pop("params", None)
        out.append(str(rule.get("id")))
    return out


def _write_answer_cell(carrier, q, param, value, quote, raw):
    cell = _answer_cell(q, param, value, quote, raw)
    fit = carrier.get("template_fit") or {}
    if fit.get("schema_version") == "landing/2":
        from template_catalog import landing
        try:
            cell = landing.prepare_cell(carrier, param, cell, _bridge.catalog())
        except _bridge.CATALOG_ERRORS as error:
            return f"{param}: typed-ячейка не построена: {error}"
        # The bounded answer parser's code_list result is a serialized list,
        # not an arbitrary raw designation. Preserve its element boundaries.
        if cell["unit"] == "code_list" and isinstance(value, str):
            cell["value"] = m.split_code_list(value)
    return write_cell(carrier, param, cell)


def apply_param_answer(doc: dict, q: dict, raw: str, revision: int,
                       targets=None) -> dict:
    from cdpm import answer_parsing
    parsed = answer_parsing.parameter(q, raw)
    result = _apply_param_answer(doc, q, raw, revision, targets, parsed)
    answer = q.get("answer")
    if isinstance(answer, dict) and answer.get("raw") == raw:
        answer["parsed"] = parsed
    return result


def _apply_param_answer(doc: dict, q: dict, raw: str, revision: int,
                        targets, parsed: dict) -> dict:
    """Применение ответа на вопрос параметра семьи КОДОМ (вызывается
    из answers.apply_answers внутри журнала следствий). Возвращает
    {"applied": bool, "touched": [id носителей]}; статус и заметка
    ложатся в q["answer"]."""
    from cdpm import question_editions
    edition_problems = question_editions.application_problems(doc, q)
    if edition_problems:
        q["answer"] = {"raw": raw, "status": "не разобран",
                       "revision": revision, "note": "; ".join(edition_problems)}
        return {"applied": False, "touched": []}
    targets = set(targets or ()) | {q.get("id"), _root_id(q)}
    tgt = question_target(q)
    qid = str(q.get("id"))
    if tgt is None:
        q["answer"] = {"raw": raw, "status": "не разобран",
                       "revision": revision,
                       "note": "ключ вопроса параметра не читается по "
                               "каталогу (семья или параметр неизвестны)"}
        return {"applied": False, "touched": []}
    fam, param = tgt
    spec = _param_spec(fam, param)
    from cdpm import parameter_contract
    contract_problem = parameter_contract.application_problem(q, fam, param)
    if contract_problem:
        q["answer"] = {"raw": raw, "status": "не разобран", "revision": revision,
                       "note": contract_problem}
        return {"applied": False, "touched": []}
    subject_problems = [p for p in parsed["problems"] if p.startswith(("предмет:", "единица:"))]
    if subject_problems:
        q["answer"] = {"raw": raw, "status": "не разобран", "revision": revision,
                       "note": "; ".join(subject_problems)}
        return {"applied": False, "touched": []}
    from cdpm import revision_cells
    rids = {r.get("id") for r in doc.get("rules", []) if isinstance(r, dict)}
    root_id = _root_id(q)
    parts = _decline_parts(raw)        # Один разбор, с барьерами общей базы.
    if parts is not None and not parts[1]:
        # K04: отказ от ЕДИНСТВЕННОГО параметра семьи ответом не
        # применяется. Снятие такой ячейки оставило бы посадку без
        # параметров — свод стал бы невалидным, и живой путь приёма
        # книги ответов уронил бы стадию ЦЕЛИКОМ: слова авиакомпании
        # пропали бы, признак пересмотра остался бы стоять. Решение
        # записывается «не разобран» словами: предмет отказа — посадка
        # строки, а не значение; прежнее значение остаётся ПОД
        # признаком (решением оно не является — барьер держит).
        emptied = _drop_leaves_landing_empty(doc, param, root_id)
        if emptied:
            q["answer"] = {
                "raw": raw, "status": "не разобран", "revision": revision,
                "note": (f"параметр {param} семьи {fam} — единственный в "
                         f"посадке "
                         f"{'строк' if len(emptied) > 1 else 'строки'} "
                         f"{', '.join(emptied)}: отказ от единственного "
                         f"параметра семьи означает, что посадка на семью "
                         f"не стоит — нужна перепосадка строки, не ответ. "
                         f"Прежнее значение остаётся под открытым "
                         f"пересмотром и решением авиакомпании не является")}
            return {"applied": False, "touched": []}
        # Решение «не применяется» отменяет и прежние значения ЭТОГО же
        # вопроса: иначе прежнее значение осталось бы в посадке рядом с
        # решением «параметр не задаётся» (K04: старое значение не
        # выдаётся за новое решение).
        dropping = [rule for rule, _ in _answer_cells(doc, param, root_id)]
        conflicts = revision_cells.conflicts(doc, q, fam, param, dropping)
        if conflicts:
            return revision_cells.refuse(q, raw, revision, conflicts)
        if _refuse_inconsistent_drops(dropping, q, raw, revision):
            return {"applied": False, "touched": []}
        dropped = _drop_answer_cells(doc, param, root_id)
        q["answer"] = {"raw": raw, "status": "применён",
                       "revision": revision,
                       "note": (f"параметр {param} семьи {fam}: требования "
                                f"у авиакомпании нет — параметр не "
                                f"задаётся, решение записано ответом")}
        for t in targets:
            _set_blockers(doc, str(t), rids, False)
        m.clear_pending_revision(doc, qid)
        return {"applied": True, "touched": dropped}
    unit = spec.get("unit")
    if parts is not None:
        # Отказ с условием, пояснением, значением или номером строки
        # анкеты — в том же предложении (В-3), в соседнем (B1) и в
        # ЛЮБОМ порядке (K-1): решение не безусловно — по §2.2 вопрос
        # остаётся открытым, носители заблокированы. Фраза отказа в
        # заметке называется, помеха — нет: она бывает любой длины, а
        # заметка ограничена. Величину ищет ВЕСЬ ответ, а не одна
        # помеха: «Не применяется для SVO. В остальных базах 600
        # минут.» и обратный порядок тех же предложений — один разлад,
        # и заметка у них одна (фразы отказа величин не несут, поэтому
        # искать её в ответе целиком безопасно).
        phrase, tail, same = parts
        if _has_payload(raw, str(unit)):
            clash = ("отказ и значение в одном предложении"
                     if same and _has_payload(tail, str(unit))
                     else "отказ и значение в одном ответе")
        else:
            clash = ("отказ с условием в том же предложении" if same
                     else "отказ с пояснением или условием в том же ответе")
        q["answer"] = {
            "raw": raw, "status": "не разобран", "revision": revision,
            "note": (f"ответ не разобран как решение по параметру {param} "
                     f"семьи {fam}: {clash} — безусловным отказом считается "
                     f"ВЕСЬ ответ из фраз отказа, а «{phrase}» здесь сказано "
                     f"не обо всём; назовите отказ отдельным ответом («Не "
                     f"применяется.»), а условия, пояснения и значения — по "
                     f"строкам анкеты, одно значение на строку "
                     f"({unit_words(unit)})")}
        return {"applied": False, "touched": []}
    domain = default_domain(unit)      # K02: домен параметра судит знак и границы
    got = None if parsed["ambiguous"] else (parsed["value"], parsed["quote"])
    if got is None:
        from cdpm import answer_subjects
        value_view = answer_subjects.value_text(raw, fam, param, unit)
        negation = negated_value_problem(value_view, unit)
        if negation and not line_number_record(raw) and not mixed_script_word(raw):
            # Preserve the established line-number/mixed-script priority.
            # Otherwise show the parser's exact refusal, not the generic note.
            note = f"ответ по параметру {param} {negation}"
        elif domain is not None and answer_subjects.read_value(raw, fam, param, unit) is not None:
            note = (f"значение параметра {param} прочитано, но лежит вне "
                    f"области параметра ({_domain_words(domain)}) — "
                    f"укажите одно значение в этой области")
        else:
            # Заметку выбирает ЭТОТ порядок (K02-T2/T3, восьмое ревью):
            # НОМЕР СТРОКИ анкеты — он отказывает всему ответу целиком,
            # раньше любой единичной причины; затем чужое слово единицы
            # — на нём разбор предложения останавливается раньше закона
            # охвата цифр, — затем цифры вне выражения, затем общая.
            # Порядок объявлен здесь, а не выведен из грамматики:
            # грамматика знает лишь «отказ», причину для человека
            # выбирает применение ответа. Номер стоит первым потому,
            # что он ОБЪЯСНЯЕТ прочие приметы: у «1) 600 минут» цифра
            # «1» и правда лежит вне числового выражения, но человеку
            # надо сказать не «уберите ссылки и даты», а «уберите номер
            # строки» — иначе заметка ведёт мимо причины.
            number = line_number_record(raw)
            mixed = "" if number else mixed_script_word(raw)
            period = "" if (number or mixed) else period_qualifier(value_view, unit)
            word = "" if (number or mixed or period) else foreign_unit_word(value_view, unit)
            quantity = "" if (number or mixed or period or word) else (
                unread_quantity_context(value_view, unit))
            stray = [] if (number or mixed or period or word or quantity) else (
                unread_digits(value_view, unit) or unread_code_records(value_view, unit))
            context = "" if (number or mixed or period or word or quantity or stray) else (
                code_context_problem(raw, unit))
            scope = "" if (number or mixed or period or word or quantity or stray or context) else (
                applicability_qualifier(value_view, unit))
            note = (f"ответ не разобран как одно значение "
                    f"параметра {param} ({unit_words(unit)})")
            if number:
                note += (f": номер строки анкеты «{_note_quote(number)}»"
                         f" — не часть "
                         f"ответа, а что названо цифрой, номер или "
                         f"значение, ответ не говорит; назовите значение "
                         f"без номера строки")
            elif mixed:
                note += (f": слово «{_note_quote(mixed)}» записано двумя "
                         f"алфавитами сразу (латиница и кириллица) — на вид "
                         f"одно, по знакам другое, и читать его значением "
                         f"код не станет; перепишите ответ одним алфавитом")
            elif period:
                note += (f": период «{_note_quote(period)}» не объявлен "
                         f"в контракте этого параметра; периодическую норму "
                         f"нельзя записать как абсолютное значение — "
                         f"требуется уточнить модель параметра и его период; "
                         f"исходный ответ сохранён, период не отбрасывается "
                         f"и автоматически не пересчитывается")
            elif word:
                note += ": " + _foreign_word_note(word, unit)
            elif quantity and unit == "time_of_day_minutes":
                note += ": " + _clock_word_note(quantity)
            elif quantity:
                name = _UNIT_NAMES.get(unit, str(unit))
                note += (f": непрочитанные слова «{_note_quote(quantity)}» "
                         f"рядом с числом у {name}; нельзя молча отбросить "
                         f"названный предмет или считать его другой единицей. "
                         f"Уточните величину и её единицу; если вопрос "
                         f"о другом предмете, требуется уточнить параметр; "
                         f"исходный ответ сохранён")
            elif stray and str(unit) in m._cat("CODE_UNITS"):
                note += (": знаки вне прочитанных обозначений — "
                         + _records_words(stray)
                         + "; назовите обозначения через запятую, без "
                           "ссылок, пояснений и записей времени")
            elif context:
                note += (f": непрочитанный контекст «{_note_quote(context)}» "
                         f"в ответе с обозначениями; отрицания, исключения "
                         f"и условия не отбрасываются, а поясняющие слова "
                         f"не становятся отдельными кодами. Укажите сами "
                         f"обозначения через запятую либо уточните полное "
                         f"название/условие; исходный ответ сохранён")
            elif stray:
                note += (": цифры вне числового выражения — "
                         + _records_words(stray)
                         + "; назовите значение отдельным предложением, "
                           "без ссылок, обозначений и дат")
            elif scope:
                note += (f": оговорка применимости «{_note_quote(scope)}» "
                         f"не представлена в скалярном значении; нельзя "
                         f"записать условную норму как безусловную. "
                         f"Требуется уточнить модель правила и перенести "
                         f"оговорку в поддерживаемое условие. Исходный "
                         f"ответ сохранён, вопрос и блокировка остаются; "
                         f"удалять оговорку из ответа не требуется")
            else:
                note += (" и не является «не применяется» — укажите "
                         "одно значение")
        q["answer"] = {"raw": raw, "status": "не разобран",
                       "revision": revision, "note": note}
        return {"applied": False, "touched": []}
    value, quote = got
    # The code writes one carrier. Other rows in the issued scope still need
    # their own result (A02), even though family-wide coverage is now complete.
    from cdpm import answers
    affected = answers._affected_rules(doc, q, include_processed=True)
    scoped_doc = {**doc, "rules": affected}
    carrier = _first_carrier(scoped_doc, fam, q.get("rule_refs"))
    if carrier is None:
        # K04: прежняя подсказка «вопрос снимет код» была неправдой —
        # код снимает вопрос ЕДИНСТВЕННЫМ исходом «вопрос больше не
        # требуется», и только когда исчезли ВСЕ носители параметра.
        # Живой носитель без посадки на семью (машинная строка под
        # контрактом посадки) исхода не даёт: вопрос останется открытым,
        # а строка — заблокированной, пока строку не перепосадят.
        if m.is_revision_question(q):
            alive = m.revision_carriers(doc, q)
            tail = (f"Код снимает пересмотр сам лишь исходом "
                    f"«{m.CODE_CLOSE_OUTCOME}», когда исчезнут ВСЕ носители "
                    f"параметра")
            if alive:
                tail += (f", а носител{'и' if len(alive) > 1 else 'ь'} "
                         f"{', '.join(alive)} жив"
                         f"{'ы' if len(alive) > 1 else ''} — вопрос "
                         f"останется открытым и стро"
                         f"{'ки' if len(alive) > 1 else 'ка'} "
                         f"заблокированной: нужна перепосадка строки на "
                         f"семью {fam}, а не ответ")
        else:
            tail = ("Вопрос снимет код: носителей у семьи в своде не "
                    "осталось")
        q["answer"] = {"raw": raw, "status": "не разобран",
                       "revision": revision,
                       "note": (f"в своде нет правила-носителя семьи {fam} "
                                f"— значение применить некуда. {tail}")}
        return {"applied": False, "touched": []}
    dropping = [rule for rule, _ in _answer_cells(
        scoped_doc, param, root_id, keep=carrier.get("id"))]
    conflicts = revision_cells.conflicts(doc, q, fam, param, [carrier, *dropping])
    if conflicts:
        return revision_cells.refuse(q, raw, revision, conflicts)
    # Снятие тоже меняет носители: иначе можно удалить свидетельство
    # конфликта на другом правиле после успешной записи в первое.
    if _refuse_inconsistent_drops(dropping, q, raw, revision):
        return {"applied": False, "touched": []}
    problem = _write_answer_cell(carrier, q, param, value, quote, raw)
    if problem:
        q["answer"] = {"raw": raw, "status": "не разобран",
                       "revision": revision,
                       "note": f"значение не записано: {problem}"}
        return {"applied": False, "touched": []}
    # Прежние значения того же вопроса на ДРУГИХ носителях снимаются:
    # значение ответа живёт ровно в одной ячейке (K04, §2.4).
    # Все отказы писателя — ДО снятия; отказ сохраняет всю операцию.
    dropped = _drop_answer_cells(scoped_doc, param, root_id,
                                 keep=carrier.get("id"))
    handled = {carrier.get("id")}
    remaining = {r.get("id") for r in affected} - handled
    for t in targets:
        _set_blockers(doc, str(t), handled, False)
    _set_blockers(doc, qid, remaining, True)
    m.clear_pending_revision({"rules": [carrier]}, qid)
    q["answer"] = {"raw": raw,
                   "status": m.PARTIAL_ANSWER_STATUS if remaining else "применён",
                   "revision": revision,
                   "note": (f"параметр {param} = {value} "
                            f"({spec.get('unit')}) внесён в посадку правила "
                            f"{carrier.get('id')} семьи {fam} по ответу "
                            f"анкеты")}
    if remaining:
        q["answer"]["note"] += (
            "; остальные строки требуют результата обработчика: "
            + ", ".join(sorted(remaining)))
    return {"applied": not remaining, "partial": bool(remaining),
            "touched": sorted({str(carrier.get("id")), *dropped})}
