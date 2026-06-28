from app import create_app
from models import db, User, SMTPConfig, MessageTemplate
from config import Config

app = create_app()

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
    # Ensure templates for new UI site codes, migrate old SAP_* templates if present
    new_codes = [c.strip() for c in Config.DEFAULT_SITE_CODES.split(',') if c.strip()]
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
    print("Database initialized and default admin user created (admin/admin).")
