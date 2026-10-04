# -*- coding: utf-8 -*-
"""Покрытие клауз заморозки сводом — замок биекции «не потерять»
(4П владельца, принцип 4; пятёрка проверок, п. 4).

Каждая клауза документов правил обязана иметь ВЕРДИКТ:
  * покрыта — на неё трассирована строка свода, целевая или вопрос
    анкеты (код читает sources/source_refs);
  * «не правило» — явный вердикт аудитора с причиной (заголовок,
    оглавление, справка, пример, повтор покрытой нормы, …), записан
    в exchange/coverage.json;
  * иначе — БЕЗ ВЕРДИКТА: молчание модели вердиктом не считается.
Клауза без вердикта останавливает выпуск пакета постановки и не даёт
циклу объявить «успех».

Замок содержания (аудит 2026-09-04, п. 2): тот же механизм вердиктов
распространён на ВЕЛИЧИНЫ клауз. Измеритель постановки (cdpm.measure)
находит величины клаузы, не взятые ни одним машинным полем строк и не
спрошенные в анкете; по каждой такой величине аудитор цикла даёт
вердикт «не норма» / «повтор» / «справочно» с причиной — строка
coverage.json с полем value. Вердикт по величине честно засчитывается
адресатом остатка; вердикт «не правило» по клаузе закрывает и
нормативный носитель без строк (его же закрывает ОТКРЫТЫЙ вопрос
анкеты на носителе — пока ответа нет). Величина без адресата
останавливает выпуск пакета так же, как клауза без вердикта.

Единый расчёт фактов биекции (A11, контракты П0 v1.4 §9): факты и
вердикт «биекция» считает ОДНА функция bijection_facts — по тем же
условиям, что замок выпуска пакета: клаузы без вердикта, величины без
адресата, нормативные носители без строк, частично покрытые клаузы,
строки без источника, висячие якоря (ссылки на клаузу, которой нет в
комплекте заморозки). Её читают индикатор «Биекция» панели
(cdpm.principles), сборщик пакета (export_task_package) и гейт пакета
(gates.gate_task_package); незнание (нет комплекта, сводки или
измерения) успехом не считается. Вход у закона тоже один: все читатели
судят свод ПОСЛЕ машинного лечения сборкой (package_doc) — панель на
копии, сборка на своём своде.

Свидетельство судится по форме и по принадлежности: числа фактов
берутся из ПЕРЕЧНЕЙ отчёта измерителя, а его счётчики служат сверкой
(подделан один счётчик — расхождение названо словом, факт берётся
больший); испорченная форма (не объект, не список, счётчик не целое)
нулём не считается; отчёт обязан быть о клаузах ТОГО комплекта
заморозки и о строках ТОГО свода, с которыми его читают, — чужой или
устаревший по любому из двух входов содержания не судит. Испорченное
свидетельство НАЗЫВАЕТСЯ, а не роняет читателя: слова замка содержания
строятся из проверенных перечней, а не из сырого отчёта.

    covered_anchors(doc) -> {(sid, anchor)}
    load_verdicts(exchange_dir) / save_verdicts(exchange_dir, verdicts)
    load_value_verdicts(...) / load_all_verdicts(...) — вердикты величин
    report(kit_entries, doc, verdicts[, measure_report]) -> сводка
        с перечнем missing (+ слой содержания при отчёте измерителя)
    content_layer(measure_report) -> слой содержания из отчёта измерителя
    bijection_facts(doc, coverage_report, measure_report, kit_entries)
        -> факты биекции и вердикт ok (один закон для всех читателей)
    refusal_words(facts) -> слова отказа выпуска по фактам
    package_doc(doc) -> свод в виде сборки пакета (копия) + слова сбоя
    run_kit(run_dir) / run_measure(run_dir) / run_report(run_dir)
        — комплект, измерение и сводка прогона панели
    MEANING_GAP — предел заявления (A12): что этими фактами НЕ доказано

Предел заявления (A12-заявления, контракт П0 v1.7 §9): всё названное
выше — ФАКТЫ ПОКРЫТИЯ (клаузы, якоря, строки, величины, содержание), и
смысловой полнотой они не являются. Покрытая клауза не равна всем
обязательствам внутри неё: в одном абзаце может стоять две нормы, из
которых извлечение вернёт первую, а счёт по якорю и числу второго —
нечислового — обязательства не заметит. Поэтому индикатор, манифест,
руководство и спецификация называют доказанное И ЭТОТ ПРОБЕЛ словами
MEANING_GAP; машинного признака, объявляющего смысловую полноту
доказанной, не существует.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

NOT_RULE = "не правило"
#: Вердикты аудитора по НЕПОТРЕБЛЁННОЙ ВЕЛИЧИНЕ клаузы (замок
#: содержания): «не норма» — число не задаёт ограничения (реквизит,
#: номер, пример); «повтор» — та же величина взята другой строкой или
#: клаузой; «справочно» — величина справочная, планированием не
#: используется. Закрытый перечень: иной вердикт отбраковывается.
VALUE_VERDICTS = ("не норма", "повтор", "справочно")
COVERAGE_FILE = "coverage.json"

#: ПРЕДЕЛ ЗАЯВЛЕНИЯ (A12-заявления, аудит 11.09.2026, контракт П0 v1.7
#: §9). Всё, что считают bijection_facts и замок содержания, — ФАКТЫ
#: ПОКРЫТИЯ: клауза получила вердикт, её якорь не висит, строка свода
#: имеет источник, величина клаузы взята или закрыта вердиктом. Единица
#: счёта — КЛАУЗА, ЯКОРЬ и ВЕЛИЧИНА, а не обязательство: в одном абзаце
#: могут стоять две нормы (предел смены 600 минут и обязательный
#: действующий допуск к типу), привязка любой строки покрывает якорь, а
#: учёт чисел нечислового обязательства не видит. Поэтому ни панель, ни
#: манифест, ни руководство не заявляют смысловой полноты: они называют
#: доказанное и ЭТОТ пробел. Слова — один носитель на код, документы и
#: интерфейс; машинного признака «доказано» у смысловой полноты нет и
#: до ведомости атомарных обязательств (волна 19) быть не может.
MEANING_GAP = ("полнота обязательств внутри клаузы — в том числе "
               "нечисловых — не доказана (A12); ведомость атомарных "
               "обязательств — волна 19")


def norm_value(text) -> str:
    """Ключ величины для вердикта: пробелы схлопнуты, регистр снят —
    аудитор копирует величину из листинга, набор допускает разницу
    пробелов и регистра, но не смысла."""
    return " ".join(str(text or "").replace(" ", " ").split()).casefold()


#: Коллекции модели со ссылками на клаузы: (коллекция, поле ссылок).
_REF_COLLECTIONS = (("rules", "sources"), ("questions", "source_refs"),
                    ("objectives", "sources"))
#: Строки свода (для факта «строк без источника»): правила и целевые;
#: вопрос анкеты — не строка, но его трассировка судится на висячие якоря.
_ROW_COLLECTIONS = ("rules", "objectives")


def _ref_key(ref) -> tuple | None:
    """Ключ ссылки строки на клаузу — (sid, anchor) строками, ровно тот
    же, что ключ клаузы комплекта (extract._load_kit → clauses[].anchor)
    и что сверяют гейт извлечения и цикл (valid_anchors): без иной
    нормализации — якорь в модели уже без скобок листинга (их снимает
    exchange.strip_anchor_brackets на входе ответа модели), и всякая
    иная запись есть иной якорь. Нет sid или якоря — None."""
    if not isinstance(ref, dict) or not ref.get("sid") \
            or not ref.get("anchor"):
        return None
    return (str(ref["sid"]), str(ref["anchor"]))


def covered_anchors(doc: dict) -> set:
    out: set = set()
    for coll, key in _REF_COLLECTIONS:
        for item in doc.get(coll, []) or []:
            if not isinstance(item, dict):
                continue
            for ref in (item.get(key) or []):
                k = _ref_key(ref)
                if k is not None:
                    out.add(k)
    return out


def load_verdicts(*dirs) -> dict:
    """{(sid, anchor): {"verdict", "reason", "round", "by"}} из
    coverage.json названных каталогов (обычно extract/ — вердикты кода
    и извлечения, затем exchange/ — вердикты аудитора; поздний каталог
    перекрывает ранний); нет файла — пусто."""
    out: dict = {}
    for d in dirs:
        if d is None:
            continue
        for row in _rows_of(d):
            if row.get("value") is None:
                out[(str(row["sid"]), str(row["anchor"]))] = _fields(row)
    return out


def _rows_of(d) -> list[dict]:
    path = Path(d) / COVERAGE_FILE
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [row for row in (raw.get("verdicts", []) or [])
            if isinstance(row, dict) and row.get("sid") and row.get("anchor")]


def _fields(row: dict) -> dict:
    return {k: row.get(k) for k in ("verdict", "reason", "round", "by")
            if row.get(k) is not None}


def load_value_verdicts(*dirs) -> dict:
    """{(sid, anchor, величина): {"verdict", "reason", "round", "by",
    "value"}} — вердикты аудитора по непотреблённым величинам клауз
    (строки coverage.json с полем value); ключ величины — norm_value,
    а поле value хранит величину ДОСЛОВНО, как в документе: файл
    вердиктов пересохраняется каждый раунд цикла, и нормализованный
    ключ на месте текста сделал бы его нечитаемым."""
    out: dict = {}
    for d in dirs:
        if d is None:
            continue
        for row in _rows_of(d):
            if row.get("value") is not None:
                out[(str(row["sid"]), str(row["anchor"]),
                     norm_value(row["value"]))] = dict(
                    _fields(row), value=str(row["value"]))
    return out


def load_all_verdicts(*dirs) -> dict:
    """Вердикты по клаузам (ключ из двух частей) и по величинам (ключ из
    трёх частей) одним словарём — вход измерителя постановки."""
    out = dict(load_verdicts(*dirs))
    out.update(load_value_verdicts(*dirs))
    return out


def run_verdicts(run_dir: Path) -> dict:
    """Все вердикты прогона панели: извлечение (код + модель), затем цикл."""
    run_dir = Path(run_dir)
    return load_verdicts(run_dir / "extract", run_dir / "exchange")


def run_all_verdicts(run_dir: Path) -> dict:
    """Вердикты прогона по клаузам И величинам (для измерителя)."""
    run_dir = Path(run_dir)
    return load_all_verdicts(run_dir / "extract", run_dir / "exchange")


def save_verdicts(exchange_dir: Path, verdicts: dict,
                  value_verdicts: dict | None = None) -> None:
    """Записать coverage.json. `verdicts` — по клаузам; `value_verdicts`
    — по величинам (ключ (sid, anchor, величина)). None у величин
    означает «не трогать»: строки величин, уже лежащие в файле,
    переписываются как есть — вердикт не теряется молча."""
    path = Path(exchange_dir) / COVERAGE_FILE
    if value_verdicts is None:
        value_verdicts = {
            (str(r["sid"]), str(r["anchor"]), norm_value(r["value"])):
                dict(_fields(r), value=r["value"])
            for r in _rows_of(exchange_dir) if r.get("value") is not None}
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [dict(sid=k[0], anchor=k[1], **v)
            for k, v in sorted(verdicts.items())]
    for k, v in sorted(value_verdicts.items()):
        body = dict(v)
        value = body.pop("value", None)
        rows.append(dict(sid=k[0], anchor=k[1],
                         value=value if value is not None else k[2],
                         **body))
    path.write_text(json.dumps({"verdicts": rows}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")


def report(kit_entries: list[dict], doc: dict, verdicts: dict | None,
           measure_report: dict | None = None) -> dict:
    """Сводка покрытия по клаузам комплекта заморозки (документы правил):
    всего / покрыто строками / «не правило» по вердикту аудитора /
    без вердикта (перечень с началом текста).

    measure_report (отчёт cdpm.measure) добавляет слой ПО СОДЕРЖАНИЮ
    (RAZDELENIE раздел 3 пункт 1): покрытие по якорю не означает, что
    взято всё содержание клаузы. Считаются: частично покрытые (у
    носителя есть строки, но остались величины без адресата) и
    нормативные носители без строк. С аудита 2026-09-04 (п. 2) эти два
    числа — замок содержания при сборке пакета: величина без адресата
    (ни поле, ни вопрос, ни вердикт аудитора по величине) и
    нормативный носитель без строк и без вердикта «не правило»
    останавливают выпуск (export_task_package.build_package)."""
    verdicts = verdicts or {}
    covered = covered_anchors(doc)
    total = by_rows = not_rule = 0
    missing: list[dict] = []
    for e in kit_entries:
        sid = str(e.get("sid"))
        for c in e.get("clauses") or []:
            anchor = str(c.get("anchor"))
            total += 1
            if (sid, anchor) in covered:
                by_rows += 1
            elif (verdicts.get((sid, anchor)) or {}).get("verdict") == NOT_RULE:
                not_rule += 1
            else:
                missing.append({"sid": sid, "anchor": anchor,
                                "text": str(c.get("text") or "")[:100]})
    out = {"clauses": total, "covered_by_rows": by_rows,
           "not_rule": not_rule, "missing": missing}
    if isinstance(measure_report, dict):
        out.update(content_layer(measure_report))
    return out


def _protocol_rows(measure_report: dict, form: list[str]) -> list[dict]:
    """Записи отчёта измерителя по клаузам — только словари. Иная форма
    называется словами, а не принимается молча: испорченное
    свидетельство свидетельством не является, и читатель не вправе ни
    упасть на чужом файле, ни промолчать о нём."""
    raw = measure_report.get("clauses")
    if raw is None:
        return []
    if not isinstance(raw, list):
        form.append(f"перечень клауз отчёта измерителя не список "
                    f"({type(raw).__name__})")
        return []
    rows = [r for r in raw if isinstance(r, dict)]
    if len(rows) != len(raw):
        form.append(f"записей отчёта измерителя не словарями "
                    f"{len(raw) - len(rows)} из {len(raw)}")
    return rows


def _whole(value, name: str, form: list[str]) -> int:
    """Счётчик отчёта — целое неотрицательное. Иное названо словами и
    считается нулём: число факта берётся из ПЕРЕЧНЯ, счётчик — только
    сверка (одному счётчику веры нет)."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        form.append(f"счётчик «{name}» отчёта измерителя не целое "
                    f"неотрицательное число: {value!r}")
        return 0
    return value


