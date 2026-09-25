from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import EbstackError
from .models import ResolvedStack


def dry_run_command(stack: ResolvedStack) -> list[str]:
    return [
        "eb",
        *stack.easyconfigs,
        *stack.options,
        *stack.cuda_options,
        *stack.cli_easybuild_options,
        *stack.robot_options,
        "--dry-run",
    ]


def missing_command(stack: ResolvedStack) -> list[str]:
    return [
        "eb",
        *stack.easyconfigs,
        *stack.options,
        *stack.cuda_options,
        *stack.cli_easybuild_options,
        *stack.robot_options,
        "--dry-run",
        "--ignore-osdeps",
    ]


def fetch_command(stack: ResolvedStack, easyconfigs: tuple[str, ...] | None = None) -> list[str]:
    selected = easyconfigs if easyconfigs is not None else stack.easyconfigs
    return [
        "eb",
        *selected,
        *stack.options,
        *stack.cuda_options,
        *stack.cli_easybuild_options,
        "--fetch-all",
    ]


def local_command(stack: ResolvedStack) -> list[str]:
    return [
        "eb",
        *stack.easyconfigs,
        *stack.options,
        *stack.cuda_options,
        *stack.cli_easybuild_options,
        *stack.robot_options,
    ]


def install_command(stack: ResolvedStack, job_log_dir: Path) -> list[str]:
    return [
        "eb",
        *stack.easyconfigs,
        *stack.options,
        *stack.cuda_options,
        *stack.cli_easybuild_options,
        *stack.robot_options,
        "--job",
        "--job-backend=Slurm",
        "--job-deps-type=abort_on_error",
        f"--job-output-dir={job_log_dir}",
    ]


def run_command(command: list[str], *, sbatch_env: tuple[str, ...] = ()) -> int:
    env = os.environ.copy()
    for item in sbatch_env:
        name, value = item.split("=", 1)
        env[name] = value
    try:
        completed = subprocess.run(command, env=env, check=False)
    except OSError as err:
        raise EbstackError(f"Failed to run {command[0]}: {err}") from err
    return completed.returncode


def collect_missing(stack: ResolvedStack) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        completed = subprocess.run(
            missing_command(stack),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as err:
        raise EbstackError(f"Failed to run eb: {err}") from err

    if completed.returncode != 0:
        raise EbstackError(
            "Failed to determine installed modules\n" + completed.stdout.rstrip()
        )

    missing_paths: list[str] = []
    missing_lines: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.startswith(" * [") or len(line) < 7:
            continue
        status = line[4]
        spec = line[7:].split(" (module: ", 1)[0]
        if status == "x":
            continue
        if status in {" ", "F", "R"}:
            missing_paths.append(spec)
            missing_lines.append(line)

    return tuple(missing_paths), tuple(missing_lines)

