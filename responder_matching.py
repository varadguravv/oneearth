"""
OneEarth — Responder Matching Module (Phase 2)
================================================
Rule-based responder TYPE decision + smart RANKING. This is explicitly
NOT machine learning — every decision here is a transparent, readable
rule, matching the same honesty standard as risk_assessment.py.

Distance is computed locally with the haversine formula using each
responder's stored latitude/longitude — no external maps/geocoding
API, no API key.

This module does not introduce any new RISK scoring — it reuses the
existing Phase 1 species/severity/priority fields already computed and
stored per report, and only decides responder type + rank.
"""

import math

# ---------- 1. Required responder type decision (rule-based) ----------
def determine_required_responder_types(species, severity, description):
    """
    Transparent if/else rules — not ML. Returns an ordered list of
    responder types appropriate for this case, e.g. ['Veterinarian']
    or ['Wildlife Specialist', 'Veterinarian'].
    """
    species = (species or '').strip().lower()
    severity = (severity or '').strip().lower()
    description = (description or '').lower()

    types = []

    # Wildlife or venomous-animal cases need a specialist regardless
    # of severity, since handling itself carries risk.
    if species == 'wildlife' or 'venom' in description or 'snake' in description:
        types.append('Wildlife Specialist')

    # Critical/High severity cases need veterinary care, whatever the species.
    if severity in ('critical', 'high'):
        types.append('Veterinarian')

    # Otherwise, a volunteer is the appropriate first responder.
    if not types:
        types.append('Volunteer')

    # De-duplicate while preserving the order they were added in.
    seen = set()
    ordered = []
    for t in types:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


# ---------- 2. Distance calculation (Haversine — pure math, no API) ----------
def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return 9999.0
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# Fallback incident coordinates — the existing report form collects a
# free-text location rather than GPS coordinates by default, so this
# is used as a stand-in city-center point (same area used elsewhere
# across the site's demo map markers).
DEFAULT_INCIDENT_LAT = 18.5204
DEFAULT_INCIDENT_LON = 73.8567


def score_responder(responder, required_types, priority, incident_lat=None, incident_lon=None):
    """
    Higher score = better match. Returns None if the responder's type
    doesn't match any required type (excluded entirely, not just
    ranked low — a volunteer should never be "ranked" for a case that
    needs a vet).

    Combines, in order of how much each factor matters:
      1. Availability     — biggest single factor
      2. Distance          — closer is better, but capped impact
      3. Current workload  — fewer active cases is better
      4. Priority urgency  — small boost for available+idle responders
                              on P1/P2 cases, since speed matters most
    """
    if responder['responder_type'] not in required_types:
        return None

    lat = incident_lat if incident_lat is not None else DEFAULT_INCIDENT_LAT
    lon = incident_lon if incident_lon is not None else DEFAULT_INCIDENT_LON

    distance = haversine_km(lat, lon, responder['latitude'], responder['longitude'])
    available = bool(responder['available'])
    active_cases = responder['active_cases'] or 0

    score = 100.0

    if not available:
        score -= 70  # an unavailable responder should almost never win

    score -= min(distance * 2, 40)      # distance penalty, capped
    score -= min(active_cases * 8, 32)  # workload penalty, capped

    if priority in ('P1', 'P2') and available and active_cases == 0:
        score += 10  # urgency bonus for available + idle responders

    return round(score, 1)


def rank_responders(all_responders, required_types, priority, incident_lat=None, incident_lon=None):
    """
    Returns (responder, score, distance_km) tuples for every responder
    whose type matches, sorted best match first.
    """
    scored = []
    for r in all_responders:
        s = score_responder(r, required_types, priority, incident_lat, incident_lon)
        if s is not None:
            lat = incident_lat if incident_lat is not None else DEFAULT_INCIDENT_LAT
            lon = incident_lon if incident_lon is not None else DEFAULT_INCIDENT_LON
            dist = haversine_km(lat, lon, r['latitude'], r['longitude'])
            scored.append((r, s, round(dist, 1)))
    scored.sort(key=lambda triple: triple[1], reverse=True)
    return scored


def recommend_responders(all_responders, species, severity, priority, description,
                          incident_lat=None, incident_lon=None):
    """
    Full pipeline: determine required type(s), rank all matching
    responders, return the top N per the existing priority level.

    Response levels (per spec):
        P4/Low       -> 1 recommendation
        P3/Moderate  -> 1 recommendation
        P2/High      -> up to 3 recommendations
        P1/Critical  -> up to 4 recommendations
    """
    required_types = determine_required_responder_types(species, severity, description)
    ranked = rank_responders(all_responders, required_types, priority, incident_lat, incident_lon)

    top_n = {'P1': 4, 'P2': 3, 'P3': 1, 'P4': 1}.get(priority, 1)
    return required_types, ranked[:top_n]
