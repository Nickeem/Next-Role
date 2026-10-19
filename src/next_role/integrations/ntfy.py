"""Publishing to a self-hosted ntfy server over HTTP (https://docs.ntfy.sh/publish/)."""

import httpx

from next_role.config import Settings
from next_role.notifications.publisher import OutgoingNotification, PublishError


class NtfyPublisher:
    """POSTs a plain-text message to one configured topic with a bearer token.

    Timeouts, connection errors, 429, and 5xx are retryable. Other 4xx responses (bad
    token, missing permission, unknown topic) are permanent until the configuration changes.
    """

    name = "ntfy"

    def __init__(
        self,
        base_url: str,
        topic: str,
        token: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._topic = topic
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            follow_redirects=False,
        )

    def publish(self, notification: OutgoingNotification) -> None:
        headers = {"Title": notification.title, "Priority": str(notification.priority)}
        if notification.tags:
            headers["Tags"] = ",".join(notification.tags)
        if notification.click_url:
            headers["Click"] = notification.click_url
        try:
            response = self._client.post(
                f"/{self._topic}",
                content=notification.message.encode(),
                headers={**headers, "Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.TimeoutException as exc:
            raise PublishError("ntfy request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise PublishError(
                f"ntfy request failed: {type(exc).__name__}", retryable=True
            ) from exc
        status = response.status_code
        if 200 <= status < 300:
            return
        retryable = status == 429 or status >= 500
        raise PublishError(f"ntfy returned HTTP {status}", retryable=retryable)

    def close(self) -> None:
        self._client.close()


def build_ntfy_publisher(settings: Settings) -> NtfyPublisher | None:
    """The configured publisher, or None when ntfy is disabled (the default)."""
    if not settings.ntfy_enabled:
        return None
    if not settings.ntfy_base_url or not settings.ntfy_topic or settings.ntfy_token is None:
        raise ValueError("ntfy base URL, topic, and token must be configured")  # also in Settings
    return NtfyPublisher(
        settings.ntfy_base_url,
        settings.ntfy_topic,
        settings.ntfy_token.get_secret_value(),
        settings.ntfy_timeout_seconds,
    )
