package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Test

/** Default-profile constraints (audit round 6, P6.1/P6.2). */
class FlightCrewSchedulingConstraintProviderTest {

    private val verifier: ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> =
        ConstraintVerifier.build(
            FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    // ------------------------------------------- Route continuity (P6.1)

    @Test
    fun `route continuity hard fires on a teleport`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        val c = airport("CCC")
        // Arrive at B at 12:00, then depart from C at 14:00 — impossible.
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), e)
        val fa2 = assignment("fa2", flight("FL2", c, t(2, 14), a, t(2, 16)), e)
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::routeContinuityHard)
            .given(fa1, fa2)
            .penalizes(1)
    }

    @Test
    fun `route continuity hard accepts a continuous routing`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        val c = airport("CCC")
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 10), b, t(2, 12)), e)
        val fa2 = assignment("fa2", flight("FL2", b, t(2, 14), c, t(2, 16)), e)
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::routeContinuityHard)
            .given(fa1, fa2)
            .penalizes(0)
    }

    // ------------------------------- Duty span across midnight (P6.2)

    @Test
    fun `duty span fires across midnight when the gap is not a rest`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        val c = airport("CCC")
        // 20:00-23:00 then 00:00-12:00 next day: 1h gap (< 8h rest) chains
        // both legs into ONE duty of span 16h = 960 min; cap is 840 min.
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 20), b, t(2, 23)), e)
        val fa2 = assignment("fa2", flight("FL2", b, t(3, 0), c, t(3, 12)), e)
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(fa1, fa2)
            .penalizesBy(960L - 840L)
    }

    @Test
    fun `duty span does not fire when the gap is a legal rest`() {
        val e = employee("E1")
        val a = airport("AAA")
        val b = airport("BBB")
        val c = airport("CCC")
        // 20:00-23:00 then 07:00-12:00 next day: 8h gap = legal rest, so two
        // separate duty periods of 3h and 5h — both under the 14h cap.
        val fa1 = assignment("fa1", flight("FL1", a, t(2, 20), b, t(2, 23)), e)
        val fa2 = assignment("fa2", flight("FL2", b, t(3, 7), c, t(3, 12)), e)
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(fa1, fa2)
            .penalizes(0)
    }
}
