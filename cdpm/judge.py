# -*- coding: utf-8 -*-
"""Судья меры успеха CDPM (пакет работ П-4, протокол — постановка §8).

ВНУТРЕННИЙ ИНСТРУМЕНТ РАЗРАБОТКИ (решение владельца 2026-08-18): из
продукта сравнение с эталоном убрано — у пользователя CDPM эталона нет
по определению задачи. Модуль остаётся для измерения качества
извлечения на выверенных наборах (пилотная линия, UniA) — только командная
строка, в веб-интерфейс и API не выведен. Третья модель в продукте —
АРБИТР тупиков аудит-цикла (cdpm.exchange.run_arbiter).

Машинное сравнение свода, собранного CDPM, с выверенным эталонным
сводом (формат свода v1). Контракт протокола:

- **единица счёта** — строка правила эталонного свода (Часть 1);
- **вердикты по строке** — «найдено» / «найдено частично» /
  «не найдено»; спорные пары судья НЕ решает сам — предъявляет
  владельцу отдельным списком;
- **метрика 2** — доля вопросов Ч2 эталона, которые CDPM переоткрыл;
- **метрика 3** — «изобретённое»: правила CDPM без соответствия в
  эталоне, чью формулировку не подтверждает клауза-источник;
- **порогов «годен/не годен» судья не выносит** — их фиксирует владелец
  по первому фактическому прогону; отчёт содержит только числа.

Сопоставление — тремя ступенями: якорная (общие номера пунктов
источника), лексическая (пересечение словарей — короткий список
кандидатов), содержательная (языковая модель выносит вердикт по
кандидатам; сомнение — «спорно», не выбор). Строка эталона без
кандидатов получает «не найдено» без обращения к модели.

Запуск отдельно от приложения:
    python -m cdpm.judge ЭТАЛОН.md КАТАЛОГ_ИЗВЛЕЧЕНИЯ \
        --kit КОМПЛЕКТ_ЗАМОРОЗКИ --out-dir КАТАЛОГ [--scope any]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cdpm import extract as extract_mod
from cdpm import llm as llm_mod
from cdpm import model as m

VERDICTS = ("найдено", "найдено частично", "не найдено")
SIM_SHORTLIST = 0.18     # порог близости Жаккара по формулировке
COVER_SHORTLIST = 0.4    # порог покрытия слов эталона клаузой-источником
TOP_CANDIDATES = 3
# Итерация 3: профиль чисел и кодов. Токен «редкий», если встречается
# не более чем в RARE_DF правилах свода; один общий редкий токен
# («ZZ252», «10:30», «80») — сильный кандидатный сигнал, PROF_MULTI
# общих токенов любой частоты — умеренный.
RARE_DF = 5
PROF_MULTI = 3
# Пакетные запросы: на настоящем эталоне (225 строк) построчные
# обращения дают сотни вызовов канала; пакет строк за одно обращение
# сокращает их на порядок, контракт вердиктов не меняется.
VERDICT_BATCH = 20
QUESTION_BATCH = 10
INVENTED_BATCH = 15


class JudgeError(ValueError):
    """Честный отказ судьи: причина всегда названа."""


# ---------------------------------------------------------------------------
# Разбор эталонного свода (формат свода v1)
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(
    r"^#{3,5}\s*(?:⚠️\s*)?ВОПРОС\s+([\w\.\-]+)\s*:\s*(.+)$")
_SUB_RE = re.compile(r"\(([а-я])\)")
_ANCHOR_RE = re.compile(r"[ПпРр]\.\s?(\d+(?:\.\d+)*)")
# Ссылка эталона на запись журнала ОПЭ — номер записи + необязательный
# лист; вымышленные примеры: «Е1 (П.7/Л1)», «Е1 (П.9-A/Л2)», «Е1 (П.4-B)».
_E1_REF_RE = re.compile(
    r"Е1\s*\(\s*П\.\s?(\d+(?:\.\d+)*)(?:[-\w]*)?\s*(?:/\s*Л(\d))?\s*\)")
# Номер записи в начале текста строки журнала (первая ячейка, возможно
# жирная): «П.5 | Телепортация…», «**П.9** | Уточнение…» — журнал
# нумерует записи «П.x», не голым числом (проверено на живых клаузах).
_E1_ROW_NUM_RE = re.compile(r"^\**\s*[Пп]\.\s?(\d+(?:\.\d+)*)\s*\**\s*\|")
_SHEET_NUM_RE = re.compile(r"(\d)\s*этап")


def parse_etalon(text: str) -> dict:
    """Строки Части 1 (таблица | ID | Правило | Этап | Тип | Источник |
    Примечание |) и вопросы Ч2 (заголовки «ВОПРОС N: …» с подпунктами
    (а)(б)…). Разбор терпим к married-тексту вокруг — эталон пилотной
    линии содержит и приложения, и ответы."""
    rules = []
    questions = []
    current_q = None
    in_part1 = False
    seen_ids: dict[str, int] = {}
    for line in text.splitlines():
        if re.match(r"^##[^#]", line):
            # Единица счёта — строка Части 1: таблицы части 0,
            # приложений и ответов в счёт не входят (заголовки того же
            # уровня закрывают зону).
            in_part1 = bool(re.match(r"^##[^#]*ЧАСТЬ\s*1\b", line))
        qm = _QUESTION_RE.match(line.strip())
        if qm:
            current_q = {"qid": qm.group(1), "title": qm.group(2).strip(),
                         "subs": set()}
            questions.append(current_q)
            continue
        if line.startswith("#"):
            current_q = None
        if current_q is not None:
            current_q["subs"].update(_SUB_RE.findall(line))
        if not line.startswith("|") or not in_part1:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in ("ID", "") or \
                set(cells[0]) <= {"-", " ", ":"}:
            continue
        rid, body, zone, kind, source, note = cells[:6]
        if kind not in m.KINDS and "/" not in kind:
            continue                      # чужая таблица того же вида
        # Ячейка ID эталона бывает склеена с хвостом разметки
        # (`OP-I1-ADD-41<br>source=[…]`) — модель отвечает ЧИСТЫМ
        # идентификатором, и пакет вердиктов не сходился (живой прогон
        # пилотной линии). Идентификатор — до первого <br> или обратной
        # кавычки;
        # дубль после очистки получает суффикс, чтобы не терять строку.
        rid = re.split(r"<br\s*/?>|`", rid)[0].strip().strip("*").strip()
        n = seen_ids.get(rid, 0) + 1
        seen_ids[rid] = n
        if n > 1:
            rid = f"{rid} ({n})"
        joined = " ".join((rid, body, source, note))
        rules.append({"id": rid, "text": body, "zone": zone,
                      "kind": kind, "source": source, "note": note,
                      "anchors": sorted(set(_ANCHOR_RE.findall(joined))),
                      "e1": etalon_e1_keys(joined)})
    for q in questions:
        q["subs"] = sorted(q["subs"])
    return {"rules": rules, "questions": questions}


# ---------------------------------------------------------------------------
# Кандидаты: якоря и лексика
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}", re.I)
# Числа (10, 4.5, 08:00) и коды (ZZ252, BSE) — «не менее 10 часов» и
# «rest_home ≥ 10 ч» почти не пересекаются словами, но числом — точно.
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,:]\d+)?")
_CODE_TOKEN_RE = re.compile(r"\b[A-Z]{2}\d{2,4}\b|\b[A-Z]{3}\b")
_STOP = {"или", "либо", "для", "при", "как", "что", "это", "все",
         "быть", "может", "должен", "должна", "должно", "более",
         "менее", "the", "and", "not"}


def _bag(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)} - _STOP


def _profile(text: str) -> set[str]:
    """Профиль чисел и кодов текста (итерация 3 меры успеха)."""
    toks = {t.replace(",", ".") for t in _NUM_TOKEN_RE.findall(text)}
    toks |= set(_CODE_TOKEN_RE.findall(text))
    return toks


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def etalon_e1_keys(text: str) -> set[str]:
    """Ключи записей журнала из текста эталона: «е1:22/1» (лист известен)
    и «е1:22» (обезличенно — записи без листа существуют на одном)."""
    keys: set[str] = set()
    for num, sheet in _E1_REF_RE.findall(text):
        keys.add(f"е1:{num}")
        if sheet:
            keys.add(f"е1:{num}/{sheet}")
    return keys


def rule_e1_keys(rule: dict, clause_by_key: dict) -> set[str]:
    """Ключи записей журнала для правила CDPM: номер записи — из ПЕРВОЙ
    ячейки текста клаузы (итерация 2 меры успеха: эталон адресует запись
    номером записи, заморозка xlsx — номером строки листа; якорная
    ступень для Е1 не работала вовсе, 70 строк эталона остались без
    кандидатов)."""
    keys: set[str] = set()
    for ref in rule.get("sources", []):
        anchor = ref.get("anchor", "")
        if "строка" not in anchor:
            continue
        clause = clause_by_key.get((ref.get("sid"), anchor)) or ""
        m = _E1_ROW_NUM_RE.match(clause)
        if not m:
            continue
        keys.add(f"е1:{m.group(1)}")
        sheet = _SHEET_NUM_RE.search(anchor)
        if sheet:
            keys.add(f"е1:{m.group(1)}/{sheet.group(1)}")
    return keys


def _cdpm_anchors(rule: dict) -> set[str]:
    out = set()
    for ref in rule.get("sources", []):
        out.update(_ANCHOR_RE.findall(ref.get("anchor", "")))
    return out


def build_rule_index(cdpm_rules: list[dict],
                     clause_by_key: dict) -> list[dict]:
    """Предвычисленный индекс кандидатов: якоря, ключи записей журнала и
    лексический словарь. Словарь включает ТЕКСТ КЛАУЗЫ-ИСТОЧНИКА
    (итерация 2): эталон часто цитирует источник дословно, а короткая
    формальная запись правила эти слова теряла."""
    index = []
    for r in cdpm_rules:
        clause_words: set[str] = set()
        for ref in r.get("sources", []):
            clause_words |= _bag(
                clause_by_key.get((ref.get("sid"), ref.get("anchor")))
                or "")
        full_text = r["title"] + " " + r["statement"]
        clause_text = " ".join(
            clause_by_key.get((ref.get("sid"), ref.get("anchor"))) or ""
            for ref in r.get("sources", []))
        index.append({
            "rule": r,
            "anchors": _cdpm_anchors(r),
            "e1": rule_e1_keys(r, clause_by_key),
            "stmt_bag": _bag(full_text),
            "full_bag": _bag(full_text) | clause_words,
            "prof": _profile(full_text + " " + clause_text),
        })
    # Частота токенов профиля по своду: редкий токен — сильная метка,
    # вездесущий («1», «MAX») — шум; редкость считается по факту.
    df: dict[str, int] = {}
    for entry in index:
        for t in entry["prof"]:
            df[t] = df.get(t, 0) + 1
    for entry in index:
        entry["rare_prof"] = {t for t in entry["prof"]
                              if df[t] <= RARE_DF}
    return index


def shortlist(et_rule: dict, rule_index: list[dict]) -> list[dict]:
    """Кандидаты строки эталона: совпадение номеров пунктов источника
    ИЛИ ключей записей журнала, затем лексика ДВУМЯ мерами: близость
    Жаккара по формулировке правила и покрытие слов эталона словами
    клаузы-источника. Меры разделены сознательно: слитый словарь
    разбавлял Жаккара и РОНЯЛ покрытие кандидатами (сухой прогон
    итерации 2: 43 строки с кандидатами против 55 у итерации 1)."""
    et_anchors = set(et_rule["anchors"])
    et_e1 = et_rule.get("e1", set())
    et_bag = _bag(et_rule["text"])
    et_prof = _profile(et_rule["text"])
    scored = []
    for entry in rule_index:
        anchor_hit = bool(et_anchors & entry["anchors"]) or \
            bool(et_e1 & entry["e1"])
        jac = _similarity(et_bag, entry["stmt_bag"])
        cover = (len(et_bag & entry["full_bag"]) / len(et_bag)
                 if et_bag else 0.0)
        # Итерация 3: общий РЕДКИЙ токен профиля (номер рейса, лимит
        # часов) — сильный сигнал; несколько общих любой частоты —
        # умеренный.
        rare_hit = bool(et_prof & entry.get("rare_prof", set()))
        multi_hit = len(et_prof & entry["prof"]) >= PROF_MULTI
        score = max(jac, cover / 2,
                    0.3 if rare_hit else 0.0,
                    0.2 if multi_hit else 0.0)
        if anchor_hit or jac >= SIM_SHORTLIST or \
                cover >= COVER_SHORTLIST or rare_hit or multi_hit:
            scored.append((anchor_hit, score, entry["rule"]))
    scored.sort(key=lambda t: (not t[0], -t[1]))
    return [r for _, _, r in scored[:TOP_CANDIDATES]]


# ---------------------------------------------------------------------------
# Содержательные вердикты (языковая модель)
# ---------------------------------------------------------------------------

_PROMPT_VERDICT = """Ты — судья сопоставления сводов правил
авиакомпании. Ниже — строки ЭТАЛОННОГО свода, у каждой — кандидаты из
свода, собранного автоматически. Для КАЖДОЙ строки вынеси вердикт.
Верни СТРОГО один JSON-объект:

