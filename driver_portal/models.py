# =============================================================================
# VRMS Driver Portal
# File: models.py
# Purpose: Database models for the portal.
#
#   1. REUSE the existing desktop app's models (Driver, Vehicle, Hire, Payment)
#      by importing them, so the two apps can never drift out of sync.
#   2. ADD one new table — DriverAccount — that holds the driver's web-portal
#      login (email + password). The existing Driver table is left UNTOUCHED.
#
# WHY A SEPARATE TABLE?
#   The desktop `drivers` table has no email/password columns and is managed by
#   staff. Rather than alter it, the portal stores login credentials in its own
#   `driver_accounts` table and links each account to a driver via driver_id.
#   This keeps the desktop schema clean and lets drivers self-register.
# =============================================================================

import os
import sys

from config import VRMS_APP_PATH

# ---------------------------------------------------------------------------
# Make the desktop app importable, then borrow its models + declarative Base.
# Putting DriverAccount on the SAME Base means a single create_all() call knows
# about every table and foreign keys to drivers.id resolve correctly.
# ---------------------------------------------------------------------------
if VRMS_APP_PATH not in sys.path:
    sys.path.insert(0, VRMS_APP_PATH)

try:
    from database.models import Base, Driver, Vehicle, Hire, Payment  # noqa: F401
except ModuleNotFoundError as exc:  # pragma: no cover - configuration error
    raise RuntimeError(
        "Could not import the VRMS desktop models. Set the VRMS_APP_PATH "
        f"environment variable to the folder containing the desktop app "
        f"(currently '{VRMS_APP_PATH}'). Original error: {exc}"
    )

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone


def _utc_now():
    """Naive UTC timestamp, matching the convention used by the desktop app."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DriverAccount(Base):
    """
    Web-portal login for a driver.

    One row per driver who has registered for the portal. Linked to the
    existing `drivers` table via driver_id (one account per driver).
    Passwords are stored only as werkzeug hashes — never plain text.
    """
    __tablename__ = "driver_accounts"

    id            = Column(Integer, primary_key=True, autoincrement=True)

    # Link to the existing driver record. unique=True => one account per driver.
    driver_id     = Column(Integer, ForeignKey("drivers.id"),
                           nullable=False, unique=True)

    email         = Column(String(150), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)

    is_active     = Column(Boolean, nullable=False, default=True)  # soft disable
    last_login    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, nullable=False, default=_utc_now)
    updated_at    = Column(DateTime, nullable=False,
                           default=_utc_now, onupdate=_utc_now)

    # Convenient access to the linked Driver record (read-only in practice).
    driver        = relationship("Driver", backref="portal_account")

    def __repr__(self):
        return f"<DriverAccount id={self.id} driver_id={self.driver_id} email='{self.email}'>"
