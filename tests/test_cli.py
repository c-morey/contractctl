"""The command line, end to end on fresh projects."""
import subprocess
import sys

import pytest

from contractctl import __version__, scaffold


def run(*args, cwd):
    return subprocess.run([sys.executable, "-m", "contractctl", *args], cwd=cwd, capture_output=True, text=True)


def test_version():
    assert __version__ in run("--version", cwd=".").stdout


def test_init_creates_an_empty_project(tmp_path):
    out = run("init", "lake", "--catalog", "analytics", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    project = tmp_path / "lake"
    for rel in ("contractctl.yml", "databricks.yml", ".gitignore", ".github/workflows/data-contracts.yml",
                ".vscode/settings.json", "platform/00_catalog_and_schemas.sql", "AGENTS.md"):
        assert (project / rel).is_file(), rel
    assert "CREATE CATALOG IF NOT EXISTS analytics" in (project / "platform/00_catalog_and_schemas.sql").read_text()
    assert "name: lake" in (project / "databricks.yml").read_text()
    assert f"contractctl=={__version__}" in (project / ".github/workflows/data-contracts.yml").read_text()
    text = "".join(p.read_text() for p in project.rglob("*") if p.is_file())
    assert not any(ph in text for ph in ("{{catalog}}", "{{bundle_name}}", "{{version}}"))
    assert not (project / "data_products/sales").exists()
    assert run("lint", cwd=project).returncode == 0
    assert run("check", cwd=project).returncode == 0


def test_init_with_example_is_ready_to_deploy(tmp_path):
    out = run("init", "--catalog", "analytics", "--example", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "resources/generated/sales.yml").is_file()
    assert (tmp_path / "generated/pipelines/sales/silver/orders.sql").is_file()
    check = run("check", cwd=tmp_path)
    assert check.returncode == 0 and "9 generated files match 2 contracts" in check.stdout


def test_init_never_overwrites_without_force(tmp_path):
    scaffold.init(tmp_path, "analytics")
    (tmp_path / "contractctl.yml").write_text("# mine\n")
    out = run("init", "--catalog", "analytics", cwd=tmp_path)
    assert out.returncode != 0 and "contractctl.yml" in out.stderr
    assert (tmp_path / "contractctl.yml").read_text() == "# mine\n"


def test_init_rejects_an_invalid_catalog(tmp_path):
    assert run("init", "--catalog", "My-Catalog", cwd=tmp_path).returncode != 0


def test_commands_outside_a_project_explain_what_to_do(tmp_path):
    out = run("lint", cwd=tmp_path)
    assert out.returncode != 0 and "contractctl init" in out.stderr


def test_check_fails_when_a_contract_changed_without_generate(tmp_path):
    scaffold.init(tmp_path, "analytics", example=True)
    run("generate", cwd=tmp_path)
    contract = tmp_path / "data_products/sales/orders/orders.odcs.yaml"
    contract.write_text(contract.read_text().replace("version: 1.0.0", "version: 1.0.1"))
    out = run("check", cwd=tmp_path)
    assert out.returncode != 0 and "out of date" in out.stdout


def test_diff_against_git_blocks_a_breaking_change_without_major_bump(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not available")
    scaffold.init(tmp_path, "analytics", example=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=tmp_path, check=True)
    contract = tmp_path / "data_products/sales/orders/orders.odcs.yaml"
    text = contract.read_text().replace("version: 1.0.0", "version: 1.1.0")
    contract.write_text(text.replace("        required: true\n        quality:\n          - type: custom\n"
                                     "            engine: lakeflow-expectations\n            name: valid_order_status",
                                     "        quality:\n          - type: custom\n"
                                     "            engine: lakeflow-expectations\n            name: valid_order_status"))
    out = run("diff", "--base", "main", cwd=tmp_path)
    assert out.returncode != 0
    assert "BREAKING" in out.stdout and "IMPACT    urn:datacontract:sales:daily_revenue" in out.stdout



def test_setup_and_generated_sql_run_as_is(tmp_path):
    """No <placeholder> a user must replace before running a SQL file."""
    import re
    scaffold.init(tmp_path, "analytics", example=True)
    run("generate", cwd=tmp_path)
    for path in [*tmp_path.glob("platform/*.sql"), *tmp_path.glob("generated/**/*.sql")]:
        code = "\n".join(line.split("--")[0] for line in path.read_text().splitlines())
        assert not re.search(r"<[a-z_]+>", code), path
