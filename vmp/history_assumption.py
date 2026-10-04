# -*- coding: utf-8 -*-
"""Заявление о непереданной истории сотрудника (внешний аудит
04.09.2026, A7, очередь 3, ступень M).

Пакет постановки сегодня не несёт раздел «история и утверждённые
назначения» (остатки на начало периода: месячные накопители, последняя
смена и отдых перед горизонтом, серии, закрепления). Правила, чья мера
зависит от того, что было ДО горизонта, судятся решателем и
сертификатом только внутри горизонта, а остаток на начало принимается
нулевым. Это допущение, не норма — и оно обязано быть сказано в выдаче
и сертификате словами, с перечнем правил, а не подразумеваться.

Ступень L (общий контракт с CDPM): раздел `history` в пакете; когда он
появится, заявление переключится на «история передана», и потребители
(решатель, сертификат) обязаны читать раздел, а не ноль.

НОСИТЕЛЬ ПЕРЕЧНЯ — ОДНО ПОЛЕ ЗАПИСИ (каталог 0.5.0, 11.09.2026).
Перечень семей, читающих историю, здесь рукой НЕ ПИШЕТСЯ и из природы
записи НЕ выводится: его несёт поле записи семьи `reads_history {need,
kind}` — запись сама говорит, нужна ли ей история, какая именно (вид
словаря `history_read_kinds`) и зачем (потребность словами).
Рукописный перечень границы горизонта (четыре семьи; долг 10.09.2026:
«признака „читает историю“ у каталога 0.4.0 нет ни в записи, ни в
таблицах решателя») снят — поле называет и их. Семья БЕЗ поля
допущения не получает даже при природе «история»: носитель один, и
молчание поля значит «истории не читает», а не «спросить у природы».

ДОПУЩЕНИЕ — ПО ВИДУ ЧТЕНИЯ. Видов четыре (accumulator, boundary,
series, window), и заявление в выдачу и в сертификат ростера идёт
блоками по видам: в блоке — семьи этого вида и их потребности словами
записи. Та же раскладка у анкеты CDPM (один блок вопросов на вид,
идентификаторы `Q-3b-история-<kind>`): восемь семей — не восемь
допущений, сколько видов встретилось в своде, столько и блоков.
"""
from __future__ import annotations

#: Природа записи каталога шаблонов, которой каталог называет механику
#: истории (серии, окна, отдых в скользящем окне, норма выходных).
#: ПЕРЕЧЕНЬ СЕМЕЙ ПО НЕЙ БОЛЬШЕ НЕ СТРОИТСЯ: носитель — поле
#: `reads_history` (см. шапку файла). Имя оставлено правилу каталога X19
#: («семья истории обязана назвать, что читает из истории») и тем
#: местам, что говорят о природе словами; носителем перечня оно не
#: является.
HISTORY_NATURE = "history"

#: Вид чтения истории у семьи, о которой запись промолчала (запас на
#: молчание двери, ниже): блок заявления всё равно обязан быть назван —
#: безымянный вид честнее пустого перечня.
KIND_UNKNOWN = "не назван"

#: Кэш перечня: (экземпляр каталога, снимок форм проверок, чтения,
#: слова). Ключ — САМ ЭКЗЕМПЛЯР двери: читатель отдаёт новый объект,
#: когда файлы каталога сменились, и сравнение по тождеству ловит смену
#: без второго обхода файлов (приём соседей — `vmp.chain_price`).
#: Второй ключ — формы проверок: от них зависит запас на молчание.
_CACHE: tuple | None = None


def _forms() -> dict:
    """Семьи, чью ФОРМУ строит сертификат и решатель
    (`vmp.run.logic_checks.FAMILY_FORMS`): {семья: имя формы}.

    Это НЕ второй перечень семей истории, а ЗАПАС НА МОЛЧАНИЕ ДВЕРИ:
    пока каталог читается, перечень строит одно поле `reads_history`.
    Дверь замолчала — заявление не отказывает и не теряет правила, чью
    форму судит сертификат: их называет сторона кода, и о том, что вид
    чтения и потребность не названы, строка заявления говорит словами.
    """
    try:
        from vmp.run import logic_checks as _lgc
    except Exception:                                    # noqa: BLE001
        return {}
    return {str(fam): str(form) for fam, form in _lgc.FAMILY_FORMS.items()}


