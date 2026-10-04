"""Public Grievance Portal: citizen pages (file and track a complaint) and officer pages (work queue,
district dashboard, reports, system checks, settings).

Run locally:   streamlit run app.py
In Docker:     docker compose up      (then open http://127.0.0.1:8502)
"""
import os
import re

import pandas as pd
import streamlit as st

from grievdesk import portal as P
from grievdesk import ui_logic as ui
from grievdesk.common import ui_report as rep
from grievdesk.speech import ready_speech, speech_profiles, speech_status

OFFICER = bool(st.session_state.get("officer"))
st.set_page_config(page_title="Public Grievance Portal", layout="wide" if OFFICER else "centered")


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
cfg_settings = P.settings()
if "lang" not in st.session_state:
    q = st.query_params.get("lang", "en")
    st.session_state["lang"] = q if q in T.names else "en"


def current_router():
    engine = cfg_settings.get("engine", "rules")
    if engine != "rules" and engine not in [k for k, _ in ui.ai_options()]:
        engine = "rules"                                   # chosen service not available: fall back safely
    return router_for(engine)


def speech_profile():
    ready = dict(ready_speech(ui.ROOT))
    chosen = cfg_settings.get("speech") or ""
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
        st.rerun()
    return new


def village_label(lang, block, village):
    if lang in ("hi", "cg", "mr"):
        return dict(places().get(block, [])).get(village, village)
    return village


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
        status, detail = P.citizen_status(rec, T, lang, router.depts)
        st.write(f"**{T(lang, 'status')}:** {status}" + (f" ({detail[0]})" if detail else ""))
        st.download_button(T(lang, "download_receipt"),
                           P.receipt_html(rec, T, lang, router.depts, village_label(lang, rec["block"], rec["village"])).encode("utf-8"),
                           file_name=rec["ticket"].replace("/", "-") + ".html", mime="text/html")
        if st.button(T(lang, "new_complaint")):
            st.session_state.pop("done", None)
            st.rerun()
        st.caption(T(lang, "emergency"))
        return

    st.markdown(f"**{T(lang, 'how')}**")
    modes = [T(lang, "type"), T(lang, "speak")]
    mode = st.radio(T(lang, "how"), modes, horizontal=True, label_visibility="collapsed", key="mode")
    st.markdown(f"**{T(lang, 'complaint')}**")
    typed, audio = "", None
    if mode == modes[0]:
        typed = st.text_area(T(lang, "complaint_hint"), key="complaint_text", height=140)
    else:
        recorder = getattr(st, "audio_input", None) or getattr(st, "experimental_audio_input", None)
        if recorder:
            audio = recorder(T(lang, "record_hint"), key="voice")
        else:
            audio = st.file_uploader(T(lang, "record_hint"), type=["wav", "mp3", "m4a", "ogg", "webm"], key="voice")

    st.markdown(f"**{T(lang, 'where')}**")
    c1, c2, c3 = st.columns(3)
    c1.selectbox(T(lang, "district"), [P.DISTRICT])
    blocks = list(places())
    block = c2.selectbox(T(lang, "block"), blocks)
    villages = [v for v, _ in places()[block]]
    village = c3.selectbox(T(lang, "village"), villages, format_func=lambda v: village_label(lang, block, v))
    mobile = st.text_input(T(lang, "mobile"), max_chars=10)

    if st.button(T(lang, "submit"), type="primary"):
        if not typed.strip() and audio is None:
            st.error(T(lang, "empty_error"))
        else:
            with st.spinner("..."):
                rec = P.submit(router, store, lang, "text" if audio is None else "voice",
                               text=typed.strip() or None, audio=audio.getvalue() if audio is not None else None,
                               audio_name=getattr(audio, "name", "complaint.wav") or "complaint.wav",
                               block=block, village=village, mobile=re.sub(r"\D", "", mobile or ""),
                               speech_profile=speech_profile() if audio is not None else None)
            st.session_state["done"] = rec["ticket"]
            st.session_state.pop("complaint_text", None)
            st.rerun()
    st.caption(f"{T(lang, 'handled_note')} {T(lang, 'emergency')}")


