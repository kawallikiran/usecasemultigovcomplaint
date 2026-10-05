"""Logic behind the portal screens (no Streamlit here, so it can be tested).

Citizen side: screen text, frequently filed complaints, filing (typed or spoken), acknowledgement, tracking.
Officer side: assignment to a named officer, human review (approve, edit, reassign, escalate), notifications,
registers and audit trail for download.
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
from .common.pii import mask
from .notify import Outbox
from .locations import zone_of
from .taxonomy import categorize, category_name

DATA = os.path.join(ROOT, "data", "grievance")
STORE_DIR = os.path.join(ROOT, "reports", "portal")
VOICE_DIR = os.path.join(STORE_DIR, "voice")
SETTINGS_PATH = os.path.join(STORE_DIR, "settings.json")
_LOCK = threading.Lock()
SLA_DEFAULT = 7
NOTICE_VERSION = "2026-10"          # version of the privacy notice shown at filing
RUBBER_STAMP_SECONDS = 30           # approvals faster than this are counted as possibly not reviewed
SENSITIVE_OFFICER, CELL_OFFICER, SENIOR_OFFICER = "OFF-SC01", "OFF-GC01", "OFF-SR01"


# ------------------------------------------------------------------ reference data
class Text:
    def __init__(self):
        with open(os.path.join(DATA, "ui_strings.yaml"), encoding="utf-8") as f:
            d = yaml.safe_load(f)
        self.names, self.fallback, self.strings = d["languages"], d.get("fallback", {}), d["strings"]

    def __call__(self, lang, key):
        lang = self.fallback.get(lang, lang)
        return self.strings.get(lang, {}).get(key) or self.strings["en"].get(key, key)


def common_complaints(lang):
    with open(os.path.join(DATA, "common_complaints.yaml"), encoding="utf-8") as f:
        d = yaml.safe_load(f)["complaints"]
    return d.get(lang) or d["en"]


def places():
    """{state: {district: [city/town, ...]}} from the Census 2011 towns list (current state boundaries)."""
    from .locations import tree
    return tree()


DEFAULT_STATE = {"ta": "Tamil Nadu", "te": "Telangana", "kn": "Karnataka", "ml": "Kerala", "bn": "West Bengal",
                 "as": "Assam", "or": "Odisha", "gu": "Gujarat", "mr": "Maharashtra", "pa": "Punjab",
                 "ur": "Jammu & Kashmir", "hi": "Chhattisgarh", "cg": "Chhattisgarh", "en": "Chhattisgarh"}


def officers():
    with open(os.path.join(DATA, "officers.csv"), encoding="utf-8") as f:
        return {r["officer_id"]: r for r in csv.DictReader(f)}


def officer_for(department):
    for oid, o in officers().items():
        if o["handles"] == department:
            return oid
    return CELL_OFFICER


def officer_label(oid):
    o = officers().get(oid)
    return f"{o['designation']} ({o['name']})" if o else ""


def settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    return {"engine": "rules", "speech": "", "retention_days": 90, **s}


def save_settings(s):
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f)


# ------------------------------------------------------------------ storage
class _JsonFile:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def write(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class Store:
    """Complaint register. Contact details are kept in a separate file, read only to send messages."""

    def __init__(self, path=os.path.join(STORE_DIR, "complaints.json")):
        self.db = _JsonFile(path)
        self.contacts = _JsonFile(os.path.join(os.path.dirname(path), "contacts.json"))
        self.outbox = Outbox(os.path.join(os.path.dirname(path), "outbox.json"))

    def next_ticket(self, today):
        n = sum(1 for t in self.db.read() if t.startswith(f"GRV/{today.year}/")) + 1
        return f"GRV/{today.year}/{n:05d}"

    def add(self, rec, contact):
        with _LOCK:
            d = self.db.read()
            d[rec["ticket"]] = rec
            self.db.write(d)
            c = self.contacts.read()
            c[rec["ticket"]] = contact
            self.contacts.write(c)

    def get(self, ticket):
        return self.db.read().get((ticket or "").strip().upper())

    def contact(self, ticket):
        return self.contacts.read().get(ticket, {})

    def update(self, ticket, event, **changes):
        with _LOCK:
            d = self.db.read()
            rec = d[ticket]
            rec.update(changes)
            rec.setdefault("history", []).append({"time": dt.datetime.now().isoformat(timespec="seconds"), **event})
            self.db.write(d)
            return rec

    def all(self):
        return sorted(self.db.read().values(), key=lambda r: r["created"], reverse=True)


# ------------------------------------------------------------------ filing
def _suggestion(out):
    return {k: out.get(k) for k in ("language", "language_name", "department", "department_name", "priority",
                                    "sensitive_categories", "flags", "human_required", "confidence", "reasons",
                                    "draft_reply", "engine")}


def _assign(out, pending_voice):
    if pending_voice or not out:
        return CELL_OFFICER
    if out.get("sensitive_categories"):
        return SENSITIVE_OFFICER
    if out.get("department") and not out.get("human_required"):
        return officer_for(out["department"])
    return CELL_OFFICER


def _due(created, department, depts):
    days = depts.get(department or "", {}).get("sla_days", SLA_DEFAULT)
    return (dt.date.fromisoformat(created[:10]) + dt.timedelta(days=int(days))).isoformat()


def _notify(store, rec, kind, T, depts, reply=""):
    c = store.contact(rec["ticket"])
    lang = rec["ui_language"]
    office = depts.get(rec.get("department") or "", {}).get("name") or T(lang, "to_be_assigned")
    date = dt.date.fromisoformat(rec["due_date"]).strftime("%d-%m-%Y")
    msg = T(lang, {"registered": "msg_registered", "approved": "msg_resolved", "escalated": "msg_escalated"}[kind]).format(
        ticket=rec["ticket"], office=office, date=date, reply=reply)
    channel = c.get("notify_by", "portal")
    to = c.get("email") if channel == "email" else c.get("mobile")
    return store.outbox.send(rec["ticket"], channel, to, msg, kind)


def submit(router, store, T, lang, channel, text=None, audio=None, audio_name="complaint.wav", state="", district="",
           town="", locality="", notify_by="portal", mobile="", email="", speech_profile=None, template_category=None, now=None,
           consent=True):
    """Registers a complaint, assigns it to an officer for review, and sends the acknowledgement."""
    now = now or dt.datetime.now()
    ticket = store.next_ticket(now.date())
    rec = {"ticket": ticket, "created": now.isoformat(timespec="seconds"), "ui_language": lang, "channel": channel,
           "zone": zone_of(state), "state": state, "district": district, "town": town,
           "locality": mask((locality or "").strip())[:120], "text": "",
           "transcript": "none", "audio": "", "status": "pending_review", "department": None, "category": "other",
           "priority": "normal", "assigned_to": CELL_OFFICER, "reply": "", "suggestion": None, "history": [],
           "notify_by": notify_by}
    if channel == "voice" and audio:
        os.makedirs(VOICE_DIR, exist_ok=True)
        ext = os.path.splitext(audio_name)[1] or ".wav"
        path = os.path.join(VOICE_DIR, ticket.replace("/", "_") + ext)
        with open(path, "wb") as f:
            f.write(audio)
        rec["audio"], rec["transcript"] = os.path.relpath(path, ROOT), "pending"
        if speech_profile:
            from .speech import transcribe
            try:
                text = transcribe(speech_profile, audio, audio_name)
                rec["transcript"] = "automatic"
            except RuntimeError as e:
                rec["history"].append({"time": rec["created"], "action": "transcription failed", "note": str(e)})
    out = None
    if text:
        out = router.process({"id": ticket, "ticket": ticket, "text": text})
        rec["text"], rec["suggestion"] = mask(text), _suggestion(out)
        rec["department"], rec["priority"] = out.get("department"), out.get("priority") or "normal"
        rec["category"] = template_category if template_category else categorize(rec["department"], text)
    rec["assigned_to"] = _assign(out, rec["transcript"] == "pending")
    rec["due_date"] = _due(rec["created"], rec["department"], router.depts)
    rec["consent"] = {"given": bool(consent), "time": rec["created"], "notice_version": NOTICE_VERSION}
    rec["history"].append({"time": rec["created"], "action": "registered", "by": "citizen",
                           "assigned_to": rec["assigned_to"]})
    store.add(rec, {"notify_by": notify_by, "mobile": (mobile or "").strip(), "email": (email or "").strip()})
    _notify(store, rec, "registered", T, router.depts)
    return rec


# ------------------------------------------------------------------ citizen view
STAGE_KEY = {"registered": "st_registered", "assigned": "st_review", "reassigned": "st_forwarded",
             "transcribed": "st_review", "escalated": "st_escalated", "approved": "st_resolved",
             "appealed": "st_appealed", "auto-escalated (overdue)": "st_escalated"}


def citizen_view(rec, T, lang, depts):
    st = rec["status"]
    status = T(lang, {"approved": "st_resolved", "escalated": "st_escalated", "appealed": "st_appealed"}.get(st, "st_review"))
    timeline = []
    for h in rec.get("history", []):
        key = STAGE_KEY.get(h.get("action"))
        if key:
            timeline.append((h["time"][:10], T(lang, key)))
    return {"status": status,
            "department": depts.get(rec.get("department") or "", {}).get("name") or T(lang, "to_be_assigned"),
            "officer": officers().get(rec.get("assigned_to"), {}).get("designation", ""),
            "expected": dt.date.fromisoformat(rec["due_date"]).strftime("%d-%m-%Y"),
            "reply": rec.get("reply") if st == "approved" else "", "timeline": timeline,
            "can_rate": st == "approved" and not rec.get("rating")}


def receipt_html(rec, T, lang, depts):
    """Printable acknowledgement slip (HTML renders every Indian script; print or save as PDF from the browser)."""
    e = html.escape
    v = citizen_view(rec, T, lang, depts)
    rtl = ' dir="rtl"' if lang == "ur" else ""
    rows = [(T(lang, "ticket"), rec["ticket"]), ("Date", rec["created"].replace("T", " ")[:16]),
            (T(lang, "state"), rec.get("state", "")), (T(lang, "district"), rec["district"]),
            (T(lang, "town"), rec.get("town", "")), (T(lang, "locality"), rec.get("locality", "")),
            (T(lang, "dept_label"), v["department"]),
            (T(lang, "officer_label"), v["officer"]), (T(lang, "expected_by"), v["expected"]),
            (T(lang, "status"), v["status"])]
    body = "".join(f"<tr><th>{e(k)}</th><td>{e(str(val))}</td></tr>" for k, val in rows if val)
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


# ------------------------------------------------------------------ officer review
PLACEHOLDER = "[Officer: add the action taken"
STATUS_EN = {"pending_review": "Waiting for officer review", "escalated": "With senior officer",
             "appealed": "Appeal with senior officer", "approved": "Approved and citizen informed"}


def can_act(officer_id, rec):
    o = officers().get(officer_id, {})
    return o.get("role") in ("supervisor", "admin") or rec.get("assigned_to") == officer_id


def queue(store, officer_id, scope="mine", show="Needs action"):
    recs = store.all()
    if scope == "mine":
        recs = [r for r in recs if r.get("assigned_to") == officer_id]
    if show == "Needs action":
        recs = [r for r in recs if r["status"] in ("pending_review", "escalated", "appealed")]
    pr = {"critical": 0, "high": 1, "normal": 2}
    return sorted(recs, key=lambda r: (not ((r.get("suggestion") or {}).get("sensitive_categories") or r["transcript"] == "pending"),
                                       pr.get(r.get("priority"), 3), r["created"]))


def queue_rows(recs, depts):
    rows = []
    for r in recs:
        s = r.get("suggestion") or {}
        rows.append({"Complaint no.": r["ticket"], "Received": r["created"].replace("T", " ")[:16],
                     "Channel": "Voice" if r["channel"] == "voice" else "Typed",
                     "Language": s.get("language_name") or "-",
                     "Place": ", ".join(x for x in (r.get("locality"), r.get("town"), r.get("district"), r.get("state")) if x),
                     "Department": depts.get(r.get("department") or "", {}).get("name", "Not assigned"),
                     "Category": category_name(r.get("department"), r.get("category")),
                     "Priority": (r.get("priority") or "-").capitalize(),
                     "Sensitive": "Yes" if s.get("sensitive_categories") else "",
                     "Assigned to": officer_label(r.get("assigned_to")),
                     "Due": dt.date.fromisoformat(r["due_date"]).strftime("%d-%m-%Y"),
                     "Status": STATUS_EN.get(r["status"], r["status"])})
    return rows


def save_transcript(router, store, ticket, text, officer_id):
    if not (text or "").strip():
        return None, ["Type what the citizen said."]
    out = router.process({"id": ticket, "ticket": ticket, "text": text})
    rec = store.get(ticket)
    dept = out.get("department")
    new_owner = _assign(out, False)
    rec = store.update(ticket, {"action": "transcribed", "by": officer_id, "assigned_to": new_owner},
                       text=mask(text), transcript="by officer", suggestion=_suggestion(out), department=dept,
                       category=categorize(dept, text), priority=out.get("priority") or "normal",
                       assigned_to=new_owner, due_date=_due(rec["created"], dept, router.depts))
    return rec, []


def approve(store, T, depts, rec, officer_id, reply, category=None, priority=None, review_seconds=None):
    errors = _common_errors(officer_id, rec)
    s = rec.get("suggestion") or {}
    if s.get("sensitive_categories") and officer_id not in (SENSITIVE_OFFICER, SENIOR_OFFICER) \
            and officers().get(officer_id, {}).get("role") != "admin":
        errors.append("Sensitive complaints can only be approved by the designated or senior officer.")
    if not (reply or "").strip():
        errors.append("Write the reply to the citizen.")
    if PLACEHOLDER in (reply or ""):
        errors.append("Replace the placeholder in the draft with the action actually taken.")
    if errors:
        return None, errors
    edits = _edits(rec, category, priority)
    ev = {"action": "approved", "by": officer_id, **edits}
    if review_seconds is not None:
        ev["review_seconds"] = int(review_seconds)
    if rec["status"] == "appealed":
        ev["appeal_decided"] = True
    new = store.update(rec["ticket"], ev, status="approved",
                       reply=mask(reply), **{k: v for k, v in edits.items() if k in ("category", "priority")})
    note = _notify(store, new, "approved", T, depts, reply=mask(reply))
    return new, [], note


def reassign(store, router, rec, officer_id, department, note, category=None, priority=None):
    errors = _common_errors(officer_id, rec)
    if not department or department == rec.get("department"):
        errors.append("Choose a different department to reassign to.")
    if not (note or "").strip():
        errors.append("Add a short note explaining the reassignment.")
    if errors:
        return None, errors
    owner = officer_for(department)
    cat = category if category and category != rec.get("category") else categorize(department, rec.get("text", ""))
    new = store.update(rec["ticket"], {"action": "reassigned", "by": officer_id, "department": department,
                                       "assigned_to": owner, "note": mask(note)},
                       department=department, assigned_to=owner, category=cat, priority=priority or rec["priority"],
                       status="pending_review", due_date=_due(rec["created"], department, router.depts))
    return new, []


def escalate(store, T, depts, rec, officer_id, note):
    errors = _common_errors(officer_id, rec)
    if not (note or "").strip():
        errors.append("Add a short note explaining why it is escalated.")
    if errors:
        return None, errors
    new = store.update(rec["ticket"], {"action": "escalated", "by": officer_id, "assigned_to": SENIOR_OFFICER,
                                       "note": mask(note)}, status="escalated", assigned_to=SENIOR_OFFICER)
    _notify(store, new, "escalated", T, depts)
    return new, []


def _common_errors(officer_id, rec):
    e = []
    if not can_act(officer_id, rec):
        e.append("This complaint is assigned to another officer.")
    if rec["transcript"] == "pending":
        e.append("Type the voice complaint first.")
    if rec["status"] == "approved":
        e.append("This complaint is already approved and closed.")
    return e


def _edits(rec, category, priority):
    out = {}
    if category and category != rec.get("category"):
        out["category"] = category
    if priority and priority != rec.get("priority"):
        out["priority"] = priority
    return out


# ------------------------------------------------------------------ registers and figures
def register_csv(store, depts):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Complaint no.", "Received", "Channel", "Screen language", "Detected language", "Zone", "State", "District",
                "City / town", "Village or locality", "Department", "Category", "Priority", "Sensitive", "Assigned to", "Due date",
                "Status", "Informed by", "Complaint (personal numbers hidden)", "Reply"])
    for r in store.all():
        s = r.get("suggestion") or {}
        w.writerow([r["ticket"], r["created"], r["channel"], r["ui_language"], s.get("language_name", ""), r.get("zone", ""), r.get("state", ""),
                    r["district"], r.get("town", ""), r.get("locality", ""),
                    depts.get(r.get("department") or "", {}).get("name", ""), category_name(r.get("department"), r.get("category")),
                    r.get("priority", ""), "yes" if s.get("sensitive_categories") else "", officer_label(r.get("assigned_to")),
                    r.get("due_date", ""), STATUS_EN.get(r["status"], r["status"]), r.get("notify_by", ""),
                    r.get("text", ""), r.get("reply", "")])
    return "\ufeff" + buf.getvalue()


def audit_rows(store):
    rows = []
    for r in store.all():
        s = r.get("suggestion") or {}
        for h in r.get("history", []):
            rows.append({"Time": h.get("time", "").replace("T", " "), "Complaint no.": r["ticket"],
                         "Action": h.get("action", ""), "By": officer_label(h.get("by")) or h.get("by", ""),
                         "Assigned to": officer_label(h.get("assigned_to")),
                         "Department": h.get("department", ""), "Category": h.get("category", ""),
                         "Priority": h.get("priority", ""), "Note": h.get("note", ""),
                         "Suggested by system": s.get("department_name") or "" if h.get("action") == "registered" else ""})
    return sorted(rows, key=lambda x: x["Time"], reverse=True)


def audit_csv(store):
    rows = audit_rows(store)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return "\ufeff" + buf.getvalue()


def summary(store):
    recs = store.all()
    today = dt.date.today().isoformat()
    open_ = [r for r in recs if r["status"] != "approved"]
    return {"Received": len(recs), "Waiting for review": sum(r["status"] == "pending_review" for r in recs),
            "With senior officer": sum(r["status"] == "escalated" for r in recs),
            "Approved": sum(r["status"] == "approved" for r in recs),
            "Overdue": sum(1 for r in open_ if r.get("due_date", "9999") < today)}


def review_agreement(store):
    """How often officers kept the system's suggested department (only complaints a person has approved)."""
    done = [r for r in store.all() if r["status"] == "approved" and r.get("suggestion")]
    if not done:
        return None
    kept = sum(1 for r in done if (r["suggestion"] or {}).get("department") == r.get("department"))
    return {"approved": len(done), "kept": kept, "changed": len(done) - kept}


