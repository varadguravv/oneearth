"""
OneEarth — Emergency Risk Assessment Module (Phase 1)
======================================================
Transparent, rule-based scoring. NO external/paid AI APIs, NO API keys.

This module is deliberately kept separate from app.py so it can be
swapped for a real ML/AI model later without touching any route logic —
only this file would need to change, and the function signature/return
shape below is the contract the rest of the app relies on.

calculate_risk_assessment() takes fields already collected by the
existing report form and returns a dict with:
    - risk_score          (int, 0-100)
    - severity            ("Critical" / "High" / "Moderate" / "Low")
    - priority             ("P1" / "P2" / "P3" / "P4")
    - recommended_action  (short guidance string)
"""

# ---------- Keyword banks used for description-based scoring ----------
HIGH_SEVERITY_KEYWORDS = [
    'unconscious', 'not moving', 'not breathing', 'severe bleeding',
    'heavy bleeding', 'dying', 'seizure', 'convulsion', 'poison',
    'poisoned', 'snake bite', 'snakebite', 'on fire', 'drowning',
    'electrocuted', 'electrocution', 'multiple injuries', 'internal bleeding',
    'critical condition', 'collapsed'
]

MEDIUM_SEVERITY_KEYWORDS = [
    'bleeding', 'fracture', 'broken', 'hit by', 'hit-and-run', 'accident',
    'trapped', 'stuck', 'injured', 'wound', 'limping', 'unable to walk',
    'unable to move', 'in pain', 'crying', 'whimpering', 'swelling'
]

LOCATION_RISK_KEYWORDS = [
    'highway', 'main road', 'busy road', 'traffic', 'expressway', 'flyover'
]

# Base score from the urgency dropdown already on the existing report form
URGENCY_BASE_SCORES = {
    'critical': 50,
    'urgent': 30,
    'moderate': 15,
}

# Species handling complexity/risk modifier (matches existing species dropdown)
SPECIES_RISK_MODIFIER = {
    'wildlife': 15,   # venomous/dangerous handling risk
    'cattle': 8,      # large-animal handling complexity
    'avian': 8,
    'urban': 5,
}


def _count_keyword_hits(text, keywords):
    if not text:
        return 0
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def calculate_risk_assessment(species, urgency, description, location):
    """
    Pure function, no side effects, no network calls.
    Deterministic: same inputs always produce the same output, but
    different inputs meaningfully change the score.
    """
    description = description or ''
    location = location or ''
    urgency_key = (urgency or '').strip().lower()
    species_key = (species or '').strip().lower()

    # 1. Base score from stated urgency — the single biggest factor,
    #    since the reporter's own assessment matters most.
    score = URGENCY_BASE_SCORES.get(urgency_key, 10)

    # 2. Species modifier — some species carry more handling risk/complexity
    score += SPECIES_RISK_MODIFIER.get(species_key, 5)

    # 3. Description keyword scoring — looks for concrete signals of
    #    severity in the reporter's own words, capped so no single
    #    factor can dominate the whole score.
    high_hits = _count_keyword_hits(description, HIGH_SEVERITY_KEYWORDS)
    medium_hits = _count_keyword_hits(description, MEDIUM_SEVERITY_KEYWORDS)
    keyword_score = min(high_hits * 15, 35) + min(medium_hits * 8, 20)
    score += keyword_score

    # 4. Location risk — animals on highways/busy roads face compounding
    #    danger (traffic) on top of their existing injury.
    if (_count_keyword_hits(location, LOCATION_RISK_KEYWORDS) > 0 or
            _count_keyword_hits(description, LOCATION_RISK_KEYWORDS) > 0):
        score += 10

    # Clamp to a valid 0-100 range
    score = max(0, min(100, score))

    # 5. Map the final score to severity / priority / recommended action
    if score >= 80:
        severity = 'Critical'
        priority = 'P1'
        recommended_action = (
            'Dispatch nearest emergency ambulance immediately — this '
            'appears to be a life-threatening situation requiring urgent '
            'veterinary intervention.'
        )
    elif score >= 60:
        severity = 'High'
        priority = 'P2'
        recommended_action = (
            'Dispatch a rescue team as soon as possible (within 15-30 '
            'minutes). The animal requires urgent medical attention.'
        )
    elif score >= 35:
        severity = 'Moderate'
        priority = 'P3'
        recommended_action = (
            'Schedule a rescue team dispatch within 1-2 hours. Monitor the '
            'animal if it is safe to do so while help is on the way.'
        )
    else:
        severity = 'Low'
        priority = 'P4'
        recommended_action = (
            'Log for follow-up and connect the reporter with the nearest '
            'shelter or veterinary center for non-emergency care.'
        )

    return {
        'risk_score': score,
        'severity': severity,
        'priority': priority,
        'recommended_action': recommended_action,
    }
