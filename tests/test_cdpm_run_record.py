# -*- coding: utf-8 -*-
"""Запись о прогоне — половина CDPM (шаги 1 и 2, слово владельца
10.09.2026 «Начинай»): сводка токенов по ролям и моделям из журнала
вызовов, ревизии книг ответов с отпечатками, сборка run-record.json по
словарю COSMOS (`cosmos-run-record/v1`) и номер записи в манифесте
пакета; гейт «Новый прогон» с объявлением ДО старта, голова прогона,
хранитель записи (ручки `record` и `record/append`), переходное правило
«два летописца» и `record_api` в манифесте (проект, раздел 11)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cdpm import run_record as rr


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _run(tmp_path):
    import zipfile

    run = tmp_path / "run-0042"
    _write(run / "status.json", {
        "state": "ответы применены", "data_state": "отчёт готов",
        "customer": "Полярис", "data_class": "synthetic", "scope": "any",
        "created_at": "2026-09-10T07:00:00+02:00",
        "files": ["ID-1.docx"], "data_files": ["DATA.xlsx"],
        "freeze_counters": {"files": 1, "clauses": 30},
        "extract_counters": {"rules": 17, "questions": 0},
        "extract_problems": ["одна"],
        "answers_counters": {"questions": 1, "applied": 1},
        "stage_channels": {
            "extractor/cloud": {"stage": "извлечение", "config_source": "run-explicit",
                                "effective": {"channel": "api", "model": "m-a",
                                              "provider": "cloud"}}},
        "passport": {"at": "2026-09-10T07:00:01+02:00",
                     "environment": {"code": {"commit": "abc", "short": "abc"},
                                     "spec_version": "1.18", "python": "3.14.4",
                                     "packages": {"ortools": "9.15"},
                                     "catalog": {"version": "0.4.0",
                                                 "fingerprint": "ca69",
                                                 "dictionary_order": "catalog"}},
                     "replay": {"mode": "replay-fresh", "source_run": "run-0007"}},
    })
    _write(run / "freeze" / "manifest.json",
           {"files": [{"sid": "S1", "file": "ID-1.docx", "sha256": "f" * 64,
                       "clause_count": 30}]})
    idx = [
        {"n": 1, "action_n": 1, "role": "extractor", "stage": "извлечение",
         "elapsed_ms": 100, "identity": {"provider": "cloud", "model": "m-a"},
         "error": None,
         "usage": {"provider": "cloud", "model": "m-a", "input_tokens": 100,
                   "output_tokens": 20, "cache_read_tokens": 50,
                   "cache_write_tokens": None, "thinking_tokens": None,
                   "counted": True, "reason": None}},
        {"n": 2, "action_n": 1, "role": "extractor", "stage": "извлечение",
         "elapsed_ms": 50, "identity": {"provider": "cloud", "model": "m-a"},
         "error": None,
         "usage": {"provider": "cloud", "model": "m-a", "input_tokens": 10,
                   "output_tokens": 5, "counted": True}},
        {"n": 3, "action_n": 5, "role": "auditor", "stage": "цикл",
         "elapsed_ms": 70, "identity": {"provider": "cloud", "model": "(claude-cli)"},
         "error": None,
         "usage": {"provider": "cloud", "model": "(claude-cli)",
                   "counted": False, "reason": "cli"}},
        {"n": 4, "action_n": 5, "role": "vision", "stage": "заморозка",
         "elapsed_ms": 30, "identity": {"provider": "cloud", "model": "m-v"},
         "error": None, "usage": None},
        {"n": 5, "action_n": 7, "role": "extractor", "stage": "извлечение",
         "elapsed_ms": 1, "identity": {"provider": "cloud", "model": "m-a"},
         "served_from": "journal", "error": None, "usage": None},
    ]
    (run / "journal" / "calls").mkdir(parents=True)
    (run / "journal" / "calls" / "index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in idx) + "\n",
        encoding="utf-8")
    acts = [
        {"n": 1, "action": "create", "at": "2026-09-10T07:00:00+02:00",
         "kind": "start", "files": [{"name": "ID-1.docx", "sha256": "f" * 64,
                                     "bytes": 10, "stored": "src/ID-1.docx"}]},
        {"n": 5, "action": "answers", "at": "2026-09-10T07:10:00+02:00",
         "kind": "start", "files": [{"name": "book-1.xlsx", "sha256": "b" * 64}]},
        {"n": 5, "kind": "end", "at": "2026-09-10T07:10:02+02:00",
         "duration_s": 1.5, "state_after": {"state": "ответы применены"},
         "error": None},
        {"n": 7, "action": "answers", "at": "2026-09-10T07:20:00+02:00",
         "kind": "start", "files": []},
    ]
    (run / "journal" / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in acts) + "\n",
        encoding="utf-8")
    book = b"book bytes"
    (run / "answers" / "rev-002").mkdir(parents=True)
    (run / "answers" / "rev-002" / "anketa-otvety.xlsx").write_bytes(book)
    _write(run / "answers" / "rev-002" / "answers-summary.json",
           {"book_sha256": hashlib.sha256(book).hexdigest(), "applied": 1,
            "unparsed": 0})
    (run / "answers" / "rev-003").mkdir(parents=True)
    _write(run / "answers" / "rev-003" / "answers-summary.json",
           {"applied": 2, "unparsed": 0})
    package = run / "channel" / "task-package.zip"
    package.parent.mkdir()
    # Artifact-identity fixture only; this minimal ZIP does not prove gate 5.
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("fixture.txt", "run-record delivery projection")
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    _write(run / "channel" / "delivery.json",
           {"sent": True, "sha": package_sha, "verdict": "OK",
            "mismatch": [], "already_accepted": False,
            "detail": "Synthetic healthy brief for run-record projection",
            "cosmos_run": "run-0035", "at": "2026-09-10 07:30:00",
            "mg": {"pairing": {"state": "optimization"}}})
    return run


def test_models_summary_counts_tokens_per_role_model_stage(tmp_path):
    run = _run(tmp_path)
    s = rr.models_summary(run)
    assert s["unit"] == "tokens"
    # Повтор из журнала без расхода — не вызов модели.
    assert (s["calls"], s["counted_calls"], s["not_counted_calls"],
            s["unknown_calls"]) == (4, 2, 1, 1)
    rows = {(r["role"], r["model"], r["stage"]): r for r in s["models"]}
    ext = rows[("extractor", "m-a", "извлечение")]
    assert (ext["calls"], ext["input_tokens"], ext["output_tokens"],
            ext["cache_read_tokens"], ext["elapsed_ms"]) == (2, 110, 25, 50, 150)
    cli = rows[("auditor", "(claude-cli)", "цикл")]
    assert cli["not_counted_calls"] == 1 and cli["reasons"] == ["cli"]
    assert cli["input_tokens"] == 0                 # не считается ≠ ноль токенов
    vis = rows[("vision", "m-v", "заморозка")]
    assert vis["unknown_calls"] == 1 and vis["counted_calls"] == 0
    # Пустой журнал — пустая честная сводка.
    assert rr.models_summary(tmp_path / "нет")["calls"] == 0


def test_answers_revisions_carry_sha_and_source(tmp_path):
    run = _run(tmp_path)
    revs = rr.answers_revisions(run)
    assert [r["revision"] for r in revs] == [2, 3]
    # 11.7: отпечаток ревизии — по СОДЕРЖАНИЮ (пары «вопрос → ответ»),
    # а не по байтам книги. Обрывок вместо книги не читается —
    # отпечатка нет, байты остаются рядом для узнавания файла
    # (канон и живые книги — tests/test_cdpm_run_tree_panel.py).
    assert revs[0]["sha256"] is None
    assert revs[0]["book_sha256"] == hashlib.sha256(b"book bytes").hexdigest()
    assert revs[0]["source"] == "книга" and revs[0]["at"] == "2026-09-10T07:10:00+02:00"
    assert revs[0]["files"][0]["name"] == "anketa-otvety.xlsx"
    assert revs[1]["source"] == "человек" and revs[1]["sha256"] is None
    assert revs[1]["at"] == "2026-09-10T07:20:00+02:00" and revs[1]["applied"] == 2
    assert rr.answers_revisions(tmp_path / "нет") == []


def test_build_and_refresh_write_record(tmp_path):
    run = _run(tmp_path)
    package_sha = hashlib.sha256(
        (run / "channel" / "task-package.zip").read_bytes()).hexdigest()
    rec = rr.build(run)
    assert rec["schema"] == rr.RECORD_SCHEMA
    assert set(rec) == {"schema", "declaration", "declaration_sha256", "journal",
                        "results"}
    d = rec["declaration"]
    assert d["run_id"] == "run-0042" and d["created_by"] == rr.PANEL_ACTOR
    assert d["purpose"] == "рабочий" and d["contour"]["name"] == "local"
    assert d["airline"] == {"name": "Полярис", "data_class": "synthetic",
                            "llm_scope": "any"}
    doc = d["rules_documents"][0]
    assert (doc["name"], doc["kind"], doc["sha256"], doc["size"]) == \
        ("ID-1.docx", "docx", "f" * 64, 10)
    assert d["data_files"][0]["name"] == "DATA.xlsx"
    assert d["data_files"][0]["sha256"] is None
    assert d["catalog"]["version"] == "0.4.0" and d["catalog"]["fingerprint"] == "ca69"
    assert d["catalog"]["name"] == "template-catalog" and d["catalog_extension"] is None
    assert set(d["bindings"]) == {"catalog_version", "catalog_fingerprint", "path"}
    assert d["models"][0]["role"] == "extractor" and d["models"][0]["model"] == "m-a"
    assert d["models"][0]["channel"] == "api" and d["models"][0]["effort"] is None
    assert d["solvers"] == [] and d["planned_stages"] == []
    assert d["stop_after"] is None
    assert d["code"] == {"cdpm_commit": "abc", "cosmos_commit": "abc",
                         "runner_fingerprint": None}
    assert d["libraries"]["python"] == "3.14.4"
    assert d["libraries"]["packages"] == [{"name": "ortools", "version": "9.15"}]
    assert len(d["libraries"]["packages_sha256"]) == 64
    assert d["expectations"] == {"kind": None, "ref": None}
    assert [a["revision"] for a in d["answers"]] == [2, 3]
    assert d["answers"][0]["source"] == "книга набора"
    assert d["answers"][1]["source"] == "человек"
    assert d["answer_policy"] == "книга"
    lin = d["lineage"]
    assert lin["root_sha256"] == rr.root_sha256(d["rules_documents"], d["data_files"])
    # Корень с ответами у головы не ставится (ответы растут по ходу, голова
    # устойчива); читатели дерева считают его по answers[] функцией словаря.
    assert lin["root_answers_sha256"] is None
    assert len(rr.root_answers_sha256(
        d["rules_documents"], d["data_files"], d["answers"])) == 64
    # Алгоритм словаря COSMOS: sha256 канонического JSON (sort_keys, без
    # пробелов) отсортированных отпечатков; корень без ответов известен
    # до старта.
    canon = json.dumps({"rules": ["f" * 64], "data": []}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert lin["root_sha256"] == hashlib.sha256(canon).hexdigest()
    assert lin["source_run"] == "run-0007" and lin["parent_run"] == "run-0007"
    assert lin["branch_of"] == "replay:replay-fresh"
    # Пакет полного прогона — выдача (results.deliveries), не происхождение:
    # объявление не меняется после отправки пакета, и его отпечаток в
    # манифесте (снят до отправки) равен отпечатку записи после неё
    # (живая находка на run-0126..0128: расходились).
    assert lin["package_sha256"] is None
    assert len(rec["declaration_sha256"]) == 64
    before = rr.build(run)["declaration_sha256"]
    (run / "channel" / "delivery.json").unlink()
    assert rr.declaration_sha256_of(run) == before
    _write(run / "channel" / "delivery.json",
           {"sent": True, "sha": package_sha, "verdict": "OK",
            "mismatch": [], "already_accepted": False,
            "detail": "Synthetic healthy brief for run-record projection",
            "cosmos_run": "run-0035", "at": "2026-09-10 07:30:00",
            "mg": {"pairing": {"state": "optimization"}}})
    assert rr.build(run)["declaration_sha256"] == before
    # Отпечаток объявления не зависит от ревизий ответов (они растут по ходу).
    assert rr.declaration_sha256(dict(d, answers=[])) == rec["declaration_sha256"]
    # Журнал — список записей этапов по журналу действий; последнее
    # незакрытое действие (n=7) в журнал не попадает.
    j = rec["journal"]
    assert [e["substage"] for e in j] == ["create", "answers"]
    # Оборванное действие (без записи об окончании, не последнее) — сбой.
    assert j[0]["stage"] == "freeze" and j[0]["outcome"] == "сбой"
    assert j[0]["reason_code"] == "aborted"
    assert j[0]["artifacts"][0] == {"kind": "file", "path": "src/ID-1.docx",
                                    "sha256": "f" * 64}
    assert j[0]["counters"] == {"files": 1, "clauses": 30}
    # Расход действия — строки словаря resource_model по action_n.
    assert j[0]["resources"]["models"] == [
        {"role": "extractor", "model": "m-a", "provider": "cloud", "calls": 2,
         "input_tokens": 110, "output_tokens": 25, "counted": True, "note": None}]
    assert j[1]["stage"] == "answers" and j[1]["outcome"] == "успех"
    assert j[1]["counters"]["applied"] == 1
    assert j[1]["duration_machine_s"] == 1.5
    models = {m["model"]: m for m in j[1]["resources"]["models"]}
    assert models["(claude-cli)"]["counted"] is False
    assert "не считается" in models["(claude-cli)"]["note"]
    assert models["m-v"]["counted"] is False               # канал не сообщил
    r = rec["results"]
    assert r["state"] == "завершён"                        # пакет доставлен
    assert r["deliveries"] == [{"kind": "task-package",
                                "path": "channel/task-package.zip",
                                "sha256": package_sha}]
    assert r["verdicts"]["package"] == "OK" and r["verdicts"]["cosmos_run"] == "run-0035"
    assert r["comparison"] == {"expectations": {}, "previous_run": {}, "golden": {}}
    assert r["golden"] == {"confirmed": False, "by": None, "at": None, "words": None}
    assert set(r["timing"]) == {"machine_s", "waiting_human_s", "total_s"}
    seen = {}
    out = rr.refresh(run, update_status=lambda res: seen.setdefault("res", res))
    assert (run / rr.RECORD_FILE).is_file()
    again = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert again["declaration_sha256"] == out["declaration_sha256"]
    # Статус панели получает БОГАТУЮ сводку (роль × провайдер × модель × этап).
    assert seen["res"]["calls"] == 4 and seen["res"]["unit"] == "tokens"
    assert rr.record_id_of(run) == "run-0042"
    assert rr.declaration_sha256_of(run) == rec["declaration_sha256"]


def test_refresh_yields_to_foreign_record(tmp_path):
    """Два летописца одного прогона (живая находка run-0129..0131 на
    6eebd870): раннер наборов объявляет прогон ДО старта и после заморозки
    пишет свою запись в папку прогона панели, панель переписывала её после
    каждого действия — и отпечаток объявления в манифесте пакета
    (панельный, снят до выпуска) расходился с файлом (раннерским, записан
    после). Правило: файл — у того, кто объявил прогон; чужую запись
    панель не трогает, сводку токенов в статус даёт всё равно, а манифест
    несёт номер и отпечаток записи, лежащей в папке."""
    run = _run(tmp_path)
    own = rr.refresh(run)
    assert own["declaration"]["created_by"] == rr.PANEL_ACTOR
    assert rr.record_id_of(run) == "run-0042"
    foreign = {"schema": rr.RECORD_SCHEMA,
               "declaration": {"run_id": "20260910-132100-9c6f40ad",
                               "created_by": "раннер наборов H0"},
               "declaration_sha256": "e" * 64, "journal": [], "results": {}}
    _write(run / rr.RECORD_FILE, foreign)
    raw = (run / rr.RECORD_FILE).read_bytes()
    seen = {}
    out = rr.refresh(run, update_status=lambda res: seen.setdefault("res", res))
    assert (run / rr.RECORD_FILE).read_bytes() == raw          # не переписана
    assert out["declaration"]["run_id"] == "20260910-132100-9c6f40ad"
    assert seen["res"]["unit"] == "tokens" and seen["res"]["calls"] == 4
    assert rr.record_id_of(run) == "20260910-132100-9c6f40ad"
    assert rr.declaration_sha256_of(run) == "e" * 64
    # Своя запись (актор и номер панели) переписывается, как прежде.
    _write(run / rr.RECORD_FILE, own)
    assert rr.refresh(run)["declaration"]["created_by"] == rr.PANEL_ACTOR
    again = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert again["declaration_sha256"] == own["declaration_sha256"]
    # Файл не по словарю (без схемы) — не запись, а мусор: переписывается.
    _write(run / rr.RECORD_FILE, {"мусор": 1})
    rr.refresh(run)
    assert rr.record_id_of(run) == "run-0042"
    assert rr.on_disk(run)["declaration"]["created_by"] == rr.PANEL_ACTOR


def test_record_validates_against_cosmos_schema(tmp_path):
    """Словарь полей один на обе системы: запись CDPM проходит схему
    COSMOS `vmp/run_record.schema.json` (данные, не код — CDPM от vmp
    не зависит). Пока схемы в дереве нет — тест пропускается словами."""
    schema_path = Path(__file__).resolve().parents[1] / "vmp" / "run_record.schema.json"
    if not schema_path.is_file():
        pytest.skip("схема словаря COSMOS vmp/run_record.schema.json ещё не в дереве")
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    rec = rr.build(_run(tmp_path))
    problems = [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"
                for e in jsonschema.Draft202012Validator(schema).iter_errors(rec)]
    assert problems == [], problems


def test_journal_entries_pair_starts_with_ends(tmp_path):
    """Записи этапов: начало/конец/длительность из журнала действий, отказ
    по правилу и сбой — исходами со словами, ожидание человека — у
    действия `answers` от закрытия предыдущего действия."""
    run = tmp_path / "run-0050"
    _write(run / "status.json", {"state": "извлечено",
                                 "extract_counters": {"rules": 3},
                                 "extract_problems": []})
    acts = [
        {"n": 1, "action": "create", "at": "2026-09-10T07:00:00+02:00",
         "kind": "start", "files": []},
        {"n": 1, "kind": "end", "at": "2026-09-10T07:00:09+02:00", "duration_s": 8.8,
         "state_after": {"state": "заморожено"}, "error": None},
        {"n": 2, "action": "extract", "at": "2026-09-10T07:01:00+02:00",
         "kind": "start", "files": []},
        {"n": 2, "kind": "end", "at": "2026-09-10T07:03:00+02:00", "duration_s": 120.0,
         "state_after": {"state": "извлечено"}, "error": None},
        {"n": 3, "action": "answers", "at": "2026-09-10T07:10:00+02:00",
         "kind": "start", "files": []},
        {"n": 3, "kind": "refused", "at": "2026-09-10T07:10:01+02:00", "status": 409,
         "detail": "прогон занят"},
        {"n": 4, "action": "full-cycle", "at": "2026-09-10T07:11:00+02:00",
         "kind": "start", "files": []},
        {"n": 4, "kind": "end", "at": "2026-09-10T07:12:00+02:00", "duration_s": 60.0,
         "state_after": {"state": "сбой"}, "error": "RuntimeError('x')"},
    ]
    (run / "journal").mkdir(parents=True)
    (run / "journal" / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in acts) + "\n",
        encoding="utf-8")
    (run / "journal" / "calls").mkdir()
    (run / "journal" / "calls" / "index.jsonl").write_text(json.dumps(
        {"n": 1, "action_n": 2, "role": "extractor", "stage": "извлечение",
         "usage": {"provider": "cloud", "model": "m", "input_tokens": 7,
                   "output_tokens": 3, "counted": True}}) + "\n", encoding="utf-8")
    entries = rr.journal_entries(run)
    by = {e["substage"]: e for e in entries}
    assert by["create"]["outcome"] == "успех" and by["create"]["duration_machine_s"] == 8.8
    assert by["extract"]["outcome"] == "успех" and by["extract"]["counters"] == {"rules": 3}
    assert by["extract"]["resources"] == {
        "models": [{"role": "extractor", "model": "m", "provider": "cloud", "calls": 1,
                    "input_tokens": 7, "output_tokens": 3, "counted": True,
                    "note": None}],
        "solver_seconds": None}
    assert by["answers"]["outcome"] == "отказ по правилу"
    assert by["answers"]["reason_code"] == "409"
    assert by["answers"]["duration_waiting_human_s"] == 420.0     # 07:03:00 → 07:10:00
    assert by["full-cycle"]["outcome"] == "сбой" and "RuntimeError" in by["full-cycle"]["words"]
    timing = rr._timing(entries)
    assert timing["machine_s"] == 188.8 and timing["waiting_human_s"] == 420.0
    assert timing["total_s"] == 608.8
    assert rr._record_state({"state": "сбой"}, entries, False) == "сбой"
    assert rr._record_state({"state": "извлечено"}, entries[:2], False) == "идёт"
    assert rr._record_state({"state": "извлечено"}, [], False) == "объявлен"


def test_panel_writes_record_after_each_action(tmp_path, monkeypatch,
                                               declared_test_layout):
    """Панель: после закрытого действия (создание прогона) в папке прогона
    лежит run-record.json по словарю, а статус несёт блок resources
    (токены по ролям и моделям); голова записи — из паспорта Р1–Р3 и
    заморозки."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    from test_cdpm_extract import _RULES_A, _RULES_B
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "cdpm-settings.json"))
    client = TestClient(webui.app)
    resp = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
    ], params={"data_class": "synthetic"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]
    run = tmp_path / "runs" / run_id
    rec = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert rec["schema"] == rr.RECORD_SCHEMA
    d = rec["declaration"]
    assert d["run_id"] == run_id and rec["declaration_sha256"]
    # Голова хранителя — в ответе рождения: отправитель append берёт
    # отпечаток отсюда, а не считает свой (живая находка run-0135, 21:3x).
    assert resp.json()["record"] == {"run_id": run_id,
                                     "declaration_sha256": rec["declaration_sha256"]}
    assert [f["name"] for f in d["rules_documents"]] == ["RULES-A.md", "RULES-B.md"]
    assert all(f["kind"] == "md" and f["size"] for f in d["rules_documents"])
    assert all(len(f["sha256"] or "") == 64 for f in d["rules_documents"])
    assert d["catalog"]["version"] and d["catalog"]["fingerprint"]
    assert d["code"]["cdpm_commit"] and d["libraries"]["python"]
    assert [e["substage"] for e in rec["journal"]] == ["create"]
    assert rec["journal"][0]["outcome"] in ("успех", "предупреждение")
    assert rec["results"]["state"] == "идёт"
    status = client.get(f"/api/runs/{run_id}").json()
    assert status["resources"]["unit"] == "tokens"
    assert "models" in status["resources"]


