# Tenuo + Claude Code — security team brief

One-page summary for platform / security reviewers evaluating the Claude Code demo.
Full mechanics: [DETAILS.md](DETAILS.md). Install and demo commands: [README.md](../README.md).

## What you get

Developers run Claude Code with normal hooks and (for fleet rollouts) managed settings.
Tenuo adds a **signed session warrant**, an **authorizer** that checks every tool call,
and a **receipt per decision**. Policy is one file (`tenuo.yaml`); hooks and MCP wiring
are generated from it so they cannot drift.

This sits **on top of** Claude Code's permission system — it does not replace managed
settings or PreToolUse hooks. It makes agent capabilities **auditable, time-bounded,
and revocable** with cryptographic evidence.

## vs. native Claude Code permissions

| | Claude Code permissions | Tenuo warrant |
|---|-------------------------|---------------|
| Policy form | Allow/ask/deny rules in `settings.json` (plus managed settings for fleet) | Signed credential; Cloud mode chains to tenant root |
| Expiry | Rules persist until edited | Session warrant TTL (~1h); `up` refreshes |
| Revocation | Change rules; existing sessions may retain prior allowances | Revoke warrant id → SRL live in ~30s (Cloud); no laptop touch |
| Evidence | Optional hook logs; no signed decision trail by default | Signed receipt per tool call; central stream when Cloud-connected |
| Delegation | Subagents follow project/user tool policy | Per-role **attenuated** child warrant; session is the ceiling |
| Exceptions | Extra allow rules in settings | Optional Cloud **approval gate** on off-allowlist `WebFetch` |
| `--dangerously-skip-permissions` | Bypasses Claude's permission prompts* | Warrant enforcement **still applies** |

\*Enterprise can disable bypass in managed settings (`disableBypassPermissionsMode`).

## Fleet / day-2 view

With [Tenuo Cloud](https://cloud.tenuo.ai), each developer session warrant chains to
your tenant root. Security can answer: *what did agents do, under what authority, who
approved exceptions* — from one receipt stream — and revoke a compromised session in
about 30 seconds without reimaging the laptop.

Open **cloud.tenuo.ai → Audit log** in your tenant for the live stream. (Add a
screenshot to `docs/images/cloud-audit-stream.png` for internal runbooks.)

Admin vs runtime keys are separated: `tenuo_admin.py setup` (once) vs `tenuo_claude.py up`
(daily). Runtime refuses to run if an admin key is present in the environment.

## Receipt trail (demo output)

After `python3 tenuo_demo.py`:

```
  ALLOW      [gov] Read           -> read_file  authorized
  DENY       [gov] Read           -> read_file  Constraint not satisfied
  DENY       [aud] delete_deployment -> unlisted  Constraint not satisfied
  ALLOW      [gov] Agent          -> spawn_agent  authorized
  DENY       [gov] Bash           <researcher> -> run_command  Constraint not satisfied
```

Local file: `.state/receipts.jsonl`. Authoritative signed receipts come from the
authorizer and stream to Cloud when connected.

## Rollout path

1. **Local eval** — `init` / `up` / `doctor` / `tenuo_demo.py` (this repo).
2. **Observe-only** — `mode: audit` in `tenuo.yaml`: real allow/deny computed and
   receipted; hook emits **no** decision, so Claude's stock prompts are not weakened.
   Review `WOULD-DENY` rows, tune policy, then enforce.
3. **Fleet enforce** — managed settings (hook cannot be removed by users) + Cloud
   root-signed warrants + MDM-deployed `tenuo.yaml` per team or golden image.

## Honest scope

Governance covers **agent tool calls** (Read, Bash, MCP, subagent spawns). It does
not intercept interactive `!` shell in the Claude Code TUI. Map vs Territory:
[The Map is not the Territory](https://niyikiza.com/posts/map-territory/).

## Fail-closed check

```bash
mv tenuo.yaml tenuo.yaml.bak
# every tool call denied: Tenuo hook error (fail-closed): Missing …/tenuo.yaml
mv tenuo.yaml.bak tenuo.yaml
```

Misconfiguration denies everything; it does not fail open.
