"""In-memory login throttle: attempts are counted atomically and the client map is bounded."""

import threading

import pytest

from next_role.auth.service import LoginThrottle
from next_role.errors import RateLimitedError


def test_attempts_over_the_limit_are_refused_until_success_clears_them() -> None:
    throttle = LoginThrottle(max_failures=3)
    for _ in range(3):
        throttle.attempt("10.0.0.1")
    with pytest.raises(RateLimitedError):
        throttle.attempt("10.0.0.1")
    throttle.attempt("10.0.0.2")  # other clients are unaffected
    throttle.succeeded("10.0.0.1")
    throttle.attempt("10.0.0.1")


def test_concurrent_attempts_cannot_exceed_the_limit() -> None:
    throttle = LoginThrottle(max_failures=5)
    allowed: list[int] = []
    barrier = threading.Barrier(20)

    def attempt() -> None:
        barrier.wait()
        try:
            throttle.attempt("10.0.0.1")
            allowed.append(1)
        except RateLimitedError:
            pass

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(allowed) == 5


def test_stale_clients_are_evicted_and_the_map_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr("next_role.auth.service.time.monotonic", lambda: clock[0])
    throttle = LoginThrottle(max_failures=5, window=300, max_clients=3)
    for client in ("a", "b", "c"):
        throttle.attempt(client)
    clock[0] += 301
    throttle.attempt("d")  # a, b, c are stale and dropped
    assert throttle.tracked_clients() == 1
    for client in ("e", "f", "g", "h"):
        throttle.attempt(client)
    assert throttle.tracked_clients() == 3
