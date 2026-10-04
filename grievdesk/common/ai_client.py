"""One small client for well-known AI services. Standard library only (no SDKs).

Providers
  openai     Any OpenAI-compatible Chat Completions API: OpenAI, Azure-style gateways, Groq,
             Together, OpenRouter, local Ollama (http://host.docker.internal:11434/v1) and others.
  anthropic  Anthropic Messages API (Claude).
  gemini     Google Gemini generateContent API.

Settings come from the `ai:` block of the YAML config and can be overridden by environment
variables (AI_PROVIDER, AI_MODEL, AI_BASE_URL, AI_API_KEY), e.g. from a .env file in Docker.
The key is read from the environment only and is never written to logs or reports.
"""
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


class AIError(RuntimeError):
    pass


def ai_settings(cfg: dict) -> dict:
    """Settings from the config's ai block (usually filled from a profile in ai_providers.yaml).
    If the block names no provider, the single-service variables AI_PROVIDER / AI_MODEL / AI_BASE_URL /
    AI_API_KEY from the environment are used instead."""
    a = dict(cfg.get("ai") or {})
    from_env = not (a.get("provider") or "").strip()
    if from_env:
        for env, key in (("AI_PROVIDER", "provider"), ("AI_MODEL", "model"), ("AI_BASE_URL", "base_url")):
            if os.environ.get(env):
                a[key] = os.environ[env]
        a.setdefault("api_key_env", "AI_API_KEY")
    a["provider"] = (a.get("provider") or "").strip().lower()
    a["api_key"] = os.environ.get(a.get("api_key_env") or "", "") if a.get("api_key_env") else ""
    if os.environ.get("AI_TIMEOUT"):
        a["timeout"] = os.environ["AI_TIMEOUT"]
    a.setdefault("timeout", 60)
    a.setdefault("max_tokens", 800)
    a.setdefault("temperature", 0)
    return a


def ai_status(cfg: dict):
    """(ready: bool, message) - used by the UI to show whether the AI engine can be used."""
    a = ai_settings(cfg)
    if a["provider"] not in DEFAULT_BASE:
        return False, "No AI service configured. Set AI_PROVIDER, AI_MODEL and AI_API_KEY (see .env.example)."
    if not a.get("model"):
        return False, "No model set (add the model name to .env, see .env.example)."
    local = str(a.get("base_url") or "").startswith("http://")      # local servers (Ollama etc.) need no key
    if not a["api_key"] and not local:
        return False, f"{a.get('api_key_env') or 'API key'} is not set in .env."
    return True, f"{a['provider']} / {a['model']}"


def image_to_b64(img) -> str:
    """PIL image -> base64 PNG, downsized to keep requests small."""
    im = img.convert("RGB").copy()
    im.thumbnail((640, 640))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if not m:
            raise AIError("AI reply was not JSON")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise AIError(f"AI reply was not valid JSON: {e}")


class AIClient:
    def __init__(self, cfg: dict):
        self.s = ai_settings(cfg)
        ready, msg = ai_status(cfg)
        if not ready:
            raise AIError(msg)
        self.provider, self.model = self.s["provider"], self.s["model"]
        self.base = (self.s.get("base_url") or DEFAULT_BASE[self.provider]).rstrip("/")
        self.label = f"{self.provider}/{self.model}"
        self.calls = 0

    # ------------------------------------------------------------------ public
    def complete_json(self, system: str, user_text: str, images=()) -> dict:
        """Send a prompt (and optional PIL images); return the parsed JSON object the model produced."""
        b64 = [image_to_b64(i) for i in images]
        builder = {"openai": self._openai, "anthropic": self._anthropic, "gemini": self._gemini}[self.provider]
        url, headers, body, extract = builder(system, user_text, b64)
        last = None
        for attempt in range(2):                      # one retry on transient errors
            try:
                raw = self._post(url, headers, body)
                self.calls += 1
                try:
                    content = extract(raw)
                except (KeyError, IndexError, TypeError) as e:
                    raise AIError(f"Unexpected reply shape from {self.provider}: {e}")
                return parse_json(content)
            except AIError as e:
                last = e
                if "HTTP 4" in str(e) and "HTTP 429" not in str(e):
                    break                              # client errors will not fix themselves
                time.sleep(1.5)
        raise last

    # ------------------------------------------------------------------ providers
    def _openai(self, system, text, b64):
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in b64]
        body = {"model": self.model, "temperature": self.s["temperature"], "max_tokens": self.s["max_tokens"],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        if str(self.s.get("json_mode", True)).lower() not in ("false", "0", "no"):
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.s["api_key"]:
            headers["Authorization"] = f"Bearer {self.s['api_key']}"
        return (f"{self.base}/chat/completions", headers, body,
                lambda r: r["choices"][0]["message"]["content"])

    def _anthropic(self, system, text, b64):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b}}
                   for b in b64] + [{"type": "text", "text": text}]
        body = {"model": self.model, "max_tokens": self.s["max_tokens"], "temperature": self.s["temperature"],
                "system": system, "messages": [{"role": "user", "content": content}]}
        headers = {"Content-Type": "application/json", "x-api-key": self.s["api_key"],
                   "anthropic-version": "2023-06-01"}
        return (f"{self.base}/messages", headers, body,
                lambda r: "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text"))

    def _gemini(self, system, text, b64):
        parts = [{"text": text}] + [{"inline_data": {"mime_type": "image/png", "data": b}} for b in b64]
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": self.s["temperature"],
                                     "maxOutputTokens": self.s["max_tokens"],
                                     "responseMimeType": "application/json"}}
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.s["api_key"]}
        return (f"{self.base}/models/{self.model}:generateContent", headers, body,
                lambda r: r["candidates"][0]["content"]["parts"][0]["text"])

    # ------------------------------------------------------------------ transport
    def _post(self, url, headers, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(self.s["timeout"])) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise AIError(f"HTTP {e.code} from {self.provider}: {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AIError(f"Cannot reach {self.provider}: {e}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise AIError(f"Unexpected reply from {self.provider}: {e}")
