"""Экстрактор правил (двухконтурный ИИ, Ф7): маршрутизация по
чувствительности документа, кандидаты → черновики Rule Pack, автогенерация
тестов, громкие отказы."""
import json

import pytest

from webapp import llm_backend
from webapp import rule_extractor as rx
from webapp import rule_packs as rp
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL

DOC = ("Статья 12. Время отдыха между полётными сменами не может быть "
       "менее 10 часов. Статья 13. Экипаж обязан быть справедливым.")


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "PACKS_PATH", tmp_path / "packs.json")
    yield tmp_path


def _pack():
    return rp.create_pack({"name": "RU", "country": "ru"}, ADMIN)


def _reply(arr):
    return json.dumps(arr, ensure_ascii=False)


def _stub(monkeypatch, reply, capture=None):
    def fake(prompt, *, backend, timeout_s=0, system=None, cwd=None,
             usage=None, model=None):
        if capture is not None:
            capture["prompt"] = prompt
            capture["backend"] = backend
        return {"ok": True, "text": reply, "backend": backend, "model": "m"}
    monkeypatch.setattr(llm_backend, "complete", fake)


def test_extract_adds_drafts_with_auto_tests(iso, monkeypatch):
    p = _pack()
    cap = {}
    _stub(monkeypatch, _reply([
        {"title": "Минимальный отдых", "statement": "Отдых не менее 600 мин.",
         "section": "ст. 12", "parameter": "min_rest_min", "value": 600,
         "positive_given": None, "negative_given": None},
        {"title": "Справедливость", "statement": "Наряды распределяются "
         "справедливо.", "section": "ст. 13", "parameter": None,
         "note": "вне Яруса 1"},
    ]), cap)
    out = rx.extract(p["id"], DOC, "confidential", "Приказ №139", ADMIN)
    assert out["added"] == 2 and out["errors"] == []
    assert out["backend"] == "local"                 # confidential => local
    pack = rp.get_pack(p["id"])
    r1 = pack["rules"][0]
    assert r1["status"] == "draft"                   # человек утверждает
    assert r1["source"] == {"document": "Приказ №139", "section": "ст. 12",
                            "url": ""}
    # auto tests: min_* -> positive above, negative below; both pass
    rep = rp.rule_test_report(r1)
    assert rep["ok"] is True and rep["pass"] == 2
    # note rule got manual test placeholders
    rep2 = rp.rule_test_report(pack["rules"][1])
    assert rep2["manual"] == 2


def test_confidential_refuses_cloud(iso, monkeypatch):
    p = _pack()
    _stub(monkeypatch, "[]")
    with pytest.raises(llm_backend.ScopeViolation):
        rx.extract(p["id"], DOC, "confidential", "Договор", ADMIN,
                   backend_request="cloud")


def test_public_may_use_cloud(iso, monkeypatch):
    p = _pack()
    cap = {}
    _stub(monkeypatch, "[]", cap)
    out = rx.extract(p["id"], DOC, "public", "АП РУз-94", ADMIN,
                     backend_request="cloud")
    assert out["backend"] == "cloud" and cap["backend"] == "cloud"
    assert out["added"] == 0


def test_bad_candidates_reported_not_silently_dropped(iso, monkeypatch):
    p = _pack()
    _stub(monkeypatch, _reply([
        {"title": "Без источника не бывает", "statement": "x",
         "parameter": "nope", "value": 5},              # unknown parameter
        {"title": "", "statement": "y", "parameter": None, "note": "z"},
    ]))
    out = rx.extract(p["id"], DOC, "confidential", "Док", ADMIN)
    assert out["added"] == 0
    assert len(out["errors"]) == 2


def test_non_json_reply_is_loud(iso, monkeypatch):
    p = _pack()
    _stub(monkeypatch, "не могу")
    with pytest.raises(rx.RuleExtractError, match="JSON"):
        rx.extract(p["id"], DOC, "confidential", "Док", ADMIN)


def test_input_validation(iso, monkeypatch):
    p = _pack()
    _stub(monkeypatch, "[]")
    with pytest.raises(rx.RuleExtractError, match="sensitivity"):
        rx.extract(p["id"], DOC, "secret", "Док", ADMIN)
    with pytest.raises(rx.RuleExtractError, match="короткий"):
        rx.extract(p["id"], "мало", "public", "Док", ADMIN)
    with pytest.raises(rx.RuleExtractError, match="документа"):
        rx.extract(p["id"], DOC, "public", "", ADMIN)
    with pytest.raises(rx.RuleExtractError, match="no such rule pack"):
        rx.extract("rpNOPE", DOC, "public", "Док", ADMIN)
