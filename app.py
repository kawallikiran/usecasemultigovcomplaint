"""Public Grievance Portal.

Citizens:  file a complaint (typed, spoken or from a frequent-complaint list) and track it, in their language.
Officers:  review complaints assigned to them (approve, edit, reassign, escalate); the citizen is informed.
District:  analytics and early warning.  Technical: quality checks, model performance, audit log, notifications.

Run locally:   streamlit run app.py
In Docker:     docker compose up      (then open http://127.0.0.1:8502)
"""
import datetime as dt
import os
import re
import time

import pandas as pd
import streamlit as st

from grievdesk import analytics as A
from grievdesk import portal as P
from grievdesk import quality as Q
from grievdesk import ui_logic as ui
from grievdesk.common import ui_report as rep
from grievdesk.notify import gateways
from grievdesk.speech import ready_speech, speech_profiles, speech_status
from grievdesk.taxonomy import category_name, category_options

USER = st.session_state.get("officer")            # officer_id when logged in
st.set_page_config(page_title="Public Grievance Portal", layout="wide" if USER else "centered")


@st.cache_resource
def text():
    return P.Text()


@st.cache_resource
def router_for(engine):
    return ui.make_router(engine)[0]


@st.cache_resource
def places():
    return P.places()


T = text()
store = P.Store()
OFFICERS = P.officers()
ROLE = OFFICERS.get(USER, {}).get("role", "")
if "lang" not in st.session_state:
    q = st.query_params.get("lang", "en")
    st.session_state["lang"] = q if q in T.names else "en"


def housekeeping():
    today = dt.date.today().isoformat()
    if st.session_state.get("housekeeping") != today:
        P.auto_escalate_overdue(store, T, current_router().depts)
        P.apply_retention(store, P.settings().get("retention_days", 90))
        st.session_state["housekeeping"] = today


def current_router():
    engine = P.settings().get("engine", "rules")
    if engine != "rules" and engine not in [k for k, _ in ui.ai_options()]:
        engine = "rules"
    return router_for(engine)


def speech_profile():
    ready = dict(ready_speech(ui.ROOT))
    chosen = P.settings().get("speech") or ""
    if chosen == "off":
        return None
    return ready.get(chosen) or (next(iter(ready.values())) if ready else None)


def header():
    lang = st.session_state["lang"]
    left, right = st.columns([3, 1])
    left.markdown(f"### {T(lang, 'title')}")
    left.caption(T(lang, "subtitle"))
    codes = list(T.names)
    new = right.selectbox(T(lang, "language"), codes, index=codes.index(lang), format_func=lambda c: T.names[c])
    if new != lang:
        st.session_state["lang"] = new
        st.query_params["lang"] = new
        st.session_state.pop("pick", None)
        st.rerun()
    return new


def status_block(rec, lang, router):
    v = P.citizen_view(rec, T, lang, router.depts)
    st.info(f"**{T(lang, 'status')}:** {v['status']}")
    st.write(f"**{T(lang, 'dept_label')}:** {v['department']}  \n**{T(lang, 'officer_label')}:** {v['officer']}  \n"
             f"**{T(lang, 'expected_by')}:** {v['expected']}")
    if v["reply"]:
        st.success(f"**{T(lang, 'officer_reply')}:** {v['reply']}")
    if v["timeline"]:
        st.markdown(f"**{T(lang, 'timeline')}**")
        st.markdown("\n".join(f"- {d}: {s}" for d, s in v["timeline"]))
    st.download_button(T(lang, "download_receipt"),
                       P.receipt_html(rec, T, lang, router.depts).encode("utf-8"),
                       file_name=rec["ticket"].replace("/", "-") + ".html", mime="text/html")


