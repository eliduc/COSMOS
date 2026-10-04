package org.acme.flighcrewscheduling.domain

import ai.timefold.solver.core.api.domain.common.PlanningId
import com.fasterxml.jackson.annotation.JsonIdentityInfo
import com.fasterxml.jackson.annotation.JsonIgnore
import com.fasterxml.jackson.annotation.ObjectIdGenerators
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth
import java.time.temporal.ChronoUnit

@JsonIdentityInfo(scope = Flight::class, generator = ObjectIdGenerators.PropertyGenerator::class, property = "flightNumber")
class Flight : Comparable<Flight> {

    @PlanningId
    var flightNumber: String = ""

    /**
     * C01/C02 phase 3: first-class operating-leg identity as DATA (falls back
     * to flightNumber when the payload does not materialize it). NOTE the
     * Jackson OBJECT identity deliberately stays on flightNumber: Jackson's
     * PropertyGenerator registers ids only from the JSON token, never through
     * a getter fallback (verified empirically — switching the property to
     * flightLegId broke every legacy payload with UnresolvedForwardReference).
     * The C02 dedup mine is defused by LOUDNESS instead: a duplicate wire
     * flightNumber fails deserialization (see FlightIdentityJacksonTest);
     * multi-date payloads reach the solver with a UNIQUE wire number = the
     * leg id (the established _bts_to_schedule convention).
     */
    var flightLegId: String = ""
        get() = if (field.isNotEmpty()) field else flightNumber
    var departureAirport: Airport? = null
    var departureUTCDateTime: LocalDateTime? = null
    var arrivalAirport: Airport? = null
    var arrivalUTCDateTime: LocalDateTime? = null
    /** Aircraft type code (e.g. "A320", "A350"). Defaults to A320 to match Python. */
    var aircraftType: String = "A320"

    /**
     * Рейс-транспорт (чужой перевозчик либо перелёт, предоставленный как
     * средство доставки): обязательством покрытия не является, экипаж
     * может лететь им пассажиром. Правило свода о транспортных рейсах; до этого поля
     * признак молча игнорировался десериализацией (терпимость Quarkus) —
     * теперь он осознанная часть словаря payload.
     */
    var transportOnly: Boolean = false

    /**
     * Все типы судов, которые несёт этот рейс. У обычного рейса — пусто
     * (действует aircraftType). У СИНТЕТИЧЕСКОГО рейса-пейринга список
     * перечисляет типы ВСЕХ его активных участков: цепочка может
     * пересаживать экипаж с одного типа на другой, и допуск обязан быть
     * у человека на КАЖДЫЙ (дефект найден перепроверкой 2026-07-24:
     * проверялся только тип первого участка, 198 юнитов смешанные).
     */
    var aircraftTypes: MutableList<String> = mutableListOf()

    /** Типы, требующие допуска: список, если задан, иначе один тип. */
    @JsonIgnore
    fun requiredTypeRatings(): List<String> =
        if (aircraftTypes.isNotEmpty()) aircraftTypes
        else if (aircraftType.isNotEmpty()) listOf(aircraftType)
        else emptyList()

    /**
     * Коды станций, которые посещает этот рейс. У обычного рейса — пусто
     * (действуют аэропорты вылета и прилёта). У синтетического
     * рейса-пейринга перечисляет ВСЕ станции его участков: правило
     * «запрещённые станции» обязано видеть всю цепочку, а не её концы.
     */
    var stationCodes: MutableList<String> = mutableListOf()

    /** Станции цепочки: список, если задан, иначе вылет и прилёт. */
    @JsonIgnore
    fun visitedStationCodes(): List<String> {
        if (stationCodes.isNotEmpty()) return stationCodes
        val out = ArrayList<String>(2)
        departureAirport?.code?.let { out.add(it) }
        arrivalAirport?.code?.let { out.add(it) }
        return out
    }

    /** Коды допусков, обязательные КАЖДОМУ члену экипажа этого рейса
     *  (участковые требования источника, 2026-07-25). Пусто = нет. */
    var requiredCodesAll: MutableList<String> = mutableListOf()

    // GERAD phase 2, stage-2 rostering over pairings: when this Flight is a
    // synthetic pairing-flight, the real leg ids it covers and their summed
    // block minutes (the span-based duration would overcount block time).
    var legIds: MutableList<String> = mutableListOf()
    var blockMinutes: Int = 0

    // Паритет правил (2026-07-24): точный помесячный налёт — карта
    // «YYYY-MM -> минуты», заполняется строителем синтетического payload
    // по фактическим датам участков пейринга.
    var blockMinutesByMonth: MutableMap<String, Long> = mutableMapOf()

