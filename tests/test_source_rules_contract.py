# -*- coding: utf-8 -*-
"""Контракт правил источника: загрузка, эстафеты, лимиты перевозок.

Негативные тесты к пакету 2026-08-11. Каждый закрывает дефект, который
уже случился на прогоне заказчика за август, и проверяет
ИМЕННО тот путь, на котором дефект прошёл незамеченным.

Класс дефектов у всех один: правило, которое исполняется и молча не
срабатывает. Полный набор его не ловил, потому что при пустом правиле
код выходит первой строкой, и тело не исполняется ни разу.
"""
from datetime import datetime

import pytest

import _cos_rules as cr
import _pairing_builder as pb


# ---------------------------------------------------------------- загрузка

def test_snapshot_survives_round_trip():
    """`as_dict()` читается обратно без потерь.

    Снимок публикуется в отпечаток каждого каталога, поэтому он же
    оказывается образцом, с которого собирают файл правил. Пара
    «печатаем — читаем» обязана быть замкнутой.
    """
    base = cr.CosRules()
    snap = base.as_dict()
    back = cr.from_dict(snap).as_dict()
    ignore = {"source_keys_read", "source_keys_missing"}
    assert {k: v for k, v in snap.items() if k not in ignore} \
        == {k: v for k, v in back.items() if k not in ignore}


def test_unrecognized_document_is_refused():
    """Документ без единого ключа правил — отказ, а не умолчания.

    Корневой дефект пакета: `from_parameters` не находил в снимке
    `as_dict()` ни одного своего ключа, оставлял умолчания заказчика и объявлял их правилами заказчика. Прогон «с правилами»
    правил не видел.
    """
    for doc in ({"мусор": 1}, {}, []):
        with pytest.raises(cr.SourceRulesError):
            cr.load_document(doc, where="проверка")


def test_typo_in_rule_name_is_refused():
    """Опечатка в имени правила неотличима от его отсутствия."""
    snap = dict(cr.CosRules().as_dict(), min_sit_minutes=40)
    with pytest.raises(cr.SourceRulesError):
        cr.load_document(snap, where="проверка")


def test_both_formats_load():
    """Формат вендора и снимок COSMOS читаются одной точкой входа."""
    vendor = cr.load_document({"parameters": {"maximumPairingDays": 4}})
    assert vendor.max_pairing_days == 4
    flat = cr.load_document({"maximumPairingDays": 7})
    assert flat.max_pairing_days == 7
    snap = cr.load_document(dict(cr.CosRules().as_dict(), min_sit_min=40))
    assert snap.min_sit_min == 40


def _doc(payload, section=cr.MACHINE_SECTION, extra=""):
    """Документ постановки в том же виде, что отдаётся заказчику."""
    return ("# CMD-GPT-FINAL-FULL-XX\n\n## ЧАСТЬ 0\n\n"
            "%s\n\n```json\n%s\n```\n%s\n## ЧАСТЬ 1\n| правило |\n"
            % (section, payload, extra))


def test_statement_document_is_accepted():
    """Расчёт читает переданный документ постановки, а не служебный файл.

    Заказчик возвращает ОДИН документ — единый свод правил. Всякая
    ручная подготовка между ним и расчётом — место, где они
    расходятся.
    """
    snap = dict(cr.CosRules().as_dict(), min_sit_min=40,
                max_dh_per_flight=4)
    import json as _json
    r = cr.load_bytes(_doc(_json.dumps(snap, ensure_ascii=False)).encode(),
                      where="документ")
    assert r.min_sit_min == 40 and r.max_dh_per_flight == 4


def test_statement_document_json_block_is_chosen_by_heading():
    """Блок выбирается по заголовку раздела, а не первым попавшимся.

    В документе больше одного блока JSON: машинные параметры и подпись
    происхождения. Взять «какой-нибудь» — значит посчитать по чужим
    числам.
    """
    import json as _json
    snap = _json.dumps(dict(cr.CosRules().as_dict(), min_sit_min=41),
                       ensure_ascii=False)
    decoy = '```json\n{"min_sit_min": 99}\n```\n'
    text = ("# док\n\n## ЧАСТЬ 0\n\n" + decoy + "\n" + cr.MACHINE_SECTION
            + "\n\n```json\n" + snap + "\n```\n")
    assert cr.load_bytes(text.encode(), where="док").min_sit_min == 41


