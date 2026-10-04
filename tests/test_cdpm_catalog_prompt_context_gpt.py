"""Catalogue facts at actual audit/worker/arbiter prompt boundaries.

Finite validated synthetic catalogue copies, offline callables only. Capturing
data/guidance is not a semantic LLM verdict or an end-to-end dataset PASS.
"""
from copy import deepcopy
import hashlib
import json
import socket

import pytest

from cdpm import catalog_bridge as cb, exchange as ex
from template_catalog import canon, family_contract
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken


FAMILY = "crew_unavailability"
NORM = "Член экипажа не может быть назначен на рейс в дни своего отпуска."
SOURCE = NORM + " Шаблона нет: дни отсутствия не судит ни одна семья каталога."
MARKER = "ФАКТИЧЕСКИЙ КАТАЛОГ ДЛЯ ПРОВЕРКИ ПОСАДКИ (JSON):\n"
ROLES = ("auditor", "worker", "arbiter")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Catalogue prompt tests must not access network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    cb.forget()
    yield
    cb.forget()


def _catalogue(tmp_path, monkeypatch, *, present=True, mutate=None):
    def prepare(docs):
        if present:
            modern_docs(docs)
        else:
            # A finite older-state control, not a claim about a signed release.
            docs["catalog.json"]["version"] = "0.5.0"
            docs["families.json"]["families"] = [
                r for r in docs["families.json"]["families"] if r["id"] != FAMILY]
            docs["requests.json"]["requests"] = [
                r for r in docs["requests.json"]["requests"]
                if r.get("record_id") != FAMILY]
            docs["dictionaries.json"]["lexicon_order"] = [
                fid for fid in docs["dictionaries.json"]["lexicon_order"]
                if fid != FAMILY]
        if mutate is not None:
            mutate(docs)
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    cat = cb.catalog()
    assert not [p for p in cat.validate() if p.severity == "error"]
    assert (FAMILY in cat.families()) is present
    return cat


