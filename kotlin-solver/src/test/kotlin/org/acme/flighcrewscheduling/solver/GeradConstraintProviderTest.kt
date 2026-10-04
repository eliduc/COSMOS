package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.schedule
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Test

/** GERAD core legality profile. The constraint functions are private, so we
 *  verify through whole-solution scoring: the fixtures are built so that
 *  ONLY the constraint under test (plus, where noted, the waiting-cost
 *  soft term) can match. */
class GeradConstraintProviderTest {

    private val verifier: ConstraintVerifier<GeradConstraintProvider, FlightCrewSchedule> =
        ConstraintVerifier.build(
            GeradConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    @Test
    fun `unassigned slot is hard in the core profile`() {
        val a = airport("AAA")
        val b = airport("BBB")
        val fa = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), null)
        val solution = schedule(listOf(a, b), listOf(employee("E1")), listOf(fa))
        // Only "G Unassigned slot" (weight 25 hard) matches: every other
        // core constraint starts from forEach(), which skips unassigned.
        verifier.verifyThat()
            .givenSolution(solution)
            .scores(HardSoftScore.ofHard(-25))
    }

    @Test
    fun `a 25-min sit violates min sit with the default 30-min rule value`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        // Continuous rotation A->B->A with a 25-min connection at B: only
        // "G Min sit 60m" fires — RulesConfig default min_sit_min=30, so
        // the shortfall is 30-25=5 min at hard weight 5 -> -25 hard. The
        // gap is below min sit, so the soft "G Waiting" term (window
        // [min_sit, min_rest)) must NOT match.
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), e)
        val fa2 = assignment("fa2", flight("FL2", b, t(2, 12, 25), a, t(2, 14)), e)
        val solution = schedule(listOf(a, b), listOf(e), listOf(fa1, fa2))
        verifier.verifyThat()
            .givenSolution(solution)
            .scores(HardSoftScore.ofHard(-25))
    }

    @Test
    fun `teleport violates the continuity hard constraint`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        val c = airport("CCC")
        // Arrive at B, then depart from C 120 min later: "G Continuity"
        // fires once (weight 20 hard); the only other match is the soft
        // "G Waiting" sit-minutes proxy (120 min in [30m, 10h)).
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), e)
        val fa2 = assignment("fa2", flight("FL2", c, t(2, 14), a, t(2, 16)), e)
        val solution = schedule(listOf(a, b, c), listOf(e), listOf(fa1, fa2))
        verifier.verifyThat()
            .givenSolution(solution)
            .scores(HardSoftScore.of(-20, -120))
    }
}
