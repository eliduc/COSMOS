"""Условия расчёта из машиночитаемой спецификации заказчика.

Спецификация (`usloviya-rascheta.json`) делит правила на две группы:

  * **A** — заданные источниками однозначно;
  * **B** — противоречивые, неоднозначные или неопределённые, принятые в
    наихудшем для исполнителя значении.

Модуль превращает спецификацию в объект, которым построитель пользуется
вместо зашитых величин. Разделение групп сохраняется: каждое значение
помнит, из какой группы пришло, и это попадает в отчёт расчёта — чтобы
из результата было видно, где требование заказчика, а где наша трактовка.

Модуль СОЗНАТЕЛЬНО не содержит ни одного числа: всё приходит из файла.
Зашитое здесь умолчание означало бы, что расчёт может пойти по величине,
которой нет в условиях, и никто этого не заметит.
"""
from __future__ import annotations

import json
from pathlib import Path


class ConditionsError(ValueError):
    """Спецификация условий неполна или противоречит себе."""


class Conditions:
    """Условия расчёта пейрингов кабинного экипажа."""

    def __init__(self, spec: dict):
        self.spec = spec
        A, B = spec.get("A") or {}, spec.get("B") or {}
        self.A, self.B = A, B
        req = ("crew_min_by_fleet", "briefing_base_min", "briefing_offbase_min",
               "debrief_base_min", "debrief_offbase_min", "sit_offbase_min",
               "rest_offbase_by_work_h", "rest_base_by_work_h",
               "chain_max_days")
        missing = [k for k in req if k not in A]
        if missing:
            raise ConditionsError(
                "в условиях нет обязательных правил группы A: "
                + ", ".join(missing))
        # base — строка (однобазовый мир, легаси) либо список
        # (многобазовый, 2026-08-30); поле трассировочное, суды по
        # базам ведёт построитель своим множеством из данных.
        _b = spec.get("base")
        self.base = _b
        self.bases = (tuple(_b) if isinstance(_b, list)
                      else ((_b,) if _b else ()))
        # --- состав экипажа -------------------------------------------
        self._crew_min = dict(A["crew_min_by_fleet"]["values"])
        # Стандартный состав — группа A с аудита №2 (колонка РПП,
        # подтверждена отчётом заказчика); старое место в группе B
        # осталось запасным для прежних файлов условий.
        # Стандартный состав живёт в группе A с аудита №2 (колонка РПП);
        # старое место в группе B оставлено запасным для прежних файлов.
        # ОТСУТСТВИЕ ОБОИХ — отказ, а не пустая таблица: пустая таблица
        # заставляет crew_std() отдавать МИНИМАЛЬНЫЙ состав вместо
        # стандартного, то есть недокомплектовать план молча и
        # правдоподобно. Прежний код индексировал группу B напрямую и
        # падал громко; запасное чтение сделало отказ тихим, и это надо
        # было вернуть.
        std_src = A.get("crew_std_by_fleet") or B.get("b1_crew_std_by_fleet")
        if not std_src or not (std_src.get("values") or {}):
            raise KeyError(
                "в условиях расчёта нет стандартного состава экипажа "
                "(crew_std_by_fleet в группе A либо b1_crew_std_by_fleet "
                "в группе B): без него планирование молча пошло бы по "
                "минимальному составу")
        self._crew_std = dict(std_src["values"])
        # Особая форма «нормы по типу ВС» снята (пакет «умолчания-в-
        # данные»): нормы по типу живут в общих таблицах
        # crew_min_by_fleet / crew_std_by_fleet, где имя типа — ключ
        # таблицы В ДАННЫХ, а не литерал в коде. Страж fail-closed:
        # архивный файл, всё ещё несущий значения особой формой,
        # потерял бы минимум состава правдоподобно и тихо — отказ
        # называет перенос. Снятая форма (withdrawn без values)
        # проходит молча — живые файлы условий не задеты.
        _b2 = B.get("b2_b787_9_norms") or {}
        if _b2.get("values"):
            raise ConditionsError(
                "b2_b787_9_norms: особая форма норм по типу ВС снята — "
                "значения переносятся в таблицы crew_min_by_fleet/"
                "crew_std_by_fleet (имя типа — ключ таблицы в данных); "
                "молча игнорировать values нельзя: минимум состава "
                "потерялся бы тихо")
        # --- рабочее время --------------------------------------------
        wt = B["b3_worktime_table"]
        self._wt_day = dict(wt["day_by_landings_h"])
        self._wt_night = dict(wt["night_by_landings_h"])
        # --- ночь ------------------------------------------------------
        # Определение найдено в самом документе свода — группа A;
        # чтение из группы B осталось запасным для прежних файлов условий.
        n = (A.get("standard_night") or B["b4_standard_night"])["values"]
        self.night_start_h = int(str(n["start"]).split(":")[0])
        self.night_end_h = int(str(n["end"]).split(":")[0])
        self.night_share = float(n["share"])
        # --- стыковка --------------------------------------------------
        self.sit_offbase = int(A["sit_offbase_min"]["value"])
        self.sit_base = int(B["b6_sit_base_min"]["value"])
        # --- подготовка и разбор ---------------------------------------
        self.brief_base = int(A["briefing_base_min"]["value"])
        self.brief_offbase = int(A["briefing_offbase_min"]["value"])
        self.debrief_base = int(A["debrief_base_min"]["value"])
        self.debrief_offbase = int(A["debrief_offbase_min"]["value"])
        # --- отдых ------------------------------------------------------
        self._rest_off = A["rest_offbase_by_work_h"]["values"]
        self._rest_off_trans = A["rest_offbase_by_work_h"]["transmeridian"]
        self._rest_base = A["rest_base_by_work_h"]["values"]
        self._rest_base_trans = A["rest_base_by_work_h"]["transmeridian"]
        self._rest_base_upto12 = B["b12_rest_base_upto_12h"]["values"]
        # Порог «смены до N часов» ветви b12 — ИЗ ДАННЫХ условий
        # (values.work_upto_h), не из кода: прежде зашитые 12 часов
        # судили ветвь у всех заказчиков одинаково. Отказ — на
        # загрузке, не в судействе: файл без порога молча ослабил бы
        # или ужесточил лестницу отдыха.
        _thr = self._rest_base_upto12.get("work_upto_h")
        if isinstance(_thr, bool) or not isinstance(_thr, (int, float)):
            raise ConditionsError(
                "b12_rest_base_upto_12h.values: нет числового порога "
                "work_upto_h — порог смены больше не зашит в коде; "
                "закрытие — переиздать файл условий/экспортёр")
        self._rest_base_upto12_thr = float(_thr) * 60.0
        # Эстафета и её надбавка определены сводом и примечанием (группа A)
        self.rest_bonus_offbase = int(
            (A.get("estafeta_rest_bonus_min")
             or B["b15_estafeta_rest_bonus_min"])["value"])
        self.trans_when_unknown = bool(B["b13_transmeridian_unknown"]["value"])
        # --- прочее ------------------------------------------------------
        self.chain_max_days = int(A["chain_max_days"]["value"])
        # Порог I–II групп — группа A с аудита (агентский раунд 3):
        # примечание и раздел свода + согласованный список аэропортов.
        _cc = A.get("c_class_attendant_required") or {}
        self.c_class_from_h = float(
            _cc.get("c_class_required_from_h")
            or B["b8_airport_group"]["values"]["c_class_required_from_h"])
        # Имя роли «бортпроводник с допуском в бизнес-класс» (П.1.9) —
        # написание конкретной а/к живёт в её файле условий (ключ role
        # того же узла), не в коде. Контракт с построителем:
        # getattr(cond, "c_class_role", None); ключа нет — None
        # (построитель берёт роль из спроса набора по суффиксу «-cl»,
        # а без обоих носителей отказывает громко).
        _role = _cc.get("role")
        if _role is not None and (
                not isinstance(_role, str) or not _role.strip()):
            raise ConditionsError(
                "c_class_attendant_required.role: имя роли обязано "
                "быть непустой строкой, получено %r" % (_role,))
        self.c_class_role = _role
        self.lowcost_prefix = str(
            B["b7_lowcost_vs_charter"]["values"]["lowcost_prefix"])
        self.charter_digits = int(
            B["b7_lowcost_vs_charter"]["values"]["charter_digits"])
        self.deadhead_to_work_pct = int(
            A["deadhead_credit_to_work_pct"]["values"]["without_rest_12h_before"])
        # Минимум между соседними перелётами пассажиром одной смены.
        # Ключа нет — правило не применяется (у прежних наборов условий
        # его не было, и вводить его от себя нельзя).
        _adj = A.get("adjacent_deadhead_min")
        self.adjacent_dh_min = int(_adj["value"]) if _adj else None
        # --- точная ярусная форма предела смены (необязательная) --------
        # Форма свода: предел FDP по периоду местного времени старта в
        # акклиматизированном поясе (прил. 3), отдельная таблица при
        # неопределённой акклиматизации (прил. 4), продлённый предел с
        # отдыхом в полёте по классу места отдыха типа ВС (прил. 6).
        # Без формы всё поведение прежнее (двухстрочная таблица b3).
        we = (A.get("worktime_exact") or {}).get("values")
        self._exact = None
        if we:
            periods = []
            for p in we["acclimatized_by_period"]:
                f_h, f_m = str(p["from"]).split(":")
                t_h, t_m = str(p["to"]).split(":")
                periods.append((int(f_h) * 60 + int(f_m),
                                int(t_h) * 60 + int(t_m),
                                dict(p["by_landings"])))
            # Семантика предела: к чему применяется таблица.
            #   "duty_period" — к ПЕРИОДУ обязанностей (смена вместе с
            #       перегонами и стоянками, не отделёнными нормативным
            #       отдыхом) — строгая трактовка;
            #   "per_duty"    — к каждой рабочей смене в отдельности,
            #       перегоны и стоянки период не склеивают — трактовка,
            #       которой фактически следует план заказчика (входной аудит
            #       2026-08-25: 13,3% её периодов не проходят строгую).
            # Выбор — предмет вопроса интегратору; до ответа расчёты
            # ведутся в обеих трактовках параллельно.
            sem = str(we.get("period_semantics", "duty_period"))
            if sem not in ("duty_period", "per_duty"):
                raise ConditionsError(
                    "worktime_exact.period_semantics: неизвестное значение "
                    "%r (ожидается duty_period либо per_duty)" % sem)
            self._exact = {
                "period_semantics": sem,
                "periods": periods,
                "unknown": dict(we["unknown_by_landings"]),
                "acclim_elapsed_b_h": float(we["acclimatization"]["elapsed_b_h"]),
                "acclim_tz_from_h": float(we["acclimatization"]["tz_from_h"]),
                "ext_by_class": {str(k): float(v) * 60 for k, v in
                                 we["extended_by_class_h"].items()},
                "class_by_fleet": {str(k): str(v) for k, v in
                                   we["rest_class_by_fleet"].items()},
            }
            # Лестница продления по ЧИСЛУ дополнительных членов экипажа
            # (реестр соответствия 2026-08-28, группа Е: одна плоская
            # ступень схлопывала измерение состава — смена под предел
            # одного дополнительного члена оплачивалась как двумя).
            # Ступени по возрастанию состава; выбор наименьшей
            # достаточной — у судьи построителя.
            lad = we.get("extended_ladder") or {}
            steps = []
            prev_crew = 0
            for s in lad.get("steps") or []:
                crew = int(s["extra_crew"])
                if crew <= prev_crew:
                    raise ConditionsError(
                        "extended_ladder: ступени не строго возрастают "
                        "по числу дополнительных членов (%d после %d)"
                        % (crew, prev_crew))
                steps.append((crew, {str(k): float(v) for k, v in
                                     (s.get("by_class_min") or {}).items()}))
                prev_crew = crew
            if not steps:
                # Запасной путь прежних файлов условий: единственный
                # плоский продлённый предел — это ВЕРХНЯЯ ступень
                # лестницы ценой ДВУХ дополнительных членов (fail-closed:
                # прежняя цена одного члена при пределе удвоенного
                # состава недокомплектовывала экипаж).
                steps = [(2, dict(self._exact["ext_by_class"]))]
            self._exact["ext_ladder"] = steps
            b = lad.get("long_sector_bonus")
            if b:
                cond = b.get("condition") or {}
                if cond.get("kind") != "one_sector_over_h":
                    raise ConditionsError(
                        "extended_ladder.long_sector_bonus: неизвестный "
                        "вид условия %r" % cond.get("kind"))
                self._exact["ls_bonus"] = {
                    "add_min": float(b["add_min"]),
                    "over_min": float(cond["value"]) * 60,
                    "max_sectors": int(cond["max_sectors"])}
            else:
                self._exact["ls_bonus"] = None

    # ------------------------------------------------------- состав
    def crew_min(self, fleet: str) -> int:
        return int(self._crew_min.get(_base_fleet(fleet), 0))

    def crew_std(self, fleet: str) -> int:
        """Стандартный состав — база планирования (группа B: колонка «по
        РПП», выбранная как наихудшая из двух противоречащих)."""
        f = _base_fleet(fleet)
        if f in self._crew_std:
            return int(self._crew_std[f])
        return int(self._crew_min.get(f, 1))

    # -------------------------------------------- рабочее время смены
    @staticmethod
    def _landings_key(landings: int) -> str:
        if landings <= 2:
            return "1-2"
        if landings == 3:
            return "3"
        if landings == 4:
            return "4"
        return "5+"

    def work_limit(self, landings: int, night: bool) -> float | None:
        """Предел рабочего времени смены, минут; None — смена запрещена.

        Зависит от числа посадок (перегоны в них не входят) и от того,
        приходится ли на стандартную ночь половина рабочего времени и
        более. Прочерк источника в ночной строке для пяти и более посадок
        принят как запрет (группа B).
        """
        key = self._landings_key(max(1, int(landings)))
        h = (self._wt_night if night else self._wt_day).get(key)
        return None if h is None else float(h) * 60.0

    def max_work_limit(self) -> float:
        vals = [v for v in list(self._wt_day.values())
                + list(self._wt_night.values()) if v is not None]
        return float(max(vals)) * 60.0

    # ------------------------------------------------------------ отдых
    @staticmethod
    def _pick(rows, work_min: float, key: str) -> float:
        best = None
        for r in rows:
            lim = r.get(key)
            if lim is None:
                continue
            if work_min <= float(lim) * 60.0:
                best = r
                break
        if best is None:
            best = rows[-1]
        return float(best["rest_h"]) * 60.0

    def required_layover(self, prev_work_minutes: float, on_base: bool,
                         night: bool, trans: bool) -> float:
        """Обязательный отдых после смены, минут.

        Трансмеридианность при неизвестных данных считается выполненной
        (группа B), поэтому берётся наибольшее из обычного и дальнего
        требований. Вне базы добавляется дорога до места отдыха.
        """
        if on_base:
            if prev_work_minutes <= self._rest_base_upto12_thr:
                need = float(self._rest_base_upto12["rest_h_if_ambiguous"]) * 60
            else:
                need = self._pick(self._rest_base, prev_work_minutes,
                                  "work_upto_h")
            if trans:
                need = max(need, self._pick(self._rest_base_trans,
                                            prev_work_minutes, "work_upto_h"))
            return need
        need = self._pick(self._rest_off, prev_work_minutes, "work_upto_h")
        if trans:
            need = max(need, self._pick(self._rest_off_trans,
                                        prev_work_minutes, "work_upto_h"))
        return need + self.rest_bonus_offbase

    # RULE: 0043 -- разница часовых поясов со станцией базы
    def is_trans(self, station_tz_offset_min, base_tz_offset_min=None) -> bool:
        """Признак дальнего перелёта.

        Документ заказчика определяет его через пересечение порогового
        числа часовых поясов беспосадочным полётом; поясов станций в том
        наборе нет,
        поэтому принята наихудшая трактовка: признак считается
        выполненным (группа B). Когда же спецификация задаёт порог
        `trans_tz_from_h` (группа A) И пояса обеих сторон известны,
        признак считается честно — по фактическому расхождению поясов
        станции стоянки и базы. Прежнее поведение остаётся запасным для
        прежних файлов условий и для станций без пояса: наихудшая
        трактовка вместо молчаливого ослабления.
        """
        thr = (self.A.get("trans_tz_from_h") or {}).get("value")
        if (thr is not None and station_tz_offset_min is not None
                and base_tz_offset_min is not None):
            gap = abs(int(station_tz_offset_min) - int(base_tz_offset_min))
            return gap >= float(thr) * 60 - 1e-6
        return True if self.trans_when_unknown else False

    # ------------------------------------------------------------ прочее
    def sit_min(self, on_base: bool) -> int:
        return self.sit_base if on_base else self.sit_offbase

    def is_night_duty(self, work_start, work_end,
                      base_tz_offset_min: int = 0) -> bool:
        """Ночная смена по ОПРЕДЕЛЕНИЮ УСЛОВИЙ (правила
        night_duty_window, группа A): доля рабочего времени в ночном
        окне не менее night_share. Реестр соответствия 2026-08-28
        (группа В): определение разбиралось, но не потреблялось —
        метку ночи ставил только контракт, гашеный нейтрализатором."""
        from _cos_rules import window_overlap
        span = (work_end - work_start).total_seconds() / 60
        if span <= 0:
            return False
        ov = window_overlap(work_start, work_end,
                            self.night_start_h * 60,
                            self.night_end_h * 60, base_tz_offset_min)
        return ov / span >= self.night_share

    def briefing(self, on_base: bool) -> int:
        return self.brief_base if on_base else self.brief_offbase

    def debriefing(self, on_base: bool) -> int:
        return self.debrief_base if on_base else self.debrief_offbase

    def briefing_floor(self, value, on_base: bool) -> int:
        """Пол документа поверх данных участка.

        Данные участков и документ противоречат друг другу (у части
        станций подготовка 45 минут при норме 60 вне базы). Наихудшая
        трактовка — большее из двух: документ задаёт нижнюю границу,
        данные могут её только поднять. Правило живёт ЗДЕСЬ в одном
        экземпляре: построитель и выгрузка обязаны звать этот метод, а
        не повторять max() каждый по-своему — двойная реализация уже
        дала выгрузку, не совпавшую с расчётом.
        """
        floor = self.briefing(on_base)
        return floor if value is None else max(int(value), floor)

    def debriefing_floor(self, value, on_base: bool) -> int:
        floor = self.debriefing(on_base)
        return floor if value is None else max(int(value), floor)

    # RULE: none -- норма кабинного экипажа (бизнес-класс); контингент расчёта — ЧЛЭ, признак выключен недостижимым значением 999 ч
    def c_class_required(self, flight_minutes: float) -> bool:
        return float(flight_minutes) >= self.c_class_from_h * 60.0

    # RULE: R-S9-табл-1-строка-19-кол-4 -- стыковка в базе перед вылетом ранее порога UTC не планируется
    @property
    def base_sit_dep_utc_before_min(self):
        """Порог (минуты UTC от полуночи): участок с вылетом из базы
        раньше него не достигается стыковкой в базе. None — правила в
        условиях нет (прежние файлы условий)."""
        v = self.A.get("base_sit_dep_utc_before_min")
        return int(v["value"]) if v else None

    # RULE: R-S6-табл-1-строка-17-кол-3 -- ранний пейринг из базы завершается первым возвращением (без разворота в базе)
    @property
    def base_turn_dep_utc_before_min(self):
        v = self.A.get("base_turn_dep_utc_before_min")
        return int(v["value"]) if v else None

    # RULE: R-S6-табл-4-строка-18-кол-3 R-S8-абз-6 -- типы ВС, исключённые из автоматического планирования: их леги — только перегоны
    def auto_plan_excluded_fleets(self) -> frozenset:
        """Типы судов, чьи леги маркируются перегонами и в
        автоматическое планирование не входят (ручное планирование).
        Ключа нет — правило не применяется (прежние файлы условий)."""
        v = self.A.get("fleet_auto_plan_excluded") or {}
        return frozenset(_base_fleet(t) for t in v.get("types") or []
                         if _base_fleet(t))

    # RULE: none -- признак чартера; правил разделения чартера и регулярного расписания в своде нет, признак выключен
    def is_charter(self, flight_number) -> bool:
        digits = "".join(ch for ch in str(flight_number) if ch.isdigit())
        return (len(digits) == self.charter_digits
                and not digits.startswith(self.lowcost_prefix))

    # ---------------------------------------- точная ярусная форма
    @property
    # RULE: none -- признак наличия точных таблиц пределов в условиях расчёта; сами пределы применяет exact_limits по правилам 0022/0023/0024/0030
    def has_exact_worktime(self) -> bool:
        return self._exact is not None

    @property
    def limit_per_duty(self) -> bool:
        """Истина, когда предел судится по каждой смене в отдельности
        (трактовка «per_duty»), а не по периоду обязанностей."""
        return bool(self._exact
                    and self._exact["period_semantics"] == "per_duty")

    # RULE: 0024 -- определение состояния акклиматизации
    def is_acclimatized(self, station_tz_offset_min, base_tz_offset_min,
                        elapsed_min: float) -> bool:
        """Состояние «Б» матрицы акклиматизации (прил. 2 свода).

        Акклиматизирован: разница поясов места начала смены и базы меньше
        порога таблицы (в таблицу такие строки не входят) ЛИБО от начала
        обязанностей ротации прошло меньше границы первой колонки. Всё
        прочее — «В»/«Н», и предел берётся из таблицы неопределённого
        состояния. Неизвестный пояс — не акклиматизирован (наихудшее).
        """
        e = self._exact
        if station_tz_offset_min is None or base_tz_offset_min is None:
            return False
        gap_h = abs(int(station_tz_offset_min) - int(base_tz_offset_min)) / 60
        if gap_h < e["acclim_tz_from_h"]:
            return True
        return elapsed_min < e["acclim_elapsed_b_h"] * 60

    # RULE: 0022 0023 0024 0030 -- таблицы пределов из условий расчёта
    def exact_limits(self, base_local_start_min: int, landings: int,
                     acclimatized: bool, fleet) -> tuple:
        """(базовый предел, ступени продления), минуты.

        Базовый: строка прил. 3 по периоду времени старта в
        акклиматизированном (базовом) поясе либо таблица прил. 4 при
        неопределённом состоянии. Ступени продления — лестница по числу
        дополнительных членов экипажа: список пар (дополнительных
        членов, предел) по возрастанию состава; наименьшую достаточную
        ступень выбирает судья построителя. У типа без класса места
        отдыха продления нет; ступень не мягче базового предела и не
        мягче предыдущей ступени, иначе отбрасывается (fail-closed —
        обобщение прежнего «продлённый не бывает мягче базового»).
        """
        e = self._exact
        key = self._landings_key(max(1, int(landings)))
        if acclimatized:
            mod = base_local_start_min % (24 * 60)
            base = None
            for lo, hi, table in e["periods"]:
                if lo <= mod <= hi:
                    base = table.get(key)
                    break
        else:
            base = e["unknown"].get(key)
        base_min = None if base is None else float(base) * 60
        cls = e["class_by_fleet"].get(_base_fleet(fleet))
        steps: list = []
        if cls:
            prev = None
            for crew, table in e["ext_ladder"]:
                lim = table.get(cls)
                if lim is None:
                    continue
                lim = float(lim)
                if base_min is not None and lim < base_min:
                    continue
                if prev is not None and lim < prev:
                    continue
                steps.append((crew, lim))
                prev = lim
        return base_min, steps

    @property
    def long_sector_bonus(self):
        """Надбавка к ступеням продления при длинном секторе (правило
        R-S3-стр-7-абз-9): словарь {add_min, over_min, max_sectors}
        либо None. Условие по секторам проверяет построитель — здесь
        секторов нет."""
        return self._exact.get("ls_bonus") if self._exact else None

    # ------------------------------------------------------------ отчёт
    def provenance(self) -> dict:
        """Откуда пришло каждое применённое значение — A или B."""
        return {
            "group_A": sorted(k for k in self.A if k != "_"),
            "group_B_worst_case": sorted(k for k in self.B if k != "_"),
        }


def _base_fleet(v) -> str:
    return str(v or "").replace("(*)", "").strip()


def load(path) -> Conditions:
    p = Path(path)
    if not p.exists():
        raise ConditionsError(f"файл условий не найден: {p}")
    return Conditions(json.loads(p.read_text(encoding="utf-8")))
