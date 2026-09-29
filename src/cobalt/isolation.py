"""Prepare a task copy without local credentials or later Git history."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .workspace import SKIP_DIRS, private_name


def is_isolated_workspace(path: Path) -> bool:
    marker = path / ".cobalt" / "isolated.json"
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == {"version": 1}
    except (OSError, ValueError):
        return False


def prepare_isolated_workspace(source: Path) -> Path:
    source = source.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("workspace root must be a directory")
    target = Path(tempfile.mkdtemp(prefix="cobalt-isolated-"))
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS
                   and not private_name(name) and not (current_path / name).is_symlink()]
        for name in files:
            path = current_path / name
            if private_name(name) or path.is_symlink():
                continue
            shutil.copy2(path, destination / name)
    # A one-commit history supports diff-based tasks without leaking future fixes.
    commands = (["git", "init", "-q"],
                ["git", "config", "--local", "core.autocrlf", "false"],
                ["git", "add", "-A"],
                ["git", "-c", "user.name=Cobalt", "-c", "user.email=cobalt@localhost",
                 "commit", "--allow-empty", "-qm", "Initial task snapshot"])
    for command in commands:
        try:
            subprocess.run(command, cwd=target, capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Could not initialize isolated task copy at {target}") from exc
    with (target / ".git" / "info" / "exclude").open("a", encoding="utf-8") as stream:
        stream.write("\n.cobalt/\n")
    (target / ".cobalt").mkdir()
    (target / ".cobalt" / "isolated.json").write_text('{"version": 1}\n', encoding="utf-8")
    return target
