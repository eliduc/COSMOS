package org.acme.flighcrewscheduling.solver

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * Крупные ходы поиска: включение только параметром (умолчание — null,
 * прежние прогоны байт-идентичны), состав наборов закрытым перечнем,
 * неизвестное имя — отказ словами.
 */
class LargeMovesTest {

    @Test
    fun `умолчание пусто — фаза не строится`() {
        assertNull(LargeMoves.lsPhase(null))
        assertNull(LargeMoves.lsPhase(""))
        assertNull(LargeMoves.lsPhase("  "))
    }

    @Test
    fun `pillar — четыре хода, pillar-rr — пять`() {
        val pillar = LargeMoves.lsPhase("pillar")!!
        val union = pillar.moveSelectorConfig
            as ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
        assertEquals(4, union.moveSelectorList!!.size)
        val rr = LargeMoves.lsPhase("PILLAR-RR")!!
        val union2 = rr.moveSelectorConfig
            as ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
        assertEquals(5, union2.moveSelectorList!!.size)
    }

    @Test
    fun `неизвестный набор ходов — отказ словами`() {
        val e = assertThrows<TaskModeRefused> { LargeMoves.lsPhase("чудо-ходы") }
        assertTrue(e.message!!.contains("неизвестный набор ходов"), e.message)
        assertTrue(e.message!!.contains("pillar"), e.message)
    }
}
