"""Review state machine: transition matrix, confirmations, reasons, and postpone dates."""

from datetime import UTC, datetime, timedelta

import pytest

from next_role.errors import ConflictError, InvalidInputError
from next_role.matching.engine import Tier
from next_role.review.workflow import (
    AssessmentSignal,
    Decision,
    ReviewAction,
    ReviewStatus,
    allowed_actions,
    decide,
    overrides_assessment,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
MODERATE = AssessmentSignal(tier=Tier.MODERATE, has_blocking_gaps=False)
S = ReviewStatus
A = ReviewAction

EXPECTED = {
    S.PENDING_REVIEW: {
        A.SHORTLIST: S.SHORTLISTED,
        A.POSTPONE: S.POSTPONED,
        A.REJECT: S.REJECTED,
        A.APPROVE_FOR_DRAFTING: S.APPROVED_FOR_DRAFTING,
    },
    S.SHORTLISTED: {
        A.POSTPONE: S.POSTPONED,
        A.REJECT: S.REJECTED,
        A.APPROVE_FOR_DRAFTING: S.APPROVED_FOR_DRAFTING,
        A.RETURN_TO_REVIEW: S.PENDING_REVIEW,
    },
    S.POSTPONED: {
        A.SHORTLIST: S.SHORTLISTED,
        A.POSTPONE: S.POSTPONED,
        A.REJECT: S.REJECTED,
        A.APPROVE_FOR_DRAFTING: S.APPROVED_FOR_DRAFTING,
        A.RETURN_TO_REVIEW: S.PENDING_REVIEW,
    },
    S.REJECTED: {A.REOPEN: S.PENDING_REVIEW},
    S.APPROVED_FOR_DRAFTING: {A.REOPEN: S.PENDING_REVIEW},
}


def _valid(action: ReviewAction) -> Decision:
    """A decision with everything the action could require."""
    return Decision(
        action=action,
        reason="Checked the details",
        confirm=True,
        postpone_until=NOW + timedelta(days=3) if action is A.POSTPONE else None,
    )


@pytest.mark.parametrize("status", list(S))
@pytest.mark.parametrize("action", list(A))
def test_transition_matrix(status: ReviewStatus, action: ReviewAction) -> None:
    expected = EXPECTED[status].get(action)
    if expected is None:
        with pytest.raises(ConflictError):
            decide(status, _valid(action), MODERATE, NOW)
    else:
        assert decide(status, _valid(action), MODERATE, NOW).new_status is expected


@pytest.mark.parametrize("status", list(S))
def test_allowed_actions_match_matrix(status: ReviewStatus) -> None:
    assert set(allowed_actions(status)) == set(EXPECTED[status])


@pytest.mark.parametrize("action", [A.REJECT, A.APPROVE_FOR_DRAFTING])
def test_reject_and_approve_require_confirmation(action: ReviewAction) -> None:
    with pytest.raises(InvalidInputError, match="confirm"):
        decide(S.PENDING_REVIEW, Decision(action=action, reason="x"), MODERATE, NOW)


def test_shortlist_needs_no_confirmation_or_reason_when_consistent() -> None:
    outcome = decide(S.PENDING_REVIEW, Decision(action=A.SHORTLIST), MODERATE, NOW)
    assert outcome.new_status is S.SHORTLISTED
    assert outcome.overrides_assessment is False


@pytest.mark.parametrize(
    ("action", "signal", "overrides"),
    [
        (A.APPROVE_FOR_DRAFTING, AssessmentSignal(Tier.WEAK, False), True),
        (A.APPROVE_FOR_DRAFTING, AssessmentSignal(Tier.INSUFFICIENT_DATA, False), True),
        (A.APPROVE_FOR_DRAFTING, AssessmentSignal(Tier.STRONG, True), True),
        (A.APPROVE_FOR_DRAFTING, AssessmentSignal(Tier.MODERATE, False), False),
        (A.SHORTLIST, AssessmentSignal(Tier.WEAK, False), True),
        (A.REJECT, AssessmentSignal(Tier.STRONG, False), True),
        (A.REJECT, AssessmentSignal(Tier.STRONG, True), False),
        (A.REJECT, AssessmentSignal(Tier.WEAK, False), False),
        (A.POSTPONE, AssessmentSignal(Tier.WEAK, True), False),
    ],
)
def test_overrides_assessment(
    action: ReviewAction, signal: AssessmentSignal, overrides: bool
) -> None:
    assert overrides_assessment(action, signal) is overrides


def test_low_score_does_not_block_approval_but_requires_reason() -> None:
    weak = AssessmentSignal(tier=Tier.WEAK, has_blocking_gaps=True)
    decision = Decision(action=A.APPROVE_FOR_DRAFTING, confirm=True)
    with pytest.raises(InvalidInputError, match="reason"):
        decide(S.PENDING_REVIEW, decision, weak, NOW)
    with pytest.raises(InvalidInputError, match="reason"):
        decide(S.PENDING_REVIEW, Decision(A.APPROVE_FOR_DRAFTING, "   ", True), weak, NOW)
    outcome = decide(
        S.PENDING_REVIEW, Decision(A.APPROVE_FOR_DRAFTING, "Referral", True), weak, NOW
    )
    assert outcome.new_status is S.APPROVED_FOR_DRAFTING
    assert outcome.overrides_assessment is True


def test_reopen_requires_reason() -> None:
    with pytest.raises(InvalidInputError, match="reason"):
        decide(S.REJECTED, Decision(action=A.REOPEN), MODERATE, NOW)


def test_postpone_requires_future_aware_date() -> None:
    with pytest.raises(InvalidInputError, match="requires postpone_until"):
        decide(S.PENDING_REVIEW, Decision(action=A.POSTPONE), MODERATE, NOW)
    with pytest.raises(InvalidInputError, match="future"):
        decide(S.PENDING_REVIEW, Decision(A.POSTPONE, postpone_until=NOW), MODERATE, NOW)
    with pytest.raises(InvalidInputError, match="timezone"):
        naive = datetime(2027, 1, 1)
        decide(S.PENDING_REVIEW, Decision(A.POSTPONE, postpone_until=naive), MODERATE, NOW)
    later = NOW + timedelta(hours=1)
    outcome = decide(S.PENDING_REVIEW, Decision(A.POSTPONE, postpone_until=later), MODERATE, NOW)
    assert outcome.postpone_until == later


def test_postpone_date_rejected_with_other_actions() -> None:
    later = NOW + timedelta(days=1)
    with pytest.raises(InvalidInputError, match="only accepted with postpone"):
        decide(S.PENDING_REVIEW, Decision(A.SHORTLIST, postpone_until=later), MODERATE, NOW)


@pytest.mark.parametrize("action", [A.SHORTLIST, A.RETURN_TO_REVIEW, A.REJECT])
def test_leaving_postponed_clears_the_date(action: ReviewAction) -> None:
    outcome = decide(S.POSTPONED, Decision(action, "x", True), MODERATE, NOW)
    assert outcome.postpone_until is None
