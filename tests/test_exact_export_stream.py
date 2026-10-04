# -*- coding: utf-8 -*-
"""Стенд потокового экспорта точной модели (поручение владельца
04.09.2026: CPLEX обязан работать для разработки на всех наборах).

Известный ответ: байты MPS / LP / справочника у потоковой записи
совпадают с эталонной реализацией — прежней, копившей все строки
списком; на большой синтетической модели пик памяти потоковой записи
укладывается в малую долю того, что съедал список строк."""
from __future__ import annotations

import json
import random
import sys
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import _pairing_builder as _pb  # noqa: E402


# ------------------------------------------------------------ эталон
# Дословная копия прежней записи (_export_exact_model до 04.09.2026):
# список строк + "\n".join + write_text. Держится здесь как известный
# ответ стенда; менять запрещено.

def _legacy_mps(path, rows, cost, fixed_zero, n_all):
    L = ["NAME exact_cover", "ROWS", " N  COST"]
    for sense, name, _, _ in rows:
        L.append(" %s  %s" % (sense, name))
    L.append("COLUMNS")
    L.append("    MARKER                 'MARKER'"
             "                 'INTORG'")
    by_var = [[] for _ in range(n_all)]
    for sense, name, rhs, terms in rows:
        for ci, coef in terms:
            by_var[ci].append((name, coef))
    for ci in range(n_all):
        L.append("    x%d COST %d" % (ci, cost[ci]))
        for name, coef in by_var[ci]:
            L.append("    x%d %s %d" % (ci, name, coef))
    L.append("    MARKER                 'MARKER'"
             "                 'INTEND'")
    L.append("RHS")
    for sense, name, rhs, _ in rows:
        L.append("    RHS %s %d" % (name, rhs))
    L.append("BOUNDS")
    for ci in range(n_all):
        L.append(" UP BND x%d %d" % (ci, 0 if ci in fixed_zero else 1))
    L.append("ENDATA")
    Path(path).write_text("\n".join(L) + "\n", encoding="ascii")


def _legacy_lp(path, rows, cost, fixed_zero, nvar, n_all):
    def terms_of(terms):
        return " ".join(("%+d x%d" % (coef, ci)) for ci, coef in terms)
    L = ["Minimize", " obj: " + " ".join(
        "%+d x%d" % (cost[ci], ci) for ci in range(nvar))]
    L.append("Subject To")
    for sense, name, rhs, terms in rows:
        op = "=" if sense == "E" else "<="
        L.append(" %s: %s %s %d" % (name, terms_of(terms), op, rhs))
    L.append("Bounds")
    for ci in sorted(fixed_zero):
        L.append(" x%d = 0" % ci)
    L.append("Binary")
    L.append(" " + " ".join("x%d" % ci for ci in range(n_all)))
    L.append("End")
    Path(path).write_text("\n".join(L) + "\n", encoding="ascii")


def _legacy_map(path, cands, cost, aux_map, cons_map, tail):
    var_map = {"x%d" % ci: {"fns": sorted(c["legs"]),
                            "dh": sorted(c.get("dh") or ()),
                            "cost": cost[ci],
                            "crew": c.get("crew")}
               for ci, c in enumerate(cands)}
    aux_vars = {"x%d" % yv: {"aux": "покрытие рейса исполнителем",
                             "fn": lid}
                for yv, lid in sorted(aux_map.items())}
    doc = {"variables": var_map, "aux_variables": aux_vars,
           "constraints": cons_map}
    doc.update(tail)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False),
                          encoding="utf-8")


# ------------------------------------------------------------ модель

