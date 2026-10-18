"""Aggregates every ORM model so Alembic and tests see the complete metadata."""

from next_role.candidates.models import Candidate, CandidateFact, EvidenceSource, FactEvidence
from next_role.db import Base
from next_role.jobs.models import JobOpportunity, JobPosting, JobRequirement

__all__ = [
    "Base",
    "Candidate",
    "CandidateFact",
    "EvidenceSource",
    "FactEvidence",
    "JobOpportunity",
    "JobPosting",
    "JobRequirement",
]
