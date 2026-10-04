"""Data-backed nonnumeric norms are not bare procedural references.

The prompt is captured from real verify(); translator responses are explicitly
synthetic. JSON parsing, completeness and in-process Z3 are real. These tests
do not establish how an external model will translate the revised prompt, and
do not certify a concrete schedule or alter any historical SMT report.
"""

import copy
import json
import socket
import subprocess

import pytest

from cdpm import smt_verify as sv


# Exact statement of the refused inputs06 source; no data values or expected
# outcomes are added to the norm. The source catalogue/run is never read here.
NORM = (
    "член экипажа не назначается на рейсы в дни своего отпуска и иных "
    "объявленных дней отсутствия; дни берутся из книги данных авиакомпании, "
    "параметров у правила нет\nИсточник синтетического правила: стенд"
)
RELATION = "(=> unavailable_on_date (not assigned_on_date))"
DECLS = ["(declare-fun unavailable_on_date () Bool)",
         "(declare-fun assigned_on_date () Bool)"]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external I/O is forbidden in this offline profile")

    for name in ("connect", "connect_ex", "bind", "listen", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def rule(rid, statement, kind="HARD"):
    return {"id": rid, "kind": kind, "statement": statement}


def doc(*extra):
    return {"rules": [rule("R-UNAVAILABLE", NORM), *extra]}


class Channel:
    def __init__(self, rows, decls=DECLS):
        self.reply = json.dumps({"decls": decls, "asserts": rows},
                                ensure_ascii=False)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def translated(rid="R-UNAVAILABLE", smt=RELATION):
    return {"rule": rid, "smt": smt}


def check(document, channel, verdict):
    before = copy.deepcopy(document)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    result = sv.verify(document, channel, model_sha256=sv.model_fingerprint(raw))
    assert result["verdict"] == verdict
    assert document == before
    assert result["model_sha256"] == sv.model_fingerprint(raw)
    assert result["subject_sha256"] == sv.subject_fingerprint(document)
    assert result["translator"] == {"role": "smt_translator",
                                    "provider": None, "model": None}
    return result


def test_actual_prompt_separates_data_source_from_the_declared_relation():
    channel = Channel([translated()])
    check(doc(), channel, "sat")
    assert len(channel.prompts) == 1
    prompt = channel.prompts[0]
    assert "R-UNAVAILABLE | " + NORM + "\nkind=HARD" in prompt
    # These are prompt-contract checks, not a pretend LLM behavioural test.
    words = " ".join(prompt.casefold().split())
    assert "источник данных сам по себе не делает норму непереводимой" in words
    assert "отсутствие числовых параметров" in words
    assert "членство" in words and "назначение" in words
    assert "одной и той же сущности и даты" in words
    assert "не выдумывай конкретные дни" in words
    assert "одним свободным флагом" in words
    assert "чистая ссылка без самой нормы" in words
    # Keep the existing scalar-consistency boundary; no blanket new mandate
    # to run quantified scheduling or materialize external data is introduced.
    assert "кванторы не нужны" in words


def test_norm_without_external_facts_keeps_both_membership_and_assignment_unknown():
    channel = Channel([translated()])
    result = check(doc(), channel, "sat")
    assert result["n_rules"] == result["n_translated"] == 1
    assert result["translations"][0]["smt"] == RELATION
    assert result["untranslated"] == result["dropped"] == []
    assert "(= unavailable_on_date" not in result["script"]
    assert "(= assigned_on_date" not in result["script"]
    assert sv.subject_of(doc()) == [{"id": "R-UNAVAILABLE", "kind": "HARD",
                                    "statement": NORM}]


@pytest.mark.parametrize("unavailable,assigned", [(False, False), (False, True),
                                                  (True, False), (True, True)])
def test_real_z3_relation_truth_table_and_independent_conflicting_facts(
        unavailable, assigned):
    document = doc(
        rule("R-MEMBERSHIP", f"В рассматриваемую дату недоступность равна {unavailable}.", "DATA"),
        rule("R-ASSIGNMENT", f"В ту же дату назначение того же члена равно {assigned}.", "DATA"),
    )
    channel = Channel([translated(),
        translated("R-MEMBERSHIP", f"(= unavailable_on_date {str(unavailable).lower()})"),
        translated("R-ASSIGNMENT", f"(= assigned_on_date {str(assigned).lower()})")])
    verdict = "unsat" if unavailable and assigned else "sat"
    result = check(document, channel, verdict)
    assert result["n_rules"] == result["n_translated"] == 3
    assert len(channel.prompts) == 1
    if verdict == "unsat":
        assert set(result["core"]) == {"R-UNAVAILABLE", "R-MEMBERSHIP", "R-ASSIGNMENT"}
    else:
        assert result["core"] == []


@pytest.mark.parametrize("crew,day,verdict", [("P1", "D1", "unsat"),
    ("P2", "D1", "sat"), ("P1", "D2", "sat"), ("P2", "D2", "sat")])
def test_finite_membership_relation_does_not_conflate_different_people_or_days(
        crew, day, verdict):
    # This fixture declares its entire finite domain explicitly. The ground
    # conjunction is the full finite norm, not an arbitrary representative or
    # an assertion that the norm is true. Neither dates nor people come from
    # an unseen dataset or an expected result.
    document = {"rules": [
        rule("R-UNAVAILABLE", NORM + "\nСинтетический домен: только P1, P2 и D1, D2."),
        rule("R-MEMBERSHIP", "P1 недоступен в D1.", "DATA"),
        rule("R-ASSIGNMENT", f"{crew} назначен на рейс в {day}.", "DATA"),
    ]}
    decls = ["(declare-datatypes () ((Crew P1 P2) (Day D1 D2)))",
             "(declare-fun unavailable (Crew Day) Bool)",
             "(declare-fun assigned (Crew Day) Bool)"]
    clauses = [f"(=> (unavailable {c} {d}) (not (assigned {c} {d})))"
               for c in ("P1", "P2") for d in ("D1", "D2")]
    channel = Channel([
        translated(smt="(and " + " ".join(clauses) + ")"),
        translated("R-MEMBERSHIP", "(unavailable P1 D1)"),
        translated("R-ASSIGNMENT", f"(assigned {crew} {day})"),
    ], decls)
    result = check(document, channel, verdict)
    assert result["n_rules"] == result["n_translated"] == 3
    assert len(channel.prompts) == 1
    if verdict == "unsat":
        assert set(result["core"]) == {"R-UNAVAILABLE", "R-MEMBERSHIP", "R-ASSIGNMENT"}


@pytest.mark.parametrize("qualified,assigned", [(False, False), (False, True),
                                               (True, False), (True, True)])
def test_neighbor_nonnumeric_qualification_relation_keeps_its_polarity(qualified, assigned):
    document = {"rules": [
        rule("R-LICENSE", "Нельзя назначать сотрудника на рейс без допуска; допуски в реестре."),
        rule("R-QUALIFIED", f"Для рассматриваемого сотрудника допуск равен {qualified}.", "DATA"),
        rule("R-ASSIGNED", f"Его назначение на рассматриваемый рейс равно {assigned}.", "DATA"),
    ]}
    channel = Channel([
        translated("R-LICENSE", "(=> assigned qualified)"),
        translated("R-QUALIFIED", f"(= qualified {str(qualified).lower()})"),
        translated("R-ASSIGNED", f"(= assigned {str(assigned).lower()})"),
    ], ["(declare-fun qualified () Bool)", "(declare-fun assigned () Bool)"])
    verdict = "unsat" if assigned and not qualified else "sat"
    result = check(document, channel, verdict)
    assert result["n_rules"] == result["n_translated"] == 3
    assert len(channel.prompts) == 1


@pytest.mark.parametrize("statement", [
    "Применять ограничения из внешнего приказа, который здесь не приведён.",
    "Порядок согласования отпуска определяет другой документ.",
])
def test_bare_reference_or_undefined_process_still_refuses(statement):
    document = {"rules": [rule("R-REFERENCE", statement)]}
    channel = Channel([{"rule": "R-REFERENCE", "status": "untranslatable",
                        "note": "В тексте не задана сама норма."}], [])
    result = check(document, channel, "none")
    assert result["n_rules"] == result["n_untranslatable"] == 1
    assert result["n_translated"] == 0
    assert result["untranslated"] == ["R-REFERENCE"]
    assert result["core"] == [] and len(channel.prompts) == 1


def test_translatable_relation_does_not_hide_an_unknown_neighbor():
    document = doc(rule("R-REFERENCE", "Соблюдать не приведённый внешний регламент."))
    channel = Channel([translated(), {"rule": "R-REFERENCE", "status": "untranslatable",
                                      "note": "Норма не приведена."}])
    result = check(document, channel, "none")
    assert result["n_rules"] == 2 and result["n_translated"] == 1
    assert result["untranslated"] == ["R-REFERENCE"]
    assert result["core"] == [] and len(channel.prompts) == 1


@pytest.mark.parametrize("bad_rows", [[], [translated(smt="true")]])
def test_missing_rule_and_literal_true_do_not_become_a_translation(bad_rows):
    channel = Channel(bad_rows)
    result = check(doc(), channel, "none")
    assert len(channel.prompts) == 2
    assert result["n_rules"] == 1 and result["n_translated"] == 0
    assert result["untranslated"] == ["R-UNAVAILABLE"]