# ====================================================================== citizen pages
def file_page():
    lang = header()
    router = current_router()
    done = st.session_state.get("done")
    if done and store.get(done):
        rec = store.get(done)
        st.success(T(lang, "ack_title"))
        st.write(T(lang, "ticket"))
        st.markdown(f"## {rec['ticket']}")
        st.write(T(lang, "keep_number"))
        status_block(rec, lang, router)
        if st.button(T(lang, "new_complaint")):
            st.session_state.pop("done", None)
            st.rerun()
        st.caption(T(lang, "emergency"))
        return

    st.markdown(f"**{T(lang, 'how')}**")
    modes = [T(lang, "type"), T(lang, "speak")]
    mode = st.radio(T(lang, "how"), modes, horizontal=True, label_visibility="collapsed", key="mode")
    st.markdown(f"**{T(lang, 'complaint')}**")
    typed, audio, template = "", None, None
    if mode == modes[0]:
        common = P.common_complaints(lang)
        labels = [T(lang, "common_none")] + [c["text"] for c in common]
        pick = st.selectbox(T(lang, "common"), labels, key="pick")
        if pick != st.session_state.get("last_pick"):
            st.session_state["last_pick"] = pick
            if pick != labels[0]:
                st.session_state["complaint_text"] = pick
        template = next((c for c in common if c["text"] == pick), None)
        typed = st.text_area(T(lang, "complaint_hint"), key="complaint_text", height=130)
        if template and typed.strip() != template["text"]:
            template = None                                  # edited: let the system choose the category
    else:
        recorder = getattr(st, "audio_input", None) or getattr(st, "experimental_audio_input", None)
        audio = recorder(T(lang, "record_hint"), key="voice") if recorder else \
            st.file_uploader(T(lang, "record_hint"), type=["wav", "mp3", "m4a", "ogg", "webm"], key="voice")

    st.markdown(f"**{T(lang, 'where')}**")
    states = list(places())
    default = P.DEFAULT_STATE.get(lang, states[0])
    c1, c2, c3 = st.columns(3)
    state = c1.selectbox(T(lang, "state"), states, index=states.index(default) if default in states else 0)
    district = c2.selectbox(T(lang, "district"), list(places()[state]))
    town = c3.selectbox(T(lang, "town"), places()[state][district])
    locality = st.text_input(T(lang, "locality"), max_chars=120)

    st.markdown(f"**4. {T(lang, 'notify_by')}**")
    ch_keys = ["sms", "whatsapp", "email", "portal"]
    ch = st.radio(T(lang, "notify_by"), ch_keys, horizontal=True, label_visibility="collapsed",
                  format_func=lambda k: T(lang, f"ch_{k}"))
    mobile = email = ""
    if ch in ("sms", "whatsapp"):
        mobile = st.text_input(T(lang, "mobile"), max_chars=10)
    elif ch == "email":
        email = st.text_input(T(lang, "email"))

    st.caption(T(lang, "privacy_notice").format(days=P.settings().get("retention_days", 90)))
    agreed = st.checkbox(T(lang, "consent"))
    if st.button(T(lang, "submit"), type="primary"):
        mobile = re.sub(r"\D", "", mobile or "")
        if not typed.strip() and audio is None:
            st.error(T(lang, "empty_error"))
        elif not agreed:
            st.error(T(lang, "consent_needed"))
        elif (ch in ("sms", "whatsapp") and not re.fullmatch(r"[6-9]\d{9}", mobile)) or \
                (ch == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or "")):
            st.error(T(lang, "contact_needed"))
        else:
            with st.spinner("..."):
                rec = P.submit(router, store, T, lang, "text" if audio is None else "voice",
                               text=typed.strip() or None, audio=audio.getvalue() if audio is not None else None,
                               audio_name=getattr(audio, "name", "complaint.wav") or "complaint.wav",
                               state=state, district=district, town=town, locality=locality, notify_by=ch, mobile=mobile,
                               email=email, speech_profile=speech_profile() if audio is not None else None,
                               template_category=template["category"] if template else None, consent=agreed)
            st.session_state["done"] = rec["ticket"]
            for k in ("complaint_text", "pick", "last_pick"):
                st.session_state.pop(k, None)
            st.rerun()
    st.caption(f"{T(lang, 'handled_note')} {T(lang, 'emergency')}")


