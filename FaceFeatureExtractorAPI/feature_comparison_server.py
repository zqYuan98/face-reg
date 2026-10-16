"""Add vector comparison to a deployed 1.1.0 feature service without replacing it.

Used only by Dockerfile.compare, which keeps the existing extractor, models,
dependencies, image routes and lifespan from its pinned base image.
"""

import os

import runtime_env  # noqa: F401  (thread limits must precede numpy/cv2/onnxruntime)
from api.body_limit import FEATURE_COMPARISON_MAX_BYTES, RequestBodyLimitMiddleware
from api.deadline import FEATURE_COMPARISON_DEADLINE_SECONDS, RequestDeadlineMiddleware
from api.feature_comparison import LEGACY_FEATURE_COMPARISON_PATH, create_feature_comparison_router
from face_algorithm.schemas import ReasonCode
from feature_server import app


# A spawned worker runs this file as __mp_main__ and then imports it again by name; both share
# one feature_server.app, so install the overlay once per process.
if not getattr(app.state, "feature_comparison_overlay", False):
    if app.version != "1.1.0":
        raise RuntimeError("Comparison overlay requires the original 1.1.0 feature service")
    if LEGACY_FEATURE_COMPARISON_PATH in {route.path for route in app.routes}:
        raise RuntimeError("Feature comparison is already registered")

    app.add_middleware(
        RequestBodyLimitMiddleware,
        limits={LEGACY_FEATURE_COMPARISON_PATH: (
            FEATURE_COMPARISON_MAX_BYTES, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE,
        )},
    )
    app.add_middleware(
        RequestDeadlineMiddleware,
        deadlines={LEGACY_FEATURE_COMPARISON_PATH: FEATURE_COMPARISON_DEADLINE_SECONDS},
    )
    app.include_router(create_feature_comparison_router(path=LEGACY_FEATURE_COMPARISON_PATH))
    app.version = "1.2.0"
    app.openapi_schema = None
    app.state.feature_comparison_overlay = True


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Feature service with vector comparison")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=int(os.getenv("FACE_WORKERS", "1")))
    args = parser.parse_args()
    # Each worker is a separate process with its own models and inference slot.
    if args.workers > 1:
        uvicorn.run("feature_comparison_server:app", host="0.0.0.0", port=args.port, workers=args.workers)
    else:
        uvicorn.run(app, host="0.0.0.0", port=args.port)
