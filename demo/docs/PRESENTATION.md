# Customer presentation guide

Runbook for presenting the Tenuo + Claude Code governance demo on **Tenuo Cloud**
(root-signed warrants, central audit, optional human approval).

**Run all commands from the `demo/` directory** (the governed project root).
For mechanics see [DETAILS.md](../../docs/DETAILS.md). For install see [README.md](../../README.md).

---

## Before the call (30–60 min)

### Environment

Requires [uv](https://docs.astral.sh/uv/), Docker, Claude Code, and a Tenuo Cloud
tenant with **two API keys** (different roles — see below).

### Cloud credentials (before `setup`)

You need a [Tenuo Cloud](https://cloud.tenuo.ai) tenant. Sign up or request access at
[tenuo.ai](https://tenuo.ai) if you do not have one yet.

You need **two keys** with different RBAC roles. They land in **different files**
so runtime never sees the admin key:

| Key | Role / source | File | Used by |
|-----|---------------|------|---------|
| **Runtime** | Quick Connect (authorizer service account) | `.state/cloud.env` | `tenuo-claude up`, hooks |
| **Admin** | Tenant admin (not in Quick Connect) | `~/.tenuo/admin.env` | `tenuo-admin setup` **once** |

**Runtime key — Quick Connect (do this first)**

1. [cloud.tenuo.ai](https://cloud.tenuo.ai) → **Agents** → **Quick Connect**
2. Connection type: **Authorizer Only** (you register the demo holder agent later
   with the admin key via `tenuo-admin setup`)
3. Copy the connect token (`tenuo_ct_…`) — shown once; treat like a password
4. `cp cloud.env.example .state/cloud.env` and paste:

   ```bash
   export TENUO_CONNECT_TOKEN="tenuo_ct_..."
   export TENUO_AUTHORIZER_NAME="claude-code-demo"
   ```

   Alternative: Quick Connect **Manual** tab — copy `TENUO_CONTROL_PLANE_URL`
   + `TENUO_API_KEY` (`https://api.tenuo.ai`, no `/v1` suffix).

Do **not** paste `ak_…` key IDs from the API Keys table; those are identifiers, not
secrets. The connect token embeds the real `tc_…` bearer key used on Cloud API calls.

**Admin key (not in Quick Connect — create or request separately)**

Tenant administration: register holder agents, create/update triggers, wire approval
policies. Either:

1. **Dashboard:** Settings → API Keys → Create key with the **tenant admin**
   role, then `cp admin.env.example ~/.tenuo/admin.env` and paste it, or
2. **Onboarding:** admin key from whoever provisioned your tenant.

This is a **platform / prep step**: admin registers the holder agent and
creates the Cloud trigger from `tenuo.yaml`. Run `tenuo-admin setup` once
before the demo (or after policy changes), not on every `up`. Day-to-day
runtime uses `.state/cloud.env` only.

**Why Authorizer Only, not Agent + Authorizer?**

Quick Connect **Agent + Authorizer** is for embedded SDKs where one process owns
both identities and auto-claims the agent with the authorizer's signing key. This
demo is a **sidecar PEP**: the authorizer container verifies; Claude Code (via the
hook) is the holder that signs PoP. Those are separate keys:

| Identity | Key material | Role |
|----------|--------------|------|
| **Authorizer service account** | Quick Connect `tc_…` in `.state/cloud.env` | Heartbeat, SRL sync, receipts, trigger fire, agent claim API |
| **Holder agent** | `.state/holder_key.b64` (local Ed25519) | Signs PoP on every tool call |
| **Tenant admin** | `~/.tenuo/admin.env` | Creates agent + trigger (setup only) |

PoP is checked **locally by the authorizer**, not by Cloud on each call:

1. `tenuo-admin setup` registers a holder agent and **claims** it with the public
   key from `.state/holder_key.b64`.
2. `tenuo-claude up` fires the trigger; Cloud issues a warrant whose `holder`
   field resolves to that claimed key (`holder: ${event.agent_id}` in the trigger
   template).
3. On each tool call the hook signs PoP with the **holder private key** and sends
   `X-Tenuo-Warrant` + `X-Tenuo-PoP` to the authorizer.
4. The authorizer verifies the warrant chain and that PoP matches the holder.

Using **Agent + Authorizer** Quick Connect would pre-create and auto-claim an agent
with a *different* key than `.state/holder_key.b64`, so warrants and PoP would not
match. **Authorizer Only** gives the sidecar credentials; `tenuo-admin setup` wires
the holder agent and PoP key separately.

```bash
git clone https://github.com/tenuo-ai/claude-governance.git
cd claude-governance/demo

uv venv && uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate

mkdir -p .state ~/.tenuo
cp ../cloud.env.example .state/cloud.env
cp ../admin.env.example ~/.tenuo/admin.env

tenuo-claude init --cloud
tenuo-admin setup
tenuo-claude init
tenuo-claude up
tenuo-claude verify
tenuo-claude demo
```

For the **advanced** human-approval beat, add the overlay separately (see
[Advanced — Beat 5](#advanced--beat-5--human-approval-optional-3-min)) — do not enable
it for a first run unless you need that beat.

Confirm Docker is running. Claude auth: `claude -p "hi"` once.

**Cloud dashboard:** open cloud.tenuo.ai → Receipts (rows appear after `tenuo-claude demo`).
Optional second monitor: `docs/images/` screenshots from the product README.

### Pre-stage on screen

- `tenuo.yaml` open in an editor (show `enforce`, `subagents`, `mcp`)
- `tenuo.cloud.yaml` for Cloud; `tenuo.advanced.yaml` only if running the approval beat
- Architecture diagram: `tenuo_claude_code_architecture.svg` or README
- `tenuo-claude status` (warrant id, Cloud registration, subagents)
- [cloud.tenuo.ai](https://cloud.tenuo.ai) Receipts tab
- Skim `sandbox/incident-report.md` (know where the injection is; don't spoil it upfront)

---

## Talk track (~15 min)

1. Problem — tools can reach sensitive resources; model refusal is not a capability boundary.
2. Model — `tenuo.yaml` → warrant, hook, MCP proxy, authorizer. Decision is outside the model.
3. Proof — deny still happens with `--dangerously-skip-permissions`.
4. Enterprise — tenant-root warrants, Cloud receipts, optional approval, revoke in ~30s.
5. Scope — agent tool calls only, not interactive `!` shell. [Map vs Territory](https://niyikiza.com/posts/map-territory/) if they ask about Bash/DNS.

---

## Live demo script (~25–30 min)

Run from the demo directory with the authorizer up and Cloud connected.

### Beat 1 — Policy is one file (2 min)

Show `tenuo.yaml`: `sandbox`, `enforce`, `WebFetch` domains, `subagents`, `mcp`.
(If running the advanced beat later, show `tenuo.advanced.yaml` separately.)

```bash
tenuo-claude status
```

**Say:** Everything downstream is generated from this file. Cloud fires a root-signed
session warrant; hooks and proxy cannot drift.

### Beat 2 — Deterministic tour + Cloud receipts (4 min)

```bash
tenuo-claude demo
tenuo-claude audit --tail 20
```

Switch to **cloud.tenuo.ai → Receipts** — same allow/deny/approved rows, signed and retained.

**Say:** Every CLI line is a real authorizer decision. Cloud is the fleet view and audit record.

Call out: poisoned-file read denied, `delete_deployment` denied, shlex blocks chaining,
SSRF URLs denied, off-allowlist WebFetch **denied by allowlist** (default tour).

### Beat 3 — Guardrails off, governance on (5 min)

Use `--dangerously-skip-permissions` so Claude's permission UI does not dominate.

```bash
# allowed
claude -p "Read sandbox/notes.txt and summarize."

# denied
claude -p "Read /etc/hosts" --dangerously-skip-permissions

# injection narrative
claude -p "Summarize sandbox/incident-report.md for me." --dangerously-skip-permissions

# MCP destruction
claude -p "Use delete_deployment to tear down production." --dangerously-skip-permissions
```

**Say:** We are not detecting injection. The warrant never granted those capabilities.

Refresh Cloud Receipts and/or:

```bash
tenuo-claude audit --tail 15
```

### Beat 4 — Subagent attenuation (3 min)

```bash
claude -p "Use the researcher subagent to run 'ls -la sandbox' and report the result." \
  --dangerously-skip-permissions
```

**Say:** The session can run `ls`; the researcher child warrant cannot. Attenuation is
cryptographic — show `agent_type=researcher` in audit / Cloud.

### Advanced — Beat 5 — Human approval (optional, 3 min)

**Not part of the default tour.** Requires `tenuo.advanced.yaml`, a **pre-provisioned
approver identity** in Cloud, and `tenuo-admin setup`.

**Platform prep** (before the call — [Adding channels](https://docs.tenuo.ai/guides/adding-channels),
[Identity bindings](https://docs.tenuo.ai/integrations/identity-bindings)):

```bash
tenuo-claude init --advanced --approver "Jane Doe"   # exact Cloud Display Name
tenuo-admin setup
tenuo-claude demo --advanced
tenuo-claude demo --advanced --live-approval   # blocks until approver responds
```

Have the approver online on their configured notification channel. After approve,
show receipt detail in Cloud (approver, request hash) — see `docs/images/cloud-receipt-approval-detail.png`.

### Beat 6 — Revocation + fail-closed (4 min)

**Revoke** (Cloud dashboard or admin API): copy warrant id from `status`, revoke in
Cloud → next tool call denied within ~30s (SRL sync). No laptop reimage.

**Fail-closed** (high impact):

```bash
mv tenuo.yaml tenuo.yaml.bak
# any claude -p tool call → denied: Tenuo hook error (fail-closed): Missing …/tenuo.yaml
mv tenuo.yaml.bak tenuo.yaml
```

**Say:** Misconfiguration denies everything; it does not fail open.

### Beat 7 — Enterprise rollout (2 min)

- Managed-settings JSON (product README, Enterprise deployment)
- Admin vs runtime keys: `tenuo-admin setup` vs `tenuo-claude up`
- Rollout: `mode: audit` → review `WOULD-DENY` in Cloud → `mode: enforce`
- Forward [README § Security](../../README.md#security) to their reviewer

---

## By audience

**Security / platform** — architecture, Cloud receipts, `verify`. Beats 2, 4, 5, 6.
Expect questions on `!` bash, DNS, hook exit codes ([DETAILS.md](../../docs/DETAILS.md)).

**App / eng leadership** — injection file, delete_prod, one yaml. `tenuo-claude demo`
next to Cloud dashboard. Rollout: audit → tune → enforce.

**Exec (~10 min)** — diagram, one Cloud screenshot, one allow and two denies, revoke.

---

## Pitfalls

- Forgot `source .venv/bin/activate` or ran `init` outside venv → hooks point at wrong Python
- Authorizer not up → `tenuo-claude demo` fails immediately
- `verify --deep` without `--no-live` on slow network (two live `claude -p`, up to ~90s each)
- Changed `tenuo.yaml` policy → re-run `tenuo-admin setup` then `tenuo-claude up`
- Do not name Telegram/Slack unless that channel is wired — say "approver's configured channel"
- Model refuses on injection file → fine; still show audit if any tool was attempted
- Staging tenant receipt volume looks noisy — scroll to your demo session rows

---

## After the call

1. Send repo link + [README](../../README.md) + [DETAILS.md](../../docs/DETAILS.md)
2. Offer follow-up: draft their `tenuo.yaml` (sandbox, MCP tools, approver identity)
3. Pilot proposal: `mode: audit` on one team, Cloud stream for tuning, then enforce

---

## Day-of checklist

- [ ] `uv sync`; `source .venv/bin/activate`
- [ ] Admin key in `~/.tenuo/admin.env`; authorizer key in `.state/cloud.env`
- [ ] `tenuo-admin setup` completed (once; re-run only after policy changes)
- [ ] `tenuo-claude status` healthy (Cloud registered)
- [ ] `tenuo-claude demo` clean run
- [ ] Claude auth works (if live prompts)
- [ ] Terminal font readable on screen share
- [ ] Second pane: Cloud Receipts (+ optional README screenshots)
- [ ] Approver on standby (if live approval beat)
- [ ] Docker up; authorizer image `0.1.0-beta.24` (see package pin in `cli.py`)
