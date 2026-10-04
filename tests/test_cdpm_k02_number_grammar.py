# -*- coding: utf-8 -*-
"""K02 (аудит каталога Н0-Б/Н0-С, fcec7a2e): числовой разбор ответа
читает значение целиком, а не фрагмент.

Ожидания — по контракту П0 §2.2 и oracle аудитора (REPLY-AUDITOR-CATALOG-01,
план v5 §2 п. 2) с ИНВЕРСИЕЙ дефектного поведения: раньше «10,5 часов»
давало 300 («5 часов»), «10:90» — 690, знак у «-600 минут» терялся.

Охраняемое:
- токен значения с границами: целое; десятичное с точкой или запятой
  (точно, без округления); ЧЧ:ММ с минутами 0–59; знак — часть токена;
- целые минуты — только если результат целый, иначе отказ разбора;
- домен параметра (из контракта семьи) судит допустимость: знак и
  границы; без домена значение принимается как прочитано;
- неразобранное или неоднозначное — None (вопрос остаётся открытым);
- прежние допустимые записи разбираются по-старому (положительные
  контроли)."""
from decimal import Decimal, getcontext
from fractions import Fraction
import hashlib

import pytest

from cdpm import answers
from cdpm import family_params as fp
from cdpm import model as m
from cdpm.sample import sample_model
from cdpm.anketa_xlsx import question_fingerprint
from _k02_work_budget import k02_work_budget


def _expected_answer_source(q, param, raw):
    """Полная форма A03/K05, не частичное сравнение старой ячейки.

    Не зовём писателя answer_source: отдельно задаём поля и отпечаток
    текста; отпечаток издания берём из контракта книги ответов.
    """
    return {"kind": "answer", "root": q.get("revision_of") or q["id"],
            "question": {"id": q["id"], "revision": 1,
                         "fingerprint": question_fingerprint(q)},
            "version": {"fingerprint": hashlib.sha256(
                " ".join(raw.split()).encode("utf-8")).hexdigest()[:12]},
            "effect": {"rule": param, "field": "value"}}


@pytest.mark.parametrize("raw,want", [
    ("10,5 часов", 630),            # oracle аудитора: раньше 300
    ("1.5 часа", 90),               # oracle аудитора
    ("10 ч 30 мин", 630),           # прежняя запись
    ("600 минут", 600),
    ("10:00", 600),
    ("10:00 (600 минут)", 600),     # две записи одного значения
    ("0 минут", 0),
    ("30 часов", 1800),             # больше суток — без домена допустимо
    ("Отдых вне базы — 9 часов.", 540),
])
def test_duration_tokens_are_read_whole(raw, want):
    got = fp.parse_value(raw, "duration_minutes")
    assert got is not None and got[0] == want


@pytest.mark.parametrize("raw", [
    "10:90",                        # минуты вне 0–59 (oracle аудитора)
    "12:00 (600 минут)",            # противоречивые записи
    "10 часов (500 минут)",         # две противоречащие единицы
    "2,25 минуты",                  # нецелые минуты — не округляются
    "Зависит от типа рейса.",       # значения нет
])
def test_unparsable_or_ambiguous_is_refused(raw):
    assert fp.parse_value(raw, "duration_minutes") is None


def test_sign_is_part_of_the_token_and_domain_judges_it():
    """«-600 минут»: знак сохраняется (oracle аудитора); допустимость —
    по домену параметра: неотрицательная длительность отказывает,
    параметр без домена принимает как прочитано."""
    got = fp.parse_value("-600 минут", "duration_minutes")
    assert got is not None and got[0] == -600
    assert fp.parse_value("-600 минут", "duration_minutes",
                          domain={"min": 0}) is None
    assert fp.parse_value("600 минут", "duration_minutes",
                          domain={"min": 0})[0] == 600


def test_domain_bounds_from_family_contract():
    assert fp.parse_value("30 часов", "duration_minutes",
                          domain={"max": 24 * 60}) is None
    assert fp.parse_value("23 часа", "duration_minutes",
                          domain={"max": 24 * 60})[0] == 1380


def test_decimal_is_exact_not_float_rounding():
    """Десятичные часы преобразуются точно: 0,1 часа = 6 минут ровно."""
    assert fp.parse_value("0,1 часа", "duration_minutes")[0] == 6
    assert fp.parse_value("2.5 часа", "duration_minutes")[0] == 150


def test_time_of_day_still_validates_minutes():
    assert fp.parse_value("с 22:00", "time_of_day_minutes")[0] == 1320
    assert fp.parse_value("с 22:60", "time_of_day_minutes") is None


def test_count_and_credit_hours_keep_working():
    assert fp.parse_value("не более 3 смен подряд", "count")[0] == 3
    assert fp.parse_value("1,5", "hours_per_credit_hour")[0] == 1.5
    assert fp.parse_value("75 часов кредита", "credit_hours")[0] == 75


# ── VERDICT-K02-647bcaf0 (12.09.2026): пять обязательных замечаний ───
# аудитора, −/+ контроли. Отрицательные случаи — дословно из вердикта
# (раньше давали число); положительные контроли — прежние допустимые
# записи, которые обязаны читаться по-старому.

U = "duration_minutes"
QID = "Q-3b-параметр-rest_floors-min_rest_outstation"
CLAUSES = {("S1", "П.2.3"):
           "«не планировать явку ранее 08:00 после ночного рейса»"}


def _prepared():
    """Свод образца с одним параметром rest_floors из двух (как VN-023
    в Н0-B): вопрос второго параметра открыт, носитель заблокирован."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {
        "category": "A", "registry_rev": m.registry_fingerprint(),
        "family": "rest_floors",
        "params": {"min_rest_home": {"value": 720, "quote": "ранее 08:00"}}}
    assert fp.ensure_family_param_questions(doc) == [QID]
    q = next(x for x in doc["questions"] if x["id"] == QID)
    assert QID in rule["blockers"] and m.is_open_question(q)
    return doc, rule, q


# K02-F1 — точка сокращения единицы не рвёт противоречивую запись

@pytest.mark.parametrize("raw", [
    "12 ч. (600 минут)", "600 мин. (12 часов)",
    "1 сут. (60 минут)", "600 мин. = 12 часов",
    "12 ч.(600 минут)", "600 мин.= 12 часов",
])
def test_f1_unit_dot_keeps_contradictory_record_whole(raw):
    """Точка сокращения единицы («ч.», «мин.», «сут.», «дн.» — слова
    П0 §12.6) — часть слова, а не конец предложения: запись остаётся
    одним предложением, и противоречащая вторая запись не теряется —
    отказ, а не первое число (раньше: 720 / 600 / 1440 / 600)."""
    assert fp._sentences(raw) == [raw]
    assert fp.parse_value(raw, U) is None


@pytest.mark.parametrize("raw,want", [
    ("10 ч. (600 минут)", 600), ("600 мин. (10 часов)", 600),
    ("600 мин. = 10 часов", 600), ("1 сут. (1440 минут)", 1440),
    ("10 ч. 30 мин.", 630), ("10 ч. 30 мин. (630 минут)", 630),
    ("10 ч.", 600), ("600 мин.", 600), ("1 сут.", 1440),
])
def test_f1_consistent_records_with_unit_dot_are_read(raw, want):
    """Положительные контроли: согласные повторные записи и сокращения
    с точкой читаются; цитата — всё предложение, с точкой единицы."""
    assert fp.parse_value(raw, U) == (want, raw)


@pytest.mark.parametrize("raw", [
    "12 часов. (600 минут)", "12 час. (600 минут)",
    "600 минут. (12 часов)", "12 часов. = 600 минут",
    "600 минут. 720 минут", "12 часов. 600 минут",
    "600 минут. и 12 часов",
])
def test_f1_dot_before_a_non_sentence_start_keeps_the_record_whole(raw):
    """Тот же дефект границы без сокращения: точка перед скобкой,
    знаком равенства, цифрой или строчной буквой — не конец
    предложения, потому что продолжение не способно начать
    предложение. Противоречие судится внутри одной записи — отказ, а не
    первое число (раньше: 720 / 720 / 600 / 720 / 600 / 720 / 600)."""
    assert fp._sentences(raw) == [raw]
    assert fp.parse_value(raw, U) is None


@pytest.mark.parametrize("raw,want", [
    ("10 часов. (600 минут)", 600), ("600 минут. (10 часов)", 600),
    ("10 час. = 600 минут", 600), ("600 минут. 600 минут", 600),
])
def test_f1_consistent_records_across_a_dot_are_read(raw, want):
    """Положительный контроль оборотной стороны: согласные записи через
    точку перед не-началом предложения читаются, а не отказывают."""
    assert fp.parse_value(raw, U) == (want, raw)


def test_f1_ordinary_sentence_split_survives():
    """Обычное деление сохраняется: точка перед ПРОПИСНОЙ буквой —
    конец предложения, первое предложение со значением даёт значение и
    цитату (соседние величины следующих предложений не мешают)."""
    full = "Отдых вне базы — 10 часов. Дома — 12 часов."
    assert fp._sentences(full) == ["Отдых вне базы — 10 часов",
                                   "Дома — 12 часов"]
    assert fp.parse_value(full, U) == (600, "Отдых вне базы — 10 часов")
    assert fp.parse_value("600 минут. Для A320 — 720 минут.", U) \
        is None  # CDPM-251: later applicability cannot be discarded.
    assert fp.parse_value(
        "Отдых вне базы — 600 минут. Основание — п. 4.2.", U) \
        == (600, "Отдых вне базы — 600 минут")


@pytest.mark.parametrize("short,long,want,quote", [
    ("Отдых вне базы — 10 ч. Дома — 12 ч.",
     "Отдых вне базы — 10 часов. Дома — 12 часов.",
     600, "Отдых вне базы — 10 ч."),
    ("Минимум 12 ч. Это норма.", "Минимум 12 часов. Это норма.",
     720, "Минимум 12 ч."),
    ("10 ч. 30 мин. Это минимум.", "10 часов 30 минут. Это минимум.",
     630, "10 ч. 30 мин."),
    ("1 дн. Это минимум.", "1 день. Это минимум.", 1440, "1 дн."),
    ("Отдых 10 ч. Основание — пункт 4 приложения.",
     "Отдых 10 часов. Основание — пункт 4 приложения.",
     600, "Отдых 10 ч."),
])
def test_f1_abbreviation_does_not_change_the_split(short, long, want, quote):
    """Закон границы ОДИН для любой точки: сокращение не меняет
    деления — запись с «ч.»/«дн.» делится там же, где та же запись
    словом целиком, и даёт то же значение. Точка единицы при этом
    остаётся в цитате («Отдых вне базы — 10 ч.»), потому что она часть
    объявленного слова, а не конец предложения (K02-F5); дословность
    цитаты сохраняется. Без этого закона сокращение съедало бы границу:
    «Отдых 10 ч. Основание — пункт 4 приложения.» стало бы одним
    предложением и отказало бы по нечитаемой цифре пункта (K02-F2)."""
    assert fp.parse_value(short, U) == (want, quote)
    assert fp.parse_value(long, U)[0] == want
    assert quote in short                      # цитата дословна
    assert len(fp._sentences(short)) == len(fp._sentences(long)) == 2


@pytest.mark.parametrize("raw,want,quote", [
    ("Отдых вне базы — 10 часов. «Дома» — 12 часов.", 600,
     "Отдых вне базы — 10 часов"),
    ("Отдых вне базы — 10 ч. «Дома» — 12 ч.", 600,
     "Отдых вне базы — 10 ч."),
    ("Отдых 600 минут. (Пункт 4.2 приложения.)", 600, "Отдых 600 минут"),
    ("Отдых 600 минут. \"Дома\" — 720 минут.", 600, "Отдых 600 минут"),
])
def test_f1_opening_quote_or_bracket_does_not_hide_a_sentence_start(
        raw, want, quote):
    """Предложение начинает прописная БУКВА, а открывающие кавычки и
    скобки перед ней границы не прячут: «…10 часов. «Дома» — 12 часов.»
    делится, как и без кавычек. Иначе соседняя величина в кавычках
    сливалась бы в одну запись и ответ отказывал бы на ровном месте."""
    assert fp.parse_value(raw, U) == (want, quote)


@pytest.mark.parametrize("raw", [
    "12 ч. (600 минут)", "12 ч. (дома 600 минут)", "600 минут. (720 минут)",
    "600 минут. «720 минут»", "12 ч. \"600 минут\"",
])
def test_f1_bracket_before_a_number_or_lowercase_is_not_a_sentence_start(raw):
    """Оборотная сторона того же закона: за кавычкой или скобкой стоит
    цифра либо строчная буква — предложение не начато, запись одна,
    противоречие судится внутри неё (K02-F1)."""
    assert fp._sentences(raw) == [raw]
    assert fp.parse_value(raw, U) is None


# Целость разбора: служебная метка точки единицы против повреждённого
# текста (собственная граница делимости — F1/F5 — не подделывается)

@pytest.mark.parametrize("raw", [
    "600\x00минут", "12 ч\x00 Дома 600 минут", "600 минут\x00",
    "600 \x07минут", "600 минут\x1b[0m", "\x7f600 минут",
])
def test_damaged_control_characters_give_no_value_and_no_quote(raw):
    """Разбор не подделывается собственной меткой: точку сокращения
    единицы через деление на предложения несёт управляющий знак, и
    ответ, содержащий такой знак сам, повреждён — ни предложений, ни
    цитаты, ни значения. Иначе «12 ч\\x00 Дома 600 минут» дало бы 720 с
    цитатой «12 ч.», которой в ответе нет, а «600\\x00минут» — цитату,
    не являющуюся дословной подстрокой ответа (свод отверг бы её)."""
    assert fp._sentences(raw) == []
    assert fp.answer_quote(raw) == ""
    assert fp.parse_value(raw, U) is None


@pytest.mark.parametrize("raw", [
    "600\tминут", "600\nминут", "600 минут\r\n", "600\x0bминут",
    "600\x0cминут", "600\x1fминут", "600  минут",
])
def test_whitespace_controls_stay_ordinary_spaces(raw):
    """Обратная сторона: пробельные знаки (в том числе управляющие
    \\t \\n \\v \\f \\r \\x1c–\\x1f) — обычный пробел, как прежде."""
    assert fp.parse_value(raw, U)[0] == 600


def test_whitespace_control_answer_applies_with_verbatim_quote():
    """Сквозной контроль: ответ с переносом строки применяется, цитата
    судится сводом как дословная (нормализация пробелов)."""
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: "Отдых вне базы —\n600 минут."})[
        "applied"] == 1
    assert rule["template_fit"]["params"]["min_rest_outstation"]["value"] == 600
    assert m.validate_model(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []


# K02-F2 — нераспознанная числовая запись рядом с правильной

@pytest.mark.parametrize("raw", [
    "10:900 (600 минут)", "10:90:00 (600 минут)",
    "10:00:30 (600 минут)", "10:00x (600 минут)",
    "x10:00 (600 минут)", "600 минут (10:00:00)",
    "по п.4.2 — 600 минут", "для A320 — 600 минут",
    "с 01.10.2026 — 600 минут",
])
def test_f2_unread_digits_next_to_a_valid_value_refuse(raw):
    """Числовое выражение читается целиком: каждая цифра предложения
    обязана лежать в прочитанном токене. Повреждённая или
    неподдерживаемая запись (секунды, буква у времени, ссылка на пункт,
    обозначение с цифрами, дата) рядом с правильной — отказ, а не 600;
    успех отдельных совпадений регулярок — не охват выражения."""
    assert fp.parse_value(raw, U) is None


@pytest.mark.parametrize("unit,raw,want", [
    (U, "10:00 (600 минут)", 600), (U, "10 ч 30 мин (630 минут)", 630),
    (U, "10ч30мин", 630), (U, "Отдых вне базы — 9 часов", 540),
    ("time_of_day_minutes", "с 22:00", 1320),
    ("time_of_day_minutes", "с 22:00x", None),
    ("count", "не более 3 смен подряд", 3),
    ("count", "3 смены (72 часа)", None),
])
def test_f2_fully_read_expressions_keep_working(unit, raw, want):
    got = fp.parse_value(raw, unit)
    assert (got[0] if got else None) == want


# K02-F3 — минус у нулевой часовой части составной длительности

@pytest.mark.parametrize("raw", [
    "-0 ч 30 мин", "−0 ч 30 мин", "-0 ч. 30 мин.", "-0ч30мин",
])
def test_f3_minus_before_zero_hours_is_kept(raw):
    """Знак — лексический токен и относится ко всей составной величине:
    «-0 ч 30 мин» → −30 (раньше +30, потому что Decimal(«-0») < 0 ложно);
    в неотрицательном домене параметра — отказ, не +30."""
    assert fp.parse_value(raw, U)[0] == -30
    assert fp.parse_value(raw, U, {"min": 0}) is None
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None


@pytest.mark.parametrize("raw,want", [
    ("-1 ч 30 мин", -90), ("-0:30", -30), ("−0:30", -30),
    ("+0 ч 30 мин", 30), ("0 ч 30 мин", 30), ("-0 минут", 0),
    ("-600 минут", -600), ("-1,5 часа", -90),
])
def test_f3_sign_controls(raw, want):
    assert fp.parse_value(raw, U)[0] == want
    assert (fp.parse_value(raw, U, {"min": 0}) is None) == (want < 0)
    assert fp.parse_value("-0", "count")[0] == 0
    assert fp.parse_value("-3", "count")[0] == -3


# K02-F4 — Decimal округляет до проверки целых минут

@pytest.mark.parametrize("token,factor,word", [
    ("1.0000000000000000000000000001", 1, "минуты"),
    ("0.099999999999999999999999999999", 60, "часа"),
    ("0.10000000000000000000000000001", 60, "часа"),
    ("0,00000000000000000000000000000001", 1440, "суток"),
    ("0.10000000000000000000000000000", 60, "часа"),
    ("1.0000000000000000000000000000", 1, "минуты"),
])
def test_f4_long_fractions_are_exact_not_rounded_by_decimal_context(
        token, factor, word):
    """Арифметика точная (Fraction): длинная дробь не округляется
    контекстом Decimal (точность 28) до целых минут. Независимый oracle
    — рациональная дробь: целые минуты только при знаменателе 1;
    точная дробь той же длины читается (0,1000…0 часа → 6)."""
    assert getcontext().prec == 28      # ловушка контекста существует…
    assert str(abs(Decimal("1.0000000000000000000000000001"))) \
        == "1.000000000000000000000000000"  # …и abs в ней округляет
    exact = Fraction(token.replace(",", ".")) * factor
    want = exact.numerator if exact.denominator == 1 else None
    got = fp.parse_value(f"{token} {word}", U)
    assert (got[0] if got else None) == want


def test_f4_count_long_fraction_is_not_whole():
    assert fp.parse_value("3.0000000000000000000000000001", "count") is None
    assert fp.parse_value("3.0000000000000000000000000000", "count")[0] == 3


# K02-F5 — объявленное «дн.» принимается

@pytest.mark.parametrize("raw,want", [
    ("1 дн.", 1440), ("2 дн.", 2880), ("0,5 дн.", 720),
    ("1 дн. (1440 минут)", 1440), ("1 дн. Это минимум.", 1440),
])
def test_f5_declared_dn_with_dot_is_read(raw, want):
    """Слово «дн.» объявлено П0 §12.6 — точка единицы доходит до
    грамматики (раньше «1 дн.» → None: точку снимало деление на
    предложения)."""
    assert fp.parse_value(raw, U)[0] == want


def test_f5_dn_without_dot_is_not_a_declared_word():
    """Словарь не расширяется: «дн» без точки в перечне §12.6 нет."""
    assert fp.parse_value("1 дн", U) is None


# Сквозные контроли parser → применение (настоящий apply_answers)

@pytest.mark.parametrize("raw,want", [
    ("1 дн.", 1440), ("10 ч. 30 мин.", 630), ("10 ч. (600 минут)", 600),
])
def test_application_writes_cell_with_verbatim_quote_and_clears_blocker(
        raw, want):
    """K02-F1/F5 сквозь настоящий обработчик: ячейка с дословной
    цитатой (точка единицы — в цитате), вопрос применён, блокер снят,
    модель и цитатный слой валидны."""
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 1 and summary["unparsed"] == 0
    assert rule["template_fit"]["params"]["min_rest_outstation"] \
        == {"value": want, "quote": raw, "answer": QID,
            "source": _expected_answer_source(q, "min_rest_outstation", raw)}
    assert not m.is_open_question(q) and QID not in rule.get("blockers", [])
    assert m.validate_model(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []


@pytest.mark.parametrize("raw", [
    "12 ч. (600 минут)", "600 мин. (12 часов)", "1 сут. (60 минут)",  # F1
    "10:900 (600 минут)", "10:00x (600 минут)",                         # F2
    "-0 ч 30 мин", "−0 ч. 30 мин.",                                      # F3
    "0.099999999999999999999999999999 часа",                             # F4
])
def test_application_refusal_keeps_question_open_without_cell(raw):
    """Отказ разбора — не догадка: ячейка не пишется, вопрос открыт со
    статусом «не разобран», блокер носителя на месте."""
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert q["answer"]["status"] == "не разобран"
    assert m.validate_model(doc) == []


def test_application_note_names_domain_for_negative_compound_duration():
    """«-0 ч 30 мин» прочитан как −30 и отвергнут доменом: заметка
    называет область параметра, а не «не разобран как одно значение»."""
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: "-0 ч 30 мин"})
    assert "вне области параметра" in q["answer"]["note"]


# ── Неавторское ревью K02 (12.09.2026): замечания R1–R5, −/+ контроли ──
# Отрицательные случаи — дословно из ревью (раньше давали число, чужую
# единицу или исключение); положительные контроли — записи, которые
# обязаны читаться по-прежнему.

CH, HPCH, CNT = "credit_hours", "hours_per_credit_hour", "count"
#: Семья каталога 0.5.0 с параметром в часах кредита — для сквозных
#: контролей R2 на настоящем apply_answers.
QID_CH = "Q-3b-параметр-balanced_workload-workload_spread_threshold"


def _prepared_credit_hours():
    """Свод образца с посадкой семьи balanced_workload без параметра:
    вопрос о часах кредита открыт, носитель заблокирован."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {
        "category": "A", "registry_rev": m.registry_fingerprint(),
        "family": "balanced_workload", "params": {}}
    assert fp.ensure_family_param_questions(doc) == [QID_CH]
    q = next(x for x in doc["questions"] if x["id"] == QID_CH)
    assert QID_CH in rule["blockers"] and m.is_open_question(q)
    return doc, rule, q


# K02-R1 — непомерный токен: отказ, а не исключение и не 10^400 минут

@pytest.mark.parametrize("unit,raw", [
    (U, "1" + "0" * 400 + " минут"),          # раньше: 10^400 минут в посадку
    (U, "1" + "0" * 5000 + " минут"),         # раньше: ValueError на str(int)
    (CH, "1" + "0" * 400 + ".5"),             # раньше: OverflowError на float
    (CNT, "1" * 25),                          # 25 значащих цифр
    (U, "0," + "0" * 24 + "1 часа"),          # 25 цифр дробной части
    (U, "-" + "9" * 30 + " минут"),
])
def test_r1_immense_token_is_unreadable_and_refuses(unit, raw):
    """Длина токена — часть читаемости, закон один для всех единиц: в
    токене не больше 24 значащих цифр, иначе отказ. Ни одна величина
    авиакомпании столько не требует, а разбор обязан оставаться
    разбором: раньше такой токен либо вносился в посадку как 10^400
    минут, либо ронял исключением перевод величины в число."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.parse_value(raw, unit) is None


@pytest.mark.parametrize("unit,raw,want", [
    (CNT, "1" * 24, int("1" * 24)),           # ровно предел — читается
    (U, "9" * 24 + " минут", int("9" * 24)),
    (U, "010 часов", 600),                    # ведущие нули не значащие
    (U, "0." + "1" + "0" * 28 + " часа", 6),  # хвостовые нули не значащие
    (U, "600 минут", 600), (U, "10,5 часов", 630), (CH, "75", 75),
])
def test_r1_tokens_within_the_limit_are_read_as_before(unit, raw, want):
    """Положительный контроль предела: 24 значащие цифры читаются, а
    незначащие нули (ведущие у целой части, хвостовые у дробной) в счёт
    не идут — «010 часов» и «0,1000…0 часа» остаются прежними."""
    assert fp.parse_value(raw, unit)[0] == want


def test_r1_immense_token_does_not_break_the_application_stage():
    """Сквозной контроль: непомерный токен отказывает ОДНИМ ответом —
    вопрос остаётся открытым, ячейка не пишется, стадия применения
    ответов доходит до конца и свод сериализуется. Раньше «1 и 5000
    нулей» роняли ValueError на str(int) из заметки, то есть всю
    стадию, а не один ответ (в webui — через _stage_failure)."""
    import json
    for raw in ("1" + "0" * 5000 + " минут", "1" + "0" * 400 + " минут"):
        doc, rule, q = _prepared()
        summary = answers.apply_answers(doc, {QID: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert "min_rest_outstation" not in rule["template_fit"]["params"]
        assert m.is_open_question(q) and QID in rule["blockers"]
        assert q["answer"]["status"] == "не разобран"
        assert m.validate_model(doc) == []
        assert json.dumps(doc, ensure_ascii=False)


# K02-R2 — слово единицы при числе судится по единице параметра

@pytest.mark.parametrize("unit,raw", [
    (CH, "75 ч 30 мин"), (CH, "75 минут"), (CH, "4500 минут"),
    (CH, "75 мин"), (CH, "3 суток"), (CH, "75 часов 30 минут"),
    (HPCH, "1 ч 30 мин"), (HPCH, "1,5 минуты"),
    (CNT, "72 часа"), (CNT, "3 суток"), (CNT, "72 мин"), (CNT, "1 ч 30 мин"),
])
def test_r2_foreign_unit_word_next_to_a_number_refuses(unit, raw):
    """Слово единицы рядом с числом — часть записи значения, а не шум:
    оно судится по единице параметра, как и «ЧЧ:ММ рядом с числом».
    Чужое слово — чужая запись, отказ. Раньше слово молча отбрасывалось
    вместе со своим смыслом: «75 ч 30 мин» → 75, «75 минут» → 75 часов
    кредита, «4500 минут» → 4500, «72 часа» → счёт 72.

    Отказ при этом ИМЕНУЕТСЯ: третье ревью (K02-T2) потребовало, чтобы
    заметка называла само слово из ответа и что делать отвечающему, —
    здесь сверяется, что слово названо у каждой строки, а полная заметка
    — в test_t2_note_names_the_foreign_unit_word_*."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.parse_value(raw, unit) is None
    assert fp.foreign_unit_word(raw, unit) in raw.lower()
    assert fp.foreign_unit_word(raw, unit) != ""


