# -*- coding: utf-8 -*-
"""Генератор эталона гейта паритета: снимок констант реестра кода шага 1.

Шаг 2 каталога шаблонов (07.09.2026) делает константы
vmp/template_registry.py и vmp/criteria.SPEC проекциями каталога; гейт
«каталог ≡ код» стал бы тавтологией. Эталоном остаётся ЗАМОРОЖЕННЫЙ
снимок констант кода на ревизии шага 1 (tests/fixtures/registry_step1.json):
три ребра гейта — «каталог ≡ снимок» (общий файл паритета), «снимок ≡
проекции обёртки» (tests/vmp/test_template_registry_wrapper.py),
«каталог ≡ проекции» (по построению + L10).

Снимок воспроизводим: тексты двух файлов берутся из git по ревизии
(`git show <rev>:vmp/template_registry.py`, `…/vmp/criteria.py`),
исполняются в изолированных пространствах имён (на 754c272 импорты уровня
модуля — только стандартная библиотека), константы снимаются, registry_rev
считается формулой кода, даты истории (known_dates) — теми же регулярными
выражениями, что гейт L17. Запись — template_catalog.export.dump_json
(отступ 1, ключи по алфавиту, LF); порядко-значимые структуры — списками
пар.

    python tests/fixtures/freeze_registry_step1.py            # запись
    python tests/fixtures/freeze_registry_step1.py --check    # 0 — равно, 1 — нет,
                                                              # 3 — ревизия недоступна

Правило заморозки: файл снимка не переписывается; новая заморозка (после
первого законного изменения каталога) — НОВЫЙ файл registry_step<N>.json
по слову владельца.
"""
from __future__ import annotations

import argparse
import copy
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REV = "754c272613f075c3978a0d242b35e3a9185e4eb2"
DEFAULT_OUT = Path(__file__).with_name("registry_step1.json")
SOURCE_FILES = ("vmp/template_registry.py", "vmp/criteria.py")
FROZEN_AT = "2026-09-07"
FROZEN_BY = "сессия COSMOS"
#: Имена, которые обязаны быть в исполненном тексте (защита от запуска на
#: ревизии, где константы уже стали проекциями обёртки).
NEED_REGISTRY = ("RULE_MECHANIC_TYPES", "PARAM_TO_FAMILY", "FAMILY_KIND_ZONE",
                 "FAMILY_VIOLATION_UNIT", "VIOLATION_UNITS", "CRITERION_TYPES",
                 "families", "registry_rev", "families_meta")
NEED_CRITERIA = ("SPEC",)


class RevisionUnavailable(RuntimeError):
    """git show не отдал файл ревизии (неполный клон, нет git)."""


