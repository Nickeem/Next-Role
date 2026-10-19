"""Aggregates every ORM model so Alembic and tests see the complete metadata."""

from next_role.auth.models import OperatorSession
from next_role.candidates.models import Candidate, CandidateFact, EvidenceSource, FactEvidence
from next_role.db import Base
from next_role.jobs.models import JobOpportunity, JobPosting, JobRequirement
from next_role.matching.models import RequirementAssessment, SuitabilityAssessment
from next_role.notifications.models import NotificationOutbox, NotificationPreferences
from next_role.review.models import OpportunityReview, ReviewEvent

__all__ = [
    "Base",
    "Candidate",
    "CandidateFact",
    "EvidenceSource",
    "FactEvidence",
    "JobOpportunity",
    "JobPosting",
    "JobRequirement",
    "NotificationOutbox",
    "NotificationPreferences",
    "OperatorSession",
    "OpportunityReview",
    "RequirementAssessment",
    "ReviewEvent",
    "SuitabilityAssessment",
]
