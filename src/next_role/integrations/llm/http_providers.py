"""HTTP adapters for Ollama and OpenAI-compatible chat-completions APIs."""

import logging
import threading
from dataclasses import dataclass
from typing import Any

import httpx

from next_role.integrations.llm.base import Completion, LLMError, ReasoningEffort, RequestParams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Sent:
    params: RequestParams
    # Params of a first request rejected for its temperature, when it was resent without one.
    temperature_rejected: RequestParams | None

    def error(self, message: str) -> LLMError:
        return LLMError(message, self.params, self.temperature_rejected)

    def completion(self, text: str) -> Completion:
        return Completion(text, self.params, self.temperature_rejected)


class _HTTPProvider:
    name = "http"
    external = False

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers, transport=transport
        )

    def _params(self, body: dict[str, Any]) -> RequestParams:
        raise NotImplementedError

    def _post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], _Sent]:
        """POST ``body``; return the JSON response and what was sent.

        Params are returned, not stored on the instance: the provider is shared by request
        threads, and each call must report what it sent itself.
        """
        rejected: RequestParams | None = None
        params = self._params(body)
        try:
            response = self._client.post(path, json=body)
            if self._retry_without_temperature(response, body):
                rejected = params
                body = {k: v for k, v in body.items() if k != "temperature"}
                params = self._params(body)
                response = self._client.post(path, json=body)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"{self.name} returned HTTP {exc.response.status_code}",
                params,
                rejected,
                model_unavailable=self._model_unavailable(exc.response),
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(
                f"{self.name} request failed: {type(exc).__name__}", params, rejected
            ) from exc
        return data, _Sent(params, rejected)

    def _retry_without_temperature(self, response: httpx.Response, body: dict[str, Any]) -> bool:
        return False

    def _model_unavailable(self, response: httpx.Response) -> bool:
        return False


def _error_object(response: httpx.Response) -> dict[str, Any] | None:
    """The ``error`` object of an OpenAI-style error body, or None. Read, never stored."""
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


class OllamaProvider(_HTTPProvider):
    """Self-hosted Ollama (``POST /api/chat`` with a JSON-schema ``format``)."""

    name = "ollama"
    external = False

    def _params(self, body: dict[str, Any]) -> RequestParams:
        temperature = body.get("options", {}).get("temperature")
        return RequestParams(body["model"], temperature, "json_schema", self.timeout)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        model: str,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Completion:
        # Ollama has no reasoning-effort parameter; ``reasoning_effort`` is ignored.
        data, sent = self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise sent.error("ollama response missing message content") from exc
        if not isinstance(content, str):
            raise sent.error("ollama response content is not text")
        return sent.completion(content)


class OpenAICompatibleProvider(_HTTPProvider):
    """Any ``POST /v1/chat/completions`` API supporting ``json_schema`` response formats."""

    name = "openai_compatible"
    external = True

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, model, timeout, api_key, transport)
        # Models seen rejecting an explicit temperature with HTTP 400 (for example OpenAI
        # reasoning models sent no reasoning effort). Later calls to them omit it. Shared by
        # request threads, hence the lock.
        self._no_temperature: set[str] = set()
        self._lock = threading.Lock()

    def _sends_temperature(self, model: str, reasoning_effort: ReasoningEffort | None) -> bool:
        if reasoning_effort is not None:
            # Reasoning models accept a temperature only when the effort is ``none``.
            return reasoning_effort is ReasoningEffort.NONE
        with self._lock:
            return model not in self._no_temperature

    def _retry_without_temperature(self, response: httpx.Response, body: dict[str, Any]) -> bool:
        if response.status_code != 400 or "temperature" not in body:
            return False
        # Any shape other than {"error": {"param": "temperature", ...}} falls through to the
        # normal HTTP-status handling, which raises LLMError.
        error = _error_object(response)
        if error is None or error.get("param") != "temperature":
            return False
        # Remembered per model, so later calls to it skip the parameter. A race between threads
        # costs at most one extra 400; what each call sent is reported in its result.
        with self._lock:
            self._no_temperature.add(body["model"])
        logger.info(
            "model rejected temperature; using its default",
            extra={"provider": self.name, "model": body["model"]},
        )
        return True

    def list_models(self) -> list[str]:
        """``GET /v1/models``: the ids this key can use. Raises a redacted LLMError.

        Only the ``id`` of each entry is read.
        """
        try:
            response = self._client.get("/v1/models")
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data, list):
                raise TypeError("data is not a list")
            return [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"{self.name} returned HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"{self.name} model listing failed: {type(exc).__name__}") from exc

    def _model_unavailable(self, response: httpx.Response) -> bool:
        # OpenAI answers HTTP 404 with code "model_not_found" when the model does not exist or
        # the key cannot use it. Its error-code guide does not document this code, so it is
        # matched exactly; any other answer stays an ordinary provider error (no fallback).
        if response.status_code != 404:
            return False
        error = _error_object(response)
        return error is not None and error.get("code") == "model_not_found"

    def _params(self, body: dict[str, Any]) -> RequestParams:
        return RequestParams(
            body["model"],
            body.get("temperature"),
            body["response_format"]["type"],
            self.timeout,
            body.get("reasoning_effort"),
        )

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        model: str,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort.value
        if self._sends_temperature(model, reasoning_effort):
            body["temperature"] = 0
        data, sent = self._post("/v1/chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise sent.error("openai_compatible response missing message content") from exc
        if not isinstance(content, str):
            raise sent.error("openai_compatible response content is not text")
        return sent.completion(content)
