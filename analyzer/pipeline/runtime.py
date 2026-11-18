"""Local run identity and an explicit-path lock. Importing this module writes nothing."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterable, Iterator


class RunBusy(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Acquire before orphan recovery; never remove a shared lock file on release."""
    path = Path(path)
    with path.open("a+b") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunBusy("lock_busy") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _hash_files(root: Path, files: Iterable[Path]) -> str:
    entries = []
    for path in sorted(set(files)):
        if path.is_symlink():
            raise ValueError("source symlinks are unsupported")
        path = path.resolve()
        relative = path.relative_to(root).as_posix()
        entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


class UnhashedModule(ValueError):
    """Official callers must record aborted(unhashed_module), not run the cohort."""


def _project_module_files(root: Path, modules: Iterable[ModuleType]) -> set[Path]:
    files = set()
    for module in modules:
        name = getattr(module, '__file__', None)
        if not name:
            continue
        path = Path(name).resolve()
        # Membership depends on its file, not a list of module/package names.
        if path.is_relative_to(root):
            if path.suffix != '.py':
                raise ValueError('project module must load from Python source')
            files.add(path)
    return files


@dataclass(frozen=True)
class SourceManifest:
    digest: str
    paths: tuple[str, ...]

    def verify_loaded(self, root: Path, modules: Iterable[ModuleType]) -> None:
        root = Path(root).resolve()
        loaded = {p.relative_to(root).as_posix() for p in _project_module_files(root, modules)}
        missing = sorted(loaded.difference(self.paths))
        if missing:
            raise UnhashedModule('unhashed_module: ' + ', '.join(missing))


def source_manifest(root: Path, entry_points: Iterable[str],
                    modules: Iterable[ModuleType] | None = None) -> SourceManifest:
    """Fixed source scope UNION imported project files; freeze paths for the run.

    Compute after imports; verify_loaded at start/end rejects late imports beyond
    the registered manifest. The stage 4 runner translates that failure to aborted.
    """
    root = Path(root).resolve()
    files = []
    for dirname in ("analyzer", "database"):
        directory = root / dirname
        if not directory.is_dir():
            raise ValueError(f"missing source directory: {dirname}")
        files.extend(directory.rglob("*.py"))
    names = {"constants.py", "config.py", "run.py", "morning_run.py",
             "backtests/snapshot.py", "backtests/market_db.py", *entry_points}
    for name in names:
        path = root / name
        if not path.is_file():
            raise ValueError(f"missing source: {name}")
        files.append(path)
    modules = tuple(sys.modules.values()) if modules is None else modules
    files.extend(_project_module_files(root, modules))
    paths = tuple(sorted({p.resolve().relative_to(root).as_posix() for p in files}))
    return SourceManifest(_hash_files(root, files), paths)


def source_hash(root: Path, entry_points: Iterable[str],
                modules: Iterable[ModuleType] | None = None) -> str:
    return source_manifest(root, entry_points, modules).digest


def loaded_modules_hash(root: Path, modules: Iterable[ModuleType]) -> str:
    """Hash files of imported project modules, not bytecode held in memory."""
    root = Path(root).resolve()
    files = _project_module_files(root, modules)
    if not files:
        raise ValueError("no project modules provided")
    return _hash_files(root, files)


# ── 4단계: 실행 경로 확인 (예정 실행 근거) ─────────────────────────────────────
# 실행 경로 확인 장치이며 완전한 증명은 아니다. 확인한 값을 그대로 기록한다.

SCHEDULED_MAX_AGE_SECONDS = 60


class ProcessStartUnknown(RuntimeError):
    """OS 기준 프로세스 시작 시각을 확인하지 못함 → 공식 실행 중단."""


def os_process_started_at(pid: int):
    """`ps -o lstart=`로 OS 기준 시작 시각(로컬 시간대, 초 단위)을 읽는다. 실패 시 예외."""
    import subprocess
    from datetime import datetime
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=10, check=True).stdout.strip()
        return datetime.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y").astimezone()
    except Exception as exc:
        raise ProcessStartUnknown(f"process start time unavailable: {exc}") from exc


@dataclass(frozen=True)
class LaunchEvidence:
    parent_pid: int
    service_label: str | None          # 환경 변수 XPC_SERVICE_NAME 그대로
    expected_label: str
    os_process_started_at: object      # timezone-aware datetime

    def scheduled_failures(self, now) -> tuple[str, ...]:
        fails = []
        if self.parent_pid != 1:
            fails.append("parent_not_launchd")
        if not self.service_label or self.service_label != self.expected_label:
            fails.append("service_label_mismatch")
        age = (now - self.os_process_started_at).total_seconds()
        if age < 0 or age > SCHEDULED_MAX_AGE_SECONDS:
            fails.append("process_not_fresh")
        return tuple(fails)


def collect_launch_evidence(expected_label: str, pid: int | None = None) -> LaunchEvidence:
    """현재 프로세스의 실행 경로 근거. 시작 시각을 못 읽으면 ProcessStartUnknown."""
    import os
    pid = os.getpid() if pid is None else pid
    return LaunchEvidence(os.getppid(), os.environ.get("XPC_SERVICE_NAME"), expected_label,
                          os_process_started_at(pid))
