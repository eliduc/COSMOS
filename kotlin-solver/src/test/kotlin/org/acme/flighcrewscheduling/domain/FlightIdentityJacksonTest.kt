package org.acme.flighcrewscheduling.domain

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNotSame
import org.junit.jupiter.api.Assertions.assertSame
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * C01/C02 phase 3 — identity contract through Jackson, verified empirically.
 *
 *  1. LEGACY payloads (identity = flightNumber) keep resolving exactly as
 *     before — @JsonIdentityInfo stays on flightNumber. (An attempt to move
 *     it to a fallback-getter flightLegId broke every legacy payload with
 *     UnresolvedForwardReference: Jackson registers PropertyGenerator ids
 *     only from the JSON token, never through a getter fallback.)
 *  2. The C02 mine must be LOUD, not silent: a payload whose flights repeat
 *     a wire flightNumber must NOT silently merge two legs into one entity.
 *  3. Multi-date legs reach the solver with a UNIQUE wire number = leg id
 *     (the _bts_to_schedule convention); flightLegId rides along as data.
 *  4. crewSlotId is the Timefold planning id with fallback to id.
 */
class FlightIdentityJacksonTest {

    private val mapper: ObjectMapper = ObjectMapper().findAndRegisterModules()

    private fun legacyJson() = """
        {
          "airports": [{"code": "AAA"}, {"code": "BBB"}],
          "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Captain"]}],
          "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "BBB", "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F2", "departureAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalAirport": "AAA", "arrivalUTCDateTime": "2026-03-01T14:00:00"}
          ],
          "flightAssignments": [
            {"id": "1", "flight": "F1", "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "2", "flight": "F2", "indexInFlight": 0, "requiredSkill": "Captain"}
          ]
        }
    """.trimIndent()

    private fun duplicateWireNumberJson() = """
        {
          "airports": [{"code": "AAA"}, {"code": "BBB"}],
          "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Captain"]}],
          "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "BBB", "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalAirport": "BBB", "arrivalUTCDateTime": "2026-03-02T10:00:00"}
          ],
          "flightAssignments": [
            {"id": "1", "flight": "F1", "indexInFlight": 0, "requiredSkill": "Captain"}
          ]
        }
    """.trimIndent()

    private fun multiDateBtsConventionJson() = """
        {
          "airports": [{"code": "AAA"}, {"code": "BBB"}],
          "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Captain"]}],
          "flights": [
            {"flightNumber": "F1|2026-03-01T08:00:00", "flightLegId": "F1|2026-03-01T08:00:00",
             "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "BBB", "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F1|2026-03-02T08:00:00", "flightLegId": "F1|2026-03-02T08:00:00",
             "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalAirport": "BBB", "arrivalUTCDateTime": "2026-03-02T10:00:00"}
          ],
          "flightAssignments": [
            {"id": "1", "crewSlotId": "s1", "flight": "F1|2026-03-01T08:00:00",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "2", "crewSlotId": "s2", "flight": "F1|2026-03-02T08:00:00",
             "indexInFlight": 0, "requiredSkill": "Captain"}
          ]
        }
    """.trimIndent()

    @Test
    fun legacyPayloadResolvesRefsByFlightNumber() {
        val s = mapper.readValue(legacyJson(), FlightCrewSchedule::class.java)
        assertEquals(2, s.flights.size)
        assertEquals(2, s.flightAssignments.size)
        assertSame(s.flights[0], s.flightAssignments[0].flight)
        assertSame(s.flights[1], s.flightAssignments[1].flight)
        // effective leg id falls back to the flightNumber
        assertEquals("F1", s.flights[0].flightLegId)
        // planning id fallback: crewSlotId == id
        assertEquals("1", s.flightAssignments[0].crewSlotId)
    }

