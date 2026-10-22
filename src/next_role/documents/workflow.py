"""Document lifecycles: generation runs, bundles, verifications, clarifications, artifacts.

Pure logic over plain values, like ``review.workflow``. Generation runs, review state and
artifact rendering are separate lifecycles (specification §7), so no single enum has to
describe every combination.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from next_role.errors import ConflictError


class RunIntent(StrEnum):
    # Draft both documents after an approval to draft.
    INITIAL = "initial"
    # Redraft one section of one document.
    REGENERATE_SECTION = "regenerate_section"
    # Check the current revisions again (after an edit or a confirmed fact).
    VERIFY = "verify"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BundleStatus(StrEnum):
    # Questions or blocking issues remain; approval is not possible yet.
    NEEDS_INPUT = "needs_input"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    # The approval to draft was revoked (review reopened). Kept for history.
    WITHDRAWN = "withdrawn"


class VerificationOutcome(StrEnum):
    # Every claim is supported and there are no blocking issues.
    PASSED = "passed"
    BLOCKED = "blocked"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    # Checks could not establish the claim (uncertain entailment, unconfirmed fact).
    NEEDS_REVIEW = "needs_review"
    UNSUPPORTED = "unsupported"


class ClarificationStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
    # A newer revision or style replaced it before or after rendering.
    SUPERSEDED = "superseded"


ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})
SETTLED_ARTIFACT_STATUSES = frozenset({ArtifactStatus.READY, ArtifactStatus.FAILED})
CLOSED_BUNDLE_STATUSES = frozenset({BundleStatus.WITHDRAWN})


def bundle_status(*, verifications_passed: bool, open_material_questions: int) -> BundleStatus:
    """Status of an open bundle after any change. A change never keeps an approval."""
    if verifications_passed and open_material_questions == 0:
        return BundleStatus.READY_FOR_REVIEW
    return BundleStatus.NEEDS_INPUT


def ensure_open(status: BundleStatus) -> None:
    if status in CLOSED_BUNDLE_STATUSES:
        raise ConflictError(f"This document bundle is {status.value}")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(minutes=30)

    def next_attempt(self, attempts: int, now: datetime) -> datetime | None:
        """When to try again after ``attempts`` failed attempts, or None to give up."""
        if attempts >= self.max_attempts:
            return None
        # Cap the exponent so a large budget cannot overflow.
        delay: timedelta = self.base_delay * (2 ** min(attempts - 1, 16))
        return now + min(delay, self.max_delay)
