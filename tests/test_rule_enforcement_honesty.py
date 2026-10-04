"""Честность про то, КАК добывается каждое правило (укрепление яруса 2).

Правило бывает двух родов, и разница операционная, а не терминологическая:

  * солвер ИЩЕТ решение, удовлетворяющее правилу — тогда включение
    правила меняет результат;
  * солвер о правиле НЕ ЗНАЕТ, а сертификат проверяет его постфактум —
    тогда включение правила не улучшает ростер, а лишь заставляет вердикт
    браковать нарушающие. Автоматического пути к легальному ростеру у
    системы в этом случае НЕТ.

Такое правило уже выпущено: `comp_language_mix` разбирается обеими
половинами, выставлен в интерфейсе и проверяется питоновским
сертификатом, но ни одно ограничение солвера его не использует. Оператор,
включивший его, видел бы повторяющийся отказ вердикта и прочёл бы это как
поломку солвера. Тесты ниже закрепляют, что различие названо вслух — в
данных и на экране.
"""
import re

import pytest

from webapp import config_scope

import rules_config
from webapp import registry

PROVIDERS = (registry.REPO_ROOT / "kotlin-solver" / "src" / "main" / "kotlin" /
             "org" / "acme" / "flighcrewscheduling" / "solver")


ENGINE_FILES = ("_gerad_colgen.py", "_gerad_trajectory_check.py",
                "_gerad_leg_feasibility.py")


def _provider_sources() -> str:
    if not PROVIDERS.exists():
        pytest.skip("нет исходников солвера")
    out = []
    for p in sorted(PROVIDERS.glob("*ConstraintProvider.kt")):
        out.append(p.read_text(encoding="utf-8"))
    assert out, "не найдено ни одного ConstraintProvider"
    return "\n".join(out)


def _pairing_engine_sources() -> str:
    return "\n".join((registry.REPO_ROOT / f).read_text(encoding="utf-8")
                     for f in ENGINE_FILES
                     if (registry.REPO_ROOT / f).exists())


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _used_by_pairing_engine(key: str, src: str) -> bool:
    """Движок первого этапа связывает правило с модульной константой и
    дальше работает с ней — искать надо константу, а не ключ."""
    consts = re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*_RULES\["%s"\]' % key,
                        src, re.M)
    return any(re.search(r"\b%s\b" % c, src) for c in consts)


def test_every_rule_has_a_declared_enforcement():
    """Ни одно правило не должно быть «неизвестно как добывается»."""
    unknown = [k for k in rules_config.DEFAULTS
               if rules_config.enforcement(k) == "unknown"]
    assert not unknown, f"правила без объявленного способа добычи: {unknown}"


def test_enforcement_sets_do_not_overlap_and_cover_everything():
    assert not (rules_config.ENFORCED_BY_SOLVER & rules_config.CERTIFIED_ONLY)
    assert (rules_config.ENFORCED_BY_SOLVER | rules_config.CERTIFIED_ONLY
            == set(rules_config.DEFAULTS))


def test_declared_engines_match_the_code():
    """Объявленный движок обязан действительно использовать правило.

    Проверяются ОБА направления: заявили «ищет» — пусть найдётся; заявили
    «не ищет» — пусть не найдётся. Односторонняя проверка пропустила бы
    ровно тот дефект, из-за которого появился этот файл."""
    kt, py = _provider_sources(), _pairing_engine_sources()
    wrong = {}
    for key, engines in sorted(rules_config.ENFORCED_BY.items()):
        actual = []
        if _used_by_pairing_engine(key, py):
            actual.append("pairing")
        if _camel(key) in kt:
            actual.append("roster")
        if tuple(actual) != tuple(engines):
            wrong[key] = {"объявлено": engines, "в коде": tuple(actual)}
    assert not wrong, f"объявленные движки разошлись с кодом: {wrong}"


def test_the_known_unenforced_rule_stays_declared_as_such():
    """Правило состава по языкам не ищет никто — ни один движок.

    Тест закрепляет ФАКТ, а не мирится с ним: как только в солвере
    появится ограничение, тест выше потребует обновить объявление, и
    пометка на экране исчезнет вместе с ним."""
    assert rules_config.ENFORCED_BY["comp_language_mix"] == ()
    assert rules_config.enforcement("comp_language_mix") == "certificate"


def test_api_ships_the_enforcement_map(tmp_path, monkeypatch):
    """Экран обязан получить признак с сервера: подпись на клиенте по
    списку зашитых имён разошлась бы с кодом при первом же изменении."""
    from webapp import app as app_mod

    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        tmp_path / "airline.json")
    cfg = app_mod._read_airline_config()
    assert set(cfg["enforcement"]) == set(cfg["parameters"])
    assert cfg["enforcement"]["min_sit_min"] == "solver"
    assert cfg["enforcement"]["comp_language_mix"] == "certificate"


def test_client_labels_certificate_only_rules():
    """Пометка рисуется по признаку с сервера, а не по зашитому списку."""
    src = (registry.REPO_ROOT / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8")
    assert 'enforcement[key] === "certificate"' in src
    assert 't("app.cfg.certOnly")' in src


def test_rule_pack_vocabulary_is_derived_not_restated():
    """Словарь пакета правил выводится из rules_config.

    Рукописная копия уже отставала: comp_* появились в словаре и сюда не
    попали. Тест закрепляет вывод, а не список."""
    from webapp import rule_packs

    expected = tuple(sorted(set(rules_config.DEFAULTS)
                            - rules_config.COMPOSITION_KEYS))
    assert rule_packs.PARAMETERS == expected


def test_config_write_is_atomic():
    """Прямая запись оставляет окно с усечённым файлом, а обе половины
    системы на битом конфиге падают громко."""
    src = (registry.REPO_ROOT / "webapp" / "app.py").read_text(
        encoding="utf-8")
    m = re.search(r"def _write_airline_params\(.*?\n    return "
                  r"_read_airline_config\(\)", src, re.S)
    assert m, "не найдена _write_airline_params"
    body = m.group(0)
    assert ".replace(target)" in body
    assert "target.write_text(" not in body
