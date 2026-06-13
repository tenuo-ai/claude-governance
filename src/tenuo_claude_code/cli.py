#!/usr/bin/env python3
"""tenuo-claude — govern, enforce, and audit Claude Code with Tenuo.

One CLI driven by one policy file (tenuo.yaml in your project directory). It
generates the warrant, gateway config, Claude hooks, and MCP proxy wiring so
nothing drifts, and manages the Cloud-connected authorizer lifecycle.

Install: ``pip install tenuo-claude-code``  (command: ``tenuo-claude``)

    tenuo-claude init      # generate keys, warrant, gateway, Claude hooks
    tenuo-claude bootstrap # example policy + init + up + verify (fresh folder)
    tenuo-claude refresh   # re-apply tenuo.yaml after policy edits
    tenuo-claude up        # start the authorizer (+ connect Cloud if configured)
    tenuo-claude status    # warrant / authorizer / Cloud / policy summary
    tenuo-claude check     # preflight: deps, credentials, wiring drift
    tenuo-claude verify    # policy self-test against the authorizer
    tenuo-claude demo      # scripted tour (tenuo_demo.py in project, if present)
    tenuo-claude bench     # per-tool-call overhead (authorizer + hooks)

Internal entrypoints (wired into Claude, not called by hand):
    _hook  _post  _mcp-proxy

Optional scripted tour: ``tenuo_demo.py`` in your project directory (see repo ``demo/``).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tenuo_claude_code import authorizer_runtime as art
from tenuo_claude_code.paths import (
    ADMIN_COMMAND,
    ADMIN_COMMAND_LEGACY,
    BIN_LAUNCHER,
    CLI_COMMAND,
    CLI_COMMAND_LEGACY,
    bind_project_paths,
    scaffold_example_policy,
)
from tenuo_claude_code.verify import ProbeResult, build_probes, format_text, run_probes

# ---------------------------------------------------------------------------
# Paths & constants (bound to project root via bind_project_paths() at startup)
# ---------------------------------------------------------------------------

DEMO_DIR: Path
STATE: Path
CONFIG_FILE: Path
CLOUD_PROFILE: Path
ADVANCED_PROFILE: Path
CLOUD_ENV_EXAMPLE: Path
HARNESS_TOOLS_FILE: Path
AGENTS_DIRS: tuple
LAUNCHER: Path
LAUNCHER_REL: str
MCP_SERVER_NAME = "tenuo-files"
HOLDER_KEY: Path
ISSUER_KEY: Path
ISSUER_PUB: Path
WARRANT: Path
STATE_JSON: Path
GATEWAY: Path
SRL: Path
RECEIPTS: Path
CLOUD_ENV: Path
CLOUD_STATE: Path

ADMIN_ENV = Path.home() / ".tenuo" / "admin.env"
_receipt_write_warned = False  # one-time stderr if .state/receipts.jsonl can't be written

PORT = art.resolve_authorizer_port()
AUTHZ_URL = f"http://127.0.0.1:{PORT}"


def resolve_authz_url() -> str:
    """URL for authorizer client calls. ``state.json`` overrides port env vars."""
    try:
        if STATE_JSON.is_file():
            url = json.loads(STATE_JSON.read_text()).get("authorizer_url")
            if isinstance(url, str) and url.startswith("http"):
                return url
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return AUTHZ_URL


WARRANT_HEADER = "X-Tenuo-Warrant"
POP_HEADER = "X-Tenuo-PoP"
# The authorizer ships as a published container image (Docker Hub), pinned in
# lockstep with the `tenuo` PyPI package. Override with TENUO_AUTHORIZER_IMAGE or
# an `authorizer.image` key in tenuo.yaml.
DEFAULT_AUTHZ_IMAGE = "tenuo/authorizer:0.1.0-beta.24"

# Claude tool -> (capability, primary arg, Claude input field for that arg)
TOOL_DEFAULTS = {
    "Read": ("read_file", "path", "file_path"),
    "Write": ("write_file", "path", "file_path"),
    "Edit": ("edit_file", "path", "file_path"),
    "Bash": ("run_command", "command", "command"),
    "Glob": ("glob", "path", "path"),
    "Grep": ("grep", "path", "path"),
    "WebFetch": ("web_fetch", "url", "url"),
    "WebSearch": ("web_search", "query", "query"),
    "NotebookEdit": ("notebook_edit", "path", "notebook_path"),
}
_CAP_TO_CLAUDE = {cap: tool for tool, (cap, *_) in TOOL_DEFAULTS.items()}


def claude_tool_for_cap(cap: str) -> str:
    """Map a Tenuo capability back to the Claude tool name (fallback: cap)."""
    return _CAP_TO_CLAUDE.get(cap, cap)
# Catch-all capability/tool names. "audit" is granted in the warrant (allow +
# log); "unlisted" is intentionally NOT granted, so routing to it yields a
# signed DENY receipt (used when default: deny).
CATCHALL_AUDIT = "audit"
CATCHALL_DENY = "unlisted"


def slug(name: str) -> str:
    return name.lower().replace("-", "_")


def wiring_command_parts(subcommand: str) -> tuple[str, list[str]]:
    """Stable command for Claude hooks / MCP wiring (portable across machines).

    Priority: ``TENUO_CLAUDE_BIN`` → repo ``bin/tenuo-claude`` → ``tenuo-claude``
    on PATH (bare name) → ``tenuo-claude-code`` alias → ``python -m`` fallback.
    """
    override = os.environ.get("TENUO_CLAUDE_BIN", "").strip()
    if override:
        return override, [subcommand]
    if LAUNCHER.is_file():
        return LAUNCHER_REL, [subcommand]
    for name in (CLI_COMMAND, CLI_COMMAND_LEGACY):
        if shutil.which(name):
            return name, [subcommand]
    return sys.executable, ["-m", "tenuo_claude_code.cli", subcommand]


def wiring_command_string(subcommand: str) -> str:
    cmd, args = wiring_command_parts(subcommand)
    return shlex.join([cmd, *args])


def mcp_wiring(cfg: dict) -> dict | None:
    """Expected ``.mcp.json`` content when ``mcp.downstream`` is configured."""
    if not cfg.get("mcp", {}).get("downstream"):
        return None
    cmd, args = wiring_command_parts("_mcp-proxy")
    return {"mcpServers": {MCP_SERVER_NAME: {"command": cmd, "args": args}}}


def audit_map(cfg: dict) -> dict:
    """Claude tool name -> capability name for the audit-allow list."""
    out = {}
    for tool in cfg.get("audit", []) or []:
        out[tool] = TOOL_DEFAULTS.get(tool, (slug(tool),))[0]
    return out


def default_mode(cfg: dict) -> str:
    mode = (cfg.get("default") or "deny").lower()
    return mode if mode in ("deny", "audit") else "deny"


def catchall_cap(cfg: dict) -> str:
    """Capability the /gate catch-all routes to: the granted 'audit' (under
    default: audit -> allow + log) or the ungranted 'unlisted' (under default:
    deny -> the authorizer returns a signed DENY)."""
    return CATCHALL_AUDIT if default_mode(cfg) == "audit" else CATCHALL_DENY


def subagent_roles(cfg: dict) -> dict:
    """Declared subagent roles (name -> {tools, ttl_seconds, ...}); {} when none."""
    return cfg.get("subagents") or {}


def webfetch_approval(cfg: dict) -> dict | None:
    """`enforce.WebFetch.approval` block if declared, else None (Cloud human-approval gate)."""
    wf = (cfg.get("enforce") or {}).get("WebFetch")
    if isinstance(wf, dict) and isinstance(wf.get("approval"), dict):
        return wf["approval"]
    return None


def is_audit_mode(cfg: dict) -> bool:
    """Global observe-only posture (`mode: audit` in tenuo.yaml).

    When on, the hook and MCP proxy still compute the REAL allow/deny against the
    warrant and write it to the signed receipt — but never block. You get the
    full audit trail (including what WOULD be denied) with zero enforcement, for
    safe rollout / shadowing. Flip back with `mode: enforce` (the default).
    """
    return str(cfg.get("mode", "enforce")).strip().lower() == "audit"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_harness_tools() -> list[str]:
    import yaml

    if not HARNESS_TOOLS_FILE.exists():
        return []
    data = yaml.safe_load(HARNESS_TOOLS_FILE.read_text()) or {}
    return [str(t) for t in (data.get("tools") or [])]


def _deep_merge(base: dict, overlay: dict) -> None:
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_config() -> dict:
    import yaml

    if not CONFIG_FILE.exists():
        raise SystemExit(f"Missing {CONFIG_FILE}")
    cfg = yaml.safe_load(CONFIG_FILE.read_text()) or {}
    if CLOUD_PROFILE.exists():
        overlay = yaml.safe_load(CLOUD_PROFILE.read_text()) or {}
        _deep_merge(cfg, overlay)
    if ADVANCED_PROFILE.exists():
        overlay = yaml.safe_load(ADVANCED_PROFILE.read_text()) or {}
        _deep_merge(cfg, overlay)
    cfg.setdefault("sandbox", "./sandbox")
    cfg["_sandbox_abs"] = str((DEMO_DIR / cfg["sandbox"]).resolve())
    cfg.setdefault("enforce", {})
    cfg.setdefault("mcp", {})
    if cfg.get("audit_bundled", True):
        bundled = load_harness_tools()
        legacy = cfg.get("audit") if isinstance(cfg.get("audit"), list) else []
        extra = [str(t) for t in (cfg.get("audit_extra") or [])]
        seen: set[str] = set()
        audit: list[str] = []
        for t in bundled + legacy + extra:
            if t not in seen:
                seen.add(t)
                audit.append(t)
        cfg["audit"] = audit
    else:
        cfg.setdefault("audit", [])
    return cfg


def make_constraint(spec: str, sandbox: str):
    """Constraint DSL -> tenuo constraint object."""
    from tenuo import Exact, OneOf, Pattern, Regex, Subpath
    from tenuo_core import Shlex  # core constraint type (mintable)

    spec = spec.replace("{sandbox}", sandbox)
    kind, _, rest = spec.partition(":")
    if kind == "subpath":
        return Subpath(rest)
    if kind == "shlex":
        return Shlex(allow=[v.strip() for v in rest.split(",") if v.strip()])
    if kind == "regex":
        return Regex(rest)
    if kind == "pattern":
        return Pattern(rest)
    if kind == "oneof":
        return OneOf([v.strip() for v in rest.split(",")])
    if kind == "exact":
        return Exact(rest)
    raise SystemExit(f"Unknown constraint kind '{kind}' in '{spec}'")


def make_web_constraints(policy: dict, *, approval_gate: bool = False) -> dict:
    """Org egress policy -> {url: UrlSafe, host: AnyOf(domains|cidrs) or Wildcard}.

    approval_gate=True: SSRF-only url + wildcard host; domain policy moves to Cloud gate.
    """
    from tenuo_core import AnyOf, Cidr, Pattern, UrlSafe, Wildcard

    domains = [str(d) for d in policy.get("domains") or []]
    cidrs = [str(c) for c in policy.get("cidrs") or []]
    if not domains and not cidrs:
        raise SystemExit("WebFetch policy needs at least one of: domains, cidrs")
    # https-only unless internal ranges are in play (internal services are
    # often plain http); explicit `schemes:` in the policy overrides.
    schemes = [str(s) for s in policy.get("schemes") or
               (["https"] if not cidrs else ["http", "https"])]
    kwargs = dict(allow_schemes=schemes,
                  block_private=not cidrs, block_loopback=True,
                  block_metadata=True, block_reserved=True)
    if policy.get("ports"):
        kwargs["allow_ports"] = [int(p) for p in policy["ports"]]
    url = UrlSafe(**kwargs)
    if approval_gate:
        return {"url": url, "host": Wildcard()}
    members = [Pattern(d) for d in domains] + [Cidr(c) for c in cidrs]
    host = members[0] if len(members) == 1 else AnyOf(members)
    return {"url": url, "host": host}


def governed_map(cfg: dict) -> dict:
    """Claude tool -> dict(capability, arg, field, constraint spec/policy)."""
    out = {}
    for tool, spec in cfg["enforce"].items():
        if tool not in TOOL_DEFAULTS:
            raise SystemExit(f"enforce: unknown tool '{tool}'")
        cap, arg, field = TOOL_DEFAULTS[tool]
        if isinstance(spec, dict):
            if tool != "WebFetch":
                raise SystemExit(f"enforce: structured policy is only for WebFetch, not '{tool}'")
            out[tool] = {"cap": cap, "arg": arg, "field": field, "web": spec}
        else:
            out[tool] = {"cap": cap, "arg": arg, "field": field, "spec": spec}
    return out


def subwarrant_path(role: str) -> Path:
    return STATE / f"subwarrant_{slug(role)}.b64"


def refresh_subwarrants(cfg: dict) -> None:
    """Mint one attenuated child warrant per declared subagent role.

    Each child is the live session (parent) warrant narrowed to the role's tool
    subset, delegated to the same holder. The parent is the ceiling: a subagent
    can only ever do LESS than the session, never more — and the parent->child
    delegation is what the audit trail shows. Re-minted on every `up` so the
    children always chain to the current session warrant. Works identically for
    locally-minted and Cloud-issued session warrants.
    """
    for stale in STATE.glob("subwarrant_*.b64"):
        stale.unlink()
    roles = subagent_roles(cfg)
    if not roles:
        return
    from tenuo import SigningKey, Warrant
    from tenuo_core import encode_warrant_stack

    parent = Warrant.from_base64(WARRANT.read_text())
    holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
    parent_caps = set((parent.capabilities or {}).keys())
    for role, rc in roles.items():
        rc = rc or {}
        caps = []
        for tool in rc.get("tools") or []:
            if tool not in TOOL_DEFAULTS:
                raise SystemExit(f"subagents.{role}: unknown tool '{tool}'")
            caps.append(TOOL_DEFAULTS[tool][0])
        missing = [c for c in caps if c not in parent_caps]
        if missing:
            raise SystemExit(
                f"subagents.{role}: {missing} not granted by the session warrant. "
                "A subagent can't exceed the parent — add the tool to `enforce` first.")
        builder = parent.attenuate_builder()
        builder.inherit_all()
        builder.with_tools(caps)  # retain subset, keep the parent's constraints
        builder.with_intent(f"subagent:{role}")
        if rc.get("ttl_seconds"):
            builder.with_ttl(int(rc["ttl_seconds"]))
        child = builder.delegate(holder)
        # Persist the full chain (parent..child): a delegated warrant must be
        # presented as a WarrantStack so the authorizer can verify to the root.
        write_secret(subwarrant_path(role),
                     encode_warrant_stack([parent, child]))


def _parse_env_value(raw: str) -> str:
    """Parse one KEY=VALUE tail from a shell-style env file."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in '"\'':
        q = raw[0]
        end = raw.find(q, 1)
        if end != -1:
            return raw[1:end]
        return raw.strip(q)
    # Unquoted: strip trailing inline comment.
    if "#" in raw:
        raw = raw[: raw.index("#")].strip()
    return raw.strip().strip('"').strip("'")


