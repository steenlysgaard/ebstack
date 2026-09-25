from typer.testing import CliRunner

from ebstack.cli import app


def test_config_is_required_without_env_var() -> None:
    result = CliRunner().invoke(app, ["show-config"], env={"EBSTACK_CONFIG": ""})

    assert result.exit_code == 2
    assert "Missing option '--config'" in result.output
    assert "EBSTACK_CONFIG" in result.output
