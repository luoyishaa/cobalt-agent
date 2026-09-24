"""Provider selection and local credentials, independent of the target workspace."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Provider:
    key_name: str
    base_url: str
    fast_model: str
    pro_model: str
    protocol: str = "chat"


# Presets are only for providers with a documented Chat tools or Messages API.
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-flash", "deepseek-v4-pro"),
    "zai": Provider("ZAI_API_KEY", "https://api.z.ai/api/paas/v4", "glm-5.3-flash", "glm-5.3"),
    "qwen": Provider("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash", "qwen3.8-max"),
    "kimi": Provider("MOONSHOT_API_KEY", "https://api.moonshot.ai/v1", "kimi-k2.6", "kimi-k3"),
    "openai": Provider("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-6-luna", "gpt-6-sol"),
    "gemini": Provider("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "gemini-3.1-pro-preview"),
    "anthropic": Provider("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1", "claude-haiku-4-5", "claude-sonnet-5", "messages"),
    "groq": Provider("GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "llama-3.3-70b-versatile"),
    "mistral": Provider("MISTRAL_API_KEY", "https://api.mistral.ai/v1", "mistral-small-latest", "mistral-medium-latest"),
    "xai": Provider("XAI_API_KEY", "https://api.x.ai/v1", "grok-4.7", "grok-4.7"),
    "openrouter": Provider("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "openai/gpt-oss-120b", "openai/gpt-oss-120b"),
    "siliconflow": Provider("SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Flash", "Pro/deepseek-ai/DeepSeek-V4"),
    "minimax": Provider("MINIMAX_API_KEY", "https://api.minimax.io/v1", "MiniMax-M2.7", "MiniMax-M3"),
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    protocol: str


def read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE lines without evaluating shell syntax or changing os.environ."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name.isidentifier():
            raise ValueError(f"Invalid .env entry at line {number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        result[name] = value
    return result


def resolve_config(
    *, env_file: Path | None = None, environ: Mapping[str, str] | None = None,
    provider: str | None = None, model: str | None = None, base_url: str | None = None,
) -> ModelConfig:
    # Process variables override local file values; CLI flags override both.
    settings = read_env_file(env_file) if env_file else {}
    settings.update(dict(os.environ if environ is None else environ))
    name = (provider or settings.get("COBALT_PROVIDER") or "deepseek").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Choose: {', '.join(PROVIDERS)}")
    preset = PROVIDERS[name]
    key = settings.get(preset.key_name, "").strip()
    if not key:
        raise ValueError(f"{preset.key_name} is required for provider '{name}'")
    tier = settings.get("COBALT_MODEL_TIER", "fast").strip().lower()
    if tier not in {"fast", "pro"}:
        raise ValueError("COBALT_MODEL_TIER must be fast or pro")
    selected_model = (model or settings.get("COBALT_MODEL_ID") or
                      (preset.fast_model if tier == "fast" else preset.pro_model)).strip()
    selected_url = (base_url or settings.get("COBALT_BASE_URL") or preset.base_url).strip().rstrip("/")
    if not selected_model or not selected_url.startswith("https://"):
        raise ValueError("Model ID is required and base URL must use HTTPS")
    if name == "openai" and selected_model.startswith("gpt-6-astra"):
        raise ValueError("GPT-6 Astra tool calls require the Responses API, which Cobalt does not yet implement")
    return ModelConfig(name, key, selected_model, selected_url, preset.protocol)