def _catalog():
    """Экземпляр каталога у двери либо None. Каталог молчит — None:
    молчание каталога не превращает вопрос об истории в отказ (тот же
    выбор, что у CDPM; отказ без каталога — у выпуска пакета и у двери,
    не здесь), а семьи форм заявление всё равно получает стороной кода."""
    try:
        from vmp import catalog_access as ca
    except ImportError:                                  # pragma: no cover
        return None
    try:
        return ca.current()
    except ca.CATALOG_ERRORS:
        return None


def _reads(cat) -> dict:
    """Чтение истории по записям каталога: {семья: {"kind", "need"}}.

    Читается ОДНО поле записи — `reads_history`; его форму судит сам
    каталог (правила X15 и X19), здесь она только приводится к строкам.
    """
    if cat is None:
        return {}
    try:
        meta = cat.families_meta()
    except Exception:                                    # noqa: BLE001
        return {}                                        # pragma: no cover
    out: dict = {}
    for fid, m in meta.items():
        if not isinstance(m, dict):
            continue
        rh = m.get("reads_history")
        if not isinstance(rh, dict):
            continue
        out[str(fid)] = {"kind": str(rh.get("kind") or "").strip(),
                         "need": str(rh.get("need") or "").strip()}
    return out


def _words(fid: str, read: dict | None, form: str | None = None) -> str:
    """Слова о семье для заявления — из носителя, а не из перечня.

    Вид чтения назван КОДОМ словаря каталога (`history_read_kinds`), а
    человеческие слова — это `need` самой записи: русской карты видов в
    COSMOS нет и не заводится, иначе рядом с полем встал бы второй
    носитель — ровно то, что выпуск 0.5.0 убрал у единиц нарушения.
    """
    if read:
        kind, need = read.get("kind"), read.get("need")
        head = "семья истории %s" % fid
        if kind:
            head = "%s (чтение истории «%s»)" % (head, kind)
        return "%s: %s" % (head, need) if need else head
    if form:
        return ("семья истории %s (форма решателя %s; каталог молчит — "
                "вид чтения и потребность не названы)" % (fid, form))
    return "семья истории %s" % fid                      # pragma: no cover


def _known() -> tuple:
    """Носитель одним обходом: ({семья: {kind, need}}, {семья: слова}).

    Каталог читается — перечень строит ТОЛЬКО поле записи. Дверь
    молчит — остаются семьи форм проверок (запас, см. `_forms`), и
    молчание кэшем не запоминается: живой каталог сразу возвращает свои
    записи.
    """
    global _CACHE
    forms = _forms()
    key = tuple(sorted(forms.items()))
    cat = _catalog()
    hit = _CACHE
    if hit is not None and cat is not None and hit[0] is cat and hit[1] == key:
        # Копия ДВУХ уровней: в кэше лежат вложенные словари чтения, и
        # общая ссылка на них дала бы зовущему право молча испортить
        # кэш — ровно тот класс расхождения, который чинит носитель.
        return ({fid: dict(rec) for fid, rec in hit[2].items()},
                dict(hit[3]))
    if cat is None:
        return {}, {fid: _words(fid, None, form)
                    for fid, form in sorted(forms.items())}
    reads = _reads(cat)
    words = {fid: _words(fid, reads[fid]) for fid in sorted(reads)}
    _CACHE = (cat, key,
              {fid: dict(rec) for fid, rec in reads.items()}, dict(words))
    return reads, words


def history_families() -> dict:
    """Семьи правил, мера которых зависит от событий до начала горизонта:
    {семья: слова о ней}.

    НОСИТЕЛЬ — поле записи каталога `reads_history` (см. шапку файла):
    ни рукописного перечня, ни вывода из природы здесь нет. Дверь
    молчит — остаются семьи, чью форму строит сертификат
    (`logic_checks.FAMILY_FORMS`), и это сказано словами самой строки.
    """
    return _known()[1]


def history_reads() -> dict:
    """Чтение истории по семьям: {семья: {"kind", "need"}}.

    Пусто, когда дверь молчит: вид чтения и потребность живут только в
    записи, и выдумывать их запасу нечем.
    """
    return _known()[0]


