"""Early-warning layer: planted patterns, false alarms, privacy and the workbook."""
import csv
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from grievdesk.common.config import load_config    # noqa: E402
from grievdesk.system import GrievanceRouter        # noqa: E402
from grievdesk import early_warning as ew          # noqa: E402
from grievdesk.ew_datagen import generate          # noqa: E402


class TestEarlyWarning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL"):
            os.environ.pop(k, None)
        cls.tmp = tempfile.mkdtemp()
        cls.cfg = load_config(os.path.join(ROOT, "config", "early_warning.yaml"))
        cls.cfg.update(dataset=os.path.join(cls.tmp, "c.csv"), output_dir=os.path.join(cls.tmp, "out"))
        generate(cls.cfg)
        cls.rows, cls.items, cls.groups, cls.alerts, cls.findings = ew.run(cls.cfg, GrievanceRouter(cls.cfg))

    def key(self, issue, area):
        return next((a for a in self.alerts if a["issue"] == issue and a["area"] == area), None)

    def test_generator_uses_real_places_and_is_complete(self):
        self.assertEqual(len(self.rows), 2000)
        from grievdesk.locations import load
        real = {(r["state"], r["district"], r["town"]) for r in load()}
        self.assertTrue(all((r["state"], r["district"], r["town"]) in real for r in self.rows))
        self.assertTrue(all(r["synthetic"] == "yes" for r in self.rows))
        self.assertEqual(len({r["zone"] for r in self.rows}), 6)
        spike = [r for r in self.rows if r["scenario"] == "spike_water" and r["date"] >= "2026-09-20"]
        self.assertEqual(len(spike), 127)
        self.assertEqual(len({r["town"] for r in spike}), 14)

    def test_water_spike_alert_is_high(self):
        a = self.key("water", "Raipur, Chhattisgarh")
        self.assertIsNotNone(a)
        self.assertEqual((a["current"], a["previous"], a["towns"], a["priority"], a["zone"]), (127, 77, 14, "High", "Central Zone"))

    def test_no_false_alarms(self):
        self.assertIsNone(self.key("electricity", "North Twentyfour Parganas, West Bengal"))
        self.assertIsNone(self.key("roads", "Ernakulam, Kerala"))
        self.assertEqual(len(self.alerts), 2)

    def test_officer_routed_complaints_are_counted(self):
        a = self.key("water", "Raipur, Chhattisgarh")
        self.assertGreater(a["provisional_pct"], 0)

    def test_privacy(self):
        for a in self.alerts:
            for c in a["town_counts"].values():
                self.assertFalse(c.isdigit() and int(c) < 5)
            text = " ".join(sum(ew.alert_card(a, self.cfg)[:2], []))
            self.assertFalse(any(r["text"] in text for r in self.rows))

    def test_card_wording(self):
        lines, _ = ew.alert_card(self.key("water", "Raipur, Chhattisgarh"), self.cfg)
        self.assertIn("127 related complaints across 14 towns", lines[1])
        self.assertIn("Raipur district, Chhattisgarh (Central Zone)", lines[0])
        self.assertIn("field verification recommended", lines[-1])

    def test_outputs_written(self):
        ew.write_outputs(self.cfg, self.items, self.groups, self.alerts, self.findings)
        out = self.cfg["output_dir"]
        for f in ("issue_trends.csv", "alerts.txt", "report.md"):
            self.assertTrue(os.path.exists(os.path.join(out, f)), f)
        try:
            import openpyxl  # noqa: F401
            self.assertTrue(os.path.exists(os.path.join(out, "early_warning.xlsx")))
        except ImportError:
            pass


if __name__ == "__main__":
    unittest.main()
