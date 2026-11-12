"""/api/search_feature: strict validation, ranking, versions and the single search slot.

Runs the real spawned search worker; no image models are loaded.
"""
import asyncio
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.feature_search import FEATURE_SEARCH_PATH, create_feature_search_router
from api.search_execution import SearchExecution
from face_algorithm.core.matcher import match_embeddings
from face_algorithm.core.search import rank_embeddings

V1, V2 = "recognizer_v1_1024", "arcface_r50_512"


def unit(dim, axis, other=0.0):
    v = np.zeros(dim, np.float32)
    v[axis] = 1.0
    v[(axis + 1) % dim] = other
    return v.tolist()


def request(version=V2, dim=512, **overrides):
    body = {
        "probe_feature": unit(dim, 0),
        "probe_version": version,
        "reference_version": version,
        "candidates": [
            {"id": "far", "feature": unit(dim, 5)},
            {"id": "near", "feature": unit(dim, 0, 0.3)},
            {"id": "same", "feature": unit(dim, 0)},
        ],
        "top_k": 2,
    }
    body.update(overrides)
    return body


@pytest.fixture
def client():
    """Runs a scenario against a fresh router: a closed search worker never restarts."""
    async def run(make_requests):
        app = FastAPI()
        router = create_feature_search_router()
        app.include_router(router)
        async with router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                return await make_requests(c)

    return lambda make_requests: asyncio.run(run(make_requests))


def post(c, body):
    return c.post(FEATURE_SEARCH_PATH, json=body)


def test_ranks_both_versions_and_reports_them(client):
    async def scenario(c):
        v2 = (await post(c, request())).json()
        v1 = (await post(c, request(version=V1, dim=1024, top_k=3))).json()
        return v2, v1
    v2, v1 = client(scenario)
    assert [m["id"] for m in v2["matches"]] == ["same", "near"]
    assert v2["matches"][0]["similarity_score"] == pytest.approx(1.0)
    assert v2["feature_dimension"] == 512 and v2["feature_version"] == V2 and v2["candidate_count"] == 3
    assert [m["id"] for m in v1["matches"]] == ["same", "near", "far"] and v1["feature_dimension"] == 1024


@pytest.mark.parametrize("bad", [
    {"reference_version": V1},                                   # mixed versions
    {"probe_feature": unit(1024, 0)},                            # length does not match declared v2
    {"candidates": [{"id": "a", "feature": unit(512, 1)}, {"id": "a", "feature": unit(512, 2)}]},
    {"top_k": 4},                                                # more than the candidates
    {"top_k": True},
    {"candidates": [{"id": " ", "feature": unit(512, 1)}]},
    {"candidates": [{"id": "z", "feature": [0.0] * 512}]},       # zero vector
    {"probe_version": "arcface_r100", "reference_version": "arcface_r100"},
    {"extra": 1},
])
def test_invalid_requests_fail_as_a_batch(client, bad):
    async def scenario(c):
        return await post(c, request(**bad))
    response = client(scenario)
    assert response.status_code == 400
    assert response.json()["reason_codes"] == ["INVALID_FEATURE"]


def test_ties_keep_input_order_and_ranking_matches_the_matcher():
    rng = np.random.default_rng(1)
    probe = rng.standard_normal(512).astype(np.float32)
    candidates = rng.standard_normal((40, 512)).astype(np.float32)
    candidates[7] = candidates[3]
    ranked = rank_embeddings(probe.tolist(), candidates.tolist(), probe_version=V2, reference_version=V2,
                             top_k=40, deadline=float("inf"))
    for index, score in ranked:
        expected = match_embeddings(candidates[index], probe, reference_version=V2, probe_version=V2).score
        assert score == pytest.approx(expected, abs=1e-12)
    order = [i for i, _ in ranked]
    assert order.index(3) == order.index(7) - 1


def test_second_request_while_busy_is_refused_not_queued():
    async def scenario():
        execution = SearchExecution()
        await execution.start()
        try:
            first = execution.reserve()
            with pytest.raises(Exception) as busy:
                execution.reserve()
            assert getattr(busy.value, "status_code", None) == 429
            execution.release_unsubmitted(first)
            execution.release_unsubmitted(execution.reserve())
        finally:
            execution.close()
    asyncio.run(scenario())


def test_oversized_declared_body_is_rejected_before_reading(client):
    async def scenario(c):
        return await c.post(FEATURE_SEARCH_PATH, content=b"{}",
                            headers={"content-type": "application/json", "content-length": str(64 * 1024 * 1024)})
    assert client(scenario).status_code == 413
