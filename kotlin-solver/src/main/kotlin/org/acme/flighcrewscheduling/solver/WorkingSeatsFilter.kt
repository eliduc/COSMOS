package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.impl.heuristic.selector.common.decorator.SelectionFilter
import ai.timefold.solver.core.impl.score.director.ScoreDirector
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * Фильтр конструктива: перегонные кресла (deadhead) конструктив НЕ
 * заполняет — их занимает только локальный поиск, когда перегон нужен
 * (непрерывность маршрута). Первый живой проход с перегонами по умолчанию
 * (05.09.2026, исходный H0): конструктив «первый подходящий» рассадил
 * пилотов в 314 перегонных кресел из 594 — перегон ничего не стоит, а
 * занятость по нему съела резерв (−54) и окна отдыха. Это средство
 * поиска, не правило и не цена: счёт не меняется, умолчание для наборов
 * без перегонных кресел байт-идентично (фильтр пропускает все места).
 */
class WorkingSeatsFilter : SelectionFilter<FlightCrewSchedule, FlightAssignment> {
    override fun accept(scoreDirector: ScoreDirector<FlightCrewSchedule>,
                        seat: FlightAssignment): Boolean = !seat.deadhead
}
