# -*- coding: utf-8 -*-
"""Свидетель закона паритета: каталог шаблонов ≡ коду COSMOS.

Закон живёт в модуле `catalog/tools/parity.py` (движок `judge` и команда
с кодами 0/1/2), а этот файл — его СВИДЕТЕЛЬ: он зовёт движок, печатает
числа вердикта и краснеет на нарочно испорченных копиях. До пакета
паритета закон жил здесь, и числа гейта были литералами: первая же
сборка службой краснила их по построению, и правка класса «рост таблиц
решателя» доходила до `needs_human` не по делу.

Что здесь судится и чего здесь больше нет:

  * судимый каталог на дереве — «живой ⊕ все кандидаты
    `catalog/candidates/**`» (привязки для него движок строит в памяти);
    рядом печатается вердикт живого каталога без кандидатов. Перечня
    «ожидаемо красных тестов» нет: ветка правки класса «рост таблиц»
    зелена ПО СУЩЕСТВУ, а не по перечню;
  * ни одного сравнения с числом-литералом: числа печатаются, а
    утверждается равенство множеств с именами. Единственное исключение
    названо поимённо — таблица `NUMBERS_RELEASE`, и сверяется она с
    НЕИЗМЕНЯЕМОЙ папкой выпуска `catalog/releases/<v>/`, а не с живым
    каталогом: живой растёт сборками службы, и литерал краснил бы
    `pytest -q` клона у каждого CR, меняющего счёт;
  * вердикт живого каталога без кандидатов печатается, но не
    утверждается: на ветке класса «рост таблиц решателя» он красен по
    делу, а судьёй дерева ради этого и стал «живой ⊕ кандидаты»;
  * L24 («два читателя Kotlin») живёт только здесь: собственный
    регулярный читатель этого файла ≡ `vmp.kotlin_tables`. Так
    независимость свидетеля решателя держится явным правилом, а не
    молчаливой копией кода.

Отрицательные случаи — на tmp-копиях (`tests/catalog_copies.py`) и на
ПОДМЕНЁННЫХ свидетелях: движок берёт свидетелей данными, ключа командной
строки для подмены нет намеренно (закон подменять нельзя, свидетеля —
можно только в стенде).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
from tests import catalog_copies as cc                            # noqa: E402

CATALOG = ROOT / "catalog"
TOOLS = CATALOG / "tools"
KOTLIN = (ROOT / "kotlin-solver" / "src" / "main" / "kotlin" / "org" / "acme"
          / "flighcrewscheduling" / "solver")
CAND_STAMP = {"by": "тест паритета", "saved_at": "2026-09-12T10:15:00+03:00",
              "request_id": "req-03707195193482806055",
              "cr_key": "cr575b6c6a"}

#: Выпуск, чьи числа держит таблица ниже. Папка `catalog/releases/<v>/`
#: НЕИЗМЕНЯЕМА (её пишет `writer.snapshot_release`, а `--refresh` под
#: `releases/` отказывает словами), поэтому её числа — факт, а не
#: заморозка живого каталога.
RELEASE_OF_NUMBERS = "0.2.0"

#: Строки схемы формы записи СЕГОДНЯШНЕГО читателя (правило гейта T18
#: `test_template_catalog_releases.py`, повторённое здесь одной строкой:
#: пакеты тестов друг друга не импортируют).
СХЕМА_ПАКЕТА = (ROOT / "template_catalog" / "schemas" / "catalog.schema.json"
                ).read_text(encoding="utf-8").splitlines()


def форма_своего_времени(папка) -> bool:
    """Снимок выпуска написан НЫНЕШНЕЙ формой записи?

    Папка выпуска неизменяема и несёт схему своего дня. Закон паритета
    судит её правилами сегодняшнего читателя и сегодняшнего инструмента
    (L11 «ноль ошибок validate()», L18 «папка воспроизводится из своих
    курируемых полей ⊕ код») — а они знают только нынешнюю форму. Выпуск
    0.5.0 форму сменил (три поля записи семьи стали обязательными,
    единицы нарушения — картой «код → слово»), и снимки прежней формы
    краснели бы по построению, а не по делу."""
    файл = Path(папка) / "schema" / "catalog.schema.json"
    return (файл.is_file()
            and файл.read_text(encoding="utf-8").splitlines() == СХЕМА_ПАКЕТА)

#: Числа гейта на выпуске 0.2.0 — ЕДИНСТВЕННОЕ место, где закон сверяется
#: с литералами: они доказывают, что производная не потеряла ни одного
#: числа прежнего гейта (16 семей, 46 имён, 17 несвязанных, 0 «только
#: решатель», 8 критериев, 22 шаблона проверок, 4 мягких семьи, 13
#: слов-носителей, 44 стендовые задачи). Числа-СКРЕЩЕНИЯ с решателем
#: (`solver_only` — ручки, у которых нет ни записи, ни имени в словаре
#: CDPM) в таблицу не входят: выпуск заморожен, а решатель идёт вперёд, и
#: на ветке роста таблиц такое число законно меняется. Их держит само
#: правило равенством множеств. Живой каталог этими числами НЕ
#: судится: он растёт сборками службы, и литерал краснил бы `pytest -q`
#: клона у каждого CR, меняющего счёт. Новый выпуск — новая строка
#: таблицы рядом с прежней, а не правка старой.
NUMBERS_RELEASE = {
    "L01": {"families": 16, "criteria": 8},
    "L05": {"params": 46, "unbound": 17},
    "L06": {"subject_words": 13},
    "L09": {"templates": 22},
    "L14": {"soft": 4},
    "L18": {"stand_tasks": 44},
}


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module", autouse=True)
def _own_catalog():
    """Судья дерева судит СВОЙ каталог: настройка контура
    `CREW_TEMPLATE_CATALOG` на время модуля снимается.

    Фикстура модульная и autouse намеренно: общая функциональная
    (`tests/conftest.py::_isolate_template_catalog_env`) поднимается
    ПОЗЖЕ модульных `verdict`/`live`, и на машине с выставленной
    переменной вердикт в процессе судил бы двери чужого каталога (L10
    отвечал бы «двери читают другой каталог»), а подпроцесс — своего:
    равенство «команда ≡ процесс» краснело бы на одном L10 и зависело от
    окружения машины."""
    mp = pytest.MonkeyPatch()
    mp.delenv("CREW_TEMPLATE_CATALOG", raising=False)
    yield
    mp.undo()


@pytest.fixture(scope="module")
def law():
    """Движок закона — модулем по пути: `catalog/tools/` вне пакетов."""
    return _module("parity_law", TOOLS / "parity.py")


@pytest.fixture(scope="module")
def wit(law):
    return law.load_witnesses()


@pytest.fixture(scope="module")
def verdict(law, wit):
    """Вердикт судимого каталога дерева: живой ⊕ все кандидаты."""
    return law.judge(CATALOG, with_candidates=True, witnesses=wit)


@pytest.fixture(scope="module")
def live(law, wit):
    """Вердикт живого каталога без кандидатов — печатается рядом."""
    return law.judge(CATALOG, witnesses=wit)


def rule_of(verdict, rid: str):
    for r in verdict.rules:
        if r.id == rid:
            return r
    raise AssertionError(f"правила {rid} в вердикте нет")


#: Правила, которым пропуск на дереве НЕ дозволен: их судить есть чем, и
#: молчаливый `skipped` означал бы, что свидетель отпал, а не что судить
#: нечего. L10 — двери продуктов (переменную контура снимает фикстура
#: модуля); L19/L22 — строки привязок «живой ⊕ кандидаты» (движок строит
#: их в памяти, и отказ генератора теперь красный, а не пропуск); L21 —
#: «запись ≡ заявке», единственный независимый якорь владельца, когда
#: кандидат рядом есть; L11 — запретные слова заказчика. L23 и L10 в
#: перечни не входят: у временного каталога «живой ⊕ кандидаты» нет ни
#: `tools/`, ни дверей продуктов (они читают папку дерева) — оба судятся
#: отдельными стендами на ЖИВОМ каталоге.
NO_SKIP_ALWAYS = ("L11",)
NO_SKIP_WITH_CANDIDATES = ("L19", "L21", "L22")


def красные(verdict) -> set:
    return {r.id for r in verdict.rules if r.ok is False}


def check(verdict, rid: str):
    """Правило зелёное либо честно пропущено; числа печатаются.

    Пропуск дозволен не всякому правилу: перечни `NO_SKIP_*` называют
    те, у которых на дереве есть свидетель, и молчаливый `skipped` у них
    — дыра, а не воздержание."""
    r = rule_of(verdict, rid)
    print("\n" + r.line())
    assert r.ok is not False, r.words
    if r.ok is None:
        assert r.skipped, f"{rid}: пропуск без слов"
        assert rid not in NO_SKIP_ALWAYS, \
            f"{rid}: свидетель на дереве есть — пропуск здесь дыра: {r.skipped}"
        if verdict.catalog.get("with_candidates"):
            assert rid not in NO_SKIP_WITH_CANDIDATES, \
                f"{rid}: рядом с каталогом есть кандидат — пропуск здесь " \
                f"дыра: {r.skipped}"
    return r


# ------------------------------------------------- правила на дереве
@pytest.mark.parametrize("rid", ["L01", "L02", "L04", "L05", "L05b", "L06",
                                 "L07", "L08", "L08b", "L09", "L10", "L11",
                                 "L13", "L14", "L15", "L16", "L17", "L18",
                                 "L19", "L20", "L21", "L22", "L23"])
def test_правило_закона_зелено_на_дереве(verdict, rid):
    check(verdict, rid)


def test_вердикт_дерева_зелёный_и_печатает_числа(verdict, live):
    """Судимый каталог дерева — «живой ⊕ кандидаты» — зелёный.

    Вердикт живого каталога БЕЗ кандидатов печатается рядом, но не
    утверждается (0.3 проекта): на ветке класса «рост таблиц решателя»
    живой каталог красен по делу — новая ручка объявлена записью
    КАНДИДАТА, а не живой записью, и это ровно тот случай, ради которого
    судьёй дерева стал «живой ⊕ кандидаты». Утверждать здесь `live.ok`
    значило бы красить `pytest -q` клона у каждого такого CR."""
    print("\nсудимый каталог: " + json.dumps(verdict.catalog,
                                             ensure_ascii=False))
    print("живой каталог без кандидатов: "
          + json.dumps(live.counts(), ensure_ascii=False))
    if not live.ok:
        print("живой каталог краснеет (ждёт сборки записи службой): "
              + str(live.words))
    for r in verdict.rules:
        print("  " + r.line())
    assert verdict.ok, verdict.words
    counts = verdict.counts()
    assert counts["red"] == 0
    assert counts["green"] + counts["skipped"] == counts["rules"]


def test_литерал_равен_производной_на_выпуске(law, wit):
    """Числа сегодняшнего гейта равны числам вердикта — на НЕИЗМЕНЯЕМОЙ
    папке выпуска, а не на живом каталоге.

    Числа сверяются с литералом только там, где содержание заморожено:
    `catalog/releases/<версия>/` выпуском и создана неизменяемой. На
    живом каталоге такая сверка краснела бы первой же сборкой службой
    (новый параметр, критерий, стендовая задача меняют счёт), а с ней
    краснел бы `pytest -q` клона — и каждый CR уходил бы в `needs_human`
    по литералу теста, пока человек не поправит число. Выпуска на диске
    нет — пропуск словами: числа держит папка, а не этот файл."""
    folder = CATALOG / "releases" / RELEASE_OF_NUMBERS
    if not folder.is_dir():
        pytest.skip(f"папки выпуска {RELEASE_OF_NUMBERS} на диске нет — "
                    f"числа гейта держит она, а не этот файл")
    if not форма_своего_времени(folder):
        pytest.skip(f"выпуск {RELEASE_OF_NUMBERS} написан прежней формой "
                    f"записи: сегодняшний закон ему не судья. После выпуска "
                    f"новой формы назовите его здесь (RELEASE_OF_NUMBERS) и "
                    f"перепишите таблицу чисел его вердиктом")
    v = law.judge(folder, witnesses=wit)
    # Кандидат ветки рядом с ЖИВЫМ каталогом означает, что таблицы
    # решателя ушли вперёд замороженной папки выпуска: правило, которое
    # пересобирает её из своих курируемых полей ⊕ КОД (L18), краснеет по
    # делу и числа не даёт. Такое правило пропускается СЛОВАМИ — рост
    # таблиц под кандидатом судит закон паритета на временном каталоге
    # «живой ⊕ кандидаты» (решение 0.3), — а остальные числа выпуска
    # сверяются как прежде: сторож выпуска не ослабляется. На чистом
    # дереве кандидатов нет, и пропускать нечего.
    кандидаты = law.candidate_files_of(CATALOG)
    got, пропущено = {}, {}
    for rid, want in NUMBERS_RELEASE.items():
        r = rule_of(v, rid)
        if кандидаты and r.ok is False:
            пропущено[rid] = r.words
            continue
        got[rid] = {k: (r.numbers or {}).get(k) for k in want}
    print(f"\nчисла вердикта выпуска {RELEASE_OF_NUMBERS}: "
          + json.dumps(got, ensure_ascii=False))
    for rid, words in пропущено.items():
        print(f"  {rid}: рост таблиц под кандидатом — судит закон паритета "
              f"на временном каталоге: {words}")
    assert got == {rid: want for rid, want in NUMBERS_RELEASE.items()
                   if rid not in пропущено}


LITERAL_RE = re.compile(r"==\s*\d{2,}")


def test_литералов_в_законе_нет():
    """Ни в движке, ни в свидетеле нет сравнения с числом-литералом:
    закон судит равенством множеств, числа он печатает."""
    for path in (TOOLS / "parity.py", Path(__file__)):
        text = path.read_text(encoding="utf-8")
        bad = [line for line in text.split("\n") if LITERAL_RE.search(line)]
        print(f"\n{path.name}: строк со сравнением с числом {len(bad)}")
        assert not bad, "\n".join(bad)


def test_вердикт_команды_равен_вердикту_в_процессе(law, verdict, tmp_path):
    """Команда подпроцессом даёт те же правила и исходы, что `judge()`."""
    out = tmp_path / "parity.json"
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("CREW_TEMPLATE_CATALOG", None)
    rc = subprocess.run(
        [sys.executable, str(TOOLS / "parity.py"), "--catalog", str(CATALOG),
         "--with-candidates", "--json", str(out), "--quiet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    print("\n" + rc.stdout[-2000:] + rc.stderr[-2000:])
    doc = json.loads(out.read_text(encoding="utf-8"))
    mine = verdict.as_dict()
    assert doc["rc"] == rc.returncode == 0
    assert doc["ok"] is mine["ok"]
    assert {k: v["ok"] for k, v in doc["rules"].items()} == \
        {k: v["ok"] for k, v in mine["rules"].items()}
    assert doc["counts"] == mine["counts"]
    assert set(doc["growth_judged"]) == set(mine["growth_judged"])


# ------------------------------------------------------ L24: два читателя
def _block(text: str, marker: str) -> str:
    i = text.index(marker)
    j = text.find("\n    )", i)
    return text[i:j]


def _knobs_regex(block: str) -> tuple[dict, set]:
    out, keyed = {}, set()
    for m in re.finditer(r'"(\w+)"\s+to\s+Knob\(', block):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(block[i], 0)
            i += 1
        args = block[m.end():i - 1]
        a = re.match(r'\s*(?:"[^"]*"|[\w.]+)\s*,\s*"(\w+)"\s*,\s*"(\w+)"', args)
        assert a, f"ручка {m.group(1)}: аргументы не разобраны"
        out[m.group(1)] = (a.group(1), a.group(2))
        if re.search(r"\bkeyed\s*=\s*true\b", args):
            keyed.add(m.group(1))
    return out, keyed


@pytest.fixture(scope="module")
def kotlin_regex():
    """Собственный читатель Kotlin ЭТОГО файла — независимый свидетель."""
    rt = (KOTLIN / "RunThresholds.kt").read_text(encoding="utf-8")
    lm = (KOTLIN / "LogicModel.kt").read_text(encoding="utf-8")
    tp = (KOTLIN / "TaskPackage.kt").read_text(encoding="utf-8")
    tm = (KOTLIN / "TaskModes.kt").read_text(encoding="utf-8")
    knobs, keyed = _knobs_regex(
        _block(rt, "val KNOBS: Map<String, Knob> = linkedMapOf("))
    sit, _k = _knobs_regex(
        _block(rt, "private val SIT_KNOBS: Map<String, Knob> = linkedMapOf("))
    sit_param = re.search(r'private const val PARAM_SIT = "(\w+)"', rt).group(1)
    hf = re.findall(r'"(\w+)"', re.search(
        r"val HISTORY_FIT_FAMILIES: Set<String> = setOf\(([^)]*)\)",
        lm).group(1))
    hu = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(
        r'"(\w+)" to \("(\w+)" to "(\w+)"\)',
        _block(lm, "HISTORY_FIT_UNITS: Map<String, Pair<String, String>> "
                   "= mapOf("))}
    f2c = dict(re.findall(r'"(\w+)" to "([^"]+)"', _block(
        tp, "val FAMILY_TO_CONSTRAINT: Map<String, String> = mapOf(")))
    block = tm[tm.index("val METRIC_EXECUTORS"):]
    block = block[:block.index(chr(10) + "object TaskModes")]
    metrics = {}
    for m in re.finditer(r'"(\w+)" to MetricExecutor\(', block):
        tail = block[m.end():]
        stop = re.search(chr(10) + r'    "\w+" to MetricExecutor\(', tail)
        body = tail[:stop.start()] if stop else tail
        metrics[m.group(1)] = tuple(sorted(re.findall(r'"(\w+)"', re.search(
            r"tasks = setOf\(([^)]*)\)", body).group(1))))
    assert knobs and sit and hf and hu and f2c and metrics, \
        "Kotlin-таблицы этим файлом не прочитаны"
    return {"knobs": knobs, "keyed": keyed, "sit_param": sit_param,
            "history_families": hf, "history_units": hu, "f2c": f2c,
            "metrics": metrics}


def test_l24_два_читателя_kotlin_согласны(kotlin_regex, wit):
    """L24: регулярный читатель этого файла ≡ `vmp.kotlin_tables`."""
    mod = wit["kotlin"]
    mine = kotlin_regex
    print(f"\nL24: ручек {len(mine['knobs'])} | истории "
          f"{len(mine['history_units'])} | мягких {len(mine['f2c'])} | "
          f"метрик {len(mine['metrics'])}")
    assert {n: (k["unit"], k["relation"]) for n, k in mod["knobs"].items()} \
        == mine["knobs"]
    assert {n for n, k in mod["knobs"].items() if k["keyed"]} == mine["keyed"]
    assert mod["sit_param"] == mine["sit_param"]
    assert sorted(mod["history_families"]) == sorted(mine["history_families"])
    assert mod["history_units"] == mine["history_units"]
    assert mod["family_to_constraint"] == mine["f2c"]
    assert {n: tuple(sorted(m["tasks"])) for n, m in mod["metrics"].items()} \
        == mine["metrics"]


# ------------------------------------------- отрицательные случаи
def _свободная_ручка(folder: Path, base: str = "max_night_sits") -> str:
    """Имя ручки, которого в судимом каталоге ещё НЕТ.

    На ветке правки класса «рост таблиц решателя» копия каталога уже
    несёт запись кандидата (помощник копий сливает кандидатов дерева), и
    жёстко зашитое имя могло бы совпасть с тем, что объявил агент, —
    стенд судил бы столкновение имён вместо правила."""
    известные = set(tc.load(folder).params(status=None))
    имя, n = base, 1
    while имя in известные:
        n += 1
        имя = f"{base}_{n}"
    return имя


def _witnesses_with_knob(law, name: str, unit: str, relation: str) -> dict:
    """Свидетель «решатель вырос на ручку» — подменой ДАННЫХ."""
    wit = copy.deepcopy(law.load_witnesses())
    wit["kotlin"]["knobs"][name] = {"threshold": "1", "threshold_ref": "1",
                                    "unit": unit, "relation": relation,
                                    "keyed": False, "accepts": []}
    return wit


def _candidate(copy_dir: Path, section: str, rid: str, record: dict) -> Path:
    p = copy_dir / "candidates" / section / f"{rid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"form_version": 1, "section": section, "id": rid,
           "record": record, "note": "кандидат стенда паритета", **CAND_STAMP}
    p.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n").encode("utf-8"))
    return p


def _place_tree_candidates(copy_dir: Path) -> list:
    """Кандидаты ДЕРЕВА — файлами рядом с копией каталога.

    Судимый каталог дерева — «живой ⊕ кандидаты `catalog/candidates/**`»
    (решение 0.3 проекта паритета). Стенд, кладущий рядом СВОЕГО
    кандидата на копию без чужих, судил бы каталог, которого на ветке
    нет: таблицы решателя ветки знают запись кандидата, а копия — нет.
    На чистом дереве список пуст и ничего не меняется."""
    out = []
    for doc in cc.tree_candidate_docs():
        p = (copy_dir / "candidates" / str(doc.get("section"))
             / f"{doc.get('id')}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                  indent=1) + "\n").encode("utf-8"))
        out.append(p)
    return out


def _record_with_param(folder: Path, fid: str, param: dict) -> dict:
    """Запись семьи с новым параметром (рост ручки). У семьи с
    объявленными `param_sets` (с каталога 0.3.0, 09.09.2026: у
    duty_limits — три одиночных набора) новый параметр обязан войти в
    набор — правило X12 «объединение наборов = params»; растущая ручка
    получает свой одиночный набор, как и прочие её соседи."""
    cat = tc.load(folder)
    rec = cat.family(fid)
    rec = {k: v for k, v in rec.items() if k != "history"}
    rec["params"] = sorted(list(rec["params"]) + [param],
                           key=lambda p: str(p["name"]))
    sets = list(rec.get("param_sets") or [])
    if sets:
        rec["param_sets"] = sets + [{"id": str(param["name"]),
                                     "params": [str(param["name"])],
                                     "note": "ручка роста стенда паритета"}]
    return rec


def _request_row(section: str, rid: str, draft, *, state: str = "есть",
                 request_id: str | None = None, kinds=None) -> dict:
    """Заявка ФОРМОЙ СЛУЖБЫ и схемы читателя.

    Запись заявки адресуется полем `record_id`: так её пишет
    `catalog_service/request_queue.py`, и того же требует схема
    (`$defs/request`: `additionalProperties: false`, `record_id` в
    `required`, поля `id` у заявки нет). Стенды, писавшие заявку с
    ключом `id`, судили форму, которой служба не порождает, и правило
    L21 зеленело на любой настоящей заявке с нулём сверок."""
    return {"request_id": request_id or CAND_STAMP["request_id"],
            "state": state, "section": section, "record_id": rid,
            "submitted_at": "2026-09-12T10:00:00+03:00",
            "submitted_by": "владелец",
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None},
            "draft": draft, "executor_kinds_requested": kinds,
            "decision": {"by": "владелец",
                         "at": "2026-09-12T10:05:00+03:00",
                         "words": "заявка принята стендом паритета"},
            "cr_key": CAND_STAMP["cr_key"]}


def _write_requests(folder: Path, *rows: dict) -> list:
    """Очередь копии: строки стенда ⊕ заявки-свидетели кандидатов дерева
    (помощник копий). Очередь ровно из своих строк на ветке с кандидатом
    новой записи стирала свидетеля единиц его имён — L05/L05b краснели
    «единицу не подтверждает … ни заявка» по построению (09.09.2026)."""
    return cc.write_requests(folder, list(rows))


def _draft_from(folder: Path, section: str, rid: str, **over) -> dict:
    """Черновик заявки по записи каталога: форма `family_draft` схемы —
    запись без `status`, `history` и `stand_tasks`."""
    cat = tc.load(folder)
    rec = cat.family(rid) if section == "families" else cat.criterion(rid)
    draft = {k: copy.deepcopy(v) for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update(over)
    return draft


def test_рост_ручки_кандидатом_зелёный_на_временном_красный_на_живом(
        law, tmp_path):
    """Решатель вырос на ручку (свидетель подменён данными): живой
    каталог краснеет словами с именем, единицей и отношением; каталог с
    кандидатом, объявившим параметр записи, — зелёный."""
    folder = cc.copy_catalog(tmp_path)
    name, unit, relation = _свободная_ручка(folder), "count", "max"
    wit = _witnesses_with_knob(law, name, unit, relation)
    red = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    l05 = rule_of(red, "L05")
    print("\nживой каталог: " + l05.line())
    assert l05.ok is False
    assert name in l05.words and unit in l05.words and relation in l05.words
    assert not red.ok and red.words

    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": name, "unit": unit,
                                   "relation": relation, "qualifier": None,
                                   "note": None}))
    green = law.judge(folder, with_candidates=True, witnesses=wit)
    for r in green.rules:
        print("  " + r.line())
    # Зелёный ИЛИ красный ТОЛЬКО заявленными пробелами синтетических
    # записей режима роста каталога (`cc.verdict_ok`): исполнителя и
    # стендовых задач у синтетической записи нет по замыслу.
    assert cc.verdict_ok(green), green.words
    assert green.catalog["with_candidates"] == ["families/duty_limits"]
    assert rule_of(green, "L05").numbers["knobs"] == len(wit["kotlin"]["knobs"])
    assert green.catalog["fingerprint"] != green.catalog["source_fingerprint"]


def test_кандидат_объявляет_параметр_которого_нет_в_решателе_красный(
        law, wit, tmp_path):
    """Параметр без свидетеля (ни словарь CDPM, ни решатель, ни заявка) —
    красный словами; строка истории кандидата свидетелем не считается."""
    folder = cc.copy_catalog(tmp_path)
    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": "phantom_limit",
                                   "unit": "duration_minutes",
                                   "relation": "max", "qualifier": None,
                                   "note": None}))
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    words = v.words or ""
    print("\n" + words[:600])
    assert not v.ok
    assert "phantom_limit" in words
    assert "не подтверждает ни словарь CDPM, ни таблицы решателя, ни заявка" \
        in words


def test_имя_из_черновика_заявки_законно(law, wit, tmp_path):
    """Имя, которого нет ни в словаре CDPM, ни у решателя, законно, если
    его называет черновик ПРИНЯТОЙ заявки владельца (форма службы)."""
    folder = cc.copy_catalog(tmp_path)
    name, unit, relation = "phantom_limit", "duration_minutes", "max"
    draft = _draft_from(folder, "families", "duty_limits")
    draft["params"] = sorted(
        list(draft["params"]) + [{"name": name, "unit": unit,
                                  "relation": relation, "qualifier": None,
                                  "note": None}],
        key=lambda p: str(p["name"]))
    _write_requests(folder, _request_row("families", "duty_limits", draft))
    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": name, "unit": unit,
                                   "relation": relation, "qualifier": None,
                                   "note": None}))
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    l05 = rule_of(v, "L05")
    l05b = rule_of(v, "L05b")
    print("\n" + l05.line() + "\n" + l05b.line())
    assert l05.ok is not False, l05.words
    assert l05b.ok is not False, l05b.words
    assert l05.numbers["by_request"] >= 1


def test_метрика_решателя_без_записи_красная(law, tmp_path):
    """Решатель вырос на метрику: каталог без критерия-метрики краснеет
    L08, а CDPM метрики не знает — L08b пропуск с предупреждением."""
    wit = copy.deepcopy(law.load_witnesses())
    wit["kotlin"]["metrics"]["night_stops_total"] = {
        "constraint": "nightStopsTotal", "tasks": ["rostering"],
        "refusal": None, "approximation": None}
    folder = cc.copy_catalog(tmp_path)
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    l08 = rule_of(v, "L08")
    l08b = rule_of(v, "L08b")
    print("\n" + l08.line() + "\n" + l08b.line())
    assert l08.ok is False and "night_stops_total" in l08.words
    assert l08b.ok is None and l08b.warning is True
    assert "night_stops_total" in l08b.skipped
    assert v.as_dict()["rules"]["L08b"]["warning"] is True
    assert v.as_dict()["counts"]["warning"] >= 1


def test_изменение_атрибута_базовой_записи_красный_новая_заморозка(
        law, wit, tmp_path):
    """Пересмотр заголовка базовой записи — не рост, а новая заморозка."""
    folder = cc.copy_catalog(
        tmp_path, families={"duty_limits": {"title": "иной заголовок"}})
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    r = rule_of(v, "L02")
    print("\n" + r.line())
    assert r.ok is False
    assert "новым файлом снимка по слову владельца" in r.words


def test_отношение_вне_kotlin_без_слова_владельца_красное(law, wit, tmp_path):
    """Отношение имени, которого решатель не знает, законно только со
    словом владельца (жёсткий блок посева) либо с заявкой."""
    folder = cc.copy_catalog(tmp_path)
    rec = _record_with_param(folder, "duty_limits",
                             {"name": "max_block_time", "unit": "duration_minutes",
                              "relation": "max", "qualifier": None,
                              "note": None})
    _candidate(folder, "families", "duty_limits", rec)
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    r = rule_of(v, "L05b")
    print("\n" + r.line())
    assert r.ok is False
    assert "max_block_time" in r.words and "слово владельца" in r.words


def test_отношение_владельца_поверх_ручки_решателя_красное(law, tmp_path):
    """Отношение, назначенное словом владельца имени, которое ЕСТЬ в
    таблицах решателя, — красный: отношение исполнителя пишет код."""
    wit = copy.deepcopy(law.load_witnesses())
    knob = sorted(wit["kotlin"]["knobs"])[0]
    wit["hard"]["params_relation_set"][knob] = "max"
    folder = cc.copy_catalog(tmp_path)
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    r = rule_of(v, "L05b")
    print("\n" + r.line())
    assert r.ok is False and knob in r.words


def test_дельта_вычислена_и_свежа(law, wit, tmp_path):
    """Дельта на диске ≡ пересчёту; подмена отпечатка — красный L23 со
    словами команды `--refresh`."""
    v = law.judge(CATALOG, witnesses=wit)
    assert rule_of(v, "L23").ok is True
    delta = json.loads((CATALOG / "transition" / "step4_additions.json")
                       .read_text(encoding="utf-8"))
    assert delta["fingerprint"] == tc.load(CATALOG).fingerprint()
    assert delta["form_version"] == 1
    for key in ("params_to_families", "params_relation_set", "criteria_added",
                "executor_kinds_added", "families_added", "history_added",
                "stand_tasks", "dates", "dictionaries", "gates", "base"):
        assert key in delta, key
    print(f"\nдельта: имён {len(delta['params_to_families'])} | критериев "
          f"{len(delta['criteria_added'])} | семей "
          f"{len(delta['families_added'])} | историй "
          f"{len(delta['history_added'])}")


def test_папка_выпуска_судится_как_каталог(law, wit, live):
    """Папка выпуска — тот же каталог: L13 и L23 пропуск словами.

    Красные правила папки выпуска обязаны быть ПОДМНОЖЕСТВОМ красных
    правил живого каталога: выпуск — снимок прошлого, и решатель, ушедший
    вперёд (ветка класса «рост таблиц»), краснит их обоих одинаково. На
    чистом дереве живой каталог зелен, значит и выпуск обязан быть
    зелёным."""
    releases = sorted((CATALOG / "releases").glob("*"))
    if not releases:
        pytest.skip("выпусков на диске нет")
    if not форма_своего_времени(releases[-1]):
        pytest.skip(f"последний выпуск {releases[-1].name} написан прежней "
                    f"формой записи: сегодняшний закон ему не судья — "
                    f"судить его будет выпуск новой формы")
    v = law.judge(releases[-1], witnesses=wit)
    for r in v.rules:
        print("  " + r.line())
    assert красные(v) <= красные(live), \
        sorted(красные(v) - красные(live))
    assert rule_of(v, "L13").ok is None
    assert "снимке выпуска" in rule_of(v, "L13").skipped
    assert rule_of(v, "L23").ok is None
    assert "без tools/" in rule_of(v, "L23").skipped


def test_двери_читают_другой_каталог_l10_skipped_словами(law, wit, tmp_path,
                                                         monkeypatch):
    """Чужой каталог в настройке контура — L10 пропуск словами, не тихий
    зелёный и не красный не по делу."""
    folder = cc.copy_catalog(tmp_path)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
    v = law.judge(CATALOG, witnesses=wit)
    r = rule_of(v, "L10")
    print("\n" + r.line())
    assert r.ok is None and "двери читают другой каталог" in r.skipped
    # Судится ровно L10: остальной вердикт живого каталога тут ни при чём
    # (на ветке роста таблиц решателя он красен по делу).
    assert "L10" not in красные(v)


def test_l10_двери_дерева_судятся_а_не_пропускаются(live):
    """Свидетель дверей продуктов на дереве: L10 обязано СУДИТЬ.

    Переменную контура снимает фикстура модуля, и судимая папка живого
    вердикта — сам каталог дерева, поэтому «двери читают другой каталог»
    здесь означало бы, что свидетель отпал, а не что судить нечего."""
    r = rule_of(live, "L10")
    print("\n" + r.line())
    assert r.ok is not None, r.skipped
    assert r.ok is True, r.words


def test_запись_равна_заявке(law, wit, tmp_path):
    """L21: поля, которые называет черновик заявки, обязана повторить
    запись; поля сверх заявки — слова `beyond_request`, не отказ.

    Заявка пишется ФОРМОЙ СЛУЖБЫ (`record_id`, состояние, решение
    владельца): правило обязано найти именно её. Прежний стенд писал
    заявку с ключом `id`, которого служба не порождает, и краснел на
    заявке, какой не бывает."""
    folder = cc.copy_catalog(tmp_path)
    _write_requests(folder, _request_row(
        "families", "duty_limits",
        _draft_from(folder, "families", "duty_limits", zone="ROSTERING")))
    rec = _record_with_param(folder, "duty_limits",
                             {"name": "max_block_time",
                              "unit": "duration_minutes", "relation": None,
                              "qualifier": None, "note": None})
    _candidate(folder, "families", "duty_limits", rec)
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    r = rule_of(v, "L21")
    print("\n" + r.line() + " | " + json.dumps(r.extra, ensure_ascii=False))
    assert r.ok is False
    assert "zone" in r.words and "ROSTERING" in r.words
    assert any("max_block_time" in w for w in r.extra["beyond_request"])


def test_запись_совпала_с_заявкой_зелёный_и_судил_не_ноль(law, wit, tmp_path):
    """L21 на заявке формы службы СУДИТ, а не отвечает «сверять не с чем».

    Сторож дефекта, из-за которого правило было мертво: движок искал
    заявку по полю `id`, а служба и схема пишут `record_id` — у любой
    настоящей заявки правило зеленело с `judged 0`. Здесь запись
    кандидата совпадает с черновиком, и число сверенных заявок обязано
    быть не нулевым."""
    folder = cc.copy_catalog(tmp_path)
    draft = _draft_from(folder, "families", "duty_limits")
    _write_requests(folder, _request_row("families", "duty_limits", draft))
    rec = _record_with_param(folder, "duty_limits",
                             {"name": "max_block_time",
                              "unit": "duration_minutes", "relation": None,
                              "qualifier": None, "note": None})
    _candidate(folder, "families", "duty_limits", rec)
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    r = rule_of(v, "L21")
    print("\n" + r.line() + " | " + json.dumps(r.extra, ensure_ascii=False))
    assert r.ok is True, r.words
    assert r.numbers["judged"] == 1, "заявка формы службы не найдена"
    assert any("max_block_time" in w for w in r.extra["beyond_request"])


def test_заявка_чужого_состояния_свидетелем_не_считается(law, wit, tmp_path):
    """Свидетель — только заявка, которую принял владелец: у «поданной»
    черновик решением ещё не стал, и L21 сверять по нему нечего."""
    folder = cc.copy_catalog(tmp_path)
    draft = _draft_from(folder, "families", "duty_limits", zone="ROSTERING")
    _write_requests(folder, _request_row(
        "families", "duty_limits", draft, state="подана"))
    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": "max_block_time",
                                   "unit": "duration_minutes",
                                   "relation": None, "qualifier": None,
                                   "note": None}))
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    r = rule_of(v, "L21")
    print("\n" + r.line() + " | " + json.dumps(r.extra, ensure_ascii=False))
    assert r.ok is True and r.numbers["judged"] == 0
    assert any("заявки с черновиком нет" in w
               for w in r.extra["beyond_request"])


def test_kotlin_и_привязки_l22(law, wit, tmp_path):
    """L22 на подменённой строке метрики привязок — красный словами."""
    folder = cc.copy_catalog(tmp_path)
    path = folder / cc.BINDINGS_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for row in doc["bindings"]:
        for ex in row["executors"]:
            if ex.get("kind") == "kotlin_metric" and ex.get("state") == "есть":
                ex["constraint"] = "ограничение-которого-нет"
                changed = True
                break
        if changed:
            break
    if not changed:
        pytest.skip("строк «есть» вида kotlin_metric в копии нет")
    path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                 indent=1) + "\n").encode("utf-8"))
    v = law.judge(folder, bindings=path, witnesses=wit)
    r = rule_of(v, "L22")
    print("\n" + r.line())
    assert r.ok is False and "метрики" in r.words


def test_l22_строка_метрики_без_имени_говорит_словами(law, wit, tmp_path):
    """Строка вида `kotlin_metric` без поля `metric` — вердикт словами.

    Прежде закон падал `TypeError: '<' not supported between 'NoneType'
    and 'str'` на `sorted` смеси имён и `None` — и ровно на красной
    ветке, где вердикт и нужен (сквозная проверка 08.09.2026)."""
    folder = cc.copy_catalog(tmp_path)
    path = folder / cc.BINDINGS_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for row in doc["bindings"]:
        for ex in row["executors"]:
            if ex.get("kind") == "kotlin_metric" and ex.get("metric"):
                ex.pop("metric")
                changed = True
                break
        if changed:
            break
    if not changed:
        pytest.skip("строк вида kotlin_metric с именем в копии нет")
    path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                 indent=1) + "\n").encode("utf-8"))
    v = law.judge(folder, bindings=path, witnesses=wit)
    r = rule_of(v, "L22")
    print("\n" + r.line())
    assert r.ok is False
    assert "без поля metric" in r.words


def test_файла_привязок_нет_l19_l22_skipped(law, wit, tmp_path):
    """Файла привязок нет — L19 и L22 пропуск словами: строки судит гейт
    привязок, а не паритет."""
    folder = cc.copy_catalog(tmp_path)
    v = law.judge(folder, bindings=folder / "нет-такого-файла.json",
                  witnesses=wit)
    for rid in ("L19", "L22"):
        r = rule_of(v, rid)
        print("\n" + r.line())
        assert r.ok is None and "гейт привязок" in r.skipped
    # Зелёный ИЛИ красный ТОЛЬКО заявленными пробелами синтетических
    # записей режима роста каталога (`cc.verdict_ok`): исполнителя и
    # стендовых задач у синтетической записи нет по замыслу.
    assert cc.verdict_ok(v), v.words


def test_метрика_решателя_без_записи_красная_с_записью_зелёная(law, tmp_path):
    """Зелёная половина стенда метрики (§5 проекта): кандидат-критерий с
    `kotlin_metric`, зоной по задачам решателя и дословным отказом делает
    L08 зелёным; зона не по задачам — красный.

    Заодно назван шов кандидата НОВОЙ записи: привязки такому каталогу
    движок собирает в памяти по посеву привязок дерева, а посев новой
    записи ещё не называет — генератор отказывает, и L19/L22 краснеют
    ЕГО словами. Прежде любой отказ генератора становился молчаливым
    пропуском, и стенд этого шва не видел вовсе."""
    name = "night_stops_total"
    wit = copy.deepcopy(law.load_witnesses())
    wit["kotlin"]["metrics"][name] = {
        "constraint": "nightStopsTotal", "tasks": ["rostering"],
        "refusal": None, "approximation": None}
    folder = cc.copy_catalog(tmp_path)
    образец = tc.load(folder).criterion("credit_hours_spread")
    запись = {k: copy.deepcopy(v) for k, v in образец.items()
              if k != "history"}
    запись.update({"id": name, "zone": "ROSTERING", "class": "extension",
                   "refusal": None, "stand_tasks": [],
                   "title": "ночёвки вне базы, всего",
                   "computable_by": sorted(set(образец["computable_by"])
                                           | {"kotlin_metric"})})
    _candidate(folder, "criteria", name, запись)
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    for r in v.rules:
        print("  " + r.line())
    assert rule_of(v, "L08").ok is True, rule_of(v, "L08").words
    # L08b: с 09.09.2026 метрики CDPM — проекция моста по СУДИМОМУ каталогу
    # (cdpm_metrics_of), и кандидат-метрика с зоной по задачам решателя
    # ему известна; прежде правило предупреждало «CDPM ещё не знает
    # метрику» о любом кандидате по построению.
    l08b = rule_of(v, "L08b")
    assert l08b.ok is True, l08b.line()
    assert l08b.numbers["metrics_known_to_cdpm"] >= 1
    for rid in ("L19", "L22"):
        r = rule_of(v, rid)
        assert r.ok is False, f"{rid}: отказ генератора стал пропуском"
        assert "привязки в памяти не собрались" in r.words

    # зона не по задачам решателя — красный L08 словами
    запись["zone"] = "PAIRING"
    _candidate(folder, "criteria", name, запись)
    v2 = law.judge(folder, with_candidates=True, witnesses=wit)
    r = rule_of(v2, "L08")
    print("\nзона не по задачам: " + r.line())
    assert r.ok is False and name in r.words and "ROSTERING" in r.words


def _кандидат_метрика(folder, name: str, zone: str) -> None:
    образец = tc.load(folder).criterion("credit_hours_spread")
    запись = {k: copy.deepcopy(v) for k, v in образец.items()
              if k != "history"}
    запись.update({"id": name, "zone": zone, "class": "extension",
                   "refusal": None, "stand_tasks": [],
                   "title": "ночёвки вне базы, всего",
                   "computable_by": sorted(set(образец["computable_by"])
                                           | {"kotlin_metric"})})
    _candidate(folder, "criteria", name, запись)


def test_L08b_задачи_метрики_по_зоне_записи_против_решателя(law, tmp_path):
    """Метрики CDPM у судимого каталога — проекция моста по экземпляру:
    задачи по зоне записи. Зона кандидата шире задач решателя — L08b
    красный словами «задачи разошлись», а не пропуск с предупреждением."""
    name = "night_stops_total"
    wit = copy.deepcopy(law.load_witnesses())
    wit["kotlin"]["metrics"][name] = {
        "constraint": "nightStopsTotal", "tasks": ["rostering"],
        "refusal": None, "approximation": None}
    folder = cc.copy_catalog(tmp_path)
    _кандидат_метрика(folder, name, "BOTH")
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    l08b = rule_of(v, "L08b")
    print("\n" + l08b.line())
    assert l08b.ok is False and "разошлись" in l08b.words \
        and name in l08b.words


def test_L08b_старый_мост_без_metrics_of_словарь_модели(law, tmp_path,
                                                        monkeypatch):
    """Откат: у моста без `metrics_of` свидетель — словарь модели CDPM
    (снят с живого каталога), и кандидат-метрика остаётся пропуском
    «CDPM ещё не знает метрику» с предупреждением, как прежде."""
    import types
    import cdpm
    name = "night_stops_total"
    wit = copy.deepcopy(law.load_witnesses())
    # Старый мост — модуль без `metrics_of`: подменяется сам объект модуля
    # у пакета (так его находит `from cdpm import catalog_bridge`); удалять
    # атрибут у настоящего моста нельзя — его собственная `metrics()` зовёт
    # `metrics_of` по имени.
    monkeypatch.setattr(cdpm, "catalog_bridge", types.SimpleNamespace())
    wit["kotlin"]["metrics"][name] = {
        "constraint": "nightStopsTotal", "tasks": ["rostering"],
        "refusal": None, "approximation": None}
    folder = cc.copy_catalog(tmp_path)
    _кандидат_метрика(folder, name, "ROSTERING")
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    l08b = rule_of(v, "L08b")
    print("\n" + l08b.line())
    assert l08b.ok is None and l08b.warning is True and name in l08b.skipped


def _семья_с_ограничением(folder: Path):
    """(id семьи, литерал ограничения) по привязкам копии: семья вида
    `kotlin_constraint` со строкой «есть». Свидетель мягкой семьи берётся
    из данных копии, а не из литерала теста."""
    doc = json.loads((folder / cc.BINDINGS_FILE).read_text(encoding="utf-8"))
    meta = tc.load(folder).families_meta()
    for row in doc.get("bindings") or []:
        if row.get("section") != "families":
            continue
        m = meta.get(row.get("id"))
        if m is None or m["kind"] != "HARD":
            continue
        for ex in row.get("executors") or []:
            if ex.get("kind") == "kotlin_constraint" \
                    and ex.get("state") == "есть" and ex.get("constraint"):
                return row["id"], ex["constraint"]
    return None, None


def test_семья_истории_и_мягкая_семья_кандидатом(law, tmp_path):
    """L15 и L14 в обе стороны: решатель назвал семью историей (мягкой) —
    живой каталог краснеет словами, кандидат нужной природы (вида) делает
    правило зелёным.

    С выпуска 0.5.0 «нужная природа» — это ДВА поля: сама природа и
    свидетель, который её подтверждает (`nature_confirmed_by`). Запись
    со свидетелем «none» (отрицательное свидетельство: семьи нет ни у
    решателя, ни у сертификатора) краснеет ровно потому, что решатель
    семью теперь знает, — и это закон, а не помеха стенду."""
    folder = cc.copy_catalog(tmp_path)
    fid, constraint = _семья_с_ограничением(folder)
    if fid is None:
        pytest.skip("в привязках копии нет семьи со строкой «есть» вида "
                    "kotlin_constraint")
    bindings = folder / cc.BINDINGS_FILE

    # 1. природа: решатель объявил семью историей
    wit = copy.deepcopy(law.load_witnesses())
    wit["kotlin"]["history_families"] = sorted(
        set(wit["kotlin"]["history_families"]) | {fid})
    red = law.judge(folder, bindings=bindings, witnesses=wit)
    r = rule_of(red, "L15")
    print("\nживой каталог: " + r.line())
    assert r.ok is False and fid in r.words and "history" in r.words
    rec = tc.load(folder).family(fid)
    rec = {k: copy.deepcopy(v) for k, v in rec.items() if k != "history"}
    rec["nature"] = "history"
    # 0.5.0: природу подтверждает НАЗВАННЫЙ свидетель, и у природы
    # «история» он один — таблицы решателя; чтение истории такая запись
    # обязана назвать сама (X19).
    rec["nature_confirmed_by"] = "solver_tables"
    rec["reads_history"] = {"kind": "series",
                            "need": "серия смен до горизонта (стенд L15)"}
    _candidate(folder, "families", fid, rec)
    green = law.judge(folder, with_candidates=True, witnesses=wit)
    print("кандидат природы: " + rule_of(green, "L15").line())
    assert rule_of(green, "L15").ok is True, rule_of(green, "L15").words

    # 2. вид: решатель объявил ту же семью мягкой
    (folder / "candidates" / "families" / f"{fid}.json").unlink()
    wit2 = copy.deepcopy(law.load_witnesses())
    wit2["kotlin"]["family_to_constraint"][fid] = constraint
    red2 = law.judge(folder, bindings=bindings, witnesses=wit2)
    r2 = rule_of(red2, "L14")
    print("живой каталог: " + r2.line())
    assert r2.ok is False and fid in r2.words
    rec2 = tc.load(folder).family(fid)
    rec2 = {k: copy.deepcopy(v) for k, v in rec2.items() if k != "history"}
    rec2["kind"] = "SOFT"
    rec2["violation_unit"] = rec2["violation_unit"] or sorted(
        tc.load(folder).dictionaries()["violation_units"])[0]
    _candidate(folder, "families", fid, rec2)
    green2 = law.judge(folder, with_candidates=True, witnesses=wit2)
    print("кандидат вида: " + rule_of(green2, "L14").line())
    assert rule_of(green2, "L14").ok is True, rule_of(green2, "L14").words


#: Пробная людская семья конвейера пополнения: имя нарочно не похоже на
#: семьи продукта — стенд судит СВИДЕТЕЛЯ, а не имя.
PROBE_PEOPLE = "probe_people_family"


def _заявка(folder: Path, fid: str, *, nature: str) -> None:
    """Принятая владельцем заявка на семью — В ОЧЕРЕДЬ КОПИИ. Заявки
    лежат вне отпечатка каталога, поэтому файл правится прямо: свидетель
    `change_request` читает именно её черновик."""
    path = folder / "requests.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = [q for q in (doc.get("requests") or [])
            if str(q.get("record_id")) != fid]
    rows.append({
        "cr_key": "cr00000015",
        "decision": {"at": "2026-09-12T10:00:00+03:00", "by": "владелец",
                     "words": "стенд свидетеля природы: заявка принята"},
        "draft": {"id": fid, "nature": nature},
        "executor_kinds_requested": None,
        "record_id": fid,
        "request_id": "req-00000000000000000015",
        "section": "families",
        "source": {"document_class": "документ свода правил авиакомпании",
                   "kind": "посадка категории C",
                   "ref": "стенд#l15", "run_class": "прогон CDPM"},
        "state": "принята",
        "submitted_at": "2026-09-12T09:00:00+03:00",
        "submitted_by": "CDPM"})
    doc["requests"] = rows
    path.write_bytes((json.dumps(doc, ensure_ascii=False, indent=1)
                      + "\n").encode("utf-8"))


def _без_заявки(folder: Path, fid: str) -> None:
    path = folder / "requests.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["requests"] = [q for q in (doc.get("requests") or [])
                       if str(q.get("record_id")) != fid]
    path.write_bytes((json.dumps(doc, ensure_ascii=False, indent=1)
                      + "\n").encode("utf-8"))


def test_l15_природу_подтверждает_названный_свидетель(law, wit, tmp_path):
    """Людская семья конвейера пополнения (выпуск 0.5.0): её природу
    подтверждает НЕ перечень сертификата ростера (его ведёт тот же
    контур, и агенту он неприкосновенен — PARITY_WITNESS_CONSTS), а
    вердикт по принятой владельцем заявке. Свидетеля называет запись, и
    закон спрашивает именно его: заявка о другой природе и отсутствие
    заявки — красное словами."""
    folder = cc.copy_catalog(tmp_path, families={
        PROBE_PEOPLE: {"kind": "HARD", "zone": "ROSTERING",
                       "nature": "people",
                       "nature_confirmed_by": "change_request",
                       "violation_unit": None, "violation_size": None,
                       "reads_history": None, "check_templates": [],
                       "executor_kinds": ["check_code"]}})
    bindings = folder / cc.BINDINGS_FILE
    assert PROBE_PEOPLE not in set(wit["certify"]["people"]), (
        "стенд опирался бы на перечень сертификата — свидетель не "
        "независим")

    _заявка(folder, PROBE_PEOPLE, nature="people")
    green = law.judge(folder, bindings=bindings, witnesses=wit)
    r = rule_of(green, "L15")
    print("\nсвидетель заявки: " + r.line())
    assert r.ok is True, r.words

    _заявка(folder, PROBE_PEOPLE, nature="schedule")
    red = law.judge(folder, bindings=bindings, witnesses=wit)
    r2 = rule_of(red, "L15")
    print("заявка о другой природе: " + r2.line())
    assert r2.ok is False and PROBE_PEOPLE in r2.words
    assert "schedule" in r2.words

    _без_заявки(folder, PROBE_PEOPLE)
    red2 = law.judge(folder, bindings=bindings, witnesses=wit)
    r3 = rule_of(red2, "L15")
    print("заявки нет: " + r3.line())
    assert r3.ok is False and PROBE_PEOPLE in r3.words
    assert "не знает" in r3.words


def test_отношение_владельца_вне_kotlin_законно(verdict):
    """Положительная половина стенда отношений: имя вне таблиц решателя с
    отношением из жёсткого блока `params_relation_set` — зелёный L05b, и
    таких имён на дереве не ноль (иначе правило зеленело бы пустотой)."""
    r = check(verdict, "L05b")
    assert r.ok is True, r.words
    assert r.numbers["by_owner"] >= 1, \
        "ни одного отношения по слову владельца — свидетель пуст"
    assert r.numbers["outside_solver"] >= 1


def test_после_выпуска_паритет_зелёный_после_цепочки(law, wit, tmp_path):
    """Требование (3) пакета на копии: после выпуска цепочка `--refresh`
    делает паритет зелёным БЕЗ единой правки числа человеком.

    Копия с выпусками правится службой (заметка записи), выпускается
    писателем (`write_catalog` ⊕ `snapshot_release`) — и посев с дельтой
    отстают: L23 красен и называет команду. Одна команда `--refresh` — и
    вердикт зелёный целиком; шесть файлов разделов при этом не меняются
    ни на байт (посев и дельта — единственное, что освежается), а журнал
    версий непуст. До пакета этот шаг делался правкой дельты рукой (две
    строки в коммите выпуска 0.2.0)."""
    import shutil
    from template_catalog import diff as tcdiff
    from template_catalog import writer as tcw
    folder = cc.copy_catalog(tmp_path, releases=True)
    cc.copy_tools(folder)
    bindings = folder / cc.BINDINGS_FILE

    cat = tc.load(folder)
    прежние = cat.versions()
    основа = cat.body()
    тело = cat.body()
    правленая = next(r for r in тело["families"] if r["status"] == "есть")
    правленая["note"] = "заметка выпуска стенда паритета"
    # Меняется только заметка: обычная классификация обязана дать patch.
    # Принудительный minor ошибочно переносил 0.6 в схему 0.7 без миграции
    # и отказывал ещё до проверяемой цепочки refresh.
    changes = tcdiff.diff_bodies(основа, тело)
    level = tcdiff.suggest_level(changes)
    assert level == "patch"
    версия = tcdiff.next_version(cat.version(), level)
    head = tcw.build_header(
        cat.header, version=версия, released_by="владелец",
        released_at="2026-09-09", note="выпуск стенда паритета", body=тело,
        legacy_registry_rev=tcw.legacy_registry_rev(cat.families()))
    запись = {"version": версия, "fingerprint": head["fingerprint"],
              "released_at": "2026-09-09", "released_by": "владелец",
              "code": "a" * 40,
              "changes": tcdiff.as_journal(changes)}
    порядок = json.loads((folder / "transition" / "families_order.json")
                         .read_text(encoding="utf-8"))
    tcw.write_catalog(folder, header=head, families=тело["families"],
                      criteria=тело["criteria"],
                      dictionaries=тело["dictionaries"],
                      requests=cat.requests(),
                      versions=прежние + [запись], families_order=порядок)
    tcw.snapshot_release(folder, версия)

    разделы = ("catalog.json", "families.json", "criteria.json",
               "dictionaries.json", "requests.json", "versions.json")
    до = {n: (folder / n).read_bytes() for n in разделы}

    красный = law.judge(folder, bindings=bindings, witnesses=wit)
    l23 = rule_of(красный, "L23")
    print("\nсразу после выпуска: " + l23.line())
    assert l23.ok is False
    assert "--refresh" in l23.words

    rc = law.bfc.main(["--out", str(folder), "--refresh"])
    assert rc == 0

    зелёный = law.judge(folder, bindings=bindings, witnesses=wit)
    for r in зелёный.rules:
        print("  " + r.line())
    # Зелёный ИЛИ красный ТОЛЬКО заявленными пробелами синтетических
    # записей режима роста каталога (`cc.verdict_ok`): исполнителя и
    # стендовых задач у синтетической записи нет по замыслу.
    assert cc.verdict_ok(зелёный), зелёный.words
    assert rule_of(зелёный, "L23").ok is True
    после = {n: (folder / n).read_bytes() for n in разделы}
    assert после == до, "цепочка после выпуска тронула разделы каталога"
    assert tc.load(folder).versions(), "журнал версий пуст"
    выпуск = law.judge(folder / "releases" / версия, bindings=bindings,
                       witnesses=wit)
    print("папка выпуска: " + json.dumps(выпуск.counts(), ensure_ascii=False))
    # Тот же закон, что выше: заявленные пробелы синтетических записей
    # режима роста каталога зелёному вердикту равны.
    assert cc.verdict_ok(выпуск), выпуск.words


def test_ветка_роста_таблиц_зелена_по_существу(law, tmp_path):
    """Ветка класса «рост таблиц решателя» ПО СУЩЕСТВУ зелена там, где
    судит `pytest -q` клона (решение 0.9, требование 0.3).

    Решатель вырос на ручку (свидетель подменён данными), кандидат
    объявил её параметром записи, живой каталог не тронут. Судятся ровно
    те суждения, которые делает клон: вердикт «живой ⊕ кандидаты»,
    свежесть носителей живого каталога (L23 — рост Kotlin на неё не
    влияет) и воспроизводимость судимой папки инструментом
    (`--check --from-catalog`, он же L18). Красным остаётся только то,
    что красно по делу: живой каталог без кандидата не знает новой
    ручки."""
    import shutil
    # Копия — ЖИВОЙ каталог дерева (без кандидатов ветки): своего
    # кандидата стенд кладёт рядом сам, и слияние чужого спутало бы
    # свежесть посева, который стенд сюда же копирует.
    folder = cc.copy_catalog(tmp_path / "дерево", candidates=False)
    name, unit, relation = _свободная_ручка(folder), "count", "max"
    wit = _witnesses_with_knob(law, name, unit, relation)
    # Папка инструмента + освежённые носители: посев инструмента и
    # дельта приколочены к отпечатку каталога, и копия, выросшая в
    # режиме роста, обязана нести их пересчитанными (иначе L23 краснеет
    # по построению стенда, а не по делу).
    cc.copy_tools(folder, kotlin=wit["kotlin"])
    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": name, "unit": unit,
                                   "relation": relation, "qualifier": None,
                                   "note": None}))
    # Кандидаты ДЕРЕВА кладутся ФАЙЛАМИ рядом, а не сливаются в разделы
    # (`candidates=False` выше): свежесть посева живой копии остаётся
    # той же, а судимый каталог становится тем же, что и на дереве, —
    # «живой ⊕ кандидаты». Без них таблицы решателя ветки знают запись,
    # которой в судимой папке нет, и L08/L18/L22 красны по построению.
    _place_tree_candidates(folder)
    v = law.judge(folder, with_candidates=True, witnesses=wit)
    for r in v.rules:
        print("  " + r.line())
    # Зелёный ИЛИ красный ТОЛЬКО заявленными пробелами синтетических
    # записей режима роста каталога (`cc.verdict_ok`): исполнителя и
    # стендовых задач у синтетической записи нет по замыслу.
    assert cc.verdict_ok(v), v.words

    живой = law.judge(folder, bindings=folder / cc.BINDINGS_FILE,
                      witnesses=wit)
    l23 = rule_of(живой, "L23")
    print("\nживой каталог: " + l23.line() + " | " + rule_of(живой, "L05").line())
    assert l23.ok is True, l23.words
    assert rule_of(живой, "L05").ok is False, \
        "живой каталог без кандидата обязан краснеть по делу"

    with law.Judged(folder, with_candidates=True) as judged:
        rc, diff, info = law.bfc.check_from_catalog(judged.path,
                                                    kt=wit["kotlin"])
    print(f"инструмент на судимой папке: код {rc} | расхождений {len(diff)}")
    assert rc == 0, diff[:4]


def test_запись_вне_базы_задним_числом_красная(law, wit, tmp_path):
    """L17 у записи вне базы заморозки: первая дата истории обязана быть
    датой окна заморозки либо позже неё.

    Иначе новая запись датируется раньше заморозки и выглядит старше
    снимка, а множество «известных дат» её не ловит: в него входит
    `delta.dates`, выведенная из тех же хвостов, — проверка была бы
    тавтологией."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    base = json.loads((TOOLS / "catalog_seed.json")
                      .read_text(encoding="utf-8"))["base"]
    новая = next((r for r in cat.families_full(status=None)
                  if r["id"] not in set(base["families"])), None)
    if новая is None:
        новая = next((r for r in cat.criteria(status=None).values()
                      if r["id"] not in set(base["criteria"])), None)
        раздел = "criteria"
    else:
        раздел = "families"
    if новая is None:
        pytest.skip("записей вне базы заморозки в каталоге нет")
    рано = "2025-01-01"
    assert рано not in set(wit["snapshot"]["known_dates"])
    ключ = "families.json" if раздел == "families" else "criteria.json"
    doc = json.loads((folder / ключ).read_text(encoding="utf-8"))
    for r in doc[раздел]:
        if r["id"] == новая["id"]:
            r["history"][0] = dict(r["history"][0], date=рано)
    (folder / ключ).write_bytes(
        (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
        .encode("utf-8"))
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    r = rule_of(v, "L17")
    print("\n" + r.line())
    assert r.ok is False
    assert "задним числом" in r.words and новая["id"] in r.words


def test_негодный_перечень_маркеров_не_зеленит_l11(law, wit, tmp_path,
                                                   monkeypatch):
    """L11 без запретных слов не зеленеет молча: негодный перечень
    маркеров заказчика — пропуск СЛОВАМИ с признаком предупреждения.

    Прежде любое исключение при чтении перечня давало `markers 0` и
    зелёное правило: чем хуже свидетель, тем охотнее оно зеленело."""
    from tools import customer_markers as cmk

    def падает():
        raise ValueError("перечень маркеров не по форме")

    monkeypatch.setattr(cmk, "load_checks", падает)
    folder = cc.copy_catalog(tmp_path)
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE, witnesses=wit)
    r = rule_of(v, "L11")
    print("\n" + r.line())
    assert r.ok is None and r.warning is True
    assert "маркеров заказчика" in r.skipped
    assert v.as_dict()["counts"]["warning"] >= 1


