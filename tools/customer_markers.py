# -*- coding: utf-8 -*-
"""Линт маркеров заказчика (совместная задача, дизайн согласован).

ЕДИНЫЙ источник правды — customer-markers.json рядом с оснасткой
публикации (handoff/github-export/, ВНЕ публикуемого репозитория:
список того, что мы прячем, опаснее случайного маркера). Схема:
checks[] c kind substring | token | regex (+ case: "exact" —
регистр строгий; иначе без регистра; token — границы слова, где
подчёркивание — часть слова), exceptions[] — поимённые исключения
путей (решения владельца).

Область гейта — ПУБЛИКУЕМОЕ: локальный main законно несёт отчёты
заказчика (данные не покидают машину), публикация идёт срезом, и
строгий прогон судит worktree среза (`--repo <путь>`). Замер
локального main 2026-08-25 моим прежним перечнем: 145 находок — все
в непубликуемых final-report/docs/knowledge; коды-замены
обезличивания (BSE и т.п.) в publish-гейт не входят по построению —
их место в будущем линте связки (узел customer-markers-lint).

Запуск: `python -m tools.customer_markers [--repo ПУТЬ]` — находки
с контекстом, код возврата 1 при любой.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARKERS_FILE = (REPO.parent / "handoff" / "github-export"
                / "customer-markers.json")
MARKERS_ENV = "CATALOG_MARKERS_FILE"

_WORD = "0-9A-Za-zА-Яа-яЁё_"


def markers_path(path: Path | None = None) -> Path:
    """Явный аргумент сильнее окружения; без них прежний внешний путь."""
    if path is not None:
        return Path(path)
    configured = (os.environ.get(MARKERS_ENV) or "").strip()
    return Path(configured) if configured else MARKERS_FILE


def load_checks(path: Path | None = None) -> dict | None:
    """Считать действительный перечень, не превращая явный отказ в пустоту.

    Прежний API возвращает None для отсутствующего path/default. Но явно
    настроенный через env источник обязателен: L11 зовёт load_checks() и
    не должен получить зелёный вердикт с нулём маркеров при потере файла.
    Диагностика не раскрывает значения private patterns.
    """
    configured = path is None and bool((os.environ.get(MARKERS_ENV) or "").strip())
    path = markers_path(path)
    if not path.is_file():
        if configured:
            raise FileNotFoundError("explicit marker policy is unavailable")
        return None

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate marker policy field")
            result[key] = value
        return result

    try:
        spec = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite policy field")))
    except (OSError, ValueError):
        raise ValueError("marker policy cannot be read as valid JSON") from None
    if not isinstance(spec, dict):
        raise ValueError("marker policy must be an object")
    checks = spec.get("checks", spec.get("markers"))
    if not isinstance(checks, list) or not checks:
        raise ValueError("marker policy has no checks")
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("marker policy check must be an object")
        values = check.get("values")
        if (not isinstance(values, list) or not values
                or not all(isinstance(value, str) and value.strip() for value in values)):
            raise ValueError("marker policy check has no valid values")
        allows = check.get("context_allow", [])
        if not isinstance(allows, list) or not all(isinstance(value, str) for value in allows):
            raise ValueError("invalid marker policy context controls")
        try:
            _patterns(check)
            _allow_patterns(check)
        except (ValueError, TypeError, re.error):
            raise ValueError("invalid marker policy check or pattern") from None
    # The service historically accepts the legacy `markers` spelling; all
    # consumers, including L11 and scan(), must inspect those same checks.
    return {**spec, "checks": checks}


def _patterns(check: dict) -> list[re.Pattern]:
    flags = 0 if check.get("case") == "exact" else re.IGNORECASE
    kind = check.get("kind")
    pats = []
    for v in check.get("values") or []:
        if kind == "substring":
            pats.append(re.compile(re.escape(v), flags))
        elif kind == "token":
            pats.append(re.compile(
                f"(?<![{_WORD}])" + re.escape(v) + f"(?![{_WORD}])",
                flags))
        elif kind == "regex":
            pats.append(re.compile(
                f"(?<![{_WORD}])(?:" + v + f")(?![{_WORD}])", flags))
        else:
            raise ValueError(f"неизвестный kind {kind!r} "
                             f"в перечне маркеров")
    return pats


def _allow_patterns(check: dict) -> list[re.Pattern]:
    """context_allow перечня: языковые контексты (названия языка
    интерфейса и т.п.) — строка с маркером НЕ находка, если в ней же
    присутствует разрешающий контекст. Замечание решателя 2026-08-25:
    молчаливый игнор поля красил i18n/login на публично известных
    названиях языков. Regex, без регистра."""
    return [re.compile(v, re.IGNORECASE)
            for v in check.get("context_allow") or []]


def tracked_files(repo: Path) -> list[str]:
    txt = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True,
        text=True, encoding="utf-8", check=True).stdout
    return [p for p in txt.splitlines() if p]


def in_publish_scope(spec: dict, rel: str) -> bool:
    """Область публикации из САМОГО перечня (унификация 2026-08-29:
    прежде списки жили в двух местах — здесь и в сборщике среза — и
    расходились бы молча). publish_keep сильнее publish_exclude;
    publish_exclude_permanent — ПОСТОЯННЫЕ исключения решением
    владельца 2026-08-29 «навсегда вне публикации» — сильнее всего,
    включая publish_keep, и не вычищаются обычной ревизией
    publish_exclude_exact. Сборщик среза build_export.py читает то же
    объединение exact+permanent."""
    if rel in set(spec.get("publish_exclude_permanent") or []):
        return False
    if rel in set(spec.get("publish_keep") or []):
        return True
    if rel in set(spec.get("publish_exclude_exact") or []):
        return False
    for pref in spec.get("publish_exclude") or []:
        if rel.startswith(pref):
            return False
    return True


def scan(spec: dict, repo: Path, publish_scope: bool = False
         ) -> list[tuple[str, int, str, str]]:
    """[(путь, строка, имя проверки, контекст)] по отслеживаемым
    файлам repo; exceptions перечня — поимённые пропуски путей;
    publish_scope=True — судить только публикуемую область (файлы
    вне publish_exclude перечня)."""
    exceptions = set(spec.get("exceptions") or [])
    checks = [(c.get("name") or c.get("kind"), _patterns(c),
               _allow_patterns(c))
              for c in spec.get("checks") or []]
    findings = []
    for rel in tracked_files(repo):
        if rel in exceptions:
            continue
        if publish_scope and not in_publish_scope(spec, rel):
            continue
        path = repo / rel
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue                       # бинарный
        text = raw.decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            for name, pats, allows in checks:
                if any(p.search(line) for p in pats) \
                        and not any(a.search(line) for a in allows):
                    findings.append((rel, n, name,
                                     line.strip()[:120]))
                    break
    return findings


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    repo = (Path(argv[argv.index("--repo") + 1]).resolve()
            if "--repo" in argv else REPO)
    path = markers_path()
    try:
        spec = load_checks()
    except (OSError, ValueError) as error:
        print(f"перечень маркеров не проверен ({path}): {type(error).__name__}; линт не выполнен")
        return 2
    if spec is None:
        print(f"перечня маркеров на машине нет ({path}) — "
              f"линт молчит")
        return 0
    findings = scan(spec, repo,
                    publish_scope="--publishable" in argv)
    print(f"репозиторий: {repo}; проверок: "
          f"{len(spec.get('checks') or [])}; файлов: "
          f"{len(tracked_files(repo))}; находок: {len(findings)}")
    for rel, n, name, ctx in findings:
        print(f"  {rel}:{n}  [{name}]  {ctx}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
