"""Logic behind the portal screens (no Streamlit here, so it can be tested).

Citizen side: screen text per language, submitting a typed or spoken complaint, the acknowledgement slip,
tracking status. Officer side: the work queue, transcribing voice complaints, forward / escalate / reply,
registers for download, and system settings.
"""
import csv
import datetime as dt
import html
import io
import json
import os
import threading

import yaml

from .__main__ import ROOT
from .common.audit import AuditLog
from .common.config import load_config
from .common.pii import mask

DATA = os.path.join(ROOT, "data", "grievance")
STORE_DIR = os.path.join(ROOT, "reports", "portal")
VOICE_DIR = os.path.join(STORE_DIR, "voice")
SETTINGS_PATH = os.path.join(STORE_DIR, "settings.json")
_LOCK = threading.Lock()


# ------------------------------------------------------------------ screen text
class Text:
    def __init__(self):
        with open(os.path.join(DATA, "ui_strings.yaml"), encoding="utf-8") as f:
            d = yaml.safe_load(f)
        self.names, self.fallback, self.strings = d["languages"], d.get("fallback", {}), d["strings"]

    def __call__(self, lang, key):
        lang = self.fallback.get(lang, lang)
        return self.strings.get(lang, {}).get(key) or self.strings["en"].get(key, key)


def places():
    """{block: [(village, village_local), ...]} for the location dropdowns."""
    out = {}
    with open(os.path.join(DATA, "villages.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["block"], []).append((r["village"], r.get("village_local") or r["village"]))
    return out


DISTRICT = "Demo District"


# ------------------------------------------------------------------ settings (officer page)
def settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"engine": "rules", "speech": ""}


def save_settings(s):
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f)


# ------------------------------------------------------------------ complaint register
class Store:
    """All complaints in one JSON file (fine for a district demo; a database in production)."""

    def __init__(self, path=os.path.join(STORE_DIR, "complaints.json")):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def next_ticket(self, today=None):
        today = today or dt.date.today()
        d = self._read()
        n = sum(1 for t in d if t.startswith(f"GRV/{today.year}/")) + 1
        return f"GRV/{today.year}/{n:05d}"

    def add(self, rec):
        with _LOCK:
            d = self._read()
            d[rec["ticket"]] = rec
            self._write(d)

    def get(self, ticket):
        return self._read().get((ticket or "").strip().upper())

    def update(self, ticket, event, **changes):
        with _LOCK:
            d = self._read()
            rec = d[ticket]
            rec.update(changes)
            rec.setdefault("history", []).append({"time": dt.datetime.now().isoformat(timespec="seconds"), **event})
            self._write(d)
            return rec

    def all(self):
        return sorted(self._read().values(), key=lambda r: r["created"], reverse=True)


def _suggestion(out):
    return {k: out.get(k) for k in ("language", "language_name", "department", "department_name", "priority",
                                    "sensitive_categories", "flags", "human_required", "assigned_to", "confidence",
                                    "reasons", "auto_reply", "draft_reply", "engine")}


def submit(router, store, lang, channel, text=None, audio=None, audio_name="complaint.wav", block="", village="",
           mobile="", speech_profile=None, today=None):
    """Registers a complaint. Voice: the recording is kept; if a speech service is ready it is transcribed,
    otherwise the complaint waits for an officer to transcribe it. Returns the stored record."""
    now = dt.datetime.now() if today is None else dt.datetime.combine(today, dt.time(10, 0))
    ticket = store.next_ticket(now.date())
    rec = {"ticket": ticket, "created": now.isoformat(timespec="seconds"), "ui_language": lang, "channel": channel,
           "district": DISTRICT, "block": block, "village": village,
           "mobile": ("XXXXXX" + mobile.strip()[-4:]) if mobile and mobile.strip() else "",
           "text": "", "transcript": "none", "audio": "", "status": "registered", "department": None,
           "reply": "", "suggestion": None, "history": []}
    if channel == "voice" and audio:
        os.makedirs(VOICE_DIR, exist_ok=True)
        ext = os.path.splitext(audio_name)[1] or ".wav"
        path = os.path.join(VOICE_DIR, ticket.replace("/", "_") + ext)
        with open(path, "wb") as f:
            f.write(audio)
        rec["audio"] = os.path.relpath(path, ROOT)
        rec["transcript"] = "pending"
        if speech_profile:
            from .speech import transcribe
            try:
                text = transcribe(speech_profile, audio, audio_name)
                rec["transcript"] = "automatic"
            except RuntimeError as e:
                rec["history"].append({"time": rec["created"], "action": "transcription_failed", "detail": str(e)})
    if text:
        out = router.process({"id": ticket, "ticket": ticket, "text": text})
        rec["text"] = mask(text)
        rec["suggestion"] = _suggestion(out)
        rec["department"] = out.get("department")
        rec["status"] = "review" if out.get("human_required") else "forwarded"
    else:
        rec["status"] = "review"                      # voice waiting for transcription
    rec["history"].append({"time": rec["created"], "action": "registered", "by": "citizen"})
    store.add(rec)
    return rec


