package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.EmployeeHistory
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.HistoryLocationInterval
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource

/**
 * Стенды Контракта Б для Kotlin-цены ночёвок (K-32/K-33 единой версии,
 * стенды GPT RosterLayoverCostTest, адаптированные к main: окно решатель
 * не дописывает — область без объявленного окна берётся из единого
 * горизонта по вылетам; отчёт не сериализуется с решением). Календарь
 * фикстур: март-2026.
 */
class RosterLayoverCostTest {
    private val home = airport("HOME")
    private val away = airport("AWAY")
    private val e = employee("E").apply { homeAirport = home }
    private fun seat(id: String, dep: org.acme.flighcrewscheduling.domain.Airport,
                     start: java.time.LocalDateTime, arr: org.acme.flighcrewscheduling.domain.Airport,
                     end: java.time.LocalDateTime) =
        FlightAssignment(id, flight(id, dep, start, arr, end), 0, Fixtures.SKILL).apply { employee = e }

    @Test
    fun `terminal daytime arrival is unknown not one night`() {
        val m = RosterLayoverCost.measure(listOf(seat("a", home, t(6, 10), away, t(6, 12))))
        assertEquals(0L, m.count())
        assertEquals("terminal_continuation_unknown", m.unknown.single()["reason"])
    }

    @Test
    fun `real overnight counted once and chain partition does not matter`() {
        e.layoverAccountingEnd = t(7, 12)
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", away, t(7, 10), home, t(7, 12)))
        aa.forEach { it.chainId = "one" }
        val together = RosterLayoverCost.measure(aa)
        aa.forEach { it.chainId = it.id }
        assertEquals(together, RosterLayoverCost.measure(aa))
        assertEquals(1L, together.count())
        assertTrue(together.unknown.isEmpty())
    }

    @Test
    fun `start station does not replace actual home`() {
        val aa = listOf(seat("a", away, t(6, 10), home, t(6, 12)),
            seat("b", home, t(7, 10), away, t(7, 12)))
        assertEquals(0L, RosterLayoverCost.measure(aa).count())
        assertEquals(1, RosterLayoverCost.measure(aa).unknown.size)
    }

    @Test
    fun `known internal night survives unknown terminal continuation`() {
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", away, t(7, 10), away, t(7, 12)))
        val m = RosterLayoverCost.measure(aa)
        assertEquals(1L, m.count())
        assertEquals("terminal_continuation_unknown", m.unknown.single()["reason"])
    }

    @Test
    fun `same day return and explicit deadhead create no artificial layover`() {
        e.layoverAccountingEnd = t(6, 18)
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", away, t(6, 16), home, t(6, 18)).apply { deadhead = true })
        val m = RosterLayoverCost.measure(aa)
        assertEquals(0L, m.count())
        assertTrue(m.unknown.isEmpty())
    }

    @Test
    fun `location snapshot alone is not proof of historical overnight`() {
        e.layoverAccountingStart = t(5, 0)
        e.layoverAccountingEnd = t(6, 12)
        e.historyAsOf = t(6, 0).toString()
        e.history = EmployeeHistory().apply {
            historyLastDutyEnd = t(5, 16).toString()
            historyLastDutyStation = away.code
            historyLocation = away.code
        }
        val aa = listOf(seat("a", away, t(6, 10), home, t(6, 12)))
        val unknown = RosterLayoverCost.measure(aa)
        assertEquals(0L, unknown.count())
        assertTrue(unknown.unknown.isNotEmpty())
        e.history!!.historyLocationIntervals.add(HistoryLocationInterval().apply {
            start = t(5, 16).toString(); end = t(6, 10).toString(); station = away.code
            kind = "history_fact"
        })
        val known = RosterLayoverCost.measure(aa)
        assertEquals(1L, known.count())
        // Явный интервал доказывает свою ночь, но не покрывает раннюю
        // часть объявленной области (5-го 00:00–16:00).
        assertEquals("initial_boundary_location_interval_unknown", known.unknown.single()["reason"])
        e.history!!.historyLocationIntervals.add(HistoryLocationInterval().apply {
            start = t(5, 0).toString(); end = t(5, 16).toString(); station = away.code
        })
        assertTrue(RosterLayoverCost.measure(aa).unknown.isEmpty())
        // Та же известная историческая ночь вне более поздней области учёта.
        e.layoverAccountingStart = t(6, 0)
        assertEquals(0L, RosterLayoverCost.measure(aa).count())
    }

