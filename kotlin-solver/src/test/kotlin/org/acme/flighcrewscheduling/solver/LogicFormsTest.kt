package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth

/**
 * Форма первой волны ступени 3: ВЫХОДНЫХ В МЕСЯЦ НЕ МЕНЕЕ N.
 *
 * Проверяется прочтение, а не арифметика: клетка — сотрудник × ПОЛНЫЙ
 * месяц горизонта; сутки заняты, если их касается хотя бы одно
 * назначение (рейс через полночь занимает двое суток); сотрудник без
 * назначений — все дни выходные (подстановка нуля, а не молчание);
 * неполный месяц вне оценки; область — только код базы.
 */
class LogicFormsTest {

    @AfterEach
    fun cleanup() { RunThresholds.clearForTests() }

    private val home = airport("PLY")
    private val out = airport("OUT")

    private fun plan(bound: Int, base: String? = null) = LogicPlan(
        ruleIds = listOf("R-34"), constraintId = "logic test0001",
        form = LogicForms.FORM_DAYS_OFF_PER_MONTH_MIN, subject = "day_off", relation = "min",
        bound = bound, boundParam = "min_days_off", baseScope = base, enforce = "required",
        weight = 1, reading = LogicForms.READING_DAYS_OFF, shapeSha = "x")

    private fun restPlan(restMin: Int, longMin: Int) = LogicPlan(
        ruleIds = listOf("R-20"), constraintId = "logic test0003",
        form = LogicForms.FORM_REST_AFTER_LONG_DUTY_MIN, subject = "rest", relation = "min",
        bound = restMin, boundParam = "min_rest_after_long", baseScope = null,
        condBound = longMin, condParam = "long_duty_threshold", enforce = "required",
        weight = 1, reading = LogicForms.READING_REST_AFTER, shapeSha = "x")

    private fun runsPlan(bound: Int, base: String? = null) = LogicPlan(
        ruleIds = listOf("R-23"), constraintId = "logic test0002",
        form = LogicForms.FORM_CONSECUTIVE_DAYS_MAX, subject = "day", relation = "max",
        bound = bound, boundParam = "max_consecutive_days", baseScope = base, enforce = "required",
        weight = 1, reading = LogicForms.READING_CONSECUTIVE, shapeSha = "x")

    /** Рейс в сутки `day` марта 2026, 10:00–12:00. */
    private fun dayFlight(n: Int, day: Int): Flight =
        flight("F$n", home, t(day, 10), out, t(day, 12))

