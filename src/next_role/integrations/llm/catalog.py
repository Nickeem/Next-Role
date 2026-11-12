"""OpenAI models that may be chosen per task, and the reasoning effort each task uses.

The list is explicit and curated: a model the API lists but this table does not is never
offered, and is sent no ``reasoning_effort``. Each entry was checked against OpenAI's
documentation on 2026-11-11:

- Models and their efforts: https://developers.openai.com/api/docs/models and
  ``/models/gpt-6-luna``, ``/models/gpt-6.1-terra``, ``/models/gpt-6.1-sol``
- "Using GPT-6": https://developers.openai.com/api/docs/guides/latest-model ("When reasoning
  effort is not `none`, remove `temperature`, `top_p`, and `top_logprobs`"; "Use
  `reasoning.effort` in Responses or `reasoning_effort` in Chat Completions")
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/guides/structured-outputs

Structured Outputs: each model page lists Chat Completions as supported and
"structured_outputs" among its features, and the guide says Structured Outputs works in Chat
Completions. No page states ``strict`` ``json_schema`` on Chat Completions for these models in
so many words (its examples use Responses), hence ``strict_json_schema`` records our reading.

Only families are listed: OpenAI listed no dated snapshots for them, but a snapshot named
``<family>-YYYY-MM-DD`` would match its family.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.models import LLMTask

E = ReasoningEffort


class Tier(StrEnum):
    """A hint shown next to each model in Settings."""

    # Cheaper and faster; suited to extraction.
    FAST = "fast"
    # More capable; suited to drafting and the claim check.
    STRONG = "strong"


@dataclass(frozen=True)
class CatalogModel:
    family: str
    tier: Tier
    # Supported reasoning efforts, lowest first.
    efforts: tuple[ReasoningEffort, ...]
    # Supports ``response_format`` ``json_schema`` with ``strict: true`` on Chat Completions.
    strict_json_schema: bool

    @property
    def lowest_effort(self) -> ReasoningEffort:
        return self.efforts[0]


_ALL = (E.NONE, E.LOW, E.MEDIUM, E.HIGH, E.XHIGH, E.MAX)

CATALOG: tuple[CatalogModel, ...] = (
    # "Our most efficient model for focused, high-volume tasks." Default effort medium.
    CatalogModel("gpt-6-luna", Tier.FAST, _ALL, strict_json_schema=True),
    # "A fast, efficient model for visual reasoning, precise computer use, and clear, natural
    # writing." Tier strong here: it is the model this app recommends for drafting.
    CatalogModel("gpt-6.1-terra", Tier.STRONG, _ALL, strict_json_schema=True),
    # "Near-Astra performance for complex work at a lower cost." No ``none`` effort.
    CatalogModel("gpt-6.1-sol", Tier.STRONG, _ALL[1:], strict_json_schema=True),
)

# The effort each task uses unless one is chosen for it.
TASK_DEFAULT_EFFORT: dict[LLMTask, ReasoningEffort] = {
    LLMTask.EXTRACTION: E.NONE,
    LLMTask.RESUME_DRAFTING: E.MEDIUM,
    LLMTask.LETTER_DRAFTING: E.MEDIUM,
    LLMTask.SEMANTIC_CHECK: E.LOW,
}

# A dated snapshot of a family, e.g. ``gpt-6-luna-2026-05-18``.
_SNAPSHOT = re.compile(r"^(?P<family>.+)-\d{4}-\d{2}-\d{2}$")


def lookup(model_id: str, catalog: tuple[CatalogModel, ...] = CATALOG) -> CatalogModel | None:
    """The entry for an exact family id or one of its dated snapshots, else None.

    Matching is exact on purpose: ``gpt-6`` must not match ``gpt-6-luna``, nor
    ``gpt-6-luna`` match ``gpt-6-luna-mini``.
    """
    by_family = {m.family: m for m in catalog}
    if model_id in by_family:
        return by_family[model_id]
    snapshot = _SNAPSHOT.match(model_id)
    return by_family.get(snapshot["family"]) if snapshot else None


def effective_effort(
    model_id: str, requested: ReasoningEffort, catalog: tuple[CatalogModel, ...] = CATALOG
) -> ReasoningEffort | None:
    """The effort to send to ``model_id``: None for a model outside the catalog (no effort is
    sent), ``requested`` if the model supports it, else the model's lowest supported effort."""
    entry = lookup(model_id, catalog)
    if entry is None:
        return None
    return requested if requested in entry.efforts else entry.lowest_effort
