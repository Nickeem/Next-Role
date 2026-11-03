"""HTTP adapters for Ollama and OpenAI-compatible chat-completions APIs."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from next_role.integrations.llm.base import Completion, LLMError, RequestParams

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
                f"{self.name} returned HTTP {exc.response.status_code}", params, rejected
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(
                f"{self.name} request failed: {type(exc).__name__}", params, rejected
            ) from exc
        return data, _Sent(params, rejected)

    def _retry_without_temperature(self, response: httpx.Response, body: dict[str, Any]) -> bool:
        return False


class OllamaProvider(_HTTPProvider):
    """Self-hosted Ollama (``POST /api/chat`` with a JSON-schema ``format``)."""

    name = "ollama"
    external = False

    def _params(self, body: dict[str, Any]) -> RequestParams:
        temperature = body.get("options", {}).get("temperature")
        return RequestParams(self.model, temperature, "json_schema", self.timeout)

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> Completion:
        data, sent = self._post(
            "/api/chat",
            {
                "model": self.model,
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
    # Some models (for example OpenAI reasoning models) accept only the default temperature
    # and reject any explicit value with HTTP 400. Once seen, temperature is no longer sent.
    _send_temperature = True

    def _retry_without_temperature(self, response: httpx.Response, body: dict[str, Any]) -> bool:
        if response.status_code != 400 or "temperature" not in body:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        # Any shape other than {"error": {"param": "temperature", ...}} falls through to the
        # normal HTTP-status handling, which raises LLMError.
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict) or error.get("param") != "temperature":
            return False
        # A per-instance cache so later calls skip the rejected parameter. A race between
        # threads costs at most one extra 400; what each call sent is reported in its result.
        self._send_temperature = False
        logger.info("model rejected temperature; using its default", extra={"provider": self.name})
        return True

    def _params(self, body: dict[str, Any]) -> RequestParams:
        return RequestParams(
            self.model, body.get("temperature"), body["response_format"]["type"], self.timeout
        )

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._send_temperature:
            body["temperature"] = 0
        data, sent = self._post("/v1/chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise sent.error("openai_compatible response missing message content") from exc
        if not isinstance(content, str):
            raise sent.error("openai_compatible response content is not text")
        return sent.completion(content)
