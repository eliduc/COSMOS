"""Закрытие отсрочек аудита R10 по личности постановки:

№1 — colgen принимает правила источника (fp обязателен, числовые пределы
     применяются, равнозначные станции — отказ: формулировка их не
     выражает);
№2 — прямой CLI построителя не принимает --source-rules без отпечатка;
№3 — этап 2 сверяет контент-отпечаток каталога от тех же байтов.
"""
import hashlib
import json
import sys

import pytest

import _gerad_colgen as gcol
import _gerad_stage2_roster as st2
import _pairing_builder as pb


def _run_main(module, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prog"] + argv)
    return module.main()


# ---------------------------------------------------------- отсрочка №2

def test_builder_cli_source_rules_without_fp_refused(tmp_path, monkeypatch):
    rules = tmp_path / "sr.json"
    rules.write_text('{"parameters": {}}', encoding="utf-8")
    payload = tmp_path / "p.json"
    payload.write_text('{"flights": []}', encoding="utf-8")
    rc = _run_main(pb, ["--payload", str(payload), "--label", "t-x",
                        "--level", "3", "--source-rules", str(rules)],
                   monkeypatch)
    assert rc == 2


# ---------------------------------------------------------- отсрочка №1

def test_colgen_source_rules_without_fp_refused(tmp_path, monkeypatch):
    rules = tmp_path / "sr.json"
    rules.write_text('{"parameters": {}}', encoding="utf-8")
    rc = _run_main(gcol, ["0", "--payload", str(tmp_path / "p.json"),
                          "--source-rules", str(rules)], monkeypatch)
    assert rc == 2


def test_colgen_source_rules_fp_mismatch_refused(tmp_path, monkeypatch):
    rules = tmp_path / "sr.json"
    rules.write_text('{"parameters": {}}', encoding="utf-8")
    rc = _run_main(gcol, ["0", "--payload", str(tmp_path / "p.json"),
                          "--source-rules", str(rules),
                          "--source-rules-fp", "0" * 64], monkeypatch)
    assert rc == 2


def test_colgen_refuses_station_equivalence(tmp_path, monkeypatch):
    """Равнозначные станции меняют пространство этапа 1, а формулировка
    colgen стыкует смены строго по одному аэропорту — тихое игнорирование
    дало бы каталог другой постановки; отказ всегда, не только в strict."""
    rules = tmp_path / "sr.json"
    rules.write_text(json.dumps({"parameters": {
        "stationEquivalenceRules": {"teleportationCodesList": [
            {"departureStation": "XXX", "arrivalStation": "YYY",
             "minimumDuration": "PT1H"}]}}}), encoding="utf-8")
    fp = hashlib.sha256(rules.read_bytes()).hexdigest()
    rc = _run_main(gcol, ["0", "--payload", str(tmp_path / "p.json"),
                          "--source-rules", str(rules),
                          "--source-rules-fp", fp], monkeypatch)
    assert rc == 2


def test_colgen_applies_numeric_source_limits(tmp_path, monkeypatch):
    """Числовые пределы источника применяются к формулировке (и
    печатаются перекрытиями) ИЗ ФАЙЛА ПРАВИЛ: каждый ассерт сверяет
    точное число файла, а не умолчание класса — умолчаний у класса
    больше нет, и прежняя сверка с голым CosRules доказывала бы
    протечку умолчаний. Числа файла — явная синтетика, отличная от
    профиля colgen. Прогон дальше падает только на отсутствующем
    payload — то есть ПОСЛЕ применения правил."""
    rules = tmp_path / "sr.json"
    rules.write_text(json.dumps({
        "work_time_limit_by_tier": [600, 700, 810],
        "min_sit_min": 41,
        "max_pairing_days": 5}), encoding="utf-8")
    fp = hashlib.sha256(rules.read_bytes()).hexdigest()
    old = (gcol.MAX_DUTY_SPAN_MIN, gcol.MIN_GROUND_TIME_MIN,
           gcol.MAX_PAIRING_MIN)
    try:
        with pytest.raises(FileNotFoundError):
            _run_main(gcol, ["0", "--payload", str(tmp_path / "нет.json"),
                             "--source-rules", str(rules),
                             "--source-rules-fp", fp], monkeypatch)
        assert gcol.MIN_GROUND_TIME_MIN == 41
        assert gcol.MAX_PAIRING_MIN == 5 * 1440
        assert gcol.MAX_DUTY_SPAN_MIN == 810
    finally:
        (gcol.MAX_DUTY_SPAN_MIN, gcol.MIN_GROUND_TIME_MIN,
         gcol.MAX_PAIRING_MIN) = old


# ---------------------------------------------------------- отсрочка №3

def test_stage2_catalog_fp_mismatch_refused(tmp_path):
    cat = tmp_path / "cat.json"
    cat.write_text('{"selected": []}', encoding="utf-8")
    with pytest.raises(st2.RulesMismatch, match="контент-отпечатком"):
        st2.run_one("t-x", tmp_path / "p.json", cat, 60, 7, inst=None,
                    results_path=tmp_path / "r.json",
                    catalog_fp="0" * 64)


def test_stage2_catalog_fp_match_reads_same_bytes(tmp_path):
    """Совпавший отпечаток проходит дальше (падение уже по содержимому
    каталога — нет правил в config_sig и т.д., но НЕ по отпечатку)."""
    cat = tmp_path / "cat.json"
    cat.write_text('{"selected": []}', encoding="utf-8")
    fp = hashlib.sha256(cat.read_bytes()).hexdigest()
    try:
        st2.run_one("t-x", tmp_path / "p.json", cat, 60, 7, inst=None,
                    results_path=tmp_path / "r.json", catalog_fp=fp)
    except st2.RulesMismatch as exc:
        assert "контент-отпечатком" not in str(exc)
    except Exception:                                   # noqa: BLE001
        pass
