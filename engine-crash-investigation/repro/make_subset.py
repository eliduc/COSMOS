"""Cut inst4_p2.json down to the first N flights (by departure time).

Usage: python make_subset.py <N_flights> <N_employees|all> <out.json>
"""
import json
import sys

SRC = (r"C:\Users\rlgle\AppData\Local\Temp\claude\C--work-Sandbox-Testing-TimeFold"
       r"\c12666e8-866a-402a-887c-f5220903bed9\scratchpad\kasirzadeh\payloads\inst4_p2.json")


def main():
    n_flights = int(sys.argv[1])
    n_emp_arg = sys.argv[2]
    out_path = sys.argv[3]

    d = json.load(open(SRC))

    flights = sorted(d["flights"], key=lambda f: (f["departureUTCDateTime"], f["flightNumber"]))
    keep_flights = flights[:n_flights]
    keep_numbers = {f["flightNumber"] for f in keep_flights}

    fas = [fa for fa in d["flightAssignments"] if fa["flight"] in keep_numbers]

    employees = d["employees"]
    if n_emp_arg != "all":
        n_emp = int(n_emp_arg)
        # Keep skill diversity: interleave by skill group.
        by_skill = {}
        for e in employees:
            by_skill.setdefault(tuple(sorted(e["skills"])), []).append(e)
        picked = []
        idx = 0
        while len(picked) < n_emp:
            added = False
            for grp in by_skill.values():
                if idx < len(grp) and len(picked) < n_emp:
                    picked.append(grp[idx])
                    added = True
            if not added:
                break
            idx += 1
        employees = picked

    # Trim employee preferredFlights to legs that still exist (keeps payload small).
    emp_out = []
    for e in employees:
        e = dict(e)
        e["preferredFlights"] = [x for x in e.get("preferredFlights", []) if x in keep_numbers]
        emp_out.append(e)

    used_airports = set()
    for f in keep_flights:
        used_airports.add(f["departureAirport"])
        used_airports.add(f["arrivalAirport"])
    for e in emp_out:
        if e.get("homeAirport"):
            used_airports.add(e["homeAirport"])
    airports = [a for a in d["airports"] if a["code"] in used_airports]

    out = {
        "airports": airports,
        "employees": emp_out,
        "flights": keep_flights,
        "flightAssignments": fas,
        "score": None,
    }
    with open(out_path, "w") as fh:
        json.dump(out, fh)
    print(f"wrote {out_path}: flights={len(keep_flights)} assignments={len(fas)} "
          f"employees={len(emp_out)} airports={len(airports)}")


if __name__ == "__main__":
    main()
