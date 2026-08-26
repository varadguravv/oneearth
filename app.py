from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os
import time

# ADDED (Phase 1): risk assessment is a separate, swappable module —
# see risk_assessment.py for the scoring logic itself.
from risk_assessment import calculate_risk_assessment

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

    # Existing migration pattern: add any missing columns without
    # losing existing data. Untouched from before, plus the new
    # risk-assessment columns added the same safe way.
    c.execute("PRAGMA table_info(reports)")
    columns = [row[1] for row in c.fetchall()]

    if 'status' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN status TEXT DEFAULT 'Report Received'")
        conn.commit()

    # ADDED (Phase 1): risk assessment columns
    if 'risk_score' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN risk_score INTEGER")
        conn.commit()
    if 'severity' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN severity TEXT")
        conn.commit()
    if 'priority' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN priority TEXT")
        conn.commit()
    if 'recommended_action' not in columns:
        c.execute("ALTER TABLE reports ADD COLUMN recommended_action TEXT")
        conn.commit()

    conn.close()


init_db()

last_submission_by_ip = {}
MIN_SECONDS_BETWEEN_SUBMISSIONS = 20


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

        # ADDED (Phase 1): compute the risk assessment for this specific
        # report before saving. Different inputs -> different results,
        # since calculate_risk_assessment() is a pure function of the
        # fields already collected by this existing form.
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
        conn.close()

        return redirect(url_for('success', case_id=case_id))
    return render_template('report.html')


@app.route('/directory')
def directory():
    return render_template('directory.html')


@app.route('/success')
def success():
    case_id = request.args.get('case_id')
    # ADDED (Phase 1): fetch the just-created report so the success page
    # can show its risk assessment. Falls back gracefully if no case_id
    # is present (e.g. spam/rate-limit redirects still work as before).
    report = None
    if case_id and case_id.isdigit():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
        report = c.fetchone()
        conn.close()
    return render_template('success.html', case_id=case_id, report=report)


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
    if request.method == 'POST':
        case_id = request.form.get('case_id', '').strip()
        if case_id.isdigit():
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM reports WHERE id = ?', (case_id,))
            result = c.fetchone()
            conn.close()
            if not result:
                error = "No case found with that number. Please check and try again."
        else:
            error = "Please enter a valid case number (numbers only)."
    return render_template('check_status.html', result=result, error=error, stages=STATUS_STAGES)


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
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports ORDER BY id DESC')
    reports = c.fetchall()
    conn.close()
    return render_template('reports.html', reports=reports, stages=STATUS_STAGES)


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


if __name__ == '__main__':
    app.run(debug=True, port=5001)