def test_негодный_кандидат_не_оставляет_временной_папки(law, wit, tmp_path):
    """Отказ разбора кандидата называет ФАЙЛ и не оставляет за собой
    временной папки: прежде `mkdtemp` делался до разбора, и каждый
    прогон гейта или самопроверки агента на негодном кандидате ронял
    папку `parity-…` в системный temp."""
    import tempfile
    folder = cc.copy_catalog(tmp_path)
    плохой = folder / "candidates" / "families" / "bad.json"
    плохой.parent.mkdir(parents=True, exist_ok=True)
    плохой.write_text('{"form_version": 1, "section": "nothing"}',
                      encoding="utf-8")
    # Свой temp на время теста: общий системный temp делят соседние
    # прогоны (гейт в двух режимах идёт параллельно), и чужая папка
    # `parity-…`, рождённая между двумя снимками, красила тест не по делу.
    свой_temp = tmp_path / "temp"
    свой_temp.mkdir()
    прежний = tempfile.tempdir
    tempfile.tempdir = str(свой_temp)
    try:
        было = {p.name for p in свой_temp.glob("parity-*")}
        with pytest.raises(law.ParityError) as e:
            law.judge(folder, with_candidates=True, witnesses=wit)
        стало = {p.name for p in свой_temp.glob("parity-*")}
    finally:
        tempfile.tempdir = прежний
    print("\n" + str(e.value)[:200])
    assert "bad.json" in str(e.value)
    assert стало == было, sorted(стало - было)


