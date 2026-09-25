"""The portal: publish the dashboard on your own domain with a Cloudflare Tunnel, and keep it running.

    daxton tunnel setup daxton.example.com   install cloudflared, log in, create the tunnel, route the
                                             hostname, write ~/.cloudflared/config.yml, install the service
    daxton tunnel run                        run the tunnel in the foreground (for a first test)
    daxton tunnel quick [--service]          no domain needed: a Cloudflare "quick tunnel" with a random
                                             https://<words>.trycloudflare.com address (changes on restart)
    daxton tunnel status                     what is configured, what is running, what is still missing
    daxton service install | uninstall | status
                                             a launchd agent that keeps `daxton ui` running at login

Nothing here opens a port on the Mac: cloudflared dials out to Cloudflare, and Cloudflare
forwards https://<hostname> to http://127.0.0.1:<port>. The dashboard's own login
(DASHBOARD_PASSWORD) is required before the tunnel is set up; Cloudflare Access in front
of the hostname is strongly recommended and is described by `daxton tunnel setup` at the end.

Every subprocess call is printed before it runs, and the ones that need a browser
(`cloudflared tunnel login`) or elevated rights are left to the terminal, so nothing
happens behind your back.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CLOUDFLARED_DIR = Path.home() / ".cloudflared"
CANDIDATE_BINARIES = ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SERVICE_LABEL = "ai.daxton.dashboard"
QUICK_LABEL = "ai.daxton.tunnel"
QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


# ------------------------------------------------------------------ pure helpers (tested)
def valid_hostname(hostname: str) -> bool:
    return bool(HOSTNAME_RE.match((hostname or "").strip().lower()))


def config_yaml(tunnel_id: str, credentials_file: Path | str, hostname: str, port: int = 8765,
                host: str = "127.0.0.1") -> str:
    """The cloudflared config: one hostname to the dashboard, everything else 404."""
    if not valid_hostname(hostname):
        raise TunnelError(f"'{hostname}' is not a valid hostname (expected something like daxton.example.com)")
    return (
        f"# Daxton AI portal, written by `daxton tunnel setup` on {datetime.now():%Y-%m-%d %H:%M}\n"
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials_file}\n"
        "\n"
        "ingress:\n"
        f"  - hostname: {hostname.strip().lower()}\n"
        f"    service: http://{host}:{int(port)}\n"
        "  - service: http_status:404\n"
    )


def parse_tunnel_list(output: str) -> list[dict]:
    """`cloudflared tunnel list --output json` -> [{id, name, ...}], tolerant of an empty answer."""
    output = (output or "").strip()
    if not output:
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def find_tunnel(tunnels: list[dict], name: str) -> dict | None:
    for t in tunnels:
        if t.get("name") == name and not t.get("deleted_at"):
            return t
    return None


def parse_created_tunnel(output: str) -> str | None:
    """The id in 'Created tunnel daxton with id 6ff42ae2-...' (or the credentials path cloudflared prints)."""
    m = re.search(r"with id ([0-9a-f-]{36})", output or "")
    if m:
        return m.group(1)
    m = re.search(r"([0-9a-f-]{36})\.json", output or "")
    return m.group(1) if m else None


def launchd_plist(label: str, program_args: list[str], working_dir: str | Path, log_path: str | Path,
                  env: dict[str, str] | None = None) -> bytes:
    plist = {
        "Label": label,
        "ProgramArguments": [str(a) for a in program_args],
        "WorkingDirectory": str(working_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1",
                                 **(env or {})},
    }
    return plistlib.dumps(plist)


def access_instructions(hostname: str, email_hint: str = "") -> str:
    who = email_hint or "your email address"
    return (
        "Put Cloudflare Access in front of it (free for up to 50 users), so only you reach the login page:\n"
        "  1. https://one.dash.cloudflare.com  ->  Access  ->  Applications  ->  Add an application  ->  Self-hosted\n"
        f"  2. Application domain: {hostname}    Session duration: 1 month (or what you like)\n"
        f"  3. Add a policy: Allow  ->  Include  ->  Emails  ->  {who}\n"
        "  4. Authentication: One-time PIN is on by default (a code by email); add Google or Apple if you prefer\n"
        "  5. Save. From now on the hostname asks Cloudflare for a login first, then Daxton asks for its password.\n"
        "Cloudflare Access also keeps bots and scanners from ever touching the Mac."
    )


# ------------------------------------------------------------------ cloudflared
def find_cloudflared() -> str | None:
    path = shutil.which("cloudflared")
    if path:
        return path
    for candidate in CANDIDATE_BINARIES:
        if Path(candidate).is_file():
            return candidate
    return None


def _run(cmd: list[str], check: bool = True, capture: bool = False, quiet: bool = False, **kw) -> subprocess.CompletedProcess:
    if not quiet:
        print("  $ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, **kw)


def install_cloudflared() -> str:
    """brew install cloudflared (macOS/Linuxbrew); otherwise explain."""
    brew = shutil.which("brew") or ("/opt/homebrew/bin/brew" if Path("/opt/homebrew/bin/brew").is_file() else "")
    if not brew:
        raise TunnelError("cloudflared is not installed and Homebrew is not available. Install it from "
                          "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ "
                          "then run this again.")
    print("Installing cloudflared with Homebrew ...")
    _run([brew, "install", "cloudflared"])
    path = find_cloudflared()
    if not path:
        raise TunnelError("brew finished but cloudflared is still not on PATH; open a new terminal and retry")
    return path


def logged_in() -> bool:
    return (CLOUDFLARED_DIR / "cert.pem").is_file()


def list_tunnels(cloudflared: str) -> list[dict]:
    r = _run([cloudflared, "tunnel", "list", "--output", "json"], check=False, capture=True, quiet=True)
    if r.returncode != 0:
        raise TunnelError(f"cloudflared tunnel list failed: {(r.stderr or r.stdout).strip()[:400]}")
    return parse_tunnel_list(r.stdout)


def ensure_tunnel(cloudflared: str, name: str) -> tuple[str, Path]:
    """Create the named tunnel if it does not exist. Returns (tunnel id, credentials file)."""
    existing = find_tunnel(list_tunnels(cloudflared), name)
    if existing:
        tunnel_id = existing["id"]
        print(f"Tunnel '{name}' already exists ({tunnel_id}).")
    else:
        r = _run([cloudflared, "tunnel", "create", name], check=False, capture=True)
        out = (r.stdout or "") + (r.stderr or "")
        tunnel_id = parse_created_tunnel(out)
        if r.returncode != 0 or not tunnel_id:
            raise TunnelError(f"could not create the tunnel: {out.strip()[:400]}")
        print(f"Created tunnel '{name}' ({tunnel_id}).")
    creds = CLOUDFLARED_DIR / f"{tunnel_id}.json"
    if not creds.is_file():
        raise TunnelError(f"the tunnel exists but its credentials file is missing: {creds}\n"
                          f"Delete the tunnel in the Cloudflare dashboard (or `cloudflared tunnel delete {name}`) and rerun.")
    return tunnel_id, creds


def write_config(text: str, path: Path | None = None) -> Path:
    path = path or (CLOUDFLARED_DIR / "config.yml")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") != text:
        backup = path.with_suffix(f".yml.{datetime.now():%Y%m%d-%H%M%S}.bak")
        shutil.copy2(path, backup)
        print(f"Existing {path.name} kept as {backup.name}.")
    path.write_text(text, encoding="utf-8")
    return path


def route_dns(cloudflared: str, name: str, hostname: str) -> None:
    r = _run([cloudflared, "tunnel", "route", "dns", name, hostname], check=False, capture=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode == 0:
        print(f"DNS: {hostname} -> tunnel '{name}'.")
    elif "already exists" in out or "record with that host already exists" in out.lower():
        print(f"DNS: a record for {hostname} already exists; if it is not this tunnel's CNAME, fix it in the "
              "Cloudflare DNS page (target <tunnel id>.cfargotunnel.com, proxied).")
    else:
        raise TunnelError(f"could not route DNS: {out[:400]}")


def cloudflared_service_installed() -> bool:
    if platform.system() == "Darwin":
        return (Path.home() / "Library/LaunchAgents/com.cloudflare.cloudflared.plist").is_file() or \
            Path("/Library/LaunchDaemons/com.cloudflare.cloudflared.plist").is_file()
    return Path("/etc/systemd/system/cloudflared.service").is_file()


def install_cloudflared_service(cloudflared: str) -> None:
    """A launch agent for the current user on macOS (no sudo); systemd needs root elsewhere."""
    if cloudflared_service_installed():
        print("cloudflared service already installed.")
        return
    if platform.system() == "Darwin":
        r = _run([cloudflared, "service", "install"], check=False, capture=True)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            raise TunnelError(f"cloudflared service install failed: {out[:400]}")
        print("cloudflared installed as a launch agent (runs at login).")
    else:
        print("Run `sudo cloudflared service install` to start the tunnel at boot.")


def cloudflared_running() -> bool:
    r = subprocess.run(["pgrep", "-x", "cloudflared"], capture_output=True, text=True)
    return r.returncode == 0


# ------------------------------------------------------------------ commands
def setup(settings, hostname: str, port: int | None = None, name: str | None = None, no_service: bool = False) -> int:
    hostname = (hostname or settings.public_hostname or "").strip().lower().rstrip(".")
    name = name or settings.tunnel_name
    port = port or settings.dashboard_port
    if not hostname:
        raise TunnelError("which hostname? daxton tunnel setup daxton.yourdomain.com (the domain must be on Cloudflare DNS)")
    if not valid_hostname(hostname):
        raise TunnelError(f"'{hostname}' is not a valid hostname")
    if not settings.dashboard_password:
        raise TunnelError("set DASHBOARD_PASSWORD in .env first (a long passphrase); the portal is never published "
                          "without a login")
    if len(settings.dashboard_password) < 12:
        print("Warning: DASHBOARD_PASSWORD is short. Anything reachable from the internet deserves 12+ characters.")

    print(f"Portal: https://{hostname}  ->  http://127.0.0.1:{port}  (tunnel '{name}')\n")
    cloudflared = find_cloudflared() or install_cloudflared()
    print(f"cloudflared: {cloudflared}")

    if not logged_in():
        print("\nLogging in to Cloudflare: a browser window opens; pick the domain the hostname belongs to.")
        _run([cloudflared, "tunnel", "login"])
        if not logged_in():
            raise TunnelError("login did not produce ~/.cloudflared/cert.pem; run `cloudflared tunnel login` and retry")
    else:
        print("Cloudflare login: present.")

    tunnel_id, creds = ensure_tunnel(cloudflared, name)
    path = write_config(config_yaml(tunnel_id, creds, hostname, port))
    print(f"Wrote {path}.")
    route_dns(cloudflared, name, hostname)
    if not no_service:
        install_cloudflared_service(cloudflared)

    print("\nDone. Next:")
    print(f"  * add PUBLIC_HOSTNAME={hostname} to .env (the dashboard uses it to check WebSocket origins)")
    print("  * start the dashboard: daxton ui --no-browser   (or install it as a service: daxton service install)")
    print(f"  * open https://{hostname} from your phone; the login page asks for DASHBOARD_PASSWORD")
    if no_service or not cloudflared_service_installed():
        print(f"  * run the tunnel: daxton tunnel run   (or install it: cloudflared service install)")
    print()
    print(access_instructions(hostname))
    return 0


def run(settings, name: str | None = None) -> int:
    cloudflared = find_cloudflared()
    if not cloudflared:
        raise TunnelError("cloudflared is not installed; run `daxton tunnel setup <hostname>` first")
    name = name or settings.tunnel_name
    print(f"Running tunnel '{name}' in the foreground (Ctrl-C to stop). Keep `daxton ui` running in another terminal.")
    try:
        return subprocess.call([cloudflared, "tunnel", "run", name])
    except KeyboardInterrupt:
        return 130


def status(settings) -> int:
    cloudflared = find_cloudflared()
    config = CLOUDFLARED_DIR / "config.yml"
    lines = [
        ("cloudflared", cloudflared or "not installed (daxton tunnel setup <hostname> installs it)"),
        ("logged in", "yes" if logged_in() else "no (cloudflared tunnel login)"),
        ("config", str(config) if config.is_file() else "missing"),
        ("hostname", settings.public_hostname or "PUBLIC_HOSTNAME not set"),
        ("password", "set" if settings.dashboard_password else "DASHBOARD_PASSWORD not set: remote access is refused"),
        ("tunnel service", "installed" if cloudflared_service_installed() else "not installed"),
        ("quick tunnel", ("service installed" if quick_service_installed() else "not installed")
         + (f", address {quick_url(settings)}" if quick_url(settings) else "")),
        ("tunnel process", "running" if cloudflared_running() else "not running"),
        ("dashboard service", service_status_text()),
    ]
    if config.is_file():
        text = config.read_text(encoding="utf-8")
        m = re.search(r"hostname:\s*(\S+)", text)
        if m:
            lines.insert(4, ("published", f"https://{m.group(1)}"))
    url = portal_url(settings)
    if url:
        lines.insert(0, ("portal", url))
    width = max(len(k) for k, _ in lines)
    for k, v in lines:
        print(f"  {k:<{width}}  {v}")
    return 0


# ------------------------------------------------------------------ quick tunnels (no domain)
def quick_url_from_log(text: str) -> str | None:
    """The newest https://<words>.trycloudflare.com address in cloudflared's output."""
    found = QUICK_URL_RE.findall(text or "")
    return found[-1] if found else None


