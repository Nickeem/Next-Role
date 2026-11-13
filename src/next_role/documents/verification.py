"""Claim verification (specification §6): pure checks of a document against facts.

Order of checks for each claim:

1. Schema and completeness are enforced by the canonical models before this runs.
2. Protected facts: item fields equal the source fact's; every number in a claim, with its
   unit, appears in a cited fact.
3. Evidence: substantive claims cite known, active facts (or, in a cover letter, the
   candidate's own answers); bullets cite their item's fact or a fact under it; skills named
   in a claim are among the cited facts' skills or text.
4. Meaning: a claim that reproduces a cited statement verbatim is supported. Anything else
   needs an LLM entailment verdict; "uncertain" is ``needs_review``, "not entailed" is
   ``unsupported``. Without an LLM it is ``unsupported``.
5. Trust: a claim resting only on facts the candidate has not confirmed is ``needs_review``.

Personal statements (claims citing ``about:me``, ADR 0009) are checked differently: they may
sit in any cover-letter paragraph or, at most one, in the resume summary; the about-me must
exist and support them (verbatim or by the LLM check, with the about-me as a source); and
every checkable detail (``details.py``) must appear in a cited confirmed fact. They need no
confirmed fact unless they cite facts, which must then all be confirmed.

Only ``supported`` claims can be approved. The LLM check can only downgrade a claim that
the deterministic checks allow; it never overrides them.
"""

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from next_role.candidates.models import FactType
from next_role.documents.details import unsupported_details
from next_role.documents.schemas import (
    ABOUT_ME_ID,
    STATEMENT_PREFIX,
    CertificationItem,
    CertificationsSection,
    Claim,
    ClaimRef,
    CoverLetterDocument,
    Document,
    EducationItem,
    EducationSection,
    ExperienceItem,
    ExperienceSection,
    ProjectItem,
    ProjectsSection,
    ResumeDocument,
    iter_claims,
)
from next_role.documents.snapshot import FactSnapshot
from next_role.documents.text import (
    fact_segments,
    fact_text,
    is_verbatim,
    normalize,
    numbers,
    skills_in,
    year_month,
)
from next_role.documents.workflow import ClaimStatus, VerificationOutcome
from next_role.skills import normalize_skill

CHECK_VERSION = "checks-v1"

# Connective sentences may not carry facts: no numbers, no skills, and this long at most.
MAX_CONNECTIVE_CHARS = 220


class Verdict(StrEnum):
    """An LLM's judgement of one claim against its cited facts."""

    ENTAILED = "entailed"
    UNCERTAIN = "uncertain"
    NOT_ENTAILED = "not_entailed"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


@dataclass
class ClaimResult:
    claim_id: str
    path: str
    text: str
    status: ClaimStatus
    issues: list[Issue] = field(default_factory=list)
    # Cited fact ids with the content hash they were checked against.
    evidence: dict[str, str] = field(default_factory=dict)
    # Set when the claim needs (or had) an LLM verdict; the key caches that verdict.
    semantic_key: str | None = None
    verdict: Verdict | None = None
    # Personal statements: checkable details no cited confirmed fact states. Not stored.
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "path": self.path,
            "status": self.status.value,
            "issues": [i.as_dict() for i in self.issues],
            "evidence": self.evidence,
            "semantic_key": self.semantic_key,
            "verdict": self.verdict.value if self.verdict else None,
        }


@dataclass
class VerificationResult:
    outcome: VerificationOutcome
    claims: list[ClaimResult]
    issues: list[Issue]
    semantic_checked: bool

    def pending_semantic(self) -> list[ClaimResult]:
        return [c for c in self.claims if c.semantic_key and c.verdict is None]


@dataclass(frozen=True)
class SemanticRequest:
    """One claim the LLM is asked to judge, with the statements it may rely on."""

    key: str
    text: str
    connective: bool
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Context:
    facts: Mapping[uuid.UUID, FactSnapshot]
    # Letter-only answers by evidence id ("stmt:<clarification id>").
    statements: Mapping[str, str] = field(default_factory=dict)
    # Connective sentences allowed without an LLM (fixed templates, already filled in).
    connective_templates: frozenset[str] = frozenset()
    # Whether an LLM may judge claims that are not verbatim.
    semantic_available: bool = False
    # Earlier verdicts by semantic key, so unchanged claims are not judged again.
    verdicts: Mapping[str, Verdict] = field(default_factory=dict)
    # The job title and company, which connective sentences may name.
    target_phrases: tuple[str, ...] = ()
    # The about-me personal statements cite, and its hash for approval (ADR 0009).
    about_me: str | None = None
    about_me_hash: str | None = None


