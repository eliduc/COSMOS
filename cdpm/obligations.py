"""A12: evidence ledger for atomic obligations, NOT a semantic-completeness gate.

This additive, in-memory layer does not extract meaning, merge reader lists,
approve experts, update a model or change the existing coverage verdict. Its
versioned JSON carrier records claims; ``evaluate`` checks their exact inputs,
byte locators and model addresses. A linked component is a declared
correspondence, not a machine judgement that the field expresses its meaning.

Document inputs are ``{sid, body: bytes, clauses: [{anchor, text: str}]}``.
Freeze/model fingerprints cover the supplied bytes, without reserialization.
The caller remains responsible for obtaining these inputs from its real freeze.
Answer identities use the existing provenance resolver, never lineage guessing.

Reader results have independent local IDs; even identical results are not
merged. ``seal_reader`` hashes a result to detect accidental later editing; it
is not a signature or proof of independent extraction. Scope exclusions and
expert reviews retain author/reason, but authority is not inferred from JSON.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

SCHEMA = "obligation-ledger/1"
SNAPSHOT_SCHEMA = "obligation-inputs/1"
REPORT_SCHEMA = "obligation-report/1"
LEDGER_REL = "exchange/obligations.json"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COLLECTIONS = {"rules", "objectives", "questions"}
_PARTS = ("subject", "statement", "modality", "conditions", "exceptions", "sources")


class ObligationError(ValueError):
    """Addressed carrier/input error; no partial success is implied."""


def _require(ok, path, message):
    if not ok:
        raise ObligationError(f"{path}: {message}")


def _text(value, path):
    _require(isinstance(value, str) and bool(value.strip()), path, "expected nonempty text")
    return value


def _object(value, path):
    _require(isinstance(value, dict), path, "expected object")
    return value


def _list(value, path):
    _require(isinstance(value, list), path, "expected list")
    return value


def _sha(value, path):
    _require(isinstance(value, str) and _SHA.fullmatch(value), path, "expected SHA-256")
    return value


def _bytes_sha(value, path):
    _require(isinstance(value, bytes), path, "expected exact bytes")
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    """Canonical JSON fingerprint of ledger metadata, not source/model bytes."""
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ObligationError("carrier is not finite JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def review_content_sha256(ledger):
    """Bind a review to all authored content, excluding only the review itself.

    This is a canonical JSON content digest, not a signature or a new review.
    Preparation must never replace an expert's old digest automatically.
    """
    return fingerprint({key: value for key, value in _object(ledger, "ledger").items()
                        if key != "expert_review"})


def document_unit(sid, anchor):
    return "document:" + fingerprint([_text(sid, "sid"), _text(anchor, "anchor")])


def _answer_identity(source):
    _object(source, "answer.source")
    _require(source.get("kind") == "answer", "answer.source.kind", "expected answer")
    q = _object(source.get("question"), "answer.source.question")
    _text(q.get("id"), "answer.source.question.id")
    _text(q.get("fingerprint"), "answer.source.question.fingerprint")
    _require(type(q.get("revision")) is int and q["revision"] > 0,
             "answer.source.question.revision", "expected positive integer")
    version = _object(source.get("version"), "answer.source.version")
    _text(version.get("fingerprint"), "answer.source.version.fingerprint")
    return {"kind": "answer", "question": {k: q[k] for k in ("id", "revision", "fingerprint")},
            "version": {"fingerprint": version["fingerprint"]}}


def answer_unit(source):
    return "answer:" + fingerprint(_answer_identity(source))


def _capture(freeze_bytes, model_bytes, documents, answer_sources):
    # The strict decoder is shared; importing it does not run model validation,
    # migrations, configuration or a catalogue lookup.
    from cdpm.model import loads_strict
    snap = {"schema_version": SNAPSHOT_SCHEMA,
            "freeze_sha256": _bytes_sha(freeze_bytes, "freeze"),
            "model_sha256": _bytes_sha(model_bytes, "model"), "documents": [], "units": []}
    try:
        model = _object(loads_strict(model_bytes), "model")
    except (ValueError, UnicodeError) as exc:
        raise ObligationError(f"model: {exc}") from exc
    texts, sids = {}, set()
    for document in _list(documents, "documents"):
        _object(document, "document")
        sid = _text(document.get("sid"), "document.sid")
        _require(sid not in sids, sid, "duplicate source ID")
        sids.add(sid)
        digest = _bytes_sha(document.get("body"), sid)
        snap["documents"].append({"sid": sid, "sha256": digest})
        for clause in _list(document.get("clauses"), f"{sid}.clauses"):
            _object(clause, f"{sid}.clause")
            anchor = _text(clause.get("anchor"), f"{sid}.anchor")
            text = _text(clause.get("text"), f"{sid}/{anchor}.text")
            key = document_unit(sid, anchor)
            _require(key not in texts, f"{sid}/{anchor}", "duplicate clause")
            texts[key] = text.encode("utf-8")
            snap["units"].append({"id": key, "kind": "document", "sid": sid,
                                  "anchor": anchor, "source_sha256": digest,
                                  "text_sha256": _bytes_sha(texts[key], key)})
    for source in _list(answer_sources, "answer_sources"):
        from cdpm import provenance, question_editions
        identity = _answer_identity(source)
        try:
            question, raw = provenance.resolve_source(model, identity)
            targets = sorted(question_editions.targets(question))
        except ValueError as exc:
            raise ObligationError(f"answer: {exc}") from exc
        key = answer_unit(identity)
        _require(key not in texts, key, "duplicate answer identity")
        texts[key] = raw.encode("utf-8")
        version = question["answer"].get("answer_version") or {}
        snap["units"].append({"id": key, "kind": "answer", "source": identity,
                              "text_sha256": _bytes_sha(texts[key], key),
                              "received_at": version.get("received_at"),
                              "book_sha256": version.get("book_sha256"), "targets": targets,
                              "answer_status": question["answer"].get("status")})
    snap["documents"].sort(key=lambda x: x["sid"])
    snap["units"].sort(key=lambda x: x["id"])
    return snap, texts, model


def capture_snapshot(*, freeze_bytes, model_bytes, documents, answer_sources=None):
    """Capture explicit source inputs; no discovery, extraction or file writes."""
    return _capture(freeze_bytes, model_bytes, documents,
                    [] if answer_sources is None else answer_sources)[0]


def seal_reader(reader):
    """Return a detached result with a digest, leaving its original untouched."""
    result = copy.deepcopy(_object(reader, "reader"))
    result.pop("result_sha256", None)
    result["result_sha256"] = fingerprint(result)
    return result


def comparison_payload(obligation):
    """Content for duplicate/cross-reader comparison, without address provenance.

    Keep actual source units, quotes and resolved ranges. The original range,
    resolution method and input hash remain visible and sealed in the ledger,
    but must not turn equal content into different obligations.
    """
    result = copy.deepcopy({key: value for key, value in obligation.items() if key != "id"})
    for ref in result.get("sources", []):
        for fragment in ref.get("fragments", []):
            fragment.pop("address", None)
    return result


def _address_evidence(fragment, unit, source, fresh, path):
    if "address" not in fragment:
        return  # Historical authored ledgers are not migrated or repaired.
    address = _object(fragment["address"], path + ".address")
    _require(set(address) == {"origin", "unit_text_sha256", "declared", "resolved", "method"},
             path, "invalid address evidence fields")
    origin = address["origin"]
    _require(origin in {"declared", "recovered"}, path, "unknown address origin")
    _require(address["method"] == ("declared_exact" if origin == "declared" else "unique_exact_utf8"),
             path, "address origin/method mismatch")
    _require(address["unit_text_sha256"] == unit["text_sha256"], path,
             "address evidence belongs to another input text")
    for name in ("declared", "resolved"):
        value = _object(address[name], path + ".address." + name)
        _require(set(value) == {"start", "end"} and all(type(v) is int for v in value.values()),
                 path, "address evidence needs integer ranges")
    resolved = {key: fragment[key] for key in ("start", "end")}
    _require(address["resolved"] == resolved, path, "address evidence differs from fragment")
    if origin == "declared":
        _require(address["declared"] == resolved, path, "declared range was changed")
    elif fresh:
        quote = fragment["quote"].encode("utf-8")
        first = source.find(quote)
        _require(first == fragment["start"] and source.find(quote, first + 1) < 0,
                 path, "recovered address is not a unique exact occurrence")
        start, end = address["declared"]["start"], address["declared"]["end"]
        _require(not (0 <= start < end <= len(source) and source[start:end] == quote),
                 path, "valid declared range was marked recovered")


def _local_file(root, relative):
    """Read only within the selected run, including through symlinks."""
    path = (root / relative).resolve()
    _require(path.is_relative_to(root), relative, "path escapes the selected run")
    return path


def _filename(value, path):
    _text(value, path)
    _require(value not in {".", ".."} and not any(c in value for c in "/\\:"),
             path, "expected a single local filename")
    return value


def _run_inputs(run_dir, *, answer_sources=None, model_bytes=None, expected_model=None):
    """Read the existing CDPM layout; no extraction, healing or model calls.

    ``freeze_bytes`` is a deterministic inventory of the exact manifest and
    clause-file byte digests. Thus even an edit that preserves decoded JSON
    invalidates an old snapshot. Projections are not authority for obligation
    locators. This is input binding, not a replacement for gate_freeze.
    """
    from cdpm.model import loads_strict
    root = Path(run_dir).resolve()
    observed = {}

    def read(relative):
        path = _local_file(root, relative)
        raw = path.read_bytes()
        observed[path] = raw
        return raw

    manifest_raw = read("freeze/manifest.json")
    manifest = _object(loads_strict(manifest_raw), "freeze/manifest.json")
    documents, freeze_members, names, sids = [], [], set(), set()
    freeze_members.append({"path": "manifest.json", "sha256": _bytes_sha(manifest_raw, "manifest")})
    for entry in _list(manifest.get("files"), "freeze.files"):
        _object(entry, "freeze.files[]")
        sid = _filename(entry.get("sid"), "freeze.sid")
        name = _filename(entry.get("file"), "freeze.file")
        _require(sid not in sids and name not in names, "freeze", "duplicate source ID or file")
        sids.add(sid)
        names.add(name)
        expected = _sha(entry.get("sha256"), f"freeze/{sid}.sha256")
        body = read("src/" + name)
        _require(_bytes_sha(body, name) == expected, name, "source SHA differs from the freeze manifest")
        relative = f"clauses/{sid}.json"
        clause_raw = read("freeze/" + relative)
        clause_file = _object(loads_strict(clause_raw), relative)
        for field in ("sid", "file", "sha256"):
            _require(clause_file.get(field) == entry[field], relative, f"{field} differs from manifest")
        clauses = _list(clause_file.get("clauses"), relative + ".clauses")
        _require(type(entry.get("clause_count")) is int and entry["clause_count"] == len(clauses),
                 relative, "clause count differs from manifest")
        freeze_members.append({"path": relative, "sha256": _bytes_sha(clause_raw, relative)})
        documents.append({"sid": sid, "body": body, "clauses": clauses})
    if model_bytes is None:
        model_bytes = read("extract/model.json")
    _bytes_sha(model_bytes, "model_bytes")
    if expected_model is not None:
        _require(fingerprint(loads_strict(model_bytes)) == fingerprint(expected_model), "model_bytes",
                 "judged model differs from supplied exact bytes; provide that model's bytes explicitly")
    # Detect observed files changing during this read, without locks or writes.
    for path, raw in observed.items():
        _require(path.read_bytes() == raw, str(path.relative_to(root)), "input changed while reading")
    bundle = json.dumps({"schema_version": "obligation-freeze-members/1", "files": freeze_members},
                        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"freeze_bytes": bundle, "model_bytes": model_bytes, "documents": documents,
            "answer_sources": [] if answer_sources is None else answer_sources}


def capture_run_snapshot(run_dir, *, answer_sources=None, model_bytes=None):
    """Capture a real run for a new ledger; failures never create a ledger.

    ``model_bytes`` may explicitly name the final package model. Omitting it
    means the exact on-disk extract/model.json, not its migrated/healed copy.
    Answer sources are explicit existing question/version identities.
    """
    try:
        return capture_snapshot(**_run_inputs(run_dir, answer_sources=answer_sources, model_bytes=model_bytes))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ObligationError(f"cannot capture run obligations inputs: {exc}") from exc


def run_report(run_dir, *, model_bytes=None, expected_model=None):
    """Read exchange/obligations.json and judge it against the current real run.

    Read-only and opt-in: absence is ``not_recorded``, not an empty successful
    list. Unavailable inputs/invalid JSON are addressed, never silently repaired.
    The ledger's exact old answer identities are resolved again; no latest-
    answer fallback is permitted. A missing coverage report does not prevent a
    caller from using this adapter directly.
    """
    from cdpm.model import loads_strict
    report = _base_report()
    path = None
    try:
        path = _local_file(Path(run_dir).resolve(), LEDGER_REL)
        if not path.exists():
            report["form"] = "not_recorded"
            report["issues"].append({"code": "ledger_missing", "path": LEDGER_REL})
            return report
        raw = path.read_bytes()
        ledger = _object(loads_strict(raw), LEDGER_REL)
        snapshot = _object(ledger.get("snapshot"), "ledger.snapshot")
        units = _list(snapshot.get("units"), "ledger.snapshot.units")
        answers = []
        for unit in units:
            _object(unit, "ledger.snapshot.units[]")
            if unit.get("kind") == "answer":
                answers.append(_answer_identity(unit.get("source")))
        inputs = _run_inputs(run_dir, answer_sources=answers, model_bytes=model_bytes,
                             expected_model=expected_model)
        report = evaluate(ledger, **inputs)
        _require(path.read_bytes() == raw, LEDGER_REL, "ledger changed while reading")
        report["ledger"] = {"path": LEDGER_REL, "sha256": _bytes_sha(raw, LEDGER_REL)}
        report["input_binding"] = {"model_sha256": _bytes_sha(inputs["model_bytes"], "model"),
                                   "freeze_sha256": _bytes_sha(inputs["freeze_bytes"], "freeze"),
                                   "source_files": len(inputs["documents"])}
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report = _base_report()
        report["form"] = "unavailable"
        report["freshness"] = "stale_or_unresolved"
        report["issues"].append({"code": "run_inputs_unavailable", "path": LEDGER_REL,
                                 "detail": str(exc)})
        return report


def _ids(rows, path):
    out = {}
    for row in _list(rows, path):
        _object(row, path)
        rid = _text(row.get("id"), path + ".id")
        _require(rid not in out, f"{path}/{rid}", "duplicate ID")
        out[rid] = row
    return out


def _strings(value, path):
    rows = _list(value, path)
    for row in rows:
        _text(row, path)
    _require(len(rows) == len(set(rows)), path, "duplicate entry")
    return set(rows)


def _decision(row, path):
    _text(row.get("author"), path + ".author")
    _text(row.get("reason"), path + ".reason")


def _components(obligation, path):
    for name in ("subject", "statement", "modality"):
        _text(obligation.get(name), path + "." + name)
    _require(obligation["modality"] in {"requirement", "prohibition", "permission", "unknown"},
             path + ".modality", "unknown modality")
    components = {"statement"}
    for field in ("conditions", "exceptions"):
        for key, item in _ids(obligation.get(field), path + "." + field).items():
            _text(item.get("text"), path + "." + field + ".text")
            components.add(f"{field}/{key}")
    return components


def _pointer(row, pointer, path):
    _require(isinstance(pointer, str) and pointer.startswith("/") and len(pointer) > 1,
             path, "expected non-root row-relative JSON pointer")
    node = row
    for raw in pointer[1:].split("/"):
        _require(not re.search(r"~(?![01])", raw), path, "invalid pointer escape")
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            _require(token in node, path, "field does not exist")
            node = node[token]
        elif isinstance(node, list):
            _require(bool(re.fullmatch(r"0|[1-9][0-9]*", token)), path, "invalid array index")
            index = int(token)
            _require(index < len(node), path, "array index out of range")
            node = node[index]
        else:
            raise ObligationError(f"{path}: cannot descend into scalar")
    return node


def _target_source(row, pointer):
    """Find an existing cell's provenance; never manufacture it from a link."""
    parts = pointer.split("/")
    for stop in range(len(parts), 1, -1):
        node = _pointer(row, "/".join(parts[:stop]), "target provenance")
        if isinstance(node, dict) and isinstance(node.get("source"), dict):
            return node["source"]
    return None


