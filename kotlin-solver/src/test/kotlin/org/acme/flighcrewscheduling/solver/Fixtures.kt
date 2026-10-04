package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.Airport
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import java.time.LocalDateTime

/** Minimal domain fixtures shared by the ConstraintVerifier tests.
 *  Every employee is fully qualified (skill + A320 type rating) so tests
 *  only trip the constraint they intend to trip. */
internal object Fixtures {

    const val SKILL = "PILOT"

    fun airport(code: String): Airport = Airport(code)

    fun flight(
        number: String,
        dep: Airport,
        depTime: LocalDateTime,
        arr: Airport,
        arrTime: LocalDateTime,
    ): Flight = Flight(number).apply {
        departureAirport = dep
        departureUTCDateTime = depTime
        arrivalAirport = arr
        arrivalUTCDateTime = arrTime
    }

    fun employee(id: String): Employee = Employee(id).apply {
        name = id
        skills = mutableListOf(SKILL)
        typeRatings = mutableListOf("A320")
    }

    fun assignment(id: String, flight: Flight, employee: Employee?): FlightAssignment =
        FlightAssignment(id, flight, 0, SKILL).apply { this.employee = employee }

    /** Full solution for multi-constraint (givenSolution) verification. */
    fun schedule(
        airports: List<Airport>,
        employees: List<Employee>,
        assignments: List<FlightAssignment>,
    ): FlightCrewSchedule = FlightCrewSchedule().apply {
        this.airports = airports.toMutableList()
        this.employees = employees.toMutableList()
        this.flights = assignments.mapNotNull { it.flight }.distinct().toMutableList()
        this.flightAssignments = assignments.toMutableList()
    }

    /** 2026-03-`day` `hour`:`minute` UTC. */
    fun t(day: Int, hour: Int, minute: Int = 0): LocalDateTime =
        LocalDateTime.of(2026, 3, day, hour, minute)
}
