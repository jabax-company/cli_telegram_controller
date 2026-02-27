#!/usr/bin/env python3
"""Serve current directory and expose it through Cloudflare tunnel.

This script is launched by bot.py (/serve command). It:
1) starts a local static file server on the requested port
2) starts cloudflared tunnel to that local URL
3) streams cloudflared logs to stdout so bot.py can capture the public URL
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time


def _parse_port(argv: list[str]) -> int:
    if len(argv) < 2:
        return 8080
    try:
        return int(argv[1])
    except ValueError:
        return 8080


def _terminate(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    port = _parse_port(sys.argv)
    local_url = f"http://127.0.0.1:{port}"
    serve_dir = os.getcwd()

    server_proc: subprocess.Popen[str] | None = None
    tunnel_proc: subprocess.Popen[str] | None = None

    try:
        server_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
                "--directory",
                serve_dir,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(0.8)
        if server_proc.poll() is not None:
            print("Local HTTP server failed to start.", flush=True)
            return 1

        cloudflared = shutil.which("cloudflared")
        if cloudflared is None:
            print("cloudflared not found in PATH.", flush=True)
            return 1

        tunnel_proc = subprocess.Popen(
            [cloudflared, "tunnel", "--url", local_url, "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def _handle_signal(_sig: int, _frame: object) -> None:
            _terminate(tunnel_proc)
            _terminate(server_proc)
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        if tunnel_proc.stdout is None:
            print("cloudflared started without stdout stream.", flush=True)
            return 1

        for line in tunnel_proc.stdout:
            print(line.rstrip("\n"), flush=True)

        return tunnel_proc.wait()
    finally:
        _terminate(tunnel_proc)
        _terminate(server_proc)


if __name__ == "__main__":
    raise SystemExit(main())
