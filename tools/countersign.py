"""Выписать подтверждение продвижения (контрподпись человека).

Продвижение правки на внутренний и боевой контуры требует подтверждения,
которое автоматика не может выписать себе сама: подпись считается по
секрету, известному только человеку, и привязана к тройке «правка, срез,
фаза». Подтверждение для одного среза не подходит другому, поэтому
«однажды разрешили» не превращается в постоянный доступ.

Секрет задаётся переменной окружения COSMOS_PROMOTION_SECRET и в
репозитории не хранится.

Использование:
    set COSMOS_PROMOTION_SECRET=...            (в вашей сессии)
    python tools/countersign.py --id 42 --sha <срез> --phase stage

Полученную строку передать в вызов фазы параметром `countersign`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console    # noqa: E402

from webapp.evolution_runner import (          # noqa: E402
    COUNTERSIGN_ENV, CountersignatureRequired, countersign_expected)

force_utf8_console()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=int, required=True,
                    help="номер правки")
    ap.add_argument("--sha", required=True,
                    help="срез, который подтверждается (полный или тот, "
                         "что записан у правки)")
    ap.add_argument("--phase", required=True, choices=("stage", "prod"),
                    help="фаза продвижения")
    a = ap.parse_args(argv)
    if not os.environ.get(COUNTERSIGN_ENV):
        print(f"ОТКАЗ: переменная {COUNTERSIGN_ENV} не задана. "
              f"Секрет держит человек; без него подтверждение не "
              f"выписывается.", file=sys.stderr)
        return 2
    try:
        print(countersign_expected(a.id, a.sha, a.phase))
    except CountersignatureRequired as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
