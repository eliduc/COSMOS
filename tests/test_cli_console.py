"""Страж настройки консоли для командных инструментов.

Урок аудита 2026-07-26 (P1-15): четыре новых инструмента печатали русский
отчёт и падали `UnicodeEncodeError` в обычной консоли Windows (cp1252).
Дефект не поймался, потому что прогоны шли с внешним `PYTHONIOENCODING`,
а падение случалось ПОСЛЕ записи артефакта — то есть работа удавалась, а
код возврата сообщал неудачу.

Здесь два теста. Первый — статический страж: любой корневой инструмент,
который печатает и содержит кириллицу, обязан настроить консоль сам.
Второй — поведенческий: инструмент, запущенный в окружении БЕЗ
`PYTHONUTF8`/`PYTHONIOENCODING` и с консольной кодировкой cp1251, всё
равно печатает отчёт и возвращает ожидаемый код.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _root_cli_modules() -> list[Path]:
    """Модули с точкой входа, которые что-то печатают.

    Каталог `tools/` добавлен пятым аудитом: страж стерёг только корень,
    а инструменты сопровождения переехали в `tools/` — и первый же новый
    сборщик там повторил ровно тот дефект, ради которого страж написан.
    Периметр стража обязан совпадать с периметром инструментов."""
    out = []
    for p in sorted(REPO.glob("_*.py")) + sorted(REPO.glob("tools/*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if "if __name__" in src and re.search(r"\bprint\(", src):
            out.append(p)
    return out


def _has_cyrillic_literal(src: str) -> bool:
    """Кириллица в СТРОКОВОМ ЛИТЕРАЛЕ, а не в комментарии. Комментарий
    напечатать нельзя, а литерал — можно: через print, через `help=`
    argparse и через `description=__doc__` (тогда падает `--help`)."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return bool(re.search(r"[А-Яа-яЁё]", src))
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
               and re.search(r"[А-Яа-яЁё]", n.value)
               for n in ast.walk(tree))


def test_every_printing_cli_configures_its_console():
    """Инструмент, способный вывести кириллицу, обязан настроить консоль
    сам: зависимость от внешней переменной окружения — скрытое требование
    к среде, а не свойство программы."""
    guilty = []
    for p in _root_cli_modules():
        src = p.read_text(encoding="utf-8", errors="replace")
        if not _has_cyrillic_literal(src):
            continue
        if "force_utf8_console" in src or "reconfigure(encoding" in src \
                or "TextIOWrapper" in src:
            continue
        guilty.append(p.name)
    assert not guilty, (
        "эти инструменты могут вывести кириллицу, но не настраивают "
        "консоль — в обычной консоли Windows они упадут "
        "UnicodeEncodeError: " + ", ".join(guilty))


