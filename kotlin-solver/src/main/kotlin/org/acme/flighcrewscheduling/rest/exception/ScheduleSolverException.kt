package org.acme.flighcrewscheduling.rest.exception

import jakarta.ws.rs.core.Response

class ScheduleSolverException : RuntimeException {

    val jobId: String
    val status: Response.Status

    constructor(jobId: String, status: Response.Status, message: String) : super(message) {
        this.jobId = jobId
        this.status = status
    }

    constructor(jobId: String, cause: Throwable) : super(cause.message, cause) {
        this.jobId = jobId
        this.status = Response.Status.INTERNAL_SERVER_ERROR
    }
}
