# -*- coding: utf-8 -*-
"""`template_catalog.candidates.record_in_tree`: запись так, как её несёт
дерево — из каталога, если он её назвал, иначе из файла кандидата.

Тестам исполнителей (в том числе написанным агентом конвейера) запись
нужна и ДО сборки (кандидат рядом с каталогом), и ПОСЛЕ (сборка службой
удаляет файл кандидата, запись живёт в каталоге). Чтение по пути файла
кандидата краснело первым же выпуском (0.4.0, 09.09.2026: тест семьи
duty_series_limit искал исчезнувший файл)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
from template_catalog import candidates as tcc                    # noqa: E402


def _candidate_doc(rec: dict) -> dict:
    return {"form_version": 1, "section": "families", "id": rec["id"],
            "record": rec, "note": "проба стенда", "by": "контур самоулучшения",
            "saved_at": "2026-09-09T20:00:00+03:00",
            "request_id": "req-00000000000000000001", "cr_key": "cr00000001"}


def test_запись_каталога_берётся_из_каталога(tmp_path):
    folder = cc.copy_catalog(tmp_path, candidates=False)
    rec, source = tcc.record_in_tree(folder, "families", "duty_limits")
    assert source == "каталог" and rec["id"] == "duty_limits"
    assert "history" in rec or rec.get("status") == "есть"


def test_записи_нет_в_каталоге_берётся_кандидат(tmp_path):
    folder = cc.copy_catalog(tmp_path, candidates=False)
    base = json.loads((folder / "families.json").read_text(encoding="utf-8"))
    template = next(f for f in base["families"] if f["id"] == "duty_limits")
    rec = dict(template, id="phantom_family_record_in_tree")
    rec.pop("history", None)
    cand_dir = folder / "candidates" / "families"
    cand_dir.mkdir(parents=True)
    (cand_dir / f"{rec['id']}.json").write_text(
        json.dumps(_candidate_doc(rec), ensure_ascii=False, indent=1),
        encoding="utf-8")
    got, source = tcc.record_in_tree(folder, "families", rec["id"])
    assert source == "кандидат" and got["id"] == rec["id"]
    assert "history" not in got


def test_нет_ни_записи_ни_кандидата_отказ_словами(tmp_path):
    folder = cc.copy_catalog(tmp_path, candidates=False)
    with pytest.raises(tcc.CandidateError) as e:
        tcc.record_in_tree(folder, "families", "no_such_family_anywhere")
    assert "ни в каталоге" in str(e.value) and "ни кандидатом" in str(e.value)