def quick_log_path(settings) -> Path:
    return Path(settings.data_dir) / "logs" / "tunnel.log"


def quick_url_file(settings) -> Path:
    return Path(settings.data_dir) / "portal-url.txt"


def quick_url(settings) -> str | None:
    """The current quick-tunnel address, from whichever of the service log or the foreground run is newer."""
    candidates = []
    for path in (quick_log_path(settings), quick_url_file(settings)):
        try:
            if path.is_file():
                url = quick_url_from_log(path.read_text(encoding="utf-8", errors="replace")[-20000:])
                if url:
                    candidates.append((path.stat().st_mtime, url))
        except OSError:
            continue
    return max(candidates)[1] if candidates else None


def portal_url(settings) -> str | None:
    """Where the portal answers right now: the named hostname if configured, else the quick tunnel's address."""
    if settings.public_hostname:
        return f"https://{settings.public_hostname}"
    return quick_url(settings)


def quick_plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{QUICK_LABEL}.plist"


def quick(settings, port: int | None = None, service: bool = False, wait: float = 45.0) -> int:
    """A quick tunnel: https://<random>.trycloudflare.com -> the dashboard. No account, no domain, no DNS.

    The address is random and changes every time cloudflared restarts, and there is no Cloudflare Access in
    front of it, so the dashboard password is the only lock. Fine to start with; `daxton tunnel setup` with a
    domain on Cloudflare DNS is the permanent version.
    """
    port = port or settings.dashboard_port
    if not settings.dashboard_password:
        raise TunnelError("set DASHBOARD_PASSWORD in .env first; nothing is published without a login")
    cloudflared = find_cloudflared() or install_cloudflared()
    args = [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
    if service:
        if platform.system() != "Darwin":
            raise TunnelError("--service uses macOS launchd; on Linux run the tunnel under systemd")
        log_path = quick_log_path(settings)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        plist = quick_plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(launchd_plist(QUICK_LABEL, args, Path.home(), log_path))
        _launchctl_load(plist)
        print(f"Installed {QUICK_LABEL}: the quick tunnel runs at login and restarts if it drops. Log: {log_path}")
        print("Waiting for the address ...")
        import time

        started = time.time()
        while time.time() - started < wait:
            url = quick_url_from_log(log_path.read_text(encoding="utf-8", errors="replace")[-20000:]) if log_path.is_file() else None
            if url:
                print(f"\nPortal: {url}\n(the address changes when the tunnel restarts; `daxton tunnel status` shows the current one, "
                      "and the dashboard's Systems panel shows it with a QR code)")
                return 0
            time.sleep(1.0)
        print("No address yet; `daxton tunnel status` will show it once the tunnel connects.")
        return 0
    print(f"Quick tunnel to http://127.0.0.1:{port} (Ctrl-C to stop). Keep `daxton ui` running.")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    url_file = quick_url_file(settings)
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            url = quick_url_from_log(line)
            if url:
                url_file.parent.mkdir(parents=True, exist_ok=True)
                url_file.write_text(url + "\n", encoding="utf-8")
                print(f"\nPortal: {url}\n", flush=True)
            elif "ERR" in line or "error" in line.lower():
                print(line.rstrip(), flush=True)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 130
    finally:
        try:
            url_file.unlink()
        except OSError:
            pass


def quick_service_installed() -> bool:
    return quick_plist_path().is_file()


def quick_service_uninstall() -> None:
    plist = quick_plist_path()
    if plist.is_file():
        _launchctl_unload(plist)
        plist.unlink()


# ------------------------------------------------------------------ launchd service for the dashboard
def service_plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{SERVICE_LABEL}.plist"


def service_installed() -> bool:
    return service_plist_path().is_file()


def service_running() -> bool:
    if platform.system() != "Darwin":
        return False
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return any(line.strip().endswith(SERVICE_LABEL) for line in r.stdout.splitlines())


def service_status_text() -> str:
    if platform.system() != "Darwin":
        return "launchd services are macOS only"
    if not service_installed():
        return "not installed (daxton service install)"
    return "installed, " + ("running" if service_running() else "not running")


def service_install(settings, host: str | None = None, port: int | None = None) -> int:
    if platform.system() != "Darwin":
        raise TunnelError("the service installer is for macOS launchd; on Linux write a systemd unit for `daxton ui`")
    daxton = shutil.which("daxton") or str(Path(sys.executable).parent / "daxton")
    if not Path(daxton).is_file():
        raise TunnelError("cannot find the `daxton` executable; activate the virtualenv (source .venv/bin/activate) first")
    workdir = Path.cwd()
    if not (workdir / ".env").is_file():
        print(f"Note: no .env in {workdir}; the service loads .env from there. Run this from the repo folder.")
    log_dir = Path(settings.data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    args = [daxton, "ui", "--no-browser", "--host", host or settings.dashboard_host, "--port", str(port or settings.dashboard_port)]
    plist = service_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(launchd_plist(SERVICE_LABEL, args, workdir, log_dir / "dashboard.log"))
    print(f"Wrote {plist}")
    _launchctl_load(plist)
    print(f"Installed {SERVICE_LABEL}: `daxton ui` now runs at login and restarts if it stops. Log: {log_dir / 'dashboard.log'}")
    print("If the Mac asks whether Python may use the microphone, allow it once; the voice loop needs it.")
    return 0


def service_uninstall(settings) -> int:
    plist = service_plist_path()
    if quick_service_installed():
        quick_service_uninstall()
        print(f"Removed {QUICK_LABEL} (the quick tunnel).")
    if not plist.is_file():
        print("Dashboard service not installed.")
        return 0
    _launchctl_unload(plist)
    plist.unlink()
    print(f"Removed {SERVICE_LABEL}.")
    return 0


def service_status(settings) -> int:
    print(f"  {SERVICE_LABEL}: {service_status_text()}")
    plist = service_plist_path()
    if plist.is_file():
        data = plistlib.loads(plist.read_bytes())
        print("  command:", " ".join(data.get("ProgramArguments", [])))
        print("  folder: ", data.get("WorkingDirectory"))
        print("  log:    ", data.get("StandardOutPath"))
    return 0


def _launchctl_load(plist: Path) -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
    if r.returncode != 0:  # older launchctl
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        r = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
        if r.returncode != 0:
            raise TunnelError(f"launchctl could not load the service: {(r.stderr or r.stdout).strip()}")


def _launchctl_unload(plist: Path) -> None:
    uid = os.getuid()
    r = subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