    @Test
    fun `station break is unknown and does not manufacture a night`() {
        e.layoverAccountingEnd = t(7, 12)
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", home, t(7, 10), home, t(7, 12)))
        val m = RosterLayoverCost.measure(aa)
        assertEquals(0L, m.count())
        assertEquals("discontinuous_timeline", m.unknown.single()["reason"])
    }

    @Test
    fun `multiple employees retain individual home and night identity`() {
        val other = employee("E2").apply { homeAirport = away }
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", away, t(7, 10), home, t(7, 12)))
        val bb = aa.map { a -> FlightAssignment("x" + a.id, a.flight!!, 1, Fixtures.SKILL).apply {
            employee = other
        } }
        val m = RosterLayoverCost.measure(aa + bb)
        assertEquals(1L, m.count())
        assertEquals("E", m.nights.single().employee)
    }

    private fun presence(startAt: java.time.LocalDateTime, endAt: java.time.LocalDateTime, stationAt: String) {
        if (e.history == null) e.history = EmployeeHistory()
        e.history!!.historyLocationIntervals.add(HistoryLocationInterval().apply {
            start = startAt.toString(); end = endAt.toString(); station = stationAt
        })
    }

    @Test
    fun `late departure at home does not prove the initial horizon location`() {
        val aa = listOf(seat("a", home, t(8, 10), home, t(8, 12)))
        val m = RosterLayoverCost.measure(aa, t(6, 0), t(8, 12), listOf(e))
        assertEquals(0L, m.count())
        val missing = m.unknown.single()
        assertEquals("initial_boundary_location_interval_unknown", missing["reason"])
        assertEquals(t(6, 0).toString(), missing["start"])
        assertEquals(t(8, 10).toString(), missing["end"])
    }

    @Test
    fun `adjacent explicit intervals jointly cover the initial gap`() {
        val aa = listOf(seat("a", away, t(8, 10), home, t(8, 12)))
        presence(t(6, 0), t(7, 0), away.code)
        presence(t(7, 0), t(8, 10), away.code)
        val m = RosterLayoverCost.measure(aa, t(6, 0), t(8, 12), listOf(e))
        assertEquals(2L, m.count())
        assertTrue(m.unknown.isEmpty())
    }

    @Test
    fun `idle employee has unknown location until explicit intervals cover the scope`() {
        val unknown = RosterLayoverCost.measure(emptyList(), t(6, 0), t(8, 0), listOf(e))
        assertEquals("idle_employee_location_unknown", unknown.unknown.single()["reason"])
        presence(t(6, 0), t(8, 0), away.code)
        val known = RosterLayoverCost.measure(emptyList(), t(6, 0), t(8, 0), listOf(e))
        assertEquals(2L, known.count())
        assertTrue(known.unknown.isEmpty())
    }

    @Test
    fun `contradictory boundary evidence cannot become a known night`() {
        val aa = listOf(seat("a", away, t(7, 10), home, t(7, 12)))
        presence(t(6, 0), t(7, 10), away.code)
        presence(t(6, 0), t(7, 10), home.code)
        val m = RosterLayoverCost.measure(aa, t(6, 0), t(7, 12), listOf(e))
        assertEquals(0L, m.count())
        assertEquals("location_evidence_conflict", m.unknown.single()["reason"])
    }

