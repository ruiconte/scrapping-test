"""Streamlit dashboard for the Fableya prospector.

Run with: streamlit run dashboard/app.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

import config
import session_control
from storage import database as db

st.set_page_config(page_title="Fableya Prospector", layout="wide")
db.init_db()

st.title("🔎 Fableya Prospector")

# --------------------------------------------------------------------------
# Sidebar: session controls
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Session")
    status = session_control.get_status()
    state = status["state"]
    badge = {"IDLE": "⚪ Idle", "RUNNING": "🟢 Running", "PAUSED": "🟡 Paused"}[state]
    st.markdown(f"**Status:** {badge}")

    with st.expander("Seed keywords", expanded=False):
        default_keywords = ", ".join(config.SEARCH_SEEDS[:5])
        keywords_input = st.text_area(
            "Comma-separated keywords (blank = use config defaults)",
            value="", placeholder=default_keywords, height=80,
        )
    max_profiles = st.number_input("Max profiles this session", min_value=1, max_value=500,
                                    value=config.MAX_PROFILES_PER_SESSION)

    st.divider()
    st.header("Settings")
    st.caption("Applied to the NEXT session you start (env overrides).")
    s_min_score = st.slider("Minimum score", 0, 100, config.MIN_SCORE)
    s_deep_threshold = st.slider("Deep-analysis threshold", 0, 100, config.DEEP_ANALYSIS_THRESHOLD)
    s_followers_pref = st.slider(
        "Preferred follower range", 0, 50_000,
        (config.FOLLOWERS_PREFERRED_MIN, config.FOLLOWERS_PREFERRED_MAX),
    )
    s_followers_max = st.number_input("Max acceptable followers", value=config.FOLLOWERS_ACCEPTABLE_MAX)
    s_max_posts = st.number_input("Max posts per deep analysis", value=config.MAX_POSTS_STAGE2, min_value=1, max_value=20)
    s_max_comments = st.number_input("Max comments per post", value=config.MAX_COMMENTS_PER_POST, min_value=1, max_value=20)
    s_gemini_model = st.text_input("Gemini model", value=config.GEMINI_MODEL)

    env_overrides = {
        "MIN_SCORE": s_min_score,
        "DEEP_ANALYSIS_THRESHOLD": s_deep_threshold,
        "FOLLOWERS_PREFERRED_MIN": s_followers_pref[0],
        "FOLLOWERS_PREFERRED_MAX": s_followers_pref[1],
        "FOLLOWERS_ACCEPTABLE_MAX": s_followers_max,
        "MAX_POSTS_STAGE2": s_max_posts,
        "MAX_COMMENTS_PER_POST": s_max_comments,
        "GEMINI_MODEL": s_gemini_model,
    }

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ START", disabled=(state != "IDLE"), use_container_width=True):
            keywords = [k.strip() for k in keywords_input.split(",") if k.strip()] or None
            try:
                session_control.start_session(keywords, int(max_profiles), env_overrides=env_overrides)
                st.success("Session started.")
                time.sleep(1)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
        if st.button("⏸ PAUSE", disabled=(state != "RUNNING"), use_container_width=True):
            session_control.pause_session()
            st.rerun()
    with col2:
        if st.button("⏹ STOP", disabled=(state == "IDLE"), use_container_width=True):
            session_control.stop_session()
            st.rerun()
        if st.button("▶️ RESUME", disabled=(state != "PAUSED"), use_container_width=True):
            session_control.resume_session()
            st.rerun()

    auto_refresh = st.checkbox("Auto-refresh (5s)", value=(state == "RUNNING"))

# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
stats = db.get_summary_stats()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Discovered", stats["total_discovered"])
m2.metric("Total Analyzed", stats["total_analyzed"])
m3.metric("High Priority", stats["high_priority"])
m4.metric("Medium Priority", stats["medium_priority"])
m5.metric("Low Priority", stats["low_priority"])
m6.metric("Rejected", stats["rejected"])

m7, m8, m9 = st.columns(3)
m7.metric("Discovered Today", stats["discovered_today"])
m8.metric("Analyzed Today", stats["analyzed_today"])
hours_elapsed = max(datetime.now(timezone.utc).hour, 1)
m9.metric("Profiles / Hour (today avg)", round(stats["analyzed_today"] / hours_elapsed, 1))

with st.expander("Recent sessions"):
    sessions = db.recent_sessions()
    if sessions:
        st.dataframe(pd.DataFrame([{
            "Start": s["session_start"], "End": s["session_end"],
            "Processed": s["profiles_discovered"], "Errors": s["errors"],
        } for s in sessions]), hide_index=True, use_container_width=True)
    else:
        st.caption("No sessions recorded yet.")

st.divider()

# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
c1, c2, c3 = st.columns(3)
with c1:
    st.subheader("Top discovery keywords")
    kw = db.top_discovery_keywords()
    if kw:
        st.bar_chart(pd.DataFrame(kw, columns=["keyword", "count"]).set_index("keyword"))
    else:
        st.caption("No data yet.")
with c2:
    st.subheader("Top categories")
    cats = db.distribution("primary_category")
    if cats:
        st.bar_chart(pd.DataFrame(cats, columns=["category", "count"]).set_index("category"))
    else:
        st.caption("No data yet.")
with c3:
    st.subheader("Score distribution")
    scores = db.score_distribution()
    st.bar_chart(pd.DataFrame(scores, columns=["range", "count"]).set_index("range"))

c4, c5, c6 = st.columns(3)
with c4:
    st.subheader("Languages")
    langs = db.distribution("language")
    if langs:
        st.bar_chart(pd.DataFrame(langs, columns=["language", "count"]).set_index("language"))
    else:
        st.caption("No data yet.")
with c5:
    st.subheader("Countries")
    countries = db.distribution("country")
    if countries:
        st.bar_chart(pd.DataFrame(countries, columns=["country", "count"]).set_index("country"))
    else:
        st.caption("No data yet.")
with c6:
    st.subheader("Discovery sources")
    sources = db.distribution("discovery_source")
    if sources:
        st.bar_chart(pd.DataFrame(sources, columns=["source", "count"]).set_index("source"))
    else:
        st.caption("No data yet.")

st.divider()

# --------------------------------------------------------------------------
# Filters + main table
# --------------------------------------------------------------------------
st.subheader("Prospects")

f1, f2, f3, f4 = st.columns(4)
f_min_score = f1.number_input("Min score", 0, 100, 0)
f_min_followers = f2.number_input("Min followers", 0, value=0)
f_max_followers = f3.number_input("Max followers", 0, value=0, help="0 = no limit")

all_prospects_for_filters = db.list_prospects()
languages = sorted({r["language"] for r in all_prospects_for_filters if r.get("language")})
countries = sorted({r["country"] for r in all_prospects_for_filters if r.get("country")})
statuses = sorted({r["status"] for r in all_prospects_for_filters if r.get("status")})
sources = sorted({r["discovery_source"] for r in all_prospects_for_filters if r.get("discovery_source")})
categories = sorted({r.get("primary_category") or r.get("account_category")
                      for r in all_prospects_for_filters
                      if r.get("primary_category") or r.get("account_category")})
all_prospect_types = sorted({t for r in all_prospects_for_filters for t in (r.get("prospect_types") or [])})

f_language = f4.selectbox("Language", ["All"] + languages)

f5, f6, f7, f8 = st.columns(4)
f_country = f5.selectbox("Country", ["All"] + countries)
f_status = f6.selectbox("Status", ["All"] + statuses)
f_source = f7.selectbox("Discovery source", ["All"] + sources)
f_category = f8.selectbox("Category", ["All"] + categories)

f_prospect_type = st.selectbox("Prospect type", ["All"] + all_prospect_types)

filters = {"min_score": f_min_score or None}
if f_min_followers:
    filters["min_followers"] = f_min_followers
if f_max_followers:
    filters["max_followers"] = f_max_followers
if f_language != "All":
    filters["language"] = f_language
if f_country != "All":
    filters["country"] = f_country
if f_status != "All":
    filters["status"] = f_status
if f_source != "All":
    filters["discovery_source"] = f_source
if f_category != "All":
    filters["category"] = f_category
if f_prospect_type != "All":
    filters["prospect_type"] = f_prospect_type

rows = db.list_prospects(filters)

if rows:
    table_df = pd.DataFrame([{
        "Score": r.get("final_score") if r.get("final_score") is not None else r.get("preliminary_score"),
        "Username": r["username"],
        "Followers": r.get("followers"),
        "Category": r.get("primary_category") or r.get("account_category"),
        "Language": r.get("language"),
        "Country": r.get("country"),
        "Prospect Type": ", ".join(r.get("prospect_types") or []),
        "Reason": r.get("analysis_reason"),
        "Status": r.get("status"),
        "Instagram": r.get("profile_url"),
    } for r in rows])

    st.dataframe(
        table_df,
        column_config={
            "Instagram": st.column_config.LinkColumn("Instagram", display_text="Open profile"),
        },
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("No prospects match these filters yet.")

if st.button("⬇ EXPORT CSV"):
    export_path = config.EXPORTS_DIR / f"prospects_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    count = db.export_csv(export_path, filters)
    st.success(f"Exported {count} row(s) to {export_path}")
    with open(export_path, "rb") as f:
        st.download_button("Download CSV", f, file_name=export_path.name, mime="text/csv")

st.divider()

# --------------------------------------------------------------------------
# Ready to contact: draft-only, one click per prospect opens the real DM
# composer pre-filled — you review and press send yourself. Nothing here
# ever sends automatically.
# --------------------------------------------------------------------------
st.subheader("Ready to Contact")
st.caption(
    "Drafts a message into the real Instagram DM composer, automatically translated into the "
    "prospect's detected language (falls back to English if unknown). You review and send it "
    "yourself — nothing is sent automatically."
)

DEFAULT_TEMPLATE_EN = (
    "Hi {display_name}! I hope you don’t mind me reaching out. I recently created a small "
    "project called Fableya, which allows parents to create personalized illustrated stories "
    "for their children.\n\nI’m currently trying to get the project out there and would "
    "really love to hear what you think about the concept. If you’re curious, you can "
    "check it out at fableya.com \U0001F60A\n\nThanks for taking the time to read my message!"
)

message_template = st.text_area(
    "Message template in English (use {display_name} or {username})",
    value=st.session_state.get("outreach_template", DEFAULT_TEMPLATE_EN),
    key="outreach_template",
    height=180,
)

ready_rows = [r for r in db.list_prospects()
              if r["status"] in ("HIGH_PRIORITY", "MEDIUM_PRIORITY", "DEEP_ANALYSIS_PENDING", "PREQUALIFIED")
              and r.get("outreach_status") != "SENT"]

if not ready_rows:
    st.caption("No qualified prospects awaiting outreach yet.")
else:
    for r in ready_rows[:20]:
        rc1, rc2, rc3, rc4, rc5 = st.columns([2, 1, 1, 1, 2])
        rc1.markdown(f"**@{r['username']}**  \n{r.get('display_name') or ''}")
        rc2.markdown(f"Score: {r.get('final_score') or r.get('preliminary_score') or '—'}")
        rc3.markdown(f"Lang: {r.get('language') or 'UNKNOWN'}")
        rc4.markdown(f"Draft: {r.get('outreach_status', 'NOT_DRAFTED')}")
        if rc5.button("✉️ Open & pre-fill DM", key=f"draft_{r['username']}"):
            log_path = config.BASE_DIR / "logs" / "prospector.log"
            with open(log_path, "a") as log_file:
                subprocess.Popen(
                    [sys.executable, str(config.BASE_DIR / "main.py"), "draft-outreach",
                     r["username"], "--message", message_template],
                    cwd=str(config.BASE_DIR), stdout=log_file, stderr=subprocess.STDOUT,
                )
            st.info(f"Opening Instagram and drafting a message for @{r['username']}... check the Chrome window.")

if auto_refresh:
    time.sleep(5)
    st.rerun()