# ------------------------------------------------------------------ citizen: status and receipt
def citizen_status(rec, T, lang, depts):
    """(status text, detail lines) for the Track page."""
    st = rec["status"]
    if st == "resolved":
        return T(lang, "st_resolved"), [f"{T(lang, 'officer_reply')}: {rec.get('reply', '')}"]
    if st == "escalated":
        return T(lang, "st_escalated"), []
    if st == "forwarded" and rec.get("department"):
        return T(lang, "st_forwarded"), [depts[rec["department"]]["name"]]
    if st == "review":
        return T(lang, "st_review"), []
    return T(lang, "st_registered"), []


def receipt_html(rec, T, lang, depts, village_label=""):
    """Printable acknowledgement slip (HTML renders every Indian script; print or save as PDF from the browser)."""
    e = html.escape
    rtl = ' dir="rtl"' if lang == "ur" else ""
    dept = depts.get(rec.get("department") or "", {})
    created = rec["created"].replace("T", " ")[:16]
    rows = [(T(lang, "ticket"), rec["ticket"]), ("Date", created),
            (T(lang, "district"), rec["district"]), (T(lang, "block"), rec.get("block", "")),
            (T(lang, "village"), village_label or rec.get("village", ""))]
    if dept:
        rows.append(("Department", f"{dept['name']} ({dept['sla_days']} days)"))
    body = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in rows if v)
    text = rec.get("text") or ("Voice complaint" if rec["channel"] == "voice" else "")
    return f"""<!doctype html><html lang="{e(lang)}"{rtl}><head><meta charset="utf-8"><title>{e(rec['ticket'])}</title>
<style>body{{font-family:'Noto Sans','Nirmala UI',Arial,sans-serif;max-width:640px;margin:24px auto;color:#222}}
h1{{font-size:20px;color:#1f3a5f;margin:0}} h2{{font-size:15px;margin:2px 0 16px;color:#555}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #bbb;padding:6px 8px;text-align:start}}
th{{background:#f0f0f0;width:40%}} .box{{border:1px solid #bbb;padding:8px;margin-top:12px}}</style></head><body>
<h1>{e(T(lang, 'title'))}</h1><h2>{e(T(lang, 'subtitle'))}</h2>
<p><b>{e(T(lang, 'ack_title'))}</b></p><table>{body}</table>
<div class="box">{e(text)}</div>
<p>{e(T(lang, 'keep_number'))}</p><p>{e(T(lang, 'emergency'))}</p></body></html>"""


# ------------------------------------------------------------------ officer actions
ACTIONS = {"Forward to department": "forward", "Escalate to senior officer": "escalate",
           "Send reply and close": "reply"}
PLACEHOLDER = "[Officer: add the action taken"


def queue(store, show="Needs action"):
    recs = store.all()
    if show == "Needs action":
        recs = [r for r in recs if r["status"] in ("review", "forwarded")]
    pr = {"critical": 0, "high": 1, "normal": 2}
    def key(r):
        s = r.get("suggestion") or {}
        return (r["transcript"] != "pending" and not s.get("sensitive_categories"),
                pr.get(s.get("priority"), 3), r["created"])
    return sorted(recs, key=key)


def queue_rows(recs, depts):
    rows = []
    for r in recs:
        s = r.get("suggestion") or {}
        rows.append({"Complaint no.": r["ticket"], "Received": r["created"].replace("T", " ")[:16],
                     "Channel": "Voice" if r["channel"] == "voice" else "Typed",
                     "Language": s.get("language_name") or "-", "Block / village": f"{r.get('block', '')} / {r.get('village', '')}",
                     "Department": depts.get(r.get("department") or "", {}).get("name", "Not assigned"),
                     "Priority": (s.get("priority") or "-").capitalize(),
                     "Sensitive": "Yes" if s.get("sensitive_categories") else "",
                     "Needs typing": "Yes" if r["transcript"] == "pending" else "",
                     "Status": STATUS_EN.get(r["status"], r["status"])})
    return rows


