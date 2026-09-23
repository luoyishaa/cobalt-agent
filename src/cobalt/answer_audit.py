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


@dataclass(frozen=True)
class ReadSpan:
    path: str
    start: int
    end: int
    digest: str


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
