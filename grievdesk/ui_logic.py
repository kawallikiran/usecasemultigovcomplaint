"""Everything the Streamlit page needs, kept free of Streamlit so it can be unit-tested."""
import csv
import os
import uuid

from .__main__ import ROOT, run_suite
from .common.audit import AuditLog
from .common.config import load_config
from .common.decisions import DecisionStore

UI_DIR = os.path.join(ROOT, "reports", "ui")
MAIN_CONFIG = os.path.join(ROOT, "config", "grievance.yaml")
AI_CONFIG = os.path.join(ROOT, "config", "grievance_ai.yaml")
TEST_SETS = {
    "Main test set (40 grievances)": os.path.join(ROOT, "config", "grievance.yaml"),
    "Held-out phrasing set (16 grievances)": os.path.join(ROOT, "config", "grievance_challenge.yaml"),
    "AI engine on the main set (20 sampled)": AI_CONFIG,
    "AI engine on the held-out set (16)": os.path.join(ROOT, "config", "grievance_challenge_ai.yaml"),
}
DECISIONS = {"Approve and send reply": "approve", "Send to another department": "reroute",
             "Escalate to senior officer": "escalate"}
LANG_NAMES = {"en": "English", "hi": "Hindi", "cg": "Chhattisgarhi", "hinglish": "Hinglish",
              "mixed": "Mixed Hindi and English", "unsupported": "Not supported", "unknown": "Unknown"}
PLACEHOLDER = "[Officer: add the action taken"


def ai_ready():
    from .common.ai_client import ai_status
    return ai_status(load_config(AI_CONFIG))


def is_ai_set(cfg_path):
    return load_config(cfg_path).get("engine") == "ai"


def make_router(engine="rules"):
    """engine 'rules' (offline) or 'ai' (configured AI service; raises AIError if not configured)."""
    from .system import GrievanceRouter
    cfg = load_config(AI_CONFIG if engine == "ai" else MAIN_CONFIG)
    return GrievanceRouter(cfg, AuditLog(os.path.join(UI_DIR, "ai_outputs.jsonl")), engine=engine), cfg


def load_samples(cfg):
    with open(cfg["dataset"], encoding="utf-8") as f:
        return list(csv.DictReader(f))


def new_case(text):
    return {"id": f"UI-{uuid.uuid4().hex[:6]}", "text": text}


def handling(out):
    if out["sensitive_categories"]:
        return "sensitive"
    return "human" if out["human_required"] else "auto"


def summary_rows(out):
    goes = out["assigned_to"] + (" (human review required)" if out["human_required"]
                                 else " (auto-acknowledged; officer approves the reply)")
    return [
        {"Item": "Ticket", "Value": out["ticket"]},
        {"Item": "Engine", "Value": out.get("engine", "rules")},
        {"Item": "Language", "Value": f'{LANG_NAMES.get(out["language"], out["language"])} '
                                      f'(confidence {out["language_confidence"]})'},
        {"Item": "Department", "Value": f'{out["department_name"] or "Not identified"} '
                                        f'(confidence {out["route_confidence"]})'},
        {"Item": "Priority", "Value": out["priority"]},
        {"Item": "Sensitive content", "Value": ", ".join(out["sensitive_categories"]).replace("_", " ") or "None"},
        {"Item": "Checks raised", "Value": ", ".join(out["flags"]).replace("_", " ") or "None"},
        {"Item": "Goes to", "Value": goes},
    ]


def decision_options(out):
    """Sensitive cases cannot be approved from the desk."""
    opts = list(DECISIONS)
    return opts[1:] if out["sensitive_categories"] else opts


def validate_decision(decision, officer, note, reply, ai_handling, ai_department, new_department):
    errors = []
    if not (officer or "").strip():
        errors.append("Enter the officer name.")
    if decision not in DECISIONS.values():
        errors.append("Choose a decision.")
    if decision == "approve" and ai_handling == "sensitive":
        errors.append("Sensitive cases cannot be approved from this desk.")
    if decision == "approve" and PLACEHOLDER in (reply or ""):
        errors.append("Edit the draft reply: replace the officer placeholder with the action taken.")
    if decision == "approve" and not (reply or "").strip():
        errors.append("Write the reply to send.")
    if decision in ("reroute", "escalate") and not (note or "").strip():
        errors.append("Add a note explaining the change.")
    if decision == "reroute" and new_department == ai_department:
        errors.append("Choose a different department, or approve instead.")
    return errors


def decision_store():
    return DecisionStore(os.path.join(UI_DIR, "human_decisions.jsonl"))


def audit_rows(router, store, limit=50):
    dec = store.by_id()
    rows = []
    for a in router.audit.read_all()[-limit:][::-1]:
        o = a["ai_output"]
        t = o.get("ticket")
        sens = "sensitive" if o.get("sensitive_categories") else ("human review" if o.get("human_required") else "auto")
        human = "; ".join(f'{d["decision"]} by {d["officer"]}' + (f' to {d["final_department"]}' if d["decision"] == "reroute" else "")
                          + (f': {d["note"]}' if d.get("note") else "") for d in dec.get(t, []))
        rows.append({"Time": a["ts"].replace("T", " "), "Ticket": t, "Engine": o.get("engine", "rules"),
                     "Tool suggested": f'{o.get("department_name") or "No department"}, {sens}',
                     "Complaint (masked)": (o.get("complaint_text") or "")[:90],
                     "Human decision": human or "Waiting for a decision"})
    return rows


def output_dir(cfg_path):
    return load_config(cfg_path)["output_dir"]


def run_tests(cfg_path):
    return run_suite(cfg_path, quiet=True)[0]