def track_page():
    lang = header()
    router = current_router()
    st.markdown(f"**{T(lang, 'track_title')}**")
    ticket = st.text_input(T(lang, "enter_ticket"), value=st.session_state.get("done", ""), placeholder="GRV/2026/00001")
    if st.button(T(lang, "check"), type="primary") or (ticket and st.session_state.get("tracked") == ticket):
        rec = store.get(ticket)
        if not rec:
            st.error(T(lang, "not_found"))
            return
        st.session_state["tracked"] = ticket
        st.caption(f"{T(lang, 'ticket')}: {rec['ticket']} · {rec['created'].replace('T', ' ')[:16]}")
        status_block(rec, lang, router)
        if P.citizen_view(rec, T, lang, router.depts)["can_rate"]:
            st.markdown(f"**{T(lang, 'rate_title')}**")
            ans = st.radio(T(lang, "rate_title"), ["yes", "no"], horizontal=True, index=None, label_visibility="collapsed",
                           format_func=lambda k: T(lang, "sat_yes" if k == "yes" else "sat_no"))
            reason = st.text_area(T(lang, "appeal_reason"), height=80) if ans == "no" else ""
            if ans and st.button(T(lang, "appeal_submit") if ans == "no" else T(lang, "check"), key="rate_btn"):
                new, errs = P.rate(store, rec, ans == "yes", reason)
                if errs:
                    st.error(T(lang, "appeal_reason"))
                else:
                    st.success(T(lang, "thanks") if ans == "yes" else T(lang, "appeal_done"))
        with st.expander(T(lang, "my_data")):
            fix = st.text_input(T(lang, "dr_details"), key="dr_fix")
            c1, c2 = st.columns(2)
            if c1.button(T(lang, "dr_correct")) and fix.strip():
                P.data_request(store, rec, "correct", fix)
                st.success(T(lang, "dr_done"))
            if c2.button(T(lang, "dr_delete")):
                P.data_request(store, rec, "delete")
                st.success(T(lang, "dr_done"))


def login_page():
    header()
    st.markdown("**Officer login**")
    oid = st.selectbox("Officer", list(OFFICERS), format_func=lambda k: f"{OFFICERS[k]['name']}, {OFFICERS[k]['designation']}")
    pw = st.text_input("Password", type="password")
    if st.button("Log in", type="primary"):
        if pw and pw == os.environ.get("OFFICER_PASSWORD", "officer@123"):
            st.session_state["officer"] = oid
            st.rerun()
        st.error("Incorrect password.")


# ====================================================================== complaints (officers)
def complaint_cards(recs, router, show_assignee, slot):
    """One small card per complaint; 'Open' shows the full details."""
    for r in recs:
        c = P.card(r, router.depts)
        with st.container(border=True):
            top, btn = st.columns([5, 1])
            flags = " · ".join(x for x in ("Sensitive" if c["sensitive"] else "", "Voice" if c["voice"] else "",
                                           c["priority"].capitalize() if c["priority"] != "normal" else "") if x)
            top.markdown(f"**{c['ticket']}** · {c['status']}" + (f" · :red[{flags}]" if flags else ""))
            top.write(c["summary"])
            due = f":red[Overdue, due {c['due']}]" if c["overdue"] else f"Due {c['due']}"
            top.caption(f"{c['place']} · {c['department']} · {due}"
                        + (f" · Assigned to {c['assigned']}" if show_assignee else ""))
            if btn.button("Open", key=f"open_{slot}_{c['ticket']}"):
                st.session_state[f"open_{slot}"] = c["ticket"]
                st.rerun()


def open_complaint(router, back_label, slot):
    """Full details of the opened complaint; False if nothing is open on this page."""
    ticket = st.session_state.get(f"open_{slot}")
    rec = store.get(ticket) if ticket else None
    if not rec:
        return False
    if st.button(f"\u2190 {back_label}"):
        st.session_state.pop(f"open_{slot}", None)
        st.rerun()
    review_panel(rec, router)
    return True


def my_page():
    router = current_router()
    me = OFFICERS[USER]
    if open_complaint(router, "Back to my complaints", "mine"):
        return
    st.title("My complaints")
    st.caption(f"{me['name']}, {me['designation']}")
    for c, (k, v) in zip(st.columns(4), P.my_counts(store, USER).items()):
        c.metric(k, v)
    show = st.radio("Show", ["Needs action", "All"], horizontal=True, label_visibility="collapsed")
    recs = P.queue(store, USER, "mine", show)
    if not recs:
        st.info("Nothing waiting for you." if show == "Needs action" else "No complaints assigned to you yet.")
        return
    complaint_cards(recs, router, show_assignee=False, slot="mine")


