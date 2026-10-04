# -*- coding: utf-8 -*-
"""Паспорт прогона: все параметры прогона объявляются и сверяются ДО его
начала.

Слова владельца 10.09.2026 (дословно): «Перед каждым прогоном нужно
чётко установить все его параметры, в том числе: модели LLM, которые
будут использоваться; солверы, которые будут использоваться; все
библиотеки; все каталоги, в том числе каталог правил, которые будут
использоваться перед этим прогоном. Не может быть, что остаётся старый
каталог предыдущего прогона. Каждый прогон имеет свои параметры» и «и
ещё, конечно, набор правил авиакомпании и набор данных авиакомпании, на
которых будет делаться прогон. Это тоже является параметром прогона,
который должен вместе с ним храниться и задаваться до его начала».

Повод — два спотыкания показа фазы 1 Н0-C 09.09.2026 (снимок каталога
0.3.0):

  1. раскладка моделей искалась по относительному пути от ПАПКИ НАБОРА
     («../polyaris-h0/agents-layout-h0.json»); у копии набора, лежащей
     вне open-bench, такого соседа нет — прогон падал на первой стадии;
  2. гейт эквивалентности читал привязки исполнителей ДЕРЕВА (собранные
     по каталогу 0.4.0), тогда как каталог прогона был 0.3.0 из
     переменной CREW_TEMPLATE_CATALOG: сторож дрейфа отказал НА
     СЕРЕДИНЕ прогона, когда полчаса работы моделей уже потрачены.

Оба случая — один и тот же корень: параметр прогона брался «как
получится» в тот момент, когда он понадобился, а не назывался до
старта. Паспорт закрывает корень, а не два случая: он СОБИРАЕТ один
объявленный набор параметров и СВЕРЯЕТ его с действительностью до
первой стадии; любое расхождение — отказ словами «объявлено … / на
деле …», и ни один вызов модели не тратится.

Что в паспорте (и откуда берётся):

  * набор правил авиакомпании — файлы документов из манифеста набора,
    sha256 каждого; отпечатки СЧИТАЮТСЯ ОТ ФАЙЛОВ НАБОРА при сборке
    паспорта (см. ниже «манифест объявляет состав, паспорт записывает
    факт») и после заморозки сверяются с манифестом заморозки CDPM;
  * набор данных авиакомпании — так же, книга операционных данных;
  * каталог правил — папка, версия и отпечаток из catalog.json; источник
    называется словами: явный ключ, переменная контура либо «каталог
    дерева <версия> (<отпечаток>)». Манифест набора объявляет версию и
    отпечаток каталога, которым набор выпущен, — они обязаны совпасть;
  * привязки исполнителей COSMOS — файл, его catalog_version и
    catalog_fingerprint ОБЯЗАНЫ равняться каталогу паспорта; привязки
    дерева берутся молча только тогда, когда каталог прогона — тот же
    каталог дерева, иначе отказ «привязки для каталога X не названы»;
  * раскладка моделей по ролям — путь ОТ КОРНЯ репозитория (или
    абсолютный), sha256 файла и сами модели по ролям;
  * решатель — адрес без учётных данных, проба живости рабочей ручкой
    (ручки версии у решателя нет — версия берётся из файлов сборки:
    отпечатки jar, коммит манифеста сборки, версии Timefold/Quarkus);
    решатель недоступен — отказ, если стадии до заданной его требуют,
    иначе честная пометка «не требуется до стадии X»;
  * окружение Python — версия интерпретатора, площадка, отсортированный
    перечень установленных пакетов с версиями и sha256 этого перечня;
  * код — коммит и отпечаток исходников (даёт вызывающий: у раннера
    свой счёт исполняемых корней);
  * панель CDPM — при работе с внешней службой её версия каталога
    (`GET /api/catalog`) обязана совпасть с каталогом паспорта; панель в
    процессе — так и сказано словами.

Почему отпечатки документов НЕ лежат в манифесте набора. Манифест —
источник ОБЪЯВЛЕНИЯ СОСТАВА: какие файлы образуют набор (и какой
каталог, какая раскладка). Байты документов в манифесте были бы вторым
источником правды, который стареет по построению: сборщики набора
переписывают docx/xlsx (архив с отметками времени внутри) при каждом
перевыпуске, и отпечаток в манифесте расходился бы с файлом при любой
перегенерации манифеста без перевыпуска документов. Объявление, которое
регулярно неверно, хуже отсутствия объявления. Поэтому паспорт —
ЗАПИСЬ ФАКТА: отпечатки считаются от файлов набора до старта, печатаются
в журнал, ложатся файлом рядом с итогами прогона и сверяются с тем, что
конвейер ЗАМОРОЗИЛ. Сверка двух паспортов двух прогонов — и есть рецепт
воспроизведения.

Секретов в паспорте нет по построению: читаются только двери (пути
каталога и привязок, адрес решателя, адрес панели) и файлы набора;
значения переменных с ключами и токенами не читаются вовсе, а из адресов
убирается «пользователь:пароль@».

Модуль — продуктовый и не зависит от раннера: им пользуется раннер
наборов (open-bench/polyaris-h0/run_local_pipeline.py), и его же читают
панели CDPM и COSMOS, когда показывают, чем шёл прогон.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Имя схемы паспорта: читатель обязан знать, что читает.
SCHEMA = "cosmos-run-passport/v1"
#: Имя файла паспорта в каталоге прогона и рядом с итогами.
PASSPORT_FILE = "run-passport.json"

#: Ключ манифеста набора с путём раскладки моделей ОТ КОРНЯ репозитория.
#: Заведён 10.09.2026: прежний ключ (ниже) называл путь от папки набора,
#: и копия набора вне open-bench раскладку не находила.
LAYOUT_KEY_REPO = "agents_layout_from_repo"
#: Прежний ключ: путь от ПАПКИ НАБОРА. Остаётся читаемым — наборы,
#: выпущенные раньше, обязаны идти как прежде.
LAYOUT_KEY_KIT = "agents_layout"
#: Умолчание прежнего ключа — файл рядом с манифестом набора.
LAYOUT_DEFAULT = "agents-layout-h0.json"
#: Служебные ключи файла раскладки: ролями не являются.
LAYOUT_AUX = ("form_version", "customer", "_comment")
#: Поля канала раскладки, которые входят в паспорт (ключей здесь нет и
#: быть не может — раскладка их не хранит).
CHANNEL_FIELDS = ("provider", "model", "effort", "temperature")

#: Двери контура: одно место, где названо, чем задаётся параметр.
#: Значения читает сам модуль, менять их — дело вызывающего.
CATALOG_ENV = "CREW_TEMPLATE_CATALOG"
BINDINGS_ENV = "CREW_COSMOS_BINDINGS"
SOLVER_ENV = "CREW_SOLVER_URL"

#: Слова об источнике параметра.
SRC_KEY = "ключ командной строки"
SRC_ENV = "переменная контура"
SRC_MANIFEST = "манифест набора"
SRC_TREE = "дерево"

#: Стадии раннера наборов, которым НУЖЕН решатель. Пусто, и это не
#: недосмотр: конвейер CDPM (заморозка → извлечение → цикл → пакет)
#: решателя не касается — его касаются НАЖАТИЯ COSMOS, идущие отдельным
#: прогоном. Поэтому недоступный решатель не останавливает прогон CDPM,
#: а честно помечается в паспорте «не требуется до стадии X».
SOLVER_STAGES: tuple = ()

#: Рабочая ручка решателя: ручки версии у него нет (живая служба
#: отвечала 404 на /q/health — находка прогона кнопки 24.08.2026), и
#: живость судится тем же обращением, что у кнопки COSMOS.
SOLVER_PROBE_PATH = "/schedules/applied-rules"
#: Ручка панели CDPM, называющая её каталог шаблонов.
PANEL_CATALOG_PATH = "/api/catalog"
#: Потолок пробы по сети, с: паспорт собирается ДО старта, и ждать
#: молчащий адрес минутами незачем.
PROBE_TIMEOUT_S = 10.0


# ----------------------------------------------------------- служебное
def sha256_file(path) -> str:
    """sha256 файла целиком."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(value, n: int = 12) -> str:
    """Отпечаток для слов: первые n знаков и многоточие."""
    s = str(value or "")
    return (s[:n] + "…") if len(s) > n else (s or "нет")