def _listed(measure_report: dict, name: str, form: list[str]) -> list | None:
    """Перечень отчёта измерителя: нет ключа — None (о перечне не
    заявлено), не список — None со словом о форме."""
    raw = measure_report.get(name)
    if raw is None:
        return None
    if not isinstance(raw, list):
        form.append(f"перечень «{name}» отчёта измерителя не список "
                    f"({type(raw).__name__})")
        return None
    return raw


def content_layer(measure_report: dict) -> dict:
    """Слой ПО СОДЕРЖАНИЮ из отчёта измерителя постановки (cdpm.measure)
    — один расчёт для сводки покрытия (report), замка выпуска и гейта
    пакета (bijection_facts по протоколу measure-protocol.json):
    частично покрытые клаузы (у носителя есть строки, но остались
    величины без адресата), нормативные носители без строк, величины
    без адресата; рядом — сколько остатка закрыто словом аудитора.

    Числа фактов берутся из ПЕРЕЧНЕЙ отчёта, а счётчики `counts` служат
    сверкой: расхождение перечня и счётчика — слово о форме
    (`content_form`), а величина факта берётся большая. Протокол, у
    которого подделан только счётчик, зелёного не даёт (ранние классы
    аудитора, A11-05); испорченная форма (не список, не словарь, не
    целое) тоже названа словом, а не молчанием.

    ПЕРЕЧЕНЬ НОРМАТИВНЫХ БЕЗ СТРОК ВЫВОДИТСЯ ИЗ ЗАПИСЕЙ САМОГО ОТЧЁТА
    (ревью A11-2, находка Н-4): носитель нормативен, строк не дал, не
    закрыт вердиктом «не правило» и не закрыт открытым вопросом на
    носителе — ровно этим правилом его кладёт измеритель (measure()), и
    ровно им читатель ПЕРЕСЧИТЫВАЕТ перечень. Перечню на слово веры
    нет: удалить строку перечня, сохранив запись клаузы, — тот же класс
    подделки, что «удалить дефектную строку отчёта, сохранив
    количество» (A11-05). Расхождение перечня с записями называется
    словом о форме, а факт берётся ОБЪЕДИНЕНИЕМ: заявленное плюс
    выведенное. Тогда остаток, который читателю принять нечем,
    сужается до одного признака — «не правило» (вердикт аудитора
    вне отчёта); он и назван недоступным.

    Назвать величину вправе только запись остатка ФОРМЫ ОБЪЕКТА: иных
    здесь считают (число факта их не теряет) и называют словом о форме,
    а слова замка содержания строятся из проверенных записей, а не из
    сырого отчёта — испорченное свидетельство даёт слова, а не падение
    читателя (ревью A11, п. 2).

    Граница счётчика частично покрытых (поведение измерителя, не A11):
    частичным считается носитель, у остатка которого нет НИ ОДНОГО
    адресата; закрыта вердиктом одна величина из двух — счётчик молчит,
    хотя вторая осталась без адресата. Занижение безопасно для вердикта:
    такая клауза всё равно даёт величину без адресата, а это отдельный
    факт биекции и слово замка содержания (тест
    test_частичный_адресат_остатка_счётчик_молчит_вердикт_нет)."""
    form: list[str] = []
    if not isinstance(measure_report, dict):
        form.append(f"отчёт измерителя не объект "
                    f"({type(measure_report).__name__})")
        measure_report = {}
    rows = _protocol_rows(measure_report, form)
    partial: list[dict] = []
    bad_unconsumed = 0
    for row in rows:
        if not row.get("rows"):
            continue
        unconsumed = row.get("unconsumed")
        if unconsumed is None:
            continue
        if not isinstance(unconsumed, list):
            bad_unconsumed += 1
            continue
        if row.get("unconsumed_addressed"):
            continue        # у остатка есть адресат — носитель не частичный
        if unconsumed:
            partial.append({"clause": row.get("clause"),
                            "rows": row.get("rows"),
                            "values_left": [
                                v.get("text") if isinstance(v, dict) else str(v)
                                for v in unconsumed][:8]})
    if bad_unconsumed:
        form.append(f"остаток клауз отчёта измерителя не списком: "
                    f"{bad_unconsumed}")
    counts = measure_report.get("counts")
    if counts is not None and not isinstance(counts, dict):
        form.append(f"счётчики отчёта измерителя не объект "
                    f"({type(counts).__name__})")
        counts = None
    counts = counts or {}
    orphan = _listed(measure_report, "unconsumed_without_addressee", form)
    counter = _whole(counts.get("unconsumed_without_addressee"),
                     "unconsumed_without_addressee", form)
    if orphan is not None and len(orphan) != counter:
        form.append(f"счётчик величин без адресата {counter} ≠ перечню "
                    f"величин {len(orphan)}: одному счётчику веры нет — "
                    f"факт берётся больший")
    # Записи перечня величин, У КОТОРЫХ ЕСТЬ ИМЯ (объект с клаузой и
    # величиной): только ими называются слова замка содержания. Запись
    # иной формы имени не несёт — она названа словом о форме, но из
    # ЧИСЛА факта не выпадает (величина заявлена, имени у неё нет).
    named = [v for v in orphan or () if isinstance(v, dict)]
    if orphan is not None and len(named) != len(orphan):
        form.append(f"записей перечня величин без адресата не объектами "
                    f"{len(orphan) - len(named)} из {len(orphan)} — "
                    f"назвать такую величину нечем")
    bare = _listed(measure_report, "normative_without_rows", form) or []
    bare_counter = _whole(counts.get("normative_without_rows"),
                          "normative_without_rows", form)
    if len(bare) != bare_counter:
        form.append(f"счётчик нормативных носителей без строк "
                    f"{bare_counter} ≠ перечню {len(bare)}: одному "
                    f"счётчику веры нет — факт берётся больший")
    # Перечень против ЗАПИСЕЙ отчёта (Н-4): признак «не правило» —
    # единственная часть правила, которой у читателя нет своего
    # свидетельства (вердикт аудитора с пакетом не едет); нормативность,
    # строки и открытые вопросы отчёт несёт сам, и перечень обязан им
    # отвечать.
    derived = [str(r.get("clause")) for r in rows
               if r.get("normative") and not r.get("rows")
               and not r.get("not_rule_verdict")
               and not r.get("questions_open")]
    told_bare = {str(k) for k in bare}
    missed = [k for k in derived if k not in told_bare]
    extra = sorted(told_bare - set(derived))
    if missed or extra:
        form.append(
            "перечень нормативных носителей без строк не выведен из "
            "записей самого отчёта измерителя"
            + (f"; записи называют, а перечень молчит о {len(missed)}: "
               + ", ".join(missed[:5]) if missed else "")
            + (f"; перечень называет, а записи не подтверждают "
               f"{len(extra)}: " + ", ".join(extra[:5]) if extra else ""))
    bare = list(bare) + missed
    return {
        "covered_partially": len(partial),
        "partial": partial,
        "normative_without_rows": list(bare),
        # Тот же закон для обоих счётчиков содержания: число факта —
        # большее из счётчика и перечня, расхождение названо словом.
        "normative_without_rows_count": max(bare_counter, len(bare)),
        "values_without_addressee": max(counter, len(orphan or ())),
        # Слой вердиктов по величинам и по нормативным носителям без
        # строк (замок содержания): сколько остатка закрыто словом
        # аудитора, а не полем и не вопросом — предъявляется отдельно.
        "values_by_verdict": _whole(counts.get("unconsumed_by_verdict"),
                                    "unconsumed_by_verdict", form),
        "normative_not_rule": _whole(counts.get("normative_not_rule"),
                                     "normative_not_rule", form),
        # Слова о ФОРМЕ свидетельства (испорчено, не сходится с самим
        # собой): читатель обязан их предъявить, а не считать нулями.
        "content_form": form,
    }


