# -*- coding: utf-8 -*-
"""Дерево прогонов ПАНЕЛИ: индекс по каталогу прогонов, ответы ручек и
золотой прогон (шаг 3 проекта «Запись о прогоне», раздел 12; половина
CDPM).

Ядро дерева — ОДНО на обе системы: `vmp/run_tree.py` (обход перечня
корней, индекс `cosmos-run-tree/v1`, рёбра, путь от корня, сравнение,
подпись золотого). Этот модуль ядра не повторяет: он знает лишь то,
чего ядру знать не положено, — ГДЕ у панели лежат прогоны, КОГДА индекс
перестраивается и ЧЕМ панель подписывает золотой прогон.

Корень обхода — каталог прогонов панели (`webui.runs_root()`,
переменная `CDPM_RUNS_DIR`). Архив прогонов (`cdpm-runs-archive`) в
обход НЕ входит: узел индекса — закрытый словарь схемы
`cosmos-run-tree/v1`, пометить архивный прогон в нём нечем, а дерево,
молча воскрешающее убранные из списка прогоны, обманывало бы читателя.
Понадобится архив — сперва слово «архивный» в ядре и схеме, потом
второй корень здесь.

Индекс кладётся ФАЙЛОМ `run-tree.json` в корень прогонов и
перестраивается ПО СОБЫТИЮ: закрытие записи прогона (состояние
«завершён»), приём чужих записей хранителем (`record/append`),
подтверждение и снятие золотого, ручка `POST /api/run-tree/rebuild`.
Чтение — из файла: обход диска на каждый запрос не делается (12.1).
Отказ перестройки действие панели не роняет — он печатается, как отказ
записи о прогоне, а индекс догонит следующим событием.

Носитель золотого признака. Голова записи после старта неизменна, а
`run-record.json` панель пересобирает после каждого действия
(`run_record.refresh`) — значит, признак нельзя дописать в файл записи:
следующее же действие его сотрёт. Поэтому подтверждение владельца
ложится строкой в журнал хранителя `journal/record-appends.jsonl`
(`{received_at, system: "cdpm", results: {golden: {...}}}`) — тем же
путём, каким чужая система дописывает свои итоги, — а `run_record.build`
и `run_record.stitch` кладут её в `results.golden`. Журнал приписок
только растёт: снятие признака — не стирание строки, а новая строка с
`confirmed: false`; кто и когда подтверждал, видно навсегда.

Известная граница: индекс собирается по ФАЙЛАМ `run-record.json`, а при
чужой голове (переходное правило «два летописца», 11.3) панель файл не
переписывает — признак и приписки видны в ручке записи (сшивка), но не
в узле индекса. Пока раннер не объявляет прогон через панель, золотым
называют прогоны панели; когда объявит — файл станет панельным, и
граница исчезнет сама."""
from __future__ import annotations

from pathlib import Path

from cdpm import run_record as rec_mod
from vmp import run_record as vrr
from vmp import run_tree as rt

#: Имя файла индекса — то же, что у COSMOS (ядро).
INDEX_FILE = rt.INDEX_FILE

#: Система-летописец панели в журнале приписок хранителя.
SYSTEM = rec_mod.PRODUCER

#: Площадка, на которой прогон может стать золотым (решение владельца
#: 6.3 от 10.09.2026: признак ставится только после приёмки на Prod).
GOLDEN_CONTOUR = "prod"

#: Поводы отказать в золотом признаке: код → слова. Код переводится в
#: номер отказа ручкой (`cdpm/webui.py`) — модуль про HTTP не знает.
#: Коды поводов — ОДНИ у обеих систем (договор с COSMOS 10.09.2026 23:1x):
#: 422 — чего не сказал человек (без «кто», без слов, снимает не тот),
#: 409 — чего не позволяют площадка и прогон.
GOLDEN_NO_SIGNER = "golden-without-by"
GOLDEN_NO_WORDS = "golden-without-words"
GOLDEN_NOT_DONE = "golden-run-not-done"
GOLDEN_NOT_PROD = "golden-not-prod"
GOLDEN_NOT_MARKED = "golden-refused"
GOLDEN_OTHER_SIGNER = "golden-by-mismatch"
GOLDEN_HUMAN_CODES = (GOLDEN_NO_SIGNER, GOLDEN_NO_WORDS, GOLDEN_OTHER_SIGNER)


# ----------------------------------------------------------- индекс
def roots(runs_root) -> list[Path]:
    """Корни обхода панели: каталог прогонов. Перечнем — потому что
    перечень принимает ядро, и второй корень (архив, чужая машина)
    добавится сюда, а не переписыванием обхода."""
    return [Path(runs_root)]


