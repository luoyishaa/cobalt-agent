"""Transport adapters for structured model tool calls."""

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


class ModelOutputError(RuntimeError):
    """A provider response was received but could not be used as a model turn."""


class ChatCompletions:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: int = 120,
        provider: str = "DeepSeek",
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.provider = provider

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }
        if self.provider == "openai" and self.model.startswith("gpt-6-"):
            # Chat tools on Sol/Luna require no reasoning. Astra is rejected by config.
            payload["reasoning_effort"] = "none"
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
                raise RuntimeError(f"{self.provider} returned HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"{self.provider} is unreachable: {exc.reason}") from exc
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("model response was cut off by its output limit")
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ModelOutputError("provider stopped without a complete answer or tool call")
            message = choice["message"]
            calls = []
            for raw in message.get("tool_calls") or []:
                encoded = raw["function"]["arguments"]
                arguments = json.loads(encoded) if isinstance(encoded, str) else encoded
                if not isinstance(arguments, dict):
                    raise TypeError("tool arguments must be an object")
                calls.append(ToolCall(raw["id"], raw["function"]["name"], arguments))
            if choice["finish_reason"] == "tool_calls" and not calls:
                raise ModelOutputError("provider reported tool calls but returned none")
            usage = data.get("usage") or {}
            return ModelTurn(
                text=message.get("content") or "",
                calls=tuple(calls),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelOutputError(f"{self.provider} returned an invalid structured response") from exc


class DeepSeek(ChatCompletions):
    """Backward-compatible adapter used by the pinned DeepSeek benchmark."""


class AnthropicMessages:
    """Translate Cobalt's canonical tool transcript to Claude Messages blocks."""

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: int = 120):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        transcript: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                         "content": str(message.get("content") or "")}
                if transcript and transcript[-1]["role"] == "user" and isinstance(transcript[-1]["content"], list):
                    transcript[-1]["content"].append(block)
                else:
                    transcript.append({"role": "user", "content": [block]})
            elif role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message["tool_calls"]:
                    blocks.append({"type": "tool_use", "id": call["id"],
                                   "name": call["function"]["name"],
                                   "input": json.loads(call["function"]["arguments"])})
                transcript.append({"role": "assistant", "content": blocks})
            elif role in {"user", "assistant"}:
                transcript.append({"role": role, "content": str(message.get("content") or "")})
            else:
                raise ValueError(f"Unsupported message role: {role}")
        native_tools = [{"name": t["function"]["name"],
                         "description": t["function"].get("description", ""),
                         "input_schema": t["function"]["parameters"]} for t in tools]
        payload = {"model": self.model, "max_tokens": 4096, "system": system,
                   "messages": transcript, "tools": native_tools}
        request = urllib.request.Request(
            self.base_url + "/messages", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"}, method="POST",
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
                raise RuntimeError(f"Anthropic returned HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Anthropic is unreachable: {exc.reason}") from exc
        try:
            if data.get("stop_reason") == "max_tokens":
                raise RuntimeError("model response was cut off by its output limit")
            if data.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence"}:
                raise ModelOutputError("Anthropic stopped without a complete answer or tool call")
            blocks = data["content"]
            calls = tuple(ToolCall(b["id"], b["name"], b["input"])
                          for b in blocks if b["type"] == "tool_use")
            if any(not isinstance(call.arguments, dict) for call in calls):
                raise TypeError("tool arguments must be an object")
            if data["stop_reason"] == "tool_use" and not calls:
                raise ModelOutputError("Anthropic reported tool use but returned none")
            usage = data.get("usage") or {}
            return ModelTurn("\n".join(b["text"] for b in blocks if b["type"] == "text"),
                             calls, usage.get("input_tokens"), usage.get("output_tokens"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelOutputError("Anthropic returned an invalid structured response") from exc


def from_config(config: Any) -> Model:
    if config.protocol == "messages":
        return AnthropicMessages(api_key=config.api_key, model=config.model, base_url=config.base_url)
    return ChatCompletions(api_key=config.api_key, model=config.model,
                           base_url=config.base_url, provider=config.provider)
