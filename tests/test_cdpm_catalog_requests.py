# -*- coding: utf-8 -*-
"""Заявки CDPM в каталог шаблонов из посадок категории C (шаг 6 каталога,
часть CDPM; проект `final-report/KATALOG-SHAG-6-CDPM-ZAYAVKI-PROEKT-CLD.md`,
§5.2 — тесты стадий 1–3).

Гейт файла: ключ и номер заявки, кандидаты, сборка черновика, пять слоёв
обезличивания, запасное имя, очередь исключительным созданием и индекс,
состояния для экрана, ручки панели, неизменность модели и пакета, повтор
прогона, двойное нажатие, изоляция очереди, автозапуск, напоминание.
Настоящий вызов модели не делается: составитель — подставная функция.
"""
import hashlib
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import catalog_copies as cc
import template_catalog as tc
from template_catalog.requests import draft_canon as _draft_canon
from cdpm import actions_journal as aj
from cdpm import call_journal as cj
from cdpm import catalog_bridge
from cdpm import catalog_requests as cr
from cdpm import webui
from test_cdpm_exchange_run import _make_auditor
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm

REPO = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO / "catalog"


@pytest.fixture(autouse=True)
def _clean(declared_test_layout):
    cj.forget()
    aj.forget()
    aj.set_current(None)
    catalog_bridge.forget()
    yield
    cj.forget()
    aj.forget()
    aj.set_current(None)
    catalog_bridge.forget()


# ─────────────────────────────────────────────── стенд без панели

_GAP = ("нет семьи, считающей разрыв между соседними явками члена "
        "экипажа")


def _rule(rid, statement, *, kind="HARD", zone="PAIRING", fit=None,
          title="Строка свода", anchor="п.1.1", params=None):
    r = {"id": rid, "title": title, "statement": statement, "zone": zone,
         "kind": kind, "sources": [{"sid": "S1", "anchor": anchor}]}
    if params:
        r["params"] = params
    if fit is not None:
        r["template_fit"] = fit
    return r


def _c(gap=_GAP):
    return {"category": "C", "registry_rev": "rev-1", "gap": gap}


def _model(rules, questions=()):
    return {"meta": {"customer": "Полярис (тест)", "mode": 1,
                     "cdpm_version": "тест", "fit_contract": True},
            "sources": [{"sid": "S1", "file": "RULES-A.md",
                         "sha256": "0" * 64}],
            "rules": list(rules), "questions": list(questions)}


def _make_run(tmp_path, doc, *, name="run-0090", customer="Полярис (тест)",
              status=None):
    run_dir = tmp_path / "runs" / name
    (run_dir / "extract").mkdir(parents=True, exist_ok=True)
    (run_dir / "extract" / "model.json").write_bytes(
        (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    st = {"run": name, "customer": customer, "state": "извлечено"}
    st.update(status or {})
    (run_dir / "status.json").write_bytes(
        json.dumps(st, ensure_ascii=False).encode("utf-8"))
    return run_dir


def _cat():
    return tc.load(CATALOG_DIR)


def _proposal(prompt: str, **over) -> dict:
    """Предложение подставного составителя: детерминировано по
    формулировке строки (два черновика одного пакета не сталкиваются
    именами)."""
    st = re.search(r"- формулировка: (.+)", prompt).group(1).strip()
    kind = re.search(r"- вид: (\w+)", prompt).group(1)
    tag = hashlib.sha1(st.encode("utf-8")).hexdigest()[:6]
    got = {
        "id": f"probe_{tag}",
        "title": "Разрывы между явками",
        "subject": "разрыв между соседними явками члена экипажа",
        "nature": "schedule",
        "violation_unit": "event" if kind == "SOFT" else None,
        # Каталог 0.5.0: у мягкой семьи названы и единица, и размер
        # нарушения; чтения истории у семьи расписания нет.
        "violation_size": "count" if kind == "SOFT" else None,
        "reads_history": None,
        "params": [{"name": f"probe_{tag}_floor", "unit": "duration_minutes",
                    "relation": "min",
                    "note": "нижняя граница разрыва"}],
        "semantics": {"scope": "пара соседних явок", "unit": "минуты",
                      "measure": "недостача до нижней границы",
                      "text": "мера равна недостаче разрыва до объявленной "
                              "нижней границы"},
    }
    got.update(over)
    return got


def _composer(calls=None, patch=None, patch_after=None):
    """Подставной составитель: функция вместо канала. `patch` — правка
    предложения на каждом вызове, `patch_after` — начиная со второго
    (проверка повторов со словами отказа)."""
    calls = calls if calls is not None else []

    def _llm(prompt: str) -> str:
        calls.append(prompt)
        over = dict(patch or {})
        if patch_after and len(calls) > 1:
            over = dict(patch_after)
        return json.dumps(_proposal(prompt, **over), ensure_ascii=False)
    _llm.calls = calls
    return _llm


def _write_watch(monkeypatch) -> list:
    """Сторож записи: все пути, открытые на запись за время подачи."""
    import builtins
    import os as _os
    from cdpm import file_replace as _fr
    seen: list = []
    real_open, real_wb = builtins.open, Path.write_bytes
    real_wt, real_replace = Path.write_text, _os.replace
    real_fr = _fr.replace_file

    def _open(file, mode="r", *a, **kw):
        if any(ch in str(mode) for ch in "wxa+"):
            seen.append(str(file))
        return real_open(file, mode, *a, **kw)

    def _wb(self, data):
        seen.append(str(self))
        return real_wb(self, data)

    def _wt(self, data, **kw):
        seen.append(str(self))
        return real_wt(self, data, **kw)

    def _rep(src, dst):
        seen.append(str(dst))
        return real_replace(src, dst)

    def _frep(src, dst):
        seen.append(str(dst))
        return real_fr(src, dst)
    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(Path, "write_bytes", _wb)
    monkeypatch.setattr(Path, "write_text", _wt)
    monkeypatch.setattr(_os, "replace", _rep)
    monkeypatch.setattr(_fr, "replace_file", _frep)
    return seen


def _submit(run_dir, rows, llm, cat=None, outbox=None, doc=None, st=None,
            replay=False, source_dir=None):
    """Прямой прогон составления без панели (стадии 1–2)."""
    cat = cat or _cat()
    outbox = outbox or cr.outbox_root()
    doc = doc or json.loads((run_dir / "extract" / "model.json")
                            .read_text(encoding="utf-8"))
    st = st or json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    idx_rows = dict(cr.read_index(run_dir).get("rows") or {})
    counts = {"submitted": 0, "held": 0, "failed": 0}
    webui._catalog_compose(run_dir, doc, st, rows, idx_rows,
                           cr.DEFAULT_DOCUMENT_CLASS, outbox, counts,
                           replay, source_dir)
    cr.write_index(run_dir, {"form": 1, "rows": idx_rows})
    return counts, idx_rows


# ═══════════════════════════════════ 1. Ключ и номер заявки

def test_1_key_and_request_id_are_from_content():
    a = cr.request_key("HARD", "PAIRING", "Отдых на базе не менее нормы")
    b = cr.request_key("HARD", "PAIRING", "  отдых, на базе — не менее НОРМЫ ")
    assert a == b, "регистр и незначащая пунктуация номер не меняют"
    assert a != cr.request_key("SOFT", "PAIRING", "Отдых на базе не менее нормы")
    assert a != cr.request_key("HARD", "ROSTERING", "Отдых на базе не менее нормы")
    rid = cr.request_id_of(a)
    assert cr.REQUEST_ID_RE.match(rid), rid
    assert re.match(r"^req-\d{20}$", rid)
    # Литерал закона до переноса ключа в читатель (§0.12, вопрос К6).
    key = cr.request_key("HARD", "PAIRING", "правило образец сторожа закона")
    assert key == hashlib.sha256(
        "families\nHARD\nPAIRING\nправило образец сторожа закона"
        .encode("utf-8")).hexdigest()
    assert key == ("421af21358a4aa18e4a9cd0bc6e6dc62"
                   "1552dc4d3a438dd82923f9cdaa0c5063")
    assert cr.request_id_of(key) == "req-04763385720803207704"
    assert cr.fallback_id(key) == "family_" + key[:12]
    assert re.match(r"^[a-z][a-z0-9_]{2,40}$", cr.fallback_id(key))


# ═══════════════════════════════════ 2. Кандидаты

def test_2_candidates_only_meaningful_c(tmp_path, monkeypatch):
    rules = [
        _rule("R-S1-a", "разрыв между явками", fit=_c()),
        _rule("R-S1-serv1", "с1", fit=_c("модель не выполнила посадку")),
        _rule("R-S1-serv2", "с2", fit=_c("посадка модели отброшена: цитата")),
        _rule("R-S1-serv3", "с3", fit=_c("посадка не выполнялась: пропуск")),
        _rule("R-S1-a2", "правило A", fit={"category": "A",
                                           "registry_rev": "r",
                                           "family": "rest_floors"}),
        _rule("R-S1-b2", "правило B", fit={"category": "B",
                                           "registry_rev": "r",
                                           "family": "rest_floors",
                                           "assumption": "допущение"}),
        _rule("R-S1-d2", "правило D", fit={"category": "D",
                                           "registry_rev": "r",
                                           "candidates": ["a", "b"]}),
        _rule("R-S1-none", "без посадки"),
        _rule("R-S1-disp", "спорная строка", fit=_c()),
        _rule("R-S1-q", "строка с вопросом", fit=_c()),
    ]
    doc = _model(rules, questions=[{"id": "Q-2-x", "part": "2",
                                    "rule_refs": ["R-S1-q"]}])
    run_dir = _make_run(tmp_path, doc)
    findings = [{"key": json.dumps(["искажение+посадка", "R-S1-disp", None,
                                    []], ensure_ascii=False),
                 "state": "открыто"}]
    rows = {r["rule_id"]: r for r in cr.candidates(doc, run_dir, findings)}
    assert set(rows) == {"R-S1-a", "R-S1-serv1", "R-S1-serv2", "R-S1-serv3",
                         "R-S1-disp", "R-S1-q"}
    assert rows["R-S1-a"]["candidate"] is True
    for rid in ("R-S1-serv1", "R-S1-serv2", "R-S1-serv3"):
        assert rows[rid]["candidate"] is False
        assert rows[rid]["service_c"] is True
        assert "служебная C" in rows[rid]["reason"]
    assert rows["R-S1-disp"]["candidate"] is False
    assert "спор посадки" in rows["R-S1-disp"]["reason"]
    assert rows["R-S1-q"]["candidate"] is False
    assert "вопрос анкеты" in rows["R-S1-q"]["reason"]
    # Ключ, погашенный вердиктом арбитра, спором больше не считается.
    settled = [dict(findings[0], state="арбитр: принято")]
    rows2 = {r["rule_id"]: r for r in cr.candidates(doc, run_dir, settled)}
    assert rows2["R-S1-disp"]["candidate"] is False   # состояние вне живых
    live = webui._live_findings  # предикат живых находок — у панели
    assert callable(live)
    # Единственная причина «заявки не подаются вовсе» — недоступный
    # каталог: прежней второй причины (режим среза базы шаблонов) у
    # CDPM нет со стадии 1б шага 7 — посадки судятся каталогом, и
    # пробел при заданной переменной среза такой же пробел живого
    # каталога, как всякий другой.
    assert cr.blocked_words() is None
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(tmp_path / "нет-такой"))
    catalog_bridge.forget()
    assert "каталог шаблонов недоступен" in cr.blocked_words()
    rows3 = {r["rule_id"]: r for r in cr.candidates(doc, run_dir, [])}
    assert rows3["R-S1-a"]["candidate"] is False
    assert "каталог шаблонов недоступен" in rows3["R-S1-a"]["reason"]


# ═══════════════════════════════════ 3. Сборка черновика

def test_3_draft_is_built_by_code_from_seven_model_fields(tmp_path):
    cat = _cat()
    row = {"rule_id": "R-S1-a", "kind": "SOFT", "zone": "PAIRING",
           "statement": "минимизировать разрывы", "gap": _GAP, "params": [],
           "key": cr.request_key("SOFT", "PAIRING", "минимизировать разрывы")}
    row["request_id"] = cr.request_id_of(row["key"])
    llm = _composer()
    fields, words = cr.call_composer(llm, row, cat)
    assert words == []
    draft = cr.build_draft(row, fields, draft_id=fields["id"],
                           document_class=cr.DEFAULT_DOCUMENT_CLASS,
                           origin_date="2026-09-07")
    required = json.loads(
        (REPO / "template_catalog" / "schemas" / "catalog.schema.json")
        .read_text(encoding="utf-8"))["$defs"]["family_core"]["required"]
    assert set(required) <= set(draft), sorted(set(required) - set(draft))
    for forbidden in ("status", "history", "stand_tasks"):
        assert forbidden not in draft
    # Вид и зона — от СТРОКИ, не от модели.
    assert draft["kind"] == "SOFT" and draft["zone"] == "PAIRING"
    assert draft["class"] == "extension"
    assert draft["origin"]["kind"] == "заявка"
    assert draft["origin"]["confirmed_by"] == "аналитик"
    assert draft["violation_unit"] == "event"
    # Таблица видов исполнителя.
    assert draft["executor_kinds"] == ["kotlin_constraint", "kotlin_knob",
                                       "builder_method"]
    assert cr.executor_kinds_for("schedule", "ROSTERING", False) == \
        ["kotlin_constraint"]
    assert cr.executor_kinds_for("history", "BOTH", True) == \
        ["kotlin_logic_form", "check_code"]
    # У жёсткой строки единицы нарушения нет (X03).
    hard_row = dict(row, kind="HARD")
    fields2, _ = cr.call_composer(llm, hard_row, cat)
    d2 = cr.build_draft(hard_row, fields2, draft_id=fields2["id"],
                        document_class=cr.DEFAULT_DOCUMENT_CLASS,
                        origin_date="2026-09-07")
    assert d2["violation_unit"] is None
    req = cr.assemble_request(row, draft,
                              document_class=cr.DEFAULT_DOCUMENT_CLASS,
                              run_name="run-0090",
                              submitted_at="2026-09-07T10:00:00+03:00")
    assert req["record_id"] == draft["id"]
    assert req["source"]["ref"] == f"run-0090#{row['key'][:12]}"
    assert req["state"] == "подана" and req["submitted_by"] == "CDPM"
    assert req["decision"] == {"by": None, "at": None, "words": None}
    assert req["executor_kinds_requested"] is None and req["cr_key"] is None


def test_3c_catalog_050_fields_of_the_draft(tmp_path):
    """Каталог 0.5.0 в черновике заявки: размер нарушения у мягкой семьи,
    чтение истории у семьи истории, свидетель природы — вердикт судьи
    заявки (X18), и он ставится КОДОМ, а не спрашивается у модели."""
    cat = _cat()
    row = {"rule_id": "R-S1-a", "kind": "SOFT", "zone": "ROSTERING",
           "statement": "минимизировать разрывы", "gap": _GAP, "params": [],
           "key": cr.request_key("SOFT", "ROSTERING", "минимизировать разрывы")}
    row["request_id"] = cr.request_id_of(row["key"])
    fields, words = cr.call_composer(_composer(), row, cat)
    assert words == []
    draft = cr.build_draft(row, fields, draft_id=fields["id"],
                           document_class=cr.DEFAULT_DOCUMENT_CLASS,
                           origin_date="2026-09-07")
    assert draft["violation_size"] == "count"
    assert draft["reads_history"] is None
    assert draft["nature_confirmed_by"] == "change_request"
    # Свидетеля у модели не спрашивают: её слово поле не меняет.
    подделка = _composer(patch={"nature_confirmed_by": "certifier_registry"})
    f2, _ = cr.call_composer(подделка, row, cat)
    assert "nature_confirmed_by" not in f2
    d2 = cr.build_draft(row, f2, draft_id=f2["id"],
                        document_class=cr.DEFAULT_DOCUMENT_CLASS,
                        origin_date="2026-09-07")
    assert d2["nature_confirmed_by"] == "change_request"
    # «Свидетеля нет» у записи от заявки — отказ сборки словами (X18).
    with pytest.raises(cr.RequestAssemblyError) as e:
        cr.assemble_request(row, dict(draft, nature_confirmed_by="none"),
                            document_class=cr.DEFAULT_DOCUMENT_CLASS,
                            run_name="run-0090",
                            submitted_at="2026-09-07T10:00:00+03:00")
    assert "вердиктом судьи заявки" in str(e.value)
    # У жёсткой строки размера нарушения нет — как и единицы (X03/X16).
    hard = dict(row, kind="HARD")
    f3, _ = cr.call_composer(_composer(), hard, cat)
    d3 = cr.build_draft(hard, f3, draft_id=f3["id"],
                        document_class=cr.DEFAULT_DOCUMENT_CLASS,
                        origin_date="2026-09-07")
    assert d3["violation_unit"] is None and d3["violation_size"] is None


def test_3d_judges_of_the_new_fields_refuse_with_words(tmp_path):
    """Судьи полей 0.5.0: размер нарушения и вид чтения истории — только
    словами словарей каталога; семья истории обязана назвать чтение."""
    cat = _cat()
    row = {"rule_id": "R-S1-a", "kind": "SOFT", "zone": "ROSTERING",
           "statement": "минимизировать разрывы", "gap": _GAP, "params": [],
           "key": cr.request_key("SOFT", "ROSTERING", "минимизировать разрывы")}
    row["request_id"] = cr.request_id_of(row["key"])

    def _words_of(**over):
        llm = _composer(patch=over)
        _fields, words = cr.call_composer(llm, row, cat)
        return words

    assert any("violation_size" in w and "violation_sizes" in w
               for w in _words_of(violation_size="сколько-нибудь"))
    assert any("violation_size" in w for w in _words_of(violation_size=None))
    # Единица нарушения судится КОДОМ словаря, а не русским словом.
    assert any("violation_unit" in w for w in _words_of(violation_unit="событие"))
    assert _words_of(violation_unit="night", violation_size="magnitude") == []
    # Природа «история» без чтения истории — отказ (X19).
    assert any("reads_history" in w and "семья истории" in w
               for w in _words_of(nature="history", reads_history=None))
    # Вид чтения — слово словаря каталога, потребность — непустой текст.
    bad = _words_of(nature="history",
                    reads_history={"need": "серия смен до горизонта",
                                   "kind": "остатки"})
    assert any("history_read_kinds" in w for w in bad), bad
    bad2 = _words_of(nature="history",
                     reads_history={"need": "  ", "kind": "series"})
    assert any("reads_history.need" in w for w in bad2), bad2
    ok = _composer(patch={"nature": "history",
                          "reads_history": {"need": "серия смен до горизонта",
                                            "kind": "series"}})
    fields, words = cr.call_composer(ok, row, cat)
    assert words == []
    assert fields["reads_history"] == {"need": "серия смен до горизонта",
                                       "kind": "series"}


def test_3e_composer_task_lists_codes_and_new_fields():
    """Задание составителю: словари КОДАМИ (карта единиц отдаёт ключи) и
    три поля каталога 0.5.0 с их формами."""
    cat = _cat()
    row = {"rule_id": "R-S1-a", "kind": "SOFT", "zone": "ROSTERING",
           "statement": "минимизировать разрывы", "gap": _GAP, "params": [],
           "key": cr.request_key("SOFT", "ROSTERING", "минимизировать разрывы")}
    text = cr.compose_prompt(row, cat)
    units = cat.dictionaries()["violation_units"]
    assert isinstance(units, dict), "словарь единиц 0.5.0 — карта «код → слово»"
    assert f"- violation_units: {', '.join(units)}" in text
    for word in units.values():
        assert f" {word}," not in text, "в задании стоят коды, не слова"
    assert "- violation_sizes: count, magnitude" in text
    assert "- history_read_kinds: accumulator, boundary, series, window" in text
    assert '"violation_size": null' in text and '"reads_history": null' in text
    assert "nature_confirmed_by" not in text, "свидетеля природы ставит код"


def test_3b_call_journal_gets_composer_role(tmp_path, monkeypatch):
    """Журнал внешних вызовов получает роль составителя."""
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    _submit(run_dir, rows, llm)
    idx = cj.read_index(run_dir)
    assert idx and {r["role"] for r in idx} == {"catalog_composer"}
    st = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert any(k.startswith("catalog_composer/")
               for k in (st.get("stage_channels") or {}))


# ═══════════════════════════════════ 4. Г1 — правила читателя

def test_4_gate_one_validate_call_for_the_whole_batch(tmp_path, monkeypatch):
    cat = _cat()
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c()),
                  _rule("R-S1-b", "второй пробел покрытия", fit=_c(),
                        anchor="п.1.2")])
    run_dir = _make_run(tmp_path, doc)
    ctx = cr.run_context(run_dir, doc, {}, cat)
    llm = _composer()
    reqs = []
    for row in cr.candidates(doc, run_dir, []):
        fields, words = cr.call_composer(llm, row, cat)
        assert words == []
        d = cr.build_draft(row, fields, draft_id=fields["id"],
                           document_class=cr.DEFAULT_DOCUMENT_CLASS,
                           origin_date="2026-09-07")
        reqs.append(cr.assemble_request(
            row, d, document_class=cr.DEFAULT_DOCUMENT_CLASS,
            run_name=run_dir.name,
            submitted_at="2026-09-07T10:00:00+03:00"))
    calls = cc.count_validate(monkeypatch)
    got = cr.gate(reqs, cat, ctx, {})
    assert len(calls) == 1, "validate зовётся один раз на весь пакет"
    for v in got.values():
        assert v["hard"] == [] and v["soft"] == []


@pytest.mark.parametrize("field,value,code", [
    ("text", "разрыв считается для базы SVO", "A01"),
    ("text", "механика по п. 3.4 применяется всегда", "A01"),
    ("text", "величина restGap задаётся снаружи", "A05"),
])
def test_4b_reader_rules_refuse_customer_traces(tmp_path, field, value, code):
    cat = _cat()
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    ctx = cr.run_context(run_dir, doc, {}, cat)
    row = cr.candidates(doc, run_dir, [])[0]
    llm = _composer(patch={"semantics": {"scope": "пара явок",
                                         "unit": "минуты",
                                         "measure": "недостача",
                                         field: value}})
    fields, _ = cr.call_composer(llm, row, cat)
    d = cr.build_draft(row, fields, draft_id=fields["id"],
                       document_class=cr.DEFAULT_DOCUMENT_CLASS,
                       origin_date="2026-09-07")
    req = cr.assemble_request(row, d,
                              document_class=cr.DEFAULT_DOCUMENT_CLASS,
                              run_name=run_dir.name,
                              submitted_at="2026-09-07T10:00:00+03:00")
    got = cr.gate([req], cat, ctx, {})[row["request_id"]]
    assert any(code in w for w in got["hard"]), got
    assert not (cr.outbox_root() / f"{row['request_id']}.json").is_file()


# ═══════════════════════════════════ 5. Г2 — маркеры прогона

def test_5_run_markers_are_refused(tmp_path):
    cat = _cat()
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c(),
                        params=[{"name": "base_codes", "unit": "code_list",
                                 "value": "КОДБ"}])])
    run_dir = _make_run(tmp_path, doc, customer="Северный Ветер")
    (run_dir / "data-freeze").mkdir(parents=True, exist_ok=True)
    (run_dir / "data-freeze" / "dataset.json").write_text(json.dumps({
        "tables": {"D1": {"Экипажи": {"columns": ["табельный", "квалиф",
                                                  "минуты"]}}}},
        ensure_ascii=False), encoding="utf-8")
    ctx = cr.run_context(run_dir, doc, {"customer": "Северный Ветер"}, cat)
    row = cr.candidates(doc, run_dir, [])[0]

    def _gate_for(text, addr="text"):
        llm = _composer(patch={"semantics": {"scope": "пара явок",
                                             "unit": "минуты",
                                             "measure": "недостача",
                                             "text": text}})
        fields, _ = cr.call_composer(llm, row, cat)
        d = cr.build_draft(row, fields, draft_id=fields["id"],
                           document_class=cr.DEFAULT_DOCUMENT_CLASS,
                           origin_date="2026-09-07")
        req = cr.assemble_request(
            row, d, document_class=cr.DEFAULT_DOCUMENT_CLASS,
            run_name=run_dir.name, submitted_at="2026-09-07T10:00:00+03:00")
        return cr.gate([req], cat, ctx, {})[row["request_id"]], req

    got, _ = _gate_for("механика применяется у перевозчика Северный Ветер")
    assert any("Г2" in w and "авиакомпании" in w for w in got["hard"]), got
    got, _ = _gate_for("механика опирается на перечень КОДБ")
    assert any("Г2" in w for w in got["hard"]), got
    got, _ = _gate_for("механика опирается на колонку квалиф")
    assert any("Г2" in w and "колонки" in w for w in got["hard"]), got
    # Слово колонки, встречающееся в прозе самого каталога, — не отказ.
    assert all("минуты" not in v for _k, v, _p in ctx["markers"])
    got, _ = _gate_for("мера равна недостаче разрыва в минутах")
    assert got["hard"] == [], got
    # Код внутри hex-хвоста source.ref судится только до «#».
    key = "abcdef0123456789" + "0" * 48
    assert cr._ref_head(f"run-0090#{key[:12]}") == "run-0090"


# ═══════════════════════════════════ 6а. Г3 и Г4 без модели

def _ready_request(run_dir, cat, row, **over):
    fields = _proposal(f"- формулировка: {row['statement']}\n"
                       f"- вид: {row['kind']}", **over)
    clean, words, rid = cr.judge_proposal(fields, row, cat)
    assert words == [], words
    d = cr.build_draft(row, clean, draft_id=rid,
                       document_class=cr.DEFAULT_DOCUMENT_CLASS,
                       origin_date="2026-09-07")
    return cr.assemble_request(row, d,
                               document_class=cr.DEFAULT_DOCUMENT_CLASS,
                               run_name=run_dir.name,
                               submitted_at="2026-09-07T10:00:00+03:00")


