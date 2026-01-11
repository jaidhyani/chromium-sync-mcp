#!/usr/bin/env python3
"""
Launch a browser in a virtual display with web VNC access for sync passphrase entry.

Auto-downloads cloudflared and noVNC. Only system requirement is Xvnc (TigerVNC)
or Xvfb+x11vnc, plus a Chromium-based browser.

Architecture:
  Xvnc (virtual X + VNC) → Chrome/Brave → noVNC (web client) → cloudflared tunnel
"""

import atexit
import io
import os
import platform
import re
import secrets
import shutil
import signal
import stat
import string
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "chromium-sync"
CLOUDFLARED_VERSION = "2024.12.2"
NOVNC_VERSION = "1.5.0"


class ManagedProcess:
    """A process with a name for logging."""

    def __init__(self, name: str, proc: subprocess.Popen):
        self.name = name
        self.proc = proc
        self.pid = proc.pid
        self.reported_dead = False


class SetupSession:
    """Manages the lifecycle of all spawned processes."""

    def __init__(self):
        self.processes: list[ManagedProcess] = []
        self.temp_dirs: list[Path] = []
        self.log_files: list[io.IOBase] = []
        self.display = ":99"
        self.cleaned_up = False
        self.log_dir = Path("/tmp/chromium-sync-setup")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def spawn(
        self, cmd: list[str], name: str, env: dict | None = None, **kwargs
    ) -> subprocess.Popen:
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        # Log stdout/stderr to files unless caller provides their own
        if "stdout" not in kwargs:
            stdout_log = open(self.log_dir / f"{name}.stdout.log", "w")
            self.log_files.append(stdout_log)
            kwargs["stdout"] = stdout_log
        if "stderr" not in kwargs:
            stderr_log = open(self.log_dir / f"{name}.stderr.log", "w")
            self.log_files.append(stderr_log)
            kwargs["stderr"] = stderr_log

        proc = subprocess.Popen(cmd, env=full_env, **kwargs)
        self.processes.append(ManagedProcess(name, proc))
        print(f"  Started {name} (pid {proc.pid})")
        return proc

    def check_processes(self):
        """Check for dead processes and report them once."""
        for mp in self.processes:
            if mp.proc.poll() is not None and not mp.reported_dead:
                mp.reported_dead = True
                exit_code = mp.proc.returncode
                print(f"DIED: {mp.name} (pid {mp.pid}) exited with code {exit_code}")
                print(f"      Check logs: {self.log_dir}/{mp.name}.stderr.log")

    def cleanup(self):
        if self.cleaned_up:
            return
        self.cleaned_up = True

        for mp in reversed(self.processes):
            if mp.proc.poll() is None:
                mp.proc.terminate()
                try:
                    mp.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    mp.proc.kill()

        for log_file in self.log_files:
            try:
                log_file.close()
            except Exception:
                pass

        for temp_dir in self.temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)


def download_file(url: str, dest: Path, desc: str) -> bool:
    """Download a file with progress indication."""
    print(f"  Downloading {desc}...")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as e:
        print(f"  Failed: {e}")
        return False