def track_page():
    lang = header()
    router = current_router()
    st.markdown(f"**{T(lang, 'track_title')}**")
    ticket = st.text_input(T(lang, "enter_ticket"), value=st.session_state.get("done", ""), placeholder="GRV/2026/00001")
    if st.button(T(lang, "check"), type="primary") or st.session_state.get("tracked") == ticket:
        rec = store.get(ticket)
        if not rec:
            st.error(T(lang, "not_found"))
            return
        st.session_state["tracked"] = ticket
        status, detail = P.citizen_status(rec, T, lang, router.depts)
        st.info(f"**{T(lang, 'status')}:** {status}" + "".join(f"  \n{d}" for d in detail))
        st.caption(f"{T(lang, 'ticket')}: {rec['ticket']} · {rec['created'].replace('T', ' ')[:16]}")
        st.download_button(T(lang, "download_receipt"),
                           P.receipt_html(rec, T, lang, router.depts, village_label(lang, rec["block"], rec["village"])).encode("utf-8"),
                           file_name=rec["ticket"].replace("/", "-") + ".html", mime="text/html")


def login_page():
    header()
    st.markdown("**Officer login**")
    pw = st.text_input("Password", type="password")
    if st.button("Log in", type="primary"):
        if pw and pw == os.environ.get("OFFICER_PASSWORD", "officer@123"):
            st.session_state["officer"] = True
            st.rerun()
        st.error("Incorrect password.")


# ====================================================================== officer pages
def queue_page():
    st.title("Work queue")
    router = current_router()
    m = P.summary(store)
    cols = st.columns(len(m))
    for c, (k, v) in zip(cols, m.items()):
        c.metric(k, v)
    show = st.radio("Show", ["Needs action", "All"], horizontal=True)
    recs = P.queue(store, show)
    if not recs:
        st.info("No complaints to show.")
        return
    st.dataframe(pd.DataFrame(P.queue_rows(recs, router.depts)), hide_index=True)
    ticket = st.selectbox("Open complaint", [r["ticket"] for r in recs])
    rec = store.get(ticket)
    s = rec.get("suggestion") or {}
    st.subheader(ticket)
    st.caption(f"Received {rec['created'].replace('T', ' ')[:16]} · {rec['district']}, {rec['block']}, {rec['village']}"
               + (f" · Mobile {rec['mobile']}" if rec.get("mobile") else ""))
    if rec.get("audio") and os.path.exists(os.path.join(ui.ROOT, rec["audio"])):
        st.audio(os.path.join(ui.ROOT, rec["audio"]))
    if rec["transcript"] == "pending":
        st.warning("Voice complaint not yet typed. Listen and type what the citizen said.")
        with st.form("transcribe"):
            words = st.text_area("What the citizen said")
            name = st.text_input("Your name")
            if st.form_submit_button("Save and sort", type="primary"):
                new, errs = P.save_transcript(router, store, ticket, words, name)
                for e in errs:
                    st.error(e)
                if new:
                    st.rerun()
        return
    st.write(rec.get("text") or "")
    if s.get("sensitive_categories"):
        st.error("Sensitive complaint (" + ", ".join(s["sensitive_categories"]).replace("_", " ")
                 + "). Only the designated officer may act on it.")
    st.write(f"**Suggested department:** {router.depts.get(rec.get('department') or '', {}).get('name', 'Not identified')}"
             f" · **Priority:** {(s.get('priority') or '-').capitalize()} · **Language:** {s.get('language_name') or '-'}")
    with st.expander("Why this suggestion"):
        st.markdown("\n".join(f"- {r}" for r in s.get("reasons") or []))
    if rec["status"] in ("resolved", "escalated"):
        st.info(f"Status: {P.STATUS_EN[rec['status']]}." + (f" Reply: {rec['reply']}" if rec.get("reply") else ""))
    else:
        dept_keys = list(router.depts)
        with st.form("action"):
            name = st.text_input("Your name")
            label = st.radio("Action", list(P.ACTIONS), horizontal=True, index=None)
            dept = st.selectbox("Department (for forwarding)", dept_keys,
                                index=dept_keys.index(rec["department"]) if rec.get("department") in dept_keys else 0,
                                format_func=lambda k: router.depts[k]["name"])
            note = st.text_input("Note for the record")
            reply = st.text_area("Reply to the citizen (for 'Send reply and close')", value=s.get("draft_reply") or "")
            if st.form_submit_button("Save", type="primary"):
                new, errs = P.act(store, rec, P.ACTIONS.get(label), name, dept, note, reply)
                for e in errs:
                    st.error(e)
                if new:
                    st.success(f"Saved: {P.STATUS_EN[new['status']]}.")
    with st.expander("History"):
        st.dataframe(pd.DataFrame(rec.get("history", [])), hide_index=True)


