"""Server-side browser sessions for the operator (ADR 0005).

Only a hash of the session token is stored, so a database dump cannot be replayed as a
cookie. Each session has its own CSRF token.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from next_role.db import Base, IdMixin


class OperatorSession(IdMixin, Base):
    __tablename__ = "operator_sessions"

    # SHA-256 of the cookie value.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
