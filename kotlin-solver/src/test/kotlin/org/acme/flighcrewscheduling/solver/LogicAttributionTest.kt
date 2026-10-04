package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * Реестр отнесения (ступень 3) против ФАКТИЧЕСКОГО перечня имён, которые
 * регистрирует профиль по умолчанию.
 *
 * Зачем: порождать из logic то, что уже считает существующее ограничение, —
 * двойной счёт одного нарушения. Защита держится на реестре, а реестр
 * устаревает молча (свежесть допуска стала 26-м ограничением за одну
 * ночь). Поэтому перечень берётся у построенной фабрики — от библиотеки,
 * не от документа, — и тест краснеет на любом имени, которое человек не
 * отнёс ни к исполнителю, ни к «не относится» с причиной.
 */
class LogicAttributionTest {

    private fun factory(): SolverFactory<FlightCrewSchedule> =
        SolverFactory.create(SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java))

    @Test
    fun `каждое имя профиля отнесено человеком — либо исполнитель, либо причина`() {
        val registered = LogicCompiler.registeredIds(factory())
        assertTrue(registered.size >= 26, "профиль регистрирует " + registered.size + " имён")
        val known = LogicAttribution.allKnownIds()
        val orphans = registered.filter { it !in known }
        assertTrue(orphans.isEmpty(),
            "имена профиля без отнесения (новое ограничение — отнести ДО первого logic): " + orphans)
    }

    @Test
    fun `реестр не держит мёртвых имён`() {
        val registered = LogicCompiler.registeredIds(factory()).toSet()
        val stale = LogicAttribution.allKnownIds().filter { it !in registered }
        assertTrue(stale.isEmpty(), "в реестре имена, которых профиль не регистрирует: " + stale)
    }

    @Test
    fun `имена от фабрики — идентификаторы, а не ярлыки отчёта`() {
        // hardWeights() — ярлыки отчёта («… (per minute)»), не идентификаторы;
        // реестр обязан сверяться с тем, что зарегистрировала библиотека.
        val registered = LogicCompiler.registeredIds(factory()).toSet()
        assertTrue("Flight conflict" in registered)
        assertTrue("Flight conflict (per hour of overlap)" !in registered)
        assertTrue("Type rating currency" in registered)
    }

    @Test
    fun `ручка ведёт к своему исполнителю`() {
        assertEquals(listOf("Max duty period (rest-segmented)"), LogicAttribution.executorOf("max_duty_span_min"))
        assertEquals(listOf("Reserve crews"), LogicAttribution.executorOf("reserve_crews_min[PLY]"))
        assertEquals(listOf("Rest between duty periods"), LogicAttribution.executorOf("min_rest_outstation_min"))
        assertTrue(LogicAttribution.executorOf("нет такой ручки").isEmpty())
    }

    @Test
    fun `порождённое имя проходит выражение Timefold и не зависит от кириллицы правила`() {
        val id = LogicCompiler.constraintIdFor("R-ТЕСТ-правило-6")
        assertTrue(LogicDictionaries.CONSTRAINT_ID.matches(id), id)
        assertTrue(id.startsWith("logic "))
        assertEquals(id, LogicCompiler.constraintIdFor("R-ТЕСТ-правило-6"))
        assertTrue(id != LogicCompiler.constraintIdFor("R-ТЕСТ-правило-7"))
    }
}