def semantic_key(text: str, sources: list[str], connective: bool) -> str:
    raw = "\x1f".join([CHECK_VERSION, str(connective), normalize(text), *sorted(sources)])
    return hashlib.sha256(raw.encode()).hexdigest()


def verify(document: Document, ctx: Context) -> VerificationResult:
    issues: list[Issue] = []
    is_letter = isinstance(document, CoverLetterDocument)
    claims = _item_results(document, ctx)
    personal_in_summary = 0
    for ref, claim in iter_claims(document):
        if claim.personal:
            in_summary = not is_letter and "/claims/" in ref.path
            personal_in_summary += in_summary
            allowed = is_letter or (in_summary and personal_in_summary == 1)
            claims.append(_check_personal(ref, claim, ctx, allowed))
        else:
            claims.append(_check_claim(ref, claim, ctx, is_letter))
    if isinstance(document, ResumeDocument) and not any(
        isinstance(s, ExperienceSection | ProjectsSection | EducationSection)
        for s in document.sections
    ):
        issues.append(Issue("empty_resume", "The resume has no experience, projects, or education"))
    blocked = any(i.blocking for i in issues) or any(
        c.status is not ClaimStatus.SUPPORTED for c in claims
    )
    return VerificationResult(
        outcome=VerificationOutcome.BLOCKED if blocked else VerificationOutcome.PASSED,
        claims=claims,
        issues=issues,
        semantic_checked=any(c.verdict is not None for c in claims),
    )


def semantic_requests(
    result: VerificationResult, document: Document, ctx: Context
) -> list[SemanticRequest]:
    """Claims still waiting for an LLM verdict, with the statements they may rely on."""
    by_id = {c.claim_id: c for _, c in iter_claims(document)}
    requests = []
    for pending in result.pending_semantic():
        if pending.semantic_key is None:  # pragma: no cover - pending_semantic filters these
            continue
        claim = by_id[pending.claim_id]
        requests.append(
            SemanticRequest(
                pending.semantic_key,
                claim.text,
                claim.connective,
                tuple(_sources(claim, ctx)),
            )
        )
    return requests


def stale_evidence(
    claims: list[dict[str, Any]],
    current: Mapping[uuid.UUID, FactSnapshot],
    about_me_hash: str | None = None,
) -> list[str]:
    """Claim ids whose cited facts were retired, edited, excluded or are not confirmed now,
    or that cite an about-me that changed since."""
    stale = []
    for claim in claims:
        for evidence_id, digest in claim.get("evidence", {}).items():
            if evidence_id == ABOUT_ME_ID:
                if digest != about_me_hash:
                    stale.append(claim["claim_id"])
                    break
                continue
            if evidence_id.startswith(STATEMENT_PREFIX):
                continue
            fact = current.get(uuid.UUID(evidence_id))
            if fact is None or fact.content_hash != digest or not fact.confirmed:
                stale.append(claim["claim_id"])
                break
    return stale


# --- Claims --------------------------------------------------------------------------------


def _sources(claim: Claim, ctx: Context) -> list[str]:
    sources: list[str] = []
    for evidence_id in claim.evidence_ids:
        if evidence_id == ABOUT_ME_ID:
            if ctx.about_me:
                sources.append(ctx.about_me)
        elif evidence_id.startswith(STATEMENT_PREFIX):
            if evidence_id in ctx.statements:
                sources.append(ctx.statements[evidence_id])
        elif (fact := _fact(evidence_id, ctx)) is not None:
            sources.extend(fact_segments(fact))
    return sources


def _fact(evidence_id: str, ctx: Context) -> FactSnapshot | None:
    try:
        return ctx.facts.get(uuid.UUID(evidence_id))
    except ValueError:
        return None


