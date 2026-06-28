import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import oracledb

from config import Config

PHONE_PATTERN = re.compile(r"\d+")


def normalize_phone(raw_phone: str) -> str | None:
    if not raw_phone:
        return None

    digits = re.sub(r"\D", "", raw_phone)
    if not digits:
        return None

    if digits.startswith("00961") and len(digits) >= 12:
        digits = digits[2:]

    if digits.startswith("+961"):
        digits = digits[1:]

    if digits.startswith("961") and len(digits) == 11:
        return digits

    if digits.startswith("0") and len(digits) == 8:
        digits = digits[1:]
        return f"961{digits}"

    if len(digits) == 8:
        return f"961{digits}"

    if len(digits) == 9 and digits.startswith("0"):
        digits = digits[1:]
        return f"961{digits}"

    return None


def connect_oracle():
    if not Config.ORACLE_USER or not Config.ORACLE_PASSWORD or not Config.ORACLE_DSN:
        raise RuntimeError("Oracle connection settings are not configured. Set ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN.")

    connection = oracledb.connect(
        user=Config.ORACLE_USER,
        password=Config.ORACLE_PASSWORD,
        dsn=Config.ORACLE_DSN,
        encoding="UTF-8",
        nencoding="UTF-8",
    )
    return connection


def get_reminder_window():
    timezone = ZoneInfo(Config.ORACLE_TIMEZONE)
    now_local = datetime.now(timezone)
    start_local = now_local + timedelta(hours=24)
    end_local = now_local + timedelta(hours=25, minutes=30)
    return start_local.replace(tzinfo=None), end_local.replace(tzinfo=None)


def fetch_earliest_scheduled_exams():
    start_dt, end_dt = get_reminder_window()
    sql = """
SELECT * FROM (
    SELECT
        pid.patient_id,
        pe.PATIENT_PHONE_NUMBER AS raw_phone,
        sw.scheduled_date,
        ord.ISSUER_OF_PLACER_ORDER_NUMBER AS site_code,
        ROW_NUMBER() OVER (
            PARTITION BY pid.patient_id
            ORDER BY sw.scheduled_date
        ) AS rn
    FROM site_worklist sw
    JOIN PATIENT_ID_LIST pid
      ON sw.patient_person_key = pid.patient_person_key
    JOIN site_patient pa
      ON sw.patient_person_key = pa.patient_person_key
    JOIN site_person pe
      ON pa.patient_person_key = pe.person_key
    JOIN ORDERS ord
      ON sw.order_key = ord.order_key
    WHERE sw.status_key = 40
      AND sw.scheduled_date BETWEEN :start_dt AND :end_dt
)
WHERE rn = 1
"""
    with connect_oracle() as conn:
        cur = conn.cursor()
        cur.execute(sql, [start_dt, end_dt])
        columns = [col[0].lower() for col in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    results = []
    for row in rows:
        normalized = normalize_phone(row.get("raw_phone"))
        if not normalized:
            continue
        raw_site = row.get("site_code") or "SAP_PROD"
        mapped_site = Config.SITE_CODE_MAP.get(raw_site, raw_site)
        results.append({
            "patient_id": row["patient_id"],
            "phone_number": normalized,
            "scheduled_date": row["scheduled_date"],
            "site_code": mapped_site,
        })

    return results