{{"verdicts": [{{
  "etalon": "<id строки эталона, ровно как в списке>",
  "verdict": "найдено" | "найдено частично" | "не найдено" | "спорно",
  "candidate": "<id выбранного кандидата или null>",
  "basis": "<одно предложение: основание вердикта>"
}}, …]}}

«найдено» — кандидат выражает то же правило (та же норма, те же
границы); «найдено частично» — правило узнаваемо, но потеряны условия,
границы или зона; «не найдено» — ни один кандидат не о том;
«спорно» — уверенного ответа нет: НЕ выбирай наугад. Ровно одна запись
на каждую строку эталона; пропуски недопустимы.

{blocks}""" + m.ID_IMMUTABILITY_RULE

_PROMPT_QUESTION = """Ты — судья сопоставления анкет. Ниже — вопросы
ЭТАЛОННОЙ анкеты, у каждого — вопросы-кандидаты автоматического свода.
Для КАЖДОГО вопроса эталона вынеси вердикт. Верни СТРОГО один
JSON-объект:

{{"verdicts": [{{
  "etalon": "<id вопроса эталона, ровно как в списке>",
  "verdict": "переоткрыт" | "не переоткрыт" | "спорно",
  "candidate": "<id вопроса-кандидата или null>",
  "basis": "<одно предложение>"
}}, …]}}

