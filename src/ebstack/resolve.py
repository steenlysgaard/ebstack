from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import EbstackError
from .models import Architecture, ResolvedStack, StackConfig

CONTROLLED_SBATCH = {
    "DEPENDENCY",
    "EXPORT",
    "HOLD",
    "JOB_NAME",
    "NODES",
    "NTASKS",
    "OUTPUT",
    "TIME",
    "TIMELIMIT",
}


@dataclass
class CliOptions:
    only: str | None = None
    sbatch: list[tuple[str, str]] = field(default_factory=list)
    easybuild: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class JobDefaults:
    sbatch: tuple[tuple[str, str], ...]
    easybuild: tuple[tuple[str, str], ...]


def add_sbatch_option(
    options: CliOptions, name: str, value: str, *, track_cli: bool = True
) -> None:
    normalized = normalize_sbatch_name(name)
    if normalized in CONTROLLED_SBATCH:
        raise EbstackError(f"SBATCH_{normalized} is controlled by EasyBuild's Slurm backend")
    if not value:
        raise EbstackError(f"Empty value for SBATCH_{normalized}")
    options.sbatch.append((normalized, value))


def add_positive_int_option(
    options: CliOptions, option: str, value: int | str, *, track_cli: bool = True
) -> None:
    text = str(value)
    if not text.isdigit() or int(text) <= 0:
        raise EbstackError(f"{option} expects a positive integer")
    options.easybuild.append((option, text))


def normalize_sbatch_name(name: str) -> str:
    normalized = name.upper().replace("-", "_")
    if normalized.startswith("SBATCH_"):
        normalized = normalized[len("SBATCH_") :]
    if normalized == "MEM":
        normalized = "MEM_PER_NODE"
    if not normalized.replace("_", "").isalnum():
        raise EbstackError(f"Invalid SBATCH variable '{normalized}'")
    return normalized


