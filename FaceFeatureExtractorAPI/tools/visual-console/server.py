"""Hardened, dependency-free static server and face API proxy.

The visual console deliberately knows only the public HTTP contract.  It never
imports the algorithm package, writes uploaded media to disk, or accepts an
upstream address from a browser request.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import email.message
import http.client
import http.server
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, Final


CHUNK_SIZE: Final = 64 * 1024
REQUEST_HEADER_IDLE_SECONDS: Final = 10.0
STREAM_IDLE_SECONDS: Final = 15.0
UPSTREAM_CONNECT_SECONDS: Final = 5.0
READY_TOTAL_SECONDS: Final = 20.0
READY_RESPONSE_LIMIT: Final = 1 * 1024 * 1024
STATIC_FILE_LIMIT: Final = 4 * 1024 * 1024
SESSION_TTL_SECONDS: Final = 15 * 60.0
SESSION_CAPACITY: Final = 256

SINGLE_IMAGE_BODY_LIMIT: Final = 15_794_176
TWO_IMAGE_BODY_LIMIT: Final = 31_522_816
IMAGE_VIDEO_BODY_LIMIT: Final = 540_082_176
DERIVED_IMAGE_RESPONSE_LIMIT: Final = 22 * 1024 * 1024
IMAGE_VERIFY_RESPONSE_LIMIT: Final = 2 * 1024 * 1024
VIDEO_VERIFY_RESPONSE_LIMIT: Final = 4 * 1024 * 1024

CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; "
    "font-src 'none'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'; worker-src 'none'; "
    "manifest-src 'none'"
)
PERMISSIONS_POLICY: Final = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), "
    "picture-in-picture=(), publickey-credentials-get=(), "
    "screen-wake-lock=(), usb=()"
)
SECURITY_HEADERS: Final = (
    ("Cache-Control", "no-store"),
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)

STATIC_FILES: Final = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/core.js": ("core.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

_BOUNDARY_RE = re.compile(r"^[0-9A-Za-z'()+_,./:=?-]{1,70}$")
_TOKEN_RE = re.compile(r"^[0-9A-Za-z_-]{1,256}$")
_CONTENT_TYPE_RE = re.compile(
    r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+"
    r"(?:\s*;\s*[!#$%&'*+.^_`|~0-9A-Za-z-]+=(?:[^\x00-\x20\x7f()<>@,;:\\"
    r"/\[\]?={}]+|\"[^\x00\r\n\"]*\"))*$"
)


@dataclasses.dataclass(frozen=True, slots=True)
class RoutePolicy:
    body_limit: int
    response_limit: int
    total_seconds: float


POST_ROUTES: Final = {
    "/api/v2/portrait/generate": RoutePolicy(
        SINGLE_IMAGE_BODY_LIMIT, DERIVED_IMAGE_RESPONSE_LIMIT, 45.0
    ),
    "/api/v2/witness/blur-background": RoutePolicy(
        SINGLE_IMAGE_BODY_LIMIT, DERIVED_IMAGE_RESPONSE_LIMIT, 45.0
    ),
    "/api/v2/verify/images": RoutePolicy(
        TWO_IMAGE_BODY_LIMIT, IMAGE_VERIFY_RESPONSE_LIMIT, 45.0
    ),
    "/api/v2/verify/video/quick": RoutePolicy(
        IMAGE_VIDEO_BODY_LIMIT, VIDEO_VERIFY_RESPONSE_LIMIT, 75.0
    ),
    "/api/v2/verify/video/full": RoutePolicy(
        IMAGE_VIDEO_BODY_LIMIT, VIDEO_VERIFY_RESPONSE_LIMIT, 620.0
    ),
}


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedOrigin:
    value: str
    scheme: str
    hostname: str
    port: int
    authority: str


def _parse_origin(value: str, variable_name: str) -> ParsedOrigin:
    if not value or value != value.strip():
        raise ValueError(f"{variable_name} must be a non-empty origin")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{variable_name} must use http or https")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{variable_name} must contain only scheme and authority")
    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise ValueError(f"{variable_name} contains an invalid port") from error
    port = parsed_port or (443 if parsed.scheme == "https" else 80)
    return ParsedOrigin(
        value=value,
        scheme=parsed.scheme,
        hostname=parsed.hostname,
        port=port,
        authority=parsed.netloc,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ConsoleConfig:
    bind_host: str
    port: int
    upstream: ParsedOrigin
    public_origin: ParsedOrigin
    static_root: Path

    @classmethod
    def create(
        cls,
        *,
        bind_host: str,
        port: int,
        upstream: str,
        public_origin: str,
        static_root: Path,
    ) -> "ConsoleConfig":
        if (
            not bind_host
            or bind_host != bind_host.strip()
            or any(character in bind_host for character in "/\\\r\n\t")
        ):
            raise ValueError("VISUAL_CONSOLE_HOST is invalid")
        if not 0 <= port <= 65_535:
            raise ValueError("VISUAL_CONSOLE_PORT must be between 0 and 65535")
        return cls(
            bind_host=bind_host,
            port=port,
            upstream=_parse_origin(upstream, "VISUAL_CONSOLE_UPSTREAM"),
            public_origin=_parse_origin(
                public_origin, "VISUAL_CONSOLE_PUBLIC_ORIGIN"
            ),
            static_root=static_root.resolve(),
        )

    @classmethod
    def from_environment(cls, *, static_root: Path | None = None) -> "ConsoleConfig":
        upstream = os.environ.get("VISUAL_CONSOLE_UPSTREAM")
        public_origin = os.environ.get("VISUAL_CONSOLE_PUBLIC_ORIGIN")
        if upstream is None:
            raise ValueError("VISUAL_CONSOLE_UPSTREAM is required")
        if public_origin is None:
            raise ValueError("VISUAL_CONSOLE_PUBLIC_ORIGIN is required")
        raw_port = os.environ.get("VISUAL_CONSOLE_PORT", "8080")
        if not raw_port.isascii() or not raw_port.isdecimal():
            raise ValueError("VISUAL_CONSOLE_PORT must be a decimal integer")
        port = int(raw_port)
        if not 1 <= port <= 65_535:
            raise ValueError("VISUAL_CONSOLE_PORT must be between 1 and 65535")
        return cls.create(
            bind_host=os.environ.get("VISUAL_CONSOLE_HOST", "127.0.0.1"),
            port=port,
            upstream=upstream,
            public_origin=public_origin,
            static_root=static_root or Path(__file__).resolve().parent,
        )


class SessionStore:
    """Small in-memory CSRF token store with a strict lifetime and capacity."""

    def __init__(
        self,
        *,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        capacity: int = SESSION_CAPACITY,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        if ttl_seconds <= 0 or capacity <= 0:
            raise ValueError("session limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._capacity = capacity
        self._clock = clock
        self._token_factory = token_factory
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def _purge_expired(self, now: float) -> None:
        for token, expiry in tuple(self._tokens.items()):
            if expiry <= now:
                del self._tokens[token]

    def create(self) -> str:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            while len(self._tokens) >= self._capacity:
                earliest = min(self._tokens, key=self._tokens.__getitem__)
                del self._tokens[earliest]
            for _attempt in range(8):
                token = self._token_factory(32)
                if token and token not in self._tokens:
                    self._tokens[token] = now + self._ttl_seconds
                    return token
            raise RuntimeError("could not allocate a unique session token")

    def validate(self, candidate: str) -> bool:
        if not isinstance(candidate, str) or not _TOKEN_RE.fullmatch(candidate):
            return False
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            return any(
                secrets.compare_digest(candidate, token) for token in self._tokens
            )

    def __len__(self) -> int:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            return len(self._tokens)


class ClientRequestError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.public_message = message


class UpstreamError(Exception):
    def __init__(self, code: str, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.timeout = timeout


class ResponseLimitExceeded(UpstreamError):
    pass


class VisualConsoleServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: ConsoleConfig,
        *,
        sessions: SessionStore | None = None,
    ) -> None:
        self.config = config
        self.sessions = sessions or SessionStore()
        self.post_slot = threading.BoundedSemaphore(value=1)
        super().__init__(server_address, VisualConsoleHandler)


class VisualConsoleHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VisualConsole"
    sys_version = ""
    server: VisualConsoleServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_HEADER_IDLE_SECONDS)
        self._request_started_at = time.monotonic()
        self._response_committed = False

    def handle_one_request(self) -> None:
        self._request_started_at = time.monotonic()
        super().handle_one_request()

    def log_message(self, _format: str, *args: object) -> None:
        # Do not risk logging filenames, tokens, traces, or upstream internals.
        del args

    def handle_expect_100(self) -> bool:
        self._send_error_json(
            417,
            "EXPECT_NOT_ALLOWED",
            "Expect requests are not accepted",
        )
        return False

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self._send_error_json(code, "INVALID_HTTP_REQUEST", "invalid HTTP request")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        try:
            path = self._validated_target()
            self._validate_host(allow_loopback_health=path == "/health/live")
            if path == "/favicon.ico":
                self._send_bytes(204, b"", content_type="image/x-icon")
                return
            if path in STATIC_FILES:
                self._serve_static(path)
                return
            if path == "/health/live":
                self._send_json(200, {"status": "live"})
                return
            if path == "/api/console/session":
                token = self.server.sessions.create()
                self._send_json(
                    200,
                    {
                        "csrf_token": token,
                        "expires_in_seconds": int(self.server.sessions.ttl_seconds),
                    },
                )
                return
            if path == "/api/console/ready":
                self._serve_readiness()
                return
            raise ClientRequestError(404, "NOT_FOUND", "resource not found")
        except ClientRequestError as error:
            self._send_client_error(error)
        except Exception:
            self._send_error_json(500, "CONSOLE_ERROR", "console request failed")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        acquired = False
        try:
            path = self._validated_target()
            policy = POST_ROUTES.get(path)
            if policy is None:
                raise ClientRequestError(404, "NOT_FOUND", "resource not found")
            self._validate_host()
            self._validate_post_origin()
            content_type, content_length = self._validate_post_headers(policy)
            self._validate_csrf()
            acquired = self.server.post_slot.acquire(blocking=False)
            if not acquired:
                raise ClientRequestError(
                    429,
                    "CONSOLE_BUSY",
                    "another algorithm request is still running",
                )
            self._proxy_post(
                path=path,
                policy=policy,
                content_type=content_type,
                content_length=content_length,
            )
        except ClientRequestError as error:
            self._send_client_error(error)
        except UpstreamError as error:
            if not self._response_committed:
                self._send_error_json(
                    504 if error.timeout else 502,
                    error.code,
                    error.public_message,
                )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            if not self._response_committed:
                self._send_error_json(
                    502,
                    "UPSTREAM_FAILURE",
                    "algorithm service request failed",
                )
        finally:
            if acquired:
                self.server.post_slot.release()

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        self.close_connection = True
        self._send_error_json(405, "METHOD_NOT_ALLOWED", "method not allowed")

    def _validated_target(self) -> str:
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise ClientRequestError(400, "INVALID_TARGET", "invalid request target")
        if "?" in self.path or "#" in self.path:
            raise ClientRequestError(
                400, "QUERY_NOT_ALLOWED", "query parameters are not accepted"
            )
        return self.path

    def _header_values(self, name: str) -> list[str]:
        values = self.headers.get_all(name)
        return list(values) if values else []

    def _one_header(
        self,
        name: str,
        *,
        required: bool = True,
        max_length: int = 1024,
    ) -> str | None:
        values = self._header_values(name)
        if not values:
            if required:
                raise ClientRequestError(
                    400, "MISSING_HEADER", f"{name} header is required"
                )
            return None
        if len(values) != 1:
            raise ClientRequestError(
                400, "DUPLICATE_HEADER", f"duplicate {name} header is not accepted"
            )
        value = values[0].strip()
        if not value or len(value) > max_length or "\r" in value or "\n" in value:
            raise ClientRequestError(400, "INVALID_HEADER", f"invalid {name} header")
        return value

    def _validate_host(self, *, allow_loopback_health: bool = False) -> None:
        host = self._one_header("Host", max_length=512)
        assert host is not None
        if host == self.server.config.public_origin.authority:
            return
        if allow_loopback_health and self._is_loopback_client():
            internal_port = self.server.server_address[1]
            if host in {f"127.0.0.1:{internal_port}", f"[::1]:{internal_port}"}:
                return
        raise ClientRequestError(421, "INVALID_HOST", "request Host is not allowed")

    def _is_loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _validate_post_origin(self) -> None:
        origin = self._one_header("Origin", max_length=512)
        if origin != self.server.config.public_origin.value:
            raise ClientRequestError(403, "INVALID_ORIGIN", "request Origin is not allowed")

    def _validate_csrf(self) -> None:
        token = self._one_header("X-Visual-Console-CSRF", max_length=256)
        assert token is not None
        if not self.server.sessions.validate(token):
            raise ClientRequestError(403, "INVALID_CSRF", "CSRF token is invalid or expired")

    def _validate_post_headers(self, policy: RoutePolicy) -> tuple[str, int]:
        if self._header_values("Transfer-Encoding"):
            raise ClientRequestError(
                400, "TRANSFER_ENCODING_NOT_ALLOWED", "Transfer-Encoding is not accepted"
            )
        if self._header_values("Expect"):
            raise ClientRequestError(417, "EXPECT_NOT_ALLOWED", "Expect is not accepted")

        forbidden_hop_headers = (
            "Keep-Alive",
            "Proxy-Authenticate",
            "Proxy-Authorization",
            "Proxy-Connection",
            "TE",
            "Trailer",
            "Upgrade",
        )
        if any(self._header_values(name) for name in forbidden_hop_headers):
            raise ClientRequestError(
                400, "HOP_HEADER_NOT_ALLOWED", "hop-by-hop headers are not accepted"
            )
        connection_values = self._header_values("Connection")
        if len(connection_values) > 1:
            raise ClientRequestError(
                400, "DUPLICATE_HEADER", "duplicate Connection header is not accepted"
            )
        if connection_values:
            tokens = {
                token.strip().lower()
                for token in connection_values[0].split(",")
                if token.strip()
            }
            if not tokens or not tokens.issubset({"close", "keep-alive"}):
                raise ClientRequestError(
                    400,
                    "HOP_HEADER_NOT_ALLOWED",
                    "invalid Connection header",
                )

        raw_length = self._one_header("Content-Length", max_length=32)
        assert raw_length is not None
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise ClientRequestError(
                400, "INVALID_CONTENT_LENGTH", "Content-Length must be a decimal integer"
            )
        content_length = int(raw_length)
        if content_length <= 0:
            raise ClientRequestError(
                400, "INVALID_CONTENT_LENGTH", "Content-Length must be positive"
            )
        if content_length > policy.body_limit:
            raise ClientRequestError(
                413, "REQUEST_TOO_LARGE", "multipart request exceeds the route limit"
            )

        raw_content_type = self._one_header("Content-Type", max_length=512)
        assert raw_content_type is not None
        self._validate_multipart_content_type(raw_content_type)
        return raw_content_type, content_length

    @staticmethod
    def _validate_multipart_content_type(raw_content_type: str) -> None:
        message = email.message.Message()
        message["content-type"] = raw_content_type
        if message.get_content_type().lower() != "multipart/form-data":
            raise ClientRequestError(
                400,
                "INVALID_CONTENT_TYPE",
                "Content-Type must be multipart/form-data",
            )
        params = message.get_params(header="content-type", unquote=True) or []
        boundary_values = [
            value for name, value in params[1:] if name.lower() == "boundary"
        ]
        unknown_params = [name for name, _value in params[1:] if name.lower() != "boundary"]
        if len(boundary_values) != 1 or unknown_params:
            raise ClientRequestError(
                400, "INVALID_BOUNDARY", "multipart boundary is invalid"
            )
        boundary = boundary_values[0]
        if not isinstance(boundary, str) or not _BOUNDARY_RE.fullmatch(boundary):
            raise ClientRequestError(
                400, "INVALID_BOUNDARY", "multipart boundary is invalid"
            )

    def _serve_static(self, path: str) -> None:
        filename, content_type = STATIC_FILES[path]
        root = self.server.config.static_root
        candidate = (root / filename).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ClientRequestError(404, "NOT_FOUND", "resource not found") from error
        try:
            size = candidate.stat().st_size
            if not candidate.is_file() or size > STATIC_FILE_LIMIT:
                raise OSError
            payload = candidate.read_bytes()
        except OSError as error:
            raise ClientRequestError(404, "NOT_FOUND", "resource not found") from error
        self._send_bytes(200, payload, content_type=content_type)

    def _serve_readiness(self) -> None:
        try:
            status, payload = self._read_upstream_readiness()
        except UpstreamError as error:
            self._send_error_json(
                504 if error.timeout else 502,
                error.code,
                error.public_message,
            )
            return
        self._send_json(status, payload)

    def _read_upstream_readiness(self) -> tuple[int, Mapping[str, object]]:
        deadline = time.monotonic() + READY_TOTAL_SECONDS
        connection = self._new_upstream_connection(deadline)
        response: http.client.HTTPResponse | None = None
        try:
            connection.putrequest(
                "GET", "/health/ready", skip_host=True, skip_accept_encoding=True
            )
            connection.putheader("Host", self.server.config.upstream.authority)
            connection.putheader("Accept", "application/json")
            connection.putheader("Connection", "close")
            connection.endheaders()
            self._set_upstream_timeout(connection, deadline, first_response=True)
            response = connection.getresponse()
            if response.status not in {200, 503}:
                raise UpstreamError(
                    "READINESS_UNAVAILABLE", "algorithm readiness is unavailable"
                )
            declared_length, _content_type = self._validated_response_headers(
                response, READY_RESPONSE_LIMIT
            )
            body = self._read_upstream_body_buffered(
                connection,
                response,
                deadline=deadline,
                limit=READY_RESPONSE_LIMIT,
                declared_length=declared_length,
            )
            try:
                raw = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise UpstreamError(
                    "INVALID_READINESS", "algorithm readiness response is invalid"
                ) from error
            if not isinstance(raw, dict):
                raise UpstreamError(
                    "INVALID_READINESS", "algorithm readiness response is invalid"
                )
            models = raw.get("models")
            model_items = models if isinstance(models, list) else []
            ready_models = sum(
                1
                for item in model_items
                if isinstance(item, dict) and item.get("state") == "ready"
            )
            upstream_capabilities = raw.get("capabilities")
            if not isinstance(upstream_capabilities, dict):
                upstream_capabilities = {}
            capabilities = {
                name: (
                    "ready"
                    if upstream_capabilities.get(name) == "ready"
                    else "unavailable"
                )
                for name in (
                    "portrait",
                    "image_verification",
                    "video_verification",
                    "witness_blur",
                )
            }
            status_value = raw.get("status")
            if status_value not in {"healthy", "degraded", "unhealthy"}:
                status_value = "unhealthy"
            checked_at = dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            return response.status, {
                "status": status_value,
                "ready": raw.get("ready") is True,
                "models": {
                    "ready": ready_models,
                    "total": len(model_items),
                },
                "capabilities": capabilities,
                "checked_at": checked_at,
            }
        except socket.timeout as error:
            raise UpstreamError(
                "READINESS_TIMEOUT", "algorithm readiness timed out", timeout=True
            ) from error
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise UpstreamError(
                "READINESS_UNAVAILABLE", "algorithm readiness is unavailable"
            ) from error
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _proxy_post(
        self,
        *,
        path: str,
        policy: RoutePolicy,
        content_type: str,
        content_length: int,
    ) -> None:
        deadline = self._request_started_at + policy.total_seconds
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection = self._new_upstream_connection(deadline)
            connection.putrequest(
                "POST", path, skip_host=True, skip_accept_encoding=True
            )
            connection.putheader("Host", self.server.config.upstream.authority)
            connection.putheader("Content-Type", content_type)
            connection.putheader("Content-Length", str(content_length))
            connection.putheader("Connection", "close")
            connection.endheaders()
            self._stream_request_body(
                connection, content_length=content_length, deadline=deadline
            )

            # The first response header may take the full route budget.  The
            # 15-second idle rule begins only after the upstream has responded.
            self._set_upstream_timeout(connection, deadline, first_response=True)
            response = connection.getresponse()
            declared_length, response_content_type = self._validated_response_headers(
                response, policy.response_limit
            )
            self._stream_response_body(
                connection,
                response,
                deadline=deadline,
                response_limit=policy.response_limit,
                declared_length=declared_length,
                content_type=response_content_type,
            )
        except ClientRequestError:
            raise
        except ResponseLimitExceeded:
            raise
        except socket.timeout as error:
            raise UpstreamError(
                "UPSTREAM_TIMEOUT", "algorithm service request timed out", timeout=True
            ) from error
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise UpstreamError(
                "UPSTREAM_FAILURE", "algorithm service request failed"
            ) from error
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def _new_upstream_connection(
        self, deadline: float
    ) -> http.client.HTTPConnection:
        remaining = self._remaining(deadline)
        timeout = min(UPSTREAM_CONNECT_SECONDS, remaining)
        upstream = self.server.config.upstream
        if upstream.scheme == "https":
            return http.client.HTTPSConnection(
                upstream.hostname,
                upstream.port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(
            upstream.hostname, upstream.port, timeout=timeout
        )

    def _stream_request_body(
        self,
        connection: http.client.HTTPConnection,
        *,
        content_length: int,
        deadline: float,
    ) -> None:
        remaining_bytes = content_length
        while remaining_bytes:
            self.connection.settimeout(
                min(STREAM_IDLE_SECONDS, self._remaining(deadline))
            )
            read_size = min(CHUNK_SIZE, remaining_bytes)
            reader = self.rfile
            chunk = (
                reader.read1(read_size)
                if hasattr(reader, "read1")
                else reader.read(read_size)
            )
            if not chunk:
                raise ClientRequestError(
                    400, "INCOMPLETE_BODY", "request body ended before Content-Length"
                )
            self._set_upstream_timeout(connection, deadline)
            connection.send(chunk)
            remaining_bytes -= len(chunk)

    def _validated_response_headers(
        self,
        response: http.client.HTTPResponse,
        response_limit: int,
    ) -> tuple[int | None, str | None]:
        if not 200 <= response.status <= 599:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        connection_values = response.headers.get_all("Connection") or []
        if len(connection_values) > 1:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        if connection_values:
            connection_tokens = {
                token.strip().lower()
                for token in connection_values[0].split(",")
                if token.strip()
            }
            if not connection_tokens or not connection_tokens.issubset(
                {"close", "keep-alive"}
            ):
                raise UpstreamError(
                    "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
                )
        length_values = response.headers.get_all("Content-Length") or []
        if len(length_values) > 1:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        transfer_values = response.headers.get_all("Transfer-Encoding") or []
        if length_values and transfer_values:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        if len(transfer_values) > 1 or (
            transfer_values and transfer_values[0].strip().lower() != "chunked"
        ):
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        declared_length: int | None = None
        if length_values:
            raw_length = length_values[0].strip()
            if not raw_length.isascii() or not raw_length.isdecimal():
                raise UpstreamError(
                    "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
                )
            declared_length = int(raw_length)
            if declared_length > response_limit:
                raise ResponseLimitExceeded(
                    "UPSTREAM_RESPONSE_TOO_LARGE",
                    "algorithm service response exceeds the console limit",
                )

        content_type_values = response.headers.get_all("Content-Type") or []
        if len(content_type_values) > 1:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
            )
        content_type: str | None = None
        if content_type_values:
            candidate = content_type_values[0].strip()
            if len(candidate) > 256 or not _CONTENT_TYPE_RE.fullmatch(candidate):
                raise UpstreamError(
                    "INVALID_UPSTREAM_RESPONSE", "algorithm service response is invalid"
                )
            content_type = candidate
        return declared_length, content_type

    def _read_upstream_body_buffered(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        *,
        deadline: float,
        limit: int,
        declared_length: int | None,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            self._set_upstream_timeout(connection, deadline)
            chunk = response.read1(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ResponseLimitExceeded(
                    "UPSTREAM_RESPONSE_TOO_LARGE",
                    "algorithm service response exceeds the console limit",
                )
            chunks.append(chunk)
        if declared_length is not None and total != declared_length:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is incomplete"
            )
        return b"".join(chunks)

    def _stream_response_body(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        *,
        deadline: float,
        response_limit: int,
        declared_length: int | None,
        content_type: str | None,
    ) -> None:
        # Read one bounded chunk before committing the downstream response so a
        # malformed empty response can still become a controlled 502.
        self._set_upstream_timeout(connection, deadline)
        first_chunk = response.read1(CHUNK_SIZE)
        if declared_length not in {None, 0} and not first_chunk:
            raise UpstreamError(
                "INVALID_UPSTREAM_RESPONSE", "algorithm service response is incomplete"
            )
        if len(first_chunk) > response_limit:
            raise ResponseLimitExceeded(
                "UPSTREAM_RESPONSE_TOO_LARGE",
                "algorithm service response exceeds the console limit",
            )

        self.send_response(response.status)
        self._send_security_headers()
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if declared_length is not None:
            self.send_header("Content-Length", str(declared_length))
        self.send_header("Connection", "close")
        self.end_headers()
        self._response_committed = True

        total = 0
        downstream_open = True
        chunk = first_chunk
        while chunk:
            total += len(chunk)
            if total > response_limit:
                break
            if downstream_open:
                try:
                    self.connection.settimeout(
                        min(STREAM_IDLE_SECONDS, self._remaining(deadline))
                    )
                    self.wfile.write(chunk)
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    socket.timeout,
                    OSError,
                ):
                    # Keep the expensive slot until the upstream has completed
                    # or its absolute deadline expires.
                    downstream_open = False
            self._set_upstream_timeout(connection, deadline)
            chunk = response.read1(CHUNK_SIZE)

        if declared_length is not None and total != declared_length:
            return

    def _set_upstream_timeout(
        self,
        connection: http.client.HTTPConnection,
        deadline: float,
        *,
        first_response: bool = False,
    ) -> None:
        remaining = self._remaining(deadline)
        timeout = remaining if first_response else min(STREAM_IDLE_SECONDS, remaining)
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("request deadline exceeded")
        return remaining

    def _send_client_error(self, error: ClientRequestError) -> None:
        if not self._response_committed:
            self._send_error_json(error.status, error.code, error.public_message)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(
            status,
            {
                "success": False,
                "error": {"code": code, "message": message},
            },
        )

    def _send_json(self, status: int, value: Mapping[str, object]) -> None:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._send_bytes(status, payload, content_type="application/json; charset=utf-8")

    def _send_bytes(self, status: int, payload: bytes, *, content_type: str) -> None:
        if self._response_committed:
            return
        try:
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self._response_committed = True
            if payload:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self._response_committed = True

    def _send_security_headers(self) -> None:
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)


def run(config: ConsoleConfig) -> None:
    server = VisualConsoleServer((config.bind_host, config.port), config)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def main() -> None:
    try:
        config = ConsoleConfig.from_environment()
    except ValueError as error:
        raise SystemExit(f"visual console configuration error: {error}") from error
    run(config)


if __name__ == "__main__":
    main()
