"""Parameterless landing guidance at the real worker/enforcement boundary.

Only synthetic source clauses and validated temporary catalogues are used.
Scripted callbacks are not LLM quality evidence or a completed live run.
The existing receiver, quote/pin checks and bounded retry remain unmodified.
"""
from copy import deepcopy
import json
import os
import socket
import subprocess

import pytest

from cdpm import catalog_bridge as cb, exchange as ex, llm, model
from template_catalog import family_contract
from test_cdpm_catalog_prompt_context_gpt import (
    FAMILY, MARKER, NORM, SOURCE, _case, _catalogue,
)


ASSUMPTION = "Дни отпуска трактуются как календарные дни недоступности."
PROMPTS = ("_PROMPT_RESPOND", "_PROMPT_ENFORCE")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Synthetic guidance tests must not call providers or processes")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(os, "system", denied)
    monkeypatch.setattr(llm, "cosmos_llm", denied)
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    cb.forget()
    yield
    cb.forget()


@pytest.fixture
def case(tmp_path, monkeypatch):
    cat = _catalogue(tmp_path / "catalogue", monkeypatch)
    doc, kit, pack = _case(cat, gap=True)
    # The old typed C is retained until the ordinary receiver accepts a patch.
    doc["rules"][0]["template_fit"].update(
        schema_version="landing/2", registry_rev=cat.fingerprint())
    clauses = {(item["sid"], clause["anchor"]): clause["text"]
               for item in kit for clause in item["clauses"]}
    assert model.validate_schema(doc) == model.validate_model(doc) == []
    assert ex.validate_remarks(pack, doc) == []
    return cat, doc, pack, clauses


def answer(case, *, category="B"):
    cat, doc, pack, _clauses = case
    row = deepcopy(doc["rules"][0])
    row["template_fit"] = {
        "schema_version": "landing/2", "registry_rev": cat.fingerprint(),
        "category": category, "family": FAMILY,
        "family_contract": family_contract.snapshot(cat, FAMILY),
        "params": {}, "quote": NORM,
    }
    if category == "B":
        row["template_fit"]["assumption"] = ASSUMPTION
    return {"remark": pack["remarks"][0]["id"], "verdict": "ПРИНЯТО",
            "basis": NORM, "change": "Исправлена посадка по источнику и каталогу",
            "patch": {"rule_id": row["id"], "rule": row}}


def receive(case, item, *, enforce=False):
    _cat, doc, pack, clauses = case
    response = {"round": pack["round"], "answers": [deepcopy(item)]}
    before = deepcopy((doc, pack, response))
    executed = set()
    result = ex._apply_patches(
        doc, pack, response, 99 if enforce else pack["round"],
        valid_anchors=set(clauses), clause_texts=clauses,
        convert_reading_choice=not enforce, executed_out=executed)
    assert (doc, pack, response) == before
    return result, executed


def damage(case, name):
    cat, _doc, _pack, _clauses = case
    item = answer(case)
    fit = item["patch"]["rule"]["template_fit"]
    if name == "quote-missing":
        del fit["quote"]
    elif name == "params-quote":
        fit["params"] = {"quote": fit.pop("quote")}
    elif name == "catalog-quote":
        fit["quote"] = cat.family(FAMILY)["semantics"]["text"]
        assert fit["quote"] not in SOURCE
    elif name == "contract-wrapper":
        fit["family_contract"] = {"record": cat.family(FAMILY),
                                  "references": [fit["family_contract"]]}
    elif name == "contract-forged":
        fit["family_contract"]["record_fingerprint"] = "0" * 64
    else:
        raise AssertionError(name)
    return item


@pytest.mark.parametrize("name", PROMPTS)
def test_real_prompt_formatting_explains_new_paramless_landing(case, name):
    cat, doc, pack, clauses = case
    seen = []
    good = answer(case)

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": [good]}, ensure_ascii=False)

    response = ex._respond_round(worker, doc, pack, clauses, template=getattr(ex, name))
    assert response["answers"] == [good] and len(seen) == 1
    rendered = seen[0]
    # This is the real .format() path with a dictionary containing JSON braces,
    # not merely a substring check of the unformatted shared constant.
    snapshot = json.JSONDecoder().raw_decode(rendered.split(MARKER, 1)[1])[0]
    assert snapshot["family_contracts"][FAMILY]["references"] == [
        family_contract.snapshot(cat, FAMILY)]
    assert SOURCE in rendered
    assert "family_contracts[family].references" in rendered
    assert "пять полей" in rendered and "не весь объект" in rendered
    assert "template_fit.params = {}" in rendered
    assert "template_fit.quote" in rendered and "params.quote" in rendered
    assert "ДОСЛОВНАЯ подстрока клаузы-источника" in rendered
    assert "не цитата семантики каталога" in rendered
    assert "basis не заменяет" in rendered
    assert "РОВНО ОДНО" in rendered and "assumption" in rendered
    assert "не превращай B в A" in rendered


