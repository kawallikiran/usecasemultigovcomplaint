"""Synthetic data for the early-warning demo: a made-up district, its villages, and 500 complaints over 28 days.

Planted patterns (exact counts, so the checks know the right answer):
  spike_water        South Block, water: 127 complaints in the last 14 days across 23 villages, 77 before -> should alert (High)
  cluster_health     West Block, health: 22 vs 12, in 3 villages around one health centre      -> should alert (Medium)
  stable_electricity East Block, electricity: 26 vs 20 (+30%)                                   -> should NOT alert (rise too small)
  small_roads        North Block, roads: 9 vs 3 (+200%)                                         -> should NOT alert (too few)
  background         everything else, spread thinly                                              -> should NOT alert
All names are invented; any match with a real village is coincidental.
"""
import csv
import datetime as dt
import os
import random

BLOCKS = {  # block: (number of villages, name suffix, language mix)
    "North Block": (12, "pur", {"cg": 0.45, "hi": 0.35, "hinglish": 0.15, "en": 0.05}),
    "South Block": (26, "di", {"cg": 0.70, "hi": 0.18, "hinglish": 0.10, "en": 0.02}),
    "East Block": (12, "nagar", {"cg": 0.15, "hi": 0.40, "hinglish": 0.30, "en": 0.15}),
    "West Block": (12, "gaon", {"cg": 0.55, "hi": 0.30, "hinglish": 0.12, "en": 0.03}),
}
STEMS = ["Ama", "Bhanu", "Chira", "Dongri", "Garhi", "Jhari", "Kesa", "Lata", "Mohla", "Nawa", "Pandri", "Rani",
         "Sona", "Tola", "Urla", "Bela", "Kosa", "Mahu", "Saja", "Teli", "Bori", "Hardi", "Kuru", "Paru", "Sirsi", "Dhamni"]

DEVA_STEMS = {"Ama": "आमा", "Bhanu": "भानु", "Chira": "चिरा", "Dongri": "डोंगरी", "Garhi": "गढ़ी", "Jhari": "झरी",
              "Kesa": "केसा", "Lata": "लता", "Mohla": "मोहला", "Nawa": "नवा", "Pandri": "पंडरी", "Rani": "रानी",
              "Sona": "सोना", "Tola": "टोला", "Urla": "उरला", "Bela": "बेला", "Kosa": "कोसा", "Mahu": "महु",
              "Saja": "सजा", "Teli": "तेली", "Bori": "बोरी", "Hardi": "हरदी", "Kuru": "कुरु", "Paru": "परु",
              "Sirsi": "सिरसी", "Dhamni": "धमनी"}
DEVA_SUFFIX = {"pur": "पुर", "di": "डीह", "nagar": "नगर", "gaon": "गाँव", "garh": "गढ़", "pali": "पाली",
               "kona": "कोना", "ghat": "घाट", "tikra": "टिकरा", "para": "पारा"}
STATE = "Demo State"
OTHER_DISTRICTS = {   # background complaints only (no planted patterns), for state and district analytics
    "Hill District": {"Upper Block": (8, "garh", {"cg": 0.5, "hi": 0.35, "hinglish": 0.1, "en": 0.05}),
                      "Lower Block": (8, "pali", {"cg": 0.45, "hi": 0.4, "hinglish": 0.1, "en": 0.05}),
                      "Valley Block": (8, "kona", {"cg": 0.6, "hi": 0.3, "hinglish": 0.08, "en": 0.02})},
    "River District": {"Ghat Block": (8, "ghat", {"cg": 0.3, "hi": 0.45, "hinglish": 0.2, "en": 0.05}),
                       "Delta Block": (8, "tikra", {"cg": 0.4, "hi": 0.4, "hinglish": 0.15, "en": 0.05}),
                       "Bank Block": (8, "para", {"cg": 0.25, "hi": 0.45, "hinglish": 0.2, "en": 0.1})},
}
SLA = {"water": 7, "electricity": 3, "ration": 7, "pension": 15, "roads": 30, "health": 3, "revenue": 30,
       "education": 15, "police": 1}

