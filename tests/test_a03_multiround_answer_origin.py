"""A03: accumulated exchange rounds preserve the active answer's origin.

Local deterministic calls, no model/solver/export. The existing single-round
builder is extended by actual round numbers and two real answer revisions.
"""
import copy

import pytest

from cdpm import answers as ans
from cdpm import exchange as ex
from cdpm import model as m
from cdpm import provenance as prov
from test_cdpm_a03m_provenance import (
    CLAUSES, PARAM, QID, RAW_600, RAW_540, _answered, _cell, _rule,
)


def exchange_round(doc, rule, number):
    rid = f"A-{number:03d}"
    pack = {"round": number, "remarks": [{
        "id": rid, "severity": "КРИТИЧЕСКОЕ", "defect_class": "искажение",
        "target": {"rule": "R-R"},
        "source_refs": [{"sid": "S1", "anchor": "п.2"}],
        "text": "Уточнить пояснение к норме отдыха",
        "evidence": "«Минимальный отдых дома — 12:00»",
        "proposal": "сохранить точное происхождение ответа"}]}
    response = {"round": number, "answers": [{
        "remark": rid, "verdict": "ПРИНЯТО", "basis": "источник: «12:00»",
        "change": "уточнено пояснение",
        "patch": {"rule_id": "R-R", "rule": rule}}]}
    return ex._apply_patches(doc, pack, response, number, clause_texts=CLAUSES)


@pytest.mark.parametrize("forgery", ["wrong_version", "old_cell"])
def test_three_rounds_and_two_revisions_keep_current_answer_authoritative(forgery):
    doc, _ = _answered()
    qid = QID
    previous_cells = []
    for number, (raw, value) in enumerate([
            (RAW_600, 600), (RAW_540, 540),
            ("Теперь вне базы — 8 часов.", 480)], start=1):
        if number > 1:
            qid = ans.open_revision(doc, qid)["created"]
            assert ans.apply_answers(doc, {qid: raw})["applied"] == 1
        current = copy.deepcopy(_cell(doc))
        assert current["value"] == value
        assert current["source"]["question"]["id"] == qid
        assert current["source"]["root"] == QID

        healthy = copy.deepcopy(_rule(doc))
        healthy["note"] = f"Пояснение после круга {number}"
        before = copy.deepcopy(doc)
        doc, accepted, rejected, _ = exchange_round(doc, healthy, number)
        assert rejected == [] and accepted
        assert _rule(doc)["note"] == healthy["note"]
        assert _cell(doc) == current
        assert m.validate_schema(doc) + m.validate_model(doc) == []
        assert m.validate_fit_quotes(doc, CLAUSES) == []
        assert prov.package_problems(doc) == []
        resolved, resolved_raw = ans.resolve_source(doc, _cell(doc)["source"])
        assert resolved["id"] == qid and resolved_raw == raw
        assert _rule(before).get("note") != healthy["note"]

        bad = copy.deepcopy(_rule(doc))
        bad["note"] = "Эта сопутствующая правка тоже не должна примениться"
        if forgery == "old_cell" and previous_cells:
            bad["template_fit"]["params"][PARAM] = copy.deepcopy(previous_cells[-1])
        else:
            bad["template_fit"]["params"][PARAM]["source"]["version"]["fingerprint"] = "0" * 12
        preserved = copy.deepcopy(doc)
        refused, accepted, rejected, _ = exchange_round(doc, bad, number + 10)
        assert rejected and not accepted
        assert refused == preserved and doc == preserved
        assert _cell(refused) == current
        previous_cells.append(current)
