# -*- coding: utf-8 -*-
"""Дерево прогонов: индекс записей, путь от корня, сравнение ветвей,
признак золотого прогона.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`: раздел
7 «Дерево прогонов» (корень — отпечатки ИДАК, данных и ОТВЕТОВ на
анкеты; ветвь — те же входы с другим объявлением; ребро «продолжение от
артефакта родителя» — частичный прогон от готового ВМП; у прогонов
одного корня сравнение показывается рядом; золотой прогон — узел с
признаком, с которым новые ветви сравниваются по умолчанию), решение
владельца 6.3 (признак золотого ставится ТОЛЬКО подтверждением
владельца после приёмки на Prod), требования 4–6 раздела 8 и шаг 3
раскладки.

Словарь полей записи один, и он в `vmp/run_record.py`: этот модуль его
ЧИТАЕТ и второго перечня полей не заводит. Дерево — не новая сущность,
а ВЗГЛЯД на уже написанные записи: узел собирается из объявления и
итогов записи, рёбра — из `lineage`, корень — из отпечатков входов.

Почему индексатор не знает раскладок хранения. Записи лежат в трёх
местах и по-разному: у панели CDPM — `<прогоны>/run-NNNN/`, у COSMOS —
`<данные>/package-runs/<отпечаток пакета>/`, у раннера наборов — в
папке `--out`. Раскладки меняются (и уже менялись), поэтому `scan`
принимает ПЕРЕЧЕНЬ КОРНЕВЫХ КАТАЛОГОВ и ищет в них файлы записи
рекурсивно с ограничением глубины, не идя по ссылкам-перенаправлениям.
Негодная запись (не JSON, не по словарю) не роняет обход — она
попадает в перечень отказов со словами: одна испорченная папка не имеет
права скрыть дерево целиком.

Чего модуль НЕ делает намеренно:

  * не чинит записи. Расхождение со словарём — отказ словами, а не
    «прочитаем, что получится»;
  * не ставит и не снимает золотой признак сам. `mark_golden` требует
    имя подтвердившего и его слова; без них — отказ. Приёмку на Prod
    модуль судить не может, поэтому судит то, что может: подпись и
    завершённость прогона;
  * не хранит в индексе адресов служб площадки: у узла остаётся имя
    площадки (local / test / stage / prod), а перечень адресов из
    объявления в индекс не переносится — индекс носят между машинами.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

from vmp import run_record as rr

#: Версия индекса дерева. Меняется вместе со схемой.
SCHEMA = "cosmos-run-tree/v1"

#: Схема индекса: те же поля машинно. Модуль её ЧИТАЕТ.
SCHEMA_PATH = Path(__file__).resolve().with_name("run_tree.schema.json")

#: Имя файла индекса.
INDEX_FILE = "run-tree.json"

#: Глубина обхода корневого каталога по умолчанию: файл записи в самом
#: каталоге — глубина 0, `<прогоны>/run-NNNN/run-record.json` — 1,
#: `<данные>/package-runs/<отпечаток>/run-record.json` — 2. Четыре
#: уровня закрывают сегодняшние раскладки с запасом и не пускают обход
#: гулять по всему диску.
MAX_DEPTH = 4

# ---------------------------------------------------------------- слова
#: Вид ребра дерева (раздел 7 проекта).
EDGE_ROOT = "корень"
EDGE_SIBLING = "соседняя ветвь"
EDGE_ANSWERS = "изменены ответы"
EDGE_CONTINUATION = "продолжение"
EDGES = (EDGE_ROOT, EDGE_SIBLING, EDGE_ANSWERS, EDGE_CONTINUATION)

#: Чем ветвь отличается от соседней — крупными частями объявления.
DIFFERS = ("модели", "решатели", "параметры решателей", "каталог",
           "привязки", "код", "библиотеки", "цель", "площадка", "план")

#: Вид сравнения: с кем сравнивают.
COMPARE_PAIR, COMPARE_GOLDEN, COMPARE_PREVIOUS = ("пара", "золотой",
                                                  "предыдущий")

# --------------------------------------------------------- поля индекса
#: Индекс целиком.
TREE_FIELDS = ("schema", "built_at", "roots", "orphans", "refusals")

#: Корень дерева: входы и прогоны на них.
ROOT_FIELDS = ("root_sha256", "root_answers_sha256", "airline", "documents",
               "runs")

#: Узел дерева — прогон. Плоский взгляд на запись: им же судится
#: сравнение ветвей, поэтому в узле лежит всё, что сравнивается
#: (объявление крупными частями и итоги), и не лежит ничего, что растёт
#: без меры (перечень пакетов, журнал этапов, адреса служб).
NODE_FIELDS = (
    "run_id", "path", "created_at", "created_by", "purpose", "contour",
    "state", "declaration_sha256", "root_sha256", "root_answers_sha256",
    "airline", "catalog", "catalog_extension", "bindings", "code",
    "libraries", "models", "solvers", "planned_stages", "stop_after",
    "parent_run", "branch_of", "source_run", "package_sha256", "edge",
    "verdicts", "timing", "tokens", "counters", "systems", "golden")

#: Расход моделей узла: ключ «роль/модель» → эти поля.
TOKENS_FIELDS = ("input_tokens", "output_tokens", "calls", "counted")

#: Вложенные словари индекса: путь → состав полей (читается так же, как
#: `run_record.NESTED_FIELDS`: точка — вложенный словарь, «[]» —
#: элемент списка). Схема описывает ровно эти поля, и согласие модуля со
#: схемой судится стендом перебором.
NESTED_FIELDS = {
    "roots[]": ROOT_FIELDS,
    "roots[].documents": ("rules", "data"),
    "roots[].documents.rules[]": ("name", "sha256"),
    "roots[].documents.data[]": ("name", "sha256"),
    "roots[].runs[]": NODE_FIELDS,
    "roots[].runs[].catalog": ("name", "version", "fingerprint"),
    "roots[].runs[].catalog_extension": ("name", "version", "fingerprint"),
    "roots[].runs[].bindings": ("catalog_version", "catalog_fingerprint"),
    "roots[].runs[].code": ("cdpm_commit", "cosmos_commit",
                            "runner_fingerprint"),
    "roots[].runs[].libraries": ("python", "packages_sha256"),
    "roots[].runs[].models": (),
    "roots[].runs[].solvers": (),
    "roots[].runs[].edge": ("kind", "to", "differs", "words"),
    "roots[].runs[].timing": ("machine_s", "waiting_human_s", "total_s"),
    "roots[].runs[].golden": ("confirmed", "by", "at", "words"),
    "orphans[]": NODE_FIELDS,
    "refusals[]": ("path", "words"),
}

#: Поля узла, которые сравниваются как ОБЪЯВЛЕНИЕ (требование 5 раздела
#: 8 проекта: ветви — модели, решатели, параметры). Порядок — порядок
#: слов в сравнении.
COMPARED_DECLARATION = ("purpose", "contour", "catalog", "catalog_extension",
                        "bindings", "code", "libraries", "models", "solvers",
                        "planned_stages", "stop_after")

#: Крупная часть объявления по имени поля — для слов «чем ветвь
#: отличается».
_PART_WORDS = {"models": "модели", "solvers": "решатели",
               "catalog": "каталог", "catalog_extension": "каталог",
               "bindings": "привязки", "code": "код",
               "libraries": "библиотеки", "purpose": "цель",
               "contour": "площадка", "planned_stages": "план",
               "stop_after": "план"}

#: Параметры решателя (в отличие от самого движка): ими ветви одного
#: корня различаются чаще всего.
_SOLVER_PARAMETERS = ("spent_limit_s", "unimproved_spent_limit_s", "moves",
                      "seed", "step_limit", "unimproved_step_limit",
                      "deterministic_time", "accepted_uncovered")


class RunTreeError(ValueError):
    """Дерево не собралось или узел не найден: слова расхождения.

    Отказ записи (её словаря, подписи золотого) остаётся
    `run_record.RunRecordError` — словарь полей судит он, а не дерево."""


# ------------------------------------------------------------- мелочи
def _text(value):
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def _number(value):
    """Число или пусто: счётчики и хронометраж слов не хранят."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _round(value):
    """Округление суммы секунд: 0.1 + 0.2 не должно давать хвост."""
    return None if value is None else round(value, 3)


