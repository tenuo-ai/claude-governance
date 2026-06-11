# Implementation details

Reference for security reviewers. The README stays short; this file holds the
mechanics, invariants, and examples.

For the trust-boundary model (Map vs Territory), see
[The Map is not the Territory](https://niyikiza.com/posts/map-territory/).

## Audit mode (`mode: audit`)

Shadow mode: every call's real allow/deny is still computed against the warrant
and written to the signed receipt, but nothing is blocked.

**Neutrality invariant:** in audit mode the hook emits *no* permission decision.
Claude's own permission prompts and settings stay fully in effect. Observe-only
never weakens the stock posture — an explicit hook "allow" would silently
auto-approve calls the user's settings would have prompted on.

Rollout: watch `WOULD-DENY` rows in `audit`, tune policy, then set `mode: enforce`.
The hook reads `mode` live (next tool call); the MCP proxy picks it up on the
next Claude session. `status` shows the active posture.

## Why hook and MCP proxy

Both check the same warrant against the same authorizer. The interception point
differs:

| Path | Enforcement |
|------|-------------|
| MCP proxy | Structural — Claude is wired to the proxy, not the downstream server |
| PreToolUse hook | Cooperative — Claude must honor the allow/deny decision |

You could point `.mcp.json` at the downstream server and rely on the hook alone;
policy would be the same. We still ship the proxy because if the hook fails or is
removed, denied MCP calls never reach the server. Native tools have no alternative
interception point, so the hook is mandatory there.

For MCP tools the hook also checks `mcp__…` names against the same `mcp.enforce`
policy the proxy uses.

## WebFetch egress

The structured policy compiles to two checks: `UrlSafe` SSRF hygiene on the raw
`url` (https-only by default, metadata/loopback/encoded-IP blocks), and host
must match the domain allowlist (`*` wildcards ok).

Examples exercised by `doctor`:

| URL | Result |
|-----|--------|
| `https://api.github.com`, `https://raw.githubusercontent.com/…` | allow |
| `https://evil.com` | deny (off-allowlist) |
| `http://api.github.com` | deny (plain http) |
| `http://169.254.169.254/latest/meta-data/` | deny (metadata) |
| `http://2130706433/` | deny (decimal-encoded loopback) |
| `http://0x7f000001/` | deny (hex-encoded loopback) |
| `https://api.github.com.evil.com/` | deny (suffix spoof) |
| `https://api.github.com@evil.com/` | deny (userinfo spoof) |

With an `approval` block, an off-allowlist but SSRF-safe URL like
`https://evil.com` pauses for approver sign-off instead of a hard deny; SSRF
cases above are still denied outright.

URL validation is on the string Claude passes in. DNS rebinding and redirects at
connect time need complementary controls in the fetching process — see the
Map/Territory essay above.

Local minting supports internal-CIDR egress and custom schemes/ports; the Cloud
trigger path is domain-allowlist v1 (internal-CIDR on the Cloud roadmap).

## Human approval (Cloud)

Adding `approval:` under `WebFetch` yields three outcomes:

| URL | Outcome |
|-----|---------|
| allowlisted domain | allowed directly |
| off-allowlist, SSRF-safe | paused for approver sign-off |
| SSRF / metadata / loopback / plain-http / suffix-spoof | denied (gate not reached) |

Cloud-only: the gate carries the approver's KMS public key; there is no local
fallback. In local mode off-allowlist URLs are simply denied.

End to end:

1. `cloud.approver_identity` names an existing Cloud identity (KMS key +
   notification routing). `tenuo-admin setup` resolves it, creates or reuses an
   approval policy for `web_fetch`, and bakes an approval gate into the trigger
   warrant config. Allowlisted hosts are exempt from the gate.
2. Off-allowlist `WebFetch` returns `approval-required` (code `1707`). The hook
   creates a Cloud approval request bound to that call via a holder-signed context
   attestation; the approver gets a prompt on their configured notification channel.
3. The hook polls until approve/deny/timeout. On approve, Cloud KMS signs a
   `SignedApproval`; the hook re-authorizes with `X-Tenuo-Approvals`. The generated
   hook `timeout` is extended so Claude waits for the approver.

Receipts: `PENDING [appr]` while parked, then `ALLOW`/`DENY`. In audit mode the
gate is reported only, never blocks. `python3 tenuo_demo.py --live-approval` drives
the full flow.

**Live demo:** the session blocks on that tool call until someone responds. Have
an approver ready. Claude's hook timeout can expire first and look like a deny.

## Search tools and symlinks

`Glob`/`Grep` search roots are constrained like `Read`. A bare `Grep` with no
path is checked against the hook cwd and denied outside the sandbox. Subpath
arguments are `realpath()`-resolved before authorization, so
`ln -s /etc/passwd sandbox/escape.txt` does not smuggle a read out (`doctor`
plants this case). Races between check and open need execution-time guards
(e.g. `path_jail`).

## Warrant TTL and refresh

Session warrants expire (1h TTL). `status` flags `EXPIRED` when lapsed.
`tenuo-claude up` refreshes even while the authorizer is running: Cloud
re-fires the trigger; local re-mints with the same issuer key. No container
restart — the warrant rides in each request header. Subagent child warrants are
re-derived from the fresh session warrant.

## Bash: `shlex` not `regex`

`Shlex` is structure-aware: rejects pipes, chaining (`&&`/`;`), subshells, and
expansion that `regex:.*` would admit (e.g. `ls && rm -rf /`). A command
allowlist authorizes the verb, not paths — `cat /etc/passwd` can still pass.
Keep Bash to inert commands; scope filesystem with `Read`/`Write`/`Edit`
(`subpath`). For a hard sandbox, drop `Bash` from `enforce`.

## Subagents

When `subagents:` is present:

1. **Spawn gate** — `spawn_agent` with `subagent_type` constrained to declared
   roles. Undeclared spawns are denied by the warrant, not a string check in the hook.
2. **Per-subagent warrant** — each role runs under the session warrant attenuated
   to its `tools`. The session is the ceiling; attenuation is one-way and
   cryptographic.

Omit `subagents:` for flat coverage: spawns are audited, not gated; the subagent
runs under the session warrant.

Roles must match a real `subagent_type` (`.claude/agents/<name>.md` frontmatter
`name:` or a built-in). `doctor` and `status` validate this.

**Workflow:** bundled as audit-allow in `harness_tools.yaml`. With `subagents:`
declared, inner tool calls from Workflow agents carry an `agent_type` that is
not a declared role — layer 2 denies them (fail-closed). Workflow is effectively
unusable in subagent mode unless you remove it from the audit list or omit
`subagents:` for flat session coverage.

Changing `subagents:` is a policy change: re-run `tenuo-admin setup` (Cloud) or
`init` (local). Subagents may drop parent approval gates for tools they no longer
hold (e.g. a read-only `researcher` without `WebFetch`).

Pair with authorizer `0.1.0-beta.23-authz.2` or newer (pinned in `tenuo_claude.py`).
Until `tenuo` 0.1.0b24 is on PyPI, install the Python SDK from the monorepo
(`pip install -e path/to/tenuo-python`) so subwarrant minting picks up the same fix.

## Receipts

`PreToolUse` / `PostToolUse` use match-all hooks; MCP tools are also gated by the
proxy. Every call produces a signed receipt:

- **Enforced** tools: constraint-checked; out-of-scope denied.
- **Audit-allowed** harness tools: logged, not blocked.
- **Default-deny** for everything else.

Subagent calls carry `agent_type`; the hook enforces the child warrant.

In audit mode denials are recorded as `WOULD-DENY` without blocking.

**Authority:** `tenuo-claude audit` pretty-prints `.state/receipts.jsonl` (local
convenience). Authoritative signed receipts come from the authorizer and stream
to Tenuo Cloud when connected.

## Hook exit codes and fail-closed

Claude Code blocks PreToolUse only on exit code **2** or an explicit `deny`.
Exit code **1** (including an unhandled traceback) is non-blocking — the tool
call proceeds. Harness semantics can change between Claude Code releases.

`_hook` wraps its body in a fail-closed guard: internal errors become explicit
deny decisions, never bare exceptions.

`doctor` runs a live canary when `claude` is on PATH (`--no-live` to skip). The
test hook writes a marker file before exiting so doctor can distinguish "hook
never ran" from "exit 2 did not block."

## Tenuo Cloud (extended)

By default `init` mints from a local issuer key. With
[Cloud](https://cloud.tenuo.ai), warrants are issued by the tenant root via a
trigger. Admin registers the holder and creates the trigger; runtime only fires
it. Runtime refuses to start if an admin key is reachable. First trigger fire
locks to the discovered runtime service account.

Revocation: Cloud revokes by warrant id; authorizer pulls SRL within ~30s. Local:
`tenuo-claude revoke` writes a signed SRL and reloads.