def disagreement(what: str, declared, actual) -> str:
    """Слова расхождения — ровно те, которых требует владелец: что
    именно, что объявлено и что на деле."""
    return f"{what}: объявлено {declared} / на деле {actual}"


def strip_userinfo(url):
    """`http://user:pass@host:9099/x` → `http://host:9099/x`. Одна
    формула на весь продукт — та же, которой пользуется журнал вызовов;
    её отсутствие (сокращённая установка) не должно ронять паспорт."""
    if not isinstance(url, str) or not url:
        return None
    try:
        from vmp.run.call_journal import _strip_userinfo
        return _strip_userinfo(url)
    except Exception:                                     # noqa: BLE001
        from urllib.parse import urlsplit, urlunsplit
        try:
            parts = urlsplit(url)
        except ValueError:                                # pragma: no cover
            return url
        if not parts.netloc or "@" not in parts.netloc:
            return url
        host = parts.netloc.rsplit("@", 1)[1]
        return urlunsplit((parts.scheme, host, parts.path, parts.query,
                           parts.fragment))


def scrub_text(text) -> str:
    """Убрать «пользователь:пароль@» из URL-подобных подстрок сообщения:
    httpx и urllib подставляют в текст исключения полный адрес запроса
    вместе с учётными данными, а паспорт печатается в журнал."""
    try:
        from vmp.run.call_journal import strip_userinfo_text
        return strip_userinfo_text(text)
    except Exception:                                     # noqa: BLE001
        import re
        return re.sub(r"\b([a-z][a-z0-9+.\-]*://)[^/\s@]+@", r"\1",
                      str(text), flags=re.I)


