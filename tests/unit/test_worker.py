"""Worker heartbeat used by the Compose healthcheck."""

import os
import time
from pathlib import Path

import pytest

from next_role import worker
from next_role.config import Settings


def test_heartbeat_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    beat = tmp_path / "beat"
    monkeypatch.setattr(worker, "HEARTBEAT", beat)
    assert worker.healthy(60) is False
    beat.touch()
    assert worker.healthy(60) is True
    old = time.time() - 120
    os.utime(beat, (old, old))
    assert worker.healthy(60) is False


def test_health_threshold_covers_a_slow_publish() -> None:
    def settings(poll: float, timeout: float) -> Settings:
        return Settings(
            auth_disabled=True,
            worker_poll_seconds=poll,
            ntfy_timeout_seconds=timeout,
            _env_file=None,
        )

    assert worker.health_threshold(settings(10, 10)) == 60
    assert worker.health_threshold(settings(30, 10)) == 90
    # A publish that hits a 60 s timeout between heartbeats is not "stuck".
    assert worker.health_threshold(settings(10, 60)) == 140
