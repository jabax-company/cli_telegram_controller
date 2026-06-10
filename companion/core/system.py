"""Local system control helpers: info, processes, screenshot, lock."""

from __future__ import annotations

import datetime
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import psutil
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None


def _require_psutil() -> None:
    if psutil is None:
        raise RuntimeError("psutil is not installed. Run: pip install psutil")


def _fmt_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def get_system_info() -> str:
    _require_psutil()
    boot = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.datetime.now() - boot
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home()))

    lines = [
        f"Host: {platform.node()}",
        f"OS: {platform.system()} {platform.release()}",
        f"Python: {platform.python_version()}",
        f"Uptime: {str(uptime).split('.')[0]}",
        f"CPU: {cpu_pct:.0f}% ({psutil.cpu_count()} cores)",
        f"RAM: {mem.percent:.0f}% used ({_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)})",
        f"Disk (home): {disk.percent:.0f}% used ({_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)})",
    ]
    battery = getattr(psutil, "sensors_battery", lambda: None)()
    if battery is not None:
        plug = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {battery.percent:.0f}% ({plug})")
    return "\n".join(lines)


def list_processes(name_filter: str = "", limit: int = 15) -> str:
    _require_psutil()
    name_filter = name_filter.strip().lower()
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            name = info.get("name") or "?"
            if name_filter and name_filter not in name.lower():
                continue
            rss = info["memory_info"].rss if info.get("memory_info") else 0
            procs.append((rss, info["pid"], name, info.get("cpu_percent") or 0.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not procs:
        return "No matching processes."
    procs.sort(reverse=True)
    lines = ["PID      MEM       CPU%   NAME"]
    for rss, pid, name, cpu in procs[:limit]:
        lines.append(f"{pid:<8} {_fmt_bytes(rss):<9} {cpu:<6.1f} {name[:40]}")
    return "\n".join(lines)


def describe_process(pid: int) -> str:
    _require_psutil()
    p = psutil.Process(pid)
    return f"{p.name()} (pid {pid}, started {datetime.datetime.fromtimestamp(p.create_time()):%H:%M:%S})"


def kill_process(pid: int) -> str:
    _require_psutil()
    p = psutil.Process(pid)
    name = p.name()
    p.terminate()
    try:
        p.wait(timeout=3)
    except psutil.TimeoutExpired:
        p.kill()
    return f"Terminated {name} (pid {pid})."


def take_screenshot() -> str:
    """Capture all screens to a temp PNG and return its path."""
    try:
        from PIL import ImageGrab
    except Exception as exc:
        raise RuntimeError(f"Pillow is required for screenshots: {exc}") from exc

    fd_path = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        image = ImageGrab.grab(all_screens=True)
    except TypeError:
        # all_screens is Windows-only on older Pillow versions
        image = ImageGrab.grab()
    image.save(fd_path, format="PNG")
    return str(fd_path)


def lock_screen() -> str:
    system = platform.system()
    if system == "Windows":
        cmd = ["rundll32.exe", "user32.dll,LockWorkStation"]
    elif system == "Darwin":
        cmd = [
            "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
            "-suspend",
        ]
    else:
        for candidate in (
            ["loginctl", "lock-session"],
            ["xdg-screensaver", "lock"],
            ["gnome-screensaver-command", "--lock"],
        ):
            if shutil.which(candidate[0]):
                cmd = candidate
                break
        else:
            raise RuntimeError("No screen lock command found on this system.")

    subprocess.run(cmd, check=True, timeout=10)
    return "Screen locked."


__all__ = [
    "get_system_info",
    "list_processes",
    "describe_process",
    "kill_process",
    "take_screenshot",
    "lock_screen",
]
