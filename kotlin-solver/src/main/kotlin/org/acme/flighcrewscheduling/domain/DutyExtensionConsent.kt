package org.acme.flighcrewscheduling.domain

import com.fasterxml.jackson.core.JsonParser
import com.fasterxml.jackson.core.JsonToken
import com.fasterxml.jackson.databind.DeserializationContext
import com.fasterxml.jackson.databind.JsonDeserializer
import com.fasterxml.jackson.databind.JsonMappingException
import com.fasterxml.jackson.databind.annotation.JsonDeserialize

/** Explicit input evidence for ONE employee's duty, not permission for every future duty.
 * start/end are ISO LocalDateTime in the declared package time scale, matching flight timestamps.
 * Null consent is unknown, never a positive decision; the input validator refuses incomplete rows.
 */
class DutyExtensionConsent {
    var start: String? = null
    var end: String? = null
    @field:JsonDeserialize(using = ExplicitDutyConsentBoolean::class)
    var consent: Boolean? = null
    var source: String? = null
}

/** Jackson's normal boolean coercion must not turn strings/numbers into a human decision. */
class ExplicitDutyConsentBoolean : JsonDeserializer<Boolean>() {
    override fun deserialize(parser: JsonParser, context: DeserializationContext): Boolean =
        when (parser.currentToken()) {
            JsonToken.VALUE_TRUE -> true
            JsonToken.VALUE_FALSE -> false
            else -> throw JsonMappingException.from(parser, "consent: требуется JSON true/false, не строка/число")
        }

    override fun getNullValue(context: DeserializationContext): Boolean =
        throw JsonMappingException.from(context.parser, "consent: null не является явным согласием/отказом")
}
