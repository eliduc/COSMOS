# -*- coding: utf-8 -*-
"""Стенд потоковой записи модели отбора внешних движков
(_select_engines._write_lp; поручение владельца 04.09.2026: CPLEX на
всех наборах). Известный ответ: байты LP у потоковой записи равны
эталонной копии прежней записи (список строк + join), включая
прослабления, запреты пар и групп; пик памяти на большой модели мал."""
from __future__ import annotations

import random
import sys
import tracemalloc
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import _select_engines as se  # noqa: E402


def _legacy_write_lp(path, rows, cons_map, cost, fixed_zero,
                     hard_names, ban_pairs, ban_groups):
    """Дословная копия _write_lp до 04.09.2026 — эталон стенда."""
    big_m = sum(int(c) for c in cost) + 1
    slacks = []
    L = ["Minimize", " obj: " + " + ".join(
        "%d x%d" % (int(cost[ci]), ci)
        for ci in range(len(cost)))]
    body = ["Subject To"]
    for sense, name, rhs, terms in rows:
        kind = (cons_map.get(name) or {}).get("kind")
        expr = " ".join("%+d x%d" % (coef, ci) for ci, coef in terms)
        if kind == "cover" and name not in hard_names:
            body.append(" %s_u: %s <= 1" % (name, expr))
            body.append(" %s_c: %s +1 s_%s >= 1" % (name, expr, name))
            slacks.append("s_%s" % name)
        else:
            op = "=" if sense == "E" else "<="
            body.append(" %s: %s %s %d" % (name, expr, op, int(rhs)))
    for k, (bi, bj) in enumerate(ban_pairs or ()):
        body.append(" bp%d: +1 x%d +1 x%d <= 1" % (k, bi, bj))
    for k, idxs in enumerate(ban_groups or ()):
        if len(idxs) >= 2:
            body.append(" bg%d: %s <= %d" % (
                k, " ".join("+1 x%d" % i for i in idxs),
                len(idxs) - 1))
    if slacks:
        L[1] += " + " + " + ".join("%d %s" % (big_m, s)
                                   for s in slacks)
    L += body
    L.append("Bounds")
    for ci in sorted(fixed_zero):
        L.append(" x%d = 0" % ci)
    for s in slacks:
        L.append(" 0 <= %s <= 1" % s)
    L.append("Binary")
    L += [" x%d" % ci for ci in range(len(cost))]
    L.append("End")
    Path(path).write_text("\n".join(L) + "\n", encoding="ascii")
    return big_m, slacks


