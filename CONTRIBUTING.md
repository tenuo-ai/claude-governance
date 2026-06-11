# Contributing / maintainer notes

Internal scaffolding for the standalone repo — not required for running the demo.

## Creating the private GitHub repo

From `demo/claude-governance` (after the hygiene pass in the README), use a **fresh
git history** so local `.state/` never appears in the log:

```bash
cd demo/claude-governance   # or your export copy

git init
git add .gitignore README.md CONTRIBUTING.md requirements.txt cloud.env.example \
  harness_tools.yaml tenuo.yaml tenuo.yaml.cloud.example tenuo_claude.py tenuo_admin.py \
  tenuo_demo.py ops_server.py tenuo_claude_code_architecture.svg \
  sandbox/ prod-credentials.env
git status    # confirm .state/ and .mcp.json are NOT listed
git commit -m "Initial commit: Tenuo governance demo for Claude Code"

gh repo create tenuo-ai/claude-governance --private --source=. --remote=origin --push
# adjust org/name as needed
```

Before pushing, verify nothing sensitive is staged:

```bash
git status --ignored
git diff --cached | rg -i 'tc_[a-zA-Z0-9]{20,}|sk_live_(?!FAKEFAKE)|/Users/' || true
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
`PATH` (see `check_claude_hook_exit_contract()`). Re-run after Claude Code upgrades;
update the documented baseline version in the README if semantics change.
