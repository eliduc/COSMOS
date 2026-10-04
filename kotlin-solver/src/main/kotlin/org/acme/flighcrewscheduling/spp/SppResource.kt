package org.acme.flighcrewscheduling.spp

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.EnvironmentMode
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import jakarta.inject.Inject
import jakarta.inject.Named
import jakarta.ws.rs.Consumes
import jakarta.ws.rs.POST
import jakarta.ws.rs.Path
import jakarta.ws.rs.Produces
import jakarta.ws.rs.QueryParam
import jakarta.ws.rs.core.MediaType
import org.slf4j.LoggerFactory
import java.time.Duration

class SppColDto {
    var id: Int = 0
    var cost: Int = 0
    var rows: MutableList<Int> = mutableListOf()
}

class SppRequest {
    var rowCount: Int = 0
    var columns: MutableList<SppColDto> = mutableListOf()
}

/**
 * Timefold-on-SPP experiment endpoint: solve an OR-Library style
 * set-partitioning instance with Timefold local search and return the
 * best score found — directly comparable to CP-SAT's proven optimum on
 * the same instance ("can one solver do Stage 1's job?" thesis test).
 */
@Path("spp")
class SppResource @Inject constructor(
    // Injected only to satisfy the Quarkus named-solver wiring ("spp" name
    // must have a @Named injection point); the endpoint below builds its
    // own per-request SolverConfig from the same XML.
    @Suppress("unused")
    @Named("spp") private val sppSolverManager:
        ai.timefold.solver.core.api.solver.SolverManager<SppSolution>,
) {
    private val sppTemplateConfig: SolverConfig =
        SolverConfig.createFromXmlResource("sppSolverConfig.xml")

    private val log = LoggerFactory.getLogger(javaClass)

    @POST
    @Path("solve")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun solve(
        req: SppRequest,
        @QueryParam("spentLimitSeconds") spentLimitSeconds: Int?,
        @QueryParam("randomSeed") randomSeed: Long?,
    ): Map<String, Any?> {
        val seconds = (spentLimitSeconds ?: 60).coerceAtLeast(1).toLong()

        val solution = SppSolution().apply {
            rowFacts = (1..req.rowCount).map { SppRow(it) }
            columns = req.columns.map { d ->
                SppColumn().apply { id = d.id; cost = d.cost; rows = d.rows }
            }
        }

        val cfg = SolverConfig(sppTemplateConfig)
        cfg.environmentMode = EnvironmentMode.NO_ASSERT
        cfg.terminationConfig = TerminationConfig().apply {
            spentLimit = Duration.ofSeconds(seconds)
        }
        if (randomSeed != null) cfg.randomSeed = randomSeed

        log.info("SPP solve: rows={}, cols={}, seconds={}, seed={}",
            req.rowCount, req.columns.size, seconds, randomSeed ?: 0L)
        val solved = SolverFactory.create<SppSolution>(cfg).buildSolver()
            .solve(solution)

        val selected = solved.columns.filter { it.selected == true }
        val cost = selected.sumOf { it.cost }
        log.info("SPP solve done: score={}, cost={}, selected={}",
            solved.score, cost, selected.size)
        return mapOf(
            "score" to solved.score.toString(),
            "hard" to solved.score?.hardScore(),
            "cost" to cost,
            "selectedColumns" to selected.size,
        )
    }
}
