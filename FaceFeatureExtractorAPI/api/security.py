"""Network and token gate used only by the legacy embedding exception."""

from __future__ import annotations

import hashlib
import hmac
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any, Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import ApiError, ApiReasonCode, error_payload


IpAddress = IPv4Address | IPv6Address


def _contains(address: IpAddress, networks: Iterable[object]) -> bool:
    return any(address in network for network in networks)


def resolve_client_address(
    request: Request,
    *,
    trusted_proxy_cidrs: tuple[object, ...],
) -> IpAddress:
    direct_host = request.client.host if request.client is not None else ""
    try:
        direct = ip_address(direct_host)
    except ValueError:
        raise ApiError(403, ApiReasonCode.ACCESS_DENIED, "legacy route access denied") from None
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not _contains(direct, trusted_proxy_cidrs):
        return direct
    try:
        chain = tuple(ip_address(item.strip()) for item in forwarded.split(","))
    except ValueError:
        raise ApiError(403, ApiReasonCode.ACCESS_DENIED, "legacy route access denied") from None
    if not chain:
        raise ApiError(403, ApiReasonCode.ACCESS_DENIED, "legacy route access denied")
    current = direct
    for candidate in reversed(chain):
        if not _contains(current, trusted_proxy_cidrs):
            break
        current = candidate
    return current


def _constant_time_token_matches(provided: str, expected: str) -> bool:
    provided_digest = hashlib.sha256(provided.encode("utf-8", errors="replace")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8", errors="replace")).digest()
    return hmac.compare_digest(provided_digest, expected_digest)


def require_legacy_access(
    request: Request,
    *,
    expected_token: str,
    allowed_client_cidrs: tuple[object, ...],
    trusted_proxy_cidrs: tuple[object, ...],
) -> None:
    provided = request.headers.get("x-internal-token", "")
    token_allowed = _constant_time_token_matches(provided, expected_token)
    try:
        client = resolve_client_address(
            request,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
        )
        network_allowed = _contains(client, allowed_client_cidrs)
    except ApiError:
        network_allowed = False
    if not (token_allowed and network_allowed):
        raise ApiError(403, ApiReasonCode.ACCESS_DENIED, "legacy route access denied")


class LegacyAccessMiddleware:
    """Authenticate the embedding exception before multipart is consumed."""

    def __init__(
        self,
        app: Any,
        *,
        expected_token: str,
        allowed_client_cidrs: tuple[object, ...],
        trusted_proxy_cidrs: tuple[object, ...],
    ) -> None:
        self._app = app
        self._expected_token = expected_token
        self._allowed_client_cidrs = allowed_client_cidrs
        self._trusted_proxy_cidrs = trusted_proxy_cidrs

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/extract_feature"
        ):
            request = Request(scope)
            try:
                require_legacy_access(
                    request,
                    expected_token=self._expected_token,
                    allowed_client_cidrs=self._allowed_client_cidrs,
                    trusted_proxy_cidrs=self._trusted_proxy_cidrs,
                )
            except ApiError as error:
                response = JSONResponse(
                    status_code=error.status_code,
                    content=error_payload(error.reason_code, error.public_message),
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)
