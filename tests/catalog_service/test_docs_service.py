# -*- coding: utf-8 -*-
"""T14: документы службы — контракт, руководства, README каталога.

Контракт (`catalog_service/API-CONTRACT.md`) — шов между дорожками, и он
судится строго: каждая ручка, каждый машинный код и каждая переменная
обязаны быть в нём названы. Документы ЧУЖИХ дорожек (часть 13.9 и прочие
руководства — дорожка D; README установки и контуры — зона CDPM)
судятся, только когда они сданы: без их маркера тест пропускается
СЛОВАМИ, а не молчит и не краснеет за чужую работу.
"""
from __future__ import annotations

import re

import pytest

from catalog_service import errors, identity
from catalog_service.settings import DEFAULT_PORT
from tests.catalog_service.conftest import ROOT

CONTRACT = ROOT / "catalog_service" / "API-CONTRACT.md"
SERVICE_README = ROOT / "catalog_service" / "README.md"
CATALOG_README = ROOT / "catalog" / "README.md"
MANUAL = ROOT / "docs" / "manual"
#: Переменные и ключи §1.4 проекта.
VARIABLES = ("CREW_TEMPLATE_CATALOG", "CATALOG_WORK_DIR", "CATALOG_ACTOR",
             "CATALOG_READ_ONLY", "CREW_COSMOS_BINDINGS",
             "CREW_CDPM_BINDINGS", "CATALOG_MARKERS_FILE", "CREW_CATALOG_URL")
#: Переменная машинного канала заявок (шаг 6, §5.1 проекта). Отдельно от
#: VARIABLES: части руководства дорожки D шага 5 её ещё не знают, и
#: краснеть за чужую несданную работу тест не должен.
STEP6_VARIABLES = ("CATALOG_MACHINE_TOKEN",)
#: Коды отказов шага 6 — перечень С4 проекта: каждый обязан быть назван
#: в §2.1 контракта, иначе продукт увидит код, которого нет в договоре.
STEP6_CODES = ("machine-channel-off", "bad-token", "request-exists",
               "request-closed", "bad-request-id", "no-candidate",
               "candidate-mismatch", "candidate-pending",
               "executors-incomplete")
#: Порты службы по контурам (§1.4): ряд «умолчание + номер контура»
#: (Test и локальная машина — умолчание, Stage +1, Prod +2,
#: docs/knowledge/deploys/contours.md). Считаются от константы кода:
#: литерал разошёлся бы с настройкой при первой же смене порта.
PORTS = tuple(str(DEFAULT_PORT + n) for n in (0, 1, 2))


def _handles(app) -> set[str]:
    out = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api") or path in ("/healthz", "/readyz",
                                               "/versionz"):
            out.add(path)
    return out


def test_contract_names_every_handle(make_service):
    text = CONTRACT.read_text(encoding="utf-8")
    svc = make_service("owner")
    missing = []
    for path in sorted(_handles(svc.app)):
        pattern = re.sub(r"\{[a-z_]+\}", "{", path)
        if pattern.split("{")[0].rstrip("/") not in text:
            missing.append(path)
    assert not missing, f"ручки вне контракта: {missing}"
    print(f"ручек службы названо в контракте: {len(_handles(svc.app))}")


def test_contract_names_every_code_and_variable():
    text = CONTRACT.read_text(encoding="utf-8")
    missing = [c for c in errors.CODES if c not in text]
    assert not missing, f"коды отказов вне контракта: {missing}"
    missing = [v for v in VARIABLES if v not in text]
    assert not missing, f"переменные вне контракта: {missing}"
    assert "owner-only" in text and "read-only" in text
    # Одиннадцатая операция — `support-refresh` (волна 17б, K06):
    # пересчёт свидетельства поддержки кладёт файл `catalog/support.json`,
    # и права у него общие с остальной записью.
    assert len(identity.OPERATIONS) == 11, \
        "одиннадцать операций записи — контракт таблицы прав"
    assert "/ui/#/" in text, "контракт адресов экранов не назван"
    for anchor in ("#/records", "#/dictionaries", "#/requests", "#/executors",
                   "#/versions"):
        assert anchor in text, anchor
    print(f"кодов отказов сверено: {len(errors.CODES)}; переменных: "
          f"{len(VARIABLES)}")


