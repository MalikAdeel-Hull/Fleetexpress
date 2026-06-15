# =============================================================================
# VRMS Driver Portal
# File: app.py
# Purpose: The Flask application — routes, session handling, CSRF protection,
#          and the logic that turns raw Hire/Payment rows into the figures the
#          driver dashboard shows (rent, paid this week, amount owed, status).
#
# Run:   python app.py        (listens on http://0.0.0.0:5000)
# =============================================================================

import csv
import hmac
import io
import os
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, Response, send_from_directory,
)

from config import Config
from db import SessionLocal, init_db
from models import Driver, Vehicle, Hire, Payment
from auth import register_driver, authenticate


# ---------------------------------------------------------------------------
# App + configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config.from_object(Config)

# Folder holding the public marketing site (index.html). It lives one level up
# from this package (E:\Fleetexpres\Fleetexpress\index.html). This single Flask
# app serves BOTH the website and the driver portal on one address — so there's
# no separate domain/subdomain to set up. Override with SITE_DIR if you move it.
SITE_DIR = os.environ.get("SITE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

# Create the driver_accounts table if it doesn't exist yet (one-time, additive).
init_db()


# Make sure every scoped session is returned to the pool at the end of a request
# — prevents connection leaks and stale data.
@app.teardown_appcontext
def _remove_session(exception=None):
    SessionLocal.remove()


# ---------------------------------------------------------------------------
# CSRF PROTECTION
# A per-session random token is embedded as a hidden field in every form and
# checked on every POST. Uses a constant-time compare to avoid timing attacks.
# ---------------------------------------------------------------------------
def generate_csrf_token() -> str:
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


# Expose the token to all templates as {{ csrf_token() }}.
app.jinja_env.globals["csrf_token"] = generate_csrf_token


@app.before_request
def csrf_protect():
    """Reject any POST whose CSRF token is missing or doesn't match the session."""
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="Invalid or missing CSRF token. Please reload and try again.")


# ---------------------------------------------------------------------------
# AUTH GUARD
# ---------------------------------------------------------------------------
def login_required(view):
    """Redirect anonymous users to the login page, remembering where they wanted
    to go so we can send them back after a successful login."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("driver_id"):
            flash("Please log in to view your payment status.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# Inject common values into every template (navbar name + office contact info).
@app.context_processor
def inject_driver():
    return {
        "driver_name": session.get("driver_name"),
        "admin_email": app.config.get("ADMIN_EMAIL"),
        "admin_phone": app.config.get("ADMIN_PHONE"),
        "site_url": app.config.get("SITE_URL"),
    }


# ---------------------------------------------------------------------------
# DASHBOARD DATA
# Turns the driver's hires + payments into the numbers shown on screen.
# All money is handled as Decimal until the final formatting step.
# ---------------------------------------------------------------------------
def _current_week_window(hire_start: date, today: date):
    """Return (week_start, week_end) for the rental week that `today` falls in,
    aligned to the hire's start day — matching how the business bills weekly."""
    if today < hire_start:                 # hire hasn't started yet
        week_start = hire_start
    else:
        weeks_elapsed = (today - hire_start).days // 7
        week_start = hire_start + timedelta(days=weeks_elapsed * 7)
    return week_start, week_start + timedelta(days=6)