@pytest.mark.parametrize("text,why", [
    ("# док\n\nбез машинного раздела\n", "раздела нет"),
    ("# док\n" + cr.MACHINE_SECTION + "\nбез блока\n", "блока нет"),
    ("# док\n" + cr.MACHINE_SECTION + "\n```json\n{\n", "блок не закрыт"),
    ("# док\n" + cr.MACHINE_SECTION + "\n```json\n\n```\n", "блок пуст"),
    ("# док\n" + cr.MACHINE_SECTION + "\n```json\n{нет\n```\n", "не JSON"),
    ("# д\n" + cr.MACHINE_SECTION + "\n```json\n{}\n```\n"
     + cr.MACHINE_SECTION + "\n```json\n{}\n```\n", "два раздела"),
])
def test_broken_statement_document_is_refused(text, why):
    """Документ без однозначного машинного блока — отказ, не умолчания."""
    with pytest.raises(cr.SourceRulesError):
        cr.load_bytes(text.encode(), where="док")


def test_load_bytes_dispatches_by_content_not_extension():
    """Вид определяется содержимым: переименование файла при пересылке
    не должно менять поведение расчёта."""
    import json as _json
    snap = _json.dumps(dict(cr.CosRules().as_dict(), min_sit_min=42),
                       ensure_ascii=False)
    assert cr.load_bytes(snap.encode(), where="json").min_sit_min == 42
    assert cr.load_bytes(_doc(snap).encode(), where="md").min_sit_min == 42


def test_overlays_reach_the_rules():
    """Значения снимка доезжают до объекта правил, а не теряются."""
    r = cr.load_document(dict(cr.CosRules().as_dict(), min_sit_min=40,
                              max_pairing_days=5, base_tz_offset_min=300))
    assert (r.min_sit_min, r.max_pairing_days, r.base_tz_offset_min) \
        == (40, 5, 300)
    assert len(r.source_keys_read) > 15


# ------------------------------------------------------- номер рейса

@pytest.mark.parametrize("raw,core", [
    ("ZZ613@1", "613"),               # wire-имя внутри расчёта
    ("ZZ613", "613"),                 # номер как в данных
    ("ZZ613$01.08.2026$1", "613"),    # тождество экземпляра
    ("613", "613"),
    ("ZZ9610@3", "9610"),
])
def test_flight_number_core(raw, core):
    """Ядро номера извлекается из всех встречающихся форм.

    Внутри расчёта участок адресуется wire-именем с порядковым суффиксом
    (номера в расписании не уникальны). Сравнение по окончанию строки
    здесь молча ложно: `ZZ613@1` не кончается на `613`, и запрет
    эстафет не срабатывал ни разу, хотя исполнялся на каждой цепочке.
    """
    assert cr.CosRules.flight_number_core(raw) == core


# ------------------------------------------------------------- эстафеты

def _leg(fn, fleet, arr, day, h0=8, h1=12):
    return {"fn": fn, "fleet": fleet, "dep": "BSE", "arr": arr,
            "t0": datetime(2026, 8, day, h0),
            "t1": datetime(2026, 8, day, h1)}


def _duty(*legs):
    return {"legs": list(legs), "dep": legs[0]["dep"], "arr": legs[-1]["arr"]}


# Синтетическая таблица отдыха фикстуры: умолчаний у класса больше нет,
# порог ночёвки (min_off_base_rest, зовётся из relay_ok) обязан
# приходить из явных данных. Минимум off_base-столбца — 750.
_TIERS = (
    cr.LayoverTier(600, 700, 750, 1400, 750, 1400, 1400),
    cr.LayoverTier(800, 2000, 1450, 2000, 1450, 2800, 1300),
)


