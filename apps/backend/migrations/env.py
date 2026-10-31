"""PostgreSQL migration environment; never read or print passwords from Git."""
import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
from models import Base

config = context.config
target_metadata = Base.metadata

def url_from_environment():
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if not all(os.environ.get(k) for k in required):
        raise RuntimeError("Required DB_* environment variables are missing")
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
    )

def run_migrations_online():
    engine = create_engine(url_from_environment(), poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata,
                compare_type=True, transactional_ddl=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()

if context.is_offline_mode():
    raise RuntimeError("Offline migration generation is disabled; use a connected PostgreSQL")
else:
    run_migrations_online()
