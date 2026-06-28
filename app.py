from datetime import datetime
from pathlib import Path
import os
import subprocess
import shutil
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import oracledb
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import smtplib
from email.message import EmailMessage

from oracle_client import normalize_phone

from config import Config
from models import db, User, SMTPConfig, MessageTemplate, EmailLog


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    def init_db():
        with app.app_context():
            db.create_all()
            if not User.query.filter_by(username="admin").first():
                user = User(username="admin")
                user.set_password("admin")
                db.session.add(user)
            if not SMTPConfig.query.first():
                smtp = SMTPConfig(smtp_server="", smtp_port=587, use_tls=True, use_ssl=False, username="", password="", from_address="")
                db.session.add(smtp)
            default_template = (
                "Your Radiology appointment is confirmed on {date} at {time}\n\n"
                "Please bring original prescription and imaging results done outside LAUMC\n\n"
                "Info: 01-200800 ext.5070"
            )
            # Ensure templates exist for the new UI site codes. If old SAP_* templates exist, migrate them.
            new_codes = [c.strip() for c in Config.DEFAULT_SITE_CODES.split(',') if c.strip()]
            # migrate any old templates (SAP_PROD/SAP_SJH) to new codes if present
            for old_code, new_code in getattr(Config, 'SITE_CODE_MAP', {}).items():
                old_tpl = MessageTemplate.query.filter_by(site_code=old_code).first()
                new_tpl = MessageTemplate.query.filter_by(site_code=new_code).first()
                if old_tpl:
                    if not new_tpl:
                        new_tpl = MessageTemplate(
                            site_code=new_code,
                            subject_template=old_tpl.subject_template,
                            body_template=old_tpl.body_template,
                        )
                        db.session.add(new_tpl)
                    # remove the old template
                    try:
                        db.session.delete(old_tpl)
                    except Exception:
                        pass

            for code in new_codes:
                if not MessageTemplate.query.filter_by(site_code=code).first():
                    template = MessageTemplate(
                        site_code=code,
                        subject_template=Config.HOSPITAL_NAME,
                        body_template=default_template,
                    )
                    db.session.add(template)
            db.session.commit()

    @app.before_first_request
    def setup_app():
        init_db()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Invalid username or password", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        status_filter = request.args.get("status", "all")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        query = EmailLog.query.order_by(EmailLog.scheduled_date.desc(), EmailLog.last_attempt_at.desc())

        if status_filter != "all":
            query = query.filter_by(status=status_filter)
        if start_date:
            query = query.filter(EmailLog.scheduled_date >= start_date)
        if end_date:
            query = query.filter(EmailLog.scheduled_date <= end_date)

        logs = query.limit(200).all()
        config = SMTPConfig.query.first()
        return render_template("dashboard.html", logs=logs, config=config, status_filter=status_filter, start_date=start_date, end_date=end_date)

    @app.route("/smtp", methods=["GET", "POST"])
    @login_required
    def smtp_config():
        smtp = SMTPConfig.query.first()
        if request.method == "POST":
            smtp.smtp_server = request.form.get("smtp_server", "")
            smtp.smtp_port = int(request.form.get("smtp_port", 587))
            smtp.use_tls = bool(request.form.get("use_tls"))
            smtp.use_ssl = bool(request.form.get("use_ssl"))
            smtp.username = request.form.get("username", "")
            smtp.password = request.form.get("password", "")
            smtp.from_address = request.form.get("from_address", smtp.from_address)
            smtp.last_updated = datetime.utcnow()
            db.session.commit()
            flash("SMTP settings saved.", "success")
            return redirect(url_for("smtp_config"))
        return render_template("smtp.html", smtp=smtp)

    @app.route("/templates")
    @login_required
    def templates():
        templates = MessageTemplate.query.order_by(MessageTemplate.site_code).all()
        return render_template("templates.html", templates=templates)

    @app.route("/setup", methods=["GET", "POST"])
    @login_required
    def setup():
        env_path = Path(__file__).resolve().parent / ".env"
        existing = {}
        if env_path.exists():
            try:
                with env_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.strip().split("=", 1)
                            existing[k] = v
            except Exception:
                existing = {}

        if request.method == "POST":
            # backup existing .env
            if env_path.exists():
                try:
                    backup_path = env_path.with_name(f".env.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
                    shutil.copy(str(env_path), str(backup_path))
                    flash(f"Existing .env backed up to {backup_path.name}", "info")
                except Exception as exc:
                    flash(f"Failed to backup .env: {exc}", "warning")

            keys = ["ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN", "ORACLE_TIMEZONE", "EMAIL_GATEWAY_DOMAIN", "HOSPITAL_NAME", "SECRET_KEY"]
            lines = []
            for k in keys:
                val = request.form.get(k, "")
                lines.append(f"{k}={val}")
            # persist DB URI if provided
            app_db = request.form.get("APP_DATABASE_URI", app.config.get("SQLALCHEMY_DATABASE_URI"))
            lines.append(f"APP_DATABASE_URI={app_db}")
            try:
                with env_path.open("w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                flash(".env saved.", "success")
            except Exception as exc:
                flash(f"Failed writing .env: {exc}", "danger")

            # admin password
            admin_pw = request.form.get("ADMIN_PASSWORD", "")

            # initialize DB and defaults
            try:
                with app.app_context():
                    db.create_all()
                    # create or update admin
                    admin = User.query.filter_by(username="admin").first()
                    if not admin:
                        admin = User(username="admin")
                        db.session.add(admin)
                    if admin_pw:
                        admin.set_password(admin_pw)

                    if not SMTPConfig.query.first():
                        smtp = SMTPConfig(smtp_server="", smtp_port=587, use_tls=True, use_ssl=False, username="", password="", from_address="")
                        db.session.add(smtp)
                    default_template = (
                        "Your Radiology appointment is confirmed on {date} at {time}\n\n"
                        "Please bring original prescription and imaging results done outside LAUMC\n\n"
                        "Info: 01-200800 ext.5070"
                    )
                    for code in ["SAP_PROD", "SAP_SJH"]:
                        if not MessageTemplate.query.filter_by(site_code=code).first():
                            template = MessageTemplate(
                                site_code=code,
                                subject_template=app.config.get("HOSPITAL_NAME", "LAUMC"),
                                body_template=default_template,
                            )
                            db.session.add(template)
                    db.session.commit()
                flash("Database initialized.", "success")
            except Exception as exc:
                flash(f"DB initialization failed: {exc}", "danger")

            # scheduled task
            create_task = request.form.get("create_task")
            if create_task:
                task_name = "LAUMC Reminder"
                run_path = str(Path(__file__).resolve().parent / "run_reminder.bat")
                try:
                    subprocess.run(["schtasks", "/Create", "/SC", "MINUTE", "/MO", "30", "/TN", task_name, "/TR", run_path, "/F"], check=True)
                    flash("Scheduled task created.", "success")
                except Exception as exc:
                    flash(f"Failed creating scheduled task: {exc}", "warning")

            return redirect(url_for("setup"))

        return render_template("setup.html", existing=existing)

    @app.route('/api/test_smtp', methods=['POST'])
    @login_required
    def api_test_smtp():
        data = request.form or request.get_json() or {}
        server = data.get('smtp_server') or request.form.get('smtp_server')
        port = int(data.get('smtp_port') or request.form.get('smtp_port') or 587)
        use_tls = data.get('use_tls') in ('true', '1', 'on') or request.form.get('use_tls')
        use_ssl = data.get('use_ssl') in ('true', '1', 'on') or request.form.get('use_ssl')
        username = data.get('username') or request.form.get('username')
        password = data.get('password') or request.form.get('password')

        if not server:
            return jsonify(success=False, msg='SMTP server is required')

        try:
            if str(use_ssl) in ('True', 'true', '1', 'on'):
                smtp = smtplib.SMTP_SSL(server, port, timeout=10)
            else:
                smtp = smtplib.SMTP(server, port, timeout=10)
            smtp.ehlo()
            if str(use_tls) in ('True', 'true', '1', 'on') and not str(use_ssl) in ('True', 'true', '1', 'on'):
                smtp.starttls()
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.quit()
            return jsonify(success=True, msg='SMTP connection successful')
        except Exception as exc:
            return jsonify(success=False, msg=str(exc))

    @app.route('/api/test_oracle', methods=['POST'])
    @login_required
    def api_test_oracle():
        data = request.form or request.get_json() or {}
        user = data.get('ORACLE_USER') or request.form.get('ORACLE_USER')
        password = data.get('ORACLE_PASSWORD') or request.form.get('ORACLE_PASSWORD')
        dsn = data.get('ORACLE_DSN') or request.form.get('ORACLE_DSN')

        if not (user and password and dsn):
            return jsonify(success=False, msg='ORACLE_USER, ORACLE_PASSWORD and ORACLE_DSN are required')

        try:
            conn = oracledb.connect(user=user, password=password, dsn=dsn, encoding='UTF-8', nencoding='UTF-8')
            cur = conn.cursor()
            cur.execute('SELECT 1 FROM DUAL')
            val = cur.fetchone()
            cur.close()
            conn.close()
            return jsonify(success=True, msg=f'Oracle connection OK, test query returned: {val[0]}')
        except Exception as exc:
            return jsonify(success=False, msg=str(exc))

    @app.route('/api/test_sms', methods=['POST'])
    @login_required
    def api_test_sms():
        data = request.form or request.get_json() or {}
        raw_phone = data.get('phone') or request.form.get('phone')
        message = data.get('message') or request.form.get('message') or 'Test message from LAUMC Reminder'

        if not raw_phone:
            return jsonify(success=False, msg='Phone number is required')

        normalized = normalize_phone(raw_phone)
        if not normalized:
            return jsonify(success=False, msg='Phone number could not be normalized')

        smtp_cfg = SMTPConfig.query.first()
        if not smtp_cfg:
            return jsonify(success=False, msg='SMTP configuration not set')

        to_addr = f"{normalized}@{app.config.get('EMAIL_GATEWAY_DOMAIN')}"
        msg = EmailMessage()
        msg['From'] = smtp_cfg.from_address or app.config.get('HOSPITAL_NAME')
        msg['To'] = to_addr
        msg['Subject'] = f"{app.config.get('HOSPITAL_NAME')} - Test SMS"
        msg.set_content(message)

        try:
            if smtp_cfg.use_ssl:
                server = smtplib.SMTP_SSL(smtp_cfg.smtp_server, smtp_cfg.smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(smtp_cfg.smtp_server, smtp_cfg.smtp_port, timeout=10)
            server.ehlo()
            if smtp_cfg.use_tls and not smtp_cfg.use_ssl:
                server.starttls()
                server.ehlo()
            if smtp_cfg.username and smtp_cfg.password:
                server.login(smtp_cfg.username, smtp_cfg.password)
            server.send_message(msg)
            server.quit()
            return jsonify(success=True, msg=f'Test SMS sent to {to_addr}')
        except Exception as exc:
            return jsonify(success=False, msg=str(exc))

    @app.route("/templates/edit/<string:site_code>", methods=["GET", "POST"])
    @login_required
    def edit_template(site_code):
        template = MessageTemplate.query.filter_by(site_code=site_code).first_or_404()
        if request.method == "POST":
            template.subject_template = request.form.get("subject_template", template.subject_template)
            template.body_template = request.form.get("body_template", template.body_template)
            db.session.commit()
            flash(f"Template for {site_code} updated.", "success")
            return redirect(url_for("templates"))
        return render_template("edit_template.html", template=template)

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5000, debug=True)
