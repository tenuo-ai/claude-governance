"""Local IPC binding for the authorizer client.

Loopback TCP cannot AUTHENTICATE the responder: if the system authorizer is down,
any local process can bind 127.0.0.1:<port> and answer "allow". The Unix-socket
transport closes that gap by trusting OS file ownership instead of a port — a
root-owned socket under a root-owned, non-world-writable dir cannot be replaced by
an unprivileged user. These tests pin the security-critical shape of that path:
the resolver opts in safely, managed mode CANNOT be downgraded to TCP by inherited
env, the safety check rejects spoofable sockets, and every unsafe/missing socket
FAILS CLOSED.

Sockets live under a short ``/tmp`` dir on purpose: ``AF_UNIX`` paths are capped
(~104 chars on macOS) and pytest's ``tmp_path`` blows past that.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer
from types import SimpleNamespace

import pytest

posix_only = pytest.mark.skipif(os.name != "posix", reason="Unix-socket transport is POSIX-only")

# The ownership-discrimination tests assume the test user is NOT root: their premise
# is "a user-owned socket/dir must be rejected as untrusted". When the runner is root
# (e.g. CI under WSL), everything the test creates is root-owned and therefore
# correctly TRUSTED, so those negative assertions don't apply. Skip them as root.
requires_nonroot = pytest.mark.skipif(
    os.name == "posix" and os.geteuid() == 0,
    reason="needs a non-root user (root owns everything it creates, so user-vs-root "
           "ownership can't be distinguished)")


@pytest.fixture(autouse=True)
def _clean_env_bound(monkeypatch, bound):
    """Every test runs with project path globals bound (so `resolve_authz_url` can
    read STATE_JSON) and a clean transport/managed environment."""
    for var in ("TENUO_AUTHZ_TRANSPORT", "TENUO_AUTHZ_SOCKET",
                "TENUO_AUTHZ_SERVICE_UID", "TENUO_MANAGED_ENFORCE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def short_socket_dir():
    """A short-pathed dir for binding AF_UNIX sockets, cleaned up after."""
    d = tempfile.mkdtemp(prefix="tnu-ipc-", dir="/tmp")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _bind_socket(dir_path: str, name: str = "authorizer.sock") -> tuple[socket.socket, str]:
    path = os.path.join(dir_path, name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    return s, path


# --- transport resolution (unmanaged) --------------------------------------

def test_default_endpoint_is_tcp(cli_mod):
    """No transport env -> behavior is unchanged: loopback TCP. The socket path is
    strictly opt-in, so existing deployments don't silently change."""
    mode, loc = cli_mod.authz_endpoint()
    assert mode == "tcp" and loc.startswith("http")


def test_explicit_unix_transport_uses_default_socket(cli_mod, monkeypatch):
    monkeypatch.setenv("TENUO_AUTHZ_TRANSPORT", "unix")
    assert cli_mod.authz_endpoint() == ("unix", cli_mod.DEFAULT_AUTHZ_SOCKET)


def test_authz_display_tracks_transport(cli_mod, monkeypatch):
    """status/verify must show the endpoint actually in use, not always the TCP URL,
    so a healthy managed socket deployment doesn't point admins at the old transport."""
    assert cli_mod.authz_display().startswith("http")  # default tcp
    monkeypatch.setenv("TENUO_AUTHZ_TRANSPORT", "unix")
    monkeypatch.setenv("TENUO_AUTHZ_SOCKET", "/run/tenuo/x.sock")
    assert cli_mod.authz_display() == "unix:///run/tenuo/x.sock"


def test_socket_path_implies_unix(cli_mod, monkeypatch):
    monkeypatch.setenv("TENUO_AUTHZ_SOCKET", "/run/tenuo/x.sock")
    assert cli_mod.authz_endpoint() == ("unix", "/run/tenuo/x.sock")


