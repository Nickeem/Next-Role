from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.notifications import service
from next_role.notifications.schemas import (
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
)

router = APIRouter(tags=["notifications"])


@router.get("/notification-preferences")
def get_preferences(session: Session = Depends(get_session)) -> NotificationPreferencesOut:
    return service.preferences_out(service.get_preferences(session))


@router.patch("/notification-preferences")
def update_preferences(
    payload: NotificationPreferencesUpdate, session: Session = Depends(get_session)
) -> NotificationPreferencesOut:
    """Enable or disable each notification type. Takes effect for events queued afterwards."""
    return service.preferences_out(service.update_preferences(session, payload))
