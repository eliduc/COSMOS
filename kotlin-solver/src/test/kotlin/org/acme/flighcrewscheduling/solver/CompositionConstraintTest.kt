package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test

/**
 * Crew-composition rules (multirole increment 4) in the GERAD2P profile.
 * OFF by default — the first assertions pin the no-op; the rest flip the
 * flags via RulesConfig.overrideForTests (the singleton loads once per JVM,
 * so env-based flipping is impossible in-process).
 */
class CompositionConstraintTest {

    private val verifier:
        ConstraintVerifier<GeradPairingRosterConstraintProvider,
                           FlightCrewSchedule> =
        ConstraintVerifier.build(
            GeradPairingRosterConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    @AfterEach
    fun restoreDefaults() {
        RulesConfig.overrideForTests(RulesConfig.DEFAULTS)
    }

    private fun coLocatedPair(
        e1: Employee?, e2: Employee?,
    ): Pair<FlightAssignment, FlightAssignment> {
        val a = airport("BASE")
        val b = airport("DEST")
        val f = flight("C_000000", a, t(1, 8), b, t(1, 12))
        val fa1 = FlightAssignment("1", f, 0, "Captain").apply { employee = e1 }
        val fa2 = FlightAssignment("2", f, 1, "FirstOfficer")
            .apply { employee = e2 }
        return fa1 to fa2
    }

    @Test
    fun `no-two-reserves is off by default and fires when enabled`() {
        val r1 = employee("R1").apply { isReserve = true }
        val r2 = employee("R2").apply { isReserve = true }
        val (fa1, fa2) = coLocatedPair(r1, r2)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionNoTwoReserves)
            .given(fa1, fa2)
            .penalizesBy(0)                     // выключено по умолчанию
        RulesConfig.overrideForTests(
            RulesConfig.DEFAULTS.copy(compNoTwoReserves = 1))
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionNoTwoReserves)
            .given(fa1, fa2)
            .penalizesBy(1)                     // пара резервистов — брак
    }

    @Test
    fun `no-two-reserves ignores mixed pair and different flights`() {
        RulesConfig.overrideForTests(
            RulesConfig.DEFAULTS.copy(compNoTwoReserves = 1))
        val r = employee("R1").apply { isReserve = true }
        val l = employee("L1")                   // линейный
        val (fa1, fa2) = coLocatedPair(r, l)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionNoTwoReserves)
            .given(fa1, fa2)
            .penalizesBy(0)
    }

    @Test
    fun `seniority mix is off by default and penalizes shortfall`() {
        // seniority 1000 -> вес 2 (11 - (999 div 100)); пара весом 4 при
        // пороге 6 — недобор 2
        val j1 = employee("J1").apply { seniority = 1000 }
        val j2 = employee("J2").apply { seniority = 1000 }
        val (fa1, fa2) = coLocatedPair(j1, j2)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionSeniorityMix)
            .given(fa1, fa2)
            .penalizesBy(0)                     // выключено по умолчанию
        RulesConfig.overrideForTests(
            RulesConfig.DEFAULTS.copy(compMinPairSeniorityWeight = 6))
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionSeniorityMix)
            .given(fa1, fa2)
            .penalizesBy(2)                     // 6 - (2+2)
        // старший (seniority 1 -> вес 10) вытягивает пару выше порога
        val s = employee("S1").apply { seniority = 1 }
        val (fb1, fb2) = coLocatedPair(s, j2)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::compositionSeniorityMix)
            .given(fb1, fb2)
            .penalizesBy(0)
    }
}
