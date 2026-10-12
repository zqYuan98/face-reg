# Feature Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Add the agreed bounded pure-vector 1:N endpoint and document the working service.

**Architecture:** Preserve the original feature service and existing 1:1 router. Add a model-free search worker process, a single admission slot before upload, and a strict JSON router. Reuse the current numerical protocol; reject incomplete rankings.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, NumPy, ProcessPoolExecutor, existing Docker overlay; bundled python-docx and Microsoft Word PDF rendering for document QA.

## Task 1 Implementation

- [x] Review the adjacent approved-scope spec and numerical/deadline constraints.
- [x] Add `FaceFeatureExtractorAPI/face_algorithm/core/search.py` for blocked batch scoring and stable top-k.
- [x] Add `FaceFeatureExtractorAPI/api/search_execution.py` for a single spawned worker with completion-based admission release; keep parsing and model-free computation out of the event loop.
- [x] Add `FaceFeatureExtractorAPI/api/feature_search.py` for request/response contracts, bounded upload, errors and router lifespan.
- [x] Reuse/refactor vector validation narrowly in `api/feature_comparison.py`; extend ReasonCode in `face_algorithm/schemas.py`.
- [x] Integrate only the new route into `feature_comparison_server.py` and its dependency closure in `Dockerfile.compare`; version 1.3.0.

## Task 2 Verification and release

- [x] Keep task-specific test and load drivers in the release temp directory, not permanent test clutter. Prove single-candidate scores match existing `match_embeddings`, ordering is stable and no partial results escape.
- [x] Exercise invalid inputs, aggregate size, slow upload, unavailable worker, saturation and cancellation; original compare/detect/extract contracts must remain unchanged.
- [x] Build from the known running production image; verify in an isolated loopback candidate container under the same 2 CPU / 4 GiB limits. Measure real 1000-candidate JSON bytes, serial latency, mixed-load responsiveness and peak process/container memory.
- [x] Review and commit; push source to the authorized remote and replace only face-algorithm with rollback configuration saved. Verify production and remove temporary containers.

## Task 3 Documentation

- [x] Update `FaceFeatureExtractorAPI/FEATURE_COMPARISON.md`, root README and verified release notes with actual measured limits, errors and runnable examples.
- [x] Preserve `C:/Users/24830/Desktop/01_人脸识别与活体检测算法接口文档_v1.3.docx`; create v1.4 with the new pure-vector API, API client instructions and example. Preserve old chapters, tables and media.
- [x] Update TOC, render to PDF/PNG, visually inspect every page and check code/schema examples. Deliver only final Word and concise endpoint/deployment information.

Release evidence: `docs/reports/2026-10-12-feature-search-deployment.md`. The mixed-load issue was resolved by the reviewed, image-thread-scoped ORT options adapter; the 5-second search deadline and deployment resources remain unchanged.
