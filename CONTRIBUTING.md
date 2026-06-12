# Contributing

Apache-2.0 — see [LICENSE](LICENSE). Bug reports and PRs welcome.

## Getting started

```bash
git clone https://github.com/tenuo-ai/claude-governance.git
cd claude-governance
uv venv && uv sync && chmod +x bin/tenuo-claude
cd demo && uv run tenuo-claude verify   # reference project with sample tenuo.yaml
```

Project discovery: `tenuo.yaml` in cwd or any parent, or set `TENUO_PROJECT_DIR`.
Re-run `init` or `refresh` after switching Python venvs (hooks pin `sys.executable`).

## Pull requests

1. Fork and branch from `main`.
2. Keep changes focused; match existing style in `src/tenuo_claude_code/`.
3. Run `cd demo && tenuo-claude verify` if you touch enforcement or policy wiring.
4. Do not commit `.state/`, real API keys, or home-directory paths.

Security issues: see [SECURITY.md](SECURITY.md) — no public issues for vulnerabilities.

## Never commit secrets

| Path | Contains |
|------|----------|
| `.state/` | Holder/issuer keys, warrant, receipts, `cloud.env` |
| `.claude/settings.json` | Generated hook wiring |
| `~/.tenuo/admin.env` | Admin key for `tenuo-admin setup` only |

Safe to commit: source, `demo/` (sample policy and fake `prod-credentials.env` decoy),
examples, and templates (`*.example`).

Before pushing, scan staged diffs for bearer tokens (`tc_…`, `tenuo_ct_…`) and paths
under `/Users/` or `C:\`.

## Code layout

| Module | Role |
|--------|------|
| `src/tenuo_claude_code/cli.py` | Policy, hooks, MCP proxy, lifecycle commands |
| `src/tenuo_claude_code/admin.py` | One-time Cloud setup (`tenuo-admin`) |
| `src/tenuo_claude_code/verify.py` | Policy-driven authorizer self-test |
| `src/tenuo_claude_code/data/harness_tools.yaml` | Bundled audit-allow tool list |

Root `tenuo_claude.py` / `tenuo_admin.py` are thin shims; prefer `tenuo-claude` on PATH.

When Claude Code adds new inert harness tools, append them to
`src/tenuo_claude_code/data/harness_tools.yaml` and run `verify`.

## Releases (maintainers)

PyPI package: **`tenuo-claude-code`**. Tag `v*` triggers
[`.github/workflows/release.yml`](.github/workflows/release.yml) (trusted publishing
or `PYPI_TOKEN` secret).

Bump `version` in `pyproject.toml` and `src/tenuo_claude_code/__init__.py`, then tag.

## Reference demo

Presentation runbook and sample fixtures: [demo/](demo/).