def ensure_cloudflared() -> Path | None:
    """Download cloudflared if not available."""
    if shutil.which("cloudflared"):
        return Path(shutil.which("cloudflared"))  # type: ignore

    cached = CACHE_DIR / "bin" / "cloudflared"
    if cached.exists() and os.access(cached, os.X_OK):
        return cached

    system = platform.system().lower()
    machine = platform.machine().lower()

    arch_map = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine, machine)

    if system == "linux":
        filename = f"cloudflared-linux-{arch}"
    elif system == "darwin":
        filename = f"cloudflared-darwin-{arch}"
    else:
        print(f"  Unsupported platform: {system}/{machine}")
        return None

    url = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{filename}"

    if not download_file(url, cached, "cloudflared"):
        return None

    cached.chmod(cached.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return cached


def ensure_novnc() -> Path | None:
    """Download noVNC if not available."""
    # Check common system locations first
    system_paths = [
        Path("/usr/share/novnc"),
        Path("/usr/share/webapps/novnc"),
        Path("/opt/novnc"),
    ]
    for p in system_paths:
        if (p / "vnc.html").exists():
            return p

    cached = CACHE_DIR / "novnc" / NOVNC_VERSION
    if (cached / "vnc.html").exists():
        return cached

    url = f"https://github.com/novnc/noVNC/archive/refs/tags/v{NOVNC_VERSION}.zip"
    zip_path = CACHE_DIR / "novnc.zip"

    if not download_file(url, zip_path, "noVNC"):
        return None

    try:
        cached.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                # Strip the top-level directory from paths
                parts = member.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    target = cached / parts[1]
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            dst.write(src.read())
        zip_path.unlink()
        return cached
    except Exception as e:
        print(f"  Failed to extract noVNC: {e}")
        return None


def check_command(name: str) -> bool:
    return shutil.which(name) is not None


def check_websockify() -> bool:
    """Check if websockify is available."""
    try:
        subprocess.run(
            [sys.executable, "-c", "import websockify"],
            capture_output=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def find_terminal() -> str | None:
    """Find an available terminal emulator."""
    terminals = ["xterm", "gnome-terminal", "konsole", "xfce4-terminal", "lxterminal"]
    for term in terminals:
        if check_command(term):
            return term
    return None


def find_available_browsers() -> list[str]:
    """Find all available browsers."""
    browsers = ["brave-browser", "google-chrome", "chromium-browser", "chromium"]
    return [b for b in browsers if check_command(b)]


def prompt_app_choice(browsers: list[str], terminal: str | None) -> tuple[str, list[str]]:
    """Prompt user to choose what to launch. Returns (choice_type, command)."""
    options: list[tuple[str, str, list[str]]] = []

    for browser in browsers:
        options.append(
            (
                "browser",
                browser,
                [
                    browser,
                    "--no-first-run",
                    "--disable-default-apps",
                    "chrome://settings/syncSetup",
                ],
            )
        )

    if terminal:
        options.append(("terminal", f"{terminal} (terminal)", [terminal]))

    if not options:
        return ("none", [])

    print("Available options:")
    for i, (_, name, _) in enumerate(options, 1):
        print(f"  {i}. {name}")
    print()

    while True:
        try:
            choice = input(f"Select [1-{len(options)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                selected = options[idx]
                return (selected[0], selected[2])
        except (ValueError, EOFError):
            pass
        print(f"Please enter a number between 1 and {len(options)}")


def generate_password(length: int = 8) -> str:
    """Generate a random alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_vnc_passwd_file(password: str, path: Path) -> bool:
    """Create a VNC password file using vncpasswd."""
    try:
        # TigerVNC's vncpasswd can read from stdin with -f flag
        result = subprocess.run(
            ["vncpasswd", "-f"],
            input=password.encode(),
            capture_output=True,
            check=True,
        )
        path.write_bytes(result.stdout)
        path.chmod(0o600)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """Wait for a port to become available."""
    import socket

    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(0.2)
    return False


def extract_tunnel_url(proc: subprocess.Popen, log_path: Path, timeout: float = 30.0) -> str | None:
    """Read cloudflared output to find the tunnel URL, also logging to file."""
    import select

    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    start = time.time()

    with open(log_path, "w") as log_file:
        while time.time() - start < timeout:
            if proc.poll() is not None:
                return None

            if proc.stderr and select.select([proc.stderr], [], [], 0.5)[0]:
                line = proc.stderr.readline()
                if line:
                    decoded = line.decode("utf-8", errors="replace")
                    log_file.write(decoded)
                    log_file.flush()
                    match = url_pattern.search(decoded)
                    if match:
                        return match.group(0)

    return None


def has_xvnc() -> bool:
    """Check if Xvnc (TigerVNC) is available."""
    return check_command("Xvnc")


def has_xvfb_x11vnc() -> bool:
    """Check if Xvfb + x11vnc combo is available."""
    return check_command("Xvfb") and check_command("x11vnc")


def print_install_instructions():
    """Print installation instructions for missing dependencies."""
    print()
    print("Missing X server with VNC support. Install one of:")
    print()
    print("  Option 1 - TigerVNC (recommended, single package):")
    print("    Ubuntu/Debian:  sudo apt install tigervnc-standalone-server")
    print("    Fedora:         sudo dnf install tigervnc-server")
    print("    Arch:           sudo pacman -S tigervnc")
    print()
    print("  Option 2 - Xvfb + x11vnc (two packages):")
    print("    Ubuntu/Debian:  sudo apt install xvfb x11vnc")
    print("    Fedora:         sudo dnf install xorg-x11-server-Xvfb x11vnc")
    print("    Arch:           sudo pacman -S xorg-server-xvfb x11vnc")
    print()


def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Chrome Sync Passphrase Setup                                ║")
    print("║  Web VNC access to browser for headless passphrase entry     ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    # Set up session and cleanup handlers early
    session = SetupSession()

    def signal_handler(_sig, _frame):
        print("\n\nShutting down...")
        session.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(session.cleanup)

    # Find available browsers and terminal
    browsers = find_available_browsers()
    terminal = find_terminal()

    if not browsers and not terminal:
        print("ERROR: No browser or terminal emulator found.")
        print("Install a browser (brave-browser, google-chrome, chromium)")
        print("Or a terminal (xterm, gnome-terminal, konsole)")
        sys.exit(1)

    # Let user choose what to launch
    app_type, app_cmd = prompt_app_choice(browsers, terminal)
    if app_type == "none":
        print("ERROR: No application selected")
        sys.exit(1)

    # Check for X server with VNC
    use_xvnc = has_xvnc()
    use_xvfb = has_xvfb_x11vnc()

    if not use_xvnc and not use_xvfb:
        print_install_instructions()
        sys.exit(1)

    # Check/install websockify
    if not check_websockify():
        print("Installing websockify...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "websockify"],
            capture_output=True,
        )
        if result.returncode != 0:
            print("ERROR: Failed to install websockify")
            print("Try: pip install websockify")
            sys.exit(1)

    # Download cloudflared if needed
    print("Checking cloudflared...")
    cloudflared = ensure_cloudflared()
    if not cloudflared:
        print("ERROR: Could not find or download cloudflared")
        sys.exit(1)
    print(f"  Using: {cloudflared}")

    # Download noVNC if needed
    print("Checking noVNC...")
    novnc_path = ensure_novnc()
    if not novnc_path:
        print("ERROR: Could not find or download noVNC")
        sys.exit(1)
    print(f"  Using: {novnc_path}")

    # Generate VNC password
    vnc_password = generate_password(8)
    passwd_file = session.log_dir / "vncpasswd"

    print()
    print(f"Launching: {app_cmd[0]}")
    print(f"Display server: {'Xvnc (TigerVNC)' if use_xvnc else 'Xvfb + x11vnc'}")
    print(f"Logs: {session.log_dir}/")
    print()

    vnc_port = 5900
    display_num = 99
    session.display = f":{display_num}"

    # Create VNC password file
    if use_xvnc:
        if not create_vnc_passwd_file(vnc_password, passwd_file):
            print("ERROR: Failed to create VNC password file")
            print("       Is vncpasswd installed?")
            sys.exit(1)

    if use_xvnc:
        # Xvnc combines X server and VNC in one
        print("Starting Xvnc (virtual display + VNC server)...")
        session.spawn(
            [
                "Xvnc",
                session.display,
                "-geometry",
                "1280x720",
                "-depth",
                "24",
                "-rfbport",
                str(vnc_port),
                "-SecurityTypes",
                "VncAuth",
                "-PasswordFile",
                str(passwd_file),
                "-localhost",
            ],
            name="xvnc",
        )
    else:
        # Xvfb + x11vnc
        print("Starting Xvfb (virtual display)...")
        xvfb = session.spawn(
            ["Xvfb", session.display, "-screen", "0", "1280x720x24"],
            name="xvfb",
        )
        time.sleep(1)
        if xvfb.poll() is not None:
            print("ERROR: Xvfb failed to start")
            sys.exit(1)

        print("Starting x11vnc...")
        session.spawn(
            [
                "x11vnc",
                "-display",
                session.display,
                "-passwd",
                vnc_password,
                "-listen",
                "localhost",
                "-rfbport",
                str(vnc_port),
                "-shared",
                "-forever",
            ],
            name="x11vnc",
        )

    time.sleep(1)
    if not wait_for_port(vnc_port):
        print("ERROR: VNC server failed to start on port", vnc_port)
        sys.exit(1)

    display_env = {"DISPLAY": session.display}

    # Start the selected application
    print(f"Starting {app_cmd[0]}...")
    session.spawn(
        app_cmd,
        name="app",
        env=display_env,
    )
    time.sleep(1)

    # Start noVNC via websockify
    print("Starting noVNC web server...")
    novnc_port = 6080
    session.spawn(
        [
            sys.executable,
            "-m",
            "websockify",
            "--web",
            str(novnc_path),
            str(novnc_port),
            f"localhost:{vnc_port}",
        ],
        name="websockify",
    )

    if not wait_for_port(novnc_port):
        print("ERROR: noVNC/websockify failed to start")
        sys.exit(1)

    # Start cloudflared tunnel
    print("Starting cloudflared tunnel...")
    tunnel = session.spawn(
        [str(cloudflared), "tunnel", "--url", f"http://localhost:{novnc_port}"],
        name="cloudflared",
        stderr=subprocess.PIPE,  # Need to read stderr for URL extraction
    )

    tunnel_url = extract_tunnel_url(tunnel, session.log_dir / "cloudflared.stderr.log")
    if not tunnel_url:
        print("ERROR: Failed to get tunnel URL from cloudflared")
        print("       cloudflared may have exited. Check your network connection.")
        sys.exit(1)

    vnc_url = f"{tunnel_url}/vnc.html"

    print()
    print("═" * 66)
    print()
    print("  VNC URL:")
    print(f"  {vnc_url}")
    print()
    print(f"  Password: {vnc_password}")
    print()
    print("  Steps:")
    print("  1. Open the URL above in your browser")
    print("  2. Enter the password when prompted")
    if app_type == "browser":
        print("  3. Sign into Chrome/Brave and enter your sync passphrase")
        print("  4. Wait for sync to complete")
    else:
        print("  3. In the terminal, launch your browser (e.g., brave-browser)")
        print("  4. Sign into Chrome/Brave and enter your sync passphrase")
    print("  5. Press Ctrl+C here when done")
    print()
    print("═" * 66)
    print()
    print("Waiting... (Ctrl+C to stop)")

    while True:
        session.check_processes()
        time.sleep(2)


if __name__ == "__main__":
    main()