def _builder(**fields):
    r = cr.CosRules(layover_tiers=_TIERS)
    for k, v in fields.items():
        setattr(r, k, v)
    b = pb.Builder.__new__(pb.Builder)
    b.rules = r
    b.report = {}
    b.bases = {"BSE"}
    b.legs = {}
    return b


def _relay_rules():
    return ({"flight_numbers": ("613", "614"), "aircraft_types": ("A320",)},)


def test_min_off_base_rest_comes_from_the_table():
    """Порог ночёвки — из таблицы отдыха, а не выбран проверкой.

    Пока расчёт и приёмка выбирали его каждый сам (построитель —
    «между сменами», аудит — 600 минут), они мерили разное и
    расходились на 24 цепочках. Таблица здесь — явная синтетика; в
    off_base-столбце минимум 760, при этом в соседних столбцах есть
    числа меньше — минимум обязан браться ИМЕННО по off_base.
    """
    r = cr.CosRules(layover_tiers=(
        cr.LayoverTier(600, 700, 810, 1400, 900, 1400, 1400),
        cr.LayoverTier(800, 2000, 760, 2000, 1450, 2800, 1300)))
    assert r.min_off_base_rest() == 760
    # голый объект таблицы не несёт: порог — отказ, а не чужое число
    with pytest.raises(cr.SourceRulesError, match="layover_tiers"):
        cr.CosRules().min_off_base_rest()


def test_relay_ban_catches_wire_named_legs():
    b = _builder(no_relay_rules=_relay_rules())
    chain = [_duty(_leg("ZZ613@1", "A320", "IST", 1)),
             _duty(_leg("ZZ614@7", "A320", "BSE", 2))]
    assert b.relay_ok(chain) is False


def test_relay_ban_sees_overnight_at_the_edge_of_the_chain():
    """Ночёвка бывает между перевозкой и сменой, а не только между сменами.

    Проверка «только между сменами» пропускала 5 цепочек из одной смены
    и 19 с ночёвкой у края цепочки.
    """
    b = _builder(no_relay_rules=_relay_rules())
    chain = [_duty(_leg("ZZ613@1", "A320", "VKO", 1))]
    dh = [_leg("ZZ9607@2", "A320", "BSE", 2)]
    assert b.relay_ok(chain, dh) is False


def test_relay_ban_ignores_flights_flown_as_passenger():
    """Правило судит о ВЫПОЛНЕНИИ рейса.

    Экипаж, летящий на рейсе пассажиром, его не выполняет: 28 цепочек
    обвинялись напрасно.
    """
    b = _builder(no_relay_rules=_relay_rules())
    chain = [_duty(_leg("ZZ9608@1", "A320", "VKO", 1))]
    dh = [_leg("ZZ613@1", "A320", "VKO", 1, 2, 6)]
    assert b.relay_ok(chain, dh) is True


def test_relay_ban_is_narrow():
    """Запрет относится к СОЧЕТАНИЮ номера и типа, не к ним порознь."""
    b = _builder(no_relay_rules=_relay_rules())
    other_type = [_duty(_leg("ZZ613@1", "B767", "IST", 1)),
                  _duty(_leg("ZZ614@7", "B767", "BSE", 2))]
    other_num = [_duty(_leg("ZZ777@2", "A320", "IST", 1)),
                 _duty(_leg("ZZ778@3", "A320", "BSE", 2))]
    at_base = [_duty(_leg("ZZ613@1", "A320", "BSE", 1)),
               _duty(_leg("ZZ614@7", "A320", "BSE", 1, 14, 18))]
    assert b.relay_ok(other_type) is True
    assert b.relay_ok(other_num) is True
    assert b.relay_ok(at_base) is True


def test_no_relay_rule_means_no_ban():
    """Без правила построитель не ужесточает задачу сам."""
    chain = [_duty(_leg("ZZ613@1", "A320", "IST", 1)),
             _duty(_leg("ZZ614@7", "A320", "BSE", 2))]
    assert _builder().relay_ok(chain) is True
    bare = pb.Builder.__new__(pb.Builder)
    bare.rules = None
    bare.bases = {"BSE"}
    assert bare.relay_ok(chain) is True


