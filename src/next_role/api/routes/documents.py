import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.documents import queries
from next_role.documents.api_schemas import ReviewDocumentsOut

router = APIRouter(tags=["documents"])


@router.get("/reviews/{review_id}/documents")
def review_documents(
    review_id: uuid.UUID, session: Session = Depends(get_session)
) -> ReviewDocumentsOut:
    """Generation runs and document bundles for a review, newest first."""
    return queries.review_documents(session, review_id)
