"""Add vector comparison/search to the original 1.1.0 feature service.

Used only by Dockerfile.compare, which keeps the existing extractor, models,
dependencies, image routes and lifespan from its pinned base image.
"""

from contextlib import asynccontextmanager

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
from feature_server import app


if app.version != "1.1.0":
    raise RuntimeError("Comparison overlay requires the original 1.1.0 feature service")
if {LEGACY_FEATURE_COMPARISON_PATH, FEATURE_SEARCH_PATH}.intersection(route.path for route in app.routes):
    raise RuntimeError("Feature comparison or search is already registered")

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
app.version = "1.3.0"
app.openapi_schema = None


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Feature service with vector comparison and search")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
