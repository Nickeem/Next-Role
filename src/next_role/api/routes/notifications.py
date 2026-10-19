from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from next_role.api.deps import get_app_settings, get_session
from next_role.config import Settings
from next_role.errors import ConflictError
from next_role.notifications import service
from next_role.notifications.schemas import (
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
    NtfyStatusOut,
    TestNotificationOut,
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


@router.get("/integrations/ntfy/status")
def ntfy_status(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> NtfyStatusOut:
    """Whether ntfy is configured, and delivery counts. Never discloses the topic or token."""
    delivery = service.delivery_status(session)
    return NtfyStatusOut(
        **delivery.model_dump(),
        enabled=settings.ntfy_enabled,
        server=urlsplit(settings.ntfy_base_url).hostname if settings.ntfy_base_url else None,
        click_links=settings.public_base_url is not None,
    )


@router.post(
    "/integrations/ntfy/test",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"description": "ntfy is not enabled"},
        429: {"description": "A test was queued in the last minute"},
    },
)
def send_test_notification(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> TestNotificationOut:
    """Queue a test notification. The worker publishes it on its next cycle."""
    if not settings.ntfy_enabled:
        raise ConflictError("ntfy is not enabled (NEXT_ROLE_NTFY_ENABLED=false)")
    row = service.queue_test(session, datetime.now(UTC))
    return TestNotificationOut(outbox_id=row.id, status=row.status)
