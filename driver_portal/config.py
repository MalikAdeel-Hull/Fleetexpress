# =============================================================================
# VRMS Driver Portal
# File: config.py
# Purpose: Central configuration for the Flask driver portal.
#
#   - Resolves which database the portal talks to (SQLite by default,
#     PostgreSQL when DATABASE_URL is set).
#   - Holds Flask security settings (secret key, session lifetime, cookie flags).
#
# WHY CONFIGURABLE?
#   The existing VRMS desktop app uses a single-file SQLite database (vrms.db).
#   That is fine for one desktop user, but a web portal serving many drivers is
#   safer on a real database server. So this portal defaults to the SQLite file
#   (works immediately against live data) but switches to PostgreSQL the moment
#   you set the DATABASE_URL environment variable — no code change required.
# =============================================================================

import os
import secrets
from datetime import timedelta


# -----------------------------------------------------------------------------
# Locate the existing VRMS desktop application.
# The portal REUSES the desktop app's SQLAlchemy models (Driver, Vehicle, Hire,
# Payment) by importing them — see models.py. To do that, Python needs to know
# where the desktop app lives so it can import its `database` package.
#
# Override with the VRMS_APP_PATH environment variable if your app is elsewhere.
# -----------------------------------------------------------------------------
VRMS_APP_PATH = os.environ.get("VRMS_APP_PATH", r"E:\vrms")


def _default_sqlite_url() -> str:
    """Build a sqlite:// URL pointing at the desktop app's vrms.db file."""
    db_file = os.path.join(VRMS_APP_PATH, "vrms.db")
    # SQLAlchemy needs forward slashes in the URL even on Windows.
    return "sqlite:///" + db_file.replace("\\", "/")


class Config:
    """Base configuration shared by all environments."""

    # ------------------------------------------------------------------
    # DATABASE
    # If DATABASE_URL is set (e.g. postgresql+psycopg2://user:pass@host/db)
    # it is used as-is. Otherwise we fall back to the desktop app's SQLite file.
    # ------------------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or _default_sqlite_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False  # silence noisy Flask-SQLAlchemy signal

    # connect_args is only needed for SQLite (allow access across Flask threads).
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"connect_args": {"check_same_thread": False}}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite")
        else {"pool_pre_ping": True}  # postgres: drop dead connections automatically
    )

    # ------------------------------------------------------------------
    # SECURITY
    # SECRET_KEY signs the session cookie and CSRF token. In production ALWAYS
    # set a fixed FLASK_SECRET_KEY env var, otherwise every restart invalidates
    # all sessions (the random fallback below is only for first-run convenience).
    # ------------------------------------------------------------------
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    # Session timeout — drivers are logged out after this idle period.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    )

    # Cookie hardening.
    SESSION_COOKIE_HTTPONLY = True   # JS cannot read the cookie (blocks XSS theft)
    SESSION_COOKIE_SAMESITE = "Lax"  # mitigates CSRF on top-level navigations
    # Set SESSION_COOKIE_SECURE=1 in the environment once you serve over HTTPS.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    # How many of the driver's most recent payments to show on the dashboard.
    PAYMENT_HISTORY_LIMIT = 10

    # Office contact details shown on the "Contact admin" button / FAQ.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "office@ecoexpress.example")
    ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "0000 000 0000")

    # Public marketing site the portal links back to ("← Back to website").
    # The static index.html lives there and links INTO this portal.
    SITE_URL = os.environ.get("SITE_URL", "/")
