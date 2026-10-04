package org.acme.flighcrewscheduling.rest

import com.fasterxml.jackson.annotation.JsonUnwrapped
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.PackagedRuns
import org.acme.flighcrewscheduling.solver.RunRecord
import org.acme.flighcrewscheduling.solver.TaskModeRefused

/**
 * Ответ `GET /schedules/run/{runId}/result` (H11-WIRE-1, рецензия GPT-024
 * от 06.09.2026): решение прогона ПЛЮС отчёт цены ночёвок ТОГО ЖЕ прогона.
 *
 * До правки решение уезжало голым [FlightCrewSchedule] — у класса нет
 * поля `layoverCost`, — а отчёт RosterLayoverCost жил только в
 * `run/{id}/applied-rules`. Экспортёр входа сертификатора искал отчёт в
 * решении и не находил: сверка сертификата с решателем всегда давала
 * «несравнимо» (`solver_layover_cost_missing`) даже при посчитанном отчёте.
 *
 * Поля решения разворачиваются на верхний уровень — формат обмена для
 * прежних читателей не меняется; `layoverCost` — ключом рядом, ТЕМ ЖЕ
 * объектом [RunRecord.layoverCost], который отдают статус и `applied-rules`
 * этого runId (run-scoped: из записи прогона, не «последний отчёт»
 * процесса); null — прогон отчёта не считал (пейринг, зарегистрированный
 * каталог, прогон прежней сборки).
 *
 * Готовность судится СОСТОЯНИЕМ записи, не наличием решения
 * (H11-K-STATE-1, рецензия GPT-031 от 06.09.2026): в жизненном цикле
 * прогона [PackagedRuns.start] решение публикуется в запись РАНЬШЕ отчёта
 * ночёвок и раньше перевода в «готов», а при падении после публикации
 * решение в записи остаётся. Проверка «solution != null» в этом окне
 * отдавала решение с null-отчётом при «считает» и решение при «упал» —
 * читатель принимал промежуточный снимок за итог.
 */
class PackagedRunResult(
    @get:JsonUnwrapped val solution: FlightCrewSchedule,
    val layoverCost: Map<String, Any?>?,
) {
    companion object {
        /**
         * Решение прогона по его записи. Отдаётся ТОЛЬКО при состоянии
         * [PackagedRuns.ST_DONE]; пока считает, упал или любое иное
         * состояние — отказ словами состояния (тот же [TaskModeRefused],
         * что и прежде: провод отвечает 400 с названной причиной).
         * Готовая запись без отчёта ночёвок (прогон прежней сборки,
         * пейринг) — решение с null-отчётом, это допустимо.
         */
        fun of(r: RunRecord): PackagedRunResult {
            if (r.state != PackagedRuns.ST_DONE) {
                throw TaskModeRefused(
                    "прогон " + r.runId + " не готов: состояние «" + r.state + "»" +
                    (r.failure?.let { " — " + it } ?: ""))
            }
            val solution = r.solution ?: throw TaskModeRefused(
                "прогон " + r.runId + " в состоянии «" + r.state + "» без решения" +
                " (задача " + r.task + ", способ " + r.mode + ")")
            return PackagedRunResult(solution, r.layoverCost)
        }
    }
}
