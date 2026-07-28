"""Run the pinned Odoo add-on correctness gate inside its container."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


def build_correctness_command(
    *,
    odoo_root: Path,
    addons_root: Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Build the fixed, add-on-scoped Odoo correctness command."""
    return _build_command(
        odoo_root=odoo_root,
        addons_root=addons_root,
        test_tag="pyroxide_benchmark,-pyroxide_benchmark_performance",
        environment=environment,
    )


def build_performance_command(
    *,
    odoo_root: Path,
    addons_root: Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Build the explicit command that selects only the timed Odoo driver."""
    return _build_command(
        odoo_root=odoo_root,
        addons_root=addons_root,
        test_tag="pyroxide_benchmark_performance",
        environment=environment,
    )


def _build_command(
    *,
    odoo_root: Path,
    addons_root: Path,
    test_tag: str,
    environment: Mapping[str, str] | None,
) -> tuple[str, ...]:
    values = os.environ if environment is None else environment
    database = values.get("ODOO_DATABASE", "odoo_benchmark")
    database_host = values.get("ODOO_DATABASE_HOST", "postgres")
    database_port = values.get("ODOO_DATABASE_PORT", "5432")
    database_user = values.get("ODOO_DATABASE_USER", "odoo")
    database_password = values.get("ODOO_DATABASE_PASSWORD", "odoo")
    return (
        "python",
        str(odoo_root / "odoo-bin"),
        f"--database={database}",
        f"--db_host={database_host}",
        f"--db_port={database_port}",
        f"--db_user={database_user}",
        f"--db_password={database_password}",
        f"--addons-path={addons_root},{odoo_root / 'addons'}",
        "--init=pyroxide_benchmark",
        "--test-enable",
        f"--test-tags={test_tag}",
        "--stop-after-init",
        "--workers=0",
        "--without-demo=all",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one allow-listed correctness or performance entry point."""
    if argv:
        raise SystemExit("container entrypoint does not accept arbitrary commands")
    mode = os.environ.get("PYROXIDE_ODOO_MODE", "correctness")
    if mode == "correctness":
        builder = build_correctness_command
    elif mode in {"performance", "performance_recycling"}:
        builder = build_performance_command
    else:
        raise SystemExit(f"unsupported PYROXIDE_ODOO_MODE: {mode}")
    command = builder(odoo_root=Path("/opt/odoo"), addons_root=Path("/workspace/addons"))
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(None, ("/opt/pyroxide-source", os.environ.get("PYTHONPATH")))
        ),
    }
    return subprocess.run(command, check=False, env=environment).returncode


if __name__ == "__main__":  # pragma: no cover - container entry point.
    raise SystemExit(main())
