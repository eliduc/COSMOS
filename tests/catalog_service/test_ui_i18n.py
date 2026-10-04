# -*- coding: utf-8 -*-
"""T13 — словарь подписей интерфейса каталога.

Правило экрана: все слова живут в `catalog-ui/src/i18n/ru.ts`, а в
файлах экранов (`src/**/*.tsx`) русских строк нет вовсе. Тогда пять
языков (по отдельному слову владельца) заводятся добавлением словаря,
а не вычёсыванием подписей из разметки.

Здесь же проверяется обратное направление: каждый ключ, которым зовут
`t()` и `tf()`, в словаре есть; мёртвых ключей в словаре нет; строка с
подстановками `{имя}` не показывается через `t()` — иначе пользователь
увидел бы фигурные скобки.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI_SRC = ROOT / "catalog-ui" / "src"
RU = UI_SRC / "i18n" / "ru.ts"

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
#: Ключ словаря в вызове t('…') / tf('…', …); точка перед t отсекает
#: чужие вызовы вроде obj.t( и format(.
CALL = re.compile(r"(?<![A-Za-z0-9_$.])(t|tf)\(\s*'([^']+)'")
KEY_LINE = re.compile(r"^\s*'([a-z][A-Za-z]*\.[A-Za-z]+)':", re.MULTILINE)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def screens() -> list[Path]:
    """Файлы экранов: всё, кроме словаря подписей."""
    return sorted(p for p in UI_SRC.rglob("*.tsx")
                  if "i18n" not in p.relative_to(UI_SRC).parts)


def sources() -> list[Path]:
    """Все исходники интерфейса, кроме самого словаря."""
    return sorted(p for p in UI_SRC.rglob("*.ts*") if p != RU)


def callers() -> list[Path]:
    """Исходники, зовущие подписи: слой i18n не судится по своим же
    примерам в докстроках."""
    return sorted(p for p in UI_SRC.rglob("*.ts*")
                  if "i18n" not in p.relative_to(UI_SRC).parts)


def dictionary_keys() -> list[str]:
    return KEY_LINE.findall(RU.read_text(encoding="utf-8"))


def test_no_russian_words_in_screens() -> None:
    """Ни одной кириллической строки в *.tsx вне словаря подписей."""
    files = screens()
    assert files, "файлов экранов не найдено — проверьте раскладку catalog-ui"
    guilty: list[str] = []
    for path in files:
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if CYRILLIC.search(line):
                rel = path.relative_to(ROOT).as_posix()
                guilty.append(f"{rel}:{number}: {line.strip()}")
    assert not guilty, (
        "русские слова в файлах экранов — их место в "
        "catalog-ui/src/i18n/ru.ts:\n" + "\n".join(guilty[:20]))
    print(f"T13: просмотрено файлов экранов — {len(files)}, "
          "кириллицы вне словаря нет")


def test_every_called_key_is_in_dictionary() -> None:
    """Каждый ключ вызова t()/tf() есть в словаре."""
    known = set(dictionary_keys())
    assert len(known) > 100, (
        f"словарь подписей подозрительно мал: ключей {len(known)}")
    called: dict[str, str] = {}
    for path in callers():
        rel = path.relative_to(ROOT).as_posix()
        for _, key in CALL.findall(path.read_text(encoding="utf-8")):
            called.setdefault(key, rel)
    assert called, "вызовов t()/tf() не найдено — словарь не подключён"
    missing = sorted(f"{key} ({where})"
                     for key, where in called.items() if key not in known)
    assert not missing, (
        "ключей нет в catalog-ui/src/i18n/ru.ts:\n" + "\n".join(missing))
    print(f"T13: ключей в словаре {len(known)}, "
          f"вызовов с разными ключами {len(called)}")


def test_dictionary_has_no_dead_keys() -> None:
    """Мёртвых ключей в словаре нет: словарь — не свалка."""
    text = "\n".join(path.read_text(encoding="utf-8")
                     for path in sources())
    dead = [key for key in dictionary_keys() if f"'{key}'" not in text]
    assert not dead, (
        "ключи словаря не зовёт ни один экран — уберите их из ru.ts:\n"
        + "\n".join(dead))
    print(f"T13: мёртвых ключей нет, всего ключей {len(dictionary_keys())}")


def test_substitutions_go_through_tf() -> None:
    """Строка с подстановками {имя} показывается только через tf()."""
    body = RU.read_text(encoding="utf-8")
    with_params = {key for key in dictionary_keys()
                   if PLACEHOLDER.search(_message_of(body, key))}
    assert with_params, "строк с подстановками в словаре нет — проверьте разбор"
    plain_calls: list[str] = []
    for path in callers():
        text = path.read_text(encoding="utf-8")
        for kind, key in CALL.findall(text):
            if kind == "t" and key in with_params:
                plain_calls.append(f"{path.relative_to(ROOT).as_posix()}: {key}")
    assert not plain_calls, (
        "подпись с подстановками показана через t() — пользователь увидит "
        "фигурные скобки:\n" + "\n".join(plain_calls))
    print(f"T13: подписей с подстановками {len(with_params)}, "
          "все показываются через tf()")


def _message_of(body: str, key: str) -> str:
    """Значение ключа словаря: подпись собирается из склейки строк
    (`'…' + '…'`), как она записана в ru.ts."""
    found = re.search(
        r"^\s*'" + re.escape(key) + r"':\s*(.*?)"
        r"(?=^\s*'[a-z][A-Za-z]*\.[A-Za-z]+':|^\} as const)",
        body, re.S | re.M)
    if not found:
        return ""
    return "".join(re.findall(r"'([^']*)'", found.group(1)))
