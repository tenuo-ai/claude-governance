# Tenuo for Claude Code

PyPI package: [`tenuo-claude-code`](https://pypi.org/project/tenuo-claude-code/) · CLI: `tenuo-claude`, `tenuo-admin`

[![PyPI](https://img.shields.io/pypi/v/tenuo-claude-code)](https://pypi.org/project/tenuo-claude-code/)
[![Python](https://img.shields.io/pypi/pyversions/tenuo-claude-code)](https://pypi.org/project/tenuo-claude-code/)
[![CI](https://github.com/tenuo-ai/claude-governance/actions/workflows/ci.yml/badge.svg)](https://github.com/tenuo-ai/claude-governance/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Keep your AI coding agent inside the lines.** Tenuo governs every tool call Claude Code makes: native tools, MCP, and subagent spawns, on one laptop or across a Cloud-connected fleet.

**Native tools** (Read, Bash, WebFetch, and the rest) go through a PreToolUse hook. **MCP tools** go through a proxy you wire in place of the downstream server. Both paths hit the same authorizer before anything runs.

At `init`, Tenuo mints a **session warrant**: a signed credential that says what this session may do and how long it lasts. On every call, the hook or proxy must **prove it holds that warrant** before the authorizer allows the action. Policy is one file (`tenuo.yaml`), but the limits are cryptographic. Editing Claude's permission settings or running `--dangerously-skip-permissions` does not widen what the warrant permits.

File reads stay in the sandbox. Shell commands match an allowlist. MCP tool arguments get the same checks. URLs match policy.

Connect [Tenuo Cloud](https://cloud.tenuo.ai) for **verifiable audit receipts** (signed allow/deny/spawn events) and fleet-wide warrant revocation.

Install: `pip install tenuo-claude-code` (commands: `tenuo-claude`, `tenuo-admin`).

## How it works

![Architecture](docs/images/tenuo_claude_code_architecture.svg)

```
tenuo.yaml  →  init/up  →  warrant + authorizer + hooks + MCP proxy
                                    |
                                    |
                                    v
              native tools (PreToolUse hook)  |  MCP tools (proxy)
                                    |
                                    |
                                    v
                         authorizer → allow / deny → log / receipt
```

Claude hits the MCP proxy (not your downstream server) and the PreToolUse hook for native tools.

Both paths use the same warrant and authorizer.

More: [docs/DETAILS.md](docs/DETAILS.md)

## Prerequisites

- Python ≥ 3.10
- **Authorizer runtime** (pick one):
  - **Docker:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) or your engine, then `tenuo-claude up` (pulls the pinned `tenuo/authorizer` image). This is the default when Docker is running.
  - **Native (no Docker):** install the matching binary to `~/.tenuo/bin` (prebuilt download when available, otherwise `cargo install --root`):

    ```bash
    pip install tenuo-claude-code
    tenuo-claude install-authorizer
    tenuo-claude up --native
    ```

    One step on first start: `tenuo-claude up --native --install`. Override the binary with `TENUO_AUTHORIZER_BIN`. The CLI checks the binary matches the package pin; set `TENUO_AUTHORIZER_SKIP_VERSION=1` only for local dev builds.
- [Claude Code](https://code.claude.com/docs) for live agent use. For a first eval without Claude, see [Quick eval](#quick-eval-no-claude).

**Local mode:** no Tenuo Cloud account. Good for one project or evaluation.

**Cloud mode:** [cloud.tenuo.ai](https://cloud.tenuo.ai) tenant for tenant-root warrants, central audit, fleet revocation, and org-wide rollout ([Cloud mode](#cloud-mode)).

| If you want to… | Start here |
|-----------------|------------|
| Install and run on your machine | [Use the tool (PyPI)](#use-the-tool-pypi) |
| Clone, hack, or run the sample project | [Build from source](#build-from-source) |
| Review security posture | [Security](#security) |
| Plan org-wide rollout (managed settings, Cloud) | [Talk to us](https://tenuo.ai/early-access.html) |
| Implementation depth | [docs/DETAILS.md](docs/DETAILS.md) |

---

## Use the tool (PyPI)

Five steps. All commands run in **one project directory** that contains `tenuo.yaml`.

**1. Install**

```bash
pip install tenuo-claude-code
# If you are not using Docker:
tenuo-claude install-authorizer
```

**2. Create a project folder**

```bash
mkdir my-project && cd my-project
mkdir workspace    # sandbox: files Claude may read per policy
```

**3. Add policy**: save as `tenuo.yaml` in this folder:

```yaml
name: my-project
sandbox: ./workspace
mode: enforce
enforce:
  Read: "subpath:{sandbox}"
  Bash: "shlex:ls,pwd,echo,date"
default: deny
```

Or fetch the example policy:

```bash
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/templates/tenuo.yaml.example -o tenuo.yaml
```

**4. Initialize and start**

`up` uses Docker when the daemon is running. Pass `--native` to force the host binary.

```bash
# Docker (default when Docker is running)
tenuo-claude init
tenuo-claude up
tenuo-claude verify

# Native (no Docker)
tenuo-claude init
tenuo-claude up --native --install   # first run: install binary to ~/.tenuo/bin
tenuo-claude verify
```

Wizard (same end state; local wizard prefers Docker when it is available):

```bash
tenuo-claude onboard --local
```

**5. Use Claude Code**

Open Claude Code **in `my-project/`** (same directory as `tenuo.yaml`).

### Quick eval (no Claude)

Prove policy enforcement without Claude Code:

```bash
pip install tenuo-claude-code
# create my-project/ with tenuo.yaml (steps 2-3 above)

# Docker (when Docker is running):
tenuo-claude onboard --local

# Native (no Docker):
tenuo-claude install-authorizer
tenuo-claude init && tenuo-claude up --native && tenuo-claude verify
```

`verify` runs allow/deny probes against the live authorizer (out-of-scope reads, shell chaining, default-deny).

Or run the [reference demo](demo/) tour: `tenuo-claude demo`.

### Day to day

| When | Command |
|------|---------|
| Start work (authorizer down or warrant expired) | `tenuo-claude up` (Docker) or `tenuo-claude up --native` |
| You edited `tenuo.yaml` | `tenuo-claude refresh` |
| Something broken | `tenuo-claude check` |
| See decisions | `tenuo-claude audit` |
| Stop authorizer | `tenuo-claude down` |

If you always use native without Docker, set `TENUO_AUTHORIZER_BACKEND=native` in your shell so plain `up` picks the host binary.

### Port conflicts

The authorizer listens on `127.0.0.1:9090` by default. If that port is taken, set `TENUO_AUTHORIZER_PORT` **before** `init` and `up`:

```bash
export TENUO_AUTHORIZER_PORT=9091
tenuo-claude init
tenuo-claude up --native
```

(`PORT` still works but is deprecated; it collides with other tools.)

The chosen URL is saved in `.state/state.json`. Hooks and `verify` read that file, so they stay aligned even if the env var is unset later.

Generated files (do not commit): `.state/` (keys, warrant), `.claude/settings.json` (hooks).

### All commands

| Command | Does |
|---------|------|
| `init` | Mint warrant, wire hooks and `.mcp.json` |
| `up` / `down` | Start / stop authorizer. `up` flags: `--native`, `--docker`, `--install` (native, first run) |
| `install-authorizer` | Install `tenuo-authorizer` to `~/.tenuo/bin` (no manual `cargo`) |
| `refresh` | Re-apply `tenuo.yaml` (restarts authorizer if up) |
| `check` | Preflight: deps, credentials, wiring drift |
| `verify [--deep]` | Policy self-test against the authorizer |
| `status` | Warrant, posture, Cloud summary |
| `onboard` | Interactive local or Cloud setup wizard |
| `bench [--json]` | Per-tool-call overhead |
| `audit [--tail N]` | Receipt trail |
| `revoke` | Revoke session warrant |

See also: [Policy](#policy-tenuoyaml) · [Cloud mode](#cloud-mode) · [docs/DETAILS.md](docs/DETAILS.md)

---

## Build from source

For hacking on the CLI, running the reference demo from git, or using `./bin/tenuo-claude` instead of a PyPI install.

```bash
git clone https://github.com/tenuo-ai/claude-governance.git
cd claude-governance

uv venv && uv sync && chmod +x bin/tenuo-claude
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

Without Docker, install the authorizer binary once: `uv run tenuo-claude install-authorizer`

Run commands via the repo launcher or editable install:

```bash
./bin/tenuo-claude --help
# or: uv run tenuo-claude --help
# or: pip install -e . && tenuo-claude --help
```

**Reference demo** (sample policy and sandbox):

```bash
cd demo
tenuo-claude bootstrap          # check → init → up → verify (Docker if available)
tenuo-claude demo               # optional scripted tour
```

Use `tenuo-claude up --native` instead of plain `up` if you are not running Docker.

Open Claude Code in `demo/`. See [demo/README.md](demo/README.md).

Re-run `tenuo-claude init` or `refresh` after switching Python venvs. Hooks pin `sys.executable` in `.claude/settings.json`.

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

- `enforce`: allowed and argument-checked.
- `audit`: harness tools from bundled list (extend with `audit_extra:`).
- `default: deny`: everything else blocked with a receipt.
- `mode: audit`: receipt allow/deny without blocking (rollout).
- `subagents:`: [DETAILS.md](docs/DETAILS.md#subagents).

**Policy templates:** ready-made patterns in [examples/policies/](examples/policies/). Download one, save as `tenuo.yaml` in your project directory, adapt sandbox paths and tool lists, then run `tenuo-claude init`. To contribute a template, see that folder's README.

Cloud overlays: [templates/tenuo.yaml.cloud.example](templates/tenuo.yaml.cloud.example), [templates/tenuo.yaml.advanced.example](templates/tenuo.yaml.advanced.example). Download from this repo or use `tenuo-claude init --cloud` / `--advanced`.

---

## Cloud mode

Requires a [cloud.tenuo.ai](https://cloud.tenuo.ai) tenant.

Use Cloud when you need organization-scale governance, not just a single laptop:

- **Tenant-root warrants**: session credentials chain to your tenant, not a local issuer key
- **Central audit**: signed allow/deny/spawn/approval receipts in one stream
- **Fleet revocation**: revoke a warrant id; authorizers pull the SRL within ~30s
- **Split keys**: admins run `tenuo-admin setup` once; developers use runtime keys only
- **Optional approver gates**: high-risk governed tool calls can pause for
  [Cloud identity](https://docs.tenuo.ai/integrations/identity-bindings) sign-off
  before proceeding ([setup](#human-approval-cloud))
- **Managed rollout**: wire hooks via Claude Code managed settings; keep team
  `tenuo.yaml` in git
- **Org-wide baseline**: with managed settings, the signed warrant is enforced on
  every tool call at the hook and MCP proxy. Engineers cannot disable it with local
  Claude permission edits or `--dangerously-skip-permissions`

Local mode (no Cloud account) remains fully supported for evaluation and single-project use.

Two keys, two files (runtime never sees the admin key):

| Key | File | Used by |
|-----|------|---------|
| Runtime (Quick Connect) | `.state/cloud.env` | `tenuo-claude up`, hooks |
| Admin | `~/.tenuo/admin.env` | `tenuo-admin setup` once |

```bash
pip install tenuo-claude-code
cd my-project                 # your tenuo.yaml lives here

mkdir -p .state ~/.tenuo
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/templates/cloud.env.example -o .state/cloud.env
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/templates/admin.env.example -o ~/.tenuo/admin.env
# Edit .state/cloud.env: set TENUO_CONNECT_TOKEN from cloud.tenuo.ai → Quick Connect → Authorizer Only
# Edit ~/.tenuo/admin.env: paste tenant-admin API key

tenuo-claude init --cloud
tenuo-admin setup
tenuo-claude up
tenuo-claude verify
```

Re-run `tenuo-admin setup` when Cloud capabilities change. Re-run `tenuo-claude refresh` for local policy edits.

### Human approval (Cloud)

Approval is a **third outcome** on any governed tool call: not just egress.

When the warrant includes an approval gate for a capability, the authorizer can return `approval-required` instead of allow/deny. The hook creates a Cloud approval request, waits for an approver on their notification channel, then re-authorizes with signed approvals in `X-Tenuo-Approvals`.

**Included policy example:** off-allowlist `WebFetch` (URLs that pass SSRF checks but
are not on your domain allowlist). Other capabilities can carry approval gates in the
Cloud trigger warrant config the same way.

1. Configure a notification channel and identity binding in Cloud ([channels](https://docs.tenuo.ai/guides/adding-channels),
   [identity bindings](https://docs.tenuo.ai/integrations/identity-bindings)).
2. Add `approval:` under `enforce.WebFetch` and set `cloud.approver_identity` to that
   identity's display name. See [templates/tenuo.yaml.advanced.example](templates/tenuo.yaml.advanced.example).
3. Run `tenuo-claude init --advanced --approver "<identity display name>"`, then
   `tenuo-admin setup`.

For the WebFetch example: allowlisted domains pass directly. SSRF cases remain hard-denied.

Details: [docs/DETAILS.md § Human approval](docs/DETAILS.md#human-approval-cloud).

---

## Security

Tenuo works **alongside** Claude Code permissions. It does not replace managed settings.

You still deploy hooks. Tenuo adds a signed session warrant, a local authorizer on every tool call, and a decision log per call. Policy is one file (`tenuo.yaml`).

### vs. Claude Code permissions

| | Claude Code permissions | Tenuo warrant |
|---|-------------------------|---------------|
| Policy | Allow/ask/deny in settings | Signed credential; Cloud chains to tenant root |
| Expiry | Until edited | Session TTL (~1h); `up` refreshes |
| Revocation | Edit rules; sessions may keep prior allowances | Revoke warrant id → ~30s SRL sync (Cloud) |
| Evidence | Optional hook logs | Local JSONL log; signed audit stream in Cloud |
| Delegation | Project/user tool policy | Per-role child warrant; session is the ceiling |
| Org-wide deployment | Per-user settings; users can edit local hooks | Managed settings + shared policy; hook/proxy enforcement is not user-editable |
| `--dangerously-skip-permissions` | Bypasses Claude permission prompts | Hook and MCP proxy still enforce the warrant |

That flag skips Claude's permission UI, not the warrant.

Org admins can block it in managed settings (`disableBypassPermissionsMode`).

### Organization-wide policy

For teams that need a **global configuration engineers cannot bypass**:

1. Store `tenuo.yaml` in version control and deploy the same policy to every project.
2. Push hook and MCP wiring through Claude Code **managed settings** (org admin), not
   per-developer `settings.local.json`.
3. Use **Cloud** for tenant-root warrants, central audit, and revocation across machines.

[Talk to us](https://tenuo.ai/early-access.html) about managed-settings rollout and Cloud deployment.

### Receipts

Every governed tool call must prove possession of the session key (proof-of-possession) when asking the authorizer for allow/deny.

What you can **read back** depends on mode:

**Local (always):** the hook appends a JSON line to `.state/receipts.jsonl`. Inspect
with `tenuo-claude audit`:

```json
{"phase": "pre", "decision": "deny", "claude_tool": "Read", "governed": true,
 "args": {"file_path": "/etc/passwd"}, "reason": "Constraint not satisfied", "ts": "…"}
```

In `mode: audit`, denials show as `WOULD-DENY` in `audit` output (`shadow: true` in the file).

**Cloud (when connected):** the authorizer emits **signed** audit events (Ed25519 over a CBOR payload) to your tenant.

These are the non-repudiable receipts for compliance and fleet audit, not the local JSONL file.

![Authorization receipts in Tenuo Cloud](docs/images/cloud-audit-stream.png)

More: [docs/DETAILS.md § Receipts](docs/DETAILS.md#receipts).

### Cloud audit

With [Tenuo Cloud](https://cloud.tenuo.ai), session warrants chain to your tenant root.

Allow, deny, spawn, and approved exceptions appear in one signed audit log. Revoke a warrant id from `status` or the dashboard without touching the laptop.

Admin and runtime keys stay split: `tenuo-admin setup` (once) vs `tenuo-claude up` (daily).

Runtime refuses to start if an admin key is in the environment.

### Rollout

1. Start on one project with `init`, `up`, `verify` (or the [demo/](demo/) sample).
2. Set `mode: audit` to compute allow/deny in receipts without blocking. Review
   `WOULD-DENY` rows, tune policy, then set `mode: enforce`.
3. Organization-wide rollout: managed-settings hooks, Cloud warrants, shared `tenuo.yaml`
   in version control, and the [Cloud capabilities above](#cloud-mode). See
   [Tenuo Cloud docs](https://docs.tenuo.ai).

### Scope and fail-closed

Governance covers agent tool calls (Read, Bash, MCP, subagent spawns), not interactive `!` shell in the Claude TUI ([Map vs Territory](https://niyikiza.com/posts/map-territory/)).

Missing or broken `tenuo.yaml` denies every call until restored.

Keys and credentials in `.state/` must be owner-only (`0600` in a `0700` directory).

The authorizer reads gateway config from `.state/authorizer/` (Docker mount or native process). Holder keys and `cloud.env` stay on the host. Session-key signing runs in the hook.

Report vulnerabilities: [SECURITY.md](SECURITY.md).

Implementation depth: [docs/DETAILS.md](docs/DETAILS.md).

---

## Performance

Run `tenuo-claude bench` after `up`.

On a typical laptop, Tenuo authorization is about 1-3 ms per call. Command hooks add about 100-200 ms (mostly process startup). Use `bench --json` on your machines.

---

## This repo

GitHub: [`tenuo-ai/claude-governance`](https://github.com/tenuo-ai/claude-governance)

PyPI: [`tenuo-claude-code`](https://pypi.org/project/tenuo-claude-code/)

| Path | Contents |
|------|----------|
| `src/tenuo_claude_code/` | Package source |
| `templates/` | Starter `tenuo.yaml` and credential examples |
| `examples/policies/` | Ready-made policy templates |
| `demo/` | Reference project and scripted tour |
| `docs/` | Architecture diagram, implementation notes |

[Build from source](#build-from-source) · [CONTRIBUTING.md](CONTRIBUTING.md)