def test_bindings_не_читается_при_собранном_каталоге(law, tmp_path):
    """Контракт §1: при `--with-candidates` привязки строятся В ПАМЯТИ, а
    `--bindings` НЕ читается — и вердикт говорит об этом словами.

    Прежде оба ключа вместе судили временный каталог строками ЖИВОГО
    файла привязок, где строк нового параметра нет, и L19/L22 краснели
    не по делу."""
    folder = cc.copy_catalog(tmp_path)
    name, unit, relation = _свободная_ручка(folder), "count", "max"
    wit = _witnesses_with_knob(law, name, unit, relation)
    _candidate(folder, "families", "duty_limits",
               _record_with_param(folder, "duty_limits",
                                  {"name": name, "unit": unit,
                                   "relation": relation, "qualifier": None,
                                   "note": None}))
    v = law.judge(folder, bindings=folder / cc.BINDINGS_FILE,
                  with_candidates=True, witnesses=wit)
    print("\n" + json.dumps(v.catalog, ensure_ascii=False))
    assert "в памяти" in v.catalog["bindings"]
    assert "не читается" in v.catalog["bindings"]
    # Зелёный ИЛИ красный ТОЛЬКО заявленными пробелами синтетических
    # записей режима роста каталога (`cc.verdict_ok`): исполнителя и
    # стендовых задач у синтетической записи нет по замыслу.
    assert cc.verdict_ok(v), v.words


