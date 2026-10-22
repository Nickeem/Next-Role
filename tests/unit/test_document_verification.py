"""Claim verification: protected values, evidence, wording, trust, and connective text."""

import uuid
from typing import Any

import pytest

from next_role.documents.drafting import CLOSING, HIGHLIGHTS, opening
from next_role.documents.schemas import CoverLetterDocument, ResumeDocument, Target
from next_role.documents.service import verification_context
from next_role.documents.verification import (
    Verdict,
    semantic_key,
    semantic_requests,
    stale_evidence,
    verify,
)
from next_role.documents.workflow import ClaimStatus, VerificationOutcome
from tests.unit.document_fixtures import ACH, EDU, EXP, OLD_EXP, SKILL_PY, facts

FACTS = {f.id: f for f in facts()}
TARGET = Target(job_title="Senior Backend Engineer", company="Acme Logistics (fictional)")


def _resume(bullets: list[dict[str, Any]], **item: Any) -> ResumeDocument:
    return ResumeDocument.model_validate(
        {
            "header": {"name": "Alex Example"},
            "target": TARGET.model_dump(),
            "sections": [
                {
                    "id": "experience",
                    "type": "experience",
                    "title": "Experience",
                    "items": [
                        {
                            "id": "exp-1",
                            "source_fact_id": str(EXP),
                            "organization": "Northwind Freight (fictional)",
                            "position": "Backend Engineer",
                            "location": "Toronto, ON",
                            "start_date": "2019-03",
                            "is_current": True,
                            "bullets": bullets,
                            **item,
                        }
                    ],
                }
            ],
        }
    )


def _claim(text: str, *evidence: uuid.UUID | str, claim_id: str = "c-1") -> dict[str, Any]:
    return {"claim_id": claim_id, "text": text, "evidence_ids": [str(e) for e in evidence]}


def _check(doc: Any, *, semantic: bool = False, verdicts: Any = None, fact_map: Any = None) -> Any:
    ctx = verification_context(fact_map or FACTS, {}, TARGET, semantic, verdicts)
    return verify(doc, ctx)


def _status(result: Any, claim_id: str = "c-1") -> tuple[ClaimStatus, list[str]]:
    claim = next(c for c in result.claims if c.claim_id == claim_id)
    return claim.status, [i.code for i in claim.issues]


def test_verbatim_claims_from_confirmed_facts_pass() -> None:
    doc = _resume(
        [
            _claim("Built REST APIs in Python for shipment tracking.", EXP),
            _claim("Cut p95 API latency by 40% by adding query caching", ACH, claim_id="c-2"),
        ]
    )
    result = _check(doc)
    assert result.outcome is VerificationOutcome.PASSED
    item = next(c for c in result.claims if c.claim_id == "item-exp-1")
    assert item.evidence == {str(EXP): FACTS[EXP].content_hash}


@pytest.mark.parametrize(
    ("text", "evidence", "code"),
    [
        ("Cut p95 API latency by 60% by adding query caching", [ACH], "unsupported_number"),
        ("Built REST APIs in Python and Kubernetes", [EXP], "unsupported_skill"),
        ("Built REST APIs in Python for shipment tracking", [uuid.uuid4()], "unknown_evidence"),
        ("Built REST APIs in Python for shipment tracking", [], "no_evidence"),
        # A fact from another job cannot support a bullet under this one.
        ("Wrote internal dashboards", [OLD_EXP], "misattributed"),
        # Statements are for cover letters only.
        (
            "Built REST APIs in Python for shipment tracking",
            [f"stmt:{uuid.uuid4()}"],
            "unknown_evidence",
        ),
    ],
)
def test_deterministic_checks_reject_unsupported_claims(
    text: str, evidence: list[Any], code: str
) -> None:
    result = _check(_resume([_claim(text, *evidence)]))
    status, codes = _status(result)
    assert status is ClaimStatus.UNSUPPORTED
    assert code in codes
    assert result.outcome is VerificationOutcome.BLOCKED


def test_a_dropped_negation_is_not_verbatim() -> None:
    fact_map = dict(FACTS)
    fact_map[EXP] = FACTS[EXP].model_copy(
        update={
            "attributes": {
                **FACTS[EXP].attributes,
                "responsibilities": ["Did not lead the migration"],
            }
        }
    )
    result = _check(_resume([_claim("lead the migration", EXP)]), fact_map=fact_map)
    assert _status(result) == (ClaimStatus.UNSUPPORTED, ["not_verbatim"])


def test_rewording_without_an_llm_is_blocking_and_explains_how_to_resolve() -> None:
    result = _check(_resume([_claim("Developed Python REST services for tracking shipments", EXP)]))
    status, codes = _status(result)
    assert (status, codes) == (ClaimStatus.UNSUPPORTED, ["not_verbatim"])
    message = result.claims[1].issues[0].message
    assert "confirm it as a new fact" in message