@st.cache_data(show_spinner=False)
def dashboard_data(stamp, n_live, as_of):
    return ui.run_early_warning(P.live_rows_for_dashboard(store), as_of=as_of)


def dashboard_page():
    st.title("District dashboard")
    import datetime as _dt
    base = ui.load_config(ui.EW_CONFIG)["as_of"]
    c1, c2 = st.columns([1, 3])
    as_of = c1.date_input("Report date", value=_dt.date.fromisoformat(str(base)))
    if c2.button("Refresh"):
        st.session_state["dash"] = st.session_state.get("dash", 0) + 1
    with st.spinner("Updating"):
        cfg, items, groups, cards, _ = dashboard_data(st.session_state.get("dash", 0), len(store.all()), str(as_of))
    rules = cfg.get("alert_rules", {})
    st.caption(f"Date {cfg['as_of']}. Last {cfg['window_days']} days compared with the {cfg['window_days']} days before. "
               f"An issue in a block is flagged at {rules.get('min_complaints')}+ complaints and a rise of "
               f"{rules.get('min_rise_pct')}%+. Village counts below {cfg.get('suppress_below')} are hidden.")
    if not cards:
        st.success("No emerging issues in this period.")
    for a, lines, notes in cards:
        (st.error if a["priority"] == "High" else st.warning)("  \n".join([f"**{lines[0]}**"] + lines[1:]))
        for n_ in notes:
            st.caption(n_)
        with st.expander(f"Villages and daily trend: {a['issue_name']}, {a['block']}"):
            c1, c2 = st.columns([1, 2])
            c1.dataframe(pd.DataFrame(ui.village_rows(a)), hide_index=True)
            c2.bar_chart(pd.DataFrame(ui.daily_rows(a, cfg)).set_index("Date"))
    st.subheader("All issues by block")
    st.dataframe(pd.DataFrame(ui.trend_rows(groups)), hide_index=True)


