from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import os
import time
from datetime import datetime

# Phase 1 — unchanged
from risk_assessment import calculate_risk_assessment

# Phase 2 — unchanged
from responder_matching import recommend_responders, haversine_km

# Phase 3 — unchanged
from escalation import needs_escalation

# Phase 4 — unchanged
from location_utils import parse_incident_coordinates, estimate_eta_minutes

# Phase 5 — unchanged
from live_tracking import (
    get_live_position, compute_live_distance_eta,
    simulate_responder_movement, simplify_stage_label
)

# ADDED (Final Phase): new modules for dashboard, disaster mode,
# notifications, and duplicate detection
from notifications import log_notification, get_notifications, unread_count, mark_all_read
from disaster_mode import is_disaster_mode_active
from duplicate_detection import check_possible_duplicate

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'change-this-to-a-random-string-oneearth-2026')
REPORTS_PASSWORD = os.environ.get('REPORTS_PASSWORD', 'oneearth2026')

DB_PATH = os.path.join(os.path.dirname(__file__), 'reports.db')

STATUS_STAGES = [
    'Report Received', 'Rescue Team Assigned', 'Team En Route',
    'Animal Rescued', 'Under Treatment', 'Recovering'
]

RESCUE_LIFECYCLE = [
    'Reported', 'Assessed', 'Assigned', 'Accepted', 'En Route',
    'Arrived', 'Rescue In Progress', 'Rescue Completed', 'Case Closed',
]

DEMO_RESPONDERS = [
    ('Priya Sharma', 'Volunteer', '+91 98765 11111', 'Kothrud, Pune', 18.5074, 73.8077, 1, 1, 'urban,cattle'),
    ('Dr. Ananya Rao', 'Veterinarian', '+91 98765 22222', 'Baner, Pune', 18.5642, 73.7769, 1, 0, 'urban,cattle,avian'),
    ('Rahul Deshmukh', 'Wildlife Specialist', '+91 98765 33333', 'Hadapsar, Pune', 18.5089, 73.9260, 1, 2, 'wildlife,venomous'),
    ('Dr. Vikram Joshi', 'Veterinarian', '+91 98765 44444', 'Katraj, Pune', 18.4515, 73.8646, 0, 1, 'urban,wildlife,cattle'),
    ('Sneha Patil', 'Volunteer', '+91 98765 55555', 'Shivajinagar, Pune', 18.5308, 73.8475, 1, 0, 'urban'),
    ('Dr. Meera Nair', 'Veterinarian', '+91 98765 66666', 'Viman Nagar, Pune', 18.5679, 73.9143, 1, 3, 'avian,wildlife'),
    ('Arjun Kulkarni', 'Wildlife Specialist', '+91 98765 77777', 'Aundh, Pune', 18.5590, 73.8080, 0, 0, 'wildlife'),
    ('Kavita Singh', 'Volunteer', '+91 98765 88888', 'Wanowrie, Pune', 18.4886, 73.9019, 1, 1, 'cattle,urban'),
    ('Dr. Sanjay Mehta', 'Veterinarian', '+91 98765 99999', 'Deccan, Pune', 18.5158, 73.8412, 1, 2, 'urban,cattle,avian,wildlife'),
    ('Farhan Ali', 'Volunteer', '+91 98765 10101', 'Kondhwa, Pune', 18.4654, 73.8890, 1, 0, 'urban,avian'),
]