def _rel(path, repo: Path = REPO) -> str:
    """Путь от корня репозитория, если он внутри; иначе — как есть."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(repo.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(p)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def http_json(url: str, timeout_s: float = PROBE_TIMEOUT_S) -> dict:
    """Ответ ручки одним словарём: {"status": код|None, "body": разбор|None,
    "error": слова|None}. Сеть не роняет сборку паспорта — недоступность
    судится вызывающим, а не исключением на полпути."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:  # noqa: S310
            raw = r.read(1 << 20)
            code = int(getattr(r, "status", 0) or r.getcode() or 0)
    except urllib.error.HTTPError as e:
        return {"status": int(e.code), "body": None, "error": None}
    except Exception as e:                                # noqa: BLE001
        return {"status": None, "body": None,
                "error": scrub_text(str(e))[:300]}
    try:
        return {"status": code, "body": json.loads(raw.decode("utf-8")),
                "error": None}
    except (ValueError, UnicodeDecodeError):
        return {"status": code, "body": None, "error": None}


# --------------------------------------------- набор: правила и данные
def kit_files(kit_dir, manifest: dict, key: str, title: str,
              repo: Path = REPO) -> tuple[list, list]:
    """Объявленные файлы набора по ключу манифеста с отпечатками:
    (перечень, расхождения). Файла нет — расхождение словами; выдумывать
    отпечаток нечему."""
    kit = Path(kit_dir)
    rows: list = []
    problems: list = []
    for rel in manifest.get(key) or []:
        p = kit / str(rel)
        if not p.is_file():
            problems.append(disagreement(
                f"{title}: файл {rel}", "объявлен манифестом набора",
                f"файла нет ({_rel(p, repo)})"))
            continue
        rows.append({"file": _rel(p, repo), "name": p.name,
                     "sha256": sha256_file(p), "bytes": p.stat().st_size})
    return rows, problems


def kit_references(kit_dir, manifest: dict, repo: Path = REPO) -> dict:
    """Опорные файлы набора, которыми судится прогон: книга ответов,
    эталон ВМП, эталонная форма, строки jpg. Их подмена меняет вердикт
    гейта так же, как подмена документов, — отпечатки идут в паспорт.
    Пути эталонов манифест называет от КОРНЯ репозитория, книгу ответов и
    строки jpg — от папки набора."""
    kit = Path(kit_dir)
    out: dict = {}
    for key, base in (("answer_book", kit), ("jpg_lines", kit),
                      ("etalon_vmp", repo), ("etalon_form", repo)):
        rel = manifest.get(key)
        if not rel:
            continue
        p = Path(rel)
        p = p if p.is_absolute() else (base / str(rel))
        out[key] = {"file": _rel(p, repo),
                    "sha256": sha256_file(p) if p.is_file() else None,
                    "present": p.is_file()}
    return out


# ------------------------------------------------------ каталог правил
def catalog_block(manifest: dict, explicit: str | None = None) -> tuple[dict, list]:
    """Каталог правил прогона: папка, версия, отпечаток и ИСТОЧНИК
    словами. Объявление манифеста набора (`catalog {version,
    fingerprint}`) обязано совпасть с каталогом, которым идёт продукт.

    «Умолчание из дерева» — законная конфигурация, но названная явно:
    паспорт пишет «каталог дерева <версия> (<отпечаток>)», и читатель
    видит, что каталог не был выбран, а достался."""
    from vmp import catalog_access as ca
    env = (os.environ.get(CATALOG_ENV) or "").strip()
    if explicit:
        source = f"{SRC_KEY} --catalog"
    elif env:
        source = f"{SRC_ENV} {CATALOG_ENV}"
    else:
        source = SRC_TREE
    declared = manifest.get("catalog") if isinstance(manifest.get("catalog"),
                                                     dict) else None
    block = {"source": source, "path": None, "version": None,
             "fingerprint": None, "declared": declared, "words": None}
    problems: list = []
    try:
        st = ca.stamp()
    except Exception as e:                                # noqa: BLE001
        block["error"] = str(e)[:300]
        block["words"] = f"каталог правил ({source}) не читается: {block['error']}"
        problems.append(disagreement(
            "каталог правил",
            (f"{(declared or {}).get('version')} "
             f"({short((declared or {}).get('fingerprint'))})") if declared
            else "каталог продукта",
            f"каталог не читается: {block['error']}"))
        return block, problems
    block.update({"path": st.get("path"), "version": st.get("version"),
                  "fingerprint": st.get("fingerprint")})
    where = ("каталог дерева" if source == SRC_TREE
             else f"каталог ({source})")
    block["words"] = (f"{where} {block['version']} "
                      f"({short(block['fingerprint'])})")
    if declared is None:
        block["declared_words"] = ("набор каталог не закрепляет (в манифесте "
                                   "нет блока catalog) — сверять не с чем")
        return block, problems
    same = (str(declared.get("version")) == str(block["version"])
            and str(declared.get("fingerprint")) == str(block["fingerprint"]))
    if not same:
        problems.append(disagreement(
            "каталог правил",
            f"{declared.get('version')} ({short(declared.get('fingerprint'))}) "
            f"— {SRC_MANIFEST}",
            f"{block['version']} ({short(block['fingerprint'])}) — {where}"))
    return block, problems


