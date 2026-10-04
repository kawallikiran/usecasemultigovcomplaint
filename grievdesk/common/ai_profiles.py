"""Named AI services from config/ai_providers.yaml.

Each profile says which API style to use (openai / anthropic / gemini), the model, the endpoint and the NAME of
the environment variable holding the key. Values may use ${VAR} or ${VAR:-default}, so models and URLs can be
changed from .env without editing YAML. Keys are never stored in the file.
"""
import copy
import os
import re
import time

import yaml

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
_CACHE = {}


def expand(value):
    if not isinstance(value, str):
        return value
    return _VAR.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value).strip()


def load_dotenv(root):
    """Read .env when running without Docker (Docker passes it in already). Existing variables win."""
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0].strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)


def load_profiles(root):
    load_dotenv(root)
    path = os.path.join(root, "config", "ai_providers.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = (yaml.safe_load(f) or {}).get("providers", {}) or {}
    out = {}
    for name, p in raw.items():
        p = {k: expand(v) for k, v in (p or {}).items()}
        p.setdefault("label", name)
        out[name] = p
    # Backwards compatible: a single service set directly with AI_PROVIDER / AI_MODEL / AI_API_KEY in .env
    if os.environ.get("AI_PROVIDER") and os.environ.get("AI_MODEL"):
        out["env"] = {"label": f"From .env ({os.environ['AI_PROVIDER']} / {os.environ['AI_MODEL']})",
                      "provider": os.environ["AI_PROVIDER"], "model": os.environ["AI_MODEL"],
                      "base_url": os.environ.get("AI_BASE_URL", ""), "api_key_env": "AI_API_KEY"}
    return out


def apply_profile(cfg, profile):
    """Copy of an engine config with the profile's settings in the ai block (and optional critic limits)."""
    c = copy.deepcopy(cfg)
    ai = dict(c.get("ai") or {})
    for k in ("provider", "model", "base_url", "api_key_env", "timeout", "max_tokens", "temperature", "json_mode"):
        if profile.get(k) not in (None, ""):
            ai[k] = profile[k]
    c["ai"] = ai
    crit = dict(c.get("critic") or {})
    for k_src, k_dst in (("critic_max_cases", "max_cases"), ("critic_consistency_runs", "consistency_runs")):
        if profile.get(k_src):
            crit[k_dst] = int(profile[k_src])
    c["critic"] = crit
    return c


def profile_status(profile, cfg_template=None):
    """(ready, message). Local servers are probed (cached for 20 s) so the UI only offers working services."""
    from .ai_client import ai_status
    cfg = apply_profile(cfg_template or {}, profile)
    ok, msg = ai_status(cfg)
    if not ok:
        return ok, msg
    base = str(profile.get("base_url") or "")
    if base.startswith("http://"):
        return probe_local(base, profile.get("model"))
    return True, msg


def probe_local(base, model):
    key = (base, model)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/models", timeout=2) as r:
            ids = [m.get("id", "") for m in json.loads(r.read().decode()).get("data", [])]
        if model and ids and not any(i == model or i.split(":")[0] == model for i in ids):
            res = (False, f"Local server is up but model '{model}' is not downloaded yet (still pulling?).")
        else:
            res = (True, f"local / {model}")
    except Exception:
        res = (False, f"Local model server not reachable at {base}.")
    _CACHE[key] = (time.time(), res)
    return res


def ready_profiles(root, cfg_template=None):
    """[(name, label, message)] for every profile that can be used right now."""
    out = []
    for name, p in load_profiles(root).items():
        ok, msg = profile_status(p, cfg_template)
        if ok:
            out.append((name, p["label"], msg))
    return out


def all_status(root, cfg_template=None):
    return [(n, p["label"], *profile_status(p, cfg_template)) for n, p in load_profiles(root).items()]
