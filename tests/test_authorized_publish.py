# -*- coding: utf-8 -*-
"""Публикация ослабленного каталога по слову владельца.

Контракт: RELAXED_COVER принимается этапом 2 ТОЛЬКО с полным
машинным свидетельством утверждения (блок authorization, отпечаток
утверждённого пакета сверяется байт-в-байт); всё прочее — прежний
отказ без обхода (R6 P0-05). Публикатор не перезаписывает молча.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import _gerad_stage2_roster as st2  # noqa: E402
from tools import publish_authorized_catalog as pub  # noqa: E402


def _pending(tmp_path, uncovered=3):
    doc = {"coverage": {"verdict": "RELAXED_COVER",
                        "uncovered": uncovered, "legs_total": 100},
           "selected": []}
    p = tmp_path / "cat.pending.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    pkg = tmp_path / "package.md"
    pkg.write_text("пакет авторизации 3 непокрытых",
                   encoding="utf-8")
    return p, pkg


def test_публикация_и_приём_этапом_2(tmp_path, capsys):
    p, pkg = _pending(tmp_path)
    rc = pub.main(["--pending", str(p), "--package", str(pkg),
                   "--approved-by", "владелец, лично",
                   "--approved-at", "2026-08-29"])
    assert rc == 0
    out = tmp_path / "cat.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    auth = doc["authorization"]
    assert auth["scope_uncovered"] == 3
    assert len(auth["package_sha256"]) == 64
    # этап 2 принимает со свидетельством — и говорит об этом вслух
    st2._assert_publishable(doc, out)
    assert "свидетельством утверждения" in capsys.readouterr().out


def test_без_свидетельства_прежний_отказ(tmp_path):
    p, _ = _pending(tmp_path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    with pytest.raises(st2.RulesMismatch):
        st2._assert_publishable(doc, p)


def test_подменённый_пакет_отказ(tmp_path):
    p, pkg = _pending(tmp_path)
    assert pub.main(["--pending", str(p), "--package", str(pkg),
                     "--approved-by", "x", "--approved-at",
                     "2026-08-29"]) == 0
    pkg.write_text("другой текст", encoding="utf-8")
    out = tmp_path / "cat.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    with pytest.raises(st2.RulesMismatch, match="изменён"):
        st2._assert_publishable(doc, out)


def test_объём_согласия_не_совпал_отказ(tmp_path):
    p, pkg = _pending(tmp_path)
    assert pub.main(["--pending", str(p), "--package", str(pkg),
                     "--approved-by", "x", "--approved-at",
                     "2026-08-29"]) == 0
    out = tmp_path / "cat.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    doc["coverage"]["uncovered"] = 4        # каталог «поплыл»
    with pytest.raises(st2.RulesMismatch, match="объём согласия"):
        st2._assert_publishable(doc, out)


def test_публикатор_не_перезаписывает_молча(tmp_path):
    p, pkg = _pending(tmp_path)
    (tmp_path / "cat.json").write_text("{}", encoding="utf-8")
    rc = pub.main(["--pending", str(p), "--package", str(pkg),
                   "--approved-by", "x", "--approved-at", "y"])
    assert rc == 2
