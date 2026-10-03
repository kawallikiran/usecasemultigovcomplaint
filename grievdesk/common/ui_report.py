"""Turns critic findings into plain rows for any UI (Streamlit tables, CSV, etc.). No UI imports here."""
import json
import os
import time

from .report import CRITIC_TESTS, SEVERITY_ORDER


def load_findings(output_dir):
    """Returns (data, last_run_text) or (None, None) if the tests have not been run yet."""
    p = os.path.join(output_dir, "findings.json")
    if not os.path.exists(p):
        return None, None
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data, time.strftime("%d %b %Y %H:%M", time.localtime(os.path.getmtime(p)))


def plain(s):
    return str(s).replace("_", " ")


def by_test_rows(findings):
    rows = []
    for no, name in CRITIC_TESTS.items():
        t = [x for x in findings if x["test_no"] == no]
        tf = [x for x in t if not x["passed"]]
        worst = min((x["severity"] for x in tf), key=lambda s: SEVERITY_ORDER.get(s, 9), default="-")
        rows.append({"#": no, "Critic test": name, "Checks": len(t), "Failed": len(tf),
                     "Worst severity": worst if tf else "-"})
    return rows


def failed_rows(findings):
    failed = sorted([x for x in findings if not x["passed"]],
                    key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["test_no"]))
    return [{"Severity": x["severity"], "Test": f'{x["test_no"]}. {x["test_name"]}', "Check": plain(x["probe"]),
             "What was tested": plain(x["tested"]), "Expected": x["expected"], "What happened": x["actual"]}
            for x in failed]


def all_rows(findings, show="All"):
    rows = []
    for x in sorted(findings, key=lambda x: (x["test_no"], x["passed"])):
        if show == "Failed only" and x["passed"]:
            continue
        if show == "Passed only" and not x["passed"]:
            continue
        rows.append({"Result": "Pass" if x["passed"] else "Fail", "#": x["test_no"], "Critic test": x["test_name"],
                     "Check": plain(x["probe"]), "What was tested": plain(x["tested"]),
                     "Expected": x["expected"], "What happened": x["actual"],
                     "Severity if failed": x["severity"]})
    return rows


def subgroup_tables(metrics, adverse_label):
    """[(group field, rows)] from metrics['by_<field>']."""
    out = []
    for k, groups in metrics.items():
        if not k.startswith("by_"):
            continue
        rows = [{"Group": g, "Cases": v["n"], "Accuracy": f'{v["accuracy"]:.0%}',
                 adverse_label: "-" if v["false_adverse_rate"] is None else f'{v["false_adverse_rate"]:.0%}'}
                for g, v in groups.items()]
        out.append((plain(k[3:]), rows))
    return out