def all_page():
    router = current_router()
    if open_complaint(router, "Back to all complaints", "all"):
        return
    st.title("All complaints")
    for c, (k, v) in zip(st.columns(4), list(P.summary(store).items())[:4]):
        c.metric(k, v)
    with st.expander("Who has what (all officers)"):
        st.dataframe(pd.DataFrame(P.workload(store)), hide_index=True)
    c1, c2 = st.columns(2)
    names = {oid: f"{o['name']}, {o['designation']}" for oid, o in OFFICERS.items() if o["role"] != "admin"}
    who = c1.selectbox("Assigned to", ["All"] + list(names), format_func=lambda k: "All officers" if k == "All" else names[k])
    show = c2.selectbox("Status", ["Needs action", "All"])
    recs = P.queue(store, USER, "all", show)
    if who != "All":
        recs = [r for r in recs if r.get("assigned_to") == who]
    if not recs:
        st.info("No complaints to show.")
        return
    complaint_cards(recs, router, show_assignee=True, slot="all")


def place_text(rec):
    parts = []
    for x in (rec.get("locality"), rec.get("town"), rec.get("district"), rec.get("state"), rec.get("zone")):
        if x and (not parts or parts[-1] != x):         # "Chennai, Chennai" -> "Chennai"
            parts.append(x)
    return ", ".join(parts)


def review_panel(rec, router):
    s = rec.get("suggestion") or {}
    opened = st.session_state.setdefault(f"opened_{rec['ticket']}", time.time())
    st.subheader(rec["ticket"])
    st.markdown(f"**Assigned to:** {P.officer_label(rec['assigned_to'])} · **Status:** {P.STATUS_EN.get(rec['status'], rec['status'])}")
    st.caption(f"Received {rec['created'].replace('T', ' ')[:16]} · {place_text(rec)} · "
               f"Inform by: {rec.get('notify_by', 'portal')} · Due {dt.date.fromisoformat(rec['due_date']).strftime('%d-%m-%Y')}")
    if rec.get("audio") and os.path.exists(os.path.join(ui.ROOT, rec["audio"])):
        st.audio(os.path.join(ui.ROOT, rec["audio"]))
    if rec["transcript"] == "pending":
        st.warning("Voice complaint not yet typed. Listen and type what the citizen said.")
        words = st.text_area("What the citizen said", key=f"tr_{rec['ticket']}")
        if st.button("Save and sort", type="primary"):
            new, errs = P.save_transcript(router, store, rec["ticket"], words, USER)
            for e in errs:
                st.error(e)
            if new:
                st.rerun()
        return
    st.write(rec.get("text") or "")
    if s.get("sensitive_categories"):
        st.error("Sensitive complaint (" + ", ".join(s["sensitive_categories"]).replace("_", " ")
                 + "). Only the designated or senior officer may approve it.")
    with st.expander("System suggestion (check before approving)"):
        st.write(f"Department: {s.get('department_name') or 'Not identified'} · Priority: {(s.get('priority') or '-').capitalize()} · "
                 f"Language: {s.get('language_name') or '-'}")
        st.markdown("\n".join(f"- {r}" for r in s.get("reasons") or []))
    if rec["status"] == "approved":
        st.success(f"Approved. Reply sent: {rec.get('reply', '')}")
    elif not P.can_act(USER, rec):
        st.info("Assigned to another officer. You can view it but not act on it.")
    else:
        dept_keys = list(router.depts)
        c1, c2, c3 = st.columns(3)
        dept = c1.selectbox("Department", dept_keys, index=dept_keys.index(rec["department"]) if rec.get("department") in dept_keys else 0,
                            format_func=lambda k: router.depts[k]["name"], key=f"d_{rec['ticket']}")
        cats = category_options(dept)
        ckeys = [k for k, _ in cats]
        cat = c2.selectbox("Category", ckeys, index=ckeys.index(rec["category"]) if rec.get("category") in ckeys else 0,
                           format_func=lambda k: dict(cats)[k], key=f"c_{rec['ticket']}_{dept}")
        prios = ["normal", "high", "critical"]
        pr = c3.selectbox("Priority", prios, index=prios.index(rec.get("priority", "normal")) if rec.get("priority") in prios else 0,
                          format_func=str.capitalize, key=f"p_{rec['ticket']}")
        reply = st.text_area("Reply to the citizen", value=s.get("draft_reply") or "", key=f"r_{rec['ticket']}", height=110)
        note = st.text_input("Note for the record (needed to reassign or escalate)", key=f"n_{rec['ticket']}")
        b1, b2, b3 = st.columns(3)
        if b1.button("Approve and inform citizen", type="primary"):
            if dept != rec.get("department"):
                st.error("You changed the department. Use 'Reassign' so the right officer reviews it.")
            else:
                res = P.approve(store, T, router.depts, rec, USER, reply, category=cat, priority=pr,
                                review_seconds=time.time() - opened)
                for e in res[1]:
                    st.error(e)
                if res[0]:
                    st.success(f"Approved. Message to citizen: {res[2]['channel']} {res[2]['to']} ({res[2]['status']}).")
        if b2.button("Reassign to selected department"):
            new, errs = P.reassign(store, router, rec, USER, dept, note, category=cat, priority=pr)
            for e in errs:
                st.error(e)
            if new:
                st.success(f"Reassigned to {P.officer_label(new['assigned_to'])}.")
        if b3.button("Escalate to senior officer"):
            new, errs = P.escalate(store, T, router.depts, rec, USER, note)
            for e in errs:
                st.error(e)
            if new:
                st.success("Escalated to the Additional Collector.")
    with st.expander("History"):
        st.dataframe(pd.DataFrame([r for r in P.audit_rows(store) if r["Complaint no."] == rec["ticket"]]), hide_index=True)




