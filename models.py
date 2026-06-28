from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

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
    status = db.Column(db.String(32), nullable=False, default="pending")
    attempt_count = db.Column(db.Integer, default=0)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    message_body = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('patient_id', 'scheduled_date', 'site_code', name='uq_patient_schedule_site'),
    )
