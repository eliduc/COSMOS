# -*- coding: utf-8 -*-
"""Generic-этап 2 берёт документ постановки той же функцией, что этап 1.

Дефект 2026-08-12: generic-ветка stage2 в jobs._build_commands одна
ходила мимо `_statement_or_source_rules` — брала только служебный
`source_rules_<label>.json`. На контуре заказчика (документ постановки
загружен, служебного файла нет) этап 2 стартовал бы вовсе без правил
источника — и был бы отвергнут сверкой с каталогом, который несёт
отпечаток документа. Ростеринг из интерфейса не работал ни разу.
"""
import hashlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def jobs(monkeypatch, tmp_path):
    import _data_paths
    from webapp import jobs as jobs_mod, registry
    root = tmp_path / "data-root"
    (root / "rules").mkdir(parents=True)
    monkeypatch.setattr(_data_paths, "data_root", lambda: root)
    monkeypatch.setattr(registry, "resolve_rel", lambda p: p)
    return jobs_mod, root


def _inst(cat: Path, payload: Path) -> dict:
    # Пути входов ведут на СУЩЕСТВУЮЩИЕ файлы: с 2026-08-13 сборщик
    # команд разрешает их и отказывает на пропавшем входе
    # (см. test_jobs_child_paths.py).
    return {"id": "upload-x--uz", "label": "uz-x", "source": "upload",
            "payload_path": str(payload), "catalog_path": str(cat)}


def _payload(tmp_path: Path) -> Path:
    p = tmp_path / "payload.json"
    p.write_text("{}", encoding="utf-8")
    return p


def test_stage2_uses_statement_document(jobs, tmp_path):
    jobs_mod, root = jobs
    doc = root / "rules" / "postanovka.md"
    doc.write_text("# правила", encoding="utf-8")
    cat = tmp_path / "catalog.json"
    cat.write_text("{}", encoding="utf-8")
    pay = _payload(tmp_path)
    cmds = jobs_mod._build_commands(
        "stage2", _inst(cat, pay), {"budget_s": 60, "seed": 7})
    cmd = cmds[0]
    assert "--source-rules" in cmd, cmd
    i = cmd.index("--source-rules")
    assert cmd[i + 1] == str(doc)
    fp = cmd[cmd.index("--source-rules-fp") + 1]
    assert fp == hashlib.sha256(doc.read_bytes()).hexdigest()
    assert "--source-rules-strict" in cmd


def test_stage2_without_any_rules_carries_none(jobs, tmp_path):
    # ни документа, ни служебного файла — правила в команду не идут
    jobs_mod, _root = jobs
    cat = tmp_path / "catalog.json"
    cat.write_text("{}", encoding="utf-8")
    cmds = jobs_mod._build_commands(
        "stage2", _inst(cat, _payload(tmp_path)), {"budget_s": 60, "seed": 7})
    assert "--source-rules" not in cmds[0]