    @Test
    fun legacyRoundTripKeepsResolvingAfterReserialization() {
        val s = mapper.readValue(legacyJson(), FlightCrewSchedule::class.java)
        val again = mapper.readValue(
            mapper.writeValueAsString(s), FlightCrewSchedule::class.java)
        assertEquals(2, again.flights.size)
        assertSame(again.flights[0], again.flightAssignments[0].flight)
        assertEquals("F1", again.flightAssignments[0].flight!!.flightNumber)
    }

    @Test
    fun duplicateWireNumberIsLoudNotSilentMerge() {
        // The C02 mine: two legs sharing a wire flightNumber must never
        // SILENTLY merge into one entity. Either Jackson rejects the payload
        // loudly, or both legs must survive as distinct entities.
        val outcome = runCatching {
            mapper.readValue(duplicateWireNumberJson(),
                             FlightCrewSchedule::class.java)
        }
        if (outcome.isSuccess) {
            val s = outcome.getOrThrow()
            assertEquals(2, s.flights.size,
                "duplicate wire flightNumber silently collapsed two legs")
            assertNotSame(s.flights[0], s.flights[1],
                "duplicate wire flightNumber deduped into one instance")
            assertNotEquals(
                s.flights[0].departureUTCDateTime,
                s.flights[1].departureUTCDateTime,
                "second leg's schedule was overwritten by the first")
        }
        // isFailure => loud rejection: exactly what fail-closed requires.
    }

    @Test
    fun multiDateBtsConventionKeepsLegsDistinct() {
        val s = mapper.readValue(multiDateBtsConventionJson(),
                                 FlightCrewSchedule::class.java)
        assertEquals(2, s.flights.size)
        assertNotSame(s.flights[0], s.flights[1])
        assertNotEquals(s.flights[0], s.flights[1])
        val f0 = s.flightAssignments[0].flight!!
        val f1 = s.flightAssignments[1].flight!!
        assertNotSame(f0, f1)
        assertEquals("2026-03-01T08:00", f0.departureUTCDateTime.toString())
        assertEquals("2026-03-02T08:00", f1.departureUTCDateTime.toString())
        assertNotEquals(f0.flightLegId, f1.flightLegId)
        assertEquals("s1", s.flightAssignments[0].crewSlotId)
    }

    @Test
    fun isReserveBindsUnderItsContractName() {
        // Латентный баг (найден инкрементом 4): Kotlin isReserve мапился
        // Jackson'ом в "reserve", и канонический payload не биндился.
        val e = mapper.readValue(
            """{"id": "R1", "isReserve": true}""", Employee::class.java)
        org.junit.jupiter.api.Assertions.assertTrue(e.isReserve)
        val json = mapper.writeValueAsString(e)
        assertTrue(json.contains("\"isReserve\":true"))
        assertTrue(!json.contains("\"reserve\""))
    }

    @Test
    fun transportOnlyIsPartOfThePayloadDictionary() {
        // Правило свода о транспортных рейсах: такой рейс — не обязательство
        // покрытия. Раньше поле молча съедалось терпимостью Quarkus к
        // неизвестным свойствам; теперь оно осознанная часть словаря:
        // читается, переживает сериализацию и по умолчанию ложно.
        val f = mapper.readValue(
            """{"flightNumber": "TT1", "transportOnly": true}""",
            Flight::class.java)
        org.junit.jupiter.api.Assertions.assertTrue(f.transportOnly)
        assertTrue(mapper.writeValueAsString(f)
            .contains("\"transportOnly\":true"))
        val plain = mapper.readValue(
            """{"flightNumber": "TT2"}""", Flight::class.java)
        org.junit.jupiter.api.Assertions.assertFalse(plain.transportOnly)
    }

    @Test
    fun equalsHashCodeUseEffectiveLegId() {
        val a = Flight("X1")
        val b = Flight("X1")
        assertEquals(a, b)                       // legacy: same number == equal
        b.flightLegId = "X1|d2"
        assertNotEquals(a, b)                    // distinct leg ids differ
        assertTrue(a.hashCode() != b.hashCode() || a != b)
    }
}
