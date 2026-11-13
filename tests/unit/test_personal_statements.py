"""Personal statements (ADR 0009): claims that cite the about-me instead of facts."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from next_role.documents import drafting, generation
from next_role.documents.details import checkable_details
from next_role.documents.models import DocumentKind
from next_role.documents.schemas import (
    ABOUT_ME_ID,
    Claim,
    CoverLetterDocument,
    Paragraph,
    ResumeDocument,
    SummarySection,
    iter_claims,
)
from next_role.documents.service import verification_context
from next_role.documents.snapshot import about_me_hash
from next_role.documents.verification import Verdict, semantic_requests, stale_evidence, verify
from next_role.documents.workflow import ClaimStatus
from next_role.integrations.llm.gateway import LLMGateway
from tests.unit.document_fixtures import EXP, SKILL_PY, facts, snapshot
from tests.unit.test_document_drafting import LETTER_OUTPUT, FakeProvider, _resume_output

TODAY = date(2026, 10, 22)
ABOUT_ME = (
    "I enjoy turning slow, fragile systems into calm, observable ones. "
    "I like small teams that write things down. "
    "I have spent 10 years leading teams at Initech."
)
CALM = "I enjoy turning slow, fragile systems into calm, observable ones."


def _snap(about_me: str | None = ABOUT_ME, fact_list: Any = None) -> Any:
    snap = snapshot(fact_list)
    candidate = snap.candidate.model_copy(
        update={"about_me": about_me, "about_me_hash": about_me_hash(about_me)}
    )
    return snap.model_copy(update={"candidate": candidate})


def _ctx(snap: Any, semantic: bool = False, verdicts: Any = None) -> Any:
    return verification_context(
        {f.id: f for f in snap.facts},
        {},
        drafting.target_of(snap),
        semantic,
        verdicts,
        snap.candidate.about_me,
    )


def _letter(snap: Any, *claims: Claim) -> CoverLetterDocument:
    return CoverLetterDocument(
        header=drafting.header_of(snap),
        target=drafting.target_of(snap),
        paragraphs=[Paragraph(id="p1", claims=list(claims))],
    )


def _resume(snap: Any, *summary: Claim) -> ResumeDocument:
    base = drafting.assemble_resume(snap, TODAY)
    sections = [s for s in base.sections if not isinstance(s, SummarySection)]
    return base.model_copy(
        update={
            "sections": [
                SummarySection(id="summary", title="Summary", claims=list(summary)),
                *sections,
            ]
        }
    )


def _personal(text: str, *facts_cited: Any, claim_id: str = "p1-s1") -> Claim:
    return Claim(
        claim_id=claim_id, text=text, evidence_ids=[ABOUT_ME_ID, *(str(f) for f in facts_cited)]
    )


def _result(doc: Any, snap: Any, **ctx: Any) -> dict[str, Any]:
    return {c.claim_id: c for c in verify(doc, _ctx(snap, **ctx)).claims}


# --- Representation -----------------------------------------------------------------------


def test_a_claim_citing_the_about_me_is_a_personal_statement() -> None:
    assert _personal(CALM).personal
    assert not Claim(claim_id="c1", text=CALM, evidence_ids=[str(EXP)]).personal
    with pytest.raises(ValueError):
        Claim(claim_id="c1", text=CALM, evidence_ids=["about:you"])


# --- Placement ----------------------------------------------------------------------------


def test_allowed_in_any_letter_paragraph_and_once_in_the_resume_summary() -> None:
    snap = _snap()
    letter = _result(_letter(snap, _personal(CALM), _personal(CALM, claim_id="p1-s2")), snap)
    assert {c.status for c in letter.values()} == {ClaimStatus.SUPPORTED}

    resume = _result(
        _resume(snap, _personal(CALM, claim_id="sum-1"), _personal(CALM, claim_id="sum-2")), snap
    )
    assert resume["sum-1"].status is ClaimStatus.SUPPORTED
    assert resume["sum-2"].status is ClaimStatus.UNSUPPORTED
    assert [i.code for i in resume["sum-2"].issues] == ["personal_statement_placement"]


def test_not_allowed_in_bullets_or_skills() -> None:
    snap = _snap()
    data = drafting.assemble_resume(snap, TODAY).model_dump(mode="json")
    for section in data["sections"]:
        if section["type"] == "experience":
            section["items"][0]["bullets"][0]["evidence_ids"].append(ABOUT_ME_ID)
        if section["type"] == "skills":
            section["skills"][0]["evidence_ids"].append(ABOUT_ME_ID)
    claims = verify(ResumeDocument.model_validate(data), _ctx(snap)).claims
    personal = [c for c in claims if ABOUT_ME_ID in c.evidence]
    assert len(personal) == 2
    assert all("personal_statement_placement" in {i.code for i in c.issues} for c in personal)


# --- Citation and support -----------------------------------------------------------------


def test_needs_an_about_me() -> None:
    snap = _snap(about_me=None)
    claim = _result(_letter(snap, _personal(CALM)), snap)["p1-s1"]
    assert claim.status is ClaimStatus.UNSUPPORTED
    assert [i.message for i in claim.issues] == ["Cites your about-me, which is empty"]


def test_a_verbatim_sentence_needs_no_llm_and_no_confirmed_fact() -> None:
    snap = _snap()
    claim = _result(_letter(snap, _personal(CALM)), snap)["p1-s1"]
    assert claim.status is ClaimStatus.SUPPORTED
    assert claim.evidence == {ABOUT_ME_ID: about_me_hash(ABOUT_ME)}


def test_rewording_is_judged_against_the_about_me() -> None:
    snap = _snap()
    text = "I like making fragile systems calm and observable."
    letter = _letter(snap, _personal(text))
    ctx = _ctx(snap, semantic=True)
    result = verify(letter, ctx)
    assert result.claims[0].status is ClaimStatus.NEEDS_REVIEW
    [request] = semantic_requests(result, letter, ctx)
    assert request.sources == (ABOUT_ME,)
    key = result.claims[0].semantic_key
    assert _result(letter, snap, semantic=True, verdicts={key: Verdict.ENTAILED})[
        "p1-s1"
    ].status is (ClaimStatus.SUPPORTED)
    rejected = _result(letter, snap, semantic=True, verdicts={key: Verdict.NOT_ENTAILED})
    assert rejected["p1-s1"].status is ClaimStatus.UNSUPPORTED
    # Without an LLM a reworded statement cannot be checked.
    assert _result(letter, snap)["p1-s1"].status is ClaimStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("I have shipped software for 10 years.", "10"),
        ("I have spent a decade on calm systems.", "decade"),
        ("Since March I have focused on calm systems.", "March"),
        ("I enjoy calm Python systems.", "python"),
        ("As a lead I keep systems calm.", "lead"),
        ("I keep systems calm at Initech.", "Initech"),
        ("PMP training taught me to keep systems calm.", "PMP"),
    ],
    ids=["number", "duration", "date", "skill", "title", "employer", "credential"],
)
def test_each_checkable_detail_needs_a_cited_confirmed_fact(text: str, detail: str) -> None:
    snap = _snap()
    claim = _result(_letter(snap, _personal(text)), snap)["p1-s1"]
    assert claim.status is ClaimStatus.UNSUPPORTED
    assert detail in claim.details
    assert "unsupported_detail" in {i.code for i in claim.issues}


def test_a_detail_a_cited_confirmed_fact_states_is_allowed() -> None:
    snap = _snap()
    text = "I enjoy calm Python systems."
    claim = _result(_letter(snap, _personal(text, SKILL_PY)), snap, semantic=True)["p1-s1"]
    assert "unsupported_detail" not in {i.code for i in claim.issues}
    # An unconfirmed fact does not count for details.
    unconfirmed = _snap(fact_list=facts(**{str(SKILL_PY): {"confirmed": False}}))
    claim = _result(_letter(unconfirmed, _personal(text, SKILL_PY)), unconfirmed)["p1-s1"]
    assert claim.details == ["python"]


def test_cited_facts_must_be_confirmed() -> None:
    unconfirmed = _snap(fact_list=facts(**{str(EXP): {"confirmed": False}}))
    claim = _result(_letter(unconfirmed, _personal(CALM, EXP)), unconfirmed)["p1-s1"]
    assert claim.status is ClaimStatus.NEEDS_REVIEW
    assert [i.code for i in claim.issues] == ["unconfirmed_fact"]


def test_the_target_job_may_be_named() -> None:
    assert (
        checkable_details(
            "I would enjoy calm systems at Acme Logistics (fictional).",
            ["Acme Logistics (fictional)"],
        )
        == set()
    )


def test_a_changed_about_me_flags_approval() -> None:
    snap = _snap()
    claims = [c.as_dict() for c in verify(_letter(snap, _personal(CALM)), _ctx(snap)).claims]
    facts_now = {f.id: f for f in snap.facts}
    assert stale_evidence(claims, facts_now, about_me_hash(ABOUT_ME)) == []
    assert stale_evidence(claims, facts_now, about_me_hash("Something else.")) == ["p1-s1"]
    assert stale_evidence(claims, facts_now, None) == ["p1-s1"]


# --- Drafting -----------------------------------------------------------------------------


def test_failed_statements_are_removed_and_about_me_facts_become_questions() -> None:
    snap = _snap()
    letter = _letter(
        snap,
        _personal(CALM, claim_id="p1-s1"),
        _personal("I have led teams for 10 years.", claim_id="p1-s2"),
        _personal("I won a national award.", claim_id="p1-s3"),
    )
    result = verify(letter, _ctx(snap))
    pruned, questions = drafting.prune_personal(letter, result, ABOUT_ME, DocumentKind.COVER_LETTER)
    assert [c.claim_id for _, c in iter_claims(pruned)] == ["p1-s1"]
    # "10" is in the about-me: ask. The award is not anywhere: just remove it.
    assert len(questions) == 1
    assert "I have spent 10 years leading teams at Initech." in questions[0].question
    assert questions[0].answer_kind == "career_fact" and questions[0].key.startswith("about-me:")


def test_an_empty_summary_is_removed() -> None:
    snap = _snap()
    resume = _resume(snap, _personal("I have led teams for 10 years.", claim_id="sum-1"))
    pruned, _ = drafting.prune_personal(
        resume, verify(resume, _ctx(snap)), ABOUT_ME, DocumentKind.RESUME
    )
    assert isinstance(pruned, ResumeDocument)
    assert not any(isinstance(s, SummarySection) for s in pruned.sections)


def _gen_ctx(provider: Any) -> generation.GenerationContext:
    return generation.GenerationContext(
        factory=None,  # type: ignore[arg-type]
        llm=LLMGateway(provider),
        no_llm_reason=None,
        lease=timedelta(minutes=1),
        max_attempts=1,
        now=lambda: datetime.now(UTC),
    )


def test_drafting_sends_the_about_me_and_keeps_supported_statements() -> None:
    snap = _snap()
    letter_out = json.loads(json.dumps(LETTER_OUTPUT))
    letter_out["paragraphs"][1]["sentences"] += [
        {"text": CALM, "evidence_ids": [ABOUT_ME_ID], "connective": False},
        {
            "text": "I have led teams for 10 years.",
            "evidence_ids": ["about:me"],
            "connective": False,
        },
    ]
    provider = FakeProvider(_resume_output(), letter_out)
    draft = drafting.draft(snap, {}, TODAY, LLMGateway(provider))
    for _, user in provider.calls:
        assert "<about_me>" in user and "calm, observable" in user
        assert "Backend engineer" not in user  # no headline (none set) and no contact
        assert "alex@example.invalid" not in user

    # No verdicts: anything not verbatim stays unchecked, and is asked about again after
    # pruning (the second reply).
    judge = FakeProvider({"judgements": []}, {"judgements": []})
    document, checked, questions = generation.check_drafted(
        _gen_ctx(judge), draft.letter, DocumentKind.COVER_LETTER, snap, {}, {}
    )
    texts = [c.text for _, c in iter_claims(document)]
    assert CALM in texts and "I have led teams for 10 years." not in texts
    assert [q.key.split(":")[0] for q in questions] == ["about-me"]
    assert all(c.claim_id != "p2-s3" for c in checked.result.claims)


def test_the_deterministic_path_ignores_the_about_me() -> None:
    snap = _snap()
    draft = drafting.draft(snap, {}, TODAY, None, "No language model is configured")
    for doc in (draft.resume, draft.letter):
        assert not any(c.personal for _, c in iter_claims(doc))
