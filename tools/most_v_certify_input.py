"""Вход независимого сертификатора — файлами, по эталонам (этап В, «в»).

Сертификация обязана проверять ТОТ ЖЕ выход решателя, который был
посчитан, поэтому вход фиксируется файлами рядом с отчётом, а не
пересчитывается сертификатором заново. На каждый эталон — три файла:

  <имя>-certifier-input.json — назначения и смены ОТ РЕШАТЕЛЯ
      (контракт докстроки vmp/run/roster_certify.py); границы смен по
      порогу отдыха, которым решатель считал, а не по догадке;
  <имя>-thresholds.json      — lastRun.thresholds того же прогона:
      applied[] (что применено, с литералом и заявлениями), ignored[]
      (что НЕ применено и почему — машинный перечень «формализовано,
      решателем не исполнялось»), paramToThreshold;
  <имя>-run.json             — шапка для трассировки: отпечаток пакета,
      source_run, профиль, режим, целевая, показания провайдера.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_v_certify_input
Пишет в handoff\\2026-08-22-MOST-A\\certify\\.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import quals_export as qe                          # noqa: E402
from webapp import reserve_oracle as ro                        # noqa: E402
from webapp import solution_export as se                       # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

OUT = mv.HANDOFF / "certify"


def _solve_full(payload: dict, mode: str, objective_id: str | None) -> dict:
    """Прогон с возвратом решения (mv.solve отдаёт только код)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    url = "%s/schedules/single?mode=%s&spentLimitSeconds=%d" % (
        mv.BASE, mode, 30)
    if objective_id:
        url += "&objective_id=" + urllib.parse.quote(objective_id)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for name, path in mv.ETALONS:
        pkg = tp.load(path, run_gate=False)
        payload = {k: v for k, v in tp.to_payload(pkg).items()
                   if not k.startswith("_")}
        ros = tp.objectives(pkg, "rostering")
        objective_id = str(ros[0]["id"]) if ros else None
        proc = mv.start_jvm(str(path))
        try:
            solved = _solve_full(payload, "optimization", objective_id)
            rep = mv.applied()
        finally:
            mv.stop_jvm(proc)
        run = rep.get("lastRun") or {}
        rest = se.min_rest_from_applied(rep)
        coverage: dict = {}
        inp = se.certifier_input(
            payload, solved, rest,
            name="%s: %s, профиль %s, пороги отдыха из прогона: дома %d, "
                 "вне базы %d мин"
                 % (name, pkg["manifest"].get("source_run"),
                    run.get("rulesProfile"), rest["home"], rest["outstation"]),
            coverage=coverage)
        # Лист «Резерв» — оракул по набору и решению (строка 8 свода):
        # (база, сутки) → свободно по ролям, экипажей, норма, нехватка.
        norms = ro.norms_from_applied(rep)
        reserve_rows = ro.reserve_table(payload, solved, norms) if norms else []
        head = {
            "etalon": name,
            "package_fingerprint": (rep.get("taskPackage") or {}).get("fingerprint"),
            "source_run": pkg["manifest"].get("source_run"),
            "spec_version": pkg["manifest"].get("spec_version"),
            "rules_profile": run.get("rulesProfile"),
            "mode": run.get("mode"),
            "objective": run.get("objective"),
            "profile_readings": run.get("profileReadings"),
            "score": solved.get("score"),
            # Укомплектованность — иначе сертификат зелёный на ростере,
            # который не летает: выгрузка пропускает незанятые места.
            "seats": len(solved.get("flightAssignments") or []),
            "seats_unassigned": ro.unassigned_seats(solved),
            "overlapping_pairs": ro.overlapping_pairs(payload, solved),
            "reserve_norms": norms,
            "reserve_shortfall_total": ro.total_shortfall(reserve_rows),
            "coverage": run.get("coverage"),
            "assignments": len(inp["assignments"]),
            "duties": len(inp["duties"]),
            "rest_thresholds_used_for_duties_min": rest,
            # Покрытие ручек отдыха: скольким разрывам досталась домашняя
            # и внебазовая, у кого базы нет. Ручка, не судившая ни одного
            # разрыва, — «применена» к пустому множеству, и это видно.
            "rest_places": coverage,
            "note": ("pairings не выгружаются: прогон по пакету — объединённая "
                     "модель назначения, цепочек как сущности в ней нет"),
        }
        slug = {"нано": "nano", "мини": "mini"}[name]
        quals_rows = qe.qualification_rows(payload)
        for suffix, obj in (("certifier-input", inp),
                            ("thresholds", run.get("thresholds") or {}),
                            ("quals", {"rows": quals_rows,
                                       "reading": qe.READING}),
                            ("reserve", {"norms": norms, "rows": reserve_rows,
                                         "shortfall_total": ro.total_shortfall(reserve_rows),
                                         "reading": ("экипаж в резерве — по одному свободному "
                                                     "сотруднику базы на каждую роль состава; "
                                                     "свободен = ни одно назначение не касается "
                                                     "суток и сутки не в unavailableDays")}),
                            ("run", head)):
            f = OUT / ("%s-%s.json" % (slug, suffix))
            f.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            written.append(f)
        print("%s: счёт %s, назначений %d, смен %d, пустых мест %d, пороги дома %d / вне базы %d, "
              "разрывов дома %d / вне базы %d, без базы %d, нехватка резерва %d, отпечаток %s"
              % (name, head["score"], head["assignments"], head["duties"], head["seats_unassigned"],
                 rest["home"], rest["outstation"],
                 coverage["home"], coverage["outstation"],
                 len(coverage["no_base"]), head["reserve_shortfall_total"],
                 str(head["package_fingerprint"])[:12]))
    print("записано файлов: %d в %s" % (len(written), OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
