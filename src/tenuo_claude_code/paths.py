"""Project root discovery and path constants for tenuo-claude-code."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "ADMIN_COMMAND",
    "ADMIN_COMMAND_LEGACY",
    "BIN_LAUNCHER",
    "CLI_COMMAND",
    "CLI_COMMAND_LEGACY",
    "PACKAGE_DIR",
    "bind_project_paths",
    "find_project_root",
    "harness_tools_file",
]

PACKAGE_DIR = Path(__file__).resolve().parent

CLI_COMMAND = "tenuo-claude-code"
CLI_COMMAND_LEGACY = "tenuo-claude"
ADMIN_COMMAND = "tenuo-claude-admin"
ADMIN_COMMAND_LEGACY = "tenuo-admin"
BIN_LAUNCHER = "tenuo-claude-code"


def find_project_root(start: Path | None = None) -> Path:
    """Locate the directory containing ``tenuo.yaml`` (the governed project).

    Search order: ``TENUO_PROJECT_DIR`` env → walk up from *start* or cwd.
    """
    env = os.environ.get("TENUO_PROJECT_DIR", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if not (root / "tenuo.yaml").is_file():
            raise SystemExit(f"TENUO_PROJECT_DIR={env!r} has no tenuo.yaml")
        return root
    cur = (start or Path.cwd()).resolve()
    for directory in (cur, *cur.parents):
        if (directory / "tenuo.yaml").is_file():
            return directory
    raise SystemExit(
        "No tenuo.yaml in this directory or any parent — "
        "cd to your governed project or set TENUO_PROJECT_DIR."
    )


def harness_tools_file() -> Path:
    """Bundled harness tool list (package data)."""
    bundled = PACKAGE_DIR / "data" / "harness_tools.yaml"
    if bundled.is_file():
        return bundled
    # Editable install from demo repo: fall back to repo-root copy if present.
    legacy = PACKAGE_DIR.parent.parent / "harness_tools.yaml"
    if legacy.is_file():
        return legacy
    return bundled


def bind_project_paths(module) -> None:
    """Bind project-scoped path globals on *module* (typically ``cli``)."""
    root = find_project_root()
    module.DEMO_DIR = root
    module.STATE = root / ".state"
    module.CONFIG_FILE = root / "tenuo.yaml"
    module.CLOUD_PROFILE = root / "tenuo.cloud.yaml"
    module.ADVANCED_PROFILE = root / "tenuo.advanced.yaml"
    module.CLOUD_ENV_EXAMPLE = root / "cloud.env.example"
    module.LAUNCHER = root / "bin" / BIN_LAUNCHER
    module.LAUNCHER_REL = f"./bin/{BIN_LAUNCHER}"
    module.HARNESS_TOOLS_FILE = harness_tools_file()
    module.HOLDER_KEY = module.STATE / "holder_key.b64"
    module.ISSUER_KEY = module.STATE / "issuer_key.b64"
    module.ISSUER_PUB = module.STATE / "issuer_pub.hex"
    module.WARRANT = module.STATE / "warrant.b64"
    module.STATE_JSON = module.STATE / "state.json"
    module.GATEWAY = module.STATE / "gateway.yaml"
    module.SRL = module.STATE / "srl.cbor"
    module.RECEIPTS = module.STATE / "receipts.jsonl"
    module.CLOUD_ENV = module.STATE / "cloud.env"
    module.CLOUD_STATE = module.STATE / "cloud_state.json"
    module.AGENTS_DIRS = (root / ".claude" / "agents", Path.home() / ".claude" / "agents")
