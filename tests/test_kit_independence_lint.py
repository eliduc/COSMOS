# -*- coding: utf-8 -*-
"""Линт независимости кода от наборов: комментарии и докстринги с
уроками прогонов законны, маркер набора в исполняемом коде — находка."""
from tools import kit_independence_lint as lint


def test_lint_ignores_comments_and_docstrings_but_catches_code(tmp_path):
    repo = tmp_path
    (repo / "cdpm").mkdir()
    (repo / "cdpm" / "a.py").write_text(
        '"""Модуль. Урок прогона №5 H0: критерий O-PAIR терялся."""\n'
        "import re\n"
        "\n"
        "def f(customer):\n"
        "    # проход №6 H0: VN-050 → C (комментарий законен)\n"
        "    x = re.compile(r'^ВМП — ')\n"
        "    if customer == 'Полярис-H0':\n"           # находка: ветвь под набор
        "        return 'PLY'\n"                        # находка: код базы стенда
        "    return x\n",
        encoding="utf-8")
    (repo / "vmp").mkdir()
    (repo / "vmp" / "b.py").write_text(
        "ICON = '<svg xmlns=\"http://www.w3.org/2000/svg\"/>'\n"   # SVG как формат — законно
        "def g():\n"
        "    '''Докстринга про VN-011 не судится.'''\n"
        "    return 1\n",
        encoding="utf-8")
    (repo / "kotlin-solver" / "src" / "main").mkdir(parents=True)
    (repo / "kotlin-solver" / "src" / "main" / "X.kt").write_text(
        "// H0 в комментарии законно\n"
        "/* и в блоке VN-003 тоже */\n"
        "val base = \"GRT\"\n",                       # находка
        encoding="utf-8")
    found = lint.scan(repo)
    keys = sorted((rel, n, name) for rel, n, name, _ctx in found)
    assert keys == [("cdpm/a.py", 7, "набор H0"), ("cdpm/a.py", 8, "коды баз стенда"),
                    ("kotlin-solver/src/main/X.kt", 3, "коды баз стенда")]
