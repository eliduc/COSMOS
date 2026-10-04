package org.acme.flighcrewscheduling.domain

import ai.timefold.solver.core.api.domain.common.PlanningId
import com.fasterxml.jackson.annotation.JsonIdentityInfo
import com.fasterxml.jackson.annotation.ObjectIdGenerators

@JsonIdentityInfo(scope = Airport::class, generator = ObjectIdGenerators.PropertyGenerator::class, property = "code")
class Airport {

    @PlanningId
    var code: String = ""
    var name: String = ""
    var latitude: Double = 0.0
    var longitude: Double = 0.0
    var taxiTimeInMinutes: Map<Airport, Long>? = null
    var languages: MutableList<String> = mutableListOf()

    constructor()

    constructor(code: String) {
        this.code = code
    }

    constructor(code: String, name: String, latitude: Double, longitude: Double) {
        this.code = code
        this.name = name
        this.latitude = latitude
        this.longitude = longitude
    }

    override fun toString(): String = name

    override fun equals(other: Any?): Boolean = other is Airport && other.code == code
    override fun hashCode(): Int = code.hashCode()
}
