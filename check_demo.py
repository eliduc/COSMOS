import json, pathlib
d = json.loads(pathlib.Path(__file__).with_name("demo_data_sample.json").read_text(encoding="utf-8"))
print("airports:", len(d.get("airports", [])))
print("employees:", len(d.get("employees", [])))
print("flights:", len(d.get("flights", [])))
print("flightAssignments:", len(d.get("flightAssignments", [])))
if d.get("employees"):
    print("sample employee:", d["employees"][0])
if d.get("flights"):
    print("sample flight:", d["flights"][0])
