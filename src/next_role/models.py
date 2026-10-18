"""Aggregates every ORM model so Alembic and tests see the complete metadata."""

from next_role.db import Base

__all__ = ["Base"]
