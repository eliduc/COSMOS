"""Native chain labels reach the independent certifier; no solver or model calls."""
from copy import deepcopy
import unittest

from webapp import solution_export as se
from vmp.run import roster_certify as rc


def fixture(two_days=False):
    day = "2026-09-08" if two_days else "2026-09-07"
    flights = [
        {"flightNumber": "F1", "departureUTCDateTime": "2026-09-07T08:00:00",
         "arrivalUTCDateTime": "2026-09-07T09:00:00",
         "departureAirport": "AAA", "arrivalAirport": "BBB"},
        {"flightNumber": "F2", "departureUTCDateTime": day + "T14:00:00",
         "arrivalUTCDateTime": day + "T15:00:00",
         "departureAirport": "BBB", "arrivalAirport": "AAA"}]
    seats = [{"id": f"{fid}-{slot}", "crewSlotId": f"{fid}-{slot}",
              "flight": fid, "indexInFlight": slot, "employee": None, "deadhead": False}
             for fid in ("F1", "F2") for slot in (0, 1)]
    payload = {"flights": flights, "employees": [
        {"id": "E1", "homeAirport": "AAA"}, {"id": "E2", "homeAirport": "AAA"}],
        "flightAssignments": deepcopy(seats)}
    solved = {"flightAssignments": [
        {**a, "employee": f"E{a['indexInFlight'] + 1}",
         "chainId": f"каталог:7:slot:{a['indexInFlight']}"} for a in seats]}
    return payload, solved


def certify(solution):
    rule = {"id": "R-DAYS", "title": "duration", "statement": "at most one calendar day",
            "kind": "HARD", "zone": "PAIRING",
            "sources": [{"sid": "S1", "anchor": "1"}],
            "template_fit": {"category": "A", "family": "duty_limits",
                             "params": {"max_pairing_days": {"value": 1}}}}
    svod = {"meta": {"customer": "test", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "rule.md", "sha256": "0" * 64}],
            "rules": [rule], "questions": [], "objectives": []}

    def no_llm(_):
        raise AssertionError("fitted rule must execute without a model call")

    return rc.certify(svod, solution, no_llm)


class ChainExportTest(unittest.TestCase):
    def test_slot_layers_preserve_identity_and_certify_one_day(self):
        payload, solved = fixture()
        inp = se.certifier_input(payload, solved, 600)
        self.assertEqual([p["id"] for p in inp["pairings"]],
                         ["каталог:7:slot:0", "каталог:7:slot:1"])
        self.assertEqual([p["employee"] for p in inp["pairings"]], ["E1", "E2"])
        self.assertTrue(all(p["start"] == "2026-09-07T08:00:00" and
                            p["end"] == "2026-09-07T15:00:00" for p in inp["pairings"]))
        self.assertTrue(all(a["pairing_id"].startswith("каталог:7:slot:") for a in inp["assignments"]))
        report = certify(inp)
        check = next(c for c in report["checks"] if c.get("basis", {}).get("rule") == "R-DAYS")
        self.assertEqual(check["status"], "прогнана")
        self.assertEqual(check["rows_checked"], 2)
        self.assertTrue(report["certificate"]["certified"])

    def test_two_calendar_days_are_a_real_violation(self):
        payload, solved = fixture(two_days=True)
        report = certify(se.certifier_input(payload, solved, 600))
        self.assertFalse(report["certificate"]["certified"])
        self.assertIn("R-DAYS", report["certificate"]["violated_rules"])
        self.assertEqual(report["certificate"]["rules_uncovered"], [])

    def test_no_labels_leave_rule_uncovered_instead_of_inventing_chains(self):
        payload, solved = fixture()
        for a in solved["flightAssignments"]:
            a.pop("chainId")
        inp = se.certifier_input(payload, solved, 600)
        self.assertNotIn("pairings", inp)
        report = certify(inp)
        self.assertFalse(report["certificate"]["certified"])
        self.assertIn("R-DAYS", report["certificate"]["rules_uncovered"])

    def test_partial_labels_missing_seats_and_mixed_employees_refuse(self):
        for defect in ("missing_label", "missing_seat", "mixed_employee", "unassigned"):
            with self.subTest(defect=defect):
                payload, solved = fixture()
                if defect == "missing_label":
                    solved["flightAssignments"][0].pop("chainId")
                elif defect == "missing_seat":
                    solved["flightAssignments"].pop()
                elif defect == "mixed_employee":
                    solved["flightAssignments"][0]["employee"] = "E2"
                else:
                    solved["flightAssignments"][0]["employee"] = None
                with self.assertRaises(se.SolutionExportError):
                    se.certifier_input(payload, solved, 600)

    def test_assigned_positioning_leg_extends_native_chain(self):
        payload, solved = fixture()
        payload["flights"].append({"flightNumber": "DH", "departureUTCDateTime": "2026-09-08T08:00:00",
            "arrivalUTCDateTime": "2026-09-08T09:00:00", "departureAirport": "AAA", "arrivalAirport": "CCC"})
        solved["flightAssignments"].append({"id": "DH-0", "flight": "DH", "employee": "E1",
                                            "deadhead": True, "chainId": "каталог:7:slot:0"})
        report = certify(se.certifier_input(payload, solved, 600))
        self.assertIn("R-DAYS", report["certificate"]["violated_rules"])


if __name__ == "__main__":
    unittest.main()