def test_contract_carries_the_machine_channel_of_step_six():
    """С4: машинный канал, закон номера, кандидат и инструмент импорта —
    в контракте буквально. Контракт — шов с продуктами и с сессией CDPM:
    имя заголовка, порядок отказов и форма файла-ответа не могут жить
    только в коде одной стороны."""
    text = CONTRACT.read_text(encoding="utf-8")
    missing = [c for c in STEP6_CODES if c not in text]
    assert not missing, f"коды шага 6 вне контракта: {missing}"
    missing = [v for v in STEP6_VARIABLES if v not in text]
    assert not missing, f"переменные шага 6 вне контракта: {missing}"
    for word in (identity.PRODUCT_HEADER, identity.TOKEN_HEADER,
                 "/api/requests/dry-run", "import-requests", "--inbox",
                 "--reply", "candidate", "cr_key_expected",
                 "request_id_of", "cr_key_of", "kind_request_key",
                 "manual_key", ".reply.json"):
        assert word in text, f"в контракте не названо: {word}"
    from catalog_service import import_requests as tool
    assert tool.IMPORTER in text, "подпись файла-ответа вне контракта"
    from catalog_service import releases
    for key, _title in releases.CHECK_TITLES:
        assert key in text or _title.split()[0] in text, key
    assert len(releases.CHECK_TITLES) == 12, \
        "двенадцать предпроверок выпуска — договор §4 шага 6 и 0.13 " \
        "пакета «паритет на временном каталоге»"
    # Слова приёма — такой же шов, как коды отказов: продукт кладёт их в
    # квитанцию, а инструмент импорта — в файл-ответ площадки. Строка
    # контракта сверяется С ТОЙ ЖЕ функцией службы (номер и ключ —
    # многоточиями): пересказ в документе разошёлся бы с кодом молча.
    from catalog_service import request_queue as rq
    assert rq.accepted_words("req-…", "COSMOS") in text, \
        "слова приёма машинного канала вне контракта"
    assert rq.cr_key_words("req-…", "cr…", "COSMOS") in text, \
        "слова записи ключа CR вне контракта"
    assert "перепишите словами о механике" in text
    print(f"кодов шага 6 сверено: {len(STEP6_CODES)}; предпроверок "
          f"выпуска: {len(releases.CHECK_TITLES)}; слов приёма: 3")


def test_manual_part_13_10_is_judged_only_when_delivered():
    """Часть 13.10 «Шаг 6: конвейер пополнения» — дорожка D. Без её
    маркера тест говорит это словами, а не краснеет за чужую работу."""
    path = MANUAL / "13-katalog-shablonov-RU.md"
    if not path.is_file():                               # pragma: no cover
        pytest.skip("руководства 13 нет")
    text = path.read_text(encoding="utf-8")
    if "13.10" not in text:
        pytest.skip("часть 13.10 «Шаг 6: конвейер пополнения» ещё не сдана "
                    "дорожкой D — судить нечего")
    for word in ("CATALOG_MACHINE_TOKEN", "import-requests",
                 "candidates", "req-", "cr_key"):
        assert word in text, f"13.10 не называет {word}"


def test_manual_13_names_the_parity_law_commands_and_keys():
    """Шов «руководство ↔ код» пакета «паритет на временном каталоге»:
    команды и ключи закона руководство обязано называть ТЕМИ ЖЕ словами,
    какими их пишет код, — иначе владелец наберёт по руководству то, чего
    программа не примет.

    Сверяется машинно: ключи командной строки — из `add_argument`
    движка, строки цепочки — из `assemble`, коды отказов — из закрытого
    перечня службы. Ни одной строки руками здесь не повторено."""
    law = ROOT / "catalog" / "tools" / "parity.py"
    manual = MANUAL / "13-katalog-shablonov-RU.md"
    for p in (law, manual):
        if not p.is_file():                              # pragma: no cover
            pytest.skip(f"{p.name} отсутствует — судить нечего")
    text = manual.read_text(encoding="utf-8")
    keys = sorted(set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"',
                                 law.read_text(encoding="utf-8"))))
    assert keys, "у закона паритета не нашлось ни одного ключа"
    missing = [k for k in keys if f"`{k}`" not in text]
    assert not missing, f"13.13 не называет ключи закона: {missing}"

    from catalog_service import assemble as asm
    # Вся цепочка `next`, а не только её края: разойдись руководство с
    # ответом службы хоть ключом, владелец наберёт не ту команду.
    for command in asm.NEXT_COMMANDS:
        assert command in text, f"руководство не называет команду: {command}"
    assert asm.NEXT_COMMANDS[0] == asm.REFRESH_COMMAND
    assert asm.NEXT_COMMANDS[-1] == asm.PARITY_COMMAND

    for code in ("parity-red", "parity-unconfirmed", "confirm_parity"):
        assert f"`{code}`" in text, f"руководство не называет {code}"
        if code != "confirm_parity":
            assert code in errors.CODES, f"{code} вне закрытого перечня"
    # Строка чек-листа выпуска — двенадцатая: число в руководстве и длина
    # перечня заголовков обязаны сойтись.
    from catalog_service import releases as rel
    n = len(rel.CHECK_TITLES)
    assert rel.CHECK_TITLES[n - 1][0] == "parity"
    assert f"({n})" in text, f"руководство не называет строку ({n})"
    print(f"13.13 сверено с кодом: ключей {len(keys)} ({', '.join(keys)}), "
          f"команд цепочки {len(asm.NEXT_COMMANDS)}, предпроверок {n}")