def build_dashboard(driver_id: int):
    """
    Assemble everything the dashboard template needs for one driver.
    Returns a dict, or None if the driver record no longer exists.
    """
    db = SessionLocal()
    try:
        driver = db.query(Driver).filter(
            Driver.id == driver_id, Driver.is_active.is_(True)
        ).first()
        if not driver:
            return None

        today = date.today()

        # --- Pick the CURRENT hire ---------------------------------------
        # A few drivers have more than one active hire in the data, so we take
        # the most recently started one as "current".
        current = (
            db.query(Hire)
            .filter(Hire.driver_id == driver_id, Hire.is_active.is_(True),
                    Hire.contract_status == "active")
            .order_by(Hire.hire_start_date.desc(), Hire.id.desc())
            .first()
        )

        # --- Payment history across ALL the driver's hires ---------------
        hire_ids = [h.id for h in db.query(Hire.id).filter(
            Hire.driver_id == driver_id, Hire.is_active.is_(True)
        ).all()]

        history = []
        last_payment_date = None
        if hire_ids:
            payments = (
                db.query(Payment)
                .filter(Payment.hire_id.in_(hire_ids), Payment.is_active.is_(True))
                .order_by(Payment.payment_date.desc(), Payment.id.desc())
                .all()
            )
            if payments:
                last_payment_date = payments[0].payment_date
            for p in payments[: app.config["PAYMENT_HISTORY_LIMIT"]]:
                history.append({
                    "date": p.payment_date,
                    "amount": Decimal(p.amount_paid or 0),
                    "method": (p.payment_method or "").replace("_", " ").title(),
                    "notes": p.notes or "",
                })

        data = {
            "driver": driver,
            "has_rental": current is not None,
            "history": history,
            "last_payment_date": last_payment_date,
        }

        if not current:
            # Driver is between hires — show history but no live rental figures.
            return data

        # --- Current rental figures --------------------------------------
        vehicle = db.query(Vehicle).filter(Vehicle.id == current.vehicle_id).first()
        weekly_rate = Decimal(current.weekly_rate or 0)

        week_start, week_end = _current_week_window(current.hire_start_date, today)

        # Sum payments dated within the current rental week.
        paid_this_week = db.query(Payment).filter(
            Payment.hire_id == current.id,
            Payment.is_active.is_(True),
            Payment.payment_date >= week_start,
            Payment.payment_date <= week_end,
        ).all()
        amount_paid_week = sum((Decimal(p.amount_paid or 0) for p in paid_this_week),
                               Decimal(0))

        owed = weekly_rate - amount_paid_week  # negative => credit / overpaid

        # Days remaining in the rental period (open-ended hires have no end date).
        if current.expected_return_date:
            days_remaining = (current.expected_return_date - today).days
        else:
            days_remaining = None

        data.update({
            "vehicle": vehicle,
            "weekly_rate": weekly_rate,
            "amount_paid_week": amount_paid_week,
            "owed": owed,
            "is_paid": owed <= 0,
            "week_start": week_start,
            "week_end": week_end,
            "next_due_date": week_end + timedelta(days=1),  # next week's rent
            "days_until_due": (week_end - today).days,      # countdown to week end
            "hire_start": current.hire_start_date,
            "expected_return": current.expected_return_date,
            "days_remaining": days_remaining,
        })
        return data
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Serve the public marketing site (index.html). The driver portal lives on
    this SAME app at /login, /register and /dashboard — one server, one origin,
    no subdomain required. The portal's 'Open the Driver Portal' button links to
    /login here."""
    return send_from_directory(SITE_DIR, "index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("driver_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        # Drivers log in with the same thing they registered with: their phone
        # number OR their National Insurance number (NOT email).
        identifier = request.form.get("identifier", "")
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        identity, message = authenticate(identifier, password)
        if not identity:
            flash(message, "danger")
            return render_template("login.html", identifier=identifier), 401

        # Establish the session.
        session.clear()
        session["driver_id"] = identity["driver_id"]
        session["account_id"] = identity["account_id"]

        # "Remember me" makes the cookie persist for PERMANENT_SESSION_LIFETIME;
        # otherwise it is a browser-session cookie that ends when the browser closes.
        session.permanent = remember

        # Cache the driver's display name for the navbar.
        db = SessionLocal()
        try:
            driver = db.query(Driver).filter(Driver.id == identity["driver_id"]).first()
            session["driver_name"] = driver.full_name if driver else "Driver"
        finally:
            db.close()

        # Only follow a local "next" path (never an external URL — open-redirect guard).
        nxt = request.args.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("driver_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        form = {
            "email": request.form.get("email", ""),
            "phone": request.form.get("phone", ""),
            "ni": request.form.get("ni", ""),
        }
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if password != confirm:
            flash("The two passwords don't match.", "danger")
            return render_template("register.html", **form), 400

        account, message = register_driver(
            email=form["email"], password=password,
            phone=form["phone"], ni=form["ni"],
        )
        if not account:
            flash(message, "danger")
            return render_template("register.html", **form), 400

        flash(message, "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    data = build_dashboard(session["driver_id"])
    if data is None:
        # The driver record was removed/deactivated while logged in.
        session.clear()
        flash("Your driver record could not be found. Please contact the office.",
              "danger")
        return redirect(url_for("login"))
    return render_template("dashboard.html", **data)


@app.route("/history.csv")
@login_required
def history_csv():
    """Download the driver's full payment history as a CSV file."""
    db = SessionLocal()
    try:
        hire_ids = [h.id for h in db.query(Hire.id).filter(
            Hire.driver_id == session["driver_id"], Hire.is_active.is_(True)
        ).all()]
        rows = []
        if hire_ids:
            rows = (
                db.query(Payment)
                .filter(Payment.hire_id.in_(hire_ids), Payment.is_active.is_(True))
                .order_by(Payment.payment_date.desc(), Payment.id.desc())
                .all()
            )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Date", "Amount (GBP)", "Payment Method", "Week", "Notes"])
        for p in rows:
            writer.writerow([
                p.payment_date.isoformat() if p.payment_date else "",
                f"{Decimal(p.amount_paid or 0):.2f}",
                (p.payment_method or "").replace("_", " ").title(),
                p.week_reference or "",
                p.notes or "",
            ])

        filename = f"payment_history_{date.today().isoformat()}.csv"
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TEMPLATE HELPERS + ERROR HANDLERS
# ---------------------------------------------------------------------------
@app.template_filter("gbp")
def gbp(value):
    """Format a number as British pounds, e.g. 1234.5 -> '£1,234.50'."""
    try:
        return f"£{Decimal(value):,.2f}"
    except Exception:
        return "£0.00"


@app.template_filter("ukdate")
def ukdate(value):
    """Format a date as DD Mon YYYY, e.g. '13 Jun 2026'."""
    if not value:
        return "—"
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d %b %Y")


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           message=getattr(e, "description", "Bad request.")), 400


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404,
                           message="That page doesn't exist."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500,
                           message="Something went wrong on our end."), 500


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # host=0.0.0.0 makes the portal reachable from other devices on the network
    # (e.g. a driver's phone). Turn debug OFF in production.
    app.run(host="0.0.0.0", port=5000, debug=False)
