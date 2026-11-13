"""Profile data in documents (ADR 0009): headline, display details, excluded facts."""

from datetime import date
from typing import Any

from next_role.candidates.models import FactType
from next_role.documents import display, docx_renderer, drafting, qa, templates
from next_role.documents.schemas import (
    CertificationsSection,
    ProjectsSection,
    ResumeDocument,
    StyleSettings,
)
from next_role.documents.service import verification_context
from next_role.documents.snapshot import RequirementSnapshot
from next_role.documents.verification import verify
from next_role.documents.workflow import ClaimStatus, VerificationOutcome
from next_role.matching.engine import MatchStatus
from tests.unit.document_fixtures import CERT, PROJECT, REQ_K8S, fact, facts, snapshot

TODAY = date(2026, 10, 22)
CRED_URL = "https://example.invalid/verify/abc"
PROJECT_URL = "https://example.invalid/route-planner"


def _snapshot(**changes: Any) -> Any:
    extra = [
        fact(
            CERT,
            FactType.CERTIFICATION,
            "Cloud Practitioner (fictional)",
            organization="Example Cloud",
            start_date=date(2024, 1, 1),
            attributes={"credential_id": "ABC-123", "credential_url": CRED_URL},
        ),
        fact(
            PROJECT,
            FactType.PROJECT,
            "Route planner (fictional)",
            description="Plans delivery routes.",
            attributes={"url": PROJECT_URL},
        ),
    ]
    snap = snapshot([*facts(), *extra])
    candidate = snap.candidate.model_copy(update={"headline": "Backend engineer", **changes})
    return snap.model_copy(update={"candidate": candidate})


def _check(doc: Any, snap: Any) -> Any:
    ctx = verification_context({f.id: f for f in snap.facts}, {}, drafting.target_of(snap), False)
    return verify(doc, ctx)


def _section(doc: ResumeDocument, kind: type) -> Any:
    return next(s for s in doc.sections if isinstance(s, kind))


def test_headline_and_details_are_copied_by_code_and_verify() -> None:
    snap = _snapshot()
    resume = drafting.assemble_resume(snap, TODAY)
    letter = drafting.assemble_letter(snap, {})
    assert resume.header.headline == letter.header.headline == "Backend engineer"
    cert = _section(resume, CertificationsSection).items[0]
    assert (cert.credential_id, cert.credential_url) == ("ABC-123", CRED_URL)
    assert _section(resume, ProjectsSection).items[0].url == PROJECT_URL
    assert _check(resume, snap).outcome is VerificationOutcome.PASSED


def test_changed_details_are_protected_fields() -> None:
    snap = _snapshot()
    resume = drafting.assemble_resume(snap, TODAY)
    data = resume.model_dump(mode="json")
    for section in data["sections"]:
        for item in section.get("items", []):
            if "credential_url" in item:
                item["credential_url"] = "https://attacker.invalid/"
            if "url" in item:
                item["url"] = "https://attacker.invalid/"
    result = _check(ResumeDocument.model_validate(data), snap)
    issues = {i.message for c in result.claims for i in c.issues}
    assert "The credential link differs from your profile" in issues
    assert "The link differs from your profile" in issues


def test_documents_drafted_without_details_still_verify() -> None:
    snap = _snapshot()
    data = drafting.assemble_resume(snap, TODAY).model_dump(mode="json")
    data["header"].pop("headline")
    for section in data["sections"]:
        for item in section.get("items", []):
            item.pop("url", None)
            item.pop("credential_id", None)
            item.pop("credential_url", None)
    result = _check(ResumeDocument.model_validate(data), snap)
    assert all(c.status is ClaimStatus.SUPPORTED for c in result.claims)


def test_headline_and_details_render_in_every_template_and_in_docx() -> None:
    snap = _snapshot()
    resume = drafting.assemble_resume(snap, TODAY)
    for template_id in ("clean", "modern-deedy", "modern"):
        settings = StyleSettings(template_id=template_id)
        shown = display.build(resume, settings)
        texts = shown.texts()
        assert texts[:2] == ["Alex Example", "Backend engineer"]
        assert {"Credential ID ABC-123", CRED_URL, PROJECT_URL} <= set(texts)
        tokens = templates.resolve_tokens(settings)
        data = docx_renderer.render(shown, tokens)
        assert qa.check_docx(shown, data, tokens).passed


def test_headline_and_details_are_never_sent_to_the_llm() -> None:
    snap = _snapshot()
    user = drafting._user(snap, {})
    for text in ("Backend engineer", "ABC-123", CRED_URL, PROJECT_URL):
        assert text not in user


def test_requirements_only_excluded_facts_address_are_not_asked_about() -> None:
    snap = _snapshot()
    gap = next(r for r in snap.job.requirements if r.id == REQ_K8S)
    excluded = RequirementSnapshot(
        **{**gap.model_dump(), "status": MatchStatus.UNKNOWN, "excluded_only": True}
    )
    job = snap.job.model_copy(update={"requirements": [excluded]})
    snap = snap.model_copy(update={"job": job})
    assert not [q for q in drafting.questions_for(snap, {}) if q.key.startswith("requirement:")]
    assert drafting._job_payload(snap)["requirements"] == []
