from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from examples.benchmarks import environment


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        cpu_seconds: float,
        rss_bytes: int,
        voluntary_switches: int,
        involuntary_switches: int,
        file_descriptors: int,
        affinity: tuple[int, ...] = (0, 2),
    ) -> None:
        self.pid = pid
        self._children: list[FakeProcess] = []
        self._cpu_seconds = cpu_seconds
        self._rss_bytes = rss_bytes
        self._voluntary_switches = voluntary_switches
        self._involuntary_switches = involuntary_switches
        self._file_descriptors = file_descriptors
        self._affinity = affinity

    def children(self, recursive: bool = False) -> list[FakeProcess]:
        assert recursive
        return list(self._children)

    def cpu_times(self) -> SimpleNamespace:
        return SimpleNamespace(user=self._cpu_seconds, system=0.0)

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self._rss_bytes)

    def num_ctx_switches(self) -> SimpleNamespace:
        return SimpleNamespace(
            voluntary=self._voluntary_switches,
            involuntary=self._involuntary_switches,
        )

    def num_fds(self) -> int:
        return self._file_descriptors

    def cpu_affinity(self) -> list[int]:
        return list(self._affinity)


class FakePsutil:
    def __init__(self, process: FakeProcess) -> None:
        self._process = process

    def Process(self) -> FakeProcess:
        return self._process

    @staticmethod
    def cpu_count(*, logical: bool) -> int:
        return 8 if logical else 4

    @staticmethod
    def virtual_memory() -> SimpleNamespace:
        return SimpleNamespace(total=32 * 1024**3)


def test_collect_environment_records_reproducibility_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Omitting a reproducibility field must fail this complete metadata contract."""
    process = FakeProcess(
        101,
        cpu_seconds=1.5,
        rss_bytes=4096,
        voluntary_switches=3,
        involuntary_switches=2,
        file_descriptors=7,
    )
    expected_timestamp = datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        environment,
        "_package_versions",
        lambda: {"pyro3": "1.0.0rc1", "psutil": "7.1.0"},
    )
    monkeypatch.setattr(
        environment,
        "_git_metadata",
        lambda root: ("deadbeef", False, {}),
    )
    monkeypatch.setattr(
        environment,
        "_pyroxide_metadata",
        lambda: ({"PYROXIDE_WORKERS": "2"}, {}),
    )
    monkeypatch.setattr(
        environment,
        "_compiler_metadata",
        lambda: ({"cc": "clang", "soabi": "cpython-314t"}, {}),
    )
    monkeypatch.setattr(
        environment,
        "_artifact_metadata",
        lambda: ({"path": "fixture-extension.so", "sha256": "abc123"}, {}),
    )
    monkeypatch.setattr(environment, "_start_method", lambda unavailable: "spawn")

    metadata = environment.collect_environment(
        now=expected_timestamp,
        psutil_module=FakePsutil(process),
        repository_root=tmp_path,
    )

    assert metadata.executable
    assert metadata.python_build
    assert metadata.gil_enabled in {True, False, None}
    assert metadata.package_versions == {"pyro3": "1.0.0rc1", "psutil": "7.1.0"}
    assert metadata.git_sha == "deadbeef"
    assert metadata.git_dirty is False
    assert metadata.argv
    assert metadata.timestamp_utc == "2026-07-27T12:30:00+00:00"
    assert metadata.cpu_logical_count == 8
    assert metadata.cpu_physical_count == 4
    assert metadata.ram_total_bytes == 32 * 1024**3
    assert metadata.os_name
    assert metadata.kernel_release
    assert metadata.cpu_affinity == (0, 2)
    assert metadata.multiprocessing_start_method == "spawn"
    assert metadata.pyroxide_settings == {"PYROXIDE_WORKERS": "2"}
    assert metadata.compiler == {"cc": "clang", "soabi": "cpython-314t"}
    assert metadata.artifact == {"path": "fixture-extension.so", "sha256": "abc123"}
    if metadata.gil_enabled is None:
        assert metadata.unavailable == {
            "gil_enabled": "interpreter does not expose GIL state"
        }
    else:
        assert metadata.unavailable == {}


def test_collect_environment_does_not_import_pyroxide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata collection must not lock configuration before backend creation."""
    imported: list[str] = []
    monkeypatch.setattr(
        environment.importlib,
        "import_module",
        lambda name: imported.append(name) or (_ for _ in ()).throw(ImportError(name)),
    )

    settings, unavailable = environment._pyroxide_metadata()

    assert imported == []
    assert isinstance(settings, dict)
    assert unavailable == {}


def test_collect_environment_marks_unavailable_affinity_with_a_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An unavailable host probe must be null with a reason, never silently omitted."""
    process = FakeProcess(
        101,
        cpu_seconds=0.0,
        rss_bytes=1,
        voluntary_switches=0,
        involuntary_switches=0,
        file_descriptors=0,
    )

    def unavailable_affinity() -> list[int]:
        raise PermissionError("permission denied")

    monkeypatch.setattr(process, "cpu_affinity", unavailable_affinity)
    monkeypatch.setattr(environment, "_package_versions", lambda: {})
    monkeypatch.setattr(environment, "_git_metadata", lambda root: (None, None, {}))
    monkeypatch.setattr(environment, "_pyroxide_metadata", lambda: ({}, {}))
    monkeypatch.setattr(environment, "_compiler_metadata", lambda: ({}, {}))
    monkeypatch.setattr(environment, "_artifact_metadata", lambda: ({}, {}))

    metadata = environment.collect_environment(
        psutil_module=FakePsutil(process),
        repository_root=tmp_path,
    )

    assert metadata.cpu_affinity is None
    assert "cpu_affinity" in metadata.unavailable
    assert "PermissionError" in metadata.unavailable["cpu_affinity"]
    assert metadata.git_sha is None
    assert metadata.git_dirty is None
    assert metadata.unavailable["git_sha"] == "git metadata unavailable"
    assert metadata.unavailable["git_dirty"] == "git metadata unavailable"


def test_process_tree_sampler_aggregates_resources_and_child_churn() -> None:
    """Dropping a child or a resource counter from a sample must fail this aggregate view."""
    root = FakeProcess(
        1,
        cpu_seconds=1.0,
        rss_bytes=100,
        voluntary_switches=2,
        involuntary_switches=3,
        file_descriptors=4,
    )
    first_child = FakeProcess(
        2,
        cpu_seconds=0.5,
        rss_bytes=200,
        voluntary_switches=5,
        involuntary_switches=7,
        file_descriptors=11,
    )
    second_child = FakeProcess(
        3,
        cpu_seconds=0.25,
        rss_bytes=300,
        voluntary_switches=13,
        involuntary_switches=17,
        file_descriptors=19,
    )
    root._children = [first_child]
    sampler = environment.ProcessTreeSampler(process=root)

    first = sampler.sample()
    root._children = [second_child]
    second = sampler.sample()

    assert first.cpu_time_seconds == 1.5
    assert first.rss_bytes == 300
    assert first.voluntary_context_switches == 7
    assert first.involuntary_context_switches == 10
    assert first.file_descriptors == 15
    assert first.children_total == 1
    assert first.children_started == 1
    assert first.children_exited == 0
    assert second.children_total == 1
    assert second.children_started == 1
    assert second.children_exited == 1
