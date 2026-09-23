"""Native structured tool calls from a DeepSeek chat model."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from .domain import ModelTurn, ToolCall


class Model(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn: ...


class DeepSeek:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: int = 120,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError(f"DeepSeek returned HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"DeepSeek is unreachable: {exc.reason}") from exc
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("model response was cut off by its output limit")
            message = choice["message"]
            calls = []
            for raw in message.get("tool_calls") or []:
                arguments = json.loads(raw["function"]["arguments"])
                if not isinstance(arguments, dict):
                    raise TypeError("tool arguments must be an object")
                calls.append(ToolCall(raw["id"], raw["function"]["name"], arguments))
            usage = data.get("usage") or {}
            return ModelTurn(
                text=message.get("content") or "",
                calls=tuple(calls),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("DeepSeek returned an invalid structured response") from exc
