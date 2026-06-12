"""File-permission hardening for .state secrets (owner-only)."""
from __future__ import annotations

import os
import stat

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_ensure_state_dir_is_0700(cli_mod, bound):
    cli_mod.ensure_state_dir()
    assert cli_mod.STATE.is_dir()
    assert _mode(cli_mod.STATE) == 0o700


def test_ensure_state_dir_tightens_existing(cli_mod, bound):
    cli_mod.STATE.mkdir(parents=True, exist_ok=True)
    cli_mod.STATE.chmod(0o755)            # simulate a world-readable dir
    cli_mod.ensure_state_dir()
    assert _mode(cli_mod.STATE) == 0o700


def test_write_secret_is_0600(cli_mod, bound):
    cli_mod.ensure_state_dir()
    secret = cli_mod.STATE / "holder_key.b64"
    cli_mod.write_secret(secret, "deadbeef")
    assert secret.read_text() == "deadbeef"
    assert _mode(secret) == 0o600


def test_authorizer_mount_is_world_readable(cli_mod, bound):
    """Docker bind mount: container user must traverse and read gateway.yaml."""
    cli_mod.ensure_state_dir()
    cli_mod.GATEWAY.write_text("version: '1'\n")
    cli_mod.sync_authorizer_mount()
    mount = cli_mod.authorizer_mount_dir()
    staged = mount / cli_mod.GATEWAY.name
    assert staged.is_file()
    assert _mode(mount) == 0o755
    assert _mode(staged) == 0o644