# ====================================================================== analytics
@st.cache_data(show_spinner=False)
def ew_cards(n_live, as_of):
    return ui.run_early_warning(P.portal_rows_for_analytics(store), as_of=as_of)


def analytics_page():
    st.title("Analytics")
    base = dt.date.fromisoformat(str(ui.load_config(ui.EW_CONFIG)["as_of"]))
    df, vil = A.load(P.portal_rows_for_analytics(store))
    st.caption("Places are real (Census 2011 cities and towns, current state boundaries). Complaint figures include sample data.")
    c1, c2, c3 = st.columns(3)
    zone = c1.selectbox("Zone", ["All"] + sorted(df["zone"].unique()))
    zdf = df if zone == "All" else df[df["zone"] == zone]
    state = c2.selectbox("State / UT", ["All"] + sorted(zdf["state"].unique()))
    sdf = zdf if state == "All" else zdf[zdf["state"] == state]
    district = c3.selectbox("District", ["All"] + sorted(sdf["district"].unique()))
    c4, c5, c6 = st.columns(3)
    department = c4.selectbox("Department", ["All"] + sorted(df["Department"].unique()))
    start = c5.date_input("From", value=df["date"].min().date())
    as_of = c6.date_input("To (report date)", value=base)
    f = A.filter_df(df, zone, state, district, department, start, as_of)
    k = A.kpis(f, as_of)
    for c, key in zip(st.columns(4), ["Complaints", "Pending", "Overdue", "Average days to resolve"]):
        c.metric(key, k[key])

    st.subheader("Where to focus")
    cfg, _, _, cards, _ = ew_cards(len(store.all()), str(as_of))
    alerts = [a for a, _, _ in cards if (zone == "All" or a.get("zone") == zone) and (state == "All" or a.get("state") == state)
              and (district == "All" or a.get("district") == district)]
    for a, lines, notes in cards:
        if a in alerts:
            (st.error if a["priority"] == "High" else st.warning)("  \n".join([f"**{lines[0]}**"] + lines[1:]))
    st.dataframe(A.focus_areas(f, vil, alerts, as_of).head(6), hide_index=True)

    g1, g2 = st.columns(2)
    g1.markdown("**Complaints by department**")
    g1.bar_chart(A.by(f, "Department").set_index("Department"))
    g2.markdown("**Complaints over time**")
    g2.line_chart(A.trend(f))
    with st.expander("More breakdowns: category, zone, state, district, town, performance, language"):
        st.markdown("**By category**")
        st.dataframe(A.by(f, ["Department", "Category"]), hide_index=True)
        l1, l2 = st.columns(2)
        l1.markdown("**By zone**")
        l1.bar_chart(A.by(f, "zone").rename(columns={"zone": "Zone"}).set_index("Zone"))
        l2.markdown("**By state (top 15)**")
        l2.bar_chart(A.by(f, "state", top=15).rename(columns={"state": "State"}).set_index("State"))
        st.markdown("**By district**")
        st.dataframe(A.district_table(f, as_of), hide_index=True)
        st.markdown("**By city / town**")
        st.dataframe(A.location_table(f, vil), hide_index=True)
        st.markdown("**Service performance by department**")
        st.dataframe(A.department_performance(f, as_of), hide_index=True)
        k1, k2 = st.columns(2)
        k1.markdown("**By language**")
        k1.bar_chart(A.by(f, "Language").set_index("Language"))
        k2.markdown("**By channel**")
        k2.bar_chart(A.by(f, "channel").rename(columns={"channel": "Channel"}).set_index("Channel"))
    d1, d2 = st.columns(2)
    d1.download_button("Download analytics (Excel)", A.excel_report(f, vil, alerts, as_of), file_name="grievance_analytics.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("Download complaint register (CSV)", P.register_csv(store, current_router().depts).encode("utf-8"),
                       file_name="complaint_register.csv", mime="text/csv")