def parse_sbatch_spec(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise EbstackError("--sbatch expects NAME=VALUE")
    name, value = spec.split("=", 1)
    return normalize_sbatch_name(name), value


def merge_jobs(config: StackConfig, architecture: Architecture) -> JobDefaults:
    jobs = dict(config.defaults_jobs)
    jobs.update(architecture.jobs)

    sbatch: list[tuple[str, str]] = []
    easybuild: list[tuple[str, str]] = []
    for key, value in jobs.items():
        if key == "job_cores":
            easybuild.append(("--job-cores", value))
        elif key == "job_max_walltime":
            easybuild.append(("--job-max-walltime", value))
        elif key == "partition":
            sbatch.append(("PARTITION", value))
        elif key == "gres":
            sbatch.append(("GRES", value))
        elif key == "account":
            sbatch.append(("ACCOUNT", value))
        elif key == "qos":
            sbatch.append(("QOS", value))
        elif key == "mem":
            sbatch.append(("MEM_PER_NODE", value))
        elif key == "mem_per_cpu":
            sbatch.append(("MEM_PER_CPU", value))
        elif key == "mem_per_gpu":
            sbatch.append(("MEM_PER_GPU", value))
        else:
            raise EbstackError(f"Internal error: unknown job default '{key}'")

    return JobDefaults(tuple(sbatch), tuple(easybuild))


def resolve_stack(
    *,
    config: StackConfig,
    cpu_arch: str,
    cli_options: CliOptions,
    robot_overlays: tuple[Path, ...] = (),
) -> ResolvedStack:
    architecture = config.architectures.get(cpu_arch)
    if architecture is None:
        raise EbstackError(f"CPU_ARCH '{cpu_arch}' must appear in architectures")

    layers = select_layers(architecture, cli_options.only)
    easyconfigs: list[str] = []
    extra_options: list[str] = []
    for layer_name in layers:
        layer = config.layers[layer_name]
        easyconfigs.extend(layer.easyconfigs)
        extra_options.extend(layer.options)

    easyconfig_prs, extra_options = extract_from_pr_options(extra_options)
    extra_options = normalize_accept_eula_options(extra_options)

    cuda_options: list[str] = []
    if "gpu" in layers:
        cuda_options.append(
            "--cuda-compute-capabilities="
            + ",".join(architecture.cuda_compute_capabilities)
        )

    job_defaults = merge_jobs(config, architecture)
    sbatch_env = merged_sbatch_env(job_defaults.sbatch, cli_options.sbatch)
    cli_easybuild_options = merged_easybuild_options(
        job_defaults.easybuild, cli_options.easybuild
    )
    robot_options = build_robot_options(robot_overlays)

    return ResolvedStack(
        config_path=config.path,
        log_root=config.log_root,
        cpu_arch=cpu_arch,
        architecture=architecture,
        layers=tuple(layers),
        easyconfigs=tuple(easyconfigs),
        options=tuple(extra_options),
        cuda_options=tuple(cuda_options),
        cli_easybuild_options=tuple(cli_easybuild_options),
        sbatch_env=tuple(sbatch_env),
        easyconfig_prs=tuple(easyconfig_prs),
        robot_overlays=robot_overlays,
        robot_options=tuple(robot_options),
    )


def select_layers(architecture: Architecture, only: str | None) -> list[str]:
    if only is not None and only not in {"amd", "gpu", "intel"}:
        raise EbstackError("--only expects one of: amd, gpu, intel")

    if only == "intel":
        if architecture.cpu_vendor != "intel":
            raise EbstackError("--only intel requires an Intel CPU_ARCH")
        return ["common", "intel"]
    if only == "amd":
        if architecture.cpu_vendor != "amd":
            raise EbstackError("--only amd requires an AMD CPU_ARCH")
        return ["common", "amd"]
    if only == "gpu":
        if architecture.stack != "gpu":
            raise EbstackError("--only gpu requires a GPU CPU_ARCH")
        return ["common", "gpu"]

    layers = ["common", architecture.cpu_vendor]
    if architecture.stack == "gpu":
        layers.append("gpu")
    return layers


def extract_from_pr_options(options: list[str]) -> tuple[list[str], list[str]]:
    prs: list[str] = []
    filtered: list[str] = []
    for option in options:
        if option.startswith("--from-pr="):
            pr = option.split("=", 1)[1]
            if not pr.isdigit() or int(pr) <= 0:
                raise EbstackError("--from-pr expects a positive integer")
            prs.append(pr)
        elif option == "--from-pr":
            raise EbstackError("Use --from-pr=<PR> in EasyBuild options")
        else:
            filtered.append(option)
    return prs, filtered


def normalize_accept_eula_options(options: list[str]) -> list[str]:
    filtered: list[str] = []
    eulas: list[str] = []
    seen: set[str] = set()

    for option in options:
        if option.startswith("--accept-eula-for="):
            value = option.split("=", 1)[1]
            for eula in value.split(","):
                item = eula.strip()
                if item and item not in seen:
                    seen.add(item)
                    eulas.append(item)
        elif option == "--accept-eula-for":
            raise EbstackError("Use --accept-eula-for=<name>[,<name>...] in options")
        else:
            filtered.append(option)

    if eulas:
        filtered.append("--accept-eula-for=" + ",".join(eulas))
    return filtered


def merged_sbatch_env(
    defaults: tuple[tuple[str, str], ...], cli: list[tuple[str, str]]
) -> list[str]:
    cli_names = {name for name, _ in cli}
    merged = [(name, value) for name, value in defaults if name not in cli_names]
    merged.extend(cli)
    return [f"SBATCH_{name}={value}" for name, value in merged]


def merged_easybuild_options(
    defaults: tuple[tuple[str, str], ...], cli: list[tuple[str, str]]
) -> list[str]:
    cli_names = {name for name, _ in cli}
    merged = [(name, value) for name, value in defaults if name not in cli_names]
    merged.extend(cli)
    return [f"{name}={value}" for name, value in merged]


def build_robot_options(robot_overlays: tuple[Path, ...]) -> list[str]:
    if robot_overlays:
        return ["--robot=" + ":".join(str(path) for path in robot_overlays)]
    return ["--robot"]