# ------------------------------------------- привязки исполнителей COSMOS
def bindings_block(catalog: dict, explicit: str | None = None
                   ) -> tuple[dict, list]:
    """Привязки исполнителей COSMOS прогона: файл, каталог, по которому
    они собраны, отпечаток канона и ИСТОЧНИК словами.

    Правило: версия и отпечаток каталога В ФАЙЛЕ привязок обязаны
    равняться каталогу паспорта. Привязки дерева берутся молча только
    тогда, когда каталог прогона — тот самый каталог дерева; каталог
    выбран другой, а привязки не названы — отказ «привязки для каталога
    X не названы» ДО старта, а не сторожем дрейфа на середине прогона
    (живой случай Н0-C, 09.09.2026)."""
    from vmp import catalog_access as ca
    env = (os.environ.get(BINDINGS_ENV) or "").strip()
    if explicit:
        source = f"{SRC_KEY} --bindings"
    elif env:
        source = f"{SRC_ENV} {BINDINGS_ENV}"
    else:
        source = SRC_TREE
    path = ca.bindings_path()
    block = {"source": source, "file": _rel(path), "present": path.is_file(),
             "catalog_version": None, "catalog_fingerprint": None,
             "fingerprint": None, "product_version": None,
             "generated_at": None, "words": None}
    problems: list = []
    doc = _read_json(path) if path.is_file() else None
    if not isinstance(doc, dict):
        block["words"] = f"привязки COSMOS ({source}): файл не читается"
        problems.append(disagreement(
            "привязки COSMOS",
            f"{catalog.get('version')} ({short(catalog.get('fingerprint'))})",
            (f"файла нет ({block['file']})" if not path.is_file()
             else f"файл не разобран как JSON ({block['file']})")))
        return block, problems
    block.update({
        "catalog_version": doc.get("catalog_version"),
        "catalog_fingerprint": doc.get("catalog_fingerprint"),
        "product_version": doc.get("product_version"),
        "generated_at": doc.get("generated_at"),
        "rows": len(doc.get("bindings") or [])})
    try:
        from vmp.bindings_build import fingerprint as _fp
        block["fingerprint"] = _fp(doc)
    except Exception:                                     # noqa: BLE001
        block["fingerprint"] = None
    block["words"] = (f"привязки COSMOS ({source}) собраны по каталогу "
                      f"{block['catalog_version']} "
                      f"({short(block['catalog_fingerprint'])}), строк "
                      f"{block.get('rows')}")
    same = (str(block["catalog_version"]) == str(catalog.get("version"))
            and str(block["catalog_fingerprint"])
            == str(catalog.get("fingerprint")))
    if same:
        return block, problems
    if source == SRC_TREE:
        problems.append(
            f"привязки для каталога {catalog.get('version')} "
            f"({short(catalog.get('fingerprint'))}) не названы (ключ "
            f"--bindings или переменная {BINDINGS_ENV}); "
            + disagreement("привязки дерева",
                           f"каталог {catalog.get('version')} "
                           f"({short(catalog.get('fingerprint'))})",
                           f"каталог {block['catalog_version']} "
                           f"({short(block['catalog_fingerprint'])})"))
    else:
        problems.append(disagreement(
            f"привязки COSMOS ({source})",
            f"каталог {catalog.get('version')} "
            f"({short(catalog.get('fingerprint'))})",
            f"каталог {block['catalog_version']} "
            f"({short(block['catalog_fingerprint'])})"))
    return block, problems


