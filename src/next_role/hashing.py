"""Stable hashes of JSON-like data, shared by candidates, matching and documents."""

import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_json(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()