def _is_link(path: Path) -> bool:
    """Ссылка-перенаправление (символическая ссылка либо соединение
    Windows). По ним обход не идёт: круг из ссылок обошёл бы дерево
    бесконечно, а чужая папка попала бы в индекс дважды."""
    try:
        if path.is_symlink():
            return True
        checker = getattr(os.path, "isjunction", None)
        return bool(checker(path)) if checker else False
    except OSError:
        return True


# ------------------------------------------------------------- обход
def scan(roots, max_depth: int = MAX_DEPTH, names=None) -> dict:
    """Найти записи о прогонах в перечне корневых каталогов.

    `roots` — каталоги (или прямо файлы записей); `max_depth` —
    насколько глубоко спускаться от каждого корня; `names` — имена
    файлов записи (по умолчанию `run-record.json` словаря записи).

    Возвращает `{"roots": [пути], "records": [{"path", "record"}],
    "refusals": [{"path", "words"}]}`. Негодная запись — отказ со
    словами, а не исключение: одна испорченная папка не имеет права
    скрыть дерево целиком."""
    names = tuple(names or (rr.RECORD_FILE,))
    given = [roots] if isinstance(roots, (str, Path)) else list(roots or [])
    records, refusals, seen = [], [], set()

    def take(path: Path):
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        try:
            doc = rr.load(path)
        except rr.RunRecordError as e:
            refusals.append({"path": str(path), "words": [str(e)]})
            return
        words = rr.validate(doc)
        if words:
            refusals.append({"path": str(path), "words": list(words)})
            return
        records.append({"path": str(path), "record": doc})

    for root in given:
        base = Path(root)
        if base.is_file():
            take(base)
            continue
        if not base.is_dir():
            refusals.append({"path": str(base),
                             "words": [f"корня обхода нет: {base}"]})
            continue
        stack = [(base, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                entries = sorted(folder.iterdir())
            except OSError as e:
                refusals.append({"path": str(folder),
                                 "words": [f"папка не читается: {e}"]})
                continue
            for entry in entries:
                if _is_link(entry):
                    continue
                if entry.is_file() and entry.name in names:
                    take(entry)
                elif entry.is_dir() and depth < max_depth:
                    stack.append((entry, depth + 1))
    records.sort(key=lambda x: x["path"])
    refusals.sort(key=lambda x: x["path"])
    return {"roots": [str(Path(r)) for r in given], "records": records,
            "refusals": refusals}


# -------------------------------------------------------------- узел
def _models(declaration: dict) -> dict:
    """Модели узла: роль → модель, провайдер, канал, усилие.

    Роль в объявлении может встретиться дважды (одна роль в двух
    каналах — так собирает раскладку паспорт раннера). Тогда ключом
    становится «роль/канал»: терять вторую строку нельзя, а молча
    перетирать первую — тем более."""
    rows = [m for m in (declaration.get("models") or []) if isinstance(m, dict)]
    seen = {}
    for m in rows:
        seen[str(m.get("role"))] = seen.get(str(m.get("role")), 0) + 1
    out = {}
    for m in rows:
        role = str(m.get("role"))
        key = role if seen[role] == 1 else f"{role}/{m.get('channel')}"
        out[key] = {"model": _text(m.get("model")),
                    "provider": _text(m.get("provider")),
                    "channel": _text(m.get("channel")),
                    "effort": _text(m.get("effort"))}
    return out


def _solvers(declaration: dict) -> dict:
    """Решатели узла: задача → движок, версия, сборка, лимиты, ходы,
    воспроизводимый профиль. Состав полей — из словаря записи, чтобы
    новый параметр решателя приезжал в дерево сам."""
    fields = [f for f in rr.NESTED_FIELDS["declaration.solvers[]"]
              if f != "task"]
    out = {}
    for s in (declaration.get("solvers") or []):
        if not isinstance(s, dict):
            continue
        key = str(s.get("task") or "задача не названа")
        out[key] = {f: s.get(f) for f in fields}
    return out


def _tokens(record: dict) -> dict:
    """Расход моделей прогона: «роль/модель» → вход, выход, вызовов,
    сосчитано (решение владельца № 1 — в токенах, не в деньгах).

    Складывается по журналу. Если хоть одна строка канала пришла без
    учёта (`counted` ложь — так честно помечается канал командной
    строки), сумма помечается несосчитанной: половина расхода, выданная
    за целое, хуже честного «не считается»."""
    out = {}
    for entry in (record.get("journal") or []):
        if not isinstance(entry, dict):
            continue
        models = ((entry.get("resources") or {}).get("models")
                  if isinstance(entry.get("resources"), dict) else None)
        for m in (models or []):
            if not isinstance(m, dict):
                continue
            key = f"{m.get('role') or 'роль не названа'}/" \
                  f"{m.get('model') or 'модель не названа'}"
            row = out.setdefault(key, {"input_tokens": None,
                                       "output_tokens": None, "calls": None,
                                       "counted": True})
            for field in ("input_tokens", "output_tokens", "calls"):
                value = _number(m.get(field))
                if value is not None:
                    row[field] = (row[field] or 0) + value
            row["counted"] = bool(row["counted"]) and bool(m.get("counted"))
    return out


def _systems(record: dict) -> list:
    """Какие системы отметились в записи: имена систем-летописцев журнала
    (`journal[].system` — `cdpm`, `runner`, `cosmos`) без повторов и по
    порядку словаря.

    Единица журнала без имени системы — запись хозяина шага 1 (поля
    `system` у неё ещё не было), и выдумывать ей систему нельзя: она
    просто не добавляет в перечень ничего. Перечень нужен общему экрану
    прогонов обеих систем: по нему видно, кто из систем в этой записи
    уже писал, а кто ещё нет."""
    seen = set()
    for entry in (record.get("journal") or []):
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("system"))
        if name:
            seen.add(name)
    known = [s for s in rr.SYSTEMS if s in seen]
    return known + sorted(seen - set(known))


def _counters(record: dict) -> dict:
    """Счётчики прогона: имя → сумма по этапам журнала. Числа читаются
    как есть; их сравнение — «справочно» (раздел 3.3 проекта: число
    гуляет по чтению модели)."""
    out = {}
    for entry in (record.get("journal") or []):
        if not isinstance(entry, dict):
            continue
        for name, value in (entry.get("counters") or {}).items():
            number = _number(value)
            if number is None:
                continue
            out[str(name)] = (out.get(str(name)) or 0) + number
    return out


def node(record: dict, path=None) -> dict:
    """Узел дерева из записи о прогоне: плоский взгляд на объявление и
    итоги. Журнал в узел не переносится — узел показывают списком, а
    журнал читают в самой записи (`run_record.summary`)."""
    if not isinstance(record, dict) or not isinstance(
            record.get("declaration"), dict):
        raise RunTreeError("узел дерева не собран: это не запись о прогоне "
                           "(нет объявления)")
    d = record["declaration"]
    res = record.get("results") if isinstance(record.get("results"), dict) else {}
    lin = d.get("lineage") or {}
    cat = d.get("catalog") or {}
    ext = d.get("catalog_extension") or None
    bind = d.get("bindings") or {}
    code = d.get("code") or {}
    libs = d.get("libraries") or {}
    golden = res.get("golden") or {}
    timing = res.get("timing") or {}
    return {
        "run_id": _text(d.get("run_id")),
        "path": _text(path) if path is not None else None,
        "created_at": _text(d.get("created_at")),
        "created_by": _text(d.get("created_by")),
        "purpose": _text(d.get("purpose")),
        # Только имя площадки: адреса служб в индекс не переносятся —
        # индекс носят между машинами.
        "contour": _text((d.get("contour") or {}).get("name")),
        "state": _text(res.get("state")),
        "declaration_sha256": _text(record.get("declaration_sha256")),
        "root_sha256": _text(lin.get("root_sha256")),
        "root_answers_sha256": _text(lin.get("root_answers_sha256")),
        "airline": _text((d.get("airline") or {}).get("name")),
        "catalog": {"name": _text(cat.get("name")),
                    "version": _text(cat.get("version")),
                    "fingerprint": _text(cat.get("fingerprint"))},
        "catalog_extension": ({"name": _text(ext.get("name")),
                               "version": _text(ext.get("version")),
                               "fingerprint": _text(ext.get("fingerprint"))}
                              if isinstance(ext, dict) else None),
        "bindings": {"catalog_version": _text(bind.get("catalog_version")),
                     "catalog_fingerprint":
                         _text(bind.get("catalog_fingerprint"))},
        "code": {"cdpm_commit": _text(code.get("cdpm_commit")),
                 "cosmos_commit": _text(code.get("cosmos_commit")),
                 "runner_fingerprint": _text(code.get("runner_fingerprint"))},
        "libraries": {"python": _text(libs.get("python")),
                      "packages_sha256": _text(libs.get("packages_sha256"))},
        "models": _models(d),
        "solvers": _solvers(d),
        "planned_stages": [str(s) for s in (d.get("planned_stages") or [])],
        "stop_after": _text(d.get("stop_after")),
        "parent_run": _text(lin.get("parent_run")),
        "branch_of": _text(lin.get("branch_of")),
        "source_run": _text(lin.get("source_run")),
        "package_sha256": _text(lin.get("package_sha256")),
        "edge": {"kind": EDGE_ROOT, "to": None, "differs": [], "words": None},
        "verdicts": dict(res.get("verdicts") or {}),
        "timing": {f: _number(timing.get(f))
                   for f in rr.NESTED_FIELDS["results.timing"]},
        "tokens": _tokens(record),
        "counters": _counters(record),
        "systems": _systems(record),
        "golden": {"confirmed": bool(golden.get("confirmed")),
                   "by": _text(golden.get("by")),
                   "at": _text(golden.get("at")),
                   "words": _text(golden.get("words"))},
    }


def _order(item: dict):
    """Порядок прогонов: по моменту объявления, затем по номеру. Без
    момента прогон уходит в начало — он объявлен раньше, чем часы
    научились ставить пояс."""
    return (item.get("created_at") or "", item.get("run_id") or "")


# ------------------------------------------------------------ индекс
def index(records, refusals=None) -> dict:
    """Дерево прогонов из перечня записей.

    `records` — перечень `{"path", "record"}` (то, что вернул `scan`)
    либо просто записей. `refusals` — отказы обхода: они едут в индекс,
    чтобы читатель видел не только найденное, но и НЕ прочитанное.

    Корень — отпечатки входов (`lineage.root_sha256`): документы,
    данные. Ветви одного корня — прогоны на тех же входах; смена
    ответа анкеты (другой `root_answers_sha256` при том же корне) —
    ветвь с пометкой «изменены ответы»; частичный прогон от ВМП —
    ребро «продолжение» к прогону-источнику."""
    built, refused = [], list(refusals or [])
    for item in (records or []):
        if isinstance(item, dict) and isinstance(item.get("record"), dict):
            record = item["record"]
            built.append((node(record, item.get("path")), record))
        else:
            built.append((node(item), item if isinstance(item, dict) else {}))

    # Объявление узла держим рядом: имена документов корня берутся из
    # записи, а в узел они не переносятся (у ветвей одного корня они
    # одни и те же).
    origin = {id(n): (record.get("declaration") or {})
              for n, record in built}
    all_nodes = [n for n, _ in built]

    by_id = {}
    for n in all_nodes:
        if not n["run_id"]:
            continue
        if n["run_id"] in by_id:
            refused.append({
                "path": n.get("path"),
                "words": [f"номер прогона {n['run_id']} встретился дважды: "
                          f"{by_id[n['run_id']].get('path')} и "
                          f"{n.get('path')} — номер один на обе системы, "
                          f"дерево берёт первую запись"]})
            continue
        by_id[n["run_id"]] = n

    groups, orphans = {}, []
    for n in all_nodes:
        if n["root_sha256"]:
            groups.setdefault(n["root_sha256"], []).append(n)
        else:
            orphans.append(n)

    # Прогон без корня, но с названным родителем, живёт на дереве
    # родителя: частичный прогон от ВМП корня входов не считал — он его
    # унаследовал. Проходов несколько: цепочка «пакет от пакета» тоже
    # должна найти своё дерево.
    for _ in range(len(orphans) + 1):
        moved = set()
        for n in orphans:
            parent = by_id.get(n["source_run"]) or by_id.get(n["parent_run"])
            if parent and parent["root_sha256"]:
                n["root_sha256"] = parent["root_sha256"]
                groups.setdefault(parent["root_sha256"], []).append(n)
                moved.add(id(n))
        if not moved:
            break
        orphans = [n for n in orphans if id(n) not in moved]

    roots = []
    for root_sha, group in groups.items():
        group.sort(key=_order)
        _edges(group, by_id)
        first = group[0]
        answers = sorted({n["root_answers_sha256"] for n in group
                          if n["root_answers_sha256"]})
        roots.append({
            "root_sha256": root_sha,
            "root_answers_sha256": answers,
            "airline": first["airline"],
            "documents": _documents(origin.get(id(first)) or {}),
            "runs": group,
        })
    roots.sort(key=lambda r: (_order(r["runs"][0]) if r["runs"] else ("", ""),
                              r["root_sha256"]))
    orphans.sort(key=_order)
    for n in orphans:
        n["edge"] = {"kind": EDGE_ROOT, "to": None, "differs": [],
                     "words": "корень дерева не назван: прогон стоит "
                              "особняком"}
    return {"schema": SCHEMA, "built_at": rr.now_iso(), "roots": roots,
            "orphans": orphans,
            "refusals": [{"path": _text(r.get("path")),
                          "words": [str(w) for w in (r.get("words") or [])]}
                         for r in refused]}


def _documents(declaration: dict) -> dict:
    """Документы корня по ПЕРВОЙ записи дерева: имена и отпечатки.

    Отпечатки у всех ветвей одного корня одни (иначе это другой
    корень), а имена файлов в счёт корня не входят — поэтому имена
    берутся у той записи, что легла первой, и служат человеку для
    узнавания, а не для сверки."""
    rules = declaration.get("rules_documents") or []
    data = declaration.get("data_files") or []
    return {"rules": [{"name": _text(r.get("name")),
                       "sha256": _text(r.get("sha256"))} for r in rules],
            "data": [{"name": _text(r.get("name")),
                      "sha256": _text(r.get("sha256"))} for r in data]}


def _edges(group: list, by_id: dict):
    """Рёбра ветвей одного корня (раздел 7 проекта)."""
    base = group[0]
    base_answers = base["root_answers_sha256"]
    for n in group:
        named = n["source_run"] or n["parent_run"]
        if named:
            found = by_id.get(named)
            words = (f"продолжение от артефакта прогона {named}"
                     if found else
                     f"продолжение от прогона {named} — его записи в "
                     f"перечне нет")
            if n["branch_of"]:
                words += f"; ветвь по: {n['branch_of']}"
            n["edge"] = {"kind": EDGE_CONTINUATION, "to": named,
                         "differs": _differs(found, n) if found else [],
                         "words": words}
            continue
        if n is base:
            n["edge"] = {"kind": EDGE_ROOT, "to": None, "differs": [],
                         "words": "первый прогон на этих входах"}
            continue
        differs = _differs(base, n)
        if (n["root_answers_sha256"] and base_answers
                and n["root_answers_sha256"] != base_answers):
            n["edge"] = {
                "kind": EDGE_ANSWERS, "to": base["run_id"], "differs": differs,
                "words": f"ветвь от прогона {base['run_id']}: изменены "
                         f"ответы на анкету — корень с ответами "
                         f"{rr.short(n['root_answers_sha256'])} вместо "
                         f"{rr.short(base_answers)}"}
            continue
        n["edge"] = {"kind": EDGE_SIBLING, "to": base["run_id"],
                     "differs": differs,
                     "words": "те же входы, другое объявление"
                              + (": " + ", ".join(differs) if differs
                                 else " — расхождений в объявлении нет")}


def _differs(a: dict | None, b: dict) -> list:
    """Чем ветви различаются крупными частями: модели, решатели,
    параметры решателей, каталог, привязки, код, библиотеки, цель,
    площадка, план."""
    if not a:
        return []
    parts = []
    for row in compare(a, b)["declaration"]["changed"]:
        field = str(row["field"])
        head = field.split(".")[0]
        if head == "solvers" and field.split(".")[-1] in _SOLVER_PARAMETERS:
            parts.append("параметры решателей")
        else:
            parts.append(_PART_WORDS.get(head, head))
    return [p for p in DIFFERS if p in parts]


def build(roots, max_depth: int = MAX_DEPTH, names=None) -> dict:
    """Обход и индекс одним движением: `scan` + `index`."""
    found = scan(roots, max_depth=max_depth, names=names)
    return index(found["records"], found["refusals"])


# --------------------------------------------------------- узлы дерева
def nodes(tree: dict) -> list:
    """Все узлы дерева: ветви корней и прогоны без корня."""
    out = []
    for root in (tree or {}).get("roots") or []:
        out.extend(root.get("runs") or [])
    out.extend((tree or {}).get("orphans") or [])
    return out


def find_node(tree: dict, run_id: str):
    """Узел по номеру прогона либо пусто."""
    for n in nodes(tree):
        if n.get("run_id") == run_id:
            return n
    return None


def root_of(tree: dict, run_id: str):
    """Корень, на котором стоит прогон, либо пусто."""
    for root in (tree or {}).get("roots") or []:
        for n in root.get("runs") or []:
            if n.get("run_id") == run_id:
                return root
    return None


def _node_or_refuse(tree: dict, run_id: str) -> dict:
    found = find_node(tree, run_id)
    if not found:
        raise RunTreeError(f"прогона {run_id!r} в дереве нет: индекс собран "
                           f"из {len(nodes(tree))} записей")
    return found


def path_from_root(tree: dict, run_id: str) -> dict:
    """Путь от корня к прогону: входы → родители → прогон (раздел 7
    проекта — карточка показывает путь от корня).

    Возвращает `{"root": {…входы…}, "runs": [узлы от предка к
    прогону]}`. Родство читается по ребру узла: «продолжение» ведёт к
    прогону-источнику, «изменены ответы» и «соседняя ветвь» — к первому
    прогону корня. Круг в родстве обрывается: запись, названная своим
    же родителем, путь не рушит."""
    target = _node_or_refuse(tree, run_id)
    chain, seen, current = [], set(), target
    while current is not None and current.get("run_id") not in seen:
        chain.append(current)
        seen.add(current.get("run_id"))
        to = (current.get("edge") or {}).get("to")
        current = find_node(tree, to) if to else None
    chain.reverse()
    root = root_of(tree, run_id)
    return {"root": {k: (root or {}).get(k) for k in
                     ("root_sha256", "root_answers_sha256", "airline",
                      "documents")},
            "runs": chain}


def siblings(tree: dict, run_id: str) -> list:
    """Соседние ветви: прогоны того же корня, кроме самого прогона."""
    _node_or_refuse(tree, run_id)
    root = root_of(tree, run_id)
    if not root:
        return []
    return [n for n in (root.get("runs") or []) if n.get("run_id") != run_id]


def golden_node(tree: dict, root_sha256: str):
    """Золотой прогон корня либо пусто. Золотых больше одного быть не
    должно; если их всё же несколько — берётся последний по времени:
    образец — тот, что подтверждён позже."""
    for root in (tree or {}).get("roots") or []:
        if root.get("root_sha256") != root_sha256:
            continue
        found = [n for n in (root.get("runs") or [])
                 if (n.get("golden") or {}).get("confirmed")]
        return sorted(found, key=_order)[-1] if found else None
    return None


# --------------------------------------------------------- сравнение
def _view(value) -> dict:
    """Взгляд на прогон: узел дерева либо узел из записи."""
    if isinstance(value, dict) and "edge" in value and "run_id" in value:
        return value
    return node(value)


def _flat(value, prefix: str = "") -> dict:
    """Плоский вид словаря: «поле.поле» → значение. Списки — целиком:
    план этапов и принятые рейсы сравниваются как одно значение."""
    out = {}
    if isinstance(value, dict):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flat(value[key], name))
    else:
        out[prefix] = value
    return out