def test_service_readme_says_how_to_run_and_check():
    text = SERVICE_README.read_text(encoding="utf-8")
    assert "-m catalog_service" in text
    assert "--actor owner" in text and "--read-only" in text
    assert str(DEFAULT_PORT) in text
    assert "Как проверить" in text
    assert "127.0.0.1" in text
    print("разделов README службы проверено: запуск, режимы, как проверить")


def test_catalog_readme_names_the_service_and_only_check(request):
    if not CATALOG_README.is_file():                     # pragma: no cover
        pytest.skip("catalog/README.md отсутствует")
    text = CATALOG_README.read_text(encoding="utf-8")
    if "правки — службой" not in text:
        pytest.skip("catalog/README.md ещё не правлен дорожкой D "
                    "(правило «правки — службой каталога») — сдаётся с её "
                    "пакетом")
    assert "--check" in text
    writes = [line for line in text.splitlines()
              if "build_from_code" in line and "--out" in line
              and "--check" not in line]
    assert not writes, f"в README каталога осталась команда записи: {writes}"


def test_manual_part_13_carries_the_service_section():
    path = MANUAL / "13-katalog-shablonov-RU.md"
    if not path.is_file():                               # pragma: no cover
        pytest.skip("руководства 13 нет")
    text = path.read_text(encoding="utf-8")
    if "13.9" not in text:
        pytest.skip("часть 13.9 «Шаг 5: служба каталога и пять экранов» "
                    "ещё не сдана дорожкой D — судить нечего")
    for word in ("catalog_service", "--actor owner", "Как проверить"):
        assert word in text, word
    missing = [v for v in VARIABLES if v not in text]
    assert not missing, f"переменные вне 13.9: {missing}"
    assert str(DEFAULT_PORT) in text


def test_manual_reference_and_contours_carry_the_ports():
    plan = {"09-spravochnik-RU.md": VARIABLES,
            "01-vvedenie-i-ustrojstvo-RU.md": (str(DEFAULT_PORT),),
            "08-administrirovanie-RU.md": PORTS}
    checked = 0
    for name, needed in plan.items():
        path = MANUAL / name
        if not path.is_file():                           # pragma: no cover
            pytest.skip(f"{name} отсутствует")
        text = path.read_text(encoding="utf-8")
        if "catalog_service" not in text and str(DEFAULT_PORT) not in text:
            pytest.skip(f"{name} ещё не правлена дорожкой D — судить нечего")
        missing = [w for w in needed if w not in text]
        assert not missing, f"{name}: не названо {missing}"
        checked += 1
    print(f"частей руководства сверено: {checked}")


def test_index_lists_part_13():
    path = MANUAL / "00-INDEX-RU.md"
    if not path.is_file():                               # pragma: no cover
        pytest.skip("оглавления нет")
    text = path.read_text(encoding="utf-8")
    if "13-katalog-shablonov-RU.md" not in text:
        pytest.skip("строка части 13 в оглавлении ещё не сдана дорожкой D — "
                    "судить нечего")
    assert "Каталог шаблонов" in text


def test_cdpm_zone_documents_are_judged_only_when_delivered():
    """Часть CDPM (установщик, README установки, контуры) — её зона.
    Без её маркера тест говорит это словами и пропускается."""
    installer = ROOT / "install_cosmos.py"
    marker = installer.is_file() and "CATALOG_PORT" in \
        installer.read_text(encoding="utf-8", errors="replace")
    if not marker:
        pytest.skip("часть CDPM не сдана: в установщике нет CATALOG_PORT — "
                    "документы её зоны не судятся")
    readme = ROOT / "docs" / "install" / "README-RU.md"
    if readme.is_file():
        assert str(DEFAULT_PORT) in readme.read_text(encoding="utf-8")
    contours = ROOT / "docs" / "knowledge" / "deploys" / "contours.md"
    if contours.is_file():
        text = contours.read_text(encoding="utf-8")
        assert all(p in text for p in PORTS), "порты службы вне таблицы контуров"


def test_service_files_carry_no_customer_markers():
    """Каталог и служба обезличены по построению: маркеров заказчика в
    коде и контракте службы нет."""
    try:
        from tools import customer_markers as cm
    except Exception:                                    # pragma: no cover
        pytest.skip("линт маркеров недоступен")
    spec = cm.load_checks()
    if not spec:
        pytest.skip("перечня маркеров на машине нет — судить нечем")
    pats = []
    for check in spec.get("checks") or []:
        if isinstance(check, dict):
            pats += cm._patterns(check)
    files = sorted(list((ROOT / "catalog_service").rglob("*.py"))
                   + list((ROOT / "catalog_service").rglob("*.md"))
                   + list((ROOT / "catalog_service").rglob("*.json")))
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in pats):
                found.append(f"{path.name}:{n}")
    assert not found, f"маркеры заказчика в файлах службы: {found}"
    print(f"файлов службы просмотрено линтом маркеров: {len(files)}")
