"""Worker heartbeat used by the Compose healthcheck."""

import os
import time
from pathlib import Path

import pytest

from next_role import worker


def test_heartbeat_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    beat = tmp_path / "beat"
    monkeypatch.setattr(worker, "HEARTBEAT", beat)
    assert worker.healthy(60) is False
    beat.touch()
    assert worker.healthy(60) is True
    old = time.time() - 120
    os.utime(beat, (old, old))
    assert worker.healthy(60) is False
