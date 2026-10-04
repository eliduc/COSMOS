# -*- coding: utf-8 -*-
"""Вложить словари диалога в установщик и деинсталлятор.

Зачем так. Оба файла приносят на площадку по одному — недостающий
внешний словарь означал бы немой диалог в решающий момент. Поэтому
переводы хранятся здесь по-человечески (отдельные JSON, удобно править и
проверять), а в код попадают вложением: единственный источник правды —
файлы `messages-<язык>.json`, а `MESSAGES = {...}` в скриптах собирается
этой программой и вручную не правится.

Гейт (иначе сборка отказывает):
  * во всех языках ровно те же ключи, что в русском исходнике;
  * число подстановок «%s» совпадает по каждому ключу и языку —
    иначе строка упадёт при подстановке уже у заказчика;
  * после вложения оба скрипта разбираются как программы.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

# Вывод — всегда UTF-8. Иначе при перенаправлении в файл или в трубу
# первая же русская строка валит сборку исключением кодировки: гейт
# отказывает не потому, что словари плохи, а потому что не смог о них
# рассказать. Тот же класс дефекта уже чинили в самих скриптах.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

HERE = Path(__file__).parent
REPO = HERE.parent.parent
LANGS = ("ru", "en", "ar", "uz", "kk")
TARGETS = (REPO / "install_cosmos.py", REPO / "uninstall_cosmos.py")
ANCHOR = "MESSAGES: dict[str, dict[str, str]] = {"


def load(lang: str) -> dict[str, str]:
    path = HERE / ("messages-%s.json" % lang)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def main() -> int:
    ru = load("ru")
    if not ru:
        print("нет исходного словаря messages-ru.json")
        return 2
    langs = {"ru": ru}
    missing_langs = []
    for lang in LANGS[1:]:
        rows = load(lang)
        if not rows:
            missing_langs.append(lang)
            continue
        langs[lang] = rows

    # --- гейт -----------------------------------------------------------
    problems = []
    checked = 0
    for lang, rows in langs.items():
        if lang == "ru":
            continue
        only_ru = set(ru) - set(rows)
        only_lang = set(rows) - set(ru)
        if only_ru or only_lang:
            problems.append("%s: ключей не хватает %d, лишних %d"
                            % (lang, len(only_ru), len(only_lang)))
        for key in set(ru) & set(rows):
            checked += 1
            if ru[key].count("%s") != rows[key].count("%s"):
                problems.append("%s: подстановки не сходятся в ключе %s "
                                "(было %d, стало %d)"
                                % (lang, key, ru[key].count("%s"),
                                   rows[key].count("%s")))
    print("языков: %d (%s)" % (len(langs), ", ".join(sorted(langs))))
    if missing_langs:
        print("перевода пока нет: %s — эти языки покажут русский исходник"
              % ", ".join(missing_langs))
    print("ключей в исходнике: %d; пар «ключ-язык» проверено: %d"
          % (len(ru), checked))
    if problems:
        for p in problems[:40]:
            print("  ! %s" % p)
        print("ОТКАЗ: словари не сходятся (%d расхождений)" % len(problems))
        return 1
    if checked == 0 and len(langs) > 1:
        print("ОТКАЗ: проверять было нечего — это не проверка")
        return 1

    # --- сборка словаря в текст программы -------------------------------
    lines = ["MESSAGES: dict[str, dict[str, str]] = {"]
    for key in sorted(ru):
        parts = []
        for lang in LANGS:
            if lang in langs and key in langs[lang]:
                parts.append("%s: %s" % (json.dumps(lang, ensure_ascii=False),
                                         json.dumps(langs[lang][key],
                                                    ensure_ascii=False)))
        lines.append("    %s: {%s}," % (json.dumps(key, ensure_ascii=False),
                                        ", ".join(parts)))
    lines.append("}")
    block = "\n".join(lines)

    # --- вложение --------------------------------------------------------
    for path in TARGETS:
        src = path.read_text(encoding="utf-8")
        start = src.find(ANCHOR)
        if start < 0:
            print("ОТКАЗ: в %s нет места для словаря" % path.name)
            return 1
        # Место вставки бывает двух видов: пустой словарь в одну строку
        # (первая сборка) и уже вложенный многострочный блок (повторная).
        if src.startswith(ANCHOR + "}", start):
            end = start + len(ANCHOR) + 1
        else:
            m = re.compile(r"^\}\s*$", re.M).search(src, start)
            if not m:
                print("ОТКАЗ: в %s не найден конец словаря" % path.name)
                return 1
            end = m.end()
        new = src[:start] + block + src[end:]
        ast.parse(new)                       # гейт: файл остался программой
        path.write_text(new, encoding="utf-8")
        print("вложено в %s: %d ключей" % (path.name, len(ru)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