def _value_words(value) -> str:
    """Значение словами: пусто, слова в кавычках, число, перечень.

    Целое число печатается целым: «120 секунд» человеку понятнее, чем
    «120.0 секунд», а откуда взялась дробная часть (деление длительности
    по этапам), читателю знать незачем."""
    if value is None:
        return "нет"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(x) for x in value) + "]" if value else "[]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"«{value}»"


def _delta(a, b):
    """Сдвиг числа; одно из значений не число — пусто."""
    x, y = _number(a), _number(b)
    return None if x is None or y is None else _round(y - x)


def _delta_words(delta) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return " (без сдвига)"
    return f" ({'+' if delta > 0 else ''}{_value_words(delta)})"


def compare(a, b, kind: str = COMPARE_PAIR) -> dict:
    """Сравнение двух прогонов: что менялось в объявлении и что
    изменилось в итогах (раздел 7 проекта — у прогонов одного корня
    сравнение показывается рядом).

    `a` и `b` — записи о прогоне либо узлы дерева. Возвращает
    `{"kind", "a", "b", "same_root", "declaration": {"changed", "same"},
    "results": {"verdicts", "timing", "tokens", "counters"}, "words"}`.
    Счётчики помечены «справочно»: их числа гуляют по чтению модели
    (раздел 3.3 проекта), и расхождение счётчика — повод посмотреть, а
    не вердикт."""
    x, y = _view(a), _view(b)
    changed, same = [], []
    for field in COMPARED_DECLARATION:
        left = _flat(x.get(field), field)
        right = _flat(y.get(field), field)
        for name in sorted(set(left) | set(right)):
            lv, rv = left.get(name), right.get(name)
            if lv == rv:
                same.append(name)
            else:
                changed.append({"field": name, "a": lv, "b": rv})

    verdicts = {}
    for task in sorted(set(x.get("verdicts") or {})
                       | set(y.get("verdicts") or {})):
        lv = (x.get("verdicts") or {}).get(task)
        rv = (y.get("verdicts") or {}).get(task)
        verdicts[task] = {"a": lv, "b": rv, "same": lv == rv}

    timing = {}
    for field in rr.NESTED_FIELDS["results.timing"]:
        lv = (x.get("timing") or {}).get(field)
        rv = (y.get("timing") or {}).get(field)
        timing[field] = {"a": lv, "b": rv, "delta": _delta(lv, rv)}

    tokens = {}
    for key in sorted(set(x.get("tokens") or {}) | set(y.get("tokens") or {})):
        lv = (x.get("tokens") or {}).get(key) or {}
        rv = (y.get("tokens") or {}).get(key) or {}
        # Роль в разных прогонах могла идти РАЗНОЙ моделью — тогда ключ
        # есть только у одного. Это не «не считается», а «такой модели у
        # второго нет»: сравнивать её расход не с чем.
        present = [row for row in (lv, rv) if row]
        tokens[key] = {
            "a": {f: lv.get(f) for f in TOKENS_FIELDS},
            "b": {f: rv.get(f) for f in TOKENS_FIELDS},
            "delta": {f: _delta(lv.get(f), rv.get(f))
                      for f in ("input_tokens", "output_tokens", "calls")},
            "counted": all(bool(row.get("counted")) for row in present),
            "only_in": None if lv and rv else ("a" if lv else "b")}

    counters = {}
    for name in sorted(set(x.get("counters") or {})
                       | set(y.get("counters") or {})):
        lv = (x.get("counters") or {}).get(name)
        rv = (y.get("counters") or {}).get(name)
        counters[name] = {"a": lv, "b": rv, "delta": _delta(lv, rv),
                          "note": "справочно"}

    result = {
        "kind": kind, "a": x.get("run_id"), "b": y.get("run_id"),
        "same_root": bool(x.get("root_sha256"))
        and x.get("root_sha256") == y.get("root_sha256"),
        "declaration": {"changed": changed, "same": same},
        "results": {"verdicts": verdicts, "timing": timing, "tokens": tokens,
                    "counters": counters},
        "words": [],
    }
    result["words"] = _compare_words(x, y, result)
    return result


