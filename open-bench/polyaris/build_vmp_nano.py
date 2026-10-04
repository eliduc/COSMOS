# -*- coding: utf-8 -*-
r"""ВМП нано — канонический ВМП стенда «Полярис-нано» (решение
владельца 2026-09-01, методика «строить от входа COSMOS»).

Эталон пишется ПЕРВЫМ: этот ВМП корректно ставит задачи пейринга и
ростеринга и ложится на базу шаблонов COSMOS посадками категории A
по построению (пакет A/B/C/D); одно правило — НАМЕРЕННАЯ категория C
(заявки-пожелания: механики dayoff_request_penalty в базе нет) — тест
честности посадки и регламента «нужен новый шаблон». Рекурсия
следующего шага (Ш-3): документы авиакомпании рендерятся из этого ВМП
с параметризованным шумом; критерий замыкания (Ш-4): конвейер с
посадкой возвращает ВМП' == ВМП нано по семьям и параметрам.

Роль в ФПР (термин владельца 2026-09-01): для финальной проверки
решений первоисточником «правил авиакомпании» решательной лестницы
служит сам ВМП нано (текстовая сериализация — клаузы читателя).

Запуск: ..\..\.venv\Scripts\python.exe build_vmp_nano.py --nano
        (перевыпуск замороженного нано-эталона — только явным ключом)
        ..\..\.venv\Scripts\python.exe build_vmp_nano.py --kit КАТАЛОГ
        [--carriers-in-model]   (эталон комплекта: модель + форма)
Выход:  ..\polyaris-nano\etalon\VMP-NANO.json (--nano) либо
        <КАТАЛОГ>/etalon/VMP-H0.json + VMP-H0-form.json (--kit);
        без ключей ничего не пишется. Все проверки — до записи.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
OUT = HERE.parent / "polyaris-nano" / "etalon"
sys.path.insert(0, str(CREW))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SID_DOCX = "S1"      # PNI1-Pravila-Polyaris.docx
SID_DATA = "S2"      # POLYARIS-DATA.xlsx
SID_JPG = "S3"       # PNC1-Pamyatka-Polyaris.jpg


def _src(sid: str, anchor: str) -> list[dict]:
    return [{"sid": sid, "anchor": anchor}]


def _fit(category: str, family: str | None = None,
         params: dict | None = None, **extra) -> dict:
    from cdpm.model import registry_fingerprint
    fit: dict = {"category": category,
                 "registry_rev": registry_fingerprint()}
    if family is not None:
        fit["family"] = family
    if params is not None:
        fit["params"] = params
    fit.update(extra)
    return fit


def _p(name: str, value, quote: str) -> dict:
    return {name: {"value": value, "quote": quote}}


def _sources_with_hashes() -> list[dict]:
    """Честные sha256 файлов набора — источники ВМП нано и есть
    файлы стенда (для решательной лестницы ВМП нано сам служит
    первоисточником ФПР)."""
    import hashlib
    nano = HERE.parent / "polyaris-nano"
    files = [(SID_DOCX, nano / "docs" / "PNI1-Pravila-Polyaris.docx"),
             (SID_DATA, nano / "data" / "POLYARIS-DATA.xlsx"),
             (SID_JPG, nano / "docs" / "PNC1-Pamyatka-Polyaris.jpg")]
    out = []
    for sid, p in files:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        out.append({"sid": sid, "file": p.name, "sha256": h})
    return out


def build(phase: int = 1) -> dict:
    """ВМП нано: пост-анкетное каноническое состояние — веса и
    развилки уже закрыты каноническими ответами книги стенда.

    phase 1 (ИДАК-0): ВСЕ правила и критерии ложатся на существующие
    шаблоны категорией A — идеальный сквозной контур; подтверждение
    посадки — НЕ априорное: приёмка внутри COSMOS (гейт посадки,
    реестр, матрица) плюс разборщик-аудитор CDPM.
    phase 2 (ИДАК-1+): добавляются намеренные неоднозначности."""
    rules = [
        # ── Мир (вне области посадки: определения и данные) ──
        {"id": "VN-003", "title": "Базы приписки",
         "statement": "Базы приписки экипажей: SVG (Северогорск), "
                      "PLY (Полярный), GRT (Гранитный); главная "
                      "база — PLY.",
         "zone": "BOTH", "kind": "DEF",
         "sources": _src(SID_DOCX, "табл.2, строка 2, кол 3")},
        {"id": "VN-002", "title": "Парк воздушных судов",
         # Числительного в определении быть не должно: измеримая
         # величина внутри определения слота не получает и становится
         # находкой баланса (канон К-3 — определение не лазейка для
         # величин). Перечень типов несут коды записи.
         # Связка определения — тире: двоеточием пишутся заголовки и
         # реквизиты, и тест нормативности такую клаузу нормой не
         # признаёт (строка свода на ненормативной клаузе = дефект).
         "statement": "Парк набора — воздушные суда типа Полярис-Р200; "
                      "судов иных типов в наборе нет.",
         "zone": "BOTH", "kind": "DEF",
         "sources": _src(SID_DOCX, "табл.2, строка 5, кол 3"),
         # Перечень кодов определения (заявка COSMOS 03.09): код и
         # дословная цитата — приёмник сверяет коды с данными набора.
         "codes": [{"code": "Полярис-Р200", "quote": "Полярис-Р200"}]},
        {"id": "VN-005", "title": "Должности пилотов",
         "statement": "Должности пилотов набора: КВС (командир "
                      "воздушного судна) и Второй пилот.",
         "zone": "BOTH", "kind": "DEF",
         "sources": _src(SID_DOCX, "табл.2, строка 6, кол 3"),
         # Эталон несёт то, что выдаёт конвейер: поле codes есть
         # доказательный слой «какие коды НАЗВАНЫ клаузой», а не
         # перечень сущностей мира. «Второй пилот» кодом-аббревиатурой
         # не является, и его отсутствие здесь — не потеря должности,
         # а отсутствие кода (семантика подтверждена COSMOS 03.09).
         "codes": [{"code": "КВС", "quote": "КВС (командир воздушного судна)"}]},
        {"id": "VN-006", "title": "Кредитный час",
         # Решение владельца 03.09 по VN-050 («входит в разброс»):
         # правило говорит о кредитных часах, а определения в наборе
         # не было — исполнитель и сертификат считали минуты налёта по
         # назначенным. Определение кладёт единицу в документы:
         # кредит = налёт по назначению; пилот без назначений — ноль
         # кредитных часов, и он входит в разброс.
         # Без числительного: «ноль часов» стал бы находкой баланса
         # (определение — не лазейка для величин, канон К-3).
         # Уточнение COSMOS 03.09: исполнитель перелёт пассажиром не
         # засчитывает — определение говорит это явно, не подразумевает.
         "statement": "Кредитный час — час налёта по назначению (время "
                      "от вылета до прилёта рейса), засчитанный пилоту; "
                      "перелёт пассажиром пилоту не засчитывается; "
                      "пилоту без назначений кредитные часы не "
                      "засчитываются, и он входит в разброс.",
         "zone": "BOTH", "kind": "DEF",
         "sources": _src(SID_DOCX, "табл.2, строка 7, кол 3")},

        # ── Жёсткие посадки категории A ──
        {"id": "VN-004", "title": "Шкала времени",
         "statement": "Все времена набора — фиксированное местное "
                      "время главной базы PLY; смещение шкалы данных "
                      "0 минут.",
         "zone": "BOTH", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.2, строка 3, кол 3"),
         # Цитаты посадок — ФРАГМЕНТЫ собственной формулировки (решение
         # владельца 2026-09-02 о строке правила: дословная цитата ИЗ
         # ФОРМУЛИРОВКИ; самопроверка генератора).
         "template_fit": _fit("A", "time_scale", _p(
             "base_tz_offset_min", 0,
             "смещение шкалы данных 0 минут"))},
        {"id": "VN-010", "title": "Предел полётной смены",
         # «Согласие экипажа» из формулировок убрано (03.09): для
         # него в базе шаблонов нет механики, а клауза с двумя
         # требованиями порождала ВТОРУЮ строку свода с посадкой C —
         # проход 10 серии прочитал документ вернее эталона и честно
         # сказал «нет механики проверки согласия». Набор ИДАК-0 обязан
         # состоять из правил, ложащихся категорией A: неоднозначности
         # — предмет следующих ступеней, не этой.
         "statement": "Базовый предел полётной смены — 12:00 "
                      "(720 минут); превышение — только продлением "
                      "по правилу VN-011.",
         "zone": "PAIRING", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.3, строка 2, кол 3"),
         "template_fit": _fit("A", "duty_limits", _p(
             "max_duty_duration", 720,
             "Базовый предел полётной смены — 12:00 (720 минут)"))},
        {"id": "VN-020", "title": "Стыковка в базе",
         "statement": "Минимальная стыковка между рейсами — 45 минут "
                      "(в базе и вне базы одинаково).",
         "zone": "PAIRING", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.3, строка 3, кол 3"),
         "template_fit": _fit("A", "connection_windows", _p(
             "min_turnaround", 45,
             "Минимальная стыковка между рейсами — 45 минут"))},
        {"id": "VN-023", "title": "Отдых дома",
         "statement": "Минимальный отдых между сменами дома — "
                      "12:00 (720 минут).",
         "zone": "ROSTERING", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.3, строка 4, кол 3"),
         "template_fit": _fit("A", "rest_floors", _p(
             "min_rest_home", 720,
             "Минимальный отдых между сменами дома — 12:00 (720 минут)"))},
        {"id": "VN-024", "title": "Отдых вне базы",
         "statement": "Минимальный отдых между сменами вне базы — "
                      "10:00 (600 минут).",
         "zone": "ROSTERING", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.3, строка 5, кол 3"),
         "template_fit": _fit("A", "rest_floors", _p(
             "min_rest_outstation", 600,
             "Минимальный отдых между сменами вне базы — 10:00 "
             "(600 минут)"))},
        {"id": "VN-060", "title": "Резерв на базах",
         "statement": "Ежедневно резерв: PLY ≥ 2 экипажа, SVG ≥ 1, "
                      "GRT ≥ 1; сутки — по местному времени базы; "
                      "мера — полные экипажи.",
         "zone": "ROSTERING", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.5, строка 2, кол 3"),
         # per-base значения — строкой канонической формы, коды баз
         # СОРТИРОВАНЫ алфавитно (решение К-2: детерминизм диффа).
         "template_fit": _fit("A", "reserve_floors", _p(
             "reserve_crews", "GRT:1,PLY:2,SVG:1",
             "Ежедневно резерв: PLY ≥ 2 экипажа, SVG ≥ 1, GRT ≥ 1"))},
        {"id": "VN-070", "title": "Допуск по типу воздушного судна",
         # Залог утверждения, а не «только при»: конструкция «только
         # при X» на прогоне 03.09 была пересказана моделью как
         # «запрещено при X» — норма читается однозначно, когда
         # написана прямым утверждением.
         "statement": "Рейс на воздушном судне типа Полярис-Р200 "
                      "выполняет пилот, у которого допуск на этот тип "
                      "действует на дату рейса.",
         "zone": "BOTH", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.3, строка 8, кол 3"),
         "template_fit": _fit("A", "fleet_exclusions", _p(
             "fleet_types", "Полярис-Р200",
             "на воздушном судне типа Полярис-Р200"))},
        {"id": "VN-081", "title": "Состав экипажа",
         # Должности названы ТЕМИ ЖЕ словами, что в определении
         # VN-005: два разных имени одной сущности («роли
         # командир/второй пилот» против «должности КВС и Второй
         # пилот») породили вопрос анкеты о том, различаются ли роли
         # (прогон 03.09).
         "statement": "Экипаж смены — ровно 2 пилота; должности КВС и "
                      "Второй пилот при комплектовании смены не "
                      "различаются.",
         "zone": "BOTH", "kind": "HARD",
         "sources": _src(SID_DOCX, "табл.2, строка 4, кол 3"),
         "template_fit": _fit("A", "crew_composition", _p(
             "crew_size", 2, "Экипаж смены — ровно 2 пилота"))},

        # ── Мягкие посадки категории A (веса — канон книги стенда) ──
        {"id": "VN-011", "title": "Продление смены",
         # Потолок продления — в самой формулировке (аудитор прогона
         # №3 H0: потолок был только в параметрах с чужой цитатой).
         "statement": "Превышение базового предела 12:00 (720 минут) "
                      "— мягкое предпочтение со штрафом 15 условных "
                      "единиц за смену с превышением; потолок "
                      "продления — 13:00 "
                      "(780 минут).",
         "zone": "PAIRING", "kind": "SOFT", "weight": 15,
         "sources": _src(SID_DOCX, "табл.3, строка 2, кол 6"),
         "template_fit": _fit("A", "duty_extension_penalty", {
             **_p("long_duty_threshold", 720,
                  "Превышение базового предела 12:00 (720 минут)"),
             **_p("duty_extension_cap", 780,
                  "потолок продления — 13:00 (780 минут)")})},
        {"id": "VN-030", "title": "Ночёвки вне базы",
         "statement": "Штраф 10 условных единиц за каждую ночёвку "
                      "вне базы приписки.",
         "zone": "PAIRING", "kind": "SOFT", "weight": 10,
         "sources": _src(SID_DOCX, "табл.3, строка 6, кол 3"),
         "template_fit": _fit(
             "A", "layover_penalty",
             quote="Штраф 10 условных единиц за каждую ночёвку вне "
                   "базы приписки")},
        {"id": "VN-D91", "title": "Ночёвки в конце недели",
         "statement": "Надбавка 25 условных единиц к штрафу за "
                      "ночёвку вне базы, календарный день начала "
                      "которой (по местному времени базы) — пятница "
                      "или суббота.",
         "zone": "PAIRING", "kind": "SOFT", "weight": 25,
         "sources": _src(SID_DOCX, "табл.3, строка 7, кол 3"),
         "template_fit": _fit(
             "A", "calendar_windows",
             quote="ночёвку вне базы, календарный день начала которой "
                   "(по местному времени базы) — пятница или суббота")},
        {"id": "VN-050", "title": "Разброс кредитных часов",
         "statement": "Штраф 3 условные единицы за каждый кредитный "
                      "час разброса сверх порога существенности "
                      "10 кредитных часов за месяц.",
         "zone": "ROSTERING", "kind": "SOFT", "weight": 3,
         "sources": _src(SID_DOCX, "табл.4, строка 2, кол 3"),
         # Порог существенности — словарный параметр семьи (реестр
         # 2712cf6, решение владельца 03.09): часы, читается
         # исполнителем решателя и мерой сертификата.
         "template_fit": _fit(
             "A", "balanced_workload",
             {**_p("workload_spread_threshold", 10,
                   "сверх порога существенности 10 кредитных часов "
                   "за месяц")},
             quote="Штраф 3 условные единицы за каждый кредитный час "
                   "разброса сверх порога существенности 10 кредитных "
                   "часов за месяц")},

    ]
    if phase >= 2:
        # Фаза неоднозначностей (ИДАК-1+): намеренная категория C —
        # честное «нет шаблона» как тест регламента. В фазе ИДАК-0
        # (последовательность владельца 2026-09-01) ВСЕ правила
        # ложатся на существующие шаблоны — этого правила нет,
        # и листа «Заявки» в книге данных ИДАК-0 нет.
        rules.append(
            {"id": "VN-REQ", "title": "Заявки-пожелания выходных",
             "statement": "Пожелание сотрудника о выходном дне "
                          "(лист «Заявки») желательно удовлетворять; "
                          "нарушение — штраф (механики-исполнителя в "
                          "базе нет: кандидат dayoff_request_penalty).",
             "zone": "ROSTERING", "kind": "SOFT", "weight": 20,
             "sources": _src(SID_DATA, "лист «Заявки», строка 2"),
             "template_fit": _fit(
                 "C",
                 gap="нет механики штрафа за нарушение заявки "
                     "выходного дня (кандидат-шаблон "
                     "dayoff_request_penalty)")})
    # Семья решателя у мягких — из посадки (контракт r55-CDPM
    # assign_soft_families: export_solver.family := template_fit.family;
    # окно дней и пояс calendar_windows — в той же записи, иначе форма
    # окна решателя не соберётся — валидатор r55 это требует и от
    # эталона). Эталон обязан нести то, что выдаёт конвейер.
    for r in rules:
        fit = r.get("template_fit") or {}
        if r.get("kind") != "SOFT" or fit.get("category") not in ("A", "B"):
            continue
        es = {"family": fit["family"]}
        if fit["family"] == "calendar_windows":
            es["days"] = ["friday", "saturday"]
            es["tz"] = "base-local"
        r["export_solver"] = es
    objectives = [
        {"id": "O-PAIR", "title": "Стоимость пейрингов",
         "zone": "PAIRING", "direction": "minimize",
         "metric": "total_pairing_cost",
         "statement": "Минимум суммарной стоимости пейрингов; "
                      "машинный критерий MIN_PERSON_DAYS, ставка "
                      "100 условных единиц за календарный день "
                      "командировки (версия M-036/demo-1).",
         "sources": _src(SID_DOCX, "табл.2, строка 2, кол 3")},
        {"id": "O-ROST", "title": "Равномерность кредитных часов",
         "zone": "ROSTERING", "direction": "minimize",
         "metric": "credit_hours_spread",
         # Без ссылки на правило чужой зоны (аудитор прогона №3 H0:
         # VN-011 — пейринг; коэффициенты мягких — в их строках).
         # Ставка немашинного критерия — носитель в модели (структура
         # ВМП, решение 5: строка в) несёт ставку в валюте формы;
         # аудит №1 M1): 1 условная единица за час разброса, цитата —
         # фрагмент формулировки.
         "statement": "Минимум разброса кредитных часов по ростеру; "
                      "ставка 1 условная единица за час разброса "
                      "кредитных часов; коэффициенты мягких правил "
                      "части «Ростеринг» — в их строках.",
         "rate": {"value": 1, "per": "час разброса кредитных часов",
                  "as_written": "ставка 1 условная единица за час "
                                "разброса кредитных часов"},
         "sources": _src(SID_DOCX, "табл.4, строка 2, кол 3")},
    ]
    return {
        "meta": {
            "customer": "Полярис-нано (ВМП нано)",
            "mode": 1,
            "cdpm_version": "vmp-nano-1",
            "fit_contract": True,
            "note": "канонический ВМП по построению (решение "
                    "владельца 2026-09-01): вход COSMOS первичен, "
                    "документы рендерятся из него (Ш-3)",
        },
        "sources": _sources_with_hashes(),
        "rules": rules,
        # Канонический носитель машинного критерия пейринга —
        # ПРИМЕНЁННЫЙ ответ вопроса о критерии (решение К-3: экспорт
        # условий читает raw строгим разбором; отдельного машинного
        # поля критерия в модели нет).
        "questions": [
            {"id": "Q-3a-критерий-пейринга", "part": "3a",
             "title": "Машинный критерий пейринга",
             "text": "Подтвердите машинный критерий построения "
                     "пейрингов и ставку.",
             "answer_format": {"type": "value",
                               "note": "машинный блок критерия"},
             "rule_refs": ["VN-010"], "source_refs": _src(
                 SID_DOCX, "табл.2, строка 2, кол 3"),
             "answer": {
                 "raw": "Машинный критерий пейринга — минимум "
                        "человеко-дней командировок, ставка 100 "
                        "условных единиц за календарный день "
                        "командировки. Версия ставок M-036/demo-1, "
                        "действует с 2026-08-01. Машинный блок: "
                        "{\"criterion\": \"MIN_PERSON_DAYS\", "
                        "\"rate_per_duty\": 100, "
                        "\"rule\": \"O-PAIR\", "
                        "\"version\": \"M-036/demo-1\", "
                        "\"effective_from\": \"2026-08-01\"}",
                 "status": "применён", "revision": 1}},
            {"id": "Q-3b-GOAL", "part": "3b",
             "title": "Целевая функция ростеринга: состав и веса",
             "text": "Подтвердите целевую функцию ростеринга и веса "
                     "мягких правил.",
             "answer_format": {"type": "registry",
                               "note": "таблица целевых и веса"},
             "rule_refs": ["VN-050"], "source_refs": _src(
                 SID_DOCX, "табл.4, строка 2, кол 3"),
             "answer": {
                 "raw": "Компонент: равномерность кредитных часов; "
                        "метрика: разброс кредитных часов "
                        "(credit_hours_spread), часы; направление: "
                        "минимизировать разброс; ярус 1; вес 1; "
                        "ничьи: лексикографически по табельному "
                        "номеру. Веса мягких правил ростеринга: "
                        "разброс кредитных часов — 3, превышение "
                        "порога длительности смены (продление с "
                        "согласия) — 15.",
                 "status": "применён", "revision": 1}},
        ],
        "objectives": objectives,
    }


def _kit_sources(kit: Path) -> list[dict]:
    """Источники ВМП комплекта — по kit-manifest.json: файлы правил
    (rules_files) в ПОРЯДКЕ подачи получают S1, S2, … — ровно как
    нумерует cdpm.freeze; файлы данных (data_files) — не источники
    правил (решение Р1б, 2026-09-01), sid не получают. Честные sha256.
    Без манифеста или без rules_files — отказ словами."""
    import hashlib
    mani_path = kit / "kit-manifest.json"
    if not mani_path.is_file():
        raise ValueError(f"нет манифеста комплекта: {mani_path}")
    mani = json.loads(mani_path.read_text(encoding="utf-8"))
    rules_files = mani.get("rules_files") or []
    if not rules_files:
        raise ValueError(f"{mani_path.name}: rules_files пуст")
    out = []
    for i, rel in enumerate(rules_files, start=1):
        p = kit / rel
        if not p.is_file():
            raise ValueError(f"файл правил комплекта не найден: {p}")
        out.append({"sid": f"S{i}", "file": p.name,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return out


def apply_anchor_map(doc: dict, anchors: dict,
                     sources: list[dict] | None = None) -> dict:
    """Якоря комплекта первичны (якорь принадлежит документу, ВМП несёт
    ссылку; обратная задача H0, решение 2026-09-01): карта
    {id: [{sid, anchor}]} переопределяет sources правил и целевых по
    id; source_refs вопроса — по его id в карте, иначе по первому
    rule_refs. Служебные ключи карты («_…») пропускаются. Правило
    ВМП без записи в карте — отказ: якорь ниоткуда не берётся."""
    missing = []
    for r in doc.get("rules") or []:
        if r["id"] in anchors:
            r["sources"] = [dict(a) for a in anchors[r["id"]]]
        else:
            missing.append(r["id"])
    for o in doc.get("objectives") or []:
        if o["id"] in anchors:
            o["sources"] = [dict(a) for a in anchors[o["id"]]]
        else:
            missing.append(o["id"])
    by_id = {x["id"]: x for x in (doc.get("rules") or [])
             + (doc.get("objectives") or [])}
    for q in doc.get("questions") or []:
        if q["id"] in anchors:
            q["source_refs"] = [dict(a) for a in anchors[q["id"]]]
        else:
            ref = next((by_id[x] for x in q.get("rule_refs") or []
                        if x in by_id), None)
            if ref is not None:
                q["source_refs"] = [dict(a) for a in ref["sources"]]
    if missing:
        raise ValueError("в карте якорей комплекта нет записей: "
                         + ", ".join(missing))
    if sources is not None:
        doc["sources"] = sources
    return doc


def carriers_in_model(doc: dict) -> dict:
    """Носители машинных условий — В МОДЕЛИ, а не в ответах анкеты
    (принципы ИД→ВМП П6/П11, прогон №3 H0 2026-09-02: набор «буква в
    букву» нёс записи вопросов эталона с ответами, и конвейер прочитал
    их целевыми — документы авиакомпании вопросов CDPM не содержат по
    природе). Машинный блок критерия пейринга из применённого ответа
    вопроса части 3a переносится в целевую пейринга полем criterion
    (форма r55-CDPM: kind, rate_per_duty, version, effective_from,
    as_written); вопросы анкеты снимаются целиком — веса мягких уже в
    правилах, состав целевой ростеринга — в целевой. Нано-эталон этим
    не перевыпускается (решение владельца «нано-стенд не трогать»)."""
    import re
    questions = list(doc.get("questions") or [])
    q3a = next((q for q in questions
                if str(q.get("part")) == "3a"
                and isinstance(q.get("answer"), dict)
                and q["answer"].get("status") == "применён"), None)
    if q3a is None:
        raise ValueError("нет применённого ответа части 3a — критерий "
                         "переносить неоткуда")
    raw = str(q3a["answer"].get("raw") or "")
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("ответ части 3a без машинного блока критерия")
    blk = json.loads(m.group(0))
    kind = blk.get("criterion") or blk.get("kind")
    if not kind or blk.get("rate_per_duty") is None:
        raise ValueError("машинный блок критерия неполон (criterion, "
                         "rate_per_duty)")
    target_id = blk.get("rule") or "O-PAIR"
    target = next((o for o in doc.get("objectives") or []
                   if o.get("id") == target_id), None)
    if target is None:
        raise ValueError(f"целевой {target_id!r} для критерия нет")
    # Вид — значением закрытого словаря CDPM (метка MIN_PERSON_DAYS →
    # min_person_days): блок обязан быть ИСПОЛНИМЫМ по контракту r55.
    from cdpm.model import CRITERION_KINDS
    kind_value = CRITERION_KINDS.get(str(kind).upper(), str(kind))
    if kind_value not in CRITERION_KINDS.values():
        raise ValueError(f"вид критерия {kind!r} вне словаря CDPM")
    # as_written — ФРАГМЕНТ собственной формулировки целевой (принцип
    # самосогласованности эталона; приёмник CDPM требует цитату из
    # клаузы): часть формулировки после «; », где названы критерий и
    # ставка; отказ, если фрагмента нет.
    statement = str(target.get("statement") or "")
    as_written = statement.split("; ", 1)[1].rstrip(".") \
        if "; " in statement else ""
    if not as_written or _norm_text(as_written) not in _norm_text(statement):
        raise ValueError(f"формулировка целевой {target_id!r} не несёт "
                         f"фрагмента с критерием и ставкой для as_written")
    target["criterion"] = {
        "kind": kind_value,
        "rate_per_duty": blk.get("rate_per_duty"),
        # «за что» ставки (структура ВМП, строка в): единица ставки
        # словами — из формулировки целевой.
        "per": "календарный день командировки"
        if "календарный день командировки" in statement else None,
        "version": blk.get("version"),
        "effective_from": blk.get("effective_from"),
        "as_written": as_written,
    }
    target["criterion"] = {k: v for k, v in target["criterion"].items()
                           if v not in (None, "")}
    doc["questions"] = []
    doc["meta"]["note"] = (doc["meta"].get("note") or "") + \
        "; носители машинных условий — в модели (criterion у целевой " \
        "пейринга), вопросов анкеты нет"
    return doc


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ВМП нано / ВМП H0 комплекта")
    ap.add_argument("--kit", default=None,
                    help="каталог комплекта (open-bench/polyaris-h0): якоря "
                         "из <kit>/etalon/anchors-map.json, источники — "
                         "файлы комплекта; выход <kit>/etalon/VMP-H0.json")
    ap.add_argument("--carriers-in-model", action="store_true",
                    help="носители машинных условий — в модели: критерий "
                         "пейринга блоком criterion у целевой, вопросов "
                         "анкеты нет (только с --kit; нано-эталон не "
                         "трогается)")
    ap.add_argument("--nano", action="store_true",
                    help="перевыпуск замороженного нано-эталона "
                         "VMP-NANO.json (стенд «Полярис-нано»); без ключа "
                         "и без --kit генератор ничего не пишет")
    a = ap.parse_args(argv)
    doc = build()
    if a.carriers_in_model and not a.kit:
        print("ОТКАЗ: --carriers-in-model действует только с --kit "
              "(нано-эталон не перевыпускается)")
        return 2
    if not a.kit and not a.nano:
        # Нано-эталон заморожен (решение владельца «нано-стенд не
        # трогать»): умолчание не должно быть разрушительным (аудит №1).
        same = json.dumps(build(), ensure_ascii=False, sort_keys=True) == \
            json.dumps(json.loads((OUT / "VMP-NANO.json").read_text(
                encoding="utf-8")), ensure_ascii=False, sort_keys=True) \
            if (OUT / "VMP-NANO.json").is_file() else None
        print("ничего не записано: укажите --kit КАТАЛОГ (эталон "
              "комплекта) либо --nano (перевыпуск нано-эталона); "
              "построение %s нано-эталону на диске"
              % ("совпадает с" if same else
                 "НЕ совпадает с" if same is False else
                 "— файла нет,"))
        return 2
    if a.kit:
        kit = Path(a.kit)
        amap_path = kit / "etalon" / "anchors-map.json"
        if not amap_path.is_file():
            print(f"ОТКАЗ: нет карты якорей комплекта: {amap_path}")
            return 2
        amap = json.loads(amap_path.read_text(encoding="utf-8"))
        amap = {k: v for k, v in amap.items() if not k.startswith("_")}
        try:
            if a.carriers_in_model:
                # Сначала носители — вопросов в модели не остаётся, и
                # карта якорей обязана покрыть только правила и целевые.
                doc = carriers_in_model(doc)
            doc = apply_anchor_map(doc, amap, _kit_sources(kit))
        except ValueError as e:
            print(f"ОТКАЗ: {e}")
            return 2
        doc["meta"]["customer"] = "Полярис-H0 (ВМП H0 комплекта)"
        doc["meta"]["note"] += "; якоря — из карты комплекта " \
                               + amap_path.name
        out_dir = kit / "etalon"
        path = out_dir / "VMP-H0.json"
    else:
        out_dir = OUT
        path = OUT / "VMP-NANO.json"
    # ВСЕ проверки — ДО записи: на диск попадают только чистые
    # проекции, и обе сразу (аудит №1: запись до проверок оставляла
    # невалидную модель рядом с прежней формой).
    # 1) Контракт CDPM обоими слоями — эталон обязан быть чист.
    from cdpm import model as m
    problems = (m.validate_schema(doc) + m.validate_model(doc)
                + m.validate_template_fit(doc))
    from vmp import registry_check as rc
    for r in doc["rules"]:
        if isinstance(r.get("template_fit"), dict):
            g = rc.fit_gap(r)
            ok_c = (r["template_fit"]["category"] == "C"
                    and g and g.get("kind") == "rule_no_template")
            if g and not ok_c:
                problems.append("fit_gap %s: %s" % (r["id"], g))
    if problems:
        print("ОТКАЗ — НАРУШЕНИЯ контракта (%d), ничего не записано:"
              % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    # 2) Самосогласованность (принцип П1): цитата посадки, цитаты
    # параметров и цитата критерия — фрагменты СОБСТВЕННОЙ формулировки.
    # В режиме --kit это условие выпуска (ту же проверку делает строгая
    # форма); в нано-режиме — отчёт поимённо.
    loose = self_consistency_report(doc)
    if loose and a.kit:
        print("ОТКАЗ — САМОСОГЛАСОВАННОСТЬ (%d), ничего не записано:"
              % len(loose))
        for line in loose:
            print(" -", line)
        return 1
    # 3) Форма ВМП (структура владельца 2026-09-02, шаг 1): вторая
    # проекция того же построения — без потерь и строго чиста.
    form = None
    layout = ""
    if a.kit:
        from cdpm import vmp_form as vf
        rt = vf.roundtrip_problems(doc)
        if rt:
            print("ОТКАЗ: модель ↔ форма с потерями (%d), ничего не "
                  "записано:" % len(rt))
            for line in rt:
                print(" -", line)
            return 1
        form = vf.to_form(doc)
        fp = vf.validate_form(form, strict=True)
        if fp:
            print("ОТКАЗ: форма эталона не чиста (%d), ничего не "
                  "записано:" % len(fp))
            for line in fp:
                print(" -", line)
            return 1
        layout = "; ".join(
            f"{name} {n}" for name, n in (
                ("определения", len(form["common"]["definitions"])),
                ("мир", len(form["common"]["world"])),
                ("ч.1 а/б/в", "/".join(str(len(form["pairing"][k]))
                                       for k in ("hard", "soft", "criteria"))),
                ("ч.2 а/б/в", "/".join(str(len(form["rostering"][k]))
                                       for k in ("hard", "soft", "criteria"))),
                ("снято", len(form["withdrawn"])),
                ("вопросов отвеченных/открытых",
                 f"{len(form['answered_questions'])}/"
                 f"{len(form['open_questions'])}")))
    # 4) Запись — обе проекции разом.
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1)
                    + "\n", encoding="utf-8")
    print(f"ВМП записан: {path}")
    if form is not None:
        form_path = out_dir / (path.stem + "-form.json")
        vf.dump_form(form, form_path)
        print(f"форма записана: {form_path} (без потерь, строго чиста; "
              f"{layout})")
    print("контракт чист: схема + инварианты + посадки")
    if loose:
        print("САМОСОГЛАСОВАННОСТЬ (цитата посадки вне формулировки, %d):"
              % len(loose))
        for line in loose:
            print(" -", line)
    else:
        print("самосогласованность: цитаты посадок — фрагменты формулировок")
    return 0


def _norm_text(s: str) -> str:
    return " ".join(str(s or "").replace("«", "").replace("»", "")
                    .replace(" ", " ").casefold().split())


def self_consistency_report(doc: dict) -> list[str]:
    """Перечень правил, у которых цитата посадки (quote) или цитата
    параметра (params[*].quote) не является фрагментом собственной
    формулировки (statement); пусто = самосогласован."""
    out: list[str] = []
    for r in doc.get("rules") or []:
        fit = r.get("template_fit")
        if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
            continue
        st = _norm_text(r.get("statement"))
        quotes = []
        if fit.get("quote"):
            quotes.append(("quote", fit["quote"]))
        for name, cell in (fit.get("params") or {}).items():
            q = cell.get("quote") if isinstance(cell, dict) else None
            if q:
                quotes.append((f"params.{name}", q))
        for where, q in quotes:
            if _norm_text(q) not in st:
                out.append(f"{r.get('id')}: {where} «{q}» — не фрагмент "
                           f"формулировки")
    # Целевые: цитаты-носители ставки (criterion.as_written у
    # машинного критерия и rate.as_written у немашинного) — тоже
    # фрагменты собственной формулировки целевой (аудит №2).
    for o in doc.get("objectives") or []:
        st = _norm_text(o.get("statement"))
        for where, block in (("criterion", o.get("criterion")),
                             ("rate", o.get("rate"))):
            if isinstance(block, dict) and block.get("as_written") \
                    and _norm_text(block["as_written"]) not in st:
                out.append(f"{o.get('id')}: {where}.as_written "
                           f"«{block['as_written']}» — не фрагмент "
                           f"формулировки")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
