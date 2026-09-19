"""
OneEarth — Duplicate/Suspicious Report Detection (Final Phase, Part 6)
=========================================================================
Flags likely duplicates for ADMIN REVIEW ONLY — this NEVER blocks a
submission or auto-deletes anything. A false positive here could cost
an animal its life if a genuine second reporter is turned away, so
the check only marks a flag for a human to look at; the report is
always saved and dispatched normally regardless of the result.

Uses only fields already collected by the existing report form:
species, location (text and/or parsed GPS), and submission time.
"""

import sqlite3
from datetime import datetime, timedelta
from responder_matching import haversine_km

DUPLICATE_WINDOW_MINUTES = 30
NEARBY_DISTANCE_KM = 0.5  # ~500 meters


def _parse_dt(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def check_possible_duplicate(conn, species, location_text, incident_lat, incident_lon, submitted_at):
    """
    Returns the ID of a likely-duplicate existing report, or None.

    Conservative by design — flags only when species matches AND the
    reports are close in BOTH time (within DUPLICATE_WINDOW_MINUTES)
    AND place (same location text, or GPS within NEARBY_DISTANCE_KM).
    Matching on species+time+place together, rather than any one
    factor alone, keeps false positives low.
    """
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT * FROM reports WHERE species = ? AND rescue_stage != 'Case Closed' ORDER BY id DESC LIMIT 50",
        (species,)
    )
    candidates = c.fetchall()

    submitted_dt = _parse_dt(submitted_at)
    if not submitted_dt:
        return None
    cutoff = submitted_dt - timedelta(minutes=DUPLICATE_WINDOW_MINUTES)

    for r in candidates:
        r_dt = _parse_dt(r['submitted_at'])
        if not r_dt or r_dt < cutoff:
            continue

        same_location_text = (location_text or '').strip().lower() == (r['location'] or '').strip().lower()

        close_gps = False
        if (incident_lat is not None and incident_lon is not None
                and r['incident_lat'] is not None and r['incident_lon'] is not None):
            dist = haversine_km(incident_lat, incident_lon, r['incident_lat'], r['incident_lon'])
            close_gps = dist <= NEARBY_DISTANCE_KM

        if same_location_text or close_gps:
            return r['id']

    return None
