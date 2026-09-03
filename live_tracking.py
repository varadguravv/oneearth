"""
OneEarth — Live Rescue Tracking Module (Phase 5)
==================================================
Two complementary, free mechanisms for "real-time" responder position:

1. REAL: a responder can share their actual GPS via the browser's
   built-in Geolocation API (templates/responder_location.html) — free,
   no API key, built into every modern browser.

2. SIMULATED FALLBACK: if no real location has been shared yet, the
   responder's position is nudged a little closer to the incident each
   time the tracking page/API is polled, while status is 'En Route'.
   This makes the feature demonstrable without needing a real moving
   phone, and is clearly distinguishable in the UI (is_live=False).

A real geolocation update always takes priority — simulation only
starts from and moves the LAST KNOWN position, so it never overrides
a genuine GPS report.

No new scoring or matching logic here — this only tracks WHERE the
already-assigned responder currently is.
"""

import time
from responder_matching import haversine_km
from location_utils import estimate_eta_minutes

# Each simulated tick moves the responder this fraction of the
# remaining distance closer to the incident. Deliberately fast so the
# effect is visible within a short demo/testing session.
SIMULATED_MOVEMENT_STEP = 0.12


def get_live_position(responder):
    """
    Returns (lat, lon, is_live) for a responder row:
      - is_live=True  -> a real or simulated current position exists
      - is_live=False -> no position has ever been recorded; falls
                          back to the responder's fixed home-base
                          coordinates.
    """
    if responder is None:
        return None, None, False
    if responder['current_lat'] is not None and responder['current_lon'] is not None:
        return responder['current_lat'], responder['current_lon'], True
    return responder['latitude'], responder['longitude'], False


def compute_live_distance_eta(incident_lat, incident_lon, resp_lat, resp_lon):
    """Same haversine + ETA math as Phase 4, applied to the CURRENT
    (possibly-moved) responder position instead of the original
    assignment-time snapshot."""
    if None in (incident_lat, incident_lon, resp_lat, resp_lon):
        return None, None
    distance_km = round(haversine_km(incident_lat, incident_lon, resp_lat, resp_lon), 2)
    eta_minutes = estimate_eta_minutes(distance_km)
    return distance_km, eta_minutes


def simulate_responder_movement(conn, report):
    """
    ADDED (Phase 5): demo/testing movement simulation. Only runs while
    rescue_stage is 'En Route' — once Arrived, movement stops (as it
    should). Moves from the responder's LAST recorded position (real
    or simulated), never resets to their home base, so a genuine
    geolocation update is never overwritten or fought against.
    """
    if not report or report['rescue_stage'] != 'En Route':
        return

    responder_id = report['assigned_responder_id']
    if not responder_id:
        return

    incident_lat = report['incident_lat']
    incident_lon = report['incident_lon']
    if incident_lat is None or incident_lon is None:
        return

    conn.row_factory = __import__('sqlite3').Row
    c = conn.cursor()
    c.execute('SELECT * FROM responders WHERE id = ?', (responder_id,))
    responder = c.fetchone()
    if not responder:
        return

    cur_lat, cur_lon, _ = get_live_position(responder)

    new_lat = cur_lat + (incident_lat - cur_lat) * SIMULATED_MOVEMENT_STEP
    new_lon = cur_lon + (incident_lon - cur_lon) * SIMULATED_MOVEMENT_STEP

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        UPDATE responders SET current_lat = ?, current_lon = ?, last_location_update = ?
        WHERE id = ?
    ''', (new_lat, new_lon, now_str, responder_id))
    conn.commit()


def simplify_stage_label(rescue_stage):
    """
    Maps the detailed internal rescue_stage (unchanged from Phase 3)
    to the simplified 5-step status the reporter sees, per Phase 5's
    spec. This is a DISPLAY-only mapping — the underlying stored
    rescue_stage values and lifecycle are not altered or renamed.
    """
    mapping = {
        'Reported': 'Assigned',
        'Assessed': 'Assigned',
        'Assigned': 'Assigned',
        'Accepted': 'Assigned',
        'En Route': 'En Route',
        'Arrived': 'Arrived',
        'Rescue In Progress': 'Rescue In Progress',
        'Rescue Completed': 'Resolved',
        'Case Closed': 'Resolved',
    }
    return mapping.get(rescue_stage, 'Assigned')
