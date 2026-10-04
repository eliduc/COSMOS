"""Six saved VN-050 proposals through the current offline patch receiver.

The initial row is read unchanged from authentic prompt28. Later saved rows
are checkpoints, not replacements for the evolving test document. Only this
row's six addressed answers are replayed, not their sibling answers, the full
run, registry/arbitration lifecycle, or subsequent calls. After refusing call51
the corrected branch differs from saved call54 (which saw legacy A); those
later prompts cannot be replayed as a new live continuation.

The positive control reuses the already established explicit consistent full
proposal helper. That proposal is NOT a saved model reply, an independent
semantic oracle, a catalogue migration, or proof that all A05 is complete.

External pinned fixtures can live outside the checkout: set COSMOS_AUDIT_ROOT
to their absolute cdpm-audit-20260911 root. Without it the original scratchpad
layout remains supported. Missing or changed evidence is a failure, not a skip
or a successful replay; none of the fourteen input hashes is relaxed.
"""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, exchange as ex, extract, llm, model as m


def _audit_root():
    configured = os.environ.get("COSMOS_AUDIT_ROOT")
    if configured is None:
        return Path(__file__).resolve().parents[2] / "cdpm-audit-20260911"
    root = Path(configured).expanduser()
    if not configured.strip() or not root.is_absolute():
        raise ValueError("COSMOS_AUDIT_ROOT must be a non-empty absolute fixture path")
    return root.resolve()


AUDIT_ROOT = _audit_root()
INPUT = (AUDIT_ROOT / "exchange-COSMOS/h0-24f-live-20260917").resolve()
BODIES = INPUT / "izvlechenie-run-9053/journal-calls/bodies"
RID = "R-S2-лист-«VMP-H0-2»-строка-6"
ANCHOR = "лист «VMP-H0-2», строка 6"
NAME = "workload_spread_threshold"
CATALOG_FP = "5534196f824aff80156e33cd5fa741c869c5fd8d23e48b39e295d15d73e8b7d9"
V00_SHA = "80debaccffb89f7695e0b6e5c05f0dc973a13451b062c3d6dc95761cfa1b00b6"
HELPER_SHA = "9fe8d4ca73599ec871c6dadfa042ab5df72af3e0c6dd557e14c7a7f92efe46da"
# The immutable input manifest resolves every dependency to a full local path.
# n, receiver round, remark id, prompt SHA256, reply SHA256
CALLS = (
    (28, 1, "A1-011",
     "6e64e1a07322a191749e24d1396bf44080431c4117878f0fe4f497ceb8d45ff1",
     "e9de8df3033bc252e67ed98d71638169f65265b4f51887c8bbdc75116edd01a9"),
    (31, 2, "A2-006",
     "79676b7114f69886c230f1a9e34ae0cf3abccdb6d4fc558df176a83ac561466b",
     "85bea2a218aa77747615bd44e4ff622c91b1f25ec70e3fa5df06ef63bd19a86c"),
    (34, 3, "A3-005",
     "6213b449f76927231636727179069ecfd5cd6590b3eeef926b5a8325beb6e762",
     "d79be9eb8273137e06e7e9bb6bb42a9491dc71c745881b1ea17dbf8733057d58"),
    (42, 4, "A4-017",
     "bfaf1bf3d51a58f3e51414b8bf33094b2a3993a644ab3087ce2fa8ee658697a3",
     "a68d829f56096e19049b4057b42deea2756b835000289c51353f127422174baf"),
    (47, 5, "A5-013",
     "50690137c1cc5f5e106cdf4afa4fbe4123158d999b1dc1f9270390213f19b7b8",
     "b9cbc43f36e2cd4c8436f446c547d935a6fd3608ed91e101421fe2408dcbe0ec"),
    (51, 99, "R1-A1-015",
     "7e610b6b481b85e80a137cee4c42a2deda2a2bb49745e141a4072242f6586d5b",
     "cace215ae3c249a8833ce11770a7970a2a2e31ab1a444914451b2596cba6402b"),
)
INPUT_FILES = tuple((BODIES / digest, digest)
                    for call in CALLS for digest in call[3:]) + (
    (INPUT / "model-v00-run-9053.json", V00_SHA),
    ((AUDIT_ROOT / "exchange-GPT/A05-9053-RAW-REPLAY-20260918-agent.py").resolve(), HELPER_SHA),
)


def _verified_text(path, sha256):
    assert path.is_absolute(), str(path)
    assert path.is_file(), (
        f"Saved A05 evidence is missing: {path}. Set COSMOS_AUDIT_ROOT to the "
        "absolute cdpm-audit-20260911 fixture root; this is not a completed replay.")
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == sha256, (str(path), "expected", sha256, "actual", actual)
    return raw.decode("utf-8")


def _one(items, predicate):
    selected = [item for item in items if predicate(item)]
    assert len(selected) == 1, selected
    return selected[0]