def index_path(runs_root) -> Path:
    """Файл индекса дерева: `<каталог прогонов>/run-tree.json`."""
    return Path(runs_root) / INDEX_FILE


def build_index(runs_root) -> dict:
    """Обойти каталог прогонов и положить индекс файлом. Отказы обхода
    (папка не читается, запись не по словарю, номер встретился дважды)
    едут В ИНДЕКСЕ перечнем `refusals`, а не исключением наружу: одна
    испорченная папка не имеет права скрыть дерево целиком."""
    tree = rt.build(roots(runs_root))
    rt.save_index(index_path(runs_root), tree)
    return tree


def load_or_build(runs_root, rebuild: bool = False, save: bool = True) -> dict:
    """Индекс дерева: из файла, а нет файла (или просят заново) — обходом.

    `rebuild` — перестроить, не читая файла (ручка `?rebuild=1`);
    `save` — класть перестроенный индекс файлом (сборка «на посмотреть»
    файла не трогает)."""
    if not rebuild:
        try:
            return rt.load_index(index_path(runs_root))
        except rt.RunTreeError:
            pass                       # файла нет либо он не индекс — обойдём
    if save:
        return build_index(runs_root)
    return rt.build(roots(runs_root))


def refresh_index(runs_root, why: str = "") -> dict | None:
    """Перестроить индекс по событию. Отказ действие панели НЕ роняет:
    он печатается словами (как отказ записи о прогоне), а индекс
    догонит следующим событием либо ручкой перестройки."""
    try:
        return build_index(runs_root)
    except Exception as e:                                # noqa: BLE001
        print(f"CDPM: индекс дерева прогонов не перестроен "
              f"({why or 'событие не названо'}: {str(e)[:200]})", flush=True)
        return None


# ------------------------------------------------------------- узлы
def _fresh_node(record: dict, run_dir=None) -> dict:
    path = str(Path(run_dir) / rec_mod.RECORD_FILE) if run_dir else None
    return rt.node(record, path)


def _with_record(tree: dict, record: dict, run_dir=None) -> dict:
    """Индекс со СВЕЖИМ узлом этого прогона: запись в руках новее той,
    что лежит в файле (её же и пишут сейчас). Ребро остаётся прежним —
    оно судится по всему дереву, а не по одной записи."""
    fresh = _fresh_node(record, run_dir)
    if not fresh.get("run_id"):
        return tree
    old = rt.find_node(tree, fresh["run_id"])
    if old is not None:
        edge = old.get("edge")
        old.clear()
        old.update(fresh)
        old["edge"] = edge
        return tree
    # Прогона в индексе ещё нет (запись только что родилась и файл её
    # не видел): ставим узел на его корень, а корня нет — в «без корня».
    for root in tree.get("roots") or []:
        if root.get("root_sha256") and \
                root["root_sha256"] == fresh.get("root_sha256"):
            root.setdefault("runs", []).append(fresh)
            root["runs"].sort(key=lambda n: (n.get("created_at") or "",
                                             n.get("run_id") or ""))
            return tree
    tree.setdefault("orphans", []).append(fresh)
    return tree


def record_id_of_run(runs_root, name: str, record: dict | None = None) -> str:
    """Номер ЗАПИСИ по имени прогона панели (живая находка 10.09.2026
    23:5x, run-0147 круга s5): узлы дерева ключуются номером записи —
    у прогонов, объявленных раннером, это его номер
    (20260910-234806-9c6f40ad), а не имя папки run-0147; у прогонов
    человека номер = имени папки. Запись в руках — из неё; иначе — из
    declaration.json папки; нет ни того, ни другого — имя как есть."""
    if isinstance(record, dict):
        d = record.get("declaration")
        if isinstance(d, dict) and d.get("run_id"):
            return str(d["run_id"])
    run_dir = Path(runs_root) / str(name)
    if run_dir.is_dir():
        head = rec_mod.read_declaration(run_dir)
        if head and (head.get("declaration") or {}).get("run_id"):
            return str(head["declaration"]["run_id"])
        on_disk = rec_mod.on_disk(run_dir)
        if on_disk:
            return str(on_disk["declaration"]["run_id"])
    return str(name)