def _synthetic(nvar: int, nrows: int, seed: int = 3):
    rnd = random.Random(seed)
    rows, cons_map = [], {}
    for ri in range(nrows):
        idx = sorted(rnd.sample(range(nvar), rnd.randint(1, 4)))
        r = ri % 4
        if r in (0, 1):
            rows.append(("E", "c%d" % ri, 1, [(ci, 1) for ci in idx]))
            cons_map["c%d" % ri] = {"kind": "cover", "flight": "F%d" % ri}
        elif r == 2:
            rows.append(("L", "c%d" % ri, 1, [(ci, 1) for ci in idx]))
            cons_map["c%d" % ri] = {"kind": "at_most_one",
                                    "flight": "F%d" % ri}
        else:
            rows.append(("L", "c%d" % ri, 0,
                         [(idx[0], 1)] + [(ci, -1) for ci in idx[1:]]))
            cons_map["c%d" % ri] = {"kind": "dh_requires_covered_link",
                                    "flight": "F%d" % ri}
    cost = [rnd.randint(0, 500) for _ in range(nvar)]
    fixed_zero = set(rnd.sample(range(nvar), max(1, nvar // 20)))
    hard_names = {"c%d" % ri for ri in range(0, nrows, 8)}   # часть покрытий жёсткая
    ban_pairs = [tuple(sorted(rnd.sample(range(nvar), 2))) for _ in range(5)]
    ban_groups = [sorted(rnd.sample(range(nvar), 3)), [1]]    # группа из 1 — не пишется
    return rows, cons_map, cost, fixed_zero, hard_names, ban_pairs, ban_groups


def test_поток_байт_в_байт_как_эталон(tmp_path):
    m = _synthetic(200, 96)
    r1 = _legacy_write_lp(tmp_path / "a.lp", *m)
    r2 = se._write_lp(tmp_path / "b.lp", *m)
    assert r1 == r2
    assert (tmp_path / "a.lp").read_bytes() == (tmp_path / "b.lp").read_bytes()
    # имена прослаблений читаются тем же разборщиком, что у HiGHS-пути
    assert se._slack_names(tmp_path / "b.lp") == r2[1]


def test_крайние_случаи_без_прослаблений_и_пустая(tmp_path):
    rows, cons_map, cost, fixed_zero, _h, bp, bg = _synthetic(30, 12)
    # все покрытия жёсткие — прослаблений нет, слагаемое big_m не пишется
    hard = {n for n, v in cons_map.items() if v["kind"] == "cover"}
    for m in ((rows, cons_map, cost, fixed_zero, hard, bp, bg),
              ([], {}, [], set(), set(), [], []),
              (rows, cons_map, cost, set(), set(), [], [])):
        r1 = _legacy_write_lp(tmp_path / "a.lp", *m)
        r2 = se._write_lp(tmp_path / "b.lp", *m)
        assert r1 == r2
        assert (tmp_path / "a.lp").read_bytes() == (tmp_path / "b.lp").read_bytes()


def test_словарь_статусов_один_для_любого_движка():
    """Контракт независимости от решателя: статус любого движка — в
    словаре CP-SAT; сырые строки CPLEX и HiGHS не просачиваются."""
    n = se.normalize_status
    assert n("integer optimal solution", True) == "OPTIMAL"
    assert n("integer optimal, tolerance", True) == "OPTIMAL"
    assert n("kOptimal", True) == "OPTIMAL"
    assert n("OPTIMAL", True) == "OPTIMAL"
    assert n("integer infeasible", False) == "INFEASIBLE"
    assert n("kInfeasible", False) == "INFEASIBLE"
    assert n("time limit exceeded", True) == "FEASIBLE"
    assert n("time limit exceeded", False) == "UNKNOWN"
    assert n("kTimeLimit", True) == "FEASIBLE"
    assert n("integer feasible solution", True) == "FEASIBLE"
    assert n("?", False) == "UNKNOWN"
    assert n("", True) == "UNKNOWN"
    assert set(n(s, True) for s in ("abc", "kOptimal", "infeasible")) \
        <= set(se.STATUS_VOCABULARY)


def test_граница_cplex_из_журнала(tmp_path):
    log = tmp_path / "x-cplex.log"
    log.write_text("Root relaxation solution time = 0.00 sec.\n"
                   "Current MIP best bound =  1.8500000000e+03 (gap = 40)\n"
                   "Current MIP best bound =  1.8900000000e+03 (gap = 0.0000, 0.00%)\n"
                   "MIP - Integer optimal solution:  Objective =  1.8900000000e+03\n",
                   encoding="utf-8")
    assert se._cplex_bound_from_log(log) == 1890.0
    assert se._cplex_bound_from_log(tmp_path / "нет.log") is None
    (tmp_path / "пусто.log").write_text("Presolve eliminated all\n", encoding="utf-8")
    assert se._cplex_bound_from_log(tmp_path / "пусто.log") is None


@pytest.mark.parametrize("label", ["t", "пакет-т"])
def test_cplex_зовётся_именами_файлов_в_рабочем_каталоге(tmp_path, monkeypatch,
                                                         label):
    """Живой отказ 04.09 (COSMOS, каталог данных длиной ~230 знаков):
    интерактивный CPLEX не нашёл LP по полному пути. Известный ответ:
    командный файл несёт только имена, подпроцесс идёт с cwd=обмен.
    Второй известный ответ (стенд 04.09): метка с кириллицей — имена
    файлов обмена всё равно ASCII (командный файл пишется в ASCII, и
    кириллица в имени LP роняла движок), следы — под именами метки."""
    import subprocess
    # длинный и нелатинский рабочий каталог — как в каталоге данных
    wd = tmp_path / ("пакет-" + "x" * 150) / "button-pairing"
    wd.mkdir(parents=True)
    monkeypatch.setenv("CREW_CPLEX_EXE", str(tmp_path / "cplex.exe"))
    (tmp_path / "cplex.exe").write_bytes(b"")
    seen = {}

    def fake_run(argv, stdin=None, stdout=None, stderr=None, cwd=None,
                 timeout=None, **kw):
        seen["cwd"] = cwd
        seen["cmd_text"] = (Path(cwd) / "model.cmd").read_text(
            encoding="ascii")
        assert (Path(cwd) / "model.lp").is_file(), "LP не скопирован"
        assert all(ord(ch) < 128 for ch in
                   " ".join(p.name for p in Path(cwd).iterdir()))
        (Path(cwd) / "model.sol").write_text(
            '<?xml version="1.0"?><CPLEXSolution><header '
            'solutionStatusString="integer optimal solution"/>'
            '<variables><variable name="x0" value="1"/>'
            '<variable name="x1" value="0"/></variables></CPLEXSolution>',
            encoding="utf-8")

        class P:
            returncode = 0
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = [("E", "c0", 1, [(0, 1), (1, 1)])]
    cons = {"c0": {"kind": "cover", "flight": "A"}}
    res = se.solve("cplex", rows, cons, [10, 12], set(), {"c0"}, [], [],
                   30, wd, label)
    # Обмен — короткий временный каталог (не рабочий), только имена.
    assert Path(seen["cwd"]) != wd and len(str(seen["cwd"])) < len(str(wd))
    assert all(ord(ch) < 128 for ch in str(seen["cwd"])), seen["cwd"]
    assert "read model.lp\n" in seen["cmd_text"]
    assert "write model.sol\n" in seen["cmd_text"]
    assert "/" not in seen["cmd_text"] and "\\" not in seen["cmd_text"]
    assert res["picked"] == [0] and res["status"] == "integer optimal solution"
    # Следы обмена — в рабочем каталоге нажатия под именами метки;
    # временный каталог убран.
    stem = "select-%s-cplex" % label
    for name in (stem + ".cmd", stem + ".sol", stem + "-cplex.log"):
        assert (wd / name).is_file(), name
    assert not Path(seen["cwd"]).exists()


def test_пик_памяти_потоковой_записи_мал(tmp_path):
    """300 000 строк, 100 000 переменных: потоковая запись LP модели
    отбора — десятки мегабайт пика, а не сотни (список строк)."""
    m = _synthetic(100_000, 300_000, seed=5)
    tracemalloc.start()
    se._write_lp(tmp_path / "big.lp", *m)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 60 * 1024 * 1024, "пик памяти %.1f МБ" % (peak / 2**20)
    text = (tmp_path / "big.lp").read_text(encoding="ascii")
    assert text.startswith("Minimize\n obj: ") and text.endswith("End\n")
    assert text.count("\n x") >= 100_000
