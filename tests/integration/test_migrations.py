from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from next_role.models import Base
from tests.conftest import TEST_DATABASE_URL, alembic_config


def test_downgrade_and_upgrade_round_trip(engine: Engine) -> None:
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert {t.name for t in Base.metadata.sorted_tables} <= set(inspect(engine).get_table_names())


def test_models_match_migrated_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"ORM models and migrations have drifted: {diff}"
