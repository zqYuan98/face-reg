"""Independent, privacy-safe video-presence capability with lazy exports."""

from importlib import import_module

__all__ = [
    "VideoPresencePolicy", "PresenceDecision", "PresenceEvidenceFrame",
    "VideoPresenceResult", "VideoPresenceRuntime", "build_video_presence_runtime",
]


def __getattr__(name: str):
    module = {
        "VideoPresencePolicy": "config",
        "PresenceDecision": "contracts",
        "PresenceEvidenceFrame": "contracts",
        "VideoPresenceResult": "contracts",
        "VideoPresenceRuntime": "runtime",
        "build_video_presence_runtime": "runtime",
    }.get(name)
    if module is None:
        raise AttributeError(name)
    return getattr(import_module(f"{__name__}.{module}"), name)
