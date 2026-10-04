package org.acme.flighcrewscheduling.rest.exception

import jakarta.ws.rs.core.Response
import jakarta.ws.rs.ext.ExceptionMapper
import jakarta.ws.rs.ext.Provider

@Provider
class ScheduleSolverExceptionMapper : ExceptionMapper<ScheduleSolverException> {
    override fun toResponse(exception: ScheduleSolverException): Response =
        Response.status(exception.status)
            .entity(ErrorInfo(exception.jobId, exception.message))
            .build()
}