def _case(cat, *, gap=False, statement=NORM):
    fit = {"category": "C", "gap": "Недоступность в каталоге не представлена"} if gap else {
        "category": "A", "family": FAMILY, "quote": NORM, "params": {},
        "schema_version": "landing/2", "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, FAMILY),
    }
    doc = {
        "meta": {"customer": "Synthetic catalogue prompt", "mode": 1,
                 "cdpm_version": "0.1", "revision": 1},
        "sources": [{"sid": "S1", "file": "rules.txt",
                     "sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}],
        "rules": [{"id": "R-1", "title": "Недоступность", "kind": "HARD",
                   "zone": "ROSTERING", "statement": statement,
                   "sources": [{"sid": "S1", "anchor": "п.1"}], "template_fit": fit}],
        "objectives": [], "questions": [],
    }
    kit = [{"sid": "S1", "clauses": [{"anchor": "п.1", "text": SOURCE}]}]
    remark = {"id": "A1-001", "severity": "СЕРЬЁЗНОЕ", "defect_class": "искажение",
              "target": {"rule": "R-1"}, "source_refs": [{"sid": "S1", "anchor": "п.1"}],
              "text": "Посадка требует проверки по источнику и каталогу",
              "evidence": SOURCE, "proposal": "Проверить соответствие механики"}
    return doc, kit, {"round": 1, "remarks": [remark]}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _arbiter_input(tmp_path, doc, kit, pack):
    frozen = tmp_path / "freeze"
    exchange = tmp_path / "exchange"
    finding = {"id": "R1-A1-001", "key": ex.remark_key(pack["remarks"][0]),
               "state": "тупик", "severity": "СЕРЬЁЗНОЕ", "deadlock_kind": "повтор"}
    _write(frozen / "manifest.json", {"files": [{"sid": "S1"}]})
    _write(frozen / "clauses/S1.json", kit[0])
    _write(exchange / "registry.json", {"schema_version": "registry/2", "findings": [finding]})
    _write(exchange / "verdict.json", {"deadlocks": [finding], "final_version": "model-v00.json"})
    _write(exchange / "versions/model-v00.json", doc)
    _write(exchange / "rounds/round-01/remarks.json", pack)
    _write(exchange / "rounds/round-01/response.json", {"answers": [{
        "remark": "A1-001", "verdict": "ОТКЛОНЕНО", "basis": NORM}]})
    return frozen, exchange


class Captured(RuntimeError):
    pass


def _capture(role, tmp_path, case, *, template=None):
    doc, kit, pack = deepcopy(case)
    prompts = []

    def capture(prompt):
        prompts.append(prompt)
        raise Captured("offline capture; no model verdict")

    with pytest.raises(Captured):
        if role == "auditor":
            ex._audit_round(capture, doc, kit, 1)
        elif role == "worker":
            ex._respond_round(capture, doc, pack, {("S1", "п.1"): SOURCE},
                              template=template)
        else:
            frozen, exchange = _arbiter_input(tmp_path, doc, kit, pack)
            ex.run_arbiter(frozen, exchange, capture, capture)
    assert len(prompts) == 1
    return prompts[0]


def _snapshot(prompt):
    assert MARKER in prompt, "actual role prompt omits validated catalogue facts"
    return json.JSONDecoder().raw_decode(prompt.split(MARKER, 1)[1])[0]


@pytest.mark.parametrize("role", ROLES)
def test_current_catalogue_is_independent_evidence_at_each_real_boundary(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat)
    before = deepcopy(case)
    prompt = _capture(role, tmp_path / "call", case)
    snapshot = _snapshot(prompt)
    assert snapshot["catalog"] == {"version": cat.version(), "fingerprint": cat.fingerprint()}
    entry = next(r for r in snapshot["semantic_index"] if r["id"] == FAMILY)
    assert entry["semantics"] == cat.family(FAMILY)["semantics"]
    detail = snapshot["family_contracts"][FAMILY]
    assert detail["record_fingerprint"] == canon.record_fingerprint(cat.family(FAMILY))
    assert detail["record"]["params"] == []
    assert detail["record"]["semantics"] == cat.family(FAMILY)["semantics"]
    assert detail["references"] == [family_contract.snapshot(cat, FAMILY)]
    assert SOURCE in prompt, "historical assertion remains verbatim in the source"
    assert "НОРМА ЗАКАЗЧИКА И ФАКТ КАТАЛОГА" in prompt
    assert "не доказывает смысловое соответствие" in prompt
    assert case == before


@pytest.mark.parametrize("role", ROLES)
def test_real_old_catalogue_gap_is_not_invented_away(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch, present=False)
    case = _case(cat, gap=True)
    prompt = _capture(role, tmp_path / "call", case)
    snapshot = _snapshot(prompt)
    assert snapshot["catalog"]["version"] == "0.5.0"
    assert FAMILY not in {r["id"] for r in snapshot["semantic_index"]}
    assert FAMILY not in snapshot["family_contracts"]
    assert set(snapshot["family_contracts"]) == set(cat.families()), "C needs all real alternatives"
    assert case[0]["rules"][0]["template_fit"]["category"] == "C"
    assert SOURCE in prompt
    assert "реальный пробел остаётся C" in prompt


def test_three_roles_share_the_same_snapshot(tmp_path, monkeypatch):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat)
    snapshots = [_snapshot(_capture(role, tmp_path / role, case)) for role in ROLES]
    assert snapshots[0] == snapshots[1] == snapshots[2]


@pytest.mark.parametrize("role", ROLES)
def test_disputed_model_claim_cannot_forge_catalogue_evidence(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat)
    fit = case[0]["rules"][0]["template_fit"]
    fit["family_contract"]["record_fingerprint"] = "0" * 64
    prompt = _capture(role, tmp_path / "call", case)
    detail = _snapshot(prompt)["family_contracts"][FAMILY]
    assert detail["record_fingerprint"] == canon.record_fingerprint(cat.family(FAMILY))
    assert detail["record_fingerprint"] != fit["family_contract"]["record_fingerprint"]


@pytest.mark.parametrize("vote", ["B", "C"])
def test_present_but_non_equivalent_family_does_not_override_independent_vote(tmp_path, monkeypatch, vote):
    cat = _catalogue(tmp_path, monkeypatch)
    other_norm = "Не менее восьми выходных в каждом месяце."
    doc, kit, _pack = _case(cat, statement=other_norm)
    kit[0]["clauses"][0]["text"] = other_norm
    doc["sources"][0]["sha256"] = hashlib.sha256(other_norm.encode()).hexdigest()
    before = deepcopy(doc)
    prompts = []

    def auditor(prompt):
        prompts.append(prompt)
        return json.dumps({"remarks": [], "fit_votes": [{
            "rule": "R-1", "category": vote, "quote": other_norm, "confidence": 91}]},
            ensure_ascii=False)

    pack, rejected = ex._audit_round(auditor, doc, kit, 1)
    assert rejected == []
    assert pack["fit_votes_journal"][0]["category"] == vote
    assert pack["fit_votes_journal"][0]["disputed"] is True
    assert pack["remarks"][0]["fit_dispute"] is True
    assert doc == before
    assert "не доказывает смысловое соответствие" in prompts[0]


@pytest.mark.parametrize("role", ROLES)
def test_unavailable_catalogue_refuses_before_the_model(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat)
    monkeypatch.setenv(cb.ENV_CATALOG, str(tmp_path / "missing-catalogue"))
    cb.forget()
    with pytest.raises(cb.CatalogRequired):
        _capture(role, tmp_path / "call", case)


@pytest.mark.parametrize("role", ROLES)
def test_c_without_family_exposes_full_current_alternatives(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch)
    prompt = _capture(role, tmp_path / "call", _case(cat, gap=True))
    snapshot = _snapshot(prompt)
    assert set(snapshot["family_contracts"]) == set(cat.families())
    assert snapshot["family_contracts"][FAMILY]["record"]["params"] == []
    for fid, detail in snapshot["family_contracts"].items():
        record = cat.family(fid)
        for key in ("semantics", "params", "param_sets", "conditions",
                    "kind", "zone", "reads_history", "rule_form"):
            if key in record:
                assert detail["record"][key] == record[key]
        sets = [row["id"] for row in record.get("param_sets") or []] or ["all"]
        assert detail["references"] == [family_contract.snapshot(cat, fid, set_id=sid)
                                         for sid in sorted(sets)]


@pytest.mark.parametrize("role", ROLES)
def test_unknown_family_is_named_but_never_forged(tmp_path, monkeypatch, role):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat, gap=True)
    case[0]["rules"][0]["template_fit"]["family"] = "synthetic_missing_family"
    snapshot = _snapshot(_capture(role, tmp_path / "call", case))
    assert snapshot["requested_but_unavailable"] == ["synthetic_missing_family"]
    assert "synthetic_missing_family" not in snapshot["family_contracts"]
    assert "synthetic_missing_family" not in {r["id"] for r in snapshot["semantic_index"]}


