"""Small data contracts shared by the runtime, model adapter, and tools."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    text: str
    calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ToolOutcome:
    status: Literal["ok", "error", "denied"]
    message: str
    path: str | None = None
    digest: str | None = None
    changed: bool = False
    verified: bool = False

    def to_message(self) -> str:
        body = self.message[:12_000]
        if len(body) < len(self.message):
            body += f"\n[truncated {len(self.message) - len(body)} characters]"
        parts = [f"status: {self.status}", body]
        if self.path:
            parts.append(f"path: {self.path}")
        if self.digest:
            parts.append(f"sha256: {self.digest}")
        return "\n".join(parts)


@dataclass
class RunResult:
    run_id: str
    answer: str
    status: Literal["completed", "unverified", "limit", "model_error"]
    tool_calls: int
    changed_paths: list[str] = field(default_factory=list)
    verified_commands: list[list[str]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    session_id: str = ""
    unsupported_references: list[str] = field(default_factory=list)
