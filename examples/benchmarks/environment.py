"""Read-only host and process-tree evidence for benchmark reports."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import multiprocessing
import os
import platform
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Mapping

_PACKAGE_NAMES = ("pyro3", "psutil", "pyperf", "loky", "cffi", "jinja2")
_PYROXIDE_ENVIRONMENT_NAMES = (
    "PYROXIDE_WORKERS",
    "PYROXIDE_QUEUE_CAPACITY",
    "PYROXIDE_MAX_PROCESSES",
    "PYROXIDE_MAX_TASKS_PER_WORKER",
    "PYROXIDE_WORKER_STARTUP_TIMEOUT_SEC",
    "PYROXIDE_IDLE_TIMEOUT_SEC",
    "PYROXIDE_MIN_WORKERS",
    "PYROXIDE_SHM_THRESHOLD",
    "PYROXIDE_MAX_IPC_FRAME_BYTES",
    "PYROXIDE_QUEUE_TIMEOUT_MS",
    "PYROXIDE_WASM_TICK_MS",
    "PYROXIDE_WASM_MEMORY_LIMIT_BYTES",
    "PYROXIDE_WASM_TIMEOUT_MS",
    "PYROXIDE_MAX_NATIVE_OUTPUT_BYTES",
    "PYROXIDE_CACHE_DIR",
    "PYROXIDE_COMPILER_TIMEOUT_SEC",
)


@dataclass(frozen=True, slots=True)
class EnvironmentMetadata:
    """Interpreter, host, repository, and artifact state for one run."""

    executable: str
    python_version: str
    python_implementation: str
    python_build: str
    gil_enabled: bool | None
    package_versions: Mapping[str, str | None]
    git_sha: str | None
    git_dirty: bool | None
    argv: tuple[str, ...]
    timestamp_utc: str
    cpu_logical_count: int | None
    cpu_physical_count: int | None
    ram_total_bytes: int | None
    os_name: str
    os_release: str
    kernel_release: str
    cpu_affinity: tuple[int, ...] | None
    multiprocessing_start_method: str | None
    pyroxide_settings: Mapping[str, str | None]
    compiler: Mapping[str, str | None]
    artifact: Mapping[str, str | int | None]
    unavailable: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ProcessTreeSample:
    """Aggregate resource counters for a root process and its descendants."""

    cpu_time_seconds: float | None
    rss_bytes: int | None
    voluntary_context_switches: int | None
    involuntary_context_switches: int | None
    file_descriptors: int | None
    children_total: int | None
    children_started: int | None
    children_exited: int | None
    unavailable: Mapping[str, str] = field(default_factory=dict)


class ProcessTreeSampler:
    """Read aggregate counters without starting, stopping, or waiting for processes."""

    def __init__(self, *, process: object | None = None, psutil_module: ModuleType | None = None) -> None:
        if process is None:
            psutil_module = psutil_module or _load_psutil()
            process = psutil_module.Process()
        self._process = process
        self._known_children: set[int] = set()

    def sample(self) -> ProcessTreeSample:
        """Return a point-in-time aggregate and child PID churn since the prior sample."""
        unavailable: dict[str, str] = {}
        try:
            children = tuple(self._process.children(recursive=True))
        except Exception as error:
            children = ()
            _mark_unavailable(unavailable, "children", error)

        child_ids = {child.pid for child in children}
        if "children" in unavailable:
            children_total = None
            children_started = None
            children_exited = None
        else:
            children_total = len(child_ids)
            children_started = len(child_ids - self._known_children)
            children_exited = len(self._known_children - child_ids)
            self._known_children = child_ids

        processes = (self._process, *children)
        return ProcessTreeSample(
            cpu_time_seconds=_aggregate(
                processes,
                "cpu_time_seconds",
                lambda process: process.cpu_times().user + process.cpu_times().system,
                unavailable,
            ),
            rss_bytes=_aggregate(
                processes,
                "rss_bytes",
                lambda process: process.memory_info().rss,
                unavailable,
            ),
            voluntary_context_switches=_aggregate(
                processes,
                "voluntary_context_switches",
                lambda process: process.num_ctx_switches().voluntary,
                unavailable,
            ),
            involuntary_context_switches=_aggregate(
                processes,
                "involuntary_context_switches",
                lambda process: process.num_ctx_switches().involuntary,
                unavailable,
            ),
            file_descriptors=_aggregate(
                processes,
                "file_descriptors",
                _file_descriptor_count,
                unavailable,
            ),
            children_total=children_total,
            children_started=children_started,
            children_exited=children_exited,
            unavailable=unavailable,
        )


def collect_environment(
    *,
    now: datetime | None = None,
    psutil_module: ModuleType | None = None,
    repository_root: Path | None = None,
) -> EnvironmentMetadata:
    """Collect reproducibility metadata using bounded, read-only host probes."""
    unavailable: dict[str, str] = {}
    package_versions = _package_versions()
    for package, version in package_versions.items():
        if version is None:
            unavailable[f"package_versions.{package}"] = "package is not installed"

    git_sha, git_dirty, git_unavailable = _git_metadata(
        repository_root or Path(__file__).resolve().parents[2]
    )
    unavailable.update(git_unavailable)
    if git_sha is None:
        unavailable.setdefault("git_sha", "git metadata unavailable")
    if git_dirty is None:
        unavailable.setdefault("git_dirty", "git metadata unavailable")

    pyroxide_settings, pyroxide_unavailable = _pyroxide_metadata()
    compiler, compiler_unavailable = _compiler_metadata()
    artifact, artifact_unavailable = _artifact_metadata()
    unavailable.update(pyroxide_unavailable)
    unavailable.update(compiler_unavailable)
    unavailable.update(artifact_unavailable)

    gil_enabled = _gil_enabled(unavailable)
    logical_count, physical_count, ram_total, affinity = _system_resources(
        psutil_module,
        unavailable,
    )
    start_method = _start_method(unavailable)
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    return EnvironmentMetadata(
        executable=sys.executable,
        python_version=sys.version,
        python_implementation=platform.python_implementation(),
        python_build=" ".join(platform.python_build()),
        gil_enabled=gil_enabled,
        package_versions=package_versions,
        git_sha=git_sha,
        git_dirty=git_dirty,
        argv=tuple(sys.argv),
        timestamp_utc=timestamp.isoformat(),
        cpu_logical_count=logical_count,
        cpu_physical_count=physical_count,
        ram_total_bytes=ram_total,
        os_name=platform.system(),
        os_release=platform.release(),
        kernel_release=platform.version(),
        cpu_affinity=affinity,
        multiprocessing_start_method=start_method,
        pyroxide_settings=pyroxide_settings,
        compiler=compiler,
        artifact=artifact,
        unavailable=unavailable,
    )


def _aggregate(
    processes: tuple[object, ...],
    field_name: str,
    read: object,
    unavailable: dict[str, str],
) -> float | int | None:
    total: float | int = 0
    for process in processes:
        try:
            total += read(process)  # type: ignore[operator]
        except Exception as error:
            _mark_unavailable(unavailable, field_name, error)
            return None
    return total


def _file_descriptor_count(process: object) -> int:
    if hasattr(process, "num_fds"):
        return process.num_fds()
    return process.num_handles()


def _load_psutil() -> ModuleType:
    return importlib.import_module("psutil")


def _system_resources(
    psutil_module: ModuleType | None,
    unavailable: dict[str, str],
) -> tuple[int | None, int | None, int | None, tuple[int, ...] | None]:
    try:
        psutil_module = psutil_module or _load_psutil()
        process = psutil_module.Process()
    except Exception as error:
        _mark_unavailable(unavailable, "cpu_logical_count", error)
        _mark_unavailable(unavailable, "cpu_physical_count", error)
        _mark_unavailable(unavailable, "ram_total_bytes", error)
        _mark_unavailable(unavailable, "cpu_affinity", error)
        return None, None, None, None

    logical_count = _probe(
        "cpu_logical_count",
        lambda: psutil_module.cpu_count(logical=True),
        unavailable,
    )
    physical_count = _probe(
        "cpu_physical_count",
        lambda: psutil_module.cpu_count(logical=False),
        unavailable,
    )
    ram_total = _probe(
        "ram_total_bytes",
        lambda: psutil_module.virtual_memory().total,
        unavailable,
    )
    affinity = _probe(
        "cpu_affinity",
        lambda: tuple(process.cpu_affinity()),
        unavailable,
    )
    return logical_count, physical_count, ram_total, affinity


def _probe(field_name: str, read: object, unavailable: dict[str, str]):
    try:
        return read()  # type: ignore[operator]
    except Exception as error:
        _mark_unavailable(unavailable, field_name, error)
        return None


def _gil_enabled(unavailable: dict[str, str]) -> bool | None:
    checker = getattr(sys, "_is_gil_enabled", None)
    if checker is None:
        unavailable["gil_enabled"] = "interpreter does not expose GIL state"
        return None
    try:
        return bool(checker())
    except Exception as error:
        _mark_unavailable(unavailable, "gil_enabled", error)
        return None


def _start_method(unavailable: dict[str, str]) -> str | None:
    try:
        value = multiprocessing.get_start_method(allow_none=True)
    except Exception as error:
        _mark_unavailable(unavailable, "multiprocessing_start_method", error)
        return None
    if value is None:
        unavailable["multiprocessing_start_method"] = "start method is not configured"
    return value


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in _PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _git_metadata(root: Path) -> tuple[str | None, bool | None, dict[str, str]]:
    try:
        sha = _git(root, "rev-parse", "HEAD")
        dirty = bool(_git(root, "status", "--porcelain"))
        return sha, dirty, {}
    except (OSError, subprocess.CalledProcessError) as error:
        reason = _exception_reason(error)
        return None, None, {"git_sha": reason, "git_dirty": reason}


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _pyroxide_metadata() -> tuple[dict[str, str | None], dict[str, str]]:
    """Return configuration without importing Pyroxide and locking its engine."""
    settings = {name: os.environ.get(name) for name in _PYROXIDE_ENVIRONMENT_NAMES}
    return settings, {}


def _compiler_metadata() -> tuple[dict[str, str | None], dict[str, str]]:
    compiler = {
        "cc": sysconfig.get_config_var("CC"),
        "cflags": sysconfig.get_config_var("CFLAGS"),
        "soabi": sysconfig.get_config_var("SOABI"),
        "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
    }
    unavailable = {
        f"compiler.{name}": "interpreter build setting unavailable"
        for name, value in compiler.items()
        if value is None
    }
    return compiler, unavailable


def _artifact_metadata() -> tuple[dict[str, str | int | None], dict[str, str]]:
    try:
        specification = importlib.util.find_spec("pyroxide._pyroxide")
        if specification is None or specification.origin is None:
            raise LookupError("Pyroxide extension artifact is not importable")
        path = Path(specification.origin)
        content = path.read_bytes()
        return {
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }, {}
    except Exception as error:
        return (
            {"path": None, "sha256": None, "size_bytes": None},
            {
                "artifact.path": _exception_reason(error),
                "artifact.sha256": _exception_reason(error),
                "artifact.size_bytes": _exception_reason(error),
            },
        )


def _mark_unavailable(
    unavailable: dict[str, str], field_name: str, error: Exception
) -> None:
    unavailable[field_name] = _exception_reason(error)


def _exception_reason(error: Exception) -> str:
    message = str(error)
    return type(error).__name__ if not message else f"{type(error).__name__}: {message}"
