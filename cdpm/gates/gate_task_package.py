# -*- coding: utf-8 -*-
"""Гейт пакета постановки задачи COSMOS (мост А).

Проверяет самодостаточность и честность пакета против спецификации
docs/spec-postanovka-cosmos-v1.md:

1. самотест: порча байта файла обязана ловиться контрольной суммой;
2. манифест: известная версия спецификации, перечень файлов полон
   (каждый файл пакета назван, лишних нет), SHA-256 совпадают;
3. model.json проходит оба слоя контракта П-1 (схема + инварианты);
4. разрешённые режимы и целевые манифеста РАВНЫ пересчёту матрицы
   корректности из model.json — заявленное не расходится с моделью;
5. веса: при разрешённых feasibility-soft/optimization задачи каждый
   компонент её зоны в objective.json несёт числовой вес (fail-closed:
   потребитель не имеет права подставлять умолчания);
6. clean-data.json существует ⇔ манифест заявляет has_data;
7. непротиворечивость (`manifest.consistency`, СМТ-1): форма ключа и
   вердикт из перечня — «sat» либо «none»; пакет с иным вердиктом
   рубеж выпуска не выпускает, значит справка подделана;
8. биекция и содержание (A11): факты считаются тем же расчётом, что у
   панели и замка выпуска (`cdpm.coverage.bijection_facts`), и ПО
   САМОМУ ПАКЕТУ — `model.json` против `freeze/clauses/<sid>.json`;
   протокол измерителя как производный артефакт ПЕРЕСЧИТЫВАЕТСЯ
   (`_recount_problems`) по вердикт-независимым частям. Недоступен
   гейту только адресат остатка ВЕРДИКТОМ аудитора и признак «не
   правило»: `exchange/coverage.json` в пакет не едет — граница
   названа в спецификации, п. 4.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный —
код возврата 1.

Запуск: python -m cdpm.gates.gate_task_package ПАКЕТ.zip|КАТАЛОГ
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from cdpm import model as m

_KNOWN_MAJOR = "1"


def _load(package: Path) -> dict[str, bytes]:
    # Пакет-каталог читается рекурсивно, имена — относительные POSIX
    # (как в zip): с дополнения к 1.17 записи бывают вложенными
    # (freeze/clauses/<sid>.json), плоское чтение теряло их и краснило
    # «назван в манифесте, но отсутствует» (красные test_cdpm_content_lock
    # на полном прогоне 06.09).
    if package.is_dir():
        return {p.relative_to(package).as_posix(): p.read_bytes()
                for p in sorted(package.rglob("*")) if p.is_file()}
    with zipfile.ZipFile(package) as z:
        return {n: z.read(n) for n in z.namelist()}


def _selftest() -> None:
    files = {"manifest.json": json.dumps({
        "spec_version": "1.0",
        "files": [{"name": "model.json", "sha256": "0" * 64,
                   "bytes": 2}],
        "allowed_modes": {}, "objectives": {}, "has_data": False,
    }).encode(), "model.json": b"{}"}
    problems, _ = _check_files(files)
    # Явный `raise`, а не `assert` (12.09.2026): под `python -O`
    # утверждения не исполняются — самотест стал бы пустым местом, а
    # гейт печатал бы «самотест пройден», ничего не проверив.
    if not any("sha256" in p for p in problems):
        raise RuntimeError("самотест гейта: порча файла не поймана "
                           "контрольной суммой — гейт не судит")


def _check_files(files: dict[str, bytes]) -> tuple[list[str], dict]:
    problems: list[str] = []
    if "manifest.json" not in files:
        return ["manifest.json отсутствует"], {}
    try:
        manifest = m.loads_strict(files["manifest.json"])
    except Exception as e:                                # noqa: BLE001
        return [f"manifest.json не читается: {e}"], {}
    if not isinstance(manifest, dict):
        return ["manifest.json не объект"], {}
    ver = str(manifest.get("spec_version", ""))
    if ver.split(".")[0] != _KNOWN_MAJOR:
        problems.append(f"неизвестная версия спецификации {ver!r}")
    listed = {f.get("name"): f for f in manifest.get("files", [])
              if isinstance(f, dict)}
    for name, entry in listed.items():
        if name not in files:
            problems.append(f"{name}: назван в манифесте, но в "
                            f"пакете отсутствует")
            continue
        got = hashlib.sha256(files[name]).hexdigest()
        if got != entry.get("sha256"):
            problems.append(f"{name}: sha256 не совпадает с "
                            f"манифестом")
    for name in files:
        if name != "manifest.json" and name not in listed:
            problems.append(f"{name}: файл пакета не назван в "
                            f"манифесте")
    return problems, manifest


def check(package: Path, *, landing_compat=None) -> tuple[list[str], dict]:
    files = _load(package)
    if {"component-manifest.json", "declared-world.json", "declared-expected.json"} & set(files):
        return ["component-only: component input is not an executable task package"], {
            "files": len(files), "rules": 0, "objectives": 0, "modes": 0,
            "spec_version": "component-only"}
    problems, manifest = _check_files(files)
    if manifest.get("schema") == "component-input/1" or manifest.get("scope") == "component-only":
        return ["component-only: renamed component input is not an executable task package"], {
            "files": len(files), "rules": 0, "objectives": 0, "modes": 0,
            "spec_version": "component-only"}
    counters = {"files": len(files), "rules": 0, "objectives": 0,
                "modes": 0,
                "spec_version": str(manifest.get("spec_version",
                                                 "?"))}
    if "model.json" not in files:
        problems.append("model.json отсутствует")
        return problems, counters
    try:
        # Строгое чтение (A05, Н-3): дубль ключа в байтах пакета —
        # молчаливая подмена значения последним объявлением; гейт
        # краснит её теми же словами, что рубеж выпуска.
        doc = m.loads_strict(files["model.json"])
    except Exception as e:                                # noqa: BLE001
        problems.append(f"model.json не читается: {e}")
        return problems, counters
    problems += m.validate_schema(doc)
    problems += m.validate_model(doc)
    # A05 landing-version/landing-incomplete are judged from raw package
    # bytes too. The producer gate never grants legacy execution consent.
    from cdpm import landing_contract
    landing_state = landing_contract.inspect(doc, manifest,
        landing_compat=landing_compat, for_execution=landing_compat is None)
    problems += [p["words"] for p in landing_state["problems"]]
    counters["landing_archive_only"] = landing_state["archive_only"]
    # Точное происхождение значения из ответа (A03-мин/K05-мин,
    # контракт П0 §2.3 п. 3): гейт судит пакет тем же расчётом, что
    # рубеж выпуска (общий закон П0 № 4) — по model.json пакета, без
    # доверия справкам манифеста. Приписанное значение с фиктивным
    # основанием в ответе краснит гейт словами.
    from cdpm import provenance as _prov
    problems += [f"model.json: {p} (происхождение значения из ответа — "
                 f"код {_prov.ANSWER_PROVENANCE})"
                 for p in _prov.package_problems(doc)]
    # Блокер прохода 06.09.2026: правило с исполнителем-ручкой и logic
    # required — решатель отвергает пакет; гейт краснит до отправки.
    problems += [f"model.json: {p} (блок logic у правила с исполнителем-"
                 f"ручкой шаблона обязан быть post-check)"
                 for p in m.logic_fit_conflicts(doc)]
    # K04-барьер (контракт П0 §2.4 (а)): ячейка посадки под открытым
    # пересмотром — пакет с ней не выпускается. Гейт производителя
    # считает ТЕМ ЖЕ расчётом, что рубеж выпуска и готовность, но по
    # `model.json` САМОГО пакета: обход сборщика (подложенный пакет,
    # чужая сборка) краснеет здесь.
    from cdpm.export_task_package import FAMILY_INCOMPLETE
    for fact in m.pending_revision_facts(doc):
        problems.append(f"model.json: {fact['words']} "
                        f"(отказ {FAMILY_INCOMPLETE}: пересмотр открыт — "
                        f"пакет с прежним значением не выпускается)")
    counters["rules"] = len(doc.get("rules", []))
    counters["objectives"] = len(doc.get("objectives", []))

    # Ревизия 1.10: файл форм порогов сверяется ПЕРЕСБОРКОЙ из
    # model.json (детерминизм сборки — контракт); файл без
    # объявления манифестом ловится общей сверкой файлов ниже.
    from cdpm import threshold_forms as tf
    # Ревизия 1.13: байты форм судятся версией СВОЕГО пакета (текст
    # warning версионирован; легаси-текст — из контура данных).
    # Структура ВМП 2026-09-02: правила с пометкой «исключено до
    # получения данных» сборщик в машинные файлы не кладёт — гейт
    # пересобирает по тому же срезу (аудит №2), иначе ложное
    # «не совпадает с пересборкой».
    from cdpm.export_task_package import excluded_rules_slice
    doc_x = excluded_rules_slice(doc)
    try:
        expect_tf = tf.forms_bytes(doc_x, manifest.get("spec_version"))
    except tf.ThresholdFormError as e:
        problems.append(f"threshold-forms.json: {e}" +
                        (f" (отказ {e.code})" if e.code else ""))
    else:
        got_tf = files.get("threshold-forms.json")
        if expect_tf is not None and got_tf is None:
            problems.append("threshold-forms.json отсутствует, а модель "
                            "несёт машинные формы порогов")
        elif expect_tf is None and got_tf is not None:
            problems.append("threshold-forms.json есть, а из модели "
                            "формы не собираются — файл ниоткуда")
        elif expect_tf is not None and got_tf != expect_tf:
            problems.append("threshold-forms.json не совпадает с "
                            "пересборкой из model.json — детерминизм "
                            "сборки нарушен")

    from cdpm.export_task_package import allowed_modes
    modes, objective_ids = allowed_modes(
        doc, spec_version=manifest.get("spec_version"))
    counters["modes"] = sum(len(v) for v in modes.values())
    if manifest.get("allowed_modes") != modes:
        problems.append(
            f"allowed_modes манифеста {manifest.get('allowed_modes')} "
            f"≠ пересчёту матрицы {modes}")
    if manifest.get("objectives") != objective_ids:
        problems.append(
            f"objectives манифеста {manifest.get('objectives')} ≠ "
            f"пересчёту матрицы {objective_ids}")

    if "objective.json" not in files:
        problems.append("objective.json отсутствует")
    else:
        try:
            obj = json.loads(files["objective.json"].decode("utf-8"))
        except Exception as e:                            # noqa: BLE001
            problems.append(f"objective.json не читается: {e}")
            obj = None
        from cdpm import catalog_bridge as cb
        from vmp import objective_projection as op
        if op.judged_for_spec(manifest.get("spec_version")):
            try:
                projection_problems = op.compare(
                    obj, doc, cb.catalog(),
                    context={"criterion": m.derive_pairing_criterion(doc)})
            except (op.ObjectiveProjectionError, *cb.CATALOG_ERRORS) as exc:
                problems.append(f"criterion-direction: {exc}")
            else:
                problems.extend(f"{p['code']}: {p['words']}"
                                for p in projection_problems)
        if obj:
            for task in ("pairing", "rostering"):
                needs_weights = any(
                    md in (manifest.get("allowed_modes") or {})
                    .get(task, [])
                    for md in ("feasibility-soft", "optimization"))
                if not needs_weights:
                    continue
                for comp in (obj.get(task) or {}).get(
                        "components", []):
                    if not isinstance(comp.get("weight"), int):
                        problems.append(
                            f"{task}: компонент "
                            f"{comp.get('rule_id')!r} без числового "
                            f"веса при разрешённом режиме со "
                            f"штрафами")

    crew = manifest.get("crew_size")
    # Пакет судится по ЗАЯВЛЕННОЙ им версии: crew_size — контракт
    # спецификации 1.2+, у более ранних пакетов отсутствие поля
    # законно (но присутствующее поле сверяется всегда).
    ver_parts = str(manifest.get("spec_version", "")).split(".")
    minor = int(ver_parts[1]) if (len(ver_parts) > 1
                                  and ver_parts[1].isdigit()) else None
    if minor is None and ver_parts[0] == _KNOWN_MAJOR:
        # Fail-closed: «1.x»/«1» при известном major — не тихий
        # перевод пакета в «до-1.2», а нарушение (манифест — един-
        # ственный файл пакета без собственной контрольной суммы).
        problems.append(f"неразбираемая версия спецификации "
                        f"{manifest.get('spec_version')!r}")
    from cdpm.export_task_package import crew_size_of
    derived = crew_size_of(doc)
    if (crew is not None or (minor is not None and minor >= 2)) \
            and derived != crew:
        problems.append(f"manifest.crew_size {crew} ≠ производному "
                        f"из модели {derived}")
    if crew is not None:
        v = crew.get("value") if isinstance(crew, dict) else None
        rid = crew.get("rule") if isinstance(crew, dict) else None
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            problems.append(f"crew_size.value не положительное "
                            f"целое: {v!r}")
        rule_ids = {r.get("id") for r in doc.get("rules", [])}
        if rid not in rule_ids:
            problems.append(f"crew_size.rule {rid!r} не существует "
                            f"в модели")

    # Признак допуска моделей комплекта (дополнение к 1.14, 04.09):
    # ключ необязателен, но присутствующий судится формой — значения
    # из перечней, действующий круг обязателен, у проприетарного
    # класса с кругом any — основание (согласие авиакомпании).
    kit = manifest.get("kit")
    if kit is not None:
        from cdpm.kit import DATA_CLASSES, LLM_SCOPES
        if not isinstance(kit, dict):
            problems.append("manifest.kit не объект")
        else:
            dc = kit.get("data_class")
            if dc is not None and dc not in DATA_CLASSES:
                problems.append(f"kit.data_class {dc!r} вне перечня "
                                f"{list(DATA_CLASSES)}")
            if kit.get("llm_scope") not in LLM_SCOPES:
                problems.append(f"kit.llm_scope {kit.get('llm_scope')!r} "
                                f"вне перечня {list(LLM_SCOPES)}")
            if kit.get("llm_scope_source") not in (
                    "признак комплекта", "умолчание по классу данных",
                    "умолчание без признака", "признак вне перечня"):
                problems.append(f"kit.llm_scope_source "
                                f"{kit.get('llm_scope_source')!r} вне перечня")
            if dc == "proprietary" and kit.get("llm_scope") == "any" \
                    and not str(kit.get("llm_scope_basis") or "").strip():
                problems.append("kit: проприетарный класс с кругом any "
                                "без основания (согласия авиакомпании)")

    # Дополнение к 1.18 (второе, 07.09.2026; шаг 3 переезда на каталог
    # шаблонов): `catalog` — каким каталогом пакет выпущен,
    # `fits_catalog` — сводка ревизий посадок строк. Оба ключа
    # необязательны: пакет до каталога законно идёт без них (прежний
    # закон «каталог не объявлен»). Присутствующие судятся ФОРМОЙ, а
    # сводка — ещё и ПРАВДОЙ: гейт пересчитывает ревизии по строкам
    # model.json пакета. Расхождение ревизии строк с отпечатком каталога
    # — НЕ нарушение (пакет с посадками прежнего каталога законен), об
    # этом говорит сама сводка словами.
    cat = manifest.get("catalog")
    cat_ok = False
    if cat is not None:
        if not isinstance(cat, dict) or set(cat) != {"version",
                                                     "fingerprint"}:
            problems.append("manifest.catalog не объект ровно с ключами "
                            "version и fingerprint")
        else:
            ver_c, fp_c = cat.get("version"), cat.get("fingerprint")
            bad = False
            if not isinstance(ver_c, str) or not ver_c.strip():
                problems.append(f"manifest.catalog.version не непустая "
                                f"строка: {ver_c!r}")
                bad = True
            if not isinstance(fp_c, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", fp_c or ""):
                problems.append(f"manifest.catalog.fingerprint не отпечаток "
                                f"каталога (64 hex): {fp_c!r}")
                bad = True
            cat_ok = not bad
    fits = manifest.get("fits_catalog")
    if fits is not None:
        form_bad = False
        if not isinstance(fits, dict) or set(fits) != {
                "revs", "uniform", "matches_catalog", "note"}:
            problems.append("manifest.fits_catalog не объект ровно с "
                            "ключами revs, uniform, matches_catalog, note")
            form_bad = True
        else:
            if not isinstance(fits.get("revs"), list) or not all(
                    isinstance(x, str) for x in fits["revs"]):
                problems.append("manifest.fits_catalog.revs не список строк")
                form_bad = True
            if not isinstance(fits.get("uniform"), bool):
                problems.append(f"manifest.fits_catalog.uniform не да/нет: "
                                f"{fits.get('uniform')!r}")
                form_bad = True
            if not isinstance(fits.get("matches_catalog"), (bool, type(None))):
                problems.append(f"manifest.fits_catalog.matches_catalog не "
                                f"да/нет и не пусто: "
                                f"{fits.get('matches_catalog')!r}")
                form_bad = True
            if not isinstance(fits.get("note"), (str, type(None))):
                problems.append(f"manifest.fits_catalog.note не слова и не "
                                f"пусто: {fits.get('note')!r}")
                form_bad = True
        # Правда сводки судится только при верной форме — иначе к одному
        # дефекту приписывалось бы второе нарушение теми же словами.
        if not form_bad:
            from cdpm.export_task_package import fits_catalog_of
            want_fits = fits_catalog_of(doc, cat if cat_ok else None)
            if fits != want_fits:
                problems.append(
                    f"manifest.fits_catalog не сходится с посадками пакета "
                    f"(заявлено {fits}, пересчитано по model.json "
                    f"{want_fits})")

    # Ступень 3: каждая пара из logic-examples обязана проходить
    # механический гейт (нарушающее — ровно одно нарушение именно
    # этого правила, соблюдающее — ноль), а перечень недоказанного
    # в манифесте — сходиться с фактом: правило с logic без пары и
    # без записи в logic_unproven — молчаливое «доказано», красный.
    from cdpm import logic_proof as lp_mod
    examples = {}
    if "logic-examples.json" in files:
        try:
            examples = json.loads(
                files["logic-examples.json"].decode("utf-8"))
        except Exception as e:                            # noqa: BLE001
            problems.append(f"logic-examples.json не читается: {e}")
            examples = {}
    declarations = manifest.get("logic_unproven", [])
    if not isinstance(declarations, list):
        problems.append("logic_unproven должен быть списком записей")
        declarations = []
    rule_ids = {r.get("id") for r in doc.get("rules", [])}
    unproven_ids = set()
    declared_reasons = {}
    for entry in declarations:
        rid = entry.get("rule") if isinstance(entry, dict) else None
        if not isinstance(rid, str) or not rid:
            problems.append("logic_unproven: запись без строкового rule")
            continue
        if rid in unproven_ids:
            problems.append(f"{rid}: повтор записи logic_unproven")
        unproven_ids.add(rid)
        if rid not in rule_ids:
            problems.append(f"{rid}: logic_unproven ссылается на чужое правило")
        reason = entry.get("reason")
        declared_reasons[rid] = reason
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{rid}: logic_unproven без непустой причины")
    counters["logic_unproven"] = []
    for r in doc.get("rules", []):
        if not isinstance(r.get("logic"), dict) or r.get("blockers") \
                or r.get("excluded_until_data"):
            # Исключённое до получения данных правило сборщик в
            # примеры не кладёт — гейт судит тем же срезом (аудит №2).
            continue
        rid_l = r.get("id")
        expected_pair, expected_reason = lp_mod.generate_pair(r)
        if expected_pair is None:
            # Never publish untrusted manifest words as the computed reason.
            counters["logic_unproven"].append(
                {"rule": rid_l, "reason": expected_reason})
        if rid_l in unproven_ids:
            if expected_pair is not None:
                problems.append(
                    f"{rid_l}: ложная запись logic_unproven — правило "
                    "доказуемо и обязано нести контрпримерную пару")
            elif declared_reasons[rid_l] != expected_reason:
                problems.append(
                    f"{rid_l}: logic_unproven — причина не совпадает "
                    "с пересчитанной по полному правилу")
        if rid_l in examples:
            problems += lp_mod.check_pair(examples[rid_l], rid_l, rule=r)
        elif rid_l not in unproven_ids:
            problems.append(f"{rid_l}: logic без контрпримерной "
                            f"пары и без записи в logic_unproven — "
                            f"молчаливое «доказано» запрещено")

    has_data = bool(manifest.get("has_data"))
    if has_data != ("clean-data.json" in files):
        problems.append("has_data манифеста расходится с наличием "
                        "clean-data.json")

    # Ревизия 1.12: матрица готовности ВМП. Пакет судится своей
    # версией (у младших отсутствие поля законно), но присутствующая
    # матрица сверяется ВСЕГДА — независимым пересчётом из
    # model.json + упакованного clean-data.json; расхождение =
    # подделка/дрейф состояния готовности (fail-closed, директива
    # владельца 2026-08-29).
    ladder = manifest.get("readiness_matrix")
    if ladder is None and minor is not None and minor >= 12:
        problems.append("readiness_matrix отсутствует в манифесте "
                        "(контракт спецификации 1.12+)")
    if ladder is not None:
        clean = None
        if "clean-data.json" in files:
            try:
                clean = json.loads(
                    files["clean-data.json"].decode("utf-8"))
            except Exception as e:                        # noqa: BLE001
                problems.append(f"clean-data.json не читается: {e}")
        from cdpm.export_task_package import readiness_ladder
        expect = readiness_ladder(
            doc, clean, spec_version=manifest.get("spec_version"))
        if ladder != expect:
            got_states = {t: (c or {}).get("state")
                          for t, c in (ladder.get("columns")
                                       or {}).items()} \
                if isinstance(ladder, dict) else ladder
            want_states = {t: c.get("state")
                           for t, c in expect["columns"].items()}
            problems.append(
                f"readiness_matrix манифеста ≠ пересчёту из "
                f"model.json+clean-data (заявлено {got_states}, "
                f"пересчитано {want_states}) — состояние готовности "
                f"не машинная правда")

    # Ревизия 1.15 (аудит 04.09.2026, ведомость исполнения): у правил
    # пакета — машинная применимость и требуемые данные, у манифеста с
    # данными — отпечаток очищенных данных. Судится ЗАЯВЛЕННАЯ версия:
    # пакет 1.14 без этих полей законен; пакет 1.15 обязан нести их и
    # совпадать с пересчётом из своего же model.json / clean-data.json.
    if minor is not None and minor >= 15:
        from cdpm.export_task_package import (applicability_of,
                                              required_data_of,
                                              data_fingerprint_of)
        for r in doc.get("rules", []):
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if r.get("applicability") != applicability_of(r):
                problems.append(
                    f"правило {rid}: applicability отсутствует или не "
                    f"совпадает с пересчётом (ревизия 1.15)")
            if r.get("required_data") != required_data_of(r):
                problems.append(
                    f"правило {rid}: required_data отсутствует или не "
                    f"совпадает с пересчётом (ревизия 1.15)")
        if has_data:
            fp = manifest.get("data_fingerprint")
            if "clean-data.json" in files:
                clean_text = files["clean-data.json"].decode("utf-8")
                try:
                    want = data_fingerprint_of(clean_text,
                                               json.loads(clean_text))
                except ValueError:
                    want = None
                if fp != want:
                    problems.append(
                        "data_fingerprint манифеста отсутствует или не "
                        "совпадает с пересчётом из clean-data.json "
                        "(ревизия 1.15)")
        elif manifest.get("data_fingerprint") is not None:
            problems.append("data_fingerprint без clean-data.json "
                            "(ревизия 1.15)")
    # Ревизия 1.16 (A9): объявление системы времени сверяется пересчётом
    # из model.json + clean-data.json (ключ отсутствует ⇔ правила шкалы
    # нет); пакеты младше судятся прежним законом.
    if minor is not None and minor >= 16:
        from cdpm.export_task_package import time_declaration_of
        clean_for_time = None
        if "clean-data.json" in files:
            try:
                clean_for_time = json.loads(
                    files["clean-data.json"].decode("utf-8"))
            except ValueError:
                clean_for_time = None
        want_time = time_declaration_of(doc, clean_for_time)
        if manifest.get("time") != want_time:
            problems.append(
                "time манифеста (шкала меток / пояс базы) отсутствует или "
                "не совпадает с пересчётом из model.json + clean-data.json "
                "(ревизия 1.16)")
        # Ревизия 1.17 (A7): раздел истории сотрудника — пересчёт из
        # clean-data.json (ключ есть ⇔ лист «История» есть).
        if minor >= 17:
            from cdpm.history_section import history_section
            want_hist = history_section(clean_for_time, doc) \
                if clean_for_time is not None else None
            if manifest.get("history") != want_hist:
                problems.append(
                    "history манифеста (история сотрудника) отсутствует или "
                    "не совпадает с пересчётом из clean-data.json "
                    "(ревизия 1.17)")
        # Ревизия 1.18.1: объявленный горизонт — пересчёт из «Свойств»
        # clean-data.json с поясом из пересчитанного объявления времени
        # (ключ есть ⇔ оба ключа planSince/planUntil читаемы).
        if minor >= 18:
            from cdpm.history_section import declared_horizon
            want_horizon = declared_horizon(
                clean_for_time, tz=(want_time or {}).get("base_time_zone")) \
                if clean_for_time is not None else None
            if manifest.get("horizon") != want_horizon:
                problems.append(
                    "horizon манифеста (объявленный горизонт) отсутствует или "
                    "не совпадает с пересчётом из clean-data.json "
                    "(ревизия 1.18)")
            # GPT-022 B2: структурная передаваемость и готовность по
            # C-27b — разные вещи. Пакет без объявленного горизонта
            # структурно верен (ключа законно нет), но проверки окон у
            # потребителя дадут только частичную диагностику — гейт
            # говорит это явно, не молча зелёным.
            counters["horizon_declared"] = bool(manifest.get("horizon"))
            if not manifest.get("horizon"):
                counters.setdefault("readiness_notes", []).append(
                    "горизонт не объявлен (лист «Свойства» без planSince/"
                    "planUntil, ревизия 1.18.1): пакет передаваем, но "
                    "проверки окон у потребителя — только частичная "
                    "диагностика по назначениям, полного зелёного "
                    "результата по ним не будет")

    # Ревизия 1.14 (пакет Б): секция rule_maps. Форма судится всегда;
    # равенство носителю — только там, где носитель контура доступен
    # (замороженный пакет на чужой машине судится формой — карта
    # приезжает пакетом, носителя там законно нет).
    maps = manifest.get("rule_maps")
    if maps is not None:
        from cdpm.export_task_package import (_RULE_MAPS_SECTIONS,
                                              _rule_maps_for)
        if not isinstance(maps, dict) or not maps:
            problems.append("rule_maps: секция пуста или не объект")
        else:
            for k in maps:
                if k not in _RULE_MAPS_SECTIONS:
                    problems.append(
                        f"rule_maps: неизвестная подсекция {k!r}")
            try:
                expect_maps = _rule_maps_for(doc)
            except Exception as e:                        # noqa: BLE001
                problems.append(f"rule_maps: носитель контура "
                                f"кривой: {e}")
                expect_maps = None
            if expect_maps is not None and expect_maps != maps:
                problems.append(
                    "rule_maps манифеста ≠ носителю контура — карта "
                    "не машинная правда")

    # Аудит 2026-09-04 (п. 2, замок содержания): measure и coverage
    # манифеста — не справка. Гейт ПЕРЕСЧИТЫВАЕТ вердикт и счётчики
    # измерителя из протокола пакета (measure-protocol.json) и краснит
    # расхождение с манифестом, measure.ok=false и ненулевой остаток
    # замка содержания (величины без адресата, нормативные носители без
    # строк). Комплекта заморозки в пакете нет — пересчёт по протоколу,
    # который сборщик обязан приложить при любом покрытии клауз.
    problems += _check_measure(files, manifest)
    problems += _check_consistency(manifest, counters, files, doc)
    # Барьеры 17а (A05/K01/K06): те же расчёты, что у рубежа выпуска и
    # готовности, на model.json пакета — теми же словами.
    problems += _check_barriers(doc, manifest, counters)
    from cdpm.family_manifest import check as check_families
    problems += [f"{p['words']} (код {p['code']})"
                 for p in check_families(doc, manifest)]
    from cdpm.support_manifest import check as check_support
    problems += [f"{p['words']} (код {p['code']})"
                 for p in check_support(doc, manifest)]
    return problems, counters


def _check_barriers(doc: dict, manifest: dict, counters: dict) -> list[str]:
    """Барьеры волны 17а на пакете (контракт П0 §3.1, §3.2, §3.5; закон
    0.4 «одинаковый вердикт у производителя и приёмника»):

    - A05 — носители параметра правила (ячейка посадки и `params[]`)
      расходятся (`model.reconcile_carriers`): красный всегда — рубеж
      выпуска такой пакет не выпускает (код `param-carriers-diverge`);
    - K01 — обязательный параметр использованной семьи не задан и не
      отклонён (`family_params.family_completeness`), K06 — семья без
      подтверждённой поддержки исполнителя (`catalog_bridge.
      family_support`): красный, если манифест РАЗРЕШАЕТ режим задачи,
      который эта проблема держит (жёсткие носители — все режимы
      задачи, мягкие — режимы со штрафами); задача, снятая матрицей
      целиком, — законная частичная передача, и слова её оснований
      видны в манифесте — в `readiness_matrix.columns.<задача>.notes`
      строками «барьер: …» (С-2 неавтор-ревью 12.09.2026: в счётчик
      `grounds.questions.open_blocking` барьер не входит — он не вопрос
      анкеты). Здесь же они ложатся в счётчик гейта `barriers_held`.

    Расчёт полноты и поддержки требует каталога того же отпечатка и
    свидетельства поддержки на машине гейта: без каталога гейт уже
    красен словами валидатора модели."""
    problems: list[str] = []
    for d in m.carrier_problems(doc):
        problems.append(
            f"model.json: {d['words']} — у параметра один носитель "
            f"истины, рубеж выпуска такой пакет не выпускает "
            f"(код {m.PARAM_CARRIERS_DIVERGE})")
    allowed = manifest.get("allowed_modes") \
        if isinstance(manifest.get("allowed_modes"), dict) else {}
    held: list[str] = []
    for pr in m.readiness_barriers(doc, manifest.get("spec_version")):
        hit = sorted({task for task, mode in m.barrier_affects(pr)
                      if mode in (allowed.get(task) or [])})
        if hit:
            problems.append(
                f"model.json: {pr['words']} — а манифест разрешает режимы "
                f"задач {hit} (правила {', '.join(pr.get('rules') or [])}; "
                f"код {pr.get('code')})")
        else:
            held.append(str(pr["words"]))
    if held:
        counters["barriers_held"] = held
    return problems


    return problems, counters


#: Форма ключа `manifest.consistency` (Дополнение к 1.18, третье; с
#: 12.09.2026 — предмет проверки A01-F1): ровно эти поля и ровно эти
#: вердикты — иное читается как подделка справки о непротиворечивости.
_CONSISTENCY_KEYS = {"verdict", "model_sha256", "n_rules", "n_translated",
                     "translator", "report", "subject_sha256", "subject"}
#: Форма ключа до 12.09.2026 — без предмета проверки и без отчёта в
#: пакете: сверить справку с набором жёстких правил пакета по ней
#: нельзя, слова отказа называют это отдельно.
_CONSISTENCY_KEYS_V1 = _CONSISTENCY_KEYS - {"subject_sha256", "subject"}
_CONSISTENCY_VERDICTS = ("sat", "none")


def _check_consistency(manifest: dict, counters: dict,
                       files: dict[str, bytes], doc: dict) -> list[str]:
    """Непротиворечивость свода в манифесте (СМТ-1, 11.09.2026): гейт
    производителя судит ФОРМУ ключа, вердикт и — с 12.09.2026 (A01-F1
    аудита) — СВЯЗЬ справки с фактически выпущенным набором правил.

    Вердикт решателя по пакету не пересчитывается (переводчика у гейта
    нет), но пакет несёт копию отчёта, которым вердикт вынесен
    (`smt-report.json`), и гейт сверяет:
    - `subject_sha256` манифеста с отпечатком жёстких правил model.json
      ПАКЕТА (`smt_verify.subject_fingerprint`) — справка о другом
      наборе правил красная;
    - строки `translations[]` отчёта с точным набором жёстких правил
      model.json пакета (`report_completeness`): жёсткое правило пакета
      без строки `translated`, чужая, повторная строка, строка без тела,
      запись вне формы в ответе переводчика (A01-F2) — красные;
    - счётчики манифеста с пересчётом по отчёту и model.json, вердикт
      отчёта с вердиктом манифеста, отпечаток модели отчёта с
      манифестом и отпечаток ПРЕДМЕТА отчёта — с пересчитанным по
      пакету (обязателен: без него строки перевода привязаны лишь к
      именам правил, но не к их формулировкам).
    Пакет с вердиктом, которого не бывает у выпущенного пакета
    (`unsat`, `unknown`, пусто), — красный: рубеж выпуска такой пакет не
    выпускает, значит, справка подделана или пакет собран в обход
    рубежа. Ключа нет — пакет выпущен до рубежа и судится прежним
    законом; ключ формы до 12.09.2026 (без предмета и отчёта в пакете)
    — красный словами: сверить его с набором правил пакета нельзя,
    пакет надо выпустить заново."""
    problems: list[str] = []
    if "stand_only" in manifest:
        counters["consistency"] = "только стенд"
        return ["stand-only: стендовый пакет с исключением SMT не допускается "
                "в клиентский канал; выпустите настоящий пакет с полным SMT-переводом"]
    cons = manifest.get("consistency")
    if cons is None:
        counters["consistency"] = "не объявлена"
        return problems
    if not isinstance(cons, dict) or set(cons) != _CONSISTENCY_KEYS:
        counters["consistency"] = "форма нарушена"
        old_form = isinstance(cons, dict) and set(cons) == _CONSISTENCY_KEYS_V1
        return ["manifest.consistency не объект ровно с ключами "
                + ", ".join(sorted(_CONSISTENCY_KEYS))
                + (" (форма до 12.09.2026: без предмета проверки "
                   "subject_sha256/subject и без отчёта smt-report.json "
                   "в пакете — сверить справку с набором жёстких правил "
                   "пакета нельзя; выпустите пакет заново)"
                   if old_form else "")]
    verdict = cons.get("verdict")
    counters["consistency"] = str(verdict)
    if verdict not in _CONSISTENCY_VERDICTS:
        problems.append(
            f"manifest.consistency.verdict вне перечня "
            f"{list(_CONSISTENCY_VERDICTS)}: {verdict!r} — пакет с таким "
            f"вердиктом рубеж выпуска не выпускает")
    sha = cons.get("model_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        problems.append(f"manifest.consistency.model_sha256 не sha256 "
                        f"канонической модели: {sha!r}")
    counts = {}
    for key in ("n_rules", "n_translated"):
        val = cons.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            problems.append(f"manifest.consistency.{key} не целое число "
                            f"правил: {val!r}")
        else:
            counts[key] = val
    if len(counts) == 2:
        if counts["n_translated"] > counts["n_rules"]:
            problems.append(
                f"manifest.consistency: переведённых утверждений "
                f"{counts['n_translated']} больше жёстких правил "
                f"{counts['n_rules']} — счёт не машинная правда")
        elif verdict == "sat" and counts["n_translated"] < counts["n_rules"]:
            # A01: «sat» по части свода рубеж выпуска не выпускает
            # (код smt-incomplete) — справка с таким счётом подделана
            # или пакет собран в обход рубежа.
            problems.append(
                f"manifest.consistency: вердикт «sat» при переведённых "
                f"{counts['n_translated']} из {counts['n_rules']} жёстких "
                f"правил — «sat» требует полного перевода")
        if verdict == "none" and counts["n_rules"]:
            problems.append(
                f"manifest.consistency: вердикт «none» (проверять нечего) "
                f"при {counts['n_rules']} жёстких правилах")
        if verdict == "sat" and not counts["n_translated"]:
            problems.append(
                "manifest.consistency: вердикт «sat» без единого "
                "переведённого утверждения — проверен пустой свод")
    # Имя роли и путь отчёта берутся у самой проверки (один носитель),
    # а не переписываются в гейт второй раз.
    from cdpm import smt_verify as smt_mod
    from cdpm.export_task_package import SMT_REPORT_NAME
    tr = cons.get("translator")
    if not isinstance(tr, dict) or set(tr) != {"role", "provider", "model"}:
        problems.append("manifest.consistency.translator не объект ровно с "
                        "ключами role, provider, model")
    elif tr.get("role") != smt_mod.TRANSLATOR_ROLE:
        problems.append(f"manifest.consistency.translator.role не роль "
                        f"переводчика SMT: {tr.get('role')!r}")
    # A01-F1: предмет проверки — какой отчёт (кнопки о каноническом своде
    # либо пакета о выпускаемом) и отпечаток жёстких правил model.json
    # ПАКЕТА; путь отчёта обязан отвечать предмету.
    subject_remedy = ("пересоберите справку по выпускаемому набору жёстких "
                      "правил и выпустите пакет заново")
    subject = cons.get("subject")
    want_report = {smt_mod.CANONICAL: smt_mod.REPORT_REL,
                   smt_mod.PACKAGE: smt_mod.PACKAGE_REPORT_REL}.get(subject)
    if want_report is None:
        problems.append(f"manifest.consistency.subject вне перечня "
                        f"[{smt_mod.CANONICAL!r}, {smt_mod.PACKAGE!r}]: "
                        f"{subject!r}; {subject_remedy}")
    elif cons.get("report") != want_report:
        problems.append(f"manifest.consistency.report не указывает на отчёт "
                        f"проверки прогона для предмета {subject!r} "
                        f"({want_report!r}): {cons.get('report')!r}; {subject_remedy}")
    ssha = cons.get("subject_sha256")
    if not isinstance(ssha, str) or not re.fullmatch(r"[0-9a-f]{64}", ssha):
        problems.append(f"manifest.consistency.subject_sha256 не sha256 "
                        f"предмета проверки: {ssha!r}; {subject_remedy}")
    else:
        want_sha = smt_mod.subject_fingerprint(doc)
        if ssha != want_sha:
            problems.append(
                f"manifest.consistency.subject_sha256 не совпадает с "
                f"отпечатком жёстких правил model.json пакета — справка о "
                f"ДРУГОМ наборе правил (в манифесте {ssha[:12]}…, по пакету "
                f"{want_sha[:12]}…); {subject_remedy}")
    # Строки отчёта из пакета против жёстких правил model.json пакета:
    # счётчикам манифеста гейт не верит.
    raw = files.get(SMT_REPORT_NAME)
    if raw is None:
        problems.append(
            f"{SMT_REPORT_NAME} отсутствует в пакете — строки перевода "
            f"сверить с жёсткими правилами model.json пакета нельзя")
        return problems
    try:
        # Apply the same raw-evidence boundary as the local report reader.
        report = m.loads_strict(raw)
        if not isinstance(report, dict):
            raise ValueError("не объект")
    except Exception as e:                                # noqa: BLE001
        problems.append(f"{SMT_REPORT_NAME} не читается: {e}")
        return problems
    if report.get("schema_version") != smt_mod.REPORT_SCHEMA:
        problems.append(
            f"{SMT_REPORT_NAME}: версия схемы отчёта "
            f"{report.get('schema_version')!r}, ожидается "
            f"{smt_mod.REPORT_SCHEMA!r}")
    if report.get("model_sha256") != cons.get("model_sha256"):
        problems.append(
            f"{SMT_REPORT_NAME}: отчёт о другом каноническом своде "
            f"(model_sha256 отчёта не равен manifest.consistency."
            f"model_sha256)")
    # Предмет отчёта обязателен и сверяется с ПЕРЕСЧИТАННЫМ по пакету
    # отпечатком (выше): именно он связывает строки перевода не только с
    # именами правил model.json пакета, но и с их ФОРМУЛИРОВКАМИ —
    # отчёт, не назвавший своего предмета, о выпущенном своде не
    # свидетельствует (отчёт без него — форма до 12.09.2026).
    if report.get("subject_sha256") != ssha:
        problems.append(
            f"{SMT_REPORT_NAME}: отчёт не о том предмете проверки, что "
            f"назван в манифесте и пересчитан по model.json пакета "
            f"(subject_sha256 отчёта {report.get('subject_sha256')!r}); "
            f"{subject_remedy}")
    completeness = smt_mod.report_completeness(report, doc)
    n_hard = completeness["n_rules"]
    if completeness["missing"]:
        problems.append(
            "жёсткие правила model.json пакета без перевода в отчёте "
            f"{SMT_REPORT_NAME} (строки translated нет, untranslatable, "
            f"missing, повтор или тело без S-выражения): "
            + ", ".join(completeness["missing"][:12]))
    if completeness["foreign"]:
        problems.append(
            f"{SMT_REPORT_NAME}: записи, которым правило model.json пакета "
            f"не соответствует (чужое правило; запись ответа переводчика "
            f"без идентификатора названа позицией): "
            + ", ".join(completeness["foreign"][:12]))
    if completeness["duplicate"]:
        problems.append(f"{SMT_REPORT_NAME}: повторные строки перевода: "
                        + ", ".join(completeness["duplicate"][:12]))
    if completeness["bad"]:
        problems.append(f"{SMT_REPORT_NAME}: строки translated без "
                        f"S-выражения либо с true: "
                        + ", ".join(completeness["bad"][:12]))
    if completeness["malformed"]:
        problems.append(
            f"{SMT_REPORT_NAME}: записи вне формы в ответе переводчика "
            f"(A01-F2) — такой ответ переводом не считается: "
            + "; ".join(completeness["malformed_words"][:12]))
    rv = report.get("verdict")
    if verdict == "sat" and rv != "sat":
        problems.append(f"{SMT_REPORT_NAME}: вердикт отчёта {rv!r}, а "
                        f"манифест объявляет sat")
    if verdict == "none" and n_hard:
        problems.append(
            f"manifest.consistency: вердикт «none» (проверять нечего) при "
            f"{n_hard} жёстких правилах model.json пакета: "
            + ", ".join(r["id"] for r in smt_mod.subject_of(doc)[:12]))
    if len(counts) == 2 and (counts["n_rules"] != n_hard or
                             counts["n_translated"]
                             != completeness["n_translated"]):
        problems.append(
            f"manifest.consistency: счётчики {counts['n_rules']}/"
            f"{counts['n_translated']} не равны пересчёту по отчёту и "
            f"model.json пакета {n_hard}/{completeness['n_translated']}")
    return problems


def _kit_names(files: dict[str, bytes]) -> list[str]:
    """Записи комплекта заморозки, ПРИЕХАВШИЕ С ПАКЕТОМ, — по имени
    файла и только по нему: ровно эти файлы сборщик считает в
    manifest.freeze_clauses (export_task_package), то есть сам объявляет
    их комплектом. Одно определение «комплект в пакете есть» на всех
    читателей гейта."""
    return sorted(n for n in files
                  if n.startswith("freeze/clauses/") and n.endswith(".json"))


def _package_kit(files: dict[str, bytes]) -> tuple[list[dict] | None,
                                                   list[str]]:
    """Комплект заморозки САМОГО ПАКЕТА — записи freeze/clauses/<sid>.json
    (едут с пакетом с дополнения к 1.17). Якоря судит только запись
    ФОРМЫ КОМПЛЕКТА {sid, clauses: [{anchor, text, …}]} — ровно в этом
    виде их пишет cdpm.freeze; записей иной формы возвращается None
    (якоря судить нечем).

    НО «иной формы» НЕ ЗНАЧИТ «комплекта нет» (ревью A11-2, находка
    Н-2): файл freeze/clauses/*.json комплект ОБЪЯВЛЯЕТ, и запись не
    формы комплекта — красное слово о форме записи. Молчаливый выход по
    форме записи давал зелёное пакету с висячим якорем, стоило
    переписать записи списком клауз либо отдать clauses не списком и
    снять покрытие с измерением (пробы П1-d, П1-g).

    Возвращает (записи формы комплекта либо None, слова о форме)."""
    kit: list[dict] | None = None
    problems: list[str] = []
    for name in _kit_names(files):
        try:
            got = json.loads(files[name].decode("utf-8"))
        except Exception as e:                            # noqa: BLE001
            problems.append(f"запись комплекта заморозки {name} не "
                            f"читается как JSON ({type(e).__name__}: {e}) "
                            f"— якоря по ней не судятся")
            continue
        if isinstance(got, dict) and isinstance(got.get("clauses"), list):
            kit = (kit or []) + [{
                "sid": got.get("sid") or name[len("freeze/clauses/"):-5],
                "clauses": got["clauses"]}]
        else:
            problems.append(
                f"запись комплекта заморозки {name} не формы комплекта "
                f"{{sid, clauses: [{{anchor, …}}]}} "
                f"({type(got).__name__}) — якоря по ней не судятся")
    return kit, problems


def _package_doc(files: dict[str, bytes]) -> dict:
    try:
        doc = json.loads(files["model.json"].decode("utf-8"))
    except Exception:                                     # noqa: BLE001
        return {}
    return doc if isinstance(doc, dict) else {}


def _recount_problems(files: dict[str, bytes], proto: dict,
                      kit: list[dict] | None) -> list[str]:
    """ПЕРЕСЧЁТ ИЗМЕРИТЕЛЯ ПО САМОМУ ПАКЕТУ (A11, ревью п. 4): протокол
    измерителя — производный артефакт, и гейт не вправе верить ему на
    слово, сверив одни тождества. Измеритель заново читает model.json и
    freeze/clauses пакета и сверяется с протоколом ПО ТЕМ ЧАСТЯМ, что от
    вердиктов аудитора НЕ ЗАВИСЯТ: состав носителей, строки на клаузах,
    число величин и потреблённых, сам остаток, нормативность,
    открытые вопросы на носителе, строки без клаузы-источника и
    вердикт-независимые счётчики. Расхождение — подделка протокола либо
    подмена свода/клауз под готовый протокол (подделанный пакет).

    ФОРМА ЧИТАЕМОГО СУДИТСЯ ТЕМ ЖЕ ЗАКОНОМ, ЧТО В content_layer (ревью
    A11-2, находка Н-1): всякий перечень протокола, который пересчёт
    читает, — сам перечень клауз, строки без клаузы-источника, адресаты
    остатка на записи — проверяется на форму (coverage._protocol_rows /
    _listed), и порок называется КРАСНЫМ СЛОВОМ. Не следом («х» вместо
    перечня — не пустой перечень), не молчанием и не падением гейта:
    одного try вокруг measure() мало, потому что измеритель считает по
    ПАКЕТУ, а роняли гейт перечни ПРОТОКОЛА (TypeError на clauses: 5,
    orphan_rows: 5, unconsumed_addressed: 5 — проба П2-gate).

    ОСТАТОК, КОТОРЫЙ ГЕЙТУ НЕДОСТУПЕН (называется в руководстве):
    вердиктов аудитора (exchange/coverage.json) в пакете нет, поэтому
    адресат остатка ВЕРДИКТОМ и признак «не правило» подтвердить нечем
    — их гейт принимает как заявлено. Адресатов-ВОПРОСОВ это не
    касается: вопросы едут в model.json, и заявленный вопрос-адресат,
    которого пересчёт не подтверждает, краснится здесь."""
    from cdpm import coverage as cov_mod
    from cdpm import measure as ms_mod
    doc = _package_doc(files)
    clauses = [{"sid": e.get("sid"), "anchor": c.get("anchor"),
                "text": c.get("text") or ""}
               for e in kit or () for c in e.get("clauses") or ()
               if isinstance(c, dict)]
    try:
        mine = ms_mod.measure(doc, clauses)
    except Exception as e:                                # noqa: BLE001
        return [f"пересчёт измерителя по самому пакету не отработал "
                f"({type(e).__name__}: {e}) — протокол проверить нечем"]
    form: list[str] = []
    told = {str(r.get("clause")): r
            for r in cov_mod._protocol_rows(proto, form)}
    ours = {str(r["clause"]): r for r in mine["clauses"]}
    diff: list[str] = []
    # A03: answer evidence is derived from this package's model, not from
    # the report's own claim. Keep the structural clause judgement separate.
    actual_answers = mine["answer_carriers"]
    stated_answers = proto.get("answer_carriers")
    def answer_names(rows):
        names = sorted({f"{r.get('rule_id', '?')}/{r.get('field', '?')}" for r in rows})
        return ", ".join(names[:5]) + (f" и ещё {len(names) - 5}" if len(names) > 5 else "")
    if "answer_carriers" not in proto and not actual_answers:
        stated_answers = []  # Old protocols without answer cells stay valid.
    if not isinstance(stated_answers, list) or any(
            not isinstance(row, dict) for row in stated_answers):
        form.append("answer_carriers протокола не список записей происхождения"
                    + ("; носители модели: " + answer_names(actual_answers) if actual_answers else ""))
    else:
        # Canonical JSON keeps bool/int distinct; order is not evidence.
        from collections import Counter
        def answer_rows(rows):
            return Counter(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        expected, declared = answer_rows(actual_answers), answer_rows(stated_answers)
        if expected != declared:
            changed = (expected - declared) + (declared - expected)
            diff.append("answer_carriers протокола ≠ точным источникам и "
                        "результатам проверки ответов самого пакета; носители: "
                        + answer_names([json.loads(row) for row in changed]))
    lost = sorted(set(ours) - set(told))
    alien = sorted(set(told) - set(ours))
    if lost:
        diff.append(f"носителей пересчёта нет в протоколе {len(lost)}: "
                    + ", ".join(lost[:5]))
    if alien:
        diff.append(f"носителей протокола нет в пересчёте {len(alien)}: "
                    + ", ".join(alien[:5]))

    def _texts(row, key):
        got = row.get(key)
        return sorted(str(v.get("text") if isinstance(v, dict) else v)
                      for v in got) if isinstance(got, list) else None

    for key in sorted(set(ours) & set(told)):
        a, b = ours[key], told[key]
        for field in ("normative", "values", "consumed", "unclassified"):
            if a.get(field) != b.get(field):
                diff.append(f"{key}: {field} пересчёта {a.get(field)!r} ≠ "
                            f"протоколу {b.get(field)!r}")
        for field in ("rows", "members", "questions_open"):
            if _texts(a, field) != _texts(b, field):
                diff.append(f"{key}: {field} пересчёта {_texts(a, field)} "
                            f"≠ протоколу {_texts(b, field)}")
        if _texts(a, "unconsumed") != _texts(b, "unconsumed"):
            diff.append(f"{key}: остаток величин пересчёта "
                        f"{_texts(a, 'unconsumed')} ≠ протоколу "
                        f"{_texts(b, 'unconsumed')}")
        # Адресат-ВОПРОС верифицируем (вопросы в model.json); адресат
        # ВЕРДИКТОМ — нет (coverage.json в пакет не едет).
        claimed_raw = b.get("unconsumed_addressed")
        if claimed_raw is not None and not isinstance(claimed_raw, list):
            form.append(f"перечень адресатов остатка «{key}» отчёта "
                        f"измерителя не список "
                        f"({type(claimed_raw).__name__})")
            claimed_raw = None
        claimed = [str(x) for x in (claimed_raw or ())
                   if not str(x).startswith(ms_mod.VERDICT_ADDRESSEE)]
        real = {str(x) for x in (a.get("unconsumed_addressed") or ())}
        ghost = sorted(set(claimed) - real)
        if ghost:
            diff.append(f"{key}: протокол числит адресатами остатка "
                        f"вопросы, которых пересчёт не подтверждает: "
                        + ", ".join(ghost[:5]))
    ours_orphan = {str(x.get("id")) for x in mine["orphan_rows"]}
    told_orphan_raw = cov_mod._listed(proto, "orphan_rows", form)
    told_orphan_rows = [x for x in (told_orphan_raw or ())
                        if isinstance(x, dict)]
    if told_orphan_raw is not None \
            and len(told_orphan_rows) != len(told_orphan_raw):
        form.append(f"записей перечня «orphan_rows» отчёта измерителя не "
                    f"словарями {len(told_orphan_raw) - len(told_orphan_rows)}"
                    f" из {len(told_orphan_raw)}")
    told_orphan = {str(x.get("id")) for x in told_orphan_rows}
    if ours_orphan != told_orphan:
        diff.append(f"строки без клаузы-источника: пересчёт "
                    f"{sorted(ours_orphan)[:5]} ≠ протоколу "
                    f"{sorted(told_orphan)[:5]}")
    told_counts = proto.get("counts")
    told_counts = told_counts if isinstance(told_counts, dict) else {}
    for name in ("clauses", "carriers", "carriers_composed",
                 "clauses_with_rows", "rows", "rows_orphan",
                 "rows_on_nonnormative", "fields_without_carrier",
                 "fields_without_carrier_slot", "unconsumed_values",
                 "questions", "unclassified_tokens"):
        if mine["counts"][name] != told_counts.get(name):
            diff.append(f"счётчик «{name}»: пересчёт "
                        f"{mine['counts'][name]} ≠ протоколу "
                        f"{told_counts.get(name)!r}")
    words = [f"пересчёт измерителя по самому пакету: {w}" for w in form]
    if diff:
        words.append(f"протокол измерителя ≠ пересчёту по самому пакету "
                     f"(расхождений {len(diff)}): " + "; ".join(diff[:5]))
    return words


def _bijection_facts_of(files: dict[str, bytes], proto: dict | None,
                        kit: list[dict] | None) -> dict:
    """Факты биекции ИЗ САМОГО ПАКЕТА тем же расчётом, что у панели и
    замка выпуска (coverage.bijection_facts, A11): свод — model.json,
    содержание — протокол измерителя, якоря — записи
    freeze/clauses/<sid>.json (едут с пакетом с дополнения к 1.17;
    судится запись формы комплекта {sid, clauses: [{anchor, …}]}, иная
    форма якоря не судит — anchors_judged=false). Вердиктов
    coverage.json в пакете нет — клаузы без вердикта пересчёту
    недоступны (заявленный manifest.coverage.missing гейт не
    подтверждает и не опровергает)."""
    from cdpm import coverage as cov
    return cov.bijection_facts(_package_doc(files), None,
                               measure_report=proto, kit_entries=kit)


def _check_measure(files: dict[str, bytes], manifest: dict) -> list[str]:
    from cdpm import measure as ms_mod
    problems: list[str] = []
    facts: dict | None = None
    meas = manifest.get("measure")
    cov_m = manifest.get("coverage")
    proto_raw = files.get("measure-protocol.json")
    # A11 (ревью п. 4): судьями строк и якорей служат КЛАУЗЫ ЗАМОРОЗКИ
    # САМОГО ПАКЕТА, а не заявления манифеста о покрытии: иначе пакет с
    # клаузами и висячим якорем проходил зелёным, стоило удалить из
    # манифеста ключи coverage/measure и файл протокола (пробы A4/A5).
    # Честная сборка пишет комплект, покрытие и измерение ВМЕСТЕ
    # (export_task_package), поэтому рассогласование заявлений — само
    # по себе красное слово.
    # «Клаузы заморозки в пакете есть» = есть ФАЙЛ freeze/clauses/*.json
    # (Н-2): именно эти файлы сборщик считает в manifest.freeze_clauses,
    # то есть сам объявляет их комплектом. Якоря судят только записи
    # ФОРМЫ КОМПЛЕКТА ({sid, clauses: [{anchor, …}]}, как их пишет
    # cdpm.freeze); запись иной формы — красное СЛОВО О ФОРМЕ, а не
    # «комплекта нет»: иначе переписанная запись покупала зелёное
    # пакету с висячим якорем (пробы П1-d, П1-g).
    kit, kit_form = _package_kit(files)
    problems += kit_form
    kit_present = bool(_kit_names(files))
    declared_kit = manifest.get("freeze_clauses")
    if isinstance(declared_kit, dict):
        # Сводка манифеста о клаузах — про ЭТОТ пакет: сколько записей
        # объявлено, столько и едет (иначе комплект вынут после сборки).
        in_pkg = sorted(n[len("freeze/clauses/"):-5]
                        for n in _kit_names(files))
        want = [str(s) for s in (declared_kit.get("sids") or [])]
        if in_pkg != sorted(want):
            problems.append(
                f"manifest.freeze_clauses объявляет записи {sorted(want)}, "
                f"а в пакете {in_pkg} — комплект заморозки не тот, что "
                f"объявлен")
    # Заявление о покрытии и условие судейства — ОДНО (Н-3): покрытием
    # объявлено всякое НЕ-ПУСТОЕ значение ключа, какого бы вида оно ни
    # было. Условие `isinstance(cov_m, dict)` было уже условия
    # `cov_m is not None`, и в зазор проходил пакет с coverage вида
    # [] / 0 / "x" / true: заявление есть, а судейство строк и якорей
    # молчит (проба П1-e). Порочный вид назван словом, а судится пакет
    # как заявивший покрытие.
    if cov_m is not None and not isinstance(cov_m, dict):
        problems.append(
            f"coverage манифеста не объект ({type(cov_m).__name__}) — "
            f"сводка покрытия клауз не машинная правда")
    available = isinstance(meas, dict) and bool(meas.get("available"))
    if meas is None and cov_m is None and proto_raw is None \
            and not kit_present:
        return problems                     # сборка без заморозки/легаси
    if kit_present and cov_m is None and not available:
        problems.append(
            "клаузы заморозки в пакете есть, а покрытие клауз "
            "(manifest.coverage) и измерение постановки "
            "(manifest.measure) не заявлены — честная сборка пишет их "
            "вместе: пакет собран в обход замка биекции либо манифест "
            "подделан")
    if cov_m is not None and not available:
        problems.append(
            "coverage манифеста есть, а measure.available нет — пакет "
            "с покрытием клауз обязан нести измерение постановки")
    if available and cov_m is None:
        problems.append(
            "measure.available заявлено, а coverage манифеста нет — "
            "измерение и покрытие клауз выпускаются вместе")
    # Протокол, годный к чтению измерителем; None — содержания им не
    # судить. Порок протокола РАССКАЗАН, но судейство строк и якорей
    # (ниже) на нём не обрывается: нечитаемое свидетельство одного слоя
    # не отменяет фактов, которые видны в самом пакете (ревью п. 1).
    proto: dict | None = None
    if available and proto_raw is None:
        problems.append("measure.available заявлено, а "
                        "measure-protocol.json в пакете нет")
    elif available:
        try:
            proto = json.loads(proto_raw.decode("utf-8"))
            ok, ms_problems = ms_mod.verdict(proto)
        except Exception as e:                            # noqa: BLE001
            problems.append(f"measure-protocol.json не читается "
                            f"измерителем: {e}")
            proto = None
    if proto is not None:
        if bool(meas.get("ok")) != ok or \
                list(meas.get("problems") or []) != list(ms_problems):
            problems.append(
                f"measure манифеста ≠ пересчёту по протоколу "
                f"(заявлено ok={meas.get('ok')}, пересчитано ok={ok}) — "
                f"вердикт измерителя не машинная правда")
        if (proto.get("counts") or {}) != (meas.get("counts") or {}):
            problems.append("measure.counts манифеста ≠ счётчикам "
                            "протокола измерителя")
        if not ok:
            problems.append(
                "измеритель постановки: структурные дефекты свода "
                "(measure.ok=false) — пакет к запуску не готов: "
                + "; ".join(ms_problems[:3]))
        # A11: слой содержания — тем же единым расчётом фактов биекции,
        # что у панели и замка выпуска (coverage.bijection_facts: слова
        # measure.content_lock по протоколу + частично покрытые клаузы).
        # Порок самого свидетельства (вид «form»: испорченная форма,
        # счётчик против перечня, протокол не о клаузах этого пакета) —
        # красное слово: подделка одного счётчика зелёного не даёт.
        facts = _bijection_facts_of(files, proto, kit)
        for p in facts["problems_by_kind"]["content"]:
            problems.append("замок содержания по протоколу: " + p)
        for p in facts["problems_by_kind"]["form"]:
            problems.append("замок биекции по пакету: " + p)
        # A11 (ревью п. 4): протокол — производный артефакт, и тождества
        # ключей его не удостоверяют; измеритель пересчитывается по
        # самому пакету, и вердикт-независимые части обязаны сойтись.
        if facts["anchors_judged"]:
            problems += _recount_problems(files, proto, kit)
        if isinstance(cov_m, dict):
            c = proto.get("counts") or {}
            for key_m, key_p in (("values_without_addressee",
                                  "unconsumed_without_addressee"),
                                 ("normative_without_rows",
                                  "normative_without_rows")):
                if key_m in cov_m and int(cov_m[key_m]) != int(c.get(key_p) or 0):
                    problems.append(
                        f"coverage.{key_m} манифеста ({cov_m[key_m]}) ≠ "
                        f"протоколу измерителя ({c.get(key_p)})")
    if not available and proto_raw is not None:
        problems.append("measure-protocol.json в пакете есть, а манифест "
                        "измерение не заявляет")
    # A11: строки без источника и висячие якоря — из свода и клауз
    # заморозки САМОГО ПАКЕТА, тем же расчётом. Судятся по наличию
    # клауз заморозки или заявленного покрытия, а не по одному лишь
    # манифесту: удаление ключей манифеста зелёного не покупает
    # (ревью п. 1, пробы A4/A5). Клаузы заявлены, а записей формы
    # комплекта в пакете нет — якоря судить нечем, и это красное слово,
    # не тишина.
    if kit_present or cov_m is not None:
        if facts is None:
            facts = _bijection_facts_of(files, None, kit)
            for p in facts["problems_by_kind"]["form"]:
                problems.append("замок биекции по пакету: " + p)
        for p in (facts["problems_by_kind"]["rows"]
                  + facts["problems_by_kind"]["anchors"]):
            problems.append("замок биекции по пакету: " + p)
        if not facts["anchors_judged"]:
            problems.append(
                "замок биекции по пакету: клаузы заморозки заявлены "
                "(freeze_clauses манифеста либо покрытие клауз), а "
                "записей freeze/clauses/<sid>.json формы комплекта в "
                "пакете нет — висячие якоря судить нечем")
    return problems


def run(package: str, *, landing_compat=None) -> int:
    _selftest()
    problems, c = check(Path(package), landing_compat=landing_compat)
    print(f"рассмотрено: файлов {c['files']}, правил {c['rules']}, "
          f"целевых {c['objectives']}, разрешённых режимов "
          f"{c['modes']}, непротиворечивость "
          f"{c.get('consistency', 'не судилась')} (самотест пройден)")
    from cdpm import logic_proof as lp_mod
    print(lp_mod.render_unproven_summary(c.get("logic_unproven")))
    from vmp import objective_projection as op
    if not op.judged_for_spec(c.get("spec_version")):
        print(op.archive_words(c["spec_version"]))
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    if c.get("landing_archive_only"):
        print("ВЕРДИКТ: архивное чтение landing/1; исполнение запрещено")
        return 0
    print(f"ВЕРДИКТ: пакет постановки соответствует спецификации "
          f"{c['spec_version']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Гейт пакета постановки задачи COSMOS")
    ap.add_argument("package", help="zip-архив или каталог пакета")
    ap.add_argument("--landing-compat", type=int, choices=[1],
                    help="явное архивное чтение landing/1; не разрешает исполнение")
    args = ap.parse_args(argv)
    return run(args.package, landing_compat=args.landing_compat)


if __name__ == "__main__":
    raise SystemExit(main())