def _saved_call(record, verified):
    number, round_no, remark_id, prompt_sha, reply_sha = record
    prompt = verified[BODIES / prompt_sha].replace("\r\n", "\n")
    rows_text = prompt.split("Затронутые строки свода:\n", 1)[1].split("\n\nКлаузы-источники:", 1)[0]
    rows = [m.loads_strict(line) for line in rows_text.splitlines() if line.strip()]
    row = _one(rows, lambda r: r["id"] == RID)
    # Ordinary replies and the arbiter enforcement prompt use distinct
    # headings; neither changes the verbatim JSON data underneath it.
    heading = "Замечания к исполнению:\n" if number == 51 else "Замечания аудитора:\n"
    assert heading in prompt, (number, str(BODIES / prompt_sha))
    remarks_text = prompt.split(heading, 1)[1].split("\n\nЗатронутые строки свода:", 1)[0]
    remark = _one(m.loads_strict(remarks_text), lambda r: r["id"] == remark_id)
    answers = llm.parse_json_reply(verified[BODIES / reply_sha])["answers"]
    answer = _one(answers, lambda a: a.get("remark") == remark_id)
    clause = _one(prompt.splitlines(), lambda line: line.startswith(f"[{ANCHOR}] ")).split("] ", 1)[1]
    assert remark["target"]["rule"] == answer["patch"]["rule_id"] == RID
    assert remark["defect_class"] == "искажение"
    assert answer["verdict"] == "ПРИНЯТО"
    assert row["template_fit"]["category"] == "C"
    assert row["params"][0]["relation"] == "min"
    return {"number": number, "round": round_no, "remark": remark,
            "answer": answer, "row": row, "clause": clause}


@pytest.fixture
def saved_sequence(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(Path(__file__).resolve().parents[1] / "catalog"))
    cb.forget()
    try:
        assert cb.catalog().version() == "0.5.0"
        assert cb.catalog().fingerprint() == CATALOG_FP
        verified = {path: _verified_text(path, digest) for path, digest in INPUT_FILES}
        calls = [_saved_call(record, verified) for record in CALLS]
        saved = m.loads_strict(verified[INPUT / "model-v00-run-9053.json"])
        source = _one(saved["sources"], lambda s: s["sid"] == "S2")
        # The empty normal envelope is synthetic; the initial source row is
        # not. No assignment to its category, params, pin or evidence occurs.
        doc = extract._Assembler("Saved A05 six-proposal subdocument", [copy.deepcopy(source)]).doc
        doc["rules"] = [copy.deepcopy(calls[0]["row"])]
        assert m.validate_schema(doc) == m.validate_model(doc) == []
        assert m.carrier_problems(doc) == []
        assert all(call["row"] == calls[0]["row"] for call in calls)
        assert all(call["clause"] == calls[0]["clause"] for call in calls)
        yield doc, calls, saved
    finally:
        cb.forget()


def test_six_saved_proposals_do_not_corrupt_the_evolving_row(saved_sequence):
    doc, calls, _saved = saved_sequence
    original = copy.deepcopy(doc)
    for call in calls:
        # Compare the prior RESULT to the next saved input; never reset the
        # result from that checkpoint, which would hide partial corruption.
        assert doc["rules"][0] == call["row"], call["number"]
        before = copy.deepcopy(doc)
        pack = {"round": call["round"], "remarks": [copy.deepcopy(call["remark"])]}
        response = {"round": call["round"], "answers": [copy.deepcopy(call["answer"])]}
        assert ex.validate_remarks(pack, doc) == []
        executed = set()
        new, targets, rejected, tokens = ex._apply_patches(
            doc, pack, response, call["round"], valid_anchors={("S2", ANCHOR)},
            clause_texts={("S2", ANCHOR): call["clause"]},
            convert_reading_choice=call["number"] != 51, executed_out=executed)
        assert doc == before, call["number"]
        assert len(rejected) == 1 and rejected[0]["answer"] == call["remark"]["id"], rejected
        reasons = " ".join(rejected[0]["reasons"])
        assert RID in reasons, (call["number"], rejected)
        if call["number"] == 51:
            assert all(word in reasons for word in (NAME, "два носителя", "min", "max")), rejected
        else:
            # These raw typed proposals omit the mandatory pinned contract;
            # absence of an exception, or an unrelated refusal, is not proof.
            assert "family_contract" in reasons, (call["number"], rejected)
        assert targets == set() and tokens == [] and executed == set(), (call["number"], targets, tokens, executed)
        for field in ("template_fit", "params", "weight", "weight_quote", "sources"):
            assert new["rules"][0].get(field) == before["rules"][0].get(field), (call["number"], field)
        # Rejected answers are transactional in this receiver. Its legitimate
        # candidate metadata normalization must not be published on rejection.
        assert new == before, call["number"]
        assert m.validate_schema(new) == m.validate_model(new) == []
        assert m.carrier_problems(new) == []
        doc = new
    assert doc == original
    assert doc["rules"][0]["template_fit"]["category"] == "C"
    # Stop here. Saved call54 starts from legacy A; it is a different branch.


def test_existing_explicit_consistent_proposal_remains_a_healthy_control(saved_sequence):
    doc, calls, saved = saved_sequence
    path, digest = INPUT_FILES[-1]
    _verified_text(path, digest)
    spec = importlib.util.spec_from_file_location("a05_saved_call51_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    last = calls[-1]
    # Reuse the previous exact-source fixture's explicit NEW full proposal.
    # It is deliberately not inserted into the six saved-reply sequence.
    case = (copy.deepcopy(doc), copy.deepcopy(last["remark"]),
            copy.deepcopy(last["answer"]), last["clause"], saved)
    module.test_explicit_consistent_full_patch_has_a_healthy_operator_path(case)