def test_l10_свидетель_формулы_старой_ревизии_живой(law, tmp_path):
    """L10: свидетель формулы старой ревизии СРАБАТЫВАЕТ.

    Прежде сверка была цепным сравнением `a != b != c`, то есть
    `a != b and b != c`, а снимок и блок `base` несут одну ревизию по
    построению — второй операнд был ложен всегда, и ветка «разошлась
    формула, а не данные» оставалась недостижимой при любом расхождении.
    Здесь оба расхождения судятся по отдельности и своими словами."""
    folder = cc.copy_catalog(tmp_path)
    bindings = folder / cc.BINDINGS_FILE

    # 1. формула над семьями снимка не даёт ревизию снимка
    wit = copy.deepcopy(law.load_witnesses())
    семья = sorted(wit["snapshot"]["families"])[0]
    wit["snapshot"]["families"][семья] = sorted(
        list(wit["snapshot"]["families"][семья]) + ["__подмена__"])
    v = law.judge(folder, bindings=bindings, witnesses=wit)
    r = rule_of(v, "L10")
    print("\nформула: " + r.line())
    assert r.ok is False and "разошлась формула" in r.words

    # 2. ревизия снимка и ревизия блока base разошлись
    wit2 = copy.deepcopy(law.load_witnesses())
    wit2["hard"]["base"]["legacy_registry_rev"] = "0" * 12
    v2 = law.judge(folder, bindings=bindings, witnesses=wit2)
    r2 = rule_of(v2, "L10")
    print("база: " + r2.line())
    assert r2.ok is False and "блока base" in r2.words


