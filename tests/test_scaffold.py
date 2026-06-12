"""Tests for example policy scaffolding."""

from __future__ import annotations

import pytest
import yaml

from tenuo_claude_code.paths import (
    bundled_template,
    default_policy_name,
    find_project_root,
    scaffold_example_policy,
)


def test_bundled_template_exists():
    path = bundled_template("tenuo.yaml.example")
    assert path.is_file()
    assert "enforce" in path.read_text(encoding="utf-8")


def test_scaffold_writes_example_policy(tmp_path):
    project = tmp_path / "my-app"
    project.mkdir()
    created = scaffold_example_policy(project)
    assert created is True
    policy = project / "tenuo.yaml"
    assert policy.is_file()
    text = policy.read_text(encoding="utf-8")
    assert "Example policy" in text
    assert yaml.safe_load(text)["name"] == "my-app"
    assert (project / "workspace").is_dir()


@pytest.mark.parametrize(
    ("dirname", "expected"),
    [
        ("acme-backend", "acme-backend"),
        ("My Project", "my-project"),
        ("foo__bar", "foo-bar"),
        ("...", "tenuo-claude"),
    ],
)
def test_default_policy_name(dirname, expected, tmp_path):
    path = tmp_path / dirname
    path.mkdir()
    assert default_policy_name(path) == expected


def test_scaffold_uses_directory_name(tmp_path):
    project = tmp_path / "Acme_Backend"
    project.mkdir()
    scaffold_example_policy(project)
    assert yaml.safe_load((project / "tenuo.yaml").read_text())["name"] == "acme-backend"


def test_scaffold_no_op_when_policy_exists(tmp_path):
    (tmp_path / "tenuo.yaml").write_text("name: existing\n")
    assert scaffold_example_policy(tmp_path) is False


def test_scaffold_no_scaffold_raises(tmp_path):
    with pytest.raises(SystemExit, match="Missing tenuo.yaml"):
        scaffold_example_policy(tmp_path, no_scaffold=True)


def test_find_project_root_fallback_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("TENUO_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert find_project_root(fallback_cwd=True) == tmp_path.resolve()


def test_find_project_root_walks_up(tmp_path, monkeypatch):
    monkeypatch.delenv("TENUO_PROJECT_DIR", raising=False)
    project = tmp_path / "proj"
    nested = project / "sub"
    nested.mkdir(parents=True)
    (project / "tenuo.yaml").write_text("name: p\n")
    monkeypatch.chdir(nested)
    assert find_project_root() == project.resolve()