def _ensure_column(c, conn, table, col, coltype):
    c.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in c.fetchall()]
    if col not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        conn.commit()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species TEXT, urgency TEXT, reporter_name TEXT, phone TEXT,
            location TEXT, description TEXT, submitted_at TEXT,
            status TEXT DEFAULT 'Report Received'
        )
    ''')
    conn.commit()

    for col, coltype in [
        ('status', "TEXT DEFAULT 'Report Received'"),
        ('risk_score', 'INTEGER'), ('severity', 'TEXT'), ('priority', 'TEXT'),
        ('recommended_action', 'TEXT'),
        ('required_responder_type', 'TEXT'), ('assigned_responder_id', 'INTEGER'),
        ('assignment_time', 'TEXT'), ('response_status', "TEXT DEFAULT 'Unassigned'"),
        ('escalation_count', 'INTEGER DEFAULT 0'),
        ('rescue_stage', "TEXT DEFAULT 'Reported'"),
        ('incident_lat', 'REAL'), ('incident_lon', 'REAL'),
        ('location_precise', 'INTEGER DEFAULT 0'),
        ('distance_km', 'REAL'), ('eta_minutes', 'INTEGER'),
        ('possible_duplicate_of', 'INTEGER'),
    ]:
        _ensure_column(c, conn, 'reports', col, coltype)

    c.execute('''
        CREATE TABLE IF NOT EXISTS responders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, responder_type TEXT, phone TEXT, location TEXT,
            latitude REAL, longitude REAL, available INTEGER DEFAULT 1,
            active_cases INTEGER DEFAULT 0, capabilities TEXT
        )
    ''')
    conn.commit()

    for col, coltype in [
        ('current_lat', 'REAL'),
        ('current_lon', 'REAL'),
        ('last_location_update', 'TEXT'),
        ('verified', 'INTEGER DEFAULT 0'),
    ]:
        _ensure_column(c, conn, 'responders', col, coltype)

    c.execute("SELECT COUNT(*) FROM responders")
    if c.fetchone()[0] == 0:
        c.executemany('''
            INSERT INTO responders
            (name, responder_type, phone, location, latitude, longitude, available, active_cases, capabilities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', DEMO_RESPONDERS)
        conn.commit()
        # ADDED (Final Phase, Part 5): mark the veterinarians and the
        # first wildlife specialist as verified out of the box, for a
        # realistic-looking demo state (not all-or-nothing).
        c.execute("UPDATE responders SET verified = 1 WHERE responder_type = 'Veterinarian'")
        c.execute("UPDATE responders SET verified = 1 WHERE id = (SELECT MIN(id) FROM responders WHERE responder_type = 'Wildlife Specialist')")
        conn.commit()

    c.execute('''
        CREATE TABLE IF NOT EXISTS case_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER, event_type TEXT, event_detail TEXT, event_time TEXT
        )
    ''')
    conn.commit()

    # ADDED (Final Phase, Part 4): notifications table
    c.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT, event_type TEXT, report_id INTEGER,
            created_at TEXT, is_read INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()


init_db()

last_submission_by_ip = {}
MIN_SECONDS_BETWEEN_SUBMISSIONS = 20


def log_event(conn, report_id, event_type, event_detail=''):
    c = conn.cursor()
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO case_events (report_id, event_type, event_detail, event_time) VALUES (?, ?, ?, ?)',
              (report_id, event_type, event_detail, now_str))
    conn.commit()

    # ADDED (Final Phase, Part 4): every timeline event already logged
    # for the case history also becomes a notification, automatically.
    # No new call sites needed anywhere else in the app.
    message = f"Case #{report_id}: {event_type}"
    if event_detail:
        message += f" — {event_detail}"
    log_notification(conn, message, event_type=event_type, report_id=report_id)


def get_case_events(conn, report_id):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM case_events WHERE report_id = ? ORDER BY id ASC', (report_id,))
    return c.fetchall()