def _check_claim(ref: ClaimRef, claim: Claim, ctx: Context, is_letter: bool) -> ClaimResult:
    result = ClaimResult(claim.claim_id, ref.path, claim.text, ClaimStatus.SUPPORTED)
    if claim.connective:
        return _check_connective(result, claim, ctx)

    facts: list[FactSnapshot] = []
    statements: list[str] = []
    for evidence_id in claim.evidence_ids:
        if evidence_id.startswith(STATEMENT_PREFIX):
            if not is_letter or evidence_id not in ctx.statements:
                result.issues.append(
                    Issue("unknown_evidence", "Cites an answer that cannot be used here")
                )
            else:
                statements.append(ctx.statements[evidence_id])
                result.evidence[evidence_id] = ""
            continue
        fact = _fact(evidence_id, ctx)
        if fact is None:
            result.issues.append(
                Issue(
                    "unknown_evidence",
                    "Cites a fact that does not exist, was retired, or is kept off documents",
                )
            )
            continue
        facts.append(fact)
        result.evidence[evidence_id] = fact.content_hash

    if not claim.evidence_ids:
        result.issues.append(Issue("no_evidence", "Cites no supporting fact"))
    if ref.item is not None:
        # Every cited fact must be this entry's or sit under it; skills belong to no entry.
        source = ref.item.source_fact_id
        if any(
            f.id != source and f.parent_fact_id != source and f.fact_type is not FactType.SKILL
            for f in facts
        ):
            result.issues.append(
                Issue("misattributed", "Cites facts that do not belong to this entry")
            )

    support_text = " \n ".join([*(fact_text(f) for f in facts), *statements])
    missing_numbers = numbers(claim.text) - numbers(support_text)
    if missing_numbers:
        result.issues.append(
            Issue(
                "unsupported_number",
                "Numbers not found in the cited facts: " + ", ".join(sorted(missing_numbers)),
            )
        )

    is_skill_claim = ref.path.split("/")[-2] == "skills"
    if is_skill_claim:
        canonical = normalize_skill(claim.text)
        known = set().union(*(set(f.skills) | skills_in(fact_text(f)) for f in facts))
        if canonical not in known:
            result.issues.append(
                Issue("unsupported_skill", "The skill is not among the cited facts' skills")
            )
    else:
        known = set().union(*(set(f.skills) | skills_in(fact_text(f)) for f in facts))
        known |= set().union(*(skills_in(s) for s in statements))
        extra = skills_in(claim.text) - known
        if extra:
            result.issues.append(
                Issue(
                    "unsupported_skill",
                    "Skills not found in the cited facts: " + ", ".join(sorted(extra)),
                )
            )

    if result.issues:
        result.status = ClaimStatus.UNSUPPORTED
        return result

    sources = _sources(claim, ctx)
    if not is_skill_claim and not is_verbatim(claim.text, sources):
        _apply_semantic(result, semantic_key(claim.text, sources, False), ctx)
        if result.status is not ClaimStatus.SUPPORTED:
            return result

    if facts and not statements and not any(f.confirmed for f in facts):
        result.status = ClaimStatus.NEEDS_REVIEW
        result.issues.append(
            Issue(
                "unconfirmed_fact",
                "Rests on facts you have not confirmed yet; confirm them to approve",
            )
        )
    return result