def _measure_keys(rows: list[dict]) -> set:
    """Клаузы источника, которые ОТЧЁТ измерителя объявляет судимыми:
    состав носителя (`members` — клетки таблицы, собранные в строку), а
    где состава нет — сам ключ клаузы «sid|якорь»."""
    seen: set = set()
    for r in rows:
        members = r.get("members")
        if isinstance(members, list) and members:
            seen.update(str(k) for k in members)
        elif r.get("clause"):
            seen.add(str(r["clause"]))
    return seen


def _measure_row_ids(measure_report: dict) -> set:
    """Строки свода, о которых ОТЧЁТ измерителя судит: идентификаторы на
    клаузах (`clauses[].rows`) и строки без клаузы-источника
    (`orphan_rows`). Измеритель кладёт всякую строку свода ровно в одно
    из двух мест, поэтому это множество и есть «свод, о котором отчёт»;
    испорченная форма перечня молча нулём не считается — ключа просто
    нет, и множество разойдётся со сводом."""
    out: set = set()
    for r in _protocol_rows(measure_report, []):
        rows = r.get("rows")
        if isinstance(rows, list):
            out.update(str(x) for x in rows)
    orphan = measure_report.get("orphan_rows")
    if isinstance(orphan, list):
        out.update(str(x.get("id")) for x in orphan if isinstance(x, dict))
    return out


