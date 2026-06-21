"""
notifier.py — Outcome notifications via Resend (email) and ntfy.sh (push).

Called by the scoring job after each event is scored. Only notifies voters
who left contact information. Voter PII is stored separately from the
research dataset and never included in commits.

Email:  Resend API  (3,000 free emails/month, no card required)
Push:   ntfy.sh     (free, open-source push notifications)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .config import (
    NTFY_BASE,
    NTFY_TOPIC,
    NOTIFICATION_FROM,
    RESEND_API_KEY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resend email
# ---------------------------------------------------------------------------

def send_outcome_email(
    to_address: str,
    event_summary: str,
    crowd_direction: str,
    crowd_correct: bool,
    winning_theories: list[str],
    leaderboard_url: str = "https://your-app.streamlit.app",
) -> bool:
    """
    Send an outcome notification email via Resend API.

    Returns True on success.
    """
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email notification")
        return False

    crowd_result = "✅ correct" if crowd_correct else "❌ incorrect"
    theories_str = ", ".join(winning_theories) if winning_theories else "none"

    html_body = f"""
<html><body style="font-family: sans-serif; max-width: 600px; margin: auto;">
<h2>Theory Tournament — Event Resolved</h2>
<p>{event_summary}</p>
<hr/>
<p><strong>Your prediction:</strong> {crowd_direction.upper()} — {crowd_result}</p>
<p><strong>Theories that got it right:</strong> {theories_str}</p>
<p><a href="{leaderboard_url}">View full leaderboard →</a></p>
<hr/>
<p style="font-size:12px; color:#888;">
You received this because you voted on this event.
This is a research project at the Strategy Department.
</p>
</body></html>
"""

    payload = json.dumps({
        "from":    NOTIFICATION_FROM,
        "to":      [to_address],
        "subject": "Theory Tournament: Your event just resolved",
        "html":    html_body,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("Email sent to %s (status %d)", to_address, resp.status)
            return True
    except urllib.error.HTTPError as e:
        logger.error("Resend API error %d for %s: %s", e.code, to_address, e.read())
        return False
    except Exception as e:
        logger.error("Email send failed for %s: %s", to_address, e)
        return False


# ---------------------------------------------------------------------------
# ntfy.sh push notification
# ---------------------------------------------------------------------------

def send_push_notification(
    ntfy_channel: str,
    event_summary: str,
    crowd_direction: str,
    crowd_correct: bool,
    winning_theories: list[str],
) -> bool:
    """
    Send a push notification via ntfy.sh.

    The voter subscribes to a personal topic (e.g., "theory-tournament-USER123")
    and receives the result when it resolves.

    Returns True on success.
    """
    if not ntfy_channel:
        return False

    crowd_result = "✅ You got it right!" if crowd_correct else "❌ Missed this one."
    theories_str = ", ".join(winning_theories[:3]) if winning_theories else "none"

    title = "Theory Tournament: Result in"
    body = (
        f"You predicted {crowd_direction.upper()}. {crowd_result}\n"
        f"Winning theories: {theories_str}"
    )

    url = f"{NTFY_BASE}/{ntfy_channel}"

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "default",
            "Tags": "chart_with_upwards_trend",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Push sent to ntfy/%s (status %d)", ntfy_channel, resp.status)
            return True
    except Exception as e:
        logger.warning("ntfy push failed for %s: %s", ntfy_channel, e)
        return False


# ---------------------------------------------------------------------------
# Batch notification for an event
# ---------------------------------------------------------------------------

def notify_voters_for_event(
    event_record: dict[str, Any],
    score_records: list[dict[str, Any]],
    crowd_votes: list[dict[str, Any]],
    leaderboard_url: str = "https://your-app.streamlit.app",
) -> int:
    """
    Notify all crowd voters for a given event after scoring.

    Args:
        event_record: structured event dict
        score_records: scored theory records (list of score dicts)
        crowd_votes: list of crowd vote dicts for this event
        leaderboard_url: URL of the public leaderboard

    Returns count of notifications sent.
    """
    if not crowd_votes:
        return 0

    event_summary = (
        f"{event_record.get('company', 'A company')} announced "
        f"{event_record.get('event_type', 'a strategic event').replace('_', ' ')} "
        f"on {event_record.get('filing_date', '')}."
    )

    # Determine the "crowd" direction from majority vote
    from collections import Counter
    vote_counts = Counter(v.get("direction", "") for v in crowd_votes)
    crowd_direction = vote_counts.most_common(1)[0][0] if vote_counts else "positive"

    # Determine winning theories at 3-day horizon (primary)
    winning_theories = [
        s.get("theory_name", "")
        for s in score_records
        if s.get("horizon") in (3, "3") and s.get("is_win") in (True, "True")
    ]

    # Determine realized direction at 3-day horizon
    realized_3d = next(
        (s.get("realized_direction") for s in score_records if s.get("horizon") in (3, "3")),
        None,
    )
    crowd_correct = (
        realized_3d is not None
        and crowd_direction == realized_3d
        and not any(
            s.get("is_flat") in (True, "True")
            for s in score_records
            if s.get("horizon") in (3, "3")
        )
    )

    sent = 0
    for vote in crowd_votes:
        if vote.get("notified") == "True":
            continue

        email = vote.get("email", "").strip()
        ntfy  = vote.get("ntfy_channel", "").strip()

        if email:
            ok = send_outcome_email(
                to_address=email,
                event_summary=event_summary,
                crowd_direction=crowd_direction,
                crowd_correct=crowd_correct,
                winning_theories=winning_theories,
                leaderboard_url=leaderboard_url,
            )
            if ok:
                sent += 1

        if ntfy:
            send_push_notification(
                ntfy_channel=ntfy,
                event_summary=event_summary,
                crowd_direction=crowd_direction,
                crowd_correct=crowd_correct,
                winning_theories=winning_theories,
            )

    return sent