# ====================================================================== technical
def assurance_page():
    st.title("Assurance")
    st.caption("Answers to the three critics in one place. Figures are live; details are folded away.")
    router = current_router()
    sets = P.settings()
    days = sets.get("retention_days", 90)
    fig = P.assurance_figures(store)
    tech = Q.tech_summary()
    max_deadline = max(d["sla_days"] for d in router.depts.values())

    st.subheader("1. Does it work well, for everyone?")
    c1, c2, c3 = st.columns(3)
    c1.metric("Quality checks passed", f"{tech['passed']} of {tech['total']}" if tech["total"] else "Not run")
    w = tech["weakest"]
    c2.metric("Weakest language", f"{router.language_name(w['Language'])}: {w['Sorted correctly']}" if w else "-")
    c3.metric("Approvals under 30 seconds", f"{fig['quick_approvals']} of {fig['approvals']}",
              help="Very quick approvals may mean the officer did not really read the complaint.")
    if not tech["total"]:
        st.caption("Quality checks have not been run yet: run them on the Quality checks page.")
    elif tech["serious"]:
        st.warning(f"{len(tech['serious'])} serious problem(s) open. These are the Phase 2 fixes.")
    elif tech["total"]:
        st.success("No serious problems open.")
    with st.expander("Details and try it yourself"):
        if tech["serious"]:
            st.dataframe(pd.DataFrame(tech["serious"]), hide_index=True)
        trial = st.text_input("Type any complaint to see how it is sorted (nothing is saved)")
        if trial.strip():
            st.dataframe(pd.DataFrame(ui.summary_rows(router.process({"id": "TRY", "text": trial}))), hide_index=True)

    st.subheader("2. Is it lawful?")
    rows = Q.compliance_rows(days, max_deadline, fig)
    c1, c2, c3 = st.columns(3)
    c1.metric("Requirements met", f"{sum(r['Status'] == 'Met' for r in rows)} of {len(rows)}")
    c2.metric("Consent recorded", f"{fig['consent_share']}%" if fig["consent_share"] is not None else "-")
    c3.metric("Citizen data requests open", fig["open_data_requests"])
    with st.expander("Compliance map"):
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.caption("Our reading of the law, for the demo; to be confirmed by the group's legal member.")
        open_reqs = [q for q in P.data_requests(store) if q["status"] == "open"]
        if open_reqs and ROLE == "admin":
            st.dataframe(pd.DataFrame(open_reqs), hide_index=True)
            rid = st.selectbox("Data request", [q["id"] for q in open_reqs])
            if st.button("Mark as done (a deletion request erases the contact details)"):
                P.close_data_request(store, rid, USER)
                st.rerun()

    st.subheader("3. What happens to the citizen when something goes wrong?")
    c1, c2, c3 = st.columns(3)
    c1.metric("Sent to the wrong office first", fig["reassigned"])
    c2.metric("Overdue now", fig["overdue_open"], help=f"{fig['auto_escalated']} moved to the senior officer automatically")
    c3.metric("Appeals", fig["appeals"])
    with st.expander("Citizen's view"):
        st.dataframe(pd.DataFrame(Q.stakeholder_rows(fig, days)), hide_index=True)