@pytest.mark.parametrize("unit,raw,want", [
    (CH, "75 часов", 75), (CH, "75 часов кредита", 75), (CH, "75", 75),
    (CH, "75,5 ч", 75.5), (CH, "0,1", 0.1),
    (HPCH, "1,5", 1.5), (HPCH, "1,5 часа", 1.5),
    (CNT, "3", 3), (CNT, "3 смены", 3), (CNT, "не более 3 смен подряд", 3),
])
def test_r2_canonical_or_absent_unit_word_is_read(unit, raw, want):
    """Оборотная сторона: каноническое слово единицы параметра («час»
    у часов кредита) и запись без слова читаются по-прежнему — закон
    судит слово, а не запрещает его."""
    assert fp.parse_value(raw, unit)[0] == want


@pytest.mark.parametrize("raw", [
    "75 ч 30 мин", "75 минут", "4500 минут",
])
def test_r2_foreign_unit_word_refuses_through_the_real_application(raw):
    """Сквозной контроль на настоящем apply_answers и параметре
    каталога 0.5.0 (balanced_workload.workload_spread_threshold, часы
    кредита): отказ — не догадка, ячейка не пишется, вопрос открыт."""
    doc, rule, q = _prepared_credit_hours()
    summary = answers.apply_answers(doc, {QID_CH: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CH in rule["blockers"]
    assert q["answer"]["status"] == "не разобран"


@pytest.mark.parametrize("raw,want", [
    ("75 часов", 75), ("75", 75), ("75,5", 75.5),
])
def test_r2_credit_hours_value_applies_through_the_real_application(raw, want):
    """Положительный сквозной контроль того же пути: каноническая
    запись вносится в посадку с дословной цитатой, блокер снят, свод
    валиден."""
    doc, rule, q = _prepared_credit_hours()
    assert answers.apply_answers(doc, {QID_CH: raw})["applied"] == 1
    assert rule["template_fit"]["params"]["workload_spread_threshold"] \
        == {"value": want, "quote": raw, "answer": QID_CH,
            "source": _expected_answer_source(q, "workload_spread_threshold", raw)}
    assert not m.is_open_question(q) and QID_CH not in rule["blockers"]
    assert m.validate_model(doc) == []


# K02-R3 — заметка отказа называет причину

@pytest.mark.parametrize("raw,named", [
    ("по п.4.2 — 600 минут", "4.2"),
    ("600 мин (10 часов) согласно п. 4.2", "4.2"),
    ("600 мин (10 часов) согласно п. 4.2.", "4.2"),
    ("06:00 UTC+3", "3"),
    ("для A320 — 600 минут", "320"),
    ("с 01.10.2026 — 600 минут", "01.10.2026"),
    ("10:900 (600 минут)", "10:900"),
    ("10:00x (600 минут)", "10:00x"),
])
def test_r3_note_names_the_digits_left_outside_the_value(raw, named):
    """Отказ по закону охвата цифр — безопасная сторона, но человек
    обязан узнать причину: ссылка на пункт, обозначение с цифрами,
    дата, часовой пояс, повреждённая запись времени НАЗЫВАЮТСЯ в
    заметке. Иначе авиакомпания видит «укажите одно значение» на ответ,
    в котором значение названо."""
    assert fp.unread_digits(raw, U) == [named]
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: raw})
    note = q["answer"]["note"]
    assert "цифры вне числового выражения" in note and f"„{named}“" in note
    assert q["answer"]["status"] == "не разобран"
    assert "min_rest_outstation" not in rule["template_fit"]["params"]


@pytest.mark.parametrize("raw", [
    "10 часов (500 минут)", "12:00 (600 минут)", "2,25 минуты",
    "1" + "0" * 400 + " минут", "Зависит от типа рейса.",
    "10-12 часов",
])
def test_r3_other_causes_keep_the_former_note(raw):
    """Оборотная сторона: причина другая (несогласные записи, нецелые
    минуты, непомерный токен, значения нет) — заметка прежняя, цифры не
    называются. Кроме диапазона «10-12 часов»: там цифры и правда не
    прочитаны ни одной записью длительности — и называется вся склейка
    «10-12», как её написала авиакомпания (N1), а не «10» и «12»
    порознь, которых в ответе нет."""
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: raw})
    note = q["answer"]["note"]
    if raw == "10-12 часов":
        assert fp.unread_digits(raw, U) == ["10-12"]
        assert "цифры вне числового выражения" in note
    else:
        assert fp.unread_digits(raw, U) == []
        assert "цифры вне числового выражения" not in note
        assert "укажите одно значение" in note


@pytest.mark.parametrize("raw,want,quote", [
    ("Отдых вне базы — 600 минут. Основание — п. 4.2.", 600,
     "Отдых вне базы — 600 минут"),
])
def test_r3_value_in_its_own_sentence_reads_despite_a_reference(
        raw, want, quote):
    """Граница закона: цифры ссылки или обозначения мешают только в
    ТОМ ЖЕ предложении, что значение; отдельным предложением значение
    читается как прежде."""
    assert fp.parse_value(raw, U) == (want, quote)


def test_r3_closing_quote_after_a_dot_blocks_the_split_safely():
    """Известная граница деления (правится не здесь): закрывающая
    кавычка или скобка ПОСЛЕ точки не даёт разделить предложения —
    запись остаётся одной, и разные величины в ней дают отказ. Сторона
    безопасная (значение не выдумано), поведение записано в руководстве
    как граница, а не как обещание."""
    raw = "«Отдых вне базы — 10 часов.» Дома — 12 часов."
    assert len(fp._sentences(raw)) == 1
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value("Отдых вне базы — 10 часов. Дома — 12 часов.",
                          U) == (600, "Отдых вне базы — 10 часов")


# K02-R4 — повреждённый ответ не несёт и решения «не применяется»

@pytest.mark.parametrize("raw", [
    "не применяется\x00", "Не применимо.\x1b[0m", "\x07такого требования нет",
])
def test_r4_damaged_answer_does_not_decline_the_parameter(raw):
    """Ответ, повреждённый управляющим знаком, не даёт ни предложений,
    ни цитаты, ни значения — и решения тоже не даёт: «не применяется»
    судится по первому ПРЕДЛОЖЕНИЮ, как значение и цитата. Раньше такой
    ответ снимал вопрос как решённый — самый дорогой исход разбора."""
    assert fp.is_declined(raw) is False
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]


@pytest.mark.parametrize("raw", [
    "не применяется", "Не применимо.", "такого требования нет",
    # Ожидание менялось (B1, пятое неавторское ревью): было «Не
    # применяется. Отдых задаёт свод.» — ответ с ПОЯСНЯЮЩИМ вторым
    # предложением; теперь безусловен только ответ, ВЕСЬ состоящий из
    # фраз отказа (см. test_b1_*). Контроль многопредложенного отказа
    # остаётся — но из одних фраз отказа.
    "Не применяется. Нет такого требования.",
])
def test_r4_clean_decline_still_closes_the_question(raw):
    """Положительный контроль: неповреждённый отказ от параметра
    применяется как решение авиакомпании и снимает блокер."""
    assert fp.is_declined(raw) is True
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["applied"] == 1
    assert q["answer"]["status"] == "применён"
    assert not m.is_open_question(q) and QID not in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]


# K02-R5 — ответ есть текст; не текст — не ответ

@pytest.mark.parametrize("raw", [
    ["600 минут"], ("600 минут",), {"value": "600 минут"}, 600, 600.0,
    b"600 minut", None,
])
def test_r5_non_text_answer_gives_no_sentences_value_or_quote(raw):
    """Ответ авиакомпании — текст: цитата обязана быть его дословной
    подстрокой. Не текст разбирался через str(): список
    ['600 минут'] давал 600 с «цитатой» "['600 минут']", которой в
    ответе нет. Не текст — ни предложений, ни цитаты, ни значения, ни
    решения."""
    assert fp._sentences(raw) == []
    assert fp.answer_quote(raw) == ""
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value(raw, CNT) is None
    assert fp.is_declined(raw) is False


def test_r5_the_same_answer_as_text_is_read():
    """Положительный контроль: тот же ответ строкой читается."""
    assert fp.parse_value("600 минут", U) == (600, "600 минут")
    assert fp.parse_value("600", CNT) == (600, "600")
    assert fp.is_declined("не применяется") is True


# ── Третье неавторское ревью K02 (12.09.2026): замечания T1–T4 ─────────
# T1 — закон длины судит КАЖДУЮ группу цифр записи, не только токен
# числа; T2 — отказ по чужому слову единицы имеет свою заметку; T3 —
# закон охвата цифр называет записи только там, где грамматика отличает
# запись значения от посторонней; T4 — разбор линеен по длине ответа.

TOD = "time_of_day_minutes"
#: Семья каталога 0.5.0 со СЧЁТНЫМ параметром, считающим сутки, — для
#: сквозных контролей T2 на настоящем apply_answers.
QID_CNT = "Q-3b-параметр-days_off_floor-days_off_window_days"


def _prepared_count():
    """Свод образца с посадкой семьи days_off_floor, где задана норма
    выходных в окне, но не длина окна: вопрос счётного параметра открыт,
    носитель заблокирован."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {
        "category": "A", "registry_rev": m.registry_fingerprint(),
        "family": "days_off_floor",
        "params": {"min_days_off_in_window": {"value": 2,
                                              "quote": "две в неделю"}}}
    assert fp.ensure_family_param_questions(doc) == [QID_CNT]
    q = next(x for x in doc["questions"] if x["id"] == QID_CNT)
    assert QID_CNT in rule["blockers"] and m.is_open_question(q)
    return doc, rule, q


# K02-T1 — одна длина, один закон: каждая группа цифр записи

def test_t1_immense_compound_minutes_do_not_break_the_application_stage():
    """Близнец test_r1_immense_token_does_not_break_the_application_stage
    для МИНУТНОЙ ЧАСТИ составной записи «N ч M мин». Её группа цифр
    переводилась голым int(), закон предела её не судил, и «10 ч <4301
    ноль> мин» роняло ValueError («Exceeds the limit (4300 digits)») —
    не один ответ, а всю стадию применения ответов, тот самый класс
    R1, который считался закрытым для группы числа.

    Теперь запись судится тем же законом: непомерная группа — отказ
    одним ответом (вопрос открыт, ячейка не пишется, свод валиден и
    сериализуется), а ведущие нули значащими цифрами не считаются,
    поэтому «10 ч <4301 ноль> мин» читается как 10 ч 0 мин = 600 —
    ровно то же, что давали 4300 нулей до правки, но без исключения."""
    import json
    for raw in ("10 ч " + "9" * 5000 + " мин",
                "10 ч " + "9" * 4301 + " мин",
                "10 ч " + "1" * 25 + " мин"):
        doc, rule, q = _prepared()
        summary = answers.apply_answers(doc, {QID: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert "min_rest_outstation" not in rule["template_fit"]["params"]
        assert m.is_open_question(q) and QID in rule["blockers"]
        assert q["answer"]["status"] == "не разобран"
        assert m.validate_model(doc) == []
        assert json.dumps(doc, ensure_ascii=False)
    for raw in ("10 ч " + "0" * 4301 + " мин",
                "10 ч " + "0" * 4300 + " мин"):
        doc, rule, q = _prepared()
        assert answers.apply_answers(doc, {QID: raw})["applied"] == 1
        assert rule["template_fit"]["params"]["min_rest_outstation"][
            "value"] == 600
        assert not m.is_open_question(q) and QID not in rule["blockers"]
        assert m.validate_model(doc) == []
        assert json.dumps(doc, ensure_ascii=False)


@pytest.mark.parametrize("raw,want", [
    ("10 ч 30 мин", 630), ("10 ч 0 мин", 600), ("10 ч 59 мин", 659),
    ("10 ч " + "0" * 4300 + " мин", 600),     # ведущие нули не значащие
    ("10 ч " + "0" * 24 + "30 мин", 630),
])
def test_t1_readable_compound_minutes_keep_their_value(raw, want):
    """Положительный контроль предела у минутной части: группа читается,
    пока в ней не больше 24 ЗНАЧАЩИХ цифр, — ведущие нули величины не
    добавляют и в счёт не идут, как и у токена числа."""
    assert fp.parse_value(raw, U)[0] == want


@pytest.mark.parametrize("raw", [
    "10 ч " + "1" * 25 + " мин",               # 25 значащих цифр
    "10 ч " + "9" * 4301 + " мин",             # раньше: ValueError
    "10 ч 60 мин",                             # прежний закон 0–59
    "10 ч 90 мин",
])
def test_t1_unreadable_compound_minutes_refuse(raw):
    """Оборотная сторона: непомерная минутная часть отказывает разбором
    (None), а прежний закон «минуты целые и 0–59» остаётся на месте."""
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None


def test_t1_the_length_law_is_one_helper_for_every_digit_group():
    """Закон длины — ОДИН помощник на все группы цифр модуля: предел
    судится по значащим цифрам, непомерная группа отказывает (None), а
    не роняет исключением. Границу сверяем прямо у помощника: 24
    значащие цифры — читаются, 25 — нет, сколько угодно ведущих нулей —
    ноль значащих цифр и величина 0 (int() на строке длиннее 4300 цифр
    сам по себе исключение, поэтому нули снимаются до перевода)."""
    assert fp._group_digits("1" * 24) == int("1" * 24)
    assert fp._group_digits("1" * 25) is None
    assert fp._group_digits("0" * 5000) == 0
    assert fp._group_digits("0" * 5000 + "7") == 7
    assert fp._group_digits("0") == 0


def test_t1_no_digit_group_is_converted_outside_the_one_law():
    """Сторожевой контроль закона: ни одна группа регулярки в модуле не
    переводится в число напрямую (int/float/Decimal/Fraction от
    `…group(…)`) — только через _group_digits или _magnitude, где стоит
    предел длины. Иначе новая группа цифр снова уронит стадию."""
    import inspect
    import re as _re
    src = inspect.getsource(fp)
    bad = _re.findall(
        r"\b(?:int|float|Decimal|Fraction)\(\s*[A-Za-z_]\w*\.group\(",
        src)
    assert bad == []


@pytest.mark.parametrize("raw,unit,want", [
    ("10:30", U, 630), ("999:59", U, 59999), ("22:00", TOD, 1320),
    ("00:00", TOD, 0),
])
def test_t1_hhmm_groups_read_the_same_through_the_one_law(raw, unit, want):
    """Группы записи ЧЧ:ММ тоже переведены на общий закон; форма держит
    их короткими (часы — до трёх цифр, минуты — ровно две), поэтому
    значения прежние — помощник стоит там ради одного закона, а не ради
    нового поведения."""
    assert fp.parse_value(raw, unit)[0] == want


# K02-T2 — отказ по чужому слову единицы называет слово и что делать

def test_t2_note_names_the_foreign_unit_word_at_a_count_parameter():
    """Сквозной контроль на настоящем apply_answers и счётном параметре
    каталога 0.5.0 (days_off_floor.days_off_window_days — длина окна в
    сутках): «7 суток» отказывает, как и прежде (эвристик по имени
    параметра не будет), но заметка теперь НАЗЫВАЕТ слово из ответа и
    говорит, что делать. Раньше отвечающий видел общее «укажите одно
    значение» на ответ, где значение названо, и вопрос вставал."""
    doc, rule, q = _prepared_count()
    summary = answers.apply_answers(doc, {QID_CNT: "7 суток"})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "days_off_window_days" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    note = q["answer"]["note"]
    assert "слово «суток»" in note and "счётного параметра" in note
    assert "укажите число без слова единицы" in note
    assert "цифры вне числового выражения" not in note
    assert m.validate_model(doc) == []


def test_t2_note_names_the_foreign_unit_word_at_credit_hours():
    """То же у часов кредита: чужое слово длительности названо, и
    сказано, в чём указать значение."""
    doc, rule, q = _prepared_credit_hours()
    assert answers.apply_answers(doc, {QID_CH: "75 минут"})["unparsed"] == 1
    note = q["answer"]["note"]
    assert "слово «минут»" in note and "часов кредита" in note
    assert "укажите значение в часах" in note
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CH in rule["blockers"]


@pytest.mark.parametrize("unit,raw,word", [
    (CNT, "7 суток", "суток"), (CNT, "3 дня", "дня"), (CNT, "72 часа", "часа"),
    (CNT, "72 мин", "мин"), (CNT, "1 ч 30 мин", "ч"),
    (CH, "75 минут", "минут"), (CH, "75 ч 30 мин", "мин"),
    (CH, "3 суток", "суток"), (HPCH, "1,5 минуты", "минуты"),
])
def test_t2_the_named_word_is_the_word_from_the_answer(unit, raw, word):
    """Называется ИМЕННО то слово, что стоит при числе в ответе (не
    каноническое имя единицы): у счёта — первое слово записи, у часов
    кредита — чужая часть составной записи («75 ч 30 мин» → «мин»)."""
    assert fp.foreign_unit_word(raw, unit) == word


@pytest.mark.parametrize("raw,want", [
    ("8 выходных дней", 8), ("3 смены", 3), ("не более 3 смен подряд", 3),
    ("2 посадки", 2),
])
def test_t2_a_counted_noun_next_to_a_number_is_not_a_unit_word(raw, want):
    """Граница закона, записанная и в руководстве: слово единицы стоит
    ВПЛОТНУЮ к числу. «8 выходных дней» — существительное при числе, а
    не единица («дней» отделено словом «выходных»), поэтому счёт
    читается как 8 и заметки о чужом слове нет."""
    assert fp.parse_value(raw, CNT) == (want, raw)
    assert fp.foreign_unit_word(raw, CNT) == ""


@pytest.mark.parametrize("unit,raw", [
    (U, "3 суток"), (U, "75 минут"), (U, "10 ч 30 мин"), (TOD, "22:00"),
    (CNT, "3"), (CH, "75 часов"), (HPCH, "1,5 часа"),
    (CNT, "Зависит от графика."), (CNT, "10:30"),
])
def test_t2_no_foreign_word_where_the_record_is_canonical(unit, raw):
    """Оборотная сторона: у длительности слово единицы называет предмет
    величины и чужим не бывает; каноническое слово своей единицы и
    запись без слова — тоже не повод для этой заметки. Пусто и там, где
    причина отказа другая («10:30» у счёта — чужая ЗАПИСЬ, не слово)."""
    assert fp.foreign_unit_word(raw, unit) == ""


# K02-T3 — закон охвата называет записи там, где отличает их от значения

@pytest.mark.parametrize("unit,named", [
    (CNT, []), (CH, []), (HPCH, []),
    (U, ["3", "4.2"]), (TOD, ["3", "4.2"]),
])
def test_t3_a_reference_at_count_is_a_second_value_not_unread_digits(
        unit, named):
    """Закон, который теперь записан и в коде, и в руководстве: цифры
    вне выражения называются там, где грамматика отличает запись
    значения от посторонней — у длительности её метит слово единицы, у
    времени суток форма ЧЧ:ММ. У счёта и часов кредита запись значения
    — САМО ЧИСЛО, поэтому «3 (п. 4.2)» читается как два разных значения
    (у счёта ещё и нецелых) и отказывает своей причиной, а не «цифрами
    вне числового выражения». Догадки «это ссылка» здесь нет и не
    будет: код не знает, где ссылка, а где значение."""
    assert fp.unread_digits("3 (п. 4.2)", unit) == named
    assert fp.parse_value("3 (п. 4.2)", unit) is None


def test_t3_the_count_note_says_what_to_do_without_naming_digits():
    """Сквозной контроль той же границы: заметка счётного параметра на
    «3 (п. 4.2)» — общая («укажите одно значение»), цифры не называет и
    ничего не обещает сверх того, что код умеет."""
    doc, rule, q = _prepared_count()
    assert answers.apply_answers(doc, {QID_CNT: "3 (п. 4.2)"})["unparsed"] == 1
    note = q["answer"]["note"]
    assert "укажите одно значение" in note
    assert "цифры вне числового выражения" not in note
    assert "слово" not in note
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]


def test_t3_duration_still_names_the_reference_digits():
    """Положительный контроль другой стороны границы: у длительности
    ссылка по-прежнему названа — обещание руководства там в силе."""
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: "по п.4.2 — 600 минут"})
    assert "цифры вне числового выражения" in q["answer"]["note"]
    assert "„4.2“" in q["answer"]["note"]


# K02-T4 — разбор линеен по длине ответа
# Стоимость проверяется линейным бюджетом операций, не порогом по
# настенным часам. Прежние входы и функциональные ожидания сохранены.
# Чувствительность к двум квадратичным алгоритмам отдельно проверяет
# test_cdpm_k02_work_budget.py; времена ниже — исторические наблюдения,
# не обещание задержки на любой машине.

@pytest.mark.parametrize("raw,unit", [
    ("3 " + "1 " * 8000, CNT),                 # ревизорская проба: ≈ 22 с
    ("3 " + "1 " * 8000, U),
    (" ".join(str(i) for i in range(4000)), CNT),   # все числа разные
])
def test_t4_a_long_answer_is_parsed_and_noted_in_linear_time(raw, unit, k02_work_budget):
    """Ответ в 16 КБ — обычная ячейка xlsx, а не диверсия. Охват цифр
    судится по отметке позиций (одна на предложение), поэтому разбор и
    заметка стоят доли секунды. Раньше перебор «запись × цифра ×
    граница» стоил ≈ 22 с при разборе и ещё ≈ 22 с при заметке — анкета
    вставала на одном ответе."""
    assert len(raw) >= 16000
    with k02_work_budget(raw):
        assert fp.parse_value(raw, unit) is None
    with k02_work_budget(raw):
        assert isinstance(fp.unread_digits(raw, unit), list)


def test_t4_the_same_long_answer_through_the_real_application_stage(k02_work_budget):
    """Сквозной контроль того же: стадия применения ответов проходит
    длинный ответ насквозь — отказ одним ответом, вопрос открыт."""
    doc, rule, q = _prepared()
    raw = "3 " + "1 " * 8000
    with k02_work_budget(raw):
        summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID in rule["blockers"]


# ═════════════ Четвёртое неавторское ревью K02 (12.09.2026) ═════════════

QID_NS = "Q-3b-параметр-night_series-night_duty_end"


def _prepared_night_series():
    """Свод образца с посадкой night_series, где заданы серия подряд и
    начало ночного окна: открыт вопрос КОНЦА окна — время суток. Это
    единственный в каталоге 0.5.0 параметр-«время суток», и сквозные
    контроли времени суток идут через него."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {
        "category": "A", "registry_rev": m.registry_fingerprint(),
        "family": "night_series",
        "params": {
            "max_consecutive_night_duties": {"value": 3,
                                             "quote": "три подряд"},
            "night_duty_start": {"value": 1320, "quote": "с 22:00"}}}
    assert fp.ensure_family_param_questions(doc) == [QID_NS]
    q = next(x for x in doc["questions"] if x["id"] == QID_NS)
    assert QID_NS in rule["blockers"] and m.is_open_question(q)
    return doc, rule, q


# N1 — граница токена СИММЕТРИЧНА: приклеенный справа знак делает
# запись нечитаемой так же, как приклеенный слева.

@pytest.mark.parametrize("unit,raw,record", [
    (CH, "75-ти минут", "75-ти"),      # ревизор: вносилось 75 часов кредита
    (CH, "75-ти часов", "75-ти"),
    (CH, "75-ти", "75-ти"),
    (CNT, "7-ми суток", "7-ми"),       # ревизор: счёт 7 молча, без заметки
    (CNT, "7-суток", "7-суток"),
    (CNT, "72-х часов", "72-х"),
    (CNT, "не более 72-х часов", "72-х"),
    (CNT, "3-х смен", "3-х"),
    (CNT, "3-х", "3-х"),
    (CNT, "7-дневное окно", "7-дневное"),
    (CNT, "7+ суток", "7+"),
    (CNT, "7+", "7+"),
    (CNT, "7- суток", "7-"),
    (CNT, "7–суток", "7–суток"),       # тире U+2013
    (CNT, "7−ми", "7−ми"),             # знак минуса U+2212
    (CNT, "7—суток", "7—суток"),       # тире U+2014
    (CNT, "10-12", "10-12"),
    (HPCH, "1,5-х минут", "1,5-х"),
    (U, "10-ти часов", "10-ти"),
    (U, "600-та минут", "600-та"),
    (U, "10-12 часов", "10-12"),
    (TOD, "6-ти утра", "6-ти"),
])
def test_n1_a_sign_glued_to_the_right_of_the_digits_is_unreadable(
        unit, raw, record):
    """Дефис, минус, плюс или тире ВПЛОТНУЮ к цифрам справа — тот же
    закон, что слева: запись нечитаема, значения нет (fail-closed).

    Раньше правая граница запрещала лишь точку/запятую/двоеточие с
    цифрой, и знак в приклейке молча отваливался: «75-ти минут»
    вносилось как 75 часов кредита (75 минут, названные авиакомпанией,
    становились 75 часами), «7-ми суток» — как счёт 7 там, где «7
    суток» отказывает по слову единицы. Заметка называет ВСЮ склейку —
    «75-ти», а не «75»: отвечающий видит свою запись, а не выдуманную
    грамматикой."""
    assert fp.parse_value(raw, unit) is None
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.unread_digits(raw, unit) == [record]