def reports_page():
    st.title("Reports and downloads")
    router = current_router()
    m = P.summary(store)
    cols = st.columns(len(m))
    for c, (k, v) in zip(cols, m.items()):
        c.metric(k, v)
    st.subheader("Complaint register")
    rows = P.queue_rows(store.all(), router.depts)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    c1, c2 = st.columns(2)
    c1.download_button("Complaint register (CSV)", P.register_csv(store, router.depts).encode("utf-8"),
                       file_name="complaint_register.csv", mime="text/csv")
    c2.download_button("Officer actions (CSV)", P.actions_csv(store).encode("utf-8"),
                       file_name="officer_actions.csv", mime="text/csv")
    st.subheader("District dashboard")
    out = os.path.join(ui.ROOT, "reports", "early_warning")
    files = [("Dashboard workbook (Excel)", "early_warning.xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             ("Issues by block (CSV)", "issue_trends.csv", "text/csv"), ("Alerts (text)", "alerts.txt", "text/plain")]
    cols = st.columns(3)
    for c, (label, name, mime) in zip(cols, files):
        path = os.path.join(out, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                c.download_button(label, fh.read(), file_name=name, mime=mime)
        else:
            c.caption(f"{label}: open the District dashboard once to create it.")


def checks_page():
    st.title("System checks")
    st.write("Runs the eight standard checks (bias, robustness, privacy, over-reliance, consistency, failure handling, "
             "factual errors, adversarial inputs) on a test set, and lets you try sample complaints without registering them.")
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
    if data:
        f, met = data["findings"], data["metrics"]
        st.write(f"Last run {when}: {len(f)} checks, {sum(not x['passed'] for x in f)} failed. "
                 f"Accuracy {met['baseline_accuracy']}; sent to an officer {met['share_sent_to_human']}.")
        st.dataframe(pd.DataFrame(rep.by_test_rows(f)), hide_index=True)
        failed = rep.failed_rows(f)
        if failed:
            st.markdown("**Problems found, most serious first**")
            st.dataframe(pd.DataFrame(failed), hide_index=True)
        out_dir = ui.output_dir(cfg_path, profile)
        c1, c2 = st.columns(2)
        with open(os.path.join(out_dir, "report.md"), "rb") as fh:
            c1.download_button("Check report", fh.read(), file_name="check_report.md")
        with open(os.path.join(out_dir, "findings.csv"), "rb") as fh:
            c2.download_button("All results (CSV)", fh.read(), file_name="check_results.csv")
    with st.expander("Try sample complaints"):
        samples = ui.load_samples(None)
        by_id = {r["id"]: r for r in samples}
        sid = st.selectbox("Sample", list(by_id), format_func=lambda k: f"{k}: {by_id[k]['text'][:70]}")
        if st.button("Sort this sample"):
            out = current_router().process({"id": sid, "text": by_id[sid]["text"]})
            st.dataframe(pd.DataFrame(ui.summary_rows(out)), hide_index=True)


def settings_page():
    st.title("Settings")
    s = dict(cfg_settings)
    options = [("rules", "Rules (offline)")] + ui.ai_options()
    keys = [k for k, _ in options]
    cur = s.get("engine", "rules") if s.get("engine", "rules") in keys else "rules"
    eng = st.selectbox("Complaint sorting", keys, index=keys.index(cur), format_func=lambda k: dict(options)[k])
    if eng != "rules":
        st.caption("Complaint text (with personal numbers hidden) is sent to this service.")
    sp_ready = [n for n, _ in ready_speech(ui.ROOT)]
    sp_keys = ["off"] + sp_ready
    sp_cur = s.get("speech") if s.get("speech") in sp_keys else (sp_ready[0] if sp_ready else "off")
    profiles = speech_profiles(ui.ROOT)
    sp = st.selectbox("Speech-to-text for voice complaints", sp_keys, index=sp_keys.index(sp_cur),
                      format_func=lambda k: "Off (officers type voice complaints)" if k == "off" else profiles[k]["label"])
    if st.button("Save settings", type="primary"):
        P.save_settings({"engine": eng, "speech": sp})
        st.success("Saved.")
    with st.expander("AI services and their status"):
        st.dataframe(pd.DataFrame(ui.ai_status_rows()), hide_index=True)
    with st.expander("Speech services and their status"):
        st.dataframe(pd.DataFrame([{"Service": p["label"], "Ready": "Yes" if speech_status(p)[0] else "No",
                                    "Detail": speech_status(p)[1]} for p in profiles.values()]), hide_index=True)
    with st.expander("Languages"):
        st.dataframe(pd.DataFrame(ui.language_table(current_router())), hide_index=True)
    if st.button("Log out"):
        st.session_state.pop("officer", None)
        st.rerun()


# ====================================================================== navigation
lang = st.session_state["lang"]
citizen = [st.Page(file_page, title=T(lang, "nav_file"), url_path="file", default=True),
           st.Page(track_page, title=T(lang, "nav_track"), url_path="track")]
if OFFICER:
    pages = {"Citizen services": citizen,
             "Officer": [st.Page(queue_page, title="Work queue", url_path="queue"),
                         st.Page(dashboard_page, title="District dashboard", url_path="dashboard"),
                         st.Page(reports_page, title="Reports and downloads", url_path="reports"),
                         st.Page(checks_page, title="System checks", url_path="checks"),
                         st.Page(settings_page, title="Settings", url_path="settings")]}
else:
    pages = citizen + [st.Page(login_page, title=T(lang, "nav_officer"), url_path="officer")]
try:
    nav = st.navigation(pages, position="top")
except TypeError:                     # older Streamlit: navigation in the sidebar
    nav = st.navigation(pages)
nav.run()
