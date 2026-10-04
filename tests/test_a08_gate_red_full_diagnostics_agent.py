"""A08: a real gate-only refusal must retain its complete existing detail.

Reuse the registered in-memory receive handler and actual SOFT-only exporter,
gate, package reader and storage. The thirteen unlisted ZIP members trigger
the producer gate without corrupting any declared member. This is not socket
HTTP, middleware, operational readiness, an executor run or a catalogue release.
The temporary modern catalogue/support fixture has no released executor proof;
the healthy receipt must therefore keep launch disallowed.

The carrier is the existing ``detail`` field, already used in full by REFUSED
and its adjacent handler regression, not a newly invented response field.
"""
import hashlib
import io
import json
import zipfile

from cdpm.gates import gate_task_package as producer_gate
from test_a08_receive_handler_agent import handler, _body, _complete, _request
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from webapp import task_package as receiver


def test_gate_red_keeps_all_thirteen_reasons_and_preserves_accepted_package(
        tmp_path, monkeypatch, handler):
    application, channel, dispatched = handler
    parts = real_soft_package(tmp_path / "seed", monkeypatch, "1.20")
    source_path, source_bytes, original, package_path = (
        parts[5], parts[6], parts[-2], parts[-1])
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        original_members = {name: archive.read(name) for name in archive.namelist()}
    assert json.loads(original_members["manifest.json"])["spec_version"] == "1.20"
    assert producer_gate.check(package_path)[0] == []
    healthy_package = receiver.load(package_path, run_gate=False)
    assert healthy_package["objective_projection"]["judged"] is True

    request, body_reads = _request(application, original)
    healthy_response = _complete(application.api_task_package_receive(request))
    healthy = _body(healthy_response)
    assert healthy_response.status_code == 200, healthy
    assert healthy["verdict"] in {"OK", "MISMATCH"}, healthy
    assert body_reads == [True] and len(dispatched) == 1
    assert healthy["cosmos_run"] is None and healthy["run_note_code"] == "storage-off"
    assert set(healthy["mg"]) == {"pairing", "rostering"}, healthy
    assert all(not cell["launch_allowed"] for cell in healthy["mg"].values()), healthy
    inbox = channel.inbox_dir()
    accepted_sha = hashlib.sha256(original).hexdigest()
    accepted = inbox / accepted_sha
    accepted_before = {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()}
    current_before = (inbox / channel.CURRENT_NAME).read_bytes()
    registry = channel.registry_path()
    registry_before = registry.read_bytes() if registry.is_file() else None
    if registry_before is None:
        assert "вывод реестра исполнителей не удался" in healthy["detail"], healthy
    assert accepted_before[channel.PACKAGE_NAME] == original

    names = [f"a08_unlisted_{number:02d}.txt" for number in range(13)]
    expected_problems = [f"{name}: файл пакета не назван в манифесте" for name in names]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in original_members.items():
            archive.writestr(name, content)
        for name in names:
            assert name not in original_members
            archive.writestr(name, b"isolated unlisted member")
    changed = stream.getvalue()
    changed_path = tmp_path / "thirteen-unlisted-members.zip"
    changed_path.write_bytes(changed)
    with zipfile.ZipFile(changed_path) as archive:
        assert {name: archive.read(name) for name in original_members} == original_members
    actual_problems, _ = producer_gate.check(changed_path)
    assert actual_problems == expected_problems, actual_problems
    # Unlike REFUSED, this is a real gate-only disagreement: the full package
    # reader accepts the same untouched declared content without a fake judge.
    read_again = receiver.load(changed_path, run_gate=False)
    assert read_again["model"] == healthy_package["model"]
    assert read_again["manifest"] == healthy_package["manifest"]
    assert read_again["objective_projection"]["judged"] is True

    request, body_reads = _request(application, changed)
    response = _complete(application.api_task_package_receive(request))
    refused = _body(response)
    assert response.status_code == 422, refused
    assert refused["verdict"] == "GATE_RED", refused
    assert refused["sha"] == hashlib.sha256(changed).hexdigest()
    assert body_reads == [True] and len(dispatched) == 2
    assert not (inbox / refused["sha"]).exists()
    assert not list(inbox.glob("incoming-*.zip"))
    assert {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()} == accepted_before
    assert (inbox / channel.CURRENT_NAME).read_bytes() == current_before
    assert (registry.read_bytes() if registry.is_file() else None) == registry_before
    journal = [json.loads(line) for line in (inbox / channel.RECEIPTS_NAME)
               .read_text(encoding="utf-8").splitlines()]
    assert len(journal) == 2
    assert journal[-1]["verdict"] == "GATE_RED" and journal[-1]["sha"] == refused["sha"]
    assert source_path.read_bytes() == source_bytes
    assert package_path.read_bytes() == original and changed_path.read_bytes() == changed

    # Preservation assertions above complete before the diagnostic oracle.
    # REFUSED already retains its full reader exception in this same carrier;
    # GATE_RED must not silently discard a cause beyond its preview/list caps.
    absent_from_detail = [problem for problem in actual_problems
                          if problem not in refused["detail"]]
    print(json.dumps({"gate_problem_count": len(actual_problems),
                      "last_reason_in_response": expected_problems[-1] in json.dumps(refused, ensure_ascii=False),
                      "last_reason_in_journal": expected_problems[-1] in json.dumps(journal, ensure_ascii=False),
                      "detail_missing_count": len(absent_from_detail),
                      "accepted_current_and_sources_unchanged": True}))
    assert absent_from_detail == [], {
        "missing_from_existing_detail": absent_from_detail, "response": refused}
    assert refused["mismatch"][:len(actual_problems)] == actual_problems

    # A compact receiver journal still needs an actionable cause after the
    # caller loses its response; it must not duplicate the whole diagnostic.
    latest = journal[-1]
    assert latest.get("reasons_count") == len(actual_problems), latest
    assert latest.get("first_reason") == actual_problems[0], latest
    assert channel.receipts_tail(1) == [latest]
    assert "reasons_count" not in journal[0] and "first_reason" not in journal[0]