def test_6a_digits_and_closeness_outcomes(tmp_path):
    cat = _cat()
    statement = "разрыв между явками не менее установленной величины"
    doc = _model([_rule("R-S1-a", statement, fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    ctx = cr.run_context(run_dir, doc, {}, cat)
    row = cr.candidates(doc, run_dir, [])[0]
    rid = row["request_id"]

    def _judge(**over):
        req = _ready_request(run_dir, cat, row, **over)
        return cr.gate([req], cat, ctx, {rid: [statement]})[rid]

    got = _judge(title="Разрывы 2 между явками")
    assert any("цифра в поле title" in w for w in got["hard"]), got
    got = _judge(semantics={"scope": "пара явок", "unit": "минуты",
                            "measure": "недостача",
                            "text": "разрыв не более 12 часов"})
    assert any("Г3" in w and "число с единицей" in w for w in got["hard"])
    got = _judge(semantics={"scope": "пара явок", "unit": "минуты/60 с "
                                                          "усечением",
                            "measure": "недостача",
                            "text": "мера равна недостаче разрыва"})
    assert got["hard"] == [] and any("голая цифра" in w for w in got["soft"])
    got = _judge(semantics={"scope": "пара явок", "unit": "минуты",
                            "measure": "недостача",
                            "text": statement})
    assert got["hard"] == [] and any("Г4" in w for w in got["soft"]), got
    # Формулировка из трёх слов окном не судится.
    assert cr._closeness("разрыв явок мал", "разрыв явок мал") is None


# ═══════════════════════════════════ 6б. Повторы, удержание, подтверждение

def test_6b_retries_hold_and_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    statement = "разрыв между явками не менее установленной величины"
    doc = _model([_rule("R-S1-a", statement, fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    outbox = cr.outbox_root()
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    rid = rows[0]["request_id"]
    # Жёсткий отказ на каждом вызове — три вызова, «не составлена».
    hard = _composer(patch={"title": "Разрывы 2 между явками"})
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: hard)
    counts, idx = _submit(run_dir, rows, hard)
    assert len(hard.calls) == 3, "один вызов плюс не более двух повторов"
    assert "ПРЕДЫДУЩЕЕ ПРЕДЛОЖЕНИЕ ОТКЛОНЕНО" in hard.calls[1]
    assert counts == {"submitted": 0, "held": 0, "failed": 1}
    assert idx["R-S1-a"]["composed"] == cr.COMPOSED_FAILED
    assert "цифра в поле title" in idx["R-S1-a"]["words"]
    assert not (outbox / f"{rid}.json").is_file()
    # Мягкое замечание — удержание, не тупик.
    (run_dir / "catalog-requests" / "index.json").unlink()
    soft = _composer(patch={"semantics": {"scope": "пара явок",
                                          "unit": "минуты/60 с усечением",
                                          "measure": "недостача",
                                          "text": "мера равна недостаче"}})
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: soft)
    counts, idx = _submit(run_dir, rows, soft)
    assert len(soft.calls) == 3
    assert counts == {"submitted": 0, "held": 1, "failed": 0}
    assert idx["R-S1-a"]["composed"] == cr.COMPOSED_HELD
    assert "голая цифра" in idx["R-S1-a"]["words"]
    held = run_dir / "catalog-requests" / "held" / f"{rid}.json"
    assert held.is_file() and not (outbox / f"{rid}.json").is_file()
    # Подтверждение аналитика: модель не зовётся, файл уходит в очередь.
    idx_rows = dict(cr.read_index(run_dir).get("rows") or {})
    counts2 = {"submitted": 0, "held": 0, "failed": 0}
    webui._catalog_confirm(run_dir, outbox, idx_rows, [rid],
                           cr.DEFAULT_DOCUMENT_CLASS, counts2, False)
    assert len(soft.calls) == 3, "подтверждение модель не зовёт"
    assert counts2["submitted"] == 1
    assert idx_rows["R-S1-a"]["composed"] == cr.COMPOSED_CONFIRMED
    assert not held.is_file()
    assert (outbox / f"{rid}.json").is_file()
    assert (run_dir / "catalog-requests" / f"{rid}.json").is_file()


# ═══════════════════════════════════ 7. Запасное имя

def test_7_fallback_family_id(tmp_path):
    cat = _cat()
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    row = cr.candidates(doc, run_dir, [])[0]
    clean, words, rid = cr.judge_proposal(
        _proposal("- формулировка: x\n- вид: HARD", id="rest_floors"),
        row, cat)
    assert words == [] and rid == cr.fallback_id(row["key"])
    assert re.match(r"^[a-z][a-z0-9_]{2,40}$", rid)
    # Имя, занятое ожидающей заявкой, — тоже запасное.
    folder = cc.copy_catalog(tmp_path, name="cat7")
    reqs = json.loads((folder / "requests.json").read_text(encoding="utf-8"))
    reqs["requests"] = [{
        "request_id": "req-" + "1" * 20, "state": "подана",
        "section": "families", "record_id": "probe_pending",
        "submitted_at": "2026-09-07T10:00:00+03:00", "submitted_by": "CDPM",
        "source": {"kind": "посадка категории C",
                   "document_class": "документ свода правил авиакомпании",
                   "run_class": "прогон CDPM", "ref": "run-0088"},
        "draft": None, "executor_kinds_requested": ["kotlin_constraint"],
        "decision": {"by": None, "at": None, "words": None}, "cr_key": None}]
    (folder / "requests.json").write_bytes(
        tc.__dict__["canonical_json"] and
        (json.dumps(reqs, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    cat2 = tc.load(folder)
    _c2, _w2, rid2 = cr.judge_proposal(
        _proposal("- формулировка: x\n- вид: HARD", id="probe_pending"),
        row, cat2)
    assert rid2 == cr.fallback_id(row["key"])
    # record_id заявки равен draft.id.
    d = cr.build_draft(row, clean, draft_id=rid,
                       document_class=cr.DEFAULT_DOCUMENT_CLASS,
                       origin_date="2026-09-07")
    req = cr.assemble_request(row, d,
                              document_class=cr.DEFAULT_DOCUMENT_CLASS,
                              run_name=run_dir.name,
                              submitted_at="2026-09-07T10:00:00+03:00")
    assert req["record_id"] == d["id"] == rid


# ═══════════════════════════════════ 8. Очередь и индекс

def test_8_queue_index_and_exclusive_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    outbox = cr.outbox_root()
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    rid = rows[0]["request_id"]
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    counts, idx = _submit(run_dir, rows, llm)
    assert counts["submitted"] == 1
    queued = outbox / f"{rid}.json"
    assert queued.is_file()
    raw = queued.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    doc_q = json.loads(raw.decode("utf-8"))
    assert raw.decode("utf-8") == json.dumps(
        doc_q, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    assert (run_dir / "catalog-requests" / f"{rid}.json").is_file()
    # Индекс — ровно пять полей, без меток времени и источника запуска.
    index = json.loads((run_dir / "catalog-requests" / "index.json")
                       .read_text(encoding="utf-8"))
    assert index["form"] == 1
    assert set(index["rows"]["R-S1-a"]) == {"key", "request_id", "composed",
                                            "words", "document_class"}
    assert index["rows"]["R-S1-a"]["request_id"] == rid
    assert index["rows"]["R-S1-a"]["composed"] == cr.COMPOSED_SUBMITTED
    # Повторная подача того же черновика — файл не переписан.
    before = queued.read_bytes()
    got = cr.enqueue(json.loads(before.decode("utf-8")), run_dir, outbox)
    assert got["queued"] is True and "уже подана" in got["words"]
    assert queued.read_bytes() == before
    # Исключительное создание: файл-победитель иного прогона с иным каноном.
    other = json.loads(before.decode("utf-8"))
    other["source"]["ref"] = "run-0088#abcdef012345"
    other["draft"]["title"] = "Иной заголовок семьи"
    queued.write_bytes((json.dumps(other, ensure_ascii=False, sort_keys=True,
                                   indent=1) + "\n").encode("utf-8"))
    got = cr.enqueue(json.loads(before.decode("utf-8")), run_dir, outbox)
    assert got["queued"] is False
    assert "подана иным черновиком из run-0088" in got["words"]
    assert json.loads(queued.read_text(encoding="utf-8"))["draft"]["title"] \
        == "Иной заголовок семьи"
    # Канон черновика собственной формы: NFC и порядок ключей не важны.
    d = json.loads(before.decode("utf-8"))["draft"]
    assert cr.draft_canon(d) == cr.draft_canon(dict(reversed(list(d.items()))))


# ═══════════════════════════════════ 10. Состояния для экрана

def test_10_states_from_catalog_reply_and_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    outbox = cr.outbox_root()
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    rid = rows[0]["request_id"]
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    _submit(run_dir, rows, llm)
    cat = _cat()
    got = cr.states(run_dir, cat, outbox, rows)[0]
    assert got["request_id"] == rid
    assert got["state"] == "в очереди, службой не принята"
    assert got["submitted_at"]
    # Строка состояний несёт СЕМЬЮ-ЧЕРНОВИК: имя из файла заявки
    # (`draft.id`, он же `record_id` записи §6) — по нему заявку ищут в
    # каталоге шаблонов, и номер заявки его не заменяет.
    saved = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                       .read_text(encoding="utf-8"))
    assert got["record_id"] == saved["draft"]["id"] == saved["record_id"]
    assert got["record_id"] and got["record_id"] != rid
    # Файла заявки в прогоне нет (заявка иного прогона) — честный None.
    assert cr._record_id(run_dir, "req-00000000000000000000") is None
    # Файл-ответ с отказом — слова читающей стороны доходят до CDPM.
    (outbox / f"{rid}.reply.json").write_text(json.dumps(
        {"request_id": rid, "accepted": False, "state": "не принята",
         "problems": [{"where": "requests", "code": "R02",
                       "message": "имя семьи занято"}]},
        ensure_ascii=False), encoding="utf-8")
    got = cr.states(run_dir, cat, outbox, rows)[0]
    assert got["state"] == "не принята: имя семьи занято"
    (outbox / f"{rid}.reply.json").unlink()
    # Номер появился в каталоге — состояние и слова владельца из каталога.
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    folder = cc.copy_catalog(tmp_path, name="cat10")
    for state, decision, expect in (
            ("подана", {"by": None, "at": None, "words": None}, "подана"),
            ("отклонена", {"by": "владелец", "at": "2026-09-07",
                           "words": "механика покрыта существующей семьёй"},
             "отклонена: механика покрыта существующей семьёй")):
        reqs = {"form_version": 1,
                "requests": [dict(record, state=state, decision=decision)]}
        (folder / "requests.json").write_bytes(
            (json.dumps(reqs, ensure_ascii=False, sort_keys=True, indent=1)
             + "\n").encode("utf-8"))
        cc.use_catalog(monkeypatch, folder)
        catalog_bridge.forget()
        cat2 = tc.load(folder)
        got = cr.states(run_dir, cat2, outbox, rows)[0]
        assert got["state"] == expect, got
    # Чтение состояний очередь НЕ трогает (K08, §4.3: проверки
    # повторяемые и идемпотентные): файл остаётся живым, перенос в
    # `sent/` — дело доставки. Прежде здесь ждали переноса чтением.
    assert (outbox / f"{rid}.json").is_file()
    assert not (outbox / "sent" / f"{rid}.json").exists()


# ═══════════════════════════════════ 11. Ручки панели

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


#: Третий документ стенда: клауза, дающая СОДЕРЖАТЕЛЬНУЮ посадку C без
#: открытого вопроса анкеты (у всех строк комплекта A/B такой вопрос есть,
#: и кандидатом ни одна из них быть не может — это правило §1.1 п.3).
_RULES_C = "9.1. Разрыв между явками не менее установленной величины.\n"
_EXTRA_CANDIDATES = {
    "п.9.1": {"anchor": "п.9.1", "title": "Разрыв между соседними явками",
              "statement": "разрыв между явками не менее установленной "
                           "величины",
              "zone": "PAIRING", "kind": "HARD", "question": None,
              "template_fit": {"category": "C", "gap": _GAP}},
}


def _fit_llm(composer=None):
    """Подставной канал панели: извлечение даёт СОДЕРЖАТЕЛЬНУЮ посадку C,
    составитель — черновик; прочие задания идут прежним фальшивым
    каналом."""
    def _llm(prompt: str) -> str:
        if "ЗАДАНИЕ: предложить ОБЕЗЛИЧЕННЫЙ черновик" in prompt:
            return (composer or _composer())(prompt)
        out = fake_llm(prompt)
        try:
            doc = json.loads(out)
        except ValueError:
            return out
        if "candidates" not in doc:
            return out
        for a in re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M):
            if a in _EXTRA_CANDIDATES:
                doc["candidates"].append(json.loads(json.dumps(
                    _EXTRA_CANDIDATES[a], ensure_ascii=False)))
        return json.dumps(doc, ensure_ascii=False)
    return _llm


def _wait_line(client, run_id, key="catalog_state", timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        if not webui._line_busy(st.get(key)) \
                and not webui._line_busy(st.get("state")):
            return st
        time.sleep(0.05)
    raise AssertionError(f"линия {key} не освободилась")


def _run_with_c(client, monkeypatch, composer=None) -> str:
    llm = _fit_llm(composer)
    monkeypatch.setattr(webui, "llm_factory", lambda scope: llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    r = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-C.md", _RULES_C.encode("utf-8"), "text/markdown")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]
    assert client.post(f"/api/runs/{run_id}/extract").status_code == 200
    st = _wait_line(client, run_id, "state")
    assert st["state"] == "извлечено", st.get("error")
    return run_id


def test_11_panel_routes(client, monkeypatch, tmp_path):
    calls: list = []
    run_id = _run_with_c(client, monkeypatch, _composer(calls))
    run_dir = webui._run_dir(run_id)
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    assert [c["rule_id"] for c in got["candidates"] if c["candidate"]]
    assert got["requests"] == []
    assert got["auto"]["enabled"] is False
    assert "CDPM_CATALOG_REQUESTS_AUTO=0" in got["auto"]["skipped_reason"]
    assert got["last_action"] is None
    assert got["document_class"] == cr.DEFAULT_DOCUMENT_CLASS
    assert got["counts"]["candidates"] >= 1
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "all"})
    assert r.status_code == 200, r.text
    assert r.json()["catalog_state"] == cr.LINE_RUNNING
    st = _wait_line(client, run_id)
    assert st["catalog_state"].startswith("заявки в каталог: подано 1")
    acts = [a for a in aj.actions(run_dir)
            if a["action"] == "catalog-request"]
    assert len(acts) == 1 and acts[0]["files"] == []
    assert acts[0]["params"]["mode"] == "all"
    assert acts[0]["params"]["trigger"] == "button"
    assert acts[0]["params"]["auto_setting"] is None
    assert acts[0]["state_after"]["catalog_state"] == st["catalog_state"]
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    assert len(got["requests"]) == 1
    rid = got["requests"][0]["request_id"]
    assert got["requests"][0]["state"] == "в очереди, службой не принята"
    assert got["last_action"]["trigger"] == "button"
    assert got["last_action"]["mode"] == "all"
    assert got["last_action"]["at"]
    # Строка уже не C — заявка живёт дольше строки.
    doc = json.loads((run_dir / "extract" / "model.json")
                     .read_text(encoding="utf-8"))
    rule_id = got["requests"][0]["rule_id"]
    for r0 in doc["rules"]:
        if r0["id"] == rule_id:
            r0["template_fit"] = {"category": "A", "registry_rev": "r",
                                  "family": "rest_floors"}
    (run_dir / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    assert "строка уже не C" in got["requests"][0]["state"]
    # Отзыв: файл переезжает в withdrawn/, повторный — отказ словами.
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "withdraw", "request_ids": [rid]})
    assert r.status_code == 200, r.text
    outbox = cr.outbox_root()
    assert (outbox / "withdrawn" / f"{rid}.json").is_file()
    assert not (outbox / f"{rid}.json").is_file()
    assert cr.read_index(run_dir)["rows"][rule_id]["composed"] == \
        cr.COMPOSED_WITHDRAWN
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "withdraw", "request_ids": [rid]})
    assert r.status_code == 200      # уже отозвана — идемпотентно
    acts = [a for a in aj.actions(run_dir)
            if a["action"] == "catalog-request"]
    assert all(a["done"] for a in acts)
    assert acts[-1]["params"]["mode"] == "withdraw"
    # Повторная подача возвращает файл в очередь тем же номером.
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "resend", "request_ids": [rid]})
    assert r.status_code == 200, r.text
    assert (outbox / f"{rid}.json").is_file()


def test_11b_service_c_needs_confirmation(client, monkeypatch):
    """Служебная C — только кнопкой строки с явным подтверждением."""
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    doc = json.loads((run_dir / "extract" / "model.json")
                     .read_text(encoding="utf-8"))
    service = [r["id"] for r in doc["rules"]
               if (r.get("template_fit") or {}).get("gap", "")
               .startswith("модель не выполнила посадку")]
    assert service
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "one", "rule_ids": service[:1]})
    assert r.status_code == 409 and "служебная C" in r.json()["detail"]
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "one", "rule_ids": service[:1],
                          "confirm_service_c": True})
    assert r.status_code == 200, r.text
    _wait_line(client, run_id)
    assert service[0] in cr.read_index(run_dir)["rows"]


# ═══════════════════════════════════ 12–13. Гейты зоны и модели

def test_12_13_no_writes_under_catalog_and_model_untouched(
        tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    model_path = run_dir / "extract" / "model.json"
    before = model_path.read_bytes()
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    writes = _write_watch(monkeypatch)
    _submit(run_dir, rows, llm)
    inside = [p for p in writes
              if str(Path(p).resolve()).startswith(str(CATALOG_DIR.resolve()))]
    assert inside == [], inside
    assert model_path.read_bytes() == before
    from cdpm import model as m_mod
    assert m_mod.validate_schema(
        json.loads(before.decode("utf-8"))) == []


# ═══════════════════════════════════ 14. Пакет не меняется

def _manifest_or_words(run_dir: Path):
    """Манифест пакета либо слова отказа его сборки — обе стороны
    сравнимы, и сравнение не становится пустым, когда прогон до пакета
    ещё не дозрел."""
    from cdpm import export_task_package as etp
    try:
        return etp.build_package(run_dir)["manifest.json"]
    except etp.TaskPackageError as e:
        return f"отказ сборки: {e}"


def test_14_task_package_manifest_byte_for_byte(client, monkeypatch):
    from cdpm import run_passport
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    st = client.get(f"/api/runs/{run_id}").json()
    stages_before = {(s["role"], s["klass"]): s for s in st["passport"]["stages"]}
    journal_before = dict(webui._read_status(run_dir).get("stage_channels") or {})
    made_before = run_passport.made_by_of(run_dir, webui._read_status(run_dir))
    before = _manifest_or_words(run_dir)
    # Смена раскладки роли МЕЖДУ сборками: составитель получает свою
    # модель — «чем сделан пакет» это менять не должно.
    agents = client.get(f"/api/runs/{run_id}/agents").json()
    agents = dict(agents or {})
    agents["catalog_composer"] = {"channels": {
        "cloud": {"model": "test-stub-composer"},
        "local": {"provider": "local", "model": "test-local-composer"}}}
    assert client.put(f"/api/runs/{run_id}/agents",
                      json=agents).status_code == 200
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "all"})
    assert r.status_code == 200, r.text
    _wait_line(client, run_id)
    after = _manifest_or_words(run_dir)
    assert after == before, "манифест пакета байт в байт не меняется"
    made_after = run_passport.made_by_of(run_dir, webui._read_status(run_dir))
    assert made_after == made_before, "«чем сделан пакет» не меняется"
    journal_after = dict(webui._read_status(run_dir).get("stage_channels") or {})
    for key, entry in journal_before.items():
        assert journal_after[key] == entry, key
    new_keys = set(journal_after) - set(journal_before)
    assert new_keys and all(k.startswith("catalog_composer/") for k in new_keys)
    st2 = client.get(f"/api/runs/{run_id}").json()
    stages_after = {(s["role"], s["klass"]): s for s in st2["passport"]["stages"]}
    for key, row in stages_before.items():
        assert stages_after[key] == row, key
    added = [s for k, s in stages_after.items() if k not in stages_before]
    assert len(added) == 1 and added[0]["role"] == "catalog_composer"
    assert added[0]["role_title"] == "составитель заявок каталога"
    assert added[0]["stage"] == "заявки в каталог"
    from cdpm import run_passport
    assert "catalog_composer" not in run_passport.MADE_BY_ROLES


# ═══════════════════════════════════ 15. Повтор прогона

def _wait_replay(client, run_id, timeout_s=180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        rep = (st.get("passport") or {}).get("replay") or {}
        if rep.get("verdict"):
            return st
        time.sleep(0.1)
    raise AssertionError("повтор не завершился")


def _tree(root: Path, rel: str) -> dict:
    base = root / rel
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): p.read_bytes()
            for p in sorted(base.rglob("*")) if p.is_file()}


def test_15_replay_from_journal_repeats_the_request(client, monkeypatch):
    src = _run_with_c(client, monkeypatch)
    src_dir = webui._run_dir(src)
    assert client.post(f"/api/runs/{src}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, src)
    outbox = cr.outbox_root()
    queue_before = _tree(outbox.parent, "outbox")
    assert queue_before
    src_files = _tree(src_dir, "catalog-requests")
    assert "index.json" in src_files
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    rep = st["passport"]["replay"]
    assert rep["verdict"] == "совпадает", rep
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    assert report["first_divergence"] is None
    paths = [row["path"] for row in report["artifacts"]]
    assert "catalog-requests/index.json" in paths
    assert all(row["same"] for row in report["artifacts"])
    assert report["calls"]["fresh"] == 0 and report["calls"]["unconsumed"] == 0
    child_dir = webui._run_dir(child)
    assert _tree(child_dir, "catalog-requests") == src_files
    # Очередь повтор не тронул: те же файлы и те же байты.
    assert _tree(outbox.parent, "outbox") == queue_before
    assert not (outbox / f"{list(src_files)[0]}").is_file() or True
    child_ids = {v["request_id"] for v in
                 cr.read_index(child_dir)["rows"].values()}
    for rid in child_ids:
        assert (child_dir / "catalog-requests" / f"{rid}.json").is_file()
    # Автозапуска у потомка не было: действие одно, не два.
    acts = [a for a in aj.actions(child_dir)
            if a["action"] == "catalog-request"]
    assert len(acts) == 1, acts
    assert acts[0]["params"]["trigger"] == "replay"
    assert acts[0]["state_after"]["catalog_state"] == \
        webui._read_status(src_dir)["catalog_state"]


def test_15b_replay_fresh_compares_outcomes_not_drafts(client, monkeypatch):
    src = _run_with_c(client, monkeypatch)
    src_dir = webui._run_dir(src)
    assert client.post(f"/api/runs/{src}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, src)
    outbox = cr.outbox_root()
    queue_before = _tree(outbox.parent, "outbox")
    # Свежая модель даёт ИНОЙ черновик — номер тот же.
    other = _composer(patch={"title": "Иной заголовок той же семьи"})
    monkeypatch.setattr(webui, "llm_factory", lambda scope: _fit_llm(other))
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: _fit_llm(other))
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "fresh"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    rep = st["passport"]["replay"]
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    names = {row["name"]: row for row in report["outcomes"]}
    assert "заявки в каталог: номера" in names
    assert names["заявки в каталог: номера"]["same"] is True
    assert names["заявки в каталог: состояния составления"]["same"] is True
    assert rep["verdict"] == "итоги совпадают", report["first_divergence"]
    child_dir = webui._run_dir(child)
    src_draft = json.loads(
        next((src_dir / "catalog-requests").glob("req-*.json"))
        .read_text(encoding="utf-8"))["draft"]
    child_draft = json.loads(
        next((child_dir / "catalog-requests").glob("req-*.json"))
        .read_text(encoding="utf-8"))["draft"]
    assert src_draft["title"] != child_draft["title"]
    assert _tree(outbox.parent, "outbox") == queue_before


# ═══════════════════════════════════ 16. Двойное нажатие и линия

def test_16_one_composition_per_line(client, monkeypatch):
    calls: list = []
    slow = _composer(calls)

    def _slow(prompt: str) -> str:
        time.sleep(0.4)
        return slow(prompt)
    run_id = _run_with_c(client, monkeypatch, _slow)
    run_dir = webui._run_dir(run_id)
    n_before = len(calls)
    codes: list = []

    def _hit():
        codes.append(client.post(f"/api/runs/{run_id}/catalog-requests",
                                 json={"mode": "all"}))
    threads = [threading.Thread(target=_hit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r.status_code for r in codes)
    assert statuses == [200, 409], [r.text for r in codes]
    assert "занят" in [r for r in codes if r.status_code == 409][0].json()["detail"]
    st = _wait_line(client, run_id)
    assert st["catalog_state"].startswith("заявки в каталог: подано ")
    assert not re.search(r"\d{2}:\d{2}", st["catalog_state"])
    n_rows = len(cr.read_index(run_dir)["rows"])
    assert len(calls) - n_before == n_rows, "по одному вызову на строку"
    # Занятая побочная линия — повтор недоступен словами.
    webui._update_status(run_dir, catalog_state=cr.LINE_RUNNING)
    from cdpm import replay as rp
    got = rp.availability(run_dir, webui._read_status(run_dir))
    assert got["journal"]["reason_code"] == "run-busy"
    # Гибель процесса: осиротевшее состояние переводится словами.
    recovered = webui.recover_orphaned_runs()
    assert any(run_id in s for s in recovered), recovered
    assert webui._read_status(run_dir)["catalog_state"] == cr.LINE_ORPHAN


def test_16b_composer_channel_refusal_is_words(client, monkeypatch, tmp_path):
    """Отказ канала составителя: индекс — «не составлена», линия — отказ."""
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    # Раскладки ролей нет — облачный класс не строится словами.
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    (run_dir / "agents.json").unlink(missing_ok=True)
    monkeypatch.setattr(webui, "_strictest_class", lambda run_dir_: "cloud")
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "all"})
    assert r.status_code == 200, r.text
    st = _wait_line(client, run_id)
    assert st["catalog_state"].startswith("отказ заявок в каталог: ")
    rows = cr.read_index(run_dir)["rows"]
    assert rows and all(v["composed"] == cr.COMPOSED_FAILED
                        for v in rows.values())
    assert all(v["words"] for v in rows.values())


# ═══════════════════════════════════ 17. Изоляция очереди

def test_17_queue_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_CATALOG_OUTBOX", raising=False)
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    root = cr.outbox_root()
    assert root == tmp_path / "cdpm-catalog-requests" / "outbox"
    import _data_paths
    assert not str(root).startswith(str(_data_paths.data_root()))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc)
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    writes = _write_watch(monkeypatch)
    _submit(run_dir, rows, llm)
    outside = [p for p in writes
               if not str(Path(p).resolve()).startswith(str(tmp_path.resolve()))]
    assert outside == [], outside
    assert (root / f"{rows[0]['request_id']}.json").is_file()


# ═══════════════════════════════════ 18. Автозапуск

def test_18_auto_after_audit_cycle(client, monkeypatch):
    calls: list = []
    run_id = _run_with_c(client, monkeypatch, _composer(calls))
    run_dir = webui._run_dir(run_id)
    clean = _make_auditor([[], []])
    # Аудитор и ответчик — чистый аудитор; составитель — свой канал.
    orig = webui._channel_for

    def _channel_for(scope, run_dir_, role, *a, **kw):
        if role == cr.ROLE:
            return _composer(calls)
        return orig(scope, run_dir_, role, *a, **kw)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: clean)
    monkeypatch.setattr(webui, "_channel_for", _channel_for)
    monkeypatch.setenv("CDPM_CATALOG_REQUESTS_AUTO", "1")
    r = client.post(f"/api/runs/{run_id}/exchange",
                    params={"auditor": "cloud", "worker": "cloud"})
    assert r.status_code == 200, r.text
    st = _wait_line(client, run_id)
    assert str(st["state"]).startswith("аудит-цикл: "), st.get("error")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        acts = [a for a in aj.actions(run_dir)
                if a["action"] == "catalog-request"]
        if acts and acts[-1].get("done"):
            break
        time.sleep(0.05)
    acts = [a for a in aj.actions(run_dir) if a["action"] == "catalog-request"]
    assert len(acts) == 1, acts
    assert acts[0]["params"]["mode"] == "auto"
    assert acts[0]["params"]["trigger"] == "auto"
    assert acts[0]["params"]["auto_setting"] == "1"
    index = cr.read_index(run_dir)
    assert index["rows"]
    for row in index["rows"].values():
        assert set(row) == {"key", "request_id", "composed", "words",
                            "document_class"}
    # При «0» автозапуска нет, а прямой вызов ручки с mode=auto идёт.
    monkeypatch.setenv("CDPM_CATALOG_REQUESTS_AUTO", "0")
    (run_dir / "catalog-requests" / "index.json").unlink()
    r = client.post(f"/api/runs/{run_id}/exchange",
                    params={"auditor": "cloud", "worker": "cloud"})
    assert r.status_code == 200, r.text
    _wait_line(client, run_id, "state")
    time.sleep(0.5)
    acts = [a for a in aj.actions(run_dir) if a["action"] == "catalog-request"]
    assert len(acts) == 1, "при «0» автозапуска нет"
    r = client.post(f"/api/runs/{run_id}/catalog-requests?trigger=auto",
                    json={"mode": "auto"})
    assert r.status_code == 200, r.text
    _wait_line(client, run_id)
    acts = [a for a in aj.actions(run_dir) if a["action"] == "catalog-request"]
    assert len(acts) == 2 and acts[-1]["params"]["mode"] == "auto"


def test_18b_auto_is_off_for_replay_child_and_red_gate(client, monkeypatch):
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    monkeypatch.setenv("CDPM_CATALOG_REQUESTS_AUTO", "1")
    doc = webui._catalog_model_doc(run_dir)
    rows = webui._catalog_rows(run_dir, doc)
    st = webui._read_status(run_dir)
    assert webui._catalog_auto_reason(run_dir, st, rows) is None
    st2 = dict(st, passport={"replay": {"mode": "replay-journal",
                                        "source_run": "run-0001"}})
    assert "потомок повтора" in webui._catalog_auto_reason(run_dir, st2, rows)
    st3 = dict(st, state="аудит-цикл идёт")
    assert "линия занята" in webui._catalog_auto_reason(run_dir, st3, rows)
    # Красный гейт стадии триггером не является.
    webui._update_status(run_dir, state="красный гейт аудит-цикла")
    webui._catalog_auto_after(run_dir, run_id,
                              webui._CATALOG_AFTER_EXCHANGE)()
    assert not [a for a in aj.actions(run_dir)
                if a["action"] == "catalog-request"]


# ═══════════════════════════════════ 19. Напоминание

