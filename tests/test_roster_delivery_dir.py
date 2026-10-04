# -*- coding: utf-8 -*-
"""Последняя выдача ростеринга — штатная (button/) либо «на утверждённом
пейринге» (button-on-pairing/): по времени delivery.json (06.09.2026,
демо-путь «пейринг → ростеринг на пейринге»)."""
from __future__ import annotations

import os
import time

from webapp import package_runs as pr


def _touch(p, t):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")
    os.utime(p, (t, t))


def test_последняя_выдача_по_времени(tmp_path):
    assert pr.roster_delivery_dir(tmp_path) is None
    now = time.time()
    _touch(tmp_path / "button" / "delivery.json", now - 100)
    assert pr.roster_delivery_dir(tmp_path) == tmp_path / "button"
    _touch(tmp_path / "button-on-pairing" / "delivery.json", now)
    assert pr.roster_delivery_dir(tmp_path) == tmp_path / "button-on-pairing"
    _touch(tmp_path / "button" / "delivery.json", now + 100)
    assert pr.roster_delivery_dir(tmp_path) == tmp_path / "button"


def test_алиасы_на_пейринге_ведут_в_свой_каталог():
    for alias in pr.TASK_LINKS["rostering_on_pairing"]:
        d, _f = pr.ALLOWED_FILES[alias]
        assert d == "button-on-pairing", alias
    assert "сертификат-кнопки-на-пейринге-текст" in pr.ALLOWED_FILES
    assert pr.ALLOWED_FILES["решение-кнопки-ростер-на-пейринге"] == (
        "button-on-pairing", "solution.json")
