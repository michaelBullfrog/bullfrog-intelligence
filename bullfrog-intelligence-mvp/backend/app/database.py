from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _normalize_database_url(value: str) -> str:
    url = value.strip()

    # Render may provide postgres:// or postgresql://. SQLAlchemy + psycopg 3
    # should use postgresql+psycopg://.
    if url.startswith("postgres://"):
        return url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )

    if url.startswith("postgresql://") and not url.startswith(
        "postgresql+psycopg://"
    ):
        return url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    return url


DATABASE_URL = _normalize_database_url(settings.database_url)


class Base(DeclarativeBase):
    pass


engine_options: dict = {
    "pool_pre_ping": True,
}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {
        "check_same_thread": False,
    }
else:
    engine_options.update(
        {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_recycle": 300,
        }
    )


engine: Engine = create_engine(
    DATABASE_URL,
    **engine_options,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


@contextmanager
def database_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def initialize_database() -> None:
    # Importing registers model metadata before create_all.
    from . import database_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def database_health() -> dict[str, object]:
    with database_session() as session:
        value = session.execute(text("SELECT 1")).scalar_one()

    return {
        "connected": value == 1,
        "driver": engine.url.drivername,
        "database": engine.url.database,
    }