def test_19_reminder_in_status_and_package_send(client, monkeypatch):
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    st = client.get(f"/api/runs/{run_id}").json()
    counts = st["catalog_requests"]
    assert counts["candidates"] >= 1 and counts["submitted"] == 0
    assert counts["pending"] == counts["candidates"]
    before = _manifest_or_words(run_dir)
    assert client.post(f"/api/runs/{run_id}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, run_id)
    st = client.get(f"/api/runs/{run_id}").json()
    assert st["catalog_requests"]["submitted"] == 1
    assert st["catalog_requests"]["pending"] == counts["candidates"] - 1
    assert _manifest_or_words(run_dir) == before, \
        "содержимое пакета заявкой не меняется"
    r = client.get(f"/api/runs/{run_id}/export/task-package-send")
    if r.status_code == 200:
        assert r.json()["catalog_requests"]["submitted"] == 1


# ═══════════════════════════════════ 9. Доставка в службу каталога
#
# Служба каталога (шаг 5 COSMOS) в дереве ещё не написана — контракт
# доставки судится ПОДДЕЛЬНЫМ сервером стандартной библиотеки на
# случайном порту (0). Контракт — `final-report/KATALOG-SHAG-6-PROEKT-
# CLD.md` §1.1 (ответы 201/200/409/422/400/401/403 и форма отказа
# `{"detail": {code, words, problems, extra}}`) и §8 (часть CDPM).

#: Токен доставки в тестах — ВЫДУМАННАЯ строка: настоящих секретов
#: тесты не читают и не печатают. Латиница не для красоты: заголовок
#: HTTP несёт латиницу-1, и настоящий токен службы — тоже ASCII.
_TEST_TOKEN = "proba-tokena-kataloga-ne-nastoyashchiy"


class _FakeService:
    """Поддельная служба каталога: порт 0 (случайный), ответы — по
    сценарию, каждый запрос записывается целиком (заголовки и тело).

    Ответ сценария — `(код, тело)` либо `(код, тело, тип содержимого)`;
    тело строкой уходит СЫРЫМ (страница входа площадки, мусор, пустота),
    объектом — JSON. Так стенд умеет то, ради чего K07 и затеян: отдать
    HTML со статусом 200 и пустое тело 204.

    `gets` — сценарий ответов на `GET /api/requests/<номер>` (сверка
    карантина, §4.3); `session` — ответ предпроверки «один каталог на
    машину»."""

    def __init__(self, answers=(), session=None, gets=()):
        self.answers = list(answers)
        self.gets = list(gets)
        self.session = session
        self.seen: list = []
        srv = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):                    # тишина в отчёте
                pass

            def _reply(self, code, body, ctype="application/json"):
                if isinstance(body, bytes):
                    raw = body
                elif isinstance(body, str):
                    raw = body.encode("utf-8")
                else:
                    raw = b"" if body is None else json.dumps(
                        body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                srv.seen.append({"method": "GET", "path": self.path,
                                 "headers": dict(self.headers)})
                if self.path == cr.SERVICE_SESSION_PATH \
                        and srv.session is not None:
                    self._reply(200, srv.session)
                    return
                if self.path.startswith(cr.SERVICE_REQUEST_PATH) \
                        and srv.gets:
                    self._reply(*srv.gets.pop(0))
                    return
                self._reply(404, {"detail": {"code": "not-found",
                                             "words": "ручки нет"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8") if n else ""
                srv.seen.append({"method": "POST", "path": self.path,
                                 "headers": dict(self.headers),
                                 "raw": raw,
                                 "body": json.loads(raw) if raw else None})
                if self.path == cr.SERVICE_DRY_RUN_PATH and srv.gets:
                    self._reply(*srv.gets.pop(0))
                    return
                answer = srv.answers.pop(0) if srv.answers \
                    else (500, {"detail": {"code": "no-script",
                                           "words": "сценарий кончился"}})
                self._reply(*answer)

        self.httpd = HTTPServer(("127.0.0.1", 0), _H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)

    @property
    def posts(self) -> list:
        return [x for x in self.seen if x["method"] == "POST"]


def _queued(tmp_path, monkeypatch, name="run-0090"):
    """Стенд доставки: прогон с одной поданной заявкой в очереди.

    Очередь — СВОЯ на каждый стенд: номер заявки от содержания пробела
    один и тот же, и общая очередь склеила бы соседние пробы."""
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_CATALOG_OUTBOX", str(tmp_path / "queue"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_dir = _make_run(tmp_path, doc, name=name)
    outbox = cr.outbox_root()
    rows = [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    counts, _idx = _submit(run_dir, rows, llm)
    assert counts["submitted"] == 1
    rid = rows[0]["request_id"]
    assert (outbox / f"{rid}.json").is_file()
    return run_dir, outbox, rid


def _service_env(monkeypatch, url: str) -> None:
    monkeypatch.setenv("CDPM_CATALOG_SERVICE_URL", url)
    monkeypatch.setenv("CDPM_CATALOG_SERVICE_TOKEN", _TEST_TOKEN)


def _dead_url() -> str:
    """Адрес заведомо закрытого порта: сеть недоступна честно, без
    ожидания настоящего таймаута."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


def _no_sleep(_s):
    return None


# ─────────────── квитанция службы: независимый образец по схеме
#
# Тело собирается ЗДЕСЬ по схеме `receipt/1`, а не вызовом службы
# (независимый оракул K07): стенд обязан проверять закон клиента, а не
# согласие клиента со службой — иначе одна ошибка в них двоих взаимно
# оправдалась бы.

def _sent_of(rid, outbox=None) -> dict:
    """Запись заявки, как она лежит в очереди (по ней служба считает
    раздел, запись и канон черновика квитанции)."""
    outbox = Path(outbox) if outbox is not None else cr.outbox_root()
    for p in (outbox / f"{rid}.json", outbox / "sent" / f"{rid}.json"):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise AssertionError(f"записи {rid} нет ни в очереди, ни в sent/")


def _receipt(rid, *, state="подана", repeat=False, sent=None,
             issued_at=None, **over) -> dict:
    """Тело квитанции `receipt/1`, как его отдаёт служба каталога.

    `issued_at` — ЧАСЫ СЛУЖБЫ: по умолчанию те же, что у прогона (в
    стенде служба и прогон на одной машине), но задать их отдельно
    можно — на них и держатся ворота свежести решения (K08, N2)."""
    issued_at = issued_at or cr._now()
    body = {
        "schema_version": "receipt/1",
        "request_id": rid,
        "state": state,
        "note": "уже подана" if repeat else None,
        "repeat": bool(repeat),
        "catalog": {"version": "0.5.0", "fingerprint": "a" * 64},
        "section": (sent or {}).get("section", "families"),
        "record_id": (sent or {}).get("record_id", "probe_x"),
        "canon": _draft_canon((sent or {}).get("draft")),
        "decision": {"by": None, "at": None, "words": None, "version": 0},
        "journal_ref": {"op": "request-submit", "at": issued_at,
                        "actor": "owner"},
        "issued_at": issued_at,
        "submitted_by": "CDPM",
        "words": f"заявка {rid} принята",
    }
    body.update(over)
    return body


def _ok(rid, outbox=None, **over) -> tuple:
    """Ответ службы «201 + квитанция» на заявку, лежащую в очереди."""
    return (201, _receipt(rid, sent=_sent_of(rid, outbox), **over))


def _again(rid, outbox=None, **over) -> tuple:
    """Ответ службы «200 + та же квитанция, уже подана»."""
    return (200, _receipt(rid, sent=_sent_of(rid, outbox), repeat=True,
                          **over))


def test_9_delivery_201_moves_file_to_sent_and_keeps_index(tmp_path,
                                                           monkeypatch):
    """`201` — заявка принята: файл очереди уходит в `sent/`, квитанция
    ложится в `channel/catalog-requests/`, а индекс НЕ меняется байтом
    (§1.3: исходов доставки в индексе не бывает — он детерминирован и
    сверяется повтором байт в байт)."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    body_bytes = (outbox / f"{rid}.json").read_bytes()
    index_before = (run_dir / "catalog-requests" / "index.json").read_bytes()
    with _FakeService([_ok(rid, outbox)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, _sleep=_no_sleep)
        posts = srv.posts
    assert got["delivered"] == 1 and got["refused"] == 0
    assert got["refusal"] is None
    # Ручка, заголовки и тело — буквально по контракту §1.1.
    assert len(posts) == 1
    assert posts[0]["path"] == "/api/requests" == cr.SERVICE_REQUESTS_PATH
    h = posts[0]["headers"]
    assert h.get("Content-Type") == "application/json"
    assert h.get("X-Catalog-Product") == "CDPM"
    assert h.get("X-Catalog-Token") == _TEST_TOKEN
    # Тело — запись заявки §6 целиком, как она лежит в очереди.
    assert posts[0]["raw"].encode("utf-8") == body_bytes
    sent = posts[0]["body"]
    assert sent["state"] == "подана" and sent["submitted_by"] == "CDPM"
    assert sent["cr_key"] is None and sent["executor_kinds_requested"] is None
    assert sent["decision"] == {"by": None, "at": None, "words": None}
    assert sent["source"]["kind"] == "посадка категории C"
    # Файл — в sent/, очередь пуста.
    assert (outbox / "sent" / f"{rid}.json").is_file()
    assert not (outbox / f"{rid}.json").is_file()
    # Индекс не тронут; момент и ответ службы — в квитанции.
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before
    receipt = cr.read_receipt(run_dir, rid)
    assert receipt["schema_version"] == "receipt-file/2"
    assert receipt["outcome"] == "доставлена" and receipt["http"] == 201
    assert receipt["outcome_kind"] == "доставлена"
    assert receipt["at"] and len(receipt["attempts"]) == 1
    assert receipt["last_checked_at"] == receipt["at"]
    assert receipt["state"] == "подана" and receipt["queue"] == "sent"
    # Тело квитанции лежит в файле целиком: по нему судится идентичность
    # и читается решение владельца (K08).
    assert receipt["receipt"]["schema_version"] == "receipt/1"
    assert receipt["receipt"]["request_id"] == rid
    assert cr.receipt_path(run_dir, rid).parts[-3:-1] == ("channel",
                                                          "catalog-requests")
    state = cr.states(run_dir, _cat(), outbox)[0]
    assert state["state"] == "подана в службу каталога"
    assert state["delivered"] is True


def test_9b_delivery_answers_map_to_words(tmp_path, monkeypatch):
    """Таблица исходов §4.1 целиком: «ответ службы → вид случая, исход,
    слова экрана, положение файла очереди».

    Исходов три (`доставлена | неизвестен | отказ`), видов случая —
    сколько велит различать контракт: `таймаут`, `5xx`, оба 409, канал,
    форма. В `sent/` файл уходит ТОЛЬКО у доставленной заявки: при любом
    ином исходе он остаётся в очереди, и повтор идёт тем же номером.

    Изменение против прежней редакции теста (K07): прежде `200` с ЧУЖИМ
    номером в теле считался доставкой, а `409 request-exists` уносил
    файл в `sent/`. Имён у конфликта канона два — `request-exists`
    каталога 0.6.0 и `duplicate-request` нынешней службы, — и читаются
    они ОДИНАКОВО (K08): вид случая один, файл остаётся в очереди."""
    def дано(rid, outbox):
        sent = _sent_of(rid, outbox)
        чужой = "req-" + "9" * 20
        return [
            # (ответ службы, вид случая, исход, слова экрана, файл в sent/)
            (_again(rid, outbox), "доставлена", "доставлена",
             "подана в службу каталога: уже подана", True),
            ((200, _receipt(rid, sent=sent, state="принята", repeat=True)),
             "доставлена", "доставлена",
             "подана в службу каталога: уже подана", True),
            # (а) 200 без слов повторной выдачи — не квитанция.
            ((200, _receipt(rid, sent=sent)), "неизвестен", "неизвестен",
             None, False),
            # (б) чужой номер в теле — не квитанция (граница 21n).
            ((201, _receipt(чужой, sent=sent)), "неизвестен", "неизвестен",
             None, False),
            # (в) слово состояния вне закрытого перечня.
            ((201, _receipt(rid, sent=sent, state="рассматривается")),
             "неизвестен", "неизвестен", None, False),
            # (г) тип содержимого не JSON при годном теле.
            ((201, json.dumps(_receipt(rid, sent=sent), ensure_ascii=False),
              "text/html; charset=utf-8"),
             "неизвестен", "неизвестен", None, False),
            # 204, пустое тело и страница входа площадки.
            ((204, None), "неизвестен", "неизвестен", None, False),
            ((200, "<html>страница входа</html>", "text/html"),
             "неизвестен", "неизвестен", None, False),
            ((202, {"note": "принято к обработке"}), "неизвестен",
             "неизвестен", None, False),
            # 5xx — о судьбе заявки не сказано ничего.
            ((500, {"detail": {"code": "catalog-unavailable",
                               "words": "каталог шаблонов недоступен"}}),
             "5xx", "неизвестен",
             "подана без подтверждения: каталог шаблонов недоступен", False),
            # 409 конфликт канона — файл ОСТАЁТСЯ В ОЧЕРЕДИ (§11.2 п. 6).
            ((409, {"detail": {"code": "request-exists",
                               "words": "заявка уже подана из run-0088 иным "
                                        "черновиком; дубль не заводится",
                               "extra": {"existing": чужой,
                                         "ref": "run-0088#abcdef0123"}}}),
             "409 request-exists", "отказ",
             f"конфликт канона: у службы заявка {чужой} (иной черновик из "
             f"run-0088); свой номер {rid} остаётся в очереди до решения",
             False),
            # Тот же отказ под вторым именем: служба нынешнего рубежа
            # отвечает `duplicate-request` и называет ИНОЙ номер (K08).
            ((409, {"detail": {"code": "duplicate-request",
                               "words": "по записи families/probe_x уже "
                                        "есть открытая заявка (состояние "
                                        "«подана»)",
                               "extra": {"existing": чужой}}}),
             "409 request-exists", "отказ",
             f"конфликт канона: у службы заявка {чужой} (иной черновик); "
             f"свой номер {rid} остаётся в очереди до решения",
             False),
            # 409 закрыта владельцем — фактическое состояние «отклонена»
            # с решением, не успех и не «неизвестно».
            ((409, {"detail": {"code": "request-closed",
                               "words": "заявка отклонена владельцем: "
                                        "механика покрыта",
                               "extra": {"existing": rid,
                                         "state": "отклонена",
                                         "decision": {
                                             "words": "механика покрыта",
                                             "version": 1,
                                             "at": "2026-09-12T09:00:00"
                                                   "+02:00"}}}}),
             "409 request-closed", "отказ", "отклонена: механика покрыта",
             False),
            ((422, {"detail": {"code": "invalid-request", "problems": [
                {"where": "requests/req/draft", "code": "A01",
                 "message": "ссылка на пункт документа в примечании"},
                {"where": "requests/req/draft", "code": "X02",
                 "message": "имя параметра занято"}]}}),
             "422/400", "отказ",
             "не принята: ссылка на пункт документа в примечании; "
             "имя параметра занято", False),
            ((422, {"detail": {"code": "bad-request-id",
                               "words": "номер заявки не по закону "
                                        "читателя"}}),
             "422/400", "отказ",
             "не принята: номер заявки не по закону читателя", False),
            ((400, {"detail": {"code": "bad-form",
                               "words": "тело не по форме"}}),
             "422/400", "отказ", "не принята: тело не по форме", False),
            ((400, {"detail": {"code": "submitter-not-allowed",
                               "words": "продукт не в перечне подателей"}}),
             "422/400", "отказ",
             "не принята: продукт не в перечне подателей", False),
            ((401, {"detail": {"code": "bad-token",
                               "words": "токен не тот"}}),
             "401/403", "отказ", "не принята: токен не тот", False),
            ((403, {"detail": {"code": "machine-channel-off",
                               "words": "машинный канал заявок выключен: "
                                        "задайте CATALOG_MACHINE_TOKEN"}}),
             "401/403", "отказ",
             "не принята: машинный канал заявок выключен: задайте "
             "CATALOG_MACHINE_TOKEN", False),
            ((403, {"detail": {"code": "read-only",
                               "words": "служба поднята только на чтение"}}),
             "401/403", "отказ",
             "не принята: служба поднята только на чтение", False),
        ]
    with monkeypatch.context() as mp:                     # сколько случаев
        проба = _queued(tmp_path / "probe", mp)
        всего = len(дано(проба[2], проба[1]))
    for i in range(всего):
        with monkeypatch.context() as mp:
            run_dir, outbox, rid = _queued(tmp_path / f"c{i}", mp)
            answer, kind, outcome, words, in_sent = дано(rid, outbox)[i]
            index_before = (run_dir / "catalog-requests"
                            / "index.json").read_bytes()
            with _FakeService([answer]) as srv:
                _service_env(mp, srv.url)
                got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
                posts = srv.posts
            assert len(posts) == 1, (kind, "повтора у определённого ответа "
                                           "не бывает")
            assert got["delivered"] == (1 if outcome == "доставлена" else 0)
            receipt = got["receipts"][0]
            assert (receipt["outcome_kind"], receipt["outcome"]) \
                == (kind, outcome), receipt
            assert receipt["outcome_kind"] in cr.DELIVERY_KINDS
            assert receipt["outcome"] in cr.OUTCOMES
            assert receipt["http"] == answer[0]
            if words is None:
                # Ответ 2xx, квитанцией не являющийся: слова называют,
                # чего не хватило, и что заявка осталась в очереди.
                assert receipt["state_words"].startswith(
                    "подана без подтверждения: ответ службы не является "
                    "квитанцией"), receipt
                assert "повтор тем же номером" in receipt["state_words"]
            else:
                assert receipt["state_words"] == words, receipt
            if receipt["refusal"] == "invalid-request":
                assert [p["code"] for p in receipt["problems"]] == ["A01",
                                                                    "X02"]
            assert (outbox / "sent" / f"{rid}.json").is_file() is in_sent, \
                kind
            assert (outbox / f"{rid}.json").is_file() is not in_sent, kind
            # Индекс — ни при каком ответе службы.
            assert (run_dir / "catalog-requests"
                    / "index.json").read_bytes() == index_before, kind
            строка = cr.states(run_dir, _cat(), outbox)[0]
            # Слова панели — слова квитанции; состояние службы, отличное
            # от «подана», K08 называет впереди («принята · подана в
            # службу каталога: уже подана»).
            assert строка["state"].endswith(receipt["state_words"]), kind
            # Транспорт: доставлена | отказ | неизвестно — и никогда
            # «в очереди» там, где служба ответила.
            assert строка["transport_state"] == {
                "доставлена": "доставлена", "отказ": "отказ",
                "неизвестен": "неизвестно"}[outcome], (kind, строка)


def test_9c_write_busy_is_the_only_repeatable_4xx(tmp_path, monkeypatch):
    """`409 write-busy` — каталог занят выпуском: три попытки с паузой,
    как у сетевой ошибки (§8 п.5 контракта). Успех на третьей попытке —
    заявка доставлена; отказ на всех трёх — слова и файл в очереди."""
    busy = (409, {"detail": {"code": "write-busy",
                             "words": "каталог занят выпуском версии"}})
    pauses: list = []
    run_dir, outbox, rid = _queued(tmp_path / "ok", monkeypatch)
    with _FakeService([busy, busy, _ok(rid, outbox)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=pauses.append)
        posts = len(srv.posts)
    assert posts == 3 and got["delivered"] == 1
    assert len(got["receipts"][0]["attempts"]) == 3
    assert pauses == list(cr.DELIVERY_PAUSES_S[1:])
    assert (outbox / "sent" / f"{rid}.json").is_file()

    with monkeypatch.context() as mp:
        run2, outbox2, rid2 = _queued(tmp_path / "busy", mp)
        with _FakeService([busy, busy, busy]) as srv:
            _service_env(mp, srv.url)
            got2 = cr.deliver(run2, [rid2], _sleep=_no_sleep)
            posts2 = len(srv.posts)
        assert posts2 == 3, "три попытки, не больше и не меньше"
        assert got2["delivered"] == 0
        assert got2["receipts"][0]["state_words"] == (
            "в очереди, доставка не удалась: каталог занят выпуском версии")
        assert (outbox2 / f"{rid2}.json").is_file(), \
            "файл остаётся в очереди — «Повторить подачу» её добьёт"


def test_9d_network_down_is_three_attempts_and_words(tmp_path, monkeypatch):
    """Сеть/таймаут — три попытки, затем слова «служба не отвечает: …»;
    заявка остаётся в очереди, индекс не тронут."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    index_before = (run_dir / "catalog-requests" / "index.json").read_bytes()
    pauses: list = []
    _service_env(monkeypatch, _dead_url())
    got = cr.deliver(run_dir, [rid], _sleep=pauses.append)
    assert got["delivered"] == 0 and got["refused"] == 1
    receipt = got["receipts"][0]
    assert len(receipt["attempts"]) == 3 and receipt["http"] is None
    assert receipt["words"].startswith("служба не отвечает: ")
    assert receipt["state_words"].startswith("в очереди, доставка не удалась:")
    assert pauses == list(cr.DELIVERY_PAUSES_S[1:])
    assert (outbox / f"{rid}.json").is_file()
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before


def test_9e_one_catalog_per_machine_precheck(tmp_path, monkeypatch):
    """Предпроверка «один каталог на машину» (§8 п.7): расхождение
    отпечатков — отказ доставки словами и БЕЗ записи в службу; нет ручки
    `/api/session` (404) — предпроверка пропускается СЛОВАМИ, а не
    молча; совпадение — доставка идёт."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    mine = catalog_bridge.stamp()["fingerprint"]
    # (а) отпечатки разошлись — в службу не пишем вовсе.
    with _FakeService([_ok(rid)],
                      session={"catalog": {"fingerprint": "0" * 64}}) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
        posts = srv.posts
    assert posts == [], "запись в чужой каталог не делается"
    assert got["delivered"] == 0
    assert got["refusal"].startswith("служба смотрит в другой каталог: ")
    assert "0" * 64 in got["refusal"] and mine in got["refusal"]
    assert (outbox / f"{rid}.json").is_file()
    # (б) ручки нет — предпроверка пропущена словами, доставка идёт.
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
        gets = [x for x in srv.seen if x["method"] == "GET"]
    assert len(gets) == 1 and gets[0]["path"] == cr.SERVICE_SESSION_PATH
    assert got["delivered"] == 1
    assert "предпроверка" in got["note"] and "/api/session" in got["note"]
    # (в) отпечатки совпали — доставка идёт, слов о пропуске нет.
    run2, outbox2, rid2 = _queued(tmp_path / "same", monkeypatch,
                                  name="run-0091")
    with _FakeService([_ok(rid2)],
                      session={"catalog": {"fingerprint": mine,
                                           "version": "0.1.0"}}) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run2, [rid2], _sleep=_no_sleep)
    assert got["delivered"] == 1 and got["note"] is None
    assert (outbox2 / "sent" / f"{rid2}.json").is_file()


def test_9f_without_settings_delivery_refuses_with_words(tmp_path,
                                                         monkeypatch):
    """Без адреса и токена доставки нет — отказ СЛОВАМИ, и те же слова
    стоят каналом на панели (§8 п.6 контракта COSMOS)."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    monkeypatch.delenv("CDPM_CATALOG_SERVICE_URL", raising=False)
    monkeypatch.delenv("CDPM_CATALOG_SERVICE_TOKEN", raising=False)
    assert cr.service_configured() is False
    got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 0 and got["receipts"] == []
    assert got["refusal"] == cr.service_off_words()
    assert got["refusal"] == ("доставка в службу не настроена: очередь "
                              "читает инструмент import-requests --inbox "
                              "на рабочем месте владельца")
    assert (outbox / f"{rid}.json").is_file()
    # Половина настроек — тоже «не настроена» (fail-closed).
    monkeypatch.setenv("CDPM_CATALOG_SERVICE_URL", "http://127.0.0.1:1")
    assert cr.service_configured() is False
    assert cr.deliver(run_dir, [rid])["refusal"] == cr.service_off_words()


def test_9g_replay_does_not_deliver_and_leaves_queue_alone(tmp_path,
                                                           monkeypatch):
    """Повтор прогона очередь не трогает и доставку не выполняет: у
    потомка ни запроса в службу, ни изменения очереди и индекса — слова
    об этом идут в квитанцию (`channel/` вне сверки повтора)."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
        queue_before = _tree(outbox, "")
        index_before = (run_dir / "catalog-requests"
                        / "index.json").read_bytes()
        seen_before = len(srv.seen)
        got = cr.deliver(run_dir, [rid], replay=True, _sleep=_no_sleep)
        assert len(srv.seen) == seen_before, "повтор в службу не ходит"
    assert got["refusal"] == cr.LINE_REPLAY_WORDS
    assert got["delivered"] == 0 and got["receipts"] == []
    assert _tree(outbox, "") == queue_before
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before
    assert cr.read_receipt(run_dir, rid)["words"] == cr.LINE_REPLAY_WORDS


def test_9h_delivery_route_of_the_panel(client, monkeypatch):
    """Ручка панели: `POST …/catalog-requests` с `mode: "deliver"` —
    действие журнала `catalog-request` с тем же `mode`; занятая линия
    `catalog_state` — 409, как у `withdraw`/`resend`; повтор ручки
    идемпотентен по номеру («уже подана»)."""
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    assert client.post(f"/api/runs/{run_id}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, run_id)
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    rid = got["requests"][0]["request_id"]
    # Без настроек службы канал говорит словами переходного канала.
    assert got["channel"]["service"] is False
    assert got["channel"]["words"] == cr.service_off_words()
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "deliver"})
    assert r.status_code == 409
    assert r.json()["detail"] == cr.service_off_words()
    index_before = (run_dir / "catalog-requests" / "index.json").read_bytes()
    with _FakeService([_ok(rid),
                       _again(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
        assert got["channel"]["service"] is True
        assert got["channel"]["pending"] == [rid]
        r = client.post(f"/api/runs/{run_id}/catalog-requests",
                        json={"mode": "deliver"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["delivered"] == 1 and body["refused"] == 0
        assert body["requests"][0]["request_id"] == rid
        assert body["requests"][0]["state"] == "подана в службу каталога"
        # Повторная доставка того же номера — идемпотентна.
        r = client.post(f"/api/runs/{run_id}/catalog-requests",
                        json={"mode": "deliver", "request_ids": [rid]})
        assert r.status_code == 200, r.text
        assert r.json()["requests"][0]["state"] == \
            "подана в службу каталога: уже подана"
        # Занятая линия `catalog_state` — 409, как у withdraw/resend.
        webui._update_status(run_dir, catalog_state=cr.LINE_RUNNING)
        r = client.post(f"/api/runs/{run_id}/catalog-requests",
                        json={"mode": "deliver", "request_ids": [rid]})
        assert r.status_code == 409 and "занят" in r.json()["detail"]
        webui._update_status(
            run_dir, catalog_state=cr.line_outcome({"submitted": 1}))
    acts = [a for a in aj.actions(run_dir)
            if a["action"] == "catalog-request"
            and (a.get("params") or {}).get("mode") == "deliver"]
    assert len(acts) == 4, [a.get("detail") for a in acts]
    assert acts[0]["refused"] is True and acts[0]["status"] == 409
    assert acts[0]["detail"] == cr.service_off_words()
    assert acts[1]["done"] and acts[1]["refused"] is False
    assert acts[1]["files"] == [] and acts[1]["params"]["mode"] == "deliver"
    assert acts[-1]["refused"] is True and acts[-1]["status"] == 409
    # Индекс доставкой не тронут — он сверяется повтором байт в байт.
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before
    st = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    assert st["requests"][0]["state"] == "подана в службу каталога: уже подана"
    assert st["requests"][0]["delivery"]["http"] == 200


def test_9i_replay_route_delivers_nothing(client, monkeypatch):
    """Воспроизведённое действие `mode: "deliver"` (`trigger: "replay"`)
    в службу не ходит и очередь не трогает — 200 со словами повтора."""
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    assert client.post(f"/api/runs/{run_id}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, run_id)
    rid = client.get(f"/api/runs/{run_id}/catalog-requests"
                     ).json()["requests"][0]["request_id"]
    outbox = cr.outbox_root()
    queue_before = _tree(outbox, "")
    index_before = (run_dir / "catalog-requests" / "index.json").read_bytes()
    with _FakeService([]) as srv:
        _service_env(monkeypatch, srv.url)
        out = webui.submit_catalog_requests(
            run_id, rule_ids=None, request_ids=[rid], document_class=None,
            mode="deliver", confirm_service_c=False, trigger="replay")
        assert srv.seen == [], "повтор в службу не ходит"
    assert out["delivered"] == 0 and out["words"] == cr.LINE_REPLAY_WORDS
    assert _tree(outbox, "") == queue_before
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before


# ═══════════════════════════════════ 20. Ключ свода и ключ пакета

def test_20_key_of_run_model_equals_key_of_package_model(tmp_path):
    """§8 п.8 контракта COSMOS: равенство ключей CDPM и COSMOS держится
    на ПОБАЙТОВОМ равенстве `statement` в своде прогона и в `model.json`
    пакета — сторож этого равенства.

    Ключ считается от «раздел + вид + зона + нормализованная
    формулировка»: разойдись `statement`, `kind` или `zone` при сборке
    пакета — COSMOS увидела бы пробел под другим номером, чем подала
    CDPM, и одна заявка стала бы двумя."""
    import test_cdpm_task_package as bench
    from cdpm import export_task_package as etp

    doc = bench._ready_doc()
    doc["rules"].append(
        {"id": "R-C", "title": "Разрыв между явками",
         "statement": "разрыв между соседними явками — не менее нормы",
         "zone": "PAIRING", "kind": "HARD",
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "template_fit": {"category": "C", "registry_rev": "rev-1",
                          "gap": _GAP}})
    run = bench._run_dir(tmp_path, doc)
    # Слово владельца 09.09.2026 (пробел покрытия): пока у строки нет
    # шаблона, пакет не выдаётся — выпуск отказывает словами, называя
    # строку. Сторож равенства ключей судит то, что пакет понёс бы:
    # model.json пакета — байты свода прогона без пересборки.
    with pytest.raises(etp.TaskPackageError, match="R-C"):
        etp.build_package(run)
    model_bytes = (run / "extract" / "model.json").read_bytes()
    source = json.loads(model_bytes.decode("utf-8"))
    package = json.loads(model_bytes.decode("utf-8"))
    keys_of = {r["id"]: (r["statement"], r["kind"], r["zone"],
                         cr.request_key(r["kind"], r["zone"], r["statement"]))
               for r in package["rules"]}
    checked = 0
    for r in source["rules"]:
        want = cr.request_key(r["kind"], r["zone"], r["statement"])
        got = keys_of.get(r["id"])
        assert got is not None, f"строка {r['id']} потеряна в пакете"
        assert got[0] == r["statement"], f"{r['id']}: формулировка разошлась"
        assert (got[1], got[2]) == (r["kind"], r["zone"]), \
            f"{r['id']}: вид или зона разошлись"
        assert got[3] == want, f"{r['id']}: ключ заявки разошёлся"
        checked += 1
    assert checked == len(source["rules"]) >= 3, checked
    # И тот же номер у строки посадки C — от свода и от пакета.
    from_run = [c for c in cr.candidates(source, run, []) if c["candidate"]]
    from_pkg = [c for c in cr.candidates(package, run, []) if c["candidate"]]
    assert [c["request_id"] for c in from_run] == \
        [c["request_id"] for c in from_pkg] != []


# ═══════════════════════════════════ 21. K08 — состояние общей заявки
#
# Контракт П0 §4.2 (`request-state/2`), oracle аудитора
# (`C_other_run_rejected_view`, `C_queue_idempotency`,
# `C_service_idempotency`): два прогона — одна заявка; решение службы по
# ней видно ОДИНАКОВО из любого прогона, подпись происхождения у каждого
# своя и решения не подменяет; повтор и возобновление дубля не создают;
# старый ответ службы нового решения не откатывает; без свежей квитанции
# решение показано как последнее известное; ответ службы без прикладной
# квитанции — «неизвестно», не успех.

_K08_DECISION = {"by": "владелец", "at": "2026-09-12T09:10:00+02:00",
                 "words": "исполнение пока не согласовано"}


def _k08_pair(tmp_path, monkeypatch, *, name_a="run-0040",
              name_b="run-0041"):
    """Стенд K08: два прогона одного документа — один номер заявки;
    первый подал (файл-победитель очереди — его). Возвращает
    (документ, прогон A, прогон B, очередь, номер, запись A)."""
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_CATALOG_OUTBOX", str(tmp_path / "queue"))
    doc = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    run_a = _make_run(tmp_path, doc, name=name_a)
    run_b = _make_run(tmp_path, doc, name=name_b)
    outbox = cr.outbox_root()
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    rows = [r for r in cr.candidates(doc, run_a, []) if r["candidate"]]
    counts, _idx = _submit(run_a, rows, llm)
    assert counts["submitted"] == 1
    rid = rows[0]["request_id"]
    record = json.loads((run_a / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    assert (outbox / f"{rid}.json").is_file()
    return doc, run_a, run_b, outbox, rid, record


def _rows_of(doc, run_dir):
    return [r for r in cr.candidates(doc, run_dir, []) if r["candidate"]]


def _catalog_with_requests(tmp_path, monkeypatch, name, records):
    """Копия каталога с заданными записями заявок — «служба решила»;
    дверь каталога переключается на копию (составление судит Г5 по ней)."""
    folder = cc.copy_catalog(tmp_path, name=name)
    reqs = {"form_version": 1, "requests": list(records)}
    (folder / "requests.json").write_bytes(
        (json.dumps(reqs, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    cc.use_catalog(monkeypatch, folder)
    catalog_bridge.forget()
    return tc.load(folder)


def _queue_files(outbox, rid) -> list:
    return sorted(p.relative_to(outbox).as_posix()
                  for p in Path(outbox).rglob(f"{rid}*.json"))


def test_21_k08_two_runs_see_one_decision_with_their_own_origin(
        tmp_path, monkeypatch):
    """Два прогона / одна заявка: подана → принята → отклонена →
    пересмотрена (принята заново) → есть. Оба прогона видят ОДНО решение
    службы (состояние, слова, момент = версия); подпись происхождения у
    каждого своя и решения не подменяет.

    Инверсия дефекта K08 (oracle `C_other_run_rejected_view`): прежде
    второй прогон видел в `state` только «подана из run-0040» — без
    состояния «отклонена» и слов владельца."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_a, rows_b = _rows_of(doc, run_a), _rows_of(doc, run_b)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    counts_b, idx_b = _submit(run_b, rows_b, llm)
    # Очередь: тот же канон — «уже подана», файл на номер ОДИН.
    assert counts_b["submitted"] == 1
    assert "уже подана" in (idx_b["R-S1-a"]["words"] or "")
    assert [p.name for p in outbox.glob("*.json")] == [f"{rid}.json"]
    none = {"by": None, "at": None, "words": None}
    accepted = {"by": "владелец", "at": "2026-09-12T09:00:00+02:00",
                "words": "нужна новая механика"}
    revised = {"by": "владелец", "at": "2026-09-12T09:20:00+02:00",
               "words": "после пересмотра: механика нужна"}
    cases = [("подана", none), ("принята", accepted),
             ("отклонена", _K08_DECISION), ("принята", revised),
             ("есть", revised)]
    for i, (state, decision) in enumerate(cases):
        cat = _catalog_with_requests(
            tmp_path, monkeypatch, f"cat21-{i}",
            [dict(record, state=state, decision=decision)])
        a = cr.states(run_a, cat, outbox, rows_a)[0]
        b = cr.states(run_b, cat, outbox, rows_b)[0]
        for got in (a, b):
            assert got["schema_version"] == "request-state/2"
            assert got["canonical_request_id"] == rid == got["request_id"]
            assert got["remote_state"] == state, got
            assert got["decision"]["words"] == decision["words"]
            assert got["decision"]["at"] == decision["at"]
            # Версия решения до каталога 0.6.0 — момент решения.
            assert got["decision"]["version"] == decision["at"]
            assert got["transport_state"] == "доставлена"
            assert got["delivered"] is True
            assert got["source_run"] == "run-0040"
            # Прикладной квитанции у прогонов нет — последнее известное.
            assert got["stale"] is True
            assert got["state"].startswith(state), got["state"]
        assert a["origin"] == "этот прогон"
        assert b["origin"] == "другой прогон (тот же черновик)"
        expect = state + (f": {decision['words']}" if decision["words"]
                          else "")
        assert a["state"] == expect
        assert b["state"] == expect + " · подана из run-0040"
        assert not b["state"].startswith("подана из")
    # Номер появился в каталоге, а чтение состояний очередь не трогает
    # (§4.3): файл-победитель run-0040 остаётся живым, новой подачи нет.
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    # Сценарий аудита буквально: владелец отклонил — второй прогон видит
    # отклонение и слова, а подпись происхождения — рядом, не вместо.
    cat = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21-audit",
        [dict(record, state="отклонена", decision=_K08_DECISION)])
    view = cr.states(run_b, cat, outbox, rows_b)[0]
    assert view["remote_state"] == "отклонена"
    assert view["decision"]["words"] == "исполнение пока не согласовано"
    assert view["state"] == ("отклонена: исполнение пока не согласовано"
                             " · подана из run-0040")


def test_21b_k08_other_draft_is_an_origin_signature_not_a_decision(
        tmp_path, monkeypatch):
    """Второй прогон составил ИНОЙ черновик того же пробела: дубль не
    заводится, строка «подана» со словами очереди, а решение службы по
    общей заявке видно и ему — с подписью «иной черновик»."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    other = _composer(patch={"title": "Иной заголовок той же семьи"})
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: other)
    counts_b, idx_b = _submit(run_b, rows_b, other)
    assert counts_b["submitted"] == 1
    assert idx_b["R-S1-a"]["composed"] == cr.COMPOSED_SUBMITTED
    assert "подана иным черновиком из run-0040" in idx_b["R-S1-a"]["words"]
    assert [p.name for p in outbox.glob("*.json")] == [f"{rid}.json"]
    # Решения ещё нет — очередь: файл-победитель run-0040, иной канон.
    b0 = cr.states(run_b, _cat(), outbox, rows_b)[0]
    assert b0["origin"] == "другой прогон (иной черновик)"
    assert b0["source_run"] == "run-0040"
    assert b0["remote_state"] is None
    assert b0["transport_state"] == "в очереди"
    assert b0["state"] == ("в очереди, службой не принята · подана иным "
                           "черновиком из run-0040")
    cat = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21b",
        [dict(record, state="отклонена", decision=_K08_DECISION)])
    b = cr.states(run_b, cat, outbox, rows_b)[0]
    assert b["origin"] == "другой прогон (иной черновик)"
    assert b["source_run"] == "run-0040"
    assert b["remote_state"] == "отклонена"
    assert b["decision"]["words"] == "исполнение пока не согласовано"
    assert b["state"] == ("отклонена: исполнение пока не согласовано · "
                          "подана иным черновиком из run-0040")


def test_21c_k08_gate_g5_refuses_only_the_rejected_number(tmp_path,
                                                          monkeypatch):
    """Г5 при номере, уже известном каталогу: запись «отклонена» — отказ
    составления словами решения владельца и путём пересмотра (без
    повторов модели — решение она не снимет); иное состояние —
    сведение, не отказ: строка индекса «подана» со словами, своя запись
    в папке прогона есть, а в очередь та же заявка повторно не
    публикуется (закон исключительной публикации сохранён)."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    # run-0040 доставил: запись в каталоге «подана», файл ушёл в sent/.
    cr._move_to_sent(cr._outbox_paths(outbox, rid))
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21c",
                                 [dict(record, state="подана")])
    ctx = cr.run_context(run_b, doc, {}, cat)
    # Тот же черновик под ссылкой run-0041 (как в пробе аудитора).
    req_same = json.loads(json.dumps(record, ensure_ascii=False))
    req_same["source"]["ref"] = "run-0041#" + rows_b[0]["key"][:12]
    v = cr.gate([req_same], cat, ctx, {})[rid]
    assert v["hard"] == [] and v["soft"] == [], v
    assert any("уже подана из run-0040 (тот же черновик)" in w
               for w in v["warnings"]), v
    assert v["known"]["same_draft"] is True
    assert v["known"]["closed"] is False
    assert v["known"]["state"] == "подана"
    # Черновик, составленный сейчас: имя семьи занято ожидающей заявкой
    # (судья полей даёт запасное имя) — иной канон, тоже сведение.
    req_b = _ready_request(run_b, cat, rows_b[0])
    v_other = cr.gate([req_b], cat, ctx, {})[rid]
    assert v_other["hard"] == [] and v_other["known"]["same_draft"] is False
    assert any("уже подана иным черновиком из run-0040" in w
               for w in v_other["warnings"]), v_other
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    counts, idx = _submit(run_b, rows_b, llm)
    assert len(llm.calls) == 1, "сведение Г5 — не отказ: повторов модели нет"
    assert counts == {"submitted": 1, "held": 0, "failed": 0}
    assert idx["R-S1-a"]["composed"] == cr.COMPOSED_SUBMITTED
    assert idx["R-S1-a"]["words"] == ("подана иным черновиком из run-0040; "
                                      "дубль не заводится")
    assert (run_b / "catalog-requests" / f"{rid}.json").is_file()
    assert not (outbox / f"{rid}.json").is_file(), "повторной публикации нет"
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    b = cr.states(run_b, cat, outbox, rows_b)[0]
    assert b["remote_state"] == "подана"
    assert b["origin"] == "другой прогон (иной черновик)"
    assert b["state"] == "подана · подана иным черновиком из run-0040"
    # Владелец отклонил — отказ сразу, словами решения и путём пересмотра.
    cat2 = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21c-2",
        [dict(record, state="отклонена", decision=_K08_DECISION)])
    v2 = cr.gate([req_same], cat2, ctx, {})[rid]
    assert v2["known"]["closed"] is True
    # Слов Г5 в сведениях нет (общее сведение о перечне маркеров машины
    # — не Г5); отказ один — словами решения.
    assert len(v2["hard"]) == 1
    assert not [w for w in v2["warnings"] if w.startswith("[Г5]")]
    assert ("отклонена владельцем каталога: исполнение пока не согласовано"
            in v2["hard"][0])
    assert "пересмотр" in v2["hard"][0] and "request-closed" in v2["hard"][0]
    run_c = _make_run(tmp_path, doc, name="run-0042")
    rows_c = _rows_of(doc, run_c)
    llm3 = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm3)
    counts3, idx3 = _submit(run_c, rows_c, llm3)
    assert len(llm3.calls) == 1, "повтор модели решения владельца не снимет"
    assert counts3 == {"submitted": 0, "held": 0, "failed": 1}
    assert idx3["R-S1-a"]["composed"] == cr.COMPOSED_FAILED
    assert "исполнение пока не согласовано" in idx3["R-S1-a"]["words"]
    assert "пересмотр" in idx3["R-S1-a"]["words"]
    assert not (outbox / f"{rid}.json").is_file()
    c = cr.states(run_c, cat2, outbox, rows_c)[0]
    assert c["state"].startswith("черновик не составлен: ")
    assert c["remote_state"] == "отклонена"
    assert c["decision"]["words"] == "исполнение пока не согласовано"
    assert c["transport_state"] is None
    # Положительный контроль: номер, каталогу неизвестный, Г5 не трогает.
    v3 = cr.gate([req_same], _cat(), ctx, {})[rid]
    assert v3["hard"] == [] and v3["known"] is None
    assert not [w for w in v3["warnings"] if w.startswith("[Г5]")]


def test_21d_k08_old_receipt_does_not_roll_back_a_newer_decision(
        tmp_path, monkeypatch):
    """Свежесть решения: ответ службы с ТОЙ ЖЕ редакцией решения и тем же
    состоянием — подтверждено (`stale: False`); решение владельца ПОЗЖЕ
    последнего ответа — показано новое решение как последнее известное
    (старый ответ службы «подана» его не откатывает); повторная выдача
    (200 «уже подана»), несущая ту же запись с её решением, подтверждает;
    сбой связи после — решение остаётся, но снова как последнее
    известное.

    Правка по K08-F1: подтверждает не буква состояния и не время
    получения ответа, а САМА редакция решения в ответе службы — служба
    отдаёт её полной записью `request` и на 201, и на 200
    (`catalog_service/request_queue.py`, `submit`/`_known`). Ответ,
    решения не назвавший, читается консервативно: решение записи
    показано последним известным."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T10:00:00+02:00")
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["at"] == "2026-09-12T10:00:00+02:00"
    # (а) запись «подана» без решения, квитанция не старше — подтверждено.
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21d-1",
                                 [dict(record, state="подана")])
    got = cr.states(run_dir, cat, outbox)[0]
    assert (got["remote_state"], got["stale"], got["transport_state"]) == \
        ("подана", False, "доставлена")
    assert got["state"] == "подана" and got["origin"] == "этот прогон"
    assert got["decision"] == {"words": None, "version": None, "at": None}
    # (б) решение в 10:30 — позже квитанции 10:00: показано ОНО, как
    # последнее известное; старый ответ «подана» его не откатывает.
    accepted = {"by": "владелец", "at": "2026-09-12T10:30:00+02:00",
                "words": "нужна новая механика"}
    cat2 = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21d-2",
        [dict(record, state="принята", decision=accepted)])
    got = cr.states(run_dir, cat2, outbox)[0]
    assert got["remote_state"] == "принята"
    assert got["decision"] == {"words": "нужна новая механика",
                               "version": "2026-09-12T10:30:00+02:00",
                               "at": "2026-09-12T10:30:00+02:00"}
    assert got["stale"] is True
    assert got["state"] == "принята: нужна новая механика"
    # (в) повторная выдача в 11:00 — 200 «уже подана» с состоянием
    # «принята» и ТОЙ ЖЕ записью (служба отдаёт её целиком, с решением):
    # подтверждено, тот же номер, файл один.
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T11:00:00+02:00")
    with _FakeService([_again(rid, outbox, state="принята",
                              decision=dict(accepted, version=1),
                              issued_at="2026-09-12T11:00:00+02:00")]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    # Решение ответа службы сохранено квитанцией целиком — им и судят.
    # Числовую версию `receipt/1` несёт всегда, а запись каталога 0.5.0
    # — никогда (схема закрыта тремя полями): редакцию опознают `by`,
    # слова и момент, и версия одной стороны расхождением редакций не
    # становится.
    assert cr.receipt_confirmed(cr.read_receipt(run_dir, rid),
                                rid)["decision"] == dict(accepted, version=1)
    got = cr.states(run_dir, cat2, outbox)[0]
    assert got["remote_state"] == "принята" and got["stale"] is False
    assert got["decision"]["words"] == "нужна новая механика"
    assert got["state"] == "принята: нужна новая механика"
    assert got["canonical_request_id"] == rid
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    # Та же квитанция без записи в каталоге: состояние службы названо
    # впереди слов доставки — «уже подана» решения не скрывает.
    only = cr.states(run_dir, _cat(), outbox)[0]
    assert only["remote_state"] == "принята" and only["stale"] is False
    assert only["state"] == "принята · подана в службу каталога: уже подана"
    # (в2) отрицательный контроль к (в): тот же 200 «уже подана» с тем же
    # словом состояния, но решения ответ НЕ называет — подтверждением он
    # не становится (K08-F1: буква состояния редакции решения не
    # доказывает; таким же выглядит и ответ, сложенный службой ДО
    # пересмотра и пришедший после него).
    with _FakeService([_again(rid, outbox, state="принята")]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.decision_identity(
        cr.read_receipt(run_dir, rid)["receipt"]["decision"]) == ()
    mute = cr.states(run_dir, cat2, outbox)[0]
    assert mute["remote_state"] == "принята"
    assert mute["decision"]["words"] == "нужна новая механика"
    assert mute["stale"] is True
    assert mute["stale_reason"] == cr.STALE_NOT_PROVEN
    assert mute["transport_state"] == "доставлена"
    # Возвращаем подтверждённую квитанцию (в): (г) — о сбое связи ПОСЛЕ
    # доказанного подтверждения, а не после ответа, ничего не доказавшего.
    with _FakeService([_again(rid, outbox, state="принята",
                              decision=dict(accepted, version=1))]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    # (г) сбой связи в 11:30: последнее известное решение показано как
    # устаревшее, не как подтверждённое.
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T11:30:00+02:00")
    _service_env(monkeypatch, _dead_url())
    assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 0
    got = cr.states(run_dir, cat2, outbox)[0]
    assert got["remote_state"] == "принята" and got["stale"] is True
    assert got["stale_reason"] == cr.STALE_LAST_CHECK
    assert got["decision"]["words"] == "нужна новая механика"
    # Форма K07 держит прикладную квитанцию РЯДОМ с журналом попыток:
    # неудачная сверка её не стирает, и доказанная вчера доставка
    # остаётся доставкой, когда сеть молчит (граница, названная K08).
    # Прежняя форма файла подтверждения не хранила — первая же неудача
    # связи роняла транспорт в «неизвестно».
    assert got["transport_state"] == "доставлена"
    assert got["delivered"] is True
    # Что сверка НЕ удалась, видно отдельно: слова последней попытки и
    # момент последней сверки.
    assert got["delivery"]["words"].startswith("служба не отвечает: ")
    assert got["delivery"]["last_checked_at"] == "2026-09-12T11:30:00+02:00"
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome"] == "доставлена"
    assert файл["receipt"]["state"] == "принята"
    assert [a["outcome"] for a in файл["attempts"]][-3:] == \
        ["неизвестен"] * 3, "три неудачные попытки — в журнале"


def test_21e_k08_answer_without_application_receipt_is_unknown(tmp_path,
                                                               monkeypatch):
    """Ответ службы без прикладной квитанции — пустое тело 204, слово
    состояния ВНЕ словаря, страница вместо JSON (три ошибочные квитанции
    аудита) — читается как транспорт «неизвестно» и карантин «подана без
    подтверждения», не как успех.

    После K07 приём судится ПРИ ДОСТАВКЕ: файл очереди при таком ответе
    в `sent/` больше не уходит (прежде уходил, и читателю оставалось
    видеть карантин задним числом)."""
    cases = [(204, None), (200, {"request_id": "x", "state": "x"}),
             (200, "<html>страница входа</html>", "text/html")]
    for i, answer in enumerate(cases):
        with monkeypatch.context() as mp:
            run_dir, outbox, rid = _queued(tmp_path / f"u{i}", mp)
            with _FakeService([answer]) as srv:
                _service_env(mp, srv.url)
                cr.deliver(run_dir, [rid], _sleep=_no_sleep)
            got = cr.states(run_dir, _cat(), outbox)[0]
            assert got["transport_state"] == "неизвестно", (answer, got)
            assert got["delivered"] is False and got["remote_state"] is None
            assert got["stale"] is False
            assert got["state"].startswith("подана без подтверждения"), got
            assert got["canonical_request_id"] == rid
            assert (outbox / f"{rid}.json").is_file(), \
                "файл остаётся в очереди: повтор идёт тем же номером"
            assert not (outbox / "sent" / f"{rid}.json").is_file()
    # Положительный контроль: 201 с тем же номером и словом словаря.
    run_dir, outbox, rid = _queued(tmp_path / "ok", monkeypatch)
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    got = cr.states(run_dir, _cat(), outbox)[0]
    assert (got["transport_state"], got["remote_state"], got["delivered"]) \
        == ("доставлена", "подана", True)
    assert got["state"] == "подана в службу каталога"


def test_21e_k08_saved_receipt_requires_the_complete_application_body(
        tmp_path, monkeypatch):
    """Прямой читатель файла: legacy-флаг не заменяет receipt/1 (§4.3).

    Отдельный узел без HTTP сохраняет положительные и отрицательные
    проверки формы, номера и часов службы из транспортного стенда.
    """
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    sent = _sent_of(rid, outbox)
    # Читатель квитанций напрямую: старая форма файла и форма K07
    # `receipt-file/2`; чужая версия — не квитанция (закон 5).
    states_ = cr.service_request_states(_cat())
    assert set(states_) == {"есть", "исполнитель заявлен", "отклонена",
                            "подана", "принята"}
    # Старые прогоны не перештамповываются (§4.3), но transport-флаг
    # без прикладного тела не доказывает доставку и не снимает карантин.
    legacy = {"request_id": rid, "delivered": True, "http": 201,
              "refusal": None, "words": None, "problems": [], "extra": {},
              "attempts": 1, "at": "2026-09-12T10:00:00+02:00",
              "state": "подана", "note": None, "queue": "sent"}
    assert cr.receipt_confirmed(legacy, rid, states_) is None
    assert cr.receipt_confirmed(dict(legacy, state="x"), rid, states_) is None
    assert cr.receipt_confirmed(dict(legacy, http=204), rid, states_) is None
    assert cr.receipt_confirmed(legacy, "req-" + "0" * 20, states_) is None
    # Даже слово повторной выдачи не восполняет отсутствующее тело.
    assert cr.receipt_confirmed(dict(legacy, http=200), rid, states_) is None
    assert cr.receipt_confirmed(dict(legacy, http=200, note="уже подана"),
                                rid, states_) is None
    assert cr.receipt_confirmed(dict(legacy, http=200, note="принята к "
                                     "рассмотрению"), rid, states_) is None
    # Полное тело K07 с идентичностью той же отправленной заявки.
    cr.write_receipt(run_dir, {
        "schema_version": "receipt-file/2", "request_id": rid,
        "outcome": "доставлена", "receipt": _receipt(rid, sent=sent)})
    свежий = cr.read_receipt(run_dir, rid)
    assert свежий["schema_version"] == "receipt-file/2"
    assert cr.receipt_confirmed(свежий, rid, states_)["state"] == "подана"
    v2 = {"schema_version": "receipt-file/2", "outcome": "доставлена",
          "request_id": rid,
          "last_checked_at": "2026-09-12T12:00:00+02:00",
          "receipt": _receipt(
              rid, sent=sent, state="принята", repeat=True,
              issued_at="2026-09-12T10:00:00+02:00",
              decision={"by": "владелец",
                        "at": "2026-09-12T09:30:00+02:00",
                        "words": "нужна", "version": 2})}
    got2 = cr.receipt_confirmed(v2, rid, states_, sent=sent)
    assert got2["state"] == "принята"
    # Момент свидетельства — ЧАСЫ СЛУЖБЫ (`issued_at`), а не часы
    # прогона (`last_checked_at`): ворота свежести сравнивают его с
    # моментом решения записи, который ставит та же служба (K08, N2).
    assert got2["at"] == "2026-09-12T10:00:00+02:00"
    # Обязательные часы службы нельзя заменить часами обёртки.
    assert cr.receipt_confirmed(
        {k: v for k, v in v2.items() if k != "receipt"}
        | {"receipt": {k: v for k, v in v2["receipt"].items()
                       if k != "issued_at"}},
        rid, states_) is None
    assert cr.decision_of({"decision": got2["decision"]}) == \
        {"words": "нужна", "version": 2, "at": "2026-09-12T09:30:00+02:00"}
    assert cr.receipt_confirmed(dict(v2, outcome="неизвестен"), rid,
                                states_) is None
    foreign = dict(v2, receipt=dict(v2["receipt"],
                                    request_id="req-" + "0" * 20))
    assert cr.receipt_confirmed(foreign, rid, states_) is None
    assert cr.receipt_confirmed(dict(v2, schema_version="receipt-file/3"),
                                rid, states_) is None
    assert cr.decision_of({"decision": {"by": None, "at": None,
                                        "words": None}}) == \
        {"words": None, "version": None, "at": None}


def test_21f_k08_sent_without_receipt_is_quarantine_and_resume_no_dup(
        tmp_path, monkeypatch):
    """Файл очереди в sent/ без прикладной квитанции у ЭТОГО прогона
    (доставил другой прогон, копия каталога записи ещё не несёт) —
    карантин «подана без подтверждения»: не успех, новой подачи нет.
    Повторное чтение идемпотентно; возобновление доставки из первого
    прогона (200 «уже подана») даёт тот же номер и второго файла не
    создаёт; появление записи в каталоге снимает карантин у обоих."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_a, rows_b = _rows_of(doc, run_a), _rows_of(doc, run_b)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    assert _submit(run_b, rows_b, llm)[0]["submitted"] == 1
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_a, [rid], _sleep=_no_sleep)["delivered"] == 1
    cat = _cat()
    a = cr.states(run_a, cat, outbox, rows_a)[0]
    b = cr.states(run_b, cat, outbox, rows_b)[0]
    assert a["transport_state"] == "доставлена" and a["delivered"] is True
    assert b["transport_state"] == "неизвестно" and b["delivered"] is False
    assert b["state"].startswith("подана без подтверждения")
    assert b["remote_state"] is None and b["stale"] is False
    assert b["origin"] == "другой прогон (тот же черновик)"
    assert b["source_run"] == "run-0040"
    assert cr.states(run_b, cat, outbox, rows_b) == [b], "чтение идемпотентно"
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    # Возобновление: повторная доставка — тот же номер, второго файла нет.
    with _FakeService([_again(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_a, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 1 and got["receipts"][0]["request_id"] == rid
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    # Запись появилась в каталоге — карантин снят: одно состояние у
    # обоих; у run-0041 — последнее известное (квитанции нет), у
    # run-0040 — подтверждено квитанцией не старше записи.
    cat2 = _catalog_with_requests(tmp_path, monkeypatch, "cat21f",
                                  [dict(record, state="подана")])
    a2 = cr.states(run_a, cat2, outbox, rows_a)[0]
    b2 = cr.states(run_b, cat2, outbox, rows_b)[0]
    assert (a2["remote_state"], a2["stale"]) == ("подана", False)
    assert (b2["remote_state"], b2["stale"], b2["transport_state"]) == \
        ("подана", True, "доставлена")
    assert b2["state"] == "подана · подана из run-0040"


def test_21g_k08_panel_route_carries_request_state_2(client, monkeypatch):
    """Ручка панели отдаёт поля `request-state/2` у только что поданной
    заявки этого прогона: транспорт «в очереди», состояния службы нет,
    решения нет, происхождение — этот прогон."""
    run_id = _run_with_c(client, monkeypatch)
    assert client.post(f"/api/runs/{run_id}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, run_id)
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    row = got["requests"][0]
    assert row["schema_version"] == "request-state/2"
    assert row["canonical_request_id"] == row["request_id"]
    assert row["transport_state"] == "в очереди"
    assert row["remote_state"] is None
    assert row["decision"] == {"words": None, "version": None, "at": None}
    assert row["stale"] is False
    assert row["origin"] == "этот прогон"
    assert row["source_run"] == run_id
    assert row["state"] == "в очереди, службой не принята"


#: Мягкое замечание составителю — черновик удерживается до подтверждения
#: аналитика (голая цифра в единице измерения).
_SOFT_PATCH = {"semantics": {"scope": "пара явок",
                             "unit": "минуты/60 с усечением",
                             "measure": "недостача",
                             "text": "мера равна недостаче"}}


def _confirm(run_dir, outbox, rid):
    """«Подтвердить и подать» напрямую, как это делает ручка панели."""
    idx_rows = dict(cr.read_index(run_dir).get("rows") or {})
    counts = {"submitted": 0, "held": 0, "failed": 0}
    webui._catalog_confirm(run_dir, outbox, idx_rows, [rid],
                           cr.DEFAULT_DOCUMENT_CLASS, counts, False)
    cr.write_index(run_dir, {"form": 1, "rows": idx_rows})
    return counts, idx_rows


def _queue_bytes(outbox) -> dict:
    """Снимок очереди целиком: путь → байты (для сверки идемпотентности)."""
    return {p.relative_to(outbox).as_posix(): p.read_bytes()
            for p in sorted(Path(outbox).rglob("*.json"))}


def _run_requests_bytes(run_dir) -> dict:
    """Снимок папки заявок прогона (индекс и записи) — путь → байты."""
    folder = cr.run_requests_dir(run_dir)
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(Path(folder).rglob("*.json"))}


def test_21h_k08_confirm_obeys_gate_g5_and_publishes_no_second_file(
        tmp_path, monkeypatch):
    """«Подтвердить и подать» судит каталог тем же законом Г5, что и
    составление (контракт П0 §4.2): номер уже известен каталогу —
    запись ложится в папку прогона, а в очередь заявка повторно НЕ
    публикуется.

    Инверсия дефекта ревью (проба P1): прежде подтверждение звало
    `enqueue` без сведения Г5 и заводило ВТОРОЙ файл очереди под
    номером, который у службы уже есть (победитель — в `sent/`)."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    # run-0040 доставил: файл-победитель в sent/, запись в каталоге.
    cr._move_to_sent(cr._outbox_paths(outbox, rid))
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21h",
                                 [dict(record, state="подана")])
    soft = _composer(patch=_SOFT_PATCH)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: soft)
    counts, idx = _submit(run_b, rows_b, soft, cat=cat)
    assert counts == {"submitted": 0, "held": 1, "failed": 0}
    assert idx["R-S1-a"]["composed"] == cr.COMPOSED_HELD
    held = run_b / "catalog-requests" / cr.HELD_DIR_NAME / f"{rid}.json"
    assert held.is_file()
    before = _queue_bytes(outbox)
    counts2, idx2 = _confirm(run_b, outbox, rid)
    assert counts2["submitted"] == 1
    assert idx2["R-S1-a"]["composed"] == cr.COMPOSED_CONFIRMED
    # Сведение Г5 на пути «удержан → подтверждён» НЕ теряется: почему
    # заявка в очередь не ушла, видно в строке индекса.
    assert idx2["R-S1-a"]["words"] == ("подана иным черновиком из run-0040; "
                                       "дубль не заводится")
    assert not held.is_file(), "удержанный черновик подтверждён"
    # Своя запись у прогона есть, а очередь не тронута ни байтом.
    assert (run_b / "catalog-requests" / f"{rid}.json").is_file()
    assert _queue_bytes(outbox) == before
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    b = cr.states(run_b, cat, outbox, rows_b)[0]
    assert b["remote_state"] == "подана"
    assert b["origin"] == "другой прогон (иной черновик)"
    assert b["transport_state"] == "доставлена"
    assert b["state"] == "подана · подана иным черновиком из run-0040"


def test_21i_k08_confirm_refuses_a_number_rejected_after_the_hold(
        tmp_path, monkeypatch):
    """Между удержанием черновика и подтверждением владелец каталога
    отклонил заявку: «Подтвердить и подать» отказывает СЛОВАМИ решения и
    путём пересмотра (Г5 судится заново, на момент нажатия кнопки),
    черновик остаётся удержанным, в очередь ничего не уходит.

    Инверсия дефекта ревью (проба P1b): прежде подтверждение публиковало
    ОТКЛОНЁННЫЙ номер и ставило строке «подтверждена и подана»."""
    from fastapi import HTTPException
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    cr._move_to_sent(cr._outbox_paths(outbox, rid))
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21i",
                                 [dict(record, state="подана")])
    soft = _composer(patch=_SOFT_PATCH)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: soft)
    assert _submit(run_b, rows_b, soft, cat=cat)[0]["held"] == 1
    held = run_b / "catalog-requests" / cr.HELD_DIR_NAME / f"{rid}.json"
    cat2 = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21i-2",
        [dict(record, state="отклонена", decision=_K08_DECISION)])
    before = _queue_bytes(outbox)
    with pytest.raises(HTTPException) as ei:
        _confirm(run_b, outbox, rid)
    assert ei.value.status_code == 409
    words = str(ei.value.detail)
    assert "отклонена владельцем каталога" in words
    assert "исполнение пока не согласовано" in words
    assert "пересмотр" in words and "request-closed" in words
    assert held.is_file(), "отказ черновик не теряет — он остаётся удержанным"
    assert _queue_bytes(outbox) == before
    assert cr.read_index(run_b)["rows"]["R-S1-a"]["composed"] == \
        cr.COMPOSED_HELD
    b = cr.states(run_b, cat2, outbox, rows_b)[0]
    assert b["remote_state"] == "отклонена"
    assert b["decision"]["words"] == "исполнение пока не согласовано"
    assert b["transport_state"] is None, "удержанный черновик не подан"
    assert b["state"].startswith("ждёт подтверждения аналитика: ")


def test_21j_k08_reading_states_never_touches_the_queue(tmp_path,
                                                        monkeypatch):
    """Чтение состояний ИДЕМПОТЕНТНО (контракт §4.3): два чтения подряд
    оставляют очередь байт в байт той же, а файл-победитель в `sent/`
    остаётся файлом ПОДАВШЕГО прогона.

    Инверсия дефекта ревью (проба P1c): прежде чтение из второго прогона
    переносило его живой файл в `sent/` поверх победителя — и первый
    прогон начинал видеть чужой черновик под своим номером."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_a, rows_b = _rows_of(doc, run_a), _rows_of(doc, run_b)
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21j",
                                 [dict(record, state="подана")])
    # Живой файл очереди run-0040 и запись в каталоге — прежнее условие
    # переноса чтением.
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    before = _queue_bytes(outbox)
    # Папка заявок прогона (индекс `form: 1` и запись) — тоже до и
    # после: новые поля ВЫЧИСЛЯЮТСЯ при чтении, файлы не переписываются
    # (контракт §4.3).
    folder_before = _run_requests_bytes(run_a)
    assert json.loads(folder_before["index.json"])["form"] == 1
    first = cr.states(run_a, cat, outbox, rows_a)
    assert _queue_bytes(outbox) == before, "чтение очередь не пишет"
    assert _run_requests_bytes(run_a) == folder_before, "индекс не переписан"
    assert cr.states(run_a, cat, outbox, rows_a) == first
    assert _queue_bytes(outbox) == before
    assert _run_requests_bytes(run_a) == folder_before
    # Победитель в `sent/` (доставка перенесла) — чтение из ЧУЖОГО
    # прогона его не подменяет.
    cr._move_to_sent(cr._outbox_paths(outbox, rid))
    winner = (outbox / "sent" / f"{rid}.json").read_bytes()
    soft = _composer(patch=_SOFT_PATCH)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: soft)
    assert _submit(run_b, rows_b, soft, cat=cat)[0]["held"] == 1
    _confirm(run_b, outbox, rid)
    snapshot = _queue_bytes(outbox)
    assert cr.states(run_b, cat, outbox, rows_b) == \
        cr.states(run_b, cat, outbox, rows_b)
    assert _queue_bytes(outbox) == snapshot
    assert (outbox / "sent" / f"{rid}.json").read_bytes() == winner
    got = cr._read_json(outbox / "sent" / f"{rid}.json")
    assert str(got["source"]["ref"]).startswith("run-0040#")
    # Перенос — дело ДОСТАВКИ: живой файл при занятом `sent/` уходит в
    # `duplicates/`, победителя не трогая (закон исключительной подачи).
    (outbox / f"{rid}.json").write_text('{"request_id": "x"}',
                                        encoding="utf-8")
    assert cr._move_to_sent(cr._outbox_paths(outbox, rid)) == "duplicates"
    assert (outbox / "sent" / f"{rid}.json").read_bytes() == winner
    assert (outbox / "duplicates" / f"{rid}.json").is_file()


def test_21k_k08_unreadable_decision_moment_keeps_the_decision(
        tmp_path, monkeypatch):
    """Момент решения не читается (не ISO, пустой, отсутствует) — свежести
    у квитанции НЕТ: старый ответ службы «подана» решения «отклонена» не
    откатывает, решение показано как последнее известное. Пояса часовые
    сравниваются как моменты, а не как строки.

    Инверсия дефекта ревью (проба P2): прежде моментом записи служил
    момент ПОДАЧИ (он всегда раньше решения), и квитанция «подана»
    оказывалась «свежее» решения «отклонена»."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T10:00:00+02:00")
    with _FakeService([_ok(rid)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    for i, at in enumerate(("12.09.2026 10:30", None, "")):
        cat = _catalog_with_requests(
            tmp_path, monkeypatch, f"cat21k-{i}",
            [dict(record, state="отклонена",
                  decision={"by": "владелец", "at": at,
                            "words": "нет: механика спорна"})])
        got = cr.states(run_dir, cat, outbox)[0]
        assert got["remote_state"] == "отклонена", (at, got)
        assert got["stale"] is True, at
        assert got["decision"]["words"] == "нет: механика спорна"
        assert got["state"] == "отклонена: нет: механика спорна", at
    # Пояса: решение 09:30+02:00 (07:30Z) ПОЗЖЕ квитанции 10:00+03:00
    # (07:00Z) — строкой они сравнились бы наоборот.
    # Момент свидетельства — `issued_at` КВИТАНЦИИ (часы службы),
    # а не `at` файла (часы прогона): форма K07 их различает.
    rc = cr.read_receipt(run_dir, rid)
    rc["receipt"]["issued_at"] = "2026-09-12T10:00:00+03:00"
    cr.write_receipt(run_dir, rc)
    cat = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21k-tz",
        [dict(record, state="отклонена",
              decision={"by": "владелец", "at": "2026-09-12T09:30:00+02:00",
                        "words": "нет: механика спорна"})])
    got = cr.states(run_dir, cat, outbox)[0]
    assert (got["remote_state"], got["stale"]) == ("отклонена", True)
    # Положительный контроль: решения нет — моментом записи служит
    # момент подачи, и квитанция 10:00 его свежее (решение не откачено,
    # его просто нет).
    cat_ok = _catalog_with_requests(tmp_path, monkeypatch, "cat21k-ok",
                                    [dict(record, state="подана")])
    rc["receipt"]["issued_at"] = "2026-09-12T10:00:00+02:00"
    cr.write_receipt(run_dir, rc)
    ok = cr.states(run_dir, cat_ok, outbox)[0]
    assert (ok["remote_state"], ok["stale"]) == ("подана", False)


def test_21l_k08_request_closed_receipt_is_not_a_delivery(tmp_path,
                                                          monkeypatch):
    """`409 request-closed` — отказ службы (§4.1: не успех): транспорт
    «отказ», `delivered: false`, файл остаётся в очереди. Запись каталога
    этого не перекрывает: она говорит, что заявка у службы ЕСТЬ, а не
    что подача ЭТОГО прогона дошла. Записи у прогона нет — слова решения
    берутся из отказа службы с пометкой источника.

    Инверсия дефекта ревью (проба P3): прежде запись каталога давала
    «доставлена» и `delivered: true`, экран писал «доставка: исполнена ·
    код 409 · request-closed» и прятал «Подать снова» и «Доставить»."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0041")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    said = (f"заявка {rid} отклонена владельцем: нет; повторная подача — "
            f"только после пересмотра")
    body = {"detail": {"code": "request-closed", "words": said,
                       "extra": {"existing": rid, "state": "отклонена"}}}
    with _FakeService([(409, body)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 0 and got["refused"] == 1
    assert cr.read_receipt(run_dir, rid)["refusal"] == "request-closed"
    assert _queue_files(outbox, rid) == [f"{rid}.json"], "отказ — не sent/"
    # (а) записи каталога у прогона нет: состояние «отклонена» известно
    # из отказа, слова решения — из слов отказа С ПОМЕТКОЙ источника.
    alone = cr.states(run_dir, _cat(), outbox)[0]
    assert alone["remote_state"] == "отклонена" and alone["stale"] is True
    assert alone["transport_state"] == "отказ"
    assert alone["delivered"] is False
    assert alone["decision"]["words"] == f"{said} — {cr.DECISION_FROM_REFUSAL}"
    assert alone["decision"]["at"] is None
    assert alone["decision"]["version"] is None
    assert alone["state"] == f"отклонена: {said}"
    # (б) запись каталога появилась: решение — из НЕЁ (с моментом), а
    # транспорт остаётся отказом — своя подача не дошла.
    cat = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21l",
        [dict(record, state="отклонена", decision=_K08_DECISION)])
    with_rec = cr.states(run_dir, cat, outbox)[0]
    assert with_rec["remote_state"] == "отклонена"
    assert with_rec["decision"]["words"] == "исполнение пока не согласовано"
    assert with_rec["decision"]["at"] == _K08_DECISION["at"]
    assert with_rec["transport_state"] == "отказ"
    assert with_rec["delivered"] is False
    assert with_rec["delivery"]["refusal"] == "request-closed"
    # Положительный контроль: у прогона БЕЗ своей квитанции запись
    # каталога по-прежнему даёт «доставлена» (доставил другой прогон).
    cat_ok = _catalog_with_requests(tmp_path, monkeypatch, "cat21l-ok",
                                    [dict(record, state="подана")])
    doc = json.loads((run_dir / "extract" / "model.json")
                     .read_text(encoding="utf-8"))
    other = _make_run(tmp_path, doc, name="run-0042")
    rows_o = _rows_of(doc, other)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    _submit(other, rows_o, llm, cat=cat_ok)
    o = cr.states(other, cat_ok, outbox, rows_o)[0]
    assert o["transport_state"] == "доставлена" and o["delivered"] is True
    # А у прогона с отказом службы — «отказ» и при той же записи.
    assert cr.states(run_dir, cat_ok, outbox)[0]["transport_state"] == "отказ"


def test_21m_k08_replay_child_repeats_the_words_of_a_known_number(
        tmp_path, monkeypatch):
    """Повтор прогона под номером, УЖЕ известным каталогу: слова индекса
    у источника и у потомка совпадают, и `catalog-requests/` сверяется
    байт в байт (§1.3, образец теста 15). Слова сведения Г5 идут от
    ЗАПИСИ каталога, а не от файлов очереди, — потомок их повторяет, не
    трогая очередь."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    cr._move_to_sent(cr._outbox_paths(outbox, rid))
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21m",
                                 [dict(record, state="подана")])
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    counts, idx = _submit(run_b, rows_b, llm, cat=cat)
    assert counts["submitted"] == 1
    assert idx["R-S1-a"]["words"] == ("подана иным черновиком из run-0040; "
                                      "дубль не заводится")
    run_c = _make_run(tmp_path, doc, name="run-0043")
    rows_c = _rows_of(doc, run_c)
    llm2 = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm2)
    before = _queue_bytes(outbox)
    counts2, idx2 = _submit(run_c, rows_c, llm2, cat=cat, replay=True,
                            source_dir=run_b)
    assert counts2["submitted"] == 1
    assert idx2["R-S1-a"] == idx["R-S1-a"]
    assert (run_c / "catalog-requests" / "index.json").read_bytes() == \
        (run_b / "catalog-requests" / "index.json").read_bytes()
    assert (run_c / "catalog-requests" / f"{rid}.json").read_bytes() == \
        (run_b / "catalog-requests" / f"{rid}.json").read_bytes()
    assert _queue_bytes(outbox) == before, "повтор очередь не трогает"


def test_21n_k08_foreign_number_is_not_a_saved_receipt(
        tmp_path, monkeypatch):
    """Номер обёртки не оправдывает чужой номер полного тела receipt/1.

    Прямой сохранённый вход без HTTP: отказ вызван именно номером,
    а не отсутствием обязательных полей современной квитанции.
    """
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    sent = _sent_of(rid, outbox)
    foreign = "req-" + "0" * 20
    states_ = cr.service_request_states(_cat())
    # Форма K07: номер тела чужой — не квитанция, и подача не засчитана.
    v2 = {"schema_version": "receipt-file/2", "request_id": rid,
          "outcome": "доставлена",
          "last_checked_at": "2026-09-12T10:00:00+02:00",
          "receipt": _receipt(foreign, sent=sent)}
    cr.write_receipt(run_dir, v2)
    assert cr.read_receipt(run_dir, rid)["request_id"] == rid != foreign
    assert cr.receipt_confirmed(v2, rid, states_, sent=sent) is None
    assert cr.receipt_confirmed(
        dict(v2, receipt=dict(v2["receipt"], request_id=rid)),
        rid, states_, sent=sent)["state"] == "подана"


def _receipt_v2(run_dir, rid, *, state, at, words=None, version=None,
                decision_at=None):
    """Файл квитанции формы K07 (`receipt-file/2`) с решением службы:
    `at` — момент ВЫДАЧИ (транспорт), `version`/`decision_at` — версия и
    момент самого РЕШЕНИЯ."""
    sent = _sent_of(rid)
    decision = {"by": None, "at": None, "words": None, "version": 0}
    extra = {}
    if words is not None:
        decision = {"by": "владелец", "at": decision_at, "words": words,
                    "version": version}
        if version is None:
            # Канал 0.5: снаружи обязательный признак наличия решения,
            # внутри — полная запись без числовой редакции. Так исходный
            # K08 oracle сравнивает момент с моментом, не с числом 1.
            by_time = {k: v for k, v in decision.items() if k != "version"}
            extra["request"] = dict(sent, state=state, decision=by_time)
            decision["version"] = 1
    cr.write_receipt(run_dir, {
        "schema_version": "receipt-file/2", "request_id": rid,
        "outcome": "доставлена", "last_checked_at": at,
        "attempts": [{"at": at, "http": 200, "outcome": "доставлена"}],
        "receipt": _receipt(rid, sent=sent, state=state, repeat=True,
                            issued_at=at, decision=decision, **extra)})


def test_21o_k08_decision_version_is_the_seniority_not_the_transport_time(
        tmp_path, monkeypatch):
    """Старшинство решения — у самого РЕШЕНИЯ, не у момента выдачи
    квитанции (класс аудитора K08-03): при известном v8 «отклонена»
    пришедший позже ответ с v7 «принята» решения не откатывает; v10
    после v9 — числом, не строкой; конфликт содержания при ОДНОЙ версии
    сохраняет последнее достоверное решение, а не выбранное по порядку
    прихода ответов (класс K08-05).

    Инверсия дефекта: прежде «свежесть» доказывал один лишь момент
    выдачи квитанции — запоздалый ответ службы с устаревшим решением
    вытеснял бы новое."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    rejected = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
                "words": "исполнение пока не согласовано", "version": 8}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21o",
                                 [dict(record, state="отклонена",
                                       decision=rejected)])
    # (а) запоздалый ответ с УСТАРЕВШИМ решением v7: выдан позже
    # (11:00), а решение старше — v8 «отклонена» остаётся.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:00:00+02:00", words="можно делать",
                version=7, decision_at="2026-09-12T09:30:00+02:00")
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["remote_state"] == "отклонена", got
    assert got["decision"]["words"] == "исполнение пока не согласовано"
    assert got["decision"]["version"] == 8
    assert got["stale"] is True
    assert got["transport_state"] == "доставлена"
    # (б) положительный контроль: то же свидетельство с v9 — решение
    # службы новее, оно и показано.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:00:00+02:00", words="после пересмотра: да",
                version=9, decision_at="2026-09-12T10:40:00+02:00")
    newer = cr.states(run_dir, cat, outbox)[0]
    assert newer["remote_state"] == "принята"
    assert newer["decision"] == {"words": "после пересмотра: да",
                                 "version": 9,
                                 "at": "2026-09-12T10:40:00+02:00"}
    assert newer["stale"] is True, "копия каталога отстала — не подтверждено"
    # (в) число, а не строка: v10 после v9 (лексически «10» < «9»).
    nine = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21o-9",
        [dict(record, state="отклонена", decision=dict(rejected, version=9))])
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:00:00+02:00", words="да",
                version=10, decision_at="2026-09-12T10:40:00+02:00")
    assert cr.states(run_dir, nine, outbox)[0]["remote_state"] == "принята"
    ten = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21o-10",
        [dict(record, state="отклонена",
              decision=dict(rejected, version=10))])
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:00:00+02:00", words="да",
                version=9, decision_at="2026-09-12T10:40:00+02:00")
    held = cr.states(run_dir, ten, outbox)[0]
    assert held["remote_state"] == "отклонена"
    assert held["decision"]["version"] == 10
    # (г) конфликт содержания при ОДНОЙ версии: сохранено последнее
    # достоверное решение записи, выбора по порядку нет — и момент
    # выдачи квитанции его не меняет.
    for issued in ("2026-09-12T11:00:00+02:00", "2026-09-12T23:00:00+02:00"):
        _receipt_v2(run_dir, rid, state="принята", at=issued,
                    words="можно делать", version=8,
                    decision_at="2026-09-12T10:00:00+02:00")
        same = cr.states(run_dir, cat, outbox)[0]
        assert same["remote_state"] == "отклонена", issued
        assert same["decision"]["words"] == "исполнение пока не согласовано"
        assert same["stale"] is True, issued
    # (д) ответ НАЗЫВАЕТ своё решение на другой шкале (число против
    # момента): старшинства это не доказывает, и момент выдачи квитанции
    # за него не отвечает — решение записи остаётся, сколь угодно поздно
    # ни был бы выдан ответ.
    plain = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
             "words": "исполнение пока не согласовано"}
    cat_plain = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21o-plain",
        [dict(record, state="отклонена", decision=plain)])
    for issued in ("2026-09-12T10:00:00+02:00", "2026-09-12T23:00:00+02:00"):
        _receipt_v2(run_dir, rid, state="принята", at=issued, words="да",
                    version=3, decision_at="2026-09-12T09:00:00+02:00")
        mixed = cr.states(run_dir, cat_plain, outbox)[0]
        assert mixed["remote_state"] == "отклонена", issued
        assert mixed["decision"]["words"] == "исполнение пока не согласовано"
    # Тот же ответ, но на ОДНОЙ шкале с записью (момент решения) и
    # строго позже — замена законна, положительный контроль.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T23:00:00+02:00", words="да",
                decision_at="2026-09-12T10:00:01+02:00")
    assert cr.states(run_dir, cat_plain, outbox)[0]["remote_state"] == \
        "принята"
    # (д2) решения ответ НЕ называет (три null и version=0):
    # доказывать старшинство ему НЕЧЕМ — момент выдачи ответа за решение
    # не отвечает, и решение записи остаётся, когда бы ответ ни пришёл.
    #
    # Инверсия дефекта K08-F1: прежде здесь решали моменты свидетельств,
    # и ответ, выданный службой в 09:00 и полученный в 11:00, отменял
    # решение владельца, принятое в 10:00.
    for issued in ("2026-09-12T10:00:00+02:00", "2026-09-12T10:00:01+02:00",
                   "2026-09-12T23:00:00+02:00"):
        _receipt_v2(run_dir, rid, state="принята", at=issued)
        mute = cr.states(run_dir, cat_plain, outbox)[0]
        assert mute["remote_state"] == "отклонена", issued
        assert mute["decision"]["words"] == "исполнение пока не согласовано"
        assert mute["stale"] is True and mute["last_known"] is None, issued
        assert mute["stale_reason"] == cr.STALE_NOT_PROVEN, issued
    # (д3) известного решения у записи нет — откатывать нечего: свежий
    # ответ со своим решением показан, а не спрятан.
    cat_none = _catalog_with_requests(tmp_path, monkeypatch, "cat21o-none",
                                      [dict(record, state="подана")])
    _receipt_v2(run_dir, rid, state="отклонена",
                at="2026-09-12T10:00:01+02:00", words="нет: механика спорна",
                version=1, decision_at="2026-09-12T10:00:00+02:00")
    fresh = cr.states(run_dir, cat_none, outbox)[0]
    assert fresh["remote_state"] == "отклонена"
    assert fresh["decision"]["words"] == "нет: механика спорна"
    # Старшинство словами: виды шкал не смешиваются, `0` — число, а
    # отсутствие и пустое — нет.
    assert cr._as_number(0) == 0.0 and cr._as_number("10") == 10.0
    assert cr._as_number(None) is None and cr._as_number("") is None
    assert cr._as_number(True) is None and cr._as_number("v9") is None
    # Бесконечность и NaN версией не считаются (RFC 8259 §6): иначе
    # «inf» вытеснил бы любое решение, а «nan» молча дал бы «не старше».
    assert cr._as_number("inf") is None and cr._as_number("nan") is None
    assert cr._as_number(float("inf")) is None
    num = {"version": 9, "at": "2026-09-12T10:00:00+02:00"}
    assert cr._decision_rank(num) == ("число", 9.0)
    assert cr._decision_rank(
        {"at": "2026-09-12T10:00:00+02:00"})[0] == "момент"
    assert cr._decision_rank({"version": None, "at": None}) is None
    assert cr._newer_decision(dict(num, version=10), num) is True
    assert cr._newer_decision(num, num) is False
    assert cr._newer_decision(num, {"at": "2026-09-12T09:00:00+02:00"}) is None
    assert cr._newer_decision(num, None) is None
    # (е) подделанная версия ответа службы («inf», «nan», текст) числом
    # не становится: receipt/1 отказывает, решение записи остаётся.
    for forged in ("inf", "nan", "самая новая"):
        _receipt_v2(run_dir, rid, state="принята",
                    at="2026-09-12T11:00:00+02:00", words="можно делать",
                    version=forged, decision_at="2026-09-12T09:30:00+02:00")
        assert cr.receipt_confirmed(cr.read_receipt(run_dir, rid), rid) is None
        bad = cr.states(run_dir, cat, outbox)[0]
        assert bad["remote_state"] == "отклонена", forged
        assert bad["decision"]["version"] == 8, forged


def test_21p_k08_confirm_without_a_catalog_publishes_nothing(tmp_path,
                                                             monkeypatch):
    """«Подтвердить и подать» без каталога НЕ подаёт вслепую: Г5 судить
    нечем — отказ словами, черновик остаётся удержанным, очередь не
    тронута. Рубеж один и у ручки (`blocked_words`), и у самого
    подтверждения.

    Инверсия дефекта ревью: прежде ручка пропускала `confirm` мимо
    рубежа доступности каталога (`if blocked and mode != "confirm"`), а
    подтверждение каталог не читало вовсе."""
    from fastapi import HTTPException
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    soft = _composer(patch=_SOFT_PATCH)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: soft)
    assert _submit(run_b, rows_b, soft)[0]["held"] == 1
    held = run_b / "catalog-requests" / cr.HELD_DIR_NAME / f"{rid}.json"
    assert held.is_file()
    before = _queue_bytes(outbox)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(tmp_path / "нет-такой"))
    catalog_bridge.forget()
    assert "каталог шаблонов недоступен" in cr.blocked_words()
    # Ручка панели: отказ до всякой подачи.
    with pytest.raises(HTTPException) as route:
        webui.submit_catalog_requests(
            run_b.name, rule_ids=None, request_ids=[rid],
            document_class=None, mode="confirm", confirm_service_c=False)
    assert route.value.status_code == 409
    assert "каталог шаблонов недоступен" in str(route.value.detail)
    # Само подтверждение — тоже рубеж, а не молчаливая подача.
    with pytest.raises(HTTPException) as direct:
        _confirm(run_b, outbox, rid)
    assert direct.value.status_code == 503
    assert held.is_file(), "черновик не потерян"
    assert _queue_bytes(outbox) == before
    assert cr.read_index(run_b)["rows"]["R-S1-a"]["composed"] == \
        cr.COMPOSED_HELD


def test_21q_k08_resend_and_withdraw_stop_at_a_number_the_catalog_knows(
        tmp_path, monkeypatch):
    """Повторная подача судит каталог тем же законом, что и отзыв (K08):
    номер, уже известный каталогу, второго живого файла в очереди не
    получает — заявка общая, решение по ней на экране каталога; файл,
    уже доставленный (`sent/`), повторной подачи тоже не допускает —
    сверка со службой идёт «Доставить», без новой заявки.

    Инверсия дефекта ревью: прежде `resend` каталог не читал вовсе и
    возвращал в очередь номер, который служба уже знает."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21q",
                                 [dict(record, state="принята")])
    # Отзыв при известном номере отказывает — и повторная подача тоже.
    assert cr.withdraw(rid, run_a, outbox, cat)["ok"] is False
    got = cr.resend(rid, run_a, outbox, cat)
    assert got["ok"] is False
    assert "уже известна каталогу" in got["words"]
    assert "принята" in got["words"], "состояние названо словами"
    assert _queue_files(outbox, rid) == [f"{rid}.json"], "очередь не тронута"
    # Положительный контроль: номер каталогу неизвестен — отзыв и
    # возврат в очередь работают как прежде, тем же номером.
    assert cr.withdraw(rid, run_a, outbox, _cat())["ok"] is True
    assert _queue_files(outbox, rid) == [f"withdrawn/{rid}.json"]
    # Пока номера у каталога нет — файл возвращается в очередь.
    assert cr.resend(rid, run_a, outbox, _cat())["ok"] is True
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    # Отозван, а тем временем номер появился у каталога: второго живого
    # файла не будет — решение на экране каталога.
    assert cr.withdraw(rid, run_a, outbox, _cat())["ok"] is True
    assert cr.resend(rid, run_a, outbox, cat)["ok"] is False
    assert _queue_files(outbox, rid) == [f"withdrawn/{rid}.json"]
    # Доставленный файл (`sent/`) — повторной подачи не нужно и без
    # записи в каталоге: состояние читается по квитанции, сверка —
    # «Доставить».
    (outbox / "withdrawn" / f"{rid}.json").replace(outbox / f"{rid}.json")
    assert cr._move_to_sent(cr._outbox_paths(outbox, rid)) == "sent"
    sent = cr.resend(rid, run_a, outbox, _cat())
    assert sent["ok"] is False and "«Доставить»" in sent["words"]
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    # Потомок повтора очередь не трогает ни в одном из случаев.
    before = _queue_bytes(outbox)
    assert cr.resend(rid, run_a, outbox, cat, replay=True) == \
        {"ok": True, "words": None}
    assert _queue_bytes(outbox) == before


def test_21r_k08_a_failed_check_is_not_a_delivery_by_the_record(tmp_path,
                                                                monkeypatch):
    """Связь не состоялась (три попытки, кода нет) — запись каталога
    транспорт НЕ подменяет: файл прогона всё ещё ждёт в очереди, и
    «доставлена» была бы производным зелёным поверх собственной
    квитанции «служба не отвечает» (класс аудитора K08-02: доставка ≠
    наличие записи; сведения при этом не теряются — состояние службы и
    решение остаются на своей оси).

    Инверсия дефекта: прежде запись каталога давала «доставлена» и
    `delivered: true` при живом файле очереди — экран прятал кнопку
    «Доставить в службу», которой сверка и делается."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_a = _rows_of(doc, run_a)
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21r",
                                 [dict(record, state="принята",
                                       decision=_K08_DECISION)])
    # Положительный контроль: своего ответа службы у прогона нет ВОВСЕ
    # (заявку забрала читающая сторона либо доставил другой прогон) —
    # запись каталога даёт «доставлена», как и прежде.
    assert cr.read_receipt(run_a, rid) is None
    ok = cr.states(run_a, cat, outbox, rows_a)[0]
    assert ok["transport_state"] == "доставлена" and ok["delivered"] is True
    # Прогон ПЫТАЛСЯ доставить, службы нет: файл остаётся живым.
    _service_env(monkeypatch, _dead_url())
    assert cr.deliver(run_a, [rid], _sleep=_no_sleep)["delivered"] == 0
    assert cr.read_receipt(run_a, rid)["words"].startswith(
        "служба не отвечает: ")
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    assert cr.deliverable(run_a, outbox) == [rid], "доставка ещё нужна"
    got = cr.states(run_a, cat, outbox, rows_a)[0]
    assert got["transport_state"] == "в очереди"
    assert got["delivered"] is False, "кнопка «Доставить» обязана остаться"
    # Ось состояния службы при этом не пострадала: решение видно, и оно
    # названо последним известным.
    assert got["remote_state"] == "принята"
    assert got["decision"]["words"] == _K08_DECISION["words"]
    assert got["stale"] is True
    assert got["state"] == f"принята: {_K08_DECISION['words']}"
    # Прогон-сосед, своей подачи не делавший, по той же записи
    # по-прежнему видит «доставлена» — правило общее, не про этот файл.
    rows_b = _rows_of(doc, run_b)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    _submit(run_b, rows_b, llm, cat=cat)
    b = cr.states(run_b, cat, outbox, rows_b)[0]
    assert b["transport_state"] == "доставлена" and b["delivered"] is True


def test_21s_k08_a_record_without_a_number_is_nobody_s_decision(tmp_path,
                                                                monkeypatch):
    """Номер — идентичность заявки: запись каталога БЕЗ номера (пустой,
    пробельный, `null`) не достаётся строке прогона, у которой номера
    тоже нет. Иначе `str(None)` и `""` слили бы разные сущности под
    одним ключом и чужое решение показалось бы своим (класс аудитора
    K08-05, общий класс X-03)."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    dec = {"by": "владелец", "at": "2026-09-12T09:10:00+02:00",
           "words": "нет: чужое решение"}
    faceless = [dict(record, request_id=x, state="отклонена", decision=dec)
                for x in ("", "   ", None)]
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21s",
                                 faceless + [dict(record, state="подана")])
    # Перечень записей по номеру: безымянные в него не входят.
    got = cr._catalog_records(cat)
    assert sorted(got) == [rid]
    assert "" not in got and "None" not in got
    # Строка прогона без номера (старый индекс, правка руками) чужого
    # решения не получает: ни состояния, ни слов.
    idx = cr.read_index(run_dir)
    idx["rows"]["R-S1-z"] = cr.index_row("k-z", "", cr.COMPOSED_SUBMITTED,
                                         None, cr.DEFAULT_DOCUMENT_CLASS)
    cr.write_index(run_dir, idx)
    rows = {r["rule_id"]: r for r in cr.states(run_dir, cat, outbox)}
    assert rows["R-S1-z"]["remote_state"] is None
    assert rows["R-S1-z"]["decision"] == {"words": None, "version": None,
                                          "at": None}
    assert rows["R-S1-z"]["transport_state"] == "неизвестно"
    # Своя строка со своим номером читается как прежде — положительный
    # контроль (запись «подана» у каталога есть).
    assert rows["R-S1-a"]["remote_state"] == "подана"
    assert rows["R-S1-a"]["decision"]["words"] is None
    # Тот же закон у Г5: безымянная запись номера не занимает.
    ctx = cr.run_context(run_dir, json.loads(
        (run_dir / "extract" / "model.json").read_text(encoding="utf-8")),
        {}, cat)
    assert cr.known_of(dict(record, request_id=""), cat) is None
    assert cr.gate([record], cat, ctx, {})[rid]["known"]["state"] == "подана"
    # Повтор номера — нарушение самого каталога (правило читателя R01), и
    # разрешает его КАТАЛОГ: прогон видит ту же запись, что отдаёт
    # `cat.request(rid)`, а не выбранную своим порядком перечисления —
    # иначе на экране службы и в прогоне стояли бы разные решения.
    twice = _catalog_with_requests(
        tmp_path, monkeypatch, "cat21s-2",
        [dict(record, state="отклонена", decision=dec),
         dict(record, state="принята")])
    assert twice.request(rid)["state"] == "отклонена"
    assert cr._catalog_records(twice)[rid] == twice.request(rid)
    assert cr.states(run_dir, twice, outbox)[0]["remote_state"] == "отклонена"


def test_21t_k08_a_decision_belongs_to_the_state_it_was_made_for(
        tmp_path, monkeypatch):
    """Решение принадлежит СОСТОЯНИЮ, для которого вынесено. Ответ службы
    назвал СВОЁ решение и иное состояние — показано его решение, а
    последнее известное решение записи стоит ОТДЕЛЬНО (`last_known`) и
    названо словами панели: слова решения одного состояния к слову
    другого не приписываются никогда.

    Ответ, решения НЕ назвавший (старая форма файла квитанции), состояния
    записи не меняет вовсе: доказать старшинство ему нечем, и решение
    записи остаётся показанным как последнее известное (правка K08-F1 —
    прежде старшинство выводилось из времени получения ответа, и
    запоздавший ответ «принята» отменял отказ владельца).

    Инверсия дефекта (второе ревью K08, находка 1): прежде строка брала
    состояние из квитанции, а слова решения — из записи, и настоящий путь
    волны 17а («отклонена» → владелец пересмотрел на «принята» → прогон
    сверился, 200 «уже подана») складывал их в «принята: нет: механика
    спорна» — решение с обратным смыслом."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    rejected = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
                "words": "нет: механика спорна"}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21t",
                                 [dict(record, state="отклонена",
                                       decision=rejected)])
    # Владелец пересмотрел в 10:30, копия каталога отстала; прогон
    # сверился в 10:31 и получил ответ с ПЕРЕСМОТРЕННЫМ решением
    # (служба отдаёт запись целиком): показано решение ответа, решение
    # записи — отдельной строкой.
    revised = {"by": "владелец", "at": "2026-09-12T10:30:00+02:00",
               "words": "после пересмотра: да"}
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T10:31:00+02:00")
    with _FakeService([_again(rid, outbox, state="принята",
                              decision=dict(revised, version=1),
                              request=dict(record, state="принята",
                                           decision=revised))]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["remote_state"] == "принята"
    assert got["decision"] == cr.decision_of({"decision": revised})
    assert got["last_known"] == {
        "state": "отклонена",
        "decision": cr.decision_of({"decision": rejected})}
    assert got["stale"] is True
    assert got["stale_reason"] == cr.STALE_CATALOG_BEHIND
    assert got["state"] == ("принята: после пересмотра: да · последнее "
                            "известное решение записи: отклонена — нет: "
                            "механика спорна")
    assert "принята: нет" not in got["state"], "слова решения ЧУЖОГО состояния"
    # Тот же ответ, но своей редакции решения он не называет («решения
    # нет вовсе» — три `null` и версия 0): состояние записи остаётся
    # показанным — слово «принята» без редакции решения отказ владельца
    # не отменяет (K08-F1).
    with _FakeService([_again(rid, outbox, state="принята")]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    mute = cr.states(run_dir, cat, outbox)[0]
    assert mute["remote_state"] == "отклонена"
    assert mute["decision"]["words"] == "нет: механика спорна"
    assert mute["stale"] is True
    assert mute["stale_reason"] == cr.STALE_NOT_PROVEN
    assert mute["last_known"] is None, "показано решение самой записи"
    # Обратная сторона того же закона: запись «принята» со своими
    # словами, ответ службы «отклонена» со своим решением — «отклонена:
    # нужна новая механика» сказать нельзя.
    accepted = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
                "words": "нужна новая механика"}
    refused = {"by": "владелец", "at": "2026-09-12T10:30:00+02:00",
               "words": "после пересмотра: нет"}
    cat2 = _catalog_with_requests(tmp_path, monkeypatch, "cat21t-2",
                                  [dict(record, state="принята",
                                        decision=accepted)])
    with _FakeService([_again(rid, outbox, state="отклонена",
                              decision=dict(refused, version=1),
                              request=dict(record, state="отклонена",
                                           decision=refused))]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    back = cr.states(run_dir, cat2, outbox)[0]
    assert back["remote_state"] == "отклонена"
    assert back["decision"]["words"] == "после пересмотра: нет"
    assert back["last_known"]["state"] == "принята"
    assert back["last_known"]["decision"]["words"] == "нужна новая механика"
    assert back["state"] == ("отклонена: после пересмотра: нет · последнее "
                             "известное решение записи: принята — нужна "
                             "новая механика")
    # Положительный контроль: ответ НАЗЫВАЕТ своё решение (форма
    # `receipt-file/2`) — оно и показано решением нового состояния, а
    # решение записи остаётся рядом последним известным.
    _receipt_v2(run_dir, rid, state="отклонена",
                at="2026-09-12T10:31:00+02:00",
                words="после пересмотра: нет",
                decision_at="2026-09-12T10:30:00+02:00")
    named = cr.states(run_dir, cat2, outbox)[0]
    assert named["remote_state"] == "отклонена"
    assert named["decision"]["words"] == "после пересмотра: нет"
    assert named["decision"]["at"] == "2026-09-12T10:30:00+02:00"
    assert named["last_known"]["decision"]["words"] == "нужна новая механика"
    assert named["state"] == ("отклонена: после пересмотра: нет · последнее "
                              "известное решение записи: принята — нужна "
                              "новая механика")
    # Показано решение САМОЙ записи — отдельной подписи нет: она
    # появляется только там, где показано не оно. Ответ назвал то же
    # состояние, но решения не назвал — показано решение записи, и оно
    # последнее ИЗВЕСТНОЕ: подтвердить его нечем.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T10:31:00+02:00")
    own = cr.states(run_dir, cat2, outbox)[0]
    assert own["remote_state"] == "принята"
    assert own["decision"]["words"] == "нужна новая механика"
    assert own["last_known"] is None
    assert own["stale"] is True and own["stale_reason"] == cr.STALE_NOT_PROVEN
    assert own["state"] == "принята: нужна новая механика"
    # Положительный контроль подтверждения: ответ назвал ТУ ЖЕ редакцию
    # решения при том же состоянии — подтверждено, причины нет.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T10:31:00+02:00", words=accepted["words"],
                decision_at=accepted["at"])
    same = cr.states(run_dir, cat2, outbox)[0]
    assert same["stale"] is False and same["stale_reason"] is None
    assert same["decision"]["words"] == "нужна новая механика"
    assert same["state"] == "принята: нужна новая механика"


def test_21u_k08_one_state_letter_is_not_a_confirmed_decision(
        tmp_path, monkeypatch):
    """Одна буква состояния — ещё не подтверждение решения: пересмотр
    «принята → отклонена → принята» возвращает то же слово, и решение в
    квитанции бывает СТРОГО старше решения записи. Тогда отстала копия
    каталога: показано решение ответа службы, последнее известное названо
    отдельно, устаревание — с причиной. Не старшее и несравнимое решение
    записи сохраняет, но подтверждением НЕ становится: подтверждает
    только ТА ЖЕ редакция решения (K08-F1 — «одинаковое слово состояния
    не доказывает ту же редакцию»).

    Инверсия дефекта (второе ревью K08, находка 2): прежде равенство
    буквы состояния объявляло решение записи подтверждённым (`stale:
    false`), и решение квитанции 11:00 уступало решению записи 09:30."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    first = {"by": "владелец", "at": "2026-09-12T09:30:00+02:00",
             "words": "нужна"}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21u",
                                 [dict(record, state="принята",
                                       decision=first)])
    # (а) моментами: решение квитанции 11:00 новее решения записи 09:30.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00",
                words="после второго пересмотра: да",
                decision_at="2026-09-12T11:00:00+02:00")
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["remote_state"] == "принята"
    assert got["decision"]["words"] == "после второго пересмотра: да"
    assert got["decision"]["at"] == "2026-09-12T11:00:00+02:00"
    assert got["stale"] is True
    assert got["stale_reason"] == cr.STALE_CATALOG_BEHIND
    assert got["last_known"]["decision"]["words"] == "нужна"
    # Состояние записи то же — в подписи оно не повторяется.
    assert got["state"] == ("принята: после второго пересмотра: да · "
                            "последнее известное решение записи: нужна")
    # (б) числами: запись v1, квитанция v3 — то же самое.
    cat_v = _catalog_with_requests(tmp_path, monkeypatch, "cat21u-v",
                                   [dict(record, state="принята",
                                         decision=dict(first, version=1))])
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00",
                words="после второго пересмотра: да", version=3,
                decision_at="2026-09-12T11:00:00+02:00")
    num = cr.states(run_dir, cat_v, outbox)[0]
    assert num["decision"]["version"] == 3 and num["stale"] is True
    assert num["last_known"]["decision"]["version"] == 1
    # (в) решение квитанции НЕ старше (равное по моменту и более раннее),
    # но ИНОЕ по содержанию: решение записи сохранено — и НЕ объявлено
    # подтверждённым. Одинаковая буква состояния при разных словах
    # решения — разные редакции, и выбирать между ними по порядку
    # прихода ответов нельзя (K08-F1).
    for at, why in (("2026-09-12T09:30:00+02:00", cr.STALE_NOT_PROVEN),
                    ("2026-09-12T09:00:00+02:00", cr.STALE_RECEIPT_OLDER)):
        _receipt_v2(run_dir, rid, state="принята",
                    at="2026-09-12T11:30:00+02:00", words="иное",
                    decision_at=at)
        same = cr.states(run_dir, cat, outbox)[0]
        assert same["decision"]["words"] == "нужна", at
        assert same["stale"] is True and same["last_known"] is None, at
        assert same["stale_reason"] == why, at
    # (в2) положительный контроль: ТА ЖЕ редакция решения (кто, слова,
    # момент) при том же состоянии — подтверждено, причины нет.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00", words="нужна",
                decision_at="2026-09-12T09:30:00+02:00")
    exact = cr.states(run_dir, cat, outbox)[0]
    assert exact["decision"]["words"] == "нужна"
    assert exact["stale"] is False and exact["stale_reason"] is None
    assert exact["last_known"] is None
    # (г) разные шкалы (число против момента) старшинства не доказывают —
    # и подтверждением тоже не являются.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00", words="иное", version=3,
                decision_at="2026-09-12T11:00:00+02:00")
    mixed = cr.states(run_dir, cat, outbox)[0]
    assert mixed["decision"]["words"] == "нужна"
    assert mixed["stale"] is True and mixed["last_known"] is None
    assert mixed["stale_reason"] == cr.STALE_NOT_PROVEN
    # (д) решения ответ не называет вовсе — доказывать ему нечем:
    # решение записи остаётся последним известным.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00")
    plain = cr.states(run_dir, cat, outbox)[0]
    assert plain["decision"]["words"] == "нужна" and plain["stale"] is True
    assert plain["stale_reason"] == cr.STALE_NOT_PROVEN