def _doc_row_ids(doc: dict) -> set:
    """Строки свода (правила и целевые) — те же записи и тем же ключом
    идентификатора, что берёт измеритель (measure.measure: rules +
    objectives, `str(id)`)."""
    out: set = set()
    for coll in _ROW_COLLECTIONS:
        items = doc.get(coll)
        if not isinstance(items, list):
            continue
        out.update(str(it.get("id")) for it in items if isinstance(it, dict))
    return out


def bijection_facts(doc: dict, coverage_report: dict | None = None,
                    measure_report: dict | None = None,
                    kit_entries: list[dict] | None = None) -> dict:
    """ЕДИНЫЙ расчёт фактов биекции «не потерять» и вердикт по ним
    (A11, контракты П0 v1.4 §9): одна функция для индикатора «Биекция»
    панели (principles.summary), замка выпуска пакета постановки
    (export_task_package._build_package) и гейта пакета
    (gates.gate_task_package) — одна норма, одна функция, все читатели.

    Факты (число или перечень, не мнение):
      * rows_without_source — строки свода (правила, целевые) без ссылки
        на источник либо со ссылкой на необъявленный источник (перечень
        — rows_without_source_ids);
      * dangling_anchors — висячие якоря: ссылки правил, вопросов и
        целевых на необъявленный источник (судятся всегда) или на якорь,
        которого нет среди клауз этого источника в комплекте заморозки
        (судятся только при данном комплекте — anchors_judged); тот же
        ключ (sid, anchor), что у гейта извлечения и цикла;
      * missing — клаузы без вердикта покрытия (из сводки report;
        coverage_judged — сводка дана);
      * covered_partially, values_without_addressee,
        normative_without_rows — слой содержания (content_judged): из
        отчёта измерителя, когда он дан (протокол пакета), иначе из
        того же слоя в сводке покрытия (report с measure_report), иначе
        не судится.

    ok — ТОЛЬКО когда всё судилось и все факты нулевые: ровно те
    условия, при которых слова отказа по покрытию и содержанию
    (refusal_words) пусты. Обратного ok не обещает: у выпуска есть и
    другие рубежи (готовность, каталог, непротиворечивость) — их судит
    не эта функция и не принцип 4. Незнание успехом не считается: не
    данный комплект, сводка или измерение дают ok=False и называются
    словами. Свод читателям ОДИН: и панель, и сборка судят свод ПОСЛЕ
    машинного лечения сборкой (package_doc → heal_for_package) —
    один закон требует одного входа, иначе индикатор и выпуск
    расходятся на строках, которые лечение снимает (ревью A11, п. 3).

    СВИДЕТЕЛЬСТВО СУДИТСЯ ПО ФОРМЕ И ПО ПРИНАДЛЕЖНОСТИ. Испорченный
    вход (не объект, не список, счётчик не целое) нулём не считается —
    он назван словом вида «form». Отчёт измерителя обязан быть О ТОМ
    комплекте заморозки и О ТОМ своде, с которыми его читают: клаузы
    отчёта сверяются с клаузами комплекта (measure_of_kit), строки
    отчёта — со строками свода (measure_of_doc), и чужой либо
    устаревший по любому из двух входов отчёт (клауза комплекта не
    судилась, клауза отчёта комплекту не принадлежит, строка свода не
    судилась, строка отчёта своду не принадлежит) содержания не судит.
    Панель и сборка меряют текущие входы и сходятся всегда; проверка
    работает там, где отчёт приезжает готовым, — у гейта пакета.

    problems — слова по каждому ненулевому факту и по каждому
    несудившемуся; problems_by_kind — те же слова по видам (rows /
    anchors / missing / content / form / unjudged), чтобы читатель дал
    им свой префикс (замок сборки, гейт по протоколу). Вид «form» —
    порок самого свидетельства, он красный у всякого читателя; вид
    «unjudged» (свидетельства нет вовсе) читатель, которому этого входа
    и не полагается, вправе не предъявлять."""
    form: list[str] = []
    if not isinstance(doc, dict):
        form.append(f"свод не объект ({type(doc).__name__}) — судить нечего")
        doc = {}
    raw_sources = doc.get("sources")
    if raw_sources is not None and not isinstance(raw_sources, list):
        form.append(f"перечень источников свода не список "
                    f"({type(raw_sources).__name__})")
        raw_sources = None
    sources = {str(s.get("sid")) for s in raw_sources or []
               if isinstance(s, dict) and s.get("sid")}
    known: set | None = None
    if kit_entries is not None:
        if not isinstance(kit_entries, list):
            form.append(f"комплект заморозки не список записей "
                        f"({type(kit_entries).__name__}) — якоря судить "
                        f"нечем")
        else:
            known = set()
            bad_entries = bad_clauses = 0
            for e in kit_entries:
                if not isinstance(e, dict) or not e.get("sid") \
                        or not isinstance(e.get("clauses"), list):
                    bad_entries += 1
                    continue
                for c in e["clauses"]:
                    if not isinstance(c, dict) or not c.get("anchor"):
                        bad_clauses += 1
                        continue
                    known.add((str(e["sid"]), str(c["anchor"])))
            if bad_entries:
                form.append(f"записей комплекта заморозки не формы "
                            f"{{sid, clauses: [...]}}: {bad_entries}")
            if bad_clauses:
                form.append(f"клауз комплекта заморозки без якоря: "
                            f"{bad_clauses}")
    rows_without_source: list[str] = []
    dangling: list[dict] = []
    for coll, key in _REF_COLLECTIONS:
        items = doc.get(coll)
        if items is not None and not isinstance(items, list):
            form.append(f"перечень «{coll}» свода не список "
                        f"({type(items).__name__})")
            continue
        for item in items or []:
            if not isinstance(item, dict):
                form.append(f"запись «{coll}» свода не объект")
                continue
            item_id = str(item.get("id") or "<без id>")
            refs = item.get(key)
            bad_row = not refs
            if refs is not None and not isinstance(refs, list):
                # Ссылки не списком — ОДНА неисправная трассировка, а не
                # столько, сколько в строке знаков.
                bad_row = True
                dangling.append({"coll": coll, "id": item_id,
                                 "sid": None, "anchor": None,
                                 "why": "перечень ссылок не список"})
                refs = None
            for ref in refs or []:
                k = _ref_key(ref)
                if k is None:
                    bad_row = True
                    dangling.append({"coll": coll, "id": item_id,
                                     "sid": None, "anchor": None,
                                     "why": "ссылка не объект {sid, anchor}"})
                elif k[0] not in sources:
                    bad_row = True
                    dangling.append({"coll": coll, "id": item_id,
                                     "sid": k[0], "anchor": k[1],
                                     "why": "источник не объявлен"})
                elif known is not None and k not in known:
                    dangling.append({"coll": coll, "id": item_id,
                                     "sid": k[0], "anchor": k[1],
                                     "why": "якоря нет среди клауз "
                                            "источника в комплекте"})
            if bad_row and coll in _ROW_COLLECTIONS:
                rows_without_source.append(item_id)
    facts: dict = {
        "rows_without_source": len(rows_without_source),
        "rows_without_source_ids": rows_without_source,
        "dangling_anchors": dangling,
        "anchors_judged": known is not None,
        "coverage_judged": isinstance(coverage_report, dict),
        "clauses": 0,
        "missing": 0,
        "content_judged": False,
        "covered_partially": 0,
        "values_without_addressee": 0,
        "normative_without_rows": 0,
        "partial": [],
        "normative_without_rows_keys": [],
        "measure_of_kit": None,
        "measure_of_doc": None,
    }
    missing_rows: list = []
    if coverage_report is not None and not isinstance(coverage_report, dict):
        form.append(f"сводка покрытия не объект "
                    f"({type(coverage_report).__name__})")
    elif isinstance(coverage_report, dict):
        raw_missing = coverage_report.get("missing")
        if raw_missing is not None and not isinstance(raw_missing, list):
            form.append(f"перечень клауз без вердикта не список "
                        f"({type(raw_missing).__name__})")
        else:
            missing_rows = list(raw_missing or [])
        facts["clauses"] = _whole(coverage_report.get("clauses"),
                                  "clauses сводки покрытия", form)
        facts["missing"] = len(missing_rows)

    # Свидетельство о содержании: протокол измерителя старше слоя,
    # уехавшего в сводку, но и он обязан быть О ЭТОМ комплекте.
    layer = None
    #: Отчёт измерителя формы объекта — только по нему строятся слова с
    #: именами (иначе свидетельство названо словом о форме и молчит).
    proto: dict | None = None
    if measure_report is not None:
        if not isinstance(measure_report, dict):
            form.append(f"отчёт измерителя не объект "
                        f"({type(measure_report).__name__}) — содержание "
                        f"им не судится")
        else:
            proto = measure_report
            layer = content_layer(measure_report)
            form += list(layer.get("content_form") or [])
        if known is not None and isinstance(measure_report, dict):
            seen = _measure_keys(_protocol_rows(measure_report, []))
            kit_keys = {f"{sid}|{anchor}" for sid, anchor in known}
            alien = sorted(seen - kit_keys)
            lost = sorted(kit_keys - seen)
            facts["measure_of_kit"] = not (alien or lost)
            if alien or lost:
                layer = None
                form.append(
                    "отчёт измерителя не о клаузах этого комплекта "
                    "заморозки — содержание им не судится"
                    + (f"; чужих клауз {len(alien)}: " + ", ".join(alien[:5])
                       if alien else "")
                    + (f"; клауз комплекта не судилось {len(lost)}: "
                       + ", ".join(lost[:5]) if lost else ""))
        if isinstance(measure_report, dict):
            # Та же принадлежность по ВТОРОМУ входу измерителя: отчёт
            # обязан быть о строках ТОГО свода, с которым его читают.
            # Панель и сборка меряют текущий свод и сходятся всегда;
            # гейт читает запечатанный пакет, где свод подменить можно
            # (манифест пересчитывается), — и тогда содержание судится
            # по чужому либо устаревшему свидетельству.
            told = _measure_row_ids(measure_report)
            mine = _doc_row_ids(doc)
            alien_rows = sorted(told - mine)
            lost_rows = sorted(mine - told)
            facts["measure_of_doc"] = not (alien_rows or lost_rows)
            if alien_rows or lost_rows:
                layer = None
                form.append(
                    "отчёт измерителя не о строках этого свода — "
                    "содержание им не судится"
                    + (f"; чужих строк {len(alien_rows)}: "
                       + ", ".join(alien_rows[:5]) if alien_rows else "")
                    + (f"; строк свода не судилось {len(lost_rows)}: "
                       + ", ".join(lost_rows[:5]) if lost_rows else ""))
    elif isinstance(coverage_report, dict) \
            and "values_without_addressee" in coverage_report:
        layer = coverage_report
        form += list(coverage_report.get("content_form") or [])
    if layer is not None:
        keys = list(layer.get("normative_without_rows") or [])
        facts.update(
            content_judged=True,
            covered_partially=_whole(layer.get("covered_partially"),
                                     "covered_partially", form),
            values_without_addressee=_whole(
                layer.get("values_without_addressee"),
                "values_without_addressee", form),
            normative_without_rows=max(
                len(keys), _whole(layer.get("normative_without_rows_count"),
                                  "normative_without_rows_count", form)),
            # Чистота входов (ревью A11, п. 7): результат не делит
            # объектов со сводкой и отчётом — порча через факты во вход
            # не проходит, и повторный вызов равен первому.
            partial=copy.deepcopy(list(layer.get("partial") or [])),
            normative_without_rows_keys=keys)

    by_kind: dict = {"rows": [], "anchors": [], "missing": [],
                     "content": [], "form": form, "unjudged": []}
    if rows_without_source:
        by_kind["rows"].append(
            f"строк без источника {len(rows_without_source)} — каждая "
            f"строка свода (правило, целевая) обязана ссылаться на "
            f"объявленный источник: {', '.join(rows_without_source[:5])}")
    if dangling:
        heads = "; ".join(f"{d['id']} → {d['sid']}/{d['anchor']!r} "
                          f"({d['why']})" for d in dangling[:5])
        by_kind["anchors"].append(
            f"висячих якорей {len(dangling)} — трассировка на "
            f"несуществующую клаузу комплекта заморозки (та же проверка, "
            f"что у гейта извлечения): {heads}")
    if facts["missing"]:
        heads = "; ".join(f"{x.get('sid')}/{x.get('anchor')}: "
                          f"{str(x.get('text') or '')[:60]}"
                          for x in missing_rows[:3] if isinstance(x, dict))
        by_kind["missing"].append(
            f"клауз без вердикта покрытия {facts['missing']} из "
            f"{facts['clauses']} — каждая клауза документов правил "
            f"обязана быть покрыта строкой свода либо признана "
            f"аудитором «не правило» с причиной (аудит-цикл): {heads}")
    if layer is not None:
        if proto is not None:
            # Слова замка содержания — с именем клаузы и величины, из
            # той же функции, что судит сборщик и гейт (measure). Читает
            # она ПРОВЕРЕННУЮ выписку протокола, а не сырой отчёт:
            # испорченное свидетельство обязано дать слова, а не уронить
            # читателя (ревью A11, п. 2 — «нормативные без строк: 5»,
            # «остаток: 'нет'», «остаток: [1]» роняли и панель, и гейт).
            from cdpm import measure as ms_mod
            named = [v for v in
                     (_listed(proto, "unconsumed_without_addressee", []) or ())
                     if isinstance(v, dict)]
            by_kind["content"] += ms_mod.content_lock({
                "unconsumed_without_addressee": named,
                "normative_without_rows": list(
                    layer.get("normative_without_rows") or []),
                "clauses": _protocol_rows(proto, [])})
            if facts["values_without_addressee"] > len(named):
                by_kind["content"].append(
                    f"величин клауз без адресата "
                    f"{facts['values_without_addressee']} по счётчику "
                    f"протокола, названо {len(named)} — остальных "
                    f"перечень протокола не несёт")
            if facts["normative_without_rows"] > len(
                    facts["normative_without_rows_keys"]):
                by_kind["content"].append(
                    f"нормативных носителей без строк свода "
                    f"{facts['normative_without_rows']} по счётчику "
                    f"протокола, названо "
                    f"{len(facts['normative_without_rows_keys'])} — "
                    f"остальных перечень протокола не несёт")
        else:
            if facts["values_without_addressee"]:
                by_kind["content"].append(
                    f"величин клауз без адресата "
                    f"{facts['values_without_addressee']} — каждая "
                    f"величина документа правил обязана быть взята "
                    f"машинным полем строки, спрошена в анкете либо "
                    f"получить вердикт аудитора «не норма / повтор / "
                    f"справочно» с причиной (перечень — в отчёте "
                    f"измерителя)")
            if facts["normative_without_rows"]:
                by_kind["content"].append(
                    f"нормативных носителей без строк свода "
                    f"{facts['normative_without_rows']} — клауза с "
                    f"признаками нормы обязана дать строку свода либо "
                    f"получить вердикт аудитора «не правило» с причиной "
                    f"либо открытый вопрос анкеты на носителе: "
                    + ", ".join(str(k) for k in
                                facts["normative_without_rows_keys"][:5]))
        if facts["covered_partially"]:
            heads = "; ".join(
                f"{p.get('clause')}: "
                + ", ".join(f"«{v}»" for v in
                            (p.get("values_left") or [])[:4])
                for p in facts["partial"][:5])
            by_kind["content"].append(
                f"частично покрытых клауз {facts['covered_partially']} — "
                f"у носителя есть строки свода, но остались величины без "
                f"адресата: {heads}")
    if known is None:
        by_kind["unjudged"].append(
            "висячие якоря не судились — комплект заморозки не дан")
    if not facts["coverage_judged"]:
        by_kind["unjudged"].append(
            "покрытие клауз не судилось — сводки покрытия нет")
    if layer is None:
        if measure_report is None:
            why = "отчёта измерителя нет"
        elif proto is None:
            why = "отчёт измерителя испорчен — он не объект"
        elif facts["measure_of_kit"] is False:
            why = "отчёт измерителя этому комплекту заморозки не принадлежит"
        else:
            why = "отчёт измерителя этому своду не принадлежит"
        by_kind["unjudged"].append("содержание клауз не судилось — " + why)
    problems = (by_kind["missing"] + by_kind["content"] + by_kind["rows"]
                + by_kind["anchors"] + by_kind["form"] + by_kind["unjudged"])
    facts["problems_by_kind"] = by_kind
    facts["problems"] = problems
    facts["ok"] = (not problems and not rows_without_source and not dangling
                   and facts["missing"] == 0
                   and facts["covered_partially"] == 0
                   and facts["values_without_addressee"] == 0
                   and facts["normative_without_rows"] == 0)
    return facts


