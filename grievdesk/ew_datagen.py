"""Demo complaint data on real places (zone, state, district, city/town from data/grievance/locations.csv).

Complaints are SYNTHETIC: every row is marked synthetic=yes. Texts are the frequently filed complaints in the
main language of each state. Planted patterns (exact counts, so the early-warning checks know the right answer):
  spike_water         Raipur, Chhattisgarh, water:           127 in the last 14 days vs 77 before  -> should alert (High)
  cluster_health      Bilaspur, Chhattisgarh, health:        22 vs 12, in 3 towns                  -> should alert (Medium)
  stable_electricity  North Twentyfour Parganas, WB:        26 vs 20 (+30%)                       -> should NOT alert
  small_roads         Ernakulam, Kerala, roads:              9 vs 3 (+200%)                        -> should NOT alert
  background          the rest, thinly spread across India                                        -> should NOT alert
"""
import csv
import datetime as dt
import math
import os
import random

import yaml

from .locations import load as load_locations

DATA = os.path.join(os.path.dirname(__file__), os.pardir, "data", "grievance")
STATE_LANG = {  # main language of complaints in each state (shares; the rest Hindi / Hinglish / English)
    "Andhra Pradesh": "te", "Telangana": "te", "Karnataka": "kn", "Kerala": "ml", "Lakshadweep": "ml", "Tamil Nadu": "ta",
    "Puducherry": "ta", "West Bengal": "bn", "Odisha": "or", "Assam": "as", "Tripura": "bn", "Gujarat": "gu",
    "Dadra and Nagar Haveli and Daman and Diu": "gu", "Maharashtra": "mr", "Goa": "mr", "Punjab": "pa", "Chandigarh": "pa",
    "Jammu & Kashmir": "ur", "Chhattisgarh": "cg",
}
HINDI_BELT = {"Uttar Pradesh", "Bihar", "Madhya Pradesh", "Rajasthan", "Haryana", "Delhi", "Himachal Pradesh",
              "Uttarakhand", "Jharkhand", "Chhattisgarh", "Chandigarh"}
SLA = {"water": 7, "electricity": 3, "ration": 7, "pension": 15, "roads": 21, "health": 3, "revenue": 21,
       "education": 15, "police": 1}
OFFICER_FOR = {"water": "OFF-WAT01", "electricity": "OFF-ELE01", "ration": "OFF-RAT01", "pension": "OFF-PEN01",
               "roads": "OFF-ROA01", "health": "OFF-HEA01", "revenue": "OFF-REV01", "education": "OFF-EDU01",
               "police": "OFF-POL01"}
EXTRA = {   # sensitive and unclear complaints (not in the frequent list)
    "sensitive": {"hi": ["पड़ोसी ने मेरे साथ मारपीट की और धमकी दी।", "राशन दुकान वाले ने रिश्वत मांगी।"],
                  "en": ["The ration dealer asked for a bribe to give my quota.", "My neighbour beat me and threatened me."]},
    "unclear": {"hi": ["मेरा काम नहीं हो रहा, कोई सुनता नहीं है।"], "en": ["Nobody is listening to us, please check."]},
}
HINGLISH = {"water": "Kai din se paani nahi aa raha, nal sukha hai.", "electricity": "Bijli nahi hai kai din se, transformer kharab hai.",
            "ration": "Ration dukan wala chawal nahi de raha.", "pension": "Meri vridha pension kai mahine se band hai.",
            "roads": "Sadak pe bahut gaddhe hai.", "health": "Aspatal mein doctor nahi hai, dawai bhi nahi mil rahi.",
            "education": "School mein teacher nahi aate."}
PLANTED = [  # scenario, state, district, issue, (current, previous), towns used in the current window
    ("spike_water", "Chhattisgarh", "Raipur", "water", (127, 77), 14),
    ("cluster_health", "Chhattisgarh", "Bilaspur", "health", (22, 12), 3),
    ("stable_electricity", "West Bengal", "North Twentyfour Parganas", "electricity", (26, 20), 10),
    ("small_roads", "Kerala", "Ernakulam", "roads", (9, 3), 4),
]
MESSY_WATER = [   # informal wording real citizens use; often needs an officer to confirm the department
    ("Hamaar gaon ke handpump kai din se kharab hai.", "cg"), ("हमर पारा के हैंडपंप बंद हवय।", "cg"),
    ("Paani nai aawat he, kai din hoge.", "cg"), ("Water supply nahi aa rahi since 10 days.", "hinglish")]
ISSUES = ["water", "electricity", "ration", "pension", "roads", "health", "education"]


def _templates():
    with open(os.path.join(DATA, "common_complaints.yaml"), encoding="utf-8") as f:
        d = yaml.safe_load(f)["complaints"]
    t = {}
    for lang, items in d.items():
        for it in items:
            t.setdefault(lang, {}).setdefault(it["department"], []).append((it["text"], it["category"]))
    return t


