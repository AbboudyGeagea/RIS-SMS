from datetime import datetime
import smtplib
from email.message import EmailMessage

from app import create_app
from config import Config
from models import db, SMTPConfig, MessageTemplate, EmailLog
from oracle_client import fetch_earliest_scheduled_exams

app = create_app()


def get_smtp_settings() -> SMTPConfig:
    with app.app_context():
        smtp = SMTPConfig.query.first()
        if smtp is None:
            raise RuntimeError("SMTP configuration not found. Please configure it in the admin UI.")
        return smtp


def get_template_for_site(site_code: str) -> MessageTemplate:
    with app.app_context():
        template = MessageTemplate.query.filter_by(site_code=site_code).first()
        if template:
            return template
        return MessageTemplate(
            site_code=site_code,
            subject_template=Config.HOSPITAL_NAME,
            body_template=(
                "Your Radiology appointment is confirmed on {date} at {time}\n\n"
                "Please bring original prescription and imaging results done outside LAUMC\n\n"
                "Info: 01-200800 ext.5070"
            ),
        )


def build_message(email_address: str, site_code: str, scheduled_date: datetime) -> tuple[str, str]:
    template = get_template_for_site(site_code)
    date_text = scheduled_date.strftime("%d-%m-%Y")
    time_text = scheduled_date.strftime("%H:%M")
    subject = template.subject_template
    body = template.body_template.format(date=date_text, time=time_text)
    return subject, body


def send_email(email_address: str, subject: str, body: str) -> None:
    smtp = get_smtp_settings()
    msg = EmailMessage()
    msg["From"] = smtp.from_address
    msg["To"] = email_address
    msg["Subject"] = subject
    msg.set_content(body)

    if smtp.use_ssl:
        server = smtplib.SMTP_SSL(smtp.smtp_server, smtp.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp.smtp_server, smtp.smtp_port, timeout=30)
    try:
        server.ehlo()
        if smtp.use_tls and not smtp.use_ssl:
            server.starttls()
            server.ehlo()
        if smtp.username and smtp.password:
            server.login(smtp.username, smtp.password)
        server.send_message(msg)
    finally:
        server.quit()


def create_or_update_logs(candidates: list[dict]) -> None:
    with app.app_context():
        for candidate in candidates:
            patient_id = candidate["patient_id"]
            phone_number = candidate["phone_number"]
            scheduled_date = candidate["scheduled_date"]
            site_code = candidate.get("site_code", "SAP_PROD")
            email_address = f"{phone_number}@{Config.EMAIL_GATEWAY_DOMAIN}"

            log = EmailLog.query.filter_by(
                patient_id=patient_id,
                scheduled_date=scheduled_date,
                site_code=site_code,
            ).first()

            if log is None:
                log = EmailLog(
                    patient_id=patient_id,
                    phone_number=phone_number,
                    email_address=email_address,
                    scheduled_date=scheduled_date,
                    site_code=site_code,
                    status="pending",
                    attempt_count=0,
                    message_body="",
                )
                db.session.add(log)
            else:
                if log.status == "sent":
                    continue
                log.phone_number = phone_number
                log.email_address = email_address
                log.site_code = site_code
            db.session.commit()


def process_pending_logs() -> None:
    now = datetime.utcnow()
    with app.app_context():
        logs = EmailLog.query.filter(
            EmailLog.status != "sent",
            EmailLog.scheduled_date >= now,
        ).order_by(EmailLog.scheduled_date).all()

        for log in logs:
            subject, body = build_message(log.email_address, log.site_code, log.scheduled_date)
            try:
                send_email(log.email_address, subject, body)
                log.status = "sent"
                log.error_message = None
            except Exception as exc:
                log.status = "failed"
                log.error_message = str(exc)
            finally:
                log.attempt_count += 1
                log.last_attempt_at = datetime.utcnow()
                log.message_body = body
                db.session.commit()


def run_once() -> None:
    candidates = fetch_earliest_scheduled_exams()
    create_or_update_logs(candidates)
    process_pending_logs()


if __name__ == "__main__":
    run_once()