def read_env_file(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = _parse_env_value(v)
    return env


def runtime_env() -> dict:
    """Process environment overlaid with .state/cloud.env (the runtime/authorizer
    credentials). The single place those two sources are merged."""
    env = dict(os.environ)
    env.update(read_env_file(CLOUD_ENV))
    return env


def _parse_connect_token(raw: str) -> dict:
    """Extract endpoint + API key from a dashboard Quick Connect token."""
    try:
        from tenuo_core import ConnectToken

        ct = ConnectToken.parse(raw)
        return {"url": ct.endpoint, "api_key": ct.api_key}
    except ImportError as e:
        raise SystemExit(
            "TENUO_CONNECT_TOKEN requires tenuo_core (install tenuo==0.1.0b24)."
        ) from e
    except Exception as e:
        raise SystemExit(f"Invalid TENUO_CONNECT_TOKEN: {e}") from e


# ---------------------------------------------------------------------------
# Enforcement core (shared by the hook, the MCP proxy, verify, and tenuo_demo)
# ---------------------------------------------------------------------------


def _authorize_attempt(tenuo_tool: str, route: str, sign_args: dict, body,
                       warrant_b64: str | None, approvals_b64: str | None = None):
    """One signed authorizer call. Returns (allowed, reason, response_body|{}).

    Fail-closed: any signing/transport error -> (False, reason, {}). `approvals_b64`
    attaches base64-CBOR SignedApproval(s) (the X-Tenuo-Approvals header) so a
    previously approval-gated call can be re-authorized.
    """
    try:
        from tenuo import SigningKey
        from tenuo_core import decode_warrant_stack_base64

        holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
        header_b64 = warrant_b64 if warrant_b64 is not None else WARRANT.read_text()
        # The header may carry a single warrant or a WarrantStack; either decodes
        # to a chain (single -> length 1). The leaf (last) is what signs the PoP.
        leaf = decode_warrant_stack_base64(header_b64)[-1]
        pop = leaf.sign(holder, tenuo_tool, sign_args, int(time.time()))
        headers = {
            WARRANT_HEADER: header_b64,
            POP_HEADER: base64.b64encode(bytes(pop)).decode("ascii"),
            "Content-Type": "application/json",
        }
        if approvals_b64:
            headers[APPROVALS_HEADER] = approvals_b64
        req = urllib.request.Request(
            resolve_authz_url() + route, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
    except Exception as exc:
        return False, f"enforcement error ({exc})", {}
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            rb = json.loads(resp.read().decode() or "{}")
            if resp.status == 200 and rb.get("authorized"):
                return True, "authorized", rb
            return False, rb.get("message") or rb.get("error") or f"status {resp.status}", rb
    except urllib.error.HTTPError as exc:
        try:
            rb = json.loads(exc.read().decode() or "{}")
            return False, rb.get("message") or rb.get("error") or f"status {exc.code}", rb
        except Exception:
            return False, f"status {exc.code}", {}
    except Exception as exc:
        return False, f"authorizer unreachable, denying ({exc})", {}


def authorize(tenuo_tool: str, route: str, sign_args: dict, body=None,
              warrant_b64: str | None = None):
    """Sign PoP, ask the authorizer. Returns (allowed, reason). Fail-closed.

    `warrant_b64` overrides the session warrant — used to present a subagent's
    attenuated child warrant. A subagent's credential is a full WarrantStack
    (child + parent chain), needed so the authorizer can verify back to the
    trusted root; the LEAF signs the PoP. The holder key is the same in both
    cases (children are delegated to the session holder), so signing is uniform.
    """
    allowed, reason, _ = _authorize_attempt(
        tenuo_tool, route, sign_args, sign_args if body is None else body, warrant_b64)
    return allowed, reason


# `error_code` the authorizer returns when a gated capability is invoked without
# the required approvals (the call is paused, not denied — provide signatures).
APPROVAL_REQUIRED_CODE = 1707
APPROVALS_HEADER = "X-Tenuo-Approvals"
# Bounded wait for the approver. Kept under the Claude hook timeout budget.
# `timeout` set in generate() so the hook resolves rather than being killed.
APPROVAL_POLL_SECONDS = 150
APPROVAL_POLL_INTERVAL = 3
APPROVAL_TTL_SECONDS = 300
# Prefix the runtime uses to mark a "would require human approval" outcome so the
# hook (audit mode) and verify can distinguish it from a hard deny.
APPROVAL_PENDING_REASON = "approval required"


def encode_approvals_header(sigs: list) -> str:
    """Encode SignedApproval blob(s) for X-Tenuo-Approvals.

    The authorizer accepts base64 of either a single SignedApproval CBOR or a
    CBOR array of them. Each Cloud `signed_approval` is base64 of one CBOR blob,
    so a threshold-1 response passes through as-is; for N > 1 we wrap the decoded
    blobs in a CBOR array (header byte 0x80|N + concatenated items — valid for
    N < 24, far above any sane approval threshold).
    """
    if len(sigs) == 1:
        return sigs[0]
    items = [base64.b64decode(s) for s in sigs[:23]]
    return base64.b64encode(bytes([0x80 | len(items)]) + b"".join(items)).decode()


def request_cloud_approval(creds: dict, policy_id: str, claude_tool: str, tenuo_tool: str,
                           args: dict, rb: dict, warrant_b64: str | None):
    """Create a Cloud approval request, poll until resolved, return signed approval(s).

    Returns (signatures, reason). Signatures are base64 CBOR blobs for
    X-Tenuo-Approvals. The request_hash and attestation bind to the exact args.
    """
    from tenuo import SigningKey
    from tenuo.cp_approval import build_approval_context_attestation
    from tenuo_core import decode_warrant_stack_base64

    request_hash = rb.get("request_hash")
    if not request_hash:
        return [], "authorizer gave no request_hash for approval"
    try:
        holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
        header_b64 = warrant_b64 if warrant_b64 is not None else WARRANT.read_text()
        warrant_id = decode_warrant_stack_base64(header_b64)[-1].id
        # The attestation recomputes the request_hash from the SAME (warrant, tool,
        # args, holder) the authorizer hashed; if they diverge the gate args differ
        # and the approval would not apply — surface it rather than prompt a human.
        _, meta = build_approval_context_attestation(
            holder, warrant_id, tenuo_tool, args, holder.public_key)
    except Exception as exc:
        return [], f"could not build approval attestation ({exc})"
    if meta.get("request_hash") != request_hash:
        return [], (f"approval hash mismatch — gate args differ "
                    f"(authz={request_hash[:12]}…, local={str(meta.get('request_hash'))[:12]}…)")

    body = {
        "policy_id": policy_id,
        "warrant_id": warrant_id,
        "tool": tenuo_tool,
        "request_hash": request_hash,
        "holder_key": holder.public_key.to_bytes().hex(),
        "args_canonical_cbor_b64": meta["args_canonical_cbor_b64"],
        "approval_context_attestation": meta,
        "metadata": {"source": "claude-code", **args},
        "threshold": int(rb.get("required_approvals") or 1),
        "approver_keys": rb.get("required_approvers") or [],
        "ttl_seconds": APPROVAL_TTL_SECONDS,
    }
    status, resp = cloud_api("POST", creds["url"], creds["api_key"],
                             "/v1/approvals/requests", body)
    if status not in (200, 201) or not isinstance(resp, dict) or not resp.get("id"):
        return [], f"approval request rejected ({status}): {resp}"
    rid = resp["id"]
    receipt_base = {"phase": "pre", "source": "approval", "claude_tool": claude_tool,
                    "tenuo_tool": tenuo_tool, "args": args, "approval_request_id": rid}
    write_receipt({**receipt_base, "decision": "pending",
                   "reason": "awaiting human approval"})

    deadline = time.time() + APPROVAL_POLL_SECONDS
    while time.time() < deadline:
        time.sleep(APPROVAL_POLL_INTERVAL)
        s, ar = cloud_api("GET", creds["url"], creds["api_key"],
                          f"/v1/approvals/requests/{rid}")
        if s != 200 or not isinstance(ar, dict):
            continue
        st = ar.get("status")
        if st == "approved":
            # Go marshals []byte as standard base64; the authorizer accepts it
            # directly for X-Tenuo-Approvals (single SignedApproval CBOR).
            sigs = [r.get("signed_approval") for r in (ar.get("responses") or [])
                    if r.get("signed_approval")]
            if sigs:
                write_receipt({**receipt_base, "decision": "allow",
                               "reason": "approved"})
                return sigs, "approved"
            write_receipt({**receipt_base, "decision": "deny",
                           "reason": "approved but no signature returned"})
            return [], "approved but no signature returned"
        if st == "denied":
            reason = f"denied by approver ({ar.get('denied_reason') or 'no reason given'})"
            write_receipt({**receipt_base, "decision": "deny", "reason": reason})
            return [], reason
        if st == "expired":
            write_receipt({**receipt_base, "decision": "deny",
                           "reason": "approval window expired"})
            return [], "approval window expired"
    write_receipt({**receipt_base, "decision": "deny",
                   "reason": "approval timed out (no response from approver)"})
    return [], "approval timed out (no response from approver)"


def authorize_with_approval(cfg: dict, claude_tool: str, tenuo_tool: str, route: str,
                            sign_args: dict, body, warrant_b64: str | None, live: bool):
    """authorize() + Cloud approval retry on 1707. live=False = report-only (verify/demo)."""
    if body is None:
        body = sign_args
    allowed, reason, rb = _authorize_attempt(tenuo_tool, route, sign_args, body, warrant_b64)
    if allowed:
        return True, reason
    if not (isinstance(rb, dict) and rb.get("error_code") == APPROVAL_REQUIRED_CODE):
        return False, reason

    creds = cloud_creds(cfg)
    policy_id = load_cloud_state().get("web_fetch_approval_policy_id")
    if not (creds.get("url") and creds.get("api_key") and policy_id):
        return False, ("approval required, but the Cloud approver isn't configured — "
                       "run `tenuo-admin setup` (approval gates require Tenuo Cloud)")
    threshold = int(rb.get("required_approvals") or 1)
    if not live:
        return False, f"{APPROVAL_PENDING_REASON}: {threshold} approval(s) required"

    sigs, areason = request_cloud_approval(
        creds, policy_id, claude_tool, tenuo_tool, sign_args, rb, warrant_b64)
    if not sigs:
        return False, f"{APPROVAL_PENDING_REASON} — {areason}"
    allowed, reason, _ = _authorize_attempt(
        tenuo_tool, route, sign_args, body, warrant_b64,
        approvals_b64=encode_approvals_header(sigs))
    if allowed:
        return True, "approved"
    return False, f"re-authorize after approval failed: {reason}"


def mcp_tool_name(tool_name: str) -> str | None:
    """Bare MCP tool from a Claude MCP call name, else None.

    Claude exposes proxied MCP tools as `mcp__<server>__<tool>` and fires the
    PreToolUse hook on that prefixed name. We strip to the bare `<tool>` so the
    hook can enforce it against the SAME `mcp.enforce` policy the proxy uses —
    otherwise the prefixed name matches nothing and catch-all default-denies even
    allowed MCP tools, shadowing the proxy. `<tool>` may contain underscores; the
    `__` delimiter is double, so split(maxsplit=2) keeps it intact.
    """
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__", 2)
    return parts[2] if len(parts) == 3 and parts[2] else None


def resolve_tool(cfg: dict, tool_name: str, tool_input: dict):
    """Map a Claude tool call to (tenuo_tool, route, sign_args, body, governed).

    - enforced native tools: real constraints (may mutate within scope).
    - enforced MCP tools (bare downstream name or `mcp__<server>__<tool>` from the
      hook): same policy/route the proxy uses, so hook and proxy agree.
    - audit-listed tools: allowed + logged (no constraints).
    - everything else: catch-all. With default: deny it routes to a capability
      the warrant does NOT grant, so the authorizer returns a signed DENY.
    """
    gov = governed_map(cfg)
    if tool_name in gov:
        g = gov[tool_name]
        val = (tool_input or {}).get(g["field"])
        if "web" in g:
            url = val if isinstance(val, str) else ""
            try:
                host = urllib.parse.urlsplit(url).hostname or ""
            except ValueError:
                host = ""
            args = {"url": url, "host": host}
        else:
            if g.get("spec", "").startswith("subpath:"):
                # Tools like Glob/Grep default their search root to the cwd;
                # authorize what would actually be touched, not a blank.
                if not (isinstance(val, str) and val):
                    val = os.getcwd()
                # Resolve symlinks so a link inside the sandbox can't point
                # the Territory outside the Map we check (realpath ~= Layer 2
                # for pre-existing links; races still need path_jail).
                val = os.path.realpath(os.path.abspath(val))
            args = {g["arg"]: val}
        return g["cap"], f"/verify/{g['cap']}", args, args, True

    audit = audit_map(cfg)
    if tool_name in audit:
        cap = audit[tool_name]
        return cap, f"/verify/{cap}", {}, dict(tool_input or {}), False

    mcp_enforce = cfg.get("mcp", {}).get("enforce") or {}
    bare = mcp_tool_name(tool_name)
    if bare is None and tool_name in mcp_enforce:
        bare = tool_name
    if bare is not None and bare in mcp_enforce:
        # mcp.enforce keys on bare tool name + path arg only (demo assumes one
        # downstream server; a second server with the same tool name would share policy).
        # Mirror cmd_mcp_proxy: realpath the path arg (so symlinks/relatives
        # can't smuggle out) and authorize against /verify/<tool>. Same cap and
        # body field the proxy and gateway use, so the warrant check is identical.
        val = (tool_input or {}).get("path")
        if isinstance(val, str) and val:
            val = os.path.realpath(os.path.abspath(val))
        args = {"path": val}
        return bare, f"/verify/{bare}", args, args, True

    return catchall_cap(cfg), "/gate", {}, {"tool": tool_name, **(tool_input or {})}, False


# Claude Code's subagent-spawn tool(s). Empirically "Agent" on claude 2.1.x;
# "Task" is accepted too for forward/back-compat. If Anthropic renames the spawn
# tool, spawns default-deny (safe) unless the new name lands in the audit list
# (allow+log). verify --deep checks hook exit-code contract, not spawn-tool names.
SPAWN_TOOLS = ("Agent", "Task")
# Synthetic capability: spawning a subagent. Constrained to a oneof of declared
# roles, minted locally AND issuable via a Cloud trigger, so the spawn decision
# is a signed authorizer check (not a local-only policy gate) in both modes.
SPAWN_CAP = "spawn_agent"
# Claude Code's built-in subagent types (no .md file). Declaring one of these as
# a role is valid even without an agent definition. The list can drift across
# Claude versions, so it's only used to avoid false "unresolved" warnings.
BUILTIN_SUBAGENTS = frozenset({"general-purpose", "Explore", "Plan"})


def agent_definitions() -> dict[str, Path]:
    """Map every discoverable custom subagent_type -> its definition file.

    The type is the frontmatter `name:` (Claude's actual `subagent_type`), not the
    filename. Project-level definitions win over user-level on name collision.
    """
    import yaml

    found: dict[str, Path] = {}
    for base in AGENTS_DIRS:
        if not base.is_dir():
            continue
        for md in sorted(base.glob("*.md")):
            name = md.stem
            text = md.read_text(errors="replace")
            if text.startswith("---") and (end := text.find("\n---", 3)) != -1:
                fm = yaml.safe_load(text[3:end]) or {}
                if isinstance(fm, dict) and fm.get("name"):
                    name = str(fm["name"]).strip()
            found.setdefault(name, md)
    return found


def resolve_subagent_role(role: str, defs: dict[str, Path] | None = None) -> tuple[bool, str]:
    """Does a declared role map to a real, spawnable subagent_type? -> (ok, where)."""
    defs = agent_definitions() if defs is None else defs
    if role in defs:
        try:
            return True, str(defs[role].relative_to(DEMO_DIR))
        except ValueError:
            return True, str(defs[role])
    if role in BUILTIN_SUBAGENTS:
        return True, "built-in"
    return False, "no agent definition"


def authorize_call(cfg: dict, tool: str, tin: dict, agent_type, roles: dict,
                   live: bool = False, skip_approval_gate: bool = False):
    """Decide one tool call. Returns (allowed, reason, governed, tenuo_tool).

    Three layers, in order:
      1. SPAWN GATE — a main-thread Agent/Task call. With `subagents:` declared,
         spawning is a SIGNED capability (`spawn_agent`, root-signed in Cloud
         mode): the warrant's oneof decides which roles pass. With NO block
         declared, it's FLAT COVERAGE — spawning isn't gated and the subagent
         just runs under the session warrant (its inner calls stay enforced), so
         the spawn is audited, not default-denied.
      2. PER-SUBAGENT WARRANT — when roles are declared, a call made INSIDE a
         subagent runs under that role's attenuated child warrant, never the
         session warrant. An undeclared role (or missing child) gets nothing —
         fail-closed.
      3. SESSION — everything else (incl. in-subagent calls under flat coverage):
         the main-thread session warrant, as before.
    """
    if tool in SPAWN_TOOLS and not agent_type:
        if not roles:
            # Flat coverage: no subagent roles -> spawning is plain orchestration.
            # The subagent's tool calls still run under the session warrant, so
            # nothing escalates; record the spawn as audited rather than denying.
            return True, "flat coverage (no subagent roles declared)", False, tool
        # Signed spawn gate: ask the authorizer (root-signed in Cloud mode). The
        # warrant's spawn_agent oneof decides which declared roles may spawn.
        requested = (tin or {}).get("subagent_type") or ""
        allowed, reason = authorize(SPAWN_CAP, f"/verify/{SPAWN_CAP}",
                                    {"subagent_type": requested},
                                    {"subagent_type": requested})
        return allowed, reason, True, SPAWN_CAP

    tenuo_tool, route, sign_args, body, governed = resolve_tool(cfg, tool, tin)
    warrant_b64 = None
    if agent_type and roles:
        sw = subwarrant_path(agent_type)
        if agent_type not in roles or not sw.exists():
            return False, f"undeclared subagent '{agent_type}'", governed, tenuo_tool
        warrant_b64 = sw.read_text()
    # Any governed tool call can return approval-required (1707) when the warrant
    # includes an approval gate for that capability. The hook resolves it via
    # Cloud (live) or reports PAUSE (verify / demo / audit mode).
    if not skip_approval_gate:
        allowed, reason = authorize_with_approval(
            cfg, tool, tenuo_tool, route, sign_args, body, warrant_b64, live=live)
    else:
        allowed, reason = authorize(tenuo_tool, route, sign_args, body, warrant_b64=warrant_b64)
    return allowed, reason, governed, tenuo_tool


def write_receipt(entry: dict) -> None:
    global _receipt_write_warned
    try:
        ensure_state_dir()
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        with RECEIPTS.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        if not _receipt_write_warned:
            _receipt_write_warned = True
            print(f"warning: could not write receipt to {RECEIPTS}: {exc}",
                  file=sys.stderr)


# ---------------------------------------------------------------------------
# Internal entrypoints: Claude hooks + MCP proxy
# ---------------------------------------------------------------------------


def cmd_hook(_args) -> None:
    # FAIL-CLOSED GUARD. Claude Code treats hook exit code 2 as "block", but
    # any other non-zero exit (e.g. an unhandled traceback -> exit 1) is a
    # NON-blocking error and the tool call PROCEEDS. A crash here would be a
    # full bypass, so every internal error must end as an explicit deny
    # decision (exit 0 + JSON) — verified empirically against claude 2.1.x.
    decision, reason_text = "deny", "Tenuo hook internal error (fail-closed)"
    tool, agent_type, audit_only = "", None, False
    try:
        cfg = load_config()
        audit_only = is_audit_mode(cfg)
        try:
            event = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            event = {}
        tool = event.get("tool_name", "")
        tin = event.get("tool_input", {}) or {}
        # `agent_type` is populated by Claude Code ONLY when the call originates
        # inside a subagent (it's the subagent's frontmatter name). Main-thread
        # calls have no agent_type and run under the session warrant as usual.
        agent_type = event.get("agent_type")
        roles = subagent_roles(cfg)
        # Enforce mode drives the live approval flow; audit mode only reports.
        allowed, reason, governed, tenuo_tool = (
            authorize_call(cfg, tool, tin, agent_type, roles, live=not audit_only))
        write_receipt(
            {"phase": "pre", "decision": "allow" if allowed else "deny",
             "shadow": audit_only, "claude_tool": tool, "tenuo_tool": tenuo_tool,
             "governed": governed, "agent_type": agent_type, "args": tin,
             "reason": reason}
        )
        kind = "authorized" if governed else "audited"
        scope = f" (subagent:{agent_type})" if agent_type else ""
        decision = "allow" if allowed else "deny"
        if allowed:
            # Include approval outcome in the hook reason when relevant.
            extra = f" — {reason}" if "approv" in (reason or "").lower() else ""
            reason_text = f"Tenuo {kind}: {tool}{scope}{extra}"
        else:
            reason_text = f"Tenuo denied {tool}{scope}: {reason}"
    except (Exception, SystemExit) as exc:
        # Always leave a signal: a silent failure with no receipt would be zero
        # governance AND zero observability (e.g. authorizer down, missing deps).
        write_receipt({"phase": "pre", "decision": decision, "shadow": audit_only,
                       "claude_tool": tool, "agent_type": agent_type,
                       "error": str(exc), "reason": f"hook error: {exc}"})
        reason_text = f"Tenuo hook error (fail-closed): {exc}"
    if audit_only:
        # Observe-only is NEUTRAL: print no permissionDecision at all, so Claude's
        # own permission system stays fully in effect. Emitting "allow" here would
        # auto-approve every call and BYPASS the user's permission prompts — i.e.
        # observe-only would be MORE permissive than having no hook. The receipt
        # above already records what enforcement would have decided (shadow:true).
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision,
        "permissionDecisionReason": reason_text}}))