@pytest.mark.parametrize("raw,record", [
    ("7-ми суток", "7-ми"), ("7+ суток", "7+"), ("10-12", "10-12"),
    ("7—суток", "7—суток"),
])
def test_n1_the_glued_record_refuses_through_the_real_application(
        raw, record):
    """Сквозной контроль на настоящем apply_answers и счётном параметре
    каталога 0.5.0: вопрос остаётся открытым, носитель заблокирован,
    заметка называет склейку из ответа, а не голое число."""
    doc, rule, q = _prepared_count()
    summary = answers.apply_answers(doc, {QID_CNT: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "days_off_window_days" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    note = q["answer"]["note"]
    assert "цифры вне числового выражения" in note and f"„{record}“" in note
    assert "„7“" not in note and "„10“" not in note
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("raw,record", [
    ("10-ти часов", "10-ти"), ("600-та минут", "600-та"),
    ("10-12 часов", "10-12"),
])
def test_n1_the_glued_record_refuses_at_duration_too(raw, record):
    """Та же граница у длительности — единицы двух правил-носителей
    набора Н0-B."""
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["unparsed"] == 1
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert f"„{record}“" in q["answer"]["note"]
    assert m.validate_model(doc) == []


def test_n1_the_glued_record_refuses_at_time_of_day_too():
    """И у времени суток: «6-ти утра» — не 06:00 и не 6."""
    doc, rule, q = _prepared_night_series()
    assert answers.apply_answers(doc, {QID_NS: "6-ти утра"})["unparsed"] == 1
    assert "night_duty_end" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID_NS in rule["blockers"]
    assert "„6-ти“" in q["answer"]["note"]
    assert m.validate_model(doc) == []


def test_n1_the_glued_record_refuses_at_credit_hours_too():
    """То же у часов кредита — там приклейка была опаснее всего: «75-ти
    минут» вносилось как 75 ЧАСОВ кредита (минуты, названные в ответе,
    молча становились часами)."""
    doc, rule, q = _prepared_credit_hours()
    assert answers.apply_answers(doc, {QID_CH: "75-ти минут"})["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CH in rule["blockers"]
    note = q["answer"]["note"]
    assert "„75-ти“" in note and "„75“" not in note


@pytest.mark.parametrize("unit,raw,want", [
    (CNT, "7", 7), (CNT, "8 выходных дней", 8), (CNT, "3 смены", 3),
    (CNT, "-30", -30), (CNT, "+7", 7), (CNT, "3,0", 3),
    (U, "600 минут", 600), (U, "-30 минут", -30), (U, "10:30", 630),
    (U, "10,5 часов", 630), (U, "-0 ч 30 мин", -30), (U, "10 ч 30 мин", 630),
    (TOD, "10:30", 630), (CH, "75", 75), (CH, "75 часов", 75),
    (HPCH, "1,5", 1.5),
])
def test_n1_bare_numbers_and_the_left_side_law_are_untouched(
        unit, raw, want):
    """Оборотная сторона: голое число читается как прежде, а знак СЛЕВА
    остаётся частью токена — «-30» → −30, «+7» → 7 (он там и объявлен
    грамматикой). Закон запрещает приклейку, а не знак."""
    got = fp.parse_value(raw, unit)
    assert got is not None and got[0] == want


def test_n1_a_unit_word_after_a_space_still_refuses_by_its_own_law():
    """Граница между двумя законами: «7 суток» — слово единицы при
    числе (K02-R2), отказ со СВОЕЙ заметкой; «7-ми суток» — склейка,
    отказ по границе токена. Причины разные, и заметки разные."""
    assert fp.parse_value("7 суток", CNT) is None
    assert fp.foreign_unit_word("7 суток", CNT) == "суток"
    assert fp.unread_digits("7 суток", CNT) == []
    assert fp.foreign_unit_word("7-ми суток", CNT) == ""
    assert fp.unread_digits("7-ми суток", CNT) == ["7-ми"]


# N2 — заметка о непрочитанных записях: без квадрата и без предела

def test_n2_the_note_names_the_first_records_and_counts_the_rest():
    """Заметка — сообщение человеку, а не выгрузка: ответ из 4000
    разных чисел давал заметку в 47 КБ (она ложится в свод, в журнал
    следствий и в выгрузки). Теперь названы первые 12, остальные
    сосчитаны."""
    raw = " ".join(str(i) for i in range(4000))
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: raw})
    note = q["answer"]["note"]
    assert "„0“" in note and "„11“" in note and "„12“" not in note
    assert "и ещё 3988" in note
    assert len(note) < 600, f"заметка {len(note)} знаков"
    assert m.is_open_question(q) and QID in rule["blockers"]


def test_n2_the_note_of_a_180kb_answer_of_distinct_numbers_is_linear(k02_work_budget):
    """Ревизорская проба: 32 000 РАЗНЫХ чисел (180 КБ). Повтор записи
    узнавался перебором уже названного списка — квадрат по числу разных
    записей: заметка стоила 5,1 с при разборе в 0,02 с. Множество
    вместо списка — та же заметка за доли секунды."""
    raw = " ".join(str(i) for i in range(32000))
    assert len(raw) >= 180000
    with k02_work_budget(raw):
        assert fp.parse_value(raw, U) is None
    with k02_work_budget(raw):
        stray = fp.unread_digits(raw, U)
    assert len(stray) == 32000 and stray[:3] == ["0", "1", "2"]


def test_n2_the_same_180kb_answer_through_the_real_application_stage(k02_work_budget):
    """Сквозной контроль того же на настоящем apply_answers: стадия
    проходит ответ насквозь, вопрос остаётся открытым, заметка
    ограничена."""
    doc, rule, q = _prepared()
    raw = " ".join(str(i) for i in range(32000))
    with k02_work_budget(raw):
        summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert len(q["answer"]["note"]) < 600


# В-3 (замечание ревью барьера K01) — отказ безусловен только сам по
# себе: условие или значение в том же предложении параметр не закрывают

#: Ответ авиакомпании из ревью барьера K01: отказ для ОДНОЙ базы и
#: значение для остальных. Раньше совпадения начала хватало, чтобы
#: закрыть параметр решением «не применяется» — для ВСЕХ баз.
K01_ANSWER = ("Не применяется для сотрудников базы SVO; "
              "в остальных базах 10:00")


def test_v3_a_conditional_decline_keeps_the_question_open():
    """Ответ ревью K01 на настоящем apply_answers: параметр НЕ закрыт,
    вопрос открыт, носитель заблокирован, семья не полна. Раньше
    is_declined совпадал началом предложения, ответ шёл в «применён», и
    параметр пропадал для всех баз — а «10:00», названное для
    остальных, не попадало никуда."""
    doc, rule, q = _prepared_night_series()
    summary = answers.apply_answers(doc, {QID_NS: K01_ANSWER})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "night_duty_end" not in rule["template_fit"]["params"]
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID_NS in rule["blockers"]
    assert "отказ и значение в одном предложении" in q["answer"]["note"]
    cov = fp.family_coverage(doc)["night_series"]
    assert "night_duty_end" not in cov["declined"]
    assert "night_duty_end" in cov["missing"]
    assert m.validate_model(doc) == []


def test_v3_the_same_answer_does_not_become_a_value_either():
    """Оборотная опасность того же ответа: у длительности «10:00»
    прочиталось бы как 600 минут. Ответ и отказывает, и называет
    величину — это противоречие, а не значение: «не разобран»."""
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: K01_ANSWER})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "отказ и значение в одном предложении" in q["answer"]["note"]
    assert fp.parse_value(K01_ANSWER, U) is None


@pytest.mark.parametrize("raw,tail", [
    (K01_ANSWER, "для сотрудников базы SVO; в остальных базах 10:00"),
    ("Не применяется, 600 минут", ", 600 минут"),
    ("Не применяется для базы SVO", "для базы SVO"),
    ("Нет такого требования, кроме ночных рейсов", ", кроме ночных рейсов"),
    ("Не применяется. 600 минут.", ". 600 минут"),
])
def test_v3_the_decline_with_a_tail_is_neither_decision_nor_value(raw, tail):
    """Предикат: безусловный отказ — ПЕРВОЕ предложение из одних фраз
    отказа. Хвост в том же предложении (условие, значение, «;»- или
    «,»-продолжение) — не отказ и не значение: вопрос открыт."""
    assert fp.is_declined(raw) is False
    assert fp.decline_tail(raw) == tail
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value(raw, TOD) is None
    assert fp.parse_value(raw, CNT) is None


@pytest.mark.parametrize("raw", [
    "Не применяется", "не применяется", "Не применимо.", "Неприменимо",
    "такого требования нет", "«Не применяется».", "Не применяется!",
    "Не применяется: такого требования у нас нет.",
    # Ожидания менялись (B1, пятое неавторское ревью): «Не применяется.
    # Основание — п. 4.2.», «Не применяется. Отдых задаёт свод.» и «Нет
    # такого требования. Есть 600 минут.» были здесь — отказ судился по
    # ПЕРВОМУ предложению, и пояснение считалось законным. Теперь
    # безусловен ВЕСЬ ответ из фраз отказа (§2.2: исключения не
    # сворачиваются в глобальное «не применяется»), и эти три ответа
    # проверяются как открытые (test_b1_*). На их месте — те же формы,
    # где ВСЕ предложения суть отказ.
    "Не применяется. Нет такого требования.",
    "Не применяется! Неприменимо. Такого требования нет.",
])
def test_v3_an_unconditional_decline_still_closes_the_parameter(raw):
    """Положительные контроли: безусловный отказ работает как прежде —
    и когда фраз отказа НЕСКОЛЬКО подряд предложениями, и когда в том
    же предложении стоит ДРУГАЯ фраза отказа («Не применяется: такого
    требования у нас нет» — тот же отказ, сказанный дважды)."""
    assert fp.is_declined(raw) is True and fp.decline_tail(raw) == ""
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["applied"] == 1
    assert q["answer"]["status"] == "применён"
    assert not m.is_open_question(q) and QID not in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    cov = fp.family_coverage(doc)["rest_floors"]
    assert "min_rest_outstation" in cov["declined"]


def test_v3_a_stored_conditional_decline_no_longer_counts_as_decided():
    """Свод, где условный отказ уже записан решением прежней выдачей:
    полнота считает параметр НЕзаданным (не «declined»), и значение из
    такого ответа самолечение тоже не берёт — полнотой такой ответ не
    засчитывается, а повторный вопрос по старому своду ставит пересмотр
    K04 (миграция волны 17б), не разбор."""
    doc, rule, q = _prepared()
    q["answer"] = {"raw": K01_ANSWER, "status": "применён", "revision": 1}
    cov = fp.family_coverage(doc)
    assert "min_rest_outstation" not in cov["rest_floors"]["declined"]
    assert "min_rest_outstation" in cov["rest_floors"]["missing"]
    assert fp._reapply_values(doc, cov) == []
    assert "min_rest_outstation" not in rule["template_fit"]["params"]


# ── Пятое неавторское ревью K02 (12.09.2026): замечания B1 и B2 ────────
# B1 — безусловный отказ есть ВЕСЬ ответ из фраз отказа: обычная
# русская пунктуация (точка, восклицательный знак, скобка перед
# прописной буквой) больше не снимает вопрос вместе с условием,
# значением или основанием, названными СЛЕДУЮЩИМ предложением.
# B2 — самолечение не заслоняется старым условным отказом, лежащим в
# своде решением прежней выдачи: значение из пересмотра K04 возвращается
# в посадку, а не теряется.

#: Ответы, где отказ назван не сам по себе: рядом стоит условие,
#: значение или основание — ПРЕДЛОЖЕНИЕМ ПОЗЖЕ. Судилось одно первое
#: предложение, поэтому каждый из них давал статус «применён»,
#: declined=[параметр] и снятие блокировок: условие и значение,
#: названные авиакомпанией, пропадали молча.
B1_ANSWERS = [
    "Не применяется. Кроме базы SVO.",
    "Не применяется. В остальных базах 10:00.",
    "Не применяется. Для SVO — 600 минут.",
    "Не применяется! Кроме SVO.",
    "Не применяется. (Для SVO — 10:00.)",
    "Не применяется. Основание — п. 4.2.",
]


@pytest.mark.parametrize("raw", B1_ANSWERS)
def test_b1_a_decline_beside_another_sentence_is_neither_decision_nor_value(
        raw):
    """Предикат B1 на длительности, сквозным путём (настоящий
    apply_answers): ответ не решение и не значение — «не разобран»,
    вопрос открыт, носитель заблокирован, семья не полна, а заметка
    называет закон («безусловным отказом считается ВЕСЬ ответ из фраз
    отказа») и говорит, как ответить."""
    assert fp.is_declined(raw) is False and fp.decline_tail(raw) != ""
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    note = q["answer"]["note"]
    assert "безусловным отказом считается ВЕСЬ ответ из фраз отказа" in note
    assert "назовите отказ отдельным ответом" in note
    cov = fp.family_coverage(doc)["rest_floors"]
    assert "min_rest_outstation" not in cov["declined"]
    assert "min_rest_outstation" in cov["missing"]
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("raw", [
    "Не применяется. В остальных базах 10:00.",
    "Не применяется. Кроме базы SVO.",
])
def test_b1_the_same_law_holds_for_time_of_day(raw):
    """Та же проверка на ВТОРОЙ единице — времени суток (night_series):
    «10:00» следующим предложением раньше и снимало вопрос как решённый,
    и одновременно читалось разбором как 600 — один ответ давал то
    отказ, то значение."""
    assert fp.parse_value(raw, TOD, fp.default_domain(TOD)) is None
    doc, rule, q = _prepared_night_series()
    summary = answers.apply_answers(doc, {QID_NS: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID_NS in rule["blockers"]
    assert "night_duty_end" not in rule["template_fit"]["params"]
    assert "назовите отказ отдельным ответом" in q["answer"]["note"]
    cov = fp.family_coverage(doc)["night_series"]
    assert "night_duty_end" not in cov["declined"] \
        and "night_duty_end" in cov["missing"]
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("lower,upper", [
    ("не применяется. кроме SVO", "Не применяется. Кроме SVO."),
    ("не применяется. в остальных базах 10:00",
     "Не применяется. В остальных базах 10:00."),
])
def test_b1_the_verdict_no_longer_depends_on_the_case_of_the_next_sentence(
        lower, upper):
    """Решение авиакомпании не зависит от того, начала ли она вторую
    половину ответа с прописной буквы. Раньше зависело: строчная не
    рвала предложение — вопрос оставался открытым, прописная рвала — и
    тот же ответ закрывал параметр «не применяется» для всех баз."""
    assert fp.is_declined(lower) is False
    assert fp.is_declined(upper) is False
    assert fp.decline_tail(lower) != "" and fp.decline_tail(upper) != ""
    for unit in (U, TOD, CNT):
        assert fp.parse_value(lower, unit) is None
        assert fp.parse_value(upper, unit) is None


@pytest.mark.parametrize("raw,tail", [
    ("Не применяется. Кроме базы SVO.", "Кроме базы SVO"),
    ("Не применяется. В остальных базах 10:00.", "В остальных базах 10:00"),
    ("Не применяется! Кроме SVO.", "Кроме SVO"),
    ("Не применяется. Основание — п. 4.2.", "Основание — п. 4.2"),
    ("Не применяется. (Для SVO — 10:00.)", "(Для SVO — 10:00.)"),
    ("Не применяется. Не применяется для SVO.", "Не применяется для SVO"),
])
def test_b1_decline_tail_names_the_first_non_decline_sentence(raw, tail):
    """Помеха безусловности называется целиком — ПЕРВЫМ предложением,
    которое отказом не является (в том числе отказом со своим хвостом):
    по ней применение ответа выбирает заметку человеку."""
    assert fp.decline_tail(raw) == tail


@pytest.mark.parametrize("raw", [
    "Не применяется.", "не применяется", "Неприменимо!",
    "Не применяется. Нет такого требования.",
    "Не применяется! Неприменимо. Такого требования нет.",
    "Нет такого требования. Не применяется.",
])
def test_b1_an_answer_made_only_of_decline_phrases_is_still_a_decision(raw):
    """Положительный контроль B1: отказ, СКАЗАННЫЙ ОДИН, — решение
    авиакомпании, сколькими бы предложениями он ни был сказан. Закон
    fail-closed сужает отказ, но не отменяет его."""
    assert fp.is_declined(raw) is True and fp.decline_tail(raw) == ""
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["applied"] == 1
    assert q["answer"]["status"] == "применён"
    assert not m.is_open_question(q) and QID not in rule["blockers"]
    assert "min_rest_outstation" in fp.family_coverage(
        doc)["rest_floors"]["declined"]


@pytest.mark.parametrize("raw", ["10:00-", "10:00+", "10:00–", "10:00−",
                                 "10:00—"])
def test_b1_low_the_glue_sign_after_hhmm_leaves_the_record_unread(raw):
    """Правая граница записи ЧЧ:ММ (низкая находка пятого ревью: закон
    N1 держался кодом, но тестом закреплён не был — мутант, вернувший
    старую границу `(?![\\w:]|[.,]\\d)`, проходил). Знак, приклеенный к
    «10:00» справа, оставляет запись непрочитанной — тем же законом,
    что у голого числа, — и заметка называет её целиком, со знаком."""
    assert fp.parse_value(raw, TOD, fp.default_domain(TOD)) is None
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None
    assert fp.unread_digits(raw, TOD) == [raw]
    assert fp.parse_value("10:00", TOD, fp.default_domain(TOD)) == (600,
                                                                    "10:00")


def _stored_decline_with_k04_revision():
    """Свод ПРЕЖНЕЙ выдачи: условный отказ записан решением («применён»,
    блокировки сняты), значение названо пересмотром K04 (`revision_of`),
    а посадку носителя переиздала заплата — записи параметра в ней нет.
    Ровно тот свод, на котором ревьюер увидел regression B2."""
    doc, rule, q = _prepared()
    doc.setdefault("meta", {})["revision"] = 2
    q["answer"] = {"raw": K01_ANSWER, "status": "применён", "revision": 1,
                   "note": "прежняя выдача: решение записано ответом"}
    fp._set_blockers(doc, QID, {r["id"] for r in doc["rules"]}, False)
    rev = {k: v for k, v in q.items() if k != "answer"}
    rev["id"] = QID + "-R"
    rev["revision_of"] = QID
    rev["text"] = q["text"] + " Пересмотр: назовите одно значение."
    rev["answer"] = {"raw": "600 минут", "status": "применён", "revision": 2,
                     "note": "пересмотр K04: значение названо"}
    doc["questions"].append(rev)
    q["answer"].update(status="пересмотрен", revised_by=rev["id"])
    return doc, rule, q, rev


def test_b2_self_healing_takes_the_value_from_the_k04_revision():
    """Регрессия самолечения (B2): старый условный отказ лежит в своде
    решением «применён», разбор его НЕ читает — и он заслонял собой
    пересмотр. `_is_applied_value` считал его ответом-значением,
    `_reapply_values` брал ПЕРВЫЙ такой ответ (`next(...)`), parse_value
    возвращал None, перебор кончался — значение «600 минут» в посадку не
    возвращалось, параметр молча оставался недостающим, вопроса и
    блокировки не было. Теперь фраза отказа исключает ответ из значений,
    а значение берётся из действующего звена цепочки A03/K04, не
    произвольного применённого ответа. Фикстура называет эту связь."""
    doc, rule, q, rev = _stored_decline_with_k04_revision()
    assert fp._is_applied_value(q) is False        # отказ — не значение
    assert fp._is_applied_value(rev) is True
    cov = fp.family_coverage(doc)
    assert cov["rest_floors"]["missing"] == ["min_rest_outstation"]
    assert fp._reapply_values(doc, cov) == ["R-S1-П-2-3"]
    cell = rule["template_fit"]["params"]["min_rest_outstation"]
    assert cell["value"] == 600 and cell["quote"] == "600 минут"
    assert cell["answer"] == QID       # ответ назван КОРНЕМ пересмотра
    assert cell["source"] == _expected_answer_source(
        rev, "min_rest_outstation", "600 минут")
    assert cov["rest_floors"]["missing"] == []
    assert fp.ensure_family_param_questions(doc) == []
    assert m.validate_model(doc) == []


def test_b2_an_unreadable_applied_answer_does_not_shadow_the_next_one():
    """Вторая половина B2 — действующий пересмотр заменяет старое
    значение по явной цепочке. Старый ответ фразой отказа не начат: прежняя
    выдача записала «-600 минут» решением (знак тогда отваливался), а
    сегодняшний разбор его не читает — значение вне области параметра.
    Пока брался ПЕРВЫЙ применённый ответ, такая запись отменяла
    самолечение целиком; теперь она лишь пропускается, и значение
    пересмотра возвращается в посадку."""
    doc, rule, q = _prepared()
    doc.setdefault("meta", {})["revision"] = 2
    q["answer"] = {"raw": "-600 минут", "status": "применён", "revision": 1,
                   "note": "прежняя выдача: знак терялся"}
    fp._set_blockers(doc, QID, {r["id"] for r in doc["rules"]}, False)
    rev = {k: v for k, v in q.items() if k != "answer"}
    rev["id"] = QID + "-R"
    rev["revision_of"] = QID
    rev["text"] = q["text"] + " Пересмотр: назовите одно значение."
    rev["answer"] = {"raw": "600 минут", "status": "применён", "revision": 2,
                     "note": "пересмотр K04: значение названо"}
    doc["questions"].append(rev)
    assert fp._is_applied_value(q) is True      # фразы отказа в нём нет
    assert fp.parse_value("-600 минут", U, fp.default_domain(U)) is None
    q["answer"].update(status="пересмотрен", revised_by=rev["id"])
    cov = fp.family_coverage(doc)
    assert fp._reapply_values(doc, cov) == ["R-S1-П-2-3"]
    assert rule["template_fit"]["params"]["min_rest_outstation"][
        "value"] == 600
    assert m.validate_model(doc) == []


def test_b2_an_old_conditional_decline_alone_does_not_hide_the_parameter():
    """Тот же старый ответ БЕЗ пересмотра: значение не выдумывается —
    параметр остаётся недостающим и решением не считается (полнота
    называет его вслух), а на живом пути тот же ответ оставляет вопрос
    открытым и носитель заблокированным. Повторный вопрос по СТАРОМУ
    своду ставит пересмотр K04 (миграция волны 17б), не разбор."""
    doc, rule, q = _prepared()
    q["answer"] = {"raw": K01_ANSWER, "status": "применён", "revision": 1}
    fp._set_blockers(doc, QID, {r["id"] for r in doc["rules"]}, False)
    cov = fp.family_coverage(doc)
    assert cov["rest_floors"]["declined"] == set()
    assert cov["rest_floors"]["missing"] == ["min_rest_outstation"]
    assert fp._reapply_values(doc, cov) == []
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    # Живой путь того же ответа: отказ виден вопросом и блокировкой.
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: K01_ANSWER})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert fp.family_coverage(doc)["rest_floors"]["missing"] == [
        "min_rest_outstation"]


# ── Шестое неавторское ревью K02 (12.09.2026): находка K-1 ────────────
# K-1 — фраза отказа судилась только в НАЧАЛЕ ответа, поэтому
# исключение, названное ПОСЛЕ значения, молча исчезало из выпускаемой
# постановки: «600 минут. Не применяется для базы SVO.» шло в
# «применён» значением 600 — вопрос закрывался, блокировки снимались,
# условие авиакомпании не попадало никуда (принцип 4 ВМП),
# ограниченное значение издавалось как общее (принцип 3), и один смысл
# давал два исхода по ПОРЯДКУ предложений (принцип 2). Закон B1
# расширен симметрично ОДНИМ предикатом: безусловен ответ, ВЕСЬ
# состоящий из фраз отказа; фраза отказа при не-отказном остатке —
# «не разобран», где бы она ни стояла.

#: Ответы находки K-1 дословно (значение названо ДО отказа) с
#: единицей, на которой ревьюер их воспроизвёл. Раньше каждый давал
#: значение и закрывал вопрос.
K1_ANSWERS = [
    ("600 минут. Не применяется для базы SVO.", U),      # раньше: 600
    ("600 минут. Не применяется.", U),                   # раньше: 600
    ("600 минут; для SVO не применяется.", U),           # раньше: 600
    ("10:00. Не применяется для базы SVO.", TOD),        # раньше: 600
]


@pytest.mark.parametrize("raw,unit", K1_ANSWERS)
def test_k1_a_decline_after_the_value_is_neither_decision_nor_value(raw, unit):
    """Предикат K-1 на разборе: ответ, где отказ назван ПОСЛЕ значения,
    не даёт ни значения, ни решения — ни в том же предложении («600
    минут; для SVO не применяется»), ни соседним («600 минут. Не
    применяется для базы SVO.»). Раньше отказ судился по началу
    ПЕРВОГО предложения, и всё сказанное после него пропадало."""
    assert fp.parse_value(raw, unit) is None
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.is_declined(raw) is False
    assert fp.decline_tail(raw) != ""
    for other in (U, TOD, CNT, CH, HPCH):   # единица параметра не спасает
        assert fp.parse_value(raw, other) is None


@pytest.mark.parametrize("raw", [r for r, u in K1_ANSWERS if u == U])
def test_k1_the_same_answers_through_the_real_application_stage(raw):
    """Те же ответы сквозным путём (настоящий apply_answers): «не
    разобран», вопрос открыт, носитель заблокирован, ячейка не
    записана, семья не полна и решением параметр не считается, свод
    чист. Заметка называет закон и способ ответить."""
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    note = q["answer"]["note"]
    assert "безусловным отказом считается ВЕСЬ ответ из фраз отказа" in note
    assert "назовите отказ отдельным ответом" in note
    cov = fp.family_coverage(doc)["rest_floors"]
    assert "min_rest_outstation" not in cov["declined"]
    assert "min_rest_outstation" in cov["missing"]
    assert m.validate_model(doc) == []


def test_k1_the_same_law_holds_for_time_of_day_through_the_stage():
    """То же на ВТОРОЙ единице — времени суток (night_series): «10:00.
    Не применяется для базы SVO.» раньше вносило 600 как общее правило
    ночного окна, хотя авиакомпания отказалась от него для одной базы
    и назвала значение лишь для остальных."""
    doc, rule, q = _prepared_night_series()
    summary = answers.apply_answers(
        doc, {QID_NS: "10:00. Не применяется для базы SVO."})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID_NS in rule["blockers"]
    assert "night_duty_end" not in rule["template_fit"]["params"]
    assert "назовите отказ отдельным ответом" in q["answer"]["note"]
    cov = fp.family_coverage(doc)["night_series"]
    assert "night_duty_end" not in cov["declined"] \
        and "night_duty_end" in cov["missing"]
    assert m.validate_model(doc) == []


#: Зеркальные пары: один и тот же смысл, переставленный порядком.
#: Первая пара — соседними предложениями, вторая — внутри одного
#: предложения (ответ ревью барьера K01 и его зеркало).
K1_MIRRORS = [
    ("Не применяется для базы SVO. В остальных базах 600 минут.",
     "В остальных базах 600 минут. Не применяется для базы SVO."),
    ("Не применяется для сотрудников базы SVO; в остальных базах 10:00",
     "В остальных базах 10:00; для сотрудников базы SVO не применяется"),
]


