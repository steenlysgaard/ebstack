from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from datetime import datetime
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
    selected_timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return stack.log_root / stack.cpu_arch / selected_timestamp


def print_items(title: str, items: tuple[str, ...]) -> None:
    console.print(f"\n{title}:")
    for item in items:
        console.print(f"  {item}")


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
