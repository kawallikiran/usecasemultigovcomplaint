"""Early-warning layer: turns many individual complaints into district-level alerts.

How it works (plain words):
 1. Each complaint is sorted by the normal router (language, issue = department, sensitive or not).
    Complaints the router sent to an officer still count, using its suggested issue, and are marked
    "provisional" until the officer confirms; otherwise local-language complaints, which go to officers
    most often, would be under-counted in the very alerts meant to help them.
 2. Complaints are grouped by issue and block (a block is a group of nearby villages).
 3. For each group: complaints in the last N days vs the N days before.
 4. A group alerts only if it has at least `min_complaints` AND rose by at least `min_rise_pct`.
 5. The alert is a recommendation for field verification. It never triggers action by itself.
Privacy: alerts carry counts only, never complaint text or names; village counts below `suppress_below`
are shown as "<5".
"""
import csv
import datetime as dt
import random
from collections import Counter, defaultdict

from .common.report import Finding

ISSUE_NAMES = {"water": "Water-service", "electricity": "Electricity", "health": "Health-service", "roads": "Roads",
               "ration": "Ration (PDS)", "pension": "Pension", "education": "School", "revenue": "Land-records",
               "police": "Police"}


def load_rows(cfg):
    with open(cfg["dataset"], encoding="utf-8") as f:
        return list(csv.DictReader(f))


def classify(router, rows):
    """Adds the system's view of each complaint (never the true labels)."""
    out = []
    for r in rows:
        o = router.process({"id": r["complaint_id"], "text": r["text"]})
        out.append({**r, "issue": o["department"] or "unassigned", "sensitive": bool(o["sensitive_categories"]),
                    "detected_language": o["language"], "provisional": bool(o["human_required"]),
                    "confidence": o["confidence"]})
    return out


def windows(cfg):
    as_of = dt.date.fromisoformat(str(cfg.get("as_of")))
    n = int(cfg.get("window_days", 14))
    cur = (as_of - dt.timedelta(days=n - 1), as_of)
    prev = (as_of - dt.timedelta(days=2 * n - 1), as_of - dt.timedelta(days=n))
    return cur, prev


def _in(d, w):
    return w[0].isoformat() <= d <= w[1].isoformat()


def suppress(n, k):
    return f"<{k}" if 0 < n < k else str(n)


def aggregate(cfg, items):
    """Returns (groups, alerts). One group per issue x block that has complaints in either window."""
    cur_w, prev_w = windows(cfg)
    rules, high = cfg.get("alert_rules", {}), cfg.get("high_priority", {})
    k = int(cfg.get("suppress_below", 5))
    g = defaultdict(lambda: {"current": [], "previous": []})
    for it in items:
        if it["issue"] == "unassigned":
            continue
        key = (it["issue"], it["block"])
        if _in(it["date"], cur_w):
            g[key]["current"].append(it)
        elif _in(it["date"], prev_w):
            g[key]["previous"].append(it)
    groups, alerts = [], []
    for (issue, block), v in sorted(g.items()):
        c, p = len(v["current"]), len(v["previous"])
        rise = None if p == 0 else round((c - p) / p * 100)
        alert = c >= int(rules.get("min_complaints", 20)) and (rise is None or rise >= int(rules.get("min_rise_pct", 50)))
        villages = Counter(x["village"] for x in v["current"])
        langs = Counter(x["detected_language"] for x in v["current"])
        row = {"issue": issue, "issue_name": ISSUE_NAMES.get(issue, issue), "block": block, "current": c,
               "previous": p, "change_pct": rise, "villages": len(villages),
               "provisional_pct": round(100 * sum(x["provisional"] for x in v["current"]) / c) if c else 0,
               "voice_pct": round(100 * sum(x["channel"] == "voice" for x in v["current"]) / c) if c else 0,
               "sensitive_inside": sum(x["sensitive"] for x in v["current"]),
               "top_language": langs.most_common(1)[0] if langs else ("-", 0),
               "village_counts": {vn: suppress(n, k) for vn, n in villages.most_common()},
               "daily": Counter(x["date"] for x in v["current"] + v["previous"]),
               "alert": alert}
        if alert:
            hi = c >= int(high.get("min_complaints", 50)) and (rise is None or rise >= int(high.get("min_rise_pct", 50)))
            row["priority"] = "High" if hi else "Medium"
            alerts.append(row)
        groups.append(row)
    alerts.sort(key=lambda a: (a["priority"] != "High", -a["current"]))
    return groups, alerts


