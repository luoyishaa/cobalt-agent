"""All repository reads and writes pass through this module.

The interface accepts paths relative to one root. Every path is resolved before
access, including symlinks. An edit checks the content digest it was based on,
so an older model observation cannot silently overwrite a newer human change.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

from .domain import ToolOutcome

SKIP_DIRS = {".git", ".cobalt", ".venv", "__pycache__", "node_modules"}
MAX_READ_BYTES = 128_000
MAX_OUTPUT_CHARS = 12_000


def private_name(name: str) -> bool:
    lower = name.lower()
    return (lower == ".env" or lower.startswith(".env.")) and lower != ".env.example"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace root must be a directory")

    def _path(self, relative: str, *, must_exist: bool = False) -> Path:
        if not relative or Path(relative).is_absolute():
            raise ValueError("provide a non-empty relative path")
        path = (self.root / relative).resolve(strict=False)
        if not path.is_relative_to(self.root):
            raise ValueError("path escapes the workspace")
        if any(part in SKIP_DIRS for part in path.relative_to(self.root).parts):
            raise ValueError("path is reserved for runtime or dependencies")
        if private_name(path.name):
            raise ValueError("private configuration files cannot be read by the agent")
        if must_exist and not path.exists():
            raise ValueError("path does not exist")
        return path

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def file_digest(self, relative: str) -> str:
        path = self._path(relative, must_exist=True)
        if not path.is_file() or path.stat().st_size > MAX_READ_BYTES:
            raise ValueError("observed file is unavailable or too large")
        return digest_bytes(path.read_bytes())

    def overview(self) -> str:
        names = self.list_files(".", max_entries=60).message
        try:
            result = subprocess.run(
                ["git", "status", "--short"], cwd=self.root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
            )
            status = result.stdout.strip() if result.returncode == 0 else "no Git status"
        except (OSError, subprocess.TimeoutExpired):
            status = "Git status unavailable"
        return f"Workspace: {self.root}\nGit status: {status or 'clean'}\n{names}"

    def list_files(self, relative: str = ".", *, max_entries: int = 100) -> ToolOutcome:
        path = self.root if relative == "." else self._path(relative, must_exist=True)
        if not path.is_dir():
            raise ValueError("path is not a directory")
        entries: list[str] = []
        for current, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(dirs + files):
                entry = Path(current) / name
                if entry.is_symlink() or private_name(name):
                    continue
                entries.append(self._rel(entry) + ("/" if entry.is_dir() else ""))
                if len(entries) >= max_entries:
                    return ToolOutcome("ok", "\n".join(entries) + "\n[entry limit reached]")
        return ToolOutcome("ok", "\n".join(entries) or "(empty)")

    def read_file(self, relative: str, *, start: int = 1, lines: int = 160) -> ToolOutcome:
        if start < 1 or not 1 <= lines <= 400:
            raise ValueError("start must be positive and lines must be 1..400")
        path = self._path(relative, must_exist=True)
        if not path.is_file():
            raise ValueError("path is not a regular file")
        data = path.read_bytes()
        if len(data) > MAX_READ_BYTES:
            raise ValueError("file is too large to read with this tool")
        content = data.decode("utf-8", errors="replace").splitlines()
        selected = content[start - 1 : start - 1 + lines]
        numbered = "\n".join(f"{n:>4} {line}" for n, line in enumerate(selected, start))
        if start - 1 + lines < len(content):
            numbered += "\n[more lines available]"
        return ToolOutcome("ok", numbered or "(empty)", self._rel(path), digest_bytes(data))

    def search(self, query: str, *, max_matches: int = 80) -> ToolOutcome:
        if not query or len(query) > 200:
            raise ValueError("query must be 1..200 characters")
        matches: list[str] = []
        for current, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if private_name(name):
                    continue
                path = Path(current) / name
                if path.is_symlink() or path.stat().st_size > MAX_READ_BYTES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeError, OSError):
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append(f"{self._rel(path)}:{number}: {line[:240]}")
                        if len(matches) >= max_matches:
                            return ToolOutcome("ok", "\n".join(matches) + "\n[match limit reached]")
        return ToolOutcome("ok", "\n".join(matches) or "(no matches)")

    def replace_text(self, relative: str, old: str, new: str, expected_sha256: str) -> ToolOutcome:
        if not old:
            raise ValueError("old text must be non-empty")
        path = self._path(relative, must_exist=True)
        if not path.is_file() or path.is_symlink():
            raise ValueError("path is not a regular file")
        before = path.read_bytes()
        if digest_bytes(before) != expected_sha256:
            raise ValueError("file changed since it was read; read it again")
        content = before.decode("utf-8")
        if content.count(old) != 1:
            raise ValueError("old text must occur exactly once")
        updated = content.replace(old, new, 1).encode("utf-8")
        self._atomic_write(path, updated)
        return ToolOutcome("ok", "text replaced", self._rel(path), digest_bytes(updated), changed=True)

    def create_file(self, relative: str, content: str) -> ToolOutcome:
        path = self._path(relative)
        if path.exists():
            raise ValueError("file already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self._path(relative)
        data = content.encode("utf-8")
        self._atomic_write(path, data, create_only=True)
        return ToolOutcome("ok", "file created", self._rel(path), digest_bytes(data), changed=True)

    @staticmethod
    def _atomic_write(path: Path, data: bytes, *, create_only: bool = False) -> None:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".cobalt-write-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if create_only:
                # A hard link fails atomically if another writer created the name.
                try:
                    os.link(name, path)
                except FileExistsError as exc:
                    raise ValueError("file appeared while creating it") from exc
            else:
                os.chmod(name, stat.S_IMODE(path.stat().st_mode))
                os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def run_command(self, argv: list[str], *, timeout: int = 60) -> ToolOutcome:
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("argv must be a non-empty list of strings")
        if not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1..120 seconds")
        executable = shutil.which(argv[0])
        if os.name == "nt" and executable and Path(executable).suffix.lower() in {".bat", ".cmd"}:
            raise ValueError("batch files are not supported by the no-shell command tool")
        env_names = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TMP", "TEMP", "HOME", "USERPROFILE")
        env = {name: os.environ[name] for name in env_names if name in os.environ}
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        # Temporary files bound memory use even when a command prints indefinitely.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            proc = subprocess.Popen(
                argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, creationflags=flags,
                start_new_session=os.name != "nt",
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._stop_process_tree(proc)
                return ToolOutcome("error", f"command timed out after {timeout}s")
            stdout.seek(0)
            stderr.seek(0)
            combined = stdout.read(MAX_OUTPUT_CHARS + 1) + b"\n" + stderr.read(MAX_OUTPUT_CHARS + 1)
            output = combined[:MAX_OUTPUT_CHARS].decode("utf-8", errors="replace").strip()
            if len(combined) > MAX_OUTPUT_CHARS:
                output += "\n[output truncated]"
            message = f"exit_code: {proc.returncode}\n{output or '(no output)'}"
            return ToolOutcome("ok" if proc.returncode == 0 else "error", message, verified=proc.returncode == 0)

    @staticmethod
    def _stop_process_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
