"""Command execution adapters. The workspace and evidence rules do not depend on Docker."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .domain import ToolOutcome
from .outputs import OutputStore

MAX_OUTPUT_CHARS = 12_000


class CommandRunner(Protocol):
    display_root: str

    def run(self, argv: list[str], *, timeout: int = 60) -> ToolOutcome: ...


class LocalCommandRunner:
    def __init__(self, root: Path):
        self.root = root
        self.display_root = str(root)

    def run(self, argv: list[str], *, timeout: int = 60) -> ToolOutcome:
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("argv must be a non-empty list of strings")
        if not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1..120 seconds")
        executable = shutil.which(argv[0])
        if os.name == "nt" and executable and Path(executable).suffix.lower() in {".bat", ".cmd"}:
            raise ValueError("batch files are not supported by the no-shell command tool")
        env_names = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TMP", "TEMP", "HOME", "USERPROFILE")
        env = {name: os.environ[name] for name in env_names if name in os.environ}
        return _run_process(self.root, argv, timeout=timeout, env=env)


class DockerCommandRunner:
    """Run one command in an existing image, mounting only an isolated workspace."""

    def __init__(self, root: Path, image: str, *, container_env: dict[str, str] | None = None):
        self.root = root.resolve(strict=True)
        self.display_root = "/workspace"
        self.image = image
        self.container_env = dict(container_env or {})
        if not image or any(char.isspace() for char in image):
            raise ValueError("a single Docker image name is required")
        if "," in str(self.root):
            raise ValueError("workspace path with a comma cannot be mounted safely")
        for name, value in self.container_env.items():
            if (not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
                    or any(secret in name for secret in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                    or not isinstance(value, str) or "\0" in value):
                raise ValueError(f"invalid or secret-like container environment name: {name}")
        os_type = self._probe(["docker", "info", "--format", "{{.OSType}}"],
                              "Docker engine is unavailable")
        if os_type.strip() != b"linux":
            raise ValueError("container execution requires a Linux Docker engine")
        self.image_id = self._probe(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                    f"Docker image {image!r} is unavailable; pull or build it before starting Cobalt")

    @staticmethod
    def _probe(argv: list[str], message: str) -> bytes:
        try:
            result = subprocess.run(argv, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(message) from exc
        if result.returncode != 0:
            raise ValueError(message)
        return result.stdout

    def run(self, argv: list[str], *, timeout: int = 60) -> ToolOutcome:
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("argv must be a non-empty list of strings")
        if not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1..120 seconds")
        user = f"{os.getuid()}:{os.getgid()}" if os.name != "nt" else "1000:1000"
        config_dir = self.root / ".cobalt"
        if config_dir.is_symlink():
            raise ValueError("runtime directory cannot be a symlink")
        config_dir.mkdir(exist_ok=True)
        # Older Git versions only accept safe.directory from a global file.
        config_path = config_dir / "container.gitconfig"
        expected_config = "[safe]\n\tdirectory = /workspace\n"
        if config_path.is_symlink():
            raise ValueError("container Git config cannot be a symlink")
        if config_path.exists():
            if config_path.read_text(encoding="utf-8") != expected_config:
                raise ValueError("container Git config was changed")
        else:
            config_path.write_text(expected_config, encoding="utf-8")
        # The Docker CLI inherits only its connection variables, never model credentials.
        allowed = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "HOME", "USERPROFILE",
                   "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
        env = {name: os.environ[name] for name in allowed if name in os.environ}
        with tempfile.TemporaryDirectory(prefix="cobalt-docker-") as directory:
            cidfile = Path(directory) / "cid"
            command = [
                "docker", "run", "--rm", "--cidfile", str(cidfile), "--pull=never",
                "--network=none", "--read-only", "--memory=2g", "--memory-swap=2g", "--cpus=2",
                "--pids-limit=256", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=512m",
                "--tmpfs=/testbed:rw,nosuid,nodev,size=16m", "--user", user,
                "--mount", f"type=bind,source={self.root},target=/workspace",
                "--workdir", "/workspace", "--env", "HOME=/tmp",
                "--env", "GIT_CONFIG_GLOBAL=/workspace/.cobalt/container.gitconfig",
                "--env", "PYTHONDONTWRITEBYTECODE=1",
                *(item for name, value in self.container_env.items()
                  for item in ("--env", f"{name}={value}")), self.image, *argv,
            ]

            def cleanup() -> None:
                try:
                    container_id = cidfile.read_text(encoding="ascii").strip()
                    if container_id:
                        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True,
                                       timeout=10, check=False, env=env)
                except (OSError, subprocess.TimeoutExpired):
                    pass

            return _run_process(self.root, command, timeout=timeout, env=env, on_timeout=cleanup)


def _stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
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


def _run_process(root: Path, argv: list[str], *, timeout: int, env: dict[str, str],
                 on_timeout: Callable[[], None] | None = None) -> ToolOutcome:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, creationflags=flags,
                                start_new_session=os.name != "nt")
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_process_tree(proc)
            timed_out = True
            if on_timeout is not None:
                on_timeout()
        try:
            output_id = OutputStore(root).save(stdout, stderr, exit_code=proc.returncode,
                                               timed_out=timed_out)
            archive_notice = (f"output_id: {output_id}\n"
                              "Use read_output with this ID to search or page through the original output; do not rerun for logs.\n")
        except (OSError, ValueError) as exc:
            archive_notice = (f"output archive unavailable ({type(exc).__name__}). Command already finished. "
                              "Do not rerun merely to recover logs; only the following bounded preview is available.\n")
        stdout.seek(0)
        stderr.seek(0)
        combined = stdout.read(MAX_OUTPUT_CHARS + 1) + b"\n" + stderr.read(MAX_OUTPUT_CHARS + 1)
        output = combined[:MAX_OUTPUT_CHARS].decode("utf-8", errors="replace").strip()
        if len(combined) > MAX_OUTPUT_CHARS:
            output += "\n[output truncated]"
        message = (f"exit_code: {proc.returncode}\n" + archive_notice
                   + (f"command timed out after {timeout}s\n" if timed_out else "")
                   + (output or "(no output)"))
        success = proc.returncode == 0 and not timed_out
        return ToolOutcome("ok" if success else "error", message, verified=success)
