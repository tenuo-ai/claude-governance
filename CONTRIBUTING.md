# Contributing / maintainer notes

Internal scaffolding for the standalone repo — not required for running the demo.

## Python environment

Use a project venv so the launcher resolves the same interpreter everywhere:

```bash
uv venv && uv sync && chmod +x bin/tenuo-claude
./bin/tenuo-claude init   # or: uv run tenuo-claude init
```

Re-run `init` or `refresh` after switching venvs. `.venv/` is gitignored.

Project discovery: `tenuo.yaml` in cwd or any parent, or set `TENUO_PROJECT_DIR`.

## Never commit secrets

The following stay local and are gitignored:

| Path | Contains |
|------|----------|
| `.state/` | Holder/issuer keys, warrant, receipts, `cloud.env` |
| `.claude/settings.json` | Generated hook wiring (timeout varies with approval overlay) |
| `.mcp.json` | **Committed** — uses `tenuo-claude` on PATH or `./bin/tenuo-claude` |
| `src/tenuo_claude_code/` | PyPI package (CLI, admin, bundled harness list) |
| `~/.tenuo/admin.env` | Admin key for `tenuo-admin setup` only |

Safe to commit: source, `tenuo.yaml`, `harness_tools.yaml`, examples, `sandbox/`,
and `prod-credentials.env` (fake decoy — `sk_live_FAKEFAKE…` prefix).

## Creating the private GitHub repo

From `demo/claude-governance`, use a **fresh git history** so local `.state`
never appears in the log:

```bash
cd demo/claude-governance   # or your export copy

git init
git add .gitignore README.md CONTRIBUTING.md docs/ requirements.txt pyproject.toml \
  .python-version uv.lock cloud.env.example .mcp.json bin/tenuo-claude \
  harness_tools.yaml tenuo.yaml tenuo.yaml.cloud.example \
  src/tenuo_claude_code/ tenuo_claude.py tenuo_admin.py tenuo_demo.py ops_server.py \
  tenuo_claude_code_architecture.svg \
  sandbox/ prod-credentials.env
git status    # confirm .state/ is NOT listed (.mcp.json should be listed)
git commit -m "Initial commit: Tenuo governance demo for Claude Code"

gh repo create tenuo-ai/claude-governance --private --source=. --remote=origin --push
# adjust org/name as needed
```

Before pushing, verify nothing sensitive is staged:

```bash
git status --ignored
if git diff --cached | rg -i 'tc_[a-zA-Z0-9]{20,}|/Users/'; then
  echo "ERROR: possible API key or home path in staged diff" >&2
  exit 1
fi
if git diff --cached | rg -i 'sk_live_' | rg -v FAKEFAKE; then
  echo "ERROR: possible live Stripe key in staged diff" >&2
  exit 1
fi
echo "Secret scan: clean"
```

If this folder stays inside the monorepo, use a **separate clone** of the private
repo for day-to-day demo work so `.state/` never lands in the main tree.

## Code layout

`tenuo_claude.py` is intentionally standalone for the demo, but the enforcement
surface splits logically for review:

| Concern | Location (today) | Audit focus |
|---------|------------------|-------------|
| Policy load + `authorize()` | `tenuo_claude.py` (~config/enforcement) | Warrant/PoP/approval path |
| PreToolUse / PostToolUse | `tenuo_claude.py` (`cmd_hook`, `cmd_post`) | Fail-closed hook contract |
| MCP proxy | `tenuo_claude.py` (`cmd_mcp_proxy`) | Structural interposition |
| CLI lifecycle | `tenuo_claude.py` (`init`/`up`/…) | Ops plumbing |

A future refactor should extract hook + MCP proxy into separate modules without
changing the public `python3 tenuo_claude.py _hook` entrypoints.

## Harness tool list

When Claude Code ships new inert harness tools, append them to `harness_tools.yaml`
rather than asking every customer to edit `tenuo.yaml`. Run `doctor` after upgrades
to catch default-denies on tools not yet in the bundled list.

## Hook exit-code contract

`doctor` runs a live Claude Code harness check when the `claude` binary is on
`PATH` (see `check_claude_hook_exit_contract()`). The hook writes a marker file
before exiting so doctor can tell "hook never ran" from "exit 2 didn't block."
Use `doctor --no-live` in automation. Re-run after Claude Code upgrades.
