# -*- coding: utf-8 -*-
"""Гейт артефактов CDPM: зелёный на чистом комплекте, красный на каждом
классе порчи, счётчики в выводе (У-05), работоспособность из командной
строки без переменных кодировки (У-08/У-14).

Отдельно проверяется охранное свойство «правка мимо модели»: артефакт,
подправленный руками в обход машинной модели, не проходит гейт — это
механизация урока «ответы вносятся в свод, иначе свод и анкета
расходятся»."""
import subprocess
import sys
from pathlib import Path

from cdpm import model as m
from cdpm import render
from cdpm.gates import gate_artifacts
from cdpm.sample import sample_model

REPO = Path(__file__).resolve().parents[1]


def _kit(tmp_path, doc=None):
    """Полный комплект артефактов во временном каталоге."""
    doc = doc or sample_model()
    model_path = tmp_path / "model.json"
    m.dump_model(doc, model_path)
    svod = tmp_path / "svod.md"
    anketa = tmp_path / "anketa.md"
    svod.write_text(render.render_svod(doc), encoding="utf-8")
    anketa.write_text(render.render_anketa(doc), encoding="utf-8")
    return model_path, svod, anketa


def test_gate_green_on_clean_kit(tmp_path, capsys):
    model_path, svod, anketa = _kit(tmp_path)
    rc = gate_artifacts.run(str(model_path), str(svod), str(anketa))
    out = capsys.readouterr().out
    assert rc == 0
    # У-05: гейт печатает, сколько объектов рассмотрел.
    assert "рассмотрено: источников 2, правил 10, вопросов 5" in out
    assert "рендеров сверено 2" in out
    assert "самотест пройден" in out
    assert "ВЕРДИКТ: артефакты соответствуют контракту" in out


def test_gate_red_on_broken_model(tmp_path, capsys):
    doc = sample_model()
    doc["rules"][0]["sources"] = []
    model_path = tmp_path / "model.json"
    m.dump_model(doc, model_path)
    rc = gate_artifacts.run(str(model_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "НАРУШЕНИЕ" in out
    assert "ВЕРДИКТ: КРАСНЫЙ" in out


def test_gate_red_on_render_tampered_behind_models_back(tmp_path, capsys):
    model_path, svod, anketa = _kit(tmp_path)
    svod.write_text(
        svod.read_text(encoding="utf-8").replace("≤ 4", "≤ 5"),
        encoding="utf-8")
    rc = gate_artifacts.run(str(model_path), str(svod), str(anketa))
    out = capsys.readouterr().out
    assert rc == 1
    assert "мимо модели" in out


def test_gate_red_on_missing_render_file(tmp_path, capsys):
    model_path, svod, anketa = _kit(tmp_path)
    svod.unlink()
    rc = gate_artifacts.run(str(model_path), str(svod), str(anketa))
    assert rc == 1
    assert "не существует" in capsys.readouterr().out


def test_gate_red_on_schema_violation(tmp_path, capsys):
    doc = sample_model()
    doc["rules"][0]["vydumka"] = 5          # чужое поле — схема закрыта
    model_path = tmp_path / "model.json"
    m.dump_model(doc, model_path)
    rc = gate_artifacts.run(str(model_path))
    assert rc == 1
    assert "схема:" in capsys.readouterr().out


def test_gate_cli_runs_from_anywhere_without_encoding_vars(tmp_path):
    """Гейт зовётся полными путями из чужого каталога (рабочее
    соглашение) и не требует PYTHONIOENCODING/PYTHONUTF8 — печать
    кириллицы не роняет процесс (класс «печать роняет процесс» чинился
    дважды, У-14)."""
    model_path, svod, anketa = _kit(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "cdpm.gates.gate_artifacts",
         str(model_path), "--svod", str(svod), "--anketa", str(anketa)],
        capture_output=True, cwd=str(tmp_path),
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
        timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ВЕРДИКТ".encode("utf-8") in proc.stdout
