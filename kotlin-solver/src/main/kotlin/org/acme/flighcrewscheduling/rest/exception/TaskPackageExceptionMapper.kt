package org.acme.flighcrewscheduling.rest.exception

import jakarta.ws.rs.core.Response
import jakarta.ws.rs.ext.ExceptionMapper
import jakarta.ws.rs.ext.Provider
import org.acme.flighcrewscheduling.solver.TaskModeRefused
import org.acme.flighcrewscheduling.solver.TaskPackageError

/**
 * Мост CDPM -> COSMOS, этап А: отказы пакета постановки — 400 с
 * НАЗВАННОЙ причиной, а не 500 «внутренняя ошибка».
 *
 * Различие существенно для вызывающей стороны: 400 говорит «постановка
 * не годится, читай причину», 500 говорит «решатель сломался». Условие
 * МУ-02 требует именно первого.
 */
@Provider
class TaskPackageErrorMapper : ExceptionMapper<TaskPackageError> {
    override fun toResponse(exception: TaskPackageError): Response =
        Response.status(Response.Status.BAD_REQUEST)
            .entity(ErrorInfo("task-package",
                "пакет постановки отвергнут: " + exception.message))
            .build()
}

@Provider
class TaskModeRefusedMapper : ExceptionMapper<TaskModeRefused> {
    override fun toResponse(exception: TaskModeRefused): Response =
        Response.status(Response.Status.BAD_REQUEST)
            .entity(ErrorInfo("task-mode",
                "режим задачи отвергнут: " + exception.message))
            .build()
}
