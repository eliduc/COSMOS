"""Read-only A12 field backlinks, separate from the canonical VMP form.

The input report must come from obligations.run_report for these exact model
bytes. Current means byte-bound, never semantically complete or approved.
"""
from __future__ import annotations

import copy
import hashlib

from cdpm import model, obligations as ob

_STATUSES = {"linked", "missing", "disputed", "open_question",
             "unverified_answer_application", "stale"}


def from_report(*, model_bytes: bytes, report: dict) -> dict:
    """Group checked backlinks by exact model field without modifying inputs."""
    if not isinstance(model_bytes, bytes):
        raise TypeError("model_bytes must be exact bytes")
    digest = hashlib.sha256(model_bytes).hexdigest()
    out = {"schema_version": "obligation-field-view/1", "model_sha256": digest,
           "status": "unavailable", "meaning": "not_proven",
           "independence": "not_verified", "expert_review": {"status": "not_reviewed"},
           "fields": [], "issues": []}
    if not isinstance(report, dict):
        out["issues"].append({"code": "field_report_invalid"})
        return out
    out["issues"] = copy.deepcopy(report.get("issues", []))
    out["independence"] = copy.deepcopy(report.get("independence", "not_verified"))
    out["expert_review"] = copy.deepcopy(report.get("expert_review", {"status": "not_reviewed"}))
    if report.get("form") == "not_recorded":
        out["status"] = "not_recorded"
        return out
    if report.get("form") != "valid":
        return out
    if (report.get("freshness") != "current"
            or not isinstance(report.get("input_binding"), dict)
            or report["input_binding"].get("model_sha256") != digest):
        out["status"] = "stale_or_unresolved"
        out["issues"].append({"code": "field_model_binding_mismatch"})
        return out
    try:
        doc = ob._object(model.loads_strict(model_bytes), "model")
        item_states = {}
        for reader in ob._list(report.get("readers"), "report.readers"):
            ob._object(reader, "report.reader")
            rid = ob._text(reader.get("reader"), "report.reader.id")
            for item in ob._list(reader.get("items"), "report.reader.items"):
                ob._object(item, "report.reader.item")
                oid = ob._text(item.get("id"), "report.reader.item.id")
                key = (rid, oid)
                ob._require(key not in item_states, "report.reader.item", "duplicate reader obligation")
                ob._require(item.get("status") in _STATUSES,
                            "report.reader.item", "unknown obligation status")
                item_states[key] = item["status"]
        fields = {}
        for ref in ob._list(report.get("reverse_links"), "report.reverse_links"):
            ob._object(ref, "report.reverse_link")
            collection = ref.get("collection")
            ob._require(collection in ob._COLLECTIONS, "report.reverse_link", "unknown collection")
            row_id = ob._text(ref.get("row_id"), "report.reverse_link.row_id")
            pointer = ob._text(ref.get("pointer"), "report.reverse_link.pointer")
            rows = ob._ids(doc.get(collection, []), "model." + collection)
            ob._require(row_id in rows, "report.reverse_link", "missing target row")
            value = ob._pointer(rows[row_id], pointer, "report.reverse_link.pointer")
            reader = ob._text(ref.get("reader"), "report.reverse_link.reader")
            obligation = ob._text(ref.get("obligation"), "report.reverse_link.obligation")
            component = ob._text(ref.get("component"), "report.reverse_link.component")
            ob._require((reader, obligation) in item_states,
                        "report.reverse_link", "missing obligation disposition")
            key = (collection, row_id, pointer)
            field = fields.setdefault(key, {"collection": collection, "row_id": row_id,
                                            "pointer": pointer, "value": copy.deepcopy(value),
                                            "links": []})
            field["links"].append({"reader": reader, "obligation": obligation,
                                   "component": component, "status": item_states[(reader, obligation)],
                                   "correspondence": "declared_not_semantically_verified"})
        out["fields"] = list(fields.values())
        out["status"] = "current"
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        out["fields"] = []
        out["status"] = "unavailable"
        out["issues"].append({"code": "field_report_invalid", "detail": str(exc)})
    return out