def _base_report():
    return {"schema_version": REPORT_SCHEMA, "form": "valid", "freshness": "unknown",
            "meaning": "not_proven", "independence": "not_verified", "issues": [],
            "readers": [], "comparisons": [], "reverse_links": [],
            "expert_review": {"status": "not_reviewed"}}


def evaluate(ledger, *, freeze_bytes, model_bytes, documents, answer_sources=None):
    """Check the carrier and current inputs, returning gaps without a global PASS.

    Links name ``reader, obligation, model_sha256, status, author, reason`` and
    targets ``collection, row_id, pointer, component``. Components are
    ``statement``, ``conditions/<id>``, ``exceptions/<id>``. Question links are
    always unresolved representation, even when a question has an answer.
    A new applied field must be linked explicitly. No question closes a norm.

    A source/input mismatch invalidates *all* current links conservatively;
    prior results remain in the unchanged ledger rather than being refreshed.
    """
    report = _base_report()
    try:
        _evaluate(ledger, report, freeze_bytes, model_bytes, documents,
                  [] if answer_sources is None else answer_sources)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # Do not expose partially counted links as valid after a form error.
        report = _base_report()
        report["form"] = "invalid"
        report["issues"].append({"code": "invalid_form", "path": "ledger", "detail": str(exc)})
    return report


