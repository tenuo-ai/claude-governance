"""encode_approvals_header — X-Tenuo-Approvals wire encoding."""
from __future__ import annotations

import base64


def test_single_signature_passes_through(cli_mod):
    # A threshold-1 Cloud response is already base64(CBOR); send it as-is.
    assert cli_mod.encode_approvals_header(["AAAA"]) == "AAAA"


def test_multi_signature_wraps_in_cbor_array(cli_mod):
    blobs = [b"\x01", b"\x02", b"\x03"]
    sigs = [base64.b64encode(b).decode() for b in blobs]
    out = cli_mod.encode_approvals_header(sigs)
    raw = base64.b64decode(out)
    assert raw[0] == 0x80 | 3            # CBOR array header for 3 items
    assert raw[1:] == b"".join(blobs)    # concatenated item bodies


def test_multi_signature_caps_at_23(cli_mod):
    # CBOR single-byte array header is only valid for N < 24.
    sigs = [base64.b64encode(bytes([i])).decode() for i in range(25)]
    raw = base64.b64decode(cli_mod.encode_approvals_header(sigs))
    assert raw[0] == 0x80 | 23
    assert len(raw) == 1 + 23