def cmd_post(_args) -> None:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        event = {}
    resp = event.get("tool_response", event.get("tool_result", ""))
    write_receipt({"phase": "post", "claude_tool": event.get("tool_name", ""),
                   "agent_type": event.get("agent_type"),
                   "outcome_preview": json.dumps(resp)[:240] if resp is not None else ""})


def cmd_mcp_proxy(_args) -> None:
    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent

    cfg = load_config()
    mcp_cfg = cfg.get("mcp", {})
    downstream = str((DEMO_DIR / mcp_cfg.get("downstream", "")).resolve())
    enforced = set((mcp_cfg.get("enforce") or {}).keys())
    catchall = catchall_cap(cfg)
    audit_only = is_audit_mode(cfg)

    def log(m):
        print(f"[tenuo-mcp-proxy] {m}", file=sys.stderr, flush=True)

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[downstream])
        async with stdio_client(params) as (dr, dw):
            async with ClientSession(dr, dw) as down:
                await down.initialize()
                log(f"connected to {downstream}")
                proxy = Server("tenuo-mcp-proxy")

                @proxy.list_tools()
                async def _lt():
                    return (await down.list_tools()).tools

                @proxy.call_tool()
                async def _ct(name: str, arguments: dict):
                    # enforced keys are bare downstream tool names; path-only (see resolve_tool).
                    fwd = dict(arguments)
                    if name in enforced:
                        val = arguments.get("path")
                        if isinstance(val, str) and val:
                            # abspath + realpath: relative paths resolve and
                            # symlinks can't smuggle reads outside the sandbox.
                            val = os.path.realpath(os.path.abspath(val))
                            fwd["path"] = val
                        allowed, reason = await asyncio.to_thread(
                            authorize, name, f"/verify/{name}", {"path": val}, {"path": val})
                    else:
                        allowed, reason = await asyncio.to_thread(
                            authorize, catchall, "/gate", {}, {"tool": name, **arguments})
                    write_receipt({"phase": "pre", "source": "mcp_proxy",
                                   "decision": "allow" if allowed else "deny",
                                   "shadow": audit_only, "claude_tool": name,
                                   "args": fwd, "reason": reason})
                    if not allowed and not audit_only:
                        log(f"DENY {name}: {reason}")
                        return [TextContent(type="text", text=f"Tenuo denied {name}: {reason}")]
                    if not allowed:
                        log(f"WOULD-DENY {name} (observe-only, forwarding): {reason}")
                    return (await down.call_tool(name, fwd)).content

                async with stdio_server() as (r, w):
                    await proxy.run(r, w, proxy.create_initialization_options())

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Generation: warrant, gateway, Claude wiring
# ---------------------------------------------------------------------------


def enforced_capabilities(cfg: dict) -> dict:
    """Capability -> {field: constraint} for everything `enforce`d.

    Unifies native tools, MCP tools, and — when `subagents:` is declared — the
    synthetic `spawn_agent` capability (subagent_type constrained to a oneof of
    the declared roles). De-duplicated: a capability shared by a native and an
    MCP tool (e.g. read_file) yields one entry.
    """
    from tenuo import OneOf

    sandbox = cfg["_sandbox_abs"]
    gate_web = bool(webfetch_approval(cfg) and trigger_id(cfg))
    caps: dict = {}
    for g in governed_map(cfg).values():
        if g["cap"] in caps:
            continue
        if "web" in g:
            caps[g["cap"]] = make_web_constraints(g["web"], approval_gate=gate_web)
        else:
            caps[g["cap"]] = {g["arg"]: make_constraint(g["spec"], sandbox)}
    for mtool, spec in (cfg.get("mcp", {}).get("enforce") or {}).items():
        caps.setdefault(mtool, {"path": make_constraint(spec, sandbox)})
    roles = subagent_roles(cfg)
    if roles:
        # Spawning is a first-class signed capability; per-role child warrants
        # drop it (with_tools), so a subagent can't spawn further subagents.
        caps[SPAWN_CAP] = {"subagent_type": OneOf(list(roles.keys()))}
    return caps


