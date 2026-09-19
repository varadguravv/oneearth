"""
OneEarth — Disaster / High-Volume Mode (Final Phase, Part 3)
===============================================================
Threshold-based detection computed LIVE from existing report data on
every check — there is no stored "disaster mode = on" flag anywhere.
This means it can never get stuck active by mistake, and automatically
returns to normal the instant case volume drops back down, per the
spec's requirement that normal operations resume automatically.

Purely additive: when conditions are normal, is_disaster_mode_active()
returns False and nothing about existing behavior changes anywhere
else in the app.
"""

import sqlite3
from datetime import datetime, timedelta

# Tunable thresholds — deliberately simple and transparent, not a
# black-box model. Easy to adjust for real deployment later.
RECENT_WINDOW_MINUTES = 60
CRITICAL_HIGH_THRESHOLD = 5   # 5+ active P1/P2 cases reported in the window
TOTAL_ACTIVE_THRESHOLD = 10   # or 10+ total active cases overall, any priority


def _parse_dt(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def is_disaster_mode_active(conn):
    """
    Returns (is_active, reason_text, stats_dict).
    """
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE rescue_stage != 'Case Closed'")
    active = c.fetchall()

    cutoff = datetime.now() - timedelta(minutes=RECENT_WINDOW_MINUTES)
    recent_critical_high = 0
    for r in active:
        if r['priority'] in ('P1', 'P2'):
            dt = _parse_dt(r['submitted_at'])
            if dt and dt >= cutoff:
                recent_critical_high += 1

    total_active = len(active)
    stats = {
        'total_active': total_active,
        'recent_critical_high': recent_critical_high,
        'window_minutes': RECENT_WINDOW_MINUTES,
    }

    if recent_critical_high >= CRITICAL_HIGH_THRESHOLD:
        return True, f"{recent_critical_high} critical/high cases in the last {RECENT_WINDOW_MINUTES} minutes", stats
    if total_active >= TOTAL_ACTIVE_THRESHOLD:
        return True, f"{total_active} active cases currently open", stats
    return False, None, stats
