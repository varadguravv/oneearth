from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
import time

app = Flask(__name__)

# ============================================================
# ADDED: Database setup for storing emergency reports
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
# ADDED: Basic rate limiting (per IP) to reduce spam submissions
# In-memory only — resets on app restart, which is fine for
# basic abuse prevention without adding external dependencies.
# ============================================================
last_submission_by_ip = {}
MIN_SECONDS_BETWEEN_SUBMISSIONS = 20

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/report', methods=['GET', 'POST'])
def report_rescue():
    if request.method == 'POST':
        # ADDED: Honeypot spam check — a hidden field real users never fill in.
        # Bots that auto-fill every form field will trip this and get silently
        # redirected without their fake report being saved.
        honeypot = request.form.get('website', '')
        if honeypot:
            return redirect(url_for('success'))

        # ADDED: Simple per-IP rate limiting
        ip = request.remote_addr or 'unknown'
        now = time.time()
        last_time = last_submission_by_ip.get(ip, 0)
        if now - last_time < MIN_SECONDS_BETWEEN_SUBMISSIONS:
            return redirect(url_for('success'))
        last_submission_by_ip[ip] = now

        # ADDED: Save the report to the database
        species = request.form.get('species', '')
        urgency = request.form.get('urgency', '')
        reporter_name = request.form.get('reporter_name', '')
        phone = request.form.get('phone', '')
        location = request.form.get('location', '')
        description = request.form.get('description', '')
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

# ============================================================
# ADDED: Simple admin view to see submitted reports
# No authentication yet — fine for local testing, but before
# using this in production you'd want to add a password check
# so the public can't view everyone's report details.
# ============================================================
@app.route('/reports')
def view_reports():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM reports ORDER BY id DESC')
    reports = c.fetchall()
    conn.close()
    return render_template('reports.html', reports=reports)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
