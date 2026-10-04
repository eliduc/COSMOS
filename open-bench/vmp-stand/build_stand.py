# -*- coding: utf-8 -*-
"""Сборка ступеней стенда в каталог: по подкаталогу на ступень с
членами пакета (manifest.json, model.json, objective.json,
clean-data.json, vmp-form.json) и expectation.json — приговором,
написанным вместе с материалом.

    python open-bench/vmp-stand/build_stand.py --out <каталог вне репозитория>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stand  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="каталог ступеней")
    args = ap.parse_args(argv)
    out = Path(args.out)
    for name, pkg, expectation in stand.build_all():
        stand.write_step(out, name, pkg, expectation)
        print("%s  %s  → %s" % (name, expectation["title"], out / name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