def human_elapsed(timestamp_str):
    if not timestamp_str:
        return '—'
    try:
        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return '—'
    seconds = max(0, int((datetime.now() - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def get_all_responders(conn):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM responders')
    return c.fetchall()


def get_responder_by_id(conn, responder_id):
    if not responder_id:
        return None
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM responders WHERE id = ?', (responder_id,))
    return c.fetchone()


def get_report_by_id(conn, report_id):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports WHERE id = ?', (report_id,))
    return c.fetchone()


def assign_best_responder(conn, report_id, species, severity, priority, description,
                           incident_lat=None, incident_lon=None, is_reassignment=False):
    c = conn.cursor()
    all_responders = get_all_responders(conn)

    # ADDED (Final Phase, Part 3): check disaster mode live at
    # assignment time, so it always reflects current conditions.
    disaster_active, disaster_reason, _ = is_disaster_mode_active(conn)

    required_types, ranked = recommend_responders(
        all_responders, species, severity, priority, description,
        incident_lat=incident_lat, incident_lon=incident_lon,
        disaster_mode=disaster_active
    )
    required_type_str = ', '.join(required_types)

    if not ranked:
        c.execute("UPDATE reports SET required_responder_type = ?, response_status = 'No Suitable Responder' WHERE id = ?",
                  (required_type_str, report_id))
        conn.commit()
        log_event(conn, report_id, 'No Suitable Responder Found', f'Required: {required_type_str}')
        return None

    best_responder, score, distance_km = ranked[0]
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    eta_minutes = estimate_eta_minutes(distance_km)

    c.execute('''
        UPDATE reports
        SET required_responder_type = ?, assigned_responder_id = ?, assignment_time = ?,
            response_status = 'Pending Response', rescue_stage = 'Assigned',
            distance_km = ?, eta_minutes = ?
        WHERE id = ?
    ''', (required_type_str, best_responder['id'], now_str, distance_km, eta_minutes, report_id))

    c.execute('UPDATE responders SET active_cases = active_cases + 1 WHERE id = ?', (best_responder['id'],))

    c.execute('UPDATE responders SET current_lat = NULL, current_lon = NULL, last_location_update = NULL WHERE id = ?',
              (best_responder['id'],))
    conn.commit()

    # ADDED (Final Phase, Part 2): plain-language explanation of why
    # this responder was chosen, built from the same factors already
    # used in responder_matching.py's scoring — not a new algorithm,
    # just a readable summary of the existing one.
    reasons = ['capability match']
    reasons.append('available' if best_responder['available'] else 'currently unavailable')
    workload = best_responder['active_cases'] or 0
    reasons.append('low workload' if workload == 0 else f'{workload} active case(s)')
    reasons.append(f'{distance_km} km / ~{eta_minutes} min practical ETA')
    explanation = ' + '.join(reasons)

    detail = f"{best_responder['name']} ({best_responder['responder_type']}) — {explanation}"
    if is_reassignment:
        detail += ' — reassigned'
    if disaster_active:
        detail += f' [Disaster Mode active: {disaster_reason}]'
    log_event(conn, report_id, 'Responder Assigned', detail)

    return best_responder


def run_escalation_check(conn):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE response_status = 'Pending Response'")
    pending = c.fetchall()

    for report in pending:
        if needs_escalation(report):
            old_responder_id = report['assigned_responder_id']
            old_responder = get_responder_by_id(conn, old_responder_id)
            log_event(conn, report['id'], 'Escalated',
                      f"No response from {old_responder['name'] if old_responder else 'assigned responder'} in time")

            if old_responder_id:
                c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?', (old_responder_id,))
                conn.commit()

            c.execute('UPDATE reports SET escalation_count = escalation_count + 1 WHERE id = ?', (report['id'],))
            conn.commit()

            assign_best_responder(
                conn, report['id'], report['species'], report['severity'], report['priority'],
                report['description'], incident_lat=report['incident_lat'], incident_lon=report['incident_lon'],
                is_reassignment=True
            )


def handle_decline(conn, report_id):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports WHERE id = ?', (report_id,))
    report = c.fetchone()
    if not report:
        return
    old_responder_id = report['assigned_responder_id']
    old_responder = get_responder_by_id(conn, old_responder_id)
    log_event(conn, report_id, 'Declined', f"Declined by {old_responder['name'] if old_responder else 'responder'}")

    if old_responder_id:
        c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?', (old_responder_id,))
        conn.commit()

    assign_best_responder(
        conn, report_id, report['species'], report['severity'], report['priority'], report['description'],
        incident_lat=report['incident_lat'], incident_lon=report['incident_lon']
    )


def get_live_tracking_data(conn, report):
    simulate_responder_movement(conn, report)
    responder = get_responder_by_id(conn, report['assigned_responder_id']) if report else None
    live_lat, live_lon, is_live = get_live_position(responder)
    live_distance, live_eta = compute_live_distance_eta(
        report['incident_lat'] if report else None,
        report['incident_lon'] if report else None,
        live_lat, live_lon
    )
    return {
        'responder': responder,
        'live_lat': live_lat,
        'live_lon': live_lon,
        'is_live': is_live,
        'live_distance_km': live_distance,
        'live_eta_minutes': live_eta,
        'simple_status': simplify_stage_label(report['rescue_stage']) if report else None,
    }


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/report', methods=['GET', 'POST'])
def report_rescue():
    if request.method == 'POST':
        if request.form.get('website', ''):
            return redirect(url_for('success'))

        ip = request.remote_addr or 'unknown'
        now = time.time()
        if now - last_submission_by_ip.get(ip, 0) < MIN_SECONDS_BETWEEN_SUBMISSIONS:
            return redirect(url_for('success'))
        last_submission_by_ip[ip] = now

        species = request.form.get('species', '').strip()[:50]
        urgency = request.form.get('urgency', '').strip()[:50]
        reporter_name = request.form.get('reporter_name', '').strip()[:100]
        phone = request.form.get('phone', '').strip()[:30]
        location = request.form.get('location', '').strip()[:300]
        description = request.form.get('description', '').strip()[:2000]

        # ADDED (Final Phase, Part 8): basic input validation hardening.
        # Length caps above prevent oversized payloads; this rejects
        # obviously-invalid phone characters without being overly
        # strict about real-world phone number formats.
        if not all([species, urgency, reporter_name, phone, location, description]):
            return redirect(url_for('report_rescue'))
        if len(phone) < 7 or len(description) < 5:
            return redirect(url_for('report_rescue'))
        if not any(ch.isdigit() for ch in phone):
            return redirect(url_for('report_rescue'))

        submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')
        assessment = calculate_risk_assessment(species, urgency, description, location)
        incident_lat, incident_lon, location_precise = parse_incident_coordinates(location)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # ADDED (Final Phase, Part 6): check for a likely duplicate
        # BEFORE inserting, so we can store the reference. This NEVER
        # blocks submission — it only flags the new report for admin
        # review; the animal still gets a real response either way.
        duplicate_of = check_possible_duplicate(conn, species, location, incident_lat, incident_lon, submitted_at)

        c.execute('''
            INSERT INTO reports (
                species, urgency, reporter_name, phone, location, description, submitted_at,
                status, risk_score, severity, priority, recommended_action, rescue_stage,
                incident_lat, incident_lon, location_precise, possible_duplicate_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            species, urgency, reporter_name, phone, location, description, submitted_at,
            'Report Received', assessment['risk_score'], assessment['severity'],
            assessment['priority'], assessment['recommended_action'], 'Assessed',
            incident_lat, incident_lon, int(location_precise), duplicate_of
        ))
        conn.commit()
        case_id = c.lastrowid

        log_event(conn, case_id, 'Report Received', f'Reported by {reporter_name}')
        log_event(conn, case_id, 'Risk Assessed', f"{assessment['severity']} severity, risk score {assessment['risk_score']}/100")
        if duplicate_of:
            log_event(conn, case_id, 'Possible Duplicate Flagged', f'Similar to existing Case #{duplicate_of} — flagged for admin review')

        disaster_active, disaster_reason, _ = is_disaster_mode_active(conn)
        if disaster_active:
            log_notification(conn, f'⚠️ Disaster Mode active — {disaster_reason}', event_type='Disaster Mode', report_id=case_id)

        assign_best_responder(conn, case_id, species, assessment['severity'], assessment['priority'],
                               description, incident_lat=incident_lat, incident_lon=incident_lon)
        conn.close()
        return redirect(url_for('success', case_id=case_id))
    return render_template('report.html')


@app.route('/directory')
def directory():
    return render_template('directory.html')


@app.route('/success')
def success():
    case_id = request.args.get('case_id')
    report, assigned_responder, elapsed = None, None, None
    if case_id and case_id.isdigit():
        conn = sqlite3.connect(DB_PATH)
        report = get_report_by_id(conn, case_id)
        if report:
            if report['assigned_responder_id']:
                assigned_responder = get_responder_by_id(conn, report['assigned_responder_id'])
            elapsed = human_elapsed(report['submitted_at'])
        conn.close()
    return render_template('success.html', case_id=case_id, report=report,
                            assigned_responder=assigned_responder, elapsed=elapsed)


@app.route('/awareness')
def awareness():
    return render_template('awareness.html')


@app.route('/adoption')
def adoption():
    return render_template('adoption.html')


@app.route('/track')
def track():
    return render_template('track.html')


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/get-involved')
def get_involved():
    return render_template('get_involved.html')


@app.route('/check-status', methods=['GET', 'POST'])
def check_status():
    result, error, elapsed, events = None, None, None, []
    live = None
    if request.method == 'POST':
        case_id = request.form.get('case_id', '').strip()
        if case_id.isdigit():
            conn = sqlite3.connect(DB_PATH)
            result = get_report_by_id(conn, case_id)
            if result:
                elapsed = human_elapsed(result['submitted_at'])
                events = get_case_events(conn, result['id'])
                live = get_live_tracking_data(conn, result)
            conn.close()
            if not result:
                error = "No case found with that number. Please check and try again."
        else:
            error = "Please enter a valid case number (numbers only)."
    return render_template('check_status.html', result=result, error=error, stages=STATUS_STAGES,
                            elapsed=elapsed, events=events, live=live)


@app.route('/api/case/<int:report_id>/live')
def api_case_live(report_id):
    conn = sqlite3.connect(DB_PATH)
    report = get_report_by_id(conn, report_id)
    if not report:
        conn.close()
        return jsonify({'error': 'not found'}), 404

    live = get_live_tracking_data(conn, report)
    conn.close()

    return jsonify({
        'rescue_stage': report['rescue_stage'],
        'simple_status': live['simple_status'],
        'responder_lat': live['live_lat'],
        'responder_lon': live['live_lon'],
        'is_live': live['is_live'],
        'distance_km': live['live_distance_km'],
        'eta_minutes': live['live_eta_minutes'],
    })


@app.route('/responder-location/<int:report_id>')
def responder_location_page(report_id):
    conn = sqlite3.connect(DB_PATH)
    report = get_report_by_id(conn, report_id)
    responder = get_responder_by_id(conn, report['assigned_responder_id']) if report else None
    live = get_live_tracking_data(conn, report) if report else None
    conn.close()
    return render_template('responder_location.html', report=report, responder=responder, live=live)


@app.route('/responder-location/<int:report_id>/update-location', methods=['POST'])
def update_responder_location(report_id):
    conn = sqlite3.connect(DB_PATH)
    report = get_report_by_id(conn, report_id)
    if not report or not report['assigned_responder_id']:
        conn.close()
        return redirect(url_for('responder_location_page', report_id=report_id))

    try:
        lat = float(request.form.get('lat', ''))
        lon = float(request.form.get('lon', ''))
    except (TypeError, ValueError):
        conn.close()
        return redirect(url_for('responder_location_page', report_id=report_id))

    # ADDED (Final Phase, Part 8): sanity-check coordinate ranges
    # rather than trusting raw client input blindly.
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        conn.close()
        return redirect(url_for('responder_location_page', report_id=report_id))

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    c = conn.cursor()
    c.execute('UPDATE responders SET current_lat = ?, current_lon = ?, last_location_update = ? WHERE id = ?',
              (lat, lon, now_str, report['assigned_responder_id']))
    conn.commit()
    log_event(conn, report_id, 'Location Updated', f'Responder shared live position ({lat:.4f}, {lon:.4f})')
    conn.close()
    return redirect(url_for('responder_location_page', report_id=report_id))


@app.route('/responder-location/<int:report_id>/set-stage', methods=['POST'])
def responder_set_stage(report_id):
    stage = request.form.get('stage', '')
    allowed = ['En Route', 'Arrived', 'Rescue In Progress', 'Rescue Completed']
    if stage not in allowed:
        return redirect(url_for('responder_location_page', report_id=report_id))

    conn = sqlite3.connect(DB_PATH)
    # ADDED (Final Phase, Part 8): verify the case actually exists
    # before writing anything — previously this ran blindly.
    report = get_report_by_id(conn, report_id)
    if not report:
        conn.close()
        return redirect(url_for('home'))

    c = conn.cursor()
    if stage == 'Rescue Completed':
        c.execute("UPDATE reports SET rescue_stage = ?, status = 'Animal Rescued' WHERE id = ?", (stage, report_id))
        conn.commit()
        if report['assigned_responder_id']:
            c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                      (report['assigned_responder_id'],))
            conn.commit()
    else:
        c.execute('UPDATE reports SET rescue_stage = ? WHERE id = ?', (stage, report_id))
        conn.commit()
    log_event(conn, report_id, stage, 'Updated by responder')
    conn.close()
    return redirect(url_for('responder_location_page', report_id=report_id))


@app.route('/reports-login', methods=['GET', 'POST'])
def reports_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password', '') == REPORTS_PASSWORD:
            session['reports_authenticated'] = True
            return redirect(url_for('view_reports'))
        error = "Incorrect password. Please try again."
    return render_template('reports_login.html', error=error)


@app.route('/reports-logout')
def reports_logout():
    session.pop('reports_authenticated', None)
    return redirect(url_for('reports_login'))


@app.route('/reports')
def view_reports():
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))

    conn = sqlite3.connect(DB_PATH)
    run_escalation_check(conn)

    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports ORDER BY id DESC')
    reports = c.fetchall()

    responders = get_all_responders(conn)
    responders_by_id = {r['id']: r for r in responders}

    enriched_reports = []
    for r in reports:
        d = dict(r)
        d['assigned_responder'] = responders_by_id.get(r['assigned_responder_id'])
        d['elapsed'] = human_elapsed(r['submitted_at'])
        d['events'] = get_case_events(conn, r['id'])
        live = get_live_tracking_data(conn, r)
        d['live_lat'] = live['live_lat']
        d['live_lon'] = live['live_lon']
        d['is_live'] = live['is_live']
        d['live_distance_km'] = live['live_distance_km']
        d['live_eta_minutes'] = live['live_eta_minutes']
        d['simple_status'] = live['simple_status']
        enriched_reports.append(d)

    # ADDED (Final Phase, Parts 3 & 4)
    disaster_active, disaster_reason, disaster_stats = is_disaster_mode_active(conn)
    notif_unread = unread_count(conn)

    conn.close()
    return render_template('reports.html', reports=enriched_reports, stages=STATUS_STAGES,
                            responders=responders, lifecycle=RESCUE_LIFECYCLE,
                            disaster_active=disaster_active, disaster_reason=disaster_reason,
                            disaster_stats=disaster_stats, notif_unread=notif_unread)


# ============================================================
# ADDED (Final Phase, Part 1): Rescue Intelligence Dashboard.
# Every number here is computed live from the existing reports/
# responders tables — nothing fabricated, and it degrades
# gracefully to zeros/empty states if the database has no data yet.
# ============================================================
@app.route('/dashboard')
def dashboard():
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('SELECT * FROM reports')
    all_reports = c.fetchall()
    total_reports = len(all_reports)

    active_cases = [r for r in all_reports if r['rescue_stage'] != 'Case Closed']
    closed_cases = [r for r in all_reports if r['rescue_stage'] == 'Case Closed']
    completed_cases = [r for r in all_reports if r['rescue_stage'] in ('Rescue Completed', 'Case Closed')]

    severity_counts = {'Critical': 0, 'High': 0, 'Moderate': 0, 'Low': 0}
    for r in all_reports:
        if r['severity'] in severity_counts:
            severity_counts[r['severity']] += 1

    escalated_cases = [r for r in all_reports if (r['escalation_count'] or 0) > 0]
    duplicate_flagged = [r for r in all_reports if r['possible_duplicate_of']]

    # Average time from submission to first responder assignment
    response_times = []
    for r in all_reports:
        events = get_case_events(conn, r['id'])
        submitted_evt = next((e for e in events if e['event_type'] == 'Report Received'), None)
        assigned_evt = next((e for e in events if e['event_type'] == 'Responder Assigned'), None)
        if submitted_evt and assigned_evt:
            try:
                t1 = datetime.strptime(submitted_evt['event_time'], '%Y-%m-%d %H:%M:%S')
                t2 = datetime.strptime(assigned_evt['event_time'], '%Y-%m-%d %H:%M:%S')
                response_times.append((t2 - t1).total_seconds())
            except (TypeError, ValueError):
                pass
    avg_response_seconds = round(sum(response_times) / len(response_times)) if response_times else None

    # Average time from submission to rescue completion
    completion_times = []
    for r in completed_cases:
        events = get_case_events(conn, r['id'])
        submitted_evt = next((e for e in events if e['event_type'] == 'Report Received'), None)
        completed_evt = next((e for e in events if e['event_type'] == 'Rescue Completed'), None)
        if submitted_evt and completed_evt:
            try:
                t1 = datetime.strptime(submitted_evt['event_time'], '%Y-%m-%d %H:%M:%S')
                t2 = datetime.strptime(completed_evt['event_time'], '%Y-%m-%d %H:%M:%S')
                completion_times.append((t2 - t1).total_seconds())
            except (TypeError, ValueError):
                pass
    avg_completion_seconds = round(sum(completion_times) / len(completion_times)) if completion_times else None

    responders = get_all_responders(conn)
    available_responders = [r for r in responders if r['available']]
    busy_responders = [r for r in responders if not r['available'] or (r['active_cases'] or 0) > 0]
    total_workload = sum(r['active_cases'] or 0 for r in responders)

    location_counts = {}
    for r in all_reports:
        loc_key = (r['location'] or 'Unknown')[:40]
        location_counts[loc_key] = location_counts.get(loc_key, 0) + 1
    top_locations = sorted(location_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    disaster_active, disaster_reason, disaster_stats = is_disaster_mode_active(conn)
    conn.close()

    def fmt_duration(seconds):
        if seconds is None:
            return '—'
        if seconds < 60:
            return f'{int(seconds)}s'
        minutes = seconds / 60
        if minutes < 60:
            return f'{minutes:.1f}m'
        return f'{minutes/60:.1f}h'

    return render_template('dashboard.html',
        total_reports=total_reports,
        active_count=len(active_cases),
        closed_count=len(closed_cases),
        completed_count=len(completed_cases),
        severity_counts=severity_counts,
        escalated_count=len(escalated_cases),
        duplicate_count=len(duplicate_flagged),
        avg_response=fmt_duration(avg_response_seconds),
        avg_completion=fmt_duration(avg_completion_seconds),
        total_responders=len(responders),
        available_count=len(available_responders),
        busy_count=len(busy_responders),
        total_workload=total_workload,
        top_locations=top_locations,
        disaster_active=disaster_active,
        disaster_reason=disaster_reason,
        disaster_stats=disaster_stats,
    )


# ADDED (Final Phase, Part 4): notifications feed
@app.route('/notifications')
def notifications_page():
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    notifs = get_notifications(conn, limit=50)
    conn.close()
    return render_template('notifications.html', notifications=notifs)


@app.route('/notifications/mark-read', methods=['POST'])
def notifications_mark_read():
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    mark_all_read(conn)
    conn.close()
    return redirect(url_for('notifications_page'))


# ADDED (Final Phase, Part 5): responder verification toggle (admin only)
@app.route('/responder/<int:responder_id>/toggle-verified', methods=['POST'])
def toggle_verified(responder_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT verified FROM responders WHERE id = ?', (responder_id,))
    row = c.fetchone()
    if row is not None:
        new_val = 0 if row[0] else 1
        c.execute('UPDATE responders SET verified = ? WHERE id = ?', (new_val, responder_id))
        conn.commit()
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/update-status/<int:report_id>', methods=['POST'])
def update_status(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    new_status = request.form.get('status', '')
    if new_status in STATUS_STAGES:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE reports SET status = ? WHERE id = ?', (new_status, report_id))
        conn.commit()
        conn.close()
    return redirect(url_for('view_reports'))


@app.route('/reassign-responder/<int:report_id>', methods=['POST'])
def reassign_responder(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    report = get_report_by_id(conn, report_id)
    if report:
        old_id = report['assigned_responder_id']
        if old_id:
            c = conn.cursor()
            c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?', (old_id,))
            conn.commit()
        assign_best_responder(conn, report_id, report['species'], report['severity'], report['priority'],
                               report['description'], incident_lat=report['incident_lat'],
                               incident_lon=report['incident_lon'], is_reassignment=True)
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/confirm-response/<int:report_id>', methods=['POST'])
def confirm_response(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET response_status = 'Responded', rescue_stage = 'Accepted' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Accepted', 'Confirmed via admin dashboard')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/accept', methods=['POST'])
def accept_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET response_status = 'Responded', rescue_stage = 'Accepted' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Accepted', '')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/decline', methods=['POST'])
def decline_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    handle_decline(conn, report_id)
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/en-route', methods=['POST'])
def en_route_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET rescue_stage = 'En Route' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'En Route', '')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/arrived', methods=['POST'])
def arrived_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET rescue_stage = 'Arrived' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Arrived', '')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/start-rescue', methods=['POST'])
def start_rescue_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET rescue_stage = 'Rescue In Progress' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Rescue In Progress', '')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/complete', methods=['POST'])
def complete_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    report = get_report_by_id(conn, report_id)
    c = conn.cursor()
    c.execute("UPDATE reports SET rescue_stage = 'Rescue Completed', status = 'Animal Rescued' WHERE id = ?", (report_id,))
    conn.commit()
    if report and report['assigned_responder_id']:
        c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                  (report['assigned_responder_id'],))
        conn.commit()
    log_event(conn, report_id, 'Rescue Completed', '')
    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/case/<int:report_id>/close', methods=['POST'])
def close_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET rescue_stage = 'Case Closed', status = 'Recovering' WHERE id = ?", (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Case Closed', 'Resolved — full history preserved')
    conn.close()
    return redirect(url_for('view_reports'))


if __name__ == '__main__':
    app.run(debug=True, port=5001)
