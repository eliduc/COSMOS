# -*- coding: utf-8 -*-
"""Сторож: в тестах нет литералов среды.

Корень двух покраснений сентября 2026 года один и тот же.

  * CI № 73 на GitHub (09.09.2026) упал на чистом раннере: проверка
    ждала службу каталога на порту 8105. Локально она была зелёной
    только потому, что на этом порту отвечала ЖИВАЯ служба
    разработчика.
  * Ранее два десятка стендов службы и конвейера каталога краснели в
    клоне агента: они брали живой номер заявки из `catalog/` и ждали
    пустую очередь.

Правило, а не заплатка: тест не имеет права зависеть от того, что
запущено на машине и что лежит в живом каталоге. Сторож ловит это до
раската — на своей машине, а не на чужом раннере.

Что ловится:

  * порт службы пакета или контура (`SERVICE_PORTS` — по таблице
    `docs/knowledge/deploys/contours.md`), числом или внутри строки;
  * адрес петли (`127.0.0.1`, `localhost`, `0.0.0.0`) с портом 1024 и
    выше. Заведомо закрытые порты (ниже 1024, обычно 1 и 9) — законный
    способ доказать «служба не отвечает», их сторож не трогает;
  * путь машины разработчика: `C:\\work`, `C:\\Users`, `/home/`,
    `/Users/`;
  * адрес площадки: `fregene.ai`, `cos-test`, `cos-stage`;
  * имя машины разработчика;
  * личность машины владельца: его почтовый адрес и учётное имя из него
    (10.09.2026 — `CREW_SUPER_ADMIN` в `tests/conftest.py` и тот же
    литерал в сорока с лишним файлах). Личность человека — такой же
    носитель среды, как порт и путь: в чужом клоне её нет, а в выводе
    прогонов и в исходниках она быть не должна;
  * живой идентификатор каталога: номер заявки, ключ CR или отпечаток,
    которые ПРЯМО СЕЙЧАС лежат в `catalog/*.json`.

Чем заменять: свободный порт (порт 0 у слушателя с чтением фактического
номера) либо вымышленный адрес зоны `.invalid`; синтетический
идентификатор, построенный в самом тесте; `tmp_path` и `monkeypatch`
вместо путей машины; константу боевого кода вместо повтора числа;
личность стенда — импортом `_stand_identity.ADMIN_EMAIL` (вымышленный
адрес, которым `tests/conftest.py` задаёт `CREW_SUPER_ADMIN` на весь
прогон), а производные от неё — из `ADMIN_SLUG` и `LEGACY_UPLOAD_ID`.

Судятся ЗНАЧЕНИЯ, а не текст файла: он разбирается `ast`, и смотрятся
только строковые, байтовые и числовые константы. Комментарии и строки
описания (docstring) — рассказ о происхождении теста, а не зависимость
от среды, и сторож их не читает.

Зона CDPM (`test_cdpm_*`, `test_installer*`, `test_uninstaller*`) —
не наша: её сторожит дорожка CDPM.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
LIVE_CATALOG = ROOT / "catalog"

#: Файлы вне сторожа: зона CDPM и он сам.
SKIP_PREFIXES = ("test_cdpm_", "_cdpm_", "test_installer",
                 "test_uninstaller")
SELF = Path(__file__).name

#: Порты служб пакета и контуров (docs/knowledge/deploys/contours.md):
#: веб-приложение, панель CDPM, решатель, служба каталога — по всем
#: площадкам, локальной машине и установке заказчика. Плюс порты
#: исполнителей-судей (8110+) и порт локальной модели.
SERVICE_PORTS = frozenset(
    {8090, 8091, 8092, 8093, 8094, 8095, 8096, 8097, 8098, 8099, 8100,
     8105, 8106, 8107, 8123, 8124, 9096, 9099, 11434}
    | set(range(8110, 8120)))

#: Порт ниже этого — заведомо закрытый: службы там не бывает ни на
#: машине разработчика, ни на чистом раннере.
FREE_PORT_BOUND = 1024

_LOOPBACK = re.compile(r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0)\s*:\s*(\d{1,5})")
_NUMBER = re.compile(r"(?<![\d.])(\d{4,5})(?![\d.])")
#: Путь машины Windows — без учёта регистра (диск пишут по-разному);
#: путь POSIX — С УЧЁТОМ: адрес ручки `/api/users/` регистром
#: отличается от домашней папки `/Users/` и путём не является.
_DEV_PATH_WIN = re.compile(r"[A-Za-z]:[\\/]{1,2}(?:work|Users)\b",
                           re.IGNORECASE)
_DEV_PATH_POSIX = re.compile(r"/home/|/Users/")
_SITE = re.compile(r"fregene\.ai|cos-test|cos-stage", re.IGNORECASE)
_DEV_HOST = re.compile(r"\bLEGION\b")
#: Личность машины владельца: почтовый адрес и учётное имя из него
#: (учётное имя ловится отдельно — из него строятся слаг папки выгрузок
#: и идентификаторы наборов, `upload-<слаг>--<имя>`).
#:
#: Адрес зашит открытым текстом НАРОЧНО, и это осознанное исключение из
#: собственного правила сторожа. Сторож обязан ловить ИМЕННО этот адрес:
#: искать «любую почту» нельзя — вымышленные адреса тестов (`.invalid`,
#: `example.com`) законны и должны оставаться; хранить искомое в
#: отдельном файле или хешем — значит спрятать от чтения то самое
#: значение, ради которого сторож заведён, и первый же читатель не
#: поймёт, что именно запрещено. Файл сторожа из-под сторожа исключён
#: (`SELF`), поэтому находки сам на себя он не даёт. Смена личности
#: владельца — правка этой строки.
_OWNER_IDENTITY = re.compile(r"levrlg(?:@gmail\.com)?", re.IGNORECASE)

#: Названия находок — ими же ведётся перечень исключений.
KIND_PORT = "порт службы пакета"
KIND_LOOPBACK = "адрес петли с портом"
KIND_PATH = "путь машины разработчика"
KIND_SITE = "адрес площадки"
KIND_HOST = "имя машины разработчика"
KIND_OWNER = "личность машины владельца"
KIND_LIVE_ID = "живой идентификатор каталога"

#: Перечень исключений: «файл: что: почему». Каждая строка обоснована —
#: значение либо требуется боевым кодом, либо является ИСКОМЫМ данными
#: самого теста (то, чего в выдаче быть не должно), либо лежит в зоне
#: другого исполнителя и передано отчётом.
ALLOWLIST: dict[str, dict[str, str]] = {
    "tests/test_settings.py": {
        KIND_PORT: "порт локальной модели — часть того же адреса "
                   "петли, который требует боевая проверка; в сеть "
                   "тест не ходит",
        KIND_LOOPBACK: "боевая проверка assert_local_model_url принимает "
                       "ТОЛЬКО петлю и частную сеть — иной адрес тест "
                       "проверить не может; соединения нет, значение "
                       "задаёт и тут же читает сам тест",
    },
    "tests/test_observability.py": {
        KIND_PATH: "«/home/» здесь — ИСКОМОЕ: страж утечки путей "
                   "доказывает, что его в выдаче НЕТ",
    },
    "tests/vmp/test_solver_build.py": {
        KIND_PATH: "пути — входные данные проверки затирания: тест "
                   "доказывает, что они исчезают из отчёта о сборке",
        KIND_SITE: "адрес площадки — входные данные той же проверки: он, "
                   "наоборот, обязан УЦЕЛЕТЬ, затираются только пути",
        KIND_LOOPBACK: "адрес решателя — входные данные той же проверки: "
                       "затиранию подлежат пути, адрес обязан уцелеть",
    },
    "tests/test_change_requests.py": {
        KIND_PORT: "содержимое подставного application.properties в "
                   "tmp-дереве: файл создаёт сам тест, службы за ним нет. "
                   "Зона другого исполнителя — передано отчётом",
    },
    "tests/test_package_runs.py": {
        KIND_PORT: "переменная CREW_SOLVER_URL задаётся и тут же читается "
                   "самим тестом, обращения к решателю нет. Зона другого "
                   "исполнителя — передано отчётом",
        KIND_LOOPBACK: "тот же случай: значение задано и прочитано в одном "
                       "тесте, в сеть он не ходит",
    },
}

WORDS = ("замените на свободный порт / синтетический идентификатор / "
         "tmp_path / _stand_identity.ADMIN_EMAIL или внесите в allowlist "
         "с обоснованием")


def refusal(rel: str, line: int, kind: str, what: str) -> str:
    """Слова отказа: где, что за литерал и чем его заменить."""
    return (f"литерал среды в тесте {rel}:{line} — {kind} «{what}» "
            f"— {WORDS}")


def live_catalog_ids() -> frozenset[str]:
    """Номера заявок, ключи CR и отпечатки ИЗ ЖИВОГО каталога.

    Читается то, что лежит на диске сейчас: перечень «живых» значений
    меняется вместе с каталогом, и зашивать его числом нельзя — это был
    бы такой же литерал среды. Каталога нет — перечень пуст."""
    found: set[str] = set()
    if not LIVE_CATALOG.is_dir():
        return frozenset()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("request_id", "cr_key", "fingerprint") \
                        and isinstance(value, str) and value:
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in sorted(LIVE_CATALOG.glob("*.json")):
        try:
            walk(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):                    # pragma: no cover
            continue
    return frozenset(found)


def _constants(tree: ast.AST):
    """Константы файла БЕЗ строк описания (docstring)."""
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docstrings:
            yield node


def _text_findings(text: str, live_ids: frozenset[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for match in _LOOPBACK.finditer(text):
        port = int(match.group(1))
        if port >= FREE_PORT_BOUND:
            out.append((KIND_LOOPBACK, match.group(0)))
    for match in _NUMBER.finditer(text):
        if int(match.group(1)) in SERVICE_PORTS:
            out.append((KIND_PORT, match.group(1)))
    for kind, pattern in ((KIND_PATH, _DEV_PATH_WIN),
                          (KIND_PATH, _DEV_PATH_POSIX),
                          (KIND_SITE, _SITE), (KIND_HOST, _DEV_HOST),
                          (KIND_OWNER, _OWNER_IDENTITY)):
        match = pattern.search(text)
        if match:
            out.append((kind, match.group(0)))
    for value in live_ids:
        if value in text:
            out.append((KIND_LIVE_ID, value))
    return out


def scan_text(text: str, *, filename: str = "<текст>",
              live_ids: frozenset[str] = frozenset()
              ) -> list[tuple[int, str, str]]:
    """Находки файла: (строка, что за литерал, само значение).

    Разбирается `ast`: смотрятся строковые, байтовые и числовые
    константы. Комментарии и строки описания не читаются."""
    tree = ast.parse(text, filename=filename)
    seen: set[tuple[int, str, str]] = set()
    for node in _constants(tree):
        value = node.value
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value in SERVICE_PORTS:
                seen.add((node.lineno, KIND_PORT, str(value)))
            continue
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        if not isinstance(value, str):
            continue
        for kind, what in _text_findings(value, live_ids):
            seen.add((node.lineno, kind, what))
    return sorted(seen)


def scan_file(path: Path, *, live_ids: frozenset[str] = frozenset()
              ) -> list[tuple[int, str, str]]:
    return scan_text(path.read_text(encoding="utf-8"),
                     filename=str(path), live_ids=live_ids)


def watched_files() -> list[Path]:
    """Файлы тестов под сторожем: всё, кроме зоны CDPM и самого сторожа."""
    out = []
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name == SELF or path.name.startswith(SKIP_PREFIXES):
            continue
        out.append(path)
    return out


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# --------------------------------------------------------------- сторож

def test_в_тестах_нет_литералов_среды():
    """Ни один тест не зависит от того, что запущено на машине и что
    лежит в живом каталоге."""
    live_ids = live_catalog_ids()
    files = watched_files()
    problems: list[str] = []
    excused = 0
    for path in files:
        rel = _relative(path)
        allowed = ALLOWLIST.get(rel, {})
        for line, kind, what in scan_file(path, live_ids=live_ids):
            if kind in allowed:
                excused += 1
                continue
            problems.append(refusal(rel, line, kind, what))
    assert not problems, "\n".join(problems)
    print(f"файлов под сторожем: {len(files)}; живых значений каталога "
          f"в перечне: {len(live_ids)}; исключений применено: {excused}")


def test_перечень_исключений_обоснован_и_не_зарос():
    """Каждая строка перечня исключений: файл существует, находка такого
    вида в нём ЕСТЬ, обоснование написано словами. Исключение, под
    которым уже нечего прощать, обязано быть снято."""
    live_ids = live_catalog_ids()
    problems: list[str] = []
    for rel, kinds in ALLOWLIST.items():
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}: файла нет — строку перечня снять")
            continue
        found = {kind for _line, kind, _what in scan_file(path,
                                                          live_ids=live_ids)}
        for kind, why in kinds.items():
            if kind not in found:
                problems.append(f"{rel}: находок вида «{kind}» больше нет — "
                                f"строку перечня снять")
            if len(why.split()) < 5:
                problems.append(f"{rel}: «{kind}» — обоснование не написано "
                                f"словами")
    assert not problems, "\n".join(problems)
    print(f"строк перечня исключений: "
          f"{sum(len(v) for v in ALLOWLIST.values())} в "
          f"{len(ALLOWLIST)} файлах")


def test_сторож_ловит_подложенный_литерал(tmp_path):
    """Самопроверка: сторож видит нарочно подложенное и молчит там, где
    зависимости от среды нет."""
    live = frozenset({"req-15317771322635164531"})
    bad = tmp_path / "test_подложенный.py"
    bad.write_text(
        '# -*- coding: utf-8 -*-\n'
        '"""Строка описания: служба на 8105 и машина LEGION."""\n'
        'URL = "http://127.0.0.1:8105/api/requests"\n'
        'PORT = 9099\n'
        'GATE = r"C:\\work\\TimeFold\\tools\\gate.py"\n'
        'SITE = "https://cos.fregene.ai"\n'
        'HOST = "LEGION\\\\cosmos-agent"\n'
        'RID = "req-15317771322635164531"\n'
        'WHO = "LevRLG@Gmail.com"\n'
        'SLUG = "upload-levrlg--legacy-seed"\n'
        '# комментарий про 8123 и C:\\Users\\lev\n',
        encoding="utf-8")
    got = scan_file(bad, live_ids=live)
    kinds = {kind for _line, kind, _what in got}
    assert kinds == {KIND_LOOPBACK, KIND_PORT, KIND_PATH, KIND_SITE,
                     KIND_HOST, KIND_OWNER, KIND_LIVE_ID}, kinds
    lines = {line for line, _kind, _what in got}
    assert 2 not in lines, "строка описания прочитана как зависимость"
    assert 11 not in lines, "комментарий прочитан как зависимость"
    # личность ловится и в другом регистре, и без домена (учётное имя в
    # слаге набора) — оба написания были в наборе до 10.09.2026
    owner = {what.lower() for _line, kind, what in got if kind == KIND_OWNER}
    assert owner == {"levrlg@gmail.com", "levrlg"}, owner

    good = tmp_path / "test_чистый.py"
    good.write_text(
        '# -*- coding: utf-8 -*-\n'
        '"""Стенд без литералов среды."""\n'
        'URL = "http://catalog.invalid/api/requests"\n'
        'CLOSED = "http://127.0.0.1:1"\n'
        'RID = "req-00000000000000000042"\n'
        'WHO = "owner@stand.invalid"\n'
        'def test_x(tmp_path):\n'
        '    assert (tmp_path / "q.json").parent.is_dir()\n',
        encoding="utf-8")
    assert scan_file(good, live_ids=live) == []

    # Слова отказа называют место, вид литерала и чем его заменить.
    line, kind, what = got[0]
    said = refusal("tests/test_подложенный.py", line, kind, what)
    assert said.startswith("литерал среды в тесте "
                           "tests/test_подложенный.py:%d — " % line)
    assert kind in said and what in said and WORDS in said
    print(f"подложенных литералов поймано: {len(got)} "
          f"({len(kinds)} видов); на чистом файле находок: 0")