def portal_rows_for_analytics(store):
    """Portal complaints in the same shape as the district data."""
    rows = []
    for r in store.all():
        if not r.get("district"):
            continue
        rows.append({"complaint_id": r["ticket"], "date": r["created"][:10], "zone": r.get("zone", ""),
                     "state": r.get("state", ""), "district": r["district"], "town": r.get("town", ""),
                     "channel": r["channel"], "language_group": (r.get("suggestion") or {}).get("language") or "",
                     "text": r.get("text", ""), "true_issue": "", "scenario": "portal",
                     "department": r.get("department") or "", "category": r.get("category", ""),
                     "officer_id": r.get("assigned_to", ""),
                     "status": "Resolved" if r["status"] == "approved" else "Pending",
                     "resolved_date": next((h["time"][:10] for h in r.get("history", []) if h.get("action") == "approved"), ""),
                     "days_to_resolve": "", "due_date": r.get("due_date", "")})
    return rows


# ------------------------------------------------------------------ citizen rating and appeal
def rate(store, rec, satisfied, reason=""):
    """After a reply: the citizen says whether they are satisfied. 'No' sends an appeal to the senior officer."""
    if rec["status"] != "approved" or rec.get("rating"):
        return None, ["Rating is only possible once, after a reply."]
    if satisfied:
        return store.update(rec["ticket"], {"action": "rated satisfied", "by": "citizen"}, rating="satisfied"), []
    if not (reason or "").strip():
        return None, ["reason"]
    return store.update(rec["ticket"], {"action": "appealed", "by": "citizen", "note": mask(reason)[:500],
                                        "assigned_to": SENIOR_OFFICER},
                        rating="not satisfied", status="appealed", assigned_to=SENIOR_OFFICER), []


