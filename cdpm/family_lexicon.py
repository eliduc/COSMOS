# -*- coding: utf-8 -*-
"""Лексический контракт семьи шаблонов — слой 1 против самосогласованного
неверного чтения (план «прямой ход» 2.7, решение владельца 02.09).

У каждой семьи базы есть ПРЕДМЕТ (отдых, смена, стыковка, ночёвка, резерв,
состав экипажа, шкала времени, календарное окно, перегон, накопители,
исключения флота). Посадка A/B правила на семью законна, только если
клауза-источник (или формулировка строки) содержит слово-носитель предмета
этой семьи. Иначе «12:00» из клаузы про отдых нельзя положить в предел
смены: код видит, что в клаузе нет слов класса «смена».

Источник истины — КАТАЛОГ ШАБЛОНОВ (поля `subject_words` и
`complements` записи семьи, порядок перебора — словарь
`lexicon_order`) через мост `cdpm.catalog_bridge`. Со стадии 1б шага 7
переезда своих перечней у модуля НЕТ: без каталога — отказ словами
(`CatalogRequired`), а не молчаливый лексикон из кода.

    lexicon_families() -> tuple[str, ...]              # порядок перебора
    family_subject_words(family) -> tuple[str, ...]    # основы слов
    subject_hit(family, text) -> str | None            # найденная основа
"""
from __future__ import annotations

import re


def lexicon_families() -> tuple:
    """Порядок перебора семей лексиконом — словарь каталога
    `lexicon_order` (прежде им был порядок вставки перечня кода).
    Порядок значим: `subject_conflict` называет ПЕРВУЮ чужую семью, чьё
    слово нашлось в тексте, и текст проблемы обязан быть один и тот же
    у двух прогонов на одном каталоге."""
    from cdpm import catalog_bridge as _bridge
    return _bridge.lexicon_order()


def complementary_families(fam_a: str, fam_b: str) -> bool:
    """Дополняют ли семьи друг друга (предел ↔ продление: мягкое
    превышение того же предмета). Строки таких семей друг другу не
    противоречат. Источник — записи каталога (`complements` семьи);
    отношение симметрично, даже если запись назвала одну сторону."""
    if not fam_a or not fam_b or fam_a == fam_b:
        return False
    from cdpm import catalog_bridge as _bridge
    comps = _bridge.complements()
    return (fam_b in (comps.get(fam_a) or ())
            or fam_a in (comps.get(fam_b) or ()))


def family_subject_words(family: str) -> tuple:
    """Слова-носители предмета семьи из каталога шаблонов (у семьи без
    слов и у неизвестной — пустой кортеж: лексический контракт семью не
    судит); без каталога — отказ словами.

    Основы в каталоге СПЕЦИФИЧНЫ для предмета семьи: общие слова
    («смена», «время», «рабочий») намеренно исключены — они встречаются
    в клаузах соседних предметов («отдых между сменами») и не различают
    семьи. Живой урок набора КН0-ДЕМО (05.09.2026): у `duty_limits`
    носителем служит ФРАЗА «длительность/продолжительность пейринга» в
    обеих падежных формах, а одно слово «пейринг» — нарочно нет: оно
    есть и в клаузах об отдыхе между пейрингами, и в клаузах о днях
    недели вылета."""
    from cdpm import catalog_bridge as _bridge
    return _bridge.subject_words(family)


def subject_hit(family: str, text: str) -> str | None:
    """Основа слова-носителя предмета семьи, найденная в тексте; None —
    ни одной (посадка без слова-носителя)."""
    low = " ".join(str(text or "").lower().split())
    for stem in family_subject_words(family):
        if re.search(r"(?<![а-яёa-z])" + re.escape(stem), low):
            return stem
    return None


def subject_conflict(family: str, text: str):
    """Семья, чьё слово-носитель есть в тексте, когда слова семьи
    `family` в нём нет: (чужая_семья, основа) либо None. Клауза без
    слов-носителей вовсе (абстрактный или синтетический текст) конфликта
    не даёт — судят другие слои; конфликт есть только при положительном
    свидетельстве иного предмета («отдых» в клаузе, посаженной на предел
    смены). Перебор идёт порядком каталога (`lexicon_order`)."""
    if subject_hit(family, text) is not None:
        return None
    for other in lexicon_families():
        if other == family:
            continue
        stem = subject_hit(other, text)
        if stem is not None:
            return other, stem
    return None


def lexicon_problems(doc: dict, clause_text_by_key: dict | None = None) -> list:
    """Нарушения лексического контракта: правило с посадкой A/B на семью,
    в клаузах-источниках (либо формулировке, когда текстов клауз нет)
    которого нет ни одного слова-носителя предмета семьи."""
    problems: list = []
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
            continue
        fam = fit.get("family")
        if not fam or not family_subject_words(fam):
            continue
        texts = []
        if clause_text_by_key:
            for s in r.get("sources") or []:
                if isinstance(s, dict):
                    texts.append(clause_text_by_key.get((s.get("sid"), s.get("anchor")), ""))
        haystack = " ".join(t for t in texts if t) or str(r.get("statement") or "")
        conflict = subject_conflict(fam, haystack)
        if conflict is not None:
            problems.append(f"{r.get('id')}: посадка {fit['category']} на семью "
                            f"{fam!r}, а клауза несёт предмет семьи "
                            f"{conflict[0]!r} («{conflict[1]}») и ни одного "
                            f"слова-носителя {fam!r} — спор о чтении, не посадка")
    return problems
