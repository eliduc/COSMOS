package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.factory.MoveListFactoryConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.ChangeMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.PillarChangeMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.PillarSwapMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.RuinRecreateMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.SwapMoveSelectorConfig
import ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig
import ai.timefold.solver.core.config.localsearch.decider.acceptor.AcceptorType
import ai.timefold.solver.core.config.localsearch.decider.acceptor.LocalSearchAcceptorConfig

/**
 * Крупные ходы локального поиска (решение владельца 2026-08-25, «всё
 * сразу»): плато полного набора держит непрерывность маршрута, а ходы
 * по умолчанию двигают ОДНО место — переставить цепочку сотрудника
 * целиком поиск не умеет и мёрзнет. Опора (pillar) Timefold — все места
 * с одним значением переменной, то есть вся цепочка человека: ходы
 * опоры переносят и меняют цепочки как единое целое; «разрушить и
 * собрать» (ruin-recreate) выбивает кусок и достраивает конструктивом.
 *
 * Включение — ТОЛЬКО параметром прогона (moves=pillar | pillar-rr):
 * умолчание пустое, прежние прогоны байт-идентичны. Неизвестное имя
 * отвергается словами.
 */
object LargeMoves {

    const val LATE_ACCEPTANCE_SIZE = 400

    /**
     * Ходы по цепочкам (план 05.09.2026, шаг 4): moves=chain-seed
     * начинает с начального решения по цепочкам ([ChainSeeding]) и идёт
     * без конструктива — поиск стартует с целых цепочек.
     */
    fun seedsChains(moves: String?): Boolean {
        val m = moves?.trim()?.lowercase().orEmpty()
        return m == "chain-seed"
    }

    /** Режим цепочек (chain | chain-seed): гарантии K-30 действуют на прогон. */
    fun chainMode(moves: String?): Boolean {
        val m = moves?.trim()?.lowercase().orEmpty()
        return m == "chain" || m == "chain-seed"
    }

    /**
     * K-30-b: гарантии цепочек — на ВСЕ активные семейства ходов режима:
     * штатные Change/Swap идут через фильтры [ChainSeatChangeFilter] /
     * [ChainSeatSwapFilter] (место цепочки одиночным ходом не ходит,
     * свободное место судится пригодностью и соседством по порогам
     * прогона); цепочки ходят только целиком через [ChainMoveFactory].
     * K-FIX-R1 (ревью GPT-029): семейства «разрушить и собрать»
     * (ruin-recreate) в режимах цепочек НЕТ — фильтр отбора видит ход до
     * исполнения, а достройка выбитых мест идёт вложенным конструктивом
     * внутри хода: пригодность получателя и соседство достроенных мест
     * (та же гарантия, что у changeOk/swapOk) фильтру недоступны, и
     * защита одних мест с меткой не защищает их соседей (выбитый мост без
     * метки между двумя местами цепочки одного человека рвёт станции).
     * Имена chain-rr / chain-seed-rr отвергаются словами.
     */
    private fun chainUnion() = listOf(
        ChangeMoveSelectorConfig().withFilterClass(ChainSeatChangeFilter::class.java),
        SwapMoveSelectorConfig().withFilterClass(ChainSeatSwapFilter::class.java),
        MoveListFactoryConfig().withMoveListFactoryClass(ChainMoveFactory::class.java))

    /** Фаза поиска с крупными ходами; null — умолчание (без правок). */
    fun lsPhase(moves: String?): LocalSearchPhaseConfig? {
        val m = moves?.trim()?.lowercase().orEmpty()
        if (m.isEmpty()) return null
        val union = UnionMoveSelectorConfig().apply {
            moveSelectorList = when (m) {
                "pillar" -> listOf(
                    ChangeMoveSelectorConfig(), SwapMoveSelectorConfig(),
                    PillarChangeMoveSelectorConfig(), PillarSwapMoveSelectorConfig())
                "pillar-rr" -> listOf(
                    ChangeMoveSelectorConfig(), SwapMoveSelectorConfig(),
                    PillarChangeMoveSelectorConfig(), PillarSwapMoveSelectorConfig(),
                    RuinRecreateMoveSelectorConfig())
                // Шаг 4 плана 05.09.2026: единица хода — цепочка пейринга
                // (ChainChangeMove / ChainSwapMove); одиночные ходы
                // остаются для мест без метки и для доводки. chain —
                // после конструктива; chain-seed — от начального решения
                // по цепочкам (ChainSeeding) без конструктива (первый живой
                // замер 05.09: раздача без учёта правил стартует с −36048
                // жёстких против −589 у конструктива за 2,7 с).
                "chain", "chain-seed" -> chainUnion()
                "chain-rr", "chain-seed-rr" -> throw TaskModeRefused(
                    "набор ходов moves=" + m + " отключён: семейство «разрушить и собрать» " +
                    "(ruin-recreate) в режимах цепочек не даёт гарантий K-30 — достройка выбитых " +
                    "мест идёт вложенным конструктивом, невидимым фильтру отбора, и пригодность " +
                    "получателя и соседство достроенных мест судить нельзя; используйте " +
                    "moves=chain либо chain-seed")
                else -> throw TaskModeRefused(
                    "неизвестный набор ходов moves=" + m +
                    "; известные: pillar, pillar-rr, chain, chain-seed (пусто — умолчание)")
            }
        }
        return LocalSearchPhaseConfig().apply {
            moveSelectorConfig = union
            acceptorConfig = LocalSearchAcceptorConfig().apply {
                acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                lateAcceptanceSize = LATE_ACCEPTANCE_SIZE
            }
        }
    }
}