@pytest.mark.parametrize("first,second", K1_MIRRORS)
def test_k1_the_verdict_does_not_depend_on_the_order(first, second):
    """Ядро находки: значение ДО отказа и отказ ДО значения дают ОДИН
    исход. Раньше «Не применяется… В остальных базах 600 минут.» давало
    «не разобран» (вопрос открыт), а тот же смысл в обратном порядке —
    «применён» со значением 600: один смысл, два исхода по порядку
    предложений (принцип 2 ВМП)."""
    out = []
    for raw in (first, second):
        assert fp.is_declined(raw) is False
        for unit in (U, TOD, CNT):
            assert fp.parse_value(raw, unit) is None
            assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
        doc, rule, q = _prepared()
        summary = answers.apply_answers(doc, {QID: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert q["answer"]["status"] == "не разобран"
        assert m.is_open_question(q) and QID in rule["blockers"]
        assert "min_rest_outstation" not in rule["template_fit"]["params"]
        cov = fp.family_coverage(doc)["rest_floors"]
        assert "min_rest_outstation" not in cov["declined"]
        assert "min_rest_outstation" in cov["missing"]
        out.append(q["answer"]["note"])
    # Заметка у зеркал одна и та же — с точностью до регистра фразы
    # отказа, написанной авиакомпанией («Не применяется» / «не
    # применяется»): разлад назван одинаково, потому что величину ищет
    # ВЕСЬ ответ, а не одна помеха.
    assert out[0].lower() == out[1].lower()
    assert "отказ и значение" in out[0]


@pytest.mark.parametrize("raw,tail", [
    ("600 минут; для SVO не применяется", "600 минут; для SVO"),
    ("В остальных базах 600 минут. Не применяется для базы SVO.",
     "В остальных базах 600 минут"),
    ("10:00. Не применяется для базы SVO.", "10:00"),
    ("600 минут. Не применяется.", "600 минут"),
    ("1) Не применяется", "1)"),
    ("2. Не применяется", "2"),
])
def test_k1_decline_tail_names_the_obstruction_before_the_phrase(raw, tail):
    """Помеха называется целиком и ТАМ, ГДЕ ОНА ЕСТЬ: до фразы отказа
    (голова предложения или предыдущее предложение) — так же, как
    раньше называлась после неё. По ней применение ответа выбирает
    заметку человеку."""
    assert fp.decline_tail(raw) == tail


# Остаток (а) шестого ревью — нумерованный ответ анкеты

@pytest.mark.parametrize("raw", [
    "2. Не применяется", "1) Не применяется", "1. Не применяется",
    "3) не применяется", "10. Не применяется.",
])
def test_k1_a_numbered_decline_is_not_a_count(raw):
    """Остаток (а): ответ, где отказ помечен НОМЕРОМ СТРОКИ анкеты,
    читался счётом («2. Не применяется» → 2, «1) Не применяется» → 1) —
    номер строки становился значением параметра. Решение по существу:
    номер — не фраза отказа, значит ответ не весь из фраз отказа и не
    безусловен; а что названо цифрой — номер строки или значение, —
    ответ не говорит, и закон fail-closed велит спросить снова, а не
    гадать. Обе письменные формы («2.» рвёт предложение точкой, «1)» не
    рвёт) дают ОДИН исход."""
    assert fp.parse_value(raw, CNT) is None
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None
    assert fp.parse_value(raw, U) is None
    assert fp.is_declined(raw) is False
    assert fp.decline_tail(raw) != ""


def test_k1_a_numbered_decline_keeps_the_count_question_open():
    """Тот же остаток сквозным путём на СЧЁТНОМ параметре (длина окна
    выходных): раньше «2. Не применяется» закрывало вопрос числом 2 —
    номер строки анкеты уезжал в посадку длиной окна."""
    for raw in ("2. Не применяется", "1) Не применяется"):
        doc, rule, q = _prepared_count()
        summary = answers.apply_answers(doc, {QID_CNT: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert q["answer"]["status"] == "не разобран"
        assert m.is_open_question(q) and QID_CNT in rule["blockers"]
        assert "days_off_window_days" not in rule["template_fit"]["params"]
        assert "назовите отказ отдельным ответом" in q["answer"]["note"]
        assert m.validate_model(doc) == []


def test_k1_a_numbered_value_is_judged_by_the_old_law_not_by_the_predicate():
    """Обратная сторона остатка (а): предикат отказа трогает только
    ответы С ФРАЗОЙ ОТКАЗА. Нумерованное ЗНАЧЕНИЕ он не судит вовсе
    (помехи нет), и отказывает ему прежний закон охвата цифр — номер
    строки остаётся цифрой вне числового выражения. Расширение
    предиката эту границу не сдвинуло."""
    for raw, stray in (("1) 600 минут", "1"), ("2. 600 минут", "2")):
        assert fp.decline_tail(raw) == "" and fp.is_declined(raw) is False
        assert fp.parse_value(raw, U) is None
        assert fp.unread_digits(raw, U) == [stray]
    assert fp.parse_value("600 минут", U) == (600, "600 минут")


# Тот же предикат — у ответа-ЗНАЧЕНИЯ прежней выдачи (требование 4)

def test_k1_a_stored_answer_with_a_trailing_decline_is_not_a_value():
    """`_is_applied_value` судит ВЕСЬ ответ: запись прежней выдачи, где
    отказ назван ПОСЛЕ значения, значением не считается ни при каком
    исходе — иначе самолечение брало бы именно её, разбор возвращал бы
    None, и значение из пересмотра K04 в посадку не возвращалось."""
    doc, rule, q = _prepared()
    q["answer"] = {"raw": "600 минут. Не применяется для базы SVO.",
                   "status": "применён", "revision": 1}
    assert fp._is_applied_value(q) is False
    cov = fp.family_coverage(doc)
    assert cov["rest_floors"]["declined"] == set()
    assert cov["rest_floors"]["missing"] == ["min_rest_outstation"]
    assert fp._reapply_values(doc, cov) == []
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    q["answer"]["raw"] = "600 минут"            # тот же ответ без отказа
    assert fp._is_applied_value(q) is True


# Положительные контроли K-1

@pytest.mark.parametrize("raw", [
    "Не применяется.", "не применяется", "Неприменимо!",
    "Не применяется. Нет такого требования.",
    "Нет такого требования. Не применяется.",
    "Не применяется! Неприменимо. Такого требования нет.",
    "Не применяется: такого требования у нас нет.",
    "«Не применяется». Неприменимо!",
])
def test_k1_an_honest_decline_is_still_a_decision(raw):
    """Положительный контроль: честный отказ — сколькими бы
    предложениями он ни был сказан и в каком бы порядке фразы ни шли —
    остаётся решением авиакомпании. Расширение предиката сузило
    безусловность, но не отменило её."""
    assert fp.is_declined(raw) is True and fp.decline_tail(raw) == ""
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["applied"] == 1
    assert q["answer"]["status"] == "применён"
    assert not m.is_open_question(q) and QID not in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert "min_rest_outstation" in fp.family_coverage(
        doc)["rest_floors"]["declined"]


@pytest.mark.parametrize("unit,raw,want", [
    (U, "600 минут", 600),
    (U, "10 ч 30 мин", 630),
    (U, "Да, требование есть. Отдых вне базы — 9 часов.", 540),
    (TOD, "с 22:00", 1320),
    (CNT, "3 смены подряд", 3),
    (CH, "75 часов кредита", 75),
    (HPCH, "1,5", 1.5),
    ("code", "A320", "A320"),
    ("code_list", "B738, A320", "A320, B738"),
])
def test_k1_an_honest_value_without_decline_phrases_reads_as_before(
        unit, raw, want):
    """Положительный контроль на ВСЕХ СЕМИ единицах каталога 0.5.0:
    ответ без единой фразы отказа читается прежней грамматикой —
    предикат отказа его не трогает."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want
    assert fp.decline_tail(raw) == "" and fp.is_declined(raw) is False


def test_k1_k03_scope_is_refused_until_it_can_be_preserved():
    """CDPM-251 closes the old silent-loss boundary, not full K03.

    A clause without a decline phrase is still not an unconditional scalar.
    This inverts the former regression that explicitly preserved the loss.
    """
    assert fp.decline_tail("600 минут, кроме SVO") == ""
    assert fp.parse_value("600 минут, кроме SVO", U) is None


@pytest.mark.parametrize("raw", [
    None, 600, 600.0, ["600 минут"], ("600 минут",), b"600 minut",
    {"value": "600 минут"}, "", "   ",
    "600 минут. Не применяется\x00", "не применяется\x1b[0m",
])
def test_k1_broken_and_non_text_answers_do_not_break_the_predicate(raw):
    """Не текст, пустое и повреждённое управляющим знаком: предикат не
    падает и решения не даёт — предложений нет, значит нет ни отказа,
    ни помехи, ни значения (тот же закон, что у K02-R4/R5)."""
    assert fp.is_declined(raw) is False
    assert fp.decline_tail(raw) == ""
    assert fp.parse_value(raw, U) is None
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None


def test_k1_a_long_answer_with_a_trailing_decline_stays_linear(k02_work_budget):
    """Скорость: фраза отказа ищется теперь во ВСЁМ ответе, поэтому
    самый дорогой для предиката случай — длинный ответ, где фраза стоит
    в САМОМ КОНЦЕ (раньше разбор отказа кончался на первом же слове).
    Проход один и линейный: 180 КБ (обычная ячейка xlsx) на настоящей
    стадии применения ответов — доли секунды, заметка ограничена."""
    raw = " ".join(str(i) for i in range(32000)) + " Не применяется."
    doc, rule, q = _prepared()
    with k02_work_budget(raw):
        summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert len(q["answer"]["note"]) < 600


def test_k1_the_price_of_symmetry_is_named_not_hidden():
    """Цена симметрии, названная и в руководстве: закон знает ФРАЗЫ
    отказа перечнем, а не смысл отрицания, поэтому фраза отказа,
    сказанная о ДРУГОМ, тоже делает ответ «не разобран» — сторона
    fail-closed, и цена ей один повторный вопрос, а не молчаливое
    значение. Отрицание, сказанное иными словами, закон не знает
    вовсе, и такой ответ читается как прежде."""
    priced = "600 минут. Другого требования нет."
    assert fp.parse_value(priced, U) is None
    assert fp.decline_tail(priced) == "600 минут"
    assert fp.parse_value("600 минут. Иных ограничений не установлено.",
                          U) == (600, "600 минут")


# ═══════ Восьмое неавторское ревью K02 (12.09.2026): номер строки ═══════
# анкеты у ВСЕХ единиц каталога 0.5.0, а не только у отказа и у числа.
#
# Находка ревью: нумерованный ответ у ЕДИНИЦ ОБОЗНАЧЕНИЙ давал КОД
# «1» — номер строки анкеты уезжал в посадку правила-носителя
# обозначением, которого авиакомпания не называла (fail-open с НЕВЕРНЫМ
# значением: принцип 3 ВМП — ограниченное издаётся как общее, принцип 4
# — в постановку попадает не сказанное; контракт П0 §2.6 K02 —
# «значение равно тому, что прочитал бы человек, либо вопрос открыт»).
#
# Проверка своими пробами показала, что дыра шире находки: она есть у
# ПЯТИ единиц из семи — везде, где ЗАПИСЬЮ ЗНАЧЕНИЯ служит голое целое
# или любой токен справочника, и закон охвата цифр номер не ловит:
#   code                  «1. A320»          → «1»
#   code_list             «1. A320, B738»    → «1»
#                         «1) A320, B738»    → «1, A320, B738»
#   count                 «1. Это норма»     → 1
#   credit_hours          «75. Это норма.»   → 75
#   hours_per_credit_hour «10) Это норма»    → 10
# У duration_minutes и time_of_day_minutes тот же ответ отказывал и
# прежде: там запись значения помечена словом единицы или формой
# ЧЧ:ММ, и номер оставался цифрой вне числового выражения.
# Третье место той же дыры — номер С НОВОЙ СТРОКИ текста после
# вводных слов без точки (перенос строки нормализация схлопывает):
#   code_list             «Типы судов:\n1) A320\n2) B738» → «1, 2, A320, B738»
#   count                 «Норма:\n1) Это норма»          → 1
#
# Закон — ТОТ ЖЕ, которым закрыт нумерованный ОТКАЗ (остаток (а)
# шестого ревью): номер строки анкеты — не часть ответа, а что названо
# цифрой (номер или значение), ответ не говорит, поэтому fail-closed —
# «не разобран» с заметкой, просящей назвать значение без номера
# строки. Ни догадки «это номер, отбросим его», ни тихого чтения
# номера значением (см. fp.line_number_record).

#: Все семь единиц каталога 0.5.0 и честное значение каждой — то самое,
#: что обязано читаться по-прежнему, когда номера строки при нём нет.
K8_UNITS = [
    (U, "600 минут", 600),
    (CNT, "3", 3),
    ("time_of_day_minutes", "06:00", 360),
    ("code", "A320", "A320"),
    ("code_list", "A320, B738", "A320, B738"),
    (CH, "75", 75),
    (HPCH, "1,5", 1.5),
]
#: Обе письменные формы номера — с пробелом за знаком и без него.
#: Скобка отделяет номер сама, точка — нет: точка ВПЛОТНУЮ к цифре
#: значения есть разделитель дробной части («1.75» — это 1,75, а не
#: строка 1 со значением 75), поэтому форма «1.» без пробела стоит
#: только перед значением, начинающимся не с цифры. Это не поблажка, а
#: та же грамматика числа (K02 §2.2): иначе закон номера съел бы
#: десятичную запись, которую он обязан сохранить (см.
#: test_k8_a_decimal_and_a_clause_reference_are_not_line_numbers).
K8_PREFIXES = ["1. ", "1.", "1) ", "1)", "2. ", "2)", "10. ", "10) "]


def _k8_cases():
    """Все семь единиц × обе формы номера × две позиции: номер в начале
    ОТВЕТА и номер в начале ПРЕДЛОЖЕНИЯ после вводной фразы."""
    for unit, body, _ in K8_UNITS:
        for pref in K8_PREFIXES:
            if pref.endswith(".") and body[0].isdigit():
                continue            # «1.600» — дробь, а не строка анкеты
            yield unit, pref + body
            yield unit, "Смотрите ниже. " + pref + body


@pytest.mark.parametrize("unit,raw", list(_k8_cases()))
def test_k8_a_numbered_answer_is_not_a_value_at_any_unit(unit, raw):
    """«Минус» находки на ВСЕХ семи единицах: ответ с номером строки
    анкеты значения не даёт — ни с доменом параметра, ни без него, — а
    номер называется дословно. Раньше пять единиц из семи читали НОМЕР:
    «1. A320» → код «1», «1) A320, B738» → перечень «1, A320, B738»,
    «1. Это норма» → счёт 1, «75. Это норма.» → 75 часов кредита."""
    assert fp.parse_value(raw, unit) is None
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.line_number_record(raw) != ""


@pytest.mark.parametrize("unit,raw,number,was", [
    ("code", "1. A320", "1.", "1"),
    ("code", "10. A320", "10.", "10"),
    ("code_list", "1. A320, B738", "1.", "1"),
    ("code_list", "1) A320, B738", "1)", "1, A320, B738"),
    ("code_list", "10) A320, B738", "10)", "10, A320, B738"),
    ("code_list", "1)A320, B738", "1)", "1, A320, B738"),
    ("code_list", "1.A320, B738", "1.", "1, A320, B738"),
    ("code_list", "Смотрите ниже. 1. A320, B738", "1.", "1"),
    (CNT, "1. Это норма", "1.", 1),
    (CNT, "1) Это норма", "1)", 1),
    (CH, "75. Это норма.", "75.", 75),
    (HPCH, "10) Это норма", "10)", 10),
])
def test_k8_the_old_law_read_the_line_number_itself(unit, raw, number, was):
    """Тот же «минус», названный ПРЕДМЕТНО: `was` — значение, которое
    прежний код вносил в посадку правила-носителя; во всех случаях в
    нём сидит номер строки, а не то, что сказала авиакомпания. Теперь
    разбора нет вовсе, а номер называется дословно тем знаком, каким
    его написала авиакомпания («1.» и «1)» — разные записи одного
    номера, и заметка не переписывает ответ за отвечающего."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is None, f"прежнее ошибочное значение {was!r} вернулось"
    assert fp.line_number_record(raw) == number
    assert str(was).startswith(number.rstrip(".)"))


@pytest.mark.parametrize("unit,body,want", K8_UNITS)
def test_k8_positive_control_the_same_answer_without_a_number_reads(
        unit, body, want):
    """Положительный контроль каждой единицы: честное значение БЕЗ
    номера строки читается ровно как прежде — закон трогает только
    запись номера, а не грамматику значения."""
    got = fp.parse_value(body, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want
    assert fp.line_number_record(body) == ""


@pytest.mark.parametrize("raw,want", [
    ("A320", "A320"),                       # одно обозначение
    ("737", "737"),                         # обозначение ИЗ ОДНИХ ЦИФР
    ("A320-200", "A320-200"),               # дефис — часть обозначения
])
def test_k8_a_digit_only_designation_is_kept_at_code(raw, want):
    """Граница закона, названная контрактом семьи: параметры
    fleet_exclusions.fleet_type/fleet_types каталога 0.5.0 объявлены как
    «коды — из документа авиакомпании», то есть обозначение ВПРАВЕ
    состоять из одних цифр (типы судов «737», «320»). Закон номера обязан
    их сохранить: цифры без знака номера остаются обозначением."""
    got = fp.parse_value(raw, "code")
    assert got is not None and got[0] == want
    assert fp.line_number_record(raw) == ""


@pytest.mark.parametrize("raw,want", [
    ("A320, B738", "A320, B738"),
    ("B738, A320", "A320, B738"),           # перечень канонически
    ("737, 320", "320, 737"),               # перечень ИЗ ОДНИХ ЦИФР
    ("A320, B738, A321", "A320, A321, B738"),
])
def test_k8_a_comma_list_of_designations_is_kept(raw, want):
    """Тот же положительный контроль у перечня: запятая перечня знаком
    номера не является, и перечень обозначений — в том числе целиком
    числовых — читается как прежде."""
    got = fp.parse_value(raw, "code_list")
    assert got is not None and got[0] == want
    assert fp.line_number_record(raw) == ""


@pytest.mark.parametrize("raw,unit,want", [
    ("1.75", CH, 1.75),                     # десятичная запись, не строка 1
    ("1.5", HPCH, 1.5),
    ("10,5 часов", U, 630),
    ("0.5", HPCH, 0.5),
    ("Отдых вне базы — 600 минут. Основание — п. 4.2.", U, 600),
])
def test_k8_a_decimal_and_a_clause_reference_are_not_line_numbers(
        raw, unit, want):
    """Отрицательный контроль САМОГО закона: точка со знаком номера
    смешана быть не должна. За знаком номера идёт содержание, а не
    цифра, поэтому «1.75» и «1.5» остаются десятичной записью, а
    ссылка «п. 4.2» — ссылкой (её судит прежний закон охвата цифр,
    K02-T3/R3), и ответ со ссылкой в ОТДЕЛЬНОМ предложении читается
    как прежде."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want
    assert fp.line_number_record(raw) == ""


@pytest.mark.parametrize("raw,unit,want", [
    ("1.600 минут", U, None),               # 1,6 минуты — не целая
    ("Смотрите ниже. 1.600 минут", U, None),
    ("1.3", CNT, None),                     # счёт — целое
    ("1.06:00", "time_of_day_minutes", None),
    ("1.75", CH, 1.75),                     # честная дробь
    ("1.1,5", HPCH, None),
])
def test_k8_a_dot_glued_to_a_digit_stays_a_decimal_point(raw, unit, want):
    """Та самая пара, которую перебор выше пропускает, — названная
    отдельно. Точка ВПЛОТНУЮ к цифре значения остаётся разделителем
    дробной части: номера строки здесь нет, и прежняя грамматика числа
    судит запись сама. Главное — ни один такой ответ не даёт ЗНАЧЕНИЯ
    «1»: либо честная дробь («1.75» → 1,75), либо отказ."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert fp.line_number_record(raw) == ""
    if want is None:
        assert got is None
    else:
        assert got is not None and got[0] == want
    assert got is None or got[0] != 1


@pytest.mark.parametrize("raw", [
    "3.", "3", "Смотрите приложение. 3.", "600 минут",
    "Парк. A320", "2 (два пилота)", "10 ч. 30 мин.",
])
def test_k8_a_number_without_a_marked_line_is_not_a_line_number(raw):
    """Другая сторона границы: номером строки запись становится ТОЛЬКО
    со знаком номера И содержанием за ним. «3.» — само число 3 (нечего
    нумеровать), «10 ч. 30 мин.» — точка сокращения единицы (K02-F5),
    «2 (два пилота)» — живой ответ книги набора Н0."""
    assert fp.line_number_record(raw) == ""


@pytest.mark.parametrize("unit,raw,number,was", [
    ("code_list", "Типы судов:\n1) A320\n2) B738", "1)", "1, 2, A320, B738"),
    ("code_list", "Типы судов:\n1. A320\n2. B738", "1.", "1"),
    ("code_list", "Типы —\r\n1) A320", "1)", "1, A320"),
    ("code_list", "Типы —\n  10) A320, B738", "10)", "10, A320, B738"),
    ("code_list", "Типы:\n1)\nA320", "1)", "1, A320"),   # номер один на строке
    ("code_list", "Типы:\n1)\nA320\n2)\nB738", "1)", "1, 2, A320, B738"),
    ("code", "Тип:\n1) A320", "1)", None),
    ("code", "Тип:\n1. A320", "1.", None),
    (CNT, "Норма:\n1) Это норма", "1)", 1),
    (CNT, "Норма:\r\n1. Это норма", "1.", 1),
    (CH, "Норма:\n75. Это норма", "75.", 75),
    (HPCH, "Норма:\n10) Это норма", "10)", 10),
    (U, "Отдых:\n1) 600 минут", "1)", None),
    ("time_of_day_minutes", "Начало:\n2. 06:00", "2.", None),
])
def test_k8_a_numbered_line_after_a_line_break_is_a_line_number(
        unit, raw, number, was):
    """Третье место номера строки — НАЧАЛО СТРОКИ ТЕКСТА ответа, найдено
    своими пробами сверх находки ревью. Вводные слова с двоеточием или
    тире не кончают предложение, а перенос строки нормализация
    схлопывает в пробел, поэтому в двух первых местах (начало ответа,
    начало предложения) номер не стоял и закон его не видел: `was` —
    значение, которое читалось на HEAD и после первых двух мест
    закона (перечень «1, 2, A320, B738» с двумя обозначениями,
    которых авиакомпания не называла; счёт 1; 75 часов кредита).
    None в `was` — единица отказывала и прежде, но с заметкой мимо
    причины. Теперь начало каждой строки сырого текста судится тем же
    знаком номера, и исход у всех семи единиц один — «не разобран»."""
    assert fp.line_number_record(raw) == number
    assert fp.parse_value(raw, unit) is None
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert was is None or str(was).startswith(number.rstrip(".)"))


@pytest.mark.parametrize("unit,raw,want", [
    ("code_list", "A320\nB738", "A320, B738"),      # перечень по строкам
    ("code_list", "A320,\nB738", "A320, B738"),
    (U, "Отдых:\n600 минут", 600),
    (U, "10 ч.\n30 мин.", 630),                    # точка сокращения единицы
    (HPCH, "Норма:\n1.5", 1.5),                    # дробь с новой строки
    (HPCH, "Норма:\n1,5", 1.5),
    (CNT, "Норма:\n3.", 3),                        # число без содержания за точкой
    (CNT, "Норма:\n3", 3),
    ("time_of_day_minutes", "Начало:\n06:00", 360),
    (U, "  \n 600 минут \n", 600),              # пустые строки и отступы
    ("code_list", "\u2028A320\u2029B738", "A320, B738"),  # разделители строк Unicode
])
def test_k8_a_line_break_without_a_line_number_reads_as_before(
        unit, raw, want):
    """Положительный контроль третьего места: перенос строки сам по
    себе номером не является — строка без знака номера или без
    содержания за ним читается ровно как прежде, дробь с новой строки
    остаётся дробью, точка сокращения единицы — точкой единицы."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


def test_k8_numbered_designation_keeps_the_code_list_question_open():
    """Сквозной контроль НАХОДКИ на настоящем apply_answers и
    настоящем параметре-перечне каталога 0.5.0
    (fleet_exclusions.fleet_types): вопрос остаётся открытым, носитель
    заблокирован, в посадку ничего не садится, а заметка называет
    номер строки и просит значение без него. Раньше в посадку садился
    перечень «1» либо «1, A320, B738».

    `validate_model` на этом стенде даёт ровно свои прежние замечания о
    пустых `params` посадки — это отдельный остаток ревью (стенд
    семьи без параметров), в охват этого круга он не входит; тест
    сторожит, что НОВЫХ замечаний правка не добавила."""
    qid_fl = "Q-3b-параметр-fleet_exclusions-fleet_types"
    for raw in ("1. A320, B738", "1) A320, B738",
                "Смотрите ниже. 1. A320, B738",
                "Типы судов:\n1) A320\n2) B738"):
        doc = sample_model()
        rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
        rule["template_fit"] = {
            "category": "A", "registry_rev": m.registry_fingerprint(),
            "family": "fleet_exclusions", "params": {}}
        assert fp.ensure_family_param_questions(doc) == [qid_fl]
        q = next(x for x in doc["questions"] if x["id"] == qid_fl)
        summary = answers.apply_answers(doc, {qid_fl: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert q["answer"]["status"] == "не разобран"
        assert m.is_open_question(q) and qid_fl in rule["blockers"]
        assert rule["template_fit"]["params"] == {}
        note = q["answer"]["note"]
        assert "номер строки анкеты" in note
        assert "назовите значение без номера строки" in note
        assert all("params" in e for e in m.validate_model(doc))


def test_k8_numbered_count_keeps_the_question_open_with_its_own_note():
    """Тот же сквозной путь на СЧЁТНОМ параметре каталога 0.5.0
    (days_off_floor.days_off_window_days — длина окна выходных) и на
    полном стенде, где свод остаётся действительным: раньше
    «1. Это норма» закрывало вопрос длиной окна 1 — номер строки
    анкеты уезжал в посадку сутками."""
    for raw in ("1. Это норма", "1) Это норма", "10. Смотрите приложение"):
        doc, rule, q = _prepared_count()
        summary = answers.apply_answers(doc, {QID_CNT: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert q["answer"]["status"] == "не разобран"
        assert m.is_open_question(q) and QID_CNT in rule["blockers"]
        assert "days_off_window_days" not in rule["template_fit"]["params"]
        note = q["answer"]["note"]
        assert "номер строки анкеты" in note
        assert "назовите значение без номера строки" in note
        assert m.validate_model(doc) == []


def test_k8_the_numbered_decline_keeps_its_own_law_and_its_own_note():
    """Два закона не столкнулись: нумерованный ОТКАЗ судится, как и
    прежде, законом безусловности (номер — помеха, отказ не безусловен,
    остаток (а) шестого ревью), и заметка у него своя — про отдельный
    ответ отказом. Закон номера строки эту границу не сдвинул."""
    for raw in ("2. Не применяется", "1) Не применяется"):
        doc, rule, q = _prepared_count()
        assert answers.apply_answers(doc, {QID_CNT: raw})["unparsed"] == 1
        note = q["answer"]["note"]
        assert "назовите отказ отдельным ответом" in note
        assert "номер строки анкеты" not in note
        assert fp.is_declined(raw) is False and fp.decline_tail(raw) != ""
        assert m.validate_model(doc) == []


def test_k8_the_note_names_the_line_number_before_the_stray_digits():
    """Изменённое прежнее ожидание, названное вслух: у нумерованного
    ЧИСЛА («1) 600 минут») отказ был и раньше — по закону охвата цифр,
    — но заметка звала убрать «ссылки, обозначения и даты», то есть
    вела МИМО причины. Теперь причину называет номер строки; закон
    охвата цифр не тронут, и unread_digits отвечает по-прежнему."""
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: "1) 600 минут"})
    note = q["answer"]["note"]
    assert "номер строки анкеты «1)»" in note
    assert "цифры вне числового выражения" not in note
    assert fp.unread_digits("1) 600 минут", U) == ["1"]   # закон цел
    assert fp.parse_value("600 минут", U) == (600, "600 минут")


def test_k8_a_broken_or_non_text_answer_gives_no_line_number():
    """Повреждённый управляющим знаком ответ и не-текст номера не дают
    — тот же вход, что у предложений и цитаты (K02-R4/R5): разбор не
    падает и решения не принимает."""
    for raw in (None, 600, ["1. A320"], b"1. A320", "", "   ",
                "1. A320\x00", "1) A320\x1b[0m"):
        assert fp.line_number_record(raw) == ""
        assert fp.parse_value(raw, "code") is None
        assert fp.parse_value(raw, "code_list") is None


# ═══════ Девятое неавторское ревью K02 (12.09.2026): ТОТ ЖЕ номер ══════
# строки анкеты В ОДНУ СТРОКУ (B-1) и остальные письменные формы знака
# номера (K-1). Обе находки — непокрытые части ОДНОЙ дыры восьмого
# круга, а не новые дефекты.
#
# B-1 (блокирующая). Постановка дыры называла ДВЕ причины, по которым
# номер не стоял ни в одном известном закону месте: (а) вводные слова с
# двоеточием — не граница предложения; (б) перенос строки схлопывается
# нормализацией. Закрыта была одна — (б), — и ТОТ ЖЕ ответ, набранный в
# ОДНУ строку, тёк дальше:
#   «Типы судов:\n1) A320\n2) B738» → отказ                (закрыто)
#   «Типы судов: 1) A320, 2) B738»  → «1, 2, A320, B738»    (текло)
#   «Типы судов: 1. A320, 2. B738»  → код «1»               (текло)
#   «Норма: 10. Это норма»          → счёт 10               (текло)
# Сквозным ходом на настоящем apply_answers однострочный ответ САДИЛСЯ
# в посадку правила-носителя и СНИМАЛ блокировку, а его многострочный
# близнец оставлял вопрос открытым: один смысл давал два исхода смотря
# по тому, нажал ли отвечающий Enter (принцип 2 ВМП), — причём живые
# параметрные ответы книг Н0-Б и polyaris-h0b как раз ОДНОСТРОЧНЫЕ, то
# есть текла рабочая форма ответа, а закрыта была редкая.
#
# K-1 (критическая). Знак номера узнавался в ДВУХ письменных формах из
# встречающихся: закон ловил ПОЛОВИНУ знака («1)») и пропускал ЦЕЛЫЙ
# («(1)») — разноголосица (принцип 2) с fail-open и НЕВЕРНЫМ значением:
#   «(1) A320», «[1] A320», «№1 A320», «№ 1 A320», «1: A320»,
#   «1 ) A320»  → перечень «1, A320»
#   «1 - A320»  → «-, 1, A320» (обозначением стал сам дефис)
#   «1 . A320»  → код «1»
#
# Цена закрытия названа вслух и закреплена тестами ниже: ТИРЕ и
# ДВОЕТОЧИЕ — знаки слабые (теми же знаками отделяют пояснение и
# связывают диапазон), поэтому за ними номер узнаётся только при
# СОДЕРЖАНИИ строки — ОБОЗНАЧЕНИИ справочника (десятый круг, K-1: не
# по регистру первой буквы) — и
# только там, где строка начинается на самом деле. Иначе закон съел бы
# законный ответ «⟨значение⟩ — ⟨пояснение⟩». Цена измерена на переборе
# рецензента (2640 входов): тире без разбора содержания отняло бы 21
# верный прежний исход, двоеточие — 84 (все записи времени), а слабый
# знак за каждой точкой — ещё 12 («Согласно п. 4 — A320»).

#: Вводные обороты, за которыми идёт перечисление, — все разделители
#: перечня закона в одну строку.
K9_LEADS = ["Типы судов: ", "Типы судов:", "Типы судов — ", "Типы судов - ",
            "Типы судов – ", "Типы судов; ", "Типы судов, а именно ",
            "Типы судов в частности "]


@pytest.mark.parametrize("lead", K9_LEADS)
def test_k9_a_one_line_enumeration_after_a_lead_is_a_line_number(lead):
    """B-1: номер строки анкеты узнаётся не только с новой строки, но и
    за вводным оборотом В ОДНУ СТРОКУ — по КАЖДОМУ разделителю перечня
    (двоеточие с пробелом и без, тире, дефис, короткое тире, точка с
    запятой, обороты «а именно» и «в частности»)."""
    raw = lead + "1) A320, 2) B738"
    assert fp.line_number_record(raw) == "1)"
    assert fp.parse_value(raw, "code_list") is None
    assert fp.parse_value(raw, "code_list",
                          fp.default_domain("code_list")) is None


#: Тело ответа и номер, который в нём стоит, — для сверки близнецов.
K9_TWIN_BODIES = [
    ("code_list", "1) A320, 2) B738", "1)"),
    ("code_list", "1. A320, 2. B738", "1."),
    ("code", "1) A320", "1)"),
    (CNT, "10. Это норма", "10."),
    (CH, "75. Это норма", "75."),
]
K9_TWIN_LEADS = ["Типы судов:", "Норма:", "Ответ —", "Строки;"]


@pytest.mark.parametrize("lead", K9_TWIN_LEADS)
@pytest.mark.parametrize("unit,body,number", K9_TWIN_BODIES)
def test_k9_the_one_line_twin_gives_the_same_verdict(lead, unit, body,
                                                     number):
    """Принцип 2 ВМП предметно: ОДИН И ТОТ ЖЕ ответ, отличающийся лишь
    тем, нажал ли отвечающий Enter за вводным оборотом, даёт ОДИН исход
    — и номер называется одинаково. Раньше многострочный отказывал, а
    однострочный садился в посадку."""
    multi, single = lead + "\n" + body, lead + " " + body
    assert fp.line_number_record(multi) == number
    assert fp.line_number_record(single) == number
    for raw in (multi, single):
        assert fp.parse_value(raw, unit) is None
        assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("unit,raw,number,was", [
    ("code_list", "Типы судов: 1) A320, 2) B738", "1)", "1, 2, A320, B738"),
    ("code_list", "Типы судов: 1. A320, 2. B738", "1.", "1"),
    ("code_list", "Типы судов — 1) A320", "1)", "1, A320"),
    ("code_list", "Типы судов - 1) A320", "1)", "-, 1, A320"),
    ("code_list", "Типы судов; 1) A320; 2) B738", "1)", "1, 2, A320, B738"),
    ("code_list", "Типы судов, а именно 1) A320 и 2) B738", "1)",
     "1, 2, A320, B738"),
    ("code_list", "Типы судов:1) A320", "1)", "1, A320"),
    ("code_list", "Типы судов: (1) A320, (2) B738", "(1)",
     "1, 2, A320, B738"),
    ("code_list", "№1 A320, №2 B738", "№1", "1, 2, A320, B738"),
    (CNT, "Норма: 10. Это норма", "10.", 10),
    (CNT, "Норма — 10. Это норма", "10.", 10),
    (CNT, "Норма; 10. Это норма", "10.", 10),
    (CNT, "Норма, а именно 10. Это норма", "10.", 10),
    (CH, "Норма: 75. Это норма", "75.", 75),
])
def test_k9_the_old_law_read_the_line_number_of_a_one_line_answer(
        unit, raw, number, was):
    """«Минус» находки B-1, названный предметно: `was` — значение,
    которое прежний код вносил в посадку правила-носителя по
    ОДНОСТРОЧНОМУ ответу. В каждом сидит номер строки анкеты, а не то,
    что сказала авиакомпания."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is None, f"прежнее ошибочное значение {was!r} вернулось"
    assert fp.line_number_record(raw) == number
    assert number.strip("№.)][( ") in str(was)


@pytest.mark.parametrize("unit,raw,number,was", [
    ("code_list", "(1) A320", "(1)", "1, A320"),
    ("code_list", "[1] A320", "[1]", "1, A320"),
    ("code_list", "№1 A320", "№1", "1, A320"),
    ("code_list", "№ 1 A320", "№ 1", "1, A320"),
    ("code_list", "1: A320", "1:", "1, A320"),
    ("code_list", "1 - A320", "1 -", "-, 1, A320"),
    ("code_list", "1 – A320", "1 –", "1, A320"),
    ("code_list", "1 ) A320", "1 )", "1, A320"),
    ("code_list", "1 . A320", "1 .", "1"),
    ("code", "1 . A320", "1 .", "1"),
    ("code_list", "Типы:\n(1) A320\n(2) B738", "(1)", "1, 2, A320, B738"),
    ("code_list", "Типы:\n1 - A320\n2 - B738", "1 -", "-, 1, 2, A320, B738"),
    ("code_list", "Типы:\n№1 A320\n№2 B738", "№1", "1, 2, A320, B738"),
    ("code_list", "Типы:\n1: A320\n2: B738", "1:", "1, 2, A320, B738"),
    ("code_list", "1 - a320", "1 -", "-, 1, a320"),
    ("code_list", "1: a320", "1:", "1, a320"),
    ("code_list", "1 – b738", "1 –", "1, b738"),
])
def test_k9_every_written_form_of_the_sign_is_a_line_number(
        unit, raw, number, was):
    """«Минус» находки K-1: закон знал две формы знака из встречающихся
    и ловил ПОЛОВИНУ знака («1)»), пропуская ЦЕЛЫЙ («(1)»), — `was`
    показывает, что уезжало в посадку. Теперь исход у всех форм один —
    «не разобран», а номер называется ДОСЛОВНО, как его написала
    авиакомпания."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is None, f"прежнее ошибочное значение {was!r} вернулось"
    assert fp.line_number_record(raw) == number


@pytest.mark.parametrize("raw,unit,want", [
    ("3 — по документу", CNT, 3),          # значение с пояснением через тире
    ("3 - по документу", CNT, 3),
    ("3: по документу", CNT, 3),
    ("№1", CNT, 1),                        # «номер 1» — само значение
    ("№ 1", CNT, 1),
    ("Борт №1", CNT, 1),
    ("№1.", CNT, 1),
    ("№1 — по документу", CNT, 1),
    ("2 (два пилота)", CNT, 2),            # живой ответ книги Н0
    ("10:00", "time_of_day_minutes", 600),
    ("23:59", "time_of_day_minutes", 1439),
    ("1:30", "time_of_day_minutes", 90),
    ("A320-200", "code", "A320-200"),      # дефис вплотную — часть кода
    ("737-800", "code", "737-800"),
    ("737-MAX", "code", "737-MAX"),
    ("Ответ: 600 минут", U, 600),          # вводный оборот без номера
    ("Отдых вне базы — 10 ч. Дома — 12 ч.", U, 600),
])
def test_k9_the_widened_law_does_not_eat_legal_answers(raw, unit, want):
    """Главное ограничение правки — не допустить перегиба: расширенный
    закон не отвергает ни одного законного ответа. Здесь — именованные
    границы каждой новой формы знака; перебором это же проверено на
    2640 входах рецензента (закон сработал на 318, из них 306 — как и
    до правки, а 12 новых суть однострочные близнецы прежде
    отвергнутых) и на 10 344 входах своего перебора."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


@pytest.mark.parametrize("raw", [
    "10 - 12 часов", "10-12 часов", "10 – 12 часов", "600 - 720 минут",
    "600 мин. - 12 ч.", "10:00-12:00", "10 ч. 30 мин.", "1 сут. 12 ч.",
    "1 200 руб.", "12.09.2026", "1.5 часа", "1,5", "0.5", "50%",
    "п. 4", "гл. 3", "Согласно п. 4 — A320", "Строка 1: 10 часов",
    "Документ гласит: «1. A320»", "№ 1.5", "1 A320",
])
def test_k9_no_line_number_where_the_law_must_stay_silent(raw):
    """Вторая сторона того же ограничения: записи, похожие на знак
    номера, знаком номера не становятся — диапазон, время, составная
    запись, сокращение единицы, деньги, дата, дробь, проценты, ссылка
    на пункт, цитата документа в кавычках, число без знака вовсе."""
    assert fp.line_number_record(raw) == ""


def test_k9_the_price_of_the_weak_sign_border_is_named_not_hidden():
    """Цена границы названа вслух, а не спрятана: за СЛАБЫМ знаком
    (тире, двоеточие) номер узнаётся только при ОБОЗНАЧЕНИИ за ним, и
    «1 - 600 минут», «1: 600 минут» закон не судит — там он молчит, и
    ответ читается как прежде. Это плата за то, что «3 — по документу»
    остаётся значением 3. Десятый круг (K-1) снял с этой границы
    РЕГИСТР: русское пояснение за слабым знаком номера не даёт ни с
    прописной, ни со строчной буквы, и плата этому симметрична —
    «1 - Это норма» читается счётом 1, как и «1 - это норма». У
    СИЛЬНОГО знака («1.», «1)», «(1)», «[1]») платы нет ни там, ни
    там: он закрывает число сам."""
    for raw in ("1 - 600 минут", "1 - 12 часов", "1 - не применяется",
                "1: 600 минут", "1 - 3 суток",
                "1 - Это норма", "1: Это норма", "1 - это норма",
                "1 - Не применяется"):
        assert fp.line_number_record(raw) == ""
    for raw in ("1. 600 минут", "1) 600 минут", "(1) 600 минут",
                "[1] 600 минут", "1 . 600 минут"):
        assert fp.line_number_record(raw) != ""


def test_k9_the_one_line_answer_no_longer_lands_in_the_carrier():
    """Сквозной контроль B-1 на настоящем apply_answers и настоящем
    параметре-перечне каталога 0.5.0 (fleet_exclusions.fleet_types):
    однострочный ответ больше не садится в посадку и не снимает
    блокировку. Раньше в посадку уезжало «1, 2, A320, B738» — два
    обозначения, которых авиакомпания не называла."""
    qid_fl = "Q-3b-параметр-fleet_exclusions-fleet_types"
    for raw in ("Типы судов: 1) A320, 2) B738",
                "Типы судов — 1) A320",
                "Типы судов, а именно 1) A320 и 2) B738",
                "Типы судов: (1) A320, (2) B738",
                "№1 A320, №2 B738"):
        doc = sample_model()
        rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
        rule["template_fit"] = {
            "category": "A", "registry_rev": m.registry_fingerprint(),
            "family": "fleet_exclusions", "params": {}}
        assert fp.ensure_family_param_questions(doc) == [qid_fl]
        q = next(x for x in doc["questions"] if x["id"] == qid_fl)
        summary = answers.apply_answers(doc, {qid_fl: raw})
        assert summary["applied"] == 0 and summary["unparsed"] == 1
        assert q["answer"]["status"] == "не разобран"
        assert m.is_open_question(q) and qid_fl in rule["blockers"]
        assert rule["template_fit"]["params"] == {}
        note = q["answer"]["note"]
        assert "номер строки анкеты" in note
        assert "назовите значение без номера строки" in note
        assert all("params" in e for e in m.validate_model(doc))


@pytest.mark.parametrize("raw", [
    "Норма: 10. Это норма", "Норма — 10. Это норма",
    "Норма; 10. Это норма", "Норма, а именно 10. Это норма",
    "(1) Это норма", "№1 Это норма", "Норма, 10. Это норма",
])
def test_k9_the_one_line_count_answer_keeps_the_question_open(raw):
    """То же сквозным ходом на СЧЁТНОМ параметре и полном стенде, где
    свод остаётся действительным: раньше «Норма: 10. Это норма»
    закрывало вопрос длиной окна 10 — номер строки уезжал в посадку
    сутками."""
    doc, rule, q = _prepared_count()
    summary = answers.apply_answers(doc, {QID_CNT: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    assert "days_off_window_days" not in rule["template_fit"]["params"]
    assert "номер строки анкеты" in q["answer"]["note"]
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("raw,number,was", [
    ("3. 3", "3.", 3),
    ("3) 3", "3)", 3),
    ("1. 1", "1.", 1),
    ("10. 10", "10.", 10),
    ("75. 75", "75.", 75),
])
def test_k9_a_line_number_repeating_the_value_is_still_refused(
        raw, number, was):
    """Изменённый исход, которого не держал ни один тест (проба
    рецензента p5): ответ, где номер строки СОВПАДАЕТ с числом за ним
    («3. 3» — строка 3, значение 3), прежде читался счётом. Совпадение
    догадкой не становится: что названо цифрой, ответ не говорит, и
    исход тот же fail-closed."""
    assert fp.line_number_record(raw) == number
    assert fp.parse_value(raw, CNT) is None
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None
    # тот же ответ БЕЗ номера читается как прежде
    assert fp.parse_value(str(was), CNT)[0] == was
    doc, rule, q = _prepared_count()
    assert answers.apply_answers(doc, {QID_CNT: raw})["unparsed"] == 1
    assert "номер строки анкеты" in q["answer"]["note"]
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]


def test_k9_the_note_is_bounded_when_the_line_number_is_giant():
    """Остаток ревью: ответ «⟨5000 цифр⟩. Это норма» давал заметку в
    5235 знаков, целиком уезжавшую в свод, в журнал следствий и в
    выгрузки. Теперь дословная выдержка обрезана многоточием — тем же
    пределом, каким ограничены прочие выдержки заметок модуля."""
    raw = "1" * 5000 + ". Это норма"
    doc, rule, q = _prepared_count()
    assert answers.apply_answers(doc, {QID_CNT: raw})["unparsed"] == 1
    note = q["answer"]["note"]
    assert "номер строки анкеты" in note and "…" in note
    assert len(note) < 400, f"заметка {len(note)} знаков"
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    assert m.validate_model(doc) == []


def test_k9_the_same_limit_holds_for_the_other_verbatim_quotes():
    """Предел один на ВСЕ дословные выдержки заметок модуля, а не
    заплата на номере строки: непрочитанная числовая запись длиной 200
    цифр называется обрезанной так же. Чужое слово единицы длиной не
    растёт — его словарь конечен, — и предел ему безвреден."""
    raw = "600 минут согласно п. " + "4" * 200
    doc, rule, q = _prepared()
    assert answers.apply_answers(doc, {QID: raw})["unparsed"] == 1
    note = q["answer"]["note"]
    assert "цифры вне числового выражения" in note and "…" in note
    assert len(note) < 400, f"заметка {len(note)} знаков"
    assert m.is_open_question(q) and QID in rule["blockers"]


# =====================================================================
# ДЕСЯТОЕ неавторское ревью K02 (12.09.2026) — пять находок.
#
# B-1 (блокирующая). ОБОЗНАЧЕНИЕМ считался токен из одних дефисов и
# подчёркиваний: `_CODE_RE`/`_CODE_TOKEN_RE` не требовали ни буквы, ни
# цифры. Ответ «нет данных - » садился в посадку правила-носителя
# значением «-», «A320 - B738» — перечнем «-, A320, B738», живая книга
# polyaris-nano (Q-3a-критерий-пейринга) — перечнем «-, 100»: в
# выпускаемую постановку входил тип судна, которого авиакомпания не
# называла, статус «применён», блокировка снята (принципы 3 и 4 ВМП).
# Исход зависел и от того, какое тире набрано: «A320 — B738»
# разбиралось верно, «A320 - B738» — нет (принцип 2).
#
# B-2 (блокирующая). Запрет «двоеточие при цифре» ставился ради записей
# времени, но у записи времени цифра стоит ЗА двоеточием, а не перед
# ним, и запрет гасил перечень после вводной части, кончающейся
# номером: «Вопрос 2: 1. A320» → «1, 2» (обозначение пропадало совсем),
# «Строка 1: 1) A320» → «1, A320», «Пункт 3: 1) A320, 2) B738» → «1, 2,
# 3, A320, B738», тогда как двухстрочные близнецы всех трёх отказывали
# с блокировкой.
#
# B-3 (блокирующая). Список вводных оборотов лексически неполон: «Типы
# судов, 1) A320, 2) B738» и «Типы судов то есть 1) A320» текли
# перечнем «1, 2, A320, B738», а двухстрочные близнецы отказывали — та
# самая разноголосица «Enter / не Enter», ради которой правился девятый
# круг.
#
# K-1 (критическая). Исход решал РЕГИСТР первой буквы пояснения:
# разошлись 11 пар из 11 — «3 — по документу» давало значение 3, а
# «3 — По документу» отказывало.
#
# K-2 (критическая, качество тестов). Несущая клауза «начало строки
# СЫРОГО текста» (`_raw_line_starts`) не была защищена НИ ОДНИМ тестом:
# её снятие оставляло набор зелёным, хотя 9 входов из 12 снова садились
# в посадку. Причина слепоты: все многострочные случаи набора написаны
# с ДВОЕТОЧИЕМ во вводной части, а такие перекрыты `_list_starts`;
# вводная БЕЗ знака препинания не покрыта была ничем.
# =====================================================================

# --- B-1: обозначение обязано нести буквенно-цифровой знак -----------

@pytest.mark.parametrize("unit,raw,was,want", [
    ("code", "-", "-", None),
    ("code_list", "-", "-", None),
    ("code", "_", "_", None),
    ("code", "--", "--", None),
    ("code_list", "нет данных - ", "-", None),
    ("code_list", "- , A320", "-, A320", "A320"),
    ("code_list", "A320, -, B738", "-, A320, B738", "A320, B738"),
    ("code_list", "A320 - B738", "-, A320, B738", "A320, B738"),
    ("code_list", "A320 - 737", "-, 737, A320", "737, A320"),
    ("code_list", "10 - 12", "-, 10, 12", "10, 12"),
    # K02 context law: duration words cannot become two aircraft codes.
    ("code_list", "600 мин. - 12 ч.", "-, 12, 600", None),
])
def test_k10_a_designation_must_carry_a_letter_or_digit(unit, raw, was, want):
    """«Минус» находки B-1: `was` — то ЛОЖНОЕ значение, что уезжало в
    посадку правила-носителя, когда обозначением становился сам дефис.
    Дефис и подчёркивание — знаки ВНУТРИ обозначения («A320-200»,
    «737-MAX»), сами по себе обозначением они не являются; где знаков
    справочника нет вовсе, значения нет и вопрос остаётся открытым."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    if want is None:
        assert got is None, f"прежнее ошибочное значение {was!r} вернулось"
    else:
        assert got is not None and got[0] == want
        assert "-" not in [t.strip() for t in got[0].split(",")]


@pytest.mark.parametrize("raw", [
    "A320-200", "737-800", "737-MAX", "O-S1-", "A320", "737", "_1",
])
def test_k10_a_hyphen_inside_a_designation_is_kept(raw):
    """Вторая сторона того же закона: дефис и подчёркивание ВНУТРИ
    обозначения ничего не теряют — правка судит токен целиком, а не
    выбрасывает знак."""
    assert fp.parse_value(raw, "code", fp.default_domain("code")) == (raw, raw)


@pytest.mark.parametrize("hyphen,dash", [
    ("A320 - B738", "A320 — B738"),
    ("A320 - 737", "A320 — 737"),
    ("- , A320", "— , A320"),
])
def test_k10_the_verdict_does_not_depend_on_which_dash_is_typed(hyphen, dash):
    """Принцип 2 ВМП предметно: один и тот же ответ, отличающийся лишь
    тем, дефис набран или длинное тире, даёт ОДНО значение. Раньше
    длинное тире разбиралось верно, а дефис добавлял в перечень
    обозначение «-», которого авиакомпания не называла."""
    dom = fp.default_domain("code_list")
    a = fp.parse_value(hyphen, "code_list", dom)
    b = fp.parse_value(dash, "code_list", dom)
    assert a is not None and b is not None and a[0] == b[0]


QID_FLEET = "Q-3b-параметр-fleet_exclusions-fleet_types"


def _prepared_fleet():
    """Свод образца с носителем семьи перечня обозначений: вопрос
    параметра открыт, носитель заблокирован."""
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-П-2-3")
    rule["template_fit"] = {
        "category": "A", "registry_rev": m.registry_fingerprint(),
        "family": "fleet_exclusions", "params": {}}
    assert fp.ensure_family_param_questions(doc) == [QID_FLEET]
    q = next(x for x in doc["questions"] if x["id"] == QID_FLEET)
    assert QID_FLEET in rule["blockers"] and m.is_open_question(q)
    return doc, rule, q


def test_k10_an_answer_of_a_bare_dash_keeps_the_question_open():
    """B-1 сквозным ходом на настоящем apply_answers: «нет данных - »
    больше не садится в посадку обозначением «-» и не снимает
    блокировку — вопрос остаётся открытым, свод действителен."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: "нет данных - "})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    assert all("params" in e for e in m.validate_model(doc))


@pytest.mark.parametrize("raw,want", [
    ("A320 - B738", "A320, B738"),
    ("A320 — B738", "A320, B738"),
    ("A320, -, B738", "A320, B738"),
])
def test_k10_the_phantom_designation_does_not_reach_the_carrier(raw, want):
    """B-1 сквозным ходом: законный перечень садится БЕЗ призрачного
    обозначения «-». Раньше в посадку уезжало «-, A320, B738» — тип
    судна, которого авиакомпания не называла."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: raw})
    assert summary["applied"] == 1 and summary["unparsed"] == 0
    got = rule["template_fit"]["params"]["fleet_types"]["value"]
    assert got == want and "-" not in [t.strip() for t in got.split(",")]
    assert not m.is_open_question(q) and m.validate_model(doc) == []


def _h0_fleet(raw):
    """Настоящий свод эталона Н0 с настоящим носителем VN-070 (семья
    fleet_exclusions) и ответом анкеты — вход сборки пакета."""
    import json
    from pathlib import Path
    et = (Path(__file__).resolve().parent.parent / "open-bench"
          / "polyaris-h0" / "etalon" / "VMP-H0.json")
    doc = json.loads(et.read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"] if r["id"] == "VN-070")
    rule["template_fit"]["params"] = {}
    assert fp.ensure_family_param_questions(doc) == [QID_FLEET]
    q = next(x for x in doc["questions"] if x["id"] == QID_FLEET)
    answers.apply_answers(doc, {QID_FLEET: raw})
    return doc, rule, q


def _run_dir_of(doc, run):
    """Папка прогона со сводом и комплектом заморозки — как её читает
    сборщик пакета постановки."""
    import json
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    by = {}
    for holder in list(doc["rules"]) + list(doc["objectives"]):
        for s in holder["sources"]:
            by.setdefault(s["sid"], []).append(
                {"anchor": s["anchor"], "text": holder["statement"]})
    entries = [{"sid": sid, "file": f"{sid}.docx", "sha256": "0" * 64,
                "clauses": cl} for sid, cl in sorted(by.items())]
    kit = run / "freeze"
    (kit / "clauses").mkdir(parents=True)
    (kit / "manifest.json").write_text(json.dumps({"files": [
        {"sid": e["sid"], "file": e["file"], "sha256": e["sha256"]}
        for e in entries]}), encoding="utf-8")
    for e in entries:
        (kit / "clauses" / f"{e['sid']}.json").write_text(
            json.dumps(e, ensure_ascii=False), encoding="utf-8")
    return run


def test_k10_the_phantom_designation_never_reaches_the_released_model(
        tmp_path):
    """ТРЕТИЙ слой B-1 — сборка пакета постановки и её рубеж: ложное
    обозначение не доезжает до `model.json` ВЫПУСКАЕМОГО пакета.
    Законный ответ выпускается перечнем «A320, B738» без призрака «-»,
    а ответ «нет данных - », оставляющий вопрос открытым, выпуска не
    получает вовсе — рубеж говорит словами, а не молчит."""
    import json
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_fleet("A320 - B738")
    assert rule["template_fit"]["params"][
        "fleet_types"]["value"] == "A320, B738"
    files = etp.build_package(_run_dir_of(doc, tmp_path / "ok"))
    body = json.loads(files["model.json"].decode("utf-8"))
    out = next(r for r in body["rules"] if r["id"] == "VN-070")
    got = out["template_fit"]["params"]["fleet_types"]["value"]
    assert got == "A320, B738"
    assert "-" not in [t.strip() for t in got.split(",")]
    doc2, rule2, q2 = _h0_fleet("нет данных - ")
    assert rule2["template_fit"]["params"] == {}
    assert m.is_open_question(q2) and QID_FLEET in rule2["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc2, tmp_path / "bad"))


# --- B-2: вводная часть, кончающаяся номером вопроса или строки ------

@pytest.mark.parametrize("unit,raw,number,was", [
    ("code_list", "Строка 1: 1) A320", "1)", "1, A320"),
    ("code_list", "Вопрос 2: 1. A320", "1.", "1, 2"),
    ("code", "Вопрос 2: 1. A320", "1.", None),
    ("code_list", "Пункт 3: 1) A320, 2) B738", "1)", "1, 2, 3, A320, B738"),
    ("code_list", "Строка 10: 1) A320", "1)", "1, 10, A320"),
    ("code_list", "Вопрос 2; 1) A320", "1)", "1, 2, A320"),
])
def test_k10_a_lead_ending_in_a_number_still_opens_the_list(unit, raw, number,
                                                            was):
    """«Минус» находки B-2: запрет «двоеточие при цифре» гасил перечень
    там, где вводная часть кончается номером вопроса или строки, и
    `was` показывает, что уезжало в посадку. У «Вопрос 2: 1. A320»
    обозначение пропадало совсем — оставалось «1, 2»."""
    assert fp.line_number_record(raw) == number
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("lead", ["Строка 1:", "Вопрос 2:", "Пункт 3:",
                                  "Строка 10:", "Вопрос 2;"])
@pytest.mark.parametrize("body,number", [("1) A320, 2) B738", "1)"),
                                         ("1. A320", "1.")])
def test_k10_the_twin_of_a_numbered_lead_agrees(lead, body, number):
    """Принцип 2 ВМП: ответ, отличающийся лишь тем, нажал ли отвечающий
    Enter за вводной частью с номером, даёт ОДИН исход и один номер."""
    multi, single = lead + "\n" + body, lead + " " + body
    assert fp.line_number_record(single) == number
    assert fp.line_number_record(multi) == number
    for unit in ("code", "code_list"):
        dom = fp.default_domain(unit)
        assert fp.parse_value(single, unit, dom) is None
        assert fp.parse_value(multi, unit, dom) is None


@pytest.mark.parametrize("raw,want", [
    ("10:00", 600), ("1:30", 90), ("23:59", 1439), ("0:30", 30),
    ("Явка: 10:00", 600), ("Начало: 8:00", 480), ("Ответ: 10:00", 600),
])
def test_k10_time_records_survive_the_widened_colon(raw, want):
    """Положительный контроль B-2: расширение запрета не тронуло НИ
    ОДНОЙ записи времени — цифра по обе стороны двоеточия по-прежнему
    вводной частью не является."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, "time_of_day_minutes",
                         fp.default_domain("time_of_day_minutes"))
    assert got is not None and got[0] == want


def test_k10_the_colon_remainder_is_named_not_hidden():
    """ОСТАТОК B-2 назван вслух: «Строка 10:1) A320» — цифра по обе
    стороны двоеточия и пробела нет, и от записи времени «10:1» такая
    запись не отличается ни машиной, ни человеком. Там закон молчит, и
    ответ читается как прежде — это цена, а не оплошность."""
    assert fp.line_number_record("Строка 10:1) A320") == ""
    assert fp.line_number_record("Строка 10: 1) A320") == "1)"


@pytest.mark.parametrize("raw", [
    "Строка 1: 1) A320", "Вопрос 2: 1. A320", "Пункт 3: 1) A320, 2) B738",
])
def test_k10_the_numbered_lead_no_longer_lands_in_the_carrier(raw):
    """B-2 сквозным ходом на настоящем apply_answers: однострочный
    ответ с вводной частью, кончающейся номером вопроса, больше не
    садится в посадку и не снимает блокировку."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    assert "номер строки анкеты" in q["answer"]["note"]
    assert all("params" in e for e in m.validate_model(doc))


# --- B-3: вводная часть вне словаря оборотов -------------------------

@pytest.mark.parametrize("lead", [
    "Типы судов, ",
    "Типы судов, то бишь ",
    "Типы судов, как их называет документ, ",
    "Типы судов то есть ",
    "Типы судов как то ",
])
def test_k10_a_lead_outside_the_dictionary_still_opens_the_list(lead):
    """«Минус» находки B-3: словарь вводных оборотов полным не бывает.
    Раньше «Типы судов, 1) A320, 2) B738» и «Типы судов то есть 1)
    A320» садились в посадку перечнем «1, 2, A320, B738», а их
    двухстрочные близнецы отказывали. Теперь вводную часть кончает и
    ЗАПЯТАЯ, до которой в ответе не было ни одной цифры, — признак без
    словаря, и он закрывает обороты, которых в списке нет."""
    raw = lead + "1) A320, 2) B738"
    twin = lead.rstrip().rstrip(",") + "\n1) A320, 2) B738"
    assert fp.line_number_record(raw) == "1)"
    assert fp.line_number_record(twin) == "1)"
    for unit in ("code", "code_list"):
        dom = fp.default_domain(unit)
        assert fp.parse_value(raw, unit, dom) is None
        assert fp.parse_value(twin, unit, dom) is None


def test_k10_a_lead_without_punctuation_keeps_the_count_question_open():
    """Тот же «минус» на счётном параметре и сквозным ходом: «Норма, 10.
    Это норма» читалось счётом 10 — номер строки уезжал в посадку
    длиной окна."""
    doc, rule, q = _prepared_count()
    assert fp.line_number_record("Норма, 10. Это норма") == "10."
    summary = answers.apply_answers(doc, {QID_CNT: "Норма, 10. Это норма"})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "days_off_window_days" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("raw,unit,want", [
    ("Флот: 320, 737. Конец", "code_list", "320, 737"),
    ("737, 320", "code_list", "320, 737"),
    ("A320, B738", "code_list", "A320, B738"),
    ("Типы: A320, B738", "code_list", "A320, B738"),
    ("Типы, а именно A320, B738", "code_list", "A320, B738"),
    ("A320-200, B737-800", "code_list", "A320-200, B737-800"),
])
def test_k10_a_comma_after_a_digit_is_not_a_lead(raw, unit, want):
    """Граница бессловарного признака: запятая, до которой цифра уже
    была, кончает не вводную часть, а ЗНАЧЕНИЕ — перечисление уже идёт,
    и такие ответы читаются как прежде. Перегиба нет: на переборе 2640
    входов, на 158 законных ответах и на 103 ответах 14 живых книг
    признак не изменил ни одного исхода."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


