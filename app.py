from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os
import time
from datetime import datetime

# Phase 1 — unchanged, still the only place risk is actually scored
from risk_assessment import calculate_risk_assessment

# Phase 2 — new, separate modules for responder matching + escalation
from responder_matching import recommend_responders, DEFAULT_INCIDENT_LAT, DEFAULT_INCIDENT_LON
from escalation import needs_escalation

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

# ============================================================
# ADDED (Phase 2): demo responder seed data.
# Realistic sample data only — no real people. Coordinates are
# spread around the same Pune-area coordinates already used
# throughout the site's demo maps, so distance scoring has
# something meaningful to differentiate on.
# ============================================================
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

    c.execute("PRAGMA table_info(reports)")
    columns = [row[1] for row in c.fetchall()]

    if 'status' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN status TEXT DEFAULT 'Report Received'")
        conn.commit()

    # Phase 1 columns (unchanged from before)
    for col, coltype in [
        ('risk_score', 'INTEGER'),
        ('severity', 'TEXT'),
        ('priority', 'TEXT'),
        ('recommended_action', 'TEXT'),
    ]:
        if col not in columns:
            c.execute(f"ALTER TABLE reports ADD COLUMN {col} {coltype}")
            conn.commit()

    # ============================================================
    # ADDED (Phase 2): responder-assignment columns on the existing
    # reports table, using the exact same safe migration pattern
    # already used for every prior column. Existing rows are
    # untouched; new columns default to NULL/'Unassigned'.
    # ============================================================
    for col, coltype in [
        ('required_responder_type', 'TEXT'),
        ('assigned_responder_id', 'INTEGER'),
        ('assignment_time', 'TEXT'),
        ('response_status', "TEXT DEFAULT 'Unassigned'"),
        ('escalation_count', 'INTEGER DEFAULT 0'),
    ]:
        c.execute("PRAGMA table_info(reports)")
        current_cols = [row[1] for row in c.fetchall()]
        if col not in current_cols:
            c.execute(f"ALTER TABLE reports ADD COLUMN {col} {coltype}")
            conn.commit()

    # ============================================================
    # ADDED (Phase 2): new, separate responders table.
    # ============================================================
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

    # Seed demo responders only if the table is empty, so re-running
    # the app never duplicates or resets responder data.
    c.execute("SELECT COUNT(*) FROM responders")
    count = c.fetchone()[0]
    if count == 0:
        c.executemany('''
            INSERT INTO responders
            (name, responder_type, phone, location, latitude, longitude, available, active_cases, capabilities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', DEMO_RESPONDERS)
        conn.commit()

    conn.close()


init_db()

last_submission_by_ip = {}
MIN_SECONDS_BETWEEN_SUBMISSIONS = 20


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


def assign_best_responder(conn, report_id, species, severity, priority, description):
    """
    ADDED (Phase 2): runs the full recommend -> assign pipeline for a
    single report. Used both at submission time and during manual
    reassignment/escalation, so the logic only lives in one place.
    Updates the report row AND the responders' active_cases counts.
    Returns the assigned responder row, or None if nobody was suitable.
    """
    c = conn.cursor()
    all_responders = get_all_responders(conn)

    required_types, ranked = recommend_responders(
        all_responders, species, severity, priority, description
    )
    required_type_str = ', '.join(required_types)

    if not ranked:
        c.execute('''
            UPDATE reports
            SET required_responder_type = ?, response_status = 'No Suitable Responder'
            WHERE id = ?
        ''', (required_type_str, report_id))
        conn.commit()
        return None

    best_responder, score, distance_km = ranked[0]
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')

    c.execute('''
        UPDATE reports
        SET required_responder_type = ?, assigned_responder_id = ?,
            assignment_time = ?, response_status = 'Pending Response'
        WHERE id = ?
    ''', (required_type_str, best_responder['id'], now_str, report_id))

    c.execute('''
        UPDATE responders SET active_cases = active_cases + 1 WHERE id = ?
    ''', (best_responder['id'],))
    conn.commit()

    return best_responder


def run_escalation_check(conn):
    """
    ADDED (Phase 2): on-demand escalation sweep, called each time the
    admin reports dashboard loads. No background scheduler needed for
    this phase — checking on page load is sufficient to demonstrate
    the full escalation flow locally.
    """
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE response_status = 'Pending Response'")
    pending = c.fetchall()

    for report in pending:
        if needs_escalation(report):
            old_responder_id = report['assigned_responder_id']

            # Free up the old responder's slot before searching again,
            # and exclude them from re-selection this round.
            all_responders = [r for r in get_all_responders(conn) if r['id'] != old_responder_id]

            required_types, ranked = recommend_responders(
                all_responders, report['species'], report['severity'],
                report['priority'], report['description']
            )

            if old_responder_id:
                c.execute('UPDATE responders SET active_cases = MAX(active_cases - 1, 0) WHERE id = ?',
                          (old_responder_id,))

            if ranked:
                new_responder, score, distance_km = ranked[0]
                now_str = time.strftime('%Y-%m-%d %H:%M:%S')
                c.execute('''
                    UPDATE reports
                    SET assigned_responder_id = ?, assignment_time = ?,
                        response_status = 'Pending Response',
                        escalation_count = escalation_count + 1
                    WHERE id = ?
                ''', (new_responder['id'], now_str, report['id']))
                c.execute('UPDATE responders SET active_cases = active_cases + 1 WHERE id = ?',
                          (new_responder['id'],))
            else:
                c.execute('''
                    UPDATE reports
                    SET response_status = 'Escalated - No Responder Available',
                        escalation_count = escalation_count + 1
                    WHERE id = ?
                ''', (report['id'],))
            conn.commit()


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

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO reports (
                species, urgency, reporter_name, phone, location, description,
                submitted_at, status, risk_score, severity, priority, recommended_action
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            species, urgency, reporter_name, phone, location, description,
            submitted_at, 'Report Received',
            assessment['risk_score'], assessment['severity'],
            assessment['priority'], assessment['recommended_action']
        ))
        conn.commit()
        case_id = c.lastrowid

        # ADDED (Phase 2): automatically determine + assign the best
        # available responder right after the report is created.
        assign_best_responder(
            conn, case_id, species, assessment['severity'],
            assessment['priority'], description
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
    if case_id and case_id.isdigit():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
        report = c.fetchone()
        if report and report['assigned_responder_id']:
            assigned_responder = get_responder_by_id(conn, report['assigned_responder_id'])
        conn.close()
    return render_template('success.html', case_id=case_id, report=report,
                            assigned_responder=assigned_responder)


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
    if request.method == 'POST':
        case_id = request.form.get('case_id', '').strip()
        if case_id.isdigit():
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
            result = c.fetchone()
            if result and result['assigned_responder_id']:
                assigned_responder = get_responder_by_id(conn, result['assigned_responder_id'])
            conn.close()
            if not result:
                error = "No case found with that number. Please check and try again."
        else:
            error = "Please enter a valid case number (numbers only)."
    return render_template('check_status.html', result=result, error=error,
                            stages=STATUS_STAGES, assigned_responder=assigned_responder)


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

    # ADDED (Phase 2): run the on-demand escalation sweep before
    # displaying the dashboard, so any newly-overdue P1/P2 cases get
    # reassigned before the admin even looks at them.
    run_escalation_check(conn)

    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports ORDER BY id DESC')
    reports = c.fetchall()

    responders = get_all_responders(conn)
    responders_by_id = {r['id']: r for r in responders}
    conn.close()

    # Enrich each report with its assigned responder's details for
    # easy display in the template (sqlite3.Row is read-only, so we
    # build plain dicts here rather than mutating the rows).
    enriched_reports = []
    for r in reports:
        d = dict(r)
        d['assigned_responder'] = responders_by_id.get(r['assigned_responder_id'])
        enriched_reports.append(d)

    return render_template('reports.html', reports=enriched_reports, stages=STATUS_STAGES,
                            responders=responders)


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


# ============================================================
# ADDED (Phase 2): manual admin actions — reassign a case to a
# different responder, or confirm that the assigned responder
# has responded. Both require the existing admin login.
# ============================================================
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
            report['priority'], report['description']
        )
        # Manual reassignment doesn't count as an automatic escalation,
        # but we still want it clearly separate from the original
        # assignment — no escalation_count change here.

    conn.close()
    return redirect(url_for('view_reports'))


@app.route('/confirm-response/<int:report_id>', methods=['POST'])
def confirm_response(report_id):
    if not session.get('reports_authenticated'):
        return redirect(url_for('reports_login'))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reports SET response_status = 'Responded' WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('view_reports'))


if __name__ == '__main__':
    app.run(debug=True, port=5001)
