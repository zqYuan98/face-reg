from __future__ import annotations

import contextlib
import http.client
import http.server
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


CONSOLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONSOLE_ROOT))

import server as console_server  # noqa: E402


PUBLIC_ORIGIN = "http://console.test:18003"
PUBLIC_HOST = "console.test:18003"
BOUNDARY = "----VisualConsoleBoundary7MA4YWxkTrZu0gW"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def multipart_file_body(
    *, field: str, filename: str, content_type: str, payload: bytes
) -> bytes:
    return (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        "\r\n"
    ).encode("ascii") + payload + f"\r\n--{BOUNDARY}--\r\n".encode("ascii")


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, object]] = []
        self.post_responder = self.default_post_responder
        self.ready_status = 200
        self.ready_headers: list[tuple[str, str]] = []
        self.ready_body = json.dumps(
            {
                "status": "healthy",
                "ready": True,
                "models": [{"name": "detector", "state": "ready"}],
                "capabilities": {
                    "portrait": "ready",
                    "image_verification": "ready",
                    "video_verification": "ready",
                    "witness_blur": "ready",
                },
            }
        ).encode()

    def record(self, handler: http.server.BaseHTTPRequestHandler, body: bytes) -> None:
        with self.lock:
            self.requests.append(
                {
                    "method": handler.command,
                    "path": handler.path,
                    "headers": list(handler.headers.items()),
                    "body": body,
                }
            )

    @staticmethod
    def default_post_responder(
        handler: http.server.BaseHTTPRequestHandler, _body: bytes
    ) -> None:
        payload = b'{"success":true,"suggestion":"REVIEW"}'
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: http.server.ThreadingHTTPServer

    @property
    def state(self) -> UpstreamState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health/ready":
            self.send_error(404)
            return
        self.state.record(self, b"")
        self.send_response(self.state.ready_status)
        for name, value in self.state.ready_headers:
            self.send_header(name, value)
        if not any(
            name.lower() == "content-length" for name, _value in self.state.ready_headers
        ):
            self.send_header("Content-Length", str(len(self.state.ready_body)))
        if not any(
            name.lower() == "content-type" for name, _value in self.state.ready_headers
        ):
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.state.ready_body:
            self.wfile.write(self.state.ready_body)

    def do_POST(self) -> None:  # noqa: N802
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length.isdecimal() else 0
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.rfile.read(min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        self.state.record(self, body)
        try:
            self.state.post_responder(self, body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


class RunningServers:
    def __init__(
        self,
        *,
        sessions: console_server.SessionStore | None = None,
    ) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.static_root = Path(self.tempdir.name)
        files = {
            "index.html": b"<!doctype html><title>Console</title>",
            "styles.css": b"body { color: #123; }",
            "core.js": b"export const core = true;",
            "app.js": b"import { core } from './core.js'; void core;",
        }
        for name, payload in files.items():
            (self.static_root / name).write_bytes(payload)

        self.upstream_state = UpstreamState()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), MockUpstreamHandler
        )
        self.upstream.daemon_threads = True
        self.upstream.state = self.upstream_state  # type: ignore[attr-defined]
        upstream_port = self.upstream.server_address[1]

        config = console_server.ConsoleConfig.create(
            bind_host="127.0.0.1",
            port=0,
            upstream=f"http://127.0.0.1:{upstream_port}",
            public_origin=PUBLIC_ORIGIN,
            static_root=self.static_root,
        )
        self.console = console_server.VisualConsoleServer(
            ("127.0.0.1", 0), config, sessions=sessions
        )
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.console_thread = threading.Thread(
            target=self.console.serve_forever, daemon=True
        )
        self.upstream_thread.start()
        self.console_thread.start()

    @property
    def console_port(self) -> int:
        return self.console.server_address[1]

    def close(self) -> None:
        self.console.shutdown()
        self.upstream.shutdown()
        self.console.server_close()
        self.upstream.server_close()
        self.console_thread.join(timeout=2)
        self.upstream_thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        request_headers = {"Host": PUBLIC_HOST}
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.console_port, timeout=3
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, response.getheaders(), payload
        finally:
            connection.close()

    def raw_request(self, request: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.console_port), timeout=3) as sock:
            sock.settimeout(3)
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def session_token(self) -> str:
        status, _headers, body = self.request("GET", "/api/console/session")
        if status != 200:
            raise AssertionError(body)
        return json.loads(body)["csrf_token"]

    def post_headers(self, token: str, body: bytes) -> dict[str, str]:
        return {
            "Origin": PUBLIC_ORIGIN,
            "X-Visual-Console-CSRF": token,
            "Content-Type": CONTENT_TYPE,
            "Content-Length": str(len(body)),
        }