def write_gateway(cfg: dict, enforced_caps: dict) -> None:
    """Write the authorizer's gateway config (routes + tool decls) from policy.

    A pure generated artifact (no keys), so it's safe to refresh on every `up` —
    that keeps routes aligned with tenuo.yaml even for Cloud-issued warrants.
    """
    import yaml

    audit = audit_map(cfg)
    tools, routes = {}, []
    for cap, cons in enforced_caps.items():
        tools[cap] = {"description": cap, "constraints": {}}
        routes.append({"pattern": f"/verify/{cap}", "method": ["POST"], "tool": cap,
                       "constraints": {f: {"from": "body", "path": f, "required": True}
                                       for f in cons}})
    for cap in audit.values():
        if cap in tools:
            continue
        tools[cap] = {"description": cap, "constraints": {}}
        routes.append({"pattern": f"/verify/{cap}", "method": ["POST"], "tool": cap,
                       "constraints": {}})
    # Catch-all route /gate -> the granted "audit" or the ungranted "unlisted"
    # capability (declared as a tool either way so the route compiles).
    catchall = catchall_cap(cfg)
    tools.setdefault(catchall, {"description": catchall, "constraints": {}})
    routes.append({"pattern": "/gate", "method": ["POST"], "tool": catchall, "constraints": {}})
    gw = {"version": "1",
          "settings": {"debug_mode": True, "warrant_header": WARRANT_HEADER,
                       "pop_header": POP_HEADER, "clock_tolerance_secs": 30},
          "tools": tools, "routes": routes}
    GATEWAY.write_text(yaml.safe_dump(gw, sort_keys=False))
    sync_authorizer_mount()


def mint_local_warrant(cfg: dict, issuer, holder):
    """Mint a session warrant from tenuo.yaml with the given keys."""
    from tenuo import Warrant

    builder = Warrant.mint_builder()
    enforced = enforced_capabilities(cfg)
    for cap, cons in enforced.items():
        builder = builder.capability(cap, cons)
    # Mirror write_gateway: audit caps must not overwrite enforced constraints
    # (mint builder is last-wins on duplicate tool names).
    for cap in audit_map(cfg).values():
        if cap not in enforced:
            builder = builder.capability(cap, {})
    # Catch-all: grant "audit" only when default: audit. Under default: deny the
    # catch-all capability is intentionally absent so unlisted tools are denied.
    if default_mode(cfg) == "audit":
        builder = builder.capability(CATCHALL_AUDIT, {})
    return builder.holder(holder.public_key).ttl(3600).mint(issuer)


def remint_session(cfg: dict) -> str:
    """Re-mint the local session warrant REUSING the existing issuer + holder.

    Because the issuer key is unchanged, the running authorizer's trust anchor
    still applies — no container restart needed (the warrant itself rides in
    every request header). Used to self-heal an expired warrant on `up`.
    """
    from tenuo import SigningKey

    issuer = SigningKey.from_bytes(base64.b64decode(ISSUER_KEY.read_text()))
    holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
    warrant = mint_local_warrant(cfg, issuer, holder)
    write_secret(WARRANT, warrant.to_base64())
    update_state_warrant_id(warrant.id)
    return warrant.id


def update_state_warrant_id(wid: str) -> None:
    """Keep state.json's warrant_id in sync after a re-mint / trigger re-fire."""
    try:
        st = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {}
        st["warrant_id"] = wid
        STATE_JSON.write_text(json.dumps(st, indent=2))
    except Exception:
        pass


def refresh_policy(cfg: dict | None = None) -> str:
    """Re-read tenuo.yaml into runtime artifacts (wiring, gateway, warrant, subwarrants).

    Local mode re-mints the session warrant from policy. Cloud trigger mode re-fires
    the trigger (capabilities still come from the trigger config — run `tenuo-admin
    setup` first when enforce/audit/subagent policy changed on Cloud).
    """
    cfg = cfg or load_config()
    creds = cloud_creds(cfg)
    use_trigger = bool(creds["url"] and creds["api_key"] and trigger_id(cfg))

    write_claude_wiring(cfg)
    write_gateway(cfg, enforced_capabilities(cfg))

    if use_trigger:
        warrant_b64, _root = fire_session_warrant(cfg, creds)
        _record_fired_warrant(warrant_b64)
        from tenuo import Warrant
        wid = Warrant.from_base64(warrant_b64).id
    elif ISSUER_KEY.exists() and HOLDER_KEY.exists():
        if not WARRANT.exists():
            raise SystemExit("Run `tenuo-claude init` first.")
        wid = remint_session(cfg)
    else:
        raise SystemExit("Run `tenuo-claude init` first.")

    refresh_subwarrants(cfg)
    harden_state_permissions()
    return wid


def write_claude_wiring(cfg: dict) -> None:
    """Generate .claude/settings.json (hooks) and .mcp.json (MCP proxy).

    Uses ``bin/tenuo-claude`` (or ``TENUO_CLAUDE_BIN``) so wiring stays portable —
    no machine-specific Python paths. Re-run ``init`` / ``refresh`` after moving
    the repo or changing the install path.
    """
    claude_dir = DEMO_DIR / ".claude"
    claude_dir.mkdir(exist_ok=True)
    hook_timeout = APPROVAL_POLL_SECONDS + 30 if webfetch_approval(cfg) else 30
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": wiring_command_string("_hook"),
             "timeout": hook_timeout}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": wiring_command_string("_post")}]}],
    }}, indent=2))
    mcp = mcp_wiring(cfg)
    mcp_path = DEMO_DIR / ".mcp.json"
    if mcp:
        mcp_path.write_text(json.dumps(mcp, indent=2) + "\n")
    elif mcp_path.exists():
        mcp_path.unlink()


def ensure_state_dir() -> None:
    """Create .state as an owner-only (0700) directory, tightening it even if it
    already exists. It holds the holder/issuer signing keys and cloud
    credentials, so it must not be group/world-readable."""
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        STATE.chmod(0o700)
    except OSError:
        pass


def write_secret(path, text: str) -> None:
    """Write a secret file (private key, cloud credential) as owner-only 0600.

    Mirrors write_admin_env's handling. The holder key signs every PoP — leaving
    it world-readable (the default 0644) lets any local user mint authorizations
    against the warrant, so secrets are never written at the default mode."""
    path.write_text(text)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _secret_paths() -> list[Path]:
    """Paths under .state that must be owner-only (0600)."""
    paths: list[Path] = []
    for name in ("holder_key.b64", "issuer_key.b64", "cloud.env", "warrant.b64"):
        p = STATE / name
        if p.exists():
            paths.append(p)
    paths.extend(STATE.glob("subwarrant_*.b64"))
    paths.extend(STATE.glob("cloud*.env"))
    return paths


def harden_state_permissions() -> None:
    """Tighten .state to 0700 and secret files to 0600 (idempotent)."""
    ensure_state_dir()
    for path in _secret_paths():
        try:
            path.chmod(0o600)
        except OSError:
            pass


def check_state_permissions() -> tuple[bool, list[str]]:
    """Return (ok, issues) for .state and secret file modes."""
    issues: list[str] = []
    if not STATE.exists():
        return True, issues
    mode = STATE.stat().st_mode & 0o777
    if mode != 0o700:
        issues.append(f".state is {oct(mode)} (want 0o700)")
    for path in _secret_paths():
        fmode = path.stat().st_mode & 0o777
        if fmode != 0o600:
            issues.append(f"{path.name} is {oct(fmode)} (want 0o600)")
    return not issues, issues


def authorizer_mount_dir() -> Path:
    """Host directory mounted into the authorizer container (gateway + SRL only)."""
    return STATE / "authorizer"


def sync_authorizer_mount() -> None:
    """Stage gateway (+ optional SRL) for Docker. Private keys stay on the host."""
    import shutil

    ensure_state_dir()
    mount = authorizer_mount_dir()
    mount.mkdir(exist_ok=True)
    # World-traversable, not secret: the authorizer container runs as a non-root
    # user and must read gateway.yaml via the bind mount (CI runners included).
    try:
        mount.chmod(0o755)
    except OSError:
        pass
    if not GATEWAY.exists():
        return
    dest = mount / GATEWAY.name
    shutil.copy2(GATEWAY, dest)
    try:
        dest.chmod(0o644)
    except OSError:
        pass
    srl_dest = mount / SRL.name
    if SRL.exists():
        shutil.copy2(SRL, srl_dest)
        try:
            srl_dest.chmod(0o644)
        except OSError:
            pass
    elif srl_dest.exists():
        srl_dest.unlink()

def generate(cfg: dict) -> dict:
    from tenuo import SigningKey

    ensure_state_dir()
    sandbox = cfg["_sandbox_abs"]
    Path(sandbox).mkdir(parents=True, exist_ok=True)

    issuer = SigningKey.generate()
    # REUSE an existing holder key: `tenuo-admin setup` claims this key with the
    # Cloud agent, and a Cloud-issued warrant binds to it (PoP). Regenerating it
    # on re-init would silently break every cloud-issued warrant until the agent
    # key is rotated and re-claimed.
    if HOLDER_KEY.exists():
        holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
    else:
        holder = SigningKey.generate()
        write_secret(HOLDER_KEY, base64.b64encode(bytes(holder.secret_key_bytes())).decode())
    warrant = mint_local_warrant(cfg, issuer, holder)

    write_secret(ISSUER_KEY, base64.b64encode(bytes(issuer.secret_key_bytes())).decode())
    ISSUER_PUB.write_text(issuer.public_key.to_bytes().hex())
    write_secret(WARRANT, warrant.to_base64())

    write_gateway(cfg, enforced_capabilities(cfg))

    STATE_JSON.write_text(json.dumps({
        "name": cfg.get("name", "tenuo-claude"), "warrant_id": warrant.id,
        "issuer_pub_hex": issuer.public_key.to_bytes().hex(), "sandbox": sandbox,
        "authorizer_url": AUTHZ_URL}, indent=2))

    write_claude_wiring(cfg)
    harden_state_permissions()
    return {"warrant_id": warrant.id, "issuer_pub": issuer.public_key.to_bytes().hex(),
            "sandbox": sandbox}


# ---------------------------------------------------------------------------
# Authorizer lifecycle
# ---------------------------------------------------------------------------


def fetch_tenant_root(url: str, api_key: str):
    try:
        req = urllib.request.Request(url + "/v1/revocations/srl/signed",
                                     headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=8, context=_https_context()) as resp:
            issuer_b64 = json.loads(resp.read().decode())["payload"]["Issuer"]
            return base64.b64decode(issuer_b64).hex()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tenuo Cloud: triggers (Cloud-issued, root-signed warrants)
# ---------------------------------------------------------------------------


def _https_context():
    """TLS context for Cloud calls, preferring certifi's CA bundle when present.

    macOS python.org framework builds ship without a CA store wired up, which
    makes every https call die with CERTIFICATE_VERIFY_FAILED. certifi rides in
    with the `tenuo` install, so use it when available; otherwise fall back to
    the system default context.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def cloud_api(method: str, cloud_url: str, key: str, path: str, body=None):
    """Call the Cloud control plane. Returns (status, parsed_json|text).

    Network/TLS failures return (0, <readable message>) instead of raising, so
    callers fail with one clear line rather than an urllib traceback.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        cloud_url.rstrip("/") + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_https_context()) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or ""
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(reason):
            hint = (" — this Python has no usable CA store; `pip install certifi` "
                    "(bundled with `tenuo`) or set SSL_CERT_FILE to a CA bundle")
        return 0, f"network error reaching {cloud_url}: {reason}{hint}"


ADMIN_KEY_VARS = ("TENUO_ADMIN_KEY", "TENUO_ADMIN_API_KEY")


def assert_no_admin_key() -> None:
    """Separation of duties: the runtime/agent plane must NEVER carry a tenant-admin
    API key. Admin actions (create agent/trigger) live in `tenuo-admin` with
    their own out-of-tree key (~/.tenuo/admin.env).

    Fail closed if an admin key is reachable from the runtime environment or
    leaks into .state/cloud.env — that would let a compromised session escalate.
    """
    reach = runtime_env()
    for var in ADMIN_KEY_VARS:
        if reach.get(var):
            raise SystemExit(
                f"Refusing to run: {var} is present in the runtime environment.\n"
                "Admin credentials must not reach the agent/runtime plane.\n"
                "Move it to ~/.tenuo/admin.env and run admin actions via `tenuo-admin`.")


def cloud_creds(cfg: dict) -> dict:
    """Resolve Cloud URL + the runtime authorizer key from cfg + .state/cloud.env.

    Accepts either ``TENUO_CONNECT_TOKEN`` (Quick Connect) or explicit
    ``TENUO_CONTROL_PLANE_URL`` + ``TENUO_API_KEY``. Explicit vars win when set.

    Note: no admin key here by design — runtime fires triggers and consumes
    warrants with the Quick Connect / authorizer service-account key only.
    See `tenuo-admin` / tenuo_claude_code.admin.
    """
    env = runtime_env()
    url = (cfg.get("cloud") or {}).get("url") or env.get("TENUO_CONTROL_PLANE_URL")
    api_key = env.get("TENUO_API_KEY")
    connect = env.get("TENUO_CONNECT_TOKEN")
    if connect:
        parsed = _parse_connect_token(connect)
        url = url or parsed["url"]
        api_key = api_key or parsed["api_key"]
    return {
        "url": url,
        "api_key": api_key,
        "root": env.get("TENUO_TENANT_ROOT"),
    }


# ---------------------------------------------------------------------------
# Onboarding helpers (check / onboard / bootstrap / cloud profile)
# ---------------------------------------------------------------------------


def cloud_mode_files() -> dict[str, bool]:
    return {
        "cloud_env": CLOUD_ENV.exists(),
        "cloud_state": CLOUD_STATE.exists(),
        "cloud_profile": CLOUD_PROFILE.exists(),
    }


