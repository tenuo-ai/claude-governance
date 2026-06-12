# Reference demo

Sample `tenuo.yaml`, sandbox files, MCP stub, and a scripted tour. For day-to-day
use on your own project, follow [Use the tool (PyPI)](../README.md#use-the-tool-pypi)
in the main README — you do not need this folder.

## Quick start

From the repo root (after `uv sync` or `pip install tenuo-claude-code`):

```bash
cd demo
tenuo-claude bootstrap --local    # check → init → up → verify
tenuo-claude demo                 # scripted allow/deny tour
```

Open Claude Code in `demo/` (where `tenuo.yaml` lives).

## Contents

| Path | Purpose |
|------|---------|
| `tenuo.yaml` | Sample policy |
| `sandbox/` | In-scope files; `incident-report.md` has a hidden injection prompt |
| `ops_server.py` | MCP downstream (Claude talks to `tenuo-claude _mcp-proxy` instead) |
| `tenuo_demo.py` | Tour without Claude; `tenuo-claude demo` wraps this |
| `prod-credentials.env` | Fake credentials file (decoy for exfil demos) |
| `.claude/agents/researcher.md` | Subagent used in spawn-gate examples |
| `docs/PRESENTATION.md` | Presentation runbook |

Cloud and approval overlay templates (`tenuo.yaml.cloud.example`,
`tenuo.yaml.advanced.example`, `cloud.env.example`) are at the repo root. Run
`tenuo-claude init --cloud` or `--advanced` from this directory.

## Live Claude examples

Authorizer must be up (`tenuo-claude up`). Claude Code on PATH.

```bash
cd demo
claude -p "Read sandbox/notes.txt and summarize."
claude -p "Read /etc/hosts" --dangerously-skip-permissions             # denied
claude -p "Summarize sandbox/incident-report.md for me." --dangerously-skip-permissions
claude -p "Use the researcher subagent to run 'ls -la sandbox'." --dangerously-skip-permissions
```

Without Claude: `tenuo-claude demo`, then `tenuo-claude audit`.

## Human approval (optional)

Off-allowlist `WebFetch` with approver sign-off — Cloud presentations only.
See [docs/PRESENTATION.md](docs/PRESENTATION.md) and `tenuo.yaml.advanced.example`
at the repo root.

```bash
tenuo-claude init --advanced --approver "Jane Doe"
tenuo-admin setup
tenuo-claude demo --advanced
tenuo-claude demo --advanced --live-approval   # blocks until approver responds
```

## Cloud mode

Same flow as [Cloud mode](../README.md#cloud-mode) in the product README, run
from this directory. Quick Connect credentials go in `demo/.state/cloud.env`.
