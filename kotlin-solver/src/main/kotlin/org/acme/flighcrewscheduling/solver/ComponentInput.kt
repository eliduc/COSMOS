package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.core.JsonParser
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.util.zip.ZipInputStream

/** Native measurement input only. Deliberately not a LoadedTaskPackage, with
 * no executable modes, readiness, customer-run identity or conversion method.
 */
class LoadedComponentInput internal constructor(
    val fingerprint: String,
    val catalogVersion: String,
    val catalogFingerprint: String,
    val record: String,
    val taskId: String,
    val nativeModel: NativeModel,
    val model: JsonNode,
    val manifest: JsonNode,
    val world: JsonNode,
    val expected: JsonNode,
    private val problem: ByteArray,
) {
    fun problemBytes(): ByteArray = problem.copyOf()
}

/** A separate, closed envelope for component measurement. Hashes are integrity
 * bindings, not signatures or an approval. The catalog's declared oracle is
 * retained separately from observations; normal run admission cannot read this
 * format, even if someone adds an executable manifest or fake allowed_modes.
 */
object ComponentInput {
    private const val MANIFEST = "component-manifest.json"
    private const val MAX_MEMBER_BYTES = 16 * 1024 * 1024
    private const val MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
    private val requiredKeys = setOf("schema", "scope", "spec_version", "catalog", "task", "files",
        "consistency", "families", "form", "coverage", "measure", "has_data", "excluded_until_data")
    private val optionalKeys = setOf("fits_catalog", "rule_maps", "logic_unproven", "crew_size", "time",
        "horizon", "history", "freeze_clauses", "obligations")
    private val requiredMembers = setOf("model.json", "objective.json", "vmp-form.json", "vmp-form.md",
        "smt-report.json", "world.json", "problem.json", "stand-world.json",
        "declared-world.json", "declared-expected.json")
    private val optionalMembers = setOf("threshold-forms.json", "logic-examples.json", "clean-data.json",
        "measure-protocol.json", "obligations-ledger.json", "obligations-report.json", "svod.md", "anketa.md")
    private val clauseMember = Regex("freeze/clauses/[A-Za-z0-9_-]+\\.json")
    private val digestPattern = Regex("[a-f0-9]{64}")
    private val mapper = ObjectMapper().enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)

    private fun bad(reason: String): Nothing = throw TaskPackageError("component-input: $reason")
    private fun keys(node: JsonNode): Set<String> = node.fieldNames().asSequence().toSet()
    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }

    private fun json(name: String, bytes: ByteArray): JsonNode = try {
        mapper.readTree(bytes) ?: bad("$name is empty JSON")
    } catch (e: TaskPackageError) { throw e }
      catch (e: Exception) { bad("$name is not unambiguous JSON: ${e.message}") }

    private fun objectKeys(node: JsonNode, required: Set<String>, optional: Set<String> = emptySet(),
                           label: String) {
        if (!node.isObject || !keys(node).containsAll(required) || (keys(node) - required - optional).isNotEmpty())
            bad("$label has missing or unknown fields")
    }

    private fun text(node: JsonNode?, label: String): String {
        if (node == null || !node.isTextual || node.textValue().isBlank() || node.textValue().trim() != node.textValue())
            bad("$label must be nonempty text without surrounding whitespace")
        return node.textValue()
    }

    private fun digest(node: JsonNode?, label: String): String = text(node, label).also {
        if (!digestPattern.matches(it)) bad("$label must be lowercase SHA-256")
    }

    fun readBytes(zipBytes: ByteArray): LoadedComponentInput {
        val files = linkedMapOf<String, ByteArray>()
        var total = 0L
        try {
            ZipInputStream(ByteArrayInputStream(zipBytes)).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    val name = entry.name
                    if (entry.isDirectory || name == "manifest.json" ||
                        name != MANIFEST && name !in requiredMembers && name !in optionalMembers && !clauseMember.matches(name))
                        bad("unknown, mixed, or noncanonical member: $name")
                    if (name in files) bad("duplicate archive member: $name")
                    val bytes = zip.readNBytes(MAX_MEMBER_BYTES + 1)
                    if (bytes.size > MAX_MEMBER_BYTES) bad("member too large: $name")
                    total += bytes.size
                    if (total > MAX_ARCHIVE_BYTES) bad("archive payload is too large")
                    files[name] = bytes
                }
            }
        } catch (e: TaskPackageError) { throw e }
          catch (e: Exception) { bad("unreadable archive: ${e.message}") }
        if (!files.keys.containsAll(requiredMembers + MANIFEST)) bad("required members are missing")

        val manifestBytes = files.getValue(MANIFEST)
        val manifest = json(MANIFEST, manifestBytes)
        objectKeys(manifest, requiredKeys, optionalKeys, MANIFEST)
        if (manifest.path("schema").textValue() != "component-input/1" ||
            manifest.path("scope").textValue() != "component-only" ||
            manifest.path("spec_version").textValue() != "1.21")
            bad("unsupported schema, scope, or native spec_version")
        if (!manifest.path("has_data").isBoolean) bad("has_data must be boolean")

        val listed = manifest.path("files")
        if (!listed.isArray || listed.isEmpty) bad("files must be a nonempty list")
        val names = mutableListOf<String>()
        for (entry in listed) {
            objectKeys(entry, setOf("name", "sha256", "bytes"), label = "files entry")
            val name = text(entry.get("name"), "files.name")
            if (name == MANIFEST || name in names) bad("duplicate/self-declared file: $name")
            val body = files[name] ?: bad("listed file is missing: $name")
            val size = entry.path("bytes")
            if (!size.isIntegralNumber || !size.canConvertToLong() || size.longValue() != body.size.toLong())
                bad("byte count mismatch: $name")
            if (digest(entry.get("sha256"), "$name.sha256") != sha(body)) bad("hash mismatch: $name")
            names.add(name)
        }
        if (names != names.sorted() || names.toSet() != files.keys - MANIFEST)
            bad("files must be the exact sorted member list")

        // Check every JSON member for duplicate keys before the common decoder
        // reads it with its historical number-preservation rules.
        val parsed = files.filterKeys { it.endsWith(".json") }.mapValues { (name, bytes) -> json(name, bytes) }
        val catalog = manifest.path("catalog")
        objectKeys(catalog, setOf("version", "fingerprint"), label = "catalog")
        val catalogVersion = text(catalog.get("version"), "catalog.version")
        val catalogFingerprint = digest(catalog.get("fingerprint"), "catalog.fingerprint")
        val task = manifest.path("task")
        objectKeys(task, setOf("record", "id", "world_sha256", "expected_sha256"), label = "task")
        val record = text(task.get("record"), "task.record")
        if (!Regex("(families|criteria)/[a-z][a-z0-9_]*").matches(record)) bad("invalid task.record")
        val taskId = text(task.get("id"), "task.id")
        if (!Regex("[A-Za-z0-9][A-Za-z0-9_-]*").matches(taskId)) bad("invalid task.id")
        for ((field, file) in listOf("world_sha256" to "declared-world.json", "expected_sha256" to "declared-expected.json"))
            if (digest(task.get(field), "task.$field") != sha(files.getValue(file))) bad("task declaration hash mismatch: $field")
        val world = parsed.getValue("world.json")
        objectKeys(world, setOf("world", "expected"), label = "world.json")
        val declaredWorld = parsed.getValue("declared-world.json")
        val declaredExpected = parsed.getValue("declared-expected.json")
        if (!declaredWorld.isObject || !declaredExpected.isObject ||
            world.get("world") != declaredWorld || world.get("expected") != declaredExpected)
            bad("world/expected differs from the bound task declarations")
        if (parsed.getValue("stand-world.json").path("стенд").path("world") != declaredWorld)
            bad("native source world differs from the task world")

        val model = parsed.getValue("model.json")
        for (rule in model.path("rules")) {
            val pin = rule.path("template_fit").path("family_contract")
            if (!pin.isMissingNode && (pin.path("catalog_version").textValue() != catalogVersion ||
                    pin.path("catalog_fingerprint").textValue() != catalogFingerprint))
                bad("landing catalog identity differs from the component catalog")
        }
        val native = TaskPackage.decodeNative(files, manifest, "1.21", names.toSet())
        if (native.landingArchiveOnly) bad("historical landing is not a modern component input")
        return LoadedComponentInput(sha(manifestBytes), catalogVersion, catalogFingerprint, record, taskId,
            native, model, manifest, declaredWorld, declaredExpected, files.getValue("problem.json"))
    }
}
