"""Ступень 3, шаг 9г — входы сертификатора с показаниями logic (моя половина).

По каждому перевыпущенному эталону (нано / мини / полный,
handoff\\2026-08-24-REISSUE-LOGIC): прогон решателя, вход сертификатора
(назначения + смены той же нарезкой) с ключом `lastRunLogic` ВНУТРИ
JSON решения — формат согласован с производителем 2026-08-24: пара
«решение ↔ показания» атомарна и проходит один файлом и через
веб-эндпоинт сертификации, и через командную строку. Рядом — пороги,
допуски, резерв и шапка прогона, как у приёмки вехи А.

До записи каждый lastRun.logic прогоняется гейтом webapp.logic_gate
против своего пакета: вход сертификатора с корзинами, которые гейт не
подтвердил, не пишется (сертификация неподтверждённых показаний — та же
«зелёная проверка ни на чём»).

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_g_certify_logic
Пишет в handoff\\2026-08-24-REISSUE-LOGIC\\certify\\.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import certify_input as ci                         # noqa: E402
from webapp import logic_gate as lg                            # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

BASE_DIR = Path(r"C:\work\Sandbox\Testing\TimeFold\handoff\2026-08-24-REISSUE-LOGIC")
OUT = BASE_DIR / "certify"
PACKAGES = (
    ("nano", "нано", "nano-task-package.zip", 30),
    ("mini", "мини", "mini-task-package.zip", 30),
    ("full", "полный", "full-task-package.zip", 120),
)


def _solve_full(payload: dict, mode: str, seconds: int,
                objective_id: str | None) -> dict:
    url = "%s/schedules/single?mode=%s&spentLimitSeconds=%d" % (mv.BASE, mode, seconds)
    if objective_id:
        url += "&objective_id=" + urllib.parse.quote(objective_id)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=seconds + 300) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    defects = 0
    for slug, name, fname, budget in PACKAGES:
        path = BASE_DIR / fname
        pkg = tp.load(path, run_gate=False)
        payload = {k: v for k, v in tp.to_payload(pkg).items()
                   if not k.startswith("_")}
        # Целевая — из пакета, когда объявлена; иначе допустимость по мягким.
        ros = tp.objectives(pkg, "rostering")
        objective_id = str(ros[0]["id"]) if ros else None
        mode = "optimization" if objective_id else "feasibility-soft"
        proc = mv.start_jvm(str(path))
        try:
            solved = _solve_full(payload, mode, budget, objective_id)
            rep = mv.applied()
        finally:
            mv.stop_jvm(proc)
        run = rep.get("lastRun") or {}
        counts = {b: len((run.get("logic") or {}).get(b) or []) for b in lg.BASKETS}
        # Сборка — модулем webapp.certify_input (этап Г, Г-S2): инструмент —
        # тонкая обёртка, идентичность вывода держит tests/test_certify_input.
        try:
            from webapp import solution_export as se
            rest = se.min_rest_from_applied(rep)
            inp, coverage = ci.build(
                pkg, payload, solved, rep,
                name="%s: %s, профиль %s, пороги отдыха из прогона: дома %d, "
                     "вне базы %d мин"
                     % (name, pkg["manifest"].get("source_run"),
                        run.get("rulesProfile"), rest["home"], rest["outstation"]))
        except ci.CertifyInputError as e:
            print("%s: счёт %s, гейт logic ДЕФЕКТ, корзины %s" % (
                name, solved.get("score"), counts))
            print(str(e))
            defects += 1
            continue
        print("%s: счёт %s, гейт logic сходится, корзины %s" % (
            name, solved.get("score"), counts))
        head = ci.head(pkg, payload, solved, rep, inp, coverage)
        head["etalon"] = name
        head["rest_thresholds_used_for_duties_min"] = rest
        head["note"] = ("lastRunLogic вшит в certifier-input (формат 9г); "
                        "pairings не выгружаются: прогон по пакету — объединённая "
                        "модель назначения, цепочек как сущности в ней нет")
        for suffix, obj in (("certifier-input", inp),
                            ("thresholds", run.get("thresholds") or {}),
                            ("quals", ci.quals_sheet(payload)),
                            ("reserve", ci.reserve_sheet(payload, solved, rep)),
                            ("run", head)):
            f = OUT / ("%s-%s.json" % (slug, suffix))
            f.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            written.append(f)
    print("записано файлов: %d в %s" % (len(written), OUT))
    print("итог: %s" % ("готово к сертификации" if defects == 0
                        else "ДЕФЕКТОВ %d — входы с дефектами не написаны" % defects))
    return 0 if defects == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