# ------------------------------------------------- раскладка моделей
def resolve_layout(kit_dir, manifest: dict, repo: Path = REPO) -> dict:
    """Файл раскладки моделей: {"file": Path|None, "source": слова,
    "tried": [пути]}.

    Порядок: путь ОТ КОРНЯ репозитория (ключ `agents_layout_from_repo`,
    абсолютный принимается как есть) → прежний путь от ПАПКИ НАБОРА
    (ключ `agents_layout`, умолчание — файл рядом с манифестом). Файла
    нет ни там, ни там — file None и перечень испробованных путей: отказ
    обязан назвать оба, иначе «файла нет» ничего не объясняет."""
    kit, repo = Path(kit_dir), Path(repo)
    tried: list = []
    rel = manifest.get(LAYOUT_KEY_REPO)
    if rel:
        p = Path(str(rel))
        p = p if p.is_absolute() else (repo / p)
        tried.append(_rel(p, repo))
        if p.is_file():
            return {"file": p, "tried": tried,
                    "source": f"{LAYOUT_KEY_REPO} манифеста набора "
                              f"(путь от корня репозитория)"}
    legacy = manifest.get(LAYOUT_KEY_KIT) or LAYOUT_DEFAULT
    p = Path(str(legacy))
    p = p if p.is_absolute() else (kit / p)
    tried.append(_rel(p, repo))
    if p.is_file():
        return {"file": p, "tried": tried,
                "source": (f"{LAYOUT_KEY_KIT} манифеста набора (путь от папки "
                           f"набора)" if manifest.get(LAYOUT_KEY_KIT)
                           else f"умолчание {LAYOUT_DEFAULT} рядом с манифестом")}
    return {"file": None, "tried": tried, "source": None}


def layout_roles(doc: dict | None) -> dict:
    """Модели по ролям из файла раскладки: {роль: {канал: {провайдер,
    модель, усилие, температура}}}. Служебные ключи файла ролями не
    считаются; ключей провайдеров раскладка не хранит."""
    out: dict = {}
    for role, cfg in sorted((doc or {}).items()):
        if role in LAYOUT_AUX or not isinstance(cfg, dict):
            continue
        chans: dict = {}
        for name, ch in sorted((cfg.get("channels") or {}).items()):
            if isinstance(ch, dict):
                chans[str(name)] = {k: ch.get(k) for k in CHANNEL_FIELDS
                                    if ch.get(k) is not None}
        out[str(role)] = chans
    return out


def layout_block(kit_dir, manifest: dict, repo: Path = REPO
                 ) -> tuple[dict, list]:
    info = resolve_layout(kit_dir, manifest, repo)
    if info["file"] is None:
        return ({"source": None, "file": None, "sha256": None, "roles": {},
                 "tried": info["tried"],
                 "words": "раскладка моделей не найдена"},
                [disagreement("раскладка моделей по ролям",
                              "путь манифеста набора "
                              + ", ".join(info["tried"]),
                              "файла нет ни по одному из путей")])
    doc = _read_json(info["file"])
    if not isinstance(doc, dict):
        return ({"source": info["source"], "file": _rel(info["file"], repo),
                 "sha256": sha256_file(info["file"]), "roles": {},
                 "tried": info["tried"],
                 "words": "раскладка моделей не разобрана"},
                [disagreement("раскладка моделей по ролям",
                              _rel(info["file"], repo),
                              "файл не разобран как JSON")])
    roles = layout_roles(doc)
    words = ", ".join(
        f"{role}={(ch.get('cloud') or next(iter(ch.values()), {})).get('model') or '?'}"
        for role, ch in roles.items())
    return ({"source": info["source"], "file": _rel(info["file"], repo),
             "sha256": sha256_file(info["file"]), "roles": roles,
             "tried": info["tried"], "words": words}, [])


