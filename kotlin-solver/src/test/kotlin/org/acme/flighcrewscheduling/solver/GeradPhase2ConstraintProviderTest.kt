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

/** GERAD phase 2 (personalization) profile: composed from
 *  GeradConstraintProvider.coreConstraints — i.e. WITHOUT the hard
 *  unassigned rule — so the thesis' SOFT 10000/slot uncovered cost stays
 *  live (audit 2026-07-12). */
class GeradPhase2ConstraintProviderTest {

    private val verifier: ConstraintVerifier<GeradPhase2ConstraintProvider, FlightCrewSchedule> =
        ConstraintVerifier.build(
            GeradPhase2ConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    @Test
    fun `unassigned slot is soft uncovered, not hard`() {
        val a = airport("AAA")
        val b = airport("BBB")
        val fa = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), null)
        val solution = schedule(listOf(a, b), listOf(employee("E1")), listOf(fa))
        // Exactly one match: "G2 Uncovered flight" at 10000 soft. Hard MUST
        // stay 0 — the composition deliberately excludes "G Unassigned slot".
        verifier.verifyThat()
            .givenSolution(solution)
            .scores(HardSoftScore.of(0, -10_000))
    }
}
