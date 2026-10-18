"""Aggregates every ORM model so Alembic and tests see the complete metadata."""

from next_role.candidates.models import Candidate, CandidateFact, EvidenceSource, FactEvidence
from next_role.db import Base

__all__ = ["Base", "Candidate", "CandidateFact", "EvidenceSource", "FactEvidence"]