    /**
     * Проверяющий требует, чтобы ограничение было среди defineConstraints
     * провайдера: провайдер создаётся ВНУТРИ окна с планом — ровно так, как
     * его создаёт фабрика прогона. Формы на нарезке смен строятся ЕГО
     * нарезкой (buildLogic), не голым построителем.
     */
    private fun check(p: LogicPlan, vararg facts: Any) =
        RunThresholds.withRunModel(emptyMap(), listOf(p)) {
            ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
                FlightCrewSchedule::class.java, FlightAssignment::class.java)
        }.verifyThat { prov, cf -> prov.buildLogic(cf, p) }.given(*facts)

    @Test
    fun `полный месяц горизонта — нехватка выходных штрафуется за каждый день`() {
        // Горизонт 1–31 марта: два граничных рейса задают полный месяц.
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        // E1 занят 25 различных суток марта -> выходных 6, нехватка 2 при норме 8.
        for (d in 1..25) {
            val f = dayFlight(d, d)
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        val edge = dayFlight(99, 31)
        facts.add(edge); facts.add(assignment("s99", edge, null))
        check(plan(8), *facts.toTypedArray()).penalizesBy(2)
        // E2 без назначений: 31 выходной — ноль; при норме 32 нехватка 1 у E2 и 26 у E1.
        check(plan(32), *facts.toTypedArray()).penalizesBy(26 + 1)
    }

    @Test
    fun `неполный месяц — вне оценки, штрафа нет даже у занятого каждый день`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        for (d in 10..20) {
            val f = dayFlight(d, d)
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        check(plan(8), *facts.toTypedArray()).penalizes(0)
        assertTrue(LogicForms.fullMonths(LocalDate.of(2026, 3, 10), LocalDate.of(2026, 4, 5)).isEmpty())
        assertEquals(listOf(YearMonth.of(2026, 3)),
            LogicForms.fullMonths(LocalDate.of(2026, 3, 1), LocalDate.of(2026, 4, 5)))
    }

    @Test
    fun `рейс через полночь занимает двое суток`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val first = flight("F0", home, t(1, 8), out, t(1, 10))
        val last = flight("F31", home, t(31, 8), out, t(31, 10))
        val night = flight("FN", home, t(15, 22), out, LocalDateTime.of(2026, 3, 16, 2, 0))
        assertEquals(listOf(LocalDate.of(2026, 3, 15), LocalDate.of(2026, 3, 16)), LogicForms.touchedDays(night))
        // Заняты 1, 15, 16 и 31 марта -> выходных 27; норма 29 -> нехватка 2.
        check(plan(29), e1, first, last, night,
            assignment("a", first, e1), assignment("b", last, e1), assignment("c", night, e1))
            .penalizesBy(2)
    }

    @Test
    fun `область — код базы, сотрудник другой базы вне счёта`() {
        val svg = airport("SVG")
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = svg }
        val first = dayFlight(1, 1); val last = dayFlight(31, 31)
        // Оба без назначений; норма 32 невыполнима ни у кого: в области PLY — один человек.
        check(plan(32, base = "PLY"), e1, e2, first, last,
            assignment("a", first, null), assignment("b", last, null)).penalizesBy(1)
        check(plan(32), e1, e2, first, last,
            assignment("a", first, null), assignment("b", last, null)).penalizesBy(2)
    }

    // ------------------------------ ночных смен подряд (правило 14)

    private fun nightPlan(bound: Int) = LogicPlan(
        ruleIds = listOf("R-14"), constraintId = "logic test0005",
        form = LogicForms.FORM_CONSECUTIVE_NIGHT_DUTIES_MAX, subject = "duty", relation = "max",
        bound = bound, boundParam = "max_consecutive_night_duties", baseScope = null,
        nightFrom = 22 * 60, nightTo = 6 * 60, enforce = "required",
        weight = 1, reading = LogicForms.READING_NIGHT, shapeSha = "x")

    /** Ночная смена в сутки d: вылет 23:00, прилёт 01:00 следующих суток. */
    private fun nightDuty(n: Int, d: Int) =
        flight("N$n", home, t(d, 23), out, LocalDateTime.of(2026, 3, d + 1, 1, 0))

    @Test
    fun `три ночных подряд при границе две — штраф за лишнюю`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        for (d in listOf(2, 4, 6)) {
            val f = nightDuty(d, d)
            facts.add(f); facts.add(assignment("n$d", f, e1))
        }
        // Между ночными нет других смен: серия из трёх ночных, граница 2.
        check(nightPlan(2), *facts.toTypedArray()).penalizesBy(1)
        check(nightPlan(3), *facts.toTypedArray()).penalizes(0)
    }

    @Test
    fun `дневная смена между ночными рвёт серию`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val n1 = nightDuty(1, 2); val n2 = nightDuty(2, 4); val n3 = nightDuty(3, 8)
        val day = flight("D1", out, t(6, 10), home, t(6, 12))
        check(nightPlan(2), e1, n1, n2, n3, day,
            assignment("a", n1, e1), assignment("b", n2, e1),
            assignment("c", day, e1), assignment("d", n3, e1)).penalizes(0)
        assertTrue(LogicForms.inNightWindow(23 * 60, 22 * 60, 6 * 60))
        assertTrue(LogicForms.inNightWindow(3 * 60, 22 * 60, 6 * 60))
        assertFalse(LogicForms.inNightWindow(10 * 60, 22 * 60, 6 * 60))
        assertFalse(LogicForms.inNightWindow(6 * 60, 22 * 60, 6 * 60))
    }

    @Test
    fun `смена с вылетом до шести утра — ночная (окно через полночь)`() {
        val e1 = employee("E1").apply { homeAirport = home }
        // Три смены с вылетами 05:00 — все в окне [22:00, 06:00).
        val facts = ArrayList<Any>(listOf(e1))
        for (d in listOf(2, 4, 6)) {
            val f = flight("M$d", home, t(d, 5), out, t(d, 7))
            facts.add(f); facts.add(assignment("m$d", f, e1))
        }
        check(nightPlan(2), *facts.toTypedArray()).penalizesBy(1)
    }

    // ------------------------------ отдых в скользящем окне (правило 33)

    private fun rollPlan(days: Int, restMin: Int) = LogicPlan(
        ruleIds = listOf("R-33"), constraintId = "logic test0004",
        form = LogicForms.FORM_REST_IN_ROLLING_WINDOW, subject = "rest", relation = "min",
        bound = restMin, boundParam = "weekly_rest_required", baseScope = null,
        condBound = days, condParam = "rolling_window_days", enforce = "required",
        weight = 1, reading = LogicForms.READING_ROLLING, shapeSha = "x")

    @Test
    fun `окно без 36 часов отдыха — штраф за каждое нарушенное окно`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        // 10 суток подряд по рейсу 06:00–20:00: ночного отдыха 10 часов,
        // непрерывных 36 часов нет нигде. K-15 (непрерывное покрытие):
        // полностью известные окончания окон [8-го 00:00, 11-го 00:00] —
        // две точки и один открытый промежуток, все три нарушены.
        for (d in 1..10) {
            val f = flight("F$d", home, t(d, 6), out, t(d, 20))
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        check(rollPlan(7, 2160), *facts.toTypedArray()).penalizesBy(3)
        // Суточная форма — legacy-диагностика: окна со стартами 1..4 — 4.
        assertEquals(4L, LogicForms.violatedWindows(
            facts.filterIsInstance<FlightAssignment>(), 7, 2160))
    }

    @Test
    fun `выходные внутри окна дают отдых — окно выполнено`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        // Работа 1..3 и 6..10: разрыв 3-го 20:00 — 6-го 06:00 = 58 ч >= 36 ч.
        for (d in (1..3) + (6..10)) {
            val f = flight("F$d", home, t(d, 6), out, t(d, 20))
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        check(rollPlan(7, 2160), *facts.toTypedArray()).penalizes(0)
        // При требовании 60 часов проходящих промежутков нет (58 < 60):
        // полностью известные окончания дают три нарушенные клетки (K-15).
        check(rollPlan(7, 3600), *facts.toTypedArray()).penalizesBy(3)
        // Суточная форма — legacy-диагностика: окна со стартами 1..4 — 4.
        assertEquals(4L, LogicForms.violatedWindows(
            facts.filterIsInstance<FlightAssignment>(), 7, 3600))
    }

    @Test
    fun `сотрудник без назначений выполняет все окна — универсум не нужен`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in 1..10) {
            val f = flight("F$d", home, t(d, 6), out, t(d, 20))
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        // E2 не назначен вовсе: нарушений столько же, сколько без него
        // (сотрудник без назначений судится и выполняет все известные окна).
        check(rollPlan(7, 2160), *facts.toTypedArray()).penalizesBy(3)
    }

    @Test
    fun `показание rolling_rest_audit — expected = passed + violated + unknown, violated = штраф`() {
        // K-15-a: показание прогона по сотрудникам от той же непрерывной
        // проверки (горизонт horizonOf, нарезка смен провайдера), что у
        // ограничения: Σ violated равна штрафу (вес 1 за клетку); у
        // незанятого E2 без истории ранние окна неизвестны — unknown > 0,
        // violated = 0; expected = passed + violated + unknown у каждого.
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in 1..10) {
            val f = flight("F$d", home, t(d, 6), out, t(d, 20))
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        val plan = rollPlan(7, 2160)
        check(plan, *facts.toTypedArray()).penalizesBy(3)
        val problem = Fixtures.schedule(listOf(home, out), listOf(e1, e2),
            facts.filterIsInstance<FlightAssignment>())
        val slicer = FlightCrewSchedulingConstraintProvider()::duties
        val reading = LogicForms.rollingRestAuditReading(problem, listOf(plan), slicer)!!
        assertEquals(1, reading["version"])
        assertEquals("critical_cell", reading["unit"])
        assertEquals(mapOf("start" to t(1, 0).toString(), "end" to t(11, 0).toString()), reading["horizon"])
        val c = (reading["constraints"] as List<*>).single() as Map<*, *>
        assertEquals(plan.constraintId, c["constraint"])
        assertEquals(7, c["windowDays"]); assertEquals(2160, c["restMin"])
        val employees = c["employees"] as Map<*, *>
        assertEquals(setOf("E1", "E2"), employees.keys)
        for (row in employees.values.map { it as Map<*, *> }) {
            assertEquals(row["expected"],
                (row["passed"] as Long) + (row["violated"] as Long) + (row["unknown"] as Long))
        }
        val totals = c["totals"] as Map<*, *>
        assertEquals(3L, totals["violated"])
        assertEquals(totals["expected"],
            (totals["passed"] as Long) + (totals["violated"] as Long) + (totals["unknown"] as Long))
        assertEquals(3L, (employees["E1"] as Map<*, *>)["violated"])
        assertEquals(0L, (employees["E2"] as Map<*, *>)["violated"])
        assertTrue((employees["E2"] as Map<*, *>)["unknown"] as Long > 0)
        // Планов формы нет — показания нет; без горизонта — пометка.
        assertEquals(null, LogicForms.rollingRestAuditReading(problem, emptyList(), slicer))
        val empty = LogicForms.rollingRestAuditReading(
            Fixtures.schedule(listOf(home), listOf(e2), emptyList()), listOf(plan), slicer)!!
        assertEquals(null, empty["horizon"])
        assertTrue((empty["note"] as String).contains("горизонта нет"))
    }

    @Test
    fun `fit counts employees while continuous critical cells depend on their roster`() {
        // K-15: единица универсума — сотрудник (критические клетки зависят
        // от его ростера); горизонт короче окна — не пустой универсум, а
        // неизвестные ранние окна.
        val e1 = employee("E1").apply { homeAirport = home }
        val sch = Fixtures.schedule(listOf(home, out), listOf(e1, employee("E2")),
            listOf(assignment("a", dayFlight(1, 1), e1),
                   assignment("b", dayFlight(2, 10), e1)))
        assertEquals(2, LogicForms.cells(sch, rollPlan(7, 2160)).first)
        val short = Fixtures.schedule(listOf(home, out), listOf(e1),
            listOf(assignment("a", dayFlight(1, 1), e1)))
        assertEquals(1, LogicForms.cells(short, rollPlan(7, 2160)).first)
    }

    @Test
    fun `declared horizon not assigned span controls cells and excludes next month spillover`() {
        // K-14/K-4 (стенд GPT): объявленное окно 01.03..01.04 при рейсе
        // 31.03 23:00 → 01.04 02:00 — горизонт март, апрель не задет.
        val e = employee("E1").apply { homeAirport = home }
        val edge = flight("spill", home, LocalDateTime.of(2026, 3, 31, 23, 0),
            out, LocalDateTime.of(2026, 4, 1, 2, 0))
        val schedule = Fixtures.schedule(listOf(home, out), listOf(e), listOf(assignment("s", edge, e)))
        val declared = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = "2026-03-01"; end_exclusive = "2026-04-01"; source = "package_manifest"
        }
        schedule.windowHorizon = declared
        assertEquals(Horizon(LocalDate.of(2026, 3, 1), LocalDate.of(2026, 3, 31)), LogicForms.horizonOf(schedule))
        assertEquals(1, LogicForms.cells(schedule, rollPlan(7, 2160)).first)
        assertEquals(1, LogicForms.cells(schedule, plan(8)).first)
        // Один рабочий день марта => 30 выходных; норма 31 — нехватка 1.
        check(plan(31), declared, e, edge, schedule.flightAssignments.single()).penalizesBy(1)
        // Без объявленного окна — единый fallback по ВЫЛЕТАМ: 31.03..31.03.
        schedule.windowHorizon = null
        assertEquals(Horizon(LocalDate.of(2026, 3, 31), LocalDate.of(2026, 3, 31)), LogicForms.horizonOf(schedule))
    }

    @Test
    fun `rolling rest includes idle days of declared horizon instead of employee assignment span`() {
        val e = employee("E1").apply { homeAirport = home }
        val f = flight("one", home, t(5, 6), out, t(5, 20))
        val declared = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = "2026-03-01"; end_exclusive = "2026-03-11"
        }
        check(rollPlan(7, 2160), declared, e, f, assignment("one", f, e)).penalizesBy(0)
    }

    @Test
    fun `continuous rest catches an open violation span between passing endpoints`() {
        val start = t(1, 0)
        val h = Horizon(start.toLocalDate(), start.toLocalDate(), start, start.plusMinutes(200))
        val audit = LogicForms.rollingRestAudit(listOf(
            RestInterval(start, start.plusMinutes(20)),
            RestInterval(start.plusMinutes(60), start.plusMinutes(120)),
            RestInterval(start.plusMinutes(160), start.plusMinutes(200))), 100, 40, h)
        // Отдыхи [20,60] и [120,160] дают проходящие окончания [60,120] и
        // [160,200]. Открытый промежуток (120,160) нарушен, хотя оба его
        // конца проходят — суточная сетка его не видит.
        assertEquals(RollingRestAudit(11, 8, 1, 2), audit)
    }

    @Test
    fun `continuous rest retains exact equality and sub-minute failures`() {
        val start = t(1, 0)
        val h = Horizon(start.toLocalDate(), start.toLocalDate(), start, start.plusMinutes(200))
        fun audit(restEnd: LocalDateTime) = LogicForms.rollingRestAudit(listOf(
            RestInterval(start, start.plusMinutes(20)),
            RestInterval(restEnd, start.plusMinutes(200))), 100, 40, h)
        assertEquals(2, audit(start.plusMinutes(60)).violatedCells)
        assertEquals(3, audit(start.plusMinutes(60).minusNanos(1)).violatedCells)
        // K-3: доли секунды и смещение сохраняются, смещение → UTC.
        val window = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = "2026-03-01T01:00:00.123+01:00"
            end_exclusive = "2026-03-02T00:00:00Z"
        }
        assertEquals(start.plusNanos(123_000_000), window.startAt())
        val e = withHistory(employee("exact"), "2026-03-01T00:00:00Z",
            "2026-02-28T23:00:00.123+01:00", null, null)
        assertEquals(LocalDateTime.parse("2026-02-28T22:00:00.123"), e.historyLastDutyEndAt())
        assertEquals(LocalDate.of(2026, 3, 1), e.historyAsOfDate())
        // Прежние форматы наборов — без зоны и без секунд — читаются как прежде.
        val plain = withHistory(employee("plain"), "2026-03-04T00:00", "2026-03-03 20:00:00", null, null)
        assertEquals(LocalDate.of(2026, 3, 4), plain.historyAsOfDate())
        assertEquals(LocalDateTime.of(2026, 3, 3, 20, 0), plain.historyLastDutyEndAt())
    }

    @Test
    fun `unknown early rolling windows remain unknown even for an idle employee`() {
        val start = t(4, 0)
        val h = Horizon(start.toLocalDate(), start.plusDays(1).toLocalDate(), start, start.plusDays(2))
        val withoutHistory = LogicForms.rollingRestAudit(emptyList(), 10080, 2160, h)
        assertTrue(withoutHistory.unknownCells > 0)
        assertEquals(0, withoutHistory.violatedCells)
        val matching = LogicForms.rollingRestAudit(emptyList(), 10080, 2160, h,
            start.minusDays(8), start)
        assertEquals(0, matching.unknownCells)
        assertEquals(0, matching.violatedCells)
        val stale = LogicForms.rollingRestAudit(emptyList(), 10080, 2160, h,
            start.minusDays(8), start.minusNanos(1))
        assertEquals(withoutHistory, stale)
        // Допущение нулевой истории — известный отдых от среза: окна
        // судятся, нарушений у незанятого нет.
        val assumed = LogicForms.rollingRestAudit(emptyList(), 10080, 2160, h,
            assumedFreeFrom = start.toLocalDate())
        assertEquals(0, assumed.unknownCells)
        assertEquals(0, assumed.violatedCells)
    }

    @Test
    fun `rolling rest is outside normative duties not an intra-duty sit`() {
        val e = employee("E1").apply { homeAirport = home }
        val a = assignment("a", flight("a", home, t(1, 0), out, t(1, 1)), e)
        val b = assignment("b", flight("b", out, t(1, 3), home, t(1, 4)), e)
        val h = Horizon(t(1, 0).toLocalDate(), t(1, 0).toLocalDate(), t(1, 0), t(1, 4))
        val ds = FlightCrewSchedulingConstraintProvider().duties(listOf(a, b))
        assertEquals(1, ds.size)
        val occupancy = ds.map { RestInterval(it.first().flight!!.departureUTCDateTime!!,
            it.last().flight!!.arrivalUTCDateTime!!) }
        // Двухчасовая стыковка — часть одной смены: в полностью известном
        // четырёхчасовом окне у конца горизонта нет 90 минут отдыха.
        assertEquals(1, LogicForms.rollingRestAudit(occupancy, 240, 90, h).violatedCells)
        // Пустой список назначений — нарезка пуста (K-9).
        assertTrue(FlightCrewSchedulingConstraintProvider().duties(emptyList()).isEmpty())
    }

    // ------------------------------------ отдых после продлённой смены

    @Test
    fun `после длинной смены короткий отдых — штраф за каждую минуту недобора`() {
        val e1 = employee("E1").apply { homeAirport = home }
        // Смена 1: 05:00–18:30 (размах 810 > 720). Отдых до следующей смены:
        // 18:30 -> 08:00 следующего дня = 810 мин (>= порога отдыха профиля
        // 480 — граница смен состоялась), но меньше требуемых 840 — недобор 30.
        val a1 = flight("A1", home, t(2, 5), out, t(2, 12))
        val a2 = flight("A2", out, t(2, 13), home, t(2, 18, 30))
        val b1 = flight("B1", home, t(3, 8), out, t(3, 10))
        check(restPlan(restMin = 840, longMin = 720), e1, a1, a2, b1,
            assignment("s1", a1, e1), assignment("s2", a2, e1), assignment("s3", b1, e1))
            .penalizesBy(30)
    }

    @Test
    fun `короткая смена — правило не судит её отдых`() {
        val e1 = employee("E1").apply { homeAirport = home }
        // Размах 300 мин <= 720: отдых 810 мин после неё не судится даже
        // при требуемых 840.
        val a1 = flight("A1", home, t(2, 5), out, t(2, 10))
        val b1 = flight("B1", out, t(2, 23, 30), home, t(3, 1))
        check(restPlan(restMin = 840, longMin = 720), e1, a1, b1,
            assignment("s1", a1, e1), assignment("s2", b1, e1)).penalizes(0)
    }

    @Test
    fun `отдых после последней смены горизонта не судится`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val a1 = flight("A1", home, t(2, 5), out, t(2, 12))
        val a2 = flight("A2", out, t(2, 13), home, t(2, 18, 30))
        check(restPlan(restMin = 840, longMin = 720), e1, a1, a2,
            assignment("s1", a1, e1), assignment("s2", a2, e1)).penalizes(0)
    }

    @Test
    fun `разрыв меньше порога отдыха — не граница смен, судят другие правила`() {
        val e1 = employee("E1").apply { homeAirport = home }
        // Разрыв 450 мин < порога отдыха профиля (480): смены сливаются, и
        // «отдыха после» здесь нет — размах судит правило размаха.
        val a1 = flight("A1", home, t(2, 5), out, t(2, 18, 30))
        val b1 = flight("B1", out, t(3, 2), home, t(3, 6))
        check(restPlan(restMin = 840, longMin = 720), e1, a1, b1,
            assignment("s1", a1, e1), assignment("s2", b1, e1)).penalizes(0)
    }

    // ---------------------------------------- рабочих дней подряд не более N

    @Test
    fun `серия сверх границы — штраф за каждый лишний день, разрывы делят серии`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        // Сутки 1..7 подряд, разрыв, сутки 10..13: серии 7 и 4.
        for (d in (1..7) + (10..13)) {
            val f = dayFlight(d, d)
            facts.add(f); facts.add(assignment("s$d", f, e1))
        }
        check(runsPlan(5), *facts.toTypedArray()).penalizesBy(2)
        check(runsPlan(3), *facts.toTypedArray()).penalizesBy(4 + 1)
        check(runsPlan(7), *facts.toTypedArray()).penalizes(0)
    }

    @Test
    fun `ровно одни сутки — серия длины один, а не двух`() {
        // Ловушка «+1», пойманная пробой: до дедупликации getCount() врёт,
        // после — getCount() == дни серии; граница 1 на одном дне — ноль.
        val e1 = employee("E1").apply { homeAirport = home }
        val f = dayFlight(1, 5)
        check(runsPlan(1), e1, f, assignment("a", f, e1)).penalizes(0)
    }

    @Test
    fun `две смены в одни сутки — один рабочий день`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val m1 = dayFlight(1, 5)
        val m2 = flight("F2", out, t(5, 15), home, t(5, 17))
        val next = dayFlight(3, 6)
        // Сутки 5 (дважды) и 6: серия 2; граница 1 -> нехватка 1, не 2.
        check(runsPlan(1), e1, m1, m2, next,
            assignment("a", m1, e1), assignment("b", m2, e1), assignment("c", next, e1))
            .penalizesBy(1)
    }

    @Test
    fun `рейс через полночь сшивает соседние сутки в одну серию`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val night = flight("FN", home, t(5, 22), out, LocalDateTime.of(2026, 3, 6, 2, 0))
        val after = dayFlight(2, 7)
        // Сутки 5, 6 (полночным рейсом) и 7: серия 3.
        check(runsPlan(2), e1, night, after,
            assignment("a", night, e1), assignment("b", after, e1)).penalizesBy(1)
    }

    @Test
    fun `область серий — код базы`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = airport("SVG") }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in 1..3) {
            val f = dayFlight(d, d)
            facts.add(f)
            facts.add(assignment("p$d", f, e1))
            val g = flight("G$d", out, t(d, 14), home, t(d, 16))
            facts.add(g)
            facts.add(assignment("q$d", g, e2))
        }
        check(runsPlan(2), *facts.toTypedArray()).penalizesBy(2)
        check(runsPlan(2, base = "PLY"), *facts.toTypedArray()).penalizesBy(1)
        val (n, measure, _) = LogicForms.cells(
            Fixtures.schedule(listOf(home, out), listOf(e1, e2), emptyList()), runsPlan(2, base = "PLY"))
        assertEquals(1, n)
        assertTrue(measure.startsWith("сотрудников области"), measure)
    }

    @Test
    fun `окно withRunModel реентрантно — вложенная сборка не стирает внешнюю`() {
        val p = plan(8)
        RunThresholds.withRunModel(emptyMap(), listOf(p)) {
            assertEquals(listOf(p), RunThresholds.plans())
            RunThresholds.withRunModel(emptyMap(), emptyList()) {
                assertTrue(RunThresholds.plans().isEmpty())
            }
            assertEquals(listOf(p), RunThresholds.plans())
        }
        assertTrue(RunThresholds.plans().isEmpty())
    }

    @Test
    fun `фабрика регистрирует порождённое имя только внутри окна`() {
        val cfg = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        val inside = RunThresholds.withRunModel(emptyMap(), listOf(plan(8))) {
            LogicCompiler.registeredIds(SolverFactory.create<FlightCrewSchedule>(cfg))
        }
        assertTrue("logic test0001" in inside, inside.toString())
        val outside = LogicCompiler.registeredIds(SolverFactory.create<FlightCrewSchedule>(cfg))
        assertFalse(outside.any { it.startsWith("logic ") }, outside.toString())
        assertEquals(inside.size, outside.size + 1)
    }

    @Test
    fun `клетки fit — сотрудники области умножить на полные месяцы`() {
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = airport("SVG") }
        val first = dayFlight(1, 1); val last = flight("FL", home, t(31, 8), out, LocalDateTime.of(2026, 4, 3, 10, 0))
        val sch = Fixtures.schedule(listOf(home, out), listOf(e1, e2),
            listOf(assignment("a", first, e1), assignment("b", last, e1)))
        val (n, _, partial) = LogicForms.cells(sch, plan(8))
        assertEquals(2, n)
        // K-14: fallback-горизонт — по ВЫЛЕТАМ (01.03..31.03): прилёт 03.04
        // апрель в универсум не вносит, неполных месяцев нет.
        assertEquals(emptyList<String>(), partial)
        assertEquals(1, LogicForms.cells(sch, plan(8, base = "PLY")).first)
        // Объявленное окно до 03.04 включительно — апрель неполный и назван.
        sch.windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = "2026-03-01"; end_exclusive = "2026-04-04"
        }
        val declared = LogicForms.cells(sch, plan(8))
        assertEquals(2, declared.first)
        assertEquals(listOf("2026-04"), declared.third)
        sch.windowHorizon = null
        val short = Fixtures.schedule(listOf(home, out), listOf(e1),
            listOf(assignment("a", dayFlight(5, 5), e1)))
        assertEquals(0, LogicForms.cells(short, plan(8)).first)
    }

    // ------------------------------ история сотрудника (ревизия 1.17, решение владельца 05.09.2026)

    private fun withHistory(e: Employee, asOf: String, lastEnd: String?,
                            nights: Long?, daysWorked: Long?): Employee = e.apply {
        historyAsOf = asOf
        history = org.acme.flighcrewscheduling.domain.EmployeeHistory().apply {
            historyLastDutyEnd = lastEnd
            historyConsecutiveNightDuties = nights
            historyMonthDaysWorked = daysWorked
        }
    }

    @Test
    fun `серия ночных из истории продолжается первой ночной сменой горизонта`() {
        // Перенос 2 + одна ночная смена горизонта = серия 3 при границе 2.
        val e1 = withHistory(employee("E1").apply { homeAirport = home },
            "2026-03-02T00:00:00", "2026-03-01T01:00:00", 2L, null)
        val f = nightDuty(2, 2)
        check(nightPlan(2), e1, f, assignment("n2", f, e1)).penalizesBy(1)
        // Первая смена горизонта дневная — перенос не действует.
        val e2 = withHistory(employee("E2").apply { homeAirport = home },
            "2026-03-02T00:00:00", "2026-03-01T01:00:00", 2L, null)
        val day = flight("D1", home, t(2, 10), out, t(2, 12))
        val n3 = nightDuty(3, 4)
        check(nightPlan(2), e2, day, n3, assignment("a", day, e2), assignment("b", n3, e2)).penalizes(0)
        // Без истории — прежнее прочтение: одна ночная — нарушений нет.
        val e3 = employee("E3").apply { homeAirport = home }
        val g = nightDuty(9, 2)
        check(nightPlan(2), e3, g, assignment("c", g, e3)).penalizes(0)
    }

    @Test
    fun `конец последней смены истории открывает окна до горизонта`() {
        fun facts(e: Employee, days: IntRange, tag: String): Array<Any> {
            val fs = ArrayList<Any>(listOf(e))
            for (d in days) {
                val f = flight("$tag$d", home, t(d, 6), out, t(d, 20))
                fs.add(f); fs.add(assignment("$tag-a$d", f, e))
            }
            return fs.toTypedArray()
        }
        // K-15 (непрерывное покрытие): без истории работа 3..10 — известное
        // окончание [10-го 00:00, 11-го 00:00]: две точки и один промежуток.
        check(rollPlan(7, 2160), *facts(employee("E1").apply { homeAirport = home }, 3..10, "F")).penalizesBy(3)
        // История: последняя смена кончилась 1-го в 20:00, срез = началу
        // горизонта (3-го 00:00) — известный отдых до 3-го 06:00 (34 ч < 36);
        // полнота начинается с 8-го 20:00 — три нарушенные клетки.
        val e2 = withHistory(employee("E2").apply { homeAirport = home },
            "2026-03-03T00:00:00", "2026-03-01T20:00:00", null, null)
        check(rollPlan(7, 2160), *facts(e2, 3..10, "G")).penalizesBy(3)
        // Та же история, работа 4..10: горизонт fallback начинается 4-го, срез
        // 3-го с ним НЕ совпадает — снимок конца смены не выдумывает
        // содержимое суток между ними; как без истории — одна клетка.
        val e3 = withHistory(employee("E3").apply { homeAirport = home },
            "2026-03-03T00:00:00", "2026-03-01T20:00:00", null, null)
        check(rollPlan(7, 2160), *facts(e3, 4..10, "H")).penalizesBy(1)
        check(rollPlan(7, 2160), *facts(employee("E4").apply { homeAirport = home }, 4..10, "K")).penalizesBy(1)
        // Суточная форма — legacy-диагностика (прежние прочтения main):
        // без истории старты 3..4 — 2; с историей старты со 2-го — 3;
        // конец последней смены ровно в полночь — те сутки уже полные.
        assertEquals(2L, LogicForms.violatedWindows(
            facts(employee("E1").apply { homeAirport = home }, 3..10, "L").filterIsInstance<FlightAssignment>(), 7, 2160))
        assertEquals(3L, LogicForms.violatedWindows(
            facts(e2, 3..10, "G2").filterIsInstance<FlightAssignment>(), 7, 2160, e2.historyLastDutyEndAt()))
        assertEquals(2L, LogicForms.violatedWindows(
            facts(e3, 4..10, "H2").filterIsInstance<FlightAssignment>(), 7, 2160, e3.historyLastDutyEndAt()))
        assertEquals(3L, LogicForms.violatedWindows(
            (facts(e2, 3..10, "M").filterIsInstance<FlightAssignment>()), 7, 2160,
            LocalDateTime.of(2026, 3, 2, 0, 0)))
    }

    @Test
    fun `допущение нулевой истории открывает окна с среза, как у сертификатора`() {
        // Простой Н0 на семи сутках (05.09.2026, H19): первый рейс 5-го, окно
        // 4..10 содержит известный отдых 4-го 00:00 — 5-го 06:00 (30 ч < 36)
        // и остальные промежутки по 10 ч — нарушено. Без допущения решатель
        // начинал окна с первого рейса сотрудника и окна 4..10 не видел, а
        // сертификат его краснил (continue_zero: lo = первый вылет − 6).
        fun facts(e: Employee, days: IntRange, tag: String): Array<Any> {
            val fs = ArrayList<Any>(listOf(e))
            for (d in days) {
                val f = flight("$tag$d", home, t(d, 6), out, t(d, 20))
                fs.add(f); fs.add(assignment("$tag-a$d", f, e))
            }
            return fs.toTypedArray()
        }
        val assumed = employee("E5").apply {
            homeAirport = home
            history = org.acme.flighcrewscheduling.domain.EmployeeHistory()
            historyAsOf = "2026-03-04T00:00"
            historyAssumedZero = true
        }
        assertEquals(LocalDate.of(2026, 3, 4), assumed.assumedFreeFrom())
        // K-15 (непрерывное покрытие, §3.10.3: то же явное основание и та же
        // область у решателя и сертификатора): известный отдых от среза
        // открывает окна с концом до 9-го 18:00 как проходящие; полностью
        // известные окончания (9-го 18:00, 11-го 00:00] — открытый промежуток
        // и конечная точка — нарушены: две клетки.
        check(rollPlan(7, 2160), *facts(assumed, 5..10, "N")).penalizesBy(2)
        // Суточная форма — legacy-диагностика: окна раньше среза (27.02..3.03
        // … 3..9) содержат отдых до первого рейса не короче 54 ч — выполнены;
        // считается ровно одно окно 4..10.
        assertEquals(1L, LogicForms.violatedWindows(
            facts(assumed, 5..10, "P").filterIsInstance<FlightAssignment>(), 7, 2160,
            null, LocalDate.of(2026, 3, 4)))
        // Без допущения — ранние окна неизвестны и не штрафуются (ноль — не
        // доказательство допустимости); горизонт 5..10 короче окна: полностью
        // известных окончаний нет.
        check(rollPlan(7, 2160), *facts(employee("E6").apply { homeAirport = home }, 5..10, "Q")).penalizesBy(0)
        // Запись истории с концом последней смены имеет приоритет над допущением.
        val e7 = withHistory(employee("E7").apply { homeAirport = home },
            "2026-03-04T00:00:00", "2026-03-03T20:00:00", null, null).apply { historyAssumedZero = true }
        assertEquals(null, e7.assumedFreeFrom())
    }

    @Test
    fun `месяц среза истории судится с переносом отработанных суток`() {
        // Горизонт 10..31 марта — полных месяцев нет. С историей (срез 10.03,
        // отработано 8 суток) и занятостью 10..31: занято 22 + 8 = 30,
        // выходной 1 при норме 8 — нехватка 7. Без истории месяц вне оценки.
        val e1 = withHistory(employee("E1").apply { homeAirport = home },
            "2026-03-10T00:00:00", null, null, 8L)
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in 10..31) {
            val f = flight("F$d", home, t(d, 10), out, t(d, 12))
            facts.add(f); facts.add(assignment("a$d", f, e1))
            val g = flight("G$d", home, t(d, 10), out, t(d, 12))
            facts.add(g); facts.add(assignment("b$d", g, e2))
        }
        check(plan(8), *facts.toTypedArray()).penalizesBy(7)
        // Сотрудник с историей без назначений: занято 0 + 8, выходных 23 —
        // при норме 24 нехватка 1; сотрудник без истории (E2, занят 22 суток)
        // не судится.
        val e3 = withHistory(employee("E3").apply { homeAirport = home },
            "2026-03-10T00:00:00", null, null, 8L)
        val facts2 = ArrayList<Any>(listOf(e2, e3))
        for (d in 10..31) {
            val g = flight("G$d", home, t(d, 10), out, t(d, 12))
            facts2.add(g); facts2.add(assignment("b$d", g, e2))
        }
        check(plan(24), *facts2.toTypedArray()).penalizesBy(1)
        // Клетки fit: месяц среза — клетка только у сотрудника с историей.
        val sch = Fixtures.schedule(listOf(home, out), listOf(e1, e2),
            listOf(assignment("x", dayFlight(1, 10), e1), assignment("y", dayFlight(2, 31), e2)))
        val cells = LogicForms.cells(sch, plan(8))
        assertEquals(1, cells.first)
        assertTrue(cells.second.contains("месяц среза истории у 1"))
        assertEquals(listOf("2026-03"), cells.third)
    }

    // ------------------------------ счётчики скользящих окон (решение владельца 05.09.2026)

    private fun offWinPlan(days: Int, minOff: Int) = LogicPlan(
        ruleIds = listOf("R-OFF7"), constraintId = "logic test0010",
        form = LogicForms.FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN, subject = "day_off", relation = "min",
        bound = minOff, boundParam = "min_days_off_window", baseScope = null,
        condBound = days, condParam = "window_days", enforce = "required",
        weight = 1, reading = LogicForms.READING_DAYS_OFF_ROLLING, shapeSha = "x")

    private fun nightWinPlan(days: Int, maxNights: Int) = LogicPlan(
        ruleIds = listOf("R-N3"), constraintId = "logic test0011",
        form = LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX, subject = "duty", relation = "max",
        bound = maxNights, boundParam = "max_nights_window", baseScope = null,
        condBound = days, condParam = "window_days",
        nightFrom = 22 * 60, nightTo = 6 * 60, enforce = "required",
        weight = 1, reading = LogicForms.READING_NIGHT_ROLLING, shapeSha = "x")

    private fun withCalendar(e: Employee, asOf: String,
                             days: List<Triple<String, String, Boolean?>>): Employee = e.apply {
        historyAsOf = asOf
        history = org.acme.flighcrewscheduling.domain.EmployeeHistory().apply {
            for ((date, kind, night) in days) {
                historyCalendar.add(org.acme.flighcrewscheduling.domain.HistoryDay().apply {
                    this.date = date; this.kind = kind; this.night = night
                })
            }
        }
    }

    @Test
    fun `выходных в окне не менее K — нехватка по окнам горизонта`() {
        // Горизонт 1..14 марта (рейсы E2 1-го и 14-го задают горизонт).
        // E1 работает 1..7 и 9..14: окно 7 суток с K=2 — окна 1..7 (0 выходных,
        // нехватка 2), 2..8 (1, нехватка 1), 3..9 (1), 4..10 (1), 5..11 (1),
        // 6..12 (1), 7..13 (1), 8..14 (1) — итого 2 + 7 = 9.
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in (1..7) + (9..14)) {
            val f = flight("F$d", home, t(d, 10), out, t(d, 12))
            facts.add(f); facts.add(assignment("a$d", f, e1))
        }
        for (d in listOf(1, 14)) {
            val g = flight("G$d", home, t(d, 10), out, t(d, 12))
            facts.add(g); facts.add(assignment("b$d", g, e2))
        }
        check(offWinPlan(7, 2), *facts.toTypedArray()).penalizesBy(9)
        // K=1: нарушено только окно 1..7 (0 выходных) — нехватка 1.
        check(offWinPlan(7, 1), *facts.toTypedArray()).penalizesBy(1)
        // Сотрудник с редкими рейсами (E2) окна выполняет.
        check(offWinPlan(7, 2), e2, *facts.filter { it !is FlightAssignment || (it as FlightAssignment).employee === e2 }
            .filter { it !== e1 }.toTypedArray()).penalizes(0)
    }

    @Test
    fun `календарь истории открывает окна выходных через границу горизонта`() {
        // Горизонт 1..7 марта, E1 работает все 7 дней: без календаря — одно окно
        // 1..7, нехватка 2 при K=2.
        val e1 = employee("E1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1))
        for (d in 1..7) {
            val f = flight("F$d", home, t(d, 10), out, t(d, 12))
            facts.add(f); facts.add(assignment("a$d", f, e1))
        }
        check(offWinPlan(7, 2), *facts.toTypedArray()).penalizesBy(2)
        // Календарь: 23.02..28.02 — 26-го и 27-го выходные, остальное работа.
        // Окна со стартом 23.02..1.03: 23..1 (2 выходных, чисто), 24..2 (2),
        // 25..3 (2), 26..4 (2), 27..5 (1 — нехватка 1), 28..6 (0 — нехватка 2),
        // 1..7 (0 — нехватка 2): итого 5.
        val cal = listOf("2026-02-23" to "work", "2026-02-24" to "work", "2026-02-25" to "work",
                         "2026-02-26" to "day_off", "2026-02-27" to "day_off", "2026-02-28" to "work")
            .map { Triple(it.first, it.second, false) }
        val e2 = withCalendar(employee("E2").apply { homeAirport = home }, "2026-03-01T00:00:00", cal)
        val facts2 = ArrayList<Any>(listOf(e2))
        for (d in 1..7) {
            val f = flight("G$d", home, t(d, 10), out, t(d, 12))
            facts2.add(f); facts2.add(assignment("b$d", f, e2))
        }
        check(offWinPlan(7, 2), *facts2.toTypedArray()).penalizesBy(5)
        // Сутки без записи (28.02 unknown) — окна с ними не судятся: остаётся
        // только окно 1..7 (нехватка 2).
        val cal3 = cal.dropLast(1) + Triple("2026-02-28", "unknown", null)
        val e3 = withCalendar(employee("E3").apply { homeAirport = home }, "2026-03-01T00:00:00", cal3)
        val facts3 = ArrayList<Any>(listOf(e3))
        for (d in 1..7) {
            val f = flight("H$d", home, t(d, 10), out, t(d, 12))
            facts3.add(f); facts3.add(assignment("c$d", f, e3))
        }
        check(offWinPlan(7, 2), *facts3.toTypedArray()).penalizesBy(2)
    }

    @Test
    fun `ночных смен в окне не более K — по суткам начала смены и календарю`() {
        // Ночные смены 2, 3, 4 марта (23:00 → 01:00): окно 3 суток, K=2 —
        // окна 1..3 (2), 2..4 (3 — превышение 1), 3..5 (2), 4..6 (1) при
        // горизонте 1..6 (рейс E2 1-го и 6-го днём).
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e1, e2))
        for (d in listOf(2, 3, 4)) {
            val f = nightDuty(d, d)
            facts.add(f); facts.add(assignment("n$d", f, e1))
        }
        for (d in listOf(1, 6)) {
            val g = flight("D$d", home, t(d, 10), out, t(d, 12))
            facts.add(g); facts.add(assignment("d$d", g, e2))
        }
        check(nightWinPlan(3, 2), *facts.toTypedArray()).penalizesBy(1)
        check(nightWinPlan(3, 3), *facts.toTypedArray()).penalizes(0)
        // Календарь: 27.02 и 28.02 — ночные рабочие сутки. Окна через границу:
        // 27..1 (2), 28..2 (2), 1..3 (2), 2..4 (3 — превышение 1) → 1; при K=1
        // превышения: 27..1 (1), 28..2 (1), 1..3 (1), 2..4 (2), 3..5 (1), 4..6 (0) → 6.
        val cal = listOf(Triple("2026-02-27", "work", true), Triple("2026-02-28", "work", true))
        val e3 = withCalendar(employee("E3").apply { homeAirport = home }, "2026-03-01T00:00:00", cal)
        val facts3 = ArrayList<Any>(listOf(e3, e2))
        for (d in listOf(2, 3, 4)) {
            val f = nightDuty(d + 10, d)
            facts3.add(f); facts3.add(assignment("m$d", f, e3))
        }
        for (d in listOf(1, 6)) {
            val g = flight("E$d", home, t(d, 10), out, t(d, 12))
            facts3.add(g); facts3.add(assignment("e$d", g, e2))
        }
        check(nightWinPlan(3, 2), *facts3.toTypedArray()).penalizesBy(1)
        check(nightWinPlan(3, 1), *facts3.toTypedArray()).penalizesBy(6)
        // Клетки fit — сотрудники × старты окон горизонта (6 суток, окно 3: 4 старта).
        val sch = Fixtures.schedule(listOf(home, out), listOf(e1, e2),
            listOf(assignment("x", dayFlight(1, 1), e1), assignment("y", dayFlight(2, 6), e2)))
        assertEquals(8, LogicForms.cells(sch, nightWinPlan(3, 2)).first)
        assertEquals(8, LogicForms.cells(sch, offWinPlan(3, 1)).first)
    }
}
