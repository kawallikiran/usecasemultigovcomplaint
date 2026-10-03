"""Streamlit UI for Use case 8: multilingual citizen grievance chatbot.

Run locally:   streamlit run app.py
In Docker:     docker compose up      (then open http://localhost:8502)
"""
import pandas as pd
import streamlit as st

from grievdesk import ui_logic as ui
from grievdesk.common import ui_report as rep

st.set_page_config(page_title="Citizen grievance desk", layout="wide")


@st.cache_resource
def get_router(engine):
    return ui.make_router(engine)


ai_ok, ai_msg = ui.ai_ready()
router, cfg = get_router("rules")
samples = ui.load_samples(cfg)
store = ui.decision_store()

st.title("Citizen grievance desk")
st.caption("AIGP26 capstone prototype, use case 8. Synthetic data only. The bot routes complaints and sends "
           "approved acknowledgements; an officer decides every substantive reply.")

tab_use, tab_report, tab_results, tab_audit = st.tabs(
    ["Register a grievance", "Test report", "All test results", "Audit log"])

# ------------------------------------------------------------------ register a grievance
with tab_use:
    engines = ["Rules (offline)"] + ([f"AI service ({ai_msg})"] if ai_ok else [])
    engine_label = st.radio("Engine", engines, horizontal=True)
    engine = "ai" if engine_label.startswith("AI") else "rules"
    if not ai_ok:
        st.caption(f"AI engine not available: {ai_msg}")
    if engine == "ai":
        st.warning("The complaint text is sent to the AI service named above, with Aadhaar, phone numbers and "
                   "emails masked first. Use synthetic data only.")
    active = get_router(engine)[0]
    st.write("Write a complaint as a citizen would, in English, Hindi, Chhattisgarhi or a mix.")
    sample_ids = [""] + [r["id"] for r in samples]
    by_id = {r["id"]: r for r in samples}
    pick = st.selectbox("Fill in a sample complaint (optional)", sample_ids,
                        format_func=lambda k: "Choose a sample" if not k else
                        f"{k} ({by_id[k]['language_group']}): {by_id[k]['text'][:70]}")
    if pick and st.session_state.get("last_pick") != pick:
        st.session_state["complaint"] = by_id[pick]["text"]
        st.session_state["last_pick"] = pick
    with st.form("complaint_form"):
        text = st.text_area("Complaint", key="complaint", height=110)
        submitted = st.form_submit_button("Register grievance", type="primary")
    if submitted:
        if not text.strip():
            st.error("Write the complaint first.")
        else:
            with st.spinner("Routing the grievance"):
                st.session_state["g_result"] = active.process(ui.new_case(text))
            st.session_state.pop("g_saved", None)

    out = st.session_state.get("g_result")
    if out:
        st.subheader("What the bot did")
        st.dataframe(pd.DataFrame(ui.summary_rows(out)), hide_index=True)
        st.write("Why:")
        st.markdown("\n".join(f"- {r}" for r in out["reasons"]))
        st.write("Message already sent to the citizen (approved template only):")
        st.info(out["auto_reply"])

        st.subheader("Officer decision")
        if out["sensitive_categories"]:
            st.warning("Sensitive case. Only the designated officer can act on it. "
                       "The bot has not replied with any detail.")
        dept_keys = list(router.depts)
        with st.form("decision_form"):
            officer = st.text_input("Officer name")
            label = st.radio("Decision", ui.decision_options(out), index=None, horizontal=True)
            new_dept = st.selectbox("Department (for 'Send to another department')", dept_keys,
                                    index=dept_keys.index(out["department"]) if out["department"] in dept_keys else 0,
                                    format_func=lambda k: router.depts[k]["name"])
            reply = st.text_area("Reply to send", value=out["draft_reply"] or "", height=110)
            note = st.text_input("Note (required if you change the department or escalate)")
            save = st.form_submit_button("Save decision", type="primary")
        if save:
            decision = ui.DECISIONS.get(label)
            errors = ui.validate_decision(decision, officer, note, reply, ui.handling(out),
                                          out["department"], new_dept)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                store.save({"id": out["ticket"], "officer": officer, "decision": decision,
                            "ai_department": out["department"],
                            "final_department": new_dept if decision == "reroute" else out["department"],
                            "reply": reply, "note": note})
                st.session_state["g_saved"] = (out["ticket"], decision)
        saved = st.session_state.get("g_saved")
        if saved and saved[0] == out["ticket"]:
            st.success(f"Decision saved for ticket {saved[0]}: {saved[1]}"
                       + (" (officer overrode the bot)." if saved[1] == "reroute" else "."))


# ------------------------------------------------------------------ test report / results
def pick_set(key):
    name = st.radio("Test set", list(ui.TEST_SETS), horizontal=True, key=key)
    return name, ui.TEST_SETS[name]


with tab_report:
    set_name, cfg_path = pick_set("report_set")
    blocked = ui.is_ai_set(cfg_path) and not ai_ok
    if blocked:
        st.warning(f"This set uses the AI engine, which is not configured: {ai_msg}")
    elif ui.is_ai_set(cfg_path):
        st.caption("This run makes a few hundred calls to the AI service. It takes several minutes and uses API credit.")
    if st.button("Run the 8 tests on this set", type="primary", disabled=blocked):
        with st.spinner("Running the tests."):
            ui.run_tests(cfg_path)
        st.success(f"Tests finished for: {set_name}.")
    data, when = rep.load_findings(ui.output_dir(cfg_path))
    if not data:
        st.info("No results for this set yet. Run the tests to create them.")
    else:
        f, m = data["findings"], data["metrics"]
        n_failed = sum(not x["passed"] for x in f)
        st.write(f"Last run {when}. {len(f)} checks, {n_failed} failed. Accuracy on the set: "
                 f"{m['baseline_accuracy']}. Sent to a human: {m['share_sent_to_human']}.")
        st.subheader("By critic test")
        st.dataframe(pd.DataFrame(rep.by_test_rows(f)), hide_index=True)
        if n_failed:
            st.subheader("Problems found, most severe first")
            st.dataframe(pd.DataFrame(rep.failed_rows(f)), hide_index=True)
        for group, rows in rep.subgroup_tables(m, "Wrongly treated as sensitive"):
            st.subheader(f"Results by {group}")
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        out_dir = ui.output_dir(cfg_path)
        d1, d2 = st.columns(2)
        with open(f"{out_dir}/report.md", "rb") as fh:
            d1.download_button("Download report.md", fh.read(), file_name="grievance_report.md")
        with open(f"{out_dir}/findings.csv", "rb") as fh:
            d2.download_button("Download findings.csv", fh.read(), file_name="grievance_findings.csv")

with tab_results:
    _, cfg_path2 = pick_set("results_set")
    show = st.radio("Show", ["All", "Failed only", "Passed only"], horizontal=True)
    data2, _ = rep.load_findings(ui.output_dir(cfg_path2))
    if not data2:
        st.info("No results yet. Run the tests from the Test report tab.")
    else:
        st.dataframe(pd.DataFrame(rep.all_rows(data2["findings"], show)), hide_index=True)

# ------------------------------------------------------------------ audit log
with tab_audit:
    st.write("Every bot output from this screen and the officer's decision on it, newest first. "
             "Aadhaar, phone numbers and emails are masked before saving.")
    st.button("Refresh")
    rows = ui.audit_rows(router, store)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("Nothing logged yet. Register a grievance in the first tab.")