def intended_mode(cfg: dict | None = None) -> str:
    """Return ``local`` or ``cloud`` from on-disk artifacts."""
    files = cloud_mode_files()
    if files["cloud_env"] or files["cloud_profile"]:
        return "cloud"
    if cfg and cfg.get("cloud"):
        return "cloud"
    return "local"


def probe_runtime_creds(creds: dict) -> tuple[bool, str]:
    url, key = creds.get("url"), creds.get("api_key")
    if not url or not key:
        return False, "missing control-plane URL or runtime key"
    status, body = cloud_api("GET", url, key, "/v1/revocations/srl/signed")
    if status == 200:
        return True, "runtime key accepted by Cloud"
    if status == 401:
        return False, "invalid_api_key (use Quick Connect token, not ak_… id)"
    if status == 403:
        return False, "forbidden — wrong key role for this endpoint"
    return False, f"HTTP {status}: {body}"


def write_cloud_env(connect_token: str, authorizer_name: str | None = None) -> None:
    ensure_state_dir()
    cfg = load_config() if CONFIG_FILE.exists() else {}
    name = authorizer_name or cfg.get("name", "tenuo-claude")
    # Validate before writing.
    _parse_connect_token(connect_token.strip())
    # 0600: holds the runtime connect token (authorizer bearer key).
    write_secret(
        CLOUD_ENV,
        "# Written by tenuo-claude onboard — edit as needed.\n"
        f'export TENUO_CONNECT_TOKEN="{connect_token.strip()}"\n'
        f'export TENUO_AUTHORIZER_NAME="{name}"\n'
    )


def write_admin_env(admin_key: str) -> None:
    ADMIN_ENV.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_ENV.write_text(f'export TENUO_ADMIN_KEY="{admin_key.strip()}"\n')
    try:
        ADMIN_ENV.chmod(0o600)
    except OSError:
        pass


def write_cloud_profile(*, url: str) -> None:
    import yaml

    data: dict = {"cloud": {"url": url.rstrip("/")}}
    CLOUD_PROFILE.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def write_advanced_profile(*, approver: str, threshold: int = 1) -> None:
    import yaml

    data: dict = {
        "cloud": {"approver_identity": approver},
        "enforce": {"WebFetch": {"approval": {"threshold": threshold}}},
    }
    ADVANCED_PROFILE.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def disable_cloud_artifacts() -> list[str]:
    """Move Cloud/advanced files aside for local mode. Returns paths moved."""
    moved = []
    for path in (CLOUD_ENV, CLOUD_STATE, CLOUD_PROFILE, ADVANCED_PROFILE):
        if path.exists():
            dest = path.with_suffix(path.suffix + ".bak")
            n = 0
            while dest.exists():
                n += 1
                dest = path.with_name(f"{path.name}.bak{n}")
            path.rename(dest)
            moved.append(f"{path.name} -> {dest.name}")
    return moved


def _docker_ok() -> tuple[bool, str]:
    return art.docker_ok()


def _native_logs(state: Path) -> None:
    log = art.native_log_path(state)
    if log.is_file():
        print(log.read_text()[-1500:])


def _start_authorizer_docker(cfg: dict, denv: dict, *, cloud: bool) -> None:
    image, name = authorizer_image(cfg), container_name(cfg)
    docker("rm", "-f", name)
    sync_authorizer_mount()
    mount = authorizer_mount_dir()
    art.assert_port_available(PORT, AUTHZ_URL, mount)
    if SRL.exists() and not cloud:
        denv["TENUO_REVOCATION_LIST"] = f"/state/{SRL.name}"
    run = ["run", "-d", "--name", name, "-p", f"127.0.0.1:{PORT}:9090",
           "-v", f"{mount.resolve()}:/state:ro"]
    for k, v in denv.items():
        run += ["-e", f"{k}={v}"]
    serve = ["serve", "--config", f"/state/{GATEWAY.name}", "--port", "9090", "--bind", "0.0.0.0"]
    print(f"Starting authorizer container {name} ({image}; pulling if needed)…")
    started = docker(*run, image, *serve)
    if started.returncode != 0:
        raise SystemExit(f"Failed to start authorizer container ({image}):\n{started.stderr.strip()}")
    art.write_runtime_meta(mount, backend="docker", image=image)

    def _on_exit() -> None:
        logs = docker("logs", name)
        raise SystemExit("Authorizer container exited during startup:\n"
                         + (logs.stdout or logs.stderr)[-1500:])

    art.wait_healthy(
        AUTHZ_URL,
        is_running=lambda: authorizer_running(cfg),
        on_exited=_on_exit,
    )
    print(f"Authorizer up (container {name}).")


def _start_authorizer_native(cfg: dict, denv: dict, *, image: str, install: bool = False) -> None:
    sync_authorizer_mount()
    mount = authorizer_mount_dir()
    binary = art.resolve_authorizer_binary(image, install=install)
    print(f"Starting native authorizer ({binary})…")
    art.start_native(
        binary=binary,
        mount=mount,
        gateway_name=GATEWAY.name,
        port=PORT,
        authz_url=AUTHZ_URL,
        denv=denv,
        srl_name=SRL.name if SRL.exists() else None,
        state=STATE,
        image=image,
    )

    def _on_exit() -> None:
        _native_logs(STATE)
        raise SystemExit("Native authorizer exited during startup (see .state/authorizer.log)")

    art.wait_healthy(
        AUTHZ_URL,
        is_running=lambda: art.native_process_alive(mount),
        on_exited=_on_exit,
    )
    print(f"Authorizer up (native, {AUTHZ_URL}).")


def _check_line(ok: bool | None, label: str, detail: str = "", hint: str = "") -> bool:
    """Print one preflight line. ``None`` = warn. Returns False if hard fail."""
    mark = "ok" if ok is True else ("!!" if ok is False else "..")
    msg = f"  {mark} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if hint:
        print(f"      → {hint}")
    return ok is not False


def _check_wiring(cfg: dict | None, ok: bool) -> bool:
    """Validate launcher + generated Claude/MCP wiring."""
    if LAUNCHER.is_file():
        exe = os.access(LAUNCHER, os.X_OK)
        ok = _check_line(exe, "launcher", LAUNCHER_REL,
                         "" if exe else f"run: chmod +x {LAUNCHER_REL}") and ok
        if exe:
            try:
                r = subprocess.run(
                    [str(LAUNCHER), "status"], cwd=DEMO_DIR, capture_output=True,
                    text=True, timeout=45)
                smoke_ok = r.returncode == 0
                ok = _check_line(
                    smoke_ok, "launcher smoke", "status ok" if smoke_ok else "status failed",
                    "" if smoke_ok else "run: uv sync && tenuo-claude refresh") and ok
            except subprocess.TimeoutExpired:
                ok = _check_line(False, "launcher smoke", "timed out",
                                 "check authorizer / docker") and ok
    else:
        _check_line(None, "launcher", "not present", "optional — PyPI installs use `tenuo-claude` on PATH")

    if not cfg:
        return ok

    hooks = DEMO_DIR / ".claude" / "settings.json"
    if hooks.exists():
        try:
            settings = json.loads(hooks.read_text())
            pre = (settings.get("hooks") or {}).get("PreToolUse") or []
            cmd = ""
            if pre and pre[0].get("hooks"):
                cmd = pre[0]["hooks"][0].get("command", "")
            expect = wiring_command_string("_hook")
            drift = cmd != expect
            ok = _check_line(
                not drift, "hook wiring",
                "current" if not drift else f"stale (want {expect!r})",
                "" if not drift else "tenuo-claude refresh") and ok
        except (json.JSONDecodeError, IndexError, KeyError):
            ok = _check_line(False, "hook wiring", "unreadable settings.json",
                             "tenuo-claude refresh") and ok
    else:
        _check_line(None, "claude hooks", "not wired yet", "run: tenuo-claude init")

    expected_mcp = mcp_wiring(cfg)
    mcp_path = DEMO_DIR / ".mcp.json"
    if expected_mcp:
        if mcp_path.exists():
            try:
                actual = json.loads(mcp_path.read_text())
                drift = actual != expected_mcp
                ok = _check_line(
                    not drift, "mcp wiring",
                    "current" if not drift else "stale",
                    "" if not drift else "tenuo-claude refresh") and ok
            except json.JSONDecodeError:
                ok = _check_line(False, "mcp wiring", "invalid JSON",
                                 "tenuo-claude refresh") and ok
        else:
            _check_line(None, "mcp wiring", "missing", "tenuo-claude init")
    elif mcp_path.exists():
        _check_line(None, "mcp wiring", "present but mcp.downstream unset",
                    "remove .mcp.json or restore mcp: in tenuo.yaml")
    return ok


def cmd_check(_args) -> None:
    """Preflight before init/up: deps, credentials, mode, suggested next steps."""
    ok = True
    print("Preflight check\n")

    py_ok = sys.version_info >= (3, 10)
    ok = _check_line(py_ok, "python", f"{sys.version_info.major}.{sys.version_info.minor}") and ok

    try:
        import tenuo  # noqa: F401
        import yaml  # noqa: F401
        ok = _check_line(True, "python deps", "tenuo, PyYAML installed") and ok
    except ImportError as exc:
        ok = _check_line(False, "python deps", str(exc),
                         "run: uv sync  (or pip install -r requirements.txt)") and ok

    d_ok, d_msg = _docker_ok()
    if d_ok:
        _check_line(True, "docker", d_msg)
    else:
        _check_line(None, "docker", d_msg, "optional — `tenuo-claude up --native` uses a host binary")
    native_bin = art.find_authorizer_binary(DEFAULT_AUTHZ_IMAGE)
    pinned_ver = art.authorizer_crate_version(DEFAULT_AUTHZ_IMAGE)
    if native_bin:
        _check_line(True, "authorizer binary", str(native_bin))
        installed = art.query_binary_version(native_bin)
        if installed and art.version_compatible(installed, pinned_ver):
            _check_line(True, "authorizer version", art.crate_version_from_authorizer_version(installed))
        elif installed:
            _check_line(
                None, "authorizer version",
                f"{art.crate_version_from_authorizer_version(installed)} (want {pinned_ver})",
                art.install_hint(DEFAULT_AUTHZ_IMAGE),
            )
    elif not d_ok:
        _check_line(
            False, "authorizer binary", "not installed",
            art.install_hint(DEFAULT_AUTHZ_IMAGE),
        )
        ok = False

    claude = shutil.which("claude")
    _check_line(bool(claude), "claude CLI", claude or "not on PATH",
                "install Claude Code; optional for check / verify --deep")

    ok = _check_line(CONFIG_FILE.exists(), "tenuo.yaml", str(CONFIG_FILE)) and ok

    cfg = None
    if CONFIG_FILE.exists():
        try:
            cfg = load_config()
        except SystemExit as exc:
            ok = _check_line(False, "policy load", str(exc)) and ok

    ok = _check_wiring(cfg, ok)

    if STATE.exists():
        perm_ok, perm_issues = check_state_permissions()
        ok = _check_line(
            perm_ok, "secret permissions",
            "ok" if perm_ok else "; ".join(perm_issues),
            "" if perm_ok else "run: tenuo-claude refresh") and ok

    mode = intended_mode(cfg)
    files = cloud_mode_files()
    _check_line(True, "mode", mode, None)
    if mode == "local" and any(files.values()):
        _check_line(None, "cloud files", "present but mode is local",
                    "remove/rename .state/cloud.env or run: tenuo-claude init --local")

    reach = runtime_env()
    admin_leak = next((v for v in ADMIN_KEY_VARS if reach.get(v)), None)
    if admin_leak:
        ok = _check_line(False, "runtime env", f"{admin_leak} is exported",
                         "unset it; admin key belongs in ~/.tenuo/admin.env only") and ok
    else:
        _check_line(True, "runtime env", "no admin key leaked")

    if mode == "cloud" or files["cloud_env"]:
        if not files["cloud_env"]:
            ok = _check_line(False, "cloud.env", "missing",
                             "run: tenuo-claude onboard --cloud") and ok
        else:
            creds = cloud_creds(cfg or {})
            parsed = bool(creds.get("url") and creds.get("api_key"))
            ok = _check_line(parsed, "connect credentials", "parsed from cloud.env") and ok
            if parsed:
                p_ok, p_msg = probe_runtime_creds(creds)
                ok = _check_line(p_ok, "cloud probe", p_msg) and ok
        if ADMIN_ENV.exists():
            _check_line(True, "admin.env", str(ADMIN_ENV))
        else:
            _check_line(None, "admin.env", "missing",
                        "needed once for tenuo-admin setup (Settings → API Keys)")
        if files["cloud_state"]:
            st = load_cloud_state()
            tid = st.get("trigger_id")
            _check_line(bool(tid), "cloud setup", f"trigger {tid}" if tid else "incomplete")
        else:
            _check_line(None, "cloud setup", "not run yet", "run: tenuo-admin setup")
        if cfg and webfetch_approval(cfg) and not load_cloud_state().get("web_fetch_approval_policy_id"):
            _check_line(None, "web approval", "policy not wired", "re-run: tenuo-admin setup")

    run_cfg = cfg or {"name": "tenuo-claude"}
    if WARRANT.exists():
        exp = warrant_expired()
        soon, exp_at = warrant_expires_within(24)
        detail = "present"
        if exp:
            detail += " (expired — run up)"
        elif soon:
            detail += f" (expires soon: {exp_at} — run up to refresh)"
        _check_line(None if exp or soon else True, "warrant", detail)
    elif mode == "local":
        _check_line(None, "warrant", "missing", "run: tenuo-claude init")

    if authorizer_running(run_cfg):
        mount = authorizer_mount_dir()
        meta = art.read_runtime_meta(mount)
        backend = meta.get("backend") or "unknown"
        st = _status_json()
        running_ver = (st or {}).get("version")
        ver_detail = f"up ({resolve_authz_url()}) | {backend}"
        if running_ver:
            ver_detail += f" | v{running_ver}"
            if running_ver != pinned_ver:
                _check_line(
                    None, "running authorizer",
                    ver_detail,
                    f"want v{pinned_ver} — restart after upgrade",
                )
            else:
                _check_line(True, "running authorizer", ver_detail)
        else:
            _check_line(True, "running authorizer", ver_detail)
    else:
        img = authorizer_image(run_cfg)
        img_ver = art.authorizer_crate_version(img)
        if img_ver != pinned_ver:
            _check_line(None, "authorizer image", img_ver, f"pinned package expects {pinned_ver}")
        _check_line(None, "authorizer", "down", "run: tenuo-claude up")

    print("\nSuggested next steps:")
    hooks_wired = (DEMO_DIR / ".claude" / "settings.json").exists()
    if not hooks_wired:
        print("  tenuo-claude init")
    elif mode == "cloud" and not files["cloud_state"]:
        print("  tenuo-admin setup && tenuo-claude up")
    elif not authorizer_running(run_cfg):
        print("  tenuo-claude up")
    elif WARRANT.exists() and not warrant_expired():
        print("  tenuo-claude verify")
        print("  open Claude Code in this directory")
    else:
        print("  tenuo-claude up   # refresh warrant (authorizer already running)")
    print("\nCHECK OK" if ok else "\nCHECK FAILED — fix items marked !!")
    raise SystemExit(0 if ok else 1)


