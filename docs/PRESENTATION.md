# Customer presentation guide

Runbook for presenting the Tenuo + Claude Code governance demo on **Tenuo Cloud**
(root-signed warrants, central audit, optional human approval). For mechanics
and reviewer depth, see [DETAILS.md](DETAILS.md). For install, see [README.md](../README.md).

---

## Before the call (30–60 min)

### Environment

Requires [uv](https://docs.astral.sh/uv/), Docker, Claude Code, and a Tenuo Cloud
tenant with **two API keys** (different roles — see below).

### Cloud credentials (before `setup`)

You need a tenant on [cloud.tenuo.ai](https://cloud.tenuo.ai) (staging or production).
If you do not have one yet, request access via [tenuo.ai/early-access](https://tenuo.ai/early-access.html)
or use the tenant your platform team already provisioned.

You need **two keys** with different RBAC roles. They land in **different files**
so runtime never sees the admin key:

| Key | Role / source | File | Used by |
|-----|---------------|------|---------|
| **Runtime** | Quick Connect (authorizer service account) | `.state/cloud.env` | `tenuo_claude.py up`, hooks, demo |
| **Admin** | Tenant admin (not in Quick Connect) | `~/.tenuo/admin.env` | `tenuo_admin.py setup` **once** |

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

   Alternative: in Quick Connect choose deployment **Manual** and copy
   `TENUO_CONTROL_PLANE_URL` + `TENUO_API_KEY` instead (staging:
   `https://api-staging.tenuo.ai`, prod: `https://api.tenuo.ai` — no `/v1` suffix).

Do **not** paste `ak_…` key IDs from the API Keys table; those are identifiers, not
secrets. The connect token embeds the real `tc_…` bearer key used on Cloud API calls.

**Admin key (not in Quick Connect — create or request separately)**

Tenant administration: register holder agents, create/update triggers, wire approval
policies. Either:

1. **Dashboard:** Settings → API Keys → Create key with the **tenant admin**
   role, then `cp admin.env.example ~/.tenuo/admin.env` and paste it, or
2. **Onboarding:** use the separate admin key your Tenuo contact sent when the
   tenant was provisioned (common for early-access / staging).

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
git clone https://github.com/tenuo-ai/claude-governance.git   # or use your local copy
cd claude-governance

uv venv && uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate

mkdir -p .state ~/.tenuo
cp cloud.env.example .state/cloud.env      # authorizer key + API URL
cp admin.env.example ~/.tenuo/admin.env    # admin key (setup only)

# Policy: merge cloud + WebFetch approval from tenuo.yaml.cloud.example into tenuo.yaml
# (cloud.approver_identity, enforce.WebFetch.approval — subagents: can stay on)

python3 tenuo_admin.py setup               # needs admin.env + cloud.env
python3 tenuo_claude.py init               # hooks pin this venv's python — re-run if you change venvs
python3 tenuo_claude.py up                 # runtime uses cloud.env only (no admin key)
python3 tenuo_claude.py doctor --no-live
python3 tenuo_demo.py
python3 tenuo_demo.py --live-approval      # optional dry-run; approver must respond
```

Confirm Docker is running. Claude auth: `claude -p "hi"` once.

**Cloud dashboard:** open [cloud.tenuo.ai](https://cloud.tenuo.ai) → Receipts (demo
rows should appear after `tenuo_demo.py`). Skim README screenshots in
`docs/images/` if you want them on a second monitor.

### Pre-stage on screen

- `tenuo.yaml` open in an editor (show `cloud:`, `enforce`, `subagents`, `mcp`)
- Architecture diagram: `tenuo_claude_code_architecture.svg` or README
- `python3 tenuo_claude.py status` (root-signed warrant, web-approval, subagents)
- [cloud.tenuo.ai](https://cloud.tenuo.ai) Receipts tab
- Skim `sandbox/incident-report.md` (know where the injection is; don't spoil it upfront)

---

## Narrative arc (~15 min talk track)

1. **Problem** — Claude Code tools can touch sensitive resources; model refusal and UI prompts are not a capability boundary.
2. **Model** — One policy file (`tenuo.yaml`) → Cloud-issued warrant, hook, MCP proxy, authorizer. The decision lives outside the model.
3. **Proof** — Deny still happens under `--dangerously-skip-permissions` (Claude guardrails off, Tenuo on).
4. **Enterprise** — Tenant-root warrants, signed receipt stream in Cloud, human approval for exceptions, revoke in ~30s.
5. **Honest scope** — Agent tool calls only; not interactive `!` shell. Map vs Territory if they push on Bash/DNS: [niyikiza.com/posts/map-territory](https://niyikiza.com/posts/map-territory/).

---

## Live demo script (~25–30 min)

Run from the demo directory with the authorizer up and Cloud connected.

### Beat 1 — Policy is one file (2 min)

Show `tenuo.yaml`: `cloud`, `sandbox`, `enforce`, `WebFetch` (+ `approval` if enabled),
`subagents`, `mcp`.

```bash
python3 tenuo_claude.py status
```

**Say:** Everything downstream is generated from this file. Cloud fires a root-signed
session warrant; hooks and proxy cannot drift.

### Beat 2 — Deterministic tour + Cloud receipts (4 min)

```bash
python3 tenuo_demo.py
python3 tenuo_claude.py audit --tail 20
```

Switch to **cloud.tenuo.ai → Receipts** — same allow/deny/approved rows, signed and retained.

**Say:** Every CLI line is a real authorizer decision. Cloud is the fleet view and audit record.

Call out: poisoned-file read denied, `delete_deployment` denied, shlex blocks chaining,
SSRF URLs denied, off-allowlist WebFetch **PAUSE** (approval) if wired.

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
python3 tenuo_claude.py audit --tail 15
```

### Beat 4 — Subagent attenuation (3 min)

```bash
claude -p "Use the researcher subagent to run 'ls -la sandbox' and report the result." \
  --dangerously-skip-permissions
```

**Say:** The session can run `ls`; the researcher child warrant cannot. Attenuation is
cryptographic — show `agent_type=researcher` in audit / Cloud.

### Beat 5 — Human approval (optional, 3 min)

Requires `WebFetch.approval` + `cloud.approver_identity` in `tenuo.yaml` and
`tenuo-admin setup`. Works **with** `subagents:` when roles omit WebFetch (default
`researcher` is read-only).

```bash
python3 tenuo_demo.py --live-approval
```

Have the approver ready on their configured notification channel. After approve,
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

- Managed-settings JSON (README Enterprise deployment section)
- Admin vs runtime keys: `tenuo_admin.py setup` vs `tenuo_claude.py up`
- Rollout: `mode: audit` → review `WOULD-DENY` in Cloud → `mode: enforce`
- Forward [docs/SECURITY-TEAM.md](SECURITY-TEAM.md) to their reviewer

---

## Audience-specific tips

**Security / platform**

- Lead with architecture + Cloud receipt stream + `doctor`
- Beats 2, 4, 5, 6 (audit trail, attenuation, approval proof, revocation)
- Expect `!` bash, DNS, hook exit codes — README Security boundaries + DETAILS

**App / eng leadership**

- Lead with injection + delete_prod + one yaml file
- `tenuo_demo.py` + Cloud dashboard side-by-side
- Rollout: audit → tune → enforce

**Exec (10 min max)**

- Diagram → Cloud receipts screenshot → one allow, two denies → revoke story

---

## Pitfalls

- Forgot `source .venv/bin/activate` or ran `init` outside venv → hooks point at wrong Python
- Authorizer not up → `tenuo_demo.py` fails immediately
- `doctor` without `--no-live` on slow network (two live `claude -p`, up to ~90s each)
- Changed `tenuo.yaml` policy → re-run `tenuo-admin setup` then `tenuo-claude up`
- Do not name Telegram/Slack unless that channel is wired — say "approver's configured channel"
- Model refuses on injection file → fine; still show audit if any tool was attempted
- Staging tenant receipt volume looks noisy — scroll to your demo session rows

---

## After the call

1. Send repo link + [docs/SECURITY-TEAM.md](SECURITY-TEAM.md) + [DETAILS.md](DETAILS.md)
2. Offer follow-up: draft their `tenuo.yaml` (sandbox, MCP tools, approver identity)
3. Pilot proposal: `mode: audit` on one team, Cloud stream for tuning, then enforce

---

## Day-of checklist

- [ ] `uv sync`; `source .venv/bin/activate`
- [ ] Admin key in `~/.tenuo/admin.env`; authorizer key in `.state/cloud.env`
- [ ] `tenuo-admin setup` completed (once; re-run only after policy changes)
- [ ] `python3 tenuo_claude.py status` healthy (Cloud registered)
- [ ] `python3 tenuo_demo.py` clean run
- [ ] Claude auth works (if live prompts)
- [ ] Terminal font readable on screen share
- [ ] Second pane: Cloud Receipts (+ optional README screenshots)
- [ ] Approver on standby (if live approval beat)
- [ ] Docker up; authorizer image `0.1.0-beta.24` (see `tenuo_claude.py` pin)
