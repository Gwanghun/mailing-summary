"""
SQLite database connection, session management, and high-level helper functions
for the mailing_summary storage layer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, List

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from storage.models import Base, ProcessedEmail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons – initialised lazily via _get_engine()
# ---------------------------------------------------------------------------
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _get_engine(db_path: str = "data/mailing_summary.db") -> Engine:
    """Return (and lazily create) the shared SQLAlchemy engine.

    The database file and its parent directory are created automatically if
    they do not already exist.

    Parameters
    ----------
    db_path:
        Relative or absolute path to the SQLite file.

    Returns
    -------
    Engine
        A configured SQLAlchemy engine with WAL journal mode enabled.
    """
    global _engine
    if _engine is None:
        _engine = init_db(db_path)
    return _engine


def init_db(db_path: str = "data/mailing_summary.db") -> Engine:
    """Create the SQLAlchemy engine, ensure the directory exists, and create
    all ORM-defined tables.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file (created if absent).

    Returns
    -------
    Engine
        The configured SQLAlchemy engine.
    """
    global _engine, _SessionLocal

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{path}",
        echo=False,
        connect_args={
            "check_same_thread": False,
            # Enable WAL mode for better concurrent read performance
            "timeout": 30,
        },
    )

    # Enable WAL journal mode for improved concurrency
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    # Create all tables defined in the ORM models
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised at %s", path.resolve())

    _engine = engine
    # expire_on_commit=False keeps objects usable after the session closes,
    # avoiding DetachedInstanceError when callers access attributes post-commit.
    _SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

    return engine


def _get_session_factory(db_path: str = "data/mailing_summary.db") -> sessionmaker[Session]:
    """Return (and lazily create) the session factory.

    Parameters
    ----------
    db_path:
        Path forwarded to :func:`init_db` if the factory has not yet been
        created.

    Returns
    -------
    sessionmaker[Session]
        Configured session factory bound to the shared engine.
    """
    global _SessionLocal
    if _SessionLocal is None:
        _get_engine(db_path)
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def get_db(db_path: str = "data/mailing_summary.db") -> Generator[Session, None, None]:
    """Context manager that yields a database session and handles commit/rollback.

    Usage::

        with get_db() as session:
            session.add(some_model)

    Parameters
    ----------
    db_path:
        Path to the SQLite file (used only on first call to bootstrap the
        engine/session-factory).

    Yields
    ------
    Session
        An active SQLAlchemy ORM session.

    Raises
    ------
    Exception
        Any exception from the body is re-raised after rolling back the
        transaction.
    """
    factory = _get_session_factory(db_path)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# High-level helper functions
# ---------------------------------------------------------------------------


def save_processed_emails(
    emails: List[ProcessedEmail],
    db_path: str = "data/mailing_summary.db",
) -> int:
    """Persist a list of :class:`ProcessedEmail` objects to the database.

    Emails whose ``message_id`` already exists in the database are silently
    skipped (upsert-like behaviour using ``merge``).

    Parameters
    ----------
    emails:
        List of :class:`~storage.models.ProcessedEmail` instances to save.
    db_path:
        Path to the SQLite database (used for lazy initialisation).

    Returns
    -------
    int
        Number of emails actually written (new rows, not merges of existing).
    """
    if not emails:
        return 0

    saved = 0
    with get_db(db_path) as session:
        for email in emails:
            # Read the PK as a plain Python string *before* handing the object
            # to the session, so SQLAlchemy never needs to lazy-load it from a
            # potentially-detached state.
            message_id: str = str(email.message_id)
            existing = session.get(ProcessedEmail, message_id)
            if existing is None:
                session.add(email)
                saved += 1
                logger.debug("Saved new email: %s", message_id)
            else:
                logger.debug("Skipping already-processed email: %s", message_id)

    logger.info("save_processed_emails: %d new records saved (of %d supplied)", saved, len(emails))
    return saved


def is_already_processed(
    message_id: str,
    db_path: str = "data/mailing_summary.db",
) -> bool:
    """Check whether a Gmail message has already been processed.

    Parameters
    ----------
    message_id:
        The Gmail message ID to look up.
    db_path:
        Path to the SQLite database (used for lazy initialisation).

    Returns
    -------
    bool
        ``True`` if the message exists in the ``processed_emails`` table.
    """
    with get_db(db_path) as session:
        result = session.get(ProcessedEmail, message_id)
        return result is not None


def get_processed_today(
    db_path: str = "data/mailing_summary.db",
    reference_date: date | None = None,
) -> List[ProcessedEmail]:
    """Return all emails processed on *today's* date (UTC).

    Parameters
    ----------
    db_path:
        Path to the SQLite database (used for lazy initialisation).
    reference_date:
        Override for the current date (useful in tests). Defaults to
        ``datetime.utcnow().date()``.

    Returns
    -------
    list[ProcessedEmail]
        Emails whose ``processed_at`` falls on the requested date, ordered by
        ``received_at`` descending.
    """
    target = reference_date or datetime.utcnow().date()
    start = datetime(target.year, target.month, target.day, 0, 0, 0)
    end = datetime(target.year, target.month, target.day, 23, 59, 59)

    with get_db(db_path) as session:
        stmt = (
            select(ProcessedEmail)
            .where(ProcessedEmail.processed_at >= start)
            .where(ProcessedEmail.processed_at <= end)
            .order_by(ProcessedEmail.received_at.desc())
        )
        results = list(session.scalars(stmt))

    logger.debug("get_processed_today(%s): %d records found", target.isoformat(), len(results))
    return results
