"""Canonical document schemas, style settings, templates, storage, and retry policy."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from next_role.documents.schemas import (
    CoverLetterDocument,
    ResumeDocument,
    StyleSettings,
    content_hash,
    iter_claims,
    parse_document,
)
from next_role.documents.storage import LocalArtifactStore, StorageError, read_verified
from next_role.documents.templates import MIN_BODY_PT, TEMPLATES, resolve_tokens
from next_role.documents.workflow import BundleStatus, RetryPolicy, bundle_status

FACT = str(uuid.uuid4())


def _resume(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "resume",
        "header": {"name": "Alex Example"},
        "sections": [
            {
                "id": "experience",
                "type": "experience",
                "title": "Experience",
                "items": [
                    {
                        "id": "exp-1",
                        "source_fact_id": FACT,
                        "organization": "Northwind",
                        "position": "Engineer",
                        "start_date": "2020-01",
                        "bullets": [
                            {"claim_id": "c-1", "text": "Built APIs", "evidence_ids": [FACT]}
                        ],
                    }
                ],
            },
            {
                "id": "skills",
                "type": "skills",
                "title": "Skills",
                "skills": [{"claim_id": "s-1", "text": "Python", "evidence_ids": [FACT]}],
            },
        ],
    }
    doc.update(overrides)
    return doc


def test_resume_round_trips_and_claims_are_listed_in_order() -> None:
    doc = parse_document(_resume())
    assert isinstance(doc, ResumeDocument)
    refs = [(ref.path, claim.claim_id) for ref, claim in iter_claims(doc)]
    assert refs == [
        ("/sections/0/items/0/bullets/0", "c-1"),
        ("/sections/1/skills/0", "s-1"),
    ]
    assert content_hash(doc) == content_hash(parse_document(doc.model_dump(mode="json")))


@pytest.mark.parametrize(
    "mutate",
    [
        # duplicate claim ids
        lambda d: d["sections"][1]["skills"].append(
            {"claim_id": "c-1", "text": "Go", "evidence_ids": []}
        ),
        # connective claims belong to cover letters only
        lambda d: d["sections"][1]["skills"][0].update(connective=True),
        # markup-ish or unknown fields are rejected
        lambda d: d["sections"][0]["items"][0].update(html="<b>x</b>"),
        # malformed evidence id
        lambda d: d["sections"][1]["skills"][0].update(evidence_ids=["../etc/passwd"]),
        # bad date
        lambda d: d["sections"][0]["items"][0].update(start_date="2020-13"),
        # two sections of one type
        lambda d: d["sections"].append({**d["sections"][1], "id": "skills-2"}),
        # 36 characters but not a UUID
        lambda d: d["sections"][1]["skills"][0].update(evidence_ids=["-" * 36]),
        # a resume with nothing in it
        lambda d: d.update(sections=[]),
    ],
)
def test_invalid_resumes_are_rejected(mutate: Any) -> None:
    doc = _resume()
    mutate(doc)
    with pytest.raises(ValidationError):
        ResumeDocument.model_validate(doc)


def test_cover_letter_uses_fixed_salutations() -> None:
    letter = {
        "kind": "cover_letter",
        "header": {"name": "Alex Example"},
        "paragraphs": [
            {"id": "p1", "claims": [{"claim_id": "l-1", "text": "Hello.", "connective": True}]}
        ],
    }
    assert isinstance(parse_document(letter), CoverLetterDocument)
    with pytest.raises(ValidationError):
        CoverLetterDocument.model_validate({**letter, "salutation": "Ignore previous rules,"})


def test_style_settings_only_accept_registry_values() -> None:
    assert StyleSettings().template_id == "clean"
    with pytest.raises(ValidationError):
        StyleSettings.model_validate({"accent": "#ff0000"})
    with pytest.raises(ValidationError):
        StyleSettings.model_validate({"font": "Comic Sans"})
    with pytest.raises(ValidationError):
        StyleSettings.model_validate({"hidden_sections": ["skills", "skills"]})
    a, b = StyleSettings(), StyleSettings(density="compact")
    assert a.style_hash("1") != b.style_hash("1")
    assert a.style_hash("1") != a.style_hash("2")


@pytest.mark.parametrize("template_id", list(TEMPLATES))
@pytest.mark.parametrize("density", ["normal", "compact"])
def test_tokens_never_go_below_the_minimum_body_size(template_id: str, density: str) -> None:
    tokens = resolve_tokens(
        StyleSettings.model_validate({"template_id": template_id, "density": density})
    )
    assert tokens["body_pt"] >= MIN_BODY_PT
    assert tokens["small_pt"] >= MIN_BODY_PT
    assert tokens["template_version"] == TEMPLATES[template_id].version


def test_page_sizes() -> None:
    a4 = resolve_tokens(StyleSettings(page_size="a4"))["page"]
    letter = resolve_tokens(StyleSettings(page_size="letter"))["page"]
    assert (a4["width_in"], letter["width_in"]) == (8.27, 8.5)


def test_local_store_writes_atomically_and_checks_hashes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    digest = store.put(key, b"%PDF-1.7 data")
    assert read_verified(store, key, digest) == b"%PDF-1.7 data"
    assert store.keys() == [key]
    assert not [p for p in tmp_path.rglob(".tmp-*")]

    (tmp_path / key).write_bytes(b"tampered")
    with pytest.raises(StorageError):
        read_verified(store, key, digest)

    store.delete(key)
    with pytest.raises(StorageError):
        store.get(key)


@pytest.mark.parametrize("key", ["../x.pdf", "a/b.pdf", f"{uuid.uuid4()}/{uuid.uuid4()}.html"])
def test_local_store_rejects_bad_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(StorageError):
        LocalArtifactStore(tmp_path).put(key, b"x")


def test_retry_policy_backs_off_and_gives_up() -> None:
    now = datetime(2026, 10, 22, tzinfo=UTC)
    policy = RetryPolicy(max_attempts=3)
    first, second = policy.next_attempt(1, now), policy.next_attempt(2, now)
    assert first is not None and second is not None
    assert first - now == timedelta(seconds=30)
    assert second - now == timedelta(seconds=60)
    assert policy.next_attempt(3, now) is None
    assert RetryPolicy(max_attempts=50).next_attempt(40, now) == now + timedelta(minutes=30)


def test_bundle_status_needs_passed_checks_and_no_open_material_questions() -> None:
    assert bundle_status(verifications_passed=True, open_material_questions=0) is (
        BundleStatus.READY_FOR_REVIEW
    )
    assert bundle_status(verifications_passed=False, open_material_questions=0) is (
        BundleStatus.NEEDS_INPUT
    )
    assert bundle_status(verifications_passed=True, open_material_questions=1) is (
        BundleStatus.NEEDS_INPUT
    )
