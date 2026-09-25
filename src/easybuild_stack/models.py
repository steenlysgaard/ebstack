from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


VALID_LAYERS = {"common", "intel", "amd", "gpu"}
VALID_CPU_VENDORS = {"intel", "amd"}
VALID_STACKS = {"cpu", "gpu"}
VALID_JOB_KEYS = {
    "account",
    "gres",
    "job_cores",
    "job_max_walltime",
    "mem",
    "mem_per_cpu",
    "mem_per_gpu",
    "partition",
    "qos",
}


@dataclass(frozen=True)
class Architecture:
    name: str
    cpu_vendor: str
    stack: str
    cuda_compute_capabilities: tuple[str, ...]
    jobs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Layer:
    name: str
    easyconfigs: tuple[str, ...]
    options: tuple[str, ...]


@dataclass(frozen=True)
class StackConfig:
    path: Path
    log_root: Path
    defaults_jobs: dict[str, str]
    architectures: dict[str, Architecture]
    layers: dict[str, Layer]


@dataclass(frozen=True)
class ResolvedStack:
    config_path: Path
    log_root: Path
    cpu_arch: str
    architecture: Architecture
    layers: tuple[str, ...]
    easyconfigs: tuple[str, ...]
    options: tuple[str, ...]
    cuda_options: tuple[str, ...]
    cli_easybuild_options: tuple[str, ...]
    sbatch_env: tuple[str, ...]
    easyconfig_prs: tuple[str, ...]
    robot_overlays: tuple[Path, ...]
    robot_options: tuple[str, ...]
