"""A06/06 local preflight: native draft.3 criterion -> actual Builder.price.

Synthetic support from the existing native fixture is NOT stand approval.
No JVM, service, solve/search, release or independent acceptance is claimed.
The two-day world is a declared catalogue stand input, unchanged here.
"""
import copy

import pytest

from cdpm.gates import gate_task_package as producer
from vmp import criteria, stand_adapters as sa, stand_tasks as st
from webapp import task_package as consumer
import _pairing_builder as pb
from test_a06_local_package_closure import native_package, isolated, unchanged_sources


@pytest.mark.parametrize("carrier,rate", [("document", 77), ("answer", 123), ("prose", 100)])
def test_native_received_criterion_prices_actual_two_day_chain(tmp_path, monkeypatch, carrier, rate):
    path, _, cat, _ = native_package(tmp_path, monkeypatch, carrier)
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["direction"] == "minimize"
    assert criterion["rate_per_duty"] == rate
    world = copy.deepcopy(cat.criteria()["min_person_days"]["stand_tasks"][0]["world"])
    before = copy.deepcopy((world, criterion))
    payload, args = sa.world_to_builder(world)
    argv, provenance = [], {}
    assert criteria.apply_criterion(criterion, argv, provenance) is None
    st._apply_cli(args, argv)
    builder = pb.Builder(payload, args)
    chain, dh_legs = sa.chain_of(builder, world)
    assert len(chain) == 2 and dh_legs == []
    assert builder.crew_size(chain) == 1
    priced = builder.price(chain, dh_legs, 1)
    # Independently count the declared days (Oct 1 and Oct 2), not a returned
    # catalogue score or a builder-derived expected value.
    assert priced["criterion"] == 2 * rate
    assert priced["cost"] == 2 * rate
    assert (world, criterion) == before
    forged = dict(criterion, direction="maximize")
    bad_args, bad_provenance = ["--existing", "1"], {"existing": 1}
    with pytest.raises(criteria.CriteriaError, match="minimize"):
        criteria.apply_criterion(forged, bad_args, bad_provenance)
    assert bad_args == ["--existing", "1"] and bad_provenance == {"existing": 1}


def test_declared_draft3_builder_criterion_stand_rows(tmp_path, monkeypatch):
    _, _, cat, _ = native_package(tmp_path, monkeypatch)
    stand_dir = tmp_path / "builder-stand"
    stand_dir.mkdir()  # CLI _run prepares this; the lower-level API expects it.
    rows = st.run_tasks(catalog=cat, kinds=["builder_method"],
                        records=["min_person_days", "rank_normalized_cost", "total_pairing_cost"],
                        tmp_dir=stand_dir)
    assert len(rows) == 3
    assert all(row["result"] == "passed" for row in rows), [(r["record"], r["result"], r["note"]) for r in rows]
    assert {(row["record"], row["got"]) for row in rows} == {
        ("criteria/min_person_days", 14),
        ("criteria/rank_normalized_cost", 14400),
        ("criteria/total_pairing_cost", 15),
    }
    assert all(row["run_id"] is None and row["package_fingerprint"] is None for row in rows)