def refusal_words(facts: dict) -> list[str]:
    """Слова отказа выпуска пакета по фактам биекции — строки для
    TaskPackageError сборщика; та же норма слов, что видит панель в
    problems индикатора и гейт по протоколу: клаузы без вердикта как
    есть; слой содержания — с именем замка «замок содержания»; строки
    без источника, висячие якоря, порочное свидетельство и всё
    несудившееся — «замок биекции». Пусто ⇔ facts["ok"]."""
    kinds = facts.get("problems_by_kind") or {}
    out = list(kinds.get("missing") or [])
    out += ["замок содержания: " + p for p in kinds.get("content") or []]
    out += ["замок биекции: " + p
            for p in (list(kinds.get("rows") or [])
                      + list(kinds.get("anchors") or [])
                      + list(kinds.get("form") or [])
                      + list(kinds.get("unjudged") or []))]
    if not out and not facts.get("ok"):
        out.append("замок биекции: факты биекции ненулевые, слов нет — "
                   "расчёт не сошёлся")
    return out


def package_doc(doc: dict) -> tuple[dict, str | None]:
    """Свод в том виде, в каком его судит ЗАМОК ВЫПУСКА пакета: КОПИЯ
    канонического свода, проведённая через ту же цепочку машинного
    лечения сборки (export_task_package.heal_for_package —
    детерминированную и идемпотентную). Возвращает (копия, None) либо
    (исходный свод, слова сбоя).

    Зачем (A11, ревью п. 3): сборка судит факты биекции ПОСЛЕ лечения,
    а панель судила канонический свод — и расходилась со сборкой на
    строках, которые лечение снимает (целевая-осколок ответа без
    машинной метрики и без трассировки: панель краснила «строк без
    источника 1», выпуск и гейт были зелёные). Один закон требует
    одного входа: панель судит ту же копию.

    Канонический model.json прогона не трогается: лечение идёт по
    глубокой копии, ничего не пишется. Сбой лечения (каталог шаблонов
    недоступен — ярлыки семей) не прячется и карточку не роняет: судим
    канонический свод и называем расхождение словом; канон строже
    лечёного (лечение строки снимает, а не добавляет источники), то
    есть ложного зелёного такой отказ дать не может."""
    from cdpm import export_task_package as etp_mod
    out = copy.deepcopy(doc) if isinstance(doc, dict) else doc
    try:
        # CDPM-298: expose the same role-unit refusal as package release,
        # both on the original carriers and on the healed copy.
        etp_mod.refuse_diverging_carriers(out)
        etp_mod.refuse_invalid_logic_units(out)
        etp_mod.heal_for_package(out)
        etp_mod.refuse_diverging_carriers(out)
        etp_mod.refuse_invalid_logic_units(out)
        # CDPM-310 / COSMOS-9d85: readiness judges the full release
        # projection, even when logic does not reference a bad clock cell.
        # The release reader already supplies the addressed refusal words;
        # do not persist a second diagnostic or rewrite the facts copy.
        for rule in out.get("rules", []):
            etp_mod.derive_params_from_landing(rule)
    except etp_mod.TaskPackageError as exc:
        return doc, str(exc)
    except Exception as exc:                                  # noqa: BLE001
        return doc, (f"свод к виду сборки пакета не приведён "
                     f"({type(exc).__name__}: {exc}) — факты считаны по "
                     f"каноническому своду и могут быть строже "
                     f"выпуска")[:300]
    return out, None