def test_k10_the_lead_comma_remainder_is_named_not_hidden():
    """ОСТАТОК B-3 назван вслух: вводный оборот БЕЗ знака препинания и
    вне словаря («Типы судов сиречь 1) A320») перечня не вводит.
    Бессловарный признак «любое слово до первой цифры» измерен и
    ОТВЕРГНУТ как перегиб: он менял 658 исходов перебора из 2640 —
    «Пункт 4. 600 минут» и «Раздел 3. 12 часов» стали бы отказами, а
    они законны."""
    assert fp.line_number_record("Типы судов сиречь 1) A320") == ""
    for raw in ("Пункт 4. 600 минут", "Раздел 3. 12 часов",
                "Приказ №12 от 01.09"):
        assert fp.line_number_record(raw) == ""


# --- K-1: исход не решается регистром --------------------------------

K10_CASE_PAIRS = [
    ("3 — по документу", "3 — По документу"),
    ("3: по документу", "3: По документу"),
    ("2 — два пилота", "2 — Два пилота"),
    ("2: два пилота", "2: Два пилота"),
    ("12 — норма отдыха", "12 — Норма отдыха"),
    ("12: норма отдыха", "12: Норма отдыха"),
    ("600 — минут по приказу", "600 — Минут по приказу"),
    ("3 — согласно приказу", "3 — Согласно приказу"),
    ("10 - часов", "10 - Часов"),
    ("3 — это норма", "3 — Это норма"),
    ("600 — норма налёта", "600 — Норма налёта"),
]