LANG_NAMES = {"cg": "Chhattisgarhi", "hi": "Hindi", "hinglish": "Hinglish", "en": "English", "mixed": "mixed-script"}


def alert_card(a, cfg):
    """The plain-language alert, as the district administration would see it."""
    n = int(cfg.get("window_days", 14))
    lang, ln = a["top_language"]
    share = round(100 * ln / a["current"]) if a["current"] else 0
    rise = "new this period" if a["change_pct"] is None else (f"up {a['change_pct']}%" if a["change_pct"] >= 0 else f"down {-a['change_pct']}%")
    lines = [f"Emerging {a['issue_name'].lower()} issue, {a['block']}",
             f"{a['current']} related complaints across {a['villages']} villages in the last {n} days",
             f"{rise} compared with the previous {n} days ({a['previous']})",
             f"Mostly {LANG_NAMES.get(lang, lang)}-language submissions ({share}%); {a['voice_pct']}% by voice",
             f"Priority: {a['priority']}: field verification recommended"]
    notes = []
    if a["provisional_pct"]:
        notes.append(f"{a['provisional_pct']}% of these complaints are still awaiting officer confirmation of the issue.")
    if a["sensitive_inside"]:
        notes.append(f"{a['sensitive_inside']} sensitive complaint(s) in this group are handled separately by the designated officer.")
    return lines, notes


# ---------------------------------------------------------------------------------------------- checks
def run_checks(cfg, rows, items, groups, alerts):
    """Early-warning checks. Uses the synthetic 'scenario' and 'true_issue' columns, which the system never sees."""
    F = []
    ck = cfg.get("checks", {})
    alerted = {(a["issue"], a["block"]) for a in alerts}
    scen = {}
    for r in rows:
        scen.setdefault(r["scenario"], set()).add((r["true_issue"], r["block"]))
    must = {("spike_water", "High"), ("cluster_health", "Medium")}
    for name, prio in must:
        keys = scen.get(name, set())
        hit = [a for a in alerts if (a["issue"], a["block"]) in keys]
        F.append(Finding(6, f"Planted pattern detected: {name}", f"{name} in {sorted(keys)}",
                         f"An alert is raised, priority {prio}",
                         f"alert raised, priority {hit[0]['priority']}" if hit else "no alert raised",
                         bool(hit) and hit[0]["priority"] == prio, "Critical" if name == "spike_water" else "High"))
    for name, why in (("stable_electricity", "rise too small"), ("small_roads", "too few complaints")):
        keys = scen.get(name, set())
        bad = keys & alerted
        F.append(Finding(4, f"No false alarm: {name}", f"{name} ({why})", "No alert raised",
                         f"alert raised for {sorted(bad)}" if bad else "no alert", not bad, "High"))
    bg = scen.get("background", set()) - set().union(*(v for k, v in scen.items() if k != "background"))
    bad_bg = bg & alerted
    F.append(Finding(4, "No false alarm from background complaints", f"{len(bg)} background issue-block groups",
                     "No alert from thinly spread complaints", f"{len(bad_bg)} alerts: {sorted(bad_bg)[:3]}" if bad_bg else "none",
                     not bad_bg, "High"))

    # counts include officer-routed complaints, and match the true picture
    cur_w, _ = windows(cfg)
    gap_lim = float(ck.get("max_count_gap_pct", 10))
    for a in alerts:
        true_n = sum(1 for r in rows if r["true_issue"] == a["issue"] and r["block"] == a["block"] and _in(r["date"], cur_w))
        gap = abs(a["current"] - true_n) / max(true_n, 1) * 100
        without = a["current"] - round(a["current"] * a["provisional_pct"] / 100)
        F.append(Finding(7, f"Alert count is accurate: {a['issue_name']}, {a['block']}",
                         "Alert count vs the true number of such complaints",
                         f"Within {gap_lim:.0f}% (officer-routed complaints included)",
                         f"alert says {a['current']}, true number {true_n} (gap {gap:.0f}%); without the "
                         f"{a['provisional_pct']}% awaiting officer confirmation it would say about {without}",
                         gap <= gap_lim, "High"))

    # language mix shown in the alert vs reality
    for a in alerts:
        cur_items = [x for x in items if x["issue"] == a["issue"] and x["block"] == a["block"] and _in(x["date"], cur_w)]
        true_lang = Counter(x["language_group"] for x in cur_items).most_common(1)[0]
        true_share = round(100 * true_lang[1] / len(cur_items))
        shown = a["top_language"]
        shown_share = round(100 * shown[1] / a["current"])
        diff = abs(true_share - shown_share) if shown[0] == true_lang[0] else 100
        F.append(Finding(1, f"Language mix shown correctly: {a['issue_name']}, {a['block']}",
                         "Language share in the alert vs the true language of each complaint",
                         "Same main language, share within 10 points",
                         f"alert: {LANG_NAMES.get(shown[0], shown[0])} {shown_share}%; true: "
                         f"{LANG_NAMES.get(true_lang[0], true_lang[0])} {true_share}%",
                         diff <= 10, "Medium"))

    # privacy: small numbers suppressed, no text in alerts
    k = int(cfg.get("suppress_below", 5))
    leaks = [(a["block"], v, c) for a in alerts for v, c in a["village_counts"].items() if c.isdigit() and int(c) < k]
    F.append(Finding(3, "Small village counts are hidden", f"Village counts in {len(alerts)} alerts",
                     f"Counts below {k} shown as <{k}", f"{len(leaks)} small counts shown" if leaks else "all hidden",
                     not leaks, "High"))
    texts = {r["text"] for r in rows}
    leaked = [a for a in alerts if any(t in " ".join(alert_card(a, cfg)[0] + alert_card(a, cfg)[1]) for t in texts)]
    F.append(Finding(3, "No complaint text in alerts", "Alert cards", "Counts only, no complaint wording",
                     "complaint text found" if leaked else "no complaint text", not leaked, "High"))

    # robustness: alerts survive losing some complaints at random
    drop, runs = float(ck.get("robustness_drop_pct", 10)) / 100, int(ck.get("robustness_runs", 5))
    base = {(a["issue"], a["block"]) for a in alerts}
    lost = Counter()
    for sd in range(runs):
        rng = random.Random(sd)
        kept = [x for x in items if rng.random() > drop]
        _, al = aggregate(cfg, kept)
        for key in base - {(a["issue"], a["block"]) for a in al}:
            lost[key] += 1
    F.append(Finding(2, f"Alerts stable when {int(drop * 100)}% of complaints are missing",
                     f"{runs} runs, each dropping {int(drop * 100)}% of complaints at random",
                     "Every alert still raised in every run",
                     "all alerts kept" if not lost else f"lost: {dict(lost)}", not lost, "Medium"))

    # fairness note: who waits for an officer
    by_lang = defaultdict(list)
    for x in items:
        by_lang[x["language_group"]].append(x["provisional"])
    rates = {LANG_NAMES.get(l, l): round(100 * sum(v) / len(v)) for l, v in by_lang.items()}
    spread = max(rates.values()) - min(rates.values())
    F.append(Finding(1, "Share of complaints waiting for an officer, by language",
                     "Provisional (officer-review) share per language",
                     "Gap of at most 25 points between languages", f"{rates}", spread <= 25, "Medium"))
    return F


