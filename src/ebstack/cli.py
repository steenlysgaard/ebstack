from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import dataclass
import datetime as dt
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .config import load_stack_config
from .easybuild import (
    collect_missing,
    dry_run_command,
    fetch_command,
    install_command,
    local_command,
    run_command,
)
from .errors import EbstackError
from .git_overlays import materialize_easyconfigs_prs
from .models import ResolvedStack
from .resolve import (
    CliOptions,
    add_positive_int_option,
    add_sbatch_option,
    parse_sbatch_spec,
    resolve_stack,
)

DEFAULT_REPO_URL = "https://github.com/easybuilders/easybuild-easyconfigs.git"

console = Console()
err_console = Console(stderr=True)
app = typer.Typer(no_args_is_help=True, add_completion=False)


OnlyOption = Annotated[
    str | None,
    typer.Option("--only", help="Restrict run to common plus one of: amd, gpu, intel."),
]
PartitionOption = Annotated[str | None, typer.Option("--partition", help="Slurm partition.")]
GresOption = Annotated[str | None, typer.Option("--gres", help="Slurm GRES specification.")]
AccountOption = Annotated[str | None, typer.Option("--account", help="Slurm account.")]
QosOption = Annotated[str | None, typer.Option("--qos", help="Slurm QOS.")]
MemOption = Annotated[str | None, typer.Option("--mem", help="Slurm memory per node.")]
MemPerCpuOption = Annotated[str | None, typer.Option("--mem-per-cpu", help="Slurm memory per CPU.")]
MemPerGpuOption = Annotated[str | None, typer.Option("--mem-per-gpu", help="Slurm memory per GPU.")]
SbatchOption = Annotated[
    list[str] | None,
    typer.Option("--sbatch", help="Generic Slurm NAME=VALUE environment override."),
]
JobCoresOption = Annotated[int | None, typer.Option("--job-cores", help="EasyBuild job cores.")]
JobWalltimeOption = Annotated[
    int | None,
    typer.Option("--job-max-walltime", help="EasyBuild max job walltime in hours."),
]
SinceOption = Annotated[
    str | None,
    typer.Option("--since", help="Only check logs modified since DATE or Nd."),
]
ArchOption = Annotated[
    str | None,
    typer.Option("--arch", help="Only check logs for CPU_ARCH."),
]
ShowSuccessOption = Annotated[
    bool,
    typer.Option("--show-success", help="Include successful builds in the report."),
]

LOG_FAILURE_PATTERN = re.compile(
    r"^\s*\* \[FAILED\]|^ERROR:|EasyBuild crashed|Build failed|FAILED",
    re.MULTILINE,
)
LOG_SUCCESS_PATTERN = re.compile(r"^\s*\* \[SUCCESS\]|Build succeeded", re.MULTILINE)
LOG_SUMMARY_PATTERN = re.compile(r"^\s*\* \[(SUCCESS|FAILED|SKIPPED)\] (.*)$", re.MULTILINE)
ARCH_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class AppState:
    config_path: Path


@dataclass(frozen=True)
class CommonArgs:
    only: str | None = None
    partition: str | None = None
    gres: str | None = None
    account: str | None = None
    qos: str | None = None
    mem: str | None = None
    mem_per_cpu: str | None = None
    mem_per_gpu: str | None = None
    sbatch: list[str] | None = None
    job_cores: int | None = None
    job_max_walltime: int | None = None


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            envvar="EBSTACK_CONFIG",
            help="Path to the stack config file. May also be set with EBSTACK_CONFIG.",
        ),
    ],
) -> None:
    ctx.obj = AppState(config_path=config)


def common_args(
    only: str | None,
    partition: str | None,
    gres: str | None,
    account: str | None,
    qos: str | None,
    mem: str | None,
    mem_per_cpu: str | None,
    mem_per_gpu: str | None,
    sbatch: list[str] | None,
    job_cores: int | None,
    job_max_walltime: int | None,
) -> CommonArgs:
    return CommonArgs(
        only=only,
        partition=partition,
        gres=gres,
        account=account,
        qos=qos,
        mem=mem,
        mem_per_cpu=mem_per_cpu,
        mem_per_gpu=mem_per_gpu,
        sbatch=sbatch,
        job_cores=job_cores,
        job_max_walltime=job_max_walltime,
    )


