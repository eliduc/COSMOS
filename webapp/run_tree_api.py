# -*- coding: utf-8 -*-
"""Дерево прогонов на стороне COSMOS: корни обхода, индекс файлом,
карточка прогона на дереве, сравнение ветвей и золотой прогон.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, раздел
7 «Дерево прогонов», решение владельца 6.3 (золотой прогон ставится
ТОЛЬКО подтверждением владельца после приёмки на Prod) и раздел 12
(шаг 3: 12.1 корни обхода, 12.2 ручки, 12.4 сравнение в записи).

Ядро дерева — общий модуль `vmp/run_tree.py`: обход, индекс, рёбра,
путь от корня, сравнение и подпись золотого живут ТАМ и читаются обоими
продуктами. Здесь — только то, чего ядро знать не должно: где у COSMOS
лежат записи, куда кладётся файл индекса, когда индекс перестраивается,
и кто имеет право назвать прогон золотым.

Устройство.

  * Корни обхода (12.1): `<каталог данных>/package-runs` — записи
    ЧАСТНЫХ прогонов, объявленных в COSMOS (хозяин — COSMOS), и каталог
    прогонов панели CDPM на той же машине (`CREW_CDPM_RUNS_DIR`, а без
    переменной — `<каталог данных>/cdpm-runs`, как сегодня читается
    заморозка клауз). Второй корень нужен потому, что у ПОЛНОГО прогона
    своей записи в папке нажатий нет: хозяин записи — панель, и дерево
    видит такой прогон только через её `run-record.json`.
  * Индексируются только НАСТОЯЩИЕ записи (`run-record.json`): фрагмент
    COSMOS (`run-record.cosmos.json`) и полученная копия чужой записи
    (`run-record.remote.json`) записями не являются и в дерево не идут —
    иначе один прогон встал бы на дерево дважды.
  * Индекс кладётся файлом `<каталог данных>/run-tree.json` и читается
    ИЗ ФАЙЛА: обход диска на каждый запрос карточки — цена, которой
    дерево не стоит. Перестраивается он по событию (объявление прогона,
    запись этапа, золотой прогон) и по ручке `POST /api/run-tree/rebuild`,
    не чаще раза в `CREW_RUN_TREE_REBUILD_S` секунд: нажатие идёт
    очередями, и три записи подряд не имеют права трижды обойти диск.
  * Золотой прогон: ЧАСТНЫЙ прогон COSMOS помечает сам (хозяин записи —
    он), у ПОЛНОГО хозяин записи — панель, и подтверждение пересылается
    ей ручкой `POST <record_api без /record>/golden`; ответ хозяина
    отдаётся как есть. Площадка записи не `prod` либо прогон не завершён
    — отказ словами (решение владельца 6.3: признак ставится после
    приёмки на Prod).

Правило, которому подчинён весь модуль: ДЕРЕВО НЕ ЛОМАЕТ НАЖАТИЕ.
Индекс — производное от записей; любая его беда — слова в ответе, а не
исключение в крючке нажатия и не отказ кнопки.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from urllib import error as _urlerror
from urllib import request as _urlrequest

from vmp import run_record as rr
from vmp import run_tree as rt

#: Каталог прогонов панели CDPM на этой машине (та же переменная, по
#: которой сегодня читается заморозка клауз).
CDPM_RUNS_ENV = "CREW_CDPM_RUNS_DIR"

#: Каталог прогонов панели внутри корня данных: одна площадка на оба
#: продукта (запасной корень, когда переменная не задана).
CDPM_RUNS_DIR = "cdpm-runs"

#: Как часто индекс имеет право перестраиваться по событию, секунды.
REBUILD_EVERY_ENV = "CREW_RUN_TREE_REBUILD_S"
DEFAULT_REBUILD_EVERY_S = 15.0

#: Насколько глубоко обход спускается от корня. Запись прогона лежит
#: ровно на один уровень ниже корня (`package-runs/<отпечаток>/` и
#: `<прогоны панели>/run-NNNN/`); второй уровень — запас на раскладку с
#: папкой заказчика. Глубже спускаться незачем: в папке нажатий лежат
#: снимки попыток, и обход по ним стоил бы дороже самого дерева.
SCAN_DEPTH = 2

#: Короткий срок обращения к хозяину записи: подтверждение золотого идёт
#: из ручки, и человек не должен ждать чужую площадку.
TIMEOUT_S = 5.0

#: Площадка, после приёмки на которой прогон может стать золотым
#: (решение владельца 6.3).
PROD = rr.CONTOURS[3]

#: Хвост адреса записи у хозяина (`…/api/runs/<прогон>/record`): им
#: адрес ручки золотого отличается от адреса записи (12.2).
RECORD_TAIL = "/record"

#: Кто пометил прогон золотым: своя запись либо запись хозяина.
OWNER_LOCAL = "cosmos"
OWNER_REMOTE = "хозяин записи"

# ----------------------------------------------------------- коды отказов
#: Текущего пакета постановки нет — дерева по нему не построить.
CODE_PACKAGE_MISSING = "package-missing"
#: Прогона в индексе дерева нет (запись не найдена ни в одном корне).
CODE_NOT_IN_TREE = "run-not-in-tree"
#: Сравнение позвали без двух номеров прогонов.
CODE_COMPARE_ARGS = "compare-without-runs"
#: Не сказано, КТО подтвердил золотой прогон.
CODE_GOLDEN_BY = "golden-without-by"
#: Не сказано, КАКИМИ СЛОВАМИ подтверждён золотой прогон.
CODE_GOLDEN_WORDS = "golden-without-words"
#: Подпись в теле не совпала с личностью сессии.
CODE_GOLDEN_BY_MISMATCH = "golden-by-mismatch"
#: Прогон не завершён — образцом не бывает.
CODE_GOLDEN_STATE = "golden-run-not-done"
#: Площадка записи не `prod` — приёмки на Prod не было.
CODE_GOLDEN_CONTOUR = "golden-not-prod"
#: Ручку золотого просят по машинному каналу — подтверждает человек.
CODE_GOLDEN_MACHINE = "golden-machine-channel"
#: Признак не поставлен: словарь записи отказал словами.
CODE_GOLDEN_REFUSED = "golden-refused"
#: Хозяин записи недоступен: подтверждение переслать некому.
CODE_OWNER_UNREACHABLE = "owner-unreachable"
#: Хозяин записи есть, а адреса его ручек нет.
CODE_OWNER_NO_API = "record-api-missing"
#: Ручку позвали с параметром, которого она не знает.
CODE_FILTER_UNKNOWN = "filter-unknown"
#: Значение отбора не по словарю (площадка, состояние, кто объявил,
#: золотой, дата).
CODE_FILTER_VALUE = "filter-value"


class RunTreeApiError(RuntimeError):
    """Отказ ручки дерева: код причины, слова и код ответа.

    Форма та же, что у отказа гейта (`webapp/run_gate.py`): `detail`
    строкой читают сегодняшние клиенты, рядом — код и слова перечнем."""

    def __init__(self, code: str, words, status: int = 409):
        self.code = str(code)
        self.words = [str(w) for w in (words if isinstance(words, (list, tuple))
                                       else [words])]
        self.status = int(status)
        super().__init__(self.text())

    def text(self) -> str:
        return "; ".join(self.words) or self.code

    def payload(self) -> dict:
        return {"detail": self.text(), "code": self.code, "words": self.words}


# ------------------------------------------------------------- мелочи
def _text(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _order_key(node: dict):
    """Порядок прогонов корня: момент объявления, затем номер — тот же,
    которым ядро сортирует ветви."""
    return (node.get("created_at") or "", node.get("run_id") or "")


# ------------------------------------------------------------- отбор
# Отбор прогонов — ОДНО место правды на обе стороны: ручка дерева
# (`GET /api/run-tree`) и общий экран прогонов обеих систем
# (`GET /api/runs-all`, модуль `webapp/runs_all_api.py`) читают эти же
# функции, а интерфейс только передаёт параметры. Второй разбор тех же
# слов на стороне браузера разошёлся бы с первым на первой же правке.

#: Имена параметров отбора. Параметр, которого здесь нет, — отказ
#: словами (422), а не молчаливое «показали всё»: человек обязан узнать,
#: что его отбор не применён.
FILTER_PARAMS = ("customer", "contour", "state", "declared_by", "golden",
                 "since", "until", "root", "q")

#: Кто объявил прогон — три вида, по которым человек и отбирает.
DECLARED_RUNNER, DECLARED_PANEL, DECLARED_HUMAN = "runner", "panel", "human"
DECLARED_BY_KINDS = (DECLARED_RUNNER, DECLARED_PANEL, DECLARED_HUMAN)

#: Приметы вида по слову `declaration.created_by`. Слова пишут раннер
#: наборов («раннер наборов H0») и панель CDPM («панель CDPM»); всё
#: прочее непустое — человек (в COSMOS прогон объявляет личность сессии).
#: Судим по примете, а не по точному слову: у каждого набора свой раннер,
#: и точный перечень имён устарел бы на следующем наборе.
_DECLARED_MARKS = ((DECLARED_RUNNER, ("раннер", "runner")),
                   (DECLARED_PANEL, ("панель", "panel", "cdpm")))

#: Слова «да» и «нет» для признака золотого прогона: и словом, и цифрой.
GOLDEN_YES = ("1", "да", "yes", "true")
GOLDEN_NO = ("0", "нет", "no", "false")

#: Дата отбора: только день, без часов — период человек называет днями.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def declared_by(node) -> str | None:
    """Кто объявил прогон: `runner` | `panel` | `human`; слова нет —
    пусто (выдумывать за запись, кто её объявил, нельзя)."""
    who = _text((node or {}).get("created_by"))
    if not who:
        return None
    low = who.lower()
    for kind, marks in _DECLARED_MARKS:
        if any(mark in low for mark in marks):
            return kind
    return DECLARED_HUMAN


def _refuse_value(name: str, value: str, allowed) -> None:
    raise RunTreeApiError(
        CODE_FILTER_VALUE,
        ["отбор «%s» такого значения не знает: «%s»; известные — %s"
         % (name, value, ", ".join(str(a) for a in allowed))], status=422)


def check_params(params, extra=()) -> None:
    """Отказ словами (422) на параметр, которого ручка не знает.

    Молчаливо пропускать незнакомый параметр нельзя: человек увидел бы
    полный список там, где просил отбор, и решил, что отбор пуст."""
    known = set(FILTER_PARAMS) | set(extra)
    unknown = sorted({str(k) for k in (params or ()) if str(k) not in known})
    if unknown:
        raise RunTreeApiError(
            CODE_FILTER_UNKNOWN,
            ["ручка таких параметров запроса не знает: %s; известные — %s"
             % (", ".join(unknown), ", ".join(sorted(known)))], status=422)


def filters_from(params) -> dict:
    """Разбор отбора из параметров запроса; негодное значение — отказ
    словами (422). Пустое значение параметра — отбора по нему нет: так
    интерфейс шлёт «все» одним и тем же адресом."""
    params = params or {}
    out: dict = {}
    for name in FILTER_PARAMS:
        raw = _text(params.get(name))
        if raw is None:
            continue
        if name == "contour":
            if raw not in rr.CONTOURS:
                _refuse_value(name, raw, rr.CONTOURS)
        elif name == "state":
            if raw not in rr.STATES:
                _refuse_value(name, raw, rr.STATES)
        elif name == "declared_by":
            if raw not in DECLARED_BY_KINDS:
                _refuse_value(name, raw, DECLARED_BY_KINDS)
        elif name == "golden":
            low = raw.lower()
            if low in GOLDEN_YES:
                out[name] = True
            elif low in GOLDEN_NO:
                out[name] = False
            else:
                _refuse_value(name, raw, GOLDEN_YES + GOLDEN_NO)
            continue
        elif name in ("since", "until"):
            if not _DATE_RE.match(raw):
                _refuse_value(name, raw, ["день вида ГГГГ-ММ-ДД"])
        out[name] = raw
    return out


def row_of_node(node: dict) -> dict:
    """Поля головы записи, по которым идёт отбор, — из узла дерева.

    Тот же вид строки собирает и общий экран прогонов обеих систем: у
    отбора одна форма строки, и правило не переписывается дважды."""
    n = node if isinstance(node, dict) else {}
    return {"customer": n.get("airline"),
            "contour": n.get("contour"),
            "state": n.get("state"),
            "declared_by": declared_by(n),
            "golden": bool((n.get("golden") or {}).get("confirmed")),
            "created_at": n.get("created_at"),
            "root": n.get("root_sha256"),
            "search": [n.get("run_id"), n.get("source_run"),
                       n.get("parent_run")]}


def matches(row: dict, flt: dict | None) -> bool:
    """Подходит ли строка под отбор. Пустой отбор берёт всё."""
    if not flt:
        return True
    row = row if isinstance(row, dict) else {}
    customer = _text(flt.get("customer"))
    if customer is not None and \
            str(row.get("customer") or "").strip().lower() != customer.lower():
        return False
    for name in ("contour", "state", "declared_by"):
        want = _text(flt.get(name))
        if want is not None and row.get(name) != want:
            return False
    if flt.get("golden") is not None and \
            bool(row.get("golden")) is not bool(flt["golden"]):
        return False
    day = str(row.get("created_at") or "")[:10]
    since, until = _text(flt.get("since")), _text(flt.get("until"))
    if since is not None and (not day or day < since):
        return False
    if until is not None and (not day or day > until):
        return False
    root = _text(flt.get("root"))
    if root is not None and not str(row.get("root") or "").startswith(root):
        return False
    q = _text(flt.get("q"))
    if q is not None:
        hay = " ".join(str(v) for v in (row.get("search") or []) if v).lower()
        if q.lower() not in hay:
            return False
    return True


def filter_tree(tree_doc: dict, flt: dict | None) -> dict:
    """Индекс, отобранный по полям головы записи.

    Форма ответа та же (`cosmos-run-tree/v1`): отбор только УБИРАЕТ
    ветви, а корень без единой оставшейся ветви уходит целиком —
    показывать корень, у которого нечего показать, значило бы врать про
    отбор. Отказы обхода остаются как есть: они говорят не о прогонах, а
    о том, чего обход НЕ прочитал, и отбором не скрываются."""
    if not flt or not isinstance(tree_doc, dict):
        return tree_doc
    roots = []
    for root in tree_doc.get("roots") or []:
        runs = [n for n in (root.get("runs") or [])
                if matches(row_of_node(n), flt)]
        if runs:
            roots.append(dict(root, runs=runs))
    out = dict(tree_doc)
    out["roots"] = roots
    out["orphans"] = [n for n in tree_doc.get("orphans") or []
                      if matches(row_of_node(n), flt)]
    return out


# ------------------------------------------------------------- корни
def data_root() -> Path:
    import _data_paths
    return Path(_data_paths.data_root())


def roots() -> list:
    """Корни обхода COSMOS со словами, ЧТО в каждом лежит (12.1).

    Возвращает перечень `{"path", "kind", "words", "present"}`. Корня
    может не быть на диске (панель CDPM на этой машине не стоит) — это
    не беда, а состояние: обход по нему не идёт, а читатель видит, где
    дерево искали."""
    from . import cosmos_runs as cr
    # Раскладку папок нажатий знает хранилище прогонов, а не дерево:
    # второе её описание разошлось бы с первым на первой же правке.
    out = [{"path": str(cr.package_runs_root()),
            "kind": cr.PACKAGE_RUNS_DIR,
            "words": "записи частных прогонов COSMOS (хозяин — COSMOS)"}]
    env = _text(os.environ.get(CDPM_RUNS_ENV))
    if env:
        out.append({"path": str(Path(env)), "kind": "cdpm-runs",
                    "words": "каталог прогонов панели CDPM (%s)"
                             % CDPM_RUNS_ENV})
    else:
        out.append({"path": str(data_root() / CDPM_RUNS_DIR),
                    "kind": "cdpm-runs",
                    "words": "каталог прогонов панели CDPM в корне данных"})
    for row in out:
        row["present"] = Path(row["path"]).is_dir()
    return out


def scan_roots() -> list:
    """Корни, по которым обход действительно идёт: только те, что есть
    на диске. Несуществующий корень в обход не отдаётся — иначе каждый
    индекс нёс бы отказ «корня обхода нет» там, где панель просто не
    стоит на этой машине."""
    return [Path(r["path"]) for r in roots() if r["present"]]


def index_path() -> Path:
    """Файл индекса дерева: `<каталог данных>/run-tree.json`."""
    return data_root() / rt.INDEX_FILE


def rebuild_every_s() -> float:
    """Как часто индекс имеет право перестраиваться по событию."""
    raw = _text(os.environ.get(REBUILD_EVERY_ENV))
    if raw is None:
        return DEFAULT_REBUILD_EVERY_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_REBUILD_EVERY_S
    return value if value >= 0 else DEFAULT_REBUILD_EVERY_S


# ------------------------------------------------------------- индекс
_LOCK = threading.RLock()
#: Состояние перестройки: когда собирали в последний раз (часы хода, не
#: календарь — их не переводят) и какое событие ждёт своей перестройки.
_STATE: dict = {"at": None, "pending": None, "reason": None}


def forget() -> None:
    """Забыть отметку последней перестройки (стенды: у каждого теста
    свой каталог данных, и защита от частой перестройки не имеет права
    протянуться из соседнего)."""
    with _LOCK:
        _STATE.update(at=None, pending=None, reason=None)


def _due() -> bool:
    """Пора ли перестраивать: срок с прошлой перестройки вышел."""
    at = _STATE.get("at")
    return at is None or (time.monotonic() - at) >= rebuild_every_s()


def rebuild(reason: str | None = None) -> dict:
    """Обойти корни, собрать индекс и положить его файлом.

    Файл индекса — производное от записей, а не хранилище: не легло
    (каталог только на чтение) — дерево всё равно отдаётся, а следующее
    чтение соберёт его обходом заново. Терять из-за снимка сами записи
    было бы нечем оправдать."""
    tree_doc = rt.build(scan_roots(), max_depth=SCAN_DEPTH)
    try:
        rt.save_index(index_path(), tree_doc)
    except OSError:
        pass
    with _LOCK:
        _STATE.update(at=time.monotonic(), pending=None, reason=reason)
    return tree_doc


def tree(rebuild_now: bool = False) -> dict:
    """Индекс дерева: из файла, а при нужде — обходом.

    Обход идёт в трёх случаях: попросили прямо (`?rebuild=1`), файла
    индекса ещё нет, либо событие ждало своей перестройки и срок вышел.
    В остальных — читается файл: обход диска на каждый запрос карточки
    не делается (12.1)."""
    if rebuild_now:
        return rebuild("по запросу")
    with _LOCK:
        pending, due = _STATE.get("pending"), _due()
    if pending and due:
        return rebuild(pending)
    try:
        return rt.load_index(index_path())
    except rt.RunTreeError:
        return rebuild("индекса ещё нет")


def _refresh(reason: str) -> dict:
    """Перестроить индекс, если срок защиты вышел; иначе — прежний
    снимок. Так запрос про прогон, которого на дереве нет, не гоняет
    обход диска в цикле: срок один и для событий, и для промахов."""
    with _LOCK:
        due = _due()
    return rebuild(reason) if due else tree()


def note_change(reason: str | None = None) -> dict:
    """Событие, от которого дерево могло измениться: объявление прогона,
    запись этапа, золотой прогон.

    Перестройка идёт не чаще раза в `CREW_RUN_TREE_REBUILD_S` секунд;
    событие, пришедшее раньше срока, запоминается и перестроит индекс
    при первом же чтении после срока. Ни одна беда обхода наружу не
    выходит: крючок стоит в пути нажатия."""
    try:
        with _LOCK:
            if not _due():
                _STATE["pending"] = reason or _STATE.get("pending") or "событие"
                return {"rebuilt": False, "pending": _STATE["pending"],
                        "words": []}
        rebuild(reason)
        return {"rebuilt": True, "pending": None, "words": []}
    except Exception as e:                                 # noqa: BLE001
        return {"rebuilt": False, "pending": reason,
                "words": ["индекс дерева не перестроен: %s" % str(e)[:200]]}


# --------------------------------------------------- свежий узел поверх
def _with_fresh(tree_doc: dict, record) -> dict:
    """Свежий узел прогона поверх индекса.

    Запись, которую только что закрыли, в файле индекса ещё прежняя (и
    обходить диск ради одного узла незачем). Индекс не переписывается —
    правится КОПИЯ перечней: чужие узлы остаются теми же объектами."""
    if not isinstance(record, dict) or not isinstance(tree_doc, dict):
        return tree_doc
    try:
        fresh = rt.node(record)
    except rt.RunTreeError:
        return tree_doc
    run_id = fresh.get("run_id")
    if not run_id:
        return tree_doc
    out = dict(tree_doc)
    out["roots"] = [dict(r, runs=list(r.get("runs") or []))
                    for r in tree_doc.get("roots") or []]
    out["orphans"] = list(tree_doc.get("orphans") or [])
    for group in [r["runs"] for r in out["roots"]] + [out["orphans"]]:
        for i, n in enumerate(group):
            if n.get("run_id") == run_id:
                # Ребро и путь к файлу посчитал индекс — у одинокого
                # узла их взять неоткуда, и терять их нельзя.
                fresh["edge"] = n.get("edge") or fresh["edge"]
                fresh["path"] = n.get("path")
                group[i] = fresh
                return out
    for root in out["roots"]:
        if root.get("root_sha256") and \
                root["root_sha256"] == fresh.get("root_sha256"):
            root["runs"].append(fresh)
            root["runs"].sort(key=_order_key)
            return out
    out["orphans"].append(fresh)
    return out


# ------------------------------------------------------------ карточка
def golden_action(node: dict | None, head: dict | None = None) -> dict:
    """Можно ли назвать этот прогон золотым — и если нет, то почему.

    Правило одно на обе системы (12.2): кнопка «Золотой» показывается
    только у прогонов площадки `prod` в состоянии «завершён». Показ
    правило не пересказывает — он берёт готовый ответ."""
    n = node if isinstance(node, dict) else {}
    confirmed = bool((n.get("golden") or {}).get("confirmed"))
    code, words = None, []
    if not n:
        code = CODE_NOT_IN_TREE
        words = ["прогона на дереве нет — подтверждать нечего"]
    elif n.get("contour") != PROD:
        code = CODE_GOLDEN_CONTOUR
        words = ["золотым бывает прогон, прошедший приёмку на Prod: "
                 "площадка записи — «%s», а не «%s» (решение владельца "
                 "6.3 от 10.09.2026)" % (n.get("contour") or "не названа",
                                         PROD)]
    elif n.get("state") != rr.STATE_DONE:
        code = CODE_GOLDEN_STATE
        words = ["золотым может быть только завершённый прогон: сейчас "
                 "состояние «%s»" % (n.get("state") or "не названо")]
    return {"allowed": code is None, "code": code, "words": words,
            "confirmed": confirmed,
            "owner": (head or {}).get("owner"),
            "by": (n.get("golden") or {}).get("by"),
            "at": (n.get("golden") or {}).get("at"),
            "words_of_owner": (n.get("golden") or {}).get("words")}


def counts(tree_doc: dict | None = None) -> dict:
    """Сколько в индексе корней, прогонов, прогонов без корня и отказов
    обхода — сводка для карточки и для слов о перестройке."""
    t = tree_doc if isinstance(tree_doc, dict) else tree()
    return {"roots": len(t.get("roots") or []),
            "runs": len(rt.nodes(t)),
            "orphans": len(t.get("orphans") or []),
            "refusals": len(t.get("refusals") or [])}


def pending_golden(tree_doc: dict | None = None) -> list:
    """Прогоны, ждущие слова владельца: площадка `prod`, состояние
    «завершён», признака ещё нет (12.2 — «прогоны <номера> прошли
    приёмку на Prod, подтвердить золотые»).

    Сам запрос владельцу (строка в ведомость и уведомление) шлёт тот,
    кто закрывает приёмку; здесь — перечень, из которого он строится."""
    t = tree_doc if isinstance(tree_doc, dict) else tree()
    out = []
    for n in rt.nodes(t):
        if n.get("contour") == PROD and n.get("state") == rr.STATE_DONE \
                and not (n.get("golden") or {}).get("confirmed"):
            out.append({"run_id": n.get("run_id"),
                        "created_at": n.get("created_at"),
                        "purpose": n.get("purpose"),
                        "airline": n.get("airline"),
                        "root_sha256": n.get("root_sha256"),
                        "verdicts": n.get("verdicts") or {}})
    out.sort(key=_order_key)
    return out


def view(run_id: str, record: dict | None = None,
         head: dict | None = None) -> dict:
    """Тело ручки дерева по НОМЕРУ ЗАПИСИ прогона (12.2).

    `{node, path, siblings, golden, compare_golden, compare_previous,
    expectations}` — путь от корня, соседние ветви, золотой прогон
    корня и сравнение с ним (по умолчанию) и с предыдущим прогоном.
    `expectations` — сравнение с ожиданиями набора из записи
    (`results.comparison.expectations`), его пишет раннер при закрытии."""
    run_id = str(run_id or "")
    t = _with_fresh(tree(), record)
    node = rt.find_node(t, run_id)
    if node is None:
        # Записи могло не быть в файле индекса: её положили только что.
        t = _with_fresh(_refresh("прогона в индексе не было"), record)
        node = rt.find_node(t, run_id)
    if node is None:
        raise RunTreeApiError(
            CODE_NOT_IN_TREE,
            ["прогона %s на дереве нет: индекс собран из %d записей по "
             "корням %s" % (run_id, len(rt.nodes(t)),
                            ", ".join(str(p) for p in scan_roots()) or "—")],
            status=404)
    root = rt.root_of(t, run_id) or {}
    gold = rt.golden_node(t, root.get("root_sha256")) if root else None
    comparison = ((record or {}).get("results") or {}).get("comparison") or {}
    return {"run_id": run_id,
            "built_at": t.get("built_at"),
            "node": node,
            "path": rt.path_from_root(t, run_id),
            "siblings": rt.siblings(t, run_id),
            "golden": gold,
            "compare_golden": rt.compare_with_golden(t, run_id),
            "compare_previous": rt.compare_with_previous(t, run_id),
            "expectations": comparison.get("expectations") or None,
            "golden_action": golden_action(node, head)}


def compare(a: str, b: str) -> dict:
    """Тело ручки `GET /api/run-tree/compare`: два прогона рядом."""
    a, b = _text(a), _text(b)
    if not a or not b:
        raise RunTreeApiError(CODE_COMPARE_ARGS,
                              ["сравнение зовут двумя номерами прогонов: "
                               "a=<прогон> и b=<прогон>"], status=400)
    t = tree()
    missing = [x for x in (a, b) if rt.find_node(t, x) is None]
    if missing:
        t = _refresh("прогонов сравнения в индексе не было")
        missing = [x for x in (a, b) if rt.find_node(t, x) is None]
    if missing:
        raise RunTreeApiError(
            CODE_NOT_IN_TREE,
            ["на дереве нет прогона %s" % x for x in missing], status=404)
    return rt.compare(rt.find_node(t, a), rt.find_node(t, b))


# ------------------------------------------------- сравнение в записи
def comparison_for(record: dict, tree_doc: dict | None = None) -> dict:
    """Сравнение с предыдущим прогоном и с золотым — для ИТОГОВ записи
    (12.4): `{"previous_run": {run_id, words[]}, "golden": {…}}`.

    Сравнивать не с чем (прогон первый на корне, золотого нет) — пусто:
    выдумывать сравнение с самим собой хуже честной пустоты."""
    out: dict = {"previous_run": {}, "golden": {}}
    run_id = _text(((record or {}).get("declaration") or {}).get("run_id"))
    if not run_id:
        return out
    try:
        t = _with_fresh(tree_doc if isinstance(tree_doc, dict) else tree(),
                        record)
        previous = rt.compare_with_previous(t, run_id)
        golden = rt.compare_with_golden(t, run_id)
    except (rt.RunTreeError, rr.RunRecordError, OSError):
        return out
    if previous:
        out["previous_run"] = {"run_id": previous.get("a"),
                               "words": list(previous.get("words") or [])}
    if golden:
        out["golden"] = {"run_id": golden.get("a"),
                         "words": list(golden.get("words") or [])}
    return out


# --------------------------------------------------------- текущий пакет
def _current() -> tuple:
    """(голова записи, папка нажатий, запись) текущего пакета.

    Хозяин записи чужой — запись читается у него (местная копия, когда
    он недоступен); хозяин COSMOS — своя запись из папки нажатий."""
    from . import package_runs as pr
    from . import run_gate
    from . import run_journal
    res = pr.resolve_current()
    if res is None:
        raise RunTreeApiError(CODE_PACKAGE_MISSING,
                              ["текущего пакета постановки нет — дерева по "
                               "нему не построить"])
    _cur, _sha, pkg_path, wd, _run = res
    head = run_gate.head_for(wd, pr._package_manifest(pkg_path))
    if head is None:
        raise RunTreeApiError(run_gate.CODE_NOT_DECLARED,
                              run_gate.declare_refusal_words())
    return head, wd, run_journal.record_view(wd, head).get("record")


def current_view() -> dict:
    """Тело ручки `GET /api/run/current/tree`: дерево по записи ТЕКУЩЕГО
    пакета постановки."""
    head, _wd, record = _current()
    run_id = _text(head.get("record_id"))
    if not run_id:
        raise RunTreeApiError(CODE_NOT_IN_TREE,
                              ["у текущего пакета нет номера записи"],
                              status=404)
    return view(run_id, record, head)


# ------------------------------------------------------- прогон COSMOS
def _context(run: str) -> tuple:
    """(голова записи, папка нажатий) по номеру прогона COSMOS.

    Собирается без обращения к хозяину записи: подтверждение золотого —
    действие, и лишний поход в сеть ради головы ему не нужен (карточка
    ходит к хозяину сама, ручкой `record`)."""
    from . import cosmos_runs as cr
    from . import package_runs as pr
    from . import run_gate
    doc = cr.read_run(cr.check_run_id(run))
    pkg = cr._current_pkg(doc)
    if pkg is None:
        raise RunTreeApiError(CODE_PACKAGE_MISSING,
                              ["у прогона %s нет текущего пакета постановки "
                               "— записи о прогоне здесь нет" % run],
                              status=404)
    wd = cr.workdir_of_package(pkg)
    path = cr.package_path_of(pkg)
    man = pr._package_manifest(path) if path is not None else {}
    if not man:
        # Байты пакета утеряны — голова собирается из записи прогона
        # (её поля пишет приём канала), как у ручки записи.
        man = {"record_id": pkg.get("record_id"),
               "declaration_sha256": pkg.get("declaration_sha256"),
               "record_api": pkg.get("record_api"),
               "source_run": pkg.get("source_run"),
               "customer": pkg.get("customer")}
    head = run_gate.head_for(wd, man)
    if head is None:
        raise RunTreeApiError(run_gate.CODE_NOT_DECLARED,
                              run_gate.declare_refusal_words())
    return head, wd


def view_of_run(run: str) -> dict:
    """Тело ручки `GET /api/cosmos-runs/<run>/tree`: дерево по записи
    названного прогона COSMOS."""
    from . import run_journal
    head, wd = _context(run)
    record = run_journal.record_view(wd, head).get("record")
    run_id = _text(head.get("record_id"))
    if not run_id:
        raise RunTreeApiError(CODE_NOT_IN_TREE,
                              ["у прогона %s нет номера записи" % run],
                              status=404)
    got = view(run_id, record, head)
    got["run"] = run
    return got


# ------------------------------------------------------------- золотой
def golden_url(record_api: str, mark: bool = True) -> str:
    """Адрес ручки золотого у хозяина записи: `record_api` без хвоста
    `/record` плюс `/golden` (12.2 — `POST /api/runs/<run>/golden`)."""
    base = str(record_api or "").rstrip("/")
    if base.endswith(RECORD_TAIL):
        base = base[: -len(RECORD_TAIL)]
    return base + "/golden" + ("" if mark else "/unmark")


def _forward(head: dict, *, by: str, words: str | None, mark: bool,
             timeout: float = TIMEOUT_S) -> dict:
    """Переслать подтверждение хозяину записи (панель либо раннер).

    Ответ хозяина отдаётся КАК ЕСТЬ: судит признак тот, у кого лежит
    запись, и пересказывать его отказ своими словами значило бы завести
    второе правило золотого прогона."""
    api = _text(head.get("record_api"))
    if not api:
        raise RunTreeApiError(
            CODE_OWNER_NO_API,
            ["хозяин записи — %s, а адреса его ручек у пакета нет: "
             "подтверждать золотой прогон некому (код %s)"
             % (head.get("owner"), head.get("record_api_code")
                or CODE_OWNER_NO_API)])
    url = golden_url(api, mark)
    body = {"by": by, "words": words} if mark else {"by": by}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = _urlrequest.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            raw = resp.read()
    except _urlerror.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw = e.read()
        except Exception:                                  # noqa: BLE001
            raw = b""
    except Exception as e:                                 # noqa: BLE001
        raise RunTreeApiError(
            CODE_OWNER_UNREACHABLE,
            ["хозяин записи недоступен (%s): %s" % (url, str(e)[:200])],
            status=502) from None
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"answer": payload}
    return {"ok": status == 200, "status": status or 502, "owner": OWNER_REMOTE,
            "url": url, "payload": payload}


def golden(run: str, *, by, words=None, confirm_by=None, mark: bool = True,
           timeout: float = TIMEOUT_S) -> dict:
    """Назвать прогон золотым либо снять признак (12.2).

    `by` — почта личности сессии: подтверждает ЧЕЛОВЕК (решение
    владельца 6.3), и машинный канал этой ручки не получает — это судит
    ручка приложения. `confirm_by` — та же почта в теле запроса:
    поле допускается только как подтверждение и обязано совпасть.

    Хозяин записи COSMOS (частный прогон) — признак ставится на своей
    записи и индекс перестраивается; хозяин панель либо раннер (полный
    прогон) — подтверждение пересылается ему, ответ отдаётся как есть."""
    by = _text(by)
    words_text = _text(words)
    if not by:
        raise RunTreeApiError(
            CODE_GOLDEN_BY,
            ["золотым прогон не назван: не сказано, КТО подтвердил — "
             "признак ставится только словом владельца после приёмки на "
             "Prod (решение владельца 6.3 от 10.09.2026)"], status=422)
    if confirm_by is not None and _text(confirm_by) != by:
        raise RunTreeApiError(
            CODE_GOLDEN_BY_MISMATCH,
            ["подпись в теле запроса «%s» не совпала с личностью сессии "
             "«%s»: подтверждает тот, кто вошёл" % (_text(confirm_by), by)],
            status=422)
    if mark and not words_text:
        raise RunTreeApiError(
            CODE_GOLDEN_WORDS,
            ["золотым прогон не назван: не сказано, КАКИМИ СЛОВАМИ "
             "владелец подтвердил — подпись без слов не подпись"], status=422)
    from . import run_gate
    head, wd = _context(run)
    if head.get("owner") != run_gate.OWNER_COSMOS:
        return _forward(head, by=by, words=words_text, mark=mark,
                        timeout=timeout)
    try:
        record = rr.load(wd)
    except rr.RunRecordError as e:
        raise RunTreeApiError(CODE_NOT_IN_TREE, [str(e)[:300]],
                              status=404) from None
    if mark:
        contour = _text(((record.get("declaration") or {}).get("contour")
                         or {}).get("name"))
        state = _text((record.get("results") or {}).get("state"))
        if contour != PROD:
            raise RunTreeApiError(
                CODE_GOLDEN_CONTOUR,
                ["золотым бывает прогон, прошедший приёмку на Prod: "
                 "площадка записи — «%s», а не «%s» (решение владельца 6.3 "
                 "от 10.09.2026)" % (contour or "не названа", PROD)])
        if state != rr.STATE_DONE:
            raise RunTreeApiError(
                CODE_GOLDEN_STATE,
                ["золотым может быть только завершённый прогон: сейчас "
                 "состояние «%s» — образцом незаконченный прогон не бывает"
                 % (state or "не названо")])
    try:
        if mark:
            rt.mark_golden(record, by=by, words=words_text)
        else:
            rt.unmark_golden(record)
    except rr.RunRecordError as e:
        raise RunTreeApiError(CODE_GOLDEN_REFUSED, [str(e)[:400]]) from None
    try:
        rr.save(wd, record)
    except OSError as e:
        raise RunTreeApiError("record-not-written",
                              ["запись о прогоне не сохранена: %s"
                               % str(e)[:200]], status=503) from None
    # Признак золотого меняет дерево: с ним сравниваются новые ветви —
    # индекс перестраивается сразу, срока он тут не ждёт.
    rebuild("золотой прогон")
    return {"ok": True, "owner": OWNER_LOCAL, "status": 200, "run": run,
            "record": record,
            "golden": (record.get("results") or {}).get("golden")}
