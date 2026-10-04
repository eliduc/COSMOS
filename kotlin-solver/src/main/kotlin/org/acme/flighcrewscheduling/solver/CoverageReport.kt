package org.acme.flighcrewscheduling.solver

/**
 * П-1 — предпрогонная сверка покрытия (решение владельца 2026-08-24,
 * «после этапа Г»): получив пакет, решатель ДО оптимизации отдаёт
 * машинный отчёт, какие правила он умеет учитывать и чем, какие — нет,
 * и какие целевые вычислимы. Кнопка производителя данных останавливает
 * на этом отчёте поток («ожидает решения человека»): пользователь либо
 * авторизует счёт без неисполняемых правил, либо помечает критичные —
 * помеченные уходят в конвейер шаблонов (П-2).
 *
 * Сверка идёт БЕЗ счёта и БЕЗ набора данных: пороги и корзины ступени 3
 * судятся резолвером и компилятором пакета; клетки «предметов
 * рассмотрено» (fit) и пост-проверки судятся только при прогоне — отчёт
 * говорит это явно, а не молчит. Вычислимость целевых судит ТОТ ЖЕ
 * резолвер, что и прогон (одна правда, два читателя).
 */
object CoverageReport {

    fun of(pkg: LoadedTaskPackage): Map<String, Any?> {
        val tasks = pkg.allowedModes.keys.sorted()
        val perTask = LinkedHashMap<String, Any?>()
        for (task in tasks) {
            perTask[task] = taskCoverage(pkg, task)
        }
        return linkedMapOf(
            "packageFingerprint" to pkg.fingerprint,
            "tasks" to perTask,
            "note" to ("сверка пакета без счёта и без набора: клетки «предметов " +
                "рассмотрено» и пост-проверки судятся при прогоне; вычислимость " +
                "целевых — тем же резолвером, что у прогона"),
        )
    }

    private fun taskCoverage(pkg: LoadedTaskPackage, task: String): Map<String, Any?> {
        val modes = pkg.allowedModes[task].orEmpty()
        if (modes.isEmpty()) {
            return linkedMapOf("refused" to
                ("задаче " + task + " пакет не разрешил ни одного режима"))
        }
        val mode = if (MODE_FEASIBILITY_HARD in modes) MODE_FEASIBILITY_HARD else modes.first()
        val tm = try {
            TaskModes.resolve(mode, pkg, task = task)
        } catch (e: TaskModeRefused) {
            return linkedMapOf("mode" to mode, "refused" to e.message)
        }
        return linkedMapOf(
            "mode" to mode,
            "allowedModes" to modes,
            "thresholds" to RunThresholds.snapshot(tm.thresholds),
            "logic" to tm.logic?.snapshot(),
            "unmappedSoftRules" to tm.unmappedRules,
            "objectives" to objectivesCoverage(pkg, task),
        )
    }

    private fun objectivesCoverage(pkg: LoadedTaskPackage, task: String): List<Map<String, Any?>> =
        pkg.objectives[task].orEmpty().map { id ->
            try {
                val o = TaskModes.resolveObjective(id, pkg, task)
                linkedMapOf<String, Any?>(
                    "id" to id, "computable" to true,
                    "constraint" to o.constraint,
                    "approximation" to o.approximation)
            } catch (e: TaskModeRefused) {
                linkedMapOf<String, Any?>(
                    "id" to id, "computable" to false,
                    "reason" to e.message)
            }
        }
}
