# LAUMC Reminder Service

## Overview
This project reads scheduled exams from Oracle, selects the earliest exam per patient for the 24-25.5 hour reminder window, normalizes phone numbers, and sends reminder emails to `phone_number@broadnetsms.com`.

The web UI provides:
- login
- SMTP configuration
- message templates per site code (`SAP_PROD`, `SAP_SJH`)
- monitoring dashboard

## Setup
1. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Create a `.env` file in the project root:
   ```text
   ORACLE_USER=your_oracle_user
   ORACLE_PASSWORD=your_oracle_password
   ORACLE_DSN=your_oracle_dsn
   ORACLE_TIMEZONE=Asia/Beirut
   EMAIL_GATEWAY_DOMAIN=broadnetsms.com
   HOSPITAL_NAME=LAUMC
   SECRET_KEY=replace-with-a-secret-key
   ```

3. Run the web UI:
   ```powershell
   python app.py
   ```

4. Open the app at `http://127.0.0.1:5000`
   - login: `admin` / `admin`
   - configure SMTP and template text

## Running the reminder job
Run manually or schedule it every 30 minutes:
```powershell
python reminder_service.py
```

## Recommended schedule
- Windows Task Scheduler: every 30 minutes
- Linux cron: `*/30 * * * * /usr/bin/python /path/to/reminder_service.py`

## Notes
- Phone numbers are normalized to `961XXXXXXXX`.
- Only rows with valid 8-digit phone numbers are retained.
- The app retries failed sends automatically until the exam time passes.
