"""
Single-file LAUMC Reminder application

Usage:
  python laumc_reminder_single.py --init-db      # initialize local SQLite DB and defaults
  python laumc_reminder_single.py --run-server   # run the Flask admin UI
  python laumc_reminder_single.py --run-job      # run the reminder job once

This script bundles a minimal Flask UI, SQLite metadata, Oracle query, phone normalization,
and reminder sending logic so the app can be run from one file if desired.
"""
from __future__ import annotations
import os
import re
import sys
import smtplib
import argparse
import subprocess
from email.message import EmailMessage
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Flask, request, redirect, url_for, flash, render_template_string, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

try:
    import oracledb
except Exception:
    oracledb = None

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'monitor_single.db'
DEFAULT_DB_URI = f'sqlite:///{DB_PATH}'

class SimpleConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'replace-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get('APP_DATABASE_URI', DEFAULT_DB_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ORACLE_USER = os.environ.get('ORACLE_USER', '')
    ORACLE_PASSWORD = os.environ.get('ORACLE_PASSWORD', '')
    ORACLE_DSN = os.environ.get('ORACLE_DSN', '')
    ORACLE_TIMEZONE = os.environ.get('ORACLE_TIMEZONE', 'Asia/Beirut')
    EMAIL_GATEWAY_DOMAIN = os.environ.get('EMAIL_GATEWAY_DOMAIN', 'broadnetsms.com')
    HOSPITAL_NAME = os.environ.get('HOSPITAL_NAME', 'LAUMC')
    SITE_CODE_MAP = {'SAP_PROD': 'LAUMC-RH', 'SAP_SJH': 'LAUMC-SJH'}

# --- App & DB setup ---
app = Flask(__name__)
app.config.from_object(SimpleConfig)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

PHONE_RE = re.compile(r'\d+')

# --- Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, pw:str):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw:str) -> bool:
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, pw)

class SMTPConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    smtp_server = db.Column(db.String(256), nullable=True)
    smtp_port = db.Column(db.Integer, default=587)
    use_tls = db.Column(db.Boolean, default=True)
    use_ssl = db.Column(db.Boolean, default=False)
    username = db.Column(db.String(256), nullable=True)
    password = db.Column(db.String(256), nullable=True)
    from_address = db.Column(db.String(256), nullable=True)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

class MessageTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_code = db.Column(db.String(64), unique=True, nullable=False)
    subject_template = db.Column(db.String(256), nullable=False)
    body_template = db.Column(db.Text, nullable=False)

class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.String(128), nullable=False)
    phone_number = db.Column(db.String(32), nullable=False)
    email_address = db.Column(db.String(256), nullable=False)
    scheduled_date = db.Column(db.DateTime, nullable=False)
    site_code = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='pending')
    attempt_count = db.Column(db.Integer, default=0)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    message_body = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Utilities ---
def normalize_phone(raw_phone: str) -> str | None:
    if not raw_phone:
        return None
    digits = re.sub(r'\D', '', raw_phone)
    if not digits:
        return None
    # handle variants
    if digits.startswith('00961'):
        digits = digits[2:]
    if digits.startswith('+'):
        digits = digits.lstrip('+')
    if digits.startswith('961') and len(digits) >= 11:
        return digits
    if digits.startswith('0') and len(digits) == 8:
        return '961' + digits[1:]
    if len(digits) == 8:
        return '961' + digits
    if len(digits) == 9 and digits.startswith('0'):
        return '961' + digits[1:]
    return None

def connect_oracle(user=None, password=None, dsn=None):
    if oracledb is None:
        raise RuntimeError('oracledb package not installed')
    user = user or SimpleConfig.ORACLE_USER
    password = password or SimpleConfig.ORACLE_PASSWORD
    dsn = dsn or SimpleConfig.ORACLE_DSN
    if not (user and password and dsn):
        raise RuntimeError('Oracle credentials not configured')
    return oracledb.connect(user=user, password=password, dsn=dsn, encoding='UTF-8', nencoding='UTF-8')

def get_reminder_window():
    tz = ZoneInfo(SimpleConfig.ORACLE_TIMEZONE)
    now_local = datetime.now(tz)
    start = now_local + timedelta(hours=24)
    end = now_local + timedelta(hours=25, minutes=30)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)