def test_explicit_tcp_wins_over_socket_when_unmanaged(cli_mod, monkeypatch):
    """Unmanaged: an explicit tcp transport is the dev escape and overrides a stray
    socket path."""
    monkeypatch.setenv("TENUO_AUTHZ_SOCKET", "/run/tenuo/x.sock")
    monkeypatch.setenv("TENUO_AUTHZ_TRANSPORT", "tcp")
    assert cli_mod.authz_endpoint()[0] == "tcp"


def test_apply_transport_env_seeds_from_policy(cli_mod, make_cfg):
    cli_mod.apply_transport_env(make_cfg(authorizer={"transport": "unix", "socket": "/run/tenuo/a.sock"}))
    assert cli_mod.authz_endpoint() == ("unix", "/run/tenuo/a.sock")


def test_apply_transport_env_does_not_clobber_existing_env(cli_mod, make_cfg, monkeypatch):
    monkeypatch.setenv("TENUO_AUTHZ_SOCKET", "/run/tenuo/pinned.sock")
    cli_mod.apply_transport_env(make_cfg(authorizer={"socket": "/tmp/attacker.sock"}))
    assert cli_mod.authz_endpoint() == ("unix", "/run/tenuo/pinned.sock")


# --- managed mode cannot be downgraded to TCP (P1a) -------------------------

def test_managed_forces_unix_ignoring_inherited_tcp(cli_mod, monkeypatch):
    """The core P1a property: Claude runs hooks with the user's env, so a user
    setting TENUO_AUTHZ_TRANSPORT=tcp must NOT put managed enforcement back on
    unauthenticated loopback TCP."""
    monkeypatch.setenv("TENUO_MANAGED_ENFORCE", "1")
    monkeypatch.setenv("TENUO_AUTHZ_TRANSPORT", "tcp")
    assert cli_mod.authz_endpoint() == ("unix", cli_mod.DEFAULT_AUTHZ_SOCKET)


def test_managed_honors_socket_path_from_env(cli_mod, monkeypatch):
    """The socket PATH may come from env (a hostile path is caught by the ownership
    check, not here), but the transport is still forced to unix."""
    monkeypatch.setenv("TENUO_MANAGED_ENFORCE", "1")
    monkeypatch.setenv("TENUO_AUTHZ_SOCKET", "/run/tenuo/managed.sock")
    monkeypatch.setenv("TENUO_AUTHZ_TRANSPORT", "tcp")
    assert cli_mod.authz_endpoint() == ("unix", "/run/tenuo/managed.sock")


def test_managed_breakglass_is_root_owned_file_not_env(cli_mod, monkeypatch):
    """An env var must NOT be able to flip the break-glass (it would be user-
    spoofable); only the root-owned marker file does."""
    monkeypatch.setenv("TENUO_MANAGED_ENFORCE", "1")
    monkeypatch.setenv("TENUO_AUTHZ_INSECURE_TCP", "1")  # must be ignored
    assert cli_mod.authz_endpoint()[0] == "unix"

    monkeypatch.setattr(cli_mod, "_insecure_tcp_breakglass", lambda: True)
    assert cli_mod.authz_endpoint()[0] == "tcp"


@posix_only
@requires_nonroot
def test_breakglass_rejects_user_owned_marker(cli_mod, monkeypatch, short_socket_dir):
    """A real, user-created marker is not trusted (it isn't root-owned)."""
    marker = os.path.join(short_socket_dir, "allow_insecure_tcp")
    open(marker, "w").close()
    monkeypatch.setattr(cli_mod, "BREAKGLASS_TCP_FILE", marker)
    assert cli_mod._insecure_tcp_breakglass() is False