def only_args(only: str | None) -> CommonArgs:
    return common_args(
        only,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def to_cli_options(args: CommonArgs) -> CliOptions:
    options = CliOptions(only=args.only)

    if args.partition is not None:
        add_sbatch_option(options, "PARTITION", args.partition)
    if args.gres is not None:
        add_sbatch_option(options, "GRES", args.gres)
    if args.account is not None:
        add_sbatch_option(options, "ACCOUNT", args.account)
    if args.qos is not None:
        add_sbatch_option(options, "QOS", args.qos)
    if args.mem is not None:
        add_sbatch_option(options, "MEM_PER_NODE", args.mem)
    if args.mem_per_cpu is not None:
        add_sbatch_option(options, "MEM_PER_CPU", args.mem_per_cpu)
    if args.mem_per_gpu is not None:
        add_sbatch_option(options, "MEM_PER_GPU", args.mem_per_gpu)
    for spec in args.sbatch or []:
        name, value = parse_sbatch_spec(spec)
        add_sbatch_option(options, name, value)

    if args.job_cores is not None:
        add_positive_int_option(options, "--job-cores", args.job_cores)
    if args.job_max_walltime is not None:
        add_positive_int_option(options, "--job-max-walltime", args.job_max_walltime)

    return options


def resolve_for_command(
    ctx: typer.Context,
    args: CommonArgs,
    *,
    materialize_prs: bool,
) -> ResolvedStack:
    state = require_state(ctx)
    cpu_arch = require_cpu_arch()
    config = load_stack_config(state.config_path)
    stack = resolve_stack(config=config, cpu_arch=cpu_arch, cli_options=to_cli_options(args))

    if materialize_prs and stack.easyconfig_prs:
        repo_url = os.environ.get("EASYCONFIGS_REPO_URL", DEFAULT_REPO_URL)
        err_console.print(
            "Checking out EasyBuild easyconfig PRs: "
            f"{', '.join(stack.easyconfig_prs)}"
        )
        overlays = materialize_easyconfigs_prs(stack.easyconfig_prs, repo_url=repo_url)
        stack = resolve_stack(
            config=config,
            cpu_arch=cpu_arch,
            cli_options=to_cli_options(args),
            robot_overlays=overlays,
        )

    return stack


def run_resolved_command(
    ctx: typer.Context,
    args: CommonArgs,
    command_builder,
    *,
    use_sbatch_env: bool = False,
) -> None:
    stack = resolve_for_command(ctx, args, materialize_prs=True)
    sbatch_env = stack.sbatch_env if use_sbatch_env else ()
    exit_with_status(run_command(command_builder(stack), sbatch_env=sbatch_env))


def require_state(ctx: typer.Context) -> AppState:
    if not isinstance(ctx.obj, AppState):
        raise EbstackError("Internal error: missing CLI state")
    return ctx.obj


def require_cpu_arch() -> str:
    cpu_arch = os.environ.get("CPU_ARCH", "")
    if not cpu_arch:
        raise EbstackError("CPU_ARCH must be set")
    return cpu_arch


def shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def print_config(stack, *, command: list[str], label: str) -> None:
    console.print("EasyBuild stack configuration", style="bold")
    rows = [
        ("Config", str(stack.config_path)),
        ("Log root", str(stack.log_root)),
        ("CPU_ARCH", stack.cpu_arch),
        ("CPU vendor", stack.architecture.cpu_vendor),
        ("Stack", stack.architecture.stack),
        ("Layers", " ".join(stack.layers)),
        ("CUDA compute capabilities", ",".join(stack.architecture.cuda_compute_capabilities) or "none"),
        ("Easyconfigs", str(len(stack.easyconfigs))),
        ("Easyconfigs PRs", " ".join(stack.easyconfig_prs) if stack.easyconfig_prs else "none"),
        ("Execution", label),
    ]
    table = Table(show_header=False, box=None, padding=(0, 2))
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)

    print_items("Easyconfigs", stack.easyconfigs)
    display_options = [*stack.options, *stack.cuda_options, *stack.cli_easybuild_options]
    print_items("EasyBuild options", tuple(display_options))
    print_items("Easyconfig PR overlays", tuple(str(path) for path in stack.robot_overlays))
    print_items("Slurm environment", stack.sbatch_env)
    console.print("\nEasyBuild command:")
    console.print(f"  {shell_command(command)}")


