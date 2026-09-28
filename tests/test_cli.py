from typer.testing import CliRunner

from ebstack.cli import CommonArgs, app, parse_since_epoch, to_cli_options
from ebstack.models import Architecture, StackConfig
from ebstack.resolve import build_robot_options, merge_jobs


def write_config(tmp_path):
    config_path = tmp_path / "ebstack.yaml"
    config_path.write_text(
        """
defaults:
  paths:
    log_root: logs
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


def test_config_is_required_without_env_var() -> None:
    result = CliRunner().invoke(app, ["show-config"], env={"EBSTACK_CONFIG": ""})

    assert result.exit_code == 2
    assert "Missing option '--config'" in result.output
    assert "EBSTACK_CONFIG" in result.output


def test_parse_since_epoch_accepts_documented_absolute_formats() -> None:
    assert parse_since_epoch("2026-09-01") == parse_since_epoch("2026-09-01 00:00")


def test_parse_since_epoch_accepts_relative_days() -> None:
    before = parse_since_epoch("1d")
    after = parse_since_epoch("1d")

    assert after >= before


def test_to_cli_options_uses_registered_sbatch_and_easybuild_options() -> None:
    options = to_cli_options(
        CommonArgs(
            only="gpu",
            partition="gpu-build",
            mem="128G",
            sbatch=["constraint=zen4"],
            job_cores=16,
            job_max_walltime=12,
        )
    )

    assert options.only == "gpu"
    assert options.sbatch == [
        ("PARTITION", "gpu-build"),
        ("MEM_PER_NODE", "128G"),
        ("CONSTRAINT", "zen4"),
    ]
    assert options.easybuild == [
        ("--job-cores", "16"),
        ("--job-max-walltime", "12"),
    ]


def test_merge_jobs_uses_registered_job_targets(tmp_path) -> None:
    config = StackConfig(
        path=tmp_path / "ebstack.yaml",
        log_root=tmp_path / "logs",
        defaults_jobs={
            "partition": "default-build",
            "mem": "64G",
            "job_cores": "8",
        },
        architectures={},
        layers={},
    )
    architecture = Architecture(
        name="zen4",
        cpu_vendor="amd",
        stack="cpu",
        cuda_compute_capabilities=(),
        jobs={"partition": "zen4-build", "job_max_walltime": "12"},
    )

    jobs = merge_jobs(config, architecture)

    assert jobs.sbatch == (
        ("PARTITION", "zen4-build"),
        ("MEM_PER_NODE", "64G"),
    )
    assert jobs.easybuild == (
        ("--job-cores", "8"),
        ("--job-max-walltime", "12"),
    )


def test_robot_options_keep_easybuild_robot_paths_without_overlays(monkeypatch) -> None:
    monkeypatch.setenv("EASYBUILD_ROBOT_PATHS", "/existing/a:/existing/b")

    assert build_robot_options(()) == ["--robot"]


def test_robot_options_prepend_pr_overlays_with_trailing_colon(monkeypatch, tmp_path) -> None:
    overlay = tmp_path / "overlay" / "easybuild" / "easyconfigs"
    monkeypatch.setenv("EASYBUILD_ROBOT_PATHS", "/existing/a:/existing/b")

    assert build_robot_options((overlay,)) == ["--robot=" + str(overlay) + ":"]


def test_check_logs_reports_failed_and_unknown_logs(tmp_path) -> None:
    config_path = write_config(tmp_path)
    log_dir = tmp_path / "logs" / "skylake_el9"
    log_dir.mkdir(parents=True)
    (log_dir / "zlib-1.3-123.out").write_text("* [SUCCESS] zlib-1.3\n", encoding="utf-8")
    (log_dir / "hdf5-1.14-456.out").write_text("Build failed\n", encoding="utf-8")
    (log_dir / "openmpi-789.log").write_text("still running\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["--config", str(config_path), "check-logs"])

    assert result.exit_code == 1
    assert "Logs checked: 3" in result.output
    assert " * [FAILED] skylake_el9 hdf5-1.14 (" in result.output
    assert " * [UNKNOWN] skylake_el9 openmpi (" in result.output
    assert " * [SUCCESS]" not in result.output
    assert "Totals: 1 failed, 1 succeeded, 1 unknown" in result.output


def test_check_logs_can_show_successful_logs(tmp_path) -> None:
    config_path = write_config(tmp_path)
    log_dir = tmp_path / "logs" / "skylake_el9"
    log_dir.mkdir(parents=True)
    (log_dir / "zlib-1.3-123.out").write_text("* [SUCCESS] zlib-1.3\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["--config", str(config_path), "check-logs", "--arch", "skylake_el9", "--show-success"],
    )

    assert result.exit_code == 0
    assert "CPU_ARCH:     skylake_el9" in result.output
    assert " * [SUCCESS] skylake_el9 zlib-1.3 (" in result.output
    assert "Totals: 0 failed, 1 succeeded, 0 unknown" in result.output
