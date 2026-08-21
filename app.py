from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os
import time

app = Flask(__name__)

# ============================================================
# ADDED: Secret key required for login sessions to work.
# IMPORTANT: Change this to your own random string before
# real use — anyone who knows this value could forge sessions.
# ============================================================
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-to-a-random-string-oneearth-2026')

# ============================================================
# ADDED: Password for viewing /reports.
# IMPORTANT: Change this to your own password. For real use,
# set it as an environment variable in Render instead of
# hardcoding it here (Render dashboard -> Environment).
# ============================================================
REPORTS_PASSWORD = os.environ.get('REPORTS_PASSWORD', 'oneearth2026')

# ============================================================
# Database setup for storing emergency reports
# ============================================================
DB_PATH = os.path.join(os.path.dirname(__file__), 'reports.db')

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
            submitted_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ============================================================
# Basic rate limiting (per IP) to reduce spam submissions
# ============================================================
last_submission_by_ip = {}
MIN_SECONDS_BETWEEN_SUBMISSIONS = 20

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/report', methods=['GET', 'POST'])
def report_rescue():
    if request.method == 'POST':
        # Honeypot spam check
        honeypot = request.form.get('website', '')
        if honeypot:
            return redirect(url_for('success'))

        # Per-IP rate limiting
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

        # Server-side validation
        required_fields = [species, urgency, reporter_name, phone, location, description]
        if not all(required_fields):
            return redirect(url_for('report_rescue'))

        if len(phone) < 7 or len(description) < 5:
            return redirect(url_for('report_rescue'))

        submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO reports (species, urgency, reporter_name, phone, location, description, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (species, urgency, reporter_name, phone, location, description, submitted_at))
        conn.commit()
        conn.close()

        return redirect(url_for('success'))
    return render_template('report.html')

@app.route('/directory')
def directory():
    return render_template('directory.html')

@app.route('/success')
def success():
    return render_template('success.html')

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

# ============================================================
# ADDED: Password-protected reports view.
# ============================================================
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
    return render_template('reports.html', reports=reports)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
