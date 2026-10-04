"""Root-local A06/03-05: real draft.3 -> native ZIP -> two readers/card data.

No manifest revision relabelling or reader/producer verdict substitution.
Temporary synthetic support is explicitly NOT stand or release evidence.
Only SOFT rules: the real SMT verifier has zero hard rules, no solver/LLM.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import template_catalog as tc

from cdpm import catalog_bridge as cb, export_task_package as tp, model, smt_verify
from cdpm.gates import gate_task_package as producer
from vmp import catalog_access as ca, objective_projection as op
from webapp import task_channel as channel, task_package as consumer
from test_a06_k15_direction_tokens import _candidate, _hashes, FINGERPRINT
from test_cdpm_task_package import _ready_doc, _run_dir
from test_k06_scope_agent import native_support, publish
import test_k06_scope_agent as support_fixture
from template_catalog import bindings
from test_objective_projection_boundaries import modern_soft_rule
from vmp import pairing_input
from tests.vmp.test_pairing_input import APPLIED, PAYLOAD


@pytest.fixture(scope="module", autouse=True)
def unchanged_sources():
    before = _hashes()
    yield
    assert _hashes() == before


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("CREW_DATA_DIR", "CREW_COSMOS_RUNS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    for name in ("CREW_TASK_INBOX", "CREW_CDPM_RECORD_API", "CREW_TASK_PACKAGE"):
        monkeypatch.setenv(name, "")
    for name in ("CREW_CHANNEL_URL", "CREW_CHANNEL_TOKEN", "CREW_SOLVER_URL"):
        monkeypatch.delenv(name, raising=False)
    cb.forget()
    ca.forget()
    tc.forget_current()
    channel._CARD_CACHE.update(key=None, value=None)
    yield
    cb.forget()
    ca.forget()
    tc.forget_current()
    channel._CARD_CACHE.update(key=None, value=None)


def candidate_support(tmp_path, monkeypatch, cat, records):
    """Complete old fixture declarations honestly: missing executors are NOT present.

    The real binding/support validators remain unchanged. Only records actually
    used by the synthetic document receive synthetic local outcomes below.
    """
    root = Path(__file__).resolve().parents[1]
    fixture_root = tmp_path / "binding-input"
    for relative in ("vmp/bindings.json", "cdpm/bindings.json"):
        raw = json.loads((root / relative).read_text(encoding="utf-8"))
        rows = {(r["section"], r["id"]): r for r in raw["bindings"]}
        catalogue = [("families", r) for r in cat.families_full()]
        catalogue += [("criteria", r) for r in cat.criteria().values()]
        for section, rec in catalogue:
            key = section, rec["id"]
            row = rows.get(key)
            kinds = set(rec.get("executor_kinds", rec.get("computable_by", [])))
            missing = kinds & bindings.zone_of(raw)
            if row is not None:
                missing -= {e["kind"] for e in row["executors"]}
            if not missing:
                continue
            assert key not in records, "Do not invent support for a used record"
            if row is None:
                row = {"section": section, "id": rec["id"],
                       "executors": [], "stand_verified": []}
                raw["bindings"].append(row)
            row["executors"].extend({
                "kind": kind, "state": "нет", "lang": None, "file": None,
                "symbol": None, "cr_key": None,
                "note": "Absent from old fixture input; no executor or stand claimed."
            } for kind in sorted(missing))
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with monkeypatch.context() as local:
        local.setattr(support_fixture, "ROOT", fixture_root)
        return native_support(tmp_path, monkeypatch, cat, records)


def native_package(tmp_path, monkeypatch, carrier="document", spec="1.20",
                   prose_rate=None, kind="min_person_days", rank_rate=12.3):
    folder = _candidate(tmp_path, "min_person_days", "minimize")
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cat = tc.current(folder, validated=True)
    assert cat.fingerprint() == FINGERPRINT
    doc = _ready_doc()
    doc["rules"] = [modern_soft_rule(cat)]
    for question in doc["questions"]:
        question["rule_refs"] = ["R-S"]
    objective = doc["objectives"][0]
    if kind == "rank_normalized_cost":
        objective["title"] = objective["statement"] = "Нормированная стоимость"
    if prose_rate is not None:
        assert carrier == "prose"
        objective["statement"] = objective["statement"].replace("100", prose_rate)
    if carrier == "document":
        objective["criterion"] = {
            "kind": "min_person_days", "rate_per_duty": 77,
            "version": "v7", "effective_from": "2030-01-01",
            "per": "день командировки", "as_written": "Полная цитата. " * 60}
        if kind == "rank_normalized_cost":
            objective["criterion"].update(kind=kind, rates={"flight": rank_rate, "work": 2.5})
            del objective["criterion"]["rate_per_duty"]
    elif carrier == "answer":
        question = copy.deepcopy(doc["questions"][0])
        question["id"] = model.CRITERION_QUESTION_ID
        question["title"] = "Машинный критерий пейринга"
        question["text"] = "Назовите критерий, полную ставку и её версию."
        question["answer"]["raw"] = json.dumps({
            "criterion": "MIN_PERSON_DAYS", "rate_per_duty": 123,
            "rule": "R-S", "version": "v7", "effective_from": "2030-01-01"})
        if kind == "rank_normalized_cost":
            question["answer"]["raw"] = json.dumps({
                "criterion": "NORMALIZED_COST", "rule": "R-S", "rates_param": "rates",
                "version": "v7", "effective_from": "2030-01-01",
                "rates": {rank: {"flight": rank_rate, "work": 2.5} for rank in ("CP", "FO")}})
        doc["questions"].append(question)
    elif carrier != "prose":
        raise AssertionError(carrier)
    assert model.validate_model(doc) == []
    records = {("families", "layover_penalty")}
    criterion = model.derive_pairing_criterion(doc)
    if criterion is not None:
        records.add(("criteria", criterion["kind"]))
    support = candidate_support(tmp_path, monkeypatch, cat, records)
    publish(cat, doc, support, records)
    run = _run_dir(tmp_path, doc)
    raw = (run / "extract/model.json").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("No translator, hard-rule solver or external runtime")

    monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    if spec in ("omitted", "none"):
        monkeypatch.setattr(tp, "SPEC_VERSION", "1.20")  # local writer configuration only
        kwargs = {} if spec == "omitted" else {"spec_version": None}
        expected_spec = "1.20"
    else:
        kwargs, expected_spec = {"spec_version": spec}, spec
    files = tp.build_package(run, **kwargs)
    assert json.loads(files["manifest.json"])["spec_version"] == expected_spec
    assert (run / "extract/model.json").read_bytes() == raw
    path = tmp_path / "native.zip"
    path.write_bytes(tp.write_zip(files))
    return path, files, cat, doc


def _rewrite(path, files, mutation):
    files = dict(files)
    projected = json.loads(files["objective.json"])
    mutation(projected)
    files["objective.json"] = op.canonical_bytes(projected)
    manifest = json.loads(files["manifest.json"])
    for row in manifest["files"]:
        if row["name"] == "objective.json":
            row["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
            row["bytes"] = len(files["objective.json"])
    files["manifest.json"] = json.dumps(manifest).encode()
    path.write_bytes(tp.write_zip(files))


@pytest.mark.parametrize("carrier,rate", [("document", 77), ("answer", 123), ("prose", 100)])
@pytest.mark.parametrize("spec", ["1.20", "1.21", "omitted", "none"])
def test_native_current_zip_preserves_direction_source_and_complete_criterion(
        tmp_path, monkeypatch, carrier, rate, spec):
    path, files, cat, doc = native_package(tmp_path, monkeypatch, carrier, spec)
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    assert received["objective_projection"]["judged"] is True
    assert received["objective_projection"]["catalog"]["fingerprint"] == FINGERPRINT
    assert received["manifest"]["catalog"]["version"] == "0.7.0-draft.3"
    assert received["landing_projection"]["archive_only"] is False
    assert received["support_projection"]["judged"] is True
    assert "optimization" in received["manifest"]["allowed_modes"]["pairing"]
    projected = received["objective"]["pairing"]
    assert projected["direction"] == projected["criterion"]["direction"] == "minimize"
    criterion = projected["criterion"]
    assert criterion["rate_per_duty"] == rate
    assert projected["components"][0]["weight"] == 10
    assert projected["components"][0]["sources"] == doc["rules"][0]["sources"]
    if carrier == "document":
        assert criterion["as_written"] == doc["objectives"][0]["criterion"]["as_written"]
        assert criterion["source_objective"] == "O-1"
    elif carrier == "answer":
        assert criterion["as_written"] == doc["questions"][-1]["answer"]["raw"]
        assert criterion["source_answer"] == {"question": model.CRITERION_QUESTION_ID, "revision": 2}
    else:
        assert criterion["as_written"] == doc["objectives"][0]["statement"]
    assert criterion["source_rule"] == ("R-S" if carrier == "answer" else "O-1")
    card = channel._card_body(path)  # actual card payload, without app startup
    assert not card.get("error"), card
    assert card["objective_projection"] == received["objective_projection"]
    assert card["criterion"]["source_rule"] == criterion["source_rule"]
    assert card["criterion"]["rates_version"] == criterion.get("rates_version")
    before = copy.deepcopy(received)
    args, provenance = pairing_input.builder_args(
        copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD), budget_s=60, criterion=criterion)
    assert args.count("--w-span-day") == 1
    assert args[args.index("--w-span-day") + 1] == str(rate)
    assert provenance["--w-span-day"]["value"] == rate
    assert criterion["source_rule"] in provenance["--w-span-day"]["from"]
    for flag in ("--w-duty", "--w-night", "--w-dh", "--w-chain"):
        assert args[args.index(flag) + 1] == "0"
    forged = dict(criterion, direction="maximize")
    with pytest.raises(pairing_input.PairingInputError, match="каталог объявил minimize"):
        pairing_input.builder_args(copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD),
                                   budget_s=60, criterion=forged)
    assert received == before


@pytest.mark.parametrize("carrier", ["document", "answer", "prose"])
@pytest.mark.parametrize("damage", ["direction", "rate", "source", "quote", "missing"])
def test_rehashed_native_zip_is_refused_by_both_readers_and_card(
        tmp_path, monkeypatch, carrier, damage):
    path, files, _, _ = native_package(tmp_path, monkeypatch, carrier)
    assert producer.check(path)[0] == []
    assert consumer.load(path, run_gate=False)["objective_projection"]["judged"]
    def mutate(obj):
        zone = obj["pairing"]
        if damage == "direction":
            zone["direction"] = "maximize"
        elif damage == "missing":
            zone["criterion"] = None
        else:
            zone["criterion"][{"rate": "rate_per_duty", "source": "source_rule",
                               "quote": "as_written"}[damage]] = 999 if damage == "rate" else "forged"
    _rewrite(path, files, mutate)
    problems = producer.check(path)[0]
    expected = "criterion-direction" if damage == "direction" else "objective-mismatch"
    assert any(expected in p for p in problems), problems
    assert not any("SHA-256" in p or "support-missing" in p for p in problems), problems
    with pytest.raises(consumer.TaskPackageRefused) as error:
        consumer.load(path, run_gate=False)
    assert error.value.code == expected
    card = channel._card_body(path)
    assert card["error"] == str(error.value)  # real human explanation, not UI code
    assert "objective_projection" not in card


def test_card_recomputes_basis_instead_of_trusting_forged_package_prose(tmp_path, monkeypatch):
    path, files, _, _ = native_package(tmp_path, monkeypatch)
    _rewrite(path, files, lambda obj: obj["pairing"].update(direction_basis="FORGED <script>"))
    assert producer.check(path)[0] == []
    card = channel._card_body(path)
    assert not card.get("error"), card
    assert "FORGED" not in json.dumps(card["objective_projection"])
    assert FINGERPRINT in json.dumps(card["objective_projection"])


def test_explicit_native_archive_is_unjudged_not_current_approval(tmp_path, monkeypatch):
    path, _, _, _ = native_package(tmp_path, monkeypatch, spec="1.18")
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    assert received["objective_projection"]["judged"] is False
    assert "не есть разрешение исполнения" in received["objective_projection"]["words"]
    assert channel._card_body(path)["objective_projection"] == received["objective_projection"]


@pytest.mark.parametrize("rate", ["100*2", "100.5", "-100", "100 до 200", "2 × 100", "0"])
def test_unparsed_prose_rate_removes_optimization_end_to_end(tmp_path, monkeypatch, rate):
    path, _, _, doc = native_package(tmp_path, monkeypatch, "prose", prose_rate=rate)
    assert model.derive_pairing_criterion(doc) is None
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    assert received["objective"]["pairing"]["criterion"] is None
    assert "optimization" not in received["manifest"]["allowed_modes"]["pairing"]
    assert "feasibility-soft" in received["manifest"]["allowed_modes"]["pairing"]
    card = channel._card_body(path)
    assert not card.get("error"), card
    assert not card.get("criterion")
    assert card["objective_projection"]["judged"] is True


@pytest.mark.parametrize("rate", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_normalized_answer_never_enables_native_optimization(tmp_path, monkeypatch, rate):
    path, _, _, doc = native_package(tmp_path, monkeypatch, "answer",
                                     kind="rank_normalized_cost", rank_rate=rate)
    assert model.derive_pairing_criterion(doc) is None
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    assert received["objective"]["pairing"]["criterion"] is None
    assert "optimization" not in received["manifest"]["allowed_modes"]["pairing"]
    assert "feasibility-soft" in received["manifest"]["allowed_modes"]["pairing"]
    card = channel._card_body(path)
    assert not card.get("error") and not card.get("criterion"), card


@pytest.mark.parametrize("rate", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_normalized_document_is_refused_before_native_export(rate):
    doc = _ready_doc()
    doc["objectives"][0]["criterion"] = {
        "kind": "rank_normalized_cost", "version": "v7", "effective_from": "2030-01-01",
        "rates": {"flight": rate, "work": 2.5}}
    assert any("блок criterion неполон" in p for p in model.validate_model(doc))


@pytest.mark.parametrize("carrier", ["document", "answer"])
def test_healthy_normalized_rate_survives_native_zip_and_card(tmp_path, monkeypatch, carrier):
    path, _, _, doc = native_package(tmp_path, monkeypatch, carrier, kind="rank_normalized_cost")
    assert producer.check(path)[0] == []
    received = consumer.load(path, run_gate=False)
    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["rates"] == {"flight": 12.3, "work": 2.5}
    assert criterion["direction"] == "minimize"
    assert criterion["rates_version"]["version"] == "v7"
    assert "optimization" in received["manifest"]["allowed_modes"]["pairing"]
    assert channel._card_body(path)["objective_projection"] == received["objective_projection"]
    args, provenance = pairing_input.builder_args(
        copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD), budget_s=60, criterion=criterion)
    assert args[args.index("--cost-rank-deci-work") + 1] == "148"
    assert args[args.index("--cost-rank-deci-dh") + 1] == "25"
    assert provenance["--cost-rank-deci-work"]["value"] == 148
    assert provenance["--cost-rank-deci-dh"]["value"] == 25
    assert "--w-span-day" not in args and "--w-duty" not in args
