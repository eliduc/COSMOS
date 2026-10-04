package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.acme.flighcrewscheduling.domain.Flight
import java.time.DayOfWeek
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.util.logging.Logger

/**
 * Ярус 2в (2026-07-24): ресурсные правила как ДАННЫЕ прогона.
 *
 * Env CREW_RESOURCE_RULES — необязательный JSON (числа — условный пример):
 *   {"weeklyRest": {"requiredMin": 3000, "periodMin": 600,
 *                   "tzOffsetMin": 0,
 *                   "horizonFromEpochMin": ..., "horizonToEpochMin": ...}}
 *
 * Отсутствие переменной или блока -> правило выключено, поведение прежних
 * прогонов байт-идентично. Отдельная переменная (не CREW_RULES_CONFIG),
 * чтобы не трогать замороженный девятиключевой контракт конфига правил.
 */
data class WeeklyRestParams(
    val requiredMin: Long,
    val periodMin: Long,
    val tzOffsetMin: Long,
    val horizonFromEpochMin: Long,
    val horizonToEpochMin: Long,
)

object ResourceRules {
    private val log: Logger = Logger.getLogger(ResourceRules::class.java.name)

    val weeklyRest: WeeklyRestParams? = parseWeeklyRest()

    /** Свидетельство потребителя (пятый аудит, пакет B): сколько раз
     *  правило РЕАЛЬНО считалось при оценке решения. Ноль при заданных
     *  параметрах означает, что правило разобрано, но ни разу не
     *  применено, — и это должно быть видно снаружи, а не угадываться. */
    private val weeklyRestEvaluations = java.util.concurrent.atomic
        .AtomicLong()

    internal fun noteWeeklyRestEvaluation() {
        weeklyRestEvaluations.incrementAndGet()
    }

    fun weeklyRestEvaluations(): Long = weeklyRestEvaluations.get()

    fun resetObservations() { weeklyRestEvaluations.set(0) }

    private fun parseWeeklyRest(): WeeklyRestParams? {
        val raw = System.getenv("CREW_RESOURCE_RULES") ?: return null
        return try {
            val root = ObjectMapper().readTree(raw)
            val wr = root.get("weeklyRest") ?: return null
            val p = WeeklyRestParams(
                requiredMin = wr.get("requiredMin").asLong(),
                periodMin = wr.get("periodMin").asLong(),
                tzOffsetMin = wr.get("tzOffsetMin")?.asLong() ?: 0L,
                horizonFromEpochMin = wr.get("horizonFromEpochMin").asLong(),
                horizonToEpochMin = wr.get("horizonToEpochMin").asLong(),
            )
            log.info("ResourceRules: weeklyRest enabled $p")
            p
        } catch (e: Exception) {
            // громкий отказ вместо тихо выключенного правила
            throw IllegalArgumentException(
                "CREW_RESOURCE_RULES is set but unparseable: ${e.message}", e)
        }
    }
}

/** Минуты эпохи UTC для LocalDateTime, который в домене хранится как UTC. */
internal fun epochMin(t: LocalDateTime): Long =
    t.toEpochSecond(ZoneOffset.UTC) / 60

/**
 * Суммарный недобор еженедельного отдыха (в минутах) по всем календарным
 * неделям, целиком лежащим в горизонте прогона. Семантика откалибрована по
 * собственному учёту вендора (2026-07-24): неделя с понедельника 00:00
 * местного времени (tzOffsetMin), отдых = промежутки между окнами
 * занятости, каждый засчитываемый период >= periodMin В ГРАНИЦАХ недели,
 * сумма за неделю >= requiredMin.
 */
internal fun weeklyRestShortfallMin(flights: List<Flight>,
                                    rr: WeeklyRestParams): Long {
    ResourceRules.noteWeeklyRestEvaluation()
    if (flights.isEmpty()) return 0L
    // окна занятости в минутах эпохи; busyWindows юнита точнее его спана
    val windows = ArrayList<LongArray>(flights.size)
    for (f in flights) {
        if (f.busyWindows.isNotEmpty()) {
            for (w in f.busyWindows) {
                if (w.size == 2 && w[1] > w[0]) {
                    windows.add(longArrayOf(w[0], w[1]))
                }
            }
        } else {
            val dep = f.departureUTCDateTime ?: continue
            val arr = f.arrivalUTCDateTime ?: continue
            windows.add(longArrayOf(epochMin(dep), epochMin(arr)))
        }
    }
    if (windows.isEmpty()) return 0L
    windows.sortBy { it[0] }
    val merged = ArrayList<LongArray>(windows.size)
    for (w in windows) {
        val last = merged.lastOrNull()
        if (last != null && w[0] <= last[1]) {
            last[1] = maxOf(last[1], w[1])
        } else {
            merged.add(longArrayOf(w[0], w[1]))
        }
    }
    val firstBusy = merged.first()[0]
    val lastBusy = merged.last()[1]

    // понедельник 00:00 местного времени недели, содержащей firstBusy
    val firstLocal = LocalDateTime.ofEpochSecond(
        (firstBusy + rr.tzOffsetMin) * 60, 0, ZoneOffset.UTC)
    var weekStartLocalDay = firstLocal.toLocalDate()
    while (weekStartLocalDay.dayOfWeek != DayOfWeek.MONDAY) {
        weekStartLocalDay = weekStartLocalDay.minusDays(1)
    }
    var weekLo = weekStartLocalDay.atStartOfDay()
        .toEpochSecond(ZoneOffset.UTC) / 60 - rr.tzOffsetMin

    var shortfall = 0L
    val weekMin = 7L * 24 * 60
    while (weekLo <= lastBusy) {
        val weekHi = weekLo + weekMin
        if (weekLo >= rr.horizonFromEpochMin
            && weekHi <= rr.horizonToEpochMin
            && weekHi > firstBusy && weekLo < lastBusy) {
            var got = 0L
            var cursor = weekLo
            for (w in merged) {
                if (w[1] <= weekLo || w[0] >= weekHi) continue
                if (w[0] > cursor) {
                    val rest = minOf(w[0], weekHi) - cursor
                    if (rest >= rr.periodMin) got += rest
                }
                cursor = maxOf(cursor, minOf(w[1], weekHi))
            }
            if (cursor < weekHi) {
                val rest = weekHi - cursor
                if (rest >= rr.periodMin) got += rest
            }
            if (got < rr.requiredMin) shortfall += rr.requiredMin - got
        }
        weekLo = weekHi
    }
    return shortfall
}
