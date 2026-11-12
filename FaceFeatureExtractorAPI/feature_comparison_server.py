"""Add vector comparison and search to the feature service (and to a deployed 1.1.0 image).

Dockerfile.compare layers this file and its model-free dependencies onto the pinned
1.1.0 image, keeping that image's extractor, models and image routes; the full images
(Dockerfile, Dockerfile.gpu) run it on the current feature_server.
"""

import os
from contextlib import asynccontextmanager

import runtime_env  # noqa: F401  (thread limits must precede numpy/cv2/onnxruntime)
from api.body_limit import FEATURE_COMPARISON_MAX_BYTES, RequestBodyLimitMiddleware
from api.deadline import FEATURE_COMPARISON_DEADLINE_SECONDS, RequestDeadlineMiddleware
from api.feature_comparison import LEGACY_FEATURE_COMPARISON_PATH, create_feature_comparison_router
from api.feature_search import (
    FEATURE_SEARCH_DEADLINE_SECONDS,
    FEATURE_SEARCH_MAX_BYTES,
    FEATURE_SEARCH_PATH,
    create_feature_search_router,
)
from face_algorithm.schemas import ReasonCode
from feature_server import app as original_app, create_app, default_factory
from image_runtime_options import initialize_image_worker


if original_app.version != "1.1.0":
    raise RuntimeError("Comparison overlay requires the original 1.1.0 feature service")
if {LEGACY_FEATURE_COMPARISON_PATH, FEATURE_SEARCH_PATH}.intersection(getattr(route, "path", None) for route in original_app.routes):
    raise RuntimeError("Feature comparison or search is already registered")


def bounded_factory():
    return initialize_image_worker(default_factory)


# A fresh app per module import: a spawned uvicorn worker imports this file both as __mp_main__
# and by name, and each import must get its own routes and middleware.
app = create_app(factory=bounded_factory)
app.add_middleware(
    RequestBodyLimitMiddleware,
    limits={
        LEGACY_FEATURE_COMPARISON_PATH: (FEATURE_COMPARISON_MAX_BYTES, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE),
        FEATURE_SEARCH_PATH: (FEATURE_SEARCH_MAX_BYTES, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE),
    },
)
app.add_middleware(
    RequestDeadlineMiddleware,
    deadlines={
        LEGACY_FEATURE_COMPARISON_PATH: FEATURE_COMPARISON_DEADLINE_SECONDS,
        FEATURE_SEARCH_PATH: FEATURE_SEARCH_DEADLINE_SECONDS,
    },
)
original_lifespan = app.router.lifespan_context
app.include_router(create_feature_comparison_router(path=LEGACY_FEATURE_COMPARISON_PATH))
search_router = create_feature_search_router()
app.include_router(search_router)


@asynccontextmanager
async def lifespan(application):
    # Explicit composition also supports FastAPI versions predating automatic
    # router-lifespan merging. The original image runtime remains unchanged.
    async with original_lifespan(application):
        async with search_router.lifespan_context(application):
            yield


app.router.lifespan_context = lifespan
# 1.2.0 added comparison, 1.3.0 search; 1.4.0 is the multi-version service with v2 extraction.
# An overlay on the 1.1.0 image has no v2 routes and stays 1.3.0.
app.version = "1.4.0" if any(getattr(route, "path", None) == "/api/v2/extract_feature" for route in app.routes) else "1.3.0"
app.openapi_schema = None


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Feature service with vector comparison and search")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=int(os.getenv("FACE_WORKERS", "1")))
    args = parser.parse_args()
    # Each worker is a separate process with its own models, inference slot and search worker.
    if args.workers > 1:
        uvicorn.run("feature_comparison_server:app", host="0.0.0.0", port=args.port, workers=args.workers)
    else:
        uvicorn.run(app, host="0.0.0.0", port=args.port)