def _prompt(text: str, default: str = "") -> str:
    try:
        suffix = f" [{default}]" if default else ""
        line = input(f"{text}{suffix}: ").strip()
    except EOFError:
        line = ""
    return line or default


def cmd_onboard(args) -> None:
    """Interactive wizard for local or Cloud onboarding."""
    cloud = getattr(args, "cloud", False)
    local = getattr(args, "local", False)
    if not cloud and not local:
        choice = _prompt("Mode — (l)ocal or (c)loud", "local").lower()
        cloud = choice.startswith("c")
        local = not cloud

    created = scaffold_example_policy(DEMO_DIR, no_scaffold=getattr(args, "no_scaffold", False))

    if not created:
        print("\nRunning preflight…")
        try:
            cmd_check(argparse.Namespace())
        except SystemExit as exc:
            if exc.code not in (0, None):
                if getattr(args, "yes", False):
                    print("Preflight failed — continuing (--yes).")
                elif not _prompt("Preflight failed — continue anyway?", "n").lower().startswith("y"):
                    raise SystemExit(1)

    if local or not cloud:
        moved = disable_cloud_artifacts()
        if moved:
            print("Moved aside for local mode:", ", ".join(moved))
        cmd_init(argparse.Namespace(cloud=False, local=True))
        cmd_up(argparse.Namespace())
        try:
            cmd_verify(argparse.Namespace(deep=False, no_live=True))
        except SystemExit as exc:
            raise SystemExit(1 if exc.code is None else exc.code)
        print("\nOnboard complete (local). Open Claude Code in this directory.")
        return

    # Cloud path
    token = getattr(args, "connect_token", None) or os.environ.get("TENUO_CONNECT_TOKEN")
    if not token and CLOUD_ENV.exists():
        token = runtime_env().get("TENUO_CONNECT_TOKEN")
    if not token and not getattr(args, "yes", False):
        token = _prompt("Paste Quick Connect token (tenuo_ct_…)")
    if not token:
        raise SystemExit("Cloud onboarding needs TENUO_CONNECT_TOKEN (Quick Connect → Authorizer Only).")
    write_cloud_env(token)

    url = _parse_connect_token(token.strip())["url"]
    write_cloud_profile(url=url)
    print(f"Wrote {CLOUD_PROFILE.name}")

    if getattr(args, "advanced", False) or getattr(args, "demo", False):
        approver = getattr(args, "approver", None)
        if not approver and not getattr(args, "yes", False):
            approver = _prompt("Approver display name (must exist in Cloud)")
        if not approver:
            raise SystemExit("--advanced requires an approver display name.")
        write_advanced_profile(approver=approver)
        print(f"Wrote {ADVANCED_PROFILE.name} (advanced — re-run `tenuo-admin setup`)")

    admin_key = getattr(args, "admin_key", None) or os.environ.get("TENUO_ADMIN_KEY")
    if not admin_key and ADMIN_ENV.exists():
        admin_key = read_env_file(ADMIN_ENV).get("TENUO_ADMIN_KEY")
    if not admin_key and not getattr(args, "yes", False):
        admin_key = _prompt("Paste tenant-admin key for one-time setup (blank = skip)", "")
    if admin_key:
        write_admin_env(admin_key)
        print(f"Wrote {ADMIN_ENV}")

    cmd_init(argparse.Namespace(cloud=False, local=False))

    if admin_key:
        env = os.environ.copy()
        for var in ADMIN_KEY_VARS:
            env.pop(var, None)
        r = subprocess.run(
            [sys.executable, "-m", "tenuo_claude_code.admin", "setup"],
            cwd=DEMO_DIR, env=env,
        )
        if r.returncode != 0:
            raise SystemExit("tenuo-admin setup failed — fix errors above and re-run setup")
    else:
        print("\nSkipped tenuo-admin setup (no admin key). Run setup with a tenant-admin key.")

    for var in ADMIN_KEY_VARS:
        os.environ.pop(var, None)
    cmd_up(argparse.Namespace())
    try:
        cmd_verify(argparse.Namespace(deep=False, no_live=True))
    except SystemExit as exc:
        raise SystemExit(1 if exc.code is None else exc.code)
    print("\nOnboard complete (cloud). Open Claude Code in this directory.")


def cmd_bootstrap(args) -> None:
    """check → init → up → verify (--local default)."""
    local = getattr(args, "local", True) and not getattr(args, "cloud", False)
    ns = dict(local=True, cloud=False, yes=True,
              no_scaffold=getattr(args, "no_scaffold", False))
    if local:
        cmd_onboard(argparse.Namespace(**ns))
    else:
        cmd_onboard(argparse.Namespace(
            local=False, cloud=True, yes=getattr(args, "yes", False),
            no_scaffold=getattr(args, "no_scaffold", False),
            advanced=getattr(args, "advanced", False) or getattr(args, "demo", False),
            connect_token=getattr(args, "connect_token", None),
            approver=getattr(args, "approver", None),
            admin_key=getattr(args, "admin_key", None),
        ))


def load_cloud_state() -> dict:
    if CLOUD_STATE.exists():
        try:
            return json.loads(CLOUD_STATE.read_text())
        except Exception:
            return {}
    return {}


def save_cloud_state(patch: dict) -> None:
    st = load_cloud_state()
    st.update(patch)
    CLOUD_STATE.write_text(json.dumps(st, indent=2))


def trigger_id(cfg: dict) -> str | None:
    """Trigger id to fire on `up`: tenuo.yaml cloud.trigger, else cloud-setup state."""
    return (cfg.get("cloud") or {}).get("trigger") or load_cloud_state().get("trigger_id")


def fire_session_warrant(cfg: dict, creds: dict) -> tuple[str, str]:
    """Fire the configured trigger -> (warrant_b64, tenant_root_hex). Runtime key."""
    tid = trigger_id(cfg)
    if not tid:
        raise SystemExit("No trigger configured. Run `tenuo-admin setup` first.")
    agent = load_cloud_state().get("agent_id", "")
    event = {"sandbox": cfg["_sandbox_abs"], "agent_id": agent}
    status, body = cloud_api("POST", creds["url"], creds["api_key"],
                             f"/v1/triggers/{tid}/fire", {"event_data": event})
    if status != 200 or not isinstance(body, dict) or not body.get("warrant"):
        raise SystemExit(f"Trigger fire failed ({status}): {body}")
    root = creds.get("root") or fetch_tenant_root(creds["url"], creds["api_key"])
    if not root:
        raise SystemExit("Fired warrant but could not resolve tenant root for trust anchor.")
    return body["warrant"], root


def authorizer_image(cfg: dict) -> str:
    return (os.environ.get("TENUO_AUTHORIZER_IMAGE")
            or (cfg.get("authorizer") or {}).get("image") or DEFAULT_AUTHZ_IMAGE)


def container_name(cfg: dict) -> str:
    return f"tenuo-authorizer-{slug(cfg.get('name', 'tenuo-claude'))}"