# ------------------------------------------------------------------ overdue complaints escalate themselves
def auto_escalate_overdue(store, T, depts, today=None):
    """Complaints waiting for review past their due date go to the senior officer, and the citizen is told."""
    today = (today or dt.date.today()).isoformat()
    moved = []
    for r in store.all():
        if r["status"] == "pending_review" and r.get("due_date", "9999") < today and r["transcript"] != "pending":
            new = store.update(r["ticket"], {"action": "auto-escalated (overdue)", "by": "system",
                                             "assigned_to": SENIOR_OFFICER, "note": f"due {r['due_date']}"},
                               status="escalated", assigned_to=SENIOR_OFFICER)
            _notify(store, new, "escalated", T, depts)
            moved.append(r["ticket"])
    return moved


# ------------------------------------------------------------------ citizen data requests and retention
def data_request(store, rec, kind, details=""):
    """kind: 'correct' or 'delete'. Recorded for an officer to act on; the citizen is told it is recorded."""
    if kind not in ("correct", "delete"):
        return None
    reqs = _requests(store)
    entry = {"id": f"DR{len(reqs.read().get('items', [])) + 1:04d}", "time": dt.datetime.now().isoformat(timespec="seconds"),
             "ticket": rec["ticket"], "kind": kind, "details": mask(details)[:300], "status": "open"}
    with _LOCK:
        d = reqs.read()
        d.setdefault("items", []).append(entry)
        reqs.write(d)
    store.update(rec["ticket"], {"action": f"data request ({kind})", "by": "citizen"})
    return entry


