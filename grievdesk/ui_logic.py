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
    "Multilingual set (35 complaints, 11 more Indian languages)": os.path.join(ROOT, "config", "grievance_multilingual.yaml"),
    "AI engine on the main set (20 sampled)": AI_CONFIG,
    "AI engine on the held-out set (16)": os.path.join(ROOT, "config", "grievance_challenge_ai.yaml"),
    "AI engine on the multilingual set (24 sampled)": os.path.join(ROOT, "config", "grievance_multilingual_ai.yaml"),
}
SAMPLE_FILES = ["grievances.csv", "multilingual.csv"]
DECISIONS = {"Approve and send reply": "approve", "Send to another department": "reroute",
             "Escalate to senior officer": "escalate"}
PLACEHOLDER = "[Officer: add the action taken"


def ai_options():
    """[(engine key, label)] for every AI service that is ready right now."""
    from .common.ai_profiles import ready_profiles
    return [(f"ai:{n}", label) for n, label, _ in ready_profiles(ROOT, load_config(AI_CONFIG))]


def ai_status_rows():
    from .common.ai_profiles import all_status
    return [{"Service": label, "Name": n, "Ready": "Yes" if ok else "No", "Detail": msg}
            for n, label, ok, msg in all_status(ROOT, load_config(AI_CONFIG))]


def engine_config(engine):
    from .common.ai_profiles import load_profiles, apply_profile
    name = engine.split(":", 1)[1]
    return apply_profile(load_config(AI_CONFIG), load_profiles(ROOT)[name])


def is_ai_set(cfg_path):
    return load_config(cfg_path).get("engine") == "ai"


def make_router(engine="rules"):
    """engine 'rules' (offline) or 'ai:<service name>' from config/ai_providers.yaml."""
    from .system import GrievanceRouter
    if engine == "rules":
        cfg = load_config(MAIN_CONFIG)
        return GrievanceRouter(cfg, AuditLog(os.path.join(UI_DIR, "ai_outputs.jsonl")), engine="rules"), cfg
    cfg = engine_config(engine)
    return GrievanceRouter(cfg, AuditLog(os.path.join(UI_DIR, "ai_outputs.jsonl")), engine="ai"), cfg


def load_samples(cfg):
    """Sample complaints for the first tab: the main set plus the multilingual set."""
    rows = []
    for name in SAMPLE_FILES:
        path = os.path.join(ROOT, "data", "grievance", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                rows += list(csv.DictReader(f))
    return rows


def language_table(router):
    return [{"Code": k, "Language": v.get("name"), "Script": v.get("script"),
             "Support": "Routed" if v.get("support") == "full" else "Recognised, sent to language desk",
             "Trust": v.get("trust")} for k, v in router.languages.items()]


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
        {"Item": "Language", "Value": f'{out.get("language_name") or out["language"]} '
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


def output_dir(cfg_path, profile=None):
    d = load_config(cfg_path)["output_dir"]
    return f"{d}_{profile}" if profile and is_ai_set(cfg_path) else d


def run_tests(cfg_path, profile=None):
    return run_suite(cfg_path, quiet=True, profile=profile)[0]


# ------------------------------------------------------------------ early warning
EW_CONFIG = os.path.join(ROOT, "config", "early_warning.yaml")


def run_early_warning(extra_rows=None, as_of=None):
    """Runs the early-warning layer on the district data (plus portal complaints) and writes its reports.
    as_of: report date (defaults to the date in config/early_warning.yaml)."""
    from . import early_warning as ew
    from .__main__ import early_warning_cmd
    from .system import GrievanceRouter
    cfg = load_config(EW_CONFIG)
    if not os.path.exists(cfg["dataset"]):
        early_warning_cmd("ew-generate", quiet=True)
    if as_of:
        cfg["as_of"] = str(as_of)
    rows, items, groups, alerts, findings = ew.run(cfg, GrievanceRouter(cfg), extra_rows=extra_rows)
    ew.write_outputs(cfg, items, groups, alerts, findings)
    cards = [(a, *ew.alert_card(a, cfg)) for a in alerts]
    return cfg, items, groups, cards, findings


def trend_rows(groups):
    rows = [{"Issue": g["issue_name"], "District": g["district"], "State": g["state"], "Last 14 days": g["current"],
             "Previous 14 days": g["previous"],
             "Change": "new" if g["change_pct"] is None else f'{g["change_pct"]:+d}%', "Towns": g["towns"],
             "Awaiting officer": f'{g["provisional_pct"]}%', "Alert": g.get("priority", "")} for g in groups]
    return sorted(rows, key=lambda r: (r["Alert"] == "", -r["Last 14 days"]))


def town_rows(alert):
    return [{"City / town": v, "Complaints (last 14 days)": c} for v, c in alert["town_counts"].items()]


def daily_rows(alert, cfg):
    import datetime as dt
    as_of = dt.date.fromisoformat(str(cfg["as_of"]))
    n = int(cfg.get("window_days", 14))
    out = []
    for i in range(2 * n - 1, -1, -1):
        d = (as_of - dt.timedelta(days=i)).isoformat()
        out.append({"Date": d, "Complaints": alert["daily"].get(d, 0)})
    return out


def check_rows(findings):
    return [{"Result": "Pass" if f.passed else "Fail", "Severity if failed": f.severity, "Check": f.probe,
             "Expected": f.expected, "What happened": f.actual} for f in sorted(findings, key=lambda f: f.passed)]
