package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test

/**
 * Классический способ (директива владельца 2026-08-24): целостность
 * цепочки утверждённого пейринга — все места одной цепочки достаются
 * ОДНОМУ сотруднику. Без меток ограничение молчит: прогоны без
 * фиксации пейринга не меняются ни на очко.
 */
class PairingChainTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private val v: ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> =
        ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java, FlightAssignment::class.java)

    private val a = airport("AAA")
    private val b = airport("BBB")
    private val e1 = employee("E1")
    private val e2 = employee("E2")
    private val f1 = flight("F1", a, t(2, 8), b, t(2, 9))
    private val f2 = flight("F2", b, t(2, 11), a, t(2, 12))

    private fun seat(id: String, f: org.acme.flighcrewscheduling.domain.Flight,
                     who: org.acme.flighcrewscheduling.domain.Employee?,
                     chain: String?) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply {
            employee = who
            chainId = chain
        }

    @Test
    fun `одна цепочка у одного человека — ноль`() {
        v.verifyThat(FlightCrewSchedulingConstraintProvider::pairingChainIntegrity)
            .given(seat("s1", f1, e1, "ц1"), seat("s2", f2, e1, "ц1"))
            .penalizes(0)
    }

    @Test
    fun `одна цепочка у двух людей — разрыв фиксации`() {
        v.verifyThat(FlightCrewSchedulingConstraintProvider::pairingChainIntegrity)
            .given(seat("s1", f1, e1, "ц1"), seat("s2", f2, e2, "ц1"))
            .penalizesBy(1)
    }

    @Test
    fun `пустое место цепочки — не разрыв целостности, а непокрытость`() {
        // Классовый фикс: штраф пары «назначенный ↔ пустой» делал
        // назначение первого места цепочки дороже пустоты — конструктив
        // и поиск оставляли цепочки пустыми навсегда (мини, Г-П2а).
        // Пустоту судит «Unassigned slot»; целостность — только двух
        // назначенных разных людей.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::pairingChainIntegrity)
            .given(seat("s1", f1, e1, "ц1"), seat("s2", f2, null, "ц1"))
            .penalizes(0)
    }

    @Test
    fun `разные цепочки и места без меток не судятся`() {
        v.verifyThat(FlightCrewSchedulingConstraintProvider::pairingChainIntegrity)
            .given(seat("s1", f1, e1, "ц1"), seat("s2", f2, e2, "ц2"),
                   seat("s3", f1, e2, null))
            .penalizes(0)
    }
}
