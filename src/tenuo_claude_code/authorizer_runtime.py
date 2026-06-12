"""Authorizer process lifecycle: Docker container or native ``tenuo-authorizer`` binary."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_IMAGE = "tenuo/authorizer:0.1.0-beta.24"
_INFO_VERSION_RE = re.compile(r"^Tenuo Authorizer v(\S+)", re.MULTILINE)


def authorizer_crate_version(image: str = DEFAULT_IMAGE) -> str:
    """Crates.io / Docker tag for the pinned authorizer (e.g. ``0.1.0-beta.24``)."""
    return image.rsplit(":", 1)[-1].lstrip("v")


def install_hint(image: str = DEFAULT_IMAGE) -> str:
    version = authorizer_crate_version(image)
    return (
        f"cargo install tenuo --version {version} "
        f"--features data-plane,server --bin tenuo-authorizer --locked"
    )


def crate_version_from_authorizer_version(version: str) -> str:
    """Strip ``+authz.N`` build metadata from a full authorizer version string."""
    return version.split("+", 1)[0]


def parse_binary_version(info_output: str) -> str | None:
    match = _INFO_VERSION_RE.search(info_output)
    return match.group(1) if match else None


def query_binary_version(binary: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(binary), "info"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return parse_binary_version(result.stdout)


def version_compatible(installed: str | None, pinned_crate: str) -> bool:
    if not installed:
        return True
    return crate_version_from_authorizer_version(installed) == pinned_crate


def ensure_binary_version(binary: Path, image: str = DEFAULT_IMAGE) -> str | None:
    """Fail (or warn) when the native binary crate version differs from the package pin."""
    installed = query_binary_version(binary)
    pinned = authorizer_crate_version(image)
    if installed is None:
        print("Warning: could not read tenuo-authorizer version (continuing)")
        return None
    if version_compatible(installed, pinned):
        return installed
    msg = (
        f"tenuo-authorizer {installed} does not match pinned {pinned}.\n"
        f"  • {install_hint(image)}\n"
        "  • Or set TENUO_AUTHORIZER_SKIP_VERSION=1 to override"
    )
    if os.environ.get("TENUO_AUTHORIZER_SKIP_VERSION", "").strip().lower() in ("1", "true", "yes"):
        print(f"Warning: {msg.replace(chr(10), ' ')}")
        return installed
    raise SystemExit(msg)


def runtime_meta_path(mount: Path) -> Path:
    return mount / "runtime.json"


def read_runtime_meta(mount: Path) -> dict:
    path = runtime_meta_path(mount)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def read_runtime_backend(mount: Path) -> str | None:
    backend = read_runtime_meta(mount).get("backend")
    return backend if isinstance(backend, str) else None


def write_runtime_meta(mount: Path, **fields: object) -> None:
    mount.mkdir(parents=True, exist_ok=True)
    data = read_runtime_meta(mount)
    data.update(fields)
    runtime_meta_path(mount).write_text(json.dumps(data, indent=2))


def clear_runtime_meta(mount: Path) -> None:
    runtime_meta_path(mount).unlink(missing_ok=True)


def find_authorizer_binary(image: str = DEFAULT_IMAGE) -> Path | None:
    """Best-effort lookup: ``TENUO_AUTHORIZER_BIN`` or ``tenuo-authorizer`` on PATH."""
    override = os.environ.get("TENUO_AUTHORIZER_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    found = shutil.which("tenuo-authorizer")
    return Path(found) if found else None


def resolve_authorizer_binary(image: str = DEFAULT_IMAGE) -> Path:
    """Locate the published ``tenuo-authorizer`` binary (override → PATH)."""
    override = os.environ.get("TENUO_AUTHORIZER_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise SystemExit(f"TENUO_AUTHORIZER_BIN={override!r} not found")
        return path
    found = shutil.which("tenuo-authorizer")
    if found:
        return Path(found)
    raise SystemExit(
        "Could not find tenuo-authorizer on PATH.\n"
        f"  • {install_hint(image)}\n"
        "  • Or set TENUO_AUTHORIZER_BIN to the binary path"
    )


def docker_ok() -> tuple[bool, str]:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return False, "Docker not installed"
    except subprocess.TimeoutExpired:
        return False, "Docker not responding"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else "docker info failed"
    return True, "Docker daemon running"


def choose_backend(args, *, force_native: bool = False) -> str:
    if getattr(args, "native", False) and getattr(args, "docker", False):
        raise SystemExit("Use only one of --native or --docker")
    if getattr(args, "docker", False):
        return "docker"
    if force_native or getattr(args, "native", False):
        return "native"
    env = os.environ.get("TENUO_AUTHORIZER_BACKEND", "").strip().lower()
    if env in ("native", "process"):
        return "native"
    if os.environ.get("TENUO_AUTHORIZER_NATIVE", "").strip().lower() in ("1", "true", "yes"):
        return "native"
    ok, _ = docker_ok()
    return "native" if not ok else "docker"


def port_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def fetch_health(authz_url: str) -> dict | None:
    try:
        with urllib.request.urlopen(authz_url + "/health", timeout=2) as resp:
            data = json.loads(resp.read().decode())
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_tenuo_authorizer_health(authz_url: str) -> bool:
    health = fetch_health(authz_url)
    return bool(health and health.get("service") == "tenuo-authorizer")


def assert_port_available(port: int, authz_url: str, mount: Path) -> None:
    """Raise if the loopback port is taken by a foreign or unmanaged process."""
    if not port_listening("127.0.0.1", port):
        return
    if is_tenuo_authorizer_health(authz_url):
        if native_process_alive(mount):
            return
        raise SystemExit(
            f"127.0.0.1:{port} already serves tenuo-authorizer but is not managed by "
            f"this project (stale PID or manual start). Stop it, run `tenuo-claude down`, "
            f"or set PORT to another value."
        )
    raise SystemExit(
        f"127.0.0.1:{port} is already in use by another process. "
        f"Free the port or set PORT to use a different one."
    )


def native_pid_path(mount: Path) -> Path:
    return mount / "native.pid"


def native_log_path(state: Path) -> Path:
    return state / "authorizer.log"


def native_process_alive(mount: Path) -> bool:
    """True if the native authorizer PID file points at a live process."""
    pid_file = native_pid_path(mount)
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False


def native_running(mount: Path, authz_url: str) -> bool:
    if not native_process_alive(mount):
        return False
    try:
        with urllib.request.urlopen(authz_url + "/health", timeout=2):
            return True
    except Exception:
        return False


def stop_native(mount: Path, *, timeout_s: float = 5.0) -> bool:
    pid_file = native_pid_path(mount)
    if not pid_file.is_file():
        return False
    pid: int | None = None
    try:
        pid = int(pid_file.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        clear_runtime_meta(mount)
        return True
    deadline = time.time() + timeout_s
    while time.time() < deadline and pid is not None:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        if pid is not None:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, OSError, ValueError):
                pass
    pid_file.unlink(missing_ok=True)
    clear_runtime_meta(mount)
    return True


def start_native(
    *,
    binary: Path,
    mount: Path,
    gateway_name: str,
    port: int,
    authz_url: str,
    denv: dict[str, str],
    srl_name: str | None,
    state: Path,
    image: str = DEFAULT_IMAGE,
) -> None:
    gw = mount / gateway_name
    if not gw.is_file():
        raise SystemExit(f"Missing {gw} — run `tenuo-claude init` first.")
    assert_port_available(port, authz_url, mount)
    ensure_binary_version(binary, image)
    stop_native(mount)
    env = os.environ.copy()
    env.update(denv)
    if srl_name and (mount / srl_name).is_file():
        env["TENUO_REVOCATION_LIST"] = str((mount / srl_name).resolve())
    log_path = native_log_path(state)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary), "serve",
        "--config", str(gw.resolve()),
        "--port", str(port),
        "--bind", "127.0.0.1",
    ]
    with log_path.open("a", encoding="utf-8") as logfh:
        logfh.write(f"\n--- native authorizer start {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ---\n")
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=logfh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    native_pid_path(mount).write_text(str(proc.pid))
    write_runtime_meta(mount, backend="native", binary=str(binary.resolve()))


def wait_healthy(
    authz_url: str,
    *,
    is_running: Callable[[], bool],
    on_exited: Callable[[], None] | None = None,
    timeout_s: float = 20.0,
) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(authz_url + "/health", timeout=2):
                return
        except Exception:
            if not is_running():
                if on_exited:
                    on_exited()
                raise SystemExit("Authorizer exited during startup")
            time.sleep(0.5)
    raise SystemExit("Authorizer didn't become healthy in time")