@pytest.mark.parametrize("lower,upper", K10_CASE_PAIRS)
def test_k10_the_verdict_does_not_depend_on_the_case_of_the_explanation(
        lower, upper):
    """«Минус» находки K-1: исход решал РЕГИСТР первой буквы пояснения
    — разошлись 11 пар из 11. «3 — по документу» давало значение 3, а
    «3 — По документу» отказывало номером строки «3 —», хотя ответ один
    и тот же, и докстрока сама называла его законным. Регистр —
    привычка набора, а не закон (принцип 2 ВМП). Теперь у обеих
    половин пары ОДИН номер и ОДНО значение у КАЖДОЙ единицы."""
    assert fp.line_number_record(lower) == fp.line_number_record(upper) == ""
    for unit in ("code", "code_list", CNT, CH, HPCH, U,
                 "time_of_day_minutes"):
        dom = fp.default_domain(unit)
        a = fp.parse_value(lower, unit, dom)
        b = fp.parse_value(upper, unit, dom)
        assert (a is None) == (b is None), unit
        if a is not None:
            assert a[0] == b[0], unit


@pytest.mark.parametrize("raw,number", [
    ("1 - A320", "1 -"), ("1 - a320", "1 -"),
    ("1: A320", "1:"), ("1: a320", "1:"),
    ("2 — B738", "2 —"), ("2 — b738", "2 —"),
    ("1 – A320", "1 –"), ("1 – a320", "1 –"),
])
def test_k10_a_designation_after_a_weak_sign_is_a_line_number_in_any_case(
        raw, number):
    """Вторая сторона K-1: содержание строки узнаётся по ОБОЗНАЧЕНИЮ, а
    не по регистру, поэтому «1 - a320» отказывает так же, как
    «1 - A320». Раньше строчное обозначение закон пропускал, и ответ
    уезжал в посадку перечнем «-, 1, a320»."""
    assert fp.line_number_record(raw) == number
    for unit in ("code", "code_list"):
        assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


def test_k10_the_case_pair_gives_one_verdict_and_one_note_through_the_stage():
    """K-1 сквозным ходом на настоящем apply_answers: половины пары,
    различающиеся ТОЛЬКО регистром пояснения, дают один исход И одну
    заметку. Раньше ответ с прописной буквы оставлял вопрос открытым и
    блокировал носителя, а его строчный близнец вносил значение 3."""
    seen = []
    for raw in ("3 — по документу", "3 — По документу"):
        doc, rule, q = _prepared_count()
        summary = answers.apply_answers(doc, {QID_CNT: raw})
        assert summary["applied"] == 1 and summary["unparsed"] == 0
        assert q["answer"]["status"] == "применён"
        assert rule["template_fit"]["params"][
            "days_off_window_days"]["value"] == 3
        assert not m.is_open_question(q) and m.validate_model(doc) == []
        seen.append(q["answer"]["note"])
    assert seen[0] == seen[1], "заметки зеркальной пары разошлись"


# --- K-2: клауза «начало строки сырого текста» под тестом ------------

@pytest.mark.parametrize("unit,raw,number,lands", [
    ("code_list", "Типы судов\n1) A320\n2) B738", "1)", "1, 2, A320, B738"),
    ("code_list", "Список типов\n1) A320\n2) B738", "1)", "1, 2, A320, B738"),
    ("code_list", "Перечень\n1) A320", "1)", "1, A320"),
    ("code_list", "Ответ\n1) A320", "1)", "1, A320"),
    ("code_list", "Вот что применяется\n1) A320", "1)", "1, A320"),
    ("code_list", "Типы судов\n1: A320", "1:", "1, A320"),
    ("code_list", "Типы судов\n1 - A320", "1 -", "1, A320"),
    ("code", "Типы судов\n1. A320", "1.", None),
    (CNT, "Норма\n10. Это норма", "10.", 10),
    (CH, "Норма\n75. Это норма", "75.", 75),
])
def test_k10_the_start_of_a_raw_line_is_a_place_of_its_own(unit, raw, number,
                                                           lands):
    """K-2: НЕСУЩАЯ клауза «начало строки СЫРОГО текста»
    (`_raw_line_starts`) не была защищена ни одним тестом — её снятие
    оставляло набор зелёным. Причина слепоты: все прежние многострочные
    случаи набора написаны с ДВОЕТОЧИЕМ во вводной части, а такие
    перекрыты `_list_starts`; вводная БЕЗ знака препинания не покрыта
    была ничем. `lands` — то, что садится в посадку правила-носителя,
    если клаузу снять."""
    assert fp.line_number_record(raw) == number
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("raw", [
    "Типы судов\n1) A320\n2) B738", "Перечень\n1) A320",
    "Вот что применяется\n1) A320", "Типы судов\n1: A320",
])
def test_k10_a_multiline_answer_without_punctuation_keeps_the_question_open(
        raw):
    """K-2 сквозным ходом на настоящем apply_answers: многострочный
    ответ, вводная часть которого не кончается знаком препинания,
    больше не садится в посадку и не снимает блокировку."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    assert "номер строки анкеты" in q["answer"]["note"]
    assert all("params" in e for e in m.validate_model(doc))


@pytest.mark.parametrize("raw,unit,want", [
    ("Типы судов\nA320, B738", "code_list", "A320, B738"),
    ("Норма\n600 минут", U, 600),
    ("Ответ\nA320, B738", "code_list", "A320, B738"),
])
def test_k10_a_line_break_without_a_number_still_reads(raw, unit, want):
    """Положительный контроль к K-2: сам по себе перенос строки ничего
    не отвергает — отвергает ЗНАК НОМЕРА в начале строки."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


@pytest.mark.parametrize("raw,unit,want", [
    ("737-800. Конец", "code", "737-800"),
    ("A320-200. Конец", "code", "A320-200"),
    ("737-MAX. Конец", "code", "737-MAX"),
    ("Флот: A320-200. Конец", "code_list", "A320-200"),
    ("Типы: 737-800, A320-200. Конец", "code_list", "737-800, A320-200"),
])
def test_k10_a_glued_hyphen_does_not_open_a_list(raw, unit, want):
    """Сторож клаузы «тире перечня отделено ПРОБЕЛАМИ» (мутация M7
    десятого круга осталась бы зелёной без него): дефис ВПЛОТНУЮ
    соединяет запись — «A320-200», «737-800», «10-12», — а перечня не
    вводит. Иначе «737-800. Конец» дало бы номер строки «800.» и
    законное обозначение уехало бы в отказ."""
    assert fp.line_number_record(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


@pytest.mark.parametrize("raw", ["10-12. Конец", "600-720. Конец"])
def test_k10_a_glued_hyphen_in_a_range_is_not_a_line_number(raw):
    """Та же клауза у диапазона: «10-12. Конец» — не строка 12, а
    диапазон, который закон охвата цифр судит своим законом."""
    assert fp.line_number_record(raw) == ""


# =====================================================================
# Одиннадцатый круг — свободная линия взломщика: шесть находок.
# K02-11-1 — слово ВЕЛИЧИНЫ при числе судится единицей параметра (было
# закрыто только для минут, часов и суток); K02-11-2 — тире; K02-11-3 —
# омоглифы; K02-11-4 — доли и проценты; K02-11-5 — охват у обозначений;
# K02-11-6 — цифра значения только 0–9 (своя находка).
# =====================================================================

#: Счётный параметр настоящего эталона Н0 — для сквозного контроля
#: находки 1 на пути СБОРКИ ПАКЕТА.
QID_CREW = "Q-3b-параметр-crew_composition-crew_size"


def _h0_count(raw):
    """Настоящий свод эталона Н0 с настоящим носителем VN-081 (семья
    crew_composition, счётный параметр crew_size) и ответом анкеты."""
    import json
    from pathlib import Path
    et = (Path(__file__).resolve().parent.parent / "open-bench"
          / "polyaris-h0" / "etalon" / "VMP-H0.json")
    doc = json.loads(et.read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"] if r["id"] == "VN-081")
    rule["template_fit"]["params"] = {}
    assert fp.ensure_family_param_questions(doc) == [QID_CREW]
    q = next(x for x in doc["questions"] if x["id"] == QID_CREW)
    answers.apply_answers(doc, {QID_CREW: raw})
    return doc, rule, q


# --- K02-11-1: чужое слово величины при числе ------------------------

FOREIGN_AT_NUMBER = [
    ("Не менее 2 недель", "недель"), ("2 недели", "недели"),
    ("2 нед.", "нед"), ("3 месяца", "месяца"), ("6 мес.", "мес"),
    ("1 год", "год"), ("5 лет", "лет"), ("2 квартала", "квартала"),
    ("10 секунд", "секунд"), ("30 сек.", "сек"),
    ("2 weeks", "weeks"), ("3 months", "months"), ("1 year", "year"),
    ("90 seconds", "seconds"),
    ("2 декады", "декады"), ("2 полугодия", "полугодия"),
]


@pytest.mark.parametrize("raw,word", FOREIGN_AT_NUMBER)
@pytest.mark.parametrize("unit", [CNT, CH, HPCH, U, TOD])
def test_k11_a_foreign_unit_word_at_a_number_is_not_a_value(raw, word, unit):
    """НАХОДКА 1 (блокирующая). Слово величины при числе судится
    ЕДИНИЦЕЙ ПАРАМЕТРА — и словарь этого закона знал прежде ровно три
    слова (минуты, часы, сутки). Всё остальное — недели, месяцы,
    кварталы, годы, секунды, латинские написания — молча отваливалось
    вместе со своим смыслом, и «Не менее 2 недель» у счётного параметра
    давало 2 при прочитанных человеком 14.

    Обратимость: на прежнем коде parse_value возвращал (2, …) у счёта и
    у часов кредита, и ответ садился в посадку правила-носителя."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.foreign_unit_word(raw, unit) == word


@pytest.mark.parametrize("raw,want", [
    ("8 выходных дней", 8), ("5 смен", 5), ("3 рейса", 3),
    ("2 экипажа", 2), ("14", 14), ("2 человека", 2),
])
def test_k11_a_noun_at_a_number_is_still_a_count(raw, want):
    """Положительный контроль к находке 1 и ЦЕНА закона, названная
    вслух: существительное при числе единицей не является — закон
    судит СЛОВАРЬ величин, а не всякое слово. Поэтому единица, в
    словарь не попавшая, пройдёт молча; полный словарь
    `unit_words`/`subject_words` — каталог 0.6.0, волна 17б."""
    got = fp.parse_value(raw, CNT, fp.default_domain(CNT))
    assert got is not None and got[0] == want


def test_k11_the_note_names_the_foreign_word_and_the_expected_unit():
    """Второй слой находки 1 — настоящий answers.apply_answers: ответ
    не применён, носитель заблокирован, заметка называет ПРОЧИТАННОЕ
    слово и ожидаемую единицу."""
    doc, rule, q = _prepared_count()
    summary = answers.apply_answers(doc, {QID_CNT: "Не менее 2 недель"})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {"min_days_off_in_window":
                                              {"value": 2,
                                               "quote": "две в неделю"}}
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    note = q["answer"]["note"]
    assert "«недель»" in note and "счётного параметра" in note
    assert "укажите число без слова единицы" in note


def test_k11_the_false_count_never_reaches_the_released_model(tmp_path):
    """ТРЕТИЙ слой находки 1 — сборка пакета постановки: ложное
    значение не доезжает до `model.json` ВЫПУСКАЕМОГО пакета. Прежде
    «Не менее 2 недель» вносилось счётом 2, гейт выпуска был ЗЕЛЁН и
    пакет выпускался с числом, которого авиакомпания не называла.
    Теперь вопрос остаётся открытым, носитель заблокирован, и выпуск
    отказывает словами."""
    import json
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_count("Не менее 2 недель")
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CREW in rule["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc, tmp_path / "bad"))
    doc2, rule2, q2 = _h0_count("2")
    assert rule2["template_fit"]["params"]["crew_size"]["value"] == 2
    files = etp.build_package(_run_dir_of(doc2, tmp_path / "ok"))
    body = json.loads(files["model.json"].decode("utf-8"))
    out = next(r for r in body["rules"] if r["id"] == "VN-081")
    assert out["template_fit"]["params"]["crew_size"]["value"] == 2


# --- K02-11-2: тире --------------------------------------------------

def test_k11_the_dash_class_is_derived_from_unicode():
    """СТОРОЖ клаузы: перечень знаков тире равен разряду Юникода
    Dash_Punctuation (Pd) плюс мягкий перенос U+00AD и знак минус
    U+2212. Перечень в коде — константа (класс регулярки собирается
    один раз), и сторож пересчитывает его по `unicodedata`, чтобы ни
    правка рукой, ни новая версия Юникода не оставили щели."""
    import unicodedata
    want = {chr(c) for c in range(0x110000)
            if unicodedata.category(chr(c)) == "Pd"}
    want |= {"\u00ad", "\u2212"}
    assert set(fp._DASHES) == want


DASH_CHARS = ["-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014",
              "\u2015", "\u00ad", "\uff0d", "\u2212"]


@pytest.mark.parametrize("dash", DASH_CHARS)
def test_k11_every_dash_glues_the_record_the_same_way(dash):
    """НАХОДКА 2. Исход разбора решало, КАКИМ знаком набрано тире:
    U+2010, U+2011, U+2012, U+00AD и U+FF0D в склейку не входили, и
    «7‐ми суток» давало счёт 7 там, где «7-ми суток» отказывает,
    «A320‐200» — перечень из двух обозначений вместо одного. Вид один,
    исход разный — принцип 2 ВМП. Теперь исход один на все знаки."""
    assert fp.parse_value("7" + dash + "ми суток", CNT,
                          fp.default_domain(CNT)) is None
    assert fp.parse_value("600" + dash + "минут", CNT,
                          fp.default_domain(CNT)) is None
    assert fp.parse_value("10" + dash + "12 минут", U,
                          fp.default_domain(U)) is None


@pytest.mark.parametrize("dash", [d for d in DASH_CHARS if d != "-"])
def test_k11_only_the_plain_hyphen_lives_inside_a_designation(dash):
    """Та же находка 2 у обозначений — и ГРАНИЦА закона, названная
    вслух: знаком ВНУТРИ обозначения справочника служит только дефис
    U+002D («A320-200», «737-MAX»), потому что обозначение уезжает в
    постановку дословно и сверяется с данными авиакомпании
    (). Всякое иное тире обозначения не
    продолжает: прежде «A320‐200» давало ДВА обозначения «200, A320»,
    которых авиакомпания не называла, теперь — отказ с заметкой."""
    assert fp.parse_value("A320" + dash + "200", "code_list",
                          fp.default_domain("code_list")) is None
    assert fp.unread_code_records("A320" + dash + "200",
                                  "code_list") == ["A320" + dash + "200"]
    got = fp.parse_value("A320-200", "code_list",
                         fp.default_domain("code_list"))
    assert got is not None and got[0] == "A320-200"


@pytest.mark.parametrize("dash", DASH_CHARS)
def test_k11_a_dash_in_spaces_still_separates(dash):
    """ЦЕНА закона названа: склейка — это знак ВПЛОТНУЮ к записи.
    Тире, отделённое пробелами, ничего не склеивает и читается как
    прежде — иначе закон съел бы обычный ответ «значение — пояснение»."""
    got = fp.parse_value("600 минут " + dash + " норма", U,
                         fp.default_domain(U))
    assert got is not None and got[0] == 600
    got = fp.parse_value("A320 " + dash + " B738", "code_list",
                         fp.default_domain("code_list"))
    assert got is not None and got[0] == "A320, B738"


# --- K02-11-3: омоглифы ----------------------------------------------

MIXED = [
    ("2 c\u0443\u0442ok", "c\u0443\u0442ok"),      # латинские c, o, k
    ("2 \u0441ac\u0430", "\u0441ac\u0430"),        # латинские a, c
    ("20 \u043cu\u043d", "\u043cu\u043d"),         # латинская u
    ("H\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442\u0441\u044f",
     "H\u0435"),                                      # латинская H
]


@pytest.mark.parametrize("raw,word", MIXED)
@pytest.mark.parametrize("unit", [CNT, CH, HPCH, U, TOD, "code", "code_list"])
def test_k11_a_mixed_script_word_is_not_read(raw, word, unit):
    """НАХОДКА 3. Слово, записанное ДВУМЯ алфавитами сразу, на вид одно,
    а по знакам другое. Прежде «2 сacа» и «20 мuн» давали счёт 2 и 20
    (слово единицы не узнавалось и молча отваливалось), а «2 cутok» —
    счёт 2. Теперь такое слово не читается вовсе: вопрос остаётся
    открытым, заметка называет слово."""
    assert fp.mixed_script_word(raw) == word
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("raw", [
    "600 \u043c\u0438\u043d\u0443\u0442. H\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442\u0441\u044f \u0434\u043b\u044f SVO.",
    "600 \u043c\u0438\u043d\u0443\u0442. \u041d\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442c\u044f \u0434\u043b\u044f SVO.",
    "\u041d\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442\u0441\u044f \u0434\u043b\u044f SVO. \u0412 \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044bx \u0431\u0430\u0437\u0430x 600 \u043c\u0438\u043d\u0443\u0442.",
])
def test_k11_a_homoglyph_decline_no_longer_hides_the_value(raw):
    """Самая дорогая половина находки 3: омоглиф в ФРАЗЕ ОТКАЗА.
    `_DECLINE_RE` набран кириллицей, и «600 минут. Hе применяется для
    SVO.» с латинской «H» отказом не считалось — 600 садилось в
    посадку правила-носителя, исключение молча исчезало из выпускаемой
    постановки, статус «применён», блокировка снята. Тот же ответ
    кириллицей отказывает. Теперь отказывают оба."""
    assert fp.mixed_script_word(raw) != ""
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None


@pytest.mark.parametrize("raw,word", [
    ("2 cyt", "cyt"), ("2 cytok", "cytok"), ("6 mec", "mec"),
])
def test_k11_a_latin_lookalike_unit_word_is_judged_like_its_twin(raw, word):
    """Вторая половина находки 3: слово, набранное ЛАТИНИЦЕЙ ЦЕЛИКОМ,
    смешанным не является, но на вид это то же слово единицы. Свёртка
    омоглифов (_folded) применяется к УЗНАВАНИЮ слова величины — то
    есть в сторону ОТКАЗА. Читать по свёртке ЗНАЧЕНИЕ код не станет:
    это была бы догадка, и «2 cyt» у длительности остаётся нечитаемым,
    а не 2880."""
    assert fp.mixed_script_word(raw) == ""
    assert fp.foreign_unit_word(raw, CNT) == word
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None


def test_k11_the_note_names_the_mixed_word():
    """Заметка находки 3 на настоящем apply_answers."""
    doc, rule, q = _prepared()
    raw = ("600 \u043c\u0438\u043d\u0443\u0442. H\u0435 "
           "\u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442"
           "\u0441\u044f \u0434\u043b\u044f SVO.")
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {"min_rest_home":
                                              {"value": 720,
                                               "quote": "\u0440\u0430\u043d"
                                                        "\u0435\u0435 08:00"}}
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "\u0434\u0432\u0443\u043c\u044f \u0430\u043b\u0444"
    assert "двумя алфавитами" in q["answer"]["note"]


@pytest.mark.parametrize("raw,unit,want", [
    ("600 минут", U, 600), ("A320, B738", "code_list", "A320, B738"),
    ("SVO — 600 минут", U, 600), ("75 часов", CH, 75),
])
def test_k11_a_one_script_answer_reads_as_before(raw, unit, want):
    """Положительный контроль к находке 3: ответ, набранный ОДНИМ
    алфавитом (в каждом слове), читается как прежде — латинские
    обозначения рядом с русскими словами смешанным словом не являются."""
    assert fp.mixed_script_word(raw) == ""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


# --- K02-11-4: доли и проценты ---------------------------------------

SHARE = [("50 %", "%"), ("50%", "%"), ("50 процентов", "процентов"),
         ("50 проц.", "проц"), ("не более 10 %", "%"),
         ("0,5 доли", "доли"), ("25 промилле", "промилле")]


@pytest.mark.parametrize("raw,word", SHARE)
@pytest.mark.parametrize("unit", [CNT, CH, HPCH, U, TOD])
def test_k11_a_share_at_a_number_is_a_foreign_unit(raw, word, unit):
    """НАХОДКА 4. Ни одна единица каталога 0.5.0 доли не читает,
    а знак «%» и слово «процентов» молча отваливались: «не более 10 %»
    у часов на час кредита давало 10 при прочитанных человеком 0,1 —
    ошибка в сто раз. Теперь доля отказывает и называется заметкой."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.foreign_unit_word(raw, unit) == word


@pytest.mark.parametrize("raw,want", [("0,5", 0.5), ("1,5", 1.5),
                                      ("0.25", 0.25), ("2", 2)])
def test_k11_a_bare_fraction_still_reads_at_hours_per_credit_hour(raw, want):
    """Положительный контроль к находке 4 и ЦЕНА закона: доля БЕЗ знака
    и без слова — законное значение единицы `hours_per_credit_hour`,
    и она читается как прежде. Закон судит только ПОМЕТКУ доли."""
    got = fp.parse_value(raw, HPCH, fp.default_domain(HPCH))
    assert got is not None and got[0] == want


# --- K02-11-5: охват у обозначений -----------------------------------

CODE_STRAY = [
    ("A320, B738 — см. п. 4.2", "4.2", "2, 4, A320, B738"),
    ("Как в п. 4.2", "4.2", "2, 4"),
    ("n/a", "n/a", "a, n"),
    ("N/A", "N/A", "A, N"),
    ("\u0410320", "\u0410320", "320"),
    ("10:00", "10:00", "00, 10"),
    ("A320\u2010200", "A320\u2010200", "200, A320"),
]


@pytest.mark.parametrize("raw,record,was", CODE_STRAY)
def test_k11_unread_signs_refuse_a_designation_answer(raw, record, was):
    """НАХОДКА 5. У числовых единиц каждая цифра предложения обязана
    лежать внутри прочитанного токена (_digits_covered), а у
    ОБОЗНАЧЕНИЙ такого закона не было вовсе: выбиралось всё похожее на
    знаки справочника, и прочитанное частью уезжало в посадку без
    единого слова — `was` показывает, что именно. Теперь знак записи,
    не вошедший в прочитанное обозначение, отказывает и называется."""
    assert fp.unread_code_records(raw, "code_list") == [record]
    assert fp.parse_value(raw, "code_list",
                          fp.default_domain("code_list")) is None
    assert fp.parse_value(raw, "code", fp.default_domain("code")) is None


def test_k11_a_list_token_must_be_a_designation():
    """Вторая половина находки 5: токен перечня обязан быть
    ОБОЗНАЧЕНИЕМ (_is_code). Прежде единица `code` токен длиннее 16
    знаков отвергала, а `code_list` — вносила в посадку: один и тот же
    токен был обозначением в перечне и не был им в одиночку."""
    long = "ATR72-500-600-700-800-900-1000"
    assert len(long) > 16 and not fp._is_code(long)
    assert fp.parse_value("A320, B738, " + long, "code_list",
                          fp.default_domain("code_list")) is None
    assert fp.unread_code_records("A320, " + long, "code_list") == [long]


@pytest.mark.parametrize("raw,want", [
    ("737", "737"), ("737, 320", "320, 737"),
    ("A320, B738", "A320, B738"), ("A320,B738", "A320, B738"),
    ("Типы: A320 и B738", "A320, B738"), ("A320-200", "A320-200"),
    ("A320 - B738", "A320, B738"), ("B737-800", "B737-800"),
    ("Тип судна: A320", "A320"), ("A_320", "A_320"),
])
def test_k11_a_clean_designation_answer_reads_as_before(raw, want):
    """Положительный контроль к находке 5 и ЦЕНА закона: русские слова
    охват не судит (как и у чисел), запятая и пробел запись не
    склеивают, а дефис ВПЛОТНУЮ — знак внутри обозначения. Обозначение
    ИЗ ОДНИХ ЦИФР («737», «320» — типы судов каталога 0.5.0) читается
    как прежде: цену этой границы назвал десятый круг."""
    got = fp.parse_value(raw, "code_list", fp.default_domain("code_list"))
    assert got is not None and got[0] == want


def test_k11_the_phantom_code_never_reaches_the_released_model(tmp_path):
    """ТРЕТИЙ слой находки 5 — сборка пакета постановки: обозначения,
    выдуманные из ссылки на пункт, не доезжают до `model.json`
    выпускаемого пакета."""
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_fleet("A320, B738 — см. п. 4.2")
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc, tmp_path / "bad"))