def run_kit(run_dir: Path) -> list[dict] | None:
    """Комплект заморозки прогона панели (записи clauses/<sid>.json по
    манифесту) — вход судьи якорей (bijection_facts); без заморозки —
    None."""
    from cdpm import extract as extract_mod
    kit = Path(run_dir) / "freeze"
    if not (kit / "manifest.json").is_file():
        return None
    return extract_mod._load_kit(kit)


def run_measure(run_dir: Path, kit_entries: list[dict],
                doc: dict) -> tuple[dict | None, str | None]:
    """Измерение постановки для панели: измеритель (cdpm.measure) на
    ТЕКУЩЕМ своде и клаузах комплекта с теми же вердиктами аудитора,
    что судят замок выпуска (run_all_verdicts) — панель видит те же
    факты, что замок (стоимость — десятки миллисекунд на прогон).
    Возвращает (отчёт, None) либо (None, слова сбоя): сбой измерителя
    не прячется и не роняет карточку — единый расчёт
    (bijection_facts) честно скажет «содержание не судилось»."""
    try:
        from cdpm import measure as ms_mod
        report_ = ms_mod.measure(
            doc, [{"sid": e["sid"], "anchor": c["anchor"],
                   "text": c.get("text") or ""}
                  for e in kit_entries for c in e.get("clauses") or []],
            verdicts=run_all_verdicts(Path(run_dir)))
    except Exception as exc:                                  # noqa: BLE001
        return None, f"измеритель постановки не отработал: {exc}"[:300]
    return report_, None


