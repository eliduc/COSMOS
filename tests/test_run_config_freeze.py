"""Заморозка конфигурации на прогон и сверка половин (P1-10 / P1-13).

Прогон обязан читать СНИМОК, а не живой файл: правку, сделанную между
этапом 1 и этапом 2, половины системы иначе увидят по-разному, и
расхождение будет тихим. Артефакты обеих половин обязаны нести отпечаток
правил и личность прогона, иначе «по чему это посчитано» приходится
угадывать.
"""
import json
import sys
from pathlib import Path

import pytest

import rules_config
from webapp import config_scope, jobs

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from _gerad_stage2_roster import (  # noqa: E402
    RulesMismatch, _assert_same_rules, _rules_provenance)


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    root = tmp_path / "config"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        root / "airline_default.json")
    for domain in config_scope.DOMAINS:
        monkeypatch.setitem(config_scope.INSTANCE_DIRS, domain,
                            root / f"instance-{domain}")
    monkeypatch.setattr(config_scope, "RUNS_CONFIG_DIR", tmp_path / "runs")
    return root


# --- снимок -------------------------------------------------------------------

def test_freeze_writes_every_domain_and_a_manifest(iso, tmp_path):
    res = config_scope.freeze_run("run0001", "gerad-1")
    for domain in config_scope.DOMAINS:
        assert Path(res["files"][domain]).exists()
        assert res["fingerprints"][domain]
    manifest = json.loads((res["dir"] / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run0001"
    assert manifest["instance"] == "gerad-1"
    assert manifest["fingerprints"] == res["fingerprints"]


def test_frozen_rules_are_readable_by_the_engine_loader(iso):
    config_scope.write_instance(config_scope.DOMAIN_RULES, "gerad-1",
                                {**rules_config.DEFAULTS, "min_sit_min": 55})
    res = config_scope.freeze_run("run0002", "gerad-1")
    assert rules_config.load_from(
        res["files"]["rules"])["min_sit_min"] == 55


def test_a_later_edit_does_not_reach_a_frozen_run(iso):
    """Суть заморозки: правка между этапами не догоняет уже идущий прогон."""
    config_scope.write_instance(config_scope.DOMAIN_RULES, "gerad-1",
                                {**rules_config.DEFAULTS, "min_sit_min": 30})
    res = config_scope.freeze_run("run0003", "gerad-1")
    config_scope.write_instance(config_scope.DOMAIN_RULES, "gerad-1",
                                {**rules_config.DEFAULTS, "min_sit_min": 90})
    assert rules_config.load_from(
        res["files"]["rules"])["min_sit_min"] == 30
    # а живое разрешение уже видит правку
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 90


def test_rates_are_frozen_too(iso):
    """Отчёт с невоспроизводимыми денежными числами хуже отчёта с
    невоспроизводимыми правилами: ставки замораживаются наравне."""
    res = config_scope.freeze_run("run0004", "gerad-1")
    assert "cost" in res["files"] and "pay" in res["files"]
    from webapp import costing, pay_credit
    assert costing.load_cost_config_from(res["files"]["cost"])["currency"]
    assert pay_credit.load_pay_config_from(res["files"]["pay"])["duty_rig"] > 0


def test_freeze_is_used_by_the_job_launcher(iso, monkeypatch):
    frozen = jobs._freeze_run_config("run0005", {"id": "gerad-1",
                                                 "label": "inst1"})
    assert frozen is not None
    assert Path(frozen["files"]["rules"]).exists()


def test_a_failed_snapshot_does_not_block_the_run(iso, monkeypatch):
    """Отказ снимка не имеет права отменить запуск — прогон пойдёт по
    живым файлам, как раньше; но след обязан остаться."""
    def boom(*_a, **_kw):
        raise OSError("том только для чтения")
    monkeypatch.setattr(config_scope, "freeze_run", boom)
    assert jobs._freeze_run_config("run0006", {"id": "gerad-1"}) is None


def test_drop_run_removes_the_snapshot(iso):
    res = config_scope.freeze_run("run0007", "gerad-1")
    assert res["dir"].exists()
    config_scope.drop_run("run0007")
    assert not res["dir"].exists()
    config_scope.drop_run("run0007")           # повтор безопасен


# --- сверка половин -----------------------------------------------------------

def test_stage2_refuses_a_catalog_built_under_other_rules(iso, tmp_path):
    """Поле называется `config_sig` — так его пишет построитель. Первая
    версия проверки читала `config_signature`, которого в настоящем
    каталоге нет, и не срабатывала НИКОГДА; этот тест был зелёным, потому
    что подставлял выдуманное поле руками (повторный аудит 2026-07-26)."""
    doc = {"selected": [],
           "config_sig": {"rules_fingerprint": "deadbeefdeadbeef"}}
    with pytest.raises(RulesMismatch) as exc:
        _assert_same_rules(doc, tmp_path / "gerad_colgen_selected_x.json")
    assert "deadbeefdeadbeef" in str(exc.value) and "ОТКАЗ" in str(exc.value)


def test_stage2_accepts_a_catalog_with_the_same_fingerprint(iso, tmp_path):
    ours = _rules_provenance()["rules_fingerprint"]
    doc = {"selected": [], "config_sig": {"rules_fingerprint": ours}}
    _assert_same_rules(doc, tmp_path / "c.json")          # без исключения


def test_stage2_refuses_an_incomplete_search_catalog(iso, tmp_path,
                                                     monkeypatch):
    """Вердикт покрытия лежит ВНУТРИ каталога — переименование файла
    `.pending.json` гейт не обходит. 2026-07-28 ровно так на этап 2
    уехал каталог с восемью достижимыми, но не построенными участками."""
    from _gerad_stage2_roster import _assert_publishable
    doc = {"selected": [],
           "coverage": {"verdict": "INCOMPLETE_SEARCH", "not_built": 8}}
    monkeypatch.delenv("CREW_ACCEPT_INCOMPLETE_CATALOG", raising=False)
    with pytest.raises(RulesMismatch) as exc:
        _assert_publishable(doc, tmp_path / "renamed_clean.json")
    assert "INCOMPLETE_SEARCH" in str(exc.value) and "ОТКАЗ" in str(exc.value)


def test_stage2_accepts_incomplete_only_by_explicit_consent(
        iso, tmp_path, monkeypatch, capsys):
    """Осознанный запуск возможен, но только вслух — и с предупреждением."""
    from _gerad_stage2_roster import _assert_publishable
    doc = {"selected": [],
           "coverage": {"verdict": "INCOMPLETE_SEARCH", "not_built": 8}}
    monkeypatch.setenv("CREW_ACCEPT_INCOMPLETE_CATALOG", "1")
    _assert_publishable(doc, tmp_path / "c.json")          # без исключения
    assert "ВНИМАНИЕ" in capsys.readouterr().out


def test_stage2_honors_consent_stamped_in_the_catalog(iso, tmp_path,
                                                      monkeypatch, capsys):
    """Согласие, данное при выпуске (--accept-incomplete-search), едет в
    отпечатке каталога — второго согласия на этапе 2 не требуется."""
    from _gerad_stage2_roster import _assert_publishable
    monkeypatch.delenv("CREW_ACCEPT_INCOMPLETE_CATALOG", raising=False)
    doc = {"selected": [],
           "coverage": {"verdict": "INCOMPLETE_SEARCH", "not_built": 3},
           "config_sig": {"accept_incomplete_search": True}}
    _assert_publishable(doc, tmp_path / "c.json")          # без исключения
    assert "ВНИМАНИЕ" in capsys.readouterr().out


def test_stage2_passes_complete_and_legacy_catalogs(iso, tmp_path,
                                                    monkeypatch):
    from _gerad_stage2_roster import RulesMismatch, _assert_publishable
    monkeypatch.delenv("CREW_ACCEPT_INCOMPLETE_CATALOG", raising=False)
    _assert_publishable({"selected": [],
                         "coverage": {"verdict": "OK"}},
                        tmp_path / "c.json")
    _assert_publishable({"selected": []}, tmp_path / "c.json")  # прежний
    # Неизвестный вердикт привилегию годного не получает (внешний аудит
    # R6, P0-05): раньше любой не-INCOMPLETE вердикт проходил молча.
    import pytest
    with pytest.raises(RulesMismatch):
        _assert_publishable({"selected": [],
                             "coverage": {"verdict": "COMPLETE"}},
                            tmp_path / "c.json")


def test_old_catalogs_without_a_fingerprint_still_run(iso, tmp_path):
    """Каталоги прежнего выпуска отпечатка не несут; обесценивать уже
    посчитанные наборы правка не должна. Второе имя поля (день жизни
    дефекта) тоже читается."""
    _assert_same_rules({"selected": []}, tmp_path / "c.json")
    _assert_same_rules({"selected": [], "config_sig": {}}, tmp_path / "c.json")
    with pytest.raises(RulesMismatch):
        _assert_same_rules(
            {"selected": [],
             "config_signature": {"rules_fingerprint": "0badc0de0badc0de"}},
            tmp_path / "c.json")


def test_contract_on_the_real_builder_output(iso, tmp_path, monkeypatch):
    """КОНТРАКТНЫЙ тест: подпись берётся из НАСТОЯЩЕГО каталога, который
    выпускает построитель, — не из выдуманного словаря. Ловит расхождение
    имён поля между писателем и читателем, которое ручные фикстуры
    скрывали."""
    import argparse

    from _pairing_builder import Builder
    monkeypatch.setenv("CREW_RUN_ID", "feed0000feed")
    args = argparse.Namespace(
        level=3, method="enumerate", budget_s=1, solve_s=1, seed=7,
        bases=["AAA"], w_night=1, w_dh=1, duty_span=600, pairing_span=2880,
        max_segs=5, sit_min=30, rescue_dh=0, rescue_per_leg=0,
        rules_fingerprint="feedfacefeedface", rules_effective={},
        rules_overridden=[])
    b = Builder.__new__(Builder)
    b.args = args
    # ровно так каталог собирает to_catalog(): поле называется config_sig
    catalog = {"selected": [], "config_sig": b.config_signature()}
    with pytest.raises(RulesMismatch) as exc:
        _assert_same_rules(catalog, tmp_path / "real.json")
    assert "feedfacefeedface" in str(exc.value)
    # и совпадающий отпечаток проходит
    args.rules_fingerprint = _rules_provenance()["rules_fingerprint"]
    catalog2 = {"selected": [], "config_sig": b.config_signature()}
    _assert_same_rules(catalog2, tmp_path / "real2.json")


# --- личность прогона в артефактах --------------------------------------------

def test_stage2_row_carries_rules_and_run_id(iso, monkeypatch):
    monkeypatch.setenv("CREW_RUN_ID", "abc123abc123")
    prov = _rules_provenance()
    assert prov["run_id"] == "abc123abc123"
    assert prov["rules_fingerprint"] and prov["rules_effective"]


def test_catalog_signature_carries_run_id(monkeypatch):
    """P1-13: у каталога стояло `run_id: null` — теперь личность прогона
    доходит до артефакта первого этапа."""
    import argparse

    from _pairing_builder import Builder
    monkeypatch.setenv("CREW_RUN_ID", "cafe0000cafe")
    args = argparse.Namespace(
        level=3, method="enumerate", budget_s=1, solve_s=1, seed=7,
        bases=["AAA"], w_night=1, w_dh=1, duty_span=600, pairing_span=2880,
        max_segs=5, sit_min=30, rescue_dh=0, rescue_per_leg=0,
        rules_fingerprint="ffff0000ffff0000", rules_effective={},
        rules_overridden=[])
    b = Builder.__new__(Builder)
    b.args = args
    assert b.config_signature()["run_id"] == "cafe0000cafe"