def fetch_earliest_exams():
    start_dt, end_dt = get_reminder_window()
    sql = '''
SELECT * FROM (
    SELECT
        pid.patient_id,
        pe.PATIENT_PHONE_NUMBER AS raw_phone,
        sw.scheduled_date,
        ord.ISSUER_OF_PLACER_ORDER_NUMBER AS site_code,
        ROW_NUMBER() OVER (PARTITION BY pid.patient_id ORDER BY sw.scheduled_date) rn
    FROM site_worklist sw
    JOIN PATIENT_ID_LIST pid ON sw.patient_person_key = pid.patient_person_key
    JOIN site_patient pa ON sw.patient_person_key = pa.patient_person_key
    JOIN site_person pe ON pa.patient_person_key = pe.person_key
    JOIN ORDERS ord ON sw.order_key = ord.order_key
    WHERE sw.status_key = 40
      AND sw.scheduled_date BETWEEN :start_dt AND :end_dt
)
WHERE rn = 1
'''
    conn = connect_oracle()
    cur = conn.cursor()
    cur.execute(sql, [start_dt, end_dt])
    cols = [c[0].lower() for c in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close(); conn.close()
    out = []
    for r in rows:
        norm = normalize_phone(r.get('raw_phone'))
        if not norm:
            continue
        raw_site = r.get('site_code') or 'SAP_PROD'
        mapped = SimpleConfig.SITE_CODE_MAP.get(raw_site, raw_site)
        out.append({'patient_id': r['patient_id'], 'phone_number': norm, 'scheduled_date': r['scheduled_date'], 'site_code': mapped})
    return out

def send_via_smtp(smtp_cfg: SMTPConfig, to_addr: str, subject: str, body: str):
    msg = EmailMessage()
    msg['From'] = smtp_cfg.from_address or SimpleConfig.HOSPITAL_NAME
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.set_content(body)
    if smtp_cfg.use_ssl:
        server = smtplib.SMTP_SSL(smtp_cfg.smtp_server, smtp_cfg.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp_cfg.smtp_server, smtp_cfg.smtp_port, timeout=30)
    try:
        server.ehlo()
        if smtp_cfg.use_tls and not smtp_cfg.use_ssl:
            server.starttls(); server.ehlo()
        if smtp_cfg.username and smtp_cfg.password:
            server.login(smtp_cfg.username, smtp_cfg.password)
        server.send_message(msg)
    finally:
        server.quit()

def create_or_update_logs(candidates):
    for c in candidates:
        email_address = f"{c['phone_number']}@{SimpleConfig.EMAIL_GATEWAY_DOMAIN}"
        existing = EmailLog.query.filter_by(patient_id=c['patient_id'], scheduled_date=c['scheduled_date'], site_code=c['site_code']).first()
        if existing:
            if existing.status == 'sent':
                continue
        existing.phone_number = c['phone_number']
        existing.email_address = email_address
        db.session.commit()
        continue
    log = EmailLog(patient_id=c['patient_id'], phone_number=c['phone_number'], email_address=email_address, scheduled_date=c['scheduled_date'], site_code=c['site_code'], status='pending')
    db.session.add(log)
    db.session.commit()

def process_pending_logs():
    now = datetime.utcnow()
    logs = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.scheduled_date >= now).order_by(EmailLog.scheduled_date).all()
    smtp_cfg = SMTPConfig.query.first()
    for log in logs:
        subject = SimpleConfig.HOSPITAL_NAME
        date_text = log.scheduled_date.strftime('%d-%m-%Y')
        time_text = log.scheduled_date.strftime('%H:%M')
        body = f"Your Radiology appointment is confirmed on {date_text} at {time_text}\n\nPlease bring original prescription and imaging results done outside LAUMC\n\nInfo: 01-200800 ext.5070"
        try:
            send_via_smtp(smtp_cfg, log.email_address, subject, body)
            log.status = 'sent'; log.error_message = None
        except Exception as exc:
            log.status = 'failed'; log.error_message = str(exc)
        finally:
            log.attempt_count += 1
            log.last_attempt_at = datetime.utcnow()
            log.message_body = body
            db.session.commit()

# --- Minimal Flask UI (render_template_string for single-file) ---
LOGIN_HTML = '''
<form method="post">Username: <input name="username"><br>Password: <input name="password" type="password"><br><button type="submit">Login</button></form>
'''

@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()
        if u and u.check_password(request.form['password']):
            login_user(u); return redirect(url_for('setup'))
        flash('Invalid')
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('login'))

@app.route('/setup', methods=['GET','POST'])
@login_required
def setup():
    if request.method == 'POST':
        # simple save of env-like keys
        keys = ['ORACLE_USER','ORACLE_PASSWORD','ORACLE_DSN','ORACLE_TIMEZONE','EMAIL_GATEWAY_DOMAIN','HOSPITAL_NAME','SECRET_KEY']
        env_lines = []
        for k in keys:
            env_lines.append(f"{k}={request.form.get(k,'')}")
        env_lines.append(f"APP_DATABASE_URI={app.config['SQLALCHEMY_DATABASE_URI']}")
        (BASE_DIR / '.env').write_text('\n'.join(env_lines))
        # set admin pw
        if request.form.get('ADMIN_PASSWORD'):
            admin = User.query.filter_by(username='admin').first()
            if admin:
                admin.set_password(request.form.get('ADMIN_PASSWORD'))
                db.session.commit()
        flash('Saved')
    return render_template_string('''
<h3>Setup</h3>
<form method="post">
ORACLE_USER: <input name="ORACLE_USER"><br>
ORACLE_PASSWORD: <input name="ORACLE_PASSWORD"><br>
ORACLE_DSN: <input name="ORACLE_DSN"><br>
EMAIL_GATEWAY_DOMAIN: <input name="EMAIL_GATEWAY_DOMAIN" value="{{domain}}"><br>
ADMIN_PASSWORD: <input name="ADMIN_PASSWORD" type="password"><br>
<button type="submit">Save</button>
</form>
''', domain=SimpleConfig.EMAIL_GATEWAY_DOMAIN)

