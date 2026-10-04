package org.acme.flighcrewscheduling.domain

import ai.timefold.solver.core.api.domain.common.PlanningId
import com.fasterxml.jackson.annotation.JsonIdentityInfo
import com.fasterxml.jackson.annotation.JsonIgnore
import com.fasterxml.jackson.annotation.JsonProperty
import com.fasterxml.jackson.annotation.ObjectIdGenerators
import java.time.LocalDate

/** Запись истории сотрудника (ревизия 1.17): имена полей — как в наборе
 *  решателя (webapp/task_package.HISTORY_FIELDS); отсутствующее поле —
 *  null, не ноль. */
class EmployeeHistory {
    var historyMonthBlockMin: Long? = null
    var historyMonthWorkMin: Long? = null
    var historyMonthFlightTimeMin: Long? = null
    var historyMonthDaysWorked: Long? = null
    var historyMonthLandings: Long? = null
    var historyLastDutyEnd: String? = null
    var historyLastDutyStation: String? = null
    var historyLastDutyWorkMin: Long? = null
    var historyLastDutySpanMin: Long? = null
    var historyLastDutyNight: Boolean? = null
    var historyRestBeforeHorizonMin: Long? = null
    var historyConsecutiveDutyDays: Long? = null
    var historyConsecutiveNightDuties: Long? = null
    var historyDaysSinceDayOff: Long? = null
    var historyLocation: String? = null
    /** Скользящие окна раздела rolling (имя окна из параметров правил,
     *  например last_7d_work_min) — переносятся как есть; исполнителей
     *  окон у решателя пока нет (открытый пункт ступени L). */
    var historyRolling: MutableMap<String, Long?> = mutableMapOf()
    /** Посуточный календарь до горизонта (ревизия 1.17.2, проект COSMOS
     *  05.09.2026): последние K суток до среза — вид суток и признак
     *  ночной смены; открывает скользящие окна счётчиков через границу
     *  горизонта. Пусто — календаря нет (окна через границу не судятся). */
    var historyCalendar: MutableList<HistoryDay> = mutableListOf()
    /** Явные непрерывные интервалы присутствия сотрудника на станции
     *  (K-1; канонический список location_intervals[] раздела history
     *  по §3.10.3 плана единой версии: одна станция на интервал, без
     *  перекрытий). Только они — доказательство ночёвок вне базы до
     *  первого назначения / после последнего; снимок historyLocation
     *  доказательством не считается. Пусто — интервалов нет. */
    var historyLocationIntervals: MutableList<HistoryLocationInterval> = mutableListOf()
}

/** Интервал присутствия [start, end) на станции; kind ∈ history_fact |
 *  declared_presence (null — не назван); sources — провенанс записи,
 *  читателями не интерпретируется. */
class HistoryLocationInterval {
    var start: String = ""
    var end: String = ""
    var station: String = ""
    var kind: String? = null
    var sources: MutableList<String> = mutableListOf()
}

/** Сутки календаря до горизонта: kind ∈ work | reserve | day_off | leave |
 *  sick | unknown; night — ночная смена началась в эти сутки (null —
 *  неизвестно). Неизвестное не считается ни отдыхом, ни работой. */
class HistoryDay {
    var date: String = ""
    var kind: String = "unknown"
    var night: Boolean? = null
}

@JsonIdentityInfo(scope = Employee::class, generator = ObjectIdGenerators.PropertyGenerator::class, property = "id")
class Employee {

    @PlanningId
    var id: String = ""
    var name: String = ""
    var homeAirport: Airport? = null
    /** Addressed consent rows from the input; absence grants no consent-dependent extension. */
    @set:com.fasterxml.jackson.annotation.JsonSetter(nulls = com.fasterxml.jackson.annotation.Nulls.FAIL)
    var dutyExtensionConsents: MutableList<DutyExtensionConsent?> = mutableListOf()
    var skills: MutableList<String> = mutableListOf()
    var unavailableDays: MutableList<LocalDate> = mutableListOf()

    // --- realistic extensions (mirror Python domain.py) -------------------
    var languages: MutableList<String> = mutableListOf()
    var typeRatings: MutableList<String> = mutableListOf()

    // Паритет правил вендора (2026-07-24) — персональные операционные
    // ограничения из импортированных данных (0/пусто = правило для
    // сотрудника не задано; прежние payload'ы не меняются):
    /** Табельные номера несовместимых коллег (список односторонний). */
    var doNotFlyWith: MutableList<String> = mutableListOf()
    /** Станции, которые сотруднику запрещено посещать (коды аэропортов;
     *  пусто = запретов нет). Перепроверка 2026-07-24: правило вендора
     *  DO_NOT_VISIT_DENIED_STATIONS ранее не исполнялось решателем. */
    var stationsDenied: MutableList<String> = mutableListOf()
    /** Датированные окна допусков (2026-07-25): код -> список окон
     *  [отМинутыЭпохи, доМинутыЭпохи]. Переносятся только коды, на
     *  которые ссылаются требования участков/мест задачи. */
    var qualificationWindows:
        MutableMap<String, MutableList<MutableList<Long>>> = mutableMapOf()

