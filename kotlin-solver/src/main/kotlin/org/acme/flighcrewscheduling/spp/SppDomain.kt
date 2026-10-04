package org.acme.flighcrewscheduling.spp

import ai.timefold.solver.core.api.domain.common.PlanningId
import ai.timefold.solver.core.api.domain.entity.PlanningEntity
import ai.timefold.solver.core.api.domain.solution.PlanningEntityCollectionProperty
import ai.timefold.solver.core.api.domain.solution.PlanningScore
import ai.timefold.solver.core.api.domain.solution.PlanningSolution
import ai.timefold.solver.core.api.domain.solution.ProblemFactCollectionProperty
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeFactory
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeProvider
import ai.timefold.solver.core.api.domain.variable.PlanningVariable
import ai.timefold.solver.core.api.domain.valuerange.ValueRange
import ai.timefold.solver.core.api.score.HardSoftScore

/**
 * Minimal set-partitioning domain for the "can Timefold replace CP-SAT on
 * Stage 1?" experiment (OR-Library airline SPP instances). One entity per
 * candidate pairing (column) with a Boolean planning variable "selected";
 * exact cover is enforced as hard score (uncovered + over-covered rows),
 * total cost of selected columns is the soft score.
 */
class SppRow {
    var id: Int = 0
    constructor()
    constructor(id: Int) { this.id = id }
    override fun equals(other: Any?) = other is SppRow && other.id == id
    override fun hashCode() = id
}

@PlanningEntity
class SppColumn {
    @PlanningId
    var id: Int = 0
    var cost: Int = 0
    var rows: MutableList<Int> = mutableListOf()

    @Transient
    private var rowSet: HashSet<Int>? = null

    fun coversRow(rowId: Int): Boolean {
        var s = rowSet
        if (s == null) {
            s = HashSet(rows)
            rowSet = s
        }
        return rowId in s
    }

    @PlanningVariable(valueRangeProviderRefs = ["boolRange"])
    var selected: Boolean? = null

    constructor()
    override fun equals(other: Any?) = other is SppColumn && other.id == id
    override fun hashCode() = id
}

@PlanningSolution
class SppSolution {
    @ProblemFactCollectionProperty
    var rowFacts: List<SppRow> = emptyList()

    @PlanningEntityCollectionProperty
    var columns: List<SppColumn> = emptyList()

    @ValueRangeProvider(id = "boolRange")
    fun boolRange(): ValueRange<Boolean> =
        ValueRangeFactory.createBooleanValueRange()

    @PlanningScore
    var score: HardSoftScore? = null

    constructor()
}
