# Tenuo for Claude Code

[Tenuo](https://tenuo.ai) governance for [Claude Code](https://code.claude.com/docs):
every agent tool call is checked against a signed warrant (hook → authorizer),
with a receipt on each decision, including under `--dangerously-skip-permissions`.

**Install:** `pip install tenuo-claude-code` — CLI commands `tenuo-claude` and `tenuo-admin`.
You do not need to clone this repo to use the tool.

Licensed under [Apache-2.0](LICENSE). PyPI: [pypi.org/project/tenuo-claude-code](https://pypi.org/project/tenuo-claude-code/)

## Prerequisites

- Python ≥ 3.10
- Docker (runs the authorizer container)
- [Claude Code](https://code.claude.com/docs)

**Local mode** — no Tenuo Cloud account. **Cloud mode** — [cloud.tenuo.ai](https://cloud.tenuo.ai) tenant ([Cloud mode](#cloud-mode); skip if you are local-only).

| If you want to… | Start here |
|-----------------|------------|
| Install and run on your machine | [Use the tool (PyPI)](#use-the-tool-pypi) |
| Clone, hack, or run the sample project | [Build from source](#build-from-source) |
| Review for security / platform | [Security](#security) |
| Implementation depth | [docs/DETAILS.md](docs/DETAILS.md) |

---

## Use the tool (PyPI)

Five steps. All commands run in **one project directory** that contains `tenuo.yaml`.

**1. Install**

```bash
pip install tenuo-claude-code
```

**2. Create a project folder**

```bash
mkdir my-project && cd my-project
mkdir workspace    # sandbox — files Claude may read per policy
```

**3. Add policy** — save as `tenuo.yaml` in this folder:

```yaml
name: my-project
sandbox: ./workspace
mode: enforce
enforce:
  Read: "subpath:{sandbox}"
  Bash: "shlex:ls,pwd,echo,date"
default: deny
```

Or download the starter file (no git clone):

```bash
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/tenuo.yaml.example -o tenuo.yaml
```

**4. Initialize and start** — pick one:

```bash
# Manual
tenuo-claude init      # warrant + Claude hooks + MCP wiring
tenuo-claude up        # authorizer in Docker — expect "Local mode (no Cloud)"
tenuo-claude verify    # confirm policy matches authorizer
```

```bash
# Or wizard (same end state; runs check + init + up + verify)
tenuo-claude onboard --local
```

**5. Use Claude Code**

Open Claude Code **in `my-project/`** (same directory as `tenuo.yaml`).

### Day to day

| When | Command |
|------|---------|
| Start work (authorizer down or warrant expired) | `tenuo-claude up` |
| You edited `tenuo.yaml` | `tenuo-claude refresh` |
| Something broken | `tenuo-claude check` |
| See decisions | `tenuo-claude audit` |
| Stop authorizer | `tenuo-claude down` |

Generated files (do not commit): `.state/` (keys, warrant), `.claude/settings.json` (hooks).

### All commands

| Command | Does |
|---------|------|
| `init` | Mint warrant, wire hooks and `.mcp.json` |
| `up` / `down` | Start / stop authorizer |
| `refresh` | Re-apply `tenuo.yaml` (restarts authorizer if up) |
| `check` | Preflight: deps, credentials, wiring drift |
| `verify [--deep]` | Policy self-test against the authorizer |
| `status` | Warrant, posture, Cloud summary |
| `onboard` | Interactive local or Cloud setup wizard |
| `bench [--json]` | Per-tool-call overhead |
| `audit [--tail N]` | Receipt trail |
| `revoke` | Revoke session warrant |

Docs (no source required): [Policy](#policy-tenuoyaml) · [Cloud mode](#cloud-mode) · [docs/DETAILS.md](docs/DETAILS.md)

---

## Build from source

For hacking on the CLI, running the reference demo from git, or using `./bin/tenuo-claude`
instead of a PyPI install.

```bash
git clone https://github.com/tenuo-ai/claude-governance.git
cd claude-governance

uv venv && uv sync && chmod +x bin/tenuo-claude
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

Run commands via the repo launcher or editable install:

```bash
./bin/tenuo-claude --help
# or: uv run tenuo-claude --help
# or: pip install -e . && tenuo-claude --help
```

**Reference demo** (sample policy, sandbox, presentation runbook):

```bash
cd demo
tenuo-claude bootstrap --local   # check → init → up → verify
tenuo-claude demo                # optional scripted tour
```

Open Claude Code in `demo/`. See [demo/README.md](demo/README.md).

Re-run `tenuo-claude init` or `refresh` after switching Python venvs — hooks pin
`sys.executable` in `.claude/settings.json`.

Contributors: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Policy (`tenuo.yaml`)

One file drives the warrant, authorizer routes, hooks, and MCP proxy. The [minimal example](#use-the-tool-pypi) is enough to start; expand as needed:

```yaml
name: my-project
sandbox: ./workspace
mode: enforce
enforce:
  Read:  "subpath:{sandbox}"
  Bash:  "shlex:ls,pwd,echo,date"
  WebFetch:
    domains: ["api.github.com", "*.githubusercontent.com"]
default: deny
subagents:
  analyst:
    tools: [Read, Grep, Glob]
mcp:
  downstream: ./your_mcp_server.py
  enforce:
    read_file: "subpath:{sandbox}"
```

- `enforce` — allowed and argument-checked.
- `audit` — harness tools from bundled list (extend with `audit_extra:`).
- `default: deny` — everything else blocked with a receipt.
- `mode: audit` — receipt allow/deny without blocking (rollout).
- `subagents:` — [DETAILS.md](docs/DETAILS.md#subagents).

Cloud overlays (`tenuo.yaml.cloud.example`, `tenuo.yaml.advanced.example`) — download from this repo or use `tenuo-claude init --cloud` / `--advanced`.

## Cloud mode

Requires a [cloud.tenuo.ai](https://cloud.tenuo.ai) tenant. Root-signed session warrants, central audit stream, fleet revocation (~30s SRL sync).

Two keys, two files (runtime never sees the admin key):

| Key | File | Used by |
|-----|------|---------|
| Runtime (Quick Connect) | `.state/cloud.env` | `tenuo-claude up`, hooks |
| Admin | `~/.tenuo/admin.env` | `tenuo-admin setup` once |

```bash
pip install tenuo-claude-code
cd my-project                 # your tenuo.yaml lives here

mkdir -p .state ~/.tenuo
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/cloud.env.example -o .state/cloud.env
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/admin.env.example -o ~/.tenuo/admin.env
# Edit .state/cloud.env — TENUO_CONNECT_TOKEN from cloud.tenuo.ai → Quick Connect → Authorizer Only
# Edit ~/.tenuo/admin.env — tenant-admin API key

tenuo-claude init --cloud
tenuo-admin setup
tenuo-claude up
tenuo-claude verify
```

Re-run `tenuo-admin setup` when Cloud capabilities change. Re-run `tenuo-claude refresh` for local policy edits.

Optional approval gates: [DETAILS.md](docs/DETAILS.md#human-approval-cloud).

## How it works

![Architecture](tenuo_claude_code_architecture.svg)

```
tenuo.yaml  →  init/up  →  warrant + authorizer + hooks + MCP proxy
                                    ↓
              native tools (PreToolUse hook)  |  MCP tools (proxy)
                                    ↓
                         authorizer → allow / deny → receipt
```

Claude hits the MCP proxy (not your downstream server) and the PreToolUse hook for
native tools. Both paths use the same warrant and authorizer.

More: [docs/DETAILS.md](docs/DETAILS.md)

## Security

Tenuo works **alongside** Claude Code permissions — it does not replace managed
settings. You still deploy hooks; Tenuo adds a signed session warrant, a local
authorizer on every tool call, and a receipt per decision. Policy is one file
(`tenuo.yaml`).

### vs. Claude Code permissions

| | Claude Code permissions | Tenuo warrant |
|---|-------------------------|---------------|
| Policy | Allow/ask/deny in settings | Signed credential; Cloud chains to tenant root |
| Expiry | Until edited | Session TTL (~1h); `up` refreshes |
| Revocation | Edit rules; sessions may keep prior allowances | Revoke warrant id → ~30s SRL sync (Cloud) |
| Evidence | Optional hook logs | Signed receipt per call; Cloud audit stream |
| Delegation | Project/user tool policy | Per-role child warrant; session is the ceiling |
| `--dangerously-skip-permissions` | Bypasses Claude prompts* | Warrant still enforced |

\*Managed settings can disable bypass (`disableBypassPermissionsMode`).

### Cloud audit

With [Tenuo Cloud](https://cloud.tenuo.ai), session warrants chain to your tenant
root. Allow, deny, spawn, and approved exceptions appear in one audit log. Revoke a
warrant id from `status` or the dashboard without touching the laptop.

![Authorization receipts in Tenuo Cloud](docs/images/cloud-audit-stream.png)

Admin and runtime keys stay split: `tenuo-admin setup` (once) vs `tenuo-claude up`
(daily). Runtime refuses to start if an admin key is in the environment.

### Rollout

1. Pilot on one project — `init`, `up`, `verify` (or the [demo/](demo/) sample).
2. `mode: audit` — real allow/deny in receipts, hook does not block. Review
   `WOULD-DENY`, tune policy, then `mode: enforce`.
3. Fleet — managed-settings hooks, Cloud warrants, team `tenuo.yaml` via MDM.
   Help: [team@tenuo.ai](mailto:team@tenuo.ai).

### Scope and fail-closed

Governance covers agent tool calls (Read, Bash, MCP, subagent spawns), not
interactive `!` shell in the Claude TUI
([Map vs Territory](https://niyikiza.com/posts/map-territory/)). Missing or broken
`tenuo.yaml` denies every call until restored.

Keys and credentials in `.state/` must be owner-only (`0600` in a `0700` directory).
The authorizer container mounts only `.state/authorizer/` — not holder keys or
`cloud.env`. PoP signing stays in the hook on the host.

Report vulnerabilities: [SECURITY.md](SECURITY.md). Implementation depth: [docs/DETAILS.md](docs/DETAILS.md).

## Performance

Run `tenuo-claude bench` after `up`. On a typical laptop, Tenuo authorization is ~1–3 ms per call; command hooks add ~100–200 ms (mostly process startup). Use `bench --json` on your machines.

## Enterprise

Fleet rollout (managed settings, team policy in git, Cloud Quick Connect): [team@tenuo.ai](mailto:team@tenuo.ai).

## This repo

PyPI package source (`src/tenuo_claude_code/`). [Build from source](#build-from-source) · [CONTRIBUTING.md](CONTRIBUTING.md)
