#!/usr/bin/env python3
"""Local fullstack reverse proxy.

Routes API calls to backend and everything else to frontend so both are exposed
through one Cloudflare URL.
"""

from __future__ import annotations

import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class FullstackProxyHandler(BaseHTTPRequestHandler):
    frontend_port: int = 0
    backend_port: int = 0
    api_prefix: str = "/api"
    protocol_version = "HTTP/1.1"

    def _is_api_path(self, path: str) -> bool:
        prefix = self.api_prefix
        if prefix == "/":
            return True
        return path == prefix or path.startswith(prefix + "/")

    def _target_port(self) -> int:
        path_only = self.path.split("?", 1)[0]
        return self.backend_port if self._is_api_path(path_only) else self.frontend_port

    def _proxy_request(self) -> None:
        target_port = self._target_port()
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {}
        for key, value in self.headers.items():
            key_lower = key.lower()
            if key_lower in HOP_BY_HOP_HEADERS or key_lower == "host":
                continue
            headers[key] = value
        headers["Host"] = f"127.0.0.1:{target_port}"

        conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=120)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()

            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                key_lower = key.lower()
                if key_lower in HOP_BY_HOP_HEADERS or key_lower == "content-length":
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception as exc:
            message = f"Proxy error to 127.0.0.1:{target_port}: {exc}\n".encode("utf-8")
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
        finally:
            conn.close()

    def do_GET(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy_request()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def _parse_args(argv: list[str]) -> tuple[int, int, int, str]:
    if len(argv) < 4:
        raise ValueError(
            "Usage: python fullstack_proxy.py <listen_port> <frontend_port> "
            "<backend_port> [api_prefix]"
        )
    listen_port = int(argv[1])
    frontend_port = int(argv[2])
    backend_port = int(argv[3])
    api_prefix = argv[4] if len(argv) > 4 else "/api"
    if not api_prefix.startswith("/"):
        api_prefix = "/" + api_prefix
    if api_prefix != "/" and api_prefix.endswith("/"):
        api_prefix = api_prefix.rstrip("/")
    return listen_port, frontend_port, backend_port, api_prefix


def main() -> int:
    try:
        listen_port, frontend_port, backend_port, api_prefix = _parse_args(sys.argv)
    except Exception as exc:
        print(str(exc), flush=True)
        return 1

    handler_cls = type(
        "ConfiguredFullstackProxyHandler",
        (FullstackProxyHandler,),
        {
            "frontend_port": frontend_port,
            "backend_port": backend_port,
            "api_prefix": api_prefix,
        },
    )

    server = ThreadingHTTPServer(("127.0.0.1", listen_port), handler_cls)
    print(
        f"fullstack proxy listening on 127.0.0.1:{listen_port} "
        f"(front={frontend_port}, backend={backend_port}, api_prefix={api_prefix})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
