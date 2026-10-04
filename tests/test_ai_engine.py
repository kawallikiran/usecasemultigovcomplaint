"""AI engine tests with a local mock server (no key, no network). Covers all three provider formats."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

REPLY = {}          # JSON the mock "model" returns
SEEN = []           # raw request bodies, to check what was sent
MODELS = {"ids": ["gemma3:4b"]}
AUTH = []
FAIL = {"on": False}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):                    # Ollama-style model list
        b = json.dumps({"data": [{"id": i} for i in MODELS["ids"]]}).encode()
        self.send_response(200 if self.path.endswith("/models") else 404)
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        SEEN.append(raw)
        if FAIL["on"]:
            self.send_response(500); self.end_headers(); return
        t = json.dumps(REPLY)
        AUTH.append(self.headers.get("Authorization"))
        if self.path.endswith("/chat/completions"):
            ok, body = self.headers.get("Authorization") in ("Bearer k", None), {"choices": [{"message": {"content": t}}]}
        elif self.path.endswith("/messages"):
            ok, body = self.headers.get("x-api-key") == "k", {"content": [{"type": "text", "text": "```json\n" + t + "\n```"}]}
        else:
            ok, body = self.headers.get("x-goog-api-key") == "k", {"candidates": [{"content": {"parts": [{"text": t}]}}]}
        b = json.dumps(body).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
PORT = SERVER.server_address[1]
BASES = {"openai": f"http://127.0.0.1:{PORT}/v1", "anthropic": f"http://127.0.0.1:{PORT}/v1",
         "gemini": f"http://127.0.0.1:{PORT}/v1beta"}


def use(provider):
    os.environ.update(AI_PROVIDER=provider, AI_MODEL="mock", AI_API_KEY="k", AI_BASE_URL=BASES[provider])
    FAIL["on"] = False
    SEEN.clear()


def clear_env():
    for k in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY", "AI_BASE_URL"):
        os.environ.pop(k, None)


from grievdesk.common.config import load_config   # noqa: E402
from grievdesk.system import GrievanceRouter       # noqa: E402

CFG = os.path.join(ROOT, "config", "grievance_ai.yaml")


class TestGrievanceAI(unittest.TestCase):
    def tearDown(self):
        clear_env()

    def router(self, provider):
        use(provider)
        return GrievanceRouter(load_config(CFG))

    def test_all_providers_route(self):
        REPLY.clear(); REPLY.update(language="en", language_confidence=0.9, department="water",
                                    department_confidence=0.9, sensitive_categories=[], reason="water")
        for p in ("openai", "anthropic", "gemini"):
            o = self.router(p).process({"id": "a", "text": "No water from the handpump."})
            self.assertEqual(o["department"], "water", p)
            self.assertTrue(o["engine"].startswith(f"ai:{p}"))

    def test_pii_masked_before_sending(self):
        REPLY.clear(); REPLY.update(language="en", language_confidence=0.9, department="water",
                                    department_confidence=0.9, sensitive_categories=[])
        self.router("openai").process({"id": "b", "text": "Aadhaar 2345 6789 0123 phone 9876543210, no water"})
        self.assertFalse(any("2345 6789 0123" in s or "9876543210" in s for s in SEEN))

    def test_ai_cannot_remove_rule_sensitive_flag(self):
        REPLY.clear(); REPLY.update(language="hi", language_confidence=0.9, department="police",
                                    department_confidence=0.9, sensitive_categories=[])
        o = self.router("anthropic").process({"id": "c", "text": "पड़ोसी ने मारपीट की और धमकी दी"})
        self.assertIn("violence", o["sensitive_categories"])

    def test_invented_department_rejected(self):
        REPLY.clear(); REPLY.update(language="en", language_confidence=0.9, department="ministry_of_magic",
                                    department_confidence=0.99, sensitive_categories=["made_up"])
        o = self.router("gemini").process({"id": "d", "text": "Something odd happened."})
        self.assertIsNone(o["department"])
        self.assertTrue(o["human_required"])
        self.assertNotIn("made_up", o["sensitive_categories"])

    def test_service_failure_falls_back_to_human(self):
        r = self.router("openai")
        FAIL["on"] = True
        o = r.process({"id": "e", "text": "No water from the handpump for a week."})
        self.assertIn("ai_service_failure", o["flags"])
        self.assertTrue(o["human_required"])

    def test_not_configured_is_reported(self):
        clear_env()
        from grievdesk.common.ai_client import ai_status
        self.assertFalse(ai_status(load_config(CFG))[0])



class TestProviderProfiles(unittest.TestCase):
    def setUp(self):
        from grievdesk.common import ai_profiles
        self.p = ai_profiles
        ai_profiles._CACHE.clear()

    def tearDown(self):
        for k in ("OLLAMA_URL", "LOCAL_MODEL", "OPENAI_MODEL", "OPENAI_API_KEY"):
            os.environ.pop(k, None)

    def test_expand_defaults(self):
        os.environ.pop("LOCAL_MODEL", None)
        self.assertEqual(self.p.expand("${LOCAL_MODEL:-gemma3:4b}"), "gemma3:4b")
        os.environ["LOCAL_MODEL"] = "qwen2.5vl:7b"
        self.assertEqual(self.p.expand("${LOCAL_MODEL:-gemma3:4b}"), "qwen2.5vl:7b")

    def test_providers_file_lists_local_and_cloud(self):
        names = set(self.p.load_profiles(ROOT))
        self.assertTrue({"local-open-model", "openai", "anthropic", "gemini", "groq", "openrouter", "custom"} <= names)

    def test_cloud_needs_key(self):
        os.environ["OPENAI_MODEL"] = "some-model"
        ok, msg = self.p.profile_status(self.p.load_profiles(ROOT)["openai"])
        self.assertFalse(ok)
        self.assertIn("OPENAI_API_KEY", msg)

    def test_local_states(self):
        os.environ["OLLAMA_URL"] = "http://127.0.0.1:9/v1"
        self.assertFalse(self.p.profile_status(self.p.load_profiles(ROOT)["local-open-model"])[0])
        os.environ["OLLAMA_URL"] = BASES["openai"]
        self.p._CACHE.clear()
        MODELS["ids"] = ["other:1b"]
        ok, msg = self.p.profile_status(self.p.load_profiles(ROOT)["local-open-model"])
        self.assertFalse(ok)
        self.assertIn("not downloaded", msg)
        MODELS["ids"] = ["gemma3:4b"]
        self.p._CACHE.clear()
        self.assertTrue(self.p.profile_status(self.p.load_profiles(ROOT)["local-open-model"])[0])

    def test_profile_applied_with_small_critic_run_and_no_key_sent(self):
        os.environ["OLLAMA_URL"] = BASES["openai"]
        clear_env()
        cfg = self.p.apply_profile(load_config(CFG), self.p.load_profiles(ROOT)["local-open-model"])
        self.assertEqual(cfg["critic"]["max_cases"], 8)
        self.assertEqual(cfg["ai"]["model"], "gemma3:4b")
        from grievdesk.common.ai_client import AIClient
        REPLY.clear(); REPLY.update(ok=True)
        FAIL["on"] = False
        AUTH.clear()
        AIClient(cfg).complete_json("system", "hello")
        self.assertEqual(AUTH[-1], None)


if __name__ == "__main__":
    unittest.main()