    @Test
    fun `explicit terminal continuation closes only the declared night scope`() {
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)))
        presence(t(6, 12), t(8, 0), away.code)
        val schedule = Fixtures.schedule(listOf(home, away), listOf(e), aa).apply {
            windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
                start_inclusive = t(6, 10).toString(); end_exclusive = t(8, 0).toString()
            }
            layoverCostWeights = mapOf("Layover nights away from home" to 10L)
        }
        RosterLayoverCost.prepare(schedule)
        val report = RosterLayoverCost.report(schedule)
        assertEquals(2L, report["known_nights"])
        assertEquals(20L, report["known_subtotal"])
        assertEquals(true, report["night_cost_confirmed"])
        assertEquals(false, report["full_cost_confirmed"])
        assertEquals("declared_window_horizon", report["scope_source"])
    }

    @ParameterizedTest
    @ValueSource(strings = ["layover", "calendar", "sum"])
    fun `unrepresentable known cost stays unknown rather than negative confirmed cost`(kind: String) {
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)))
        presence(t(6, 12), t(8, 0), away.code)
        val schedule = Fixtures.schedule(listOf(home, away), listOf(e), aa).apply {
            windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
                start_inclusive = t(6, 10).toString(); end_exclusive = t(8, 0).toString()
            }
            calendarWindowRules.add(org.acme.flighcrewscheduling.domain.CalendarWindowRule(
                "R-window", "layover_penalty", java.time.DayOfWeek.entries.toSet()))
            layoverCostWeights = when (kind) {
                "calendar" -> mapOf("Calendar window layover penalty" to Long.MAX_VALUE)
                "sum" -> mapOf("Layover nights away from home" to Long.MAX_VALUE / 4 + 1,
                    "Calendar window layover penalty" to Long.MAX_VALUE / 4 + 1)
                else -> mapOf("Layover nights away from home" to Long.MAX_VALUE)
            }
        }
        RosterLayoverCost.prepare(schedule)
        val report = RosterLayoverCost.report(schedule)
        assertEquals(2L, report["known_nights"])
        assertNull(report["known_subtotal"])
        assertEquals(false, report["night_cost_confirmed"])
        assertEquals(false, report["complete"])
        assertTrue((report["unknown_events"] as List<*>).any {
            (it as Map<*, *>)["reason"] == "known_cost_overflow"
        })
        schedule.layoverCostWeights = mapOf("Layover nights away from home" to Long.MAX_VALUE / 2)
        val healthy = RosterLayoverCost.report(schedule)
        assertEquals(Long.MAX_VALUE - 1, healthy["known_subtotal"])
        assertEquals(true, healthy["night_cost_confirmed"])
        assertEquals(2, (healthy["known_events"] as List<*>).size)
        // A huge rate on zero actual nights is representable; no arbitrary cap.
        schedule.windowHorizon!!.end_exclusive = t(6, 23).toString()
        schedule.layoverCostWeights = mapOf("Layover nights away from home" to Long.MAX_VALUE)
        RosterLayoverCost.prepare(schedule)
        val zero = RosterLayoverCost.report(schedule)
        assertEquals(0L, zero["known_subtotal"])
        assertEquals(true, zero["night_cost_confirmed"])
    }

    @Test
    fun `midnight sit within one normative duty is not a layover night`() {
        val aa = listOf(seat("a", home, t(6, 20), away, t(6, 22)),
            seat("b", away, t(7, 1), home, t(7, 3)))
        aa.forEach { it.chainId = it.id }
        val m = RosterLayoverCost.measure(aa, t(6, 20), t(7, 3), listOf(e))
        assertEquals(0L, m.count())
        assertTrue(m.unknown.isEmpty())
    }

    @Test
    fun `post solve cost report retains the packaged normative rest thresholds`() {
        val aa = listOf(seat("a", home, t(6, 16), away, t(6, 18)),
            seat("b", away, t(7, 3), home, t(7, 5)))
        val schedule = Fixtures.schedule(listOf(home, away), listOf(e), aa).apply {
            windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
                start_inclusive = t(6, 16).toString(); end_exclusive = t(7, 5).toString()
            }
        }
        val param = RuleParam("min_rest_outstation", 600.0, "600", "duration_minutes", "min", null, "", "R-rest", "HARD")
        val applied = RunThresholds.resolve(listOf(param), TaskModes.PROFILE_DEFAULT).applied
        // Профиль 8 часов разрезал бы 9-часовой разрыв; 10-часовой минимум
        // пакета держит его внутри нормативной смены и после счёта.
        assertEquals(1L, RosterLayoverCost.measure(aa).count())
        RosterLayoverCost.prepare(schedule, applied, packaged = true)
        assertEquals(0L, RosterLayoverCost.report(schedule)["known_nights"])
    }

    @Test
    fun `arrival after horizon is not overlap and all unassigned slots remain unknown`() {
        val crossing = seat("crossing", home, t(6, 23, 30), away, t(7, 1, 10))
        val unassigned = (0 until 150).map { i ->
            seat("unassigned-$i", home, t(6, 10), away, t(6, 12)).apply { employee = null }
        }
        val schedule = Fixtures.schedule(listOf(home, away), listOf(e), listOf(crossing) + unassigned).apply {
            windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
                start_inclusive = t(6, 0).toString(); end_exclusive = t(7, 0).toString()
            }
        }
        RosterLayoverCost.prepare(schedule)
        val report = RosterLayoverCost.report(schedule)
        val unknown = report["unknown_events"] as List<*>
        assertEquals(150, report["unassigned_required_slots"])
        assertEquals(151, report["unknown_count"])
        assertEquals(150, unknown.count { (it as Map<*, *>)["reason"] == "unassigned_slot" })
        assertEquals(1, unknown.count { (it as Map<*, *>)["reason"] == "initial_boundary_location_interval_unknown" })
        assertFalse(unknown.any { (it as Map<*, *>)["reason"] == "overlapping_assignments" })
        assertEquals(false, report["complete"])
        assertEquals("partial", report["certainty"])
        assertEquals(false, report["full_cost_confirmed"])
        assertEquals(t(7, 0).toString(), report["scope_end"])
        assertEquals(t(7, 0).toString(), schedule.windowHorizon!!.end_exclusive)
        assertEquals(t(7, 1, 10), crossing.flight!!.arrivalUTCDateTime)
    }

    @Test
    fun `departure before horizon does not create an initial overlap`() {
        val crossing = seat("crossing", home, t(5, 23, 30), away, t(6, 1, 10))
        val m = RosterLayoverCost.measure(listOf(crossing), t(6, 0), t(6, 1, 10), listOf(e))
        assertEquals(0L, m.count())
        assertTrue(m.unknown.isEmpty())
        assertEquals(t(5, 23, 30), crossing.flight!!.departureUTCDateTime)
    }

    @Test
    fun `actual overlapping assignments still produce addressed uncertainty`() {
        val aa = listOf(seat("left", home, t(6, 10), away, t(6, 12)),
            seat("right", away, t(6, 11), home, t(6, 13)))
        val m = RosterLayoverCost.measure(aa, t(6, 10), t(6, 13), listOf(e))
        assertEquals(0L, m.count())
        val unknown = m.unknown.single()
        assertEquals("overlapping_assignments", unknown["reason"])
        assertEquals(t(6, 12).toString(), unknown["start"])
        assertEquals(t(6, 11).toString(), unknown["end"])
        assertEquals(away.code, unknown["station"])
    }

    @Test
    fun `выведенная область — адресное неизвестное, отчёт частичный, известные ночи остаются`() {
        // K-32-a (H11-4): ночь 6-го в AWAY доказана, обе границы закрыты
        // явными интервалами присутствия дома — при ОБЪЯВЛЕННОМ окне отчёт
        // полный; без окна (область из программы рейсов) либо с
        // source=inferred — адресное declared_accounting_horizon_unknown с
        // пометкой, complete=false, certainty=partial,
        // night_cost_confirmed=false, а известная ночь остаётся.
        val aa = listOf(seat("a", home, t(6, 10), away, t(6, 12)),
            seat("b", away, t(7, 10), home, t(7, 12)))
        presence(t(6, 0), t(6, 10), home.code)
        presence(t(7, 12), t(8, 0), home.code)
        fun schedule(window: org.acme.flighcrewscheduling.domain.AccountingWindow?) =
            Fixtures.schedule(listOf(home, away), listOf(e), aa).apply {
                windowHorizon = window
                layoverCostWeights = mapOf("Layover nights away from home" to 10L)
            }
        fun window(source: String?) = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = t(6, 0).toString(); end_exclusive = t(8, 0).toString(); this.source = source
        }
        val declared = schedule(window(null))
        RosterLayoverCost.prepare(declared)
        val full = RosterLayoverCost.report(declared)
        assertEquals(1L, full["known_nights"])
        assertEquals(true, full["complete"])
        assertEquals(true, full["night_cost_confirmed"])
        for (s in listOf(schedule(null), schedule(window("inferred")))) {
            RosterLayoverCost.prepare(s)
            val report = RosterLayoverCost.report(s)
            assertEquals(1L, report["known_nights"])
            assertEquals(1, (report["known_events"] as List<*>).size)
            assertEquals(10L, report["known_subtotal"])
            val unknown = (report["unknown_events"] as List<*>).map { it as Map<*, *> }
            assertEquals(1, unknown.size, unknown.toString())
            val ev = unknown.single()
            assertEquals("declared_accounting_horizon_unknown", ev["reason"])
            assertEquals(null, ev["employee"])
            assertEquals(null, ev["station"])
            assertEquals(t(6, 0).toString(), ev["start"])
            assertEquals(t(8, 0).toString(), ev["end"])
            assertEquals("область выведена из программы рейсов, окно не объявлено", ev["note"])
            assertEquals(false, report["complete"])
            assertEquals("partial", report["certainty"])
            assertEquals(false, report["night_cost_confirmed"])
            assertEquals(false, report["full_cost_confirmed"])
        }
        assertEquals("flight_program_departure_calendar_span", RosterLayoverCost.report(schedule(null).also { RosterLayoverCost.prepare(it) })["scope_source"])
        assertEquals("inferred", RosterLayoverCost.report(schedule(window("inferred")).also { RosterLayoverCost.prepare(it) })["scope_source"])
    }

    @Test
    fun `без объявленного окна область — единый горизонт по вылетам, окно решатель не дописывает`() {
        // K-11: prepare() не ставит windowHorizon; область — сутки от первого
        // до последнего ВЫЛЕТА (прилёт 30.09 23:30 → 01.10 календарь не
        // расширяет); без сотрудников и без рейсов — адресное неизвестное.
        val crossing = seat("crossing", home, t(6, 23, 30), away, t(7, 1, 10))
        val schedule = Fixtures.schedule(listOf(home, away), listOf(e), listOf(crossing))
        RosterLayoverCost.prepare(schedule)
        assertNull(schedule.windowHorizon)
        assertEquals(t(6, 0), e.layoverAccountingStart)
        assertEquals(t(7, 0), e.layoverAccountingEnd)
        val report = RosterLayoverCost.report(schedule)
        assertEquals("flight_program_departure_calendar_span", report["scope_source"])
        assertEquals(t(7, 0).toString(), report["scope_end"])
        assertEquals(null, report["known_subtotal"])
        val empty = RosterLayoverCost.report(Fixtures.schedule(listOf(home), emptyList(), emptyList()))
        val reasons = (empty["unknown_events"] as List<*>).map { (it as Map<*, *>)["reason"] }
        assertTrue("declared_accounting_horizon_unknown" in reasons)
        assertTrue("employee_universe_unknown" in reasons)
    }
}
