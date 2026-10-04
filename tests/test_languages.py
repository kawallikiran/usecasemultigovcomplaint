"""Language support tests: script identification, routing and replies in more Indian languages."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from grievdesk.adapters import normalize                  # noqa: E402
from grievdesk.common.config import load_config          # noqa: E402
from grievdesk.system import GrievanceRouter              # noqa: E402

CFG = os.path.join(ROOT, "config", "grievance.yaml")


class TestLanguages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL"):
            os.environ.pop(k, None)
        cls.r = GrievanceRouter(load_config(CFG))

    def lang(self, text):
        return self.r.process({"id": "x", "text": text})["language"]

    def test_normalize_keeps_vowel_signs_and_drops_zero_width(self):
        self.assertIn("குழிகள்", normalize("சாலையில் பெரிய குழிகள்"))
        self.assertEqual(normalize("ట్రాన్\u200cస్ఫార్మర్"), normalize("ట్రాన్స్ఫార్మర్"))

    def test_scripts_identified(self):
        cases = {"ta": "ரேஷன் கடையில் அரிசி கொடுக்கவில்லை.", "te": "కరెంటు లేదు", "kn": "ನೀರು ಬರುತ್ತಿಲ್ಲ",
                 "ml": "വെള്ളം ഇല്ല", "gu": "પાણી નથી", "pa": "ਬਿਜਲੀ ਨਹੀਂ ਹੈ", "or": "ପାଣି ମିଳୁନାହିଁ",
                 "ur": "پانی نہیں آ رہا", "bn": "আমাদের গ্রামে জল নেই", "as": "আমাৰ গাঁৱত পানী নাই"}
        for code, text in cases.items():
            self.assertEqual(self.lang(text), code, text)

    def test_devanagari_languages_told_apart(self):
        self.assertEqual(self.lang("आमच्या गावात पाणी नाही"), "mr")
        self.assertEqual(self.lang("हमर गांव म पानी नइ हे"), "cg")
        self.assertEqual(self.lang("हमारे गांव में पानी नहीं है"), "hi")

    def test_routing_in_new_languages(self):
        cases = {"ration": "ரேஷன் கடையில் அரிசி கொடுக்கவில்லை.",
                 "health": "ہسپتال میں ڈاکٹر نہیں ہے اور دوائی نہیں ملتی۔",
                 "electricity": "ਸਾਡੇ ਪਿੰਡ ਵਿੱਚ ਬਿਜਲੀ ਨਹੀਂ ਹੈ, ਟਰਾਂਸਫਾਰਮਰ ਸੜ ਗਿਆ।",
                 "water": "ನಮ್ಮ ಊರಿನಲ್ಲಿ ಕುಡಿಯುವ ನೀರು ಬರುತ್ತಿಲ್ಲ, ಕೊಳವೆ ಬಾವಿ ಕೆಟ್ಟಿದೆ."}
        for dept, text in cases.items():
            self.assertEqual(self.r.process({"id": "x", "text": text})["department"], dept, text)

    def test_sensitive_in_new_languages(self):
        o = self.r.process({"id": "x", "text": "నా భర్త నన్ను కొట్టాడు, చంపుతానని బెదిరిస్తున్నాడు."})
        self.assertIn("violence", o["sensitive_categories"])
        self.assertEqual(o["assigned_to"], "designated sensitive-case officer")
        o = self.r.process({"id": "y", "text": "வில்லேஜ் அலுவலர் லஞ்சம் கேட்டார்"})
        self.assertIn("corruption", o["sensitive_categories"])

    def test_reply_line_in_citizen_language_and_facts_in_template(self):
        o = self.r.process({"id": "x", "text": "ரேஷன் கடையில் அரிசி கொடுக்கவில்லை."})
        self.assertTrue(o["auto_reply"].startswith("உங்கள் புகார்"))
        self.assertIn("within 7 days", o["auto_reply"])

    def test_identify_only_goes_to_language_desk(self):
        o = self.r.process({"id": "x", "text": "ᱥᱟᱱᱛᱟᱲᱤ ᱯᱟᱹᱨᱥᱤ"})
        self.assertEqual(o["language"], "sat")
        self.assertIn("language_desk", o["flags"])
        self.assertTrue(o["human_required"])

    def test_non_indian_script_unsupported(self):
        o = self.r.process({"id": "x", "text": "我们村没有水"})
        self.assertIn("unsupported_language", o["flags"])

    def test_no_keyword_in_two_departments(self):
        seen = {}
        for dept, d in self.r.depts.items():
            for kw in d["keywords"]:
                seen.setdefault(kw, set()).add(dept)
        clashes = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(clashes, {}, f"keywords in more than one department: {clashes}")


if __name__ == "__main__":
    unittest.main()
