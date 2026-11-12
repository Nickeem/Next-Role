"""Models the configured OpenAI key can use, filtered to the catalog and cached in the API.

``GET /v1/models`` is called at most once per ``ttl`` (an hour). If a refresh fails, the last
listing is served with ``stale=True``; with no earlier listing, the result says why (a
redacted HTTP status or error type), so the Settings page keeps working either way. After a
failure the API is not asked again for ``retry`` seconds.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from next_role.integrations.llm.base import LLMError, describe_error
from next_role.integrations.llm.catalog import CATALOG, CatalogModel, Tier, lookup

logger = logging.getLogger(__name__)

CACHE_SECONDS = 3600.0
RETRY_SECONDS = 60.0

ModelLister = Callable[[], list[str]]


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    entry: CatalogModel


@dataclass(frozen=True)
class Listing:
    models: list[DiscoveredModel] = field(default_factory=list)
    fetched_at: datetime | None = None
    # True when the last refresh failed and an older listing is served.
    stale: bool = False
    # Why no listing is available.
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.fetched_at is not None

    def get(self, model_id: str) -> DiscoveredModel | None:
        return next((m for m in self.models if m.id == model_id), None)


def catalogued(
    listed: list[str], catalog: tuple[CatalogModel, ...] = CATALOG
) -> list[DiscoveredModel]:
    """The listed ids that are in the catalog and support strict JSON-schema output, fast
    models first, then by id. Anything else the key can use is hidden."""
    out = []
    for model_id in sorted(set(listed)):
        entry = lookup(model_id, catalog)
        if entry is not None and entry.strict_json_schema:
            out.append(DiscoveredModel(model_id, entry))
    return sorted(out, key=lambda m: (m.entry.tier is not Tier.FAST, m.id))


class ModelDiscovery:
    def __init__(
        self,
        lister: ModelLister,
        catalog: tuple[CatalogModel, ...] = CATALOG,
        ttl: float = CACHE_SECONDS,
        retry: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._lister = lister
        self._catalog = catalog
        self._ttl = ttl
        self._retry = retry
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._listing: Listing | None = None
        self._next_check = 0.0

    def listing(self) -> Listing:
        with self._lock:
            now = self._clock()
            if self._listing is not None and now < self._next_check:
                return self._listing
            try:
                models = catalogued(self._lister(), self._catalog)
            except LLMError as exc:
                logger.warning("llm model listing failed", extra={"error": describe_error(exc)})
                previous = self._listing
                self._listing = (
                    Listing(previous.models, previous.fetched_at, stale=True)
                    if previous is not None and previous.available
                    else Listing(error=describe_error(exc))
                )
                self._next_check = now + self._retry
                return self._listing
            self._listing = Listing(models, self._now())
            self._next_check = now + self._ttl
            return self._listing