def generate(cfg) -> int:
    from .taxonomy import categorize
    g = cfg.get("generator", {})
    rng = random.Random(g.get("seed", 26))
    as_of = dt.date.fromisoformat(str(cfg.get("as_of", "2026-10-03")))
    win = int(cfg.get("window_days", 14))
    tpl = _templates()
    locs = load_locations()
    towns, zone = {}, {}
    for r in locs:
        towns.setdefault((r["state"], r["district"]), []).append(r["town"])
        zone[r["state"]] = r["zone"]

    def pick_lang(state):
        main, x = STATE_LANG.get(state), rng.random()
        if state in HINDI_BELT and main != "cg":
            return "hi" if x < 0.7 else ("hinglish" if x < 0.9 else "en")
        if main == "cg":
            return "cg" if x < 0.55 else ("hi" if x < 0.9 else "hinglish")
        if main:
            return main if x < 0.75 else ("en" if x < 0.9 else ("hi" if x < 0.95 else "hinglish"))
        return "en" if x < 0.7 else "hi"

    def text_for(issue, lang):
        if issue in EXTRA:
            pool = EXTRA[issue].get(lang) or EXTRA[issue]["en"]
            return rng.choice(pool), None
        if lang == "hinglish":
            return HINGLISH[issue], None
        return rng.choice(tpl.get(lang, tpl["en"])[issue])

    def make(state, district, town, issue, window, scenario):
        lang = pick_lang(state)
        text, cat = text_for(issue, lang)
        if scenario == "spike_water" and rng.random() < 0.3:
            text, lang = rng.choice(MESSY_WATER)
            cat = None
        start = as_of - dt.timedelta(days=(win if window == "current" else 2 * win) - 1)
        date = start + dt.timedelta(days=rng.randint(0, win - 1))
        voice = rng.random() < (0.25 if lang not in ("en", "hinglish") else 0.1)
        return {"date": date.isoformat(), "zone": zone[state], "state": state, "district": district, "town": town,
                "channel": "voice" if voice else "text", "language_group": lang, "text": text, "true_issue": issue,
                "scenario": scenario, "_cat": cat}

    rows = []
    for scen, state, district, issue, (cur, prev), n_towns in PLANTED:
        pool = towns[(state, district)]
        tw = rng.sample(pool, min(n_towns, len(pool)))
        cur_t = tw + [rng.choice(tw) for _ in range(cur - len(tw))]
        rows += [make(state, district, t, issue, "current", scen) for t in cur_t]
        prev_pool = tw[: max(1, int(len(tw) * 0.65))]
        rows += [make(state, district, rng.choice(prev_pool), issue, "previous", scen) for _ in range(prev)]
    planted_keys = {(s, d, i) for _, s, d, i, _, _ in PLANTED}
    keys = list(towns)
    weights = [math.sqrt(len(towns[k])) for k in keys]          # bigger districts get a little more
    target = len(rows) + int(g.get("background", 1704))
    while len(rows) < target:
        state, district = rng.choices(keys, weights=weights)[0]
        r = rng.random()
        issue = "sensitive" if r < 0.07 else ("unclear" if r < 0.12 else rng.choice(ISSUES))
        if (state, district, issue) in planted_keys:
            continue
        rows.append(make(state, district, rng.choice(towns[(state, district)]), issue,
                         rng.choice(["current", "previous"]), "background"))
    rows.sort(key=lambda x: (x["date"], x["state"], x["district"], x["town"]))
    for i, r in enumerate(rows, 1):
        r["complaint_id"] = f"DEMO{i:05d}"
        dept = r["true_issue"] if r["true_issue"] in SLA else ("police" if r["true_issue"] == "sensitive"
                                                                else rng.choice(ISSUES))
        r["department"] = dept
        r["category"] = r.pop("_cat") or categorize(dept, r["text"])
        r["officer_id"] = "OFF-SC01" if r["true_issue"] == "sensitive" else OFFICER_FOR[dept]
        d = dt.date.fromisoformat(r["date"])
        r["due_date"] = (d + dt.timedelta(days=SLA[dept])).isoformat()
        age = (as_of - d).days
        if rng.random() < (0.85 if age >= 14 else 0.45):
            late = rng.random() < 0.18
            days = SLA[dept] + rng.randint(1, 10) if late else max(1, int(SLA[dept] * rng.uniform(0.3, 1.0)))
            days = min(days, max(age, 1))
            r.update(status="Resolved", days_to_resolve=days, resolved_date=(d + dt.timedelta(days=days)).isoformat())
        else:
            r.update(status="Pending", days_to_resolve="", resolved_date="")
        r["synthetic"] = "yes"
    cols = ["complaint_id", "date", "zone", "state", "district", "town", "channel", "language_group", "text", "true_issue",
            "scenario", "department", "category", "officer_id", "status", "due_date", "resolved_date", "days_to_resolve",
            "synthetic"]
    os.makedirs(os.path.dirname(cfg["dataset"]), exist_ok=True)
    with open(cfg["dataset"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)
