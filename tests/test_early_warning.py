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
        cls.cfg.update(dataset=os.path.join(cls.tmp, "c.csv"), villages=os.path.join(cls.tmp, "v.csv"),
                       output_dir=os.path.join(cls.tmp, "out"))
        generate(cls.cfg)
        cls.rows, cls.items, cls.groups, cls.alerts, cls.findings = ew.run(cls.cfg, GrievanceRouter(cls.cfg))

    def key(self, issue, block):
        return next((a for a in self.alerts if a["issue"] == issue and a["block"] == block), None)

    def test_generator_is_deterministic_and_complete(self):
        self.assertEqual(len(self.rows), 500)
        spike = [r for r in self.rows if r["scenario"] == "spike_water" and r["date"] >= "2026-09-20"]
        self.assertEqual(len(spike), 127)
        self.assertEqual(len({r["village"] for r in spike}), 23)

    def test_water_spike_alert_is_high(self):
        a = self.key("water", "South Block")
        self.assertIsNotNone(a)
        self.assertEqual((a["current"], a["previous"], a["villages"], a["priority"]), (127, 77, 23, "High"))

    def test_no_false_alarms(self):
        self.assertIsNone(self.key("electricity", "East Block"))
        self.assertIsNone(self.key("roads", "North Block"))
        self.assertEqual(len(self.alerts), 2)

    def test_officer_routed_complaints_are_counted(self):
        a = self.key("water", "South Block")
        self.assertGreater(a["provisional_pct"], 0)

    def test_privacy(self):
        for a in self.alerts:
            for c in a["village_counts"].values():
                self.assertFalse(c.isdigit() and int(c) < 5)
            text = " ".join(sum(ew.alert_card(a, self.cfg)[:2], []))
            self.assertFalse(any(r["text"] in text for r in self.rows))

    def test_card_wording(self):
        lines, _ = ew.alert_card(self.key("water", "South Block"), self.cfg)
        self.assertIn("127 related complaints across 23 villages", lines[1])
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
