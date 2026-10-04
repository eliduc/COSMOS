# -*- coding: utf-8 -*-
"""Набор правил построения цепочек в формулировке системы-источника.

Зачем отдельный модуль. Построитель умеет считать цепочки по СВОИМ
правилам — плоский предел смены, четыре числа отдыха. Этого достаточно,
пока нас сравнивают с самими собой, и совершенно недостаточно, когда
сравнение идёт с чужим оптимизатором: у него правила другой формы —
предел зависит от состава экипажа, отдых берётся из таблицы по трём
признакам, «ночная смена» определена долей рабочего времени в окне по
времени базы. Считать «примерно те же» правила и называть это сравнением
нельзя.

Здесь правила выражены ровно так, как заданы у источника, и ничего
сверх того. Каждое сопровождается тем, чем оно доказано: либо ключом
настроек, прочитанным дословно, либо проверкой на его собственном
результате. Разбор целиком — во внутренней документации проверки.

Модуль намеренно не знает ни о построителе, ни о файлах: на вход —
времена и числа, на выход — числа и признаки. Тогда каждое правило
проверяется поштучно, а не через прогон всей задачи.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Полсуток в минутах — граница, после которой окно считается переходящим
# через полночь (22:00-06:00), а не обычным (07:00-19:00).
DAY = 24 * 60


def _minutes_of_day(t: datetime) -> int:
    return t.hour * 60 + t.minute


def window_overlap(t0: datetime, t1: datetime, start_min: int, end_min: int,
                   tz_offset_min: int = 0) -> float:
    """Минуты пересечения отрезка [t0, t1) с суточным окном.

    Окно задано местным временем (`start_min`, `end_min` от полуночи) и
    может переходить через полночь. `tz_offset_min` — смещение того пояса,
    в котором окно понимается; сами отметки задаются во всемирном времени.

    Пояс здесь не украшение: у окна 22:00-06:00 по времени базы и того же
    окна по местному времени станций расходится классификация 62 смен из
    1970 в наборе заказчика.
    """
    if t1 <= t0:
        return 0.0
    off = timedelta(minutes=tz_offset_min)
    a, b = t0 + off, t1 + off
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    while day <= b:
        if start_min < end_min:
            spans = [(day + timedelta(minutes=start_min),
                      day + timedelta(minutes=end_min))]
        else:
            spans = [(day, day + timedelta(minutes=end_min)),
                     (day + timedelta(minutes=start_min), day + timedelta(days=1))]
        for lo, hi in spans:
            x, y = max(a, lo), min(b, hi)
            if y > x:
                total += (y - x).total_seconds() / 60
        day += timedelta(days=1)
    return total


def in_window(t: datetime, start_min: int, end_min: int,
              tz_offset_min: int = 0) -> bool:
    """Момент попадает в суточное окно местного времени."""
    m = _minutes_of_day(t + timedelta(minutes=tz_offset_min))
    if start_min < end_min:
        return start_min <= m < end_min
    return m >= start_min or m < end_min


@dataclass(frozen=True)
class LayoverTier:
    """Строка таблицы отдыха: предельная длительность смены и требуемый
    после неё отдых по шести случаям."""
    duty_upto_min: int
    on_base_min: int
    off_base_min: int
    on_base_night_min: int
    off_base_night_min: int
    on_base_trans_min: int
    off_base_trans_min: int


@dataclass
class CosRules:
    """Правила построения цепочек, как они заданы у системы-источника.

    Числовых умолчаний заказчика в классе НЕТ: значение каждого правила
    приходит из данных (файл правил вендора, снимок `as_dict()`, пакет
    постановки ВМП), а None означает «правило не задано данными».
    Обращение потребителя к незаполненному полю — отказ словами
    (`_need`), причём именно ПРИ ОБРАЩЕНИИ, а не при загрузке: файл
    правил может законно не нести часть полей, пока расчёт ими не
    пользуется. Подставив другие значения, тем же кодом считается
    другая авиакомпания — правил в коде не зашито.
    """

    # --- рабочее время смены -------------------------------------------
    # Предел рабочего времени по ярусу состава: обычный, усиленный,
    # двойной. Совпадает с порогами таблицы отдыха. Значение приходит
    # из данных (`layoverRules.thresholds` вендора, ключ снимка либо
    # пакет постановки); None = правило не задано данными — обращение
    # к нему есть отказ, а не расчёт по чужим числам.
    work_time_limit_by_tier: tuple | None = None

    # --- матрица предела смены (ревизия 1.10, форма threshold_matrix) ----
    # Предел рабочего времени по ОКНУ МЕСТНОГО ВРЕМЕНИ старта смены и
    # ЧИСЛУ УЧАСТКОВ: строки-пятёрки целых
    # (start_from_min, start_to_min, sectors_from, sectors_to, limit_min).
    # Окно старта — полуинтервал [from, to) в минутах от полуночи в поясе
    # `base_tz_offset_min`, переход через полночь допустим; границы
    # участков включительны. Матрица только УЖЕСТОЧАЕТ: действует минимум
    # из неё и яруса состава, поэтому её отсутствие (пустой кортеж) —
    # «правила нет», а не умолчание другой авиакомпании.
    duty_span_matrix: tuple = ()

    # --- ночная смена (nightStart, nightEnd, nightDutyRules.thresholdRatio)
    night_start_min: int = 22 * 60
    night_end_min: int = 6 * 60
    night_ratio: float = 0.5

    # --- «тёмная» смена (darkDutyRules) ---------------------------------
    dark_start_min: int = 23 * 60
    dark_end_min: int = 7 * 60
    dark_check_window: int = 3
    dark_seq_max: int = 2
    # Определение «тёмной» смены источником НЕ установлено: он не пометил
    # тёмной ни одной смены из 2966, и ни одно из четырёх проверенных
    # определений не даёт ноль. Поэтому определение — параметр, а не
    # догадка: расчёт ведётся при крайних вариантах, и если результат
    # совпал, неопределённость доказуемо не влияет на сравнение.
    #   "strict" — смена целиком внутри окна (самое узкое)
    #   "ratio"  — доля рабочего времени в окне не менее половины
    #   "touch"  — смена задевает окно (самое широкое)
    dark_definition: str = "strict"

    # --- «дальняя» стоянка (столбец Trans таблицы отдыха) -----------------
    # Таблица отдыха у источника имеет тип
    # `durationAndStationAndTranslatitude`: длительность смены, станция и
    # третий признак. Третий — про ГЕОГРАФИЮ, а не про длину рейса:
    # экипаж, прилетевший туда, где его внутренние часы разошлись с
    # местными, отдыхает хуже и нуждается в большем времени. Это то же,
    # что в отрасли зовут акклиматизацией (у источника есть и
    # `acclimatizationPeriod` = 72 ч).
    #
    # Порог установлен ПО ЕГО РЕШЕНИЮ. Среди переходов, где признак
    # поднимает требование, он соблюдает его:
    #     пояс >= 2 ч — 72,9 % (случайная выборка даёт 67,4 %)
    #     пояс >= 3 ч — 89,9 %
    #     пояс >= 4 ч — 89,8 %
    #     пояс >= 5 ч — 100 %
    # Ниже трёх часов правило размывается до случайного уровня.
    #
    # Прежде здесь стояло «рейс с блочным временем свыше 12 часов» —
    # догадка по созвучию ключа `transLegThreshold`. Она опровергнута
    # измерением: такое определение не поднимает требование НИ В ОДНОМ
    # из 5785 переходов его решения, то есть столбец был мёртв.
    # Сам порог в часах — значение заказчика и приходит из данных
    # (снимок, пакет постановки, явный флаг); None = не задан.
    trans_tz_hours: float | None = None
    # Порог «дальнего участка» из настроек: он существует, но относится к
    # составу экипажа длинного рейса, а не к таблице отдыха.
    # Поведенческих потребителей нет — только снимок и печать.
    trans_leg_min: int | None = None

    # --- перегон пассажиром в границах наряда -----------------------------
    # В настройках источника этих границ нет — они откалиброваны по его
    # решению (2026-07-28, 112 нарядов с перегоном в хвосте и 125 с
    # перегоном в голове):
    #   * посадка на перегон после смены — не ранее 20 минут от конца
    #     дебрифинга (его минимум; единичный случай −70 минут — его же
    #     нарушение, как и 29 смен сверх предела);
    #   * перегон в голове предшествует брифингу: прилёт перегона не
    #     позднее начала брифинга (его медиана — ровно ноль).
    # Значение минимального зазора — из данных (снимок либо пакет
    # постановки); None = не задан, откат на своё число запрещён.
    dh_board_after_debrief_min: int | None = None

    # --- таблица отдыха (layoverRules.thresholds) ------------------------
    # Таблица приходит из данных (`layoverRules.thresholds` вендора,
    # ключ снимка, пакет постановки); None = не задана данными.
    layover_tiers: tuple | None = None

    # --- ночная серия (nightDutySequenceRules) ---------------------------
    night_seq_max: int = 1
    night_next_duty_start_min: int = 6 * 60
    night_seq_layover_on_base_min: int = 24 * 60
    night_seq_layover_off_base_min: int = 12 * 60

    # --- строение смены и цепочки ----------------------------------------
    max_landings_per_duty: int = 3       # распределение 0/1/2/3, четырёх нет
    # Минимальная стыковка и предел суток цепочки — из данных (снимок,
    # пакет постановки; у вендорского формата ключа стыковки нет вовсе,
    # предел суток — `maximumPairingDays`). None = не заданы данными.
    min_sit_min: int | None = None
    max_pairing_days: int | None = None

    # --- правила, которых нет в `parameters` вендора -----------------------
    # Четыре правила ниже заданы сводом заказчика (И1/И2), а не разделом
    # настроек: у вендора для них ключей не существует. Пока полей не было,
    # такое правило нельзя было ни передать, ни нарушить машинно — боевой
    # прогон одной из линий дал по ним 157 нарушений, и все они оставались
    # вне контракта, то есть вне ответственности расчёта.
    #
    # Умолчание у всех четырёх — «правила нет» (None/пусто), и это
    # намеренно: они пришли бы из свода, а молчаливое умолчание здесь
    # ужесточало бы чужую задачу правилом, которого заказчик не заявлял.
    # (Та же None-конвенция теперь у всего класса.)

    # Порога стыковки при смене типа воздушного судна здесь НЕТ
    # намеренно: построитель цепочек строже свода — он не допускает
    # смены типа внутри смены вовсе (участки стыкуются только при
    # совпадении `fleet`, а `fleet` и есть точный тип). Поле, которое
    # ничего не меняет, хуже отсутствующего: оно создаёт видимость
    # применяемого правила. Если запрет когда-нибудь ослабят, порог
    # вводится вместе с ослаблением, а не заранее.

    # Перевозка экипажа пассажиром: сколько экипажей допустимо везти на
    # одном рейсе и сколько перевозок допустимо за сутки по времени
    # базы. Первое — про места в салоне, второе — про сменность.
    #
    # Эти два ограничивают не цепочку, а ВЫБОР решения целиком: одна
    # цепочка сама по себе правила не нарушает, нарушает их набор
    # выбранных вместе. Поэтому они выражены ограничениями модели
    # покрытия, а не проверкой при выпуске цепочки.
    #
    # Единица счёта у обоих одна — ПЕРЕВОЗКА, то есть пара «цепочка и
    # рейс, которым её экипаж летит пассажиром». Ею же считает приёмка:
    # расчёт и проверка обязаны мерить одно.
    max_dh_per_flight: int | None = None
    max_dh_per_day: int | None = None
    # Гранула дневного лимита. Источник её однозначно НЕ задаёт: в
    # заголовке записи лимит отнесён к одному рейсу, в теле — к суткам
    # (не более 4-5 бригад в день), а пример даёт одно направление.
    # Кандидатов три, и выбор между ними — решение заказчика, а не
    # умолчание кода:
    #   "network"     — 4 перевозки в сутки по всей сети;
    #   "destination" — 4 в сутки на каждое направление (аэропорт
    #                   назначения перевозки), как в примере свода.
    # Проверено на данных планового месяца: трактовка "network"
    # несовместима с покрытием расписания (требуется 7,5 заброски в
    # сутки в среднем и до 20 в пик, превышение в 27 сутках из 31).
    dh_day_scope: str = "network"
    # Что считается перевозкой под лимитами. Правило первоисточника
    # говорит об избытке ЗАБРАСЫВАЕМЫХ бригад: речь о засылке бригады
    # на внестанцию. Возврат экипажа домой — не заброска: пиковые
    # ячейки месяца — сплошь возвраты на базу (до 23 в последние
    # сутки, край горизонта), и лимит «4 в день», применённый к
    # возвратам, запрещал бы авиакомпании возвращать собственные
    # экипажи — задача доказуемо неразрешима при любом пуле.
    #   "placements" — только заброски (прибытие вне базы);
    #   "all"        — любая перевозка, включая возврат на базу.
    dh_limit_counts: str = "placements"

    # Месячный предел налёта контингента набора, минуты. Свод задаёт
    # норму текстом, причём для разных контингентов экипажа нормы
    # разные; ключ несёт значение ДЛЯ КОНТИНГЕНТА НАБОРА. None =
    # документ значения не заявил, действует ярус 1 расчёта
    # (молчаливого умолчания свода здесь нет — принцип тот же, что у
    # лимитов перевозок выше).
    # Тип счётного окна заблокирован открытым вопросом анкеты: ответ
    # заказчика ссылается на пустой раздел собственного документа;
    # до ответа окно — КАЛЕНДАРНЫЙ месяц, и это допущение названо в
    # самом документе рядом с ключом. Решатель применяет значение через
    # персональные flightTimeLimitMin (документ не перетирает
    # персональный лимит из данных).
    monthly_flight_time_limit_min: int | None = None

    # Рейсы, выполняемые без эстафеты (ночёвки вне базы), — перечнем
    # номеров и типов воздушных судов, к которым запрет относится.
    # Форма: ({"flight_numbers": ("901", "902"),
    #          "aircraft_types": ("A320",)}, ...)
    no_relay_rules: tuple = ()
    split_duty_allowed: bool = False     # allowedSplitDutySequenceLength = 0
    extended_duty_allowed: bool = False  # extendedDutyUpperLimit = 0
    transfer_deadheads: bool = False     # transferDeadheadRules: noTransfers

    # --- равнозначные станции (stationEquivalenceRules) -------------------
    # Пара станций и наименьший разрыв, при котором экипажу засчитывается
    # перемещение между ними без перелёта. Пары хранятся симметрично:
    # в решении источника они использованы в обе стороны. Различие
    # состояний несёт сам тип: None = «не заявлено данными» (механизм
    # спит — станции не равнозначны, отсутствие пар только ужесточает),
    # {} = «заявлено пустым».
    teleportation: dict | None = None

    # --- пояс, в котором понимаются окна местного времени ------------------
    # В настройках источника такого ключа нет вовсе; смещение берётся из
    # ДАННЫХ — часового пояса аэропорта базы (либо ключа снимка, либо
    # явного флага). None = пояс не задан ни данными, ни флагом —
    # обращение к нему есть отказ словами, а не чужое число.
    base_tz_offset_min: int | None = None
    # A9 (внешний аудит 04.09.2026): признак, что base_tz_offset_min —
    # ОБЪЯВЛЕННЫЙ пакетом сдвиг шкалы меток (контракт транслятора
    # vmp/pairing_input), а не число из файла правил или умолчания:
    # объявленный сдвиг побеждает пояс листа «Аэропорты», необъявленный
    # уступает данным, как прежде.
    base_tz_offset_declared: bool = False

    # --- происхождение значений --------------------------------------------
    # Какие ключи настроек действительно прочитаны, а какие остались
    # умолчанием. Без этого разреза умолчание неотличимо от данных
    # заказчика, и снимок правил утверждает больше, чем известно.
    source_keys_read: list = field(default_factory=list)
    source_keys_missing: list = field(default_factory=list)

    # ======================================================================

    def _need(self, name: str):
        """Значение правила либо отказ словами.

        Отказ происходит ПРИ ОБРАЩЕНИИ к незаполненному полю, а не при
        загрузке: боевые файлы правил законно неполны, пока расчёт не
        пользуется отсутствующим правилом. Молчаливое умолчание здесь
        опаснее отказа — расчёт пошёл бы по чужим числам и назвал их
        правилами заказчика.
        """
        value = getattr(self, name)
        if value is None:
            raise SourceRulesError(
                "правило %s не задано данными (файл правил/условий/пакет "
                "постановки); расчёт по умолчанию запрещён" % name)
        return value

    def tier_for_work_time(self, minutes: float) -> int | None:
        """Наименьший ярус состава, которым такая смена допустима.

        Возвращает None, если смена не укладывается и в наибольший ярус:
        такую смену строить нельзя ни при каком составе.
        """
        for i, lim in enumerate(self._need("work_time_limit_by_tier")):
            if minutes <= lim:
                return i
        return None

    def max_work_time(self) -> int:
        return max(self._need("work_time_limit_by_tier"))

    # RULE: 0022 -- предел FDP по окну старта и числу участков (матрица)
    def duty_span_limit(self, work_start: datetime,
                        sectors: int) -> int | None:
        """Предел рабочего времени по матрице для смены, начатой в
        `work_start` (всемирное время) с `sectors` рабочими участками.

        Несколько применимых строк — строже (минимум). Ни одной — None;
        при НЕПУСТОЙ матрице потребитель обязан счесть такую смену
        недопустимой: молчаливый возврат к плоскому пределу превратил бы
        дыру покрытия матрицы в ослабление задачи.
        """
        if not self.duty_span_matrix:
            return None
        tz = self._need("base_tz_offset_min")
        best = None
        for f, t, s0, s1, lim in self.duty_span_matrix:
            if s0 <= sectors <= s1 and in_window(work_start, f, t, tz):
                best = lim if best is None else min(best, lim)
        return best

    def is_night_duty(self, work_start: datetime, work_end: datetime) -> bool:
        """Ночная смена: доля рабочего времени в окне 22:00-06:00 по
        времени базы не менее половины.

        Правило воспроизводит метку источника с НУЛЁМ расхождений на 1970
        сменах (из них 530 ночных). Перебирались 72 варианта: три периода
        смены, четыре знаменателя, три пояса, два числителя.
        """
        span = (work_end - work_start).total_seconds() / 60
        if span <= 0:
            return False
        ov = window_overlap(work_start, work_end, self.night_start_min,
                            self.night_end_min,
                            self._need("base_tz_offset_min"))
        return ov / span >= self.night_ratio

    def is_dark_duty(self, work_start: datetime, work_end: datetime) -> bool:
        span = (work_end - work_start).total_seconds() / 60
        if span <= 0:
            return False
        ov = window_overlap(work_start, work_end, self.dark_start_min,
                            self.dark_end_min,
                            self._need("base_tz_offset_min"))
        if self.dark_definition == "touch":
            return ov > 0
        if self.dark_definition == "ratio":
            return ov / span >= 0.5
        return ov >= span - 1e-6      # strict: целиком внутри окна

    def is_trans_layover(self, station_tz_offset_min: int | None) -> bool:
        """Стоянка «дальняя»: часы станции разошлись с часами базы.

        Признак принадлежит СТОЯНКЕ, а не смене: значение имеет то, где
        экипаж отдыхает, а не сколько он до этого летел. Станция без пояса
        в данных дальней не считается — иначе умолчание ужесточало бы
        правило молча.
        """
        if station_tz_offset_min is None:
            return False
        gap = abs(int(station_tz_offset_min)
                  - int(self._need("base_tz_offset_min")))
        return gap >= self._need("trans_tz_hours") * 60 - 1e-6

    def required_layover(self, prev_work_minutes: float, on_base: bool,
                         night: bool, trans: bool) -> int:
        """Отдых, обязательный после смены.

        Ярус выбирается по рабочему времени ПРЕДЫДУЩЕЙ смены, столбец — по
        станции стоянки и признакам смены. Если смена и ночная, и дальняя,
        берётся наибольшее из требований: послабление здесь было бы
        подменой правила.
        """
        tiers = self._need("layover_tiers")
        tier = tiers[-1]
        for t in tiers:
            if prev_work_minutes <= t.duty_upto_min:
                tier = t
                break
        need = tier.on_base_min if on_base else tier.off_base_min
        if night:
            need = max(need, tier.on_base_night_min if on_base
                       else tier.off_base_night_min)
        if trans:
            need = max(need, tier.on_base_trans_min if on_base
                       else tier.off_base_trans_min)
        return need

    def layover_after_night_sequence(self, on_base: bool) -> int:
        return (self.night_seq_layover_on_base_min if on_base
                else self.night_seq_layover_off_base_min)

    def next_duty_start_allowed(self, prev_night: bool,
                                next_work_start: datetime) -> bool:
        """После ночной смены следующая начинается не ранее 06:00."""
        if not prev_night:
            return True
        m = _minutes_of_day(
            next_work_start
            + timedelta(minutes=self._need("base_tz_offset_min")))
        return m >= self.night_next_duty_start_min

    def night_sequence_ok(self, night_flags) -> bool:
        """Не более `night_seq_max` ночных смен подряд."""
        run = 0
        for f in night_flags:
            run = run + 1 if f else 0
            if run > self.night_seq_max:
                return False
        return True

    def dark_sequence_ok(self, dark_flags) -> bool:
        """Не более `dark_seq_max` «тёмных» смен в окне из
        `dark_check_window` подряд идущих."""
        flags = list(dark_flags)
        w = self.dark_check_window
        for i in range(len(flags)):
            if sum(1 for f in flags[i:i + w] if f) > self.dark_seq_max:
                return False
        return True

    def equivalent_station_gap(self, frm: str, to: str) -> int | None:
        """Наименьший разрыв, при котором экипаж считается перемещённым
        между равнозначными станциями. None — пара не равнозначна.

        Незаявленная таблица (`teleportation is None`) — сон механизма,
        а не отказ: отсутствие пар только УЖЕСТОЧАЕТ задачу (станции не
        равнозначны), молчаливое умолчание тут ничего не ослабляет.
        """
        if frm == to:
            return 0
        return (self.teleportation or {}).get((frm, to))

    def pairing_days(self, start: datetime, end: datetime) -> int:
        """Длительность цепочки в сутках по местному времени базы."""
        off = timedelta(minutes=self._need("base_tz_offset_min"))
        return ((end + off).date() - (start + off).date()).days + 1

    def min_off_base_rest(self) -> int:
        """Наименьший отдых вне базы — порог, с которого разрыв между
        участками считается ночёвкой (эстафетой), а не стыковкой.

        Определение вынесено в контракт намеренно: им пользуются и
        расчёт, и приёмка. Пока каждый выбирал порог сам (построитель —
        «между сменами», аудит — 600 минут), они мерили разное, и
        разошлись на 24 цепочках.
        """
        tiers = self._need("layover_tiers")
        if not tiers:
            return self._need("min_sit_min")
        return min(t.off_base_min for t in tiers)

    @staticmethod
    def flight_number_core(value) -> str:
        """Числовое ядро номера рейса: `ZZ901@1` и `ZZ-901` дают `901`.

        Внутри расчёта участок адресуется wire-именем с порядковым
        суффиксом (`ZZ901@1`), потому что номера в расписании не
        уникальны. Правило заказчика при этом судит по НОМЕРУ. Сравнение
        по окончанию строки здесь молча ложно: `ZZ901@1` не кончается на
        `901`, и запрет не срабатывал ни разу, хотя исполнялся на каждой
        цепочке (боевой прогон одной из линий, 111 нарушений при
        активном правиле).
        """
        import re as _re
        s = str(value).split("@")[0].split("$")[0]
        m = _re.search(r"(\d+)", s)
        return m.group(1).lstrip("0") or "0" if m else s

    def relay_forbidden(self, flight_numbers, aircraft_types) -> bool:
        """Набору рейсов запрещена эстафета (ночёвка вне базы).

        Правило срабатывает, когда в цепочке есть И номер из перечня, И
        тип воздушного судна из перечня: запрет относится к их сочетанию,
        а не к номеру или типу порознь. Пустой перечень типов означает
        «на любом типе».
        """
        nums = {self.flight_number_core(x) for x in flight_numbers}
        types = {str(x or "") for x in aircraft_types}
        for rule in self.no_relay_rules:
            want = {self.flight_number_core(x)
                    for x in (rule.get("flight_numbers") or ())}
            if not want or not (nums & want):
                continue
            ac = {str(x) for x in (rule.get("aircraft_types") or ())}
            if not ac or any(t.startswith(a) for t in types for a in ac):
                return True
        return False

    def as_dict(self) -> dict:
        """Снимок действующих правил для отпечатка прогона и аудита.

        None-толерантен: незаполненное правило печатается как null, а не
        рушит снимок — снимок голого объекта нужен уже на импорте модуля
        (`_SNAPSHOT_KEYS`), и null обязан переживать пару
        «печатаем-читаем» (`from_dict` кладёт такой ключ в
        `source_keys_missing`, различая null и пустой список).
        """
        return {
            "max_dh_per_flight": self.max_dh_per_flight,
            "max_dh_per_day": self.max_dh_per_day,
            "dh_day_scope": self.dh_day_scope,
            "dh_limit_counts": self.dh_limit_counts,
            "monthly_flight_time_limit_min":
                self.monthly_flight_time_limit_min,
            "no_relay_rules": [
                {"flight_numbers": list(r.get("flight_numbers") or ()),
                 "aircraft_types": list(r.get("aircraft_types") or ())}
                for r in self.no_relay_rules],
            "work_time_limit_by_tier": (
                list(self.work_time_limit_by_tier)
                if self.work_time_limit_by_tier is not None else None),
            "duty_span_matrix": [list(r) for r in self.duty_span_matrix],
            "night_start_min": self.night_start_min,
            "night_end_min": self.night_end_min,
            "night_ratio": self.night_ratio,
            "dark_start_min": self.dark_start_min,
            "dark_end_min": self.dark_end_min,
            "dark_check_window": self.dark_check_window,
            "dark_seq_max": self.dark_seq_max,
            "dark_definition": self.dark_definition,
            "trans_leg_min": self.trans_leg_min,
            "trans_tz_hours": self.trans_tz_hours,
            "dh_board_after_debrief_min": self.dh_board_after_debrief_min,
            "layover_tiers": (
                [[t.duty_upto_min, t.on_base_min, t.off_base_min,
                  t.on_base_night_min, t.off_base_night_min,
                  t.on_base_trans_min, t.off_base_trans_min]
                 for t in self.layover_tiers]
                if self.layover_tiers is not None else None),
            "night_seq_max": self.night_seq_max,
            "night_next_duty_start_min": self.night_next_duty_start_min,
            "night_seq_layover_on_base_min": self.night_seq_layover_on_base_min,
            "night_seq_layover_off_base_min": self.night_seq_layover_off_base_min,
            "max_landings_per_duty": self.max_landings_per_duty,
            "min_sit_min": self.min_sit_min,
            "max_pairing_days": self.max_pairing_days,
            "split_duty_allowed": self.split_duty_allowed,
            "extended_duty_allowed": self.extended_duty_allowed,
            "transfer_deadheads": self.transfer_deadheads,
            "teleportation": (
                sorted("%s-%s:%d" % (a, b, v)
                       for (a, b), v in self.teleportation.items())
                if self.teleportation is not None else None),
            "base_tz_offset_min": self.base_tz_offset_min,
            "base_tz_offset_declared": bool(self.base_tz_offset_declared),
            "source_keys_read": list(self.source_keys_read),
            "source_keys_missing": list(self.source_keys_missing),
        }


class SourceRulesError(ValueError):
    """Настройки источника не удалось прочитать без домысла.

    Отдельный вид ошибки, потому что молчаливое умолчание здесь опаснее
    отказа: расчёт пойдёт по чужим числам, назовёт их правилами
    заказчика, и никто не узнает.
    """


# ======================================================================
# Два формата и опознание документа
# ======================================================================
# Правила приходят в двух видах, и оба законны:
#   * `parameters` рабочего файла вендора — то, что присылает
#     авиакомпания (`nightStart`, `layoverRules`, `maximumPairingDays`);
#   * снимок `as_dict()` — то, что COSMOS сам публикует в отпечаток
#     каталога (`night_start_min`, `layover_tiers`, `max_pairing_days`).
#
# Общих ключей у них НЕТ. Пока читался только первый, документ второго
# вида проходил разбор молча: ни один ключ не опознавался, все значения
# оставались умолчаниями заказчика, и расчёт объявлял их
# правилами заказчика. Обнаружено на боевом прогоне одной из линий —
# переданный файл правил не повлиял на расчёт НИ ОДНИМ значением.
#
# Отсюда два требования, и оба обязательны:
#   1. система обязана читать формат, который сама печатает;
#   2. документ, в котором не опознан ни один ключ, — это ОТКАЗ.
#      Файл передан явно, значит намерение применить правила заявлено;
#      ноль прочитанных ключей означает «файл не тот», а не «правил нет».
_VENDOR_KEYS = frozenset((
    "nightStart", "nightEnd", "nightDutyRules", "darkDutyRules",
    "transLegThreshold", "layoverRules", "nightDutySequenceRules",
    "maximumPairingDays", "allowedSplitDutySequenceLength",
    "extendedDutyUpperLimit", "transferDeadheadRules",
    "stationEquivalenceRules",
))

_SNAPSHOT_KEYS = frozenset(CosRules().as_dict())


def from_dict(d: dict, strict: bool = True) -> CosRules:
    """Правила из снимка `as_dict()` — обратная к нему операция.

    Снимок публикуется в отпечаток каждого каталога, поэтому он же
    оказывается образцом, с которого человек собирает файл правил. Пара
    «печатаем — читаем» обязана быть замкнутой: формат, который система
    выдаёт наружу, она обязана принимать обратно без потерь.

    Неизвестный ключ — отказ, а не пропуск: опечатка в имени правила
    иначе неотличима от его отсутствия.
    """
    r = CosRules()
    d = d or {}
    read: list[str] = []
    unknown = sorted(set(d) - _SNAPSHOT_KEYS - {"_provenance"})
    if unknown and strict:
        raise SourceRulesError(
            "снимок правил: неизвестные ключи %s. Опечатка в имени "
            "правила неотличима от его отсутствия, поэтому она отказ, а "
            "не пропуск" % ", ".join(unknown[:5]))

    def take(key, cast=int):
        if key not in d or d[key] is None:
            return None
        read.append(key)
        try:
            return cast(d[key])
        except (TypeError, ValueError) as exc:
            raise SourceRulesError(
                "снимок правил: ключ %s = %r не разобран (%s)"
                % (key, d[key], exc)) from exc

    for key, cast in (
            ("night_start_min", int), ("night_end_min", int),
            ("night_ratio", float), ("dark_start_min", int),
            ("dark_end_min", int), ("dark_check_window", int),
            ("dark_seq_max", int), ("dark_definition", str),
            ("trans_leg_min", int), ("trans_tz_hours", float),
            ("dh_board_after_debrief_min", int), ("night_seq_max", int),
            ("night_next_duty_start_min", int),
            ("night_seq_layover_on_base_min", int),
            ("night_seq_layover_off_base_min", int),
            ("max_landings_per_duty", int), ("min_sit_min", int),
            ("max_dh_per_flight", int), ("max_dh_per_day", int),
            ("dh_day_scope", str), ("dh_limit_counts", str),
            ("monthly_flight_time_limit_min", int),
            ("max_pairing_days", int), ("split_duty_allowed", bool),
            ("extended_duty_allowed", bool), ("transfer_deadheads", bool),
            ("base_tz_offset_min", int),
            ("base_tz_offset_declared", bool)):
        v = take(key, cast)
        if v is not None:
            setattr(r, key, v)

    if d.get("dark_definition") not in (None, "strict", "ratio", "touch") \
            and strict:
        raise SourceRulesError(
            "снимок правил: dark_definition = %r; допустимы strict, ratio, "
            "touch" % d.get("dark_definition"))

    tiers = d.get("layover_tiers")
    if tiers:
        read.append("layover_tiers")
        rows = []
        for n, t in enumerate(tiers):
            if len(t) != 7:
                raise SourceRulesError(
                    "снимок правил: layover_tiers[%d] содержит %d чисел "
                    "вместо семи (порог смены и шесть случаев отдыха)"
                    % (n, len(t)))
            rows.append(LayoverTier(*(int(x) for x in t)))
        rows.sort(key=lambda x: x.duty_upto_min)
        r.layover_tiers = tuple(rows)
        # Пределы рабочего времени — те же пороги (как в from_parameters).
        r.work_time_limit_by_tier = tuple(t.duty_upto_min for t in rows)
    wt = d.get("work_time_limit_by_tier")
    if wt:
        read.append("work_time_limit_by_tier")
        r.work_time_limit_by_tier = tuple(int(x) for x in wt)

    dm = d.get("duty_span_matrix")
    if dm:
        read.append("duty_span_matrix")
        rows = []
        for n, row in enumerate(dm):
            if len(row) != 5:
                raise SourceRulesError(
                    "снимок правил: duty_span_matrix[%d] содержит %d чисел "
                    "вместо пяти (окно старта от/до, участки от/до, предел)"
                    % (n, len(row)))
            f, t, s0, s1, lim = (int(x) for x in row)
            if t == DAY:
                # «24:00» — законное правописание конца суток у конца
                # ПОЛУИНТЕРВАЛА: экспортёру естественно писать 1440, а в
                # полуинтервальной арифметике это та же полночь. Начало
                # окна так писать нельзя — старт в 24:00 и есть 0.
                t = 0
            if not (0 <= f < DAY and 0 <= t < DAY):
                raise SourceRulesError(
                    "снимок правил: duty_span_matrix[%d] — границы окна "
                    "%d..%d вне минут суток (от 0..1439, до 0..1440)"
                    % (n, f, t))
            if f == t:
                raise SourceRulesError(
                    "снимок правил: duty_span_matrix[%d] — окно старта "
                    "пусто (from == to = %d): пустое окно неотличимо от "
                    "суточного" % (n, f))
            if s0 < 1 or s0 > s1:
                raise SourceRulesError(
                    "снимок правил: duty_span_matrix[%d] — границы "
                    "участков %d..%d не образуют диапазона от единицы"
                    % (n, s0, s1))
            if lim <= 0:
                raise SourceRulesError(
                    "снимок правил: duty_span_matrix[%d] — предел %d не "
                    "положителен" % (n, lim))
            rows.append((f, t, s0, s1, lim))
        r.duty_span_matrix = tuple(rows)

    nr = d.get("no_relay_rules")
    if nr:
        read.append("no_relay_rules")
        rules = []
        for n, item in enumerate(nr):
            if not isinstance(item, dict):
                raise SourceRulesError(
                    "снимок правил: no_relay_rules[%d] не объект" % n)
            nums = tuple(str(x) for x in (item.get("flight_numbers") or ()))
            if not nums:
                raise SourceRulesError(
                    "снимок правил: no_relay_rules[%d] без списка номеров "
                    "рейсов — запрет без предмета запретил бы эстафеты "
                    "всему расписанию" % n)
            rules.append({
                "flight_numbers": nums,
                "aircraft_types": tuple(
                    str(x) for x in (item.get("aircraft_types") or ()))})
        r.no_relay_rules = tuple(rules)

    tel = d.get("teleportation")
    if tel is not None:
        read.append("teleportation")
        pairs = {}
        for item in tel:
            # Вид "LHR-LGW:2160" — тот же, что печатает as_dict().
            try:
                route, mins = str(item).rsplit(":", 1)
                a, b = route.split("-")
                pairs[(a, b)] = int(mins)
                pairs[(b, a)] = int(mins)
            except ValueError as exc:
                raise SourceRulesError(
                    "снимок правил: равнозначные станции %r не разобраны; "
                    "ожидается вид КОД-КОД:минуты" % item) from exc
        r.teleportation = pairs

    r.source_keys_read = sorted(set(read))
    # `duty_span_matrix` — чистое УЖЕСТОЧЕНИЕ поверх обязательного
    # плоского предела (действует минимум из двух): его отсутствие не
    # подставляет умолчаний другой авиакомпании, поэтому строгий режим
    # («неполный файл — отказ») о нём не спотыкается. Файлы правил,
    # выпущенные до ревизии 1.10, остаются строго читаемыми.
    r.source_keys_missing = sorted(_SNAPSHOT_KEYS - set(read)
                                   - {"source_keys_read",
                                      "source_keys_missing",
                                      "duty_span_matrix"})
    return r


MACHINE_SECTION = "### Машинная форма (JSON)"


def load_bytes(raw: bytes, where: str = "файл правил",
               strict: bool = True) -> CosRules:
    """Правила из содержимого файла: документ постановки либо JSON.

    Единственная точка приёма для всех потребителей. Вид определяется
    СОДЕРЖИМЫМ, а не расширением: файл, переименованный при пересылке,
    не должен менять поведение расчёта.
    """
    text = raw.decode("utf-8-sig")
    head = text.lstrip()
    if head.startswith("{") or head.startswith("["):
        try:
            return load_document(json.loads(text), where=where,
                                 strict=strict)
        except ValueError as exc:
            if isinstance(exc, SourceRulesError):
                raise
            raise SourceRulesError(
                "%s: содержимое начинается как JSON, но не разобрано: %s"
                % (where, exc)) from exc
    return from_markdown(text, where=where, strict=strict)


def from_markdown(text: str, where: str = "документ правил",
                  strict: bool = True) -> CosRules:
    """Правила из переданного документа постановки (Markdown).

    Заказчик получает и возвращает ОДИН документ — единый свод правил
    вместе с ответами и машинной секцией. Расчёт обязан читать именно
    его: отдельный служебный файл правил пришлось бы готовить руками, а
    всякая ручная подготовка между документом и расчётом — место, где
    они расходятся.

    В документе больше одного блока JSON (машинные параметры и подпись
    происхождения), поэтому блок ищется по ЗАГОЛОВКУ РАЗДЕЛА, а не
    первым попавшимся. Отсутствие раздела, два раздела, пустой или
    неразобранный блок — отказ: молчаливый выбор «какого-нибудь» блока
    здесь означал бы расчёт по чужим числам.
    """
    parts = text.split(MACHINE_SECTION)
    if len(parts) != 2:
        raise SourceRulesError(
            "%s: раздел «%s» %s. Документ постановки обязан содержать "
            "ровно один машинный блок правил."
            % (where, MACHINE_SECTION,
               "не найден" if len(parts) < 2 else
               "встречается %d раза" % (len(parts) - 1)))
    tail = parts[1]
    open_at = tail.find("```json")
    if open_at < 0:
        raise SourceRulesError(
            "%s: под разделом «%s» нет блока ```json"
            % (where, MACHINE_SECTION))
    body = tail[open_at + len("```json"):]
    close_at = body.find("```")
    if close_at < 0:
        raise SourceRulesError(
            "%s: блок ```json под разделом «%s» не закрыт"
            % (where, MACHINE_SECTION))
    raw = body[:close_at].strip()
    if not raw:
        raise SourceRulesError(
            "%s: машинный блок правил пуст" % where)
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise SourceRulesError(
            "%s: машинный блок правил не разобран как JSON: %s"
            % (where, exc)) from exc
    return load_document(doc, where=where, strict=strict)


def load_document(doc, where: str = "файл правил",
                  strict: bool = True) -> CosRules:
    """Правила из документа любого из двух видов, с опознанием формата.

    Единственная точка загрузки: все потребители (построитель цепочек,
    порождение колонок, проверка траекторий) обязаны читать правила
    через неё. Разные точки чтения расходятся, и расхождение проявляется
    молча — расчётом по чужим умолчаниям.
    """
    if not isinstance(doc, dict):
        raise SourceRulesError(
            "%s: ожидался объект с правилами, получено %s"
            % (where, type(doc).__name__))
    params = doc.get("parameters")
    if isinstance(params, dict):
        body, kind = params, "parameters"
    elif set(doc) & _VENDOR_KEYS:
        body, kind = doc, "parameters"
    elif set(doc) & _SNAPSHOT_KEYS:
        return from_dict(doc, strict=strict)
    else:
        # Ни одного опознанного ключа. Молчаливое умолчание здесь и есть
        # дефект: расчёт пошёл бы по правилам другой авиакомпании.
        raise SourceRulesError(
            "%s: формат не опознан — не найдено ни одного ключа правил. "
            "Ожидается либо раздел `parameters` рабочего файла источника "
            "(%s, ...), либо снимок правил COSMOS (%s, ...). Расчёт по "
            "умолчаниям здесь запрещён: он выдал бы правила другой "
            "авиакомпании за правила заказчика."
            % (where, ", ".join(sorted(_VENDOR_KEYS)[:3]),
               ", ".join(sorted(_SNAPSHOT_KEYS)[:3])))
    r = from_parameters(body, strict=strict)
    if not r.source_keys_read:
        raise SourceRulesError(
            "%s: опознан как раздел `parameters`, но не прочитано ни "
            "одного правила. Пустой разбор означает несовпадение формата, "
            "а не отсутствие правил у заказчика." % where)
    return r


def from_parameters(params: dict, strict: bool = True) -> CosRules:
    """Правила из раздела `parameters` рабочего файла источника.

    Ничего не додумывает. Ключ, который есть в настройках, но прочитать
    его не удалось, — это ОТКАЗ, а не повод подставить умолчание: прежде
    длительность вида `P1D` давала ноль, то есть требование «отдых ноль
    минут», и отличить его от настоящего нуля было нельзя.

    Ключ, которого в настройках НЕТ, оставляет поле незаполненным
    (None — «правило не задано данными»), и его имя попадает в
    `missing_keys` — чтобы отсутствие нельзя было прочитать как данные
    заказчика; обращение потребителя к такому полю — отказ словами.
    """
    import re

    r = CosRules()
    p = params or {}
    read: list[str] = []
    missing: list[str] = []

    def iso(v, default=None, where=""):
        if v is None:
            if where:
                missing.append(where)
            return default
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
                         str(v).strip())
        if not m:
            if strict:
                raise SourceRulesError(
                    "%s: длительность %r не разобрана; поддерживается вид "
                    "PT<часы>H<минуты>M<секунды>S. Молчаливая подстановка "
                    "умолчания здесь недопустима — расчёт пошёл бы по "
                    "чужому правилу" % (where or "длительность", v))
            return default
        if where:
            read.append(where)
        h, mi, s = (float(x) if x else 0 for x in m.groups())
        return int(h * 60 + mi + s / 60)

    def hhmm(v, default=None, where=""):
        if v is None:
            if where:
                missing.append(where)
            return default
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(v).strip())
        if not m:
            if strict:
                raise SourceRulesError(
                    "%s: время суток %r не разобрано; поддерживается вид "
                    "ЧЧ:ММ" % (where or "время", v))
            return default
        h, mi = int(m.group(1)), int(m.group(2))
        if m.group(3) is not None and int(m.group(3)) > 59:
            if strict:
                raise SourceRulesError(
                    "%s: секунды %r вне диапазона" % (where or "время", v))
            return default
        # Диапазон, а не только формат (внешний аудит R7, P1-05):
        # «99:99» проходило регулярку и превращалось в 6039 минут —
        # окно ночи молча вырождалось.
        if h > 23 or mi > 59:
            if strict:
                raise SourceRulesError(
                    "%s: время суток %r вне диапазона (часы 0-23, "
                    "минуты 0-59)" % (where or "время", v))
            return default
        if where:
            read.append(where)
        return h * 60 + mi
    r.night_start_min = hhmm(p.get("nightStart"), r.night_start_min,
                             "nightStart")
    r.night_end_min = hhmm(p.get("nightEnd"), r.night_end_min, "nightEnd")
    nd = p.get("nightDutyRules") or {}
    if nd.get("thresholdRatio") is None:
        missing.append("nightDutyRules.thresholdRatio")
    else:
        ratio = float(nd["thresholdRatio"])
        # Доля вне (0, 1] — не доля. Порог, заданный процентами (50),
        # обнулил бы число ночных смен, а отчёт показал бы это свойством
        # данных.
        if not 0 < ratio <= 1 and strict:
            raise SourceRulesError(
                "nightDutyRules.thresholdRatio: доля %r вне (0, 1]; "
                "порог в процентах сделал бы ночных смен ноль" % ratio)
        r.night_ratio = ratio
        read.append("nightDutyRules.thresholdRatio")
    dd = p.get("darkDutyRules") or {}
    r.dark_start_min = hhmm(dd.get("darkStart"), r.dark_start_min,
                            "darkDutyRules.darkStart")
    r.dark_end_min = hhmm(dd.get("darkEnd"), r.dark_end_min,
                          "darkDutyRules.darkEnd")

    def whole(container, key, where):
        if container.get(key) is None:
            missing.append(where)
            return None
        read.append(where)
        return int(container[key])

    v = whole(dd, "darkDutyCheckWindow", "darkDutyRules.darkDutyCheckWindow")
    if v is not None:
        r.dark_check_window = v
    v = whole(dd, "allowedDarkDutySequenceLength",
              "darkDutyRules.allowedDarkDutySequenceLength")
    if v is not None:
        r.dark_seq_max = v
    r.trans_leg_min = iso(p.get("transLegThreshold"), r.trans_leg_min,
                          "transLegThreshold")
    lay = p.get("layoverRules") or {}
    tiers = []
    for n, t in enumerate(lay.get("thresholds") or ()):
        w = "layoverRules.thresholds[%d]" % n
        tiers.append(LayoverTier(
            iso(t.get("thresholdDuration"), 0, w + ".thresholdDuration"),
            iso(t.get("onBaseMinimum"), 0, w + ".onBaseMinimum"),
            iso(t.get("offBaseMinimum"), 0, w + ".offBaseMinimum"),
            iso(t.get("onBaseNightMinimum"), 0, w + ".onBaseNightMinimum"),
            iso(t.get("offBaseNightMinimum"), 0, w + ".offBaseNightMinimum"),
            iso(t.get("onBaseTransMinimum"), 0, w + ".onBaseTransMinimum"),
            iso(t.get("offBaseTransMinimum"), 0, w + ".offBaseTransMinimum")))
    if tiers:
        tiers.sort(key=lambda x: x.duty_upto_min)
        if strict and any(t.duty_upto_min <= 0 for t in tiers):
            raise SourceRulesError(
                "layoverRules.thresholds: нулевой порог яруса сделал бы "
                "предел рабочего времени нулевым")
        # Отрицательный отдых и немонотонные ярусы — не постановка, а
        # опечатка данных (внешний аудит R7, P1-05): молча принятые, они
        # вырождают таблицу отдыха.
        if strict:
            for t in tiers:
                if min(t.on_base_min, t.off_base_min, t.on_base_night_min,
                       t.off_base_night_min, t.on_base_trans_min,
                       t.off_base_trans_min) < 0:
                    raise SourceRulesError(
                        "layoverRules.thresholds: отрицательный минимум "
                        "отдыха (ярус до %d мин)" % t.duty_upto_min)
            uppers = [t.duty_upto_min for t in tiers]
            if len(set(uppers)) != len(uppers):
                raise SourceRulesError(
                    "layoverRules.thresholds: два яруса с одинаковым "
                    "порогом %s — таблица неоднозначна"
                    % [u for u in uppers if uppers.count(u) > 1][0])
        r.layover_tiers = tuple(tiers)
        # Пределы рабочего времени по ярусу состава — это те же пороги.
        r.work_time_limit_by_tier = tuple(t.duty_upto_min for t in tiers)
    else:
        missing.append("layoverRules.thresholds")
    ns = p.get("nightDutySequenceRules") or {}
    v = whole(ns, "allowedNightDutySequenceLength",
              "nightDutySequenceRules.allowedNightDutySequenceLength")
    if v is not None:
        r.night_seq_max = v
    r.night_next_duty_start_min = hhmm(
        ns.get("localTimeNextDutyStart"), r.night_next_duty_start_min,
        "nightDutySequenceRules.localTimeNextDutyStart")
    r.night_seq_layover_on_base_min = iso(
        ns.get("minLayoverAfterNightDutySequenceOnBase"),
        r.night_seq_layover_on_base_min,
        "nightDutySequenceRules.minLayoverAfterNightDutySequenceOnBase")
    r.night_seq_layover_off_base_min = iso(
        ns.get("minLayoverAfterNightDutySequenceOffBase"),
        r.night_seq_layover_off_base_min,
        "nightDutySequenceRules.minLayoverAfterNightDutySequenceOffBase")
    v = whole(p, "maximumPairingDays", "maximumPairingDays")
    if v is not None:
        r.max_pairing_days = v
    v = whole(p, "allowedSplitDutySequenceLength",
              "allowedSplitDutySequenceLength")
    if v is not None:
        r.split_duty_allowed = v > 0
    v = whole(p, "extendedDutyUpperLimit", "extendedDutyUpperLimit")
    if v is not None:
        r.extended_duty_allowed = v > 0
    tdr = (p.get("transferDeadheadRules") or {}).get("@type")
    if tdr:
        r.transfer_deadheads = tdr != "noTransfers"
        read.append("transferDeadheadRules")
    else:
        missing.append("transferDeadheadRules")
    # Равнозначные станции присваиваются ВСЕГДА, в том числе пустым
    # списком: прежде пустой список оставлял зашитые пары, и умолчание
    # выдавало себя за данные заказчика — построитель разрешал экипажу
    # закончить смену в одном аэропорту и начать следующую в другом.
    tel = {}
    se = p.get("stationEquivalenceRules")
    if se is None:
        missing.append("stationEquivalenceRules")
    for n, x in enumerate((se or {}).get("teleportationCodesList") or ()):
        a, b = str(x.get("departureStation")), str(x.get("arrivalStation"))
        m = iso(x.get("minimumDuration"), None,
                "stationEquivalenceRules.teleportationCodesList[%d]"
                ".minimumDuration" % n)
        if m is None:
            continue
        # Пары хранятся в обе стороны: в решении источника они так и
        # использованы, включая помеченные несимметричными.
        tel[(a, b)] = m
        tel[(b, a)] = m
    if se is not None:
        r.teleportation = tel
    r.source_keys_read = sorted(set(read))
    r.source_keys_missing = sorted(set(missing))
    return r
