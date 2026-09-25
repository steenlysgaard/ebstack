from typer.testing import CliRunner

from ebstack.cli import app, parse_since_epoch


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
