package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.domain.parseAccountingMoment
import java.math.BigInteger
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Известная часть цены ночёвок по Контракту Б (K-32 единой версии,
 * редакция GPT, объединённая с main d1ee521): ночёвка вне базы
 * начисляется только ДОКАЗАННАЯ — известное присутствие на станции ≠ базе
 * через границу календарной даты между двумя известными моментами; после
 * последнего назначения продолжение неизвестно (ночь не начисляется —
 * main-овское исправление ложной последней ночи сохранено); полночная
 * стыковка ВНУТРИ одной нормативной смены ночью не является; разрыв
 * станций (прилёт ≠ следующий вылет) — неизвестное, не ночь. Явные
 * интервалы истории (K-1) закрывают начальную и конечную границы; снимок
 * historyLocation доказательством не считается.
 *
 * Здесь — известное подытоживание, а не утверждение о неизвестном:
 * `full_cost_confirmed` всегда false (политика H11/P-417 плана —
 * partial ≠ certified=false; полное подтверждение — только у сертификата
 * при полном покрытии ВСЕХ членов цены); при полном покрытии ночёвок в
 * объявленной области — `night_cost_confirmed`. Таксономия причин
 * unknown — контракт для Python-сертификата (vmp/chain_price,
 * roster_certify): time_unknown, home_base_unknown,
 * history_interval_invalid, location_evidence_conflict,
 * overlapping_assignments, station_unknown, discontinuous_timeline,
 * normative_duty_identity_unknown,
 * initial_boundary_location_interval_unknown, terminal_continuation_unknown,
 * idle_employee_location_unknown, unassigned_slot,
 * declared_accounting_horizon_unknown, employee_universe_unknown,
 * known_cost_overflow (the exact subtotal does not fit the report's Long).
 */
object RosterLayoverCost {
    const val SCOPE = "actual_home_known_assignment_gaps_and_explicit_history_intervals"
    const val NIGHT_DEFINITION = "calendar_date_transition_at_station"
    const val TIMEZONE = "input_timestamp_calendar"

    data class Night(val employee: String, val date: LocalDate, val station: String,
                     val from: String, val to: String, val source: String) {
        fun report(): Map<String, Any?> = linkedMapOf(
            "employee" to employee, "date" to date.toString(), "station" to station,
            "from" to from, "to" to to, "source" to source)
    }

    data class Measure(val nights: List<Night>, val unknown: List<Map<String, Any?>>) {
        fun count(days: Set<DayOfWeek>? = null): Long =
            nights.count { days == null || it.date.dayOfWeek in days }.toLong()
    }

    /** Полный порядок вылета — тот же ключ, что у нарезки смен провайдера. */
    private val order = compareBy<FlightAssignment>(
        { it.flight!!.departureUTCDateTime }, { it.flight!!.departureAirport?.code },
        { it.flight!!.arrivalUTCDateTime }, { it.flight!!.arrivalAirport?.code },
        { it.flight!!.flightNumber }, { it.crewSlotId })

    /**
     * Границы области учёта: объявленное окно либо единый fallback
     * ([LogicForms.horizonOf]: календарные сутки от первого до последнего
     * ВЫЛЕТА, [lo 00:00, hi+1 00:00)). Окно решатель не дописывает —
     * иначе прилёт после границы возвращал бы фиктивные сутки (K-11).
     */
    fun bounds(schedule: FlightCrewSchedule): Pair<LocalDateTime?, LocalDateTime?> {
        val declared = schedule.windowHorizon
        if (declared != null) {
            val start = parse(declared.start_inclusive)
                ?: throw TaskModeRefused("window_horizon.start_inclusive — не дата/время ISO")
            val end = parse(declared.end_exclusive)
                ?: throw TaskModeRefused("window_horizon.end_exclusive — не дата/время ISO")
            if (end <= start) throw TaskModeRefused("window_horizon: начало обязано быть раньше конца")
            return start to end
        }
        val h = LogicForms.horizonOf(schedule) ?: return null to null
        return h.startInclusive to h.endExclusive
    }

    /**
     * До создания директора счёта: снять пороги отдыха прогона в
     * решение (нарезка нормативных смен отчёта — по тем же порогам, что
     * у прогона) и раздать границы учёта сотрудникам — измеритель не
     * выводит горизонт из назначений одного человека. Побочный эффект —
     * только поля вне обмена; объявленное окно не трогается.
     */
    fun prepare(schedule: FlightCrewSchedule, applied: Map<String, RunThresholds.Applied>? = null,
                packaged: Boolean = false) {
        if (applied != null) schedule.layoverDutyRules = ChainSearchRules.of(applied, packaged)
        val (start, end) = bounds(schedule)
        schedule.employees.forEach {
            it.layoverAccountingStart = start; it.layoverAccountingEnd = end
        }
    }

    fun measure(assignments: List<FlightAssignment>, accountingStart: LocalDateTime? = null,
                accountingEnd: LocalDateTime? = null,
                employees: List<Employee>? = null,
                dutySlicer: (List<FlightAssignment>) -> List<List<FlightAssignment>> = {
                    NormativeDuties.slice(it) { DefaultProviderRules.MIN_DUTY_REST_MIN }
                }): Measure {
        val nights = linkedMapOf<Pair<String, LocalDate>, Night>()
        val blockedNights = hashSetOf<Pair<String, LocalDate>>()
        val unknown = mutableListOf<Map<String, Any?>>()
        val grouped = assignments.filter { it.employee != null && it.flight != null }.groupBy { it.employee!! }
        val people = (employees.orEmpty() + grouped.keys).associateBy { it.id }.toSortedMap().values
        data class Presence(val start: LocalDateTime, val end: LocalDateTime, val station: String)
        data class Piece(val start: LocalDateTime, val end: LocalDateTime,
                         val station: String?, val reason: String?)
        for (employee in people) {
            val raw = grouped[employee].orEmpty().sortedWith(order)
            val sorted = raw.filter {
                val a = it.flight!!.departureUTCDateTime; val b = it.flight!!.arrivalUTCDateTime
                a != null && b != null && b >= a
            }
            val invalidTime = sorted.size != raw.size
            val home = employee.homeAirport?.code?.takeIf { it.isNotBlank() }
            // Без границ (прямой вызов, прогон без prepare) — прежняя
            // локальная мера по промежуткам между назначениями; report()
            // всегда даёт полную объявленную область и универсум.
            val scopeStart = accountingStart ?: employee.layoverAccountingStart
                ?: sorted.firstOrNull()?.flight?.departureUTCDateTime
            val scopeEnd = accountingEnd ?: employee.layoverAccountingEnd
            fun uncertain(reason: String, from: String?, to: String?, station: String?,
                          start: LocalDateTime?, end: LocalDateTime?) {
                unknown += linkedMapOf("employee" to employee.id, "reason" to reason,
                    "from" to from, "to" to to, "station" to station,
                    "start" to start?.toString(), "end" to end?.toString())
            }
            for (seat in raw.filter { it !in sorted }) {
                uncertain("time_unknown", seat.crewSlotId, null, null,
                    seat.flight!!.departureUTCDateTime, seat.flight!!.arrivalUTCDateTime)
            }
            if (home == null) uncertain("home_base_unknown", null, null, null, scopeStart, scopeEnd)
            val explicit = employee.history?.historyLocationIntervals.orEmpty().mapIndexedNotNull { i, loc ->
                val start = parse(loc.start); val end = parse(loc.end)
                val station = loc.station.takeIf { it.isNotBlank() }
                if (start == null || end == null || end <= start || station == null) {
                    uncertain("history_interval_invalid", "history.location_intervals[$i]", null,
                        station, start, end)
                    null
                } else Presence(start, end, station)
            }
            fun countPresence(p: Presence, from: String?, to: String?, source: String) {
                if (home == null || p.station == home) return
                var date = p.start.toLocalDate()
                while (date < p.end.toLocalDate()) {
                    val key = employee.id to date
                    val old = nights[key]
                    if (old != null && old.station != p.station) {
                        nights.remove(key); blockedNights.add(key)
                        uncertain("location_evidence_conflict", from, to, p.station, p.start, p.end)
                    } else if (key !in blockedNights) {
                        nights.putIfAbsent(key, Night(employee.id, date, p.station,
                            from ?: "scope_start", to ?: "scope_end", source))
                    }
                    date = date.plusDays(1)
                }
            }
            fun gap(rawStart: LocalDateTime?, rawEnd: LocalDateTime?,
                    leftStation: String?, rightStation: String?, inferred: Boolean,
                    reason: String, from: String?, to: String?, source: String, countNights: Boolean = true) {
                if (rawStart == null || rawEnd == null) {
                    uncertain(reason, from, to, leftStation ?: rightStation, rawStart, rawEnd)
                    return
                }
                if (rawEnd < rawStart) {
                    // Пересекаться могут только два назначения. Начальная
                    // или конечная граница вне объявленного горизонта —
                    // пустой промежуток, не конфликт и не расширение.
                    if (inferred) uncertain("overlapping_assignments", from, to, leftStation, rawStart, rawEnd)
                    return
                }
                val start = if (scopeStart != null) maxOf(rawStart, scopeStart) else rawStart
                val end = if (scopeEnd != null) minOf(rawEnd, scopeEnd) else rawEnd
                if (end <= start) return
                if (inferred && (leftStation == null || rightStation == null || leftStation != rightStation)) {
                    uncertain(if (leftStation == null || rightStation == null) "station_unknown" else "discontinuous_timeline",
                        from, to, leftStation, start, end)
                    return
                }
                if (inferred) {
                    // Непрерывность известных назначений судится одним
                    // промежутком; противоречащее явное свидетельство делает
                    // его неизвестным.
                    if (explicit.any { it.start < rawEnd && it.end > rawStart && it.station != leftStation }) {
                        uncertain("location_evidence_conflict", from, to, leftStation, rawStart, rawEnd)
                    } else if (countNights) countPresence(Presence(start, end, leftStation!!), from, to, source)
                    return
                }
                val cuts = java.util.TreeSet<LocalDateTime>()
                cuts.add(start); cuts.add(end)
                for (p in explicit) if (p.start < end && p.end > start) {
                    cuts.add(maxOf(start, p.start)); cuts.add(minOf(end, p.end))
                }
                val cells = cuts.toList()
                val pieces = ArrayList<Piece>()
                for ((a, b) in cells.zipWithNext()) {
                    val stations = explicit.filter { it.start <= a && it.end >= b }.map { it.station }.toSet()
                    val station = stations.singleOrNull()
                    val cellReason = when {
                        stations.isEmpty() -> reason
                        stations.size > 1 -> "location_evidence_conflict"
                        (a == start && leftStation != null && station != leftStation) ||
                            (b == end && rightStation != null && station != rightStation) -> "discontinuous_timeline"
                        else -> null
                    }
                    val prev = pieces.lastOrNull()
                    if (prev != null && prev.end == a && prev.station == station && prev.reason == cellReason) {
                        pieces[pieces.lastIndex] = Piece(prev.start, b, station, cellReason)
                    } else {
                        pieces.add(Piece(a, b, station, cellReason))
                    }
                }
                for ((prev, next) in pieces.zipWithNext()) {
                    if (prev.reason == null && next.reason == null && prev.station != next.station) {
                        uncertain("location_evidence_discontinuity", from, to, prev.station, prev.end, next.start)
                    }
                }
                for (piece in pieces) {
                    if (piece.reason != null) uncertain(piece.reason, from, to,
                        piece.station ?: leftStation ?: rightStation, piece.start, piece.end)
                    else if (countNights) countPresence(Presence(piece.start, piece.end, piece.station!!), from, to, source)
                }
            }

            if (sorted.isEmpty()) {
                gap(scopeStart, scopeEnd, null, null, false, "idle_employee_location_unknown",
                    null, null, "explicit_history")
                continue
            }
            val dutyIds = dutySlicer(sorted).flatMapIndexed { i, duty -> duty.map { it.id to i } }.toMap()
            val first = sorted.first()
            gap(scopeStart, first.flight!!.departureUTCDateTime, null, first.flight!!.departureAirport?.code,
                false, "initial_boundary_location_interval_unknown", null, first.crewSlotId, "explicit_history")
            for ((left, right) in sorted.zipWithNext()) {
                val leftDuty = dutyIds[left.id]; val rightDuty = dutyIds[right.id]
                val identityKnown = leftDuty != null && rightDuty != null && !invalidTime
                val start = left.flight!!.arrivalUTCDateTime
                val end = right.flight!!.departureUTCDateTime
                val clippedStart = start?.let { if (scopeStart != null) maxOf(it, scopeStart) else it }
                val clippedEnd = end?.let { if (scopeEnd != null) minOf(it, scopeEnd) else it }
                if (!identityKnown && clippedStart != null && clippedEnd != null &&
                    clippedStart.toLocalDate() < clippedEnd.toLocalDate() && left.flight!!.arrivalAirport?.code != home) {
                    uncertain("normative_duty_identity_unknown", left.crewSlotId, right.crewSlotId,
                        left.flight!!.arrivalAirport?.code, clippedStart, clippedEnd)
                }
                gap(start, end, left.flight!!.arrivalAirport?.code, right.flight!!.departureAirport?.code,
                    true, "internal_location_unknown", left.crewSlotId, right.crewSlotId,
                    "adjacent_assignments", identityKnown && leftDuty != rightDuty)
            }
            val last = sorted.last()
            gap(last.flight!!.arrivalUTCDateTime, scopeEnd, last.flight!!.arrivalAirport?.code, null,
                false, "terminal_continuation_unknown", last.crewSlotId, null, "explicit_history")
        }
        return Measure(nights.values.sortedWith(compareBy({ it.employee }, { it.date }, { it.station })),
            unknown.distinctBy { listOf(it["employee"], it["reason"], it["start"], it["end"], it["station"]) })
    }

    /** Отчёт v2 по решению: область, универсум, известные ночи, адресный
     *  перечень неизвестного. Считается один раз после счёта
     *  (RunRecord.layoverCost), не при каждой сериализации решения. */
    fun report(schedule: FlightCrewSchedule): Map<String, Any?> {
        val (start, end) = bounds(schedule)
        val rules = schedule.layoverDutyRules
        val measure = measure(schedule.flightAssignments, start, end, schedule.employees) { assignments ->
            NormativeDuties.slice(assignments) { fa -> rules.rest(fa.flight?.arrivalAirport?.code, fa.employee!!) }
        }
        val unassigned = schedule.flightAssignments.filter { it.employee == null && !it.deadhead }
        // K-32-a (H11-4 плана): область, выведенная из программы рейсов —
        // окно не объявлено либо source=inferred, — не объявленный горизонт
        // учёта: адресное неизвестное с пометкой, отчёт частичный
        // (complete=false, certainty=partial, night_cost_confirmed=false);
        // известные ночи и события остаются известными.
        val declared = schedule.windowHorizon?.takeIf { it.source != "inferred" }
        val scopeUnknown = if (start == null || end == null || declared == null) listOf(linkedMapOf<String, Any?>(
            "employee" to null, "reason" to "declared_accounting_horizon_unknown", "station" to null,
            "start" to start?.toString(), "end" to end?.toString(),
            "note" to (if (start == null || end == null)
                "область не определена: ни объявленного окна, ни программы рейсов"
                else "область выведена из программы рейсов, окно не объявлено"))) else emptyList()
        val universeUnknown = if (schedule.employees.isEmpty()) listOf(linkedMapOf<String, Any?>(
            "employee" to null, "reason" to "employee_universe_unknown", "station" to null,
            "start" to start?.toString(), "end" to end?.toString())) else emptyList()
        val evidenceUnknown = measure.unknown + scopeUnknown + universeUnknown + unassigned.map { seat -> linkedMapOf<String, Any?>(
            "reason" to "unassigned_slot", "employee" to null, "from" to seat.crewSlotId,
            "to" to null, "flight" to seat.flight?.flightNumber,
            "station" to null, "start" to seat.flight?.departureUTCDateTime?.toString(),
            "end" to seat.flight?.arrivalUTCDateTime?.toString()) }
        val windows = schedule.calendarWindowRules.filter { it.action == "layover_penalty" }
            .groupBy { it.ruleId }.mapValues { (_, rules) -> rules.sumOf { measure.count(it.days) } }
        val weights = schedule.layoverCostWeights
        // This report is outside Timefold's checked score arithmetic. Preserve
        // the exact products AND their sum before conversion; a wrapped negative
        // subtotal must never be published as a confirmed positive night cost.
        val exactCost = if (weights.isEmpty()) null else
            BigInteger.valueOf(measure.count()).multiply(BigInteger.valueOf(weights["Layover nights away from home"] ?: 0L))
                .add(windows.values.fold(BigInteger.ZERO) { total, value -> total.add(BigInteger.valueOf(value)) }
                    .multiply(BigInteger.valueOf(weights["Calendar window layover penalty"] ?: 0L)))
        val knownCost = try { exactCost?.longValueExact() } catch (_: ArithmeticException) { null }
        val unknown = evidenceUnknown + if (exactCost != null && knownCost == null) listOf(linkedMapOf<String, Any?>(
            "employee" to null, "reason" to "known_cost_overflow", "station" to null,
            "start" to start?.toString(), "end" to end?.toString(),
            "note" to "точная стоимость ночёвок не представима в Long; сумма не подтверждена")) else emptyList()
        return linkedMapOf(
            "version" to 2, "contract" to "B", "task" to "rostering", "scope" to SCOPE, "timezone" to TIMEZONE,
            "scope_start" to start?.toString(), "scope_end" to end?.toString(),
            "scope_source" to (schedule.windowHorizon?.let { it.source ?: "declared_window_horizon" }
                ?: "flight_program_departure_calendar_span"),
            "night_definition" to NIGHT_DEFINITION,
            "duty_semantics" to "normative_duty_identity",
            "employees" to schedule.employees.map { it.id }.sorted(),
            "complete" to unknown.isEmpty(),
            "certainty" to if (unknown.isEmpty()) "complete_in_declared_scope" else "partial",
            "full_cost_confirmed" to false,
            "night_cost_confirmed" to (unknown.isEmpty() && knownCost != null && start != null && end != null),
            "known_subtotal_is_full_objective" to false,
            "known_nights" to measure.count(), "known_calendar_nights" to windows,
            "known_subtotal" to knownCost, "known_events" to measure.nights.map { it.report() },
            "unassigned_required_slots" to unassigned.size,
            "unknown_count" to unknown.size, "unknown_events" to unknown)
    }

    private fun parse(value: String?): LocalDateTime? =
        try { value?.let(::parseAccountingMoment) }
        catch (_: Exception) { null }
}