def test_k11_the_note_names_the_unread_signs():
    """Заметка находки 5 на настоящем apply_answers."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: "Как в п. 4.2"})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    note = q["answer"]["note"]
    assert "знаки вне прочитанных обозначений" in note and "„4.2“" in note


# --- K02-11-6: цифра значения — только 0–9 (своя находка) ------------

NON_ASCII_DIGITS = ["\uff13\uff10 минут", "\u0663\u0660 минут", "\uff15",
                    "\uff11\uff10:\uff10\uff10"]


@pytest.mark.parametrize("raw", NON_ASCII_DIGITS)
@pytest.mark.parametrize("unit", [CNT, CH, HPCH, U, TOD])
def test_k11_a_non_ascii_digit_is_not_a_value(raw, unit):
    """СВОЯ НАХОДКА того же класса, что омоглифы букв: `\\d` в Python —
    это ВСЯКАЯ десятичная цифра Юникода, и «３０ минут» (полноширинные)
    и «٣٠ минут» (арабо-индийские) читались как 30 — Decimal берёт их
    наравне с ASCII, а длина токена считалась по правилам ASCII
    (ведущий полноширинный ноль не снимался). Значение читается только
    из цифр 0–9; иная цифра остаётся вне токена и отказывает законом
    охвата, а заметка называет запись."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


def test_k11_the_note_names_the_non_ascii_record():
    """Заметка своей находки: непрочитанная запись называется дословно."""
    assert fp.unread_digits("\uff13\uff10 минут", U) == ["\uff13\uff10"]
    doc, rule, q = _prepared()
    answers.apply_answers(doc, {QID: "\uff13\uff10 минут"})
    note = q["answer"]["note"]
    assert "цифры вне числового выражения" in note


