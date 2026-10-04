package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.impl.score.director.InnerScoreDirector
import ai.timefold.solver.core.impl.score.stream.common.AbstractConstraintStreamScoreDirectorFactory
import ai.timefold.solver.core.impl.solver.DefaultSolverFactory
import org.acme.flighcrewscheduling.domain.Airport
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.condition.EnabledIfSystemProperty
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import java.util.Random
import java.util.function.Function

/**
 * ЗАМЕР стоимости формы «отдых не менее W минут в каждом скользящем окне
 * N суток» (правило 33) — обещание разнесения ступени 3: число ДО
 * включения формы в приёмку.
 *
 * Не приёмочный тест: включается `-Dbench=true` и печатает числа; в
 * полном наборе пропускается. Методика — та же, что у эмпирического
 * разбора проекта (одиночные ходы через директор счёта): масштаб Лайта
 * (34 сотрудника, ~1000 рейсов, 32 суток), 20 000 ходов, сравнение
 * базового профиля и профиля с ограничением окна.
 *
 * Форма ограничения — НЕ универсум клеток (сотрудник × окно), а
 * по-сотрудничный счёт нарушенных окон: отсутствие назначений означает
 * ВЫПОЛНЕНИЕ («в окне есть отдых»), поэтому подстановка нуля не нужна, и
 * пересчёт хода локален для затронутого сотрудника.
 */
class LogicRollingBenchTest {

    private val rnd = Random(7)

    private fun schedule(): FlightCrewSchedule {
        val home = Airport("PLY"); val out = Airport("OUT")
        val employees = (1..34).map { i ->
            Employee("E$i").apply {
                name = "E$i"; skills = mutableListOf("Pilot"); typeRatings = mutableListOf("A320")
                homeAirport = if (i % 2 == 0) home else out
            }
        }
        val flights = ArrayList<Flight>()
        val assignments = ArrayList<FlightAssignment>()
        var n = 0
        for (day in 1..28) {
            for (k in 0 until 34) {
                n++
                val dep = LocalDateTime.of(2026, 3, day, 6 + (k % 14), (k * 7) % 60)
                val f = Flight("PL$n").apply {
                    departureAirport = if (k % 2 == 0) home else out
                    arrivalAirport = if (k % 2 == 0) out else home
                    departureUTCDateTime = dep
                    arrivalUTCDateTime = dep.plusMinutes(90)
                }
                flights.add(f)
                assignments.add(FlightAssignment("s$n", f, 0, "Pilot").apply {
                    employee = employees[(k + day) % employees.size]
                })
            }
        }
        return FlightCrewSchedule().apply {
            this.airports = mutableListOf(home, out)
            this.employees = employees.toMutableList()
            this.flights = flights
            this.flightAssignments = assignments
        }
    }

    /** Замер обязан мерить ТУ ЖЕ функцию, что уйдёт в форму. */
    private fun violatedWindows(list: List<FlightAssignment>, windowDays: Long, restMin: Long): Long =
        LogicForms.violatedWindows(list, windowDays, restMin)

    private fun rollingConstraint(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! },
                     ConstraintCollectors.toList())
            .filter { _, list -> violatedWindows(list, 7, 2160) > 0 }
            .penalize(HardSoftScore.ofHard(1L)) { _, list -> violatedWindows(list, 7, 2160) }
            .asConstraint("logic bench rolling")

    private class BenchProvider(val extra: Boolean) : ai.timefold.solver.core.api.score.stream.ConstraintProvider {
        override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> {
            val base = FlightCrewSchedulingConstraintProvider().defineConstraints(cf)
            if (!extra) return base
            return base + LogicRollingBenchTest().rollingConstraint(cf)
        }
    }

    private fun measure(extra: Boolean, moves: Int): Long {
        val cfg = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(if (extra) WithExtra::class.java else WithoutExtra::class.java)
        val factory = SolverFactory.create<FlightCrewSchedule>(cfg)
        val sdf = (factory as DefaultSolverFactory<FlightCrewSchedule>)
            .getScoreDirectorFactory<HardSoftScore>()
        @Suppress("UNCHECKED_CAST")
        val director = sdf.createScoreDirectorBuilder().build() as InnerScoreDirector<FlightCrewSchedule, HardSoftScore>
        director.use {
            val sch = schedule()
            director.setWorkingSolution(sch)
            director.calculateScore()
            val emps = sch.employees
            val t0 = System.nanoTime()
            for (i in 0 until moves) {
                val fa = sch.flightAssignments[rnd.nextInt(sch.flightAssignments.size)]
                val e = emps[rnd.nextInt(emps.size)]
                director.beforeVariableChanged(fa, "employee")
                fa.employee = e
                director.afterVariableChanged(fa, "employee")
                director.triggerVariableListeners()
                director.calculateScore()
            }
            return (System.nanoTime() - t0) / 1_000_000
        }
    }

    class WithoutExtra : ai.timefold.solver.core.api.score.stream.ConstraintProvider {
        override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> =
            FlightCrewSchedulingConstraintProvider().defineConstraints(cf)
    }

    class WithExtra : ai.timefold.solver.core.api.score.stream.ConstraintProvider {
        override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> =
            FlightCrewSchedulingConstraintProvider().defineConstraints(cf) +
                LogicRollingBenchTest().rollingConstraint(cf)
    }

    @Test
    @EnabledIfSystemProperty(named = "bench", matches = "true")
    fun `замер стоимости скользящего окна на масштабе Лайта`() {
        val moves = 20_000
        measure(extra = false, moves = 2_000)   // прогрев JIT
        val base = measure(extra = false, moves = moves)
        measure(extra = true, moves = 2_000)
        val with = measure(extra = true, moves = moves)
        println("BENCH_ROLLING base=${base}ms with=${with}ms ratio=%.3f moves=$moves".format(
            with.toDouble() / base))
    }
}
