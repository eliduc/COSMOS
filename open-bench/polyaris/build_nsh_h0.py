# -*- coding: utf-8 -*-
r"""НШ-H0 — набор шаблонов COSMOS под ВМП H0 (термин владельца
2026-09-01): поимённый детерминированный СРЕЗ базы шаблонов, точно
покрывающий все правила и критерии оптимизации ВМП нано фазы ИДАК-0.

Артефакт отдельный (решение владельца): вход лестницы «ВМП H0 → пейринг
и ростеринг → проверка» состоит из ДВУХ частей — постановки (ВМП H0) и
набора шаблонов (НШ-H0); отпечаток базы (registry_rev) входит в
сигнатуру золотого прогона.

Содержимое НШ-H0 выводится ИЗ БАЗЫ (vmp/template_registry) по факту
использования в ВМП H0 — руками ничего не вписывается: семьи механик с
исполнителями и словарными параметрами, критерии с исполнителями,
отпечаток базы. Проверка покрытия — отдельная функция check_coverage:
каждое правило с посадкой и каждый критерий обязаны указывать ВНУТРЬ
НШ-H0; всё, что мимо, — поимённый отказ (fail-closed).

Срез принадлежит НАБОРУ, чей ВМП он покрывает: ключ --kit выпускает
срез под ВМП комплекта и кладёт рядом с ним. Все проверки — до записи.

Запуск: ..\..\.venv\Scripts\python.exe build_nsh_h0.py
        ..\..\.venv\Scripts\python.exe build_nsh_h0.py --kit ..\polyaris-h0
Выход:  ..\polyaris-nano\etalon\NSH-H0.json (нано-стенд) либо
        <КОМПЛЕКТ>\etalon\NSH-H0.json (--kit)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
ETALON = HERE.parent / "polyaris-nano" / "etalon"
sys.path.insert(0, str(CREW))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class CoverageError(ValueError):
    """Покрытие ВМП H0 набором НШ-H0 не полное — перечень поимённо."""

    def __init__(self, message: str, gaps: list):
        self.gaps = gaps
        super().__init__(message)


def used_families(vmp: dict) -> list[str]:
    """Семьи, фактически использованные посадками ВМП H0."""
    out: set[str] = set()
    for r in vmp.get("rules") or []:
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("family"):
            out.add(str(fit["family"]))
    return sorted(out)


def used_criteria(vmp: dict) -> list[str]:
    """Виды машинных критериев, использованные ВМП H0: из
    применённых ответов о критерии (канонический носитель, решение
    К-3) — вид читается штатным разбором стороны CDPM."""
    from cdpm.export_solver import derive_pairing_criterion
    crit = derive_pairing_criterion(vmp)
    return [str(crit["kind"])] if crit and crit.get("kind") else []


def build(vmp: dict) -> dict:
    """Срез базы шаблонов под ВМП H0 — детерминированно из базы."""
    from vmp import template_registry as reg
    from vmp import criteria as crit_mod
    from vmp import catalog_access as ca
    from cdpm.model import registry_fingerprint

    params_by_family: dict[str, list[str]] = {}
    for pname, fam in reg.PARAM_TO_FAMILY.items():
        params_by_family.setdefault(fam, []).append(pname)

    families = {}
    for fam in used_families(vmp):
        spec = reg.RULE_MECHANIC_TYPES.get(fam)
        if spec is None:
            continue          # честно поймает check_coverage
        kind, zone = reg.FAMILY_KIND_ZONE[fam]
        families[fam] = {
            "class": spec.get("class"),
            "title": spec.get("title"),
            "executors": list(spec.get("executors") or ()),
            "params": sorted(params_by_family.get(fam, [])),
            # Детерминизация формализации (владелец 2026-09-02): вид
            # и зона правила, которые несёт шаблон семьи.
            "kind": kind,
            "zone": zone,
            # Структура ВМП (владелец 2026-09-02): единица размера
            # нарушения мягкого правила — атрибут семьи (None у жёстких).
            "violation_unit": reg.FAMILY_VIOLATION_UNIT.get(fam),
        }

    criteria = {}
    for kind in used_criteria(vmp):
        spec = crit_mod.SPEC.get(kind)
        if spec is None:
            continue
        criteria[kind] = {
            "title": spec.get("title"),
            "executor": bool(crit_mod.EXECUTORS.get(kind)),
        }

    # Закрепление базы у среза — ВЕРСИЯ И ОТПЕЧАТОК КАТАЛОГА (перевыпуск
    # эталонов, слово владельца 08.09; §10 схемы строка «3» и часть 7-Б):
    # срез перестаёт быть рукописной копией подмножества и становится
    # проекцией закреплённой версии. Отпечаток базы и отпечаток каталога
    # — одна величина с шага 3; расхождение здесь означало бы две мерки в
    # одном файле, поэтому оно — отказ, а не молчаливое расхождение.
    stamp = ca.stamp()
    rev = registry_fingerprint()
    if stamp["fingerprint"] != rev:
        raise ValueError(
            "отпечаток каталога двери %s ≠ отпечатку базы шаблонов %s — "
            "две мерки в одном срезе" % (stamp["fingerprint"], rev))
    return {
        "form_version": 1,
        "name": "НШ-H0",
        "note": "набор шаблонов COSMOS под ВМП H0 (фаза ИДАК-0): "
                "детерминированный срез базы по факту использования; "
                "руками не редактируется — перевыпускается генератором",
        "registry_rev": rev,
        "catalog_version": stamp["version"],
        "catalog_fingerprint": stamp["fingerprint"],
        "vmp": {"customer": (vmp.get("meta") or {}).get("customer"),
                "cdpm_version": (vmp.get("meta") or {})
                .get("cdpm_version")},
        "families": families,
        "criteria": criteria,
    }


def check_coverage(vmp: dict, nsh: dict) -> list[dict]:
    """ТЩАТЕЛЬНАЯ проверка покрытия (fail-closed): каждое правило ВМП
    H0 и каждый критерий обязаны указывать внутрь НШ-H0.

    Проверяется поимённо: (1) правило зон расчёта несёт посадку;
    (2) её семья есть в НШ-H0; (3) все её параметры принадлежат этой
    семье по словарю И перечислены в НШ-H0; (4) беспараметрная семья
    несёт цитату-носитель; (5) критерий пейринга есть в НШ-H0 и имеет
    исполнителя; (6) целевые ВМП несут метрику закрытого словаря;
    (7) отпечаток базы НШ-H0 совпадает с отпечатками посадок ВМП.
    Возврат: перечень пробелов (пусто = 100% покрытие)."""
    from vmp import template_registry as reg
    from vmp import registry_check as rc
    from cdpm.model import METRICS, registry_fingerprint

    gaps: list[dict] = []
    fams = nsh.get("families") or {}
    zones_calc = {"PAIRING", "ROSTERING", "BOTH"}

    # (7) отпечаток базы — один и тот же у среза и у посадок
    rev = nsh.get("registry_rev")
    if rev != registry_fingerprint():
        gaps.append({"kind": "registry_rev_stale",
                     "id": str(rev),
                     "note": "отпечаток НШ-H0 разошёлся с базой "
                             "шаблонов — срез устарел, перевыпустить"})

    for r in vmp.get("rules") or []:
        rid = str(r.get("id"))
        kind = r.get("kind")
        if r.get("zone") not in zones_calc:
            continue
        fit = r.get("template_fit")
        # (1) правила видов HARD/SOFT обязаны нести посадку
        if kind in ("HARD", "SOFT"):
            if not isinstance(fit, dict):
                gaps.append({"kind": "rule_no_fit", "id": rid,
                             "note": "правило вида %s без посадки на "
                                     "шаблон" % kind})
                continue
        elif not isinstance(fit, dict):
            continue          # DEF/DATA/OUT — посадка не требуется
        cat = str(fit.get("category") or "")
        if cat != "A":
            gaps.append({"kind": "rule_not_category_a", "id": rid,
                         "note": "фаза ИДАК-0 требует посадки "
                                 "категории A, а тут %r" % cat})
            continue
        # (2) семья — внутри НШ-H0
        fam = str(fit.get("family") or "")
        if fam not in fams:
            gaps.append({"kind": "family_outside_nsh", "id": rid,
                         "note": "семья %r вне НШ-H0" % fam})
            continue
        # (3)/(4) параметры и цитаты — по общему судье посадок
        g = rc.fit_gap(r)
        if g is not None:
            gaps.append({"kind": g["kind"], "id": rid,
                         "note": g["note"]})
            continue
        for pname in (fit.get("params") or {}):
            if reg.PARAM_TO_FAMILY.get(str(pname)) != fam:
                gaps.append({"kind": "param_family_mismatch",
                             "id": rid,
                             "note": "параметр %r не принадлежит "
                                     "семье %r" % (pname, fam)})
            elif str(pname) not in (fams[fam].get("params") or []):
                gaps.append({"kind": "param_outside_nsh", "id": rid,
                             "note": "параметр %r отсутствует в "
                                     "НШ-H0" % pname})
        if fit.get("registry_rev") != rev:
            gaps.append({"kind": "rule_rev_mismatch", "id": rid,
                         "note": "отпечаток посадки %r ≠ отпечатка "
                                 "НШ-H0 %r"
                                 % (fit.get("registry_rev"), rev)})

    # (5) критерий пейринга
    crits = nsh.get("criteria") or {}
    kinds = used_criteria(vmp)
    if not kinds:
        gaps.append({"kind": "criterion_absent", "id": "—",
                     "note": "машинный критерий пейринга не выводится "
                             "из ВМП H0"})
    for k in kinds:
        if k not in crits:
            gaps.append({"kind": "criterion_outside_nsh", "id": k,
                         "note": "вид критерия вне НШ-H0"})
        elif not crits[k].get("executor"):
            gaps.append({"kind": "criterion_no_executor", "id": k,
                         "note": "у вида критерия нет исполнителя"})

    # (6) целевые ВМП — с метрикой закрытого словаря
    for o in vmp.get("objectives") or []:
        met = o.get("metric")
        if met is None:
            gaps.append({"kind": "objective_no_metric",
                         "id": str(o.get("id")),
                         "note": "целевая без машинной метрики"})
        elif met not in METRICS:
            gaps.append({"kind": "objective_metric_unknown",
                         "id": str(o.get("id")),
                         "note": "метрика %r вне закрытого словаря"
                                 % met})
    return gaps


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="НШ-H0 — срез базы шаблонов")
    ap.add_argument("--kit", default=None,
                    help="каталог комплекта (open-bench/polyaris-h0): срез "
                         "выпускается ПОД ВМП КОМПЛЕКТА и кладётся рядом с "
                         "ним (<kit>/etalon/NSH-H0.json); без ключа — срез "
                         "нано-стенда под VMP-NANO.json")
    a = ap.parse_args(argv)
    # Срез принадлежит НАБОРУ, чей ВМП он покрывает: покрытие судится
    # против того же ВМП, из которого срез выведен. Иначе отпечатки
    # посадок одного набора сверяются со срезом другого (расхождение
    # 2026-09-03: база выросла семьёй флота, нано-эталон заморожен).
    etalon_dir = (Path(a.kit) / "etalon") if a.kit else ETALON
    vmp_path = etalon_dir / ("VMP-H0.json" if a.kit else "VMP-NANO.json")
    if not vmp_path.is_file():
        print("нет ВМП: %s" % vmp_path)
        return 2
    vmp = json.loads(vmp_path.read_text(encoding="utf-8"))
    nsh = build(vmp)
    out = etalon_dir / "NSH-H0.json"
    # ВСЕ проверки — ДО записи (тот же канон, что у генератора эталона):
    # красное покрытие не должно оставлять на диске срез, которым уже
    # нельзя пользоваться. Живой случай 2026-09-03: запуск без ключа
    # переписал срез нано-стенда отпечатком новой базы, а покрытие было
    # красным — файл пришлось восстанавливать из истории.
    gaps = check_coverage(vmp, nsh)
    if gaps:
        print("ПОКРЫТИЕ НЕ ПОЛНОЕ (%d), ничего не записано:" % len(gaps))
        for g in gaps:
            print(" - [%s] %s: %s" % (g["kind"], g["id"], g["note"]))
        return 1
    out.write_text(json.dumps(nsh, ensure_ascii=False, indent=1)
                   + "\n", encoding="utf-8")
    print("НШ-H0 записан: %s" % out)
    print("семей: %d, критериев: %d, отпечаток базы: %s"
          % (len(nsh["families"]), len(nsh["criteria"]),
             nsh["registry_rev"]))
    print("ПОКРЫТИЕ 100%%: каждое правило и критерий ВМП H0 "
          "покрыты шаблонами НШ-H0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