# ---------------------------------------------------------- решатель
def solver_block(url: str | None = None, until: str | None = None,
                 explicit: str | None = None,
                 probe: bool = True) -> tuple[dict, list]:
    """Решатель прогона: адрес БЕЗ учётных данных, проба живости рабочей
    ручкой и версия по файлам сборки (ручки версии у решателя нет).

    Недоступность — отказ ТОЛЬКО тогда, когда стадии до `until` решателя
    требуют (SOLVER_STAGES); у конвейера CDPM они его не требуют, и
    паспорт честно пишет «не требуется до стадии <until>» вместо
    молчания."""
    env = (os.environ.get(SOLVER_ENV) or "").strip()
    raw = (url or env or "").strip() or None
    if explicit:
        source = f"{SRC_KEY} --solver-url"
    elif env:
        source = f"{SRC_ENV} {SOLVER_ENV}"
    else:
        source = None
    required = bool(SOLVER_STAGES) and (until is None or until in SOLVER_STAGES)
    block = {"source": source, "url": strip_userinfo(raw), "engine": "timefold",
             "required": required, "reachable": None, "probe": None,
             "version_endpoint": None,
             "version_endpoint_code": "no-version-endpoint",
             "build": None, "words": None}
    problems: list = []
    try:
        from webapp import build_identity as bi
        sol = (bi.build_identity() or {}).get("solver") or {}
        if sol.get("jar_present") or sol.get("app_jar_sha256") \
                or sol.get("timefold_version"):
            block["build"] = {
                "launcher_jar_sha256": sol.get("jar_sha256"),
                "app_jar_sha256": sol.get("app_jar_sha256"),
                "manifest_commit_short": sol.get("manifest_commit_short"),
                "matches_manifest": sol.get("matches_manifest"),
                "timefold_version": sol.get("timefold_version"),
                "quarkus_version": sol.get("quarkus_version"),
                "config_sha256": sol.get("config_sha256")}
    except Exception as e:                                # noqa: BLE001
        block["build_error"] = str(e)[:200]
    if raw is None:
        block["words"] = ("решатель не назван (ключ --solver-url либо "
                          f"{SOLVER_ENV})"
                          + ("" if required else
                             f" — не требуется до стадии {until or 'package'}"))
        if required:
            problems.append(disagreement(
                "решатель", f"адрес до стадии {until}", "адрес не назван"))
        return block, problems
    if probe:
        got = http_json(block["url"].rstrip("/") + SOLVER_PROBE_PATH,
                        PROBE_TIMEOUT_S)
        # Живость — по РАБОЧЕЙ ручке: любой ответ ниже 500 значит
        # «служба жива»; не встало соединение — мертва.
        block["probe"] = {"path": SOLVER_PROBE_PATH, "status": got["status"],
                          "error": got["error"]}
        block["reachable"] = bool(got["status"] is not None
                                  and got["status"] < 500)
    build = block["build"] or {}
    block["words"] = (
        f"решатель {block['url']}: "
        + ("жив" if block["reachable"] else
           "не отвечает" if block["reachable"] is False else "не опрошен")
        + (f", jar {short(build.get('app_jar_sha256'))}, коммит сборки "
           f"{build.get('manifest_commit_short')}" if build else
           ", версия по файлам сборки неизвестна")
        + ("" if required else
           f"; не требуется до стадии {until or 'package'}"))
    if required and block["reachable"] is False:
        why = (block["probe"] or {}).get("error") \
            or f"HTTP {(block['probe'] or {}).get('status')}"
        problems.append(disagreement(
            "решатель", f"живая служба {block['url']} до стадии {until}",
            f"не отвечает ({why})"))
    return block, problems


# --------------------------------------------------- окружение Python
def python_block() -> dict:
    """Версия интерпретатора, площадка и ВСЕ установленные пакеты с
    версиями (отсортированно) плюс sha256 этого перечня: «все
    библиотеки» — слово владельца. Отпечаток перечня даёт сверку двух
    прогонов одной строкой, не читая двести имён."""
    import importlib.metadata as md
    seen: dict = {}
    try:
        for dist in md.distributions():
            name = (dist.metadata["Name"] if dist.metadata else None) \
                or getattr(dist, "name", None)
            if not name:
                continue
            seen[str(name)] = str(dist.version)
    except Exception:                                     # noqa: BLE001
        pass
    rows = [{"name": n, "version": v} for n, v in sorted(seen.items())]
    listing = "\n".join(f"{r['name']}=={r['version']}" for r in rows)
    return {"version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "packages": rows, "packages_count": len(rows),
            "packages_sha256": sha256_text(listing)}