def docker(*args: str) -> subprocess.CompletedProcess:
    """Run a docker subcommand, captured. Fail closed with a clear message if
    Docker isn't installed (the authorizer ships only as a container image)."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("Docker not found. Install Docker Desktop / engine — the "
                         "Tenuo authorizer runs as a container (tenuo/authorizer).")


def authorizer_running(cfg: dict) -> bool:
    mount = authorizer_mount_dir()
    backend = art.read_runtime_backend(mount)
    if backend == "native" or art.native_pid_path(mount).is_file():
        return art.native_running(mount, resolve_authz_url())
    r = docker("inspect", "-f", "{{.State.Running}}", container_name(cfg))
    return r.returncode == 0 and r.stdout.strip() == "true"


def warrant_expired() -> bool:
    try:
        from tenuo import Warrant
        return Warrant.from_base64(WARRANT.read_text()).is_expired()
    except Exception:
        return False  # missing/unreadable warrant still fails closed at the authorizer


def warrant_expires_within(hours: float = 24) -> tuple[bool, str]:
    """True when the session warrant expires within ``hours`` (and is not already expired)."""
    try:
        from tenuo import Warrant
        w = Warrant.from_base64(WARRANT.read_text())
        if w.is_expired():
            return False, ""
        exp_raw = w.expires_at()
        exp = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        remaining = (exp - datetime.now(timezone.utc)).total_seconds()
        if 0 < remaining <= hours * 3600:
            return True, exp_raw
    except Exception:
        pass
    return False, ""


def _authorizer_status_line(cfg: dict) -> str:
    mount = authorizer_mount_dir()
    meta = art.read_runtime_meta(mount)
    backend = meta.get("backend")
    if backend == "native":
        binary = meta.get("binary")
        return f"native ({binary})" if binary else "native"
    if backend == "docker":
        image = meta.get("image") or authorizer_image(cfg)
        return f"docker ({image})"
    try:
        if docker("inspect", "-f", "{{.State.Running}}", container_name(cfg)).stdout.strip() == "true":
            return f"docker ({authorizer_image(cfg)})"
    except SystemExit:
        pass
    if art.native_pid_path(mount).is_file():
        return "native"
    return "unknown backend"


def _record_fired_warrant(warrant_b64: str) -> None:
    write_secret(WARRANT, warrant_b64)
    try:
        from tenuo import Warrant
        update_state_warrant_id(Warrant.from_base64(warrant_b64).id)
    except Exception:
        pass


def cmd_install_authorizer(args) -> None:
    """Install the pinned ``tenuo-authorizer`` binary to ``~/.tenuo/bin``."""
    image = DEFAULT_AUTHZ_IMAGE
    try:
        bind_project_paths(sys.modules[__name__])
        try:
            image = authorizer_image(load_config())
        except SystemExit:
            pass
    except SystemExit:
        pass
    path = art.install_authorizer(image, force=getattr(args, "force", False))
    installed = art.query_binary_version(path)
    print(f"Installed: {path}")
    if installed:
        print(f"Version:   {installed}")
    print("Next: `tenuo-claude up --native`")


def cmd_up(_args) -> None:
    cfg = load_config()
    creds = cloud_creds(cfg)
    use_trigger = bool(creds["url"] and creds["api_key"] and trigger_id(cfg))

    if not WARRANT.exists() and not use_trigger:
        raise SystemExit("Run `tenuo-claude init` first.")
    refreshed = False
    if warrant_expired() or (use_trigger and not WARRANT.exists()):
        # Re-apply tenuo.yaml (reuse issuer locally; re-fire trigger on Cloud).
        # Trust anchor unchanged — no container restart strictly required for the
        # warrant itself (it rides in every request header).
        print("Warrant expired — refreshing from tenuo.yaml…")
        if use_trigger or (ISSUER_KEY.exists() and HOLDER_KEY.exists() and WARRANT.exists()):
            refresh_policy(cfg)
        else:
            generate(cfg)
        refreshed = True
    if authorizer_running(cfg):
        if refreshed:
            refresh_subwarrants(cfg)
            print("Session warrant refreshed (running authorizer untouched).")
        else:
            print("Authorizer already running.")
        return cmd_status(_args)

    # Only authorizer-scoped values are passed to the container (explicit -e),
    # never the host environment or any admin key (separation of duties).
    cloud_url, api_key = creds["url"], creds["api_key"]
    cloud = use_trigger or bool(cloud_url and api_key)
    denv: dict = {}
    if api_key:
        denv["TENUO_API_KEY"] = api_key
    if cloud:
        denv["TENUO_CONTROL_PLANE_URL"] = cloud_url
        denv["TENUO_AUTHORIZER_NAME"] = cfg.get("name", "tenuo-claude")

    if use_trigger:
        # Cloud-issued session warrant: fire the trigger, trust the tenant ROOT
        # ONLY (no local issuer — all authority chains to the cloud root).
        warrant_b64, root = fire_session_warrant(cfg, creds)
        _record_fired_warrant(warrant_b64)
        denv["TENUO_TRUSTED_KEYS"] = root
        print(f"Cloud mode (trigger {trigger_id(cfg)}): root-signed session warrant, "
              f"trust anchor {root[:16]}…")
    elif cloud:
        # Locally-minted warrant: trust the tenant root AND the local issuer.
        root = creds["root"] or fetch_tenant_root(cloud_url, api_key)
        if not root:
            raise SystemExit("Cloud configured but could not resolve tenant root key.")
        denv["TENUO_TRUSTED_KEYS"] = f"{root},{ISSUER_PUB.read_text().strip()}"
        print(f"Cloud mode: {cloud_url} (tenant root {root[:16]}…)")
    else:
        denv["TENUO_TRUSTED_KEYS"] = ISSUER_PUB.read_text().strip()
        print("Local mode (no Cloud).")

    # Refresh the gateway from tenuo.yaml (routes only, no keys) so it's aligned
    # even for a Cloud-issued warrant — in particular the spawn_agent route.
    write_gateway(cfg, enforced_capabilities(cfg))
    # Derive per-subagent child warrants from the now-final session warrant.
    refresh_subwarrants(cfg)
    roles = subagent_roles(cfg)
    if roles:
        print(f"Subagent warrants: {', '.join(roles)} (attenuated from the session).")

    # Launch the authorizer (Docker container or native binary).
    image = authorizer_image(cfg)
    backend = art.choose_backend(_args)
    if backend == "native":
        _start_authorizer_native(cfg, denv, image=image, install=getattr(_args, "install", False))
    else:
        _start_authorizer_docker(cfg, denv, cloud=cloud)
    cmd_status(_args)


def cmd_down(_args) -> None:
    cfg = load_config()
    mount = authorizer_mount_dir()
    running = authorizer_running(cfg)
    stopped = False
    if art.read_runtime_backend(mount) == "native" or art.native_pid_path(mount).is_file():
        stopped = art.stop_native(mount)
    name = container_name(cfg)
    if docker("inspect", "-f", "{{.State.Running}}", name).returncode == 0:
        docker("rm", "-f", name)
        art.clear_runtime_meta(mount)
        stopped = True
    if stopped:
        print("Stopped authorizer.")
    else:
        print("Authorizer not running.")


def _status_json():
    try:
        with urllib.request.urlopen(resolve_authz_url() + "/status", timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def cmd_status(_args) -> None:
    cfg = load_config()
    st = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {}
    print(f"policy      : {CONFIG_FILE.name}  (sandbox {cfg['_sandbox_abs']})")
    print(f"warrant     : {st.get('warrant_id', '<none — run init>')}")
    if WARRANT.exists():
        try:
            from tenuo import Warrant
            w = Warrant.from_base64(WARRANT.read_text())
            flag = ("  !! EXPIRED — run `tenuo-claude up` to refresh"
                    if w.is_expired() else "")
            soon, _ = warrant_expires_within(24)
            if soon and not flag:
                flag = "  !! expires within 24h — run `tenuo-claude up` to refresh"
            print(f"  expires   : {w.expires_at()}{flag}")
        except Exception:
            print("  expires   : <unreadable warrant>")
    gov = ", ".join(cfg["enforce"].keys())
    aud = ", ".join(cfg.get("audit", []) or [])
    if is_audit_mode(cfg):
        print("mode        : AUDIT — observe-only (decisions logged, NOT enforced)")
    print(f"enforced    : {gov or '<none>'}")
    print(f"audit-allow : {aud or '<none>'}   | default: {default_mode(cfg)}")
    if webfetch_approval(cfg):
        cs = load_cloud_state()
        who = cs.get("web_fetch_approver") or (cfg.get("cloud") or {}).get("approver_identity") or "?"
        pid = cs.get("web_fetch_approval_policy_id")
        wired = f"policy {pid}" if pid else "NOT set up (run `tenuo-admin setup`)"
        print(f"approval: gated tool calls -> approver sign-off ({who}) | {wired}")
    roles = subagent_roles(cfg)
    if roles:
        defs = agent_definitions()
        parts = []
        for r in roles:
            resolved, _ = resolve_subagent_role(r, defs)
            minted = "✓" if subwarrant_path(r).exists() else "no warrant (run `up`)"
            parts.append(f"{r} ({minted})" if resolved
                         else f"{r} (!! no agent definition)")
        print(f"subagents   : {', '.join(parts)}")
    s = _status_json()
    if s:
        cp = s.get("cp", {})
        runtime = _authorizer_status_line(cfg)
        ver = s.get("version")
        ver_bit = f" v{ver}" if ver else ""
        print(f"authorizer  : up ({resolve_authz_url()}) | {runtime}{ver_bit} | "
              f"cloud: {cp.get('status')} {cp.get('authorizer_id') or ''}")
    else:
        print(f"authorizer  : down (run `tenuo-claude up`)")
    files = cloud_mode_files()
    if files["cloud_env"] and not files["cloud_state"]:
        print("cloud       : credentials present — run `tenuo-admin setup` then `tenuo-claude up`")
    elif mode := intended_mode(cfg):
        if mode == "cloud" and files["cloud_profile"]:
            print(f"cloud       : profile {CLOUD_PROFILE.name} merged")


# ---------------------------------------------------------------------------
# Audit / revoke / verify
# ---------------------------------------------------------------------------


def cmd_audit(args) -> None:
    if not RECEIPTS.exists():
        print("No receipts yet.")
        return
    rows = [json.loads(x) for x in RECEIPTS.read_text().splitlines() if x.strip()]
    if getattr(args, "tail", None):
        rows = rows[-args.tail:]
    for r in rows:
        if r.get("phase") == "post":
            print(f"  · outcome  {r.get('claude_tool',''):14} {r.get('outcome_preview','')[:60]}")
        else:
            d = r.get("decision", "")
            # In observe-only mode a "deny" was logged but NOT enforced.
            if d == "pending":
                mark = "PENDING"   # parked on human approval
            elif r.get("shadow") and d == "deny":
                mark = "WOULD-DENY"
            else:
                mark = "ALLOW" if d == "allow" else "DENY "
            src = ("appr" if r.get("source") == "approval"
                   else "mcp" if r.get("source") == "mcp_proxy"
                   else "gov" if r.get("governed") else "aud")
            who = f" <{r['agent_type']}>" if r.get("agent_type") else ""
            print(f"  {mark:10} [{src}] {r.get('claude_tool',''):14}{who} -> {r.get('tenuo_tool','')}  {r.get('reason','')}")


def cmd_revoke(_args) -> None:
    cfg = load_config()
    if not STATE_JSON.exists():
        raise SystemExit("Run `tenuo-claude init` first.")
    st = json.loads(STATE_JSON.read_text())
    wid = st["warrant_id"]
    env = runtime_env()
    creds = cloud_creds(cfg)
    cloud = bool(creds.get("url") and creds.get("api_key"))
    if cloud:
        url = creds["url"]
        print(f"Cloud mode. Revoke from the dashboard or an admin key:\n"
              f"  curl -X POST {url}/v1/revocations \\\n"
              f"    -H \"Authorization: Bearer $ADMIN_API_KEY\" -H 'Content-Type: application/json' \\\n"
              f"    -d '{{\"warrant_id\":\"{wid}\",\"reason\":\"revoked\"}}'\n"
              f"The authorizer pulls the new SRL within one heartbeat; next call is denied.")
        return
    from tenuo import SigningKey, SignedRevocationList

    issuer = SigningKey.from_bytes(base64.b64decode(ISSUER_KEY.read_text()))
    b = SignedRevocationList.builder()
    b.revoke(wid)
    b.version(int(time.time()))
    SRL.write_bytes(bytes(b.build(issuer).to_bytes()))
    print(f"Revoked {wid} locally. Restarting authorizer to load the SRL…")
    cmd_down(_args)
    cmd_up(_args)


def check_claude_hook_exit_contract() -> bool:
    """Live-check Claude Code PreToolUse semantics: exit 1 proceeds, exit 2 blocks."""
    claude = shutil.which("claude")
    if not claude:
        print("  .. hook-exit  claude not in PATH — skipped (install Claude Code to verify)")
        return True
    try:
        ver = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=15)
        version = (ver.stdout or ver.stderr or "").strip().splitlines()[0]
    except Exception as exc:
        print(f"  .. hook-exit  could not read claude --version ({exc}) — skipped")
        return True
    print(f"  .. hook-exit  live harness ({version})")

    with tempfile.TemporaryDirectory(prefix="tenuo-hook-exit-") as tmp:
        root = Path(tmp)
        canary = root / "CANARY.txt"
        canary.write_text("TENUO_HOOK_CANARY")
        claude_dir = root / ".claude"
        claude_dir.mkdir()

        def run_with_hook(exit_code: int) -> tuple[subprocess.CompletedProcess, Path]:
            marker = root / f"hook_ran_{exit_code}.txt"
            marker.unlink(missing_ok=True)
            hook = root / f"hook_exit{exit_code}.py"
            hook.write_text(
                "import sys\n"
                f"from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('exit={exit_code}\\n')\n"
                f"sys.exit({exit_code})\n")
            settings = {
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "Read",
                        "hooks": [{"type": "command",
                                   "command": f'"{sys.executable}" "{hook}"'}],
                    }]
                }
            }
            (claude_dir / "settings.json").write_text(json.dumps(settings))
            proc = subprocess.run(
                [claude, "-p",
                 "Read CANARY.txt. Reply with only the file contents, nothing else.",
                 "--dangerously-skip-permissions"],
                cwd=root, capture_output=True, text=True, timeout=90)
            return proc, marker

        try:
            r1, m1 = run_with_hook(1)
            r2, m2 = run_with_hook(2)
        except subprocess.TimeoutExpired:
            print("  .. hook-exit  claude timed out — skipped (API/network?)")
            return True
        except Exception as exc:
            print(f"  .. hook-exit  claude harness error ({exc}) — skipped")
            return True

        ok = True
        for exit_code, proc, marker in ((1, r1, m1), (2, r2, m2)):
            out = (proc.stdout or "") + (proc.stderr or "")
            leaked = "TENUO_HOOK_CANARY" in out
            ran = marker.is_file()
            if not ran:
                print(f"  XX hook-exit  exit {exit_code}: hook never ran "
                      f"(project settings not loaded? trust prompt?)")
                ok = False
                continue
            if exit_code == 1:
                if leaked:
                    print("  ok  hook-exit  exit 1 is non-blocking (canary reached)")
                else:
                    print("  XX hook-exit  exit 1 blocked unexpectedly — contract may have changed")
                    ok = False
            elif leaked:
                print("  XX hook-exit  exit 2 did NOT block (canary leaked)")
                ok = False
            else:
                print("  ok  hook-exit  exit 2 blocks the tool call")
        return ok


def cmd_verify(args) -> None:
    """Policy-driven authorizer self-test from tenuo.yaml."""
    if not _status_json():
        raise SystemExit("Authorizer not running. Run `tenuo-claude up` first.")
    cfg = load_config()
    deep = getattr(args, "deep", False)
    probes, _ = build_probes(cfg, deep=deep)
    roles = subagent_roles(cfg)

    def decide(tool, tin, role):
        allowed, reason, _, _ = authorize_call(cfg, tool, tin, role, roles, live=False)
        return allowed, reason

    ok, results = run_probes(probes, decide)
    extra: list[str] = []

    if roles:
        extra.append("  [subagent wiring]")
        defs = agent_definitions()
        for role in roles:
            resolved, where = resolve_subagent_role(role, defs)
            ok = ok and resolved
            mark = "ok" if resolved else "XX"
            detail = where if resolved else (
                f"NO agent definition — add .claude/agents/{role}.md or rename to a real subagent_type")
            extra.append(f"    {mark} role {role} -> {detail}")

    appr = webfetch_approval(cfg)
    if appr:
        extra.append("  [approval]")
        st = load_cloud_state()
        pid = st.get("web_fetch_approval_policy_id")
        approver = st.get("web_fetch_approver")
        cloud_ready = bool((cfg.get("cloud") or {}).get("url") and pid)
        ok = ok and (bool(pid) if cloud_ready else True)
        extra.append(
            f"    {'ok' if pid else '..'} policy {pid or 'not set up (run tenuo-admin setup)'}"
            f"{f'  approver={approver}' if approver else ''}")
        allowed, reason, _, _ = authorize_call(
            cfg, "WebFetch", {"url": "https://example.com/data"}, None, roles, live=False)
        gated = (not allowed) and reason.startswith(APPROVAL_PENDING_REASON)
        if cloud_ready:
            ok = ok and gated
            extra.append(
                f"    {'ok' if gated else 'XX'} gate off-allowlist -> "
                f"{'approval required' if gated else 'NOT gated (' + reason + ')'}")
        else:
            extra.append(
                f"    .. gate off-allowlist denied locally (approval is Cloud-only): {reason}")

    if deep and not getattr(args, "no_live", False):
        ok = ok and check_claude_hook_exit_contract()
    elif deep:
        extra.append("  .. hook-exit  skipped (--no-live)")

    format_text(cfg, results, extra_lines=extra, overall_ok=ok)
    raise SystemExit(0 if ok else 1)


def cmd_doctor(args) -> None:
    print("note: `doctor` is deprecated — use `tenuo-claude verify` "
          "(add `--deep` for SSRF / hook canary).", file=sys.stderr)
    cmd_verify(argparse.Namespace(deep=True, no_live=getattr(args, "no_live", False)))


def cmd_demo(args) -> None:
    """Run tenuo_demo.py in the project directory, if present."""
    demo_script = DEMO_DIR / "tenuo_demo.py"
    if not demo_script.is_file():
        raise SystemExit(
            "No scripted tour in this project (tenuo_demo.py).\n"
            "  tenuo-claude verify     policy self-test against your tenuo.yaml\n"
            "  Reference demo: https://github.com/tenuo-ai/claude-governance/tree/main/demo")
    cmd = [sys.executable, str(demo_script)]
    if getattr(args, "advanced", False):
        cmd.append("--advanced")
    if getattr(args, "live_approval", False):
        cmd.append("--live-approval")
    raise SystemExit(subprocess.run(cmd, cwd=DEMO_DIR).returncode or 0)


def _bench_percentile(sorted_ms: list[float], pct: float) -> float:
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, max(0, int(len(sorted_ms) * pct / 100)))
    return sorted_ms[idx]


def _bench_summary(samples_ms: list[float]) -> dict:
    if not samples_ms:
        return {"n": 0, "min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "p95": _bench_percentile(ordered, 95),
        "max": ordered[-1],
    }


def _bench_run(label: str, fn, *, iterations: int, warmup: int) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return {"label": label, **_bench_summary(samples)}


def _bench_pop_sign_ms(tenuo_tool: str, sign_args: dict, warrant_b64: str | None = None) -> None:
    from tenuo import SigningKey
    from tenuo_core import decode_warrant_stack_base64

    holder = SigningKey.from_bytes(base64.b64decode(HOLDER_KEY.read_text()))
    header_b64 = warrant_b64 if warrant_b64 is not None else WARRANT.read_text()
    leaf = decode_warrant_stack_base64(header_b64)[-1]
    leaf.sign(holder, tenuo_tool, sign_args, int(time.time()))


def cmd_bench(args) -> None:
    """Measure per-tool-call overhead (PoP sign, authorizer RTT, hook path)."""
    if not _status_json():
        raise SystemExit("Authorizer not running. Run `tenuo-claude up` first.")
    cfg = load_config()
    sb = cfg["_sandbox_abs"]
    Path(sb).mkdir(parents=True, exist_ok=True)
    probe = Path(sb) / ".tenuo_bench_probe"
    probe.write_text("bench\n")
    roles = subagent_roles(cfg)
    iterations = max(1, int(getattr(args, "iterations", 100)))
    warmup = max(0, int(getattr(args, "warmup", 10)))
    include_hook = not getattr(args, "no_hook", False)

    read_args = {"file_path": str(probe)}
    read_sign = {"path": os.path.realpath(os.path.abspath(read_args["file_path"]))}
    bash_args = {"command": "ls -la"}
    bash_sign = {"command": "ls -la"}

    scenarios: list[tuple[str, object]] = [
        ("PoP sign (session warrant)", lambda: _bench_pop_sign_ms("read_file", read_sign)),
        ("Authorizer /verify/read_file (allow)", lambda: authorize(
            "read_file", "/verify/read_file", read_sign, read_sign)),
        ("authorize_call Read (allow)", lambda: authorize_call(
            cfg, "Read", read_args, None, roles)),
        ("authorize_call Bash (allow)", lambda: authorize_call(
            cfg, "Bash", bash_args, None, roles)),
        ("authorize_call WebSearch (audit-allow)", lambda: authorize_call(
            cfg, "WebSearch", {"query": "bench"}, None, roles)),
    ]
    if roles:
        role = next(iter(roles))
        sw = subwarrant_path(role)
        if sw.exists():
            scenarios.insert(1, (
                f"PoP sign (subagent:{role})",
                lambda r=role, s=sw.read_text(): _bench_pop_sign_ms(
                    "read_file", read_sign, warrant_b64=s)))
            scenarios.append((
                f"authorize_call Read (subagent:{role})",
                lambda r=role: authorize_call(
                    cfg, "Read", read_args, r, roles)))

    results = [_bench_run(label, fn, iterations=iterations, warmup=warmup)
               for label, fn in scenarios]

    if include_hook:
        hook_event = json.dumps({
            "tool_name": "Read",
            "tool_input": read_args,
        })
        hook_cmd, hook_args = wiring_command_parts("_hook")
        hook_argv = [hook_cmd, *hook_args]

        def run_hook_subprocess() -> None:
            proc = subprocess.run(
                hook_argv,
                input=hook_event,
                text=True,
                capture_output=True,
                cwd=DEMO_DIR,
                timeout=30,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"hook exit {proc.returncode}")

        hook_label = shlex.join(hook_argv)
        results.append(_bench_run(
            f"Hook subprocess ({hook_label})", run_hook_subprocess,
            iterations=max(5, iterations // 10), warmup=min(warmup, 3)))

    payload = {
        "iterations": iterations,
        "warmup": warmup,
        "authorizer": resolve_authz_url(),
        "mode": cfg.get("mode", "enforce"),
        "subagents": bool(roles),
        "results": results,
    }

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return

    print(f"Tenuo overhead bench  (iterations={iterations}, warmup={warmup})")
    print(f"  authorizer : {resolve_authz_url()}")
    print(f"  mode       : {cfg.get('mode', 'enforce')}"
          f"{'  subagents: yes' if roles else ''}")
    print()
    print(f"{'Scenario':<42} {'p50':>8} {'p95':>8} {'max':>8}")
    print("-" * 68)
    for row in results:
        print(f"{row['label']:<42} {row['p50']:7.2f}ms {row['p95']:7.2f}ms {row['max']:7.2f}ms")
    print()
    print("Notes:")
    print("  • PoP sign = local Ed25519 + warrant decode (no network).")
    print("  • Authorizer row = sign + HTTP round-trip to localhost.")
    print("  • authorize_call = route resolution + authorizer (typical hook core).")
    if include_hook:
        print("  • Hook subprocess = full PreToolUse path incl. Python cold start per call.")
    print("  • Compare p50 on a quiet machine; authorizer RTT dominates on hot paths.")
    print("  • Core chain-verify microbenches: tenuo-core `cargo bench --bench warrant_benchmarks`.")


def cmd_init(args) -> None:
    scaffold_example_policy(DEMO_DIR, no_scaffold=getattr(args, "no_scaffold", False))
    if getattr(args, "local", False):
        moved = disable_cloud_artifacts()
        if moved:
            print("Local mode — moved aside:", ", ".join(moved))
    elif getattr(args, "cloud", False):
        url = getattr(args, "cloud_url", None)
        if not url and CLOUD_ENV.exists():
            creds = cloud_creds(load_config())
            url = creds.get("url")
        write_cloud_profile(url=url or "https://api.tenuo.ai")
        print(f"Cloud profile written: {CLOUD_PROFILE.name}")
        if getattr(args, "advanced", False) or getattr(args, "demo", False):
            approver = getattr(args, "approver", None)
            if not approver:
                raise SystemExit("--advanced requires --approver (Cloud identity display name).")
            write_advanced_profile(approver=approver)
            print(f"Advanced profile written: {ADVANCED_PROFILE.name}")
        if not CLOUD_ENV.exists() and CLOUD_ENV_EXAMPLE.exists():
            print(f"Next: copy {CLOUD_ENV_EXAMPLE.name} → .state/cloud.env and paste Quick Connect token")
    if (getattr(args, "advanced", False) or getattr(args, "demo", False)) and not getattr(args, "cloud", False):
        approver = getattr(args, "approver", None)
        if not approver:
            raise SystemExit("--advanced requires --approver (Cloud identity display name).")
        write_advanced_profile(approver=approver)
        print(f"Advanced profile written: {ADVANCED_PROFILE.name} — re-run `tenuo-admin setup`")
    cfg = load_config()
    info = generate(cfg)
    print("Initialized tenuo-claude.")
    print(f"  warrant  : {info['warrant_id']}")
    print(f"  sandbox  : {info['sandbox']}")
    print(f"  wired    : .claude/settings.json (PreToolUse/PostToolUse), .mcp.json, .state/gateway.yaml")
    print("Next: `tenuo-claude up` then use Claude Code in this directory.")


def cmd_refresh(args) -> None:
    cfg = load_config()
    creds = cloud_creds(cfg)
    use_trigger = bool(creds["url"] and creds["api_key"] and trigger_id(cfg))
    was_running = authorizer_running(cfg)

    wid = refresh_policy(cfg)
    print("Refreshed from tenuo.yaml:")
    print(f"  warrant  : {wid}")
    print(f"  gateway  : .state/{GATEWAY.name}")
    print(f"  wiring   : .claude/settings.json, .mcp.json")
    if use_trigger:
        print("  note     : enforce/audit/subagent changes need `tenuo-admin setup` first")

    if was_running and not getattr(args, "no_restart", False):
        print("Restarting authorizer (reload gateway)…")
        cmd_down(args)
        cmd_up(args)
    elif was_running:
        print("Authorizer left running (--no-restart). Run `down` then `up` to reload gateway.")
    else:
        print("Next: `tenuo-claude up`")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "init": cmd_init, "refresh": cmd_refresh, "up": cmd_up, "down": cmd_down, "status": cmd_status,
    "check": cmd_check, "onboard": cmd_onboard, "bootstrap": cmd_bootstrap,
    "install-authorizer": cmd_install_authorizer,
    "audit": cmd_audit, "revoke": cmd_revoke,
    "verify": cmd_verify, "doctor": cmd_doctor, "demo": cmd_demo, "bench": cmd_bench,
    "_hook": cmd_hook, "_post": cmd_post, "_mcp-proxy": cmd_mcp_proxy,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog=CLI_COMMAND, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    for name in ["down", "status", "check", "revoke", "_hook", "_post", "_mcp-proxy"]:
        sub.add_parser(name)
    pu = sub.add_parser("up", help="start the authorizer (Docker or native binary)")
    pu.add_argument("--native", action="store_true",
                    help="run tenuo-authorizer as a host process (no Docker)")
    pu.add_argument("--docker", action="store_true",
                    help="force Docker container (default when Docker is available)")
    pu.add_argument("--install", action="store_true",
                    help="with --native: install tenuo-authorizer to ~/.tenuo/bin if missing")
    pi_auth = sub.add_parser(
        "install-authorizer",
        help="install the pinned tenuo-authorizer binary to ~/.tenuo/bin",
    )
    pi_auth.add_argument("--force", action="store_true", help="reinstall even if version matches")
    pr = sub.add_parser("refresh",
                        help="re-apply tenuo.yaml (warrant, gateway, hooks) after policy edits")
    pr.add_argument("--no-restart", action="store_true",
                    help="skip authorizer restart (gateway stays stale until down/up)")
    pi = sub.add_parser("init")
    pi.add_argument("--cloud", action="store_true", help="write tenuo.cloud.yaml (Cloud URL)")
    pi.add_argument("--local", action="store_true", help="move Cloud files aside for local mode")
    pi.add_argument("--no-scaffold", action="store_true",
                    help="fail if tenuo.yaml is missing (default: write an example policy)")
    pi.add_argument("--advanced", action="store_true",
                    help="write tenuo.advanced.yaml (human approval overlay; WebFetch example)")
    pi.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)  # deprecated alias
    pi.add_argument("--approver", help="approver identity display name (requires --advanced)")
    pi.add_argument("--cloud-url", help="control plane URL (with --cloud; default from connect token or api.tenuo.ai)")
    po = sub.add_parser("onboard", help="interactive local or Cloud setup wizard")
    po.add_argument("--local", action="store_true", help="local mode (default when neither flag set)")
    po.add_argument("--cloud", action="store_true", help="Cloud mode")
    po.add_argument("--advanced", action="store_true",
                    help="also write advanced overlay (human approval; with --cloud)")
    po.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    po.add_argument("--yes", "-y", action="store_true", help="non-interactive where possible")
    po.add_argument("--connect-token", help="Quick Connect token (Cloud)")
    po.add_argument("--admin-key", help="tenant-admin key for one-time setup (Cloud)")
    po.add_argument("--approver", help="approver display name (requires --advanced)")
    po.add_argument("--no-scaffold", action="store_true",
                    help="fail if tenuo.yaml is missing (default: write an example policy)")
    pb = sub.add_parser("bootstrap", help="check + init + up + verify")
    pb.add_argument("--cloud", action="store_true", help="Cloud quickstart (default: local)")
    pb.add_argument("--advanced", action="store_true", help="include advanced overlay (with --cloud)")
    pb.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    pb.add_argument("--yes", "-y", action="store_true")
    pb.add_argument("--no-scaffold", action="store_true",
                    help="fail if tenuo.yaml is missing (default: write an example policy)")
    pb.add_argument("--connect-token")
    pb.add_argument("--admin-key")
    pb.add_argument("--approver")
    pv = sub.add_parser("verify", help="policy self-test against the authorizer")
    pv.add_argument("--deep", action="store_true",
                    help="SSRF matrix, extra Bash denies, live hook exit-code harness")
    pv.add_argument("--no-live", action="store_true",
                    help="with --deep: skip live Claude Code PreToolUse exit-code harness")
    pd = sub.add_parser("doctor", help=argparse.SUPPRESS)
    pd.add_argument("--no-live", action="store_true")
    pdemo = sub.add_parser("demo", help="scripted policy tour (tenuo_demo.py in project, if present)")
    pdemo.add_argument("--advanced", action="store_true",
                       help="include human approval scenarios (WebFetch example; requires overlay in policy)")
    pdemo.add_argument("--live-approval", action="store_true",
                       help="with --advanced: block until approver responds (Cloud)")
    pbench = sub.add_parser("bench", help="measure per-tool-call overhead (authorizer RTT, hook path)")
    pbench.add_argument("--iterations", type=int, default=100,
                        help="timed iterations per scenario (default: 100)")
    pbench.add_argument("--warmup", type=int, default=10, help="warmup iterations (default: 10)")
    pbench.add_argument("--no-hook", action="store_true",
                        help="skip subprocess _hook benchmark (faster)")
    pbench.add_argument("--json", action="store_true", help="machine-readable output")
    pa = sub.add_parser("audit")
    pa.add_argument("--tail", type=int, default=None)
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    # Global install — no governed project directory required.
    if args.cmd != "install-authorizer":
        bind_project_paths(
            sys.modules[__name__],
            fallback_cwd=args.cmd in ("init", "onboard", "bootstrap"),
        )
    # Separation of duties: the runtime/agent plane must never carry an admin
    # credential. Admin actions live in `tenuo-admin`. Skip the internal hook
    # handlers — they have their own fail-closed contract and must emit a deny
    # decision rather than raise (a raised SystemExit would be fail-open).
    if args.cmd not in ("_hook", "_post", "_mcp-proxy", "onboard", "bootstrap", "install-authorizer"):
        assert_no_admin_key()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