def _evaluate(ledger, out, freeze, model_bytes, documents, answer_sources):
    _object(ledger, "ledger")
    _require(ledger.get("schema_version") == SCHEMA, "schema_version", "unsupported version")
    fingerprint(ledger)  # reject non-JSON/nonfinite in otherwise unexamined metadata
    saved = _object(ledger.get("snapshot"), "snapshot")
    _require(saved.get("schema_version") == SNAPSHOT_SCHEMA, "snapshot", "unsupported version")
    _sha(saved.get("freeze_sha256"), "snapshot.freeze_sha256")
    _sha(saved.get("model_sha256"), "snapshot.model_sha256")
    units = _ids(saved.get("units"), "snapshot.units")
    snapshot_sha = fingerprint(saved)
    try:
        current, texts, model = _capture(freeze, model_bytes, documents, answer_sources)
    except ObligationError as exc:
        current, texts, model = None, {}, {}
        out["issues"].append({"code": "inputs_unresolved", "path": "snapshot", "detail": str(exc)})
    fresh = current == saved
    out["freshness"] = "current" if fresh else "stale_or_unresolved"
    if current is not None and not fresh:
        out["issues"].append({"code": "snapshot_changed", "path": "snapshot"})
    scope = _object(ledger.get("scope"), "scope")
    included = _strings(scope.get("included"), "scope.included")
    _require(included <= units.keys(), "scope.included", "unknown input unit")
    excluded = set()
    for entry in _list(scope.get("excluded"), "scope.excluded"):
        _object(entry, "scope.excluded")
        key = _text(entry.get("unit"), "scope.excluded.unit")
        _require(key in units and key not in excluded and key not in included,
                 "scope.excluded", "unknown, duplicate or included unit")
        _decision(entry, "scope.excluded")
        excluded.add(key)
    segmented = {unit.get("sid") for unit in units.values() if unit.get("kind") == "document"}
    out["scope"] = {"unaccounted": sorted(units.keys() - included - excluded),
                    "unsegmented_documents": sorted(d["sid"] for d in saved.get("documents", [])
                                                     if d["sid"] not in segmented),
                    "excluded": copy.deepcopy(scope["excluded"]), "exclusion_authority": "not_verified"}
    readers = _ids(ledger.get("readers"), "readers")
    _require(len(readers) == 2, "readers", "exactly two separate reader results required")
    obligations, components, result_by_reader = {}, {}, {}
    exposures = []
    for reader_id, reader in readers.items():
        path = f"readers/{reader_id}"
        for field in ("actor", "method", "version"):
            _text(reader.get(field), path + "." + field)
        # Machine versions select a known evidence contract. Do not treat an
        # unknown revision as the legacy, address-optional schema. Authored
        # reader versions outside this namespace retain their old contract.
        if reader["version"].startswith("obligation-readings/"):
            _require(reader["version"] in {"obligation-readings/1", "obligation-readings/2"},
                     path + ".version", "unsupported machine reading version")
        _require(reader.get("input_sha256") == snapshot_sha, path, "reader used a different snapshot")
        _require(reader.get("result_sha256") == seal_reader(reader)["result_sha256"],
                 path, "reader result changed after sealing")
        exposure = _object(reader.get("exposure"), path + ".exposure")
        for field in ("other_reader", "model"):
            _require(exposure.get(field) is None or type(exposure.get(field)) is bool,
                     path + ".exposure." + field, "expected boolean or unknown (null)")
            exposures.append(exposure.get(field))
        reviewed = _strings(reader.get("reviewed_units"), path + ".reviewed_units")
        _require(reviewed <= included, path, "reviewed unit outside included scope")
        own = _ids(reader.get("obligations"), path + ".obligations")
        obligations[reader_id] = own
        stats = {"reader": reader_id, "total": len(own), "linked": 0,
                 "missing": [], "unreviewed_units": sorted(included - reviewed),
                 "unmatched": [], "items": []}
        result_by_reader[reader_id] = stats
        out["readers"].append(stats)
        if not own:
            out["issues"].append({"code": "empty_extraction_unverified", "path": path})
        for oid, obligation in own.items():
            here = path + "/" + oid
            components[(reader_id, oid)] = _components(obligation, here)
            dependencies = _strings(obligation.get("depends_on"), here + ".depends_on")
            _require(dependencies <= own.keys() and oid not in dependencies,
                     here, "unresolved/self dependency")
            refs = _list(obligation.get("sources"), here + ".sources")
            _require(bool(refs), here, "source fragments required")
            for ref in refs:
                _object(ref, here + ".source")
                key = _text(ref.get("unit"), here + ".source.unit")
                _require(key in included and key in reviewed, here, "source outside reviewed scope")
                fragments = _list(ref.get("fragments"), here + ".fragments")
                _require(bool(fragments), here, "empty fragments")
                for fragment in fragments:
                    _object(fragment, here + ".fragment")
                    # Version 1 used the same reading method without address
                    # evidence. Require the v2 contract without migrating it.
                    if reader["version"] == "obligation-readings/2":
                        _require("address" in fragment, here,
                                 "address evidence required for obligation-readings/2")
                    start, end = fragment.get("start"), fragment.get("end")
                    _require(type(start) is int and type(end) is int and 0 <= start < end,
                             here, "expected UTF-8 byte range [start,end)")
                    quote = _text(fragment.get("quote"), here + ".quote")
                    if fresh:
                        _require(end <= len(texts[key]) and texts[key][start:end] == quote.encode("utf-8"),
                                 here, "fragment does not reproduce exact source bytes")
                    _address_evidence(fragment, units[key], texts.get(key), fresh, here)
    if any(value is True for value in exposures):
        out["independence"] = "contradicted_by_declaration"
    elif all(value is False for value in exposures):
        out["independence"] = "declared_only"
    covered = {key: set() for key in components}
    disputed, questioned, unverified_answers = set(), set(), set()
    for index, link in enumerate(_list(ledger.get("links"), "links")):
        path = f"links/{index}"
        _object(link, path)
        key = (link.get("reader"), link.get("obligation"))
        _require(key in components, path, "unknown obligation")
        _decision(link, path)
        _require(link.get("status") in {"declared", "disputed", "open_question"}, path, "unknown link status")
        _sha(link.get("model_sha256"), path + ".model_sha256")
        targets = _list(link.get("targets"), path + ".targets")
        _require(bool(targets), path, "empty targets")
        if link["status"] == "disputed":
            disputed.add(key)
        if link["status"] == "open_question":
            questioned.add(key)
        for target in targets:
            _object(target, path + ".target")
            collection, rid = target.get("collection"), target.get("row_id")
            _require(collection in _COLLECTIONS, path, "unknown collection")
            _text(rid, path + ".row_id")
            component = _text(target.get("component"), path + ".component")
            _require(component in components[key], path, "unknown obligation component")
            if not fresh or link["model_sha256"] != saved["model_sha256"]:
                out["issues"].append({"code": "stale_link", "path": path})
                continue
            rows = _ids(model.get(collection, []), "model." + collection)
            _require(rid in rows, path, "target row does not exist")
            _pointer(rows[rid], target.get("pointer"), path + ".pointer")
            existing_source = _target_source(rows[rid], target["pointer"])
            obligation = obligations[key[0]][key[1]]
            for ref in obligation["sources"]:
                unit = units[ref["unit"]]
                if unit.get("kind") == "answer" and collection != "questions":
                    _require(rid in unit["targets"], path, "answer edition does not target this row")
                    if existing_source is not None:
                        _require(_answer_identity(existing_source) == unit["source"], path,
                                 "target cell provenance disagrees with answer source")
                        from cdpm import provenance
                        question, _ = provenance.resolve_source(model, existing_source)
                        parts = target["pointer"].split("/")
                        if parts[1:3] == ["template_fit", "params"] and len(parts) >= 4:
                            cell = _pointer(rows[rid], "/".join(parts[:4]), path)
                            name = parts[3].replace("~1", "/").replace("~0", "~")
                            problems = provenance._source_shape_problems(question, name, cell, existing_source)
                            _require(not problems, path, "; ".join(problems))
                    elif unit.get("answer_status") == "применён частично":
                        # A generic text link cannot establish which part was
                        # applied. Do not invent a processed subset of targets.
                        unverified_answers.add(key)
            reverse = {"collection": collection, "row_id": rid, "pointer": target["pointer"],
                       "reader": key[0], "obligation": key[1], "component": component}
            out["reverse_links"].append(reverse)
            if collection == "questions":
                questioned.add(key)
            elif link["status"] == "declared":
                covered[key].add(component)
    for key, wanted in components.items():
        absent = sorted(wanted - covered[key])
        status = ("stale" if not fresh else "disputed" if key in disputed else
                  "open_question" if key in questioned else
                  "unverified_answer_application" if key in unverified_answers else
                  "missing" if absent else "linked")
        row = {"id": key[1], "status": status, "missing_components": absent,
               "correspondence": "declared_not_semantically_verified"}
        stats = result_by_reader[key[0]]
        stats["items"].append(row)
        if status == "linked":
            stats["linked"] += 1
        else:
            stats["missing"].append(key[1])
    matched = {key: set() for key in readers}
    for cid, match in _ids(ledger.get("comparisons"), "comparisons").items():
        _decision(match, "comparisons/" + cid)
        _require(match.get("status") in {"agreed", "disputed"}, cid, "unknown comparison status")
        sides = []
        payloads = []
        for side in ("left", "right"):
            ref = _object(match.get(side), cid + "." + side)
            reader_id = ref.get("reader")
            _require(reader_id in readers, cid, "unknown reader")
            names = _strings(ref.get("obligations"), cid + "." + side)
            _require(bool(names) and names <= obligations[reader_id].keys(), cid, "unknown/empty obligations")
            _require(not names & matched[reader_id], cid, "obligation compared more than once")
            matched[reader_id].update(names)
            sides.append(reader_id)
            payloads.append([{p: comparison_payload(obligations[reader_id][oid])[p] for p in _PARTS}
                             for oid in sorted(names)])
        _require(sides[0] != sides[1], cid, "comparison must cross reader results")
        out["comparisons"].append({"id": cid, "decision": match["status"],
                                   "identical_payload": payloads[0] == payloads[1],
                                   "different_parts": [p for p in _PARTS
                                                       if [x[p] for x in payloads[0]] != [x[p] for x in payloads[1]]],
                                   "meaning": "not_verified", "reason": match["reason"], "author": match["author"]})
    for rid, own in obligations.items():
        result_by_reader[rid]["unmatched"] = sorted(own.keys() - matched[rid])
    review = ledger.get("expert_review")
    if review is not None:
        _object(review, "expert_review")
        for field in ("reviewer", "decision", "reason"):
            _text(review.get(field), "expert_review." + field)
        _require(review["decision"] in {"accepted", "rejected", "open"}, "expert_review", "unknown decision")
        _list(review.get("issues"), "expert_review.issues")
        if "reviewed_content_sha256" in review:
            _sha(review["reviewed_content_sha256"], "expert_review.reviewed_content_sha256")
        same = (review.get("snapshot_sha256") == snapshot_sha and
                review.get("scope_sha256") == fingerprint(scope) and
                review.get("reviewed_content_sha256") == review_content_sha256(ledger) and fresh)
        out["expert_review"] = {"status": "recorded" if same else "stale", "record": copy.deepcopy(review),
                                "authority": "not_verified"}
