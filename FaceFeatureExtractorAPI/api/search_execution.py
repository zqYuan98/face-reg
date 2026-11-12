"""One spawned, model-free feature-search worker with no admission queue."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from face_algorithm.schemas import ReasonCode

from .errors import ApiError


logger = logging.getLogger("face_algorithm.api.search")


def _warm_worker() -> bool:
    # Import the parsing/numerical closure in the spawned process. No image
    # extractor, model registry, or inference lifespan is initialized here.
    from .feature_search import _parse_and_search

    return callable(_parse_and_search)


def _run_search(body: bytes, deadline: float) -> tuple[int, dict[str, object]]:
    from .feature_search import _parse_and_search

    return _parse_and_search(body, deadline=deadline)


@dataclass(eq=False)
class SearchAdmission:
    submitted: bool = False


class SearchExecution:
    """Reserve before upload; only raw worker completion frees submitted work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: ProcessPoolExecutor | None = None
        self._admission: SearchAdmission | None = None
        self._available = False
        self._started = False
        self._closed = False

    async def start(self) -> None:
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        try:
            pool = ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn"),
            )
            with self._lock:
                self._pool = pool
            warmup = asyncio.wrap_future(pool.submit(_warm_worker))
            warmup.add_done_callback(self._consume_late_exception)
            await asyncio.wait_for(asyncio.shield(warmup), timeout=15.0)
            with self._lock:
                self._available = not self._closed
        except asyncio.CancelledError:
            self.close()
            raise
        except Exception as error:
            # Search can be unavailable without disabling the original image or
            # 1:1 routes. No raw request/validation exception is ever logged.
            logger.error("feature search worker initialization failed: %s", type(error).__name__)
            self.close()

    def reserve(self) -> SearchAdmission:
        with self._lock:
            if self._closed or not self._available or self._pool is None:
                raise ApiError(503, ReasonCode.FEATURE_SEARCH_UNAVAILABLE, "feature search is unavailable")
            if self._admission is not None:
                raise ApiError(429, ReasonCode.FEATURE_SEARCH_BUSY, "feature search is busy")
            admission = SearchAdmission()
            self._admission = admission
            return admission

    def release_unsubmitted(self, admission: SearchAdmission) -> None:
        with self._lock:
            if self._admission is admission and not admission.submitted:
                self._admission = None

    def submit(
        self, admission: SearchAdmission, body: bytes, *, deadline: float,
    ) -> Future[tuple[int, dict[str, object]]]:
        with self._lock:
            if self._closed or not self._available or self._pool is None:
                raise ApiError(503, ReasonCode.FEATURE_SEARCH_UNAVAILABLE, "feature search is unavailable")
            if self._admission is not admission or admission.submitted:
                raise RuntimeError("feature search admission is invalid")
            try:
                future = self._pool.submit(_run_search, body, deadline)
            except (BrokenProcessPool, RuntimeError, OSError):
                self._available = False
                raise ApiError(503, ReasonCode.FEATURE_SEARCH_UNAVAILABLE, "feature search is unavailable") from None
            admission.submitted = True
        # Register outside the lock: add_done_callback may invoke synchronously
        # if the raw future has already completed.
        future.add_done_callback(lambda completed: self._completed(admission, completed))
        return future

    def _completed(self, admission: SearchAdmission, future: Future) -> None:
        broken = not future.cancelled() and isinstance(future.exception(), BrokenProcessPool)
        with self._lock:
            if broken:
                self._available = False
            if self._admission is admission:
                self._admission = None

    @staticmethod
    def _consume_late_exception(future: asyncio.Future) -> None:
        if not future.cancelled():
            future.exception()

    async def wait(
        self, future: Future[tuple[int, dict[str, object]]], *, timeout: float,
    ) -> tuple[int, dict[str, object]]:
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(self._consume_late_exception)
        try:
            # Cancelling the HTTP task or expiring its deadline must NEVER
            # cancel the raw future and release a still-running worker slot.
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=max(0.0, timeout))
        except BrokenProcessPool:
            raise ApiError(503, ReasonCode.FEATURE_SEARCH_UNAVAILABLE, "feature search is unavailable") from None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._available = False
            pool = self._pool
        if pool is not None:
            # Running work retains its completion callback and exits naturally;
            # the pool then closes the child. Never fork a loaded image runtime.
            pool.shutdown(wait=False, cancel_futures=True)
