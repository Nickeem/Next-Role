"""The single entry point for LLM calls: call, validate, retry, and record what happened.

Every call site goes through ``LLMGateway.complete_model`` so each attempt, schema failure,
temperature drop, model fallback and deterministic fallback is recorded per ``LLMTask``. The
model and reasoning effort for each call come from the injected ``ModelResolver``, asked once
per call.

Recording is best-effort: a failure to record is logged and never affects the call.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from next_role.integrations.llm.base import (
    LLMError,
    LLMProvider,
    ReasoningEffort,
    RequestParams,
    describe_error,
)
from next_role.integrations.llm.models import LLMCallEvent, LLMOutcome, LLMTask
from next_role.integrations.llm.resolver import ModelResolver, Resolved, StaticResolver

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 500
# Set on the error ``complete_model`` raises: the model its last attempt used.
_MODEL_ATTR = "next_role_llm_model"


def failed_model(exc: BaseException) -> str | None:
    """The model the failed ``complete_model`` call last used, if ``exc`` came from one.

    It is carried on the exception, not stored on the gateway, because one gateway serves
    concurrent requests from the API's threadpool.
    """
    model = getattr(exc, _MODEL_ATTR, None)
    return model if isinstance(model, str) else None


def _stamp(exc: BaseException, model: str) -> None:
    setattr(exc, _MODEL_ATTR, model)


@dataclass(frozen=True)
class CallEvent:
    task: LLMTask
    outcome: LLMOutcome
    provider: str
    model: str | None
    attempt: int | None = None
    error: str | None = None
    params: dict[str, Any] | None = None
    latency_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventRecorder(Protocol):
    def record(self, event: CallEvent) -> None: ...


class SessionRecorder:
    """Writes each event in its own short transaction, separate from the caller's."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def record(self, event: CallEvent) -> None:
        with self._factory.begin() as session:
            session.add(
                LLMCallEvent(
                    task=event.task,
                    outcome=event.outcome,
                    provider=event.provider,
                    model=event.model,
                    attempt=event.attempt,
                    error=event.error,
                    params=event.params,
                    latency_ms=event.latency_ms,
                    created_at=event.created_at,
                )
            )


def _params(params: RequestParams | None) -> dict[str, Any] | None:
    return params.as_dict() if params else None


@dataclass(frozen=True)
class Completed[T: BaseModel]:
    """Validated output and the model and effort that produced it."""

    value: T
    model: str
    reasoning_effort: ReasoningEffort | None
    # Provider, model and effort, for notes on stored results.
    label: str


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        recorder: EventRecorder | None = None,
        resolver: ModelResolver | None = None,
    ) -> None:
        self.provider = provider
        self._recorder = recorder
        self.resolver = resolver or StaticResolver(provider.model)

    def _label(self, resolved: Resolved) -> str:
        label = f"{self.provider.name}/{resolved.model}"
        if resolved.reasoning_effort is not None:
            label += f", effort {resolved.reasoning_effort.value}"
        return label

    def complete_model[T: BaseModel](
        self, task: LLMTask, system: str, user: str, model_cls: type[T], attempts: int = 2
    ) -> Completed[T]:
        """Return validated output, retrying on LLMError or ValidationError.

        The task's model and effort are resolved once, at the start of the call. If the
        provider says that model is unavailable, the same attempt is repeated once with the
        default model and its own effort (recorded as ``model_fallback``). After ``attempts``
        failures the last error is raised, carrying the model it used (``failed_model``).
        """
        schema = model_cls.model_json_schema()
        resolved = self.resolver.resolve(task)
        default = self.provider.model
        fell_back = False
        attempt = 1
        while attempt <= attempts:
            model = resolved.model
            started = time.perf_counter()
            try:
                completion = self.provider.complete_json(
                    system,
                    user,
                    schema,
                    model=model,
                    reasoning_effort=resolved.reasoning_effort,
                )
            except LLMError as exc:
                self._dropped(task, model, attempt, exc.temperature_rejected)
                self._record(
                    task,
                    LLMOutcome.PROVIDER_ERROR,
                    model,
                    attempt=attempt,
                    error=describe_error(exc),
                    params=_params(exc.params),
                    latency_ms=_since(started),
                )
                if exc.model_unavailable and model != default and not fell_back:
                    fell_back = True
                    self._record(
                        task,
                        LLMOutcome.MODEL_FALLBACK,
                        model,
                        attempt=attempt,
                        error=f"{model} unavailable; used {default}",
                    )
                    resolved = self.resolver.for_model(task, default)
                    continue  # the same attempt, with the default model
                if attempt == attempts:
                    _stamp(exc, model)
                    raise
                attempt += 1
                continue
            latency = _since(started)
            self._dropped(task, model, attempt, completion.temperature_rejected)
            try:
                result = model_cls.model_validate_json(completion.text)
            except ValidationError as exc:
                # The type name only: Pydantic messages can contain input values.
                self._record(
                    task,
                    LLMOutcome.SCHEMA_INVALID,
                    model,
                    attempt=attempt,
                    error=type(exc).__name__,
                    params=_params(completion.params),
                    latency_ms=latency,
                )
                if attempt == attempts:
                    _stamp(exc, model)
                    raise
                attempt += 1
                continue
            self._record(
                task,
                LLMOutcome.OK,
                model,
                attempt=attempt,
                params=_params(completion.params),
                latency_ms=latency,
            )
            return Completed(result, model, resolved.reasoning_effort, self._label(resolved))
        raise LLMError("attempts must be at least 1")

    def record_fallback(self, task: LLMTask, reason: str, *, model: str | None = None) -> None:
        """Record that ``task`` used deterministic code instead of the model's output.

        ``model`` is the model the call actually used: ``failed_model(exc)`` after an error,
        or ``Completed.model`` when the output was unusable. That can differ from the task's
        current resolution (after a ``model_fallback``, or a settings change since), which is
        used only when the model is unknown.
        """
        model = model or self.resolver.resolve(task).model
        self._record(task, LLMOutcome.DETERMINISTIC_FALLBACK, model, error=reason)

    def _dropped(
        self, task: LLMTask, model: str, attempt: int, rejected: RequestParams | None
    ) -> None:
        if rejected is not None:
            self._record(
                task,
                LLMOutcome.TEMPERATURE_DROPPED,
                model,
                attempt=attempt,
                params=_params(rejected),
            )

    def _record(self, task: LLMTask, outcome: LLMOutcome, model: str, **fields: Any) -> None:
        """Record one event; ``model`` is the model this call resolved, not the default."""
        if self._recorder is None:
            return
        if fields.get("error"):
            fields["error"] = fields["error"][:MAX_ERROR_CHARS]
        event = CallEvent(
            task=task, outcome=outcome, provider=self.provider.name, model=model, **fields
        )
        try:
            self._recorder.record(event)
        except Exception as exc:
            # Recording must never break the call it describes (AGENTS.md section 3). Log the
            # error type only, without a traceback: this often runs while a ValidationError is
            # being handled, and the chained exception would carry the model's output (which
            # can quote candidate facts) into the logs.
            logger.warning(
                "failed to record llm call event",
                extra={
                    "task": task.value,
                    "outcome": outcome.value,
                    "error_type": type(exc).__name__,
                },
            )


def _since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