# {v} = village, {d} = number of days. Water texts deliberately mix strong (2 keywords) and weak (1 keyword) wording,
# so some route automatically and some go to an officer first, as in real life.
T = {
    "water": {
        "cg": ["हमर गाँव {v} के हैंडपंप {d} दिन ले खराब हवय, पीए बर पानी नइ मिलत हे।",
               "{v} म नल ले पानी नइ आवत हे, {d} दिन होगे।",
               "हमर पारा के हैंडपंप {d} दिन ले बंद हवय।"],
        "cg_roman": ["Hamaar gaon {v} ke handpump {d} din se kharab hai.",
                     "{v} me paani nai aawat he, {d} din hoge."],
        "hi": ["{v} गाँव में नल से पानी नहीं आ रहा है, {d} दिन हो गए।",
               "हमारे गाँव {v} का हैंडपंप {d} दिन से खराब है, पीने का पानी नहीं है।"],
        "hinglish": ["Water supply nahi aa rahi since {d} days in {v}.",
                     "{v} mein {d} din se paani nahi aa raha, nal sukha hai."],
        "en": ["No drinking water in {v} for {d} days, the handpump is broken."],
    },
    "health": {
        "cg": ["{v} के अस्पताल म डाक्टर नइ हे, दवई घलो नइ मिलत।"],
        "hi": ["{v} के स्वास्थ्य केंद्र में डॉक्टर नहीं है और दवाई नहीं मिल रही।"],
        "hinglish": ["{v} ke aspatal mein doctor nahi hai, dawai bhi nahi mil rahi."],
        "en": ["No doctor at the {v} health centre and no medicine for {d} days."],
    },
    "electricity": {
        "cg": ["{v} म {d} दिन ले बिजली नइ हे, ट्रांसफार्मर जर गे हे।"],
        "hi": ["{v} में {d} दिन से बिजली नहीं है, ट्रांसफार्मर जल गया है।"],
        "hinglish": ["{v} mein bijli nahi hai {d} din se, meter kharab hai."],
        "en": ["No electricity in {v} for {d} days, the transformer is burnt."],
    },
    "roads": {
        "cg": ["{v} के रद्दा म बड़े-बड़े गड्ढा हवय।"],
        "hi": ["{v} की सड़क पर बड़े गड्ढे हैं।"],
        "hinglish": ["{v} ki sadak pe bahut gaddhe hai."],
        "en": ["Big potholes on the road to {v}, the road is unsafe."],
    },
    "ration": {
        "cg": ["{v} के राशन दुकान म चाउर नइ देवत हे।"],
        "hi": ["{v} की राशन दुकान वाला चावल नहीं दे रहा।"],
        "hinglish": ["{v} ki ration dukan wala chawal nahi de raha."],
        "en": ["The ration shop dealer in {v} refuses to give rice."],
    },
    "pension": {
        "cg": ["मोर वृद्धावस्था पेंशन {d} दिन ले नइ मिले हे।"],
        "hi": ["मेरी विधवा पेंशन {d} दिन से नहीं आई है।"],
        "hinglish": ["Meri vridha pension {d} din se band hai."],
        "en": ["My old age pension has not come for {d} days."],
    },
    "education": {
        "cg": ["{v} के इस्कूल म गुरुजी नइ आवत हे।"],
        "hi": ["{v} के स्कूल में शिक्षक नहीं आते हैं।"],
        "hinglish": ["{v} ke school mein teacher nahi aate."],
        "en": ["The teacher does not come to the {v} primary school."],
    },
    "revenue": {
        "cg": ["{v} म मोर भुइयां के पट्टा अभी तक नइ मिले हे।"],
        "hi": ["{v} में मेरी जमीन का नामांतरण लंबित है।"],
        "hinglish": ["{v} mein zameen ka patta nahi mila."],
        "en": ["The land record for my land in {v} shows the wrong boundary after mutation."],
    },
    "sensitive": {
        "cg": ["{v} के सरपंच ह जाति के नाम ले के गारी देथे अउ धमकी देथे।"],
        "hi": ["{v} में पड़ोसी ने मेरे साथ मारपीट की और धमकी दी।"],
        "hinglish": ["{v} ke ration dealer ne rishwat maangi."],
        "en": ["The ration dealer in {v} asked for a bribe to give my quota."],
    },
    "unclear": {
        "cg": ["हमर बात कोनो नइ सुनय।"],
        "hi": ["मेरा काम नहीं हो रहा, कोई सुनता नहीं है।"],
        "hinglish": ["Koi sunta nahi hai, kaam nahi ho raha."],
        "en": ["Nobody is listening to us, please check."],
    },
}