def tree_of(runs_root, run_id: str, record: dict | None = None) -> dict:
    """Ответ ручки «дерево прогона» (12.2): `{node, path, siblings,
    golden, compare_golden, compare_previous, expectations}`.

    Индекс читается из файла; прогона в нём нет (индекс отстал —
    прогон родился между событиями) — один обход диска и вторая
    попытка, а уж потом отказ словами."""
    run_id = record_id_of_run(runs_root, run_id, record)
    tree = load_or_build(runs_root)
    if rt.find_node(tree, run_id) is None:
        tree = load_or_build(runs_root, rebuild=True)
    node = rt.find_node(tree, run_id)
    if node is None:
        raise rt.RunTreeError(
            f"прогона {run_id!r} в дереве нет: индекс собран из "
            f"{len(rt.nodes(tree))} записей — записи о прогоне ещё нет "
            f"либо она не по словарю (см. отказы индекса)")
    expectations = None
    if isinstance(record, dict):
        comparison = ((record.get("results") or {}).get("comparison")
                      if isinstance(record.get("results"), dict) else None)
        got = (comparison or {}).get("expectations")
        expectations = got if got else None
    return {
        # Форма ответа — договор с COSMOS 10.09.2026 23:1x (одна у обеих систем).
        "run_id": node.get("run_id"),
        "built_at": tree.get("built_at"),
        "node": node,
        "path": rt.path_from_root(tree, run_id),
        # Соседние ветви несут своё ребро с `differs` — «чем отличается»
        # у карточки берётся оттуда, второго перечня не заводится.
        "siblings": rt.siblings(tree, run_id),
        "golden": rt.golden_node(tree, node.get("root_sha256")),
        "compare_golden": rt.compare_with_golden(tree, run_id),
        "compare_previous": rt.compare_with_previous(tree, run_id),
        "expectations": expectations,
        "golden_action": golden_action(record, contour=rec_mod._contour().get("name")),
    }


def golden_action(record: dict | None, *, contour: str | None) -> dict:
    """Правило кнопки «Золотой» считает СЕРВЕР (договор с COSMOS 23:1x):
    `{allowed, code, words, confirmed, owner, by, at, words_of_owner}` —
    можно ли сейчас подтвердить прогон золотым (площадка, состояние), и
    что уже подтверждено. Подпись и слова человека сюда не входят — их
    судит сама ручка при нажатии."""
    rec = record if isinstance(record, dict) else {}
    results = rec.get("results") if isinstance(rec.get("results"), dict) else {}
    golden = results.get("golden") if isinstance(results.get("golden"), dict) else {}
    decl = rec.get("declaration") if isinstance(rec.get("declaration"), dict) else {}
    problems = [p for p in golden_problems(rec, by="—", words="—",
                                           contour=contour, marking=True)
                if p.get("code") not in GOLDEN_HUMAN_CODES] if rec else [
        {"code": "no-record", "words": "записи о прогоне нет"}]
    first = problems[0] if problems else {}
    return {
        "allowed": not problems,
        "code": first.get("code"),
        "words": first.get("words"),
        "confirmed": bool(golden.get("confirmed")),
        "owner": decl.get("created_by"),
        "by": golden.get("by"),
        "at": golden.get("at"),
        "words_of_owner": golden.get("words"),
    }


def compare(runs_root, a: str, b: str) -> dict:
    """Сравнение двух прогонов дерева по номерам (12.2). Узла нет —
    `RunTreeError` со словами."""
    a, b = record_id_of_run(runs_root, a), record_id_of_run(runs_root, b)
    tree = load_or_build(runs_root)
    if rt.find_node(tree, a) is None or rt.find_node(tree, b) is None:
        tree = load_or_build(runs_root, rebuild=True)
    x, y = rt.find_node(tree, a), rt.find_node(tree, b)
    for run_id, found in ((a, x), (b, y)):
        if found is None:
            raise rt.RunTreeError(
                f"прогона {run_id!r} в дереве нет: индекс собран из "
                f"{len(rt.nodes(tree))} записей")
    return rt.compare(x, y)


# ------------------------- сравнение в записи (12.4: пишет хранитель)
def _brief(cmp) -> dict:
    """Сравнение для карточки: номер прогона, с которым сравнивали, и
    слова. Полное сравнение карточка берёт ручкой — в запись оно не
    кладётся: запись читают глазами, а не листают."""
    if not isinstance(cmp, dict):
        return {}
    return {"run_id": cmp.get("a"), "words": list(cmp.get("words") or [])}


def comparison(runs_root, record: dict, run_dir=None) -> dict:
    """`results.comparison.previous_run` и `.golden` записи (12.4):
    `{run_id, words[]}` по индексу дерева — карточка показывает их без
    сборки дерева.

    Зовётся хранителем при закрытии записи (состояние «завершён»).
    Индекс читается из файла и дополняется свежим узлом этого прогона;
    отказ дерева записи не роняет — сравнения просто нет (`{}`), а
    после ближайшей перестройки индекса оно появится."""
    try:
        run_id = str((record.get("declaration") or {}).get("run_id") or "")
        if not run_id:
            return {}
        tree = _with_record(load_or_build(runs_root, save=False), record,
                            run_dir)
        return {"previous_run": _brief(rt.compare_with_previous(tree, run_id)),
                "golden": _brief(rt.compare_with_golden(tree, run_id))}
    except Exception:                                     # noqa: BLE001
        return {}