@posix_only
def test_breakglass_rejects_symlink_even_to_root_file(cli_mod, monkeypatch, short_socket_dir):
    """The symlink attack: point the marker at a root-owned file (e.g. /etc/hosts)
    so a following `stat` sees root. lstat + regular-file check must reject it."""
    target = os.path.join(short_socket_dir, "target")
    open(target, "w").close()
    marker = os.path.join(short_socket_dir, "allow_insecure_tcp")
    os.symlink(target, marker)
    monkeypatch.setattr(cli_mod, "BREAKGLASS_TCP_FILE", marker)
    # lstat sees the link, not the target, so it's rejected before any owner check —
    # which is exactly why a symlink to a root-owned file can't re-enable TCP.
    assert cli_mod._insecure_tcp_breakglass() is False


@posix_only
def test_breakglass_accepts_root_owned_regular_file(cli_mod, monkeypatch):
    """Positive path: a regular, root-owned, non-writable file under a root-owned,
    non-writable dir. We fake ownership since tests don't run as root (and only for
    the marker path, so fixture teardown's own lstat/stat calls keep working)."""
    import stat as _stat
    marker = "/etc/tenuo/allow_insecure_tcp"
    monkeypatch.setattr(cli_mod, "BREAKGLASS_TCP_FILE", marker)
    real_lstat, real_stat = os.lstat, os.stat
    file_mode = {"v": _stat.S_IFREG | 0o644}
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: (
        SimpleNamespace(st_uid=0, st_mode=file_mode["v"]) if str(p) == marker else real_lstat(p, *a, **k)))
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: (
        SimpleNamespace(st_uid=0, st_mode=_stat.S_IFDIR | 0o755) if str(p) == os.path.dirname(marker)
        else real_stat(p, *a, **k)))
    assert cli_mod._insecure_tcp_breakglass() is True

    # Group/world-writable marker (same owner) is rejected.
    file_mode["v"] = _stat.S_IFREG | 0o666
    assert cli_mod._insecure_tcp_breakglass() is False


# --- socket safety (the authentication substitute) -------------------------

@posix_only
def test_unsafe_socket_missing(cli_mod, short_socket_dir):
    ok, why = cli_mod._safe_managed_socket(os.path.join(short_socket_dir, "nope.sock"))
    assert not ok and "unavailable" in why


@posix_only
def test_unsafe_socket_not_a_socket(cli_mod, short_socket_dir):
    f = os.path.join(short_socket_dir, "regular")
    with open(f, "w") as fh:
        fh.write("x")
    ok, why = cli_mod._safe_managed_socket(f)
    assert not ok and "not a socket" in why


@posix_only
def test_unsafe_socket_symlink_rejected(cli_mod, short_socket_dir):
    s, real = _bind_socket(short_socket_dir, "real.sock")
    link = os.path.join(short_socket_dir, "link.sock")
    os.symlink(real, link)
    try:
        ok, why = cli_mod._safe_managed_socket(link)
        assert not ok and "symlink" in why
    finally:
        s.close()


@posix_only
@requires_nonroot
def test_unsafe_socket_user_owned_dir_rejected(cli_mod, short_socket_dir, monkeypatch):
    """The realistic local-spoofing case: a socket under a dir the user owns. Even
    if the socket itself passed the owner check (here via TENUO_AUTHZ_SERVICE_UID),
    a non-root-owned parent dir must still reject it — a user who owns the dir could
    swap the socket."""
    s, path = _bind_socket(short_socket_dir)
    monkeypatch.setenv("TENUO_AUTHZ_SERVICE_UID", str(os.getuid()))
    try:
        ok, why = cli_mod._safe_managed_socket(path)
        assert not ok and "not trusted-owned" in why
    finally:
        s.close()


@posix_only
@requires_nonroot
def test_unsafe_socket_user_owned_socket_rejected(cli_mod, short_socket_dir):
    """With no service-uid override, a user-created socket is rejected at the
    socket-owner check (it isn't root-owned)."""
    s, path = _bind_socket(short_socket_dir)
    try:
        ok, why = cli_mod._safe_managed_socket(path)
        assert not ok and "not trusted" in why
    finally:
        s.close()


