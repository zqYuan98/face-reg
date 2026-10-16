"""Concurrency fault tests use synthetic models; they do not validate ONNX recognition accuracy."""
import asyncio
import io
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bounded_inference import BoundedInference
from feature_server import create_app


def upload(contents=b"synthetic"):
    return UploadFile(filename="synthetic.jpg", file=io.BytesIO(contents))


async def ready(runtime):
    for _ in range(100):
        if runtime.readiness()["status"] == "ready":
            return
        await asyncio.sleep(0.005)
    raise AssertionError("synthetic model did not initialize")


def test_timed_out_native_call_retains_slot_until_actual_completion():
    async def scenario():
        runtime = BoundedInference(object, timeout_seconds=0.04, unhealthy_after_seconds=1)
        release, entered = threading.Event(), threading.Event()
        runtime.start()
        await ready(runtime)
        def blocked(model, contents):
            entered.set()
            release.wait(3)
            return "finished"
        try:
            with pytest.raises(HTTPException) as failure:
                await runtime.execute(upload(), blocked)
            assert failure.value.status_code == 504
            assert entered.is_set()
            assert runtime.readiness()["busy"]
            with pytest.raises(HTTPException) as busy:
                await runtime.execute(upload(), blocked)
            assert busy.value.status_code == 429
            release.set()
            for _ in range(100):
                if not runtime.readiness()["busy"]:
                    break
                await asyncio.sleep(0.005)
            assert await runtime.execute(upload(), lambda model, data: "next") == "next"
        finally:
            release.set()
            runtime.close()
    asyncio.run(scenario())


def test_client_cancel_does_not_release_native_slot():
    async def scenario():
        runtime = BoundedInference(object, timeout_seconds=2)
        release, entered = threading.Event(), threading.Event()
        runtime.start()
        await ready(runtime)
        def blocked(model, contents):
            entered.set()
            release.wait(3)
        try:
            request = asyncio.create_task(runtime.execute(upload(), blocked))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert runtime.readiness()["busy"]
            with pytest.raises(HTTPException) as busy:
                await runtime.execute(upload(), blocked)
            assert busy.value.status_code == 429
        finally:
            release.set()
            runtime.close()
    asyncio.run(scenario())


def test_health_responsive_and_readiness_degrades_during_hung_inference():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        class Model:
            def extract_features(self, *args, **kwargs):
                entered.set()
                release.wait(3)
                return SimpleNamespace(success=False, faces=[], processing_time=0.1)
        app = create_app(Model, lambda data: data, timeout_seconds=0.04, unhealthy_after_seconds=0.06)
        try:
            async with app.router.lifespan_context(app):
                await ready(app.state.inference)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post("/api/extract_feature", files={"image": ("x.jpg", b"image")})
                    assert response.status_code == 504
                    assert entered.is_set()
                    await asyncio.sleep(0.03)
                    started = time.monotonic()
                    health = await asyncio.wait_for(client.get("/health"), timeout=0.2)
                    assert health.status_code == 200
                    assert time.monotonic() - started < 0.2
                    state = await client.get("/ready")
                    assert state.status_code == 503
                    assert state.json()["restart_required"]
        finally:
            release.set()
    asyncio.run(scenario())


def test_invalid_image_and_oversized_upload_are_real_http_errors_and_free_slot():
    async def scenario():
        def invalid(data):
            raise HTTPException(400, detail={"errorCode": "INVALID_IMAGE"})
        app = create_app(object, invalid, max_image_bytes=8)
        async with app.router.lifespan_context(app):
            await ready(app.state.inference)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                large = await client.post("/api/extract_feature", files={"image": ("x.jpg", b"x" * 9)})
                assert large.status_code == 413
                invalid_response = await client.post("/api/extract_feature", files={"image": ("x.jpg", b"x")})
                assert invalid_response.status_code == 400
                assert not app.state.inference.readiness()["busy"]
    asyncio.run(scenario())