STATUS_EN = {"registered": "Registered", "review": "Under review", "forwarded": "Sent to department",
             "escalated": "Escalated", "resolved": "Replied and closed"}


def save_transcript(router, store, ticket, text, officer):
    if not (officer or "").strip():
        return None, ["Enter your name."]
    if not (text or "").strip():
        return None, ["Type what the citizen said."]
    out = router.process({"id": ticket, "ticket": ticket, "text": text})
    rec = store.update(ticket, {"action": "transcribed", "by": officer}, text=mask(text), transcript="by officer",
                       suggestion=_suggestion(out), department=out.get("department"), status="review")
    return rec, []


def act(store, rec, action, officer, department=None, note="", reply=""):
    s = rec.get("suggestion") or {}
    errors = []
    if not (officer or "").strip():
        errors.append("Enter your name.")
    if rec["transcript"] == "pending":
        errors.append("Type the voice complaint first.")
    if action not in ACTIONS.values():
        errors.append("Choose an action.")
    if action == "reply":
        if s.get("sensitive_categories"):
            errors.append("Sensitive complaints cannot be closed from this desk. Escalate to the designated officer.")
        if not reply.strip():
            errors.append("Write the reply to the citizen.")
        if PLACEHOLDER in reply:
            errors.append("Replace the placeholder in the draft with the action actually taken.")
    if action == "forward" and not department:
        errors.append("Choose the department.")
    if action in ("forward", "escalate") and not note.strip():
        errors.append("Add a short note for the record.")
    if errors:
        return None, errors
    ev = {"action": action, "by": officer, "note": mask(note)}
    if action == "forward":
        ev["department"] = department
        return store.update(rec["ticket"], ev, status="forwarded", department=department), []
    if action == "escalate":
        return store.update(rec["ticket"], ev, status="escalated"), []
    return store.update(rec["ticket"], ev, status="resolved", reply=mask(reply)), []


# ------------------------------------------------------------------ registers for download
def register_csv(store, depts):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Complaint no.", "Received", "Channel", "Screen language", "Detected language", "District", "Block",
                "Village", "Department", "Priority", "Sensitive", "Status", "Complaint (personal numbers hidden)", "Reply"])
    for r in store.all():
        s = r.get("suggestion") or {}
        w.writerow([r["ticket"], r["created"], r["channel"], r["ui_language"], s.get("language_name", ""), r["district"],
                    r.get("block", ""), r.get("village", ""), depts.get(r.get("department") or "", {}).get("name", ""),
                    s.get("priority", ""), "yes" if s.get("sensitive_categories") else "", STATUS_EN.get(r["status"], r["status"]),
                    r.get("text", ""), r.get("reply", "")])
    return "\ufeff" + buf.getvalue()       # BOM so Excel opens Indian scripts correctly


def actions_csv(store):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Complaint no.", "Time", "Action", "By", "Department", "Note"])
    for r in store.all():
        for h in r.get("history", []):
            w.writerow([r["ticket"], h.get("time", ""), h.get("action", ""), h.get("by", ""), h.get("department", ""),
                        h.get("note", h.get("detail", ""))])
    return "\ufeff" + buf.getvalue()


def summary(store):
    recs = store.all()
    open_ = [r for r in recs if r["status"] not in ("resolved",)]
    return {"Total complaints": len(recs), "Open": len(open_), "Closed": len(recs) - len(open_),
            "Sensitive (open)": sum(1 for r in open_ if (r.get("suggestion") or {}).get("sensitive_categories")),
            "Voice": sum(1 for r in recs if r["channel"] == "voice")}


def live_rows_for_dashboard(store):
    """Portal complaints in the same shape as the early-warning data, so the district dashboard includes them."""
    rows = []
    for r in store.all():
        if r.get("text") and r.get("block") and r.get("village"):
            rows.append({"complaint_id": r["ticket"], "date": r["created"][:10], "district": r["district"],
                         "block": r["block"], "village": r["village"], "channel": r["channel"],
                         "language_group": (r.get("suggestion") or {}).get("language") or "",
                         "written_as": "", "text": r["text"], "true_issue": "", "scenario": "portal"})
    return rows
