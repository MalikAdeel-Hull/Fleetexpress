# =============================================================================
# VRMS Driver Portal
# File: db.py
# Purpose: Owns the SQLAlchemy engine and session factory for the portal.
#          (Small helper module so app.py and auth.py share ONE engine instead
#          of each opening their own connection pool.)
# =============================================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from config import Config
from models import Base  # noqa: F401  (Base carries every table's metadata)

# ---------------------------------------------------------------------------
# Engine — the low-level pool of connections to the database chosen in config.
# ---------------------------------------------------------------------------
engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    echo=False,
    **Config.SQLALCHEMY_ENGINE_OPTIONS,
)

# scoped_session gives each web request its own session automatically and is the
# standard pattern for using bare SQLAlchemy inside Flask.
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False))


def init_db() -> None:
    """
    Create any missing tables. In practice this only creates `driver_accounts`
    (every other table already exists, created by the desktop app). create_all
    uses CREATE TABLE IF NOT EXISTS, so it never touches existing data.
    """
    Base.metadata.create_all(bind=engine)