# ══════════════════ шаг 2: гейт «Новый прогон», голова, хранитель ═════

@pytest.fixture()
def panel(tmp_path, monkeypatch, declared_test_layout):
    """Панель на тестовом клиенте: каталог прогонов и настройки в tmp,
    служба каталога — заведомо недоступный адрес (среда машины в тесты
    не течёт), раскладка ролей объявлена явно (S06-R2)."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    from test_cdpm_extract import fake_llm
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setenv("CREW_CATALOG_URL", "http://catalog.invalid")
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    client = TestClient(webui.app)
    client.runs = tmp_path / "runs"
    return client


def _create(panel, **params):
    from test_cdpm_extract import _RULES_A, _RULES_B
    p = {"data_class": "synthetic", "customer": "Полярис (тест)"}
    p.update(params)
    return panel.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
    ], params=p)


def _entry(stage="pairing", substage="attempt-1", outcome="успех", **kw):
    """Запись журнала по словарю `journal_entry` — так их шлёт COSMOS."""
    entry = {"stage": stage, "substage": substage,
             "started_at": "2026-09-10T18:00:00+02:00",
             "finished_at": "2026-09-10T18:05:00+02:00",
             "duration_machine_s": 300.0, "duration_waiting_human_s": None,
             "outcome": outcome, "reason_code": None,
             "words": "пейринг посчитан", "actor": "cosmos",
             "artifacts": [{"kind": "solution", "path": "runs/x.json",
                            "sha256": "a" * 64}],
             "counters": {"chains": 12},
             "resources": {"models": [], "solver_seconds": 42.0}}
    entry.update(kw)
    return entry


def _append_body(head, entries, **kw):
    body = {"schema": rr.APPEND_SCHEMA, "record_id": head["declaration"]["run_id"],
            "declaration_sha256": head["declaration_sha256"],
            "system": "cosmos", "entries": entries}
    body.update(kw)
    return body


def test_declaration_written_at_birth_before_freeze(panel, monkeypatch):
    """Голова прогона — ДО заморозки (11.1): к первому вызову заморозки
    `declaration.json` уже на диске, запись в состоянии «объявлен», а
    отпечаток объявления не меняется на всём пути прогона."""
    from cdpm import freeze as freeze_mod
    from cdpm import webui
    seen = {}
    real = freeze_mod.freeze

    def spy(saved, out, vision_llm=None):
        run = Path(out).parent
        seen["declaration"] = json.loads(
            (run / rr.DECLARATION_FILE).read_text(encoding="utf-8"))
        seen["record"] = json.loads(
            (run / rr.RECORD_FILE).read_text(encoding="utf-8"))
        return real(saved, out, vision_llm=vision_llm)
    monkeypatch.setattr(webui.freeze, "freeze", spy)
    got = _create(panel, purpose="приёмка", created_by="владелец",
                  answer_policy="книга", stop_after="cycle",
                  planned_stages="create,extract,cycle",
                  expectations_kind="манифест набора",
                  expectations_ref="kits/H0/expected.json")
    assert got.status_code == 200, got.text
    run_id = got.json()["run"]
    run = panel.runs / run_id
    # Голова была на диске ДО заморозки — и она объявлена, а не собрана
    # по следам: документы правил названы отпечатками байтов загрузки.
    d = seen["declaration"]["declaration"]
    assert d["run_id"] == run_id and d["created_by"] == "владелец"
    assert d["purpose"] == "приёмка" and d["answer_policy"] == "книга"
    assert d["planned_stages"] == ["create", "extract", "cycle"]
    assert d["stop_after"] == "cycle"
    assert d["expectations"] == {"kind": "манифест набора",
                                 "ref": "kits/H0/expected.json"}
    assert [f["name"] for f in d["rules_documents"]] == ["RULES-A.md",
                                                         "RULES-B.md"]
    assert all(len(f["sha256"]) == 64 and f["size"] for f in d["rules_documents"])
    assert d["catalog"]["version"] and d["catalog"]["fingerprint"]
    assert d["code"]["cdpm_commit"] and d["libraries"]["python"]
    assert d["models"] and all(m["role"] and m["channel"] for m in d["models"])
    assert d["airline"] == {"name": "Полярис (тест)",
                            "data_class": "synthetic", "llm_scope": "any"}
    assert d["lineage"]["root_sha256"] == rr.root_sha256(d["rules_documents"], [])
    assert seen["record"]["results"]["state"] == "объявлен"
    assert seen["record"]["journal"] == []
    # Отпечаток стоит: он же в записи после заморозки, у ручки и в манифесте.
    sha = seen["declaration"]["declaration_sha256"]
    assert len(sha) == 64
    assert rr.build(run)["declaration_sha256"] == sha
    assert rr.declaration_sha256_of(run) == sha
    rec = panel.get(f"/api/runs/{run_id}/record").json()
    assert rec["declaration_sha256"] == sha
    assert rec["declaration"]["created_by"] == "владелец"
    # Голова НЕ реконструируется: правку файла запись отдаёт как есть.
    head = json.loads((run / rr.DECLARATION_FILE).read_text(encoding="utf-8"))
    head["declaration"]["purpose"] = "демонстрация"
    _write(run / rr.DECLARATION_FILE, head)
    assert rr.build(run)["declaration"]["purpose"] == "демонстрация"
    # Актор записей журнала панели — кто объявил прогон (11.5).
    assert all(e["actor"] == "владелец" for e in rr.build(run)["journal"])


def test_answers_grow_the_head_but_not_its_fingerprint(panel):
    """Растут только ревизии ответов и производный от них корень дерева:
    отпечаток объявления от них не зависит — иначе он расходился бы с
    отпечатком в манифесте пакета, снятым до этапа ответов."""
    run_id = _create(panel).json()["run"]
    run = panel.runs / run_id
    before = rr.build(run)["declaration_sha256"]
    book = b"book bytes"
    (run / "answers" / "rev-001").mkdir(parents=True)
    (run / "answers" / "rev-001" / "anketa-otvety.xlsx").write_bytes(book)
    _write(run / "answers" / "rev-001" / "answers-summary.json",
           {"book_sha256": hashlib.sha256(book).hexdigest(), "applied": 1})
    rec = rr.build(run)
    d = rec["declaration"]
    assert [a["revision"] for a in d["answers"]] == [1]
    # Голова объявлена до ответов: корень с ответами в ней остаётся null
    # (отпечаток словаря исключает только answers — ничего сверх).
    assert d["lineage"]["root_answers_sha256"] is None
    assert rec["declaration_sha256"] == before
    assert rr.declaration_sha256(dict(d, answers=[])) == before


def test_gate_refuses_before_the_run_is_born(panel, tmp_path, monkeypatch,
                                             declared_test_layout):
    """Отказ гейта — ДО рождения папки (11.1): ни прогона, ни
    израсходованного номера. Четыре повода: слово вне словаря,
    раскладка «blocked» при допуске, разрешающем облако, каталог
    шаблонов не объявим, привязки продукта CDPM не читаются."""
    from cdpm import run_passport as passport_mod
    from cdpm import webui
    runs = panel.runs

    def _no_traces():
        assert list(runs.glob("run-*")) == [], "папка прогона не рождается"
        assert not (runs / "counter.json").is_file(), "номер не расходуется"

    got = _create(panel, purpose="срочно")
    assert got.status_code == 400, got.text
    assert "прогон не объявлен" in got.json()["detail"]
    assert "цель прогона" in got.json()["detail"]
    _no_traces()

    got = _create(panel, answer_policy="как получится")
    assert got.status_code == 400 and "политика ответов" in got.json()["detail"]
    got = _create(panel, planned_stages="create,полёт")
    assert got.status_code == 400 and "этапы плана" in got.json()["detail"]
    got = _create(panel, stop_after="never")
    assert got.status_code == 400 and "остановка после" in got.json()["detail"]
    _no_traces()

    # Раскладка моделей не объявлена, а допуск разрешает облако.
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    got = _create(panel)
    assert got.status_code == 400, got.text
    assert "раскладка моделей не объявлена" in got.json()["detail"]
    _no_traces()
    # Тот же контур с допуском «только локально» гейт пропускает:
    # раскладка судится там, где облако разрешено.
    got = _create(panel, data_class="")
    assert got.status_code == 200, got.text
    assert (runs / got.json()["run"] / rr.DECLARATION_FILE).is_file()

    # Раскладка снова объявлена — дальше судится только среда.
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", str(declared_test_layout))

    # Каталог шаблонов не объявим (версии и отпечатка нет).
    real_env = passport_mod.environment
    env = dict(real_env())
    monkeypatch.setattr(webui.passport_mod, "environment",
                        lambda: {**env, "catalog": {
                            "version": None, "fingerprint": None,
                            "error": "каталога нет на площадке"}})
    got = _create(panel)
    assert got.status_code == 400, got.text
    assert "каталог шаблонов не объявим" in got.json()["detail"]
    assert "каталога нет на площадке" in got.json()["detail"]
    assert "раскладка моделей" not in got.json()["detail"]
    monkeypatch.setattr(webui.passport_mod, "environment", real_env)

    # Привязки продукта CDPM не читаются.
    monkeypatch.setenv("CDPM_BINDINGS_FILE", str(tmp_path / "нет-привязок.json"))
    got = _create(panel)
    assert got.status_code == 400, got.text
    assert "привязки CDPM не читаются" in got.json()["detail"]


def test_runner_declares_the_run_panel_keeps_the_file(panel):
    """Объявление раннером (11.1, 11.3): раннер передаёт свой номер
    записи и актора — голова его, а файл записи ведёт панель-ХРАНИТЕЛЬ
    (второй записи в папке не заводится)."""
    rid = "20260910-132100-9c6f40ad"
    got = _create(panel, record_run_id=rid, created_by="раннер наборов H0",
                  purpose="регресс", answer_policy="книга",
                  expectations_kind="манифест набора",
                  expectations_ref="kits/H0/manifest.json")
    assert got.status_code == 200, got.text
    run_id = got.json()["run"]
    run = panel.runs / run_id
    d = json.loads((run / rr.DECLARATION_FILE).read_text(
        encoding="utf-8"))["declaration"]
    assert d["run_id"] == rid and d["created_by"] == "раннер наборов H0"
    assert d["purpose"] == "регресс" and d["answer_policy"] == "книга"
    # Хозяин головы чужой, а хранитель файла — панель: запись в папке
    # своя (чужой её не считает) и переписывается после каждого действия.
    assert rr.foreign_record(run) is None
    file_rec = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert file_rec["declaration"]["run_id"] == rid
    assert rr.record_id_of(run) == rid
    assert rr.declaration_sha256_of(run) == file_rec["declaration_sha256"]
    rr.refresh(run)
    assert json.loads((run / rr.RECORD_FILE).read_text(
        encoding="utf-8"))["declaration"]["run_id"] == rid
    # Актор записей журнала панели — раннер: честно по объявлению.
    assert all(e["actor"] == "раннер наборов H0"
               for e in rr.build(run)["journal"])


def test_append_accepts_results_without_entries(panel):
    """Итоги (вердикты, выдачи) могут ехать без записей этапов — тело
    пустое только без того и другого (просьба COSMOS 22:0x: итоги после
    нажатий шли бы вместе с последней записью, а это лишняя сцепка)."""
    run_id = _create(panel).json()["run"]
    rec = panel.get(f"/api/runs/{run_id}/record").json()
    body = {"schema": rr.APPEND_SCHEMA, "record_id": rec["declaration"]["run_id"],
            "declaration_sha256": rec["declaration_sha256"], "system": "cosmos",
            "entries": [], "results": {"verdicts": {"pairing": "построен"}}}
    got = panel.post(f"/api/runs/{run_id}/record/append", json=body)
    assert got.status_code == 200, got.text
    assert got.json()["accepted"] == 0
    assert got.json()["record"]["results"]["verdicts"]["cosmos"] == {"pairing": "построен"}
    empty = dict(body, entries=[], results=None)
    got = panel.post(f"/api/runs/{run_id}/record/append", json=empty)
    assert got.status_code == 400 and "тело пустое" in got.json()["detail"]


def test_runner_sends_the_full_head_and_panel_keeps_it_as_is(panel):
    """Вариант «б» (договор с COSMOS 21:4x, живая находка run-0135):
    хозяин шлёт голову ЦЕЛИКОМ полем формы `declaration` (JSON) с её
    отпечатком `declaration_sha256`; панель судит словарём, отпечатком и
    сверкой «объявлено / на деле» по своим ролям, кладёт голову в
    declaration.json КАК ЕСТЬ и отвечает её отпечатком — отпечатки
    хозяина и хранителя равны по построению. Расхождение — 400 до
    рождения папки."""
    from test_cdpm_extract import _RULES_A, _RULES_B
    env = panel.get("/api/declaration-preview",
                    params={"data_class": "synthetic"}).json()["declaration"]
    items = [("RULES-A.md", _RULES_A.encode("utf-8")),
             ("RULES-B.md", _RULES_B.encode("utf-8"))]
    rid = "20260910-213221-9c6f40ad"
    head = {
        "run_id": rid, "created_at": "2026-09-10T21:32:21+02:00",
        "created_by": "раннер наборов H0", "purpose": "регресс",
        "contour": {"name": env["contour"]["name"],
                    "services": env["contour"]["services"]},
        "airline": {"name": "Полярис-H0", "data_class": "synthetic",
                    "llm_scope": "any"},
        "rules_documents": rr.rules_documents_of(items),
        "data_files": [{"name": "ID-H0-DATA.xlsx", "sheets": 6, "rows": 80,
                        "sha256": "5" * 64}],
        "answers": [],
        "catalog": env["catalog"], "catalog_extension": None,
        "bindings": env["bindings"], "models": env["models"],
        "solvers": [], "code": env["code"], "libraries": env["libraries"],
        "planned_stages": list(rr.PANEL_STAGES), "stop_after": "package",
        "expectations": {"kind": "манифест набора",
                         "ref": "open-bench/polyaris-h0c/kit-manifest.json"},
        "lineage": {"root_sha256": "9" * 64, "root_answers_sha256": None,
                    "parent_run": None, "branch_of": None,
                    "package_sha256": None, "source_run": None},
        "answer_policy": "книга",
    }
    sha = rr.declaration_sha256(head)

    def post(h, s, **extra):
        data = {"record_run_id": h["run_id"], "created_by": h["created_by"],
                "declaration": json.dumps(h, ensure_ascii=False, sort_keys=True),
                "declaration_sha256": s, **extra}
        return panel.post("/api/runs", files=[
            ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
            ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
        ], params={"data_class": "synthetic", "customer": "Полярис-H0"},
            data=data)
    got = post(head, sha)
    assert got.status_code == 200, got.text
    assert got.json()["record"] == {"run_id": rid, "declaration_sha256": sha}
    run = panel.runs / got.json()["run"]
    doc = json.loads((run / rr.DECLARATION_FILE).read_text(encoding="utf-8"))
    assert doc["declaration"] == head and doc["declaration_sha256"] == sha
    rec = rr.stitch(run)
    assert rec["declaration"]["run_id"] == rid and rr.foreign_record(run) is None
    assert rr.record_id_of(run) == rid and rr.declaration_sha256_of(run) == sha
    # Отказы — до рождения папки: номер не расходуется.
    before = sorted(p.name for p in panel.runs.glob("run-*"))
    got = post(head, "0" * 64)
    assert got.status_code == 400
    assert "отпечаток объявления не сходится" in got.json()["detail"]
    other = json.loads(json.dumps(head))
    other["catalog"] = dict(head["catalog"], fingerprint="f" * 64)
    got = post(other, rr.declaration_sha256(other))
    assert got.status_code == 400 and "каталог шаблонов" in got.json()["detail"]
    got = post(head, sha, record_run_id="другой")
    assert got.status_code == 400 and "record_run_id" in got.json()["detail"]
    bad = json.loads(json.dumps(head))
    bad["purpose"] = "прочее"
    got = post(bad, rr.declaration_sha256(bad))
    assert got.status_code == 400 and "цель" in got.json()["detail"]
    assert sorted(p.name for p in panel.runs.glob("run-*")) == before


def test_record_handle_serves_and_downloads_the_record(panel):
    """`GET …/record` — единая запись; `?download=1` — файлом
    `run-record.json`; прогона нет — 404, записи нет — тоже 404."""
    run_id = _create(panel).json()["run"]
    rec = panel.get(f"/api/runs/{run_id}/record")
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["schema"] == rr.RECORD_SCHEMA
    assert body["declaration"]["run_id"] == run_id
    assert [e["substage"] for e in body["journal"]] == ["create"]
    got = panel.get(f"/api/runs/{run_id}/record?download=1")
    assert got.status_code == 200
    assert 'filename="run-record.json"' in got.headers["content-disposition"]
    assert json.loads(got.content.decode("utf-8"))["declaration_sha256"] \
        == body["declaration_sha256"]
    assert panel.get("/api/runs/run-9999/record").status_code == 404
    # Прогон есть, записи нет (рождён до шага 2) — 404 словами.
    bare = panel.runs / "run-0900"
    bare.mkdir(parents=True)
    _write(bare / "status.json", {"state": "заморожено"})
    got = panel.get("/api/runs/run-0900/record")
    assert got.status_code == 404 and "записи о прогоне" in got.json()["detail"]


def test_append_checks_head_and_dictionary(panel):
    """`POST …/record/append` (11.2): 404 без записи, 409 при
    несовпадении головы, 422 при записи не по словарю (не принято
    ничего), 200 с записью целиком — и идемпотентность по ключу."""
    run_id = _create(panel).json()["run"]
    run = panel.runs / run_id
    head = panel.get(f"/api/runs/{run_id}/record").json()
    url = f"/api/runs/{run_id}/record/append"

    got = panel.post("/api/runs/run-9999/record/append",
                     json=_append_body(head, [_entry()]))
    assert got.status_code == 404, got.text

    got = panel.post(url, json=_append_body(head, [_entry()],
                                            declaration_sha256="e" * 64))
    assert got.status_code == 409, got.text
    assert got.json()["code"] == "declaration-mismatch"
    assert head["declaration_sha256"][:12] in got.json()["words"]
    got = panel.post(url, json=_append_body(head, [_entry()],
                                            record_id="20260910-нет"))
    assert got.status_code == 409 and got.json()["ok"] is False

    # Тело не разобрано: чужая схема, пустые записи, система без имени.
    for body in ({**_append_body(head, [_entry()]), "schema": "чужая/v9"},
                 _append_body(head, []),
                 {**_append_body(head, [_entry()]), "system": " "}):
        assert panel.post(url, json=body).status_code == 400, body

    bad = _entry()
    bad["outcome"] = "получилось"
    bad["лишнее"] = 1
    got = panel.post(url, json=_append_body(head, [_entry(), bad]))
    assert got.status_code == 422, got.text
    assert got.json()["code"] == "entry-invalid" and got.json()["words"]
    assert not rr.appends_path(run).is_file(), "не принято НИЧЕГО"

    got = panel.post(url, json=_append_body(
        head, [_entry(), _entry(stage="source-check", substage=None,
                                outcome="предупреждение")],
        results={"verdicts": {"pairing": "OK"},
                 "deliveries": [{"kind": "solution", "path": "runs/x.json",
                                 "sha256": "a" * 64}]}))
    assert got.status_code == 200, got.text
    assert got.json()["accepted"] == 2 and got.json()["replaced"] == 0
    rec = got.json()["record"]
    stages = [e["stage"] for e in rec["journal"]]
    assert set(stages) == {"freeze", "pairing", "source-check"}
    # При равном моменте порядок — по приёму (часы панели у записи
    # рождения — часы машины, поэтому судится порядок чужих записей).
    assert stages.index("pairing") < stages.index("source-check")
    assert rec["results"]["verdicts"]["cosmos"] == {"pairing": "OK"}
    assert {"kind": "solution", "path": "runs/x.json", "sha256": "a" * 64} \
        in rec["results"]["deliveries"]
    # Слияние видно и в ручке, и в файле записи (панель — хранитель).
    assert panel.get(f"/api/runs/{run_id}/record").json() == rec
    on_disk = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert [e["stage"] for e in on_disk["journal"]] == [e["stage"]
                                                        for e in rec["journal"]]

    # Повтор ключа (система, этап, подэтап) ЗАМЕНЯЕТ прежнюю запись.
    got = panel.post(url, json=_append_body(
        head, [_entry(outcome="сбой", words="решатель упал")]))
    assert got.status_code == 200 and got.json()["replaced"] == 1
    rec = got.json()["record"]
    pairing = [e for e in rec["journal"] if e["stage"] == "pairing"]
    assert len(pairing) == 1 and pairing[0]["outcome"] == "сбой"
    assert pairing[0]["words"] == "решатель упал"
    # Чужой исход старше панельного состояния: прогон в сбое.
    assert rec["results"]["state"] == "сбой"


def test_append_to_foreign_head_keeps_the_file_and_stitches(panel):
    """Переходное правило «два летописца» (11.3): раннер положил свою
    запись в папку прогона — панель файл не переписывает, но append
    принимает по ЧУЖОЙ голове, а `GET …/record` отдаёт сшивку."""
    run_id = _create(panel).json()["run"]
    run = panel.runs / run_id
    rid = "20260910-132100-9c6f40ad"
    foreign = {"schema": rr.RECORD_SCHEMA,
               "declaration": {"run_id": rid,
                               "created_by": "раннер наборов H0"},
               "declaration_sha256": "e" * 64,
               "journal": [_entry(stage="kit", substage=None,
                                  started_at="2026-09-10T17:00:00+02:00",
                                  actor="раннер наборов H0")],
               "results": {"verdicts": {"kit": "OK"}, "state": "идёт"}}
    _write(run / rr.RECORD_FILE, foreign)
    raw = (run / rr.RECORD_FILE).read_bytes()
    head = panel.get(f"/api/runs/{run_id}/record").json()
    assert head["declaration"]["run_id"] == rid
    assert head["declaration_sha256"] == "e" * 64
    url = f"/api/runs/{run_id}/record/append"
    # Голова панели чужой записи не судит: сверка идёт по голове файла.
    assert panel.post(url, json={
        "schema": rr.APPEND_SCHEMA, "record_id": run_id,
        "declaration_sha256": rr.build(run)["declaration_sha256"],
        "system": "cosmos", "entries": [_entry()]}).status_code == 409
    got = panel.post(url, json=_append_body(head, [_entry()]))
    assert got.status_code == 200, got.text
    assert (run / rr.RECORD_FILE).read_bytes() == raw, "файл не переписан"
    rec = got.json()["record"]
    assert rec["declaration"]["run_id"] == rid
    assert [e["stage"] for e in rec["journal"]] == ["kit", "pairing"]
    assert rec["results"]["verdicts"]["kit"] == "OK"
    assert panel.get(f"/api/runs/{run_id}/record").json() == rec
    # Принятое лежит строками в журнале приёмов, а не в чужом файле.
    rows = [json.loads(x) for x in rr.appends_path(run)
            .read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows and rows[0]["system"] == "cosmos"
    assert rows[0]["key"] == ["cosmos", "pairing", "attempt-1"]


def test_stitched_record_validates_against_cosmos_schema(panel):
    """Сшитая запись — по тому же словарю: ручка отдаёт запись, которую
    COSMOS читает своим `validate`."""
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).resolve().parents[1] / "vmp" / "run_record.schema.json"
    if not schema_path.is_file():
        pytest.skip("схема словаря COSMOS ещё не в дереве")
    run_id = _create(panel).json()["run"]
    head = panel.get(f"/api/runs/{run_id}/record").json()
    panel.post(f"/api/runs/{run_id}/record/append", json=_append_body(
        head, [_entry()], results={"verdicts": {"pairing": "OK"},
                                   "deliveries": []}))
    rec = panel.get(f"/api/runs/{run_id}/record").json()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    problems = [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"
                for e in jsonschema.Draft202012Validator(schema).iter_errors(rec)]
    assert problems == [], problems


def test_declaration_preview_shows_environment_and_problems(panel, monkeypatch):
    """Блок «Объявление прогона» (11.4): объявимая часть среды и слова
    проблем гейта — ДО кнопки «Заморозить»."""
    # Форма ответа — договор с формой панели и COSMOS (11.4, письмо 16:5x):
    # {ok, words[], declaration{…}, defaults{…}, choices{…}}; с
    # 11.09.2026 рядом стоит `cosmos{asked, detail}` — спрошена ли среда
    # COSMOS для моделей головы (стенд — test_cdpm_run_record_env.py).
    got = panel.get("/api/declaration-preview",
                    params={"data_class": "synthetic"}).json()
    assert set(got) == {"ok", "words", "declaration", "defaults", "choices",
                        "cosmos"}
    assert got["ok"] is True and got["words"] == []
    d, df, ch = got["declaration"], got["defaults"], got["choices"]
    assert df["purpose"] == rr.DEFAULT_PURPOSE
    assert ch["purposes"] == list(rr.PURPOSES)
    assert df["answer_policy"] == "человек"
    assert ch["answer_policies"] == list(rr.ANSWER_POLICIES)
    assert ch["stages"] == list(rr.PANEL_STAGES)
    assert df["stop_after"] == "package"
    assert df["planned_stages"] == list(rr.PANEL_STAGES)
    assert df["created_by"] == rr.PANEL_ACTOR
    assert d["catalog"]["version"] and d["catalog"]["fingerprint"]
    assert d["code"]["cdpm_commit"] and d["libraries"]["python"]
    assert d["airline"]["llm_scope"] == "any" and d["airline"]["reading"]
    assert d["models"] and d["layout"]["cloud_gate"] == "open"
    assert d["contour"]["name"] in rr.CONTOURS
    assert set(d["bindings"]) == {"catalog_version", "catalog_fingerprint", "path"}
    # Почта Cloudflare Access — умолчание поля «кто запускает».
    got = panel.get("/api/declaration-preview",
                    headers={"Cf-Access-Authenticated-User-Email":
                             "kto@example.test"}).json()
    assert got["defaults"]["created_by"] == "kto@example.test"
    # Проблемы гейта видны словами, и они же остановят «Заморозить».
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    got = panel.get("/api/declaration-preview",
                    params={"data_class": "synthetic"}).json()
    assert got["ok"] is False
    assert any("раскладка моделей не объявлена" in p for p in got["words"])
    # Кривой признак комплекта тоже называется, а не молчит.
    got = panel.get("/api/declaration-preview",
                    params={"data_class": "секретный"}).json()
    assert got["ok"] is False
    assert any("признак комплекта" in p for p in got["words"])


def test_manifest_carries_record_api(tmp_path, monkeypatch):
    """`record_api` в манифесте (11.2): адрес записи у хранителя —
    из переменной площадки, иначе из адреса запроса на выпуск; нет ни
    того, ни другого — ключа нет."""
    from cdpm import export_task_package as etp
    from test_cdpm_task_package import _ready_doc, _run_dir as _pkg_run
    monkeypatch.delenv(etp.PUBLIC_URL_ENV, raising=False)
    run = _pkg_run(tmp_path, _ready_doc())
    man = json.loads(etp.build_package(run)["manifest.json"].decode("utf-8"))
    assert "record_api" not in man
    assert man["record_id"] == run.name
    man = json.loads(etp.build_package(
        run, "http://127.0.0.1:8095/")["manifest.json"].decode("utf-8"))
    assert man["record_api"] == f"http://127.0.0.1:8095/api/runs/{run.name}/record"
    monkeypatch.setenv(etp.PUBLIC_URL_ENV, "https://cdpm.example.test")
    man = json.loads(etp.build_package(
        run, "http://127.0.0.1:8095")["manifest.json"].decode("utf-8"))
    assert man["record_api"] == \
        f"https://cdpm.example.test/api/runs/{run.name}/record"


def test_export_handle_puts_panel_address_into_manifest(panel, monkeypatch):
    """Ручка выпуска пакета кладёт в манифест адрес СВОЕЙ панели — по
    нему COSMOS дописывает записи этапов (`record_api + "/append"`)."""
    import io
    import zipfile

    from cdpm import export_task_package as etp
    from test_cdpm_task_package import _ready_doc, _run_dir as _pkg_run
    monkeypatch.delenv(etp.PUBLIC_URL_ENV, raising=False)
    run = _pkg_run(panel.runs, _ready_doc())          # прогон с моделью
    got = panel.get(f"/api/runs/{run.name}/export/task-package")
    assert got.status_code == 200, got.text
    with zipfile.ZipFile(io.BytesIO(got.content)) as z:
        man = json.loads(z.read("manifest.json"))
    assert man["record_api"] == \
        f"http://testserver/api/runs/{run.name}/record"
    assert man["record_id"] == run.name


def test_runs_list_carries_record_fields_of_each_run(panel, monkeypatch):
    """Список прогонов отдаёт поля ЗАПИСИ о прогоне — их читает экран
    «Все прогоны обеих систем» (COSMOS зовёт `GET /api/runs` только на
    чтение): номер записи, площадка и кто объявил — из головы,
    признак золотого — из журнала приписок хранителя, номер прогона
    COSMOS — из квитанции последней доставки. У прогона без головы все
    поля — null: носителя нет, и ничего не достраивается."""
    run_id = _create(panel).json()["run"]
    run = panel.runs / run_id
    # Прогон без головы (рождён до гейта «Новый прогон»).
    _write(panel.runs / "run-0900" / "status.json", {"state": "заморожено"})

    listed = {r["run"]: r for r in panel.get("/api/runs").json()}
    assert set(listed) == {run_id, "run-0900"}
    head = rr.read_declaration(run)["declaration"]
    assert listed[run_id]["record_id"] == head["run_id"] == run_id
    assert listed[run_id]["contour"] == head["contour"]["name"] == "local"
    assert listed[run_id]["created_by"] == head["created_by"] == rr.PANEL_ACTOR
    assert listed[run_id]["golden"] is None          # владелец не подтверждал
    assert listed[run_id]["cosmos_run"] is None      # доставок не было
    bare = listed["run-0900"]
    assert [bare["record_id"], bare["contour"], bare["created_by"],
            bare["golden"], bare["cosmos_run"]] == [None] * 5

    # Пакет доставлен: номер прогона COSMOS — из квитанции, прогон стал
    # завершённым, и владелец вправе назвать его золотым.
    import zipfile

    package = run / "channel" / "task-package.zip"
    package.parent.mkdir(exist_ok=True)
    # Artifact-identity fixture, not proof of package contents or gate 5.
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("fixture.txt", "runs-list delivery projection")
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    _write(run / "channel" / "delivery.json",
           {"sent": True, "sha": package_sha, "verdict": "OK",
            "mg": {}, "mismatch": [], "already_accepted": False,
            "detail": "Synthetic healthy brief for runs-list projection",
            "cosmos_run": "run-0077"})
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    got = panel.post(f"/api/runs/{run_id}/golden",
                     json={"by": "hozyain@example.test",
                           "words": "приёмка трёх наборов пройдена"})
    assert got.status_code == 200, got.text

    row = {r["run"]: r for r in panel.get("/api/runs").json()}[run_id]
    assert row["cosmos_run"] == "run-0077"
    assert row["golden"]["confirmed"] is True
    assert row["golden"]["by"] == "hozyain@example.test"
    assert row["golden"]["words"] == "приёмка трёх наборов пройдена"
    assert row["golden"]["at"]
    # Голова прогона от списка и подтверждения не шелохнулась.
    assert row["record_id"] == run_id and row["contour"] == "local"