_KIND_WORDS = {COMPARE_PAIR: "прогон против прогона",
               COMPARE_GOLDEN: "против золотого прогона",
               COMPARE_PREVIOUS: "против предыдущего прогона того же корня"}


def _compare_words(x: dict, y: dict, cmp: dict) -> list:
    """Сравнение словами человеку."""
    changed = cmp["declaration"]["changed"]
    rows = [f"СРАВНЕНИЕ ПРОГОНОВ {cmp['a']} → {cmp['b']} "
            f"({_KIND_WORDS.get(cmp['kind'], cmp['kind'])})"]
    if cmp["same_root"]:
        rows.append(f"   корень один: {rr.short(x.get('root_sha256'))} — "
                    f"входы те же")
    else:
        rows.append(f"   КОРНИ РАЗНЫЕ: {rr.short(x.get('root_sha256'))} и "
                    f"{rr.short(y.get('root_sha256'))} — сравниваются прогоны "
                    f"на разных входах")
    if (x.get("root_answers_sha256") != y.get("root_answers_sha256")
            and cmp["same_root"]):
        rows.append("   ответы на анкеты разные: корень с ответами "
                    f"{rr.short(x.get('root_answers_sha256'))} против "
                    f"{rr.short(y.get('root_answers_sha256'))}")
    rows.append(f"   объявление: изменено полей {len(changed)}, совпало "
                f"{len(cmp['declaration']['same'])}")
    for row in changed:
        rows.append(f"      {row['field']}: {_value_words(row['a'])} → "
                    f"{_value_words(row['b'])}")
    if not changed:
        rows.append("      объявления совпадают полностью")
    rows.append(f"   состояние: {x.get('state')} → {y.get('state')}")
    verdicts = cmp["results"]["verdicts"]
    if verdicts:
        for task, row in verdicts.items():
            rows.append(f"   вердикт {task}: {_value_words(row['a'])} → "
                        f"{_value_words(row['b'])}"
                        + ("" if row["same"] else "  — РАСХОЖДЕНИЕ"))
    else:
        rows.append("   вердиктов ни у того, ни у другого нет")
    for field, name in (("machine_s", "время машины"),
                        ("waiting_human_s", "ожидание человека"),
                        ("total_s", "всего")):
        row = cmp["results"]["timing"][field]
        if row["a"] is None and row["b"] is None:
            continue
        rows.append(f"   {name}: {_value_words(row['a'])} → "
                    f"{_value_words(row['b'])}{_delta_words(row['delta'])}")
    for key, row in cmp["results"]["tokens"].items():
        if row["only_in"]:
            side = "б" if row["only_in"] == "a" else "а"
            tail = f"  — этой модели у прогона {side} нет"
        else:
            tail = "" if row["counted"] else "  — не считается"
        rows.append(f"   токены {key}: вход {_value_words(row['a']['input_tokens'])}"
                    f" → {_value_words(row['b']['input_tokens'])}"
                    f"{_delta_words(row['delta']['input_tokens'])}, выход "
                    f"{_value_words(row['a']['output_tokens'])} → "
                    f"{_value_words(row['b']['output_tokens'])}"
                    f"{_delta_words(row['delta']['output_tokens'])}" + tail)
    counters = {k: v for k, v in cmp["results"]["counters"].items()
                if v["a"] != v["b"]}
    if counters:
        rows.append("   счётчики (справочно — число гуляет по чтению модели):")
        for name, row in counters.items():
            rows.append(f"      {name}: {_value_words(row['a'])} → "
                        f"{_value_words(row['b'])}{_delta_words(row['delta'])}")
    for who, n in (("а", x), ("б", y)):
        if (n.get("golden") or {}).get("confirmed"):
            rows.append(f"   прогон {who} ({n.get('run_id')}) — ЗОЛОТОЙ: "
                        f"{(n.get('golden') or {}).get('by')}, "
                        f"{(n.get('golden') or {}).get('at')}")
    return rows


