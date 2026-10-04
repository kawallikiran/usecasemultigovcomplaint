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