@pytest.mark.parametrize("raw,unit,want", [
    ("30 минут", U, 30), ("10:00", TOD, 600), ("5", CNT, 5),
    ("010 минут", U, 10),
])
def test_k11_an_ascii_number_reads_as_before(raw, unit, want):
    """Положительный контроль к своей находке."""
    got = fp.parse_value(raw, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


# =====================================================================
# ДВЕНАДЦАТОЕ неавторское ревью K02 (13.09.2026): три БЛОКИРУЮЩИЕ
# находки одной природы — закон был закрыт ПОЛОВИНОЙ, и открытая
# половина давала НЕВЕРНОЕ ЗНАЧЕНИЕ в выпускаемой постановке при
# статусе «применён» и снятой блокировке.
#   Б1 — фраза отказа, набранная ЛАТИНИЦЕЙ ЦЕЛИКОМ («He применяется»);
#   Б2 — слово величины ЧЕРЕЗ ОПРЕДЕЛЕНИЕ («2 календарных недель» → 2);
#   Б3 — кириллическое слово, приклеенное к цифрам ЧЕРЕЗ ТИРЕ
#        («Ту-154» → обозначение «-154»).
# У каждой — три слоя: библиотечная функция, настоящий
# answers.apply_answers и путь СБОРКИ ПАКЕТА на эталоне Н0.
# =====================================================================

# --- Б1: фраза отказа целиком латиницей ------------------------------

#: Один и тот же отказ в трёх написаниях. Кириллица — закрыта шестым
#: кругом; СМЕШАННОЕ («Hе» — латинская H при кириллической «е») —
#: одиннадцатым; ЦЕЛИКОМ ЛАТИНСКОЕ («He» — обе буквы латинские) не было
#: закрыто ничем.
B1_SPELLINGS = [
    ("600 минут. Не применяется для базы SVO.", "кириллица"),
    ("600 минут. Hе применяется для базы SVO.", "смешанное"),
    ("600 минут. He применяется для базы SVO.", "целиком латинское"),
]


@pytest.mark.parametrize("raw,how", B1_SPELLINGS)
def test_k12_one_answer_in_three_spellings_gives_one_verdict(raw, how):
    """НАХОДКА Б1 (блокирующая) и принцип 2 ВМП. На вид это ОДИН ответ:
    авиакомпания назвала 600 минут и тут же исключила базу SVO. Прежде
    исход решало, какими ЗНАКАМИ набрано слово «Не»: кириллица и
    смешанное написание отказывали, а целиком латинское «He» фразой
    отказа не считалось — 600 садилось в посадку правила-носителя,
    условие авиакомпании молча исчезало из выпускаемой постановки,
    статус «применён», блокировка снята (принципы 3 и 4 ВМП).

    Обратимость: на прежнем коде (688f8a35) третья строка давала
    (600, «600 минут»), первые две — None."""
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None, how
    assert fp.decline_tail(raw) != "" or fp.mixed_script_word(raw) != ""


@pytest.mark.parametrize("raw,phrase", [
    ("He применяется.", "He применяется"),
    ("He применимо.", "He применимо"),
    ("Требования HeT.", "Требования HeT"),
    ("Het такого требования.", "Het такого требования"),
    ("HeT такого ограничения.", "HeT такого ограничения"),
])
def test_k12_a_fully_latin_decline_is_a_decline(raw, phrase):
    """Вторая половина Б1: БЕЗУСЛОВНЫЙ отказ, набранный латиницей,
    теперь узнаётся решением — вопрос закрывается словом авиакомпании,
    а не остаётся «не разобран». Заметка и цитата берут фразу ДОСЛОВНО
    из ответа («He применяется»), а не из свёртки («Не применяется»):
    свёртка УЗНАЁТ фразу, цитирует её ответ.

    Обратимость: на прежнем коде is_declined давал False у всех пяти."""
    assert fp.is_declined(raw)
    parts = fp._decline_parts(raw)
    assert parts is not None and parts[0] == phrase and parts[1] == ""


def test_k12_the_fold_of_the_decline_is_only_toward_refusal():
    """ЦЕНА и СТОРОНА закона Б1, названные вслух: свёртка помогает
    УЗНАТЬ отказ и не читает по свёртке ни значения, ни условия. Отказ
    С УСЛОВИЕМ, набранный латиницей, решением не становится — вопрос
    остаётся открытым, — а слово величины, набранное латиницей, по
    свёртке в ЗНАЧЕНИЕ не превращается: «600 mинут» остаётся
    нечитаемым, а не 600."""
    raw = "He применяется для базы SVO. В остальных базах 600 минут."
    assert fp.parse_value(raw, U, fp.default_domain(U)) is None
    assert not fp.is_declined(raw) and fp.decline_tail(raw) != ""
    assert fp.parse_value("600 mинут", U, fp.default_domain(U)) is None


def test_k12_a_mixed_decline_is_still_not_read_at_all():
    """Б1 НЕ снимает закон находки 3. Слово ДВУМЯ алфавитами не
    читается ВОВСЕ — «ни как единица, ни как обозначение, НИ КАК ФРАЗА
    ОТКАЗА», — поэтому свёртка к нему не применяется: «Hе применяется.»
    (латинская H при кириллической «е») остаётся «не разобран», вопрос
    открыт, носители заблокированы. Без этой оговорки свёртка закрыла
    бы вопрос решением и сняла блокировку — самый дорогой исход."""
    raw = "Hе применяется."
    assert fp.mixed_script_word(raw) == "Hе"
    assert fp._decline_parts(raw) is None and not fp.is_declined(raw)
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert m.is_open_question(q) and QID in rule["blockers"]
    assert "двумя алфавитами" in q["answer"]["note"]


@pytest.mark.parametrize("unit", ["code", "code_list", CNT, CH, HPCH, U, TOD])
def test_k12_an_answer_with_a_decline_phrase_is_not_a_value(unit):
    """Своё следствие Б1, названное законом, а не заплаткой: ответ,
    несущий фразу отказа, ЗНАЧЕНИЯ НЕ ДАЁТ ВОВСЕ — безусловный отказ
    это РЕШЕНИЕ, а не значение. Прежде безусловный отказ доходил до
    поиска значения и ничего там не находил, пока свёртка не открыла в
    самой фразе отказа латинские буквы: «Требования нет. Het такого
    требования.» давало ПЕРЕЧЕНЬ ОБОЗНАЧЕНИЙ «Het» — тип судна из слова
    «нет»."""
    raw = "Требования нет. Het такого требования."
    assert fp.is_declined(raw)
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


def test_k12_the_latin_decline_refuses_through_the_real_application():
    """ВТОРОЙ слой Б1 — настоящий answers.apply_answers: ответ не
    применён, носитель заблокирован, вопрос открыт, а заметка цитирует
    фразу ДОСЛОВНО, как её написала авиакомпания."""
    doc, rule, q = _prepared()
    raw = "600 минут. He применяется для базы SVO."
    summary = answers.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {
        "min_rest_home": {"value": 720, "quote": "ранее 08:00"}}
    assert m.is_open_question(q) and QID in rule["blockers"]
    note = q["answer"]["note"]
    assert "«He применяется»" in note
    assert "отказ и значение в одном ответе" in note
    assert "«Не применяется»" not in note      # цитата — ответ, не свёртка
    assert all("params" in e for e in m.validate_model(doc))


def test_k12_the_latin_decline_never_reaches_the_released_model(tmp_path):
    """ТРЕТИЙ слой Б1 — сборка пакета постановки: значение, названное
    ПРИ отказе, не доезжает до `model.json` ВЫПУСКАЕМОГО пакета.
    Прежде «2 человека. He применяется для базы SVO.» вносилось счётом
    2, гейт выпуска был зелён и пакет выпускался с числом, которое
    авиакомпания сама же и отменила."""
    import json
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_count("2 человека. He применяется для базы SVO.")
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CREW in rule["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc, tmp_path / "bad"))
    doc2, rule2, q2 = _h0_count("2 человека")
    assert rule2["template_fit"]["params"]["crew_size"]["value"] == 2
    files = etp.build_package(_run_dir_of(doc2, tmp_path / "ok"))
    body = json.loads(files["model.json"].decode("utf-8"))
    out = next(r for r in body["rules"] if r["id"] == "VN-081")
    assert out["template_fit"]["params"]["crew_size"]["value"] == 2


def test_k12_a_clean_cyrillic_decline_still_closes_the_question():
    """Положительный контроль Б1: обычный отказ кириллицей закрывает
    вопрос решением, как и прежде, — закон ничего у него не отнял."""
    doc, rule, q = _prepared()
    summary = answers.apply_answers(doc, {QID: "Не применяется."})
    assert summary["applied"] == 1 and summary["unparsed"] == 0
    assert q["answer"]["status"] == "применён"
    assert not m.is_open_question(q) and QID not in rule["blockers"]


# --- СТОРОЖ клаузы свёртки омоглифов ---------------------------------

#: (латинская буква, её кириллический двойник, слово словаря, в котором
#: этот двойник стоит). Слово — настоящее: слово величины либо фраза
#: отказа, то есть ровно то, что свёртка и обязана узнавать.
CONFUSABLE_PROBES = [
    ("a", "а", "часа"), ("A", "А", "ЧАСА"),
    ("c", "с", "суток"), ("C", "С", "СУТОК"),
    ("e", "е", "недель"), ("E", "Е", "НЕДЕЛЬ"),
    ("o", "о", "суток"), ("O", "О", "СУТОК"),
    ("p", "р", "процентов"), ("P", "Р", "ПРОЦЕНТОВ"),
    ("x", "х", "часах"), ("X", "Х", "ЧАСАХ"),
    ("y", "у", "суток"), ("Y", "У", "СУТОК"),
    ("k", "к", "суток"), ("K", "К", "СУТОК"),
    ("m", "м", "минут"), ("M", "М", "МИНУТ"),
    ("t", "т", "минут"), ("T", "Т", "МИНУТ"),
    ("B", "В", "ТРЕБОВАНИЯ НЕТ"), ("H", "Н", "Не применяется"),
]


def test_k12_the_fold_declares_exactly_these_pairs():
    """СТОРОЖ полноты сторожа: у КАЖДОЙ пары свёртки есть проба ниже.
    Добавили пару в `_CONFUSABLE`, не заведя ей пробы, — красное."""
    declared = {lat for lat, _, _ in CONFUSABLE_PROBES}
    assert {chr(k) for k in fp._CONFUSABLE} == declared
    assert all(fp._folded(lat) == cyr for lat, cyr, _ in CONFUSABLE_PROBES)


@pytest.mark.parametrize("lat,cyr,word", CONFUSABLE_PROBES)
def test_k12_every_confusable_pair_is_load_bearing(lat, cyr, word):
    """СТОРОЖ клаузы `_CONFUSABLE` (остаток двенадцатого ревью). Буквы
    «B» и «H» в свёртке были МЁРТВЫМИ: их снятие не красило ни одного
    теста — свёртка до фразы отказа не доходила, а слова величины «В» и
    «Н» не несут. Теперь у каждой объявленной пары есть проба: слово
    словаря, набранное с этой латинской буквой, свёртка обязана вернуть
    к кириллическому виду. Снятие ЛЮБОЙ пары красит эту пробу.

    Проба именно КЛАУЗЫ, а не исхода: у «B» исхода сегодня нет и быть
    не может — ни одно слово словарей величин и фраз отказа не
    набирается латиницей ЦЕЛИКОМ так, чтобы в нём стояло «В» (остаток
    назван вслух). У «H» исход есть — «He применяется»."""
    assert cyr in word
    assert fp._folded(word.replace(cyr, lat)) == word


def test_k12_the_letter_h_of_the_fold_now_changes_an_outcome():
    """Вторая половина сторожа: у буквы «H» проба ИСХОДА, а не только
    клаузы. Снять «H» из `_CONFUSABLE` — и «He применяется» перестанет
    быть отказом, то есть вернётся находка Б1."""
    assert fp.is_declined("He применяется.")
    assert fp.parse_value("600 минут. He применяется для SVO.", U,
                          fp.default_domain(U)) is None


# --- Б2: слово величины через определение ----------------------------

#: Число, определение и слово величины: то, что человек читает как «14
#: дней», «три месяца», «0,1», а код читал как голые 2, 3 и 10.
B2_THROUGH_A_DEFINITION = [
    ("Не менее 2 календарных недель", "недель"),
    ("2 рабочих недели", "недели"),
    ("3 календарных месяца", "месяца"),
    ("2 полных календарных недели", "недели"),
    ("10 условных процентов", "процентов"),
    ("2 полных квартала", "квартала"),
    ("5 полных лет", "лет"),
    ("30 астрономических секунд", "секунд"),
    ("2 полные декады", "декады"),
    ("50 условных промилле", "промилле"),
]


@pytest.mark.parametrize("raw,word", B2_THROUGH_A_DEFINITION)
@pytest.mark.parametrize("unit", [CNT, CH, HPCH, U, TOD])
def test_k12_a_magnitude_through_a_definition_is_not_a_bare_number(
        raw, word, unit):
    """НАХОДКА Б2 (блокирующая). Одиннадцатый круг закрыл слово
    величины ВПЛОТНУЮ к числу, и одно прилагательное снимало весь
    закон: «Не менее 2 календарных недель» у счётного параметра давало
    2 при прочитанных человеком 14, «не более 10 условных процентов» у
    часов на час кредита — 10 вместо 0,1 (ошибка в сто раз), со
    статусом «применён» и снятой блокировкой.

    Приведения не появилось: неделя не стала 14 днями, процент — 0,01.
    Ответ ОТКАЗЫВАЕТ, вопрос остаётся открытым, заметка называет слово
    (fail-closed).

    Обратимость: на прежнем коде parse_value давал 2, 3, 10, 5, 30, 50
    у счёта, часов кредита и часов на час кредита."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.foreign_unit_word(raw, unit) == word


@pytest.mark.parametrize("kind_word,gap", [
    ("недель", " "), ("недель", " календарных "),
    ("месяцев", " "), ("месяцев", " календарных "),
    ("процентов", " "), ("процентов", " условных "),
    ("лет", " "), ("лет", " полных "),
])
def test_k12_both_halves_of_the_magnitude_law_agree(kind_word, gap):
    """Принцип 2 ВМП у находки Б2: ПОЛОВИНЫ закона сошлись. Слово
    величины ВПЛОТНУЮ и то же слово ЧЕРЕЗ ОПРЕДЕЛЕНИЕ дают ОДИН исход —
    отказ, — а не значение в одной половине и отказ в другой."""
    raw = "2" + gap + kind_word
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None
    assert fp.foreign_unit_word(raw, CNT) == kind_word


@pytest.mark.parametrize("raw,want", [
    ("8 выходных дней", 8), ("5 смен", 5), ("2 полных экипажа", 2),
    ("3 рабочих дня", 3), ("2 астрономических часа", 2),
    ("10 календарных дней", 10), ("2 человека", 2),
])
def test_k12_a_counted_noun_through_a_definition_is_still_a_count(raw, want):
    """ЦЕНА закона Б2, названная вслух и измеренная. Окно дано ТОЛЬКО
    родам, которых не читает ни одна единица каталога и которые не
    бывают ПРЕДМЕТОМ счёта. Минуты, часы и сутки окна не получают:
    «дней» и «часа» бывают существительным при числе, и окно съело бы
    законный ответ «8 выходных дней» → 8. Это абсолютный счёт предметов,
    не разрешение отбросить отдельно названный период нормы."""
    got = fp.parse_value(raw, CNT, fp.default_domain(CNT))
    assert got is not None and got[0] == want
    assert fp.foreign_unit_word(raw, CNT) == ""


def test_k12_compound_period_is_not_an_absolute_count():
    # Same historical input, moved out of the healthy noun controls:
    # the period contract, not the local 1–2-word window, now judges it.
    raw = "2 в течение каждого полного месяца"
    assert fp.foreign_unit_word(raw, CNT) == ""
    assert fp.period_qualifier(raw, CNT) == "в течение каждого полного месяца"
    assert fp.parse_value(raw, CNT, fp.default_domain(CNT)) is None
    assert fp.parse_value("2", CNT, fp.default_domain(CNT))[0] == 2


def test_k12_the_window_is_given_only_to_the_declared_kinds():
    """СТОРОЖ клаузы окна: судья с окном (`_GAP_UNIT_RE`) знает РОВНО
    объявленные рода `_GAP_KINDS` и НЕ знает минут, часов и суток.
    Широкий вариант — окно всем родам — измерен и отвергнут: он теряет
    законные ответы вида «8 выходных дней». Снятие любого рода из
    `_GAP_KINDS` либо добавление туда min/hour/day красит эту пробу."""
    have = {name[2:] for name in fp._GAP_UNIT_RE.groupindex
            if name.startswith("k_")}
    assert have == set(fp._GAP_KINDS)
    assert not have & {"min", "hour", "day"}
    read_by_units = set()
    for kinds in fp._UNIT_KINDS.values():
        read_by_units |= set(kinds)
    assert set(fp._GAP_KINDS).isdisjoint(read_by_units)


def test_k12_the_magnitude_note_names_the_word_through_the_application():
    """ВТОРОЙ слой Б2 — настоящий answers.apply_answers: ответ не
    применён, носитель заблокирован, заметка называет ПРОЧИТАННОЕ слово
    и ожидаемую единицу."""
    doc, rule, q = _prepared_count()
    raw = "Не менее 2 календарных недель"
    summary = answers.apply_answers(doc, {QID_CNT: raw})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert "days_off_window_days" not in rule["template_fit"]["params"]
    assert m.is_open_question(q) and QID_CNT in rule["blockers"]
    note = q["answer"]["note"]
    assert "«недель»" in note and "счётного параметра" in note
    assert "укажите число без слова единицы" in note


def test_k12_the_false_count_through_a_definition_never_released(tmp_path):
    """ТРЕТИЙ слой Б2 — сборка пакета постановки: число без его
    величины не доезжает до `model.json` выпускаемого пакета."""
    import json
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_count("Не менее 2 календарных недель")
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_CREW in rule["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc, tmp_path / "bad"))
    doc2, rule2, q2 = _h0_count("2 полных экипажа")
    assert rule2["template_fit"]["params"]["crew_size"]["value"] == 2
    files = etp.build_package(_run_dir_of(doc2, tmp_path / "ok"))
    body = json.loads(files["model.json"].decode("utf-8"))
    out = next(r for r in body["rules"] if r["id"] == "VN-081")
    assert out["template_fit"]["params"]["crew_size"]["value"] == 2


# --- Б3: слово, приклеенное к цифрам через тире ----------------------

#: Тип судна, названный по-русски: слово и цифры через тире. Прежде
#: слово отваливалось, и в посадку уезжало обозначение из тире с
#: цифрами (`was` — прежний исход).
B3_DASHED_TYPES = [
    ("Ту-154", "Ту-154", "-154"),
    ("Ил-96", "Ил-96", "-96"),
    ("Боинг-737", "Боинг-737", "-737"),
    ("МС-21", "МС-21", "-21"),
    ("Як-42", "Як-42", "-42"),
    ("Типы: A320, B738 и Ту-154", "Ту-154", "-154, A320, B738"),
]


@pytest.mark.parametrize("raw,record,was", B3_DASHED_TYPES)
def test_k12_a_word_glued_by_a_dash_is_part_of_the_record(raw, record, was):
    """НАХОДКА Б3 (блокирующая). Закон охвата обозначений не судил
    кириллическое слово, ПРИКЛЕЕННОЕ к цифрам через тире: прогон без
    знака справочника пропускался ДО склейки, «Ту» отваливалось, и в
    посадку правила-носителя уезжало обозначение «-154» — тип судна,
    которого авиакомпания не называла, со статусом «применён» и снятой
    блокировкой.

    Обратимость: на прежнем коде (688f8a35) parse_value давал именно
    `was`, а unread_code_records был пуст."""
    assert fp.unread_code_records(raw, "code_list") == [record]
    assert fp.parse_value(raw, "code_list",
                          fp.default_domain("code_list")) is None
    assert fp.parse_value(raw, "code", fp.default_domain("code")) is None


@pytest.mark.parametrize("dash", DASH_CHARS)
def test_k12_every_dash_glues_the_word_the_same_way(dash):
    """Б3 не зависит от того, КАКИМ знаком набрано тире (принцип 2 ВМП
    и находка 2 одиннадцатого круга — один закон на весь разряд)."""
    raw = "Ту" + dash + "154"
    assert fp.unread_code_records(raw, "code_list") == [raw]
    assert fp.parse_value(raw, "code_list",
                          fp.default_domain("code_list")) is None


def test_k12_both_halves_of_the_designation_law_agree():
    """Принцип 2 ВМП у находки Б3: половины закона сошлись.
    Кириллическая буква ВПЛОТНУЮ к цифрам («А320») и та же буква ЧЕРЕЗ
    ТИРЕ («Ту-154») дают ОДИН исход — отказ, — а не отказ в одной
    половине и выдуманное обозначение в другой."""
    for raw in ("А320", "Ту-154"):
        assert fp.unread_code_records(raw, "code_list") == [raw]
        assert fp.parse_value(raw, "code_list",
                              fp.default_domain("code_list")) is None


@pytest.mark.parametrize("raw,want", [
    ("Типы:A320", "A320"), ("Типы: A320, B738", "A320, B738"),
    ("Типы: A320 и B738", "A320, B738"), ("A320-200", "A320-200"),
    ("B737-800", "B737-800"), ("737-MAX", "737-MAX"),
    ("A320 - B738", "A320, B738"), ("A_320", "A_320"),
    ("737", "737"), ("320, 737", "320, 737"),
    ("Тип судна: A320", "A320"), ("Флот: 320, 737. Конец", "320, 737"),
])
def test_k12_a_clean_designation_answer_reads_as_before(raw, want):
    """ЦЕНА закона Б3, названная вслух и узкая: прогон БЕЗ знака
    справочника склеивается ТОЛЬКО тире, а не всем набором _CODE_GLUE.
    Иначе двоеточие склеило бы вводный оборот с перечнем и «Типы:A320»
    (без пробела) стало бы отказом. Широкий вариант измерен и
    отвергнут: он ломает именно эту строку."""
    got = fp.parse_value(raw, "code_list", fp.default_domain("code_list"))
    assert got is not None and got[0] == want
    assert fp.unread_code_records(raw, "code_list") == []


def test_k12_the_dashed_type_refuses_through_the_real_application():
    """ВТОРОЙ слой Б3 — настоящий answers.apply_answers: ответ не
    применён, носитель заблокирован, заметка называет запись ЦЕЛИКОМ
    («Ту-154»), а не её кусок («-154»)."""
    doc, rule, q = _prepared_fleet()
    summary = answers.apply_answers(doc, {QID_FLEET: "Ту-154"})
    assert summary["applied"] == 0 and summary["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    note = q["answer"]["note"]
    assert "знаки вне прочитанных обозначений" in note
    assert "„Ту-154“" in note and "„-154“" not in note
    assert all("params" in e for e in m.validate_model(doc))


def test_k12_the_dashed_type_never_reaches_the_released_model(tmp_path):
    """ТРЕТИЙ слой Б3 — сборка пакета постановки на НАСТОЯЩЕМ эталоне
    Н0 с семьёй `fleet_exclusions`: обозначение «-154», выдуманное из
    русского имени типа, не доезжает до `model.json` выпускаемого
    пакета, а законный перечень выпускается как прежде."""
    import json
    from cdpm import export_task_package as etp
    doc, rule, q = _h0_fleet("Типы: A320, B738 и Ту-154")
    assert rule["template_fit"]["params"] == {}
    assert m.is_open_question(q) and QID_FLEET in rule["blockers"]
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(_run_dir_of(doc, tmp_path / "bad"))
    doc2, rule2, q2 = _h0_fleet("Типы: A320, B738")
    assert rule2["template_fit"]["params"][
        "fleet_types"]["value"] == "A320, B738"
    files = etp.build_package(_run_dir_of(doc2, tmp_path / "ok"))
    body = json.loads(files["model.json"].decode("utf-8"))
    out = next(r for r in body["rules"] if r["id"] == "VN-070")
    assert out["template_fit"]["params"]["fleet_types"]["value"] == \
        "A320, B738"


# --- Р1: второй рубеж перечня объявлен и имеет свою пробу ------------

def test_k12_the_second_barrier_of_the_list_refuses_on_its_own():
    """ОСТАТОК Р1. Клауза «не всякий токен перечня — обозначение»
    (`_parse_sentence`) сегодня не срабатывает ни на одном входе:
    непомерный токен уже вернул отказ ПЕРВЫМ рубежом — законом охвата,
    — и мутация «снять клаузу» оставляла набор зелёным.

    Клауза оставлена ВТОРЫМ РУБЕЖОМ и объявлена вслух, а проба у неё
    своя: первый рубеж снимается, и отказ обязан дать второй. Без этой
    пробы рубеж был бы мёртвым кодом, а не рубежом."""
    long = "ATR72-500-600-700-800-900-1000"
    assert len(long) > 16 and not fp._is_code(long)
    raw = "A320, " + long
    assert fp.unread_code_records(raw, "code_list") == [long]   # 1-й рубеж
    was = fp._unread_code_records
    try:
        fp._unread_code_records = lambda s: []                  # снят
        assert fp._parse_sentence(raw, "code_list") == ("refused", None)
        assert fp._parse_sentence("A320, B738", "code_list") == (
            "value", "A320, B738")
    finally:
        fp._unread_code_records = was


# ===================================================================
# ТРИНАДЦАТОЕ неавторское ревью K02: окно за собственным словом
# величины (С-1), вводный оборот через тире (С-2), второй рубеж
# отказа (Р-1).
# ===================================================================

# --- С-1: окно не тянется дальше собственного слова величины --------
#
# Посылка закона окна (_GAP_KINDS) объявлена вслух: род окна даётся
# числу, «своей величины лишённому». Судья с окном доставал род ДАЛЬШЕ
# по строке и там, где слово величины стоит при числе ВПЛОТНУЮ, — то
# есть на входах, где посылка НЕ ВЫПОЛНЕНА. «Не более 75 часов в месяц»
# у часов кредита — канонная запись месячного предела авиакомпании —
# молча становилась «не разобран».

K13_OWN_UNIT_READ = [
    ("не более 75 часов в месяц", "credit_hours", 75),
    ("не более 100 часов в квартал", "credit_hours", 100),
    ("2 часа в неделю", "credit_hours", 2),
    ("максимум 85 часов в месяц", "credit_hours", 85),
    ("600 минут в месяц", "duration_minutes", 600),
    ("10 часов в неделю", "duration_minutes", 600),
    ("75 часов в полугодие", "credit_hours", 75),
    ("не более 10 часов в год", "hours_per_credit_hour", 10),
]


@pytest.mark.parametrize("raw,unit,want", K13_OWN_UNIT_READ)
def test_k13_own_unit_does_not_authorize_discarding_a_period(raw, unit, want):
    """CDPM-194: все восемь прежних входов сохранены, но абсолютное
    значение из периодической нормы больше не принимается. Само
    абсолютное число в канонической единице остаётся читаемым."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.period_qualifier(raw, unit)
    absolute = f"{want} минут" if unit == "duration_minutes" else str(want)
    got = fp.parse_value(absolute, unit, fp.default_domain(unit))
    assert got is not None and got[0] == want


@pytest.mark.parametrize("short,long,unit,want", [
    ("максимум 85 часов в месяц", "максимум 90 часов налёта в месяц",
     "credit_hours", None),
    ("10 часов в неделю", "10 часов в сутки", "duration_minutes", 600),
])
def test_k13_halves_of_the_window_law_agree(short, long, unit, want):
    """Прежние пары сохранены. Длина вставки и словоформа периода не
    разрешают терять период. Неделя и сутки — не одна норма: их общий
    отказ означает отсутствие поддержки обоих периодов, не равенство."""
    a = fp.parse_value(short, unit, fp.default_domain(unit))
    b = fp.parse_value(long, unit, fp.default_domain(unit))
    # CDPM-194: одинаковый отказ вместо одинаковой потери периода.
    assert a is None and b is None
    assert fp.period_qualifier(short, unit)
    assert fp.period_qualifier(long, unit)


K13_WINDOW_STILL_REFUSES = [
    # Б2: слова величины при числе НЕТ — посылка окна выполнена
    ("Не менее 2 календарных недель", "count"),
    ("2 рабочих недели", "count"),
    ("3 календарных месяца", "count"),
    ("не более 10 условных процентов", "hours_per_credit_hour"),
    ("2 недель", "count"),
    # предлог окна НЕ рвёт: «в процентах» — не срок, а ФОРМА выражения
    ("10 в процентах", "hours_per_credit_hour"),
    ("50 в процентах", "hours_per_credit_hour"),
    ("2 в неделях", "count"),
    ("10 за процент", "hours_per_credit_hour"),
    # предмет счёта — не слово величины (остаток назван вслух)
    ("не более 2 раз в месяц", "count"),
    ("3 рейса в неделю", "count"),
    # чужое слово ВПЛОТНУЮ отказывает первым судьёй, как и прежде
    ("2 недели в месяц", "count"),
    ("75 часов в месяц", "count"),
    ("75 ч 30 мин", "credit_hours"),
]


@pytest.mark.parametrize("raw,unit", K13_WINDOW_STILL_REFUSES)
def test_k13_window_law_keeps_refusing_where_the_premise_holds(raw, unit):
    """С-1, ЦЕНА закона названа и ограничена. Окно рвёт СВОЁ СЛОВО
    ВЕЛИЧИНЫ, а не предлог: широкий вариант («предлог рвёт окно»)
    измерен и ОТВЕРГНУТ — он открывает ту самую ошибку в сто раз, ради
    которой писался закон Б2 («10 в процентах» → 10 вместо 0,1).
    Приведения не прибавилось: неделя не стала 14 днями."""
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


def test_k13_own_unit_mark_is_taken_from_the_very_same_text():
    """С-1, сам предикат `_own_unit_at`: своё слово при числе, чужого
    рода слово и взгляд назад из середины токена.

    ШВА ЭТА ПРОБА НЕ КАСАЕТСЯ, и прежняя её запись («отметка снимается
    ПО ТОМУ ЖЕ тексту») была не о том: предикат зовётся здесь НАПРЯМУЮ,
    текст ему передаёт сама проба, и мутация вызывающего
    (`_own_unit_at(s, …)` вместо `_own_unit_at(text, …)`) её не красит.
    Шов держит отдельная проба — K02-13-S1 ниже."""
    assert fp._own_unit_at("75 часов в месяц", 0) is True
    assert fp._own_unit_at("75 календарных недель", 0) is False
    # из середины токена совпадение не засчитывается (взгляд назад)
    assert fp._own_unit_at("175 часов", 1) is False


# --- С-2: вводный оборот через тире вплотную ------------------------

K13_DASH_LEAD_READ = [
    ("Тип судна—A320", "A320"),
    ("Типы—A320, B738", "A320, B738"),
    ("Тип—A320", "A320"),
    ("Тип судна–A320", "A320"),
    ("Флот—A320, B738", "A320, B738"),
    ("Тип судна — A320", "A320"),          # форма с пробелами — как была
    ("Типы:A320", "A320"),                 # двоеточие — как было
]


@pytest.mark.parametrize("raw,want", K13_DASH_LEAD_READ)
def test_k13_lead_phrase_is_not_eaten_by_the_dash_glue(raw, want):
    """С-2. У ДВОЕТОЧИЯ граница вводного оборота названа вслух и
    защищена пробой, у ТИРЕ — не была: склейка Б3 съедала оборот, и
    «Тип судна—A320» (было A320) стало отказом.

    Проба обратимости: на b844ecb7 первые пять строк давали None."""
    got = fp.parse_value(raw, "code_list", fp.default_domain("code_list"))
    assert got is not None and got[0] == want
    assert fp.unread_code_records(raw, "code_list") == []


K13_DASH_STILL_REFUSES = [
    "Ту-154", "Ил-96", "Боинг-737", "МС-21", "Як-42",
    "Типы: A320, B738 и Ту-154",
    "Ту—154", "Ту‐154",          # цифровой хвост — склейка держит
    "Ту-A320", "Ту‐A320",        # ДЕФИС соединяет всегда
    "Тип судна-A320",            # он же у оборота: fail-closed
    "Флот—737",                  # чисто цифровое обозначение: остаток
    # Хвост, который ЛАТИНИЦУ НЕСЁТ, но ею НЕ ОТКРЫВАЕТСЯ: закон смотрит
    # ПЕРВЫЙ знак прогона, а не «есть ли где-нибудь латинская буква».
    # Мутация «искать латиницу где угодно» иначе зелена, а она вернула бы
    # «Ту—154MAX» обозначением «154MAX» — дыра Б3 в обход.
    "Ту—154MAX", "Флот—737MAX", "Ил—96A",
]


@pytest.mark.parametrize("raw", K13_DASH_STILL_REFUSES)
def test_k13_dash_glue_keeps_the_b3_hole_shut(raw):
    """С-2, ОБРАТНАЯ половина: дыра Б3 закрыта по-прежнему. «Ту-154» не
    уезжает обозначением «-154», а «Тип судна-A320» — обозначением
    «-A320» (так было ДО 688f8a35, это fail-open, и возвращать его
    нельзя)."""
    assert fp.parse_value(raw, "code_list",
                          fp.default_domain("code_list")) is None
    assert fp.unread_code_records(raw, "code_list") != []


def test_k13_join_dashes_are_taken_from_unicode_not_written_by_hand():
    """С-2, СТОРОЖ. Разбор тире на СОЕДИНЯЮЩИЕ и РАЗДЕЛЯЮЩИЕ снят с
    Юникода по имени знака, а не выписан рукой: ни правка рукой, ни
    новая версия Юникода щели не оставят."""
    import unicodedata
    want = "".join(d for d in fp._DASHES
                   if "HYPHEN" in unicodedata.name(d, ""))
    assert fp._JOIN_DASHES == want
    assert "-" in fp._JOIN_DASHES            # дефис соединяет
    assert "—" not in fp._JOIN_DASHES        # длинное тире разделяет
    assert "–" not in fp._JOIN_DASHES        # короткое тире разделяет
    assert set(fp._JOIN_DASHES) < set(fp._DASHES)


def test_k13_every_dash_the_reader_swallows_is_a_joining_one():
    """С-2, K02-13-D2: ЗАВИСИМОСТЬ между читателем обозначений и
    разбором тире, которую держит проба, а не клауза.

    За тире, которое ГЛОТАЕТ сам _CODE_TOKEN_RE, усечённая часть
    остаётся читаемым обозначением («Ту-154» → «-154»); разорвать на
    нём запись значило бы снова открыть дыру Б3. Сегодня это выходит
    само собой — читатель глотает ровно ASCII-дефис, а он зовётся
    HYPHEN-MINUS, — и вписывать условие в код значило бы оставить
    мёртвую клаузу. Условие стоит ЗДЕСЬ: расширится читатель — набор
    покраснеет, и решать будет автор, а не молчание."""
    swallowed = [d for d in fp._DASHES
                 if fp._CODE_TOKEN_RE.fullmatch("1" + d + "1")]
    assert swallowed == ["-"], "читатель обозначений стал глотать иное тире"
    for d in swallowed:
        assert d in fp._JOIN_DASHES


# --- Р-1: второй рубеж отказа объявлен и имеет свою пробу -----------

def test_k13_r1_decline_barrier_is_a_second_line_with_its_own_probe():
    """ОСТАТОК Р-1. Прежняя запись клаузы «ответ с фразой отказа
    значения не даёт» называла ДЕЙСТВУЮЩИЙ вред, а его на пути
    пользователя НЕТ: `apply_param_answer` разбирает отказ САМ, до
    всякого вызова parse_value. Мутация «снять клаузу» измерена — путь
    применения расходится в 0 парах из 60.

    Клауза оставлена ВТОРЫМ РУБЕЖОМ: parse_value — объявленная точка
    входа грамматики, её зовут и помимо применения ответа. Проба у
    рубежа своя — она зовёт parse_value ПРЯМО (K02-13-R1); без неё
    мутация «снять» набор бы не покраснила."""
    for raw, unit in (("Требования нет. Het такого требования.", "code_list"),
                      ("He применяется.", "code_list"),
                      ("Не применяется для базы SVO.", "code_list"),
                      ("600 минут. He применяется.", "duration_minutes")):
        assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    # ПЕРВЫЙ рубеж — применение ответа: тот же исход и без клаузы
    assert fp._decline_parts("Требования нет. Het такого требования.") \
        is not None
    assert fp.is_declined("Требования нет. Het такого требования.")


# --- С-1, ШОВ свёртки омоглифов объявлен РУБЕЖОМ и имеет пробу -------

def test_k13_s1_own_unit_mark_and_the_judge_read_the_very_same_text():
    """K02-13-S1. ШОВ: отметка «у числа своя величина» снимается по ТОМУ
    ЖЕ тексту, по которому идёт судья с окном, — ответ как есть и
    свёртка омоглифов судятся ЦЕЛИКОМ каждый сам по себе, а отказы
    объединяются.

    Прежняя проба шва не касалась: она звала `_own_unit_at` НАПРЯМУЮ, а
    на базе краснела лишь потому, что функции там ещё не было. Мутация
    «снимать отметку по ответу как есть» (`_own_unit_at(s, …)` вместо
    `_own_unit_at(text, …)`) была на всём наборе ЗЕЛЁНОЙ.

    Здесь шов достижим: в «10 cytok в mec» латинские омоглифы стоят и в
    слове при числе («cytok» → «суток»), и в роде окна («mec» → «мес»).
    По ответу как есть судья с окном не находит НИЧЕГО; по свёртке он
    находит «мес», но там же видно и своё слово числа — посылка окна не
    выполнена, заметки о чужом слове нет. Мутация судит по двум РАЗНЫМ
    текстам сразу и даёт «mec»."""
    assert fp.foreign_unit_word("10 cytok в mec", "duration_minutes") == ""
    assert fp.foreign_unit_word("10 cyt в mec", "duration_minutes") == ""
    # ОБРАТНАЯ половина шва: свёртка отказ по-прежнему ОТКРЫВАЕТ —
    # без неё эти три строки зеленели бы на «свёртку убрать вовсе».
    assert fp.foreign_unit_word("10 штук в mec", "duration_minutes") == "mec"
    assert fp.foreign_unit_word("10 cytok в mec", "count") == "cytok"
    assert fp.foreign_unit_word("2 cутok", "count") == "cутok"
    # и свой текст судья судит целиком: по ответу как есть «месяц»
    # чужое, и отказ по нему остаётся, сколько бы ни дала свёртка
    assert fp.foreign_unit_word("10 cytok в месяц", "duration_minutes") \
        == "месяц"


# --- С-1, ОСТАТОК: срок ответа со сроком параметра не сверяется ------

def test_k13_s1_no_declared_parameter_period_means_no_periodic_value():
    """CDPM-194: пока период не объявлен машинным контрактом, ни
    недельная, ни месячная норма не садится как абсолютная величина.
    Появление поля требует отдельной интеграции, не автоматического
    допуска по имени параметра. Старые входы и проверка метаданных
    остаются, прежнее ложное значение заменено отказом."""
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "catalog", "families.json"),
              encoding="utf-8") as fh:
        cat = json.load(fh)
    spans = {}
    for fam in cat.get("families", []):
        for p in fam.get("params") or []:
            if p.get("name") in ("max_weekly_block", "max_monthly_block"):
                spans[p["name"]] = (p.get("unit"), p.get("period"))
    assert spans == {"max_weekly_block": ("duration_minutes", None),
                     "max_monthly_block": ("duration_minutes", None)}, \
        "у параметра появился объявленный срок — остаток пора закрывать"
    # CDPM-194: отсутствие машинного срока теперь не разрешает его
    # отбросить. Все прежние периодические входы сохранены с отказом.
    for raw in ("10 часов в неделю", "10 часов в месяц",
                "10 часов в квартал", "10 часов в сутки"):
        assert fp.parse_value(raw, "duration_minutes",
                              fp.default_domain("duration_minutes")) is None
        assert fp.period_qualifier(raw, "duration_minutes")
    got = fp.parse_value("10 часов", "duration_minutes",
                         fp.default_domain("duration_minutes"))
    assert got is not None and got[0] == 600


# --- С-2, ОБРАТНЫЙ ОСТАТОК — КЛАСС, а не один вход ------------------

@pytest.mark.parametrize("raw", [
    "Ту—A320", "Кроме—A320", "Не—A320", "Нет—A320", "Исключая—A320",
])
def test_k13_s2_the_reverse_remainder_is_a_class_not_a_single_input(raw):
    """ОСТАТОК назван верно. Запись «обратный остаток ОДИН» называла
    классом один вход и была НЕВЕРНА: разделяющее тире съедает ЛЮБОЙ
    кириллический оборот вплотную, включая ОТРИЦАЮЩИЙ.

    Класс при этом НЕ НОВ — см. пробу ниже: с пробелом и с двоеточием те
    же обороты читались так и до правки."""
    got = fp.parse_value(raw, "code_list", fp.default_domain("code_list"))
    # K02 integral: same historical raw, but fail-open is now corrected.
    assert got is None
    assert fp.code_context_problem(raw, "code_list")


@pytest.mark.parametrize("raw", [
    "Не A320", "Кроме A320", "Нет A320", "Не: A320", "Кроме: A320",
])
def test_k13_s2_the_negating_lead_phrase_class_predates_the_dash(raw):
    """ГРАНИЦА остатка: класс давний. Эти пять входов дают «A320» и на
    b844ecb7 — слово без знака справочника записью не считается, и
    отрицание в нём код не читает ВОВСЕ. С-2 добавила к пробелу и
    двоеточию ТИРЕ, то есть расширила давний класс, а не завела новый.

    Проба обратимости у неё обратная обычной: на базе она ЗЕЛЕНА — тем и
    доказано, что класс предсуществует."""
    got = fp.parse_value(raw, "code_list", fp.default_domain("code_list"))
    # Historical behavior remains documented above, not required as healthy.
    assert got is None
    assert fp.code_context_problem(raw, "code_list")