def _requests(store):
    return _JsonFile(os.path.join(os.path.dirname(store.db.path), "data_requests.json"))


def data_requests(store):
    return _requests(store).read().get("items", [])


def close_data_request(store, req_id, officer_id, note=""):
    with _LOCK:
        d = _requests(store).read()
        req = next((x for x in d.get("items", []) if x["id"] == req_id), None)
        if not req or req["status"] != "open":
            return None
        if req["kind"] == "delete":
            _erase_contact(store, req["ticket"], f"citizen request {req_id}")
            erased = True
        else:
            erased = False
        req.update(status="done", closed_by=officer_id, closed_at=dt.datetime.now().isoformat(timespec="seconds"),
                   note=mask(note)[:300])
        _requests(store).write(d)
    extra = {"contact_erased": dt.date.today().isoformat()} if erased else {}
    store.update(req["ticket"], {"action": f"data request {req_id} done", "by": officer_id, "note": mask(note)[:200]}, **extra)
    return req


def _erase_contact(store, ticket, why):
    c = store.contacts.read()
    if ticket in c:
        c[ticket] = {"notify_by": "portal", "erased": dt.date.today().isoformat(), "why": why}
        store.contacts.write(c)
    rec = store.get(ticket)
    if rec and rec.get("audio"):
        path = os.path.join(ROOT, rec["audio"])
        if os.path.exists(path):
            os.remove(path)