@app.route('/api/test_smtp', methods=['POST'])
@login_required
def api_test_smtp():
    server = request.form.get('smtp_server') or request.form.get('smtp_server')
    port = int(request.form.get('smtp_port') or 587)
    use_tls = request.form.get('use_tls') in ('1','true','on')
    use_ssl = request.form.get('use_ssl') in ('1','true','on')
    username = request.form.get('username')
    password = request.form.get('password')
    try:
        if use_ssl:
            s = smtplib.SMTP_SSL(server, port, timeout=10)
        else:
            s = smtplib.SMTP(server, port, timeout=10)
        s.ehlo()
        if use_tls and not use_ssl:
            s.starttls(); s.ehlo()
        if username and password:
            s.login(username, password)
        s.quit()
        return jsonify(success=True, msg='OK')
    except Exception as exc:
        return jsonify(success=False, msg=str(exc))

@app.route('/api/test_oracle', methods=['POST'])
@login_required
def api_test_oracle():
    if oracledb is None:
        return jsonify(success=False, msg='oracledb not installed')
    try:
        conn = connect_oracle(request.form.get('ORACLE_USER'), request.form.get('ORACLE_PASSWORD'), request.form.get('ORACLE_DSN'))
        cur = conn.cursor(); cur.execute('SELECT 1 FROM DUAL'); v = cur.fetchone(); cur.close(); conn.close()
        return jsonify(success=True, msg=f'ok {v[0]}')
    except Exception as exc:
        return jsonify(success=False, msg=str(exc))

@app.route('/api/test_sms', methods=['POST'])
@login_required
def api_test_sms():
    phone = request.form.get('phone')
    message = request.form.get('message') or ''
    if not phone:
        return jsonify(success=False, msg='phone required')
    norm = normalize_phone(phone)
    if not norm:
        return jsonify(success=False, msg='phone normalize failed')
    smtp_cfg = SMTPConfig.query.first()
    if not smtp_cfg:
        return jsonify(success=False, msg='smtp not configured')
    addr = f"{norm}@{SimpleConfig.EMAIL_GATEWAY_DOMAIN}"
    try:
        send_via_smtp(smtp_cfg, addr, f"{SimpleConfig.HOSPITAL_NAME} test SMS", message)
        return jsonify(success=True, msg=f'sent to {addr}')
    except Exception as exc:
        return jsonify(success=False, msg=str(exc))

# --- CLI actions ---
def init_db_cmd():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        u = User(username='admin'); u.set_password('admin'); db.session.add(u)
    if not SMTPConfig.query.first():
        db.session.add(SMTPConfig())
    # migrate templates
    default = "Your Radiology appointment is confirmed on {date} at {time}\n\nPlease bring original prescription and imaging results done outside LAUMC\n\nInfo: 01-200800 ext.5070"
    # migrate and remove old SAP_* if exist
    for old, new in SimpleConfig.SITE_CODE_MAP.items():
        old_tpl = MessageTemplate.query.filter_by(site_code=old).first()
        new_tpl = MessageTemplate.query.filter_by(site_code=new).first()
        if old_tpl and not new_tpl:
            db.session.add(MessageTemplate(site_code=new, subject_template=SimpleConfig.HOSPITAL_NAME, body_template=old_tpl.body_template))
        if old_tpl:
            try: db.session.delete(old_tpl)
            except: pass
    for code in [c.strip() for c in SimpleConfig.SITE_CODE_MAP.values()]:
        if not MessageTemplate.query.filter_by(site_code=code).first():
            db.session.add(MessageTemplate(site_code=code, subject_template=SimpleConfig.HOSPITAL_NAME, body_template=default))
    db.session.commit()
    print('Initialized DB and defaults (admin/admin)')

def run_job_cmd():
    candidates = fetch_earliest_exams()
    create_or_update_logs(candidates)
    process_pending_logs()
    print('Job completed')

def run_server_cmd():
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-db', action='store_true')
    parser.add_argument('--run-server', action='store_true')
    parser.add_argument('--run-job', action='store_true')
    args = parser.parse_args()
    if args.init_db:
        init_db_cmd(); sys.exit(0)
    if args.run_job:
        run_job_cmd(); sys.exit(0)
    if args.run_server:
        run_server_cmd(); sys.exit(0)
    print('No action specified. Use --run-server, --run-job or --init-db')