def quality_page():
    st.title("Quality checks")
    st.write("Each check tests one way the system could let citizens down. Run them after any change to keyword lists, "
             "languages or the sorting engine.")
    set_name = st.selectbox("Test set", list(ui.TEST_SETS))
    cfg_path = ui.TEST_SETS[set_name]
    profile, blocked = None, False
    if ui.is_ai_set(cfg_path):
        opts = ui.ai_options()
        if not opts:
            blocked = True
            st.warning("This test set needs an AI service, and none is ready. See Settings.")
        else:
            lab = st.selectbox("AI service", [l for _, l in opts])
            profile = dict((l, k) for k, l in opts)[lab].split(":", 1)[1]
    if st.button("Run checks", type="primary", disabled=blocked):
        with st.spinner("Running checks"):
            ui.run_tests(cfg_path, profile)
    data, when = rep.load_findings(ui.output_dir(cfg_path, profile))
    if not data:
        st.info("Not run yet for this test set.")
        return
    st.caption(f"Last run {when}")
    st.dataframe(pd.DataFrame(Q.plain_checks(data["findings"])), hide_index=True)
    out_dir = ui.output_dir(cfg_path, profile)
    c1, c2 = st.columns(2)
    with open(os.path.join(out_dir, "report.md"), "rb") as fh:
        c1.download_button("Detailed report", fh.read(), file_name="quality_report.md")
    with open(os.path.join(out_dir, "findings.csv"), "rb") as fh:
        c2.download_button("All results (CSV)", fh.read(), file_name="quality_results.csv")
    with st.expander("Try sample complaints"):
        samples = ui.load_samples(None)
        by_id = {r["id"]: r for r in samples}
        sid = st.selectbox("Sample", list(by_id), format_func=lambda k: f"{k}: {by_id[k]['text'][:70]}")
        if st.button("Sort this sample"):
            out = current_router().process({"id": sid, "text": by_id[sid]["text"]})
            st.dataframe(pd.DataFrame(ui.summary_rows(out)), hide_index=True)


def performance_page():
    st.title("Model performance")
    router = current_router()
    st.caption(f"Sorting engine in use: {router.engine_label}")
    sets, langs = Q.performance()
    st.subheader("On test sets (from the last quality-check runs)")
    st.dataframe(pd.DataFrame(sets), hide_index=True)
    if langs:
        st.subheader("By language")
        st.dataframe(pd.DataFrame([{**r, "Language": router.language_name(r["Language"])} for r in langs]), hide_index=True)
    st.subheader("On real use: do officers agree with the suggestion?")
    ag = P.review_agreement(store)
    if not ag:
        st.info("No complaints approved yet.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Approved by officers", ag["approved"])
        c2.metric("Suggested department kept", f"{round(100 * ag['kept'] / ag['approved'])}%")
        c3.metric("Changed by officer", ag["changed"])


def audit_page():
    st.title("Audit log")
    st.write("Every action on every complaint: who did it, when, and what changed. Personal numbers are hidden.")
    rows = P.audit_rows(store)
    if not rows:
        st.info("Nothing recorded yet.")
        return
    df = pd.DataFrame(rows)
    a = st.selectbox("Action", ["All"] + sorted(df["Action"].unique()))
    if a != "All":
        df = df[df["Action"] == a]
    st.dataframe(df, hide_index=True)
    st.download_button("Download audit log (CSV)", P.audit_csv(store).encode("utf-8"), file_name="audit_log.csv", mime="text/csv")


def notifications_page():
    st.title("Notifications")
    g = gateways()
    st.write("Message gateways: " + " · ".join(f"{k.upper() if k == 'sms' else k.capitalize()}: {'set up' if v else 'not set up'}"
                                              for k, v in g.items() if k != "portal"))
    items = store.outbox.all()
    if not items:
        st.info("No messages yet.")
        return
    st.dataframe(pd.DataFrame(items[::-1]), hide_index=True)


