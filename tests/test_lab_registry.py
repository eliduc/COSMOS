"""Реестр находок (tools/lab_registry.py + evolution.update_catalog).

Проверяется ровно то, чем механизм может навредить: затереть решение
оператора, тихо проглотить битую строку, дать недетерминированный файл или
пустить в каталог поле конвейера. Все тесты — негативные по замыслу:
happy-path здесь ничего не доказывает.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from webapp import evolution, users

_SPEC = importlib.util.spec_from_file_location(
    "lab_registry",
    Path(__file__).resolve().parents[1] / "tools" / "lab_registry.py")
lab_registry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lab_registry)

SITE = Path("<not used: evolution injected>")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    return tmp_path


def _seed():
    _, a = evolution.add_finding("Column generation warm start",
                                 "Тёплый старт из прошлого оптимума.",
                                 source_url="https://example.org/a")
    _, b = evolution.add_finding("Duty-based master formulation",
                                 "Двухэтапная модель: смены, затем пейринги.",
                                 source_url="https://example.org/b")
    return a, b


def _registry(tmp_path, records):
    path = tmp_path / "lab-registry.jsonl"
    with path.open("w", encoding="utf-8", newline="") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _full(rec):
    return {k: rec.get(k) for k in lab_registry.FIELD_ORDER}


# ------------------------------------------------- 1. детерминированность

def test_export_is_byte_stable(store):
    _seed()
    one, two = store / "one.jsonl", store / "two.jsonl"
    lab_registry.export(SITE, one, evolution=evolution)
    lab_registry.export(SITE, two, evolution=evolution)
    assert one.read_bytes() == two.read_bytes()
    # сортировка по ключу, а не по id: порядок не зависит от истории вставок
    keys = [json.loads(x)["dedup_key"]
            for x in one.read_text(encoding="utf-8").splitlines()]
    assert keys == sorted(keys)
    # перевод строки только LF — иначе файл различается между машинами
    assert b"\r\n" not in one.read_bytes()


# ------------------------------------------------------ 2. идемпотентность

def test_import_twice_changes_nothing(store):
    a, _ = _seed()
    reg = store / "reg.jsonl"
    lab_registry.export(SITE, reg, evolution=evolution)
    lab_registry.import_(SITE, reg, apply=True, evolution=evolution)
    snapshot = evolution.list_all()
    lab_registry.import_(SITE, reg, apply=True, evolution=evolution)
    assert evolution.list_all() == snapshot


# ------------------------------------------- 3. конвейер неприкосновенен

def test_import_never_touches_pipeline_state(store):
    a, b = _seed()
    evolution.set_status(a["id"], evolution.STATUS_REJECTED,
                         decided_by="admin@example.org")
    evolution.set_status(b["id"], evolution.STATUS_TEST_IMPROVED,
                         branch="cr-7")
    evolution.attach_metrics(b["id"], evolution.PHASE_TEST, {"gain": 3.5})
    reg = _registry(store, [
        _full({"dedup_key": "https://example.org/a",
               "title": "Column generation warm start",
               "summary": "Тёплый старт из прошлого оптимума.",
               "source_url": "https://example.org/a",
               "published_at": "2024"}),
        _full({"dedup_key": "https://example.org/b",
               "title": "Duty-based master formulation",
               "summary": "Двухэтапная модель: смены, затем пейринги.",
               "source_url": "https://example.org/b",
               "impact": {"source": "llm", "score": 80}}),
    ])
    lab_registry.import_(SITE, reg, apply=True, evolution=evolution)

    ra, rb = evolution.get(a["id"]), evolution.get(b["id"])
    assert ra["status"] == evolution.STATUS_REJECTED
    assert ra["decided_by"] == "admin@example.org"
    assert rb["status"] == evolution.STATUS_TEST_IMPROVED
    assert rb["branch"] == "cr-7"
    assert rb["test_metrics"] == {"gain": 3.5}
    # каталожная часть при этом обновилась
    assert ra["published_at"] == "2024"
    assert rb["impact"]["score"] == 80


# --------------------------------------- 4. белый список каталожных полей

@pytest.mark.parametrize("field", ["status", "branch", "test_metrics",
                                   "decided_by", "dedup_key", "id"])
def test_update_catalog_refuses_pipeline_fields(store, field):
    a, _ = _seed()
    with pytest.raises(ValueError):
        evolution.update_catalog(a["id"], {field: "x"})
    assert evolution.get(a["id"])["status"] == evolution.STATUS_FOUND


def test_update_catalog_refuses_empty_title(store):
    a, _ = _seed()
    with pytest.raises(ValueError):
        evolution.update_catalog(a["id"], {"title": "   "})
    assert evolution.get(a["id"])["title"] == "Column generation warm start"


# ------------------------------------------------- 5. битый реестр = отказ

@pytest.mark.parametrize("bad", [
    '{"dedup_key": "k", "title": "T"}',                      # нет summary
    '{"dedup_key": "k", "title": "", "summary": "S"}',        # пустой title
    '{"dedup_key": "k", "title": "T", "summary": "S", "status": "found"}',
    '{"title": "T", "summary": "S"}',                         # нет ключа
    'не json вовсе',
])
def test_broken_registry_line_refuses_whole_import(store, bad):
    _seed()
    before = evolution.list_all()
    path = store / "broken.jsonl"
    path.write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(lab_registry.RegistryError):
        lab_registry.import_(SITE, path, apply=True, evolution=evolution)
    assert evolution.list_all() == before


def test_duplicate_key_in_registry_refuses(store):
    _seed()
    rec = _full({"dedup_key": "https://example.org/a", "title": "T",
                 "summary": "S"})
    path = _registry(store, [rec, rec])
    with pytest.raises(lab_registry.RegistryError):
        lab_registry.import_(SITE, path, apply=False, evolution=evolution)


# --------------------------------------------------- 6. холостой прогон

def test_dry_run_writes_nothing(store):
    before = evolution.list_all()
    reg = _registry(store, [_full({"dedup_key": "https://example.org/new",
                                   "title": "Новая находка",
                                   "summary": "Из реестра."})])
    lab_registry.import_(SITE, reg, apply=False, evolution=evolution)
    assert evolution.list_all() == before
    lab_registry.import_(SITE, reg, apply=True, evolution=evolution)
    assert len(evolution.list_all()) == len(before) + 1


def test_registry_does_not_delete_local_findings(store):
    _seed()
    reg = _registry(store, [_full({"dedup_key": "https://example.org/a",
                                   "title": "Column generation warm start",
                                   "summary": "Тёплый старт."})])
    lab_registry.import_(SITE, reg, apply=True, evolution=evolution)
    keys = {r["dedup_key"] for r in evolution.list_all()}
    assert "https://example.org/b" in keys
