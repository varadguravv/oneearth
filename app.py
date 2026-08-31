from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os
import time
from datetime import datetime

# Phase 1 — unchanged
from risk_assessment import calculate_risk_assessment

# Phase 2 — unchanged
from responder_matching import recommend_responders

# Phase 3 — unchanged
from escalation import needs_escalation

# Phase 4 — new, separate module
from location_utils import parse_incident_coordinates, estimate_eta_minutes

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'change-this-to-a-random-string-oneearth-2026')
REPORTS_PASSWORD = os.environ.get('REPORTS_PASSWORD', 'oneearth2026')

DB_PATH = os.path.join(os.path.dirname(__file__), 'reports.db')

STATUS_STAGES = [
    'Report Received',
    'Rescue Team Assigned',
    'Team En Route',
    'Animal Rescued',
    'Under Treatment',
    'Recovering'
]

RESCUE_LIFECYCLE = [
    'Reported', 'Assessed', 'Assigned', 'Accepted',
    'En Route', 'Arrived', 'Rescue Completed', 'Case Closed',
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


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species TEXT,
            urgency TEXT,
            reporter_name TEXT,
            phone TEXT,
            location TEXT,
            description TEXT,
            submitted_at TEXT,
            status TEXT DEFAULT 'Report Received'
        )
    ''')
    conn.commit()

    def ensure_column(col, coltype):
        c.execute("PRAGMA table_info(reports)")
        existing = [row[1] for row in c.fetchall()]
        if col not in existing:
            c.execute(f"ALTER TABLE reports ADD COLUMN {col} {coltype}")
            conn.commit()

    ensure_column('status', "TEXT DEFAULT 'Report Received'")
    # Phase 1
    ensure_column('risk_score', 'INTEGER')
    ensure_column('severity', 'TEXT')
    ensure_column('priority', 'TEXT')
    ensure_column('recommended_action', 'TEXT')
    # Phase 2
    ensure_column('required_responder_type', 'TEXT')
    ensure_column('assigned_responder_id', 'INTEGER')
    ensure_column('assignment_time', 'TEXT')
    ensure_column('response_status', "TEXT DEFAULT 'Unassigned'")
    ensure_column('escalation_count', 'INTEGER DEFAULT 0')
    # Phase 3
    ensure_column('rescue_stage', "TEXT DEFAULT 'Reported'")
    # ADDED (Phase 4): incident location + distance/ETA columns
    ensure_column('incident_lat', 'REAL')
    ensure_column('incident_lon', 'REAL')
    ensure_column('location_precise', 'INTEGER DEFAULT 0')
    ensure_column('distance_km', 'REAL')
    ensure_column('eta_minutes', 'INTEGER')

    c.execute('''
        CREATE TABLE IF NOT EXISTS responders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            responder_type TEXT,
            phone TEXT,
            location TEXT,
            latitude REAL,
            longitude REAL,
            available INTEGER DEFAULT 1,
            active_cases INTEGER DEFAULT 0,
            capabilities TEXT
        )
    ''')
    conn.commit()

    c.execute("SELECT COUNT(*) FROM responders")
    if c.fetchone()[0] == 0:
        c.executemany('''
            INSERT INTO responders
            (name, responder_type, phone, location, latitude, longitude, available, active_cases, capabilities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', DEMO_RESPONDERS)
        conn.commit()

    c.execute('''
        CREATE TABLE IF NOT EXISTS case_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER,
            event_type TEXT,
            event_detail TEXT,
            event_time TEXT
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
    c.execute('''
        INSERT INTO case_events (report_id, event_type, event_detail, event_time)
        VALUES (?, ?, ?, ?)
    ''', (report_id, event_type, event_detail, now_str))
    conn.commit()


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
    seconds = int((datetime.now() - dt).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours}h {remaining_minutes}m"


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


def assign_best_responder(conn, report_id, species, severity, priority, description,
                           incident_lat=None, incident_lon=None, is_reassignment=False):
    """
    Unchanged Phase 2/3 pipeline, now also accepts the incident's real
    coordinates (Phase 4) so distance/ETA are computed against the
    actual reported location instead of always falling back to the
    default city-center point.
    """
    c = conn.cursor()
    all_responders = get_all_responders(conn)

    required_types, ranked = recommend_responders(
        all_responders, species, severity, priority, description,
        incident_lat=incident_lat, incident_lon=incident_lon
    )
    required_type_str = ', '.join(required_types)

    if not ranked:
        c.execute('''
            UPDATE reports
            SET required_responder_type = ?, response_status = 'No Suitable Responder'
            WHERE id = ?
        ''', (required_type_str, report_id))
        conn.commit()
        log_event(conn, report_id, 'No Suitable Responder Found', f'Required: {required_type_str}')
        return None

    best_responder, score, distance_km = ranked[0]
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')

    # ADDED (Phase 4): store distance + estimated ETA alongside the assignment
    eta_minutes = estimate_eta_minutes(distance_km)

    c.execute('''
        UPDATE reports
        SET required_responder_type = ?, assigned_responder_id = ?,
            assignment_time = ?, response_status = 'Pending Response',
            rescue_stage = 'Assigned', distance_km = ?, eta_minutes = ?
        WHERE id = ?
    ''', (required_type_str, best_responder['id'], now_str, distance_km, eta_minutes, report_id))

    c.execute('UPDATE responders SET active_cases = active_cases + 1 WHERE id = ?', (best_responder['id'],))
    conn.commit()

    detail = f"{best_responder['name']} ({best_responder['responder_type']}), {distance_km} km away, ~{eta_minutes} min ETA"
    if is_reassignment:
        detail += ' — reassigned'
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

            all_responders = [r for r in get_all_responders(conn) if r['id'] != old_responder_id]

            if old_responder_id:
                c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                          (old_responder_id,))
                conn.commit()

            c.execute('UPDATE reports SET escalation_count = escalation_count + 1 WHERE id = ?', (report['id'],))
            conn.commit()

            assign_best_responder(
                conn, report['id'], report['species'], report['severity'],
                report['priority'], report['description'],
                incident_lat=report['incident_lat'], incident_lon=report['incident_lon'],
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
    log_event(conn, report_id, 'Declined',
              f"Declined by {old_responder['name'] if old_responder else 'responder'}")

    if old_responder_id:
        c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                  (old_responder_id,))
        conn.commit()

    assign_best_responder(
        conn, report_id, report['species'], report['severity'], report['priority'], report['description'],
        incident_lat=report['incident_lat'], incident_lon=report['incident_lon']
    )


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/report', methods=['GET', 'POST'])
def report_rescue():
    if request.method == 'POST':
        honeypot = request.form.get('website', '')
        if honeypot:
            return redirect(url_for('success'))

        ip = request.remote_addr or 'unknown'
        now = time.time()
        last_time = last_submission_by_ip.get(ip, 0)
        if now - last_time < MIN_SECONDS_BETWEEN_SUBMISSIONS:
            return redirect(url_for('success'))
        last_submission_by_ip[ip] = now

        species = request.form.get('species', '').strip()
        urgency = request.form.get('urgency', '').strip()
        reporter_name = request.form.get('reporter_name', '').strip()
        phone = request.form.get('phone', '').strip()
        location = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()

        required_fields = [species, urgency, reporter_name, phone, location, description]
        if not all(required_fields):
            return redirect(url_for('report_rescue'))

        if len(phone) < 7 or len(description) < 5:
            return redirect(url_for('report_rescue'))

        submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')

        # Phase 1 — unchanged
        assessment = calculate_risk_assessment(species, urgency, description, location)

        # ADDED (Phase 4): extract real coordinates from the location
        # text if the reporter used the existing map picker. Falls
        # back gracefully to an approximate point otherwise.
        incident_lat, incident_lon, location_precise = parse_incident_coordinates(location)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO reports (
                species, urgency, reporter_name, phone, location, description,
                submitted_at, status, risk_score, severity, priority, recommended_action,
                rescue_stage, incident_lat, incident_lon, location_precise
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            species, urgency, reporter_name, phone, location, description,
            submitted_at, 'Report Received',
            assessment['risk_score'], assessment['severity'],
            assessment['priority'], assessment['recommended_action'],
            'Assessed', incident_lat, incident_lon, int(location_precise)
        ))
        conn.commit()
        case_id = c.lastrowid

        log_event(conn, case_id, 'Report Received', f'Reported by {reporter_name}')
        log_event(conn, case_id, 'Risk Assessed',
                  f"{assessment['severity']} severity, risk score {assessment['risk_score']}/100")

        assign_best_responder(
            conn, case_id, species, assessment['severity'],
            assessment['priority'], description,
            incident_lat=incident_lat, incident_lon=incident_lon
        )
        conn.close()

        return redirect(url_for('success', case_id=case_id))
    return render_template('report.html')


@app.route('/directory')
def directory():
    return render_template('directory.html')


@app.route('/success')
def success():
    case_id = request.args.get('case_id')
    report = None
    assigned_responder = None
    elapsed = None
    if case_id and case_id.isdigit():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
        report = c.fetchone()
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
    result = None
    error = None
    assigned_responder = None
    elapsed = None
    events = []
    if request.method == 'POST':
        case_id = request.form.get('case_id', '').strip()
        if case_id.isdigit():
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
            result = c.fetchone()
            if result:
                if result['assigned_responder_id']:
                    assigned_responder = get_responder_by_id(conn, result['assigned_responder_id'])
                elapsed = human_elapsed(result['submitted_at'])
                events = get_case_events(conn, result['id'])
            conn.close()
            if not result:
                error = "No case found with that number. Please check and try again."
        else:
            error = "Please enter a valid case number (numbers only)."
    return render_template('check_status.html', result=result, error=error,
                            stages=STATUS_STAGES, assigned_responder=assigned_responder,
                            elapsed=elapsed, events=events)


@app.route('/reports-login', methods=['GET', 'POST'])
def reports_login():
    error = None
    if request.method == 'POST':
        entered_password = request.form.get('password', '')
        if entered_password == REPORTS_PASSWORD:
            session['reports_authenticated'] = True
            return redirect(url_for('view_reports'))
        else:
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
        enriched_reports.append(d)

    conn.close()

    return render_template('reports.html', reports=enriched_reports, stages=STATUS_STAGES,
                            responders=responders, lifecycle=RESCUE_LIFECYCLE)


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
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports WHERE id = ?', (report_id,))
    report = c.fetchone()

    if report:
        old_responder_id = report['assigned_responder_id']
        if old_responder_id:
            c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                      (old_responder_id,))
            conn.commit()

        assign_best_responder(
            conn, report_id, report['species'], report['severity'],
            report['priority'], report['description'],
            incident_lat=report['incident_lat'], incident_lon=report['incident_lon'],
            is_reassignment=True
        )

    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/confirm-response/<int:report_id>', methods=['POST'])
def confirm_response(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET response_status = 'Responded', rescue_stage = 'Accepted' WHERE id = ?",
              (report_id,))
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
    c.execute("UPDATE reports SET response_status = 'Responded', rescue_stage = 'Accepted' WHERE id = ?",
              (report_id,))
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


@app.route('/case/<int:report_id>/complete', methods=['POST'])
def complete_case(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT assigned_responder_id FROM reports WHERE id = ?', (report_id,))
    row = c.fetchone()
    c.execute("UPDATE reports SET rescue_stage = 'Rescue Completed', status = 'Animal Rescued' WHERE id = ?",
              (report_id,))
    conn.commit()
    if row and row['assigned_responder_id']:
        c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                  (row['assigned_responder_id'],))
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
    c.execute("UPDATE reports SET rescue_stage = 'Case Closed', status = 'Recovering' WHERE id = ?",
              (report_id,))
    conn.commit()
    log_event(conn, report_id, 'Case Closed', 'Full history preserved in reports table')
    conn.close()
    return redirect(url_for('view_reports'))


if __name__ == '__main__':
    app.run(debug=True, port=5001)
