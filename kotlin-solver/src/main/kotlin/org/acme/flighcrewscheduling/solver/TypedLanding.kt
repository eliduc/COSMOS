package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.JsonNode

/**
 * Lossless landing/2 wire reader. Never infer unit, relation or scope from a
 * solver knob. This checks wire identity and provenance shape, not authenticity
 * of a remote catalogue release; package admission remains a separate gate.
 * Unversioned historical landings retain their compatibility reader.
 */
internal object TypedLanding {
    private fun keys(node: JsonNode) = node.fieldNames().asSequence().toSet()
    private fun text(node: JsonNode?) = node != null && node.isTextual && node.asText().isNotBlank()
    private fun sameValue(a: JsonNode?, b: JsonNode?): Boolean =
        if (a?.isNumber == true && b?.isNumber == true)
            a.decimalValue().compareTo(b.decimalValue()) == 0 else a == b

    fun params(rule: JsonNode): List<RuleParam>? {
        val fit = rule.get("template_fit") ?: return null
        if (!fit.has("schema_version") && !fit.has("family_contract")) return null
        val rid = rule.get("id")?.asText() ?: ""
        fun bad(code: String, why: String, name: String? = null): Nothing =
            throw TaskPackageError("$rid" + (name?.let { "/$it" } ?: "") + ": $code: $why")
        if (fit.get("schema_version")?.textValue() != "landing/2")
            bad("landing-version", "unsupported landing version; legacy fallback is forbidden")
        if (fit.get("category")?.asText() !in setOf("A", "B")) return emptyList()
        val pin = fit.get("family_contract")
        val pinKeys = setOf("family_id", "catalog_version", "catalog_fingerprint", "record_fingerprint", "param_set")
        if (pin == null || !pin.isObject || keys(pin) != pinKeys ||
            pin.any { !text(it) || it.asText().trim() != it.asText() } ||
            listOf("catalog_fingerprint", "record_fingerprint").any {
                !Regex("[a-f0-9]{64}").matches(pin.get(it).asText()) } ||
            pin.get("family_id") != fit.get("family") ||
            pin.get("catalog_fingerprint") != fit.get("registry_rev"))
            bad("landing-contract", "family_contract identity is incomplete or inconsistent")
        val cells = fit.get("params")
        if (cells == null || !cells.isObject) bad("landing-incomplete", "params must be an object")
        val required = setOf("value", "unit", "relation", "condition", "quote", "source")
        val allowed = required + setOf("tz", "answer", "pending_revision")
        val output = ArrayList<RuleParam>()
        val kind = rule.get("kind")?.asText() ?: ""
        for ((name, cell) in cells.fields().asSequence()) {
            fun incomplete(why: String): Nothing = bad("landing-incomplete", why, name)
            if (!cell.isObject || !keys(cell).containsAll(required) || (keys(cell) - allowed).isNotEmpty())
                incomplete("required cell fields are missing or unknown fields are present")
            if (cell.has("pending_revision")) incomplete("pending revision is not an executable value")
            if (!text(cell.get("unit")) || !text(cell.get("quote"))) incomplete("unit/quote must be nonempty text")
            val relationNode = cell.get("relation")
            if (!relationNode.isNull && !text(relationNode)) incomplete("relation must be text or null")
            val unit = cell.get("unit").asText()
            val relation = if (relationNode.isNull) {
                if (unit in setOf("code", "code_list")) "eq" else ""
            } else relationNode.asText()
            val condition = cell.get("condition")
            if (!condition.isNull && (!condition.isObject || keys(condition) != setOf("kind", "value") ||
                    !text(condition.get("kind")) || !text(condition.get("value"))))
                incomplete("condition must be null or a typed {kind,value} scope")
            val qualifier = if (condition.isNull) null else condition.get("value").asText()
            if (!condition.isNull && condition.get("kind").asText() !in setOf("area", "base_code", "crew_variant"))
                incomplete("unknown condition kind cannot be erased")
            if (unit == "time_of_day_minutes") {
                if (cell.get("tz")?.textValue() !in setOf("UTC", "base-local", "acclimatized"))
                    incomplete("time_of_day_minutes requires a declared timezone")
            } else if (cell.has("tz")) incomplete("timezone is only valid for time_of_day_minutes")
            val source = cell.get("source")
            if (!source.isObject) incomplete("source must be an object")
            when (source.get("kind")?.textValue()) {
                "rule" -> if (keys(source) != setOf("kind", "rule_id") || source.get("rule_id")?.textValue() != rid)
                    incomplete("source.rule_id must identify the carrier rule")
                "code" -> if (keys(source) != setOf("kind", "note") || !text(source.get("note")))
                    incomplete("code source requires a note")
                "answer" -> {
                    val q = source.get("question")
                    val v = source.get("version")
                    val effect = source.get("effect")
                    if (keys(source) != setOf("kind", "question", "version", "effect", "root") ||
                        q == null || !q.isObject || keys(q) != setOf("id", "revision", "fingerprint") ||
                        !text(q.get("id")) || !text(q.get("fingerprint")) ||
                        q.get("revision")?.isIntegralNumber != true || q.get("revision").asLong() < 1 ||
                        v == null || !v.isObject || keys(v) != setOf("fingerprint") || !text(v.get("fingerprint")) ||
                        effect == null || !effect.isObject || keys(effect) != setOf("rule", "field") ||
                        effect.get("rule")?.textValue() != name || effect.get("field")?.textValue() != "value" ||
                        !text(source.get("root"))) incomplete("answer source requires exact question/version/effect/root")
                }
                else -> incomplete("unknown source kind")
            }
            val value = cell.get("value")
            var codes: List<String>? = null
            when (unit) {
                "code" -> {
                    if (!text(value)) incomplete("code requires one nonempty designation")
                    codes = listOf(value.asText())
                }
                "code_list" -> {
                    if (!value.isArray || value.isEmpty || value.any { !text(it) })
                        incomplete("code_list requires nonempty text designations")
                    codes = value.map { it.asText() }
                    if (codes.distinct().size != codes.size) incomplete("duplicate designation")
                }
                "flag" -> if (!value.isBoolean) incomplete("flag requires a boolean")
                "count" -> if (value.isObject) {
                    if (value.isEmpty || value.fields().asSequence().any { (base, n) ->
                        !Regex("[A-Z0-9]{2,4}").matches(base) || !n.isIntegralNumber || n.bigIntegerValue().signum() < 0 })
                        incomplete("count/base map requires nonnegative integer values and canonical keys")
                    if (!condition.isNull && (condition.get("kind").asText() != "base_code" ||
                        keys(value) != setOf(qualifier))) incomplete("base condition disagrees with map")
                } else if (!value.isNumber || !value.asDouble().isFinite() ||
                    value.decimalValue().stripTrailingZeros().scale() > 0 || value.decimalValue().signum() < 0)
                    incomplete("count requires a finite nonnegative integer")
                "duration_minutes", "time_of_day_minutes", "credit_hours", "credit_hours_per_hour", "hours_per_credit_hour" ->
                    if (!value.isNumber || !value.asDouble().isFinite()) incomplete("numeric unit requires a finite JSON number")
                else -> incomplete("unsupported typed unit $unit")
            }
            fun param(v: JsonNode, scope: String?, raw: String = v.asText()) = RuleParam(
                name = name, value = if (v.isNumber) v.asDouble() else null,
                rawValue = raw, unit = unit, relation = relation, qualifier = scope,
                asWritten = cell.get("quote").asText(), ruleId = rid, ruleKind = kind,
                tz = cell.get("tz")?.textValue(), codeValues = codes)
            if (unit == "count" && value.isObject) {
                for ((base, number) in value.fields().asSequence()) output.add(param(number, base))
            } else output.add(param(value, qualifier, if (value.isContainerNode) value.toString() else value.asText()))
        }
        val legacy = rule.get("params")
        if (legacy != null && !legacy.isNull) {
            if (!legacy.isArray || legacy.any { !it.isObject || !text(it.get("name")) })
                bad("param-carriers-diverge", "legacy params must be named records")
            for (p in legacy) {
                val name = p.get("name").asText()
                val cell = cells.get(name) ?: continue
                val candidates = output.filter { it.name == name }
                val matches = candidates.filter { it.qualifier == p.get("qualifier")?.takeUnless { q -> q.isNull }?.asText() }
                if (matches.size != 1) bad("param-carriers-diverge", "scope differs between carriers", name)
                val projected = matches.single()
                val expectedValue = if (cell.get("value").isObject && projected.unit == "count")
                    cell.get("value").get(projected.qualifier) else cell.get("value")
                val legacyValue = p.get("value")
                val valueAgrees = if (projected.codeValues != null && projected.unit == "code_list" && legacyValue?.isTextual == true)
                    projected.codeValues.sorted() == legacyValue.asText().split(',').map { it.trim() }.sorted()
                    else sameValue(expectedValue, legacyValue)
                if (!valueAgrees || p.get("unit")?.textValue() != projected.unit ||
                    p.get("relation")?.textValue() != projected.relation ||
                    p.get("tz")?.takeUnless { it.isNull }?.textValue() != projected.tz)
                    bad("param-carriers-diverge", "value/unit/relation/timezone differ between carriers", name)
            }
            // Legacy-only records are parsed by the established reader.
        }
        return output
    }
}

