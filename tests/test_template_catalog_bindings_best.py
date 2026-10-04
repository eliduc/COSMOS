# -*- coding: utf-8 -*-
"""T17: лучшее состояние клетки — `template_catalog.bindings.best_states`
(§2.9 проекта шага 5).

Гейт файла: ранг «есть» > «заявлен» > «нет» у нескольких строк одного
продукта, независимость от порядка строк в файле, состояние вне словаря
не побеждает известного, заявки «исполнитель заявлен» под именем
подавшего — и главное обязательство перед сессией CDPM:
`executor_states()` этой правкой НЕ ТРОНУТ (он по-прежнему оставляет в
клетке ПОСЛЕДНЮЮ строку файла, и его текст равен тексту на HEAD).

Равенство `best_states` с дверью COSMOS (`vmp.catalog_access.
bindings_states`) на одних файлах доказывает T9 дорожки A: дверь — зона
продукта, и гейт читателя её не импортирует.
"""
import ast
import inspect
import subprocess
from pathlib import Path

import pytest

import template_catalog as tc
from template_catalog import bindings as tb

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "template_catalog" / "bindings.py"


class _КаталогЗаявок:
    """Подставной каталог: `best_states` и `executor_states` читают у
    каталога только заявки."""

    def __init__(self, requests=()):
        self._requests = list(requests)

    def requests(self):
        return list(self._requests)


def _строка(kind: str, state: str, **kw) -> dict:
    row = {"kind": kind, "state": state, "lang": None, "file": None,
           "symbol": None, "note": None, "cr_key": None}
    row.update(kw)
    return row


def _файл(product: str, *executors: dict, section="families",
          rid="duty_limits") -> dict:
    return {"product": product,
            "bindings": [{"section": section, "id": rid,
                          "executors": list(executors),
                          "stand_verified": []}]}


def test_лучшее_состояние_побеждает_последнюю_строку(capsys):
    """У одного вида записи законно несколько строк одного продукта
    (ручка «есть» и ручка-синоним «нет»); строка, оказавшаяся в файле
    последней, за клетку не решает."""
    doc = _файл("COSMOS",
                _строка("kotlin_knob", "нет", note="синоним"),
                _строка("kotlin_knob", "есть", lang="kotlin", file="a.kt",
                        symbol="A.b"),
                _строка("kotlin_knob", "нет", note="второй синоним"))
    cat = _КаталогЗаявок()
    best = tb.best_states(cat, doc)
    последняя = tb.executor_states(cat, doc)
    assert best["families"]["duty_limits"]["kotlin_knob"] == {"COSMOS": "есть"}
    assert последняя["families"]["duty_limits"]["kotlin_knob"] == \
        {"COSMOS": "нет"}, "executor_states обязан остаться прежним"
    print(f"\nT17 клетка из трёх строк: лучшее "
          f"{best['families']['duty_limits']['kotlin_knob']}, последняя "
          f"{последняя['families']['duty_limits']['kotlin_knob']}")


def test_ранг_состояний_и_независимость_от_порядка():
    cat = _КаталогЗаявок()
    пары = [(("нет", "заявлен"), "заявлен"), (("заявлен", "есть"), "есть"),
            (("нет", "есть"), "есть"), (("нет", "нет"), "нет"),
            (("есть", "заявлен"), "есть")]
    рассмотрено = 0
    for состояния, ожидание in пары:
        for порядок in (состояния, tuple(reversed(состояния))):
            строки = [_строка("check_code", s) if s != "есть" else
                      _строка("check_code", "есть", lang="python",
                              file="x.py", symbol="x.y") for s in порядок]
            out = tb.best_states(cat, _файл("COSMOS", *строки))
            assert out["families"]["duty_limits"]["check_code"] == \
                {"COSMOS": ожидание}, (порядок, ожидание)
            рассмотрено += 1
    assert рассмотрено == 10


def test_состояние_вне_словаря_не_побеждает_известного():
    """Чужое слово в поле state — не повод спрятать «есть»; но клетку,
    где других строк нет, оно занимает: молчать о строке нельзя."""
    cat = _КаталогЗаявок()
    out = tb.best_states(cat, _файл("COSMOS",
                                    _строка("check_code", "есть", lang="python",
                                            file="x.py", symbol="x.y"),
                                    _строка("check_code", "непонятно")))
    assert out["families"]["duty_limits"]["check_code"] == {"COSMOS": "есть"}
    out2 = tb.best_states(cat, _файл("COSMOS",
                                     _строка("check_code", "непонятно")))
    assert out2["families"]["duty_limits"]["check_code"] == \
        {"COSMOS": "непонятно"}