def compare_with_golden(tree: dict, run_id: str):
    """Сравнение прогона с золотым прогоном ТОГО ЖЕ КОРНЯ (раздел 7
    проекта: новые ветви сравниваются с золотым по умолчанию).

    Золотого у корня нет либо золотой — это он сам: пусто, и решение,
    что сказать человеку, остаётся за показом."""
    n = _node_or_refuse(tree, run_id)
    gold = golden_node(tree, n.get("root_sha256"))
    if not gold or gold.get("run_id") == run_id:
        return None
    return compare(gold, n, COMPARE_GOLDEN)


def compare_with_previous(tree: dict, run_id: str):
    """Сравнение прогона с предыдущим прогоном того же корня (раздел
    3.3 проекта — сдвиги чисел против предыдущего прогона). Прогон
    первый на корне: пусто."""
    n = _node_or_refuse(tree, run_id)
    root = root_of(tree, run_id)
    if not root:
        return None
    earlier = [x for x in (root.get("runs") or [])
               if x.get("run_id") != run_id and _order(x) < _order(n)]
    if not earlier:
        return None
    return compare(sorted(earlier, key=_order)[-1], n, COMPARE_PREVIOUS)


# ---------------------------------------------------------- золотой
def mark_golden(record: dict, *, by: str, words: str, at: str | None = None,
                state: str | None = None) -> dict:
    """Назвать прогон золотым (прогоном-образцом).

    Решение владельца 6.3 от 10.09.2026: после приёмки на Prod система
    ЗАПРАШИВАЕТ владельца, и признак ставится только по его
    подтверждению. Поэтому подпись обязательна: кто подтвердил (`by`),
    какими словами (`words`) и когда (`at`, по умолчанию — сейчас).
    Подтверждения нет — отказ, а не «поставим и разберёмся».

    Приёмку на Prod модуль судить не может (её судит гейт трёх наборов),
    поэтому судит то, что видит: незавершённый прогон образцом не
    бывает — сравнивать с ним новые ветви было бы сравнением с
    незаконченным."""
    if not isinstance(record, dict):
        raise rr.RunRecordError("золотым можно назвать запись о прогоне, а "
                                "не что-то другое")
    if not _text(by):
        raise rr.RunRecordError(
            "золотым прогон не назван: не сказано, КТО подтвердил. Признак "
            "ставится только словом владельца после приёмки на Prod "
            "(решение владельца 6.3 от 10.09.2026)")
    if not _text(words):
        raise rr.RunRecordError(
            "золотым прогон не назван: не сказано, КАКИМИ СЛОВАМИ владелец "
            "подтвердил — подпись без слов не подпись")
    now = (record.get("results") or {}).get("state")
    if now != rr.STATE_DONE:
        raise rr.RunRecordError(
            f"золотым может быть только завершённый прогон: сейчас "
            f"состояние «{now or 'не названо'}» — образцом незаконченный "
            f"прогон не бывает")
    return rr.finish(record, {"golden": {"confirmed": True, "by": _text(by),
                                         "at": _text(at) or rr.now_iso(),
                                         "words": _text(words)}},
                     state=_text(state) or now)


