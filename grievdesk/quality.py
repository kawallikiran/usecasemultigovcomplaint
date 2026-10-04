"""Plain-language view of the system checks and how well the sorting performs."""
import json
import os

from .__main__ import ROOT
from .common.config import load_config

PLAIN = {
    1: ("Fair to every language and area", "Results are equally good whatever the language, channel or district."),
    2: ("Copes with typing mistakes", "Spelling errors, capital letters or extra symbols do not change the result."),
    3: ("Protects personal details", "Aadhaar and phone numbers are hidden; nobody can ask for someone else's details."),
    4: ("People stay in charge", "The system only suggests; an officer reviews every complaint."),
    5: ("Same answer every time", "The same complaint, asked again or in another language, gets the same result."),
    6: ("Handles problems safely", "Empty, very long or unreadable complaints, and system faults, go to an officer."),
    7: ("No made-up facts", "Replies only state departments and timelines that are actually true."),
    8: ("Resists misuse", "Attempts to trick the system or jump the queue are caught."),
}
SETS = [("Everyday complaints (40)", "grievance.yaml"), ("New wording (16)", "grievance_challenge.yaml"),
        ("11 more Indian languages (35)", "grievance_multilingual.yaml")]


def _findings(cfg_name):
    cfg = load_config(os.path.join(ROOT, "config", cfg_name))
    p = os.path.join(cfg["output_dir"], "findings.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def plain_checks(findings):
    rows = []
    for no, (name, meaning) in PLAIN.items():
        mine = [f for f in findings if f["test_no"] == no]
        failed = [f for f in mine if not f["passed"]]
        if failed:
            worst = sorted(failed, key=lambda f: ["Critical", "High", "Medium", "Low"].index(f["severity"])
                           if f["severity"] in ("Critical", "High", "Medium", "Low") else 9)[0]
            found = f"{len(failed)} of {len(mine)} checks need attention. Most serious: {worst['probe'].replace('_', ' ')}: {worst['actual']}"
            result = "Needs attention" + (" (serious)" if worst["severity"] in ("Critical", "High") else "")
        else:
            found, result = f"No problems found in {len(mine)} checks.", "OK"
        rows.append({"Check": name, "What it means": meaning, "Result": result, "What we found": found})
    return rows


def performance():
    """Rows per test set, and accuracy by language, from the last check runs."""
    sets, langs = [], {}
    for label, cfg_name in SETS:
        d = _findings(cfg_name)
        if not d:
            sets.append({"Test set": label, "Complaints": "-", "Sorted correctly": "not run yet", "Sent to an officer": "-"})
            continue
        m = d["metrics"]
        sets.append({"Test set": label, "Complaints": m.get("cases"), "Sorted correctly": m.get("baseline_accuracy"),
                     "Sent to an officer": m.get("share_sent_to_human")})
        for g, v in (m.get("by_language_group") or {}).items():
            langs.setdefault(g, {"Language": g, "Complaints": 0, "_ok": 0.0})
            langs[g]["Complaints"] += v["n"]
            langs[g]["_ok"] += v["accuracy"] * v["n"]
    lang_rows = [{"Language": r["Language"], "Complaints": r["Complaints"],
                  "Sorted correctly": f"{round(100 * r['_ok'] / r['Complaints'])}%"} for r in langs.values() if r["Complaints"]]
    return sets, sorted(lang_rows, key=lambda r: r["Language"])


# ------------------------------------------------------------------ Assurance page
SEV = ["Critical", "High", "Medium", "Low"]


def tech_summary():
    """Checks passed across the three everyday test sets, serious open problems, weakest language."""
    passed = total = 0
    serious = []
    for label, cfg_name in SETS:
        d = _findings(cfg_name)
        if not d:
            continue
        for f in d["findings"]:
            total += 1
            passed += f["passed"]
            if not f["passed"] and f["severity"] in ("Critical", "High"):
                serious.append({"Test set": label, "Problem": f["probe"].replace("_", " "), "What happened": f["actual"],
                                "Severity": f["severity"]})
    _, langs = performance()
    weakest = min(langs, key=lambda r: int(r["Sorted correctly"].rstrip("%")), default=None)
    return {"passed": passed, "total": total, "serious": serious, "weakest": weakest}


def sensitive_gap():
    """Honest figure on missed sensitive complaints from the 'new wording' test set, if it has been run."""
    d = _findings("grievance_challenge.yaml")
    if not d:
        return "Run the 'New wording' quality check to measure this."
    f = next((x for x in d["findings"] if x["probe"].startswith("Sensitive grievances not detected")), None)
    return f"On new wording, {f['actual'].split(':')[0]} were missed by the keyword rules." if f else ""


def compliance_rows(retention_days, max_deadline, fig):
    met, part = "Met", "Partly met"
    return [
        {"Law": "DPDP Act 2023, s.5", "Requirement": "Tell people what data is collected and why",
         "Status": met, "How": "Privacy notice on the filing screen, in the citizen's language"},
        {"Law": "DPDP Act 2023, s.6-7", "Requirement": "Lawful use / consent",
         "Status": met if fig["consent_share"] in (None, 100) else part,
         "How": f"Consent tick-box; recorded with each complaint ({fig['consent_share'] if fig['consent_share'] is not None else '-'}% of complaints)"},
        {"Law": "DPDP Act 2023, s.8", "Requirement": "Use only what is needed; keep it safe",
         "Status": met, "How": "Only contact details for updates; stored apart; numbers hidden on screens; role-based access"},
        {"Law": "DPDP Act 2023, s.8(7)", "Requirement": "Delete data when no longer needed",
         "Status": met, "How": f"Contact details and recordings deleted {retention_days} days after closing (automatic)"},
        {"Law": "DPDP Act 2023, s.11-12", "Requirement": "Citizen can see, correct or erase their data",
         "Status": met, "How": f"'Your data' on the Track page; {fig['data_requests']} requests, {fig['open_data_requests']} open"},
        {"Law": "DPDP Act 2023, s.8(6), s.13", "Requirement": "Breach reporting; a contact for data complaints",
         "Status": part, "How": "Audit log in place; breach procedure and named data contact to be set in the governance framework"},
        {"Law": "DPDP Act 2023, s.8(2)", "Requirement": "Contract with any outside AI service",
         "Status": part, "How": "Offline rules or local model by default; contract needed before switching on a cloud service"},
        {"Law": "IT Act 2000 and CERT-In directions (2022)", "Requirement": "Security practices, logs, incident reporting",
         "Status": part, "How": "Audit log of every action; log-keeping period and 6-hour incident reporting to be written into the SOP"},
        {"Law": "DARPG grievance guidelines", "Requirement": "Redress within 21 days",
         "Status": met if max_deadline <= 21 else part,
         "How": f"Longest department deadline {max_deadline} days; overdue complaints move to the senior officer automatically"},
        {"Law": "DARPG / CPGRAMS practice", "Requirement": "Citizen can appeal a reply",
         "Status": met, "How": "'Not satisfied' sends an appeal to the senior officer"},
        {"Law": "GIGW and RPwD Act 2016", "Requirement": "Accessible to all citizens",
         "Status": part, "How": "13 languages and voice complaints; formal accessibility audit still to be done"},
    ]


def stakeholder_rows(fig, retention_days):
    return [
        {"If this happens to me": "My complaint goes to the wrong office",
         "What the system does": "The officer reassigns it and the right officer reviews it",
         "Live figure": f"{fig['reassigned']} reassigned, {fig['reassign_days']} days added on average"},
        {"If this happens to me": "My complaint about danger is treated as routine",
         "What the system does": "Danger words send it to the designated officer at once; every complaint is read by an officer",
         "Live figure": sensitive_gap()},
        {"If this happens to me": "Nobody replies in time",
         "What the system does": "After the due date it moves to the senior officer and I am told",
         "Live figure": f"{fig['overdue_open']} overdue now, {fig['auto_escalated']} moved automatically"},
        {"If this happens to me": "I don't agree with the reply",
         "What the system does": "I tick 'not satisfied' and it goes to the senior officer as an appeal",
         "Live figure": f"{fig['appeals']} appeals" + (f"; {fig['satisfied_share']}% satisfied" if fig['satisfied_share'] is not None else "")},
        {"If this happens to me": "I can't write, or speak another language",
         "What the system does": "I can speak my complaint; 13 languages; anything unclear goes to a person", "Live figure": ""},
        {"If this happens to me": "I worry about my phone number",
         "What the system does": f"Hidden on officers' screens; deleted {retention_days} days after closing; I can ask for deletion",
         "Live figure": f"{fig['open_data_requests']} data requests open"},
    ]