def test_два_продукта_дают_две_клетки():
    cat = _КаталогЗаявок()
    свой = _файл("COSMOS", _строка("kotlin_constraint", "нет"))
    чужой = _файл("CDPM", _строка("kotlin_constraint", "есть", lang="kotlin",
                                  file="b.kt", symbol="B.c"))
    out = tb.best_states(cat, свой, чужой)
    assert out["families"]["duty_limits"]["kotlin_constraint"] == \
        {"COSMOS": "нет", "CDPM": "есть"}


def test_заявка_исполнитель_заявлен_даёт_заявлен_под_именем_подавшего():
    заявки = [{"request_id": "req-0001", "state": "исполнитель заявлен",
               "section": "families", "record_id": "новая_семья",
               "submitted_by": "CDPM", "executor_kinds_requested":
                   ["check_template"], "cr_key": "cr1a2b3c4d"},
              {"request_id": "req-0002", "state": "принята",
               "section": "families", "record_id": "иная_семья",
               "submitted_by": "CDPM",
               "executor_kinds_requested": ["check_template"]}]
    cat = _КаталогЗаявок(заявки)
    out = tb.best_states(cat, _файл("COSMOS", _строка("kotlin_knob", "нет")))
    assert out["families"]["новая_семья"]["check_template"] == \
        {"CDPM": "заявлен"}
    assert "иная_семья" not in out["families"], \
        "заявка «принята» исполнителя ещё не называет"


def test_на_живом_файле_привязок_клетки_не_хуже_последних(capsys):
    """Живые привязки COSMOS: у каждой клетки лучшее состояние не хуже
    того, что оставляет `executor_states`, и обе таблицы накрывают одни
    и те же клетки."""
    path = REPO / "vmp" / "bindings.json"
    if not path.is_file():
        pytest.skip("файла привязок COSMOS нет — сверять не на чем")
    doc = tb.load_bindings(path)
    cat = tc.load(REPO / "catalog")
    best = tb.best_states(cat, doc)
    last = tb.executor_states(cat, doc)
    ранг = {"есть": 2, "заявлен": 1, "нет": 0}
    клеток = 0
    лучше = 0
    for section, records in best.items():
        assert set(records) == set(last[section])
        for rid, kinds in records.items():
            assert set(kinds) == set(last[section][rid])
            for kind, by_product in kinds.items():
                for product, state in by_product.items():
                    клеток += 1
                    было = last[section][rid][kind][product]
                    assert ранг.get(str(state), -1) >= ранг.get(str(было), -1)
                    лучше += int(state != было)
    assert клеток > 0
    print(f"\nT17 живые привязки: клеток {клеток}, из них лучшее состояние "
          f"отличается от последней строки в {лучше}")


# ------------------------------- обязательство перед сессией CDPM
def _исходник_функции(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"функции {name} в тексте нет")


def test_executor_states_не_тронут_относительно_head(capsys):
    """Договорённость с сессией CDPM (проект шага 4 §2.1 п.3):
    `executor_states()` шаг 5 не меняет. Судится текстом: функция в
    рабочем дереве обязана совпасть с функцией на HEAD."""
    try:
        head = subprocess.run(
            ["git", "show", "HEAD:template_catalog/bindings.py"],
            cwd=REPO, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:   # pragma: no cover
        pytest.skip(f"git недоступен: {e}")
    if head.returncode != 0:                             # pragma: no cover
        pytest.skip("файла привязок на HEAD нет — сверять не с чем")
    было = _исходник_функции(head.stdout.decode("utf-8"), "executor_states")
    стало = _исходник_функции(SRC.read_text(encoding="utf-8"),
                              "executor_states")
    assert стало == было, "executor_states изменён — это чужая зона"
    assert стало == inspect.getsource(tb.executor_states).rstrip("\n")
    print(f"\nT17 executor_states: {len(стало.splitlines())} строк, "
          f"совпадает с HEAD")


def test_best_states_добавлена_а_не_подменила():
    """Обе функции живут рядом: служба зовёт лучшее, старые потребители —
    прежнее."""
    assert callable(tb.best_states) and callable(tb.executor_states)
    assert tb.best_states is not tb.executor_states
    подпись = inspect.signature(tb.best_states)
    assert list(подпись.parameters) == ["catalog", "bindings"]
