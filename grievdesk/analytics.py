"""Grievance analytics: counts by department, category, zone, state, district and city/town, trends, service
performance and where to focus. Uses the district complaint data plus complaints filed on the portal."""
import csv
import datetime as dt
import io
import os

import pandas as pd

from .__main__ import ROOT
from .early_warning import ISSUE_NAMES
from .taxonomy import category_name

DATA = os.path.join(ROOT, "data", "grievance")
LANG = {"cg": "Chhattisgarhi", "hi": "Hindi", "hinglish": "Hinglish", "en": "English", "mixed": "Mixed"}


def load(extra_rows=None):
    df = pd.read_csv(os.path.join(DATA, "demo_complaints.csv"), dtype=str).fillna("")
    if extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows).astype(str)], ignore_index=True)
    vil = pd.read_csv(os.path.join(DATA, "locations.csv"), dtype=str)
    df["date"] = pd.to_datetime(df["date"])
    df["Department"] = df["department"].map(lambda d: ISSUE_NAMES.get(d, d.capitalize() if d else "Not assigned"))
    df["Category"] = [category_name(d, c) for d, c in zip(df["department"], df["category"])]
    df["Language"] = df["language_group"].map(lambda l: LANG.get(l, l or "Unknown"))
    df["days_to_resolve"] = pd.to_numeric(df["days_to_resolve"], errors="coerce")
    return df, vil


def filter_df(df, zone="All", state="All", district="All", department="All", start=None, end=None):
    out = df
    if zone != "All":
        out = out[out["zone"] == zone]
    if state != "All":
        out = out[out["state"] == state]
    if district != "All":
        out = out[out["district"] == district]
    if department != "All":
        out = out[out["Department"] == department]
    if start is not None:
        out = out[out["date"] >= pd.Timestamp(start)]
    if end is not None:
        out = out[out["date"] <= pd.Timestamp(end)]
    return out


def kpis(df, as_of):
    pending = df[df["status"] == "Pending"]
    overdue = pending[pending["due_date"] < str(as_of)]
    resolved = df[df["status"] == "Resolved"]
    return {"Complaints": len(df), "Resolved": len(resolved), "Pending": len(pending), "Overdue": len(overdue),
            "Average days to resolve": round(float(resolved["days_to_resolve"].mean()), 1) if len(resolved) else 0,
            "By voice": f"{round(100 * (df['channel'] == 'voice').mean())}%" if len(df) else "0%"}


def by(df, col, top=None):
    t = df.groupby(col).size().sort_values(ascending=False).rename("Complaints").reset_index()
    return t.head(top) if top else t


def location_table(df, vil):
    """Complaints per city/town, with its urban status from the Census list."""
    t = df.groupby(["zone", "state", "district", "town"]).size().rename("Complaints").reset_index()
    t = t.merge(vil[["state", "district", "town", "urban_status"]], on=["state", "district", "town"], how="left")
    pend = df[df["status"] == "Pending"].groupby(["state", "district", "town"]).size().rename("Pending").reset_index()
    t = t.merge(pend, on=["state", "district", "town"], how="left").fillna({"Pending": 0})
    t["Pending"] = t["Pending"].astype(int)
    return t.rename(columns={"zone": "Zone", "state": "State", "district": "District", "town": "City / town",
                             "urban_status": "Urban status"}).sort_values("Complaints", ascending=False)


def district_table(df, as_of, window=14):
    """Complaints per district: total, last window vs the window before, pending."""
    end = pd.Timestamp(as_of)
    cur = df[(df["date"] > end - pd.Timedelta(days=window)) & (df["date"] <= end)]
    prev = df[(df["date"] > end - pd.Timedelta(days=2 * window)) & (df["date"] <= end - pd.Timedelta(days=window))]
    keys = ["zone", "state", "district"]
    t = df.groupby(keys).size().rename("Complaints").reset_index()
    for name, part in (("Last 14 days", cur), ("Previous 14 days", prev), ("Pending", df[df["status"] == "Pending"])):
        t = t.merge(part.groupby(keys).size().rename(name).reset_index(), on=keys, how="left")
    t = t.fillna(0)
    for c in ("Last 14 days", "Previous 14 days", "Pending"):
        t[c] = t[c].astype(int)
    return t.rename(columns={"zone": "Zone", "state": "State", "district": "District"}).sort_values("Complaints", ascending=False)


def trend(df):
    return df.groupby(df["date"].dt.date).size().rename("Complaints").reset_index().rename(columns={"date": "Date"}).set_index("Date")


def department_performance(df, as_of):
    rows = []
    for dept, g in df.groupby("Department"):
        pend = g[g["status"] == "Pending"]
        res = g[g["status"] == "Resolved"]
        late = res[res["resolved_date"] > res["due_date"]]
        rows.append({"Department": dept, "Complaints": len(g), "Pending": len(pend),
                     "Overdue": int((pend["due_date"] < str(as_of)).sum()),
                     "Resolved on time": f"{round(100 * (len(res) - len(late)) / len(res))}%" if len(res) else "-",
                     "Average days to resolve": round(float(res["days_to_resolve"].mean()), 1) if len(res) else None})
    return pd.DataFrame(rows).sort_values("Overdue", ascending=False)


def focus_areas(df, vil, alerts, as_of, top=5):
    """Plain-language pointers: emerging issues, departments falling behind, districts with most pending complaints."""
    pts = []
    for a in alerts:
        pts.append(("Emerging issue", f"{a['issue_name']} in {a['district']} district, {a['state']}: {a['current']} complaints "
                    f"in {a['towns']} towns, up {a['change_pct']}% (priority {a['priority']})."))
    perf = department_performance(df, as_of)
    for _, r in perf[perf["Overdue"] > 0].head(3).iterrows():
        pts.append(("Falling behind", f"{r['Department']}: {r['Overdue']} complaints past their due date."))
    dist = district_table(df, as_of)
    for _, r in dist.sort_values("Pending", ascending=False).head(top).iterrows():
        pts.append(("Most pending", f"{r['District']} district, {r['State']}: {r['Pending']} complaints still pending "
                    f"({r['Complaints']} received)."))
    return pd.DataFrame(pts, columns=["Focus", "What the figures show"])


def excel_report(df, vil, alerts, as_of):
    """Static summary workbook for download (values, not formulas)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame(list(kpis(df, as_of).items()), columns=["Figure", "Value"]).to_excel(xl, sheet_name="Summary", index=False)
        focus_areas(df, vil, alerts, as_of).to_excel(xl, sheet_name="Where to focus", index=False)
        department_performance(df, as_of).to_excel(xl, sheet_name="By department", index=False)
        by(df, ["Department", "Category"]).to_excel(xl, sheet_name="By category", index=False)
        by(df, "zone").rename(columns={"zone": "Zone"}).to_excel(xl, sheet_name="By zone", index=False)
        by(df, ["zone", "state"]).rename(columns={"zone": "Zone", "state": "State"}).to_excel(xl, sheet_name="By state", index=False)
        district_table(df, as_of).to_excel(xl, sheet_name="By district", index=False)
        location_table(df, vil).to_excel(xl, sheet_name="By city or town", index=False)
        by(df, "Language").to_excel(xl, sheet_name="By language", index=False)
        cols = ["complaint_id", "date", "zone", "state", "district", "town", "channel", "Language", "Department",
                "Category", "status", "due_date", "resolved_date"]
        out = df[cols].copy()
        out["date"] = out["date"].dt.date
        out.to_excel(xl, sheet_name="Complaints", index=False)
        for ws in xl.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col[:50]) + 2))
    return buf.getvalue()