# ------------------------------------------------- лимиты перевозок

def _dh_builder(**fields):
    b = _builder(base_tz_offset_min=300, **fields)
    b.legs = {
        # 31.07 20:00 UTC = 1 августа 01:00 по базе
        "A@1": {"t0": datetime(2026, 7, 31, 20, 0), "arr": "VKO"},
        "B@1": {"t0": datetime(2026, 8, 1, 6, 0), "arr": "VKO"},
        "C@1": {"t0": datetime(2026, 8, 2, 6, 0), "arr": "VKO"},
        "D@1": {"t0": datetime(2026, 8, 1, 7, 0), "arr": "IST"},
        "H@1": {"t0": datetime(2026, 8, 1, 9, 0), "arr": "BSE"},
    }
    return b


def test_dh_limit_group_per_flight():
    b = _dh_builder(max_dh_per_flight=2, dh_limit_counts="all")
    cands = [{"dh": ["A@1"]} for _ in range(5)]
    groups = b.dh_limit_groups(cands, list(range(5)))
    assert len(groups) == 1
    assert len(groups[0][0]) == 5 and groups[0][1] == 2


def test_dh_limit_skips_unreachable_cap():
    """Ограничение на пять цепочек при пределе девять — лишняя строка."""
    b = _dh_builder(max_dh_per_flight=9, dh_limit_counts="all")
    assert b.dh_limit_groups([{"dh": ["A@1"]} for _ in range(5)],
                             list(range(5))) == []


def test_dh_day_is_counted_in_base_timezone():
    """Перевозка 31.07 20:00 UTC — это 1 августа по времени базы."""
    b = _dh_builder(max_dh_per_day=2, dh_limit_counts="all")
    cands = [{"dh": ["A@1"]}, {"dh": ["B@1"]}, {"dh": ["B@1"]},
             {"dh": ["C@1"]}]
    sizes = sorted(len(vs) for vs, _ in b.dh_limit_groups(cands, range(4)))
    assert sizes == [3]


def test_dh_day_scope_destination_separates_directions():
    """Гранула суток задаётся правилами, а не выбирается кодом."""
    cands = [{"dh": ["A@1"]}, {"dh": ["B@1"]}, {"dh": ["B@1"]},
             {"dh": ["D@1"]}]
    net = _dh_builder(max_dh_per_day=3, dh_day_scope="network",
                      dh_limit_counts="all")
    dst = _dh_builder(max_dh_per_day=3, dh_day_scope="destination",
                      dh_limit_counts="all")
    assert len(net.dh_limit_groups(cands, range(4))[0][0]) == 4
    assert dst.dh_limit_groups(cands, range(4)) == []


def test_return_home_is_not_a_placement():
    """Возврат на базу под лимит забросок не попадает.

    Правило первоисточника — «Слишком много ЗАБРАСЫВАЕТСЯ бригад» с
    примером «Москва». Замер решения интегратора: возвраты превышают
    четвёрку в 32 днях из 37, заброски по направлению — лишь в 2
    ячейках из 254. Лимит, применённый к возвратам, запрещал бы
    авиакомпании возвращать собственные экипажи.
    """
    home = [{"dh": ["H@1"]} for _ in range(5)]
    counted = _dh_builder(max_dh_per_flight=2, dh_limit_counts="all")
    placements = _dh_builder(max_dh_per_flight=2,
                             dh_limit_counts="placements")
    assert len(counted.dh_limit_groups(home, range(5))[0][0]) == 5
    assert placements.dh_limit_groups(home, range(5)) == []
    # заброска на внестанцию под лимитом остаётся
    away = [{"dh": ["A@1"]} for _ in range(5)]
    assert len(placements.dh_limit_groups(away, range(5))[0][0]) == 5


def test_no_dh_limits_means_no_groups():
    b = _dh_builder()
    assert b.dh_limit_groups([{"dh": ["A@1"]} for _ in range(5)],
                             range(5)) == []
    bare = pb.Builder.__new__(pb.Builder)
    bare.rules = None
    assert bare.dh_limit_groups([{"dh": ["A@1"]}], [0]) == []


