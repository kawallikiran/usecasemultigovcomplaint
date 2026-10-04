"""Excel workbook for the early warning. Counts and alerts are live formulas driven by the Settings sheet, so the
district team can change the date, window or thresholds in Excel and see the alerts update."""
import datetime as dt

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .early_warning import LANG_NAMES

ARIAL = "Arial"
BLUE = Font(name=ARIAL, color="0000FF")
BOLD = Font(name=ARIAL, bold=True)
NORMAL = Font(name=ARIAL)
HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
INPUT_FILL = PatternFill("solid", fgColor="FFFF00")


def _header(ws, cols, widths):
    for i, (c, w) in enumerate(zip(cols, widths), 1):
        cell = ws.cell(row=1, column=i, value=c)
        cell.font, cell.fill = BOLD, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def build(cfg, items, groups, alerts, findings, path):
    wb = Workbook()
    ab = wb.active
    ab.title = "About"
    lines = [("Early-warning workbook: grievance trends by district", BOLD),
             ("Places are real (Census 2011 cities and towns, current state boundaries). Complaints are sample data for demonstration.", NORMAL),
             ("", NORMAL), ("How to use", BOLD),
             ("1. Change the yellow cells on the Settings sheet (date, window, thresholds). Blue text = values you can change.", NORMAL),
             ("2. 'Issue trends' recalculates counts, change and alerts for every issue and district.", NORMAL),
             ("3. 'Town counts' shows complaints per town in districts with an alert; counts below the privacy limit show as <5.", NORMAL),
             ("4. 'Complaints' holds every complaint with the system's issue and language.", NORMAL),
             ("5. 'Checks' lists the automated early-warning checks and their results.", NORMAL), ("", NORMAL),
             ("An alert is a recommendation for field verification. It never triggers action by itself.", BOLD),
             ("Complaints still awaiting officer confirmation are counted, using the system's suggested issue.", NORMAL)]
    for i, (t, f) in enumerate(lines, 1):
        ab.cell(row=i, column=1, value=t).font = f
    ab.column_dimensions["A"].width = 120

    st = wb.create_sheet("Settings")
    for c, h in zip("ABC", ("Setting", "Value", "Meaning")):
        st[f"{c}1"], st[f"{c}1"].font = h, BOLD
    rules, high = cfg.get("alert_rules", {}), cfg.get("high_priority", {})
    inputs = [("Alert date (as of)", dt.date.fromisoformat(str(cfg["as_of"])), "Day the alerts are produced", "yyyy-mm-dd"),
              ("Window (days)", int(cfg.get("window_days", 14)), "Last N days compared with the N days before", "0"),
              ("Alert: minimum complaints", int(rules.get("min_complaints", 20)), "Fewer than this never alerts", "0"),
              ("Alert: minimum rise", int(rules.get("min_rise_pct", 50)) / 100, "Rise vs previous window needed to alert", "0%"),
              ("High priority: minimum complaints", int(high.get("min_complaints", 50)), "Alert becomes High at or above this", "0"),
              ("High priority: minimum rise", int(high.get("min_rise_pct", 50)) / 100, "and at or above this rise", "0%"),
              ("Privacy: hide town counts below", int(cfg.get("suppress_below", 5)), "Shown as <N to protect individuals", "0")]
    for i, (label, val, meaning, fmt) in enumerate(inputs, 2):
        st.cell(row=i, column=1, value=label).font = NORMAL
        c = st.cell(row=i, column=2, value=val)
        c.font, c.fill, c.number_format = BLUE, INPUT_FILL, fmt
        st.cell(row=i, column=3, value=meaning).font = NORMAL
    st["B2"].comment = Comment("Source: config/early_warning.yaml (as_of).", "Grievance portal")
    for i, (label, f) in enumerate([("Last window: first day", "=B2-B3+1"), ("Last window: last day", "=B2"),
                                    ("Previous window: first day", "=B2-2*B3+1"), ("Previous window: last day", "=B2-B3")], 10):
        st.cell(row=i, column=1, value=label).font = NORMAL
        c = st.cell(row=i, column=2, value=f)
        c.font, c.number_format = NORMAL, "yyyy-mm-dd"
    st.column_dimensions["A"].width, st.column_dimensions["B"].width, st.column_dimensions["C"].width = 36, 14, 48
    S = {"min": "Settings!$B$4", "rise": "Settings!$B$5", "hmin": "Settings!$B$6", "hrise": "Settings!$B$7",
         "sup": "Settings!$B$8", "c0": "Settings!$B$10", "c1": "Settings!$B$11", "p0": "Settings!$B$12", "p1": "Settings!$B$13"}

    cp = wb.create_sheet("Complaints")
    cols = ["Complaint id", "Date", "Zone", "State", "District", "City / town", "Channel", "Language (true)",
            "Language (detected)", "Issue (system)", "Awaiting officer", "Sensitive", "True issue (test label)",
            "Scenario (test label)", "Complaint text"]
    _header(cp, cols, [12, 11, 13, 16, 20, 20, 9, 14, 15, 13, 10, 9, 15, 17, 70])
    for r, it in enumerate(items, 2):
        vals = [it["complaint_id"], dt.date.fromisoformat(it["date"]), it.get("zone", ""), it["state"], it["district"],
                it["town"], it["channel"], LANG_NAMES.get(it["language_group"], it["language_group"]),
                LANG_NAMES.get(it["detected_language"], it["detected_language"]), it["issue"],
                "yes" if it["provisional"] else "no", "yes" if it["sensitive"] else "no", it.get("true_issue", ""),
                it.get("scenario", ""), it["text"]]
        for c, v in enumerate(vals, 1):
            cell = cp.cell(row=r, column=c, value=v)
            cell.font = NORMAL
            if c == 2:
                cell.number_format = "yyyy-mm-dd"
    last = len(items) + 1
    rng = lambda col: f"Complaints!${col}$2:${col}${last}"
    D, STATE, DIST, TOWN, ISS, PROV = rng("B"), rng("D"), rng("E"), rng("F"), rng("J"), rng("K")

    tr = wb.create_sheet("Issue trends")
    _header(tr, ["Issue code", "Issue", "Zone", "State", "District", "Last window", "Previous window", "Change",
                 "Awaiting officer (last window)", "Alert", "Priority"], [11, 16, 13, 18, 22, 12, 14, 10, 16, 9, 10])
    for r, g in enumerate(sorted(groups, key=lambda g: (g["zone"], g["state"], g["district"], g["issue"])), 2):
        for c, v in enumerate([g["issue"], g["issue_name"], g["zone"], g["state"], g["district"]], 1):
            tr.cell(row=r, column=c, value=v).font = NORMAL
        base = f'{ISS},$A{r},{STATE},$D{r},{DIST},$E{r}'
        tr.cell(row=r, column=6, value=f'=COUNTIFS({base},{D},">="&{S["c0"]},{D},"<="&{S["c1"]})')
        tr.cell(row=r, column=7, value=f'=COUNTIFS({base},{D},">="&{S["p0"]},{D},"<="&{S["p1"]})')
        tr.cell(row=r, column=8, value=f'=IF(G{r}=0,"",(F{r}-G{r})/G{r})').number_format = "0%"
        tr.cell(row=r, column=9, value=f'=COUNTIFS({base},{D},">="&{S["c0"]},{D},"<="&{S["c1"]},{PROV},"yes")')
        tr.cell(row=r, column=10, value=f'=IF(AND(F{r}>={S["min"]},OR(G{r}=0,H{r}>={S["rise"]})),"ALERT","")')
        tr.cell(row=r, column=11, value=f'=IF(J{r}="","",IF(AND(F{r}>={S["hmin"]},OR(G{r}=0,H{r}>={S["hrise"]})),"High","Medium"))')
        for c in range(6, 12):
            tr.cell(row=r, column=c).font = NORMAL
    tr.auto_filter.ref = f"A1:K{len(groups) + 1}"

    tc = wb.create_sheet("Town counts")
    _header(tc, ["Issue code", "State", "District", "City / town", "Complaints (last window)"], [11, 18, 22, 26, 16])
    r = 2
    for a in alerts:
        for town in sorted({it["town"] for it in items if it["area"] == a["area"]}):
            for c, v in enumerate([a["issue"], a["state"], a["district"], town], 1):
                tc.cell(row=r, column=c, value=v).font = NORMAL
            cnt = f'COUNTIFS({ISS},$A{r},{STATE},$B{r},{DIST},$C{r},{TOWN},$D{r},{D},">="&{S["c0"]},{D},"<="&{S["c1"]})'
            tc.cell(row=r, column=5, value=f'=IF({cnt}=0,0,IF({cnt}<{S["sup"]},"<"&{S["sup"]},{cnt}))').font = NORMAL
            r += 1

    ckw = wb.create_sheet("Checks")
    _header(ckw, ["Result", "Severity if failed", "Check", "What was tested", "Expected", "What happened"], [8, 12, 40, 40, 34, 70])
    for r, f in enumerate(sorted(findings, key=lambda f: f.passed), 2):
        for c, v in enumerate(["Pass" if f.passed else "Fail", f.severity, f.probe, f.tested, f.expected, f.actual], 1):
            cell = ckw.cell(row=r, column=c, value=v)
            cell.font, cell.alignment = NORMAL, Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    return path