@posix_only
@requires_nonroot
def test_unmanaged_unix_accepts_user_owned_socket(cli_mod, short_socket_dir):
    """The dev opt-in (managed=False) must actually work: a socket the developer
    owns, in a dir they own and that isn't world-writable, is trusted. Otherwise the
    advertised unmanaged unix transport would always fail closed."""
    s, path = _bind_socket(short_socket_dir)
    try:
        ok, why = cli_mod._safe_managed_socket(path, managed=False)
        assert ok, why
        # ...but the SAME socket is rejected under the strict managed check.
        assert cli_mod._safe_managed_socket(path, managed=True)[0] is False
    finally:
        s.close()


def test_apply_transport_env_is_noop_when_managed(cli_mod, make_cfg, monkeypatch):
    """Managed mode must not read transport/socket from the editable policy file —
    not even a socket path, which a dev could otherwise set to silently turn managed
    enforcement into deny-all (or just to confuse). It uses the default socket."""
    monkeypatch.setenv("TENUO_MANAGED_ENFORCE", "1")
    cli_mod.apply_transport_env(make_cfg(authorizer={"transport": "tcp", "socket": "/tmp/dev.sock"}))
    assert "TENUO_AUTHZ_SOCKET" not in os.environ
    assert cli_mod.authz_endpoint() == ("unix", cli_mod.DEFAULT_AUTHZ_SOCKET)


@posix_only
def test_safe_socket_accepted_with_root_owned_dir(cli_mod, short_socket_dir, monkeypatch):
    """Positive path: a real socket owned by the configured service uid under a
    (faked) root-owned, non-writable dir passes. We fake only the dir's stat since
    tests don't run as root; the socket-type/symlink/owner checks run for real."""
    s, path = _bind_socket(short_socket_dir)
    monkeypatch.setenv("TENUO_AUTHZ_SERVICE_UID", str(os.getuid()))
    parent = os.path.dirname(os.path.realpath(path))
    real_stat = os.stat
    monkeypatch.setattr(os, "stat",
                        lambda p, *a, **k: (SimpleNamespace(st_uid=0, st_mode=0o040755)
                                            if str(p) == parent else real_stat(p, *a, **k)))
    try:
        ok, why = cli_mod._safe_managed_socket(path)
        assert ok, why
    finally:
        s.close()


# --- fail-closed enforcement over the socket -------------------------------

@posix_only
def test_authorize_over_uds_denies_missing_socket(cli_mod, short_socket_dir):
    allowed, reason, body = cli_mod._authorize_over_uds(
        os.path.join(short_socket_dir, "gone.sock"), "/authorize", {}, b"{}")
    assert allowed is False and body == {} and "untrusted authorizer socket" in reason


@posix_only
@requires_nonroot
def test_authorize_over_uds_denies_user_owned_socket_when_managed(cli_mod, short_socket_dir, monkeypatch):
    """Under the pinned managed hook, a user-owned socket must be refused outright
    (not even connected to), since the developer is the adversary."""
    monkeypatch.setenv("TENUO_MANAGED_ENFORCE", "1")
    s, path = _bind_socket(short_socket_dir)
    try:
        allowed, reason, _ = cli_mod._authorize_over_uds(path, "/authorize", {}, b"{}")
        assert allowed is False and "untrusted authorizer socket" in reason
    finally:
        s.close()


# --- HTTP-over-UDS plumbing ------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def _reply(self, payload: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._reply(b'{"authorized": true}')

    def do_GET(self):
        self._reply(b'{"running": true}')

    def log_message(self, *a):
        pass


@posix_only
def test_uds_connection_speaks_http_to_a_unix_server(cli_mod, short_socket_dir):
    sock_path = os.path.join(short_socket_dir, "srv.sock")
    server = UnixStreamServer(sock_path, _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, raw = cli_mod._UDSConnection(sock_path, timeout=3).request(
            "POST", "/authorize", {"Content-Length": "2"}, b"{}")
        assert status == 200 and json.loads(raw)["authorized"] is True
    finally:
        server.shutdown()
        server.server_close()
