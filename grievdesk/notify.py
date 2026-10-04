"""Citizen notifications by SMS, WhatsApp, email or the portal itself.

Every message is recorded in an outbox (reports/portal/outbox.json). Real sending is switched on from .env:
  Email:     SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
  SMS:       SMS_GATEWAY_URL (+ SMS_GATEWAY_TOKEN)          -> POST {"to": ..., "message": ...}
  WhatsApp:  WHATSAPP_GATEWAY_URL (+ WHATSAPP_GATEWAY_TOKEN) -> POST {"to": ..., "message": ...}
The SMS and WhatsApp settings fit any provider that accepts a simple JSON POST (or a small relay in front of
the provider's own API). Without them, messages are recorded as "not sent: no gateway set up".
"""
import datetime as dt
import json
import os
import smtplib
import threading
import urllib.request
from email.message import EmailMessage

_LOCK = threading.Lock()


def gateways():
    return {"sms": bool(os.environ.get("SMS_GATEWAY_URL")), "whatsapp": bool(os.environ.get("WHATSAPP_GATEWAY_URL")),
            "email": bool(os.environ.get("SMTP_HOST")), "portal": True}


def _mask(channel, to):
    if not to:
        return ""
    if channel == "email" and "@" in to:
        name, dom = to.split("@", 1)
        return name[:2] + "***@" + dom
    return "XXXXXX" + to[-4:]


def _post(url, token, to, message):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps({"to": to, "message": message}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _send(channel, to, message, subject):
    if channel == "portal":
        return "shown on portal"
    if not to:
        return "not sent: no contact given"
    try:
        if channel == "email" and os.environ.get("SMTP_HOST"):
            m = EmailMessage()
            m["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "")
            m["To"], m["Subject"] = to, subject
            m.set_content(message)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(m)
            return "sent"
        url = os.environ.get(f"{channel.upper()}_GATEWAY_URL")
        if channel in ("sms", "whatsapp") and url:
            _post(url, os.environ.get(f"{channel.upper()}_GATEWAY_TOKEN", ""), to, message)
            return "sent"
        return "not sent: no gateway set up"
    except Exception as e:                       # a failed message never blocks the complaint
        return f"failed: {type(e).__name__}"


class Outbox:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def send(self, ticket, channel, to, message, kind, subject="Complaint update"):
        status = _send(channel, to, message, subject)
        entry = {"time": dt.datetime.now().isoformat(timespec="seconds"), "ticket": ticket, "kind": kind,
                 "channel": channel, "to": _mask(channel, to), "message": message, "status": status}
        with _LOCK:
            items = self.all()
            items.append(entry)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        return entry