def job_log_dir(stack, timestamp: str | None = None) -> Path:
    selected_timestamp = timestamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return stack.log_root / stack.cpu_arch / selected_timestamp


def print_items(title: str, items: tuple[str, ...]) -> None:
    console.print(f"\n{title}:")
    for item in items:
        console.print(f"  {item}")


def parse_since_epoch(value: str) -> float:
    match = re.fullmatch(r"([1-9][0-9]*)d", value)
    if match:
        return (dt.datetime.now() - dt.timedelta(days=int(match.group(1)))).timestamp()

    for date_format in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            parsed = dt.datetime.strptime(value, date_format)
        except ValueError:
            continue
        return time.mktime(parsed.timetuple())

    raise EbstackError(f"Could not parse --since value '{value}'")


def detect_log_status(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if LOG_FAILURE_PATTERN.search(content):
        return "FAILED"
    if LOG_SUCCESS_PATTERN.search(content):
        return "SUCCESS"
    return "UNKNOWN"


def detect_log_module(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    matches = LOG_SUMMARY_PATTERN.findall(content)
    if matches:
        return matches[-1][1]

    base = path.name
    for suffix in (".out", ".log"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return re.sub(r"-[0-9]*$", "", base)


def detect_log_arch(path: Path, log_root: Path) -> str:
    relative = path.relative_to(log_root)
    return relative.parts[0]


def find_log_files(search_root: Path, since_epoch: float) -> list[Path]:
    files = [
        path
        for path in search_root.rglob("*")
        if path.is_file()
        and path.suffix in {".out", ".log"}
        and path.stat().st_mtime >= since_epoch
    ]
    return sorted(files)


def exit_with_status(status: int) -> None:
    raise typer.Exit(status)


def handle_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except EbstackError as err:
            err_console.print(f"ERROR: {err}", style="bold red")
            raise typer.Exit(1) from err

    return wrapper


@app.command("show-config")
@handle_errors
def show_config(
    ctx: typer.Context,
    materialize_prs: Annotated[
        bool,
        typer.Option("--materialize-prs", help="Fetch PR overlays before printing command."),
    ] = False,
    only: OnlyOption = None,
    partition: PartitionOption = None,
    gres: GresOption = None,
    account: AccountOption = None,
    qos: QosOption = None,
    mem: MemOption = None,
    mem_per_cpu: MemPerCpuOption = None,
    mem_per_gpu: MemPerGpuOption = None,
    sbatch: SbatchOption = None,
    job_cores: JobCoresOption = None,
    job_max_walltime: JobWalltimeOption = None,
) -> None:
    args = common_args(
        only,
        partition,
        gres,
        account,
        qos,
        mem,
        mem_per_cpu,
        mem_per_gpu,
        sbatch,
        job_cores,
        job_max_walltime,
    )
    stack = resolve_for_command(ctx, args, materialize_prs=materialize_prs)
    print_config(
        stack,
        command=install_command(stack, job_log_dir(stack, "<timestamp>")),
        label="Slurm jobs",
    )


@app.command("dry-run")
@handle_errors
def dry_run(
    ctx: typer.Context,
    only: OnlyOption = None,
) -> None:
    args = only_args(only)
    run_resolved_command(ctx, args, dry_run_command)


@app.command("missing")
@handle_errors
def missing(
    ctx: typer.Context,
    only: OnlyOption = None,
) -> None:
    args = only_args(only)
    stack = resolve_for_command(ctx, args, materialize_prs=True)
    err_console.print("Checking installed modules with EasyBuild dry run...")
    missing_paths, missing_lines = collect_missing(stack)
    if not missing_paths:
        console.print("All selected easyconfigs and dependencies are already installed.")
        return
    for line in missing_lines:
        console.print(line)


@app.command("fetch-sources")
@handle_errors
def fetch_sources(
    ctx: typer.Context,
    only: OnlyOption = None,
) -> None:
    args = only_args(only)
    stack = resolve_for_command(ctx, args, materialize_prs=True)
    console.print("Planning source fetches from module-aware EasyBuild dry run...")
    missing_paths, _ = collect_missing(stack)
    if not missing_paths:
        console.print("All selected easyconfigs and dependencies are already installed; nothing to fetch.")
        return
    console.print("Fetching sources for missing easyconfigs and dependencies:")
    for path in missing_paths:
        console.print(f"  {path}")
    exit_with_status(run_command(fetch_command(stack, missing_paths)))


@app.command("check-logs")
@handle_errors
def check_logs(
    ctx: typer.Context,
    since: SinceOption = None,
    arch: ArchOption = None,
    show_success: ShowSuccessOption = False,
) -> None:
    state = require_state(ctx)
    config = load_stack_config(state.config_path)
    log_root = config.log_root

    if not log_root.is_dir():
        raise EbstackError(f"Log directory does not exist: {log_root}")

    if arch is not None:
        if not ARCH_PATTERN.fullmatch(arch):
            raise EbstackError(f"Invalid --arch value '{arch}'")
        search_root = log_root / arch
        if not search_root.is_dir():
            raise EbstackError(f"Log directory does not exist for --arch '{arch}': {search_root}")
    else:
        search_root = log_root

    since_epoch = parse_since_epoch(since) if since else 0
    log_files = find_log_files(search_root, since_epoch)

    console.print("EasyBuild log summary")
    console.print(f"  Log root:     {log_root}")
    if arch is not None:
        console.print(f"  CPU_ARCH:     {arch}")
    if since is not None:
        console.print(f"  Since:        {since}")
    console.print(f"  Logs checked: {len(log_files)}")
    console.print("\nSummary:")

    failed_count = 0
    success_count = 0
    unknown_count = 0
    printed_count = 0

    for path in log_files:
        status = detect_log_status(path)
        log_arch = detect_log_arch(path, log_root)
        module = detect_log_module(path)

        if status == "FAILED":
            failed_count += 1
        elif status == "SUCCESS":
            success_count += 1
        else:
            unknown_count += 1

        if status == "SUCCESS" and not show_success:
            continue

        console.print(f" * [{status}] {log_arch} {module} ({path})", soft_wrap=True)
        printed_count += 1

    if printed_count == 0:
        if show_success:
            console.print("No log files matched.")
        else:
            console.print("No failed or unknown builds found.")

    console.print(
        f"\nTotals: {failed_count} failed, {success_count} succeeded, {unknown_count} unknown"
    )

    if failed_count > 0 or unknown_count > 0:
        exit_with_status(1)


@app.command("install")
@handle_errors
def install(
    ctx: typer.Context,
    only: OnlyOption = None,
    partition: PartitionOption = None,
    gres: GresOption = None,
    account: AccountOption = None,
    qos: QosOption = None,
    mem: MemOption = None,
    mem_per_cpu: MemPerCpuOption = None,
    mem_per_gpu: MemPerGpuOption = None,
    sbatch: SbatchOption = None,
    job_cores: JobCoresOption = None,
    job_max_walltime: JobWalltimeOption = None,
) -> None:
    args = common_args(
        only,
        partition,
        gres,
        account,
        qos,
        mem,
        mem_per_cpu,
        mem_per_gpu,
        sbatch,
        job_cores,
        job_max_walltime,
    )
    stack = resolve_for_command(ctx, args, materialize_prs=True)
    selected_job_log_dir = job_log_dir(stack)
    selected_job_log_dir.mkdir(parents=True, exist_ok=True)
    exit_with_status(
        run_command(install_command(stack, selected_job_log_dir), sbatch_env=stack.sbatch_env)
    )


@app.command("local")
@handle_errors
def local(
    ctx: typer.Context,
    only: OnlyOption = None,
) -> None:
    args = only_args(only)
    run_resolved_command(ctx, args, local_command)


if __name__ == "__main__":
    app()