# -------------------------------------------------------- панель CDPM
def panel_block(base_url: str | None, catalog: dict,
                probe: bool = True) -> tuple[dict, list]:
    """Панель CDPM прогона. Без адреса — панель в процессе (тот же код,
    тот же каталог, что у паспорта), и так сказано словами. С адресом —
    её версия каталога (`GET /api/catalog`) ОБЯЗАНА совпасть с каталогом
    паспорта: панель со своим каталогом посадит правила на чужие
    шаблоны, и узнать это на середине прогона поздно."""
    if not base_url:
        return ({"base_url": None, "in_process": True, "catalog": None,
                 "words": "панель в процессе (каталог — каталог паспорта)"},
                [])
    block = {"base_url": strip_userinfo(base_url), "in_process": False,
             "catalog": None, "words": None}
    problems: list = []
    if not probe:
        block["words"] = f"панель {block['base_url']}: не опрошена"
        return block, problems
    got = http_json(str(block["base_url"]).rstrip("/") + PANEL_CATALOG_PATH,
                    PROBE_TIMEOUT_S)
    body = got["body"] if isinstance(got["body"], dict) else None
    if body is None:
        why = got["error"] or f"HTTP {got['status']}"
        block["words"] = (f"панель {block['base_url']}: ручка "
                          f"{PANEL_CATALOG_PATH} не ответила ({why})")
        problems.append(disagreement(
            "каталог панели CDPM",
            f"{catalog.get('version')} ({short(catalog.get('fingerprint'))})",
            f"ручка {PANEL_CATALOG_PATH} не ответила ({why})"))
        return block, problems
    block["catalog"] = {"version": body.get("version"),
                        "fingerprint": body.get("fingerprint"),
                        "available": body.get("available"),
                        "path": body.get("path")}
    block["words"] = (f"панель {block['base_url']}: каталог "
                      f"{body.get('version')} "
                      f"({short(body.get('fingerprint'))})")
    same = (str(body.get("version")) == str(catalog.get("version"))
            and str(body.get("fingerprint")) == str(catalog.get("fingerprint")))
    if not same:
        problems.append(disagreement(
            "каталог панели CDPM",
            f"{catalog.get('version')} ({short(catalog.get('fingerprint'))}) "
            f"— каталог паспорта",
            f"{body.get('version')} ({short(body.get('fingerprint'))}) — "
            f"панель {block['base_url']}"))
    return block, problems


# ------------------------------------------------------------ паспорт
def build(*, kit_dir, manifest: dict | None = None, repo: Path = REPO,
          code: dict | None = None, base_url: str | None = None,
          until: str | None = None, catalog_key: str | None = None,
          bindings_key: str | None = None, solver_key: str | None = None,
          probe: bool = True, at: str | None = None) -> dict:
    """Собрать ОДИН объявленный набор параметров прогона и СВЕРИТЬ его с
    действительностью. Возврат — паспорт; расхождения лежат в ключе
    `problems` (пусто — паспорт чист). Исключений сборка не бросает:
    её дело — назвать все расхождения сразу, а не первое встретившееся.

    Двери (каталог, привязки, решатель) читаются из окружения: их
    выставляет вызывающий ДО сборки; ключи `*_key` нужны лишь для того,
    чтобы паспорт назвал ИСТОЧНИК словами."""
    kit = Path(kit_dir).resolve()
    man_path = kit / "kit-manifest.json"
    manifest = manifest if manifest is not None else (_read_json(man_path) or {})
    problems: list = []

    rules, p1 = kit_files(kit, manifest, "rules_files",
                          "набор правил авиакомпании", repo)
    data, p2 = kit_files(kit, manifest, "data_files",
                         "набор данных авиакомпании", repo)
    problems += p1 + p2
    catalog, p3 = catalog_block(manifest, catalog_key)
    problems += p3
    bindings, p4 = bindings_block(catalog, bindings_key)
    problems += p4
    layout, p5 = layout_block(kit, manifest, repo)
    problems += p5
    solver, p6 = solver_block(until=until, explicit=solver_key, probe=probe)
    problems += p6
    panel, p7 = panel_block(base_url, catalog, probe=probe)
    problems += p7

    return {
        "schema": SCHEMA,
        "at": at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kit": {"dir": _rel(kit, repo),
                "manifest": _rel(man_path, repo),
                "manifest_sha256": sha256_file(man_path)
                if man_path.is_file() else None,
                "customer": manifest.get("customer"),
                "stage": manifest.get("stage"),
                "phase": manifest.get("phase"),
                "data_class": manifest.get("data_class"),
                "llm_scope": manifest.get("llm_scope"),
                "references": kit_references(kit, manifest, repo)},
        "rules_documents": rules,
        "data_documents": data,
        "catalog": catalog,
        "bindings": bindings,
        "agents_layout": layout,
        "solver": solver,
        "python": python_block(),
        "code": dict(code or {}),
        "panel": panel,
        "stages": {"until": until},
        "problems": problems,
    }


def refusal(passport: dict) -> str | None:
    """Слова отказа по паспорту: перечень расхождений с заголовком.
    Расхождений нет — None."""
    problems = list((passport or {}).get("problems") or [])
    if not problems:
        return None
    return ("ПАСПОРТ ПРОГОНА НЕ СОШЁЛСЯ (%d): параметры прогона объявлены "
            "иначе, чем на деле — прогон не стартует\n" % len(problems)
            + "\n".join("   ! " + p for p in problems))


