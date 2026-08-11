"""Privacy-safe HTTP error contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from face_algorithm.schemas import ReasonCode


class ApiReasonCode(str, Enum):
    ACCESS_DENIED = "ACCESS_DENIED"


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    reason_code: ReasonCode | ApiReasonCode
    public_message: str

    def __str__(self) -> str:
        return self.public_message


def error_payload(
    reason_code: ReasonCode | ApiReasonCode,
    message: str,
) -> dict[str, object]:
    return {
        "success": False,
        "suggestion": "REVIEW",
        "reason_codes": [reason_code.value],
        "message": message,
    }
