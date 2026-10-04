# -*- coding: utf-8 -*-
"""Внешние движки фазы отбора покрытия (пакет «выбор движка»,
поручение владельца 2026-08-28).

Модель отбора у всех движков ОДНА — строки зеркала
`_selection_rows` построителя (менять синхронно с CP-SAT-сборкой):
переменная на кандидата, покрытие, «не более одного», лимиты
перевозок, ёмкости баз, «перегон требует исполнителя». Семантика
покрытия — лексикографика «покрытие первое»: строка покрытия получает
прослабление (slack) с ценой M = сумма всех стоимостей + 1, поэтому
любой непокрытый рейс дороже любого удорожания плана; жёсткие строки
(0055: двойное покрытие нелегально) прослабления не имеют.

Движки:
  * highs — open-source HiGHS (пакет highspy), модель файлом
    CPLEX-LP, решение в процессе;
  * cplex — IBM CPLEX файловым мостом (MPS + командный файл +
    разбор .sol); путь берётся из CREW_CPLEX_EXE либо стандартной
    установки.

Отказ ЧЕСТНЫЙ: недоступный движок — исключение EngineUnavailable с
причиной; молчаливых подмен движка нет.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

_CPLEX_DEFAULT = (r"C:\Program Files\IBM\ILOG"
                  r"\CPLEX_Studio_Community222\cplex\bin"
                  r"\x64_win64\cplex.exe")
#: Корни установок IBM ILOG (подменяются в тестах) и путь исполняемого
#: файла внутри каталога установки CPLEX Studio.
_CPLEX_STUDIO_ROOTS: tuple = ((Path(r"C:\Program Files\IBM\ILOG"),) if os.name == "nt"
                              else (Path("/opt/ibm/ILOG"),))
_CPLEX_REL: tuple = ("cplex", "bin",
                     "x64_win64" if os.name == "nt" else "x86-64_linux",
                     "cplex.exe" if os.name == "nt" else "cplex")


class EngineUnavailable(RuntimeError):
    """Движок отбора недоступен; причина — в сообщении."""


# Единый словарь статусов отбора для ЛЮБОГО движка (контракт
# независимости от решателя, владелец 04.09.2026: «код должен быть
# абсолютно одинаков для любых решателей»). Сырой статус движка
# сохраняется рядом (solver_status_raw), в решения и отчёты идёт
# словарь CP-SAT: OPTIMAL | FEASIBLE | INFEASIBLE | UNKNOWN.
STATUS_VOCABULARY = ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN")


def normalize_status(raw: str, picked_any: bool) -> str:
    """Статус движка (CPLEX / HiGHS / CP-SAT) → словарь STATUS_VOCABULARY.

    CPLEX: «integer optimal solution», «integer optimal, tolerance» →
    OPTIMAL; «time limit exceeded», «integer feasible …» → FEASIBLE при
    наличии выбора; «infeasible» → INFEASIBLE. HiGHS: kOptimal → OPTIMAL,
    kInfeasible → INFEASIBLE, предел времени/итераций → FEASIBLE при
    наличии выбора. Всё прочее — UNKNOWN (никаких догадок)."""
    s = (raw or "").strip().lower()
    if s in ("optimal", "koptimal") or "optimal" in s and "infeasible" not in s:
        return "OPTIMAL"
    if "infeasible" in s or "unbounded" in s:
        return "INFEASIBLE"
    if "feasible" in s or "limit" in s or "aborted" in s \
            or s in ("kfeasible", "feasible"):
        return "FEASIBLE" if picked_any else "UNKNOWN"
    if s in ("unknown", "kunknown", "?", ""):
        return "UNKNOWN"
    return "UNKNOWN"


def _studio_rank(name: str) -> tuple:
    """Порядок каталогов CPLEX_Studio*: новее выше, полная редакция выше
    Community при равной версии."""
    digits = "".join(ch for ch in name if ch.isdigit())
    return (int(digits) if digits else 0, "Community" not in name)


def cplex_candidates() -> list[str]:
    """Где ищется исполняемый файл CPLEX, по убыванию приоритета: явная
    переменная CREW_CPLEX_EXE → переменные установщика IBM
    CPLEX_STUDIO_DIR* (новейшая версия первой) → cplex в PATH → каталоги
    CPLEX_Studio* под корнями IBM ILOG → прежний зашитый путь. Без дублей.

    09.09.2026 на контуре Test legion CPLEX стоял не в каталоге
    Community222 машины разработки, и движок отказывал по единственному
    зашитому пути; молчаливой подмены движка по-прежнему нет — не нашли,
    отказ словами с перечнем мест поиска."""
    out: list[str] = []

    def add(p) -> None:
        s = str(p)
        if s and s not in out:
            out.append(s)

    env = os.environ.get("CREW_CPLEX_EXE")
    if env:
        # Явная настройка — единственный кандидат: неверный путь в ней
        # даёт честный отказ, а не молчаливый уход на другой экземпляр
        # CPLEX той же машины (стенд «недоступный движок — честный отказ»).
        add(env)
        return out
    for key in sorted((k for k in os.environ if k.startswith("CPLEX_STUDIO_DIR")),
                      reverse=True):
        home = os.environ.get(key)
        if home:
            add(Path(home).joinpath(*_CPLEX_REL))
    found = shutil.which("cplex")
    if found:
        add(found)
    for root in _CPLEX_STUDIO_ROOTS:
        try:
            names = sorted((p.name for p in Path(root).iterdir()
                            if p.is_dir() and p.name.startswith("CPLEX_Studio")),
                           key=_studio_rank, reverse=True)
        except OSError:
            names = []
        for name in names:
            add(Path(root) / name / Path(*_CPLEX_REL))
    add(_CPLEX_DEFAULT)
    return out


def cplex_exe() -> str:
    for exe in cplex_candidates():
        if Path(exe).is_file():
            return exe
    raise EngineUnavailable(
        "движок cplex: исполняемый файл не найден; искали: CREW_CPLEX_EXE, "
        "переменные установщика CPLEX_STUDIO_DIR*, cplex в PATH, каталоги "
        "CPLEX_Studio* под %s (проверено: %s); задайте CREW_CPLEX_EXE либо "
        "установите CPLEX" % (", ".join(str(r) for r in _CPLEX_STUDIO_ROOTS),
                              "; ".join(cplex_candidates()) or "—"))


def check_available(engine: str) -> None:
    if engine == "highs":
        try:
            import highspy                            # noqa: F401
        except ImportError as e:
            raise EngineUnavailable(
                "движок highs: пакет highspy не установлен "
                "(pip install highspy)") from e
    elif engine == "cplex":
        cplex_exe()
    else:
        raise EngineUnavailable("неизвестный движок отбора: %r"
                                % engine)


def _write_lp(path: Path, rows, cons_map, cost, fixed_zero,
              hard_names, ban_pairs, ban_groups):
    """Модель CPLEX-LP: цель + строки + запреты + прослабления.

    Запись ПОТОКОВАЯ (поручение владельца 04.09.2026: CPLEX обязан
    работать для разработки на всех наборах): строки уходят в файл по
    мере генерации, список строк и одна гигантская строка цели в
    памяти не собираются. Байты — те же, что у прежней записи (стенд
    tests/test_select_engines_stream.py сверяет с эталонной копией).
    """
    big_m = sum(int(c) for c in cost) + 1
    # Прослабления известны заранее: строки покрытия, не объявленные
    # жёсткими, — по порядку строк (тот же порядок, что у записи тела).
    slacks = ["s_%s" % name for _s, name, _r, _t in rows
              if (cons_map.get(name) or {}).get("kind") == "cover"
              and name not in hard_names]
    with open(path, "w", encoding="ascii") as f:
        w = f.write
        # Цель перечисляет ВСЕ переменные по порядку, включая нулевые
        # цены вспомогательных «рейс покрыт»: разбор решения HiGHS
        # опирается на порядок колонок LP-файла — его задаёт первое
        # появление, то есть эта строка.
        w("Minimize\n obj: ")
        sep = ""
        for ci in range(len(cost)):
            w("%s%d x%d" % (sep, int(cost[ci]), ci))
            sep = " + "
        for s in slacks:
            w(" + %d %s" % (big_m, s))
        w("\nSubject To\n")
        for sense, name, rhs, terms in rows:
            kind = (cons_map.get(name) or {}).get("kind")
            expr = " ".join("%+d x%d" % (coef, ci) for ci, coef in terms)
            if kind == "cover" and name not in hard_names:
                # лексикографика покрытия: <=1 жёстко, >=1 с прослаблением
                w(" %s_u: %s <= 1\n" % (name, expr))
                w(" %s_c: %s +1 s_%s >= 1\n" % (name, expr, name))
            else:
                op = "=" if sense == "E" else "<="
                w(" %s: %s %s %d\n" % (name, expr, op, int(rhs)))
        for k, (bi, bj) in enumerate(ban_pairs or ()):
            w(" bp%d: +1 x%d +1 x%d <= 1\n" % (k, bi, bj))
        for k, idxs in enumerate(ban_groups or ()):
            if len(idxs) >= 2:
                w(" bg%d: %s <= %d\n" % (
                    k, " ".join("+1 x%d" % i for i in idxs),
                    len(idxs) - 1))
        w("Bounds\n")
        for ci in sorted(fixed_zero):
            w(" x%d = 0\n" % ci)
        for s in slacks:
            w(" 0 <= %s <= 1\n" % s)
        w("Binary\n")
        for ci in range(len(cost)):
            w(" x%d\n" % ci)
        w("End\n")
    return big_m, slacks


def solve(engine: str, rows, cons_map, cost, fixed_zero, hard_names,
          ban_pairs, ban_groups, budget_s: int, workdir: Path,
          label: str) -> dict:
    """Решение модели отбора внешним движком.

    Возврат: {picked: [ci...], status: str, objective: int (только
    стоимость, без штрафов прослаблений), uncovered_rows: [имя...],
    bound: float | None}.
    """
    check_available(engine)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    lp = workdir / ("select-%s-%s.lp" % (label, engine))
    big_m, slacks = _write_lp(lp, rows, cons_map, cost, fixed_zero,
                              hard_names, ban_pairs, ban_groups)
    if engine == "highs":
        return _solve_highs(lp, cost, budget_s)
    return _solve_cplex(lp, cost, budget_s, workdir, label)


def _solve_highs(lp: Path, cost, budget_s: int) -> dict:
    import highspy
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(max(1, budget_s)))
    st = h.readModel(str(lp))
    if str(st) not in ("HighsStatus.kOk", "kOk", "0"):
        raise EngineUnavailable("движок highs: модель не прочитана "
                                "(%s)" % st)
    h.run()
    status = str(h.getModelStatus()).split(".")[-1]
    sol = h.getSolution()
    vals = list(sol.col_value)
    # порядок колонок LP-файла: x0..xN-1, затем прослабления
    picked = [ci for ci in range(len(cost)) if vals[ci] > 0.5]
    uncovered = []
    lp_names = None
    if len(vals) > len(cost):
        # имена колонок прослаблений — по порядку записи
        lp_names = _slack_names(lp)
        for k, v in enumerate(vals[len(cost):]):
            if v > 1e-6 and lp_names and k < len(lp_names):
                uncovered.append(lp_names[k])
    info = h.getInfo()
    bound = getattr(info, "mip_dual_bound", None)
    obj = sum(int(cost[ci]) for ci in picked)
    return {"picked": picked, "status": status, "objective": obj,
            "uncovered_rows": uncovered, "bound": bound}


def _slack_names(lp: Path):
    out = []
    for ln in lp.read_text(encoding="ascii").splitlines():
        ln = ln.strip()
        if ln.startswith("0 <= s_"):
            out.append(ln.split()[2])
    return out


def _solve_cplex(lp: Path, cost, budget_s: int, workdir: Path,
                 label: str) -> dict:
    exe = cplex_exe()
    # Обмен с интерактивным CPLEX — через КОРОТКИЙ временный каталог
    # именами файлов: его чтение файлов не дружит с длинными (≈200+
    # знаков) и нелатинскими путями — живой отказ 04.09 «select-…-cplex.lp
    # not found» в каталоге данных COSMOS и полным путём, и именем с
    # cwd. Следы (lp, cmd, sol, журнал) копируются обратно в рабочий
    # каталог нажатия, чтобы прогон оставался воспроизводимым.
    exchange = Path(tempfile.mkdtemp(prefix="cplex-"))
    # Имена файлов обмена — постоянные и только ASCII: командный файл
    # пишется в ASCII, а метка построителя (она в имени LP) бывает с
    # кириллицей — стенд 04.09 ронял движок «'ascii' codec can't encode»,
    # cpsat при той же метке проходил. Следы возвращаются в рабочий
    # каталог под именами метки.
    x_lp = exchange / "model.lp"
    shutil.copyfile(lp, x_lp)
    x_sol = exchange / "model.sol"
    x_cmd = exchange / "model.cmd"
    x_cmd.write_text(
        "set timelimit %d\nread %s\noptimize\nwrite %s\nquit\n"
        % (max(1, int(budget_s)), x_lp.name, x_sol.name),
        encoding="ascii")
    x_log = exchange / "model-cplex.log"
    with open(x_cmd, "rb") as fi, open(x_log, "wb") as fo:
        proc = subprocess.run([exe], stdin=fi, stdout=fo,
                              stderr=subprocess.STDOUT,
                              cwd=str(exchange),
                              timeout=budget_s + 120)
    sol = workdir / (lp.stem + ".sol")
    log = workdir / (lp.stem + "-cplex.log")
    for src, dst in ((x_cmd, workdir / (lp.stem + ".cmd")), (x_log, log),
                     (x_sol, sol)):
        if src.exists():
            shutil.copyfile(src, dst)
    shutil.rmtree(exchange, ignore_errors=True)
    if not sol.exists():
        tail = log.read_text(encoding="utf-8",
                             errors="replace")[-500:] \
            if log.exists() else ""
        raise EngineUnavailable(
            "движок cplex: решение не записано (код %d): …%s"
            % (proc.returncode, tail))
    root = ET.parse(sol).getroot()
    h = root.find("header")
    status = (h.get("solutionStatusString") or "?") if h is not None \
        else "?"
    picked, uncovered = [], []
    for v in root.iter("variable"):
        name = v.get("name") or ""
        val = float(v.get("value") or 0)
        if name.startswith("x") and val > 0.5:
            picked.append(int(name[1:]))
        elif name.startswith("s_") and val > 1e-6:
            uncovered.append(name[2:])
    obj = sum(int(cost[ci]) for ci in picked)
    # Нижняя граница — из журнала CPLEX («Current MIP best bound = …»);
    # у доказанного оптимума граница равна цели. Те же поля отчёта,
    # что у HiGHS (mip_dual_bound): движок не должен быть беднее соседа.
    bound = _cplex_bound_from_log(log)
    if bound is None and "optimal" in status.lower():
        # цель модели включает штрафы прослаблений; при пустых
        # прослаблениях это ровно стоимость выбора
        bound = float(obj) if not uncovered else None
    return {"picked": sorted(picked), "status": status,
            "objective": obj, "uncovered_rows": sorted(uncovered),
            "bound": bound}


def _cplex_bound_from_log(log: Path):
    """Последнее значение «Current MIP best bound» из журнала CPLEX;
    None — журнал без строки границы (например, решено пресолвом)."""
    import re
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.findall(r"Current MIP best bound =\s*([-+0-9.eE]+)", text)
    if not found:
        return None
    try:
        return float(found[-1])
    except ValueError:
        return None