def test_l13_порядок_семей_судит_инвариант_а_не_пересчёт(law, wit, tmp_path):
    """L13 судит ИНВАРИАНТ порядка семей, а не пересчёт формулы писателя
    над порядком снимка.

    Формула `refresh_families_order` накопительна: служба и
    `materialize` применяют её к ПРЕЖНЕМУ порядку на диске. После двух
    сборок подряд (сначала `zeta`, потом `alpha`) на диске законно лежит
    […, zeta, alpha], а пересчёт от снимка дал бы […, alpha, zeta] —
    прежнее правило краснело со второй прибавленной семьи по построению,
    включая временный каталог пятого гейта."""
    from template_catalog import writer as tcw
    folder = cc.copy_catalog(
        tmp_path, families={"zeta_family": {"status": "есть"},
                            "alpha_family": {"status": "есть"}})
    order_path = folder / "transition" / "families_order.json"
    было = json.loads(order_path.read_text(encoding="utf-8"))
    все = [r["id"] for r in tc.load(folder).families_full()]
    без_alpha = [f for f in все if f != "alpha_family"]
    как_на_диске = tcw.refresh_families_order(
        tcw.refresh_families_order(было, без_alpha), все)

    def судить(порядок):
        order_path.write_bytes(
            (json.dumps(порядок, ensure_ascii=False, indent=1) + "\n")
            .encode("utf-8"))
        return rule_of(law.judge(folder, bindings=folder / cc.BINDINGS_FILE,
                                 witnesses=wit), "L13")

    # Хвост порядка — из ДОБАВЛЕННЫХ семей, и судится их взаимный
    # порядок, а не позиции с конца: в режиме роста каталога рядом с
    # ними в перечне стоит синтетическая запись, и «последние две»
    # краснели бы ростом каталога, а не правилом L13.
    пара = ["zeta_family", "alpha_family"]
    assert [f for f in как_на_диске if f in пара] == пара, как_на_диске
    r = судить(как_на_диске)
    print("\nдве сборки подряд: " + r.line())
    assert r.ok is True, r.words
    # тот же набор в алфавитном хвосте — тоже законен
    assert судить([f for f in как_на_диске if f not in пара]
                  + ["alpha_family", "zeta_family"]).ok

    смешанный = ["alpha_family"] + [f for f in как_на_диске
                                    if f != "alpha_family"]
    r2 = судить(смешанный)
    print("прибавленная среди базовых: " + r2.line())
    assert r2.ok is False and "среди базовых" in r2.words

    база = [f for f in как_на_диске if f not in ("zeta_family",
                                                 "alpha_family")]
    переставленный = ([база[1], база[0]] + база[2:]
                      + ["zeta_family", "alpha_family"])
    r3 = судить(переставленный)
    print("база переставлена: " + r3.line())
    assert r3.ok is False and "порядок базовых семей" in r3.words

    пропавшая = [f for f in как_на_диске if f != "zeta_family"]
    r4 = судить(пропавшая)
    print("семьи нет в порядке: " + r4.line())
    assert r4.ok is False and "zeta_family" in r4.words
