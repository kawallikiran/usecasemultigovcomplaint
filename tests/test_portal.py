"""Portal logic: screen text, citizen flow, voice, officer rules, downloads, speech-to-text."""
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
from grievdesk.common.config import load_config     # noqa: E402
from grievdesk.system import GrievanceRouter         # noqa: E402


class STT(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        ok = self.path.endswith("/audio/transcriptions") and b'name="file"' in body and b'name="model"' in body
        out = json.dumps({"text": "गाँव में नल से पानी नहीं आ रहा है, हैंडपंप खराब है।"}).encode()
        self.send_response(200 if ok else 400)
        self.end_headers()
        self.wfile.write(out)


class TestPortal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL"):
            os.environ.pop(k, None)
        cls.T = P.Text()
        cls.r = GrievanceRouter(load_config(os.path.join(ROOT, "config", "grievance.yaml")))

    def setUp(self):
        self.store = P.Store(os.path.join(tempfile.mkdtemp(), "c.json"))

    def test_every_language_has_every_text(self):
        keys = set(self.T.strings["en"])
        for lang, d in self.T.strings.items():
            self.assertEqual(set(d), keys, lang)
        self.assertEqual(self.T("cg", "title"), self.T("hi", "title"))      # Chhattisgarhi uses Hindi screen text

    def test_typed_complaint_flow(self):
        rec = P.submit(self.r, self.store, "ta", "text", text="ரேஷன் கடையில் அரிசி கொடுக்கவில்லை.",
                       block="South Block", village="Amadi", mobile="9876543210")
        self.assertRegex(rec["ticket"], r"^GRV/\d{4}/\d{5}$")
        self.assertEqual((rec["status"], rec["department"], rec["mobile"]), ("forwarded", "ration", "XXXXXX3210"))
        status, _ = P.citizen_status(rec, self.T, "ta", self.r.depts)
        self.assertEqual(status, self.T("ta", "st_forwarded"))
        html = P.receipt_html(rec, self.T, "ta", self.r.depts)
        self.assertIn(rec["ticket"], html)
        self.assertIn(self.T("ta", "ack_title"), html)

    def test_aadhaar_in_complaint_is_hidden(self):
        rec = P.submit(self.r, self.store, "en", "text", text="My Aadhaar 2345 6789 0123, no water from handpump")
        self.assertNotIn("2345 6789 0123", rec["text"])

    def test_voice_without_speech_service_waits_for_officer(self):
        rec = P.submit(self.r, self.store, "hi", "voice", audio=b"RIFFxxxx", block="North Block", village="Amapur")
        self.assertEqual((rec["status"], rec["transcript"]), ("review", "pending"))
        _, errs = P.act(self.store, rec, "reply", "Asha", reply="done")
        self.assertIn("Type the voice complaint first.", errs)
        new, errs = P.save_transcript(self.r, self.store, rec["ticket"], "गाँव में नल से पानी नहीं आ रहा है, हैंडपंप खराब है।", "Asha")
        self.assertEqual(new["department"], "water")

    def test_voice_with_speech_service(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), STT)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            prof = {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m", "api_key_env": ""}
            rec = P.submit(self.r, self.store, "hi", "voice", audio=b"RIFFxxxx", speech_profile=prof)
            self.assertEqual((rec["transcript"], rec["department"]), ("automatic", "water"))
        finally:
            srv.shutdown()

    def test_officer_rules(self):
        rec = P.submit(self.r, self.store, "hi", "text", text="पड़ोसी ने मारपीट की और धमकी दी")
        _, errs = P.act(self.store, rec, "reply", "Asha", reply="ok")
        self.assertTrue(any("Sensitive" in e for e in errs))
        _, errs = P.act(self.store, rec, "escalate", "Asha")
        self.assertIn("Add a short note for the record.", errs)
        new, errs = P.act(self.store, rec, "escalate", "Asha", note="threat to life")
        self.assertEqual((errs, new["status"]), ([], "escalated"))

    def test_placeholder_must_be_replaced(self):
        rec = P.submit(self.r, self.store, "en", "text", text="No drinking water from the handpump for a week.")
        _, errs = P.act(self.store, rec, "reply", "Asha", reply=rec["suggestion"]["draft_reply"])
        self.assertTrue(any("placeholder" in e for e in errs))

    def test_downloads(self):
        P.submit(self.r, self.store, "en", "text", text="No drinking water from the handpump for a week.")
        reg = P.register_csv(self.store, self.r.depts)
        self.assertTrue(reg.startswith("\ufeff"))
        self.assertIn("Public Health Engineering Department", reg)
        self.assertIn("registered", P.actions_csv(self.store))

    def test_queue_puts_sensitive_first(self):
        P.submit(self.r, self.store, "en", "text", text="No drinking water from the handpump for a week.")
        s = P.submit(self.r, self.store, "hi", "text", text="पड़ोसी ने मारपीट की और धमकी दी")
        self.assertEqual(P.queue(self.store)[0]["ticket"], s["ticket"])


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
        next(b for b in at.button if b.label == "Submit complaint").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("GRV/" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