class ConfigTests(unittest.TestCase):
    def test_origin_must_be_only_http_scheme_and_authority(self) -> None:
        invalid = (
            "",
            "ftp://example.test",
            "http://user@example.test",
            "http://example.test/path",
            "http://example.test?query=1",
            "http://example.test/",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                console_server.ConsoleConfig.create(
                    bind_host="127.0.0.1",
                    port=8080,
                    upstream=value,
                    public_origin="http://console.test:8080",
                    static_root=CONSOLE_ROOT,
                )

    def test_environment_requires_fixed_upstream_and_public_origin(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            console_server.ConsoleConfig.from_environment(static_root=CONSOLE_ROOT)
        with mock.patch.dict(
            os.environ,
            {
                "VISUAL_CONSOLE_UPSTREAM": "http://127.0.0.1:8000",
                "VISUAL_CONSOLE_PUBLIC_ORIGIN": "http://127.0.0.1:8080",
                "VISUAL_CONSOLE_PORT": "not-a-number",
            },
            clear=True,
        ), self.assertRaises(ValueError):
            console_server.ConsoleConfig.from_environment(static_root=CONSOLE_ROOT)


class SessionStoreTests(unittest.TestCase):
    def test_token_expires_without_refresh(self) -> None:
        clock = MutableClock()
        store = console_server.SessionStore(
            ttl_seconds=10,
            capacity=2,
            clock=clock,
            token_factory=lambda _size: "token-one",
        )
        token = store.create()
        self.assertTrue(store.validate(token))
        clock.value += 10
        self.assertFalse(store.validate(token))
        self.assertEqual(len(store), 0)

    def test_capacity_evicts_earliest_expiring_token(self) -> None:
        clock = MutableClock()
        generated = iter(("first", "second", "third"))
        store = console_server.SessionStore(
            ttl_seconds=100,
            capacity=2,
            clock=clock,
            token_factory=lambda _size: next(generated),
        )
        first = store.create()
        clock.value += 1
        second = store.create()
        clock.value += 1
        third = store.create()
        self.assertFalse(store.validate(first))
        self.assertTrue(store.validate(second))
        self.assertTrue(store.validate(third))
        self.assertEqual(len(store), 2)

    def test_non_urlsafe_token_is_rejected_and_restart_has_empty_state(self) -> None:
        store = console_server.SessionStore(token_factory=lambda _size: "safe_token")
        token = store.create()
        self.assertTrue(store.validate(token))
        self.assertFalse(store.validate("令牌"))
        self.assertFalse(console_server.SessionStore().validate(token))


class VisualConsoleHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.running = RunningServers()

    def tearDown(self) -> None:
        self.running.close()

    @staticmethod
    def header_map(headers: list[tuple[str, str]]) -> dict[str, str]:
        return {name.lower(): value for name, value in headers}

    def assert_security_headers(self, headers: list[tuple[str, str]]) -> None:
        mapped = self.header_map(headers)
        self.assertEqual(mapped["cache-control"], "no-store")
        self.assertIn("default-src 'none'", mapped["content-security-policy"])
        self.assertEqual(mapped["x-content-type-options"], "nosniff")
        self.assertEqual(mapped["x-frame-options"], "DENY")
        self.assertEqual(mapped["referrer-policy"], "no-referrer")
        self.assertEqual(mapped["cross-origin-resource-policy"], "same-origin")
        # The approved LAN origin is plain HTTP, where browsers ignore COOP
        # and emit a console error. CSP/frame protections remain active.
        self.assertNotIn("cross-origin-opener-policy", mapped)
        self.assertNotIn("access-control-allow-origin", mapped)

    def test_static_whitelist_and_security_headers(self) -> None:
        expected_types = {
            "/": "text/html; charset=utf-8",
            "/index.html": "text/html; charset=utf-8",
            "/styles.css": "text/css; charset=utf-8",
            "/core.js": "text/javascript; charset=utf-8",
            "/app.js": "text/javascript; charset=utf-8",
        }
        for path, content_type in expected_types.items():
            with self.subTest(path=path):
                status, headers, body = self.running.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(self.header_map(headers)["content-type"], content_type)
                self.assert_security_headers(headers)

        status, headers, body = self.running.request("GET", "/favicon.ico")
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(self.header_map(headers)["content-type"], "image/x-icon")
        self.assert_security_headers(headers)

    def test_unknown_traversal_and_query_targets_are_blocked(self) -> None:
        cases = (
            ("/secret.txt", 404),
            ("/../server.py", 404),
            ("/%2e%2e/server.py", 404),
            ("/index.html?debug=1", 400),
            ("/api/console/ready?", 400),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                status, headers, _body = self.running.request("GET", path)
                self.assertEqual(status, expected)
                self.assert_security_headers(headers)

    def test_method_whitelist_rejects_head_options_put_and_delete(self) -> None:
        for method in ("HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, headers, _body = self.running.request(method, "/index.html")
                self.assertEqual(status, 405)
                self.assert_security_headers(headers)

    def test_public_host_is_required_except_loopback_liveness(self) -> None:
        status, _headers, _body = self.running.request(
            "GET", "/index.html", headers={"Host": "127.0.0.1"}
        )
        self.assertEqual(status, 421)

        internal_host = f"127.0.0.1:{self.running.console_port}"
        status, _headers, body = self.running.request(
            "GET", "/health/live", headers={"Host": internal_host}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "live"})

        status, _headers, _body = self.running.request(
            "GET", "/api/console/session", headers={"Host": internal_host}
        )
        self.assertEqual(status, 421)

    def test_session_has_no_cookie_and_returns_expiring_csrf_token(self) -> None:
        status, headers, body = self.running.request("GET", "/api/console/session")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertGreaterEqual(len(payload["csrf_token"]), 40)
        self.assertEqual(payload["expires_in_seconds"], 900)
        mapped = self.header_map(headers)
        self.assertNotIn("set-cookie", mapped)
        self.assert_security_headers(headers)

    def test_readiness_is_cropped_and_preserves_unready_status(self) -> None:
        self.running.upstream_state.ready_status = 503
        self.running.upstream_state.ready_body = json.dumps(
            {
                "status": "degraded",
                "ready": False,
                "models": [
                    {
                        "name": "one",
                        "state": "ready",
                        "path": "/secret/model.onnx",
                        "sha256": "secret",
                    },
                    {"name": "two", "state": "missing", "error": "secret"},
                ],
                "capabilities": {
                    "portrait": "ready",
                    "image_verification": "unknown",
                    "video_verification": "unavailable",
                    "witness_blur": "ready",
                    "private_capability": "ready",
                },
                "config_path": "/secret/config.yaml",
            }
        ).encode()
        status, headers, body = self.running.request("GET", "/api/console/ready")
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertEqual(
            set(payload),
            {"status", "ready", "models", "capabilities", "checked_at"},
        )
        self.assertEqual(payload["models"], {"ready": 1, "total": 2})
        self.assertEqual(payload["capabilities"]["portrait"], "ready")
        self.assertEqual(
            payload["capabilities"]["image_verification"], "unavailable"
        )
        self.assertNotIn("secret", body.decode())
        self.assert_security_headers(headers)

    def test_invalid_or_oversized_readiness_becomes_controlled_502(self) -> None:
        self.running.upstream_state.ready_body = b"not-json"
        status, _headers, body = self.running.request("GET", "/api/console/ready")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_READINESS")

        self.running.upstream_state.ready_headers = [
            ("Content-Length", str(console_server.READY_RESPONSE_LIMIT + 1)),
            ("Content-Type", "application/json"),
        ]
        self.running.upstream_state.ready_body = b""
        status, _headers, body = self.running.request("GET", "/api/console/ready")
        self.assertEqual(status, 502)
        self.assertEqual(
            json.loads(body)["error"]["code"], "UPSTREAM_RESPONSE_TOO_LARGE"
        )

    def test_all_five_post_routes_stream_to_the_fixed_upstream(self) -> None:
        token = self.running.session_token()
        body = b"--" + BOUNDARY.encode() + b"--\r\n"
        for path in console_server.POST_ROUTES:
            with self.subTest(path=path):
                status, headers, payload = self.running.request(
                    "POST", path, body=body, headers=self.running.post_headers(token, body)
                )
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(payload)["suggestion"], "REVIEW")
                self.assert_security_headers(headers)
        recorded = self.running.upstream_state.requests
        post_requests = [item for item in recorded if item["method"] == "POST"]
        self.assertEqual([item["path"] for item in post_requests], list(console_server.POST_ROUTES))
        self.assertTrue(all(item["body"] == body for item in post_requests))

    def test_proxy_forwards_only_the_frontend_safe_multipart_filename(self) -> None:
        token = self.running.session_token()
        safe_filename = "visual-console-image.jpg"
        body = multipart_file_body(
            field="image",
            filename=safe_filename,
            content_type="image/jpeg",
            payload=b"synthetic-image-bytes",
        )

        status, _headers, _payload = self.running.request(
            "POST",
            "/api/v2/portrait/generate",
            body=body,
            headers=self.running.post_headers(token, body),
        )

        self.assertEqual(status, 200)
        upstream_body = self.running.upstream_state.requests[-1]["body"]
        self.assertEqual(
            re.findall(br'filename="([^"]+)"', upstream_body),
            [safe_filename.encode()],
        )
        self.assertEqual(upstream_body, body)

    def test_post_requires_exact_origin_and_valid_csrf(self) -> None:
        token = self.running.session_token()
        body = b"x"
        base = self.running.post_headers(token, body)
        cases = (
            ({key: value for key, value in base.items() if key != "Origin"}, 400),
            ({**base, "Origin": "http://attacker.test"}, 403),
            ({**base, "X-Visual-Console-CSRF": "invalid"}, 403),
        )
        for headers, expected in cases:
            with self.subTest(expected=expected):
                status, _response_headers, _payload = self.running.request(
                    "POST", "/api/v2/portrait/generate", body=body, headers=headers
                )
                self.assertEqual(status, expected)
        self.assertFalse(
            any(
                request["method"] == "POST"
                for request in self.running.upstream_state.requests
            )
        )

    def test_post_rejects_query_and_duplicate_security_headers(self) -> None:
        token = self.running.session_token()
        common = (
            "POST {target} HTTP/1.1\r\n"
            f"Host: {PUBLIC_HOST}\r\n"
            f"Origin: {PUBLIC_ORIGIN}\r\n"
            f"X-Visual-Console-CSRF: {token}\r\n"
            f"Content-Type: {CONTENT_TYPE}\r\n"
            "Content-Length: 1\r\n"
            "Connection: close\r\n\r\nx"
        )
        duplicate_cases = (
            common.replace(
                f"Host: {PUBLIC_HOST}\r\n",
                f"Host: {PUBLIC_HOST}\r\nHost: {PUBLIC_HOST}\r\n",
            ),
            common.replace(
                f"Origin: {PUBLIC_ORIGIN}\r\n",
                f"Origin: {PUBLIC_ORIGIN}\r\nOrigin: {PUBLIC_ORIGIN}\r\n",
            ),
            common.replace(
                f"X-Visual-Console-CSRF: {token}\r\n",
                f"X-Visual-Console-CSRF: {token}\r\n"
                f"X-Visual-Console-CSRF: {token}\r\n",
            ),
        )
        for raw in duplicate_cases:
            with self.subTest(raw=raw[:40]):
                response = self.running.raw_request(
                    raw.format(target="/api/v2/portrait/generate").encode()
                )
                self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        response = self.running.raw_request(
            common.format(target="/api/v2/portrait/generate?mode=debug").encode()
        )
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])

    def test_proxy_strips_browser_credentials_and_forwarding_headers(self) -> None:
        token = self.running.session_token()
        body = b"proxy-body"
        headers = self.running.post_headers(token, body)
        headers.update(
            {
                "Authorization": "Bearer browser-secret",
                "Cookie": "session=browser-secret",
                "X-Internal-Token": "internal-secret",
                "X-Forwarded-For": "203.0.113.9",
                "Forwarded": "for=203.0.113.9",
            }
        )
        status, _headers, _payload = self.running.request(
            "POST", "/api/v2/portrait/generate", body=body, headers=headers
        )
        self.assertEqual(status, 200)
        upstream_headers = {
            name.lower(): value
            for name, value in self.running.upstream_state.requests[-1]["headers"]
        }
        self.assertEqual(
            set(upstream_headers),
            {"host", "content-type", "content-length", "connection"},
        )
        self.assertEqual(upstream_headers["content-type"], CONTENT_TYPE)
        self.assertEqual(upstream_headers["connection"], "close")

    def test_proxy_strips_upstream_sensitive_and_hop_headers(self) -> None:
        def responder(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            payload = b'{"ok":true}'
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.send_header("Set-Cookie", "secret=value")
            handler.send_header("WWW-Authenticate", "Bearer secret")
            handler.send_header("X-Internal-Token", "secret")
            handler.send_header("X-Upstream-Trace", "secret")
            handler.end_headers()
            handler.wfile.write(payload)

        self.running.upstream_state.post_responder = responder
        token = self.running.session_token()
        body = b"x"
        status, headers, payload = self.running.request(
            "POST",
            "/api/v2/portrait/generate",
            body=body,
            headers=self.running.post_headers(token, body),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, b'{"ok":true}')
        mapped = self.header_map(headers)
        for name in (
            "set-cookie",
            "www-authenticate",
            "x-internal-token",
            "x-upstream-trace",
            "transfer-encoding",
        ):
            self.assertNotIn(name, mapped)

    def test_ambiguous_upstream_response_headers_fail_closed(self) -> None:
        token = self.running.session_token()
        body = b"x"
        path = "/api/v2/portrait/generate"

        def duplicate_length(
            handler: http.server.BaseHTTPRequestHandler, _body: bytes
        ) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", "2")
            handler.send_header("Content-Length", "2")
            handler.end_headers()
            handler.wfile.write(b"{}")

        def nominated_length(
            handler: http.server.BaseHTTPRequestHandler, _body: bytes
        ) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", "2")
            handler.send_header("Connection", "Content-Length")
            handler.end_headers()
            handler.wfile.write(b"{}")

        for responder in (duplicate_length, nominated_length):
            with self.subTest(responder=responder.__name__):
                self.running.upstream_state.post_responder = responder
                status, _headers, payload = self.running.request(
                    "POST",
                    path,
                    body=body,
                    headers=self.running.post_headers(token, body),
                )
                self.assertEqual(status, 502)
                self.assertEqual(
                    json.loads(payload)["error"]["code"],
                    "INVALID_UPSTREAM_RESPONSE",
                )

    def test_request_length_and_multipart_header_fail_closed_before_body(self) -> None:
        token = self.running.session_token()
        common = (
            f"Host: {PUBLIC_HOST}\r\n"
            f"Origin: {PUBLIC_ORIGIN}\r\n"
            f"X-Visual-Console-CSRF: {token}\r\n"
            f"Content-Type: {CONTENT_TYPE}\r\n"
        )
        requests = {
            "missing": common,
            "duplicate": common + "Content-Length: 1\r\nContent-Length: 1\r\n",
            "conflict": common + "Content-Length: 1\r\nContent-Length: 2\r\n",
            "negative": common + "Content-Length: -1\r\n",
            "comma": common + "Content-Length: 1, 1\r\n",
            "too-large": common
            + f"Content-Length: {console_server.SINGLE_IMAGE_BODY_LIMIT + 1}\r\n",
        }
        expected = {
            "missing": 400,
            "duplicate": 400,
            "conflict": 400,
            "negative": 400,
            "comma": 400,
            "too-large": 413,
        }
        for name, header_block in requests.items():
            with self.subTest(case=name):
                request = (
                    "POST /api/v2/portrait/generate HTTP/1.1\r\n"
                    + header_block
                    + "Connection: close\r\n\r\n"
                ).encode()
                response = self.running.raw_request(request)
                status = int(response.split(b"\r\n", 1)[0].split()[1])
                self.assertEqual(status, expected[name])

    def test_transfer_expect_boundary_and_hop_headers_are_rejected(self) -> None:
        token = self.running.session_token()
        base = (
            "POST /api/v2/portrait/generate HTTP/1.1\r\n"
            f"Host: {PUBLIC_HOST}\r\n"
            f"Origin: {PUBLIC_ORIGIN}\r\n"
            f"X-Visual-Console-CSRF: {token}\r\n"
        )
        cases = {
            "transfer": (
                f"Content-Type: {CONTENT_TYPE}\r\n"
                "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
                400,
            ),
            "expect": (
                f"Content-Type: {CONTENT_TYPE}\r\n"
                "Content-Length: 1\r\nExpect: 100-continue\r\n\r\nx",
                417,
            ),
            "boundary-space": (
                "Content-Type: multipart/form-data; boundary=bad boundary\r\n"
                "Content-Length: 1\r\n\r\nx",
                400,
            ),
            "duplicate-boundary": (
                "Content-Type: multipart/form-data; boundary=one; boundary=two\r\n"
                "Content-Length: 1\r\n\r\nx",
                400,
            ),
            "hop-combination": (
                f"Content-Type: {CONTENT_TYPE}\r\n"
                "Content-Length: 1\r\nConnection: keep-alive, X-Smuggle\r\n"
                "X-Smuggle: yes\r\n\r\nx",
                400,
            ),
        }
        for name, (tail, expected) in cases.items():
            with self.subTest(case=name):
                response = self.running.raw_request((base + tail).encode())
                status = int(response.split(b"\r\n", 1)[0].split()[1])
                self.assertEqual(status, expected)

    def test_body_limit_is_route_specific(self) -> None:
        token = self.running.session_token()
        declared = console_server.TWO_IMAGE_BODY_LIMIT + 1
        raw = (
            "POST /api/v2/verify/images HTTP/1.1\r\n"
            f"Host: {PUBLIC_HOST}\r\n"
            f"Origin: {PUBLIC_ORIGIN}\r\n"
            f"X-Visual-Console-CSRF: {token}\r\n"
            f"Content-Type: {CONTENT_TYPE}\r\n"
            f"Content-Length: {declared}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        response = self.running.raw_request(raw)
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])

    def test_declared_or_streamed_oversized_upstream_response_is_bounded(self) -> None:
        token = self.running.session_token()
        body = b"x"
        path = "/api/v2/verify/images"

        def declared(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header(
                "Content-Length", str(console_server.IMAGE_VERIFY_RESPONSE_LIMIT + 1)
            )
            handler.end_headers()

        self.running.upstream_state.post_responder = declared
        status, _headers, payload = self.running.request(
            "POST", path, body=body, headers=self.running.post_headers(token, body)
        )
        self.assertEqual(status, 502)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "UPSTREAM_RESPONSE_TOO_LARGE"
        )

        def no_length(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(b"01234567890")
            handler.close_connection = True

        self.running.upstream_state.post_responder = no_length
        small_policy = console_server.RoutePolicy(100, 10, 5)
        with mock.patch.dict(console_server.POST_ROUTES, {path: small_policy}):
            status, _headers, payload = self.running.request(
                "POST", path, body=body, headers=self.running.post_headers(token, body)
            )
        self.assertEqual(status, 502)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "UPSTREAM_RESPONSE_TOO_LARGE"
        )

    def test_single_post_slot_returns_429_without_blocking_gets(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocked(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            entered.set()
            release.wait(timeout=3)
            UpstreamState.default_post_responder(handler, _body)

        self.running.upstream_state.post_responder = blocked
        token = self.running.session_token()
        body = b"x"
        first_result: list[int] = []

        def first_request() -> None:
            status, _headers, _payload = self.running.request(
                "POST",
                "/api/v2/portrait/generate",
                body=body,
                headers=self.running.post_headers(token, body),
            )
            first_result.append(status)

        thread = threading.Thread(target=first_request)
        thread.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            status, _headers, payload = self.running.request(
                "POST",
                "/api/v2/verify/images",
                body=body,
                headers=self.running.post_headers(token, body),
            )
            self.assertEqual(status, 429)
            self.assertEqual(json.loads(payload)["error"]["code"], "CONSOLE_BUSY")
            get_status, _get_headers, _get_body = self.running.request(
                "GET", "/health/live"
            )
            self.assertEqual(get_status, 200)
        finally:
            release.set()
            thread.join(timeout=3)
        self.assertEqual(first_result, [200])

    def test_slot_is_released_after_upstream_failure(self) -> None:
        def close_without_response(
            handler: http.server.BaseHTTPRequestHandler, _body: bytes
        ) -> None:
            handler.connection.shutdown(socket.SHUT_RDWR)
            handler.connection.close()

        self.running.upstream_state.post_responder = close_without_response
        token = self.running.session_token()
        body = b"x"
        status, _headers, _payload = self.running.request(
            "POST",
            "/api/v2/portrait/generate",
            body=body,
            headers=self.running.post_headers(token, body),
        )
        self.assertEqual(status, 502)

        self.running.upstream_state.post_responder = UpstreamState.default_post_responder
        status, _headers, _payload = self.running.request(
            "POST",
            "/api/v2/portrait/generate",
            body=body,
            headers=self.running.post_headers(token, body),
        )
        self.assertEqual(status, 200)

    def test_total_deadline_returns_504_and_releases_slot(self) -> None:
        def slow_header(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            time.sleep(0.25)
            UpstreamState.default_post_responder(handler, _body)

        self.running.upstream_state.post_responder = slow_header
        token = self.running.session_token()
        body = b"x"
        path = "/api/v2/portrait/generate"
        short_policy = console_server.RoutePolicy(100, 100, 0.1)
        with mock.patch.dict(console_server.POST_ROUTES, {path: short_policy}):
            status, _headers, payload = self.running.request(
                "POST", path, body=body, headers=self.running.post_headers(token, body)
            )
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(payload)["error"]["code"], "UPSTREAM_TIMEOUT")

        self.running.upstream_state.post_responder = UpstreamState.default_post_responder
        status, _headers, _payload = self.running.request(
            "POST", path, body=body, headers=self.running.post_headers(token, body)
        )
        self.assertEqual(status, 200)

    def test_client_disconnect_keeps_slot_until_upstream_is_drained(self) -> None:
        first_chunk_sent = threading.Event()
        release = threading.Event()

        def streamed(handler: http.server.BaseHTTPRequestHandler, _body: bytes) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(b'{"part":')
            handler.wfile.flush()
            first_chunk_sent.set()
            release.wait(timeout=3)
            handler.wfile.write(b'"done"}')
            handler.close_connection = True

        self.running.upstream_state.post_responder = streamed
        token = self.running.session_token()
        body = b"x"
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.running.console_port, timeout=3
        )
        connection.request(
            "POST",
            "/api/v2/portrait/generate",
            body=body,
            headers={"Host": PUBLIC_HOST, **self.running.post_headers(token, body)},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(first_chunk_sent.wait(timeout=2))
        connection.close()
        try:
            status, _headers, _payload = self.running.request(
                "POST",
                "/api/v2/verify/images",
                body=body,
                headers=self.running.post_headers(token, body),
            )
            self.assertEqual(status, 429)
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            acquired = self.running.console.post_slot.acquire(blocking=False)
            if acquired:
                self.running.console.post_slot.release()
                break
            time.sleep(0.01)
        else:
            self.fail("POST slot was not released after draining the upstream")


if __name__ == "__main__":
    unittest.main()