def read_blocks(families, *, passed: bool) -> list:
    """Заявление ПО ВИДАМ чтения истории: по блоку на вид, семьи вида
    названы поимённо, потребности — текстом записи.

    Так же разложена анкета CDPM (`Q-3b-история-<kind>`): один блок
    вопросов на вид, а не на семью — восемь вопросов на маленьком наборе
    были бы шумом. Допущение о нулевом остатке называется В КАЖДОМ
    блоке: оно действует по виду чтения, а не «вообще».
    """
    reads = history_reads()
    by_kind: dict = {}
    for fid in families:
        rec = reads.get(str(fid)) or {}
        kind = str(rec.get("kind") or "") or KIND_UNKNOWN
        block = by_kind.setdefault(kind, {"kind": kind, "families": [],
                                          "needs": []})
        if str(fid) not in block["families"]:
            block["families"].append(str(fid))
        need = str(rec.get("need") or "").strip()
        if need and need not in block["needs"]:
            block["needs"].append(need)
    out = []
    for kind in sorted(by_kind):
        block = by_kind[kind]
        block["families"] = sorted(block["families"])
        block["assumption"] = (
            None if passed else
            "остаток на начало периода = 0 (чтение истории «%s»)" % kind)
        out.append(block)
    return out


def _blocks_words(blocks: list) -> str:
    """Блоки видов словами — для строки заявления."""
    parts = []
    for b in blocks:
        needs = "; ".join(b["needs"]) or "потребность записью не названа"
        parts.append("«%s» — %s (%s)"
                     % (b["kind"], ", ".join(b["families"]), needs))
    return "; ".join(parts)


def forget() -> None:
    """Забыть кэш перечня (тесты, меняющие каталог или формы)."""
    global _CACHE
    _CACHE = None


#: Раздел пакета, который закроет допущение (ступень L, спецификация
#: будущей ревизии; имя согласуется с CDPM).
HISTORY_SECTION = "history"

_ZONES = {"pairing": ("PAIRING", "BOTH"),
          "rostering": ("ROSTERING", "BOTH")}