def settings_page():
    st.title("Settings")
    s = P.settings()
    options = [("rules", "Rules (offline)")] + ui.ai_options()
    keys = [k for k, _ in options]
    cur = s.get("engine", "rules") if s.get("engine", "rules") in keys else "rules"
    eng = st.selectbox("Complaint sorting", keys, index=keys.index(cur), format_func=lambda k: dict(options)[k])
    if eng != "rules":
        st.caption("Complaint text (with personal numbers hidden) is sent to this service.")
    profiles = speech_profiles(ui.ROOT)
    sp_ready = [n for n, _ in ready_speech(ui.ROOT)]
    sp_keys = ["off"] + sp_ready
    sp_cur = s.get("speech") if s.get("speech") in sp_keys else (sp_ready[0] if sp_ready else "off")
    sp = st.selectbox("Speech-to-text for voice complaints", sp_keys, index=sp_keys.index(sp_cur),
                      format_func=lambda k: "Off (officers type voice complaints)" if k == "off" else profiles[k]["label"])
    keep = st.number_input("Delete contact details and recordings this many days after a complaint is closed",
                           min_value=7, max_value=365, value=int(s.get("retention_days", 90)))
    if st.button("Save settings", type="primary"):
        P.save_settings({"engine": eng, "speech": sp, "retention_days": int(keep)})
        st.success("Saved.")
    with st.expander("AI services and their status"):
        st.dataframe(pd.DataFrame(ui.ai_status_rows()), hide_index=True)
    with st.expander("Speech services and their status"):
        st.dataframe(pd.DataFrame([{"Service": p["label"], "Ready": "Yes" if speech_status(p)[0] else "No",
                                    "Detail": speech_status(p)[1]} for p in profiles.values()]), hide_index=True)
    with st.expander("Languages"):
        st.dataframe(pd.DataFrame(ui.language_table(current_router())), hide_index=True)
    with st.expander("Officers"):
        st.dataframe(pd.DataFrame(OFFICERS.values()), hide_index=True)


def logout_page():
    st.session_state.pop("officer", None)
    st.rerun()


# ====================================================================== navigation (left menu)
st.markdown("""<style>
section[data-testid="stSidebar"] {min-width: 230px; max-width: 230px;}
section[data-testid="stSidebar"] a span {font-size: 0.95rem;}
</style>""", unsafe_allow_html=True)
housekeeping()
lang = st.session_state["lang"]
page = lambda fn, title, path, icon, **k: st.Page(fn, title=title, url_path=path, icon=icon, **k)
citizen = [page(file_page, T(lang, "nav_file"), "file", ":material/edit_note:", default=not USER),
           page(track_page, T(lang, "nav_track"), "track", ":material/search:")]
if USER:
    work = [page(my_page, "My complaints", "my", ":material/inbox:", default=True)]
    if ROLE in ("supervisor", "admin"):
        work.append(page(all_page, "All complaints", "all", ":material/view_list:"))
    work.append(page(analytics_page, "Analytics", "analytics", ":material/insights:"))
    tech = []
    if ROLE in ("supervisor", "admin"):
        tech.append(page(assurance_page, "Assurance", "assurance", ":material/verified_user:"))
    if ROLE == "admin":
        tech += [page(quality_page, "Quality checks", "quality", ":material/fact_check:"),
                 page(performance_page, "Model performance", "performance", ":material/speed:")]
    if ROLE in ("supervisor", "admin"):
        tech += [page(audit_page, "Audit log", "audit", ":material/history:"),
                 page(notifications_page, "Notifications", "notifications", ":material/notifications:")]
    if ROLE == "admin":
        tech.append(page(settings_page, "Settings", "settings", ":material/settings:"))
    pages = {"Work": work}
    if tech:
        pages["Technical"] = tech
    pages["Citizen services"] = citizen
    pages["Account"] = [page(logout_page, "Log out", "logout", ":material/logout:")]
    with st.sidebar:
        st.caption(f"{OFFICERS[USER]['name']}  \n{OFFICERS[USER]['designation']}")
else:
    pages = citizen + [page(login_page, T(lang, "nav_officer"), "officer", ":material/login:")]
nav = st.navigation(pages)
nav.run()
