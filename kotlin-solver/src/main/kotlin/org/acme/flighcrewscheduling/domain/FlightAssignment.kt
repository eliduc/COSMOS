package org.acme.flighcrewscheduling.domain

import ai.timefold.solver.core.api.domain.common.PlanningId
import ai.timefold.solver.core.api.domain.entity.PlanningEntity
import ai.timefold.solver.core.api.domain.entity.PlanningPin
import ai.timefold.solver.core.api.domain.variable.PlanningVariable
import com.fasterxml.jackson.annotation.JsonIgnore
import org.acme.flighcrewscheduling.solver.FlightAssignmentDifficultyComparatorFactory
import java.time.LocalDateTime

// comparatorFactoryClass (Timefold 2.0 API) enables the *_DECREASING
// construction heuristics (FIRST_FIT_DECREASING etc.) to place the
// hardest-to-staff assignments first. Plain FIRST_FIT ignores it, so the
// default path is unaffected.
@PlanningEntity(comparatorFactoryClass = FlightAssignmentDifficultyComparatorFactory::class)
class FlightAssignment {

    var id: String = ""

    /**
     * C01/C02 phase 3: first-class crew-seat identity, the Timefold
     * @PlanningId. Falls back to id when the payload does not carry it, so
     * every legacy payload keeps its planning identity unchanged. NOTE:
     * equals/hashCode and every constraint self-join guard stay on the raw
     * `id` field on purpose — importers must keep id populated and unique.
     */
    @get:PlanningId
    var crewSlotId: String = ""
        get() = if (field.isNotEmpty()) field else id

    var flight: Flight? = null
    var indexInFlight: Int = 0
    var requiredSkill: String = ""

    // allowsUnassignedValues (audit 2026-07-12, round 5): with forced
    // assignment the soft uncovered-penalties of GERAD2/GERAD2P were dead
    // code and the solver could prefer an ILLEGAL assignment over leaving
    // a slot open. Profiles where unassignment must stay forbidden now
    // declare an explicit hard constraint instead.
    @PlanningVariable(allowsUnassigned = true)
    var employee: Employee? = null

    // Disruption repair (2026-07-16): a pinned assignment keeps its pre-set
    // employee — the neighbourhood re-solve moves ONLY the broken part of
    // the roster. Timefold skips pinned entities natively; the input JSON
    // carries "pinned": true together with the frozen employee.
    @PlanningPin
    var pinned: Boolean = false

    // Требования допусков МЕСТА (2026-07-25): коды «тип судна_роль» —
    // сотрудник обязан держать каждый на всём интервале рейса-цепочки.
    var requiredCodes: MutableList<String> = mutableListOf()

    // Классический способ (директива владельца 2026-08-24): ростеринг
    // НА УТВЕРЖДЁННОМ ПЕЙРИНГЕ. Метка цепочки ставится решателем из
    // решения прогона пейринга (pairingRunId): все места одной цепочки
    // обязаны достаться ОДНОМУ сотруднику («Pairing chain integrity»).
    // Пусто — прогон без фиксации пейринга, поведение прежнее.
    var chainId: String? = null

    // Перегон экипажа (этап Г, 2026-08-24): место ПАССАЖИРА на рейсе —
    // сотрудник перемещается, не работая. Кресла порождает решатель из
    // порога пакета max_deadheads_per_flight (без пакета возможность
    // выключена). Живя тем же классом, перегон КАК ЕСТЬ входит в
    // непрерывность маршрута, стыковки, отдых и размах смены (зачёт в
    // рабочее время), а из налёта, посадок, навыков, допусков,
    // численности и покрытия ИСКЛЮЧЁН (зачёт по позициям — аналогия
    // правил обеих линий). Пустое перегонное кресло ничего не стоит.
    var deadhead: Boolean = false

    constructor()

    constructor(id: String, flight: Flight, indexInFlight: Int, requiredSkill: String) {
        this.id = id
        this.flight = flight
        this.indexInFlight = indexInFlight
        this.requiredSkill = requiredSkill
    }

    @JsonIgnore
    fun hasRequiredSkills(): Boolean = employee?.hasSkill(requiredSkill) == true

    @JsonIgnore
    fun isUnavailableEmployee(): Boolean {
        val e = employee ?: return false
        val f = flight ?: return false
        return !e.isAvailable(f.getDepartureUTCDate(), f.arrivalUTCDateTime!!.toLocalDate())
    }

    @JsonIgnore
    fun getDepartureUTCDateTime(): LocalDateTime = flight!!.departureUTCDateTime!!

    override fun toString(): String = "$flight-$indexInFlight"
    override fun equals(other: Any?): Boolean = other is FlightAssignment && other.id == id
    override fun hashCode(): Int = id.hashCode()
}