def run_report(run_dir: Path, kit_entries: list[dict] | None = None,
               doc: dict | None = None,
               measured: tuple | None = None, *,
               include_obligations: bool = False,
               obligations_model_bytes: bytes | None = None) -> dict | None:
    """Сводка покрытия прогона панели: freeze + extract/model.json +
    вердикты extract/ и exchange/ (coverage.json); без заморозки или
    модели — None.

    A11: сводка несёт и слой содержания — `measured` = результат
    run_measure (отчёт, сбой); не дан — измеряется здесь. При сбое
    измерителя сводка остаётся без слоя содержания с полем
    measure_error.

    A12 opt-in: ``include_obligations`` добавляет отдельную ведомость
    ``obligations`` из exchange/obligations.json. Её отсутствие, устаревание
    и ошибки не становятся пустым успешным списком и не меняют A11/MEANING_GAP.
    Если doc — лечёная/пакетная копия, нужны точные bytes этого свода в
    obligations_model_bytes; канонические bytes нельзя выдать за другой свод.
    Без комплекта/модели прежний возврат None сохранён; отдельный адаптер
    cdpm.obligations.run_report доступен независимо от отчёта покрытия."""
    from cdpm import model as m
    run_dir = Path(run_dir)
    if kit_entries is None:
        kit_entries = run_kit(run_dir)
    model_path = run_dir / "extract" / "model.json"
    if kit_entries is None or not model_path.is_file():
        return None
    if doc is None:
        doc = m.load_model(model_path)
    if measured is None:
        measured = run_measure(run_dir, kit_entries, doc)
    ms_report, error = measured
    out = report(kit_entries, doc, run_verdicts(run_dir),
                 measure_report=ms_report)
    if error:
        out["measure_error"] = error
    if include_obligations:
        from cdpm import obligations
        out["obligations"] = obligations.run_report(
            run_dir, model_bytes=obligations_model_bytes, expected_model=doc)
    return out
