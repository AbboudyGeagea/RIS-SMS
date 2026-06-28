import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "monitor.db"

load_dotenv(BASE_DIR / ".env")

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "replace-me-with-a-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get("APP_DATABASE_URI", f"sqlite:///{DEFAULT_SQLITE_PATH}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ORACLE_USER = os.environ.get("ORACLE_USER", "")
    ORACLE_PASSWORD = os.environ.get("ORACLE_PASSWORD", "")
    ORACLE_DSN = os.environ.get("ORACLE_DSN", "")
    ORACLE_TIMEZONE = os.environ.get("ORACLE_TIMEZONE", "Asia/Beirut")
    EMAIL_GATEWAY_DOMAIN = os.environ.get("EMAIL_GATEWAY_DOMAIN", "broadnetsms.com")
    HOSPITAL_NAME = os.environ.get("HOSPITAL_NAME", "LAUMC")
    DEFAULT_SITE_CODES = os.environ.get("DEFAULT_SITE_CODES", "LAUMC-RH,LAUMC-SJH")
    # Mapping from values found in ORDERS.ISSUER_OF_PLACER_ORDER_NUMBER to UI/template site codes
    SITE_CODE_MAP = {
        "SAP_PROD": "LAUMC-RH",
        "SAP_SJH": "LAUMC-SJH",
    }