def apply_retention(store, days, today=None):
    """Deletes contact details and voice recordings of complaints closed more than `days` days ago."""
    today = today or dt.date.today()
    done = []
    for r in store.all():
        if r["status"] != "approved" or r.get("contact_erased"):
            continue
        closed = next((h["time"][:10] for h in reversed(r.get("history", [])) if h.get("action") == "approved"), None)
        if closed and (today - dt.date.fromisoformat(closed)).days > int(days):
            _erase_contact(store, r["ticket"], f"retention {days} days")
            store.update(r["ticket"], {"action": "contact details deleted (retention)", "by": "system"},
                         contact_erased=today.isoformat(), audio="")
            done.append(r["ticket"])
    return done


# ------------------------------------------------------------------ figures for the Assurance page
def assurance_figures(store, today=None):
    today = (today or dt.date.today()).isoformat()
    recs = store.all()
    approvals = [h for r in recs for h in r.get("history", []) if h.get("action") == "approved"]
    timed = [h["review_seconds"] for h in approvals if "review_seconds" in h]
    reassigned = [r for r in recs if any(h.get("action") == "reassigned" for h in r.get("history", []))]

    def days_added(r):
        first = next((h["time"] for h in r["history"] if h.get("action") == "reassigned"), None)
        last = next((h["time"] for h in reversed(r["history"]) if h.get("action") == "reassigned"), None)
        return 0 if not first else (dt.datetime.fromisoformat(last) - dt.datetime.fromisoformat(r["created"])).days

    rated = [r for r in recs if r.get("rating")]
    reqs = data_requests(store)
    return {
        "complaints": len(recs),
        "consent_share": round(100 * sum(1 for r in recs if (r.get("consent") or {}).get("given")) / len(recs)) if recs else None,
        "approvals": len(approvals),
        "quick_approvals": sum(1 for t in timed if t < RUBBER_STAMP_SECONDS),
        "median_review_seconds": sorted(timed)[len(timed) // 2] if timed else None,
        "reassigned": len(reassigned),
        "reassign_days": round(sum(days_added(r) for r in reassigned) / len(reassigned), 1) if reassigned else 0,
        "overdue_open": sum(1 for r in recs if r["status"] == "pending_review" and r.get("due_date", "9999") < today),
        "auto_escalated": sum(1 for r in recs if any(h.get("action") == "auto-escalated (overdue)" for h in r.get("history", []))),
        "appeals": sum(1 for r in recs if r.get("rating") == "not satisfied"),
        "satisfied_share": round(100 * sum(r["rating"] == "satisfied" for r in rated) / len(rated)) if rated else None,
        "open_data_requests": sum(1 for q in reqs if q["status"] == "open"),
        "data_requests": len(reqs),
    }


# ------------------------------------------------------------------ officer screens: cards and workload
def card(rec, depts, today=None):
    """Short summary of one complaint for the officer's list."""
    today = (today or dt.date.today()).isoformat()
    s = rec.get("suggestion") or {}
    text = rec.get("text") or ("Voice complaint, not yet typed" if rec.get("transcript") == "pending" else "")
    open_ = rec["status"] not in ("approved",)
    return {"ticket": rec["ticket"], "summary": text if len(text) <= 90 else text[:87].rstrip() + "...",
            "place": ", ".join(dict.fromkeys(x for x in (rec.get("town"), rec.get("district"), rec.get("state")) if x)),
            "department": depts.get(rec.get("department") or "", {}).get("name", "Department not set"),
            "due": dt.date.fromisoformat(rec["due_date"]).strftime("%d-%m-%Y"),
            "overdue": open_ and rec.get("due_date", "9999") < today,
            "status": STATUS_EN.get(rec["status"], rec["status"]),
            "assigned": officer_label(rec.get("assigned_to")),
            "sensitive": bool(s.get("sensitive_categories")),
            "voice": rec.get("channel") == "voice", "priority": rec.get("priority", "normal")}


def my_counts(store, officer_id, today=None):
    today = (today or dt.date.today()).isoformat()
    mine = [r for r in store.all() if r.get("assigned_to") == officer_id]
    open_ = [r for r in mine if r["status"] != "approved"]
    return {"Waiting for me": len(open_), "Overdue": sum(r.get("due_date", "9999") < today for r in open_),
            "Sensitive": sum(bool((r.get("suggestion") or {}).get("sensitive_categories")) for r in open_),
            "Approved by me": sum(1 for r in store.all() for h in r.get("history", [])
                                  if h.get("action") == "approved" and h.get("by") == officer_id)}


def workload(store, today=None):
    """One row per officer: what is with them now."""
    today = (today or dt.date.today()).isoformat()
    rows = []
    for oid, o in officers().items():
        if o["role"] == "admin":
            continue
        mine = [r for r in store.all() if r.get("assigned_to") == oid]
        open_ = [r for r in mine if r["status"] != "approved"]
        rows.append({"Officer": f"{o['name']}", "Designation": o["designation"], "Open": len(open_),
                     "Overdue": sum(r.get("due_date", "9999") < today for r in open_),
                     "Closed": len(mine) - len(open_)})
    return sorted(rows, key=lambda r: (-r["Overdue"], -r["Open"]))
