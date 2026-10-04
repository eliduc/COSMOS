# -*- coding: utf-8 -*-
"""Дифференциальный оракул полноты ценообразования (аудит R10, пакет A).

Рецепт аудитора: перепись по именам вызовов слабее проверки СМЫСЛА.
Если прайсер объявил полноту, полный перебор без эвристических капов не
должен находить ни одной цепочки, которой нет в капованном прогоне; а
каждая цепочка, потерянная капованным прогоном, обязана объясняться
ЗАПИСАННЫМ усечением. Плюс «тест теста»: реинъекция немоты приёмника
обязана краснить оракул.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

import _cos_rules  # noqa: E402
import _pairing_builder as _pb  # noqa: E402
import _pricing_certificate as _pc  # noqa: E402
from test_pairing_builder import _args, _leg, _payload  # noqa: E402

UNCAPPED = dict(depth=30, branch_duty=99, branch_chain=99, per_duty=10000,
                dh_options=8, budget_s=30)


def _unit_keys(catalog):
    return {tuple(sorted(u.get("fns") or ())) for u in catalog["selected"]}


def oracle(payload, capped_over, rules=None):
    """Свойства полноты и объяснимости на паре прогонов capped/uncapped.

    Возвращает (нарушения, capped_report) — пустой список нарушений
    означает: либо капованный прогон полон, либо каждая потеря объяснена
    записанным усечением.
    """
    a1 = _args(method="columns", **capped_over)
    a2 = _args(method="columns", **UNCAPPED)
    if rules is not None:
        a1.cos_rules = rules
        a2.cos_rules = rules
    cat1, rep1 = _pb.build(payload, a1)
    cat2, rep2 = _pb.build(payload, a2)
    # Потери меряются по ПУЛУ КАНДИДАТОВ, а не по выбранному покрытию:
    # отбор из разных пулов может выбрать одинаковое покрытие, и разница
    # видна только в числе кандидатов (мета-мутация «онемевший приёмник»
    # это доказала: пул 26 против 30 при одинаковом каталоге).
    missing = _unit_keys(cat2) - _unit_keys(cat1)
    pool_lost = int(rep2.get("candidates") or 0) - int(
        rep1.get("candidates") or 0)
    complete = bool(rep1.get("pricing_complete"))
    rows = list(rep1.get("pricing_truncations") or [])
    caps = set(rep1.get("pricing_caps") or [])
    violations = []
    if complete and (missing or pool_lost > 0):
        violations.append(
            "полнота объявлена, но полный перебор нашёл больше: "
            "юнитов +%d, кандидатов +%d" % (len(missing), pool_lost))
    if (missing or pool_lost > 0) and not (rows or caps):
        violations.append(
            "кандидаты потеряны (юнитов %d, пул +%d), а перечень "
            "усечений пуст" % (len(missing), pool_lost))
    return violations, rep1


# --------------------------- батарея решётки заниженных капов -----------

GRID = [dict(depth=1), dict(depth=2), dict(branch_duty=1),
        dict(branch_chain=1), dict(per_duty=1), dict(dh_options=1)]


def _net_payload():
    """Сеть на 8 участков с веером продолжений и ночёвкой."""
    legs = [
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        _leg("F3", "AAA", "BASE", "2026-08-01T11:00:00", 120),
        _leg("F4", "BASE", "AAA", "2026-08-01T14:00:00", 120),
        _leg("F5", "AAA", "BBB", "2026-08-02T07:00:00", 90),
        _leg("F6", "BBB", "BASE", "2026-08-02T10:30:00", 150),
        _leg("F7", "BASE", "BBB", "2026-08-01T07:00:00", 150),
        _leg("F8", "BBB", "AAA", "2026-08-02T08:00:00", 90),
    ]
    return _payload(legs)


@pytest.mark.parametrize("capped", GRID,
                         ids=lambda d: ",".join(f"{k}={v}"
                                                for k, v in d.items()))
def test_oracle_grid_completeness_or_explained(capped):
    violations, _rep = oracle(_net_payload(), capped)
    assert not violations, violations


# --------------------------- мутации аудита -----------------------------

def _tele_rules():
    r = _cos_rules.from_parameters({
        "stationEquivalenceRules": {
            "@type": "equivalentAndTeleportationWithTimeLimit",
            "allowRestOffBase": True,
            "teleportationCodesList": [
                {"symmetric": True, "arrivalStation": "YYY",
                 "minimumDuration": "PT0S", "departureStation": "XXX"}]},
    }, strict=False)
    # Пакет «умолчания-в-данные»: умолчаний заказчика у CosRules больше
    # нет — фикстура доопределяет ПОЛНЫЙ явный набор синтетики тестового
    # мира (значения — как у test_pairing_builder._rules); телепортация
    # остаётся из вендор-раздела выше — она и есть предмет теста.
    r.work_time_limit_by_tier = (720, 840, 960)
    r.layover_tiers = (
        _cos_rules.LayoverTier(720, 720, 760, 1440, 760, 1440, 1440),
        _cos_rules.LayoverTier(840, 2160, 1480, 2160, 1480, 2880, 1440),
        _cos_rules.LayoverTier(960, 2160, 1480, 2160, 1480, 2880, 1440),
    )
    r.min_sit_min = 50
    r.max_pairing_days = 6
    r.base_tz_offset_min = 300
    r.trans_tz_hours = 3.0
    r.trans_leg_min = 720
    r.dh_board_after_debrief_min = 20
    return r


def test_depth_witness_sees_teleport_space():
    """Мутация №1 аудита: продолжение существует только с равнозначной
    станции. Свидетель, смотревший в by_dep[станции], усечение ПРОПУСКАЛ
    (полнота лгала); честный свидетель пишет depth~."""
    legs = [
        _leg("A1", "BASE", "XXX", "2026-08-01T06:00:00", 120),
        _leg("B1", "YYY", "BASE", "2026-08-02T09:00:00", 120),
    ]
    payload = _payload(legs)
    # sit_min=50 явно — равным min_sit_min правил фикстуры (сцепка
    # тестов с cos_rules, см. комментарий у test_pairing_builder._args).
    a = _args(method="columns", depth=1, sit_min=50)
    a.cos_rules = _tele_rules()
    _cat, rep = _pb.build(payload, a)
    rows = rep.get("pricing_truncations") or []
    assert any(r.get("name") == "depth~" for r in rows), rows
    assert rep.get("pricing_complete") is False
    a2 = _args(method="columns", depth=2, sit_min=50)
    a2.cos_rules = _tele_rules()
    cat2, _ = _pb.build(payload, a2)
    assert ("A1", "B1") in _unit_keys(cat2)


def test_one_stop_transfer_is_order_invariant():
    """Мутация №2 аудита: перестановка массива рейсов не меняет
    математический ответ — либо оба порядка находят юнит, либо усечение
    dh_transfer_first_only записано."""
    base = [
        _leg("W1", "CCC", "BASE", "2026-08-01T18:00:00", 120),
        _leg("L1", "BASE", "MID", "2026-08-01T06:00:00", 90),
        _leg("BAD", "MID", "CCC", "2026-08-01T09:00:00", 60),
        _leg("GOOD", "MID", "CCC", "2026-08-01T10:30:00", 60),
    ]
    flipped = [base[0], base[1], base[3], base[2]]
    outs = []
    for legs in (base, flipped):
        a = _args(method="columns", dh_options=1)
        cat, rep = _pb.build(_payload(legs), a)
        rows = {r.get("name") for r in
                (rep.get("pricing_truncations") or [])}
        outs.append((_unit_keys(cat), rows))
    same_units = outs[0][0] == outs[1][0]
    explained = ("dh_transfer_first_only" in outs[0][1]
                 and "dh_transfer_first_only" in outs[1][1])
    assert same_units or explained, outs


# --------------------------- мета-мутации («тест теста») ----------------

def test_oracle_catches_muted_recorder(monkeypatch):
    """Реинъекция дефекта класса: приёмник усечений онемел (record —
    no-op). Факт (проверен вручную): капованный прогон объявляет
    полноту при пуле 26 против 30 у полного. Оракул ОБЯЗАН покраснеть —
    иначе он театр."""
    monkeypatch.setattr(_pc.Truncations, "record",
                        lambda self, name, limit, observed: None)
    violations, rep = oracle(_net_payload(), dict(per_duty=1))
    assert violations, ("оракул не заметил ложную полноту: complete=%s"
                        % rep.get("pricing_complete"))


def test_oracle_catches_fully_muted_certificate(monkeypatch):
    """Полное онемение (record no-op + take без подсматривания):
    ни одной записи — потери обязаны читаться оракулом как нарушение."""
    monkeypatch.setattr(_pc.Truncations, "record",
                        lambda self, name, limit, observed: None)

    def blind_take(source, k, *, sink, name):
        out = []
        for item in source:
            if len(out) >= k:
                break
            out.append(item)
        return out

    monkeypatch.setattr(_pc, "take", blind_take)
    violations, rep = oracle(_net_payload(), dict(dh_options=1))
    assert violations or rep.get("pricing_complete") is not True