def rule_family(rule: dict) -> str | None:
    """Семья механики правила: посадка (A/B) первична, легаси
    export_solver.family — обратная совместимость (тот же порядок, что
    у сертификатора и решателя)."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        return fit.get("family")
    return (rule.get("export_solver") or {}).get("family")


#: Блоки записи сотрудника, хотя бы один из которых обязан нести
#: значение, чтобы запись считалась историей (спецификация 1.17: у
#: сотрудника «Экипажей» без строки листа все блоки null).
HISTORY_BLOCKS = ("month_to_date", "rolling", "last_duty",
                  "rest_before_horizon_min", "series", "location")


def _has_value(v) -> bool:
    """Значение есть — не None, не пустая строка; словарь — если хоть
    одно его значение есть (ревью CDPM 05.09: писатель мог дать словарь
    из одних null — это «без записи», не история)."""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, dict):
        return any(_has_value(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_has_value(x) for x in v)
    return True


def entry_has_history(entry: dict | None) -> bool:
    """Запись раздела несёт историю — хотя бы один блок со значением."""
    if not isinstance(entry, dict):
        return False
    return any(_has_value(entry.get(b)) for b in HISTORY_BLOCKS)


def history_passed(manifest: dict | None) -> bool:
    """История передана — когда манифест несёт раздел history словарём."""
    return isinstance(manifest, dict) \
        and isinstance(manifest.get(HISTORY_SECTION), dict)


def dependent_rules(model: dict | None, task: str) -> list[dict]:
    """Правила зоны задачи, зависящие от истории (по семье). История —
    о СОТРУДНИКАХ; у цепочек пейринга людей нет, поэтому у задачи
    пейринга зависимых правил нет: остатки судятся на ростеринге (проход
    КИ-стоп 05.09.2026: замок остановил и пейринг — лишнее)."""
    if task == "pairing":
        return []
    zones = _ZONES.get(task, ("PAIRING", "ROSTERING", "BOTH"))
    fams = history_families()
    out: list[dict] = []
    for r in (model or {}).get("rules") or ():
        if not isinstance(r, dict):
            continue
        if r.get("kind") not in ("HARD", "SOFT"):
            continue
        if r.get("zone") not in zones:
            continue
        fam = rule_family(r)
        if fam in fams:
            out.append({"rule_id": str(r.get("id")),
                        "title": str(r.get("title") or ""),
                        "family": fam})
    return out


def missing_history(manifest: dict | None, clean_data: dict | None) -> list[str]:
    """Сотрудники листа «Экипажи» очищенных данных без записи в разделе
    history (ревизия 1.17). Раздела нет — пусто (действует заявление о
    непереданной истории, не замок)."""
    if not history_passed(manifest):
        return []
    hist = manifest[HISTORY_SECTION]
    # Спецификация 1.17: сотрудник без строки листа «История» присутствует
    # в employees[] со всеми блоками null — истории у него нет; словарь из
    # одних null — тоже «без записи».
    have = {str(e.get("employee_id")) for e in (hist.get("employees") or [])
            if entry_has_history(e)}
    out: list[str] = []
    for r in crew_rows(clean_data):
        if not isinstance(r, dict):
            continue
        tab = str(r.get("Табельный номер") or "").strip()
        if tab and tab not in have:
            out.append(tab)
    return out


def crew_rows(clean_data: dict | None) -> list[dict]:
    """Строки листа «Экипажи» очищенных данных. Таблицы пакета лежат
    под идентификатором источника (tables[sid][лист]) — первый живой
    пакет 1.17 (H0-КИ, 05.09) показал, что плоский поиск листа молчал и
    замок не срабатывал; принимаются обе раскладки."""
    tables = (clean_data or {}).get("tables") or {}
    if not isinstance(tables, dict):
        return []
    found: list[dict] = []
    direct = tables.get("Экипажи")
    if isinstance(direct, dict) and isinstance(direct.get("rows"), list):
        found = [r for r in direct["rows"] if isinstance(r, dict)]
    else:
        for sid, sheets in tables.items():
            if not isinstance(sheets, dict):
                continue
            sheet = sheets.get("Экипажи")
            if isinstance(sheet, dict) and isinstance(sheet.get("rows"), list):
                found.extend(r for r in sheet["rows"] if isinstance(r, dict))
    return found


def assume_zero(manifest: dict | None) -> bool:
    """Признак производителя assume_zero_when_missing раздела history."""
    return history_passed(manifest) \
        and bool(manifest[HISTORY_SECTION].get("assume_zero_when_missing"))


#: Решения пользователя по неполной истории (ревизия 1.17.2, анкета части
#: 3б, решение владельца 05.09.2026: «недостаточно — предупредить и
#: предложить дополнить либо продолжить по частичным; нет — предложить
#: ввести либо продолжить с нулевыми»): «продолжить» снимает замок
#: словами, «запросить данные» оставляет вопрос открытым — замок стоит.
DECISIONS_CONTINUE = ("continue_partial", "continue_zero")
DECISIONS_OPEN = ("supplement_requested", "enter_requested")


def user_decision(manifest: dict | None) -> dict | None:
    """Решение пользователя раздела history (1.17.2): {question, key,
    decision} либо None — вопроса не было или решения ещё нет."""
    if not history_passed(manifest):
        return None
    ud = manifest[HISTORY_SECTION].get("user_decision")
    if isinstance(ud, dict) and ud.get("decision"):
        return ud
    return None


def coverage_kind(manifest: dict | None) -> str | None:
    """coverage.kind раздела history (1.17.2): full | partial | none."""
    if not history_passed(manifest):
        return None
    cov = manifest[HISTORY_SECTION].get("coverage")
    if isinstance(cov, dict) and cov.get("kind"):
        return str(cov.get("kind"))
    return None


def zero_allowed(manifest: dict | None) -> tuple[bool, str]:
    """Разрешена ли нулевая подстановка у сотрудников без записи и чем:
    (True, «решение пользователя <key>: <decision>») либо (True, «признак
    производителя assume_zero_when_missing») либо (False, причина
    остановки — открытый вопрос анкеты либо отсутствие решения)."""
    dec = user_decision(manifest)
    name = (dec or {}).get("decision")
    question = str((dec or {}).get("question") or "").strip()
    answer = str((dec or {}).get("key") or "").strip()
    # Писатель 1.17.2 кладёт в key ответ анкеты («б»), в question — имя
    # вопроса: словами — «по вопросу Q-… (ответ б)»; совпадают или один
    # пуст — как есть.
    if question and answer and question != answer:
        key = "по вопросу %s (ответ %s)" % (question, answer)
    else:
        key = question or answer or "?"
    if name in DECISIONS_CONTINUE:
        return True, "решение пользователя %s: %s" % (key, name)
    if assume_zero(manifest):
        return True, "признак производителя assume_zero_when_missing"
    if name in DECISIONS_OPEN:
        return False, ("вопрос анкеты %s открыт (%s): данные истории запрошены, "
                       "но не получены" % (key, name))
    if coverage_kind(manifest) == "none":
        return False, ("история отсутствует (coverage none), решения пользователя "
                       "по вопросу анкеты нет")
    return False, ("нулевая подстановка не разрешена производителем и решения "
                   "пользователя нет")


def statement(model: dict | None, manifest: dict | None,
              task: str) -> dict:
    """Заявление для выдачи и сертификата: {"history": {passed, section,
    rules, families, reads, note}}. passed=False и rules непусты —
    допущение «остаток на начало периода нулевой» действует и названо
    поимённо.

    `reads` — блоки ПО ВИДАМ чтения истории (`read_blocks`): в каждом
    семьи вида, их потребности словами записи каталога и допущение
    этого вида. Восемь семей — не восемь допущений: их столько, сколько
    видов встретилось в своде задачи.
    """
    rules = dependent_rules(model, task)
    fams = sorted({r["family"] for r in rules})
    passed = history_passed(manifest)
    blocks = read_blocks(fams, passed=passed)
    # Порядок ветвей: задача пейринга — раньше «передана» (ревью CDPM
    # 05.09: при переданной истории пейринг получал ростеринговую
    # формулировку «правила зоны судятся с учётом остатков»).
    if task == "pairing":
        note = ("история сотрудника относится к людям, у цепочек пейринга "
                "людей нет — остатки на начало периода судятся на ростеринге"
                + (" (раздел history передан)" if passed else ""))
    elif passed:
        note = ("история сотрудника передана разделом пакета «%s» — "
                "правила %s судятся с учётом остатков на начало периода"
                % (HISTORY_SECTION,
                   ", ".join(r["rule_id"] for r in rules) or "зоны"))
    elif rules:
        note = ("история сотрудника (остатки на начало периода: месячные "
                "накопители, последняя смена и отдых, серии) пакетом не "
                "передана — правила %s судятся только внутри горизонта; "
                "читается история по видам: %s; остаток на начало периода "
                "принят нулевым — это допущение, не норма"
                % (", ".join(r["rule_id"] for r in rules),
                   _blocks_words(blocks)))
    else:
        note = ("правил, зависящих от истории сотрудника, в зоне задачи "
                "нет — допущение о нулевом остатке ни на что не влияет")
    assumption = None if passed or not rules else "остаток на начало периода = 0"
    extra: dict = {}
    if passed:
        # Ревизия 1.17.2: покрытие и решение пользователя по неполной
        # истории — в заявление словами; «продолжить» = допущение о нуле у
        # сотрудников без записи, названное с источником решения.
        dec = user_decision(manifest)
        allowed, why = zero_allowed(manifest)
        kind = coverage_kind(manifest)
        extra = {"coverage_kind": kind,
                 "user_decision": ({"question": dec.get("question"),
                                    "key": dec.get("key") or dec.get("question"),
                                    "decision": dec.get("decision")} if dec else None),
                 "zero_allowed": allowed,
                 "zero_allowed_by": why if allowed else None}
        if task != "pairing" and rules and (dec or kind in ("partial", "none")):
            note += "; покрытие истории: %s" % (kind or "не указано")
            if allowed and dec and dec.get("decision") in DECISIONS_CONTINUE:
                note += ("; %s — у сотрудников без записи остаток на начало "
                         "периода принят нулевым (допущение, не норма)" % why)
                assumption = ("остаток на начало периода = 0 у сотрудников без "
                              "записи (%s)" % why)
            elif not allowed:
                note += "; " + why
    return {"history": {"passed": passed,
                        "section": HISTORY_SECTION if passed else None,
                        "task": task,
                        "rules": rules, "families": fams,
                        "reads": blocks,
                        "assumption": assumption,
                        "note": note, **extra}}
