@echo off
REM One-step installer for LAUMC Reminder (Windows)
cd /d %~dp0

REM Create virtual environment
if not exist ".venv" (
  python -m venv .venv
)

call ".venv\Scripts\activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

REM Create .env if missing
if not exist ".env" (
  copy ".env.example" ".env"
  echo Created .env from .env.example. Please edit .env to add Oracle and SMTP credentials if needed.
)

REM Initialize SQLite DB and defaults
python init_db.py

REM Register scheduled task every 30 minutes (requires admin privileges)
set TASK_NAME="LAUMC Reminder"
set TASK_PATH="%~dp0run_reminder.bat"

schtasks /Create /SC MINUTE /MO 30 /TN %TASK_NAME% /TR %TASK_PATH% /F >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  echo Scheduled task created: %TASK_NAME%
) else (
  echo Failed to create scheduled task. You can create it manually via Task Scheduler to run run_reminder.bat every 30 minutes.
)

echo Installation complete. To run the server now:
echo    .venv\Scripts\activate
echo    python app.py

echo To run the reminder immediately:
echo    .venv\Scripts\activate
echo    python reminder_service.py
pause
