"""The transport boundary for notifications. Adapters live in ``next_role.integrations``."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OutgoingNotification:
    title: str
    message: str
    priority: int = 3
    tags: list[str] = field(default_factory=list)
    # Absolute link into the app, or None when no public URL is configured.
    click_url: str | None = None


class PublishError(Exception):
    """Publishing failed. ``detail`` is safe to store and display: no secrets or bodies."""

    def __init__(self, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


class NotificationPublisher(Protocol):
    name: str

    def publish(self, notification: OutgoingNotification) -> None:
        """Hand the notification to the transport. Raises PublishError on failure.

        Success means the server accepted it, not that a device displayed it.
        """
        ...
