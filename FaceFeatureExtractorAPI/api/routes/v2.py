"""Versioned public routes; biometric embeddings never cross this boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Request, UploadFile

from face_algorithm.runtime.config import AlgorithmConfig

from ..execution import AlgorithmExecutor
from ..deadline import request_deadline
from ..media import read_image_upload, spool_video_upload
from ..serialization import (
    derived_image_payload,
    image_verification_payload,
    video_verification_payload,
    witness_contract_available,
)


def create_v2_router(
    *,
    config: AlgorithmConfig,
    services: Any,
    executor: AlgorithmExecutor,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["algorithm-v2"])
    resources = config.resources
    witness_max_pixels = min(
        resources.max_image_pixels,
        config.witness.max_witness_pixels,
    )

    @router.post("/portrait/generate", operation_id="generate_standard_portrait")
    async def generate_portrait(
        request: Request,
        image: UploadFile = File(...),
    ) -> dict[str, Any]:
        deadline = request_deadline(
            request,
            fallback_seconds=config.route_deadlines.image_seconds,
        )
        decoded = await read_image_upload(
            image,
            max_bytes=resources.max_image_bytes,
            max_pixels=resources.max_image_pixels,
            deadline_monotonic=deadline,
        )
        return await executor.run(
            lambda: derived_image_payload(
                services.generate_portrait(
                    decoded,
                    deadline_monotonic=deadline,
                ),
                kind="portrait",
                max_pixels=resources.max_image_pixels,
                max_encoded_bytes=resources.max_image_bytes,
            ),
            deadline_monotonic=deadline,
        )

    @router.post("/verify/images", operation_id="verify_two_images")
    async def verify_images(
        request: Request,
        reference_image: UploadFile = File(...),
        probe_image: UploadFile = File(...),
    ) -> dict[str, Any]:
        deadline = request_deadline(
            request,
            fallback_seconds=config.route_deadlines.image_seconds,
        )
        reference = await read_image_upload(
            reference_image,
            max_bytes=resources.max_image_bytes,
            max_pixels=resources.max_image_pixels,
            deadline_monotonic=deadline,
        )
        probe = await read_image_upload(
            probe_image,
            max_bytes=resources.max_image_bytes,
            max_pixels=resources.max_image_pixels,
            deadline_monotonic=deadline,
        )
        return await executor.run(
            lambda: image_verification_payload(
                services.verify_images(
                    reference,
                    probe,
                    deadline_monotonic=deadline,
                )
            ),
            deadline_monotonic=deadline,
        )

    async def verify_video(
        *,
        request: Request,
        reference_upload: UploadFile,
        video_upload: UploadFile,
        mode: Literal["quick", "full"],
    ) -> dict[str, Any]:
        timeout = (
            config.route_deadlines.video_quick_seconds
            if mode == "quick"
            else config.route_deadlines.video_full_seconds
        )
        deadline = request_deadline(request, fallback_seconds=timeout)
        reference = await read_image_upload(
            reference_upload,
            max_bytes=resources.max_image_bytes,
            max_pixels=resources.max_image_pixels,
            deadline_monotonic=deadline,
        )
        async with spool_video_upload(
            video_upload,
            max_bytes=resources.max_video_bytes,
            deadline_monotonic=deadline,
        ) as spooled_video:
            cleanup = spooled_video.transfer_cleanup()
            return await executor.run(
                lambda: video_verification_payload(
                    services.verify_video(
                        reference,
                        Path(spooled_video.path),
                        mode=mode,
                        deadline_monotonic=deadline,
                    )
                ),
                deadline_monotonic=deadline,
                on_complete=cleanup,
            )

    @router.post("/verify/video/quick", operation_id="quick_verify_video")
    async def quick_verify_video(
        request: Request,
        reference_image: UploadFile = File(...),
        video: UploadFile = File(...),
    ) -> dict[str, Any]:
        return await verify_video(
            request=request,
            reference_upload=reference_image,
            video_upload=video,
            mode="quick",
        )

    @router.post("/verify/video/full", operation_id="full_verify_video")
    async def full_verify_video(
        request: Request,
        reference_image: UploadFile = File(...),
        video: UploadFile = File(...),
    ) -> dict[str, Any]:
        return await verify_video(
            request=request,
            reference_upload=reference_image,
            video_upload=video,
            mode="full",
        )

    witness_operation = getattr(services, "blur_witness_background", None)
    if callable(witness_operation) and witness_contract_available():

        @router.post(
            "/witness/blur-background",
            operation_id="blur_witness_background",
        )
        async def blur_witness_background(
            request: Request,
            image: UploadFile = File(...),
        ) -> dict[str, Any]:
            deadline = request_deadline(
                request,
                fallback_seconds=config.route_deadlines.image_seconds,
            )
            decoded = await read_image_upload(
                image,
                max_bytes=resources.max_image_bytes,
                max_pixels=witness_max_pixels,
                deadline_monotonic=deadline,
            )
            return await executor.run(
                lambda: derived_image_payload(
                    witness_operation(
                        decoded,
                        deadline_monotonic=deadline,
                    ),
                    kind="witness",
                    max_pixels=witness_max_pixels,
                    max_encoded_bytes=resources.max_image_bytes,
                ),
                deadline_monotonic=deadline,
            )

    return router
