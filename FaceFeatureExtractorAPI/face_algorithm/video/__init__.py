"""Bounded signing-video identity consistency algorithms."""

from face_algorithm.video.verifier import (
    VideoVerificationRuntime,
    build_video_verification_runtime,
)

__all__ = ["VideoVerificationRuntime", "build_video_verification_runtime"]