    // Помесячное рабочее время пейринга (дневные окна «первый вылет —
    // последний прилёт», зеркалит сертификат паритета).
    var workMinutesByMonth: MutableMap<String, Long> = mutableMapOf()

    // Ярус 2в (2026-07-24): исполняемые ресурсные правила как ДАННЫЕ юнита.
    // minRestAfterMin — требуемый отдых ПОСЛЕ этого пейринга перед любым
    // следующим (ступенчатая таблица вендора, вычислена строителем payload
    // по последнему наряду юнита); 0 = только глобальный минимум.
    var minRestAfterMin: Int = 0

    // Окна занятости юнита в минутах эпохи UTC ([start, end] на каждый
    // рабочий наряд) — для еженедельного непрерывного отдыха: промежутки
    // МЕЖДУ окнами внутри пейринга тоже отдых. Пусто = весь интервал
    // пейринга считается занятостью (прежнее поведение).
    var busyWindows: MutableList<MutableList<Long>> = mutableListOf()

    /** Помесячные доли рабочего времени; fallback — длительность рейса
     *  в месяце вылета (для прежних payload'ов без карты). */
    @JsonIgnore
    fun workMonthShares(): Map<String, Long> {
        if (workMinutesByMonth.isNotEmpty()) return workMinutesByMonth
        val dep = departureUTCDateTime ?: return emptyMap()
        return mapOf(YearMonth.from(dep).toString()
                         to getDurationMinutes().toLong())
    }

    /** Помесячные доли налёта. Fallback для payload'ов без карты (все
     *  прежние: числовые наборы GERAD, обычные рейсы) — весь налёт в
     *  месяце вылета: для месячных наборов это байт-идентично прежнему
     *  правилу, для одиночных рейсов — точный месяц участка. */
    @JsonIgnore
    fun monthShares(): Map<String, Long> {
        if (blockMinutesByMonth.isNotEmpty()) return blockMinutesByMonth
        val dep = departureUTCDateTime ?: return emptyMap()
        val block = (if (blockMinutes > 0) blockMinutes
                     else getDurationMinutes()).toLong()
        return mapOf(YearMonth.from(dep).toString() to block)
    }

    constructor()
    constructor(flightNumber: String) { this.flightNumber = flightNumber }

    @JsonIgnore
    fun getDepartureUTCDate(): LocalDate = departureUTCDateTime!!.toLocalDate()

    /** Flight duration in minutes. */
    @JsonIgnore
    fun getDurationMinutes(): Int {
        val dep = departureUTCDateTime ?: return 0
        val arr = arrivalUTCDateTime ?: return 0
        return ChronoUnit.MINUTES.between(dep, arr).toInt()
    }

    /** Long-haul = 8h+ → triggers augmented crew handling in some constraints. */
    @JsonIgnore
    fun isLongHaul(): Boolean = getDurationMinutes() >= 8 * 60

    /** Departures between 00:00 and 05:59 UTC count as red-eye. */
    @JsonIgnore
    fun isRedEye(): Boolean {
        val h = departureUTCDateTime?.hour ?: return false
        return h in 0..5
    }

    /** Saturday/Sunday departure (UTC). */
    @JsonIgnore
    fun isWeekendFlight(): Boolean {
        val d = departureUTCDateTime ?: return false
        return d.dayOfWeek.value >= 6
    }

    /** Year-month bucket for monthly flight-hours rule. */
    @JsonIgnore
    fun getYearMonth(): YearMonth = YearMonth.from(departureUTCDateTime)

    /** Union of languages spoken at departure and arrival airports. */
    @JsonIgnore
    fun getRequiredLanguages(): List<String> {
        val out = mutableListOf<String>()
        departureAirport?.languages?.forEach { if (it !in out) out.add(it) }
        arrivalAirport?.languages?.forEach { if (it !in out) out.add(it) }
        return out
    }

    override fun compareTo(other: Flight): Int =
        compareValuesBy(this, other,
            { it.departureUTCDateTime },
            { it.departureAirport?.code },
            { it.arrivalUTCDateTime },
            { it.arrivalAirport?.code },
            { it.flightLegId })

    override fun toString(): String = "$flightNumber@${departureUTCDateTime?.toLocalDate()}"
    // Identity = the effective leg id (falls back to flightNumber), so two
    // same-numbered legs of different dates with materialized flightLegId are
    // DISTINCT while every legacy payload keeps flightNumber semantics.
    override fun equals(other: Any?): Boolean = other is Flight && other.flightLegId == flightLegId
    override fun hashCode(): Int = flightLegId.hashCode()
}