@pytest.mark.parametrize("name", PROMPTS)
def test_dictionary_braces_are_data_not_a_second_format_pass(name):
    dictionary = '{"AAA": {"value": 45, "quote": "{literal source}"}}'
    rendered = getattr(ex, name).format(
        remarks="REMARKS", rules="RULES", clauses=SOURCE, dictionary=dictionary)
    assert dictionary in rendered and SOURCE in rendered
    assert '"answers"' in rendered and '"patch"' in rendered


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.parametrize("category", ["A", "B"])
def test_real_receiver_accepts_source_quote_and_exact_reference(case, enforce, category):
    good = answer(case, category=category)
    (new, targets, rejected, tokens), executed = receive(case, good, enforce=enforce)
    assert not rejected and targets and tokens and executed == {"A1-001"}
    fit = new["rules"][0]["template_fit"]
    assert fit == good["patch"]["rule"]["template_fit"]
    assert fit["params"] == {} and fit["quote"] == NORM
    assert fit["category"] == category
    if category == "B":
        assert fit["assumption"] == ASSUMPTION
    assert model.validate_schema(new) == model.validate_model(new) == []


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.parametrize("name", ["quote-missing", "params-quote", "catalog-quote",
                                 "contract-wrapper", "contract-forged"])
def test_bad_paramless_shapes_keep_original_c_and_receiver_reasons(case, enforce, name):
    item = damage(case, name)
    before = deepcopy(item)
    (new, targets, rejected, tokens), executed = receive(case, item, enforce=enforce)
    assert new == case[1] and not targets and not tokens and not executed
    assert item == before and len(rejected) == 1
    reason = str(rejected[0])
    if name == "quote-missing":
        assert "params либо quote" in reason
    elif name == "params-quote":
        assert "запись параметра 'quote' не объект" in reason
    elif name == "catalog-quote":
        assert "дословной подстрокой клауз источников" in reason
    else:
        assert "family_contract" in reason


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.parametrize("name", ["params-quote", "catalog-quote", "contract-wrapper"])
def test_existing_retry_passes_exact_refusal_then_accepts_checked_b(case, tmp_path, enforce, name):
    _cat, doc, pack, clauses = case
    bad, good, seen = damage(case, name), answer(case), []
    before = deepcopy((doc, pack, bad, good))

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": [bad if len(seen) == 1 else good]}, ensure_ascii=False)

    template = ex._PROMPT_ENFORCE if enforce else ex._PROMPT_RESPOND
    response = ex._respond_round(worker, doc, pack, clauses,
                                 template=template, cache_dir=tmp_path / "cache")
    assert len(seen) == 2 and response["answers"] == [good]
    assert "МАШИННАЯ ПРОВЕРКА ПРЕДЫДУЩЕГО ОТВЕТА" in seen[1]
    assert "A1-001" in seen[1] and "patch_rejections" in seen[1]
    assert (doc, pack, bad, good) == before
    (_new, _targets, rejected, _tokens), executed = receive(case, good, enforce=enforce)
    assert not rejected and executed == {"A1-001"}
    attempts = sorted((tmp_path / "cache/attempts").glob("*.json"))
    assert len(attempts) == 2
    saved = [json.loads(p.read_text(encoding="utf-8")) for p in attempts]
    assert saved[0]["patch_rejections"] and saved[1]["patch_rejections"] == []
    assert ex._respond_round(worker, doc, pack, clauses, template=template,
                             cache_dir=tmp_path / "cache") == response
    assert len(seen) == 2


@pytest.mark.parametrize("enforce", [False, True])
def test_two_bad_replies_do_not_become_enforced(case, enforce):
    _cat, doc, pack, clauses = case
    bad, seen = damage(case, "catalog-quote"), []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": [bad]}, ensure_ascii=False)

    response = ex._respond_round(worker, doc, pack, clauses,
        template=ex._PROMPT_ENFORCE if enforce else ex._PROMPT_RESPOND)
    assert len(seen) == 2 and response["answers"] == [bad]
    (new, _targets, rejected, _tokens), executed = receive(case, bad, enforce=enforce)
    assert new == doc and rejected and not executed


def test_shared_guidance_is_generic_not_a_case_or_family_override():
    for forbidden in (FAMILY, "run-0001", "VN-027", "C6-R1-A1-003"):
        assert forbidden not in ex._PATCH_LANDING_GUIDANCE