# ------------------------------------------------- матрица предела смены


def test_duty_span_matrix_round_trip():
    """Матрица (ревизия 1.10) переживает пару «печатаем — читаем»."""
    base = cr.CosRules()
    base.duty_span_matrix = ((360, 839, 1, 2, 780), (360, 839, 3, 3, 720))
    back = cr.from_dict(base.as_dict())
    assert back.duty_span_matrix == base.duty_span_matrix
    assert "duty_span_matrix" in back.source_keys_read


def test_pre_110_snapshot_stays_strict_readable():
    """Файл правил, выпущенный ДО ревизии 1.10 (без ключа матрицы),
    обязан читаться строгим режимом без отказа: матрица — чистое
    ужесточение поверх обязательного плоского предела, её отсутствие не
    подставляет умолчаний другой авиакомпании."""
    snap = cr.CosRules().as_dict()
    snap.pop("duty_span_matrix")
    back = cr.from_dict(snap)
    assert "duty_span_matrix" not in back.source_keys_missing


@pytest.mark.parametrize("row, why", [
    ([360, 839, 1, 2], "четыре числа вместо пяти"),
    ([360, 360, 1, 2, 780], "пустое окно неотличимо от суточного"),
    ([1440, 60, 1, 2, 780], "начало окна вне суток (старт 24:00 = 0)"),
    ([360, 1441, 1, 2, 780], "конец окна дальше 24:00"),
    ([360, 839, 2, 1, 780], "границы участков наоборот"),
    ([360, 839, 0, 2, 780], "участки не от единицы"),
    ([360, 839, 1, 2, 0], "предел не положителен"),
])
def test_bad_matrix_rows_are_refused(row, why):
    """Кривая строка матрицы — отказ загрузки, а не тихая правка."""
    snap = cr.CosRules().as_dict()
    snap["duty_span_matrix"] = [row]
    with pytest.raises(cr.SourceRulesError):
        cr.from_dict(snap)


def test_duty_span_limit_semantics():
    """Выбор строки: местное окно старта (пояс базы, полуинтервал,
    переход через полночь) x участки включительно; несколько применимых
    строк — минимум; ни одной — None."""
    r = cr.CosRules()
    r.base_tz_offset_min = 300
    r.duty_span_matrix = (
        (360, 840, 1, 2, 780),     # день, 1-2 участка
        (360, 840, 3, 4, 720),     # день, 3-4 участка
        (1320, 360, 1, 4, 600),    # ночь, через полночь
        (360, 840, 2, 2, 750),     # пересекается с первой: строже
    )
    day = datetime(2026, 8, 1, 1, 30)      # 06:30 местного при поясе +5
    assert r.duty_span_limit(day, 1) == 780
    assert r.duty_span_limit(day, 2) == 750
    assert r.duty_span_limit(day, 3) == 720
    night = datetime(2026, 8, 1, 18, 30)   # 23:30 местного
    assert r.duty_span_limit(night, 2) == 600
    gap = datetime(2026, 8, 1, 10, 0)      # 15:00 местного — дыра окон
    assert r.duty_span_limit(gap, 1) is None
    assert r.duty_span_limit(day, 5) is None

def test_midnight_end_spelled_1440_is_accepted():
    """Конец полуинтервала «24:00» пишется как 1440 и означает полночь:
    экспортёру естественна исключительная граница, приёмник нормализует
    её в каноническую wrap-форму (to=0) без потери смысла."""
    snap = cr.CosRules().as_dict()
    snap["base_tz_offset_min"] = 0
    snap["duty_span_matrix"] = [[1020, 1440, 1, 2, 600]]
    back = cr.from_dict(snap)
    assert back.duty_span_matrix == ((1020, 0, 1, 2, 600),)
    assert back.duty_span_limit(datetime(2026, 8, 1, 23, 30), 1) == 600
    assert back.duty_span_limit(datetime(2026, 8, 1, 0, 30), 1) is None
