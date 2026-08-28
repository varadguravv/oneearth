"""
OneEarth — Escalation Module (Phase 2)
========================================
Configurable, on-demand escalation check. No background scheduler and
no external notification service — this runs as a lightweight check
each time the admin reports dashboard is loaded, which is enough to
demonstrate the full escalation flow locally without new dependencies.

Timeouts are deliberately short by default so the flow can be tested
in minutes rather than hours — change ESCALATION_TIMEOUT_MINUTES for
a more realistic production value later.
"""

from datetime import datetime, timedelta

# How long a case can sit with no confirmed response before it's
# considered for escalation. Only P1/P2 cases escalate automatically —
# P3/P4 are lower urgency by design (per the existing priority system).
ESCALATION_TIMEOUT_MINUTES = {
    'P1': 2,   # short on purpose, for easy local demo/testing
    'P2': 5,
}


def _parse_dt(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def needs_escalation(report_row):
    """
    A case needs escalation if all of the following are true:
      1. Its priority is P1 or P2 (configured above).
      2. Its response_status is still 'Pending Response' — i.e.
         nobody has confirmed picking it up yet.
      3. Its assignment_time is older than the configured timeout
         for its priority level.
    """
    priority = report_row['priority'] if 'priority' in report_row.keys() else None
    if priority not in ESCALATION_TIMEOUT_MINUTES:
        return False

    response_status = report_row['response_status'] if 'response_status' in report_row.keys() else None
    if response_status != 'Pending Response':
        return False

    assignment_time = report_row['assignment_time'] if 'assignment_time' in report_row.keys() else None
    assigned_dt = _parse_dt(assignment_time)
    if not assigned_dt:
        return False

    timeout = timedelta(minutes=ESCALATION_TIMEOUT_MINUTES[priority])
    return datetime.now() - assigned_dt > timeout
