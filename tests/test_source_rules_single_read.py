"""Правила источника читаются ОДИН раз за прогон (аудит R9, P0-02).

Класс дефекта: отпечаток считался по одному чтению файла, а работа шла по
ВТОРОМУ чтению того же пути. Между ними у этапа 2 проходили минуты
(проверка до решения задачи, чтение — после), и подмена файла в этом окне
завершалась нулевым кодом возврата: сертификат судил по одним правилам,
расчёт — по другим.

Закрытие класса: тот, кто прочитал байты ради отпечатка, обязан передать
ИХ, а не путь. Загрузчик принимает байты, и второго обращения к диску не
существует — подменять между чтениями становится нечего.
"""
from __future__ import annotations

import json

import pytest

from _gerad_trajectory_check import load_equivalent_stations

def _rules(dep: str, arr: str) -> dict:
    return {"parameters": {"stationEquivalenceRules": {
        "teleportationCodesList": [
            {"departureStation": dep, "arrivalStation": arr,
             "minimumDuration": "PT36H"}]}}}


_RULES_A = _rules("AAA", "BBB")
_RULES_B = _rules("XXX", "YYY")


def test_loader_accepts_the_bytes_that_were_hashed(tmp_path):
    """Загрузчик работает по переданным байтам, а не по пути.

    Файл на диске подменяется СРАЗУ после чтения — как в мутации
    аудитора. Результат обязан соответствовать прочитанному, а не тому,
    что лежит на диске сейчас.
    """
    p = tmp_path / "source_rules.json"
    p.write_text(json.dumps(_RULES_A), encoding="utf-8")
    raw = p.read_bytes()                      # единственное чтение
    p.write_text(json.dumps(_RULES_B), encoding="utf-8")   # подмена

    equiv = load_equivalent_stations(raw, strict=True)
    assert ("AAA", "BBB") in equiv, (
        "работа обязана идти по прочитанным байтам; иначе отпечаток "
        "проверяет одно, а расчёт использует другое")
    assert ("XXX", "YYY") not in equiv


def test_path_form_still_supported_for_single_read_callers(tmp_path):
    """Форма с путём сохранена: её используют вызывающие, читающие файл
    ровно один раз. Сигнатура РАСШИРЕНА, а не заменена."""
    p = tmp_path / "source_rules.json"
    p.write_text(json.dumps(_RULES_A), encoding="utf-8")
    assert ("AAA", "BBB") in load_equivalent_stations(p, strict=True)


def test_absent_rules_still_mean_no_equivalence():
    """Закреплённое поведение: без правил ни одна пара станций не
    считается равнозначной (умолчание другой авиакомпании не
    подставляется)."""
    assert load_equivalent_stations(None) == {}


def test_dict_form_is_accepted_for_manifest_readers():
    """Разобранный документ принимается наравне с байтами — это форма для
    будущего манифеста постановки, где правила уже разобраны один раз."""
    assert ("AAA", "BBB") in load_equivalent_stations(_RULES_A, strict=True)


def test_strict_refuses_rules_without_the_section():
    """Строгий режим не подставляет зашитые пары умолчания."""
    with pytest.raises(ValueError):
        load_equivalent_stations({"parameters": {}}, strict=True)


def test_trajectory_cli_reads_the_rules_file_once(tmp_path, monkeypatch):
    """Сквозная проверка: командная строка сертификата обращается к файлу
    правил РОВНО один раз.

    Счётчик обращений — прямой способ доказать закрытие окна: пока чтений
    два, между ними всегда есть что подменить.
    """
    import _gerad_trajectory_check as tc
    from pathlib import Path

    p = tmp_path / "source_rules.json"
    p.write_text(json.dumps(_RULES_A), encoding="utf-8")

    reads = {"n": 0}
    real = Path.read_bytes

    def counting(self):
        if self == p:
            reads["n"] += 1
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    monkeypatch.setattr(tc, "check_inst",
                        lambda *a, **k: {"violations_total": 0,
                                         "violations": {}, "inst": 1,
                                         "tdh_transitions_verified": 0})
    monkeypatch.setattr(
        "sys.argv",
        ["_gerad_trajectory_check.py", "--source-rules", str(p),
         "--source-rules-fp",
         __import__("hashlib").sha256(p.read_bytes()).hexdigest(),
         "1"])
    reads["n"] = 0            # чтение ради отпечатка в argv не считаем
    tc.main()
    assert reads["n"] == 1, (
        f"файл правил прочитан {reads['n']} раз(а) — при двух чтениях "
        f"между ними остаётся окно подмены")
