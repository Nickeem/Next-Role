"""HTTP adapters for Ollama and OpenAI-compatible chat-completions APIs."""

from typing import Any

import httpx

from next_role.integrations.llm.base import LLMError


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
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers, transport=transport
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=body)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"{self.name} returned HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"{self.name} request failed: {type(exc).__name__}") from exc
        return data


class OllamaProvider(_HTTPProvider):
    """Self-hosted Ollama (``POST /api/chat`` with a JSON-schema ``format``)."""

    name = "ollama"
    external = False

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        data = self._post(
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
            raise LLMError("ollama response missing message content") from exc
        if not isinstance(content, str):
            raise LLMError("ollama response content is not text")
        return content


class OpenAICompatibleProvider(_HTTPProvider):
    """Any ``POST /v1/chat/completions`` API supporting ``json_schema`` response formats."""

    name = "openai_compatible"
    external = True

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        data = self._post(
            "/v1/chat/completions",
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("openai_compatible response missing message content") from exc
        if not isinstance(content, str):
            raise LLMError("openai_compatible response content is not text")
        return content
