"""ntfy adapter, its configuration, and the retry backoff."""

import random
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from next_role.config import Settings
from next_role.integrations.ntfy import NtfyPublisher, build_ntfy_publisher
from next_role.notifications.dispatcher import DispatchPolicy
from next_role.notifications.publisher import OutgoingNotification, PublishError

TOKEN = "tk_secret_value"
NOTE = OutgoingNotification(
    title="Review ready",
    message="A role is ready for your review.",
    priority=3,
    tags=["briefcase"],
    click_url="https://resume.example.internal/reviews/abc",
)


def _publisher(handler: httpx.MockTransport) -> NtfyPublisher:
    return NtfyPublisher("https://ntfy.example.internal/", "resume-review", TOKEN, 5, handler)


def test_publish_sends_authenticated_plain_text_request() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})

    _publisher(httpx.MockTransport(handle)).publish(NOTE)
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "https://ntfy.example.internal/resume-review"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Title"] == "Review ready"
    assert request.headers["Priority"] == "3"
    assert request.headers["Tags"] == "briefcase"
    assert request.headers["Click"] == "https://resume.example.internal/reviews/abc"
    assert request.headers["Content-Type"].startswith("text/plain")
    assert request.content == b"A role is ready for your review."


def test_click_header_omitted_without_public_url() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    _publisher(httpx.MockTransport(handle)).publish(
        OutgoingNotification(title="t", message="m", click_url=None)
    )
    assert "Click" not in seen[0].headers
    assert "Tags" not in seen[0].headers


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(401, False), (403, False), (404, False), (413, False), (429, True), (500, True), (502, True)],
)
def test_http_errors_are_classified(status: int, retryable: bool) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text=f"body {TOKEN}"))
    with pytest.raises(PublishError) as info:
        _publisher(transport).publish(NOTE)
    assert info.value.retryable is retryable
    assert info.value.detail == f"ntfy returned HTTP {status}"
    assert TOKEN not in info.value.detail


@pytest.mark.parametrize(
    ("exc", "detail"),
    [
        (httpx.ReadTimeout("slow"), "ntfy request timed out"),
        (httpx.ConnectError(f"refused {TOKEN}"), "ntfy request failed: ConnectError"),
    ],
)
def test_transport_errors_are_retryable_and_redacted(exc: Exception, detail: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(PublishError) as info:
        _publisher(httpx.MockTransport(handle)).publish(NOTE)
    assert info.value.retryable is True
    assert info.value.detail == detail


def test_redirects_are_not_followed() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"})
    )
    with pytest.raises(PublishError, match="HTTP 302"):
        _publisher(transport).publish(NOTE)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"api_key": SecretStr("k"), "_env_file": None, **overrides}
    return Settings(**values)  # type: ignore[arg-type]


def test_ntfy_disabled_by_default() -> None:
    assert build_ntfy_publisher(_settings()) is None


def test_ntfy_requires_url_topic_and_token() -> None:
    with pytest.raises(ValidationError, match="NTFY_TOKEN"):
        _settings(ntfy_enabled=True, ntfy_base_url="https://n.example", ntfy_topic="t")


def test_ntfy_requires_https_unless_allowed() -> None:
    common = {
        "ntfy_enabled": True,
        "ntfy_base_url": "http://ntfy:80",
        "ntfy_topic": "resume-review",
        "ntfy_token": SecretStr(TOKEN),
    }
    with pytest.raises(ValidationError, match="https"):
        _settings(**common)
    assert build_ntfy_publisher(_settings(**common, ntfy_allow_http=True)) is not None
    with pytest.raises(ValidationError, match="https"):
        _settings(**{**common, "ntfy_base_url": "file:///etc/passwd"}, ntfy_allow_http=True)


def test_ntfy_topic_is_restricted() -> None:
    with pytest.raises(ValidationError):
        _settings(ntfy_topic="../admin")


def test_backoff_grows_with_jitter_and_cap() -> None:
    policy = DispatchPolicy(base_delay=timedelta(seconds=30), max_delay=timedelta(minutes=10))
    rng = random.Random(1)  # noqa: S311
    for attempts, full in [(1, 30), (2, 60), (3, 120), (6, 600), (20, 600), (100, 600)]:
        delay = policy.backoff(attempts, rng).total_seconds()
        assert full / 2 <= delay <= full