def test_rewording_with_an_llm_follows_its_verdict() -> None:
    doc = _resume([_claim("Developed Python REST services for tracking shipments", EXP)])
    pending = _check(doc, semantic=True)
    assert _status(pending) == (ClaimStatus.NEEDS_REVIEW, ["awaiting_check"])
    ctx = verification_context(FACTS, {}, TARGET, True)
    [request] = semantic_requests(pending, doc, ctx)
    assert "Built REST APIs in Python for shipment tracking" in request.sources

    for verdict, expected in [
        (Verdict.ENTAILED, ClaimStatus.SUPPORTED),
        (Verdict.UNCERTAIN, ClaimStatus.NEEDS_REVIEW),
        (Verdict.NOT_ENTAILED, ClaimStatus.UNSUPPORTED),
    ]:
        result = _check(doc, semantic=True, verdicts={request.key: verdict})
        assert _status(result)[0] is expected


def test_an_llm_cannot_override_a_deterministic_failure() -> None:
    doc = _resume([_claim("Cut p95 API latency by 90%", ACH)])
    key = semantic_key("Cut p95 API latency by 90%", [], False)
    result = _check(doc, semantic=True, verdicts={key: Verdict.ENTAILED})
    assert _status(result)[0] is ClaimStatus.UNSUPPORTED


def test_claims_on_unconfirmed_facts_need_review() -> None:
    fact_map = {f.id: f for f in facts(**{str(ACH): {"confirmed": False}})}
    result = _check(
        _resume([_claim("Cut p95 API latency by 40% by adding query caching", ACH)]),
        fact_map=fact_map,
    )
    assert _status(result) == (ClaimStatus.NEEDS_REVIEW, ["unconfirmed_fact"])
    assert result.outcome is VerificationOutcome.BLOCKED


@pytest.mark.parametrize(
    ("field", "value"),
    [("organization", "Globex"), ("position", "Staff Engineer"), ("start_date", "2015-01")],
)
def test_protected_entry_fields_must_match_the_profile(field: str, value: str) -> None:
    result = _check(_resume([], **{field: value}))
    assert _status(result, "item-exp-1") == (ClaimStatus.UNSUPPORTED, ["protected_field"])


def test_skill_claims_must_be_among_cited_facts() -> None:
    doc = ResumeDocument.model_validate(
        {
            "header": {"name": "Alex Example"},
            "sections": [
                {
                    "id": "education",
                    "type": "education",
                    "title": "Education",
                    "items": [
                        {
                            "id": "edu-1",
                            "source_fact_id": str(EDU),
                            "qualification": "BSc Computer Science",
                            "institution": "Example University (fictional)",
                            "field_of_study": "Computer Science",
                            "end_date": "2018-05",
                        }
                    ],
                },
                {
                    "id": "skills",
                    "type": "skills",
                    "title": "Skills",
                    "skills": [
                        _claim("Python", SKILL_PY, claim_id="s-1"),
                        _claim("Postgres", EXP, claim_id="s-2"),
                        _claim("Kubernetes", SKILL_PY, claim_id="s-3"),
                    ],
                },
            ],
        }
    )
    result = _check(doc)
    assert _status(result, "s-1")[0] is ClaimStatus.SUPPORTED
    assert _status(result, "s-2")[0] is ClaimStatus.SUPPORTED
    assert _status(result, "s-3") == (ClaimStatus.UNSUPPORTED, ["unsupported_skill"])


def _letter(claims: list[dict[str, Any]]) -> CoverLetterDocument:
    return CoverLetterDocument.model_validate(
        {
            "header": {"name": "Alex Example"},
            "target": TARGET.model_dump(),
            "paragraphs": [{"id": "p1", "claims": claims}],
        }
    )


def test_letter_connective_templates_and_statements() -> None:
    stmt = f"stmt:{uuid.uuid4()}"
    doc = _letter(
        [
            {"claim_id": "o", "text": opening(TARGET), "connective": True},
            {"claim_id": "h", "text": HIGHLIGHTS, "connective": True},
            {"claim_id": "w", "text": "I admire your routing work.", "evidence_ids": [stmt]},
            {"claim_id": "c", "text": CLOSING, "connective": True},
        ]
    )
    ctx = verification_context(FACTS, {stmt: "I admire your routing work."}, TARGET, False)
    assert verify(doc, ctx).outcome is VerificationOutcome.PASSED


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("I have 10 years of experience.", "connective_states_facts"),
        ("I am an expert in Kubernetes.", "connective_states_facts"),
        ("I would love to join your team.", "not_verbatim"),
    ],
)
def test_connective_text_cannot_smuggle_claims(text: str, code: str) -> None:
    result = _check(_letter([{"claim_id": "x", "text": text, "connective": True}]))
    assert _status(result, "x") == (ClaimStatus.UNSUPPORTED, [code])


def test_stale_evidence_detects_retired_edited_and_unconfirmed_facts() -> None:
    result = _check(
        _resume(
            [
                _claim("Built REST APIs in Python for shipment tracking", EXP),
                _claim("Cut p95 API latency by 40% by adding query caching", ACH, claim_id="c-2"),
            ]
        )
    )
    claims = [c.as_dict() for c in result.claims]
    assert stale_evidence(claims, FACTS) == []

    retired = {k: v for k, v in FACTS.items() if k != ACH}
    assert stale_evidence(claims, retired) == ["c-2"]
    edited = dict(FACTS)
    edited[ACH] = FACTS[ACH].model_copy(update={"content_hash": "x" * 64})
    assert stale_evidence(claims, edited) == ["c-2"]
    unconfirmed = {f.id: f for f in facts(**{str(EXP): {"confirmed": False}})}
    assert stale_evidence(claims, unconfirmed) == ["item-exp-1", "c-1"]
