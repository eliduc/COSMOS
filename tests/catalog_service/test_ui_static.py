# -*- coding: utf-8 -*-
"""T12 — собранная статика интерфейса каталога и штамп свежести.

Сборка `catalog-ui/dist/` коммитится вместе с исходником: установка
COSMOS у заказчика не требует Node. Обещание «dist собран из этого
src» держится не на слове, а на штампе `dist/build-stamp.json`: он
несёт отпечаток исходных файлов, и этот гейт краснеет, как только src
уходит вперёд сборки.

ФОРМУЛА ШТАМПА — одна на три места (`catalog-ui/scripts/stamp.mjs`,
этот тест, `catalog_service/API-CONTRACT.md` §7):

* входы: `src/**`, `index.html`, `public/**`, `package.json`,
  `package-lock.json`, `vite.config.ts`, `tsconfig*.json`,
  `tailwind.config.js`, `postcss.config.js`, `.oxlintrc.json`;
* пути — относительные от `catalog-ui/`, в POSIX-форме, отсортированы;
* содержимое — байты файла с заменой CRLF на LF (клон на Windows и на
  Linux даёт один штамп);
* вклад файла — `<путь>\\0<содержимое>\\0`;
* штамп — sha256 конкатенации вкладов в порядке путей.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "catalog-ui"
DIST = UI / "dist"

#: Файлы верхнего уровня, входящие в штамп (папки — `src` и `public`).
STAMP_FILES = (
    "index.html", "package.json", "package-lock.json", "vite.config.ts",
    "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json",
    "tailwind.config.js", "postcss.config.js", ".oxlintrc.json",
)
STAMP_DIRS = ("src", "public")


def stamp_inputs(root: Path) -> list[str]:
    """Относительные пути входов штампа в POSIX-форме, по возрастанию."""
    found: list[Path] = []
    for name in STAMP_DIRS:
        folder = root / name
        if folder.exists():
            found += [p for p in folder.rglob("*") if p.is_file()]
    for name in STAMP_FILES:
        path = root / name
        if path.exists():
            found.append(path)
    return sorted(p.relative_to(root).as_posix() for p in found)


def stamp_of(root: Path) -> str:
    """sha256 по формуле штампа свежести."""
    digest = hashlib.sha256()
    for rel in stamp_inputs(root):
        body = (root / rel).read_bytes().replace(b"\r\n", b"\n")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(body)
        digest.update(b"\x00")
    return digest.hexdigest()


def test_dist_built_and_relative() -> None:
    """Сборка есть, страница по-русски, ссылок наружу нет."""
    index = DIST / "index.html"
    assert index.exists(), (
        "нет catalog-ui/dist/index.html: соберите интерфейс командой "
        "npm run build в catalog-ui (сборка коммитится вместе с src)")
    html = index.read_text(encoding="utf-8")
    assert 'lang="ru"' in html, "страница интерфейса не объявлена русской"
    assert "<title>Каталог шаблонов</title>" in html, (
        "заголовок вкладки интерфейса не «Каталог шаблонов»")

    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs, "в index.html нет ни одной ссылки на ресурсы сборки"
    for ref in refs:
        assert not re.match(r"^(?:[a-z]+:)?//", ref), (
            f"внешний адрес в сборке интерфейса: {ref} — "
            "контур работает без внешних загрузок")
        assert ref.startswith("./assets/"), (
            f"ссылка сборки не относительная ./assets/*: {ref}")
        assert (DIST / ref[2:]).exists(), (
            f"файла сборки нет: {ref}")
    print(f"T12: ссылок в index.html — {len(refs)}, все ./assets/*")


def test_build_stamp_matches_sources() -> None:
    """Штамп свежести равен пересчёту по той же формуле."""
    path = DIST / "build-stamp.json"
    assert path.exists(), (
        "нет catalog-ui/dist/build-stamp.json: сборка сделана без "
        "scripts/stamp.mjs (команда npm run build)")
    stamp = json.loads(path.read_text(encoding="utf-8"))
    inputs = stamp_inputs(UI)
    assert inputs, "входов штампа не найдено — проверьте раскладку catalog-ui"
    assert re.fullmatch(r"[0-9a-f]{64}", stamp.get("src_sha256", "")), (
        "поле src_sha256 штампа — не 64 шестнадцатеричных знака")
    assert stamp.get("files") == len(inputs), (
        f"в штампе файлов {stamp.get('files')}, в дереве {len(inputs)}: "
        "сборка отстала от исходника — npm run build в catalog-ui")
    assert stamp["src_sha256"] == stamp_of(UI), (
        "штамп свежести не сошёлся: dist/ собран не из этого src/ — "
        "выполните npm run build в catalog-ui и закоммитьте dist вместе "
        "с исходником")
    print(f"T12: входов штампа — {len(inputs)}, "
          f"src_sha256 {stamp['src_sha256'][:12]}…")


def test_stamp_ignores_line_endings(tmp_path: Path) -> None:
    """CRLF и LF дают один штамп: клон на Windows не красит гейт."""
    body = "первая строка\nвторая строка\n"
    counted = 0
    for flavour, text in (("lf", body), ("crlf", body.replace("\n", "\r\n"))):
        root = tmp_path / flavour
        (root / "src" / "pages").mkdir(parents=True)
        (root / "public").mkdir()
        (root / "src" / "pages" / "RecordsPage.tsx").write_bytes(
            text.encode("utf-8"))
        (root / "public" / "favicon.svg").write_bytes(text.encode("utf-8"))
        (root / "index.html").write_bytes(text.encode("utf-8"))
        (root / "package.json").write_bytes(text.encode("utf-8"))
        counted += len(stamp_inputs(root))
    assert counted == 8, "случай CRLF собран не из четырёх файлов на вариант"
    assert stamp_of(tmp_path / "lf") == stamp_of(tmp_path / "crlf"), (
        "штамп зависит от перевода строк — формула нормализации сломана")
    print("T12: случай CRLF — штамп тот же, файлов на вариант 4")


def test_stamp_formula_is_written_down() -> None:
    """Формула штампа записана в контракте службы (шов A ↔ B)."""
    contract = ROOT / "catalog_service" / "API-CONTRACT.md"
    if not contract.exists():
        pytest.skip("контракт службы ещё не написан (дорожка A) — "
                    "формула штампа сверяется после его появления")
    text = contract.read_text(encoding="utf-8")
    for needle in ("build-stamp.json", "src_sha256", "package-lock.json",
                   "tailwind.config.js", ".oxlintrc.json"):
        assert needle in text, (
            f"в catalog_service/API-CONTRACT.md нет части формулы штампа: "
            f"{needle}")
    print("T12: формула штампа названа в контракте службы")


#: Ручка клиента: строка запроса, начинающаяся с /api. Подстановки
#: `${…}` шаблонной строки отрезаются вместе с хвостом — сверяется
#: ЛИТЕРАЛЬНАЯ часть адреса (у службы ей отвечает часть до первого
#: параметра пути): опечатка в имени ручки ловится, а разбор TypeScript
#: не выдумывается.
API_CALL = re.compile(r"['`](/api[^'`]*)['`]")
#: Машинный код отказа, который интерфейс РАЗЛИЧАЕТ (а не просто
#: показывает словами): `err.code === '…'`.
CODE_CHECK = re.compile(r"\.code\s*===\s*'([a-z-]+)'")


def _stem(path: str) -> str:
    """Литеральное начало адреса: до первой подстановки, параметра пути
    или знака вопроса."""
    for mark in ("${", "{", "?"):
        if mark in path:
            path = path.split(mark)[0]
    return path.rstrip("/")


def _service_stems() -> set:
    from catalog_service.app import create_app
    from catalog_service.settings import Settings
    app = create_app(Settings(catalog_dir=ROOT / "catalog",
                              work_dir=ROOT.parent / "нет-рабочей-папки",
                              read_only=True))
    return {_stem(getattr(r, "path", "")) for r in app.routes}


def _client_stems() -> set:
    text = (UI / "src" / "api.ts").read_text(encoding="utf-8")
    return {_stem(raw) for raw in API_CALL.findall(text)}


def test_every_handle_of_the_client_exists_in_the_service() -> None:
    """Шов A ↔ B: интерфейс не зовёт ручек, которых у службы нет.

    Опечатка в адресе не видна ни сборке, ни линту — только пустым
    экраном у владельца, поэтому её ловит гейт."""
    service, client = _service_stems(), _client_stems()
    missing = sorted(p for p in client if p not in service)
    assert not missing, f"клиент зовёт ручки вне службы: {missing}"
    assert len(client) >= 15, f"ручек клиента разобрано мало: {len(client)}"
    print(f"T12: ручек клиента сверено со службой: {len(client)} "
          f"(у службы всего {len(service)})")


def test_codes_the_screen_distinguishes_are_service_codes() -> None:
    """Коды, по которым интерфейс ветвится, — из закрытого перечня
    службы: опечатка в коде тихо выключила бы ветку (диалог осознанного
    продолжения, подтверждение исполнителя)."""
    from catalog_service import errors
    seen = set()
    for path in sorted(UI.joinpath("src").rglob("*.ts*")):
        seen |= set(CODE_CHECK.findall(path.read_text(encoding="utf-8")))
    assert seen, "интерфейс не различает ни одного кода — проверьте разбор"
    unknown = sorted(seen - set(errors.CODES))
    assert not unknown, f"коды вне перечня службы: {unknown}"
    print(f"T12: кодов отказа, которые различает интерфейс: {len(seen)} "
          f"({', '.join(sorted(seen))})")


#: Формы ответов, которые экран обязан читать ровно так, как их отдаёт
#: служба: имя объявления в `api.ts` → поля контракта §4. Гейт ручек
#: (выше) сверяет только адреса — формы он не судит, и расхождение
#: «шапка выпуска лежит под ключом header» ловится именно здесь.
CONTRACT_SHAPES = {
    "VersionDetail": ("version", "entry", "header", "changes", "loadable",
                      "is_current"),
    "DraftsList": ("items", "suggested", "bindings_stop"),
    "Session": ("mode", "actor", "read_only", "catalog", "drafts",
                "requests_open", "rights"),
    "RecordDetail": ("record", "draft", "changes", "in_catalog", "executors",
                     "stand", "request", "problems", "shown", "pinned_words",
                     "pinned_release_ok"),
    "VersionHint": ("level", "version", "changes", "by_level", "base"),
}


def _interface_body(text: str, name: str) -> str:
    found = re.search(r"export interface " + name + r"[^{]*\{(.*?)\n\}",
                      text, re.S)
    assert found, f"объявления {name} в api.ts нет"
    return found.group(1)


def test_client_knows_the_shapes_of_the_answers() -> None:
    """Формы ответов службы объявлены в клиенте по именам полей.

    Экран, читающий `got.fingerprint` там, где служба отдаёт
    `got.header.fingerprint`, собирается и линтуется молча — пусты лишь
    строки на экране. Этот гейт сверяет имена полей контракта §4 с
    объявлениями `catalog-ui/src/api.ts`."""
    text = UI.joinpath("src", "api.ts").read_text(encoding="utf-8")
    contract = (ROOT / "catalog_service" / "API-CONTRACT.md").read_text(
        encoding="utf-8")
    checked = 0
    for name, fields in CONTRACT_SHAPES.items():
        body = _interface_body(text, name)
        missing = [f for f in fields if not re.search(
            r"(?m)^\s*" + re.escape(f) + r"[?]?\s*:", body)]
        assert not missing, f"{name}: полей нет в api.ts: {missing}"
        checked += len(fields)
    for field in ("header", "entry", "loadable", "is_current",
                  "bindings_stop", "pinned_release_ok", "by_level"):
        assert field in contract, f"поле {field} не названо в контракте"
    print(f"T12: полей форм ответов сверено с контрактом: {checked} "
          f"в {len(CONTRACT_SHAPES)} объявлениях")


def test_address_filter_values_are_not_dropped_by_the_screen() -> None:
    """Значения фильтров адресов §6.1 экран не снимает.

    Список записей чистит адрес от значений вне словаря — и однажды снял
    собственный пункт «не объявлено»: он в словаре `executor_states` не
    живёт (это КЛЕТКА без строк). Значения берёт `knownValues`, и она
    обязана знать про него."""
    text = UI.joinpath("src", "pages", "RecordsPage.tsx").read_text(
        encoding="utf-8")
    body = re.search(r"function knownValues\([^)]*\)[^{]*\{(.*?)\n\}",
                     text, re.S)
    assert body, "функции knownValues в RecordsPage.tsx нет"
    assert "STATE_UNDECLARED_FILTER" in body.group(1), \
        "knownValues не знает про «не-объявлено» — экран снимет свой же пункт"
    assert "const known = knownValues(dicts, key)" in text, \
        "эффект очистки адреса судит значения мимо knownValues"
    print("T12: значения фильтра состояния исполнителя экраном не снимаются")
