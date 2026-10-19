"""Review state machine: states, candidate actions, and the rules for applying them.

Pure logic over plain values, so it can be tested without a database. The service layer
loads the review and its latest assessment, calls :func:`decide`, and persists the result.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from next_role.errors import ConflictError, InvalidInputError
from next_role.matching.engine import Tier


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    SHORTLISTED = "shortlisted"
    POSTPONED = "postponed"
    REJECTED = "rejected"
    APPROVED_FOR_DRAFTING = "approved_for_drafting"


class ReviewAction(StrEnum):
    """Decisions the candidate can make. Nothing else changes a review's status."""

    SHORTLIST = "shortlist"
    POSTPONE = "postpone"
    REJECT = "reject"
    APPROVE_FOR_DRAFTING = "approve_for_drafting"
    RETURN_TO_REVIEW = "return_to_review"
    REOPEN = "reopen"


class ReviewEventType(StrEnum):
    """Everything recorded in a review's history: candidate actions and system events."""

    CREATED = "created"
    ASSESSMENT_UPDATED = "assessment_updated"
    SHORTLIST = ReviewAction.SHORTLIST.value
    POSTPONE = ReviewAction.POSTPONE.value
    REJECT = ReviewAction.REJECT.value
    APPROVE_FOR_DRAFTING = ReviewAction.APPROVE_FOR_DRAFTING.value
    RETURN_TO_REVIEW = ReviewAction.RETURN_TO_REVIEW.value
    REOPEN = ReviewAction.REOPEN.value


_S = ReviewStatus
_A = ReviewAction

_DECIDABLE = frozenset({_S.PENDING_REVIEW, _S.SHORTLISTED, _S.POSTPONED})

# action -> (statuses it may be applied from, resulting status)
TRANSITIONS: dict[ReviewAction, tuple[frozenset[ReviewStatus], ReviewStatus]] = {
    _A.SHORTLIST: (frozenset({_S.PENDING_REVIEW, _S.POSTPONED}), _S.SHORTLISTED),
    _A.POSTPONE: (_DECIDABLE, _S.POSTPONED),
    _A.REJECT: (_DECIDABLE, _S.REJECTED),
    _A.APPROVE_FOR_DRAFTING: (_DECIDABLE, _S.APPROVED_FOR_DRAFTING),
    _A.RETURN_TO_REVIEW: (frozenset({_S.SHORTLISTED, _S.POSTPONED}), _S.PENDING_REVIEW),
    # Reopening an approval revokes it. Phase 3 must refuse once drafting has started.
    _A.REOPEN: (frozenset({_S.REJECTED, _S.APPROVED_FOR_DRAFTING}), _S.PENDING_REVIEW),
}

_REQUIRES_CONFIRMATION = frozenset({_A.REJECT, _A.APPROVE_FOR_DRAFTING})
_POSITIVE = frozenset({_A.SHORTLIST, _A.APPROVE_FOR_DRAFTING})


def allowed_actions(status: ReviewStatus) -> list[ReviewAction]:
    return [action for action, (sources, _) in TRANSITIONS.items() if status in sources]


@dataclass(frozen=True)
class AssessmentSignal:
    """The parts of an assessment that decide whether a choice overrides it."""

    tier: Tier
    has_blocking_gaps: bool


def overrides_assessment(action: ReviewAction, signal: AssessmentSignal) -> bool:
    """Whether the action goes against the assessment, so the candidate must give a reason."""
    if action in _POSITIVE:
        return signal.has_blocking_gaps or signal.tier in {Tier.WEAK, Tier.INSUFFICIENT_DATA}
    if action is _A.REJECT:
        return signal.tier is Tier.STRONG and not signal.has_blocking_gaps
    return False


@dataclass(frozen=True)
class Decision:
    action: ReviewAction
    reason: str | None = None
    confirm: bool = False
    postpone_until: datetime | None = None


@dataclass(frozen=True)
class Outcome:
    new_status: ReviewStatus
    postpone_until: datetime | None
    overrides_assessment: bool


def decide(
    status: ReviewStatus, decision: Decision, signal: AssessmentSignal, now: datetime
) -> Outcome:
    """Validate a decision against the current status and return the resulting state.

    Raises ConflictError for a transition that is not allowed from ``status`` and
    InvalidInputError for a missing confirmation, reason, or postpone date.
    """
    sources, target = TRANSITIONS[decision.action]
    if status not in sources:
        raise ConflictError(f"Cannot {decision.action.value} a review that is {status.value}")

    if decision.action in _REQUIRES_CONFIRMATION and not decision.confirm:
        raise InvalidInputError(f"{decision.action.value} requires confirm=true")

    override = overrides_assessment(decision.action, signal)
    has_reason = bool(decision.reason and decision.reason.strip())
    if (override or decision.action is _A.REOPEN) and not has_reason:
        raise InvalidInputError(
            "A reason is required to reopen a review"
            if decision.action is _A.REOPEN
            else f"A reason is required to {decision.action.value} against the assessment"
        )

    postpone_until = None
    if decision.action is _A.POSTPONE:
        if decision.postpone_until is None:
            raise InvalidInputError("postpone requires postpone_until")
        if decision.postpone_until.tzinfo is None:
            raise InvalidInputError("postpone_until must include a timezone")
        if decision.postpone_until <= now:
            raise InvalidInputError("postpone_until must be in the future")
        postpone_until = decision.postpone_until
    elif decision.postpone_until is not None:
        raise InvalidInputError("postpone_until is only accepted with postpone")

    return Outcome(new_status=target, postpone_until=postpone_until, overrides_assessment=override)
