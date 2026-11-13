"""rules-v2 fingerprint (ADR 0009): what makes an assessment run count as new."""

import json
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from next_role.candidates.service import get_candidate
from next_role.jobs.service import get_job
from next_role.matching.service import input_fingerprint

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


@pytest.fixture
def setup(client: TestClient) -> tuple[str, str, dict[str, Any]]:
    candidate = client.post("/api/v1/candidates", json=_load("candidate.json")).json()
    job = client.post("/api/v1/jobs", json=_load("jobs/senior-backend-engineer.json")).json()
    kubernetes = next(f for f in candidate["facts"] if f["title"] == "Kubernetes")
    return candidate["id"], job["job"]["id"], kubernetes


def _fingerprint(factory: sessionmaker[Session], cid: str, jid: str) -> str:
    with factory() as session:
        candidate = get_candidate(session, uuid.UUID(cid))
        job = get_job(session, uuid.UUID(jid))
        return input_fingerprint(candidate, job, date(2026, 10, 1))


Change = Callable[[TestClient, str, dict[str, Any]], Any]


def _evidence(client: TestClient, cid: str, fact: dict[str, Any]) -> Any:
    source = client.post(
        f"/api/v1/candidates/{cid}/evidence-sources",
        json={"kind": "certificate", "title": "CKA record (synthetic)"},
    ).json()
    return client.patch(
        f"/api/v1/candidates/{cid}/facts/{fact['id']}",
        json={"evidence_source_ids": [source["id"]]},
    )


@pytest.mark.parametrize(
    ("change", "changes_result"),
    [
        (lambda c, cid, f: c.patch(f"/api/v1/candidates/{cid}", json={"location": "Ottawa"}), True),
        (lambda c, cid, f: c.post(f"/api/v1/candidates/{cid}/facts/{f['id']}/verify"), True),
        (
            lambda c, cid, f: c.patch(
                f"/api/v1/candidates/{cid}/facts/{f['id']}",
                json={"description": "Ran clusters (synthetic)"},
            ),
            True,
        ),
        (_evidence, False),
        (
            lambda c, cid, f: c.patch(
                f"/api/v1/candidates/{cid}/facts/{f['id']}", json={"include_in_documents": False}
            ),
            False,
        ),
        (
            lambda c, cid, f: c.patch(
                f"/api/v1/candidates/{cid}", json={"about_me": "I like calm systems."}
            ),
            False,
        ),
    ],
    ids=["home_location", "confirmation", "content", "evidence", "inclusion", "about_me"],
)
def test_fingerprint_changes_only_with_inputs_matching_reads(
    client: TestClient,
    session_factory: sessionmaker[Session],
    setup: tuple[str, str, dict[str, Any]],
    change: Change,
    changes_result: bool,
) -> None:
    cid, jid, fact = setup
    before = _fingerprint(session_factory, cid, jid)
    assert change(client, cid, fact).status_code in (200, 201)
    assert (_fingerprint(session_factory, cid, jid) != before) is changes_result