PLANTED = [  # scenario, block, issue, (current, previous), villages in current window
    ("spike_water", "South Block", "water", (127, 77), 23),
    ("cluster_health", "West Block", "health", (22, 12), 3),
    ("stable_electricity", "East Block", "electricity", (26, 20), 8),
    ("small_roads", "North Block", "roads", (9, 3), 4),
]


def _population(name):
    import hashlib
    return 600 + int(hashlib.sha1(name.encode()).hexdigest(), 16) % 3900      # stable 600 - 4,499


def villages():
    out = []
    every = [("Demo District", BLOCKS)] + list(OTHER_DISTRICTS.items())
    for district, blocks in every:
        for b, (n, suffix, _) in blocks.items():
            for i in range(n):
                name = f"{STEMS[i]}{suffix}"
                out.append({"village": name, "village_local": DEVA_STEMS[STEMS[i]] + DEVA_SUFFIX[suffix],
                            "block": b, "district": district, "state": STATE, "population": _population(name),
                            "settlement": "semi-urban" if b == "East Block" and i < 4 else "rural"})
    return out


def generate(cfg) -> int:
    g = cfg.get("generator", {})
    rng = random.Random(g.get("seed", 26))
    as_of = dt.date.fromisoformat(str(cfg.get("as_of", "2026-10-03")))
    win = int(cfg.get("window_days", 14))
    total = int(g.get("total", 500))
    vrows = villages()
    by_block, local = {}, {}
    for v in vrows:
        by_block.setdefault(v["block"], []).append(v["village"])
        local[v["village"]] = v["village_local"]

    mixes = {b: v[2] for b, v in BLOCKS.items()}
    for blocks in OTHER_DISTRICTS.values():
        mixes.update({b: v[2] for b, v in blocks.items()})
    district_of = {v["village"]: v["district"] for v in vrows}

    def pick_lang(block, issue):
        mix = mixes[block]
        lang = rng.choices(list(mix), weights=list(mix.values()))[0]
        if issue == "water" and lang == "cg" and rng.random() < 0.3:
            return "cg_roman"
        return lang

    def make(block, issue, window, village, scenario):
        lang = pick_lang(block, issue)
        tpls = T[issue].get(lang) or T[issue]["hi"]
        name = local[village] if lang in ("cg", "hi") else village     # Devanagari name inside Devanagari text
        text = rng.choice(tpls).format(v=name, d=rng.randint(3, 15))
        start = as_of - dt.timedelta(days=(win if window == "current" else 2 * win) - 1)
        date = start + dt.timedelta(days=rng.randint(0, win - 1))
        voice = rng.random() < (0.25 if lang in ("cg", "cg_roman") else 0.12)
        return {"date": date.isoformat(), "state": STATE, "district": district_of[village], "block": block, "village": village,
                "channel": "voice" if voice else "text",
                "language_group": "cg" if lang == "cg_roman" else lang,
                "written_as": "Latin letters" if lang in ("cg_roman", "hinglish", "en") else "own script",
                "text": text, "true_issue": issue, "scenario": scenario}

    rows = []
    for scen, block, issue, (cur, prev), nvil in PLANTED:
        vil = rng.sample(by_block[block], min(nvil, len(by_block[block])))
        cur_vil = vil + [rng.choice(vil) for _ in range(cur - len(vil))]      # every chosen village appears at least once
        rows += [make(block, issue, "current", v, scen) for v in cur_vil]
        prev_pool = vil[: max(1, int(len(vil) * 0.65))]
        rows += [make(block, issue, "previous", rng.choice(prev_pool), scen) for _ in range(prev)]
    planted_keys = {(b, i) for _, b, i, _, _ in PLANTED}
    issues = ["water", "health", "electricity", "roads", "ration", "pension", "education", "revenue"]
    while len(rows) < total:
        block = rng.choice(list(BLOCKS))
        r = rng.random()
        issue = "sensitive" if r < 0.07 else ("unclear" if r < 0.12 else rng.choice(issues))
        if (block, issue) in planted_keys:
            continue
        rows.append(make(block, issue, rng.choice(["current", "previous"]), rng.choice(by_block[block]), "background"))
    # other districts: thinly spread background complaints
    other_blocks = {b: d for d, blocks in OTHER_DISTRICTS.items() for b in blocks}
    target = len(rows) + int(g.get("other_districts_total", 300))
    while len(rows) < target:
        block = rng.choice(list(other_blocks))
        r = rng.random()
        issue = "sensitive" if r < 0.07 else ("unclear" if r < 0.12 else rng.choice(issues))
        rows.append(make(block, issue, rng.choice(["current", "previous"]), rng.choice(by_block[block]), "background"))
    rows.sort(key=lambda x: (x["date"], x["district"], x["block"], x["village"]))
    _outcomes(rows, rng, as_of)
    for i, row in enumerate(rows, 1):
        row["complaint_id"] = f"EW{i:04d}"
    os.makedirs(os.path.dirname(cfg["dataset"]), exist_ok=True)
    cols = ["complaint_id", "date", "state", "district", "block", "village", "channel", "language_group", "written_as",
            "text", "true_issue", "scenario", "department", "category", "officer_id", "status", "resolved_date",
            "days_to_resolve", "due_date"]
    with open(cfg["dataset"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with open(cfg["villages"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["village", "village_local", "block", "district", "state", "population", "settlement"])
        w.writeheader()
        w.writerows(vrows)
    return len(rows)


OFFICER_FOR = {"water": "OFF-WAT01", "electricity": "OFF-ELE01", "ration": "OFF-RAT01", "pension": "OFF-PEN01",
               "roads": "OFF-ROA01", "health": "OFF-HEA01", "revenue": "OFF-REV01", "education": "OFF-EDU01",
               "police": "OFF-POL01"}


def _outcomes(rows, rng, as_of):
    """Final department and category (as an officer would confirm them), the officer, and what happened next."""
    from .taxonomy import categorize
    depts = list(SLA)
    for r in rows:
        dept = r["true_issue"] if r["true_issue"] in SLA else ("police" if r["true_issue"] == "sensitive"
                                                                else rng.choice(depts[:-1]))
        r["department"], r["category"] = dept, categorize(dept, r["text"])
        r["officer_id"] = "OFF-SC01" if r["true_issue"] == "sensitive" else OFFICER_FOR[dept]
        d = dt.date.fromisoformat(r["date"])
        r["due_date"] = (d + dt.timedelta(days=SLA[dept])).isoformat()
        age = (as_of - d).days
        p_done = 0.85 if age >= 14 else 0.45
        if rng.random() < p_done:
            late = rng.random() < 0.18
            days = SLA[dept] + rng.randint(1, 10) if late else max(1, int(SLA[dept] * rng.uniform(0.3, 1.0)))
            days = min(days, max(age, 1))
            r["status"], r["days_to_resolve"] = "Resolved", days
            r["resolved_date"] = (d + dt.timedelta(days=days)).isoformat()
        else:
            r["status"], r["days_to_resolve"], r["resolved_date"] = "Pending", "", ""