def _check_personal(ref: ClaimRef, claim: Claim, ctx: Context, allowed: bool) -> ClaimResult:
    result = ClaimResult(claim.claim_id, ref.path, claim.text, ClaimStatus.SUPPORTED)
    if claim.connective:
        result.issues.append(Issue("connective_with_evidence", "Connective text cites evidence"))
    if not allowed:
        result.issues.append(
            Issue(
                "personal_statement_placement",
                "A sentence resting on your about-me can only be in the cover letter, or be "
                "the one such sentence in the resume summary",
            )
        )
    if ctx.about_me:
        result.evidence[ABOUT_ME_ID] = ctx.about_me_hash or ""
    else:
        result.issues.append(Issue("unknown_evidence", "Cites your about-me, which is empty"))

    facts: list[FactSnapshot] = []
    for evidence_id in claim.evidence_ids:
        if evidence_id == ABOUT_ME_ID:
            continue
        if evidence_id.startswith(STATEMENT_PREFIX):
            result.issues.append(
                Issue("unknown_evidence", "A personal statement cites your about-me and facts only")
            )
            continue
        fact = _fact(evidence_id, ctx)
        if fact is None:
            result.issues.append(
                Issue(
                    "unknown_evidence",
                    "Cites a fact that does not exist, was retired, or is kept off documents",
                )
            )
            continue
        facts.append(fact)
        result.evidence[evidence_id] = fact.content_hash

    # Anything checkable must come from a confirmed fact, never from the about-me alone.
    support = " \n ".join(fact_text(f) for f in facts if f.confirmed)
    titles = [f.title for f in ctx.facts.values()]
    missing = unsupported_details(claim.text, support, ctx.target_phrases, titles)
    if missing:
        result.details = missing
        result.issues.append(
            Issue(
                "unsupported_detail",
                "States details no cited confirmed fact contains: " + ", ".join(missing),
            )
        )
    if result.issues:
        result.status = ClaimStatus.UNSUPPORTED
        return result

    sources = _sources(claim, ctx)
    if not is_verbatim(claim.text, sources):
        _apply_semantic(result, semantic_key(claim.text, sources, False), ctx)
        if result.status is not ClaimStatus.SUPPORTED:
            return result
    if any(not f.confirmed for f in facts):
        result.status = ClaimStatus.NEEDS_REVIEW
        result.issues.append(
            Issue(
                "unconfirmed_fact",
                "Rests on facts you have not confirmed yet; confirm them to approve",
            )
        )
    return result


def _check_connective(result: ClaimResult, claim: Claim, ctx: Context) -> ClaimResult:
    if claim.evidence_ids:
        result.issues.append(Issue("connective_with_evidence", "Connective text cites evidence"))
    text = claim.text
    for phrase in ctx.target_phrases:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    if numbers(text) or skills_in(text) or len(claim.text) > MAX_CONNECTIVE_CHARS:
        result.issues.append(
            Issue("connective_states_facts", "Connective text may not state numbers or skills")
        )
    if result.issues:
        result.status = ClaimStatus.UNSUPPORTED
        return result
    if normalize(claim.text) in {normalize(t) for t in ctx.connective_templates}:
        return result
    _apply_semantic(result, semantic_key(claim.text, [], True), ctx)
    return result


def _apply_semantic(result: ClaimResult, key: str, ctx: Context) -> None:
    result.semantic_key = key
    verdict = ctx.verdicts.get(key)
    if verdict is None:
        if ctx.semantic_available:
            # Decided once the LLM has judged it; until then it cannot be approved.
            result.status = ClaimStatus.NEEDS_REVIEW
            result.issues.append(
                Issue("awaiting_check", "Waiting for the wording check", blocking=True)
            )
        else:
            result.status = ClaimStatus.UNSUPPORTED
            result.issues.append(
                Issue(
                    "not_verbatim",
                    "Not worded exactly as a fact, and no language model is configured to "
                    "check it. Edit it, or confirm it as a new fact.",
                )
            )
        return
    result.verdict = verdict
    if verdict is Verdict.UNCERTAIN:
        result.status = ClaimStatus.NEEDS_REVIEW
        result.issues.append(
            Issue(
                "uncertain_wording",
                "The wording may say more than the facts do. Edit it, or confirm it as a fact.",
            )
        )
    elif verdict is Verdict.NOT_ENTAILED:
        result.status = ClaimStatus.UNSUPPORTED
        result.issues.append(Issue("not_entailed", "The cited facts do not support this wording"))


# --- Items ---------------------------------------------------------------------------------


