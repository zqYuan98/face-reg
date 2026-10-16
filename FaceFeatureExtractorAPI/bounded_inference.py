"""Bounded native inference. Cancellation never releases a running native worker's slot."""
import asyncio
import collections
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

logger = logging.getLogger("FeatureServer")


class BoundedInference:
    def __init__(self, factory, timeout_seconds=12.0, unhealthy_after_seconds=30.0,
                 max_image_bytes=10 * 1024 * 1024, queue_limit=0):
        if not (0 < timeout_seconds <= 300 and 0 < unhealthy_after_seconds <= 600):
            raise ValueError("Invalid inference time limits")
        if not 0 < max_image_bytes <= 20 * 1024 * 1024:
            raise ValueError("Invalid image byte limit")
        if not 0 <= queue_limit <= 256:
            raise ValueError("Invalid queue limit")
        self.factory = factory
        self.timeout_seconds = timeout_seconds
        self.unhealthy_after_seconds = unhealthy_after_seconds
        self.max_image_bytes = max_image_bytes
        # A single model instance is never entered concurrently. Up to queue_limit requests may wait
        # for it within their own deadline; beyond that a busy slot is refused at once.
        self.queue_limit = queue_limit
        self._waiters = collections.deque()  # asyncio futures, touched only on the event loop
        self._loop = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face-inference")
        self._slot = threading.BoundedSemaphore(1)
        self._lock = threading.Lock()
        self._started = None
        self._closed = False
        self._initialization_failed = False
        self.model = None

    def start(self):
        self._reserve()
        self._submit_reserved(self._initialize)

    def _initialize(self):
        try:
            self.model = self.factory()
        except Exception as error:
            self._initialization_failed = True
            logger.error("Model initialization failed: %s", type(error).__name__)
            raise

    def _reserve(self):
        with self._lock:
            if self._closed:
                raise HTTPException(503, detail={"errorCode": "ALGORITHM_UNAVAILABLE"})
            if not self._slot.acquire(blocking=False):
                raise HTTPException(429, detail={"errorCode": "ALGORITHM_BUSY"})
            self._started = time.monotonic()

    def _release(self):
        with self._lock:
            self._started = None
            self._slot.release()
            loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake_one)
            except RuntimeError:
                pass  # the loop closed after the check

    def _wake_one(self):
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return

    async def _admit(self, deadline):
        """Reserves the slot, waiting in line until the deadline when the queue has room."""
        try:
            self._reserve()
            return
        except HTTPException as refused:
            if refused.status_code != 429 or len(self._waiters) >= self.queue_limit:
                raise
        loop = asyncio.get_running_loop()
        self._loop = loop
        waiter = loop.create_future()
        self._waiters.append(waiter)
        admitted = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HTTPException(429, detail={"errorCode": "ALGORITHM_BUSY"})
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), remaining)
                except asyncio.TimeoutError:
                    raise HTTPException(429, detail={"errorCode": "ALGORITHM_BUSY"}) from None
                try:
                    self._reserve()
                    admitted = True
                    return
                except HTTPException as refused:
                    if refused.status_code != 429:
                        raise
                # Another request took the slot first; wait again at the head of the line.
                waiter = loop.create_future()
                self._waiters.appendleft(waiter)
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            elif not admitted and waiter.done() and not waiter.cancelled():
                self._wake_one()  # pass on a wake-up this request can no longer use

    def _submit_reserved(self, operation):
        try:
            future = self._pool.submit(operation)
        except BaseException:
            self._release()
            raise
        # concurrent Future finishes only when the worker exits. asyncio cancellation cannot free it.
        future.add_done_callback(lambda completed: self._release())
        return future

    def readiness(self):
        with self._lock:
            duration = None if self._started is None else time.monotonic() - self._started
            ready = (not self._closed and self.model is not None and not self._initialization_failed
                     and (duration is None or duration < self.unhealthy_after_seconds))
            return {"status": "ready" if ready else "not_ready", "busy": duration is not None,
                    "restart_required": duration is not None and duration >= self.unhealthy_after_seconds}

    async def execute(self, upload, operation):
        if not self.readiness()["status"] == "ready":
            raise HTTPException(503, detail={"errorCode": "ALGORITHM_NOT_READY"})
        started = time.monotonic()
        await self._admit(started + self.timeout_seconds)
        reserved = True
        wrapped = None
        try:
            # Keep admission while reading; decoded images and inference inputs are bounded to one.
            remaining = max(0.001, self.timeout_seconds - (time.monotonic() - started))
            contents = await asyncio.wait_for(upload.read(self.max_image_bytes + 1), remaining)
            if not contents:
                raise HTTPException(400, detail={"errorCode": "INVALID_IMAGE"})
            if len(contents) > self.max_image_bytes:
                raise HTTPException(413, detail={"errorCode": "IMAGE_TOO_LARGE"})
            reserved = False
            future = self._submit_reserved(lambda: operation(self.model, contents))
            wrapped = asyncio.wrap_future(future)
            # Consume a late exception after the HTTP caller has gone away without logging inputs.
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            remaining = max(0.0, self.timeout_seconds - (time.monotonic() - started))
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning("Inference request timed out; native slot remains held until completion")
            raise HTTPException(504, detail={"errorCode": "ALGORITHM_TIMEOUT"}) from None
        finally:
            if reserved:
                self._release()
            await upload.close()

    def close(self):
        with self._lock:
            self._closed = True
        # Native calls cannot be killed by cancelling a thread. The process supervisor must restart
        # a permanently stalled process; /ready exposes that condition while /health stays responsive.
        self._pool.shutdown(wait=False, cancel_futures=True)
