"""Synthetic declarations are code-authored; real answers still need books."""
import copy
import json

import pytest

from cdpm import source_answer_evidence as evidence
from vmp import stand_solver


@pytest.mark.parametrize("part", ["3a", "3b"])
@pytest.mark.parametrize("decline", [False, True])
def test_synthetic_goal_declares_code_origin_without_invented_book(tmp_path, part, decline):
    question = stand_solver._goal_question(part, ["R-STAND-example"], decline=decline)
    answer = question["answer"]
    assert answer["closed_by"] == "code"
    assert answer["revision"] == 1 and answer["status"] == "применён"
    assert answer["goal_decline"] is decline
    assert "не ответ авиакомпании" in answer["raw"]
    assert "answer_version" not in answer
    doc = {"meta": {"revision": 1}, "rules": [], "questions": [question]}
    assert evidence.export_files(tmp_path, json.dumps(doc).encode(), "run-stand") == {}

    # The synthetic prose/goal kind/revision/run name is not a bypass. If it is
    # represented as an actual applied answer it needs ordinary book evidence.
    actual = copy.deepcopy(doc)
    actual["questions"][0]["answer"].pop("closed_by")
    with pytest.raises(evidence.EvidenceError, match="ревизия"):
        evidence.export_files(tmp_path, json.dumps(actual).encode(), "run-stand")
