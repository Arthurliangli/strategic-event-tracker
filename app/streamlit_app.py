"""
streamlit_app.py — Theory Tournament public leaderboard and crowd voting UI.

Pages:
  - Recent Events      : latest events with locked predictions for each theory
  - Theory Standings   : leaderboard by event type / horizon / moderator
  - Event Detail       : drill-down with reasoning + outcome
  - Vote               : crowd prediction for live (unscored) events
  - Residuals          : events all theories missed (quarterly review log)
  - About              : project description and methodology
"""

from __future__ import annotations

import sys
import os

# Allow importing src/ from the app/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
from datetime import datetime, date
from typing import Any

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Theory Tournament",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Local imports (after path setup)
# ---------------------------------------------------------------------------

from src.storage import (
    load_events,
    load_predictions,
    load_scores,
    load_crowd_votes,
    load_residuals,
    append_crowd_vote,
)
from src.config import (
    THEORIES,
    EVENT_TYPES,
    NOISY_EVENT_TYPES,
    THEORY_ROSTER_VERSION,
)
from src.theories import build_crowd_context

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  .metric-card {
    background: #1e2130; border-radius: 8px; padding: 16px 20px;
    margin: 4px 0; border-left: 4px solid #4f8ef7;
  }
  .win-badge  { color: #4caf50; font-weight: bold; }
  .loss-badge { color: #f44336; font-weight: bold; }
  .flat-badge { color: #999;    font-style: italic; }
  .noisy-flag { color: #ff9800; font-size: 11px; }
  .theory-name { font-weight: 600; }
  .pred-positive { color: #4caf50; }
  .pred-negative { color: #f44336; }
  .pred-neutral  { color: #999; }
  .lock-icon { font-size: 11px; color: #aaa; }
  h1, h2, h3 { font-family: 'Georgia', serif; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)  # refresh every 5 minutes
def get_data():
    events      = load_events()
    predictions = load_predictions()
    scores      = load_scores()
    crowd_votes = load_crowd_votes()
    residuals   = load_residuals()
    return events, predictions, scores, crowd_votes, residuals


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚖️ Theory Tournament")
    st.caption(f"Theory roster v{THEORY_ROSTER_VERSION}")
    st.divider()

    page = st.radio(
        "Navigate",
        ["📋 Recent Events", "🏆 Theory Standings", "🔍 Event Detail",
         "🗳️ Cast Your Vote", "🔬 Residuals", "ℹ️ About"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "A preregistered tournament where management theories compete to "
        "predict real corporate events. All predictions are locked before "
        "outcomes are observed."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIRECTION_EMOJI = {"positive": "📈", "negative": "📉", "neutral": "➖"}
WIN_EMOJI       = {True: "✅", False: "❌", "flat": "⊘"}
EVENT_EMOJI     = {
    "ceo_turnover":  "👤",
    "ma":            "🤝",
    "restructuring": "🏗️",
    "foreign_entry": "🌍",
    "foreign_exit":  "🚪",
}


def direction_html(direction: str) -> str:
    cls = f"pred-{direction}"
    emoji = DIRECTION_EMOJI.get(direction, "")
    return f'<span class="{cls}">{emoji} {direction.upper()}</span>'


def win_rate(wins: int, total: int) -> str:
    if total == 0:
        return "—"
    pct = wins / total * 100
    return f"{pct:.1f}% ({wins}/{total})"


def _build_crowd_scores(scores_df, crowd_df, events_df):
    """Aggregate crowd prediction accuracy across scored events."""
    rows = []
    for event_id in scores_df["event_id"].unique():
        crowd_info = get_crowd_prediction(crowd_df, event_id)
        if crowd_info["total"] == 0:
            continue
        ev_scores = scores_df[scores_df["event_id"] == event_id]
        for horizon in [3, 10]:
            h_sc = ev_scores[ev_scores["horizon"].isin([str(horizon), horizon])]
            if h_sc.empty:
                continue
            realized = h_sc.iloc[0].get("realized_direction", "")
            is_flat  = h_sc.iloc[0].get("is_flat") in (True, "True")
            is_win   = (
                not is_flat
                and crowd_info["direction"] == realized
                and crowd_info["direction"] != "neutral"
            )
            rows.append({
                "entity": "The Crowd",
                "event_id": event_id,
                "horizon": horizon,
                "is_win": is_win,
                "is_flat": is_flat,
            })

    if not rows:
        return []

    df = pd.DataFrame(rows)
    out = []
    for horizon in [3, 10]:
        sub = df[df["horizon"] == horizon]
        scored = sub[~sub["is_flat"]]
        wins = scored["is_win"].sum()
        total = len(scored)
        out.append({
            "Entity": "The Crowd (majority vote)",
            "Horizon": f"{horizon}-day",
            "Wins": int(wins),
            "Scored Events": int(total),
            "Win Rate": win_rate(int(wins), int(total)),
        })
    return out


def build_leaderboard(scores_df: pd.DataFrame, filter_foreign: bool = False) -> pd.DataFrame:
    """Aggregate scores into a theory leaderboard."""
    if scores_df.empty:
        return pd.DataFrame()

    df = scores_df.copy()
    df["is_win"]    = df["is_win"].map({"True": True, "False": False, True: True, False: False})
    df["is_flat"]   = df["is_flat"].map({"True": True, "False": False, True: True, False: False})
    df["horizon"]   = df["horizon"].astype(str)

    if filter_foreign:
        df = df[df["is_foreign_event"].isin(["True", True])]
    else:
        df = df[~df["is_foreign_event"].isin(["True", True])]

    rows = []
    for (theory_name, horizon), grp in df.groupby(["theory_name", "horizon"]):
        total  = len(grp)
        flat   = grp["is_flat"].sum()
        scored = total - flat
        wins   = grp["is_win"].sum()
        rows.append({
            "Theory":        theory_name,
            "Horizon":       f"{horizon}-day",
            "Events":        total,
            "Scored (non-flat)": scored,
            "Wins":          int(wins),
            "Win Rate":      win_rate(int(wins), int(scored)),
            "_win_pct":      wins / scored if scored > 0 else 0,
        })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values(["Horizon", "_win_pct"], ascending=[True, False])
    return out.drop(columns=["_win_pct"])


def get_crowd_prediction(crowd_df: pd.DataFrame, event_id: str) -> dict:
    """Aggregate crowd votes for one event."""
    evdf = crowd_df[crowd_df["event_id"] == event_id]
    if evdf.empty:
        return {"total": 0, "direction": None}
    from collections import Counter
    counts = Counter(evdf["direction"].tolist())
    top = counts.most_common(1)[0]
    return {"total": len(evdf), "direction": top[0], "counts": dict(counts)}


# ===========================================================================
# PAGE: Recent Events
# ===========================================================================

if page == "📋 Recent Events":
    st.header("📋 Recent Strategic Events")
    st.caption(
        "Events detected from SEC EDGAR 8-K filings and NewsAPI. "
        "Predictions were generated and locked **before** outcome data was observed."
    )

    events_df, predictions_df, scores_df, crowd_df, _ = get_data()

    if events_df.empty:
        st.info("No events yet. Run `python scripts/backfill.py` or wait for the next scheduled job.")
        st.stop()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        evt_filter = st.multiselect(
            "Event type",
            options=list(EVENT_TYPES.keys()),
            format_func=lambda k: f"{EVENT_EMOJI.get(k,'')} {EVENT_TYPES[k].label}",
            default=list(EVENT_TYPES.keys()),
        )
    with col2:
        scored_filter = st.selectbox(
            "Status",
            ["All", "Scored only", "Pending outcome"],
        )
    with col3:
        ai_filter = st.checkbox("AI-related events only")

    # Apply filters
    df = events_df.copy()
    if evt_filter:
        df = df[df["event_type"].isin(evt_filter)]
    if ai_filter:
        df = df[df["ai_flag"].isin(["True", True])]

    scored_ids = set(scores_df["event_id"].tolist()) if not scores_df.empty else set()
    if scored_filter == "Scored only":
        df = df[df["event_id"].isin(scored_ids)]
    elif scored_filter == "Pending outcome":
        df = df[~df["event_id"].isin(scored_ids)]

    # Sort newest first
    df = df.sort_values("filing_date", ascending=False).head(50)

    st.markdown(f"**Showing {len(df)} events**")

    predicted_pairs = set()
    if not predictions_df.empty:
        predicted_pairs = {
            (r["event_id"], r["theory_key"])
            for _, r in predictions_df.iterrows()
        }

    for _, ev in df.iterrows():
        event_id   = ev["event_id"]
        company    = ev["company"]
        ticker     = ev.get("ticker", "")
        evt_type   = ev.get("event_type", "")
        evt_label  = EVENT_TYPES[evt_type].label if evt_type in EVENT_TYPES else evt_type
        filing_dt  = ev.get("filing_date", "")
        headline   = ev.get("headline", "")[:120]
        ai_flag    = ev.get("ai_flag") in ("True", True)
        is_noisy   = evt_type in NOISY_EVENT_TYPES
        is_scored  = event_id in scored_ids

        emoji = EVENT_EMOJI.get(evt_type, "📄")
        ai_tag = "🤖 AI-related" if ai_flag else ""
        noisy_tag = '<span class="noisy-flag">⚠️ supplemental source</span>' if is_noisy else ""
        scored_tag = "✅ Scored" if is_scored else "⏳ Pending"

        with st.expander(
            f"{emoji} **{company}** ({ticker}) — {evt_label} — {filing_dt}  {ai_tag}",
            expanded=False,
        ):
            st.markdown(f"**{headline}** {noisy_tag}", unsafe_allow_html=True)
            st.markdown(f"Status: {scored_tag} | [SEC Filing]({ev.get('raw_text_url', '#')})")

            # Show predictions for this event
            if not predictions_df.empty:
                ev_preds = predictions_df[predictions_df["event_id"] == event_id]
                if not ev_preds.empty:
                    st.markdown("**Locked predictions** 🔒")
                    pred_cols = st.columns(len(ev_preds))
                    for i, (_, pred) in enumerate(ev_preds.iterrows()):
                        tname = pred.get("theory_name", "")
                        tshort = next(
                            (t.short_name for t in THEORIES.values() if t.name == tname),
                            tname
                        )
                        direction = pred.get("predicted_direction", "")
                        conf = pred.get("confidence", "")
                        with pred_cols[i]:
                            st.markdown(
                                f'<span class="theory-name">{tshort}</span><br>'
                                f'{direction_html(direction)}<br>'
                                f'<span class="lock-icon">🔒 conf: {conf}/5 | '
                                f'locked: {pred.get("predicted_at","")[:10]}</span>',
                                unsafe_allow_html=True,
                            )

            # Show scores if available
            if is_scored and not scores_df.empty:
                ev_scores = scores_df[scores_df["event_id"] == event_id]
                st.markdown("**Outcome**")
                for horizon in [3, 10]:
                    h_scores = ev_scores[ev_scores["horizon"].isin([str(horizon), horizon])]
                    if h_scores.empty:
                        continue
                    realized = h_scores.iloc[0].get("realized_direction", "")
                    is_flat  = h_scores.iloc[0].get("is_flat") in ("True", True)

                    if is_flat:
                        outcome_str = f"**{horizon}-day:** ⊘ Flat return — all theories scored as loss"
                    else:
                        outcome_str = f"**{horizon}-day:** {DIRECTION_EMOJI.get(realized,'')} {realized.upper()}"
                    st.markdown(outcome_str)

                    cols = st.columns(len(h_scores))
                    for i, (_, sc) in enumerate(h_scores.iterrows()):
                        is_win = sc.get("is_win") in (True, "True")
                        badge  = "✅" if is_win else ("⊘" if is_flat else "❌")
                        tshort = next(
                            (t.short_name for t in THEORIES.values()
                             if t.name == sc.get("theory_name","")),
                            sc.get("theory_name","")
                        )
                        with cols[i]:
                            st.markdown(f"{badge} {tshort}")

            # Crowd vote summary
            crowd_info = get_crowd_prediction(crowd_df, event_id)
            if crowd_info["total"] > 0:
                st.markdown(
                    f"**Crowd:** {crowd_info['total']} vote(s) — "
                    f"majority: {DIRECTION_EMOJI.get(crowd_info['direction'],'')} "
                    f"{crowd_info['direction'].upper()}"
                )


# ===========================================================================
# PAGE: Theory Standings
# ===========================================================================

elif page == "🏆 Theory Standings":
    st.header("🏆 Theory Standings")
    st.caption(
        "Win rate = fraction of non-flat scored events where the theory's "
        "directional prediction matched the realized abnormal return. "
        "Flat returns (|CAR| < 0.5%) are scored as a loss for all theories."
    )

    events_df, predictions_df, scores_df, crowd_df, _ = get_data()

    if scores_df.empty:
        st.info("No scored events yet. Check back after the first outcomes resolve (≥3 trading days).")
        st.stop()

    tab1, tab2 = st.tabs(["Main Events (EDGAR)", "Foreign Events (Supplemental)"])

    with tab1:
        lb = build_leaderboard(scores_df, filter_foreign=False)
        if lb.empty:
            st.info("No main event scores yet.")
        else:
            st.dataframe(lb, use_container_width=True, hide_index=True)

        # Crowd vs theories
        st.subheader("The Crowd vs. Theories")
        crowd_scores = _build_crowd_scores(scores_df, crowd_df, events_df)
        if crowd_scores:
            st.dataframe(pd.DataFrame(crowd_scores), use_container_width=True, hide_index=True)
        else:
            st.caption("Crowd scores will appear once events with votes have resolved.")

    with tab2:
        st.info(
            "⚠️ Foreign market entry/exit events are sourced from a noisier "
            "feed (NewsAPI + EDGAR Item 8.01 keyword screening). "
            "Results are shown separately and should be interpreted with more caution."
        )
        lb_foreign = build_leaderboard(scores_df, filter_foreign=True)
        if lb_foreign.empty:
            st.info("No foreign event scores yet.")
        else:
            st.dataframe(lb_foreign, use_container_width=True, hide_index=True)

    # Breakdowns
    st.subheader("Breakdown by Moderator")
    mod_col = st.selectbox(
        "Break down by",
        ["sector", "ai_flag", "event_type"],
    )

    if not events_df.empty and not scores_df.empty:
        merged = scores_df.merge(
            events_df[["event_id", "sector", "ai_flag", "event_type", "market_cap_usd"]],
            on="event_id",
            how="left",
        )
        if mod_col in merged.columns:
            grp = (
                merged.groupby(["theory_name", mod_col, "horizon"])
                .apply(lambda g: pd.Series({
                    "wins":  (g["is_win"].isin([True, "True"])).sum(),
                    "total": (~g["is_flat"].isin([True, "True"])).sum(),
                }))
                .reset_index()
            )
            grp["win_rate"] = grp.apply(
                lambda r: win_rate(int(r["wins"]), int(r["total"])), axis=1
            )
            grp.rename(columns={"theory_name": "Theory", mod_col: mod_col.replace("_"," ").title(),
                                 "horizon": "Horizon"}, inplace=True)
            st.dataframe(grp[["Theory", mod_col.replace("_"," ").title(), "Horizon", "win_rate"]],
                         use_container_width=True, hide_index=True)



# ===========================================================================
# PAGE: Event Detail
# ===========================================================================

elif page == "🔍 Event Detail":
    st.header("🔍 Event Detail")

    events_df, predictions_df, scores_df, crowd_df, _ = get_data()

    if events_df.empty:
        st.info("No events available yet.")
        st.stop()

    # Event selector
    event_options = {
        row["event_id"]: f"{row['company']} — {row['event_type'].replace('_',' ')} — {row['filing_date']}"
        for _, row in events_df.sort_values("filing_date", ascending=False).head(200).iterrows()
    }

    selected_id = st.selectbox(
        "Select event",
        options=list(event_options.keys()),
        format_func=lambda k: event_options[k],
    )

    if not selected_id:
        st.stop()

    ev = events_df[events_df["event_id"] == selected_id].iloc[0]

    # Event header
    evt_type = ev.get("event_type", "")
    emoji = EVENT_EMOJI.get(evt_type, "📄")
    st.subheader(f"{emoji} {ev['company']} ({ev.get('ticker','')}) — {EVENT_TYPES.get(evt_type, type('',(),{'label':evt_type})()).label}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Filing Date", ev.get("filing_date", ""))
    col2.metric("Sector", ev.get("sector", ""))
    col3.metric("AI-Related", "Yes 🤖" if ev.get("ai_flag") in ("True", True) else "No")
    col4.metric("Market Cap", f"${float(ev['market_cap_usd'])/1e9:.1f}B" if ev.get("market_cap_usd") else "N/A")

    st.markdown(f"**Event:** {ev.get('headline','')}")
    if ev.get("raw_text_url"):
        st.markdown(f"[View original filing / article →]({ev['raw_text_url']})")

    if evt_type in NOISY_EVENT_TYPES:
        st.warning("⚠️ This event is from the supplemental feed (NewsAPI / Item 8.01 keyword screen). "
                   "Classification confidence is lower than for clean 8-K triggers.")

    st.divider()

    # Predictions
    st.subheader("🔒 Locked Predictions")
    ev_preds = predictions_df[predictions_df["event_id"] == selected_id] if not predictions_df.empty else pd.DataFrame()

    if ev_preds.empty:
        st.info("No predictions yet for this event.")
    else:
        for _, pred in ev_preds.iterrows():
            theory_name = pred.get("theory_name", "")
            direction   = pred.get("predicted_direction", "")
            reasoning   = pred.get("reasoning", "")
            conf        = pred.get("confidence", "")
            magnitude   = pred.get("magnitude_estimate", "")
            locked_at   = pred.get("predicted_at", "")[:16]
            model       = pred.get("model_used", "")

            with st.expander(f"**{theory_name}** → {direction_html(direction)}", expanded=True):
                st.markdown(f"**Reasoning:** {reasoning}", unsafe_allow_html=True)
                st.markdown(
                    f"Magnitude: **{magnitude}** | Confidence: **{conf}/5** | "
                    f"Locked: `{locked_at}` | Model: `{model}`"
                )

                # Show score if available
                if not scores_df.empty:
                    pred_scores = scores_df[
                        (scores_df["event_id"] == selected_id) &
                        (scores_df["theory_name"] == theory_name)
                    ]
                    for _, sc in pred_scores.iterrows():
                        is_win  = sc.get("is_win") in (True, "True")
                        is_flat = sc.get("is_flat") in (True, "True")
                        realized = sc.get("realized_direction", "")
                        h = sc.get("horizon", "")
                        if is_flat:
                            st.markdown(f"**{h}-day outcome:** ⊘ Flat — no theory scored")
                        else:
                            badge = "✅ WIN" if is_win else "❌ MISS"
                            st.markdown(
                                f"**{h}-day outcome:** {badge} | "
                                f"Realized: {DIRECTION_EMOJI.get(realized,'')} {realized.upper()} | "
                                f"CAR: {sc.get('realized_direction','')}"
                            )

    st.divider()

    # Crowd
    st.subheader("🗳️ Crowd Prediction")
    crowd_info = get_crowd_prediction(crowd_df, selected_id)
    if crowd_info["total"] == 0:
        st.info("No crowd votes yet for this event.")
    else:
        vcol1, vcol2 = st.columns(2)
        vcol1.metric("Total Votes", crowd_info["total"])
        vcol2.metric(
            "Majority Direction",
            f"{DIRECTION_EMOJI.get(crowd_info['direction'],'')} {crowd_info['direction'].upper()}"
        )
        counts = crowd_info.get("counts", {})
        if counts:
            vcol1.metric("📈 Positive Votes", counts.get("positive", 0))
            vcol2.metric("📉 Negative Votes", counts.get("negative", 0))


# ===========================================================================
# PAGE: Cast Your Vote
# ===========================================================================

elif page == "🗳️ Cast Your Vote":
    st.header("🗳️ Cast Your Vote")
    st.markdown(
        "Predict the stock market reaction for live events before outcomes are known. "
        "You'll be notified when the outcome resolves — and see how you compare to the theories."
    )

    events_df, predictions_df, scores_df, crowd_df, _ = get_data()

    if events_df.empty:
        st.info("No events available yet.")
        st.stop()

    # Only show events that are predicted but not yet scored at 3-day horizon
    predicted_ids = set(predictions_df["event_id"].tolist()) if not predictions_df.empty else set()
    scored_3d_ids = set()
    if not scores_df.empty:
        scored_3d_ids = set(
            scores_df[scores_df["horizon"].isin(["3", 3])]["event_id"].tolist()
        )

    voteable = events_df[
        events_df["event_id"].isin(predicted_ids)
        & ~events_df["event_id"].isin(scored_3d_ids)
    ].sort_values("filing_date", ascending=False)

    if voteable.empty:
        st.info("No live events to vote on right now. Check back soon — new events are fetched every 6 hours.")
        st.stop()

    # Pick an event to vote on
    event_options = {
        row["event_id"]: f"{row['company']} — {row['event_type'].replace('_',' ')} — {row['filing_date']}"
        for _, row in voteable.iterrows()
    }

    selected_id = st.selectbox(
        "Choose an event to predict:",
        options=list(event_options.keys()),
        format_func=lambda k: event_options[k],
    )

    if not selected_id:
        st.stop()

    ev = events_df[events_df["event_id"] == selected_id].iloc[0]

    # Check if user already voted (session state)
    already_voted = st.session_state.get(f"voted_{selected_id}", False)

    # Show event context (no theories shown to avoid anchoring)
    crowd_ctx = build_crowd_context(ev.to_dict())
    st.markdown(crowd_ctx)

    if already_voted:
        st.success("✅ You've already voted on this event. Your vote has been recorded.")
        existing_votes = crowd_df[crowd_df["event_id"] == selected_id] if not crowd_df.empty else pd.DataFrame()
        if not existing_votes.empty:
            counts = existing_votes["direction"].value_counts()
            st.markdown(f"**Current crowd:** 📈 {counts.get('positive', 0)} positive | 📉 {counts.get('negative', 0)} negative")
    else:
        with st.form(f"vote_form_{selected_id}"):
            st.markdown("**Your prediction:**")
            direction = st.radio(
                "Direction",
                ["📈 Positive (stock rises above market)", "📉 Negative (stock falls below market)"],
                label_visibility="collapsed",
            )
            confidence = st.slider("Confidence (1 = not sure, 5 = very confident)", 1, 5, 3)

            st.markdown("**Optional: get notified when this resolves**")
            email = st.text_input("Email address", placeholder="you@email.com")
            ntfy  = st.text_input(
                "ntfy.sh channel (optional — for phone push notification)",
                placeholder="e.g. my-theory-tournament-channel",
                help="Subscribe at https://ntfy.sh/my-theory-tournament-channel on your phone",
            )

            st.markdown(
                '<small>By providing your email, you agree to receive one notification '
                'when this event resolves. Your contact info is stored separately from '
                'the research dataset and is never shared.</small>',
                unsafe_allow_html=True,
            )

            submitted = st.form_submit_button("Submit prediction")

        if submitted:
            direction_clean = "positive" if "Positive" in direction else "negative"
            vote_record = {
                "vote_id":     str(uuid.uuid4())[:16],
                "event_id":    selected_id,
                "direction":   direction_clean,
                "confidence":  str(confidence),
                "email":       email.strip(),
                "ntfy_channel": ntfy.strip(),
                "created_at":  datetime.utcnow().isoformat() + "Z",
                "notified":    "False",
            }
            append_crowd_vote(vote_record)
            st.session_state[f"voted_{selected_id}"] = True
            st.success("✅ Vote recorded! You'll be notified when the outcome resolves.")
            st.rerun()


# ===========================================================================
# PAGE: Residuals
# ===========================================================================

elif page == "🔬 Residuals":
    st.header("🔬 Residual Events")
    st.markdown(
        "Events where **all theories missed** at both scoring horizons. "
        "These are not excluded or re-coded — they're documented here for the quarterly "
        "inductive review. Patterns in residuals may motivate new theory additions (v2+)."
    )

    events_df, predictions_df, scores_df, crowd_df, residuals_df = get_data()

    if residuals_df.empty:
        st.info("No residual events yet. Once enough events are scored, persistent misses will appear here.")
        st.stop()

    st.metric("Total Residual Events", len(residuals_df))

    for _, res in residuals_df.iterrows():
        event_id = res.get("event_id", "")
        evt_type = res.get("event_type", "")
        flagged  = res.get("flagged_at", "")[:10]
        notes    = res.get("patterns_noted", "")

        ev_row = events_df[events_df["event_id"] == event_id]
        company = ev_row.iloc[0]["company"] if not ev_row.empty else event_id

        with st.expander(f"**{company}** — {evt_type.replace('_',' ')} — flagged {flagged}"):
            if not ev_row.empty:
                st.markdown(f"**Headline:** {ev_row.iloc[0].get('headline','')}")

            # Show what each theory predicted vs. what happened
            if not predictions_df.empty and not scores_df.empty:
                preds = predictions_df[predictions_df["event_id"] == event_id]
                ev_sc = scores_df[scores_df["event_id"] == event_id]
                for _, pred in preds.iterrows():
                    tname = pred.get("theory_name", "")
                    pred_dir = pred.get("predicted_direction", "")
                    sc_row = ev_sc[ev_sc["theory_name"] == tname]
                    if not sc_row.empty:
                        realized = sc_row.iloc[0].get("realized_direction", "")
                        st.markdown(
                            f"- **{tname}**: predicted {pred_dir.upper()} → "
                            f"realized {realized.upper()} ❌"
                        )

            if notes:
                st.markdown(f"**Review notes:** {notes}")
            else:
                st.caption("(No review notes yet — add in quarterly residual review)")


# ===========================================================================
# PAGE: About
# ===========================================================================

elif page == "ℹ️ About":
    st.header("ℹ️ About the Theory Tournament")

    st.markdown("""
### What is this?

The **Theory Tournament** is a preregistered predictive test of management theories.
When a real corporate strategic event occurs (CEO turnover, M&A, restructuring, or foreign
market entry/exit), each theory generates a directional prediction of the stock market
reaction **before** the outcome is observed. Predictions are cryptographically timestamped
and locked.

### Why does this matter?

A locked, pre-outcome prediction design mirrors the logic of preregistration in
experimental research — ensuring theories are tested on their predictive validity,
not their ability to explain outcomes after the fact. The accumulated events,
predictions, and outcomes form an event-history dataset usable for formal hypothesis
testing in strategy research.

### Scoring

- **Outcome window:** 3 trading days (short-run) and 10 trading days (medium-run)
- **Abnormal return:** cumulative return of the firm minus SPY (market benchmark)
- **Win:** predicted direction matches realized direction of abnormal return
- **Flat:** |CAR| < 0.5% → no clear signal; all theories scored as a **loss**
  (not excluded)
- **Foreign events** (entry/exit) are scored separately due to lower source quality

### The Crowd

Visitors can predict event outcomes alongside the theories. The crowd's majority
vote is scored identically — providing a wisdom-of-crowds comparison.

### Data availability

All event, prediction, and outcome data is available in the `/data` folder of the
[GitHub repository](https://github.com/YOUR_USERNAME/strategic-event-tracker)
as timestamped CSV files, suitable for use in R, Stata, or Python.

### Theory Roster v1

""")

    for evt_type, et in EVENT_TYPES.items():
        st.markdown(f"**{et.label}**")
        for theory_key in et.theories:
            t = THEORIES.get(theory_key)
            if t:
                st.markdown(f"- *{t.name}*: {t.description}")

    st.divider()
    st.markdown("""
### Methods note

**Sampling frame:** S&P 500 companies (defined list, backfilled 24 months then live)

**Event detection:**
- CEO/officer turnover: SEC 8-K Item 5.02
- M&A: SEC 8-K Items 1.01, 2.01
- Restructuring/layoffs: SEC 8-K Item 2.05
- Foreign market entry/exit: 8-K Item 8.01 + keyword screening + NewsAPI

**Validation:** A stratified sample (~100 events) is hand-coded by Arthur and
compared to LLM classifications via Cohen's kappa. Target ≥ 0.80. See the
repository for the coding scheme and validation results.

**No leakage:** prediction generation and outcome scoring run in separate,
time-separated GitHub Actions jobs. Predictions are never regenerated after
outcomes are known.
""")