def unmark_golden(record: dict) -> dict:
    """Снять признак золотого прогона: подтверждение отозвано (нашёлся
    прогон лучше либо признак поставлен по ошибке). Слова подтверждения
    снимаются вместе с признаком — иначе запись говорила бы, что
    владелец подтвердил то, чего в ней уже нет."""
    if not isinstance(record, dict):
        raise rr.RunRecordError("снять признак можно у записи о прогоне")
    state = (record.get("results") or {}).get("state")
    return rr.finish(record, {"golden": {"confirmed": False, "by": None,
                                         "at": None, "words": None}},
                     state=state)


# ------------------------------------------------------------- схема
@functools.lru_cache(maxsize=1)
def schema_document() -> dict:
    """Схема индекса из файла рядом с модулем."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_node(path: str = "") -> dict:
    """Узел схемы индекса по пути: точка — вложенный словарь, «[]» —
    элемент списка. Пути нет — RunTreeError."""
    root = schema_document()
    # Разыменование ссылок — общее с записью: слова отказа у обеих схем
    # одни, и второго разыменователя в проекте нет.
    current = rr._resolve(root, root)
    for step in [s for s in str(path).split(".") if s]:
        name, arrays = step, 0
        while name.endswith("[]"):
            name, arrays = name[:-2], arrays + 1
        props = (current.get("properties") or {})
        if name not in props:
            raise RunTreeError(f"схема дерева не описывает поле {path!r} "
                               f"(нет {name!r})")
        current = rr._resolve(props[name], root)
        for _ in range(arrays):
            current = rr._resolve(current.get("items") or {}, root)
    return current


def schema_properties(path: str = "") -> dict:
    """Поля схемы индекса по пути: {имя поля: узел схемы}."""
    return dict(schema_node(path).get("properties") or {})


@functools.lru_cache(maxsize=1)
def _validator():
    import jsonschema
    return jsonschema.Draft202012Validator(schema_document())


def validate_index(tree) -> list:
    """Слова расхождений индекса со схемой. Пусто — индекс сходится.

    Слова отказа берутся у записи (`run_record`): у двух схем одного
    проекта расхождение обязано звучать одинаково, а второй словарь
    слов расходился бы с первым на второй же правке."""
    errors = sorted(_validator().iter_errors(tree),
                    key=lambda e: (list(map(str, e.absolute_path)),
                                   str(e.validator)))
    return [rr._error_words(e) for e in errors]


# ------------------------------------------------------------ хранение
def save_index(path, tree: dict):
    """Индекс файлом атомарно: сперва соседний временный файл, затем
    замена (как у записи — оборванный индекс хуже отсутствующего)."""
    p = Path(path)
    if p.is_dir():
        p = p / INDEX_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(tree, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_index(path) -> dict:
    """Индекс из файла. Путь может указывать на папку: тогда имя файла
    — INDEX_FILE."""
    p = Path(path)
    if p.is_dir():
        p = p / INDEX_FILE
    if not p.is_file():
        raise RunTreeError(f"индекса дерева нет: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RunTreeError(f"индекс дерева не разобран как JSON: {p} — "
                           f"{e}") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise RunTreeError(f"это не индекс дерева прогонов ({SCHEMA}): {p}")
    return doc


# ------------------------------------------------------ слова человеку
def render(tree: dict) -> str:
    """Дерево словами: корни со входами, ветви с рёбрами, состояния,
    золотые прогоны, отказы обхода. Из этого текста собирается карточка
    дерева — человек читает не JSON."""
    t = tree if isinstance(tree, dict) else {}
    all_nodes = nodes(t)
    gold = [n for n in all_nodes if (n.get("golden") or {}).get("confirmed")]
    rows = [f"ДЕРЕВО ПРОГОНОВ ({t.get('schema')}), собрано "
            f"{t.get('built_at')}",
            f"   корней {len(t.get('roots') or [])}, прогонов "
            f"{len(all_nodes)}, из них золотых {len(gold)}; без корня "
            f"{len(t.get('orphans') or [])}; отказов "
            f"{len(t.get('refusals') or [])}"]
    for root in (t.get("roots") or []):
        docs = root.get("documents") or {}
        rows.append("")
        rows.append(f"КОРЕНЬ {rr.short(root.get('root_sha256'))} — "
                    f"авиакомпания {root.get('airline') or 'не названа'}; "
                    f"прогонов {len(root.get('runs') or [])}")
        rows.append("   входы: "
                    + (", ".join(f"{d.get('name')} {rr.short(d.get('sha256'), 8)}"
                                 for d in (docs.get("rules") or []))
                       or "документов нет")
                    + "; данные: "
                    + (", ".join(f"{d.get('name')} {rr.short(d.get('sha256'), 8)}"
                                 for d in (docs.get("data") or []))
                       or "нет"))
        answers = root.get("root_answers_sha256") or []
        rows.append("   ответы на анкеты: "
                    + (", ".join(rr.short(a, 8) for a in answers)
                       if answers else "ревизий ещё нет")
                    + (" — ветвей по ответам "
                       f"{len(answers)}" if len(answers) > 1 else ""))
        for n in (root.get("runs") or []):
            rows.extend(_node_words(n))
    if t.get("orphans"):
        rows.append("")
        rows.append("БЕЗ КОРНЯ (входы не названы отпечатками):")
        for n in t["orphans"]:
            rows.extend(_node_words(n))
    if t.get("refusals"):
        rows.append("")
        rows.append("ОТКАЗЫ ОБХОДА:")
        for r in t["refusals"]:
            rows.append(f"   {r.get('path')}")
            for w in (r.get("words") or []):
                rows.append(f"      ! {w}")
    return "\n".join(rows)


def _node_words(n: dict) -> list:
    """Узел словами: строка прогона и строка объявления под ней."""
    edge = n.get("edge") or {}
    cat = n.get("catalog") or {}
    timing = n.get("timing") or {}
    golden = n.get("golden") or {}
    rows = [f"   • {n.get('run_id')}  {n.get('created_at')}  "
            f"{n.get('state') or 'состояние не названо'}  "
            f"[{edge.get('kind')}]"
            + (f" — {edge.get('words')}" if edge.get("words") else "")]
    rows.append(f"        цель {n.get('purpose') or 'не названа'}, площадка "
                f"{n.get('contour') or 'не названа'}, каталог "
                f"{cat.get('version') or 'не назван'}; модели: "
                + (", ".join(f"{role}={row.get('model')}"
                             for role, row in sorted((n.get("models") or {})
                                                     .items()))
                   or "не названы")
                + "; решатели: "
                + (", ".join(f"{task}={row.get('engine')}"
                             for task, row in sorted((n.get("solvers") or {})
                                                     .items()))
                   or "не названы"))
    verdicts = n.get("verdicts") or {}
    rows.append("        вердикты: "
                + (", ".join(f"{k}={v}" for k, v in sorted(verdicts.items()))
                   or "нет")
                + ("; машина не считалась" if timing.get("machine_s") is None
                   else f"; машина {_value_words(timing.get('machine_s'))} с")
                + ("; токены: " + ", ".join(
                    f"{k} {row.get('input_tokens')}/{row.get('output_tokens')}"
                    + ("" if row.get("counted") else " (не считается)")
                    for k, row in sorted((n.get("tokens") or {}).items()))
                   if n.get("tokens") else ""))
    if golden.get("confirmed"):
        rows.append(f"        ЗОЛОТОЙ ПРОГОН: {golden.get('by')}, "
                    f"{golden.get('at')}"
                    + (f" — {golden.get('words')}" if golden.get("words")
                       else ""))
    return rows


def render_compare(cmp) -> str:
    """Сравнение словами. Сравнения нет (не с кем) — так и сказано."""
    if not cmp:
        return "сравнивать не с кем: ни золотого, ни предыдущего прогона " \
               "того же корня нет"
    return "\n".join(cmp.get("words") or [])


def render_path(path: dict) -> str:
    """Путь от корня словами: входы → родители → прогон."""
    root = (path or {}).get("root") or {}
    rows = [f"ПУТЬ ОТ КОРНЯ {rr.short(root.get('root_sha256'))} — "
            f"авиакомпания {root.get('airline') or 'не названа'}"]
    for step, n in enumerate((path or {}).get("runs") or [], start=1):
        rows.extend([f"   шаг {step}:"] + _node_words(n))
    if not ((path or {}).get("runs") or []):
        rows.append("   прогонов на пути нет")
    return "\n".join(rows)


# ------------------------------------------------------ командная строка
def main(argv: list | None = None) -> int:
    """Командная строка дерева прогонов.

    Коды возврата: 0 — сошлось; 1 — собрано, но есть отказы обхода;
    2 — отказ (нечего собирать, файла нет, подписи нет)."""
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m vmp.run_tree",
        description="Дерево прогонов: индекс, путь от корня, сравнение "
                    "ветвей, золотой прогон")
    sub = ap.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="собрать индекс дерева")
    p_index.add_argument("roots", nargs="+", metavar="корень")
    p_index.add_argument("--out", default=None,
                         help=f"куда положить индекс (умолчание — "
                              f"только показать; имя {INDEX_FILE})")
    p_index.add_argument("--depth", type=int, default=MAX_DEPTH,
                         help="глубина обхода корня")

    p_path = sub.add_parser("path", help="путь от корня к прогону")
    p_path.add_argument("run_id", metavar="номер_прогона")
    p_path.add_argument("--index", required=True, help="файл индекса")

    p_cmp = sub.add_parser("compare", help="сравнить две записи о прогоне")
    p_cmp.add_argument("a", metavar="запись_а")
    p_cmp.add_argument("b", metavar="запись_б")

    p_gold = sub.add_parser("golden", help="подтвердить золотой прогон")
    p_gold.add_argument("record", metavar="запись")
    p_gold.add_argument("--by", default=None, help="кто подтвердил")
    p_gold.add_argument("--words", default=None, help="слова подтверждения")
    p_gold.add_argument("--at", default=None, help="когда (умолчание — сейчас)")
    p_gold.add_argument("--unmark", action="store_true",
                        help="снять признак золотого прогона")

    args = ap.parse_args(argv)
    try:
        if args.command == "index":
            found = scan(args.roots, max_depth=args.depth)
            tree = index(found["records"], found["refusals"])
            print(render(tree))
            if args.out:
                print(f"ИНДЕКС ЗАПИСАН: {save_index(args.out, tree)}")
            if not found["records"]:
                print("ОТКАЗ: записей о прогонах в названных корнях нет")
                return 2
            return 1 if found["refusals"] else 0
        if args.command == "path":
            tree = load_index(args.index)
            print(render_path(path_from_root(tree, args.run_id)))
            neighbours = siblings(tree, args.run_id)
            print(f"   соседние ветви ({len(neighbours)}): "
                  + (", ".join(n.get("run_id") for n in neighbours)
                     or "нет"))
            gold = compare_with_golden(tree, args.run_id)
            print(render_compare(gold) if gold
                  else "   золотого прогона у этого корня нет")
            return 0
        if args.command == "compare":
            print(render_compare(compare(rr.load(args.a), rr.load(args.b))))
            return 0
        if args.command == "golden":
            record = rr.load(args.record)
            if args.unmark:
                unmark_golden(record)
                said = "признак золотого прогона снят"
            else:
                mark_golden(record, by=args.by or "", words=args.words or "",
                            at=args.at)
                said = f"прогон назван золотым: {args.by}"
            rr.save(args.record, record)
            print(f"{said}\n{rr.summary(record)}")
            return 0
    except (RunTreeError, rr.RunRecordError, OSError) as e:
        print(f"ОТКАЗ: {e}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