def git_show(rev: str, rel: str) -> str:
    try:
        res = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=str(ROOT),
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RevisionUnavailable(
            f"ревизия {rev[:12]} недоступна: git show {rev[:12]}:{rel} — "
            f"{e}") from None
    if res.returncode != 0:
        raise RevisionUnavailable(
            f"ревизия {rev[:12]} недоступна: git show {rev[:12]}:{rel} "
            f"вернул код {res.returncode}: "
            f"{res.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return res.stdout.decode("utf-8")


def _exec_isolated(text: str, rel: str, need: tuple) -> dict:
    """Текст ревизии исполняется в отдельном модуле-однодневке (dataclass
    ищет модуль класса в sys.modules); после исполнения модуль снимается."""
    import types
    name = "frozen_step1_" + Path(rel).stem
    mod = types.ModuleType(name)
    mod.__file__ = str(ROOT / rel)
    sys.modules[name] = mod
    try:
        exec(compile(text, str(ROOT / rel), "exec"), mod.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    ns = mod.__dict__
    missing = [n for n in need if n not in ns]
    if missing:
        raise SystemExit(
            f"в тексте {rel} ревизии нет ожидаемых имён {missing} — "
            f"это ревизия с проекциями обёртки, а не с константами шага 1; "
            f"снимок с неё не снимается")
    return ns


def known_dates(texts: list[str]) -> list[str]:
    """Даты из исходников теми же выражениями, что гейт L17, плюс дата
    переноса в каталог (2026-09-07)."""
    known: set[str] = set()
    for text in texts:
        known |= set(re.findall(r"\d{4}-\d{2}-\d{2}", text))
        known |= {f"{y}-{m}-{d}" for d, m, y
                  in re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", text)}
    known.add("2026-09-07")
    return sorted(known)


def build(rev: str) -> dict:
    texts = {rel: git_show(rev, rel) for rel in SOURCE_FILES}
    # Снимок исполняет ТЕКСТ РЕВИЗИИ 754c272, а тот реестр ещё знал
    # режим среза базы шаблонов: переменную снимаем, чтобы снимок был
    # кодом базы, а не срезом. В сегодняшнем дереве режима нет (шаг 7,
    # часть 7-Б, 08.09.2026) — строка стережёт СТАРЫЙ текст, не новый.
    os.environ.pop("CREW_VMP_TEMPLATE_BASE", None)
    tr = _exec_isolated(texts["vmp/template_registry.py"],
                        "vmp/template_registry.py", NEED_REGISTRY)
    cr = _exec_isolated(texts["vmp/criteria.py"], "vmp/criteria.py",
                        NEED_CRITERIA)
    rmt = tr["RULE_MECHANIC_TYPES"]
    return {
        "form_version": 1,
        "name": "registry-step1",
        "purpose": ("снимок констант реестра кода на момент шага 2 — эталон "
                    "гейта паритета до шага 7; воспроизводится "
                    "freeze_registry_step1.py --check из ревизии code; правка "
                    "— только новым файлом registry_step<N>.json по слову "
                    "владельца"),
        "code": rev,
        "source_files": list(SOURCE_FILES),
        "frozen_at": FROZEN_AT,
        "frozen_by": FROZEN_BY,
        "frozen_until": {
            "executors": "шаг 4 (привязки)",
            "families_params_registry_rev":
                "перевыпуск эталонов (шаги 3/5)",
            "class_title_kind_zone_violation_unit_criteria_spec": "шаг 7",
        },
        "registry_rev": str(tr["registry_rev"]()),
        "families": {str(f): [str(p) for p in ps]
                     for f, ps in tr["families"]().items()},
        "families_meta": {
            str(f): {"params": [str(p) for p in m["params"]],
                     "kind": m["kind"], "zone": m["zone"],
                     "class": m["class"],
                     "violation_unit": m["violation_unit"]}
            for f, m in tr["families_meta"]().items()},
        "rule_mechanic_types": [
            [str(f), {"class": s.get("class"), "title": s.get("title"),
                      "executors": [str(x) for x in (s.get("executors")
                                                     or ())]}]
            for f, s in rmt.items()],
        "param_to_family": [[str(n), str(f)]
                            for n, f in tr["PARAM_TO_FAMILY"].items()],
        "family_kind_zone": {str(f): [k, z]
                             for f, (k, z) in tr["FAMILY_KIND_ZONE"].items()},
        "family_violation_unit": {str(f): u for f, u
                                  in tr["FAMILY_VIOLATION_UNIT"].items()},
        "violation_units": [str(u) for u in tr["VIOLATION_UNITS"]],
        "criterion_types": {str(k): {"class": v.get("class")}
                            for k, v in tr["CRITERION_TYPES"].items()},
        "criteria_spec": copy.deepcopy(cr["SPEC"]),
        "known_dates": known_dates(list(texts.values())),
    }


def render(doc: dict) -> bytes:
    from template_catalog.export import dump_json
    return dump_json(doc).encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rev", default=DEFAULT_REV,
                    help="ревизия git с константами шага 1")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="файл снимка (умолчание — рядом с генератором)")
    ap.add_argument("--check", action="store_true",
                    help="сравнить с файлом байт-в-байт (CRLF→LF), ничего "
                         "не писать: 0 — равно, 1 — расходится, 3 — "
                         "ревизия недоступна")
    a = ap.parse_args(argv)
    out = Path(a.out)
    try:
        payload = render(build(a.rev))
    except RevisionUnavailable as e:
        # Клон без истории: воспроизводимость не проверена — сказать
        # это явно, с именем ревизии; отпечаток файла снимка печатается,
        # чтобы подмену снимка было видно и без git (гейт обёртки
        # держит тот же sha256 константой).
        print(f"ПРЕДУПРЕЖДЕНИЕ: снимок не снят — {e}")
        print(f"ребро гейта «снимок воспроизводим из ревизии {a.rev[:12]}» "
              f"не проверено (клон без истории); остальные два ребра гейта "
              f"— «каталог ≡ снимок» и «снимок ≡ проекции обёртки» — "
              f"действуют")
        if out.is_file():
            import hashlib
            digest = hashlib.sha256(
                out.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            print(f"sha256 снимка {out.name} (CRLF→LF): {digest}")
        return 3
    if a.check:
        if not out.is_file():
            print(f"снимка нет: {out}")
            return 1
        have = out.read_bytes().replace(b"\r\n", b"\n")
        if have == payload:
            print(f"снимок {out.name} равен константам ревизии "
                  f"{a.rev[:12]}: расхождений 0")
            return 0
        print(f"снимок {out.name} РАСХОДИТСЯ с константами ревизии "
              f"{a.rev[:12]} — снимок не переписывается; новая заморозка "
              f"— новый файл по слову владельца")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(f"снимок записан: {out} (ревизия {a.rev[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