# --------------------------------------------------------- золотой
def golden_problems(record: dict, *, by: str, words: str,
                    contour: str | None = None, marking: bool = True) -> list:
    """Поводы отказать в золотом признаке — перечнем `{code, words}` в
    том порядке, в каком их называют человеку (12.2).

    `marking` — подтверждение (иначе снятие: там своя пара поводов —
    прогон не золотой и снимает не тот, кто подтверждал)."""
    out: list[dict] = []
    who = (by or "").strip()
    if not who:
        out.append({"code": GOLDEN_NO_SIGNER,
                    "words": "золотым прогон не назван: не сказано, КТО "
                             "подтвердил. Признак ставится только словом "
                             "владельца после приёмки на Prod (решение "
                             "владельца 6.3 от 10.09.2026)"})
    if marking and not (words or "").strip():
        out.append({"code": GOLDEN_NO_WORDS,
                    "words": "золотым прогон не назван: не сказано, КАКИМИ "
                             "СЛОВАМИ владелец подтвердил — подпись без слов "
                             "не подпись"})
    if out:
        return out
    name = (contour or "").strip().lower()
    if name != GOLDEN_CONTOUR:
        out.append({"code": GOLDEN_NOT_PROD,
                    "words": f"золотым прогон называют только на площадке "
                             f"«{GOLDEN_CONTOUR}» после приёмки: здесь "
                             f"площадка «{name or 'не названа'}» (решение "
                             f"владельца 6.3 от 10.09.2026)"})
    results = record.get("results") if isinstance(record, dict) else None
    state = (results or {}).get("state")
    golden = (results or {}).get("golden") or {}
    if marking:
        if state != vrr.STATE_DONE:
            out.append({"code": GOLDEN_NOT_DONE,
                        "words": f"золотым может быть только завершённый "
                                 f"прогон: сейчас состояние "
                                 f"«{state or 'не названо'}» — образцом "
                                 f"незаконченный прогон не бывает"})
        return out
    if not golden.get("confirmed"):
        out.append({"code": GOLDEN_NOT_MARKED,
                    "words": "прогон золотым не назван: снимать нечего"})
        return out
    signer = str(golden.get("by") or "")
    if signer.strip().casefold() != who.strip().casefold():
        out.append({"code": GOLDEN_OTHER_SIGNER,
                    "words": f"признак снимает тот, кто подтверждал: прогон "
                             f"назван золотым — {signer or 'не назван'}, а "
                             f"снять просит {who}"})
    return out


def _put_golden(run_dir, golden: dict) -> dict:
    """Положить признак строкой в журнал хранителя и отдать запись
    целиком (сшивку). Файл `run-record.json` перепишет ближайший
    `run_record.refresh` — признак в нём возьмётся из этой же строки."""
    rec_mod.append(run_dir, SYSTEM, [], {"golden": golden})
    return rec_mod.stitch(run_dir)


def mark_golden(run_dir, *, by: str, words: str, at: str | None = None) -> dict:
    """Назвать прогон панели золотым: подпись судит ЯДРО
    (`run_tree.mark_golden` — кто подтвердил, какими словами, завершён
    ли прогон), носитель — журнал приписок хранителя. Возвращает запись
    целиком.

    Поводы отказа, которые ручка называет своими номерами, судятся до
    вызова (`golden_problems`); здесь ядро — последний рубеж, и его
    отказ (`run_record.RunRecordError`) наружу летит словами."""
    record = rec_mod.stitch(run_dir)
    if record is None:
        raise rt.RunTreeError(f"записи о прогоне {Path(run_dir).name} нет: "
                              f"золотым называть нечего")
    marked = rt.mark_golden(dict(record), by=by, words=words, at=at)
    return _put_golden(run_dir, marked["results"]["golden"])


def unmark_golden(run_dir) -> dict:
    """Снять признак золотого прогона: подтверждение отозвано. Слова
    подтверждения снимаются вместе с признаком (ядро), а прежняя строка
    журнала приписок остаётся — кто и когда подтверждал, видно всегда."""
    record = rec_mod.stitch(run_dir)
    if record is None:
        raise rt.RunTreeError(f"записи о прогоне {Path(run_dir).name} нет: "
                              f"снимать нечего")
    cleared = rt.unmark_golden(dict(record))
    return _put_golden(run_dir, cleared["results"]["golden"])
