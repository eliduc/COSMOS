"""P0 sections 3.3/3.7: received objective criterion -> real builder arguments.

Uses the existing synthetic 0.6.0 catalogue/support fixture and its real ZIP,
producer gate and consumer. Support evidence is synthetic, not a stand result.
The criterion is taken ONLY from the actual consumer output. Applied limits
and payload are independent input fixtures: no coverage solver, full button,
HTTP, LLM, JVM or builder execution is claimed. Production SPEC stays 1.18.
"""
import copy

import pytest
import template_catalog as tc

from cdpm import catalog_bridge as cb, export_task_package as writer
from cdpm.gates import gate_task_package as gate
from vmp import catalog_access as ca, pairing_input
from webapp import task_package as consumer
from test_a06_direction_card import package
from tests.vmp.test_pairing_input import APPLIED, PAYLOAD


@pytest.fixture(autouse=True)
def isolated_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "")
    for key in ("CREW_CHANNEL_URL", "CREW_CHANNEL_TOKEN", "CREW_SOLVER_URL"):
        monkeypatch.delenv(key, raising=False)
    cb.forget()
    ca.forget()
    tc.forget_current()
    yield
    cb.forget()
    ca.forget()
    tc.forget_current()


def test_received_criterion_reaches_builder_and_direction_tampering_refuses(
        tmp_path, monkeypatch):
    path, catalogue = package(tmp_path, monkeypatch, spec="1.20",
                              direction="minimize", carrier="document")
    problems, _ = gate.check(path)
    assert problems == [], problems
    # Producer gate was called in-process above; all receiver judges stay real.
    received = consumer.load(path, run_gate=False)
    assert writer.SPEC_VERSION == "1.18", "no release activation in this probe"
    assert received["manifest"]["spec_version"] == "1.20"
    assert received["landing_projection"]["archive_only"] is False
    assert received["support_projection"]["judged"] is True
    assert received["objective_projection"]["judged"] is True
    assert received["objective_projection"]["catalog"]["fingerprint"] == catalogue.fingerprint()

    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["kind"] == "min_person_days"
    assert criterion["direction"] == "minimize"
    assert criterion["rate_per_duty"] == 77
    assert criterion["source_rule"] == "O-1"
    assert received["objective"]["pairing"]["direction"] == "minimize"
    before = copy.deepcopy(received)

    args, provenance = pairing_input.builder_args(
        copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD), budget_s=60,
        criterion=criterion)
    assert args.count("--w-span-day") == 1
    assert args[args.index("--w-span-day") + 1] == "77"
    assert provenance["--w-span-day"] == {
        "value": 77, "from": "критерий пакета: O-1"}
    for flag in ("--w-duty", "--w-night", "--w-dh", "--w-chain"):
        assert args.count(flag) == 1
        assert args[args.index(flag) + 1] == "0", (flag, args)
    assert received == before, "the builder must not rewrite the received criterion"

    # Tamper only after proving the same received criterion has a healthy route.
    forged = copy.deepcopy(criterion)
    forged["direction"] = "maximize"
    with pytest.raises(pairing_input.PairingInputError) as refused:
        pairing_input.builder_args(
            copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD), budget_s=60,
            criterion=forged)
    words = str(refused.value)
    assert "'maximize'" in words and "каталог объявил minimize" in words, words
    assert received == before