«переоткрыт» — автоматика сама подняла тот же предмет вопроса (пусть
другими словами); сомнение — «спорно», не выбор. Ровно одна запись на
каждый вопрос эталона.

{blocks}""" + m.ID_IMMUTABILITY_RULE

_PROMPT_INVENTED = """Ты — проверяющий трассировку. Каждое правило ниже
собрано автоматикой из названной клаузы источника. Для КАЖДОГО правила
ответь: подтверждает ли ТЕКСТ КЛАУЗЫ это правило? Верни СТРОГО один
JSON-объект:

{{"verdicts": [{{
  "etalon": "<id правила, ровно как в списке>",
  "verdict": "подтверждено" | "изобретено" | "спорно",
  "basis": "<одно предложение>"
}}, …]}}

«изобретено» — в клаузе такой нормы нет (правило додумано);
сомнение — «спорно». Ровно одна запись на каждое правило.

{blocks}""" + m.ID_IMMUTABILITY_RULE


def batch_verdicts(llm, template: str, blocks: dict[str, str],
                   allowed: tuple[str, ...],
                   needs_candidate: tuple[str, ...] = (),
                   candidate_sets: dict[str, set] | None = None,
                   id_field: str = "etalon"
                   ) -> tuple[dict[str, dict], dict[str, str]]:
    """Пакет решений: один запрос на пакет блоков; ответ обязан
    покрыть каждый идентификатор РОВНО ОДИН раз, а вердикты из
    `needs_candidate` — назвать кандидата из короткого списка строки.
    Возвращает пару: принятые решения `{id: запись}` и неразрешённое
    `{id: причина словами}`.

    Невалидный ответ получает одну повторную попытку с перечнем бед.
    После повтора (A09, контракт П0 §1.3 и §12.2 — «любой дубль после
    повтора не решение»):

    - ЛЮБОЙ повтор ожидаемого идентификатора — и противоречивые
      решения, и два одинаковых — остаётся неразрешённым: дедупликации
      одинаковых нет, «первое из двух» решением не становится
      (аудит «четыре принципа ВМП», CONS-04: арбитр принимал первое из
      пары «обработчик прав» / «аудитор прав», и неоднозначность
      исчезала из записи до проверки уникальности);
    - идентификатор БЕЗ ответа — неразрешён с причиной «нет решения»
      (fail-closed: молчание модели не успех);
    - НЕИЗВЕСТНЫЙ (лишний) идентификатор не скрывается — попадает в
      тот же перечень со своей причиной, потребитель обязан его
      показать (у арбитража — строка отчёта, красный гейт); туда же
      идут записи БЕЗ формы (не объект) и БЕЗ идентификатора — их
      называют местом в ответе («запись #N …»), потому что назвать
      больше нечем;
    - однозначные решения ОСТАЛЬНЫХ идентификаторов сохраняются;
    - ответ С НЕДОПУСТИМЫМ вердиктом, БЕЗ ОСНОВАНИЯ словами или с
      недопустимым кандидатом — по-прежнему запасное «спорно»
      владельцу (это неуверенность модели, а не структурная
      неоднозначность; ключ записи — по `id_field`), и такой вердикт
      вне перечня ловится гейтом-потребителем.
    """
    prompt = template.format(blocks="\n\n".join(blocks.values()))
    why = ""
    out: dict[str, dict] = {}
    invalid: dict[str, str] = {}
    repeats: dict[str, list[dict]] = {}
    unknown: dict[str, str] = {}
    for attempt in (1, 2):
        ask = prompt if attempt == 1 else (
            prompt + f"\n\nПредыдущий ответ был неполон или невалиден "
                     f"({why}). Верни полный список заново, СТРОГО один "
                     f"JSON-объект; кавычки внутри строк экранируй.")
        try:
            got = llm_mod.parse_json_reply(llm(ask))
        except llm_mod.LLMReplyError as e:
            # Битый JSON — повод для повтора, не для смерти прогона
            # (живой прогон пилотной линии: неэкранированная кавычка
            # в basis). Разобранное прежней попыткой не стирается:
            # однозначные решения сохраняются, недостающее уйдёт в
            # неразрешённое.
            why = f"ответ не разобрался как JSON: {str(e)[:150]}"
            continue
        out, invalid, repeats, unknown = {}, {}, {}, {}
        bad = []
        # Сперва собираем ВСЕ записи по каждому ожидаемому
        # идентификатору: повтор виден только по их числу, а судить
        # первую запись до того, как известно о второй, и значит
        # «принять первое из двух».
        seen: dict[str, list[dict]] = {}
        for n, e in enumerate(got.get("verdicts") or [], 1):
            # Ответ модели — недоверенный вывод: элемент-не-объект
            # ронял `.get` и весь прогон. Запись без формы решением не
            # становится и не исчезает: идентификатора у неё нет,
            # поэтому её называют местом в ответе — потребитель
            # обязан показать её так же, как лишний идентификатор.
            if not isinstance(e, dict):
                unknown[f"<запись #{n} не объект>"] = (
                    f"запись #{n} ответа не объект: {repr(e)[:60]} — "
                    f"решением не является")
                bad.append(f"запись #{n} не объект")
                continue
            eid = e.get(id_field)
            if not isinstance(eid, str) or not eid.strip():
                unknown[f"<запись #{n} без идентификатора>"] = (
                    f"запись #{n}: идентификатор {eid!r} не назван — "
                    f"решение не относится ни к одному из "
                    f"предъявленных споров")
                bad.append(f"запись #{n} без идентификатора")
                continue
            if eid not in blocks:
                unknown[str(eid)] = (
                    f"неизвестный идентификатор {eid!r}: решение не "
                    f"относится ни к одному из предъявленных споров")
                bad.append(f"неизвестный id {eid!r}")
                continue
            seen.setdefault(eid, []).append(e)
        for eid, entries in seen.items():
            if len(entries) > 1:
                repeats[eid] = entries
                bad.append(f"повторный id {eid!r} (записей: "
                           f"{len(entries)})")
                continue
            e = entries[0]
            if e.get("verdict") not in allowed:
                bad.append(f"{eid}: вердикт {e.get('verdict')!r} вне "
                           f"перечня")
                invalid[eid] = f"вердикт {e.get('verdict')!r} вне перечня"
                continue
            # Идентичность решения — (идентификатор, вердикт,
            # основание): решение без основания неполно, и принять его
            # значило бы закрыть спор молча. Тот же исход, что у
            # недопустимого вердикта, — «спорно» владельцу.
            #
            # Основание — СЛОВА, и только они (ревью A09, находка 8):
            # `str(...)` принимал любой объект — число, список, словарь
            # — и «основанием» становилось `[1, 2]`; потребитель (гейт,
            # панель, отчёт владельцу) показал бы это как объяснение
            # решения. Тип судится явно, до всякого приведения.
            basis = e.get("basis")
            if not isinstance(basis, str) or not basis.strip():
                bad.append(f"{eid}: вердикт {e.get('verdict')!r} без "
                           f"основания словами "
                           f"({type(basis).__name__})")
                invalid[eid] = (f"вердикт {e.get('verdict')!r} "
                                f"назван без основания словами "
                                f"(основание: {type(basis).__name__})")
                continue
            if candidate_sets is not None and \
                    e.get("verdict") in needs_candidate and \
                    e.get("candidate") not in candidate_sets.get(eid,
                                                                 set()):
                bad.append(f"{eid}: вердикт {e.get('verdict')!r} без "
                           f"допустимого кандидата из короткого списка")
                invalid[eid] = (f"модель дала вердикт "
                                f"{e.get('verdict')!r}, но не назвала "
                                f"допустимого кандидата")
                continue
            out[eid] = e
        missing = sorted(set(blocks) - set(out) - set(invalid)
                         - set(repeats))
        if not bad and not missing:
            return out, {}
        why = "; ".join(bad + [f"нет ответа по {mid}" for mid in missing]
                        )[:400]
    unresolved: dict[str, str] = {}
    for eid, entries in repeats.items():
        words = [str(e.get("verdict")) for e in entries]
        if len(set(words)) == 1:
            unresolved[eid] = (
                f"повторные решения по {eid}: вердикт «{words[0]}» "
                f"назван более одного раза (записей: {len(words)}) — "
                f"после повторного запроса дубль решением не "
                f"становится")
        else:
            unresolved[eid] = (
                f"противоречивые решения по {eid}: "
                + ", ".join(f"«{w}»" for w in words)
                + " — первое из них решением не становится")
    for eid in sorted(set(blocks) - set(out) - set(invalid)
                      - set(repeats)):
        unresolved[eid] = ("нет решения: идентификатор не назван ни в "
                           "ответе, ни в повторе")
    unresolved.update(unknown)
    for eid, reason in invalid.items():
        out[eid] = {id_field: eid, "verdict": "спорно",
                    "candidate": None,
                    "basis": f"{reason} — приведено как спорное, "
                             f"решение за владельцем"}
    return out, unresolved


def _batch_verdicts(llm, template: str, blocks: dict[str, str],
                    allowed: tuple[str, ...],
                    needs_candidate: tuple[str, ...] = (),
                    candidate_sets: dict[str, set] | None = None,
                    id_field: str = "etalon"
                    ) -> dict[str, dict]:
    """Прежнее имя для потребителей, которым неразрешённое решение
    предъявить некуда (внутренний судья разработки — его отчёт судит
    каждую строку эталона): ЛЮБОЕ неразрешённое — честный отказ с
    перечнем идентификаторов и причинами, никогда не «первое из
    двух»."""
    out, unresolved = batch_verdicts(
        llm, template, blocks, allowed, needs_candidate=needs_candidate,
        candidate_sets=candidate_sets, id_field=id_field)
    if unresolved:
        raise JudgeError(
            "пакет вердиктов не сошёлся после повтора: "
            + "; ".join(f"{eid} — {reason}"
                        for eid, reason in sorted(unresolved.items())))
    return out


# ---------------------------------------------------------------------------
# Прогон судьи
# ---------------------------------------------------------------------------

def run_judge(etalon_path: Path, extract_dir: Path, kit_dir: Path,
              out_dir: Path, llm: llm_mod.LLMFn) -> dict:
    etalon = parse_etalon(etalon_path.read_text(encoding="utf-8"))
    if not etalon["rules"]:
        raise JudgeError(f"в эталоне {etalon_path} не разобрано ни одной "
                         f"строки Части 1 — формат не тот")
    doc = m.load_model(extract_dir / "model.json")
    cdpm_rules = doc.get("rules", [])
    clause_by_key: dict[tuple[str, str], str] = {}
    for entry in extract_mod._load_kit(kit_dir):
        for c in entry["clauses"]:
            clause_by_key[(entry["sid"], c["anchor"])] = c["text"]
    rule_index = build_rule_index(cdpm_rules, clause_by_key)

    with_cands = []
    cands_by_id: dict[str, list[dict]] = {}
    verdict_by_id: dict[str, dict] = {}
    for et in etalon["rules"]:
        cands = shortlist(et, rule_index)
        if cands:
            with_cands.append(et)
            cands_by_id[et["id"]] = cands
    for i in range(0, len(with_cands), VERDICT_BATCH):
        batch = with_cands[i:i + VERDICT_BATCH]
        blocks = {}
        for et in batch:
            listing = "\n".join(
                f"{r['id']}: {r['title']} — {r['statement']}"
                for r in cands_by_id[et["id"]])
            blocks[et["id"]] = (f"=== Строка эталона [{et['id']}]: "
                                f"{et['text']}\nКандидаты:\n{listing}")
        verdict_by_id.update(_batch_verdicts(
            llm, _PROMPT_VERDICT, blocks, VERDICTS + ("спорно",),
            needs_candidate=("найдено", "найдено частично"),
            candidate_sets={et["id"]: {r["id"]
                                       for r in cands_by_id[et["id"]]}
                            for et in batch}))

    rows = []
    disputed = []
    matched_ids: set[str] = set()
    for et in etalon["rules"]:
        if et["id"] not in cands_by_id:
            rows.append({"etalon": et["id"], "verdict": "не найдено",
                         "candidate": None,
                         "basis": "кандидатов не нашлось ни по якорям, "
                                  "ни по лексике"})
            continue
        got = verdict_by_id[et["id"]]
        if got["verdict"] == "спорно":
            # §8: спорные пары судья не решает — строка остаётся БЕЗ
            # вердикта и предъявляется владельцу списком.
            disputed.append({"etalon": et["id"],
                             "candidates": [r["id"] for r in
                                            cands_by_id[et["id"]]],
                             "basis": got.get("basis", "")})
            rows.append({"etalon": et["id"], "verdict": "спорно",
                         "candidate": None,
                         "basis": got.get("basis", "")})
            continue
        cand = got.get("candidate")
        if got["verdict"] != "не найдено":
            # допустимость кандидата гарантирована пакетной проверкой
            matched_ids.add(cand)
        rows.append({"etalon": et["id"], "verdict": got["verdict"],
                     "candidate": cand if got["verdict"] != "не найдено"
                     else None,
                     "basis": got.get("basis", "")})

    # Метрика 2: переоткрытые вопросы Ч2 эталона.
    q_rows = []
    cdpm_questions = doc.get("questions", [])
    q_with_cands = []
    q_cands_by_id: dict[str, list[dict]] = {}
    for et_q in etalon["questions"]:
        q_bag = _bag(et_q["title"])
        scored = sorted(
            ((_similarity(q_bag, _bag(q["title"] + " " + q["text"])), q)
             for q in cdpm_questions), key=lambda t: -t[0])
        cands = [q for s, q in scored[:TOP_CANDIDATES] if s > 0]
        if cands:
            q_with_cands.append(et_q)
            q_cands_by_id[et_q["qid"]] = cands
    q_verdicts: dict[str, dict] = {}
    for i in range(0, len(q_with_cands), QUESTION_BATCH):
        batch = q_with_cands[i:i + QUESTION_BATCH]
        blocks = {}
        for et_q in batch:
            listing = "\n".join(
                f"{q['id']}: {q['title']} — {q['text']}"
                for q in q_cands_by_id[et_q["qid"]])
            blocks[et_q["qid"]] = (f"=== Вопрос эталона "
                                   f"[{et_q['qid']}]: {et_q['title']}\n"
                                   f"Вопросы автоматики:\n{listing}")
        q_verdicts.update(_batch_verdicts(
            llm, _PROMPT_QUESTION, blocks,
            ("переоткрыт", "не переоткрыт", "спорно"),
            needs_candidate=("переоткрыт",),
            candidate_sets={et_q["qid"]: {q["id"] for q in
                                          q_cands_by_id[et_q["qid"]]}
                            for et_q in batch}))
    for et_q in etalon["questions"]:
        if et_q["qid"] not in q_cands_by_id:
            q_rows.append({"etalon": et_q["qid"],
                           "verdict": "не переоткрыт", "candidate": None,
                           "subs": len(et_q["subs"]),
                           "basis": "похожих вопросов нет"})
            continue
        got = q_verdicts[et_q["qid"]]
        q_rows.append({"etalon": et_q["qid"], "verdict": got["verdict"],
                       "candidate": got.get("candidate"),
                       "subs": len(et_q["subs"]),
                       "basis": got.get("basis", "")})

    # Метрика 3: изобретённое среди несопоставленных правил CDPM —
    # подтверждает ли клауза-источник формулировку.
    invented = []
    unmatched = []
    for r in cdpm_rules:
        if r["id"] in matched_ids:
            continue
        ref = r["sources"][0]
        clause = clause_by_key.get((ref["sid"], ref["anchor"]))
        if clause is None:
            invented.append({"rule": r["id"], "verdict": "изобретено",
                             "basis": "клауза-источник не найдена в "
                                      "комплекте заморозки"})
        else:
            unmatched.append((r, ref, clause))
    inv_verdicts: dict[str, dict] = {}
    for i in range(0, len(unmatched), INVENTED_BATCH):
        batch = unmatched[i:i + INVENTED_BATCH]
        blocks = {}
        for r, ref, clause in batch:
            blocks[r["id"]] = (f"=== Правило [{r['id']}]: "
                               f"{r['statement']}\nКлауза источника "
                               f"[{ref['anchor']}]: {clause}")
        inv_verdicts.update(_batch_verdicts(
            llm, _PROMPT_INVENTED, blocks,
            ("подтверждено", "изобретено", "спорно")))
    for r, ref, clause in unmatched:
        got = inv_verdicts[r["id"]]
        if got["verdict"] != "подтверждено":
            invented.append({"rule": r["id"], "verdict": got["verdict"],
                             "basis": got.get("basis", "")})

    by_verdict = {v: sum(1 for r in rows if r["verdict"] == v)
                  for v in VERDICTS}
    reopened = sum(1 for q in q_rows if q["verdict"] == "переоткрыт")
    report = {
        "etalon": {"file": etalon_path.name,
                   "rules": len(etalon["rules"]),
                   "questions": len(etalon["questions"]),
                   "sub_items": sum(len(q["subs"])
                                    for q in etalon["questions"])},
        "cdpm": {"rules": len(cdpm_rules),
                 "questions": len(cdpm_questions)},
        "rows": rows,
        "by_verdict": by_verdict,
        "disputed": disputed,
        "questions": q_rows,
        "reopened": reopened,
        "invented": invented,
        "note": "пороги «годен/не годен» фиксирует владелец по первому "
                "фактическому прогону — судья порогов не выносит",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "judge-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (out_dir / "judge-report.md").write_text(_render_report(report),
                                             encoding="utf-8")
    return report


def _render_report(rep: dict) -> str:
    out = ["# Отчёт судьи меры успеха CDPM", ""]
    e, c = rep["etalon"], rep["cdpm"]
    out += [f"Эталон: {e['file']} — строк Части 1: {e['rules']}, "
            f"вопросов Ч2: {e['questions']} (подпунктов: "
            f"{e['sub_items']}). Свод CDPM: правил {c['rules']}, "
            f"вопросов {c['questions']}.", ""]
    bv = rep["by_verdict"]
    out += ["## Метрика 1 — покрытие строк эталона", ""]
    out += [f"- найдено: {bv['найдено']}",
            f"- найдено частично: {bv['найдено частично']}",
            f"- не найдено: {bv['не найдено']}",
            f"- спорных пар (решение за владельцем): "
            f"{len(rep['disputed'])}", ""]
    out += ["## Метрика 2 — переоткрытые вопросы Ч2", "",
            f"- переоткрыто: {rep['reopened']} из {e['questions']}", ""]
    out += ["## Метрика 3 — изобретённое (счёт к нулю)", "",
            f"- строк CDPM без соответствия и без подтверждения "
            f"источником: {len(rep['invented'])}", ""]
    if rep["disputed"]:
        out += ["## Спорные пары — на решение владельца", ""]
        for d in rep["disputed"]:
            out.append(f"- {d['etalon']} ↔ {', '.join(d['candidates'])}: "
                       f"{d['basis']}")
        out.append("")
    out += [f"> {rep['note']}"]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None,
         llm_override: llm_mod.LLMFn | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(description="Судья меры успеха CDPM")
    ap.add_argument("etalon", help="эталонный свод (md, формат свода v1)")
    ap.add_argument("extract_dir", help="каталог результата извлечения")
    ap.add_argument("--kit", required=True,
                    help="каталог комплекта заморозки (для метрики 3)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--scope", default="any",
                    choices=["any", "local-only"])
    ap.add_argument("--llm", default=None,
                    choices=["local", "cloud", "auto"],
                    help="просьба о канале модели (cloud — облачная "
                         "Claude); законность решает дисциплина контура")
    args = ap.parse_args(argv)
    llm = llm_override or llm_mod.cosmos_llm(args.scope,
                                             requested=args.llm)
    try:
        rep = run_judge(Path(args.etalon), Path(args.extract_dir),
                        Path(args.kit), Path(args.out_dir), llm)
    except (JudgeError, llm_mod.LLMReplyError) as e:
        print(f"ОТКАЗ: {e}")
        return 1
    bv = rep["by_verdict"]
    print(f"строк эталона {rep['etalon']['rules']}: найдено "
          f"{bv['найдено']}, частично {bv['найдено частично']}, не "
          f"найдено {bv['не найдено']}, спорных {len(rep['disputed'])}; "
          f"вопросов переоткрыто {rep['reopened']} из "
          f"{rep['etalon']['questions']}; изобретённое: "
          f"{len(rep['invented'])}")
    out = Path(args.out_dir)
    print(f"записано: {(out / 'judge-report.json').resolve()}")
    print(f"записано: {(out / 'judge-report.md').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