def _synthetic(nvar: int, nrows: int, seed: int = 7, with_aux: int = 3):
    """Случайная модель в форме _selection_rows: строки (sense, name,
    rhs, [(ci, coef)]), цена, нулевые границы, вспомогательные."""
    rnd = random.Random(seed)
    n_all = nvar + with_aux
    rows = []
    cons_map = {}
    for ri in range(nrows):
        k = rnd.randint(1, 4)
        idx = sorted(rnd.sample(range(n_all), k))
        sense = "E" if ri % 3 == 0 else "L"
        name = "c%d" % ri
        rows.append((sense, name, 1 if sense == "E" else rnd.randint(1, 3),
                     [(ci, rnd.choice((1, 1, 1, -1, 2))) for ci in idx]))
        cons_map[name] = ({"kind": "cover", "flight": "F%d" % ri}
                          if sense == "E" else {"kind": "at_most_one",
                                                "flight": "F%d" % ri})
    # одна строка без слагаемых — крайний случай форматирования
    rows.append(("L", "c%d" % len(rows), 0, []))
    cons_map["c%d" % (len(rows) - 1)] = {"kind": "dh_limit", "cap": 0}
    cost = [rnd.randint(0, 999) for _ in range(nvar)] + [0] * with_aux
    fixed_zero = set(rnd.sample(range(nvar), max(1, nvar // 10)))
    cands = [{"legs": ["F%d" % rnd.randint(0, 99),
                       "F%d" % rnd.randint(0, 99)],
              "dh": (["D%d" % ci] if ci % 5 == 0 else []),
              "crew": 2 if ci % 2 else 3}
             for ci in range(nvar)]
    aux_map = {nvar + j: "F%d" % j for j in range(with_aux)}
    return rows, cons_map, cost, fixed_zero, cands, aux_map


_TAIL = {"objective": {"sense": "min", "kind": "стоимость кандидатов "
                                              "(критерий профиля запуска)"},
         "pool": 0, "exact": True, "obligations": 0,
         "uncoverable_flights": ["F-НЕТ"], "fixed_zero_vars": []}


def test_поток_байт_в_байт_как_эталон(tmp_path):
    rows, cons_map, cost, fixed_zero, cands, aux_map = _synthetic(300, 120)
    nvar, n_all = len(cands), len(cost)
    tail = dict(_TAIL, pool=nvar,
                obligations=sum(1 for v in cons_map.values()
                                if v["kind"] == "cover"),
                fixed_zero_vars=sorted("x%d" % i for i in fixed_zero))
    _legacy_mps(tmp_path / "a.mps", rows, cost, fixed_zero, n_all)
    _pb.write_exact_mps(tmp_path / "b.mps", rows, cost, fixed_zero, n_all)
    assert (tmp_path / "a.mps").read_bytes() == (tmp_path / "b.mps").read_bytes()
    _legacy_lp(tmp_path / "a.lp", rows, cost, fixed_zero, nvar, n_all)
    _pb.write_exact_lp(tmp_path / "b.lp", rows, cost, fixed_zero, nvar, n_all)
    assert (tmp_path / "a.lp").read_bytes() == (tmp_path / "b.lp").read_bytes()
    _legacy_map(tmp_path / "a.map.json", cands, cost, aux_map, cons_map, tail)
    _pb.write_exact_map(tmp_path / "b.map.json", cands, cost, aux_map,
                        cons_map, tail)
    assert (tmp_path / "a.map.json").read_bytes() \
        == (tmp_path / "b.map.json").read_bytes()
    # справочник читается и полон
    m = json.loads((tmp_path / "b.map.json").read_text(encoding="utf-8"))
    assert len(m["variables"]) == nvar and len(m["aux_variables"]) == 3
    assert m["pool"] == nvar and m["exact"] is True


def test_пустая_модель_и_модель_без_вспомогательных(tmp_path):
    for nvar, nrows, aux in ((0, 0, 0), (5, 2, 0)):
        rows, cons_map, cost, fixed_zero, cands, aux_map = _synthetic(
            nvar, nrows, with_aux=aux) if nvar else ([], {}, [], set(), [], {})
        nvar2, n_all = len(cands), len(cost)
        for name, legacy, stream in (
                ("mps", _legacy_mps, _pb.write_exact_mps),
                ("lp", _legacy_lp, _pb.write_exact_lp)):
            args = (rows, cost, fixed_zero, n_all) if name == "mps" \
                else (rows, cost, fixed_zero, nvar2, n_all)
            legacy(tmp_path / ("a.%s" % name), *args)
            stream(tmp_path / ("b.%s" % name), *args)
            assert (tmp_path / ("a.%s" % name)).read_bytes() \
                == (tmp_path / ("b.%s" % name)).read_bytes(), name
        _legacy_map(tmp_path / "a.map.json", cands, cost, aux_map, cons_map,
                    _TAIL)
        _pb.write_exact_map(tmp_path / "b.map.json", cands, cost, aux_map,
                            cons_map, _TAIL)
        assert (tmp_path / "a.map.json").read_bytes() \
            == (tmp_path / "b.map.json").read_bytes()


def test_пик_памяти_потоковой_записи_мал(tmp_path):
    """300 000 строк, ~750 000 ненулей, 100 000 переменных: пик памяти
    потоковой записи MPS+LP — доли того, что копил список строк
    (у эталона на такой модели — сотни мегабайт строк Python)."""
    rows, cons_map, cost, fixed_zero, cands, aux_map = _synthetic(
        100_000, 300_000, seed=11)
    nvar, n_all = len(cands), len(cost)
    tracemalloc.start()
    _pb.write_exact_mps(tmp_path / "big.mps", rows, cost, fixed_zero, n_all)
    _pb.write_exact_lp(tmp_path / "big.lp", rows, cost, fixed_zero, nvar, n_all)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 60 * 1024 * 1024, "пик памяти %.1f МБ" % (peak / 2**20)
    # файлы целы: число строк ROWS и COLUMNS сходится с моделью
    text = (tmp_path / "big.mps").read_text(encoding="ascii")
    assert text.count("\n UP BND ") == n_all
    assert text.startswith("NAME exact_cover\n") and text.endswith("ENDATA\n")
