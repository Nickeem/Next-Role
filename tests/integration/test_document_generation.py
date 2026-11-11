"""Document generation end to end over a real database: the worker drafts and checks
documents, and the API edits, regenerates, answers questions, and confirms claims."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from next_role.candidates.models import CandidateFact, VerificationStatus
from next_role.documents import generation, runs
from next_role.documents.models import DocumentBundle, DocumentRevision, GenerationRun
from next_role.documents.workflow import RunStatus
from next_role.integrations.llm.base import Completion, LLMError, LLMProvider, ReasoningEffort
from next_role.integrations.llm.gateway import LLMGateway, SessionRecorder
from next_role.integrations.llm.models import LLMCallEvent, LLMOutcome, LLMTask
from next_role.review.models import OpportunityReview
from tests.integration.test_document_runs import _approve, _decide
from tests.integration.test_review_workflow import _assess, _candidate, _job, _review
from tests.llm_fakes import completion


def _ctx(
    factory: sessionmaker[Session], provider: LLMProvider | None = None
) -> generation.GenerationContext:
    return generation.GenerationContext(
        factory=factory,
        llm=LLMGateway(provider, SessionRecorder(factory)) if provider else None,
        no_llm_reason=None if provider else "No language model is configured",
        lease=timedelta(minutes=10),
        max_attempts=3,
        now=lambda: datetime.now(UTC),
    )


def _drain(ctx: generation.GenerationContext) -> int:
    done = 0
    while generation.process_next(ctx):
        done += 1
    return done


@pytest.fixture
def review(client: TestClient, session: Session) -> dict[str, Any]:
    candidate_id = _candidate(client)
    job_id = _job(client)
    _assess(client, job_id, candidate_id)
    return {"id": _review(session, candidate_id, job_id).id, "candidate_id": candidate_id}


@pytest.fixture
def bundle(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> dict[str, Any]:
    _approve(session_factory, review["id"])
    assert _drain(_ctx(session_factory)) == 1
    [summary] = client.get(f"/api/v1/reviews/{review['id']}/documents").json()["bundles"]
    detail: dict[str, Any] = client.get(f"/api/v1/bundles/{summary['id']}").json()
    return detail


def _claims(revision: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["claim_id"]: c for c in revision["verification"]["claims"]}


def test_approval_drafts_both_documents_with_checks_questions_and_a_style(
    client: TestClient, session_factory: sessionmaker[Session], bundle: dict[str, Any]
) -> None:
    assert bundle["drafting_mode"] == "deterministic"
    assert bundle["drafting_notes"] == ["No language model is configured"]
    assert bundle["job"]["company"] == "Fabrikam Logistics (fictional)"
    resume, letter = bundle["resume"], bundle["cover_letter"]
    assert (resume["seq"], resume["created_by"], letter["seq"]) == (1, "system", 1)
    assert resume["content"]["header"]["contact"]["email"] == "alex@example.invalid"
    assert resume["verification"] and letter["verification"]
    assert bundle["style"]["template_id"] == "clean"
    assert bundle["style"]["tokens"]["body_pt"] >= 9.5
    assert [r["status"] for r in bundle["runs"]] == ["completed"]

    # The example profile has unconfirmed facts, so the bundle needs input before approval.
    statuses = {c["status"] for c in _claims(resume).values()}
    assert "needs_review" in statuses
    assert bundle["status"] == "needs_input"
    assert any(q["key"] == "motivation" for q in bundle["clarifications"])

    # Re-running the worker does nothing: the run is complete and idempotent.
    assert _drain(_ctx(session_factory)) == 0
    with session_factory() as session:
        assert session.scalar(select(DocumentBundle).where(DocumentBundle.id == bundle["id"]))
        assert len(session.scalars(select(DocumentRevision)).all()) == 2


def test_edit_creates_a_revision_that_is_checked_by_the_worker(
    client: TestClient, session_factory: sessionmaker[Session], bundle: dict[str, Any]
) -> None:
    content = bundle["resume"]["content"]
    experience = next(s for s in content["sections"] if s["type"] == "experience")
    bullet = experience["items"][0]["bullets"][0]
    bullet["text"] = "Shipped many great things for the business"
    url = f"/api/v1/bundles/{bundle['id']}/revisions"

    stale = client.post(url, json={"kind": "resume", "content": content, "expected_version": 0})
    assert stale.status_code == 409
    response = client.post(
        url, json={"kind": "resume", "content": content, "expected_version": bundle["version"]}
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    assert (revision["seq"], revision["created_by"], revision["verification"]) == (
        2,
        "operator",
        None,
    )
    same = client.post(
        url, json={"kind": "resume", "content": content, "expected_version": bundle["version"] + 1}
    )
    assert same.status_code == 422  # nothing changed

    assert _drain(_ctx(session_factory)) == 1
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    edited = _claims(detail["resume"])[bullet["claim_id"]]
    assert edited["status"] == "unsupported"
    assert [i["code"] for i in edited["issues"]] == ["not_verbatim"]
    assert detail["status"] == "needs_input"

    # The candidate attests the claim: it becomes a confirmed fact and is cited verbatim.
    confirm = client.post(
        f"/api/v1/bundles/{bundle['id']}/claims/{bullet['claim_id']}/confirm-as-fact",
        json={"kind": "resume", "expected_version": detail["version"]},
    )
    assert confirm.status_code == 201, confirm.text
    assert confirm.json()["note"] == "Claim confirmed as a fact"
    assert _drain(_ctx(session_factory)) == 1
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    assert _claims(detail["resume"])[bullet["claim_id"]]["status"] == "supported"
    with session_factory() as session:
        fact = session.scalar(
            select(CandidateFact).where(
                CandidateFact.title == "Shipped many great things for the business"
            )
        )
        assert fact is not None
        assert fact.verification_status is VerificationStatus.USER_VERIFIED
        assert fact.parent_fact_id == uuid.UUID(experience["items"][0]["source_fact_id"])
        assert [e.kind.value for e in fact.evidence_sources] == ["user_statement"]


def test_confirming_profile_facts_and_rechecking_unblocks_claims(
    client: TestClient,
    session_factory: sessionmaker[Session],
    bundle: dict[str, Any],
    review: dict[str, Any],
) -> None:
    pending = [c for c in _claims(bundle["resume"]).values() if c["status"] == "needs_review"]
    fact_ids = {fid for c in pending for fid in c["evidence"]}
    for fact_id in fact_ids:
        url = f"/api/v1/candidates/{review['candidate_id']}/facts/{fact_id}/verify"
        assert client.post(url).status_code == 200
    assert client.post(f"/api/v1/bundles/{bundle['id']}/verifications").status_code == 202
    assert _drain(_ctx(session_factory)) == 1
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    statuses = {c["claim_id"]: c["status"] for c in detail["resume"]["verification"]["claims"]}
    assert all(statuses[c["claim_id"]] == "supported" for c in pending)


def test_questions_are_answered_or_dismissed(
    client: TestClient, session_factory: sessionmaker[Session], bundle: dict[str, Any]
) -> None:
    questions = {q["key"]: q for q in bundle["clarifications"]}
    version = bundle["version"]

    material = next((q for q in questions.values() if q["material"]), None)
    if material is not None:
        url = f"/api/v1/clarifications/{material['id']}/dismiss"
        assert client.post(url, json={"expected_version": version}).status_code == 422
        response = client.post(url, json={"expected_version": version, "reason": "I don't"})
        assert response.status_code == 200
        version += 1

    motivation = questions["motivation"]
    response = client.post(
        f"/api/v1/clarifications/{motivation['id']}/answer",
        json={"answer": "I admire how your team ships routing tools.", "expected_version": version},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    again = client.post(
        f"/api/v1/clarifications/{motivation['id']}/answer",
        json={"answer": "x", "expected_version": version + 1},
    )
    assert again.status_code == 409

    # Regenerating the letter quotes the answer verbatim, citing it as a statement.
    response = client.post(
        f"/api/v1/bundles/{bundle['id']}/regenerations",
        json={
            "kind": "cover_letter",
            "section": "letter",
            "expected_version": version + 1,
            "idempotency_key": "regen-letter-1",
        },
    )
    assert response.status_code == 202, response.text
    assert _drain(_ctx(session_factory)) == 1
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    letter = detail["cover_letter"]
    assert (letter["seq"], letter["note"]) == (2, "Regenerated letter")
    texts = json.dumps(letter["content"])
    assert "I admire how your team ships routing tools." in texts
    stmt = _claims(letter)["why-1"]
    assert stmt["status"] == "supported"
    assert list(stmt["evidence"]) == [f"stmt:{motivation['id']}"]


def test_career_answers_become_unconfirmed_facts(
    client: TestClient, session_factory: sessionmaker[Session], bundle: dict[str, Any]
) -> None:
    metric = next(q for q in bundle["clarifications"] if q["key"].startswith("metric:"))
    response = client.post(
        f"/api/v1/clarifications/{metric['id']}/answer",
        json={"answer": "Reduced on-call pages by 30%", "expected_version": bundle["version"]},
    )
    assert response.status_code == 200
    fact_id = response.json()["proposed_fact_id"]
    with session_factory() as session:
        fact = session.get(CandidateFact, uuid.UUID(fact_id))
        assert fact is not None
        assert fact.verification_status is VerificationStatus.SELF_REPORTED
        assert str(fact.parent_fact_id) == metric["key"].split(":", 1)[1]


def test_regeneration_validates_sections_and_is_idempotent(
    client: TestClient, session_factory: sessionmaker[Session], bundle: dict[str, Any]
) -> None:
    url = f"/api/v1/bundles/{bundle['id']}/regenerations"
    body = {"kind": "resume", "section": "skills", "expected_version": bundle["version"]}
    assert (
        client.post(
            url, json={**body, "section": "letter", "idempotency_key": "k-00000001"}
        ).status_code
        == 422
    )
    first = client.post(url, json={**body, "idempotency_key": "k-00000002"})
    again = client.post(url, json={**body, "idempotency_key": "k-00000002"})
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    other = client.post(url, json={**body, "idempotency_key": "k-00000003"})
    assert other.status_code == 409  # one regeneration at a time
    assert _drain(_ctx(session_factory)) == 1
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    assert (detail["resume"]["seq"], detail["resume"]["note"]) == (2, "Regenerated skills")


def test_a_newer_assessment_stops_drafting(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    _approve(session_factory, review["id"])
    with session_factory() as session:
        row = session.get(OpportunityReview, review["id"])
        assert row is not None
        job_id = str(row.opportunity_id)
    _assess(client, job_id, review["candidate_id"], as_of="2026-10-09")
    assert _drain(_ctx(session_factory)) == 1
    with session_factory() as session:
        [run] = session.scalars(select(GenerationRun)).all()
        assert (run.status, run.last_error) == (RunStatus.FAILED, "stale_authorization")
        assert session.scalars(select(DocumentBundle)).all() == []


def test_a_run_cancelled_while_drafting_writes_nothing(
    session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    _approve(session_factory, review["id"])
    ctx = _ctx(session_factory)
    with session_factory.begin() as session:
        run = runs.claim_next(session, datetime.now(UTC), ctx.lease, 3)
        assert run is not None
        run_id, token = run.id, run.lease_token
        # Simulate the approval being revoked by an expired-lease cancellation.
        run.status = RunStatus.CANCELLED
    assert token is not None
    with pytest.raises(runs.LeaseLostError):
        generation.execute(ctx, run_id, token, run.intent)
    with session_factory() as session:
        assert session.scalars(select(DocumentBundle)).all() == []


def test_withdrawn_bundles_cannot_be_changed(
    client: TestClient,
    session_factory: sessionmaker[Session],
    bundle: dict[str, Any],
    review: dict[str, Any],
) -> None:
    _decide(session_factory, review["id"], action="reopen", reason="Not now")
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    assert detail["status"] == "withdrawn"
    response = client.post(
        f"/api/v1/bundles/{bundle['id']}/regenerations",
        json={
            "kind": "resume",
            "section": "skills",
            "expected_version": detail["version"],
            "idempotency_key": "k-00000009",
        },
    )
    assert response.status_code == 409


class _FakeLLM:
    """Writes bullets verbatim from the first responsibility and judges everything entailed."""

    name = "fake"
    model = "fake-model"
    external = False

    def __init__(self) -> None:
        self.judged = 0

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        model: str,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Completion:
        return completion(self.text(system, user, schema), model, reasoning_effort)

    def text(self, system: str, user: str, schema: dict[str, Any]) -> str:
        if "<claims>" in user:
            self.judged += 1
            payload = json.loads(user.split("<claims>\n", 1)[1].rsplit("\n</claims>", 1)[0])
            return json.dumps(
                {"judgements": [{"key": c["key"], "verdict": "entailed"} for c in payload]}
            )
        if "cover letter" in system:
            return json.dumps(
                {
                    "paragraphs": [
                        {
                            "sentences": [
                                {
                                    "text": "I am glad to apply.",
                                    "evidence_ids": [],
                                    "connective": True,
                                }
                            ]
                        }
                    ],
                    "questions": [],
                }
            )
        entries = json.loads(user.split("<entries>\n", 1)[1].rsplit("\n</entries>", 1)[0])
        items = [
            {
                "item_id": e["item_id"],
                "bullets": [
                    {
                        "text": f"Delivered work as {e['heading']}",
                        "evidence_ids": [e["source_fact_id"]],
                    }
                ],
            }
            for e in entries
        ]
        return json.dumps({"summary": [], "items": items, "skills": [], "questions": []})


def test_llm_drafting_is_checked_and_verdicts_are_reused(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    llm = _FakeLLM()
    _approve(session_factory, review["id"])
    assert _drain(_ctx(session_factory, llm)) == 1
    [summary] = client.get(f"/api/v1/reviews/{review['id']}/documents").json()["bundles"]
    detail = client.get(f"/api/v1/bundles/{summary['id']}").json()
    assert detail["drafting_mode"] == "llm"
    assert detail["resume"]["created_by"] == "llm"
    assert detail["resume"]["verification"]["semantic_checked"] is True
    rewritten = [c for c in _claims(detail["resume"]).values() if c["verdict"] == "entailed"]
    assert rewritten
    judged = llm.judged

    assert client.post(f"/api/v1/bundles/{summary['id']}/verifications").status_code == 202
    assert _drain(_ctx(session_factory, llm)) == 1
    assert llm.judged == judged  # unchanged claims are not sent to the model again


def test_drafting_provider_requires_the_candidate_data_opt_in(settings: Any) -> None:
    llm = _FakeLLM()
    provider, reason = generation.drafting_provider(settings, llm)
    assert provider is None and reason and "NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA" in reason
    allowed = settings.model_copy(update={"llm_allow_candidate_data": True})
    assert generation.drafting_provider(allowed, llm) == (llm, None)
    assert generation.drafting_provider(allowed, None)[0] is None


class _RepeatingLLM(_FakeLLM):
    """Asks the same question from both documents, with a topic reworded on every call, as
    a model at temperature 1 does."""

    QUESTION = "Have you led an on-call rotation?"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.prompts: list[str] = []

    def text(self, system: str, user: str, schema: dict[str, Any]) -> str:
        out = json.loads(super().text(system, user, schema))
        if "<claims>" in user:
            return json.dumps(out)
        self.calls += 1
        self.prompts.append(user)
        question = {"topic": f"on-call (wording {self.calls})", "question": self.QUESTION}
        out["questions"] = [{**question, "material": False}]
        return json.dumps(out)


def test_repeated_model_questions_are_asked_once(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    llm = _RepeatingLLM()
    _approve(session_factory, review["id"])
    assert _drain(_ctx(session_factory, llm)) == 1
    [summary] = client.get(f"/api/v1/reviews/{review['id']}/documents").json()["bundles"]
    detail = client.get(f"/api/v1/bundles/{summary['id']}").json()

    def asked() -> list[dict[str, Any]]:
        bundle = client.get(f"/api/v1/bundles/{summary['id']}").json()
        return [q for q in bundle["clarifications"] if q["question"] == llm.QUESTION]

    # The resume and the letter both asked it; it is stored once.
    [question] = asked()
    dismissed = client.post(
        f"/api/v1/clarifications/{question['id']}/dismiss",
        json={"reason": "Not relevant", "expected_version": detail["version"]},
    )
    assert dismissed.status_code == 200, dismissed.text

    # Regenerating asks it again under a new topic; the dismissed question is not reopened,
    # and the model was told it had already been asked.
    version = client.get(f"/api/v1/bundles/{summary['id']}").json()["version"]
    response = client.post(
        f"/api/v1/bundles/{summary['id']}/regenerations",
        json={
            "kind": "resume",
            "section": "skills",
            "expected_version": version,
            "idempotency_key": "regen-repeat-1",
        },
    )
    assert response.status_code == 202, response.text
    assert _drain(_ctx(session_factory, llm)) == 1
    assert [q["status"] for q in asked()] == ["dismissed"]
    assert llm.QUESTION in llm.prompts[-1].split("<asked_questions>", 1)[1]


class _JudgeDownLLM(_FakeLLM):
    def text(self, system: str, user: str, schema: dict[str, Any]) -> str:
        if "<claims>" in user:
            raise LLMError("fake returned HTTP 503")
        return super().text(system, user, schema)


def test_calls_are_recorded_and_a_failed_check_records_a_fallback(
    session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    _approve(session_factory, review["id"])
    # The first draft, then the verify run its deferred check queued. That run raises (it is
    # retried later) instead of falling back, so only the first run records fallbacks.
    assert _drain(_ctx(session_factory, _JudgeDownLLM())) == 2
    with session_factory() as s:
        events = s.scalars(select(LLMCallEvent).order_by(LLMCallEvent.created_at)).all()
    by_task = {(e.task, e.outcome) for e in events}
    assert (LLMTask.RESUME_DRAFTING, LLMOutcome.OK) in by_task
    assert (LLMTask.LETTER_DRAFTING, LLMOutcome.OK) in by_task
    assert (LLMTask.SEMANTIC_CHECK, LLMOutcome.PROVIDER_ERROR) in by_task
    # One deferred check per document.
    fallbacks = [e for e in events if e.outcome is LLMOutcome.DETERMINISTIC_FALLBACK]
    assert [(e.task, e.error) for e in fallbacks] == [
        (LLMTask.SEMANTIC_CHECK, "semantic check deferred (LLMError)")
    ] * 2
    with session_factory() as s:
        bundle = s.scalars(select(DocumentBundle)).one()
    assert bundle.drafting_notes[:2] == [
        "Resume: fake/fake-model (drafting-v2)",
        "Cover letter: fake/fake-model (drafting-v2)",
    ]