    /** Допуск с кодом действует на всём интервале [fromMin, toMin]. */
    @JsonIgnore
    fun holdsQualification(code: String, fromMin: Long, toMin: Long):
        Boolean {
        val ws = qualificationWindows[code] ?: return false
        for (w in ws) {
            if (w.size == 2 && w[0] <= fromMin && toMin <= w[1]) return true
        }
        return false
    }
    /** Помесячный лимит рабочего времени, минуты (0 = не задан). */
    var workTimeLimitMin: Int = 0
    /** Помесячный лимит налёта, минуты (0 = глобальный порог). */
    var flightTimeLimitMin: Int = 0
    /** 1 = most senior, 1000 = junior; converts via [seniorityWeight]. */
    var seniority: Int = 500

    /** Латентный баг до 2026-07-19: Jackson-конвенция мапила Kotlin-свойство
     *  isReserve в JSON-имя "reserve", и канонический payload ("isReserve")
     *  НИКОГДА не биндился — резерв был невидим солверу. Явная аннотация
     *  фиксирует контрактное имя в обе стороны. */
    @get:JsonProperty("isReserve")
    @set:JsonProperty("isReserve")
    var isReserve: Boolean = false
    var preferredDaysOff: MutableList<LocalDate> = mutableListOf()
    var undesiredAirports: MutableList<String> = mutableListOf()

    // GERAD phase 2 (personalization): leg ids this crew member prefers to
    // fly (G-2014-22 EmployeeLegPreferences generator, ~10% of base legs).
    var preferredFlights: MutableList<String> = mutableListOf()

    // --- история сотрудника (ревизия 1.17 пакета, ступень L аудита A7) ---
    // Остатки на начало периода: набор несёт их полем history сотрудника
    // (null явно = истории нет; ноль не подставляется — решает
    // производитель признаком assume_zero_when_missing, кнопка COSMOS
    // отказывает словами). Исполнители накопителей прибавляют остатки к
    // счёту горизонта: месячный налёт — к месяцу среза, серия рабочих дней
    // — к серии, начатой в день среза, отдых до первой смены — от конца
    // последней смены до горизонта.
    /** Момент среза истории (ISO, начало горизонта); null = истории нет. */
    var historyAsOf: String? = null
    /** Запись истории 1.17 (плоские поля); null = у сотрудника истории нет. */
    var history: EmployeeHistory? = null
    /** Запись истории поставлена решателем как нулевая по признаку раздела
     *  assume_zero_when_missing (решение пользователя «продолжить с нулевой
     *  историей»), а не передана набором — HistoryAssumption.applyZero. */
    var historyAssumedZero: Boolean = false

    /** Границы учёта ночёвок прогона (K-2): копия объявленного или
     *  выведенного горизонта задачи, ставится RosterLayoverCost.prepare до
     *  создания директора счёта — чтобы измеритель не выводил горизонт из
     *  назначений одного сотрудника. Вне обмена; null — границ нет
     *  (измеритель судит только известные промежутки между назначениями). */
    @get:JsonIgnore
    @set:JsonIgnore
    var layoverAccountingStart: java.time.LocalDateTime? = null
    @get:JsonIgnore
    @set:JsonIgnore
    var layoverAccountingEnd: java.time.LocalDateTime? = null

    /** Налёт (минуты) с начала месяца среза до среза; 0 без истории. */
    @JsonIgnore
    fun carryMonthBlockMin(month: java.time.YearMonth): Long {
        val h = history ?: return 0L
        val asOf = historyAsOfDate() ?: return 0L
        return if (java.time.YearMonth.from(asOf) == month) (h.historyMonthBlockMin ?: 0L) else 0L
    }

    /** Рабочих дней подряд к дню среза (серия продолжается, если день
     *  среза — рабочий); 0 без истории. */
    @JsonIgnore
    fun carryConsecutiveDutyDays(firstDay: LocalDate): Long {
        val h = history ?: return 0L
        val asOf = historyAsOfDate() ?: return 0L
        return if (firstDay == asOf) (h.historyConsecutiveDutyDays ?: 0L) else 0L
    }

    /** Ночных смен подряд к срезу истории (series.consecutive_night_duties):
     *  серия продолжается первой сменой горизонта, если та ночная; 0 без
     *  истории. Решение владельца 05.09.2026 «нужно учитывать». */
    @JsonIgnore
    fun carryConsecutiveNightDuties(): Long {
        val h = history ?: return 0L
        if (historyAsOfDate() == null) return 0L
        return h.historyConsecutiveNightDuties ?: 0L
    }

