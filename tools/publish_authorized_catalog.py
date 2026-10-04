# -*- coding: utf-8 -*-
"""Публикация ослабленного каталога (.pending) по слову владельца.

Построитель честно удерживает каталог с вердиктом RELAXED_COVER в
`.pending` (внешний аудит R6, P0-05), а этап 2 такой каталог не
принимает. Единственный законный путь публикации — ЛИЧНОЕ слово
владельца об утверждении пакета авторизации непокрытых. Этот
инструмент превращает слово в машинное свидетельство: пишет в
каталог блок authorization с отпечатком SHA-256 утверждённого пакета,
кем/когда утверждено и объёмом согласия; этап 2 принимает
RELAXED_COVER только с полным и сверяемым свидетельством
(_gerad_stage2_roster._assert_relaxed_authorization).

Переименование файла без свидетельства гейт НЕ обходит: вердикт и
свидетельство живут внутри каталога.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pending", type=Path, required=True,
                    help="каталог .pending.json с вердиктом "
                         "RELAXED_COVER")
    ap.add_argument("--package", type=Path, required=True,
                    help="утверждённый пакет авторизации непокрытых "
                         "(md); его SHA-256 входит в свидетельство")
    ap.add_argument("--approved-by", required=True,
                    help="кто и как утвердил (например: «владелец, "
                         "лично в сессии CDPM»)")
    ap.add_argument("--approved-at", required=True,
                    help="когда утверждено (дата/время слова)")
    ap.add_argument("--out", type=Path, default=None,
                    help="канонический путь публикации; по умолчанию "
                         "имя .pending без суффикса")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать существующий канонический файл")
    a = ap.parse_args(argv)

    doc = json.loads(a.pending.read_text(encoding="utf-8"))
    cov = (doc.get("coverage") or {})
    verdict = cov.get("verdict")
    if verdict != "RELAXED_COVER":
        print("ОТКАЗ: вердикт каталога %r — этот инструмент публикует "
              "только RELAXED_COVER (для INCOMPLETE_SEARCH есть штатный "
              "ключ построителя, OK публикуется сам)." % verdict)
        return 2
    if not a.package.is_file():
        print("ОТКАЗ: пакет утверждения не найден: %s" % a.package)
        return 2
    out = a.out
    if out is None:
        name = a.pending.name
        if not name.endswith(".pending.json"):
            print("ОТКАЗ: вход не .pending.json, а --out не задан")
            return 2
        out = a.pending.with_name(
            name[: -len(".pending.json")] + ".json")
    if out.exists() and not a.force:
        print("ОТКАЗ: %s уже существует — публикация не перезаписывает "
              "молча (--force после осознанного решения)." % out)
        return 2

    pkg_sha = hashlib.sha256(a.package.read_bytes()).hexdigest()
    doc["authorization"] = {
        "package_path": str(a.package),
        "package_sha256": pkg_sha,
        "approved_by": a.approved_by,
        "approved_at": a.approved_at,
        "scope_uncovered": int(cov.get("uncovered") or 0),
        "source_pending": a.pending.name,
    }
    out.write_text(json.dumps(doc), encoding="utf-8")
    print("каталог опубликован: %s (вердикт RELAXED_COVER, "
          "непокрытых %s; свидетельство: пакет %s @ %s, утверждено: "
          "%s, %s)" % (out, cov.get("uncovered"), a.package.name,
                       pkg_sha[:12], a.approved_by, a.approved_at))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