def test_initialization_failure_is_not_ready_but_health_is_live():
    async def scenario():
        def failed_factory():
            raise RuntimeError("secret model path")
        app = create_app(failed_factory, lambda data: data)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/health")).status_code == 200
                assert (await client.get("/ready")).status_code == 503
                response = await client.post("/api/extract_feature", files={"image": ("x.jpg", b"x")})
                assert response.status_code == 503
                assert "secret" not in response.text
    asyncio.run(scenario())


def test_extract_and_detect_share_single_model_slot():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        class Model:
            def extract_features(self, *args, **kwargs):
                entered.set()
                release.wait(3)
                return SimpleNamespace(success=False, faces=[], processing_time=0.1)
        app = create_app(Model, lambda data: data, timeout_seconds=2, queue_limit=0)
        try:
            async with app.router.lifespan_context(app):
                await ready(app.state.inference)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    extracting = asyncio.create_task(client.post("/api/extract_feature", files={"image": ("x", b"image")}))
                    for _ in range(100):
                        if entered.is_set():
                            break
                        await asyncio.sleep(0.005)
                    assert entered.is_set()
                    detection = await client.post("/api/detect_face", files={"image": ("x", b"image")})
                    assert detection.status_code == 429
                    release.set()
                    assert (await extracting).status_code == 200
        finally:
            release.set()
    asyncio.run(scenario())


async def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def test_queued_request_runs_after_the_slot_frees():
    async def scenario():
        runtime = BoundedInference(object, timeout_seconds=2, queue_limit=1)
        release, entered = threading.Event(), threading.Event()
        runtime.start()
        await ready(runtime)
        def blocked(model, contents):
            entered.set()
            release.wait(3)
            return "first"
        try:
            first = asyncio.create_task(runtime.execute(upload(), blocked))
            await wait_until(entered.is_set)
            second = asyncio.create_task(runtime.execute(upload(), lambda model, data: "second"))
            await wait_until(lambda: len(runtime._waiters) == 1)
            with pytest.raises(HTTPException) as full:
                await runtime.execute(upload(), lambda model, data: "third")
            assert full.value.status_code == 429
            release.set()
            assert await first == "first"
            assert await second == "second"
        finally:
            release.set()
            runtime.close()
    asyncio.run(scenario())


def test_queue_wait_counts_against_the_deadline():
    async def scenario():
        runtime = BoundedInference(object, timeout_seconds=0.05, unhealthy_after_seconds=1, queue_limit=4)
        release, entered = threading.Event(), threading.Event()
        runtime.start()
        await ready(runtime)
        def blocked(model, contents):
            entered.set()
            release.wait(3)
        try:
            first = asyncio.create_task(runtime.execute(upload(), blocked))
            await wait_until(entered.is_set)
            with pytest.raises(HTTPException) as busy:
                await runtime.execute(upload(), lambda model, data: "late")
            assert busy.value.status_code == 429
            assert not runtime._waiters
            with pytest.raises(HTTPException):
                await first
        finally:
            release.set()
            runtime.close()
    asyncio.run(scenario())


def test_cancelled_waiter_does_not_strand_the_next_one():
    async def scenario():
        runtime = BoundedInference(object, timeout_seconds=2, queue_limit=2)
        release, entered = threading.Event(), threading.Event()
        runtime.start()
        await ready(runtime)
        def blocked(model, contents):
            entered.set()
            release.wait(3)
            return "first"
        try:
            first = asyncio.create_task(runtime.execute(upload(), blocked))
            await wait_until(entered.is_set)
            abandoned = asyncio.create_task(runtime.execute(upload(), lambda model, data: "abandoned"))
            await wait_until(lambda: len(runtime._waiters) == 1)
            third = asyncio.create_task(runtime.execute(upload(), lambda model, data: "third"))
            await wait_until(lambda: len(runtime._waiters) == 2)
            abandoned.cancel()
            with pytest.raises(asyncio.CancelledError):
                await abandoned
            release.set()
            assert await first == "first"
            assert await asyncio.wait_for(third, 1) == "third"
            await wait_until(lambda: not runtime.readiness()["busy"])
        finally:
            release.set()
            runtime.close()
    asyncio.run(scenario())