def freeze_problems(passport: dict, run_dir) -> list:
    """Вторая сверка — ПОСЛЕ заморозки: то, что конвейер заморозил,
    обязано быть тем, что объявлено паспортом.

    Сверяются отпечатки по имени файла: манифест заморозки правил
    (`freeze/manifest.json`) против `rules_documents` и манифест
    заморозки данных (`data-freeze/manifest.json`) против
    `data_documents`. Манифеста нет — стадия ещё не проходила, и это не
    расхождение: сверять нечего, молчим."""
    out: list = []
    run_dir = Path(run_dir)
    for rel, key, title in (
            (Path("freeze") / "manifest.json", "rules_documents",
             "набор правил авиакомпании"),
            (Path("data-freeze") / "manifest.json", "data_documents",
             "набор данных авиакомпании")):
        man = _read_json(run_dir / rel)
        if not isinstance(man, dict):
            continue
        frozen = {str(f.get("file")): str(f.get("sha256"))
                  for f in (man.get("files") or []) if isinstance(f, dict)}
        for row in (passport or {}).get(key) or []:
            name = str(row.get("name"))
            got = frozen.get(name)
            if got is None:
                out.append("прогон заморозил не то, что объявлено: "
                           + disagreement(f"{title}, файл {name}",
                                          short(row.get("sha256")),
                                          "в заморозке файла нет"))
            elif got != str(row.get("sha256")):
                out.append("прогон заморозил не то, что объявлено: "
                           + disagreement(f"{title}, файл {name}",
                                          short(row.get("sha256")),
                                          short(got)))
        extra = sorted(set(frozen) - {str(r.get("name"))
                                      for r in (passport or {}).get(key) or []})
        for name in extra:
            out.append("прогон заморозил не то, что объявлено: "
                       + disagreement(f"{title}, файл {name}",
                                      "паспортом не объявлен",
                                      f"заморожен ({short(frozen[name])})"))
    return out


def render(passport: dict) -> list:
    """Читаемая выжимка паспорта строками для журнала. Полный паспорт
    печатается рядом JSON-ом — выжимка нужна человеку, который смотрит
    на бегущий журнал."""
    p = passport or {}
    kit = p.get("kit") or {}
    cat = p.get("catalog") or {}
    py = p.get("python") or {}
    code = p.get("code") or {}
    rows = [
        f"ПАСПОРТ ПРОГОНА ({p.get('schema')}), собран {p.get('at')}",
        f"   набор: {kit.get('dir')} — {kit.get('customer')}; "
        f"ступень {kit.get('stage')}"
        + (f", фаза {kit.get('phase')}" if kit.get("phase") else ""),
    ]
    for title, key in (("правила", "rules_documents"),
                       ("данные", "data_documents")):
        files = p.get(key) or []
        rows.append(f"   {title} авиакомпании: "
                    + (", ".join(f"{r['name']} {short(r['sha256'])}"
                                 for r in files) or "нет"))
    rows.append("   " + str(cat.get("words")))
    rows.append("   " + str((p.get("bindings") or {}).get("words")))
    layout = p.get("agents_layout") or {}
    rows.append(f"   раскладка моделей ({layout.get('source')}): "
                f"{layout.get('file')} {short(layout.get('sha256'))} — "
                f"{layout.get('words')}")
    rows.append("   " + str((p.get("solver") or {}).get("words")))
    rows.append("   " + str((p.get("panel") or {}).get("words")))
    rows.append(f"   Python {py.get('version')} ({py.get('implementation')}), "
                f"пакетов {py.get('packages_count')}, отпечаток перечня "
                f"{short(py.get('packages_sha256'))}")
    rows.append(f"   код: коммит {code.get('commit')}, отпечаток "
                f"{code.get('fingerprint')}"
                + (" — дерево грязное" if code.get("tree_dirty") else ""))
    return rows


def write(passport: dict, path) -> Path:
    """Паспорт файлом (UTF-8, отступ 1 — как у прочих машинных отчётов
    раннера). Путь может указывать на каталог: тогда имя — PASSPORT_FILE."""
    path = Path(path)
    if path.is_dir():
        path = path / PASSPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(passport, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def declaration(passport: dict, **named) -> dict:
    """Паспорт как ОБЪЯВЛЕНИЕ записи о прогоне: те же параметры в именах
    словаря записи (`vmp/run_record.py`, схема `cosmos-run-record/v1`).

    Паспорт — первое воплощение головы записи (проект «Запись о
    прогоне», раздел 8): он уже собрал и сверил каталог, привязки,
    раскладку моделей, решателя, документы, данные, библиотеки и код.
    Здесь только выход в словарь записи — перевод имён живёт в ОДНОМ
    месте (`run_record.passport_to_declaration`), второго перечня полей
    в продукте нет.

    Именованные ключи (номер прогона, цель, площадка, кто объявил, план
    этапов, происхождение) — то, чего в паспорте нет и что называет
    вызывающий; их перечень — у `passport_to_declaration`."""
    from vmp import run_record as rr
    return rr.passport_to_declaration(passport, **named)
