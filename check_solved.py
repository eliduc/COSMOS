import json
import pathlib
from collections import Counter

p = pathlib.Path(__file__).with_name("solved_schedule.json")
d = json.loads(p.read_text(encoding="utf-8"))

print(f"Score: {d.get('score')}")
print(f"Solver status: {d.get('solverStatus')}")

assignments = d.get("flightAssignments", [])
print(f"Total assignments: {len(assignments)}")

assigned = [a for a in assignments if a.get("employee") is not None]
print(f"Filled: {len(assigned)} / {len(assignments)}")

by_employee = Counter(a.get("employee") for a in assigned)
print(f"Employees used: {len(by_employee)} / {len(d.get('employees', []))}")

print("\nAssignment count per employee (top 10):")
for emp_id, n in by_employee.most_common(10):
    print(f"  emp {emp_id}: {n} flights")

flights_by_num = {f["flightNumber"]: f for f in d.get("flights", [])}
employees_by_id = {e["id"]: e for e in d.get("employees", [])}

print("\nSample flight assignments:")
for a in assignments[:8]:
    fn = a.get("flight")
    idx = a.get("indexInFlight")
    skill = a.get("requiredSkill")
    emp_id = a.get("employee")
    emp = employees_by_id.get(emp_id) if emp_id else None
    flight = flights_by_num.get(fn, {})
    route = f"{flight.get('departureAirport')}-{flight.get('arrivalAirport')}"
    dep = flight.get("departureUTCDateTime", "").split("T")[0]
    emp_name = emp["name"] if emp else "—"
    emp_home = emp["homeAirport"] if emp else "—"
    print(f"  {fn} ({route} {dep}) seat#{idx} {skill:18s} -> {emp_name} (home {emp_home})")
