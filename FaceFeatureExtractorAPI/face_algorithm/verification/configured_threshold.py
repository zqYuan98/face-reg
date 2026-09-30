"""Image-only trial thresholds, issued and bound to one startup runtime.

The issuance registry rejects copied/forged objects and retains the original
values separately so even an ``object.__setattr__`` mutation fails closed.
This is an internal integrity check, not a security boundary against arbitrary
Python code execution. No signed-calibration or video state is extended here.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from enum import Enum

from face_algorithm.runtime.config import ConfiguredImageVerification


class ConfiguredThresholdStatus(str, Enum):
    CONFIGURED_UNVALIDATED = "CONFIGURED_UNVALIDATED"


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class ConfiguredThresholdState:
    status: ConfiguredThresholdStatus
    threshold: float
    calibration_id: None
    artifact_sha256: None
    pipeline_fingerprint: str
    _owner: object = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "configured threshold state can only be created by the runtime builder"
        )


@dataclass(frozen=True, slots=True)
class _IssuedBinding:
    threshold: float
    pipeline_fingerprint: str
    owner: object


_ISSUANCE_LOCK = threading.Lock()
_ISSUED_STATES: weakref.WeakKeyDictionary[
    ConfiguredThresholdState, _IssuedBinding
] = weakref.WeakKeyDictionary()


def _issue_configured_threshold(
    config: ConfiguredImageVerification,
    *,
    owner: object,
    pipeline_fingerprint: str,
) -> ConfiguredThresholdState:
    # model_copy/model_construct are intentionally unvalidated in Pydantic.
    # Reparse plain values before any unvalidated object can become issued.
    if not isinstance(config, ConfiguredImageVerification):
        raise TypeError("configured threshold requires validated image configuration")
    config = ConfiguredImageVerification.model_validate(config.model_dump(warnings=False))
    if owner is None:
        raise ValueError("configured threshold requires runtime ownership")
    if (
        not isinstance(pipeline_fingerprint, str)
        or len(pipeline_fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in pipeline_fingerprint)
    ):
        raise ValueError("configured threshold requires a valid pipeline fingerprint")
    state = object.__new__(ConfiguredThresholdState)
    for name, value in {
        "status": ConfiguredThresholdStatus.CONFIGURED_UNVALIDATED,
        "threshold": config.threshold,
        "calibration_id": None,
        "artifact_sha256": None,
        "pipeline_fingerprint": pipeline_fingerprint,
        "_owner": owner,
    }.items():
        object.__setattr__(state, name, value)
    with _ISSUANCE_LOCK:
        _ISSUED_STATES[state] = _IssuedBinding(
            threshold=config.threshold,
            pipeline_fingerprint=pipeline_fingerprint,
            owner=owner,
        )
    return state


def _is_issued_configured_threshold(
    state: object,
    *,
    owner: object | None = None,
    pipeline_fingerprint: str | None = None,
) -> bool:
    if type(state) is not ConfiguredThresholdState:
        return False
    with _ISSUANCE_LOCK:
        binding = _ISSUED_STATES.get(state)
    if binding is None:
        return False
    try:
        return bool(
            state.status is ConfiguredThresholdStatus.CONFIGURED_UNVALIDATED
            and type(state.threshold) is float
            and state.threshold == binding.threshold
            and state.calibration_id is None
            and state.artifact_sha256 is None
            and state.pipeline_fingerprint == binding.pipeline_fingerprint
            and state._owner is binding.owner
            and (owner is None or owner is binding.owner)
            and (
                pipeline_fingerprint is None
                or pipeline_fingerprint == binding.pipeline_fingerprint
            )
        )
    except AttributeError:
        return False