def test_the_server_entry_configures_its_console_too():
    """Страж стерёг корень и `tools/`, а `webapp/` не стерёг никто — и
    фоновый поток начальной настройки падал `UnicodeEncodeError` ровно на
    печати успешного отчёта о переносе конфигурации. Периметр стража
    обязан совпадать с периметром кода, печатающего по-русски.

    Модули пакета `webapp` печатают через тот же процесс, что и точка
    входа, поэтому достаточно, чтобы консоль настраивала она."""
    app = REPO / "webapp" / "app.py"
    src = app.read_text(encoding="utf-8", errors="replace")
    assert "force_utf8_console" in src, (
        "webapp/app.py печатает русские сообщения (перенос конфигурации, "
        "режим входа), но не настраивает консоль")
    printing = []
    for mod in sorted((REPO / "webapp").glob("*.py")):
        text = mod.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bprint\(", text) and _has_cyrillic_literal(text):
            printing.append(mod.name)
    assert "app.py" in printing, (
        "проверка обессмыслилась: app.py больше не печатает кириллицу — "
        "перенесите стража туда, где теперь печатают")


def _clean_env() -> dict:
    """Окружение БЕЗ внешних настроек кодировки — как у обычного
    пользователя Windows."""
    env = dict(os.environ)
    for var in ("PYTHONUTF8", "PYTHONIOENCODING"):
        env.pop(var, None)
    return env


def test_tools_print_their_report_in_a_cp1251_console(tmp_path):
    """Поведенческая проверка: вывод в однобайтовую кодировку не роняет
    инструмент и не подменяет код возврата."""
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps({"name": "тест", "rules": [{
        "title": "GENDER_ACCOMMODATION",
        "constraint": {"kind": "note", "note": json.dumps(
            {"vendor_constraint": "GENDER_ACCOMMODATION",
             "weight_level": "STD"})}}]}), encoding="utf-8")
    out = tmp_path / "coverage.md"
    env = _clean_env()
    # эмулируем однобайтовую консоль: без настройки внутри программы
    # печать русского текста в неё падает
    env["PYTHONLEGACYWINDOWSSTDIO"] = "1"
    proc = subprocess.run(
        [sys.executable, str(REPO / "_rules_coverage.py"),
         "--pack", str(pack), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO, env=env,
        encoding="utf-8", errors="replace")
    assert "UnicodeEncodeError" not in (proc.stderr or "")
    # ненулевой код именно из-за непокрытого правила, а не из-за падения
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "НЕ покрыто 1" in out.read_text(encoding="utf-8")


def test_console_helper_is_idempotent_and_safe_on_odd_streams():
    sys.path.insert(0, str(REPO))
    from _cli_console import force_utf8_console

    class _NoReconfigure:
        def write(self, _s):
            return 0

    force_utf8_console()
    force_utf8_console()          # повторный вызов безвреден
    saved = sys.stdout
    try:
        sys.stdout = _NoReconfigure()
        force_utf8_console()      # поток без reconfigure — не падаем
    finally:
        sys.stdout = saved


@pytest.mark.parametrize("tool", ["k02_grammar_price.py", "k02_window_lattice.py"])
def test_k02_cli_help_configures_an_initially_cp1252_console(tool):
    bootstrap = (
        "import runpy, sys; "
        "sys.stdout.reconfigure(encoding='cp1252', errors='strict'); "
        "sys.stderr.reconfigure(encoding='cp1252', errors='strict'); "
        "sys.argv = [sys.argv[1], '--help']; "
        "runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", bootstrap, str(REPO / "tools" / tool)],
        cwd=REPO, env=_clean_env(), capture_output=True, timeout=30)
    stdout = proc.stdout.decode("utf-8", errors="strict")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stderr
    assert re.search(r"[А-Яа-яЁё]", stdout), stdout
    assert "UnicodeEncodeError" not in stderr


def test_say_survives_a_narrow_stream_without_reconfigure():
    """ДЕФЕКТ (прогон CI 2026-08-16): фоновый поток веб-приложения падал
    `UnicodeEncodeError` на печати УСПЕШНОГО отчёта о переносе
    конфигурации. `force_utf8_console` в момент импорта настроил ТОТ
    поток, который стоял на месте stdout тогда; под pytest это
    перехватчик без `reconfigure` — настройка молча пропускается, а
    печать случается позже и в узкую кодировку.

    Поток не владеет выбором stdout, поэтому печать обязана быть
    неломающейся сама. Проверяется ровно это: узкий поток без
    `reconfigure`, русский текст — исключения нет, вывод состоялся."""
    sys.path.insert(0, str(REPO))
    from _cli_console import say

    class _Cp1252Stream:
        """Консоль однобайтовой кодировки: как настоящая, роняет запись
        непредставимого символа и не умеет перенастраиваться."""

        encoding = "cp1252"

        def __init__(self):
            self.written = []

        def write(self, s):
            s.encode("cp1252")            # бросит UnicodeEncodeError
            self.written.append(s)
            return len(s)

        def flush(self):
            pass

    saved = sys.stdout
    stream = _Cp1252Stream()
    try:
        sys.stdout = stream
        say("конфигурация выдана наборам 3 из 5")     # не имеет права упасть
    finally:
        sys.stdout = saved
    text = "".join(stream.written)
    assert text.strip(), "вывод не состоялся вовсе — это не запасной путь"
    assert "3" in text and "5" in text, (
        "числа отчёта обязаны дойти даже при замене букв: %r" % text)