def _item_results(document: Document, ctx: Context) -> list[ClaimResult]:
    """Entries (jobs, projects, degrees, certifications) as results with id ``item-<id>``, so
    their source facts are tracked like a claim's evidence."""
    if isinstance(document, CoverLetterDocument):
        return []
    results = []
    for s, section in enumerate(document.sections):
        match section:
            case ExperienceSection():
                for i, item in enumerate(section.items):
                    results.append(
                        _protected(s, i, item, ctx, FactType.EXPERIENCE, _experience_fields)
                    )
            case ProjectsSection():
                for i, project in enumerate(section.items):
                    results.append(
                        _protected(s, i, project, ctx, FactType.PROJECT, _project_fields)
                    )
            case EducationSection():
                for i, education in enumerate(section.items):
                    results.append(
                        _protected(s, i, education, ctx, FactType.EDUCATION, _education_fields)
                    )
            case CertificationsSection():
                for i, cert in enumerate(section.items):
                    results.append(
                        _protected(s, i, cert, ctx, FactType.CERTIFICATION, _certification_fields)
                    )
            case _:
                pass
    return results


def _experience_fields(item: ExperienceItem, fact: FactSnapshot) -> dict[str, tuple[Any, Any]]:
    return {
        "organization": (item.organization, fact.organization),
        "position": (item.position, fact.title),
        "start date": (item.start_date, year_month(fact.start_date)),
        "end date": (item.end_date, year_month(fact.end_date)),
        "current": (item.is_current, fact.is_current),
        "location": (item.location, fact.attributes.get("location") if item.location else None),
    }


def _project_fields(item: ProjectItem, fact: FactSnapshot) -> dict[str, tuple[Any, Any]]:
    return {
        "name": (item.name, fact.title),
        "role": (item.role, fact.attributes.get("role") if item.role else None),
        "organization": (item.organization, fact.organization if item.organization else None),
        "start date": (item.start_date, year_month(fact.start_date)),
        "end date": (item.end_date, year_month(fact.end_date)),
        "current": (item.is_current, fact.is_current),
        # Display details: checked when shown, so documents drafted before they existed pass.
        "link": (item.url, fact.attributes.get("url") if item.url else None),
    }


def _education_fields(item: EducationItem, fact: FactSnapshot) -> dict[str, tuple[Any, Any]]:
    field_of_study = fact.attributes.get("field_of_study")
    return {
        "qualification": (item.qualification, fact.title),
        "institution": (item.institution, fact.organization if item.institution else None),
        "field of study": (item.field_of_study, field_of_study if item.field_of_study else None),
        "start date": (item.start_date, year_month(fact.start_date) if item.start_date else None),
        "end date": (item.end_date, year_month(fact.end_date) if item.end_date else None),
    }


def _certification_fields(
    item: CertificationItem, fact: FactSnapshot
) -> dict[str, tuple[Any, Any]]:
    return {
        "name": (item.name, fact.title),
        "issuer": (item.issuer, fact.organization if item.issuer else None),
        "issued": (item.issued, year_month(fact.start_date) if item.issued else None),
        "expires": (item.expires, year_month(fact.end_date) if item.expires else None),
        "credential ID": (
            item.credential_id,
            fact.attributes.get("credential_id") if item.credential_id else None,
        ),
        "credential link": (
            item.credential_url,
            fact.attributes.get("credential_url") if item.credential_url else None,
        ),
    }


def _protected(
    section_index: int,
    item_index: int,
    item: ExperienceItem | ProjectItem | EducationItem | CertificationItem,
    ctx: Context,
    fact_type: FactType,
    fields: Callable[[Any, FactSnapshot], dict[str, tuple[Any, Any]]],
) -> ClaimResult:
    result = ClaimResult(
        f"item-{item.id}",
        f"/sections/{section_index}/items/{item_index}",
        "",
        ClaimStatus.SUPPORTED,
    )
    fact = ctx.facts.get(item.source_fact_id)
    if fact is None or fact.fact_type is not fact_type:
        result.status = ClaimStatus.UNSUPPORTED
        result.issues.append(
            Issue("unknown_source", f"No matching {fact_type.value} fact in your profile")
        )
        return result
    result.text = fact.title
    result.evidence[str(fact.id)] = fact.content_hash
    for name, (shown, recorded) in fields(item, fact).items():
        if shown != recorded:
            result.issues.append(Issue("protected_field", f"The {name} differs from your profile"))
    if result.issues:
        result.status = ClaimStatus.UNSUPPORTED
    elif not fact.confirmed:
        result.status = ClaimStatus.NEEDS_REVIEW
        result.issues.append(
            Issue("unconfirmed_fact", "Comes from a fact you have not confirmed yet")
        )
    return result
