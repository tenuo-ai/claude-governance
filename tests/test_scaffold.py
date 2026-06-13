"""Tests for example policy scaffolding."""

from __future__ import annotations

import sys
import types

import pytest
import yaml

from tenuo_claude_code import cli
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


def test_find_project_root_tenuo_project_dir_without_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TENUO_PROJECT_DIR", str(tmp_path))
    assert find_project_root(fallback_cwd=True) == tmp_path.resolve()


def test_find_project_dir_env_requires_yaml_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TENUO_PROJECT_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="has no tenuo.yaml"):
        find_project_root(fallback_cwd=False)


def test_find_project_root_walks_up(tmp_path, monkeypatch):
    monkeypatch.delenv("TENUO_PROJECT_DIR", raising=False)
    project = tmp_path / "proj"
    nested = project / "sub"
    nested.mkdir(parents=True)
    (project / "tenuo.yaml").write_text("name: p\n")
    monkeypatch.chdir(nested)
    assert find_project_root() == project.resolve()


def test_write_advanced_profile_prefers_stable_approver_id(monkeypatch, tmp_path):
    path = tmp_path / "tenuo.advanced.yaml"
    monkeypatch.setattr(cli, "ADVANCED_PROFILE", path, raising=False)

    cli.write_advanced_profile(approver="Alice Example", approver_id="idn_123")

    data = yaml.safe_load(path.read_text())
    assert data["cloud"] == {"approver_identity_id": "idn_123"}


def test_write_advanced_profile_keeps_display_name_fallback(monkeypatch, tmp_path):
    path = tmp_path / "tenuo.advanced.yaml"
    monkeypatch.setattr(cli, "ADVANCED_PROFILE", path, raising=False)

    cli.write_advanced_profile(approver="Alice Example")

    data = yaml.safe_load(path.read_text())
    assert data["cloud"] == {"approver_identity": "Alice Example"}


def test_root_from_warrant_issuer(monkeypatch):
    class FakeIssuer:
        def to_bytes(self):
            return bytes.fromhex("ab" * 32)

    class FakeWarrant:
        issuer = FakeIssuer()

        @staticmethod
        def from_base64(warrant_b64):
            assert warrant_b64 == "WARRANT"
            return FakeWarrant()

    monkeypatch.setitem(sys.modules, "tenuo", types.SimpleNamespace(Warrant=FakeWarrant))

    assert cli.root_from_warrant_issuer("WARRANT") == "ab" * 32
