"""
OneEarth — In-App Notifications (Final Phase, Part 4)
=======================================================
Lightweight notification log built on the existing SQLite database.
This does NOT duplicate event-tracking logic — app.py's existing
log_event() function (used since Phase 3 for the case timeline) also
calls log_notification() from here, so every lifecycle event already
being recorded automatically produces a notification too, with zero
new call sites needed elsewhere in the reporting/rescue workflow.
"""

import time
import sqlite3


def log_notification(conn, message, event_type='', report_id=None):
    c = conn.cursor()
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        INSERT INTO notifications (message, event_type, report_id, created_at, is_read)
        VALUES (?, ?, ?, ?, 0)
    ''', (message, event_type, report_id, now_str))
    conn.commit()


def get_notifications(conn, limit=30, unread_only=False):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if unread_only:
        c.execute('SELECT * FROM notifications WHERE is_read = 0 ORDER BY id DESC LIMIT ?', (limit,))
    else:
        c.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT ?', (limit,))
    return c.fetchall()


def unread_count(conn):
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM notifications WHERE is_read = 0')
    return c.fetchone()[0]


def mark_all_read(conn):
    c = conn.cursor()
    c.execute('UPDATE notifications SET is_read = 1')
    conn.commit()
