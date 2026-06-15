# ECO Express — Driver Portal

A small Flask web app that lets taxi drivers check their rental payment status
(weekly rent, amount paid this week, amount owed, PAID/PENDING status, days left,
and payment history). It reads from the **same database** as the VRMS desktop
app and reuses its SQLAlchemy models.

## How it fits with the desktop app

- The portal **imports** the desktop app's models (`Driver`, `Vehicle`, `Hire`,
  `Payment`) from `VRMS_APP_PATH` so the schemas can never drift apart.
- It adds **one new table**, `driver_accounts`, holding each driver's web login
  (email + password hash). Your existing `drivers` table is left untouched.
- Drivers **self-register**: they enter their name plus the phone number or
  licence number the office has on file. The portal matches that against the
  `drivers` table and links the new account to their `driver_id`.

> Note: in the current live data many drivers have no phone/licence recorded, so
> registration falls back to a unique-name match for those records. Fill in
> phone/licence in the desktop app to make verification stronger — see
> `auth.py → _find_driver_for_registration`.

## Setup

```bash
cd driver_portal
python -m venv venv
venv\Scripts\activate            # Windows  (use: source venv/bin/activate on Mac/Linux)
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set at least `VRMS_APP_PATH` and
`FLASK_SECRET_KEY`. (The app reads real environment variables; if you use a
`.env` file, load it with your process manager or `pip install python-dotenv`.)

## Run

```bash
python app.py
```

Open http://localhost:5000 (or http://<this-machine-ip>:5000 from a phone on the
same network). First run creates the `driver_accounts` table automatically.

For production use a real WSGI server instead of the dev server, e.g.:

```bash
pip install waitress
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

## Database: SQLite now, PostgreSQL later

By default the portal opens the desktop app's `vrms.db` directly. That works, but
SQLite is single-writer — under real multi-driver load you may hit "database is
locked" errors, and it must never be served from a network share. For production,
run **PostgreSQL** as a shared server: migrate the data, then set
`DATABASE_URL=postgresql+psycopg2://...` and install `psycopg2-binary`. No code
changes required.

## Security notes

- Passwords hashed with `werkzeug.security` (pbkdf2-sha256).
- CSRF token on every form, checked on every POST.
- Session cookie is HttpOnly + SameSite=Lax; set `SESSION_COOKIE_SECURE=1` on HTTPS.
- Idle session timeout (default 30 min); "Keep me logged in" extends it.
- Open-redirect guard on the post-login `next` parameter.

## File structure

```
driver_portal/
├── app.py            # Flask routes, dashboard logic, CSRF, CSV export
├── config.py         # DB selection + security settings
├── db.py             # engine + session factory (shared by app & auth)
├── models.py         # imports desktop models + adds DriverAccount
├── auth.py           # registration + login logic
├── requirements.txt
├── .env.example
├── templates/        # base, login, register, dashboard, error
└── static/style.css
```