    /** Отработанных суток месяца среза до среза (month_to_date.days_worked);
     *  0 для других месяцев и без истории. Контракт: срез истории не позже
     *  начала горизонта — иначе сутки считались бы дважды. */
    @JsonIgnore
    fun carryMonthDaysWorked(month: java.time.YearMonth): Long {
        val h = history ?: return 0L
        val asOf = historyAsOfDate() ?: return 0L
        return if (java.time.YearMonth.from(asOf) == month) (h.historyMonthDaysWorked ?: 0L) else 0L
    }

    /** Календарь до горизонта по датам (ревизия 1.17.2); пусто без истории
     *  или без календаря. Нечитаемая дата — запись пропускается. */
    @JsonIgnore
    fun historyCalendarMap(): Map<LocalDate, HistoryDay> {
        val h = history ?: return emptyMap()
        if (h.historyCalendar.isEmpty()) return emptyMap()
        val out = HashMap<LocalDate, HistoryDay>()
        for (d in h.historyCalendar) {
            val date = try { LocalDate.parse(d.date.take(10)) } catch (_: Exception) { null } ?: continue
            out[date] = d
        }
        return out
    }

    /** Месяц среза истории у сотрудника с записью истории; null иначе. */
    @JsonIgnore
    fun historyMonth(): java.time.YearMonth? {
        if (history == null) return null
        return historyAsOfDate()?.let { java.time.YearMonth.from(it) }
    }

    @JsonIgnore
    fun historyAsOfDate(): LocalDate? = try {
        historyAsOf?.let { parseAccountingMoment(it).toLocalDate() }
    } catch (_: Exception) {
        try { historyAsOf?.take(10)?.let { LocalDate.parse(it) } } catch (_: Exception) { null }
    }

    /** Момент среза истории (K-3: доли секунды и смещение сохраняются,
     *  смещение переводится в UTC); null — нет либо не читается. */
    @JsonIgnore
    fun historyAsOfAt(): java.time.LocalDateTime? = try {
        historyAsOf?.let { parseAccountingMoment(it) }
    } catch (_: Exception) { null }

    /** Конец последней смены до горизонта как момент; null — нет. */
    @JsonIgnore
    fun historyLastDutyEndAt(): java.time.LocalDateTime? = try {
        history?.historyLastDutyEnd?.let { parseAccountingMoment(it) }
    } catch (_: Exception) { null }

    /** Дата, с которой сотрудник ЗАВЕДОМО свободен по допущению нулевой
     *  истории (HistoryAssumption: срез раздела); null — допущения нет.
     *  Окна в скользящих сутках, начатые до первого рейса горизонта, судятся
     *  с этой даты — как у сертификатора (vmp/run/logic_checks._rolling_rest,
     *  continue_zero: lo = первый вылет − (окно − 1)). */
    @JsonIgnore
    fun assumedFreeFrom(): LocalDate? =
        if (historyAssumedZero && history?.historyLastDutyEnd == null) historyAsOfDate() else null

    constructor()

    constructor(id: String) { this.id = id }

    @JsonIgnore
    fun hasSkill(skill: String): Boolean = skills.contains(skill)

    @JsonIgnore
    fun isAvailable(fromDateInclusive: LocalDate, toDateInclusive: LocalDate): Boolean {
        return fromDateInclusive
            .datesUntil(toDateInclusive.plusDays(1))
            .noneMatch { unavailableDays.contains(it) }
    }

    /** True when the employee carries the given aircraft type rating. */
    @JsonIgnore
    fun isQualifiedFor(aircraftType: String?): Boolean {
        if (aircraftType.isNullOrEmpty()) return true
        return typeRatings.contains(aircraftType)
    }

    /** Convert raw seniority (1..1000, lower=senior) into a weight 2..11
     *  (raw=1 -> 11, raw=1000 -> 2); the "1..10" wording of the original
     *  KDoc was wrong — the formula is the contract (review 2026-07-19). */
    @JsonIgnore
    fun seniorityWeight(): Int {
        val raw = seniority.coerceIn(1, 1000)
        return maxOf(1, 11 - (raw - 1) / 100)
    }

    /** True if the employee speaks at least one of the given languages.
     *  Empty/null requirement counts as "satisfied". */
    @JsonIgnore
    fun speaksAny(required: List<String>?): Boolean {
        if (required.isNullOrEmpty()) return true
        return required.any { it in languages }
    }

    override fun toString(): String = name
    override fun equals(other: Any?): Boolean = other is Employee && other.id == id
    override fun hashCode(): Int = id.hashCode()
}
