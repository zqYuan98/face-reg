"""Single-owner execution gate for heavyweight CPU runtimes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from face_algorithm.schemas import ReasonCode

from .errors import ApiError


T = TypeVar("T")
logger = logging.getLogger("face_algorithm.api")


class AlgorithmExecutor:
    """Serialize inference and keep a timed-out worker owned until it drains."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="face-algorithm-owner",
        )
        self._semaphore = asyncio.Semaphore(1)
        self._closed = False

    async def run(
        self,
        operation: Callable[[], T],
        *,
        deadline_monotonic: float,
        on_complete: Callable[[], None] | None = None,
    ) -> T:
        def complete() -> None:
            if on_complete is None:
                return
            try:
                on_complete()
            except Exception as error:
                logger.error(
                    "algorithm completion cleanup failed: %s",
                    type(error).__name__,
                )

        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            complete()
            raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "algorithm deadline exceeded")
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=remaining)
        except TimeoutError:
            complete()
            raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "algorithm deadline exceeded") from None
        except BaseException:
            complete()
            raise
        if self._closed:
            self._semaphore.release()
            complete()
            raise ApiError(503, ReasonCode.MODEL_UNAVAILABLE, "algorithm service is stopping")

        loop = asyncio.get_running_loop()

        def owned_operation() -> T:
            try:
                return operation()
            finally:
                complete()

        try:
            future = loop.run_in_executor(self._executor, owned_operation)
        except Exception:
            self._semaphore.release()
            complete()
            raise
        def release_owner(done: Future[object]) -> None:
            try:
                done.exception()
            except (asyncio.CancelledError, Exception):
                pass
            loop.call_soon_threadsafe(self._semaphore.release)

        future.add_done_callback(release_owner)
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "algorithm deadline exceeded")
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
        except TimeoutError:
            raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "algorithm deadline exceeded") from None

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
