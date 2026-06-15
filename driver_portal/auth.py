# =============================================================================
# VRMS Driver Portal
# File: auth.py
# Purpose: All authentication logic for the portal — driver self-registration
#          and login. Keeps DB/credential rules out of the Flask route handlers.
#
# IDENTITY VERIFICATION (important):
#   Drivers are created by staff in the desktop app; the portal has no way to
#   "invite" them. So a driver self-registers by proving who they are against
#   the data already on file. We match on full name, then verify with whatever
#   identifying field staff have recorded (phone number and/or licence number).
#   In the live data many drivers have neither on file yet — for those we fall
#   back to a unique-name match. Tighten this once phone/NI data is filled
#   in (see _find_driver_for_registration).
# =============================================================================

import re

from werkzeug.security import generate_password_hash, check_password_hash

# Password hashing method. We pin pbkdf2-sha256 explicitly rather than relying
# on werkzeug's default (which is scrypt in werkzeug 3.x). scrypt fails to verify
# on some Windows Python builds — the symptom is "registration works but login
# fails" — so pbkdf2 keeps hashing portable across every machine.
HASH_METHOD = "pbkdf2:sha256"

from db import SessionLocal
from models import Driver, DriverAccount

# Minimum password length for new portal accounts.
MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def normalize_email(email: str) -> str:
    """Lower-case and trim an email so look-ups are consistent."""
    return (email or "").strip().lower()


def _digits(value: str) -> str:
    """Reduce a phone number to digits only so formatting differences (spaces,
    +44, leading 0) don't cause false mismatches."""
    return re.sub(r"\D", "", value or "")


def _clean(value: str) -> str:
    """Remove all whitespace + upper-case (e.g. an NI number) for a tolerant
    comparison, so 'ab 12 34 56 c' matches 'AB123456C'."""
    return re.sub(r"\s", "", value or "").upper()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def _find_driver_for_registration(session, phone, ni):
    """
    Find the single active Driver this registration belongs to, using ONLY the
    identifiers a driver actually knows: their PHONE NUMBER or their NATIONAL
    INSURANCE number. No name typing required — the name is read from the record.

    NI is unique, so it's the preferred match. Phone may (rarely) be shared by a
    family, in which case we ask for the NI number to disambiguate.

    Returns (driver, error_message): a driver with error None on success, or
    None with a user-facing error.
    """
    phone_d, ni_c = _digits(phone), _clean(ni)
    if not phone_d and not ni_c:
        return None, ("Please enter your phone number or National Insurance "
                      "number so we can find your record.")

    # Load active drivers once; comparisons are done in Python because the stored
    # values may contain spaces/formatting that SQL can't easily normalise.
    drivers = session.query(Driver).filter(Driver.is_active.is_(True)).all()

    # 1) Try the NI number first (it's unique on the record).
    if ni_c:
        ni_matches = [d for d in drivers if _clean(d.national_insurance) == ni_c]
        if len(ni_matches) == 1:
            return ni_matches[0], None
        if len(ni_matches) > 1:  # shouldn't happen — NI is unique
            return None, ("More than one record matches that National Insurance "
                          "number. Please contact the office.")
        if not phone_d:
            return None, ("We couldn't find a driver with that National Insurance "
                          "number. Please check it, or contact the office.")

    # 2) Fall back to the phone number.
    if phone_d:
        ph_matches = [d for d in drivers if _digits(d.phone_number) == phone_d]
        if len(ph_matches) == 1:
            return ph_matches[0], None
        if len(ph_matches) == 0:
            return None, ("We couldn't find a driver with that phone number. "
                          "Please check it, or contact the office.")
        # Several drivers share this phone number — ask for the NI to be sure.
        return None, ("More than one driver is registered with that phone number. "
                      "Please also enter your National Insurance number.")

    return None, "We couldn't verify your details. Please contact the office."


def register_driver(email, password, phone, ni):
    """
    Register a new portal account for an existing driver.

    Returns (account_or_None, message). On success the message is a friendly
    confirmation; on failure it explains what to fix.
    """
    email = normalize_email(email)

    # --- Basic field validation -------------------------------------------
    if not email or "@" not in email:
        return None, "Please enter a valid email address."
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return None, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    session = SessionLocal()
    try:
        # Email must be unique across the portal.
        if session.query(DriverAccount).filter(
            DriverAccount.email == email
        ).first():
            return None, "That email is already registered. Please log in instead."

        # Identify which driver record this is (by phone or NI — no name needed).
        driver, error = _find_driver_for_registration(session, phone, ni)
        if error:
            return None, error

        # One portal account per driver.
        if session.query(DriverAccount).filter(
            DriverAccount.driver_id == driver.id
        ).first():
            return None, ("An account already exists for this driver. "
                          "Please log in, or contact the office to reset it.")

        account = DriverAccount(
            driver_id=driver.id,
            email=email,
            # werkzeug salts + hashes. Pinned to pbkdf2 (see HASH_METHOD note).
            password_hash=generate_password_hash(password, method=HASH_METHOD),
            is_active=True,
        )
        session.add(account)
        session.commit()
        return account, (f"Account created for {driver.full_name}. "
                         "You can now log in.")

    except Exception as exc:  # pragma: no cover - defensive
        session.rollback()
        return None, f"Could not create the account: {exc}"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def authenticate(identifier, password):
    """
    Log a driver in with their PHONE NUMBER or NATIONAL INSURANCE number plus
    password — the same identifiers they registered with (no email needed).

    We auto-detect which one was typed: NI numbers contain letters, phone
    numbers don't. We then find the matching active driver(s), look up their
    portal account, and verify the password.

    Returns (identity_dict_or_None, message).
    """
    identifier = (identifier or "").strip()
    if not identifier or not password:
        return None, "Please enter your phone or NI number and your password."

    session = SessionLocal()
    try:
        # NI numbers contain letters (e.g. AB123456C); phone numbers don't.
        looks_like_ni = bool(re.search(r"[A-Za-z]", identifier))

        drivers = session.query(Driver).filter(Driver.is_active.is_(True)).all()
        if looks_like_ni:
            ni_c = _clean(identifier)
            candidates = [d for d in drivers
                          if ni_c and _clean(d.national_insurance) == ni_c]
        else:
            phone_d = _digits(identifier)
            candidates = [d for d in drivers
                          if phone_d and _digits(d.phone_number) == phone_d]

        # Find the portal accounts for those drivers.
        accounts = []
        if candidates:
            accounts = session.query(DriverAccount).filter(
                DriverAccount.driver_id.in_([d.id for d in candidates]),
                DriverAccount.is_active.is_(True),
            ).all()

        # Verify the password. If a phone is shared by a family, more than one
        # account may exist — the password singles out the right one.
        matched = [a for a in accounts
                   if check_password_hash(a.password_hash, password)]
        if len(matched) != 1:
            return None, "Incorrect phone/NI number or password."

        account = matched[0]
        from datetime import datetime
        account.last_login = datetime.utcnow()
        session.commit()

        return {
            "account_id": account.id,
            "driver_id": account.driver_id,
        }, "Logged in."
    except Exception as exc:  # pragma: no cover - defensive
        session.rollback()
        return None, f"Login failed: {exc}"
    finally:
        session.close()
