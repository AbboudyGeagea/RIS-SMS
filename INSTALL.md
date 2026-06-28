# Installation Guide

## Clone the repository

```powershell
cd "D:\Intermedic\Statistics app"
git clone https://github.com/AbboudyGeagea/RIS-SMS.git
cd RIS-SMS
```

## Create and activate a Python virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configure environment variables

Copy the example file and fill in your Oracle and SMTP credentials:

```powershell
copy .env.example .env
```

Edit `.env` and set:

- `ORACLE_USER`
- `ORACLE_PASSWORD`
- `ORACLE_DSN`
- `ORACLE_TIMEZONE` (e.g. `Asia/Beirut`)
- `EMAIL_GATEWAY_DOMAIN` (default: `broadnetsms.com`)
- `HOSPITAL_NAME` (e.g. `LAUMC`)
- `SECRET_KEY`
- `APP_DATABASE_URI` (optional; default uses SQLite)

## Initialize the database

```powershell
python init_db.py
```

This creates the local SQLite metadata database and a default admin user.

## Run the web UI

```powershell
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

Default login:

- username: `admin`
- password: `admin`

Use the UI to configure SMTP settings, verify Oracle connectivity, and edit templates.

## Run the reminder job once

```powershell
python reminder_service.py
```

## Optional single-file app

You can also use the single-file launcher:

```powershell
python laumc_reminder_single.py --init-db
python laumc_reminder_single.py --run-server
python laumc_reminder_single.py --run-job
```

## Schedule regular execution

Recommended frequency: every 30 minutes.

### Windows Task Scheduler

Create a task that runs:

```powershell
D:\Intermedic\Statistics app\RIS-SMS\.venv\Scripts\python.exe D:\Intermedic\Statistics app\RIS-SMS\reminder_service.py
```

### Linux cron

```cron
*/30 * * * * /path/to/.venv/bin/python /path/to/RIS-SMS/reminder_service.py
```

## Notes

- The app sends reminders to `phone_number@EMAIL_GATEWAY_DOMAIN`.
- Phone numbers are normalized to `961XXXXXXXX`.
- The scheduler only sends one reminder per patient for the earliest exam in the target window.
