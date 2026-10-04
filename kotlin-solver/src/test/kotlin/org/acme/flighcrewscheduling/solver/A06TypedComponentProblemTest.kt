package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

/** Offline native-reference boundary only. No solver, external inputs or
 * component evidence writer. The unassigned seat is intentionally retained.
 */
class A06TypedComponentProblemTest {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private val probe = A06TypedComponentProbe()

    // Declared st-crew-1 shape: crew_size=2, only seat 0 assigned to P1.
    // The actual input's explicit seat-1 null caused the old identity assertion.
    private fun document(): ObjectNode = mapper.readTree("""
        {"airports":[{"code":"ST1"},{"code":"ST2"}],
         "employees":[{"homeAirport":"ST1","id":"P1","name":"P1","preferredDaysOff":[],
                       "seniority":500,"skills":["CP"],"typeRatings":["T1"]}],
         "flightAssignments":[
           {"deadhead":false,"employee":"P1","flight":"F1","id":"F1#0","indexInFlight":0,"requiredSkill":"CP"},
           {"deadhead":false,"employee":null,"flight":"F1","id":"F1#1","indexInFlight":1,"requiredSkill":"CP"}],
         "flights":[{"aircraftType":"T1","arrivalAirport":"ST2","arrivalUTCDateTime":"2026-10-01T08:00:00",
                     "departureAirport":"ST1","departureUTCDateTime":"2026-10-01T06:00:00","flightNumber":"F1"}],
         "window_horizon":{"end_exclusive":"2026-10-31T00:00:00","start_inclusive":"2026-10-01T00:00:00"}}
    """) as ObjectNode

    private fun schedule(doc: JsonNode): FlightCrewSchedule =
        mapper.treeToValue(doc, FlightCrewSchedule::class.java)

    private fun checked(doc: JsonNode, result: FlightCrewSchedule = schedule(doc)) =
        probe.checkProblemReferences(result, doc.path("flightAssignments"))

    @Test
    fun `explicit unassigned seat survives without assignment or input mutation`() {
        val doc = document()
        val before = mapper.writeValueAsBytes(doc)
        val result = schedule(doc)
        val assigned = result.flightAssignments[0]
        val unassigned = result.flightAssignments[1]
        assertDoesNotThrow { checked(doc, result) }
        assertEquals(2, result.flightAssignments.size)
        assertSame(result.employees.single(), assigned.employee)
        assertNull(unassigned.employee)
        assertSame(result.flights.single(), assigned.flight)
        assertSame(result.flights.single(), unassigned.flight)
        assertEquals(1, result.flightAssignments.count { it.employee == null })
        assertArrayEquals(before, mapper.writeValueAsBytes(doc))
    }

    @Test
    fun `fully assigned native identity remains healthy`() {
        val doc = document()
        (doc.path("flightAssignments")[1] as ObjectNode).put("employee", "P1")
        val result = schedule(doc)
        assertDoesNotThrow { checked(doc, result) }
        assertTrue(result.flightAssignments.all { it.employee === result.employees.single() })
    }

    @Test
    fun `missing employee property cannot masquerade as explicit null`() {
        val doc = document()
        (doc.path("flightAssignments")[1] as ObjectNode).remove("employee")
        val result = schedule(doc)
        assertNull(result.flightAssignments[1].employee)
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `nonnull declared reference cannot be normalized to null`() {
        val doc = document()
        val result = schedule(doc)
        result.flightAssignments[0].employee = null
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `detached employee with the same id still fails reference identity`() {
        val doc = document()
        val result = schedule(doc)
        result.flightAssignments[0].employee = Employee().apply { id = "P1" }
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `dangling employee id is rejected by the unchanged native decoder`() {
        val doc = document()
        (doc.path("flightAssignments")[1] as ObjectNode).put("employee", "NOT_DECLARED")
        assertThrows(Exception::class.java) { checked(doc) }
    }

    @Test
    fun `unassigned seat does not excuse a missing flight reference`() {
        val doc = document()
        val result = schedule(doc)
        result.flightAssignments[1].flight = null
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `unassigned seat does not excuse a detached same-id flight`() {
        val doc = document()
        val result = schedule(doc)
        result.flightAssignments[1].flight = Flight().apply { flightNumber = "F1" }
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `changed assignment census is refused without dropping a seat`() {
        val doc = document()
        val result = schedule(doc)
        result.flightAssignments.removeAt(1)
        assertThrows(AssertionError::class.java) { checked(doc, result) }
    }

    @Test
    fun `non-array declaration cannot justify a native schedule`() {
        val doc = document()
        assertThrows(AssertionError::class.java) {
            probe.checkProblemReferences(schedule(doc), mapper.createObjectNode())
        }
    }
}