def write_outputs(cfg, items, groups, alerts, findings):
    """Reports for the early-warning layer: checks (same format as the critic), trends and alerts as CSV."""
    import os
    from .common.report import write_reports
    out = cfg["output_dir"]
    os.makedirs(out, exist_ok=True)
    m = {"complaints": len(items), "groups": len(groups), "alerts": len(alerts),
         "as_of": str(cfg.get("as_of")), "window_days": cfg.get("window_days")}
    summary = write_reports(findings, out, "Use case 8: Early-warning layer", m)
    with open(os.path.join(out, "issue_trends.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["issue", "block", "last_14_days", "previous_14_days", "change_pct", "villages", "awaiting_officer_pct",
                    "voice_pct", "alert", "priority"])
        for g in groups:
            w.writerow([g["issue_name"], g["block"], g["current"], g["previous"], "" if g["change_pct"] is None else g["change_pct"],
                        g["villages"], g["provisional_pct"], g["voice_pct"], "yes" if g["alert"] else "", g.get("priority", "")])
    try:
        from .ew_workbook import build
        with open(cfg["villages"], encoding="utf-8") as f:
            vrows = list(csv.DictReader(f))
        build(cfg, items, vrows, findings, os.path.join(out, "early_warning.xlsx"))
    except ImportError:          # openpyxl missing: CSV outputs still written
        pass
    with open(os.path.join(out, "alerts.txt"), "w", encoding="utf-8") as f:
        for a in alerts:
            lines, notes = alert_card(a, cfg)
            f.write("\n".join(lines + notes) + "\n\n")
    return summary


def run(cfg, router):
    rows = load_rows(cfg)
    items = classify(router, rows)
    groups, alerts = aggregate(cfg, items)
    findings = run_checks(cfg, rows, items, groups, alerts)
    return rows, items, groups, alerts, findings
