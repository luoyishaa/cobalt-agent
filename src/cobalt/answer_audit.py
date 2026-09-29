"""Check that explicit source locations in an answer were actually observed."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .workspace import Workspace

SOURCE = re.compile(
    r"(?<![\w/])(?P<path>(?:[\w.-]+/)*[\w.-]+\.(?:pyi?|[jt]sx?|go|rs|java|md|json|toml|ya?ml|sh|cpp|hpp|txt))"
    r":(?P<start>[1-9]\d*)(?:[-–—](?P<end>[1-9]\d*))?\b",
    re.IGNORECASE,
)

# These deliberately cover explicit completed-action claims, not arbitrary prose
# about what a change *would* do. The runtime facts remain the source of truth.
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
EDIT_CLAIM = re.compile(
    r"\b(?:I|we)\s+(?:have\s+)?(?:modified|changed|updated|fixed|patched|created|added|removed|edited|implemented)\b"
    r"|(?<!no )(?<!no code )\bchanges?\s+(?:made|applied)\b"
    r"|\b(?:code|file|implementation)\s+(?:was|has been)\s+(?:modified|changed|updated|fixed|patched)\b"
    r"|(?:我|我们|已经|已)(?:完成)?(?:修改|修复|更改|更新|创建|新增|删除|实现)",
    re.IGNORECASE,
)
TEST_RUN_CLAIM = re.compile(
    r"\b(?:I|we)\s+(?:have\s+)?(?:ran|run|executed)\s+(?:the\s+)?(?:tests?|checks?|test suite)\b"
    r"|(?:我|我们|已经|已)(?:运行|执行)(?:了)?(?:测试|检查)",
    re.IGNORECASE,
)
TEST_PASS_CLAIM = re.compile(
    r"\b(?:tests?|checks?|test suite)\s+(?:all\s+)?(?:passed|succeeded)\b"
    r"|(?:测试|检查)(?:已经|已)?通过",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReadSpan:
    path: str
    start: int
    end: int
    digest: str
    call_id: str | None = None


def audit_action_claims(answer: str, *, changed_paths: set[str], checks_run: bool,
                        verified_commands: bool) -> list[str]:
    """Check only explicit completed-action claims against observed effects.

    Quoted code is excluded. This is a conservative audit of recognizable claims,
    not a semantic proof that the task was solved or that every sentence is true.
    """
    prose = CODE.sub(" ", answer)
    unsupported: list[str] = []
    if EDIT_CLAIM.search(prose) and not changed_paths:
        unsupported.append("file_change")
    if TEST_RUN_CLAIM.search(prose) and not checks_run:
        unsupported.append("check_run")
    if TEST_PASS_CLAIM.search(prose) and not verified_commands:
        unsupported.append("successful_check")
    return unsupported


def audit_source_references(answer: str, reads: list[ReadSpan], workspace: Workspace) -> tuple[list[str], list[str]]:
    """Return cited locations and those not backed by a fresh read in this run.

    This checks provenance and freshness, not whether the cited line proves the
    surrounding prose. An uncited claim is outside this narrow audit.
    """
    references: list[str] = []
    unsupported: list[str] = []
    for match in SOURCE.finditer(answer):
        reference = match.group(0)
        if reference in references:
            continue
        references.append(reference)
        path = match.group("path").casefold()
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        candidates = {read.path for read in reads if read.path.casefold() == path or read.path.casefold().endswith("/" + path)}
        if len(candidates) != 1 or end < start:
            unsupported.append(reference)
            continue
        resolved = next(iter(candidates))
        try:
            digest = workspace.file_digest(resolved)
            line = workspace.read_file(resolved, start=end, lines=1).message
        except (OSError, ValueError):
            unsupported.append(reference)
            continue
        if line == "(empty)" or not any(
            read.path == resolved and read.digest == digest and read.start <= start and end <= read.end
            for read in reads
        ):
            unsupported.append(reference)
    return references, unsupported
