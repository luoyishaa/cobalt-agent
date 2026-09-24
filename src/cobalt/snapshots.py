"""Workspace observations: explicit scope, content versions, and incomplete scans."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Snapshot:
    files: dict[str, str]
    errors: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str | None:
        if self.errors:
            return None
        return hashlib.sha256(json.dumps(self.files, sort_keys=True).encode()).hexdigest()

    def changes_from(self, previous: Snapshot) -> dict[str, str]:
        changes = {}
        for path in sorted(self.files.keys() | previous.files.keys()):
            if self.files.get(path) == previous.files.get(path):
                continue
            # An unreadable path is unknown, not evidence of deletion.
            if path not in self.files and self.errors:
                continue
            if path not in previous.files and previous.errors:
                continue
            changes[path] = "created" if path not in previous.files else "deleted" if path not in self.files else "modified"
        return changes


def capture(root: Path, excluded_dirs: set[str], private: Callable[[str], bool]) -> Snapshot:
    files: dict[str, str] = {}
    errors: list[str] = []

    def failed(exc):
        errors.append(f"{getattr(exc, 'filename', '')}: {type(exc).__name__}")

    for current, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(name for name in dirs if name not in excluded_dirs)
        links = [name for name in dirs if (Path(current) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in sorted(names + links):
            if name in excluded_dirs or private(name):
                continue
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            try:
                before = path.lstat()
                if stat.S_ISLNK(before.st_mode):
                    files[relative] = "link:" + os.readlink(path)
                    continue
                if not path.resolve().is_relative_to(root) or not stat.S_ISREG(before.st_mode):
                    errors.append(relative + ": not an observable regular file")
                    continue
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                after = path.lstat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    errors.append(relative + ": changed during observation")
                files[relative] = f"file:{stat.S_IMODE(after.st_mode)}:{digest}"
            except OSError as exc:
                errors.append(relative + ": " + type(exc).__name__)
    return Snapshot(files, tuple(errors))
