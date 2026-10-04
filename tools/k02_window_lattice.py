# -*- coding: utf-8 -*-
"""РЕШЁТКА ОКНА (K02, находка С-1) — числа с рецептом, а не по памяти.

Зачем. Закон окна (`family_params._foreign_unit_at`) был назван тремя
числами: «на переборе 8064 пар прежний код не читал НИ ОДНОЙ; новый
читает 1440; вариант, рвущий окно ПРЕДЛОГОМ, — 4032». Числа называли
ФОРМУ решётки («3 оговорки × 4 числа × 7 слов × 4 предлога × 6 сроков ×
4 единицы»), но не её СОСТАВ: ни одного списка выписано не было, скрипта
не было тоже. Воспроизвести такое нечем — ровно тот же род, что у
отозванного «1336 профиль». Здесь решётка ОБЪЯВЛЕНА поимённо, а числа
снимаются одной командой:

    PYTHONUTF8=1 <venv>/python.exe tools/k02_window_lattice.py --base <точный-коммит>

Что меряется. Для каждой пары (ответ × единица каталога) берётся исход
`family_params.parse_value(ответ, единица, default_domain(единица))` и
сводится к «прочитан» / «не прочитан». Считаются ПРОЧИТАННЫЕ пары у трёх
вариантов кода:

  БАЗА      `cdpm/family_params.py` из явно названного --base, взятый git show.
            Все остальные cdpm/template_catalog/catalog зависимости должны
            совпадать с рабочей линией; иначе это не историческая сборка и
            замер отказывает до импорта. Старый b844ecb7 не подразумевается.
  ГОЛОВА    рабочее дерево как есть
  ПРЕДЛОГ   голова с ОБЪЯВЛЕННОЙ здесь заплатой: окно рвёт не своё
            слово величины, а ПРЕДЛОГ перед родом окна (широкий
            вариант, измеренный и отвергнутый в круге 13). После общего
            period-guard вариант ЯВНО отключает и его: это ДВЕ подмены.

Заплата варианта ПРЕДЛОГ накладывается по ТОЧНОЙ строке исходника и
проверяется на применение: если строка не нашлась (код разошёлся с
заплатой), скрипт падает, а не печатает число молча.

Что скрипт НЕ удостоверяет: он меряет ГРАММАТИКУ ответа на объявленной
решётке — не цену правки на живых книгах (её меряет
`tools/k02_grammar_price.py`), не заметку отказа и не посадку значения.
Решётка здесь — не корпус: это перебор ОДНОЙ формы «число, слово при
числе, предлог, срок», которой закон С-1 и касается.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)
if _TREE not in sys.path:
    sys.path.insert(0, _TREE)

#: СОСТАВ РЕШЁТКИ — выписан поимённо, иначе число ничего не называет.
#: Форма перебора: «‹оговорка›‹число› ‹слово при числе› ‹предлог› ‹срок›».
CLAUSES = ("", "не более ", "максимум ")
NUMBERS = ("2", "10", "75", "600")
#: Слово при числе. ПУСТАЯ строка — «числу своей величины не
#: дано» (посылка окна выполнена); «раз» и «рейсов» — ПРЕДМЕТ
#: СЧЁТА, не слово величины (остаток, названный вслух). Без этих
#: трёх столбцов решётка вариантов НЕ РАЗЛИЧАЕТ: где своё слово
#: стоит всегда, окно рвут оба.
OWN_WORDS = ("", "часов", "минут", "суток", "дней", "раз", "рейсов")
PREPS = ("в", "за", "на", "по")
SPANS = ("месяц", "неделю", "квартал", "год", "декаду", "сутки")
#: Отдельная ось неподдерживаемых форм, включая СВОЁ слово у числа.
#: Прежняя решётка содержала только календарные слова и этот класс
#: принципиально не различала. Старые 8064 пары сохранены отдельно.
FORM_WORDS = ("процентах", "долях", "промилле", "секунду", "секундах",
              "неделях", "месяцах", "кварталах", "годах", "декадах",
              "полугодиях")
UNITS = ("duration_minutes", "count", "credit_hours",
         "hours_per_credit_hour")

#: Входы ВНЕ решётки, которыми названа цена варианта ПРЕДЛОГ: предлог
#: вводит не только СРОК, но и ФОРМУ ВЫРАЖЕНИЯ величины.
FORM_CASES = (("10 в процентах", "hours_per_credit_hour"),
              ("50 в процентах", "hours_per_credit_hour"),
              ("2 в неделях", "count"),
              ("10 за процент", "hours_per_credit_hour"),
              ("10 часов в процентах", "hours_per_credit_hour"),
              ("10 часов в долях", "credit_hours"),
              ("10 часов в секунду", "credit_hours"),
              ("10 часов в неделях", "duration_minutes"))

#: Заплата ВАРИАНТА «ПРЕДЛОГ» — по точной строке головы.
_PATCH_FROM = '_calendar_qualified_unit(text, mm)'
_PATCH_TO = '_prep_breaks_gap(text, mm)'
# After CDPM-194 the new global period guard independently refuses the
# periodic forms. This hypothetical old broad variant explicitly removes
# that guard as well; it is NOT a mutation of just the window exception.
_PERIOD_PATCH_FROM = 'if period_qualifier(raw, unit):'
_PERIOD_PATCH_TO = 'if False and period_qualifier(raw, unit):'
_PATCH_TAIL = '''

_LATTICE_PREPS = ("в", "за", "на", "по")


def _prep_breaks_gap(text, mm):
    """ВАРИАНТ, ИЗМЕРЕННЫЙ И ОТВЕРГНУТЫЙ: окно рвёт ПРЕДЛОГ перед родом
    окна, а не своё слово величины при числе."""
    gap = text[mm.end("num"):mm.start("w")]
    words = gap.split()
    return bool(words) and words[-1].lower() in _LATTICE_PREPS
'''


def lattice(spans=SPANS):
    """Решётка ответов: список строк, порядок перебора объявлен."""
    out = []
    for c, n, w, p, s in itertools.product(
            CLAUSES, NUMBERS, OWN_WORDS, PREPS, spans):
        out.append(c + " ".join(x for x in (n, w, p, s) if x))
    return out


def magnitude_lattice(periodic=False):
    """CDPM-202: preposition BEFORE the number, with a real-period control."""
    return [f"{lead} {prep} {n} {word}" + (" в неделю" if periodic else "")
            for lead, prep, n, word in itertools.product(
                ("Перерыв", "Интервал", "Резерв"), ("в", "за"),
                ("2", "10"), ("минут", "часов"))]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _base_source(sha, workdir):
    out = subprocess.run(["git", "-C", _TREE, "show",
                          "%s:cdpm/family_params.py" % sha],
                         stdout=subprocess.PIPE, check=True).stdout
    path = os.path.join(workdir, "base_family_params.py")
    with open(path, "wb") as fh:
        fh.write(out)
    return path


def _check_dependency_changes(changed, untracked):
    unexpected = sorted(set(changed) - {"cdpm/family_params.py"})
    if unexpected or untracked:
        raise SystemExit("несопоставимая среда исторического модуля: "
                         + ", ".join(unexpected + sorted(untracked)))


def _base_environment(ref):
    sha = subprocess.run(["git", "-C", _TREE, "rev-parse", "--verify",
                          "--end-of-options", ref + "^{commit}"],
                         stdout=subprocess.PIPE, check=True, text=True).stdout.strip()
    scope = ["--", "cdpm", "template_catalog", "catalog"]
    changed = subprocess.run(["git", "-C", _TREE, "diff", "--name-only", sha] + scope,
                             stdout=subprocess.PIPE, check=True, text=True).stdout.splitlines()
    untracked = subprocess.run(["git", "-C", _TREE, "ls-files", "--others",
                                "--exclude-standard"] + scope,
                               stdout=subprocess.PIPE, check=True, text=True).stdout.splitlines()
    _check_dependency_changes(changed, untracked)
    return sha


def _prep_source(workdir):
    src = open(os.path.join(_TREE, "cdpm", "family_params.py"),
               encoding="utf-8").read()
    if src.count(_PATCH_FROM) != 1:
        raise SystemExit("заплата варианта ПРЕДЛОГ НЕ ПРИМЕНИЛАСЬ: строка "
                         "%r встречается %d раз, ожидался ровно один — "
                         "код разошёлся с заплатой, число не снимается"
                         % (_PATCH_FROM, src.count(_PATCH_FROM)))
    if src.count(_PERIOD_PATCH_FROM) != 1:
        raise SystemExit("заплата варианта ПРЕДЛОГ НЕ ПРИМЕНИЛАСЬ: "
                         "глобальный рубеж периода изменился")
    src = src.replace(_PERIOD_PATCH_FROM, _PERIOD_PATCH_TO)
    path = os.path.join(workdir, "prep_family_params.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src.replace(_PATCH_FROM, _PATCH_TO) + _PATCH_TAIL)
    return path


def read_set(mod, answers):
    """Пары (ответ, единица), у которых вариант ДАЁТ значение."""
    got = set()
    for unit in UNITS:
        dom = mod.default_domain(unit)
        for raw in answers:
            try:
                if mod.parse_value(raw, unit, dom) is not None:
                    got.add((raw, unit))
            except Exception as exc:                 # noqa: BLE001
                raise SystemExit("ошибка замера на %r/%s: %s"
                                 % (raw, unit, exc))
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True,
                    help="точный коммит базы с неизменными остальными зависимостями")
    ap.add_argument("--examples", type=int, default=6,
                    help="сколько примеров печатать у каждого класса")
    args = ap.parse_args(argv)
    args.base = _base_environment(args.base)

    answers = lattice()
    pairs = len(answers) * len(UNITS)
    print("РЕШЁТКА: %d оговорок × %d чисел × %d слов × %d предлогов × "
          "%d сроков × %d единиц = %d пар"
          % (len(CLAUSES), len(NUMBERS), len(OWN_WORDS), len(PREPS),
             len(SPANS), len(UNITS), pairs))
    if pairs != len(set(answers)) * len(UNITS):
        print("ВНИМАНИЕ: в решётке есть повторы ответов")

    import cdpm.family_params as head
    if os.path.normcase(os.path.realpath(head.__file__)) != os.path.normcase(
            os.path.join(_TREE, "cdpm", "family_params.py")):
        raise SystemExit("загружена чужая голова family_params")
    with tempfile.TemporaryDirectory() as tmp:
        base = _load(_base_source(args.base, tmp), "k02lat_base")
        prep = _load(_prep_source(tmp), "k02lat_prep")

        r_base = read_set(base, answers)
        r_head = read_set(head, answers)
        r_prep = read_set(prep, answers)
        forms = lattice(FORM_WORDS)
        form_base = read_set(base, forms)
        form_head = read_set(head, forms)
        form_prep = read_set(prep, forms)
        magnitudes = magnitude_lattice()
        rates = magnitude_lattice(periodic=True)
        magnitude_base = read_set(base, magnitudes)
        magnitude_head = read_set(head, magnitudes)
        rate_base = read_set(base, rates)
        rate_head = read_set(head, rates)

    print("ФОРМА ВЕЛИЧИНЫ: %d пар; база = %d; голова = %d; ПРЕДЛОГ = %d"
          % (len(forms) * len(UNITS), len(form_base), len(form_head),
             len(form_prep)))
    print("ПРЕДЛОГ ПЕРЕД ЧИСЛОМ: %d пар; база = %d; голова = %d"
          % (len(magnitudes) * len(UNITS), len(magnitude_base), len(magnitude_head)))
    print("ТЕ ЖЕ ВЕЛИЧИНЫ С ПЕРИОДОМ: %d пар; база = %d; голова = %d"
          % (len(rates) * len(UNITS), len(rate_base), len(rate_head)))

    print("ПРОЧИТАНО пар: база %s = %d; голова = %d; вариант ПРЕДЛОГ = %d"
          % (args.base, len(r_base), len(r_head), len(r_prep)))
    print("  голова прибавила к базе: %d; отняла: %d"
          % (len(r_head - r_base), len(r_base - r_head)))
    print("  вариант ПРЕДЛОГ прибавил к голове: %d; отнял: %d"
          % (len(r_prep - r_head), len(r_head - r_prep)))

    for name, extra in (("голова над базой", sorted(r_head - r_base)),
                        ("ПРЕДЛОГ над головой", sorted(r_prep - r_head))):
        print("  примеры (%s):" % name)
        for raw, unit in extra[:args.examples]:
            print("    %-34s %s" % (raw, unit))

    # Цена варианта ПРЕДЛОГ вне решётки: предлог вводит не только СРОК,
    # но и ФОРМУ ВЫРАЖЕНИЯ величины, и по знакам они не различаются.
    print("ЦЕНА ВАРИАНТА ПРЕДЛОГ вне решётки (форма выражения величины):")
    for raw, unit in FORM_CASES:
        h = head.parse_value(raw, unit, head.default_domain(unit))
        p = prep.parse_value(raw, unit, prep.default_domain(unit))
        print("    %-22s %-22s голова %-8s ПРЕДЛОГ %s"
              % (raw, unit, "-" if h is None else h[0],
                 "-" if p is None else p[0]))
    return 0


if __name__ == "__main__":
    from _cli_console import force_utf8_console
    force_utf8_console()
    raise SystemExit(main())
