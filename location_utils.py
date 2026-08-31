"""
OneEarth — Location & Distance Utilities (Phase 4)
====================================================
Small, dependency-free helpers for extracting incident coordinates and
producing an honest, approximate ETA. No paid APIs, no external
geocoding service, no new UI — this parses coordinates that the
EXISTING report form's map picker (templates/report.html) already
writes into the location field, e.g. "GPS Pin: 18.5204° N, 73.8567° E".

If no coordinates are found (e.g. the reporter typed a plain address
instead of using the map), this falls back to the same default
city-center point already used in responder_matching.py, and clearly
flags the result as approximate so the UI can be honest about it.

Distance itself is computed with the haversine formula already defined
in responder_matching.py — imported here, not duplicated.
"""

import re
from responder_matching import haversine_km, DEFAULT_INCIDENT_LAT, DEFAULT_INCIDENT_LON

# Matches the exact format the existing report form's map picker writes,
# e.g. "GPS Pin: 18.5204° N, 73.8567° E" — tolerant of the exact
# punctuation/spacing so small formatting differences still parse.
_GPS_PIN_PATTERN = re.compile(r'GPS\s*Pin:\s*(-?\d+\.?\d*)\D+(-?\d+\.?\d*)', re.IGNORECASE)

# Assumed average travel speed for the ETA estimate — a deliberately
# conservative, transparent assumption for mixed urban/highway
# conditions. This is NOT a real routing engine — no live traffic,
# no road network — just straight-line distance / assumed speed.
ASSUMED_SPEED_KMH = 30


def parse_incident_coordinates(location_text):
    """
    Attempts to extract (lat, lon) from the existing location field.

    Returns (lat, lon, is_precise):
      - is_precise=True  -> real GPS coordinates were found in the text
      - is_precise=False -> no coordinates found; falls back to the
                             existing default city-center point. Any
                             UI displaying this should clearly label
                             the location as approximate.
    """
    if location_text:
        match = _GPS_PIN_PATTERN.search(location_text)
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
                return lat, lon, True
            except ValueError:
                pass
    return DEFAULT_INCIDENT_LAT, DEFAULT_INCIDENT_LON, False


def estimate_eta_minutes(distance_km):
    """
    Simple, transparent ETA estimate: straight-line distance divided by
    an assumed average speed. Explicitly an approximation — should
    always be labeled as such wherever it's shown. Returns a minimum
    of 2 minutes so very short distances don't display as "0 min".
    """
    if distance_km is None:
        return None
    minutes = (distance_km / ASSUMED_SPEED_KMH) * 60
    return max(2, round(minutes))
