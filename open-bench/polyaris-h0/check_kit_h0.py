# -*- coding: utf-8 -*-
"""Побуквенная сверка набора «ИД H0-ВМП» (docx + xlsx + jpg) с ФОРМОЙ
эталона (etalon/VMP-H0-form.json): каждая клауза файлов — заголовок
раздела формы либо запись формы одной строкой (render_row_line), все
записи формы присутствуют ровно один раз. Тексты docx/xlsx — штатной
заморозкой cdpm.freeze; jpg — строки, отрисованные генератором
(etalon/jpg-lines.json; зрительная транскрипция судится прогоном).
Код возврата 1 при любом расхождении."""
import difflib
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "open-bench" / "polyaris-h0"
sys.path.insert(0, str(REPO))
from cdpm import freeze as fz  # noqa: E402
from cdpm import vmp_form as vf  # noqa: E402
sys.path.insert(0, str(KIT))
import build_kit_h0 as gen  # noqa: E402

form = json.loads((KIT / "etalon" / "VMP-H0-form.json").read_text(encoding="utf-8"))
want = gen.lines_by_file(form)
got = {
    "docx": [c["text"] for c in fz._extract_docx(KIT / "docs" / "ID-H0-VMP-1.docx")],
    "xlsx": [c["text"] for c in fz._extract_xlsx(KIT / "docs" / "ID-H0-VMP-2.xlsx")],
    "jpg": json.loads((KIT / "etalon" / "jpg-lines.json").read_text(encoding="utf-8")),
}
ok = True
total_rows = 0           # записи формы, НАЙДЕННЫЕ в файлах (по got)
for fkey in ("docx", "xlsx", "jpg"):
    exp = [t for _k, _r, t in want[fkey]]
    row_texts = [t for k, _r, t in want[fkey] if k == "row"]
    # Счёт — по прочитанным файлам, не по ожиданию (аудит: тавтология):
    # каждая запись формы обязана встретиться в файле ровно один раз.
    found = sum(1 for t in row_texts if got[fkey].count(t) == 1)
    total_rows += found
    same = got[fkey] == exp
    ok &= same
    print(f"{fkey}: клауз {len(got[fkey])} (ожидалось {len(exp)}); записей "
          f"формы в файле {found} из {len(row_texts)} — "
          f"{'СОВПАДАЮТ БУКВА В БУКВУ' if same else 'РАЗЛИЧИЯ'}")
    if not same:
        for d in difflib.ndiff(exp, got[fkey]):
            if d[:1] in "+-":
                print("  ", d[:220])
n_form = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
print(f"записей формы: {n_form}; найдено в файлах: {total_rows}; символов: "
      f"{sum(len(t) for f in got.values() for t in f)}")
ok &= (n_form == total_rows)
print("ИТОГ:", "ФАЙЛЫ = ФОРМА ВМП H0 ОДИН В ОДИН" if ok else "РАСХОЖДЕНИЯ")
sys.exit(0 if ok else 1)
