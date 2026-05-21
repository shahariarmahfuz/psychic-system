import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def db_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so SQLAlchemy metadata is populated.
    from . import models  # noqa: F401

    with engine.begin() as conn:
        try:
            conn.execute(text('SELECT pg_advisory_lock(98234123)'))
            Base.metadata.create_all(bind=conn)
            conn.execute(text('SELECT pg_advisory_unlock(98234123)'))
        except Exception:
            try:
                conn.execute(text('SELECT pg_advisory_unlock(98234123)'))
            except Exception:
                pass
            raise

    logger.info('Database initialized successfully')