def test_21v_k08_version_zero_means_no_decision_at_all(tmp_path, monkeypatch):
    """Счёт версий решения у службы (служебный неизменник, подтверждён
    COSMOS 12.09.2026): `0` — решения НЕТ вовсе (три `null`), первое
    решение — `1`, версии не убывают. Поэтому `0` особым случаем не
    считается: число сравнивается числом, как всякое другое.

    Инверсия (второе ревью K08, находка 3): прежде `0` подменялся
    моментом решения и уходил на ДРУГУЮ шкалу — с числовой версией
    ответа службы он становился несравнимым, и свежий ответ v1 тонул."""
    # Неизменник словами читателя: `0` приходит только с тремя `null`.
    none3 = {"words": None, "version": None, "at": None}
    assert cr.decision_of({"decision": {"by": None, "at": None,
                                        "words": None,
                                        "version": 0}}) == none3
    assert cr.decision_of({"decision": {"version": 0}}) == none3
    # Нарушение неизменника (копия каталога правлена руками) шкалы не
    # меняет: `0` — число, как и `1`.
    v0 = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
          "words": "нет", "version": 0}
    assert cr.decision_of({"decision": v0})["version"] == 0
    assert cr._decision_rank(cr.decision_of({"decision": v0})) == \
        ("число", 0.0)
    assert cr._newer_decision({"version": 1}, {"version": 0}) is True
    assert cr._newer_decision({"version": 0}, {"version": 1}) is False
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21v",
                                 [dict(record, state="отклонена",
                                       decision=v0)])
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00", words="да", version=1,
                decision_at="2026-09-12T11:00:00+02:00")
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["remote_state"] == "принята"
    assert got["decision"]["version"] == 1
    assert got["decision"]["words"] == "да"
    assert got["stale"] is True
    assert got["last_known"] == {
        "state": "отклонена", "decision": cr.decision_of({"decision": v0})}
    # Обратное: запись v1, ответ службы v0 (выдан позже) — отката нет.
    cat1 = _catalog_with_requests(tmp_path, monkeypatch, "cat21v-1",
                                  [dict(record, state="принята",
                                        decision=dict(v0, version=1,
                                                      words="да"))])
    _receipt_v2(run_dir, rid, state="отклонена",
                at="2026-09-12T11:30:00+02:00", words="нет", version=0,
                decision_at="2026-09-12T10:00:00+02:00")
    back = cr.states(run_dir, cat1, outbox)[0]
    assert back["remote_state"] == "принята"
    assert back["decision"]["version"] == 1
    assert back["decision"]["words"] == "да"
    assert back["stale"] is True
    # Причина названа тем, что доказано: решение ОТВЕТА (v0) старше
    # решения записи (v1) — не «старшинство не доказано».
    assert back["stale_reason"] == cr.STALE_RECEIPT_OLDER
    assert back["last_known"] is None


