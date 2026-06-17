"""mcp_proxy_decision — how the MCP proxy turns an authorize result into an action.

Pure logic, no Docker / authorizer / network (per conftest). This is the decision
the proxy's call_tool handler makes AFTER authorize_call returns — including the
human-in-the-loop case, where authorize_call has already blocked on the Cloud
approval poll and `allowed` reflects the approver's decision. See
writing/hitl-mcp-gateway-plan.md.
"""
from __future__ import annotations

from tenuo_claude_code import cli


def test_allow_forwards(cli_mod):
    forward, message = cli_mod.mcp_proxy_decision(True, "authorized", False, "read_file")
    assert forward is True
    assert message is None


def test_hard_deny_does_not_forward(cli_mod):
    forward, message = cli_mod.mcp_proxy_decision(
        False, "not permitted by policy", False, "delete_deployment")
    assert forward is False
    assert message == "Tenuo denied delete_deployment: not permitted by policy"


def test_approval_pending_is_distinct_from_hard_deny(cli_mod):
    # authorize_with_approval surfaces the pending/declined/timed-out family with
    # APPROVAL_PENDING_REASON ("approval required") in the reason. The agent must be
    # able to tell "not run, awaiting/declined approval" from "denied by policy".
    for areason in (
        f"{cli_mod.APPROVAL_PENDING_REASON}: 1 approval(s) required",
        f"{cli_mod.APPROVAL_PENDING_REASON} — approval timed out (no response from approver)",
        f"{cli_mod.APPROVAL_PENDING_REASON} — denied by approver (no reason given)",
    ):
        forward, message = cli_mod.mcp_proxy_decision(False, areason, False, "delete_deployment")
        assert forward is False
        assert message == f"Tenuo: delete_deployment not run — {areason}"
        assert "denied delete_deployment" not in message  # not framed as a hard deny


def test_audit_mode_always_forwards(cli_mod):
    # Observe-only: even a would-deny forwards (the caller logs WOULD-DENY).
    forward, message = cli_mod.mcp_proxy_decision(False, "not permitted by policy", True, "delete_deployment")
    assert forward is True
    assert message is None


def test_audit_mode_forwards_even_on_approval_pending(cli_mod):
    forward, message = cli_mod.mcp_proxy_decision(
        False, f"{cli_mod.APPROVAL_PENDING_REASON}: 1 approval(s) required", True, "delete_deployment")
    assert forward is True
    assert message is None
