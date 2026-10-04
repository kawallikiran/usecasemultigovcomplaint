"""Speech-to-text for voice complaints. Standard library only.

Works with any service that offers the OpenAI-style endpoint POST <base_url>/audio/transcriptions
(OpenAI, Groq, and self-hosted servers that copy this API). Services are listed under `speech:` in
config/ai_providers.yaml; keys come from .env. If no service is ready, the recording is still saved and the
complaint waits for an officer to listen and type it in, so voice complaints always work.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

import yaml

from .common.ai_profiles import expand, load_dotenv


def speech_profiles(root):
    load_dotenv(root)
    path = os.path.join(root, "config", "ai_providers.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = (yaml.safe_load(f) or {}).get("speech", {}) or {}
    return {n: {k: expand(v) for k, v in (p or {}).items()} for n, p in raw.items()}


def speech_status(p):
    if not p.get("base_url"):
        return False, "No address set."
    if not p.get("model"):
        return False, "No model set."
    if not str(p["base_url"]).startswith("http://") and not os.environ.get(p.get("api_key_env") or "", ""):
        return False, f"{p.get('api_key_env') or 'API key'} is not set in .env."
    return True, f"{p.get('model')}"


def ready_speech(root):
    return [(n, p) for n, p in speech_profiles(root).items() if speech_status(p)[0]]


def transcribe(profile, audio: bytes, filename="complaint.wav", timeout=120):
    """Returns the transcript text. Raises RuntimeError on any failure."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("model", profile["model"]), ("response_format", "json")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    key = os.environ.get(profile.get("api_key_env") or "", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = str(profile["base_url"]).rstrip("/") + "/audio/transcriptions"
    req = urllib.request.Request(url, data=b"".join(parts), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(profile.get("timeout", timeout))) as r:
            text = (json.loads(r.read().decode()).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from speech service")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"Speech service not reachable: {e}")
    if not text:
        raise RuntimeError("Speech service returned no text")
    return text