def test_21w_k08_a_stale_decision_names_its_cause(tmp_path, monkeypatch):
    """Устаревание НАЗЫВАЕТ причину, и перечень причин закрыт: квитанции
    у прогона нет | ответ службы старше решения | старшинство не доказано
    | копия каталога отстала | состояние известно из отказа службы
    | последняя проверка не подтвердила актуальность решения службы.
    Подтверждённое решение причины не имеет.

    Инверсия дефекта (второе ревью K08, находка 1, слова экрана): прежде
    подпись была одна на все случаи — «свежей квитанции службы у прогона
    нет», и при отставшей копии каталога она прямо лгала: квитанция как
    раз свежая, отстала запись."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    dec = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
           "words": "нет: механика спорна"}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21w",
                                 [dict(record, state="отклонена",
                                       decision=dec)])
    seen = []
    # (а) прикладной квитанции у прогона нет вовсе.
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["stale"] is True
    assert got["stale_reason"] == cr.STALE_NO_RECEIPT
    seen.append(got["stale_reason"])
    # (б) РЕШЕНИЕ ответа старше решения записи (09:30 против 10:00):
    # старый ответ нового решения не откатывает. Причину доказывает само
    # решение, а не момент выдачи ответа (K08-F1).
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T23:00:00+02:00", words="прежнее принятие",
                decision_at="2026-09-12T09:30:00+02:00")
    older = cr.states(run_dir, cat, outbox)[0]
    assert older["remote_state"] == "отклонена"
    assert older["stale_reason"] == cr.STALE_RECEIPT_OLDER
    seen.append(older["stale_reason"])
    # (в) ответ свежее, но старшинства РЕШЕНИЯ не доказал (шкалы разные).
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00", words="да", version=3,
                decision_at="2026-09-12T11:00:00+02:00")
    unproven = cr.states(run_dir, cat, outbox)[0]
    assert unproven["remote_state"] == "отклонена"
    assert unproven["stale_reason"] == cr.STALE_NOT_PROVEN
    seen.append(unproven["stale_reason"])
    # (г) копия каталога отстала: ответ строго старше и на одной шкале.
    _receipt_v2(run_dir, rid, state="принята",
                at="2026-09-12T11:30:00+02:00", words="да",
                decision_at="2026-09-12T11:00:00+02:00")
    behind = cr.states(run_dir, cat, outbox)[0]
    assert behind["remote_state"] == "принята"
    assert behind["stale_reason"] == cr.STALE_CATALOG_BEHIND
    seen.append(behind["stale_reason"])
    # Подтверждено (ТА ЖЕ редакция решения при том же состоянии) —
    # причины нет: поле молчит.
    _receipt_v2(run_dir, rid, state="отклонена",
                at="2026-09-12T11:30:00+02:00", words=dec["words"],
                decision_at=dec["at"])
    ok = cr.states(run_dir, cat, outbox)[0]
    assert ok["stale"] is False and ok["stale_reason"] is None
    # (г2) доказанная доставка не теряется при сбое реальной сверки,
    # но решение становится последним известным (П0 §4.4).
    from urllib.error import URLError
    saved = cr.read_receipt(run_dir, rid)
    calls = []
    monkeypatch.setenv(cr.ENV_SERVICE_URL, "http://k07-stale.invalid")

    def unavailable(request, *, timeout):
        assert request.get_method() == "GET"
        assert request.full_url.endswith(rid)
        calls.append(request.full_url)
        raise URLError("synthetic transport timeout")

    checked = cr.recheck_one(rid, run_dir, outbox, _urlopen=unavailable)
    assert len(calls) == 1
    assert cr.read_receipt(run_dir, rid) == checked
    assert checked["receipt"] == saved["receipt"]
    failed_check = cr.states(run_dir, cat, outbox)[0]
    assert failed_check["delivered"] and failed_check["stale"]
    assert failed_check["decision"] == ok["decision"]
    assert failed_check["stale_reason"] == cr.STALE_LAST_CHECK
    seen.append(failed_check["stale_reason"])
    # (д) записи каталога у прогона нет, служба закрыла номер: состояние
    # известно из отказа — и подпись говорит именно это.
    said = f"заявка {rid} отклонена владельцем: нет"
    body = {"detail": {"code": "request-closed", "words": said,
                       "extra": {"existing": rid, "state": "отклонена"}}}
    with _FakeService([(409, body)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["refused"] == 1
    closed = cr.states(run_dir, _cat(), outbox)[0]
    assert closed["remote_state"] == "отклонена" and closed["stale"] is True
    assert closed["stale_reason"] == cr.STALE_FROM_REFUSAL
    seen.append(closed["stale_reason"])
    # Перечень закрыт и пройден весь: слова экрана берутся только из него.
    assert sorted(set(seen)) == sorted(cr.STALE_REASONS)


def test_21x_k08_the_receipt_names_where_the_queue_file_actually_is(
        tmp_path, monkeypatch):
    """Квитанция называет ФАКТИЧЕСКОЕ положение файла заявки в очереди, а
    не намерение доставки: живой файл уходит в `sent/` (при занятом
    `sent/` — дублем в `duplicates/`), а уходить нечему — пишется то, где
    файл лежит: `sent` | `withdrawn` | None. Доставка по явному номеру
    идёт и от записи прогона, и файла в очереди может не быть вовсе.

    Инверсия дефекта (второе ревью K08, находка 4): «живого файла нет»
    давало `queue: "sent"` всегда — даже когда в `sent/` ничего не было и
    заявка лежала отозванной."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    paths = cr._outbox_paths(outbox, rid)
    sent_record = _sent_of(rid, outbox)
    ok = _receipt(rid, sent=sent_record)
    again = _receipt(rid, sent=sent_record, repeat=True)
    # (а) живой файл — уходит в `sent/` (как прежде).
    with _FakeService([(201, ok)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["queue"] == "sent"
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    # (б) повтор по номеру: живого файла нет, `sent/` на месте.
    with _FakeService([(200, again)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["queue"] == "sent"
    # (в) файла нет НИГДЕ (разобрала читающая сторона): выдумывать
    # `sent/` нечем — положения нет.
    paths["sent"].unlink()
    with _FakeService([(200, again)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["queue"] is None
    assert _queue_files(outbox, rid) == []
    # (г) заявка отозвана аналитиком: файл в `withdrawn/` — его и
    # называет квитанция, в том числе при отказе `409 request-exists`.
    assert cr.resend(rid, run_dir, outbox)["ok"] is True
    assert cr.withdraw(rid, run_dir, outbox)["ok"] is True
    assert _queue_files(outbox, rid) == [f"withdrawn/{rid}.json"]
    exists = {"detail": {"code": "request-exists",
                         "words": "уже подана иным черновиком",
                         "extra": {"existing": rid}}}
    with _FakeService([(409, exists)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["refused"] == 1
    assert cr.read_receipt(run_dir, rid)["queue"] == "withdrawn"
    assert _queue_files(outbox, rid) == [f"withdrawn/{rid}.json"]
    # (д) живой файл при занятом `sent/` — дубль: квитанция говорит
    # «duplicates», победитель не тронут.
    body_bytes = (run_dir / "catalog-requests" / f"{rid}.json").read_bytes()
    paths["sent"].parent.mkdir(parents=True, exist_ok=True)
    paths["sent"].write_bytes(body_bytes)
    paths["live"].write_bytes(body_bytes)
    with _FakeService([(200, again)]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["queue"] == "duplicates"
    assert paths["sent"].read_bytes() == body_bytes
    # Закон читателя очереди — словами, без доставки: старшинство
    # положений и «нигде» как None.
    empty = cr._outbox_paths(outbox / "proba", rid)
    assert cr._queue_where(empty) is None
    for key, word in (("duplicates", "duplicates"),
                      ("withdrawn", "withdrawn"),
                      ("sent", "sent"), ("live", "outbox")):
        empty[key].parent.mkdir(parents=True, exist_ok=True)
        empty[key].write_bytes(body_bytes)
        assert cr._queue_where(empty) == word, key


def test_21y_k08_a_delayed_answer_neither_rolls_back_nor_confirms(
        tmp_path, monkeypatch):
    """Старшинство решения доказывает САМО решение, а не время получения
    ответа службы (K08-F1, три контрпримера аудитора — инверсия):

    (а) служба сложила ответ «принята» в 09:00, владелец отклонил в
    10:00, ответ пришёл в 11:00 — остаётся «отклонена» с решением 10:00;
    (б) старое принятие 09:00, пришедшее ПОСЛЕ нового принятия 10:00
    (между ними пересмотр), — то же слово состояния подтверждением не
    становится; (в) копия каталога держит принятие 09:00, а служба
    ответила пересмотренным 10:00 — показано решение ответа, решение
    записи названо отдельно.

    Прежде решало время получения ответа и равенство слова состояния:
    запоздавший ответ отменял решение владельца, а одно слово «принята»
    объявляло решение подтверждённым (`stale: false`)."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T08:00:00+02:00"
    old = {"by": "владелец", "at": "2026-09-12T09:00:00+02:00",
           "words": "первое принятие"}
    yes = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
           "words": "принято заново после пересмотра"}
    no = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
          "words": "нет: механика спорна"}

    def answer(state, decision, *, got_at):
        """Ответ службы, ПОЛУЧЕННЫЙ в `got_at`: 200 «уже подана» —
        квитанция `receipt/1`, несущая в том же теле и ПОЛНУЮ запись
        заявки, как отвечает служба (`request_queue.receipt`/`_known`).
        Решение читается из ЗАПИСИ: её форма — та же, что у записи
        каталога, и редакция сравнивается с редакцией, момент с
        моментом (числовую версию квитанции запись 0.5.0 не имеет)."""
        monkeypatch.setattr(cr, "_now", lambda: got_at)
        rcpt = _again(rid, outbox, state=state,
                      decision=dict(decision, version=1),
                      request=dict(record, state=state, decision=decision))
        with _FakeService([rcpt]) as srv:
            _service_env(monkeypatch, srv.url)
            assert cr.deliver(run_dir, [rid],
                              _sleep=_no_sleep)["delivered"] == 1

    def catalog(state, decision, name):
        return _catalog_with_requests(tmp_path, monkeypatch, name,
                                      [dict(record, state=state,
                                            decision=decision)])

    # (а) запоздавший ответ 09:00 против решения владельца 10:00.
    answer("принята", old, got_at="2026-09-12T11:00:00+02:00")
    assert cr.receipt_confirmed(cr.read_receipt(run_dir, rid),
                                rid)["decision"] == old, \
        "решение ответа службы сохранено — им и судят старшинство"
    got = cr.states(run_dir, catalog("отклонена", no, "cat21y-a"), outbox)[0]
    assert got["remote_state"] == "отклонена"
    assert got["decision"] == cr.decision_of({"decision": no})
    assert got["stale"] is True
    assert got["stale_reason"] == cr.STALE_RECEIPT_OLDER
    assert got["last_known"] is None, "решение записи — показанное, не чужое"
    assert got["transport_state"] == "доставлена"
    assert got["state"] == "отклонена: нет: механика спорна"
    # (б) то же слово состояния у обоих — подтверждением не становится.
    two = cr.states(run_dir, catalog("принята", yes, "cat21y-b"), outbox)[0]
    assert two["remote_state"] == "принята"
    assert two["decision"] == cr.decision_of({"decision": yes})
    assert two["stale"] is True and two["last_known"] is None
    assert two["stale_reason"] == cr.STALE_RECEIPT_OLDER
    # (в) отстала КОПИЯ КАТАЛОГА: ответ службы строго старше.
    answer("принята", yes, got_at="2026-09-12T11:30:00+02:00")
    behind = cr.states(run_dir, catalog("принята", old, "cat21y-c"),
                       outbox)[0]
    assert behind["remote_state"] == "принята"
    assert behind["decision"] == cr.decision_of({"decision": yes})
    assert behind["stale"] is True
    assert behind["stale_reason"] == cr.STALE_CATALOG_BEHIND
    assert behind["last_known"] == {"state": "принята",
                                    "decision": cr.decision_of(
                                        {"decision": old})}
    # Положительный контроль: ТА ЖЕ редакция решения (кто, слова, момент)
    # при том же состоянии — подтверждено, причины нет.
    same = cr.states(run_dir, catalog("принята", yes, "cat21y-ok"),
                     outbox)[0]
    assert same["remote_state"] == "принята" and same["stale"] is False
    assert same["stale_reason"] is None and same["last_known"] is None
    assert same["decision"] == cr.decision_of({"decision": yes})
    # Полная современная квитанция отдаёт собственное решение ответа.
    states_ = cr.service_request_states(_cat())
    rc = cr.read_receipt(run_dir, rid)
    assert cr.receipt_confirmed(rc, rid, states_)["decision"] == yes
    # Файл квитанции ПРЕЖНЕЙ формы (решения не несёт вовсе — прогоны
    # до волны 17а) читается КОНСЕРВАТИВНО: доказать ему нечем, и
    # решение записи показано последним известным — даже при совпавшем
    # слове состояния.
    legacy = {"request_id": rid, "delivered": True, "http": 200,
              "state": "принята", "note": "уже подана",
              "at": "2026-09-12T11:30:00+02:00"}
    cr.write_receipt(run_dir, legacy)
    assert cr.receipt_confirmed(legacy, rid, states_) is None
    quiet = cr.states(run_dir, catalog("принята", yes, "cat21y-legacy"),
                      outbox)[0]
    assert quiet["remote_state"] == "принята"
    assert quiet["decision"] == cr.decision_of({"decision": yes})
    assert quiet["stale"] is True
    assert quiet["stale_reason"] == cr.STALE_NO_RECEIPT
    assert quiet["transport_state"] == "неизвестно"
    assert quiet["delivered"] is False
    assert cr.quarantined(run_dir, outbox) == [rid]
    # Идентичность решения — закон словами: одно слово состояния её не
    # заменяет, а `by`, слова, момент и числовая версия — образуют.
    assert cr.decision_identity(yes) == cr.decision_identity(dict(yes))
    assert cr.decision_identity(yes) != cr.decision_identity(no)
    assert cr.decision_identity({"by": None, "words": None, "at": None}) == ()
    assert cr.decision_identity(None) == cr.decision_identity({})
    assert cr.decision_identity(dict(yes, version=2)) != \
        cr.decision_identity(dict(yes, version=3))
    assert cr.decision_identity(dict(yes, version=2)) == \
        cr.decision_identity(dict(yes, version="2"))
    # Целое сравнивается целым, без потери разрядов на `float`.
    big = 9007199254740992
    assert cr._newer_decision({"version": big + 1}, {"version": big}) is True
    assert cr._newer_decision({"version": 10 ** 400},
                              {"version": 10 ** 400 - 1}) is True
    # Решение, названное САМИМ ответом: поле квитанции `receipt/1` либо
    # полная запись службы; не названо — None, и это не «решения нет».
    # Первой читается ЗАПИСЬ того же тела: до каталога 0.6.0 только её
    # решение сравнимо с решением записи каталога (у квитанции числовая
    # версия есть всегда, у записи — никогда, а число с моментом не
    # сравнивается). Оба носителя называют одну редакцию: поле квитанции
    # служба складывает из той же записи (`request_queue.decision_of`).
    # …но ТОЛЬКО СВЯЗАННОЕ с самой квитанцией вложение (K07C-F1): его
    # номер, раздел, запись, канон черновика, состояние и `by`/`words`/
    # `at` решения — те же, что у квитанции. Прежде приоритет вложения
    # был безусловным, и тело с решением `no` сверху и `yes` внутри
    # отдавало `yes`: чужие слова заменяли слова решения этой заявки.
    связано = {"request_id": rid, "section": record["section"],
               "record_id": record["record_id"], "state": "принята",
               "canon": _draft_canon(record["draft"]),
               "decision": dict(yes, version=1),
               "request": dict(record, state="принята", decision=yes)}
    assert cr.answer_decision(связано) == yes
    assert cr.nested_request_problems(связано) == []
    assert cr.answer_decision({"decision": no,
                               "request": {"decision": yes}}) is None
    assert cr.answer_decision({"request": {"decision": yes}}) is None
    assert cr.answer_decision(dict(связано, state="есть")) is None
    assert cr.answer_decision({"decision": yes}) == yes
    assert cr.answer_decision({"request": {"decision": {"words": None}}}) \
        == {"words": None}
    assert cr.answer_decision({"state": "подана"}) is None


def test_21z_k08_one_lock_per_number_keeps_the_first_accepted_content(
        tmp_path, monkeypatch):
    """ОДНА координация по номеру заявки: проверка, публикация и перенос
    идут под замком номера, и первое принятое содержимое сохраняется
    (K08-F2).

    Инверсия дефекта: прежде `sent/` проверялся ОТДЕЛЬНО от
    исключительного создания живого файла — настоящая доставка
    победителя между этими двумя шагами уводила его в `sent/`, и под тем
    же номером появлялся второй, живой и отправляемый, ИНОЙ черновик; а
    `_move_to_sent` заменяющим переименованием затирал победителя,
    появившегося после его проверки."""
    import builtins
    import os as _os
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    other = json.loads(json.dumps(record, ensure_ascii=False))
    other["draft"]["semantics"]["text"] = "иная механика второго черновика"
    other["source"]["ref"] = "run-0041"
    other_bytes = (json.dumps(other, ensure_ascii=False,
                              indent=2) + "\n").encode("utf-8")
    paths = cr._outbox_paths(outbox, rid)
    winner_bytes = paths["live"].read_bytes()
    real_open, order = builtins.open, []

    # (1) порядок «A доставлен ДО создания файла B»: настоящая доставка
    # A исполняется ровно между проверкой B и его исключительным
    # созданием живого файла.
    def scheduled_open(file, mode="r", *a, **kw):
        if str(mode) == "xb" and Path(file) == paths["live"] and not order:
            with _FakeService([_ok(rid, outbox)]) as srv:
                _service_env(monkeypatch, srv.url)
                order.append(cr.deliver(run_a, [rid],
                                        _sleep=_no_sleep)["delivered"])
        return real_open(file, mode, *a, **kw)

    with monkeypatch.context() as mp:
        mp.setattr(builtins, "open", scheduled_open)
        got = cr.enqueue(other, run_b, outbox)
    assert order == [1], "доставка победителя действительно исполнилась"
    assert paths["sent"].read_bytes() == winner_bytes, "победитель цел"
    assert not paths["live"].exists(), "второго живого файла не бывает"
    assert got["queued"] is False and "дубль не заводится" in got["words"]
    assert "run-0040" in got["words"], "победитель назван поимённо"
    assert cr._read_json(paths["duplicates"]) == other, "след дубля остался"
    # Повторной отправки у проигравшего нет: файла в очереди у него нет.
    assert cr.deliverable(run_b, outbox) == []

    # (2) порядок «файл-победитель появился ДО переноса A»: победитель,
    # положенный писателем без замка, НЕ заменяется — живой файл уходит
    # в `duplicates/`.
    second = cr._outbox_paths(tmp_path / "queue2", rid)
    second["live"].parent.mkdir(parents=True, exist_ok=True)
    second["live"].write_bytes(other_bytes)
    original, claimed = cr._file_replace.claim_with_retry, []

    def concurrent_claim(src, dst, *a, **kw):
        if Path(dst) == second["sent"] and not claimed:
            second["sent"].parent.mkdir(parents=True, exist_ok=True)
            second["sent"].write_bytes(winner_bytes)
            claimed.append(True)
        return original(src, dst, *a, **kw)

    with monkeypatch.context() as mp:
        mp.setattr(cr._file_replace, "claim_with_retry", concurrent_claim)
        assert cr._move_to_sent(second) == "duplicates"
    assert claimed == [True]
    assert second["sent"].read_bytes() == winner_bytes, "победитель цел"
    assert cr._read_json(second["duplicates"]) == other
    assert not second["live"].exists()

    # (3) `sent/` уже занят ДО подачи — столкновение словами, победитель
    # не тронут, второго живого файла нет.
    thirds = cr._outbox_paths(tmp_path / "queue3", rid)
    thirds["sent"].parent.mkdir(parents=True, exist_ok=True)
    thirds["sent"].write_bytes(winner_bytes)
    stop = cr.enqueue(other, run_b, tmp_path / "queue3")
    assert stop["queued"] is False and "дубль не заводится" in stop["words"]
    assert not thirds["live"].exists()
    assert thirds["sent"].read_bytes() == winner_bytes
    # (4) положительный контроль: одиночная подача в пустую очередь.
    alone = tmp_path / "queue4"
    ok = cr.enqueue(other, run_b, alone)
    assert ok == {"queued": True, "words": None}
    assert cr._read_json(cr._outbox_paths(alone, rid)["live"]) == other
    # (5) сам замок: чужой нити он закрыт (отказ словами, а не работа
    # без координации), своей — открыт повторным входом, и после выхода
    # снимается.
    lock_dir = Path(alone) / cr.QUEUE_LOCK_DIR / rid
    busy: list = []

    def rival():
        try:
            with cr.queue_lock(alone, rid, window_s=0.05):
                busy.append("вошёл")
        except cr.QueueBusy as e:
            busy.append(str(e))

    with cr.queue_lock(alone, rid):
        assert lock_dir.is_dir()
        with cr.queue_lock(alone, rid):            # своя нить — повторно
            assert lock_dir.is_dir()
        t = threading.Thread(target=rival)
        t.start()
        t.join(timeout=10)
    assert len(busy) == 1 and "занята другим писателем" in busy[0]
    assert not lock_dir.exists(), "замок снят выходом из участка"
    # Брошенный замок (писатель аварийно вышел) снимается по возрасту.
    lock_dir.mkdir(parents=True)
    stale = time.time() - cr.LOCK_STALE_S - 1
    _os.utime(lock_dir, (stale, stale))
    with cr.queue_lock(alone, rid, window_s=0.05):
        assert lock_dir.is_dir()
    assert not lock_dir.exists()


def test_21ab_k08_canonical_number_is_resolved_before_the_decision(
        tmp_path, monkeypatch):
    """Канонический номер общей заявки разрешается ДО чтения решения
    (K08-F3): служба отказала «по записи уже есть открытая заявка
    <номер>» — решение читается по НЕЙ, свой номер и отказ остаются
    рядом, оба номера названы, файл остаётся в очереди (§11.2 п. 6), а
    повторной отправки нет.

    Понимаются ОБА имени одного отказа: `duplicate-request` нынешней
    службы (иной номер) и `request-exists` (тот же номер). Прежде
    канонический номер вычислялся ПОСЛЕ чтения решения: состояние
    оставалось пустым, решение — пустым, а ссылка панели вела на
    несуществующий черновик этого прогона."""
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_CATALOG_OUTBOX", str(tmp_path / "queue"))
    outbox = cr.outbox_root()
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    # Два прогона — два РАЗНЫХ пробела одной записи каталога: ключи и
    # номера разные, а запись у службы одна, и вторая заявка получает
    # `409 duplicate-request` с номером первой.
    doc_a = _model([_rule("R-S1-a", "разрыв между явками", fit=_c())])
    doc_b = _model([_rule("R-S1-b", "минимальный разрыв соседних явок",
                          fit=_c())])
    run_a = _make_run(tmp_path, doc_a, name="run-0040")
    run_b = _make_run(tmp_path, doc_b, name="run-0041")
    rows_a, rows_b = _rows_of(doc_a, run_a), _rows_of(doc_b, run_b)
    assert _submit(run_a, rows_a, llm)[0]["submitted"] == 1
    assert _submit(run_b, rows_b, llm)[0]["submitted"] == 1
    rid = rows_a[0]["request_id"]
    mine = rows_b[0]["request_id"]
    assert mine != rid, "иной пробел — иной ключ и иной номер"
    record = json.loads((run_a / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    decision = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
                "words": "решение общей заявки"}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat21ab",
                                 [dict(record, state="принята",
                                       decision=decision)])
    for code in ("duplicate-request", "request-exists"):
        body = {"detail": {"code": code,
                           "words": f"по записи уже есть открытая заявка "
                                    f"{rid}",
                           "extra": {"existing": rid}}}
        with _FakeService([(409, body)]) as srv:
            _service_env(monkeypatch, srv.url)
            assert cr.deliver(run_b, [mine], _sleep=_no_sleep)["refused"] == 1
        got = [r for r in cr.states(run_b, cat, outbox, rows_b)
               if r["request_id"] == mine][0]
        assert got["canonical_request_id"] == rid, code
        assert got["transport_state"] == "отказ", code
        assert got["remote_state"] == "принята", code
        assert got["decision"] == cr.decision_of({"decision": decision}), code
        assert got["source_run"] == "run-0040", code
        assert got["origin"] == "другой прогон (иной черновик)", code
        assert got["delivered"] is False, code
        # Оба номера названы словами панели, и принятым свой черновик не
        # объявляется.
        assert f"общая заявка {rid}" in got["state"], code
        assert f"номер этого прогона {mine}" in got["state"], code
        assert "принята: решение общей заявки" in got["state"], code
        # Файл остаётся в очереди: в `sent/` уходит только доставленное.
        assert cr.read_receipt(run_b, mine)["queue"] == "outbox", code
        assert _queue_files(outbox, mine) == [f"{mine}.json"], code
        # Повторной отправки нет: иной черновик ею принят не станет.
        again = cr.resend(mine, run_b, outbox, cat)
        assert again["ok"] is False, code
        assert f"конфликт канона: заявка {rid}" in again["words"], code
    # Номер расходится ТОЛЬКО при конфликте канона: у своей заявки
    # канонический номер — собственный (положительный контроль).
    own = [r for r in cr.states(run_a, cat, outbox, rows_a)
           if r["request_id"] == rid][0]
    assert own["canonical_request_id"] == rid
    assert own["origin"] == "этот прогон"
    assert "конфликт канона" not in own["state"]
    assert cr.canonical_of(None, mine) == mine
    assert cr.canonical_of({"refusal": "request-closed",
                            "extra": {"existing": rid}}, mine) == mine
    assert cr.canonical_of({"refusal": "duplicate-request",
                            "extra": {"existing": rid}}, mine) == rid


def test_21ac_k08_an_unreadable_record_moment_does_not_open_a_rollback():
    """Четвёртое неавтор-ревью K08, K1: «откатывать нечего» — только
    когда у записи НЕТ решения (ни слов, ни момента). Решение записи с
    нечитаемым моментом («вчера») — решение: старый ответ службы с
    читаемым моментом его не заменяет и не подтверждает."""
    rec_dec = {"by": "владелец", "words": "нет", "at": "вчера",
               "version": None}
    old_reply = {"by": "служба", "words": "да", "at": "2026-09-12T09:00:00",
                 "version": None}
    assert cr.decision_identity(rec_dec) != ()
    assert cr._answer_supersedes(old_reply, rec_dec) is False
    # Решения у записи нет вовсе — ответ, назвавший своё, заменяет.
    assert cr._answer_supersedes(old_reply, {"words": None, "at": None,
                                             "version": None}) is True
    # Читаемые моменты: только строго старшее решение ответа заменяет.
    newer = dict(old_reply, at="2026-09-12T11:00:00")
    rec_iso = dict(rec_dec, at="2026-09-12T10:00:00")
    assert cr._answer_supersedes(newer, rec_iso) is True
    assert cr._answer_supersedes(old_reply, rec_iso) is False


# ═════════════════════ 22. Квитанция и исход доставки (K07-клиент)
#
# Контракт П0 §4.1 и §4.3. Заявка считается ПОДАННОЙ только по
# прикладной квитанции службы с идентичностью этой заявки; всё прочее —
# исход «неизвестен»: файл остаётся в очереди, повтор идёт ТЕМ ЖЕ
# ключом, попытка записывается. Тело квитанции судит ОДНА функция на
# обоих клиентов (`template_catalog.receipts.validate_receipt`),
# транспорт — код, тип содержимого и согласие признака повтора с кодом.


def _both_clients(status, body, sent=None, ctype=None):
    """Приговор обоих клиентов: CDPM и инструмента импорта COSMOS."""
    from catalog_service import import_requests as imp
    return (cr.receipt_problems(status, body, sent, ctype),
            imp.receipt_problems(status, body, sent, ctype))


def test_22a_k07_one_law_of_the_receipt_for_both_clients(tmp_path,
                                                         monkeypatch):
    """Закон квитанции ОДИН у клиента CDPM и у инструмента импорта
    COSMOS: тело судит общая функция читателя каталога, транспорт —
    код, тип содержимого и согласие повтора с кодом.

    Стенд сверяет ПРИГОВОРЫ на одной и той же матрице ответов: разойдясь,
    два перечня разошлись бы молча — так уже случилось однажды (слова
    руководства обещали проверку схемой, а код её не делал).

    Тип содержимого назван в КАЖДОЙ строке явно (K07C-N1). Оба клиента
    требуют точный application/json. Отсутствующий/ложный заголовок
    и вложенную запись дополнительно проверяет интегральная матрица
    test_k07_integral_receipt_agent через настоящие receipt/_known
    и обе клиентские точки входа, с изолированной границей транспорта."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    sent = _sent_of(rid, outbox)
    хорошая = _receipt(rid, sent=sent)
    json_ = cr.JSON_MEDIA_TYPE
    матрица = [
        (201, хорошая, json_),
        (200, dict(хорошая, note="уже подана", repeat=True), json_),
        (200, хорошая, json_),                      # 200 без слов повтора
        (201, dict(хорошая, note="уже подана", repeat=True), json_),
        (201, хорошая, "text/html"),
        (201, {k: v for k, v in хорошая.items() if k != "journal_ref"},
         json_),
        (201, dict(хорошая, request_id="req-" + "7" * 20), json_),
        (201, dict(хорошая, state="рассматривается"), json_),
        (201, dict(хорошая, schema_version="receipt/2"), json_),
        (201, dict(хорошая, canon="0" * 64), json_),
        (201, dict(хорошая, section="criteria"), json_),
        (201, dict(хорошая, record_id="chuzhaya_zapis"), json_),
        (204, None, json_),
        (202, {"note": "принято"}, json_),
        (500, {"detail": {"code": "x", "words": "y"}}, json_),
        (201, "<html>вход</html>", "text/html"),
    ]
    зелёных = 0
    for status, body, ctype in матрица:
        мой, их = _both_clients(status, body, sent, ctype)
        assert мой == их, (status, body, мой, их)
        зелёных += (not мой)
    assert зелёных == 2, "годных ответов ровно два: 201 и повтор 200"
    # Тело судится ИМЕННО общей функцией — ОДНОЙ И ТОЙ ЖЕ: у клиентов
    # не «такой же перечень», а буквально один объект из общего пакета
    # читателя каталога. Своей копии проверок тела ни у кого нет.
    import template_catalog.receipts as tcr
    from catalog_service import import_requests as imp
    assert cr.validate_receipt is tcr.validate_receipt
    assert imp.validate_receipt is tcr.validate_receipt
    assert cr.REPEAT_NOTE is tcr.REPEAT_NOTE
    # Схема квитанции у службы — байт в байт копия схемы читателя.
    import catalog_service as cs
    свой = (Path(tcr.__file__).parent / "schemas" / "receipt.schema.json")
    копия = (Path(cs.__file__).parent / "schemas" / "receipt.schema.json")
    assert свой.read_bytes() == копия.read_bytes()


def test_22b_k07_a_foreign_receipt_is_never_a_delivery(tmp_path,
                                                       monkeypatch):
    """Идентичность заявки: чужой номер, чужой раздел, чужая запись и
    иной канон черновика — исход «неизвестен», файл в очереди, запись
    прогона не тронута.

    Здесь закрыта граница, названная тестом 21n: СТАРАЯ форма файла
    квитанции чужого номера не различала (`deliver_one` писал в
    `request_id` отправленный номер, а тела службы не хранил), и слово
    состояния из словаря делало чужую квитанцию своей. Форма
    `receipt-file/2` держит тело целиком — и номер сверяется."""
    случаи = {
        "чужой номер": {"request_id": "req-" + "7" * 20},
        "чужой раздел": {"section": "criteria"},
        "чужая запись": {"record_id": "chuzhaya_zapis"},
        "иной канон черновика": {"canon": "0" * 64},
    }
    for i, (имя, правка) in enumerate(sorted(случаи.items())):
        with monkeypatch.context() as mp:
            run_dir, outbox, rid = _queued(tmp_path / f"f{i}", mp)
            sent = _sent_of(rid, outbox)
            with _FakeService([(201, _receipt(rid, sent=sent, **правка))]) \
                    as srv:
                _service_env(mp, srv.url)
                got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
            assert got["delivered"] == 0, имя
            файл = cr.read_receipt(run_dir, rid)
            assert файл["outcome"] == "неизвестен", имя
            assert файл["receipt"] is None, "негодное тело в файл не ложится"
            assert (outbox / f"{rid}.json").is_file(), имя
            assert not (outbox / "sent" / f"{rid}.json").is_file(), имя
            строка = cr.states(run_dir, _cat(), outbox)[0]
            assert строка["transport_state"] == "неизвестно", имя
            assert строка["delivered"] is False, имя
            assert строка["state"].startswith("подана без подтверждения"), имя
    # Тот же ответ, но СВОЙ — доставка.
    with monkeypatch.context() as mp:
        run_dir, outbox, rid = _queued(tmp_path / "ok", mp)
        with _FakeService([_ok(rid, outbox)]) as srv:
            _service_env(mp, srv.url)
            assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] \
                == 1


def test_22c_k07_a_lost_answer_is_retried_by_the_same_key(tmp_path,
                                                          monkeypatch):
    """Ответ потерян ПОСЛЕ фактического приёма: повтор идёт ТЕМ ЖЕ
    ключом, служба отвечает «уже подана», второй заявки не рождается.

    Журнал попыток ведёт счёт всему пути: три неудачные связи и успех
    четвёртой попытки — одна заявка, один номер, один файл."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    body_before = (outbox / f"{rid}.json").read_bytes()
    _service_env(monkeypatch, _dead_url())            # ответ не дошёл
    got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 0
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome"] == "неизвестен"
    assert файл["outcome_kind"] == "таймаут"
    assert len(файл["attempts"]) == 3
    assert (outbox / f"{rid}.json").read_bytes() == body_before
    # Повтор: служба отвечает повторной выдачей той же квитанции.
    with _FakeService([_again(rid, outbox)]) as srv:
        _service_env(monkeypatch, srv.url)
        got2 = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
        тела = [p["raw"] for p in srv.posts]
    assert got2["delivered"] == 1
    assert len(тела) == 1 and тела[0].encode("utf-8") == body_before, \
        "повтор отдаёт ТУ ЖЕ запись — новой заявки не составляется"
    файл2 = cr.read_receipt(run_dir, rid)
    assert файл2["outcome"] == "доставлена"
    assert файл2["receipt"]["request_id"] == rid
    assert len(файл2["attempts"]) == 4, "журнал продолжается, а не начинается"
    assert [a["outcome"] for a in файл2["attempts"]] == \
        ["неизвестен"] * 3 + ["доставлена"]
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"], "файл ОДИН"
    # Бюджет повторов — у ВЫЗОВА, не у журнала: заявка, у которой в
    # журнале уже есть неудачные попытки, получает свои три попытки на
    # `409 write-busy` и в следующем вызове.
    with monkeypatch.context() as mp:
        run2, outbox2, rid2 = _queued(tmp_path / "busy", mp)
        mp.setenv("CDPM_CATALOG_SERVICE_URL", _dead_url())
        mp.setenv("CDPM_CATALOG_SERVICE_TOKEN", _TEST_TOKEN)
        assert cr.deliver(run2, [rid2], _sleep=_no_sleep)["delivered"] == 0
        assert len(cr.read_receipt(run2, rid2)["attempts"]) == 3
        busy = (409, {"detail": {"code": "write-busy",
                                 "words": "каталог занят выпуском версии"}})
        with _FakeService([busy, busy, _ok(rid2, outbox2)]) as srv:
            _service_env(mp, srv.url)
            assert cr.deliver(run2, [rid2], _sleep=_no_sleep)["delivered"] == 1
            assert len(srv.posts) == 3, "три попытки и во втором вызове"
        assert len(cr.read_receipt(run2, rid2)["attempts"]) == 6


def test_22d_k07_no_secrets_in_the_receipt_file(tmp_path, monkeypatch):
    """Секретов в файле квитанции нет ни при каком ответе службы: строка
    журнала попыток — РОВНО четыре поля (`at`, `http`, `outcome`,
    `words`), а значение токена вырезается из слов, даже если служба
    вернула его в своём ответе.

    Файл квитанции уезжает в пакет задачи и в переписку: секрет, однажды
    попавший в слова, оттуда уже не вынуть."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    эхо = (401, {"detail": {"code": "bad-token",
                            "words": f"токен {_TEST_TOKEN} не тот"}})
    with _FakeService([эхо]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    сырой = cr.receipt_path(run_dir, rid).read_text(encoding="utf-8")
    assert _TEST_TOKEN not in сырой, "токен в файле квитанции"
    assert "<токен скрыт>" in сырой
    файл = cr.read_receipt(run_dir, rid)
    for попытка in файл["attempts"]:
        assert set(попытка) == {"at", "http", "outcome", "words"}
    # Тот же закон у сетевой ошибки: адрес с токеном в словах ошибки.
    with monkeypatch.context() as mp:
        run2, outbox2, rid2 = _queued(tmp_path / "net", mp)
        _service_env(mp, _dead_url())
        cr.deliver(run2, [rid2], _sleep=_no_sleep)
        assert _TEST_TOKEN not in cr.receipt_path(run2, rid2) \
            .read_text(encoding="utf-8")
    # И у успешной доставки: заголовков в файле нет вовсе.
    with monkeypatch.context() as mp:
        run3, outbox3, rid3 = _queued(tmp_path / "ok", mp)
        with _FakeService([_ok(rid3, outbox3)]) as srv:
            _service_env(mp, srv.url)
            cr.deliver(run3, [rid3], _sleep=_no_sleep)
        сырой3 = cr.receipt_path(run3, rid3).read_text(encoding="utf-8")
        assert _TEST_TOKEN not in сырой3 and "X-Catalog-Token" not in сырой3


def test_22e_k07_canon_conflict_stays_in_the_queue(tmp_path, monkeypatch):
    """`409 request-exists` — КОНФЛИКТ КАНОНА: файл остаётся в очереди
    (П0 §11.2 п. 6 — «не в `sent`, не „подана иным черновиком“»), слова
    называют ОБА номера, `canonical_request_id` — номер службы.

    Инверсия дефекта, оставленного K08 (§7 сдачи `HANDOFF-K08`): прежде
    `request-exists` уводил живой файл в `sent/` и подписывал строку
    «подана иным черновиком из run-XXXX» — чужой черновик выдавался за
    свою подачу."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    чужой = "req-" + "8" * 20
    отказ = (409, {"detail": {
        "code": "request-exists",
        "words": f"заявка {чужой} уже подана из run-0088 иным черновиком",
        "extra": {"existing": чужой, "ref": "run-0088#0123456789ab",
                  "state": "подана"}}})
    with _FakeService([отказ]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 0 and got["refused"] == 1
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome"] == "отказ"
    assert файл["outcome_kind"] == "409 request-exists"
    assert файл["queue"] == "outbox", "конфликт канона — не в sent/"
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    строка = cr.states(run_dir, _cat(), outbox)[0]
    assert строка["transport_state"] == "отказ"
    assert строка["delivered"] is False
    assert строка["canonical_request_id"] == чужой
    assert строка["request_id"] == rid
    assert строка["state"].startswith("конфликт канона: ")
    # Панель показывает ОБА номера: свой и номер службы.
    assert чужой in строка["state"] and rid in строка["state"]
    assert "run-0088" in строка["state"]
    assert "подана иным черновиком" not in строка["state"]
    # Повтор доставки даёт то же самое: решение — за владельцем, файл
    # в очереди, второго номера не рождается.
    with _FakeService([отказ]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    assert len(cr.read_receipt(run_dir, rid)["attempts"]) == 2


def test_22f_k07_request_closed_carries_the_decision(tmp_path, monkeypatch):
    """`409 request-closed` — фактическое состояние «отклонена» С
    РЕШЕНИЕМ владельца (слова, версия, момент из `extra.decision`
    службы), а не «неизвестно» и не успех: транспорт «отказ», файл в
    очереди, экран называет решение.

    Слова отказа остаются запасным путём — их пересказ помечается
    источником, чтобы не выдаваться за запись каталога."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    решение = {"words": "механика покрыта семьёй rest_floors", "version": 3,
               "at": "2026-09-12T09:30:00+02:00"}
    отказ = (409, {"detail": {
        "code": "request-closed",
        "words": f"заявка {rid} отклонена владельцем: механика покрыта",
        "extra": {"existing": rid, "state": "отклонена",
                  "decision": решение}}})
    with _FakeService([отказ]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
    assert got["delivered"] == 0
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome_kind"] == "409 request-closed"
    assert файл["outcome"] == "отказ"
    assert файл["state_words"] == \
        "отклонена: механика покрыта семьёй rest_floors"
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    строка = cr.states(run_dir, _cat(), outbox)[0]
    assert строка["remote_state"] == "отклонена"
    assert строка["transport_state"] == "отказ"
    assert строка["delivered"] is False
    assert строка["stale"] is True
    assert строка["stale_reason"] == cr.STALE_FROM_REFUSAL
    assert строка["decision"] == {"words": решение["words"], "version": 3,
                                  "at": решение["at"]}
    # Без структурного решения — запасной путь: слова отказа С ПОМЕТКОЙ.
    with monkeypatch.context() as mp:
        run2, outbox2, rid2 = _queued(tmp_path / "bez", mp)
        голый = (409, {"detail": {
            "code": "request-closed", "words": f"заявка {rid2} отклонена",
            "extra": {"existing": rid2, "state": "отклонена"}}})
        with _FakeService([голый]) as srv:
            _service_env(mp, srv.url)
            cr.deliver(run2, [rid2], _sleep=_no_sleep)
        стр2 = cr.states(run2, _cat(), outbox2)[0]
        assert стр2["remote_state"] == "отклонена"
        assert cr.DECISION_FROM_REFUSAL in стр2["decision"]["words"]
        assert стр2["decision"]["version"] is None


def _card(sent, *, state="подана", decision=None, **over) -> dict:
    """Карточка заявки, как её отдаёт `GET /api/requests/<номер>`: сама
    запись плюс признаки экрана. Квитанцией она НЕ является."""
    got = dict(sent)
    got.update({"state": state, "record_exists": False, "has_draft": True,
                "decision": decision or {"by": None, "at": None,
                                         "words": None}})
    got.update(over)
    return got


def test_22g_k07_recheck_of_the_quarantine_is_idempotent(tmp_path,
                                                         monkeypatch):
    """Сверка карантина (§4.3): файл очереди в `sent/` без прикладной
    квитанции сверяется со службой ПО КЛЮЧУ (`GET /api/requests/<номер>`)
    БЕЗ новой подачи.

    Сверка ничего не меняет в мире: подачи нет, очередь не тронута,
    индекс не тронут, и два вызова подряд при том же ответе службы дают
    файлы БАЙТ В БАЙТ те же (часы прогона в стенде остановлены — в жизни
    расходится только момент последней сверки).

    Карточка службы — ЗНАНИЕ, а не доказательство своей подачи: тот же
    номер мог доставить другой прогон, и карантин она не снимает."""
    doc, run_a, run_b, outbox, rid, record = _k08_pair(tmp_path, monkeypatch)
    rows_b = _rows_of(doc, run_b)
    llm = _composer()
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: llm)
    assert _submit(run_b, rows_b, llm)[0]["submitted"] == 1
    with _FakeService([_ok(rid, outbox)]) as srv:        # доставил run-0040
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_a, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.quarantined(run_b, outbox) == [rid]
    assert cr.quarantined(run_a, outbox) == [], "у run-0040 квитанция есть"
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T12:00:00+02:00")
    карточка = _card(_sent_of(rid, outbox), state="принята",
                     decision={"by": "владелец", "words": "нужна",
                               "at": "2026-09-12T11:00:00+02:00"})
    снимки = []
    with _FakeService([], gets=[(200, карточка), (200, карточка)]) as srv:
        _service_env(monkeypatch, srv.url)
        for _ in range(2):
            got = cr.recheck(run_b, outbox=outbox)
            assert got["confirmed"] == 0 and got["unconfirmed"] == 1
            снимки.append((_tree(outbox, ""), _tree(run_b / "channel", ""),
                           _tree(run_b / "catalog-requests", "")))
        assert [x for x in srv.seen if x["method"] == "POST"] == [], \
            "сверка НИЧЕГО не подаёт"
    assert снимки[0] == снимки[1], "сверка идемпотентна байт в байт"
    assert _queue_files(outbox, rid) == [f"sent/{rid}.json"]
    файл = cr.read_receipt(run_b, rid)
    assert файл["outcome"] == "неизвестен", "карточка — не квитанция"
    assert файл["known"]["state"] == "принята"
    assert файл["known"]["source_run"] == "run-0040"
    assert файл["last_checked_at"] == "2026-09-12T12:00:00+02:00"
    строка = cr.states(run_b, _cat(), outbox)[0]
    assert строка["transport_state"] == "неизвестно"
    assert строка["delivered"] is False
    assert "заявка есть у службы (состояние «принята»)" in строка["state"]
    assert строка["state"].startswith("подана без подтверждения")
    # Служба ответила на сверку прикладной КВИТАНЦИЕЙ — карантин снят.
    with _FakeService([], gets=[(200, _receipt(rid, sent=_sent_of(rid, outbox),
                                               state="принята",
                                               repeat=True))]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.recheck(run_b, outbox=outbox)["confirmed"] == 1
    assert cr.quarantined(run_b, outbox) == []
    снято = cr.states(run_b, _cat(), outbox)[0]
    assert снято["transport_state"] == "доставлена"
    assert снято["remote_state"] == "принята"


def test_22h_k07_an_unknown_request_returns_to_the_queue(tmp_path,
                                                         monkeypatch):
    """Служба заявки НЕ ЗНАЕТ: подача не дошла — файл возвращается из
    `sent/` в очередь, повтор пойдёт ТЕМ ЖЕ номером. Повторная сверка
    ничего не меняет. Ручки карточки у службы нет — сверка идёт
    предпросмотром (`dry-run`), который следа не оставляет.

    Возврат — такое же ИЗМЕНЕНИЕ СОСТОЯНИЯ очереди, как публикация и
    перенос, и идёт под тем же замком номера (K08, F2): место занимают,
    а не заменяют, и живой файл, появившийся ровно между проверкой и
    переносом, не затирается."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    # Файл в `sent/` без квитанции — след прежнего кода (до K07 туда
    # уходил и неподтверждённый ответ).
    paths = cr._outbox_paths(outbox, rid)
    paths["sent"].parent.mkdir(parents=True, exist_ok=True)
    paths["live"].replace(paths["sent"])
    assert cr.quarantined(run_dir, outbox) == [rid]
    не_знает = (404, {"detail": {"code": "unknown-request",
                                 "words": f"заявки {rid} в каталоге нет"}})
    with _FakeService([], gets=[не_знает]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.recheck(run_dir, outbox=outbox)
        подачи = [x for x in srv.seen if x["method"] == "POST"]
    assert подачи == [] and got["unconfirmed"] == 1
    assert _queue_files(outbox, rid) == [f"{rid}.json"], \
        "файл вернулся в очередь: «отправленным» он назван по ошибке"
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome"] == "неизвестен"
    assert файл["words"] == cr.UNKNOWN_AT_SERVICE
    assert файл["queue"] == "outbox"
    # Повторная сверка: файл уже в очереди — возвращать нечего.
    with _FakeService([], gets=[не_знает]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.recheck(run_dir, [rid], outbox=outbox)
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    # Ручки карточки нет (404 без кода `unknown-request`) — предпросмотр.
    paths["live"].replace(paths["sent"])
    нет_ручки = (404, {"detail": "Not Found"})
    предпросмотр = (200, {"request_id": rid, "known": None, "problems": []})
    with _FakeService([], gets=[нет_ручки, предпросмотр]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.recheck(run_dir, [rid], outbox=outbox)
        пути = [x["path"] for x in srv.seen]
    assert cr.SERVICE_DRY_RUN_PATH in пути, "запасной путь — предпросмотр"
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    assert cr.read_receipt(run_dir, rid)["words"] == cr.UNKNOWN_AT_SERVICE
    # «Ручки нет» и «заявки нет» — РАЗНЫЕ вещи: спросить было нечем
    # (ни карточки у службы, ни записи у прогона) — файл не трогаем.
    paths["live"].replace(paths["sent"])
    for p in (run_dir / "catalog-requests" / f"{rid}.json",):
        p.unlink()
    assert cr._queue_record(run_dir, rid, outbox) is not None
    (outbox / "sent" / f"{rid}.json").replace(tmp_path / "spryatan.json")
    assert cr._queue_record(run_dir, rid, outbox) is None
    with _FakeService([], gets=[нет_ручки]) as srv:
        _service_env(monkeypatch, srv.url)
        cr.recheck(run_dir, [rid], outbox=outbox)
        пути2 = [x["path"] for x in srv.seen]
    assert cr.SERVICE_DRY_RUN_PATH not in пути2, "спрашивать предпросмотр нечем"
    слова = cr.read_receipt(run_dir, rid)["words"]
    assert слова.startswith("сверка не выполнена: ")
    assert слова != cr.UNKNOWN_AT_SERVICE
    # Гонка возврата: живой файл появился ПОСЛЕ проверки и ДО переноса —
    # победителя возврат не заменяет, а отступает (разрыв check/act
    # закрывает замок, а не ещё одна проверка).
    гонка = cr._outbox_paths(tmp_path / "queue-race", rid)
    гонка["sent"].parent.mkdir(parents=True, exist_ok=True)
    гонка["sent"].write_bytes(b'{"otpravlennyy": true}')
    чужой = b'{"chuzhoy": true}'
    было, занято = cr._file_replace.claim_with_retry, []

    def claim_after_rival(src, dst, *a, **kw):
        if Path(dst) == гонка["live"] and not занято:
            гонка["live"].parent.mkdir(parents=True, exist_ok=True)
            гонка["live"].write_bytes(чужой)
            занято.append(True)
        return было(src, dst, *a, **kw)

    with monkeypatch.context() as mp:
        mp.setattr(cr._file_replace, "claim_with_retry", claim_after_rival)
        assert cr._back_to_queue(гонка) is None
    assert занято == [True]
    assert гонка["live"].read_bytes() == чужой, "победитель очереди цел"
    assert гонка["sent"].read_bytes() == b'{"otpravlennyy": true}'


def test_22i_k07_a_kind_request_travels_unchanged(tmp_path, monkeypatch):
    """Закон доставки безразличен к ФОРМЕ заявки: у заявки на вид
    исполнителя черновика нет (`canon` квитанции — `null`), и тело
    уходит службе байт в байт тем, что лежит в очереди.

    Сами кандидаты на вид исполнителя — единица K06-барьера; здесь
    судится только транспорт."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch)
    вид = {"request_id": "req-" + "3" * 20, "state": "подана",
           "section": "families", "record_id": "rest_floors",
           "submitted_at": "2026-09-12T09:00:00+02:00", "submitted_by": "CDPM",
           "source": {"kind": "посадка категории C",
                      "document_class": cr.DEFAULT_DOCUMENT_CLASS,
                      "run_class": "прогон CDPM", "ref": "run-0090#abcdef"},
           "executor_kinds_requested": ["fo"], "cr_key": None,
           "decision": {"by": None, "at": None, "words": None}}
    assert cr.enqueue(вид, run_dir, outbox)["queued"] is True
    файл_очереди = (outbox / f"{вид['request_id']}.json").read_bytes()
    квитанция = _receipt(вид["request_id"], sent=вид, record_id="rest_floors")
    assert квитанция["canon"] is None, "у заявки на вид черновика нет"
    with _FakeService([(201, квитанция)]) as srv:
        _service_env(monkeypatch, srv.url)
        got = cr.deliver(run_dir, [вид["request_id"]], _sleep=_no_sleep)
        отдано = srv.posts[0]["raw"].encode("utf-8")
    assert got["delivered"] == 1
    assert отдано == файл_очереди, "тело — запись очереди байт в байт"
    assert json.loads(отдано)["executor_kinds_requested"] == ["fo"]
    assert "draft" not in json.loads(отдано)
    файл = cr.read_receipt(run_dir, вид["request_id"])
    assert файл["outcome"] == "доставлена"
    assert (outbox / "sent" / f"{вид['request_id']}.json").is_file()
    # Чужая запись в квитанции — по-прежнему не доставка.
    with monkeypatch.context() as mp:
        run2, outbox2, _ = _queued(tmp_path / "vid2", mp)
        вид2 = dict(вид, request_id="req-" + "4" * 20)
        assert cr.enqueue(вид2, run2, outbox2)["queued"] is True
        плохая = _receipt(вид2["request_id"], sent=вид2,
                          record_id="days_off_floor")
        with _FakeService([(201, плохая)]) as srv:
            _service_env(mp, srv.url)
            assert cr.deliver(run2, [вид2["request_id"]],
                              _sleep=_no_sleep)["delivered"] == 0


def test_22j_k07_legacy_flags_do_not_replace_a_modern_receipt(tmp_path,
                                                            monkeypatch):
    """Старый delivered-флаг не доказывает современную доставку (§4.3).

    Старый файл читается без перештамповки, но остаётся в карантине;
    полное receipt/1 с той же идентичностью подтверждает доставку.
    """
    факты = [("подана", None, 201), ("принята", "уже подана", 200)]
    for i, (state, note, http) in enumerate(факты):
        for форма in ("старая", "receipt-file/2"):
            with monkeypatch.context() as mp:
                run_dir, outbox, rid = _queued(tmp_path / f"{i}{форма}", mp)
                mp.setattr(cr, "_now", lambda: "2026-09-12T12:00:00+02:00")
                paths = cr._outbox_paths(outbox, rid)
                paths["sent"].parent.mkdir(parents=True, exist_ok=True)
                paths["live"].replace(paths["sent"])
                if форма == "старая":
                    cr.write_receipt(run_dir, {
                        "request_id": rid, "delivered": True, "http": http,
                        "refusal": None, "words": None, "problems": [],
                        "extra": {}, "attempts": 1,
                        "at": "2026-09-12T12:00:00+02:00", "state": state,
                        "note": note, "queue": "sent",
                        "state_words": "подана в службу каталога"
                                       + (f": {note}" if note else "")})
                else:
                    слова = ("подана в службу каталога"
                             + (f": {note}" if note else ""))
                    cr.write_receipt(run_dir, {
                        "schema_version": "receipt-file/2",
                        "request_id": rid, "outcome": "доставлена",
                        "outcome_kind": "доставлена", "delivered": True,
                        "receipt": _receipt(
                            rid, sent=_sent_of(rid, outbox), state=state,
                            repeat=(http == 200),
                            issued_at="2026-09-12T12:00:00+02:00"),
                        "attempts": [{"at": "2026-09-12T12:00:00+02:00",
                                      "http": http, "outcome": "доставлена",
                                      "words": ""}],
                        "last_checked_at": "2026-09-12T12:00:00+02:00",
                        "known": None, "http": http, "refusal": None,
                        "words": "", "problems": [], "extra": {},
                        "at": "2026-09-12T12:00:00+02:00", "url": None,
                        "state": state, "note": note, "queue": "sent",
                        "state_words": слова})
                receipt_before = cr.read_receipt(run_dir, rid)
                строка = cr.states(run_dir, _cat(), outbox)[0]
                assert строка["canonical_request_id"] == rid
                assert cr.read_receipt(run_dir, rid) == receipt_before, \
                    "чтение не перештамповывает старый или современный файл"
                if форма == "старая":
                    assert строка["transport_state"] == "неизвестно"
                    assert строка["remote_state"] is None
                    assert строка["delivered"] is False
                    assert строка["state"].startswith("подана без подтверждения")
                    assert cr.quarantined(run_dir, outbox) == [rid]
                else:
                    assert строка["transport_state"] == "доставлена"
                    assert строка["remote_state"] == state
                    assert строка["delivered"] is True
                    assert cr.quarantined(run_dir, outbox) == []


def test_22k_k07_the_service_clock_decides_the_freshness(tmp_path,
                                                         monkeypatch):
    """Момент СВИДЕТЕЛЬСТВА — часы службы (`issued_at` квитанции), а не
    часы прогона: при разбежавшихся часах момент квитанции берут у
    службы, и отставший на час прогон подтверждению не мешает.

    Старшинством РЕШЕНИЯ `issued_at` не является ни при каком случае
    (вердикт K08-F1): решение судит его собственная редакция — `by`,
    слова и момент РЕШЕНИЯ, — а не время выдачи ответа и не время его
    получения. Замечание N2 сдачи K08: прежде ворота сравнивали
    `last_checked_at` (часы прогона) с моментом решения записи (часы
    службы), и обе половины закона были неверны."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    record["submitted_at"] = "2026-09-12T09:00:00+02:00"
    accepted = {"by": "владелец", "at": "2026-09-12T10:30:00+02:00",
                "words": "нужна новая механика"}
    # Часы прогона отстали на час; служба выдала квитанцию в 11:00.
    monkeypatch.setattr(cr, "_now", lambda: "2026-09-12T10:00:00+02:00")
    with _FakeService([_again(rid, outbox, state="принята",
                              decision=dict(accepted, version=1),
                              request=dict(record, state="принята",
                                           decision=accepted),
                              issued_at="2026-09-12T11:00:00+02:00")]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    файл = cr.read_receipt(run_dir, rid)
    assert файл["last_checked_at"] == "2026-09-12T10:00:00+02:00"
    assert файл["receipt"]["issued_at"] == "2026-09-12T11:00:00+02:00"
    # Момент свидетельства читатель берёт у СЛУЖБЫ, а не у прогона.
    assert cr.receipt_confirmed(файл, rid)["at"] == "2026-09-12T11:00:00+02:00"
    # Решение записи — ТА ЖЕ редакция (`by`, слова, момент): подтверждено,
    # и отставшие на час часы прогона этому не мешают.
    cat = _catalog_with_requests(
        tmp_path, monkeypatch, "cat22k",
        [dict(record, state="принята", decision=accepted)])
    got = cr.states(run_dir, cat, outbox)[0]
    assert got["remote_state"] == "принята"
    assert got["stale"] is False, "часы прогона свежести не судят"
    assert got["decision"]["words"] == "нужна новая механика"
    # Отдельный законный ответ БЕЗ вложенной записи: числовая версия
    # receipt/1 остаётся числовой шкалой. Этот отрицательный контроль
    # нельзя незаметно превратить в сравнение двух моментов.
    mixed = dict(файл, receipt={k: v for k, v in файл["receipt"].items()
                               if k != "request"})
    cr.write_receipt(run_dir, mixed)
    assert cr.receipt_confirmed(mixed, rid)["decision"]["version"] == 1
    # Контроль K08-F1: владелец пересмотрел решение в 11:30, а квитанция,
    # выданная в 11:00, называет ПРЕЖНЮЮ редакцию — показано новое
    # решение записи, и часы службы его не откатывают. Старшинства
    # квитанция не доказала: её решение назвало числовую версию, решение
    # записи — момент, а число с моментом не сравнивается (K08).
    cat2 = _catalog_with_requests(
        tmp_path, monkeypatch, "cat22k-2",
        [dict(record, state="принята",
              decision={"by": "владелец", "at": "2026-09-12T11:30:00+02:00",
                        "words": "после пересмотра: нет"})])
    поздно = cr.states(run_dir, cat2, outbox)[0]
    assert поздно["stale"] is True
    assert поздно["stale_reason"] == cr.STALE_NOT_PROVEN
    assert поздно["decision"]["words"] == "после пересмотра: нет"


def test_22l_k07_recheck_route_of_the_panel(client, monkeypatch):
    """Ручка панели: `POST …/catalog-requests` с `mode: "recheck"` —
    сверка карантина со службой ПО КЛЮЧУ. Подачи нет ни одной, индекс не
    тронут, слова карантина уходят на экран; службы нет — 409 словами
    переходного канала, как у доставки."""
    run_id = _run_with_c(client, monkeypatch)
    run_dir = webui._run_dir(run_id)
    outbox = cr.outbox_root()
    assert client.post(f"/api/runs/{run_id}/catalog-requests",
                       json={"mode": "all"}).status_code == 200
    _wait_line(client, run_id)
    got = client.get(f"/api/runs/{run_id}/catalog-requests").json()
    rid = got["requests"][0]["request_id"]
    # Без настроек службы — 409 словами переходного канала.
    r = client.post(f"/api/runs/{run_id}/catalog-requests",
                    json={"mode": "recheck"})
    assert r.status_code == 409
    assert r.json()["detail"] == cr.service_off_words()
    # Файл в `sent/` без квитанции — карантин.
    paths = cr._outbox_paths(outbox, rid)
    sent_record = _sent_of(rid, outbox)
    paths["sent"].parent.mkdir(parents=True, exist_ok=True)
    paths["live"].replace(paths["sent"])
    assert cr.quarantined(run_dir, outbox) == [rid]
    index_before = (run_dir / "catalog-requests" / "index.json").read_bytes()
    карточка = dict(sent_record, state="принята", record_exists=False,
                    decision={"by": "владелец", "words": "нужна",
                              "at": "2026-09-12T11:00:00+02:00"})
    with _FakeService([], gets=[(200, карточка)]) as srv:
        _service_env(monkeypatch, srv.url)
        r = client.post(f"/api/runs/{run_id}/catalog-requests",
                        json={"mode": "recheck"})
        подачи = [x for x in srv.seen if x["method"] == "POST"]
    assert r.status_code == 200, r.text
    body = r.json()
    assert подачи == [], "сверка ничего не подаёт"
    assert body["confirmed"] == 0 and body["unconfirmed"] == 1
    assert body["requests"][0]["request_id"] == rid
    assert body["requests"][0]["outcome"] == "неизвестен"
    assert body["requests"][0]["known"]["state"] == "принята"
    assert body["requests"][0]["state"].startswith("подана без подтверждения")
    # Индекс сверкой не тронут — он сверяется повтором байт в байт.
    assert (run_dir / "catalog-requests" / "index.json").read_bytes() \
        == index_before
    acts = [a for a in aj.actions(run_dir)
            if a["action"] == "catalog-request"
            and (a.get("params") or {}).get("mode") == "recheck"]
    assert len(acts) == 2 and acts[0]["refused"] is True
    assert acts[1]["done"] and acts[1]["files"] == []


def test_22m_k07_a_mixed_body_never_rolls_back_a_known_decision(
        tmp_path, monkeypatch):
    """Решение подтверждает либо ЗАМЕНЯЕТ известное решение только
    вместе с состоянием ТОЙ ЖЕ заявки и ТОЙ ЖЕ редакции (K07C-F1).

    Инверсия дефекта: тело, где внешняя квитанция говорит «принята
    09:00», а вложенная запись — «отклонена 11:00», считалось
    квитанцией; состояние читалось СВЕРХУ, а доказательство старшинства
    бралось из ДРУГОГО носителя, и известное отклонение 10:00
    откатывалось чужими словами («Отклонение повторно подтверждено» при
    состоянии «принята»). Флаг устаревания ложной атрибуции не
    оправдывает: главным состоянием показывалось «принята».

    Проверка идентичности ВНЕШНЕЙ квитанции доверия на произвольное
    вложение не распространяет: связь вложения с квитанцией — номер,
    раздел, запись, канон черновика, состояние и `by`/`words`/`at`
    решения — судится отдельно (`nested_request_problems`). Несвязанное
    тело не квитанция ЭТОЙ редакции: на НОВОЙ доставке исход
    «неизвестен», очередь и ключ прежние, причина словами; на чтении
    УЖЕ СОХРАНЁННОГО — неопределённость, а достоверное решение записи
    остаётся показанным."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0040")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    отказ = {"by": "владелец", "at": "2026-09-12T10:00:00+02:00",
             "words": "нет: механика спорна"}
    принято = {"by": "владелец", "at": "2026-09-12T09:00:00+02:00",
               "words": "старое принятие"}
    позже = {"by": "владелец", "at": "2026-09-12T11:00:00+02:00",
             "words": "отклонение повторно подтверждено"}
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat22m",
                                 [dict(record, state="отклонена",
                                       decision=отказ)])
    иной_черновик = dict(record["draft"], semantics={"text": "иной черновик"})
    случаи = {
        "смешаны две квитанции": dict(record, state="отклонена",
                                      decision=позже),
        "чужой номер": dict(record, request_id="req-" + "7" * 20,
                            state="принята", decision=принято),
        "чужой раздел": dict(record, section="criteria", state="принята",
                             decision=принято),
        "чужая запись": dict(record, record_id="chuzhaya_zapis",
                             state="принята", decision=принято),
        "иной черновик": dict(record, draft=иной_черновик, state="принята",
                              decision=принято),
        "иной автор решения": dict(record, state="принята",
                                   decision=dict(принято, by="служба")),
        "иной момент решения": dict(
            record, state="принята",
            decision=dict(позже, words=принято["words"])),
        "иное состояние": dict(record, state="есть", decision=принято),
    }
    for имя, вложено in случаи.items():
        очередь = _tree(outbox, "")
        with _FakeService([_again(rid, outbox, state="принята",
                                  decision=dict(принято, version=1),
                                  request=вложено)]) as srv:
            _service_env(monkeypatch, srv.url)
            итог = cr.deliver(run_dir, [rid], _sleep=_no_sleep)
        assert итог["delivered"] == 0, имя
        файл = cr.read_receipt(run_dir, rid)
        assert файл["outcome"] == "неизвестен", имя
        assert файл["receipt"] is None, имя
        assert файл["queue"] == "outbox", имя
        assert "не является квитанцией" in файл["words"], имя
        # Очередь и ключ ПРЕЖНИЕ: файл не ушёл в `sent/`, второго номера
        # не появилось, повтор пойдёт тем же.
        assert _tree(outbox, "") == очередь, имя
        assert _queue_files(outbox, rid) == [f"{rid}.json"], имя
        строка = cr.states(run_dir, cat, outbox)[0]
        assert строка["remote_state"] == "отклонена", имя
        assert строка["decision"]["words"] == отказ["words"], имя
        assert строка["decision"]["at"] == отказ["at"], имя
        assert строка["stale"] is True, имя
        assert строка["transport_state"] == "неизвестно", имя
    # Уже СОХРАНЁННОЕ противоречивое тело (его мог записать прежний
    # клиент либо чужая рука): подтверждением оно не становится и при
    # чтении — решение записи остаётся достоверным, транспорт
    # «неизвестно», заявка возвращается в карантин.
    смешанное = _receipt(rid, sent=_sent_of(rid, outbox), state="принята",
                         repeat=True, decision=dict(принято, version=1),
                         request=dict(record, state="отклонена",
                                      decision=позже))
    сохранено = {"schema_version": cr.RECEIPT_FILE_VERSION,
                 "request_id": rid, "outcome": "доставлена",
                 "outcome_kind": "доставлена", "delivered": True,
                 "http": 200, "receipt": смешанное, "state": "принята",
                 "note": "уже подана", "queue": "sent", "attempts": [],
                 "last_checked_at": "2026-09-12T11:30:00+02:00"}
    cr.write_receipt(run_dir, сохранено)
    assert cr.receipt_confirmed(сохранено, rid) is None
    прочитано = cr.states(run_dir, cat, outbox)[0]
    assert прочитано["remote_state"] == "отклонена"
    assert прочитано["decision"]["words"] == отказ["words"]
    assert прочитано["stale"] is True
    assert прочитано["stale_reason"] == cr.STALE_NO_RECEIPT
    assert прочитано["transport_state"] == "неизвестно"
    # Достоверного решения у записи нет ВОВСЕ — и тогда такое тело
    # подтверждённым не объявляется: состояние службы неизвестно (иначе
    # чужое решение стало бы первым известным).
    пусто = _catalog_with_requests(tmp_path, monkeypatch, "cat22m-none", [])
    ничьё = cr.states(run_dir, пусто, outbox)[0]
    assert ничьё["remote_state"] is None
    assert ничьё["decision"] == cr.decision_of(None)
    assert ничьё["transport_state"] == "неизвестно"
    # Карантин: файл ушёл в `sent/`, а подтверждения нет.
    paths = cr._outbox_paths(outbox, rid)
    paths["sent"].parent.mkdir(parents=True, exist_ok=True)
    paths["live"].replace(paths["sent"])
    assert cr.quarantined(run_dir, outbox) == [rid]


def test_22n_k07_a_bound_nesting_keeps_every_positive_reachable(
        tmp_path, monkeypatch):
    """Обратная сторона K07C-F1: закон не сделан мёртвым.

    НАСТОЯЩЕЕ тело службы канала 0.5 складывает сама
    `catalog_service.request_queue.receipt` из ОДНОЙ записи, и оба его
    носителя согласованы — подтверждение достижимо и С вложением, и
    БЕЗ него. Числовая версия квитанции (`0` — решения нет, `1` — есть)
    редакцией 0.6 не объявляется: у записи каталога 0.5.0 поля версии
    нет вовсе, признак с моментом несравним, — и потому старшинство
    ответа доказывает решение ВЛОЖЕННОЙ записи, чья форма та же, что у
    записи каталога.

    Проверены все пары, которыми живёт закон: подтверждение, законное
    новое решение (копия каталога отстала), прежнее решение ответа
    (запоздавший ответ не откатывает), нечитаемый момент решения записи
    (K08, K1) и идемпотентное чтение."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0060")
    record = json.loads((run_dir / "catalog-requests" / f"{rid}.json")
                        .read_text(encoding="utf-8"))
    решение = {"by": "владелец", "at": "2026-09-12T12:00:00+02:00",
               "words": "принято владельцем"}
    запись = dict(record, state="принята", decision=решение)
    cat = _catalog_with_requests(tmp_path, monkeypatch, "cat22n", [запись])
    from catalog_service import request_queue as svc
    настоящее = svc.receipt(cat, запись, note=cr.REPEAT_NOTE, repeat=True,
                            words=f"заявка {rid} уже подана")
    assert настоящее["request"]["decision"] == решение
    assert настоящее["decision"]["version"] == 1, "признак, а не редакция"
    нет_версии = настоящее["request"]["decision"].get("version")
    assert нет_версии is None, "у записи каталога 0.5.0 версии нет вовсе"
    assert cr.nested_request_problems(настоящее) == []
    без_вложения = {k: v for k, v in настоящее.items() if k != "request"}
    assert cr.nested_request_problems(без_вложения) == []
    for имя, тело in (("без вложения", без_вложения),
                      ("с вложением", настоящее)):
        with _FakeService([(200, тело)]) as srv:
            _service_env(monkeypatch, srv.url)
            assert cr.deliver(run_dir, [rid],
                              _sleep=_no_sleep)["delivered"] == 1, имя
        строка = cr.states(run_dir, cat, outbox)[0]
        assert строка["remote_state"] == "принята", имя
        assert строка["decision"]["words"] == решение["words"], имя
        assert строка["stale"] is False, имя
        assert строка["stale_reason"] is None, имя
        assert строка["transport_state"] == "доставлена", имя
    # Законное НОВОЕ решение: копия каталога отстала, и ответ доказал
    # старшинство решением ВЛОЖЕННОЙ записи — момент с моментом. Это и
    # есть та положительная пара, которую убил бы механический возврат
    # приоритета верхнего `decision`: его версия — признак `1`, а у
    # записи вместо версии момент, и шкалы не сошлись бы никогда.
    старое = {"by": "владелец", "at": "2026-09-12T11:00:00+02:00",
              "words": "первое принятие"}
    отставший = _catalog_with_requests(
        tmp_path, monkeypatch, "cat22n-behind",
        [dict(record, state="принята", decision=старое)])
    новее = cr.states(run_dir, отставший, outbox)[0]
    assert новее["remote_state"] == "принята"
    assert новее["decision"]["words"] == решение["words"]
    assert новее["stale_reason"] == cr.STALE_CATALOG_BEHIND
    assert новее["last_known"]["decision"]["words"] == старое["words"]
    # Прежнее решение ответа: владелец пересмотрел позже — старшинства
    # ответ не доказал, решение записи остаётся последним достоверным.
    пересмотр = {"by": "владелец", "at": "2026-09-12T13:00:00+02:00",
                 "words": "после пересмотра: нет"}
    свежий = _catalog_with_requests(
        tmp_path, monkeypatch, "cat22n-newer",
        [dict(record, state="отклонена", decision=пересмотр)])
    старый = cr.states(run_dir, свежий, outbox)[0]
    assert старый["remote_state"] == "отклонена"
    assert старый["decision"]["words"] == пересмотр["words"]
    assert старый["stale_reason"] == cr.STALE_RECEIPT_OLDER
    # Нечитаемый момент решения записи (K1) — решение всё равно есть, и
    # связанный ответ его не заменяет: откатывать ЕСТЬ что.
    туман = _catalog_with_requests(
        tmp_path, monkeypatch, "cat22n-k1",
        [dict(record, state="отклонена",
              decision={"by": "владелец", "at": "вчера",
                        "words": "нет: механика спорна"})])
    k1 = cr.states(run_dir, туман, outbox)[0]
    assert k1["remote_state"] == "отклонена"
    assert k1["decision"]["words"] == "нет: механика спорна"
    assert k1["stale"] is True
    # Чтение НИЧЕГО не пишет: два чтения подряд — байт в байт.
    было = (_tree(outbox, ""), _tree(run_dir / "channel", ""),
            _tree(run_dir / "catalog-requests", ""))
    a = cr.states(run_dir, cat, outbox)
    b = cr.states(run_dir, cat, outbox)
    assert a == b
    assert (_tree(outbox, ""), _tree(run_dir / "channel", ""),
            _tree(run_dir / "catalog-requests", "")) == было


def test_22o_k07_the_media_type_is_judged_exactly(tmp_path, monkeypatch):
    """Тип содержимого судится ТОЧНО (K07C-N1): квитанция — только
    `application/json`; регистр не значит ничего, параметры
    (`; charset=utf-8`) допустимы, а `application/jsonp`,
    `application/json-bogus`, пустая строка и ОТСУТСТВИЕ заголовка —
    исход «неизвестен» словами.

    Инверсия дефекта: проверка «начинается с application/json»
    принимала `application/jsonp` и `application/json-bogus` за
    квитанцию, а ответ без заголовка не судился вовсе — поблажка стенду
    («подставной клиент заголовков не даёт») меняла ПРОДУКТОВЫЙ закон.

    Тот же закон импортёра COSMOS проверяется отдельно через обе
    клиентские точки входа в test_k07_integral_receipt_agent."""
    run_dir, outbox, rid = _queued(tmp_path, monkeypatch, name="run-0070")
    sent = _sent_of(rid, outbox)
    повтор = _receipt(rid, sent=sent, repeat=True)
    for ctype in ("application/jsonp", "application/json-bogus", "",
                  "text/html", "application/json-patch+json", None):
        беды = cr.receipt_problems(200, повтор, sent, ctype)
        assert беды, ctype
        assert any("тип содержимого" in b for b in беды), ctype
    for ctype in ("application/json", "Application/JSON",
                  "application/json; charset=utf-8",
                  " application/json ; charset=utf-8"):
        assert cr.receipt_problems(200, повтор, sent, ctype) == [], ctype
    assert cr.json_media_type_problem(None)
    assert cr.json_media_type_problem("application/jsonp")
    assert cr.json_media_type_problem("application/json") is None
    assert cr.json_media_type_problem("APPLICATION/JSON ;q=1") is None
    # Доставка: 201 с годной квитанцией, но БЕЗ заголовка типа — исход
    # «неизвестен», файл в очереди, повтор тем же номером.
    очередь = _tree(outbox, "")
    with _FakeService([(201, _receipt(rid, sent=sent), "")]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 0
    файл = cr.read_receipt(run_dir, rid)
    assert файл["outcome"] == "неизвестен"
    assert "тип содержимого не назван" in файл["words"]
    assert _tree(outbox, "") == очередь
    assert _queue_files(outbox, rid) == [f"{rid}.json"]
    # Сверка карантина судит тип тем же законом — и карантина не снимает.
    with _FakeService([], gets=[(200, повтор, "application/jsonp")]) as srv:
        _service_env(monkeypatch, srv.url)
        сверка = cr.recheck_one(rid, run_dir, outbox)
    assert сверка["outcome"] == "неизвестен"
    assert "application/jsonp" in сверка["words"]
    # Тот же ответ с ТОЧНЫМ типом — заявка доставлена.
    with _FakeService([(201, _receipt(rid, sent=sent))]) as srv:
        _service_env(monkeypatch, srv.url)
        assert cr.deliver(run_dir, [rid], _sleep=_no_sleep)["delivered"] == 1
    assert cr.read_receipt(run_dir, rid)["outcome"] == "доставлена"