def test_enforcement_uses_the_same_fact_context_as_normal_worker(tmp_path, monkeypatch):
    cat = _catalogue(tmp_path, monkeypatch)
    case = _case(cat)
    normal = _capture("worker", tmp_path / "normal", case)
    enforced = _capture("worker", tmp_path / "enforced", case, template=ex._PROMPT_ENFORCE)
    assert _snapshot(normal) == _snapshot(enforced)
    assert SOURCE in enforced


def test_catalogue_semantic_change_invalidates_audit_cache(tmp_path, monkeypatch):
    cat = _catalogue(tmp_path / "before", monkeypatch)
    doc, kit, _pack = _case(cat)
    prompts = []

    def auditor(prompt):
        prompts.append(prompt)
        return '{"remarks": []}'

    cache = tmp_path / "audit-cache"
    ex._audit_round(auditor, doc, kit, 1, cache_dir=cache)
    ex._audit_round(auditor, doc, kit, 1, cache_dir=cache)
    assert len(prompts) == 1

    def changed_semantics(docs):
        row = next(r for r in docs["families.json"]["families"] if r["id"] == FAMILY)
        row["semantics"]["measure"] += " (synthetic changed boundary)"

    changed = _catalogue(tmp_path / "after", monkeypatch, mutate=changed_semantics)
    assert changed.fingerprint() != cat.fingerprint()
    ex._audit_round(auditor, doc, kit, 1, cache_dir=cache)
    assert len(prompts) == 2
    assert _snapshot(prompts[0])["catalog"]["fingerprint"] == cat.fingerprint()
    assert _snapshot(prompts[1])["catalog"]["fingerprint"] == changed.fingerprint()
    assert doc["rules"][0]["template_fit"]["registry_rev"] == cat.fingerprint()
