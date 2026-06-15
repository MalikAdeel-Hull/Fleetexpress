# =============================================================================
# VRMS Driver Portal — admin/maintenance helper
# Run from the driver_portal folder:  python manage.py <command>
#
#   python manage.py list                       # show all portal accounts
#   python manage.py delete  <email>            # remove an account (driver can re-register)
#   python manage.py reset   <email> <password> # set a NEW password (pbkdf2 hash)
#
# Use this to recover the account that was created before the scrypt->pbkdf2
# fix: either `delete` it and register again, or `reset` its password here.
# =============================================================================

import sys

from werkzeug.security import generate_password_hash

from db import SessionLocal, init_db
from auth import HASH_METHOD, normalize_email
from models import DriverAccount, Driver


def _list():
    s = SessionLocal()
    try:
        rows = s.query(DriverAccount).all()
        if not rows:
            print("No portal accounts yet.")
            return
        print(f"{'id':>3}  {'email':<32} {'driver':<25} {'active':<6} last_login")
        for a in rows:
            drv = s.query(Driver).filter(Driver.id == a.driver_id).first()
            name = drv.full_name if drv else f"#{a.driver_id}"
            print(f"{a.id:>3}  {a.email:<32} {name:<25} "
                  f"{str(a.is_active):<6} {a.last_login or '-'}")
    finally:
        s.close()


def _delete(email):
    email = normalize_email(email)
    s = SessionLocal()
    try:
        a = s.query(DriverAccount).filter(DriverAccount.email == email).first()
        if not a:
            print(f"No account found for {email}")
            return
        s.delete(a)
        s.commit()
        print(f"Deleted account {email}. That driver can now register again.")
    finally:
        s.close()


def _reset(email, password):
    email = normalize_email(email)
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return
    s = SessionLocal()
    try:
        a = s.query(DriverAccount).filter(DriverAccount.email == email).first()
        if not a:
            print(f"No account found for {email}")
            return
        a.password_hash = generate_password_hash(password, method=HASH_METHOD)
        a.is_active = True
        s.commit()
        print(f"Password reset for {email}. They can now log in.")
    finally:
        s.close()


if __name__ == "__main__":
    init_db()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
    elif args[0] == "list":
        _list()
    elif args[0] == "delete" and len(args) == 2:
        _delete(args[1])
    elif args[0] == "reset" and len(args) == 3:
        _reset(args[1], args[2])
    else:
        print(__doc__)
