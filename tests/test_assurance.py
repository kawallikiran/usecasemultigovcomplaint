"""The six fixes behind the Assurance page: consent, appeal, overdue escalation, 21-day deadlines,
citizen data requests with retention, and the quick-approval measure."""
import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from grievdesk import portal as P                    # noqa: E402
from grievdesk import quality as Q                   # noqa: E402
from grievdesk.common.config import load_config     # noqa: E402
from grievdesk.system import GrievanceRouter         # noqa: E402

PLACE = dict(state="Tamil Nadu", district="Chennai", town="Chennai")
WATER = "No drinking water from the handpump for a week."


class TestAssurance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL", "SMS_GATEWAY_URL", "SMTP_HOST"):
            os.environ.pop(k, None)
        cls.T = P.Text()
        cls.r = GrievanceRouter(load_config(os.path.join(ROOT, "config", "grievance.yaml")))

    def setUp(self):
        self.s = P.Store(os.path.join(tempfile.mkdtemp(), "c.json"))

    def file(self, text=WATER, **kw):
        args = dict(PLACE, notify_by="sms", mobile="9876543210")
        args.update(kw)
        return P.submit(self.r, self.s, self.T, "en", "text", text=text, **args)

    def closed(self, seconds=60):
        rec = self.file()
        return P.approve(self.s, self.T, self.r.depts, rec, "OFF-WAT01", "Handpump repaired.", review_seconds=seconds)[0]

    def test_privacy_notice_and_consent_text_in_every_language(self):
        for lang in self.T.strings:
            self.assertIn("90", self.T(lang, "privacy_notice").format(days=90), lang)
            self.assertTrue(self.T(lang, "consent"))

    def test_consent_recorded_with_notice_version(self):
        c = self.file()["consent"]
        self.assertEqual((c["given"], c["notice_version"]), (True, P.NOTICE_VERSION))

    def test_no_deadline_over_21_days(self):
        self.assertLessEqual(max(d["sla_days"] for d in self.r.depts.values()), 21)

    def test_satisfied_and_appeal(self):
        rec = self.closed()
        self.assertTrue(P.citizen_view(rec, self.T, "en", self.r.depts)["can_rate"])
        self.assertEqual(P.rate(self.s, rec, False, "")[1], ["reason"])
        new, _ = P.rate(self.s, rec, False, "Still no water. Aadhaar 2345 6789 0123")
        self.assertEqual((new["status"], new["assigned_to"]), ("appealed", "OFF-SR01"))
        self.assertNotIn("2345 6789 0123", new["history"][-1]["note"])
        self.assertIn(new["ticket"], [r["ticket"] for r in P.queue(self.s, "OFF-SR01")])
        done, errs, _ = P.approve(self.s, self.T, self.r.depts, new, "OFF-SR01", "Re-inspected; supply restored.")
        self.assertTrue(done["history"][-1]["appeal_decided"])
        self.assertFalse(P.citizen_view(done, self.T, "en", self.r.depts)["can_rate"])   # rated once only

    def test_overdue_escalates_and_citizen_is_told(self):
        rec = self.file()
        self.assertEqual(P.auto_escalate_overdue(self.s, self.T, self.r.depts), [])
        later = dt.date.today() + dt.timedelta(days=30)
        self.assertEqual(P.auto_escalate_overdue(self.s, self.T, self.r.depts, today=later), [rec["ticket"]])
        self.assertEqual(self.s.get(rec["ticket"])["status"], "escalated")
        self.assertEqual(self.s.outbox.all()[-1]["kind"], "escalated")

    def test_deletion_request_erases_contact(self):
        rec = self.closed()
        req = P.data_request(self.s, rec, "delete")
        self.assertEqual(self.s.contact(rec["ticket"])["mobile"], "9876543210")
        P.close_data_request(self.s, req["id"], "ADMIN01")
        self.assertNotIn("mobile", self.s.contact(rec["ticket"]))
        self.assertTrue(self.s.get(rec["ticket"]).get("contact_erased"))

    def test_retention_deletes_only_after_the_period(self):
        rec = self.closed()
        self.assertEqual(P.apply_retention(self.s, 90), [])
        self.assertEqual(P.apply_retention(self.s, 90, today=dt.date.today() + dt.timedelta(days=91)), [rec["ticket"]])
        self.assertNotIn("mobile", self.s.contact(rec["ticket"]))

    def test_quick_approvals_counted(self):
        self.closed(seconds=10)
        self.closed(seconds=120)
        f = P.assurance_figures(self.s)
        self.assertEqual((f["approvals"], f["quick_approvals"], f["consent_share"]), (2, 1, 100))

    def test_assurance_tables(self):
        f = P.assurance_figures(self.s)
        rows = Q.compliance_rows(90, 21, f)
        self.assertTrue(all(r["Status"] in ("Met", "Partly met") for r in rows))
        self.assertEqual(len(Q.stakeholder_rows(f, 90)), 6)


if __name__ == "__main__":
    unittest.main()
