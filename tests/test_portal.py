"""Portal workflow: frequent complaints, filing, officer review, notifications, analytics, quality view."""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from grievdesk import portal as P                    # noqa: E402
from grievdesk import analytics as A                 # noqa: E402
from grievdesk import quality as Q                   # noqa: E402
from grievdesk.common.config import load_config     # noqa: E402
from grievdesk.system import GrievanceRouter         # noqa: E402

DEMO = dict(state="Tamil Nadu", district="Coimbatore", town="Coimbatore", locality="Ward 12")


class Hook(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/audio/transcriptions"):
            out = json.dumps({"text": "गाँव में नल से पानी नहीं आ रहा है, हैंडपंप खराब है।"}).encode()
        else:
            Hook.seen.append(json.loads(body))
            out = b"{}"
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)


class TestPortal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL", "SMS_GATEWAY_URL", "SMTP_HOST"):
            os.environ.pop(k, None)
        cls.T = P.Text()
        cls.r = GrievanceRouter(load_config(os.path.join(ROOT, "config", "grievance.yaml")))

    def setUp(self):
        self.store = P.Store(os.path.join(tempfile.mkdtemp(), "c.json"))

    def file(self, text, lang="en", **kw):
        args = dict(DEMO, notify_by="sms", mobile="9876543210")
        args.update(kw)
        return P.submit(self.r, self.store, self.T, lang, "text", text=text, **args)

    def test_screen_text_complete(self):
        keys = set(self.T.strings["en"])
        for lang, d in self.T.strings.items():
            self.assertEqual(set(d), keys, lang)

    def test_frequent_complaints_route_correctly_in_every_language(self):
        import yaml
        with open(os.path.join(ROOT, "data", "grievance", "common_complaints.yaml"), encoding="utf-8") as f:
            data = yaml.safe_load(f)["complaints"]
        for lang, items in data.items():
            self.assertEqual(len(items), 8, lang)
            for it in items:
                o = self.r.process({"id": "t", "text": it["text"]})
                self.assertEqual(o["department"], it["department"], (lang, it["text"]))
                self.assertFalse(o["sensitive_categories"], (lang, it["text"]))

    def test_locations_and_zones(self):
        from grievdesk import locations as L
        rows = L.load()
        self.assertGreater(len(rows), 5000)
        st = {r["state"] for r in rows}
        for z, states in L.ZONES.items():
            for s_ in states:
                self.assertIn(s_, st, s_)
        self.assertNotIn("Unmapped", {r["zone"] for r in rows})
        self.assertEqual(L.zone_of("Telangana"), "South Zone")
        self.assertIn("Hyderabad", {r["district"] for r in rows if r["state"] == "Telangana"})
        self.assertEqual({r["district"] for r in rows if r["state"] == "Ladakh"}, {"Leh (Ladakh)", "Kargil"})
        rec = self.file("No drinking water from the handpump for a week.")
        self.assertEqual((rec["zone"], rec["town"], rec["locality"]), ("South Zone", "Coimbatore", "Ward 12"))

    def test_filing_assigns_named_officer_and_acknowledges(self):
        tpl = P.common_complaints("ta")[3]
        rec = self.file(tpl["text"], "ta", template_category=tpl["category"])
        self.assertEqual((rec["status"], rec["department"], rec["assigned_to"], rec["category"]),
                         ("pending_review", "ration", "OFF-RAT01", "ration_not_given"))
        v = P.citizen_view(rec, self.T, "ta", self.r.depts)
        self.assertEqual(v["officer"], "Food Inspector")
        msg = self.store.outbox.all()[-1]
        self.assertEqual((msg["channel"], msg["to"], msg["kind"]), ("sms", "XXXXXX3210", "registered"))
        self.assertIn(rec["ticket"], msg["message"])

    def test_nothing_is_answered_without_an_officer(self):
        rec = self.file("No drinking water from the handpump for a week.")
        self.assertEqual(rec["status"], "pending_review")
        self.assertEqual(rec["reply"], "")

    def test_only_assigned_officer_or_supervisor_can_approve(self):
        rec = self.file("No drinking water from the handpump for a week.")
        self.assertIn("This complaint is assigned to another officer.", P.approve(self.store, self.T, self.r.depts, rec, "OFF-RAT01", "x")[1])
        _, errs, *_ = P.approve(self.store, self.T, self.r.depts, rec, "OFF-WAT01", rec["suggestion"]["draft_reply"])
        self.assertTrue(any("placeholder" in e for e in errs))
        new, errs, note = P.approve(self.store, self.T, self.r.depts, rec, "OFF-SR01", "Handpump repaired.", priority="high")
        self.assertEqual((errs, new["status"], new["priority"], note["kind"]), ([], "approved", "high", "approved"))

    def test_reassign_moves_to_the_new_departments_officer(self):
        rec = self.file("No drinking water from the handpump for a week.")
        _, errs = P.reassign(self.store, self.r, rec, "OFF-WAT01", "roads", "")
        self.assertIn("Add a short note explaining the reassignment.", errs)
        new, errs = P.reassign(self.store, self.r, rec, "OFF-WAT01", "roads", "pipe broken by road works")
        self.assertEqual((new["assigned_to"], new["status"]), ("OFF-ROA01", "pending_review"))

    def test_sensitive_goes_to_designated_officer(self):
        rec = self.file("पड़ोसी ने मारपीट की और धमकी दी", "hi")
        self.assertEqual(rec["assigned_to"], "OFF-SC01")
        new, errs = P.escalate(self.store, self.T, self.r.depts, rec, "OFF-SC01", "threat to life")
        self.assertEqual((new["assigned_to"], new["status"]), ("OFF-SR01", "escalated"))

    def test_voice_waits_for_officer_then_routes(self):
        rec = P.submit(self.r, self.store, self.T, "hi", "voice", audio=b"RIFFxxxx", notify_by="portal", **DEMO)
        self.assertEqual((rec["assigned_to"], rec["transcript"]), ("OFF-GC01", "pending"))
        new, _ = P.save_transcript(self.r, self.store, rec["ticket"], "गाँव में नल से पानी नहीं आ रहा है, हैंडपंप खराब है।", "OFF-GC01")
        self.assertEqual((new["department"], new["assigned_to"]), ("water", "OFF-WAT01"))

    def test_speech_service_and_sms_gateway(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        os.environ["SMS_GATEWAY_URL"] = base + "/sms"
        try:
            prof = {"base_url": base + "/v1", "model": "m", "api_key_env": ""}
            rec = P.submit(self.r, self.store, self.T, "hi", "voice", audio=b"RIFF", notify_by="sms", mobile="9876543210",
                           speech_profile=prof, **DEMO)
            self.assertEqual((rec["transcript"], rec["department"]), ("automatic", "water"))
            self.assertEqual(self.store.outbox.all()[-1]["status"], "sent")
            self.assertEqual(Hook.seen[-1]["to"], "9876543210")
        finally:
            os.environ.pop("SMS_GATEWAY_URL", None)
            srv.shutdown()

    def test_contacts_kept_out_of_the_register(self):
        rec = self.file("No drinking water from the handpump for a week.")
        self.assertNotIn("9876543210", json.dumps(self.store.get(rec["ticket"])))
        self.assertNotIn("9876543210", P.register_csv(self.store, self.r.depts))
        self.assertNotIn("9876543210", json.dumps(self.store.outbox.all()))

    def test_audit_and_agreement(self):
        rec = self.file("No drinking water from the handpump for a week.")
        P.approve(self.store, self.T, self.r.depts, rec, "OFF-WAT01", "Repaired.")
        self.assertEqual(P.review_agreement(self.store), {"approved": 1, "kept": 1, "changed": 0})
        self.assertEqual({r["Action"] for r in P.audit_rows(self.store)}, {"registered", "approved"})


class TestAnalyticsAndQuality(unittest.TestCase):
    def test_analytics_tables(self):
        df, vil = A.load()
        as_of = dt.date(2026, 10, 3)
        self.assertEqual(len(df), 2000)
        self.assertEqual(set(df["zone"]), {"North Zone", "South Zone", "East Zone", "West Zone", "Central Zone", "Northeast Zone"})
        k = A.kpis(df, as_of)
        self.assertEqual(k["Complaints"], k["Resolved"] + k["Pending"])
        self.assertEqual(len(A.filter_df(df, zone="Central Zone", state="Chhattisgarh", district="Raipur")),
                         int(((df["state"] == "Chhattisgarh") & (df["district"] == "Raipur")).sum()))
        loc = A.location_table(df, vil)
        self.assertIn("Urban status", loc.columns)
        self.assertIn("Last 14 days", A.district_table(df, as_of).columns)
        self.assertGreater(len(A.focus_areas(df, vil, [], as_of)), 0)
        self.assertGreater(len(A.excel_report(df, vil, [], as_of)), 1000)

    def test_plain_quality_view(self):
        findings = [{"test_no": 3, "passed": False, "severity": "High", "probe": "x", "actual": "leak"},
                    {"test_no": 1, "passed": True, "severity": "High", "probe": "y", "actual": ""}]
        rows = {r["Check"]: r for r in Q.plain_checks(findings)}
        self.assertEqual(rows["Protects personal details"]["Result"], "Needs attention (serious)")
        self.assertEqual(rows["Fair to every language and area"]["Result"], "OK")


class TestStreamlitPortal(unittest.TestCase):
    """Loads the real app headlessly (runs in the container, where Streamlit is installed)."""

    def test_citizen_can_file_a_complaint(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            self.skipTest("streamlit not installed")
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
        if at.exception and "navigation" in str(at.exception).lower():
            self.skipTest("This Streamlit test runner does not support page navigation")
        self.assertFalse(at.exception, at.exception)
        at.text_area(key="complaint_text").set_value("No drinking water from the handpump for a week.")
        at.radio[1].set_value("portal")
        next(b for b in at.button if b.label == "Submit complaint").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("GRV/" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()


class TestOfficerScreens(unittest.TestCase):
    def setUp(self):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL"):
            os.environ.pop(k, None)
        self.T = P.Text()
        self.r = GrievanceRouter(load_config(os.path.join(ROOT, "config", "grievance.yaml")))
        self.store = P.Store(os.path.join(tempfile.mkdtemp(), "c.json"))

    def _file(self, text):
        from grievdesk import locations as Loc
        t = Loc.tree()
        kw = dict(state="Chhattisgarh", district="Raipur", town=t["Chhattisgarh"]["Raipur"][0], notify_by="portal")
        if "consent" in P.submit.__code__.co_varnames:
            kw["consent"] = True
        return P.submit(self.r, self.store, self.T, "en", "text", text=text, **kw)

    def test_card_shows_summary_and_assignee(self):
        rec = self._file("No drinking water from the handpump for a week.")
        c = P.card(rec, self.r.depts)
        self.assertEqual(c["ticket"], rec["ticket"])
        self.assertIn("Raipur", c["place"])
        self.assertIn("Devesh Nandrekh", c["assigned"])
        self.assertFalse(c["overdue"])
        late = P.card(rec, self.r.depts, today=dt.date(2030, 1, 1))
        self.assertTrue(late["overdue"])

    def test_counts_and_workload(self):
        rec = self._file("No drinking water from the handpump for a week.")
        self.assertEqual(P.my_counts(self.store, "OFF-WAT01")["Waiting for me"], 1)
        P.approve(self.store, self.T, self.r.depts, rec, "OFF-WAT01", "Repaired.")
        self.assertEqual(P.my_counts(self.store, "OFF-WAT01")["Approved by me"], 1)
        w = {x["Officer"]: x for x in P.workload(self.store)}
        self.assertEqual(w["Devesh Nandrekh"]["Closed"], 1)
        self.assertNotIn("Sameer Kolvanta", w)            # the administrator holds no complaints

    def test_officer_names_are_the_fictional_set(self):
        names = {o["name"] for o in P.officers().values()}
        self.assertEqual(len(names), 13)
        self.assertIn("Ishani Varoli", names)
