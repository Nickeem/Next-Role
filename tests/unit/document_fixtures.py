"""Synthetic generation inputs for document unit tests (no real people or employers)."""

import uuid
from datetime import date
from typing import Any

from next_role.candidates.models import FactType, VerificationStatus
from next_role.candidates.schemas import CandidateContact
from next_role.documents.schemas import sha256_json
from next_role.documents.snapshot import (
    CandidateSnapshot,
    FactSnapshot,
    InputSnapshot,
    JobSnapshot,
    RequirementSnapshot,
)
from next_role.matching.engine import MatchStatus

EXP = uuid.UUID("00000000-0000-4000-8000-000000000001")
ACH = uuid.UUID("00000000-0000-4000-8000-000000000002")
SKILL_PY = uuid.UUID("00000000-0000-4000-8000-000000000003")
EDU = uuid.UUID("00000000-0000-4000-8000-000000000004")
OLD_EXP = uuid.UUID("00000000-0000-4000-8000-000000000005")
PROJECT = uuid.UUID("00000000-0000-4000-8000-000000000006")
CERT = uuid.UUID("00000000-0000-4000-8000-000000000007")
REQ_PY = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
REQ_K8S = uuid.UUID("00000000-0000-4000-8000-0000000000a2")


def fact(
    fact_id: uuid.UUID,
    fact_type: FactType,
    title: str,
    *,
    confirmed: bool = True,
    **fields: Any,
) -> FactSnapshot:
    data: dict[str, Any] = {
        "id": fact_id,
        "fact_type": fact_type,
        "title": title,
        "organization": None,
        "description": None,
        "start_date": None,
        "end_date": None,
        "is_current": False,
        "skills": [],
        "attributes": {},
        "parent_fact_id": None,
        "verification_status": (
            VerificationStatus.USER_VERIFIED if confirmed else VerificationStatus.SELF_REPORTED
        ),
        "evidence_kinds": ["resume"],
    }
    data.update(fields)
    data["content_hash"] = sha256_json(
        {k: v for k, v in data.items() if k != "verification_status"}
    )
    return FactSnapshot(**data)


def facts(**overrides: dict[str, Any]) -> list[FactSnapshot]:
    base = {
        EXP: dict(
            fact_type=FactType.EXPERIENCE,
            title="Backend Engineer",
            organization="Northwind Freight (fictional)",
            start_date=date(2019, 3, 1),
            is_current=True,
            skills=["python", "postgresql", "docker"],
            attributes={
                "location": "Toronto, ON",
                "responsibilities": [
                    "Built REST APIs in Python for shipment tracking",
                    "Maintained PostgreSQL schemas and query performance",
                ],
            },
        ),
        ACH: dict(
            fact_type=FactType.ACHIEVEMENT,
            title="Cut p95 API latency by 40%",
            description="Cut p95 API latency by 40% by adding query caching.",
            parent_fact_id=EXP,
            skills=["python"],
            attributes={"metrics": ["40% lower p95 latency"]},
        ),
        SKILL_PY: dict(fact_type=FactType.SKILL, title="Python", skills=["python"]),
        EDU: dict(
            fact_type=FactType.EDUCATION,
            title="BSc Computer Science",
            organization="Example University (fictional)",
            end_date=date(2018, 5, 1),
            attributes={"degree_level": "bachelor", "field_of_study": "Computer Science"},
        ),
        OLD_EXP: dict(
            fact_type=FactType.EXPERIENCE,
            title="Junior Developer",
            organization="Contoso Labs (fictional)",
            start_date=date(2017, 1, 1),
            end_date=date(2019, 2, 1),
            skills=["javascript"],
            description="Wrote internal dashboards. Fixed reporting bugs.",
        ),
    }
    result = []
    for fact_id, fields in base.items():
        fields = {**fields, **overrides.get(str(fact_id), {})}
        fact_type = fields.pop("fact_type")
        title = fields.pop("title")
        confirmed = fields.pop("confirmed", True)
        result.append(fact(fact_id, fact_type, title, confirmed=confirmed, **fields))
    return result


def snapshot(
    fact_list: list[FactSnapshot] | None = None, description: str | None = None
) -> InputSnapshot:
    return InputSnapshot(
        assessment_id=uuid.uuid4(),
        tier="moderate",
        job=JobSnapshot(
            id=uuid.uuid4(),
            title="Senior Backend Engineer",
            company="Acme Logistics (fictional)",
            location="Remote",
            description=description or "We build logistics software in Python.",
            requirements=[
                RequirementSnapshot(
                    id=REQ_PY,
                    position=0,
                    text="5+ years of Python",
                    category="skill",
                    importance="required",
                    terms=["python"],
                    status=MatchStatus.MET,
                    evidence_fact_ids=[EXP],
                ),
                RequirementSnapshot(
                    id=REQ_K8S,
                    position=1,
                    text="Experience operating Kubernetes",
                    category="skill",
                    importance="required",
                    terms=["kubernetes"],
                    status=MatchStatus.NO_EVIDENCE,
                    evidence_fact_ids=[],
                ),
            ],
        ),
        candidate=CandidateSnapshot(
            id=uuid.uuid4(),
            full_name="Alex Example",
            location="Toronto, ON",
            contact=CandidateContact(email="alex@example.invalid"),
        ),
        facts=fact_list if fact_list is not None else facts(),
    )
